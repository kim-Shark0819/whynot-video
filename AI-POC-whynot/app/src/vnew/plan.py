"""vnew 영상만들기_신규 — 계획 저장 · 대본 분석 · 콘티 작성(설계자 2026-10-20).

웹툰 이미지만 넣으면 AI 가 ② 대본(대사·상황 로그)을 읽고 그것으로 ③ 콘티(드라마 컷)를 쓴다.
영상 생성 전까지의 ①입력 ②대본 ③콘티 세 단계만 여기서 다룬다.

재사용(수정 금지): vpoc.llm(_converse 도구 JSON · 모델 체인 · broker) · vpoc.images(normalize_upload ·
tiles_for_analysis · jpeg_for_reference) · vpoc.store(S3) · vpoc.ark(choices · video_options) ·
vpoc.ledger(원장) · vnew.characters(load_char). routes.py 는 이 함수들을 HTTP 로 감싼다.

외부 호출(LLM)은 아래 래퍼 하나로 감싼다(검사가 가짜로 바꿔 끼운다): _analyze_script · _analyze_conti ·
_analyze_fx(③ 기호·분위기 채우기, vnew-live-fx 묶음 A).
STORE 는 vpoc.store.STORE 를 쓴다(검사가 모듈 변수로 바꿔 끼운다).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from ..vpoc import ark as vpoc_ark
from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE

from . import characters
from . import fx as vnew_fx
from . import prompts

log = logging.getLogger("vpoc")

# ================================================================ 상수

# 영상 길이(초) 범위. 모델별 상한과 별개로 UI 상한은 60. 최소 3초 — 최종 생성 모델
# (Kling O3 video-to-video edit)이 입력 영상 3~15초만 받으므로 그보다 짧으면 구간을 못 만든다.
MIN_TOTAL_SECONDS = 3
MAX_TOTAL_SECONDS = 60
# 녹화·생성 구간 최소 길이(초). 최종 모델이 3초 미만 입력을 거절한다. compute_segments 는 모든
# 구간을 이 값 이상, 최대 길이 이하로 만든다.
MIN_SEGMENT_SECONDS = 3
# 업로드 웹툰 이미지 최대 장수.
MAX_IMAGES = 40
# 대본 분석에 모델로 보낼 타일 상한. 넘으면 균등 선택하되 순서 유지.
MAX_SCRIPT_TILES = 40
# 콘티 최소 컷 수.
MIN_CUTS = 5
# 장면 의상·목소리 사용자 수정(castOverrides) 글자 상한(설계자 2026-10-26, 064).
MAX_CAST_COSTUME_LEN = 200
MAX_CAST_VOICE_LEN = 80
# running task 가 이 시간을 넘게 그대로면 멈춘 것으로 본다(파드 재시작 대비, 30분).
TASK_STALL_S = 1800
# 계획 작업(plan.task) 중 단계 상태(plan[name].status)를 같이 두는 작업. 취소는 none, 멈춤은 failed 로 정리한다.
# fxFill = ③ 기호·분위기 채우기(vnew-live-fx 묶음 A).
_TASK_STEPS = ("script", "conti", "fxFill")


# ================================================================ 키 규칙
#
# IRSA 가 users/* 만 허용하므로 전부 그 아래에 둔다. owner 는 safe_user_prefix(username).
# vpoc1 과 겹치지 않게 .../video/{pid}/vnew/ 아래에 둔다.


def _root(owner: str, pid: str) -> str:
    return f"users/{owner}/video/{pid}/vnew"


def plan_key(owner: str, pid: str) -> str:
    return f"{_root(owner, pid)}/plan.json"


def upload_key(owner: str, pid: str, file_id: str) -> str:
    return f"{_root(owner, pid)}/uploads/{file_id}.jpg"


# ================================================================ 자연 정렬


_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """파일 이름 자연 정렬 키. 1.png < 2.png < 10.png, 숫자 없는 이름은 뒤로.

    숫자가 있으면 (0, [조각...]) 로 앞에, 없으면 (1, 소문자이름) 으로 뒤에 둔다.
    숫자 조각은 정수로 비교해 2 < 10 이 되게 한다.
    """
    text = str(name or "")
    parts = _NUM_RE.split(text)
    has_number = any(part.isdigit() for part in parts)
    if not has_number:
        return (1, text.lower())
    key: list = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        elif part:
            key.append((1, part.lower()))
    return (0, key)


def natural_sort_order(images: list[dict]) -> list[str]:
    """images 를 이름 자연 정렬한 fileId 순서."""
    ordered = sorted(images, key=lambda item: natural_key(item.get("name") or item.get("fileId") or ""))
    return [item["fileId"] for item in ordered]


# ================================================================ 저장 레이어


def _plan_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"plan_{stamp}_{secrets.token_hex(3)}"


def _empty_task() -> dict:
    return {"name": None, "status": "idle", "startedAt": None, "id": None}


def new_plan() -> dict:
    """빈 계획. 프로젝트당 하나."""
    return {
        "planId": _plan_id(),
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "revision": 0,
        "input": {
            "images": [],  # [{fileId,name,width,height}]
            "order": [],  # [fileId...]
            "totalSeconds": 15,
            "videoModel": default_previz_model(),
            "notes": "",
            "characters": [],  # [{charId, roleName}]
            # 장면 의상·목소리 사용자 수정(설계자 2026-10-26, 064). name → {costume?, voice?}.
            # 있으면 원작 패널 자동 읽기(previz.castNotes)보다 우선한다.
            "castOverrides": {},
        },
        "script": {
            "status": "none",  # none|running|ready|failed
            "error": "",
            "model": "",
            "setting": {},
            "entries": [],
        },
        "conti": {
            "status": "none",
            "error": "",
            "model": "",
            "cuts": [],
            "segments": [],
        },
        # ③ 콘티의 장소마다 만드는 공간 디테일 시트(설계자 2026-10-21). 콘티를 만들면 목록만
        # 갱신하고 자동으로 그리지는 않는다(비용). locations.py 가 이 목록을 다룬다.
        "locations": [],
        # ③→④ 사이 프리비즈(3D 장면 설계 JSON, 설계자 2026-10-21). previz.py 가 다룬다.
        "previz": {
            "status": "none",
            "error": "",
            "model": "",
            "revision": 0,
            "scenes": [],
            "approved": False,
            "recordings": {},
            "task": _empty_task(),
        },
        "task": _empty_task(),
    }


def read_plan(owner: str, pid: str) -> tuple[dict | None, str]:
    return STORE.read_json(plan_key(owner, pid))


def load_or_create(owner: str, pid: str) -> tuple[dict, str]:
    """계획을 읽는다. 없으면 빈 계획을 만들어 저장하고 돌려준다."""
    data, etag = read_plan(owner, pid)
    if data is not None:
        return data, etag
    plan = new_plan()
    try:
        etag = STORE.write_json(plan_key(owner, pid), plan, create_only=True)
        return plan, etag
    except VpocError as exc:
        if exc.kind != "PreconditionFailed":
            raise
        # 경합: 다른 요청이 먼저 만들었다. 다시 읽는다.
        data, etag = read_plan(owner, pid)
        if data is None:
            raise
        return data, etag


def load_plan(owner: str, pid: str) -> tuple[dict, str]:
    data, etag = read_plan(owner, pid)
    if data is None:
        raise VpocError("vnew.plan.load", "NotFound", "계획을 찾을 수 없습니다.", 404)
    return data, etag


def save_plan(owner: str, pid: str, data: dict, etag: str = "") -> str:
    data["updatedAt"] = now_iso()
    return STORE.write_json(plan_key(owner, pid), data, etag=etag)


def mutate_plan(owner: str, pid: str, mutator, *, attempts: int = 5):
    """읽고-고치고-조건부쓰기. 경합이면 다시 읽어 재시도(vpoc store 패턴)."""
    last: BaseException | None = None
    for _ in range(attempts):
        data, etag = load_plan(owner, pid)
        result = mutator(data)
        try:
            save_plan(owner, pid, data, etag)
            return data, result
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise
            last = exc
    raise last if last else RuntimeError("mutate_plan 재시도 실패")


# ================================================================ 업로드


def add_uploads(owner: str, pid: str, files: list[tuple[bytes, str]]) -> list[dict]:
    """웹툰 이미지 여러 장 추가. normalize_upload 로 정규화하고 images·order 에 붙인다.

    files = [(raw_bytes, name)]. 장당 MAX_UPLOAD_BYTES(normalize_upload 가 검사), 최대 MAX_IMAGES 장.
    """
    plan, _ = load_plan(owner, pid)
    existing = len(plan.get("input", {}).get("images") or [])
    if existing + len(files) > MAX_IMAGES:
        raise VpocError(
            "vnew.plan.uploads",
            "TooManyImages",
            f"이미지는 최대 {MAX_IMAGES}장까지 올릴 수 있습니다 (현재 {existing}장).",
            400,
        )
    added: list[dict] = []
    for raw, name in files:
        if not raw:
            continue
        jpeg, width, height = vpoc_images.normalize_upload(raw, name)
        file_id = f"f_{secrets.token_hex(4)}"
        STORE.put_bytes(upload_key(owner, pid, file_id), jpeg, "image/jpeg")
        added.append({"fileId": file_id, "name": name or file_id, "width": width, "height": height})

    def mutator(data: dict) -> None:
        inp = data.setdefault("input", {})
        inp.setdefault("images", []).extend(added)
        order = inp.setdefault("order", [])
        order.extend(item["fileId"] for item in added)

    mutate_plan(owner, pid, mutator)
    return added


def delete_upload(owner: str, pid: str, file_id: str) -> None:
    """이미지 1장 제거. images·order 에서 뺀다. S3 파일은 지우지 않는다(기록)."""

    def mutator(data: dict) -> None:
        inp = data.setdefault("input", {})
        inp["images"] = [s for s in (inp.get("images") or []) if s.get("fileId") != file_id]
        inp["order"] = [fid for fid in (inp.get("order") or []) if fid != file_id]

    mutate_plan(owner, pid, mutator)


def sort_natural(owner: str, pid: str) -> list[str]:
    """order 를 파일 이름 자연 정렬 결과로 갱신하고 그 order 를 돌려준다."""

    def mutator(data: dict) -> list[str]:
        inp = data.setdefault("input", {})
        order = natural_sort_order(inp.get("images") or [])
        inp["order"] = order
        return order

    _, order = mutate_plan(owner, pid, mutator)
    return order


# ================================================================ ① 입력 검증


def available_models() -> list[dict]:
    """고를 수 있는 영상 모델 목록(ark.choices 중 available).

    각 모델에 realStartImage 를 붙인다(설계자 2026-10-20): Seedance(byteplus)는 실존 인물 필터로
    실사 시작 이미지를 거절하므로 false, 그 밖(Luma=bedrock · fal)은 true. (옛 흐름 호환 — 회귀 방지.)
    """
    models = []
    for item in vpoc_ark.choices():
        if not item.get("available"):
            continue
        row = dict(item)
        row["realStartImage"] = _real_start_image(item)
        models.append(row)
    return models


def _real_start_image(model_item: dict) -> bool:
    """이 모델이 실사 인물 시작 이미지를 받는지. byteplus(Seedance)만 false."""
    return str(model_item.get("provider") or "") != "byteplus"


def default_video_model() -> str:
    """ark.choices 기준 기본 영상 모델(옛 흐름 호환). realStartImage=true 인 available 중 첫 번째,
    없으면 ark.DEFAULT_MODEL(설계자 2026-10-20).

    결과는 ark.resolve_model 을 거친다 — 기존 ARK Seedance 가 꺼져 있으면 fal Seedance(등록 시) 또는
    Luma 로 바뀐다(설계자 2026-10-26). 꺼져 있을 때 choices 는 이미 byteplus 를 빼므로 보통 그대로지만,
    옛 기본값이 ARK Seedance 로 떨어지는 경로까지 한 함수로 막는다.
    """
    for item in available_models():
        if item.get("realStartImage"):
            return vpoc_ark.resolve_model(str(item.get("id")))
    return vpoc_ark.resolve_model(vpoc_ark.DEFAULT_MODEL)


def _model_ids() -> list[str]:
    return [str(item.get("id")) for item in available_models()]


# ---------------------------------------------------------------- ① 프리비즈 영상 모델(설계자 2026-10-22)
#
# 영상만들기_신규 ①의 영상 모델은 프리비즈 방식 전용이다(영상 입력을 받는 모델만). 목록 = 기본
# (Kling O3 edit, 첫 번째·추천) + 등록된 previzCapable fal 모델. 옛 available_models(ark.choices 기반)
# 는 회귀를 위해 그대로 두고, 아래 함수들을 ① 화면·검증·생성에서 쓴다.


def vnew_settings_module():
    """vnew.settings 를 늦게 import(순환 참조 피하기)."""
    from . import settings as vnew_settings  # noqa: PLC0415

    return vnew_settings


def default_previz_model() -> str:
    """새 계획·되돌림의 기본 프리비즈 영상 모델 id = "fal:<VNEW_PREVIZ_EDIT_MODEL>"."""
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    return vpoc_settings.FAL_ID_PREFIX + str(vnew_settings_module().VNEW_PREVIZ_EDIT_MODEL or "")


def _family_face_of(model_id: str) -> str:
    """fal Seedance 계열 짝(r2v) id 면 그 얼굴(i2v) id, 아니면 빈 문자열(073 R16.1, 작업 8).

    vpoc settings.FAL_SEEDANCE_FAMILY(얼굴 → 짝)를 거꾸로 찾는다. 얼굴 id·계열 밖·fal 이 아닌 id 는 빈 값.
    ① 목록은 이 얼굴 id 로 vpoc 과 같은 이름(ark.label)을 만든다.
    """
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    endpoint = vpoc_settings.fal_endpoint_id(model_id)
    if not endpoint:
        return ""
    for face, partner in vpoc_settings.FAL_SEEDANCE_FAMILY.items():
        if partner == endpoint:
            return vpoc_settings.FAL_ID_PREFIX + face
    return ""


def _previz_send_resolution(caps: dict) -> str:
    """vnew 가 이 프리비즈 모델에 실제로 보내는 해상도. 모르면 빈 문자열(073 R16.4-16.5, D18·D19).

    - 통 테이크 모델(caps videoListField — generate._gen_mode 가 "previz-take" 로 고른다):
      generate._new_take_gen 이 gen.resolution = VNEW_TAKE_RESOLUTION 으로 두고, _do_submit_take 가 그 값을
      _submit_take 본문 "resolution" 에 늘 명시해 보낸다(fal 스키마 기본값을 쓰지 않는다). ④ 견적(estimate)과
      원장(_charge_take → _take_usd)도 같은 값이다.
    - 컷 단위 모델: _submit_previz_edit 이 resolution 을 보내지 않아 fal 기본 해상도로 만든다 — 모른다("").
    - 설정 값이 원장 공식(generate._take_usd 의 TAKE_RESOLUTION_WH)에 없는 해상도(예: 540p·4k)면 표시
      (shot_usd)와 원장이 다른 픽셀로 계산되므로 모르는 것으로 본다("") — 화면은 "단가 미정".
    """
    if (caps or {}).get("videoListField"):
        vnew_settings = vnew_settings_module()
        resolution = str(vnew_settings.VNEW_TAKE_RESOLUTION or "")
        return resolution if resolution in vnew_settings.TAKE_RESOLUTION_WH else ""
    return ""


def _previz_price_row(model_id: str) -> dict:
    """프리비즈 모델 한 행의 단가·참고 방식 정보(화면 표시용).

    - fal 단가 unit 이 "seconds" 면 그 초당 단가(지금 그대로).
    - 기본 모델(Kling O3)은 단가를 모르면 설정 기본 단가(초당, 지금 그대로).
    - 토큰 단가("1000 tokens" 등)이고 vnew 가 보내는 해상도를 알면(통 테이크 모델) 초당 단가 =
      vpoc settings.shot_usd(id, <전송 해상도>, 1) — vpoc 과 같은 토큰 공식(073 R16.4, 작업 8). 이때만
      priceResolution(예: "480p")을 더한다. 해상도를 모르는 컷 단위 토큰 모델은 "단가 미정"(R16.5).
    """
    from ..vpoc import fal as vpoc_fal  # noqa: PLC0415
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    vnew_settings = vnew_settings_module()
    endpoint = str(model_id).split(":", 1)[-1]
    # 단가: 등록 모델이면 pricing_for, 아니면 fal pricing(endpoint). 초당 USD 만 표시한다.
    unit_price = 0.0
    price_known = False
    item = vpoc_fal.find(model_id)
    price = vpoc_fal.pricing_for(model_id) if item else vpoc_fal.pricing(endpoint)
    unit = str((price or {}).get("unit") or "").lower()
    if price and unit == "seconds":
        unit_price = float(price.get("unitPrice") or 0.0)
        price_known = unit_price > 0
    if not price_known and model_id == default_previz_model():
        # 기본 모델은 설정 기본 단가(초당)를 쓴다.
        unit_price = float(vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S)
        price_known = True
    if not price_known and price and vpoc_settings._fal_token_multiplier(unit) is not None:
        # 토큰 단가(Seedance 2.5 등, 073 R16.4): 실제 전송 해상도로 1초 금액을 vpoc 공식(shot_usd)으로 낸다.
        resolution = _previz_send_resolution((item or {}).get("caps") or {})
        if resolution:
            per_second = float(vpoc_settings.shot_usd(model_id, resolution, 1))
            if per_second > 0:
                return {"usdPerSecond": round(per_second, 4), "priceKnown": True, "priceResolution": resolution}
    return {"usdPerSecond": round(unit_price, 4), "priceKnown": price_known}


def _ref_mode(caps: dict) -> str:
    """참고 이미지 방식: elements / imageList / none(화면 표시용)."""
    if caps.get("elementsField"):
        return "elements"
    if caps.get("imageListField"):
        return "imageList"
    return "none"


def previz_models() -> list[dict]:
    """① 프리비즈 영상 모델 목록(설계자 2026-10-22).

    목록 = 기본(Kling O3 edit, 항상 첫 번째, recommended) + fal 등록 모델 중 previzCapable 인 것.
    각 행: id·label·recommended·previzCapable·refMode(elements/imageList)·usdPerSecond·priceKnown·available·reason
    (+ 토큰 단가를 실제 전송 해상도로 계산한 행은 priceResolution).

    fal Seedance 계열(073 R16.1-16.3, 설계자 2026-10-28 "신규화면도 개발에 적용해"): 얼굴(i2v)은 영상 입력을
    받지 않아 늘 "프리비즈 미지원"이므로 숨기고, 짝(r2v) 한 줄을 vpoc ② 와 같은 이름(ark.label(<얼굴>),
    "Seedance 2.5 (fal)")으로 보인다. 계열 밖 fal 모델은 지금 그대로 `fal · <label>`.
    """
    from ..vpoc import fal as vpoc_fal  # noqa: PLC0415
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    default_id = default_previz_model()
    models: list[dict] = []
    # 1. 기본 모델(항상 첫 번째). caps 는 등록돼 있으면 쓰고, 아니면 elements 방식으로 가정.
    default_item = vpoc_fal.find(default_id)
    default_caps = (default_item or {}).get("caps") or {}
    default_ref = _ref_mode(default_caps) if default_caps else "elements"
    row = {
        "id": default_id,
        "label": "Kling O3 (추천)",
        "recommended": True,
        "previzCapable": True,
        "refMode": default_ref,
        "available": True,
        "reason": "",
    }
    row.update(_previz_price_row(default_id))
    models.append(row)

    # 2. 등록된 fal 모델(기본과 중복 제외). previzCapable 가 아니면 '프리비즈 미지원' 으로 회색 표시.
    for item in vpoc_fal.registered():
        model_id = str(item.get("id") or "")
        if not model_id or model_id == default_id:
            continue
        # 073 R16.2(D17): Seedance 계열 얼굴(i2v)은 숨긴다 — 짝(r2v) 한 줄로 보인다.
        if vpoc_settings.fal_family_partner(model_id):
            continue
        caps = item.get("caps") or {}
        capable = bool(caps.get("previzCapable"))
        reason = "프리비즈 미지원(영상 입력을 받지 않는 모델)"
        if capable:
            reason = vpoc_fal.unavailable_reason(model_id)
        # 073 R16.1: 계열 짝은 vpoc 과 같은 이름 함수(ark.label(<얼굴>))로, 그 밖은 지금 그대로 `fal · <label>`.
        face = _family_face_of(model_id)
        label = (
            vpoc_ark.label(face)
            if face
            else "fal · " + str(item.get("label") or item.get("endpointId") or model_id)
        )
        row = {
            "id": model_id,
            "label": label,
            "recommended": False,
            "previzCapable": capable,
            "refMode": _ref_mode(caps),
            "available": capable and not reason,
            "reason": reason,
        }
        row.update(_previz_price_row(model_id))
        models.append(row)
    return models


def _previz_model_ids() -> set[str]:
    """고를 수 있는(검증 통과) 프리비즈 모델 id 집합: 기본 + available previzCapable fal."""
    ids = {default_previz_model()}
    for item in previz_models():
        if item.get("available") and item.get("previzCapable"):
            ids.add(str(item.get("id")))
    return ids


def update_input(owner: str, pid: str, patch: dict) -> dict:
    """① 입력 저장. order·totalSeconds·videoModel·notes·characters 를 검증해 반영한다.

    - totalSeconds: 3~60 정수
    - videoModel: 기본 프리비즈 모델 또는 등록된 previzCapable fal 모델만 허용. 모르는 모델을 보내면
      400 BadVideoModel, 빈 값은 기본으로 되돌린다(설계자 2026-10-22). 옛 ARK Seedance 값(seedance-2-0/2-5)이
      프리비즈 모델로 바뀌지 않으면 400 대신 기본 프리비즈 모델로 저장한다(073 R13.1).
    - order: images 의 fileId 순열(빠지거나 모르는 fileId 가 있으면 거절)
    - characters: [{charId, roleName}] — charId 는 캐릭터 만들기에서 만든 것
    """
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    model_ids = _previz_model_ids()

    def mutator(data: dict) -> None:
        inp = data.setdefault("input", {})
        images = inp.get("images") or []
        image_ids = {item["fileId"] for item in images}

        if "totalSeconds" in patch:
            value = patch.get("totalSeconds")
            if not isinstance(value, int) or isinstance(value, bool) or not (
                MIN_TOTAL_SECONDS <= value <= MAX_TOTAL_SECONDS
            ):
                raise VpocError(
                    "vnew.plan.input",
                    "BadTotalSeconds",
                    f"영상 길이는 {MIN_TOTAL_SECONDS}~{MAX_TOTAL_SECONDS}초 정수여야 합니다.",
                    400,
                )
            inp["totalSeconds"] = value

        if "videoModel" in patch:
            # 기존 ARK Seedance(seedance-2-0/2-5)가 저장/전송돼도 ark.resolve_model 로 먼저 바꾼다
            # (꺼져 있으면 fal Seedance 또는 Luma). 그 뒤 프리비즈 모델 목록으로 검증한다(설계자 2026-10-26).
            raw_model = str(patch.get("videoModel") or "")
            model = vpoc_ark.resolve_model(raw_model)
            if model and model not in model_ids:
                if raw_model in vpoc_settings.ARK_MODEL_IDS:
                    # 옛 ARK 값은 프리비즈 모델이 될 수 없다(073 R13.1, 리뷰 O-11). 400 대신 기본 프리비즈
                    # 모델로 저장해 옛 값이 남은 계획도 ① 입력을 저장할 수 있게 한다. 그 밖의 모르는 값은 400.
                    reset_to = default_previz_model()
                    log.info("vnew.plan.video_model_reset from=%s to=%s", raw_model, reset_to)
                    model = reset_to
                else:
                    raise VpocError(
                        "vnew.plan.input",
                        "BadVideoModel",
                        "고를 수 없는 영상 모델입니다. 기본 모델 또는 프리비즈를 지원하는 fal 모델만 쓸 수 있습니다.",
                        400,
                    )
            inp["videoModel"] = model or default_previz_model()

        if "notes" in patch:
            inp["notes"] = str(patch.get("notes") or "")

        if "order" in patch:
            order = list(patch.get("order") or [])
            if sorted(order) != sorted(image_ids) or len(order) != len(set(order)):
                raise VpocError(
                    "vnew.plan.input",
                    "BadOrder",
                    "순서 목록이 올린 이미지와 맞지 않습니다.",
                    400,
                )
            inp["order"] = order

        if "characters" in patch:
            chars = []
            for item in patch.get("characters") or []:
                char_id = str((item or {}).get("charId") or "").strip()
                role = str((item or {}).get("roleName") or "").strip()
                if not char_id:
                    continue
                chars.append({"charId": char_id, "roleName": role})
            inp["characters"] = chars

        if "castOverrides" in patch:
            raw = patch.get("castOverrides") or {}
            if not isinstance(raw, dict):
                raise VpocError(
                    "vnew.plan.input",
                    "BadCastOverrides",
                    "장면 의상·목소리 수정 값이 올바르지 않습니다.",
                    400,
                )
            overrides: dict = {}
            for name, item in raw.items():
                name_key = str(name or "").strip()
                if not name_key or not isinstance(item, dict):
                    continue
                entry: dict = {}
                costume = str(item.get("costume") or "").strip()
                voice = str(item.get("voice") or "").strip()
                if costume:
                    entry["costume"] = costume[:MAX_CAST_COSTUME_LEN]
                if voice:
                    entry["voice"] = voice[:MAX_CAST_VOICE_LEN]
                if entry:
                    overrides[name_key] = entry
            inp["castOverrides"] = overrides

    updated, _ = mutate_plan(owner, pid, mutator)
    return updated


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 두 함수를 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _analyze_script(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 대본 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=prompts.SCRIPT_SYSTEM,
        blocks=blocks,
        tool=prompts.EMIT_SCRIPT,
        max_tokens=8000,
        where="vnew.script",
        model=model,
    )


def _analyze_conti(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 콘티 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001
        system_text=prompts.CONTI_SYSTEM,
        blocks=blocks,
        tool=prompts.EMIT_CONTI,
        max_tokens=8000,
        where="vnew.conti",
        model=model,
    )


def _analyze_fx(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 기호·분위기 JSON 을 받는다(vnew-live-fx A.3). (payload, usage{..model}).

    max_tokens 8000: 채울 컷당 출력 ≈ 140토큰 × 40컷 + 도구 앞 글 ≈ 500. 8000 ≥ 4096 이라 잘림 재시도 없이
    다음 모델로 간다(llm-top-model-first 규칙).
    """
    return vpoc_llm._converse(  # noqa: SLF001
        system_text=prompts.FX_SYSTEM,
        blocks=blocks,
        tool=prompts.EMIT_FX,
        max_tokens=8000,
        where="vnew.conti.fx",
        model=model,
    )


# ================================================================ 타일 고르기


def ordered_tiles(owner: str, pid: str, order: list[str]) -> tuple[list[bytes], int, int]:
    """order 순서대로 이미지를 타일로 잘라 모은다. 상한을 넘으면 균등 선택하되 순서 유지.

    반환: (타일 bytes 목록(<=MAX_SCRIPT_TILES), 전체 타일 수, 고른 타일 수).
    세로 웹툰이라 tiles_for_analysis 가 1,568px 단위로 자른다.
    """
    all_tiles: list[bytes] = []
    for file_id in order:
        try:
            jpeg = STORE.get_bytes(upload_key(owner, pid, file_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.tiles.missing file=%s err=%s", file_id, str(exc)[:150])
            continue
        all_tiles.extend(vpoc_images.tiles_for_analysis(jpeg))

    total = len(all_tiles)
    if total <= MAX_SCRIPT_TILES:
        return all_tiles, total, total
    # 균등 선택(순서 유지). 인덱스를 고르게 뽑아 원래 순서대로 남긴다.
    step = total / MAX_SCRIPT_TILES
    indices = sorted({min(total - 1, int(i * step)) for i in range(MAX_SCRIPT_TILES)})
    picked = [all_tiles[i] for i in indices]
    return picked, total, len(picked)


def _roster_text(owner: str, chars: list[dict]) -> str:
    """캐릭터 roleName + appearance(캐릭터 만들기에서 분석한 외형)를 프롬프트용 문단으로."""
    if not chars:
        return "(연결된 캐릭터 없음)"
    lines = []
    for item in chars:
        role = str(item.get("roleName") or "").strip() or "(역할명 없음)"
        char_id = str(item.get("charId") or "")
        appearance = ""
        try:
            char, _ = characters.load_char(owner, char_id)
            appearance = str((char.get("analysis") or {}).get("appearanceEn") or "").strip()
        except Exception:  # noqa: BLE001
            appearance = ""
        lines.append(f"- {role}" + (f" ({appearance})" if appearance else ""))
    return "\n".join(lines)


# ================================================================ ② 대본(script)


def run_script(owner: str, pid: str, task_id: str) -> None:
    """대본 분석(백그라운드 스레드). order 순서대로 타일을 모델에 보내 대사·상황 로그를 받는다.

    task_id 가 현재 task.id 와 다르면(취소·재실행) 결과를 버린다.
    """
    try:
        plan, _ = load_plan(owner, pid)
        inp = plan.get("input") or {}
        order = list(inp.get("order") or [])
        if not order:
            _fail_step(owner, pid, "script", task_id, "먼저 웹툰 이미지를 올려 주세요.")
            return

        tiles, total, picked = ordered_tiles(owner, pid, order)
        if not tiles:
            _fail_step(owner, pid, "script", task_id, "분석할 이미지를 찾지 못했습니다.")
            return

        notes = str(inp.get("notes") or "").strip()
        roster = _roster_text(owner, inp.get("characters") or [])
        header = (
            f"다음은 웹툰 컷을 순서대로 자른 타일 {total}장 중 {picked}장입니다"
            + (" (균등 선택, 순서 유지)." if picked != total else ".")
            + "\n\n[부가 설명]\n"
            + (notes or "(없음)")
            + "\n\n[등장인물(역할명 · 외형)]\n"
            + roster
            + "\n\n컷 순서대로 대사(OCR)·상황·인물 위치·감정을 적고, 배경·장소는 그려진 그대로 두세요."
        )
        blocks: list[dict] = [{"text": header}]
        for jpeg in tiles:
            blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

        payload, usage = _analyze_script(blocks, model=_script_model())
        model = str(usage.get("model") or _script_model())
        _charge(owner, pid, plan.get("planId") or "", usage, model)

        script = _normalize_script(payload)
        script["status"] = "ready"
        script["error"] = ""
        script["model"] = model

        _commit_step(owner, pid, "script", task_id, script)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.run_script.failed pid=%s err=%s", pid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_step(owner, pid, "script", task_id, message)


def _normalize_script(payload: dict) -> dict:
    """모델 출력을 script 모양으로 정리한다(빈 필드 채우기 · panels 정수화).

    Fable 계열은 중첩 객체(setting)를 JSON 문자열로 주거나, 그 필드를 최상위로 올리거나,
    entries·characters 를 문자열로 주기도 한다. 그런 변형을 받아 정상 모양으로 되돌린다.
    """
    # setting 이 JSON 문자열이면 풀고, dict 가 아니면 {}.
    setting = _loads_if_str(payload.get("setting"))
    if not isinstance(setting, dict):
        setting = {}
    setting = dict(setting)

    # setting 에 없는 키는 payload 최상위의 같은 이름·별칭에서 끌어온다.
    _fill_setting_from_top(setting, payload)

    setting.setdefault("locationKo", "")
    setting.setdefault("locationEn", "")
    setting.setdefault("enclosed", False)
    setting.setdefault("timeKo", "")
    setting.setdefault("moodKo", "")
    setting.setdefault("layoutKo", "")
    setting.setdefault("layoutEn", "")
    setting["enclosed"] = _as_bool(setting.get("enclosed"))

    # entries 가 문자열이면 푼다.
    raw_entries = _loads_if_str(payload.get("entries"))
    if not isinstance(raw_entries, list):
        raw_entries = []

    # 그래도 setting.locationKo/En 이 비면 entries 첫 항목의 장소로 채운다.
    _fill_setting_location_from_entries(setting, raw_entries)

    entries = []
    for index, item in enumerate(raw_entries):
        item = _loads_if_str(item)
        if not isinstance(item, dict):
            continue
        # 각 entry 의 characters 가 문자열이면 푼다.
        raw_chars = _loads_if_str(item.get("characters"))
        if not isinstance(raw_chars, list):
            raw_chars = []
        chars = []
        for char in raw_chars:
            char = _loads_if_str(char)
            if not isinstance(char, dict):
                continue
            chars.append(
                {
                    "name": str(char.get("name") or ""),
                    "positionKo": str(char.get("positionKo") or ""),
                    "positionEn": str(char.get("positionEn") or ""),
                    "actionKo": str(char.get("actionKo") or ""),
                }
            )
        # 항목 장소. 웹툰이 장소를 바꾸지 않으면 setting 과 같다. 모델이 비우면 setting 값으로 본다
        # (옛 데이터에 필드가 없는 것과 같은 처리).
        location_ko = str(item.get("locationKo") or "").strip() or str(setting.get("locationKo") or "")
        location_en = str(item.get("locationEn") or "").strip() or str(setting.get("locationEn") or "")
        entries.append(
            {
                "id": str(item.get("id") or f"e{index + 1}"),
                "panels": [int(p) for p in (item.get("panels") or []) if _is_int(p)],
                "speaker": str(item.get("speaker") or ""),
                "lineKo": str(item.get("lineKo") or ""),
                "situationKo": str(item.get("situationKo") or ""),
                "emotionKo": str(item.get("emotionKo") or ""),
                "locationKo": location_ko,
                "locationEn": location_en,
                "characters": chars,
            }
        )
    # 정리 뒤에도 setting 이 비면(모든 값 빈/거짓) 모델이 준 모양을 남긴다(값은 남기지 않음).
    if not _setting_has_content(setting):
        log.warning(
            "vnew.script.setting_empty top_keys=%s setting_type=%s",
            sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            type(payload.get("setting")).__name__ if isinstance(payload, dict) else "n/a",
        )

    return {"setting": setting, "entries": entries}


def update_script(owner: str, pid: str, patch: dict) -> dict:
    """사용자 수정(setting · entries 추가·삭제·수정). 수정하면 revision 을 올린다."""

    def mutator(data: dict) -> None:
        script = data.setdefault("script", {})
        if "setting" in patch and isinstance(patch["setting"], dict):
            script["setting"] = patch["setting"]
        if "entries" in patch and isinstance(patch["entries"], list):
            script["entries"] = patch["entries"]
        if script.get("status") in ("none", "failed"):
            script["status"] = "ready"
        data["revision"] = int(data.get("revision") or 0) + 1
        _unapprove_previz(data)

    updated, _ = mutate_plan(owner, pid, mutator)
    return updated


# ================================================================ ③ 콘티(conti)


def run_conti(owner: str, pid: str, task_id: str) -> None:
    """콘티 작성(백그라운드 스레드). script + input 으로 드라마 컷을 받아 초를 맞추고 segments 를 계산한다.

    VNEW_FX 가 켜져 있으면(작업 시작 때 한 번 읽는다) 저장 전에 기호·분위기 채우기를 1회 부르고 결과를 같은 저장에
    넣는다(fxFill). 채우기가 실패해도 콘티는 기호 없이 저장한다(vnew-live-fx RA2.1). 끄면 지금 동작.
    """
    try:
        fx_on = bool(vnew_settings_module().VNEW_FX)
        plan, _ = load_plan(owner, pid)
        script = plan.get("script") or {}
        if script.get("status") != "ready" or not (script.get("entries")):
            _fail_step(owner, pid, "conti", task_id, "먼저 대본을 만들어 주세요.")
            return
        inp = plan.get("input") or {}
        total_seconds = int(inp.get("totalSeconds") or 15)
        if total_seconds < MIN_TOTAL_SECONDS:
            _fail_step(
                owner, pid, "conti", task_id,
                f"영상 길이는 {MIN_TOTAL_SECONDS}초 이상이어야 합니다(최종 생성 모델이 3초 미만 입력을 받지 못합니다).",
            )
            return
        notes = str(inp.get("notes") or "").strip()
        roster = _roster_text(owner, inp.get("characters") or [])

        blocks = [{"text": _conti_prompt(script, total_seconds, notes, roster)}]
        payload, usage = _analyze_conti(blocks, model=_conti_model())
        model = str(usage.get("model") or _conti_model())
        _charge(owner, pid, plan.get("planId") or "", usage, model)

        cuts = _normalize_cuts(payload, script)
        # 최소 5컷 보정: 모델이 모자라게 주면 거절(재요청) 후 그래도 모자라면 쪼개서 채운다.
        if len(cuts) < MIN_CUTS:
            cuts = _ensure_min_cuts(blocks, cuts, script, model_hint=model)

        # 초 맞춤 + 세그먼트(모델 최대 길이 기준, 컷 경계에서).
        video_model = str(inp.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
        cuts = fit_seconds(cuts, total_seconds)
        cuts, segments = compute_segments(cuts, _max_segment_seconds(video_model))

        # 기호·분위기 채우기(켬일 때만, 실패해도 콘티는 저장). 결과 상태는 콘티와 같은 저장에 넣는다.
        extra = None
        if fx_on:
            cuts, fx_state = _fill_new_conti(owner, pid, plan, cuts)
            extra = {"fxFill": fx_state}

        conti = {"status": "ready", "error": "", "model": model, "cuts": cuts, "segments": segments}
        _commit_step(owner, pid, "conti", task_id, conti, extra=extra)
        # 콘티가 바뀌면 프리비즈 승인을 해제한다(콘티·장면이 바뀌면 다시 확인·승인).
        _unapprove_previz_after_conti(owner, pid)
        # 콘티가 정해지면 장소 목록만 갱신한다(자동으로 그리지는 않는다 — 비용). 같은 이름이면 유지.
        _sync_locations_after_conti(owner, pid)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.run_conti.failed pid=%s err=%s", pid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail_step(owner, pid, "conti", task_id, message)


def _sync_locations_after_conti(owner: str, pid: str) -> None:
    """콘티가 정해진 뒤 장소 목록을 갱신한다(그림은 그리지 않는다). 실패해도 콘티는 이미 저장됐다."""
    from . import locations  # noqa: PLC0415 — 순환 import 피하기

    try:
        mutate_plan(owner, pid, locations.sync_locations)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.conti.sync_locations_failed pid=%s err=%s", pid, str(exc)[:200])


def _unapprove_previz(data: dict) -> None:
    """콘티·대본·장면을 고치면 프리비즈 승인을 해제한다. previz 를 늦게 import 해 순환 참조를 피한다."""
    from . import previz  # noqa: PLC0415

    try:
        previz.unapprove(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.unapprove_previz_failed err=%s", str(exc)[:200])


def _unapprove_previz_after_conti(owner: str, pid: str) -> None:
    """콘티 재생성 뒤 프리비즈 승인 해제(저장까지). 실패해도 콘티는 이미 저장됐다."""
    try:
        mutate_plan(owner, pid, _unapprove_previz)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.conti.unapprove_previz_failed pid=%s err=%s", pid, str(exc)[:200])



def _conti_prompt(script: dict, total_seconds: int, notes: str, roster: str) -> str:
    """콘티 요청 문단. 대본(setting + entries)·총 길이·부가 설명·캐릭터를 담는다."""
    setting = script.get("setting") or {}
    lines = [
        f"[영상 길이] {total_seconds}초 — 모든 컷의 seconds 합이 이 값이 되도록 합니다.",
        f"[컷 수] 최소 {MIN_CUTS}컷. 상황이 많으면 더 늘립니다(대략 컷당 3~8초).",
        "",
        "[대본 — 배경]",
        f"장소: {setting.get('locationKo', '')} / {setting.get('locationEn', '')}",
        f"공간: {'닫힌 방' if setting.get('enclosed') else '열린 공간'}",
        f"시간: {setting.get('timeKo', '')} / 분위기: {setting.get('moodKo', '')}",
        f"공간 배치: {setting.get('layoutKo', '')} / {setting.get('layoutEn', '')}",
        "",
        "[대본 — 대사·상황(컷 순서)]",
    ]
    for index, entry in enumerate(script.get("entries") or [], start=1):
        chars = ", ".join(
            f"{c.get('name', '')}({c.get('positionKo', '')})"
            for c in (entry.get("characters") or [])
        )
        panels = ",".join(str(p) for p in (entry.get("panels") or []))
        lines.append(
            f"{index}. [panels {panels}] 장소={entry.get('locationEn', '')}"
            f" / 화자={entry.get('speaker', '')} / 대사=\"{entry.get('lineKo', '')}\""
            f" / 상황={entry.get('situationKo', '')} / 감정={entry.get('emotionKo', '')}"
            + (f" / 인물={chars}" if chars else "")
        )
    lines.extend(
        [
            "",
            "[부가 설명]",
            notes or "(없음)",
            "",
            "[등장인물(역할명 · 외형)]",
            roster,
            "",
            "각 컷의 panels 로 어느 대본 항목을 맡는지 밝히고, 그 컷 locationEn 은 그 항목들의 장소와"
            " 같게 하세요(웹툰이 장소를 바꾸면 그 장소를 따릅니다). 인물 위치는 대본을 이어받으세요.",
            "promptEn 은 그 컷을 영상 모델에 줄 영어 문장입니다(인물은 역할명으로 부릅니다).",
        ]
    )
    return "\n".join(lines)


def _overlapping_entry_locations(cut_panels: list[int], entries: list[dict]) -> list[str]:
    """이 컷의 panels 와 겹치는 script entries 의 locationEn 목록(겹치는 순서대로)."""
    cut_set = set(cut_panels or [])
    locations: list[str] = []
    for entry in entries:
        entry_panels = set(entry.get("panels") or [])
        if cut_set & entry_panels:
            loc = str(entry.get("locationEn") or "").strip()
            locations.append(loc)
    return locations


def _cut_location(model_location: str, cut_panels: list[int], script: dict) -> str:
    """컷의 locationEn 을 '그 컷이 맡은 웹툰 컷의 장소'로 정한다(설계자 2026-10-20).

    - 겹치는 entries 의 locationEn 중 하나와 같으면 모델 값 그대로(웹툰에 있는 장소).
    - 아니면(모델이 지어낸 장소) 겹치는 entries 중 첫 번째의 locationEn 으로 되돌린다.
    - 겹치는 entry 가 없으면 setting.locationEn.
    """
    entries = script.get("entries") or []
    setting_location = str((script.get("setting") or {}).get("locationEn") or "").strip()
    overlap = _overlapping_entry_locations(cut_panels, entries)
    model_location = (model_location or "").strip()
    if not overlap:
        return model_location or setting_location
    if model_location and model_location in overlap:
        return model_location
    # 첫 번째 겹치는 entry 의 장소(비어 있으면 setting).
    return overlap[0] or setting_location


def _normalize_cuts(payload: dict, script: dict) -> list[dict]:
    """모델 출력 cuts 를 정리한다. locationEn 은 그 컷이 맡은 웹툰 컷의 장소로 보정, seconds 는 양수."""
    cuts = []
    raw_cuts = _loads_if_str(payload.get("cuts"))
    if not isinstance(raw_cuts, list):
        raw_cuts = []
    for index, item in enumerate(raw_cuts):
        item = _loads_if_str(item)
        if not isinstance(item, dict):
            continue
        chars = []
        raw_chars = _loads_if_str(item.get("characters"))
        if not isinstance(raw_chars, list):
            raw_chars = []
        for char in raw_chars:
            char = _loads_if_str(char)
            if not isinstance(char, dict):
                continue
            chars.append(
                {
                    "name": str(char.get("name") or ""),
                    "positionEn": str(char.get("positionEn") or ""),
                    "actionEn": str(char.get("actionEn") or ""),
                }
            )
        try:
            seconds = float(item.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds <= 0:
            seconds = 1.0
        cut_panels = [int(p) for p in (item.get("panels") or []) if _is_int(p)]
        cuts.append(
            {
                "id": str(item.get("id") or f"c{index + 1}"),
                "no": index + 1,
                "seconds": seconds,
                "panels": cut_panels,
                "shotKo": str(item.get("shotKo") or ""),
                "cameraEn": str(item.get("cameraEn") or ""),
                # 모델이 웹툰에 없는 장소를 지어내면 그 컷이 맡은 웹툰 컷의 장소로 되돌린다.
                "locationEn": _cut_location(str(item.get("locationEn") or ""), cut_panels, script),
                "characters": chars,
                "dialogueKo": str(item.get("dialogueKo") or ""),
                "situationKo": str(item.get("situationKo") or ""),
                "promptEn": str(item.get("promptEn") or ""),
            }
        )
    return cuts


def _ensure_min_cuts(blocks: list[dict], cuts: list[dict], script: dict, *, model_hint: str) -> list[dict]:
    """컷이 MIN_CUTS 보다 적으면 한 번 재요청하고, 그래도 적으면 긴 컷을 쪼개 채운다."""
    retry = list(blocks) + [
        {
            "text": (
                f"이전 응답의 컷이 {len(cuts)}개로 부족합니다. 컷을 최소 {MIN_CUTS}개로 다시 쓰세요"
                " (상황을 더 잘게 나누되 대본 순서와 장소를 지킵니다)."
            )
        }
    ]
    try:
        payload2, _ = _analyze_conti(retry, model=model_hint)
        cuts2 = _normalize_cuts(payload2, script)
        if len(cuts2) >= len(cuts):
            cuts = cuts2
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.conti.retry_failed err=%s", str(exc)[:200])

    # 그래도 모자라면 가장 긴 컷을 반으로 쪼개 MIN_CUTS 를 채운다(결과 >= 5 보장).
    cuts = [dict(cut) for cut in cuts]
    while 0 < len(cuts) < MIN_CUTS:
        longest = max(range(len(cuts)), key=lambda i: cuts[i]["seconds"])
        src = cuts[longest]
        half = src["seconds"] / 2.0
        first = dict(src, seconds=half)
        second = dict(src, seconds=src["seconds"] - half, id=f"{src['id']}b")
        cuts[longest : longest + 1] = [first, second]
    if not cuts:
        raise VpocError("vnew.conti", "NoCuts", "콘티 컷을 만들지 못했습니다.", 502)
    for no, cut in enumerate(cuts, start=1):
        cut["no"] = no
    return cuts


# ================================================================ 초 맞춤 · 세그먼트


def fit_seconds(cuts: list[dict], total_seconds: int) -> list[dict]:
    """컷 seconds 의 비율을 지키며 정수 초로 배분해 합이 total_seconds 가 되게 한다(최소 1초).

    1) 비율대로 내림(floor), 각 컷 최소 1초.
    2) 모자란 초를 소수부가 큰 컷부터 1초씩 더한다.
    3) total 이 컷 수보다 작으면(최소 1초 보장이 깨지면) 가능한 만큼만 맞춘다.
    """
    cuts = [dict(cut) for cut in cuts]
    count = len(cuts)
    if count == 0:
        return cuts
    total = int(total_seconds)
    if total < count:
        # 컷마다 1초도 못 주는 경우: 앞에서부터 1초씩 주고 나머지는 0 이 아니라 1 로 둔다(최소 1초).
        for index, cut in enumerate(cuts):
            cut["seconds"] = 1
        return cuts

    raw_total = sum(float(cut["seconds"]) for cut in cuts) or float(count)
    # 1차 배분: 비율 * total, 내림, 최소 1.
    floored: list[int] = []
    remainders: list[tuple[float, int]] = []
    for index, cut in enumerate(cuts):
        share = float(cut["seconds"]) / raw_total * total
        base = max(1, int(share))
        floored.append(base)
        remainders.append((share - int(share), index))

    assigned = sum(floored)
    diff = total - assigned
    if diff > 0:
        # 소수부가 큰 컷부터 1초씩 더한다.
        for _, index in sorted(remainders, key=lambda item: item[0], reverse=True):
            if diff <= 0:
                break
            floored[index] += 1
            diff -= 1
    elif diff < 0:
        # 과배분(최소 1 보장 때문): 가장 큰 컷부터 1초씩 뺀다(1 밑으로는 안 내린다).
        order = sorted(range(count), key=lambda i: floored[i], reverse=True)
        pos = 0
        while diff < 0 and any(floored[i] > 1 for i in range(count)):
            index = order[pos % count]
            if floored[index] > 1:
                floored[index] -= 1
                diff += 1
            pos += 1

    for index, cut in enumerate(cuts):
        cut["seconds"] = int(floored[index])
    return cuts


def _split_cut_to_max(cut: dict, max_seconds: int) -> list[dict]:
    """한 컷이 max_seconds 보다 길면 max_seconds 이하 조각들로 나눈다(정수 초)."""
    seconds = int(cut["seconds"])
    if seconds <= max_seconds:
        return [cut]
    pieces: list[dict] = []
    remaining = seconds
    part = 0
    while remaining > 0:
        take = min(max_seconds, remaining)
        part += 1
        piece = dict(cut, seconds=take, id=f"{cut['id']}-{part}")
        pieces.append(piece)
        remaining -= take
    return pieces


def compute_segments(cuts: list[dict], max_seconds: int) -> tuple[list[dict], list[dict]]:
    """컷을 구간으로 나눈다. **모든 구간은 MIN_SEGMENT_SECONDS 이상·max_seconds 이하**(설계자 2026-10-21).

    최종 생성 모델(Kling O3 video-to-video edit)이 입력 영상 3~15초만 받으므로, 2초짜리 구간을
    만들면 안 된다. 규칙:
    - **컷 경계를 우선**한다(한 컷은 한 구간 안에 통째로 들어가려 한다 — 가능하면 쪼개지 않는다).
    - 짧은 구간(<MIN)은 앞(없으면 뒤) 구간과 합친다.
    - 합쳐서 max 를 넘으면 그 범위를 둘로 다시 나누되 **둘 다 MIN 이상**이 되게 한다. 컷 경계로 안 되면
      컷을 쪼갠다.
    반환: (조각 반영된 cuts, segments[{id,cutIds,seconds}]).

    totalSeconds 가 MIN 미만이면 콘티에서 거절되므로(run_conti) 여기서는 total>=MIN 을 가정한다.
    """
    max_seconds = max(MIN_SEGMENT_SECONDS, int(max_seconds))

    # 1) 최대 길이보다 긴 컷을 먼저 쪼갠다.
    expanded: list[dict] = []
    for cut in cuts:
        expanded.extend(_split_cut_to_max(cut, max_seconds))
    total = sum(int(c["seconds"]) for c in expanded)
    if total <= 0:
        return expanded, []

    # 2) 컷 경계에서 구간을 모은다(<=max). 구간은 그 안의 컷 조각 목록으로 들고 있는다.
    groups: list[list[dict]] = []  # 각 구간 = 컷 조각 리스트
    current: list[dict] = []
    current_s = 0
    for cut in expanded:
        s = int(cut["seconds"])
        if current and current_s + s > max_seconds:
            groups.append(current)
            current = []
            current_s = 0
        current.append(cut)
        current_s += s
    if current:
        groups.append(current)

    # 3) 짧은 구간(<MIN)을 이웃과 합치고, 합쳐서 max 를 넘으면 둘로 다시 나눈다(둘 다 MIN 이상).
    groups = _repair_short_segments(groups, max_seconds)

    # 4) 구간·컷 조각을 평탄화해 반환.
    rebalanced: list[dict] = []
    segments: list[dict] = []
    for index, group in enumerate(groups):
        ids: list[str] = []
        secs = 0
        for cut in group:
            rebalanced.append(cut)
            ids.append(cut["id"])
            secs += int(cut["seconds"])
        segments.append({"id": f"seg{index + 1}", "cutIds": ids, "seconds": secs})
    for no, cut in enumerate(rebalanced, start=1):
        cut["no"] = no
    return rebalanced, segments


def _group_seconds(group: list[dict]) -> int:
    return sum(int(c["seconds"]) for c in group)


def _repair_short_segments(groups: list[list[dict]], max_seconds: int) -> list[list[dict]]:
    """MIN 미만 구간을 이웃과 합쳐 없앤다. 합쳐서 max 초과면 둘로 다시 나눈다(둘 다 MIN 이상)."""
    lo = MIN_SEGMENT_SECONDS
    hi = max(lo, int(max_seconds))

    # 반복: 가장 짧은 <MIN 구간을 이웃과 합친다. 구간이 하나뿐이면(= 전체) 그대로 둔다.
    guard = 0
    while len(groups) > 1 and guard < 10000:
        guard += 1
        short_idx = next((i for i, g in enumerate(groups) if _group_seconds(g) < lo), -1)
        if short_idx < 0:
            break
        # 합칠 이웃: 앞 구간 우선(마지막 짧은 구간은 앞과 합친다), 없으면 뒤.
        if short_idx > 0:
            left, right = short_idx - 1, short_idx
        else:
            left, right = short_idx, short_idx + 1
        merged = groups[left] + groups[right]
        merged_s = _group_seconds(merged)
        if merged_s <= hi:
            groups[left:right + 1] = [merged]
        else:
            # 합치면 max 초과 → 둘로 다시 나눈다. 둘 다 [MIN,max] 가 되게, 가능한 한 고르게(절반).
            # first_target 은 [max(MIN, merged_s-max), min(max, merged_s-MIN)] 안에서 절반에 가깝게.
            low_bound = max(lo, merged_s - hi)
            high_bound = min(hi, merged_s - lo)
            half = (merged_s + 1) // 2
            first_target = min(max(half, low_bound), high_bound)
            if first_target < lo:
                first_target = lo
            a, b = _split_group_at(merged, first_target)
            groups[left:right + 1] = [a, b]
    return groups


def _split_group_at(group: list[dict], first_seconds: int) -> tuple[list[dict], list[dict]]:
    """컷 조각 묶음을 앞 구간(합 = first_seconds)과 나머지로 나눈다. 경계가 어긋나면 컷을 쪼갠다."""
    first: list[dict] = []
    rest: list[dict] = []
    acc = 0
    queue = [dict(c) for c in group]
    i = 0
    while i < len(queue):
        cut = queue[i]
        s = int(cut["seconds"])
        if acc >= first_seconds:
            rest.append(cut)
            i += 1
            continue
        need = first_seconds - acc
        if s <= need:
            first.append(cut)
            acc += s
            i += 1
        else:
            part_no = _next_split_part(cut)
            head = dict(cut, seconds=need, id=f"{cut['id']}-{part_no}")
            tail = dict(cut, seconds=s - need, id=f"{cut['id']}-{part_no + 1}")
            first.append(head)
            acc += need
            rest.append(tail)
            i += 1
    return first, rest


def _next_split_part(piece: dict) -> int:
    """컷 조각 id 끝의 '-N' 다음 번호. 조각이 아니면 1 부터."""
    pid = str(piece.get("id") or "")
    if "-" in pid:
        tail = pid.rsplit("-", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return 1


def update_conti(owner: str, pid: str, patch: dict) -> dict:
    """사용자 수정(컷 추가·삭제·순서·내용·초). 저장 때마다 segments 를 다시 계산한다.

    seconds 합이 totalSeconds 와 다르면 fitNeeded 를 True 로 둬 프런트가 /conti/fit 을 부르게 한다.

    VNEW_FX 가 켜져 있으면(vnew-live-fx A.2): 1) 컷의 기호·분위기 필드를 정리한다(들어온 키만). 2) 같은 id 의 옛 컷과
    moodKo 가 다르고 moodEn 이 옛 값 그대로면 moodEn 을 비운다(번역 대상, Claude 결정 6). 3) 저장 전후 컷 목록이
    기호·분위기 필드만 다르면 프리비즈 승인을 풀지 않는다(revision 은 올린다). 끄면 1~3 없이 지금 동작이다.
    """
    fx_on = bool(vnew_settings_module().VNEW_FX)

    def mutator(data: dict) -> dict:
        conti = data.setdefault("conti", {})
        old_cuts = [dict(cut) for cut in (conti.get("cuts") or []) if isinstance(cut, dict)]
        if "cuts" in patch and isinstance(patch["cuts"], list):
            cuts = [dict(cut) for cut in patch["cuts"]]
            if fx_on:
                cuts = [vnew_fx.normalize_cut_fx(cut) for cut in cuts]
                _clear_stale_mood_en(old_cuts, cuts)
            for no, cut in enumerate(cuts, start=1):
                cut["no"] = no
                try:
                    cut["seconds"] = max(1, int(cut.get("seconds") or 1))
                except (TypeError, ValueError):
                    cut["seconds"] = 1
            video_model = str((data.get("input") or {}).get("videoModel") or vpoc_ark.DEFAULT_MODEL)
            cuts, segments = compute_segments(cuts, _max_segment_seconds(video_model))
            conti["cuts"] = cuts
            conti["segments"] = segments
            if conti.get("status") in ("none", "failed"):
                conti["status"] = "ready"
        total = int((data.get("input") or {}).get("totalSeconds") or 0)
        summed = sum(int(cut.get("seconds") or 0) for cut in conti.get("cuts") or [])
        data["revision"] = int(data.get("revision") or 0) + 1
        if not (fx_on and vnew_fx.only_fx_changed(old_cuts, conti.get("cuts") or [])):
            _unapprove_previz(data)
        return {"fitNeeded": total != summed, "totalSeconds": total, "sumSeconds": summed}

    updated, info = mutate_plan(owner, pid, mutator)
    updated["_fit"] = info  # routes 가 응답에 쓴다(저장 데이터가 아니라 반환용).
    return updated


def _clear_stale_mood_en(old_cuts: list[dict], new_cuts: list[dict]) -> None:
    """같은 id 의 옛 컷과 moodKo 가 다르고 moodEn 이 옛 값 그대로인 새 컷의 moodEn 을 비운다(제자리, A.2-2).

    새 컷은 이미 정리된 값이다. 옛 컷은 끔 상태에서 저장됐을 수 있어 비교 전에 같은 규칙으로 정리한다.
    """
    old_by_id = {str(cut.get("id") or ""): cut for cut in old_cuts if cut.get("id")}
    for cut in new_cuts:
        old = old_by_id.get(str(cut.get("id") or ""))
        if old is None or not cut.get("moodEn"):
            continue
        mood_changed = cut.get("moodKo", "") != vnew_fx.clean_text(old.get("moodKo"), vnew_fx.MOOD_KO_MAX)
        same_en = cut.get("moodEn") == vnew_fx.clean_text(old.get("moodEn"), vnew_fx.MOOD_EN_MAX)
        if mood_changed and same_en:
            cut["moodEn"] = ""


def fit_conti(owner: str, pid: str) -> dict:
    """저장된 컷을 totalSeconds 에 다시 맞추고 segments 를 재계산한다."""

    def mutator(data: dict) -> None:
        conti = data.setdefault("conti", {})
        cuts = conti.get("cuts") or []
        total = int((data.get("input") or {}).get("totalSeconds") or 0)
        video_model = str((data.get("input") or {}).get("videoModel") or vpoc_ark.DEFAULT_MODEL)
        cuts = fit_seconds(cuts, total)
        cuts, segments = compute_segments(cuts, _max_segment_seconds(video_model))
        conti["cuts"] = cuts
        conti["segments"] = segments

    updated, _ = mutate_plan(owner, pid, mutator)
    return updated


# ================================================================ ③ 기호·분위기 채우기(vnew-live-fx 묶음 A, design §A.3)
#
# 상위 모델 1회 호출(_analyze_fx). 빈 컷(FILL)은 기호·분위기를, 번역 대상 컷(TRANSLATE)은 moodEn 만 채운다.
# 모델에 보내는 글은 모두 clean_text 를 지나 한 줄씩이고, 이미지는 대본 단계와 같은 타일 번호(ordered_tiles)다.

FX_DIALOGUE_MAX, FX_SITUATION_MAX, FX_EMOTION_MAX, FX_NAME_MAX, FX_NAMES_MAX = 80, 120, 60, 40, 12


def _fx_model() -> str:
    """채우기 모델 = 콘티와 같은 최상위 분석 모델(ANALYZE_MODEL 체인, llm-top-model-first)."""
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    return vpoc_settings.ANALYZE_MODEL


def _fx_names(plan: dict, cuts: list[dict]) -> list[str]:
    """등장인물 이름(입력 역할명 → 컷 인물 이름 순, 중복 제거, 최대 12)."""
    names: list[str] = []
    raw = [item.get("roleName") for item in ((plan.get("input") or {}).get("characters") or []) if isinstance(item, dict)]
    for cut in cuts:
        raw.extend(c.get("name") for c in (cut.get("characters") or []) if isinstance(c, dict))
    for value in raw:
        name = vnew_fx.clean_text(value, FX_NAME_MAX)
        if name and name not in names:
            names.append(name)
        if len(names) >= FX_NAMES_MAX:
            break
    return names


def _cut_panels(cut: dict) -> list[int]:
    return [p for p in (cut.get("panels") or []) if _is_int(p) and p >= 0]


def _cut_emotions(cut: dict, entries: list) -> str:
    """대본 entries 중 이 컷 panels 와 겹치는 항목의 emotionKo(중복 제거, 쉼표, 60자)."""
    panels = set(_cut_panels(cut))
    found: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not panels & set(entry.get("panels") or []):
            continue
        emotion = vnew_fx.clean_text(entry.get("emotionKo"), FX_EMOTION_MAX)
        if emotion and emotion not in found:
            found.append(emotion)
    return vnew_fx.clean_text(", ".join(found), FX_EMOTION_MAX)


def _pick_even(numbers: list[int], cap: int) -> list[int]:
    """순서를 지켜 고르게 cap 개를 고른다(ordered_tiles 와 같은 방식)."""
    if len(numbers) <= cap:
        return list(numbers)
    step = len(numbers) / cap
    return [numbers[i] for i in sorted({min(len(numbers) - 1, int(i * step)) for i in range(cap)})]


def _fx_blocks(
    owner: str, pid: str, plan: dict, cuts: list[dict], empty_ids: list[str], translate_ids: list[str]
) -> tuple[list[dict], list[int], dict]:
    """채우기 요청 블록. (blocks, 보낸 패널 번호, {TRANSLATE 컷 id: 보낸 moodKo}).

    글 1개(등장인물·컷 줄) + FILL 컷 panels 합집합의 패널 이미지(번호 순, VNEW_FX_MAX_PANELS 넘으면 고르게).
    이미지마다 앞에 "Panel N". TRANSLATE 만 있으면 이미지 없이 글만(S3·타일을 읽지 않는다).
    """
    fill_set, translate_set = set(empty_ids), set(translate_ids)
    entries = (plan.get("script") or {}).get("entries") or []
    names = _fx_names(plan, cuts)
    lines = [
        "[등장인물] " + (", ".join(names) if names else "(없음)"),
        "[컷] FILL = 채울 컷, TRANSLATE = moodEn만, - = 참고",
    ]
    sent_mood: dict[str, str] = {}
    fill_panels: set[int] = set()
    for index, cut in enumerate(cuts, start=1):
        # 대상 판단은 저장된 id 그대로, 글에는 한 줄로 정리한 id 를 쓴다(id 에 줄바꿈을 넣어 줄을 꾸밀 수 없게).
        cid = str(cut.get("id") or "")
        label = vnew_fx.clean_text(cid, FX_NAME_MAX)
        if not label:
            continue
        no = cut.get("no") if _is_int(cut.get("no")) else index
        head = f"{label} (컷 {no})"
        if cid in fill_set:
            panels = _cut_panels(cut)
            fill_panels.update(panels)
            parts = [f"FILL {head} panels=" + (",".join(str(p) for p in panels) or "-")]
            dialogue = vnew_fx.clean_text(cut.get("dialogueKo"), FX_DIALOGUE_MAX)
            situation = vnew_fx.clean_text(cut.get("situationKo"), FX_SITUATION_MAX)
            emotion = _cut_emotions(cut, entries)
            if dialogue:
                parts.append(f"대사: {dialogue}")
            if situation:
                parts.append(f"상황: {situation}")
            if emotion:
                parts.append(f"감정(대본): {emotion}")
            lines.append(" | ".join(parts))
        elif cid in translate_set:
            mood = vnew_fx.clean_text(cut.get("moodKo"), vnew_fx.MOOD_KO_MAX)
            sent_mood[cid] = mood
            lines.append(f"TRANSLATE {head} moodKo=" + json.dumps(mood, ensure_ascii=False))
        else:
            parts = [f"- {head}"]
            for label, key, limit in (("fxSuggest", "fxSuggest", vnew_fx.FX_MAX), ("fx", "fx", vnew_fx.FX_MAX),
                                      ("drawnFx", "fxDrawn", vnew_fx.DRAWN_MAX)):
                keys = vnew_fx.fx_list(cut.get(key), limit)
                if keys:
                    parts.append(f"{label}=" + ", ".join(keys))
            lines.append(" | ".join(parts))
    blocks: list[dict] = [{"text": "\n".join(lines)}]
    numbers: list[int] = []
    if fill_set and fill_panels:
        order = list((plan.get("input") or {}).get("order") or [])
        tiles, _total, _picked = ordered_tiles(owner, pid, order)
        numbers = sorted(p for p in fill_panels if p < len(tiles))
        numbers = _pick_even(numbers, max(1, int(vnew_settings_module().VNEW_FX_MAX_PANELS)))
        for number in numbers:
            blocks.append({"text": f"Panel {number}"})
            blocks.append({"image": {"format": "jpeg", "source": {"bytes": tiles[number]}}})
    return blocks, numbers, sent_mood


def _fx_items(payload: dict) -> list[dict]:
    """모델 결과 cuts 를 dict 목록으로(Fable 계열의 JSON 문자열 중첩을 푼다). 값 정리는 merge_fill 이 한다."""
    raw = _loads_if_str((payload or {}).get("cuts") if isinstance(payload, dict) else None)
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for item in raw:
        item = _loads_if_str(item)
        if not isinstance(item, dict):
            continue
        row = {"id": str(item.get("id") or "").strip()}
        for key in ("drawnFx", "fxSuggest", "fx"):
            row[key] = _loads_if_str(item.get(key))
        row["moodKo"] = item.get("moodKo")
        row["moodEn"] = item.get("moodEn")
        items.append(row)
    return items


def fill_fx(
    owner: str, pid: str, plan: dict, cuts: list[dict], empty_ids: list[str], translate_ids: list[str]
) -> dict:
    """채우기 1회(모델 호출·원장). 합치기는 부르는 쪽이 지금 컷에 한다(_apply_fx).

    반환: {items, model, panels(보낸 장수), sentMood, called}. 대상이 없으면 호출하지 않는다(called False).
    """
    if not empty_ids and not translate_ids:
        return {"items": [], "model": "", "panels": 0, "sentMood": {}, "called": False}
    blocks, numbers, sent_mood = _fx_blocks(owner, pid, plan, cuts, empty_ids, translate_ids)
    model = _fx_model()
    payload, usage = _analyze_fx(blocks, model=model)
    used = str((usage or {}).get("model") or model)
    _charge(owner, pid, plan.get("planId") or "", usage or {}, used)
    return {"items": _fx_items(payload), "model": used, "panels": len(numbers), "sentMood": sent_mood, "called": True}


def _apply_fx(cuts: list[dict], result: dict, empty_ids: list[str]) -> tuple[list[dict], dict]:
    """지금 컷에 결과를 합치고(merge_fill) 같은 기호 3컷 한도를 다시 적용한다(apply_cap). (새 cuts, 통계)."""
    merged, stats = vnew_fx.merge_fill(cuts, result.get("items") or [], empty_ids, result.get("sentMood") or {})
    merged, capped = vnew_fx.apply_cap(merged, stats.get("filledIds") or [])
    stats["capped"] = capped
    return merged, stats


def _fx_state(status: str, *, model: str = "", stats: dict | None = None, error: str = "") -> dict:
    """plan.fxFill 저장 모양: {status, error, model, filled, translated, missed, at}."""
    stats = stats or {}
    return {
        "status": status,
        "error": error,
        "model": model,
        "filled": int(stats.get("filled") or 0),
        "translated": int(stats.get("translated") or 0),
        "missed": int(stats.get("missed") or 0),
        "at": now_iso(),
    }


def _fx_error_message(exc: BaseException) -> str:
    """화면용 실패 문구(300자). VpocError 는 그 문구, 그 밖은 종류 이름만(내부 원문을 화면에 두지 않는다)."""
    if isinstance(exc, VpocError):
        return vnew_fx.clean_text(exc.message, 300) or "기호·분위기를 채우지 못했습니다."
    return f"기호·분위기를 채우지 못했습니다({type(exc).__name__})."


def _log_fx(pid: str, empty_ids: list, translate_ids: list, result: dict, stats: dict, started: float) -> None:
    """한 줄 로그(모델 글은 남기지 않는다)."""
    log.info(
        "vnew.conti.fx pid=%s fill=%d translate=%d panels=%d filled=%d translated=%d missed=%d capped=%d ms=%d",
        pid, len(empty_ids), len(translate_ids), int(result.get("panels") or 0), int(stats.get("filled") or 0),
        int(stats.get("translated") or 0), int(stats.get("missed") or 0), int(stats.get("capped") or 0),
        int((time.monotonic() - started) * 1000),
    )


def _fill_new_conti(owner: str, pid: str, plan: dict, cuts: list[dict]) -> tuple[list[dict], dict]:
    """콘티를 새로 만들 때 채우기 1회(RA2.1). (컷, fxFill). 실패하면 경고 로그 · 기호 없는 컷 · fxFill failed."""
    started = time.monotonic()
    empty_ids, translate_ids = vnew_fx.fill_targets(cuts)
    try:
        result = fill_fx(owner, pid, plan, cuts, empty_ids, translate_ids)
        merged, stats = _apply_fx(cuts, result, empty_ids)
    except Exception as exc:  # noqa: BLE001 — 채우기 실패는 콘티 실패가 아니다.
        log.warning("vnew.conti.fx.failed pid=%s err=%s", pid, str(exc)[:300])
        return cuts, _fx_state("failed", error=_fx_error_message(exc))
    _log_fx(pid, empty_ids, translate_ids, result, stats, started)
    return merged, _fx_state("ready", model=str(result.get("model") or ""), stats=stats)


def start_fx_fill(owner: str, pid: str) -> dict:
    """③ [기호·분위기 채우기] 시작(RA2.2·RA2.5). {taskId, targets, translate}.

    끔 404 FxDisabled → 콘티 ready·컷 있음(아니면 409 ContiNotReady) → 대상 0 이면 409 NothingToFill →
    start_task("fxFill")(진행 중이면 409 TaskRunning) → 스레드 run_fx_fill.
    """
    if not vnew_settings_module().VNEW_FX:
        raise VpocError("vnew.conti.fx", "FxDisabled", "기호·분위기 기능이 꺼져 있습니다.", 404)
    plan, _ = load_plan(owner, pid)
    conti = plan.get("conti") or {}
    cuts = [cut for cut in (conti.get("cuts") or []) if isinstance(cut, dict)]
    if conti.get("status") != "ready" or not cuts:
        raise VpocError("vnew.conti.fx", "ContiNotReady", "먼저 콘티를 만들어 주세요.", 409)
    empty_ids, translate_ids = vnew_fx.fill_targets(cuts)
    if not empty_ids and not translate_ids:
        raise VpocError("vnew.conti.fx", "NothingToFill", "채울 컷이 없습니다.", 409)
    task_id = start_task(owner, pid, "fxFill")
    _spawn(run_fx_fill, owner, pid, task_id, name=f"vnew-fx-{pid}")
    return {"taskId": task_id, "targets": len(empty_ids), "translate": len(translate_ids)}


def run_fx_fill(owner: str, pid: str, task_id: str) -> None:
    """채우기 작업(백그라운드 스레드). 계획을 다시 읽어 대상을 정하고 1회 호출한 뒤, 저장 때 지금 컷에 합친다.

    task.id 가 다르면(취소·재실행) 결과를 버린다. 그 사이 빈 컷이 아니게 됐거나 moodKo 가 바뀐 컷은 건너뛴다.
    프리비즈 승인·revision 은 건드리지 않는다. 실패는 _fail_step("fxFill").
    """
    started = time.monotonic()
    try:
        plan, _ = load_plan(owner, pid)
        cuts = [cut for cut in ((plan.get("conti") or {}).get("cuts") or []) if isinstance(cut, dict)]
        empty_ids, translate_ids = vnew_fx.fill_targets(cuts)
        result = fill_fx(owner, pid, plan, cuts, empty_ids, translate_ids)

        def mutator(data: dict) -> dict:
            if (data.get("task") or {}).get("id") != task_id:
                raise _Discard()
            conti = data.setdefault("conti", {})
            merged, stats = _apply_fx(conti.get("cuts") or [], result, empty_ids)
            conti["cuts"] = merged
            data["fxFill"] = _fx_state("ready", model=str(result.get("model") or ""), stats=stats)
            data["task"] = {
                "name": "fxFill", "status": "done", "startedAt": (data.get("task") or {}).get("startedAt"), "id": None,
            }
            return stats

        try:
            _, stats = mutate_plan(owner, pid, mutator)
        except _Discard:
            log.info("vnew.fxFill.discarded pid=%s task=%s", pid, task_id)
            return
        _log_fx(pid, empty_ids, translate_ids, result, stats, started)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.conti.fx.failed pid=%s err=%s", pid, str(exc)[:300])
        _fail_step(owner, pid, "fxFill", task_id, _fx_error_message(exc))


# ================================================================ 모델 · 길이


def _script_model() -> str:
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    return vpoc_settings.ANALYZE_MODEL


def _conti_model() -> str:
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    return vpoc_settings.ANALYZE_MODEL


def _max_segment_seconds(video_model: str) -> int:
    """이 영상 모델의 한 번 생성 최대 길이(초). 구간 나누기 단위."""
    try:
        options = vpoc_ark.video_options(video_model)
        value = int(options.get("maxDurationS") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.max_seconds.failed model=%s err=%s", video_model, str(exc)[:150])
        value = 0
    return value if value > 0 else 5


# ================================================================ task(진행·취소)


def start_task(owner: str, pid: str, name: str) -> str:
    """새 작업을 시작한다. 진행 중이면 409. 새 task.id 를 돌려준다(백그라운드가 이 id 로 결과를 확인)."""
    new_id = secrets.token_hex(8)

    def mutator(data: dict) -> str:
        task = data.get("task") or _empty_task()
        if task.get("status") == "running" and not _task_stale(task):
            raise VpocError("vnew.task", "TaskRunning", "이미 작업이 진행 중입니다.", 409)
        data["task"] = {"name": name, "status": "running", "startedAt": now_iso(), "id": new_id}
        # 해당 단계 상태를 running 으로.
        step = data.setdefault(name, {})
        step["status"] = "running"
        step["error"] = ""
        return new_id

    mutate_plan(owner, pid, mutator)
    return new_id


def cancel_task(owner: str, pid: str) -> dict:
    """진행 중 작업 취소. task.id 를 바꿔 늦게 온 결과를 버린다(vpoc 014 방식)."""

    def mutator(data: dict) -> None:
        task = data.get("task") or _empty_task()
        if task.get("status") != "running":
            raise VpocError("vnew.task", "NothingToCancel", "진행 중인 작업이 없습니다.", 409)
        name = task.get("name") or ""
        data["task"] = {"name": name, "status": "cancelled", "startedAt": task.get("startedAt"), "id": None}
        if name in _TASK_STEPS:
            step = data.setdefault(name, {})
            if step.get("status") == "running":
                step["status"] = "none"

    updated, _ = mutate_plan(owner, pid, mutator)
    return updated.get("task") or _empty_task()


def _commit_step(owner: str, pid: str, name: str, task_id: str, result: dict, extra: dict | None = None) -> None:
    """백그라운드 결과를 저장한다. 현재 task.id 가 다르면(취소·재실행) 버린다.

    extra 가 있으면 같은 저장에 최상위 키로 함께 넣는다(예: run_conti 의 fxFill). 기본 None 이면 지금과 같다.
    """

    def mutator(data: dict) -> None:
        current = (data.get("task") or {}).get("id")
        if current != task_id:
            # 늦게 온 결과: 버린다.
            raise _Discard()
        data[name] = result
        if extra:
            data.update(extra)
        data["task"] = {"name": name, "status": "done", "startedAt": (data.get("task") or {}).get("startedAt"), "id": None}

    try:
        mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.%s.discarded pid=%s task=%s", name, pid, task_id)


def _fail_step(owner: str, pid: str, name: str, task_id: str, message: str) -> None:
    def mutator(data: dict) -> None:
        current = (data.get("task") or {}).get("id")
        if current != task_id:
            raise _Discard()
        step = data.setdefault(name, {})
        step["status"] = "failed"
        step["error"] = message
        data["task"] = {"name": name, "status": "error", "startedAt": (data.get("task") or {}).get("startedAt"), "id": None}

    try:
        mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.%s.discarded_fail pid=%s task=%s", name, pid, task_id)


class _Discard(Exception):
    """늦게 온 결과를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


def _task_stale(task: dict) -> bool:
    if task.get("status") != "running":
        return False
    started = _parse_iso(task.get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > TASK_STALL_S


def reconcile_stale(plan: dict) -> dict:
    """조회 때 호출: running 이 오래 멈췄으면 그 단계를 failed 로 본다(저장하지 않고 뷰만)."""
    task = plan.get("task") or {}
    if _task_stale(task):
        name = task.get("name") or ""
        plan = dict(plan)
        plan["task"] = {"name": name, "status": "error", "startedAt": task.get("startedAt"), "id": None}
        if name in _TASK_STEPS:
            step = dict(plan.get(name) or {})
            step["status"] = "failed"
            step["error"] = "작업이 중단되었습니다. 다시 시도해 주세요."
            plan[name] = step
    return plan


# ================================================================ 백그라운드 시작


def start_script(owner: str, pid: str) -> str:
    task_id = start_task(owner, pid, "script")
    _spawn(run_script, owner, pid, task_id, name=f"vnew-script-{pid}")
    return task_id


def start_conti(owner: str, pid: str) -> str:
    task_id = start_task(owner, pid, "conti")
    _spawn(run_conti, owner, pid, task_id, name=f"vnew-conti-{pid}")
    return task_id


def _spawn(func, owner: str, pid: str, task_id: str, *, name: str) -> None:
    thread = threading.Thread(target=func, args=(owner, pid, task_id), daemon=True, name=name)
    thread.start()


# ================================================================ 원장 · presign


def _charge(owner: str, pid: str, plan_id: str, usage: dict, model: str) -> None:
    """LLM 사용량을 원장에 남긴다. 토큰을 모르면 건너뛴다(보수적)."""
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        key = f"vnew-{pid}-{plan_id}-{secrets.token_hex(3)}"
        vpoc_ledger.charge_text(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=plan_id,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.charge.failed pid=%s err=%s", pid, str(exc)[:200])


def _presign(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, content_type="image/jpeg", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.presign.failed key=%s err=%s", key, str(exc)[:150])
        return ""


# ================================================================ 보기(presigned · 캐릭터)


def plan_view(owner: str, pid: str, plan: dict) -> dict:
    """GET /plan 응답. 이미지 썸네일 presigned URL · 캐릭터 이름·썸네일·상태를 담는다."""
    plan = reconcile_stale(plan)
    inp = plan.get("input") or {}
    images = []
    for item in inp.get("images") or []:
        images.append(dict(item, url=_presign(upload_key(owner, pid, item.get("fileId") or ""))))

    characters_out = []
    for item in inp.get("characters") or []:
        char_id = str(item.get("charId") or "")
        row = {"charId": char_id, "roleName": item.get("roleName") or "", "name": "", "status": "", "thumbUrl": ""}
        try:
            char, _ = characters.load_char(owner, char_id)
            row["name"] = char.get("name") or ""
            row["status"] = char.get("status") or ""
            thumb = ""
            for variant in characters.settings.VARIANTS:
                model3d = ((char.get("variants") or {}).get(variant) or {}).get("model3d") or {}
                if model3d.get("thumbKey"):
                    thumb = model3d["thumbKey"]
                    break
            row["thumbUrl"] = _presign(thumb) if thumb else ""
        except Exception:  # noqa: BLE001
            row["status"] = "missing"
        characters_out.append(row)

    return {
        "planId": plan.get("planId"),
        "createdAt": plan.get("createdAt"),
        "updatedAt": plan.get("updatedAt"),
        "revision": plan.get("revision", 0),
        "input": {
            "images": images,
            "order": inp.get("order") or [],
            "totalSeconds": inp.get("totalSeconds"),
            "videoModel": inp.get("videoModel"),
            "notes": inp.get("notes") or "",
            "characters": characters_out,
            # 장면 의상·목소리(설계자 2026-10-26, 064). castOverrides = 사용자 수정,
            # sceneCast = 원작 패널 자동 읽기 + override 를 인물별로 합친 화면용 표.
            "castOverrides": inp.get("castOverrides") or {},
            "sceneCast": _scene_cast_view(owner, pid, plan),
        },
        "models": previz_models(),
        "script": plan.get("script") or {},
        "conti": plan.get("conti") or {},
        # vnew-live-fx 설정(화면이 새 요소를 숨길지 안다)과 ③ 기호·분위기 채우기 상태·대상 수·추정 금액.
        "features": _features_view(),
        "fxFill": _fx_fill_view(plan),
        "locations": _locations_view(owner, pid, plan),
        "previz": _previz_view(owner, pid, plan),
        "gen": _gen_view(owner, pid, plan),
        "task": plan.get("task") or _empty_task(),
    }


def _gen_view(owner: str, pid: str, plan: dict) -> dict | None:
    """④ 영상 생성 보기(estimate 포함). generate 를 늦게 import 해 순환 참조를 피한다."""
    from . import generate  # noqa: PLC0415

    return generate.gen_view(owner, pid, plan)


def _features_view() -> dict:
    """vnew-live-fx 묶음 설정(design §2). 묶음 B·C 가 casting·takeReal 을 더한다."""
    return {"fx": bool(vnew_settings_module().VNEW_FX)}


def _fx_estimate_usd(cuts: list, empty_ids: list[str], translate_ids: list[str]) -> float:
    """채우기 1회 추정 금액(확인창용, Claude 결정 1). 원장과 같은 단가표, 패널 수 = FILL 컷 panels 합집합(상한)."""
    from ..vpoc import settings as vpoc_settings  # noqa: PLC0415

    opus = "opus" in str(_fx_model() or "")
    usd_in = vpoc_settings.OPUS_USD_PER_MTOK_IN if opus else vpoc_settings.TEXT_USD_PER_MTOK_IN
    usd_out = vpoc_settings.OPUS_USD_PER_MTOK_OUT if opus else vpoc_settings.TEXT_USD_PER_MTOK_OUT
    fill_set = set(empty_ids)
    panels: set[int] = set()
    for cut in cuts:
        if isinstance(cut, dict) and str(cut.get("id") or "") in fill_set:
            panels.update(_cut_panels(cut))
    count = min(len(panels), max(1, int(vnew_settings_module().VNEW_FX_MAX_PANELS)))
    return vnew_fx.estimate_usd(
        count, len(empty_ids), len(translate_ids), len(cuts), usd_in_per_mtok=usd_in, usd_out_per_mtok=usd_out
    )


def _fx_fill_view(plan: dict) -> dict:
    """plan.fxFill 보기: 저장 상태(없으면 none) + 지금 컷 기준 targets·translate·estimateUsd. 실패해도 plan_view 는 산다."""
    state = plan.get("fxFill") if isinstance(plan.get("fxFill"), dict) else {}

    def count(key: str) -> int:
        value = state.get(key)
        return value if _is_int(value) and value >= 0 else 0

    view = {
        "status": str(state.get("status") or "none"),
        "error": str(state.get("error") or ""),
        "model": str(state.get("model") or ""),
        "filled": count("filled"),
        "translated": count("translated"),
        "missed": count("missed"),
        "at": str(state.get("at") or ""),
        "targets": 0,
        "translate": 0,
        "estimateUsd": 0.0,
    }
    try:
        conti = plan.get("conti") or {}
        cuts = [cut for cut in (conti.get("cuts") or []) if isinstance(cut, dict)] if conti.get("status") == "ready" else []
        empty_ids, translate_ids = vnew_fx.fill_targets(cuts)
        view["targets"] = len(empty_ids)
        view["translate"] = len(translate_ids)
        view["estimateUsd"] = _fx_estimate_usd(cuts, empty_ids, translate_ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.plan.fx_view_failed err=%s", str(exc)[:200])
    return view


def _scene_cast_view(owner: str, pid: str, plan: dict) -> list[dict]:
    """장면 의상·목소리 표(설계자 2026-10-26, 064). 인물별 자동 읽기(costumeEn·voiceEn) + override.

    자동 읽기는 previz.castNotes 에 캐시돼 있을 때만(미리 [의상 읽기] 또는 생성 때 채워진다). 없으면
    자동값은 빈 문자열(화면은 참고 안내만 보인다). 화면은 override 가 있으면 그 값을 입력칸에 채운다.
    """
    from . import generate  # noqa: PLC0415

    try:
        return generate.scene_cast_view(owner, pid, plan)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.plan.scene_cast_view_failed pid=%s err=%s", pid, str(exc)[:200])
        return []


def _locations_view(owner: str, pid: str, plan: dict) -> list[dict]:
    """③ 공간 디테일 시트 보기. locations 를 늦게 import 해 순환 참조를 피한다."""
    from . import locations  # noqa: PLC0415

    try:
        return locations.locations_view(owner, pid, plan)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.plan.locations_view_failed pid=%s err=%s", pid, str(exc)[:200])
        return []


def _previz_view(owner: str, pid: str, plan: dict) -> dict:
    """프리비즈(3D 장면 설계) 보기. previz 를 늦게 import 해 순환 참조를 피한다."""
    from . import previz  # noqa: PLC0415

    try:
        return previz.previz_view(owner, pid, plan)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.plan.previz_view_failed pid=%s err=%s", pid, str(exc)[:200])
        return previz.new_previz()


# ================================================================ 작은 도우미


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _loads_if_str(value):
    """값이 JSON 문자열이면 풀어서 돌려준다. 문자열이 아니거나 풀 수 없으면 그대로 돌려준다.

    Fable 계열이 중첩 객체(setting·entries·characters)를 JSON 문자열로 주는 경우를 받는다.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return value


def _as_bool(value) -> bool:
    """enclosed 등 bool 필드를 받는다. "true"/"yes"/"닫힌" 같은 문자열도 True 로 본다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        return token in {"true", "t", "1", "yes", "y", "닫힘", "닫힌", "폐쇄", "실내"}
    return False


def _has_korean(text: str) -> bool:
    """한글 음절이 하나라도 있으면 True(한글/영문 장소 구분용)."""
    return any("\uac00" <= ch <= "\ud7a3" for ch in str(text or ""))


# setting 키 → payload 최상위에서 끌어올 때 받는 별칭들.
_SETTING_ALIASES: dict[str, tuple[str, ...]] = {
    "timeKo": ("timeKo", "time"),
    "moodKo": ("moodKo", "mood"),
    "layoutKo": ("layoutKo", "layout"),
    "layoutEn": ("layoutEn",),
    "locationKo": ("locationKo",),
    "locationEn": ("locationEn",),
    "enclosed": ("enclosed",),
}


def _fill_setting_from_top(setting: dict, payload: dict) -> None:
    """setting 에 없는 키를 payload 최상위의 같은 이름·별칭에서 채운다(값이 있을 때만).

    location/place 는 한글이면 locationKo, 영문이면 locationEn 으로 넣는다.
    """
    if not isinstance(payload, dict):
        return

    for key, aliases in _SETTING_ALIASES.items():
        if setting.get(key):
            continue
        for alias in aliases:
            if alias in payload and payload.get(alias) not in (None, ""):
                setting[key] = payload[alias]
                break

    # location/place: 한글이면 locationKo, 영문이면 locationEn.
    for alias in ("location", "place"):
        value = payload.get(alias)
        if not value or not isinstance(value, str):
            continue
        target = "locationKo" if _has_korean(value) else "locationEn"
        if not setting.get(target):
            setting[target] = value


def _fill_setting_location_from_entries(setting: dict, raw_entries: list) -> None:
    """setting.locationKo/En 이 비면 entries 첫 항목의 locationKo/En 으로 채운다."""
    if not isinstance(raw_entries, list):
        return
    for item in raw_entries:
        item = _loads_if_str(item)
        if not isinstance(item, dict):
            continue
        if not setting.get("locationKo"):
            loc_ko = str(item.get("locationKo") or "").strip()
            if loc_ko:
                setting["locationKo"] = loc_ko
        if not setting.get("locationEn"):
            loc_en = str(item.get("locationEn") or "").strip()
            if loc_en:
                setting["locationEn"] = loc_en
        if setting.get("locationKo") and setting.get("locationEn"):
            break


def _setting_has_content(setting: dict) -> bool:
    """setting 에 의미 있는 값이 하나라도 있으면 True(enclosed=True 포함)."""
    if not isinstance(setting, dict):
        return False
    for key in ("locationKo", "locationEn", "timeKo", "moodKo", "layoutKo", "layoutEn"):
        if str(setting.get(key) or "").strip():
            return True
    return bool(setting.get("enclosed"))


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
