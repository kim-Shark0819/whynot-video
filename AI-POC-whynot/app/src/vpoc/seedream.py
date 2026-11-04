"""Seedream 그림 생성 — 키 장면 · 캐릭터 시트 · 공간 시트 · 시작 장면용 (설계자 2026-10-14, 작업 124).

공급자는 settings.SEEDREAM_PROVIDER 로 고른다(작업 124, 2026-11-04 — BytePlus 쪽에서 ARK 키가 사라졌다).
- "fal"(기본): vpoc.fal 큐(제출 → 조회 → 결과)로 fal Seedream 을 부른다. 참고 이미지가 있으면 edit
  (image_urls = data URL, 순서대로 최대 10장), 없으면 text-to-image. 호출부가 넘긴 model(ARK id)은 쓰지 않는다.
  sync_mode 로 결과를 data URI 로 받아 메모리에서만 JPEG 로 바꾼다(저장은 호출부가 한다).
- "ark": 예전 BytePlus ModelArk /images/generations 그대로(_generate_ark). 인증 실패(401 · "API key doesn't
  exist" · AuthenticationError)면 그 호출 1번만 fal 로 넘긴다.

참고 이미지는 URL 이 아니라 본문 base64(data URL)로 보낸다 — 공급자 쪽에 이미지를 남기지 않는다.

실측(2026-10-14, ARK): Seedream 5.0 Flash 1920x1080 약 11초, Pro 약 60~100초. 둘 다 참고 이미지(웹툰 컷)의
인물 · 화풍을 잘 따른다. `sequential_image_generation` 은 이 모델들이 받지 않는다.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import time
import urllib.error
import urllib.request

from PIL import Image

from . import fal as vpoc_fal
from . import settings
from .errors import VpocError, error_gist

log = logging.getLogger("vpoc")

SIZE = "1920x1080"  # 16:9 — Seedance 의 시작 · 끝 장면으로 그대로 쓴다.

# fal Seedream 4.5 image_size 범위(가정, 작업 124): 총 픽셀 2560x1440 ~ 4096x4096, 한 변 최대 4096.
# 범위를 넘으면 fal_image_size 가 비율을 지키며 늘리거나 줄인다. 결과는 요청 크기로 다시 맞춘다.
_FAL_MIN_PIXELS = 2560 * 1440
_FAL_MAX_PIXELS = 4096 * 4096
_FAL_MAX_SIDE = 4096
_FAL_MAX_REFS = 10
_FAL_POLL_S = 2.0  # 큐 조회 간격
_FAL_WAIT_S = 300  # 제출부터 결과까지 상한(ARK urlopen timeout 과 같다)
_FAL_FETCH_TIMEOUT_S = 120
_FAL_MAX_BYTES = 25 * 1024 * 1024  # 결과 파일 상한(512Mi 파드 보호, casting 과 같다)
_MAX_PIXELS = 40_000_000  # 디코드 전 픽셀 상한
_SIZE_RE = re.compile(r"\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*")
# fal 거절 원문에서 안전 필터로 보는 낱말(작업 124). 422 "sensitive" · content policy · content checker.
_FAL_FILTER_WORDS = ("sensitive", "content_policy", "content policy", "content checker", "flagged", "nsfw", "safety")
_FILTERED_KIND = "FalContentFiltered"


def _data_url(jpeg: bytes, longest: int = 1536) -> str:
    with Image.open(io.BytesIO(jpeg)) as image:
        image = image.convert("RGB")
        image.thumbnail((longest, longest))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def provider() -> str:
    """지금 공급자: "ark" 면 ark, 그 밖(빈 값 · 틀린 값 포함)은 "fal"."""
    return "ark" if str(settings.SEEDREAM_PROVIDER or "").strip().lower() == "ark" else "fal"


def unit_usd() -> float:
    """원장 · 견적의 Seedream 장당 금액. fal 이면 VPOC_SEEDREAM_FAL_USD, ark 면 VPOC_SEEDREAM_USD."""
    return float(settings.SEEDREAM_FAL_USD if provider() == "fal" else settings.SEEDREAM_USD)


def _parse_wh(size) -> tuple[int, int] | None:
    found = _SIZE_RE.fullmatch(str(size or ""))
    if not found:
        return None
    width, height = int(found.group(1)), int(found.group(2))
    return (width, height) if width > 0 and height > 0 else None


def fal_image_size(size) -> tuple[int, int]:
    """"WxH" → fal image_size (width, height). 순수 함수.

    총 픽셀이 _FAL_MIN_PIXELS 보다 작으면 늘리고 _FAL_MAX_PIXELS 보다 크면 줄인다(비율 유지). 한 변은
    _FAL_MAX_SIDE 를 넘지 않는다. 범위 안이면 그대로, 꼴이 틀리면 SIZE(1920x1080)로 계산한다.
    """
    width, height = _parse_wh(size) or _parse_wh(SIZE)
    area = width * height
    scale = 1.0
    if area < _FAL_MIN_PIXELS:
        scale = math.sqrt(_FAL_MIN_PIXELS / area)
    elif area > _FAL_MAX_PIXELS:
        scale = math.sqrt(_FAL_MAX_PIXELS / area)
    scale = min(scale, _FAL_MAX_SIDE / max(width, height))
    if scale == 1.0:
        return width, height
    out_w, out_h = max(1, round(width * scale)), max(1, round(height * scale))
    if scale > 1.0 and out_w * out_h < _FAL_MIN_PIXELS and max(out_w, out_h) < _FAL_MAX_SIDE:
        out_w, out_h = math.ceil(width * scale), math.ceil(height * scale)
    if out_w * out_h > _FAL_MAX_PIXELS or max(out_w, out_h) > _FAL_MAX_SIDE:
        out_w, out_h = math.floor(width * scale), math.floor(height * scale)
    return out_w, out_h


def generate(
    prompt: str,
    references: list[bytes] | None = None,
    *,
    model: str,
    where: str = "seedream",
    size: str = SIZE,
    fal_model: str | None = None,
) -> bytes:
    """그림 1장(JPEG bytes). references 는 순서대로 "Image 1, 2, …" 가 된다(최대 10장).

    size 는 "WxH"(예: "2560x1440"). 기본은 SIZE(1920x1080) 라 기존 호출은 그대로다.
    캐릭터 만들기(vnew)가 시트 2560x1440 · 정면 1440x2560 처럼 다른 크기를 쓰려고 넣었다
    (설계자 2026-10-20). 공급자는 provider()(작업 124) — fal 이면 model 은 쓰지 않는다.
    fal_model(작업 125)을 주면 공급자와 상관없이 fal 의 그 endpoint 로 부른다(공간 시트 text-to-image).
    """
    if fal_model or provider() == "fal":
        return _generate_fal(prompt, references, where=where, size=size, fal_model=fal_model)
    try:
        return _generate_ark(prompt, references, model=model, where=where, size=size)
    except VpocError as exc:
        if not _ark_auth_failed(exc):
            raise
        # 키 값 · 원문은 남기지 않는다(작업 124). 이 호출 1번만 fal 로.
        log.warning("vpoc.seedream.ark_auth_fallback where=%s kind=%s to=fal", where, exc.kind)
    return _generate_fal(prompt, references, where=where, size=size)


def _ark_auth_failed(exc: VpocError) -> bool:
    """ARK 인증 실패(401 · "API key doesn't exist" · AuthenticationError)인가."""
    text = str(exc.message or "").lower()
    return (
        exc.kind == "HTTP401"
        or "api key doesn't exist" in text
        or "api key does not exist" in text
        or "authenticationerror" in text
    )


def _generate_ark(
    prompt: str,
    references: list[bytes] | None = None,
    *,
    model: str,
    where: str = "seedream",
    size: str = SIZE,
) -> bytes:
    """BytePlus ModelArk 경로(작업 124 전 generate 본문 그대로)."""
    if not settings.ARK_API_KEY:
        raise VpocError(where, "NoApiKey", "Seedream 키(ARK_API_KEY)가 설정되지 않았습니다.", 400)
    body: dict = {
        "model": model,
        "prompt": prompt[:3000],
        "size": size or SIZE,
        "response_format": "b64_json",
        "watermark": False,
    }
    if references:
        body["image"] = [_data_url(item) for item in references[:10]]
    request = urllib.request.Request(
        settings.ARK_BASE_URL + "/images/generations",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings.ARK_API_KEY},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = (json.loads(raw).get("error") or {}).get("message") or raw
        except ValueError:
            detail = raw
        raise VpocError(where, f"HTTP{exc.code}", str(detail)[:500], 502) from exc
    data = (payload.get("data") or [{}])[0]
    b64 = data.get("b64_json") or ""
    if not b64:
        raise VpocError(where, "NoImage", f"그림이 오지 않았습니다: {str(payload)[:300]}", 502)
    log.info(
        "vpoc.call where=%s model=%s ms=%d refs=%d",
        where,
        model,
        int((time.time() - started) * 1000),
        len(references or []),
    )
    raw_bytes = base64.b64decode(b64)
    with Image.open(io.BytesIO(raw_bytes)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


# ---------------------------------------------------------------- fal 경로(작업 124)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _fal_endpoint(model: str) -> str:
    value = str(model or "").strip()
    return value[len(settings.FAL_ID_PREFIX):] if value.startswith(settings.FAL_ID_PREFIX) else value


def _looks_filtered(text) -> bool:
    low = str(text or "").lower()
    return any(word in low for word in _FAL_FILTER_WORDS)


def _filtered_error(where: str, detail) -> VpocError:
    return VpocError(where, _FILTERED_KIND, "fal content policy: " + error_gist(detail, 200), 422)


def _fal_filtered(exc: VpocError, where: str) -> VpocError | None:
    """fal 4xx 거절 중 안전 필터(content policy · sensitive)면 _filtered 가 알아보는 오류로, 아니면 None."""
    if exc.kind not in vpoc_fal._REJECT_HTTP or not _looks_filtered(exc.message):  # noqa: SLF001
        return None
    return _filtered_error(where, exc.message)


def _fal_call(method: str, url: str, payload: dict | None, *, where: str) -> dict:
    """vpoc.fal._request 그대로(키는 거기서만 다룬다). 안전 필터 거절은 _FILTERED_KIND 로 바꾼다."""
    try:
        return vpoc_fal._request(method, url, payload, where=where)  # noqa: SLF001
    except VpocError as exc:
        filtered = _fal_filtered(exc, where)
        if filtered is None:
            raise
        raise filtered from exc


def _transient(exc: VpocError) -> bool:
    return exc.kind in ("FalUnreachable", "HTTP429") or str(exc.kind).startswith("HTTP5")


def _fetch_url(url: str, where: str) -> bytes:
    """결과가 data URI 가 아니고 https URL 일 때만 받는다(120초, 25MB 상한, 메모리로만)."""
    if not str(url or "").startswith("https://"):
        raise VpocError(where, "NoImage", "fal이 그림을 주지 않았습니다.", 502)
    try:
        with urllib.request.urlopen(url, timeout=_FAL_FETCH_TIMEOUT_S) as response:  # noqa: S310 — https 만
            raw = response.read(_FAL_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise VpocError(where, f"HTTP{exc.code}", f"결과 그림을 받지 못했습니다(HTTP {exc.code}).", 502) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise VpocError(where, "FalUnreachable", "결과 그림을 받는 중 연결이 끊겼습니다.", 502) from exc
    if len(raw) > _FAL_MAX_BYTES:
        raise VpocError(where, "ResultTooLarge", "결과 그림이 너무 큽니다.", 502)
    return raw


def _image_bytes(result, where: str) -> bytes:
    """fal 결과 → 그림 원본 bytes. images[0].url 이 data URI 면 풀고, https 면 받는다."""
    if isinstance(result, dict):
        nsfw = result.get("has_nsfw_concepts")
        if isinstance(nsfw, list) and any(item is True for item in nsfw):
            raise _filtered_error(where, "has_nsfw_concepts")
    images = result.get("images") if isinstance(result, dict) else None
    url = ""
    if isinstance(images, list) and images and isinstance(images[0], dict):
        url = str(images[0].get("url") or "")
    if url.startswith("data:"):
        _head, _sep, encoded = url.partition(",")
        if len(encoded) > _FAL_MAX_BYTES * 4 // 3 + 4:
            raise VpocError(where, "ResultTooLarge", "결과 그림이 너무 큽니다.", 502)
        try:
            return base64.b64decode(encoded)
        except ValueError as exc:
            raise VpocError(where, "BadImage", "받은 그림을 읽지 못했습니다.", 502) from exc
    return _fetch_url(url, where)


def _to_jpeg(raw: bytes, want: tuple[int, int], where: str) -> bytes:
    """원본 → RGB JPEG(q92, ARK 경로와 같다). 크기가 요청(want)과 다르면 맞춘다."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width * height > _MAX_PIXELS:
                raise ValueError(f"too many pixels: {width}x{height}")
            rgb = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise VpocError(where, "BadImage", "받은 그림을 읽지 못했습니다.", 502) from exc
    if rgb.size != want:
        rgb = rgb.resize(want, Image.LANCZOS)
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _generate_fal(
    prompt: str,
    references: list[bytes] | None,
    *,
    where: str,
    size: str,
    fal_model: str | None = None,
) -> bytes:
    """fal Seedream: 제출 → 조회 → 결과(작업 124). 참고 이미지가 있으면 edit, 없으면 text-to-image.

    fal_model(작업 125)이 있으면 그 endpoint 를 쓴다(없으면 위 규칙).
    """
    if not settings.FAL_KEY:
        raise VpocError(where, "NoApiKey", "fal 키(FAL_KEY)가 설정되지 않았습니다.", 400)
    refs = list(references or [])[:_FAL_MAX_REFS]
    default_endpoint = settings.SEEDREAM_FAL_EDIT_MODEL if refs else settings.SEEDREAM_FAL_T2I_MODEL
    endpoint = _fal_endpoint(fal_model or default_endpoint)
    want = _parse_wh(size or SIZE) or _parse_wh(SIZE)
    width, height = fal_image_size(size or SIZE)
    body: dict = {
        "prompt": prompt[:3000],
        "image_size": {"width": width, "height": height},
        "num_images": 1,
        "sync_mode": True,
    }
    if refs:
        body["image_urls"] = [_data_url(item) for item in refs]
    started = time.monotonic()
    submitted = _fal_call("POST", f"{settings.FAL_QUEUE_URL}/{endpoint}", body, where=where)
    request_id = str((submitted or {}).get("request_id") or "") if isinstance(submitted, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
        raise VpocError(where, "NoRequestId", "fal이 요청 ID를 주지 않았습니다.", 502)
    deadline = started + _FAL_WAIT_S
    while True:
        try:
            status = _fal_call("GET", vpoc_fal._status_url(endpoint, request_id), None, where=where)  # noqa: SLF001
        except VpocError as exc:
            if not _transient(exc) or time.monotonic() >= deadline:
                raise
            status = {}
        state = str((status or {}).get("status") or "").upper() if isinstance(status, dict) else ""
        if state == "COMPLETED":
            break
        if state and state not in ("IN_QUEUE", "IN_PROGRESS"):
            detail = vpoc_fal._error_detail(json.dumps(status, ensure_ascii=False, default=str))  # noqa: SLF001
            if _looks_filtered(detail):
                raise _filtered_error(where, detail)
            raise VpocError(where, f"Fal{state.title()}", error_gist(detail, 300) or state, 502)
        if time.monotonic() >= deadline:
            try:
                vpoc_fal.cancel(f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}")
            except Exception:  # noqa: BLE001 — 취소는 최선만
                pass
            raise VpocError(where, "Timeout", "fal Seedream 이 시간 안에 그림을 주지 않았습니다.", 504)
        _sleep(_FAL_POLL_S)
    result = _fal_call("GET", vpoc_fal._response_url(endpoint, request_id), None, where=where)  # noqa: SLF001
    jpeg = _to_jpeg(_image_bytes(result, where), want, where)
    log.info(
        "vpoc.call where=%s model=%s ms=%d refs=%d request=%s",
        where,
        endpoint,
        int((time.monotonic() - started) * 1000),
        len(refs),
        request_id,
    )
    return jpeg


def _filtered(exc: Exception) -> bool:
    if getattr(exc, "kind", "") == _FILTERED_KIND:
        return True
    text = str(getattr(exc, "message", "") or exc).lower()
    return "sensitive" in text or "safety" in text or "content" in text and "policy" in text


def generate_safe(
    prompt: str,
    references: list[bytes] | None = None,
    *,
    model: str,
    where: str = "seedream",
    size: str = SIZE,
    fal_model: str | None = None,
) -> bytes:
    """generate + 안전 필터 오탐 대응(설계자 2026-10-14 "에러가 다시는 나지 않도록").

    1) 그대로 2) 한 번 더(결과가 매번 달라 통과하기도 한다) 3) Claude 가 같은 장면을 무난한 말로 다시 쓴 문장
    4) 행동 묘사를 뺀 최소 문장(구도 · 인물 · 화풍 참고 이미지는 그대로). 필터가 아닌 오류는 바로 올린다.
    """
    from . import llm  # noqa: PLC0415 — 순환 import 피하기

    attempts = [prompt, prompt]
    last: Exception | None = None
    index = 0
    rewritten = False
    while index < len(attempts):
        try:
            return generate(attempts[index], references, model=model, where=where, size=size, fal_model=fal_model)
        except VpocError as exc:
            if not _filtered(exc):
                raise
            last = exc
            log.warning("vpoc.seedream.filtered where=%s step=%d", where, index + 1)
            if index == 1 and not rewritten:
                rewritten = True
                try:
                    better, _usage = llm.safe_image_prompt(prompt)
                    attempts.append(better)
                except Exception as rewrite_exc:  # noqa: BLE001
                    log.warning("vpoc.seedream.rewrite_failed err=%s", rewrite_exc)
                head = prompt.split("Scene:")[0].strip()
                attempts.append(head + " Scene: a calm, friendly moment; characters stand naturally.")
            index += 1
    raise last or VpocError(where, "Filtered", "그림을 만들지 못했습니다.", 502)
