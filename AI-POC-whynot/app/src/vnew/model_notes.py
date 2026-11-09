"""작업 198: 모델 노트 — 영상·그림 모델이 거절하거나 실패한 기록(gen 하나에 한 파일).

왜: 생성 상태에는 칸·조각·컷·다시 만들기 버전마다 마지막 error·failureKind 하나만 남고, 나중에 성공하면 error "" 로 덮인다.
공급자 원문·몇 번째 시도였는지·그 뒤에 무엇을 했는지(순화·참고 빼기·대체 모델)는 파드 로그에만 있었다.

저장: `{gen 루트}/model-notes.json` = {"version": 1, "notes": [노트, …]}(retakes.json 옆). 더하기만 하고 새것 NOTES_MAX 개만 남긴다.
쓰기는 retakes.json·prompts.json 과 같은 읽고-고치고-조건부 쓰기(etag 경합이면 다시 읽기, retake._mutate_log — ⑤ 제출 경로의
저장소 generate.STORE)와 프로세스 안 잠금(edit._lock)이라 동시에 도는 조각이 노트를 잃지 않는다.

노트 = 글만: {at, variant, stage, target{takeId?, partNo?, cutIds?, retakeCell?, version?}, model, attempt, kind, providerMessage,
reasonKo, action, resolved, source}. providerMessage 는 sanitize(주소·data URI·base64·토큰·키를 지운다, MESSAGE_MAX 자).
그림·프롬프트 본문·주소는 남기지 않는다. 거절을 설명하는 데 꼭 필요할 때만 excerpt(EXCERPT_MAX 자, 기본 없음).
kind·reasonKo 는 규칙(LLM 없음): 콘텐츠 검사 낱말은 generate.CONTENT_FLAG_WORDS(_content_kind) 한 곳을 쓴다.

record 는 생성을 막지도 늦추지도 않는다: 예외를 모두 잡아 경고 1줄만 남기고, 기존 상태 파일은 건드리지 않는다. 원장과 무관하다.
VNEW_MODEL_NOTES 가 꺼져 있으면 쓰지 않고 GET 은 404(ModelNotesDisabled).

보기(view): 파일에 노트가 없으면 지금 상태(gen 칸·조각·컷, retakes.json 버전의 error·failureKind)에서 한 번 만들어 보여 준다
(source "state", 저장하지 않는다). resolved 는 보기 때 지금 상태로 정한다 — 같은 대상(테이크·조각·컷·다시 만들기 버전)이 지금
completed 면 True.
"""

from __future__ import annotations

import logging
import re

from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, now_iso

from . import settings as vnew_settings

log = logging.getLogger("vpoc")

FILE_NAME = "model-notes.json"
VERSION = 1
NOTES_MAX = 500
MESSAGE_MAX = 600
EXCERPT_MAX = 200
MODEL_MAX = 200
CUT_IDS_MAX = 40
ID_MAX = 120

KIND_CONTENT = "contentFlag"
KIND_PROMPT_SIZE = "promptSize"
KIND_TIMEOUT = "timeout"
KIND_BAD_REQUEST = "badRequest"
KIND_PROVIDER = "provider"
KIND_QC = "qc"
KIND_OTHER = "other"
KINDS = (KIND_CONTENT, KIND_PROMPT_SIZE, KIND_TIMEOUT, KIND_BAD_REQUEST, KIND_PROVIDER, KIND_QC, KIND_OTHER)
ACTION_FALLBACK = "fallbackModel:"
ACTIONS = ("softRetry", "dropRefs", "dialogueRewrite", "gaveUp", "retry", "")
VARIANTS = ("webtoon", "real")
SOURCE_LIVE = "live"
SOURCE_STATE = "state"
IMAGE_STAGES = ("still", "costume", "opening")  # 그림 모델(Seedream) 단계 — reasonKo 가 "그림 모델"이라 쓴다

# ---------------------------------------------------------------- reasonKo(규칙, LLM 없음)
REASON_CONTENT = "{who}의 콘텐츠 검사가 이 장면(키스·신체 접촉·노출·폭력 등)을 민감한 내용으로 판단해 거절했습니다."
REASON_CONTENT_VIDEO = "{who}의 콘텐츠 검사가 프롬프트 글이 아니라 입력 영상(@Video1 등)을 민감한 내용으로 판단해 거절했습니다."
REASON_CONTENT_IMAGE = "{who}의 콘텐츠 검사가 프롬프트 글이 아니라 입력 이미지(참고·시작 그림)를 민감한 내용으로 판단해 거절했습니다."
REASON_LIKENESS = "{who}이 프롬프트 글이 아니라 입력 이미지(인물 사진)가 실존 인물과 닮았다고 판단해 거절했습니다."
REASON_AUDIO = "{who}의 콘텐츠 검사가 만들어진 소리(대사·효과음)를 민감한 내용으로 판단해 거절했습니다."
REASON_PROMPT_SIZE = "프롬프트가 모델의 길이 한도({n})를 넘었습니다."
REASON_PROMPT_SIZE_UNKNOWN = "프롬프트가 모델의 길이 한도를 넘었습니다."
REASON_TIMEOUT = "모델이 제한 시간 안에 결과를 주지 않았습니다."
REASON_QC = "결과 검사(화면 글자·멈춘 화면 등)에서 문제가 보였습니다."
REASON_BAD_REQUEST = "모델이 요청(형식·값)을 받아들이지 않았습니다{http}."
REASON_PROVIDER = "모델 쪽에서 오류가 나 결과를 받지 못했습니다{http}."
REASON_OTHER = "모델 호출이 실패했습니다{http}."
WHO_VIDEO = "영상 모델"
WHO_IMAGE = "그림 모델"

# 콘텐츠 검사가 아닌 분류 낱말(소문자로 본다). 콘텐츠 검사 낱말은 generate.CONTENT_FLAG_WORDS 한 곳.
PROMPT_SIZE_WORDS = ("prompt is too long", "prompt too long", "prompt length", "prompt exceeds", "too many characters",
                     "maximum prompt", "프롬프트가 너무 길", "프롬프트 길이")
TIMEOUT_WORDS = ("timeout", "timed out", "time out", "time-out", "deadline exceeded", "시간 초과", "시간이 초과",
                 "넘게 받지 못했", "시간 안에")
LIKENESS_WORDS = ("likeness", "real person", "real people", "실존 인물")
INPUT_VIDEO_WORDS = ("@video", "input video", "video_url", "video url", "reference video", "source video", "입력 영상",
                     "참고 영상")
INPUT_IMAGE_WORDS = ("@image", "input image", "image_url", "image url", "reference image", "start image", "first frame",
                     "keyframe", "입력 이미지", "시작 이미지", "참고 이미지")
PROVIDER_WORDS = ("fal", "seedance", "seedream", "kling", "bytedance")
_HTTP_RE = re.compile(r"\bHTTP\s*(\d{3})\b", re.IGNORECASE)
_STATUS_RE = re.compile(r"\bstatus(?:\s*code)?\s*[:=]?\s*(\d{3})\b", re.IGNORECASE)
_GEN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")

# ---------------------------------------------------------------- 정리(sanitize)
OMIT_URL = "[주소 생략]"
OMIT_DATA = "[데이터 생략]"
OMIT_LONG = "[긴 값 생략]"
OMIT_SECRET = "***"
_DATA_URI_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+(?:;[\w=.+-]+)*,[^\s\"'<>]*", re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?|s3|ftp|wss?)://[^\s\"'<>]+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?key(?:[_-]?id)?|secret(?:[_-]?key)?|session[_-]?token|token|password"
    r"|signature|credential|x-amz-[\w-]+|key)(\s*[:=]\s*)([\"']?)[^\s\"'&,;]{6,}\3")
_BEARER_RE = re.compile(r"\b(Bearer|Key)\s+(?=\S*\d)[A-Za-z0-9._~+/:=-]{16,}")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_LONG_RE = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SPACES_RE = re.compile(r"\s+")


def _long_value(match: re.Match) -> str:
    """40자 넘는 한 덩어리: base64(대·소문자·숫자가 섞임)·긴 16진 값이면 지운다. 모델 id(소문자·/·-)는 그대로."""
    text = match.group(0)
    mixed = any(c.isupper() for c in text) and any(c.islower() for c in text) and any(c.isdigit() for c in text)
    hexy = re.fullmatch(r"[0-9a-fA-F]{32,}", text) is not None
    return OMIT_LONG if mixed or hexy else text


def sanitize(value, limit: int = MESSAGE_MAX) -> str:
    """한 줄 글: 비밀 키 값·data URI·주소(URL)·base64·토큰·키를 지우고 제어 문자·공백을 한 칸으로, limit 자로."""
    text = str(value or "")
    if not text:
        return ""
    for name in ("FAL_KEY", "ARK_API_KEY"):
        secret = str(getattr(vpoc_settings, name, "") or "")
        if len(secret) >= 6:
            text = text.replace(secret, OMIT_SECRET)
    text = _DATA_URI_RE.sub(OMIT_DATA, text)
    text = _URL_RE.sub(OMIT_URL, text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + " " + OMIT_SECRET, text)
    text = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + OMIT_SECRET, text)
    text = _AWS_KEY_RE.sub(OMIT_SECRET, text)
    text = _LONG_RE.sub(_long_value, text)
    text = _SPACES_RE.sub(" ", _CONTROL_RE.sub(" ", text)).strip()
    return text[: max(0, int(limit))]


# ---------------------------------------------------------------- 분류 · 한국어 이유


def http_status(message) -> int:
    """문구 속 HTTP 상태(HTTP422 · status 500). 없으면 0."""
    text = str(message or "")
    match = _HTTP_RE.search(text) or _STATUS_RE.search(text)
    try:
        return int(match.group(1)) if match else 0
    except (TypeError, ValueError):
        return 0


def _flags(message, hint: str = "") -> dict:
    """원문에서 읽은 단서: content·audio·likeness·size(N)·timeout. 콘텐츠 검사는 generate._content_kind(CONTENT_FLAG_WORDS)."""
    from . import generate  # noqa: PLC0415 — generate 가 이 모듈을 import 한다(순환 피함)

    text = str(message or "")
    low = text.lower()
    hint = str(hint or "")
    audio = hint == "audio" or bool(generate._audio_kind(text))  # noqa: SLF001
    likeness = hint == "likeness" or any(word in low for word in LIKENESS_WORDS)
    content = hint == KIND_CONTENT or bool(generate._content_kind(text))  # noqa: SLF001
    size = generate._prompt_size_cap(text)  # noqa: SLF001
    return {"audio": audio, "likeness": likeness, "content": content, "size": size,
            "sizeWords": any(word in low for word in PROMPT_SIZE_WORDS),
            "timeout": hint == KIND_TIMEOUT or any(word in low for word in TIMEOUT_WORDS) or http_status(text) == 504,
            "video": any(word in low for word in INPUT_VIDEO_WORDS), "image": any(word in low for word in INPUT_IMAGE_WORDS)}


def classify(message, hint: str = "") -> str:
    """kind: hint(상태의 failureKind 등) + 원문 낱말 → KINDS 하나. 콘텐츠 검사·소리·likeness 거절은 contentFlag."""
    flags = _flags(message, hint)
    hint = str(hint or "")
    if flags["content"] or flags["audio"] or flags["likeness"]:
        return KIND_CONTENT
    if hint == KIND_PROMPT_SIZE or flags["size"] or flags["sizeWords"]:
        return KIND_PROMPT_SIZE
    if flags["timeout"]:
        return KIND_TIMEOUT
    if hint in (KIND_QC, KIND_BAD_REQUEST, KIND_PROVIDER):
        return hint
    status = http_status(message)
    if status in (400, 404, 405, 409, 413, 415, 422):
        return KIND_BAD_REQUEST
    if status >= 500 or status in (401, 402, 403, 429):
        return KIND_PROVIDER
    low = str(message or "").lower()
    if any(word in low for word in PROVIDER_WORDS):
        return KIND_PROVIDER
    return KIND_OTHER


def reason_ko(kind: str, message, *, stage: str = "", hint: str = "", limit: int = 0) -> str:
    """kind + 원문 낱말 → 왜 그랬는지 짧은 한국어 한 문장(규칙). limit = 알고 있는 프롬프트 한도(없으면 원문에서 읽는다)."""
    flags = _flags(message, hint)
    who = WHO_IMAGE if stage in IMAGE_STAGES else WHO_VIDEO
    status = http_status(message)
    http = f"(HTTP {status})" if status else ""
    if kind == KIND_CONTENT:
        if flags["likeness"]:
            return REASON_LIKENESS.format(who=who)
        if flags["audio"]:
            return REASON_AUDIO.format(who=who)
        if flags["video"]:
            return REASON_CONTENT_VIDEO.format(who=who)
        if flags["image"]:
            return REASON_CONTENT_IMAGE.format(who=who)
        return REASON_CONTENT.format(who=who)
    if kind == KIND_PROMPT_SIZE:
        n = int(limit or 0) or int(flags["size"] or 0)
        return REASON_PROMPT_SIZE.format(n=n) if n > 0 else REASON_PROMPT_SIZE_UNKNOWN
    if kind == KIND_TIMEOUT:
        return REASON_TIMEOUT
    if kind == KIND_QC:
        return REASON_QC
    if kind == KIND_BAD_REQUEST:
        return REASON_BAD_REQUEST.format(http=http)
    if kind == KIND_PROVIDER:
        return REASON_PROVIDER.format(http=http)
    return REASON_OTHER.format(http=http)


# ---------------------------------------------------------------- 노트 한 줄


def _int(value) -> int:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else 0
    except (TypeError, ValueError):
        return 0


def _target(value) -> dict:
    """대상은 정한 키만(글은 정리 · 길이 상한, 번호는 정수)."""
    value = value if isinstance(value, dict) else {}
    out: dict = {}
    for name in ("takeId", "retakeCell"):
        text = sanitize(value.get(name), ID_MAX)
        if text:
            out[name] = text
    for name in ("partNo", "version"):
        number = _int(value.get(name))
        if number:
            out[name] = number
    cut_ids = [sanitize(c, ID_MAX) for c in (value.get("cutIds") or [])[:CUT_IDS_MAX]] \
        if isinstance(value.get("cutIds"), (list, tuple)) else []
    if any(cut_ids):
        out["cutIds"] = [c for c in cut_ids if c]
    return out


def _action(value) -> str:
    text = str(value or "")
    if text.startswith(ACTION_FALLBACK):
        return ACTION_FALLBACK + sanitize(text[len(ACTION_FALLBACK):], MODEL_MAX)
    return text if text in ACTIONS else ""


def make_note(raw: dict, *, source: str = SOURCE_LIVE) -> dict:
    """호출부가 준 dict(아래 키만 읽는다) → 저장할 노트. message·providerMessage = 공급자 원문(정리한다), kind = 단서(상태의
    failureKind — "audio"·"likeness" 도 받는다), limit = 알고 있는 프롬프트 한도, excerpt = 거절 설명에 꼭 필요한 짧은 발췌."""
    raw = raw if isinstance(raw, dict) else {}
    message = raw.get("providerMessage") if raw.get("providerMessage") else raw.get("message")
    stage = sanitize(raw.get("stage"), 24)
    hint = str(raw.get("kind") or "")
    kind = classify(message, hint)
    variant = str(raw.get("variant") or "")
    note = {
        "at": str(raw.get("at") or now_iso()),
        "variant": variant if variant in VARIANTS else "",
        "stage": stage,
        "target": _target(raw.get("target")),
        "model": sanitize(raw.get("model"), MODEL_MAX),
        "attempt": _int(raw.get("attempt")),
        "kind": kind,
        "providerMessage": sanitize(message, MESSAGE_MAX),
        "reasonKo": reason_ko(kind, message, stage=stage, hint=hint, limit=_int(raw.get("limit"))),
        "action": _action(raw.get("action")),
        "resolved": bool(raw.get("resolved")),
        "source": source,
    }
    excerpt = sanitize(raw.get("excerpt"), EXCERPT_MAX)
    if excerpt:
        note["excerpt"] = excerpt
    return note


# ---------------------------------------------------------------- 저장


def notes_key(owner: str, pid: str, gen_id: str) -> str:
    from . import generate  # noqa: PLC0415

    return f"{generate._gen_root(owner, pid, gen_id)}/{FILE_NAME}"  # noqa: SLF001


def _valid_gen(gen_id) -> bool:
    return bool(_GEN_ID_RE.fullmatch(str(gen_id or "")))


def _append(key: str, entry: dict) -> None:
    """더하기만(새것 NOTES_MAX 개). retakes.json·prompts.json 과 같은 조건부 쓰기(경합이면 다시 읽는다)."""
    from . import edit  # noqa: PLC0415
    from . import retake  # noqa: PLC0415

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        notes = [n for n in data.get("notes") or [] if isinstance(n, dict)]
        notes.append(entry)
        return {"version": VERSION, "notes": notes[-NOTES_MAX:]}

    with edit._lock(f"model-notes:{key}"):  # noqa: SLF001
        retake._mutate_log(key, mutate)  # noqa: SLF001


def record(owner: str, pid: str, gen_id: str, note: dict) -> bool:
    """노트 한 줄을 남긴다. 예외를 내지 않는다(경고 1줄) — 생성 결과·상태 파일을 바꾸지 않는다. 끔이면 쓰지 않는다. 남겼으면 True."""
    try:
        if not vnew_settings.VNEW_MODEL_NOTES or not _valid_gen(gen_id):
            return False
        entry = make_note(note)
        _append(notes_key(owner, pid, gen_id), entry)
        log.info("vnew.model_note pid=%s gen=%s variant=%s stage=%s kind=%s action=%s model=%s attempt=%d", pid, gen_id,
                 entry["variant"] or "-", entry["stage"] or "-", entry["kind"], entry["action"] or "-", entry["model"] or "-",
                 entry["attempt"])
        return True
    except Exception as exc:  # noqa: BLE001 — 기록은 영상 생성을 막지 않는다.
        log.warning("vnew.model_notes.record_failed pid=%s gen=%s err=%s", pid, gen_id, type(exc).__name__)
        return False


def read_notes(owner: str, pid: str, gen_id: str) -> list[dict]:
    from . import generate  # noqa: PLC0415

    data, _ = generate.STORE.read_json(notes_key(owner, pid, gen_id))
    notes = (data or {}).get("notes") if isinstance(data, dict) else None
    return [dict(n) for n in notes if isinstance(n, dict)] if isinstance(notes, list) else []


# ---------------------------------------------------------------- 보기(GET) · 상태에서 만든 노트 · resolved


def require_enabled(where: str = "vnew.model_notes") -> None:
    """VNEW_MODEL_NOTES 가 꺼져 있으면 404 ModelNotesDisabled(라우트가 없는 것과 같다). 프로젝트를 보기 전에 부른다."""
    if not vnew_settings.VNEW_MODEL_NOTES:
        raise VpocError(where, "ModelNotesDisabled", "모델 에러 로그 기능이 꺼져 있습니다.", 404)


def _cells(gen: dict) -> list[dict]:
    from . import generate  # noqa: PLC0415

    cells = generate._previz_cells(gen) if generate._is_previz_mode(gen) else (gen.get("segments") or [])  # noqa: SLF001
    return [c if isinstance(c, dict) else {} for c in cells] if isinstance(cells, list) else []


def _state_at(item: dict, fallback: str) -> str:
    for name in ("finishedAt", "claimedAt", "submittedAt", "createdAt"):
        if item.get(name):
            return str(item[name])
    return fallback


def from_state(gen: dict, retakes: dict) -> list[dict]:
    """작업 198 backfill: 지금 상태에서 노트를 만든다(읽기만 — 저장하지 않는다). gen 칸·조각·Kling 컷과 retakes.json 버전 가운데
    error·failureKind 가 있는 것. action 은 모른다(""), source "state"."""
    from . import generate  # noqa: PLC0415

    out: list[dict] = []
    gen = gen if isinstance(gen, dict) else {}
    stamp = str(gen.get("createdAt") or "")
    for index, cell in enumerate(_cells(gen)):
        for variant in VARIANTS:
            state = cell.get(variant) if isinstance(cell.get(variant), dict) else None
            if state is None:
                continue
            items = [(state, None, None)]
            items += [(part, part, None) for part in state.get("parts") or [] if isinstance(part, dict)]
            fallback = state.get("fallback") if isinstance(state.get("fallback"), dict) else {}
            items += [(cut, None, cut) for cut in fallback.get("cuts") or [] if isinstance(cut, dict)]
            for item, part, cut in items:
                if not (item.get("error") or item.get("failureKind")):
                    continue
                head = generate._note_head(gen, index, variant, part=part, cut=cut)  # noqa: SLF001
                out.append(make_note({**head, "providerMessage": item.get("error") or "", "kind": item.get("failureKind"),
                                      "at": _state_at(item, stamp)}, source=SOURCE_STATE))
    cells = (retakes or {}).get("cells") if isinstance(retakes, dict) else None
    for key, cell in (cells or {}).items() if isinstance(cells, dict) else []:
        for version in (cell or {}).get("versions") or [] if isinstance(cell, dict) else []:
            if isinstance(version, dict) and (version.get("error") or version.get("failureKind")):
                out.append(make_note({**version_head(str(key), version), "providerMessage": version.get("error") or "",
                                      "kind": version.get("failureKind") or ("timeout" if version.get("timedOut") else ""),
                                      "at": _state_at(version, stamp)}, source=SOURCE_STATE))
    return out


def version_head(key: str, version: dict) -> dict:
    """다시 만들기 버전 하나의 노트 머리 {variant, stage "retake", target{retakeCell, version, cutIds}, model, attempt}."""
    version = version if isinstance(version, dict) else {}
    variant = str(key).partition(":")[0]
    span = version.get("range") if isinstance(version.get("range"), dict) else {}
    target = {"retakeCell": str(key), "version": version.get("n")}
    if span.get("cutIds"):
        target["cutIds"] = list(span.get("cutIds") or [])
    return {"variant": variant, "stage": "retake", "target": target, "model": str(version.get("model") or ""),
            "attempt": version.get("attempt")}


def _done(item) -> bool:
    return isinstance(item, dict) and item.get("status") == "completed"


def _resolved(note: dict, gen: dict, retakes: dict) -> bool:
    """같은 대상이 지금 completed 인가(best effort). 다시 만들기 = 그 버전, 테이크 = 그 칸(또는 그 조각·Kling 컷), 컷 칸 = 그 칸."""
    from . import generate  # noqa: PLC0415

    target = note.get("target") if isinstance(note.get("target"), dict) else {}
    variant = str(note.get("variant") or "")
    if target.get("retakeCell"):
        cell = ((retakes or {}).get("cells") or {}).get(target["retakeCell"]) or {}
        return any(_done(v) and v.get("n") == target.get("version") for v in cell.get("versions") or [])
    if variant not in VARIANTS:
        return False
    cut_ids = [str(c) for c in target.get("cutIds") or []]
    for cell in _cells(gen):
        state = cell.get(variant) if isinstance(cell.get(variant), dict) else {}
        if target.get("takeId"):
            if str(cell.get("id") or "") != target["takeId"]:
                continue
            if _done(state):
                return True
            if target.get("partNo"):
                return any(_done(p) and p.get("no") == target["partNo"] for p in state.get("parts") or [] if isinstance(p, dict))
            if note.get("stage") == "cut" and cut_ids:
                cuts = ((state.get("fallback") or {}) if isinstance(state.get("fallback"), dict) else {}).get("cuts") or []
                return any(_done(c) and str(c.get("cutId") or "") in cut_ids for c in cuts if isinstance(c, dict))
            return False
        if cut_ids and generate._seg_cut_ids(cell) == cut_ids:  # noqa: SLF001
            return _done(state)
    return False


def _variant_arg(value) -> str:
    text = str(value or "all").strip().lower() or "all"
    if text not in ("all",) + VARIANTS:
        raise VpocError("vnew.model_notes", "BadVariant", "variant 는 webtoon·real·all 중 하나입니다.", 400)
    return text


def view(owner: str, pid: str, gen_id=None, variant=None) -> dict:
    """GET 본문 {ok, genId, notes[새것부터], counts{byModel, byKind}}. gen 이 없으면 지금 gen. 파일에 노트가 없으면 상태에서 만든다
    (source "state", 쓰지 않는다). resolved = 지금 상태 기준. variant = webtoon·real·all."""
    from . import plan as plan_mod  # noqa: PLC0415
    from . import retake  # noqa: PLC0415

    variant = _variant_arg(variant)
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") if isinstance(plan.get("gen"), dict) else {}
    current = str(gen.get("genId") or "")
    gen_id = str(gen_id or "").strip() or current
    counts: dict = {"byModel": {}, "byKind": {}}
    if not gen_id:
        return {"ok": True, "genId": "", "notes": [], "counts": counts}
    if not _valid_gen(gen_id):
        raise VpocError("vnew.model_notes", "BadGen", "gen 값이 올바르지 않습니다.", 400)
    gen_state = gen if gen_id == current else {}
    try:
        retakes = retake.read_state(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 다시 만들기 상태가 없거나 못 읽으면 그 부분만 뺀다
        log.warning("vnew.model_notes.retakes_unread pid=%s gen=%s err=%s", pid, gen_id, type(exc).__name__)
        retakes = {}
    notes = read_notes(owner, pid, gen_id)
    if not notes:
        notes = from_state(gen_state, retakes)
    out = []
    for note in reversed(notes):  # 같은 시각이면 나중에 더한 것이 먼저
        if variant != "all" and note.get("variant") != variant:
            continue
        note = dict(note)
        note["resolved"] = bool(note.get("resolved")) or _resolved(note, gen_state, retakes)
        out.append(note)
    out.sort(key=lambda n: str(n.get("at") or ""), reverse=True)
    for note in out:
        model = str(note.get("model") or "") or "-"
        kind = str(note.get("kind") or KIND_OTHER)
        counts["byModel"][model] = counts["byModel"].get(model, 0) + 1
        counts["byKind"][kind] = counts["byKind"].get(kind, 0) + 1
    return {"ok": True, "genId": gen_id, "notes": out, "counts": counts}
