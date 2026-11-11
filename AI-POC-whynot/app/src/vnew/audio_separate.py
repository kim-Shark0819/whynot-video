"""⑥ 사운드 · ⑦ 재료 — 섞인 소리 분리하기(작업 217). 작업 211 전 영상은 대사와 배경(환경음 · 음악)이 한 트랙에 섞여 있다.

사용자가 고른 원본(⑥ 사운드 원본 영상 또는 ⑦ 재료 하나) 소리를 AI 음원 분리 모델(MODEL, fal)로 목소리(voice)와 배경(bg)으로 나눈다.
  보내는 것: ffmpeg 로 뽑은 소리만(-vn, 모노 wav). 영상 프레임 · 그림은 보내지 않고 모델용 그림도 저장하지 않는다(모델 호출만).
  금액: 시작 전에 견적(dryRun)을 보이고 confirmUsd 가 서버 금액과 같아야 보낸다(409 CostMismatch, 작업 206 과 같은 확인창 처리).
        단가는 fal 가격 조회(vpoc.fal.pricing_for → pricing, voices._price 와 같은 순서)이고, 모르면 "가격 확인 불가"(confirmUsd 숫자 필요).
        자동으로 다시 보내지 않는다. 원장(vpoc.ledger.charge)은 fal 이 요청을 받은 뒤 한 번(key vnew-sep-<request_id>).
  결과: 원본 옆 <원본 키(확장자 뺌)>-sep/ 아래 voice.wav · bg.wav(영상 버킷, 48kHz 스테레오 s16) · separate.json(상태, 메인 버킷).
        모델이 배경 한 개를 주면 그것, 줄기(drums · bass · other …)로 주면 ffmpeg amix 로 합친다.
  진행: POST 가 제출하고(202 running) GET 이 fal 상태를 한 번씩 본다(완료면 받아서 저장 — 백그라운드 스레드 없음). RUN_MAX_S 넘으면 실패.
  쓰기: useVoice(분리된 목소리 쓰기) — ⑥ 반영(sound.run_apply)의 원래 소리 · ⑦ 내보내기의 A1(클립 소리)을 voice.wav 로.
        bgOnA2(배경을 A2 에, 기본 켬) — ⑦ 내보내기에서 그 클립 자리 A2(배경음) 트랙에 bg.wav 구간을 놓는다(속도 1 클립만). ⑥ 에는 A2 가 없다.
VNEW_AUDIO_SEPARATE(기본 켬)가 꺼지면 라우트는 404 SeparateDisabled 이고 ⑥ · ⑦ 결과는 작업 217 전과 같다.
외부 호출 래퍼(검사가 바꾼다): _fal_call · _fetch · _price · _charge · _fal_ready · _ffmpeg · _measure.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone

from ..vpoc import fal as vpoc_fal
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso

from . import edit
from . import edit_audio
from . import sound
from . import voices

log = logging.getLogger("vpoc")

FLAG_ENV = "VNEW_AUDIO_SEPARATE"
# 코드 카탈로그(vpoc/fal.py · 레지스트리 · 검사)에는 음원 분리 모델이 없다. Demucs(fal-ai/demucs)는 fal 의 가장 싼 음원 분리 모델로
# 알고 있는 것이고(네트워크 없이 골랐다 — 가격은 확인하지 않았다), 화면 금액은 늘 실행 때 fal 가격 조회 값이다.
MODEL = "fal-ai/demucs"
MODEL_LABEL = "Demucs"
INPUT_FIELD = "audio_url"
VOICE_FIELDS = ("vocals", "vocal", "voice", "speech")
BG_FIELDS = ("background", "accompaniment", "instrumental", "no_vocals", "music")
STEM_FIELDS = ("drums", "bass", "other", "guitar", "piano")
SCOPES = ("sound", "edit")
INPUT_RATE = 44100
OUT_RATE = 48000
MAX_SOURCE_S = 600.0
RUN_MAX_S = 1800.0
RESUME_MAX = 2  # 작업 219: 받은 요청의 결과를 다시 받기(다시 내지 않는다) 상한
START_HOLD_S = 600.0  # 이보다 오래된 "starting" 은 끊긴 것으로 본다
PRESIGN_S = 3600
PRICE_UNKNOWN_LABEL = "가격 확인 불가"
# 작업 220a: 가격 조회도 마지막 단가도 없을 때 원장 추정 단가. 출처: fal 공개 가격(fal.ai/models/fal-ai/demucs) 오디오 1초당
# $0.0007(작업 220a 지시 값, 네트워크 없이 다시 확인하지 않았다). 화면 견적에는 쓰지 않는다(그대로 "가격 확인 불가").
FALLBACK_USD_PER_S = 0.0007
TIMEOUT_TEXT = "분리 모델이 시간 안에 결과를 주지 않았습니다(timeout)."
BUTTON_TEXT = "섞인 소리 분리하기"
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_LIVE = ("starting", "running")


def enabled() -> bool:
    raw = os.getenv(FLAG_ENV, "true").strip().lower()
    return True if not raw else raw in ("1", "true", "yes", "on")


def require_enabled(where: str, scope: str) -> None:
    """끔이면 404(라우트가 없는 것과 같다). scope 의 화면(⑥ VNEW_SOUND · ⑦ VNEW_EDIT)이 꺼져 있어도 404."""
    if scope == "sound":
        sound.require_enabled(where)
    elif scope == "edit":
        edit.require_enabled(where)
    else:
        raise VpocError(where, "BadScope", "scope 는 sound 또는 edit 이어야 합니다.", 400)
    if not enabled():
        raise VpocError(where, "SeparateDisabled", "섞인 소리 분리 기능이 꺼져 있습니다.", 404)


# ================================================================ 키


def sep_prefix(source_key: str) -> str:
    """원본 옆 폴더: 마지막 경로 조각의 확장자를 빼고 -sep."""
    head, _, name = str(source_key).rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{head}/{stem}-sep" if head else f"{stem}-sep"


def voice_key(source_key: str) -> str:
    return f"{sep_prefix(source_key)}/voice.wav"


def bg_key(source_key: str) -> str:
    return f"{sep_prefix(source_key)}/bg.wav"


def state_key(source_key: str) -> str:
    return f"{sep_prefix(source_key)}/separate.json"


def _tag(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:10]


# ================================================================ 외부 호출 래퍼(검사가 바꾼다)


def _fal_call(method: str, url: str, payload: dict | None, *, where: str) -> dict:
    return vpoc_fal._request(method, url, payload, where=where)  # noqa: SLF001


def _fetch(url: str) -> bytes:
    return voices._fetch_audio(url)  # noqa: SLF001 — data URI · https 만, 크기 상한


def _price(endpoint: str) -> dict | None:
    return voices._price(endpoint)  # noqa: SLF001


def _charge(key: str, *, seconds: float, usd: float, owner: str, pid: str, gen_id: str, price_unknown: bool = False) -> None:
    # 작업 220a: price_unknown 이면 원장 항목에 priceUnknown: True(분리 상태 priceUnknown · 경고 로그와 함께).
    vpoc_ledger.charge(key, kind="audio", model=MODEL, units=round(float(seconds), 3), usd=float(usd), user=owner,
                       project_id=pid, plan_id=gen_id, price_unknown=bool(price_unknown))


_LAST_PRICE: dict = {}  # 작업 219: 마지막으로 읽은 fal 단가(가격을 못 읽은 제출의 원장 추정에 쓴다)


def _remember_price(price) -> None:
    if isinstance(price, dict) and voices.unit_usd(price, 1.0) is not None:
        _LAST_PRICE.clear()
        _LAST_PRICE.update({"unitPrice": price.get("unitPrice"), "unit": price.get("unit")})


def _charge_usd(est: dict, seconds: float) -> tuple[float, bool]:
    """작업 219: 원장에 적을 금액과 가격 모름 여부. 견적 금액이 있으면 그것, 없으면 마지막으로 안 단가 × 길이(추정),
    그것도 없으면(작업 220a) 공개 단가 FALLBACK_USD_PER_S × 길이 — 어느 쪽이든 가격 모름이면 (금액, True) 이고
    상태(priceUnknown) · 원장 항목(priceUnknown) · 경고 로그에 남긴다(조용히 0 을 적지 않는다)."""
    if est.get("rawUsd") is not None:
        return float(est["rawUsd"]), False
    guess = voices.unit_usd(dict(_LAST_PRICE), float(seconds)) if _LAST_PRICE else None
    if guess is None:
        guess = FALLBACK_USD_PER_S * float(seconds)
    return round(float(guess), 6), True


def _source_size(key: str) -> int | None:
    """작업 219: 원본 크기(바이트). 저장소가 크기를 주지 않거나 읽지 못하면 None(검사가 바꾼다)."""
    lister = getattr(edit.STORE, "list_keys", None)
    if not callable(lister):
        return None
    try:
        for obj in lister(key, bucket=vpoc_settings.VIDEO_BUCKET) or []:
            if obj.get("key") == key:
                return int(obj.get("size") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.audio_separate.size_failed src=%s err=%s", _tag(key), type(exc).__name__)
        return None
    return 0


def _fal_ready() -> bool:
    return bool(vpoc_settings.FAL_KEY)


def _ffmpeg(args: list[str], what: str) -> None:
    voices._ffmpeg(args, what)  # noqa: SLF001


def _measure(owner: str, pid: str, gen_id: str, key: str) -> dict:
    """원본 길이 · 소리 유무(⑦ 재료 길이 캐시 — 없으면 잰다)."""
    meta, _ = edit._ensure_meta(owner, pid, gen_id, [key])  # noqa: SLF001
    return meta.get(key) or {}


def _put(key: str, path: str) -> None:
    with open(path, "rb") as handle:
        edit.STORE.put_bytes(key, handle.read(), "audio/wav", bucket=vpoc_settings.VIDEO_BUCKET)


# ================================================================ 상태 · 원본


def _read_state(source_key: str) -> dict:
    data, _ = edit._read_json(state_key(source_key))  # noqa: SLF001
    return data if isinstance(data, dict) else {}


def _write_state(source_key: str, fn) -> dict | None:
    return edit._mutate_json(state_key(source_key), fn)  # noqa: SLF001


def _age_s(iso: str) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return math.inf
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def _scope_of(raw, where: str) -> str:
    if raw not in SCOPES:
        raise VpocError(where, "BadScope", "scope 는 sound 또는 edit 이어야 합니다.", 400)
    return raw


def _sources(owner: str, pid: str, scope: str, variant_raw, where: str) -> tuple[str, str, list[dict]]:
    """(gen_id, variant, [{sourceKey, label, duration, hasAudio}]). ⑥ = 사운드 원본 1개, ⑦ = 그 그림체 재료 허용 목록(길이는 캐시만)."""
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(variant_raw, gen, where)  # noqa: SLF001
    if scope == "sound":
        source = sound._source(owner, pid, gen_id, gen, variant)  # noqa: SLF001
        doc, _ = edit._read_json(sound.doc_key(owner, pid, gen_id, variant))  # noqa: SLF001
        src = (doc or {}).get("source") if isinstance((doc or {}).get("source"), dict) else {}
        info = src if src.get("key") == source["key"] else {}
        return gen_id, variant, [{"sourceKey": source["key"], "label": "⑥ 사운드 원본", "duration": edit._num(info.get("duration")),  # noqa: SLF001
                                  "hasAudio": info.get("hasAudio") if isinstance(info.get("hasAudio"), bool) else None}]
    index = edit._read_index(owner, pid, gen_id)  # noqa: SLF001
    out = []
    for item in edit._all_items(gen, variant, edit._retake_state(owner, pid, gen_id)):  # noqa: SLF001
        info = index.get(item["mid"]) or {}
        out.append({"sourceKey": item["sourceKey"], "label": str(item.get("label") or ""), "duration": edit._num(info.get("duration")),  # noqa: SLF001
                    "hasAudio": info.get("hasAudio") if isinstance(info.get("hasAudio"), bool) else None})
    return gen_id, variant, out


def estimate(seconds) -> dict:
    """견적: fal 단가 × 길이(voices.unit_usd). 모르면 usd None · priceKnown False · "가격 확인 불가"."""
    price = _price(MODEL)
    usd = voices.unit_usd(price, float(seconds)) if seconds else None
    known = usd is not None
    if known:
        _remember_price(price)  # 작업 219
    rounded = round(usd, 2) if known else None
    if not known:
        money = PRICE_UNKNOWN_LABEL
    elif usd > 0 and rounded == 0:
        money = "$0.01 미만"
    else:
        money = f"${rounded:.2f}"
    return {"usd": rounded, "rawUsd": round(usd, 6) if known else None, "priceKnown": known, "label": money,
            "button": f"{BUTTON_TEXT} (AI, {money})", "seconds": round(float(seconds or 0.0), 3), "model": MODEL,
            "modelLabel": MODEL_LABEL,
            "unit": {"unitPrice": price.get("unitPrice"), "unit": price.get("unit")} if isinstance(price, dict) else None}


def _state_out(state: dict) -> dict:
    status = str(state.get("status") or "none")
    out = {"status": status, "useVoice": state.get("useVoice") is True, "bgOnA2": state.get("bgOnA2") is not False,
           "error": str(state.get("error") or ""), "at": str(state.get("finishedAt") or state.get("startedAt") or ""),
           "usd": state.get("usd"), "seconds": state.get("seconds")}
    if status == "done":
        out["voiceUrl"] = edit._presign(str(state.get("voiceKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET, content_type="audio/wav")  # noqa: SLF001
        out["bgUrl"] = edit._presign(str(state.get("bgKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET, content_type="audio/wav")  # noqa: SLF001
    return out


# ================================================================ 보기 · 시작 · 켜고 끄기


def view(owner: str, pid: str, args) -> dict:
    """GET /p/<pid>/audio-separate?scope=sound|edit&variant=. 원본마다 {sourceKey, label, state, estimate}. 진행 중이면 fal 상태를 한 번 본다."""
    where = "vnew.audio_separate.view"
    scope = _scope_of(args.get("scope"), where)
    gen_id, variant, sources = _sources(owner, pid, scope, args.get("variant"), where)
    price_cache: dict = {}
    items = []
    for src in sources:
        state = _read_state(src["sourceKey"])
        if state.get("status") == "running":
            state = _advance(owner, pid, gen_id, src["sourceKey"], state)
        seconds = src["duration"] or 0.0
        if seconds not in price_cache:
            price_cache[seconds] = estimate(seconds) if seconds else None
        items.append({"sourceKey": src["sourceKey"], "mid": edit.media_id(src["sourceKey"]), "label": src["label"],
                      "hasAudio": src["hasAudio"], "state": _state_out(state), "estimate": price_cache[seconds]})
    return {"enabled": True, "scope": scope, "variant": variant, "model": MODEL, "modelLabel": MODEL_LABEL, "items": items}


def _pick(owner: str, pid: str, payload: dict, where: str) -> tuple[str, str, str, dict]:
    scope = _scope_of(payload.get("scope"), where)
    gen_id, variant, sources = _sources(owner, pid, scope, payload.get("variant"), where)
    if scope == "sound":
        return gen_id, variant, sources[0]["sourceKey"], sources[0]
    wanted = payload.get("sourceKey")
    found = next((s for s in sources if isinstance(wanted, str) and s["sourceKey"] == wanted), None)
    if found is None:
        raise VpocError(where, "BadSource", "분리할 재료를 찾을 수 없습니다. 다시 불러온 뒤 골라 주세요.", 400)
    return gen_id, variant, found["sourceKey"], found


def start(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/audio-separate {scope, variant, sourceKey?(⑦), confirmUsd, dryRun?}.

    dryRun true 면 {estimate, state}. 아니면 confirmUsd(400 NoConfirmUsd · 409 CostMismatch) → 소리만 뽑아(ffmpeg -vn) 올리고 fal 제출
    (받으면 원장 1건) → {started True, state running, estimate}. 409: SeparateRunning · NoFalKey, 400: BadAudio · SourceTooLong."""
    where = "vnew.audio_separate.start"
    payload = payload if isinstance(payload, dict) else {}
    dry = payload.get("dryRun") is True
    confirm = None if dry else voices._confirm(payload, where)  # noqa: SLF001
    gen_id, _variant, key, _src = _pick(owner, pid, payload, where)
    info = _measure(owner, pid, gen_id, key)
    seconds = edit._num(info.get("duration")) or 0.0  # noqa: SLF001
    if info.get("hasAudio") is not True or seconds <= 0:
        raise VpocError(where, "BadAudio", "이 영상에는 소리가 없어 분리할 수 없습니다.", 400)
    if seconds > MAX_SOURCE_S:
        raise VpocError(where, "SourceTooLong", f"{int(MAX_SOURCE_S // 60)}분보다 긴 소리는 분리할 수 없습니다.", 400)
    est = estimate(seconds)
    if dry:
        return {"estimate": est, "state": _state_out(_read_state(key))}
    if est["priceKnown"] and round(confirm, 2) != est["usd"]:
        raise VpocError(where, "CostMismatch", f"금액이 서버 계산과 다릅니다. 서버 {est['usd']} USD, 화면 {round(confirm, 2)} USD.", 409)
    if not _fal_ready():
        raise VpocError(where, "NoFalKey", "fal 키(FAL_KEY)가 설정되지 않았습니다.", 409)
    claim = secrets.token_hex(6)
    resumed: dict = {}

    def hold(state):
        state = state if isinstance(state, dict) else {}
        live = state.get("status") in _LIVE and _age_s(state.get("startedAt") or "") < (
            START_HOLD_S if state.get("status") == "starting" else RUN_MAX_S)
        if live:
            raise VpocError(where, "SeparateRunning", "이 소리를 분리하는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        if _resumable(state, key):
            # 작업 219: 이미 받아진(돈이 나간) 요청 — 다시 내지 않고 같은 requestId 로 결과를 다시 받는다
            resumed["requestId"] = state["requestId"]
            return {**state, "status": "running", "claim": claim, "startedAt": now_iso(), "error": "", "finishedAt": "",
                    "resumable": False, "resumes": int(state.get("resumes") or 0) + 1}
        keep = {k: state[k] for k in ("useVoice", "bgOnA2") if k in state}
        return {**keep, "v": 1, "status": "starting", "claim": claim, "sourceKey": key, "model": MODEL, "startedAt": now_iso(),
                "seconds": round(seconds, 3), "usd": est["usd"], "priceKnown": est["priceKnown"], "error": ""}

    saved = _write_state(key, hold) or {}
    if resumed:
        log.info("vnew.audio_separate.resume pid=%s gen=%s src=%s req=%s", pid, gen_id, _tag(key), _tag(resumed["requestId"]))
        return {"started": True, "resumed": True, "state": _state_out(saved), "estimate": est}
    box: dict = {}
    try:
        _submit(owner, pid, gen_id, key, seconds, est, where, claim=claim, box=box)
    except Exception as exc:  # noqa: BLE001
        if box.get("requestId"):
            # 작업 219: fal 은 받았다(돈이 나간다) — requestId 를 지켜 running 으로 둔다(다음 GET 이 이어 본다, 다시 내지 않는다).
            log.warning("vnew.audio_separate.state_retry pid=%s src=%s err=%s", pid, _tag(key), type(exc).__name__)
            try:
                state = _finish(key, claim, box["running"]) or {}
            except Exception:
                log.error("vnew.audio_separate.orphan pid=%s src=%s req=%s — 상태를 쓰지 못했습니다", pid, _tag(key),
                          _tag(box["requestId"]))
                raise
            log.info("vnew.audio_separate.start pid=%s gen=%s src=%s seconds=%.3f known=%s retried=1", pid, gen_id, _tag(key), seconds,
                     est["priceKnown"])
            return {"started": True, "state": _state_out(state), "estimate": est}
        message = exc.message if isinstance(exc, VpocError) else "소리를 분리 모델에 보내지 못했습니다."
        _drop_input(box.get("inputKey"))  # 작업 219: 보내지 못한 임시 입력도 지운다
        _finish(key, claim, {"status": "failed", "error": message, "finishedAt": now_iso()})
        log.warning("vnew.audio_separate.submit_failed pid=%s src=%s err=%s", pid, _tag(key), getattr(exc, "kind", type(exc).__name__))
        raise
    state = box.get("state") or {}
    log.info("vnew.audio_separate.start pid=%s gen=%s src=%s seconds=%.3f known=%s", pid, gen_id, _tag(key), seconds, est["priceKnown"])
    return {"started": True, "state": _state_out(state), "estimate": est}


def _resumable(state: dict, key: str) -> bool:
    """작업 219: 실패 기록이지만 fal 이 받은(돈이 나간) 요청이 남아 있고 같은 원본이면 다시 받을 수 있다(RESUME_MAX 번까지).
    작업 220: 시간 상한을 넘긴 running(그동안 GET 이 없어 실패로 적히지 않았다)도 requestId 가 있으면 다시 내지 않고 이어 본다."""
    status = state.get("status")
    stale = status == "running" and _age_s(state.get("startedAt") or "") >= RUN_MAX_S
    return (((status == "failed" and state.get("resumable") is True) or stale) and state.get("sourceKey") == key
            and bool(_REQUEST_ID_RE.fullmatch(str(state.get("requestId") or ""))) and int(state.get("resumes") or 0) < RESUME_MAX)


def _drop_input(input_key) -> None:
    """작업 219: 영상 버킷의 임시 입력(input-*.wav) 지우기 — 최선만."""
    if not input_key:
        return
    try:
        edit._delete_object(str(input_key), bucket=vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001
    except Exception:  # noqa: BLE001 — 임시 입력 지우기는 최선만
        pass


def _submit(owner: str, pid: str, gen_id: str, key: str, seconds: float, est: dict, where: str, *, claim: str = "",
            box: dict | None = None) -> str:
    """소리만 뽑아(-vn 모노 wav) 영상 버킷 임시 키에 올리고 서명 주소로 제출. 받으면 원장 1건. 반환 request_id.

    작업 219: 받은 즉시 상태 한 번 쓰기로 {running, requestId, inputKey, 원본 크기 · 길이} (box["state"]). box 에 inputKey ·
    requestId · running(쓸 값)을 남긴다 — 쓰기가 실패해도 start 가 requestId 를 지켜 다시 쓴다."""
    box = box if box is not None else {}
    input_key = f"{sep_prefix(key)}/input-{secrets.token_hex(4)}.wav"
    with tempfile.TemporaryDirectory(prefix="vnew-sep-") as workdir:
        source_path, audio_path = os.path.join(workdir, "source.mp4"), os.path.join(workdir, "input.wav")
        with edit._MEDIA_SEM:  # noqa: SLF001
            edit._download(key, source_path, bucket=vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001
            source_size = os.path.getsize(source_path)
            _ffmpeg(["-i", source_path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(INPUT_RATE), "-c:a", "pcm_s16le",
                     "-f", "wav", audio_path], "소리를 뽑지 못했습니다")
            os.remove(source_path)
        box["inputKey"] = input_key
        _put(input_key, audio_path)
    url = edit.STORE.presign(input_key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="audio/wav", expires=PRESIGN_S)
    submitted = _fal_call("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{MODEL}", {INPUT_FIELD: url}, where=where)
    request_id = str((submitted or {}).get("request_id") or "") if isinstance(submitted, dict) else ""
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise VpocError(where, "NoRequestId", "분리 모델이 요청 ID를 주지 않았습니다.", 502)
    box["requestId"] = request_id
    usd, unknown = _charge_usd(est, seconds)
    running = {"status": "running", "requestId": request_id, "inputKey": input_key, "submittedAt": now_iso(),
               "sourceSize": int(source_size), "sourceDuration": round(float(seconds), 3)}
    if unknown:
        running.update(priceUnknown=True, usdEstimate=round(usd, 6))
    box["running"] = running
    write_error: Exception | None = None
    try:
        box["state"] = _finish(key, claim, running)  # 작업 219: requestId 와 running 을 한 번에(사이에 끊겨도 고아 요청이 없다)
    except Exception as exc:  # noqa: BLE001 — 원장은 그래도 적고, start 가 다시 쓴다
        write_error = exc
    if unknown:
        log.warning("vnew.audio_separate.price_unknown pid=%s req=%s usd_estimate=%.6f from_last=%s", pid, _tag(request_id), usd,
                    bool(_LAST_PRICE))
    try:
        _charge("vnew-sep-" + request_id, seconds=seconds, usd=usd, owner=owner, pid=pid, gen_id=gen_id, price_unknown=unknown)
    except Exception as exc:  # noqa: BLE001 — 원장 실패는 분리를 막지 않는다(voices 와 같다)
        log.warning("vnew.audio_separate.charge_failed pid=%s err=%s", pid, type(exc).__name__)
    if write_error is not None:
        raise write_error
    return request_id


def _finish(key: str, claim: str, fields: dict) -> dict | None:
    """claim 이 그대로일 때만 상태에 fields 를 더한다(다른 시작이 이어받았으면 쓰지 않는다)."""
    return _write_state(key, lambda s: {**s, **fields} if isinstance(s, dict) and s.get("claim") == claim else None)


def set_use(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/audio-separate {scope, variant, sourceKey?, useVoice?, bgOnA2?}. 분리가 끝난 원본만(409 NotSeparated)."""
    where = "vnew.audio_separate.use"
    payload = payload if isinstance(payload, dict) else {}
    _gen_id, _variant, key, _src = _pick(owner, pid, payload, where)
    patch = {k: payload[k] for k in ("useVoice", "bgOnA2") if isinstance(payload.get(k), bool)}
    if not patch:
        raise VpocError(where, "BadBody", "useVoice 또는 bgOnA2(참/거짓)가 필요합니다.", 400)

    def mutate(state):
        if not isinstance(state, dict) or state.get("status") != "done":
            raise VpocError(where, "NotSeparated", "먼저 섞인 소리를 분리해 주세요.", 409)
        return {**state, **patch}

    return {"state": _state_out(_write_state(key, mutate) or {})}


# ================================================================ 진행(GET 이 부른다)


def _result_urls(result) -> tuple[str, list[str]]:
    """fal 결과 → (목소리 주소, 배경 주소들). 배경 한 개가 있으면 그것, 없으면 목소리 밖 줄기 전부."""
    def url_of(value) -> str:
        value = value.get("url") if isinstance(value, dict) else value
        return value if isinstance(value, str) and value else ""

    data = result if isinstance(result, dict) else {}
    voice = next((url_of(data.get(f)) for f in VOICE_FIELDS if url_of(data.get(f))), "")
    bg = next((url_of(data.get(f)) for f in BG_FIELDS if url_of(data.get(f))), "")
    stems = [url_of(data.get(f)) for f in STEM_FIELDS if url_of(data.get(f))]
    return voice, [bg] if bg else stems


def _advance(owner: str, pid: str, gen_id: str, key: str, state: dict) -> dict:
    """진행 중 상태 한 번 보기: 완료면 결과를 받아 voice.wav · bg.wav 로 저장하고 done. 예외를 내지 않는다(보기는 계속 보인다)."""
    where = "vnew.audio_separate.poll"
    claim, request_id = str(state.get("claim") or ""), str(state.get("requestId") or "")

    def fail(message: str, resumable: bool = False) -> dict:
        # 작업 219: 실패해도 requestId 는 남는다(_finish 는 더하기만 한다). fal 이 끝낸(돈이 나간) 요청이면 resumable — 다음 [분리하기]가
        # 다시 내지 않고 결과를 다시 받는다. 임시 입력은 지운다(성공 때와 같다).
        _drop_input(state.get("inputKey"))
        return _finish(key, claim, {"status": "failed", "error": message, "finishedAt": now_iso(), "resumable": bool(resumable)}) or state

    if not _REQUEST_ID_RE.fullmatch(request_id):
        return fail("분리 요청 기록이 없습니다. 다시 눌러 주세요.")
    completed = False
    try:
        status = _fal_call("GET", vpoc_fal._status_url(MODEL, request_id), None, where=where)  # noqa: SLF001
        phase = str((status or {}).get("status") or "").upper() if isinstance(status, dict) else ""
        if phase in ("", "IN_QUEUE", "IN_PROGRESS"):
            if _age_s(state.get("startedAt") or "") > RUN_MAX_S:
                try:
                    vpoc_fal.cancel(f"{vpoc_fal.ARN_PREFIX}{MODEL}/{request_id}")
                except Exception:  # noqa: BLE001 — 취소는 최선만
                    pass
                return fail(TIMEOUT_TEXT)
            return state
        if phase != "COMPLETED":
            detail = vpoc_fal._error_detail(json.dumps(status, ensure_ascii=False, default=str))  # noqa: SLF001
            return fail("소리를 분리하지 못했습니다: " + (error_gist(detail, 200) or phase))
        completed = True
        result = _fal_call("GET", vpoc_fal._response_url(MODEL, request_id), None, where=where)  # noqa: SLF001
        voice_url, bg_urls = _result_urls(result)
        if not voice_url or not bg_urls:
            return fail("분리 모델이 목소리 · 배경 소리를 주지 않았습니다.")
        with tempfile.TemporaryDirectory(prefix="vnew-sep-") as workdir:
            paths = []
            for index, url in enumerate([voice_url] + bg_urls):
                path = os.path.join(workdir, f"in-{index}.bin")
                with open(path, "wb") as handle:
                    handle.write(_fetch(url))
                paths.append(path)
            out_args = ["-ac", "2", "-ar", str(OUT_RATE), "-c:a", "pcm_s16le", "-f", "wav"]
            voice_path, bg_path = os.path.join(workdir, "voice.wav"), os.path.join(workdir, "bg.wav")
            _ffmpeg(["-i", paths[0], "-map", "0:a:0", *out_args, voice_path], "목소리를 읽지 못했습니다")
            if len(paths) == 2:
                _ffmpeg(["-i", paths[1], "-map", "0:a:0", *out_args, bg_path], "배경 소리를 읽지 못했습니다")
            else:
                inputs = [a for p in paths[1:] for a in ("-i", p)]
                _ffmpeg([*inputs, "-filter_complex", f"amix=inputs={len(paths) - 1}:normalize=0:duration=longest", *out_args,
                         bg_path], "배경 소리를 합치지 못했습니다")
            _put(voice_key(key), voice_path)
            _put(bg_key(key), bg_path)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, VpocError) and (exc.kind in ("FalUnreachable", "HTTP429") or str(exc.kind).startswith("HTTP5")):
            if _age_s(state.get("startedAt") or "") > RUN_MAX_S:  # 작업 219: 끝없는 5xx · 429 도 시간 상한에서 끝낸다
                log.warning("vnew.audio_separate.poll_timeout pid=%s src=%s err=%s completed=%s", pid, _tag(key), exc.kind, completed)
                return fail(TIMEOUT_TEXT, resumable=completed)
            return state  # 잠깐 끊김 — 다음 GET 이 다시 본다
        log.warning("vnew.audio_separate.poll_failed pid=%s src=%s err=%s", pid, _tag(key), getattr(exc, "kind", type(exc).__name__))
        return fail(exc.message if isinstance(exc, VpocError) else "소리를 분리하지 못했습니다.", resumable=completed)
    _drop_input(state.get("inputKey"))
    log.info("vnew.audio_separate.done pid=%s gen=%s src=%s stems=%d", pid, gen_id, _tag(key), len(bg_urls))
    return _finish(key, claim, {"status": "done", "voiceKey": voice_key(key), "bgKey": bg_key(key), "finishedAt": now_iso(),
                                "error": "", "resumable": False}) or state


# ================================================================ ⑥ · ⑦ 에서 쓰기


def separated_audio(owner: str, pid: str, source_key: str, duration=None) -> dict | None:
    """분리가 끝났고 [분리된 목소리 쓰기]가 켜진 원본이면 {voiceKey, bgKey(bgOnA2 끔이면 "")}, 아니면 None. 끔(플래그)이면 None.
    작업 219: 분리 뒤 원본이 바뀌었으면(크기 · 길이 — duration 을 주면 그것도) None."""
    if not enabled() or not str(source_key or "").startswith(f"users/{owner}/video/{pid}/"):
        return None
    try:
        state = _read_state(source_key)
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 원래 소리
        log.warning("vnew.audio_separate.read_failed pid=%s err=%s", pid, type(exc).__name__)
        return None
    if state.get("status") != "done" or state.get("useVoice") is not True or state.get("voiceKey") != voice_key(source_key):
        return None
    if _source_changed(state, source_key, duration):
        return None
    return {"voiceKey": voice_key(source_key), "bgKey": bg_key(source_key) if state.get("bgOnA2") is not False else ""}


def _source_changed(state: dict, source_key: str, duration=None) -> bool:
    """작업 219: 분리할 때 적어 둔 원본 크기(sourceSize) · 길이(sourceDuration)가 지금과 다르면 True(같은 키에 새 영상을 덮어썼다).
    적어 둔 값이 없는(219 전) 기록이나 지금 값을 읽지 못한 칸은 비교하지 않는다."""
    then_size, then_dur = state.get("sourceSize"), edit._num(state.get("sourceDuration"))  # noqa: SLF001
    now_dur = edit._num(duration)  # noqa: SLF001
    if then_dur and now_dur and abs(then_dur - now_dur) > 0.05:
        log.info("vnew.audio_separate.stale src=%s why=duration then=%.3f now=%.3f", _tag(source_key), then_dur, now_dur)
        return True
    if isinstance(then_size, int) and not isinstance(then_size, bool) and then_size > 0:
        now_size = _source_size(source_key)
        if now_size is not None and now_size != then_size:
            log.info("vnew.audio_separate.stale src=%s why=size then=%d now=%d", _tag(source_key), then_size, now_size)
            return True
    return False


def sound_track(owner: str, pid: str, source_key: str, workdir: str) -> str | None:
    """⑥ 반영: 분리된 목소리 쓰기가 켜진 원본이면 voice.wav 를 workdir 에 받아 경로를, 아니면 None(원래 소리)."""
    sep = separated_audio(owner, pid, source_key)
    if not sep:
        return None
    path = os.path.join(workdir, "sep-voice.wav")
    edit._download(sep["voiceKey"], path, bucket=vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001
    return path


DERIVED_PREFIX = "215c:"  # 작업 219: edit_tools.export_plan 의 검은 클립(215c:black) · 파생 원본(215c:dN)


def _derived_origin(spec: dict, clip: dict) -> tuple[str, str]:
    """작업 219: 파생 원본(215c:dN) → (원래 원본 키, ""). 같은 시각으로 맞출 수 없으면("", 까닭) — 정방향 · 속도 1 · 음높이 그대로 · 게인 0 만 맞는다."""
    if spec.get("reverse") or float(spec.get("a") or 0.0) != 0.0:
        return "", "reverse"
    if float(spec.get("varispeed") or 1.0) != 1.0 or abs(edit._speed_of(clip) - 1.0) >= 1e-9:  # noqa: SLF001
        return "", "speed"
    if float(spec.get("gain") or 0.0) != 0.0:
        return "", "gain"  # 대체 소리에는 게인을 걸 수 없다
    return str(spec.get("from") or ""), ""


def export_sources(owner: str, pid: str, clips: list[dict], audio_from: dict, meta: dict,
                   derived: dict | None = None) -> tuple[dict, list[dict]]:
    """⑦ 내보내기: 클립 소리 원천(원본 또는 _audio_sources 대체)에 분리 결과가 쓰이면 원천을 voice.wav 로(같은 시각 대응).
    반환 (새 audio_from, 배경 [{index, key, offset, end}] — 속도 1 · 시각 배율 1 클립만).

    작업 219: clips · meta 는 edit_tools.export_plan 을 거친 뒤의 것(index = 그 목록의 자리). 검은 클립(빈틈 · 사용 안 함)은 건너뛴다.
    파생 원본(derived = plan["derived"])은 원래 원본과 시각이 같을 때(정방향 · 속도 1 · 게인 0)만 원래 원본의 분리 결과를 쓰고,
    아니면 분리된 목소리 · 배경을 넣지 않는다(파생 원본의 원래 소리 그대로 — 배경이 두 번 들리지 않게) · 로그 한 줄."""
    out = dict(audio_from)
    found: dict[str, dict | None] = {}
    bg: list[dict] = []
    for index, clip in enumerate(clips):
        key = clip["sourceKey"]
        if clip.get("muted") or clip.get("disabled"):
            continue
        lookup = key
        if str(key).startswith(DERIVED_PREFIX):
            spec = (derived or {}).get(key)
            if not isinstance(spec, dict):
                continue  # 검은 클립(215c:black) — 소리 없음
            lookup, why = _derived_origin(spec, clip)
            if not lookup:
                origin = (audio_from.get(spec.get("from")) or {}).get("sourceKey") or spec.get("from")
                if origin not in found:
                    found[origin] = edit._separated_audio(owner, pid, origin)  # noqa: SLF001
                if found[origin]:
                    log.info("vnew.audio_separate.export_skip pid=%s clip=%d src=%s why=%s — 분리된 목소리 · 배경을 넣지 않음",
                             pid, index, _tag(origin), why)
                continue
        entry = audio_from.get(lookup)
        origin = entry["sourceKey"] if entry else lookup
        if origin not in found:
            # 작업 219: 대체 소리가 아니면 지금 원본 길이를 같이 넘겨 분리 뒤 바뀐 원본을 거른다
            found[origin] = edit._separated_audio(owner, pid, origin,  # noqa: SLF001
                                                  None if entry else (meta.get(origin) or {}).get("duration"))
        sep = found[origin]
        if not sep:
            if entry and key != lookup:
                out[key] = dict(entry)  # 작업 220: 파생 원본(정방향 · 속도 1)도 116 대체 소리 그대로 — 215c 계획 뒤 키로 옮긴다
            continue
        if entry:
            offset, scale, end = float(entry["offset"]), float(entry["scale"]), float(entry["end"])
        else:
            end = edit._num((meta.get(lookup) or {}).get("duration")) or 0.0  # noqa: SLF001
            offset, scale = 0.0, 1.0
            if end <= 0:
                continue
        out[key] = {"sourceKey": sep["voiceKey"], "mid": edit.media_id(sep["voiceKey"]), "offset": round(offset, 3),
                    "scale": scale, "end": round(end, 3)}
        if sep.get("bgKey") and abs(scale - 1.0) < 1e-9 and abs(edit._speed_of(clip) - 1.0) < 1e-9:  # noqa: SLF001
            bg.append({"index": index, "key": sep["bgKey"], "offset": offset, "end": end})
    return out, bg


def export_mix_doc(mix_doc: dict | None, bg: list[dict], clips: list[dict], frames: list[int], fx: dict | None, fps: int) -> dict | None:
    """⑦ 내보내기: 배경을 A2(첫 트랙, 배경음 스템) 그 클립 자리에 놓은 섞기 문서 사본. 배경이 없으면 그대로(217 전과 같다)."""
    if not bg:
        return mix_doc
    doc = copy.deepcopy(mix_doc) if isinstance(mix_doc, dict) else {"tracks": [], "a1": edit_audio.default_a1(), "assets": {}}
    tracks = doc.setdefault("tracks", [])
    if not tracks:
        tracks.append({"id": "a2", "name": edit_audio.TRACK_NAMES[0], "mute": False, "solo": False, "gainDb": 0.0, "items": []})
    assets = doc.setdefault("assets", {})
    starts = list(fx["start"]) if fx is not None else [sum(frames[:i]) for i in range(len(frames))]
    for item in bg:
        index = item["index"]
        if index >= len(clips) or index >= len(frames):  # 작업 219: 자리가 목록 밖이면 넣지 않는다(IndexError 대신)
            continue
        src_in = float(item["offset"]) + float(clips[index].get("in") or 0.0)
        src_out = min(float(item["end"]), src_in + frames[index] / float(fps))
        if src_out - src_in < edit_audio.MIN_ITEM_S:
            continue
        aid = "s" + hashlib.sha256(item["key"].encode("utf-8")).hexdigest()[:16]
        assets[aid] = {"key": item["key"], "ext": "wav", "duration": round(float(item["end"]), 3)}
        tracks[0].setdefault("items", []).append({
            "id": f"sep{index}", "assetId": aid, "start": round(starts[index] / float(fps), 4), "in": round(src_in, 4),
            "out": round(src_out, 4), "gainDb": 0.0, "fadeIn": 0.0, "fadeOut": 0.0, "keys": []})
    return doc


# ================================================================ 라우트


def register(bp, deps, project_for, fail) -> None:
    """routes.create_blueprint 안 ⑥ 사운드 라우트 뒤에서 한 줄로 붙인다. 권한은 ⑥ 사운드와 같다(쓰기 권한, 남의 프로젝트 404)."""
    from flask import jsonify, request  # noqa: PLC0415

    from .voices_routes import _body  # noqa: PLC0415

    def scope_arg(source) -> str:
        value = source.get("scope") if hasattr(source, "get") else None
        return value if value in SCOPES else "sound"

    @bp.get("/p/<pid>/audio-separate")
    @deps.require_auth
    def audio_separate_get(pid: str):
        try:
            require_enabled("vnew.audio_separate.view", scope_arg(request.args))
            return jsonify(view(project_for(pid), pid, request.args))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.post("/p/<pid>/audio-separate")
    @deps.require_auth
    def audio_separate_post(pid: str):
        try:
            body = _body("vnew.audio_separate.start")
            require_enabled("vnew.audio_separate.start", scope_arg(body))
            result = start(project_for(pid), pid, body)
            return (jsonify({"ok": True, **result}), 202) if result.get("started") else jsonify(result)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.put("/p/<pid>/audio-separate")
    @deps.require_auth
    def audio_separate_put(pid: str):
        try:
            body = _body("vnew.audio_separate.use")
            require_enabled("vnew.audio_separate.use", scope_arg(body))
            return jsonify(set_use(project_for(pid), pid, body))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
