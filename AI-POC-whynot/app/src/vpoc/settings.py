"""vpoc1 설정값 — PRD 6.8. 전부 환경변수에서 읽는다(render.py → ConfigMap).

여기에만 os.getenv 를 둔다. 다른 모듈은 이 상수를 읽는다.
"""

from __future__ import annotations

import os


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")

# 기능 스위치. VIDEO_GEN_ENABLED 가 꺼진 배포에서는 이 값과 무관하게 라우트가 없다.
VPOC_ENABLED = _flag("VPOC_ENABLED", "true")
RAW_ERRORS = _flag("VPOC_RAW_ERRORS", "true")
SMOKE_ENABLED = _flag("VPOC_SMOKE_ENABLED", "false")
# ④단계 "이 샷만 의견 반영해 다시 만들기". 끄면 새 액션 3개가 404 가 되고 화면 버튼도 사라진다.
# 문제가 생겼을 때 이 값만 꺼서 이전 동작으로 되돌린다(regen-plan G6).
REGEN_ENABLED = _flag("VPOC_REGEN_ENABLED", "true")

MAIN_BUCKET = os.getenv("S3_BUCKET", "")

TEXT_MODEL = os.getenv(
    "VPOC_TEXT_MODEL", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
TEXT_REGION = os.getenv("VPOC_TEXT_REGION", AWS_REGION)

IMAGE_MODEL = os.getenv("VPOC_IMAGE_MODEL", "stability.sd3-5-large-v1:0")
IMAGE_REGION = os.getenv("VPOC_IMAGE_REGION", "us-west-2")

VIDEO_MODEL = os.getenv("VPOC_VIDEO_MODEL", "luma.ray-v2:0")
VIDEO_REGION = os.getenv("VPOC_VIDEO_REGION", "us-west-2")
VIDEO_BUCKET = os.getenv("VPOC_VIDEO_BUCKET", "")

# ② 영상 모델 고르기(설계자 2026-10-07, dev 만). 꺼져 있으면 항상 Luma Ray2 다.
MODEL_PICKER_ENABLED = _flag("VPOC_MODEL_PICKER_ENABLED", "false")
# BytePlus ModelArk(Seedance). 키는 Secret 에서만 온다. 모델 ID 가 비면 그 모델은 고를 수 없다.
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3").rstrip("/")
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_MODEL_IDS = {
    "seedance-2-0": os.getenv("VPOC_ARK_MODEL_2_0", "dreamina-seedance-2-0-260128"),
    "seedance-2-0-mini": os.getenv("VPOC_ARK_MODEL_2_0_MINI", ""),
    "seedance-2-0-fast": os.getenv("VPOC_ARK_MODEL_2_0_FAST", "dreamina-seedance-2-0-fast-260128"),
    "seedance-2-5": os.getenv("VPOC_ARK_MODEL_2_5", "dreamina-seedance-2-5-260628"),
}
# 초당 USD. 비면 "단가 미정" — 금액 0 으로 기록하고 화면에 미정으로 보인다(설계자가 단가표를 줄 예정).
ARK_USD_PER_SEC = {
    "seedance-2-0": os.getenv("VPOC_ARK_USD_PER_SEC_2_0", ""),
    "seedance-2-0-mini": os.getenv("VPOC_ARK_USD_PER_SEC_2_0_MINI", ""),
    "seedance-2-0-fast": os.getenv("VPOC_ARK_USD_PER_SEC_2_0_FAST", ""),
    "seedance-2-5": os.getenv("VPOC_ARK_USD_PER_SEC_2_5", ""),
}
ARK_RESOLUTION = os.getenv("VPOC_ARK_RESOLUTION", "720p")

RESOLUTION = os.getenv("VPOC_RESOLUTION", "540p")
DURATION = os.getenv("VPOC_DURATION", "5s")
ASPECT = os.getenv("VPOC_ASPECT", "16:9")
DURATION_S = _int("VPOC_DURATION_S", 5)

# 실측(2026-09-23 C8): "On-demand model inference concurrent requests for Luma Ray V2" = 1.
# 쿼터가 1이라 동시 제출도 1이다. 2 이상을 넣으면 ThrottlingException 이 난다.
MAX_INFLIGHT = max(1, _int("VPOC_MAX_INFLIGHT", 1))
MAX_SHOTS = _int("VPOC_MAX_SHOTS", 6)
MIN_SHOTS = 3

# 비용 상한은 없다(2026-09-23 사용자 결정). 기록만 하고 제출을 막지 않는다.
# 여기에 상한 상수를 다시 넣지 않는다 — 넣으면 ledger 에 검사가 따라 들어온다.
VIDEO_USD_PER_SEC = {"540p": 0.75, "720p": 1.5}
IMAGE_USD = _float("VPOC_IMAGE_USD", 0.08)
TEXT_USD_PER_MTOK_IN = _float("VPOC_TEXT_USD_PER_MTOK_IN", 3.0)
TEXT_USD_PER_MTOK_OUT = _float("VPOC_TEXT_USD_PER_MTOK_OUT", 15.0)

# 업로드
MAX_UPLOAD_BYTES = _int("VPOC_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
MAX_ANALYSIS_IMAGES = 12
TILE_HEIGHT = 1568

# 모델 호출 시간 제한
READ_TIMEOUT_S = _int("VPOC_READ_TIMEOUT_S", 300)
TASK_STALL_S = _int("VPOC_TASK_STALL_S", 600)
POLL_MIN_INTERVAL_S = 10


def ark_usd_per_sec(model: str) -> float | None:
    raw = str(ARK_USD_PER_SEC.get(model) or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def shot_usd(model: str = "") -> float:
    """샷 1개 금액. model 이 Seedance 이고 단가가 없으면 0(단가 미정)."""
    if model in ARK_MODEL_IDS:
        per_sec = ark_usd_per_sec(model)
        return round((per_sec or 0.0) * DURATION_S, 4)
    return round(VIDEO_USD_PER_SEC.get(RESOLUTION, 0.75) * DURATION_S, 4)
