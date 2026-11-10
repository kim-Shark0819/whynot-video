"""vnew ⑥ 사운드 — 대사마다 "실제 말 / 속마음"을 정하고 원래 소리에만 반영한다(작업 122, 2026-11-04).

소리를 새로 만들지 않는다(TTS·유료 호출 없음). 원본 영상의 원래 소리에서 속마음(thought) 줄 구간에만 효과(THOUGHT_FILTER,
속으로 울리는 목소리)를 걸고, 그 밖 구간은 원음 그대로 둔다. 영상 스트림은 복사만 한다(-map 0:v:0 -c:v copy) — 영상은 바뀌지 않는다.

원본 영상(_source): 그 그림체의 가장 최근 결과 1개. ⑦ 내보내기(edit-export.json 의 done 중 n 이 가장 큰 것)가 있으면 그것,
없으면 ⑤ 완성본(gen.finalWebtoon / finalReal 의 key). 원본 파일은 읽기만 한다(덮어쓰지 않는다).
사운드 문서(gen 별·그림체 별, 메인 버킷): {gen 루트}/sound/{variant}/sound.json
  {v, variant, source:{kind, key, n?, duration, hasAudio}, scriptSig,
   lines:[{id, speaker, lineKo, voice, auto, start, end}], versions:[{n, key, at, thoughtIds, sourceKey}], job:{status, error, at, …}}
반영 결과(영상 버킷): {gen 루트}/sound/{variant}/v{n}.mp4 — 새 키에만 쓴다.
처음 만들기(GET): 문서가 없거나 원본·대본 대사가 바뀌었으면 만든다. voice = 대본 entries[].voice(없으면 spoken), auto true,
start·end 는 estimate_spans 한 곳에서 정한다(컷 구간 → 그 안 말소리(silencedetect) → 고르게 → 전체 나눔).
사용자가 고친 줄(auto false)의 voice 는 다시 만들 때도 남기고, 원본이 같으면 시간도 남긴다.
대본(plan)·revision·프리비즈 승인은 읽기만 한다(쓰지 않는다).
외부 호출 래퍼(검사가 바꾼다): _download · _upload · _probe · _detect_speech · _render · _spawn_apply. 저장소는 edit.STORE 다
(edit._read_json · _mutate_json · _presign_video).

작업 202(인물 목소리, voices.py): 반영 작업(job)에 voices(줄마다 목소리 바꾸기 계획)가 있으면 run_apply 가 voices.build_track 으로
원래 소리에서 그 줄 구간만 바꾼 소리 트랙(wav, 길이 그대로)을 만들고, 속마음 효과는 그 트랙 위에 건다. 새 필드는 모두 선택이다
(job.voices·job.progress·job.message, versions[].voices) — 옛 문서·반영 기록은 그대로 읽힌다. GET 보기에는 voices(VNEW_SOUND_VOICES 켬)가 붙는다.

작업 212(대사 고치기, line_redo.py): 원본(_source)은 기준(_base_source — ⑦ 내보내기 → ⑤ 완성본) 위에 대사 다시 만든 판(kind redo, 기준과 시간이
같다)이 있으면 그 판이다. 판끼리 바뀌면 줄 시간을 이어 받고, 컷 구간은 기준 종류로 잰다. versions[].outdated(있을 때만)는 판이 바뀌어 그 반영이
지금 원본과 맞지 않음을 뜻한다(자동으로 다시 반영하지 않는다). GET 보기에는 lineRedo(VNEW_LINE_REDO 켬)가 붙는다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso

from . import edit
from . import generate
from . import plan as plan_mod
from . import prompts
from . import settings as vnew_settings
from .fx import clean_text

log = logging.getLogger("vpoc")

# ================================================================ 상수

VARIANTS = edit.VARIANTS
VOICES = prompts.VOICE_VALUES
# 속마음 효과 사슬(한 곳). 낮은·높은 소리를 깎고 짧은 메아리를 더해 머릿속에서 울리는 목소리로 만든다.
THOUGHT_FILTER = "highpass=f=180,lowpass=f=3800,aecho=0.8:0.6:50|90:0.35|0.2,volume=0.95"
FADE_S = 0.02  # 효과 구간 경계를 섞는 길이(초). 구간 안쪽으로 걸어 구간 밖 소리는 원음 그대로다.
FRAME_SAMPLES = 256  # 효과 세기(volume eval=frame)를 다시 계산하는 소리 조각 크기(48kHz 에서 약 5ms)
# 결과 소리 부호화: FLAC(무손실, s16) — 효과 밖 구간의 디코드 샘플이 원본 디코드와 같다. mp4 안 FLAC 은 Chrome·Edge·Firefox 가 재생한다.
AUDIO_CODEC_ARGS = ("-c:a", "flac", "-sample_fmt", "s16")
MIN_LINE_S = 0.2  # 줄 구간 최소 길이(초)
# 말소리 찾기(ffmpeg silencedetect) 문턱. 이 소리보다 작고 이 길이보다 긴 구간을 쉼으로 본다. 쉼 사이가 말소리다.
SPEECH_NOISE = "-30dB"
SPEECH_MIN_SILENCE_S = 0.25
MIN_SPEECH_S = 0.12  # 이보다 짧은 말소리 조각은 버린다(숨소리·잡음)
MAX_LINES = 300
MAX_SPEAKER_LEN = 40
MAX_LINE_LEN = 300
MAX_ID_LEN = 40
VERSIONS_MAX = 20  # 문서에 남기는 반영 기록 수(파일은 지우지 않는다)
# 반영 작업이 running 인데 이 시간 넘게 하트비트가 없고 이 프로세스에서 돌지 않으면 끊긴 것으로 보고 failed 로 둔다.
# ffmpeg 시간 상한(stitcher.STITCH_TIMEOUT_SECONDS 600초) + 받기·올리기보다 길게 둔다.
APPLY_STALL_S = 900
_TIME_EPS = 0.001
_SPAN_MERGE_GAP_S = 0.05  # 같은 컷 조각이 이만큼 안에서 이어지면 하나로 본다(⑦ 내보내기에서 컷을 나눈 경우)

_STAGE_MESSAGES = {
    "wait": "소리 반영을 시작하지 못했습니다.",
    "download": "원본 영상을 받지 못했습니다.",
    "probe": "원본 영상을 읽지 못했습니다.",
    "voice": "인물 목소리를 맞추지 못했습니다.",
    "render": "소리를 반영하지 못했습니다.",
    "upload": "결과 영상을 저장하지 못했습니다.",
}

_APPLY_SEM = threading.BoundedSemaphore(1)  # 프로세스당 반영 하나(ffmpeg 하나)
_threads: dict[str, threading.Thread] = {}
_threads_guard = threading.Lock()


class _ClaimLost(Exception):
    """다른 실행이 반영을 가져갔거나 끊김으로 정리됐다(이 실행은 결과를 쓰지 않는다)."""


# ================================================================ 키 · 설정


def doc_key(owner: str, pid: str, gen_id: str, variant: str) -> str:
    return f"{edit._root(owner, pid, gen_id)}/sound/{variant}/sound.json"  # noqa: SLF001


def version_key(owner: str, pid: str, gen_id: str, variant: str, n: int) -> str:
    return f"{edit._root(owner, pid, gen_id)}/sound/{variant}/v{int(n)}.mp4"  # noqa: SLF001


def require_enabled(where: str = "vnew.sound") -> None:
    """VNEW_SOUND 이 꺼져 있으면 404 SoundDisabled(라우트가 없는 것과 같다). 프로젝트를 보기 전에 부른다."""
    if not vnew_settings.VNEW_SOUND:
        raise VpocError(where, "SoundDisabled", "사운드 기능이 꺼져 있습니다.", 404)


def _variant_of(raw, gen: dict, where: str) -> str:
    if raw in (None, ""):
        selected = gen.get("selected")
        return selected if selected in VARIANTS else "webtoon"
    if raw not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    return str(raw)


# ================================================================ 외부 호출 래퍼(검사가 바꾼다)


def _download(key: str, path: str) -> int:
    return edit._download(key, path, bucket=vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001


def _upload(path: str, key: str) -> None:
    edit._upload(path, key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")  # noqa: SLF001


def _probe(path: str) -> dict:
    return stitcher.probe_media(path)


def _detect_speech(path: str, duration: float) -> list[tuple[float, float]]:
    """원본 소리의 말소리 구간(ffmpeg silencedetect 한 번). 실패하면 StitchError."""
    binary = stitcher.ffmpeg_path()
    command = [binary, "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
               "-af", f"silencedetect=noise={SPEECH_NOISE}:d={SPEECH_MIN_SILENCE_S}", "-f", "null", "-"]
    result = stitcher._edit_run(command)  # noqa: SLF001
    if result.returncode != 0:
        raise stitcher.StitchError("말소리 구간을 찾지 못했습니다.")
    return speech_from_log(result.stderr.decode("utf-8", errors="replace"), duration)


def _render(source_path: str, output_path: str, spans: list[list[float]], audio_path: str | None = None) -> None:
    """ffmpeg 한 번: 영상은 복사(-map 0:v:0 -c:v copy), 소리는 spans 안에만 THOUGHT_FILTER(경계 FADE_S).

    작업 202: audio_path(목소리를 바꾼 소리 트랙 wav)가 있으면 원본 소리 대신 그 트랙을 쓴다(효과는 그 트랙 위에)."""
    binary = stitcher.ffmpeg_path()
    inputs = ["-i", source_path] + (["-i", audio_path] if audio_path else [])
    graph = thought_graph(spans, "1:a:0" if audio_path else "0:a:0")
    command = [binary, "-y", "-hide_banner", "-loglevel", "error", *inputs,
               "-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
               *AUDIO_CODEC_ARGS, "-movflags", "+faststart", output_path]
    result = stitcher._edit_run(command)  # noqa: SLF001
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise stitcher.StitchError(f"소리를 반영하지 못했습니다: {detail}")


def _spawn_apply(owner: str, pid: str, gen_id: str, variant: str, claim: str) -> None:
    thread = threading.Thread(target=run_apply, args=(owner, pid, gen_id, variant, claim), daemon=True,
                              name=f"vnew-sound-{pid}-{variant}")
    with _threads_guard:
        for done in [k for k, t in _threads.items() if not t.is_alive()]:
            _threads.pop(done, None)
        _threads[claim] = thread
    thread.start()


def _alive_here(claim: str) -> bool:
    with _threads_guard:
        thread = _threads.get(str(claim or ""))
        return bool(thread is not None and thread.is_alive())


# ================================================================ 효과 그래프 · 말소리 읽기(순수 함수)


def merge_spans(spans) -> list[list[float]]:
    """[start, end] 목록을 정렬하고 겹치거나 맞닿은 것을 합친다(효과 세기가 1 을 넘지 않게)."""
    clean = sorted((round(float(s), 3), round(float(e), 3)) for s, e in spans if float(e) - float(s) > 0)
    out: list[list[float]] = []
    for start, end in clean:
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def _gain_expr(spans: list[list[float]]) -> str:
    """조각(볼륨 계산 단위)의 효과 세기(0~1). 구간마다 안쪽 FADE_S 로 오르내리는 사다리꼴의 합(구간은 겹치지 않는다).

    조각 시작(t)과 끝(t + FRAME_SAMPLES/sample_rate — asetnsamples 가 조각 크기를 고정한다)을 함께 본다. 구간 밖에 한 샘플이라도
    걸친 조각은 세기 0 이라 구간 밖 소리는 원음 샘플 그대로다(nb_samples 변수는 이 자리에서 값을 주지 않아 쓰지 않는다 — 실측).
    """
    frame_end = f"(t+{FRAME_SAMPLES}/sample_rate)"
    terms = [f"clip(min((t-{s:.3f})/{FADE_S},({e:.3f}-{frame_end})/{FADE_S}),0,1)" for s, e in spans]
    return "+".join(terms) if terms else "0"


def thought_graph(spans, source: str = "0:a:0") -> str:
    """소리 필터 그래프. 원음(dry)×(1−세기) + 효과음(wet)×세기. 세기 0 인 곳은 원음 샘플 그대로다. 출력 [aout].
    source: 입력 소리(기본 원본 영상의 소리, 작업 202 는 목소리를 바꾼 트랙 1:a:0)."""
    merged = merge_spans(spans or [])
    if not merged:
        return f"[{source}]anull[aout]"
    gain = _gain_expr(merged)
    return (f"[{source}]asetnsamples=n={FRAME_SAMPLES}:p=0,asplit=2[sdry][swet];"
            f"[swet]{THOUGHT_FILTER},volume='{gain}':eval=frame[wet];"
            f"[sdry]volume='1-({gain})':eval=frame[dry];"
            "[dry][wet]amix=inputs=2:normalize=0:duration=first[aout]")


def speech_from_log(text: str, duration: float) -> list[tuple[float, float]]:
    """silencedetect 로그(silence_start·silence_end) → 말소리 구간 = 쉼 사이. MIN_SPEECH_S 보다 짧은 조각은 버린다."""
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for match in re.finditer(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)", text or ""):
        value = max(0.0, float(match.group(2)))
        if match.group(1) == "start":
            start = value
        else:
            silences.append((start if start is not None else 0.0, value))
            start = None
    if start is not None:
        silences.append((start, duration))
    speech: list[tuple[float, float]] = []
    clock = 0.0
    for lo, hi in silences:
        if lo - clock >= MIN_SPEECH_S:
            speech.append((round(clock, 3), round(min(lo, duration), 3)))
        clock = max(clock, hi)
    if duration - clock >= MIN_SPEECH_S:
        speech.append((round(clock, 3), round(duration, 3)))
    return speech


# ================================================================ 구간 추정(한 곳)


def _fit(start: float, end: float, duration: float) -> tuple[float, float]:
    """원본 길이 안으로 넣고 최소 길이(MIN_LINE_S)를 맞춘다(모자라면 끝을 늘이고, 끝에 닿으면 시작을 당긴다)."""
    start = max(0.0, min(float(start), duration))
    end = min(duration, max(float(end), start))
    if end - start < MIN_LINE_S:
        end = min(duration, start + MIN_LINE_S)
        start = max(0.0, end - MIN_LINE_S)
    return round(start, 3), round(end, 3)


def estimate_spans(lines: list[dict], cut_spans: dict | None, speech: list, duration: float) -> list[tuple[float, float]]:
    """대사 줄마다 원본 영상 안 (start, end)(초). 줄 순서 그대로 돌려준다.

    lines: [{cutId}] 대본 순서. cut_spans: {cutId: (start, end)} 원본 안 컷 구간(모르면 None). speech: 원본 전체 말소리 구간.
    1. 컷 구간을 알면 그 컷의 줄들을 그 구간 안에 둔다.
    2. 그 구간 안 말소리 조각(MIN_SPEECH_S 이상)을 줄 순서대로 나눠 준다. 조각이 줄보다 많으면 앞 줄부터 하나씩 더 묶고,
       적으면 첫 조각 시작 ~ 끝 조각 끝을 고르게 나눈다.
    3. 말소리를 못 찾으면 컷 구간을 그 컷 줄 수로 고르게 나눈다.
    4. 컷 구간을 모르면(전체, 또는 그 줄의 컷만) 전체 길이를 전체 줄 수로 나눈 그 줄 자리를 쓴다.
    """
    count = len(lines)
    if count == 0 or duration <= 0:
        return []
    spans = [(duration * i / count, duration * (i + 1) / count) for i in range(count)]
    groups: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        cut = str((line or {}).get("cutId") or "")
        if cut_spans and cut in cut_spans:
            groups.setdefault(cut, []).append(index)
    for cut, indexes in groups.items():
        lo, hi = (float(v) for v in cut_spans[cut])
        found = [(max(float(s), lo), min(float(e), hi)) for s, e in speech or []
                 if min(float(e), hi) - max(float(s), lo) >= MIN_SPEECH_S]
        k = len(indexes)
        if found and len(found) >= k:
            base, extra = divmod(len(found), k)
            at = 0
            for j, index in enumerate(indexes):
                size = base + (1 if j < extra else 0)
                group = found[at:at + size]
                at += size
                spans[index] = (group[0][0], group[-1][1])
        elif found:
            a, b = found[0][0], found[-1][1]
            for j, index in enumerate(indexes):
                spans[index] = (a + (b - a) * j / k, a + (b - a) * (j + 1) / k)
        else:
            for j, index in enumerate(indexes):
                spans[index] = (lo + (hi - lo) * j / k, lo + (hi - lo) * (j + 1) / k)
    return [_fit(s, e, duration) for s, e in spans]


# ================================================================ 원본 · 대본 · 컷 구간


def _source(owner: str, pid: str, gen_id: str, gen: dict, variant: str) -> dict:
    """⑥ 원본. 기준(_base_source) 위에 작업 212 대사 다시 만들기의 지금 판이 있으면 그 판(kind redo — 기준과 시간이 같다)."""
    base = _base_source(owner, pid, gen_id, gen, variant)
    from . import line_redo  # noqa: PLC0415 — line_redo 가 이 모듈을 import 한다(순환 피함)

    if not line_redo.enabled():
        return base
    try:
        return line_redo.current_source(owner, pid, gen_id, variant, base)
    except Exception as exc:  # noqa: BLE001 — 판 기록을 못 읽으면 기준 그대로(⑥ 은 계속 쓴다)
        log.warning("vnew.sound.redo_source_failed pid=%s variant=%s err=%s", pid, variant, type(exc).__name__)
        return base


def _base_source(owner: str, pid: str, gen_id: str, gen: dict, variant: str) -> dict:
    """그 그림체의 가장 최근 결과. ⑦ 내보내기 done(n 최대) → ⑤ 완성본 순서. 없으면 409 NoSoundSource."""
    prefix = f"users/{owner}/video/{pid}/"
    state, _ = edit._read_json(edit.export_state_key(owner, pid, gen_id))  # noqa: SLF001
    state = state or {}
    items = [h for h in (state.get("history") or []) if isinstance(h, dict)]
    if isinstance(state.get("current"), dict):
        items.append(state["current"])
    best = None
    for item in items:
        n = item.get("n")
        key = str(item.get("key") or "")
        if (item.get("status") != "done" or item.get("variant") != variant or not key.startswith(prefix)
                or not isinstance(n, int) or isinstance(n, bool)):
            continue
        if best is None or n > best["n"]:
            best = item
    if best is not None:
        clips = [c for c in best.get("clips") or [] if isinstance(c, dict)]
        return {"kind": "export", "key": str(best["key"]), "n": int(best["n"]), "clips": clips}
    final = gen.get("finalReal" if variant == "real" else "finalWebtoon")
    final = final if isinstance(final, dict) else {}
    key = str(final.get("key") or "")
    if final.get("status") == "done" and key.startswith(prefix):
        return {"kind": "final", "key": key}
    raise VpocError("vnew.sound", "NoSoundSource",
                    f"소리를 바꿀 {edit.VARIANT_LABELS[variant]} 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들어 주세요.", 409)


def _script_lines(plan: dict) -> list[dict]:
    """대본 대사 줄(빈 대사 빼고, id 가 겹치면 앞 줄만). 글은 정리해서 담는다. voice 는 대본 그대로(없으면 spoken)."""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in ((plan or {}).get("script") or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        line = clean_text(entry.get("lineKo"), MAX_LINE_LEN)
        line_id = clean_text(str(entry.get("id") or ""), MAX_ID_LEN)
        if generate._blank_line(line) or not line_id or line_id in seen:  # noqa: SLF001
            continue
        seen.add(line_id)
        panels = {int(p) for p in entry.get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool)}
        out.append({"id": line_id, "speaker": clean_text(entry.get("speaker"), MAX_SPEAKER_LEN), "lineKo": line,
                    "voice": "thought" if prompts.is_thought(entry) else "spoken", "panels": panels})
        if len(out) >= MAX_LINES:
            log.warning("vnew.sound.lines_capped max=%d", MAX_LINES)
            break
    return out


def _script_sig(lines: list[dict]) -> str:
    body = [[l["id"], l["speaker"], l["lineKo"], l["voice"], sorted(l["panels"])] for l in lines]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]


def _cut_weights(plan: dict) -> dict[str, float]:
    cuts = ((plan or {}).get("conti") or {}).get("cuts") or []
    return {str(c.get("id")): edit._num(c.get("seconds")) or 0.0 for c in cuts if isinstance(c, dict)}  # noqa: SLF001


def _split_by_cuts(start: float, end: float, cut_ids: list[str], weights: dict[str, float]) -> list[tuple[float, float, str]]:
    """[start, end] 을 컷 순서대로 콘티 컷 길이 비율로 나눈다(길이를 모르면 고르게)."""
    sizes = [weights.get(c, 0.0) for c in cut_ids]
    total = sum(sizes)
    if total <= 0:
        sizes, total = [1.0] * len(cut_ids), float(len(cut_ids))
    out, clock = [], start
    for cut_id, size in zip(cut_ids, sizes):
        step = (end - start) * size / total
        out.append((clock, clock + step, cut_id))
        clock += step
    return out


def _final_cut_spans(plan: dict, gen: dict, variant: str, duration: float) -> list[tuple[float, float, str]] | None:
    """⑤ 완성본 안 컷 구간. 합칠 때와 같은 순서(완료된 칸만)와 길이(칸 seconds, 채운 컷은 origSeconds)를 이어 결과 길이에 맞춘다."""
    mode, cells = edit._cells(gen)  # noqa: SLF001
    weights = _cut_weights(plan)
    pieces: list[tuple[float, list[str]]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        state = cell.get(variant) if isinstance(cell.get(variant), dict) else {}
        if state.get("status") != "completed" or not state.get("outputKey"):
            continue
        length = edit._num(cell.get("seconds")) or 0.0  # noqa: SLF001
        if mode == "previz" and state.get("padded"):
            orig = edit._num(state.get("origSeconds"))  # noqa: SLF001
            length = orig if orig and orig > 0 else length
        ids = [str(c) for c in cell.get("cutIds") or []] or ([str(cell["cutId"])] if cell.get("cutId") else [])
        if length <= 0 or not ids:
            return None
        pieces.append((length, ids))
    total = sum(p[0] for p in pieces)
    if not pieces or total <= 0 or duration <= 0:
        return None
    scale = duration / total
    out: list[tuple[float, float, str]] = []
    clock = 0.0
    for length, ids in pieces:
        out.extend(_split_by_cuts(clock * scale, (clock + length) * scale, ids, weights))
        clock += length
    return out


def _export_cut_spans(owner: str, pid: str, gen_id: str, plan: dict, gen: dict, variant: str,
                      clips: list[dict]) -> list[tuple[float, float, str]] | None:
    """⑦ 내보내기 안 컷 구간. 내보내기 사본 clips(sourceKey·in·out·속도)를 타임라인 순서로 놓고, 재료 cutIds 를 재료 길이에
    콘티 컷 비율로 펼친 뒤 클립 구간과 겹치는 부분만 옮긴다. 하나라도 못 읽으면 None."""
    if not clips:
        return None
    items = edit._all_items(gen, variant, edit._retake_state(owner, pid, gen_id))  # noqa: SLF001
    by_key = {m["sourceKey"]: m for m in items}
    keys = list(dict.fromkeys(str(c.get("sourceKey") or "") for c in clips if c.get("sourceKey") in by_key))
    meta, _ = edit._ensure_meta(owner, pid, gen_id, keys)  # noqa: SLF001
    weights = _cut_weights(plan)
    out: list[tuple[float, float, str]] = []
    clock = 0.0
    for clip in clips:
        item = by_key.get(clip.get("sourceKey"))
        length = edit._num((meta.get(clip.get("sourceKey")) or {}).get("duration")) or 0.0  # noqa: SLF001
        seconds = edit._clip_seconds(clip)  # noqa: SLF001
        if not item or length <= 0 or seconds <= 0:
            return None
        if item["kind"] == "final":
            spans = _final_cut_spans(plan, gen, variant, length)
        else:
            ids = [str(c) for c in item.get("cutIds") or []]
            spans = _split_by_cuts(0.0, length, ids, weights) if ids else None
        if not spans:
            return None
        lo_clip, hi_clip = float(clip.get("in") or 0.0), float(clip.get("out") or 0.0)
        speed = edit._speed_of(clip)  # noqa: SLF001
        for start, end, cut_id in spans:
            lo, hi = max(start, lo_clip), min(end, hi_clip)
            if hi - lo > _TIME_EPS:
                out.append((clock + (lo - lo_clip) / speed, clock + (hi - lo_clip) / speed, cut_id))
        clock += seconds
    return out or None


def _per_cut(spans: list[tuple[float, float, str]] | None, duration: float) -> dict[str, tuple[float, float]] | None:
    """컷마다 구간 하나: 같은 컷 조각이 이어지면 합치고, 떨어진 조각이 여럿이면 가장 긴 것. 원본 길이 안으로 자른다."""
    if not spans:
        return None
    merged: list[list] = []
    for start, end, cut_id in sorted(spans, key=lambda s: s[0]):
        if merged and merged[-1][2] == cut_id and start - merged[-1][1] <= _SPAN_MERGE_GAP_S:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, cut_id])
    out: dict[str, tuple[float, float]] = {}
    for start, end, cut_id in merged:
        start, end = max(0.0, start), min(duration, end)
        if end - start <= _TIME_EPS:
            continue
        if cut_id not in out or end - start > out[cut_id][1] - out[cut_id][0]:
            out[cut_id] = (round(start, 3), round(end, 3))
    return out or None


def _line_cuts(plan: dict, lines: list[dict], cut_spans: dict | None) -> list[str]:
    """줄마다 컷 id: 대본 panels 와 겹치는 콘티 컷 중 원본에서 가장 먼저 나오는 컷. 못 찾으면 ""."""
    if not cut_spans:
        return ["" for _ in lines]
    cut_panels: dict[str, set[int]] = {}
    for cut in ((plan or {}).get("conti") or {}).get("cuts") or []:
        if isinstance(cut, dict):
            cut_panels[str(cut.get("id"))] = {int(p) for p in cut.get("panels") or []
                                              if isinstance(p, (int, float)) and not isinstance(p, bool)}
    out = []
    for line in lines:
        found = [c for c in cut_spans if line["panels"] and (cut_panels.get(c) or set()) & line["panels"]]
        out.append(min(found, key=lambda c: cut_spans[c][0]) if found else "")
    return out


# ================================================================ 문서 만들기


def _idle_job() -> dict:
    return {"status": "none", "error": "", "at": ""}


def _fresh(doc: dict | None, source: dict, sig: str) -> bool:
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list) or not isinstance(doc.get("source"), dict):
        return False
    src = doc["source"]
    return src.get("kind") == source["kind"] and src.get("key") == source["key"] and doc.get("scriptSig") == sig


def _measure(source: dict) -> tuple[float, bool, list]:
    """원본을 임시 파일로 받아 길이·소리 유무·말소리 구간을 잰다(프로세스당 한 번에 하나, edit._MEDIA_SEM)."""
    with edit._MEDIA_SEM:  # noqa: SLF001
        with tempfile.TemporaryDirectory(prefix="vnew-sound-probe-") as workdir:
            path = os.path.join(workdir, "source.mp4")
            try:
                _download(source["key"], path)
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.sound.probe_failed stage=download err=%s", type(exc).__name__)
                raise VpocError("vnew.sound", "SourceUnreadable", "원본 영상을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409) from None
            info = _probe(path) or {}
            duration = edit._num(info.get("duration")) or 0.0  # noqa: SLF001
            if duration <= 0:
                raise VpocError("vnew.sound", "SourceUnreadable", "원본 영상 길이를 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409)
            has_audio = bool(info.get("hasAudio"))
            speech: list = []
            if has_audio:
                try:
                    speech = _detect_speech(path, duration)
                except Exception as exc:  # noqa: BLE001 — 말소리를 못 찾으면 고르게 나눈다.
                    log.warning("vnew.sound.speech_failed err=%s", error_gist(f"{type(exc).__name__}: {exc}", 200))
    return round(duration, 3), has_audio, speech


def _make_doc(owner: str, pid: str, gen_id: str, plan: dict, gen: dict, variant: str, source: dict,
              lines_src: list[dict], sig: str, old: dict | None) -> dict:
    export_spans = None
    # 작업 212: 대사 다시 만든 판(kind redo)은 기준과 길이·컷 자리가 같다 — 컷 구간은 기준 종류로 잰다.
    span_kind = source.get("baseKind") if source["kind"] == "redo" else source["kind"]
    if span_kind == "export":  # 재료 길이 재기(_ensure_meta)는 _MEDIA_SEM 을 따로 잡는다 — 먼저 한다.
        try:
            export_spans = _export_cut_spans(owner, pid, gen_id, plan, gen, variant, source.get("clips") or [])
        except Exception as exc:  # noqa: BLE001 — 컷 구간을 모르면 전체를 나눈다.
            log.warning("vnew.sound.export_spans_failed pid=%s err=%s", pid, type(exc).__name__)
    duration, has_audio, speech = _measure(source)
    spans = export_spans if span_kind == "export" else _final_cut_spans(plan, gen, variant, duration)
    cut_spans = _per_cut(spans, duration)
    cuts = _line_cuts(plan, lines_src, cut_spans)
    times = estimate_spans([{"cutId": c} for c in cuts], cut_spans, speech, duration)
    old_lines = {str(l.get("id")): l for l in (old or {}).get("lines") or [] if isinstance(l, dict)}
    old_key = (old or {}).get("source", {}).get("key") if isinstance((old or {}).get("source"), dict) else None
    same_source = old_key == source["key"]
    # 작업 212: 같은 기준의 판끼리(원래 영상 ↔ 대사 다시 만든 판)는 시간이 같다 — 줄 시간을 모두 이어 받는다.
    same_chain = bool(old_key) and not same_source and old_key in (source.get("chain") or [])
    lines = []
    for line, (start, end) in zip(lines_src, times):
        item = {"id": line["id"], "speaker": line["speaker"], "lineKo": line["lineKo"], "voice": line["voice"],
                "auto": True, "start": start, "end": end}
        prev = old_lines.get(line["id"])
        if prev and same_chain:
            p_start, p_end = edit._num(prev.get("start")), edit._num(prev.get("end"))  # noqa: SLF001
            if p_start is not None and p_end is not None and 0 <= p_start < p_end <= duration + _TIME_EPS:
                item["start"], item["end"] = p_start, min(p_end, duration)
        if prev and prev.get("auto") is False:  # 사용자가 고친 줄: voice 는 남기고, 원본이 같으면 시간도 남긴다.
            if prev.get("voice") in VOICES:
                item["voice"] = prev["voice"]
            p_start, p_end = edit._num(prev.get("start")), edit._num(prev.get("end"))  # noqa: SLF001
            if (same_source or same_chain) and p_start is not None and p_end is not None and 0 <= p_start < p_end <= duration + _TIME_EPS:
                item["start"], item["end"] = p_start, min(p_end, duration)
            item["auto"] = False
        lines.append(item)
    src = {"kind": source["kind"], "key": source["key"], "duration": duration, "hasAudio": has_audio}
    if source.get("n") is not None and span_kind == "export":
        src["n"] = source["n"]
    if source["kind"] == "redo":
        src.update(r=source["r"], baseKind=source.get("baseKind"), baseKey=source.get("baseKey"))
    log.info("vnew.sound.built pid=%s gen=%s variant=%s kind=%s lines=%d cuts=%d speech=%d audio=%s seconds=%.3f",
             pid, gen_id, variant, source["kind"], len(lines), len(cut_spans or {}), len(speech), has_audio, duration)
    return {"v": 1, "variant": variant, "source": src, "scriptSig": sig, "lines": lines, "createdAt": now_iso(),
            "updatedAt": now_iso()}


def _ensure_doc(owner: str, pid: str, gen_id: str, plan: dict, gen: dict, variant: str) -> tuple[dict, bool]:
    """사운드 문서(없거나 원본·대본 대사가 바뀌었으면 새로 만든다). 반환 (문서, 새로 만들었는가). 반영 기록·작업은 이어 받는다."""
    source = _source(owner, pid, gen_id, gen, variant)
    lines_src = _script_lines(plan)
    sig = _script_sig(lines_src)
    key = doc_key(owner, pid, gen_id, variant)
    doc, _ = edit._read_json(key)  # noqa: SLF001
    if _fresh(doc, source, sig):
        return doc, False
    with edit._lock(f"sound:{owner}/{pid}/{gen_id}/{variant}"):  # noqa: SLF001
        doc, _ = edit._read_json(key)  # noqa: SLF001
        if _fresh(doc, source, sig):
            return doc, False
        built = _make_doc(owner, pid, gen_id, plan, gen, variant, source, lines_src, sig, doc)

        def mutate(current):
            base = current if isinstance(current, dict) else {}
            built["versions"] = [v for v in base.get("versions") or [] if isinstance(v, dict)]
            built["job"] = base.get("job") if isinstance(base.get("job"), dict) else _idle_job()
            if base.get("createdAt"):
                built["createdAt"] = base["createdAt"]
            return built

        return edit._mutate_json(key, mutate), True  # noqa: SLF001


# ================================================================ 보기


def _job_out(job) -> dict:
    job = job if isinstance(job, dict) else {}
    out = {"status": str(job.get("status") or "none"), "error": str(job.get("error") or ""), "at": str(job.get("at") or "")}
    if isinstance(job.get("message"), str) and job["message"]:  # 작업 202: 목소리 맞춤 결과 한 줄(있을 때만)
        out["message"] = job["message"]
    if isinstance(job.get("progress"), dict):  # 작업 202: 목소리 맞춤 진행(줄 수)
        out["progress"] = {"done": int(job["progress"].get("done") or 0), "total": int(job["progress"].get("total") or 0)}
    return out


def _view(doc: dict, gen_id: str, variant: str, rebuilt: bool) -> dict:
    src = doc.get("source") or {}
    label = f"⑦ 내보내기 {src.get('n')}" if src.get("kind") == "export" or src.get("baseKind") == "export" else "⑤ 완성본"
    if src.get("kind") == "redo":  # 작업 212: 대사 다시 만든 판
        label += f" · 대사 고침 {src.get('r')}"
    versions = []
    for item in doc.get("versions") or []:
        if not isinstance(item, dict):
            continue
        version = {"n": item.get("n"), "at": str(item.get("at") or ""), "thoughtIds": list(item.get("thoughtIds") or []),
                   "currentSource": item.get("sourceKey") == src.get("key"),
                   "videoUrl": edit._presign_video(str(item.get("key") or ""))}  # noqa: SLF001
        if isinstance(item.get("voices"), dict):  # 작업 202: 목소리 맞춤 요약(있을 때만 — 옛 기록은 그대로)
            version["voices"] = copy.deepcopy(item["voices"])
        if item.get("outdated"):  # 작업 212: 대사 다시 만들기로 원본이 바뀌었다(있을 때만)
            version["outdated"] = True
            version["outdatedText"] = "목소리 맞춤을 다시 반영해 주세요"
        versions.append(version)
    return {
        "enabled": True,
        "genId": gen_id,
        "variant": variant,
        "source": {"kind": src.get("kind"), "n": src.get("n"), "label": label, "duration": src.get("duration"),
                   "hasAudio": bool(src.get("hasAudio")), "videoUrl": edit._presign_video(str(src.get("key") or ""))},  # noqa: SLF001
        "lines": [{k: line.get(k) for k in ("id", "speaker", "lineKo", "voice", "auto", "start", "end")}
                  for line in doc.get("lines") or [] if isinstance(line, dict)],
        "versions": versions,
        "job": _job_out(doc.get("job")),
        "rebuilt": bool(rebuilt),
        "limits": {"minLineSeconds": MIN_LINE_S, "fadeSeconds": FADE_S},
    }


def sound_view(owner: str, pid: str, variant=None) -> dict:
    """GET /p/<pid>/sound. 문서(없으면 만든다)·원본·반영 기록·작업 상태. 작업 202: VNEW_SOUND_VOICES 켬이면 voices(인물 목소리)."""
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = _variant_of(variant, gen, "vnew.sound")
    reconcile_job(owner, pid, gen_id, variant)
    doc, rebuilt = _ensure_doc(owner, pid, gen_id, plan, gen, variant)
    view = _view(doc, gen_id, variant, rebuilt)
    from . import voices  # noqa: PLC0415 — voices 가 이 모듈을 import 한다(순환 피함)

    if voices.enabled():
        view["voices"] = voices.view(owner, pid, plan, gen_id, variant, doc)
    from . import line_redo  # noqa: PLC0415 — 작업 212: 대사 고치기 · 그 구간 영상 다시 만들기(켬일 때만 lineRedo)

    if line_redo.enabled():
        try:
            view["lineRedo"] = line_redo.view(owner, pid, plan, gen_id, variant, doc)
        except Exception as exc:  # noqa: BLE001 — 사운드 보기는 계속 보인다
            log.warning("vnew.sound.line_redo_view_failed pid=%s err=%s", pid, error_gist(f"{type(exc).__name__}: {exc}", 200))
            view["lineRedo"] = {"enabled": True, "error": "대사 고치기 정보를 읽지 못했습니다. 다시 불러와 주세요.", "lines": {},
                                "revisions": [], "current": 0, "busy": False, "cast": []}
    return view


# ================================================================ 저장(PUT)


def _current_doc(owner: str, pid: str, gen_id: str, gen: dict, variant: str, where: str) -> tuple[dict, dict]:
    """저장된 문서와 지금 원본. 문서가 없으면 409 SoundNotReady, 원본이 바뀌었으면 409 SoundChanged."""
    doc, _ = edit._read_json(doc_key(owner, pid, gen_id, variant))  # noqa: SLF001
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list) or not isinstance(doc.get("source"), dict):
        raise VpocError(where, "SoundNotReady", "사운드 화면을 먼저 불러와 주세요.", 409)
    source = _source(owner, pid, gen_id, gen, variant)
    if doc["source"].get("key") != source["key"]:
        raise VpocError(where, "SoundChanged", "원본 영상이 바뀌었습니다. 다시 불러온 뒤 고쳐 주세요.", 409)
    return doc, source


def _apply_patch(doc: dict, raw: list, where: str) -> int:
    """사용자가 바꾼 voice·start·end 만 반영한다(그 밖 필드는 무시). 값이 바뀐 줄은 auto false. 반환: 바뀐 줄 수. 틀리면 400."""
    duration = edit._num((doc.get("source") or {}).get("duration")) or 0.0  # noqa: SLF001
    by_id = {str(l.get("id")): l for l in doc["lines"] if isinstance(l, dict)}
    changed = 0
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in by_id:
            raise VpocError(where, "BadLine", f"{number}번째 줄을 찾을 수 없습니다. 다시 불러온 뒤 고쳐 주세요.", 400)
        line = by_id[item["id"]]
        voice = item.get("voice", line.get("voice"))
        if voice not in VOICES:
            raise VpocError(where, "BadVoice", f"{number}번째 줄의 목소리(voice)는 spoken 또는 thought 여야 합니다.", 400)
        start = edit._num(item["start"]) if "start" in item else edit._num(line.get("start"))  # noqa: SLF001
        end = edit._num(item["end"]) if "end" in item else edit._num(line.get("end"))  # noqa: SLF001
        if start is None or end is None:
            raise VpocError(where, "BadLineTime", f"{number}번째 줄의 시작·끝 시간이 올바르지 않습니다.", 400)
        start, end = round(start, 3), round(end, 3)
        if start < 0 or end > duration + _TIME_EPS or end - start < MIN_LINE_S - 1e-9:
            raise VpocError(where, "BadLineRange",
                            f"{number}번째 줄 구간은 원본 길이({duration:.2f}초) 안에서 {MIN_LINE_S}초 이상이어야 합니다.", 400)
        end = min(end, round(duration, 3))
        if (voice, start, end) != (line.get("voice"), line.get("start"), line.get("end")):
            line.update(voice=voice, start=start, end=end, auto=False)
            changed += 1
    return changed


def save_lines(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/sound. 바디 {variant, lines:[{id, voice?, start?, end?}]}. 대본(plan)은 건드리지 않는다. 반환: 보기."""
    where = "vnew.sound.save"
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("lines")
    if not isinstance(raw, list) or len(raw) > MAX_LINES:
        raise VpocError(where, "BadLines", f"고친 줄 목록(lines)은 {MAX_LINES}개까지의 목록이어야 합니다.", 400)
    _plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = _variant_of(payload.get("variant"), gen, where)
    _current_doc(owner, pid, gen_id, gen, variant, where)
    box = {"changed": 0}

    def mutate(current):
        if not isinstance(current, dict) or not isinstance(current.get("lines"), list):
            raise VpocError(where, "SoundNotReady", "사운드 화면을 먼저 불러와 주세요.", 409)
        box["changed"] = _apply_patch(current, raw, where)
        if not box["changed"]:
            return None
        current["updatedAt"] = now_iso()
        return current

    doc = edit._mutate_json(doc_key(owner, pid, gen_id, variant), mutate)  # noqa: SLF001
    log.info("vnew.sound.saved pid=%s gen=%s variant=%s changed=%d", pid, gen_id, variant, box["changed"])
    return _view(doc, gen_id, variant, False)


# ================================================================ 반영(apply)


def start_apply(owner: str, pid: str, payload, *, voice_job: dict | None = None) -> dict:
    """POST /p/<pid>/sound/apply. 저장된 문서의 thought 줄 구간으로 반영을 시작한다(백그라운드). 202 {started, job}.

    running 이면 409 SoundRunning, 원본에 소리가 없으면 400("이 영상에는 소리가 없어 바꿀 수 없습니다", kind BadAudio). 시작 때 구간·원본 키를 job 에 사본으로 남긴다.
    작업 202: voice_job(voices.start_voice_apply 가 검사·견적을 마친 줄별 계획)이 있으면 job.voices 로 남겨 run_apply 가 목소리를 먼저 바꾼다.
    """
    where = "vnew.sound.apply"
    payload = payload if isinstance(payload, dict) else {}
    _plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = _variant_of(payload.get("variant"), gen, where)
    reconcile_job(owner, pid, gen_id, variant)
    doc, source = _current_doc(owner, pid, gen_id, gen, variant, where)
    if not doc["source"].get("hasAudio"):
        # BadAudio 는 운영에서도 문구를 그대로 보이는 kind 다(vpoc.errors.USER_FACING_KINDS).
        raise VpocError(where, "BadAudio", "이 영상에는 소리가 없어 바꿀 수 없습니다", 400)
    claim = secrets.token_hex(8)

    def mutate(current):
        current = current if isinstance(current, dict) else doc
        job = current.get("job") if isinstance(current.get("job"), dict) else {}
        if job.get("status") == "running":
            raise VpocError(where, "SoundRunning", "소리를 반영하는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        thought = [l for l in current.get("lines") or [] if isinstance(l, dict) and l.get("voice") == "thought"]
        numbers = [v.get("n") for v in current.get("versions") or [] if isinstance(v, dict)] + [job.get("n")]
        n = max([x for x in numbers if isinstance(x, int) and not isinstance(x, bool)] + [0]) + 1
        now = now_iso()
        current["job"] = {"status": "running", "error": "", "at": now, "claim": claim, "beatAt": now, "n": n,
                          "sourceKey": source["key"], "thoughtIds": [str(l.get("id")) for l in thought],
                          "spans": merge_spans([(l.get("start"), l.get("end")) for l in thought])}
        if voice_job:
            current["job"]["voices"] = copy.deepcopy(voice_job)
        return current

    saved = edit._mutate_json(doc_key(owner, pid, gen_id, variant), mutate)  # noqa: SLF001
    job = saved["job"]
    log.info("vnew.sound.apply_start pid=%s gen=%s variant=%s n=%d thought=%d spans=%d voices=%d", pid, gen_id, variant, job["n"],
             len(job["thoughtIds"]), len(job["spans"]), len(((job.get("voices") or {}).get("items")) or []))
    _spawn_apply(owner, pid, gen_id, variant, claim)
    return {"started": True, "job": _job_out(job)}


def _beat(owner: str, pid: str, gen_id: str, variant: str, claim: str, *, finish: dict | None = None,
          progress: dict | None = None) -> dict:
    """claim 이 그대로면 하트비트를 쓰고(finish 가 있으면 끝 상태로 바꾸고) job 사본을 준다. 아니면 _ClaimLost.
    작업 202: progress({done, total})를 주면 하트비트와 함께 남기고, finish.message 는 끝 상태에 한 줄로 남긴다."""
    box: dict = {}

    def mutate(current):
        job = (current or {}).get("job")
        if not isinstance(job, dict) or job.get("claim") != claim or job.get("status") != "running":
            raise _ClaimLost()
        now = now_iso()
        if finish is None:
            job["beatAt"] = now
            if progress is not None:
                job["progress"] = dict(progress)
        else:
            version = finish.get("version")
            if version:
                current["versions"] = ([v for v in current.get("versions") or [] if isinstance(v, dict)] + [version])[-VERSIONS_MAX:]
            current["job"] = {"status": finish["status"], "error": finish.get("error") or "", "at": now, "n": job.get("n")}
            if finish.get("message"):
                current["job"]["message"] = str(finish["message"])
        box["job"] = copy.deepcopy(job)
        return current

    edit._mutate_json(doc_key(owner, pid, gen_id, variant), mutate)  # noqa: SLF001
    return box["job"]


def run_apply(owner: str, pid: str, gen_id: str, variant: str, claim: str) -> None:
    """반영 스레드 본체: 원본 받기 → 소리 확인 → (작업 202: 목소리 맞춤) → ffmpeg 한 번 → 새 키에 올리기 → 기록. 임시 파일은 끝나면 지운다."""
    stage = "wait"
    try:
        with _APPLY_SEM:
            job = _beat(owner, pid, gen_id, variant, claim)
            source_key, n = str(job.get("sourceKey") or ""), int(job.get("n") or 0)
            if not source_key.startswith(f"users/{owner}/video/{pid}/") or n <= 0:
                raise VpocError("vnew.sound.apply", "SourceUnreadable", "원본 영상을 읽지 못했습니다.", 409)
            voice_summary = None
            with tempfile.TemporaryDirectory(prefix="vnew-sound-") as workdir:
                stage = "download"
                source_path = os.path.join(workdir, "source.mp4")
                _download(source_key, source_path)
                stage = "probe"
                if not (_probe(source_path) or {}).get("hasAudio"):
                    raise VpocError("vnew.sound.apply", "BadAudio", "이 영상에는 소리가 없어 바꿀 수 없습니다", 400)
                audio_path = None
                from . import audio_separate; audio_path = audio_separate.sound_track(owner, pid, source_key, workdir)  # noqa: E702,PLC0415 — 작업 217
                voice_job = job.get("voices")
                if isinstance(voice_job, dict) and voice_job.get("items"):
                    stage = "voice"
                    from . import voices  # noqa: PLC0415 — voices 가 이 모듈을 import 한다(순환 피함)

                    audio_path, voice_summary = voices.build_track(
                        owner, pid, gen_id, variant, audio_path or source_path, workdir, voice_job,  # 작업 217: 분리된 목소리 위에
                        beat=lambda progress: _beat(owner, pid, gen_id, variant, claim, progress=progress))
                stage = "render"
                output = os.path.join(workdir, "sound.mp4")
                _render(source_path, output, job.get("spans") or [], audio_path=audio_path)
                seconds = edit._num((_probe(output) or {}).get("duration")) or 0.0  # noqa: SLF001
                if seconds <= 0:
                    raise stitcher.StitchError("반영한 영상 길이를 읽지 못했습니다.")
                _beat(owner, pid, gen_id, variant, claim)  # 올리기 전에 claim 확인
                stage = "upload"
                key = version_key(owner, pid, gen_id, variant, n)
                _upload(output, key)
        version = {"n": n, "key": key, "at": now_iso(), "thoughtIds": list(job.get("thoughtIds") or []), "sourceKey": source_key}
        finish = {"status": "done", "version": version}
        if voice_summary:
            version["voices"] = voice_summary
            finish["message"] = voice_summary.get("message") or ""
        _beat(owner, pid, gen_id, variant, claim, finish=finish)
        log.info("vnew.sound.apply_done pid=%s gen=%s variant=%s n=%d seconds=%.3f", pid, gen_id, variant, n, seconds)
    except _ClaimLost:
        log.info("vnew.sound.apply_claim_lost pid=%s gen=%s variant=%s stage=%s", pid, gen_id, variant, stage)
    except Exception as exc:  # noqa: BLE001
        message = exc.message if isinstance(exc, VpocError) else _STAGE_MESSAGES.get(stage, "소리를 반영하지 못했습니다.")
        log.warning("vnew.sound.apply_failed pid=%s gen=%s variant=%s stage=%s err=%s", pid, gen_id, variant, stage,
                    error_gist(f"{type(exc).__name__}: {exc}", 300))
        try:
            _beat(owner, pid, gen_id, variant, claim, finish={"status": "failed", "error": message})
        except _ClaimLost:
            pass
        except Exception as write_exc:  # noqa: BLE001
            log.warning("vnew.sound.apply_state_failed pid=%s err=%s", pid, type(write_exc).__name__)


def reconcile_job(owner: str, pid: str, gen_id: str, variant: str) -> bool:
    """끊긴 반영 정리(GET·반영 시작 때): running 인데 APPLY_STALL_S 넘게 하트비트가 없고 이 프로세스에서 돌지 않으면 failed."""
    key = doc_key(owner, pid, gen_id, variant)
    doc, _ = edit._read_json(key)  # noqa: SLF001
    job = (doc or {}).get("job")
    if not isinstance(job, dict) or job.get("status") != "running" or _alive_here(job.get("claim")):
        return False
    beat = plan_mod._parse_iso(str(job.get("beatAt") or job.get("at") or ""))  # noqa: SLF001
    if beat and (datetime.now(timezone.utc) - beat).total_seconds() <= APPLY_STALL_S:
        return False
    box: dict = {}

    def mutate(current):
        cur = (current or {}).get("job")
        if not isinstance(cur, dict) or cur.get("status") != "running" or cur.get("claim") != job.get("claim"):
            return None
        current["job"] = {"status": "failed", "error": "소리 반영이 중단됐습니다. 다시 눌러 주세요.", "at": now_iso(), "n": cur.get("n")}
        box["done"] = True
        return current

    edit._mutate_json(key, mutate)  # noqa: SLF001
    if box:
        log.warning("vnew.sound.apply_abandoned pid=%s gen=%s variant=%s", pid, gen_id, variant)
    return bool(box)
