"""vpoc1 fal.ai 영상 경로 — ② 영상 모델에 fal 모델 추가(설계자 2026-10-18).

ark.py(Seedance)와 **같은 모양**(submit → poll → 출력 mp4)을 돌려준다. ark.py 가 작업
식별자의 접두사(`ark/tasks/`)로 어느 경로인지 가르듯, fal 작업은 `fal/requests/` 로 가른다.

fal.ai 는 키 하나로 수백 개의 영상 모델(Kling · MiniMax Hailuo · Wan · Veo …)을 큐 API 로
부른다. 모델마다 입력 스키마가 다르므로, 사용자가 등록할 때 OpenAPI 스키마를 받아 우리가
쓰는 필드 이름(프롬프트 · 첫 프레임 · 끝 프레임 · 참고 이미지 · 길이 · 해상도 …)을 뽑아
`caps` 로 저장하고, 제출 때 그 caps 로 본문을 조립한다.

보안(설계자 2026-10-18): fal 쪽에 이미지를 남기지 않는다.
- 이미지 입력은 URL 을 만들지 않고 요청 본문의 `data:image/jpeg;base64,<...>` data URI 로 보낸다.
- 결과 영상 URL 은 만료되므로 완료를 본 즉시 우리 영상 버킷으로 옮긴다(ark.poll 과 같은 규칙).
- API 키(FAL_KEY)는 로그 · 오류 메시지에 절대 넣지 않는다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import settings
from .errors import VpocError, error_gist, now_iso
from .store import STORE, video_output_prefix

log = logging.getLogger("vpoc")

# ark 가 "ark/tasks/" 를 쓰듯 fal 은 이 접두사로 작업을 가른다. 뒤에 endpoint_id 와 request_id 를
# 붙인다: "fal/requests/<endpoint_id>/<request_id>". 마지막 경로 조각(request_id)이 출력 폴더명이
# 되어 video.find_output_mp4(prefix, arn) 가 그 폴더만 보도록 맞춘다(ark 와 같은 규칙).
ARN_PREFIX = "fal/requests/"

# 등록 모델 목록. 앱 IRSA 가 `users/*` 아래만 쓸 수 있어 `_system` 을 한 사용자처럼 쓴다.
REGISTRY_KEY = "users/_system/vpoc/fal-models.json"
# 프로세스 캐시. 파드가 여럿이어도 30초면 충분하다(등록은 드물다).
_CACHE_TTL_S = 30
_cache: dict = {"at": 0.0, "models": None}
_cache_lock = threading.Lock()

# 단가(pricing) 메모리 캐시. endpoint_id → {"at": epoch, "price": {...}|None}. 1시간(단가는 자주 안 바뀐다).
_PRICING_TTL_S = 3600
_pricing_cache: dict = {}
_pricing_lock = threading.Lock()
# OpenAPI prompt maxLength 를 못 찾을 때 쓰는 기본 상한(실측: Kling 2500).
DEFAULT_PROMPT_MAX = 2500

# HTTP 시간 제한. 큐 제출·조회는 짧고, 결과 영상 다운로드만 길다(ark 와 같은 값).
_CALL_TIMEOUT_S = 60
_DOWNLOAD_TIMEOUT_S = 300

# 카탈로그 조회 limit. 100 은 fal 이 거절한다(실측 전제) — 50 을 쓴다.
CATALOG_LIMIT = 50

# OpenAPI 입력 스키마에서 찾을 필드 이름 후보(앞에서부터 처음 발견한 것을 쓴다).
_START_IMAGE_FIELDS = (
    "image_url",
    "start_image_url",
    "first_frame_url",
    "first_frame_image_url",
    "input_image_url",
)
_END_IMAGE_FIELDS = (
    "end_image_url",
    "last_frame_url",
    "tail_image_url",
    "last_frame_image_url",
    "end_frame_url",
)
_REFERENCE_FIELDS = (
    "reference_image_urls",
    "image_urls",
    "reference_images",
    "ref_image_urls",
)
_AUDIO_FIELDS = ("generate_audio", "enable_audio")
# video-to-video 입력 영상 필드 후보(설계자 2026-10-22, 프리비즈 방식).
_VIDEO_FIELDS = ("video_url", "reference_video_url", "input_video_url", "video")
# reference-to-video(Seedance 2.5, Claude 조사 2026-10-25) 입력 영상 **배열** 필드 후보.
# 단일 video_url 과 달리 참조 영상 여러 개(@Video1..)를 받는다.
_VIDEO_LIST_FIELDS = (
    "video_urls",
    "reference_video_urls",
    "ref_video_urls",
    "input_video_urls",
)
# 인물별 참고(마네킹 색 ↔ 캐릭터)를 담는 elements 배열(각 항목에 정면 이미지 필드가 있다).
_ELEMENTS_FIELDS = ("elements",)
_ELEMENT_ITEM_IMAGE_FIELDS = (
    "frontal_image_url",
    "image_url",
    "reference_image_url",
    "front_image_url",
)
# 참고 이미지 목록(배열) 필드 후보. _REFERENCE_FIELDS 와 같은 후보를 쓰되 imageListField 로 따로 뽑는다.
_IMAGE_LIST_FIELDS = (
    "image_urls",
    "reference_image_urls",
    "reference_images",
    "ref_image_urls",
)
_KEEP_AUDIO_FIELDS = ("keep_audio",)
# imageListField 의 maxItems 를 못 찾을 때 기본 상한. Seedance reference-to-video(image_urls)처럼
# maxItems 를 안 적는 스키마가 많아 10 으로 둔다(Claude 조사 2026-10-25). 참조 30 은 과하다.
DEFAULT_IMAGE_LIST_MAX = 10
# videoListField 의 maxItems 를 못 찾을 때 기본 상한.
DEFAULT_VIDEO_LIST_MAX = 10


# ---------------------------------------------------------------- 모델 ID / 헬퍼


def is_fal(model: str) -> bool:
    return settings.is_fal(model)


def is_fal_job(invocation_arn: str) -> bool:
    return str(invocation_arn or "").startswith(ARN_PREFIX)


def endpoint_id(model: str) -> str:
    return settings.fal_endpoint_id(model)


def _auth_header() -> dict:
    """fal 인증 헤더. 키 값은 여기서만 만지고 어디에도 로그로 남기지 않는다."""
    return {"Authorization": "Key " + settings.FAL_KEY}


# ---------------------------------------------------------------- HTTP


def _request(method: str, url: str, payload: dict | None = None, *, where: str) -> dict:
    """fal HTTP 호출. ark._call 과 같은 방식(urllib)·오류 처리.

    402/403 본문에 잔액 소진/계정 잠금 표시가 있으면 사용자용 한국어 메시지로 감싼다.
    402 는 Payment Required 라 본문 판정이 안 돼도 잔액 소진으로 본다(2026-10-19).
    """
    headers = {"Content-Type": "application/json", **_auth_header()}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_CALL_TIMEOUT_S) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        detail = _error_detail(raw)
        if exc.code in (402, 403) and (_balance_exhausted(raw) or exc.code == 402):
            raise VpocError(
                where,
                "FalBalanceExhausted",
                "fal.ai 잔액이 부족합니다. fal.ai 대시보드에서 크레딧을 충전해 주세요. "
                "충전 후 ‘다시 시도’를 눌러 주세요.",
                402,
            ) from exc
        raise VpocError(where, f"HTTP{exc.code}", detail[:500], 502) from exc
    except urllib.error.URLError as exc:
        raise VpocError(where, "FalUnreachable", f"fal.ai 에 연결하지 못했습니다: {exc.reason}", 502) from exc


def _error_detail(raw: str) -> str:
    """fal 오류 본문에서 사람이 읽을 메시지를 뽑는다. 못 뽑으면 원문."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list) and value:
                parts = [str((item or {}).get("msg") or item) for item in value]
                return "; ".join(parts)
    return raw


def _balance_exhausted(raw: str) -> bool:
    low = raw.lower()
    return "exhausted balance" in low or "locked" in low


# ---------------------------------------------------------------- 등록 저장소


def _registry_read() -> list[dict] | None:
    """등록된 fal 모델 목록. 없으면 빈 목록, 읽기 실패면 None(073 R11.2 — 캐시하지 않게 구분한다)."""
    try:
        data, _ = STORE.read_json(REGISTRY_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("vpoc.fal.registry.read_failed err=%s", exc)
        return None
    if not data:
        return []
    models = data.get("models")
    return models if isinstance(models, list) else []


def registered() -> list[dict]:
    """등록 목록(30초 프로세스 캐시).

    읽기 실패(None)는 캐시하지 않고 빈 목록을 돌려준다 — 다음 호출이 다시 읽는다(073 R11.2, D11).
    빈 목록을 30초 캐시하면 그동안 등록된 모델이 모두 "미등록"으로 보인다(리뷰 P-3). 성공은 30초 캐시(R11.3).
    """
    now = time.time()
    with _cache_lock:
        if _cache["models"] is not None and now - _cache["at"] < _CACHE_TTL_S:
            return list(_cache["models"])
    models = _registry_read()
    if models is None:
        return []
    with _cache_lock:
        _cache["models"] = models
        _cache["at"] = now
    return list(models)


def _invalidate_cache() -> None:
    with _cache_lock:
        _cache["models"] = None
        _cache["at"] = 0.0


def find(model: str) -> dict | None:
    """등록된 모델 하나(없으면 None). model 은 "fal:<endpoint_id>"."""
    eid = endpoint_id(model)
    if not eid:
        return None
    for item in registered():
        if item.get("endpointId") == eid or item.get("id") == model:
            return item
    return None


def is_registered(model: str) -> bool:
    return find(model) is not None


def register(endpoint: str, *, added_by: str = "") -> dict:
    """endpoint_id 의 스키마를 받아 caps 를 만들고 목록에 더한다. 등록한 항목을 돌려준다."""
    if not settings.FAL_KEY:
        raise VpocError("fal.register", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise VpocError("fal.register", "BadEndpoint", "endpoint_id 가 비어 있습니다.", 400)
    if settings.is_denied_endpoint(endpoint):
        raise VpocError(
            "fal.register",
            "Denied",
            "설계자 지시로 사용이 금지된 모델입니다.",
            400,
        )

    meta = model_meta(endpoint)
    # OpenAPI 를 먼저 받는다 — 여기 info.x-fal-metadata 가 category·thumbnail 을 확실히 담는다.
    # `/v1/models` 응답이 성긴 모델(실측: Veo 샘플은 metadata 자체가 없다)에서도 카테고리를 안다.
    schema = openapi_schema(endpoint, meta.get("openapi"))
    oa_meta = _openapi_metadata(schema)

    category = str(meta.get("category") or oa_meta.get("category") or "")
    if category not in settings.FAL_VIDEO_CATEGORIES:
        raise VpocError(
            "fal.register",
            "NotVideoModel",
            f"영상 단계에서 쓸 수 없는 모델입니다(카테고리: {category or '알 수 없음'}).",
            400,
        )

    caps, reason = extract_caps(schema, endpoint)
    if caps is None:
        raise VpocError("fal.register", "SchemaUnreadable", reason or "스키마를 읽지 못했습니다.", 400)

    # 단가(설계자 2026-10-20). 등록 때 함께 받아 레지스트리에 저장한다. 실패하면 None(단가 미정).
    price = pricing(endpoint)

    item = {
        "id": settings.FAL_ID_PREFIX + endpoint,
        "endpointId": endpoint,
        "label": str(meta.get("displayName") or oa_meta.get("title") or endpoint),
        "category": category,
        "description": str(meta.get("description") or oa_meta.get("about") or ""),
        "thumbnailUrl": str(meta.get("thumbnailUrl") or oa_meta.get("thumbnailUrl") or ""),
        "caps": caps,
        "pricing": price,
        "addedBy": added_by,
        "addedAt": now_iso(),
    }

    data, etag = STORE.read_json(REGISTRY_KEY)
    models = (data or {}).get("models") if isinstance(data, dict) else None
    models = models if isinstance(models, list) else []
    if any(entry.get("endpointId") == endpoint for entry in models):
        raise VpocError("fal.register", "AlreadyRegistered", "이미 등록된 모델입니다.", 409)
    models.append(item)
    STORE.write_json(REGISTRY_KEY, {"models": models}, etag=etag)
    _invalidate_cache()
    return item


def unregister(endpoint: str) -> bool:
    """등록 해제. 지웠으면 True, 없었으면 False."""
    endpoint = str(endpoint or "").strip()
    data, etag = STORE.read_json(REGISTRY_KEY)
    models = (data or {}).get("models") if isinstance(data, dict) else None
    models = models if isinstance(models, list) else []
    kept = [entry for entry in models if entry.get("endpointId") != endpoint]
    if len(kept) == len(models):
        return False
    STORE.write_json(REGISTRY_KEY, {"models": kept}, etag=etag)
    _invalidate_cache()
    return True


# ---------------------------------------------------------------- 카탈로그 / 스키마


def model_meta(endpoint: str) -> dict:
    """GET /models?endpoint_id=.. — 한 모델의 메타(+가능하면 OpenAPI).

    실제 응답: {models:[{endpoint_id, metadata:{display_name,category,...}, openapi?}]}.
    `expand=openapi-3.0` 은 지금 대부분 실패하므로(샘플: fal 이 openapi.error 를 돌려줌),
    여기서는 메타만 확실히 받고 OpenAPI 는 openapi_schema() 가 공개 주소로 따로 받는다.
    """
    query = urllib.parse.urlencode({"endpoint_id": endpoint, "expand": "openapi-3.0"})
    result = _request("GET", f"{settings.FAL_API_URL}/models?{query}", where="fal.model_meta")
    entry = _first_model_entry(result)
    return _flatten_model_entry(entry)


def _first_model_entry(result: object) -> dict:
    """응답에서 첫 모델 항목(raw)을 고른다. {models:[..]} 또는 단일 객체."""
    if isinstance(result, dict) and isinstance(result.get("models"), list):
        return result["models"][0] if result["models"] else {}
    return result if isinstance(result, dict) else {}


def _flatten_model_entry(entry: dict) -> dict:
    """모델 항목에서 metadata 안을 먼저 읽고, 없으면 바깥을 본다(실측 2026-10-18).

    돌려주는 키: endpointId · displayName · category · description · thumbnailUrl · status · openapi.
    """
    if not isinstance(entry, dict):
        entry = {}
    meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}

    def pick(*keys: str) -> str:
        for source in (meta, entry):
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    return {
        "endpointId": pick("endpoint_id", "endpointId") or str(entry.get("endpoint_id") or ""),
        "displayName": pick("display_name", "displayName", "title"),
        "category": pick("category"),
        "description": pick("description", "short_description"),
        "thumbnailUrl": pick("thumbnail_url", "thumbnailUrl"),
        "status": pick("status"),
        "openapi": entry.get("openapi") if isinstance(entry.get("openapi"), dict) else {},
    }


def openapi_schema(endpoint: str, expand_result: dict | None = None) -> dict:
    """모델의 OpenAPI 3 스키마.

    순서(실측 2026-10-18):
      1. expand 결과(`model_meta` 의 openapi)에 `paths` 가 있으면 그것을 쓴다.
      2. 없거나 `error` 면 공개 주소 `FAL_OPENAPI_URL?endpoint_id=<id>` 를 GET 한다(인증 불필요).
    """
    if isinstance(expand_result, dict) and expand_result.get("paths") and not expand_result.get("error"):
        return expand_result
    query = urllib.parse.urlencode({"endpoint_id": endpoint})
    result = _request("GET", f"{settings.FAL_OPENAPI_URL}?{query}", where="fal.openapi")
    return result if isinstance(result, dict) else {}


def _openapi_metadata(openapi: dict) -> dict:
    """OpenAPI info.x-fal-metadata(+ info.title/about)에서 category·thumbnail·title·about 을 뽑는다.

    `/v1/models` 응답이 성긴 모델에서 카테고리·라벨을 복구하는 보조 출처다(실측 2026-10-18).
    """
    info = openapi.get("info") if isinstance(openapi, dict) else None
    info = info if isinstance(info, dict) else {}
    meta = info.get("x-fal-metadata")
    meta = meta if isinstance(meta, dict) else {}
    return {
        "category": str(meta.get("category") or ""),
        "thumbnailUrl": str(meta.get("thumbnailUrl") or ""),
        "title": str(info.get("title") or "").replace("Queue OpenAPI for ", "").strip(),
        "about": str(meta.get("about") or ""),
    }


def parse_catalog(result: object, category: str) -> dict:
    """GET /models 응답을 화면용으로 바꾼다(네트워크 없이 검증 가능하게 분리).

    각 모델의 label/description/thumbnail/category 는 **metadata 안을 먼저** 읽는다(실측 2026-10-18).
    """
    raw = result.get("models") if isinstance(result, dict) else None
    models = []
    for entry in raw or []:
        flat = _flatten_model_entry(entry)
        models.append(
            {
                "endpointId": flat["endpointId"],
                "label": flat["displayName"] or flat["endpointId"],
                "category": flat["category"] or category,
                "description": flat["description"],
                "thumbnailUrl": flat["thumbnailUrl"],
            }
        )
    next_cursor = str((result.get("next_cursor") or result.get("cursor") or "") or "") if isinstance(result, dict) else ""
    has_more = bool(next_cursor) or bool(result.get("has_more")) if isinstance(result, dict) else False
    return {"models": models, "nextCursor": next_cursor, "hasMore": has_more}


def catalog(category: str, *, q: str = "", cursor: str = "") -> dict:
    """GET /models?category=..&status=active&limit=50 — 카탈로그 한 페이지."""
    params = {"category": category, "status": "active", "limit": CATALOG_LIMIT}
    if q:
        params["q"] = q
    if cursor:
        params["cursor"] = cursor
    query = urllib.parse.urlencode(params)
    result = _request("GET", f"{settings.FAL_API_URL}/models?{query}", where="fal.catalog")
    return parse_catalog(result, category)


# ---------------------------------------------------------------- 단가(pricing)


def _parse_pricing(result: object, endpoint: str) -> dict | None:
    """pricing 응답에서 unitPrice·unit 을 뽑는다(네트워크 없이 검증 가능하게 분리).

    응답: {"prices":[{"endpoint_id":..,"unit_price":0.14,"unit":"seconds","currency":"USD"}, ...]}.
    endpoint 와 맞는 항목을 고르고(하나뿐이면 그것), 없으면 None.
    """
    prices = result.get("prices") if isinstance(result, dict) else None
    if not isinstance(prices, list) or not prices:
        return None
    chosen = None
    for item in prices:
        if isinstance(item, dict) and str(item.get("endpoint_id") or "") == endpoint:
            chosen = item
            break
    if chosen is None and len(prices) == 1 and isinstance(prices[0], dict):
        chosen = prices[0]
    if not isinstance(chosen, dict):
        return None
    try:
        unit_price = float(chosen.get("unit_price"))
    except (TypeError, ValueError):
        return None
    return {"unitPrice": unit_price, "unit": str(chosen.get("unit") or "")}


def pricing(endpoint: str) -> dict | None:
    """endpoint_id 의 단가. {"unitPrice": float, "unit": str} 또는 None(실패).

    `GET {FAL_API_URL}/models/pricing?endpoint_id=<id>` (헤더 Authorization Key — _request 가 붙이고
    키 값은 로그로 남기지 않는다). 성공 결과는 1시간 메모리 캐시한다. 키가 없거나 호출이 실패하면
    None(호출부가 "단가 미정" 으로 다룬다).
    """
    endpoint = str(endpoint or "").strip()
    if not endpoint or not settings.FAL_KEY:
        return None
    now = time.time()
    with _pricing_lock:
        hit = _pricing_cache.get(endpoint)
        if hit and now - hit["at"] < _PRICING_TTL_S:
            return dict(hit["price"]) if hit["price"] else None
    query = urllib.parse.urlencode({"endpoint_id": endpoint})
    try:
        result = _request("GET", f"{settings.FAL_API_URL}/models/pricing?{query}", where="fal.pricing")
    except VpocError as exc:
        log.warning("vpoc.fal.pricing.failed endpoint=%s kind=%s", endpoint, exc.kind)
        return None
    price = _parse_pricing(result, endpoint)
    with _pricing_lock:
        _pricing_cache[endpoint] = {"at": now, "price": price}
    return dict(price) if price else None


def pricing_for(model: str) -> dict | None:
    """등록 모델("fal:<endpoint>")의 단가. 레지스트리에 저장된 pricing 을 먼저 쓰고, 없으면 받아
    레지스트리를 갱신한다(지연 획득). 실패하면 None.
    """
    item = find(model)
    if not item:
        return None
    stored = item.get("pricing")
    if isinstance(stored, dict) and "unitPrice" in stored:
        return dict(stored)
    endpoint = str(item.get("endpointId") or "")
    price = pricing(endpoint)
    if price:
        _update_registry_pricing(endpoint, price)
    return dict(price) if price else None


def _update_registry_pricing(endpoint: str, price: dict) -> None:
    """옛 레지스트리 항목에 pricing 을 채운다(처음 쓸 때 지연 획득). 조건부 쓰기·캐시 무효화."""
    try:
        data, etag = STORE.read_json(REGISTRY_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("vpoc.fal.pricing.registry_read_failed err=%s", exc)
        return
    models = (data or {}).get("models") if isinstance(data, dict) else None
    models = models if isinstance(models, list) else []
    changed = False
    for entry in models:
        if isinstance(entry, dict) and entry.get("endpointId") == endpoint and not entry.get("pricing"):
            entry["pricing"] = dict(price)
            changed = True
    if not changed:
        return
    try:
        STORE.write_json(REGISTRY_KEY, {"models": models}, etag=etag)
        _invalidate_cache()
    except Exception as exc:  # noqa: BLE001
        log.warning("vpoc.fal.pricing.registry_write_failed err=%s", exc)


# ---------------------------------------------------------------- 스키마 → caps


def _deref(schema: dict, root: dict, _seen: set | None = None) -> dict:
    """$ref 를 따라간다(순환 방지). ref 가 아니면 그대로 돌려준다."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    seen = _seen or set()
    if ref in seen:
        return {}
    seen.add(ref)
    node: object = root
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return _deref(node, root, seen) if isinstance(node, dict) else {}


def _unwrap(spec: object) -> dict:
    """필드 스펙에서 실제 타입 노드를 꺼낸다.

    fal 은 nullable 필드를 `{"anyOf":[{...실제...},{"type":"null"}]}` 로 감싼다. anyOf 안에서
    enum 이나 type 을 가진 첫 비-null 노드를 돌려준다. 감싸지 않은 경우는 그대로.
    """
    if not isinstance(spec, dict):
        return {}
    for key in ("anyOf", "oneOf", "allOf"):
        branches = spec.get(key)
        if isinstance(branches, list):
            picked = {}
            for branch in branches:
                if not isinstance(branch, dict) or branch.get("type") == "null":
                    continue
                if branch.get("enum") is not None or branch.get("type") is not None:
                    # enum 을 가진 가지를 우선한다(타입만 있는 가지보다).
                    if branch.get("enum") is not None:
                        return {**spec, **branch}
                    picked = picked or {**spec, **branch}
            if picked:
                return picked
    return spec


def _find_input_schema(openapi: dict, endpoint: str = "") -> dict:
    """OpenAPI 에서 입력 스키마(properties 를 가진 객체)를 찾는다.

    순서(실측 2026-10-18):
      1. paths["/<endpoint_id>"].post.requestBody.content["application/json"].schema.$ref
         (endpoint 가 없거나 못 찾으면 POST requestBody 가 있는 첫 경로)
      2. 이름이 "...Input" 으로 끝나고 properties 에 prompt 가 있는 스키마 중 첫 번째
      3. properties 가 가장 많은 스키마
    못 찾으면 빈 dict.
    """
    if not isinstance(openapi, dict):
        return {}
    paths = openapi.get("paths") or {}
    schemas = (openapi.get("components") or {}).get("schemas") or {}

    # 1. endpoint 경로(없으면 POST 가 달린 경로)의 requestBody schema
    candidates = []
    if endpoint:
        candidates.append("/" + endpoint)
    candidates += [key for key in paths if isinstance(key, str) and "{request_id}" not in key]
    for path_key in candidates:
        methods = paths.get(path_key)
        if not isinstance(methods, dict):
            continue
        post = methods.get("post")
        if not isinstance(post, dict):
            continue
        body = (post.get("requestBody") or {}).get("content") or {}
        schema = (body.get("application/json") or {}).get("schema") or {}
        resolved = _deref(schema, openapi)
        if isinstance(resolved, dict) and resolved.get("properties"):
            return resolved

    # 2. 이름이 Input 으로 끝나고 prompt 를 가진 스키마
    for name, schema in schemas.items():
        if isinstance(name, str) and name.lower().endswith("input"):
            resolved = _deref(schema, openapi)
            props = resolved.get("properties") if isinstance(resolved, dict) else None
            if isinstance(props, dict) and "prompt" in props:
                return resolved

    # 3. properties 가 가장 많은 스키마
    best: dict = {}
    best_count = -1
    for schema in schemas.values():
        resolved = _deref(schema, openapi)
        props = resolved.get("properties") if isinstance(resolved, dict) else None
        if isinstance(props, dict) and len(props) > best_count:
            best, best_count = resolved, len(props)
    return best


def _first_present(props: dict, candidates) -> str:
    for name in candidates:
        if name in props:
            return name
    return ""


def _prompt_max_length(props: dict, prompt_field: str) -> int:
    """prompt 필드의 maxLength(OpenAPI). 없으면 DEFAULT_PROMPT_MAX(2500).

    nullable 로 감싼 경우(anyOf)도 _unwrap 으로 꺼내 본다. 양의 정수가 아니면 기본값.
    """
    if not prompt_field:
        return DEFAULT_PROMPT_MAX
    spec = _unwrap(props.get(prompt_field))
    value = spec.get("maxLength")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_PROMPT_MAX


def _trim_prompt(prompt: str, max_length: int) -> str:
    """prompt 가 max_length 를 넘으면 문장 경계에서 자른다(넘지 않으면 그대로).

    max_length 이내에서 마지막 문장부호(. ! ? 개행) 뒤까지만 남긴다. 한 문장도 안 들어가면
    그냥 max_length 에서 끊는다(공백 경계 우선).
    """
    text = prompt or ""
    # fal(Kling 등)은 길이를 UTF-8 바이트로 센다 — 한글 대사가 섞이면 글자 수로 2,500 이하여도 거절된다
    # (2026-10-21 실측 "prompt: size must be between 0 and 2500"). 그래서 바이트 기준으로 자른다.
    if max_length <= 0 or len(text.encode("utf-8")) <= max_length:
        return text
    window = text.encode("utf-8")[:max_length].decode("utf-8", errors="ignore")
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
    if cut >= 0:
        # 문장부호를 포함해 그 뒤 공백 전까지 남긴다(.! ? 는 한 글자, 개행은 그 자리).
        end = cut + 1
        trimmed = window[:end].rstrip()
        if trimmed:
            return trimmed
    space = window.rfind(" ")
    if space > 0:
        return window[:space].rstrip()
    return window.rstrip()


def _enum_of(props: dict, field: str) -> list:
    """field 의 enum(anyOf 로 감싼 경우도 꺼낸다). 없으면 빈 목록."""
    spec = _unwrap(props.get(field))
    enum = spec.get("enum")
    if isinstance(enum, list):
        return [item for item in enum if item is not None]
    return []


def _durations_from(props: dict) -> tuple[list[int], bool, dict]:
    """duration 필드에서 (정수 초 목록, 문자열로 보내는가, {정수문자열: 원래표기}) 를 뽑는다.

    - enum 문자열 "3".."15"      → [3..15], as_string True, {"3":"3", ...}
    - enum "4s","6s","8s"        → [4,6,8], as_string True, {"4":"4s", ...}  (원래 표기로 되돌린다)
    - type number min 0.92 max 15 → [1..15](min 은 올림, 최소 1), as_string False, {}
    - duration 필드 없음          → [5], as_string False, {} (제출 때 duration 을 보내지 않는다)
    """
    spec = _unwrap(props.get("duration"))
    if not spec:
        return [5], False, {}
    enum = spec.get("enum")
    if isinstance(enum, list) and enum:
        values: list[int] = []
        value_map: dict = {}
        as_string = False
        for item in enum:
            if isinstance(item, str):
                as_string = True
                digits = "".join(ch for ch in item if (ch.isdigit() or ch == "."))
                if not digits:
                    continue
                number = int(float(digits))
                value_map[str(number)] = item
                values.append(number)
            elif isinstance(item, (int, float)):
                values.append(int(item))
                value_map[str(int(item))] = item
        values = sorted(set(values))
        if values:
            return values, as_string, value_map
    lo = spec.get("minimum")
    hi = spec.get("maximum")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        low = max(1, int(lo) if float(lo).is_integer() else int(lo) + 1)
        return list(range(low, int(hi) + 1)) or [int(hi)], False, {}
    if isinstance(hi, (int, float)):
        return [int(hi)], False, {}
    return [5], False, {}


def _audio_field(props: dict) -> str:
    for name in _AUDIO_FIELDS:
        spec = _unwrap(props.get(name))
        if spec.get("type") == "boolean":
            return name
    return ""


def _keep_audio_field(props: dict) -> str:
    """keep_audio 류 boolean 필드(video-to-video). 없으면 빈 문자열."""
    for name in _KEEP_AUDIO_FIELDS:
        spec = _unwrap(props.get(name))
        if spec.get("type") == "boolean":
            return name
    return ""


def _array_item_props(spec: dict, root: dict) -> dict:
    """배열 필드의 items 스키마 properties(ref 풀어서). 배열이 아니면 빈 dict."""
    spec = _unwrap(spec)
    if spec.get("type") != "array":
        return {}
    items = _deref(spec.get("items") or {}, root)
    props = items.get("properties") if isinstance(items, dict) else None
    return props if isinstance(props, dict) else {}


def _elements_field(props: dict, root: dict) -> str:
    """elements 배열 필드(각 항목에 정면 이미지 필드가 있는 배열). 없으면 빈 문자열.

    설계자 2026-10-22: Kling O3 처럼 인물별 참고를 elements[{frontal_image_url, ...}] 로 받는
    모델을 가린다. 후보 이름(_ELEMENTS_FIELDS) 중 배열이고 item properties 에 정면 이미지 필드가
    있는 것을 고른다.
    """
    for name in _ELEMENTS_FIELDS:
        if name not in props:
            continue
        item_props = _array_item_props(props.get(name), root)
        if any(field in item_props for field in _ELEMENT_ITEM_IMAGE_FIELDS):
            return name
    return ""


def _image_list_field(props: dict) -> str:
    """참고 이미지 목록(문자열 배열) 필드. 없으면 빈 문자열."""
    for name in _IMAGE_LIST_FIELDS:
        spec = _unwrap(props.get(name))
        if spec.get("type") == "array":
            return name
    return ""


def _image_list_max(props: dict, field: str) -> int:
    """imageListField 의 maxItems(없으면 DEFAULT_IMAGE_LIST_MAX)."""
    if not field:
        return DEFAULT_IMAGE_LIST_MAX
    spec = _unwrap(props.get(field))
    value = spec.get("maxItems")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_IMAGE_LIST_MAX


def _video_list_field(props: dict) -> str:
    """참조 영상 목록(문자열 배열) 필드(reference-to-video). 없으면 빈 문자열.

    Seedance 2.5 의 video_urls 처럼 @Video1.. 로 참조하는 배열 영상 입력을 가린다.
    """
    for name in _VIDEO_LIST_FIELDS:
        spec = _unwrap(props.get(name))
        if spec.get("type") == "array":
            return name
    return ""


def _video_list_max(props: dict, field: str) -> int:
    """videoListField 의 maxItems(없으면 DEFAULT_VIDEO_LIST_MAX)."""
    if not field:
        return DEFAULT_VIDEO_LIST_MAX
    spec = _unwrap(props.get(field))
    value = spec.get("maxItems")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_VIDEO_LIST_MAX


def _task_field(props: dict) -> tuple[str, list]:
    """task(enum) 필드 이름과 허용값 목록. 없으면 ("", []).

    Seedance 2.5: task ∈ {'auto','reference','editing','extension'}.
    """
    if "task" not in props:
        return "", []
    values = [str(item) for item in _enum_of(props, "task")]
    if not values:
        return "", []
    return "task", values


def _bool_field(props: dict, name: str) -> str:
    """name 이 boolean 필드면 name, 아니면 빈 문자열."""
    spec = _unwrap(props.get(name))
    return name if spec.get("type") == "boolean" else ""


def _seed_field(props: dict) -> str:
    """seed(정수) 필드 이름. 없으면 빈 문자열."""
    spec = _unwrap(props.get("seed"))
    return "seed" if spec.get("type") in ("integer", "number") else ""


def extract_caps(openapi: dict, endpoint: str = "") -> tuple[dict | None, str]:
    """OpenAPI 입력 스키마에서 우리가 쓰는 필드를 뽑아 caps 로 만든다.

    못 찾으면 (None, 이유). 찾으면 (caps, ""). required 중 우리가 채울 수 없는 것이 있으면
    caps 는 만들되 available=False 와 reason(필드명)을 담는다.
    """
    input_schema = _find_input_schema(openapi, endpoint)
    props = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(props, dict) or not props:
        return None, "입력 스키마(properties)를 찾지 못했습니다."

    prompt_field = "prompt" if "prompt" in props else ""
    prompt_max = _prompt_max_length(props, prompt_field)
    start_field = _first_present(props, _START_IMAGE_FIELDS)
    end_field = _first_present(props, _END_IMAGE_FIELDS)
    reference_field = _first_present(props, _REFERENCE_FIELDS)
    durations, duration_as_string, duration_values = _durations_from(props)
    has_duration = _unwrap(props.get("duration")) != {}

    # 해상도: 스키마 원래 표기(대소문자 섞임)를 보관하고, 화면·매칭은 소문자로 정규화한다.
    raw_resolutions = [str(item) for item in _enum_of(props, "resolution")]
    resolution_values = {value.lower(): value for value in raw_resolutions}
    resolutions = [value.lower() for value in raw_resolutions]
    aspect_ratios = [str(item) for item in _enum_of(props, "aspect_ratio")]
    audio_field = _audio_field(props)

    # video-to-video(프리비즈 방식, 설계자 2026-10-22) 필드.
    video_field = _first_present(props, _VIDEO_FIELDS)
    elements_field = _elements_field(props, openapi)
    image_list_field = _image_list_field(props)
    image_list_max = _image_list_max(props, image_list_field)
    keep_audio_field = _keep_audio_field(props)
    # reference-to-video(Seedance 2.5, Claude 조사 2026-10-25) 필드.
    video_list_field = _video_list_field(props)
    video_list_max = _video_list_max(props, video_list_field)
    task_field, task_values = _task_field(props)
    draft_field = _bool_field(props, "draft")
    generate_audio_field = _bool_field(props, "generate_audio")
    seed_field = _seed_field(props)
    # 프리비즈(영상 입력 → 그림체)로 쓸 수 있는 모델: 영상 입력(단일 videoField 또는 배열
    # videoListField) + 프롬프트가 둘 다 있어야 한다.
    previz_capable = bool((video_field or video_list_field) and prompt_field)

    required = input_schema.get("required")
    required = [str(name) for name in required] if isinstance(required, list) else []

    # 우리가 채울 수 있는 필드 집합. required 가 이 밖이면 등록은 되지만 available=False.
    fillable = {
        name
        for name in (
            prompt_field,
            start_field,
            end_field,
            reference_field,
            audio_field,
            video_field,
            elements_field,
            image_list_field,
            keep_audio_field,
            video_list_field,
            task_field,
            draft_field,
            generate_audio_field,
            seed_field,
        )
        if name
    }
    fillable |= {"duration", "resolution", "aspect_ratio"}
    # 스키마에 default 가 있는 required 는 우리가 안 보내도 fal 이 기본값을 쓰므로 충족으로 본다
    # (예: MiniMax 의 prompt_expansion_mode 는 required 이지만 default "balanced").
    def _has_default(name: str) -> bool:
        spec = props.get(name)
        if isinstance(spec, dict) and "default" in spec:
            return True
        return "default" in _unwrap(spec)

    unmet = [name for name in required if name not in fillable and not _has_default(name)]

    caps = {
        "promptField": prompt_field,
        "promptMaxLength": prompt_max,
        "startImageField": start_field,
        "endImageField": end_field,
        "referenceImagesField": reference_field,
        "durations": durations,
        "durationAsString": duration_as_string,
        # 정수 초 → 스키마 원래 표기("4"→"4s"). durationAsString 일 때 제출에 쓴다.
        "durationValues": duration_values,
        # duration 필드가 아예 없는 모델(Wan)은 제출 때 duration 을 보내지 않는다.
        "sendDuration": has_duration,
        "resolutions": resolutions,
        # 소문자 → 스키마 원래 표기("1080p"→"1080P"). 제출에 쓴다.
        "resolutionValues": resolution_values,
        "aspectRatios": aspect_ratios,
        "audioField": audio_field,
        # video-to-video(프리비즈 방식) 필드(설계자 2026-10-22).
        "videoField": video_field,
        "elementsField": elements_field,
        "imageListField": image_list_field,
        "imageListMax": image_list_max,
        "keepAudioField": keep_audio_field,
        # reference-to-video(Seedance 2.5) 필드(Claude 조사 2026-10-25).
        "videoListField": video_list_field,
        "videoListMax": video_list_max,
        "taskField": task_field,
        "taskValues": task_values,
        "draftField": draft_field,
        "generateAudioField": generate_audio_field,
        "seedField": seed_field,
        "previzCapable": previz_capable,
        "requiredFields": required,
        "available": not unmet,
        "reason": ("채울 수 없는 필수 입력: " + ", ".join(unmet)) if unmet else "",
    }
    return caps, ""


def unavailable_reason(model: str) -> str:
    """고를 수 없으면 그 이유(화면에 그대로 보인다). 고를 수 있으면 빈 문자열."""
    if not settings.FAL_KEY:
        return "fal API 키 미설정"
    item = find(model)
    if not item:
        return "등록되지 않은 모델"
    caps = item.get("caps") or {}
    if not caps.get("available", True):
        return str(caps.get("reason") or "모델 입력 스키마를 쓸 수 없습니다.")
    # 금지 목록 재검사(073 R10.4, D10, E4). 등록 때만 보던 것을 고를 때·제출 때도 본다 — 금지 패턴이
    # 정해지기 전에 등록된 항목이 레지스트리에 남아 있어도 쓸 수 없다(리뷰 S-2). 문구는 register 와 같다.
    if settings.is_denied_endpoint(str(item.get("endpointId") or endpoint_id(model))):
        return "설계자 지시로 사용이 금지된 모델입니다."
    # Seedance 계열 얼굴(i2v)은 짝(r2v)까지 등록·사용 가능해야 고를 수 있다(073 D2, R2.2 E1·E1b).
    # 짝 자신은 짝 요구가 없다(fal_family_partner 가 빈 값) — vnew 의 r2v 단독 사용은 그대로다.
    partner = settings.fal_family_partner(model)
    if partner:
        partner_item = find(partner)
        if not partner_item:
            return "짝 모델(reference-to-video)이 등록되지 않았습니다. 관리자에게 fal 모델 등록을 요청해 주세요."
        partner_caps = partner_item.get("caps") or {}
        if not partner_caps.get("available", True):
            return "짝 모델(reference-to-video)을 쓸 수 없습니다: " + str(
                partner_caps.get("reason") or "모델 입력 스키마를 쓸 수 없습니다."
            )
    return ""


def route_model(model: str, *, has_references: bool, has_start: bool) -> str:
    """제출 때 실제로 보낼 모델(design.md §4, 073 R2.3).

    Seedance 계열 얼굴(i2v)일 때만 바꾼다. 참고 이미지가 있으면 짝(r2v) — 시작 프레임이 있어도
    참고가 우선이다(BytePlus 와 같은 우선순위). 참고 없이 시작 프레임만 있으면 얼굴(i2v). 둘 다
    없으면(글만) 짝(r2v). 계열이 아니면(짝이 없으면) model 그대로.
    """
    partner = settings.fal_family_partner(model)
    if not partner:
        return model
    if has_references:
        return partner
    if has_start:
        return model
    return partner


def reference_caps(model: str) -> dict:
    """참고 방식을 정할 caps(073 R2.3). 계열 얼굴이면 짝 caps, 아니면 자기 caps(없으면 {})."""
    partner = settings.fal_family_partner(model)
    if partner:
        return dict((find(partner) or {}).get("caps") or {})
    return dict((find(model) or {}).get("caps") or {})


# ---------------------------------------------------------------- 제출 / 조회


def _data_uri(b64: str) -> str:
    return "data:image/jpeg;base64," + b64


def _pick_duration(caps: dict, requested: int) -> object:
    """요청값 이상인 가장 작은 duration(없으면 최댓값).

    durationAsString 이면 스키마 원래 표기로 돌려준다("8" → "8s", "5" → "5"). durationValues 가
    비어 있으면(이론상) 정수 문자열. 그 밖에는 정수.
    """
    durations = sorted(int(item) for item in (caps.get("durations") or [5]))
    chosen = next((value for value in durations if value >= requested), durations[-1])
    if caps.get("durationAsString"):
        values = caps.get("durationValues") or {}
        return values.get(str(chosen), str(chosen))
    return chosen


def build_payload(
    caps: dict,
    *,
    prompt: str,
    keyframe_jpeg_b64: str = "",
    last_frame_jpeg_b64: str = "",
    reference_jpegs_b64: list[str] | None = None,
    resolution: str = "",
    duration_s: int | None = None,
    prompt_max: int | None = None,
) -> dict:
    """caps 로 fal 큐 제출 본문을 만든다(네트워크 없이 검증할 수 있게 submit 에서 분리).

    - prompt → promptField. 상한(UTF-8 바이트)은 prompt_max 를 주면 그 값(073 R5.1, settings.fal_prompt_max),
      없으면 caps promptMaxLength(없으면 2,500). 넘으면 문장 경계에서 자른다.
    - 첫 프레임 → startImageField, 끝 프레임 → endImageField(둘 다 있을 때만)
    - 참고 이미지 → referenceImagesField 가 있으면 그 배열, 없고 첫 프레임도 없으면 첫 장을 startImageField 로
    - duration → durations 중 요청값 이상인 가장 작은 값(durationAsString 이면 원래 표기). sendDuration=False 면 생략
    - resolution → 소문자 매칭 후 스키마 원래 표기로 보낸다(그 enum 에 있을 때만)
    - aspect_ratio → aspectRatios 에 16:9 가 있으면 16:9
    - audioField 가 있으면 ARK_GENERATE_AUDIO
    """
    body: dict = {}
    if caps.get("promptField"):
        if prompt_max is not None and int(prompt_max) > 0:
            max_length = int(prompt_max)
        else:
            max_length = int(caps.get("promptMaxLength") or DEFAULT_PROMPT_MAX)
        sent = _trim_prompt(prompt, max_length)
        if sent != prompt:
            # 073 R5.2: 길이만 남긴다(프롬프트 본문은 로그에 남기지 않는다). from/to 는 글자 수(기존), *Bytes 는 바이트.
            log.info(
                "vpoc.fal.prompt_trimmed field=%s max=%d from=%d to=%d fromBytes=%d toBytes=%d",
                caps["promptField"],
                max_length,
                len(prompt),
                len(sent),
                len(prompt.encode("utf-8")),
                len(sent.encode("utf-8")),
            )
        body[caps["promptField"]] = sent

    references = list(reference_jpegs_b64 or [])
    start_field = caps.get("startImageField") or ""
    end_field = caps.get("endImageField") or ""
    reference_field = caps.get("referenceImagesField") or ""

    if keyframe_jpeg_b64 and start_field:
        body[start_field] = _data_uri(keyframe_jpeg_b64)
        if last_frame_jpeg_b64 and end_field:
            body[end_field] = _data_uri(last_frame_jpeg_b64)

    if references:
        if reference_field:
            body[reference_field] = [_data_uri(b64) for b64 in references]
        elif not keyframe_jpeg_b64 and start_field:
            body[start_field] = _data_uri(references[0])

    # 길이. duration 필드가 없는 모델(Wan)은 보내지 않는다.
    if caps.get("sendDuration", True):
        requested = int(duration_s or settings.DURATION_S)
        body["duration"] = _pick_duration(caps, requested)

    # 해상도. 우리 값(소문자)과 대소문자 무시로 맞추고, 스키마 원래 표기로 보낸다.
    resolution_values = caps.get("resolutionValues") or {}
    if resolution:
        original = resolution_values.get(resolution.lower())
        if original:
            body["resolution"] = original

    if "16:9" in (caps.get("aspectRatios") or []):
        body["aspect_ratio"] = "16:9"

    if caps.get("audioField"):
        body[caps["audioField"]] = bool(settings.ARK_GENERATE_AUDIO)

    return body


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
    """fal 큐에 작업을 넣고 즉시 돌아온다. ark.submit / video.submit 과 같은 키를 돌려준다."""
    item = find(model)
    if not item:
        raise VpocError("fal.submit", "ModelUnavailable", f"{model}: 등록되지 않은 모델", 400)
    reason = unavailable_reason(model)
    if reason:
        raise VpocError("fal.submit", "ModelUnavailable", f"{item.get('label') or model}: {reason}", 400)
    if not prompt.strip():
        raise VpocError("fal.submit", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)

    eid = item["endpointId"]
    caps = item.get("caps") or {}
    # 073 R5.1: 프롬프트 상한은 설정 덮어쓰기(Seedance 2.5 계열 4,000) → caps promptMaxLength → 2,500 순서.
    prompt_max = settings.fal_prompt_max(eid, caps.get("promptMaxLength") or DEFAULT_PROMPT_MAX)
    body = build_payload(
        caps,
        prompt=prompt,
        keyframe_jpeg_b64=keyframe_jpeg_b64,
        last_frame_jpeg_b64=last_frame_jpeg_b64,
        reference_jpegs_b64=reference_jpegs_b64,
        resolution=resolution,
        duration_s=duration_s,
        prompt_max=prompt_max,
    )
    # 073 R5.2: 실제로 보낸 문장과 잘린 크기(바이트). promptField 가 없는 모델은 문장을 보내지 않으므로 원문 그대로.
    prompt_field = str(caps.get("promptField") or "")
    sent_prompt = str(body.get(prompt_field) or "") if prompt_field else prompt
    prompt_trimmed = (
        {"fromBytes": len(prompt.encode("utf-8")), "toBytes": len(sent_prompt.encode("utf-8"))}
        if prompt_field and sent_prompt != prompt
        else None
    )

    started = time.time()
    result = _request("POST", f"{settings.FAL_QUEUE_URL}/{eid}", body, where="fal.submit")
    request_id = str(result.get("request_id") or "")
    log.info(
        "vpoc.call where=fal.submit endpoint=%s plan=%s shot=%s ms=%d ok=%s",
        eid,
        plan_id,
        shot_id,
        int((time.time() - started) * 1000),
        bool(request_id),
    )
    if not request_id:
        raise VpocError("fal.submit", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)

    prefix = video_output_prefix(owner, project_id, plan_id, shot_id, attempt, gen_id)
    return {
        "provider": model,
        # "fal/requests/<endpoint_id>/<request_id>". 마지막 조각(request_id)이 출력 폴더명이 된다.
        "invocationArn": f"{ARN_PREFIX}{eid}/{request_id}",
        # 원장 키. request_id 는 작업마다 유일하다(작업 하나당 1회 계상).
        "clientRequestToken": "fal-" + request_id,
        "outputPrefix": prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
        # 073 R5.2: 실제로 보낸 문장, 잘렸으면 {fromBytes, toBytes}(아니면 None).
        "sentPrompt": sent_prompt,
        "promptTrimmed": prompt_trimmed,
    }


def _video_data_uri(mp4: bytes) -> str:
    """mp4 바이트 → data URI. 우리 파일을 외부 저장소에 올리지 않는다(설계자 2026-10-20).

    fal video-to-video 는 data:video/mp4;base64,... 를 받는다(실측: 1.2MB 성공).
    """
    import base64

    return "data:video/mp4;base64," + base64.b64encode(mp4).decode("ascii")


def submit_edit(
    *,
    endpoint: str,
    video_mp4: bytes,
    prompt: str,
    output_prefix: str,
    reference_jpegs_b64: list[str] | None = None,
    keep_audio: bool = True,
) -> dict:
    """실사 영상 → 애니(웹툰풍) 변환을 fal 큐에 넣고 즉시 돌아온다(설계자 2026-10-20).

    등록 모델(registry)이 아니라 settings.VPOC_ANIME_MODEL 로 고정된 video-to-video edit
    endpoint 를 직접 부른다. submit/poll/_request/_queue_app/is_fal_job 과 같은 경로·반환 모양을
    쓴다(ARN 접두사 fal/requests/ 동일 → routes·ark 가 같은 조회·취소 경로를 탄다).

    - prompt 는 영상 @Video1, 참고 이미지 @Image1.. 로 지칭하는 영어 문장이어야 한다.
    - video_url: 우리 mp4 를 data URI 로 보낸다(외부 저장소에 올리지 않는다). 3~15초·높이 ≥720 은
      호출부(routes)가 stitcher 로 맞춰서 넘긴다.
    - image_urls: 화풍 참고 이미지(웹툰 장면·캐릭터) data URI 최대 4장. 없으면 생략.
    - keep_audio: 원본 소리 유지.
    returns: submit 과 같은 키(provider·invocationArn·clientRequestToken·outputPrefix·submittedAt·promptChars).
    """
    if not settings.FAL_KEY:
        raise VpocError("fal.submit_edit", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise VpocError("fal.submit_edit", "BadEndpoint", "변환 모델 endpoint 가 비어 있습니다.", 400)
    if not prompt.strip():
        raise VpocError("fal.submit_edit", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)
    if not video_mp4:
        raise VpocError("fal.submit_edit", "EmptyVideo", "변환할 영상이 비어 있습니다.", 400)

    body: dict = {
        "prompt": prompt,
        "video_url": _video_data_uri(video_mp4),
        "keep_audio": bool(keep_audio),
    }
    references = list(reference_jpegs_b64 or [])
    if references:
        body["image_urls"] = [_data_uri(b64) for b64 in references]

    started = time.time()
    result = _request("POST", f"{settings.FAL_QUEUE_URL}/{endpoint}", body, where="fal.submit_edit")
    request_id = str(result.get("request_id") or "")
    log.info(
        "vpoc.call where=fal.submit_edit endpoint=%s ms=%d ok=%s",
        endpoint,
        int((time.time() - started) * 1000),
        bool(request_id),
    )
    if not request_id:
        raise VpocError("fal.submit_edit", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)

    return {
        "provider": settings.FAL_ID_PREFIX + endpoint,
        "invocationArn": f"{ARN_PREFIX}{endpoint}/{request_id}",
        "clientRequestToken": "fal-anime-" + request_id,
        "outputPrefix": output_prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
    }


def _parse_arn(invocation_arn: str) -> tuple[str, str]:
    """"fal/requests/<endpoint_id>/<request_id>" → (endpoint_id, request_id).

    endpoint_id 자체가 슬래시를 포함하므로 마지막 조각만 request_id 로 떼고 나머지가 endpoint_id.
    """
    rest = invocation_arn[len(ARN_PREFIX):]
    if "/" not in rest:
        return "", rest
    eid, request_id = rest.rsplit("/", 1)
    return eid, request_id


def _queue_app(endpoint: str) -> str:
    """큐 조회 주소는 앱 ID(앞 두 마디)만 쓴다 — 실측 2026-10-19: 전체 경로는 405, `fal-ai/kling-video` 는 200."""
    return "/".join(endpoint.split("/")[:2])


def _status_url(endpoint: str, request_id: str) -> str:
    return f"{settings.FAL_QUEUE_URL}/{_queue_app(endpoint)}/requests/{request_id}/status"


def _response_url(endpoint: str, request_id: str) -> str:
    return f"{settings.FAL_QUEUE_URL}/{_queue_app(endpoint)}/requests/{request_id}"


def _video_url_from(result: dict) -> str:
    """결과에서 영상 URL 을 찾는다: video.url → videos[0].url → 응답 안의 첫 .mp4 url."""
    if not isinstance(result, dict):
        return ""
    video = result.get("video")
    if isinstance(video, dict) and isinstance(video.get("url"), str):
        return video["url"]
    videos = result.get("videos")
    if isinstance(videos, list) and videos and isinstance(videos[0], dict):
        url = videos[0].get("url")
        if isinstance(url, str):
            return url
    # 응답 전체에서 첫 .mp4 url 을 재귀로 찾는다.
    found: list[str] = []

    def walk(node) -> None:
        if found:
            return
        if isinstance(node, str) and node.lower().split("?")[0].endswith(".mp4"):
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    return found[0] if found else ""


# 073 R15(작업 7): 결과 조회가 이 kind 로 실패하면 fal 이 요청을 거절한 것으로 본다. fal 은 거절 건의
# status 를 COMPLETED 로 주고 결과 조회를 4xx 로 돌려준다(유료 검증 V-2 실측 2026-10-26). 402·잔액 403 은
# _request 가 FalBalanceExhausted 로 바꾸므로 여기 들지 않는다(예외 유지). 5xx·네트워크도 예외 유지.
_REJECT_HTTP = frozenset({"HTTP400", "HTTP403", "HTTP404", "HTTP422"})

_REJECT_LIKENESS_MSG = (
    "fal이 실존 인물과 닮았을 수 있는 이미지로 판단해 이 샷을 거절했습니다. "
    "같은 이미지도 다시 시도하면 통과할 때가 있습니다. "
    "'다시 만들기'로 다시 시도하거나 인물 이미지를 바꿔 주세요."
)
_REJECT_SENSITIVE_MSG = (
    "fal이 결과 영상에 민감한 내용이 있다고 판단해 거절했습니다. 장면 설명을 바꿔 다시 만들어 주세요."
)
# vnew-live-fx C.6: 소리 검사 거절("Output audio has sensitive content."). 영상 내용 거절과 문구를 나눠 화면이
# '소리 없이 다시 만들기'를 보이게 한다. vpoc ②도 같은 문구를 받는다(결정 8).
_REJECT_AUDIO_MSG = (
    "fal이 결과 영상의 소리(대사·효과음)에 민감한 내용이 있다고 판단해 거절했습니다. "
    "'소리 없이 다시 만들기'로 다시 만들 수 있습니다."
)


def _is_audio_rejection(text) -> bool:
    """원문에 audio 와 sensitive 가 둘 다 있으면 소리 검사 거절로 본다(fal 문구가 바뀌어도 두 낱말만 본다)."""
    low = str(text or "").lower()
    return "sensitive" in low and "audio" in low


def rejection(detail: str, http: str = "") -> tuple[str, str]:
    """fal 거절 본문 → (종류, 사용자 문구). 종류는 likeness · audio · sensitive · other(design.md §13.3, vnew-live-fx C.6).

    문구에는 영어 원문 'real person' 이 없다 — vnew `_user_facing_error` 를 지나도 바뀌지 않는다.
    other 는 원문 요지(error_gist, 200자 — URL 쿼리·data URI·키 제거)를 붙인다.
    """
    low = str(detail or "").lower()
    if "likenesses of real people" in low or "real person" in low:
        return "likeness", _REJECT_LIKENESS_MSG
    if _is_audio_rejection(low):
        return "audio", _REJECT_AUDIO_MSG
    if "sensitive content" in low:
        return "sensitive", _REJECT_SENSITIVE_MSG
    gist = error_gist(detail, 200) or str(http or "").strip() or "알 수 없는 오류"
    return "other", "fal이 요청을 거절했습니다: " + gist


def poll(invocation_arn: str, output_prefix: str) -> dict:
    """작업 상태. 완료면 결과 영상을 우리 버킷 `{prefix}/{request_id}/output.mp4` 로 옮긴 뒤 Completed.

    ark.poll 과 같은 반환 모양: status(InProgress/Completed/Failed) · failureMessage · usage.
    """
    endpoint, request_id = _parse_arn(invocation_arn)
    status_result = _request("GET", _status_url(endpoint, request_id), where="fal.poll")
    raw_status = str(status_result.get("status") or "").upper()

    if raw_status in ("IN_QUEUE", "IN_PROGRESS"):
        return {"status": "InProgress", "failureMessage": "", "usage": None}

    if raw_status != "COMPLETED":
        # 알 수 없는 상태나 오류. status 응답의 error 를 원문 그대로 싣는다.
        message = _error_detail(json.dumps(status_result, ensure_ascii=False)) or raw_status
        if _is_audio_rejection(message):
            # vnew-live-fx C.6: 소리 검사 거절이면 원문 대신 안내 문구와 failureKind. 그 밖은 지금 dict 그대로.
            log.info("vpoc.fal.rejected request=%s kind=audio http=-", request_id)
            return {"status": "Failed", "failureMessage": _REJECT_AUDIO_MSG, "usage": None, "failureKind": "audio"}
        return {"status": "Failed", "failureMessage": message or "상태가 비어 있습니다.", "usage": None}

    # 완료 — 결과 본문을 받는다.
    try:
        result = _request("GET", _response_url(endpoint, request_id), where="fal.poll")
    except VpocError as exc:
        # 073 R15.1: 거절(COMPLETED + 결과 4xx)은 다시 조회해도 같다 — 예외 대신 Failed 로 끝낸다.
        if exc.kind not in _REJECT_HTTP:
            raise
        kind, message = rejection(exc.message, exc.kind)
        # 원문·프롬프트·키는 남기지 않는다(R15.4). 요청 id·종류·코드만.
        log.info(
            "vpoc.fal.rejected request=%s kind=%s http=%s",
            request_id,
            kind,
            exc.kind[len("HTTP"):],
        )
        if kind == "audio":
            return {"status": "Failed", "failureMessage": message, "usage": None, "failureKind": "audio"}
        return {"status": "Failed", "failureMessage": message, "usage": None}
    if isinstance(result, dict) and result.get("error"):
        if _is_audio_rejection(result.get("error")):
            log.info("vpoc.fal.rejected request=%s kind=audio http=-", request_id)
            return {"status": "Failed", "failureMessage": _REJECT_AUDIO_MSG, "usage": None, "failureKind": "audio"}
        return {
            "status": "Failed",
            "failureMessage": str(result.get("error"))[:500],
            "usage": None,
        }
    url = _video_url_from(result)
    if not url:
        return {"status": "Failed", "failureMessage": "완료됐지만 영상 URL 이 없습니다.", "usage": None}

    key = f"{output_prefix.rstrip('/')}/{request_id}/output.mp4"
    if not STORE.exists(key, bucket=settings.VIDEO_BUCKET):
        started = time.time()
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as response:
            STORE.put_bytes(key, response.read(), "video/mp4", bucket=settings.VIDEO_BUCKET)
        log.info("vpoc.call where=fal.copy request=%s ms=%d", request_id, int((time.time() - started) * 1000))

    return {"status": "Completed", "failureMessage": "", "usage": None}


def cancel(invocation_arn: str) -> None:
    """진행 중 작업 취소(설계자 2026-10-20). PUT {queue}/{app}/requests/{request_id}/cancel.

    fal 은 **대기열에 있을 때만** 취소한다 — 이미 생성 중이면 거절한다(그래도 우리 쪽은 cancelled 로
    둔다). 거절·오류면 VpocError 를 올리고(호출부가 cancelRemote="rejected" 로 적는다), 성공이면
    조용히 돌아온다. 큐 조회와 같은 앱 경로(_queue_app)를 쓴다.
    """
    endpoint, request_id = _parse_arn(invocation_arn)
    url = f"{settings.FAL_QUEUE_URL}/{_queue_app(endpoint)}/requests/{request_id}/cancel"
    _request("PUT", url, where="fal.cancel")
