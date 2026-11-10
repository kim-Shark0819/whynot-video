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
# ② 분석(웹툰 이미지 읽기)은 이 계정에서 쓸 수 있는 가장 좋은 Claude 로 한다(설계자 2026-10-13).
# 2026-10-13 실측: 이 계정은 Opus 4.6 · Sonnet 4.6 · Sonnet 4.5 만 호출 가능(Opus 4.7+/5.x 는 미허용).
# 설계자 2026-10-14: 가장 분석이 좋은 모델을 쓴다. 같은 웹툰 장면 비교(2026-10-14): Fable 5.1 ≥ Opus 5.5
# >> GPT-6 Astra(이미지를 거의 읽지 못함). 와이낫 계정은 5.x 한도가 0이라 와이랩 계정에서 임시로 빌린다
# (BROKER_*, Astra 는 키이스트 ASTRA_BROKER_*). 빌리기가 안 되면 아래 예비 모델(와이낫 계정)로 자동으로 넘어간다.
ANALYZE_MODEL = os.getenv("VPOC_ANALYZE_MODEL", "global.anthropic.claude-fable-5-1")
# 샷마다 맡은 웹툰 장면을 보고 분위기 · 배경 · 인물별 행동 · 상황을 적는 모델(설계자 2026-10-13).
# Opus 5.5 는 이 계정에서 약관 동의 전이라(2026-10-13) 지금은 Opus 4.6. 동의 후 이 값만 바꾼다.
BRIEF_MODEL = os.getenv("VPOC_BRIEF_MODEL", "global.anthropic.claude-fable-5-1")
# 임시(2026-10-14): 와이낫 계정에서 아직 못 쓰는 모델은 와이랩 계정 역할을 빌려 부른다. 비우면 끈다.
# 와이낫 계정 한도가 열리면 VPOC_BEDROCK_BROKER_ROLE_ARN="" 로 끄고 와이랩 역할을 지운다.
BROKER_ROLE_ARN = os.getenv(
    # 임시로 와이랩 계정 역할을 빌린다(설계자 2026-10-14 승인). account-check: ignore
    "VPOC_BEDROCK_BROKER_ROLE_ARN", "arn:aws:iam::892278727066:role/whynot-bedrock-broker"  # account-check: ignore
).strip()
BROKER_EXTERNAL_ID = os.getenv("VPOC_BEDROCK_BROKER_EXTERNAL_ID", "whynot-vpoc-broker")
BROKER_REGION = os.getenv("VPOC_BEDROCK_BROKER_REGION", "ap-northeast-2")
BROKER_MODEL_MARKERS = ("claude-fable-", "claude-opus-5", "claude-sonnet-5")
# 임시(사용자 지시 2026-11-10): GPT-6 Astra 는 와이랩이 아니라 키이스트 계정 역할을 빌려 부른다(Astra 만 허용).
# 비우면 Astra 는 와이낫 계정으로 부른다(와이랩으로 넘어가지 않는다). 와이낫 계정에서 열리면
# VPOC_BEDROCK_ASTRA_ROLE_ARN="" 로 끄고 키이스트 역할을 지운다.
ASTRA_BROKER_ROLE_ARN = os.getenv(
    # 임시로 키이스트 계정 역할을 빌린다(사용자 지시 2026-11-10). account-check: ignore
    "VPOC_BEDROCK_ASTRA_ROLE_ARN", "arn:aws:iam::722500516860:role/imsi-astra-used-role"  # account-check: ignore
).strip()
ASTRA_BROKER_EXTERNAL_ID = os.getenv("VPOC_BEDROCK_ASTRA_EXTERNAL_ID", "whynot-astra-broker")
ASTRA_BROKER_REGION = os.getenv("VPOC_BEDROCK_ASTRA_REGION", "ap-northeast-2")
ASTRA_BROKER_MODEL_MARKERS = ("gpt-6",)
# Seedance 네이티브 오디오(설계자 2026-10-14 "영상에 소리를 넣는다"). 끄려면 VPOC_ARK_GENERATE_AUDIO=false.
ARK_GENERATE_AUDIO = _flag("VPOC_ARK_GENERATE_AUDIO", "true")
# 키 장면(콘티) 방식(설계자 2026-10-14): 5초마다 키 장면을 그리고 Seedance 시작 · 끝 장면으로 잇는다.
# Seedance 2.0/2.5 의 시작 · 끝 장면 모드는 4초 이상이라 3초 간격은 불가(실측). 기본 30초 = 키 장면 7장.
KEYFRAME_INTERVAL_S = _int("VPOC_KEYFRAME_INTERVAL_S", 5)
KEYFRAME_TOTAL_CHOICES = (20, 30, 40, 60)
KEYFRAME_TOTAL_DEFAULT = 30
# 영상 1개 한 번에(single) 방식(설계자 2026-10-19): 샷별로 나눠 만들지 않고, ② 에서 고른 길이(초)의
# 영상 하나만 만든다. 길이는 고른 모델이 한 번에 만들 수 있는 범위(VIDEO_DURATION_RANGE · fal caps)
# 안에서 고른다 — 20/30/40/60 고정 선택지는 없앴다. ③ 키 장면 콘티는 그대로 두고, 완성 키 장면을
# 참고/시작·끝 이미지로 쓴다. 키 장면 수 = clamp(ceil(초 / KEYFRAME_INTERVAL_S) + 1, 2, SINGLE_MAX_KEYFRAMES).
SINGLE_MIN_KEYFRAMES = 2
SINGLE_MAX_KEYFRAMES = 4
# 길이를 못 고른 옛/깨진 값일 때의 기본값은 min(SINGLE_DURATION_DEFAULT, 모델 최대). routes 가 계산한다.
SINGLE_DURATION_DEFAULT = _int("VPOC_SINGLE_DURATION_DEFAULT", 10)
SEEDREAM_ROUGH_MODEL = os.getenv("VPOC_SEEDREAM_ROUGH_MODEL", "dola-seedream-5-0-flash-260915")
SEEDREAM_FINAL_MODEL = os.getenv("VPOC_SEEDREAM_FINAL_MODEL", "dola-seedream-5-0-pro-260628")
SEEDREAM_USD = _float("VPOC_SEEDREAM_USD", 0.035)
# Seedream 공급자(작업 124, 2026-11-04): BytePlus 쪽에서 ARK 키가 사라져("The API key doesn't exist") 기본을 fal 로
# 바꿨다. "fal" = fal 큐(참고 이미지 있으면 edit, 없으면 text-to-image), "ark" = 예전 BytePlus 그대로(인증 실패면
# 그 호출만 fal). 그 밖의 값은 fal. 모델 id 가 빈 값이면 아래 기본. 장당 금액은 fal 이면 SEEDREAM_FAL_USD.
SEEDREAM_PROVIDER = os.getenv("VPOC_SEEDREAM_PROVIDER", "fal").strip().lower()
SEEDREAM_FAL_EDIT_MODEL = (os.getenv("VPOC_SEEDREAM_FAL_EDIT_MODEL", "").strip()
                           or "fal-ai/bytedance/seedream/v4.5/edit")
SEEDREAM_FAL_T2I_MODEL = (os.getenv("VPOC_SEEDREAM_FAL_T2I_MODEL", "").strip()
                          or "fal-ai/bytedance/seedream/v4.5/text-to-image")
SEEDREAM_FAL_USD = _float("VPOC_SEEDREAM_FAL_USD", 0.04)
# 한 샷에 Seedance 로 보내는 웹툰 장면 이미지 수 / 설명을 쓸 때 Claude 에 보여 주는 장면 수.
PANELS_PER_SHOT_REFS = _int("VPOC_PANELS_PER_SHOT_REFS", 3)
PANELS_PER_SHOT_BRIEF = _int("VPOC_PANELS_PER_SHOT_BRIEF", 6)
# 앞 모델이 실패(혼잡 · 시간 초과 · 미허용 · 도구 미사용)하면 차례로 바꿔 다시 부른다.
TEXT_FALLBACK_MODELS = [
    item.strip()
    for item in os.getenv(
        "VPOC_TEXT_FALLBACK_MODELS",
        "global.anthropic.claude-opus-5-5,global.anthropic.claude-opus-4-6-v1,"
        "global.anthropic.claude-sonnet-4-6,global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ).split(",")
    if item.strip()
]


def _parse_marks(raw: object) -> tuple[str, ...]:
    """쉼표로 나눈 모델 id 부분 문자열 목록 → (소문자, …). 앞뒤 공백·빈 항목은 버린다. 빈 값이면 ()."""
    return tuple(item.strip().lower() for item in str(raw or "").split(",") if item.strip())


# 모델마다 받지 않는 설정을 첫 요청부터 뺀다(llm-top-model-first R2, 2026-10-28). 모델 id(소문자)에 이
# 부분 문자열 중 하나가 들어 있으면 적용한다. 2026-10-14 실측: Opus 5.x · Fable 5.x 는 toolChoice tool/any 와
# temperature 를, GPT-6 Astra 는 temperature 를 거절한다. 빈 값이면 끄고 예전처럼 거절을 겪은 뒤 적응한다.
LLM_NO_FORCED_TOOL_DEFAULT = "claude-opus-5,claude-fable-5"
LLM_NO_TEMPERATURE_DEFAULT = "claude-opus-5,claude-fable-5,gpt-6"
LLM_NO_FORCED_TOOL = _parse_marks(os.getenv("VPOC_LLM_NO_FORCED_TOOL", LLM_NO_FORCED_TOOL_DEFAULT))
LLM_NO_TEMPERATURE = _parse_marks(os.getenv("VPOC_LLM_NO_TEMPERATURE", LLM_NO_TEMPERATURE_DEFAULT))
# 출력이 max_tokens 에서 잘려 도구 입력이 없으면, 다음 모델로 넘어가기 전에 같은 모델로 한 번 더 부른다
# (llm-top-model-first R1). 예산 = min(max_tokens × FACTOR, CAP). 그 값이 원래 예산 이하면 다시 부르지 않는다
# (FACTOR=1 이면 끈다).
LLM_TRUNC_RETRY_FACTOR = _int("VPOC_LLM_TRUNC_RETRY_FACTOR", 4)
LLM_TRUNC_RETRY_CAP = _int("VPOC_LLM_TRUNC_RETRY_CAP", 4096)

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
# mini · fast 는 설계자 지시(2026-10-07)로 뺐다.
ARK_MODEL_IDS = {
    "seedance-2-0": os.getenv("VPOC_ARK_MODEL_2_0", "dreamina-seedance-2-0-260128"),
    "seedance-2-5": os.getenv("VPOC_ARK_MODEL_2_5", "dreamina-seedance-2-5-260628"),
}
ARK_RESOLUTION = os.getenv("VPOC_ARK_RESOLUTION", "720p")
# 생성 영상 1초당 USD (참고 영상 없이 첫 프레임만 보내는 우리 경로 기준).
#
# 출처: 설계자가 준 BytePlus 콘솔 견적 화면 7장(2026-10-07). 견적은 "참고 영상 길이 + 생성 15초"
# 합계다. 2.5·480P 에서 참고 5초 $1.54, 30초 $2.77 → 참고 1초 $0.0492, 생성 1초 $0.0863,
# 참고/생성 비 0.57. 같은 비를 다른 칸에 적용해 생성 1초 = 견적 ÷ (15 + 5×0.57) = 견적 ÷ 17.85.
# 추정값이다 — 실제 청구(usage 토큰)와 다르면 이 표를 고친다.
ARK_USD_PER_SEC_TABLE = {
    "seedance-2-0": {"480p": 0.0605, "720p": 0.1305, "1080p": 0.3221, "4k": 0.6532},
    "seedance-2-5": {"480p": 0.0863, "720p": 0.1938},
}
# 표를 덮어쓸 때만 쓴다(현재 해상도의 1초 단가). 비면 표 값.
ARK_USD_PER_SEC = {
    "seedance-2-0": os.getenv("VPOC_ARK_USD_PER_SEC_2_0", ""),
    "seedance-2-5": os.getenv("VPOC_ARK_USD_PER_SEC_2_5", ""),
}

# 기존(BytePlus ARK 직결) Seedance 사용 여부(설계자 2026-10-26: "운영과 개발에 fal.ai 의 seedance 를
# 사용하도록 활성하고 기존 seedance 는 제외"). 기본 false — ARK Seedance(seedance-2-0/2-5)는 ② 모델
# 목록에서 빠지고, 저장된 선택은 아래 ARK_SEEDANCE_FAL_MAP 으로 fal Seedance 로 바뀐다(ark.resolve_model).
# "true" 면 예전처럼 ARK Seedance 가 그대로 보이고 쓰인다(회귀 없음). 이미 제출된 ARK 작업
# (ark/tasks/ ARN)은 이 값과 무관하게 계속 조회·완료된다(ark.is_ark_job 유지).
ARK_SEEDANCE_ENABLED = _flag("VPOC_ARK_SEEDANCE_ENABLED", "false")
# 꺼져 있을 때 저장된 ARK Seedance 선택(seedance-2-0/2-5)을 바꿔 쓸 fal 모델. 둘 다 fal Seedance 2.5
# image-to-video 로 보낸다(등록·사용 가능할 때만 — ark.resolve_model 이 확인하고, 아니면 Luma 로 떨어진다).
ARK_SEEDANCE_FAL_MAP = {
    "seedance-2-0": "fal:bytedance/seedance-2.5/image-to-video",
    "seedance-2-5": "fal:bytedance/seedance-2.5/image-to-video",
}

# fal.ai — 키 하나로 수백 개의 영상 모델(Kling · MiniMax Hailuo · Wan · Veo …)을 큐 API 로 부른다
# (설계자 2026-10-18). Seedance(ark.py)와 같은 "외부 영상 공급자" 경로를 탄다. 키는 Secret 에서만
# 온다(FAL_KEY). 사용자가 ② 화면에서 "fal 에서 모델 가져오기"로 등록한 모델만 ② 목록에 나타난다.
FAL_KEY = os.getenv("FAL_KEY", "")
# 큐 API(제출·조회)와 모델 카탈로그/스키마 API 는 호스트가 다르다. 둘 다 env 로 덮어쓸 수 있다.
FAL_QUEUE_URL = os.getenv("FAL_QUEUE_URL", "https://queue.fal.run").rstrip("/")
FAL_API_URL = os.getenv("FAL_API_URL", "https://api.fal.ai/v1").rstrip("/")
# 모델 스키마 공개 주소(인증 불필요). `/v1/models?expand=openapi-3.0` 이 실제로는
# `{"error":{"code":"expansion_failed"}}` 를 돌려주므로(실측 2026-10-18), 그때 이 주소로 대체한다.
# `?endpoint_id=<id>` 를 붙여 GET 하면 정상 OpenAPI 3 를 준다.
FAL_OPENAPI_URL = os.getenv("FAL_OPENAPI_URL", "https://fal.ai/api/openapi/queue/openapi.json").rstrip("/")
# fal 모델 ID 형식: "fal:<endpoint_id>" (예: "fal:fal-ai/kling-video/v3/pro/image-to-video").
# ARK 모델 ID("seedance-2-0" 등)와 겹치지 않게 접두사로 가른다.
FAL_ID_PREFIX = "fal:"
# 영상 단계에서 실제로 쓸 수 있는 fal 카테고리. 나머지는 카탈로그 목록에만 보이고 등록은 거절된다.
# video-to-video 추가(설계자 2026-10-22): 영상만들기_신규 ①의 프리비즈 방식은 영상 입력을 받는
# 모델만 쓸 수 있으므로 등록을 허용한다. reference-to-video 추가(Claude 조사 2026-10-25): Seedance
# 2.5 reference-to-video 는 프리비즈 녹화(video_urls)+시트·그림체·장소 참조(image_urls)를 함께 받아
# 재렌더하는 1순위 후보다. vpoc(옛 ②) 피커는 video-to-video/reference-to-video/previzCapable 를 걸러
# 기존 화면에 나타나지 않게 한다(ark.choices).
FAL_VIDEO_CATEGORIES = (
    "text-to-video",
    "image-to-video",
    "video-to-video",
    "reference-to-video",
)
# 카탈로그 조회에서 함께 보여 주는(목록만, 영상 단계 사용 불가) 카테고리.
FAL_BROWSE_CATEGORIES = (
    "reference-to-video",
    "video-to-video",
    "image-to-video",
    "text-to-video",
    "image-to-3d",
    "text-to-3d",
)
# 등록 금지 endpoint 패턴(설계자 2026-10-22 지시: 사용 금지). fnmatch 글롭. 카탈로그 응답에는
# 표시하되(화면이 [활성화] 를 비활성으로), 등록 요청이 오면 400 Denied. 기본에 seedance 2.0
# fast 계열(`*seedance*2*fast*`)을 둔다. env(VPOC_FAL_DENY_ENDPOINTS, 쉼표 구분)로 덮어쓸 수 있다.
FAL_DENY_ENDPOINTS = tuple(
    item.strip()
    for item in os.getenv("VPOC_FAL_DENY_ENDPOINTS", "*seedance*2*fast*").split(",")
    if item.strip()
)


def is_denied_endpoint(endpoint: str) -> bool:
    """endpoint 가 FAL_DENY_ENDPOINTS 글롭 중 하나에 맞으면 True(소문자 비교)."""
    import fnmatch

    eid = str(endpoint or "").strip().lower()
    if not eid:
        return False
    return any(fnmatch.fnmatch(eid, pattern.lower()) for pattern in FAL_DENY_ENDPOINTS)


# dual-style(실사+애니) — 화풍을 고르지 않고 항상 실사 영상을 먼저 만든 뒤 그 실사 영상을
# 애니(웹툰풍)로 변환해 두 결과를 함께 본다(설계자 2026-10-20). 기본 false(운영 config 는 true).
# FAL_KEY 가 없으면 변환은 건너뛰고 실사만 진행한다(애니 결과에 "변환 키 없음").
VPOC_DUAL_STYLE_ENABLED = _flag("VPOC_DUAL_STYLE_ENABLED", "false")
# 실사 → 애니(웹툰풍) 변환 모델(실측 2026-10-20). fal video-to-video edit.
# 입력: prompt(@Video1 · @Image1..) · video_url(mp4, 3~15초, 높이 ≥720) · image_urls · keep_audio.
VPOC_ANIME_MODEL = os.getenv(
    "VPOC_ANIME_MODEL", "fal-ai/kling-video/o3/standard/video-to-video/edit"
)
# 변환 1초당 USD(실측 2026-10-20: $0.14/초).
VPOC_ANIME_USD_PER_S = _float("VPOC_ANIME_USD_PER_S", 0.14)


def is_fal(model: str) -> bool:
    """fal 모델 ID("fal:<endpoint_id>") 인지."""
    return str(model or "").startswith(FAL_ID_PREFIX)


def fal_endpoint_id(model: str) -> str:
    """fal 모델 ID 에서 endpoint_id 만 떼어 낸다. fal 이 아니면 빈 문자열."""
    return str(model or "")[len(FAL_ID_PREFIX):] if is_fal(model) else ""


# fal Seedance 계열(073 D2, R2): ② 목록에는 "얼굴"(image-to-video) 하나만 보이고, 제출 때 샷 방식에 따라
# 얼굴(i2v) 또는 "짝"(reference-to-video)으로 보낸다(fal.route_model). 형식 `얼굴=짝[,얼굴=짝…]`
# (endpoint id, `fal:` 없이). 빈 문자열이면 {} — 계열 라우팅을 끄고 070 동작(i2v 하나)으로 돌아간다.
# 형식이 틀린 항목(= 없음, 한쪽이 빔, 공백 포함, `fal:` 접두, 얼굴=짝 같음)은 버린다.
FAL_SEEDANCE_FAMILY_DEFAULT = "bytedance/seedance-2.5/image-to-video=bytedance/seedance-2.5/reference-to-video"
FAL_SEEDANCE_LABEL = "Seedance 2.5 (fal)"
# fal Seedance 계열 샷·영상 1개 길이 상한(초, 073 D3·R3.2). caps 는 4~30초지만 BytePlus 와 같은 15초로 묶는다
# (30초는 단가·실패 위험이 크고 미검증). 계열이 아닌 fal 모델에는 쓰지 않는다(R14.3). 최소 1.
FAL_SEEDANCE_MAX_S = max(1, _int("VPOC_FAL_SEEDANCE_MAX_S", 15))
# fal 영상 입력(참조 영상) 길이 상한(초, 152). 출력 길이(caps.durations)와 따로다 — Wan 3.0 r2v 는 출력 30초까지
# 만들지만 입력 영상은 15초까지만 받는다("Video duration exceeds the maximum allowed. Maximum is 15 seconds").
# caps 에 값이 없을 때 영상 입력 필드(videoField·videoListField)가 있는 fal 모델에 쓴다. 최소 1.
FAL_REF_VIDEO_MAX_S = max(1, _int("VPOC_FAL_REF_VIDEO_MAX_S", 15))


def _parse_fal_family(raw: str) -> dict[str, str]:
    """`얼굴=짝[,얼굴=짝…]` → {얼굴 endpoint: 짝 endpoint}. 틀린 항목은 버린다."""
    family: dict[str, str] = {}
    for item in str(raw or "").split(","):
        face, sep, partner = item.strip().partition("=")
        face, partner = face.strip(), partner.strip()
        if not sep or not face or not partner or face == partner:
            continue
        if any(ch.isspace() for ch in face + partner) or "=" in partner:
            continue
        if face.startswith(FAL_ID_PREFIX) or partner.startswith(FAL_ID_PREFIX):
            continue
        family[face] = partner
    return family


FAL_SEEDANCE_FAMILY: dict[str, str] = _parse_fal_family(
    os.getenv("VPOC_FAL_SEEDANCE_FAMILY", FAL_SEEDANCE_FAMILY_DEFAULT)
)


def fal_family_partner(model: str) -> str:
    """계열 얼굴 모델("fal:<i2v>")이면 짝 모델 id("fal:<r2v>"), 아니면 빈 문자열."""
    partner = FAL_SEEDANCE_FAMILY.get(fal_endpoint_id(model), "")
    return (FAL_ID_PREFIX + partner) if partner else ""


def is_fal_seedance_family(model: str) -> bool:
    """fal Seedance 계열(얼굴 또는 짝 endpoint)인지. fal 이 아니거나 계열 밖이면 False."""
    eid = fal_endpoint_id(model)
    if not eid:
        return False
    return eid in FAL_SEEDANCE_FAMILY or eid in FAL_SEEDANCE_FAMILY.values()


# fal 프롬프트 상한(UTF-8 바이트) 덮어쓰기(073 D5, R5.1). 형식 `글롭=바이트[,…]`(endpoint id, 소문자 fnmatch).
# 기본은 Seedance 2.5 계열을 BytePlus 와 같은 4,000 으로 둔다(fal 스키마에는 maxLength 가 없어 2,500 으로 잘렸다).
# 빈 문자열이면 덮어쓰기 없음(caps promptMaxLength · 2,500). 양의 정수가 아니거나 형식이 틀린 항목은 버린다.
FAL_PROMPT_MAX_OVERRIDES_DEFAULT = "bytedance/seedance-2.5/*=4000"
# fal 기본 상한(fal.DEFAULT_PROMPT_MAX 와 같은 값. 순환 import 를 피하려 여기 따로 둔다).
FAL_PROMPT_MAX_FALLBACK = 2500


def _parse_prompt_max_overrides(raw: str) -> tuple[tuple[str, int], ...]:
    """`글롭=바이트[,…]` → ((소문자 글롭, 바이트), …). 순서를 지킨다(첫 번째로 맞는 값을 쓴다)."""
    out: list[tuple[str, int]] = []
    for item in str(raw or "").split(","):
        pattern, sep, value = item.strip().partition("=")
        pattern, value = pattern.strip(), value.strip()
        if not sep or not pattern or not value or any(ch.isspace() for ch in pattern):
            continue
        try:
            limit = int(value)
        except ValueError:
            continue
        if limit <= 0:
            continue
        out.append((pattern.lower(), limit))
    return tuple(out)


FAL_PROMPT_MAX_OVERRIDES: tuple[tuple[str, int], ...] = _parse_prompt_max_overrides(
    os.getenv("VPOC_FAL_PROMPT_MAX_OVERRIDES", FAL_PROMPT_MAX_OVERRIDES_DEFAULT)
)


def fal_prompt_max(endpoint: str, default: object = None) -> int:
    """이 fal endpoint 의 프롬프트 상한(바이트, 073 R5.1).

    FAL_PROMPT_MAX_OVERRIDES 의 글롭 중 첫 번째로 맞는 값. 없으면 default(caps promptMaxLength),
    default 가 양의 정수가 아니면 2,500. endpoint 는 endpoint id 또는 "fal:<endpoint id>" 둘 다 받는다.
    """
    import fnmatch

    eid = str(endpoint or "").strip()
    if eid.startswith(FAL_ID_PREFIX):
        eid = eid[len(FAL_ID_PREFIX):]
    eid = eid.lower()
    if eid:
        for pattern, limit in FAL_PROMPT_MAX_OVERRIDES:
            if fnmatch.fnmatch(eid, pattern):
                return limit
    try:
        value = int(default)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else FAL_PROMPT_MAX_FALLBACK


# fal Seedance 계열 r2v 프롬프트의 이미지 지칭 방식(073 R5.3·R5.7). "at" = @Image1…, "plain" = Image 1….
# 앞뒤 공백을 지우고 소문자로 읽는다. 빈 값·틀린 값은 "at". 유료 검증 V-2 가 실패하면 config 만 plain 으로 바꾼다.
FAL_TAG_STYLES = ("at", "plain")


def _parse_tag_style(raw: object) -> str:
    value = str(raw or "").strip().lower()
    return value if value in FAL_TAG_STYLES else "at"


FAL_SEEDANCE_TAG_STYLE = _parse_tag_style(os.getenv("VPOC_FAL_SEEDANCE_TAG_STYLE", "at"))

# ③ 영상 설정 — 사용자가 화질과 샷 길이를 고른다(설계자 2026-10-07).
# 샷 길이 상한: Seedance 2.0 15초 · 2.5 15초 · Luma Ray2 5초. 하한은 4초.
# 2.0 은 처음에 30초로 두었으나 BytePlus 가 16초 이상을 "duration not valid"(t2v · r2v 모두)로
# 거절했다(운영 2026-10-13, 20초 한 번에 이어 만들기 실패 → 실측 4~15초 통과, 16·20·30초 거절).
VIDEO_RESOLUTIONS = {
    "luma-ray2": ["540p", "720p"],
    "seedance-2-0": ["480p", "720p", "1080p"],
    "seedance-2-5": ["480p", "720p"],
}
VIDEO_DURATION_RANGE = {
    "luma-ray2": (5, 5),
    "seedance-2-0": (4, 15),
    "seedance-2-5": (4, 15),
}

RESOLUTION = os.getenv("VPOC_RESOLUTION", "540p")
DURATION = os.getenv("VPOC_DURATION", "5s")
ASPECT = os.getenv("VPOC_ASPECT", "16:9")
DURATION_S = _int("VPOC_DURATION_S", 5)

# 실측(2026-09-23 C8): "On-demand model inference concurrent requests for Luma Ray V2" = 1.
# 쿼터가 1이라 동시 제출도 1이다. 2 이상을 넣으면 ThrottlingException 이 난다.
MAX_INFLIGHT = max(1, _int("VPOC_MAX_INFLIGHT", 1))
# Seedance(BytePlus) 동시 제출 수. 설계자 2026-10-13: 샷은 1번부터 **하나씩 순서대로** 만든다
# (동시 생성 아님). 값을 올리면 동시에 보내고, 한도에 걸리면(HTTP429) 대기열로 돌린다.
ARK_MAX_INFLIGHT = max(1, _int("VPOC_ARK_MAX_INFLIGHT", 1))
MAX_SHOTS = _int("VPOC_MAX_SHOTS", 6)
MIN_SHOTS = 3

# 비용 상한은 없다(2026-09-23 사용자 결정). 기록만 하고 제출을 막지 않는다.
# 여기에 상한 상수를 다시 넣지 않는다 — 넣으면 ledger 에 검사가 따라 들어온다.
VIDEO_USD_PER_SEC = {"540p": 0.75, "720p": 1.5}
IMAGE_USD = _float("VPOC_IMAGE_USD", 0.08)
TEXT_USD_PER_MTOK_IN = _float("VPOC_TEXT_USD_PER_MTOK_IN", 3.0)
TEXT_USD_PER_MTOK_OUT = _float("VPOC_TEXT_USD_PER_MTOK_OUT", 15.0)
# Opus 4.5 이후 단가(USD / 1M 토큰). 관리자 "모델 관리" 비용에 모델별로 계상한다.
OPUS_USD_PER_MTOK_IN = _float("VPOC_OPUS_USD_PER_MTOK_IN", 5.0)
OPUS_USD_PER_MTOK_OUT = _float("VPOC_OPUS_USD_PER_MTOK_OUT", 25.0)

# 업로드
MAX_UPLOAD_BYTES = _int("VPOC_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
MAX_ANALYSIS_IMAGES = 12
TILE_HEIGHT = 1568

# 모델 호출 시간 제한
READ_TIMEOUT_S = _int("VPOC_READ_TIMEOUT_S", 300)
# 분석이 모델을 바꿔 가며 다시 부를 수 있어(최대 수 분) 10분은 짧다(2026-10-13).
# 끊긴 작업을 "다시 실행" 으로 돌리는 시간. 가장 긴 작업(분석 재시도 · 콘티+러프)도 10분 안쪽이다.
TASK_STALL_S = _int("VPOC_TASK_STALL_S", 900)
# 완료를 빨리 알아채도록 5초(설계자 2026-10-13 "샷 1개 시간 줄이기"). 조회 자체는 무료다.
POLL_MIN_INTERVAL_S = 5
# 073 R15.6(작업 7): 결과 조회가 연속 POLL_GIVEUP_ERRORS 회 실패하고 제출 뒤 POLL_GIVEUP_S 초가 지나면
# 샷을 failed(PollGiveUp)로 끝낸다. 공급자 대기열이 길어지면 config 로 늘린다. 최소 60초(0 이면 곧바로 포기).
POLL_GIVEUP_S = max(60, _int("VPOC_POLL_GIVEUP_S", 1800))
POLL_GIVEUP_ERRORS = 5


def ark_usd_per_sec(model: str, resolution: str = "") -> float | None:
    """그 해상도(기본 ARK_RESOLUTION)의 생성 1초 단가. 모르면 None("단가 미정")."""
    raw = str(ARK_USD_PER_SEC.get(model) or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    return (ARK_USD_PER_SEC_TABLE.get(model) or {}).get((resolution or ARK_RESOLUTION).lower())


def default_resolution(model: str) -> str:
    """고르기 전의 화질. 예전 계획(설정 없음)은 이 값으로 읽힌다."""
    if model in ARK_MODEL_IDS:
        return ARK_RESOLUTION
    return RESOLUTION


# fal 토큰 단가에 쓰는 해상도 → 픽셀(가로×세로). 모르면 720p.
FAL_RESOLUTION_PX = {
    "480p": (854, 480),
    "540p": (960, 540),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}
# Seedance 토큰 공식의 초당 프레임(fal/BytePlus 기준 24fps).
FAL_TOKEN_FPS = 24
# 토큰 공식: tokens = (가로 × 세로 × fps × 초) / 1024.
FAL_TOKEN_DIVISOR = 1024
# unit 에 "tokens" 가 붙으면 그 단위 수(1000·1M·1)로 금액을 환산한다.
FAL_TOKEN_UNIT_MULTIPLIERS = {"1m tokens": 1_000_000, "1000 tokens": 1000, "tokens": 1}
# fal 단가 unit 이 "영상 1건"(초·토큰이 아닌 1회 가격)으로 볼 단위들.
FAL_PER_ITEM_UNITS = {
    "videos",
    "video",
    "generations",
    "generation",
    "units",
    "requests",
    "images",
}


def _fal_resolution_px(resolution: str) -> tuple[int, int]:
    """fal 토큰 단가용 해상도 → (가로, 세로). 모르면 720p(1280×720)."""
    return FAL_RESOLUTION_PX.get((resolution or "").lower(), FAL_RESOLUTION_PX["720p"])


def _fal_token_multiplier(unit: str) -> int | None:
    """unit 이 "tokens" 를 (한 토막으로) 포함하면 단위 수(1000·1000000·1)를, 아니면 None.

    예: "1000 tokens"→1000, "1M tokens"→1000000, "tokens"→1. "credits" 등은 None.
    """
    low = (unit or "").lower().strip()
    if "tokens" not in low.split():
        return None
    if low in FAL_TOKEN_UNIT_MULTIPLIERS:
        return FAL_TOKEN_UNIT_MULTIPLIERS[low]
    # "1000 tokens"·"1M tokens" 외 형태라도 앞 수를 읽어 본다.
    head = low.replace("tokens", "").strip()
    if not head:
        return 1
    if head in ("1m", "1 m"):
        return 1_000_000
    try:
        return int(float(head))
    except ValueError:
        return None


def shot_usd(model: str = "", resolution: str = "", seconds: float | None = None) -> float:
    """샷 1개 금액.

    - fal 모델("fal:<endpoint>")은 fal 단가(pricing)로 —
      · unit 이 "seconds" 면 unit_price×seconds,
      · unit 이 "tokens" 를 포함하면("1000 tokens"·"1M tokens"·"tokens") 단위 수(1000·1000000·1)를
        읽어 토큰 공식으로 계산한다. tokens = (가로 × 세로 × fps(24) × 초) / 1024,
        금액 = unit_price × tokens / 단위 수. 해상도는 FAL_RESOLUTION_PX(모르면 720p).
      · unit 이 영상 1건 단위(videos·video·generations·generation·units·requests·images)면 unit_price(1회).
      · 그 밖(모르는 단위)은 0(단가 미정) — 모르는 단위를 1건 가격으로 보지 않는다(설계자 2026-10-20).
      단가 자체를 모르면 0. Luma 표로 떨어지지 않게 한다. 순환 import 를 피하려고 fal 을 함수 안에서 늦게 import 한다.
    - Seedance(ARK_MODEL_IDS)는 그 해상도의 생성 1초 단가 × 초. 단가가 없으면 0(단가 미정).
    - 그 밖(Luma)은 VIDEO_USD_PER_SEC 표.

    인자를 비우면 예전과 같은 값(Luma · VPOC_RESOLUTION · DURATION_S)이다.
    """
    secs = DURATION_S if seconds is None else float(seconds)
    if is_fal(model):
        from . import fal  # noqa: PLC0415 — 순환 import 를 피하려 늦게 부른다.

        price = fal.pricing_for(model)
        if not price:
            return 0.0
        unit_price = float(price.get("unitPrice") or 0.0)
        unit = str(price.get("unit") or "").lower().strip()
        if unit == "seconds":
            return round(unit_price * secs, 4)
        multiplier = _fal_token_multiplier(unit)
        if multiplier is not None:
            w, h = _fal_resolution_px(resolution)
            tokens = (w * h * FAL_TOKEN_FPS * secs) / FAL_TOKEN_DIVISOR
            return round(unit_price * tokens / multiplier, 4)
        if unit in FAL_PER_ITEM_UNITS:
            return round(unit_price, 4)
        return 0.0
    if model in ARK_MODEL_IDS:
        per_sec = ark_usd_per_sec(model, resolution)
        return round((per_sec or 0.0) * secs, 4)
    return round(VIDEO_USD_PER_SEC.get(resolution or RESOLUTION, 0.75) * secs, 4)


def anime_usd(seconds: float | None = None) -> float:
    """실사 → 애니 변환 1건 금액 = 초 × VPOC_ANIME_USD_PER_S(설계자 2026-10-20).

    초를 비우면 기본 영상 길이(DURATION_S)로 계산한다.
    """
    secs = DURATION_S if seconds is None else float(seconds)
    return round(VPOC_ANIME_USD_PER_S * secs, 4)


def single_keyframe_count(total_s: int) -> int:
    """영상 1개(single) 방식의 키 장면 수(설계자 2026-10-19).

    clamp(ceil(초 / KEYFRAME_INTERVAL_S) + 1, SINGLE_MIN_KEYFRAMES, SINGLE_MAX_KEYFRAMES).
    """
    import math

    seconds = max(1, int(total_s or 1))
    raw = math.ceil(seconds / max(1, KEYFRAME_INTERVAL_S)) + 1
    return max(SINGLE_MIN_KEYFRAMES, min(SINGLE_MAX_KEYFRAMES, raw))


def single_interval_s(total_s: int) -> float:
    """키 장면 사이 간격(초, 실수). 간격 = 초 / (키 장면 수 - 1)."""
    count = single_keyframe_count(total_s)
    return float(total_s) / max(1, count - 1)


def single_default_duration(low: int, high: int) -> int:
    """고르기 전/범위 밖 값일 때 쓸 길이. min(SINGLE_DURATION_DEFAULT, 최대)이되 최소 이상."""
    return max(int(low), min(int(SINGLE_DURATION_DEFAULT), int(high)))
