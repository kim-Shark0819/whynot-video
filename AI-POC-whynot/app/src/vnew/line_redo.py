"""vnew ⑥ 사운드 — 대사 고치기 · 그 대사 구간 영상 다시 만들기(작업 212, 2026-11-10).

한 줄의 대사를 고치거나(사용자가 새 글·말하는 사람·실제 말/속마음·말투 메모를 적음) AI 로 다시 쓰고(text LLM 1회, 후보 3줄),
그 줄 구간만 영상→영상으로 다시 만들어(⑦ AI로 고치기 파이프라인 재사용 — retake.compose 초안 · retake.submit 제출) 입 모양·연기·목소리를
새 대사에 맞춘다. 배경음악·앰비언트 기능은 없다(소리 규칙은 작업 211 의 대사만 규칙 generate.audio_rule).

저장(메인 버킷, gen 루트 = edit._root):
  대사 고침(그림체와 무관): {gen 루트}/sound/lines.json
    {v, overrides: {lineId: {lineId, textKo, speaker, thought, deliveryKo, at, original:{lineKo, speaker, voice},
                             scriptApplied, scriptAt?}}}
    원래 대본은 조용히 바꾸지 않는다. 사용자가 [대본에도 반영]을 확인했을 때만 그 대본 줄(lineKo·speaker·voice)을 바꾸고
    plan.revision 을 올린다(⑥ 목소리 저장 voices.save_profile 과 같은 방식 — 프리비즈 승인은 그대로 둔다. 대사는 녹화를 바꾸지 않는다).
    그러면 그 컷의 지시서 지문(shot_brief.cut_pv_sig — 컷 대사 줄 포함)이 달라져 기존 206 흐름(지시서 따라잡기 → 테이크 briefSig)이
    ⑤ [바뀐 테이크만 다시 만들기]에서 그 테이크를 고른다. 보기는 원래 대사(회색)와 새 대사를 함께 보인다(original).
  다시 만들기(그림체마다): {gen 루트}/sound/{variant}/redo.json
    {v, variant, current: r(0 = 원래 영상), revisions: [{r, key, at, baseKey, fromKey, fromR, lineId, range, pieces, retake, model, usd}],
     jobs: {lineId: {status quoted|running|splicing|done|failed, …}}}
  다시 만든 영상(영상 버킷): {gen 루트}/final-{variant}-lines-r{r}-{token}.mp4 — 새 키에만 쓴다(원래 완성본·⑦ 내보내기·이전 판은 그대로).

판(revision) 잇기: 실사 회차 병합본(final-real-r{n}·-fixed — 원래 병합본을 덮어쓰지 않고 새 키에 다시 이은 판을 두고 기록이 새 키를 가리킨다)과
같은 원칙이다. 바뀐 구간 [a, b] 만 retake 버전 결과(그 소리 = 새 대사)로 바꾸고 앞·뒤는 지금 원본에서 프레임 정확히 잘라 같은 크기·fps 로
다시 인코딩해 잇는다(stitcher.edit_segment · edit_join — generate._conform_concat 과 같은 단계). 길이(프레임 수)는 원본 그대로라
사운드 문서의 줄 시간은 그대로 맞는다. 판마다 pieces(타임라인 조각 → 재료 키·재료 초)를 남겨 다음 고침은 그 판에서 구간을 고른다
(작업 194 의 여러 버전 섞인 조각과 같은 생각 — 한 판 안에 원본 칸과 버전 결과가 섞인다).
⑥ 원본(sound._source)은 그 그림체의 기준(⑦ 내보내기 → ⑤ 완성본) 위에 current 판이 있으면 그 판이다(기준이 바뀌면 기준이 이긴다).
[이전 영상으로 되돌리기] = current 를 다른 판(0 = 기준)으로. 판이 바뀌면 그 그림체의 목소리 반영 기록(sound.json versions)은
그 원본이 아니면 outdated(“목소리 맞춤을 다시 반영해 주세요”) — 자동으로 다시 반영하지 않는다(돈을 쓰지 않는다).

구간(line_range): 사운드 문서의 줄 구간을 지금 원본의 조각 하나(가장 많이 겹치는 조각) 안으로 넣고, 모델 최소 길이(MIN_SEGMENT_S —
generate.PREVIZ_MIN_CLIP_S, 그보다 짧아도 그 길이로 과금된다)로 줄 가운데를 기준으로 늘리고, 컷·조각 경계가 SNAP_S 안이면 경계에 붙인다.

외부 호출 래퍼(검사가 바꾼다): _llm · _compose · _submit · _models · _retake_state · _download · _upload · _probe · _splice_file · _spawn
· _scenes · _scene_changes(fix3).
작업 212 fix2: 영상 모델 기본 = 그 구간을 다시 만들 수 있는 가장 싼 모델(default_model — Seedance 계열은 다른 모델이 없을 때만). 확인 창에서
다른 모델을 고르면 견적을 다시 받는다. 콘텐츠 검사 거절(작업 195)로 실패한 줄은 다음 견적이 다음으로 싼 모델로 올리고(stepUp) 새 금액을 보인 뒤
확인을 받는다(자동으로 더 비싸게 내지 않는다). 프롬프트는 retake.compose 가 모델 형식(reference · elements)마다 만든다.
generate.py · shot_brief.py 는 고치지 않는다(호출만 한다). retake.py 는 compose 의 edit_lines 인자만 쓴다(작업 212 fix1 — ⑦ 은 그대로).
프롬프트: 머리 EDIT 줄은 고정 틀(redo_edit_lines — 입·얼굴 연기·목소리만, 구도·카메라 그대로, 새 대사는 한국어로만, 영어는 (meaning: …) 뜻)
+ 소리 규칙 한 줄, EXTRA 줄은 없다. 제출 전 _finish_prompt 가 이전 프롬프트(CONTEXT) 쪽의 겹친 소리 규칙·앞선 EDIT 줄을 빼고 옛 대사를 새 대사로
바꾼다(retake.submit 는 사용자가 확인한 프롬프트를 그대로 검사·제출한다).
작업 212 fix3(다음 샷 삼키지 않기): 모델에는 늘린 구간을 그대로 보내고(최소 길이 = 모델·과금 한도), 잇기는 그 줄의 샷 안만 바꾼다 —
splice = [max(구간 시작, 샷 시작), min(구간 끝, 샷 끝, 줄 끝 + TAIL_S)], 컷 경계를 넘지 않는다. 나머지 구간은 원래 프레임·소리.
샷 경계(shot_bounds) = 조각 경계 + 콘티 컷 경계(정본 — sound 문서·편집 문서와 같은 _split_by_cuts 시각)를 장면 바뀜 검사
(ffmpeg select='gt(scene,0.35)' · showinfo, 파드에서, ffprobe 없이 — 그 원본 구간만)가 SCENE_MATCH_S 안이면 그 시각으로 다듬는다.
콘티 컷이 없는 조각(⑦ 내보내기·판의 새 조각)은 검사한 장면 바뀜이 경계다. 검사를 못 하면 콘티만 쓴다.
늘릴 때는 줄의 샷 안에서 먼저 늘리고, 샷이 최소 길이보다 짧을 때만 이웃 샷(더 긴 쪽)으로 넘는다. 구간 안에 컷이 있으면 EDIT 줄들 뒤에
HARD_CUT_LINE 한 줄(retake.compose 의 edit_lines). 소리: splice 안 = 새 영상 소리(대사만), 밖 = 원래 소리, 이음매마다 SEAM_FADE_S 크로스페이드.
그림은 이음매를 프레임 경계에 맞춘 그냥 컷이다(20ms 는 허용 fps 60 아래에서 한 프레임보다 짧다).
rebuild_revision(owner, pid, variant, n): 저장된 판 n 의 클립으로 새 규칙에 맞춰 다시 이은 새 판(새 키, 모델 호출 0, 라우트 없음).
"""

from __future__ import annotations

import copy
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso

from . import edit
from . import generate
from . import plan as plan_mod
from . import prompts
from . import retake
from . import settings as vnew_settings
from . import sound
from .fx import clean_text

log = logging.getLogger("vpoc")

# ================================================================ 상수

ENABLED = os.getenv("VNEW_LINE_REDO", "1").strip().lower() not in ("0", "false", "off", "no")
MIN_SEGMENT_S = max(float(retake.RANGE_MIN_S), float(generate.PREVIZ_MIN_CLIP_S))  # 모델 최소 길이(짧아도 이 길이로 과금)
SNAP_S = 0.35  # 컷·조각 경계가 이만큼 안이면 경계에 붙인다
# 작업 212 fix3
TAIL_S = 0.25  # 잇기 끝 = 줄 끝 + 이만큼(샷 끝·구간 끝을 넘지 않는다)
SCENE_THRESHOLD = 0.35  # ffmpeg select='gt(scene,…)'
SCENE_MATCH_S = 0.75  # 콘티 컷 경계에서 이만큼 안의 장면 바뀜이면 그 시각으로 다듬는다
BOUND_EDGE_S = 0.08  # 조각·구간 끝에서 이만큼 안의 경계는 경계로 보지 않는다
SEAM_FADE_S = 0.02  # 이음매 소리 크로스페이드
HARD_CUT_LINE = "@Video1 has a hard cut at {t} s to a different shot; keep that cut and the second shot exactly as in @Video1."
_SCENE_CACHE: dict = {}
_SCENE_CACHE_MAX = 32
TEXT_MAX = 200  # 새 대사 글자
DELIVERY_MAX = 80  # 말투 메모 글자
SUGGEST_COUNT = 3
SUGGEST_MAX_TOKENS = 600
SUGGEST_CONTEXT_LINES = 3  # 앞뒤로 함께 보내는 대본 줄 수
REVISIONS_MAX = 20
SPLICE_STALL_S = 900
START_WRITE_TRIES = 4  # 작업 219: 제출이 받아진 뒤 running 쓰기 재시도
START_WRITE_WAIT_S = 0.25
ORPHAN_S = float(retake.PREPARE_STALL_S)  # 작업 220: running 인데 retake 상태에 버전이 이만큼 없으면 failed(끝없는 running 방지)
ORPHAN_TEXT = "다시 만들기 기록을 찾지 못했습니다. 금액을 다시 확인한 뒤 눌러 주세요."
OUTDATED_TEXT = "목소리 맞춤을 다시 반영해 주세요"
NO_AUDIO_TEXT = "새 영상에 소리가 없어 그 구간은 원래 소리를 썼습니다. 목소리 맞춤으로 새 대사 목소리를 넣어 주세요."
TAKE_REDO_TEXT = ("대본에 반영한 대사는 ⑤ [바뀐 테이크만 다시 만들기]로 그 테이크 전체를 다시 만들 수 있습니다. "
                  "촬영 지시서를 먼저 고친 뒤 금액을 보여 줍니다.")
# 작업 212 fix1: 대사 다시 만들기 프롬프트(고정 틀 — redo_edit_lines). PROMPT_V = 견적 작업의 프롬프트 꼴. 다른 꼴(옛 견적 · 실패한 작업)은
# 시작하지 않고 견적부터 다시 받는다(새 틀로 다시 만든다 — 저장된 옛 프롬프트를 다시 보내지 않는다).
PROMPT_V = 3  # fix3: 잇기 구간(splice)·컷 줄이 없는 옛 견적은 견적부터 다시
LINE_EDIT_HEAD = "EDIT: Edit @Video1 only in the mouth, face acting and voice of "
LINE_EDIT_KEEP = "Keep framing, camera, composition, people, positions, costume, lighting and timing identical to @Video1."
LINE_SPEAK = "Speak it in Korean exactly as written; never speak English."
LINE_INNER = "Inner voice: the mouth stays closed; the Korean line is heard as an inner voice."
FULL_AUDIO_MIN_BYTES = 2000  # 프롬프트 상한이 이보다 작으면 소리 규칙은 짧은 형
GLOSS_MAX = 160  # 영어 뜻 글자
_QUOTED_RE = re.compile(r"\"([^\"\n]{1,400})\"|\u201c([^\u201d\n]{1,400})\u201d")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_SPLICE_SEM = threading.BoundedSemaphore(1)
_threads: dict[str, threading.Thread] = {}
_threads_guard = threading.Lock()

_SUGGEST_TOOL = {
    "name": "emit_lines",
    "description": "Return exactly three alternative Korean dialogue lines.",
    "inputSchema": {"json": {
        "type": "object", "required": ["lines"],
        "properties": {"lines": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}}},
    }},
}
_SUGGEST_SYSTEM = (
    "You rewrite one line of dialogue for a Korean drama adapted from a webtoon. Keep the speaker, the situation and the "
    "meaning of the scene; fit the character's voice and the neighbouring lines. Return exactly three different natural "
    "Korean lines (each one sentence or two, no quotes, no speaker name, no stage directions) with emit_lines."
)


class _ClaimLost(Exception):
    """다른 실행이 잇기를 가져갔다(이 실행은 결과를 쓰지 않는다)."""


# ================================================================ 키 · 설정


def overrides_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{edit._root(owner, pid, gen_id)}/sound/lines.json"  # noqa: SLF001


def redo_key(owner: str, pid: str, gen_id: str, variant: str) -> str:
    return f"{edit._root(owner, pid, gen_id)}/sound/{variant}/redo.json"  # noqa: SLF001


def revision_key(owner: str, pid: str, gen_id: str, variant: str, r: int, token: str = "") -> str:
    """새 판 키. 작업 219: 잇기 · 다시 잇기는 token(secrets.token_hex(3))을 붙여 판마다 고유하게 쓴다 — 두 파드가 같은 r 로
    동시에 올려도 서로의 파일을 덮지 않는다(LineRedoRace 는 진 쪽만 거절). 판의 실제 키는 늘 rev["key"] 다."""
    tail = f"-{token}" if token else ""
    return f"{edit._root(owner, pid, gen_id)}/final-{variant}-lines-r{int(r)}{tail}.mp4"  # noqa: SLF001


def enabled() -> bool:
    return bool(ENABLED and vnew_settings.VNEW_SOUND)


def require_enabled(where: str = "vnew.sound.lines") -> None:
    sound.require_enabled(where)
    if not ENABLED:
        raise VpocError(where, "LineRedoDisabled", "대사 고치기 기능이 꺼져 있습니다.", 404)


# ================================================================ 외부 호출 래퍼(검사가 바꾼다)


def _llm(*, system_text: str, blocks: list[dict], model: str) -> tuple[dict, dict]:
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다(shot_brief · retake 와 같다)
        system_text=system_text, blocks=blocks, tool=_SUGGEST_TOOL, max_tokens=SUGGEST_MAX_TOKENS,
        where="vnew.sound.lines.suggest", model=model)


def _compose(owner: str, pid: str, mid: str, payload: dict, edit_lines=None) -> dict:
    return retake.compose(owner, pid, mid, payload, edit_lines=edit_lines)


def _submit(owner: str, pid: str, mid: str, payload: dict) -> dict:
    return retake.submit(owner, pid, mid, payload)


def _models(owner: str, pid: str, mid: str, rng: dict) -> list[dict]:
    """작업 212 fix2: 그 재료·구간의 retake 모델 줄(⑦ 보기와 같은 _choices — 구간 견적 range 포함, 영상 호출 0)."""
    view = retake.retake_view(owner, pid, mid, clip_in=rng["in"], clip_out=rng["out"])
    return [m for m in (view or {}).get("models") or [] if isinstance(m, dict)]


def _retake_state(owner: str, pid: str, gen_id: str) -> dict:
    return retake.reconcile_state(owner, pid, gen_id)


def _download(key: str, path: str) -> int:
    return edit._download(key, path, bucket=vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001


def _upload(path: str, key: str) -> None:
    edit._upload(path, key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")  # noqa: SLF001


def _probe(path: str) -> dict:
    return stitcher.probe_media(path)


def _scenes(path: str, start: float, end: float) -> list[float]:
    """작업 212 fix3: 로컬 영상 [start, end] 의 장면 바뀜 시각(원본 초). ffmpeg select='gt(scene,SCENE_THRESHOLD)' + showinfo 의
    pts_time(입력 -ss 로 0 부터 센다) + start. ffprobe 를 쓰지 않는다. ffmpeg 가 실패하면 StitchError(부르는 쪽은 콘티만 쓴다)."""
    start, end = max(0.0, float(start)), float(end)
    if end - start <= 0.04:
        return []
    command = [stitcher.ffmpeg_path(), "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path,
               "-map", "0:v:0", "-an", "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"]
    result = stitcher._edit_run(command)  # noqa: SLF001 — 편집 ffmpeg 와 같은 nice · 시간 제한
    if result.returncode != 0:
        raise stitcher.StitchError("장면 바뀜을 읽지 못했습니다.")
    text = result.stderr.decode("utf-8", errors="replace")
    return sorted({_r3(start + float(m)) for m in re.findall(r"pts_time:\s*(-?[0-9]+(?:\.[0-9]+)?)", text) if float(m) > 0})


def _scene_changes(key: str, start: float, end: float) -> list[float]:
    """견적 때: 원본 키를 받아 [start, end] 장면 바뀜(_scenes). 같은 원본·구간은 프로세스 안에서 한 번만 받는다."""
    cache_key = (str(key), _r3(start), _r3(end))
    if cache_key in _SCENE_CACHE:
        return list(_SCENE_CACHE[cache_key])
    with tempfile.TemporaryDirectory(prefix="vnew-line-scene-") as workdir:
        path = os.path.join(workdir, "source.mp4")
        _download(key, path)
        found = _scenes(path, start, end)
    while len(_SCENE_CACHE) >= _SCENE_CACHE_MAX:
        _SCENE_CACHE.pop(next(iter(_SCENE_CACHE)))
    _SCENE_CACHE[cache_key] = list(found)
    return found


def _spawn(target, args: tuple, name: str) -> None:
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    with _threads_guard:
        for done in [k for k, t in _threads.items() if not t.is_alive()]:
            _threads.pop(done, None)
        _threads[str(args[-1])] = thread
    thread.start()


def _alive_here(claim) -> bool:
    with _threads_guard:
        thread = _threads.get(str(claim or ""))
        return bool(thread is not None and thread.is_alive())


def _splice_file(base_path: str, new_path: str, out_path: str, *, start: float, end: float, duration: float, width: int,
                 height: int, fps: int, base_audio: bool, new_audio: bool, splice_start: float | None = None,
                 splice_end: float | None = None) -> None:
    """새 영상 = 원본 [start, end] 을 다시 만든 것(0초 = start). 작업 212 fix3: 그중 [splice_start, splice_end](없으면 구간 전체)만 쓴다 —
    원본 [0, sa) + 새 영상 [sa − start, sb − start) + 원본 [sb, 끝]. 프레임 수·크기·fps 는 원본 그대로(누적 시각 반올림 — 이음매는 프레임 경계).
    소리: 원본 전체 PCM 위에 sa~sb 만 새 영상 소리(없으면 원본 그대로), 이음매마다 SEAM_FADE_S 크로스페이드. 48k 스테레오 PCM → 한 번 AAC(edit_join)."""
    total = max(1, round(duration * fps))
    flo = min(total - 1, max(0, round(float(start) * fps)))
    fhi = min(total, max(flo + 1, round(float(end) * fps)))
    sa = float(start if splice_start is None else splice_start)
    sb = float(end if splice_end is None else splice_end)
    fa = min(fhi - 1, max(flo, round(sa * fps)))
    fb = min(fhi, max(fa + 1, round(sb * fps)))
    per = stitcher.EDIT_AUDIO_RATE // fps  # EDIT_FPS_CHOICES 는 48000 을 나누어떨어지게 한다
    with tempfile.TemporaryDirectory(prefix="vnew-line-splice-") as workdir:
        parts = ([(base_path, 0.0, fa)] if fa > 0 else []) + [(new_path, (fa - flo) / fps, fb - fa)] + \
                ([(base_path, fb / fps, total - fb)] if total - fb > 0 else [])
        videos: list[str] = []
        for index, (source, at, frames) in enumerate(parts):
            video = os.path.join(workdir, f"part-{index}.mp4")
            stitcher.edit_segment(source, video, start_sec=at, frames=frames, fps=fps, width=width, height=height)
            videos.append(video)
        pcm = os.path.join(workdir, "audio.pcm")
        if base_audio:
            stitcher.edit_audio(base_path, pcm, start_sec=0.0, samples=total * per)
        else:
            stitcher._fit_pcm(pcm, total * per * stitcher.EDIT_AUDIO_BYTES_PER_SAMPLE)  # noqa: SLF001 — 무음
        if new_audio:
            clip = os.path.join(workdir, "clip.pcm")
            stitcher.edit_audio(new_path, clip, start_sec=0.0, samples=(fhi - flo) * per)
            _blend_pcm(pcm, clip, offset=flo * per, a=fa * per, b=fb * per, total=total * per,
                       fade=round(SEAM_FADE_S * stitcher.EDIT_AUDIO_RATE))
        stitcher.edit_join(videos, out_path, audio_path=pcm)


def _blend_pcm(dst: str, clip: str, *, offset: int, a: int, b: int, total: int, fade: int) -> None:
    """dst(원본 전체 PCM, 스테레오 s16le) 의 샘플 [a, b) 를 clip(타임라인 offset 부터) 으로 바꾸고 a·b 에 fade 샘플 길이의 직선
    크로스페이드(이음매 가운데, clip 이 있는 곳 안으로 민다). 소리 바이트만 읽고 쓴다(원본 전체를 메모리에 올리지 않는다)."""
    import array
    import sys

    bps = stitcher.EDIT_AUDIO_BYTES_PER_SAMPLE
    clip_len = os.path.getsize(clip) // bps
    end = min(b, offset + clip_len, total)

    def read(handle, at: int, count: int) -> array.array:
        handle.seek(at * bps)
        data = array.array("h")
        data.frombytes(handle.read(count * bps).ljust(count * bps, b"\0"))
        if sys.byteorder == "big":
            data.byteswap()
        return data

    windows = []
    with open(dst, "r+b") as out, open(clip, "rb") as src:
        if fade >= 2 and end - a >= fade:
            for seam, rising in ((a, True), (end, False)):
                w0 = min(max(seam - fade // 2, offset, 0), min(offset + clip_len, total) - fade)
                if w0 < offset or w0 < 0:
                    continue
                base_w, clip_w = read(out, w0, fade), read(src, w0 - offset, fade)
                mixed = array.array("h", bytes(len(base_w) * 2))
                for i in range(fade):
                    x = (i + 0.5) / fade
                    x = x if rising else 1.0 - x
                    for ch in (2 * i, 2 * i + 1):
                        mixed[ch] = max(-32768, min(32767, int(round(base_w[ch] * (1.0 - x) + clip_w[ch] * x))))
                windows.append((w0, mixed))
        src.seek((a - offset) * bps)
        out.seek(a * bps)
        remaining = max(0, end - a) * bps
        while remaining > 0:
            block = src.read(min(remaining, 1 << 20))
            if not block:
                break
            out.write(block)
            remaining -= len(block)
        for w0, mixed in windows:
            if sys.byteorder == "big":
                mixed.byteswap()
            out.seek(w0 * bps)
            out.write(mixed.tobytes())


# ================================================================ 순수 함수: 구간 · 요청 글 · 프롬프트


def _r3(value: float) -> float:
    return round(float(value), 3)


def shot_bounds(piece: dict, scenes=()) -> list[float]:
    """작업 212 fix3: 조각 안의 샷 경계(정렬, 조각 시작·끝 포함). 콘티 컷 경계(정본)는 SCENE_MATCH_S 안의 장면 바뀜이 있으면 그 시각으로
    다듬고, 콘티 컷이 없는 조각은 장면 바뀜이 경계다. 조각 끝 BOUND_EDGE_S 안은 조각 끝으로 본다."""
    t0, t1 = float(piece["t0"]), float(piece["t1"])

    def inside(v: float) -> bool:
        return t0 + BOUND_EDGE_S < v < t1 - BOUND_EDGE_S

    conti = sorted({float(v) for c in piece.get("cuts") or [] for v in (c[0], c[1]) if inside(float(v))})
    found = sorted(float(s) for s in scenes or () if isinstance(s, (int, float)) and not isinstance(s, bool) and inside(float(s)))
    if conti:
        inner = []
        for b in conti:
            near = min(found, key=lambda s: abs(s - b), default=None)
            inner.append(near if near is not None and abs(near - b) <= SCENE_MATCH_S else b)
    else:
        inner = found
    return sorted({t0, t1, *(_r3(v) for v in inner)})


def _own_shot(bounds: list[float], start: float, end: float) -> tuple[float, float]:
    """줄의 샷 = 줄과 가장 많이 겹치는 샷(같으면 줄 가운데가 든 샷)."""
    mid = (start + end) / 2
    shots = list(zip(bounds, bounds[1:]))
    return max(shots, key=lambda s: (min(end, s[1]) - max(start, s[0]), s[0] <= mid <= s[1]))


def _splice_in(lo: float, hi: float, start: float, end: float, bounds: list[float], rate: float) -> dict:
    """구간 [lo, hi] 안의 잇기 구간·컷. splice = [max(lo, 샷 시작), min(hi, 샷 끝, 줄 끝 + TAIL_S)] — 컷 경계를 넘지 않는다.
    cutsAt = 보낸 영상(@Video1) 안의 컷 시각(재료 초)."""
    s0, s1 = _own_shot(bounds, start, end)
    sa = max(lo, s0)
    sb = min(hi, s1, max(min(end, hi), sa) + TAIL_S)
    if sb - sa <= 1e-6:
        sa, sb = lo, hi
    cuts = [b for b in bounds if lo + BOUND_EDGE_S < b < hi - BOUND_EDGE_S]
    return {"line": [_r3(start), _r3(end)], "shot": [_r3(s0), _r3(s1)], "cuts": [_r3(c) for c in cuts],
            "cutsAt": [_r3((c - lo) * rate) for c in cuts], "splice": [_r3(sa), _r3(sb)]}


def _best_piece(pieces: list[dict], start: float, end: float) -> dict | None:
    usable = [p for p in pieces or [] if isinstance(p, dict) and float(p.get("t1", 0)) - float(p.get("t0", 0)) > 1e-6]
    if not usable:
        return None
    mid = (start + end) / 2

    def overlap(p):
        return min(end, float(p["t1"])) - max(start, float(p["t0"]))

    def distance(p):
        return 0.0 if float(p["t0"]) <= mid <= float(p["t1"]) else min(abs(mid - float(p["t0"])), abs(mid - float(p["t1"])))

    return usable[max(range(len(usable)), key=lambda i: (overlap(usable[i]), -distance(usable[i])))]


def _fit(lo: float, length: float, a: float, b: float) -> tuple[float, float]:
    lo = min(max(lo, a), b - length)
    return lo, lo + length


def line_range(start: float, end: float, pieces: list[dict], *, min_s: float = MIN_SEGMENT_S, snap_s: float = SNAP_S,
               scenes=()) -> dict:
    """줄 구간(원본 초) → 다시 만들 구간. 조각 = {t0, t1, sourceKey, in0, rate, cuts?: [[t0, t1, cutId]]}.

    1. 가장 많이 겹치는 조각(없으면 줄 가운데에 가장 가까운 조각) 안으로 자른다. 2. min_s 보다 짧으면 늘린다(fix3: 줄의 샷이 min_s 이상이면
    그 샷 안에서 가운데 기준, 샷이 더 짧으면 샷 전체 + 더 긴 이웃 샷 쪽 — 조각 안으로 민다, 조각이 더 짧으면 조각 전체).
    3. 시작·끝 근처(snap_s 안)에 샷 경계(shot_bounds)가 있으면 붙인다(길이가 min_s 아래로 줄지 않을 때만).
    4. retake.RANGE_MIN_S 보다 짧으면 400 LineRangeShort. 반환 {start, end, piece, sourceKey, in, out, padded, snapped:[시작, 끝]}
    + fix3 _splice_in(line · shot · cuts · cutsAt · splice)."""
    if not [p for p in pieces or [] if isinstance(p, dict) and float(p.get("t1", 0)) - float(p.get("t0", 0)) > 1e-6]:
        raise VpocError("vnew.sound.lines", "LineNoRange", "이 대사 구간의 영상을 찾지 못했습니다. 화면을 다시 불러와 주세요.", 409)
    start, end = float(start), float(end)
    mid = (start + end) / 2
    piece = _best_piece(pieces, start, end)
    t0, t1 = float(piece["t0"]), float(piece["t1"])
    bounds = shot_bounds(piece, scenes)
    s0, s1 = _own_shot(bounds, start, end)
    lo, hi = max(start, t0), min(end, t1)
    if hi <= lo:
        lo = hi = min(max(mid, t0), t1)
    padded = False
    if hi - lo < min_s:
        padded = True
        if t1 - t0 <= min_s:
            lo, hi = t0, t1
        elif s1 - s0 >= min_s:  # fix3: 줄의 샷 안에서 먼저 늘린다
            lo, hi = _fit((lo + hi) / 2 - min_s / 2, min_s, s0, s1)
        else:  # 샷이 최소 길이보다 짧다 → 샷 전체 + 더 긴 이웃 샷 쪽으로(컷을 넘는다 — 프롬프트에 컷 줄)
            i = bounds.index(s0)
            before = s0 - bounds[i - 1] if i > 0 else 0.0
            after = bounds[i + 2] - s1 if i + 2 < len(bounds) else 0.0
            lo, hi = _fit(s0 if after >= before else s1 - min_s, min_s, t0, t1)
    snapped = [False, False]
    floor = min(min_s, t1 - t0)
    near = min(bounds, key=lambda b: abs(b - lo))
    if 1e-6 < abs(near - lo) <= snap_s and hi - near >= floor - 1e-6:
        lo, snapped[0] = near, True
    near = min(bounds, key=lambda b: abs(b - hi))
    if 1e-6 < abs(near - hi) <= snap_s and near - lo >= floor - 1e-6:
        hi, snapped[1] = near, True
    if hi - lo < float(retake.RANGE_MIN_S) - 1e-6:
        raise VpocError("vnew.sound.lines", "LineRangeShort",
                        f"이 대사 구간이 들어 있는 영상 조각이 {retake.RANGE_MIN_S:g}초보다 짧아 다시 만들 수 없습니다.", 400)
    rate = float(piece.get("rate") or 1.0)
    in0 = float(piece.get("in0") or 0.0)
    out = {"start": _r3(lo), "end": _r3(hi), "piece": pieces.index(piece), "sourceKey": str(piece.get("sourceKey") or ""),
           "in": _r3(in0 + (lo - t0) * rate), "out": _r3(in0 + (hi - t0) * rate), "padded": padded, "snapped": snapped}
    out.update(_splice_in(lo, hi, start, end, bounds, rate))
    return out


def _prompt_text(value, limit: int) -> str:
    """프롬프트에 넣는 사용자 글(대사·말투·뜻): 한 줄, 제어 문자·주소 제거, 꺾쇠 바꿈, 큰따옴표 → 작은따옴표(따옴표 묶음이 깨지지 않게),
    참고 표시(@…)는 글로."""
    text = retake._quote_user(retake._clean_user(value))  # noqa: SLF001
    text = re.sub(r"\s+", " ", text.replace('"', "'").replace("\u201c", "'").replace("\u201d", "'"))
    return re.sub(r"@(?=\w)", "", text).strip()[:limit]


def _gloss(change_en: str) -> str:
    """retake 번역 한 문장(초안과 같은 LLM 호출)에서 새 대사의 영어 뜻 = 첫 따옴표 묶음. 없거나 한글이 남으면 ""(뜻 없이 한국어만)."""
    match = _QUOTED_RE.search(str(change_en or ""))
    text = (match.group(1) or match.group(2) or "") if match else ""
    if not text or _HANGUL_RE.search(text):
        return ""
    return _prompt_text(text.replace("(", ",").replace(")", ","), GLOSS_MAX).strip(" ,;:.")


def redo_request(override: dict) -> str:
    """retake compose 의 수정 사항(한국어 — ⑦ 기록·번역 입력). 번역의 첫 따옴표 묶음이 새 대사의 영어 뜻이 된다(_gloss)."""
    speaker = str(override.get("speaker") or "").strip() or "말하는 사람"
    text = str(override.get("textKo") or "").strip()
    delivery = str(override.get("deliveryKo") or "").strip()
    if override.get("thought"):
        request_ko = f"{speaker}의 속마음 대사를 바꾼다. 새 속마음: \"{text}\". 입은 다문 채 목소리만 들린다."
    else:
        request_ko = f"{speaker}의 대사를 바꾼다. 새 대사: \"{text}\". 입 모양·표정·연기·목소리를 새 대사에 맞춘다."
    if delivery:
        request_ko += f" 말투: {delivery}."
    return request_ko[: retake.REQUEST_MAX]


def hard_cut_lines(cuts_at=()) -> list[str]:
    """작업 212 fix3: 보낸 구간(@Video1) 안의 컷마다 한 줄(HARD_CUT_LINE, 시각은 @Video1 초 — 소수 둘째 자리까지)."""
    out = []
    for t in cuts_at or ():
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
            out.append(HARD_CUT_LINE.format(t=f"{float(t):.2f}".rstrip("0").rstrip(".")))
    return out


def redo_edit_lines(override: dict, change_en: str = "", max_bytes: int = 0, cuts_at=(), *, anime: bool = False,
                    setting: str | tuple = "") -> list[str]:
    """작업 212 fix1: 대사 다시 만들기 EDIT 줄(고정 틀 — ⑦ 의 "구도가 바뀌어도 끝까지 적용" 머리를 쓰지 않는다)과 소리 규칙 한 줄.

    EDIT = 입·얼굴 연기·목소리만 고침 · 구도·카메라·사람·자리·옷·조명·타이밍 그대로 · 새 대사는 한국어 따옴표로만(영어는 (meaning: …) 안의 뜻)
    · 영어로 말하지 않음 · 속마음이면 입 다묾 · 말투 메모(한국어 따옴표, 말하지 않음). 소리 = 작업 211 대사만 규칙(속마음이면 inner 형,
    상한이 FULL_AUDIO_MIN_BYTES 보다 작으면 짧은 형). fix3: cuts_at(구간 안 컷, @Video1 초)이 있으면 그 뒤에 컷 줄(hard_cut_lines)."""
    speaker = _prompt_text(override.get("speaker"), sound.MAX_SPEAKER_LEN) or "the speaker"
    text = _prompt_text(override.get("textKo"), TEXT_MAX)
    delivery = _prompt_text(override.get("deliveryKo"), DELIVERY_MAX)
    thought = bool(override.get("thought"))
    gloss = _gloss(change_en)
    meaning = f" (meaning: {gloss})" if gloss else ""
    parts = [f"{LINE_EDIT_HEAD}{speaker}. {LINE_EDIT_KEEP}", f"{speaker} now says, in Korean: \"{text}\"{meaning}. {LINE_SPEAK}"]
    if thought:
        parts.append(LINE_INNER)
    if delivery:
        parts.append(f"Acting note in Korean (do not speak it): '{delivery}'.")
    compact = bool(max_bytes) and int(max_bytes) < FULL_AUDIO_MIN_BYTES
    # 작업 222: 애니(webtoon) 구간이면 소리 규칙 줄 끝에 자막 금지 문장(generate.audio_rule anime — 머리 줄이라 자르지 않는다).
    # 작업 223: setting(generate.setting_block — 배경 줄 + 조화 문장)이 있으면 머리 끝 줄(자르지 않는다, 이전 프롬프트의 배경 줄은
    # _finish_prompt 가 뺀다 — 한 번만). 223 fix1: (긴 형, 바닥 형) 이면 상한이 FULL_AUDIO_MIN_BYTES 보다 작을 때 바닥 형.
    if isinstance(setting, (tuple, list)):
        setting = (setting[1] if compact else setting[0]) if setting else ""
    return [" ".join(parts), generate.audio_rule(True, compact=compact, inner=thought, anime=anime), *hard_cut_lines(cuts_at),
            *([setting] if str(setting or "").strip() else [])]


# 작업 223: 이전 프롬프트(⑦ 문맥)의 배경 줄 머리(vibe 배경 줄 · 223 배경 줄) — 머리에 223 배경 줄이 있으면 뺀다.
_SETTING_HEADS = (generate.SETTING_LINE_HEAD + ":", generate.VIBE_SETTING_HEAD + ":")


def _line_setting(plan: dict, pieces: list[dict], rng: dict, real: bool) -> tuple[str, str]:
    """작업 223: 대사 구간 컷 장소의 배경 줄 + 조화 문장(generate.setting_block). 컷을 모르면 대본 첫 장소. 223 끔이면 ("", "").
    223 fix1: (긴 형, 바닥 형) — 좁은 상한(redo_edit_lines compact)이면 바닥 형(장소 + 시간대 + 주 광원 + 짧은 조화 문장)."""
    piece = pieces[rng["piece"]] if isinstance(rng.get("piece"), int) and 0 <= rng["piece"] < len(pieces) else {}
    line = rng.get("line") or [rng.get("start", 0.0), rng.get("end", 0.0)]
    mid = (float(line[0]) + float(line[-1])) / 2
    cut_id = next((str(c[2]) for c in (piece or {}).get("cuts") or []
                   if isinstance(c, (list, tuple)) and len(c) >= 3 and float(c[0]) <= mid < float(c[1])), "")
    cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == cut_id), {})
    where = str(cut.get("locationEn") or "")
    return generate.setting_block(plan, where, real=real), generate.setting_block(plan, where, real=real, room=0)


_AUDIO_RULES = tuple(sorted({generate.AUDIO_DIALOGUE_ONLY, generate.AUDIO_DIALOGUE_ONLY_SPOKEN, generate.AUDIO_DIALOGUE_ONLY_COMPACT,
                             generate.AUDIO_SILENCE_ONLY, generate.AUDIO_SILENCE_ONLY_COMPACT}, key=len, reverse=True))


def audio_rule_count(prompt: str) -> int:
    return sum(str(prompt or "").count(rule) for rule in _AUDIO_RULES)


def _finish_prompt(prompt: str, old_line: str, new_line: str, max_bytes: int) -> str:
    """retake 초안 → 제출할 프롬프트. 머리(CONTEXT 줄까지)는 그대로, 이전 프롬프트(CONTEXT 뒤) 쪽만 고친다.
    1. 앞선 대사 다시 만들기의 EDIT 줄(이 판 결과를 다시 고칠 때 이전 프롬프트에 남는다 — ⑦ 의 EDIT 는 retake 가 EARLIER EDIT 로 바꾼다)은 뺀다.
    2. 소리 규칙은 한 번만: 머리에 있으면 뒤의 것을 지우고, 없으면 뒤의 첫 것만 둔다(지워서 빈 줄은 뺀다).
    3. 옛 대사 → 새 대사(_retarget — 머리의 새 대사 글은 건드리지 않는다). 바이트 상한을 넘으면 3 은 하지 않는다."""
    lines = str(prompt or "").split("\n")
    at = lines.index(retake.COMPOSE_CONTEXT_LINE) + 1 if retake.COMPOSE_CONTEXT_LINE in lines else len(lines)
    head, rest = lines[:at], [line for line in lines[at:] if not line.startswith("EDIT:")]
    seen = audio_rule_count("\n".join(head)) > 0
    no_subs = generate.ANIME_NO_SUBTITLES
    subs_seen = bool(no_subs) and no_subs in "\n".join(head)  # 작업 222: 머리에 자막 금지 문장이 있으면 뒤의 것은 지운다(한 번만)
    setting_seen = any(line.startswith(generate.SETTING_LINE_HEAD + ":") for line in head)  # 작업 223: 배경 줄 한 번만
    kept: list[str] = []
    for line in rest:
        if setting_seen and line.startswith(_SETTING_HEADS):
            continue
        if seen:
            stripped = line
            for rule in _AUDIO_RULES:
                stripped = stripped.replace(rule, "")
            line = re.sub(r" {2,}", " ", stripped).strip() if stripped != line else line
        elif audio_rule_count(line):
            seen = True
        if subs_seen and no_subs in line:
            line = re.sub(r" {2,}", " ", line.replace(no_subs, "")).strip()
        if line.strip():
            kept.append(line)
    context = "\n".join(kept)
    moved = _retarget(context, old_line, new_line, 0)
    out = "\n".join(head + ([moved] if moved else []))
    if max_bytes and len(out.encode("utf-8")) > int(max_bytes):
        out = "\n".join(head + ([context] if context else []))
    return out


def _retarget(prompt: str, old_line: str, new_line: str, max_bytes: int) -> str:
    """retake 초안의 이전 프롬프트 부분에 남은 옛 대사 글을 새 대사로(바이트 상한을 넘으면 초안 그대로)."""
    old_line, new_line = str(old_line or "").strip(), str(new_line or "").strip()
    if not old_line or old_line == new_line or old_line not in prompt:
        return prompt
    out = prompt.replace(old_line, new_line)
    return out if not max_bytes or len(out.encode("utf-8")) <= int(max_bytes) else prompt


# ================================================================ 작업 212 fix2: 모델 고르기(가장 싼 모델 기본 · Seedance 는 마지막)


def _is_seedance(model_id) -> bool:
    return "seedance" in str(model_id or "").lower()


def model_rows(rows: list[dict]) -> list[dict]:
    """retake 모델 줄 → 대사 다시 만들기에 쓸 수 있는 줄(retake._v2v_row — 영상→영상 형식 · 구간 견적 됨 · 시작 그림 경로 아님).
    반환 [{id, label, usd(구간 견적, 모르면 None), original, seedance, format}] — 싼 순서(값 모름은 뒤, 같으면 원래 모델 먼저, 그다음 목록 순서)."""
    out = []
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict) or not row.get("id") or not retake._v2v_row(row):  # noqa: SLF001
            continue
        usd = (row.get("range") or {}).get("usd")
        usd = float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else None
        out.append({"id": str(row["id"]), "label": str(row.get("label") or row["id"]), "usd": usd, "original": bool(row.get("original")),
                    "seedance": _is_seedance(row["id"]), "format": str(row.get("format") or ""), "_i": index})
    out.sort(key=lambda r: (r["usd"] is None, r["usd"] if r["usd"] is not None else 0.0, not r["original"], r["_i"]))
    for row in out:
        row.pop("_i")
    return out


def default_model(rows: list[dict], refused=()) -> str:
    """기본 모델: 가장 싼 줄(같으면 원래 모델). Seedance 계열은 다른 줄이 없을 때만. refused(콘텐츠 검사로 거절한 모델)는 빼고
    다음으로 싼 줄로 올린다(다 거절했으면 빼지 않는다). 쓸 줄이 없으면 ""(retake.compose 기본)."""
    usable = model_rows(rows)
    fresh = [r for r in usable if r["id"] not in set(refused or ())] or usable
    priced = [r for r in fresh if r["usd"] is not None] or fresh  # 값을 모르는 줄은 확인할 금액이 없어 마지막
    pool = [r for r in priced if not r["seedance"]] or priced
    return pool[0]["id"] if pool else ""


# ================================================================ 문서 읽기·쓰기


def read_overrides(owner: str, pid: str, gen_id: str) -> dict:
    data, _ = edit._read_json(overrides_key(owner, pid, gen_id))  # noqa: SLF001
    raw = (data or {}).get("overrides")
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def read_redo(owner: str, pid: str, gen_id: str, variant: str) -> dict:
    data, _ = edit._read_json(redo_key(owner, pid, gen_id, variant))  # noqa: SLF001
    data = data if isinstance(data, dict) else {}
    return {"v": 1, "variant": variant, "current": int(data.get("current") or 0),
            "revisions": [r for r in data.get("revisions") or [] if isinstance(r, dict)],
            "jobs": {str(k): v for k, v in (data.get("jobs") or {}).items() if isinstance(v, dict)}}


def _mutate_redo(owner: str, pid: str, gen_id: str, variant: str, fn) -> dict:
    def wrap(current):
        base = current if isinstance(current, dict) else {}
        doc = {"v": 1, "variant": variant, "current": int(base.get("current") or 0),
               "revisions": [r for r in base.get("revisions") or [] if isinstance(r, dict)],
               "jobs": {str(k): v for k, v in (base.get("jobs") or {}).items() if isinstance(v, dict)}}
        return fn(doc)

    return edit._mutate_json(redo_key(owner, pid, gen_id, variant), wrap)  # noqa: SLF001


def current_source(owner: str, pid: str, gen_id: str, variant: str, base: dict) -> dict:
    """sound._source 가 부른다: 기준(⑦ 내보내기·⑤ 완성본) 위에 지금 판(current)이 있으면 그 판, 아니면 기준 그대로.
    판이 다른 기준에서 만든 것이면(기준이 바뀌었다) 기준이 이긴다. chain = 같은 기준의 키들(시간이 같다)."""
    redo = read_redo(owner, pid, gen_id, variant)
    chain = [base["key"]] + [str(r.get("key")) for r in redo["revisions"] if r.get("baseKey") == base["key"] and r.get("key")]
    rev = next((r for r in redo["revisions"] if int(r.get("r") or 0) == redo["current"] and redo["current"] > 0), None)
    if not rev or rev.get("baseKey") != base["key"] or not str(rev.get("key") or "").startswith(f"users/{owner}/video/{pid}/"):
        return dict(base, chain=chain) if len(chain) > 1 else base
    out = {"kind": "redo", "key": str(rev["key"]), "r": int(rev["r"]), "baseKind": base["kind"], "baseKey": base["key"],
           "clips": base.get("clips") or [], "chain": chain}
    if base.get("n") is not None:
        out["n"] = base["n"]
    return out


def _base_key(source: dict) -> str:
    return str(source.get("baseKey") or source.get("key") or "")


def _mark_versions(owner: str, pid: str, gen_id: str, variant: str, current_key: str) -> int:
    """목소리 반영 기록: 지금 원본이 아닌 것은 outdated, 지금 원본인 것은 표시를 뗀다. 반환: 바꾼 수."""
    box = {"n": 0}

    def mutate(doc):
        if not isinstance(doc, dict):
            return None
        for item in doc.get("versions") or []:
            if not isinstance(item, dict):
                continue
            stale = item.get("sourceKey") != current_key
            if stale and not item.get("outdated"):
                item["outdated"] = True
                box["n"] += 1
            elif not stale and item.get("outdated"):
                item.pop("outdated", None)
                box["n"] += 1
        return doc if box["n"] else None

    edit._mutate_json(sound.doc_key(owner, pid, gen_id, variant), mutate)  # noqa: SLF001
    return box["n"]


# ================================================================ 조각(원본 타임라인 → 재료)


def _pieces(owner: str, pid: str, gen_id: str, plan: dict, gen: dict, variant: str, source: dict, duration: float) -> list[dict]:
    """지금 원본의 조각. ⑤ 완성본 = 완료된 칸 순서(sound._final_cut_spans 와 같은 길이·비율), ⑦ 내보내기 = 클립 순서(재료 in·속도),
    판(redo) = 그 판의 pieces."""
    kind = source.get("kind")
    if kind == "redo":
        redo = read_redo(owner, pid, gen_id, variant)
        rev = next((r for r in redo["revisions"] if int(r.get("r") or 0) == int(source.get("r") or 0)), None)
        return copy.deepcopy((rev or {}).get("pieces") or [])
    if kind == "export":
        out, clock = [], 0.0
        for clip in source.get("clips") or []:
            seconds = edit._clip_seconds(clip)  # noqa: SLF001
            if seconds <= 0:
                continue
            out.append({"t0": _r3(clock), "t1": _r3(clock + seconds), "sourceKey": str(clip.get("sourceKey") or ""),
                        "in0": float(clip.get("in") or 0.0), "rate": edit._speed_of(clip), "cuts": []})  # noqa: SLF001
            clock += seconds
        return out
    mode, cells = edit._cells(gen)  # noqa: SLF001
    weights = sound._cut_weights(plan)  # noqa: SLF001
    rows = []
    for cell in cells:
        state = cell.get(variant) if isinstance(cell, dict) and isinstance(cell.get(variant), dict) else {}
        if state.get("status") != "completed" or not state.get("outputKey"):
            continue
        length = edit._num(cell.get("seconds")) or 0.0  # noqa: SLF001
        if mode == "previz" and state.get("padded"):
            orig = edit._num(state.get("origSeconds"))  # noqa: SLF001
            length = orig if orig and orig > 0 else length
        ids = [str(c) for c in cell.get("cutIds") or []] or ([str(cell["cutId"])] if cell.get("cutId") else [])
        if length > 0:
            rows.append((length, ids, str(state["outputKey"])))
    total = sum(r[0] for r in rows)
    if not rows or total <= 0 or duration <= 0:
        return []
    scale = duration / total
    out, clock = [], 0.0
    for length, ids, key in rows:
        t0, t1 = clock * scale, (clock + length) * scale
        cuts = [[_r3(a), _r3(b), c] for a, b, c in sound._split_by_cuts(t0, t1, ids, weights)] if ids else []  # noqa: SLF001
        out.append({"t0": _r3(t0), "t1": _r3(t1), "sourceKey": key, "in0": 0.0, "rate": 1.0 / scale, "cuts": cuts})
        clock += length
    return out


def _replace_piece(pieces: list[dict], rng: dict, new_key: str) -> list[dict]:
    """판의 조각: [start, end] 를 새 결과(속도 1)로 바꾸고 걸친 조각은 앞·뒤로 잘라 재료 초를 옮긴다.
    fix3: clipStart(새 결과 0초의 타임라인 시각 — 늘린 구간 시작)가 있으면 새 조각 재료 초 = start − clipStart, 없으면 0."""
    a, b = float(rng["start"]), float(rng["end"])
    clip_in = max(0.0, a - float(rng["clipStart"])) if isinstance(rng.get("clipStart"), (int, float)) else 0.0
    out: list[dict] = [{"t0": _r3(a), "t1": _r3(b), "sourceKey": new_key, "in0": _r3(clip_in), "rate": 1.0, "cuts": []}]
    for piece in pieces:
        t0, t1 = float(piece["t0"]), float(piece["t1"])
        if t1 <= a or t0 >= b:  # 바꾼 구간 밖
            out.append(copy.deepcopy(piece))
            continue
        rate, in0 = float(piece.get("rate") or 1.0), float(piece.get("in0") or 0.0)
        cuts = piece.get("cuts") or []
        if t0 < a:
            out.append(dict(piece, t1=_r3(a), cuts=[c for c in cuts if float(c[0]) < a]))
        if t1 > b:
            out.append(dict(piece, t0=_r3(b), in0=_r3(in0 + (b - t0) * rate), cuts=[c for c in cuts if float(c[1]) > b]))
    return sorted(out, key=lambda p: (float(p["t0"]), float(p["t1"])))


# ================================================================ 보기


def _parse(value) -> datetime | None:
    return plan_mod._parse_iso(str(value or ""))  # noqa: SLF001


def _job_out(job: dict | None) -> dict | None:
    if not isinstance(job, dict):
        return None
    out = {k: job.get(k) for k in ("status", "error", "at", "usd", "model", "modelLabel", "seconds", "range", "revision",
                                   "message", "priceKnown", "stage", "failureKind", "stepUp")}
    out["error"] = str(out["error"] or "")
    out["models"] = copy.deepcopy(job.get("models") or [])  # 작업 212 fix2: 확인 창의 모델 고르기(싼 순서, 금액)
    return out


def _override_out(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    return {k: copy.deepcopy(item.get(k)) for k in ("lineId", "textKo", "speaker", "thought", "deliveryKo", "at", "original",
                                                     "scriptApplied")}


def _cast_names(plan: dict) -> list[str]:
    names: list[str] = []
    for entry in ((plan or {}).get("script") or {}).get("entries") or []:
        name = clean_text((entry or {}).get("speaker") if isinstance(entry, dict) else "", sound.MAX_SPEAKER_LEN)
        if name and name not in names:
            names.append(name)
    for cut in ((plan or {}).get("conti") or {}).get("cuts") or []:
        for person in (cut or {}).get("characters") or [] if isinstance(cut, dict) else []:
            name = clean_text((person or {}).get("name") if isinstance(person, dict) else "", sound.MAX_SPEAKER_LEN)
            if name and name not in names:
                names.append(name)
    return names


def _refresh(owner: str, pid: str, gen_id: str, variant: str, redo: dict) -> dict:
    """진행 중 작업을 retake 상태로 맞춘다(⑥ GET 폴링마다). 결과가 나왔으면 잇기 스레드를 띄운다(돈 0)."""
    live = {k: j for k, j in redo["jobs"].items() if j.get("status") in ("running", "splicing")}
    if not live:
        return redo
    try:
        state = _retake_state(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다
        log.warning("vnew.sound.lines.retake_state_failed pid=%s err=%s", pid, type(exc).__name__)
        return redo
    now = datetime.now(timezone.utc)
    spawn: list[tuple[str, str]] = []

    def mutate(doc):
        changed = False
        for line_id, job in doc["jobs"].items():
            if job.get("status") == "splicing":
                beat = _parse(job.get("beatAt"))
                if not _alive_here(job.get("claim")) and (not beat or (now - beat).total_seconds() > SPLICE_STALL_S):
                    job.update(status="running", claim="", stage="")  # 잇기가 끊겼다 → 다음 조회가 다시 잇는다(돈 0)
                    changed = True
                continue
            if job.get("status") != "running":
                continue
            version = retake._find_version(state, str(job.get("cellKey") or ""), job.get("n")) or {}  # noqa: SLF001
            if not version and job.get("n") is None:  # 작업 219: 제출 응답에 번호가 없었다 → 초안으로 찾고 번호를 적는다
                version = _submitted_version(owner, pid, gen_id, str(job.get("cellKey") or ""), str(job.get("draftId") or ""),
                                             state=state) or {}
                if isinstance(version.get("n"), int) and not isinstance(version.get("n"), bool):
                    job["n"] = version["n"]
                    changed = True
            status = str(version.get("status") or "")
            started = _parse(job.get("startedAt"))
            if not version and started and (now - started).total_seconds() > ORPHAN_S:
                # 작업 220: retake 상태에 이 작업의 버전이 끝내 없다(번호 · 초안 모두) — 아무도 결과를 만들지 않으니 running 에 묶지 않는다
                job.update(status="failed", error=ORPHAN_TEXT, at=now_iso(), failureKind="orphan", stage="")
                log.warning("vnew.sound.lines.redo_orphan pid=%s line=%s cell=%s n=%s", pid, line_id, job.get("cellKey"), job.get("n"))
                changed = True
                continue
            if status == "completed" and version.get("outputKey"):
                claim = secrets.token_hex(8)
                job.update(status="splicing", claim=claim, beatAt=now_iso(), stage="splice", outputKey=str(version["outputKey"]))
                spawn.append((line_id, claim))
                changed = True
            elif status in ("failed", "cancelled"):
                job.update(status="failed", error=str(version.get("error") or "영상을 다시 만들지 못했습니다."), at=now_iso(),
                           failureKind=str(version.get("failureKind") or ""), stage="")
                changed = True
            elif status and status != job.get("stage"):
                job["stage"] = status
                changed = True
        return doc if changed else None

    saved = _mutate_redo(owner, pid, gen_id, variant, mutate)
    for line_id, claim in spawn:
        _spawn(run_splice, (owner, pid, gen_id, variant, line_id, claim), f"vnew-line-splice-{pid}-{variant}")
    return read_redo(owner, pid, gen_id, variant) if spawn or saved else redo


def view(owner: str, pid: str, plan: dict, gen_id: str, variant: str, doc: dict) -> dict:
    """sound_view 의 lineRedo: 줄마다 고침·작업, 판 목록·지금 판, 말하는 사람 목록, 안내 글."""
    redo = _refresh(owner, pid, gen_id, variant, read_redo(owner, pid, gen_id, variant))
    overrides = read_overrides(owner, pid, gen_id)
    src = (doc or {}).get("source") or {}
    base_key = str(src.get("baseKey") or src.get("key") or "")
    revisions = [{"r": int(r.get("r") or 0), "at": str(r.get("at") or ""), "lineId": str(r.get("lineId") or ""),
                  "range": r.get("range"), "model": r.get("model"), "usd": r.get("usd"),
                  "current": int(r.get("r") or 0) == redo["current"] and src.get("kind") == "redo",
                  "videoUrl": edit._presign_video(str(r.get("key") or ""))}  # noqa: SLF001
                 for r in redo["revisions"] if r.get("baseKey") == base_key]
    lines = {}
    for line in (doc or {}).get("lines") or []:
        line_id = str((line or {}).get("id") or "")
        item = {"override": _override_out(overrides.get(line_id)), "job": _job_out(redo["jobs"].get(line_id))}
        if item["override"] or item["job"]:
            lines[line_id] = item
    busy = any(j.get("status") in ("running", "splicing") for j in redo["jobs"].values())
    voice_outdated = any(isinstance(v, dict) and v.get("outdated") for v in (doc or {}).get("versions") or [])
    return {"enabled": True, "lines": lines, "revisions": revisions, "current": redo["current"] if src.get("kind") == "redo" else 0,
            "busy": busy, "cast": _cast_names(plan), "minSeconds": MIN_SEGMENT_S,
            "limits": {"textMax": TEXT_MAX, "deliveryMax": DELIVERY_MAX},
            "voiceOutdated": voice_outdated, "outdatedText": OUTDATED_TEXT, "takeRedoText": TAKE_REDO_TEXT}


def status_view(owner: str, pid: str, variant=None) -> dict:
    """GET /p/<pid>/sound/lines/status — 사운드 보기 전체(⑥ 폴링과 같은 결과)."""
    return sound.sound_view(owner, pid, variant)


# ================================================================ 대사 고침 저장


def _script_line(plan: dict, line_id: str, where: str) -> dict:
    for line in sound._script_lines(plan):  # noqa: SLF001
        if line["id"] == line_id:
            return line
    raise VpocError(where, "BadLine", "고칠 대사 줄을 찾을 수 없습니다. 다시 불러온 뒤 고쳐 주세요.", 400)


def _read_override_body(payload: dict, plan: dict, where: str) -> dict:
    text = clean_text(payload.get("textKo"), TEXT_MAX + 1)
    if generate._blank_line(text):  # noqa: SLF001
        raise VpocError(where, "BadLineText", "새 대사를 적어 주세요.", 400)
    if len(text) > TEXT_MAX:
        raise VpocError(where, "BadLineText", f"새 대사는 {TEXT_MAX}자까지 적을 수 있습니다.", 400)
    speaker = clean_text(payload.get("speaker"), sound.MAX_SPEAKER_LEN)
    cast = _cast_names(plan)
    if speaker and speaker not in cast:
        raise VpocError(where, "BadSpeaker", "말하는 사람은 등장인물 목록에서 골라 주세요.", 400)
    raw_thought = payload.get("thought")
    if raw_thought is None and payload.get("voice") in prompts.VOICE_VALUES:
        raw_thought = payload.get("voice") == "thought"
    if not isinstance(raw_thought, bool):
        raise VpocError(where, "BadVoice", "실제 말인지 속마음인지 골라 주세요.", 400)
    delivery = clean_text(payload.get("deliveryKo"), DELIVERY_MAX + 1)
    if len(delivery) > DELIVERY_MAX:
        raise VpocError(where, "BadDelivery", f"말투 메모는 {DELIVERY_MAX}자까지 적을 수 있습니다.", 400)
    return {"textKo": text, "speaker": speaker, "thought": raw_thought, "deliveryKo": delivery}


def save_override(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/sound/lines/override {variant?, lineId, textKo, speaker, thought, deliveryKo?, applyToScript?}. 반환 사운드 보기.

    대본은 applyToScript true 일 때만 바꾼다(그 줄 lineKo·speaker·voice, plan.revision + 1). 처음 고칠 때의 대본 줄을 original 로 지킨다."""
    where = "vnew.sound.lines.override"
    payload = payload if isinstance(payload, dict) else {}
    line_id = clean_text(str(payload.get("lineId") or ""), sound.MAX_ID_LEN)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(payload.get("variant"), gen, where)  # noqa: SLF001
    line = _script_line(plan, line_id, where)
    body = _read_override_body(payload, plan, where)
    apply_script = payload.get("applyToScript") is True
    stamp = now_iso()

    def mutate(current):
        data = current if isinstance(current, dict) else {"v": 1, "overrides": {}}
        items = data.setdefault("overrides", {})
        old = items.get(line_id) if isinstance(items.get(line_id), dict) else {}
        original = old.get("original") or {"lineKo": line["lineKo"], "speaker": line["speaker"], "voice": line["voice"]}
        item = {"lineId": line_id, **body, "at": stamp, "original": original,
                "scriptApplied": bool(apply_script or (old.get("scriptApplied") and old.get("textKo") == body["textKo"]
                                                        and old.get("speaker") == body["speaker"]
                                                        and old.get("thought") == body["thought"]))}
        if apply_script:
            item["scriptAt"] = stamp
        elif item["scriptApplied"] and old.get("scriptAt"):
            item["scriptAt"] = old["scriptAt"]
        items[line_id] = item
        data["v"] = 1
        return data

    edit._mutate_json(overrides_key(owner, pid, gen_id), mutate)  # noqa: SLF001
    if apply_script:
        apply_to_script(owner, pid, line_id, body)
    log.info("vnew.sound.lines.override pid=%s gen=%s line=%s thought=%s delivery=%s script=%s", pid, gen_id, line_id,
             body["thought"], bool(body["deliveryKo"]), apply_script)
    return sound.sound_view(owner, pid, variant)


def apply_to_script(owner: str, pid: str, line_id: str, body: dict) -> bool:
    """[대본에도 반영]: 그 대본 줄의 lineKo·speaker·voice 만 바꾸고 revision + 1(바뀐 것이 있을 때만). 프리비즈 승인은 그대로."""
    box = {"changed": False}

    def mutator(data: dict) -> None:
        for entry in ((data.get("script") or {}).get("entries") or []):
            if not isinstance(entry, dict) or str(entry.get("id") or "") != line_id:
                continue
            voice = "thought" if body["thought"] else "spoken"
            before = (entry.get("lineKo"), entry.get("speaker"), "thought" if prompts.is_thought(entry) else "spoken")
            if before == (body["textKo"], body["speaker"] or entry.get("speaker"), voice):
                return
            entry["lineKo"] = body["textKo"]
            if body["speaker"]:
                entry["speaker"] = body["speaker"]
            entry["voice"] = voice
            if voice == "spoken" and entry.get("thought") is True:
                entry["thought"] = False  # prompts.is_thought 가 thought True 도 보므로 같이 내린다(plan.apply_voices 와 같다)
            data["revision"] = int(data.get("revision") or 0) + 1
            box["changed"] = True
            return

    plan_mod.mutate_plan(owner, pid, mutator)
    return box["changed"]


# ================================================================ AI 로 다시 쓰기


def _suggest_blocks(plan: dict, line: dict, override: dict | None, hint: str) -> list[dict]:
    lines = sound._script_lines(plan)  # noqa: SLF001
    at = next((i for i, l in enumerate(lines) if l["id"] == line["id"]), 0)
    near = lines[max(0, at - SUGGEST_CONTEXT_LINES): at + SUGGEST_CONTEXT_LINES + 1]
    cut = None
    for item in ((plan.get("conti") or {}).get("cuts") or []):
        panels = {int(p) for p in (item or {}).get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool)}
        if panels & line["panels"]:
            cut = item
            break
    brief = (((plan.get("previz") or {}).get("shotBrief") or {}).get("cuts") or {}).get(str((cut or {}).get("id") or "")) or {}
    rows = [f"- {'▶ ' if l['id'] == line['id'] else ''}{l['speaker'] or '(이름 없음)'}"
            f"{' (속마음)' if l['voice'] == 'thought' else ''}: {l['lineKo']}" for l in near]
    parts = [
        "[장면]",
        f"상황: {clean_text((cut or {}).get('situationKo'), 300) or '-'}",
        f"촬영: {clean_text(brief.get('actionEn') or brief.get('shotEn') or (cut or {}).get('shotKo'), 300) or '-'}",
        f"등장인물: {', '.join(_cast_names(plan)) or '-'}",
        "[대본 줄(▶ = 고칠 줄)]", *rows,
        f"[고칠 줄] 말하는 사람: {(override or {}).get('speaker') or line['speaker'] or '-'} · "
        f"{'속마음' if (override or {}).get('thought', line['voice'] == 'thought') else '실제 말'} · 지금 대사: {line['lineKo']}",
    ]
    if hint:
        parts.append(f"[사용자 요청] {hint}")
    return [{"text": "\n".join(parts)}]


def suggest(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/lines/suggest {variant?, lineId, hintKo?, speaker?, thought?}. 텍스트 LLM 1회(지시서와 같은 싼 모델 →
    실패하면 대체 모델), 원장 1건. 반환 {lines: [새 대사 3줄], model}."""
    where = "vnew.sound.lines.suggest"
    payload = payload if isinstance(payload, dict) else {}
    line_id = clean_text(str(payload.get("lineId") or ""), sound.MAX_ID_LEN)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    line = _script_line(plan, line_id, where)
    hint = clean_text(payload.get("hintKo"), DELIVERY_MAX)
    override = read_overrides(owner, pid, gen_id).get(line_id)
    if isinstance(payload.get("speaker"), str) or isinstance(payload.get("thought"), bool):
        override = dict(override or {}, speaker=clean_text(payload.get("speaker"), sound.MAX_SPEAKER_LEN) or line["speaker"],
                        thought=bool(payload.get("thought")))
    blocks = _suggest_blocks(plan, line, override, hint)
    for model in dict.fromkeys((vnew_settings.BRIEF_MODEL, vnew_settings.BRIEF_FALLBACK_MODEL)):
        try:
            result, usage = _llm(system_text=_SUGGEST_SYSTEM, blocks=blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.sound.lines.suggest_failed model=%s err=%s", model, error_gist(f"{type(exc).__name__}: {exc}", 200))
            continue
        used = str((usage or {}).get("model") or model)
        _charge(owner, pid, gen_id, usage or {}, used)
        raw = (result or {}).get("lines") if isinstance(result, dict) else None
        out: list[str] = []
        for item in raw if isinstance(raw, list) else []:
            text = clean_text(str(item or "").strip().strip("\"“”「」"), TEXT_MAX)
            if text and not generate._blank_line(text) and text not in out:  # noqa: SLF001
                out.append(text)
        if out:
            log.info("vnew.sound.lines.suggest pid=%s line=%s model=%s count=%d", pid, line_id, used, len(out))
            return {"lines": out[:SUGGEST_COUNT], "model": used}
    raise VpocError(where, "SuggestFailed", "새 대사를 만들지 못했습니다. 잠시 뒤 다시 눌러 주세요.", 502)


def _charge(owner: str, pid: str, gen_id: str, usage: dict, model: str) -> None:
    try:
        input_tokens, output_tokens = int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0)
        if input_tokens or output_tokens:
            vpoc_ledger.charge_text("vnew-line-suggest-" + secrets.token_hex(6), model=model, input_tokens=input_tokens,
                                    output_tokens=output_tokens, user=owner, project_id=pid, plan_id=gen_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.sound.lines.charge_failed err=%s", type(exc).__name__)


# ================================================================ 견적 · 시작


def _context(owner: str, pid: str, payload: dict, where: str):
    payload = payload if isinstance(payload, dict) else {}
    line_id = clean_text(str(payload.get("lineId") or ""), sound.MAX_ID_LEN)
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(payload.get("variant"), gen, where)  # noqa: SLF001
    doc, source = sound._current_doc(owner, pid, gen_id, gen, variant, where)  # noqa: SLF001
    line = next((l for l in doc.get("lines") or [] if isinstance(l, dict) and l.get("id") == line_id), None)
    if not line:
        raise VpocError(where, "BadLine", "고칠 대사 줄을 찾을 수 없습니다. 다시 불러온 뒤 고쳐 주세요.", 400)
    return payload, line_id, plan, gen, gen_id, variant, doc, source, line


def quote(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/lines/quote {variant, lineId, model?}. 구간을 정하고 retake.compose 로 초안(프롬프트·견적)을 만든다(영상 호출 0).
    반환 {quote: 작업 보기, prompt}. 고친 대사가 없으면 409 LineNoOverride, 그 줄이 다시 만드는 중이면 409 LineRedoRunning.
    작업 212 fix2: model 이 없으면 default_model(가장 싼 영상→영상 모델 · 같으면 원래 모델 · Seedance 는 다른 모델이 없을 때만 ·
    콘텐츠 검사로 거절한 모델은 빼고 다음으로 싼 모델 — stepUp). 보기 models = 고를 수 있는 줄(싼 순서, 고른 줄 금액 = compose 견적)."""
    where = "vnew.sound.lines.quote"
    payload, line_id, plan, gen, gen_id, variant, doc, source, line = _context(owner, pid, payload, where)
    retake.require_enabled(where)
    override = read_overrides(owner, pid, gen_id).get(line_id)
    if not override:
        raise VpocError(where, "LineNoOverride", "먼저 [대사 고치기]로 새 대사를 저장해 주세요.", 409)
    job_now = read_redo(owner, pid, gen_id, variant)["jobs"].get(line_id) or {}
    job_now = _adopt_submitted(owner, pid, gen_id, variant, line_id, job_now) or job_now  # 작업 219: 이미 나간 초안 → running
    if job_now.get("status") in ("running", "splicing"):
        raise VpocError(where, "LineRedoRunning", "이 대사 영상을 다시 만드는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
    duration = edit._num(doc["source"].get("duration")) or 0.0  # noqa: SLF001
    pieces = _pieces(owner, pid, gen_id, plan, gen, variant, source, duration)
    line_start, line_end = float(line.get("start") or 0.0), float(line.get("end") or 0.0)
    scenes, scene_ok = _quote_scenes(source["key"], line_start, line_end, duration)
    rng = line_range(line_start, line_end, pieces, scenes=scenes)
    rng["scene"] = scene_ok
    mid = edit.media_id(rng["sourceKey"])
    request_ko = redo_request(override)
    extra_ko = ""  # 작업 212 fix1: 새 대사·유지·소리 규칙은 EDIT 줄들(redo_edit_lines)에 있다 — EXTRA 줄은 없다
    body = {"requestKo": request_ko, "extraKo": extra_ko, "scope": "range", "in": rng["in"], "out": rng["out"]}
    picked = payload.get("model") if isinstance(payload.get("model"), str) and payload["model"] else ""
    try:
        rows = _models(owner, pid, mid, rng)
    except Exception as exc:  # noqa: BLE001 — 목록을 못 읽으면 compose 가 제 기본 모델로(같은 오류면 compose 가 올린다)
        log.warning("vnew.sound.lines.models_failed pid=%s err=%s", pid, error_gist(f"{type(exc).__name__}: {exc}", 200))
        rows = []
    # fix2: 콘텐츠 검사로 거절한 모델(같은 새 대사일 때만 기억) — 다음 기본은 다음으로 싼 모델, 금액은 확인을 받는다(start 의 confirmUsd)
    same_line = job_now.get("textKo") == override.get("textKo") and bool(job_now.get("thought")) == bool(override.get("thought"))
    refused = [str(m) for m in job_now.get("refused") or [] if m] if same_line else []
    if same_line and job_now.get("status") == "failed" and job_now.get("failureKind") == retake.CONTENT_FLAG_KIND and job_now.get("model"):
        refused = list(dict.fromkeys(refused + [str(job_now["model"])]))
    model = picked or default_model(rows, refused)
    if model:
        body["model"] = model
    setting = _line_setting(plan, pieces, rng, variant == "real")  # 작업 223
    rv = _compose(owner, pid, mid, body,
                  edit_lines=lambda change_en, max_bytes: redo_edit_lines(override, change_en, max_bytes, rng["cutsAt"],
                                                                          anime=variant != "real", setting=setting))  # 작업 222 · 223
    draft = (rv or {}).get("draft") or {}
    info = draft.get("plan") or {}
    used = str(draft.get("model") or "")
    choices = [{k: v for k, v in r.items() if k != "format"} for r in model_rows(rows)]
    for row in choices:
        if row["id"] == used:
            row["usd"] = info.get("usd")  # 보이는 금액 = 낼 금액(compose 견적)
    step_up = None
    if refused and not picked and used and used not in refused:
        labels = {r["id"]: r["label"] for r in choices}
        step_up = {"fromModel": refused[-1], "fromLabel": labels.get(refused[-1], refused[-1])}
    original = (override.get("original") or {}).get("lineKo") or ""
    prompt = _finish_prompt(str(draft.get("prompt") or ""), original, override.get("textKo"), int(draft.get("maxBytes") or 0))
    job = {"status": "quoted", "lineId": line_id, "draftId": str(draft.get("id") or ""), "prompt": prompt, "requestKo": request_ko,
           "extraKo": extra_ko, "promptV": PROMPT_V, "mid": mid, "in": rng["in"], "out": rng["out"], "model": str(draft.get("model") or ""),
           "modelLabel": str(draft.get("modelLabel") or draft.get("model") or ""), "usd": info.get("usd"),
           "priceKnown": info.get("usd") is not None, "seconds": info.get("sentSeconds") or info.get("seconds"),
           "range": {"start": rng["start"], "end": rng["end"], "padded": rng["padded"], "snapped": rng["snapped"],
                     **{k: rng[k] for k in ("line", "shot", "cuts", "cutsAt", "splice", "scene")}},
           "sourceKey": source["key"], "baseKey": _base_key(source), "pieces": pieces, "cellKey": str((rv or {}).get("cellKey") or ""),
           "at": now_iso(), "error": "", "textKo": override.get("textKo"), "thought": bool(override.get("thought")),
           "models": choices, "refused": refused, "stepUp": step_up, "picked": bool(picked)}

    def mutate(doc_):
        if (doc_["jobs"].get(line_id) or {}).get("status") in ("running", "splicing"):
            raise VpocError(where, "LineRedoRunning", "이 대사 영상을 다시 만드는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        doc_["jobs"][line_id] = job
        return doc_

    _mutate_redo(owner, pid, gen_id, variant, mutate)
    log.info("vnew.sound.lines.quote pid=%s gen=%s variant=%s line=%s start=%.3f end=%.3f in=%.3f out=%.3f splice=%s cuts=%s scene=%s "
             "usd=%s model=%s picked=%s refused=%d step_up=%s", pid, gen_id, variant, line_id, rng["start"], rng["end"], rng["in"],
             rng["out"], rng["splice"], rng["cuts"], scene_ok, job["usd"], job["model"], bool(picked), len(refused), bool(step_up))
    return {"quote": _job_out(job), "prompt": prompt}


def _quote_scenes(key: str, start: float, end: float, duration: float) -> tuple[list[float], bool]:
    """견적 때 줄 둘레(늘릴 수 있는 만큼)의 장면 바뀜. 실패하면 ([], False) — 콘티 경계만 쓴다(잇기 때 로컬 파일로 다시 본다)."""
    a = max(0.0, start - MIN_SEGMENT_S - SNAP_S)
    b = end + MIN_SEGMENT_S + SNAP_S
    b = min(b, duration) if duration > 0 else b
    try:
        return _scene_changes(key, a, b), True
    except Exception as exc:  # noqa: BLE001 — 검사는 덤(콘티가 정본)
        log.warning("vnew.sound.lines.scene_failed err=%s", error_gist(f"{type(exc).__name__}: {exc}", 200))
        return [], False


def _plan_splice(owner: str, pid: str, gen_id: str, variant: str, line_id: str, rng: dict, pieces: list[dict], base_path: str,
                 duration: float) -> dict:
    """잇기·다시 잇기 때 잇기 구간(_splice_in): 구간 [start, end] 이 든 조각 + 로컬 원본의 장면 바뀜(실패하면 콘티만).
    줄 시각 = rng.line(없으면 사운드 문서의 그 줄, 그것도 없으면 구간 전체)."""
    lo, hi = float(rng["start"]), float(rng["end"])
    span = rng.get("line") if isinstance(rng.get("line"), list) and len(rng["line"]) == 2 else None
    if span is None:
        doc, _ = edit._read_json(sound.doc_key(owner, pid, gen_id, variant))  # noqa: SLF001
        line = next((l for l in (doc or {}).get("lines") or [] if isinstance(l, dict) and l.get("id") == line_id), None)
        span = [float(line.get("start") or lo), float(line.get("end") or hi)] if line else [lo, hi]
    piece = _best_piece(pieces, lo, hi)
    if piece is None:
        return {"splice": [_r3(lo), _r3(hi)], "cuts": [], "scene": False}
    try:
        scenes, ok = _scenes(base_path, max(0.0, lo - BOUND_EDGE_S), min(duration, hi + BOUND_EDGE_S)), True
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.sound.lines.scene_failed err=%s", error_gist(f"{type(exc).__name__}: {exc}", 200))
        scenes, ok = [], False
    out = _splice_in(lo, hi, float(span[0]), float(span[1]), shot_bounds(piece, scenes), float(piece.get("rate") or 1.0))
    out["scene"] = ok
    return out


def start(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/lines/redo {variant, lineId, confirmUsd}. 견적 초안으로 retake.submit(금액 확인 — 다르면 409 CostMismatch).
    반환 {job}. 견적이 없으면 409 LineRedoNoQuote, 원본이 바뀌었으면 409 SoundChanged."""
    where = "vnew.sound.lines.redo"
    payload, line_id, plan, gen, gen_id, variant, doc, source, line = _context(owner, pid, payload, where)
    retake.require_enabled(where)
    redo = read_redo(owner, pid, gen_id, variant)
    job = redo["jobs"].get(line_id) or {}
    adopted = _adopt_submitted(owner, pid, gen_id, variant, line_id, job)  # 작업 219: 이미 나간 초안이면 다시 내지 않는다
    if adopted is not None:
        return {"job": _job_out(adopted)}
    if job.get("status") in ("running", "splicing"):
        raise VpocError(where, "LineRedoRunning", "이 대사 영상을 다시 만드는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
    if job.get("status") != "quoted" or not job.get("draftId") or job.get("promptV") != PROMPT_V:
        raise VpocError(where, "LineRedoNoQuote", "먼저 금액을 확인해 주세요.", 409)  # fix1: 옛 꼴 견적·실패한 작업은 견적부터 다시
    if job.get("sourceKey") != source["key"]:
        raise VpocError(where, "SoundChanged", "원본 영상이 바뀌었습니다. 다시 불러온 뒤 금액을 다시 확인해 주세요.", 409)
    if len([r for r in redo["revisions"] if r.get("baseKey") == _base_key(source)]) >= REVISIONS_MAX:
        raise VpocError(where, "LineRedoLimit", f"한 영상은 대사를 {REVISIONS_MAX}번까지 다시 만들 수 있습니다.", 409)
    body = {"prompt": job["prompt"], "requestKo": job["requestKo"], "extraKo": job["extraKo"], "confirmUsd": payload.get("confirmUsd"),
            "draftId": job["draftId"], "model": job["model"], "scope": "range", "in": job["in"], "out": job["out"]}
    rv = _submit(owner, pid, job["mid"], body)  # CostMismatch(409) · 규칙 · 키 오류는 그대로 올린다(화면이 견적을 다시 받는다)
    live = (rv or {}).get("live") or {}
    n = live.get("n")
    cell_key = str((rv or {}).get("cellKey") or job.get("cellKey") or "")
    if not isinstance(n, int) or isinstance(n, bool):
        # 작업 219: 제출은 받았다(돈이 나간다) — quoted 로 두지 않는다. retake 상태에서 이 초안의 버전을 찾고, 못 찾으면 n 없이
        # running 으로 둔다(_refresh 가 draftId 로 번호를 찾는다).
        found = _submitted_version(owner, pid, gen_id, cell_key, job["draftId"])
        n = found.get("n") if found else None
        n = n if isinstance(n, int) and not isinstance(n, bool) else None
        log.warning("vnew.sound.lines.redo_no_n pid=%s line=%s cell=%s found=%s", pid, line_id, cell_key, n)

    def mutate(doc_):
        cur = doc_["jobs"].get(line_id) or {}
        cur.update(status="running", n=n, cellKey=cell_key, startedAt=now_iso(),
                   at=now_iso(), error="", stage=str(live.get("status") or "queued"), claim="")
        doc_["jobs"][line_id] = cur
        return doc_

    saved = _mutate_redo_retry(owner, pid, gen_id, variant, mutate, line_id)
    log.info("vnew.sound.lines.redo_start pid=%s gen=%s variant=%s line=%s cell=%s n=%s usd=%s", pid, gen_id, variant, line_id,
             saved["jobs"][line_id].get("cellKey"), n, job.get("usd"))
    return {"job": _job_out(saved["jobs"][line_id])}


def _mutate_redo_retry(owner: str, pid: str, gen_id: str, variant: str, fn, line_id: str) -> dict:
    """작업 219: 제출이 받아진 뒤의 running 쓰기 — 실패하면 몇 번 더 쓴다(quoted 로 남으면 다시 누를 때 두 번 낸다)."""
    last: Exception | None = None
    for attempt in range(1, START_WRITE_TRIES + 1):
        try:
            return _mutate_redo(owner, pid, gen_id, variant, fn)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("vnew.sound.lines.redo_state_retry pid=%s line=%s attempt=%d err=%s", pid, line_id, attempt,
                        type(exc).__name__)
            if attempt < START_WRITE_TRIES:
                time.sleep(START_WRITE_WAIT_S * attempt)
    log.error("vnew.sound.lines.redo_state_lost pid=%s line=%s — 다음 견적 · 시작이 진행 중인 retake 를 이어받는다", pid, line_id)
    raise VpocError("vnew.sound.lines.redo", "LineRedoStateFailed",
                    "다시 만들기는 시작됐지만 상태를 저장하지 못했습니다. 잠시 뒤 새로고침해 주세요(다시 누르지 않아도 됩니다).", 503) from last


def _submitted_version(owner: str, pid: str, gen_id: str, cell_key: str, draft_id: str, state: dict | None = None) -> dict | None:
    """작업 219: 이 초안(draftId)으로 낸 retake 버전 중 진행 중이거나 끝난 것(실패 · 취소는 빼고 — 그건 다시 낼 수 있다). 없으면 None."""
    if not cell_key or not draft_id:
        return None
    try:
        state = state if state is not None else _retake_state(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 이어받지 않는다
        log.warning("vnew.sound.lines.retake_state_failed pid=%s err=%s", pid, type(exc).__name__)
        return None
    versions = ((state.get("cells") or {}).get(cell_key) or {}).get("versions") or []
    for version in reversed(versions):
        if isinstance(version, dict) and version.get("draftId") == draft_id and version.get("status") not in ("failed", "cancelled"):
            return version
    return None


def _adopt_submitted(owner: str, pid: str, gen_id: str, variant: str, line_id: str, job: dict) -> dict | None:
    """작업 219: quoted 작업의 초안이 이미 retake 로 나갔으면(앞 시작이 상태를 못 썼다) 다시 내지 않고 running 으로 이어받는다."""
    if job.get("status") != "quoted":
        return None
    version = _submitted_version(owner, pid, gen_id, str(job.get("cellKey") or ""), str(job.get("draftId") or ""))
    if version is None:
        return None
    n = version.get("n") if isinstance(version.get("n"), int) and not isinstance(version.get("n"), bool) else None

    def mutate(doc_):
        cur = doc_["jobs"].get(line_id) or {}
        if cur.get("status") != "quoted" or cur.get("draftId") != job.get("draftId"):
            return None
        cur.update(status="running", n=n, startedAt=now_iso(), at=now_iso(), error="", stage=str(version.get("status") or "queued"),
                   claim="")
        return doc_

    saved = _mutate_redo(owner, pid, gen_id, variant, mutate)
    log.info("vnew.sound.lines.redo_adopt pid=%s line=%s cell=%s n=%s", pid, line_id, job.get("cellKey"), n)
    return (saved or read_redo(owner, pid, gen_id, variant))["jobs"].get(line_id)


# ================================================================ 잇기(새 판)


def _beat(owner: str, pid: str, gen_id: str, variant: str, line_id: str, claim: str, finish=None) -> dict:
    box: dict = {}

    def mutate(doc):
        job = doc["jobs"].get(line_id)
        if not isinstance(job, dict) or job.get("claim") != claim or job.get("status") != "splicing":
            raise _ClaimLost()
        if finish is None:
            job["beatAt"] = now_iso()
        else:
            finish(doc, job)
        box["job"] = copy.deepcopy(job)
        box["doc"] = copy.deepcopy(doc)
        return doc

    _mutate_redo(owner, pid, gen_id, variant, mutate)
    return box


def _geometry(info: dict) -> tuple[int, int, int]:
    width, height = int(info.get("width") or 0), int(info.get("height") or 0)
    fps = int(round(float(info.get("fps") or 0)))
    if fps not in stitcher.EDIT_FPS_CHOICES:
        fps = min(stitcher.EDIT_FPS_CHOICES, key=lambda f: abs(f - (fps or 24)))
    return width - width % 2, height - height % 2, fps


def run_splice(owner: str, pid: str, gen_id: str, variant: str, line_id: str, claim: str) -> None:
    """잇기 스레드: 지금 원본(같은 기준의 판이면 그 판 — 다른 줄을 먼저 고쳤어도 잃지 않는다) + retake 결과 → 새 판 키에 올리고
    current 를 새 판으로, 목소리 반영 기록은 outdated. 원본·결과·이전 판 파일은 읽기만 한다."""
    stage = "wait"
    try:
        with _SPLICE_SEM:
            job = _beat(owner, pid, gen_id, variant, line_id, claim)["job"]
            _plan, gen, _gid = edit._load_gen(owner, pid)  # noqa: SLF001
            stage = "source"
            now_source = sound._source(owner, pid, gen_id, gen, variant)  # noqa: SLF001
            if _base_key(now_source) != job.get("baseKey"):
                raise VpocError("vnew.sound.lines", "SoundChanged",
                                "원본 영상이 바뀌어 새 대사 영상을 붙이지 못했습니다. 결과는 ⑦ 편집의 버전으로 남아 있습니다.", 409)
            redo = read_redo(owner, pid, gen_id, variant)
            if now_source.get("kind") == "redo":
                rev = next((r for r in redo["revisions"] if int(r.get("r") or 0) == int(now_source["r"])), {})
                pieces = copy.deepcopy(rev.get("pieces") or [])
            else:
                pieces = copy.deepcopy(job.get("pieces") or [])
            output_key = str(job.get("outputKey") or "")
            if not output_key.startswith(f"users/{owner}/video/{pid}/"):
                raise VpocError("vnew.sound.lines", "SourceUnreadable", "새 영상을 읽지 못했습니다.", 409)
            rng = job.get("range") or {}
            with tempfile.TemporaryDirectory(prefix="vnew-line-redo-") as workdir:
                stage = "download"
                base_path, new_path = os.path.join(workdir, "base.mp4"), os.path.join(workdir, "new.mp4")
                _download(now_source["key"], base_path)
                _download(output_key, new_path)
                stage = "probe"
                base_info, new_info = _probe(base_path) or {}, _probe(new_path) or {}
                duration = float(base_info.get("duration") or 0)
                width, height, fps = _geometry(base_info)
                if duration <= 0 or width < 2 or height < 2 or float(new_info.get("duration") or 0) <= 0:
                    raise stitcher.StitchError("영상 길이·크기를 읽지 못했습니다.")
                stage = "splice"
                out_path = os.path.join(workdir, "spliced.mp4")
                new_audio = bool(new_info.get("hasAudio"))
                splice = rng.get("splice")
                if not (isinstance(splice, list) and len(splice) == 2) or not rng.get("scene"):
                    # fix3: 옛 견적(잇기 구간 없음)이거나 견적 때 장면 검사를 못 했으면 받은 원본으로 다시 정한다(콘티가 정본)
                    splice = _plan_splice(owner, pid, gen_id, variant, line_id, rng, job.get("pieces") or pieces, base_path,
                                          duration)["splice"]
                splice = [float(splice[0]), float(splice[1])]
                _splice_file(base_path, new_path, out_path, start=float(rng["start"]), end=float(rng["end"]), duration=duration,
                             width=width, height=height, fps=fps, base_audio=bool(base_info.get("hasAudio")), new_audio=new_audio,
                             splice_start=splice[0], splice_end=splice[1])
                seconds = float((_probe(out_path) or {}).get("duration") or 0)
                if seconds <= 0:
                    raise stitcher.StitchError("이은 영상 길이를 읽지 못했습니다.")
                _beat(owner, pid, gen_id, variant, line_id, claim)
                stage = "upload"
                r = max([int(x.get("r") or 0) for x in redo["revisions"]] + [0]) + 1
                key = revision_key(owner, pid, gen_id, variant, r, secrets.token_hex(3))  # 작업 219: 판마다 고유한 키(파드 경쟁)
                _upload(out_path, key)
        new_pieces = _replace_piece(pieces, {"start": splice[0], "end": splice[1], "clipStart": float(rng["start"])}, output_key)
        from_r = int(now_source.get("r") or 0) if now_source.get("kind") == "redo" else 0

        def finish(doc, cur):
            if any(int(x.get("r") or 0) == r for x in doc["revisions"]):
                raise VpocError("vnew.sound.lines", "LineRedoRace", "다른 작업이 먼저 새 판을 만들었습니다.", 409)
            doc["revisions"].append({"r": r, "key": key, "at": now_iso(), "baseKey": job.get("baseKey"),
                                     "fromKey": now_source["key"], "fromR": from_r, "lineId": line_id,
                                     "range": {"start": rng.get("start"), "end": rng.get("end"), "splice": splice,
                                               "line": rng.get("line")}, "clipKey": output_key, "pieces": new_pieces,
                                     "retake": {"cellKey": cur.get("cellKey"), "n": cur.get("n")}, "model": cur.get("model"),
                                     "usd": cur.get("usd"), "seconds": round(seconds, 3)})
            doc["current"] = r
            cur.update(status="done", revision=r, at=now_iso(), error="", claim="", stage="",
                       message="" if new_audio else NO_AUDIO_TEXT)

        _beat(owner, pid, gen_id, variant, line_id, claim, finish=finish)
        outdated = _mark_versions(owner, pid, gen_id, variant, key)
        log.info("vnew.sound.lines.redo_done pid=%s gen=%s variant=%s line=%s r=%d from_r=%d seconds=%.3f audio=%s outdated=%d",
                 pid, gen_id, variant, line_id, r, from_r, seconds, new_audio, outdated)
    except _ClaimLost:
        log.info("vnew.sound.lines.splice_claim_lost pid=%s variant=%s line=%s stage=%s", pid, variant, line_id, stage)
    except Exception as exc:  # noqa: BLE001
        message = exc.message if isinstance(exc, VpocError) else "새 대사 영상을 원본에 잇지 못했습니다. 다시 시도해 주세요."
        log.warning("vnew.sound.lines.splice_failed pid=%s variant=%s line=%s stage=%s err=%s", pid, variant, line_id, stage,
                    error_gist(f"{type(exc).__name__}: {exc}", 300))

        def failed(_doc, cur):
            cur.update(status="failed", error=message, at=now_iso(), claim="", stage="")

        try:
            _beat(owner, pid, gen_id, variant, line_id, claim, finish=failed)
        except _ClaimLost:
            pass
        except Exception as write_exc:  # noqa: BLE001
            log.warning("vnew.sound.lines.splice_state_failed pid=%s err=%s", pid, type(write_exc).__name__)


def _revision_clip(rev: dict) -> str:
    """판의 retake 결과 키: clipKey(fix3) → 옛 판은 구간 [start, end] 을 그대로 덮은 조각(재료 0초부터)."""
    if rev.get("clipKey"):
        return str(rev["clipKey"])
    rng = rev.get("range") or {}
    for piece in rev.get("pieces") or []:
        if (isinstance(piece, dict) and abs(float(piece.get("t0", -1)) - float(rng.get("start", -2))) < 1e-3
                and abs(float(piece.get("t1", -1)) - float(rng.get("end", -2))) < 1e-3 and not float(piece.get("in0") or 0)):
            return str(piece.get("sourceKey") or "")
    return ""


def rebuild_revision(owner: str, pid: str, variant: str, n: int) -> dict:
    """작업 212 fix3 데이터 고침(라우트 없음 — 파드에서 부른다): 판 n 을 그 판의 저장된 retake 결과(clip)로 새 잇기 규칙에 맞춰 다시 이어
    새 판(새 키 r = 최대 + 1)으로 두고 current 를 그 판으로. 같은 원본(fromKey)·같은 구간 · 줄의 샷 안(splice)만 결과로, 나머지는 원래
    프레임·소리. 모델·유료 호출 0(usd 0, rebuiltFrom = n). 판 n · 원본 파일은 읽기만 한다(되돌리기로 판 n 에 돌아갈 수 있다).
    반환 {r, key, fromR, range, splice, cuts, seconds, scene}."""
    where = "vnew.sound.lines.rebuild"
    plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(variant, gen, where)  # noqa: SLF001
    redo = read_redo(owner, pid, gen_id, variant)
    rev = next((x for x in redo["revisions"] if int(x.get("r") or 0) == int(n)), None)
    if not rev:
        raise VpocError(where, "BadRevision", f"판 {n} 을 찾을 수 없습니다.", 404)
    if any(j.get("status") == "splicing" for j in redo["jobs"].values()):
        raise VpocError(where, "LineRedoRunning", "새 대사 영상을 잇는 중입니다. 끝난 뒤 다시 실행해 주세요.", 409)
    prefix = f"users/{owner}/video/{pid}/"
    clip_key, from_key = _revision_clip(rev), str(rev.get("fromKey") or "")
    if not clip_key.startswith(prefix) or not from_key.startswith(prefix):
        raise VpocError(where, "SourceUnreadable", "판의 원본·결과 영상 키를 찾지 못했습니다.", 409)
    from_r = int(rev.get("fromR") or 0)
    rng = dict(rev.get("range") or {})
    with _SPLICE_SEM, tempfile.TemporaryDirectory(prefix="vnew-line-rebuild-") as workdir:
        base_path, new_path = os.path.join(workdir, "base.mp4"), os.path.join(workdir, "new.mp4")
        _download(from_key, base_path)
        _download(clip_key, new_path)
        base_info, new_info = _probe(base_path) or {}, _probe(new_path) or {}
        duration = float(base_info.get("duration") or 0)
        width, height, fps = _geometry(base_info)
        if duration <= 0 or width < 2 or height < 2 or float(new_info.get("duration") or 0) <= 0:
            raise stitcher.StitchError("영상 길이·크기를 읽지 못했습니다.")
        if from_r:
            prev = next((x for x in redo["revisions"] if int(x.get("r") or 0) == from_r), {})
            pieces = copy.deepcopy(prev.get("pieces") or [])
        else:
            base = sound._base_source(owner, pid, gen_id, gen, variant)  # noqa: SLF001
            if base["key"] != from_key:
                raise VpocError(where, "SoundChanged", "판 n 의 원본이 지금 기준 영상이 아닙니다.", 409)
            pieces = _pieces(owner, pid, gen_id, plan, gen, variant, base, duration)
        plan_ = _plan_splice(owner, pid, gen_id, variant, str(rev.get("lineId") or ""), rng, pieces, base_path, duration)
        splice = [float(v) for v in plan_["splice"]]
        out_path = os.path.join(workdir, "spliced.mp4")
        new_audio = bool(new_info.get("hasAudio"))
        _splice_file(base_path, new_path, out_path, start=float(rng["start"]), end=float(rng["end"]), duration=duration, width=width,
                     height=height, fps=fps, base_audio=bool(base_info.get("hasAudio")), new_audio=new_audio,
                     splice_start=splice[0], splice_end=splice[1])
        seconds = float((_probe(out_path) or {}).get("duration") or 0)
        if seconds <= 0:
            raise stitcher.StitchError("이은 영상 길이를 읽지 못했습니다.")
        r = max([int(x.get("r") or 0) for x in redo["revisions"]] + [0]) + 1
        key = revision_key(owner, pid, gen_id, variant, r, secrets.token_hex(3))  # 작업 219: 판마다 고유한 키(파드 경쟁)
        _upload(out_path, key)
    new_pieces = _replace_piece(pieces, {"start": splice[0], "end": splice[1], "clipStart": float(rng["start"])}, clip_key)

    def mutate(doc):
        if any(int(x.get("r") or 0) == r for x in doc["revisions"]):
            raise VpocError(where, "LineRedoRace", "다른 작업이 먼저 새 판을 만들었습니다.", 409)
        doc["revisions"].append({"r": r, "key": key, "at": now_iso(), "baseKey": rev.get("baseKey"), "fromKey": from_key, "fromR": from_r,
                                 "lineId": rev.get("lineId"), "range": {"start": rng.get("start"), "end": rng.get("end"),
                                                                        "splice": splice, "line": plan_.get("line")},
                                 "clipKey": clip_key, "pieces": new_pieces, "retake": copy.deepcopy(rev.get("retake")),
                                 "model": rev.get("model"), "usd": 0.0, "rebuiltFrom": int(n), "seconds": round(seconds, 3)})
        doc["current"] = r
        return doc

    _mutate_redo(owner, pid, gen_id, variant, mutate)
    outdated = _mark_versions(owner, pid, gen_id, variant, key)
    log.info("vnew.sound.lines.rebuild pid=%s gen=%s variant=%s from=%d r=%d range=%s-%s splice=%s cuts=%s seconds=%.3f outdated=%d",
             pid, gen_id, variant, int(n), r, rng.get("start"), rng.get("end"), splice, plan_.get("cuts"), seconds, outdated)
    return {"r": r, "key": key, "fromR": from_r, "range": [rng.get("start"), rng.get("end")], "splice": splice,
            "cuts": plan_.get("cuts"), "seconds": round(seconds, 3), "scene": plan_.get("scene")}


# ================================================================ 되돌리기


def revert(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/sound/lines/revert {variant, r}. r = 0 이면 기준(원래 영상), 아니면 같은 기준의 판. 파일은 지우지 않는다.
    목소리 반영 기록의 outdated 표시를 그 원본에 맞춘다. 반환 사운드 보기."""
    where = "vnew.sound.lines.revert"
    payload = payload if isinstance(payload, dict) else {}
    _plan, gen, gen_id = edit._load_gen(owner, pid)  # noqa: SLF001
    variant = sound._variant_of(payload.get("variant"), gen, where)  # noqa: SLF001
    raw = payload.get("r")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise VpocError(where, "BadRevision", "되돌릴 영상을 골라 주세요.", 400)
    source = sound._source(owner, pid, gen_id, gen, variant)  # noqa: SLF001
    base_key = _base_key(source)
    box: dict = {}

    def mutate(doc):
        if any(j.get("status") == "splicing" for j in doc["jobs"].values()):
            raise VpocError(where, "LineRedoRunning", "새 대사 영상을 잇는 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        if raw:
            rev = next((x for x in doc["revisions"] if int(x.get("r") or 0) == raw and x.get("baseKey") == base_key), None)
            if not rev:
                raise VpocError(where, "BadRevision", "되돌릴 영상을 찾을 수 없습니다. 다시 불러와 주세요.", 404)
            box["key"] = str(rev["key"])
        else:
            box["key"] = base_key
        doc["current"] = raw
        return doc

    _mutate_redo(owner, pid, gen_id, variant, mutate)
    _mark_versions(owner, pid, gen_id, variant, box["key"])
    log.info("vnew.sound.lines.revert pid=%s gen=%s variant=%s r=%d", pid, gen_id, variant, raw)
    return sound.sound_view(owner, pid, variant)
