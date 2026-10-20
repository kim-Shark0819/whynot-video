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
        "model3d": {
            "status": "queued",  # queued|submitted|ready|failed
            "requestArn": "",
            "glbKey": "",
            "thumbKey": "",
            "error": "",
        },
        "error": "",
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
            "outfitEn": "",
            "model": "",
        },
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
    "You analyze Korean webtoon character cut images and describe one character. "
    "Answer ONLY by calling the emit_character tool exactly once. "
    "descriptionKo must be 2-3 Korean sentences. "
    "appearanceEn is ONE English sentence about face, hair, body type, age range, glasses, etc. "
    "outfitEn is ONE English sentence about clothing and shoes. Do not add text outside the tool."
)

_ANALYZE_TOOL = {
    "name": "emit_character",
    "description": "Describe the character seen in the reference images.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "nameGuess": {"type": "string"},
                "descriptionKo": {"type": "string"},
                "appearanceEn": {"type": "string"},
                "outfitEn": {"type": "string"},
            },
            "required": ["descriptionKo", "appearanceEn", "outfitEn"],
        }
    },
}


def analyze_sources(owner: str, char: dict) -> dict:
    """업로드 이미지를 Astra 에 먼저, 실패하면 Claude 분석 체인으로 보내 분석 JSON 을 받는다.

    실제 쓴 모델을 analysis.model 에 남긴다. 반환: {nameGuess,descriptionKo,appearanceEn,outfitEn,model}.
    """
    sources = char.get("sources") or []
    if not sources:
        raise VpocError("vnew.analyze", "NoSources", "분석할 이미지가 없습니다.", 400)

    jpegs: list[bytes] = []
    for src in sources[: settings.MAX_ANALYSIS_IMAGES]:
        jpegs.append(source_jpeg(owner, char["id"], src["fileId"]))

    blocks: list[dict] = [
        {"text": "다음 웹툰 컷들에 나오는 같은 캐릭터 한 명을 분석해 주세요."}
    ]
    for jpeg in jpegs:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

    # Astra 먼저, 실패하면 Claude 분석 체인.
    for model in (settings.ASTRA_MODEL, settings.ANALYZE_FALLBACK_MODEL):
        try:
            payload, usage = _analyze(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.analyze.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        return {
            "nameGuess": str(payload.get("nameGuess") or "").strip(),
            "descriptionKo": str(payload.get("descriptionKo") or "").strip(),
            "appearanceEn": str(payload.get("appearanceEn") or "").strip(),
            "outfitEn": str(payload.get("outfitEn") or "").strip(),
            "model": str(usage.get("model") or model),
        }
    raise VpocError("vnew.analyze", "AnalyzeFailed", "캐릭터 분석에 실패했습니다.", 502)


# ================================================================ 프롬프트(b)


def _who(analysis: dict) -> str:
    appearance = analysis.get("appearanceEn") or ""
    outfit = analysis.get("outfitEn") or ""
    return f"The character: {appearance} Wearing: {outfit}".strip()


def _sheet_prompt(variant: str, analysis: dict) -> str:
    who = _who(analysis)
    if variant == "real":
        return (
            "Photorealistic live-action character turnaround reference sheet of a real Korean actor "
            "playing the character from the reference images. "
            + who
            + " Five full-body views side by side on a plain white background: front, three-quarter "
            "front, side profile, three-quarter back, back. Same person, same outfit in every view. "
            "Real photograph, natural skin texture, soft studio lighting. No text, no labels."
        )
    return (
        "Character turnaround reference sheet. "
        + who
        + " Five full-body views side by side on a plain white background: front, three-quarter "
        "front, side profile, three-quarter back, back. Same person, same outfit in every view. "
        "Keep the exact Korean webtoon art style of the references: clean ink line art, cel shading. "
        "No text, no speech bubbles, no labels."
    )


def _front_prompt(variant: str, analysis: dict) -> str:
    who = _who(analysis)
    if variant == "real":
        return (
            "Single full-body front view photograph of a real Korean actor playing the character. "
            + who
            + " Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes "
            "visible, centered, plain white background, no shadow. Real photograph, natural skin "
            "texture, soft studio lighting. No text."
        )
    return (
        "Single full-body front view. "
        + who
        + " Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes "
        "visible, centered, plain white background, no shadow. Keep the exact Korean webtoon art "
        "style of the references: clean ink line art, cel shading. No text."
    )


# ================================================================ 이미지 자르기


def _crop_fifth_panel(sheet_jpeg: bytes) -> bytes:
    """시트를 가로 5등분해 5번째 칸(후면)을 떼어 낸다."""
    with Image.open(io.BytesIO(sheet_jpeg)) as image:
        image = image.convert("RGB")
        width, height = image.size
        panel_w = width / 5.0
        left = int(round(panel_w * 4))
        crop = image.crop((left, 0, width, height))
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


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
    """Hunyuan3D v3 에 제출하고 requestArn 을 돌려준다(data URI 입력, face_count 120000)."""
    eid = settings.HUNYUAN_ENDPOINT
    body = {
        "input_image_url": _data_uri(front_square),
        "back_image_url": _data_uri(back_square),
        "enable_pbr": True,
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


def _wait_3d(request_arn: str) -> dict:
    """완료까지 폴링(5초 간격, 최대 15분)."""
    deadline = time.time() + settings.POLL_MAX_S
    while True:
        state = poll_3d(request_arn)
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


def _build_variant(
    owner: str, char_id: str, variant: str, version: int, analysis: dict, refs: list[bytes]
) -> None:
    """한 variant 의 그림 + 3D. 실패하면 그 variant 만 failed 로 두고 예외를 올리지 않는다."""
    try:
        _set_variant(owner, char_id, variant, {"status": "images"})

        # 시트(2560x1440)
        sheet = _seedream(_sheet_prompt(variant, analysis), refs, size=settings.SHEET_SIZE)
        _charge_seedream(owner, char_id, variant, "sheet")
        s_key = sheet_key(owner, char_id, variant, version)
        STORE.put_bytes(s_key, sheet, "image/jpeg")

        # 정면 전신(1440x2560)
        # 시트를 첫 참고로 넣어 정면이 시트(후면 칸)와 같은 인물·옷이 되게 한다 — 3D 정면·후면 일치.
        front = _seedream(_front_prompt(variant, analysis), [sheet] + list(refs)[:3], size=settings.FRONT_SIZE)
        _charge_seedream(owner, char_id, variant, "front")
        f_key = front_key(owner, char_id, variant, version)
        STORE.put_bytes(f_key, front, "image/jpeg")

        # 후면 = 시트 5번째 칸
        back = _crop_fifth_panel(sheet)
        b_key = back_key(owner, char_id, variant, version)
        STORE.put_bytes(b_key, back, "image/jpeg")

        _set_variant(
            owner,
            char_id,
            variant,
            {"status": "model3d", "sheetKey": s_key, "frontKey": f_key, "backKey": b_key},
        )

        # 3D: front·back 정사각 패딩 → data URI 제출 → 완료까지 폴링 → 복사
        side = settings.MODEL_INPUT_SQUARE
        front_sq = _square_pad(front, side)
        back_sq = _square_pad(back, side)
        request_arn = submit_3d(front_sq, back_sq)
        _charge_3d(owner, char_id, variant)
        _set_variant_3d(owner, char_id, variant, {"status": "submitted", "requestArn": request_arn})

        state = _wait_3d(request_arn)
        if state["status"] != "Completed":
            _set_variant_3d(owner, char_id, variant, {"status": "failed", "error": state.get("error", "")})
            _set_variant(owner, char_id, variant, {"status": "failed", "error": state.get("error", "3D 실패")})
            return

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
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.build_variant_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})


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

        # b~d. variant 별(순차. 실패해도 다음 variant 진행)
        for variant in settings.VARIANTS:
            _build_variant(owner, char_id, variant, version, analysis, refs)

        # e. 최종 상태: 하나라도 ready 면 ready, 전부 실패면 failed
        char, _ = load_char(owner, char_id)
        statuses = [(char.get("variants") or {}).get(v, {}).get("status") for v in settings.VARIANTS]
        final = "ready" if any(s == "ready" for s in statuses) else "failed"

        def set_status(data: dict) -> None:
            data["status"] = final

        updated, _ = mutate_char(owner, char_id, set_status)
        upsert_index(owner, updated)
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


def start_build(owner: str, char_id: str) -> int:
    """build 요청: 진행 중이면 409, 아니면 version+1 로 variant 초기화하고 스레드 시작. 새 version 반환."""

    def mutator(data: dict) -> int:
        if data.get("status") == "building" and not _is_stale(data):
            raise VpocError("vnew.build", "AlreadyBuilding", "이미 만들고 있습니다.", 409)
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


# ================================================================ stale(파드 재시작 대비)


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_stale(char: dict) -> bool:
    """building 이 BUILD_STALL_S 넘게 그대로면 중단된 것으로 본다."""
    if char.get("status") != "building":
        return False
    started = _parse_iso(char.get("buildStartedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > settings.BUILD_STALL_S


def reconcile_stale(owner: str, char_id: str) -> dict:
    """조회 때 호출: building 이 30분 넘게 멈춰 있으면 failed 로 바꾼다. 최신 char 반환."""
    char, _ = load_char(owner, char_id)
    if not _is_stale(char):
        return char

    def mutator(data: dict) -> None:
        data["status"] = "failed"
        data["buildError"] = "생성이 중단되었습니다. 다시 만들어 주세요."

    updated, _ = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)
    return updated


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
