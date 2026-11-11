"""vnew ⑥ 사운드 — 인물마다 같은 목소리(작업 202, 2026-11-09).

영상 모델(Seedance·Wan 소리 포함 생성)은 만들 때마다 목소리를 새로 지어낸다. 같은 인물의 목소리가 테이크·회차·다시 만들기마다 달라진다.
여기서는 프로젝트마다 인물별 목소리(프로필)를 한 번 정하고, 원래 소리에서 그 인물 대사 구간만 fal 목소리 바꾸기 모델로 바꿔 끼운다.
소리를 새로 쓰지 않는다(TTS 아님): 원래 말의 타이밍·감정은 그대로 두고 음색만 바꾼다. 영상은 복사만 한다(sound._render).

프로필(plan.voices, 대본 speaker 와 같은 이름 키 — 실사·웹툰이 함께 쓰고 perVariant 로 한쪽만 덮어쓸 수 있다. 바꾸면 revision + 1):
  {<speaker>: {mode "clip"|"preset", model <fal endpoint>, preset <목소리 이름>|"", ref {genId, variant, lineId, start, end, seconds, key}|None,
               updatedAt, perVariant?: {real|webtoon: {mode, model, preset, ref, updatedAt}}}}
  clip: 우리가 만든 영상의 그 인물 대사 한 줄에서 서버가 자른 짧은 기준 소리(wav 모노, REF_MAX_S 이하)를 닮게 한다.
        기준 소리는 소리만이다(그림·사용자 업로드 없음). 키는 그 gen 의 사운드 폴더 아래 voices/ 에 둔다.
  preset: 모델이 가진 목소리 이름(ElevenLabs·Chatterbox HD).
들어보기(preview): 한 줄 구간(앞뒤 PAD_S)만 바꿔 voices/preview-*.wav 로 저장하고 주소·견적·청구를 준다(동기, PREVIEW_WAIT_S).
전체 반영(voice-apply): sound.start_apply 의 작업(job)에 줄별 계획(voices)을 싣고 sound.run_apply 가 build_track 을 부른다.
  줄마다 원래 소리에서 구간(앞뒤 PAD_S)을 잘라 바꾸고 → 원래 구간 길이로 정확히 자르고(앞 패딩만큼 건너뜀) → 원래 구간과 RMS 를 맞추고
  → 그 자리에 FADE_S 크로스페이드로 끼운다(전체 길이·구간 길이 그대로). 속마음 효과는 그 뒤 sound._render 가 이 트랙 위에 건다.
  실패한 줄은 원래 소리 그대로 두고 모델 노트(model_notes.record, stage "voice")를 남긴다.
금액: fal 단가(vpoc.fal.pricing_for → pricing, generate._retake_price 와 같은 순서) × 보낸 길이(초·분 단위) 또는 요청당 단가. 모르면 None
  ("가격 확인 불가")이고 그래도 진행한다(confirmUsd 는 숫자면 받는다). 아는 금액이면 confirmUsd(소수 2자리)가 서버 금액과 같아야 한다(409 CostMismatch).
  원장(vpoc.ledger.charge)은 fal 이 요청을 받은 뒤(request_id) 요청마다 한 번(key vnew-voice-<request_id>).
fal 입력 소리: DATA_URI_MAX_BYTES 이하면 data URI, 넘으면 사운드 폴더 voices/tmp/ 에 올리고 PRESIGN_S 짜리 서명 주소.
로그에는 주소·키·data URI 를 남기지 않는다(인물 이름도 해시 tag 로만).
VNEW_SOUND_VOICES(기본 켬, 여기서 os.getenv 로 읽는다)가 꺼지면 새 라우트는 404 VoicesDisabled, 사운드 보기에 voices 가 없다.
외부 호출 래퍼(검사가 바꾼다): _fal_call · _fetch_audio · _price · _charge · _sleep · _fal_ready.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
from array import array

from .. import stitcher
from ..vpoc import fal as vpoc_fal
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso

from . import edit
from . import model_notes as vnew_model_notes
from . import plan as plan_mod
from . import sound
from .fx import clean_text

log = logging.getLogger("vpoc")

# ================================================================ 상수

FLAG_ENV = "VNEW_SOUND_VOICES"
MODE_CLIP = "clip"
MODE_PRESET = "preset"
MODE_OFF = "off"  # PUT 에서만: 그 인물(또는 그 그림체 덮어쓰기) 프로필을 지운다
MODES = (MODE_CLIP, MODE_PRESET)

MODEL_HD = "resemble-ai/chatterboxhd/speech-to-speech"
MODEL_ELEVEN = "fal-ai/elevenlabs/voice-changer"
MODEL_LITE = "fal-ai/chatterbox/speech-to-speech"
CHATTERBOX_HD_VOICES = ("Aurora", "Blade", "Britney", "Carl", "Cliff", "Richard", "Rico", "Siobhan", "Vicky")
# fal ElevenLabs 의 voice 는 글자 필드(enum 없음)라 fal ElevenLabs 기본 목소리 이름만 받는다.
ELEVENLABS_VOICES = ("Aria", "Roger", "Sarah", "Laura", "Charlie", "George", "Callum", "River", "Liam", "Charlotte", "Alice",
                     "Matilda", "Will", "Jessica", "Eric", "Chris", "Brian", "Daniel", "Lily", "Bill")
# 모델마다: 받는 mode, 목소리 이름, 입력 필드 이름(source 바꿀 소리 · ref 기준 소리 · preset 목소리 이름), 고정 값, seed 필드.
MODELS: dict[str, dict] = {
    MODEL_HD: {"label": "Chatterbox HD", "modes": (MODE_CLIP, MODE_PRESET), "presets": CHATTERBOX_HD_VOICES,
               "source": "source_audio_url", "ref": "target_voice_audio_url", "preset": "target_voice",
               "extra": {"high_quality_audio": True}, "seed": ""},
    MODEL_ELEVEN: {"label": "ElevenLabs", "modes": (MODE_PRESET,), "presets": ELEVENLABS_VOICES,
                   "source": "audio_url", "ref": "", "preset": "voice",
                   "extra": {"remove_background_noise": False, "output_format": "mp3_44100_128"}, "seed": "seed"},
    MODEL_LITE: {"label": "Chatterbox", "modes": (MODE_CLIP,), "presets": (),
                 "source": "source_audio_url", "ref": "target_voice_audio_url", "preset": "", "extra": {}, "seed": ""},
}
DEFAULT_MODEL = {MODE_CLIP: MODEL_HD, MODE_PRESET: MODEL_ELEVEN}

PAD_S = 0.15  # 바꿀 구간 앞뒤로 더 보내는 길이(모델이 말 앞뒤를 듣게). 끼울 때는 원래 구간만 쓴다.
FADE_S = 0.02  # 끼운 구간 양 끝 크로스페이드
REF_MAX_S = 15.0  # 기준 소리 최대 길이
REF_MIN_S = 0.5
EXCERPT_RATE = 44100  # 기준 소리·들어보기 소리(모노 s16)
DATA_URI_MAX_BYTES = 1_500_000  # 이보다 큰 입력 소리는 서명 주소로 보낸다
PRESIGN_S = 600
PREVIEW_WAIT_S = 50.0  # 들어보기는 요청 안에서 기다린다(ALB 60초보다 짧게)
LINE_WAIT_S = 180.0  # 전체 반영의 줄 하나 상한(하트비트는 줄마다 — sound.APPLY_STALL_S 900초보다 짧다)
POLL_S = 2.0
RESULT_MAX_BYTES = 50 * 1024 * 1024
FETCH_TIMEOUT_S = 120
GAIN_MIN, GAIN_MAX = 0.05, 8.0  # 소리 크기 맞춤 배율 상한(약 −26 dB ~ +18 dB)
MAX_SPEAKERS = 50
PRICE_UNKNOWN_LABEL = "가격 확인 불가"
# 작업 220b: 단가를 못 읽었을 때 원장 추정 단가(오디오 1초당 USD, 모델마다). 이 모듈은 마지막으로 안 단가를 두지 않아 이 값을 쓴다.
# 출처: fal 공개 가격 페이지(fal.ai/models/<모델 id>)의 오디오 길이 단가 — 셋 중 가장 비싼 ElevenLabs voice-changer 분당 $0.30
# (= 초당 $0.005)를 보수적으로 셋 다에 쓴다(작업 220b 작성 때 기억한 값, 네트워크 없이 다시 확인하지 못했다).
# 추정값(ESTIMATE) — 가격 확인 대기 중: fal 가격 페이지에서 세 모델 단가를 확인한 뒤 이 값을 고친다.
# 화면 견적에는 쓰지 않는다(그대로 "가격 확인 불가").
FALLBACK_USD_PER_S = {
    MODEL_HD: 0.005,  # fal.ai/models/resemble-ai/chatterboxhd/speech-to-speech
    MODEL_ELEVEN: 0.005,  # fal.ai/models/fal-ai/elevenlabs/voice-changer
    MODEL_LITE: 0.005,  # fal.ai/models/fal-ai/chatterbox/speech-to-speech
}
USING_SAVED_LABEL = "저장된 목소리 사용 중"
NO_PROFILE_LABEL = "목소리 미지정"
# 이 kind 로 실패하면 남은 줄도 같은 이유로 실패한다 — 더 보내지 않고 원래 소리로 둔다.
ABORT_KINDS = ("FalBalanceExhausted", "NoApiKey", "NoFalKey")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_TIME_EPS = 0.001


# ================================================================ 설정 · 키


def enabled() -> bool:
    """VNEW_SOUND_VOICES(기본 켬). vnew.settings._bool 과 같은 읽기(빈 값이면 기본값)."""
    raw = os.getenv(FLAG_ENV, "true").strip().lower()
    if not raw:
        return True
    return raw in ("1", "true", "yes", "on")


def require_enabled(where: str = "vnew.sound.voices") -> None:
    """VNEW_SOUND 또는 VNEW_SOUND_VOICES 가 꺼져 있으면 404(라우트가 없는 것과 같다). 프로젝트를 보기 전에 부른다."""
    sound.require_enabled(where)
    if not enabled():
        raise VpocError(where, "VoicesDisabled", "인물 목소리 기능이 꺼져 있습니다.", 404)


def voices_prefix(owner: str, pid: str, gen_id: str, variant: str) -> str:
    """그 gen·그림체의 사운드 폴더 아래 voices/(기준 소리·들어보기·임시 입력). 사운드 문서(sound.json) 옆이다."""
    return f"{edit._root(owner, pid, gen_id)}/sound/{variant}/voices"  # noqa: SLF001


def _tag(text: str) -> str:
    """키·로그용 짧은 해시(인물 이름·줄 id 를 그대로 쓰지 않는다)."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:10]


# ================================================================ 외부 호출 래퍼(검사가 바꾼다)


def _fal_call(method: str, url: str, payload: dict | None, *, where: str) -> dict:
    """fal 큐 HTTP. vpoc.fal._request 그대로(키는 거기서만 다룬다)."""
    return vpoc_fal._request(method, url, payload, where=where)  # noqa: SLF001


def _fetch_audio(url: str) -> bytes:
    """fal 결과 소리. data URI 면 풀고, https 면 받는다(FETCH_TIMEOUT_S, RESULT_MAX_BYTES 상한, 메모리로만)."""
    where = "vnew.sound.voice_fetch"
    text = str(url or "")
    if text.startswith("data:"):
        encoded = text.partition(",")[2]
        if len(encoded) > RESULT_MAX_BYTES * 4 // 3 + 4:
            raise VpocError(where, "ResultTooLarge", "결과 소리가 너무 큽니다.", 502)
        try:
            return base64.b64decode(encoded)
        except ValueError as exc:
            raise VpocError(where, "BadAudioResult", "받은 소리를 읽지 못했습니다.", 502) from exc
    if not text.startswith("https://"):
        raise VpocError(where, "NoAudioResult", "목소리 모델이 소리를 주지 않았습니다.", 502)
    try:
        with urllib.request.urlopen(text, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310 — https 만
            raw = response.read(RESULT_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise VpocError(where, f"HTTP{exc.code}", f"결과 소리를 받지 못했습니다(HTTP {exc.code}).", 502) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise VpocError(where, "FalUnreachable", "결과 소리를 받는 중 연결이 끊겼습니다.", 502) from exc
    if len(raw) > RESULT_MAX_BYTES:
        raise VpocError(where, "ResultTooLarge", "결과 소리가 너무 큽니다.", 502)
    return raw


def _price(endpoint: str) -> dict | None:
    """fal 단가 {unitPrice, unit} 또는 None. 등록 모델 레지스트리 → pricing API(generate._retake_price 와 같은 순서)."""
    try:
        return vpoc_fal.pricing_for(vpoc_settings.FAL_ID_PREFIX + endpoint) or vpoc_fal.pricing(endpoint)
    except Exception as exc:  # noqa: BLE001 — 단가를 모르면 "가격 확인 불가"
        log.warning("vnew.sound.voice_price_failed model=%s err=%s", endpoint, type(exc).__name__)
        return None


def _charge(key: str, *, model: str, seconds: float, usd: float, owner: str, pid: str, gen_id: str,
            price_unknown: bool = False) -> None:
    """원장 1건(같은 key 는 한 번만). fal 이 요청을 받은 뒤에만 부른다. 작업 220b: price_unknown 이면 원장 항목 priceUnknown: True
    (아는 금액이면 예전과 같은 호출 — priceUnknown 칸 없음)."""
    vpoc_ledger.charge(key, kind="audio", model=model, units=round(float(seconds), 3), usd=float(usd), user=owner,
                       project_id=pid, plan_id=gen_id, **({"price_unknown": True} if price_unknown else {}))


def _charge_usd(item: dict, seconds: float) -> tuple[float, bool]:
    """작업 220b: 원장에 적을 금액과 가격 모름 여부. estimate 가 단가를 알았으면 item["usd"] 그대로 (금액, False).
    몰랐으면(item["priceUnknown"]) FALLBACK_USD_PER_S[모델] × 보낸 길이 (금액, True) — 조용히 0 을 적지 않는다."""
    if not item.get("priceUnknown"):
        return float(item.get("usd") or 0.0), False
    per_s = float(FALLBACK_USD_PER_S.get(str(item.get("model") or ""), max(FALLBACK_USD_PER_S.values())))
    usd = per_s * max(0.0, float(seconds))
    return round(usd if usd > 0 else per_s, 6), True


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _fal_ready() -> bool:
    return bool(vpoc_settings.FAL_KEY)


def _put_audio(key: str, data: bytes) -> None:
    edit.STORE.put_bytes(key, data, "audio/wav", bucket=vpoc_settings.VIDEO_BUCKET)


def _get_audio(key: str) -> bytes:
    return edit.STORE.get_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)


def _presign_audio(key: str, expires: int = PRESIGN_S) -> str:
    return edit.STORE.presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="audio/wav", expires=int(expires))


def _audio_url_for_view(key: str) -> str:
    return edit._presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="audio/wav")  # noqa: SLF001


# ================================================================ wav · 소리 계산(순수 함수)


def read_wav(data: bytes) -> tuple[int, int, array]:
    """PCM s16 wav 바이트 → (sample_rate, channels, 샘플 array('h') — 채널이 번갈아 놓인다). 청크를 직접 읽는다(LIST·EXTENSIBLE 무시)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise stitcher.StitchError("wav 형식이 아닙니다.")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        chunk, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if chunk == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk == b"data":
            pcm = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None or fmt[5] != 16 or fmt[1] < 1 or fmt[2] < 1:
        raise stitcher.StitchError("16비트 PCM wav 가 아닙니다.")
    channels, rate = int(fmt[1]), int(fmt[2])
    samples = array("h")
    usable = len(pcm) // (2 * channels) * (2 * channels)
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    return rate, channels, samples


def wav_bytes(rate: int, channels: int, samples: array) -> bytes:
    """샘플 array('h') → PCM s16 wav 바이트."""
    data = array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    body = data.tobytes()
    header = (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVE" + b"fmt "
              + struct.pack("<IHHIIHH", 16, 1, int(channels), int(rate), int(rate) * int(channels) * 2, int(channels) * 2, 16)
              + b"data" + struct.pack("<I", len(body)))
    return header + body


def mono(samples: array, channels: int) -> array:
    """채널 평균(모노). 이미 모노면 사본."""
    if channels <= 1:
        return array("h", samples)
    out = array("h", bytes(2 * (len(samples) // channels)))
    for i in range(len(out)):
        base = i * channels
        out[i] = int(round(sum(samples[base:base + channels]) / channels))
    return out


def fit_frames(samples: array, channels: int, frames: int) -> array:
    """길이를 frames 로 정확히: 길면 뒤를 자르고, 짧으면 뒤를 무음으로 채운다."""
    want = max(0, int(frames)) * channels
    if len(samples) >= want:
        return array("h", samples[:want])
    return array("h", samples) + array("h", bytes(2 * (want - len(samples))))


def rms(samples) -> float:
    if not len(samples):
        return 0.0
    return math.sqrt(sum(float(v) * v for v in samples) / len(samples))


def match_gain(segment, reference) -> float:
    """segment 를 reference 와 같은 RMS 로 만드는 배율(GAIN_MIN~GAIN_MAX). 어느 쪽이든 거의 무음이면 1."""
    have, want = rms(segment), rms(reference)
    if have < 1.0 or want < 1.0:
        return 1.0
    return min(GAIN_MAX, max(GAIN_MIN, want / have))


def scaled(samples, gain: float) -> array:
    if abs(gain - 1.0) < 1e-9:
        return array("h", samples)
    return array("h", (max(-32768, min(32767, int(round(v * gain)))) for v in samples))


def splice(samples: array, segment: array, start_frame: int, channels: int, fade_frames: int) -> None:
    """samples 의 start_frame 부터 segment 를 끼운다(제자리, 길이 그대로). 양 끝 fade_frames 는 원래 소리와 직선으로 섞는다.

    섞는 비율 w(새 소리 몫)는 앞 끝에서 (i+0.5)/fade 로 오르고 뒤 끝에서 같은 꼴로 내려간다 — 구간 밖 샘플은 바뀌지 않는다."""
    frames = len(segment) // channels
    start = max(0, int(start_frame))
    frames = min(frames, len(samples) // channels - start)
    if frames <= 0:
        return
    fade = max(0, min(int(fade_frames), frames // 2))
    middle_lo, middle_hi = fade, frames - fade
    if middle_hi > middle_lo:
        samples[(start + middle_lo) * channels:(start + middle_hi) * channels] = segment[middle_lo * channels:middle_hi * channels]
    for i in list(range(0, fade)) + list(range(frames - fade, frames)):
        w = (i + 0.5) / fade if i < fade else (frames - i - 0.5) / fade
        for c in range(channels):
            at = (start + i) * channels + c
            samples[at] = max(-32768, min(32767, int(round(samples[at] * (1.0 - w) + segment[i * channels + c] * w))))


def unit_usd(price: dict | None, seconds: float) -> float | None:
    """fal 단가 × 보낸 길이. unit 이 초·분이면 길이로, 요청 단위면 그대로, 모르는 단위(글자 수 등)·단가 없음이면 None."""
    if not isinstance(price, dict):
        return None
    try:
        unit_price = float(price.get("unitPrice"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(unit_price) or unit_price < 0:
        return None
    unit = str(price.get("unit") or "").strip().lower()
    if "second" in unit or unit in ("s", "sec", "secs"):
        return unit_price * float(seconds)
    if "minute" in unit or unit in ("min", "mins"):
        return unit_price * float(seconds) / 60.0
    if unit in ("", "request", "requests", "call", "calls", "generation", "generations", "unit", "units", "audio", "audios"):
        return unit_price
    return None


# ================================================================ ffmpeg


def _ffmpeg(args: list[str], what: str) -> None:
    command = [stitcher.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", *args]
    result = stitcher._edit_run(command)  # noqa: SLF001
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace")[:300]
        raise stitcher.StitchError(f"{what}: {detail}")


def _cut_mono(source_path: str, out_path: str, start: float, seconds: float) -> None:
    """원본 영상 소리의 [start, start+seconds] → wav 모노 EXCERPT_RATE s16(기준 소리·들어보기 입력)."""
    _ffmpeg(["-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.0, seconds):.3f}", "-i", source_path, "-map", "0:a:0",
             "-ac", "1", "-ar", str(EXCERPT_RATE), "-c:a", "pcm_s16le", "-f", "wav", out_path], "소리를 자르지 못했습니다")


def _decode_wav(source_path: str, out_path: str) -> None:
    """원본 영상 소리 전체 → wav s16(원래 샘플레이트·채널 그대로)."""
    _ffmpeg(["-i", source_path, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "wav", out_path], "원래 소리를 읽지 못했습니다")


def _decode_result(in_path: str, out_path: str, rate: int, channels: int) -> array:
    """fal 결과 소리(mp3·wav 등) → rate·channels 의 s16 샘플."""
    _ffmpeg(["-i", in_path, "-map", "0:a:0", "-ac", str(channels), "-ar", str(rate), "-c:a", "pcm_s16le", "-f", "s16le", out_path],
            "바뀐 소리를 읽지 못했습니다")
    samples = array("h")
    with open(out_path, "rb") as handle:
        raw = handle.read()
    samples.frombytes(raw[:len(raw) // (2 * channels) * (2 * channels)])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _thought_wav(in_path: str, out_path: str) -> None:
    _ffmpeg(["-i", in_path, "-af", sound.THOUGHT_FILTER, "-c:a", "pcm_s16le", "-f", "wav", out_path], "속마음 효과를 걸지 못했습니다")


# ================================================================ 프로필


def _plan_voices(plan: dict) -> dict:
    value = (plan or {}).get("voices")
    return value if isinstance(value, dict) else {}


def known_speakers(plan: dict) -> list[str]:
    """대본 대사 줄의 인물 이름(사운드 줄과 같은 정리 — sound._script_lines). 순서 그대로, 겹치면 한 번."""
    return list(dict.fromkeys(l["speaker"] for l in sound._script_lines(plan) if l["speaker"]))  # noqa: SLF001


def _usable(profile) -> bool:
    if not isinstance(profile, dict):
        return False
    mode, model = profile.get("mode"), profile.get("model")
    spec = MODELS.get(str(model or ""))
    if mode not in MODES or spec is None or mode not in spec["modes"]:
        return False
    if mode == MODE_PRESET:
        return profile.get("preset") in spec["presets"]
    ref = profile.get("ref")
    return isinstance(ref, dict) and isinstance(ref.get("key"), str) and bool(ref["key"])


def resolve(plan: dict, speaker: str, variant: str) -> dict | None:
    """그 인물·그림체에 쓸 프로필 {mode, model, preset, ref, scope "variant"|"shared"}. 그림체 덮어쓰기가 먼저, 없으면 함께 쓰는 것."""
    entry = _plan_voices(plan).get(speaker)
    if not isinstance(entry, dict):
        return None
    over = (entry.get("perVariant") or {}).get(variant) if isinstance(entry.get("perVariant"), dict) else None
    for scope, profile in (("variant", over), ("shared", entry)):
        if _usable(profile):
            return {"mode": profile["mode"], "model": profile["model"], "preset": str(profile.get("preset") or ""),
                    "ref": dict(profile["ref"]) if isinstance(profile.get("ref"), dict) else None, "scope": scope}
    return None


def _public_profile(profile) -> dict | None:
    """보기용: 키 대신 들어볼 주소(ref.url)."""
    if not isinstance(profile, dict) or profile.get("mode") not in MODES:
        return None
    out = {"mode": profile["mode"], "model": str(profile.get("model") or ""), "preset": str(profile.get("preset") or ""),
           "ref": None, "updatedAt": str(profile.get("updatedAt") or "")}
    ref = profile.get("ref")
    if isinstance(ref, dict):
        out["ref"] = {k: ref.get(k) for k in ("genId", "variant", "lineId", "start", "end", "seconds")}
        out["ref"]["url"] = _audio_url_for_view(str(ref.get("key") or ""))
    if "scope" in profile:
        out["scope"] = profile["scope"]
    return out


def view(owner: str, pid: str, plan: dict, gen_id: str, variant: str, doc: dict | None) -> dict:
    """사운드 보기의 voices: 인물마다 지금 쓸 프로필·상태 문구, 저장된 프로필 전체, 모델별 목소리 이름 목록."""
    lines = [l for l in (doc or {}).get("lines") or [] if isinstance(l, dict)]
    counts: dict[str, int] = {}
    for line in lines:
        name = str(line.get("speaker") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    names = list(dict.fromkeys(list(counts) + known_speakers(plan)))
    speakers = []
    for name in names:
        profile = resolve(plan, name, variant)
        speakers.append({"speaker": name, "lines": counts.get(name, 0), "profile": _public_profile(profile),
                         "scope": profile["scope"] if profile else "", "usingSaved": bool(profile),
                         "status": USING_SAVED_LABEL if profile else NO_PROFILE_LABEL})
    profiles = {}
    for name, entry in _plan_voices(plan).items():
        if not isinstance(entry, dict):
            continue
        per = entry.get("perVariant") if isinstance(entry.get("perVariant"), dict) else {}
        profiles[name] = {"shared": _public_profile(entry),
                          "perVariant": {v: _public_profile(p) for v, p in per.items() if v in sound.VARIANTS and _public_profile(p)}}
    models = [{"id": mid, "label": spec["label"], "modes": list(spec["modes"]), "presets": list(spec["presets"])}
              for mid, spec in MODELS.items()]
    return {"enabled": True, "genId": gen_id, "variant": variant, "speakers": speakers, "profiles": profiles, "models": models,
            "defaults": dict(DEFAULT_MODEL), "priceUnknownLabel": PRICE_UNKNOWN_LABEL,
            "limits": {"padSeconds": PAD_S, "fadeSeconds": FADE_S, "refMaxSeconds": REF_MAX_S, "refMinSeconds": REF_MIN_S}}


def _make_ref(owner: str, pid: str, gen: dict, gen_id: str, raw, speaker: str, default_variant: str, where: str) -> dict:
    """기준 대사 한 줄에서 기준 소리(wav 모노, REF_MAX_S 이하)를 잘라 voices/ 에 올린다. 반환 ref."""
    if not isinstance(raw, dict):
        raise VpocError(where, "BadVoiceRef", "기준으로 쓸 대사(ref.lineId)가 필요합니다.", 400)
    variant = sound._variant_of(raw.get("variant") or default_variant, gen, where)  # noqa: SLF001
    if raw.get("genId") not in (None, "", gen_id):
        raise VpocError(where, "BadVoiceRef", "지금 영상의 대사만 기준으로 쓸 수 있습니다. 다시 불러온 뒤 골라 주세요.", 400)
    doc, source = sound._current_doc(owner, pid, gen_id, gen, variant, where)  # noqa: SLF001
    if not doc["source"].get("hasAudio"):
        raise VpocError(where, "BadAudio", "이 영상에는 소리가 없어 바꿀 수 없습니다", 400)
    line_id = raw.get("lineId")
    line = next((l for l in doc["lines"] if isinstance(l, dict) and isinstance(line_id, str) and l.get("id") == line_id), None)
    if line is None:
        raise VpocError(where, "BadVoiceRef", "기준으로 고른 대사를 찾을 수 없습니다. 다시 불러온 뒤 골라 주세요.", 400)
    if str(line.get("speaker") or "") != speaker:
        raise VpocError(where, "BadVoiceRef", f"고른 대사는 {speaker}의 대사가 아닙니다.", 400)
    duration = edit._num(doc["source"].get("duration")) or 0.0  # noqa: SLF001
    start = edit._num(raw["start"]) if "start" in raw else edit._num(line.get("start"))  # noqa: SLF001
    end = edit._num(raw["end"]) if "end" in raw else edit._num(line.get("end"))  # noqa: SLF001
    if start is None or end is None or start < 0 or end > duration + _TIME_EPS or end - start < REF_MIN_S - 1e-9:
        raise VpocError(where, "BadVoiceRefRange",
                        f"기준 구간은 원본 길이({duration:.2f}초) 안에서 {REF_MIN_S}초 이상이어야 합니다.", 400)
    start, end = round(start, 3), round(min(end, duration, start + REF_MAX_S), 3)
    key = f"{voices_prefix(owner, pid, gen_id, variant)}/ref-{_tag(speaker)}-{secrets.token_hex(4)}.wav"
    with edit._MEDIA_SEM:  # noqa: SLF001 — 원본 내려받기·ffmpeg 는 프로세스당 하나
        with tempfile.TemporaryDirectory(prefix="vnew-voice-") as workdir:
            source_path = os.path.join(workdir, "source.mp4")
            try:
                sound._download(source["key"], source_path)  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.sound.voice_ref_failed stage=download err=%s", type(exc).__name__)
                raise VpocError(where, "SourceUnreadable", "원본 영상을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409) from None
            out_path = os.path.join(workdir, "ref.wav")
            try:
                _cut_mono(source_path, out_path, start, end - start)
                with open(out_path, "rb") as handle:
                    data = handle.read()
                rate, channels, samples = read_wav(data)
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.sound.voice_ref_failed stage=cut err=%s", error_gist(f"{type(exc).__name__}: {exc}", 200))
                raise VpocError(where, "VoiceRefFailed", "기준 목소리를 자르지 못했습니다.", 500) from None
            if channels != 1 or not len(samples):
                raise VpocError(where, "VoiceRefFailed", "기준 목소리를 자르지 못했습니다.", 500)
            _put_audio(key, data)
    seconds = round(len(samples) / rate, 3)
    return {"genId": gen_id, "variant": variant, "lineId": line_id, "start": start, "end": end, "seconds": seconds, "key": key}


def save_profile(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/sound/voices {speaker, mode clip|preset|off, ref?{lineId, start?, end?, variant?, genId?}, preset?, model?, variant?}.

    variant 를 주면 그 그림체만 덮어쓴다(perVariant), 없으면 두 그림체가 함께 쓴다. 프로필은 plan.voices 에 두고 revision + 1.
    400: 모르는 인물(UnknownSpeaker)·mode·model(BadVoiceModel)·목소리 이름(BadVoicePreset)·기준 대사(BadVoiceRef)·원본 밖 구간(BadVoiceRefRange).
    반환 {"voices": 보기}(그림체 = variant 또는 gen 이 고른 것).
    """
    where = "vnew.sound.voices"
    payload = payload if isinstance(payload, dict) else {}
    speaker = clean_text(payload.get("speaker"), sound.MAX_SPEAKER_LEN) if isinstance(payload.get("speaker"), str) else ""
    if not speaker:
        raise VpocError(where, "BadSpeaker", "인물 이름(speaker)이 필요합니다.", 400)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    if speaker not in known_speakers(plan):
        raise VpocError(where, "UnknownSpeaker", f"대본에 없는 인물입니다: {speaker}", 400)
    mode = payload.get("mode")
    if mode not in (MODE_CLIP, MODE_PRESET, MODE_OFF):
        raise VpocError(where, "BadVoiceMode", "mode 는 clip·preset·off 중 하나여야 합니다.", 400)
    raw_variant = payload.get("variant")
    override = raw_variant not in (None, "")
    variant = sound._variant_of(raw_variant, gen, where)  # noqa: SLF001
    profile = None
    if mode != MODE_OFF:
        model = payload.get("model") if payload.get("model") not in (None, "") else DEFAULT_MODEL[mode]
        spec = MODELS.get(model) if isinstance(model, str) else None
        if spec is None or mode not in spec["modes"]:
            raise VpocError(where, "BadVoiceModel", f"이 목소리 방식({mode})에 쓸 수 없는 모델입니다.", 400)
        preset, ref = "", None
        if mode == MODE_PRESET:
            preset = payload.get("preset")
            if not isinstance(preset, str) or preset not in spec["presets"]:
                raise VpocError(where, "BadVoicePreset", f"{spec['label']}에 없는 목소리 이름입니다.", 400)
        else:
            ref = _make_ref(owner, pid, gen, gen_id, payload.get("ref"), speaker, variant, where)
        profile = {"mode": mode, "model": model, "preset": preset, "ref": ref, "updatedAt": now_iso()}

    def mutator(data: dict) -> None:
        voices = data.get("voices") if isinstance(data.get("voices"), dict) else {}
        entry = dict(voices.get(speaker)) if isinstance(voices.get(speaker), dict) else {}
        if override:
            per = dict(entry.get("perVariant")) if isinstance(entry.get("perVariant"), dict) else {}
            if profile:
                per[variant] = profile
            else:
                per.pop(variant, None)
            entry["perVariant"] = per
        elif profile:
            entry.update(profile)
        else:
            for name in ("mode", "model", "preset", "ref", "updatedAt"):
                entry.pop(name, None)
        if not entry.get("perVariant"):
            entry.pop("perVariant", None)
        if entry.get("mode") in MODES or entry.get("perVariant"):
            voices[speaker] = entry
        else:
            voices.pop(speaker, None)
        data["voices"] = voices
        data["revision"] = int(data.get("revision") or 0) + 1

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    log.info("vnew.sound.voice_saved pid=%s gen=%s speaker=%s mode=%s model=%s scope=%s", pid, gen_id, _tag(speaker), mode,
             (profile or {}).get("model") or "-", variant if override else "shared")
    doc, _ = edit._read_json(sound.doc_key(owner, pid, gen_id, variant))  # noqa: SLF001
    return {"voices": view(owner, pid, updated, gen_id, variant, doc)}


# ================================================================ 견적 · 확인 금액


def _confirm(payload: dict, where: str) -> float:
    confirm = payload.get("confirmUsd")
    try:
        value = float("nan") if isinstance(confirm, bool) else float(confirm)
    except (TypeError, ValueError):
        value = float("nan")
    if not math.isfinite(value):
        raise VpocError(where, "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400)
    return value


def estimate(items: list[dict]) -> dict:
    """줄마다 금액(보낸 길이 = 패딩 포함) · 합계. 하나라도 단가를 모르면 usd None·priceKnown False·label "가격 확인 불가".
    item["usd"](아는 금액, 모르면 0)를 채운다. 작업 220b: 모르면 item["priceUnknown"] = True(원장은 _charge_usd 추정)."""
    prices: dict[str, dict | None] = {}
    total, known, out = 0.0, True, []
    for item in items:
        model = item["model"]
        if model not in prices:
            prices[model] = _price(model)
        seconds = round(float(item["padEnd"]) - float(item["padStart"]), 3)
        usd = unit_usd(prices[model], seconds)
        item["usd"] = float(usd or 0.0)
        item.pop("priceUnknown", None)
        if usd is None:
            known = False
            item["priceUnknown"] = True
        else:
            total += usd
        out.append({"lineId": item["lineId"], "speaker": item["speaker"], "model": model, "seconds": seconds,
                    "usd": round(usd, 4) if usd is not None else None})
    usd_total = round(total, 2) if known else None
    return {"usd": usd_total, "priceKnown": known, "label": f"{usd_total:.2f} USD" if known else PRICE_UNKNOWN_LABEL,
            "requests": len(items), "lines": out,
            "models": {m: ({"unitPrice": p.get("unitPrice"), "unit": p.get("unit")} if isinstance(p, dict) else None)
                       for m, p in prices.items()}}


def _check_cost(est: dict, confirm: float, where: str) -> None:
    if est["priceKnown"] and round(confirm, 2) != est["usd"]:
        raise VpocError(where, "CostMismatch", f"금액이 서버 계산과 다릅니다. 서버 {est['usd']} USD, 화면 {round(confirm, 2)} USD.", 409)


def _line_item(line: dict, profile: dict, duration: float) -> dict:
    start, end = float(line.get("start") or 0.0), float(line.get("end") or 0.0)
    return {"lineId": str(line.get("id")), "speaker": str(line.get("speaker") or ""), "voice": str(line.get("voice") or "spoken"),
            "start": round(start, 3), "end": round(end, 3),
            "padStart": round(max(0.0, start - PAD_S), 3), "padEnd": round(min(duration, end + PAD_S), 3),
            "mode": profile["mode"], "model": profile["model"], "preset": profile["preset"],
            "refKey": str((profile.get("ref") or {}).get("key") or ""), "scope": profile["scope"]}


# ================================================================ fal 목소리 바꾸기 1회


def _audio_input(owner: str, pid: str, gen_id: str, variant: str, data: bytes) -> tuple[str, str]:
    """fal 에 보낼 소리 주소: 작으면 data URI, 크면 voices/tmp/ 에 올린 서명 주소(PRESIGN_S). 반환 (주소, "data"|"presigned")."""
    if len(data) <= DATA_URI_MAX_BYTES:
        return "data:audio/wav;base64," + base64.b64encode(data).decode("ascii"), "data"
    key = f"{voices_prefix(owner, pid, gen_id, variant)}/tmp/in-{secrets.token_hex(6)}.wav"
    _put_audio(key, data)
    return _presign_audio(key, PRESIGN_S), "presigned"


def _ref_input(owner: str, pid: str, ref_key: str, where: str) -> str:
    if not ref_key.startswith(f"users/{owner}/video/{pid}/") or "/sound/" not in ref_key:
        raise VpocError(where, "NoVoiceRef", "기준 목소리를 찾을 수 없습니다. 목소리를 다시 정해 주세요.", 409)
    try:
        data = _get_audio(ref_key)
    except Exception:  # noqa: BLE001
        raise VpocError(where, "NoVoiceRef", "기준 목소리를 찾을 수 없습니다. 목소리를 다시 정해 주세요.", 409) from None
    if len(data) <= DATA_URI_MAX_BYTES:
        return "data:audio/wav;base64," + base64.b64encode(data).decode("ascii")
    return _presign_audio(ref_key, PRESIGN_S)


def _seed(speaker: str) -> int:
    """인물마다 고정 seed(같은 인물은 늘 같은 값 — ElevenLabs 결과를 덜 흔든다)."""
    return int(hashlib.sha256(str(speaker).encode("utf-8")).hexdigest()[:6], 16)


def _transient(exc: VpocError) -> bool:
    return exc.kind in ("FalUnreachable", "HTTP429") or str(exc.kind).startswith("HTTP5")


def convert(owner: str, pid: str, gen_id: str, variant: str, item: dict, excerpt: bytes, *, wait_s: float, where: str,
            charged: list) -> tuple[bytes, dict]:
    """fal 목소리 바꾸기 1회: 제출 → (받으면 원장 1건) → 조회 → 결과 소리 바이트. 실패하면 VpocError. charged 에 청구한 금액을 더한다."""
    spec = MODELS[item["model"]]
    body = dict(spec["extra"])
    body[spec["source"]], input_kind = _audio_input(owner, pid, gen_id, variant, excerpt)
    if item["mode"] == MODE_CLIP:
        body[spec["ref"]] = _ref_input(owner, pid, item["refKey"], where)
    else:
        body[spec["preset"]] = item["preset"]
    if spec["seed"]:
        body[spec["seed"]] = _seed(item["speaker"])
    endpoint = item["model"]
    started = time.monotonic()
    submitted = _fal_call("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}", body, where=where)
    request_id = str((submitted or {}).get("request_id") or "") if isinstance(submitted, dict) else ""
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise VpocError(where, "NoRequestId", "목소리 모델이 요청 ID를 주지 않았습니다.", 502)
    seconds = round(float(item["padEnd"]) - float(item["padStart"]), 3)
    usd, unknown = _charge_usd(item, seconds)
    try:
        _charge("vnew-voice-" + request_id, model=endpoint, seconds=seconds, usd=usd, owner=owner, pid=pid, gen_id=gen_id,
                **({"price_unknown": True} if unknown else {}))
        charged.append(usd)
        if unknown:
            log.warning("vnew.sound.voice_price_unknown pid=%s model=%s usd_estimate=%.6f", pid, endpoint, usd)
    except Exception as exc:  # noqa: BLE001 — 원장 실패는 변환을 막지 않는다(generate 와 같다).
        log.warning("vnew.sound.voice_charge_failed pid=%s err=%s", pid, type(exc).__name__)
    deadline = started + float(wait_s)
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
            raise VpocError(where, f"Fal{state.title()}", error_gist(detail, 300) or state, 502)
        if time.monotonic() >= deadline:
            try:
                vpoc_fal.cancel(f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}")
            except Exception:  # noqa: BLE001 — 취소는 최선만
                pass
            raise VpocError(where, "Timeout", "목소리 모델이 시간 안에 결과를 주지 않았습니다(timeout).", 504)
        _sleep(POLL_S)
    try:
        result = _fal_call("GET", vpoc_fal._response_url(endpoint, request_id), None, where=where)  # noqa: SLF001
    except VpocError as exc:
        if exc.kind not in vpoc_fal._REJECT_HTTP:  # noqa: SLF001
            raise
        _kind, message = vpoc_fal.rejection(exc.message, exc.kind)
        raise VpocError(where, "VoiceRejected", f"{message} ({exc.kind})", 422) from None
    if isinstance(result, dict) and result.get("error"):
        raise VpocError(where, "VoiceFailed", error_gist(str(result.get("error")), 300), 502)
    audio = result.get("audio") if isinstance(result, dict) else None
    url = audio.get("url") if isinstance(audio, dict) else audio if isinstance(audio, str) else ""
    if not isinstance(url, str) or not url:
        raise VpocError(where, "NoAudioResult", "목소리 모델이 소리를 주지 않았습니다.", 502)
    data = _fetch_audio(url)
    if not data:
        raise VpocError(where, "NoAudioResult", "목소리 모델이 빈 소리를 주었습니다.", 502)
    log.info("vnew.sound.voice_converted pid=%s gen=%s variant=%s model=%s input=%s ms=%d", pid, gen_id, variant, endpoint,
             input_kind, int((time.monotonic() - started) * 1000))
    return data, {"input": input_kind}


def _note(owner: str, pid: str, gen_id: str, variant: str, item: dict, exc: BaseException) -> None:
    """작업 198: 목소리 바꾸기 실패·거절 노트 1줄(예외를 내지 않는다)."""
    message = exc.message if isinstance(exc, VpocError) else f"{type(exc).__name__}: {exc}"
    kind = getattr(exc, "kind", "")
    vnew_model_notes.record(owner, pid, gen_id, {
        "variant": variant, "stage": "voice", "target": {"retakeCell": f"voice:{item.get('lineId')}"},
        "model": item.get("model"), "attempt": 1, "providerMessage": f"{kind}: {message}" if kind else message,
        "kind": "timeout" if kind == "Timeout" else "", "action": "gaveUp"})


# ================================================================ 들어보기(한 줄)


def preview(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/voice-preview {variant, lineId, speaker?, confirmUsd, dryRun?}. 그 줄 구간(앞뒤 PAD_S)만 바꿔 저장한다.

    dryRun true 면 견적만 {estimate}. 아니면 confirmUsd(400 NoConfirmUsd·409 CostMismatch) → fal(받으면 원장 1건) → 소리 크기를 원래 구간에
    맞추고(속마음 줄은 효과까지) voices/preview-*.wav 로 저장. 반환 {preview{url, lineId, speaker, start, end, seconds, model, mode}, estimate, charge}.
    """
    where = "vnew.sound.voice_preview"
    payload = payload if isinstance(payload, dict) else {}
    dry = payload.get("dryRun") is True
    confirm = None if dry else _confirm(payload, where)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(payload.get("variant"), gen, where)  # noqa: SLF001
    doc, source = sound._current_doc(owner, pid, gen_id, gen, variant, where)  # noqa: SLF001
    if not doc["source"].get("hasAudio"):
        raise VpocError(where, "BadAudio", "이 영상에는 소리가 없어 바꿀 수 없습니다", 400)
    line_id = payload.get("lineId")
    line = next((l for l in doc["lines"] if isinstance(l, dict) and isinstance(line_id, str) and l.get("id") == line_id), None)
    if line is None:
        raise VpocError(where, "BadLine", "들어볼 대사를 찾을 수 없습니다. 다시 불러온 뒤 골라 주세요.", 400)
    speaker = str(line.get("speaker") or "")
    if payload.get("speaker") not in (None, ""):
        speaker = clean_text(payload.get("speaker"), sound.MAX_SPEAKER_LEN) if isinstance(payload.get("speaker"), str) else ""
        if speaker not in known_speakers(plan):
            raise VpocError(where, "UnknownSpeaker", f"대본에 없는 인물입니다: {speaker}", 400)
    if not speaker:
        raise VpocError(where, "BadSpeaker", "인물이 정해지지 않은 대사입니다.", 400)
    profile = resolve(plan, speaker, variant)
    if profile is None:
        raise VpocError(where, "NoVoiceProfile", f"{speaker}의 목소리를 먼저 정해 주세요.", 409)
    duration = edit._num(doc["source"].get("duration")) or 0.0  # noqa: SLF001
    item = {**_line_item(line, profile, duration), "speaker": speaker}
    est = estimate([item])
    if dry:
        return {"estimate": est}
    _check_cost(est, confirm, where)
    if not _fal_ready():
        raise VpocError(where, "NoFalKey", "fal 키(FAL_KEY)가 설정되지 않았습니다.", 409)
    seconds = round(item["padEnd"] - item["padStart"], 3)
    charged: list = []
    key = f"{voices_prefix(owner, pid, gen_id, variant)}/preview-{_tag(item['lineId'])}-{secrets.token_hex(4)}.wav"
    with tempfile.TemporaryDirectory(prefix="vnew-voice-") as workdir:
        source_path, excerpt_path = os.path.join(workdir, "source.mp4"), os.path.join(workdir, "excerpt.wav")
        with edit._MEDIA_SEM:  # noqa: SLF001 — 내려받기·자르기만 잡는다(fal 기다림은 밖에서)
            try:
                sound._download(source["key"], source_path)  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.sound.voice_preview_failed stage=download err=%s", type(exc).__name__)
                raise VpocError(where, "SourceUnreadable", "원본 영상을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409) from None
            _cut_mono(source_path, excerpt_path, item["padStart"], seconds)
            os.remove(source_path)
        with open(excerpt_path, "rb") as handle:
            excerpt = handle.read()
        rate, _channels, original = read_wav(excerpt)
        try:
            data, _meta = convert(owner, pid, gen_id, variant, item, excerpt, wait_s=PREVIEW_WAIT_S, where=where, charged=charged)
            result_path = os.path.join(workdir, "result.bin")
            with open(result_path, "wb") as handle:
                handle.write(data)
            converted = fit_frames(_decode_result(result_path, os.path.join(workdir, "result.raw"), rate, 1), 1, len(original))
        except Exception as exc:  # noqa: BLE001
            _note(owner, pid, gen_id, variant, item, exc)
            log.warning("vnew.sound.voice_preview_failed pid=%s gen=%s variant=%s model=%s err=%s", pid, gen_id, variant,
                        item["model"], getattr(exc, "kind", type(exc).__name__))
            if isinstance(exc, VpocError):
                raise
            raise VpocError(where, "VoiceFailed", "목소리를 바꾸지 못했습니다.", 502) from None
        converted = scaled(converted, match_gain(converted, original))
        out_path = os.path.join(workdir, "preview.wav")
        with open(out_path, "wb") as handle:
            handle.write(wav_bytes(rate, 1, converted))
        if item["voice"] == "thought":  # 속마음 줄은 반영 때와 같이 효과까지
            thought_path = os.path.join(workdir, "preview-thought.wav")
            _thought_wav(out_path, thought_path)
            out_path = thought_path
        with open(out_path, "rb") as handle:
            _put_audio(key, handle.read())
    log.info("vnew.sound.voice_preview pid=%s gen=%s variant=%s model=%s seconds=%.3f usd=%.4f", pid, gen_id, variant,
             item["model"], seconds, sum(charged))
    return {"preview": {"url": _audio_url_for_view(key), "lineId": item["lineId"], "speaker": speaker, "start": item["start"],
                        "end": item["end"], "padStart": item["padStart"], "padEnd": item["padEnd"], "seconds": seconds,
                        "model": item["model"], "mode": item["mode"], "scope": item["scope"]},
            "estimate": est, "charge": {"usd": round(sum(charged), 4), "requests": len(charged), "priceKnown": est["priceKnown"]}}


# ================================================================ 전체 반영


def start_voice_apply(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/voice-apply {variant, speakers:[...], confirmUsd, dryRun?}. 목소리가 정해진 인물의 줄을 모두 바꿔 새 v{n}.mp4.

    dryRun true 면 견적만 {estimate}(줄별 합). 아니면 confirmUsd 확인 뒤 sound.start_apply(job.voices = 줄별 계획) — 202 {started, job, estimate}.
    400: speakers 목록·모르는 인물(UnknownSpeaker), 409: 목소리 미지정(NoVoiceProfile)·CostMismatch·SoundRunning·NoFalKey.
    """
    where = "vnew.sound.voice_apply"
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("speakers")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SPEAKERS or not all(isinstance(s, str) for s in raw):
        raise VpocError(where, "BadSpeakers", f"인물 목록(speakers)은 1~{MAX_SPEAKERS}명의 이름 목록이어야 합니다.", 400)
    wanted = list(dict.fromkeys(clean_text(s, sound.MAX_SPEAKER_LEN) for s in raw))
    dry = payload.get("dryRun") is True
    confirm = None if dry else _confirm(payload, where)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(payload.get("variant"), gen, where)  # noqa: SLF001
    sound.reconcile_job(owner, pid, gen_id, variant)
    doc, _source = sound._current_doc(owner, pid, gen_id, gen, variant, where)  # noqa: SLF001
    if not doc["source"].get("hasAudio"):
        raise VpocError(where, "BadAudio", "이 영상에는 소리가 없어 바꿀 수 없습니다", 400)
    lines = [l for l in doc["lines"] if isinstance(l, dict)]
    in_doc = {str(l.get("speaker") or "") for l in lines}
    unknown = [s for s in wanted if not s or s not in in_doc]
    if unknown:
        raise VpocError(where, "UnknownSpeaker", "이 영상 대사에 없는 인물입니다: " + ", ".join(unknown), 400)
    profiles = {s: resolve(plan, s, variant) for s in wanted}
    missing = [s for s, p in profiles.items() if p is None]
    if missing:
        raise VpocError(where, "NoVoiceProfile", "목소리를 먼저 정해 주세요: " + ", ".join(missing), 409)
    duration = edit._num(doc["source"].get("duration")) or 0.0  # noqa: SLF001
    items = [_line_item(l, profiles[str(l.get("speaker"))], duration) for l in lines if str(l.get("speaker") or "") in profiles]
    est = estimate(items)
    if dry:
        return {"estimate": est}
    _check_cost(est, confirm, where)
    if not _fal_ready():
        raise VpocError(where, "NoFalKey", "fal 키(FAL_KEY)가 설정되지 않았습니다.", 409)
    voice_job = {"items": items, "speakers": wanted, "estimate": {"usd": est["usd"], "priceKnown": est["priceKnown"]}}
    started = sound.start_apply(owner, pid, {"variant": variant}, voice_job=voice_job)
    log.info("vnew.sound.voice_apply_start pid=%s gen=%s variant=%s speakers=%d lines=%d known=%s", pid, gen_id, variant,
             len(wanted), len(items), est["priceKnown"])
    return {**started, "estimate": est}


def build_track(owner: str, pid: str, gen_id: str, variant: str, source_path: str, workdir: str, voice_job: dict, *,
                beat) -> tuple[str, dict]:
    """sound.run_apply 가 부른다: 원래 소리 전체(wav, 원래 샘플레이트·채널)에서 줄마다 목소리를 바꿔 끼운 트랙 경로와 요약.

    줄마다: 패딩 구간을 모노로 잘라 보냄 → 결과를 패딩 길이로 맞춤 → 원래 구간 몫만(앞 패딩 건너뜀, 길이 정확히) → RMS 맞춤 → 크로스페이드로 끼움.
    실패한 줄은 원래 소리 그대로(모델 노트). beat({done, total}) 는 줄마다(claim 을 잃으면 예외가 그대로 올라간다)."""
    where = "vnew.sound.voice_apply"
    full_path = os.path.join(workdir, "voice-full.wav")
    _decode_wav(source_path, full_path)
    with open(full_path, "rb") as handle:
        rate, channels, original = read_wav(handle.read())
    os.remove(full_path)
    total_frames = len(original) // channels
    track = array("h", original)
    fade = int(round(FADE_S * rate))
    items = [i for i in voice_job.get("items") or [] if isinstance(i, dict)]
    done_ids: list[str] = []
    kept_ids: list[str] = []
    charged: list = []
    speakers: dict[str, dict] = {}
    abort = ""
    for index, item in enumerate(items):
        beat({"done": index, "total": len(items)})
        entry = speakers.setdefault(item["speaker"], {"mode": item["mode"], "model": item["model"], "preset": item["preset"],
                                                      "scope": item.get("scope") or "", "converted": [], "kept": []})
        if abort:
            kept_ids.append(item["lineId"])
            entry["kept"].append(item["lineId"])
            continue
        try:
            p0 = max(0, min(total_frames, int(round(float(item["padStart"]) * rate))))
            p1 = max(p0, min(total_frames, int(round(float(item["padEnd"]) * rate))))
            s0 = max(p0, min(p1, int(round(float(item["start"]) * rate))))
            s1 = max(s0, min(p1, int(round(float(item["end"]) * rate))))
            if s1 - s0 <= 2 * fade:
                raise stitcher.StitchError("구간이 너무 짧습니다.")
            excerpt = wav_bytes(rate, 1, mono(original[p0 * channels:p1 * channels], channels))
            data, _meta = convert(owner, pid, gen_id, variant, item, excerpt, wait_s=LINE_WAIT_S, where=where, charged=charged)
            result_path = os.path.join(workdir, f"voice-{index}.bin")
            with open(result_path, "wb") as handle:
                handle.write(data)
            raw_path = os.path.join(workdir, f"voice-{index}.raw")
            converted = fit_frames(_decode_result(result_path, raw_path, rate, channels), channels, p1 - p0)
            for path in (result_path, raw_path):
                os.remove(path)
            segment = converted[(s0 - p0) * channels:(s1 - p0) * channels]
            segment = scaled(segment, match_gain(segment, original[s0 * channels:s1 * channels]))
            splice(track, segment, s0, channels, fade)
            done_ids.append(item["lineId"])
            entry["converted"].append(item["lineId"])
        except sound._ClaimLost:  # noqa: SLF001 — 다른 실행이 가져갔다: 더 보내지 않는다
            raise
        except Exception as exc:  # noqa: BLE001 — 그 줄만 원래 소리로 둔다
            kept_ids.append(item["lineId"])
            entry["kept"].append(item["lineId"])
            _note(owner, pid, gen_id, variant, item, exc)
            kind = getattr(exc, "kind", type(exc).__name__)
            log.warning("vnew.sound.voice_line_failed pid=%s gen=%s variant=%s line=%s model=%s err=%s", pid, gen_id, variant,
                        _tag(item["lineId"]), item["model"], kind)
            if kind in ABORT_KINDS:
                abort = kind
    beat({"done": len(items), "total": len(items)})
    if len(track) != len(original):  # 길이는 바뀌지 않는다(splice 는 제자리)
        raise stitcher.StitchError("소리 트랙 길이가 바뀌었습니다.")
    out_path = os.path.join(workdir, "voice-track.wav")
    with open(out_path, "wb") as handle:
        handle.write(wav_bytes(rate, channels, track))
    message = f"{len(done_ids)}/{len(items)} 줄 목소리 맞춤, {len(kept_ids)}줄 원음 유지"
    summary = {"converted": len(done_ids), "total": len(items), "kept": len(kept_ids), "convertedIds": done_ids,
               "keptIds": kept_ids, "message": message, "usd": round(sum(charged), 4), "requests": len(charged),
               "priceKnown": bool((voice_job.get("estimate") or {}).get("priceKnown")), "speakers": speakers,
               "padSeconds": PAD_S, "fadeSeconds": FADE_S, "at": now_iso()}
    if abort:
        summary["stopped"] = abort
    log.info("vnew.sound.voice_track pid=%s gen=%s variant=%s converted=%d kept=%d requests=%d", pid, gen_id, variant,
             len(done_ids), len(kept_ids), len(charged))
    return out_path, summary
