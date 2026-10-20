"""vnew 캐릭터 만들기 — 저장 레이어 + 파이프라인(설계자 2026-10-20).

웹툰 이미지만 넣으면 같은 캐릭터를 두 가지(webtoon·real) 로:
각각 5면 턴어라운드 시트 + 정면 전신 + 3D 모델(GLB).

재사용(수정 금지): vpoc.seedream(그림) · vpoc.fal(3D 큐) · vpoc.llm(분석) · vpoc.images
(업로드 정규화·참고 이미지) · vpoc.store(S3) · vpoc.ledger(원장) · vpoc.errors.

외부 호출은 전부 아래 함수 하나하나로 감싼다(검사가 가짜로 바꿔 끼운다):
_analyze · _seedream · _fal_request · _fetch_url. STORE 는 vpoc.store.STORE 를 쓴다.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import secrets
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone

from PIL import Image

from ..vpoc import fal as vpoc_fal
from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import seedream as vpoc_seedream
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.fal import _parse_arn, _queue_app  # noqa: PLC2701 — fal 큐 경로 규칙 재사용
from ..vpoc.store import STORE

from . import settings

log = logging.getLogger("vpoc")

# fal ARN 접두사(vpoc.fal 과 같은 규칙). "fal/requests/<endpoint_id>/<request_id>".
ARN_PREFIX = vpoc_fal.ARN_PREFIX


# ================================================================ 파드 안 실행 중 표시 · 선점
#
# (owner,charId,variant) 가 이 파드에서 빌드/재개 스레드로 돌고 있으면 _RUNNING 에 담는다.
# 조회 때 자동 이어 만들기가 같은 variant 를 또 띄우지 않게 한다(같은 파드 중복 방지).
# 여러 파드 사이 중복은 char.json 의 variant claim(token+claimedAt, 조건부 쓰기)으로 막는다.

_RUNNING: set[tuple[str, str, str]] = set()
_RUNNING_LOCK = threading.Lock()


def _running_key(owner: str, char_id: str, variant: str) -> tuple[str, str, str]:
    return (owner, char_id, variant)


def _is_running(owner: str, char_id: str, variant: str) -> bool:
    with _RUNNING_LOCK:
        return _running_key(owner, char_id, variant) in _RUNNING


def _mark_running(owner: str, char_id: str, variant: str) -> bool:
    """이 파드에서 실행 중으로 표시. 이미 실행 중이면 False(띄우지 않는다)."""
    key = _running_key(owner, char_id, variant)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)
        return True


def _clear_running(owner: str, char_id: str, variant: str) -> None:
    key = _running_key(owner, char_id, variant)
    with _RUNNING_LOCK:
        _RUNNING.discard(key)


# ================================================================ 키 규칙
#
# IRSA 가 users/* 만 허용하므로 전부 그 아래에 둔다. owner 는 safe_user_prefix(username).


def _root(owner: str) -> str:
    return f"users/{owner}/characters"


def index_key(owner: str) -> str:
    return f"{_root(owner)}/index.json"


def char_key(owner: str, char_id: str) -> str:
    return f"{_root(owner)}/{char_id}/char.json"


def source_key(owner: str, char_id: str, file_id: str) -> str:
    return f"{_root(owner)}/{char_id}/src/{file_id}.jpg"


def _variant_dir(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_root(owner)}/{char_id}/{variant}/v{version}"


def sheet_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/sheet.jpg"


def front_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/front.jpg"


def back_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/back.jpg"


def model_glb_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/model.glb"


def model_thumb_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/thumb.png"


# ================================================================ 저장 레이어


def _new_char_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"char_{stamp}_{secrets.token_hex(3)}"


def _empty_variant() -> dict:
    return {
        "status": "queued",  # queued|images|model3d|ready|failed
        "sheetKey": "",
        "frontKey": "",
        "backKey": "",
        "warning": "",  # 정면/후면에 여러 사람이 보일 수 있을 때 안내(3D 는 진행)
        "model3d": {
            "status": "queued",  # queued|submitted|ready|failed
            "requestArn": "",
            "glbKey": "",
            "thumbKey": "",
            "error": "",
            # '잃은 요청' 자동 재제출 횟수(variant 당 최대 MAX_RESUBMITS).
            "resubmits": 0,
        },
        "error": "",
        # 단계 진입·진행 때 갱신. 비어 있으면(옛 데이터) stale 판단에서 buildStartedAt 로 넘어간다.
        "heartbeatAt": "",
        # 자동 이어 만들기(재개) 횟수. MAX_RESUME 넘으면 그 variant 만 failed.
        "resumeCount": 0,
        # 여러 파드 중복 방지 선점(016-fix2 방식). claim=token, claimedAt=시각.
        "claim": "",
        "claimedAt": "",
    }


def new_char(name: str) -> dict:
    return {
        "id": _new_char_id(),
        "name": (name or "").strip() or "이름 없는 캐릭터",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "status": "draft",  # draft|building|ready|failed
        "sources": [],  # [{fileId,name,width,height}]
        "analysis": {
            "nameGuess": "",
            "descriptionKo": "",
            "appearanceEn": "",
            "styleEn": "",
            "outfitEn": "",
            "altOutfitsEn": [],
            "expressionsKo": [],
            "extraEn": "",
            "model": "",
        },
        "extraPromptKo": "",
        "variants": {v: _empty_variant() for v in settings.VARIANTS},
        "version": 0,
        "costumes": [],
    }


def read_char(owner: str, char_id: str) -> tuple[dict | None, str]:
    return STORE.read_json(char_key(owner, char_id))


def load_char(owner: str, char_id: str) -> tuple[dict, str]:
    data, etag = read_char(owner, char_id)
    if data is None:
        raise VpocError("vnew.load_char", "NotFound", "캐릭터를 찾을 수 없습니다.", 404)
    return data, etag


def save_char(owner: str, char_id: str, data: dict, etag: str = "") -> str:
    data["updatedAt"] = now_iso()
    return STORE.write_json(char_key(owner, char_id), data, etag=etag)


def mutate_char(owner: str, char_id: str, mutator, *, attempts: int = 5):
    """읽고-고치고-조건부쓰기. 경합이면 다시 읽어 재시도(vpoc store 패턴)."""
    last: BaseException | None = None
    for _ in range(attempts):
        data, etag = load_char(owner, char_id)
        result = mutator(data)
        try:
            save_char(owner, char_id, data, etag)
            return data, result
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise
            last = exc
    raise last if last else RuntimeError("mutate_char 재시도 실패")


# --- index.json: 목록(경합에도 캐릭터 본문은 char.json 이 단일 원본) ---


def _read_index(owner: str) -> tuple[dict, str]:
    data, etag = STORE.read_json(index_key(owner))
    if data is None:
        return {"characters": []}, ""
    data.setdefault("characters", [])
    return data, etag


def _mutate_index(owner: str, mutator, *, attempts: int = 5) -> None:
    last: BaseException | None = None
    for _ in range(attempts):
        data, etag = _read_index(owner)
        mutator(data)
        try:
            STORE.write_json(index_key(owner), data, etag=etag)
            return
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise
            last = exc
    raise last if last else RuntimeError("mutate_index 재시도 실패")


def _index_row(char: dict) -> dict:
    """index.json 에 담는 가벼운 행."""
    variants = char.get("variants") or {}
    thumb = ""
    for v in settings.VARIANTS:
        model3d = (variants.get(v) or {}).get("model3d") or {}
        if model3d.get("thumbKey"):
            thumb = model3d["thumbKey"]
            break
    return {
        "id": char["id"],
        "name": char.get("name") or "",
        "status": char.get("status") or "draft",
        "updatedAt": char.get("updatedAt") or now_iso(),
        "thumbKey": thumb,
        "deleted": bool(char.get("deleted")),
    }


def upsert_index(owner: str, char: dict) -> None:
    """index.json 에 이 캐릭터 행을 넣거나 갱신한다."""
    row = _index_row(char)

    def mutator(index: dict) -> None:
        rows = index["characters"]
        for i, existing in enumerate(rows):
            if existing.get("id") == row["id"]:
                rows[i] = row
                return
        rows.append(row)

    _mutate_index(owner, mutator)


def hide_in_index(owner: str, char_id: str) -> None:
    """index 에서 deleted:true 로 숨긴다. S3 파일은 지우지 않는다."""

    def mutator(index: dict) -> None:
        for row in index["characters"]:
            if row.get("id") == char_id:
                row["deleted"] = True

    _mutate_index(owner, mutator)


def list_characters(owner: str) -> list[dict]:
    index, _ = _read_index(owner)
    # 목록 썸네일은 presigned URL 로 함께 준다(thumbKey 는 S3 키라 화면이 바로 못 쓴다).
    return [dict(row, thumbUrl=_presign(row.get("thumbKey"))) for row in index.get("characters", []) if not row.get("deleted")]


# ================================================================ 업로드


def add_source(owner: str, char_id: str, raw: bytes, name: str) -> dict:
    """웹툰 이미지 1장 추가. images.normalize_upload 로 정규화하고 sources 에 넣는다."""
    char, _ = load_char(owner, char_id)
    if len(char.get("sources") or []) >= settings.MAX_SOURCES:
        raise VpocError(
            "vnew.add_source",
            "TooManySources",
            f"이미지는 최대 {settings.MAX_SOURCES}장까지 올릴 수 있습니다.",
            400,
        )
    jpeg, width, height = vpoc_images.normalize_upload(raw, name)
    file_id = f"f_{secrets.token_hex(4)}"
    STORE.put_bytes(source_key(owner, char_id, file_id), jpeg, "image/jpeg")
    entry = {"fileId": file_id, "name": name or file_id, "width": width, "height": height}

    def mutator(data: dict) -> None:
        data.setdefault("sources", []).append(entry)

    updated, _ = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)
    return entry


def remove_source(owner: str, char_id: str, file_id: str) -> None:
    def mutator(data: dict) -> None:
        data["sources"] = [s for s in (data.get("sources") or []) if s.get("fileId") != file_id]

    updated, _ = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)


def source_jpeg(owner: str, char_id: str, file_id: str) -> bytes:
    return STORE.get_bytes(source_key(owner, char_id, file_id))


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 네 함수를 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _analyze(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 분석 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_ANALYZE_SYSTEM,
        blocks=blocks,
        tool=_ANALYZE_TOOL,
        max_tokens=1200,
        where="vnew.analyze",
        model=model,
    )


def _seedream(prompt: str, references: list[bytes], *, size: str) -> bytes:
    """Seedream Pro 로 그림 1장. 안전 필터 오탐은 generate_safe 가 처리."""
    return vpoc_seedream.generate_safe(
        prompt,
        references,
        model=settings.SEEDREAM_MODEL,
        where="vnew.seedream",
        size=size,
    )


def _fal_request(method: str, url: str, payload: dict | None = None) -> dict:
    """fal 큐 HTTP(제출·조회). vpoc.fal._request 를 그대로 쓴다."""
    return vpoc_fal._request(method, url, payload, where="vnew.fal")  # noqa: SLF001


def _fetch_url(url: str) -> bytes:
    """fal.media 결과 파일을 받아 우리 버킷으로 옮기기 위한 다운로드."""
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
        return response.read()


# ================================================================ 분석(a)

_ANALYZE_SYSTEM = (
    "You analyze Korean webtoon character cut images and describe ONE single character. "
    "All images show ONE single character. If several figures appear (even in one image), "
    "they are the SAME person drawn with different expressions, poses, angles or outfits — "
    "never different people. Ignore text and speech bubbles. "
    "Answer ONLY by calling the emit_character tool exactly once. "
    "descriptionKo must be 2-3 Korean sentences. "
    "appearanceEn is ONE English sentence about face, hair, body type, age range, glasses, etc. "
    "styleEn is 1-2 English sentences describing the EXACT drawing style of the reference art: "
    "line weight and texture (e.g. thin sketchy pencil-like lines), coloring method "
    "(flat / soft pastel / cel), palette, shading and blush, and how the eyes and hair are drawn. "
    "outfitEn is ONE English sentence about the SINGLE representative outfit — pick the outfit of the "
    "cut where the full body is drawn largest. altOutfitsEn lists other outfits seen (reference only). "
    "expressionsKo lists the facial expressions you can see (Korean words). "
    "If the user gives an extra request (extraPromptKo), turn it into a short English drawing "
    "instruction in extraEn; otherwise leave extraEn empty. Do not add text outside the tool."
)

_ANALYZE_TOOL = {
    "name": "emit_character",
    "description": "Describe the single character seen in the reference images.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "nameGuess": {"type": "string"},
                "descriptionKo": {"type": "string"},
                "appearanceEn": {"type": "string"},
                "styleEn": {"type": "string"},
                "outfitEn": {"type": "string"},
                "altOutfitsEn": {"type": "array", "items": {"type": "string"}},
                "expressionsKo": {"type": "array", "items": {"type": "string"}},
                "extraEn": {"type": "string"},
            },
            "required": ["descriptionKo", "appearanceEn", "outfitEn"],
        }
    },
}


def analyze_sources(owner: str, char: dict) -> dict:
    """업로드 이미지를 Astra 에 먼저, 실패하면 Claude 분석 체인으로 보내 분석 JSON 을 받는다.

    실제 쓴 모델을 analysis.model 에 남긴다. 반환:
    {nameGuess,descriptionKo,appearanceEn,styleEn,outfitEn,altOutfitsEn,expressionsKo,extraEn,model}.
    extraPromptKo(사용자 추가 요청)가 있으면 Astra 에 함께 주고 extraEn 으로 돌려받는다.
    """
    sources = char.get("sources") or []
    if not sources:
        raise VpocError("vnew.analyze", "NoSources", "분석할 이미지가 없습니다.", 400)

    jpegs: list[bytes] = []
    for src in sources[: settings.MAX_ANALYSIS_IMAGES]:
        jpegs.append(source_jpeg(owner, char["id"], src["fileId"]))

    extra_ko = str(char.get("extraPromptKo") or "").strip()

    intro = (
        "다음 웹툰 컷들에 나오는 같은 캐릭터 한 명을 분석해 주세요. "
        "여러 인물이 보여도 모두 같은 사람의 다른 표정·자세·각도·옷차림입니다."
    )
    blocks: list[dict] = [{"text": intro}]
    if extra_ko:
        blocks.append(
            {"text": "사용자 추가 요청(이 지시를 extraEn 영어 한 줄로 바꿔 주세요): " + extra_ko}
        )
    for jpeg in jpegs:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

    # Astra 먼저, 실패하면 Claude 분석 체인.
    for model in (settings.ASTRA_MODEL, settings.ANALYZE_FALLBACK_MODEL):
        try:
            payload, usage = _analyze(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.analyze.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        alt = payload.get("altOutfitsEn")
        expressions = payload.get("expressionsKo")
        return {
            "nameGuess": str(payload.get("nameGuess") or "").strip(),
            "descriptionKo": str(payload.get("descriptionKo") or "").strip(),
            "appearanceEn": str(payload.get("appearanceEn") or "").strip(),
            "styleEn": str(payload.get("styleEn") or "").strip(),
            "outfitEn": str(payload.get("outfitEn") or "").strip(),
            "altOutfitsEn": [str(x).strip() for x in alt if str(x).strip()] if isinstance(alt, list) else [],
            "expressionsKo": [str(x).strip() for x in expressions if str(x).strip()]
            if isinstance(expressions, list)
            else [],
            "extraEn": str(payload.get("extraEn") or "").strip(),
            "model": str(usage.get("model") or model),
        }
    raise VpocError("vnew.analyze", "AnalyzeFailed", "캐릭터 분석에 실패했습니다.", 502)


# ---- 사람 수 검사(정면·후면) ----

_PEOPLE_TOOL = {
    "name": "emit_people_count",
    "description": "Count how many distinct people are drawn in the image.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {"people": {"type": "integer"}},
            "required": ["people"],
        }
    },
}

_PEOPLE_SYSTEM = (
    "You count how many distinct people appear in ONE image. "
    "Answer only by calling the emit_people_count tool exactly once with the integer count."
)


def _count_people(jpeg: bytes) -> int | None:
    """이미지에 보이는 사람 수. 모델 오류면 None(검사 건너뜀)."""
    blocks = [
        {"text": "How many distinct people are drawn in this single image?"},
        {"image": {"format": "jpeg", "source": {"bytes": jpeg}}},
    ]
    try:
        payload, _usage = vpoc_llm._converse(  # noqa: SLF001
            system_text=_PEOPLE_SYSTEM,
            blocks=blocks,
            tool=_PEOPLE_TOOL,
            max_tokens=120,
            where="vnew.count_people",
            model=settings.PEOPLE_COUNT_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.count_people.failed err=%s", str(exc)[:200])
        return None
    try:
        return int(payload.get("people"))
    except (TypeError, ValueError):
        return None


# ================================================================ 프롬프트(b)


def _who(analysis: dict) -> str:
    appearance = analysis.get("appearanceEn") or ""
    outfit = analysis.get("outfitEn") or ""
    return f"The character: {appearance} Wearing: {outfit}".strip()


def _one_person_outfit(analysis: dict) -> str:
    """모든 뷰에 '한 사람만 · 이 옷만' 을 강제하는 문장."""
    outfit = (analysis.get("outfitEn") or "").strip() or "the outfit described above"
    return (
        "Draw exactly ONE person — only ONE single person in the image, no extra figures, "
        "no duplicates, no background characters. "
        f"Wear only this outfit in every view: {outfit}"
    )


def _style_clause(analysis: dict) -> str:
    """웹툰 variant 그림체 지시: styleEn 을 그대로 넣고 '참고 그림체 그대로' 강제."""
    style = (analysis.get("styleEn") or "").strip()
    head = (style + " ") if style else ""
    return (
        head
        + "Match the drawing style of the reference images exactly — same line quality, coloring "
        "and shading. Do NOT redraw in a generic anime or glossy webtoon style."
    )


def _with_extra(prompt: str, analysis: dict) -> str:
    """분석이 뽑은 extraEn(사용자 추가 요청) 을 프롬프트 끝에 붙인다."""
    extra = (analysis.get("extraEn") or "").strip()
    if extra:
        return prompt + " " + extra
    return prompt


def _sheet_prompt(variant: str, analysis: dict) -> str:
    who = _who(analysis)
    one = _one_person_outfit(analysis)
    if variant == "real":
        prompt = (
            "Photorealistic live-action character turnaround reference sheet of a real Korean actor "
            "playing the character from the reference images. "
            + who
            + " Five full-body views side by side on a plain white background: front, three-quarter "
            "front, side profile, three-quarter back, back. Same person, same outfit in every view. "
            + one
            + " Real photograph, natural skin texture, soft studio lighting. No text, no labels."
        )
        return _with_extra(prompt, analysis)
    prompt = (
        "Character turnaround reference sheet. "
        + who
        + " Five full-body views side by side on a plain white background: front, three-quarter "
        "front, side profile, three-quarter back, back. Same person, same outfit in every view. "
        + one
        + " "
        + _style_clause(analysis)
        + " No text, no speech bubbles, no labels."
    )
    return _with_extra(prompt, analysis)


def _front_prompt(variant: str, analysis: dict) -> str:
    who = _who(analysis)
    one = _one_person_outfit(analysis)
    if variant == "real":
        prompt = (
            "Single full-body front view photograph of a real Korean actor playing the character. "
            + who
            + " Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes "
            "visible, centered, plain white background, no shadow. "
            + one
            + " Real photograph, natural skin texture, soft studio lighting. No text."
        )
        return _with_extra(prompt, analysis)
    prompt = (
        "Single full-body front view. "
        + who
        + " Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes "
        "visible, centered, plain white background, no shadow. "
        + one
        + " "
        + _style_clause(analysis)
        + " No text."
    )
    return _with_extra(prompt, analysis)


def _back_prompt(variant: str, analysis: dict) -> str:
    """후면 전신 — 정면 이미지 + 시트를 참고로 '같은 한 사람, 같은 옷, 뒷모습'."""
    who = _who(analysis)
    one = _one_person_outfit(analysis)
    if variant == "real":
        prompt = (
            "Single full-body BACK view photograph of the same real Korean actor as in the reference "
            "images, seen from directly behind. "
            + who
            + " Back view of the same single person, same outfit, standing straight in a relaxed "
            "A-pose, whole body from head to shoes visible, centered, plain white background, no shadow. "
            + one
            + " Real photograph, natural skin texture, soft studio lighting. No text."
        )
        return _with_extra(prompt, analysis)
    prompt = (
        "Single full-body BACK view of the same single person as in the reference images, seen from "
        "directly behind. "
        + who
        + " Back view of the same single person, same outfit, standing straight in a relaxed A-pose, "
        "whole body from head to shoes visible, centered, plain white background, no shadow. "
        + one
        + " "
        + _style_clause(analysis)
        + " No text."
    )
    return _with_extra(prompt, analysis)


# ================================================================ 이미지 자르기


def _square_pad(jpeg: bytes, side: int) -> bytes:
    """흰 배경 정사각으로 맞춘다(긴 변을 side 에 맞추고 가운데 정렬)."""
    with Image.open(io.BytesIO(jpeg)) as image:
        image = image.convert("RGB")
        image.thumbnail((side, side), Image.LANCZOS)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        offset = ((side - image.width) // 2, (side - image.height) // 2)
        canvas.paste(image, offset)
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


def _data_uri(jpeg: bytes) -> str:
    """정사각 JPEG → data URI. 우리 이미지를 외부 저장소에 올리지 않는다."""
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


# ================================================================ 3D(c) — fal Hunyuan3D v3


def submit_3d(front_square: bytes, back_square: bytes) -> str:
    """Hunyuan3D v3 에 제출하고 requestArn 을 돌려준다(data URI 입력).

    face_count·enable_pbr 은 설정값을 쓴다(기본 60000·PBR off → GLB 약 10MB, 실측 2026-10-20).
    """
    eid = settings.HUNYUAN_ENDPOINT
    body = {
        "input_image_url": _data_uri(front_square),
        "back_image_url": _data_uri(back_square),
        "enable_pbr": bool(settings.HUNYUAN_ENABLE_PBR),
        "face_count": settings.HUNYUAN_FACE_COUNT,
    }
    result = _fal_request("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{eid}", body)
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.submit_3d", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:200]}", 502)
    return f"{ARN_PREFIX}{eid}/{request_id}"


def _status_url(endpoint: str, request_id: str) -> str:
    return f"{vpoc_settings.FAL_QUEUE_URL}/{_queue_app(endpoint)}/requests/{request_id}/status"


def _response_url(endpoint: str, request_id: str) -> str:
    return f"{vpoc_settings.FAL_QUEUE_URL}/{_queue_app(endpoint)}/requests/{request_id}"


def _find_url(node, needle: str) -> str:
    """결과 트리에서 .glb / 썸네일 url 을 찾는다."""
    found: list[str] = []

    def walk(item) -> None:
        if found:
            return
        if isinstance(item, str) and needle in item.lower().split("?")[0]:
            found.append(item)
        elif isinstance(item, dict):
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(node)
    return found[0] if found else ""


def _is_transient_3d_error(exc: BaseException) -> bool:
    """3D 조회 중 난 오류가 '일시 오류'(다시 조회하면 되는 것)인지.

    일시로 보는 것: HTTP 5xx·429, 연결 실패(FalUnreachable), 시간 초과(socket/Timeout),
    그리고 메시지에 'unavailable' 이 든 것. 그 밖(4xx·잔액 소진 등)은 영구 실패로 본다.
    """
    message = str(getattr(exc, "message", "") or exc)
    if "unavailable" in message.lower():
        return True
    if isinstance(exc, VpocError):
        kind = exc.kind or ""
        if kind == "FalUnreachable":
            return True
        if kind.startswith("HTTP"):
            digits = "".join(ch for ch in kind if ch.isdigit())
            if digits:
                code = int(digits)
                return code == 429 or 500 <= code <= 599
        return False
    # _request 밖으로 샌 연결·시간초과(urllib 가 감싸지 못한 경우).
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)):
        return True
    return False


def _is_lost_3d_error(exc: BaseException) -> bool:
    """이 일시 오류가 'fal 이 요청을 잃은' 신호(unavailable 류)인지.

    실측(2026-10-20): 요청 조회가 'Downstream service unavailable'(503) 만 계속 돌려주면 그
    요청은 끝내 못 받는다. 그래서 unavailable 또는 503 이 연속으로 쌓이면 '잃은 요청'으로 보고
    같은 front/back 으로 새로 제출한다(그 밖의 일시 오류는 기존대로 다시 조회만).
    """
    message = str(getattr(exc, "message", "") or exc)
    if "unavailable" in message.lower():
        return True
    if isinstance(exc, VpocError):
        kind = exc.kind or ""
        if kind.startswith("HTTP"):
            digits = "".join(ch for ch in kind if ch.isdigit())
            if digits and int(digits) == 503:
                return True
    return False


def poll_3d(request_arn: str) -> dict:
    """3D 작업 상태 한 번 조회. 완료면 {status:'Completed', glbUrl, thumbUrl}."""
    endpoint, request_id = _parse_arn(request_arn)
    status_result = _fal_request("GET", _status_url(endpoint, request_id))
    raw_status = str(status_result.get("status") or "").upper()
    if raw_status in ("IN_QUEUE", "IN_PROGRESS"):
        return {"status": "InProgress"}
    if raw_status != "COMPLETED":
        message = json.dumps(status_result, ensure_ascii=False)[:300] or raw_status
        return {"status": "Failed", "error": message or "상태가 비어 있습니다."}
    result = _fal_request("GET", _response_url(endpoint, request_id))
    if isinstance(result, dict) and result.get("error"):
        return {"status": "Failed", "error": str(result.get("error"))[:300]}
    glb_url = _find_url(result, ".glb")
    thumb_url = ""
    if isinstance(result, dict):
        thumb = result.get("thumbnail")
        if isinstance(thumb, dict):
            thumb_url = str(thumb.get("url") or "")
    if not thumb_url:
        thumb_url = _find_url(result, ".png")
    if not glb_url:
        return {"status": "Failed", "error": "완료됐지만 GLB URL 이 없습니다."}
    return {"status": "Completed", "glbUrl": glb_url, "thumbUrl": thumb_url}


def _wait_3d(request_arn: str, resubmit=None) -> dict:
    """완료까지 폴링(5초 간격, 최대 15분).

    조회 중 fal 일시 오류(5xx·429·연결·시간초과·'unavailable')는 실패로 보지 않고 다시 조회한다
    (연속 최대 POLL_TRANSIENT_MAX 회, 사이 POLL_TRANSIENT_WAIT_S 초). 그 밖의 오류나 fal 의
    FAILED 만 실패로 돌려준다.

    "잃은 요청" 재제출: resubmit 콜백이 주어지고, 같은 요청에서 unavailable 류 일시 오류가 연속
    LOST_AFTER 회 쌓이면 그 요청은 fal 이 잃은 것으로 보고 resubmit() 을 불러 같은 front/back 으로
    새로 제출한다. resubmit() 은 새 requestArn(문자열)을 돌려주며, 그때부터 그 arn 으로 계속
    조회한다(일시 오류 카운트 초기화). 재제출 한도를 넘어 resubmit() 이 None 을 돌려주면 실패한다.
    """
    deadline = time.time() + settings.POLL_MAX_S
    transient = 0
    lost = 0  # 같은 요청에서 연속으로 쌓인 unavailable 류(잃은 요청 신호) 횟수
    while True:
        try:
            state = poll_3d(request_arn)
        except Exception as exc:  # noqa: BLE001
            if not _is_transient_3d_error(exc):
                message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
                return {"status": "Failed", "error": message or "3D 조회 중 오류"}
            transient += 1
            message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
            # 잃은 요청 재제출: unavailable 류가 연속 LOST_AFTER 회면 새로 제출한다.
            if resubmit is not None and _is_lost_3d_error(exc):
                lost += 1
                if lost >= settings.LOST_AFTER:
                    new_arn = resubmit()
                    if not new_arn:
                        return {
                            "status": "Failed",
                            "error": f"fal 일시 오류가 계속됩니다: {message}",
                        }
                    log.warning(
                        "vnew.poll_3d.resubmit old=%s new=%s after_unavailable=%d",
                        request_arn, new_arn, lost,
                    )
                    request_arn = new_arn
                    transient = 0
                    lost = 0
                    if time.time() >= deadline:
                        return {"status": "Failed", "error": "3D 생성이 시간 안에 끝나지 않았습니다."}
                    time.sleep(settings.POLL_TRANSIENT_WAIT_S)
                    continue
            else:
                lost = 0  # unavailable 이 아닌 일시 오류면 '잃은 요청' 연속 카운트 초기화
            if transient > settings.POLL_TRANSIENT_MAX:
                return {"status": "Failed", "error": f"fal 일시 오류가 계속됩니다: {message}"}
            log.warning(
                "vnew.poll_3d.transient arn=%s n=%d err=%s", request_arn, transient, str(exc)[:200]
            )
            if time.time() >= deadline:
                return {"status": "Failed", "error": "3D 생성이 시간 안에 끝나지 않았습니다."}
            time.sleep(settings.POLL_TRANSIENT_WAIT_S)
            continue
        transient = 0  # 성공 조회면 연속 일시 오류 카운트 초기화
        lost = 0
        if state["status"] in ("Completed", "Failed"):
            return state
        if time.time() >= deadline:
            return {"status": "Failed", "error": "3D 생성이 시간 안에 끝나지 않았습니다."}
        time.sleep(settings.POLL_INTERVAL_S)


# ================================================================ 원장(d)


def _charge_seedream(owner: str, char_id: str, variant: str, kind: str) -> None:
    key = f"vnew-{char_id}-{variant}-{kind}-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="image",
            model=settings.SEEDREAM_MODEL,
            units=1,
            usd=settings.SEEDREAM_USD,
            user=owner,
            project_id=char_id,
            plan_id=variant,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.ledger.seedream_failed char=%s err=%s", char_id, str(exc)[:200])


def _charge_3d(owner: str, char_id: str, variant: str) -> None:
    key = f"vnew-{char_id}-{variant}-3d-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="model3d",
            model=settings.HUNYUAN_ENDPOINT,
            units=1,
            usd=settings.MODEL3D_USD,
            user=owner,
            project_id=char_id,
            plan_id=variant,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.ledger.3d_failed char=%s err=%s", char_id, str(exc)[:200])


# ================================================================ 파이프라인(e)


def _set_variant(owner: str, char_id: str, variant: str, patch: dict) -> None:
    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        v.update(patch)

    mutate_char(owner, char_id, mutator)


def _set_variant_3d(owner: str, char_id: str, variant: str, patch: dict) -> None:
    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        v.setdefault("model3d", {}).update(patch)

    mutate_char(owner, char_id, mutator)


def _touch_heartbeat(owner: str, char_id: str, variant: str) -> None:
    """단계 진입·진행 때 variant 의 heartbeatAt 을 지금으로 갱신한다(끊김 판단 기준)."""
    try:

        def mutator(data: dict) -> None:
            v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
            v["heartbeatAt"] = now_iso()

        mutate_char(owner, char_id, mutator)
    except Exception as exc:  # noqa: BLE001 — 하트비트 실패가 빌드를 막지 않게
        log.warning("vnew.heartbeat_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:200])


def _claim_variant(owner: str, char_id: str, variant: str) -> str:
    """variant 를 이 파드가 선점한다(016-fix2 방식). 성공하면 token, 못 잡으면 ""(빈 문자열).

    선점 조건: claim 이 비어 있거나(놓임), CLAIM_STALL_S 넘게 오래된 것일 때만 새 token 으로
    조건부 쓰기. 경합(PreconditionFailed)이면 다른 파드가 먼저 잡은 것이라 포기한다.
    """
    token = secrets.token_hex(6)
    got = {"ok": False}

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        existing = v.get("claim") or ""
        claimed_at = _parse_iso(v.get("claimedAt") or "")
        fresh = bool(existing) and bool(claimed_at) and (
            (datetime.now(timezone.utc) - claimed_at).total_seconds() <= settings.CLAIM_STALL_S
        )
        if fresh:
            got["ok"] = False
            return
        v["claim"] = token
        v["claimedAt"] = now_iso()
        got["ok"] = True

    try:
        mutate_char(owner, char_id, mutator)
    except VpocError as exc:
        if exc.kind == "PreconditionFailed":
            return ""
        raise
    return token if got["ok"] else ""


def _holds_claim(owner: str, char_id: str, variant: str, token: str) -> bool:
    """char.json 을 다시 읽어 이 token 이 아직 이 variant 의 claim 인지 확인한다."""
    if not token:
        return False
    try:
        char, _ = load_char(owner, char_id)
    except VpocError:
        return False
    v = (char.get("variants") or {}).get(variant) or {}
    return (v.get("claim") or "") == token


def _release_claim(owner: str, char_id: str, variant: str, token: str) -> None:
    """내 claim 이면 비운다(선점 해제). 다른 token 이면 건드리지 않는다."""

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        if (v.get("claim") or "") == token:
            v["claim"] = ""
            v["claimedAt"] = ""

    try:
        mutate_char(owner, char_id, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.release_claim_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:200])


def _gen_checked(
    prompt: str, references: list[bytes], *, size: str
) -> tuple[bytes, bool]:
    """그림 1장 생성 후 사람 수를 검사. n != 1 이면 '한 사람만' 지시를 붙여 1회만 다시 그린다.

    반환: (jpeg, multi) — multi 는 재생성 뒤에도 사람 수가 1 이 아니면 True(warning 대상).
    검사가 모델 오류로 건너뛰어진 경우(None)는 재생성하지 않고 multi=False.
    """
    image = _seedream(prompt, references, size=size)
    count = _count_people(image)
    if count is None or count == 1:
        return image, False
    # 한 번만 다시 그린다.
    retry_prompt = prompt + " Only one person. Remove the extra figures."
    image2 = _seedream(retry_prompt, references, size=size)
    count2 = _count_people(image2)
    if count2 is None or count2 == 1:
        return image2, False
    return image2, True


def _build_variant(
    owner: str, char_id: str, variant: str, version: int, analysis: dict, refs: list[bytes]
) -> None:
    """한 variant 의 그림 + 3D. 실패하면 그 variant 만 failed 로 두고 예외를 올리지 않는다."""
    try:
        _set_variant(owner, char_id, variant, {"status": "images", "warning": "", "heartbeatAt": now_iso()})

        # 시트(2560x1440)
        sheet = _seedream(_sheet_prompt(variant, analysis), refs, size=settings.SHEET_SIZE)
        _charge_seedream(owner, char_id, variant, "sheet")
        s_key = sheet_key(owner, char_id, variant, version)
        STORE.put_bytes(s_key, sheet, "image/jpeg")
        _touch_heartbeat(owner, char_id, variant)

        # 정면 전신(1440x2560) — 시트를 첫 참고로 넣어 같은 인물·옷이 되게 한다.
        # 사람 수 검사: 여러 명이면 1회 다시 그리고, 그래도면 warning.
        front, front_multi = _gen_checked(
            _front_prompt(variant, analysis), [sheet] + list(refs)[:3], size=settings.FRONT_SIZE
        )
        _charge_seedream(owner, char_id, variant, "front")
        f_key = front_key(owner, char_id, variant, version)
        STORE.put_bytes(f_key, front, "image/jpeg")
        _touch_heartbeat(owner, char_id, variant)

        # 후면 전신(1440x2560) — 정면 + 시트를 참고로 따로 생성(이전엔 시트 5번째 칸을 잘랐음).
        back, back_multi = _gen_checked(
            _back_prompt(variant, analysis), [front, sheet] + list(refs)[:2], size=settings.BACK_SIZE
        )
        _charge_seedream(owner, char_id, variant, "back")
        b_key = back_key(owner, char_id, variant, version)
        STORE.put_bytes(b_key, back, "image/jpeg")

        warning = "정면/후면에 여러 사람이 보일 수 있습니다" if (front_multi or back_multi) else ""
        _set_variant(
            owner,
            char_id,
            variant,
            {
                "status": "model3d",
                "sheetKey": s_key,
                "frontKey": f_key,
                "backKey": b_key,
                "warning": warning,
                "heartbeatAt": now_iso(),
            },
        )

        # 3D: front·back 정사각 패딩 → data URI 제출 → 완료까지 폴링 → 복사
        _build_3d_from_images(owner, char_id, variant, version, front, back)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.build_variant_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        # 3D 가 제출만 되고 끝난 경우(조회·복사 중 예외 등) model3d.status 가 submitted 로 남지 않게
        # 함께 failed 로 맞춘다 — ready 는 건드리지 않는다.
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})


def _make_3d_resubmit(owner: str, char_id: str, variant: str, front_sq: bytes, back_sq: bytes):
    """'잃은 요청' 재제출 콜백을 만든다(_wait_3d 에 넘긴다).

    같은 front/back 정사각으로 새로 제출하고, 원장을 1회 더 달고, model3d.requestArn·resubmits 를
    갱신한 뒤 새 requestArn 을 돌려준다. 재제출 한도(MAX_RESUBMITS)를 넘으면 None(그러면 실패).
    """
    done = {"n": 0}

    def resubmit() -> str:
        if done["n"] >= settings.MAX_RESUBMITS:
            return ""
        done["n"] += 1
        request_arn = submit_3d(front_sq, back_sq)
        _charge_3d(owner, char_id, variant)

        def mutator(data: dict) -> None:
            v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
            m3d = v.setdefault("model3d", {})
            m3d["status"] = "submitted"
            m3d["requestArn"] = request_arn
            m3d["resubmits"] = int(m3d.get("resubmits") or 0) + 1
            m3d["error"] = ""

        mutate_char(owner, char_id, mutator)
        _touch_heartbeat(owner, char_id, variant)
        return request_arn

    return resubmit


def _build_3d_from_images(
    owner: str, char_id: str, variant: str, version: int, front: bytes, back: bytes
) -> None:
    """front/back 바이트로 3D 를 새로 제출하고 완료까지 폴링해 복사한다(예외는 올린다).

    조회가 'Downstream service unavailable' 만 연속으로 돌려주면(fal 이 요청을 잃음) 같은
    front/back 으로 새로 제출한다(variant 당 최대 MAX_RESUBMITS 회).
    """
    side = settings.MODEL_INPUT_SQUARE
    front_sq = _square_pad(front, side)
    back_sq = _square_pad(back, side)
    request_arn = submit_3d(front_sq, back_sq)
    _charge_3d(owner, char_id, variant)
    _set_variant_3d(owner, char_id, variant, {"status": "submitted", "requestArn": request_arn})
    _touch_heartbeat(owner, char_id, variant)

    resubmit = _make_3d_resubmit(owner, char_id, variant, front_sq, back_sq)
    state = _wait_3d(request_arn, resubmit=resubmit)
    if state["status"] != "Completed":
        _set_variant_3d(owner, char_id, variant, {"status": "failed", "error": state.get("error", "")})
        _set_variant(owner, char_id, variant, {"status": "failed", "error": state.get("error", "3D 실패")})
        return

    _copy_3d_result(owner, char_id, variant, version, state)


def _fail_pending_3d(owner: str, char_id: str, variant: str, message: str) -> None:
    """model3d.status 가 ready 가 아니면 failed + error 로 맞춘다(submitted 로 남지 않게)."""

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        m3d = v.setdefault("model3d", {})
        if m3d.get("status") != "ready":
            m3d["status"] = "failed"
            if not m3d.get("error"):
                m3d["error"] = message

    mutate_char(owner, char_id, mutator)


def _copy_3d_result(owner: str, char_id: str, variant: str, version: int, state: dict) -> None:
    """완료된 3D 결과(GLB·썸네일)를 우리 버킷으로 옮기고 variant 를 ready 로 맞춘다."""
    glb_bytes_data = _fetch_url(state["glbUrl"])
    glb_key = model_glb_key(owner, char_id, variant, version)
    STORE.put_bytes(glb_key, glb_bytes_data, "model/gltf-binary")

    thumb_key = ""
    if state.get("thumbUrl"):
        try:
            thumb_data = _fetch_url(state["thumbUrl"])
            thumb_key = model_thumb_key(owner, char_id, variant, version)
            STORE.put_bytes(thumb_key, thumb_data, "image/png")
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.thumb_copy_failed char=%s err=%s", char_id, str(exc)[:200])

    _set_variant_3d(
        owner,
        char_id,
        variant,
        {"status": "ready", "glbKey": glb_key, "thumbKey": thumb_key, "error": ""},
    )
    _set_variant(owner, char_id, variant, {"status": "ready", "error": ""})


def run_build(owner: str, char_id: str, version: int) -> None:
    """빌드 파이프라인(백그라운드 스레드에서 호출). 한 variant 실패해도 다른 variant 는 계속."""
    try:
        char, _ = load_char(owner, char_id)

        # a. 분석
        analysis = analyze_sources(owner, char)

        def set_analysis(data: dict) -> None:
            data["analysis"] = analysis

        mutate_char(owner, char_id, set_analysis)

        # 참고 이미지(최대 4장)
        refs: list[bytes] = []
        for src in (char.get("sources") or [])[: settings.MAX_REFERENCE_IMAGES]:
            refs.append(source_jpeg(owner, char_id, src["fileId"]))

        # b~d. variant 별(순차. 실패해도 다음 variant 진행). 파드 안 실행 중 표시.
        for variant in settings.VARIANTS:
            _mark_running(owner, char_id, variant)
            try:
                _build_variant(owner, char_id, variant, version, analysis, refs)
            finally:
                _clear_running(owner, char_id, variant)

        # e. 최종 상태 롤업(하나라도 ready 면 ready, 진행 중이면 building, 전부 failed 면 failed)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        log.error("vnew.run_build_failed char=%s err=%s", char_id, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]

        def set_failed(data: dict) -> None:
            data["status"] = "failed"
            data.setdefault("analysis", {})
            data["buildError"] = message

        try:
            updated, _ = mutate_char(owner, char_id, set_failed)
            upsert_index(owner, updated)
        except Exception:  # noqa: BLE001
            pass


def start_build(owner: str, char_id: str, extra_prompt_ko: str | None = None) -> int:
    """build 요청: 진행 중이면 409, 아니면 version+1 로 variant 초기화하고 스레드 시작. 새 version 반환.

    extra_prompt_ko:
      - None  → 이전 extraPromptKo 를 그대로 유지(다음 빌드에도 유지).
      - ""    → extraPromptKo 를 지운다.
      - 문자열 → extraPromptKo 로 저장(≤ MAX_EXTRA_PROMPT_LEN, 넘으면 자른다).
    """

    def mutator(data: dict) -> int:
        if data.get("status") == "building" and not _is_stale(data):
            raise VpocError("vnew.build", "AlreadyBuilding", "이미 만들고 있습니다.", 409)
        if extra_prompt_ko is not None:
            cleaned = str(extra_prompt_ko or "").strip()[: settings.MAX_EXTRA_PROMPT_LEN]
            if cleaned:
                data["extraPromptKo"] = cleaned
            else:
                data.pop("extraPromptKo", None)
        version = int(data.get("version") or 0) + 1
        data["version"] = version
        data["status"] = "building"
        data["buildStartedAt"] = now_iso()
        data.pop("buildError", None)
        data["variants"] = {v: _empty_variant() for v in settings.VARIANTS}
        return version

    updated, version = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)

    thread = threading.Thread(
        target=run_build, args=(owner, char_id, version), daemon=True, name=f"vnew-build-{char_id}"
    )
    thread.start()
    return version


# ================================================================ 3D만 다시(retry3d)
#
# 시트·정면은 다시 그리지 않고(비용 절약) 그 variant 의 front/back 으로 3D 만 다시 만든다.
# 이전 requestArn 이 있고 fal 에서 아직 완료 결과를 받을 수 있으면 새로 제출하지 않고 가져온다.


def _retry3d_running(v: dict) -> bool:
    """이 variant 의 3D 재시도가 진행 중인가(submitted + 최근 시작, stall 안 넘음)."""
    m3d = v.get("model3d") or {}
    if m3d.get("status") != "submitted":
        return False
    started = _parse_iso(m3d.get("retryStartedAt") or "")
    if not started:
        # 시작 시각이 없으면(과거 build 잔여) 진행 중으로 보지 않는다 — 다시 시도할 수 있게.
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() <= settings.BUILD_STALL_S


def start_retry3d(owner: str, char_id: str, variant: str) -> int:
    """3D만 다시 만들기 요청. front/back 없으면 400, 진행 중이면 409. version 반환(스레드 시작)."""
    if variant not in settings.VARIANTS:
        raise VpocError("vnew.retry3d", "BadVariant", "알 수 없는 그림체입니다.", 404)

    def mutator(data: dict) -> int:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        if not v.get("frontKey") or not v.get("backKey"):
            raise VpocError(
                "vnew.retry3d", "NoImages", "정면·후면 이미지가 없어 3D 만 다시 만들 수 없습니다.", 400
            )
        if _retry3d_running(v):
            raise VpocError("vnew.retry3d", "AlreadyBuilding", "3D 를 이미 다시 만들고 있습니다.", 409)
        v["status"] = "model3d"
        v["error"] = ""
        m3d = v.setdefault("model3d", {})
        m3d["status"] = "submitted"
        m3d["error"] = ""
        m3d["retryStartedAt"] = now_iso()
        return int(data.get("version") or 0)

    updated, version = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)

    thread = threading.Thread(
        target=run_retry3d,
        args=(owner, char_id, variant, version),
        daemon=True,
        name=f"vnew-retry3d-{char_id}-{variant}",
    )
    thread.start()
    return version


def run_retry3d(owner: str, char_id: str, variant: str, version: int) -> None:
    """3D만 다시(백그라운드). 이전 requestArn 이 완료면 재사용(제출 0회), 아니면 새로 제출한다.

    이전 요청 조회가 'Downstream service unavailable' 만 연속으로 돌려주면(fal 이 요청을 잃음)
    기다리지 않고 같은 front/back 으로 바로 새로 제출한다(variant 당 최대 MAX_RESUBMITS 회).
    """
    try:
        char, _ = load_char(owner, char_id)
        v = (char.get("variants") or {}).get(variant) or {}
        front_key = v.get("frontKey") or ""
        back_key = v.get("backKey") or ""
        if not front_key or not back_key:
            raise VpocError("vnew.retry3d", "NoImages", "정면·후면 이미지가 없습니다.", 400)
        prior_arn = (v.get("model3d") or {}).get("requestArn") or ""

        # front/back 정사각을 미리 만들어 둔다('잃은 요청' 재제출에 쓴다).
        front = STORE.get_bytes(front_key)
        back = STORE.get_bytes(back_key)
        side = settings.MODEL_INPUT_SQUARE
        front_sq = _square_pad(front, side)
        back_sq = _square_pad(back, side)
        resubmit = _make_3d_resubmit(owner, char_id, variant, front_sq, back_sq)

        state = None
        # 1) 이전 요청이 있으면 먼저 조회 — 완료면 그 결과를 쓴다. unavailable 이 연속 LOST_AFTER 회면
        #    기다리지 않고 바로 새로 제출한다(resubmit 콜백). 그래도 못 받으면 아래 2) 로 넘어간다.
        if prior_arn:
            state = _wait_3d(prior_arn, resubmit=resubmit)
            if state["status"] != "Completed":
                state = None  # 실패·시간초과면 새로 제출한다.

        # 2) 이전 결과를 못 쓰면 front/back 으로 새로 제출한다.
        if state is None:
            request_arn = submit_3d(front_sq, back_sq)
            _charge_3d(owner, char_id, variant)
            _set_variant_3d(
                owner, char_id, variant, {"status": "submitted", "requestArn": request_arn, "error": ""}
            )
            state = _wait_3d(request_arn, resubmit=resubmit)
            if state["status"] != "Completed":
                _set_variant_3d(owner, char_id, variant, {"status": "failed", "error": state.get("error", "")})
                _set_variant(owner, char_id, variant, {"status": "failed", "error": state.get("error", "3D 실패")})
                _refresh_char_status(owner, char_id)
                return

        _copy_3d_result(owner, char_id, variant, version, state)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.retry3d_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)


# ================================================================ 자동 이어 만들기(resume)
#
# 상세 조회·reconcile 때 끊긴 variant 를 발견하면 그 variant 만 이어서 만든다.
# - 끊김 판단: status 가 queued/images/model3d 인데 heartbeatAt 이 VARIANT_STALL_S 넘게
#   갱신 안 됐고(없으면 buildStartedAt 기준) 이 파드에서 실행 중이 아님.
# - 선점: char.json 의 variant claim 을 조건부 쓰기로 잡은 쪽만 실행(여러 파드 중복 방지).
# - resumeCount 가 MAX_RESUME 를 넘으면 그 variant 만 failed(캐릭터는 다른 variant 에 따른다).


def _variant_stale(char: dict, variant: str) -> bool:
    """이 variant 가 진행 중 상태인데 heartbeatAt 이 오래됐는가(끊긴 것으로 본다)."""
    v = (char.get("variants") or {}).get(variant) or {}
    if v.get("status") not in ("queued", "images", "model3d"):
        return False
    last = _parse_iso(v.get("heartbeatAt") or "") or _parse_iso(char.get("buildStartedAt") or "")
    if not last:
        # heartbeatAt·buildStartedAt 둘 다 없으면(옛 데이터) 끊긴 것으로 본다.
        return True
    return (datetime.now(timezone.utc) - last).total_seconds() > settings.VARIANT_STALL_S


def _resume_variants(owner: str, char_id: str, char: dict) -> None:
    """조회 때 호출: 끊긴 variant 를 variant 단위로 이어 만든다(스레드 시작) 또는 cap 넘으면 failed."""
    for variant in settings.VARIANTS:
        if _is_running(owner, char_id, variant):
            continue
        if not _variant_stale(char, variant):
            continue
        v = (char.get("variants") or {}).get(variant) or {}
        resume_count = int(v.get("resumeCount") or 0)
        if resume_count >= settings.MAX_RESUME:
            # 재개 한도 초과 — 이 variant 만 failed(다른 variant 는 그대로).
            _fail_pending_3d(owner, char_id, variant, "중단되었습니다. 이 그림체만 다시 만들어 주세요")
            _set_variant(
                owner,
                char_id,
                variant,
                {"status": "failed", "error": "중단되었습니다. 이 그림체만 다시 만들어 주세요"},
            )
            _refresh_char_status(owner, char_id)
            continue
        # 선점(조건부 쓰기). 못 잡으면 다른 파드가 이어 만드는 중이라 넘어간다.
        token = _claim_variant(owner, char_id, variant)
        if not token:
            continue
        if not _mark_running(owner, char_id, variant):
            _release_claim(owner, char_id, variant, token)
            continue
        version = int(char.get("version") or 0)

        def set_resuming(data: dict, _variant=variant) -> None:
            vv = data.setdefault("variants", {}).setdefault(_variant, _empty_variant())
            vv["resumeCount"] = int(vv.get("resumeCount") or 0) + 1
            vv["heartbeatAt"] = now_iso()
            vv["error"] = ""

        try:
            mutate_char(owner, char_id, set_resuming)
        except Exception:  # noqa: BLE001
            _clear_running(owner, char_id, variant)
            _release_claim(owner, char_id, variant, token)
            continue

        thread = threading.Thread(
            target=run_resume_variant,
            args=(owner, char_id, variant, version, token),
            daemon=True,
            name=f"vnew-resume-{char_id}-{variant}",
        )
        thread.start()


def run_resume_variant(owner: str, char_id: str, variant: str, version: int, token: str) -> None:
    """끊긴 variant 를 이어서 만든다(백그라운드).

    - sheet/front/back 이 다 있으면 그림은 다시 그리지 않고 3D 만(run_retry3d 처럼 이전 requestArn
      이 있으면 조회부터).
    - 그림이 덜 됐으면 분석 결과를 재사용해 그 variant 를 처음부터 다시 그린다.
    선점 token 이 더는 내 것이 아니면(다른 파드가 가져감) 아무것도 하지 않는다.
    """
    try:
        if not _holds_claim(owner, char_id, variant, token):
            return
        char, _ = load_char(owner, char_id)
        v = (char.get("variants") or {}).get(variant) or {}
        has_images = bool(v.get("sheetKey") and v.get("frontKey") and v.get("backKey"))
        if has_images:
            # 그림은 그대로 두고 3D 만 이어서(이전 requestArn 재사용 포함).
            run_retry3d(owner, char_id, variant, version)
            return
        # 그림이 덜 됐으면 분석 결과를 재사용해 처음부터 다시 그린다.
        analysis = char.get("analysis") or {}
        if not analysis.get("appearanceEn"):
            analysis = analyze_sources(owner, char)

            def set_analysis(data: dict) -> None:
                data["analysis"] = analysis

            mutate_char(owner, char_id, set_analysis)
        refs: list[bytes] = []
        for src in (char.get("sources") or [])[: settings.MAX_REFERENCE_IMAGES]:
            refs.append(source_jpeg(owner, char_id, src["fileId"]))
        _build_variant(owner, char_id, variant, version, analysis, refs)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.resume_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)
    finally:
        _clear_running(owner, char_id, variant)
        _release_claim(owner, char_id, variant, token)


# ================================================================ 그림체만 다시(variant rebuild)
#
# 그 variant 만 처음부터(분석 결과 재사용, 없으면 분석부터). 진행 중이면 409.


def start_rebuild_variant(owner: str, char_id: str, variant: str, extra_prompt_ko: str | None = None) -> int:
    """그 그림체만 처음부터 다시 만들기. 진행 중이면 409. version 반환(스레드 시작).

    extra_prompt_ko: None 이면 이전 extraPromptKo 유지, "" 면 삭제, 문자열이면 저장(≤ 최대 길이).
    """
    if variant not in settings.VARIANTS:
        raise VpocError("vnew.rebuild", "BadVariant", "알 수 없는 그림체입니다.", 404)
    if _is_running(owner, char_id, variant):
        raise VpocError("vnew.rebuild", "AlreadyBuilding", "이 그림체를 이미 만들고 있습니다.", 409)

    def mutator(data: dict) -> int:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        # 진행 중(끊기지 않은)이면 409.
        if v.get("status") in ("queued", "images", "model3d") and not _variant_stale(data, variant):
            raise VpocError("vnew.rebuild", "AlreadyBuilding", "이 그림체를 이미 만들고 있습니다.", 409)
        if extra_prompt_ko is not None:
            cleaned = str(extra_prompt_ko or "").strip()[: settings.MAX_EXTRA_PROMPT_LEN]
            if cleaned:
                data["extraPromptKo"] = cleaned
            else:
                data.pop("extraPromptKo", None)
        version = int(data.get("version") or 0)
        # 이 variant 만 초기화(queued). 다른 variant 는 건드리지 않는다.
        fresh = _empty_variant()
        fresh["status"] = "queued"
        fresh["heartbeatAt"] = now_iso()
        data["variants"][variant] = fresh
        return version

    updated, version = mutate_char(owner, char_id, mutator)
    # 캐릭터 전체 status 롤업(다시 building 이 될 수 있다).
    _refresh_char_status(owner, char_id)

    thread = threading.Thread(
        target=run_rebuild_variant,
        args=(owner, char_id, variant, version),
        daemon=True,
        name=f"vnew-rebuild-{char_id}-{variant}",
    )
    thread.start()
    return version


def run_rebuild_variant(owner: str, char_id: str, variant: str, version: int) -> None:
    """그림체 하나를 처음부터(백그라운드). 분석 결과가 없으면 분석부터 하고 재사용한다."""
    if not _mark_running(owner, char_id, variant):
        return
    try:
        char, _ = load_char(owner, char_id)
        analysis = char.get("analysis") or {}
        if not analysis.get("appearanceEn"):
            analysis = analyze_sources(owner, char)

            def set_analysis(data: dict) -> None:
                data["analysis"] = analysis

            mutate_char(owner, char_id, set_analysis)
        refs: list[bytes] = []
        for src in (char.get("sources") or [])[: settings.MAX_REFERENCE_IMAGES]:
            refs.append(source_jpeg(owner, char_id, src["fileId"]))
        _build_variant(owner, char_id, variant, version, analysis, refs)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.rebuild_variant_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)
    finally:
        _clear_running(owner, char_id, variant)


def _rollup_status(char: dict) -> str:
    """variant 상태를 모아 캐릭터 전체 status 를 매긴다.

    - 하나라도 ready → "ready" (다만 다른 variant 가 아직 진행 중이면 "building")
    - ready 가 없고 진행 중(queued/images/model3d)이 있으면 → "building"
    - 전부 failed → "failed"
    한 variant 가 끊겨도(failed) 다른 variant 가 ready 면 캐릭터는 failed 가 아니다.
    """
    statuses = [(char.get("variants") or {}).get(v, {}).get("status") for v in settings.VARIANTS]
    in_progress = any(s in ("queued", "images", "model3d") for s in statuses)
    any_ready = any(s == "ready" for s in statuses)
    if in_progress:
        return "building"
    if any_ready:
        return "ready"
    return "failed"


def _refresh_char_status(owner: str, char_id: str) -> None:
    """variant 상태를 모아 캐릭터 전체 status 를 다시 매긴다(_rollup_status 규칙)."""
    try:
        char, _ = load_char(owner, char_id)
        final = _rollup_status(char)

        def set_status(data: dict) -> None:
            data["status"] = final

        updated, _ = mutate_char(owner, char_id, set_status)
        upsert_index(owner, updated)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.refresh_status_failed char=%s err=%s", char_id, str(exc)[:200])





def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_stale(char: dict) -> bool:
    """building 이 BUILD_STALL_S 넘게 그대로면 중단된 것으로 본다(옛 호환용)."""
    if char.get("status") != "building":
        return False
    started = _parse_iso(char.get("buildStartedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > settings.BUILD_STALL_S


def reconcile_stale(owner: str, char_id: str) -> dict:
    """조회 때 호출: 끊긴 variant 가 있으면 그 variant 만 이어서 만든다(variant 단위 자동 재개).

    한 variant 가 끊겨도 캐릭터 전체를 failed 로 만들지 않는다(이전 30분 전체 실패 규칙을 대체).
    _resume_variants 가 끊긴 variant 를 이어 만들거나(스레드) 재개 한도 초과면 그 variant 만
    failed 로 바꾼다. 그 뒤 캐릭터 전체 status 를 다시 롤업한 최신 char 를 돌려준다.

    아직 한 번도 만든 적 없는 draft(version 0)는 건드리지 않는다 — queued variant 는 '끊긴 것'이
    아니라 '아직 시작 안 한 것'이므로 자동 재개도, 상태 롤업도 하지 않는다.
    """
    char, _ = load_char(owner, char_id)
    if int(char.get("version") or 0) < 1 or char.get("status") == "draft":
        return char
    _resume_variants(owner, char_id, char)
    char, _ = load_char(owner, char_id)
    rolled = _rollup_status(char)
    if rolled != char.get("status"):
        def mutator(data: dict) -> None:
            data["status"] = rolled

        updated, _ = mutate_char(owner, char_id, mutator)
        upsert_index(owner, updated)
        return updated
    return char


# ================================================================ 상세 뷰(presigned)


def _presign(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.presign_failed key=%s err=%s", key, str(exc)[:150])
        return ""


def detail_view(owner: str, char_id: str) -> dict:
    """상세: 이미지·썸네일을 presigned URL 로 담는다. building 이 멈췄으면 failed 로 조정."""
    char = reconcile_stale(owner, char_id)
    variants_out: dict = {}
    for variant in settings.VARIANTS:
        v = (char.get("variants") or {}).get(variant) or _empty_variant()
        model3d = v.get("model3d") or {}
        variants_out[variant] = {
            "status": v.get("status"),
            "error": v.get("error") or "",
            "warning": v.get("warning") or "",
            "resumeCount": int(v.get("resumeCount") or 0),
            "sheetUrl": _presign(v.get("sheetKey")),
            "frontUrl": _presign(v.get("frontKey")),
            "backUrl": _presign(v.get("backKey")),
            "model3d": {
                "status": model3d.get("status"),
                "error": model3d.get("error") or "",
                "hasGlb": bool(model3d.get("glbKey")),
                "thumbUrl": _presign(model3d.get("thumbKey")),
            },
        }
    return {
        "id": char["id"],
        "name": char.get("name") or "",
        "status": char.get("status"),
        "version": char.get("version", 0),
        "createdAt": char.get("createdAt"),
        "updatedAt": char.get("updatedAt"),
        "buildError": char.get("buildError") or "",
        "sources": char.get("sources") or [],
        "analysis": char.get("analysis") or {},
        "extraPromptKo": char.get("extraPromptKo") or "",
        "variants": variants_out,
        "costumes": char.get("costumes") or [],
    }


# ================================================================ GLB 스트리밍


def glb_bytes(owner: str, char_id: str, variant: str) -> bytes:
    """해당 variant 의 GLB 바이트. 없으면 404."""
    char, _ = load_char(owner, char_id)
    v = (char.get("variants") or {}).get(variant) or {}
    glb_key = (v.get("model3d") or {}).get("glbKey") or ""
    if not glb_key:
        raise VpocError("vnew.glb", "NotFound", "3D 모델이 아직 없습니다.", 404)
    return STORE.get_bytes(glb_key)
