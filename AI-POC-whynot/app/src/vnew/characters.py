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
import copy
import io
import json
import logging
import math
import re
import secrets
import socket
import struct
import threading
import time
import urllib.request
from datetime import datetime, timezone

from PIL import Image, ImageOps

from ..vpoc import fal as vpoc_fal
from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import seedream as vpoc_seedream
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.fal import _parse_arn, _queue_app  # noqa: PLC2701 — fal 큐 경로 규칙 재사용
from ..vpoc.store import STORE

from . import char_fidelity
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


# ================================================================ 대기 variant 선점 유지(keepalive)
#
# run_build 가 variant 를 차례로 만드는 동안, 아직 시작 안 한(대기) variant 는 그 자리에 claim·
# heartbeat 만 찍힌 채 멈춰 있다. 앞 variant 가 오래 걸리면 그 사이 claimedAt·heartbeatAt 이
# 10분을 넘겨, 조회 자동 재개가 대기 variant 를 '끊긴 작업'으로 오인해 중복 실행할 수 있다.
# 그래서 앞 variant 의 heartbeat 갱신마다, 등록된 대기 variant 의 claim·heartbeat 도 함께 찍는다.
#
# _KEEPALIVE[(owner,char_id)] = {variant: token} — 이 token 이 여전히 claim 인 대기 variant 만 찍는다.

_KEEPALIVE: dict[tuple[str, str], dict[str, str]] = {}
_KEEPALIVE_LOCK = threading.Lock()


def _set_keepalive(owner: str, char_id: str, pending: dict[str, str]) -> None:
    """이 (owner,char_id) 의 대기 variant→token 집합을 통째로 바꾼다(비면 등록 해제)."""
    key = (owner, char_id)
    with _KEEPALIVE_LOCK:
        if pending:
            _KEEPALIVE[key] = dict(pending)
        else:
            _KEEPALIVE.pop(key, None)


def _keepalive_pending(owner: str, char_id: str) -> dict[str, str]:
    with _KEEPALIVE_LOCK:
        return dict(_KEEPALIVE.get((owner, char_id)) or {})



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


def left_key(owner: str, char_id: str, variant: str, version: int) -> str:
    """3D 옆모습(char-fidelity R4): 캐릭터 왼쪽이 카메라를 향하고 얼굴이 화면 왼쪽인 그림. 3D 에 쓸 수 있을 때만 저장한다."""
    return f"{_variant_dir(owner, char_id, variant, version)}/left.jpg"


def right_key(owner: str, char_id: str, variant: str, version: int) -> str:
    """3D 옆모습(char-fidelity R4): 캐릭터 오른쪽이 카메라를 향하고 얼굴이 화면 오른쪽인 그림."""
    return f"{_variant_dir(owner, char_id, variant, version)}/right.jpg"


def model_glb_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/model.glb"


def model_thumb_key(owner: str, char_id: str, variant: str, version: int) -> str:
    return f"{_variant_dir(owner, char_id, variant, version)}/thumb.png"


def outfit_front_key(owner: str, char_id: str, outfit_id: str, variant: str) -> str:
    """추가 복장 이미지(정면). 그림체(variant)별로 둔다. 3D 는 만들지 않는다."""
    return f"{_root(owner)}/{char_id}/outfits/{outfit_id}/{variant}/front.jpg"


def outfit_back_key(owner: str, char_id: str, outfit_id: str, variant: str) -> str:
    """추가 복장 이미지(후면)."""
    return f"{_root(owner)}/{char_id}/outfits/{outfit_id}/{variant}/back.jpg"


def casting_key(owner: str, char_id: str, cand_id: str) -> str:
    """실사 캐스팅 후보 사진(vnew-live-fx 묶음 B). fal 결과를 우리 버킷 이 키로 옮긴다(IRSA users/* 안)."""
    return f"{_root(owner)}/{char_id}/casting/{cand_id}.jpg"


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


def _empty_outfit_variant() -> dict:
    """추가 복장 한 그림체(정면·후면). 3D 는 만들지 않는다."""
    return {
        "status": "queued",  # queued|images|ready|failed
        "frontKey": "",
        "backKey": "",
        "error": "",
    }


def _new_outfit_id() -> str:
    return f"o_{secrets.token_hex(4)}"


def _empty_outfit(label_ko: str, prompt_en: str, source: str) -> dict:
    """추가 복장 한 벌. source 는 'auto'(분석 후보) 또는 'user'(사용자 추가)."""
    return {
        "id": _new_outfit_id(),
        "labelKo": (label_ko or "").strip() or (prompt_en or "").strip(),
        "promptEn": (prompt_en or "").strip(),
        "source": source if source in ("auto", "user") else "user",
        "variants": {v: _empty_outfit_variant() for v in settings.VARIANTS},
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
            "baseOutfitEn": "",
            "outfitLayersEn": [],
            "altOutfitsEn": [],
            "expressionsKo": [],
            "extraEn": "",
            "model": "",
        },
        "extraPromptKo": "",
        "variants": {v: _empty_variant() for v in settings.VARIANTS},
        "version": 0,
        "costumes": [],
        # 추가 복장(기본 복장 위에 더 입히는 옷차림). analysis.outfitLayersEn 에서 auto 후보를
        # 채우고, 사용자가 추가할 수 있다. 각 항목은 _empty_outfit() 모양.
        "outfits": [],
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
        system_text=_analyze_system(),
        blocks=blocks,
        tool=_analyze_tool(),
        max_tokens=_ANALYZE_MAX_TOKENS_FIDELITY if settings.VNEW_CHAR_FIDELITY else 1200,
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
    "baseOutfitEn is the MOST BASIC single outfit seen on the character with every extra layer "
    "(jacket, coat, cardigan, hoodie) removed — describe it very concretely: top garment type, "
    "colour and sleeve length, bottoms, shoes and any accessory. This is the outfit the 3D model "
    "is built from, so it must be one consistent set, not a mix. "
    "outfitLayersEn lists the EXTRA layers or OTHER outfits seen over or instead of the base outfit "
    "(e.g. a black suit jacket over the shirt, a light blue hoodie) — each item is an object "
    "{labelKo, promptEn} where labelKo is a short Korean name and promptEn is a short English "
    "phrase describing only the extra garment to add on top of the base outfit. "
    "outfitEn is the SAME as baseOutfitEn (kept for compatibility). "
    "altOutfitsEn lists other outfits seen (reference only). "
    "expressionsKo lists the facial expressions you can see (Korean words). "
    "If the user gives an extra request (extraPromptKo), turn it into a short English drawing "
    "instruction in extraEn; otherwise leave extraEn empty. Do not add text outside the tool."
)

# char-fidelity(2026-10-30, design §4.1): VNEW_CHAR_FIDELITY 켬일 때의 분석 SYSTEM. 기준 사진 한 장의 옷을 그대로(겹옷 포함)
# 기본 복장으로, 다른 그림의 옷차림은 한 벌씩, 한쪽 소품은 화면 기준 좌우로 받는다. 끔이면 _ANALYZE_SYSTEM 그대로.
_ANALYZE_SYSTEM_FIDELITY = (
    "You analyze Korean webtoon character cut images and describe ONE single character. "
    "All images show ONE single character. If several figures appear (even in one image), "
    "they are the SAME person drawn with different expressions, poses, angles or outfits — "
    "never different people. Ignore text and speech bubbles. "
    "Answer ONLY by calling the emit_character tool exactly once. "
    "Each image follows a text label 'Image N:'; images are numbered from 0 in the order given. "
    "descriptionKo must be 2-3 Korean sentences. "
    "appearanceEn is ONE English sentence about face, hair, body type, age range, glasses, etc. "
    "styleEn is 1-2 English sentences describing the EXACT drawing style of the reference art: "
    "line weight and texture (e.g. thin sketchy pencil-like lines), coloring method "
    "(flat / soft pastel / cel), palette, shading and blush, and how the eyes and hair are drawn. "
    "baseSourceIndex is the number of the ONE image where the character's outfit is shown most clearly and "
    "completely: the most of the body visible (head to shoes is best) and the least hidden. If that image shows "
    "several figures, baseWhereEn says which one (e.g. 'large bottom figure'); otherwise leave it empty. "
    "baseOutfitEn is exactly the outfit drawn on that figure in that image, as drawn: keep every layer (do not "
    "remove a jacket, overshirt, coat, cardigan or hoodie), do not mix in clothes from other images, and keep the "
    "drawn colours (e.g. 'pale grey-blue', not 'blue'). Describe each piece concretely: garment type, colour, fit "
    "(loose, slim, oversized), how it is worn (untucked, sleeves rolled up, buttons open), rips or patterns and "
    "where they are, the shoes and every accessory. If a part is not visible in that image, take it from another "
    "image only when the same outfit is clearly visible there; otherwise choose a plain item that fits and say so "
    "briefly (e.g. 'shoes not shown, plain white sneakers assumed'). This is the outfit the 3D model is built from. "
    "Never write left or right in baseOutfitEn or outfitLayersEn; put every item that is on one side only in "
    "sidedItemsEn. "
    "outfitLayersEn lists the OTHER outfits seen in other images, one item per outfit: {labelKo, promptEn, "
    "sourceIndex, whereEn}. promptEn is the WHOLE outfit as drawn in that image, with the same detail as "
    "baseOutfitEn (e.g. 'white crew-neck T-shirt under an open sage-green overshirt, light-wash straight jeans, "
    "white sneakers'), not one garment to add; labelKo is a short Korean name; sourceIndex is the image number; "
    "whereEn says which figure when that image shows several, otherwise empty. Do not list the base outfit again. "
    "sidedItemsEn lists items or marks that are on ONE side of the body only (wristwatch, bracelet, ring, single "
    "earring, a bag strap on one shoulder, eyepatch, bandage, tattoo, hair parting, a logo on one side of the "
    "chest): {itemEn, labelKo, partEn, sightings}. itemEn is a short noun phrase with no left/right words (e.g. "
    "'round pale-faced wristwatch with a brown leather strap'); labelKo is a short Korean name; partEn is the body "
    "part; sightings lists every image where you can see it as {sourceIndex, imageSide, facing}. imageSide is the "
    "side of the PICTURE as you look at it: 'left' when the item is on the viewer's left of that figure's body "
    "midline, 'right' when on the viewer's right. Do NOT convert it to the character's own left or right; report "
    "only what you see on the picture. facing is 'toward' when the figure faces the viewer (front or three-quarter "
    "front), 'away' when it shows its back (back or three-quarter back), 'unclear' for side profiles or when you "
    "cannot tell. "
    "outfitEn is the SAME as baseOutfitEn (kept for compatibility). "
    "altOutfitsEn lists other outfits seen (reference only). "
    "expressionsKo lists the facial expressions you can see (Korean words). "
    "If the user gives an extra request (extraPromptKo), turn it into a short English drawing "
    "instruction in extraEn; otherwise leave extraEn empty. Do not add text outside the tool."
)

# char-fidelity(design §2·§6.6): 분석 출력이 길어져 max_tokens 를 올리고(결정 S9, 켬만), 켬 프롬프트는 Seedream 이 3,000자에서
# 자르기 전에 2,900자 안으로 맞춘다(결정 S8).
_ANALYZE_MAX_TOKENS_FIDELITY = 2000
_PROMPT_MAX = 2900

# vnew-live-fx RA6.1(참고 구현 vpoc/llm.py:1255-1258): 켬이면 머리색을 그림 그대로, 옷 무늬는 위치까지 쓰게 한다.
# 이미 분석된 캐릭터는 다시 읽지 않는다(다음 분석부터 적용, RA6.2).
_ANALYZE_DETAIL_RULE = (
    " Name the hair color exactly as drawn (e.g. 'ash blonde' rather than 'light brown' when the drawing is pale "
    "blonde). For clothing patterns in baseOutfitEn and outfitLayersEn, say where the pattern is (e.g. 'a white "
    "stripe down each sleeve, plain black body', 'a logo on the chest')."
)


def _analyze_system() -> str:
    """캐릭터 분석 SYSTEM. VNEW_FX 켬이면 머리색·무늬 위치 규칙을, VNEW_CASTING 켬이면 성별 문장을 끝에 더한다(둘 다 끔이면 지금 상수 그대로).

    VNEW_CHAR_FIDELITY 켬이면 바탕을 충실 SYSTEM(_ANALYZE_SYSTEM_FIDELITY)으로 바꾼다(끔이면 지금과 같다).
    """
    base = _ANALYZE_SYSTEM_FIDELITY if settings.VNEW_CHAR_FIDELITY else _ANALYZE_SYSTEM
    text = base + _ANALYZE_DETAIL_RULE if settings.VNEW_FX else base
    if settings.VNEW_CASTING:
        text += _ANALYZE_GENDER_RULE
    return text


# vnew-live-fx RB2.1(실사 캐스팅, 결정 2): 켬이면 분석 도구에 선택 필드 genderEn 을 더하고 SYSTEM 에 이 문장을 더한다.
# 이미 분석된 캐릭터는 다시 분석하지 않는다(genderEn 없음 = unknown 으로 본다).
_ANALYZE_GENDER_RULE = (
    " genderEn is the character's gender as drawn: male, female or unknown (unknown when the drawings do not make it clear)."
)
_ANALYZE_GENDER_FIELD = {"type": "string", "enum": ["male", "female", "unknown"]}


def _analyze_tool() -> dict:
    """캐릭터 분석 도구. VNEW_CASTING 켬이면 _ANALYZE_TOOL 사본에 genderEn(required 아님)을 더한다. 끔이면 상수 그대로.

    VNEW_CHAR_FIDELITY 켬이면 사본에 충실 필드(_ANALYZE_FIDELITY_FIELDS)를 더하고 baseSourceIndex 를 required 에 넣는다.
    켜진 설정이 없으면 지금처럼 같은 객체(_ANALYZE_TOOL)를 돌려준다.
    """
    if not settings.VNEW_CASTING and not settings.VNEW_CHAR_FIDELITY:
        return _ANALYZE_TOOL
    tool = copy.deepcopy(_ANALYZE_TOOL)
    if settings.VNEW_CHAR_FIDELITY:
        schema = tool["inputSchema"]["json"]
        schema["properties"].update(copy.deepcopy(_ANALYZE_FIDELITY_FIELDS))
        schema["required"] = list(schema["required"]) + ["baseSourceIndex"]
    if not settings.VNEW_CASTING:
        return tool
    tool["inputSchema"]["json"]["properties"]["genderEn"] = dict(_ANALYZE_GENDER_FIELD)
    return tool


def _with_gender_en(payload: dict, analysis: dict) -> dict:
    """VNEW_CASTING 켬이면 분석 결과에 genderEn(male | female | unknown, 모르는 값 unknown)을 둔다. 끔이면 그대로."""
    if settings.VNEW_CASTING:
        from . import casting  # noqa: PLC0415 — casting 이 characters 를 읽으므로 늦게 import

        analysis["genderEn"] = casting.normalize_gender(payload.get("genderEn"))
    return analysis


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
                "baseOutfitEn": {"type": "string"},
                "outfitEn": {"type": "string"},
                "outfitLayersEn": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "labelKo": {"type": "string"},
                            "promptEn": {"type": "string"},
                        },
                    },
                },
                "altOutfitsEn": {"type": "array", "items": {"type": "string"}},
                "expressionsKo": {"type": "array", "items": {"type": "string"}},
                "extraEn": {"type": "string"},
            },
            "required": ["descriptionKo", "appearanceEn", "baseOutfitEn"],
        }
    },
}

# char-fidelity(design §4.3): 켬일 때 분석 도구에 더하는 필드. 부위 enum 은 char_fidelity.PARTS 의 키 목록이다.
_PART_ENUM = list(char_fidelity.PARTS)

_ANALYZE_FIDELITY_FIELDS = {
    "baseSourceIndex": {"type": "integer"},
    "baseWhereEn": {"type": "string"},
    # outfitLayersEn 은 같은 이름으로 덮는다(labelKo·promptEn 은 지금 그대로, 두 필드를 더한다).
    "outfitLayersEn": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "labelKo": {"type": "string"},
                "promptEn": {"type": "string"},
                "sourceIndex": {"type": "integer"},
                "whereEn": {"type": "string"},
            },
        },
    },
    "sidedItemsEn": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "itemEn": {"type": "string"},
                "labelKo": {"type": "string"},
                "partEn": {"type": "string", "enum": _PART_ENUM},
                "sightings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sourceIndex": {"type": "integer"},
                            "imageSide": {"type": "string", "enum": ["left", "right"]},
                            "facing": {"type": "string", "enum": ["toward", "away", "unclear"]},
                        },
                        "required": ["sourceIndex", "imageSide", "facing"],
                    },
                },
            },
            "required": ["itemEn", "partEn", "sightings"],
        },
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
    # char-fidelity: 분석에 보낸 그림의 fileId(순서 = 그림 번호). 켬일 때 기준 사진·사진 속 옷 번호를 fileId 로 바꾼다.
    file_ids = [src["fileId"] for src in sources[: settings.MAX_ANALYSIS_IMAGES]]
    fidelity = settings.VNEW_CHAR_FIDELITY

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
    for index, jpeg in enumerate(jpegs):
        if fidelity:
            # 켬: 그림마다 바로 앞에 번호 글(0부터). baseSourceIndex·sourceIndex 가 이 번호를 쓴다(R1.3).
            blocks.append({"text": f"Image {index}:"})
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
        # baseOutfitEn 이 가장 기본 한 벌. 옛 응답(baseOutfitEn 없음)은 outfitEn 으로 대신한다.
        base_outfit = str(payload.get("baseOutfitEn") or payload.get("outfitEn") or "").strip()
        layers = _parse_outfit_layers(payload.get("outfitLayersEn"))
        analysis = {
            "nameGuess": str(payload.get("nameGuess") or "").strip(),
            "descriptionKo": str(payload.get("descriptionKo") or "").strip(),
            "appearanceEn": str(payload.get("appearanceEn") or "").strip(),
            "styleEn": str(payload.get("styleEn") or "").strip(),
            "baseOutfitEn": base_outfit,
            # 호환: outfitEn 은 baseOutfitEn 과 같게 둔다(기존 프롬프트·화면이 outfitEn 을 읽음).
            "outfitEn": base_outfit,
            "outfitLayersEn": layers,
            "altOutfitsEn": [str(x).strip() for x in alt if str(x).strip()] if isinstance(alt, list) else [],
            "expressionsKo": [str(x).strip() for x in expressions if str(x).strip()]
            if isinstance(expressions, list)
            else [],
            "extraEn": str(payload.get("extraEn") or "").strip(),
            "model": str(usage.get("model") or model),
        }
        if fidelity:
            analysis = _with_fidelity(payload, analysis, file_ids)
        return _with_gender_en(payload, analysis)
    raise VpocError("vnew.analyze", "AnalyzeFailed", "캐릭터 분석에 실패했습니다.", 502)


def _with_fidelity(payload: dict, analysis: dict, file_ids: list[str]) -> dict:
    """VNEW_CHAR_FIDELITY 켬일 때 분석 결과에 충실 필드를 더한다(design §4.5). 끔이면 부르지 않는다.

    - fidelity 1(충실 분석 표시), baseSourceIndex(틀리면 None)·baseSourceFileId("")·baseWhereEn(한 줄 60자).
    - sidedItems: 모델이 화면 기준으로 적은 소품 좌우를 코드가 캐릭터 좌우로 정한 것(char_fidelity.resolve_sided_items).
    - baseOutfitEn = outfitEn = 원문의 좌우 표현을 지우고 계산한 좌우 꼬리를 붙인 글. 원문은 baseOutfitRawEn.
    - outfitLayersEn: 다른 그림의 옷차림 한 벌씩(sourceIndex·sourceFileId·whereEn·sided 포함).
    로그에는 번호·개수만 남긴다(모델 글 없음).
    """
    n_images = len(file_ids)
    index = char_fidelity.pick_index(payload.get("baseSourceIndex"), n_images)
    raw_items = payload.get("sidedItemsEn")
    items = char_fidelity.resolve_sided_items(raw_items, n_images)
    raw_outfit = analysis.get("baseOutfitEn") or ""
    outfit = char_fidelity.apply_sides(raw_outfit, items)
    layers = char_fidelity.photo_layers(payload.get("outfitLayersEn"), file_ids, items, limit=settings.MAX_OUTFITS)
    analysis.update({
        "baseOutfitEn": outfit,
        "outfitEn": outfit,
        "outfitLayersEn": layers,
        "fidelity": 1,
        "baseSourceIndex": index,
        "baseSourceFileId": file_ids[index] if index is not None else "",
        "baseWhereEn": char_fidelity.clean_line(payload.get("baseWhereEn"), 60),
        "sidedItems": items,
        "baseOutfitRawEn": raw_outfit,
    })
    dropped = max(0, (len(raw_items) if isinstance(raw_items, list) else 0) - len(items))
    log.info(
        "vnew.char.analysis_fidelity base=%s sided=%d dropped=%d layers=%d",
        "none" if index is None else index, len(items), dropped, len(layers),
    )
    return analysis


def _parse_outfit_layers(raw) -> list[dict]:
    """분석이 준 outfitLayersEn 을 [{labelKo, promptEn}] 로 정규화한다.

    각 항목이 문자열이면 promptEn 으로 보고 labelKo 는 비운다(옛/느슨한 응답 호환).
    promptEn 이 비면 버린다.
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            prompt_en = item.strip()
            label_ko = ""
        elif isinstance(item, dict):
            prompt_en = str(item.get("promptEn") or "").strip()
            label_ko = str(item.get("labelKo") or "").strip()
        else:
            continue
        if not prompt_en:
            continue
        out.append({"labelKo": label_ko or prompt_en, "promptEn": prompt_en})
    return out


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
            # 120 은 5.x 모델(toolChoice auto)이 도구 앞에 글을 쓰면 잘렸다(llm-top-model-first R3, 2026-10-28).
            max_tokens=400,
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


# ---- 복장 일관성 검사(시트가 기본 복장과 맞는지) ----
#
# 029 의 사람 수 검사와 같은 방식(vpoc.llm._converse + 도구 1개). 시트와 baseOutfitEn 을 주고
# 모든 면이 같은 기본 복장을 입었는지 {consistent, problems} 로 받는다.

_OUTFIT_TOOL = {
    "name": "emit_outfit_consistency",
    "description": "Judge whether every view in a turnaround sheet wears the same base outfit.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "consistent": {"type": "boolean"},
                "problems": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["consistent"],
        }
    },
}

_OUTFIT_SYSTEM = (
    "You check a character turnaround reference sheet. The character must wear ONE single base "
    "outfit in EVERY view (front, side, back). Compare what you see against the required base "
    "outfit given by the user. Set consistent=false if any view adds or swaps a garment (an extra "
    "jacket, coat, cardigan or hoodie, a different colour, a different top or bottom) so the views "
    "do not all match the base outfit; otherwise consistent=true. In problems, list each mismatch "
    "as a short English phrase (e.g. 'back view adds a black jacket'). "
    "Answer only by calling the emit_outfit_consistency tool exactly once."
)


def _check_outfit_consistency(sheet: bytes, base_outfit: str) -> dict | None:
    """시트가 baseOutfitEn 과 맞는지 Claude 분석 체인에 물어본다.

    반환: {"consistent": bool, "problems": [..]}. 모델 오류면 None(검사 건너뜀).
    base_outfit 이 비어 있으면 검사하지 않는다(None).
    """
    base = (base_outfit or "").strip()
    if not base:
        return None
    blocks = [
        {
            "text": (
                "The required base outfit is: "
                + base
                + " Does every view in this turnaround sheet wear exactly this base outfit "
                "and nothing else (no extra jacket/coat/layer, no swapped garment)?"
            )
        },
        {"image": {"format": "jpeg", "source": {"bytes": sheet}}},
    ]
    try:
        payload, _usage = vpoc_llm._converse(  # noqa: SLF001
            system_text=_OUTFIT_SYSTEM,
            blocks=blocks,
            tool=_OUTFIT_TOOL,
            max_tokens=300,
            where="vnew.outfit_check",
            model=settings.OUTFIT_CHECK_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.outfit_check.failed err=%s", str(exc)[:200])
        return None
    problems = payload.get("problems")
    return {
        "consistent": bool(payload.get("consistent")),
        "problems": [str(x).strip() for x in problems if str(x).strip()]
        if isinstance(problems, list)
        else [],
    }


# ---- char-fidelity 그림 검사(design §7.1·§7.3). VNEW_CHAR_FIDELITY·VNEW_3D_SIDE_VIEWS 켬일 때만 부른다. ----
#
# 그림 1장 = LLM 1회. 검사 모델은 화면에 보이는 것(사람 수·방향·글자·소품이 보이는 쪽)만 적고, 맞는지·반전·고칠 문장은
# char_fidelity 순수 함수가 정한다. 모델 오류·도구 미사용이면 None(정면·후면은 그대로 쓰고, 옆모습은 3D 에서 뺀다).

_VIEW_CHECK_MAX_TOKENS = 500
_SHEET_CHECK_MAX_TOKENS = 600

_VIEW_SYSTEM_HEAD = (
    "You check ONE generated character image. people is the number of distinct people drawn. facing is where the person "
    "faces: 'front' (toward the viewer), 'back' (we see the back of the head and body), 'left' (side profile with the face "
    "pointing to the LEFT edge of the picture), 'right' (side profile with the face pointing to the RIGHT edge of the "
    "picture) or 'other'. lettering is true when the clothes or accessories show readable letters or numbers."
)
_VIEW_ITEMS_FLAT = (
    " For each listed item give seen: 'left' when it appears on the viewer's left of the person's body midline, 'right' "
    "when on the viewer's right, 'both' when it appears on both sides, 'none' when you cannot see it. Report only what you "
    "see on the picture; do not think about the person's own left or right."
)
_VIEW_ITEMS_SIDE = (
    " For each listed item give seen: 'near' when it is on the arm or side of the body closest to the viewer, 'far' when it "
    "is visible only on the far side behind the body, 'both' when it appears on both sides, 'none' when you cannot see it."
)
_VIEW_SYSTEM_TAIL = " Answer only by calling the emit_view_check tool exactly once."


def _view_tool(view: str) -> dict:
    """그림 검사 도구(design §7.1). 보기마다 seen 열거값만 다르다(정면·후면 left·right, 옆모습 near·far)."""
    return {
        "name": "emit_view_check",
        "description": "Check one character image: how many people, which way the person faces, and where listed "
        "one-sided items appear.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "people": {"type": "integer"},
                    "facing": {"type": "string", "enum": list(char_fidelity.VIEW_FACINGS)},
                    "lettering": {"type": "boolean"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "seen": {"type": "string", "enum": list(char_fidelity.seen_values(view))},
                            },
                            "required": ["index", "seen"],
                        },
                    },
                },
                "required": ["people", "facing", "items"],
            }
        },
    }


def _view_system(view: str) -> str:
    rule = _VIEW_ITEMS_SIDE if view in char_fidelity.SIDES else _VIEW_ITEMS_FLAT
    return _VIEW_SYSTEM_HEAD + rule + _VIEW_SYSTEM_TAIL


def _inspect_view(jpeg: bytes, view: str, items: list[dict]) -> dict | None:
    """그림 1장 검사(LLM 1회, design §7.1). 반환 char_fidelity.normalize_view_obs 모양, 모델 오류면 None.

    정면·후면은 정한 소품이 있을 때만 사람 수 검사(_count_people)를 대신하고, 옆모습은 소품이 없어도 부른다(사람 수·방향).
    로그에는 오류 앞 200자만 남긴다(소품 글·프롬프트 없음).
    """
    if items:
        listed = "; ".join(f"{i}) {item['itemEn']} ({char_fidelity.PARTS[item['partEn']][0]})" for i, item in enumerate(items))
        text = "Items to check: " + listed
    else:
        text = "No items to check; return an empty items list."
    blocks = [{"text": text}, {"image": {"format": "jpeg", "source": {"bytes": jpeg}}}]
    try:
        payload, _usage = vpoc_llm._converse(  # noqa: SLF001
            system_text=_view_system(view),
            blocks=blocks,
            tool=_view_tool(view),
            max_tokens=_VIEW_CHECK_MAX_TOKENS,
            where="vnew.view_check",
            model=settings.PEOPLE_COUNT_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.view_check.failed err=%s", str(exc)[:200])
        return None
    obs = char_fidelity.normalize_view_obs(payload, view, len(items))
    if obs is None:
        log.warning("vnew.view_check.failed err=bad_payload")
    return obs


_SHEET_TOOL = {
    "name": "emit_sheet_check",
    "description": "Check a character turnaround sheet: the view of each figure from left to right and whether every figure "
    "wears the same base outfit.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "figures": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "view": {"type": "string", "enum": list(char_fidelity.SHEET_VIEW_VALUES)},
                            "faceSide": {"type": "string", "enum": list(char_fidelity.SHEET_FACE_SIDES)},
                        },
                        "required": ["view", "faceSide"],
                    },
                },
                "consistent": {"type": "boolean"},
                "problems": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["figures", "consistent"],
        }
    },
}

# 시트 검사 SYSTEM(design §7.3 원문). 복장 부분은 _OUTFIT_SYSTEM 문장을 옮기고 소품 좌우를 보지 말라는 문장을 더했다.
_SHEET_SYSTEM = (
    "You check a character turnaround reference sheet. List every full-body figure from left to right in figures. view is "
    "'front' (faces the viewer), 'three-quarter front' (turned about 45°, the face still mostly visible), 'side' (exact side "
    "profile), 'three-quarter back' (turned about 135°, mostly the back, only a cheek or ear outline of the face), 'back' "
    "(seen from directly behind) or 'other'. faceSide is the edge of the PICTURE the face or the front of the body points "
    "toward as you look at it: 'left', 'right', 'center' when it faces the viewer, 'hidden' when it faces away. The "
    "character must also wear ONE single base outfit in EVERY view (front, side, back). Compare what you see against the "
    "required base outfit given by the user. Set consistent=false if any view adds or swaps a garment (an extra jacket, "
    "coat, cardigan or hoodie, a different colour, a different top or bottom) so the views do not all match the base "
    "outfit; otherwise consistent=true. Do not judge which side small accessories are on. In problems, list each mismatch "
    "as a short English phrase (e.g. 'back view adds a black jacket'). Answer only by calling the emit_sheet_check tool "
    "exactly once."
)


def _inspect_sheet(sheet: bytes, base_outfit: str) -> dict | None:
    """시트 검사(회전 순서 + 복장 일관성, LLM 1회, design §7.3). 지금 복장 검사(_check_outfit_consistency)를 대신한다.

    기본 복장 글이 비면 회전만 본다(consistent 는 늘 참). 모델 오류면 None(다시 그리지 않고 그 시트를 쓴다).
    """
    base = (base_outfit or "").strip()
    if base:
        text = (
            "The required base outfit is: "
            + base
            + " Does every view in this turnaround sheet wear exactly this base outfit "
            "and nothing else (no extra jacket/coat/layer, no swapped garment)?"
            + " Also list every figure from left to right."
        )
    else:
        text = "No base outfit is given: set consistent=true. List every figure from left to right."
    blocks = [{"text": text}, {"image": {"format": "jpeg", "source": {"bytes": sheet}}}]
    try:
        payload, _usage = vpoc_llm._converse(  # noqa: SLF001
            system_text=_SHEET_SYSTEM,
            blocks=blocks,
            tool=_SHEET_TOOL,
            max_tokens=_SHEET_CHECK_MAX_TOKENS,
            where="vnew.sheet_check",
            model=settings.OUTFIT_CHECK_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.sheet_check.failed err=%s", str(exc)[:200])
        return None
    obs = char_fidelity.normalize_sheet_obs(payload)
    if obs is None:
        log.warning("vnew.sheet_check.failed err=bad_payload")
        return None
    if not base:
        obs["consistent"] = True
    return obs


# ================================================================ 프롬프트(b)


def _base_outfit(analysis: dict) -> str:
    """기본 복장(겹옷을 뺀 한 벌). baseOutfitEn 을 쓰고, 없으면 outfitEn(옛 데이터)로 대신한다."""
    return (analysis.get("baseOutfitEn") or analysis.get("outfitEn") or "").strip()


def _who(analysis: dict) -> str:
    appearance = analysis.get("appearanceEn") or ""
    outfit = _base_outfit(analysis)
    return f"The character: {appearance} Wearing: {outfit}".strip()


def _one_person_outfit(analysis: dict) -> str:
    """모든 뷰에 '한 사람만 · 기본 복장만(겹옷 금지)' 을 강제하는 문장.

    시트·정면·후면이 전부 똑같은 기본 복장을 입게 해, 그 아래서 만드는 3D 가 면마다 다른 옷으로
    흔들리지 않게 한다. 추가 겹옷(자켓·코트 등)은 '추가 복장' 이미지에서만 입힌다.
    """
    outfit = _base_outfit(analysis) or "the outfit described above"
    return (
        "Draw exactly ONE person — only ONE single person in the image, no extra figures, "
        "no duplicates, no background characters. "
        f"Every view wears exactly this outfit and nothing else: {outfit}. "
        "No jacket, coat or extra layer unless listed."
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


# ---- char-fidelity 프롬프트(design §6). VNEW_CHAR_FIDELITY 켬일 때만 쓴다(끔이면 아래 지금 함수 본문 그대로). ----

# 시트 회전 문장(design §6.2 원문, 웹툰·실사 같음). 옛 분석에도 넣는다(결정 S4).
_SHEET_ROTATION = (
    "Five full-body views of the same person side by side on a plain white background, turning in place toward the "
    "LEFT of the image in 45° steps, from left to right: 1 FRONT, facing the viewer; 2 THREE-QUARTER FRONT, body and "
    "face turned 45° toward the left of the image; 3 SIDE PROFILE facing the left edge of the image; 4 THREE-QUARTER "
    "BACK, turned 135°: mostly the back shows and the head turns to the upper left so only the cheek and ear outline "
    "are visible; 5 BACK, seen from directly behind. All five figures the same size, feet on one ground line, evenly "
    "spaced, the same relaxed standing pose."
)
# 켬일 때 _one_person_outfit 대신 쓰는 문장(옷 글 반복을 뺐다 — 길이 예산, design §6.1 ONE_F).
_ONE_PERSON_F = (
    "Draw exactly ONE person — only ONE single person in the image, no extra figures, no duplicates, no background "
    "characters. Every view wears exactly the outfit described above and nothing else. No jacket, coat or extra layer "
    "unless listed."
)
_REAL_STYLE = "Real photograph, natural skin texture, soft studio lighting."
_SIDED_TAIL_MARK = "Worn on one side only:"


def _is_fidelity_analysis(analysis: dict) -> bool:
    """충실 분석(analysis.fidelity == 1, bool 아님)인가. R1·R2 프롬프트 조각은 충실 분석일 때만 넣는다(결정 S4)."""
    value = analysis.get("fidelity") if isinstance(analysis, dict) else None
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _valid_sided_item(item) -> bool:
    """저장된 한쪽 소품 한 항목의 모양 검사(itemEn 글·partEn·side 열거값)."""
    return (
        isinstance(item, dict)
        and isinstance(item.get("itemEn"), str)
        and bool(item["itemEn"].strip())
        and item.get("partEn") in char_fidelity.PARTS
        and item.get("side") in char_fidelity.SIDES
    )


def _sided_items(analysis: dict) -> list[dict]:
    """켬 + 충실 분석이면 analysis.sidedItems 중 모양이 맞는 것(최대 MAX_ITEMS), 아니면 []."""
    if not settings.VNEW_CHAR_FIDELITY or not _is_fidelity_analysis(analysis):
        return []
    raw = analysis.get("sidedItems")
    return [item for item in raw if _valid_sided_item(item)][: char_fidelity.MAX_ITEMS] if isinstance(raw, list) else []


def _pronoun_of(analysis: dict) -> str:
    """좌우 문장 대명사: casting.auto_gender(분석 genderEn → 외모 문장 주어) → his·her·their."""
    from . import casting  # noqa: PLC0415 — casting 이 characters 를 읽으므로 늦게 import

    return char_fidelity.pronoun(casting.auto_gender(analysis))


def _figure_ref(where) -> str:
    """'the character' 또는 'the {whereEn}'(모델 글은 한 줄 60자, 앞 관사 제거)."""
    where = re.sub(r"^(?:the|a|an)\s+", "", char_fidelity.clean_line(where, 60), flags=re.IGNORECASE)
    return f"the {where}" if where else "the character"


def _source_clause(analysis: dict, source_no: int | None) -> str:
    """옷 그대로 문장(design §6.1 SOURCE). 충실 분석이고 기준 사진이 참고 맨 앞일 때(source_no 있음)만."""
    if source_no is None or not _is_fidelity_analysis(analysis):
        return ""
    return (
        f"Copy the clothes exactly as drawn on {_figure_ref(analysis.get('baseWhereEn'))} in Image {source_no}: the same "
        "garments and layers, colours, fit, how each piece is worn, rips or patterns and where they are, shoes and "
        "accessories."
    )


def _join(*pieces: str) -> str:
    """빈 조각은 빼고 한 칸으로 잇는다(design §6.2)."""
    return " ".join(p.strip() for p in pieces if p and p.strip())


def _clip_outfit_body(outfit: str, limit: int) -> str:
    """기본 복장 본문만 limit 자로 줄이고 'Worn on one side only:' 꼬리는 그대로 다시 붙인다(design §6.6 단계 3)."""
    at = outfit.find(_SIDED_TAIL_MARK)
    body, tail = (outfit[:at], outfit[at:]) if at >= 0 else (outfit, "")
    body = char_fidelity.clip_words(body.strip().rstrip(".").rstrip(), limit)
    return f"{body}. {tail}" if (tail and body) else (tail or body)


def _budget_prompt(build, *, style: str, appearance: str, outfit: str, kind: str, variant: str) -> str:
    """켬 프롬프트를 _PROMPT_MAX(2,900자) 안으로 맞춘다(design §6.6, 결정 S8).

    build(style, appearance, outfit) → 프롬프트. 넘으면 styleEn ≤200 → appearanceEn ≤200 → 기본 복장 본문 ≤450 순서로
    줄인다. 좌우·고칠·회전 문장·추가 요청은 줄이지 않는다. 그래도 넘으면 전체를 단어 경계로 자르고 경고 로그(길이만).
    """
    clip = char_fidelity.clip_words
    steps = (
        (style, appearance, outfit),
        (clip(style, 200), appearance, outfit),
        (clip(style, 200), clip(appearance, 200), outfit),
        (clip(style, 200), clip(appearance, 200), _clip_outfit_body(outfit, 450)),
    )
    prompt = ""
    for step_style, step_appearance, step_outfit in steps:
        prompt = build(step_style, step_appearance, step_outfit)
        if len(prompt) <= _PROMPT_MAX:
            return prompt
    log.warning("vnew.char_prompt.clipped kind=%s variant=%s len=%d", kind, variant, len(prompt))
    return clip(prompt, _PROMPT_MAX)


def _fidelity_prompt(
    kind: str, variant: str, analysis: dict, *, source_no: int | None = None, fix: str = ""
) -> str:
    """켬 시트·정면·후면 프롬프트(design §6.2·§6.3 원문). kind = sheet | front | back.

    source_no: 기준 사진의 참고 번호(시트 1·정면 2·후면 3). None 이면 옷 그대로 문장(SOURCE)을 넣지 않는다.
    fix: 다시 그릴 때 붙이는 고칠 문장(처음은 ""). 좌우 문장은 충실 분석에 정한 소품이 있을 때만 넣는다.
    """
    real = variant == "real"
    items = _sided_items(analysis)
    sided = char_fidelity.side_sentences(items, kind, _pronoun_of(analysis)) if items else ""
    source = _source_clause(analysis, source_no)
    extra = (analysis.get("extraEn") or "").strip()

    def build(style: str, appearance: str, outfit: str) -> str:
        who = _who({"appearanceEn": appearance, "baseOutfitEn": outfit})
        style_text = _REAL_STYLE if real else _style_clause({"styleEn": style})
        if kind == "sheet":
            head = (
                "Photorealistic live-action character turnaround reference sheet of a real Korean actor playing the "
                "character from the reference images."
                if real
                else "Character turnaround reference sheet."
            )
            middle = _SHEET_ROTATION + " Same person, same outfit in every view."
            end = "No text, no labels." if real else "No text, no speech bubbles, no labels."
        elif kind == "back":
            head = (
                "Single full-body BACK view photograph of the same real Korean actor as in the reference images, seen "
                "from directly behind."
                if real
                else "Single full-body BACK view of the same single person as in the reference images, seen from "
                "directly behind."
            )
            middle = (
                "Back view of the same single person, same outfit, standing straight in a relaxed A-pose, whole body "
                "from head to shoes visible, centered, plain white background, no shadow."
            )
            end = "No text."
        else:
            head = (
                "Single full-body front view photograph of a real Korean actor playing the character."
                if real
                else "Single full-body front view."
            )
            middle = (
                "Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes visible, "
                "centered, plain white background, no shadow."
            )
            end = "No text."
        return _join(head, who, middle, _ONE_PERSON_F, source, sided, fix, style_text, end, extra)

    return _budget_prompt(
        build,
        style=(analysis.get("styleEn") or "").strip(),
        appearance=analysis.get("appearanceEn") or "",
        outfit=_base_outfit(analysis),
        kind=kind,
        variant=variant,
    )


def _side_prompt(side: str, variant: str, analysis: dict, *, source_no: int | None = None, fix: str = "") -> str:
    """옆모습 프롬프트(design §6.4 원문). side = left(캐릭터 왼쪽이 카메라, 얼굴 화면 왼쪽) | right.

    참고 [정면, 후면, 시트, 기준 사진] 이라 기준 사진은 Image 4(source_no). VNEW_CHAR_FIDELITY 끔(옆모습만 켬)이면 SOURCE·좌우
    문장을 넣지 않고 ONE_F 대신 지금 _one_person_outfit 을 쓴다. 길이 예산은 늘 적용한다(새 프롬프트).
    """
    real = variant == "real"
    side = "right" if side == "right" else "left"
    big, near, far = ("RIGHT", "right", "left") if side == "right" else ("LEFT", "left", "right")
    fidelity = settings.VNEW_CHAR_FIDELITY
    items = _sided_items(analysis)
    sided = char_fidelity.side_sentences(items, side, _pronoun_of(analysis)) if items else ""
    source = _source_clause(analysis, source_no) if fidelity else ""
    extra = (analysis.get("extraEn") or "").strip()

    def build(style: str, appearance: str, outfit: str) -> str:
        who = _who({"appearanceEn": appearance, "baseOutfitEn": outfit})
        one = _ONE_PERSON_F if fidelity else _one_person_outfit({"baseOutfitEn": outfit})
        what = (
            f"Single full-body {big} SIDE view photograph of the same real Korean actor as in the reference images"
            if real
            else f"Single full-body {big} SIDE view of the same single person as in the reference images"
        )
        head = (
            f"{what}: exact side profile, the person's {big} side faces the camera and the face points to the {big} "
            "edge of the image."
        )
        middle = (
            "Same person, same outfit and the same relaxed A-pose as the front view in Image 1, seen exactly from the "
            f"side: the {near} arm, shoulder and leg are nearest to us and the {far} arm is mostly hidden behind the "
            "body. Whole body from head to shoes visible, centered, plain white background, no shadow."
        )
        style_text = _REAL_STYLE if real else _style_clause({"styleEn": style})
        return _join(head, who, middle, one, source, sided, fix, style_text, "No text.", extra)

    return _budget_prompt(
        build,
        style=(analysis.get("styleEn") or "").strip(),
        appearance=analysis.get("appearanceEn") or "",
        outfit=_base_outfit(analysis),
        kind=f"side-{side}",
        variant=variant,
    )


def _photo_outfit_prompt(
    variant: str, analysis: dict, outfit: dict, view: str, *, source_no: int | None, base_back_no: int | None
) -> str:
    """사진 속 옷(kind photo) 정면·후면 프롬프트(design §6.5 원문). '기본 복장 대신 이 한 벌 그대로'.

    정면 참고 [기본 정면, 원본 그림] → source_no 2. 후면 참고 [방금 정면, 원본 그림, 기본 후면] → source_no 2·base_back_no 3.
    원본 그림이 없으면 source_no None(그 문장을 뺀다), 기본 후면이 없으면 base_back_no None.
    """
    real = variant == "real"
    back = view == "back"
    raw_items = outfit.get("sidedItems") if isinstance(outfit.get("sidedItems"), list) else []
    items = [item for item in raw_items if _valid_sided_item(item)][: char_fidelity.MAX_ITEMS]
    sided = char_fidelity.side_sentences(items, "back" if back else "front", _pronoun_of(analysis)) if items else ""
    figure = _figure_ref(outfit.get("whereEn"))

    def build(style: str, appearance: str, prompt_en: str) -> str:
        wear = prompt_en.strip().rstrip(".").rstrip() or "the outfit described"
        style_text = _REAL_STYLE if real else _style_clause({"styleEn": style})
        end = "No text."
        if back:
            what = (
                "Single full-body BACK view photograph of the same real Korean actor as in Image 1"
                if real
                else "Single full-body BACK view of the same single person as in Image 1"
            )
            head = f"{what}, seen from directly behind, wearing the same outfit as in Image 1: {wear}."
            drawn = f"The outfit is drawn on {figure} in Image {source_no}." if source_no is not None else ""
            base_back = (
                f"Image {base_back_no} shows the same person from behind in other clothes; use it only for the hair "
                "and body, not for the clothes."
                if base_back_no is not None
                else ""
            )
            pose = (
                "Standing straight in a relaxed A-pose, whole body from head to shoes visible, centered, plain white "
                "background, no shadow."
            )
            return _join(head, drawn, base_back, sided, pose, "Draw exactly ONE person, no extra figures.", style_text, end)
        head = (
            "Single full-body front view photograph of the same real Korean actor as in Image 1."
            if real
            else "Single full-body front view of the same single person as in Image 1."
        )
        if source_no is not None:
            wears = (
                f"Instead of the base outfit, this person wears this whole outfit exactly as drawn on {figure} in Image "
                f"{source_no}: {wear}. Copy the garments and layers, colours, fit, how each piece is worn, rips or "
                "patterns and where they are, shoes and accessories."
            )
        else:
            wears = f"Instead of the base outfit, this person wears this whole outfit: {wear}."
        pose = (
            "Standing straight in a relaxed A-pose facing the camera, whole body from head to shoes visible, centered, "
            "plain white background, no shadow."
        )
        return _join(head, f"The character: {appearance}", wears, sided, pose,
                     "Draw exactly ONE person, no extra figures.", style_text, end)

    return _budget_prompt(
        build,
        style=(analysis.get("styleEn") or "").strip(),
        appearance=analysis.get("appearanceEn") or "",
        outfit=str(outfit.get("promptEn") or ""),
        kind=f"outfit-{view}",
        variant=variant,
    )


def _sheet_prompt(variant: str, analysis: dict) -> str:
    if settings.VNEW_CHAR_FIDELITY:
        return _fidelity_prompt("sheet", variant, analysis)
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
    if settings.VNEW_CHAR_FIDELITY:
        return _fidelity_prompt("front", variant, analysis)
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
    if settings.VNEW_CHAR_FIDELITY:
        return _fidelity_prompt("back", variant, analysis)
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


def _mirror_jpeg(jpeg: bytes) -> bytes:
    """그림을 좌우로 뒤집는다(PIL ImageOps.mirror, JPEG q92). 반전으로 맞는 그림만 쓴다(char-fidelity design §5.7).

    디코드하는 그림은 Seedream 결과(1440x2560 이하)뿐이다. 사용자 업로드는 뒤집지 않는다.
    """
    with Image.open(io.BytesIO(jpeg)) as image:
        mirrored = ImageOps.mirror(image.convert("RGB"))
    buffer = io.BytesIO()
    mirrored.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


# ================================================================ 3D(c) — fal Hunyuan3D v3

# char-fidelity(design §10.3): 옆모습 입력 필드. left = 캐릭터 왼쪽이 카메라를 향한 그림(Hunyuan3D-2 예제 left.png 규칙).
# 개발계 확인(VD-1)에서 좌우가 반대로 나오면 이 한 줄만 바꾼다.
_HUNYUAN_SIDE_FIELDS = {"left": "left_image_url", "right": "right_image_url"}

# ---------------------------------------------------------------- 3D 공급자(char-fidelity T2b, design §19)
#
# 새 제출은 설정 VNEW_3D_PROVIDER(기본 tripo)를 따른다. 이미 낸 요청은 설정이 아니라 requestArn 의 endpoint 로 공급자를 가린다
# (배포 전에 낸 Hunyuan 요청도 Hunyuan 으로 조회·재제출·저장한다). Tripo 결과만 정면을 +Z 로 돌린다(_orient_glb).

_TRIPO_PREFIX = "tripo3d/"


def _3d_provider() -> str:
    """설정의 3D 공급자("tripo" | "hunyuan"). 새 제출만 이 값을 쓴다."""
    return "hunyuan" if settings.VNEW_3D_PROVIDER == "hunyuan" else "tripo"


def _is_tripo_endpoint(endpoint: str) -> bool:
    return str(endpoint or "").startswith(_TRIPO_PREFIX)


def _provider_of(request_arn: str) -> str:
    """requestArn("fal/requests/<endpoint>/<id>")의 endpoint 로 가린 공급자. Tripo 접두사가 아니면 hunyuan(옛 요청)."""
    return "tripo" if _is_tripo_endpoint(_parse_arn(str(request_arn or ""))[0]) else "hunyuan"


def _3d_squares(
    front: bytes, back: bytes, left: bytes | None = None, right: bytes | None = None, *, provider: str
) -> tuple[bytes, bytes, bytes | None, bytes | None]:
    """3D 입력 정사각(흰 배경 패딩) 네 장. 처음 제출·잃은 요청 재제출·retry3d·이어 만들기가 모두 이 함수로 만든다.

    한 변: hunyuan = MODEL_INPUT_SQUARE(T2 그대로), tripo = VNEW_3D_TRIPO_INPUT_SQUARE(기본 2048). 없는 옆모습은 None.
    """
    side = settings.VNEW_3D_TRIPO_INPUT_SQUARE if provider == "tripo" else settings.MODEL_INPUT_SQUARE
    return (
        _square_pad(front, side),
        _square_pad(back, side),
        _square_pad(left, side) if left is not None else None,
        _square_pad(right, side) if right is not None else None,
    )


def _tripo_request(
    front_square: bytes, back_square: bytes | None, left_square: bytes | None = None, right_square: bytes | None = None
) -> tuple[str, dict]:
    """Tripo H3.1 제출 (endpoint, 본문). 설정만 읽는 순수 함수다.

    - 다시점: VNEW_3D_TRIPO_MULTIVIEW 켬이고 왼쪽 자리에 그림이 있으면 VNEW_3D_TRIPO_ENDPOINT,
      image_urls = [front, left, back] + (오른쪽이 있으면 right). fal·Tripo 순서 [front, left, back, right], left = 인물 자신의 왼쪽 옆면.
    - VNEW_3D_SIDE_SWAP 이면 왼쪽·오른쪽 자리를 바꿔 넣는다. 바꾼 뒤 왼쪽 자리가 비면 단일이다.
    - 단일(그 밖): VNEW_3D_TRIPO_SINGLE_ENDPOINT, image_url = front.
    - 빈 바이트·None 자리는 목록에 넣지 않는다(fal 이 빈 자리를 "Failed to download the file" 로 거절, Claude 실측).
    - 그림은 모두 data URI 다(우리 그림을 외부 저장소에 올리지 않고 presigned URL 도 넣지 않는다). enable_safety_checker 는 보내지 않는다.
    """
    slot_left, slot_right = (right_square, left_square) if settings.VNEW_3D_SIDE_SWAP else (left_square, right_square)
    common = {
        "texture": True,
        "pbr": bool(settings.HUNYUAN_ENABLE_PBR),
        "texture_quality": settings.VNEW_3D_TRIPO_TEXTURE_QUALITY,
        "geometry_quality": settings.VNEW_3D_TRIPO_GEOMETRY_QUALITY,
        "orientation": "align_image",
        "face_limit": settings.VNEW_3D_TRIPO_FACE_LIMIT,
    }
    if settings.VNEW_3D_TRIPO_MULTIVIEW and slot_left and back_square:
        views = [front_square, slot_left, back_square] + ([slot_right] if slot_right else [])
        return settings.VNEW_3D_TRIPO_ENDPOINT, {"image_urls": [_data_uri(view) for view in views], **common}
    return settings.VNEW_3D_TRIPO_SINGLE_ENDPOINT, {"image_url": _data_uri(front_square), **common}


def _post_3d(eid: str, body: dict) -> str:
    """fal 큐에 3D 작업을 내고 requestArn(실제로 쓴 endpoint 로 만든다)을 돌려준다."""
    result = _fal_request("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{eid}", body)
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.submit_3d", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:200]}", 502)
    return f"{ARN_PREFIX}{eid}/{request_id}"


def submit_3d(
    front_square: bytes,
    back_square: bytes,
    *,
    left_square: bytes | None = None,
    right_square: bytes | None = None,
    provider: str | None = None,
) -> str:
    """3D 작업(Tripo H3.1 또는 Hunyuan3D v3)을 제출하고 requestArn 을 돌려준다(data URI 입력).

    Hunyuan: face_count·enable_pbr 은 설정값을 쓴다(기본 60000·PBR off → GLB 약 10MB, 실측 2026-10-20).
    left_square·right_square(char-fidelity R4.5): 있을 때만 옆모습 data URI 를 더한다. 없으면 지금 본문 그대로다.
    provider(char-fidelity T2b R8): "tripo" 면 Tripo H3.1 본문(_tripo_request)으로 내고, "hunyuan" 이면 아래 T2 본문 그대로다.
    None 이면 설정(_3d_provider). 잃은 요청 재제출은 처음 제출과 같은 provider 를 넘긴다.
    """
    if (provider or _3d_provider()) == "tripo":
        return _post_3d(*_tripo_request(front_square, back_square, left_square, right_square))
    eid = settings.HUNYUAN_ENDPOINT
    body = {
        "input_image_url": _data_uri(front_square),
        "back_image_url": _data_uri(back_square),
        "enable_pbr": bool(settings.HUNYUAN_ENABLE_PBR),
        "face_count": settings.HUNYUAN_FACE_COUNT,
    }
    if left_square is not None:
        body[_HUNYUAN_SIDE_FIELDS["left"]] = _data_uri(left_square)
    if right_square is not None:
        body[_HUNYUAN_SIDE_FIELDS["right"]] = _data_uri(right_square)
    return _post_3d(eid, body)


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
        # Tripo 결과(char-fidelity T2b): thumbnail 이 없고 rendered_image{url(.png)} 가 있다(Claude 실측 2026-10-30).
        rendered = result.get("rendered_image")
        if not thumb_url and isinstance(rendered, dict):
            thumb_url = str(rendered.get("url") or "")
    if not thumb_url:
        thumb_url = _find_url(result, ".png")
    if not glb_url:
        return {"status": "Failed", "error": "완료됐지만 GLB URL 이 없습니다."}
    return {"status": "Completed", "glbUrl": glb_url, "thumbUrl": thumb_url}


def _wait_3d(request_arn: str, resubmit=None, *, owner: str = "", char_id: str = "", variant: str = "") -> dict:
    """완료까지 폴링(5초 간격, 최대 15분).

    조회 중 fal 일시 오류(5xx·429·연결·시간초과·'unavailable')는 실패로 보지 않고 다시 조회한다
    (연속 최대 POLL_TRANSIENT_MAX 회, 사이 POLL_TRANSIENT_WAIT_S 초). 그 밖의 오류나 fal 의
    FAILED 만 실패로 돌려준다.

    owner/char_id/variant 가 주어지면 조회 루프 한 바퀴마다 heartbeatAt 을 갱신한다 — 긴 3D
    폴링 동안 자동 재개가 '끊긴 작업'으로 오인하지 않게 한다.

    "잃은 요청" 재제출: resubmit 콜백이 주어지고, 같은 요청에서 unavailable 류 일시 오류가 연속
    LOST_AFTER 회 쌓이면 그 요청은 fal 이 잃은 것으로 보고 resubmit() 을 불러 같은 front/back 으로
    새로 제출한다. resubmit() 은 새 requestArn(문자열)을 돌려주며, 그때부터 그 arn 으로 계속
    조회한다(일시 오류 카운트 초기화). 재제출 한도를 넘어 resubmit() 이 None 을 돌려주면 실패한다.
    """
    deadline = time.time() + settings.POLL_MAX_S
    transient = 0
    lost = 0  # 같은 요청에서 연속으로 쌓인 unavailable 류(잃은 요청 신호) 횟수

    def _beat() -> None:
        if owner and char_id and variant:
            _touch_heartbeat(owner, char_id, variant)

    while True:
        _beat()
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


def _charge_draws(owner: str, char_id: str, variant: str, kind: str, draws: int) -> None:
    """그림 draws 장의 원장(char-fidelity, Claude 결정 1). 첫 장은 kind, 다시 그린 장은 '{kind}-retry'.

    draws 는 _gen_view_checked·_gen_sheet_fidelity 가 돌려준 Seedream 호출 수다. 끔 경로·_gen_checked 는 draws=1(지금 원장 그대로).
    """
    _charge_seedream(owner, char_id, variant, kind)
    for _ in range(max(0, int(draws) - 1)):
        _charge_seedream(owner, char_id, variant, f"{kind}-retry")


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


def _charge_tripo_3d(owner: str, char_id: str, variant: str, endpoint: str) -> None:
    """Tripo 3D 1건 원장(char-fidelity T2b 결정 7): 단가 VNEW_3D_TRIPO_USD, model 은 실제로 쓴 endpoint."""
    key = f"vnew-{char_id}-{variant}-3d-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="model3d",
            model=endpoint,
            units=1,
            usd=settings.VNEW_3D_TRIPO_USD,
            user=owner,
            project_id=char_id,
            plan_id=variant,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.ledger.3d_failed char=%s err=%s", char_id, str(exc)[:200])


def _charge_3d_request(owner: str, char_id: str, variant: str, request_arn: str) -> None:
    """방금 낸 3D 요청 1건의 원장. Tripo 요청이면 _charge_tripo_3d, 아니면 지금 _charge_3d 그대로(Hunyuan 단가·model)."""
    endpoint = _parse_arn(str(request_arn or ""))[0]
    if _is_tripo_endpoint(endpoint):
        _charge_tripo_3d(owner, char_id, variant, endpoint)
    else:
        _charge_3d(owner, char_id, variant)


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


def _note_3d_endpoint(m3d: dict, request_arn: str) -> None:
    """model3d.endpoint(char-fidelity T2b 결정 8): Tripo 요청이면 실제로 쓴 endpoint 를 쓰고, 아니면 남은 값을 지운다.

    Hunyuan 요청은 키를 두지 않는다(옛 데이터·T2 저장과 같다). 공급자를 되돌린 뒤 새 Hunyuan 요청에 옛 Tripo 이름이 남지 않게 한다.
    """
    endpoint = _parse_arn(str(request_arn or ""))[0]
    if _is_tripo_endpoint(endpoint):
        m3d["endpoint"] = endpoint
    else:
        m3d.pop("endpoint", None)


def _set_variant_3d_request(owner: str, char_id: str, variant: str, patch: dict) -> None:
    """_set_variant_3d(patch) + _note_3d_endpoint(patch.requestArn) 를 쓰기 한 번에 한다(새 3D 요청을 저장할 때)."""

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        m3d = v.setdefault("model3d", {})
        m3d.update(patch)
        _note_3d_endpoint(m3d, str(patch.get("requestArn") or ""))

    mutate_char(owner, char_id, mutator)


def _touch_heartbeat(owner: str, char_id: str, variant: str) -> None:
    """단계 진입·진행 때 variant 의 heartbeatAt 을 지금으로 갱신한다(끊김 판단 기준).

    같은 (owner,char_id) 에 등록된 대기 variant(_KEEPALIVE)의 claim·heartbeat 도 함께 찍는다 —
    순차 빌드에서 앞 variant 가 오래 걸려도 대기 variant 가 자동 재개로 중복 실행되지 않게 한다.
    등록된 token 이 더는 그 variant 의 claim 이 아니면(이미 시작·해제됨) 건드리지 않는다.
    """
    try:
        pending = _keepalive_pending(owner, char_id)

        def mutator(data: dict) -> None:
            now = now_iso()
            v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
            v["heartbeatAt"] = now
            for pv, ptoken in pending.items():
                if pv == variant:
                    continue
                pvv = data.setdefault("variants", {}).setdefault(pv, _empty_variant())
                if (pvv.get("claim") or "") == ptoken:
                    pvv["heartbeatAt"] = now
                    pvv["claimedAt"] = now

        mutate_char(owner, char_id, mutator)
    except Exception as exc:  # noqa: BLE001 — 하트비트 실패가 빌드를 막지 않게
        log.warning("vnew.heartbeat_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:200])


def _claim_variant(owner: str, char_id: str, variant: str) -> str:
    """variant 를 이 파드가 선점한다(016-fix2 방식). 성공하면 token, 못 잡으면 ""(빈 문자열).

    선점 조건: claim 이 비어 있거나(놓임), 더는 살아 있지 않을 때(_claim_fresh 가 False)만 새
    token 으로 조건부 쓰기. 살아 있음 판단은 _claim_fresh 와 같다(claimedAt 10분 이내 또는
    heartbeat 10분 이내) — 긴 실행이 heartbeat 만 갱신해도 claim 을 뺏기지 않게 통일한다.
    경합(PreconditionFailed)이면 다른 파드가 먼저 잡은 것이라 포기한다.
    """
    token = secrets.token_hex(6)
    got = {"ok": False}

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        if _claim_fresh(v):
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


def _guard_submit_claim(owner: str, char_id: str, variant: str, token: str) -> None:
    """외부 유료 제출(submit_3d·seedream) '직전에' 최신 상태를 다시 읽어 제출을 접을지 본다.

    두 가지를 막는다(둘 다 같은 variant 이중 제출·이중 과금 원인):
    1) claim 도둑맞음: token 이 있는데 더는 이 variant 의 claim 이 아니면(다른 실행이 가져감) 접는다.
    2) 이미 끝남: 다른 실행이 방금 3D 를 끝내(model3d.status=ready) 결과가 있는데, 막 선점을
       놓은 틈(status 가 잠깐 model3d·claim 비어 있음)에 자동 재개가 끼어든 경우 — 이미 ready 면
       또 제출하지 않는다.

    어느 쪽이든 ClaimLost 로 올려, 호출한 실행을 제출 없이 조용히 접는다. token 이 비어 있어도
    '이미 ready' 검사는 한다(resume→retry3d 처럼 claim 수명을 다른 쪽이 쥔 경우도 보호).
    """
    try:
        char, _ = load_char(owner, char_id)
    except VpocError:
        return
    v = (char.get("variants") or {}).get(variant) or {}
    # 2) 이미 끝난 3D 면 제출하지 않는다(ready 결과가 있는데 재제출하면 이중 과금).
    if (v.get("model3d") or {}).get("status") == "ready" or v.get("status") == "ready":
        raise VpocError("vnew.claim", "ClaimLost", "이미 완료되어 제출을 건너뜁니다(중복 방지).", 409)
    # 1) claim 도둑맞음.
    if token and (v.get("claim") or "") != token:
        raise VpocError("vnew.claim", "ClaimLost", "다른 실행이 이 그림체를 가져갔습니다(중복 방지).", 409)


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


def _claim_variant_dict(v: dict, token: str) -> None:
    """이미 열려 있는 mutate 안에서 variant dict 에 선점(claim)·heartbeat 를 지금으로 찍는다.

    start_* 가 '시작 전에' 선점·heartbeat 를 같은 조건부 쓰기로 잡게 해, 조회 자동 재개가 그
    사이에 끼어들어 중복 실행하지 못하게 한다(이중 과금 방지). token 은 바깥에서 만든다.
    """
    now = now_iso()
    v["claim"] = token
    v["claimedAt"] = now
    v["heartbeatAt"] = now


def _claim_fresh(v: dict) -> bool:
    """이 variant 의 선점(claim)이 아직 살아 있는가.

    claim 이 있고, claimedAt 이 CLAIM_STALL_S(10분) 이내이거나 heartbeatAt 이 VARIANT_STALL_S
    (10분) 이내면 살아 있다고 본다. 살아 있으면 다른 실행/자동 재개가 끼어들지 않는다.
    """
    if not (v.get("claim") or ""):
        return False
    now = datetime.now(timezone.utc)
    claimed_at = _parse_iso(v.get("claimedAt") or "")
    if claimed_at and (now - claimed_at).total_seconds() <= settings.CLAIM_STALL_S:
        return True
    beat = _parse_iso(v.get("heartbeatAt") or "")
    if beat and (now - beat).total_seconds() <= settings.VARIANT_STALL_S:
        return True
    return False


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


def _gen_sheet_checked(
    variant: str, analysis: dict, references: list[bytes], *, size: str, prompt: str | None = None
) -> tuple[bytes, bool]:
    """시트 1장 생성 후 복장 일관성을 검사. 면마다 옷이 다르면 problems 를 고칠 지시를 붙여
    시트를 OUTFIT_SHEET_RETRY(기본 1)회만 다시 그린다.

    반환: (sheet_jpeg, outfit_warn) — outfit_warn 은 재생성 뒤에도 일관되지 않으면 True
    (warning 대상). 검사가 모델 오류로 건너뛰어지면(None) 재생성하지 않고 False.
    prompt: 주면 그 시트 프롬프트를 쓴다(char-fidelity 켬 — _build_variant 가 기준 사진 번호를 넣어 만든다). 없으면 지금처럼
    _sheet_prompt.
    """
    base_outfit = _base_outfit(analysis)
    prompt = prompt if prompt is not None else _sheet_prompt(variant, analysis)
    sheet = _seedream(prompt, references, size=size)
    result = _check_outfit_consistency(sheet, base_outfit)
    if result is None or result.get("consistent"):
        return sheet, False
    # 일관되지 않다 — problems 를 고칠 지시를 붙여 시트를 1회만 다시 그린다.
    for _ in range(max(0, settings.OUTFIT_SHEET_RETRY)):
        problems = result.get("problems") or []
        fix = ""
        if problems:
            fix = " Fix these so every view wears the base outfit only: " + "; ".join(problems) + "."
        retry_prompt = (
            prompt
            + fix
            + f" Make EVERY view wear exactly this same base outfit and nothing else: {base_outfit}."
        )
        sheet = _seedream(retry_prompt, references, size=size)
        result = _check_outfit_consistency(sheet, base_outfit)
        if result is None or result.get("consistent"):
            return sheet, False
    # 재생성 뒤에도 일관되지 않다 — 그대로 쓰고 warning.
    return sheet, True


def _gen_view_checked(
    build_prompt,
    references: list[bytes],
    *,
    size: str,
    view: str,
    items: list[dict],
    pron: str,
    guard=None,
    beat=None,
    log_ctx: tuple = (),
) -> dict:
    """그림 1장 생성 + 검사(LLM 1회). 틀리면 고칠 문장으로 1회만 다시 그리고, 그래도 틀리면 반전을 검토한다(design §7.2).

    build_prompt(fix) → 프롬프트(fix 는 처음 "", 다시 그릴 때 char_fidelity.view_fix_text). view = front|back|left|right.
    guard()·beat() 는 Seedream 제출 직전 선점 확인(ClaimLost 를 그대로 올린다)·그린 뒤 하트비트다.
    반환 {"jpeg", "status", "warnings", "draws"}:
    - ok(첫 그림 통과) · fixed(다시 그린 그림 통과) · flipped(다시 그린 그림을 반전해 통과, 반전한 그림, 다시 검사하지 않는다)
    - warned(정면·후면: 다시 그려도 틀려 그대로 쓰고 warning) · dropped(옆모습: 3D 에서 뺀다, jpeg None)
    - unchecked(검사가 모델 오류. 정면·후면은 그 그림, 옆모습은 jpeg None)
    """
    side_view = view in char_fidelity.SIDES
    char_id, variant = (tuple(log_ctx) + ("", ""))[:2]
    draws = 0

    def draw(fix: str) -> bytes:
        nonlocal draws
        if guard is not None:
            guard()
        image = _seedream(build_prompt(fix), references, size=size)
        draws += 1
        if beat is not None:
            beat()
        return image

    def finish(jpeg, status: str, obs, warnings: list[str]) -> dict:
        seen = (obs or {}).get("items") or {}
        log.info(
            "vnew.char.view_check char=%s variant=%s view=%s status=%s draws=%d people=%s facing=%s items=%s",
            char_id, variant, view, status, draws,
            "-" if obs is None else obs.get("people"), "-" if obs is None else obs.get("facing"),
            ",".join(f"{i}:{seen[i]}" for i in sorted(seen)) or "-",
        )
        return {"jpeg": jpeg, "status": status, "warnings": warnings, "draws": draws}

    unchecked_warning = char_fidelity.side_warnings({view: "unchecked"}) if side_view else []
    image = draw("")
    obs = _inspect_view(image, view, items)
    if obs is None:
        return finish(None if side_view else image, "unchecked", None, unchecked_warning)
    mismatches = char_fidelity.view_mismatches(obs, view, items)
    if not mismatches:
        return finish(image, "ok", obs, [])
    image = draw(char_fidelity.view_fix_text(mismatches, view, items, pron))
    obs = _inspect_view(image, view, items)
    if obs is None:
        return finish(None if side_view else image, "unchecked", None, unchecked_warning)
    mismatches = char_fidelity.view_mismatches(obs, view, items)
    if not mismatches:
        return finish(image, "fixed", obs, [])
    if char_fidelity.can_flip(obs, view, items):
        return finish(_mirror_jpeg(image), "flipped", char_fidelity.flip_observation(obs, view), [])
    warnings = char_fidelity.view_warnings(mismatches, view)
    if side_view:
        return finish(None, "dropped", obs, warnings)
    return finish(image, "warned", obs, warnings)


def _sheet_outfit_fix(problems: list[str], base_outfit: str, *, with_outfit: bool) -> str:
    """시트 복장 고칠 문장(지금 _gen_sheet_checked 문장). with_outfit 이 거짓이면 끝의 기본 복장 글 반복만 뺀다(길이 예산).

    충실 분석의 기본 복장은 'only.' 로 끝나므로 끝 마침표를 한 번 떼고 붙인다(마침표 두 개 방지).
    """
    fix = ""
    if problems:
        fix = " Fix these so every view wears the base outfit only: " + "; ".join(problems) + "."
    if with_outfit:
        outfit = (base_outfit or "").strip().rstrip(".").rstrip()
        return fix + f" Make EVERY view wear exactly this same base outfit and nothing else: {outfit}."
    return fix + " Make EVERY view wear exactly this same base outfit and nothing else."


def _gen_sheet_fidelity(
    variant: str,
    analysis: dict,
    references: list[bytes],
    *,
    size: str,
    source_no: int | None,
    guard=None,
    beat=None,
    log_ctx: tuple = (),
) -> dict:
    """켬 시트 1장 생성 + 시트 검사(회전 순서·복장, LLM 1회). 틀리면 OUTFIT_SHEET_RETRY 한도 안에서 다시 그린다(design §7.4).

    고칠 문장(회전 + 복장)은 _fidelity_prompt(fix=…)로 넣어 길이 예산(_budget_prompt, 2,900자) 안에 둔다(Claude 결정 4).
    복장 고칠 문장 끝의 기본 복장 글 반복은 첫 프롬프트에 남은 자리에 들어갈 때만 넣는다. 기본 복장은 이미 WHO 에 있고,
    반복 때문에 예산이 넘치면 끝의 그림체·추가 요청이 잘리기 때문이다(ONE_F 가 옷 글 반복을 뺀 것과 같은 이유).
    반환 {"jpeg", "status"(ok|fixed|warned|unchecked), "warnings", "draws"}.
    """
    char_id, variant_name = (tuple(log_ctx) + ("", ""))[:2]
    base = _base_outfit(analysis)
    prompt = _fidelity_prompt("sheet", variant, analysis, source_no=source_no)
    room = _PROMPT_MAX - len(prompt) - 1
    draws = 1
    sheet = _seedream(prompt, references, size=size)
    if beat is not None:
        beat()
    obs = _inspect_sheet(sheet, base)
    status = ""
    mismatches: list[tuple] = []
    if obs is None:
        status = "unchecked"
    else:
        mismatches = char_fidelity.sheet_mismatches(obs)
        status = "" if mismatches else "ok"
    if not status:
        for _ in range(max(0, settings.OUTFIT_SHEET_RETRY)):
            rotation = [mm for mm in mismatches if mm[0] != "outfit"]
            fix = char_fidelity.sheet_fix_text(rotation)
            if any(mm[0] == "outfit" for mm in mismatches):
                problems = obs.get("problems") or []
                full = fix + _sheet_outfit_fix(problems, base, with_outfit=True)
                fix = full if len(full.strip()) <= room else fix + _sheet_outfit_fix(problems, base, with_outfit=False)
            if guard is not None:
                guard()
            sheet = _seedream(_fidelity_prompt("sheet", variant, analysis, source_no=source_no, fix=fix), references, size=size)
            draws += 1
            if beat is not None:
                beat()
            obs = _inspect_sheet(sheet, base)
            if obs is None:
                status = "unchecked"
                break
            mismatches = char_fidelity.sheet_mismatches(obs)
            if not mismatches:
                status = "fixed"
                break
    warnings: list[str] = []
    if not status:
        status = "warned"
        if any(mm[0] == "outfit" for mm in mismatches):
            warnings.append(char_fidelity.WARN_SHEET_OUTFIT)
        if any(mm[0] != "outfit" for mm in mismatches):
            warnings.append(char_fidelity.WARN_SHEET_ROTATION)
    log.info(
        "vnew.char.sheet_check char=%s variant=%s status=%s draws=%d figures=%s outfit=%s",
        char_id, variant_name or variant, status, draws, char_fidelity.sheet_figures_text(obs),
        "-" if obs is None else ("ok" if obs.get("consistent") else "bad"),
    )
    return {"jpeg": sheet, "status": status, "warnings": warnings, "draws": draws}


def _build_variant(
    owner: str,
    char_id: str,
    variant: str,
    version: int,
    analysis: dict,
    refs: list[bytes],
    token: str = "",
    *,
    base_first: bool = False,
) -> None:
    """한 variant 의 그림 + 3D. 실패하면 그 variant 만 failed 로 두고 예외를 올리지 않는다.

    token 이 있으면 외부 유료 제출(seedream·submit_3d) 직전마다 자기 claim 을 다시 확인하고,
    아니면 ClaimLost 로 접는다(중복 실행 이중 과금 방지).

    base_first: refs 맨 앞이 기준 사진인가(_reference_jpegs). VNEW_CHAR_FIDELITY 켬 + 충실 분석 + base_first 이면 시트·정면·
    후면 프롬프트에 기준 사진의 참고 번호(시트 1·정면 2·후면 3)로 옷 그대로 문장을 넣는다(결정 S2).

    char-fidelity T2(design §10.1): VNEW_CHAR_FIDELITY 켬이면 시트를 회전·복장 검사 1회(_gen_sheet_fidelity)로, 정한 소품이
    있으면 정면·후면을 그림 검사(_gen_view_checked)로 한다(소품이 없으면 지금 사람 수 검사). VNEW_3D_SIDE_VIEWS 켬이면 후면
    다음에 옆모습 두 장을 그려(_draw_side_views) 쓸 수 있는 것만 3D 에 보낸다. 다시 그린 그림은 '{kind}-retry' 원장(결정 1).
    두 설정 끔이면 호출·원장·저장 patch 가 지금과 같다(cf-b).
    """
    fidelity = settings.VNEW_CHAR_FIDELITY
    sides_on = settings.VNEW_3D_SIDE_VIEWS
    source_ok = fidelity and base_first and _is_fidelity_analysis(analysis)
    # 정한 소품(켬 + 충실 분석). 없으면 정면·후면은 지금 사람 수 검사(R2.13), 대명사도 쓰지 않는다.
    items = _sided_items(analysis)
    pron = _pronoun_of(analysis) if items else "their"
    check: dict[str, str] = {}  # viewCheck 에 쓸 검사 결과(검사한 것만)

    def src(n: int) -> int | None:
        return n if source_ok else None

    def guard() -> None:
        _guard_submit_claim(owner, char_id, variant, token)

    def beat() -> None:
        _touch_heartbeat(owner, char_id, variant)

    def checked_view(view: str, source_n: int, references: list[bytes], size: str) -> tuple[bytes, list[str], int]:
        """켬 + 소품 있음: 정면·후면 그림 검사(LLM 1회, 1회 재생성, 반전). (jpeg, warnings, draws)."""
        result = _gen_view_checked(
            lambda fix: _fidelity_prompt(view, variant, analysis, source_no=src(source_n), fix=fix),
            references, size=size, view=view, items=items, pron=pron, guard=guard, beat=beat, log_ctx=(char_id, variant),
        )
        check[view] = result["status"]
        return result["jpeg"], result["warnings"], result["draws"]

    def plain_prompt(view: str, source_n: int) -> str:
        """소품 없음·끔: 지금 사람 수 검사(_gen_checked)에 넘기는 프롬프트. 켬이면 _fidelity_prompt, 끔이면 지금 함수."""
        if fidelity:
            return _fidelity_prompt(view, variant, analysis, source_no=src(source_n))
        return _front_prompt(variant, analysis) if view == "front" else _back_prompt(variant, analysis)

    try:
        _set_variant(owner, char_id, variant, {"status": "images", "warning": "", "heartbeatAt": now_iso()})

        # 시트(2560x1440) — 복장 일관성 검사: 면마다 옷이 다르면 1회 다시 그리고, 그래도면 warning.
        # front/back 은 '일관성 검사를 통과한(또는 재생성한) 시트' 를 참고로 그린다.
        _guard_submit_claim(owner, char_id, variant, token)
        sheet_warnings: list[str] = []
        sheet_draws = 1
        if fidelity:
            # 켬: 회전 순서 + 복장을 한 번에 검사(_inspect_sheet)하고 틀린 인물마다 고칠 문장을 붙여 다시 그린다(R3.3~R3.7).
            result = _gen_sheet_fidelity(variant, analysis, refs, size=settings.SHEET_SIZE, source_no=src(1),
                                         guard=guard, beat=beat, log_ctx=(char_id, variant))
            sheet, outfit_warn = result["jpeg"], False
            sheet_warnings, sheet_draws = result["warnings"], result["draws"]
            check["sheet"] = result["status"]
        else:
            sheet, outfit_warn = _gen_sheet_checked(variant, analysis, refs, size=settings.SHEET_SIZE)
        _charge_draws(owner, char_id, variant, "sheet", sheet_draws)
        s_key = sheet_key(owner, char_id, variant, version)
        STORE.put_bytes(s_key, sheet, "image/jpeg")
        _touch_heartbeat(owner, char_id, variant)

        # 정면 전신(1440x2560) — 시트를 첫 참고로 넣어 같은 인물·옷이 되게 한다.
        # 사람 수 검사: 여러 명이면 1회 다시 그리고, 그래도면 warning.
        _guard_submit_claim(owner, char_id, variant, token)
        front_warnings: list[str] = []
        front_draws = 1
        front_multi = False
        if fidelity and items:
            front, front_warnings, front_draws = checked_view("front", 2, [sheet] + list(refs)[:3], settings.FRONT_SIZE)
        else:
            front, front_multi = _gen_checked(plain_prompt("front", 2), [sheet] + list(refs)[:3], size=settings.FRONT_SIZE)
        _charge_draws(owner, char_id, variant, "front", front_draws)
        f_key = front_key(owner, char_id, variant, version)
        STORE.put_bytes(f_key, front, "image/jpeg")
        _touch_heartbeat(owner, char_id, variant)

        # 후면 전신(1440x2560) — 정면 + 시트를 참고로 따로 생성(이전엔 시트 5번째 칸을 잘랐음).
        # 정면을 반전해 썼으면 후면은 반전한 정면을 참고로 그린다.
        _guard_submit_claim(owner, char_id, variant, token)
        back_warnings: list[str] = []
        back_draws = 1
        back_multi = False
        if fidelity and items:
            back, back_warnings, back_draws = checked_view("back", 3, [front, sheet] + list(refs)[:2], settings.BACK_SIZE)
        else:
            back_refs = [front, sheet] + list(refs)[:2]
            back, back_multi = _gen_checked(plain_prompt("back", 3), back_refs, size=settings.BACK_SIZE)
        _charge_draws(owner, char_id, variant, "back", back_draws)
        b_key = back_key(owner, char_id, variant, version)
        STORE.put_bytes(b_key, back, "image/jpeg")

        warnings = []
        if outfit_warn:
            warnings.append("복장이 면마다 다를 수 있습니다")
        if front_multi or back_multi:
            warnings.append("정면/후면에 여러 사람이 보일 수 있습니다")
        warnings = char_fidelity.merge_warnings(sheet_warnings, warnings, front_warnings, back_warnings)
        warning = " / ".join(warnings)
        patch = {"status": "model3d", "sheetKey": s_key, "frontKey": f_key, "backKey": b_key,
                 "warning": warning, "heartbeatAt": now_iso()}
        left = right = None
        if sides_on:
            # 옆모습을 그리는 동안 variant 는 images(R4.8). 키 3개·warning·검사 결과를 먼저 저장해 끊겨도 옆모습만 이어 그린다.
            _set_variant_checked(owner, char_id, variant, dict(patch, status="images"), check)
            sides = _draw_side_views(owner, char_id, variant, version, analysis, front, back, sheet,
                                     refs[0] if refs else None, base_first=base_first, token=token, need=("left", "right"))
            side_warnings = char_fidelity.side_warnings({view: r["status"] for view, r in sides.items()})
            warnings = char_fidelity.merge_warnings(warnings, side_warnings)
            _set_variant(owner, char_id, variant, {"status": "model3d", "warning": " / ".join(warnings), "heartbeatAt": now_iso()})
            left, right = sides["left"]["jpeg"], sides["right"]["jpeg"]
        else:
            _set_variant_checked(owner, char_id, variant, patch, check)

        # 3D: front·back(+ 쓸 수 있는 옆모습) 정사각 패딩 → data URI 제출 → 완료까지 폴링 → 복사
        _build_3d_from_images(owner, char_id, variant, version, front, back, token, left=left, right=right)
    except Exception as exc:  # noqa: BLE001
        # ClaimLost: 다른 실행이 이 variant 를 가져갔다 — 제출하지 않고 접는다. 상태는 그쪽이
        # 관리하므로 건드리지 않는다(실패로 덮어쓰지 않는다).
        if isinstance(exc, VpocError) and exc.kind == "ClaimLost":
            log.info("vnew.build_variant_claim_lost char=%s variant=%s", char_id, variant)
            return
        log.warning("vnew.build_variant_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        # 3D 가 제출만 되고 끝난 경우(조회·복사 중 예외 등) model3d.status 가 submitted 로 남지 않게
        # 함께 failed 로 맞춘다 — ready 는 건드리지 않는다.
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})


def _set_variant_checked(owner: str, char_id: str, variant: str, patch: dict, check: dict) -> None:
    """_set_variant + 검사 결과(viewCheck) 합치기. 이미 있는 viewCheck 값은 지우지 않는다. check 가 비면 _set_variant 그대로."""
    if not check:
        _set_variant(owner, char_id, variant, patch)
        return

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        v.update(patch)
        merged = dict(v["viewCheck"]) if isinstance(v.get("viewCheck"), dict) else {}
        merged.update(check)
        v["viewCheck"] = merged

    mutate_char(owner, char_id, mutator)


def _set_view_check(owner: str, char_id: str, variant: str, view: str, status: str, key: str = "") -> None:
    """viewCheck[view] = status. 옆모습(left·right)이면 leftKey·rightKey 도 함께 쓴다(3D 에 못 쓰면 "")(design §10.2).

    옆모습 하나를 끝낼 때마다 저장하므로, 왼쪽만 끝내고 끊겨도 이어 만들기가 오른쪽만 그린다.
    """

    def mutator(data: dict) -> None:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        if not isinstance(v.get("viewCheck"), dict):
            v["viewCheck"] = {}
        v["viewCheck"][view] = status
        if view in char_fidelity.SIDES:
            v[f"{view}Key"] = key

    mutate_char(owner, char_id, mutator)


def _draw_side_views(
    owner: str,
    char_id: str,
    variant: str,
    version: int,
    analysis: dict,
    front: bytes,
    back: bytes,
    sheet: bytes,
    base_ref: bytes | None,
    *,
    base_first: bool,
    token: str,
    need: tuple = ("left", "right"),
) -> dict:
    """옆모습을 차례로(왼쪽 → 오른쪽, 결정 S10) 그리고 검사한다(design §10.2). 반환 {side: _gen_view_checked 결과}.

    참고 [정면, 후면, 시트, 기준 사진(참고 맨 앞 원본)] 이라 기준 사진은 Image 4(base_first 일 때만 SOURCE 문장).
    그림마다 Seedream 제출 직전 선점 확인(_guard_submit_claim)·그린 뒤 하트비트. 3D 에서 뺀 그림도 원장에 남긴다(R4.4).
    3D 에 쓸 수 있는 옆모습(ok·fixed·flipped)만 left.jpg·right.jpg 로 저장하고, 결과는 viewCheck 에 둔다.
    """
    refs = [front, back, sheet] + ([base_ref] if base_ref is not None else [])
    items = _sided_items(analysis)
    pron = _pronoun_of(analysis) if items else "their"
    source_no = 4 if (base_first and base_ref is not None) else None

    def guard() -> None:
        _guard_submit_claim(owner, char_id, variant, token)

    def beat() -> None:
        _touch_heartbeat(owner, char_id, variant)

    out: dict = {}
    for side in need:
        if side not in char_fidelity.SIDES:
            continue
        result = _gen_view_checked(
            lambda fix, _side=side: _side_prompt(_side, variant, analysis, source_no=source_no, fix=fix),
            refs, size=settings.FRONT_SIZE, view=side, items=items, pron=pron, guard=guard, beat=beat,
            log_ctx=(char_id, variant),
        )
        _charge_draws(owner, char_id, variant, side, result["draws"])
        key = ""
        if result["jpeg"] is not None:
            key = (left_key if side == "left" else right_key)(owner, char_id, variant, version)
            STORE.put_bytes(key, result["jpeg"], "image/jpeg")
        _set_view_check(owner, char_id, variant, side, result["status"], key)
        beat()
        out[side] = result
    return out


def _make_3d_resubmit(
    owner: str,
    char_id: str,
    variant: str,
    front_sq: bytes,
    back_sq: bytes,
    token: str = "",
    *,
    left_sq: bytes | None = None,
    right_sq: bytes | None = None,
    provider: str | None = None,
    counter: dict | None = None,
):
    """'잃은 요청' 재제출 콜백을 만든다(_wait_3d 에 넘긴다).

    같은 front/back 정사각으로 새로 제출하고, 원장을 1회 더 달고, model3d.requestArn·resubmits 를
    갱신한 뒤 새 requestArn 을 돌려준다. 재제출 한도(MAX_RESUBMITS)를 넘으면 None(그러면 실패).
    제출 직전 자기 claim 을 다시 확인한다(token 이 있으면). 아니면 빈 문자열(=재제출 포기).
    left_sq·right_sq(char-fidelity R4.6): 처음 제출과 같은 옆모습 정사각을 함께 다시 낸다(없으면 지금처럼 정면·후면만).
    provider(T2b R8.6): 처음 제출과 같은 공급자로 다시 낸다(None 이면 설정). counter: 콜백 둘이 재제출 한도를 함께 셀 때 넘긴다
    (run_retry3d 가 이전 요청과 새 요청의 공급자가 다를 때).
    """
    done = counter if counter is not None else {"n": 0}

    def resubmit() -> str:
        if done["n"] >= settings.MAX_RESUBMITS:
            return ""
        # 제출 직전 claim 재확인 — 다른 실행이 가져갔으면 재제출하지 않는다.
        if token and not _holds_claim(owner, char_id, variant, token):
            return ""
        done["n"] += 1
        request_arn = submit_3d(front_sq, back_sq, left_square=left_sq, right_square=right_sq, provider=provider)
        _charge_3d_request(owner, char_id, variant, request_arn)

        def mutator(data: dict) -> None:
            v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
            m3d = v.setdefault("model3d", {})
            m3d["status"] = "submitted"
            m3d["requestArn"] = request_arn
            m3d["resubmits"] = int(m3d.get("resubmits") or 0) + 1
            m3d["error"] = ""
            _note_3d_endpoint(m3d, request_arn)

        mutate_char(owner, char_id, mutator)
        _touch_heartbeat(owner, char_id, variant)
        return request_arn

    return resubmit


def _build_3d_from_images(
    owner: str,
    char_id: str,
    variant: str,
    version: int,
    front: bytes,
    back: bytes,
    token: str = "",
    *,
    left: bytes | None = None,
    right: bytes | None = None,
) -> None:
    """front/back 바이트로 3D 를 새로 제출하고 완료까지 폴링해 복사한다(예외는 올린다).

    조회가 'Downstream service unavailable' 만 연속으로 돌려주면(fal 이 요청을 잃음) 같은
    front/back 으로 새로 제출한다(variant 당 최대 MAX_RESUBMITS 회).
    제출 직전 자기 claim 을 다시 확인한다(token 이 있으면). 아니면 ClaimLost 로 접는다.
    left·right(char-fidelity R4.5): 3D 에 쓸 수 있는 옆모습이 있으면 같은 정사각 패딩으로 함께 보내고 재제출도 같은 묶음을 쓴다.
    T2b(R8): 공급자는 시작 때 한 번 정한다(_3d_provider). 정사각(_3d_squares)·제출·재제출·원장·회전이 모두 그 공급자를 따른다.
    """
    provider = _3d_provider()
    front_sq, back_sq, left_sq, right_sq = _3d_squares(front, back, left, right, provider=provider)
    _guard_submit_claim(owner, char_id, variant, token)  # 제출 직전 claim 재확인(이중 제출 방지)
    request_arn = submit_3d(front_sq, back_sq, left_square=left_sq, right_square=right_sq, provider=provider)
    _charge_3d_request(owner, char_id, variant, request_arn)
    _set_variant_3d_request(owner, char_id, variant, {"status": "submitted", "requestArn": request_arn})
    _touch_heartbeat(owner, char_id, variant)

    resubmit = _make_3d_resubmit(
        owner, char_id, variant, front_sq, back_sq, token, left_sq=left_sq, right_sq=right_sq, provider=provider
    )
    state = _wait_3d(request_arn, resubmit=resubmit, owner=owner, char_id=char_id, variant=variant)
    if state["status"] != "Completed":
        _set_variant_3d(owner, char_id, variant, {"status": "failed", "error": state.get("error", "")})
        _set_variant(owner, char_id, variant, {"status": "failed", "error": state.get("error", "3D 실패")})
        return

    # 재제출은 같은 공급자로만 하므로, 처음 요청의 endpoint 로 Tripo 결과인지 가린다.
    _copy_3d_result(owner, char_id, variant, version, state, request_arn=request_arn)


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


def _copy_3d_result(owner: str, char_id: str, variant: str, version: int, state: dict, request_arn: str = "") -> None:
    """완료된 3D 결과(GLB·썸네일)를 우리 버킷으로 옮기고 variant 를 ready 로 맞춘다.

    request_arn(char-fidelity T2b R8.5): endpoint 가 tripo3d/ 로 시작하고 VNEW_3D_TRIPO_YAW_DEG 가 0 이 아니면 GLB 정면을 +Z 로
    돌려(_orient_glb) 저장한다. 돌리지 못하면 경고 로그 1줄을 남기고 받은 GLB 를 그대로 저장한다(빌드는 실패시키지 않는다).
    """
    glb_bytes_data = _fetch_url(state["glbUrl"])
    yaw = settings.VNEW_3D_TRIPO_YAW_DEG
    if yaw and _provider_of(request_arn) == "tripo":
        try:
            glb_bytes_data = _orient_glb(glb_bytes_data, yaw)
            log.info("vnew.3d.oriented char=%s variant=%s yaw=%s bytes=%d", char_id, variant, yaw, len(glb_bytes_data))
        except Exception as exc:  # noqa: BLE001 — 회전 실패는 원본 저장(R8.5)
            log.warning("vnew.3d.orient_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:200])
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


# ---------------------------------------------------------------- GLB 정면 돌리기(char-fidelity T2b, design §19.5)
#
# Tripo H3.1 결과는 정면이 +X 를 본다(orientation align_image 도 같음, Claude 실측). 화면(model-viewer)은 +Z 가 정면이다.
# Y축 θ 회전 R = [[cosθ, 0, sinθ], [0, 1, 0], [-sinθ, 0, cosθ]] 라 θ = -90° 면 +X → +Z, 쿼터니언 (0, -0.7071, 0, 0.7071).

_GLB_MAGIC = b"glTF"
_GLB_CHUNK_JSON = 0x4E4F534A


def _snap(value: float) -> float:
    """cos(90°) 같은 1e-12 미만 오차를 0 으로(돌린 값이 6e-17 꼴로 남지 않게)."""
    return 0.0 if abs(value) < 1e-12 else value


def _glb_numbers(value, count: int, what: str) -> list[float]:
    """glTF 숫자 배열 검사(길이·유한 실수, bool 거절). 틀리면 ValueError."""
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{what} must be {count} numbers")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError(f"{what} has a non-number")
        out.append(float(item))
    return out


def _yaw_node(node: dict, yaw_deg: float) -> None:
    """노드 한 개의 변환 앞에 Y축 yaw_deg 회전을 곱한다(제자리 수정).

    matrix 가 있으면 R·M(열 우선 16개). 없으면 rotation = q_yaw ⊗ rotation(없으면 q_yaw), translation = R·translation(있을 때).
    R·(T·Rn·S) = (R·T·R⁻¹)·(R·Rn)·S 이고 R·T·R⁻¹ 은 R·t 만큼 옮기는 것이라 노드 변환 전체를 앞에서 곱한 것과 같다.
    """
    theta = math.radians(yaw_deg)
    c, s = _snap(math.cos(theta)), _snap(math.sin(theta))
    rot = ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    if "matrix" in node:
        m = _glb_numbers(node["matrix"], 16, "matrix")
        out = list(m)
        for col in range(4):
            x, y, z = m[col * 4], m[col * 4 + 1], m[col * 4 + 2]
            for row in range(3):
                out[col * 4 + row] = _snap(rot[row][0] * x + rot[row][1] * y + rot[row][2] * z)
        node["matrix"] = out
        return
    bx, by, bz, bw = _glb_numbers(node.get("rotation", [0.0, 0.0, 0.0, 1.0]), 4, "rotation")
    ay, aw = _snap(math.sin(theta / 2)), _snap(math.cos(theta / 2))  # q_yaw = (0, ay, 0, aw)
    node["rotation"] = [
        _snap(aw * bx + ay * bz),
        _snap(aw * by + ay * bw),
        _snap(aw * bz - ay * bx),
        _snap(aw * bw - ay * by),
    ]
    if "translation" in node:
        t = _glb_numbers(node["translation"], 3, "translation")
        node["translation"] = [_snap(rot[row][0] * t[0] + rot[row][1] * t[1] + rot[row][2] * t[2]) for row in range(3)]


def _orient_glb(glb_bytes: bytes, yaw_deg: float) -> bytes:
    """GLB 기본 scene 의 루트 노드마다 Y축 yaw_deg 회전을 앞에서 곱해 다시 묶는다. 순수 함수(입출력·로그 없음).

    - 읽기: magic "glTF", version 2, 헤더 길이 = 바이트 길이, 첫 청크 JSON. 그 뒤 청크(BIN 등)는 바이트 그대로 둔다.
    - 루트 노드 = scenes[scene(없으면 0)].nodes(같은 번호는 한 번). 자식 노드는 부모를 따라 돌므로 건드리지 않는다.
    - 쓰기: JSON 은 공백(0x20), 그 밖 청크는 0 으로 4바이트 정렬. 헤더 길이를 다시 쓴다.
    - GLB 가 아니거나 모양이 틀리면 ValueError(호출한 _copy_3d_result 가 원본을 저장한다).
    메모리: BIN 은 memoryview 로 넘겨 결과 한 벌(약 GLB 크기)만 새로 만든다.
    """
    try:
        data = memoryview(glb_bytes)
        if len(data) < 20 or bytes(data[:4]) != _GLB_MAGIC:
            raise ValueError("not a GLB (magic)")
        version, total = struct.unpack_from("<II", data, 4)
        if version != 2 or total != len(data):
            raise ValueError(f"bad GLB header version={version} length={total}/{len(data)}")
        chunks: list[tuple[int, memoryview]] = []
        offset = 12
        while offset < total:
            if offset + 8 > total:
                raise ValueError("truncated GLB chunk header")
            length, kind = struct.unpack_from("<II", data, offset)
            end = offset + 8 + length
            if end > total:
                raise ValueError("truncated GLB chunk")
            chunks.append((kind, data[offset + 8:end]))
            offset = end
        if not chunks or chunks[0][0] != _GLB_CHUNK_JSON:
            raise ValueError("first GLB chunk is not JSON")
        gltf = json.loads(bytes(chunks[0][1]).rstrip(b"\x00").decode("utf-8"))
        scenes, nodes = gltf.get("scenes"), gltf.get("nodes")
        index = gltf.get("scene", 0)
        if not isinstance(scenes, list) or not isinstance(nodes, list) or isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("GLB has no scenes/nodes")
        if not 0 <= index < len(scenes) or not isinstance(scenes[index], dict):
            raise ValueError("GLB default scene out of range")
        roots = scenes[index].get("nodes")
        if not isinstance(roots, list) or not roots:
            raise ValueError("GLB default scene has no root nodes")
        for root in dict.fromkeys(roots):
            if isinstance(root, bool) or not isinstance(root, int) or not 0 <= root < len(nodes):
                raise ValueError("GLB root node out of range")
            if not isinstance(nodes[root], dict):
                raise ValueError("GLB root node is not an object")
            _yaw_node(nodes[root], yaw_deg)
        text = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        text += b" " * (-len(text) % 4)
        parts: list = [b"", struct.pack("<II", len(text), _GLB_CHUNK_JSON), text]
        for kind, body in chunks[1:]:
            pad = -len(body) % 4
            parts += [struct.pack("<II", len(body) + pad, kind), body] + ([b"\x00" * pad] if pad else [])
        parts[0] = struct.pack("<4sII", _GLB_MAGIC, 2, sum(len(part) for part in parts) + 12)
        return b"".join(parts)
    except ValueError:
        raise
    except (struct.error, TypeError, KeyError, IndexError, AttributeError, RecursionError) as exc:
        raise ValueError(f"bad GLB: {type(exc).__name__}") from exc


def _reference_jpegs(owner: str, char: dict, analysis: dict) -> tuple[list[bytes], bool]:
    """Seedream 참고 그림(최대 MAX_REFERENCE_IMAGES 장)과 '기준 사진이 맨 앞인가'(design §8).

    VNEW_CHAR_FIDELITY 켬 + 충실 분석이고 baseSourceFileId 가 지금 업로드 목록에 있으면 그 그림을 맨 앞에, 나머지를 업로드
    순서로 둔다. 아니면(끔·옛 분석·틀린 번호·지운 그림) 지금처럼 업로드 순서다. run_build·run_resume_variant·
    run_rebuild_variant 가 쓴다.
    """
    ids = [src["fileId"] for src in (char.get("sources") or [])]
    base_id = ""
    if settings.VNEW_CHAR_FIDELITY and _is_fidelity_analysis(analysis):
        base_id = str(analysis.get("baseSourceFileId") or "")
    order, base_first = char_fidelity.reference_order(ids, base_id, settings.MAX_REFERENCE_IMAGES)
    return [source_jpeg(owner, char["id"], file_id) for file_id in order], base_first


def run_build(owner: str, char_id: str, version: int, tokens: dict | None = None) -> None:
    """빌드 파이프라인(백그라운드 스레드에서 호출). 한 variant 실패해도 다른 variant 는 계속.

    tokens 는 variant 별 선점 token(start_build 가 mutate 안에서 미리 잡은 것). 각 variant 는 자기
    claim 을 잡고 있을 때만 진행하고, 끝날 때 해제한다.
    """
    tokens = tokens or {}
    try:
        char, _ = load_char(owner, char_id)

        # a. 분석
        analysis = analyze_sources(owner, char)

        def set_analysis(data: dict) -> None:
            data["analysis"] = analysis

        mutate_char(owner, char_id, set_analysis)

        # 참고 이미지(최대 4장). char-fidelity 켬 + 충실 분석이면 기준 사진을 맨 앞에(_reference_jpegs).
        refs, base_first = _reference_jpegs(owner, char, analysis)

        # b~d. variant 별(순차. 실패해도 다음 variant 진행). 파드 안 실행 중 표시 + 선점 확인.
        # 아직 시작 안 한(대기) variant 는 keepalive 로 등록해, 앞 variant 의 heartbeat 갱신마다
        # 그 claim·heartbeat 도 함께 찍는다(대기 variant 중복 자동 재개 방지).
        order = list(settings.VARIANTS)
        try:
            for i, variant in enumerate(order):
                token = tokens.get(variant, "")
                # 아직 시작 안 한 뒤쪽 variant 들만 keepalive 로 등록(자기 자신·앞선 것 제외).
                pending = {
                    pv: tokens.get(pv, "")
                    for pv in order[i + 1 :]
                    if tokens.get(pv, "")
                }
                _set_keepalive(owner, char_id, pending)
                if token and not _holds_claim(owner, char_id, variant, token):
                    continue
                _mark_running(owner, char_id, variant)
                try:
                    _touch_heartbeat(owner, char_id, variant)
                    _build_variant(owner, char_id, variant, version, analysis, refs, token, base_first=base_first)
                finally:
                    _clear_running(owner, char_id, variant)
                    if token:
                        _release_claim(owner, char_id, variant, token)
        finally:
            _set_keepalive(owner, char_id, {})

        # e. 최종 상태 롤업(하나라도 ready 면 ready, 진행 중이면 building, 전부 failed 면 failed)
        _refresh_char_status(owner, char_id)
        # f. outfitLayersEn 를 'auto' 추가 복장 후보로 넣는다(그림은 그리지 않는다 — 비용).
        sync_auto_outfits(owner, char_id, analysis)
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
        # 분석 실패 등으로 variant 루프 전에 빠졌으면 선점이 남지 않게 해제.
        for variant in settings.VARIANTS:
            token = tokens.get(variant, "")
            if token:
                _release_claim(owner, char_id, variant, token)


def start_build(owner: str, char_id: str, extra_prompt_ko: str | None = None) -> int:
    """build 요청: 진행 중이면 409, 아니면 version+1 로 variant 초기화하고 스레드 시작. 새 version 반환.

    각 variant 를 '시작 전에'(mutate 안에서) 선점(claim)·heartbeat 로 찍는다 — 직후 조회 자동
    재개가 '끊긴 작업'으로 오인해 두 번째 실행을 띄우지 못하게 한다.

    extra_prompt_ko:
      - None  → 이전 extraPromptKo 를 그대로 유지(다음 빌드에도 유지).
      - ""    → extraPromptKo 를 지운다.
      - 문자열 → extraPromptKo 로 저장(≤ MAX_EXTRA_PROMPT_LEN, 넘으면 자른다).
    """
    tokens = {v: secrets.token_hex(6) for v in settings.VARIANTS}

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
        variants = {}
        for v in settings.VARIANTS:
            fresh = _empty_variant()
            # 선점·heartbeat 를 '시작 전에' 잡는다(이후 백그라운드는 자기 claim 일 때만 진행).
            _claim_variant_dict(fresh, tokens[v])
            variants[v] = fresh
        data["variants"] = variants
        return version

    updated, version = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)

    thread = threading.Thread(
        target=run_build, args=(owner, char_id, version, tokens), daemon=True, name=f"vnew-build-{char_id}"
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
    """3D만 다시 만들기 요청. front/back 없으면 400, 진행 중이면 409. version 반환(스레드 시작).

    시작 전에(mutate 안에서) 선점(claim)·heartbeat 를 지금으로 찍는다 — 직후 조회 자동 재개가
    '끊긴 작업'으로 오인해 두 번째 실행을 띄우는(Hunyuan3D 이중 제출) 것을 막는다.
    """
    if variant not in settings.VARIANTS:
        raise VpocError("vnew.retry3d", "BadVariant", "알 수 없는 그림체입니다.", 404)

    token = secrets.token_hex(6)

    def mutator(data: dict) -> int:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        if not v.get("frontKey") or not v.get("backKey"):
            raise VpocError(
                "vnew.retry3d", "NoImages", "정면·후면 이미지가 없어 3D 만 다시 만들 수 없습니다.", 400
            )
        if _retry3d_running(v) or _claim_fresh(v):
            raise VpocError("vnew.retry3d", "AlreadyBuilding", "3D 를 이미 다시 만들고 있습니다.", 409)
        v["status"] = "model3d"
        v["error"] = ""
        # 선점·heartbeat 를 '시작 전에' 잡는다(이후 백그라운드는 자기 claim 일 때만 진행).
        _claim_variant_dict(v, token)
        m3d = v.setdefault("model3d", {})
        m3d["status"] = "submitted"
        m3d["error"] = ""
        m3d["retryStartedAt"] = now_iso()
        return int(data.get("version") or 0)

    updated, version = mutate_char(owner, char_id, mutator)
    upsert_index(owner, updated)
    _mark_running(owner, char_id, variant)

    thread = threading.Thread(
        target=run_retry3d,
        args=(owner, char_id, variant, version, token),
        daemon=True,
        name=f"vnew-retry3d-{char_id}-{variant}",
    )
    thread.start()
    return version


def _stored_side_view(v: dict, view: str, char_id: str, variant: str, where: str) -> bytes | None:
    """저장된 옆모습(leftKey·rightKey) 바이트. 키가 없으면 None, 읽기 실패면 None + 경고 로그(보기·예외 종류만, 키 없음)."""
    key = str(v.get(f"{view}Key") or "")
    if not key:
        return None
    try:
        return STORE.get_bytes(key)
    except Exception as exc:  # noqa: BLE001 — 그 옆모습만 빼고 3D 는 진행한다
        log.warning("vnew.%s.side_missing char=%s variant=%s view=%s err=%s", where, char_id, variant, view,
                    type(exc).__name__)
        return None


def run_retry3d(owner: str, char_id: str, variant: str, version: int, token: str = "", owns_claim: bool = True) -> None:
    """3D만 다시(백그라운드). 이전 requestArn 이 완료면 재사용(제출 0회), 아니면 새로 제출한다.

    token: 이 실행이 쥔 claim. 비어 있지 않으면 외부 제출(submit_3d) 직전마다 자기 claim 을 다시
      확인하고(아니면 ClaimLost) 시작 때도 holds_claim 으로 가드한다.
    owns_claim: True 면(start_retry3d 경로) 끝날 때(finally) running·claim 을 해제한다. False 면
      (resume 경로에서 claim·running 을 이미 쥔 채 호출) 해제는 그쪽 finally 가 한다.

    이전 요청 조회가 'Downstream service unavailable' 만 연속으로 돌려주면(fal 이 요청을 잃음)
    기다리지 않고 같은 front/back 으로 바로 새로 제출한다(variant 당 최대 MAX_RESUBMITS 회).
    char-fidelity R4.6: VNEW_3D_SIDE_VIEWS 켬이면 저장된 옆모습을 새 제출·재제출에 함께 보낸다(끔이면 정면·후면만).
    char-fidelity T2b(R8.6): 이전 요청은 그 requestArn 의 공급자로 조회·재제출·회전하고, 새 제출은 설정의 공급자로 한다.
    """
    try:
        if token and not _holds_claim(owner, char_id, variant, token):
            return
        char, _ = load_char(owner, char_id)
        v = (char.get("variants") or {}).get(variant) or {}
        front_key = v.get("frontKey") or ""
        back_key = v.get("backKey") or ""
        if not front_key or not back_key:
            raise VpocError("vnew.retry3d", "NoImages", "정면·후면 이미지가 없습니다.", 400)
        prior_arn = (v.get("model3d") or {}).get("requestArn") or ""
        _touch_heartbeat(owner, char_id, variant)

        # front/back 정사각을 미리 만들어 둔다('잃은 요청' 재제출에 쓴다).
        front = STORE.get_bytes(front_key)
        back = STORE.get_bytes(back_key)
        # char-fidelity R4.6: 옆모습 설정이 켜져 있으면 저장된 옆모습(leftKey·rightKey)도 같은 묶음으로 보낸다(읽기 실패면 그 쪽만 뺀다).
        stored: dict[str, bytes | None] = {"left": None, "right": None}
        if settings.VNEW_3D_SIDE_VIEWS:
            for view in char_fidelity.SIDES:
                stored[view] = _stored_side_view(v, view, char_id, variant, "retry3d")
        # T2b(R8.6): 새 제출은 설정의 공급자, 이전 요청의 잃은 요청 재제출은 그 요청의 공급자(arn endpoint)로 같은 그림을 다시 낸다.
        # 두 콜백은 재제출 한도(MAX_RESUBMITS)를 함께 센다(지금처럼 한 번의 retry3d 에 최대 1 + MAX_RESUBMITS 건).
        provider = _3d_provider()
        front_sq, back_sq, left_sq, right_sq = _3d_squares(front, back, stored["left"], stored["right"], provider=provider)
        counter = {"n": 0}
        resubmit = _make_3d_resubmit(
            owner, char_id, variant, front_sq, back_sq, token, left_sq=left_sq, right_sq=right_sq, provider=provider,
            counter=counter,
        )
        prior_resubmit = resubmit
        if prior_arn and _provider_of(prior_arn) != provider:
            prior_provider = _provider_of(prior_arn)
            p_front, p_back, p_left, p_right = _3d_squares(front, back, stored["left"], stored["right"], provider=prior_provider)
            prior_resubmit = _make_3d_resubmit(
                owner, char_id, variant, p_front, p_back, token, left_sq=p_left, right_sq=p_right, provider=prior_provider,
                counter=counter,
            )
        del stored  # 정사각만 쥐고 폴링한다(최대 15분, 512Mi).

        state = None
        done_arn = prior_arn  # 결과를 받은 요청(Tripo 인지 가려 GLB 를 돌린다)
        # 1) 이전 요청이 있으면 먼저 조회 — 완료면 그 결과를 쓴다. unavailable 이 연속 LOST_AFTER 회면
        #    기다리지 않고 바로 새로 제출한다(resubmit 콜백). 그래도 못 받으면 아래 2) 로 넘어간다.
        if prior_arn:
            state = _wait_3d(prior_arn, resubmit=prior_resubmit, owner=owner, char_id=char_id, variant=variant)
            if state["status"] != "Completed":
                state = None  # 실패·시간초과면 새로 제출한다.

        # 2) 이전 결과를 못 쓰면 front/back 으로 새로 제출한다. 제출 직전 claim 재확인.
        if state is None:
            _guard_submit_claim(owner, char_id, variant, token)
            request_arn = submit_3d(front_sq, back_sq, left_square=left_sq, right_square=right_sq, provider=provider)
            done_arn = request_arn
            _charge_3d_request(owner, char_id, variant, request_arn)
            _set_variant_3d_request(
                owner, char_id, variant, {"status": "submitted", "requestArn": request_arn, "error": ""}
            )
            _touch_heartbeat(owner, char_id, variant)
            state = _wait_3d(request_arn, resubmit=resubmit, owner=owner, char_id=char_id, variant=variant)
            if state["status"] != "Completed":
                _set_variant_3d(owner, char_id, variant, {"status": "failed", "error": state.get("error", "")})
                _set_variant(owner, char_id, variant, {"status": "failed", "error": state.get("error", "3D 실패")})
                _refresh_char_status(owner, char_id)
                return

        _copy_3d_result(owner, char_id, variant, version, state, request_arn=done_arn)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        # ClaimLost: 다른 실행이 이 variant 를 가져갔다 — 제출하지 않고 접는다(상태 건드리지 않음).
        if isinstance(exc, VpocError) and exc.kind == "ClaimLost":
            log.info("vnew.retry3d_claim_lost char=%s variant=%s", char_id, variant)
            return
        log.warning("vnew.retry3d_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)
    finally:
        if owns_claim:
            _clear_running(owner, char_id, variant)
            _release_claim(owner, char_id, variant, token)


# ================================================================ 자동 이어 만들기(resume)
#
# 상세 조회·reconcile 때 끊긴 variant 를 발견하면 그 variant 만 이어서 만든다.
# - 끊김 판단: status 가 queued/images/model3d 인데 heartbeatAt 이 VARIANT_STALL_S 넘게
#   갱신 안 됐고(없으면 buildStartedAt 기준) 이 파드에서 실행 중이 아님.
# - 선점: char.json 의 variant claim 을 조건부 쓰기로 잡은 쪽만 실행(여러 파드 중복 방지).
# - resumeCount 가 MAX_RESUME 를 넘으면 그 variant 만 failed(캐릭터는 다른 variant 에 따른다).


def _variant_stale(char: dict, variant: str) -> bool:
    """이 variant 가 진행 중 상태인데 heartbeatAt 이 오래됐는가(끊긴 것으로 본다).

    선점(claim)이 살아 있으면(claimedAt 10분 이내 또는 heartbeat 10분 이내) 끊긴 것으로 보지
    않는다 — 다른 실행이 막 선점·시작한 직후라 자동 재개가 끼어들면 중복 실행(이중 과금)이 된다.
    """
    v = (char.get("variants") or {}).get(variant) or {}
    if v.get("status") not in ("queued", "images", "model3d"):
        return False
    if _claim_fresh(v):
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
        # 선점 성공 후 최신 상태를 다시 읽어 재검증한다 — 방금 끝난 variant 에 자동 재개가 끼어들어
        # resumeCount 를 올리고 중복 실행(이중 제출)하는 것을 막는다. (예) retry3d 실행이 3D 를
        # 끝내(status·model3d=ready) claim 을 막 놓은 틈에, 조회가 오래된 스냅샷으로 들어온 경우.
        # 주의: 방금 _claim_variant 가 새 claim 을 찍었으므로 _variant_stale 로 재검증하면 안 된다
        # (우리 claim 때문에 항상 False). '이미 ready' 만 본다.
        fresh_char, _ = load_char(owner, char_id)
        fresh_v = (fresh_char.get("variants") or {}).get(variant) or {}
        if (fresh_v.get("model3d") or {}).get("status") == "ready" or fresh_v.get("status") == "ready":
            _clear_running(owner, char_id, variant)
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
    - char-fidelity R4.7: VNEW_3D_SIDE_VIEWS 켬이고 그림 3개는 있는데 옆모습 결과가 없고 3D 요청이 없으면 없는 옆모습만 그리고 3D.
    선점 token 이 더는 내 것이 아니면(다른 파드가 가져감) 아무것도 하지 않는다.
    """
    try:
        if not _holds_claim(owner, char_id, variant, token):
            return
        char, _ = load_char(owner, char_id)
        v = (char.get("variants") or {}).get(variant) or {}
        has_images = bool(v.get("sheetKey") and v.get("frontKey") and v.get("backKey"))
        if has_images:
            # char-fidelity R4.7: 옆모습 결과가 없고 3D 요청이 아직 없으면 없는 옆모습만 그리고 3D 로 간다.
            if settings.VNEW_3D_SIDE_VIEWS and _needs_side_views(v) and (char.get("analysis") or {}).get("appearanceEn"):
                _resume_side_views(owner, char_id, variant, version, token)
                _refresh_char_status(owner, char_id)
                return
            # 그림은 그대로 두고 3D 만 이어서(이전 requestArn 재사용 포함). resume 가 claim 수명을
            # 쥐고 있으므로(owns_claim=False) retry3d 는 해제하지 않고, token 으로 제출 직전 가드만 한다.
            run_retry3d(owner, char_id, variant, version, token, owns_claim=False)
            return
        # 그림이 덜 됐으면 분석 결과를 재사용해 처음부터 다시 그린다.
        analysis = char.get("analysis") or {}
        if not analysis.get("appearanceEn"):
            analysis = analyze_sources(owner, char)

            def set_analysis(data: dict) -> None:
                data["analysis"] = analysis

            mutate_char(owner, char_id, set_analysis)
        refs, base_first = _reference_jpegs(owner, char, analysis)
        _build_variant(owner, char_id, variant, version, analysis, refs, token, base_first=base_first)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, VpocError) and exc.kind == "ClaimLost":
            log.info("vnew.resume_claim_lost char=%s variant=%s", char_id, variant)
            return
        log.warning("vnew.resume_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)
    finally:
        _clear_running(owner, char_id, variant)
        _release_claim(owner, char_id, variant, token)


def _needs_side_views(v: dict) -> bool:
    """이어 만들기가 옆모습부터 그려야 하는가(design §10.5): viewCheck 에 left·right 중 없는 것이 있고, 3D 요청이 없고,
    3D 가 ready 가 아니다. 3D 요청이 이미 있으면 그 요청부터 조회한다(옆모습을 그리지 않는다)."""
    check = v.get("viewCheck") if isinstance(v.get("viewCheck"), dict) else {}
    m3d = v.get("model3d") or {}
    missing = any(view not in check for view in char_fidelity.SIDES)
    return missing and not m3d.get("requestArn") and m3d.get("status") != "ready"


def _resume_side_views(owner: str, char_id: str, variant: str, version: int, token: str) -> None:
    """끊긴 variant 의 없는 옆모습만 그리고 3D 로 간다(design §10.5). 예외는 run_resume_variant 의 except 가 처리한다.

    이미 끝난 옆모습은 저장된 그림(leftKey·rightKey)을 읽는다. warning 은 지금 warning + 옆모습 결과(viewCheck 전체)로
    다시 만든다(같은 문구는 한 번). 그림마다 제출 직전 선점 확인·하트비트는 _draw_side_views 가 한다.
    """
    char, _ = load_char(owner, char_id)
    v = (char.get("variants") or {}).get(variant) or {}
    analysis = char.get("analysis") or {}
    front = STORE.get_bytes(v["frontKey"])
    back = STORE.get_bytes(v["backKey"])
    sheet = STORE.get_bytes(v["sheetKey"])
    refs, base_first = _reference_jpegs(owner, char, analysis)
    base_ref = refs[0] if refs else None
    del refs  # 기준 사진 한 장만 쓴다. 3D 폴링(최대 15분) 동안 나머지 업로드 바이트를 쥐지 않는다(512Mi).
    done = dict(v["viewCheck"]) if isinstance(v.get("viewCheck"), dict) else {}
    need = tuple(view for view in char_fidelity.SIDES if view not in done)
    _set_variant(owner, char_id, variant, {"status": "images", "heartbeatAt": now_iso()})
    drawn = _draw_side_views(
        owner, char_id, variant, version, analysis, front, back, sheet, base_ref,
        base_first=base_first, token=token, need=need,
    )
    statuses = {view: done[view] for view in char_fidelity.SIDES if view in done}
    statuses.update({view: result["status"] for view, result in drawn.items()})
    images = {}
    for view in char_fidelity.SIDES:
        images[view] = drawn[view]["jpeg"] if view in drawn else _stored_side_view(v, view, char_id, variant, "resume")
    old = [text for text in str(v.get("warning") or "").split(" / ") if text]
    warning = " / ".join(char_fidelity.merge_warnings(old, char_fidelity.side_warnings(statuses)))
    _set_variant(owner, char_id, variant, {"status": "model3d", "warning": warning, "heartbeatAt": now_iso()})
    _build_3d_from_images(owner, char_id, variant, version, front, back, token, left=images["left"], right=images["right"])


# ================================================================ 그림체만 다시(variant rebuild)
#
# 그 variant 만 처음부터(분석 결과 재사용, 없으면 분석부터). 진행 중이면 409.


def start_rebuild_variant(owner: str, char_id: str, variant: str, extra_prompt_ko: str | None = None) -> int:
    """그 그림체만 처음부터 다시 만들기. 진행 중이면 409. version 반환(스레드 시작).

    시작 전에(mutate 안에서) 선점(claim)·heartbeat 를 지금으로 찍는다 — 직후 조회 자동 재개가
    '끊긴 작업'으로 오인해 두 번째 실행을 띄우지 못하게 한다.

    extra_prompt_ko: None 이면 이전 extraPromptKo 유지, "" 면 삭제, 문자열이면 저장(≤ 최대 길이).
    """
    if variant not in settings.VARIANTS:
        raise VpocError("vnew.rebuild", "BadVariant", "알 수 없는 그림체입니다.", 404)
    if _is_running(owner, char_id, variant):
        raise VpocError("vnew.rebuild", "AlreadyBuilding", "이 그림체를 이미 만들고 있습니다.", 409)

    token = secrets.token_hex(6)

    def mutator(data: dict) -> int:
        v = data.setdefault("variants", {}).setdefault(variant, _empty_variant())
        # 진행 중(끊기지 않은)이거나 선점이 살아 있으면 409.
        if (v.get("status") in ("queued", "images", "model3d") and not _variant_stale(data, variant)) or _claim_fresh(v):
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
        # 선점·heartbeat 를 '시작 전에' 잡는다(이후 백그라운드는 자기 claim 일 때만 진행).
        _claim_variant_dict(fresh, token)
        data["variants"][variant] = fresh
        return version

    updated, version = mutate_char(owner, char_id, mutator)
    # 캐릭터 전체 status 롤업(다시 building 이 될 수 있다).
    _refresh_char_status(owner, char_id)
    _mark_running(owner, char_id, variant)

    thread = threading.Thread(
        target=run_rebuild_variant,
        args=(owner, char_id, variant, version, token),
        daemon=True,
        name=f"vnew-rebuild-{char_id}-{variant}",
    )
    thread.start()
    return version


def run_rebuild_variant(owner: str, char_id: str, variant: str, version: int, token: str = "") -> None:
    """그림체 하나를 처음부터(백그라운드). 분석 결과가 없으면 분석부터 하고 재사용한다.

    선점 token 을 넘겨받으면 그 claim 을 잡고 있을 때만 진행하고 finally 에서 해제한다.
    """
    if token and not _holds_claim(owner, char_id, variant, token):
        return
    try:
        char, _ = load_char(owner, char_id)
        _touch_heartbeat(owner, char_id, variant)
        analysis = char.get("analysis") or {}
        if not analysis.get("appearanceEn"):
            analysis = analyze_sources(owner, char)

            def set_analysis(data: dict) -> None:
                data["analysis"] = analysis

            mutate_char(owner, char_id, set_analysis)
        refs, base_first = _reference_jpegs(owner, char, analysis)
        _build_variant(owner, char_id, variant, version, analysis, refs, token, base_first=base_first)
        _refresh_char_status(owner, char_id)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, VpocError) and exc.kind == "ClaimLost":
            log.info("vnew.rebuild_claim_lost char=%s variant=%s", char_id, variant)
            return
        log.warning("vnew.rebuild_variant_failed char=%s variant=%s err=%s", char_id, variant, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_pending_3d(owner, char_id, variant, message)
        _set_variant(owner, char_id, variant, {"status": "failed", "error": message})
        _refresh_char_status(owner, char_id)
    finally:
        _clear_running(owner, char_id, variant)
        if token:
            _release_claim(owner, char_id, variant, token)


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


# ================================================================ 추가 복장(outfits)
#
# 기본 복장 위에 더 입히는 옷차림. analysis.outfitLayersEn 를 auto 후보로 넣되 그리지는 않는다
# (비용). 사용자가 추가할 수 있고, build 때만 두 그림체 각각 정면·후면을 그린다(3D 없음).


def _find_outfit(char: dict, outfit_id: str) -> dict | None:
    for o in char.get("outfits") or []:
        if o.get("id") == outfit_id:
            return o
    return None


def sync_auto_outfits(owner: str, char_id: str, analysis: dict) -> None:
    """outfitLayersEn 를 source:'auto' 추가 복장 후보로 넣는다(그림은 그리지 않는다).

    이미 같은 promptEn 의 auto 후보가 있으면 다시 넣지 않는다(빌드 때마다 중복 누적 방지).
    사용자 추가(user)·auto 가 합쳐 MAX_OUTFITS 를 넘으면 더 넣지 않는다.
    """
    layers = _parse_outfit_layers(analysis.get("outfitLayersEn"))
    if not layers:
        return
    # char-fidelity(design §9.1): 켬 + 충실 분석이면 후보는 '사진 속 옷'(kind photo) — 그 옷이 보인 그림·인물·소품을 함께 둔다.
    photo = settings.VNEW_CHAR_FIDELITY and _is_fidelity_analysis(analysis)
    photo_fields = _photo_outfit_fields(analysis) if photo else []

    def mutator(data: dict) -> None:
        outfits = data.setdefault("outfits", [])
        existing = {str(o.get("promptEn") or "").strip().lower() for o in outfits}
        for index, layer in enumerate(layers):
            prompt_en = layer["promptEn"]
            if prompt_en.strip().lower() in existing:
                continue
            if len(outfits) >= settings.MAX_OUTFITS:
                break
            outfit = _empty_outfit(layer["labelKo"], prompt_en, "auto")
            if photo:
                outfit.update(photo_fields[index])
            outfits.append(outfit)
            existing.add(prompt_en.strip().lower())

    try:
        mutate_char(owner, char_id, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.sync_auto_outfits_failed char=%s err=%s", char_id, str(exc)[:200])


def _photo_outfit_fields(analysis: dict) -> list[dict]:
    """충실 분석 outfitLayersEn 마다 사진 속 옷 필드(kind·sourceFileId·whereEn·sidedItems). _parse_outfit_layers 와 같은 순서.

    sidedItems 는 그 옷 그림에서 본 analysis.sidedItems(sided 순번) 중 모양이 맞는 것만 itemEn·labelKo·partEn·side 로 둔다.
    """
    raw_layers = analysis.get("outfitLayersEn") if isinstance(analysis.get("outfitLayersEn"), list) else []
    sided = analysis.get("sidedItems") if isinstance(analysis.get("sidedItems"), list) else []
    out: list[dict] = []
    for layer in raw_layers:
        if not _parse_outfit_layers([layer]):
            continue
        layer = layer if isinstance(layer, dict) else {}
        picked = [
            sided[i] for i in (layer.get("sided") if isinstance(layer.get("sided"), list) else [])
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(sided) and _valid_sided_item(sided[i])
        ]
        out.append({
            "kind": "photo",
            "sourceFileId": str(layer.get("sourceFileId") or ""),
            "whereEn": char_fidelity.clean_line(layer.get("whereEn"), 60),
            "sidedItems": [
                {"itemEn": item["itemEn"], "labelKo": str(item.get("labelKo") or ""), "partEn": item["partEn"], "side": item["side"]}
                for item in picked
            ],
        })
    return out


def _translate_outfit_prompt(owner: str, char: dict, prompt_ko: str) -> str:
    """사용자가 넣은 한국어 복장 설명을 Astra(실패 시 Claude)로 영어 한 줄로 바꾼다.

    실패하면 입력을 그대로 돌려준다(빈 결과보다 원문이 낫다).
    """
    prompt_ko = (prompt_ko or "").strip()
    if not prompt_ko:
        return ""
    blocks = [
        {
            "text": (
                "Translate this Korean clothing description into ONE short English phrase that "
                "names only the garment(s) to add on top of a base outfit. Answer by calling "
                "emit_outfit_prompt once. Korean: " + prompt_ko
            )
        }
    ]
    tool = {
        "name": "emit_outfit_prompt",
        "description": "Return the English clothing phrase.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"promptEn": {"type": "string"}},
                "required": ["promptEn"],
            }
        },
    }
    for model in (settings.ASTRA_MODEL, settings.ANALYZE_FALLBACK_MODEL):
        try:
            payload, _usage = vpoc_llm._converse(  # noqa: SLF001
                system_text="You translate Korean clothing descriptions into short English phrases.",
                blocks=blocks,
                tool=tool,
                max_tokens=200,
                where="vnew.outfit_translate",
                model=model,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.outfit_translate.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        prompt_en = str(payload.get("promptEn") or "").strip()
        if prompt_en:
            return prompt_en
    return prompt_ko


def add_outfit(owner: str, char_id: str, label_ko: str, prompt_ko: str) -> dict:
    """사용자 추가 복장 1벌 등록. promptKo 는 Astra 가 promptEn 으로 바꾼다(그림은 안 그린다).

    반환: 새로 넣은 outfit dict.
    """
    char, _ = load_char(owner, char_id)
    label_ko = (label_ko or "").strip()[: settings.MAX_OUTFIT_LABEL_LEN]
    prompt_ko = (prompt_ko or "").strip()[: settings.MAX_OUTFIT_PROMPT_LEN]
    if not label_ko and not prompt_ko:
        raise VpocError("vnew.add_outfit", "NoOutfit", "복장 이름이나 설명을 넣어 주세요.", 400)
    if len(char.get("outfits") or []) >= settings.MAX_OUTFITS:
        raise VpocError(
            "vnew.add_outfit", "TooManyOutfits",
            f"추가 복장은 최대 {settings.MAX_OUTFITS}개까지 만들 수 있습니다.", 400,
        )
    prompt_en = _translate_outfit_prompt(owner, char, prompt_ko) if prompt_ko else ""
    outfit = _empty_outfit(label_ko, prompt_en, "user")

    def mutator(data: dict) -> None:
        data.setdefault("outfits", []).append(outfit)

    mutate_char(owner, char_id, mutator)
    return outfit


def delete_outfit(owner: str, char_id: str, outfit_id: str) -> None:
    """추가 복장 1벌 삭제(char.json 에서만 뺀다 — 이미지 파일은 지우지 않는다)."""

    def mutator(data: dict) -> None:
        data["outfits"] = [o for o in (data.get("outfits") or []) if o.get("id") != outfit_id]

    mutate_char(owner, char_id, mutator)


def start_build_outfit(owner: str, char_id: str, outfit_id: str) -> None:
    """추가 복장 이미지 만들기(백그라운드 스레드 시작). 두 그림체 각각 정면·후면.

    기본 정면·후면이 없으면(아직 안 만듦) 400. 진행 중이면 409.
    """
    char, _ = load_char(owner, char_id)
    outfit = _find_outfit(char, outfit_id)
    if outfit is None:
        raise VpocError("vnew.build_outfit", "NotFound", "추가 복장을 찾을 수 없습니다.", 404)
    # 기본 정면·후면이 하나라도 있어야 참고로 쓸 수 있다.
    have_base = any(
        (char.get("variants") or {}).get(v, {}).get("frontKey")
        for v in settings.VARIANTS
    )
    if not have_base:
        raise VpocError(
            "vnew.build_outfit", "NoBase",
            "먼저 캐릭터(기본 복장)를 만들어야 추가 복장을 그릴 수 있습니다.", 400,
        )

    def mutator(data: dict) -> None:
        o = _find_outfit(data, outfit_id)
        if o is None:
            return
        for v in settings.VARIANTS:
            ov = o.setdefault("variants", {}).setdefault(v, _empty_outfit_variant())
            if ov.get("status") == "images":
                raise VpocError("vnew.build_outfit", "AlreadyBuilding", "이미 만들고 있습니다.", 409)
            ov["status"] = "images"
            ov["error"] = ""

    mutate_char(owner, char_id, mutator)

    thread = threading.Thread(
        target=run_build_outfit,
        args=(owner, char_id, outfit_id),
        daemon=True,
        name=f"vnew-outfit-{char_id}-{outfit_id}",
    )
    thread.start()


def run_build_outfit(owner: str, char_id: str, outfit_id: str) -> None:
    """추가 복장 이미지(두 그림체 각각 정면·후면)를 그린다(백그라운드).

    각 그림체의 기본 정면·후면을 참고로 'same person, same base outfit, plus {promptEn}' 정면·후면을
    그린다. 3D 는 만들지 않는다(기본 복장 3D 하나만). 원장은 Seedream 장당.

    char-fidelity(design §9.2): 켬이고 kind photo(사진 속 옷)이면 '기본 복장 대신 이 한 벌 그대로'(_photo_outfit_prompt)로
    그린다. 정면 참고 [기본 정면, 원본 그림], 후면 참고 [방금 정면, 원본 그림, 기본 후면]. 원본 그림을 못 읽으면 원본 없이.
    """
    try:
        char, _ = load_char(owner, char_id)
        outfit = _find_outfit(char, outfit_id)
        if outfit is None:
            return
        analysis = char.get("analysis") or {}
        prompt_en = str(outfit.get("promptEn") or "").strip()
        photo = settings.VNEW_CHAR_FIDELITY and outfit.get("kind") == "photo"
        source = _photo_outfit_source(owner, char, outfit) if photo else None
        for variant in settings.VARIANTS:
            try:
                base_v = (char.get("variants") or {}).get(variant) or {}
                base_front_key = base_v.get("frontKey") or ""
                base_back_key = base_v.get("backKey") or ""
                if not base_front_key:
                    _set_outfit_variant(owner, char_id, outfit_id, variant,
                                        {"status": "failed", "error": "기본 정면 이미지가 없습니다."})
                    continue
                base_front = STORE.get_bytes(base_front_key)
                base_back = STORE.get_bytes(base_back_key) if base_back_key else None

                # 정면 — 기본 정면을 첫 참고로.
                source_no = 2 if source is not None else None
                if photo:
                    front_refs = [base_front] + ([source] if source is not None else [])
                    front_prompt = _photo_outfit_prompt(
                        variant, analysis, outfit, "front", source_no=source_no, base_back_no=None)
                else:
                    front_refs = [base_front]
                    front_prompt = _outfit_prompt(variant, analysis, prompt_en, "front")
                front = _seedream(front_prompt, front_refs, size=settings.FRONT_SIZE)
                _charge_seedream(owner, char_id, variant, "outfit-front")
                fk = outfit_front_key(owner, char_id, outfit_id, variant)
                STORE.put_bytes(fk, front, "image/jpeg")

                # 후면 — 방금 만든 정면 + 기본 후면을 참고로.
                back_refs = [front]
                if photo and source is not None:
                    back_refs.append(source)
                if base_back is not None:
                    back_refs.append(base_back)
                if photo:
                    back_no = len(back_refs) if base_back is not None else None
                    back_prompt = _photo_outfit_prompt(
                        variant, analysis, outfit, "back", source_no=source_no, base_back_no=back_no)
                else:
                    back_prompt = _outfit_prompt(variant, analysis, prompt_en, "back")
                back = _seedream(back_prompt, back_refs, size=settings.BACK_SIZE)
                _charge_seedream(owner, char_id, variant, "outfit-back")
                bk = outfit_back_key(owner, char_id, outfit_id, variant)
                STORE.put_bytes(bk, back, "image/jpeg")

                _set_outfit_variant(owner, char_id, outfit_id, variant,
                                    {"status": "ready", "frontKey": fk, "backKey": bk, "error": ""})
            except Exception as exc:  # noqa: BLE001
                message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
                log.warning("vnew.build_outfit_variant_failed char=%s outfit=%s variant=%s err=%s",
                            char_id, outfit_id, variant, message)
                _set_outfit_variant(owner, char_id, outfit_id, variant,
                                    {"status": "failed", "error": message})
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.build_outfit_failed char=%s outfit=%s err=%s", char_id, outfit_id, str(exc)[:300])


def _photo_outfit_source(owner: str, char: dict, outfit: dict) -> bytes | None:
    """사진 속 옷의 원본 그림 바이트. 그 그림이 업로드 목록에 없거나(지운 그림) 읽지 못하면 None(원본 없이 그린다)."""
    file_id = str(outfit.get("sourceFileId") or "")
    known = {src.get("fileId") for src in (char.get("sources") or []) if isinstance(src, dict)}
    if file_id and file_id in known:
        try:
            return source_jpeg(owner, char["id"], file_id)
        except Exception as exc:  # noqa: BLE001 — 원본이 없어도 그림은 그린다
            log.warning("vnew.build_outfit.source_missing char=%s outfit=%s err=%s", char.get("id"), outfit.get("id"),
                        type(exc).__name__)
            return None
    log.warning("vnew.build_outfit.source_missing char=%s outfit=%s err=not_in_sources", char.get("id"), outfit.get("id"))
    return None


def _set_outfit_variant(owner: str, char_id: str, outfit_id: str, variant: str, patch: dict) -> None:
    def mutator(data: dict) -> None:
        o = _find_outfit(data, outfit_id)
        if o is None:
            return
        ov = o.setdefault("variants", {}).setdefault(variant, _empty_outfit_variant())
        ov.update(patch)

    mutate_char(owner, char_id, mutator)


def _outfit_prompt(variant: str, analysis: dict, prompt_en: str, view: str) -> str:
    """추가 복장 정면/후면 프롬프트. '같은 사람·같은 기본 복장 + {promptEn}'."""
    who = _who(analysis)
    base = _base_outfit(analysis) or "the base outfit"
    extra = (prompt_en or "").strip() or "the extra garment described"
    side = "BACK view" if view == "back" else "front view"
    person = (
        "the same real Korean actor as in the reference image"
        if variant == "real"
        else "the same single person as in the reference image"
    )
    head = (
        "Single full-body " + side + " photograph of " + person + ". "
        if variant == "real"
        else "Single full-body " + side + " of " + person + ". "
    )
    # char-fidelity(design §6.5): 켬 + 충실 분석에 정한 소품이 있으면 덧입힘 문장 바로 뒤에 좌우 문장만 더한다(끔이면 "").
    items = _sided_items(analysis)
    sided = char_fidelity.side_sentences(items, "back" if view == "back" else "front", _pronoun_of(analysis)) if items else ""
    prompt = (
        head
        + who
        + f" Same person, same base outfit ({base}), plus {extra}. "
        + (sided + " " if sided else "")
        + "Standing straight in a relaxed A-pose, whole body from head to shoes visible, centered, "
        "plain white background, no shadow. Draw exactly ONE person, no extra figures. "
    )
    if variant == "real":
        prompt += "Real photograph, natural skin texture, soft studio lighting. No text."
    else:
        prompt += _style_clause(analysis) + " No text."
    return prompt


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
    char, casting_out = _casting_detail(owner, char_id, char)
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
        # char-fidelity(design §11.1, R4.9): 옆모습 그림은 키가 있을 때만 leftUrl·rightUrl 을 둔다(3D 에서 뺀 쪽은 키가 "").
        # 3D 이름표(T2b·T3): model3d.endpoint 는 저장돼 있을 때만(Tripo 요청) 준다. 옛 데이터는 응답이 지금과 같다(cf-r).
        for side in char_fidelity.SIDES:
            if v.get(f"{side}Key"):
                variants_out[variant][f"{side}Url"] = _presign(v.get(f"{side}Key"))
        if isinstance(model3d.get("endpoint"), str) and model3d.get("endpoint"):
            variants_out[variant]["model3d"]["endpoint"] = model3d["endpoint"]
    analysis = char.get("analysis") or {}
    detail = {
        "id": char["id"],
        "name": char.get("name") or "",
        "status": char.get("status"),
        "version": char.get("version", 0),
        "createdAt": char.get("createdAt"),
        "updatedAt": char.get("updatedAt"),
        "buildError": char.get("buildError") or "",
        "sources": char.get("sources") or [],
        "analysis": analysis,
        # 영상 쪽·화면에서 바로 쓰도록 기본 복장을 꺼내 준다(outfitEn 과 같다).
        "baseOutfitEn": _base_outfit(analysis),
        "extraPromptKo": char.get("extraPromptKo") or "",
        "variants": variants_out,
        "costumes": char.get("costumes") or [],
        "outfits": _outfits_view(owner, char),
        "casting": casting_out,
    }
    # char-fidelity(design §11.1, R5.1): 두 설정 중 하나라도 켜져 있을 때만 features 를 둔다. 둘 다 끔이면 키가 없다(R0.2).
    if settings.VNEW_CHAR_FIDELITY or settings.VNEW_3D_SIDE_VIEWS:
        detail["features"] = {
            "charFidelity": bool(settings.VNEW_CHAR_FIDELITY),
            "sideViews": bool(settings.VNEW_3D_SIDE_VIEWS),
        }
    return detail


def _casting_detail(owner: str, char_id: str, char: dict) -> tuple[dict, dict]:
    """실사 캐스팅(vnew-live-fx B): 멈춘 뽑기를 이어 조회하고(casting.reconcile) 보기를 만든다(casting.view).

    이어하기는 설정과 상관없이 한다(진행 중 뽑기는 끝까지, R0.5). 이어하기가 실패해도 상세 조회는 막지 않는다.
    """
    from . import casting  # noqa: PLC0415 — casting 이 characters 를 읽으므로 늦게 import

    try:
        char = casting.reconcile(owner, char_id, char)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.casting.reconcile_failed char=%s err=%s", char_id, str(exc)[:200])
    return char, casting.view(char)


def _outfits_view(owner: str, char: dict) -> list[dict]:
    """추가 복장 목록을 화면·영상용으로 presigned URL 과 함께 담는다."""
    out: list[dict] = []
    for o in char.get("outfits") or []:
        variants_out: dict = {}
        for variant in settings.VARIANTS:
            ov = (o.get("variants") or {}).get(variant) or _empty_outfit_variant()
            variants_out[variant] = {
                "status": ov.get("status") or "queued",
                "error": ov.get("error") or "",
                "frontUrl": _presign(ov.get("frontKey")),
                "backUrl": _presign(ov.get("backKey")),
            }
        out.append({
            "id": o.get("id"),
            "labelKo": o.get("labelKo") or "",
            "promptEn": o.get("promptEn") or "",
            "source": o.get("source") or "user",
            "variants": variants_out,
        })
        # char-fidelity(design §11.1): 저장된 kind(사진 속 옷 "photo")가 있을 때만 둔다. 옛 후보·사용자 복장은 지금 응답 그대로.
        if o.get("kind"):
            out[-1]["kind"] = o.get("kind")
    return out


# ================================================================ GLB 스트리밍


def glb_bytes(owner: str, char_id: str, variant: str) -> bytes:
    """해당 variant 의 GLB 바이트. 없으면 404."""
    char, _ = load_char(owner, char_id)
    v = (char.get("variants") or {}).get(variant) or {}
    glb_key = (v.get("model3d") or {}).get("glbKey") or ""
    if not glb_key:
        raise VpocError("vnew.glb", "NotFound", "3D 모델이 아직 없습니다.", 404)
    return STORE.get_bytes(glb_key)
