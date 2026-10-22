"""vnew 프리비즈 — Astra 3D 장면 설계(JSON) 서버(설계자 2026-10-21, 지시서 037).

영상 흐름: 웹툰 → 캐릭터 → 콘티(배경·대사) → [여기] 3D 모델링(프리비즈) → 모델링 영상 확인·승인 →
캐릭터 그림체·실사를 각각 입혀 영상 생성. 설계자 결정: Astra 가 장면 설계 JSON 을 쓰고, 브라우저
three.js 가 그려 재생·녹화한다. 이렇게 만든 마네킹 영상을 Kling O3 편집으로 실사화하면 배치·카메라·
인원이 정확히 유지된다(Claude 실측).

프리비즈 규칙(설계자): 배경은 하나의 덩어리로 땅/벽을 알아보고 빛 방향을 표기, 등장 인물은 고유의
색, 그 외 사물은 디테일 없이 인식만. 화질 낮아도 됨.

- 장소별(연속 컷 묶음)로 Astra 에 콘티 컷·대본 위치·location 설계·인물 목록을 주고 장면 JSON 을
  받는다(도구 스키마). previz_schema.normalize_scenes 로 정리(범위·색·시간·텍스처 키·컷 1:1).
- 녹화 업로드는 서버가 imageio-ffmpeg 로 mp4 h264 1280x720 24fps 무음으로 변환해 VIDEO_BUCKET 에
  저장하고 길이를 확인한다.

재사용(수정 금지): vpoc.llm(_converse) · vpoc.store(S3) · vpoc.ledger(charge_text) · vpoc.settings
(VIDEO_BUCKET) · stitcher(ffmpeg_path · probe_duration) · vnew.locations(wall_key/floor_key) ·
vnew.previz_schema(정리). plan.py 가 plan_view 에 previz_view 를 담고 승인 해제 훅을 부른다.

외부 호출(LLM)은 아래 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _design. STORE 는 vpoc.store.STORE.
"""

from __future__ import annotations

import copy
import logging
import os
import secrets
import subprocess
import tempfile
import threading
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE
from ..vpoc import store as vpoc_store

from . import previz_camera
from . import previz_fit
from . import previz_grammar
from . import previz_schema
from . import settings as vnew_settings

log = logging.getLogger("vpoc")

# ================================================================ 상수

# 설계 LLM 체인: Astra 먼저, 실패하면 Claude 분석 체인(locations 와 같은 규칙).
ASTRA_MODEL = vnew_settings.ASTRA_MODEL
DESIGN_FALLBACK_MODEL = vnew_settings.ANALYZE_FALLBACK_MODEL
# 텍스처로 쓸 location 그림체(실사 기본).
TEXTURE_VARIANT = "real"
# 녹화 변환 결과 규격(설계자): mp4 h264 1280x720 24fps 무음.
REC_WIDTH = 1280
REC_HEIGHT = 720
REC_FPS = 24
# 녹화 길이 허용 오차·절대 범위(초). 구간 seconds ±0.5초, 그리고 3~15초.
REC_TOLERANCE_S = 0.5
REC_MIN_S = 3.0
REC_MAX_S = 15.0
# 업로드 바이트 상한(200MB).
MAX_RECORDING_BYTES = 200 * 1024 * 1024
# 프레임(오프라인 렌더) 한 장의 바이트 상한(2MB). jpeg 0.85 1280x720 은 보통 수백 KB.
MAX_FRAME_BYTES = 2 * 1024 * 1024
# 한 구간에 허용하는 최대 프레임 수(15초 × 60fps 여유). 터무니없는 요청을 막는다.
MAX_FRAMES = 15 * 60
# 프레임 fps 허용 범위(설계자: 기본 24).
FRAME_FPS_MIN = 1
FRAME_FPS_MAX = 60
# design task 가 이 시간을 넘게 running 이면 멈춘 것으로 본다(조회 때 failed).
TASK_STALL_S = 1800

# --- 패널 샷 분석(Claude 분석 체인) · 투영 검산(설계자 2026-10-21, 지시서 045) ---
# 컷마다 그 컷 panels 웹툰 조각을 Claude(분석 모델)에 보내 샷(크기·높이·각도·인물 화면 좌표)을
# 읽는다. Astra 는 이미지 읽기가 약하므로(과거 실측) 이미지 분석은 Claude 가 맡고, Astra 는
# 그 분석을 좌표화만 한다.
PANEL_ANALYZE_MODEL = vnew_settings.ANALYZE_FALLBACK_MODEL
# 한 컷 분석에 보낼 웹툰 조각 최대 장수(말풍선 많은 조각보다 그림 큰 조각 우선).
MAX_PANEL_TILES = 2
# 투영 오차가 임계를 넘는 컷은 그 컷 camera 만 Astra 에 다시 받는다. 최대 재요청 횟수.
FIT_MAX_ASTRA_RETRY = 2
# 샷 크기 → 권장 fov(도) 범위·인물 키 화면 비율(heightFrac) 대략치. 지침·기본값에 쓴다.
SHOT_SIZES = ("extreme_close", "close", "medium_close", "medium", "full", "wide")
CAMERA_HEIGHTS = ("low", "eye", "high", "top")
SHOT_ANGLES = (
    "front", "three_quarter_left", "three_quarter_right",
    "side_left", "side_right", "back", "over_shoulder",
)

# 받을 수 있는 녹화 Content-Type.
RECORDING_CONTENT_TYPES = ("video/webm", "video/mp4")

# 승인 관문 최소 일치도(설계자 2026-10-21, 지시서 049). settings env 로 바꿀 수 있다(기본 0.7).
APPROVE_MIN_MATCH = vnew_settings.VNEW_PREVIZ_APPROVE_MIN
# 방 높이 clamp(설계자 049): 닫힌 방 2.4~3.5m, 열린 공간 3.0m. 모델이 10m 를 주는 사례 보정.
ROOM_HEIGHT_ENCLOSED_MIN = 2.4
ROOM_HEIGHT_ENCLOSED_MAX = 3.5
ROOM_HEIGHT_OPEN = 3.0

# 텍스처 확장자 → Content-Type(서버가 같은 출처로 이미지 바이트를 돌려줄 때 쓴다). 모르면 jpeg.
TEXTURE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# 머리카락 색 키워드 → hex(설계자 2026-10-22). 캐릭터 외형(analysis 의 hairEn/appearanceEn)에서
# 색 단어를 찾아 마네킹 머리카락 색을 정한다. 못 찾으면 기본(어두운 회색).
HAIR_COLOR_BY_KEYWORD = (
    ("blond", "#e8cf7a"),
    ("blonde", "#e8cf7a"),
    ("black", "#1a1a1a"),
    ("brown", "#5b3a1e"),
    ("brunette", "#5b3a1e"),
    ("gray", "#a9a9a9"),
    ("grey", "#a9a9a9"),
    ("silver", "#a9a9a9"),
    ("white", "#efefef"),
    ("red", "#9b2d20"),
    ("ginger", "#9b2d20"),
)
HAIR_COLOR_DEFAULT = "#2b2b2b"


def hair_color_for(*texts: str) -> str:
    """외형 문구(hairEn·appearanceEn 등)에서 머리카락 색 키워드를 찾아 hex 를 돌려준다.

    여러 문구를 합쳐 소문자로 보고, HAIR_COLOR_BY_KEYWORD 순서대로 처음 맞는 색을 쓴다. 없으면 기본값.
    예: "short blond hair" → #e8cf7a, "black hair" → #1a1a1a.
    """
    joined = " ".join(str(t or "") for t in texts).lower()
    for keyword, hex_color in HAIR_COLOR_BY_KEYWORD:
        if keyword in joined:
            return hex_color
    return HAIR_COLOR_DEFAULT


# 콘티 actionEn 키워드 → gesture(옛 캐시로 grammar.actions 가 없을 때의 대비책, 설계자 2026-10-22).
# 순서대로 처음 맞는 규칙을 쓴다. (키워드 튜플, gesture).
_GESTURE_KEYWORD_RULES = (
    (("tears", "wipe", "crying", "cry"), "wipe_tears"),
    (("kiss", "lean in", "lean toward", "leans in"), "lean_in"),
    (("both cheeks", "cups", "cup her face", "cup his face", "holds her face", "holds his face"), "both_hands_other_cheeks"),
    (("cheek", "her face", "his face", "touches the face"), "hand_on_other_cheek"),
    (("own face", "hand to face", "hand to mouth", "covers mouth", "cheek with hand"), "hand_to_own_face"),
    (("other's chest", "on the chest of", "against the chest", "pushes the chest", "hand to his chest", "hand to her chest"), "press_hand_to_other_chest"),
    (("chest", "heart", "clutches"), "hand_on_chest"),
    (("laugh", "laughing", "chuckle"), "laugh"),
    (("looks down", "lowers head", "head down", "bows head"), "look_down"),
    (("looks up", "raises head", "head up"), "look_up"),
    (("nods", "nodding", "nod"), "nod"),
    (("turns to", "turns head", "looks at"), "turn_head_to_other"),
)


def gesture_from_action(action_en: str) -> str:
    """콘티 actionEn 한 문장에서 제스처 하나를 고른다(옛 캐시 대비책). 못 찾으면 "none"."""
    text = str(action_en or "").lower()
    if not text:
        return "none"
    for keywords, gesture in _GESTURE_KEYWORD_RULES:
        if any(k in text for k in keywords):
            return gesture
    return "none"



# ================================================================ 키 규칙


def _root(owner: str, pid: str) -> str:
    from . import plan as plan_mod  # noqa: PLC0415

    return plan_mod._root(owner, pid)  # noqa: SLF001


def recording_key(owner: str, pid: str, rev: int, seg_id: str) -> str:
    """녹화 mp4 는 VIDEO_BUCKET 아래 .../vnew/previz/{rev}/{segId}.mp4."""
    return f"{_root(owner, pid)}/previz/{int(rev)}/{seg_id}.mp4"


def frames_prefix(owner: str, pid: str, rev: int, seg_id: str) -> str:
    """오프라인 렌더 프레임(jpeg) 임시 접두사 — 메인 버킷 .../vnew/previz/{rev}/frames/{segId}/.

    파드가 여럿이라 로컬 임시 폴더 대신 S3(메인 버킷)에 둔다. finish 때 내려받아 ffmpeg 로 묶고
    그 자리에서 지운다(임시). mp4 는 recording_key 로 VIDEO_BUCKET 에 따로 저장한다.
    """
    return f"{_root(owner, pid)}/previz/{int(rev)}/frames/{seg_id}/"


def frame_key(owner: str, pid: str, rev: int, seg_id: str, index: int) -> str:
    """프레임 한 장 키: .../frames/{segId}/{index:05d}.jpg(ffmpeg %05d 입력 순서)."""
    return f"{frames_prefix(owner, pid, rev, seg_id)}{int(index):05d}.jpg"


def _locations_prefix(owner: str, pid: str) -> str:
    """이 프로젝트의 공간 시트(텍스처) 접두사. 텍스처 키는 반드시 이 아래여야 한다."""
    return f"{_root(owner, pid)}/locations/"


def texture_api_path(pid: str, key: str) -> str:
    """같은 출처 텍스처 API 경로. 서명 URL 대신 이 경로를 textureUrl 로 준다(S3 CORS 회피)."""
    if not key:
        return ""
    from urllib.parse import quote, urlencode  # noqa: PLC0415

    return f"/api/vnew/p/{quote(str(pid), safe='')}/previz/texture?" + urlencode({"key": key})


def texture_content_type(key: str) -> str:
    """텍스처 키 확장자로 Content-Type 을 고른다. 모르는 확장자는 image/jpeg."""
    _, ext = os.path.splitext(str(key or "").lower())
    return TEXTURE_CONTENT_TYPES.get(ext, "image/jpeg")


def texture_bytes(owner: str, pid: str, key: str) -> tuple[bytes, str]:
    """프리비즈 벽·바닥 텍스처를 메인 버킷에서 읽어 (바이트, Content-Type)로 돌려준다.

    key 가 이 프로젝트의 locations 접두사(users/{owner}/video/{pid}/vnew/locations/) 안인지
    확인한다 — 아니면 404(다른 프로젝트·다른 사용자 데이터에 닿지 못하게). 못 읽어도 404.
    브라우저는 이걸 Authorization 헤더로 받아 blob 으로 three.js 에 넘긴다(같은 출처 → 캔버스
    오염 없음 → captureStream 녹화 가능).
    """
    key = str(key or "").strip()
    prefix = _locations_prefix(owner, pid)
    if not key or not key.startswith(prefix):
        raise VpocError("vnew.previz.texture", "NotFound", "텍스처를 찾을 수 없습니다.", 404)
    try:
        body = STORE.get_bytes(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.texture.missing key=%s err=%s", key, str(exc)[:150])
        raise VpocError("vnew.previz.texture", "NotFound", "텍스처를 찾을 수 없습니다.", 404) from exc
    if not body:
        raise VpocError("vnew.previz.texture", "NotFound", "텍스처를 찾을 수 없습니다.", 404)
    return body, texture_content_type(key)


def panel_api_path(pid: str, cut_id: str) -> str:
    """같은 출처 패널 썸네일 API 경로(컷의 웹툰 조각). 프론트가 이 경로로 받는다."""
    if not cut_id:
        return ""
    from urllib.parse import quote, urlencode  # noqa: PLC0415

    return f"/api/vnew/p/{quote(str(pid), safe='')}/previz/panel?" + urlencode({"cutId": cut_id})


def panel_thumb_bytes(owner: str, pid: str, cut_id: str) -> tuple[bytes, str]:
    """한 컷의 웹툰 패널 조각(분석에 쓴 그림 큰 조각 첫 장)을 (바이트, Content-Type)로 돌려준다.

    콘티에서 그 컷 panels(타일 번호)를 찾아 _cut_tiles 로 그림이 큰 조각을 고른다. 없으면 404.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    cut_id = str(cut_id or "").strip()
    if not cut_id:
        raise VpocError("vnew.previz.panel", "NotFound", "패널을 찾을 수 없습니다.", 404)
    try:
        data, _ = plan_mod.load_plan(owner, pid)
    except Exception as exc:  # noqa: BLE001
        raise VpocError("vnew.previz.panel", "NotFound", "패널을 찾을 수 없습니다.", 404) from exc
    cut = None
    for c in (data.get("conti") or {}).get("cuts") or []:
        if str(c.get("id") or "") == cut_id:
            cut = c
            break
    if cut is None:
        raise VpocError("vnew.previz.panel", "NotFound", "패널을 찾을 수 없습니다.", 404)
    tiles = _cut_tiles(cut.get("panels") or [], _all_tiles(owner, pid))
    if not tiles:
        raise VpocError("vnew.previz.panel", "NotFound", "패널을 찾을 수 없습니다.", 404)
    return tiles[0], "image/jpeg"


# ================================================================ 저장 틀


def new_previz() -> dict:
    """빈 프리비즈. plan["previz"] 에 둔다."""
    return {
        "status": "none",  # none|designing|ready|failed
        "error": "",
        "model": "",
        "revision": 0,  # 콘티·장면이 바뀔 때마다 올린다(녹화 키 경로·승인 해제 기준).
        "scenes": [],  # previz_schema.normalize_scenes 결과
        "panelShots": {},  # cutId -> 웹툰 패널 샷 분석(Claude) {shotSize, cameraHeight, angle, subjects, ...}
        "panelGrammar": {},  # cutId -> {hash, grammar} 연출 문법 읽기 캐시(049, tiles_hash 로 재호출 방지)
        "grammarOverrides": {},  # cutId -> {field: value} 사용자 지시 수정(읽은 grammar 위에 덮어씀)
        "fit": {},  # cutId -> {err, posErr, sizeErr, method} 투영 검산 결과
        "approved": False,
        "approvedForced": None,  # {at, lowCuts} 일치도 낮은 채 강제 승인했을 때 기록(없으면 None)
        "recordings": {},  # segId -> {status, key, seconds}
        "task": _empty_task(),
        "progress": _empty_progress(),  # {step, done, total, startedAt, updatedAt} — 설계 진행 표시
        "extraPromptKo": "",  # 마지막 '다시 만들기' 때 사용자가 넣은 한국어 지시(없으면 빈 문자열)
        "extraCutId": "",  # 지시가 특정 컷을 겨냥하면 그 cutId(없으면 빈 문자열)
        "lastRebuild": None,  # {extraPromptKo, cutId, changesEn, at} — 마지막 다시 만들기 요약
    }


def _empty_task() -> dict:
    return {"status": "idle", "startedAt": None, "id": None}


# ================================================================ 진행 표시(progress)
#
# 설계 중 previz["progress"] 에 {step, done, total, startedAt, updatedAt} 를 기록한다(locations 와
# 같은 모양·같은 규칙이되 import 하지 않고 여기 둔다). 화면이 스피너·진행 막대·경과 시간을 보이는
# 데 쓴다. step 순서: panels → design → fit(→fit_retry) → done. 패널 분석·fit 은 컷마다 +1,
# design 은 그룹마다 +1. task.id 가 어긋나면 건너뛰고, 경합은 mutate_plan 재시도, 실패해도 빌드는
# 계속된다(progress 는 보조 정보라 예외를 삼킨다).


def _empty_progress() -> dict:
    return {"step": "", "done": 0, "total": 0, "startedAt": None, "updatedAt": None}


def _set_progress(owner: str, pid: str, task_id: str, *, step: str, done: int, total: int) -> None:
    """previz["progress"] 를 통째로 새로 쓴다(step 전환 시). task_id 가 어긋나면 건너뛴다.

    startedAt 는 처음(panels) 한 번만 잡고 이후 유지한다. updatedAt 은 매번 now 로 갱신한다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.get("previz")
        if not isinstance(previz, dict):
            return
        if (previz.get("task") or {}).get("id") != task_id:
            return
        prior = previz.get("progress") or {}
        started = prior.get("startedAt") or now_iso()
        previz["progress"] = {
            "step": step,
            "done": int(done),
            "total": int(total),
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.progress.set_failed pid=%s err=%s", pid, str(exc)[:150])


def _set_step(owner: str, pid: str, task_id: str, *, step: str) -> None:
    """progress 의 step 만 바꾸고 done·total 은 그대로 둔다(단계 전환, 누적한 total 을 잃지 않게)."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.get("previz")
        if not isinstance(previz, dict):
            return
        if (previz.get("task") or {}).get("id") != task_id:
            return
        prior = previz.get("progress") or _empty_progress()
        started = prior.get("startedAt") or now_iso()
        total = int(prior.get("total") or 0)
        done = int(prior.get("done") or 0)
        if step == "done":
            done = total  # 끝이면 막대를 가득 채운다.
        previz["progress"] = {
            "step": step,
            "done": done,
            "total": total,
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.progress.set_step_failed pid=%s err=%s", pid, str(exc)[:150])


def _bump_progress(owner: str, pid: str, task_id: str, *, step: str | None = None) -> None:
    """done 을 +1 한다(컷·그룹 하나가 끝날 때마다). 경합은 mutate_plan 이 재시도한다.

    step 을 주면 그 step 으로 바꾼다(없으면 현재 step 유지). updatedAt 을 갱신해 '지연' 판정을 민다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.get("previz")
        if not isinstance(previz, dict):
            return
        if (previz.get("task") or {}).get("id") != task_id:
            return
        prior = previz.get("progress") or _empty_progress()
        started = prior.get("startedAt") or now_iso()
        total = int(prior.get("total") or 0)
        done = int(prior.get("done") or 0) + 1
        if total > 0:
            done = min(total, done)
        previz["progress"] = {
            "step": step or prior.get("step") or "",
            "done": done,
            "total": total,
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.progress.bump_failed pid=%s err=%s", pid, str(exc)[:150])


# ================================================================ 외부 호출 래퍼(검사가 가짜로)


def _design(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 장면 설계 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_DESIGN_SYSTEM,
        blocks=blocks,
        tool=_DESIGN_TOOL,
        max_tokens=8000,
        where="vnew.previz.design",
        model=model,
    )


def _analyze_panel(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """Claude 분석 체인으로 웹툰 패널 샷 JSON 을 받는다. (payload, usage{..model}).

    Astra 는 이미지 읽기가 약하므로(과거 실측) 패널(이미지)에서 샷 크기·카메라 높이·각도·인물
    화면 좌표를 읽는 일은 Claude(분석 모델)가 맡는다. 검사가 이 함수를 가짜로 바꿔 끼운다.
    """
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_PANEL_SYSTEM,
        blocks=blocks,
        tool=_PANEL_TOOL,
        max_tokens=1200,
        where="vnew.previz.panel",
        model=model,
    )


def _changes(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """사용자의 한국어 '다시 만들기' 지시를 '연출 문법 수정 목록'(edits)으로 바꾼다. (payload, usage).

    Astra 먼저, 실패하면 Claude(호출부 _rebuild_edits 가 체인한다). 카메라 숫자는 서버가 계산하므로,
    모델은 숫자가 아니라 연출 문법(샷 크기·종류·정면성·높이·주인공·상대 쪽·blocking)만 고친다.
    검사가 이 함수를 가짜로 바꿔 끼운다(이름은 호환을 위해 _changes 유지).
    """
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_CHANGES_SYSTEM,
        blocks=blocks,
        tool=_CHANGES_TOOL,
        max_tokens=1500,
        where="vnew.previz.edits",
        model=model,
    )


# ================================================================ 지시→연출 문법 수정 목록 스키마(Astra/Claude)

# edits 에서 받을 수 있는 grammar 필드(허용 열거값은 previz_grammar).
_GRAMMAR_EDIT_FIELDS = (
    "shotSize", "shotType", "vantage", "cameraHeight", "mainSubject",
    "offscreenPartnerSide", "headTopY", "subjectX",
)

_CHANGES_SYSTEM = (
    "You turn a Korean user's free-text request to change a 3D previz scene into a SHORT list of "
    "DIRECTING edits. The camera NUMBERS are computed by a server from film grammar, so you do NOT give "
    "camera coordinates. Instead you change the film grammar of specific cuts. Answer ONLY by calling "
    "emit_edits once. Return edits:[{cutId (the cut this applies to, or \"all\" for every cut), field, "
    "value, noteEn}]. Allowed field/value: shotSize (ECU|CU|MCU|MS|MLS|FS|LS); shotType "
    "(clean_single|dirty_single|over_shoulder|two_shot|profile_two_shot|pov|insert|establishing); "
    "vantage (front|front3q|3q|side|back3q|back); cameraHeight (low|eye|high|overhead); mainSubject "
    "(a person's role name); offscreenPartnerSide (left|right|front_of_camera|none); headTopY (0..0.9); "
    "subjectX (0..1); blocking (value is an English sentence placing actors: who sits/stands where, who "
    "faces whom). If the request is vague for a cut (e.g. 'the viewpoint is wrong'), emit that cut with "
    "reread:true (no field) to re-read its grammar. Example: '범건우 옆모습만' → {cutId:'cut-01', "
    "field:'vantage', value:'side'}. Use the cutId values the user is given; use \"all\" for a general "
    "request. Keep the list short (1-6 items)."
)

_CHANGES_TOOL = {
    "name": "emit_edits",
    "description": "Emit a short list of directing edits (film grammar changes) for the previz scene.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cutId": {"type": "string"},
                            "field": {"type": "string"},
                            "value": {},
                            "noteEn": {"type": "string"},
                            "reread": {"type": "boolean"},
                        },
                    },
                },
            },
            "required": ["edits"],
        }
    },
}


def _normalize_edits(payload: dict) -> list[dict]:
    """지시→수정 목록 출력을 정리한다. [{cutId|"all", field?, value?, noteEn?, reread?}].

    - field 가 _GRAMMAR_EDIT_FIELDS·"blocking" 이 아니면 버린다(reread 만 있는 항목은 남긴다).
    - headTopY·subjectX 숫자는 그대로, 열거값은 소문자/원형 유지(적용 때 normalize_grammar 가 보정).
    """
    payload = payload if isinstance(payload, dict) else {}
    items = payload.get("edits")
    if not isinstance(items, list):
        items = payload.get("changes") if isinstance(payload.get("changes"), list) else []
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cut_id = str(item.get("cutId") or "all").strip() or "all"
        entry: dict = {"cutId": cut_id}
        if item.get("reread"):
            entry["reread"] = True
        field = str(item.get("field") or "").strip()
        if field in _GRAMMAR_EDIT_FIELDS or field == "blocking":
            entry["field"] = field
            entry["value"] = item.get("value")
            note = str(item.get("noteEn") or "").strip()
            if note:
                entry["noteEn"] = note
        # 옛 whatEn(047) 형식도 blocking 으로 받아 둔다(호환).
        elif item.get("whatEn"):
            entry["field"] = "blocking"
            entry["value"] = str(item.get("whatEn") or "").strip()
        if entry.get("reread") or entry.get("field"):
            out.append(entry)
    return out


def _normalize_changes(payload: dict) -> list[dict]:
    """옛 047 호환: [{cutId|"all", whatEn}]. (새 흐름은 _normalize_edits 를 쓴다.)"""
    payload = payload if isinstance(payload, dict) else {}
    out: list[dict] = []
    for item in payload.get("changes") or []:
        if not isinstance(item, dict):
            continue
        what = str(item.get("whatEn") or "").strip()
        if not what:
            continue
        cut_id = str(item.get("cutId") or "all").strip() or "all"
        out.append({"cutId": cut_id, "whatEn": what})
    return out


# ================================================================ 패널 샷 분석 스키마(Claude)

_PANEL_SYSTEM = (
    "You read ONE webtoon cut (one or two drawn panels of the same moment) and report the camera shot "
    "so a 3D layout artist can place a matching camera. Answer ONLY by calling emit_shot exactly once. "
    "shotSize is how close the camera is: extreme_close (eyes/mouth), close (head and shoulders), "
    "medium_close (head to chest), medium (waist up), full (whole body), wide (body small in a large "
    "space). cameraHeight is where the camera is relative to the subject: low (below, looking up), eye "
    "(level), high (above, looking down), top (straight down). angle is which side of the subject faces "
    "the camera: front, three_quarter_left, three_quarter_right, side_left, side_right, back, "
    "over_shoulder. For every visible person give subjects:[{name, screenX 0..1 (0=left,1=right), "
    "screenY 0..1 for the TOP OF THE HEAD (0=top of frame,1=bottom), heightFrac 0..1 (the person's "
    "on-screen height divided by the full frame height), facing (camera|left|right|away), visible "
    "(full|waist_up|head)}]. Use the names the user gives (the role names) when you can match a person; "
    "otherwise use a short label. visibleWalls lists which room walls are seen (north/east/south/west). "
    "notesEn is one short English sentence. Report only what is drawn; do not invent people."
)

_PANEL_SUBJECT = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "screenX": {"type": "number"},
        "screenY": {"type": "number"},
        "heightFrac": {"type": "number"},
        "facing": {"type": "string"},
        "visible": {"type": "string"},
    },
}

_PANEL_TOOL = {
    "name": "emit_shot",
    "description": "Report the camera shot (size, height, angle) and each person's on-screen position for one webtoon cut.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "shotSize": {"type": "string"},
                "cameraHeight": {"type": "string"},
                "angle": {"type": "string"},
                "subjects": {"type": "array", "items": _PANEL_SUBJECT},
                "visibleWalls": {"type": "array", "items": {"type": "string"}},
                "notesEn": {"type": "string"},
            },
            "required": ["shotSize", "cameraHeight", "angle"],
        }
    },
}


def _normalize_shot(payload: dict) -> dict:
    """패널 샷 분석 출력을 정리한다(열거값 보정·숫자 0..1 클램프·subjects 정리)."""
    payload = payload if isinstance(payload, dict) else {}

    def _clamp01(value, default):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return float(default)
        return max(0.0, min(1.0, v))

    shot_size = str(payload.get("shotSize") or "").strip().lower()
    if shot_size not in SHOT_SIZES:
        shot_size = "medium"
    camera_height = str(payload.get("cameraHeight") or "").strip().lower()
    if camera_height not in CAMERA_HEIGHTS:
        camera_height = "eye"
    angle = str(payload.get("angle") or "").strip().lower()
    if angle not in SHOT_ANGLES:
        angle = "front"

    subjects: list[dict] = []
    for item in payload.get("subjects") or []:
        if not isinstance(item, dict):
            continue
        facing = str(item.get("facing") or "").strip().lower()
        if facing not in ("camera", "left", "right", "away"):
            facing = "camera"
        visible = str(item.get("visible") or "").strip().lower()
        if visible not in ("full", "waist_up", "head"):
            visible = "full"
        subjects.append(
            {
                "name": str(item.get("name") or "").strip(),
                "screenX": round(_clamp01(item.get("screenX"), 0.5), 3),
                "screenY": round(_clamp01(item.get("screenY"), 0.3), 3),
                "heightFrac": round(_clamp01(item.get("heightFrac"), 0.5), 3),
                "facing": facing,
                "visible": visible,
            }
        )
    walls = [str(w).strip().lower() for w in (payload.get("visibleWalls") or []) if str(w).strip().lower() in previz_schema.SIDES]
    return {
        "shotSize": shot_size,
        "cameraHeight": camera_height,
        "angle": angle,
        "subjects": subjects,
        "visibleWalls": walls,
        "notesEn": str(payload.get("notesEn") or "").strip(),
    }


# ================================================================ 도구 스키마(Astra)

_DESIGN_SYSTEM = (
    "You are a 3D previsualization layout artist. You design a simple blocky 3D scene for ONE place so "
    "a browser (three.js) can render and record a low-fidelity 'mannequin' video. The video keeps the "
    "exact camera, placement, and number of people so it can later be turned photoreal. "
    "Rules: the background is one solid mass you can read as floor or wall, and you mark where light "
    "comes from; each character is a plain figure in a UNIQUE bright colour so they are easy to tell "
    "apart; other objects are simple blocks/cylinders recognisable only as what they are (no detail). "
    "Answer ONLY by calling emit_scene exactly once. Units are metres and seconds. Coordinate system: "
    "x right, y up, z towards the camera (+); the north wall is at z = -depth/2. "
    "room.walls has north/east/south/west. light.from/to are 3D points, light.kind is 'sun' or 'lamp', "
    "and labelKo names it in Korean (e.g. '북쪽 창문 햇빛'). props are {id, labelKo, labelEn, shape "
    "('box'|'cylinder'), size:[w,h,d], pos:[x,y,z], rotY, color}. actors are {id, name (the role name), "
    "color (a unique bright colour, clearly different from every other actor), heightM, charId?, "
    "appearanceEn (a short English look: age, build, hair, clothing — ALWAYS write this, it labels the "
    "mannequin when the character has no reference image)}. "
    "timeline has one entry per conti cut (keep the given cutId values): {cutId, start, end, camera:{pos, "
    "target, fov, moveTo?}, actors:[{id, pos:[x,0,z], facingDeg, pose ('stand'|'sit'|'kneel'|'walk'|'lie'), "
    "moveTo?, gesture?, speaking}]}. Within a cut the camera may move linearly via moveTo. When a cut "
    "changes, ONLY the camera changes — the room, props, and actor positions carry over (actors move only "
    "via moveTo/walk). Place actors to match the webtoon panels and the script positions. "
    "CAMERA FITTING (very important — the mannequin video must match the webtoon panel composition): for "
    "each cut you are given a PANEL SHOT read from the webtoon (shot size, camera height, angle and, for "
    "each subject, the on-screen position screenX 0..1 left-to-right, screenY 0..1 top-to-bottom for the "
    "top of the head, and heightFrac 0..1 = the person's on-screen height as a fraction of the frame). "
    "Place the camera so that each listed subject appears at that (screenX, screenY) with that heightFrac "
    "of the 16:9 frame. Use fov 20-30 for extreme_close/close shots, 35-45 for medium shots, 50-65 for "
    "full/wide shots. Camera height above the floor is about 0.6 m for 'low', 1.5 m for 'eye', 2.3 m for "
    "'high', and near the ceiling looking down for 'top'. The angle tells you from which side of the "
    "subject the camera looks (front = the subject faces the camera; three_quarter/side/back/over_shoulder "
    "turn the camera around the subject). Keep the room, props and actor positions consistent across cuts — "
    "only the camera changes unless the script says someone moves. "
    "When one actor speaks to another, keep the speaker's head and eyes turned toward the listener for "
    "the whole line unless the panel shows otherwise (use facingDeg and gesture='turn_head_to_other' "
    "with gestureTarget set to the listener). Do not invent architecture (stairs, mezzanines, walls or "
    "ceilings) that the location references do not show; distinguish indoor/outdoor, terrace/balcony, "
    "doors, railings and whether there is a ceiling. Avoid meaningless camera shake or excessive "
    "rotation; keep each cut's camera steady as the previz cut. "
    "Write Korean for notesKo. Do not add text outside the tool."
)

_DESIGN_OPENING = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
    },
}

_DESIGN_WALL = {
    "type": "object",
    "properties": {
        "color": {"type": "string"},
        "openings": {"type": "array", "items": _DESIGN_OPENING},
    },
}

_DESIGN_CUT_ACTOR = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "pos": {"type": "array", "items": {"type": "number"}},
        "facingDeg": {"type": "number"},
        "pose": {"type": "string"},
        "moveTo": {"type": "array", "items": {"type": "number"}},
        "gesture": {"type": "string"},
        "gestureTarget": {"type": "string"},
        "speaking": {"type": "boolean"},
    },
}

_DESIGN_CAMERA = {
    "type": "object",
    "properties": {
        "pos": {"type": "array", "items": {"type": "number"}},
        "target": {"type": "array", "items": {"type": "number"}},
        "fov": {"type": "number"},
        "moveTo": {
            "type": "object",
            "properties": {
                "pos": {"type": "array", "items": {"type": "number"}},
                "target": {"type": "array", "items": {"type": "number"}},
            },
        },
    },
}

_DESIGN_TOOL = {
    "name": "emit_scene",
    "description": "Emit the 3D previz scene (room, light, props, actors, timeline) for one place.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "depth": {"type": "number"},
                        "height": {"type": "number"},
                        "floorColor": {"type": "string"},
                        "walls": {
                            "type": "object",
                            "properties": {s: _DESIGN_WALL for s in previz_schema.SIDES},
                        },
                    },
                },
                "light": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "from": {"type": "array", "items": {"type": "number"}},
                        "to": {"type": "array", "items": {"type": "number"}},
                        "color": {"type": "string"},
                        "intensity": {"type": "number"},
                        "labelKo": {"type": "string"},
                    },
                },
                "props": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "labelKo": {"type": "string"},
                            "labelEn": {"type": "string"},
                            "shape": {"type": "string"},
                            "size": {"type": "array", "items": {"type": "number"}},
                            "pos": {"type": "array", "items": {"type": "number"}},
                            "rotY": {"type": "number"},
                            "color": {"type": "string"},
                        },
                    },
                },
                "actors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "color": {"type": "string"},
                            "heightM": {"type": "number"},
                            "charId": {"type": "string"},
                            "appearanceEn": {"type": "string"},
                        },
                    },
                },
                "timeline": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cutId": {"type": "string"},
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "camera": _DESIGN_CAMERA,
                            "actors": {"type": "array", "items": _DESIGN_CUT_ACTOR},
                        },
                    },
                },
                "notesKo": {"type": "string"},
            },
            "required": ["room", "actors", "timeline"],
        }
    },
}


# ================================================================ 장소별 컷 묶기(연속)


def _scene_groups(conti: dict) -> list[dict]:
    """콘티 컷을 '연속 같은 장소'로 묶어 장면 그룹을 만든다.

    설계자: 장소가 바뀌는 컷(예: 노을 진 거리)은 새 장면. 같은 장소가 이어지면 한 장면.
    반환: [{locationEn, cuts:[{cutId, seconds, panels, ...}]}] (콘티 순서).
    """
    cuts = conti.get("cuts") or []
    groups: list[dict] = []
    for cut in cuts:
        loc = str(cut.get("locationEn") or "").strip()
        spec = {
            "cutId": str(cut.get("id") or ""),
            "seconds": float(cut.get("seconds") or 0),
            "panels": list(cut.get("panels") or []),
            "shotKo": str(cut.get("shotKo") or ""),
            "cameraEn": str(cut.get("cameraEn") or ""),
            "situationKo": str(cut.get("situationKo") or ""),
            "dialogueKo": str(cut.get("dialogueKo") or ""),
            "characters": cut.get("characters") or [],
            "locationEn": loc,
        }
        if groups and groups[-1]["locationEn"] == loc:
            groups[-1]["cuts"].append(spec)
        else:
            groups.append({"locationEn": loc, "cuts": [spec]})
    return groups


def _location_for(plan: dict, name_en: str) -> dict | None:
    """콘티 컷의 locationEn 에 맞는 plan["locations"] 항목(key 정규화 매칭)."""
    from . import locations as loc_mod  # noqa: PLC0415

    key = loc_mod.normalize_name(name_en)
    for loc in plan.get("locations") or []:
        if str(loc.get("key") or loc_mod.normalize_name(loc.get("nameEn") or "")) == key:
            return loc
    return None


def _textures_for(owner: str, pid: str, loc: dict | None) -> dict:
    """location 의 실사(real) variant 벽·바닥 이미지 키를 textures dict 로. 없으면 빈 dict."""
    if not loc:
        return {}
    variant = (loc.get("variants") or {}).get(TEXTURE_VARIANT) or {}
    wall_keys = variant.get("wallKeys") or {}
    walls = {side: str(wall_keys.get(side) or "") for side in previz_schema.SIDES if wall_keys.get(side)}
    textures: dict = {}
    if walls:
        textures["walls"] = walls
    floor_key = str(variant.get("floorKey") or "")
    if floor_key:
        textures["floorKey"] = floor_key
    return textures


def _location_id_for(loc: dict | None, name_en: str) -> str:
    return str((loc or {}).get("id") or "") or ("loc:" + (name_en or ""))


# ================================================================ design(백그라운드)


def start_design(owner: str, pid: str, *, extra_prompt_ko: str = "", cut_id: str = "") -> str:
    """프리비즈 설계 시작. 진행 중이면 409. 콘티가 준비돼 있어야 한다. 새 task.id 를 돌려준다.

    extra_prompt_ko/cut_id 는 '다시 만들기' 지시(선택). 첫 설계는 둘 다 비어 있다. 이전 결과
    previz["scenes"] 는 지우지 않는다 — 새 설계가 끝날 때(_commit_scenes) 교체되고, 실패하면
    이전 결과가 그대로 남는다(_fail 은 scenes 를 건드리지 않는다).
    """
    from . import plan as plan_mod  # noqa: PLC0415

    new_id = secrets.token_hex(8)
    extra_prompt_ko = str(extra_prompt_ko or "").strip()[:1000]
    cut_id = str(cut_id or "").strip()

    def mutator(data: dict) -> str:
        conti = data.get("conti") or {}
        if conti.get("status") != "ready" or not (conti.get("cuts")):
            raise VpocError("vnew.previz.design", "NoConti", "먼저 콘티를 만들어 주세요.", 409)
        previz = data.setdefault("previz", new_previz())
        task = previz.get("task") or _empty_task()
        if task.get("status") == "running" and not _task_stale(task):
            raise VpocError("vnew.previz.design", "Designing", "이미 장면을 설계하고 있습니다.", 409)
        previz["status"] = "designing"
        previz["error"] = ""
        previz["approved"] = False  # 다시 설계하면 승인·녹화는 무효(설계 시작 시 해제).
        previz["task"] = {"status": "running", "startedAt": now_iso(), "id": new_id}
        # 다시 만들기 지시를 저장한다(없으면 지운다 — 이전 지시가 남지 않게).
        if extra_prompt_ko:
            previz["extraPromptKo"] = extra_prompt_ko
        else:
            previz["extraPromptKo"] = ""
        if cut_id:
            previz["extraCutId"] = cut_id
        else:
            previz["extraCutId"] = ""
        # progress 초기화: panels 단계, done/total 0.
        previz["progress"] = {
            "step": "panels",
            "done": 0,
            "total": 0,
            "startedAt": now_iso(),
            "updatedAt": now_iso(),
        }

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn(run_design, owner, pid, new_id, name=f"vnew-previz-{pid}")
    return new_id


def run_design(owner: str, pid: str, task_id: str) -> None:
    """프리비즈 설계(백그라운드). 흐름(설계자 2026-10-21, 지시서 049·④):

    1) panels: 컷마다 previz_grammar.read_cut 로 웹툰 패널의 '연출 문법'(샷 크기·종류·정면성·시선·
       상대 쪽·높이)을 읽는다. tiles_hash 로 캐시(previz["panelGrammar"][cutId]={hash,grammar}) —
       같은 패널이면 Claude 를 다시 부르지 않는다(다시 만들기 때 재호출 없음). 진행 total=컷 수.
    2) design: Astra 가 장면(방·소품·인물 자리·자세·facingDeg)을 쓴다. 카메라는 서버가 계산하므로
       Astra 카메라는 대충 둬도 된다(프롬프트에 그렇게 지시). 컷마다 패널 연출 문법 요약을 함께 준다.
       방 높이는 닫힌 방 2.4~3.5m / 열린 공간 3.0m 로 clamp.
    3) camera: 장소(scene)마다 핵심 두 인물로 previz_camera.scene_line → 기본 쪽·crossed. 컷마다
       place_cut(grammar, actors_in_cut, room, side) 로 카메라·actors(hide·move·facingDeg 반영)를
       바꾸고 fit 에 {match, method:"grammar", lens, distanceM, vantageDeg, side, crossed, cheats,
       wallClamped} 저장. grammar 없는 컷은 Astra 카메라를 두고 method "astra", match null.

    다시 만들기(이전 scenes 가 있으면): 사용자 한국어 지시(extraPromptKo)를 '연출 문법 수정 목록'
    (edits)으로 바꿔 previz["grammarOverrides"][cutId]={field:value} 로 저장하고, 읽은 grammar 위에
    덮어쓴다(읽기 캐시는 그대로). reread:true 인 컷은 그 컷 grammar 를 강제로 다시 읽는다. blocking 은
    Astra 배치 프롬프트에 넣는다. 지시도 같은 계산기를 거친다(method user 경로 없음).
    진행 표시: panels → design → camera → done 을 previz["progress"] 에 기록한다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        conti = plan.get("conti") or {}
        groups = _scene_groups(conti)
        if not groups:
            _fail(owner, pid, task_id, "콘티 컷이 없습니다.")
            return

        plan_id = str(plan.get("planId") or "")
        script = plan.get("script") or {}
        chars = (plan.get("input") or {}).get("characters") or []

        # 다시 만들기 판정: 이전 scenes·지시·캐시·override 를 읽는다.
        previz_in = plan.get("previz") or {}
        prev_scenes = previz_in.get("scenes") or []
        is_rebuild = bool(prev_scenes)
        extra_prompt_ko = str(previz_in.get("extraPromptKo") or "").strip()
        extra_cut_id = str(previz_in.get("extraCutId") or "").strip()
        grammar_cache = dict(previz_in.get("panelGrammar") or {})
        overrides = dict(previz_in.get("grammarOverrides") or {})

        # 다시 만들기 지시 → 연출 문법 수정 목록(edits). reread 컷·blocking 문장도 뽑는다.
        reread_cuts: set[str] = set()
        blocking_notes: list[dict] = []
        if is_rebuild and extra_prompt_ko:
            edits = _rebuild_edits(owner, pid, plan_id, extra_prompt_ko, groups, prev_scenes)
            overrides, reread_cuts, blocking_notes = _apply_edits(edits, groups, overrides)

        total_cuts = sum(len(g["cuts"]) for g in groups)

        # 1) panels: 컷마다 연출 문법 읽기(캐시). reread 컷은 캐시 무시하고 다시 읽는다.
        _set_progress(owner, pid, task_id, step="panels", done=0, total=total_cuts)
        grammar_by_cut, grammar_cache = _read_grammars(
            owner, pid, plan_id, groups, chars, grammar_cache, reread_cuts, task_id=task_id,
        )

        # 2) design: Astra 설계. (progress: design, total=그룹 수)
        _set_progress(owner, pid, task_id, step="design", done=0, total=len(groups))
        prev_by_location = {str(s.get("locationId") or ""): s for s in prev_scenes}
        setting = script.get("setting") or {}

        target_group_index = None
        if is_rebuild and extra_cut_id:
            target_group_index = _group_index_for_cut(groups, extra_cut_id)

        raw_scenes: list = []
        scene_specs: list[dict] = []
        used_model = ""
        reuse_scene_by_index: dict[int, dict] = {}

        for group_index, group in enumerate(groups):
            name_en = group["locationEn"]
            loc = _location_for(plan, name_en)
            location_id = _location_id_for(loc, name_en)
            textures = _textures_for(owner, pid, loc)
            cut_specs = [{"cutId": c["cutId"], "seconds": c["seconds"]} for c in group["cuts"]]
            scene_specs.append({"locationId": location_id, "cuts": cut_specs, "textures": textures})

            prev_scene = prev_by_location.get(location_id)
            if target_group_index is not None and group_index != target_group_index and prev_scene is not None:
                reuse_scene_by_index[group_index] = copy.deepcopy(prev_scene)
                raw_scenes.append(None)
                _bump_progress(owner, pid, task_id, step="design")
                continue

            prev_summary = _scene_summary(prev_scene) if (is_rebuild and prev_scene) else None
            group_blocking = _blocking_for_group(blocking_notes, group)
            # 방 높이 clamp 범위: 이 장소가 대본 주 장소면 setting.enclosed, 아니면 열린 공간(3.0).
            room_h_clamp = _room_height_bounds(setting, name_en)
            blocks = [{"text": _design_prompt(
                name_en, loc, group["cuts"], script, chars, grammar_by_cut,
                previous_scene=prev_summary, is_rebuild=is_rebuild, blocking=group_blocking,
                room_height=room_h_clamp,
            )}]
            payload, model = _design_scene(owner, pid, plan_id, blocks)
            used_model = model or used_model
            payload = payload if isinstance(payload, dict) else {}
            payload["locationId"] = location_id
            # 방 높이 clamp(모델이 10m 를 주는 사례).
            _clamp_room_height(payload, room_h_clamp)
            raw_scenes.append(payload)
            _bump_progress(owner, pid, task_id, step="design")

        design_indices = [i for i in range(len(groups)) if i not in reuse_scene_by_index]
        design_raw = [raw_scenes[i] for i in design_indices]
        design_specs = [scene_specs[i] for i in design_indices]
        designed = previz_schema.normalize_scenes(design_raw, scene_specs=design_specs)

        scenes: list[dict] = []
        designed_iter = iter(designed)
        for group_index in range(len(groups)):
            if group_index in reuse_scene_by_index:
                scenes.append(reuse_scene_by_index[group_index])
            else:
                scenes.append(next(designed_iter))

        # 2.5) 외형(머리카락 색)·연기(gesture·gestureTarget)를 장면 인물·timeline 에 채운다.
        _apply_appearance(owner, scenes, chars)
        _apply_gestures(scenes, groups, grammar_by_cut, plan)

        # 3) camera: 장소마다 scene_line → 컷마다 place_cut. (progress: camera, total=컷 수)
        _set_progress(owner, pid, task_id, step="camera", done=0, total=total_cuts)
        fit = _place_cameras(owner, pid, scenes, groups, grammar_by_cut, overrides, task_id=task_id)

        # 4) 저장(done). lastRebuild 요약을 남긴다(다시 만들기였으면).
        _set_step(owner, pid, task_id, step="done")
        last_rebuild = None
        if is_rebuild:
            last_rebuild = {
                "extraPromptKo": extra_prompt_ko,
                "cutId": extra_cut_id,
                "editsEn": [{"cutId": c, "fields": v} for c, v in overrides.items()],
                "at": now_iso(),
            }
        _commit_scenes(
            owner, pid, task_id, scenes, used_model, fit=fit,
            panel_grammar=grammar_cache, grammar_overrides=overrides, last_rebuild=last_rebuild,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.run_design.failed pid=%s err=%s", pid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail(owner, pid, task_id, message)


def _room_height_bounds(setting: dict, name_en: str | None = None) -> tuple[float, float]:
    """방 높이 clamp 범위. 닫힌 방이면 (2.4, 3.5), 열린 공간이면 (3.0, 3.0).

    name_en 을 주면 그 장소가 대본 setting 의 주 장소(locationEn)와 같을 때만 setting.enclosed 를
    쓰고, 다른 장소(예: 거리)는 열린 공간(3.0)으로 본다. 대본 setting 은 주 장소를 설명하므로,
    다른 장소에까지 그 enclosed 를 적용하면 안 된다(설계자 049).
    """
    enclosed = bool((setting or {}).get("enclosed"))
    if name_en is not None:
        from . import locations as loc_mod  # noqa: PLC0415

        setting_loc = str((setting or {}).get("locationEn") or "").strip()
        if setting_loc:
            same = loc_mod.normalize_name(name_en) == loc_mod.normalize_name(setting_loc)
            enclosed = enclosed and same
    if enclosed:
        return (ROOM_HEIGHT_ENCLOSED_MIN, ROOM_HEIGHT_ENCLOSED_MAX)
    return (ROOM_HEIGHT_OPEN, ROOM_HEIGHT_OPEN)


def _clamp_room_height(payload: dict, bounds: tuple[float, float]) -> None:
    """Astra payload 의 room.height 를 clamp 범위로 자른다(모델이 10m 를 주는 사례 보정)."""
    if not isinstance(payload, dict):
        return
    room = payload.get("room")
    if not isinstance(room, dict):
        return
    low, high = bounds
    try:
        h = float(room.get("height"))
    except (TypeError, ValueError):
        h = None
    if h is None:
        room["height"] = round((low + high) / 2.0, 3)
    else:
        room["height"] = round(max(low, min(high, h)), 3)


# ================================================================ panels: 연출 문법 읽기(캐시)


def _grammar_charge(owner: str, pid: str, plan_id: str):
    """previz_grammar.read_cut 에 넘길 charge(usage, model) 콜백(기존 _charge 로 원장 계상)."""

    def _cb(usage: dict, model: str) -> None:
        _charge(owner, pid, plan_id, usage or {}, model)

    return _cb


def _read_grammars(owner, pid, plan_id, groups, chars, grammar_cache, reread_cuts, *, task_id=""):
    """컷마다 연출 문법을 읽는다(캐시). 반환 (grammar_by_cut, 갱신된 grammar_cache).

    - 캐시 previz["panelGrammar"][cutId] = {hash, grammar}. 그 컷 조각 tiles_hash 가 같고 reread 대상이
      아니면 read_cut 을 부르지 않는다(다시 만들기 때 Claude 재호출 없음).
    - 읽기 실패(이미지 없음·None)면 grammar_by_cut 에 넣지 않는다(camera 가 Astra 카메라를 둔다).
    - charge 는 _grammar_charge 콜백으로 원장에.
    """
    all_tiles = _all_tiles(owner, pid)
    role_names = _role_names(chars)
    charge = _grammar_charge(owner, pid, plan_id)
    grammar_by_cut: dict[str, dict] = {}
    cache: dict[str, dict] = dict(grammar_cache or {})
    for group in groups:
        for cut in group["cuts"]:
            cut_id = str(cut["cutId"])
            tiles = _cut_tiles(cut.get("panels") or [], all_tiles)
            hashed = previz_grammar.tiles_hash(tiles)
            cached = cache.get(cut_id)
            if (
                cut_id not in reread_cuts
                and isinstance(cached, dict)
                and cached.get("hash") == hashed
                and isinstance(cached.get("grammar"), dict)
            ):
                grammar_by_cut[cut_id] = cached["grammar"]
                if task_id:
                    _bump_progress(owner, pid, task_id, step="panels")
                continue
            try:
                grammar = previz_grammar.read_cut(
                    owner, pid, plan_id, cut, tiles, role_names, charge=charge
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.previz.grammar.failed cut=%s err=%s", cut_id, str(exc)[:200])
                grammar = None
            if isinstance(grammar, dict):
                grammar_by_cut[cut_id] = grammar
                cache[cut_id] = {"hash": hashed, "grammar": grammar}
            if task_id:
                _bump_progress(owner, pid, task_id, step="panels")
    return grammar_by_cut, cache


# ================================================================ camera: 자리 계산(scene_line·place_cut)


def _scene_key_pair(grammar_by_cut: dict, cut_ids: list[str], scene: dict) -> tuple[str, str]:
    """장면의 핵심 두 인물(가장 많이 등장하는 둘) 이름. 연출 문법 mainSubject·others 를 센다."""
    counts: dict[str, int] = {}
    for cid in cut_ids:
        g = grammar_by_cut.get(cid)
        if not isinstance(g, dict):
            continue
        subj = str(g.get("mainSubject") or "").strip()
        if subj:
            counts[subj] = counts.get(subj, 0) + 2  # 주인공은 가중
        for other in g.get("others") or []:
            nm = str((other or {}).get("name") or "").strip()
            if nm:
                counts[nm] = counts.get(nm, 0) + 1
    # 장면 actors 이름으로 보강(문법에 아무도 없을 때).
    for a in scene.get("actors") or []:
        nm = str(a.get("name") or "").strip()
        if nm and nm not in counts:
            counts[nm] = 0
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    names = [n for n, _ in ranked]
    a_name = names[0] if names else ""
    b_name = names[1] if len(names) >= 2 else ""
    return a_name, b_name


def _actor_foot(scene: dict, cut: dict, actor_id: str) -> list[float]:
    """그 컷에서 인물의 발 위치(바닥 y=0). timeline actors[].pos."""
    for ca in cut.get("actors") or []:
        if str(ca.get("id")) == str(actor_id):
            pos = ca.get("pos") or [0, 0, 0]
            return [float(pos[0]) if len(pos) > 0 else 0.0, 0.0, float(pos[2]) if len(pos) > 2 else 0.0]
    return [0.0, 0.0, 0.0]


def _name_foot(scene: dict, cut: dict, name: str) -> list[float]:
    """이름으로 그 컷 인물 발 위치를 찾는다(scene.actors 이름 → id → cut pos)."""
    for a in scene.get("actors") or []:
        if str(a.get("name") or "").strip() == str(name).strip():
            return _actor_foot(scene, cut, a.get("id"))
    return [0.0, 0.0, 0.0]


def _actors_in_cut(scene: dict, cut: dict) -> list[dict]:
    """place_cut 입력 actors = [{id, name, heightM, foot, pose, facingDeg}] (그 컷 timeline 기준)."""
    meta_by_id = {str(a.get("id")): a for a in scene.get("actors") or []}
    out: list[dict] = []
    for ca in cut.get("actors") or []:
        aid = str(ca.get("id"))
        meta = meta_by_id.get(aid) or {}
        pos = ca.get("pos") or [0, 0, 0]
        out.append({
            "id": aid,
            "name": str(meta.get("name") or aid),
            "heightM": float(meta.get("heightM") or 1.7),
            "foot": [float(pos[0]) if len(pos) > 0 else 0.0, 0.0, float(pos[2]) if len(pos) > 2 else 0.0],
            "pose": str(ca.get("pose") or "stand"),
            "facingDeg": ca.get("facingDeg"),
        })
    return out


def _merge_override(grammar: dict, override: dict | None) -> dict:
    """읽은 grammar 위에 사용자 지시 수정(override)을 덮어쓴다(읽기 캐시는 그대로)."""
    if not override:
        return grammar
    merged = copy.deepcopy(grammar)
    for field, value in (override or {}).items():
        if field in ("blocking",):  # blocking 은 Astra 배치용이라 grammar 에 안 쓴다.
            continue
        merged[field] = value
    return previz_grammar.normalize_grammar(merged)


def _apply_cut_result(scene: dict, cut: dict, result: dict) -> None:
    """place_cut 결과로 그 컷 camera·actors 를 바꾼다. actors 는 hide 빠짐·move·facingDeg 반영 목록."""
    cut["camera"] = result.get("camera") or cut.get("camera")
    out_actors = result.get("actors") or []
    # place_cut 의 out_actors 는 {id, foot, facingDeg, ...} — timeline actor 모양으로 되돌린다.
    orig_by_id = {str(a.get("id")): a for a in cut.get("actors") or []}
    new_actors: list[dict] = []
    for a in out_actors:
        aid = str(a.get("id"))
        base = dict(orig_by_id.get(aid) or {"id": aid})
        foot = a.get("foot")
        if isinstance(foot, (list, tuple)) and len(foot) >= 3:
            base["pos"] = [round(float(foot[0]), 4), 0.0, round(float(foot[2]), 4)]
        if a.get("facingDeg") is not None:
            base["facingDeg"] = round(float(a.get("facingDeg")), 2)
        new_actors.append(base)
    cut["actors"] = new_actors


# ================================================================ 외형·연기 적용(머리카락·제스처)


def _char_appearance_text(owner: str, char_id: str) -> str:
    """캐릭터 analysis 의 hairEn·appearanceEn 문구를 합쳐 돌려준다(없으면 빈 문자열)."""
    if not char_id:
        return ""
    try:
        from . import characters as char_mod  # noqa: PLC0415

        char, _ = char_mod.load_char(owner, char_id)
    except Exception:  # noqa: BLE001
        return ""
    analysis = (char or {}).get("analysis") or {}
    parts = [str(analysis.get("hairEn") or ""), str(analysis.get("appearanceEn") or "")]
    return " ".join(p for p in parts if p)


def _apply_appearance(owner: str, scenes: list[dict], chars: list[dict]) -> None:
    """각 장면 인물의 hairColor 를 캐릭터 외형(hairEn/appearanceEn)으로 정한다.

    charId 가 있으면 그 캐릭터 analysis 문구에서, 없으면 scene actor 의 appearanceEn 에서 키워드를 찾는다.
    schema 가 이미 기본값을 넣어 뒀으므로, 외형에서 색을 찾았을 때만 덮어쓴다.
    """
    text_by_charid: dict[str, str] = {}
    for scene in scenes or []:
        for actor in scene.get("actors") or []:
            char_id = str(actor.get("charId") or "")
            if char_id and char_id not in text_by_charid:
                text_by_charid[char_id] = _char_appearance_text(owner, char_id)
            appearance = str(actor.get("appearanceEn") or "")
            hair = hair_color_for(text_by_charid.get(char_id, ""), appearance)
            if hair != HAIR_COLOR_DEFAULT:
                actor["hairColor"] = hair
            elif not actor.get("hairColor"):
                actor["hairColor"] = HAIR_COLOR_DEFAULT


def _apply_gestures(scenes: list[dict], groups: list[dict], grammar_by_cut: dict, plan: dict | None = None) -> None:
    """컷마다 보이는 인물의 timeline actor gesture·gestureTarget 을 채운다.

    - grammar.actions 가 있으면 그 컷 action(name→actor)으로 gesture·target 을 쓴다. target 은 상대
      actor id(이름 일치). 말하는 컷(speaking)은 그대로 둔다.
    - grammar.actions 가 없으면(옛 캐시) 콘티 cut.characters[].actionEn 키워드 규칙으로 배정한다.
    - 대사가 있는 컷에서 말하는 인물의 timeline actor 에 다른 제스처가 없으면(위에서 none 이면)
      gesture='turn_head_to_other', gestureTarget=그 컷의 상대(듣는 사람)로 채운다(설계자 2026-10-22,
      054 제스처 재사용). 말하는 인물은 _cut_lines 의 speaker 로 찾고, 상대는 그 컷 timeline 의 다른
      인물로 본다(여럿이면 첫 번째).
    """
    cut_meta: dict[str, dict] = {}
    for group in groups or []:
        for cut in group.get("cuts") or []:
            cut_meta[str(cut.get("cutId") or "")] = cut

    conti_cut_by_id: dict[str, dict] = {}
    if isinstance(plan, dict):
        for c in (plan.get("conti") or {}).get("cuts") or []:
            conti_cut_by_id[str(c.get("id") or "")] = c

    for scene in scenes or []:
        # 이 장면의 이름→actor id(제스처 target 해석용).
        id_by_name: dict[str, str] = {}
        for actor in scene.get("actors") or []:
            name = str(actor.get("name") or "").strip()
            if name:
                id_by_name[name] = str(actor.get("id") or "")
        for cut in scene.get("timeline") or []:
            cut_id = str(cut.get("cutId") or "")
            grammar = grammar_by_cut.get(cut_id) if isinstance(grammar_by_cut, dict) else None
            actions = (grammar or {}).get("actions") if isinstance(grammar, dict) else None
            # name → (gesture, target_name) 맵.
            gesture_by_name: dict[str, tuple[str, str]] = {}
            if actions:
                for act in actions:
                    nm = str((act or {}).get("name") or "").strip()
                    if not nm:
                        continue
                    gesture_by_name[nm] = (
                        str((act or {}).get("gesture") or "none"),
                        str((act or {}).get("target") or "none"),
                    )
            else:
                # 옛 캐시 대비책: 콘티 actionEn 키워드.
                meta = cut_meta.get(cut_id) or {}
                for ch in meta.get("characters") or []:
                    nm = str((ch or {}).get("name") or "").strip()
                    if not nm:
                        continue
                    g = gesture_from_action(str((ch or {}).get("actionEn") or ""))
                    gesture_by_name[nm] = (g, "none")

            name_by_id = {v: k for k, v in id_by_name.items()}
            for ca in cut.get("actors") or []:
                aid = str(ca.get("id") or "")
                nm = name_by_id.get(aid, "")
                entry = gesture_by_name.get(nm)
                if not entry:
                    continue
                gesture, target_name = entry
                if gesture and gesture != "none":
                    ca["gesture"] = gesture
                    target_id = id_by_name.get(target_name.strip()) if target_name and target_name != "none" else None
                    if target_id and target_id != aid:
                        ca["gestureTarget"] = target_id

            # 서버 규칙(설계자 2026-10-22): 대사가 있는 컷에서 말하는 인물에 다른 제스처가 없으면
            # 상대(듣는 사람)를 바라보게 한다. 말하는 인물 = _cut_lines 의 speaker, 상대 = 그 컷
            # timeline 의 다른 actor(첫 번째).
            conti_cut = conti_cut_by_id.get(cut_id)
            if not conti_cut:
                continue
            from . import generate as gen_mod  # noqa: PLC0415 — 순환 import 피하려고 함수 안에서
            speaker_names = {
                str(line.get("speaker") or "").strip()
                for line in gen_mod._cut_lines(plan, conti_cut)
                if not line.get("silent") and str(line.get("speaker") or "").strip()
            }
            if not speaker_names:
                continue
            cut_actor_ids = [str(ca.get("id") or "") for ca in (cut.get("actors") or [])]
            for ca in cut.get("actors") or []:
                aid = str(ca.get("id") or "")
                nm = name_by_id.get(aid, "")
                if nm not in speaker_names:
                    continue
                # 이미 다른 제스처가 붙었으면 두지 않는다.
                if str(ca.get("gesture") or "none") not in ("", "none"):
                    continue
                listener_id = next((other for other in cut_actor_ids if other and other != aid), "")
                if not listener_id:
                    continue
                ca["gesture"] = "turn_head_to_other"
                ca["gestureTarget"] = listener_id


def _place_cameras(owner, pid, scenes, groups, grammar_by_cut, overrides, *, task_id="") -> dict:
    """장소마다 scene_line → 컷마다 place_cut. scenes 를 제자리에서 고치고 fit dict 를 돌려준다.

    fit[cutId] = {match, method, lens, distanceM, vantageDeg, side, crossed, cheats, wallClamped}.
    grammar 없는 컷은 Astra 카메라를 그대로 두고 method "astra", match null.
    """
    fit: dict[str, dict] = {}
    for scene_index, scene in enumerate(scenes):
        group = groups[scene_index] if scene_index < len(groups) else {"cuts": []}
        timeline = scene.get("timeline") or []
        cut_ids = [str(c.get("cutId") or "") for c in timeline]
        room = scene.get("room") or {}

        # 핵심 두 인물·라인 쪽. override 적용된 grammar 로 센다.
        merged_by_cut: dict[str, dict] = {}
        for cid in cut_ids:
            g = grammar_by_cut.get(cid)
            if isinstance(g, dict):
                merged_by_cut[cid] = _merge_override(g, overrides.get(cid))
        a_name, b_name = _scene_key_pair(merged_by_cut, cut_ids, scene)
        # 두 핵심 인물의 기본 자리(첫 유효 컷에서).
        a_pos = b_pos = [0.0, 0.0, 0.0]
        for cut in timeline:
            if a_name:
                a_pos = _name_foot(scene, cut, a_name)
            if b_name:
                b_pos = _name_foot(scene, cut, b_name)
            if a_name and b_name:
                break
        line = previz_camera.scene_line(merged_by_cut, a_name, b_name, a_pos, b_pos)
        base_side = int(line.get("side") or 1)
        crossed_set = set(line.get("crossed") or [])

        for cut in timeline:
            cut_id = str(cut.get("cutId") or "")
            g = merged_by_cut.get(cut_id)
            if not isinstance(g, dict):
                # 읽기 실패 컷: Astra 카메라 유지, method astra, match null(판정 불가).
                fit[cut_id] = {"match": None, "method": "astra"}
                if task_id:
                    _bump_progress(owner, pid, task_id, step="camera")
                continue
            crossed = cut_id in crossed_set
            g_local = dict(g)
            g_local["_crossed"] = crossed
            side = -base_side if crossed else base_side
            # 익스트림 클로즈업 하한(설계자 2026-10-22): 마네킹은 ECU 로는 정보가 없다(하늘색 공 하나).
            # place_cut 결과가 ECU 면 프리비즈는 한 단계 넓은 CU 로 렌더하고, fit 에 pushInTo:"ECU" 를
            # 남긴다(⑤ shot_brief 가 읽어 프롬프트가 "push in to an extreme close-up" 으로 쓴다).
            push_in_to = ""
            if str(g_local.get("shotSize") or "") == "ECU":
                g_local["shotSize"] = "CU"
                push_in_to = "ECU"
            try:
                result = previz_camera.place_cut(g_local, _actors_in_cut(scene, cut), room, side)
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.previz.place_cut.failed cut=%s err=%s", cut_id, str(exc)[:200])
                fit[cut_id] = {"match": None, "method": "astra"}
                if task_id:
                    _bump_progress(owner, pid, task_id, step="camera")
                continue
            _apply_cut_result(scene, cut, result)
            rfit = result.get("fit") or {}
            fit[cut_id] = {
                "match": rfit.get("match"),
                "method": "grammar",
                "lens": rfit.get("lens"),
                "distanceM": rfit.get("distanceM"),
                "vantageDeg": rfit.get("vantageDeg"),
                "side": rfit.get("side"),
                "crossed": bool(crossed),
                "cheats": result.get("cheats") or [],
                "wallClamped": bool(rfit.get("wallClamped")),
                "occluded": bool(rfit.get("occluded")),
                "extraVisible": list(rfit.get("extraVisible") or []),
                "minBodyGapM": rfit.get("minBodyGapM"),
                "needsLean": bool(rfit.get("needsLean")),
            }
            if push_in_to:
                fit[cut_id]["pushInTo"] = push_in_to
            if cut_id in overrides:
                fit[cut_id]["override"] = True
            if task_id:
                _bump_progress(owner, pid, task_id, step="camera")
    return fit


# ================================================================ 다시 만들기: 연출 문법 수정 목록(edits)


def _apply_edits(edits: list[dict], groups: list[dict], overrides: dict) -> tuple[dict, set, list]:
    """edits 를 grammarOverrides(dict)로 쌓고, reread 컷 집합·blocking 문장 목록을 뽑는다.

    edits: [{cutId|"all", field, value, noteEn, reread?, blocking?}]. "all" 은 모든 컷에 적용.
    반환 (overrides, reread_cuts, blocking_notes). blocking_notes = [{cutId, text}].
    """
    overrides = dict(overrides or {})
    reread: set[str] = set()
    blocking: list[dict] = []
    all_cut_ids = [str(c.get("cutId") or "") for g in groups for c in g.get("cuts") or []]
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        cid = str(edit.get("cutId") or "all").strip() or "all"
        targets = all_cut_ids if cid == "all" else ([cid] if cid in all_cut_ids else [])
        if edit.get("reread"):
            reread.update(targets)
        field = str(edit.get("field") or "").strip()
        value = edit.get("value")
        if field == "blocking":
            text = str(value or edit.get("noteEn") or "").strip()
            if text:
                for t in targets:
                    blocking.append({"cutId": t, "text": text})
            continue
        if field and value is not None and field in _GRAMMAR_EDIT_FIELDS:
            for t in targets:
                overrides.setdefault(t, {})[field] = value
    return overrides, reread, blocking


def _blocking_for_group(blocking_notes: list[dict], group: dict) -> list[str]:
    """이 장소 그룹 컷에 해당하는 blocking 문장(Astra 배치 프롬프트용)."""
    group_cut_ids = {str(c.get("cutId") or "") for c in group.get("cuts") or []}
    out: list[str] = []
    for note in blocking_notes or []:
        if str(note.get("cutId") or "") in group_cut_ids:
            text = str(note.get("text") or "").strip()
            if text:
                out.append(text)
    return out


def _rebuild_edits(owner, pid, plan_id, extra_prompt_ko, groups, prev_scenes) -> list[dict]:
    """사용자 한국어 지시를 '연출 문법 수정 목록'(edits)으로 바꾼다. Astra 먼저, 실패하면 Claude.

    컷 라벨·연출 문법 열거값을 함께 줘서 모델이 올바른 cutId·field·value 를 쓰게 한다.
    실패하면 "all" 대상 reread 하나(그 컷들 grammar 를 다시 읽기)로 돌려준다.
    """
    lines = [
        "사용자가 3D 프리비즈의 '연출'을 이렇게 바꿔 달라고 했습니다(한국어). 카메라 숫자는 서버가 "
        "연출 문법으로 계산하므로, 이 지시를 '연출 문법 수정 목록'(edits)으로 바꿔 emit_edits 로 주세요.",
        "",
        f"[사용자 지시] {extra_prompt_ko}",
        "",
        "[edits 규칙] 각 항목 {cutId(또는 \"all\"), field, value, noteEn}. field·value 허용값:",
        "- shotSize: ECU|CU|MCU|MS|MLS|FS|LS",
        "- shotType: clean_single|dirty_single|over_shoulder|two_shot|profile_two_shot|pov|insert|establishing",
        "- vantage: front|front3q|3q|side|back3q|back",
        "- cameraHeight: low|eye|high|overhead",
        "- mainSubject: 인물 이름(역할명)",
        "- offscreenPartnerSide: left|right|front_of_camera|none",
        "- headTopY: 0~0.9 숫자(머리 위 화면 위치), subjectX: 0~1 숫자(주인공 좌우)",
        "- blocking: 인물 자리 지시 문장(value 에 영어 문장). 자리·앉고 서기·마주봄 등.",
        "컷 전체가 '시점이 안 맞음' 같은 모호한 지시면 field 없이 reread:true 로 그 컷 grammar 를 다시 읽게 하세요.",
        "예: '범건우 옆모습만' → {cutId:\"cut-01\", field:\"vantage\", value:\"side\"}.",
        "",
        "[컷 목록(cutId 를 그대로 쓰세요)]",
    ]
    cut_index = 0
    for group in groups:
        for cut in group["cuts"]:
            cut_index += 1
            lines.append(f"- cutId={cut['cutId']} (컷{cut_index}, 장소={group['locationEn']})")
    summaries = [s for s in (_scene_summary(ps) for ps in prev_scenes) if s]
    if summaries:
        lines += ["", "[이전 설계 요약]"] + summaries
    blocks = [{"text": "\n".join(lines)}]
    for model in (ASTRA_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _changes(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.edits.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        used = str(usage.get("model") or model)
        _charge(owner, pid, plan_id, usage, used)
        edits = _normalize_edits(payload)
        if edits:
            return edits
    # 변환 실패 → 전체 컷 grammar 를 다시 읽기(reread)로 처리.
    log.warning("vnew.previz.edits.fallback pid=%s", pid)
    return [{"cutId": "all", "reread": True}]


def _rebuild_changes(owner: str, pid: str, plan_id: str, extra_prompt_ko: str, groups: list[dict], prev_scenes: list[dict]) -> list[dict]:
    """사용자 한국어 지시를 구체적 영어 변경 목록으로 바꾼다. Astra 먼저, 실패하면 Claude.

    컷 라벨(cutId·장소·순서)과 이전 설계 요약을 함께 줘서 모델이 올바른 cutId 를 쓰게 한다.
    실패하면 "all" 대상 하나로 원문 지시를 그대로 담아 돌려준다(설계 프롬프트에 지시가 들어가게).
    """
    lines = [
        "사용자가 3D 프리비즈 장면을 이렇게 바꿔 달라고 했습니다(한국어). 이를 레이아웃 엔진이 따를 수 "
        "있는 구체적인 영어 변경 목록으로 바꿔 emit_changes 로 주세요. 모호한 말은 수치·대상·방향으로.",
        "",
        f"[사용자 지시] {extra_prompt_ko}",
        "",
        "[컷 목록(cutId 를 그대로 쓰세요)]",
    ]
    cut_index = 0
    for group in groups:
        for cut in group["cuts"]:
            cut_index += 1
            lines.append(f"- cutId={cut['cutId']} (컷{cut_index}, 장소={group['locationEn']})")
    summaries = [s for s in (_scene_summary(ps) for ps in prev_scenes) if s]
    if summaries:
        lines += ["", "[이전 설계 요약]"] + summaries
    blocks = [{"text": "\n".join(lines)}]
    for model in (ASTRA_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _changes(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.changes.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        used = str(usage.get("model") or model)
        _charge(owner, pid, plan_id, usage, used)
        changes = _normalize_changes(payload)
        if changes:
            return changes
    # 변환 실패(둘 다) → 원문 지시를 'all' 대상 하나로 담아 설계 프롬프트에 그대로 전달.
    log.warning("vnew.previz.changes.fallback pid=%s", pid)
    return [{"cutId": "all", "whatEn": extra_prompt_ko}]


def _group_index_for_cut(groups: list[dict], cut_id: str) -> int | None:
    """cut_id 가 속한 장소 그룹 인덱스(없으면 None)."""
    for index, group in enumerate(groups):
        for cut in group.get("cuts") or []:
            if str(cut.get("cutId") or "") == cut_id:
                return index
    return None


def _user_changed_cut_ids(changes_en: list[dict], groups: list[dict], target_group_index: int | None) -> set[str]:
    """사용자 지시로 바뀐 컷 cutId 집합. "all" 이면 대상 그룹(없으면 전체) 모든 컷."""
    changed: set[str] = set()
    if not changes_en:
        return changed
    all_cut_ids = [str(c.get("cutId") or "") for g in groups for c in g.get("cuts") or []]
    target_cut_ids = all_cut_ids
    if target_group_index is not None and 0 <= target_group_index < len(groups):
        target_cut_ids = [str(c.get("cutId") or "") for c in groups[target_group_index].get("cuts") or []]
    for change in changes_en:
        cid = str(change.get("cutId") or "all")
        if cid == "all":
            changed.update(target_cut_ids)
        elif cid in all_cut_ids:
            changed.add(cid)
    return changed


def _changes_for_group(changes_en: list[dict], group: dict) -> list[dict]:
    """이 장소 그룹에 해당하는 변경만 추린다(그 그룹 컷 cutId 또는 "all")."""
    group_cut_ids = {str(c.get("cutId") or "") for c in group.get("cuts") or []}
    out: list[dict] = []
    for change in changes_en:
        cid = str(change.get("cutId") or "all")
        if cid == "all" or cid in group_cut_ids:
            out.append(change)
    return out


def _scene_summary(scene: dict | None) -> str:
    """이전 scene JSON 을 짧은 문단으로 요약한다(room·props·actors 자리·컷별 camera). 없으면 빈 문자열."""
    if not isinstance(scene, dict):
        return ""
    lines: list[str] = []
    room = scene.get("room") or {}
    lines.append(
        f"room width={room.get('width')} depth={room.get('depth')} height={room.get('height')}"
    )
    props = scene.get("props") or []
    if props:
        lines.append("props: " + ", ".join(
            f"{p.get('labelEn') or p.get('id')}@{p.get('pos')}" for p in props[:8]
        ))
    actors = scene.get("actors") or []
    if actors:
        lines.append("actors: " + ", ".join(
            f"{a.get('name') or a.get('id')}({a.get('color')})" for a in actors
        ))
    for cut in scene.get("timeline") or []:
        cam = cut.get("camera") or {}
        lines.append(
            f"cut {cut.get('cutId')}: camera pos={cam.get('pos')} target={cam.get('target')}"
            f" fov={cam.get('fov')}"
        )
    return "\n".join(lines)


def _design_scene(owner: str, pid: str, plan_id: str, blocks: list[dict]) -> tuple[dict, str]:
    """Astra 먼저, 실패하면 Claude. 사용량을 원장에 남긴다. (payload, model)."""
    for model in (ASTRA_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _design(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.design.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        used = str(usage.get("model") or model)
        _charge(owner, pid, plan_id, usage, used)
        return payload, used
    raise VpocError("vnew.previz.design", "DesignFailed", "장면 설계에 실패했습니다.", 502)


# ================================================================ 투영 검산·보정(previz_fit)


def _cut_subjects(scene: dict, cut: dict, shot: dict) -> list[dict]:
    """한 컷의 투영 검산 subjects = [{name, actorId, foot, heightM, target}].

    패널 샷 subjects 를 scene actors(이름 우선, 없으면 순서)와 맞춘다. actor 의 발 위치는 그 컷
    timeline actors[].pos(바닥 y=0), 키는 scene.actors[].heightM.
    """
    scene_actors = {str(a.get("id")): a for a in scene.get("actors") or []}
    cut_actors = cut.get("actors") or []
    cut_actor_by_id = {str(ca.get("id")): ca for ca in cut_actors}
    # 이름(소문자) → actorId.
    name_to_id: dict[str, str] = {}
    for a in scene.get("actors") or []:
        nm = str(a.get("name") or "").strip().lower()
        if nm:
            name_to_id.setdefault(nm, str(a.get("id")))

    subjects: list[dict] = []
    shot_subjects = shot.get("subjects") or []
    for index, subj in enumerate(shot_subjects):
        subj_name = str(subj.get("name") or "").strip().lower()
        actor_id = name_to_id.get(subj_name, "")
        if not actor_id and index < len(cut_actors):
            actor_id = str(cut_actors[index].get("id"))
        cut_actor = cut_actor_by_id.get(actor_id)
        if cut_actor is None:
            continue
        meta = scene_actors.get(actor_id) or {}
        subjects.append(
            {
                "name": str(subj.get("name") or meta.get("name") or actor_id),
                "actorId": actor_id,
                "foot": list(cut_actor.get("pos") or [0, 0, 0]),
                "heightM": float(meta.get("heightM") or 1.7),
                "target": {
                    "screenX": subj.get("screenX"),
                    "screenY": subj.get("screenY"),
                    "heightFrac": subj.get("heightFrac"),
                },
            }
        )
    return subjects


def _room_bounds_for(room: dict) -> dict:
    """카메라 격자 탐색 범위. 카메라는 방 밖에서 들여다볼 수 있으므로 방 크기에 넉넉한 여유를 둔다.

    (인물·소품은 방 안으로 제한하지만, 카메라는 벽 밖·방보다 멀리 설 수 있다.) y 는 바닥 아래로
    내려가지 않게 0.3m 하한만 둔다(천장보다 높이 들 수도 있어 위로는 넉넉히).
    """
    width = float(room.get("width") or 6.0)
    depth = float(room.get("depth") or 6.0)
    height = float(room.get("height") or 2.8)
    margin = 10.0  # 방 밖으로 카메라가 설 수 있는 여유(m).
    half_w = width / 2.0 + margin
    half_d = depth / 2.0 + margin
    return {
        "xmin": -half_w, "xmax": half_w,
        "zmin": -half_d, "zmax": half_d,
        "ymin": 0.3, "ymax": max(0.3, height) + margin,
    }


def _retry_table(subjects: list[dict], camera: dict) -> dict:
    """오차 큰 컷의 '현재 투영값 vs 목표값' 표(Astra 재요청용)."""
    rows: list[dict] = []
    for subj in subjects:
        projected = previz_fit.project_actor(camera, subj.get("foot") or [0, 0, 0], float(subj.get("heightM") or 1.7))
        tgt = subj.get("target") or {}
        if projected is None:
            cur_x = cur_y = cur_h = "화면밖"
        else:
            cur_x = round(projected["screenX"], 3)
            cur_y = round(projected["headY"], 3)
            cur_h = round(projected["heightFrac"], 3)
        rows.append(
            {
                "name": subj.get("name"),
                "curX": cur_x, "curY": cur_y, "curH": cur_h,
                "tgtX": tgt.get("screenX"), "tgtY": tgt.get("screenY"), "tgtH": tgt.get("heightFrac"),
            }
        )
    return {"subjects": rows}


def _fit_scenes(owner, pid, plan_id, scenes, groups, scene_specs, panel_shots, script, chars, plan, *, user_changed_cuts=None, task_id="") -> dict:
    """컷별 투영 오차를 재서 임계 넘는 컷만 Astra 재요청(최대 2회)→그래도 크면 격자 보정.

    scenes 를 제자리에서 고치고 fit = {cutId: {err, method}} 를 돌려준다.
    method: "astra"(처음부터 맞음)·"astra_retry"(재요청으로 맞음)·"grid"(격자 보정)·"user"(사용자
    지시로 바뀐 컷 — 되돌리지 않게 재요청·보정을 하지 않고 오차만 잰다).
    user_changed_cuts: 사용자 지시로 바뀐 cutId 집합(격자 보정·자동 재요청 제외). task_id: progress 용.
    """
    user_changed_cuts = user_changed_cuts or set()
    fit: dict[str, dict] = {}
    # cutId → (sceneIndex, timelineIndex) 로 빨리 찾게.
    for scene_index, scene in enumerate(scenes):
        group = groups[scene_index] if scene_index < len(groups) else {"cuts": []}
        loc = _location_for(plan, group.get("locationEn") or "")
        name_en = group.get("locationEn") or ""
        bounds = _room_bounds_for(scene.get("room") or {})
        for tl_index, cut in enumerate(scene.get("timeline") or []):
            cut_id = str(cut.get("cutId") or "")
            shot = panel_shots.get(cut_id)
            if not shot or not (shot.get("subjects") or []):
                fit[cut_id] = {"err": 0.0, "method": "astra"}
                if task_id:
                    _bump_progress(owner, pid, task_id, step="fit")
                continue
            subjects = _cut_subjects(scene, cut, shot)
            if not subjects:
                fit[cut_id] = {"err": 0.0, "method": "astra"}
                if task_id:
                    _bump_progress(owner, pid, task_id, step="fit")
                continue
            err = previz_fit.cut_error(cut.get("camera") or {}, subjects)
            # 사용자 지시로 바뀐 컷: 격자 보정·자동 재요청을 하지 않는다(지시를 되돌리지 않게).
            # method="user" 로 표시하고 오차만 잰다.
            if cut_id in user_changed_cuts:
                fit[cut_id] = {
                    "err": round(float(err.get("err") or 0.0), 4),
                    "posErr": round(float(err.get("posErr") or 0.0), 4),
                    "sizeErr": round(float(err.get("sizeErr") or 0.0), 4),
                    "method": "user",
                }
                if task_id:
                    _bump_progress(owner, pid, task_id, step="fit")
                continue
            method = "astra"
            # --- Astra 재요청(그 컷 camera 만) 최대 FIT_MAX_ASTRA_RETRY 회 ---
            retries = 0
            while previz_fit.exceeds_threshold(err) and retries < FIT_MAX_ASTRA_RETRY:
                retries += 1
                if task_id:
                    _set_step(owner, pid, task_id, step="fit_retry")
                retry_cut = dict(_retry_table(subjects, cut.get("camera") or {}))
                retry_cut["cutId"] = cut_id
                group_cut_specs = [{"cutId": str(c["cutId"]), "seconds": c["seconds"]} for c in group.get("cuts") or []]
                blocks = [{"text": _design_prompt(name_en, loc, group.get("cuts") or [], script, chars, panel_shots, retry_cuts=[retry_cut])}]
                try:
                    payload, _m = _design_scene(owner, pid, plan_id, blocks)
                except VpocError:
                    break
                new_cam = _camera_for_cut(payload, cut_id, scene.get("room") or {})
                if new_cam is None:
                    continue
                new_err = previz_fit.cut_error(new_cam, subjects)
                if new_err["err"] < err["err"] - 1e-9:
                    cut["camera"] = new_cam
                    err = new_err
                    method = "astra_retry"
            # --- 그래도 크면 격자 보정(결정적) ---
            if previz_fit.exceeds_threshold(err):
                refined = previz_fit.grid_refine_camera(cut.get("camera") or {}, subjects, bounds=bounds)
                refined_err = previz_fit.cut_error(refined, subjects)
                if refined_err["err"] < err["err"] - 1e-9:
                    cut["camera"] = _clamp_camera(refined, scene.get("room") or {})
                    err = refined_err
                    method = "grid"
            fit[cut_id] = {
                "err": round(float(err.get("err") or 0.0), 4),
                "posErr": round(float(err.get("posErr") or 0.0), 4),
                "sizeErr": round(float(err.get("sizeErr") or 0.0), 4),
                "method": method,
            }
            if task_id:
                _bump_progress(owner, pid, task_id, step="fit")
    return fit


def _camera_for_cut(payload: dict, cut_id: str, room: dict) -> dict | None:
    """Astra 재요청 payload 의 timeline 에서 cut_id 카메라를 꺼내 정리한다. 없으면 None."""
    if not isinstance(payload, dict):
        return None
    for item in payload.get("timeline") or []:
        if isinstance(item, dict) and str(item.get("cutId") or "") == cut_id:
            return _clamp_camera(previz_schema._normalize_camera(item.get("camera") or {}, room), room)  # noqa: SLF001
    # cutId 없이 camera 만 돌려줬으면 그걸 쓴다.
    cam = payload.get("camera")
    if isinstance(cam, dict):
        return _clamp_camera(previz_schema._normalize_camera(cam, room), room)  # noqa: SLF001
    return None


def _clamp_camera(camera: dict, room: dict) -> dict:
    """카메라 fov 를 schema 범위로 자르고 pos/target 숫자를 반올림한다(저장 모양 유지)."""
    return previz_schema._normalize_camera(camera, room)  # noqa: SLF001


def _design_prompt(
    name_en: str,
    loc: dict | None,
    cuts: list[dict],
    script: dict,
    chars: list[dict],
    grammar_by_cut: dict | None = None,
    *,
    previous_scene: str | None = None,
    is_rebuild: bool = False,
    blocking: list[str] | None = None,
    room_height: tuple[float, float] | None = None,
) -> str:
    """한 장소(연속 컷)의 설계 요청 문단(049). 카메라는 서버가 계산하므로 Astra 는 인물 자리·자세·
    서로 보는 방향·소품만 정확히 쓴다. 컷마다 패널 연출 문법 요약(주인공·샷 종류·상대 쪽)을 함께 준다.

    grammar_by_cut = {cutId: grammar} — Claude 가 읽은 연출 문법(previz_grammar.normalize_grammar).
    previous_scene = 이전 설계 요약(다시 만들기 '[이전 설계]' 문단).
    is_rebuild = 다시 만들기이면 '이전과 다른 대안' 요구 문장을 넣는다.
    blocking = [영어 문장] — 사용자 지시의 인물 자리(배치) 문장(Astra 배치 프롬프트에 넣는다).
    room_height = (low, high) — 방 높이 clamp 범위(프롬프트에 알려 둔다).
    """
    grammar_by_cut = grammar_by_cut or {}
    setting = script.get("setting") or {}
    entries = script.get("entries") or []
    room_w = room_d = room_h = "?"
    if loc:
        _d = loc.get("design") or {}
        room_w, room_d, room_h = _d.get("widthM", "?"), _d.get("depthM", "?"), _d.get("heightM", "?")
    height_lo, height_hi = (room_height or (ROOM_HEIGHT_ENCLOSED_MIN, ROOM_HEIGHT_ENCLOSED_MAX))
    lines = [
        f"[장소] {name_en or '(이름 없음)'}",
        "이 장소에서 이어지는 콘티 컷들을 하나의 3D 장면으로 설계하세요. timeline 은 아래 컷과 1:1 로,"
        " 주어진 cutId 를 그대로 쓰세요. 컷이 바뀌어도 방·소품·인물 자리는 이어집니다.",
        "",
        "[카메라는 서버가 계산합니다] 이 장면의 카메라 숫자(pos·target·fov)는 서버가 영화 연출 문법으로 "
        "정합니다. 그러니 각 컷의 camera 는 대충(장면 중앙을 보는 기본값) 둬도 됩니다. 대신 인물 자리(pos)·"
        "자세(pose: sit/stand/kneel/lie)·서로 보는 방향(facingDeg, 0=+z 시계방향)·소품을 웹툰 상황에 맞게 "
        "정확히 쓰세요(누가 누구 옆/맞은편에 앉고 서는지). 컷마다 actors 에는 그 장면 인물 전원을 기본 "
        "자리로 넣으세요(카메라는 서버가 필요한 사람만 보이게 합니다).",
        "",
        "[좌표계·치수] x 오른쪽(+), y 위(+), z 카메라 쪽(+). north 벽은 z = -depth/2. 단위 m."
        f" 방 크기 width≈{room_w} depth≈{room_d}."
        f" 방 높이(height)는 {'닫힌 방' if setting.get('enclosed') else '열린 공간'}이라 {height_lo}~{height_hi}m 사이로"
        " 두세요(천장을 10m 처럼 터무니없이 높게 두지 마세요).",
        "",
        "[공간 배치(대본)]",
        f"장소 분위기: {setting.get('moodKo', '')} / 시간: {setting.get('timeKo', '')}",
        f"공간: {'닫힌 방' if setting.get('enclosed') else '열린 공간'}",
        f"배치: {setting.get('layoutKo', '')} / {setting.get('layoutEn', '')}",
    ]
    if loc:
        design = loc.get("design") or {}
        walls = design.get("walls") or {}
        lines += [
            "",
            "[장소 설계(공간 시트)]",
            f"크기(m): width≈{design.get('widthM', '?')} depth≈{design.get('depthM', '?')} height≈{design.get('heightM', '?')}",
            f"바닥: {design.get('floorEn', '')} / 천장: {design.get('ceilingEn', '')}",
            f"빛: {design.get('lightEn', '')}",
        ]
        for side in previz_schema.SIDES:
            w = walls.get(side) or {}
            lines.append(
                f"- {side} 벽: {w.get('descEn', '')}"
                + (" [창문]" if w.get("hasWindow") else "")
                + (" [문]" if w.get("hasDoor") else "")
            )
        props = design.get("propsEn") or []
        if props:
            lines.append("소품: " + ", ".join(f"{p.get('labelEn', '')}({p.get('wall', '')})" for p in props))
    lines += ["", "[등장 인물]"]
    if chars:
        for item in chars:
            lines.append(f"- {item.get('roleName', '')} (charId={item.get('charId', '')})")
    else:
        lines.append("(연결된 캐릭터 없음 — 대본 화자 이름을 쓰세요)")
    lines += ["", "[콘티 컷(순서대로 — 각 컷을 timeline 한 항목으로)]"]
    for index, cut in enumerate(cuts, start=1):
        char_pos = _entry_positions(cut.get("panels") or [], entries)
        lines.append(
            f"{index}. cutId={cut['cutId']} seconds={cut['seconds']}"
            f" / 샷={cut.get('shotKo', '')} / 상황={cut.get('situationKo', '')}"
            + (f" / 인물위치={char_pos}" if char_pos else "")
        )
        grammar = grammar_by_cut.get(str(cut["cutId"]))
        if isinstance(grammar, dict):
            others = ", ".join(
                f"{o.get('name')}({o.get('how')})" for o in (grammar.get("others") or []) if o.get("name")
            )
            lines.append(
                f"   [패널 연출 문법] 주인공={grammar.get('mainSubject') or '(인물)'}"
                f" / 샷={grammar.get('shotSize')} {grammar.get('shotType')}"
                f" / 각도={grammar.get('vantage')} / 높이={grammar.get('cameraHeight')}"
                f" / 상대쪽={grammar.get('offscreenPartnerSide')}"
                + (f" / 함께={others}" if others else "")
            )
            lines.append(
                "   위 문법은 '이 컷에서 누가 주인공이고 누구를 보는지'의 근거입니다. 그 주인공과 상대가"
                " 서로 옆/맞은편에 오도록 인물 자리·facingDeg 를 두세요(카메라는 서버가 계산합니다)."
            )
    lines += [
        "",
        "인물은 고유의 밝은 색(서로 확실히 다르게)으로, 배경은 하나의 덩어리(회색조), 빛은 어디서 오는지"
        " light 로 표기하세요. 컷 안에서 인물이 걸으면 actor.moveTo 로.",
    ]
    # --- 사용자 blocking(인물 자리 지시) ---
    blocking = blocking or []
    if blocking:
        lines += [
            "",
            "[사용자 배치 지시 — 반드시 반영] 아래 인물 자리 지시를 정확히 반영하세요.",
        ]
        for note in blocking:
            lines.append(f"- {note}")
    # --- 다시 만들기: 이전 설계·대안 요구 ---
    if is_rebuild and previous_scene:
        lines += [
            "",
            "[이전 설계] 아래는 방금 만든 이 장소의 장면입니다(참고). 사용자 지시가 겨냥한 부분은 반드시 "
            "바꾸고, 지시가 없더라도 이전과 똑같이 수렴하지 말고 다른 배치 대안을 내세요.",
            previous_scene,
        ]
    elif is_rebuild:
        lines += [
            "",
            "[다시 만들기] 사용자가 이전 결과가 마음에 들지 않아 다시 요청했습니다. 패널 연출 문법(주인공·"
            "샷 종류·상대 쪽)은 유지하되, 이전과 다른 인물 배치 대안을 내세요(같은 결과로 수렴하지 마세요).",
        ]
    return "\n".join(lines)


def _entry_positions(cut_panels: list[int], entries: list[dict]) -> str:
    """이 컷 panels 와 겹치는 대본 entries 의 인물 positionEn 요약."""
    cut_set = set(cut_panels or [])
    parts: list[str] = []
    for entry in entries:
        if cut_set & set(entry.get("panels") or []):
            for char in entry.get("characters") or []:
                name = str(char.get("name") or "")
                pos = str(char.get("positionEn") or "")
                if name or pos:
                    parts.append(f"{name}:{pos}")
    return "; ".join(parts)


# ================================================================ 패널 샷 분석(컷마다 Claude)


def _all_tiles(owner: str, pid: str) -> list[bytes]:
    """업로드 웹툰을 통째로 분석 타일(세로 1,568px 조각)로 자른다. panels 번호가 가리키는 조각.

    locations._all_tiles 와 같은 규칙(order 순서대로 tiles_for_analysis). 못 읽은 파일은 건너뛴다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        data, _ = plan_mod.load_plan(owner, pid)
    except Exception:  # noqa: BLE001
        return []
    order = list(((data.get("input") or {}).get("order")) or [])
    tiles: list[bytes] = []
    for file_id in order:
        try:
            jpeg = STORE.get_bytes(plan_mod.upload_key(owner, pid, file_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.tiles.missing file=%s err=%s", file_id, str(exc)[:150])
            continue
        tiles.extend(vpoc_images.tiles_for_analysis(jpeg))
    return tiles


def _cut_tiles(cut_panels: list[int], all_tiles: list[bytes]) -> list[bytes]:
    """이 컷 panels 가 가리키는 조각들 중 그림이 큰(인물 적은) 조각 우선 최대 MAX_PANEL_TILES 장.

    panels 는 '분석 타일 번호'다. 번호가 범위를 벗어나면 건너뛴다. 말풍선·인물이 많은 조각보다
    배경(그림)이 큰 조각을 먼저 고른다 — characters._count_people 로 사람 수가 적은 쪽 우선.
    """
    from . import characters as char_mod  # noqa: PLC0415

    picked: list[bytes] = []
    for index in [int(p) for p in (cut_panels or []) if isinstance(p, int) or (isinstance(p, str) and p.isdigit())]:
        if 0 <= index < len(all_tiles):
            picked.append(all_tiles[index])
    if not picked:
        return []
    scored: list[tuple[int, int, bytes]] = []
    for order_index, jpeg in enumerate(picked):
        try:
            count = char_mod._count_people(jpeg)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            count = None
        rank = 99 if count is None else int(count)
        scored.append((rank, order_index, jpeg))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored][:MAX_PANEL_TILES]


def _role_names(chars: list[dict]) -> list[str]:
    return [str(c.get("roleName") or "").strip() for c in (chars or []) if str(c.get("roleName") or "").strip()]


def _analyze_cut_panel(owner: str, pid: str, plan_id: str, cut: dict, all_tiles: list[bytes], role_names: list[str]) -> dict | None:
    """한 컷의 웹툰 조각(최대 2장)을 Claude 분석 체인에 보내 샷 JSON 을 받는다. 없으면 None.

    사용량은 원장에 남긴다. 이미지가 없으면(panels 범위 밖) 분석을 건너뛴다(None).
    """
    tiles = _cut_tiles(cut.get("panels") or [], all_tiles)
    if not tiles:
        return None
    intro = [
        "아래는 웹툰 한 컷(같은 순간의 조각 1~2장)입니다. 이 컷의 카메라 샷(크기·높이·각도)과 "
        "보이는 인물들의 화면 위치(screenX·머리 screenY·heightFrac)를 읽어 emit_shot 으로 주세요.",
    ]
    if role_names:
        intro.append("등장할 수 있는 인물(역할명): " + ", ".join(role_names))
    situation = str(cut.get("situationKo") or "").strip()
    if situation:
        intro.append(f"상황(참고): {situation}")
    blocks: list[dict] = [{"text": "\n".join(intro)}]
    for jpeg in tiles:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})
    try:
        payload, usage = _analyze_panel(blocks, model=PANEL_ANALYZE_MODEL)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.panel.failed cut=%s err=%s", cut.get("cutId"), str(exc)[:200])
        return None
    used = str(usage.get("model") or PANEL_ANALYZE_MODEL)
    _charge(owner, pid, plan_id, usage, used)
    return _normalize_shot(payload)


def _analyze_panels(owner: str, pid: str, plan_id: str, groups: list[dict], chars: list[dict], *, task_id: str = "") -> dict:
    """모든 컷의 패널 샷을 분석해 {cutId: shot} 로. 이미지가 없는 컷은 뺀다.

    task_id 를 주면 컷 하나 분석이 끝날 때마다 progress(panels) 를 +1 한다.
    """
    all_tiles = _all_tiles(owner, pid)
    role_names = _role_names(chars)
    shots: dict[str, dict] = {}
    for group in groups:
        for cut in group["cuts"]:
            shot = _analyze_cut_panel(owner, pid, plan_id, cut, all_tiles, role_names)
            if shot is not None:
                shots[str(cut["cutId"])] = shot
            if task_id:
                _bump_progress(owner, pid, task_id, step="panels")
    return shots


# ================================================================ 저장(task.id 확인)


def _commit_scenes(owner: str, pid: str, task_id: str, scenes: list[dict], model: str, *, fit: dict | None = None, panel_grammar: dict | None = None, grammar_overrides: dict | None = None, last_rebuild: dict | None = None) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.setdefault("previz", new_previz())
        if (previz.get("task") or {}).get("id") != task_id:
            raise _Discard()
        previz["scenes"] = scenes
        previz["panelShots"] = {}  # 049: 패널 샷(옛) 대신 panelGrammar 를 쓴다.
        previz["panelGrammar"] = panel_grammar or {}
        previz["grammarOverrides"] = grammar_overrides or {}
        previz["fit"] = fit or {}
        previz["status"] = "ready"
        previz["error"] = ""
        previz["model"] = model
        previz["approved"] = False  # 새로 설계하면 승인 해제.
        previz["approvedForced"] = None  # 강제 승인 기록도 지운다(새 설계는 새 판정).
        previz["revision"] = int(previz.get("revision") or 0) + 1
        previz["recordings"] = {}  # revision 이 바뀌면 녹화 키 경로가 달라진다.
        previz["lastRebuild"] = last_rebuild  # 다시 만들기 요약(첫 설계면 None).
        # progress 를 done 으로 마무리(막대 가득).
        prog = previz.get("progress") or _empty_progress()
        total = int(prog.get("total") or 0)
        previz["progress"] = {
            "step": "done",
            "done": total,
            "total": total,
            "startedAt": prog.get("startedAt"),
            "updatedAt": now_iso(),
        }
        previz["task"] = {"status": "done", "startedAt": (previz.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.previz.design.discarded pid=%s task=%s", pid, task_id)


def _fail(owner: str, pid: str, task_id: str, message: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.setdefault("previz", new_previz())
        if (previz.get("task") or {}).get("id") != task_id:
            raise _Discard()
        previz["status"] = "failed"
        previz["error"] = message
        previz["task"] = {"status": "error", "startedAt": (previz.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.previz.design.discarded_fail pid=%s task=%s", pid, task_id)


class _Discard(Exception):
    """늦게 온 결과를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


# ================================================================ 사용자 수정(PUT scene) · 승인


def update_scenes(owner: str, pid: str, patch: dict) -> dict:
    """사용자가 고친 scenes 를 저장한다(같은 정리 함수 통과). 저장하면 승인 해제.

    patch = {"scenes": [...]} — 각 scene 은 locationId·timeline 을 가진다. 정리할 때 cut_specs 는
    현재 저장된 scene 의 timeline(cutId·길이)을 기준으로 쓴다(컷 수·시간을 임의로 바꾸지 못하게).
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        previz = data.setdefault("previz", new_previz())
        raw_scenes = patch.get("scenes")
        if not isinstance(raw_scenes, list):
            raise VpocError("vnew.previz.scene", "BadScenes", "scenes 배열이 필요합니다.", 400)
        # 현재 저장본에서 scene_specs(각 scene 의 cutId·seconds·textures)를 뽑는다.
        current = previz.get("scenes") or []
        scene_specs = _specs_from_scenes(current)
        if not scene_specs:
            # 아직 설계 전이면 콘티에서 그룹을 만든다(저장본이 없어도 PUT 을 허용).
            scene_specs = _specs_from_conti(owner, pid, data)
        scenes = previz_schema.normalize_scenes(raw_scenes, scene_specs=scene_specs)
        # 사용자가 직접 고친 카메라 컷은 match 를 match_visible 로 다시 계산한다(method "manual").
        _recompute_manual_fit(previz, current, scenes)
        previz["scenes"] = scenes
        if previz.get("status") in ("none", "failed"):
            previz["status"] = "ready"
        previz["approved"] = False  # 장면을 고치면 승인 해제.
        data["revision"] = int(data.get("revision") or 0) + 1
        return previz

    _, previz = plan_mod.mutate_plan(owner, pid, mutator)
    return previz


def _recompute_manual_fit(previz: dict, old_scenes: list[dict], new_scenes: list[dict]) -> None:
    """PUT 으로 카메라가 바뀐 컷의 match 를 match_visible 로 다시 계산해 previz["fit"] 에 저장(manual).

    바뀌지 않은 컷의 fit 은 그대로 둔다. grammar(패널 읽기+override)로 주인공·샷 크기·머리 위치를 안다.
    grammar 가 없는 컷은 match null·method manual 로만 둔다(판정 불가).
    """
    fit = previz.get("fit")
    if not isinstance(fit, dict):
        return
    grammar_cache = previz.get("panelGrammar") or {}
    overrides = previz.get("grammarOverrides") or {}

    def _cam_of(scenes, cut_id):
        for scene in scenes or []:
            for t in scene.get("timeline") or []:
                if str(t.get("cutId") or "") == cut_id:
                    return scene, t
        return None, None

    for scene in new_scenes or []:
        for cut in scene.get("timeline") or []:
            cut_id = str(cut.get("cutId") or "")
            _, old_cut = _cam_of(old_scenes, cut_id)
            old_cam = (old_cut or {}).get("camera") or {}
            new_cam = cut.get("camera") or {}
            if old_cam == new_cam:
                continue  # 카메라가 안 바뀐 컷은 그대로.
            cached = (grammar_cache.get(cut_id) or {}).get("grammar")
            grammar = _merge_override(cached, overrides.get(cut_id)) if isinstance(cached, dict) else None
            match = None
            if isinstance(grammar, dict):
                subject_name = str(grammar.get("mainSubject") or "")
                meta = None
                for a in scene.get("actors") or []:
                    if str(a.get("name") or "") == subject_name:
                        meta = a
                        break
                if meta is None and scene.get("actors"):
                    meta = (scene.get("actors") or [None])[0]
                foot = _actor_foot(scene, cut, (meta or {}).get("id")) if meta else [0, 0, 0]
                height = float((meta or {}).get("heightM") or 1.7)
                pose = "stand"
                for ca in cut.get("actors") or []:
                    if str(ca.get("id")) == str((meta or {}).get("id")):
                        pose = str(ca.get("pose") or "stand")
                        break
                shot_size = str(grammar.get("shotSize") or "MCU")
                bottom = previz_camera.LENS.get(shot_size, previz_camera.LENS["MCU"])[2]
                top_y = float(grammar.get("headTopY") or 0.1)
                subject_x = float(grammar.get("subjectX") or 0.5)
                try:
                    match = round(previz_fit.match_visible(
                        new_cam, foot, height, pose, top_y, subject_x, bottom
                    ), 4)
                except Exception as exc:  # noqa: BLE001
                    log.warning("vnew.previz.manual_fit.failed cut=%s err=%s", cut_id, str(exc)[:150])
                    match = None
            prev = fit.get(cut_id) if isinstance(fit.get(cut_id), dict) else {}
            fit[cut_id] = dict(prev, match=match, method="manual")


def _specs_from_scenes(scenes: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for scene in scenes or []:
        timeline = scene.get("timeline") or []
        cuts = [{"cutId": str(t.get("cutId") or ""), "seconds": max(0.0, float(t.get("end") or 0) - float(t.get("start") or 0))} for t in timeline]
        textures = _textures_from_scene(scene)
        specs.append({"locationId": str(scene.get("locationId") or ""), "cuts": cuts, "textures": textures})
    return specs


def _textures_from_scene(scene: dict) -> dict:
    """저장된 scene.room 에서 textureKey 를 뽑아 textures 모양으로(PUT 때 유지)."""
    room = scene.get("room") or {}
    walls = room.get("walls") or {}
    tex_walls = {side: str((walls.get(side) or {}).get("textureKey") or "") for side in previz_schema.SIDES if (walls.get(side) or {}).get("textureKey")}
    textures: dict = {}
    if tex_walls:
        textures["walls"] = tex_walls
    if room.get("floorTextureKey"):
        textures["floorKey"] = str(room.get("floorTextureKey"))
    return textures


def _specs_from_conti(owner: str, pid: str, data: dict) -> list[dict]:
    groups = _scene_groups(data.get("conti") or {})
    specs: list[dict] = []
    for group in groups:
        loc = _location_for(data, group["locationEn"])
        location_id = _location_id_for(loc, group["locationEn"])
        cut_specs = [{"cutId": c["cutId"], "seconds": c["seconds"]} for c in group["cuts"]]
        specs.append({"locationId": location_id, "cuts": cut_specs, "textures": _textures_for(owner, pid, loc)})
    return specs


def _low_match_cuts(previz: dict) -> list[dict]:
    """fit.match < APPROVE_MIN_MATCH 또는 match null(판정 불가)인 컷 목록(순서대로).

    반환 [{cutId, match, no}]. no 는 컷 번호(scenes timeline 순서, 1 부터).
    """
    fit = previz.get("fit") or {}
    # cutId → 컷 번호(모든 scene timeline 을 순서대로).
    order: list[str] = []
    for scene in previz.get("scenes") or []:
        for t in scene.get("timeline") or []:
            order.append(str(t.get("cutId") or ""))
    low: list[dict] = []
    for index, cut_id in enumerate(order, start=1):
        info = fit.get(cut_id) or {}
        match = info.get("match")
        # match null(판정 불가)·숫자 < 하한 이면 낮은 컷. 옛 err 형식(match 없음)은 세지 않는다.
        if "match" not in info:
            continue
        if match is None or float(match) < APPROVE_MIN_MATCH:
            low.append({"cutId": cut_id, "match": match, "no": index})
    return low


def _low_cut_message(low: list[dict]) -> str:
    parts = []
    for item in low:
        m = item.get("match")
        pct = "판정 불가" if m is None else f"{round(float(m) * 100)}%"
        parts.append(f"컷 {item['no']}({pct})")
    return "시점 일치도 " + str(round(APPROVE_MIN_MATCH * 100)) + "% 미만 컷이 있습니다: " + ", ".join(parts)


def approve(owner: str, pid: str, *, force: bool = False) -> dict:
    """프리비즈 승인. scenes 가 있어야 한다.

    모든 컷의 fit.match ≥ APPROVE_MIN_MATCH(기본 0.7)가 아니면 409 LowMatch. match null(판정 불가)
    컷도 낮은 컷으로 센다. force=True 면 낮은 컷이 있어도 승인하고 previz["approvedForced"]={at,lowCuts}
    를 기록한다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        previz = data.setdefault("previz", new_previz())
        if not (previz.get("scenes")):
            raise VpocError("vnew.previz.approve", "NoScenes", "먼저 장면을 설계해 주세요.", 409)
        if previz.get("status") != "ready":
            raise VpocError("vnew.previz.approve", "NotReady", "장면이 준비되지 않았습니다.", 409)
        low = _low_match_cuts(previz)
        if low and not force:
            raise VpocError("vnew.previz.approve", "LowMatch", _low_cut_message(low), 409)
        previz["approved"] = True
        if low:
            previz["approvedForced"] = {
                "at": now_iso(),
                "lowCuts": [{"cutId": c["cutId"], "no": c["no"], "match": c["match"]} for c in low],
            }
        else:
            previz["approvedForced"] = None
        return previz

    _, previz = plan_mod.mutate_plan(owner, pid, mutator)
    # 승인 성공 → 촬영 지시서 작성을 자동으로 시작한다(⑤, 설계자 2026-10-22). 진행 중/실패는 조용히 무시.
    try:
        from . import shot_brief as brief_mod  # noqa: PLC0415

        brief_mod.start_brief_safe(owner, pid)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.approve.brief_start_failed pid=%s err=%s", pid, str(exc)[:200])
    return previz


def unapprove(plan: dict) -> None:
    """콘티·장면을 고치면 승인 해제(plan.py 훅이 부른다). plan 을 제자리에서 고친다."""
    previz = plan.get("previz")
    if isinstance(previz, dict) and previz.get("approved"):
        previz["approved"] = False


# ================================================================ 녹화 업로드(변환·길이 확인)


def save_recording(owner: str, pid: str, seg_id: str, body: bytes, content_type: str) -> dict:
    """녹화(webm/mp4) 바이트를 받아 mp4 h264 1280x720 24fps 무음으로 변환해 VIDEO_BUCKET 에 저장.

    - segId 는 conti.segments 의 id 여야 한다(그 구간 seconds 로 길이를 검사).
    - 길이: 구간 seconds ±0.5초, 그리고 3~15초(벗어나면 400).
    - 최대 200MB. Content-Type 은 video/webm|video/mp4.
    반환: {status, key, seconds, url}.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in RECORDING_CONTENT_TYPES:
        raise VpocError("vnew.previz.recording", "BadContentType", "녹화는 webm 또는 mp4 여야 합니다.", 400)
    if not body:
        raise VpocError("vnew.previz.recording", "Empty", "녹화 데이터가 비어 있습니다.", 400)
    if len(body) > MAX_RECORDING_BYTES:
        raise VpocError("vnew.previz.recording", "TooLarge", "녹화 파일이 너무 큽니다(최대 200MB).", 400)

    plan, _ = plan_mod.load_plan(owner, pid)
    previz = plan.get("previz") or {}
    rev = int(previz.get("revision") or 0)
    segment = _find_segment(plan, seg_id)
    if segment is None:
        raise VpocError("vnew.previz.recording", "NoSegment", "구간을 찾을 수 없습니다.", 404)
    target_seconds = float(segment.get("seconds") or 0)

    raw_seconds = _probe_bytes_seconds(body, ctype)
    if target_seconds > 0 and raw_seconds and raw_seconds < target_seconds - REC_TOLERANCE_S:
        raise VpocError(
            "vnew.previz.recording", "BadDuration",
            f"녹화가 구간 길이({target_seconds:.1f}초)보다 너무 짧습니다(측정 {raw_seconds:.2f}초).", 400,
        )
    mp4, seconds = _convert_recording(body, ctype, target_seconds)

    # 길이 확인: 구간 seconds ±0.5초, 절대 3~15초.
    if seconds < REC_MIN_S or seconds > REC_MAX_S:
        raise VpocError(
            "vnew.previz.recording",
            "BadDuration",
            f"녹화 길이가 {REC_MIN_S:.0f}~{REC_MAX_S:.0f}초 범위를 벗어났습니다(측정 {seconds:.2f}초).",
            400,
        )
    if target_seconds > 0 and abs(seconds - target_seconds) > REC_TOLERANCE_S:
        raise VpocError(
            "vnew.previz.recording",
            "BadDuration",
            f"녹화 길이가 구간 {target_seconds:.2f}초와 ±{REC_TOLERANCE_S}초 넘게 차이납니다(측정 {seconds:.2f}초).",
            400,
        )

    key = recording_key(owner, pid, rev, seg_id)
    STORE.put_bytes(key, mp4, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)

    def mutator(data: dict) -> None:
        pv = data.setdefault("previz", new_previz())
        pv.setdefault("recordings", {})[seg_id] = {"status": "ready", "key": key, "seconds": round(seconds, 3)}

    plan_mod.mutate_plan(owner, pid, mutator)
    return {"status": "ready", "key": key, "seconds": round(seconds, 3), "url": _presign_video(key)}


# ================================================================ 프레임 단위 오프라인 렌더(frames · finish)
#
# 실시간 MediaRecorder 녹화는 브라우저가 rAF 를 줄이면(창 가려짐·백그라운드·느린 PC) 프레임이 빠진다
# (13초 구간이 10프레임으로). 그래서 브라우저가 장면을 '그 시각으로 설정해 한 장씩' 그려 jpeg 로 모아
# 올리면(frames), 서버가 ffmpeg -framerate 로 묶어 정확히 frames/fps 길이의 mp4 를 만든다(finish).
# 실시간·rAF 와 무관해 창을 가려도 프레임이 안 빠진다. 기존 webm 업로드 API(save_recording)는 그대로 둔다.


def save_frames(owner: str, pid: str, seg_id: str, start_index: int, frames: list[bytes]) -> dict:
    """오프라인 렌더 프레임(jpeg) 한 묶음을 메인 버킷 임시 접두사에 저장한다.

    - frames 는 순서대로. 키는 .../frames/{segId}/{(start_index+i):05d}.jpg.
    - segId 는 conti.segments 의 id 여야 한다(없으면 404).
    - 각 프레임 2MB·인덱스 범위 제한. revision 은 현재 previz.revision 을 쓴다.
    반환: {ok, start, count, saved:[index...]}.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    if not frames:
        raise VpocError("vnew.previz.frames", "Empty", "프레임이 비어 있습니다.", 400)
    try:
        start = int(start_index)
    except (TypeError, ValueError) as exc:
        raise VpocError("vnew.previz.frames", "BadStart", "start 값이 올바르지 않습니다.", 400) from exc
    if start < 0 or start + len(frames) > MAX_FRAMES:
        raise VpocError("vnew.previz.frames", "BadStart", "프레임 인덱스 범위를 벗어났습니다.", 400)

    plan, _ = plan_mod.load_plan(owner, pid)
    rev = int((plan.get("previz") or {}).get("revision") or 0)
    if _find_segment(plan, seg_id) is None:
        raise VpocError("vnew.previz.frames", "NoSegment", "구간을 찾을 수 없습니다.", 404)

    saved: list[int] = []
    for offset, body in enumerate(frames):
        if not body:
            raise VpocError("vnew.previz.frames", "EmptyFrame", "빈 프레임이 있습니다.", 400)
        if len(body) > MAX_FRAME_BYTES:
            raise VpocError("vnew.previz.frames", "FrameTooLarge", "프레임이 너무 큽니다(최대 2MB).", 400)
        index = start + offset
        STORE.put_bytes(frame_key(owner, pid, rev, seg_id, index), body, "image/jpeg")
        saved.append(index)
    return {"ok": True, "start": start, "count": len(saved), "saved": saved}


def finish_recording(owner: str, pid: str, seg_id: str, frames: int, fps: int) -> dict:
    """올라온 프레임들을 ffmpeg 로 묶어 mp4(h264 1280x720)를 만들고 저장·검증한 뒤 프레임을 지운다.

    - frames 는 전체 프레임 수(0..frames-1 이 모두 있어야 한다 — 중간 묶음이 빠지면 400).
    - 길이 = frames/fps. 구간 seconds ±0.5초, 절대 3~15초(벗어나면 400).
    - mp4 는 기존과 같은 키(recording_key)·recordings 갱신. 성공·실패와 무관하게 프레임을 지운다.
    반환: {status, key, seconds, url}.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        total = int(frames)
        rate = int(fps)
    except (TypeError, ValueError) as exc:
        raise VpocError("vnew.previz.finish", "BadArgs", "frames·fps 값이 올바르지 않습니다.", 400) from exc
    if total <= 0 or total > MAX_FRAMES:
        raise VpocError("vnew.previz.finish", "BadFrames", "프레임 수가 올바르지 않습니다.", 400)
    if rate < FRAME_FPS_MIN or rate > FRAME_FPS_MAX:
        raise VpocError("vnew.previz.finish", "BadFps", "fps 가 올바르지 않습니다.", 400)

    plan, _ = plan_mod.load_plan(owner, pid)
    previz = plan.get("previz") or {}
    rev = int(previz.get("revision") or 0)
    segment = _find_segment(plan, seg_id)
    if segment is None:
        raise VpocError("vnew.previz.finish", "NoSegment", "구간을 찾을 수 없습니다.", 404)
    target_seconds = float(segment.get("seconds") or 0)

    try:
        mp4, seconds = _frames_to_mp4(owner, pid, rev, seg_id, total, rate)

        # 길이 확인: 구간 seconds ±0.5초, 절대 3~15초. frames/fps 가 곧 길이다.
        if seconds < REC_MIN_S or seconds > REC_MAX_S:
            raise VpocError(
                "vnew.previz.finish", "BadDuration",
                f"녹화 길이가 {REC_MIN_S:.0f}~{REC_MAX_S:.0f}초 범위를 벗어났습니다(측정 {seconds:.2f}초).", 400,
            )
        if target_seconds > 0 and abs(seconds - target_seconds) > REC_TOLERANCE_S:
            raise VpocError(
                "vnew.previz.finish", "BadDuration",
                f"녹화 길이가 구간 {target_seconds:.2f}초와 ±{REC_TOLERANCE_S}초 넘게 차이납니다(측정 {seconds:.2f}초).", 400,
            )

        key = recording_key(owner, pid, rev, seg_id)
        STORE.put_bytes(key, mp4, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)

        def mutator(data: dict) -> None:
            pv = data.setdefault("previz", new_previz())
            pv.setdefault("recordings", {})[seg_id] = {"status": "ready", "key": key, "seconds": round(seconds, 3)}

        plan_mod.mutate_plan(owner, pid, mutator)
        return {"status": "ready", "key": key, "seconds": round(seconds, 3), "url": _presign_video(key)}
    finally:
        # 성공이든 실패든 임시 프레임은 지운다(파드가 여럿이라 S3 에 쌓이지 않게).
        _delete_frames(owner, pid, rev, seg_id, total)


def _frames_to_mp4(owner: str, pid: str, rev: int, seg_id: str, total: int, fps: int) -> tuple[bytes, float]:
    """프레임 jpeg 들을 내려받아 ffmpeg -framerate 로 mp4(h264 1280x720)로 묶는다. (mp4, 길이초).

    0..total-1 이 모두 있어야 한다 — 하나라도 없으면 400(중간 묶음 누락). 길이 = total/fps.
    """
    binary = stitcher.ffmpeg_path()
    with tempfile.TemporaryDirectory(prefix="previz-frames-") as workdir:
        for index in range(total):
            key = frame_key(owner, pid, rev, seg_id, index)
            try:
                body = STORE.get_bytes(key)
            except Exception as exc:  # noqa: BLE001
                raise VpocError(
                    "vnew.previz.finish", "MissingFrame",
                    f"프레임 {index} 가 없습니다. 모든 묶음을 올린 뒤 finish 해 주세요.", 400,
                ) from exc
            if not body:
                raise VpocError(
                    "vnew.previz.finish", "MissingFrame", f"프레임 {index} 가 비어 있습니다.", 400,
                )
            with open(os.path.join(workdir, f"{index:05d}.jpg"), "wb") as handle:
                handle.write(body)
        out = os.path.join(workdir, "out.mp4")
        command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(int(fps)),
            "-i", os.path.join(workdir, "%05d.jpg"),
            # 1280x720 안에 맞추고(비율 유지) 홀수 폭을 피하려 pad 로 정확히 채운다.
            "-vf", f"scale={REC_WIDTH}:{REC_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={REC_WIDTH}:{REC_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
            "-r", str(int(fps)), out,
        ]
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=stitcher.STITCH_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode != 0 or not os.path.exists(out) or not os.path.getsize(out):
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise VpocError("vnew.previz.finish", "ConvertFailed", f"프레임 묶기에 실패했습니다: {detail}", 502)
        seconds = stitcher.probe_duration(out)
        with open(out, "rb") as handle:
            mp4 = handle.read()
    return mp4, float(seconds or 0.0)


def _delete_frames(owner: str, pid: str, rev: int, seg_id: str, total: int) -> None:
    """임시 프레임(0..total-1)을 지운다. STORE.delete_bytes 가 있으면 그걸, 없으면 S3 직접."""
    deleter = getattr(STORE, "delete_bytes", None)
    for index in range(max(0, int(total))):
        key = frame_key(owner, pid, rev, seg_id, index)
        try:
            if callable(deleter):
                deleter(key)
            else:
                vpoc_store.s3_client(vpoc_settings.AWS_REGION).delete_object(
                    Bucket=vpoc_settings.MAIN_BUCKET, Key=key
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.frames.delete_failed key=%s err=%s", key, str(exc)[:150])


def _find_segment(plan: dict, seg_id: str) -> dict | None:
    for seg in (plan.get("conti") or {}).get("segments") or []:
        if str(seg.get("id") or "") == seg_id:
            return seg
    return None


def _convert_recording(body: bytes, ctype: str, target_seconds: float = 0.0) -> tuple[bytes, float]:
    """webm/mp4 바이트를 mp4 h264 1280x720 24fps 무음으로 변환하고 (mp4 bytes, 길이초)를 돌려준다.

    imageio-ffmpeg 정적 바이너리(stitcher.ffmpeg_path)를 쓴다. 변환 실패면 502.
    """
    binary = stitcher.ffmpeg_path()
    suffix = ".webm" if ctype == "video/webm" else ".mp4"
    with tempfile.TemporaryDirectory(prefix="previz-rec-") as workdir:
        src = os.path.join(workdir, "src" + suffix)
        out = os.path.join(workdir, "out.mp4")
        with open(src, "wb") as handle:
            handle.write(body)
        command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-i", src,
            # 1280x720 안에 맞추고(비율 유지) 24fps 로. 홀수 폭을 피하려 pad 로 정확히 채운다.
            "-vf", f"scale={REC_WIDTH}:{REC_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={REC_WIDTH}:{REC_HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={REC_FPS}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out,
        ]
        if target_seconds > 0:
            # 녹화는 실시간이라 계획보다 수십 ms 짧을 수 있다(예: 2.96초). 마지막 프레임을 늘리고 계획 길이로
            # 정확히 자른다 — Kling 편집은 3~15초만 받는다. 0.5초 넘게 짧은 녹화는 아래 길이 검사가 거절한다.
            command[command.index("-vf") + 1] += f",tpad=stop_mode=clone:stop_duration={REC_TOLERANCE_S}"
            command[-1:-1] = ["-t", f"{target_seconds:.3f}"]
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=stitcher.STITCH_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode != 0 or not os.path.exists(out) or not os.path.getsize(out):
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise VpocError("vnew.previz.recording", "ConvertFailed", f"녹화 변환에 실패했습니다: {detail}", 502)
        seconds = stitcher.probe_duration(out)
        with open(out, "rb") as handle:
            mp4 = handle.read()
    return mp4, float(seconds or 0.0)


def _probe_bytes_seconds(body: bytes, ctype: str) -> float:
    """원본 녹화 길이(초). 못 재면 0."""
    suffix = ".webm" if ctype == "video/webm" else ".mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="previz-probe-") as workdir:
            src = os.path.join(workdir, "src" + suffix)
            with open(src, "wb") as handle:
                handle.write(body)
            return float(stitcher.probe_duration(src) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


# ================================================================ 보기(previz_view)


def _shot_brief_view(plan: dict) -> dict:
    """촬영 지시서(shotBrief) 보기. shot_brief 모듈에 위임(순환 import 피해 늦게 import)."""
    try:
        from . import shot_brief as brief_mod  # noqa: PLC0415

        return brief_mod.brief_view(plan)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.shot_brief_view_failed err=%s", str(exc)[:200])
        return {"status": "none", "error": "", "model": "", "scene": {}, "cuts": {}, "updatedAt": "", "task": {}}


def previz_view(owner: str, pid: str, plan: dict) -> dict:
    """plan_view 의 previz — status, scenes(JSON), 텍스처 presigned URL, 승인·녹화 상태."""
    previz = plan.get("previz") or new_previz()
    previz = _reconcile_stale(previz)
    rev = int(previz.get("revision") or 0)

    scenes_out = []
    for scene in previz.get("scenes") or []:
        scenes_out.append(_scene_view(pid, scene))

    segments = (plan.get("conti") or {}).get("segments") or []
    recordings_in = previz.get("recordings") or {}
    recordings_out: dict[str, dict] = {}
    recorded_all = bool(segments)
    for seg in segments:
        seg_id = str(seg.get("id") or "")
        rec = recordings_in.get(seg_id)
        if rec and rec.get("key"):
            recordings_out[seg_id] = {
                "status": "ready",
                "url": _presign_video(str(rec.get("key") or "")),
                "seconds": rec.get("seconds"),
            }
        else:
            recordings_out[seg_id] = {"status": "none", "url": "", "seconds": None}
            recorded_all = False

    return {
        "status": previz.get("status") or "none",
        "error": previz.get("error") or "",
        "model": previz.get("model") or "",
        "revision": rev,
        "approved": bool(previz.get("approved")),
        "scenes": scenes_out,
        "panelShots": previz.get("panelShots") or {},
        "panelGrammar": previz.get("panelGrammar") or {},
        "grammarOverrides": previz.get("grammarOverrides") or {},
        "fit": previz.get("fit") or {},
        "approveMinMatch": APPROVE_MIN_MATCH,
        "lowMatchCuts": _low_match_cuts(previz),
        "approvedForced": previz.get("approvedForced"),
        "totalSeconds": previz_schema.scenes_total_seconds(previz.get("scenes") or []),
        "segments": segments,
        "recordings": recordings_out,
        "recordedAll": recorded_all,
        "task": previz.get("task") or _empty_task(),
        "progress": previz.get("progress") or _empty_progress(),
        "extraPromptKo": previz.get("extraPromptKo") or "",
        "extraCutId": previz.get("extraCutId") or "",
        "lastRebuild": previz.get("lastRebuild"),
        "shotBrief": _shot_brief_view(plan),
    }


def _scene_view(pid: str, scene: dict) -> dict:
    """scene JSON 에 텍스처 API 경로(textureUrl)를 더해 돌려준다(키는 그대로 남긴다).

    서명 URL(S3 직통) 대신 같은 출처 API 경로를 준다 — 버킷에 CORS 설정이 없어 three.js 가 S3 를
    직접 부르면 텍스처가 안 불러지거나 캔버스가 오염돼 녹화가 막히기 때문. 브라우저는 이 경로를
    Authorization 헤더로 받아 blob 으로 넘긴다(previz.texture_bytes).
    """
    out = copy.deepcopy(scene)
    room = out.get("room") or {}
    walls = room.get("walls") or {}
    for side in previz_schema.SIDES:
        wall = walls.get(side)
        if isinstance(wall, dict) and wall.get("textureKey"):
            wall["textureUrl"] = texture_api_path(pid, str(wall.get("textureKey")))
    if room.get("floorTextureKey"):
        room["floorTextureUrl"] = texture_api_path(pid, str(room.get("floorTextureKey")))
    return out


def _reconcile_stale(previz: dict) -> dict:
    task = previz.get("task") or {}
    if _task_stale(task):
        previz = dict(previz)
        previz["status"] = "failed"
        previz["error"] = "장면 설계가 중단되었습니다. 다시 시도해 주세요."
        previz["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
    return previz


# ================================================================ 원장 · presign


def _charge(owner: str, pid: str, plan_id: str, usage: dict, model: str) -> None:
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        key = f"vnew-previz-{pid}-{plan_id}-{secrets.token_hex(3)}"
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
        log.warning("vnew.previz.charge.failed pid=%s err=%s", pid, str(exc)[:200])


def _presign_video(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.presign_video.failed key=%s err=%s", key, str(exc)[:150])
        return ""


# ================================================================ 작은 도우미


def _spawn(func, owner: str, pid: str, task_id: str, *, name: str) -> None:
    thread = threading.Thread(target=func, args=(owner, pid, task_id), daemon=True, name=name)
    thread.start()


def _task_stale(task: dict) -> bool:
    if (task or {}).get("status") != "running":
        return False
    started = _parse_iso((task or {}).get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > TASK_STALL_S


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
