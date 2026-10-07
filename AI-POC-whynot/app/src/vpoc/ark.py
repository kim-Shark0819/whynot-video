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
import time
import urllib.error
import urllib.request

from . import settings
from .errors import VpocError, now_iso
from .store import STORE, video_output_prefix

log = logging.getLogger("vpoc")

ARN_PREFIX = "ark/tasks/"
MAX_PROMPT_CHARS = 2000

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
    return model in settings.ARK_MODEL_IDS


def is_ark_job(invocation_arn: str) -> bool:
    return str(invocation_arn or "").startswith(ARN_PREFIX)


def model_id(model: str) -> str:
    return str(settings.ARK_MODEL_IDS.get(model) or "").strip()


def unavailable_reason(model: str) -> str:
    """고를 수 없으면 그 이유(화면에 그대로 보인다). 고를 수 있으면 빈 문자열."""
    if not model_id(model):
        return "모델 ID 미설정"
    if not settings.ARK_API_KEY:
        return "API 키 미설정"
    return ""


DEFAULT_MODEL = "luma-ray2"


def choices() -> list[dict]:
    """② 화면의 모델 목록. 고를 수 없는 모델도 이유와 함께 보인다."""
    luma_usd = settings.shot_usd(DEFAULT_MODEL)
    items = [
        {
            "id": DEFAULT_MODEL,
            "label": "Luma Ray2",
            "available": True,
            "reason": "",
            "perShotUsd": luma_usd,
            "priceKnown": True,
        }
    ]
    for key, label in LABELS.items():
        reason = unavailable_reason(key)
        items.append(
            {
                "id": key,
                "label": label,
                "available": not reason,
                "reason": reason,
                "perShotUsd": settings.shot_usd(key),
                "priceKnown": settings.ark_usd_per_sec(key) is not None,
            }
        )
    return items


def selected(state: dict) -> str:
    """이 계획이 쓸 모델. 고르기가 꺼져 있거나 고를 수 없는 값이면 Luma."""
    if not settings.MODEL_PICKER_ENABLED:
        return DEFAULT_MODEL
    model = str((state.get("steps", {}).get("2") or {}).get("videoModel") or DEFAULT_MODEL)
    if model == DEFAULT_MODEL or (is_ark(model) and not unavailable_reason(model)):
        return model
    return DEFAULT_MODEL


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
) -> dict:
    """작업을 만들고 즉시 돌아온다. video.submit 과 같은 키를 돌려준다."""
    reason = unavailable_reason(model)
    if reason:
        raise VpocError("ark.submit", "ModelUnavailable", f"{LABELS.get(model, model)}: {reason}", 400)
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
    if keyframe_jpeg_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + keyframe_jpeg_b64},
                "role": "first_frame",
            }
        )
    body = {
        "model": model_id(model),
        "content": content,
        "ratio": settings.ASPECT,
        "duration": settings.DURATION_S,
        "resolution": settings.ARK_RESOLUTION,
        "generate_audio": False,
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
                with urllib.request.urlopen(url, timeout=300) as response:
                    STORE.put_bytes(key, response.read(), "video/mp4", bucket=settings.VIDEO_BUCKET)
    elif status == "Failed":
        error = result.get("error") or {}
        message = str(error.get("message") or error.get("code") or raw_status)
    return {
        "status": status,
        "failureMessage": message,
        "usage": result.get("usage"),
    }
