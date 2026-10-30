"""vnew 설정값 — 캐릭터 만들기(설계자 2026-10-20). 전부 환경변수에서 읽는다.

vpoc.settings 를 재사용하되(모델·버킷·키), 캐릭터 만들기 전용 값만 여기에 둔다.
여기에만 os.getenv 를 둔다. characters.py · routes.py 는 이 상수를 읽는다.
"""

from __future__ import annotations

import logging
import math
import os
import re

from ..vpoc import settings as vpoc_settings

log = logging.getLogger("vpoc")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# --- 값을 검사하는 읽기(char-fidelity T2b, 2026-10-30). 틀린 값이면 기본값을 쓰고 경고 로그 1줄을 남긴다. ---
# 위 _int·_float·_bool 은 그대로 둔다(기존 설정은 지금처럼 조용히 기본값). 아래 함수는 3D 공급자 설정만 쓴다.


def _invalid(name: str, raw: str, default) -> None:
    """틀린 설정값 경고 1줄. 비밀값이 아닌 3D 공급자 설정만 이 함수를 쓴다(값은 40자까지만 남긴다)."""
    log.warning("vnew.settings.invalid key=%s value=%r default=%r", name, raw[:40], default)


def _checked_choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.lower() in allowed:
        return raw.lower()
    _invalid(name, raw, default)
    return default


def _checked_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        _invalid(name, raw, default)
        return default
    return value


def _checked_float(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or not low <= value <= high:
        _invalid(name, raw, default)
        return default
    return value


def _checked_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    _invalid(name, raw, default)
    return default


# Tripo endpoint_id 는 "tripo3d/<버전>/<작업>" 꼴만 받는다. 큐 조회 주소(앞 두 마디)·결과 회전·원장이 이 접두사로 Tripo 를 가린다.
_TRIPO_ENDPOINT_RE = re.compile(r"^tripo3d(/[a-z0-9][a-z0-9._-]*){2,}$")


def _checked_tripo_endpoint(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if _TRIPO_ENDPOINT_RE.match(raw):
        return raw
    _invalid(name, raw, default)
    return default


# --- 재사용(vpoc.settings 를 그대로 본다) ---
MAIN_BUCKET = vpoc_settings.MAIN_BUCKET
AWS_REGION = vpoc_settings.AWS_REGION
# 시트·정면·후면 그림은 Seedream 5.0 Pro(완성 모델)로 만든다.
SEEDREAM_MODEL = vpoc_settings.SEEDREAM_FINAL_MODEL
# 분석은 GPT-6 Astra 를 먼저 쓰고(와이랩 broker 경로), 실패하면 Claude 분석 체인으로.
ASTRA_MODEL = os.getenv("VNEW_ASTRA_MODEL", "global.openai.gpt-6-astra")
ANALYZE_FALLBACK_MODEL = vpoc_settings.ANALYZE_MODEL
# 정면·후면 사람 수 검사(빠른 모델). vpoc.settings.BRIEF_MODEL 을 기본으로 쓴다.
PEOPLE_COUNT_MODEL = os.getenv("VNEW_PEOPLE_COUNT_MODEL", vpoc_settings.BRIEF_MODEL)
# 복장 일관성 검사(시트가 baseOutfitEn 과 맞는지). 사람 수 검사와 같은 Claude 분석 체인을 쓴다.
OUTFIT_CHECK_MODEL = os.getenv("VNEW_OUTFIT_CHECK_MODEL", vpoc_settings.BRIEF_MODEL)

# --- Seedream 시트/전신 크기(설계자 실측 2026-10-20) ---
SHEET_SIZE = os.getenv("VNEW_SHEET_SIZE", "2560x1440")  # 가로 5면 턴어라운드 시트
FRONT_SIZE = os.getenv("VNEW_FRONT_SIZE", "1440x2560")  # 세로 정면 전신
BACK_SIZE = os.getenv("VNEW_BACK_SIZE", "1440x2560")  # 세로 후면 전신(따로 생성)

# 추가 요청(사용자가 '다시 만들기' 때 넣는 한국어 지시)의 최대 길이.
MAX_EXTRA_PROMPT_LEN = _int("VNEW_MAX_EXTRA_PROMPT_LEN", 500)

# --- 3D(Hunyuan3D v3, fal 큐) ---
# fal endpoint_id. ARN 접두사·큐 경로는 vpoc.fal 과 같다.
HUNYUAN_ENDPOINT = os.getenv("VNEW_3D_ENDPOINT", "fal-ai/hunyuan3d-v3/image-to-3d")
# 기본 500000 이면 GLB 40MB, 120000+PBR 이면 29MB → 실측(2026-10-20) enable_pbr false·60000 이면
# 10.6MB·85초로 웹에서 쓸 만하다. env 로 바꿀 수 있게 둔다.
HUNYUAN_FACE_COUNT = _int("VNEW_3D_FACE_COUNT", 60000)
# PBR 재질을 끄면 GLB 가 크게 작아진다(기본 false). env 로 켤 수 있다.
HUNYUAN_ENABLE_PBR = _bool("VNEW_3D_ENABLE_PBR", False)
# 3D 입력 정사각 한 변(흰 배경 패딩).
MODEL_INPUT_SQUARE = _int("VNEW_3D_INPUT_SQUARE", 1024)
# 3D 완료 폴링: 5초 간격, 최대 15분.
POLL_INTERVAL_S = _int("VNEW_3D_POLL_INTERVAL_S", 5)
POLL_MAX_S = _int("VNEW_3D_POLL_MAX_S", 900)
# 3D 조회 중 fal 일시 오류(5xx·429·연결·시간초과·"unavailable")는 실패로 보지 않고 다시 조회한다.
# 연속 일시 오류가 이 횟수를 넘으면 그때 실패. 재조회 사이 대기(초).
POLL_TRANSIENT_MAX = _int("VNEW_3D_TRANSIENT_MAX", 10)
POLL_TRANSIENT_WAIT_S = _int("VNEW_3D_TRANSIENT_WAIT_S", 15)
# 같은 요청에서 unavailable 류 일시 오류가 연속 이 횟수(기본 8) 나오면 fal 이 "요청을 잃은"
# 것으로 보고 같은 front/back 으로 새로 제출한다(새 requestArn 으로 계속 조회). 실측(2026-10-20)
# 으로 503 이 10번 연속이면 그 요청은 끝내 못 받으므로, 전체 실패(TRANSIENT_MAX) 전에 재제출한다.
LOST_AFTER = _int("VNEW_3D_LOST_AFTER", 8)
# "잃은 요청" 자동 재제출은 variant 당 최대 이 횟수(model3d.resubmits). 넘으면 그때 failed.
MAX_RESUBMITS = _int("VNEW_3D_MAX_RESUBMITS", 2)

# --- 단가(원장) ---
SEEDREAM_USD = _float("VNEW_SEEDREAM_USD", 0.04)  # Seedream 장당
# 3D 1건당. fal 가격표(2026-10-30 Claude 확인): Hunyuan3D v3 Normal $0.375 + multi-view images +$0.15 + custom face count
# +$0.15 = $0.675. 지금 후면 그림(back_image_url)과 face_count 를 이미 보내므로 실제 단가가 $0.675 다. 옆모습을 더해도 같다.
MODEL3D_USD = _float("VNEW_3D_USD", 0.675)

# --- 입력/진행 ---
MAX_SOURCES = _int("VNEW_MAX_SOURCES", 8)  # 업로드 웹툰 이미지 최대 장수
MAX_ANALYSIS_IMAGES = _int("VNEW_MAX_ANALYSIS_IMAGES", 6)  # 분석에 보낼 이미지 수
MAX_REFERENCE_IMAGES = _int("VNEW_MAX_REFERENCE_IMAGES", 4)  # Seedream 참고 이미지 수
# building 이 이 시간을 넘게 그대로면 조회 때 failed 로 본다(파드 재시작 대비).
BUILD_STALL_S = _int("VNEW_BUILD_STALL_S", 1800)  # 30분
# variant 의 heartbeatAt 이 이 시간을 넘게 갱신되지 않고 이 파드에서 실행 중이 아니면
# 끊긴 것으로 보고 그 variant 만 이어서 만든다(조회 때 자동 재개).
VARIANT_STALL_S = _int("VNEW_VARIANT_STALL_S", 600)  # 10분
# 자동 이어 만들기(재개)는 variant 당 최대 이 횟수. 넘으면 그 variant 만 failed.
MAX_RESUME = _int("VNEW_MAX_RESUME", 2)
# variant claim(선점 token) 이 이 시간을 넘게 그대로면(파드 재시작 등) 다른 파드가 다시 잡을 수 있다.
CLAIM_STALL_S = _int("VNEW_CLAIM_STALL_S", 600)  # 10분

# 만들 variant(그림체). 웹툰 그대로 + 실사.
VARIANTS = ("webtoon", "real")

# --- 추가 복장(outfits) ---
# 한 캐릭터에 둘 수 있는 추가 복장(auto 후보 + 사용자 추가) 최대 개수.
MAX_OUTFITS = _int("VNEW_MAX_OUTFITS", 12)
# 추가 복장 이름·설명(한국어)의 최대 길이.
MAX_OUTFIT_LABEL_LEN = _int("VNEW_MAX_OUTFIT_LABEL_LEN", 60)
MAX_OUTFIT_PROMPT_LEN = _int("VNEW_MAX_OUTFIT_PROMPT_LEN", 300)
# 복장 일관성 검사에서 시트를 다시 그리는 최대 횟수(1회). 넘으면 warning.
OUTFIT_SHEET_RETRY = _int("VNEW_OUTFIT_SHEET_RETRY", 1)
# 추가 복장 그리기(images)가 시작(startedAt)부터 이 시간을 넘으면 멈춘 것으로 본다(앱 재시작 등, 지시서 102 피드백).
# 정상 1벌은 Seedream 4장이라 10분 안쪽이다. 멈춘 복장은 상세에 failed 로 보이고 다시 만들기가 409 없이 시작한다.
# 0·음수처럼 짧은 값이면 진행 중 빌드를 두 번 시작할 수 있어 60초 아래로는 내리지 않는다.
OUTFIT_STALL_S = max(60, _int("VNEW_OUTFIT_STALL_S", 1200))  # 20분

# --- ⑤ 영상 생성: 프리비즈 → 실사·웹툰(설계자 2026-10-21, 지시서 038) ---
# 승인된 프리비즈(3D 마네킹) 영상에 실사·웹툰 그림체를 각각 입히는 fal video-to-video/edit 모델.
# Claude 실측(2026-10-21): kling o3 standard 는 video_url(프리비즈)·elements(마네킹 색 ↔ 캐릭터
# 정면/후면)·image_urls(웹툰 그림체 참고) 를 받아 배치·카메라·인원·타이밍을 정확히 유지한다.
VNEW_PREVIZ_EDIT_MODEL = os.getenv(
    "VNEW_PREVIZ_EDIT_MODEL", "fal-ai/kling-video/o3/standard/video-to-video/edit"
)
# 단가(초당 USD). fal pricing(endpoint) 을 먼저 쓰고, 못 받으면 이 기본값(standard $0.14/초, 실측).
VNEW_PREVIZ_EDIT_USD_PER_S = _float("VNEW_PREVIZ_EDIT_USD_PER_S", 0.14)
# edit 작업 1건의 참고 입력(elements + image_urls) 합계 최대 개수(실측 2026-10-21: 4).
PREVIZ_MAX_INPUTS = _int("VNEW_PREVIZ_MAX_INPUTS", 4)
# 실사: elements 최대 개수(인물). image_urls(장소 사진)은 넣지 않는다 → elements 로 4까지.
PREVIZ_REAL_MAX_ELEMENTS = _int("VNEW_PREVIZ_REAL_MAX_ELEMENTS", 4)
# 웹툰: elements 최대 3 + image_urls 1(그림체 참고 원작 컷) = 합 4.
PREVIZ_WEBTOON_MAX_ELEMENTS = _int("VNEW_PREVIZ_WEBTOON_MAX_ELEMENTS", 3)
PREVIZ_WEBTOON_MAX_IMAGE_URLS = _int("VNEW_PREVIZ_WEBTOON_MAX_IMAGE_URLS", 1)
# 구간 프롬프트 목표 상한(모델 maxLength 가 더 작으면 그 안으로). 설계자: 2,500자 이내.
PREVIZ_PROMPT_TARGET = _int("VNEW_PREVIZ_PROMPT_TARGET", 2500)
# 프리비즈 모드 동시 제출 상한(설계자 2026-10-21, 지시서 041 · 2026-10-23 ⑤ OOM 완화). 한 순회에서
# 선점·제출할 수 있는 칸(구간×그림체) 수. preparing+submitted(진행 중) 합이 이 수를 넘지 않게 한다.
# 기본 2(옛 4에서 낮춤 — 512Mi 파드가 동시 4칸 준비에서 OOMKilled). env 로 바꿀 수 있다.
VNEW_GEN_PARALLEL = _int("VNEW_GEN_PARALLEL", 2)

# ⑤ OOM 완화(설계자 2026-10-23). 영상 생성 때 녹화 mp4 받기·ffmpeg·참고 이미지·base64·본문
# 직렬화가 동시에 메모리에 쌓여 512Mi 파드가 OOMKilled 됐다. 아래 값으로 준비 단계를 직렬화하고,
# 참고 이미지를 줄이고, 끊긴 파드가 더 빨리 복구되게 한다.
#
# 준비(녹화 받기→클립 자르기→payload→본문 직렬화·제출) 를 프로세스 전역에서 동시에 몇 칸까지
# 할지. 기본 1(한 번에 한 칸씩만 큰 바이트를 메모리에 둔다). 조회(poll)는 이 상한 밖(가볍다).
VNEW_GEN_PREPARE_CONCURRENCY = _int("VNEW_GEN_PREPARE_CONCURRENCY", 1)
# 참고 이미지(캐릭터 정면·후면·원작 웹툰 컷)를 data URI 로 만들기 전 긴 변 최대 px. 넘으면 줄여
# JPEG q=85 로 다시 인코딩한다(이미 작으면 그대로). 기본 1024.
VNEW_REF_MAX_PX = _int("VNEW_REF_MAX_PX", 1024)
# 프리비즈 모드 preparing(선점) 이 이 시간을 넘게 그대로면 queued 로 되돌린다(선점 해제). 파드가
# 죽어 드라이버가 사라져도 이 시간 뒤 다시 시도한다. 기본 300초(옛 15분보다 짧게 — 빠른 복구).
VNEW_PREPARE_STALE_S = _int("VNEW_PREPARE_STALE_S", 300)

# 프리비즈 승인 관문(설계자 2026-10-21, 지시서 049·④). 모든 컷의 시점 일치도(fit.match)가 이 값
# 이상이어야 승인된다(force 로 넘길 수 있다). match 가 null(판정 불가)인 컷도 낮은 컷으로 센다.
VNEW_PREVIZ_APPROVE_MIN = _float("VNEW_PREVIZ_APPROVE_MIN", 0.7)

# 프리비즈 소품 관통 보정(084 R1, 2026-10-28). 켜면 서버가 앉을 소품·엉덩이 높이·다리 방향·각도를 정하고
# 인물이 소품과 겹치면 밀어낸다(설계 저장 전 2회, 보기 때 사본에). false 면 지금 동작(보정·sit 없음,
# 엉덩이 0.5). 템플릿·base 에 배선해 config.<target>.env 한 줄로 되돌린다.
VNEW_PREVIZ_COLLISION = _bool("VNEW_PREVIZ_COLLISION", True)

# 프리비즈 받침 판단 반영(previz-support-reasoning, 2026-10-29). 켜면 패널에서 읽은 받침 판단(자세 → 받침 → 높이 →
# 앉는 방식 → 발)을 장면 설계 프롬프트에 넣고, 서버가 컷 인물 자세·앉을 소품·소품 윗면 높이를 맞춰 fit[cut].support 에
# 남긴다. false 면 프롬프트·맞춤·fit.support 없음(지금 동작). 패널 읽기의 supports 는 설정과 상관없이 읽어 캐시에 둔다.
VNEW_PREVIZ_SUPPORT = _bool("VNEW_PREVIZ_SUPPORT", True)

# 만화 기호·분위기(vnew-live-fx 묶음 A, 2026-10-30). 켜면 ③ 콘티 컷마다 기호(fx·fxSuggest·fxDrawn)와 분위기 한 줄
# (moodKo·moodEn)을 두고, 콘티를 만들 때·[기호·분위기 채우기] 때 상위 모델 1회로 채운다. 기호·분위기만 고친 저장은
# 프리비즈 승인을 풀지 않는다. false 면 지금 동작(채우기 라우트 404·저장 정규화 없음·늘 승인 해제). 저장된 값은 지우지 않는다.
VNEW_FX = _bool("VNEW_FX", True)
# 채우기 1회에 보낼 패널 이미지 상한. 넘으면 순서를 지켜 고르게 고른다(코드 기본, 템플릿에 넣지 않는다).
VNEW_FX_MAX_PANELS = _int("VNEW_FX_MAX_PANELS", 20)

# 실사 캐스팅(vnew-live-fx 묶음 B, 2026-10-30). 켜면 캐릭터 상세에서 실사 배우 사진을 글로만 뽑아 고르고(Seedream 4.5
# text-to-image, 웹툰 그림은 보내지 않는다), 캐릭터 분석에 성별(genderEn)을 더한다. false 면 지금 동작(캐스팅 라우트 404·
# 분석 도구·SYSTEM 그대로). 이미 시작한 뽑기는 끝까지 진행하고, 저장된 casting 은 지우지 않는다.
VNEW_CASTING = _bool("VNEW_CASTING", True)
# 아래는 코드 기본(템플릿에 넣지 않는다). 모델·장당 금액·장 수는 참고 구현 vpoc/live_service.py:36-39.
VNEW_CASTING_MODEL = os.getenv("VNEW_CASTING_MODEL", "fal-ai/bytedance/seedream/v4.5/text-to-image")
VNEW_CASTING_USD = _float("VNEW_CASTING_USD", 0.04)  # 장당(fal 가격 2026-10-26)
VNEW_CASTING_COUNT = max(1, min(4, _int("VNEW_CASTING_COUNT", 2)))  # 1~4로 자른다
VNEW_CASTING_SIZE = os.getenv("VNEW_CASTING_SIZE", "1920x2560")  # 세로 초상, 스키마 최소 1920
# 뽑기 하트비트가 이 시간보다 오래됐고 이 파드에서 돌지 않으면 이어 조회한다(제출 전 칸은 실패로 끝낸다).
VNEW_CASTING_STALL_S = _int("VNEW_CASTING_STALL_S", 600)
# 뽑기 시작부터 이 시간이 지나면 남은 칸을 실패로 끝낸다.
VNEW_CASTING_POLL_MAX_S = _int("VNEW_CASTING_POLL_MAX_S", 900)

# 통 테이크 실사 버전(vnew-live-fx 묶음 C, 2026-10-30). 켜면 웹툰 통 테이크가 끝난 gen 에서 같은 프리비즈 녹화로
# 실사 버전(회차)을 만들 수 있다(POST /generate/real). false 면 그 시작만 404(TakeRealDisabled)이고, 이미 시작한 실사
# 회차는 드라이버가 끝까지 진행한다. 소리 검사 거절 안내·"소리 없이 다시 만들기"·기존 결함 수정(C.6·C.10)은 이 설정과
# 상관없이 적용한다. 저장된 realRun·realHistory 는 지우지 않는다.
VNEW_TAKE_REAL = _bool("VNEW_TAKE_REAL", True)

# 캐릭터 충실도(char-fidelity, 2026-10-30). 켜면 분석이 기준 사진 한 장의 옷을 그대로 기본 복장으로 쓰고, 한쪽 소품의 좌우를
# 화면 기준으로 받아 코드가 캐릭터 좌우로 바꾼다. 시트는 화면 왼쪽으로 45°씩 도는 다섯 인물로 그리고, 정면·후면·시트를 검사한다.
# false 면 지금 동작(분석 SYSTEM·도구·프롬프트·검사 그대로). 저장된 새 필드는 지우지 않는다.
VNEW_CHAR_FIDELITY = _bool("VNEW_CHAR_FIDELITY", True)
# 3D 옆모습(char-fidelity D4). 켜면 variant 마다 왼쪽·오른쪽 옆모습을 그려 Hunyuan3D 에 left/right_image_url 로 보낸다.
# false 면 정면·후면만 보낸다(지금 동작).
VNEW_3D_SIDE_VIEWS = _bool("VNEW_3D_SIDE_VIEWS", True)

# 3D 공급자(char-fidelity T2b, 2026-10-30). 기본 tripo(fal Tripo H3.1). Claude 비교(여의준 정면·후면 그대로): Hunyuan3D v3 는
# 2048 입력 + 50만 면에서도 앞머리 덩어리·부은 얼굴이 그대로였고, Tripo H3.1(texture·geometry detailed)이 두 그림체 모두 사진과
# 가장 가까웠다. 되돌리기: config.<target>.env 에 VNEW_3D_PROVIDER=hunyuan (T2 본문·원장 그대로). 진행 중 요청은 공급자 설정이
# 아니라 requestArn 의 endpoint 로 조회·회전·재제출한다. 아래 값이 틀리면 기본값을 쓰고 경고 로그 vnew.settings.invalid 1줄.
VNEW_3D_PROVIDER = _checked_choice("VNEW_3D_PROVIDER", "tripo", ("tripo", "hunyuan"))
# 다시점(정면·왼쪽·후면[·오른쪽]) / 단일(정면만) endpoint_id. 큐 조회 주소는 앞 두 마디(tripo3d/h3.1)다.
VNEW_3D_TRIPO_ENDPOINT = _checked_tripo_endpoint("VNEW_3D_TRIPO_ENDPOINT", "tripo3d/h3.1/multiview-to-3d")
VNEW_3D_TRIPO_SINGLE_ENDPOINT = _checked_tripo_endpoint(
    "VNEW_3D_TRIPO_SINGLE_ENDPOINT", "tripo3d/h3.1/image-to-3d"
)
# 면 수 상한. 300000 이면 GLB 약 12MB·얼굴 품질 그대로, 제한하지 않으면 190만 면·58MB(Claude 실측 2026-10-30).
# 범위 10000~1000000(GLB 약 30MB 이하 — 512Mi 앱이 옮기며 두 벌을 쥔다).
VNEW_3D_TRIPO_FACE_LIMIT = _checked_int("VNEW_3D_TRIPO_FACE_LIMIT", 300000, 10000, 1000000)
_TRIPO_QUALITIES = ("standard", "detailed")
VNEW_3D_TRIPO_TEXTURE_QUALITY = _checked_choice("VNEW_3D_TRIPO_TEXTURE_QUALITY", "detailed", _TRIPO_QUALITIES)
VNEW_3D_TRIPO_GEOMETRY_QUALITY = _checked_choice("VNEW_3D_TRIPO_GEOMETRY_QUALITY", "detailed", _TRIPO_QUALITIES)
# Tripo 입력 정사각 한 변(흰 배경 패딩). 1024 면 얼굴이 약 150px 라 2048 로 둔다. Hunyuan 은 MODEL_INPUT_SQUARE 그대로.
VNEW_3D_TRIPO_INPUT_SQUARE = _checked_int("VNEW_3D_TRIPO_INPUT_SQUARE", 2048, 512, 4096)
# false 면 옆모습이 있어도 단일(정면만) endpoint 로 보낸다.
VNEW_3D_TRIPO_MULTIVIEW = _checked_bool("VNEW_3D_TRIPO_MULTIVIEW", True)
# Tripo 결과는 정면이 +X 를 본다(orientation align_image 도 같음). 옮길 때 루트 노드를 Y축으로 이만큼 돌려 정면을 +Z(화면
# model-viewer 정면)로 맞춘다. -90° 는 +X → +Z. 0 이면 돌리지 않는다.
VNEW_3D_TRIPO_YAW_DEG = _checked_float("VNEW_3D_TRIPO_YAW_DEG", -90.0, -360.0, 360.0)
# Tripo 1건 단가. 근거: fal 가격표(2026-10-30 Claude 확인) HD 텍스처 $0.40 + detailed 형상 $0.20 = $0.60.
VNEW_3D_TRIPO_USD = _checked_float("VNEW_3D_TRIPO_USD", 0.60, 0.0, 10.0)
# Tripo 다시점의 왼쪽·오른쪽 자리를 바꿔 넣는다. 개발계 확인에서 GLB 좌우가 반대로 나오면 켠다(Hunyuan 은 _HUNYUAN_SIDE_FIELDS).
VNEW_3D_SIDE_SWAP = _checked_bool("VNEW_3D_SIDE_SWAP", False)

# --- ⑤ 촬영 지시서(shot brief) (설계자 2026-10-22) ---
# 프리비즈 승인 직후, 장소마다 1회 호출해 그 장소 컷들의 "촬영 전문 언어" 지시서를 쓴다. 작성 모델은
# 설계와 같은 체인(Astra 먼저, 실패 시 Claude 분석 체인). 장면·배경·연기·대사 연기를 영어로 섬세히.
BRIEF_MODEL = os.getenv("VNEW_BRIEF_MODEL", ASTRA_MODEL)
BRIEF_FALLBACK_MODEL = os.getenv("VNEW_BRIEF_FALLBACK_MODEL", ANALYZE_FALLBACK_MODEL)
# 촬영 지시서 작성 task 가 이 시간을 넘게 running 이면 멈춘 것으로 본다(조회 때 failed).
BRIEF_STALL_S = _int("VNEW_BRIEF_STALL_S", 1800)

# --- ⑤ 통 테이크(Seedance 2.5 reference-to-video) 모드(설계자 2026-10-26, 063) ---
# input.videoModel 의 caps 에 videoListField(배열 영상 입력)가 있으면 gen.mode = "previz-take" 로
# 바뀐다(아니면 지금 그대로 컷 단위 "previz"). 블록아웃 녹화 통째(≤30초) + 참조 이미지(인물·그림체·
# 장소)로 웹툰 그림체 영상을 한 번에 만든다. Claude 조사(video-restyle-research.md) 레시피.
#
# 한 테이크 최대 길이(초). 녹화 구간들을 앞에서부터 묶어 합계가 이 값을 넘지 않게 테이크를 만든다.
VNEW_TAKE_MAX_SECONDS = _int("VNEW_TAKE_MAX_SECONDS", 30)
# 테이크 출력 해상도(480p 초안 권장 — 비용 통제). 480p/720p/1080p.
VNEW_TAKE_RESOLUTION = os.getenv("VNEW_TAKE_RESOLUTION", "480p")
# draft(초안) 모드 — 480p 초안 생성, 응답에 draft_id(나중 1080p 완성용). 기본 켬.
VNEW_TAKE_DRAFT = _bool("VNEW_TAKE_DRAFT", True)
# 효과음·립싱크 오디오 생성(설계자 2026-10-26, 064). fal 스키마: "영상 생성 비용은 오디오
# 생성 여부와 무관하게 같다" → 기본 켬(효과음·환경음·립싱크 대사 포함).
VNEW_TAKE_AUDIO = _bool("VNEW_TAKE_AUDIO", True)
# Seedance 토큰 단가(fal pricing unit "1000 tokens"). pricing 을 먼저 쓰고, 못 받으면 이 기본값.
VNEW_TAKE_TOKEN_UNIT_PRICE = _float("VNEW_TAKE_TOKEN_UNIT_PRICE", 0.0214)
# 참조 이미지 중 원작 그림체 패널 최대 장수.
VNEW_TAKE_MAX_PANELS = _int("VNEW_TAKE_MAX_PANELS", 3)
# 장면 의상·목소리 읽기(castNotes)에 Claude 로 보낼 원작 패널 최대 장수(인물이 보이는 것).
VNEW_TAKE_CAST_PANELS = _int("VNEW_TAKE_CAST_PANELS", 4)
# 통 테이크 프롬프트 바이트 상한(UTF-8, 설계자 2026-10-26). Seedance 2.5 reference-to-video 의
# prompt 에는 API 스키마상 maxLength 가 명시돼 있지 않다(Claude 확인) → 기본 4000 바이트로 넉넉히 둔다.
# 단, 모델 caps 의 promptMaxLength 가 '스키마에 명시된 값'이면(= 기본 2500 이 아닌 실제 maxLength)
# 그 값과 4000 중 작은 값을 쓴다(take_prompt_budget 참고). 2500 은 fal 기본값(미명시)이라 take 에선
# 길이 상한으로 보지 않는다. Kling 컷 단위는 실측 2,500 제한을 그대로 쓴다(PREVIZ_PROMPT_TARGET).
VNEW_TAKE_PROMPT_MAX = _int("VNEW_TAKE_PROMPT_MAX", 4000)
# 해상도별 (가로, 세로) — 토큰 공식 tokens = w×h×24×s/1024 에 쓴다(fal 실측 기준).
TAKE_RESOLUTION_WH = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


def previz_edit_usd(seconds: float | None = None) -> float:
    """프리비즈 edit 1건(한 그림체·한 구간) 금액 = 초 × 단가.

    단가는 fal pricing(VNEW_PREVIZ_EDIT_MODEL) 의 초당 가격을 먼저 쓰고, 못 받으면
    VNEW_PREVIZ_EDIT_USD_PER_S 기본값. 초를 비우면 vpoc 기본 영상 길이로 계산한다.
    """
    secs = float(vpoc_settings.DURATION_S if seconds is None else seconds)
    per_s = _previz_edit_unit_price()
    return round(per_s * secs, 4)


def _previz_edit_unit_price() -> float:
    """프리비즈 edit 초당 단가(USD). fal pricing 우선, 실패 시 기본값. 순환 import 피해 늦게 import."""
    try:
        from ..vpoc import fal as vpoc_fal  # noqa: PLC0415

        price = vpoc_fal.pricing(VNEW_PREVIZ_EDIT_MODEL)
        if price and str(price.get("unit") or "").lower() == "seconds":
            unit_price = float(price.get("unitPrice") or 0.0)
            if unit_price > 0:
                return unit_price
    except Exception:  # noqa: BLE001 — pricing 실패는 기본값으로 대체.
        pass
    return float(VNEW_PREVIZ_EDIT_USD_PER_S)
