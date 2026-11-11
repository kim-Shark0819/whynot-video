"""vnew 영상만들기_신규 — 공간 디테일 시트(설계자 2026-10-21, 048 컷어웨이 뷰).

콘티 단계에서 쓰이는 배경(장소)마다 공간 이미지를 만든다. 다음 단계 프리비즈(three.js)의 벽·바닥에
입힐 텍스처로 쓴다. 벽을 4장 따로 그리면 모서리·바닥선·재질·빛이 이어지지 않으므로(046 문제),
앞 벽을 떼어 낸 상자를 정면 바깥에서 본 1점 투시 '컷어웨이 뷰' 두 장으로 공간을 만든다(048):

- 뷰 A(back, 뒤 보기): 가운데=뒤 벽(north), 좌=왼쪽 벽(west), 우=오른쪽 벽(east), 아래=바닥.
- 뷰 B(front, 앞 보기): 같은 방을 반대편에서(south 가 가운데).

서버가 방 치수(design)로 PIL 가이드 이미지를 그리고(verify·cut 기준), 뷰 4장은 참고 이미지 없이 fal text-to-image
(settings.LOCATION_IMAGE_MODEL)로 동시에 그린다(작업 125 — 같은 방이 되게 지시문 앞에 공통 문장을 붙인다).
생성 뷰에서 각 면 사다리꼴을 원근 보정(location_views.rectify)해 기존 wall_key/floor_key 로 저장한다
→ previz 텍스처(_textures_for)는 수정 없이 그대로 동작한다. 바닥은 뷰 A 의 floor 사다리꼴을 편다.

장소 목록은 plan["locations"] 에 둔다(콘티를 다시 만들어도 같은 이름이면 유지). script.entries 의
locationEn 을 정규화(소문자·공백 정리)해 고유 장소를 뽑는다.

재사용(수정 금지): vpoc.seedream(generate_safe · size) · vpoc.llm(_converse) · vpoc.images
(panel_jpeg · jpeg_for_reference) · vpoc.store(S3) · vpoc.ledger(원장) · vnew.characters(styleEn) ·
vnew.location_views(가이드·투영·원근 보정 순수 함수). plan.py 가 sync_locations 를 콘티 끝에서 부르고,
plan_view 가 locations_view 를 담는다. routes.py 는 build · build-all · update 를 HTTP 로 감싼다.

외부 호출(LLM · Seedream)은 아래 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _design · _facts · _author ·
_verify · _seedream. STORE 는 vpoc.store.STORE 를 쓴다(검사가 모듈 변수로 바꿔 끼운다).
"""

from __future__ import annotations

import io
import logging
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from ..vpoc import compose as vpoc_compose
from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import seedream as vpoc_seedream
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE

from . import characters
from . import settings
from . import location_views

log = logging.getLogger("vpoc")

# ================================================================ 상수

# 설계에 보여 줄 웹툰 패널 최대 장수(그 장소가 나오는 패널). 배경이 큰 조각 우선.
MAX_DESIGN_PANELS = 6
# Seedream 참고 이미지 최대 장수(그 장소 패널).
MAX_REF_PANELS = 4
# 벽 입면도 크기(16:9). 4벽 모두 같은 크기. (옛 데이터·montage_2x2 용으로 남겨 둔다.)
WALL_SIZE = "2048x1152"
# 바닥 텍스처 크기(정사각, top-down). (옛 데이터용.)
FLOOR_SIZE = "1536x1536"
# 벽 방향(north=웹툰에서 가장 많이 보이는 정면 벽).
SIDES = ("north", "east", "south", "west")
# 그림체.
VARIANTS = ("real", "webtoon")
# 한 장소의 그림(벽4+바닥1 × 그림체 2 = 10장)을 동시에 그리는 최대 스레드 수.
IMAGE_WORKERS = 4
# 그릴 그림 총 장수(벽4+바닥1 × 그림체 2 = 10). 진행 막대 total. (옛 데이터용 상수.)
TOTAL_IMAGES = len(VARIANTS) * (len(SIDES) + 1)

# ---------------------------------------------------------------- 048 컷어웨이 뷰(box set)
#
# 새 방식(설계자 2026-10-21, 048): 벽을 4장 따로 그리지 않고 앞 벽을 뗀 상자를 정면 바깥에서 본 1점
# 투시 컷어웨이 뷰 두 장(back=뒤 보기, front=앞 보기)을 그린다. 뷰에서 각 면(사다리꼴)을 원근 보정해
# (rectify) 벽·바닥 텍스처로 쓴다. 저장 키는 기존 wall_key/floor_key 그대로 → previz 는 수정 없이 동작.

# 컷어웨이 뷰 크기(설계자 048).
VIEW_SIZE = "2560x1440"
# 가이드 크기(뷰와 같게).
GUIDE_SIZE = "2560x1440"
# 뷰 종류. back=뒤 보기(north·west·east·floor), front=앞 보기(south).
VIEWS = ("back", "front")
# 그릴 뷰 총 장수(뷰2 × 그림체2 = 4). 진행 막대 total.
TOTAL_VIEWS = len(VARIANTS) * len(VIEWS)
# 공간 시트 한 장소에서 동시에 부르는 외부 호출 수(작업 125, 1곳): 지시문 4개·그림 4장·검사 4개·사람 수 세기.
# 참고 이미지 체인(real A → real B·webtoon A → webtoon B)이 없어져 뷰 4장이 서로 독립이다.
LOCATION_WORKERS = 4
# (옛 이름) 뷰를 동시에 그리는 최대 스레드 수. LOCATION_WORKERS 를 따른다.
VIEW_WORKERS = LOCATION_WORKERS
# verify 꼭짓점 차이가 화면 가로·세로의 이 비율을 넘으면 Claude 꼭짓점을 쓴다(설계자 048).
VERTEX_TOLERANCE = 0.04

# 뷰 A(back)/뷰 B(front) 와 면별 한국어 라벨(화면). 설계 벽 이름(north/east/south/west) 한국어 표기도 겸한다.
VIEW_FACE_LABELS = {
    "back": "메인배경(뒤) 보기 — 왼쪽·뒤·오른쪽이 이어진 모습",
    "front": "앞(맞은편) 보기",
    "left": "왼쪽",
    "north": "메인배경(뒤)",
    "right": "오른쪽",
    "south": "앞(맞은편)",
    "floor": "바닥",
    # 설계 편집 화면: north=뒤, east=오른쪽, south=앞, west=왼쪽.
    "wall_north": "(뒤)",
    "wall_east": "(오른쪽)",
    "wall_south": "(앞)",
    "wall_west": "(왼쪽)",
}

# 뷰 종류·면 → 렌더러 벽 매핑은 location_views.VIEW_FACE_TO_WALL 를 쓴다(단일 출처).
VIEW_FACE_TO_WALL = location_views.VIEW_FACE_TO_WALL
# 실패한 그림을 다시 그리는 횟수(1회).
IMAGE_RETRY = 1
# 추가 요청(한국어) 최대 길이.
MAX_EXTRA_PROMPT_LEN = 500
# build task 가 이 시간을 넘게 running 이면 멈춘 것으로 본다(조회 때 failed).
BUILD_STALL_S = 1800
# 끊긴 작업 바로 재개(작업 128): 이 파드에서 돌고 있지 않고 heartbeat(progress.heartbeatAt·updatedAt 중 늦은 값)가
# 이 시간보다 오래됐으면 서버 재시작으로 끊긴 것으로 본다(조회 때 failed, build 요청이면 바로 새로 시작).
LOCATION_STALL_S = settings.LOCATION_STALL_S
# 진행 중 heartbeat 간격(작업 128). 오래 걸리는 단계(설계·사실·그림·검사)에서도 이 간격으로 heartbeatAt 을 민다.
LOCATION_HEARTBEAT_S = settings.LOCATION_HEARTBEAT_S
# 끊긴 작업 정리 문구(작업 128).
INTERRUPTED_MESSAGE = "서버 재시작으로 중단되었습니다. 다시 만들어 주세요."

# 단가(원장). Seedream 장당. (작업 125 전 값 — 다른 모듈·옛 검사가 읽을 수 있어 남긴다.)
SEEDREAM_USD = settings.SEEDREAM_USD
SEEDREAM_MODEL = settings.SEEDREAM_MODEL
# 공간 시트 그림(작업 125): 참고 이미지 없이 fal text-to-image. 원장 금액은 fal Seedream 장당 단가.
LOCATION_IMAGE_MODEL = settings.LOCATION_IMAGE_MODEL
LOCATION_IMAGE_USD = settings.LOCATION_IMAGE_USD
# 작업 143: 실사 뷰 = 같은 쪽 웹툰 뷰를 실사 사진으로 편집(video-vibecode live.PLACE_EDIT 이식). 원장은 그림 1장과 같은 단가.
LOCATION_EDIT_MODEL = "fal-ai/bytedance/seedream/v4.5/edit"
# 작업 161: 실사 장소 뷰(test3 앞 보기)에 실제 상표 "LAWSON"·ATM 간판이 나왔다. 실사 장소 글(뷰 편집·t2i 실사, generate 의 실사
# 시작 그림·실사 테이크 장소 줄)에 이 구절 1곳. 애니(웹툰) 글에는 넣지 않는다.
REAL_NO_BRAND = "generic store with no real brand names, no logos, no readable signage text"
REAL_NO_BRAND_SENTENCE = "Any shop is a " + REAL_NO_BRAND + "."
PLACE_EDIT = (
    "Turn this image into a real photograph of the same place: {place}. Remove all people completely; show the empty "
    "place. Keep the same layout, camera angle and daylight. Real photograph taken with a camera, photorealistic, natural "
    "materials and textures, no illustration, no anime, no drawing, no line art, no people. " + REAL_NO_BRAND_SENTENCE
)
# 장소 묘사 상한(live.place_prompt 와 같다).
PLACE_TEXT_MAX = 300
_PLACE_DRAW_WORDS_RE = re.compile(r"\b(?:webtoon|anime|manga|illustrat\w*|drawn|cartoon)\b", re.IGNORECASE)
# 설계 LLM 체인: Claude 분석 체인(Fable 5.1) 먼저, 실패하면 Astra.
# (실측 2026-10-21: Astra 가 패널 그림을 거의 못 읽어 빈 벽·'assumed'/'provisional' 설계가 나왔다.
#  Claude 가 이미지 읽기가 강해 먼저 쓴다. Astra 는 지시서 036 장면 설계에 그대로 쓴다.)
DESIGN_MODEL = settings.ANALYZE_FALLBACK_MODEL  # = vpoc.settings.ANALYZE_MODEL (Claude Fable 5.1)
DESIGN_FALLBACK_MODEL = settings.ASTRA_MODEL

# 사실 추출(설계자 2026-10-21, 046): 그 장소 패널에서 벽별 물건·창문·문·재질·시대·나라를 읽는다.
# 이미지 읽기가 강한 Claude 분석 체인을 먼저, 실패하면 Astra.
FACTS_MODEL = settings.ANALYZE_FALLBACK_MODEL  # Claude Fable 5.1
FACTS_FALLBACK_MODEL = settings.ASTRA_MODEL

# Astra 프롬프트 작성(설계자 2026-10-21, 046): 사실 + 그림체로 Seedream 프롬프트를 '지능 없는 이미지
# 모델용'으로 아주 구체적으로 쓴다. Astra 먼저, 실패하면 Claude.
PROMPT_AUTHOR_MODEL = settings.ASTRA_MODEL
PROMPT_AUTHOR_FALLBACK_MODEL = settings.ANALYZE_FALLBACK_MODEL

# 비교·보정(설계자 2026-10-21, 046): 벽 그림마다 (패널·생성 그림·사실)로 일치 여부를 보고, 불일치면
# 그 벽만 1회 다시 그린다. 이미지 읽기가 강한 Claude 를 쓴다.
VERIFY_MODEL = settings.ANALYZE_FALLBACK_MODEL  # Claude Fable 5.1
# verify 첫 호출 출력 한도(작업 128). 운영 실측(2026-11-04) 성공 출력 900~1535 토큰에 여유를 더했다.
# 700 이던 때는 첫 호출 4개가 모두 stop=max_tokens 로 잘려 trunc_retry 를 한 번씩 더 했다.
VERIFY_MAX_TOKENS = 2400

# Seedream 프롬프트 길이(글자 수). 설계자: 1,500~2,500자(Seedream 3,000자 이내).
PROMPT_MIN_CHARS = 1500
PROMPT_MAX_CHARS = 2500
# Seedream 하드 리밋(이 길이를 넘으면 자른다).
PROMPT_HARD_CAP = 3000

# 불일치 벽을 다시 그리는 횟수(1회).
FIX_RETRY = 1

# ---------------------------------------------------------------- 참고 사진(설계자 2026-10-22)
#
# "배경 만드는 것이 말을 잘 안 듣는다" → 장소마다 사용자가 실제 참고 사진을 첨부하고, 그 사진을
# 설계·사실·그림·확인에 웹툰 패널보다 '먼저' 쓴다. 사진마다 어느 면인지(메인배경(뒤)/왼쪽/오른쪽/
# 앞(맞은편)/바닥/전체 분위기) 지정할 수 있다. 이미지는 vpoc.images.normalize_upload 로 JPEG 로
# 정규화해(크기 제한 동일) 그 프로젝트 locations 접두사 아래 photos/{photoId}.jpg 로만 저장한다
# (외부 서비스에 올리지 않는다). 사진을 바꿔도 이미 만든 시트는 그대로 두고 [다시 만들기] 때 반영한다.

# 한 장소에 붙일 수 있는 참고 사진 최대 장수.
MAX_LOCATION_PHOTOS = 6
# 사진에 지정할 수 있는 면. back=메인배경(뒤), front=앞(맞은편), any=전체 분위기.
PHOTO_SIDES = ("back", "left", "right", "front", "floor", "any")
# 사진 메모(한국어) 최대 길이.
MAX_PHOTO_NOTE_LEN = 200

# 면(side) → 사진 블록 라벨 한국어(설계·사실 Claude 블록·화면 안내에 쓴다).
PHOTO_SIDE_LABELS_KO = {
    "back": "메인배경(뒤)",
    "left": "왼쪽",
    "right": "오른쪽",
    "front": "앞(맞은편)",
    "floor": "바닥",
    "any": "전체 분위기",
}

# 면(side) → Seedream 프롬프트에서 그 사진이 어느 화면 벽인지(영문 라벨). 'any' 는 벽이 아니다.
PHOTO_SIDE_WALL_EN = {
    "back": "CENTER BACK WALL",
    "left": "LEFT WALL",
    "right": "RIGHT WALL",
    "front": "CENTER BACK WALL",  # 뷰 B(front)에서는 앞 벽이 화면 가운데 벽이 된다.
    "floor": "FLOOR",
}

# 각 컷어웨이 뷰에 넣을 사진 면(설계자 2026-10-22). 뷰 A=back·left·right·floor, 뷰 B=front·left·right.
# 이어서 'any'(전체 분위기) 사진, 남는 자리에 웹툰 패널.
VIEW_PHOTO_SIDES = {
    "back": ("back", "left", "right", "floor"),
    "front": ("front", "left", "right"),
}


# ================================================================ 파드 안 실행 중 표시
#
# (owner,pid,lid) 가 이 파드에서 빌드 스레드로 돌고 있으면 _RUNNING 에 담는다. 같은 location 을
# 두 번 띄우지 않게 한다(409 와 함께 이중 안전장치).

_RUNNING: set[tuple[str, str, str]] = set()
_RUNNING_LOCK = threading.Lock()


def _mark_running(owner: str, pid: str, lid: str) -> bool:
    key = (owner, pid, lid)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)
        return True


def _clear_running(owner: str, pid: str, lid: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING.discard((owner, pid, lid))


def _running_here(owner: str, pid: str, lid: str) -> bool:
    """(owner,pid,lid) 빌드 스레드가 이 파드에서 돌고 있으면 True(작업 128 — 끊긴 작업 판정)."""
    with _RUNNING_LOCK:
        return (owner, pid, lid) in _RUNNING


# ================================================================ heartbeat(작업 128)
#
# 서버가 재시작되면 그 파드의 빌드 스레드가 사라져도 plan 에는 status=building 이 남는다. 그래서 빌드 중에는
# 단계마다(progress.updatedAt) 그리고 LOCATION_HEARTBEAT_S 마다(progress.heartbeatAt) 살아 있음을 적는다.
# heartbeatAt 을 updatedAt 과 따로 두는 것은 화면의 '응답이 늦어지고 있습니다'(updatedAt 3분)를 지금처럼 두기 위해서다.


def _touch_heartbeat(owner: str, pid: str, lid: str, task_id: str) -> None:
    """progress.heartbeatAt 만 now 로 쓴다. task_id 가 어긋나거나 running 이 아니면 쓰지 않는다."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        task = (loc or {}).get("task") or {}
        if loc is None or task.get("id") != task_id or task.get("status") != "running":
            raise _Discard()
        progress = dict(loc.get("progress") or _empty_progress())
        progress["heartbeatAt"] = now_iso()
        loc["progress"] = progress

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.heartbeat.failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:150])


class _Heartbeat:
    """빌드 동안 every_s 마다 _touch_heartbeat 를 부르는 데몬 스레드. stop() 하면 곧 끝난다."""

    def __init__(self, owner: str, pid: str, lid: str, task_id: str, every_s: float) -> None:
        self._args = (owner, pid, lid, task_id)
        self._every_s = max(0.01, float(every_s))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"vnew-loc-hb-{lid}")

    def _run(self) -> None:
        while not self._stop.wait(self._every_s):
            _touch_heartbeat(*self._args)

    def start(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)


# ================================================================ 진행 표시(progress)
#
# 빌드 중 location["progress"] 에 {step, done, total, startedAt, updatedAt} 를 기록한다. 화면이
# 스피너·진행 막대·경과 시간을 보이는 데 쓴다. step 순서(048): design → facts → prompts → images(뷰 4장)
# → verify → cut(잘라 펴기) → sheet → done. 뷰는 일부 병렬로 그려지므로(완료마다 +1), done 증가는
# mutate_plan(경합 재시도)로 안전하게 올린다.


def _empty_progress() -> dict:
    return {"step": "", "done": 0, "total": 0, "startedAt": None, "updatedAt": None}


def _set_progress(owner: str, pid: str, lid: str, task_id: str, *, step: str, done: int, total: int) -> None:
    """location["progress"] 를 통째로 새로 쓴다(step 전환 시). task_id 가 어긋나면 건너뛴다.

    startedAt 는 처음(design) 한 번만 잡고 이후 유지한다. updatedAt 는 매번 now 로 갱신한다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            return
        if (loc.get("task") or {}).get("id") != task_id:
            return
        prior = loc.get("progress") or {}
        started = prior.get("startedAt") or now_iso()
        loc["progress"] = {
            "step": step,
            "done": int(done),
            "total": int(total),
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.progress.set_failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:150])


def _set_step(owner: str, pid: str, lid: str, task_id: str, *, step: str) -> None:
    """progress 의 step 만 바꾸고 done·total 은 그대로 둔다(비교·보정이 늘린 total 을 잃지 않게).

    verify 단계에서 보정 장수만큼 total 이 커진 뒤, sheet·done 으로 넘어갈 때 total 을 되돌리지 않는다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            return
        if (loc.get("task") or {}).get("id") != task_id:
            return
        prior = loc.get("progress") or _empty_progress()
        started = prior.get("startedAt") or now_iso()
        total = int(prior.get("total") or TOTAL_VIEWS)
        done = int(prior.get("done") or 0)
        if step == "done":
            done = total  # 끝이면 막대를 가득 채운다.
        loc["progress"] = {
            "step": step,
            "done": done,
            "total": total,
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.progress.set_step_failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:150])


def _bump_progress(owner: str, pid: str, lid: str, task_id: str) -> None:
    """그림 한 장이 끝날 때마다 done 을 +1 한다(병렬이어도 완료마다). 경합은 mutate_plan 이 재시도한다.

    step 이 아직 images 가 아니면 images 로 바꾼다. updatedAt 을 갱신해 화면 '지연' 판정 기준을 민다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            return
        if (loc.get("task") or {}).get("id") != task_id:
            return
        prior = loc.get("progress") or _empty_progress()
        started = prior.get("startedAt") or now_iso()
        total = int(prior.get("total") or TOTAL_VIEWS)
        done = min(total, int(prior.get("done") or 0) + 1)
        loc["progress"] = {
            "step": "images",
            "done": done,
            "total": total,
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.progress.bump_failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:150])


# ================================================================ 정규화 · 목록


_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """장소 이름 정규화 키(대소문자·공백 무시). 'A closed  Principal's Office ' → 'a closed principal's office'."""
    text = str(name or "").strip().lower()
    return _WS_RE.sub(" ", text)


def _loc_id() -> str:
    return f"loc_{secrets.token_hex(4)}"


def _empty_task() -> dict:
    return {"status": "idle", "startedAt": None, "id": None}


def _new_location(name_ko: str, name_en: str, panels: list[int]) -> dict:
    """빈 장소. design·variants(real·webtoon)·task 를 틀로 둔다."""
    return {
        "id": _loc_id(),
        "nameKo": str(name_ko or "").strip(),
        "nameEn": str(name_en or "").strip(),
        "key": normalize_name(name_en),
        "panels": sorted({int(p) for p in panels if _is_int(p)}),
        "status": "none",  # none|building|ready|failed
        "error": "",
        "design": {},  # 설계 JSON(아래 _normalize_design)
        "facts": {},  # 사실 추출 JSON(아래 _normalize_facts)
        "photos": [],  # 사용자 참고 사진(설계자 2026-10-22, 아래 _normalize_photo)
        "variants": {v: _empty_variant() for v in VARIANTS},
        "task": _empty_task(),
        "progress": _empty_progress(),
    }


def _empty_variant() -> dict:
    return {
        "status": "none",
        "viewKeys": {},  # {back, front} 컷어웨이 뷰 원본 키(048)
        "wallKeys": {},  # {north,east,south,west} 펴진 벽 텍스처 키(previz 가 읽음)
        "floorKey": "",
        "sheetKey": "",
        "checks": {},
        "error": "",
    }


def sync_locations(plan: dict) -> dict:
    """plan["locations"] 를 script.entries 의 locationEn 으로 갱신한다(자동으로 그리지 않는다).

    - entries 를 돌며 locationEn 을 정규화해 고유 장소를 뽑는다(같은 key 는 하나로 합친다).
    - 이미 있는 장소(같은 key)는 유지하되 panels·이름만 갱신한다(설계·이미지는 보존).
    - 더 이상 어느 entry 에도 없는 장소는 뺀다(콘티가 바뀌어 사라진 배경).
    plan 을 제자리에서 고치고 그대로 돌려준다(저장은 호출자가 한다).
    """
    entries = ((plan.get("script") or {}).get("entries")) or []
    # key → {nameKo, nameEn, panels:set}
    found: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        name_en = str((entry or {}).get("locationEn") or "").strip()
        if not name_en:
            continue
        key = normalize_name(name_en)
        if not key:
            continue
        name_ko = str((entry or {}).get("locationKo") or "").strip()
        panels = [int(p) for p in ((entry or {}).get("panels") or []) if _is_int(p)]
        slot = found.get(key)
        if slot is None:
            slot = {"nameKo": name_ko, "nameEn": name_en, "panels": set()}
            found[key] = slot
            order.append(key)
        else:
            # 첫 이름을 유지하되, 비어 있던 한글 이름은 채운다.
            if not slot["nameKo"] and name_ko:
                slot["nameKo"] = name_ko
        slot["panels"].update(panels)

    existing = {str(loc.get("key") or normalize_name(loc.get("nameEn") or "")): loc for loc in (plan.get("locations") or [])}

    result: list[dict] = []
    for key in order:
        slot = found[key]
        panels = sorted(slot["panels"])
        prior = existing.get(key)
        if prior is not None:
            # 유지: panels·이름만 갱신, 설계·이미지·상태는 보존.
            prior = dict(prior)
            prior["key"] = key
            prior["panels"] = panels
            prior["nameEn"] = slot["nameEn"] or prior.get("nameEn") or ""
            if slot["nameKo"]:
                prior["nameKo"] = slot["nameKo"]
            prior.setdefault("variants", {v: _empty_variant() for v in VARIANTS})
            prior.setdefault("design", {})
            prior.setdefault("facts", {})
            prior.setdefault("task", _empty_task())
            prior.setdefault("progress", _empty_progress())
            prior.setdefault("status", "none")
            result.append(prior)
        else:
            result.append(_new_location(slot["nameKo"], slot["nameEn"], panels))

    plan["locations"] = result
    return plan


def _find_location(plan: dict, lid: str) -> dict | None:
    for loc in plan.get("locations") or []:
        if str(loc.get("id") or "") == lid:
            return loc
    return None


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 두 함수를 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _design(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 공간 설계 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_DESIGN_SYSTEM,
        blocks=blocks,
        tool=_DESIGN_TOOL,
        max_tokens=1600,
        where="vnew.location.design",
        model=model,
    )


def _seedream(prompt: str, references: list[bytes], *, size: str) -> bytes:
    """공간 시트 그림 1장 — fal text-to-image(LOCATION_IMAGE_MODEL, 작업 125). 안전 필터 오탐은 generate_safe.

    references 는 받기만 하고 보내지 않는다(참고 이미지 0장). 장소 사진·웹툰 패널은 사실 정리·지시문에서
    글로만 반영한다.
    """
    return vpoc_seedream.generate_safe(
        prompt,
        [],
        model=LOCATION_IMAGE_MODEL,
        where="vnew.location.seedream",
        size=size,
        fal_model=LOCATION_IMAGE_MODEL,
    )


def _seedream_edit(prompt: str, reference: bytes, *, size: str) -> bytes:
    """실사 뷰 1장 — 웹툰 뷰(reference 1장)를 fal Seedream 4.5 edit 로 실사 사진으로 바꾼다(작업 143).

    vpoc.seedream fal 경로(generate_safe) 그대로. 검사가 바꿔 끼운다.
    """
    return vpoc_seedream.generate_safe(
        prompt,
        [reference],
        model=LOCATION_EDIT_MODEL,
        where="vnew.location.seedream_edit",
        size=size,
        fal_model=LOCATION_EDIT_MODEL,
    )


def _real_from_webtoon() -> bool:
    """작업 143 설정(VNEW_LOCATION_REAL_FROM_WEBTOON). 부를 때마다 읽는다(검사가 바꿔 끼운다)."""
    return bool(settings.VNEW_LOCATION_REAL_FROM_WEBTOON)


def _place_text(loc: dict) -> str:
    """설계(design)의 영어 장소 묘사: 장소 이름 + 벽 4면 descEn(north→east→south→west). 자르기는 _place_edit_prompt."""
    design = loc.get("design") or {}
    walls = design.get("walls") if isinstance(design.get("walls"), dict) else {}
    pieces = [str(loc.get("nameEn") or "")]
    for side in SIDES:
        wall = walls.get(side) if isinstance(walls.get(side), dict) else {}
        pieces.append(str(wall.get("descEn") or ""))
    return ". ".join(p.strip().rstrip(".").strip() for p in pieces if p.strip().rstrip("."))


def _place_edit_prompt(place_text: str, *, outdoor: bool = False) -> str:
    """웹툰 뷰 → 실사 사진 편집 문장(live.place_prompt 이식). 그림 낱말을 빼고 장소 묘사는 PLACE_TEXT_MAX 자 안.
    outdoor=True(작업 226)면 PLACE_EDIT_OUTDOOR(열린 바깥 문장). 기본은 그대로."""
    place = " ".join(str(place_text or "the same location").split()).rstrip(".")
    place = _PLACE_DRAW_WORDS_RE.sub("", place)
    place = re.sub(r"(?<![\w-])-\w+", "", place)  # 낱말을 뺀 뒤 남은 "-style" 같은 조각
    place = re.sub(r"\s*,(?:\s*,)+", ",", " ".join(place.split())).strip(" ,")
    place = re.sub(r"\s+([.,;])", r"\1", place)
    place = vpoc_compose._fit_text(place, PLACE_TEXT_MAX) or "the same location"  # noqa: SLF001
    return (PLACE_EDIT_OUTDOOR if outdoor else PLACE_EDIT).format(place=place)


def _outdoor_place_text(loc: dict) -> str:
    """작업 226: _place_text 의 실외판 — 벽 descEn 에서 'no wall:' 같은 없는 구조 머리말을 뺀다."""
    design = loc.get("design") or {}
    walls = design.get("walls") if isinstance(design.get("walls"), dict) else {}
    pieces = [str(loc.get("nameEn") or "")]
    for side in SIDES:
        wall = walls.get(side) if isinstance(walls.get(side), dict) else {}
        pieces.append(re.sub(r"^open[- ]air\s*[;:,.]\s*", "", _strip_no_structure(wall.get("descEn")), flags=re.IGNORECASE))
    return ". ".join(p.strip().rstrip(".").strip() for p in pieces if p.strip().rstrip("."))


def _edit_prompt_for(loc: dict) -> str:
    """실사 뷰 편집 문장(작업 143). 실내는 226 전 그대로, 실외는 열린 바깥 문장 + 낮이면 밤 낱말 지우기(작업 226)."""
    if not _is_outdoor(loc):
        return _place_edit_prompt(_place_text(loc))
    return _day_guard(_place_edit_prompt(_outdoor_place_text(loc), outdoor=True), loc)

def _timing(pid: str, lid: str, step: str, started: float) -> None:
    """단계 걸린 시간 로그 1줄(작업 125). 사용자 글·키는 쓰지 않는다."""
    ms = int((time.monotonic() - started) * 1000)
    log.info("vnew.location.timing step=%s ms=%d pid=%s lid=%s", step, ms, pid, lid)


# 그림이 동시에 끝날 때 progress(plan.json 조건부 쓰기) 경합을 이 파드 안에서 줄인다(작업 125).
_PROGRESS_LOCK = threading.Lock()


# ================================================================ 설계(Claude → Astra)

# 작업 226: 감정 클로즈업 뒤 빗금 · 스크린톤을 '밤하늘'로 읽어 낮 장소가 밤이 됐다(prod). 사실 · 설계 system 에 이 1문장.
_SCREENTONE_RULE = (
    "Dark hatching, screentone, sparkle or gradient backgrounds behind a character in an emotional close-up "
    "are mood effects, not the time of day or the place. Take time of day and sky from the wide or establishing "
    "panels. If any panel shows a bright sky, it is daytime."
)

_DESIGN_SYSTEM = (
    "You are a set designer. You are given a few webtoon panels that all show the SAME place. "
    "Describe that place as a simple rectangular room so an artist can draw each of its four walls as "
    "a flat frontal elevation (orthographic, no perspective). Answer ONLY by calling the emit_location "
    "tool exactly once. "
    "Choose 'north' as the wall the webtoon camera faces most often (the main front wall seen in the "
    "panels); east/south/west follow clockwise. "
    "Describe ONLY what is actually drawn in the panels. For every wall, list concrete visible features: "
    "windows and their rough size and position, bookshelves, cabinets, desks standing against the wall, "
    "notice boards, blackboards, framed pictures, doors. Put these in featuresEn (a short English list) "
    "and summarise the wall in one English sentence in descEn. "
    "If a wall is never shown in any panel, infer a plausible wall that is consistent with the walls you "
    "DID see — same materials, same era, same country — and describe it with concrete features too. "
    "Never leave a wall blank, and never write the words 'assumed', 'provisional', 'not visible', or "
    "'unknown' anywhere in your answer. "
    "hasWindow / hasDoor are booleans. widthM/depthM/heightM are rough room dimensions in metres. "
    "enclosed is true for a closed room. floorEn and ceilingEn describe the floor and ceiling surfaces "
    "with concrete materials and colour. lightEn is one English sentence about where the main light comes "
    "from (e.g. 'main light comes from the north-wall windows, soft daylight'). " + _SCREENTONE_RULE + " propsEn lists free-standing "
    "props as objects {labelEn, wall, sizeM} where wall is one of north/east/south/west/center. "
    "Ignore people and speech bubbles — describe only the room itself. "
    "Do not add stairs, mezzanines, walls or ceilings that the references do not show; distinguish "
    "indoor/outdoor, terrace/balcony, doors, railings and whether there is a ceiling; estimate size "
    "from people and doors. "
    "Write plain English for every *En field. Do not add text outside the tool."
)

_DESIGN_WALL = {
    "type": "object",
    "properties": {
        "descEn": {"type": "string"},
        "hasWindow": {"type": "boolean"},
        "hasDoor": {"type": "boolean"},
        "featuresEn": {"type": "string"},
    },
}

_DESIGN_TOOL = {
    "name": "emit_location",
    "description": "Describe the single place seen in the reference panels as a rectangular room.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "widthM": {"type": "number"},
                "depthM": {"type": "number"},
                "heightM": {"type": "number"},
                "enclosed": {"type": "boolean"},
                "floorEn": {"type": "string"},
                "ceilingEn": {"type": "string"},
                "walls": {
                    "type": "object",
                    "properties": {
                        "north": _DESIGN_WALL,
                        "east": _DESIGN_WALL,
                        "south": _DESIGN_WALL,
                        "west": _DESIGN_WALL,
                    },
                },
                "lightEn": {"type": "string"},
                "propsEn": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "labelEn": {"type": "string"},
                            "wall": {"type": "string"},
                            "sizeM": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["walls"],
        }
    },
}


def design_location(
    owner: str,
    pid: str,
    loc: dict,
    panels: list[bytes] | None = None,
    *,
    setting: dict | None = None,
    photo_blocks: list[dict] | None = None,
) -> dict:
    """그 장소 패널(최대 6장)을 Claude 분석 체인에 먼저, 실패하면 Astra 로 보내 설계 JSON 을 받는다.

    세로 긴 패널은 tiles_for_analysis 로 잘려 있으므로, 인물이 적고 배경이 큰 조각을 먼저 고른다.
    setting 의 locationKo·layoutEn·enclosed 와 패널 설명을 함께 준다. 결과에 'assumed'/'provisional'
    같은 말이 있으면(벽을 못 읽었다는 뜻) 1회만 더 강하게 재요청한다.
    panels: run_build 가 한 번 고른 패널(작업 125 — 매 단계 다시 읽고 사람 수를 세지 않게). None 이면 고른다.
    setting·photo_blocks: run_build 가 한 번 준비해 사실 추출과 함께 쓰는 값(작업 128). None 이면 읽는다.
    반환: 정리된 설계 JSON(아래 _normalize_design) + model.
    """
    panels = _design_panels(owner, pid, loc) if panels is None else list(panels)
    name_en = str(loc.get("nameEn") or "").strip() or "the place"
    setting = _setting_for(owner, pid) if setting is None else setting
    location_ko = str(setting.get("locationKo") or "").strip()
    layout_en = str(setting.get("layoutEn") or "").strip()
    enclosed = _as_bool(setting.get("enclosed"))

    intro_lines = [
        f"다음 웹툰 패널들은 모두 같은 장소('{name_en}')를 보여 줍니다. 이 장소를 사각형 방으로 보고 "
        "네 벽을 각각 정면 입면도로 그릴 수 있게 설계해 주세요. 카메라가 가장 자주 보는 정면 벽을 "
        "north 로 두세요. 인물·말풍선은 무시하고 방 자체만 묘사하세요.",
    ]
    if location_ko:
        intro_lines.append(f"장소(한국어): {location_ko}")
    if layout_en:
        intro_lines.append(f"Scene layout: {layout_en}")
    intro_lines.append(
        "This is a Korean setting (closed room)." if enclosed else "This is a Korean setting."
    )
    intro_lines.append(
        "Each panel below shows this place from a different angle — read the drawn walls, windows, "
        "shelves, desks and doors in the panels and base every wall on them."
    )
    photo_blocks = _photo_blocks(owner, pid, loc) if photo_blocks is None else list(photo_blocks)
    if photo_blocks:
        intro_lines.append(
            "아래에 사용자가 첨부한 실제 참고 사진이 먼저 옵니다(최우선). " + _PHOTO_PRIORITY_KO
        )
    blocks: list[dict] = [{"text": "\n".join(intro_lines)}]
    blocks.extend(photo_blocks)  # 사진을 패널보다 먼저.
    for jpeg in panels:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

    for model in (DESIGN_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _design(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.design.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        design = _normalize_design(payload)
        # 벽을 못 읽어 'assumed'/'provisional' 로 얼버무렸으면 같은 모델에 1회 더 강하게 요청한다.
        if _has_provisional(design):
            retry_blocks = blocks + [
                {
                    "text": (
                        "Your previous answer used words like 'assumed' or 'provisional' or left a wall "
                        "blank. Look again at the panels and give concrete visible features for every "
                        "wall (windows with size/position, bookshelves, cabinets, desks, notice boards, "
                        "doors). For any wall not shown, infer a plausible wall consistent with the ones "
                        "you can see. Never write 'assumed' or 'provisional'."
                    )
                }
            ]
            try:
                payload2, usage2 = _design(retry_blocks, model=model)
                design2 = _normalize_design(payload2)
                if not _has_provisional(design2):
                    payload, usage, design = payload2, usage2, design2
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.location.design.retry_failed model=%s err=%s", model, str(exc)[:200])
        design["model"] = str(usage.get("model") or model)
        _charge_text(owner, pid, loc.get("id") or "", usage, design["model"])
        return design
    raise VpocError("vnew.location.design", "DesignFailed", "공간 설계에 실패했습니다.", 502)


_PROVISIONAL_RE = re.compile(r"\b(assumed|provisional|not visible|unknown)\b", re.IGNORECASE)


def _has_provisional(design: dict) -> bool:
    """설계 텍스트에 '못 읽었다'는 얼버무림(assumed/provisional 등)이 있으면 True."""
    parts: list[str] = [
        str((design or {}).get("floorEn") or ""),
        str((design or {}).get("ceilingEn") or ""),
        str((design or {}).get("lightEn") or ""),
    ]
    for side in SIDES:
        wall = ((design or {}).get("walls") or {}).get(side) or {}
        parts.append(str(wall.get("descEn") or ""))
        parts.append(str(wall.get("featuresEn") or ""))
    return bool(_PROVISIONAL_RE.search(" ".join(parts)))


def _setting_for(owner: str, pid: str) -> dict:
    """plan.script.setting(장소 한국어·배치·밀폐 여부). 못 읽으면 빈 dict."""
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        data, _ = plan_mod.load_plan(owner, pid)
    except Exception:  # noqa: BLE001
        return {}
    setting = ((data.get("script") or {}).get("setting")) or {}
    return setting if isinstance(setting, dict) else {}


def _design_panels(owner: str, pid: str, loc: dict) -> list[bytes]:
    """그 장소가 나오는 패널 조각들(최대 MAX_DESIGN_PANELS 장). 인물이 적고 배경이 큰 조각 우선.

    panels 는 '분석 타일 인덱스'가 아니라 script.panels 와 같은 '타일 번호'다. order 를 통째로
    타일로 잘라, 그 번호의 조각을 꺼낸다. 인물 수가 적은 조각을 먼저 고른다(배경이 크게 보이는 쪽).
    """
    from . import plan as plan_mod  # noqa: PLC0415 — 순환 import 피하기

    order = list(((plan_mod.load_plan(owner, pid)[0]).get("input") or {}).get("order") or [])
    if not order:
        return []
    tiles = _all_tiles(owner, pid, order)
    wanted = [int(p) for p in (loc.get("panels") or []) if _is_int(p)]
    picked: list[bytes] = []
    for index in wanted:
        if 0 <= index < len(tiles):
            picked.append(tiles[index])
    if not picked:
        # 번호가 범위를 벗어나면(옛 데이터) 앞에서부터 채운다.
        picked = tiles[:MAX_DESIGN_PANELS]
    # 인물이 적은 조각 우선(배경이 큰 조각). 사람 수 검사가 없으면 순서 유지.
    picked = _prefer_background(picked)
    return picked[:MAX_DESIGN_PANELS]


def _all_tiles(owner: str, pid: str, order: list[str]) -> list[bytes]:
    from . import plan as plan_mod  # noqa: PLC0415

    tiles: list[bytes] = []
    for file_id in order:
        try:
            jpeg = STORE.get_bytes(plan_mod.upload_key(owner, pid, file_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.tiles.missing file=%s err=%s", file_id, str(exc)[:150])
            continue
        tiles.extend(vpoc_images.tiles_for_analysis(jpeg))
    return tiles


def _prefer_background(tiles: list[bytes]) -> list[bytes]:
    """인물이 적은 조각을 앞으로(배경이 크게 보이는 조각 우선). characters._count_people 를 쓴다.

    검사가 _count_people 를 건너뛰거나(None) 오류면 원래 순서를 그대로 둔다(안정 정렬).
    사람 수 세기는 조각마다 LLM 1번이라 LOCATION_WORKERS 개씩 동시에 부른다(작업 125 — 차례로 부르면
    조각 수 × 수 초가 걸려 설계·사실 사이 공백의 대부분이었다).
    """

    def _count(jpeg: bytes):
        try:
            return characters._count_people(jpeg)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            return None

    if len(tiles) > 1:
        with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix="vnew-loc-people") as pool:
            counts = list(pool.map(_count, tiles))
    else:
        counts = [_count(jpeg) for jpeg in tiles]
    scored: list[tuple[int, int, bytes]] = []
    for index, (jpeg, count) in enumerate(zip(tiles, counts)):
        rank = 99 if count is None else int(count)
        scored.append((rank, index, jpeg))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]


# ---------------------------------------------------------------- 참고 사진(빌드 반영, 설계자 2026-10-22)
#
# 사진이 있으면 설계·사실·그림·확인에 웹툰 패널보다 '먼저' 쓴다. 블록·참고 이미지의 '이미지 번호'가
# 프롬프트 지시와 정확히 맞아야 하므로(이미지 모델은 지능이 없다), 참고 순서를 한 곳(_view_photo_plan)
# 에서 정하고 프롬프트·생성·확인이 그 순서를 똑같이 쓴다.


def _photo_jpeg(owner: str, pid: str, lid: str, photo: dict) -> bytes | None:
    """참고 사진 1장의 JPEG 바이트. 못 읽으면 None(그 사진만 건너뛴다)."""
    key = str((photo or {}).get("key") or "").strip()
    if not key:
        pid_ = str((photo or {}).get("photoId") or "")
        key = photo_key(owner, pid, lid, pid_) if pid_ else ""
    if not key:
        return None
    try:
        return STORE.get_bytes(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.photo.missing key=%s err=%s", key, str(exc)[:150])
        return None


def _ordered_photos_for_view(loc: dict, view: str) -> list[dict]:
    """그 뷰에 넣을 참고 사진을 순서대로(면 지정 사진 먼저, 그다음 'any').

    뷰 A(back): back·left·right·floor 순. 뷰 B(front): front·left·right 순. 이어서 'any'(전체 분위기).
    같은 사진이 두 번 들어가지 않게 한다.
    """
    photos = _loc_photos(loc)
    if not photos:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for side in VIEW_PHOTO_SIDES.get(view, ()):  # 면 지정 사진.
        for p in _photos_by_side(photos, side):
            pid_ = p.get("photoId") or ""
            if pid_ not in seen:
                out.append(p)
                seen.add(pid_)
    for p in _photos_by_side(photos, "any"):  # 전체 분위기.
        pid_ = p.get("photoId") or ""
        if pid_ not in seen:
            out.append(p)
            seen.add(pid_)
    return out


def _view_photo_plan(loc: dict, view: str, *, lead: int) -> list[dict]:
    """프롬프트·생성·확인이 공유하는 '사진 참고 계획'. lead = 사진 앞에 오는 이미지 수(가이드·앞 뷰).

    남는 참고 자리(최대 MAX_REF_PANELS 뷰 = 6 - lead - 패널은 뒤)만큼 사진을 담되, 각 사진에 최종
    '이미지 번호'(imageNo = lead + 1부터)를 매긴다. 반환: [{photo, imageNo, side}], 1-based 이미지 번호.
    """
    photos = _ordered_photos_for_view(loc, view)
    # 참고 전체 상한(6장) 안에서 가이드/앞 뷰 다음에 사진을 넣는다. 패널 자리를 최소 0 으로.
    slots = max(0, 6 - lead)
    plan: list[dict] = []
    for index, photo in enumerate(photos[:slots]):
        plan.append({"photo": photo, "imageNo": lead + 1 + index, "side": photo.get("side") or "any"})
    return plan


def _view_lead(variant: str, view: str) -> int:
    """그 (그림체, 뷰) 참고의 '사진 앞에 오는 이미지 수'(가이드/앞 뷰). _generate_views 와 같아야 한다.

    real back=[가이드] → 1, real front=[가이드, real A] → 2,
    webtoon back=[real A] → 1, webtoon front=[real B, webtoon A] → 2.
    """
    return 2 if view == "front" else 1


# 설계·사실 Claude 블록 앞에 붙이는 사진 지침(설계자 2026-10-22). 사진이 패널보다 먼저 오게 한다.
_PHOTO_PRIORITY_KO = (
    "사진과 웹툰 패널이 다르면 사진을 따르세요. 면이 지정된 사진은 그 벽의 물건·재질·색·창문·문 "
    "위치를 그대로 읽으세요."
)


def _photo_label_ko(photo: dict) -> str:
    """사진 한 장의 한국어 라벨: '{면 한국어}' 또는 '{면 한국어}, {메모}'."""
    side_ko = PHOTO_SIDE_LABELS_KO.get(photo.get("side") or "any", "전체 분위기")
    note = str(photo.get("noteKo") or "").strip()
    return f"{side_ko}, {note}" if note else side_ko


def _photo_blocks(owner: str, pid: str, loc: dict) -> list[dict]:
    """설계·사실 Claude 블록에 '먼저' 넣을 사진 블록들(라벨 텍스트 + 이미지). 사진이 없으면 빈 리스트.

    형식: "사용자가 첨부한 실제 참고 사진입니다(최우선). 사진 {n}: {면 한국어}{, 메모}" + 이미지.
    """
    photos = _loc_photos(loc)
    if not photos:
        return []
    blocks: list[dict] = []
    number = 0
    for photo in photos:
        jpeg = _photo_jpeg(owner, pid, loc.get("id") or "", photo)
        if jpeg is None:
            continue
        number += 1
        blocks.append(
            {"text": f"사용자가 첨부한 실제 참고 사진입니다(최우선). 사진 {number}: {_photo_label_ko(photo)}"}
        )
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})
    return blocks


def _normalize_design(payload: dict) -> dict:
    """설계 출력을 공간 모양으로 정리한다(없는 필드 기본값 · 벽 4면 보장 · props 정리)."""
    payload = payload if isinstance(payload, dict) else {}
    walls_in = payload.get("walls")
    walls_in = walls_in if isinstance(walls_in, dict) else {}
    walls: dict[str, dict] = {}
    for side in SIDES:
        raw = walls_in.get(side)
        raw = raw if isinstance(raw, dict) else {}
        walls[side] = {
            "descEn": str(raw.get("descEn") or "").strip(),
            "hasWindow": _as_bool(raw.get("hasWindow")),
            "hasDoor": _as_bool(raw.get("hasDoor")),
            "featuresEn": str(raw.get("featuresEn") or "").strip(),
        }
    props: list[dict] = []
    raw_props = payload.get("propsEn")
    if isinstance(raw_props, list):
        for item in raw_props:
            if not isinstance(item, dict):
                continue
            label = str(item.get("labelEn") or "").strip()
            if not label:
                continue
            props.append(
                {
                    "labelEn": label,
                    "wall": str(item.get("wall") or "center").strip().lower() or "center",
                    "sizeM": _as_float(item.get("sizeM"), 0.0),
                }
            )
    return {
        "widthM": _as_float(payload.get("widthM"), 4.0),
        "depthM": _as_float(payload.get("depthM"), 4.0),
        "heightM": _as_float(payload.get("heightM"), 2.7),
        "enclosed": _as_bool(payload.get("enclosed")),
        "floorEn": str(payload.get("floorEn") or "").strip(),
        "ceilingEn": str(payload.get("ceilingEn") or "").strip(),
        "walls": walls,
        "lightEn": str(payload.get("lightEn") or "").strip(),
        "propsEn": props,
    }


# ================================================================ 사실 추출(Claude → Astra)
#
# 설계자 2026-10-21(046): 설계 JSON(벽 한두 문장)만으로는 Seedream 이 웹툰 배경과 어긋난다. 그래서
# 그 장소 패널에서 '벽별 물건(종류·수·색·재질·위치·크기)·창문·문·벽 재질·바닥·천장·빛·시대·나라'를
# 구체적으로 읽어 loc["facts"] 에 저장한다. 이 사실을 Astra 에 주어 Seedream 프롬프트를 세밀하게 쓴다.

_FACTS_SYSTEM = (
    "You are a forensic set analyst. You are given a few webtoon panels that all show the SAME place. "
    "Read the DRAWN pixels and report concrete facts about the room so an artist can rebuild each wall "
    "exactly. Answer ONLY by calling the emit_facts tool exactly once. "
    "Treat the room as a rectangle. 'north' is the wall the webtoon camera faces most often (the main "
    "front wall seen in the panels); east/south/west follow clockwise. "
    "For EVERY wall list its objects: each object is {kind, count, colorEn, materialEn, position, sizeEn, "
    "attachedTo}. kind is a concrete noun (window, bookshelf, cabinet, desk, framed picture, clock, door, "
    "radiator, poster frame). count is the exact integer you can see. position is two words joined by a "
    "space: one of 'left'/'center'/'right' then one of 'low'/'mid'/'high' (e.g. 'left high'). sizeEn is a "
    "short literal size ('about one metre wide', 'floor to ceiling'). attachedTo is 'wall' or 'floor'. "
    "Also give wallColorEn and wallMaterialEn for every wall. Give floorEn (colour, material and pattern), "
    "ceilingEn, and a windows list [{wall, position, sizeEn, frameEn, curtainEn, outsideEn}] and a doors "
    "list [{wall, position, sizeEn, materialEn, colorEn}]. lightEn is one English sentence about the main "
    "light (direction, colour temperature, strength). " + _SCREENTONE_RULE + " eraEn and countryEn describe the time period and "
    "country the room looks like. "
    "If a wall is never shown in any panel, INFER it from the walls you did see — same wall material and "
    "colour, same era, same country — and set that wall's estimated=true. Never leave a wall with no "
    "objects and no material: fill it with a plausible estimate rather than leaving it blank. "
    "Do not add stairs, mezzanines, walls or ceilings that the references do not show; distinguish "
    "indoor/outdoor, terrace/balcony, doors, railings and whether there is a ceiling; estimate size "
    "from people and doors. "
    "Describe only the room; ignore people and speech bubbles. Write plain English for every *En field."
)

_FACTS_OBJECT = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "count": {"type": "number"},
        "colorEn": {"type": "string"},
        "materialEn": {"type": "string"},
        "position": {"type": "string"},
        "sizeEn": {"type": "string"},
        "attachedTo": {"type": "string"},
    },
}

_FACTS_WALL = {
    "type": "object",
    "properties": {
        "wallColorEn": {"type": "string"},
        "wallMaterialEn": {"type": "string"},
        "estimated": {"type": "boolean"},
        "objects": {"type": "array", "items": _FACTS_OBJECT},
    },
}

_FACTS_TOOL = {
    "name": "emit_facts",
    "description": "Report concrete, literal facts about the single place shown in the reference panels.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "walls": {
                    "type": "object",
                    "properties": {
                        "north": _FACTS_WALL,
                        "east": _FACTS_WALL,
                        "south": _FACTS_WALL,
                        "west": _FACTS_WALL,
                    },
                },
                "floorEn": {"type": "string"},
                "ceilingEn": {"type": "string"},
                "windows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "wall": {"type": "string"},
                            "position": {"type": "string"},
                            "sizeEn": {"type": "string"},
                            "frameEn": {"type": "string"},
                            "curtainEn": {"type": "string"},
                            "outsideEn": {"type": "string"},
                        },
                    },
                },
                "doors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "wall": {"type": "string"},
                            "position": {"type": "string"},
                            "sizeEn": {"type": "string"},
                            "materialEn": {"type": "string"},
                            "colorEn": {"type": "string"},
                        },
                    },
                },
                "lightEn": {"type": "string"},
                "eraEn": {"type": "string"},
                "countryEn": {"type": "string"},
            },
            "required": ["walls"],
        }
    },
}


def _facts(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 사실 추출 JSON 을 받는다. (payload, usage{..model}). 검사가 바꿔 끼운다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_FACTS_SYSTEM,
        blocks=blocks,
        tool=_FACTS_TOOL,
        max_tokens=2400,
        where="vnew.location.facts",
        model=model,
    )


def extract_facts(
    owner: str,
    pid: str,
    loc: dict,
    panels: list[bytes] | None = None,
    *,
    setting: dict | None = None,
    photo_blocks: list[dict] | None = None,
) -> dict:
    """그 장소 패널(배경 큰 조각 우선, 최대 6장)에서 벽별 물건·창문·문·재질·빛·시대·나라를 읽는다.

    Claude 분석 체인 먼저, 실패하면 Astra. 보이지 않는 벽은 보이는 벽에서 '추정'(estimated=true)해 채운다.
    panels: run_build 가 한 번 고른 패널(작업 125). None 이면 고른다.
    setting·photo_blocks: run_build 가 설계와 함께 쓰려고 한 번 준비한 값(작업 128). None 이면 읽는다.
    설계 결과(loc["design"])는 쓰지 않는다 — 그래서 run_build 가 설계와 동시에 부른다(작업 128).
    반환: 정리된 사실 JSON(_normalize_facts) + model.
    """
    panels = _design_panels(owner, pid, loc) if panels is None else list(panels)
    name_en = str(loc.get("nameEn") or "").strip() or "the place"
    setting = _setting_for(owner, pid) if setting is None else setting
    location_ko = str(setting.get("locationKo") or "").strip()
    enclosed = _as_bool(setting.get("enclosed"))

    intro_lines = [
        f"These webtoon panels all show the SAME place ('{name_en}'). Read the drawn pixels and report "
        "concrete facts about each of the four walls (objects with kind/count/colour/material/position/"
        "size), the floor, the ceiling, windows, doors, the light, the era and the country. For any wall "
        "not shown, infer it from the walls you can see and mark it estimated.",
    ]
    if location_ko:
        intro_lines.append(f"Place (Korean): {location_ko}")
    intro_lines.append(
        "This is a Korean setting (closed room)." if enclosed else "This is a Korean setting."
    )
    photo_blocks = _photo_blocks(owner, pid, loc) if photo_blocks is None else list(photo_blocks)
    if photo_blocks:
        intro_lines.append(
            "아래에 사용자가 첨부한 실제 참고 사진이 먼저 옵니다(최우선). " + _PHOTO_PRIORITY_KO
        )
    blocks: list[dict] = [{"text": "\n".join(intro_lines)}]
    blocks.extend(photo_blocks)  # 사진을 패널보다 먼저.
    for jpeg in panels:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

    for model in (FACTS_MODEL, FACTS_FALLBACK_MODEL):
        try:
            payload, usage = _facts(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.facts.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        facts = _normalize_facts(payload)
        facts["model"] = str(usage.get("model") or model)
        _charge_text(owner, pid, loc.get("id") or "", usage, facts["model"])
        return facts
    raise VpocError("vnew.location.facts", "FactsFailed", "공간 사실 추출에 실패했습니다.", 502)


def _normalize_facts(payload: dict) -> dict:
    """사실 출력을 정리한다(벽 4면 보장·물건 리스트 정리·창문·문·빈 벽은 '추정'으로 표시).

    보이지 않아 비어 있는 벽(물건·재질이 모두 비면)은 estimated=true 로 둬 '비워 두지 않는다'는 요구를
    만족시킨다(실제 추정 문장은 Astra 프롬프트 작성에서 보이는 벽을 참고해 채운다).
    """
    payload = payload if isinstance(payload, dict) else {}
    walls_in = payload.get("walls")
    walls_in = walls_in if isinstance(walls_in, dict) else {}
    walls: dict[str, dict] = {}
    for side in SIDES:
        raw = walls_in.get(side)
        raw = raw if isinstance(raw, dict) else {}
        objects: list[dict] = []
        raw_objects = raw.get("objects")
        if isinstance(raw_objects, list):
            for item in raw_objects:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip()
                if not kind:
                    continue
                objects.append(
                    {
                        "kind": kind,
                        "count": _as_int(item.get("count"), 1),
                        "colorEn": str(item.get("colorEn") or "").strip(),
                        "materialEn": str(item.get("materialEn") or "").strip(),
                        "position": _norm_position(item.get("position")),
                        "sizeEn": str(item.get("sizeEn") or "").strip(),
                        "attachedTo": "floor" if str(item.get("attachedTo") or "").strip().lower() == "floor" else "wall",
                    }
                )
        wall_color = str(raw.get("wallColorEn") or "").strip()
        wall_material = str(raw.get("wallMaterialEn") or "").strip()
        estimated = _as_bool(raw.get("estimated")) or (not objects and not wall_material and not wall_color)
        walls[side] = {
            "wallColorEn": wall_color,
            "wallMaterialEn": wall_material,
            "estimated": estimated,
            "objects": objects,
        }
    windows = _norm_openings(payload.get("windows"), ("position", "sizeEn", "frameEn", "curtainEn", "outsideEn"))
    doors = _norm_openings(payload.get("doors"), ("position", "sizeEn", "materialEn", "colorEn"))
    return {
        "walls": walls,
        "floorEn": str(payload.get("floorEn") or "").strip(),
        "ceilingEn": str(payload.get("ceilingEn") or "").strip(),
        "windows": windows,
        "doors": doors,
        "lightEn": str(payload.get("lightEn") or "").strip(),
        "eraEn": str(payload.get("eraEn") or "").strip(),
        "countryEn": str(payload.get("countryEn") or "").strip(),
    }


_POS_HORIZ = ("left", "center", "right")
_POS_VERT = ("low", "mid", "high")


def _norm_position(value) -> str:
    """위치를 'left|center|right' + ' ' + 'low|mid|high' 로 정리한다. 못 읽으면 'center mid'."""
    text = str(value or "").strip().lower()
    horiz = next((h for h in _POS_HORIZ if h in text), "center")
    vert = next((v for v in _POS_VERT if v in text), "mid")
    return f"{horiz} {vert}"


def _norm_openings(raw, keys: tuple[str, ...]) -> list[dict]:
    """창문·문 리스트 정리(벽 소문자·필드 문자열). 항목이 dict 가 아니면 건너뛴다."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = {"wall": str(item.get("wall") or "").strip().lower()}
        for key in keys:
            if key == "position":
                entry[key] = _norm_position(item.get(key))
            else:
                entry[key] = str(item.get(key) or "").strip()
        out.append(entry)
    return out


def _facts_has_wall_detail(facts: dict) -> bool:
    """사실에 벽 물건·재질이 하나라도 있으면 True(추출이 의미 있게 됐는지)."""
    for side in SIDES:
        wall = ((facts or {}).get("walls") or {}).get(side) or {}
        if wall.get("objects") or wall.get("wallMaterialEn") or wall.get("wallColorEn"):
            return True
    return False


# ================================================================ 프롬프트
#
# 048 컷어웨이 뷰: 벽을 4장 따로 그리지 않고, 가이드 위에 뷰(back·front) 한 장을 그린다. 프롬프트는
# 면별 위치를 화면 기준으로 아주 구체적으로 쓴다(이미지 모델은 지능이 없으므로).

# 뷰 프롬프트 앞부분: 가이드를 지키라는 지시(검사가 "layout guide"·"do not move walls" 확인).
_GUIDE_CLAUSE = (
    "Image 1 is a layout guide: keep the exact box geometry, the corner lines and the positions of the "
    "white window and dark door rectangles. Draw over it; do not move walls."
)

# 이음새 지시(뷰 A — 검사가 "continuous" 확인).
_CONTINUOUS_CLAUSE = (
    "The left wall, back wall and right wall are one continuous room: the floor line, ceiling line, "
    "skirting, wall material and lighting continue across both corners without a break."
)

# 뷰 B 는 이음새 대신 '반대편에서 본 같은 방'(검사가 "opposite side" 확인). 작업 125: 참고 이미지가 없으므로
# 이미지 번호 대신 '뒤 보기'로 가리킨다.
_OPPOSITE_CLAUSE = (
    "Same room as the back view seen from the opposite side; its side walls are the same walls as in the "
    "back view seen from their other end."
)

# 구도 지시(작업 125, text-to-image). 가이드 이미지를 보내지 않으므로 상자 구도를 글로 적는다.
_LAYOUT_MARK = "One-point perspective box-set cutaway"

# 모든 지시문 앞에 그대로 붙이는 공통 문장(방 구조·색·재질·조명)의 최대 길이(작업 125).
ANCHOR_MAX_CHARS = 600

# 대충 그려도 된다는 지시(공간·빛이 장식보다 중요).
_BLOCKIN_CLAUSE = (
    "Simple, low-detail block-in is fine; clear shapes, readable space and light matter more than decoration."
)

# 인물·글자 금지(검사가 "No people" 확인).
_NO_PEOPLE = "No people, no text, no letters, no signs with writing."

# (옛 데이터·fallback 호환) 원근 없음 지시.
_ORTHO = (
    "Orthographic straight-on view, the wall fills the frame edge to edge, floor line at the bottom, "
    "ceiling line at the top, no perspective, no people, no furniture in front of the wall except items "
    "attached to it."
)

# 벽·바닥 프롬프트 끝에 붙이는 지시(검사가 확인): 글자·표지판 금지 + 한국 배경.
_NO_TEXT = "No text, letters, signs or labels anywhere. Korean setting."

# Astra 지침(설계자 2026-10-21, 048 → 작업 125 text-to-image). 이 문장이 그대로 Astra system 에 들어간다(검사가 확인).
ASTRA_PROMPT_INSTRUCTION = (
    "Write a prompt for a non-intelligent text-to-image model that draws a box-set cutaway view of a room. "
    "The model gets NO reference images, only your text. Be explicit and literal: describe each screen area "
    "separately — CENTER BACK WALL (main background), LEFT WALL, RIGHT WALL, FLOOR, CEILING/SKY — and for "
    "each state every object, its exact count, color, material, size and position. State where the main "
    "light comes from, its direction and which side the shadows fall. Keep the one-point perspective box "
    "geometry given in the brief. Never contradict the fixed room description. No people, no text. "
    "Do not use vague words (nice, typical, some)."
)

# 뷰 프롬프트에 반드시 들어가야 하는 보증 문구(Astra 가 빠뜨렸으면 뒤에 덧붙인다 — 검사가 확인).
_GUARD_CLAUSES = ("CENTER BACK WALL", "LEFT WALL", "RIGHT WALL", "continuous", "No people")


# ---------------------------------------------------------------- 작업 226: 실외(design.enclosed False)
#
# 글만 받는 그림 모델은 "room · wall · ceiling" 을 글자 그대로 그린다(prod: 실사 뒤 보기 = 밤 콘크리트 벽, "no wall: open
# air" 벽 = 콘크리트 벽). 실외 장소는 같은 구조(면별 · 보증 문구 · No people · 이음새)를 열린 바깥 낱말로 쓴다. 실내 글은 그대로.

OUTDOOR_OPEN_AIR = (
    "Open-air outdoor place: no walls, no ceiling, no room, no box; the sides are open surroundings that "
    "continue into the distance; the sky is visible."
)
# 실사 뷰 편집 문장(PLACE_EDIT 의 실외판). 웹툰 뷰 옆의 '경치를 그린 평평한 판'을 실제 깊이 있는 둘레로.
PLACE_EDIT_OUTDOOR = (
    "Turn this image into a real photograph of the same open-air outdoor place: {place}. " + OUTDOOR_OPEN_AIR + " Any "
    "flat painted scenery at the sides becomes real surroundings with depth; every object stands in open air under "
    "the sky. Remove all people completely; show the empty place. Keep the same layout, camera angle, light and time "
    "of day. Real photograph taken with a camera, photorealistic, natural materials and textures, no illustration, no "
    "anime, no drawing, no line art, no people. " + REAL_NO_BRAND_SENTENCE
)
_OUTDOOR_LAYOUT_MARK = "One-point perspective fixed-camera view of an open-air place"
# 화면 면 → 실외 면 이름(CENTER BACK WALL · LEFT WALL · RIGHT WALL · FLOOR · CEILING/SKY 자리).
OUTDOOR_FACE_LABELS = {
    "back": "MAIN BACKGROUND (what is behind the characters)",
    "left": "LEFT SIDE surroundings",
    "right": "RIGHT SIDE surroundings",
    "floor": "GROUND",
    "ceiling": "SKY",
}
_OUTDOOR_CONTINUOUS_CLAUSE = (
    "The left side, main background and right side are one continuous open-air place: the ground, horizon "
    "line, sky and light continue across without a break."
)
_OUTDOOR_OPPOSITE_CLAUSE = (
    "Same open-air place as the back view seen from the opposite side, with the camera turned around; its "
    "left side and right side surroundings are the same surroundings as in the back view seen from their other end."
)
ASTRA_PROMPT_INSTRUCTION_OUTDOOR = (
    "Write a prompt for a non-intelligent text-to-image model that draws a fixed-camera view of an open-air "
    "outdoor place. " + OUTDOOR_OPEN_AIR + " The model gets NO reference images, only your text. Be explicit "
    "and literal: describe each screen area separately — MAIN BACKGROUND (what is behind the characters), LEFT "
    "SIDE surroundings, RIGHT SIDE surroundings, GROUND, SKY — and for each state every object, its exact count, "
    "color, material, size and position, standing in open air. State where the main light comes from, its "
    "direction and which side the shadows fall; take the time of day only from the given light. Keep the "
    "one-point perspective layout given in the brief. Never contradict the fixed place description. No people, "
    "no text. Do not use vague words (nice, typical, some)."
)
# Astra 가 실내 면 이름을 썼으면 실외 이름으로 바꾼다(라벨만).
_OUTDOOR_RELABEL = (
    (re.compile(r"\bCENTER BACK WALL\b(?:\s*\(main background\))?", re.IGNORECASE), "MAIN BACKGROUND"),
    (re.compile(r"\bLEFT WALL\b", re.IGNORECASE), "LEFT SIDE"),
    (re.compile(r"\bRIGHT WALL\b", re.IGNORECASE), "RIGHT SIDE"),
    (re.compile(r"\bCEILING\s*/\s*SKY\b", re.IGNORECASE), "SKY"),
    (re.compile(r"\bbox-set cutaway\b", re.IGNORECASE), "fixed-camera view"),
)
# "no wall: open air; …" 같은 사람이 고친 벽 글에서 '없는 구조' 머리말을 뺀다(그림 모델은 "wall" 을 그린다).
_NO_STRUCTURE_RE = re.compile(r"\bno\s+(?:walls?|ceilings?|roofs?)\b\s*[:;,.\-–—]*\s*", re.IGNORECASE)
# 낮 판단(lightEn). 밤 낱말이 하나라도 있으면 낮으로 보지 않는다.
_DAY_LIGHT_RE = re.compile(r"\b(?:day\w*|sun(?:ny|light|lit|shine)?|morning|noon|midday|afternoon|(?:bright|blue|clear) sky)\b",
                           re.IGNORECASE)
_NIGHT_LIGHT_RE = re.compile(r"\b(?:night\w*|evenings?|dusk|twilight|sunset|sundown|moon\w*|star(?:lit|light|ry)|after dark)\b",
                             re.IGNORECASE)
# 낮 실외 글에서 밤 · 저녁 · 어두운 하늘 낱말을 낮 낱말로 바꾼다(순서대로).
_NIGHT_SCRUB = tuple((re.compile(p, re.IGNORECASE), r) for p, r in (
    (r"\b(?:(?:dark|black|deep|starry|cross-hatched)[ ,-]+)*(?:night|night-time|nighttime|evening|dusk|twilight|"
     r"dark(?:ened)?|starry|starlit|moonlit|black)[ -](?:sky|skies)\b", "bright daytime sky"),
    (r"\bnight[- ]?time\b", "daytime"),
    (r"\b(?:at|by) night\b", "in daytime"),
    (r"\bafter dark\b", "in daytime"),
    (r"\bnights?\b", "daytime"),
    (r"\bevenings?\b", "daytime"),
    (r"\b(?:dusk|twilight|sunset|sundown)\b", "daytime"),
    (r"\bmoonlight\b", "daylight"),
    (r"\bmoonlit\b", "sunlit"),
    (r"\bmoons?\b", "sun"),
    (r"\bstar(?:lit|light|ry)\b", "clear"),
    (r"\bdarkness\b", "daylight"),
    (r"\bdark(?:er|est|ened)\b", "deeper"),
    (r"\bdark\b", "deep"),
))


def _is_outdoor(loc: dict) -> bool:
    """실외 장소 = design.enclosed 가 거짓. 설계가 없거나 enclosed 키가 없으면 실내(226 전과 같다)."""
    design = loc.get("design")
    if not isinstance(design, dict) or "enclosed" not in design:
        return False
    return not _as_bool(design.get("enclosed"))


def _light_text(loc: dict) -> str:
    """지시문의 'Light:' 글(사실 lightEn 먼저, 없으면 설계 lightEn)."""
    return str((loc.get("facts") or {}).get("lightEn") or (loc.get("design") or {}).get("lightEn") or "").strip()


def _outdoor_daytime(loc: dict) -> bool:
    """실외이고 lightEn 이 낮(낮 낱말 있음 · 밤 낱말 없음)이면 True. 빛 글이 없으면 False(밤을 덧붙이지도 않는다)."""
    if not _is_outdoor(loc):
        return False
    light = _light_text(loc)
    return bool(_DAY_LIGHT_RE.search(light)) and not _NIGHT_LIGHT_RE.search(light)


def _day_guard(text: str, loc: dict) -> str:
    """낮 실외 글에서 밤 · 저녁 · 어두운 낱말을 지운다(빗금 스크린톤을 '밤하늘'로 읽은 사실 대비). 그 밖에는 그대로."""
    if not _outdoor_daytime(loc):
        return text
    for pattern, repl in _NIGHT_SCRUB:
        text = pattern.sub(repl, text)
    return text


def _strip_no_structure(text: str) -> str:
    return _NO_STRUCTURE_RE.sub("", str(text or "")).strip(" ;,.")


def _outdoor_relabel(text: str) -> str:
    for pattern, repl in _OUTDOOR_RELABEL:
        text = pattern.sub(repl, text)
    return text


def _style_clause(variant: str, loc: dict) -> str:
    """그림체 지시. real=실사, webtoon=프로젝트 원작 그림체(캐릭터 analysis.styleEn 있으면 그것).
    작업 223: 웹툰은 화풍 줄 + 인물과 같은 선 굵기 · 칠 · 팔레트 문장(VIEW_STYLE_223 — 새 그림만, 저장된 그림 · 지시문은 그대로)."""
    if variant == "real":
        return "Photoreal, natural light."
    style = str(loc.get("styleEn") or "").strip()
    if VIEW_STYLE_223:
        match = VIEW_MATCH_CHARACTERS_OUTDOOR if _is_outdoor(loc) else VIEW_MATCH_CHARACTERS  # 작업 226: 실외는 벽 낱말 없이
        return (style or WEBTOON_STYLE_DEFAULT).rstrip(" .") + ". " + match.format(palette=view_palette(loc))
    if style:
        return style + " Match this drawing style exactly."
    # 작업 125: 참고 패널을 그림 모델에 보내지 않으므로 '참고 패널 그림체' 대신 글로 적는다.
    return "Korean webtoon drawing style: clean line art, flat cel shading, soft colors."


# 작업 223: 웹툰 장소 뷰가 인물과 같은 그림체 · 팔레트(벽 · 바닥 질감 · 참고 뷰). 검사(223 전 기대값)만 끈다(check_bg_harmony_223).
VIEW_STYLE_223 = True
WEBTOON_STYLE_DEFAULT = "Korean webtoon drawing style: clean line art, flat cel shading, soft colors"
VIEW_MATCH_CHARACTERS = ("Match this drawing style exactly: walls, floor, props and textures use the same line weight, cel "
                         "shading and palette as the webtoon characters ({palette}).")
# 작업 226: 실외 장소판(벽 · 바닥 대신 둘레 · 땅).
VIEW_MATCH_CHARACTERS_OUTDOOR = ("Match this drawing style exactly: surroundings, ground, props and textures use the same "
                                 "line weight, cel shading and palette as the webtoon characters ({palette}).")
_VIEW_PALETTE_RE = re.compile(r"[^.;,]*\b(?:palette|pastel|colou?rs?|tones?|hues?|saturat\w*|monochrome)\b[^.;,]*", re.IGNORECASE)


def view_palette(loc: dict) -> str:
    """작업 223: 장소 뷰 팔레트 = 화풍 줄(styleEn)의 색 구절 + 벽 색(사실). 없으면 웹툰 기본 팔레트."""
    pieces: list[str] = []
    match = _VIEW_PALETTE_RE.search(str(loc.get("styleEn") or ""))
    if match and match.group(0).strip():
        pieces.append(" ".join(match.group(0).split()))
    colors: list[str] = []
    for side in SIDES:
        color = str((((loc.get("facts") or {}).get("walls") or {}).get(side) or {}).get("wallColorEn") or "").strip()
        if color and color.lower() not in (c.lower() for c in colors):
            colors.append(color)
    if colors:
        pieces.append(" and ".join(colors[:2]) + (" surroundings" if _is_outdoor(loc) else " walls"))  # 226: 실외는 벽 낱말 없이
    return "; ".join(pieces) or "soft webtoon colors"


def _layout_clause(loc: dict, view: str) -> str:
    """상자 구도 지시(작업 125). 방 치수로 계산한 뒤 벽 사각형 크기를 글로 적는다(가이드 이미지 대신).

    cut 단계는 verify 가 생성 그림에서 다시 찾은 꼭짓점(4% 넘게 다르면)으로 펴므로, 여기 비율은 목표값이다.
    """
    design = loc.get("design") or {}
    size = _size_wh(VIEW_SIZE)
    try:
        geom = location_views.box_view_geometry(
            _as_float(design.get("widthM"), 4.0),
            _as_float(design.get("depthM"), 4.0),
            _as_float(design.get("heightM"), 2.7),
            view=view,
            size=size,
        )
        xs = [float(p[0]) for p in geom["back"]]
        ys = [float(p[1]) for p in geom["back"]]
        width_pct = round((max(xs) - min(xs)) / size[0] * 100)
        height_pct = round((max(ys) - min(ys)) / size[1] * 100)
    except Exception:  # noqa: BLE001 — 치수가 이상하면 보통 비율
        width_pct, height_pct = 45, 50
    if _is_outdoor(loc):
        # 작업 226: 같은 상자 비율(뒤 면 = 메인 배경 영역)을 열린 바깥 낱말로.
        return (
            f"{_OUTDOOR_LAYOUT_MARK}, seen straight on from a fixed camera height at eye level, looking level. "
            f"The main background is an area centred in the image, about {width_pct}% of the image width and "
            f"{height_pct}% of the image height; the left side and right side surroundings, the ground and the sky "
            "fill the areas between it and the image edges and recede into the distance. "
            "Straight vertical lines, level horizon, no tilt, no fisheye."
        )
    return (
        f"{_LAYOUT_MARK} of a rectangular room with its front wall removed, seen straight on from outside "
        "the open front at eye height. The back wall is a flat rectangle centred in the image, about "
        f"{width_pct}% of the image width and {height_pct}% of the image height; the left wall, right wall, "
        "floor and ceiling are trapezoids that meet its four corners and widen toward the image edges. "
        "Straight vertical corner lines, level floor line, no tilt, no fisheye."
    )


def _room_anchor(loc: dict) -> str:
    """4개 지시문 앞에 그대로 붙이는 공통 문장(작업 125): 방 구조·벽 색·재질·바닥·천장·빛·시대.

    참고 이미지 없이 그리므로 그림체 간·뷰 간 같은 방이 되게 사실(facts)·설계에서 결정적으로 만든다.
    같은 장소·같은 사실이면 언제나 같은 문장이다.
    """
    design = loc.get("design") or {}
    facts = loc.get("facts") or {}
    if _is_outdoor(loc):
        return _outdoor_anchor(loc)
    name_en = str(loc.get("nameEn") or "").strip() or "the room"
    width = _as_float(design.get("widthM"), 4.0)
    depth = _as_float(design.get("depthM"), 4.0)
    height = _as_float(design.get("heightM"), 2.7)
    kind = "closed room" if _as_bool(design.get("enclosed")) else "room"
    parts = [
        f"Same room in every image: {name_en}, a rectangular {kind} about {width:g} m wide, "
        f"{depth:g} m deep and {height:g} m high."
    ]
    surfaces: list[str] = []
    for side in SIDES:
        wall = (facts.get("walls") or {}).get(side) or {}
        surface = " ".join(
            p for p in (str(wall.get("wallColorEn") or "").strip(), str(wall.get("wallMaterialEn") or "").strip()) if p
        )
        if surface and surface not in surfaces:
            surfaces.append(surface)
    if surfaces:
        parts.append("Wall surfaces: " + "; ".join(surfaces) + ".")
    floor = str(facts.get("floorEn") or design.get("floorEn") or "").strip()
    ceiling = str(facts.get("ceilingEn") or design.get("ceilingEn") or "").strip()
    light = str(facts.get("lightEn") or design.get("lightEn") or "").strip()
    if floor:
        parts.append(f"Floor: {floor}.")
    if ceiling:
        parts.append(f"Ceiling: {ceiling}.")
    windows = len(facts.get("windows") or [])
    doors = len(facts.get("doors") or [])
    if windows or doors:
        parts.append(f"{windows} window(s) and {doors} door(s), always in the same places.")
    if light:
        parts.append(f"Light: {light}.")
    era = " ".join(p for p in (str(facts.get("eraEn") or "").strip(), str(facts.get("countryEn") or "").strip()) if p)
    if era:
        parts.append(f"Era and country: {era}.")
    return " ".join(parts)[:ANCHOR_MAX_CHARS].rstrip()


def _anchor_for(loc: dict) -> str:
    """run_build 가 사실 정리 뒤 만든 공통 문장(loc['anchorEn']). 없으면 지금 사실로 만든다."""
    return str(loc.get("anchorEn") or "").strip() or _room_anchor(loc)


def _outdoor_anchor(loc: dict) -> str:
    """작업 226: 실외 장소의 공통 문장(_room_anchor 의 실외판). 방 · 벽 · 천장 낱말 없이 둘레 · 땅 · 하늘 · 빛 · 시대."""
    design = loc.get("design") or {}
    facts = loc.get("facts") or {}
    name_en = str(loc.get("nameEn") or "").strip() or "the place"
    width = _as_float(design.get("widthM"), 4.0)
    depth = _as_float(design.get("depthM"), 4.0)
    parts = [
        f"Same open-air place in every image: {name_en}, an outdoor area about {width:g} m wide and {depth:g} m "
        "deep, seen from a fixed camera height at eye level, with open sky overhead."
    ]
    surroundings: list[str] = []
    for side in SIDES:
        wall = (facts.get("walls") or {}).get(side) or {}
        surface = " ".join(
            p for p in (_strip_no_structure(wall.get("wallColorEn")), _strip_no_structure(wall.get("wallMaterialEn"))) if p
        )
        if surface and surface not in surroundings and surface.lower().replace("-", " ") != "open air":
            surroundings.append(surface)
    if surroundings:
        parts.append("Surroundings: " + "; ".join(surroundings) + ".")
    ground = str(facts.get("floorEn") or design.get("floorEn") or "").strip()
    sky = _strip_no_structure(facts.get("ceilingEn") or design.get("ceilingEn"))
    light = _light_text(loc).rstrip(" .")
    if ground:
        parts.append(f"Ground: {ground}.")
    parts.append(f"Sky: {sky or 'open sky'}.")
    windows = len(facts.get("windows") or [])
    doors = len(facts.get("doors") or [])
    if windows or doors:
        parts.append(f"{windows} window(s) and {doors} door(s), always in the same places.")
    if light:
        parts.append(f"Light: {light}.")
    era = " ".join(p for p in (str(facts.get("eraEn") or "").strip(), str(facts.get("countryEn") or "").strip()) if p)
    if era:
        parts.append(f"Era and country: {era}.")
    return _day_guard(" ".join(parts), loc)[:ANCHOR_MAX_CHARS].rstrip()


# ---------------------------------------------------------------- 사실 → 사람이 읽는 설명


def _wall_facts_text(side: str, loc: dict) -> str:
    """사실(facts)에서 그 벽의 물건·재질·창문·문을 사람이 읽는 영어 문단으로 편다.

    Astra 가 없을 때의 결정적 프롬프트와, 비교·보정 때 Claude 에 주는 '사실 목록'에 함께 쓴다.
    """
    facts = loc.get("facts") or {}
    wall = (facts.get("walls") or {}).get(side) or {}
    lines: list[str] = []
    color = str(wall.get("wallColorEn") or "").strip()
    material = str(wall.get("wallMaterialEn") or "").strip()
    if material or color:
        lines.append(f"Wall surface: {color} {material}".strip() + ".")
    if wall.get("estimated"):
        lines.append("(This wall is not directly visible; it is inferred from the visible walls.)")
    for obj in wall.get("objects") or []:
        count = _as_int(obj.get("count"), 1)
        kind = str(obj.get("kind") or "").strip()
        if not kind:
            continue
        color = str(obj.get("colorEn") or "").strip()
        material = str(obj.get("materialEn") or "").strip()
        size = str(obj.get("sizeEn") or "").strip()
        position = str(obj.get("position") or "center mid").strip()
        attached = str(obj.get("attachedTo") or "wall").strip()
        piece = f"{count} {color} {material} {kind}".replace("  ", " ").strip()
        detail = [p for p in (size, f"at the {position}", f"on the {attached}") if p]
        lines.append(f"{piece} ({', '.join(detail)}).")
    for opening, label in ((facts.get("windows") or [], "window"), (facts.get("doors") or [], "door")):
        for item in opening:
            if str(item.get("wall") or "").strip().lower() != side:
                continue
            bits = [str(item.get(k) or "").strip() for k in ("sizeEn", "frameEn", "curtainEn", "outsideEn", "materialEn", "colorEn")]
            bits = [b for b in bits if b]
            pos = str(item.get("position") or "").strip()
            lines.append(f"A {label} at the {pos}: {', '.join(bits)}." if bits else f"A {label} at the {pos}.")
    return " ".join(lines).strip()


def _outdoor_side_text(side: str, loc: dict) -> str:
    """작업 226: 실외 장소의 면별 글(_wall_facts_text 의 실외판). 벽 표면이 아니라 그쪽으로 열린 경치 + 바깥에 선 물건.

    사실 물건이 있으면 그것(수 · 색 · 위치), 없으면 설계 featuresEn 을 쓴다. 검증용 사실 글은 _wall_facts_text 그대로.
    """
    design_wall = ((loc.get("design") or {}).get("walls") or {}).get(side) or {}
    wall = ((loc.get("facts") or {}).get("walls") or {}).get(side) or {}
    features = _strip_no_structure(design_wall.get("featuresEn") if isinstance(design_wall, dict) else "")
    if any(str(obj.get("kind") or "").strip() for obj in wall.get("objects") or []):
        features = ""  # 사실 물건(수 · 색 · 위치)이 있으면 그것만 — 같은 물건을 두 번 쓰면 지시문 상한에서 뒤가 잘린다.
    lines = [f"Open view toward the {side}" + (f": {features}." if features else ".")]
    surface = " ".join(p for p in (_strip_no_structure(wall.get("wallColorEn")), _strip_no_structure(wall.get("wallMaterialEn"))) if p)
    if surface and surface.lower().replace("-", " ") != "open air":  # "open air" 만 남으면 OUTDOOR_OPEN_AIR 와 겹친다
        lines.append(f"Surroundings: {surface}.")
    if wall.get("estimated"):
        lines.append("(This side is not directly visible; it is inferred from the visible sides.)")
    standing: list[str] = []
    for obj in wall.get("objects") or []:
        kind = str(obj.get("kind") or "").strip()
        if not kind:
            continue
        count = _as_int(obj.get("count"), 1)
        color = str(obj.get("colorEn") or "").strip()
        material = str(obj.get("materialEn") or "").strip()
        size = str(obj.get("sizeEn") or "").strip()
        position = str(obj.get("position") or "center mid").strip()
        piece = f"{count} {color} {material} {kind}".replace("  ", " ").strip()
        detail = [p for p in (size, f"at the {position}") if p]
        standing.append(f"{piece} ({', '.join(detail)})")
    if standing:
        lines.append("Standing in open air: " + "; ".join(standing) + ".")
    facts = loc.get("facts") or {}
    for opening, label in ((facts.get("windows") or [], "window"), (facts.get("doors") or [], "door")):
        for item in opening:
            if str(item.get("wall") or "").strip().lower() != side:
                continue
            bits = [str(item.get(k) or "").strip() for k in ("sizeEn", "frameEn", "curtainEn", "outsideEn", "materialEn", "colorEn")]
            bits = [b for b in bits if b]
            pos = str(item.get("position") or "").strip()
            lines.append(f"A {label} at the {pos}: {', '.join(bits)}." if bits else f"A {label} at the {pos}.")
    return _day_guard(" ".join(lines).strip(), loc)


def _facts_context(side: str, loc: dict, *, outdoor: bool = False) -> str:
    """사실 전체 맥락(빛·시대·나라·바닥·천장) + 그 벽 사실. Astra·결정적 프롬프트·비교에 공통으로 쓴다.

    outdoor=True(작업 226, 실외 지시문 늘리기 전용): 면별 글은 _outdoor_side_text, 천장 대신 Sky. 비교(검증)는 기본값 그대로.
    """
    facts = loc.get("facts") or {}
    if outdoor:
        sky = _strip_no_structure(facts.get("ceilingEn") or (loc.get("design") or {}).get("ceilingEn"))
        bits = [_outdoor_side_text(side, loc)]
        era_country = " ".join(p for p in (str(facts.get("eraEn") or "").strip(), str(facts.get("countryEn") or "").strip()) if p)
        if era_country:
            bits.append(f"Era/country: {era_country}.")
        if _light_text(loc):
            bits.append(f"Light: {_light_text(loc).rstrip(' .')}.")
        bits.append(f"Sky: {sky or 'open sky'}.")
        return _day_guard(" ".join(b for b in bits if b).strip(), loc)
    parts = [_wall_facts_text(side, loc)]
    era = str(facts.get("eraEn") or "").strip()
    country = str(facts.get("countryEn") or "").strip()
    light = str(facts.get("lightEn") or "").strip()
    ceiling = str(facts.get("ceilingEn") or "").strip()
    if era or country:
        parts.append(f"Era/country: {era} {country}".strip() + ".")
    if light:
        parts.append(f"Light: {light}.")
    if ceiling:
        parts.append(f"Ceiling: {ceiling}.")
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------- Astra 프롬프트 작성

_PROMPT_TOOL = {
    "name": "emit_image_prompt",
    "description": "Return one literal image-generation prompt for a non-intelligent image model.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {"promptEn": {"type": "string"}},
            "required": ["promptEn"],
        }
    },
}


def _author(system_text: str, user_text: str, *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 프롬프트 한 문장을 받는다. (payload, usage). 검사가 바꿔 끼운다."""
    return vpoc_llm._converse(  # noqa: SLF001
        system_text=system_text,
        blocks=[{"text": user_text}],
        tool=_PROMPT_TOOL,
        max_tokens=1400,
        where="vnew.location.prompt",
        model=model,
    )


def _view_face_facts(loc: dict, view: str) -> dict[str, str]:
    """뷰(back·front)의 화면 면별 사실 문단을 돌려준다.

    반환 키: 'back'(CENTER BACK WALL), 'left'(LEFT WALL), 'right'(RIGHT WALL), 'floor', 'ceiling'.
    각 값은 그 면에 그릴 벽의 사실(물건·재질·창문·문)을 사람이 읽는 영어 문단.
    """
    out: dict[str, str] = {}
    outdoor = _is_outdoor(loc)
    for face in ("back", "left", "right"):
        # 그 화면 면에 해당하는 설계 벽(north/east/south/west)을 찾는다.
        side = None
        for s in SIDES:
            if _wall_to_face(view, s) == face:
                side = s
                break
        if outdoor:  # 작업 226: 실외 = 그쪽으로 열린 경치.
            out[face] = _outdoor_side_text(side, loc) if side else ""
            continue
        out[face] = _wall_facts_text(side, loc) if side else ""
    facts = loc.get("facts") or {}
    out["floor"] = str(facts.get("floorEn") or "").strip()
    out["ceiling"] = str(facts.get("ceilingEn") or "").strip()
    if outdoor:
        out["floor"] = _day_guard(out["floor"], loc)
        out["ceiling"] = _day_guard(
            _strip_no_structure(facts.get("ceilingEn") or (loc.get("design") or {}).get("ceilingEn")) or "open sky", loc)
    return out


def _author_prompt(owner: str, pid: str, loc: dict, *, variant: str, view: str) -> str:
    """컷어웨이 뷰(back·front) 한 장의 Seedream 프롬프트를 Astra 가 쓴다(실패 시 Claude, 둘 다 실패 시 템플릿).

    면별 위치를 화면 기준으로 구체적으로 쓰게 하고, 보증 문구(CENTER BACK WALL/LEFT WALL/RIGHT WALL/
    continuous(또는 opposite side)/No people)를 보장한다. 작업 125: 그림 모델은 참고 이미지 없이 글만 받으므로
    real·webtoon 모두 사실(facts) 면별 설명 + 공통 문장 + 구도 글로 쓴다(그림체만 다르다).
    """
    name_en = str(loc.get("nameEn") or "").strip() or "the room"
    style = _style_clause(variant, loc)
    faces = _view_face_facts(loc, view)
    light = str((loc.get("facts") or {}).get("lightEn") or (loc.get("design") or {}).get("lightEn") or "").strip()

    seam = "continuous room" if view == "back" else "same room seen from the opposite side"
    user_text = (
        f"Place: {name_en}. Write a prompt for a box-set cutaway {view}-view of this room. The image model "
        "gets NO reference images, only your text.\n"
        f"Fixed room description (it is placed verbatim before your prompt; never contradict it): {_anchor_for(loc)}\n"
        f"Camera and layout: {_layout_clause(loc, view)}\n"
        f"Drawing style: {style}\n"
        f"CENTER BACK WALL (main background): {faces['back']}\n"
        f"LEFT WALL: {faces['left']}\n"
        f"RIGHT WALL: {faces['right']}\n"
        f"FLOOR: {faces['floor']}\n"
        f"CEILING/SKY: {faces['ceiling']}\n"
        f"Light: {light}\n"
        f"This is one {seam}; corners, floor line and lighting continue without a break.\n"
        f"Write ONE image prompt of {PROMPT_MIN_CHARS} to {PROMPT_MAX_CHARS} characters. "
        "Call emit_image_prompt once with promptEn."
    )
    system_text = ASTRA_PROMPT_INSTRUCTION
    if _is_outdoor(loc):  # 작업 226: 실외는 같은 구조를 열린 바깥 낱말로.
        user_text, system_text = _outdoor_author_brief(loc, variant=variant, view=view), ASTRA_PROMPT_INSTRUCTION_OUTDOOR
    for model in (PROMPT_AUTHOR_MODEL, PROMPT_AUTHOR_FALLBACK_MODEL):
        try:
            payload, usage = _author(system_text, user_text, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.prompt.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        text = str((payload or {}).get("promptEn") or "").strip()
        if not text:
            continue
        _charge_text(owner, pid, loc.get("id") or "", usage, str(usage.get("model") or model))
        return _finalize_prompt(text, variant, view, loc)
    # 둘 다 실패 — 결정적 템플릿(사실을 그대로 편 것)으로 떨어진다.
    return _finalize_prompt(_view_prompt(variant, view, loc), variant, view, loc)


def _outdoor_author_brief(loc: dict, *, variant: str, view: str) -> str:
    """작업 226: 실외 장소의 Astra 글(_author_prompt user_text 의 실외판). 같은 순서 · 같은 면 · 열린 바깥 낱말."""
    name_en = str(loc.get("nameEn") or "").strip() or "the place"
    faces = _view_face_facts(loc, view)
    labels = OUTDOOR_FACE_LABELS
    seam = "continuous open-air place" if view == "back" else "open-air place seen from the opposite side"
    text = (
        f"Place: {name_en}. Write a prompt for a fixed-camera {view}-view of this open-air outdoor place. The image "
        "model gets NO reference images, only your text.\n"
        f"{OUTDOOR_OPEN_AIR}\n"
        f"Fixed place description (it is placed verbatim before your prompt; never contradict it): {_anchor_for(loc)}\n"
        f"Camera and layout: {_layout_clause(loc, view)}\n"
        f"Drawing style: {_style_clause(variant, loc)}\n"
        f"{labels['back']}: {faces['back']}\n"
        f"{labels['left']}: {faces['left']}\n"
        f"{labels['right']}: {faces['right']}\n"
        f"{labels['floor']}: {faces['floor']}\n"
        f"{labels['ceiling']}: {faces['ceiling']}\n"
        f"Light and time of day (use only this): {_light_text(loc)}\n"
        f"This is one {seam}; the ground, horizon, sky and light continue without a break.\n"
        f"Write ONE image prompt of {PROMPT_MIN_CHARS} to {PROMPT_MAX_CHARS} characters. "
        "Call emit_image_prompt once with promptEn."
    )
    return _day_guard(text, loc)


def _finalize_outdoor(text: str, variant: str, view: str, loc: dict) -> str:
    """작업 226: _finalize_prompt 의 실외판. 같은 보증(구도 · 그림체 · 면별 · 이음새 · block-in · No people · 글자 금지 ·
    추가 요청 · 길이 · 실사 상표)을 열린 바깥 낱말로. 공통 문장 바로 뒤에 OUTDOOR_OPEN_AIR. 낮이면 밤 낱말을 지운다."""
    text = _outdoor_relabel((text or "").strip())
    anchor = _anchor_for(loc)
    if anchor and text.startswith(anchor):
        text = text[len(anchor):].strip()
    text = " ".join(text.replace(OUTDOOR_OPEN_AIR, " ").split())
    if _OUTDOOR_LAYOUT_MARK not in text:
        text += " " + _layout_clause(loc, view)
    style = _style_clause(variant, loc)
    front = OUTDOOR_OPEN_AIR
    if VIEW_STYLE_223 and variant != "real":
        text = " ".join(text.replace(style, " ").split())
        front += " " + style
    elif style.split(".")[0] not in text:
        text += " " + style
    labels = OUTDOOR_FACE_LABELS
    if any(labels[f].split(" (")[0] not in text for f in ("back", "left", "right")):
        faces = _view_face_facts(loc, view)
        text += " " + " ".join(f"{labels[f]}: {faces[f]}" for f in ("back", "left", "right", "floor", "ceiling"))
    if view == "front":
        if "opposite side" not in text:
            text += " " + _OUTDOOR_OPPOSITE_CLAUSE
    elif "continuous" not in text:
        text += " " + _OUTDOOR_CONTINUOUS_CLAUSE
    if "block-in" not in text:
        text += " " + _BLOCKIN_CLAUSE
    if "No people" not in text:
        text += " " + _NO_PEOPLE
    text += " " + _NO_TEXT
    # 낮이면 밤 낱말을 지운다. 사용자 추가 요청(extraEn)은 사람이 쓴 그대로 둔다.
    text = _with_extra(_day_guard(front + " " + text, loc), loc).strip()
    if anchor:
        text = anchor + " " + text
    if len(text) < PROMPT_MIN_CHARS:
        filler = _facts_context("north", loc, outdoor=True)
        while len(text) < PROMPT_MIN_CHARS and filler:
            text += " " + filler
    brand = " " + REAL_NO_BRAND_SENTENCE if variant == "real" else ""
    if len(text) + len(brand) > PROMPT_HARD_CAP:
        text = text[:PROMPT_HARD_CAP - len(brand)].rstrip()
    return text + brand


def _photo_prompt_clause(loc: dict, variant: str, view: str) -> str:
    """사진이 들어간 뷰의 프롬프트 지시(이미지 번호를 실제 참고 순서와 맞춘다). 사진 없으면 빈 문자열.

    작업 125 부터 공간 시트는 참고 이미지 없이 그리므로 _finalize_prompt 가 이 함수를 부르지 않는다(옛 경로용으로 둔다).

    면 지정 사진: "Image N is a real reference photo of the {WALL}: copy its objects, materials, colors,
    openings and light exactly, but keep Image 1's box geometry and corner positions."
    'any' 사진: "Image N shows the real look of this place (materials, colors, objects, light) — follow it."
    """
    lead = _view_lead(variant, view)
    plan = _view_photo_plan(loc, view, lead=lead)
    if not plan:
        return ""
    parts: list[str] = []
    for item in plan:
        n = item["imageNo"]
        side = item["side"]
        wall = PHOTO_SIDE_WALL_EN.get(side)
        if side == "any" or not wall:
            parts.append(
                f"Image {n} shows the real look of this place (materials, colors, objects, light) — follow it."
            )
        else:
            parts.append(
                f"Image {n} is a real reference photo of the {wall}: copy its objects, materials, colors, "
                "openings and light exactly, but keep Image 1's box geometry and corner positions."
            )
    return " ".join(parts)


def _finalize_prompt(text: str, variant: str, view: str, loc: dict) -> str:
    """Astra 가 쓴 프롬프트에 보증 문구·그림체·가이드 지시를 보장하고, 사용자 추가 요청을 붙이고, 길이를 맞춘다.

    - 공통 문장(_anchor_for, 작업 125)을 맨 앞에 그대로 붙인다(4개 지시문이 같은 문장으로 시작).
    - 구도 지시(_layout_clause)·면별 라벨(_GUARD_CLAUSES)·인물 금지(_NO_PEOPLE)가 빠졌으면 뒤에 덧붙인다.
    - 뷰 A 는 "continuous", 뷰 B 는 "opposite side" 문장을 보장한다.
    - 참고 이미지가 없으므로 사진 '이미지 번호' 지시는 넣지 않는다(사진은 사실 정리에 글로 들어가 있다).
    - PROMPT_MIN_CHARS 미만이면 사실 설명을 덧대 늘린다. PROMPT_HARD_CAP 초과면 뒤를 자른다(앞 공통 문장 보존).
    - 작업 226: 실외(_is_outdoor)는 _finalize_outdoor(같은 보증, 열린 바깥 낱말).
    """
    if _is_outdoor(loc):
        return _finalize_outdoor(text, variant, view, loc)
    text = (text or "").strip()
    anchor = _anchor_for(loc)
    if anchor and text.startswith(anchor):
        text = text[len(anchor):].strip()
    if _LAYOUT_MARK not in text:
        text += " " + _layout_clause(loc, view)
    style = _style_clause(variant, loc)
    front = ""
    if VIEW_STYLE_223 and variant != "real":
        # 작업 223: 화풍 줄 + 인물과 같은 선 · 칠 · 팔레트 문장은 공통 문장 바로 뒤(상한 자르기에서 빠지지 않게, 한 번만).
        text = " ".join(text.replace(style, " ").split())
        front = style
    elif style.split(".")[0] not in text:
        text += " " + style
    faces = _view_face_facts(loc, view)
    if "CENTER BACK WALL" not in text:
        text += (
            f" CENTER BACK WALL (main background): {faces['back']}"
            f" LEFT WALL: {faces['left']} RIGHT WALL: {faces['right']} FLOOR: {faces['floor']}"
            f" CEILING/SKY: {faces['ceiling']}"
        )
    # 이음새/반대편 보장.
    if view == "front":
        if "opposite side" not in text:
            text += " " + _OPPOSITE_CLAUSE
    else:
        if "continuous" not in text:
            text += " " + _CONTINUOUS_CLAUSE
    if "block-in" not in text:
        text += " " + _BLOCKIN_CLAUSE
    if "No people" not in text:
        text += " " + _NO_PEOPLE
    text += " " + _NO_TEXT
    text = _with_extra(text, loc)
    if front:
        text = front + " " + text.strip()
    if anchor:
        text = anchor + " " + text.strip()
    # 너무 짧으면 사실 설명으로 늘린다(1,500자 미만 방지).
    if len(text) < PROMPT_MIN_CHARS:
        filler = _facts_context("north", loc)
        while len(text) < PROMPT_MIN_CHARS and filler:
            text += " " + filler
    # 작업 161: 실사만 상표 금지 1문장을 맨 끝에(상한 자르기에서도 남게 자리를 먼저 뺀다). 웹툰은 그대로.
    brand = " " + REAL_NO_BRAND_SENTENCE if variant == "real" else ""
    if len(text) + len(brand) > PROMPT_HARD_CAP:
        text = text[:PROMPT_HARD_CAP - len(brand)].rstrip()
    return text + brand


def _view_prompt(variant: str, view: str, loc: dict) -> str:
    """결정적(Astra 없이) 뷰 프롬프트. 사실(facts)을 면별로 펴서 쓴다. _finalize_prompt 가 보증 문구를 채운다."""
    if _is_outdoor(loc):  # 작업 226: 실외 = 열린 바깥 낱말, 같은 구조.
        name_en = str(loc.get("nameEn") or "").strip() or "the place"
        faces = _view_face_facts(loc, view)
        labels = OUTDOOR_FACE_LABELS
        seam = _OUTDOOR_CONTINUOUS_CLAUSE if view == "back" else _OUTDOOR_OPPOSITE_CLAUSE
        return (
            f"{_layout_clause(loc, view)} Fixed-camera {view}-view of {name_en}. {OUTDOOR_OPEN_AIR} "
            + " ".join(f"{labels[f]}: {faces[f]}" for f in ("back", "left", "right", "floor", "ceiling"))
            + f" {seam} {_BLOCKIN_CLAUSE} {_NO_PEOPLE}"
        )
    name_en = str(loc.get("nameEn") or "").strip() or "the room"
    faces = _view_face_facts(loc, view)
    seam = _CONTINUOUS_CLAUSE if view == "back" else _OPPOSITE_CLAUSE
    prompt = (
        f"{_layout_clause(loc, view)} Box-set cutaway {view}-view of {name_en}. "
        f"CENTER BACK WALL (main background): {faces['back']} "
        f"LEFT WALL: {faces['left']} RIGHT WALL: {faces['right']} "
        f"FLOOR: {faces['floor']} CEILING/SKY: {faces['ceiling']} "
        f"{seam} {_BLOCKIN_CLAUSE} {_NO_PEOPLE}"
    )
    return prompt


def _with_extra(prompt: str, loc: dict) -> str:
    extra = str(loc.get("extraEn") or "").strip()
    if extra:
        return prompt + " " + extra
    return prompt


# ================================================================ 2x2 시트 붙이기(옛 데이터용)


def montage_2x2(walls: dict[str, bytes]) -> bytes:
    """벽 4장(N/E/S/W)을 2x2 로 붙인 한 장 시트(JPEG). 각 칸에 작은 라벨을 얹는다.

    배치: 좌상 N, 우상 E, 좌하 S, 우하 W. 각 칸은 같은 크기(WALL_SIZE)로 맞춘다.
    """
    cell_w, cell_h = _size_wh(WALL_SIZE)
    sheet = Image.new("RGB", (cell_w * 2, cell_h * 2), (24, 24, 24))
    positions = {
        "north": (0, 0),
        "east": (cell_w, 0),
        "south": (0, cell_h),
        "west": (cell_w, cell_h),
    }
    draw = ImageDraw.Draw(sheet)
    for side in SIDES:
        jpeg = walls.get(side)
        x, y = positions[side]
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as tile:
                tile = tile.convert("RGB").resize((cell_w, cell_h))
                sheet.paste(tile, (x, y))
        label = side[0].upper()  # N/E/S/W
        # 반투명 느낌의 어두운 배지 + 흰 글자(작게).
        draw.rectangle([x + 8, y + 8, x + 44, y + 40], fill=(0, 0, 0))
        draw.text((x + 18, y + 14), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# ================================================================ 뷰 시트(048)


def montage_views(back_view: bytes, walls: dict[str, bytes], floor: bytes) -> bytes:
    """컷어웨이 뷰 시트(JPEG): 위 줄 = 뷰 A 크게, 아래 줄 = 펴진 텍스처 LEFT|BACK|RIGHT|FRONT|FLOOR.

    각 아래 칸 위에 영어 라벨(PIL 기본 폰트). 048 새 시트. 기존 montage_2x2 는 옛 데이터용으로 둔다.
    """
    sheet_w = 2560
    top_h = 1000
    cell_h = 440
    label_h = 34
    bottom_h = cell_h + label_h
    sheet = Image.new("RGB", (sheet_w, top_h + bottom_h + 24), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)

    # 위 줄: 뷰 A(back) 크게(가로 꽉 채움).
    if back_view:
        with Image.open(io.BytesIO(back_view)) as top:
            ratio = min(sheet_w / top.width, top_h / top.height)
            size = (max(1, int(top.width * ratio)), max(1, int(top.height * ratio)))
            top = top.convert("RGB").resize(size)
            sheet.paste(top, ((sheet_w - size[0]) // 2, (top_h - size[1]) // 2))

    # 아래 줄: 펴진 텍스처 5칸(LEFT | BACK (main) | RIGHT | FRONT (opposite) | FLOOR).
    cells = [
        ("LEFT", walls.get("west")),
        ("BACK (main)", walls.get("north")),
        ("RIGHT", walls.get("east")),
        ("FRONT (opposite)", walls.get("south")),
        ("FLOOR", floor),
    ]
    cell_w = sheet_w // len(cells)
    y0 = top_h + 24
    for index, (label, jpeg) in enumerate(cells):
        x0 = index * cell_w
        draw.text((x0 + 8, y0), label, fill=(230, 230, 230))
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as tile:
                tile = tile.convert("RGB").resize((cell_w - 8, cell_h))
                sheet.paste(tile, (x0 + 4, y0 + label_h))

    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


# ================================================================ 키 규칙


def _var_dir(owner: str, pid: str, lid: str, variant: str) -> str:
    from . import plan as plan_mod  # noqa: PLC0415

    return f"{plan_mod._root(owner, pid)}/locations/{lid}/{variant}"  # noqa: SLF001


def wall_key(owner: str, pid: str, lid: str, variant: str, side: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/wall_{side}_v{version}.jpg"


def view_key(owner: str, pid: str, lid: str, variant: str, view: str, version: int) -> str:
    """컷어웨이 뷰 원본 키(048). view 는 'back'|'front'. .../{variant}/v{version}/view-{view}.jpg."""
    return f"{_var_dir(owner, pid, lid, variant)}/v{version}/view-{view}.jpg"


def floor_key(owner: str, pid: str, lid: str, variant: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/floor_v{version}.jpg"


def sheet_key(owner: str, pid: str, lid: str, variant: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/sheet_v{version}.jpg"


def photos_prefix(owner: str, pid: str, lid: str) -> str:
    """그 장소 참고 사진이 들어가는 S3 접두사. locations/{lid}/photos/ 아래에만 둔다(같은 출처 검증용)."""
    from . import plan as plan_mod  # noqa: PLC0415

    return f"{plan_mod._root(owner, pid)}/locations/{lid}/photos/"  # noqa: SLF001


def photo_key(owner: str, pid: str, lid: str, photo_id: str) -> str:
    """참고 사진 1장의 S3 키. photos/{photoId}.jpg(정규화 JPEG)."""
    return f"{photos_prefix(owner, pid, lid)}{photo_id}.jpg"


# ================================================================ 빌드


def start_build(owner: str, pid: str, lid: str, extra_prompt_ko: str | None = None) -> str:
    """공간 시트 만들기 시작. 진행 중이면 409. 새 task.id 를 돌려준다(백그라운드가 이 id 로 확인)."""
    from . import plan as plan_mod  # noqa: PLC0415

    new_id = secrets.token_hex(8)

    def mutator(data: dict) -> str:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.build", "NotFound", "장소를 찾을 수 없습니다.", 404)
        task = loc.get("task") or _empty_task()
        if task.get("status") == "running" and not _task_stale(task):
            if not _interrupted(owner, pid, loc):
                raise VpocError("vnew.location.build", "Building", "이미 공간 시트를 만들고 있습니다.", 409)
            # 작업 128: 이 파드에서 돌지 않고 heartbeat 가 LOCATION_STALL_S 보다 오래됨 → 끊긴 작업. 정리하고 새로 시작한다.
            log.info("vnew.location.build.interrupted pid=%s lid=%s old_task=%s", pid, lid, task.get("id"))
        if extra_prompt_ko is not None:
            cleaned = str(extra_prompt_ko or "").strip()[:MAX_EXTRA_PROMPT_LEN]
            if cleaned:
                loc["extraPromptKo"] = cleaned
            else:
                loc.pop("extraPromptKo", None)
        loc["version"] = int(loc.get("version") or 0) + 1
        loc["status"] = "building"
        loc["error"] = ""
        loc["task"] = {"status": "running", "startedAt": now_iso(), "id": new_id}
        loc["progress"] = {
            "step": "design",
            "done": 0,
            "total": TOTAL_VIEWS,
            "startedAt": now_iso(),
            "updatedAt": now_iso(),
        }
        return new_id

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn(run_build, owner, pid, lid, new_id, name=f"vnew-loc-{lid}")
    return new_id


def start_build_all(owner: str, pid: str) -> list[str]:
    """아직 진행 중이 아닌 모든 장소의 공간 시트 만들기 시작. 시작한 lid 목록을 돌려준다."""
    from . import plan as plan_mod  # noqa: PLC0415

    data, _ = plan_mod.load_plan(owner, pid)
    started: list[str] = []
    for loc in data.get("locations") or []:
        lid = str(loc.get("id") or "")
        task = loc.get("task") or {}
        if not lid or (task.get("status") == "running" and not _task_stale(task) and not _interrupted(owner, pid, loc)):
            continue
        try:
            start_build(owner, pid, lid, None)
            started.append(lid)
        except VpocError as exc:
            if exc.kind == "Building":
                continue
            raise
    return started


def run_build(owner: str, pid: str, lid: str, task_id: str) -> None:
    """공간 시트 만들기(백그라운드 스레드, 048 컷어웨이 뷰).

    설계 → 사실 → 프롬프트 → 뷰 4장(그림체별 back·front) → verify(꼭짓점) → cut(잘라 펴기) → sheet → done.
    뷰에서 각 면을 원근 보정해 기존 wall_key/floor_key 로 저장한다(previz 수정 없이 동작).
    task_id 가 현재 task.id 와 다르면(취소·재실행) 결과를 버린다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    if not _mark_running(owner, pid, lid):
        return
    # 작업 128: 빌드 동안 LOCATION_HEARTBEAT_S 마다 heartbeatAt 을 민다(다른 파드·재시작 후 조회가 끊김을 판정).
    heartbeat = _Heartbeat(owner, pid, lid, task_id, LOCATION_HEARTBEAT_S)
    try:
        heartbeat.start()
        data, _ = plan_mod.load_plan(owner, pid)
        loc = _find_location(data, lid)
        if loc is None:
            return
        version = int(loc.get("version") or 1)

        # 그림체(webtoon)용 원작 그림체: 연결된 캐릭터 analysis.styleEn 이 있으면 쓴다.
        loc = dict(loc)
        loc["styleEn"] = _project_style(owner, data)
        loc["extraEn"] = _extra_en(loc)
        build_started = time.monotonic()

        # a0. 패널 고르기(작업 125): S3 에서 업로드를 읽어 타일로 자르고 사람 수를 센다. 예전에는 설계·사실·
        #     그림 단계가 저마다 다시 해서(사람 수 = 조각마다 LLM 1번, 차례로) 단계 사이에 1~2분 공백이 생겼다.
        #     이제 빌드마다 한 번만 하고 설계·사실·검사가 같은 목록을 쓴다.
        _set_progress(owner, pid, lid, task_id, step="design", done=0, total=TOTAL_VIEWS)
        started = time.monotonic()
        panels = _design_panels(owner, pid, loc)
        _timing(pid, lid, "panels", started)

        # a1. 설계·사실 공통 입력(작업 128): 장소 설정은 이미 읽은 plan 에서, 참고 사진은 S3 에서 한 번만 읽어
        #     블록을 만든다. 설계와 사실이 같은 bytes(패널·사진)를 쓴다. 예전에는 둘이 저마다 plan 을 다시 읽고
        #     사진을 다시 받았다.
        started = time.monotonic()
        setting = ((data.get("script") or {}).get("setting")) or {}
        setting = setting if isinstance(setting, dict) else {}
        photo_blocks = _photo_blocks(owner, pid, loc)
        _timing(pid, lid, "facts_prep", started)

        # a. 설계(Claude → Astra)와 a2. 사실 추출(Claude → Astra)을 동시에 한다(작업 128). 사실 추출은 설계 결과를
        #    쓰지 않는다(입력 = 장소 이름·설정·사진·패널). 실패·대체 모델 규칙은 각 함수 안에서 지금과 같다.
        #    [다시 만들기] 는 설계를 새로 한다 — 예전 설계(벽 4면 "흰 벽 가정")를 재사용하면 창문 없는 방이 그대로
        #    남기 때문. 다만 사용자가 설계를 직접 고친 적이 있으면(designEditedAt) 그 수정본을 유지한다. 설계가 아직
        #    없으면(walls 없음) 당연히 새로 받는다. 사실도 사용자가 고친 적이 있으면(factsEditedAt) 수정본을 쓴다.
        #    장소 사진·웹툰 패널은 여기서만 Claude 가 본다 — 그림 모델에는 글로만 간다(작업 125).
        design = loc.get("design") or {}
        need_design = not design.get("walls") or not bool(design.get("designEditedAt"))
        facts = loc.get("facts") or {}
        need_facts = not bool(facts.get("factsEditedAt"))

        def _timed(step: str, func, *args, **kwargs):
            step_started = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                _timing(pid, lid, step, step_started)

        shared = {"setting": setting, "photo_blocks": photo_blocks}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"vnew-loc-read-{lid}") as pool:
            design_future = (
                pool.submit(_timed, "design", design_location, owner, pid, dict(loc), panels, **shared)
                if need_design else None
            )
            facts_future = (
                pool.submit(_timed, "facts", extract_facts, owner, pid, dict(loc), panels, **shared)
                if need_facts else None
            )
            if design_future is not None:
                design = design_future.result()
                _store_design(owner, pid, lid, design)
                loc["design"] = design
            else:
                _timing(pid, lid, "design", time.monotonic())
            _set_progress(owner, pid, lid, task_id, step="facts", done=0, total=TOTAL_VIEWS)
            if facts_future is not None:
                facts = facts_future.result()
                _store_facts(owner, pid, lid, facts)
            else:
                _timing(pid, lid, "facts", time.monotonic())
        loc["facts"] = facts
        # 공통 문장(방 구조·색·재질·조명) 1개 — 4개 지시문 앞에 그대로 붙는다(작업 125).
        loc["anchorEn"] = _room_anchor(loc)

        # a3. 가이드·프롬프트. 방 치수로 컷어웨이 뷰 가이드(PIL)를 그리고(verify·cut 기준), 뷰별 프롬프트
        #     4개(Astra→Claude)를 동시에 쓴다.
        _set_progress(owner, pid, lid, task_id, step="prompts", done=0, total=TOTAL_VIEWS)
        started = time.monotonic()
        geometries = _build_geometries(loc)
        guides = _build_guides(loc, geometries)
        prompts = _author_all_prompts(owner, pid, loc)
        loc["prompts"] = prompts
        _timing(pid, lid, "prompts", started)

        # b. 뷰 4장(그림체별 back·front)을 참고 이미지 없이 동시에 그린다(fal text-to-image, 작업 125).
        #    뷰 한 장이 끝날 때마다 done +1. 작업 143(기본 켬): 웹툰 2장 먼저, 실사 2장은 같은 쪽 웹툰 뷰 편집.
        _set_progress(owner, pid, lid, task_id, step="images", done=0, total=TOTAL_VIEWS)
        started = time.monotonic()
        refs = panels[:MAX_REF_PANELS]  # verify(Claude)가 볼 패널. 그림 모델에는 보내지 않는다.
        views = _generate_views(owner, pid, lid, version, loc, guides, refs, task_id)
        _timing(pid, lid, "images", started)

        # b2. verify(Claude). 뷰마다 패널 일치·가이드 구도(꼭짓점)를 보고, 꼭짓점 차이가 크면 Claude 꼭짓점을
        #     쓴다. (a)(c) 불일치면 그 뷰만 1회 다시 그린다. 뷰 4개를 동시에 한다. 반환: {variant:{view:{...}}}.
        _set_progress(owner, pid, lid, task_id, step="verify", done=TOTAL_VIEWS, total=TOTAL_VIEWS)
        started = time.monotonic()
        checks = _verify_views(owner, pid, lid, version, loc, guides, refs, views, geometries, task_id)
        _timing(pid, lid, "verify", started)

        # c. cut — 뷰에서 각 면을 원근 보정해 벽·바닥 텍스처(기존 wall_key/floor_key)로 저장한다.
        _set_step(owner, pid, lid, task_id, step="cut")
        started = time.monotonic()
        cut = _cut_textures(owner, pid, lid, version, views, geometries, checks)
        _timing(pid, lid, "cut", started)

        # d. 시트 합치기(뷰 A 크게 + 펴진 텍스처 줄).
        _set_step(owner, pid, lid, task_id, step="sheet")
        started = time.monotonic()
        for variant in VARIANTS:
            _assemble_variant(owner, pid, lid, variant, version, views, cut, checks, task_id)
        _timing(pid, lid, "sheet", started)

        _set_step(owner, pid, lid, task_id, step="done")
        _commit(owner, pid, lid, task_id, status="ready")
        _timing(pid, lid, "total", build_started)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.build.failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _commit(owner, pid, lid, task_id, status="failed", error=message)
    finally:
        heartbeat.stop()
        _clear_running(owner, pid, lid)


def _build_geometries(loc: dict) -> dict[str, dict]:
    """뷰별 box_view_geometry 를 계산한다(back·front). 실외(enclosed False)면 천장을 하늘로 표시.

    반환: {view: geometry}. geometry 는 location_views.box_view_geometry 결과(면별 꼭짓점 + _size 등).
    """
    design = loc.get("design") or {}
    width = _as_float(design.get("widthM"), 4.0)
    depth = _as_float(design.get("depthM"), 4.0)
    height = _as_float(design.get("heightM"), 2.7)
    enclosed = _as_bool(design.get("enclosed"))
    size = _size_wh(VIEW_SIZE)
    out: dict[str, dict] = {}
    for view in VIEWS:
        geom = location_views.box_view_geometry(width, depth, height, view=view, size=size)
        geom["_sky"] = not enclosed  # 실외면 천장 사다리꼴을 하늘색으로.
        out[view] = geom
    return out


def _build_guides(loc: dict, geometries: dict[str, dict]) -> dict[str, bytes]:
    """뷰별 가이드 이미지(PIL)를 그린다. 사실(facts)의 창문·문·계단을 화면 면 위에 투영해 표시한다."""
    size = _size_wh(GUIDE_SIZE)
    out: dict[str, bytes] = {}
    for view in VIEWS:
        openings = _openings_for_view(loc, view)
        light = _light_for_view(loc, view)
        out[view] = location_views.draw_guide(geometries[view], openings, light, size=size)
    return out


def _wall_to_face(view: str, wall: str) -> str:
    """사실의 벽(north/east/south/west)이 그 뷰의 어느 화면 면(center/left/right)에 그려지는지.

    뷰 back: north=center(back), west=left, east=right. 뷰 front: south=center(back), east=left, west=right.
    바닥·천장은 floor/ceiling 면으로. 못 맞으면 "".
    """
    wall = str(wall or "").strip().lower()
    if view == "back":
        return {"north": "back", "west": "left", "east": "right"}.get(wall, "")
    return {"south": "back", "east": "left", "west": "right"}.get(wall, "")


def _openings_for_view(loc: dict, view: str) -> dict:
    """사실의 창문·문을 그 뷰의 화면 면(back/left/right) + 위치로 바꾼 가이드 openings."""
    facts = loc.get("facts") or {}
    windows: list[dict] = []
    doors: list[dict] = []
    for win in facts.get("windows") or []:
        face = _wall_to_face(view, win.get("wall"))
        if face:
            windows.append({"wall": face, "position": win.get("position")})
    for door in facts.get("doors") or []:
        face = _wall_to_face(view, door.get("wall"))
        if face:
            doors.append({"wall": face, "position": door.get("position")})
    # 계단: 사실의 물건 kind 에 'stair' 가 있으면 그 벽 면에 표시.
    stairs: list[dict] = []
    for side in SIDES:
        wall = (facts.get("walls") or {}).get(side) or {}
        for obj in wall.get("objects") or []:
            if "stair" in str(obj.get("kind") or "").lower():
                face = _wall_to_face(view, side)
                if face:
                    stairs.append({"wall": face})
    return {"windows": windows, "doors": doors, "stairs": stairs}


def _light_for_view(loc: dict, view: str) -> dict:
    """주 광원이 어느 화면 면에서 오는지(가이드 명암용). 창이 있는 벽을 광원으로 본다."""
    facts = loc.get("facts") or {}
    for win in facts.get("windows") or []:
        face = _wall_to_face(view, win.get("wall"))
        if face:
            return {"wall": face}
    # 창이 없으면 center(뒤 벽)를 약하게 밝힌다.
    return {"wall": "center"}


def _author_all_prompts(owner: str, pid: str, loc: dict) -> dict[tuple[str, str], str]:
    """뷰2 × 그림체2 의 Seedream 프롬프트를 Astra(실패 시 Claude)로 쓴다(048).

    반환: {(variant, view): promptEn}. view 는 'back'|'front'. 설계자: 이미지 모델은 지능이 없으므로
    면별 위치를 화면 기준으로 아주 구체적으로 쓴다(CENTER BACK WALL/LEFT WALL/RIGHT WALL/FLOOR).
    작업 125: 4개는 서로 독립이라 LOCATION_WORKERS 개까지 동시에 부른다. 실패 처리·대체 모델(Astra→Claude→
    템플릿)은 _author_prompt 안에서 지금과 같다.
    """
    jobs = [(variant, view) for variant in VARIANTS for view in VIEWS]
    with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix="vnew-loc-prompt") as pool:
        futures = {
            job: pool.submit(_author_prompt, owner, pid, loc, variant=job[0], view=job[1]) for job in jobs
        }
        prompts: dict[tuple[str, str], str] = {job: futures[job].result() for job in jobs}
    return prompts


def _prompt_for(loc: dict, variant: str, view: str) -> str:
    """미리 작성한 뷰 프롬프트(loc['prompts'])가 있으면 그것, 없으면 결정적 템플릿."""
    pre = (loc.get("prompts") or {}).get((variant, view))
    if pre:
        return pre
    return _finalize_prompt(_view_prompt(variant, view, loc), variant, view, loc)


def _generate_views(
    owner: str,
    pid: str,
    lid: str,
    version: int,
    loc: dict,
    guides: dict[str, bytes],
    refs: list[bytes],
    task_id: str,
) -> dict[tuple[str, str], tuple[bytes, str]]:
    """컷어웨이 뷰 4장(그림체별 back·front)을 그린다(설계자 048 → 작업 125).

    작업 125: 참고 이미지 없이 fal text-to-image 로 그리므로 4장이 서로 독립이다 → LOCATION_WORKERS 개까지
    동시에 그린다. guides·refs 는 그림 모델에 보내지 않는다(verify 가 쓴다). 같은 방이 되게 하는 것은
    지시문 앞 공통 문장(_room_anchor)이다.
    반환: {(variant, view): (jpeg, key)}. 저장·원장은 그림당 1회. 완료마다 progress.done +1.
    한 장이라도 실패하면 나머지가 끝난 뒤(저장·원장은 된 채로) 첫 오류를 올린다.

    작업 143(VNEW_LOCATION_REAL_FROM_WEBTOON 켬, 기본): 웹툰 뷰 2장을 먼저 동시에 그리고, 실사 뷰 2장은 같은 쪽
    웹툰 뷰를 참고 1장으로 fal Seedream 4.5 edit(_place_edit_prompt)로 동시에 만든다. 웹툰 뷰가 실패한 쪽의
    실사 뷰는 지금처럼 text-to-image 로 그린다. 끄면 작업 125 그대로.
    """
    results: dict[tuple[str, str], tuple[bytes, str]] = {}

    def _draw(variant: str, view: str, base: bytes | None = None) -> tuple[bytes, str]:
        if base is not None:
            image = _seedream_edit(_edit_prompt_for(loc), base, size=VIEW_SIZE)
            model = LOCATION_EDIT_MODEL
        else:
            prompt = _prompt_for(loc, variant, view)
            image = _seedream(prompt, [], size=VIEW_SIZE)
            model = LOCATION_IMAGE_MODEL
        key = view_key(owner, pid, lid, variant, view, version)
        STORE.put_bytes(key, image, "image/jpeg")
        _charge_seedream(owner, pid, lid, f"{variant}-view-{view}", model=model)
        with _PROGRESS_LOCK:
            _bump_progress(owner, pid, lid, task_id)
        return image, key

    jobs = [(variant, view) for variant in VARIANTS for view in VIEWS]
    errors: dict[tuple[str, str], Exception] = {}

    def _run(batch: list[tuple[str, str]], bases: dict[str, bytes]) -> None:
        with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix=f"vnew-loc-view-{lid}") as pool:
            futures = {job: pool.submit(_draw, job[0], job[1], bases.get(job[1])) for job in batch}
            for job in batch:
                try:
                    results[job] = futures[job].result()
                except Exception as exc:  # noqa: BLE001
                    errors[job] = exc

    if _real_from_webtoon():
        _run([job for job in jobs if job[0] == "webtoon"], {})
        bases = {view: results[("webtoon", view)][0] for view in VIEWS if ("webtoon", view) in results}
        _run([job for job in jobs if job[0] == "real"], bases)
    else:
        _run(jobs, {})
    for job in jobs:
        if job in errors:
            raise errors[job]
    return {job: results[job] for job in jobs}


# ================================================================ verify(Claude, 꼭짓점)
#
# 설계자 2026-10-21(048): 뷰마다 (패널·가이드·생성 뷰)를 Claude 에 주고 {matchesPlace, matchesSides,
# backQuad, frontQuad, fixesEn} 를 받는다. backQuad/frontQuad 는 생성 그림에서 다시 찾은 뒤 벽·앞 열린
# 면 4 꼭짓점의 정규화 좌표(0..1). 가이드 꼭짓점과 4% 넘게 다르면 Claude 꼭짓점으로 잘라 편다(이상하면
# 가이드 좌표). matchesPlace/matchesSides 불일치면 그 뷰만 1회 다시 그린다(fixesEn 덧붙임).

_VERIFY_SYSTEM = (
    "You check a generated box-set cutaway view of a room against a layout guide and reference webtoon "
    "panels. Answer ONLY by calling emit_verify exactly once. "
    "matchesPlace = true if the generated view shows the same place as the panels (structure, main "
    "objects, light). backQuad = the four corner points of the CENTER BACK WALL rectangle in the "
    "generated image, frontQuad = the four corners of the front open face (the whole room opening), each "
    "as normalized [x,y] in 0..1, ordered top-left, top-right, bottom-right, bottom-left. "
    "matchesSides = true only for the front view, when its side walls show the same material and objects "
    "as the side walls of the back view (pass true for the back view). "
    "If matchesPlace or matchesSides is false, give fixesEn: a short list of concrete English corrections "
    "an image model can follow."
)

_VERIFY_TOOL = {
    "name": "emit_verify",
    "description": "Report place/side match and the back-wall and front-face corner points of the view.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "matchesPlace": {"type": "boolean"},
                "matchesSides": {"type": "boolean"},
                "backQuad": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "frontQuad": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "fixesEn": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["matchesPlace"],
        }
    },
}

# 작업 227: 실외(enclosed false) verify 글. 방 · 벽 · 천장 낱말은 OUTDOOR_OPEN_AIR 부정 문장에만 있다.
# backQuad/frontQuad 는 cut(_faces_from_corners)이 쓰는 같은 두 사각형 — 가이드 가운데 사각형 · 바깥 테두리.
_VERIFY_SYSTEM_OUTDOOR = (
    "You check a generated fixed-camera view of an open-air outdoor place against a layout guide and reference "
    "webtoon panels. " + OUTDOOR_OPEN_AIR + " Answer ONLY by calling emit_verify exactly once. "
    "matchesPlace = true if the generated view shows the same open-air place as the panels: the same main objects "
    "(listed in the request), the same time of day and light, open sky overhead, and nothing that encloses the place. "
    "backQuad = the four corner points of the MAIN BACKGROUND region in the generated image (the center rectangle of "
    "the layout guide: what is straight behind the characters, between the left side and right side surroundings, "
    "above the ground), frontQuad = the four corners of the full frame opening (the outer rectangle of the layout "
    "guide, where the left side, right side, ground and sky areas end), each as normalized [x,y] in 0..1, ordered "
    "top-left, top-right, bottom-right, bottom-left. If you cannot find these corners, leave backQuad and frontQuad out. "
    "matchesSides = true only for the front view, when its left side and right side surroundings show the same "
    "surroundings and objects as the left and right sides of the back view (pass true for the back view). "
    "If matchesPlace or matchesSides is false, give fixesEn: a short list of concrete English corrections an image "
    "model can follow, each naming only outdoor objects, ground, sky, surroundings or light of this open-air place, "
    "keeping the given time of day."
)
_VERIFY_TOOL_OUTDOOR = {
    **_VERIFY_TOOL,
    "description": "Report place/side match and the main-background and full-frame corner points of the view.",
}
# 작업 227: 실외 fixesEn 에서 버릴 문장(방 · 벽 · 천장 · 상자 · 실내 가구). 가구 낱말은 이 장소 사실 · 설계 물건에 있으면 둔다.
_OUTDOOR_FIX_STRUCTURE_RE = re.compile(
    r"\b(?:rooms?|walls?|ceilings?|interiors?|indoors?|box(?:es)?|cutaway)\b", re.IGNORECASE)
_OUTDOOR_FIX_FURNITURE_RE = re.compile(
    r"\b(?:desks?|chairs?|beds?|sofas?|couch(?:es)?|shel(?:f|ves)|bookshel(?:f|ves)|cabinets?|wardrobes?|"
    r"window[- ]?frames?)\b", re.IGNORECASE)
_FIX_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
VERIFY_OBJECTS_MAX_CHARS = 600


def _singular(word: str) -> str:
    word = word.replace("-", " ")
    irregular = {"shelves": "shelf", "bookshelves": "bookshelf", "couches": "couch"}
    return irregular.get(word, word[:-1] if word.endswith("s") else word)

def _outdoor_objects(loc: dict) -> list[str]:
    """작업 227: 실외 장소의 주요 물건(사실 면별 물건 → 설계 propsEn → 사실 물건이 없으면 설계 featuresEn). 중복 없이."""
    design = loc.get("design") or {}
    facts = loc.get("facts") or {}
    out: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = " ".join(_strip_no_structure(text).split())
        if text and text.lower() not in seen and text.lower().replace("-", " ") != "open air":
            seen.add(text.lower())
            out.append(text)

    for side in SIDES:
        for obj in ((facts.get("walls") or {}).get(side) or {}).get("objects") or []:
            kind = str(obj.get("kind") or "").strip()
            if kind:
                _add(f"{str(obj.get('colorEn') or '').strip()} {kind}")
    for prop in design.get("propsEn") or []:
        if isinstance(prop, dict):
            _add(str(prop.get("labelEn") or ""))
    if not out:
        for side in SIDES:
            wall = (design.get("walls") or {}).get(side)
            if isinstance(wall, dict):
                _add(str(wall.get("featuresEn") or ""))
    return out


def _outdoor_fixes(fixes: list[str], loc: dict) -> list[str]:
    """작업 227: 실외 fixesEn 에서 방 · 벽 · 천장 · 실내 가구를 묻는 문장, 낮이면 밤 문장을 버린다. 남은 문장은 _day_guard.

    가구 낱말(desk · chair · shelf …)은 이 장소 물건 목록에 같은 낱말이 있으면 둔다(가게 앞 의자 · 유리 너머 진열대).
    """
    own = " ".join(_outdoor_objects(loc)).lower()
    daytime = _outdoor_daytime(loc)
    kept: list[str] = []
    for fix in fixes:
        sentences: list[str] = []
        for sentence in _FIX_SENTENCE_SPLIT_RE.split(str(fix or "").strip()):
            if not sentence.strip():
                continue
            if _OUTDOOR_FIX_STRUCTURE_RE.search(sentence):
                continue
            furniture = [m.group(0).lower() for m in _OUTDOOR_FIX_FURNITURE_RE.finditer(sentence)]
            if any(_singular(word) not in own for word in furniture):
                continue
            if daytime and _NIGHT_LIGHT_RE.search(sentence):
                continue
            sentences.append(_day_guard(sentence.strip(), loc))
        if sentences:
            kept.append(" ".join(sentences))
    return kept


def _verify(blocks: list[dict], *, model: str, system_text: str | None = None, tool: dict | None = None) -> tuple[dict, dict]:
    """llm._converse 로 뷰 검증 {matchesPlace, matchesSides, backQuad, frontQuad, fixesEn} 를 받는다.

    검사가 바꿔 끼운다. 작업 227: 실외는 system_text · tool 을 넘긴다(실내는 넘기지 않는다 — 227 전 그대로).
    """
    return vpoc_llm._converse(  # noqa: SLF001
        system_text=system_text or _VERIFY_SYSTEM,
        blocks=blocks,
        tool=tool or _VERIFY_TOOL,
        max_tokens=VERIFY_MAX_TOKENS,
        where="vnew.location.verify",
        model=VERIFY_MODEL,
    )


def _verify_views(
    owner: str,
    pid: str,
    lid: str,
    version: int,
    loc: dict,
    guides: dict[str, bytes],
    refs: list[bytes],
    views: dict[tuple[str, str], tuple[bytes, str]],
    geometries: dict[str, dict],
    task_id: str,
) -> dict[str, dict[str, dict]]:
    """뷰마다 패널 일치·가이드 구도(꼭짓점)를 보고, 불일치면 그 뷰만 1회 다시 그린다.

    - 비교는 Claude(VERIFY_MODEL). 가이드 + 패널 + 생성 뷰를 준다.
    - backQuad/frontQuad 가 가이드와 4% 넘게 다르면 Claude 꼭짓점을 쓴다(볼록·화면 안일 때). 이상하면 가이드.
    - matchesPlace/matchesSides 불일치면 fixesEn 을 붙여 1회 다시 그리고 같은 키에 덮어쓴다(total·done +1).
    - 반환: {variant: {view: {matchesPlace, matchesSides, fixedEn, fixesEn, backQuad, frontQuad, method}}}.
      backQuad/frontQuad 는 cut 이 쓸 '최종' 꼭짓점(정규화). method 는 'guide' | 'claude'.
    """
    checks: dict[str, dict[str, dict]] = {v: {} for v in VARIANTS}
    outdoor = _is_outdoor(loc)  # 작업 227

    def _check_one(variant: str, view: str, image: bytes | None = None, redraw: bool = True) -> tuple[dict, bytes | None]:
        """뷰 1장 검사(+필요하면 다시 그리기). 반환: (checks 항목, 다시 그린 그림 또는 None).

        작업 227: image 를 주면 그 그림을 검사한다(교체한 실사). redraw=False 면 불일치여도 다시 그리지 않는다.
        """
        if image is None:
            image, key = views[(variant, view)]
        else:
            key = views[(variant, view)][1]
        geom = geometries[view]
        size = geom.get("_size") or _size_wh(VIEW_SIZE)
        guide_back = _normalize_quad(geom.get("back"), size)
        guide_front = _normalize_quad(geom.get("_front"), size)
        try:
            payload, usage = _verify_view(owner, pid, lid, loc, variant, view, image, guides[view], refs)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.verify.failed pid=%s lid=%s view=%s err=%s", pid, lid, view, str(exc)[:200])
            return {
                "matchesPlace": True, "matchesSides": True, "fixedEn": False, "fixesEn": [],
                "backQuad": guide_back, "frontQuad": guide_front, "method": "guide",
            }, None
        matches_place = _as_bool(payload.get("matchesPlace"))
        matches_sides = _as_bool(payload.get("matchesSides")) if view == "front" else True
        raw_fixes = payload.get("fixesEn")
        fixes = [str(f).strip() for f in raw_fixes if str(f).strip()] if isinstance(raw_fixes, list) else []
        if outdoor:  # 작업 227: 방 · 벽 · 천장 · 실내 가구 · (낮이면) 밤 문장을 버린다.
            kept = _outdoor_fixes(fixes, loc)
            if len(kept) != len(fixes):
                log.info("vnew.location.verify.fixes_dropped pid=%s lid=%s variant=%s view=%s dropped=%d kept=%d",
                         pid, lid, variant, view, len(fixes) - len(kept), len(kept))
            fixes = kept

        # 꼭짓점: Claude 가 준 값이 가이드와 4% 넘게 다르고 볼록·화면 안이면 Claude 좌표, 아니면 가이드.
        back_quad, back_method = _pick_quad(payload.get("backQuad"), guide_back, size)
        front_quad, front_method = _pick_quad(payload.get("frontQuad"), guide_front, size)
        method = "claude" if (back_method == "claude" or front_method == "claude") else "guide"

        fixed_image = None
        mismatch = (not matches_place or not matches_sides) and redraw  # 작업 227: redraw=False(교체한 실사)는 검사만
        if mismatch and outdoor and not fixes:
            # 작업 227: 실외 수정 문장이 하나도 남지 않으면 다시 그리지 않는다(첫 그림 유지 · 원장 0).
            log.info("vnew.location.redraw.skipped pid=%s lid=%s variant=%s view=%s reason=no_outdoor_fixes",
                     pid, lid, variant, view)
        elif mismatch:
            # 작업 143: 켬이면 실사 다시 그리기도 같은 쪽 웹툰 뷰 편집(웹툰 뷰가 없으면 text-to-image).
            base = None
            if variant == "real" and _real_from_webtoon() and ("webtoon", view) in views:
                base = views[("webtoon", view)][0]
            fixed_image = _redraw_view(owner, pid, lid, version, loc, variant, view, key, guides[view], refs, fixes, task_id,
                                       base=base)
        return {
            "matchesPlace": matches_place,
            "matchesSides": matches_sides,
            "fixedEn": fixed_image is not None,
            "fixesEn": fixes,
            "backQuad": back_quad,
            "frontQuad": front_quad,
            "method": method,
        }, fixed_image

    # 작업 125: 뷰끼리 독립(다시 그리기도 참고 이미지 없이 그 뷰만)이라 동시에 한다.
    jobs = [(variant, view) for variant in VARIANTS for view in VIEWS]

    def _apply(outcomes: dict) -> None:
        for (variant, view), (entry, fixed_image) in outcomes.items():
            if fixed_image is not None:
                views[(variant, view)] = (fixed_image, views[(variant, view)][1])
            checks[variant][view] = entry

    if outdoor and _real_from_webtoon():
        # 작업 227: 실외 + 실사=웹툰 편집이면 웹툰을 먼저 검사(+다시 그리기)하고, 실사는 그 '최종' 웹툰으로 맞춘다.
        # 다시 그린 쪽 실사는 새 웹툰 바이트로 edit 1회(원장 1건) 교체 후 1회 검사만. 나머지 실사는 지금처럼 검사(+다시 그리기,
        # 바탕 = 최종 웹툰 = 첫 웹툰). 교체가 실패하면 그 쪽도 지금처럼 검사(+다시 그리기, 바탕 = 새 웹툰).
        webtoon_jobs = [job for job in jobs if job[0] == "webtoon"]
        with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix=f"vnew-loc-verify-{lid}") as pool:
            futures = {job: pool.submit(_check_one, job[0], job[1]) for job in webtoon_jobs}
            webtoon_outcomes = {job: futures[job].result() for job in webtoon_jobs}
        _apply(webtoon_outcomes)
        redrawn = {view for (_variant, view), (_entry, fixed) in webtoon_outcomes.items() if fixed is not None}

        def _real_one(view: str) -> tuple[dict, bytes | None]:
            if view in redrawn:
                base = views[("webtoon", view)][0]
                new = _redraw_view(owner, pid, lid, version, loc, "real", view, views[("real", view)][1], guides[view],
                                   refs, [], task_id, base=base)
                if new is not None:
                    log.info("vnew.location.real.rebased pid=%s lid=%s view=%s", pid, lid, view)
                    entry, _ = _check_one("real", view, image=new, redraw=False)
                    entry["fixedEn"] = True
                    return entry, new
            return _check_one("real", view)

        real_jobs = [job for job in jobs if job[0] == "real"]
        with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix=f"vnew-loc-verify-{lid}") as pool:
            futures = {job: pool.submit(_real_one, job[1]) for job in real_jobs}
            _apply({job: futures[job].result() for job in real_jobs})
        return checks

    with ThreadPoolExecutor(max_workers=LOCATION_WORKERS, thread_name_prefix=f"vnew-loc-verify-{lid}") as pool:
        futures = {job: pool.submit(_check_one, job[0], job[1]) for job in jobs}
        outcomes = {job: futures[job].result() for job in jobs}
    _apply(outcomes)
    return checks


def _verify_view(
    owner: str, pid: str, lid: str, loc: dict, variant: str, view: str, image: bytes, guide: bytes, refs: list[bytes]
) -> tuple[dict, dict]:
    """뷰 한 장을 가이드·패널과 비교한다. (payload, usage). payload 는 _VERIFY_TOOL 모양."""
    if _is_outdoor(loc):
        return _verify_view_outdoor(owner, pid, lid, loc, variant, view, image, guide, refs)
    side_text = (
        "This is the back view (its side walls are left=west, right=east)."
        if view == "back"
        else "This is the front view (same room from the opposite side; left=east, right=west)."
    )
    # 이 뷰에 넣은 사진(면 지정·'any')을 함께 보여 주고, 사진과 맞는지 묻는다(설계자 2026-10-22).
    photo_plan = _view_photo_plan(loc, view, lead=1)  # 가이드 1장 뒤에 사진.
    photo_items: list[dict] = []
    for item in photo_plan:
        jpeg = _photo_jpeg(owner, pid, lid, item["photo"])
        if jpeg is not None:
            photo_items.append({"side": item["side"], "jpeg": jpeg})
    photo_question = ""
    if photo_items:
        sides_ko = ", ".join(
            PHOTO_SIDE_LABELS_KO.get(p["side"], p["side"]) for p in photo_items if p["side"] != "any"
        )
        photo_question = (
            " Real reference photos of this place follow the guide. For any wall that has a reference "
            f"photo ({sides_ko or 'overall look'}), matchesPlace is true only if the generated wall "
            "matches that photo's objects, materials, colors and openings."
        )
    blocks: list[dict] = [
        {
            "text": (
                f"Image 1 is the layout guide for the {view} cutaway view. {side_text}"
                + photo_question
                + " Then the reference webtoon panels, then the generated view. Does the generated view show "
                "the same place, and where are the back-wall and front-face corners? Call emit_verify."
            )
        },
        {"image": {"format": "jpeg", "source": {"bytes": guide}}},
    ]
    for item in photo_items:  # 사진을 패널보다 먼저(가이드 다음).
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": item["jpeg"]}}})
    for jpeg in refs:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})
    blocks.append({"image": {"format": "jpeg", "source": {"bytes": image}}})
    payload, usage = _verify(blocks, model=VERIFY_MODEL)
    _charge_text(owner, pid, lid, usage, str(usage.get("model") or VERIFY_MODEL))
    return payload or {}, usage


def _verify_view_outdoor(
    owner: str, pid: str, lid: str, loc: dict, variant: str, view: str, image: bytes, guide: bytes, refs: list[bytes]
) -> tuple[dict, dict]:
    """작업 227: _verify_view 의 실외판. 같은 이미지 순서(가이드 → 사진 → 패널 → 생성 뷰), 열린 바깥 글 ·
    물건 목록 · 빛. system · tool 은 _VERIFY_SYSTEM_OUTDOOR · _VERIFY_TOOL_OUTDOOR. 낮이면 밤 낱말을 지운다."""
    side_text = (
        "This is the back view (its left side looks west, its right side looks east)."
        if view == "back"
        else "This is the front view (the same open-air place seen from the opposite side; its left side looks east, "
        "its right side looks west)."
    )
    photo_items: list[dict] = []
    for item in _view_photo_plan(loc, view, lead=1):
        jpeg = _photo_jpeg(owner, pid, lid, item["photo"])
        if jpeg is not None:
            photo_items.append({"side": item["side"], "jpeg": jpeg})
    photo_question = ""
    if photo_items:
        sides_ko = ", ".join(
            PHOTO_SIDE_LABELS_KO.get(p["side"], p["side"]) for p in photo_items if p["side"] != "any"
        )
        photo_question = (
            " Real reference photos of this place follow the guide. For any side that has a reference "
            f"photo ({sides_ko or 'overall look'}), matchesPlace is true only if the generated side "
            "matches that photo's objects, materials and colors."
        )
    objects = vpoc_compose._fit_text("; ".join(_outdoor_objects(loc)), VERIFY_OBJECTS_MAX_CHARS)  # noqa: SLF001
    light = _light_text(loc).rstrip(" .")
    text = (
        f"Image 1 is the layout guide for the {view} fixed-camera view of this open-air place. {side_text}"
        + photo_question
        + (f" Main objects of this place: {objects}." if objects else "")
        + (f" Time of day and light: {light}." if light else "")
        + " Then the reference webtoon panels, then the generated view. Does the generated view show the same "
        "open-air place under open sky, and where are the MAIN BACKGROUND and full-frame corners? Call emit_verify."
    )
    blocks: list[dict] = [
        {"text": _day_guard(text, loc)},
        {"image": {"format": "jpeg", "source": {"bytes": guide}}},
    ]
    for item in photo_items:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": item["jpeg"]}}})
    for jpeg in refs:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})
    blocks.append({"image": {"format": "jpeg", "source": {"bytes": image}}})
    payload, usage = _verify(blocks, model=VERIFY_MODEL, system_text=_VERIFY_SYSTEM_OUTDOOR, tool=_VERIFY_TOOL_OUTDOOR)
    _charge_text(owner, pid, lid, usage, str(usage.get("model") or VERIFY_MODEL))
    return payload or {}, usage


def _normalize_quad(quad, size) -> list[list[float]]:
    """픽셀 꼭짓점 [(x,y)×4] 를 정규화([x,y] 0..1)로. 없으면 빈 리스트."""
    if not quad or len(quad) != 4:
        return []
    w, h = float(size[0]), float(size[1])
    return [[float(x) / w, float(y) / h] for (x, y) in quad]


def _quad_delta(a, b) -> float:
    """두 정규화 사각형의 꼭짓점 최대 차이(가로·세로 중 큰 쪽). 비교 불가면 큰 값."""
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 1.0
    worst = 0.0
    for (ax, ay), (bx, by) in zip(a, b):
        worst = max(worst, abs(ax - bx), abs(ay - by))
    return worst


def _pick_quad(claude_quad, guide_quad, size) -> tuple[list[list[float]], str]:
    """Claude 꼭짓점이 가이드와 4% 넘게 다르고 볼록·화면 안이면 그걸(정규화), 아니면 가이드.

    반환: (정규화 quad, method). method 는 'claude' 또는 'guide'.
    """
    if not isinstance(claude_quad, list) or len(claude_quad) != 4:
        return guide_quad, "guide"
    norm = []
    for p in claude_quad:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return guide_quad, "guide"
        norm.append([float(p[0]), float(p[1])])
    # 4% 이내면 가이드 유지(굳이 바꾸지 않는다).
    if _quad_delta(norm, guide_quad) <= VERTEX_TOLERANCE:
        return guide_quad, "guide"
    # 값이 이상하면(볼록 아님·화면 밖) 가이드.
    if not location_views.is_sane_quad(norm, size):
        return guide_quad, "guide"
    return norm, "claude"


def _redraw_view(
    owner: str,
    pid: str,
    lid: str,
    version: int,
    loc: dict,
    variant: str,
    view: str,
    key: str,
    guide: bytes,
    refs: list[bytes],
    fixes: list[str],
    task_id: str,
    *,
    base: bytes | None = None,
) -> bytes | None:
    """불일치 뷰를 fixesEn 을 붙여 1회 다시 그린다. 같은 키에 덮어쓰고 원장·진행을 +1 한다.

    작업 125: 참고 이미지 없이(fal text-to-image) 그린다. guide·refs 는 받기만 한다(호출부 호환).
    작업 143: base(같은 쪽 웹툰 뷰)가 있으면 편집 문장(_place_edit_prompt) + 수정 지시로 Seedream edit.
    실패하면(None) 원래 그림을 그대로 둔다.
    """
    base_prompt = _edit_prompt_for(loc) if base is not None else _prompt_for(loc, variant, view)
    fix_text = " Corrections: " + " ".join(fixes) if fixes else ""
    prompt = (base_prompt + fix_text)[:PROMPT_HARD_CAP]
    last_error: Exception | None = None
    for _ in range(FIX_RETRY):
        try:
            if base is not None:
                image = _seedream_edit(prompt, base, size=VIEW_SIZE)
            else:
                image = _seedream(prompt, [], size=VIEW_SIZE)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        STORE.put_bytes(key, image, "image/jpeg")
        _charge_seedream(owner, pid, lid, f"{variant}-view-{view}-fix",
                         model=LOCATION_EDIT_MODEL if base is not None else None)
        with _PROGRESS_LOCK:
            _bump_total(owner, pid, lid, task_id)  # 보정 장수만큼 total 을 늘리고 done 도 +1.
        return image
    if last_error is not None:
        log.warning("vnew.location.redraw.failed pid=%s lid=%s view=%s err=%s", pid, lid, view, str(last_error)[:200])
    return None


def _bump_total(owner: str, pid: str, lid: str, task_id: str) -> None:
    """보정 그림 한 장 — total 과 done 을 함께 +1 한다(진행 막대가 100%를 넘지 않게)."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            return
        if (loc.get("task") or {}).get("id") != task_id:
            return
        prior = loc.get("progress") or _empty_progress()
        started = prior.get("startedAt") or now_iso()
        total = int(prior.get("total") or TOTAL_VIEWS) + 1
        done = min(total, int(prior.get("done") or 0) + 1)
        loc["progress"] = {
            "step": prior.get("step") or "verify",
            "done": done,
            "total": total,
            "startedAt": started,
            "updatedAt": now_iso(),
        }

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.progress.bump_total_failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:150])


def _faces_from_corners(back_quad_px, front_quad_px, view: str) -> dict[str, list]:
    """뒤 벽 4꼭짓점(TL,TR,BR,BL)과 앞 열린 면 4꼭짓점으로 면별 사다리꼴(픽셀)을 다시 만든다.

    box_view_geometry 와 같은 규칙으로 left/right/floor/back 사다리꼴을 조립한다(모서리 공유). cut 이
    가이드 좌표 또는 Claude 가 교정한 좌표로 각 면을 펼 때 쓴다. 각 면은 (좌상·우상·우하·좌하).
    """
    btl, btr, bbr, bbl = back_quad_px
    ftl, ftr, fbr, fbl = front_quad_px
    return {
        "back": [btl, btr, bbr, bbl],
        # west(왼쪽): 바깥(앞)쪽 가장자리가 왼쪽, 뒤 벽 모서리가 오른쪽.
        "left": [ftl, btl, bbl, fbl],
        # east(오른쪽): 뒤 벽 모서리가 왼쪽, 바깥쪽 가장자리가 오른쪽.
        "right": [btr, ftr, fbr, bbr],
        # floor(바닥): 좌상=뒤벽 왼쪽 아래, 우상=뒤벽 오른쪽 아래, 우하=앞 오른쪽 아래, 좌하=앞 왼쪽 아래.
        "floor": [bbl, bbr, fbr, fbl],
    }


def _denorm_quad(norm_quad, size) -> list:
    """정규화 꼭짓점([x,y] 0..1)을 픽셀 (x,y) 로. 비면 None."""
    if not norm_quad or len(norm_quad) != 4:
        return None
    w, h = float(size[0]), float(size[1])
    return [(float(x) * w, float(y) * h) for (x, y) in norm_quad]


def _cut_textures(
    owner: str,
    pid: str,
    lid: str,
    version: int,
    views: dict[tuple[str, str], tuple[bytes, str]],
    geometries: dict[str, dict],
    checks: dict[str, dict[str, dict]],
) -> dict[str, dict]:
    """뷰에서 각 면을 원근 보정(location_views.rectify)해 벽·바닥 텍스처(기존 키)로 저장한다(048).

    뷰 A(back) → north(back)·west(left)·east(right)·floor. 뷰 B(front) → south(back).
    꼭짓점은 verify 가 고른 최종 좌표(가이드 또는 Claude). 반환: {variant: {wallKeys, floorKey, wallsBytes, floorBytes}}.
    저장 키는 기존 wall_key/floor_key → previz 가 수정 없이 읽는다.
    """
    out: dict[str, dict] = {}
    wall_w, wall_h = _size_wh(WALL_SIZE)
    floor_w, floor_h = _size_wh(FLOOR_SIZE)
    for variant in VARIANTS:
        wall_keys: dict[str, str] = {}
        walls_bytes: dict[str, bytes] = {}
        floor_bytes = b""
        floor_k = ""
        # 뷰 A(back): north·west·east·floor.
        back_img, _ = views[(variant, "back")]
        geom_a = geometries["back"]
        size_a = geom_a.get("_size") or (wall_w, wall_h)
        chk_a = (checks.get(variant) or {}).get("back") or {}
        back_px = _denorm_quad(chk_a.get("backQuad"), size_a) or geom_a.get("back")
        front_px = _denorm_quad(chk_a.get("frontQuad"), size_a) or geom_a.get("_front")
        faces_a = _faces_from_corners(back_px, front_px, "back")
        for face, wall in (("back", "north"), ("left", "west"), ("right", "east")):
            key = wall_key(owner, pid, lid, variant, wall, version)
            texture = location_views.rectify(back_img, faces_a[face], (wall_w, wall_h))
            STORE.put_bytes(key, texture, "image/jpeg")
            wall_keys[wall] = key
            walls_bytes[wall] = texture
        # 바닥(뷰 A 의 floor 사다리꼴을 편다 — 따로 그리지 않는다).
        floor_k = floor_key(owner, pid, lid, variant, version)
        floor_bytes = location_views.rectify(back_img, faces_a["floor"], (floor_w, floor_h))
        STORE.put_bytes(floor_k, floor_bytes, "image/jpeg")

        # 뷰 B(front): south(back 면).
        front_view_img, _ = views[(variant, "front")]
        geom_b = geometries["front"]
        size_b = geom_b.get("_size") or (wall_w, wall_h)
        chk_b = (checks.get(variant) or {}).get("front") or {}
        back_px_b = _denorm_quad(chk_b.get("backQuad"), size_b) or geom_b.get("back")
        south_key = wall_key(owner, pid, lid, variant, "south", version)
        south_texture = location_views.rectify(front_view_img, back_px_b, (wall_w, wall_h))
        STORE.put_bytes(south_key, south_texture, "image/jpeg")
        wall_keys["south"] = south_key
        walls_bytes["south"] = south_texture

        out[variant] = {
            "wallKeys": wall_keys,
            "floorKey": floor_k,
            "wallsBytes": walls_bytes,
            "floorBytes": floor_bytes,
        }
    return out


def _assemble_variant(
    owner: str,
    pid: str,
    lid: str,
    variant: str,
    version: int,
    views: dict[tuple[str, str], tuple[bytes, str]],
    cut: dict[str, dict],
    checks: dict[str, dict[str, dict]],
    task_id: str,
) -> None:
    """뷰·펴진 텍스처로 시트를 붙이고(montage_views), 그 variant 의 viewKeys·wallKeys·floorKey·sheetKey 를 저장한다."""
    back_img, back_key = views[(variant, "back")]
    front_img, front_key = views[(variant, "front")]
    cut_v = cut.get(variant) or {}
    wall_keys = cut_v.get("wallKeys") or {}
    floor_k = cut_v.get("floorKey") or ""
    walls_bytes = cut_v.get("wallsBytes") or {}
    floor_bytes = cut_v.get("floorBytes") or b""

    sheet = montage_views(back_img, walls_bytes, floor_bytes)
    skey = sheet_key(owner, pid, lid, variant, version)
    STORE.put_bytes(skey, sheet, "image/jpeg")

    _store_variant(
        owner,
        pid,
        lid,
        variant,
        {
            "status": "ready",
            "viewKeys": {"back": back_key, "front": front_key},
            "wallKeys": wall_keys,
            "floorKey": floor_k,
            "sheetKey": skey,
            "checks": (checks or {}).get(variant) or {},
            "error": "",
        },
        task_id,
    )


# ================================================================ 저장(task.id 확인)


def _store_design(owner: str, pid: str, lid: str, design: dict) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is not None:
            loc["design"] = design

    plan_mod.mutate_plan(owner, pid, mutator)


def _store_facts(owner: str, pid: str, lid: str, facts: dict) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is not None:
            loc["facts"] = facts

    plan_mod.mutate_plan(owner, pid, mutator)


def _store_variant(owner: str, pid: str, lid: str, variant: str, result: dict, task_id: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        if (data.get("task") or {}) and _task_mismatch(data, lid, task_id):
            raise _Discard()
        loc = _find_location(data, lid)
        if loc is None:
            raise _Discard()
        loc.setdefault("variants", {})[variant] = result

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.location.variant.discarded pid=%s lid=%s variant=%s", pid, lid, variant)


def _commit(owner: str, pid: str, lid: str, task_id: str, *, status: str, error: str = "") -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            raise _Discard()
        if (loc.get("task") or {}).get("id") != task_id:
            raise _Discard()
        loc["status"] = status
        loc["error"] = error
        loc["task"] = {"status": "done" if status == "ready" else "error", "startedAt": (loc.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.location.commit.discarded pid=%s lid=%s", pid, lid)


def _task_mismatch(data: dict, lid: str, task_id: str) -> bool:
    loc = _find_location(data, lid)
    if loc is None:
        return True
    return (loc.get("task") or {}).get("id") != task_id


class _Discard(Exception):
    """늦게 온 결과를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


# ================================================================ 설계 수정(PUT)


def update_location(owner: str, pid: str, lid: str, patch: dict) -> dict:
    """설계 JSON 수정 — 이름·벽 설명·빛. 이미지는 그대로 두고 다음 '다시 만들기' 때 반영된다."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.update", "NotFound", "장소를 찾을 수 없습니다.", 404)
        if "nameKo" in patch:
            loc["nameKo"] = str(patch.get("nameKo") or "").strip()
        if "nameEn" in patch:
            name_en = str(patch.get("nameEn") or "").strip()
            loc["nameEn"] = name_en
            loc["key"] = normalize_name(name_en)
        design = loc.setdefault("design", {})
        if not design.get("walls"):
            design.update(_normalize_design(design))
        if "lightEn" in patch:
            design["lightEn"] = str(patch.get("lightEn") or "").strip()
        if isinstance(patch.get("walls"), dict):
            walls = design.setdefault("walls", {})
            for side in SIDES:
                wall_patch = patch["walls"].get(side)
                if not isinstance(wall_patch, dict):
                    continue
                wall = walls.setdefault(side, {})
                if "descEn" in wall_patch:
                    wall["descEn"] = str(wall_patch.get("descEn") or "").strip()
                if "featuresEn" in wall_patch:
                    wall["featuresEn"] = str(wall_patch.get("featuresEn") or "").strip()
                if "hasWindow" in wall_patch:
                    wall["hasWindow"] = _as_bool(wall_patch.get("hasWindow"))
                if "hasDoor" in wall_patch:
                    wall["hasDoor"] = _as_bool(wall_patch.get("hasDoor"))
        if "floorEn" in patch:
            design["floorEn"] = str(patch.get("floorEn") or "").strip()
        if "ceilingEn" in patch:
            design["ceilingEn"] = str(patch.get("ceilingEn") or "").strip()
        # 사용자가 설계를 직접 고쳤음을 표시한다. 이후 [다시 만들기] 는 설계를 새로 받지 않고 이 수정본을
        # 유지한다(run_build 가 designEditedAt 을 본다). 설계 관련 필드를 고쳤을 때만 찍는다.
        if any(k in patch for k in ("lightEn", "walls", "floorEn", "ceilingEn")):
            design["designEditedAt"] = now_iso()
        # 사실(facts) 수정: 벽별 재질·물건, 바닥·천장·빛·시대·나라. 고치면 그 사실로 프롬프트를 다시 쓴다
        # (factsEditedAt 이 있으면 run_build 가 사실 추출을 건너뛰고 수정본을 쓴다).
        if isinstance(patch.get("facts"), dict):
            facts = loc.setdefault("facts", {})
            if not facts.get("walls"):
                facts.update(_normalize_facts(facts))
            _apply_facts_patch(facts, patch["facts"])
            facts["factsEditedAt"] = now_iso()
        data["revision"] = int(data.get("revision") or 0) + 1
        return loc

    _, loc = plan_mod.mutate_plan(owner, pid, mutator)
    return loc


def _apply_facts_patch(facts: dict, patch: dict) -> None:
    """사실 수정 패치를 제자리에서 적용한다(벽별 재질·물건 목록·바닥·천장·빛·시대·나라)."""
    for scalar in ("floorEn", "ceilingEn", "lightEn", "eraEn", "countryEn"):
        if scalar in patch:
            facts[scalar] = str(patch.get(scalar) or "").strip()
    walls_patch = patch.get("walls")
    if isinstance(walls_patch, dict):
        walls = facts.setdefault("walls", {})
        for side in SIDES:
            wall_patch = walls_patch.get(side)
            if not isinstance(wall_patch, dict):
                continue
            wall = walls.setdefault(side, {"wallColorEn": "", "wallMaterialEn": "", "estimated": False, "objects": []})
            if "wallColorEn" in wall_patch:
                wall["wallColorEn"] = str(wall_patch.get("wallColorEn") or "").strip()
            if "wallMaterialEn" in wall_patch:
                wall["wallMaterialEn"] = str(wall_patch.get("wallMaterialEn") or "").strip()
            if isinstance(wall_patch.get("objects"), list):
                objects: list[dict] = []
                for item in wall_patch["objects"]:
                    if not isinstance(item, dict):
                        continue
                    kind = str(item.get("kind") or "").strip()
                    if not kind:
                        continue
                    objects.append(
                        {
                            "kind": kind,
                            "count": _as_int(item.get("count"), 1),
                            "colorEn": str(item.get("colorEn") or "").strip(),
                            "materialEn": str(item.get("materialEn") or "").strip(),
                            "position": _norm_position(item.get("position")),
                            "sizeEn": str(item.get("sizeEn") or "").strip(),
                            "attachedTo": "floor" if str(item.get("attachedTo") or "").strip().lower() == "floor" else "wall",
                        }
                    )
                wall["objects"] = objects


# ================================================================ 참고 사진(CRUD)
#
# 설계자 2026-10-22: 장소마다 사용자 참고 사진을 붙이고 면(side)·메모를 지정한다. 이미지는
# vpoc.images.normalize_upload 로 JPEG 로 정규화해 photos/{photoId}.jpg 로만 저장한다(외부 금지).
# 빌드 중(running)에는 변경을 409 로 막고, 사진을 바꿔도 이미 만든 시트는 그대로 둔다.


def _photo_id() -> str:
    return f"ph_{secrets.token_hex(4)}"


def _norm_photo_side(value) -> str:
    """사진 면 정규화. 모르는 값이면 'any'(전체 분위기)."""
    side = str(value or "").strip().lower()
    return side if side in PHOTO_SIDES else "any"


def _normalize_photo(raw: dict) -> dict:
    """저장된 사진 메타를 화면·빌드용 모양으로 정리한다(없는 필드 기본값)."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "photoId": str(raw.get("photoId") or "").strip(),
        "key": str(raw.get("key") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "width": _as_int(raw.get("width"), 0),
        "height": _as_int(raw.get("height"), 0),
        "side": _norm_photo_side(raw.get("side")),
        "noteKo": str(raw.get("noteKo") or "").strip()[:MAX_PHOTO_NOTE_LEN],
        "addedAt": str(raw.get("addedAt") or "").strip(),
    }


def _loc_photos(loc: dict) -> list[dict]:
    """장소의 참고 사진 목록(정리된). 없으면 빈 리스트."""
    photos = (loc or {}).get("photos")
    if not isinstance(photos, list):
        return []
    return [_normalize_photo(p) for p in photos if isinstance(p, dict) and p.get("photoId")]


def _photos_by_side(photos: list[dict], side: str) -> list[dict]:
    """그 면(side)으로 지정된 사진만(추가 순서 유지)."""
    return [p for p in photos if p.get("side") == side]


def _guard_photos_not_running(loc: dict, owner: str | None = None, pid: str | None = None) -> None:
    """빌드 중(running, stale·끊김 아님)이면 사진 변경을 409 로 막는다(작업 128: 끊긴 작업이면 막지 않는다)."""
    task = loc.get("task") or {}
    if task.get("status") == "running" and not _task_stale(task):
        if owner is not None and pid is not None and _interrupted(owner, pid, loc):
            return
        raise VpocError(
            "vnew.location.photos",
            "Building",
            "공간 시트를 만드는 중에는 사진을 바꿀 수 없습니다.",
            409,
        )


def add_photos(owner: str, pid: str, lid: str, files: list[tuple[bytes, str]], side: str | None = None) -> dict:
    """참고 사진 여러 장을 올린다. 각 장을 JPEG 로 정규화(크기 제한 동일)해 photos/ 아래 저장한다.

    - 빌드 중(running)이면 409. 이미 6장이거나 이번에 6장을 넘기면 400.
    - side 가 주어지면 올린 사진 모두 그 면으로(없으면 'any').
    반환: 정리된 location_view(한 장소).
    """
    from . import plan as plan_mod  # noqa: PLC0415

    cleaned_side = _norm_photo_side(side) if side is not None else "any"
    pending = [(raw, name) for (raw, name) in files if raw]
    if not pending:
        raise VpocError("vnew.location.photos", "NoFiles", "올릴 사진이 없습니다.", 400)

    # 먼저 6장 상한을 본다(정규화·저장 전에). 초과면 400.
    data0, _ = plan_mod.load_plan(owner, pid)
    loc0 = _find_location(data0, lid)
    if loc0 is None:
        raise VpocError("vnew.location.photos", "NotFound", "장소를 찾을 수 없습니다.", 404)
    _guard_photos_not_running(loc0, owner, pid)
    existing = len(_loc_photos(loc0))
    if existing + len(pending) > MAX_LOCATION_PHOTOS:
        raise VpocError(
            "vnew.location.photos",
            "TooManyPhotos",
            f"참고 사진은 최대 {MAX_LOCATION_PHOTOS}장까지 넣을 수 있습니다.",
            400,
        )

    # 정규화·저장(외부 금지 — vpoc.images.normalize_upload 재사용, S3 는 STORE).
    new_metas: list[dict] = []
    for raw, name in pending:
        jpeg, width, height = vpoc_images.normalize_upload(raw, name)
        photo_id = _photo_id()
        key = photo_key(owner, pid, lid, photo_id)
        STORE.put_bytes(key, jpeg, "image/jpeg")
        new_metas.append(
            {
                "photoId": photo_id,
                "key": key,
                "name": str(name or "").strip(),
                "width": width,
                "height": height,
                "side": cleaned_side,
                "noteKo": "",
                "addedAt": now_iso(),
            }
        )

    def mutator(data: dict) -> dict:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.photos", "NotFound", "장소를 찾을 수 없습니다.", 404)
        _guard_photos_not_running(loc, owner, pid)
        photos = _loc_photos(loc)
        if len(photos) + len(new_metas) > MAX_LOCATION_PHOTOS:
            raise VpocError(
                "vnew.location.photos",
                "TooManyPhotos",
                f"참고 사진은 최대 {MAX_LOCATION_PHOTOS}장까지 넣을 수 있습니다.",
                400,
            )
        loc["photos"] = photos + new_metas
        return loc

    _, loc = plan_mod.mutate_plan(owner, pid, mutator)
    return _location_view_one(owner, pid, loc)


def update_photo(owner: str, pid: str, lid: str, photo_id: str, patch: dict) -> dict:
    """사진의 면(side)·메모(noteKo)를 바꾼다. 빌드 중이면 409. 반환: 그 장소 location_view."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.photos", "NotFound", "장소를 찾을 수 없습니다.", 404)
        _guard_photos_not_running(loc, owner, pid)
        photos = _loc_photos(loc)
        target = next((p for p in photos if p.get("photoId") == photo_id), None)
        if target is None:
            raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
        if "side" in patch:
            raw_side = str(patch.get("side") or "").strip().lower()
            if raw_side and raw_side not in PHOTO_SIDES:
                raise VpocError("vnew.location.photos", "BadSide", "알 수 없는 면입니다.", 400)
            target["side"] = _norm_photo_side(raw_side)
        if "noteKo" in patch:
            target["noteKo"] = str(patch.get("noteKo") or "").strip()[:MAX_PHOTO_NOTE_LEN]
        loc["photos"] = photos
        return loc

    _, loc = plan_mod.mutate_plan(owner, pid, mutator)
    return _location_view_one(owner, pid, loc)


def delete_photo(owner: str, pid: str, lid: str, photo_id: str) -> dict:
    """사진을 지운다(저장된 파일도 지운다). 빌드 중이면 409. 반환: 그 장소 location_view."""
    from . import plan as plan_mod  # noqa: PLC0415

    removed_key = {"value": ""}

    def mutator(data: dict) -> dict:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.photos", "NotFound", "장소를 찾을 수 없습니다.", 404)
        _guard_photos_not_running(loc, owner, pid)
        photos = _loc_photos(loc)
        target = next((p for p in photos if p.get("photoId") == photo_id), None)
        if target is None:
            raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
        removed_key["value"] = target.get("key") or photo_key(owner, pid, lid, photo_id)
        loc["photos"] = [p for p in photos if p.get("photoId") != photo_id]
        return loc

    _, loc = plan_mod.mutate_plan(owner, pid, mutator)
    # 상태를 먼저 고친 뒤 파일을 지운다(지우기가 실패해도 목록에서는 사라진다).
    key = removed_key["value"]
    if key:
        try:
            _delete_stored(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.photos.delete_file_failed key=%s err=%s", key, str(exc)[:150])
    return _location_view_one(owner, pid, loc)


def _delete_stored(key: str) -> None:
    """저장된 객체 1개를 지운다. STORE 에 delete_bytes 가 있으면 그것(검사 가짜), 없으면 vpoc store
    의 S3 클라이언트로 직접 지운다(vpoc 재사용 — 수정 아님)."""
    deleter = getattr(STORE, "delete_bytes", None)
    if callable(deleter):
        deleter(key)
        return
    from ..vpoc import store as vpoc_store  # noqa: PLC0415

    vpoc_store.s3_client(STORE.main_region).delete_object(Bucket=STORE.main_bucket, Key=key)


def photo_bytes(owner: str, pid: str, lid: str, photo_id: str) -> tuple[bytes, str]:
    """참고 사진 미리보기 바이트(같은 출처 API). 그 프로젝트 사진이 아니면 404.

    키가 이 (owner,pid,lid) 의 photos 접두사 안인지 확인한다 — 다른 사용자·다른 프로젝트 데이터에
    닿지 못하게. 못 읽어도 404. 화면은 이 경로로 받는다(버킷 CORS 없음).
    """
    from . import plan as plan_mod  # noqa: PLC0415

    photo_id = str(photo_id or "").strip()
    if not photo_id:
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
    try:
        data, _ = plan_mod.load_plan(owner, pid)
    except Exception as exc:  # noqa: BLE001
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404) from exc
    loc = _find_location(data, lid)
    if loc is None:
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
    meta = next((p for p in _loc_photos(loc) if p.get("photoId") == photo_id), None)
    if meta is None:
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
    key = meta.get("key") or photo_key(owner, pid, lid, photo_id)
    prefix = photos_prefix(owner, pid, lid)
    if not key.startswith(prefix):
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
    try:
        body = STORE.get_bytes(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.photos.missing key=%s err=%s", key, str(exc)[:150])
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404) from exc
    if not body:
        raise VpocError("vnew.location.photos", "NotFound", "사진을 찾을 수 없습니다.", 404)
    return body, "image/jpeg"


# ================================================================ 보기(presigned)


def _location_view_one(owner: str, pid: str, loc: dict) -> dict:
    """장소 하나의 location_view(사진 CRUD 응답용). locations_view 와 같은 모양 한 개."""
    return locations_view(owner, pid, {"locations": [loc]})[0]


def _photos_view(photos: list[dict]) -> list[dict]:
    """참고 사진 목록을 화면용으로. presigned URL 을 담지 않는다 — 화면이 같은 출처 API 로 받는다
    (버킷 CORS 없음). 키는 내보내지 않는다(photoId 로만 받는다)."""
    out: list[dict] = []
    for p in photos:
        out.append(
            {
                "photoId": p.get("photoId") or "",
                "name": p.get("name") or "",
                "side": p.get("side") or "any",
                "noteKo": p.get("noteKo") or "",
                "width": p.get("width") or 0,
                "height": p.get("height") or 0,
                "addedAt": p.get("addedAt") or "",
            }
        )
    return out


def locations_view(owner: str, pid: str, plan: dict) -> list[dict]:
    """plan_view 의 locations[] — 각 장소의 설계·상태·그림체별 이미지 presigned URL."""
    out: list[dict] = []
    for loc in plan.get("locations") or []:
        loc = _reconcile_stale(loc, owner=owner, pid=pid)
        variants_out = {}
        has_views = False
        for variant in VARIANTS:
            v = (loc.get("variants") or {}).get(variant) or {}
            wall_urls = {side: _presign(v.get("wallKeys", {}).get(side) or "") for side in SIDES}
            view_keys = v.get("viewKeys") or {}
            view_urls = {view: _presign(view_keys.get(view) or "") for view in VIEWS}
            if any(view_urls.values()):
                has_views = True
            variants_out[variant] = {
                "status": v.get("status") or "none",
                "error": v.get("error") or "",
                "wallUrls": wall_urls,
                "floorUrl": _presign(v.get("floorKey") or ""),
                "sheetUrl": _presign(v.get("sheetKey") or ""),
                "viewUrls": view_urls,  # {back, front} 컷어웨이 뷰 원본(048)
                "checks": v.get("checks") or {},
            }
        # 화면이 쓸 뷰 URL 묶음(048). 옛 데이터(viewKeys 없음)면 빈 값만 담긴다.
        views_out = {variant: variants_out[variant]["viewUrls"] for variant in VARIANTS}
        out.append(
            {
                "id": loc.get("id"),
                "nameKo": loc.get("nameKo") or "",
                "nameEn": loc.get("nameEn") or "",
                "panels": loc.get("panels") or [],
                "status": loc.get("status") or "none",
                "error": loc.get("error") or "",
                "design": loc.get("design") or {},
                "facts": loc.get("facts") or {},
                "photos": _photos_view(_loc_photos(loc)),  # 참고 사진(같은 출처 API 로 미리보기)
                "photoSideLabels": PHOTO_SIDE_LABELS_KO,  # 면 한국어 라벨(화면 select)
                "maxPhotos": MAX_LOCATION_PHOTOS,
                "variants": variants_out,
                "views": views_out,  # {real:{back,front}, webtoon:{back,front}} (048)
                "hasViews": has_views,  # 새 방식 데이터인지(화면이 뷰 UI 를 쓸지 판단)
                "viewLabels": VIEW_FACE_LABELS,  # 면별 한국어 라벨(화면)
                "task": loc.get("task") or _empty_task(),
                "progress": loc.get("progress") or _empty_progress(),
            }
        )
    return out


def _reconcile_stale(loc: dict, *, owner: str | None = None, pid: str | None = None) -> dict:
    """조회 때: building 이 끊겼거나 오래 멈췄으면 failed 로 본다(저장하지 않고 뷰만).

    작업 128: owner·pid 가 오면 먼저 '끊긴 작업'(_interrupted — 이 파드 미실행 + heartbeat 가 LOCATION_STALL_S
    보다 오래됨)을 본다. 다른 파드에서 heartbeat 가 신선하게 돌고 있으면 그대로 둔다.
    """
    task = loc.get("task") or {}
    if owner is not None and pid is not None and _interrupted(owner, pid, loc):
        loc = dict(loc)
        loc["status"] = "failed"
        loc["error"] = INTERRUPTED_MESSAGE
        loc["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
        return loc
    if _task_stale(task):
        loc = dict(loc)
        loc["status"] = "failed"
        loc["error"] = "공간 시트 만들기가 중단되었습니다. 다시 시도해 주세요."
        loc["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
    return loc


# ================================================================ 원장


def _charge_seedream(owner: str, pid: str, lid: str, kind: str, *, model: str | None = None) -> None:
    """공간 시트 그림 1장 원장(작업 125): 모델 = LOCATION_IMAGE_MODEL, 금액 = fal Seedream 장당(LOCATION_IMAGE_USD).

    작업 143: 실사 편집(model=LOCATION_EDIT_MODEL)도 그림 1장과 같은 단가로 적는다.
    """
    key = f"vnew-loc-{pid}-{lid}-{kind}-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="image",
            model=model or LOCATION_IMAGE_MODEL,
            units=1,
            usd=LOCATION_IMAGE_USD,
            user=owner,
            project_id=pid,
            plan_id=lid,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.ledger.seedream_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_text(owner: str, pid: str, lid: str, usage: dict, model: str) -> None:
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        key = f"vnew-loc-{pid}-{lid}-design-{secrets.token_hex(3)}"
        vpoc_ledger.charge_text(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=lid,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.ledger.text_failed pid=%s err=%s", pid, str(exc)[:200])


def _presign(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, content_type="image/jpeg", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.presign.failed key=%s err=%s", key, str(exc)[:150])
        return ""


# ================================================================ 작은 도우미


def _project_style(owner: str, plan: dict) -> str:
    """webtoon 그림체용 원작 그림체. 연결된 캐릭터 중 analysis.styleEn 이 있으면 첫 번째를 쓴다."""
    for item in ((plan.get("input") or {}).get("characters")) or []:
        char_id = str((item or {}).get("charId") or "")
        if not char_id:
            continue
        try:
            char, _ = characters.load_char(owner, char_id)
            style = str((char.get("analysis") or {}).get("styleEn") or "").strip()
            if style:
                return style
        except Exception:  # noqa: BLE001
            continue
    return ""


def _extra_en(loc: dict) -> str:
    """사용자 추가 요청(한국어)을 그대로 영어 지시로 쓸 수는 없으므로, 있으면 짧게 덧붙인다.

    간단히 원문을 'Additional request:' 로 감싸 Seedream 프롬프트 끝에 붙인다(설계 LLM 이 없을 때도
    동작하도록 번역 LLM 을 따로 호출하지 않는다 — 비용·복잡도).
    """
    extra_ko = str(loc.get("extraPromptKo") or "").strip()
    if not extra_ko:
        return ""
    return "Additional request: " + extra_ko


def _spawn(func, owner: str, pid: str, lid: str, task_id: str, *, name: str) -> None:
    thread = threading.Thread(target=func, args=(owner, pid, lid, task_id), daemon=True, name=name)
    thread.start()


def _size_wh(size: str) -> tuple[int, int]:
    try:
        width, height = str(size).lower().split("x")
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 2048, 1152


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return False


def _as_float(value, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _task_stale(task: dict) -> bool:
    if (task or {}).get("status") != "running":
        return False
    started = _parse_iso((task or {}).get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > BUILD_STALL_S


def _heartbeat_age_s(loc: dict) -> float | None:
    """마지막 살아 있음 표시(progress.heartbeatAt·updatedAt 중 늦은 값, 없으면 시작 시각)부터 지난 초. 모르면 None."""
    progress = (loc or {}).get("progress") or {}
    task = (loc or {}).get("task") or {}
    stamps = [_parse_iso(progress.get(k) or "") for k in ("heartbeatAt", "updatedAt")]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        fallback = _parse_iso(progress.get("startedAt") or "") or _parse_iso(task.get("startedAt") or "")
        stamps = [fallback] if fallback is not None else []
    if not stamps:
        return None
    return (datetime.now(timezone.utc) - max(stamps)).total_seconds()


def _interrupted(owner: str, pid: str, loc: dict) -> bool:
    """끊긴 빌드인지(작업 128): running 인데 이 파드에서 돌고 있지 않고 heartbeat 가 LOCATION_STALL_S 보다 오래됐다.

    다른 파드에서 실제로 돌고 있으면 heartbeat 가 LOCATION_HEARTBEAT_S 마다 갱신되므로 False 다.
    """
    task = (loc or {}).get("task") or {}
    if task.get("status") != "running":
        return False
    lid = str((loc or {}).get("id") or "")
    if _running_here(owner, pid, lid):
        return False
    age = _heartbeat_age_s(loc)
    return age is not None and age > LOCATION_STALL_S


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
