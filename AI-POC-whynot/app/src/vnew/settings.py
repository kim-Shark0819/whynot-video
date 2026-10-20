"""vnew 설정값 — 캐릭터 만들기(설계자 2026-10-20). 전부 환경변수에서 읽는다.

vpoc.settings 를 재사용하되(모델·버킷·키), 캐릭터 만들기 전용 값만 여기에 둔다.
여기에만 os.getenv 를 둔다. characters.py · routes.py 는 이 상수를 읽는다.
"""

from __future__ import annotations

import os

from ..vpoc import settings as vpoc_settings


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
MODEL3D_USD = _float("VNEW_3D_USD", 0.375)  # 3D 1건당

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
