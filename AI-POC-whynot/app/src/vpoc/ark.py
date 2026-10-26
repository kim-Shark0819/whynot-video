"""vpoc1 BytePlus ModelArk 영상 경로 — ② 영상 모델 고르기(설계자 2026-10-07, dev 만).

video.py 의 Luma 경로와 같은 모양(submit → poll → 출력 mp4)을 돌려준다. routes 는 작업 식별자의
접두사(`ark/tasks/`)로 어느 경로인지 가른다.

보안(설계자 2026-10-07): BytePlus 쪽에 이미지를 남기지 않는다.
- 첫 프레임은 **AI 가 만든 캐릭터 이미지만**, URL 이 아니라 요청 본문의 base64 로 보낸다.
  업로드 원본은 routes 가 애초에 키프레임으로 고르지 않는다(source == "ai" 만).
- 결과 영상 URL 은 24시간 뒤 만료된다. 완료를 본 즉시 우리 영상 버킷으로 옮긴다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import settings
from . import fal
from .errors import VpocError, now_iso
from .store import STORE, video_output_prefix

log = logging.getLogger("vpoc")

ARN_PREFIX = "ark/tasks/"
# 한 번에 만들기(A안)는 샷 문장을 모두 합치므로 샷 하나보다 길다. BytePlus 상한은 확인하지 못했다 —
# 거절되면 오류 원문이 ④ 에 보이고, 그때 이 값을 고친다.
MAX_PROMPT_CHARS = 4000

# 화면 표시 순서. Luma 는 video.py 가 맡고 여기서는 이름만 같이 둔다.
LABELS = {
    "seedance-2-0": "Seedance 2.0",
    "seedance-2-5": "Seedance 2.5",
}

# Luma(GetAsyncInvoke) 상태 이름으로 맞춘다 — routes 의 조회 분기를 그대로 쓰기 위해서다.
_STATUS = {
    "queued": "InProgress",
    "running": "InProgress",
    "succeeded": "Completed",
    "failed": "Failed",
    "cancelled": "Failed",
    "expired": "Failed",
}


def is_ark(model: str) -> bool:
    # fal 모델(등록된 것)도 Seedance 와 같은 외부 공급자 경로를 탄다(설계자 2026-10-18).
    return model in settings.ARK_MODEL_IDS or (settings.is_fal(model) and fal.is_registered(model))


def is_ark_job(invocation_arn: str) -> bool:
    # 기존 Seedance(`ark/tasks/`) 또는 fal 작업(`fal/requests/`).
    return str(invocation_arn or "").startswith(ARN_PREFIX) or fal.is_fal_job(invocation_arn)


def is_external(model: str) -> bool:
    """외부 공급자(BytePlus·fal) 경로인지. 등록 여부와 상관없다(073 R11.1·R11.4, D11).

    is_ark 는 미등록 fal 을 False 로 보아 제출이 Luma 로 새고 원장이 0달러가 됐다(리뷰 O-6).
    제출(submit_shot)·동시 제출 한도(refresh_shots)는 이 함수로 가르고, 미등록 fal 은 submit_shot 이 E5 로 막는다.
    """
    return model in settings.ARK_MODEL_IDS or settings.is_fal(model)


def model_id(model: str) -> str:
    return str(settings.ARK_MODEL_IDS.get(model) or "").strip()


def label(model: str) -> str:
    """화면에 보일 이름. fal 은 등록 label(`fal · <display_name>`), 나머지는 LABELS.

    fal Seedance 계열 얼굴(i2v)은 짝(r2v)과 묶어 하나로 보이므로 settings.FAL_SEEDANCE_LABEL(073 R2.1).
    """
    if settings.is_fal(model):
        if settings.fal_family_partner(model):
            return settings.FAL_SEEDANCE_LABEL
        item = fal.find(model)
        return ("fal · " + str(item.get("label"))) if item else model
    return LABELS.get(model, model)


def unavailable_reason(model: str) -> str:
    """고를 수 없으면 그 이유(화면에 그대로 보인다). 고를 수 있으면 빈 문자열."""
    if settings.is_fal(model):
        return fal.unavailable_reason(model)
    # 기존(BytePlus ARK 직결) Seedance 는 꺼져 있으면 고를 수 없다(설계자 2026-10-26).
    if model in settings.ARK_MODEL_IDS and not settings.ARK_SEEDANCE_ENABLED:
        return "기존 Seedance 는 사용 중지 — fal Seedance 를 쓰세요"
    if not model_id(model):
        return "모델 ID 미설정"
    if not settings.ARK_API_KEY:
        return "API 키 미설정"
    return ""


def _submit_block_reason(model: str) -> str:
    """제출을 막는 이유. 없으면 빈 문자열(073 D1, R1.1·R1.5).

    unavailable_reason 에서 플래그 게이트(VPOC_ARK_SEEDANCE_ENABLED)만 뺀 것이다. 새 선택·새 생성은
    unavailable_reason(게이트 포함)을 그대로 쓰고, 이미 ARK 로 승인된 회차(steps.4.videoModel)의
    제출·재제출·재생성은 플래그와 상관없이 BytePlus 로 끝낸다. 모델 ID·키 검사는 같다.
    """
    if settings.is_fal(model):
        return fal.unavailable_reason(model)
    if not model_id(model):
        return "모델 ID 미설정"
    if not settings.ARK_API_KEY:
        return "API 키 미설정"
    return ""


DEFAULT_MODEL = "luma-ray2"


def choices() -> list[dict]:
    """② 화면의 모델 목록. 고를 수 없는 모델도 이유와 함께 보인다.

    설계자 2026-10-19: 영상 1개(single)의 '영상 길이' 선택지를 즉시 바꿀 수 있게 모델마다
    durationChoices · minDurationS · maxDurationS 를 함께 준다(화면이 모델을 바꾸면 그 범위로 다시 그린다).
    """
    def _dur(model_id_value: str) -> dict:
        opts = video_options(model_id_value)
        return {
            "durationChoices": opts.get("durationChoices") or [],
            "minDurationS": opts.get("minDurationS"),
            "maxDurationS": opts.get("maxDurationS"),
            "durationFixed": opts.get("durationFixed"),
        }

    luma_usd = settings.shot_usd(DEFAULT_MODEL)
    items = [
        {
            "id": DEFAULT_MODEL,
            "label": "Luma Ray2",
            "available": True,
            "reason": "",
            "perShotUsd": luma_usd,
            "priceKnown": True,
            "provider": "bedrock",
            **_dur(DEFAULT_MODEL),
        }
    ]
    for key, label_text in LABELS.items():
        # 기존(BytePlus ARK 직결) Seedance 가 꺼져 있으면 ② 목록에 넣지 않는다(설계자 2026-10-26).
        # 켜져 있으면 예전과 동일(provider byteplus 2개 포함).
        if not settings.ARK_SEEDANCE_ENABLED:
            continue
        reason = unavailable_reason(key)
        items.append(
            {
                "id": key,
                "label": label_text,
                "available": not reason,
                "reason": reason,
                "perShotUsd": settings.shot_usd(key),
                "priceKnown": settings.ark_usd_per_sec(key) is not None,
                "provider": "byteplus",
                **_dur(key),
            }
        )
    # 등록된 fal 영상 모델(text-to-video · image-to-video)을 Seedance 뒤에 더한다(설계자 2026-10-18).
    # fal 은 단가를 모르므로 금액 0 · priceKnown False.
    # 회귀 방지(설계자 2026-10-22, Claude 조사 2026-10-25): video-to-video·reference-to-video·
    # previzCapable(프리비즈 방식 전용) 모델은 옛 vpoc 피커에 나타나지 않게 거른다 — 이 화면은
    # 시작 이미지(i2v) 방식이라 영상 입력 모델을 못 쓴다.
    for item in fal.registered():
        category = str(item.get("category") or "")
        if category not in settings.FAL_VIDEO_CATEGORIES:
            continue
        if category in ("video-to-video", "reference-to-video"):
            continue
        if (item.get("caps") or {}).get("previzCapable"):
            continue
        model = str(item.get("id") or "")
        reason = unavailable_reason(model)
        # 이름(073 R2.1): 계열 얼굴은 label()의 "Seedance 2.5 (fal)", 나머지는 지금과 같은 `fal · <label>`.
        name = (
            label(model)
            if settings.fal_family_partner(model)
            else "fal · " + str(item.get("label") or item.get("endpointId") or model)
        )
        items.append(
            {
                "id": model,
                "label": name,
                "available": not reason,
                "reason": reason,
                "perShotUsd": 0,
                "priceKnown": False,
                "provider": "fal",
                **_dur(model),
            }
        )
    return items


def resolve_model(model: str) -> str:
    """저장/선택된 영상 모델을 실제로 쓸 모델로 바꾼다(설계자 2026-10-26).

    기존(BytePlus ARK 직결) Seedance 가 꺼져 있고(기본), 저장된 모델이 ARK Seedance
    (`seedance-2-0`/`seedance-2-5`)면 settings.ARK_SEEDANCE_FAL_MAP 으로 fal Seedance 로 바꾼다 —
    **그 fal 모델이 등록·사용 가능할 때만**. 아니면 기본(Luma). 그 밖의 모델(이미 fal·Luma 등)과
    ARK Seedance 가 켜져 있을 때는 그대로 둔다(회귀 없음).
    """
    if settings.ARK_SEEDANCE_ENABLED:
        return model
    if model not in settings.ARK_MODEL_IDS:
        return model
    target = str(settings.ARK_SEEDANCE_FAL_MAP.get(model) or "")
    if target and fal.is_registered(target) and not fal.unavailable_reason(target):
        _log_resolved(model, target, "ark_disabled")
        return target
    _log_resolved(model, DEFAULT_MODEL, "fal_target_unavailable")
    return DEFAULT_MODEL


# 모델 치환 로그 중복 억제(073 R12.1, D12). plan_view 조회(폴링)마다 resolve_model·selected 가 불리므로
# 같은 (from, to, reason) 은 프로세스당 10분에 한 번만 남긴다. 키는 그 조합 수만큼만 생긴다.
_RESOLVED_LOG_INTERVAL_S = 600
_resolved_logged: dict[tuple[str, str, str], float] = {}
_resolved_lock = threading.Lock()


def _log_resolved(src: str, dst: str, reason: str) -> None:
    """`vpoc.model.resolved from= to= reason=` info 로그(같은 조합은 10분에 한 번). id 만 남긴다."""
    key = (str(src), str(dst), str(reason))
    now = time.monotonic()
    with _resolved_lock:
        last = _resolved_logged.get(key)
        if last is not None and now - last < _RESOLVED_LOG_INTERVAL_S:
            return
        _resolved_logged[key] = now
    log.info("vpoc.model.resolved from=%s to=%s reason=%s", key[0], key[1], key[2])


def selected(state: dict) -> str:
    """이 계획이 쓸 모델. 고르기가 꺼져 있거나 고를 수 없는 값이면 Luma.

    저장된 값은 resolve_model 을 거친다 — 기존 ARK Seedance 가 꺼져 있으면 fal Seedance(등록 시)
    또는 Luma 로 바뀐다(설계자 2026-10-26).
    """
    if not settings.MODEL_PICKER_ENABLED:
        return DEFAULT_MODEL
    model = resolve_model(
        str((state.get("steps", {}).get("2") or {}).get("videoModel") or DEFAULT_MODEL)
    )
    if model == DEFAULT_MODEL or (is_ark(model) and not unavailable_reason(model)):
        return model
    # 저장값(또는 resolve 결과)을 쓸 수 없어 Luma 로 돌아간다(073 R12.2). 화면에는 videoModelRequested 로 보인다.
    _log_resolved(model, DEFAULT_MODEL, "unavailable")
    return DEFAULT_MODEL


def plan_model(step2: dict) -> str:
    """③ 준비(prepare)가 길이·방식을 정할 모델(073 R3.7). ② 저장값을 resolve_model 로 바꾼 것.

    기존 ARK Seedance 저장값이고 플래그가 꺼져 있으면 fal 얼굴(등록 시) 또는 Luma 범위로 single 길이를 정한다.
    """
    return resolve_model(str((step2 or {}).get("videoModel") or ""))


def duration_range(model: str) -> tuple[int, int]:
    """이 모델이 한 번에 만들 수 있는 길이(초) (최소, 최대) (073 R3.1-3.2, R14.3).

    - fal Seedance 계열(얼굴·짝): caps durations(없으면 [5])의 최소~최대. 최대는 FAL_SEEDANCE_MAX_S 로 묶되
      최소보다 작아지지 않는다.
    - 그 밖(ARK·Luma·계열이 아닌 fal): VIDEO_DURATION_RANGE, 없으면 (DURATION_S, DURATION_S) — 073 이전
      _shot_seconds 와 같은 값이다(계열이 아닌 fal 은 (5, 5)).
    """
    if settings.is_fal_seedance_family(model):
        caps = (fal.find(model) or {}).get("caps") or {}
        durations = sorted({int(value) for value in (caps.get("durations") or [5])})
        low, high = durations[0], durations[-1]
        return low, max(low, min(high, settings.FAL_SEEDANCE_MAX_S))
    low, high = settings.VIDEO_DURATION_RANGE.get(model, (settings.DURATION_S, settings.DURATION_S))
    return int(low), int(high)


def snap_seconds(model: str, requested: object) -> int:
    """샷 길이(초)를 이 모델이 실제로 만드는 값으로 맞춘다(073 R3.3, X5). 화면·generate·제출이 같이 쓴다.

    - 요청값이 비거나 숫자가 아니면 DURATION_S 로 본다.
    - fal Seedance 계열: 범위(duration_range) 안의 caps 값 중 요청 이상인 가장 작은 값, 없으면 범위 안 최댓값
      (fal._pick_duration 과 같은 쪽으로 반올림해 만든 길이와 계상 길이가 같다).
    - 그 밖(계열이 아닌 fal 포함): 범위로 자르기(073 이전 _shot_seconds 와 같다. 계열이 아닌 fal 은 늘 5).
    """
    try:
        seconds = int(requested or settings.DURATION_S)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        seconds = settings.DURATION_S
    low, high = duration_range(model)
    if settings.is_fal_seedance_family(model):
        caps = (fal.find(model) or {}).get("caps") or {}
        values = sorted(
            {int(value) for value in (caps.get("durations") or [5]) if low <= int(value) <= high}
        ) or [low]
        return next((value for value in values if value >= seconds), values[-1])
    return max(low, min(high, seconds))


def one_take_max_s(model: str) -> int:
    """한 번에 만들기(A안) 길이 상한(초, 073 R3.4, R14.3).

    ARK 와 fal Seedance 계열은 duration_range 최대. 그 밖은 073 이전 값 VIDEO_DURATION_RANGE.get(model, (5, 5)).
    """
    if model in settings.ARK_MODEL_IDS or settings.is_fal_seedance_family(model):
        return duration_range(model)[1]
    return int(settings.VIDEO_DURATION_RANGE.get(model, (5, 5))[1])


def submit_resolution(model: str, stored: object) -> tuple[str, str]:
    """제출 때 보낼 화질과 보정 전 값 (value, adjusted_from) (073 R4.3-4.4, R14.3).

    - fal Seedance 계열: 저장값이 caps 화질에 있으면 그대로. 없으면(070 이전 회차의 540p 등) 승인 금액을 넘지
      않게 고른다(073 fix1): caps 화질 중 픽셀 수(settings.FAL_RESOLUTION_PX)가 저장 화질 이하인 것 가운데 가장
      큰 값(540p → 480p), 그런 값이 없거나 저장 화질의 픽셀 수를 모르면 caps 의 가장 작은 화질. 저장값이 비면
      저장 화질을 settings.default_resolution(model) 로 보고 같은 규칙을 쓴다. 바꿨으면 adjusted_from 은 그 저장
      화질(빈 값이면 default_resolution 값)이다. 늘 caps 값을 명시해 보낸다.
    - 그 밖(계열이 아닌 fal 포함): (저장값 또는 default_resolution, "") — 073 이전과 같다. 계열이 아닌 fal 은
      build_payload 가 enum 에 있을 때만 resolution 을 넣는다.
    """
    value = str(stored or "")
    if settings.is_fal_seedance_family(model):
        resolutions = [str(item) for item in video_options(model)["resolutions"]]
        base = value or settings.default_resolution(model)
        if base in resolutions:
            return base, ""

        def pixels(resolution: str) -> int | None:
            size = settings.FAL_RESOLUTION_PX.get(str(resolution or "").lower())
            return size[0] * size[1] if size else None

        known = [(pixels(item), item) for item in resolutions if pixels(item) is not None]
        limit = pixels(base)
        if not known:
            # caps 화질의 픽셀 수를 하나도 모른다(이론상) — 첫 값을 보낸다.
            return resolutions[0], base
        fitting = [entry for entry in known if limit is not None and entry[0] <= limit]
        chosen = max(fitting)[1] if fitting else min(known)[1]
        return chosen, base
    return value or settings.default_resolution(model), ""


def video_options(model: str) -> dict:
    """③ 영상 설정에서 고를 수 있는 화질 · 샷 길이(초).

    설계자 2026-10-19: 영상 1개(single)의 '영상 길이' 선택지를 durationChoices 로 함께 준다.
    fal 처럼 정해진 값 목록이면 그 값들, 연속 범위(Seedance)면 최소~최대 정수 초.
    """
    if settings.is_fal(model):
        # fal 은 caps 의 durations 최소/최대 · resolutions(없으면 ["720p"]) 를 쓴다.
        item = fal.find(model) or {}
        caps = item.get("caps") or {}
        durations = sorted({int(value) for value in (caps.get("durations") or [5])})
        low, high = durations[0], durations[-1]
        resolutions = [str(value) for value in (caps.get("resolutions") or [])] or ["720p"]
        default_resolution = resolutions[0]
        if settings.is_fal_seedance_family(model):
            # fal Seedance 계열(073 R3.2·R4.1): 길이는 duration_range(상한 FAL_SEEDANCE_MAX_S) 안의 caps 값,
            # 기본 화질은 720p(있으면). 계열이 아닌 fal 은 아래 073 이전 값 그대로(R14.3).
            low, high = duration_range(model)
            durations = [value for value in durations if low <= value <= high] or [low]
            low, high = durations[0], durations[-1]
            default_resolution = "720p" if "720p" in resolutions else resolutions[0]
        return {
            "model": model,
            "resolutions": resolutions,
            "defaultResolution": default_resolution,
            "minDurationS": low,
            "maxDurationS": high,
            "durationFixed": low == high,
            # fal 은 정해진 값 목록 그대로 선택지로 준다.
            "durationChoices": durations,
            # 참고 이미지(캐릭터)를 보낼 수 있으면 True — referenceImagesField 또는 startImageField.
            "referenceImages": bool(caps.get("referenceImagesField") or caps.get("startImageField")),
        }
    low, high = settings.VIDEO_DURATION_RANGE.get(model, (settings.DURATION_S, settings.DURATION_S))
    return {
        "model": model,
        "resolutions": list(settings.VIDEO_RESOLUTIONS.get(model) or [settings.RESOLUTION]),
        "defaultResolution": settings.default_resolution(model),
        "minDurationS": low,
        "maxDurationS": high,
        "durationFixed": low == high,
        # 연속 범위는 최소~최대 정수 초를 선택지로 준다.
        "durationChoices": list(range(int(low), int(high) + 1)),
        # Seedance 는 샷의 캐릭터 이미지를 참고 이미지로 함께 보낸다(캐릭터 그대로 유지).
        "referenceImages": is_ark(model),
    }


def tag_style(model: str) -> str:
    """프롬프트의 참고 이미지 지칭 방식(073 R5.3·R5.7). "at"(@Image1…) 또는 "plain"(Image 1…).

    fal Seedance 계열 얼굴이면 settings.FAL_SEEDANCE_TAG_STYLE(기본 "at"). 그 밖(ARK·Luma·계열이 아닌 fal)은
    설정과 상관없이 "plain"(073 이전과 같은 "Image N", R5.6·R14.1·R14.3).
    """
    if settings.fal_family_partner(model):
        return settings._parse_tag_style(settings.FAL_SEEDANCE_TAG_STYLE)
    return "plain"


def image_label(start: int, end: int, style: str = "plain") -> str:
    """참고 이미지 번호 지칭(073 R5.3). plain: "Image 3" / "Images 3-4". at: "@Image3" / "@Image3-@Image4".

    at 에서 3장 이상 이어지면 번호를 모두 적는다("@Image3, @Image4, @Image5") — 가운데 번호가 빠지면
    프롬프트 지칭 수가 실제 보낸 image_urls 수와 어긋난다(R5.3 "N = image_urls 수").
    """
    start, end = int(start), int(end)
    if style == "at":
        if end <= start:
            return f"@Image{start}"
        if end == start + 1:
            return f"@Image{start}-@Image{end}"
        return ", ".join(f"@Image{index}" for index in range(start, end + 1))
    return f"Image {start}" if end <= start else f"Images {start}-{end}"


def reference_prompt(
    prompt: str,
    names: list[str | None],
    *,
    style_refs: int = 0,
    style_lock: bool = False,
    tag_style: str = "plain",
) -> str:
    """참고 이미지가 있을 때 Seedance 에 보낼 최종 프롬프트.

    설계자 2026-10-07: 사용자가 올린 이미지(있으면) 또는 ③에서 승인한 AI 이미지 속 캐릭터가
    **그대로** 나와야 한다. 이미지 순서대로 "Image N" 이 누구인지 밝히고, 얼굴·머리·의상·체형을
    바꾸지 말라고 앞에 못박는다. 장면 묘사(prompt)는 그 뒤에 그대로 붙인다.

    설계자 2026-10-08: 첨부 이미지의 **화풍**도 그대로. 캐릭터 이미지 뒤에 화풍 참고 이미지
    style_refs 장이 붙는다(인물이 아니라 그림체 기준). style_lock 이면 고정 화풍 문장 대신
    "참고 이미지와 같은 화풍"을 지시한다 — 호출부가 prompt 에서 고정 화풍 문장을 미리 뗀다.

    설계자 2026-10-13: names 는 이미지 한 장마다 하나다. 같은 인물이 이어서 여러 장이면
    "Images 1-2 are X" 로 묶고, None 은 화풍 참고 이미지다(style_refs 를 쓰지 않는 새 호출 방식).

    073 R5.3: tag_style="at" 이면 지칭만 "@Image1"·"@Image1-@Image2"(image_label)로 쓴다. 다른 문장은 같다.
    지칭 문장은 늘 맨 앞이라 fal 이 뒤를 잘라도 남는다(R5.4).
    """
    names = list(names) + [None] * style_refs
    if not names and not style_lock:
        return prompt
    parts = []
    labels = []
    index = 1
    while index <= len(names):
        name = names[index - 1]
        end = index
        while end < len(names) and names[end] == name:
            end += 1
        span = image_label(index, end, tag_style)
        verb = "is" if end == index else "are"
        if name == PANEL_LABEL:
            what = "the webtoon panel" if end == index else "the webtoon panels"
            labels.append(
                f"{span} {verb} {what} this shot brings to life, in story order "
                "(follow their setting, art style and events)"
            )
        elif name is None:
            labels.append(f"{span} {verb} an art-style reference only (not a character)")
        elif end == index:
            labels.append(f"{span} is {name}")
        else:
            labels.append(f"{span} are all {name} (the same person from different views)")
        index = end + 1
    if labels:
        parts.append("; ".join(labels) + ".")
    if any(name is not None for name in names):
        parts.append(
            "Keep every character exactly as shown in their reference images: the same face shape, eyes "
            "and eye shape, eyebrows, nose, mouth, skin tone, hairstyle, hair color, outfit, accessories "
            "and body type, from the first frame to the last. Copy the faces from the close-up references. "
            "Do not redesign, beautify, restyle or swap any character."
        )
    if style_lock:
        parts.append(
            "Render the whole video in exactly the same art style as the reference images: the same line "
            "work, coloring, shading, color palette, rendering and level of detail, in every frame. Do not "
            "switch to a different style (no photorealism if the references are drawn, no cartoon look if "
            "they are photographs)."
        )
    parts.append(f"Scene: {prompt}")
    return " ".join(parts)


ONE_TAKE_ID = "take"
# reference_prompt 의 names 에서 "이 샷이 맡은 웹툰 장면" 이미지를 뜻하는 표식.
PANEL_LABEL = "#panel"


def strip_style(prompt: str, style_sentence: str) -> str:
    """샷 문장 맨 앞의 화풍 문장(모든 샷에 같은 문장)을 뗀다. 한 번에 만들기에서는 앞에 한 번만 둔다."""
    text = (prompt or "").strip()
    head = (style_sentence or "").strip().rstrip(".")
    if head and text.startswith(head):
        text = text[len(head):].lstrip(". ").strip()
    return text


def one_take_prompt(beats: list[tuple[int, str]], style_sentence: str = "") -> str:
    """A안(설계자 2026-10-08): ③ 의 샷들을 시간대별 지시 하나로 합쳐 Seedance 한 번에 이어서 만든다.

    beats = [(초, 샷 장면 문장)] (③ 순서). 캐릭터 유지 지시는 reference_prompt() 가 앞에 붙인다.
    """
    parts = []
    if style_sentence:
        parts.append(style_sentence.strip().rstrip(".") + ".")
    parts.append(
        "One continuous video in a single take. Move smoothly from each beat to the next with no jump "
        "cuts, keeping the same location, lighting, time of day, wardrobe and characters throughout."
    )
    start = 0
    for index, (seconds, text) in enumerate(beats, start=1):
        end = start + int(seconds)
        parts.append(f"Beat {index} ({start}-{end}s): {text.strip()}")
        start = end
    return " ".join(parts)


def single_take_prompt(beats: list[tuple[float, float, str]], style_sentence: str = "") -> str:
    """영상 1개 한 번에(single, 설계자 2026-10-19): ③ 키 장면/구간 문장을 시간 순서 하나로 합친다.

    beats = [(시작 초, 끝 초, 구간 움직임/장면 문장)] (③ 순서). one_take_prompt 와 같은 모양이되
    구간이 키 장면 사이 간격(실수)이라 시작·끝 초를 그대로 표기한다. 캐릭터 유지·참고 이미지 순서
    지시는 reference_prompt() · keyframe_order_prefix() 가 앞에 붙인다.
    """
    parts = []
    if style_sentence:
        parts.append(style_sentence.strip().rstrip(".") + ".")
    parts.append(
        "One continuous video in a single take. Move smoothly from each beat to the next with no jump "
        "cuts, keeping the same location, lighting, time of day, wardrobe and characters throughout."
    )
    for index, (start, end, text) in enumerate(beats, start=1):
        a = int(round(start))
        b = int(round(end))
        parts.append(f"Beat {index} ({a}-{b}s): {str(text).strip()}")
    return " ".join(parts)


def keyframe_order_prefix(times: list[float]) -> str:
    """완성 키 장면을 참고 이미지로 보낼 때(Seedance · fal reference), 어느 이미지가 몇 초 장면인지
    앞에 못박는다(설계자 2026-10-19). reference_prompt 의 "Image N" 과 같은 순서다.

    times = 키 장면의 초(③ 순서). 참고 이미지 맨 앞 len(times) 장이 이 키 장면들이라고 알린다.
    """
    if not times:
        return ""
    labels = []
    for index, seconds in enumerate(times, start=1):
        labels.append(f"Image {index} = the {int(round(seconds))}s scene")
    return (
        "The first reference images are keyframes of this one video in time order ("
        + ", ".join(labels)
        + "). Animate one continuous video that passes through these scenes in that order."
    )



def _call(method: str, path: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        settings.ARK_BASE_URL + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + settings.ARK_API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = (json.loads(raw).get("error") or {}).get("message") or raw
        except ValueError:
            detail = raw
        raise VpocError("ark." + method.lower(), f"HTTP{exc.code}", str(detail)[:500], 502) from exc


def submit(
    *,
    model: str,
    owner: str,
    project_id: str,
    plan_id: str,
    shot_id: str,
    attempt: int,
    prompt: str,
    keyframe_jpeg_b64: str = "",
    gen_id: str = "",
    reference_jpegs_b64: list[str] | None = None,
    resolution: str = "",
    duration_s: int | None = None,
    last_frame_jpeg_b64: str = "",
) -> dict:
    """작업을 만들고 즉시 돌아온다. video.submit 과 같은 키를 돌려준다.

    reference_jpegs_b64: 샷에 나오는 캐릭터 이미지(사용자 업로드 또는 ③에서 승인한 AI 이미지).
    `prompt` 는 reference_prompt() 로 "Image N = 누구" 지시를 붙인 최종 문장이어야 한다 — 순서가
    이미지 순서와 같아야 BytePlus 가 누가 누구인지 안다.
    """
    if settings.is_fal(model):
        # fal 모델은 fal.submit 으로 넘긴다(같은 반환 키 모양). 끝 프레임이 없는 모델은 첫 프레임만,
        # 이미지 입력이 전혀 없는 text-to-video 모델은 prompt 만 보내는 것은 fal.submit 이 caps 로 정한다.
        return fal.submit(
            model=model,
            owner=owner,
            project_id=project_id,
            plan_id=plan_id,
            shot_id=shot_id,
            attempt=attempt,
            prompt=prompt,
            keyframe_jpeg_b64=keyframe_jpeg_b64,
            gen_id=gen_id,
            reference_jpegs_b64=reference_jpegs_b64,
            resolution=resolution,
            duration_s=duration_s,
            last_frame_jpeg_b64=last_frame_jpeg_b64,
        )
    # 진행 중 회차는 플래그가 꺼져 있어도 BytePlus 로 끝낸다(073 D1). 키·모델 ID 는 그대로 검사한다.
    reason = _submit_block_reason(model)
    if reason:
        raise VpocError("ark.submit", "ModelUnavailable", f"{label(model)}: {reason}", 400)
    if not prompt.strip():
        raise VpocError("ark.submit", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)
    if len(prompt) > MAX_PROMPT_CHARS:
        # 자르지 않는다(R4).
        raise VpocError(
            "ark.submit",
            "PromptOverBudget",
            f"프롬프트가 {MAX_PROMPT_CHARS}자를 넘습니다 ({len(prompt)}자). 자르지 않고 제출을 멈췄습니다.",
            400,
        )

    content: list[dict] = [{"type": "text", "text": prompt}]
    # 캐릭터 참고 이미지. URL 이 아니라 본문 base64 로 보낸다(BytePlus 쪽에 이미지를 남기지 않는다).
    for b64 in reference_jpegs_b64 or []:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + b64},
                "role": "reference_image",
            }
        )
    if keyframe_jpeg_b64 and not reference_jpegs_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + keyframe_jpeg_b64},
                "role": "first_frame",
            }
        )
        # 키 장면 방식: 끝 장면까지 주면 Seedance 가 그 사이를 움직임으로 채운다(참고 이미지와 함께 못 쓴다).
        if last_frame_jpeg_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64," + last_frame_jpeg_b64},
                    "role": "last_frame",
                }
            )
    body = {
        "model": model_id(model),
        "content": content,
        "ratio": settings.ASPECT,
        "duration": int(duration_s or settings.DURATION_S),
        "resolution": resolution or settings.ARK_RESOLUTION,
        "generate_audio": bool(settings.ARK_GENERATE_AUDIO),
        "watermark": False,
    }
    started = time.time()
    result = _call("POST", "/contents/generations/tasks", body)
    task_id = str(result.get("id") or "")
    log.info(
        "vpoc.call where=ark.submit model=%s plan=%s shot=%s ms=%d ok=%s",
        model,
        plan_id,
        shot_id,
        int((time.time() - started) * 1000),
        bool(task_id),
    )
    if not task_id:
        raise VpocError("ark.submit", "NoTaskId", f"작업 ID 가 없습니다: {str(result)[:300]}", 502)
    prefix = video_output_prefix(owner, project_id, plan_id, shot_id, attempt, gen_id)
    return {
        "provider": model,
        "invocationArn": ARN_PREFIX + task_id,
        # 원장 키. Luma 토큰과 겹치지 않게 작업 ID 를 그대로 쓴다(작업 하나당 1회 계상).
        "clientRequestToken": "ark-" + task_id,
        "outputPrefix": prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
    }


def poll(invocation_arn: str, output_prefix: str) -> dict:
    """작업 상태. 완료면 결과 영상을 우리 버킷 `{prefix}/{taskId}/output.mp4` 로 옮긴 뒤 Completed."""
    if fal.is_fal_job(invocation_arn):
        # fal 작업은 fal.poll 로 넘긴다(같은 반환 키 모양: status · failureMessage · usage).
        return fal.poll(invocation_arn, output_prefix)
    task_id = invocation_arn[len(ARN_PREFIX):]
    result = _call("GET", f"/contents/generations/tasks/{task_id}")
    raw_status = str(result.get("status") or "")
    status = _STATUS.get(raw_status, "InProgress")
    message = ""
    if status == "Completed":
        url = str((result.get("content") or {}).get("video_url") or "")
        if not url:
            status, message = "Failed", "완료됐지만 영상 URL 이 없습니다."
        else:
            key = f"{output_prefix.rstrip('/')}/{task_id}/output.mp4"
            if not STORE.exists(key, bucket=settings.VIDEO_BUCKET):
                started = time.time()
                with urllib.request.urlopen(url, timeout=300) as response:
                    STORE.put_bytes(key, response.read(), "video/mp4", bucket=settings.VIDEO_BUCKET)
                log.info("vpoc.call where=ark.copy task=%s ms=%d", task_id, int((time.time() - started) * 1000))
    elif status == "Failed":
        error = result.get("error") or {}
        message = str(error.get("message") or error.get("code") or raw_status)
    return {
        "status": status,
        "failureMessage": message,
        "usage": result.get("usage"),
    }


def cancel(invocation_arn: str) -> None:
    """진행 중 작업 취소(설계자 2026-10-20). DELETE /contents/generations/tasks/{task_id}.

    Seedance 는 **대기 중일 때만** 취소한다 — 이미 생성 중이면 거절할 수 있다(그래도 우리 쪽은
    cancelled 로 둔다). fal 작업이면 fal.cancel 로 넘긴다. 거절·오류면 VpocError 를 올리고
    (호출부가 cancelRemote="rejected" 로 적는다), 성공이면 조용히 돌아온다.
    """
    if fal.is_fal_job(invocation_arn):
        fal.cancel(invocation_arn)
        return
    task_id = invocation_arn[len(ARN_PREFIX):]
    _call("DELETE", f"/contents/generations/tasks/{task_id}")
