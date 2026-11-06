"""vnew 영상만들기_신규 — ④ 영상 생성(설계자 2026-10-20).

모델링된 캐릭터 + 결정된 콘티의 컷 + 대본을 참고해 **하나의 영상이 이어지도록** 만든다.
모델 최대 길이에 닿으면 자동으로 끊고(③ 콘티 segments), 다음 구간을 만들 때 배경·캐릭터·상황·
위치가 이어지게 한다. 결과는 **실사**와 **웹툰 그림체** 두 개, 사용자가 하나를 골라 ⑤ 사운드로.

연속성 설계(Claude 결정 2026-10-20):
- Seedance 는 참고 이미지와 시작 이미지를 같이 못 쓴다(ark.py). 그래서 **모든 구간을 첫 프레임
  (이미지→영상) 방식**으로 만든다.
- 1구간 첫 프레임 = "실사 시작 장면": Seedream 5.0 Pro 로 그린다(그 구간 첫 컷 웹툰 패널 +
  등장 캐릭터 실사 시트·정면 참고).
- 2구간부터 첫 프레임 = 앞 구간 실사 영상의 마지막 프레임(stitcher.last_frame_jpeg).
- 웹툰 버전: 실사 구간이 끝날 때마다 016 처럼 변환(stitcher.prepare_for_anime + fal.submit_edit).
- 병합: 실사·웹툰 각각 stitcher.concat 으로 final-real.mp4 · final-webtoon.mp4.

재사용(수정 금지): vpoc.ark/fal/video(제출·조회·취소) · vpoc.seedream(시작 장면) · vpoc.store(S3) ·
vpoc.ledger(원장) · vpoc.settings(shot_usd·anime_usd) · vnew.characters(실사/웹툰 시트).

외부 호출은 아래 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _seedream · _submit_video · _poll_video ·
_cancel_video · _submit_anime · _last_frame · _prepare_anime · _concat · _fetch_bytes.
STORE 는 vpoc.store.STORE 를 쓴다(검사가 모듈 변수로 바꿔 끼운다).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import ark as vpoc_ark
from ..vpoc import fal as vpoc_fal
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import seedream as vpoc_seedream
from ..vpoc import settings as vpoc_settings
from ..vpoc import video as vpoc_video
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE, video_output_prefix

from . import casting as vnew_casting
from . import characters
from . import fx as vnew_fx
from . import imaging
from . import plan as plan_mod
from . import previz_schema
from . import settings as vnew_settings
# 작업 161: 실사 장소 글의 상표 금지 구절(한 곳 = locations). locations 는 generate 를 import 하지 않는다.
from .locations import REAL_NO_BRAND, REAL_NO_BRAND_SENTENCE
# 지시서 117: 속마음 대사 판정은 prompts.is_thought 한 곳(순수 모듈)에서만 한다.
from .prompts import is_thought as _is_thought
from .prompts import OFF_FRAME_HAND_POSITION as _OFF_FRAME_HAND
from .prompts import STILL_SHOT_SIZES as _SHOT_SIZES

log = logging.getLogger("vpoc")

# ⑤ OOM 완화(설계자 2026-10-23). 준비 단계(녹화 받기→클립 자르기→payload→본문 직렬화·제출)를
# 프로세스 전역에서 직렬화한다. 동시 4칸이 각자 큰 바이트(video·clip·built·body)를 메모리에 쌓아
# 512Mi 파드가 OOMKilled 됐다. 세마포어로 한 번에 VNEW_GEN_PREPARE_CONCURRENCY(기본 1)칸만 준비한다.
# 조회(poll)는 세마포어 밖(가볍다). 설정값을 모듈 로드 시점 1회만 읽어 상한을 고정한다.
_PREPARE_SEM = threading.BoundedSemaphore(max(1, int(vnew_settings.VNEW_GEN_PREPARE_CONCURRENCY or 1)))


def _rss_mb() -> float:
    """현재 프로세스 RSS(MB). 못 구하면 -1(관측 로그용, 설계자 2026-10-23).

    리눅스는 /proc/self/status VmRSS(kB)를 읽는다. 없으면 resource.getrusage(ru_maxrss)로.
    ru_maxrss 단위는 리눅스 kB·맥 바이트라 리눅스(kB) 기준으로 환산한다. 둘 다 실패하면 -1.
    """
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    kb = float(line.split()[1])
                    return round(kb / 1024.0, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        import resource  # noqa: PLC0415

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(float(maxrss) / 1024.0, 1)
    except Exception:  # noqa: BLE001
        return -1.0

# ================================================================ 상수

# preparing(선점) 이 이 시간을 넘게 그대로면 queued 로 되돌린다(016-fix2 와 같은 15분).
PREPARE_STALE_S = 900
# gen 전체가 이 시간을 넘게 running 이면 멈춘 것으로 본다(파드 재시작 대비, 30분).
GEN_STALL_S = 1800
# 웹툰 변환 참고 이미지 최대 장수(그 구간 웹툰 패널 + 캐릭터 웹툰 시트).
MAX_ANIME_REFS = 4
# 구간 영상 프롬프트 목표 상한(설계자 2026-10-20). 모델 maxLength 가 더 작으면 그 안으로.
SEGMENT_PROMPT_TARGET = 1800
# 시작 장면 Seedream 단가 설정 키(설계자: VNEW_SEEDREAM_USD 재사용).
SEEDREAM_USD = vnew_settings.SEEDREAM_USD

# 상태 이름.
_VARIANT_STATES = ("waiting", "queued", "preparing", "submitted", "completed", "failed", "cancelled")


# ================================================================ 키 규칙
#
# 출력 접두사(영상)는 VIDEO_BUCKET 아래 users/{owner}/video/{pid}/vnew/gen/{genId}/{segId}-{attempt}/.
# 메인 버킷에는 시작 장면 이미지·마지막 프레임·최종 병합본을 둔다.


def _gen_root(owner: str, pid: str, gen_id: str) -> str:
    return f"users/{owner}/video/{pid}/vnew/gen/{gen_id}"


def opening_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/opening.jpg"


def first_frame_key(owner: str, pid: str, gen_id: str, seg_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/{seg_id}-firstframe.jpg"


def final_real_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/final-real.mp4"


def final_real_run_key(owner: str, pid: str, gen_id: str, n: int) -> str:
    """통 테이크 실사 회차 n 의 병합본(vnew-live-fx C.1). 회차마다 이름이 달라 이전 회차를 덮어쓰지 않는다."""
    return f"{_gen_root(owner, pid, gen_id)}/final-real-r{int(n)}.mp4"


def final_real_fixed_key(owner: str, pid: str, gen_id: str, n: int) -> str:
    """작업 134: 실사 회차 n 을 다시 이어 붙인 병합본(remerge_real_cuts). 원래 final-real-r{n}.mp4 를 덮어쓰지 않는다."""
    return f"{_gen_root(owner, pid, gen_id)}/final-real-r{int(n)}-fixed.mp4"


def final_webtoon_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/final-webtoon.mp4"


def _seg_output_prefix(owner: str, pid: str, gen_id: str, seg_id: str, attempt: int) -> str:
    # 영상 제출 출력 접두사. 재사용 함수들(ark/fal/video)의 규칙과 같게 video_output_prefix 를 쓴다.
    return video_output_prefix(owner, pid, f"vnew-{gen_id}", seg_id, attempt, gen_id)


def _gen_id() -> str:
    return "g" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 함수들을 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _seedream(prompt: str, references: list[bytes], *, size: str) -> bytes:
    """Seedream 5.0 Pro 로 시작 장면 1장."""
    return vpoc_seedream.generate_safe(
        prompt,
        references,
        model=vnew_settings.SEEDREAM_MODEL,
        where="vnew.generate.seedream",
        size=size,
    )


def _submit_video(**kwargs) -> dict:
    """영상 제출(ark/fal/video 로 분기). model 로 공급자를 가른다 — ark.submit 이 fal 로 넘긴다.

    Luma 는 ark 가 처리하지 않으므로 video.submit 을 쓴다(keyframe_jpeg_b64 로 첫 프레임).
    """
    model = str(kwargs.get("model") or "")
    if model == vpoc_ark.DEFAULT_MODEL or not vpoc_ark.is_ark(model):
        # Luma Ray2(Bedrock async). ark 가 모르는 모델이면 Luma 로 본다.
        return vpoc_video.submit(
            owner=kwargs["owner"],
            project_id=kwargs["project_id"],
            plan_id=kwargs["plan_id"],
            shot_id=kwargs["shot_id"],
            attempt=kwargs["attempt"],
            prompt=kwargs["prompt"],
            keyframe_jpeg_b64=kwargs.get("keyframe_jpeg_b64", ""),
            gen_id=kwargs.get("gen_id", ""),
            resolution=kwargs.get("resolution", ""),
        )
    return vpoc_ark.submit(**kwargs)


def _poll_video(invocation_arn: str, output_prefix: str) -> dict:
    """영상 상태 조회(ark/fal/video 로 분기). ark.poll 과 같은 반환 모양으로 맞춘다."""
    if vpoc_ark.is_ark_job(invocation_arn):
        return vpoc_ark.poll(invocation_arn, output_prefix)
    # Luma(Bedrock async). video.poll 은 status·failureMessage 를 준다. 완료면 mp4 를 찾아 outputKey.
    result = vpoc_video.poll(invocation_arn)
    status = str(result.get("status") or "")
    if status == "Completed":
        item = vpoc_video.find_output_mp4(output_prefix, invocation_arn)
        if not item:
            return {"status": "Failed", "failureMessage": "완료됐지만 영상 파일을 찾지 못했습니다."}
        return {"status": "Completed", "failureMessage": "", "outputKey": item["key"]}
    return {"status": status or "InProgress", "failureMessage": result.get("failureMessage") or ""}


def _cancel_video(invocation_arn: str) -> str:
    """진행 중 영상 취소(014 규칙). ark/fal 은 cancel, Luma 는 취소 API 없음 → unsupported.

    반환: "ok"(성공) · "rejected"(공급자 거절) · "unsupported"(Luma).
    """
    if not invocation_arn:
        return "unsupported"
    if not vpoc_ark.is_ark_job(invocation_arn):
        # Luma(Bedrock async)는 취소 API 가 없다.
        return "unsupported"
    try:
        vpoc_ark.cancel(invocation_arn)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        log.info("vnew.gen.cancel_rejected arn=%s err=%s", invocation_arn, str(exc)[:200])
        return "rejected"


def _submit_anime(*, video_mp4: bytes, prompt: str, output_prefix: str, reference_jpegs_b64: list[str]) -> dict:
    """실사 구간 → 웹툰(애니) 변환 제출(fal.submit_edit)."""
    return vpoc_fal.submit_edit(
        endpoint=vpoc_settings.VPOC_ANIME_MODEL,
        video_mp4=video_mp4,
        prompt=prompt,
        output_prefix=output_prefix,
        reference_jpegs_b64=reference_jpegs_b64,
        keep_audio=True,
    )


def _last_frame(mp4: bytes) -> bytes:
    """실사 구간 영상의 마지막 프레임 JPEG(다음 구간 첫 프레임)."""
    with tempfile.TemporaryDirectory(prefix="vnew-lastframe-") as workdir:
        path = os.path.join(workdir, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(mp4)
        return stitcher.last_frame_jpeg(path)


def _prepare_anime(mp4: bytes) -> dict:
    """실사 mp4 를 fal video-to-video 가 받는 형태(3~15초·높이 ≥720)로 맞춘다.

    반환: {"bytes": 준비된 mp4 bytes, "seconds": 초}. 3초 미만이면 stitcher.AnimeTooShort.
    """
    with tempfile.TemporaryDirectory(prefix="vnew-anime-") as workdir:
        src = os.path.join(workdir, "src.mp4")
        with open(src, "wb") as handle:
            handle.write(mp4)
        out = os.path.join(workdir, "prepared.mp4")
        info = stitcher.prepare_for_anime(src, out, workdir)
        with open(info["path"], "rb") as handle:
            return {"bytes": handle.read(), "seconds": float(info.get("seconds") or 0)}


def _concat(clips: list[bytes]) -> bytes:
    """여러 mp4 바이트를 순서대로 이어 붙인다(stitcher.concat)."""
    with tempfile.TemporaryDirectory(prefix="vnew-concat-") as workdir:
        paths = []
        for index, data in enumerate(clips):
            path = os.path.join(workdir, f"clip-{index:03d}.mp4")
            with open(path, "wb") as handle:
                handle.write(data)
            paths.append(path)
        out = os.path.join(workdir, "final.mp4")
        stitcher.concat(paths, out)
        with open(out, "rb") as handle:
            return handle.read()


def _fetch_bytes(key: str, *, bucket: str = "") -> bytes:
    """우리 버킷의 파일 바이트(완료된 구간 영상 등)."""
    return STORE.get_bytes(key, bucket=bucket)


def _has_audio_stream(mp4: bytes) -> bool | None:
    """mp4 에 오디오 스트림이 있는지(진단 로그용, 설계자 2026-10-26, 064).

    ffmpeg -i 출력에서 "Audio:" 스트림 줄을 찾는다(ffprobe 없이, stitcher._audio_params 와 같은 방식).
    ffmpeg 가 없거나 못 읽으면 None(판정 불가 — 막지 않는다). 통 테이크는 generate_audio=true 로
    효과음·립싱크를 넣으므로, 최종 결과에 오디오가 있는지 로그로 남겨 사운드가 빠진 회차를 진단한다.
    """
    if not mp4:
        return None
    try:
        import subprocess as _subprocess  # noqa: PLC0415

        binary = stitcher.ffmpeg_path()
        with tempfile.TemporaryDirectory(prefix="vnew-audioprobe-") as workdir:
            path = os.path.join(workdir, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(mp4)
            result = _subprocess.run(
                [binary, "-hide_banner", "-i", path],
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
                timeout=120,
                check=False,
            )
            text = result.stderr.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if "Stream #" in line and "Audio:" in line:
                    return True
            return False
    except Exception:  # noqa: BLE001 — 진단용이라 실패는 판정 불가로 둔다.
        return None


def _trim_clip(mp4: bytes, seconds: float) -> bytes:
    """mp4 를 앞에서 seconds 만큼 잘라 돌려준다(설계자 2026-10-22, ⑤ — 채운 컷 원복).

    3초로 채워 제출한 컷의 결과를 합칠 때 원래 길이로 자른다. stitcher.trim(프레임 정확) 재사용.
    """
    with tempfile.TemporaryDirectory(prefix="vnew-trim-") as workdir:
        src = os.path.join(workdir, "src.mp4")
        with open(src, "wb") as handle:
            handle.write(mp4)
        out = os.path.join(workdir, "trimmed.mp4")
        stitcher.trim(src, out, start_sec=0.0, end_sec=max(0.1, float(seconds)))
        with open(out, "rb") as handle:
            return handle.read()


# 작업 134: 컷 단위 실사를 이을 때 컷마다 맞추는 형식. Kling 원래 컷(Main·오디오 먼저·스테레오 44.1k)과 QA 로 다시 만든 컷
# (Constrained Baseline·비디오 먼저·모노)이 섞이면 스트림 복사 concat 이 깨진다(missing picture in access unit, 길이 39.64초).
REAL_CUT_FPS = 24
REAL_CUT_SIZE = (1920, 1080)


def _real_cut_seconds(cut: dict) -> float:
    """컷을 자를 길이(작업 134): 그 컷의 콘티 길이(seconds). 3초로 채운 컷(padded)은 원래 길이(origSeconds). 없으면 0(영상 길이)."""
    cut = cut if isinstance(cut, dict) else {}
    if cut.get("padded") and float(cut.get("origSeconds") or 0) > 0:
        return float(cut["origSeconds"])
    return max(0.0, float(cut.get("seconds") or 0))


def _conform_concat(pieces: list[tuple[bytes, float]], *, hold: bool = False) -> bytes:
    """컷마다 같은 형식으로 맞춘 뒤 잇는다(작업 134). pieces = [(mp4, 자를 길이 초 — 0 이하면 그 영상 길이)].

    컷마다 stitcher.edit_segment: 0초부터 frames = round(초 × 24)장, -map 0:v:0, 1920x1080(비율 유지·검은 여백), 24fps, yuv420p,
    libx264 crf 20(High). 소리는 있으면 -map 0:a:0, 없으면 무음 — 둘 다 48kHz 스테레오 PCM 으로 정확히 frames/24 초.
    그다음 stitcher.edit_join: 맞춘 영상은 스트림 복사로 잇고, 이은 PCM 을 AAC 48k 스테레오로 한 번 인코딩한다.
    영상이 콘티 길이보다 1초 넘게 짧으면 영상 길이 + 0.9초까지만 쓴다(edit_segment 의 마지막 프레임 복제가 1초다).
    못 읽는 컷이 있으면 StitchError.
    hold(작업 156, 테이크 조각·테이크 → 최종): 늘 계획 길이로 맞춘다. 짧은 영상은 stitcher.pad_to_seconds 로 마지막 프레임을 계획
    길이까지 잡고(소리는 원본에서, 모자란 곳은 무음), 프레임 수는 누적 시각으로 센다(round(끝 × 24) − 앞까지 프레임) — 조각이 많아도
    합이 계획 합의 ±1프레임 안이다. 끄면(기본, 134 컷 단위 실사) 지금 그대로다.
    """
    if not pieces:
        raise stitcher.StitchError("이어 붙일 컷이 없습니다.")
    fps = REAL_CUT_FPS
    width, height = REAL_CUT_SIZE
    clock = 0.0
    done_frames = 0
    with tempfile.TemporaryDirectory(prefix="vnew-conform-") as workdir:
        videos: list[str] = []
        pcm_all = os.path.join(workdir, "audio.pcm")
        with open(pcm_all, "wb") as joined:
            for index, (mp4, seconds) in enumerate(pieces):
                src = os.path.join(workdir, f"src-{index:03d}.mp4")
                with open(src, "wb") as handle:
                    handle.write(mp4 or b"")
                info = stitcher.probe_media(src)
                length = float(info.get("duration") or 0)
                if length <= 0 or not info.get("width"):
                    raise stitcher.StitchError(f"컷 {index + 1} 영상을 읽지 못했습니다.")
                target = float(seconds or 0)
                video = os.path.join(workdir, f"cut-{index:03d}.mp4")
                pcm = os.path.join(workdir, f"cut-{index:03d}.pcm")
                if hold:
                    want = target if target > 0 else length
                    clock += want
                    frames = max(1, round(clock * fps) - done_frames)
                    done_frames += frames
                    source = src
                    if frames / fps > length + 0.9:  # edit_segment 의 마지막 프레임 복제(1초)로 모자란다
                        source = os.path.join(workdir, f"pad-{index:03d}.mp4")
                        stitcher.pad_to_seconds(src, source, target_sec=frames / fps + 0.1, threads=1)
                    if abs(length - want) > 1.0 / fps:
                        log.info("vnew.gen.plan_fit piece=%d got=%.3f want=%.3f frames=%d %s", index + 1, length, want, frames,
                                 "trim" if length > want else "hold")
                    stitcher.edit_segment(source, video, start_sec=0.0, frames=frames, fps=fps, width=width, height=height,
                                          audio_out=pcm, audio_from_source=bool(info.get("hasAudio")),
                                          audio_source=src if source != src else "", audio_start_sec=0.0)
                    if source != src:
                        os.unlink(source)
                    os.unlink(src)
                else:
                    target = min(target, length + 0.9) if target > 0 else length
                    stitcher.edit_segment(src, video, start_sec=0.0, frames=max(1, round(target * fps)), fps=fps, width=width,
                                          height=height, audio_out=pcm, audio_from_source=bool(info.get("hasAudio")))
                    os.unlink(src)
                with open(pcm, "rb") as handle:
                    joined.write(handle.read())
                os.unlink(pcm)
                videos.append(video)
        out = os.path.join(workdir, "merged.mp4")
        stitcher.edit_join(videos, out, audio_path=pcm_all)
        with open(out, "rb") as handle:
            return handle.read()


# ================================================================ gen 상태 모델


def _empty_variant_state() -> dict:
    return {
        "status": "waiting",  # waiting|queued|preparing|submitted|completed|failed|cancelled
        "attempt": 0,
        "invocationArn": "",
        "outputKey": "",
        "outputPrefix": "",
        "firstFrameKey": "",
        "error": "",
        "claim": "",
        "claimedAt": "",
        "cancelRemote": None,
    }


def _empty_segment(seg: dict) -> dict:
    return {
        "id": seg["id"],
        "cutIds": list(seg.get("cutIds") or []),
        "seconds": int(seg.get("seconds") or 0),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


def _new_gen(plan: dict) -> dict:
    inp = plan.get("input") or {}
    conti = plan.get("conti") or {}
    video_model = str(inp.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
    resolution = _resolution_for(video_model, inp)
    segments = [_empty_segment(seg) for seg in (conti.get("segments") or [])]
    return {
        "genId": _gen_id(),
        "status": "running",  # running|done|partial|failed|cancelled
        "videoModel": video_model,
        "resolution": resolution,
        "createdAt": now_iso(),
        "opening": {"status": "waiting", "imageKey": "", "error": "", "claim": "", "claimedAt": ""},
        "segments": segments,
        "finalReal": {"status": "waiting", "key": "", "error": ""},
        "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
        "selected": None,  # None|"real"|"webtoon"
        "costUsd": 0.0,
    }


def _resolution_for(video_model: str, inp: dict) -> str:
    """이 모델의 기본 해상도(③에서 따로 고르지 않으므로 모델 기본)."""
    try:
        options = vpoc_ark.video_options(video_model)
        return str(options.get("defaultResolution") or vpoc_settings.default_resolution(video_model))
    except Exception:  # noqa: BLE001
        return vpoc_settings.default_resolution(video_model)


# ================================================================ 프리비즈 모드 gen(설계자 2026-10-21)
#
# 승인된 프리비즈 녹화 구간마다 실사·웹툰 두 작업을 **동시에**(구간끼리도 병렬) fal
# video-to-video/edit 로 제출한다. 옛 모드(mode 없음)의 opening·순서 진행과 달리, 프리비즈가
# 연속성(배치·카메라·인원·타이밍)을 보장하므로 앞 구간을 기다릴 필요가 없다.


def _empty_previz_segment(seg: dict) -> dict:
    return {
        "id": seg["id"],
        "cutIds": list(seg.get("cutIds") or []),
        "seconds": float(seg.get("seconds") or 0),
        "recordingKey": str(seg.get("recordingKey") or ""),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


# 영상 모델(fal video-to-video)이 받는 최소 길이(초). 3초보다 짧은 컷은 마지막 프레임 복제로 채운다.
PREVIZ_MIN_CLIP_S = 3.0


def _empty_previz_unit(unit: dict) -> dict:
    """컷 단위 작업 1개(설계자 2026-10-22, ⑤). 실사·웹툰 두 변형을 담는다."""
    return {
        "id": str(unit.get("id") or ""),
        "cutId": str(unit.get("cutId") or ""),
        "recSegId": str(unit.get("recSegId") or ""),
        "recordingKey": str(unit.get("recordingKey") or ""),
        "startSec": float(unit.get("startSec") or 0.0),
        "endSec": float(unit.get("endSec") or 0.0),
        "seconds": float(unit.get("seconds") or 0.0),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


def _new_previz_gen(plan: dict) -> dict:
    """프리비즈 기반 gen(설계자 2026-10-22, ⑤). units = 컷 1개 = 작업 1개.

    기존 segments 필드는 옛 데이터 보기용으로만 남긴다(새 gen 은 빈 리스트). 모든 variant 가 바로
    queued(병렬 제출). 컷 클립은 제출 때 녹화 mp4 에서 컷 시간만큼 잘라 쓴다.
    """
    units = []
    for unit in _previz_units(plan):
        item = _empty_previz_unit(unit)
        item["real"]["status"] = "queued"
        item["webtoon"]["status"] = "queued"
        units.append(item)
    previz = plan.get("previz") or {}
    brief = previz.get("shotBrief") or {}
    return {
        "genId": _gen_id(),
        "mode": "previz",
        "status": "running",  # running|done|partial|failed|cancelled
        "videoModel": _previz_model_endpoint(plan),
        "createdAt": now_iso(),
        # 이 gen 이 어느 프리비즈 녹화·지시서를 바탕으로 만들어졌는지(설계자 2026-10-22, ⑤).
        # 나중에 previz.revision 이 바뀌거나(다시 설계·녹화) 지시서가 새로 만들어지면 stale 판정에 쓴다.
        "previzRevision": int(previz.get("revision") or 0),
        "briefUpdatedAt": str(brief.get("updatedAt") or ""),
        "units": units,
        "segments": [],  # 옛 데이터 보기용(새 gen 은 units 를 쓴다).
        "finalReal": {"status": "waiting", "key": "", "error": ""},
        "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
        "selected": None,  # None|"real"|"webtoon"
        "costUsd": 0.0,
    }


# ---------------------------------------------------------------- 영상 모델 선택(설계자 2026-10-22)
#
# 프리비즈 방식은 ①의 input.videoModel(검증됨)로 고른 모델로 만든다. 모델은 "fal:<endpoint>" id 로
# 저장되며, 못 고른/옛 값은 기본(VNEW_PREVIZ_EDIT_MODEL)으로 되돌린다. fal 큐·caps 조회는 endpoint
# (fal: 접두사 없는) 문자열을 쓴다.


def _previz_model_endpoint(plan: dict) -> str:
    """이 계획이 쓸 프리비즈 영상 모델 endpoint(설계자 2026-10-22).

    input.videoModel 이 기본 모델 또는 등록된 previzCapable fal 모델이면 그 endpoint, 아니면 기본.
    """
    default_endpoint = str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    raw = str((plan.get("input") or {}).get("videoModel") or "")
    if not raw:
        return default_endpoint
    endpoint = raw.split(":", 1)[-1] if raw.startswith(vpoc_settings.FAL_ID_PREFIX) else raw
    if endpoint == default_endpoint:
        return default_endpoint
    # 등록된 previzCapable fal 모델만 허용. 그 밖은 기본으로 되돌린다.
    item = vpoc_fal.find(vpoc_settings.FAL_ID_PREFIX + endpoint)
    if item and (item.get("caps") or {}).get("previzCapable"):
        return endpoint
    return default_endpoint


def _previz_caps(endpoint: str) -> dict:
    """그 endpoint 의 caps(등록돼 있으면 레지스트리 caps). 기본 모델처럼 미등록이면 빈 dict.

    빈 dict 는 '기본 Kling 방식(elements)'으로 다룬다(아래 _previz_payload).
    """
    item = vpoc_fal.find(vpoc_settings.FAL_ID_PREFIX + str(endpoint or ""))
    return (item or {}).get("caps") or {}


# ================================================================ 통 테이크 모드(설계자 2026-10-26, 063)
#
# input.videoModel 의 caps 에 videoListField(배열 영상 입력)가 있으면 gen.mode = "previz-take".
# 블록아웃 녹화를 통째(≤30초)로 Seedance 2.5 reference-to-video 에 넣어 웹툰 그림체를 한 번에
# 만든다(실사 칸은 skipped). 아니면 지금 그대로 컷 단위 "previz".


def _gen_mode(plan: dict) -> str:
    """이 계획이 쓸 gen 모드(설계자 2026-10-26). caps 에 videoListField 가 있으면 'previz-take',
    아니면 'previz'(컷 단위). 모드 선택은 input.videoModel 의 등록 caps 로만 정한다.
    """
    caps = _previz_caps(_previz_model_endpoint(plan))
    return "previz-take" if caps.get("videoListField") else "previz"


def _take_units(plan: dict) -> list[dict]:
    """녹화 구간(conti.segments 순서, 녹화 ready)을 앞에서부터 묶어 합계 ≤ VNEW_TAKE_MAX_SECONDS 인
    테이크로 만든다(넘으면 다음 테이크). 각 테이크:
      {id:"take1", cutIds:[...], recKeys:[...], recSegIds:[...], seconds, startCutNo, endCutNo}.
    녹화가 없는 구간은 건너뛴다(선행 조건에서 recordedAll 로 거른다).
    """
    conti = plan.get("conti") or {}
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    cut_no_by_id = {str(c.get("id")): c.get("no") for c in (conti.get("cuts") or [])}
    cap = max(4, int(vnew_settings.VNEW_TAKE_MAX_SECONDS or 30))

    takes: list[dict] = []
    current: dict | None = None
    for seg in conti.get("segments") or []:
        seg_id = str(seg.get("id") or "")
        rec = recordings.get(seg_id) or {}
        rec_key = str(rec.get("key") or "")
        if not rec_key:
            continue
        seg_seconds = float(rec.get("seconds") or 0) or float(seg.get("seconds") or 0)
        cut_ids = [str(cid) for cid in (seg.get("cutIds") or [])]
        # 현재 테이크에 넣으면 상한을 넘고, 이미 뭔가 들어 있으면 새 테이크를 연다.
        if current is not None and (current["seconds"] + seg_seconds) > cap + 0.01 and current["recKeys"]:
            takes.append(current)
            current = None
        if current is None:
            current = {
                "id": f"take{len(takes) + 1}",
                "cutIds": [],
                "recKeys": [],
                "recSegIds": [],
                "seconds": 0.0,
                "startCutNo": None,
                "endCutNo": None,
            }
        current["cutIds"].extend(cut_ids)
        current["recKeys"].append(rec_key)
        current["recSegIds"].append(seg_id)
        current["seconds"] = round(current["seconds"] + seg_seconds, 3)
        for cid in cut_ids:
            no = cut_no_by_id.get(cid)
            if no is None:
                continue
            if current["startCutNo"] is None:
                current["startCutNo"] = no
            current["endCutNo"] = no
    if current is not None and current["recKeys"]:
        takes.append(current)
    return takes


def _empty_take_unit(take: dict) -> dict:
    """테이크 1개 작업(설계자 2026-10-26). 웹툰만 생성(실사는 skipped·제출 0)."""
    return {
        "id": str(take.get("id") or ""),
        "cutIds": [str(c) for c in (take.get("cutIds") or [])],
        "recKeys": [str(k) for k in (take.get("recKeys") or [])],
        "recSegIds": [str(s) for s in (take.get("recSegIds") or [])],
        "seconds": float(take.get("seconds") or 0.0),
        "startCutNo": take.get("startCutNo"),
        "endCutNo": take.get("endCutNo"),
        "draftId": "",
        "real": {**_empty_variant_state(), "status": "skipped",
                 "note": "실사는 웹툰이 끝나면 기본 모델(Seedance)로 이어서 만듭니다."},
        "webtoon": {**_empty_variant_state(), "status": "queued"},
    }


def _new_take_gen(plan: dict) -> dict:
    """통 테이크 gen(설계자 2026-10-26, 063). units = 테이크(웹툰만). finalReal 은 skipped."""
    units = [_empty_take_unit(t) for t in _take_units(plan)]
    previz = plan.get("previz") or {}
    brief = previz.get("shotBrief") or {}
    return {
        "genId": _gen_id(),
        "mode": "previz-take",
        "status": "running",  # running|done|partial|failed|cancelled
        "videoModel": _previz_model_endpoint(plan),
        "resolution": str(vnew_settings.VNEW_TAKE_RESOLUTION),
        "createdAt": now_iso(),
        "previzRevision": int(previz.get("revision") or 0),
        "briefUpdatedAt": str(brief.get("updatedAt") or ""),
        "units": units,
        "segments": [],
        "finalReal": {"status": "skipped", "key": "", "error": "",
                      "note": "실사는 웹툰이 끝나면 기본 모델(Seedance)로 이어서 만듭니다."},
        "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
        "selected": None,
        "costUsd": 0.0,
    }


def _take_usd(endpoint: str, seconds: float, resolution: str) -> float:
    """통 테이크 1개 금액(설계자 2026-10-26). unit "1000 tokens" 모델 토큰 공식.

    해상도별 (w,h) = TAKE_RESOLUTION_WH. tokens = w×h×24×s/1024. usd = tokens/1000 × unitPrice.
    단가는 fal pricing(unit "1000 tokens") 을 먼저 쓰고, 못 받으면 VNEW_TAKE_TOKEN_UNIT_PRICE.
    """
    wh = vnew_settings.TAKE_RESOLUTION_WH.get(str(resolution or ""), vnew_settings.TAKE_RESOLUTION_WH["480p"])
    width, height = wh
    secs = max(0.0, float(seconds or 0))
    tokens = (width * height * 24.0 * secs) / 1024.0
    unit_price = _take_unit_price(endpoint)
    return round((tokens / 1000.0) * unit_price, 4)


def _take_unit_price(endpoint: str) -> float:
    """통 테이크 모델의 "1000 tokens" 단가(USD, 설계자 2026-10-26). fal pricing 우선, 없으면 기본값."""
    price = vpoc_fal.pricing_for(vpoc_settings.FAL_ID_PREFIX + str(endpoint or ""))
    if not price:
        price = vpoc_fal.pricing(str(endpoint or ""))
    if price and str(price.get("unit") or "").lower().replace("-", " ") == "1000 tokens":
        unit_price = float(price.get("unitPrice") or 0.0)
        if unit_price > 0:
            return unit_price
    return float(vnew_settings.VNEW_TAKE_TOKEN_UNIT_PRICE)


def _take_prompt_budget(caps: dict | None = None) -> int:
    """통 테이크 프롬프트 **글자** 상한(작업 157, _prompt_len 으로 잰다).

    기본 VNEW_TAKE_PROMPT_MAX_CHARS(4000). fal 스키마의 prompt 상한은 OpenAPI(JSON Schema) maxLength 라 글자 수다.
    Seedance 2.5 reference-to-video 의 prompt 에는 maxLength 가 없다(073 caps 확인) → 2500 은 우리 fal 기본값
    (DEFAULT_PROMPT_MAX)일 뿐이라 상한으로 보지 않는다. caps.promptMaxLength 가 '스키마 명시 값'(= 기본 2500 이 아닌
    실제 maxLength)이면 그 값과 기본값 중 작은 값을 쓴다. promptMaxLength 가 DEFAULT_PROMPT_MAX 와 같으면 '미명시'로 본다.
    통 테이크 API 가 바이트 상한을 문서로 밝힌 모델은 없어 바이트 안전 상한은 두지 않는다.
    """
    budget = int(vnew_settings.VNEW_TAKE_PROMPT_MAX_CHARS or 4000)
    caps = caps or {}
    model_max = int(caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
    # 스키마에 실제 maxLength 가 명시됐을 때만(기본값과 다를 때) 그 상한을 적용한다.
    if model_max != vpoc_fal.DEFAULT_PROMPT_MAX and model_max > 0:
        budget = min(budget, model_max)
    return budget


def _prompt_len(text: str) -> int:
    """통 테이크 프롬프트 길이(작업 157): 글자 수(_take_prompt_budget 과 같은 단위)."""
    return len(str(text or ""))


# ================================================================ 금액(estimate)



def _previz_segments(plan: dict) -> list[dict]:
    """프리비즈 녹화 구간 목록 = conti.segments 중 녹화(mp4)가 된 것.

    각 항목: {id, cutIds, seconds(녹화 실측 초, 없으면 conti seconds), recordingKey}.
    녹화가 없는 구간은 제외하지 않고 recordingKey="" 로 둔다(선행 조건에서 recordedAll 로 거른다).
    """
    conti = plan.get("conti") or {}
    previz = plan.get("previz") or {}
    recordings = previz.get("recordings") or {}
    out: list[dict] = []
    for seg in conti.get("segments") or []:
        seg_id = str(seg.get("id") or "")
        rec = recordings.get(seg_id) or {}
        rec_seconds = float(rec.get("seconds") or 0) or float(seg.get("seconds") or 0)
        out.append(
            {
                "id": seg_id,
                "cutIds": list(seg.get("cutIds") or []),
                "seconds": rec_seconds,
                "recordingKey": str(rec.get("key") or ""),
            }
        )
    return out


def _previz_units(plan: dict) -> list[dict]:
    """컷 단위 작업 목록(설계자 2026-10-22, ⑤). 컷 1개 = 작업 1개.

    각 항목: {id(=cutId), cutId, recSegId, recordingKey, startSec, endSec, seconds}.
    - recSegId/recordingKey: 그 컷이 속한 녹화 구간(conti.segments)과 그 mp4 키.
    - startSec/endSec: 그 구간 **녹화 mp4 안**에서 이 컷의 시간 범위. 콘티 컷 seconds 를 녹화 실측
      길이에 비례 배분해 구한다(녹화가 콘티와 조금 달라도 컷 경계가 맞게).
    - seconds: 이 컷의 실제 길이(endSec − startSec).
    녹화가 없는 구간의 컷은 recordingKey="" 로 둔다(선행 조건에서 recordedAll 로 거른다).
    """
    conti = plan.get("conti") or {}
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    cuts_by_id = {str(c.get("id")): c for c in (conti.get("cuts") or [])}
    out: list[dict] = []
    for seg in conti.get("segments") or []:
        seg_id = str(seg.get("id") or "")
        rec = recordings.get(seg_id) or {}
        rec_key = str(rec.get("key") or "")
        cut_ids = [str(cid) for cid in (seg.get("cutIds") or [])]
        # 콘티 컷 seconds 합과 녹화 실측 길이의 비(녹화가 조금 다를 수 있다).
        conti_sum = sum(float((cuts_by_id.get(cid) or {}).get("seconds") or 0) for cid in cut_ids)
        rec_seconds = float(rec.get("seconds") or 0) or conti_sum or float(seg.get("seconds") or 0)
        scale = (rec_seconds / conti_sum) if conti_sum > 0 else 1.0
        clock = 0.0
        for cid in cut_ids:
            cut = cuts_by_id.get(cid) or {}
            cut_seconds = float(cut.get("seconds") or 0) * scale
            start = clock
            end = clock + cut_seconds
            clock = end
            out.append(
                {
                    "id": cid,
                    "cutId": cid,
                    "recSegId": seg_id,
                    "recordingKey": rec_key,
                    "startSec": round(start, 3),
                    "endSec": round(end, 3),
                    "seconds": round(cut_seconds, 3),
                }
            )
    return out


def _previz_model_unit_price(endpoint: str) -> float:
    """그 프리비즈 모델의 초당 단가(USD, 설계자 2026-10-22).

    기본 모델은 vnew_settings.previz_edit_usd(1초) 로 계산(fal pricing 우선, 실패 시 기본값).
    등록된 fal 모델은 fal pricing(초당)을, 못 받으면 기본 단가를 쓴다.
    """
    default_endpoint = str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    if not endpoint or endpoint == default_endpoint:
        return float(vnew_settings.previz_edit_usd(1))
    price = vpoc_fal.pricing_for(vpoc_settings.FAL_ID_PREFIX + endpoint)
    if not price:
        price = vpoc_fal.pricing(endpoint)
    if price and str(price.get("unit") or "").lower() == "seconds":
        unit_price = float(price.get("unitPrice") or 0.0)
        if unit_price > 0:
            return unit_price
    return float(vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S)


def estimate(plan: dict) -> dict:
    """금액 미리보기(설계자 2026-10-22, ⑤ — 컷 단위).

    컷마다 max(3, seconds) × 고른 모델의 초당 단가 × 2(실사+웹툰). 반환:
    {mode:"previz", videoModel, realUsd, webtoonUsd, totalUsd, units:[{id,cutId,seconds,realUsd,webtoonUsd}],
     segments:[...]}(segments 는 옛 화면 호환). 녹화가 없으면 totalUsd 0.
    """
    endpoint = _previz_model_endpoint(plan)

    # 통 테이크 모드(설계자 2026-10-26): 테이크별 토큰 공식 금액(웹툰만, 실사 0).
    if _gen_mode(plan) == "previz-take":
        resolution = str(vnew_settings.VNEW_TAKE_RESOLUTION)
        rows = []
        webtoon_usd = 0.0
        for take in _take_units(plan):
            seconds = float(take.get("seconds") or 0)
            # 작업 152: 참고 영상 상한으로 나뉘는 테이크는 조각 길이마다 금액(합은 거의 같다). 나눌 수 없으면 통째 금액.
            try:
                parts = _take_parts_for(plan, take, endpoint)
            except VpocError:
                parts = []
            part_rows = [{"no": p["no"], "cutIds": list(p["cutIds"]), "seconds": p["seconds"],
                          "webtoonUsd": _take_usd(endpoint, p["seconds"], resolution)} for p in parts]
            cost = round(sum(r["webtoonUsd"] for r in part_rows), 4) if part_rows else _take_usd(endpoint, seconds, resolution)
            webtoon_usd += cost
            rows.append(
                {
                    "id": take.get("id"),
                    "cutIds": list(take.get("cutIds") or []),
                    "seconds": round(seconds, 3),
                    "realUsd": 0.0,
                    "webtoonUsd": cost,
                }
            )
            if part_rows:
                rows[-1]["parts"] = part_rows
        total = round(webtoon_usd, 2)
        return {
            "mode": "previz-take",
            "videoModel": endpoint,
            "resolution": resolution,
            "realUsd": 0.0,
            "webtoonUsd": round(webtoon_usd, 4),
            "totalUsd": total,
            "takes": rows,
            "units": rows,
            "segments": rows,  # 옛 화면 호환.
        }

    per_sec = _previz_model_unit_price(endpoint)
    rows = []
    real_usd = 0.0
    webtoon_usd = 0.0
    for unit in _previz_units(plan):
        seconds = float(unit.get("seconds") or 0)
        billed = max(PREVIZ_MIN_CLIP_S, seconds)
        cost = round(per_sec * billed, 4)
        real_usd += cost
        webtoon_usd += cost
        rows.append(
            {
                "id": unit.get("id"),
                "cutId": unit.get("cutId"),
                "seconds": round(seconds, 3),
                "realUsd": cost,
                "webtoonUsd": cost,
            }
        )
    total = round(real_usd + webtoon_usd, 2)
    return {
        "mode": "previz",
        "videoModel": endpoint,
        "realUsd": round(real_usd, 4),
        "webtoonUsd": round(webtoon_usd, 4),
        "totalUsd": total,
        "units": rows,
        "segments": rows,  # 옛 화면 호환(구간 합계 자리에 컷 합계를 보여 준다).
    }



# ================================================================ 선행 조건 · 시작


def _char_entries(owner: str, inp: dict) -> list[dict]:
    """계획에 연결된 캐릭터를 로드해 외형·시트 키를 모은다(실사 front 없으면 그대로 둔다)."""
    out = []
    for item in inp.get("characters") or []:
        char_id = str(item.get("charId") or "")
        role = str(item.get("roleName") or "").strip()
        try:
            char, _ = characters.load_char(owner, char_id)
        except Exception:  # noqa: BLE001
            out.append({"charId": char_id, "roleName": role, "name": role, "missing": True})
            continue
        variants = char.get("variants") or {}
        real = variants.get("real") or {}
        webtoon = variants.get("webtoon") or {}
        analysis = char.get("analysis") or {}
        out.append(
            {
                "charId": char_id,
                "roleName": role or (char.get("name") or ""),
                "name": char.get("name") or role,
                "appearanceEn": str(analysis.get("appearanceEn") or ""),
                "outfitEn": str(analysis.get("outfitEn") or ""),
                "styleEn": str(analysis.get("styleEn") or ""),
                "realFrontKey": str(real.get("frontKey") or ""),
                "realSheetKey": str(real.get("sheetKey") or ""),
                "realBackKey": str(real.get("backKey") or ""),
                "webtoonSheetKey": str(webtoon.get("sheetKey") or ""),
                "webtoonFrontKey": str(webtoon.get("frontKey") or ""),
                "webtoonBackKey": str(webtoon.get("backKey") or ""),
                "missing": False,
            }
        )
        if vnew_settings.VNEW_CASTING:
            # vnew-live-fx B(design §B.5): 고른 캐스팅 사진의 S3 키(없으면 ""). 통 테이크 실사(묶음 C)만 _real_front_key 로 쓴다.
            # 끔이면 키를 두지 않는다(결과 dict 가 B 이전과 같다). 컷 단위 Kling 실사 payload 는 이 키를 보지 않는다(결정 4).
            out[-1]["castingKey"] = _casting_key_of(char_id, char)
    return out


def _casting_key_of(char_id: str, char: dict) -> str:
    """캐릭터의 고른 캐스팅 사진 키(casting.chosen_key). 저장값이 깨져 읽지 못하면 "" — 영상 생성을 막지 않는다."""
    try:
        return vnew_casting.chosen_key(char)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.casting_key_unreadable char=%s err=%s", char_id, type(exc).__name__)
        return ""


def _real_front_key(entry: dict) -> str:
    """실사 인물 사진 키: 고른 캐스팅 사진(castingKey)이 있으면 그것, 없으면 실사 정면(realFrontKey)(design §B.5).

    통 테이크 실사(묶음 C: _take_payload(variant="real")·start_real 인물 확인)만 쓴다. 결정 4: 컷 단위 Kling 실사
    payload(_previz_payload_elements·_previz_payload_image_list)는 이 함수를 쓰지 않고 지금처럼 realFrontKey 를 쓴다.
    """
    entry = entry if isinstance(entry, dict) else {}
    return str(entry.get("castingKey") or entry.get("realFrontKey") or "")


# ---------------------------------------------------------------- 작업 126: 실사 인물 참고 = 3D 모델용 사진 · 인물 바꿔 다시 만들기
#
# 통 테이크 실사(_take_payload variant="real")의 인물 참고는 캐릭터 만들기의 3D 모델용 실사 사진이다: 실사 정면 → 실사 시트
# → 실사 후면(있으면). 캐스팅 사진은 사용자가 [이 사진으로] 고른 경우(castingKey 가 빈 값이 아님)에만 맨 앞에 둔다.
# 참고 이미지 상한(caps.imageListMax) 안에서 인물마다 첫 장부터 돌아가며 채우고(_ref_counts), 넘치면 뒤 사진부터 빠진다.
# 회차(realRun.cast)에 인물 바꾸기가 있으면 그 회차의 참고·프롬프트만 바뀐다(plan.input·대본·프리비즈는 그대로).
# 컷 단위 Kling 실사 payload(결정 4)는 이 함수들을 쓰지 않는다.

# 실사 프롬프트 ASSETS 의 고정 문장(줄이기 단계·최소형 모두 ASSETS 머리에 남는다). "sheet" 낱말은 쓰지 않는다(lr-d 검사:
# 웹툰 rule_replace 의 "reference sheet" 가 실사에 남지 않게 본다) — 시트는 "turnaround views" 로 적는다.
REAL_TAKE_SHEET_SENTENCE = (
    "Each person is the same person as in their 3D-model photos (front and turnaround views); keep face, hair and "
    "body shape as in those photos; clothing comes only from COSTUME"
)
# 작업 146: 실사 시트 인물에 쓸 만한 장면 옷이 없을 때(COSTUME 문장 없음) 시트 기본 옷을 따라 입지 않게 한다.
REAL_TAKE_NO_SHEET_CLOTHES = "Do not copy the sheet's default clothes (such as a plain white shirt)"
# 인물 바꾸기에서 모습 [애니]를 고른 인물: 웹툰 정면·시트를 참고로 주고 실사 인물로 옮기라고 적는다.
REAL_TAKE_ANIME_SENTENCE = "render as a photoreal person matching this character design"
REAL_CAST_LOOKS = ("real", "anime")
REAL_CAST_LOOK_KO = {"real": "실사", "anime": "애니"}
_CAST_CHAR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _real_photo_keys(entry) -> list[str]:
    """실사 인물 참고 키 순서(작업 126): [고른 캐스팅 사진] → 실사 정면 → 실사 시트 → 실사 후면. 빈 값·중복은 뺀다."""
    entry = entry if isinstance(entry, dict) else {}
    out: list[str] = []
    for field in ("castingKey", "realFrontKey", "realSheetKey", "realBackKey"):
        key = str(entry.get(field) or "")
        if key and key not in out:
            out.append(key)
    return out


def _anime_ref_keys(entry) -> list[str]:
    """인물 바꾸기 모습 [애니]의 참고 키: 웹툰 정면 → 웹툰 시트. 빈 값·중복은 뺀다."""
    entry = entry if isinstance(entry, dict) else {}
    out: list[str] = []
    for field in ("webtoonFrontKey", "webtoonSheetKey"):
        key = str(entry.get(field) or "")
        if key and key not in out:
            out.append(key)
    return out


def _fallback_front_keys(entry, look: str) -> list[str]:
    """작업 130 likeness 1단계 참고: 인물당 정면 1장. 실사 = 실사 정면(없으면 고른 캐스팅 사진), 인물 바꾸기 애니 = 웹툰 정면.
    시트·후면은 뺀다. 2단계(컷 단위 Kling)의 시작 그림 참고도 이 함수(실사)를 쓴다."""
    entry = entry if isinstance(entry, dict) else {}
    if look == "anime":
        return _anime_ref_keys(entry)[:1]
    key = str(entry.get("realFrontKey") or entry.get("castingKey") or "")
    return [key] if key else []


# 작업 142: 실사 인물 참고 = 우리 AI 캐릭터 시트(실사 5방향 턴어라운드 1장) + 정면. 문장은 아래 빌더 1곳(Seedance ASSETS·시작 그림 FACES/CAST).
REAL_SHEET_REF_SENTENCE = (
    "{img} is {name}'s AI-generated character turnaround sheet (front, three-quarter, side, three-quarter back, back views "
    "of the same fictional person): match face, hair, skin tone and body build from it; ignore its plain white background "
    "and its clothes — clothing comes from the scene."
)
REAL_SHEET_FRONT_SENTENCE = "{img} is {name}'s close front portrait from the same sheet set."
# 작업 160: 정면 자리의 장면 옷 편집 그림 이름표(시작 그림·Seedance 공통).
REAL_COSTUME_REF_SENTENCE = "{img} is {name} in this scene's clothes."


def _real_identity_refs(entry, look: str) -> list[str]:
    """작업 142: 실사 인물 1명의 신원 참고 키(순서 고정).

    실사: 시트가 있으면 [실사 시트, 실사 정면](정면이 없으면 [시트]), 시트가 없으면 [실사 정면 또는 고른 캐스팅 사진]
    (= _fallback_front_keys, 140 과 같다). 인물 바꾸기 애니(126): _anime_ref_keys(entry)[:1] 그대로.
    상한에 걸리면 _ref_counts 가 둘째 장(정면)부터 뺀다 — 누구의 시트보다 정면이 먼저 빠진다.
    """
    entry = entry if isinstance(entry, dict) else {}
    if look == "anime":
        return _anime_ref_keys(entry)[:1]
    sheet = str(entry.get("realSheetKey") or "")
    if not sheet:
        return _fallback_front_keys(entry, "real")
    front = str(entry.get("realFrontKey") or "")
    return [sheet, front] if front and front != sheet else [sheet]


def _real_sheet_sentences(name: str, sheet_no: int, front_no: int | None = None, *, ref: str = "Image {}",
                          costume: bool = False) -> str:
    """작업 142: 시트(와 정면) 참고 문장. ref = 번호 표기("Image {}" 시작 그림, "@Image{}" Seedance).
    작업 160: costume=True 면 front_no 는 장면 옷 편집 그림 — 시트 문장 끝 "clothes come from {그 번호}", 정면 문장은 이름표."""
    text = REAL_SHEET_REF_SENTENCE.format(img=ref.format(sheet_no), name=name)
    if front_no and costume:
        img = ref.format(front_no)
        text = text.replace("clothing comes from the scene.", f"clothes come from {img}.")
        return text + " " + REAL_COSTUME_REF_SENTENCE.format(img=img, name=name)
    if front_no:
        text += " " + REAL_SHEET_FRONT_SENTENCE.format(img=ref.format(front_no), name=name)
    return text


def _person_refs_kind(cast) -> str:
    """작업 142 제출 로그 refs=: 인물 참고에 시트가 있으면 sheet, 시트 아닌 사진이 있으면 front, 둘 다면 both."""
    members = [m for m in cast or [] if isinstance(m, dict)]
    has_sheet = any(m.get("sheetNo") for m in members)
    has_front = any(n != m.get("sheetNo") for m in members for n in m.get("refNos") or [])
    return "both" if has_sheet and has_front else "sheet" if has_sheet else "front"


def _ref_counts(lengths: list[int], cap: int) -> list[int]:
    """인물마다 넣을 참고 장수. 첫 장을 모두에게 먼저 주고 둘째 장, 셋째 장 순으로 상한(cap)까지 채운다(뒤 사진부터 빠진다)."""
    counts = [0] * len(lengths)
    left = max(0, int(cap or 0))
    rank = 0
    while left > 0 and any(rank < n for n in lengths):
        for i, n in enumerate(lengths):
            if left <= 0:
                break
            if rank < n:
                counts[i] += 1
                left -= 1
        rank += 1
    return counts


def _image_refs(nos: list[int]) -> str:
    """[2, 3] → "@Image2, @Image3"."""
    return ", ".join(f"@Image{n}" for n in nos)


def _cast_entry(owner: str, char_id: str, name: str) -> dict | None:
    """인물 바꾸기 캐릭터 1명의 _char_entries 항목(소유자 저장소에서만 읽는다). 못 읽으면 None."""
    entries = _char_entries(owner, {"characters": [{"charId": char_id, "roleName": name}]})
    entry = entries[0] if entries else None
    return None if not entry or entry.get("missing") else entry


def _cast_look_block(entry: dict | None, look: str) -> str:
    """그 캐릭터의 그 모습을 고를 수 없는 이유(한국어). 고를 수 있으면 ""."""
    if not entry:
        return "캐릭터를 찾을 수 없습니다."
    if look == "anime":
        return "" if entry.get("webtoonFrontKey") else "웹툰 그림체 정면이 아직 없습니다."
    return "" if entry.get("realFrontKey") else "실사 정면이 아직 없습니다."


def _real_take_people(plan: dict, gen: dict) -> list[dict]:
    """실사 회차가 다루는 인물(모든 테이크의 배우, 이름 기준 한 번씩, 처음 나온 순서): [{name, charId}]."""
    people: list[dict] = []
    seen: set[str] = set()
    for unit in gen.get("units") or []:
        for actor in _segment_actors(plan, unit):
            name = str(actor.get("name") or "")
            if name and name not in seen:
                seen.add(name)
                people.append({"name": name, "charId": str(actor.get("charId") or "")})
    return people


def _cast_error(message: str) -> VpocError:
    return VpocError("vnew.generate.real", "BadCast", message, 400)


def _validate_real_cast(owner: str, plan: dict, gen: dict, cast) -> list[dict] | None:
    """POST /generate/real 의 cast(선택) 검증. None·[] → None(바꾸지 않음). 틀리면 400 BadCast.

    형식 [{name, charId, look:"real"|"anime"}]. name 은 이 회차 테이크의 인물 이름(한 번씩), charId 는 소유자(owner)의
    캐릭터(지운 캐릭터 제외), 고른 모습은 완성돼 있어야 한다(실사 = 실사 정면, 애니 = 웹툰 정면). 반환은 저장할 모양
    [{name, charId, look, charName}] — charName 은 회차 머리 "인물: 이름→캐릭터(실사/애니)" 에 쓴다.
    """
    if cast is None:
        return None
    if not isinstance(cast, list):
        raise _cast_error("인물 바꾸기(cast) 형식이 틀렸습니다.")
    if not cast:
        return None
    names = {p["name"] for p in _real_take_people(plan, gen)}
    if len(cast) > len(names):
        raise _cast_error("인물 바꾸기(cast) 항목이 이 테이크의 인물 수보다 많습니다.")
    out: list[dict] = []
    used: set[str] = set()
    for item in cast:
        if not isinstance(item, dict):
            raise _cast_error("인물 바꾸기(cast) 형식이 틀렸습니다.")
        name, char_id, look = item.get("name"), item.get("charId"), item.get("look")
        if not isinstance(name, str) or not isinstance(char_id, str) or look not in REAL_CAST_LOOKS:
            raise _cast_error("인물 바꾸기(cast) 항목에는 name·charId·look(real|anime)이 있어야 합니다.")
        if name not in names or name in used:
            raise _cast_error("이 테이크에 없는 인물이거나 같은 인물이 두 번 있습니다.")
        used.add(name)
        if not _CAST_CHAR_ID_RE.match(char_id):
            raise _cast_error("캐릭터를 찾을 수 없습니다.")
        try:
            char, _ = characters.load_char(owner, char_id)
        except Exception:  # noqa: BLE001 — 남의 캐릭터·없는 캐릭터는 소유자 저장소에 없다.
            char = None
        if not char or char.get("deleted"):
            raise _cast_error("캐릭터를 찾을 수 없습니다.")
        block = _cast_look_block(_cast_entry(owner, char_id, name), look)
        if block:
            raise _cast_error(f"고른 모습({REAL_CAST_LOOK_KO[look]})을 쓸 수 없습니다: {block}")
        out.append({"name": name, "charId": char_id, "look": look, "charName": str(char.get("name") or "")[:80]})
    return out


def _real_cast_overrides(plan: dict) -> dict:
    """지금 실사 회차(plan.gen.realRun.cast)의 인물 바꾸기: 이름 → {charId, look}. 없거나 깨졌으면 {}."""
    run = ((plan or {}).get("gen") or {}).get("realRun") or {}
    items = run.get("cast") if isinstance(run, dict) else None
    out: dict = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("look") in REAL_CAST_LOOKS and item.get("name") and item.get("charId"):
            out[str(item["name"])] = {"charId": str(item["charId"]), "look": str(item["look"])}
    return out


def real_cast_options(owner: str, pid: str) -> dict:
    """[인물 바꿔 다시 만들기] 창의 재료(작업 126). 캐릭터 JSON 을 읽으므로 창을 열 때만 부른다.

    반환 {people:[{name, charId, charName}], characters:[{id, name, realBlock, animeBlock}]}. block 은 그 모습을 고를 수
    없는 이유(한국어, 고를 수 있으면 ""). 캐릭터는 소유자의 목록(지운 것 제외)이다.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    char_by_id = _char_by_id(owner, plan)
    people = []
    for person in _real_take_people(plan, gen):
        entry = char_by_id.get(person["charId"]) or {}
        people.append({**person, "charName": str(entry.get("name") or "")})
    rows = []
    try:
        listed = characters.list_characters(owner)
    except Exception as exc:  # noqa: BLE001 — 목록을 못 읽어도 창은 지금 캐릭터로 연다.
        log.warning("vnew.gen.real_cast_list_failed err=%s", type(exc).__name__)
        listed = []
    for row in listed:
        cid = str((row or {}).get("id") or "")
        if not _CAST_CHAR_ID_RE.match(cid):
            continue
        entry = _cast_entry(owner, cid, str(row.get("name") or ""))
        rows.append({
            "id": cid,
            "name": str(row.get("name") or ""),
            "realBlock": _cast_look_block(entry, "real"),
            "animeBlock": _cast_look_block(entry, "anime"),
        })
    return {"people": people, "characters": rows}


def _real_take_cast(owner: str, plan: dict, actors: list[dict], char_by_id: dict, resolved: dict,
                    cap: int, fallback_step: int = 0, *, costume_location: str | None = None,
                    pid: str = "") -> tuple[list[bytes], list[dict]]:
    """통 테이크 실사 인물 참고·ASSETS 인물(작업 126). 반환 (이미지 바이트 목록, cast).

    인물마다 참고 키: 모습 real → _real_photo_keys(캐스팅 고름 → 정면 → 시트 → 후면), anime → _anime_ref_keys(웹툰 정면 →
    시트). 회차의 인물 바꾸기(_real_cast_overrides)가 있으면 그 캐릭터·모습을 쓴다. 장수는 _ref_counts(cap)로 정하고,
    한 인물의 사진은 이어진 번호다. cast 항목: frontNo(첫 장)·refNos(모든 장)·look·appearance·costume·voice.
    작업 130 likeness 1단계(fallback_step=1): 인물당 정면 1장(_fallback_front_keys). 0 이면 지금 그대로.
    작업 142: 실사 시트가 있고 읽히는 인물은 _real_identity_refs — 0단계 [시트, 정면], 1단계 [시트] 1장. cast 항목에 sheetNo.
    시트가 없거나 못 읽는 인물·애니 모습은 140 그대로(0단계 _real_photo_keys/_anime_ref_keys, 1단계 _fallback_front_keys).
    작업 160: costume_location(장소, None 이면 끔)이 있으면 실사 정면(realFrontKey) 자리를 그 장소 장면 옷 편집 그림(_costume_refs,
    메모리만)으로 바꾸고 cast 항목에 costumeNo. 편집이 없거나 실패한 인물은 지금 그대로.
    """
    overrides = _real_cast_overrides(plan)
    loaded: dict[str, bytes | None] = {}

    def load(key: str) -> bytes | None:
        if key not in loaded:
            loaded[key] = _load_jpeg(key)
        return loaded[key]

    rows = []
    for actor in actors:
        name = actor.get("name") or "a character"
        pick = overrides.get(str(name))
        if pick:
            char_id, look = pick["charId"], pick["look"]
            char = _cast_entry(owner, char_id, str(name))
        else:
            char_id, look = str(actor.get("charId") or ""), "real"
            char = char_by_id.get(char_id) if char_id else None
        sheet = ""
        if char and look != "anime":
            ids = _real_identity_refs(char, look)
            want = str(char.get("realSheetKey") or "")
            if want and ids[:1] == [want] and load(want) is not None:
                sheet = want
        if not char:
            keys = []
        elif sheet:
            keys = ids[:1] if fallback_step == 1 else ids
        elif fallback_step == 1:
            keys = _fallback_front_keys(char, look)
        else:
            keys = _anime_ref_keys(char) if look == "anime" else _real_photo_keys(char)
        rows.append((actor, str(name), char_id, char, look, keys, sheet))
    counts = _ref_counts([len(row[5]) for row in rows], cap)
    swaps: dict[str, bytes] = {}
    if costume_location is not None:
        swaps = _costume_refs(owner, pid, plan, [(row[1], row[3], row[4]) for row in rows], costume_location,
                              {row[1]: str((resolved.get(row[1]) or {}).get("costume") or "") for row in rows}, load,
                              bases=_base_outfits(actors, char_by_id))
    images: list[bytes] = []
    cast: list[dict] = []
    for (actor, name, char_id, char, look, keys, sheet), count in zip(rows, counts):
        nos: list[int] = []
        sheet_no = costume_no = None
        front_key = str((char or {}).get("realFrontKey") or "") if look != "anime" else ""
        for key in keys[:count]:
            photo = load(key)
            if photo is not None:
                if front_key and key == front_key and swaps.get(name):
                    photo = swaps[name]  # 작업 160: 정면 자리 = 장면 옷 편집 그림(메모리만)
                    costume_no = len(images) + 1
                images.append(_fit_ref(photo))
                nos.append(len(images))
                if sheet and key == sheet:
                    sheet_no = len(images)
        if not char:
            appearance = ""
        elif look == "anime":
            appearance = _real_appearance(str(char.get("appearanceEn") or ""))
        else:
            appearance = _real_appearance(_real_look(owner, char_id))
        member = resolved.get(name) or {}
        cast.append({
            "name": name,
            "colorName": actor.get("colorName") or "grey",
            "frontNo": nos[0] if nos else None,
            "refNos": nos,
            "look": look,
            "appearance": appearance,
            "costume": vnew_fx.strip_drawing_words(member.get("costume") or ""),
            "voice": vnew_fx.strip_drawing_words(member.get("voice") or ""),
        })
        if sheet_no:
            cast[-1]["sheetNo"] = sheet_no  # 작업 142: 이 번호는 AI 캐릭터 시트(없는 인물은 키 없음 — 140 과 같은 dict)
        if costume_no:
            cast[-1]["costumeNo"] = costume_no  # 작업 160: 장면 옷 편집 그림 번호(없는 인물은 키 없음 — 159 와 같은 dict)
    return images, cast


# ---------------------------------------------------------------- 작업 160: 장면 옷 신원 참고(메모리만)
# 142 시트·정면은 인물 기본 옷(아이보리 셔츠)을 입고 있어 Seedance·Seedream 이 그 옷을 따라 입혔다(test3 take2 흰 셔츠). 실사 회차마다
# 보이는 인물 1명당 fal Seedream edit 1번(입력 = 실사 정면)으로 "같은 사람, 장면 옷"을 만들어 정면 자리에 쓴다. 바이트는 이 프로세스의
# 메모리(_COSTUME_REF_RUNS, 회차 키, 최근 _COSTUME_REF_RUNS_MAX 회차)에만 두고 S3·STORE 에 쓰지 않는다(보안 규칙). 파드가 바뀌면 다시 만든다.
REAL_COSTUME_REF_PROMPT = "Same person, same face and hair, now wearing {costume}; plain light background; photo."
_COSTUME_REF_RUNS: dict[tuple, dict] = {}
_COSTUME_REF_RUNS_MAX = 4
_COSTUME_REF_LOCK = threading.Lock()


def _seedream_costume(prompt: str, front: bytes, *, size: str) -> bytes:
    """장면 옷 편집 1장(fal Seedream 4.5 edit, 참고 = 실사 정면 1장). 재시도·다시 쓰기 없음(호출 1번 = 원장 1줄). 검사가 바꿔 끼운다."""
    model = vnew_settings.VNEW_REAL_COSTUME_REF_MODEL
    return vpoc_seedream.generate(prompt, [front], model=model, where="vnew.generate.costume_ref", size=size, fal_model=model)


def _charge_costume_ref(owner: str, pid: str, gen_id: str, run_n: int) -> None:
    """장면 옷 편집 1장 원장(그림 1장, fal Seedream 장당 = 공간 시트 편집과 같은 단가)."""
    try:
        vpoc_ledger.charge(f"vnew-gen-{pid}-{gen_id}-costume-r{run_n}-{secrets.token_hex(4)}", kind="image",
                           model=vnew_settings.VNEW_REAL_COSTUME_REF_MODEL, units=1,
                           usd=float(vnew_settings.LOCATION_IMAGE_USD), user=owner, project_id=pid, plan_id=gen_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_costume_ref_failed pid=%s err=%s", pid, str(exc)[:200])


def _costume_refs(owner: str, pid: str, plan: dict, people, location, fallbacks: dict, load=None, *,
                  bases: dict | None = None) -> dict[str, bytes]:
    """작업 160: {이름: 장면 옷 편집 그림 바이트}(메모리만). people = [(이름, 캐릭터 entry, 모습)], 실사 모습·실사 정면이 있는 인물만.

    옷 = _scene_costume(장소, fallback = 그 인물 지금 값). 같은 회차(genId·realRun.n)·같은 인물·같은 정면·같은 옷이면 다시 부르지 않는다
    (실패도 기억 — 이 회차에서 다시 사지 않는다). VNEW_REAL_COSTUME_REF 끔이면 {}(호출 0).
    작업 164: bases = {이름: 캐릭터 기본 옷}(테이크 프롬프트와 같은 후보 · 같은 검사)."""
    if not vnew_settings.VNEW_REAL_COSTUME_REF:
        return {}
    gen = plan.get("gen") or {}
    gen_id = str(gen.get("genId") or "")
    run_n = int((gen.get("realRun") or {}).get("n") or 0)
    run_key = (str(owner), str(pid), gen_id, run_n)
    load = load or _load_jpeg
    out: dict[str, bytes] = {}
    made = failed = 0
    with _COSTUME_REF_LOCK:
        run = _COSTUME_REF_RUNS.get(run_key)
        if run is None:
            run = _COSTUME_REF_RUNS[run_key] = {}
            while len(_COSTUME_REF_RUNS) > _COSTUME_REF_RUNS_MAX:
                _COSTUME_REF_RUNS.pop(next(iter(_COSTUME_REF_RUNS)))
        for name, char, look in people:
            front_key = str((char or {}).get("realFrontKey") or "") if look != "anime" else ""
            costume = _scene_costume(plan, name, location, fallback=vnew_fx.strip_drawing_words(fallbacks.get(name) or ""),
                                     base=str((bases or {}).get(name) or ""))
            if not front_key or not costume:
                continue
            slot = (str(name), front_key, costume)
            if slot not in run:
                front = load(front_key)
                image = None
                if front is not None:
                    try:
                        image = _seedream_costume(REAL_COSTUME_REF_PROMPT.format(costume=costume), _fit_ref(front),
                                                  size=vnew_settings.FRONT_SIZE) or None
                    except Exception as exc:  # noqa: BLE001 — 실패면 그 인물은 지금 참고 그대로
                        log.warning("vnew.gen.costume_ref_failed pid=%s err=%s", pid, _msg(exc))
                    if image:
                        _charge_costume_ref(owner, pid, gen_id, run_n)
                        made += 1
                    else:
                        failed += 1
                run[slot] = image
            if run[slot]:
                out[str(name)] = run[slot]
    if made or failed:
        log.info("vnew.gen.costume_ref pid=%s run=%d made=%d failed=%d stored=0", pid, run_n, made, failed)
    return out


def _model_accepts_real_start(model: str) -> bool:
    """이 모델이 실사 인물 시작 이미지를 받는지(설계자 2026-10-20).

    Seedance(byteplus, ARK_MODEL_IDS)만 실존 인물 필터로 거절한다 → false. Luma·fal 은 true.
    모델이 비거나 모르면 막지 않는다(true) — 기존 흐름을 깨지 않기 위해서다.
    """
    return str(model or "") not in vpoc_settings.ARK_MODEL_IDS




def check_preconditions(plan: dict, owner: str) -> list[dict]:
    """generate 선행 조건(설계자 2026-10-21, 지시서 038 — 프리비즈 기반). 어기면 VpocError(409).

    - 콘티가 ready 여야 한다(구간이 있어야 프리비즈·녹화가 가능).
    - 프리비즈가 **승인(approved)·모든 구간 녹화(recordedAll)** 돼야 한다. 아니면 409 PrevizRequired.
    - 등장 캐릭터 중 실사·웹툰 기본 복장 정면(front)이 없는 캐릭터가 있으면 409(이름 표기).
    - 이미 진행 중(gen.status running, stale 아님)이면 409.
    통과하면 캐릭터 목록을 돌려준다.
    """
    conti = plan.get("conti") or {}
    if conti.get("status") != "ready" or not (conti.get("segments")):
        raise VpocError("vnew.generate", "ContiNotReady", "먼저 콘티를 완성해 주세요.", 409)

    previz = plan.get("previz") or {}
    if not previz.get("approved") or not _previz_recorded_all(plan):
        raise VpocError(
            "vnew.generate",
            "PrevizRequired",
            "프리비즈를 승인하고 녹화해 주세요.",
            409,
        )

    brief = previz.get("shotBrief") or {}
    if brief.get("status") != "ready":
        raise VpocError(
            "vnew.generate",
            "BriefNotReady",
            "촬영 지시서를 만드는 중입니다. 잠시 후 다시 시도해 주세요.",
            409,
        )

    gen = plan.get("gen") or {}
    if gen.get("status") == "running" and not _gen_stale(gen):
        if _gen_previz_stale(gen, plan):
            raise VpocError(
                "vnew.generate",
                "GenRunning",
                "이전 영상 생성이 아직 진행 중입니다. 취소한 뒤 새로 만들어 주세요.",
                409,
            )
        raise VpocError("vnew.generate", "GenRunning", "이미 영상 생성이 진행 중입니다.", 409)

    chars = _char_entries(owner, plan.get("input") or {})
    missing_names = [
        (c.get("roleName") or c.get("name") or c.get("charId"))
        for c in chars
        if c.get("missing") or not c.get("realFrontKey") or not c.get("webtoonFrontKey")
    ]
    if missing_names:
        raise VpocError(
            "vnew.generate",
            "CharacterRealMissing",
            "실사·웹툰 기본 복장 정면이 없는 등장인물이 있습니다: " + ", ".join(str(n) for n in missing_names),
            409,
        )
    return chars


def _previz_recorded_all(plan: dict) -> bool:
    """콘티 구간이 모두 녹화됐는지(previz.recordings 에 key 가 있는지)."""
    conti = plan.get("conti") or {}
    segments = conti.get("segments") or []
    if not segments:
        return False
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    for seg in segments:
        rec = recordings.get(str(seg.get("id") or "")) or {}
        if not rec.get("key"):
            return False
    return True


def _wants_real(styles) -> bool:
    """POST /generate 의 styles(작업 123). None → 웹툰만(지금과 같다). ["webtoon"] | ["webtoon","real"] 만 받는다(그 밖 400)."""
    if styles is None:
        return False
    if not isinstance(styles, list) or not all(isinstance(s, str) for s in styles) \
            or len(set(styles)) != len(styles) or set(styles) not in ({"webtoon"}, {"webtoon", "real"}):
        raise VpocError("vnew.generate", "BadStyles",
                        '만들 영상(styles)은 ["webtoon"] 또는 ["webtoon","real"] 이어야 합니다.', 400)
    return "real" in styles


def start_generate(owner: str, pid: str, confirm_usd: float, styles: list | None = None) -> str:
    """새 genId 로 프리비즈 gen 을 만들고 백그라운드 진행을 시작한다. genId 를 돌려준다.

    금액이 서버 계산과 다르면 409 CostMismatch(vpoc 방식). 선행 조건 위반도 409.
    작업 123: styles 에 "real" 이 있으면(통 테이크 + VNEW_TAKE_REAL 만, 그 밖 400) 금액은 웹툰 + 실사(real_estimate) 합이고,
    gen.autoReal 에 실사 몫을 남겨 웹툰이 끝나면 드라이버가 start_real 로 실사 회차를 1번 시작한다(_maybe_auto_real).
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    want_real = _wants_real(styles)
    if want_real and not vnew_settings.VNEW_TAKE_REAL:
        raise VpocError("vnew.generate", "TakeRealDisabled", "통 테이크 실사 버전 기능이 꺼져 있습니다.", 400)
    if want_real and _gen_mode(plan) != "previz-take":
        raise VpocError("vnew.generate", "RealNotTake", "실사를 함께 만들기는 통 테이크에서만 됩니다.", 400)
    check_preconditions(plan, owner)

    new = _new_take_gen(plan) if _gen_mode(plan) == "previz-take" else _new_previz_gen(plan)
    expected = estimate(plan)["totalUsd"]
    real_usd = real_estimate(new)["usd"] if want_real else 0.0
    if want_real:
        expected = round(expected + real_usd, 2)
    if round(float(confirm_usd), 2) != expected:
        raise VpocError(
            "vnew.generate",
            "CostMismatch",
            f"금액이 서버 계산과 다릅니다. 서버 {expected} USD, 화면 {round(float(confirm_usd), 2)} USD.",
            409,
        )
    if want_real:
        new["autoReal"] = {"confirmUsd": real_usd, "audio": bool(vnew_settings.VNEW_TAKE_AUDIO), "at": now_iso()}

    def mutator(data: dict) -> None:
        current = data.get("gen") or {}
        if current.get("status") == "running" and not _gen_stale(current):
            # 진행 중인 gen 은 교체하지 않는다. 프리비즈가 바뀐(previz-stale) 진행 중이면
            # 취소 먼저 하라고 안내한다(설계자 2026-10-22, ⑤).
            if _gen_previz_stale(current, data):
                raise VpocError(
                    "vnew.generate",
                    "GenRunning",
                    "이전 영상 생성이 아직 진행 중입니다. 취소한 뒤 새로 만들어 주세요.",
                    409,
                )
            raise VpocError("vnew.generate", "GenRunning", "이미 영상 생성이 진행 중입니다.", 409)
        # 이전 gen(취소·실패·완료·시간초과)은 genHistory 에 요약만 남기고 새 gen 으로 교체한다.
        # 이전 gen 의 원장·파일은 건드리지 않는다(최근 3개만 보관).
        if current and current.get("genId"):
            history = list(data.get("genHistory") or [])
            history.append(
                {
                    "genId": current.get("genId"),
                    "status": current.get("status"),
                    "createdAt": current.get("createdAt"),
                    "finalReal": (current.get("finalReal") or {}).get("key") or "",
                    "finalWebtoon": (current.get("finalWebtoon") or {}).get("key") or "",
                }
            )
            data["genHistory"] = history[-3:]
        data["gen"] = new

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn_driver(owner, pid, new["genId"])
    return new["genId"]


# ================================================================ 통 테이크 실사 버전(vnew-live-fx 묶음 C)
#
# 웹툰 통 테이크가 끝난 gen 에서 **같은 프리비즈 녹화(unit.recKeys, @Video1)** 로 실사 회차를 만든다. 실사 칸은
# 웹툰과 같은 드라이버(_drive_previz_pass)·선점·준비 세마포어(_PREPARE_SEM)·동시 상한(VNEW_GEN_PARALLEL)으로 간다.
# 회차 번호는 gen.realRun.n, 이전 회차 요약은 gen.realHistory(최대 3), 병합본은 final-real-r{n}.mp4 다.

REAL_HISTORY_MAX = 3


def _require_take_real(where: str = "vnew.generate.real") -> None:
    """VNEW_TAKE_REAL 이 꺼져 있으면 404 TakeRealDisabled(새 라우트가 없는 것과 같다, R0.3)."""
    if not vnew_settings.VNEW_TAKE_REAL:
        raise VpocError(where, "TakeRealDisabled", "통 테이크 실사 버전 기능이 꺼져 있습니다.", 404)


def real_estimate(gen: dict) -> dict:
    """실사 회차 1번 금액(design §C.4). 테이크마다 _take_usd(실사 테이크 모델, unit.seconds, gen.resolution) 합.

    작업 156: 모델은 _real_take_model(gen)(VNEW_REAL_TAKE_MODEL, 애니 모델과 따로). 반환 {usd(소수 2자리), seconds, takes, resolution}.
    """
    gen = gen if isinstance(gen, dict) else {}
    endpoint = _real_take_model(gen)
    resolution = str(gen.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION)
    usd = 0.0
    seconds = 0.0
    takes = 0
    for unit in gen.get("units") or []:
        secs = float(unit.get("seconds") or 0)
        usd += _take_usd(endpoint, secs, resolution)
        seconds += secs
        takes += 1
    return {"usd": round(usd, 2), "seconds": round(seconds, 3), "takes": takes, "resolution": resolution}


def _real_ready_error(gen: dict) -> VpocError | None:
    """실사 회차를 시작할 수 없는 gen 상태(설정·캐릭터 제외). 시작할 수 있으면 None(design §C.4 1)."""
    gen = gen if isinstance(gen, dict) else {}
    if gen.get("mode") != "previz-take" or (gen.get("finalWebtoon") or {}).get("status") != "done":
        return VpocError(
            "vnew.generate.real",
            "RealNotReady",
            "웹툰 통 테이크가 끝난 뒤에 실사 버전을 만들 수 있습니다.",
            409,
        )
    if gen.get("status") == "running" and not _gen_stale(gen):
        return VpocError("vnew.generate.real", "RealRunning", "영상 생성이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.", 409)
    return None


def _real_missing_names(owner: str, plan: dict, gen: dict) -> list[str]:
    """테이크에 나오는 인물(charId 가 있는 배우) 중 캐스팅 사진도 실사 정면도 없는 인물 이름(RC1.3)."""
    char_by_id = _char_by_id(owner, plan)
    names: list[str] = []
    for unit in gen.get("units") or []:
        for actor in _segment_actors(plan, unit):
            char_id = str(actor.get("charId") or "")
            if not char_id:
                continue
            entry = char_by_id.get(char_id)
            if not entry or entry.get("missing") or not _real_front_key(entry):
                name = str(actor.get("name") or (entry or {}).get("roleName") or char_id)
                if name not in names:
                    names.append(name)
    return names


def _real_run_summary(gen: dict) -> dict:
    """끝난(또는 멈춘) 실사 회차 요약 → realHistory 항목(design §C.1). 파일은 지우지 않는다."""
    run = gen.get("realRun") or {}
    final = gen.get("finalReal") or {}
    units = []
    for unit in gen.get("units") or []:
        state = unit.get("real") or {}
        # 작업 134: 컷 단위 실사 칸이면 이어 붙인 컷 목록 {cutId, outputKey, qa(QA 로 다시 만든 쪽)}(없으면 키 없음 — 옛 요약과 같다).
        cuts = state.get("mergedCuts") or _real_cut_list((state.get("fallback") or {}).get("cuts"))
        units.append({
            "id": str(unit.get("id") or ""),
            "status": str(state.get("status") or ""),
            "outputKey": str(state.get("outputKey") or ""),
            "error": str(state.get("error") or "")[:300],
            **({"cuts": [dict(c) for c in cuts]} if cuts else {}),
        })
    states = [u["status"] for u in units]
    if final.get("status") == "done":
        status = "done"
    elif "cancelled" in states:
        status = "cancelled"
    elif final.get("status") == "failed" or (states and all(s == "failed" for s in states)):
        status = "failed"
    else:
        status = "partial"
    return {
        "n": int(run.get("n") or 0),
        "startedAt": str(run.get("startedAt") or ""),
        "status": status,
        "finalKey": str(final.get("key") or "") if final.get("status") == "done" else "",
        # 작업 134: remerge 로 고친 회차는 원래(깨진) 병합본 키를 남긴다.
        **({"finalKeyBroken": str(final["finalKeyBroken"])} if final.get("finalKeyBroken") else {}),
        "audio": bool(run.get("audio")),
        "units": units,
        # 작업 126: 인물 바꾸기 회차만 cast 를 남긴다(없으면 키 없음 — 옛 요약과 같다).
        **({"cast": list(run["cast"])} if isinstance(run.get("cast"), list) and run.get("cast") else {}),
    }


def _empty_real_state(audio: bool) -> dict:
    """실사 회차 칸 초기값(design §C.1: _empty_variant_state + audio·failureKind·promptEn·promptBytes)."""
    return {**_empty_variant_state(), "status": "queued", "audio": bool(audio), "failureKind": "", "promptEn": "",
            "promptBytes": 0}


def start_real(owner: str, pid: str, confirm_usd: float, audio: bool | None = None, cast=None) -> dict:
    """웹툰 통 테이크가 끝난 gen 에서 실사 회차를 시작한다(design §C.4). 반환 {n, usd}.

    금액(confirm_usd)이 real_estimate 와 다르면 409 CostMismatch(원장 기록용이고 생성을 막지 않는다 — 기존과 같다).
    이전 회차가 있으면 realHistory 에 요약을 넣고(최대 3, 오래된 것부터 버림) 칸을 모두 queued 로 되돌린다.
    웹툰 결과(unit.webtoon·finalWebtoon)는 건드리지 않는다. selected 가 real 이면 비운다(RC4.4).
    작업 126: cast(선택, [{name, charId, look}])는 _validate_real_cast 를 지나 realRun.cast 에만 저장한다(400 BadCast).
    plan.input·대본·프리비즈는 바꾸지 않는다. 바꾼 인물은 원래 캐릭터의 실사 사진이 없어도 막지 않는다.
    """
    _require_take_real()
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    blocked = _real_ready_error(gen)
    if blocked is not None:
        raise blocked
    cast_items = _validate_real_cast(owner, plan, gen, cast)
    if not vpoc_settings.FAL_KEY:
        raise VpocError("vnew.generate.real", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    replaced = {item["name"] for item in cast_items or []}
    missing = [name for name in _real_missing_names(owner, plan, gen) if name not in replaced]
    if missing:
        raise VpocError(
            "vnew.generate.real",
            "CharacterRealMissing",
            "실사 사진(캐스팅 사진 또는 실사 정면)이 없는 등장인물이 있습니다: " + ", ".join(missing),
            409,
        )
    est = real_estimate(gen)
    shown = round(float(confirm_usd), 2)
    if shown != est["usd"]:
        raise VpocError(
            "vnew.generate.real",
            "CostMismatch",
            f"금액이 서버 계산과 다릅니다. 서버 {est['usd']} USD, 화면 {shown} USD.",
            409,
        )
    gen_id = str(gen.get("genId") or "")
    audio_on = bool(vnew_settings.VNEW_TAKE_AUDIO if audio is None else audio)
    box = {"n": 0}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id:
            raise VpocError("vnew.generate.real", "RealNotReady",
                            "웹툰 통 테이크가 끝난 뒤에 실사 버전을 만들 수 있습니다.", 409)
        again = _real_ready_error(g)  # 동시 시작 막기: 저장 직전에 running 을 다시 본다.
        if again is not None:
            raise again
        previous = g.get("realRun") or {}
        if previous.get("n"):
            history = list(g.get("realHistory") or [])
            history.append(_real_run_summary(g))
            g["realHistory"] = history[-REAL_HISTORY_MAX:]
        n = int(previous.get("n") or 0) + 1
        for unit in g.get("units") or []:
            unit["real"] = _empty_real_state(audio_on)
        g["finalReal"] = {"status": "waiting", "key": "", "error": "", "n": n}
        g["realRun"] = {
            "n": n,
            "startedAt": now_iso(),
            "audio": audio_on,
            "usd": est["usd"],
            "endpoint": _real_take_model(g),  # 작업 156: 실사 테이크 모델(애니 모델과 따로)
            "resolution": est["resolution"],
        }
        if cast_items:
            g["realRun"]["cast"] = [dict(item) for item in cast_items]
        g["activeSince"] = now_iso()
        g["status"] = "running"
        if g.get("selected") == "real":
            g["selected"] = None
        box["n"] = n

    _mutate_locked(owner, pid, mutator)
    _spawn_driver(owner, pid, gen_id)
    log.info(
        "vnew.gen.real_start pid=%s n=%d takes=%d usd=%.2f audio=%s",
        pid, box["n"], est["takes"], est["usd"], "on" if audio_on else "off",
    )
    if cast_items:
        log.info("vnew.gen.real_cast pid=%s n=%d people=%d anime=%d", pid, box["n"], len(cast_items),
                 sum(1 for item in cast_items if item["look"] == "anime"))
    return {"n": box["n"], "usd": est["usd"]}


def _real_block_text(owner: str, plan: dict, gen: dict) -> str:
    """gen_view 의 realBlock(실사 버전을 시작하지 못하는 이유, 한국어). 시작할 수 있으면 ""."""
    if not vnew_settings.VNEW_TAKE_REAL:
        return "실사 버전 기능이 꺼져 있습니다."
    blocked = _real_ready_error(gen)
    if blocked is not None:
        return blocked.message
    if not vpoc_settings.FAL_KEY:
        return "fal API 키가 설정되지 않았습니다."
    try:
        missing = _real_missing_names(owner, plan, gen)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다(시작 때 다시 확인한다).
        log.warning("vnew.gen.real_block_failed err=%s", type(exc).__name__)
        return ""
    if missing:
        return "실사 사진(캐스팅 사진 또는 실사 정면)이 없는 등장인물이 있습니다: " + ", ".join(missing)
    return ""


# ---------------------------------------------------------------- 작업 123: 처음부터 실사도 함께(autoReal)
#
# POST /generate {styles:["webtoon","real"]} 이면 gen.autoReal = {confirmUsd(실사 몫), audio, at}. 드라이버가 웹툰 통 테이크를
# 다 끝내고(gen done · finalWebtoon done = canStartReal 이 true 가 되는 때) start_real 을 그대로 1번 부른다. 선점은
# autoReal.startedAt(저장 뒤에 부른다 → 같은 gen 에서 두 번 시작하지 않는다), 실패·건너뜀은 autoReal.error.

AUTO_REAL_SKIP_TEXT = "웹툰이 모두 끝나지 않아 실사를 자동으로 시작하지 않았습니다."
AUTO_REAL_CANCEL_TEXT = "취소해서 실사를 자동으로 시작하지 않았습니다."


def _take_start_real_block(owner: str, plan: dict) -> str:
    """⑤ 시작 확인창의 [실사] 체크를 흐리게 할 이유(realBlock 문구, 웹툰 진행 상태 제외). 만들 수 있으면 ""."""
    if not vnew_settings.VNEW_TAKE_REAL:
        return "실사 버전 기능이 꺼져 있습니다."
    if not vpoc_settings.FAL_KEY:
        return "fal API 키가 설정되지 않았습니다."
    try:
        missing = _real_missing_names(owner, plan, {"units": _take_units(plan)})
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다(자동 시작 때 start_real 이 다시 확인한다).
        log.warning("vnew.gen.auto_real_block_failed err=%s", type(exc).__name__)
        return ""
    if missing:
        return "실사 사진(캐스팅 사진 또는 실사 정면)이 없는 등장인물이 있습니다: " + ", ".join(missing)
    return ""


def _auto_real_mark(owner: str, pid: str, gen_id: str, field: str, value: str) -> bool:
    """autoReal 에 startedAt 또는 error 를 한 번만 남긴다(이미 시작했거나 오류가 있으면 그대로). 남겼으면 True."""
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        auto = g.get("autoReal")
        if str(g.get("genId") or "") != gen_id or not isinstance(auto, dict) or auto.get("error") \
                or (field == "startedAt" and auto.get("startedAt")):
            raise _Discard()
        auto[field] = value

    try:
        _mutate_locked(owner, pid, mutator)
    except _Discard:
        return False
    return True


def _maybe_auto_real(owner: str, pid: str, gen_id: str) -> bool:
    """웹툰 통 테이크가 끝난 gen 에 autoReal 이 있으면 start_real 로 실사 회차를 1번 시작한다. 시작했으면 True.

    웹툰이 일부 실패(partial)·실패·취소로 끝났으면 시작하지 않고 autoReal.error 를 남긴다. 작업 163: 실패한 웹툰 칸을 다시 시도하면
    retry_segment 가 그 건너뜀 표시(AUTO_REAL_SKIP_TEXT)를 지우므로 다시 done 이 되면 여기서 1번 시작한다(취소·거절 문구는 그대로).
    start_real 이 거절하면 그 문구를 autoReal.error 에 남긴다.
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return False
    gen = plan.get("gen") or {}
    auto = gen.get("autoReal")
    if str(gen.get("genId") or "") != gen_id or not isinstance(auto, dict) or auto.get("startedAt") or auto.get("error") \
            or gen.get("mode") != "previz-take" or gen.get("realRun") or gen.get("status") == "running":
        return False
    if gen.get("status") != "done" or (gen.get("finalWebtoon") or {}).get("status") != "done":
        _auto_real_mark(owner, pid, gen_id, "error", AUTO_REAL_SKIP_TEXT)
        log.info("vnew.gen.auto_real_skip pid=%s status=%s", pid, gen.get("status"))
        return False
    if not _auto_real_mark(owner, pid, gen_id, "startedAt", now_iso()):
        return False
    try:
        run = start_real(owner, pid, float(auto.get("confirmUsd") or 0), audio=bool(auto.get("audio")))
    except Exception as exc:  # noqa: BLE001 — 자동 시작 실패는 화면의 [실사 버전 만들기] 로 넘긴다.
        message = exc.message if isinstance(exc, VpocError) else "실사 버전을 자동으로 시작하지 못했습니다."
        _auto_real_mark(owner, pid, gen_id, "error", str(message)[:300])
        log.warning("vnew.gen.auto_real_failed pid=%s kind=%s", pid,
                    exc.kind if isinstance(exc, VpocError) else type(exc).__name__)
        return False
    log.info("vnew.gen.auto_real_start pid=%s n=%d usd=%.2f", pid, run["n"], run["usd"])
    return True


# ================================================================ 진행 드라이버
#
# vpoc _ensure_driver 개념: 파드당 계획 1개. 드라이버 스레드가 한 번에 하나씩 진행한다.
# mutate 안에서는 preparing+claim 만 하고, 외부 호출(Seedream·영상·ffmpeg·fal)은 저장 성공 뒤
# 스레드에서, 결과는 claim 확인 후 기록한다(016-fix2 중복 제출 방지).

_drivers: dict[str, threading.Thread] = {}
_drivers_lock = threading.Lock()

# 프리비즈 모드는 한 순회에서 여러 칸을 **동시에** 제출/조회하므로, 같은 계획(plan)에 대한
# 읽기-수정-조건부쓰기(mutate_plan)가 스레드끼리 겹친다. 실제 S3 는 etag 조건부쓰기로 경합을
# 재시도하지만, 그 창(window) 안에서도 안전하도록 **같은 계획(owner,pid)별 프로세스 락**으로
# mutate 를 직렬화한다(이중 제출은 claim 확인이 막고, 락은 갱신 유실을 막는다).
_plan_locks: dict[str, threading.Lock] = {}
_plan_locks_guard = threading.Lock()


def _plan_lock(owner: str, pid: str) -> threading.Lock:
    key = f"{owner}/{pid}"
    with _plan_locks_guard:
        lock = _plan_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _plan_locks[key] = lock
        return lock


def _mutate_locked(owner: str, pid: str, mutator):
    """mutate_plan 을 계획별 락 안에서 수행(프리비즈 동시 제출의 갱신 유실 방지)."""
    with _plan_lock(owner, pid):
        return plan_mod.mutate_plan(owner, pid, mutator)


def _touch_prepare_claim(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> bool:
    """작업 144: preparing 칸의 claimedAt 을 지금으로 갱신한다(하트비트, _reconcile_prepare_stale_previz 가 되돌리지 않게).

    저장된 claim 이 우리 것이고 칸이 preparing 일 때만 쓴다. 아니면(취소·stale 뒤 재선점) 쓰지 않고 False —
    호출한 쪽은 더 돈을 쓰지 않고 멈춘다(vnew.gen.prepare_claim_lost).
    """

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        state = _previz_cells(g)[index][variant]
        if state.get("status") != "preparing":
            raise _Discard()
        state["claimedAt"] = now_iso()

    try:
        _mutate_locked(owner, pid, mutator)
    except _Discard:
        log.info("vnew.gen.prepare_claim_lost pid=%s gen=%s take=%s variant=%s", pid, gen_id, index, variant)
        return False
    return True



def _driver_name(owner: str, pid: str) -> str:
    return f"vnew-gen-{owner}-{pid}"


def _spawn_driver(owner: str, pid: str, gen_id: str) -> None:
    name = _driver_name(owner, pid)
    with _drivers_lock:
        existing = _drivers.get(name)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(target=_drive, args=(owner, pid, gen_id), daemon=True, name=name)
        _drivers[name] = thread
        thread.start()


def ensure_driver(owner: str, pid: str) -> None:
    """조회 때 호출: gen 이 running 이면 드라이버가 살아 있게 한다(파드 재시작 대비)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if gen.get("status") == "running":
        _spawn_driver(owner, pid, str(gen.get("genId") or ""))


def _drive(owner: str, pid: str, gen_id: str) -> None:
    """드라이버 본체. 한 단계씩 진행하며, 외부 호출을 하나 수행하고 결과를 기록한 뒤 다시 돈다.

    각 반복에서: (1) preparing 선점 해제(stale) (2) opening → 각 실사 구간 순서대로 →
    실사 완료마다 웹툰 변환 → 모두 끝나면 병합. 더 할 일이 없으면 종료.

    submitted 구간을 조회(poll)했는데 아직 InProgress 면 상태가 바뀌지 않아 같은 일을 또 고른다
    — 그때는 잠깐 쉰 뒤 다시 조회한다(바쁜 대기 방지). 상태가 바뀌면 바로 다음 일을 잇는다.
    """
    import time  # noqa: PLC0415

    for _ in range(100000):  # 안전 상한(무한 루프 방지).
        try:
            plan, _ = plan_mod.load_plan(owner, pid)
        except VpocError:
            return
        gen = plan.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            return

        if gen.get("mode") in ("previz", "previz-take"):
            # 프리비즈 모드: 한 순회에서 submitted 전부 조회 + queued 를 상한까지 동시 제출(병렬).
            progressed = _drive_previz_pass(owner, pid, gen_id)
            if progressed is None:
                return  # 더 할 일 없음(병합·상태 확정까지 끝).
            if progressed == "poll_only":
                # 새로 제출한 칸이 없고 submitted 만 남았다 → 잠깐 쉬고 다시 조회(각 칸 POLL 간격).
                if _still_in_progress(owner, pid, gen_id):
                    time.sleep(vnew_settings.POLL_INTERVAL_S)
            continue

        action = _next_action(owner, pid, gen_id)
        if action is None:
            return  # 더 할 일 없음(완료·partial — poll 로 조회할 submitted 도 없음).
        if action in ("poll_real", "poll_webtoon"):
            # 조회 직후 아직 완료 전이면 상태가 그대로다. 잠깐 쉰다(설정값 재사용).
            if _still_in_progress(owner, pid, gen_id):
                time.sleep(vnew_settings.POLL_INTERVAL_S)


def _still_in_progress(owner: str, pid: str, gen_id: str) -> bool:
    """submitted(조회 대기) 구간이 아직 남아 있으면 True(쉬고 다시 조회해야 함)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return False
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
        return False
    cells = _previz_cells(gen) if _is_previz_mode(gen) else (gen.get("segments") or [])
    for seg in cells:
        for variant in ("real", "webtoon"):
            if (seg.get(variant) or {}).get("status") == "submitted":
                return True
    return False


def _reconcile_prepare_stale(gen: dict) -> bool:
    """preparing 이 15분 초과면 queued 로 되돌린다(선점 해제). 바뀌면 True."""
    changed = False
    now = datetime.now(timezone.utc)
    for seg in gen.get("segments") or []:
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "preparing":
                claimed = plan_mod._parse_iso(state.get("claimedAt") or "")
                if not claimed or (now - claimed).total_seconds() > PREPARE_STALE_S:
                    state["status"] = "queued"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    changed = True
    opening = gen.get("opening") or {}
    if opening.get("status") == "preparing":
        claimed = plan_mod._parse_iso(opening.get("claimedAt") or "")
        if not claimed or (now - claimed).total_seconds() > PREPARE_STALE_S:
            opening["status"] = "waiting"
            opening["claim"] = ""
            opening["claimedAt"] = ""
            changed = True
    return changed


def _next_action(owner: str, pid: str, gen_id: str):
    """다음에 할 일 하나를 골라 선점(claim)하고 수행한다. 수행했으면 action 이름, 없으면 None.

    순서(설계자 2026-10-20):
      1. opening(실사 시작 장면) 그리기
      2. 실사 구간을 순서대로 하나씩: 앞 구간 완료 전에는 다음 구간을 건드리지 않는다.
         - 완료된 실사 구간은 last frame 을 다음 구간 firstFrameKey 로 저장한다.
      3. submitted 실사·웹툰 구간은 조회(poll)로 완료/실패 확정.
      4. 실사 완료마다 그 구간 webtoon queued → 제출(병렬 가능).
      5. 모두 끝나면 병합.
    """
    claim = secrets.token_hex(8)
    # --- 1) stale 선점 해제 + opening 선점 ---
    picked = {"kind": None}

    def mutator(data: dict) -> None:
        gen = data.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            picked["kind"] = None
            return
        _reconcile_prepare_stale(gen)

        opening = gen.get("opening") or {}
        segments = gen.get("segments") or []

        # opening 먼저.
        if opening.get("status") in ("waiting", ""):
            opening["status"] = "preparing"
            opening["claim"] = claim
            opening["claimedAt"] = now_iso()
            picked["kind"] = "opening"
            return
        # opening 이 완료돼야 구간을 시작한다. preparing(다른 호출 진행 중)·failed 면 여기서 멈춘다
        # (failed opening 은 retry 로만 다시 시도).
        if opening.get("status") != "done":
            picked["kind"] = None
            return

        # --- 실사 구간: 순서대로 하나씩 ---
        # submitted 실사가 있으면 그것부터 조회한다(완료 전에는 다음으로 안 간다).
        for index, seg in enumerate(segments):
            real = seg["real"]
            if real["status"] == "submitted":
                picked["kind"] = "poll_real"
                picked["segIndex"] = index
                picked["claim"] = real.get("claim") or ""
                return
            if real["status"] in ("failed", "cancelled"):
                # 실패한 구간 뒤로는 진행하지 않는다(partial). retry 로만.
                break
            if real["status"] in ("waiting", "queued"):
                # 첫 프레임이 준비됐는지 확인(1구간=opening, 그 뒤=앞 구간 last frame).
                first_key = _first_frame_for(gen, index)
                if not first_key:
                    # 앞 구간이 아직 안 끝났다 → 여기서 멈춘다(순서 보장).
                    picked["kind"] = None
                    return
                real["status"] = "preparing"
                real["claim"] = claim
                real["claimedAt"] = now_iso()
                real["attempt"] = int(real.get("attempt") or 0) + 1
                real["firstFrameKey"] = first_key
                picked["kind"] = "submit_real"
                picked["segIndex"] = index
                return
            # completed 면 다음 구간으로.

        # --- 웹툰 변환: 실사 완료 구간 중 webtoon 이 할 일 있는 것(병렬) ---
        for index, seg in enumerate(segments):
            real = seg["real"]
            web = seg["webtoon"]
            if real["status"] != "completed":
                continue
            if web["status"] == "submitted":
                picked["kind"] = "poll_webtoon"
                picked["segIndex"] = index
                picked["claim"] = web.get("claim") or ""
                return
            if web["status"] in ("waiting", "queued"):
                web["status"] = "preparing"
                web["claim"] = claim
                web["claimedAt"] = now_iso()
                web["attempt"] = int(web.get("attempt") or 0) + 1
                picked["kind"] = "submit_webtoon"
                picked["segIndex"] = index
                return

        picked["kind"] = None

    plan_mod.mutate_plan(owner, pid, mutator)
    kind = picked.get("kind")
    if kind is None:
        _maybe_finalize(owner, pid, gen_id)
        return None

    # --- 저장 성공 뒤, 외부 호출을 스레드(현재) 에서 수행하고 결과를 claim 확인 후 기록 ---
    if kind == "opening":
        _do_opening(owner, pid, gen_id, claim)
    elif kind == "submit_real":
        _do_submit_real(owner, pid, gen_id, picked["segIndex"], claim)
    elif kind == "poll_real":
        _do_poll_real(owner, pid, gen_id, picked["segIndex"], picked.get("claim") or "")
    elif kind == "submit_webtoon":
        _do_submit_webtoon(owner, pid, gen_id, picked["segIndex"], claim)
    elif kind == "poll_webtoon":
        _do_poll_webtoon(owner, pid, gen_id, picked["segIndex"], picked.get("claim") or "")
    return kind


def _first_frame_for(gen: dict, index: int) -> str:
    """index 구간 실사의 첫 프레임 키. 1구간=opening, 그 뒤=앞 구간 last frame(firstFrameKey)."""
    if index == 0:
        opening = gen.get("opening") or {}
        return str(opening.get("imageKey") or "") if opening.get("status") == "done" else ""
    prev = (gen.get("segments") or [])[index - 1]
    prev_real = prev.get("real") or {}
    if prev_real.get("status") != "completed":
        return ""
    # 앞 구간 완료 시 저장해 둔 다음 구간 firstFrameKey.
    cur = (gen.get("segments") or [])[index]
    return str((cur.get("real") or {}).get("firstFrameKey") or "")


# ================================================================ 프리비즈 모드 드라이버(설계자 2026-10-21)
#
# 옛 모드와 달리 순서·opening 이 없다. 구간×그림체(real/webtoon) 를 **모두 병렬로** 제출한다.
# 각 variant 는 queued → preparing(선점) → submitted → completed/failed. 모두 completed 면
# 구간 순서대로 concat → finalReal/finalWebtoon.


def _previz_cells(gen: dict) -> list[dict]:
    """프리비즈 gen 의 작업 목록. 새 gen 은 units(컷 단위), 옛 gen 은 segments(구간 단위).

    설계자 2026-10-22(⑤): 생성 단위를 컷으로 바꿨다. units 가 있으면 그것을, 없으면 옛 segments 를
    쓴다(옛 데이터 보기·진행 호환). 반환 리스트 각 항목은 {real, webtoon} variant 상태를 담는다.
    """
    units = gen.get("units")
    if isinstance(units, list) and units:
        return units
    return gen.get("segments") or []


def _is_previz_mode(gen: dict) -> bool:
    """프리비즈 계열(컷 단위 previz · 통 테이크 previz-take) 모드인지(설계자 2026-10-26)."""
    return str((gen or {}).get("mode") or "") in ("previz", "previz-take")


def _drive_previz_pass(owner: str, pid: str, gen_id: str):
    """프리비즈 모드 한 순회(설계자 2026-10-21, 지시서 041 · 2026-10-22 컷 단위). 반환:

    - None: 더 할 일이 없다(병합·상태 확정까지 끝냈다 → 드라이버 종료).
    - "work": 새로 제출한 칸이 있다(바로 다음 순회로).
    - "poll_only": 새 제출은 없고 submitted(조회 대기)만 있었다(쉬고 다시 조회).

    한 순회에서:
      1) preparing stale 선점 해제.
      2) submitted(조회 대기) 칸을 **전부** 모은다(각 칸 스레드로 조회 — 각 칸 POLL 간격 준수).
      3) queued/waiting 칸을 **동시 실행 상한(VNEW_GEN_PARALLEL) 안에서 선점해 동시에 제출**한다.
         상한은 "진행 중(preparing+submitted)" 합이 넘지 않게 한다 → 이미 submitted 가 많으면
         이번 순회엔 적게(또는 0) 제출한다. 제출·조회는 칸별 스레드로 병렬 수행하고 모두 끝날 때까지
         기다린다(이 순회가 동기적으로 끝나야 다음 순회가 상태를 다시 본다).
      선점·claim 확인·원장은 기존 규칙 그대로(이중 제출 금지). 칸 = 컷×그림체(units).
    """
    cap = max(1, int(vnew_settings.VNEW_GEN_PARALLEL or 1))
    polls: list[dict] = []    # [{index, variant, claim}]
    submits: list[dict] = []  # [{index, variant, claim}]

    def mutator(data: dict) -> None:
        gen = data.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            return
        _reconcile_prepare_stale_previz(gen)
        cells = _previz_cells(gen)

        # 2) submitted 전부 조회 대상으로.
        active = 0  # 진행 중(preparing+submitted) — 상한 계산용.
        for index, seg in enumerate(cells):
            for variant in ("real", "webtoon"):
                state = seg.get(variant) or {}
                status = state.get("status")
                if status == "submitted":
                    active += 1
                    polls.append({"index": index, "variant": variant, "claim": state.get("claim") or ""})
                elif status == "preparing":
                    active += 1

        # 3) 상한 안에서 queued/waiting 선점(칸마다 새 claim).
        budget = max(0, cap - active)
        for index, seg in enumerate(cells):
            if budget <= 0:
                break
            for variant in ("real", "webtoon"):
                if budget <= 0:
                    break
                state = seg.get(variant) or {}
                if state.get("status") in ("waiting", "queued"):
                    claim = secrets.token_hex(8)
                    state["status"] = "preparing"
                    state["claim"] = claim
                    state["claimedAt"] = now_iso()
                    state["attempt"] = int(state.get("attempt") or 0) + 1
                    submits.append({"index": index, "variant": variant, "claim": claim})
                    budget -= 1

    _mutate_locked(owner, pid, mutator)

    if not polls and not submits:
        _maybe_finalize_previz(owner, pid, gen_id)
        # 작업 123: 웹툰 통 테이크가 끝났고 autoReal 이 있으면 실사 회차를 1번 시작한다 → 이 드라이버가 이어서 돈다.
        if _maybe_auto_real(owner, pid, gen_id):
            return "work"
        return None

    # 칸별 스레드로 병렬 수행하고 모두 끝날 때까지 기다린다.
    workers: list[threading.Thread] = []
    for job in polls:
        workers.append(
            threading.Thread(
                target=_do_poll_previz,
                args=(owner, pid, gen_id, job["index"], job["variant"], job["claim"]),
                daemon=True,
                name=f"vnew-gen-poll-{pid}-{job['index']}-{job['variant']}",
            )
        )
    for job in submits:
        workers.append(
            threading.Thread(
                target=_do_submit_previz,
                args=(owner, pid, gen_id, job["index"], job["variant"], job["claim"]),
                daemon=True,
                name=f"vnew-gen-submit-{pid}-{job['index']}-{job['variant']}",
            )
        )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    return "work" if submits else "poll_only"


def _reconcile_prepare_stale_previz(gen: dict) -> bool:
    """preparing 이 stale 기준을 넘으면 queued 로 되돌린다(선점 해제, 프리비즈 모드).

    기준은 VNEW_PREPARE_STALE_S(기본 300초, 설계자 2026-10-23 ⑤ OOM 완화). 파드가 OOMKilled 되어
    드라이버 스레드가 사라져도 이 시간 뒤 다른(또는 재시작된) 드라이버가 다시 선점해 재시도한다.
    """
    changed = False
    now = datetime.now(timezone.utc)
    stale_s = max(1, int(vnew_settings.VNEW_PREPARE_STALE_S or PREPARE_STALE_S))
    for seg in _previz_cells(gen):
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "preparing":
                claimed = plan_mod._parse_iso(state.get("claimedAt") or "")
                if not claimed or (now - claimed).total_seconds() > stale_s:
                    state["status"] = "queued"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    changed = True
    return changed


def _cut_clip(video_mp4: bytes, unit: dict) -> dict:
    """녹화 mp4 에서 이 컷 구간을 잘라 fal 가 받는 형태로 만든다(설계자 2026-10-22, ⑤).

    - 컷 [startSec, endSec) 를 trim(프레임 정확 재인코딩)으로 자른다.
    - 3초 미만이면 마지막 프레임 복제(pad_to_seconds)로 3초까지 채운다. 채운 길이는 origSeconds 로
      기록해 합칠 때 원래 길이로 자른다.
    - 높이 720 미만이면 scale_to_min_height 로 키운다(기존 규칙 재사용).
    반환: {"bytes": 준비된 mp4, "origSeconds": 원래 컷 길이, "sentSeconds": 제출한 길이, "padded": bool}.
    """
    start = float(unit.get("startSec") or 0.0)
    end = float(unit.get("endSec") or 0.0)
    orig_seconds = max(0.0, end - start)
    with tempfile.TemporaryDirectory(prefix="vnew-cut-") as workdir:
        src = os.path.join(workdir, "rec.mp4")
        with open(src, "wb") as handle:
            handle.write(video_mp4)
        # 녹화 실측 길이로 end 를 clamp(비례 배분 오차 방지).
        rec_dur = stitcher.probe_duration(src) or end
        clip_end = min(end, rec_dur) if rec_dur else end
        if clip_end <= start:
            clip_end = start + orig_seconds if orig_seconds > 0 else start + PREVIZ_MIN_CLIP_S
        cut_path = os.path.join(workdir, "cut.mp4")
        stitcher.trim(src, cut_path, start_sec=start, end_sec=clip_end, threads=1)
        current = cut_path
        cut_dur = stitcher.probe_duration(current) or orig_seconds

        padded = False
        sent_seconds = cut_dur
        if cut_dur < PREVIZ_MIN_CLIP_S - 0.01:
            padded_path = os.path.join(workdir, "padded.mp4")
            stitcher.pad_to_seconds(current, padded_path, target_sec=PREVIZ_MIN_CLIP_S, threads=1)
            current = padded_path
            sent_seconds = stitcher.probe_duration(current) or PREVIZ_MIN_CLIP_S
            padded = True

        height = stitcher.probe_height(current)
        if height and height < stitcher.ANIME_MIN_HEIGHT:
            scaled_path = os.path.join(workdir, "scaled.mp4")
            stitcher.scale_to_min_height(current, scaled_path, min_height=stitcher.ANIME_MIN_HEIGHT, threads=1)
            current = scaled_path

        with open(current, "rb") as handle:
            return {
                "bytes": handle.read(),
                "origSeconds": round(orig_seconds if orig_seconds > 0 else cut_dur, 3),
                "sentSeconds": round(sent_seconds, 3),
                "padded": padded,
            }


# ---------------------------------------------------------------- 통 테이크 제출(설계자 2026-10-26, 063)


def _stitch_recordings(rec_keys: list[str]) -> bytes:
    """테이크의 녹화 mp4 들을 순서대로 이어 붙여 1개 mp4 로(720p 유지, stitcher.concat 재사용).

    녹화 1개면 그대로 돌려준다(concat 없이). 여러 개면 _concat 으로 붙인다.
    """
    clips = []
    for key in rec_keys:
        if not key:
            continue
        clips.append(_fetch_bytes(str(key), bucket=vpoc_settings.VIDEO_BUCKET))
    if not clips:
        raise VpocError("vnew.generate", "NoRecording", "테이크 녹화가 없습니다.", 409)
    if len(clips) == 1:
        return clips[0]
    return _concat(clips)


def _take_location_jpeg(plan: dict, take: dict, variant: str = "webtoon") -> bytes | None:
    """테이크 장소의 뷰 A(back) 이미지. locations 의 {variant} variant viewKeys.back 을 쓴다.

    variant 기본 webtoon(웹툰 통 테이크). real 이면 실사 장소 뷰(vnew-live-fx C.5). 못 찾으면 None(장소 시트 생략).
    콘티 cut.locationEn 으로 장소를 찾는다.
    """
    location = _location_name_en(plan, take)
    loc = _location_by_name(plan, location)
    if not loc:
        return None
    webtoon = (loc.get("variants") or {}).get("real" if variant == "real" else "webtoon") or {}
    key = str((webtoon.get("viewKeys") or {}).get("back") or "")
    if not key:
        return None
    return _load_jpeg(key)


def _first_take_style_en(plan: dict) -> str:
    """등장 캐릭터 중 첫 styleEn(원작 그림체 한 문장, 300자 이내). 없으면 빈 문자열."""
    for item in (plan.get("input") or {}).get("characters") or []:
        char_id = str(item.get("charId") or "")
        if not char_id:
            continue
        try:
            char, _ = characters.load_char(_take_owner_hint(plan), char_id)
        except Exception:  # noqa: BLE001
            continue
        style_en = str(((char or {}).get("analysis") or {}).get("styleEn") or "").strip()
        if style_en:
            return style_en[:300]
    return ""


def _take_owner_hint(plan: dict) -> str:
    """styleEn 로드용 owner. plan 에 owner 가 없으면 빈 문자열(검사는 load_char 를 가짜로 바꾼다)."""
    return str(plan.get("owner") or "")


# ---------------------------------------------------------------- 장면 의상·목소리 읽기(설계자 2026-10-26, 064)
#
# 이 회차 원작 패널에서 인물이 **실제로 이 장면에서 입은 옷**과 목소리(성별·나이·톤)를 Claude 분석
# 모델로 1회 읽는다(캐시). 캐릭터 기본 복장 시트(analysis.outfitEn)가 아니라 장면 패널을 따라야
# 결과 의상이 맞는다(test2: 범건우 검은 와이셔츠인데 기본 아이보리 셔츠로 나오던 버그). 결과는
# plan.previz.castNotes[패널 바이트 해시] 로 캐시해 같은 패널이면 재호출하지 않는다.

def _clip_words(text: str, limit: int) -> str:
    """단어 경계로 자른다(설계자 2026-10-26, 065 — voice/costume 가 단어 중간에 끊기던 버그).

    limit 이하면 그대로. 넘으면 limit 안에서 마지막 공백까지 자른 뒤 끝의 쉼표·마침표를 떼고 마침표
    하나로 끝맺는다(말줄임표 '…' 는 쓰지 않는다). 공백이 없으면(한 단어) limit 에서 그대로 자른다.
    """
    text = str(text or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip().rstrip(",.") + "."


# 연기 문장 끝에 남으면 안 되는 접속사·전치사(단어 경계). "and." "as." "while." 금지(설계자 2026-10-26).
_ACTION_TAIL_WORDS = (
    "and", "as", "while", "but", "or", "with", "then", "so", "to", "of", "in", "on",
    "at", "for", "the", "a", "an", "into", "onto", "toward", "towards", "over", "under",
)


def _action_core(text: str, limit: int) -> str:
    """연기 문장의 핵심 절(주어+동사+목적어)만 남긴다(설계자 2026-10-26, 067 — 키스 컷 보존).

    첫 문장을 쓰되, limit 을 넘으면 쉼표·" and "·" while "·" as " 뒤의 부가절부터 떼어 낸다. 단, 핵심
    절(주어+동사+목적어)이 사라지지 않게 **최소 70자**는 남긴다 — 70자를 지키려면 limit 을 조금 넘겨도
    된다(핵심 동사·목적어를 자르지 않는다). 결과가 단어 중간이나 접속사("and"/"as"/"while" …)·쉼표로
    끝나지 않게 다듬고 마침표 하나로 끝맺는다. 빈 입력이면 빈 문자열. 작업 157: 쓸 만한 절 경계가 없으면 첫 문장을
    통째로 돌려준다(낱말 가운데서 자르지 않는다 — 연기 글을 줄이는 곳은 모두 이 함수와 _take_action 만 쓴다).
    """
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return ""
    sentences = _split_sentences(body)
    first = (sentences[0] if sentences else body).strip()
    first = first.rstrip(".!?").strip()
    floor = min(70, len(first))  # 짧은 문장은 그 자체가 하한.

    # limit 안이면 그대로(접속사·쉼표 끝만 다듬는다).
    def _tidy(candidate: str) -> str:
        out = candidate.strip().rstrip(",;: ").strip()
        # 끝 단어가 접속사·전치사면 떼어 낸다(여러 개 연속일 수 있다).
        changed = True
        while changed and out:
            changed = False
            words = out.split()
            if words and words[-1].lower().strip(",;:") in _ACTION_TAIL_WORDS:
                out = " ".join(words[:-1]).rstrip(",;: ").strip()
                changed = True
        return out

    if len(first) <= limit:
        tidy = _tidy(first)
        return (tidy + ".") if tidy else ""

    # limit 초과: 절 경계(쉼표·접속사) 뒤 부가절부터 떼어 핵심만 남긴다. floor(70) 이상인 가장 긴
    # 절 경계 자리를 고른다. 경계는 쉼표, 그리고 " and "/" while "/" as "/" but "/" with " 앞.
    boundaries: list[int] = []
    for match in _re.finditer(r",|\s+(?:and|while|as|but|with|then|so)\s+", first):
        boundaries.append(match.start())
    boundaries.sort()
    best = ""
    for pos in boundaries:
        prefix = first[:pos].strip()
        if len(prefix) < floor:
            continue  # 핵심이 잘릴 만큼 짧으면 건너뛴다(70자 보장).
        best = prefix
        if len(prefix) <= limit:
            # limit 안에 드는 가장 긴 절 경계. 더 긴 것이 있어도 limit 초과면 쓰지 않는다.
            break
    if best:
        tidy = _tidy(best)
        if len(tidy) >= floor:
            return tidy + "."
    # 작업 157: 절 경계가 없거나 floor 를 못 채우면 첫 문장을 통째로 둔다(글자 수로 자르면 "through the off." 처럼
    # 낱말 가운데서 끊겼다 — test3). 연기 줄이기는 늘 문장·절 경계에서만 한다.
    tidy = _tidy(first)
    return (tidy + ".") if tidy else ""


# 작업 157: 통 테이크 TIMELINE 연기 = 지시서 actionEn 의 앞 문장 ACTION_SENTENCES 개(통째로). 줄일 때는 첫 문장(통째로)만.
ACTION_SENTENCES = 2


def _take_action(text: str, sentences: int = ACTION_SENTENCES) -> str:
    """연기 글의 앞 문장 sentences 개를 통째로(작업 157). 문장 안에서 자르지 않고, 끝은 마침표 하나. 빈 입력이면 ""."""
    body = " ".join(str(text or "").split()).strip()
    if not body:
        return ""
    picked = _split_sentences(body)[:max(1, int(sentences or 1))] or [body]
    out = " ".join(picked).strip().rstrip(",;: ").strip()
    if not out:
        return ""
    return out if out.endswith((".", "!", "?")) else out + "."


_CAST_NOTES_SYSTEM = (
    "You read original Korean webtoon panels and report, for each named character, the clothing they "
    "actually wear IN THIS SCENE (not a default outfit) and a short voice description. Look only at "
    "what the panels show; do not guess. If a character's clothing is not visible, leave it empty. "
    "Answer ONLY by calling emit_cast_notes exactly once."
)

# vnew-live-fx RA6.1(참고 구현 vpoc/llm.py:1255-1258): 켬이면 옷 무늬 위치까지 쓰게 한다. 캐시 키는 그대로다(RA6.2).
_CAST_NOTES_PATTERN_RULE = (
    " For clothing patterns, say where the pattern is (e.g. 'white stripes only on the sleeves, plain black body')."
)


def _cast_notes_system() -> str:
    """장면 의상 읽기 SYSTEM. VNEW_FX 켬이면 무늬 위치 규칙을 끝에 더한다(끔이면 지금 상수 그대로)."""
    if vnew_settings.VNEW_FX:
        return _CAST_NOTES_SYSTEM + _CAST_NOTES_PATTERN_RULE
    return _CAST_NOTES_SYSTEM


_CAST_NOTES_TOOL = {
    "name": "emit_cast_notes",
    "description": "Report each character's in-scene costume and voice from the original panels.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "people": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            # ≤140자: 이 장면에서 실제로 입은 상의·하의·색·소재·특징. 안 보이면 빈 값.
                            "costumeEn": {"type": "string"},
                            # ≤70자: 성별·나이대·톤(예 "low, calm adult male voice").
                            "voiceEn": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["people"],
        }
    },
}


def _read_cast_notes(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 장면 의상·목소리 JSON 을 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_cast_notes_system(),
        blocks=blocks,
        tool=_CAST_NOTES_TOOL,
        max_tokens=1200,
        where="vnew.generate.cast_notes",
        model=model,
    )


def _cast_panel_jpegs(owner: str, pid: str, plan: dict, take: dict, limit: int) -> list[bytes]:
    """테이크 컷들의 원작 패널 중 인물이 잘 보이는 것 최대 limit 장(설계자 2026-10-26, 064).

    _segment_panel_jpegs 재사용: 테이크가 묶은 녹화 구간(recSegIds)마다 그 몫의 패널을 모은다.
    구간을 못 찾으면 테이크 전체(seg)로 한 번 모은다. 중복 바이트는 제거하고 앞에서 limit 장.
    """
    out: list[bytes] = []
    seen: set[bytes] = set()
    seg_ids = [str(s) for s in (take.get("recSegIds") or []) if s]
    if seg_ids:
        for seg_id in seg_ids:
            if len(out) >= limit:
                break
            for jpeg in _segment_panel_jpegs(owner, pid, plan, {"id": seg_id}, limit):
                if jpeg and jpeg not in seen:
                    seen.add(jpeg)
                    out.append(jpeg)
                    if len(out) >= limit:
                        break
    else:
        for jpeg in _segment_panel_jpegs(owner, pid, plan, take, limit):
            if jpeg and jpeg not in seen:
                seen.add(jpeg)
                out.append(jpeg)
    return out[:limit]


def _cast_notes_hash(panels: list[bytes], names: list[str]) -> str:
    """패널 바이트 + 인물 이름 목록의 해시(캐시 키). 같은 패널·인물이면 같은 키."""
    hasher = hashlib.sha256()
    for jpeg in panels:
        hasher.update(hashlib.sha256(jpeg or b"").digest())
    for name in sorted(names):
        hasher.update(str(name or "").encode("utf-8"))
    return hasher.hexdigest()[:24]


def _scene_cast_notes(owner: str, pid: str, plan: dict, take: dict) -> dict:
    """이 테이크 장면의 인물별 의상·목소리(원작 패널 읽기, Claude 1회·캐시).

    반환: {name: {"costumeEn": str, "voiceEn": str}}. 캐시(plan.previz.castNotes[hash])에 있으면
    재호출하지 않는다. 없으면 Claude 분석 모델(ANALYZE_FALLBACK_MODEL)로 1회 읽고 저장한다.
    외부 호출은 _read_cast_notes 래퍼로만(검사가 가짜로 바꾼다). 원장 text 계상.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    names = [a.get("name") for a in _segment_actors(plan, take) if a.get("name")]
    names = [str(n) for n in names if n]
    panels = _cast_panel_jpegs(owner, pid, plan, take, int(vnew_settings.VNEW_TAKE_CAST_PANELS or 4))
    if not names:
        return {}
    key = _cast_notes_hash(panels, names)

    cache = ((plan.get("previz") or {}).get("castNotes") or {}).get(key)
    if isinstance(cache, dict) and cache:
        _note_cast_cuts(owner, pid, plan, take, key)
        return {str(n): dict(v) for n, v in cache.items() if isinstance(v, dict)}

    # 패널 + 인물 이름 목록을 Claude 에 보낸다.
    blocks: list[dict] = []
    for jpeg in panels:
        blocks.append({
            "image": {"format": "jpeg", "source": {"bytes": _fit_ref(jpeg)}},
        })
    blocks.append({"text": "Characters in this scene: " + ", ".join(names) + "."})

    model = str(vnew_settings.ANALYZE_FALLBACK_MODEL)
    try:
        payload, usage = _read_cast_notes(blocks, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.cast_notes_failed pid=%s err=%s", pid, _msg(exc))
        return {}
    used = str(usage.get("model") or model)
    _charge_cast_notes(owner, pid, str(plan.get("planId") or ""), usage, used)

    notes: dict = {}
    for item in (payload.get("people") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        notes[name] = {
            "costumeEn": _clip_words(str(item.get("costumeEn") or ""), 140),
            "voiceEn": _clip_words(str(item.get("voiceEn") or ""), 70),
        }

    # 캐시에 저장(패널 바이트 해시 키). 작업 160: 컷 → 캐시 키 표(처음 읽은 테이크가 정한다)도 함께.
    cut_ids = _seg_cut_ids(take)

    def mutator(data: dict) -> None:
        previz = data.setdefault("previz", {})
        store = previz.setdefault("castNotes", {})
        store[key] = notes
        by_cut = previz.setdefault("castNotesCuts", {})
        for cut_id in cut_ids:
            by_cut.setdefault(cut_id, key)

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.cast_notes_cache_failed pid=%s err=%s", pid, str(exc)[:200])
    mutator(plan)  # 작업 160: 이 호출의 계획 사본에도(같은 순회의 _scene_costume 이 바로 본다)
    return notes


def _note_cast_cuts(owner: str, pid: str, plan: dict, take: dict, key: str) -> None:
    """작업 160: 캐시를 그대로 쓸 때 그 테이크 컷의 컷 → 캐시 키 표가 비어 있으면 채운다(계획 사본·저장 둘 다). 이미 있으면 아무것도 안 한다."""
    from . import plan as plan_mod  # noqa: PLC0415

    cut_ids = [c for c in _seg_cut_ids(take) if c not in ((plan.get("previz") or {}).get("castNotesCuts") or {})]
    if not cut_ids:
        return

    def mutator(data: dict) -> None:
        by_cut = data.setdefault("previz", {}).setdefault("castNotesCuts", {})
        for cut_id in cut_ids:
            by_cut.setdefault(cut_id, key)

    mutator(plan)
    if owner and pid:
        try:
            plan_mod.mutate_plan(owner, pid, mutator)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.gen.cast_notes_cuts_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_cast_notes(owner: str, pid: str, plan_id: str, usage: dict, model: str) -> None:
    """장면 의상·목소리 읽기 1회 text 계상(토큰이 있을 때만)."""
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        vpoc_ledger.charge_text(
            "vnew-castnotes-" + secrets.token_hex(6),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=plan_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_cast_notes_failed pid=%s err=%s", pid, str(exc)[:200])


def _resolve_cast(owner: str, pid: str, plan: dict, take: dict, *, read: bool) -> dict:
    """인물별 최종 의상·목소리(설계자 2026-10-26, 064). override(사용자 수정)가 자동 읽기보다 우선.

    반환: {name: {"costume": str, "voice": str}}. read=True 면 자동 읽기(_scene_cast_notes, Claude
    1회·캐시)를 수행하고, False 면 캐시만 본다(프롬프트 조립 등 비-생성 경로). override 는
    input.castOverrides 에서 이름으로 찾아 덮어쓴다(빈 값은 자동값을 가린다).
    """
    auto = _scene_cast_notes(owner, pid, plan, take) if read else _cached_cast_notes(plan, take)
    overrides = (plan.get("input") or {}).get("castOverrides") or {}
    out: dict = {}
    names = {str(a.get("name") or "") for a in _segment_actors(plan, take) if a.get("name")}
    names |= set(auto.keys())
    names |= {str(n) for n in overrides.keys()}
    for name in names:
        name = str(name or "")
        if not name:
            continue
        auto_item = auto.get(name) or {}
        costume = str(auto_item.get("costumeEn") or "").strip()
        voice = str(auto_item.get("voiceEn") or "").strip()
        ov = overrides.get(name) or {}
        if "costume" in ov:
            costume = str(ov.get("costume") or "").strip()
        if "voice" in ov:
            voice = str(ov.get("voice") or "").strip()
        out[name] = {"costume": costume, "voice": voice}
    return out


def _cached_cast_notes(plan: dict, take: dict) -> dict:
    """캐시된 castNotes 만 조회(Claude 호출 없음). 캐시가 없으면 빈 dict."""
    names = [str(a.get("name")) for a in _segment_actors(plan, take) if a.get("name")]
    store = (plan.get("previz") or {}).get("castNotes") or {}
    # 캐시는 패널 바이트 해시 키라 여기서는 다시 못 만든다 → 값이 하나뿐이면 그걸, 아니면 빈 dict.
    # (조립 경로는 생성 때 read=True 로 이미 채워진 뒤 호출되므로 보통 맞는 값이 들어 있다.)
    for value in store.values():
        if isinstance(value, dict) and value:
            return {str(n): dict(v) for n, v in value.items() if isinstance(v, dict)}
    _ = names
    return {}


def scene_cast_view(owner: str, pid: str, plan: dict) -> list[dict]:
    """plan_view 용 장면 의상·목소리 표(설계자 2026-10-26, 064). Claude 호출 없음(캐시만).

    테이크 모드가 아니면 빈 목록. 테이크 모드면 각 인물에 대해 {name, costumeAuto, voiceAuto,
    costume(override 우선 최종), voice}. 화면은 costumeAuto/voiceAuto 를 안내로, costume/voice 를
    입력칸 초기값으로 쓴다.
    """
    if _gen_mode(plan) != "previz-take":
        return []
    takes = _take_units(plan)
    if not takes:
        # 녹화 전이라도 등장 인물 표는 보여 준다(첫 테이크 그룹핑 불가 → 전체 배우).
        actors = []
        seen = set()
        for scene in (plan.get("previz") or {}).get("scenes") or []:
            for actor in scene.get("actors") or []:
                name = str(actor.get("name") or "")
                if name and name not in seen:
                    seen.add(name)
                    actors.append(name)
        overrides = (plan.get("input") or {}).get("castOverrides") or {}
        rows = []
        for name in actors:
            ov = overrides.get(name) or {}
            rows.append({
                "name": name, "costumeAuto": "", "voiceAuto": "",
                "costume": str(ov.get("costume") or ""), "voice": str(ov.get("voice") or ""),
            })
        return rows

    overrides = (plan.get("input") or {}).get("castOverrides") or {}
    rows: list[dict] = []
    seen: set[str] = set()
    for take in takes:
        auto = _cached_cast_notes(plan, take)
        for actor in _segment_actors(plan, take):
            name = str(actor.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            auto_item = auto.get(name) or {}
            ov = overrides.get(name) or {}
            costume_auto = str(auto_item.get("costumeEn") or "")
            voice_auto = str(auto_item.get("voiceEn") or "")
            rows.append({
                "name": name,
                "costumeAuto": costume_auto,
                "voiceAuto": voice_auto,
                "costume": str(ov.get("costume")) if "costume" in ov else costume_auto,
                "voice": str(ov.get("voice")) if "voice" in ov else voice_auto,
            })
    return rows


def read_cast_notes_all(owner: str, pid: str) -> list[dict]:
    """[의상 읽기] 버튼(미리보기): 테이크마다 _scene_cast_notes 를 수행(캐시 채움) 후 표를 돌려준다.

    생성 선행 조건을 완전히 요구하지 않는다(녹화 전이라도 패널·배우가 있으면 읽는다). 테이크 모드가
    아니면 빈 목록.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    plan, _ = plan_mod.load_plan(owner, pid)
    if _gen_mode(plan) != "previz-take":
        return []
    for take in _take_units(plan):
        try:
            _resolve_cast(owner, pid, plan, take, read=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.gen.read_cast_notes_failed pid=%s err=%s", pid, _msg(exc))
    plan, _ = plan_mod.load_plan(owner, pid)
    return scene_cast_view(owner, pid, plan)


def _short_appearance(text: str, limit: int = 170) -> str:
    """인물 외형을 짧게 요약한다(설계자 2026-10-26, 064 — 얼굴·머리만).

    외형 전체(캐릭터 analysis 전체, 인물당 ~600자)를 그대로 쓰면 TIMELINE 예산을 먹어 컷이 잘린다.
    외형·의상은 참조 이미지(@Image)와 ASSETS 의 COSTUME 문구가 담당하므로, 이 요약에는 **얼굴·머리**
    (색·스타일·특징)만 남기고 **옷 문장은 지운다**. 규칙: 문장 단위로 나눠 "wears"/"wearing"/"dressed"/
    "outfit"/"shirt" … 같은 의상 단어가 든 문장을 버리고, "hair" 문장을 맨 앞에 두고 얼굴·눈 문장을
    이어 붙여 limit 자에서 **단어 경계**로 자른다. 빈 입력이면 빈 문자열.
    """
    body = str(text or "").strip()
    if not body:
        return ""
    sentences = _split_sentences(body)
    if not sentences:
        sentences = [body]

    # 의상 단어가 든 문장은 전부 버린다(결과 프롬프트에 'shirt'/'wears' 가 남지 않게).
    outfit_words = (
        "wear", "dressed", "outfit", "shirt", "trouser", "slacks", "jacket", "coat", "suit",
        "tie", "dress", "shoe", "sleeve", "collar", "button", "hoodie", "uniform", "clothes",
        "clothing", "pants", "skirt", "sweater", "blazer", "jeans", "scarf", "vest", "robe",
    )

    def _is_outfit(sentence: str) -> bool:
        low = sentence.lower()
        return any(word in low for word in outfit_words)

    face_sentences = [s for s in sentences if not _is_outfit(s)]
    if not face_sentences:
        # 모든 문장이 옷 얘기면(드묾) 얼굴 요약이 없다 → 빈 값(옷은 COSTUME 이 담당).
        return ""

    # "hair" 문장을 맨 앞에, 그다음 얼굴·눈 문장, 그다음 나머지.
    hair = next((s for s in face_sentences if "hair" in s.lower()), "")
    facial = next(
        (s for s in face_sentences if ("face" in s.lower() or "eyes" in s.lower()) and s != hair),
        "",
    )
    picked: list[str] = []
    if hair:
        picked.append(hair)
    if facial and facial != hair:
        picked.append(facial)
    if not picked:
        picked = face_sentences[:2]

    summary = " ".join(p.rstrip(".").strip() + "." for p in picked if p.strip()).strip()
    if len(summary) <= limit:
        return summary
    # limit 에서 단어 경계로 자른다(글자 중간 잘림 방지).
    clipped = summary[:limit]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip().rstrip(",") + "…"


def _cut_grammar(plan: dict, cut_id: str) -> dict:
    """이 컷의 연출 문법(previz.panelGrammar[cutId].grammar). 없으면 빈 dict."""
    cell = ((plan.get("previz") or {}).get("panelGrammar") or {}).get(str(cut_id))
    if isinstance(cell, dict):
        grammar = cell.get("grammar")
        return grammar if isinstance(grammar, dict) else {}
    return {}


def _cut_eyelines(plan: dict, cut: dict) -> list[dict]:
    """이 컷의 인물별 시선(눈동자 방향) 목록(설계자 2026-10-26, 065-E).

    반환: [{name, target}] — target 은 person:<이름>|camera|down|up|offscreen_left|offscreen_right|closed.
    보완 규칙(옛 캐시·빈 값):
      ① grammar.eyelines 가 있으면 그대로(normalize 된 값).
      ② 없으면 주인공 faceTurn 으로: toward_camera→camera, down→down, up→up,
         screen_left/right→상대가 그쪽이면 person:상대, 아니면 offscreen_left/right.
      ③ 대사 있는 컷의 화자는 person:상대(듣는 사람) — 058 규칙과 일치.
      ④ 촬영 지시서 actionEn 에 "eyes closed"/"closes ... eyes" 가 있으면 그 인물은 closed.
      ⑤ target 이 자기 자신(person:<자기 이름>)이면 같은 컷의 상대(없으면 대사 상대)로 바꾸고,
         상대가 없으면 그 항목을 생략한다. 동일 인물 중복 항목은 제거한다(065 dry-run 1 수정).
    """
    cut_id = str(cut.get("id") or "")
    grammar = _cut_grammar(plan, cut_id)
    actors = _segment_actors(plan, {"cutIds": [cut_id]})
    names = [str(a.get("name") or "") for a in actors if a.get("name")]
    if not names:
        return []

    # 대사 화자(자기 자신 시선 보완에 쓴다 — 화자가 자기를 보면 듣는 사람으로 바꾼다).
    lines = _cut_lines(plan, cut)
    spoken = [ln for ln in lines if not ln.get("silent") and str(ln.get("lineKo") or "").strip()]
    speaker = str(spoken[0].get("speaker") or "").strip() if spoken else ""

    def _fix_self(name: str, target: str) -> str:
        """target 이 자기 자신(person:<name>)이면 상대(같은 컷의 다른 인물, 없으면 대사 상대)로,
        상대도 없으면 빈 문자열(생략). 자기 자신 시선 버그(065 dry-run 1)를 막는다."""
        if not target.startswith("person:"):
            return target
        who = target.split(":", 1)[1].strip()
        if who != name:
            return target
        # 상대 1) 같은 컷의 다른 인물.
        partner = next((n for n in names if n != name), "")
        # 상대 2) 대사 상대(화자가 자기면 듣는 사람, 듣는 사람이 자기면 화자).
        if not partner and speaker and speaker != name:
            partner = speaker
        return ("person:" + partner) if partner else ""

    def _finalize(pairs: list[dict]) -> list[dict]:
        """자기 자신 시선 교정 + 동일 인물 중복 제거(앞의 것을 유지)."""
        out: list[dict] = []
        seen: set[str] = set()
        for item in pairs:
            name = str(item.get("name") or "").strip()
            target = str(item.get("target") or "").strip()
            if not name or not target:
                continue
            if name in seen:
                continue
            target = _fix_self(name, target)
            if not target:
                continue
            seen.add(name)
            out.append({"name": name, "target": target})
        return out

    # ① grammar.eyelines 그대로(자기 자신·중복만 교정).
    existing = grammar.get("eyelines") if isinstance(grammar, dict) else None
    if isinstance(existing, list) and existing:
        out = _finalize(
            [{"name": item.get("name"), "target": item.get("target")}
             for item in existing if isinstance(item, dict)]
        )
        if out:
            return out

    # 보완 규칙(옛 캐시). 주인공·상대·faceTurn·화자를 모아 추정한다.
    main = str(grammar.get("mainSubject") or "") or (names[0] if names else "")
    other = next((n for n in names if n != main), "")
    face_turn = str(grammar.get("faceTurn") or "")
    partner_side = str(grammar.get("offscreenPartnerSide") or "none")

    # 상대가 화면 왼/오 어느 쪽인지(others.side 로 추정).
    other_side = ""
    for o in grammar.get("others") or []:
        if str(o.get("name") or "") == other:
            other_side = str(o.get("side") or "")
            break
    if not other_side and partner_side in ("left", "right"):
        other_side = partner_side

    # 촬영 지시서 actionEn 에 눈 감음.
    action_low = str(_cut_brief(plan, cut_id).get("actionEn") or "").lower()
    eyes_closed = "eyes closed" in action_low or "closes his eyes" in action_low or "closes her eyes" in action_low or "eyes shut" in action_low

    def _main_target() -> str:
        if face_turn == "toward_camera":
            return "camera"
        if face_turn == "down":
            return "down"
        if face_turn == "up":
            return "up"
        if face_turn in ("screen_left", "screen_right"):
            want_side = "left" if face_turn == "screen_left" else "right"
            if other and other_side == want_side:
                return "person:" + other
            return "offscreen_" + want_side
        # away / 기본: 상대가 있으면 상대, 없으면 카메라.
        return ("person:" + other) if other else "camera"

    out: list[dict] = []
    for name in names:
        if eyes_closed and name == main:
            target = "closed"
        elif speaker and name == speaker and other:
            # ③ 화자는 듣는 사람(상대)을 본다.
            target = "person:" + other
        elif name == main:
            target = _main_target()
        elif other and name == other and speaker == main:
            # 주인공이 말하면 상대(듣는 사람)는 주인공을 본다.
            target = "person:" + main
        else:
            # 그 밖의 인물: 상대가 있으면 상대, 없으면 카메라.
            partner = next((n for n in names if n != name), "")
            target = ("person:" + partner) if partner else "camera"
        out.append({"name": name, "target": target})
    return _finalize(out)


# 한글 음절 → 로마자(개정 로마자 표기법 근사). 이름 바이트 절약에 쓴다(설계자 2026-10-26, 066-K).
# 한글 1글자 = UTF-8 3바이트라 TIMELINE·gaze·화자에 한국어 이름을 반복하면 예산을 크게 먹는다.
# ASSETS 에서 "{한국 이름} ({로마자})" 로 한 번 소개하고, 그 밖에는 로마자를 쓴다(대사 본문은 한국어 유지).
_ROMAJA_LEAD = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]
_ROMAJA_VOWEL = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi",
    "yu", "eu", "ui", "i",
]
_ROMAJA_TAIL = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "l", "l", "m", "p", "p", "t",
    "t", "ng", "t", "t", "k", "t", "p", "t",
]


def _romanize_name(name: str) -> str:
    """한글 이름을 로마자(개정 로마자 근사)로 바꾼다. 한글이 아니면 그대로 돌려준다.

    규칙: 완성형 한글(가~힣)을 초성·중성·종성으로 분해해 표를 매핑하고, 첫 글자를 대문자로 만든다.
    한글이 섞이지 않은 입력(이미 영문 이름 등)은 변형 없이 반환한다. 빈 입력은 빈 문자열.
    로마자는 촬영 지시서/캐릭터의 영문 표기를 대신 쓸 수 없을 때의 **폴백**이다(호출부에서 영문 표기를
    먼저 쓴다).
    """
    text = str(name or "").strip()
    if not text:
        return ""
    out: list[str] = []
    has_hangul = False
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            has_hangul = True
            syl = code - 0xAC00
            lead = syl // 588
            vowel = (syl % 588) // 28
            tail = syl % 28
            out.append(_ROMAJA_LEAD[lead] + _ROMAJA_VOWEL[vowel] + _ROMAJA_TAIL[tail])
        else:
            out.append(ch)
    if not has_hangul:
        return text
    romaja = "".join(out).strip()
    if not romaja:
        return text
    return romaja[:1].upper() + romaja[1:]


def _gaze_is_default(eyelines: list[dict], speaker: str, names: list[str]) -> bool:
    """이 컷 시선이 **기본 규칙**과 같은지(설계자 2026-10-26, 066-E).

    기본 규칙(RULES 에 명시): 말하는 사람은 듣는 사람의 눈을 보고, 듣는 사람은 말하는 사람을 본다
    (두 사람 상호 eye contact). 이 경우에만 True — TIMELINE 에서 gaze 를 **생략**한다.
    예외(False → gaze 표기): closed/down/up/camera/offscreen_*, 또는 말하지 않는 사람이 상대 외 대상을
    볼 때, 또는 인물이 2명이 아닐 때, 또는 화자 미상.
    """
    if not eyelines or not speaker:
        return False
    people = [n for n in names if n]
    if len(people) != 2 or speaker not in people:
        return False
    listener = next((n for n in people if n != speaker), "")
    if not listener:
        return False
    by_name = {str(e.get("name") or ""): str(e.get("target") or "") for e in eyelines}
    # 기본: 화자 → 듣는 사람의 눈, 듣는 사람 → 화자의 눈.
    return (
        by_name.get(speaker) == "person:" + listener
        and by_name.get(listener) == "person:" + speaker
    )


def _gaze_text(eyelines: list[dict], name_map: dict | None = None) -> str:
    """시선 목록을 사람이 읽는 한 구로(설계자 2026-10-26, 065-E).

    예: "gaze: Geonu → Uijun's eyes; Uijun → down". person:<이름> 은 "<이름>'s eyes", camera 는
    "the camera", offscreen_left/right 는 "off-screen left/right", closed 는 "eyes closed",
    down/up 은 "down"/"up".
    """
    if not eyelines:
        return ""
    nm = name_map or {}

    def _disp(who: str) -> str:
        return str(nm.get(who) or who)

    parts = []
    for item in eyelines:
        name = str(item.get("name") or "").strip()
        target = str(item.get("target") or "").strip()
        if not name or not target:
            continue
        if target.startswith("person:"):
            who = target.split(":", 1)[1].strip()
            phrase = f"{_disp(who)}'s eyes" if who else "the other person"
        elif target == "camera":
            phrase = "the camera"
        elif target == "offscreen_left":
            phrase = "off-screen left"
        elif target == "offscreen_right":
            phrase = "off-screen right"
        elif target == "closed":
            phrase = "eyes closed"
        else:
            phrase = target  # down / up
        parts.append(f"{_disp(name)} \u2192 {phrase}")
    return ("gaze: " + "; ".join(parts)) if parts else ""


def _shot_abbrev(plan: dict, cut_id: str) -> str:
    """컷의 샷 약어(설계자 2026-10-26, 065 — 렌즈·거리 숫자 없이 '크기 + 각도'만).

    예: "MCU 3/4" · "MS OTS" · "ECU profile". 카메라(렌즈·거리)는 @Video1 이 담당하므로 숫자("50 mm",
    "approximately …")는 넣지 않는다. 연출 문법(previz.panelGrammar)의 shotSize·shotType·vantage 로
    만든다. grammar 가 없으면(옛/테스트 데이터) 촬영 지시서 shotEn 에서 크기 토큰만 뽑고, 그래도 없으면
    "MCU".

    각도 토큰: over_shoulder → "OTS", profile_two_shot/vantage=side → "profile", vantage 3q/front3q →
    "3/4", front → "front", back3q/back → "back". two_shot 는 각도 없이 크기만("MS")."""
    grammar = _cut_grammar(plan, cut_id)
    sizes = ("ECU", "CU", "MCU", "MS", "MLS", "FS", "LS")
    size = str(grammar.get("shotSize") or "").strip().upper()
    if size not in sizes:
        # grammar 없음: shotEn 앞부분에서 크기 토큰만 찾는다.
        shot_en = str(_cut_brief(plan, cut_id).get("shotEn") or "").upper()
        size = next((s for s in sizes if s in shot_en.replace("-", "").split()), "") or "MCU"
    shot_type = str(grammar.get("shotType") or "").strip().lower()
    vantage = str(grammar.get("vantage") or "").strip().lower()
    angle = ""
    if shot_type == "over_shoulder":
        angle = "OTS"
    elif shot_type == "profile_two_shot" or vantage == "side":
        angle = "profile"
    elif vantage in ("3q", "front3q"):
        angle = "3/4"
    elif vantage == "front":
        angle = "front"
    elif vantage in ("back", "back3q"):
        angle = "back"
    return (size + " " + angle).strip() if angle else size


def _cut_on_screen(plan: dict, cut: dict) -> dict:
    """이 컷에서 **화면에 보이는 사람**과 **화면 밖 화자**를 가린다(설계자 2026-10-26, 067).

    화면에 보이는 사람 = 연출 문법(previz.panelGrammar)의 mainSubject + others(how 와 함께). 숨긴
    사람(장면 배우 중 mainSubject·others 어디에도 없는 사람)은 제외한다. grammar 가 없으면(옛/테스트
    데이터) 장면 timeline 의 그 컷 actors 를 전부 화면에 있다고 본다(how = full).

    반환: {"onScreen": [{"name", "how"}](mainSubject 가 맨 앞, how 는 main|full|hands only|...),
           "offScreenSpeaker": 화면에 없는 화자 이름(없으면 "")}.
    how 는 grammar.others 의 값(hands_only→"hands only", partial_edge→"partial", shoulder_or_back→
    "shoulder/back only", lips_or_face_part→"face only", full→"full")을 사람이 읽는 말로 바꾼다.
    """
    cut_id = str(cut.get("id") or "")
    grammar = _cut_grammar(plan, cut_id)
    scene_actors = _segment_actors(plan, {"cutIds": [cut_id]})
    scene_names = [str(a.get("name") or "") for a in scene_actors if a.get("name")]

    how_label = {
        "full": "full",
        "partial_edge": "partial",
        "shoulder_or_back": "shoulder/back only",
        "hands_only": "hands only",
        "lips_or_face_part": "face only",
    }

    on_screen: list[dict] = []
    conti_cast = [ch for ch in cut.get("characters") or [] if isinstance(ch, dict) and str(ch.get("name") or "").strip()
                  and ch.get("presence") in ("on_screen", "hand_only", "voice_only", "off_screen")]
    if conti_cast:
        # 작업 162 fix1: 콘티 presence 가 먼저(on_screen = 보임, hand_only = hands only, voice_only·off_screen = 화면에 없음).
        # 주인공은 패널 읽기 mainSubject 가 on_screen 이면 그 사람, 아니면 첫 on_screen. 보이는 정도(how)는 패널 읽기 others 값.
        others_how = {str(o.get("name") or "").strip(): how_label.get(str(o.get("how") or "full").strip().lower(), "full")
                      for o in (grammar or {}).get("others") or [] if isinstance(o, dict)}
        shown = [str(ch["name"]).strip() for ch in conti_cast if ch.get("presence") == "on_screen"]
        main = str((grammar or {}).get("mainSubject") or "").strip()
        main = main if main in shown else (shown[0] if shown else "")
        if main:
            on_screen.append({"name": main, "how": "main"})
        for ch in conti_cast:
            name = str(ch["name"]).strip()
            if name == main or any(item["name"] == name for item in on_screen):
                continue
            if ch.get("presence") == "on_screen":
                how = others_how.get(name, "full")
                on_screen.append({"name": name, "how": "full" if how == "hands only" else how})
            elif ch.get("presence") == "hand_only":
                on_screen.append({"name": name, "how": "hands only"})
    elif grammar:
        main = str(grammar.get("mainSubject") or "").strip()
        seen: set[str] = set()
        if main:
            on_screen.append({"name": main, "how": "main"})
            seen.add(main)
        for other in grammar.get("others") or []:
            if not isinstance(other, dict):
                continue
            name = str(other.get("name") or "").strip()
            if not name or name in seen:
                continue
            how_raw = str(other.get("how") or "full").strip().lower()
            on_screen.append({"name": name, "how": how_label.get(how_raw, "full")})
            seen.add(name)
    else:
        # grammar 없음: 장면 배우 전부 화면에 있다고 본다.
        for name in scene_names:
            on_screen.append({"name": name, "how": "full"})

    on_names = {str(item.get("name") or "") for item in on_screen}

    # 화면 밖 화자: 이 컷 대사 화자가 화면에 없으면.
    lines = _cut_lines(plan, cut)
    spoken = [ln for ln in lines if not ln.get("silent") and str(ln.get("lineKo") or "").strip()]
    off_speaker = ""
    if spoken:
        speaker = str(spoken[0].get("speaker") or "").strip()
        if speaker and speaker not in on_names:
            off_speaker = speaker
    return {"onScreen": on_screen, "offScreenSpeaker": off_speaker}


# 작업 148: 프리비즈 timeline actor 의 hands(패널에서 읽은 손)로 만드는 짧은 포즈 절. 사람마다 1절, 쓰는 손이 없으면 없음.
_POSE_PART_EN = {
    "face": "face", "eyes": "eyes", "cheek": "cheek", "chin": "chin", "mouth": "mouth", "chest": "chest",
    "knee": "knee", "shoulder": "shoulder", "neck": "neck", "hand": "hand", "head": "head", "arm": "arm",
}
_POSE_PART_PLURAL = {"cheek": "cheeks", "shoulder": "shoulders", "hand": "hands", "knee": "knees", "arm": "arms"}
# act → (한 손 동사, 두 손 동사). 목표 앞에 붙는다.
_POSE_VERB = {
    "grab": ("grabs", "grab"), "hold": ("holds", "hold"), "touch": ("touches", "touch"), "cup": ("cups", "cup"),
    "cover": ("covers", "cover"), "rest_on": ("rests on", "rest on"), "point": ("points at", "point at"),
}


def _cut_poses(plan: dict, cut: dict) -> list[dict]:
    """이 컷 프리비즈 인물 중 쓰는 손(act ≠ rest·none)이 있는 사람 [{name, hands, other}](작업 148).

    hands = timeline actor 의 hands(previz_schema 로 정리된 값), other = gestureTarget 인물 이름(없으면 "").
    hands 가 없는 옛 데이터는 [] — 포즈 절이 붙지 않아 프롬프트가 지금과 같다.
    """
    cut_id = str(cut.get("id") or "")
    scene = _scene_by_cut(plan).get(cut_id)
    if not scene:
        scene = {}  # 작업 162 fix1: 장면이 없어도 콘티 손(presence·hands)은 쓴다(옛 데이터는 그대로 [])
    names = {str(a.get("id") or ""): str(a.get("name") or "").strip() for a in scene.get("actors") or []}
    # 작업 154: timeline actor 에 hands 키가 없으면(장면을 만든 뒤 패널을 다시 읽음·gestureTarget 없이 저장돼 other_* 손이 빠짐)
    # 그 컷 패널 읽기(panelGrammar actions)의 손을 쓴다. 상대 = action target(장면 인물 이름일 때만), schema 규칙으로 정리.
    grammar_acts = {str(act.get("name") or "").strip(): act for act in _cut_grammar(plan, cut_id).get("actions") or []
                    if isinstance(act, dict) and isinstance(act.get("hands"), dict)}
    out: list[dict] = []
    for entry in scene.get("timeline") or []:
        if str(entry.get("cutId") or "") != cut_id:
            continue
        for ca in entry.get("actors") or []:
            hands = ca.get("hands") if isinstance(ca, dict) else None
            name = names.get(str((ca or {}).get("id") or ""), "")
            other = names.get(str((ca or {}).get("gestureTarget") or ""), "") if isinstance(ca, dict) else ""
            if isinstance(ca, dict) and "hands" not in ca and name in grammar_acts:
                act = grammar_acts[name]
                target = str(act.get("target") or "").strip()
                other = target if target and target != name and target in names.values() else ""
                hands = previz_schema.normalize_hands(act.get("hands"), has_target=bool(other))
            if not isinstance(hands, dict) or not name:
                continue
            kept = {side: dict(hands[side]) for side in previz_schema.HAND_SIDES if isinstance(hands.get(side), dict)}
            if not any(str(h.get("act") or "") not in ("",) + previz_schema.IDLE_HAND_ACTS for h in kept.values()):
                continue
            out.append({"name": name, "hands": kept, "other": other})
        break
    # 작업 162 fix1: 콘티 인물에 presence·hands 가 있으면 그 손이 먼저(프리비즈·패널 읽기 손을 바꾼다). off_screen·voice_only 는 손 없음.
    conti_chars = [ch for ch in cut.get("characters") or [] if isinstance(ch, dict) and ch.get("presence") and "hands" in ch]
    if conti_chars:
        by_name = {p["name"]: p for p in out}
        for ch in conti_chars:
            name = str(ch.get("name") or "").strip()
            by_name.pop(name, None)
            hands = plan_mod.cast_hands(ch.get("hands")) if ch.get("presence") in ("on_screen", "hand_only") else {}
            if name and hands:
                kept = {side: v for side, v in plan_mod.conti_hands_pose(hands).items() if side in hands}
                by_name[name] = {"name": name, "hands": kept, "other": plan_mod.conti_hand_target(cut.get("characters"), name)}
        order = [str(ch.get("name") or "").strip() for ch in cut.get("characters") or [] if isinstance(ch, dict)]
        out = sorted(by_name.values(), key=lambda p: order.index(p["name"]) if p["name"] in order else len(order))
    return out


# 작업 154: 몸이 닿는 연기 동사(콘티 characters[].actionEn = 패널에서 읽은 연기). (원형, 3인칭, -ing) — 이 한 목록만 쓴다.
CONTACT_VERBS = (
    ("touch", "touches", "touching"),
    ("hold", "holds", "holding"),
    ("cup", "cups", "cupping"),
    ("stroke", "strokes", "stroking"),
    ("grab", "grabs", "grabbing"),
    ("wipe", "wipes", "wiping"),
    ("kiss", "kisses", "kissing"),
    ("press", "presses", "pressing"),
    ("hug", "hugs", "hugging"),
    ("pull", "pulls", "pulling"),
    ("caress", "caresses", "caressing"),
    ("rest on", "rests on", "resting on"),
    ("lean on", "leans on", "leaning on"),
)
# 동사 뒤가 이 말로 시작하면 닿는 연기가 아니다("holds back tears", "holds his breath").
_CONTACT_NOT_RE = re.compile(r"^\s*(?:back\b|(?:his|her|their|a)\s+breath\b|breath\b|still\b)", re.IGNORECASE)
CONTACT_MAX = 110  # 닿는 연기 문장 본문 글자 상한(이름 뒤, 단어 경계)
_CONTACT_RES = tuple(
    (forms, re.compile(r"\b(?:" + "|".join(re.escape(f).replace(r"\ ", r"\s+") for f in forms) + r")\b", re.IGNORECASE))
    for forms in CONTACT_VERBS
)


def _cut_contact(cut: dict, brief_action: str) -> dict | None:
    """콘티 인물 연기에 몸이 닿는 동사(CONTACT_VERBS)가 있는데 지시서 연기(brief_action)에 그 동사가 없으면 그 절(작업 154).

    인물 순서대로 첫 문장을 쉼표·세미콜론·while/as/then 으로 나눠 동사가 있는 첫 절을 고른다. 반환
    {"name": 한국 이름, "text": "strokes 여의준's neck and nape", "names": 컷 인물 이름} — 이름 표기는 _take_prompt 가 바꾼다. 없으면 None.
    """
    brief = str(brief_action or "")
    chars = [ch for ch in cut.get("characters") or [] if isinstance(ch, dict)]
    names = [str(ch.get("name") or "").strip() for ch in chars if str(ch.get("name") or "").strip()]
    for ch in chars:
        name = str(ch.get("name") or "").strip()
        action = str(ch.get("actionEn") or "").strip()
        if not name or not action:
            continue
        first = _split_sentences(action)
        first = (first[0] if first else action).strip()
        for clause in re.split(r"\s*[,;]\s*|\s+(?:while|as|then)\s+", first):
            for forms, pattern in _CONTACT_RES:
                match = pattern.search(clause)
                if not match or pattern.search(brief):
                    continue
                rest = clause[match.end():].rstrip(" .!?")
                if not rest.strip() or _CONTACT_NOT_RE.match(rest):
                    continue
                text = _clip_words(forms[1] + rest, CONTACT_MAX).rstrip(".")
                return {"name": name, "text": text, "names": names}
    return None


def _pose_clause(pose: dict, disp_name) -> str:
    """사람 하나의 포즈 절(영어 1문장, 마침표 없음). 예: "Beomgeonu's right hand grabs Yeouijun's chin and lifts it;
    the left hand rests". 쓰는 손이 없으면 "". disp_name(한국 이름) → 프롬프트 표기(로마자)."""
    name = disp_name(str(pose.get("name") or "")) or "A character"
    other = disp_name(str(pose.get("other") or "")) if pose.get("other") else ""
    hands = pose.get("hands") or {}

    def _active(side: str) -> dict | None:
        hand = hands.get(side)
        if isinstance(hand, dict) and str(hand.get("act") or "") in _POSE_VERB:
            return hand
        return None

    def _target(hand: dict, plural: bool) -> str:
        target = str(hand.get("target") or "none")
        if target == "none":
            return other if other and hand.get("act") == "point" else ""
        own = target.startswith("own_")
        part = target.split("_", 1)[1]
        word = _POSE_PART_EN.get(part, part)
        if plural:
            word = _POSE_PART_PLURAL.get(part, word)
        if own:
            return f"the {word}"
        return f"{other}'s {word}" if other else f"the other person's {word}"

    def _phrase(hand: dict, plural: bool) -> str:
        act = str(hand.get("act") or "")
        verb = _POSE_VERB[act][1 if plural else 0]
        target = _target(hand, plural)
        if not target:
            if act == "point":
                return "point" if plural else "points"
            return ""
        text = f"{verb} {target}"
        if act == "grab" and hand.get("target") == "other_chin":
            text += " and lifts it" if not plural else " and lift it"
        return text

    right, left = _active("right"), _active("left")
    if not right and not left:
        return ""
    if right and left and (right.get("act"), right.get("target")) == (left.get("act"), left.get("target")):
        phrase = _phrase(right, True)
        return f"Both of {name}'s hands {phrase}" if phrase else ""
    if right and left:
        first, second = _phrase(right, False), _phrase(left, False)
        parts = [f"{name}'s right hand {first}" if first else "", f"the left hand {second}" if second else ""]
        return "; ".join(p for p in parts if p)
    side, hand = ("right", right) if right else ("left", left)
    rest_side = "left" if side == "right" else "right"
    own_rest = str(hand.get("act")) == "rest_on" and str(hand.get("target")) in ("own_cheek", "own_chin")
    if own_rest:
        # 뺨·턱을 손에 괸 자세: 주어를 뺨·턱으로(손이 뺨 아래).
        part = str(hand.get("target")).split("_", 1)[1]
        return f"{name}'s {part} rests on the {side} hand; the {rest_side} hand rests"
    phrase = _phrase(hand, False)
    if not phrase:
        return ""
    return f"{name}'s {side} hand {phrase}; the {rest_side} hand rests"


# 한국어 시간대 → 영어(HARMONY 의 "time of day"). 부분일치로 가장 먼저 맞는 것을 쓴다.
_TIME_OF_DAY_EN = (
    ("새벽", "dawn"),
    ("아침", "morning"),
    ("오전", "morning"),
    ("점심", "midday"),
    ("정오", "midday"),
    ("한낮", "midday"),
    ("낮", "day"),
    ("오후", "afternoon"),
    ("해질", "sunset"),
    ("노을", "sunset"),
    ("저녁", "evening"),
    ("황혼", "dusk"),
    ("밤", "night"),
    ("심야", "night"),
    ("자정", "midnight"),
)


def _time_of_day_en(time_ko: str) -> str:
    """대본 setting.timeKo(낮/밤/저녁 …) 를 HARMONY 의 'time of day' 영어로. 못 맞추면 빈 문자열."""
    text = str(time_ko or "").strip()
    if not text:
        return ""
    for needle, english in _TIME_OF_DAY_EN:
        if needle in text:
            return english
    return ""


def _harmony_light(plan: dict) -> tuple[str, str]:
    """HARMONY 에 쓸 (시간대 영어, 장소 빛 요약 ≤80자). 설계자 2026-10-26 재지시 v2.

    시간대: 대본 setting.timeKo → day/night/evening…(없으면 @Video1 시간). 빛: 등장 장소 중 처음으로
    design.lightEn 이 있는 장소의 요약(첫 문장·≤80자, 단어 경계). 못 찾으면 established light source.
    """
    setting = (plan.get("script") or {}).get("setting") or {}
    tod = _time_of_day_en(setting.get("timeKo") or "")
    tod_phrase = tod or "the time of day shown in @Video1"

    light_en = ""
    for loc in plan.get("locations") or []:
        candidate = str((loc.get("design") or {}).get("lightEn") or "").strip()
        if candidate:
            light_en = candidate
            break
    if light_en:
        first = _split_sentences(light_en)
        light_phrase = _clip_words((first[0] if first else light_en).rstrip("."), 80)
    else:
        light_phrase = "the established light source and direction"
    return tod_phrase, light_phrase


def _harmony_line(plan: dict, full: bool) -> str:
    """인물·배경 조화(HARMONY) 한 줄(영어, 설계자 2026-10-26 재지시 v2).

    v4 에서 인물과 배경의 빛·색온도가 따로 놀고(콘티는 낮인데 노을 톤), 접지 그림자가 없고, 그림체가
    달랐다 → 시간대·빛을 한쪽으로 고정하고(노을/밤으로 떠내려가지 않게), 인물을 장소 광원으로 비추고
    색 번짐을 맞추며, 접지 그림자·크기·원근·공유 팔레트를 지시한다.

    full(통 테이크): 전체 지시. short(컷 단위 Kling, ≤250 바이트): 핵심만.
    """
    tod_phrase, light_phrase = _harmony_light(plan)
    if full:
        return (
            f"HARMONY: {tod_phrase} light, {light_phrase}; the same light direction, color temperature "
            "and time of day on characters and background in every cut (no drift to sunset or night "
            "unless stated); characters lit by the location's lights with matching color spill; soft "
            "contact shadows where they stand or touch; consistent scale and perspective; one shared "
            "color palette."
        )
    return (
        f"HARMONY: {tod_phrase} light from {light_phrase}; same light direction and color temperature on "
        "characters and background in every cut, no drift to sunset; contact shadows; consistent scale; "
        "one palette."
    )


def _motion_line(full: bool, voices: str = "spoken") -> str:
    """자연스러운 연기(MOTION) 한 줄(영어, 설계자 2026-10-26 재지시 v2).

    프리비즈의 '말하는 중' 머리 흔들림(speaking 플래그)은 화자 표시일 뿐이므로 따라 하지 말고,
    "이미지 한 장이 움직이는 느낌"이 아니라 실제 캐릭터가 사는 TV 애니처럼 2차 동작(머리카락·옷·
    숨·깜빡임·눈동자·대사별 표정)과 패럴랙스를 지시한다.

    full(통 테이크): 전체 지시. short(컷 단위 Kling, ≤300 바이트): 핵심만.

    voices(지시서 118a): 그 범위(컷·테이크) 대사 줄의 목소리 — _prompt_voices 가 정한다.
      "spoken"·"none"(기본): 지금 문구 그대로. "thought": 입 모양·립싱크 구절 → mouth stays closed(속마음 목소리만,
      눈·얼굴 연기). "mixed": 립싱크는 보통 대사에만, 속마음 줄은 mouth stays closed.
    """
    if full:
        lips = {
            "thought": ("small nods or tilts on stressed words; mouth stays closed \u2014 the lines are inner voice-over; "
                        "show the feeling with eyes and face only; "),
            "mixed": ("small nods or tilts on stressed words; lip sync only on spoken lines; mouth stays closed during "
                      "inner voice-over lines; "),
        }.get(voices, "mouth shapes matching the Korean syllables, small nods or tilts on stressed words; ")
        return (
            "MOTION: Animate living characters, not a moving still. The mannequins' head bobbing in "
            "@Video1 only marks who speaks \u2014 do NOT copy it. Natural acting: breathing, blinks, eye "
            "darts, micro-expressions that change with each line, " + lips + "hair strands and clothing react to every "
            "movement; listeners stay still but alive. Body moves only where @Video1 has real blocking "
            "(turn, reach, lean, walk) with anticipation and easing. Camera: follow @Video1's moves with "
            "multi-plane parallax between foreground, characters and background; no jitter or looping "
            "motion."
        )
    lips_short = {
        # 300B 안에 들게 "expressions changing with the line, lip sync" 자리를 통째로 바꾼다.
        "thought": "mouth stays closed, face and eyes act the inner voice-over",
        "mixed": "lip sync only on spoken lines; mouth stays closed in inner voice-over",
    }.get(voices, "expressions changing with the line, lip sync")
    return (
        "MOTION: @Video1's head bobbing only marks the speaker \u2014 do NOT copy it. Natural acting: "
        "breathing, blinks, eye darts, " + lips_short + "; hair and clothes "
        "react to movement; move only where @Video1 shows blocking; parallax depth; no jitter."
    )


def _prompt_voices(lines) -> str:
    """대사 줄 목록의 목소리(지시서 118a): 없음 → "none", 모두 속마음 → "thought", 섞임 → "mixed", 그 밖 → "spoken".

    줄은 _cut_lines 줄(thought 키) 또는 통 테이크 lines 항목({thought: bool}) — 판정은 prompts.is_thought 하나로 한다.
    """
    flags = [_is_thought(line) for line in lines or []]
    if not flags:
        return "none"
    if all(flags):
        return "thought"
    return "mixed" if any(flags) else "spoken"


def _take_timeline_entries(plan: dict, take: dict) -> list[dict]:
    """테이크 컷들의 타임라인 항목 목록(구조화, 설계자 2026-10-26, 컷 보존 수정).

    컷마다 dict 를 돌려준다: {start, end, shot, onScreen, offScreenSpeaker, action, speaker, dialogue, lines,
    eyelines, names, gazeDefault, gaze}. 조립·줄이기는 _take_prompt 가 맡는다(여기서는 자르지 않는다).
    lines(지시서 118a) = 컷의 모든 대사 [{speaker, dialogue, thought}](빈 대사 제외, 순서 유지).
    시간 범위는 콘티 컷 seconds 를 누적해 만든다. shot 은 샷 약어(크기+각도, 렌즈·거리 숫자 없음)이고
    action 은 촬영 지시서(shotBrief) actionEn 앞 ACTION_SENTENCES 문장(작업 157, 통째로). 대사는 _cut_lines 로 귀속한 첫 발화(화자·대사).
    onScreen 은 이 컷에 보이는 사람([{name,how}]), offScreenSpeaker 는 화면 밖 화자 이름이다(067).

    시선은 구조화해서 넘긴다(설계자 2026-10-26, 066-E): eyelines(원시 [{name,target}]), names(이 컷 인물),
    gazeDefault(기본 규칙=화자↔듣는 사람 상호 eye contact 면 True → TIMELINE 에서 생략). gaze 는 뒤호환용
    기본 표기(로마자 매핑 없음) — _take_prompt 는 eyelines 를 로마자·기본 규칙과 함께 다시 렌더한다.

    vnew-live-fx RA4.1: VNEW_FX 켬이면 fx(기호 0~2, fx_list) · moodEn(영어 분위기 80자, clean_text — 줄바꿈·제어 문자를
    한 칸으로)을 더한다. 끔이면 둘 다 빈 값이다(프롬프트에 쓰이지 않는다). 한국어 moodKo 는 넣지 않는다(결정 6).
    """
    fx_on = bool(vnew_settings.VNEW_FX)
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    entries: list[dict] = []
    clock = 0.0
    take_cut_ids = _seg_cut_ids(take)
    # 작업 147: 컷을 넘어 이어지는 말·나눈 컷의 같은 대사(_dialogue_runs). lines 는 이 결과로 만든다.
    runs = _dialogue_runs([cuts_by_id.get(str(cid)) or {} for cid in take_cut_ids], plan)
    for run_lines, cut_id in zip(runs, take_cut_ids):
        cut = cuts_by_id.get(str(cut_id)) or {}
        seconds = float(cut.get("seconds") or 0)
        start = int(round(clock))
        end = int(round(clock + seconds))
        clock += seconds
        brief = _cut_brief(plan, cut_id)
        # 샷 = 약어(크기+각도)만. 렌즈·거리(@Video1 담당)는 넣지 않는다 → "approximately" 등 없음.
        shot_en = _shot_abbrev(plan, str(cut_id))
        action_en = str(brief.get("actionEn") or "").strip()
        first_action = action_en
        if action_en:
            # 작업 157: 연기는 앞 문장 ACTION_SENTENCES 개 통째로(줄이기 마지막 단계에서만 첫 문장). 154 닿는 연기는 첫 문장과 비교.
            sents = _split_sentences(action_en)
            first_action = (sents[0] if sents else action_en).rstrip(".")
            action_en = " ".join(sents[:ACTION_SENTENCES]).rstrip(".") if sents else action_en.rstrip(".")
        lines = _cut_lines(plan, cut)
        spoken = [ln for ln in lines if not ln.get("silent") and str(ln.get("lineKo") or "").strip()]
        speaker = ""
        dialogue = ""
        thought = False
        if spoken:
            first = spoken[0]
            speaker = str(first.get("speaker") or "").strip() or "A character"
            dialogue = str(first.get("lineKo") or "").strip()
            thought = _is_thought(first)
        # 지시서 118a: 컷의 모든 대사 줄(빈 대사 제외, 순서 유지). speaker·dialogue·thought(첫 줄)는 뒤호환으로 둔다.
        # 작업 147: 줄은 _dialogue_runs 결과(화자 없는 조각은 앞 화자 말에 붙음). 이어지는 말이면 run·utterance 를 더한다.
        all_lines = []
        for ln in run_lines:
            item = {"speaker": ln["speaker"] or "A character", "dialogue": ln["lineKo"], "thought": ln["thought"]}
            if ln["run"]:
                item["run"] = ln["run"]
                if ln["run"] == "start":
                    item["utterance"] = ln["utterance"]
            all_lines.append(item)
        eyelines = _cut_eyelines(plan, cut)
        names = [str(e.get("name") or "") for e in eyelines if e.get("name")]
        gaze_default = _gaze_is_default(eyelines, speaker, names)
        gaze = _gaze_text(eyelines)
        on = _cut_on_screen(plan, cut)
        entries.append(
            {
                "start": start,
                "end": end,
                "shot": shot_en,
                "onScreen": on["onScreen"],
                "offScreenSpeaker": on["offScreenSpeaker"],
                "action": action_en,
                "speaker": speaker,
                "dialogue": dialogue,
                "lines": all_lines,
                "eyelines": eyelines,
                "names": names,
                "gazeDefault": gaze_default,
                "gaze": gaze,
                "fx": vnew_fx.fx_list(cut.get("fx"), vnew_fx.FX_MAX) if fx_on else [],
                "moodEn": vnew_fx.clean_text(cut.get("moodEn"), vnew_fx.MOOD_EN_MAX) if fx_on else "",
            }
        )
        # 작업 148: 화면에 보이는 사람 중 쓰는 손이 있는 사람의 손(프리비즈 hands). 없으면 키 없음(지금 항목 모양 그대로).
        visible = {str(item.get("name") or "") for item in on["onScreen"]}
        poses = [p for p in _cut_poses(plan, cut) if not visible or p["name"] in visible]
        if poses:
            entries[-1]["poses"] = poses
        # 작업 154: 콘티(패널) 연기의 닿는 동작이 지시서 연기에 없으면 그 절(없으면 키 없음 — 지금 항목 모양 그대로).
        contact = _cut_contact(cut, first_action)
        if contact:
            entries[-1]["contact"] = contact
        if fx_on:
            # 작업 127: 기호 대상 인물(정리된 값)과 이 컷 장소(테이크 안 장소가 바뀌는 컷에서만 배경 문장을 다시 쓴다).
            entries[-1]["fxWho"] = vnew_fx.fx_who(cut.get("fxWho"), vnew_fx.cut_names(cut))
            entries[-1]["locationEn"] = str(cut.get("locationEn") or "")
        if thought:
            # 지시서 117: 첫 발화가 속마음이면 표시(spoken 이면 키 없음 — 지금 항목 모양 그대로).
            entries[-1]["thought"] = True
    return entries


# vnew-live-fx RA4.2·RA5.2 묘사 규칙(참고 구현 vpoc/compose.py:491·500-502, vpoc/llm.py:1255-1258). 인물 모습 유지
# (rule_replace "keep face and hair identical …")는 이미 있어 그대로 둔다. VNEW_FX 켬일 때만 붙인다.
RULE_BODY = (
    " Each person has exactly two hands; never show extra hands. Hair color and clothing patterns,"
    " including where each pattern is, stay exactly as in the references. The location, its props and"
    " background stay identical in every cut."
)
# 작업 157: 옛 bodyShort 짧은 형(RULE_BODY_SHORT)은 없앴다 — 통 테이크 RULES 는 짧은 형 1줄(TAKE_RULES_COMPACT)로 통째로 바뀐다.
# 컷 단위(Kling) CONTINUITY 앞에 넣는 문장(090 필수 수정 2: ≤ 90B 짧은 형, 문장 89B + 띄어쓰기). 손 두 개·머리색·
# 무늬 위치를 남긴다. 넘치면 컷 단위 줄이기 첫 단계(bodyDrop)가 이 문장을 뺀다(장소 요약보다 먼저).
CUT_RULE_BODY = "Exactly two hands per person; hair color and outfit patterns as placed in the references. "
# 090 필수 수정 1(다): 통 테이크 최소형에서 MOTION 원문이 안 들어갈 때 쓰는 짧은 형(≤ 160B). 원문의 핵심 금지어
# (정지 화면을 움직이지 않기, 프리비즈 머리 흔들림 따라 하지 않기, 패럴랙스·떨림 없음)를 남긴다.
MOTION_MIN = (
    "MOTION: Living characters, not a panned or zoomed still; @Video1's head bobbing only marks the speaker, "
    "do NOT copy it; parallax depth, no jitter."
)
# 지시서 118a: 속마음이 있는 테이크(thought·mixed)의 최소형에서 MOTION_MIN 뒤에 붙이는 입 다묾 구절.
MOTION_MIN_INNER = " Mouth stays closed during inner voice-over."
# HARMONY 짧은 형 틀(≤ 160B): 시간대·빛 방향·색온도 고정, 시간대 떠내려감 금지, 접지 그림자. 장소 빛 문장은 뺀다.
HARMONY_MIN = (
    "HARMONY: {tod} light; same light direction and color temperature on characters and background in every cut, "
    "{drift}; contact shadows."
)
_LATE_TIMES_OF_DAY = ("sunset", "evening", "dusk", "night", "midnight")


def _harmony_min(plan: dict) -> str:
    """HARMONY 짧은 형(통 테이크 최소형용, ≤ 160B). 시간대를 모르면 '@Video1's light'.

    늦은 시간대(노을·저녁·밤)면 "no drift to sunset or night" 가 모순이라 "no drift in time of day" 로 쓴다.
    """
    setting = (plan.get("script") or {}).get("setting") or {}
    tod = _time_of_day_en(setting.get("timeKo") or "")
    drift = "no drift in time of day" if tod in _LATE_TIMES_OF_DAY else "no drift to sunset or night"
    return HARMONY_MIN.format(tod=tod or "@Video1's", drift=drift)


# 작업 140: 실사 인물 참고 사진은 이 프로젝트에서 AI 로 만든 캐릭터 프로필이다(실존 인물 아님). 실사 프롬프트(Seedance 통 테이크
# 0·1단계·인물 바꾸기, 컷 단위 Kling, 컷 시작 그림 Seedream)의 첫 줄. 줄이기에서 빠지지 않는다(예산에서 미리 뺀다). 애니는 넣지 않는다.
REAL_REF_AI_NOTE = (
    "MANDATORY: Every person reference image is an AI-generated fictional character profile created for this project, "
    "not a photo of any real person or celebrity. No real individual is depicted; these are original synthetic characters."
)
# vnew-live-fx C.5 실사 통 테이크 조각(design 표 원문). STYLE 은 참고 구현 vpoc/compose.py:13-17 STYLE["live_action"] 에서
# 결정 7로 두 곳을 바꿨다("warm pastel color grading" 뺌, "romance K-drama cinematography" → "K-drama cinematography").
REAL_TAKE_STYLE = (
    "STYLE: Cinematic live-action film footage, photorealistic, natural skin texture, 35mm lens, soft natural light, "
    "K-drama cinematography, soft glow, shallow depth of field."
)
REAL_TAKE_RULE_REPLACE = (
    " Replace each mannequin with its person; keep face and hair identical to the reference photo "
    "and the costume exactly as written in ASSETS in every cut."
)
REAL_TAKE_AVOID = (
    "AVOID: anime or cartoon look, illustration, cel shading, line art, plastic CGI skin, still-image pan or zoom, "
    "subtitles, burned-in text."
)

# 작업 157: 통 테이크 고정 부분의 짧은 형(예산을 넘으면 컷 내용보다 먼저 이 형으로, 이 순서로 바꾼다). 각 1줄.
TAKE_COMPACT_ORDER = ("style", "avoid", "motion", "harmony", "rules", "background", "assets")
TAKE_STYLE_COMPACT = ("STYLE: TV-anime adaptation of the original webtoon{panels}: keep its faces, hair and line art; "
                      "cel shading with anime compositing, soft rim light, shallow depth of field.")
REAL_TAKE_STYLE_COMPACT = "STYLE: Photoreal live-action K-drama footage, natural skin, soft natural light, shallow depth of field."
TAKE_AVOID_COMPACT = "AVOID: 3D CGI, photoreal skin, still-image pan or zoom, on-screen text."
REAL_TAKE_AVOID_COMPACT = "AVOID: anime or cartoon look, cel shading, plastic skin, still-image pan or zoom, on-screen text."
# RULES 짧은 형("RULES: " 뒤): 재현·인물 교체+의상·기본 시선(Eyes, 067 필수 문장 원문)·손 두 개·추가 금지·오디오.
TAKE_RULES_COMPACT = (
    "Reproduce @Video1's composition, camera, positions, cuts and timing exactly. Replace each mannequin with its person "
    "(face and hair from the references, costume as written in ASSETS). Eyes: the on-screen speaker looks at the listener; "
    "an off-screen voice never appears on screen. Two hands per person, hair and outfit patterns as in the references; "
    "no added people, no text. Korean dialogue and ambient sound, no music."
)
TAKE_RULES_COMPACT_INNER = " During an inner voice-over the mouth stays closed; the line is still heard."
# ASSETS 짧은 형: 시트·사진 문장은 모든 사람에 1번(142·126 문장을 사람마다 되풀이하지 않는다).
REAL_TAKE_SHEETS_COMPACT = ("{sheets} are AI-generated turnaround sheets of fictional characters: match face/hair/build; "
                            "clothes come from COSTUME")
REAL_TAKE_FRONTS_COMPACT = "{fronts} are their close front portraits"
REAL_TAKE_PHOTOS_COMPACT = "Each person matches their photos (face, hair, build); clothes come from COSTUME"
# 작업 161: 실사 테이크 장소 줄 꼬리(상표 금지, locations.REAL_NO_BRAND). 애니 장소 줄에는 붙이지 않는다.
REAL_TAKE_NO_BRAND = "; any shop is a " + REAL_NO_BRAND


def _real_action(text: str) -> str:
    """실사 연기 문장(참고 구현 vpoc/live.py:146-154 real_action): 홍조 선 → 옅은 홍조, 눈물 줄기 → 눈물, 그림 낱말 제거."""
    import re as _re  # noqa: PLC0415

    out = _re.sub(r"\bblush (?:marks?|lines?)\b", "a faint flush", str(text or ""), flags=_re.IGNORECASE)
    out = _re.sub(r"\btear streams?\b", "tears", out, flags=_re.IGNORECASE)
    return vnew_fx.strip_drawing_words(out)


def _with_real_expressions(action: str, fx_keys, who=None, present=None, name=None) -> str:
    """연기 뒤에 실사 표현(fx.REAL_FX, 배경 기호는 빈 값이라 빠진다)을 ", " 로 붙인다(RC3.2). 표현이 없으면 그대로.

    작업 127: who(기호 대상)가 있으면 그 사람에게만(fx.who_sentences, 실사 문장). 없으면 지금과 같다.
    """
    expressions = vnew_fx.who_sentences(fx_keys, who or {}, present or [], real=True, name=name)
    if not expressions:
        return action
    head = action.rstrip(".").strip()
    return (head + ", " if head else "") + ", ".join(expressions) + "."


def _assert_no_webtoon(prompt: str) -> None:
    """실사 프롬프트에 "webtoon"(대소문자 무시)이 남으면 제출하지 않는다(RC3.4, 참고 구현 vpoc/live.py:171-178).

    준비 단계 예외라 칸은 failed, 제출·원장 0이다. 한국어 대사 속 "웹툰"은 검사 대상이 아니다.
    """
    if "webtoon" in str(prompt or "").lower():
        raise VpocError("vnew.generate", "RealPromptWebtoon", "실사 프롬프트에 웹툰 문구가 남아 제출하지 않았습니다.", 500)


def _take_prompt(plan: dict, take: dict, *, cast: list[dict], panel_nos: list[int], location_no, caps: dict | None = None,
                 variant: str = "webtoon", soften: bool = False, anchor: dict | None = None, no_text: bool = False,
                 audit: dict | None = None, quiet: bool = False, bases: dict | None = None,
                 lines_fix: dict | None = None) -> str:
    """통 테이크 프롬프트(영어, ≤ _take_prompt_budget 글자, 설계자 2026-10-26, 066 — ASSETS·연기 보존).

    **작업 157 — TIMELINE 먼저**(이 단락이 아래 옛 줄이기 설명(①~⑨·lineDrop·최소형·byteTrim·154 순서)을 대신한다):
    컷 내용 = 연기(지시서 앞 ACTION_SENTENCES 문장, 통째로) · 154 닿는 연기 · 148 포즈 절 · 그 컷의 모든 대사(147 이어짐) ·
    ON SCREEN 은 줄이기로 빼지 않는다. 예산(글자)을 넘으면 고정 부분을 짧은 형으로 하나씩 바꾼다(TAKE_COMPACT_ORDER:
    STYLE → AVOID → MOTION → HARMONY → RULES 각 1줄, BACKGROUND 1문장, ASSETS = 시트 문장 1번·의상 고정을 COSTUME 에 합침·
    외형/목소리 없음). 고정 부분이 모두 짧은 형인데도 넘칠 때만 컷 내용을 줄인다: 시선 → 분위기·기호 → 연기 첫 문장.
    대사는 빼지 않는다. 그래도 넘치면 단계 로그 끝에 overBudget — 제출 쪽이 테이크를 152 조각으로 더 나눈다
    (_prompt_split_parts). 실사는 애니와 같은 TIMELINE 이고 낱말 바꾸기(_real_action 등)만 다르다.
    audit(dict)를 주면 {cuts, linesInConti, linesInPrompt, actionsInPrompt, compact, chars, budget, fits} 를 채운다.
    quiet=True 면 로그를 남기지 않는다(조각 나누기 시험용).

    순서: ① STYLE(스타일 앵커 맨 앞) → ② ASSETS(@Video1·@Image 매핑) → ③ TIMELINE(컷마다 1줄: ON SCREEN
    + 연기 + 대사) → ④ MOTION(마네킹 흔들림 따라 하지 말고 자연 연기) → ⑤ HARMONY(인물·배경 조화) →
    ⑥ RULES(@Video1 재현·인물 교체·추가 금지·기본 시선) → ⑦ AVOID(3D/photoreal 네거티브).

    MOTION·HARMONY(설계자 2026-10-26): 프리비즈 '말하는 중' 머리 흔들림은 화자 표시일 뿐이므로 따라
    하지 말고 자연 연기를 하라고 지시(MOTION)하고, 인물과 배경의 빛·색온도·그림체·접지 그림자를 맞춘다
    (HARMONY). 둘 다 **지우지 않는 등급**(ASSETS·ON SCREEN·연기 핵심과 같은 급) — 바이트 예산에서는
    목소리·의상·대사·연기를 먼저 줄인다. RULES 의 시선·오디오 문장과 중복되지 않게 정리했다: MOTION 이
    립싱크·연기를 맡고, RULES 는 재현·교체·금지·오디오 제어(배경음악 없음)만 둔다.

    **ON SCREEN**(설계자 2026-10-26, 067): 각 컷 항목 맨 앞(타임스탬프·샷 뒤)에 그 컷에 보이는 사람을
    적는다("ON SCREEN: {A} only" / "{A} (main), {B} (hands only)" / "{A}, {B}"). 화면에 없는 화자의
    대사는 "{이름} (off-screen voice): …" 로 표기한다. RULES 의 시선 규칙은 "on-screen speaker looks at
    the listener; an off-screen voice never appears on screen".

    **ASSETS 는 절대 빼지 않는다**(최소형 후보에서도 인물 매핑 문장 = 색·이름·@Image·COSTUME 은 유지.
    외형·목소리만 줄이거나 뺄 수 있다). 이름은 바이트 절약을 위해 ASSETS 에서 "{한국 이름} ({로마자})"
    로 한 번 소개하고, TIMELINE·gaze·화자 표기는 로마자를 쓴다(대사 본문은 립싱크를 위해 한국어 유지).

    **연기 보존**(설계자 2026-10-26, 067): 연기 문장은 _action_core 로 핵심 절(주어+동사+목적어, 최소
    70자)을 남긴다. 쉼표·and·while·as 뒤 부가절부터 떼고, "and." "as." 처럼 접속사·단어 중간으로
    끝나지 않는다. 연기를 통째로 지우는 단계는 없다.

    예산(2,500바이트)을 넘으면 아래 순서로 **단계별로 조금씩** 줄이고, 매 단계 뒤 길이를 재서 한도 안이면
    즉시 멈춘다(컷 수·ON SCREEN·샷 약어·대사·MOTION·HARMONY·ASSETS 인물 문장은 유지):
      ① STYLE 둘째 문장 제거 → ② AVOID 축약 → ③ 목소리 80→40 → ④ 목소리 제거 → ⑤ 외형 170→100 →
      ⑥ 장면 의상 200→110 → ⑦ RULES 오디오 축약 → ⑧ 연기 70→55(핵심 절 유지) → (작업 147) 시선 55·ON SCREEN 세부 →
      ⑨ (맨 마지막) 대사 25(_fit_dialogue: 뒤 문장부터 통째로, 첫 문장 유지). 대사는 기본 상한이 없다(작업 147).
    그래도 넘치면 모든 후보에 ASSETS 인물 문장·ON SCREEN·MOTION·HARMONY·연기 핵심을 포함한 최소형으로
    재구성한다. 마지막에 최종 바이트·적용 단계 이름을 로그로 남긴다.

    **기호·분위기**(vnew-live-fx RA4, VNEW_FX 켬일 때만): 컷 항목 연기 다음에 `mood: {moodEn}`, 그다음
    `FX: {기호 문장}`(2개면 " / "). moodEn 안 한국 이름은 긴 이름부터 로마자로 바꾸고, 한글이 남으면 그 분위기는
    넣지 않는다(로그 vnew.gen.take_mood_dropped). moodEn 이 비면 분위기 조각이 없다(moodKo 는 쓰지 않는다, 결정 6).
    RULES 에 묘사 규칙(RULE_BODY: 손 두 개·머리색·무늬 위치·장소 고정)을 더한다.
    어느 컷에 기호·분위기가 있으면 줄이기 단계를 더한다: ④ 목소리 제거 다음 mood40, ⑧ RULES 오디오 축약 다음
    moodDrop → fxShort. 켬이면(기호가 없어도) 그다음 bodyShort(RULE_BODY → RULE_BODY_SHORT), 그다음 ⑨ action55.
    최소형은 연기를 남기는 후보에서만 기호 짧은 형을 남기고(분위기 없음), RULES 에 지금 묘사 규칙(보통 짧은 형)을 둔다.
    켬이면 최소형 후보에도 MOTION·HARMONY 를 넣는다(090 필수 수정 1): 연기 있는 후보 × 원문 → 연기 있는 후보 ×
    짧은 형(MOTION_MIN·_harmony_min) → 연기 없는 후보 × 원문 → × 짧은 형 → 의상을 줄인 ASSETS(assetsStripped) × 짧은 형
    → (그래도 안 들어가면) 지금 후보. 적용 단계 로그 끝에 motionHarmonyShort(짧은 형)·motionHarmonyDrop(빠짐)을 적는다.
    끔이면 이 모두가 없어 지금과 바이트 단위로 같다(fx-i).

    **실사 버전**(vnew-live-fx C.5, variant="real"): 틀·줄이기·타임스탬프 보존은 같고 조각만 바뀐다 — STYLE 은
    REAL_TAKE_STYLE(둘째 문장 없음), ASSETS 인물은 "(photo)"·"overrides the photo's clothing", 장소는 "(real photo)",
    TIMELINE 연기는 _real_action 뒤에 실사 표현(배경 기호 없음, FX 조각 없음), rule_replace·AVOID 는 실사 문장이다.
    외형·의상·목소리·연기·분위기·HARMONY 는 strip_drawing_words 를 지난다. 로그는 "vnew.gen.take_prompt variant=real".
    variant="webtoon"(기본)은 지금과 글자 하나 다르지 않다(lr-e).

    작업 127(VNEW_FX 켬일 때만): 맨 앞에 고정부 1줄(_fixed_head, 첫 컷 장소 기준 — 컷 프롬프트와 같은 글자)을 두고
    예산에서 미리 뺀다. 테이크 안에서 장소가 바뀌는 컷 항목에만 "BACKGROUND now: …"(그 장소 배경 문장)를 둔다.
    FX·실사 표현은 대상 인물(fxWho)이 있으면 그 사람에게만(이름은 ASSETS 로마자 규칙).
    """
    budget = _take_prompt_budget(caps)  # 작업 157: 글자. AI 고지(140)·고정부(127)까지 합친 프롬프트 전체를 잰다.
    fx_on = bool(vnew_settings.VNEW_FX)
    real = variant == "real"
    entries = _take_timeline_entries(plan, take)
    # 작업 160: 인물 장면 옷 = 장소 단위 1벌(_scene_costume — 수정 → 콘티·대본 → 장소 모든 컷 패널 읽기 다수 → 이 테이크 값, 146 정리).
    # 애니·실사, 원형·짧은 형·155 의상 고정이 같은 값을 쓴다.
    take_location = _take_location(plan, take)
    raw = {str(m.get("name") or ""): str(m.get("costume") or "") for m in cast if m.get("name")}
    bases = {str(k): str(v or "") for k, v in (bases or {}).items()}  # 작업 164: 캐릭터 기본 옷
    costume_logged: set | None = None if quiet else set()
    if real:
        # 작업 136: 실사 연기(지시서 actionEn) = 그림 문장 빼기·옷 구절 → 그 컷 장면 옷(_real_brief_text). 웹툰은 그대로.
        costumes = {name: _costume_guard(plan, name, _scene_costume(plan, name, take_location, fallback=value,
                                                                    base=bases.get(name, "")), bases.get(name, ""))
                    for name, value in raw.items()}
        for entry, cut_id in zip(entries, _seg_cut_ids(take)):
            entry["action"] = _real_brief_text(entry.get("action"),
                                               _scene_costume_map(plan, [cut_id], take_location, costumes, bases)).rstrip(".")
    if soften:
        _soften_entries(entries, _strong(soften))  # 작업 153: 콘텐츠 검사 거절 뒤 다시 만들 때만(대사·연기·분위기 순화, 줄 수·화자 그대로)
    _apply_lines_fix(entries, lines_fix)  # 작업 168 5단계(없으면 그대로)

    def _all_entry_lines(entry: dict) -> list[dict]:
        # lines 가 없는 항목(옛 모양)은 첫 대사 필드로 1줄을 만든다.
        if "lines" in entry:
            return list(entry.get("lines") or [])
        if str(entry.get("dialogue") or "").strip():
            return [{"speaker": entry.get("speaker"), "dialogue": entry.get("dialogue"), "thought": bool(entry.get("thought"))}]
        return []

    def _entry_lines(entry: dict) -> list[dict]:
        # 작업 157: 대사 줄은 빼지 않는다(옛 lineDrop·dialogue25 없음).
        return _all_entry_lines(entry)

    take_lines = [ln for e in entries for ln in _all_entry_lines(e)]
    take_voices = _prompt_voices(take_lines)

    # 이름 → 표기 매핑(로마자). 촬영 지시서/캐릭터의 영문 표기(nameEn/roman)가 있으면 그걸, 없으면
    # 간단 로마자 변환. ASSETS 는 "{한국 이름} ({로마자})" 로 소개하고, TIMELINE·gaze·화자는 로마자를 쓴다.
    name_map: dict[str, str] = {}
    for member in cast:
        ko = str(member.get("name") or "").strip()
        if not ko:
            continue
        roman = str(member.get("nameEn") or member.get("roman") or "").strip() or _romanize_name(ko)
        name_map[ko] = roman or ko

    def _disp_name(ko: str) -> str:
        return name_map.get(str(ko or "").strip(), str(ko or "").strip())

    def _fx_name(ko: str) -> str:
        # 작업 127: 기호 대상 이름 표기 — ASSETS 매핑이 있으면 그 로마자, 없으면 같은 로마자 변환 규칙.
        ko = str(ko or "").strip()
        return name_map[ko] if ko in name_map else (_romanize_name(ko) or ko)

    def _present(entry: dict) -> list[str]:
        return [str(item.get("name") or "") for item in entry.get("onScreen") or [] if item.get("name")]

    # 작업 127: 맨 앞 고정부(화풍·배경·overall tone, 켬일 때만). 예산에서 미리 빼 두어 어떤 줄이기에도 남는다.
    # 테이크 안에서 장소가 바뀌는 컷은 항목 안에 그 장소의 배경 문장을 한 번 더 쓴다(bg_change).
    fixed_head = fixed_head_compact = ""
    bg_change: dict[int, str] = {}
    bg_change_compact: dict[int, str] = {}
    if fx_on:
        from . import locations as loc_mod  # noqa: PLC0415

        # 장소를 모르면 "" → _background_en 이 대본 setting.locationEn 을 쓴다(컷 프롬프트와 같은 규칙, 같은 글자).
        first_location = next((str(e.get("locationEn") or "") for e in entries if e.get("locationEn")), "")
        fixed_head = _fixed_head(plan, first_location, real=real)
        fixed_head_compact = _fixed_head(plan, first_location, real=real, compact=True)
        previous = loc_mod.normalize_name(first_location)
        for index, entry in enumerate(entries):
            where = str(entry.get("locationEn") or "")
            if where and loc_mod.normalize_name(where) != previous:
                bg_change[index] = "BACKGROUND now: " + _background_en(plan, where, real=real)
                bg_change_compact[index] = "BACKGROUND now: " + _background_sentence(_background_en(plan, where, real=real))
                previous = loc_mod.normalize_name(where)

    def _bg_piece(entry: dict) -> str:
        idx = next((i for i, e in enumerate(entries) if e is entry), -1)
        # 작업 157: 짧은 형(BACKGROUND 1문장)은 고정부와 같은 단계(background)에서 바뀐다. state 는 아래에서 만든다.
        return (bg_change_compact if state["bg_compact"] else bg_change).get(idx, "")

    # vnew-live-fx RA4.1: 분위기 영어의 한국 이름 → 로마자(긴 이름부터). 한글이 남으면 그 컷 분위기는 뺀다.
    # 컷마다 한 번만 계산해 항목에 둔다(moodText). 끔이면 moodEn 이 비어 아무것도 하지 않는다.
    for entry in entries:
        mood = str(entry.get("moodEn") or "")
        for ko in sorted(name_map, key=len, reverse=True):
            mood = mood.replace(ko, name_map[ko])
        if mood and vnew_fx.HANGUL.search(mood):
            if not quiet:
                log.info(
                    "vnew.gen.take_mood_dropped take=%s at=%s-%ss reason=hangul",
                    str(take.get("id") or ""), entry.get("start"), entry.get("end"),
                )
            mood = ""
        entry["moodText"] = vnew_fx.strip_drawing_words(mood) if real else mood
    fx_content = vnew_fx.has_content([
        {"fx": vnew_fx.sentences(e.get("fx"), real=True) if real else e.get("fx"), "moodEn": e.get("moodText")}
        for e in entries
    ])
    # 원작 패널 참조(의상이 빈 인물의 "costume exactly as in the panels" 문구에 쓴다).
    if panel_nos:
        if len(panel_nos) == 1:
            panel_ref = f"@Image{panel_nos[0]}"
        else:
            panel_ref = f"@Image{panel_nos[0]}\u2013@Image{panel_nos[-1]}"
    else:
        panel_ref = "the original panels"

    style_en = "" if real else _first_take_style_en(plan)
    if real:
        # 실사 STYLE(결정 7). 원작 그림체 둘째 문장은 없다(그림 낱말이 남지 않게).
        style = REAL_TAKE_STYLE
    elif panel_nos:
        style = (
            f"STYLE: Modern TV-anime adaptation of the original webtoon {panel_ref}: keep the webtoon's "
            "faces, hair and line art; anime cel shading with anime compositing \u2014 soft rim light and "
            "color spill from the scene's light sources on skin and clothes, gentle bloom on practical "
            "lights, shallow depth of field with a softly blurred painted background."
        )
    else:
        style = (
            "STYLE: Modern TV-anime adaptation of the original webtoon: keep the webtoon's faces, hair "
            "and line art; anime cel shading with anime compositing \u2014 soft rim light and color spill "
            "from the scene's light sources on skin and clothes, gentle bloom on practical lights, "
            "shallow depth of field with a softly blurred painted background."
        )
    if style_en:
        # 원작 그림체 한 구절을 둘째 문장으로(작업 157: 넘치면 STYLE 이 통째로 짧은 형이 된다).
        style += f" Original art style: {style_en.rstrip('.')}."

    # RULES: @Video1 재현 + 인물 교체 + 추가 금지 + 기본 시선(Eyes) + 동기화 오디오(설계자 2026-10-26, 067).
    # 작업 157: 예산을 넘으면 통째로 짧은 형 1줄(TAKE_RULES_COMPACT — 같은 필수 문장)로 바꾼다.
    rule_reproduce = (
        "RULES: Reproduce @Video1's composition, camera, positions, cuts and timing exactly."
    )
    if no_text:
        # 작업 156: 화면 글자 검사로 다시 만드는 제출만 — RULES 맨 앞에 글자 금지(필수 문장이라 어떤 축약에서도 남는다).
        rule_reproduce = "RULES: " + TEXT_RETRY_RULE + " " + rule_reproduce[len("RULES: "):]
    rule_replace = (
        " Replace each mannequin with its person; keep face and hair identical to the reference sheet "
        "and the costume exactly as written in ASSETS in every cut."
    )
    if real:
        rule_replace = REAL_TAKE_RULE_REPLACE
    rule_eyes = (
        " Eyes: the on-screen speaker looks at the listener; an off-screen voice never appears on screen."
    )
    if take_voices in ("thought", "mixed"):
        # 지시서 117: 속마음 대사가 있는 테이크만. rule_eyes 는 어떤 축약·최소형에서도 남는 필수 문장이라 여기 둔다.
        # 118a: 첫 줄만이 아니라 컷의 모든 줄(lines)로 판정한다.
        rule_eyes += " During an inner voice-over that person's mouth stays closed and the lips do not move; the line is still heard in their voice."
    rule_extra = (
        " Do not add any person, action or scene. No mannequins remain. "
        "No text, no subtitles, no speech bubbles."
    )
    rules_head = rule_reproduce + rule_replace + rule_eyes + rule_extra
    audio_full = (
        " Generate synchronized audio: characters speak the Korean lines in their voices; natural "
        "ambient sound of the location; no background music, no narration."
    )
    avoid_full = REAL_TAKE_AVOID if real else (
        "AVOID: 3D CGI, plastic toy look, photorealistic skin, still-image pan or zoom, subtitles, burned-in text.")

    # MOTION·HARMONY(설계자 2026-10-26 재지시 v2). 둘 다 지우지 않는 등급(고정 본문, full). MOTION 은
    # 마네킹 흔들림을 따라 하지 말고 살아 움직이는 2차 동작·패럴랙스를 하라고, HARMONY 는 인물·배경
    # 빛·색온도·접지 그림자를 맞추라고 지시한다. 통 테이크라 둘 다 full.
    motion = _motion_line(True, take_voices)
    harmony = _harmony_line(plan, True)
    if real:
        harmony = vnew_fx.strip_drawing_words(harmony)  # 장소 빛 문장(lightEn)의 그림 낱말
    # 작업 157: 고정 부분의 짧은 형(각 1줄). 예산을 넘으면 TIMELINE 보다 먼저 이 형으로 바꾼다(TAKE_COMPACT_ORDER).
    inner = take_voices in ("thought", "mixed")
    style_compact = REAL_TAKE_STYLE_COMPACT if real else TAKE_STYLE_COMPACT.format(
        panels=(" " + panel_ref) if panel_nos else "")
    avoid_compact = REAL_TAKE_AVOID_COMPACT if real else TAKE_AVOID_COMPACT
    motion_compact = MOTION_MIN + (MOTION_MIN_INNER if inner else "")
    harmony_compact = _harmony_min(plan)
    rules_compact = ("RULES: " + TEXT_RETRY_RULE + " " if no_text else "RULES: ") + TAKE_RULES_COMPACT + (
        TAKE_RULES_COMPACT_INNER if inner else "")

    # --- 줄이기 상태(작업 157: 컷 내용 = 연기·닿는 연기·포즈·대사·ON SCREEN 은 빼지 않는다). ---
    state = {
        "appearance_limit": 170,   # ASSETS 인물 외형(얼굴·머리만, 원형)
        "voice_limit": 80,         # ASSETS 목소리(원형)
        "action_sentences": ACTION_SENTENCES,  # 작업 157: 연기 문장 수(마지막 단계에서만 1)
        "dialogue_limit": 0,       # 작업 147·157: 대사 글자 상한 없음(대사는 줄이지 않는다)
        "gaze_limit": 90,          # gaze 글자 상한(예외 컷에서만)
        "gaze_on": True,           # 작업 157: 컷 내용 줄이기 1단계(시선 빼기)
        "mood_on": True,           # 작업 157: 컷 내용 줄이기 2단계(분위기 빼기)
        "on_plain": False,         # ON SCREEN 부분 노출 괄호(작업 157: 빼지 않는다)
        "pose_on": True,           # 작업 148: 포즈 절(작업 157: 빼지 않는다)
        "contact_on": True,        # 작업 154: 닿는 연기 문장(작업 157: 빼지 않는다)
        "style": style,            # 작업 157: STYLE(→ style_compact)
        "motion": motion,          # MOTION(→ MOTION_MIN)
        "harmony": harmony,        # HARMONY(→ _harmony_min)
        "rules_compact": False,    # 작업 157: RULES 1줄
        "bg_compact": False,       # 작업 157: BACKGROUND 1문장(고정부·BACKGROUND now)
        "assets_compact": False,   # 작업 157: ASSETS 짧은 형(시트 문장 1번·의상 고정 합침·외형/목소리 없음)
        "costume_limit": 200,      # ASSETS 장면 의상 글자 상한
        "avoid": avoid_full,       # AVOID(→ avoid_compact)
        "audio": audio_full,       # RULES 오디오 문장(원형)
        "mood_limit": 80,          # vnew-live-fx 분위기 글자 상한
        "fx_short": False,         # vnew-live-fx 기호 짧은 형(컷 내용 줄이기 2단계)
        "body": RULE_BODY if fx_on else "",  # vnew-live-fx RA4.2 묘사 규칙(끔이면 없음)
    }
    applied: list[str] = []

    def _clip(text: str, limit: int) -> str:
        return _clip_words(text, limit)

    def _txt(value) -> str:
        # 실사 버전은 외형·의상·목소리 글에서 그림 낱말을 지운다(RC3.3). 웹툰은 지금 그대로.
        text = str(value or "").strip()
        return vnew_fx.strip_drawing_words(text) if real else text

    def _member_costume(member: dict) -> str:
        # 인물 장면 옷(원형·짧은 형 ASSETS 가 같은 값을 쓴다). 작업 160: 장소 단위 1벌(_scene_costume), 줄이기 상한으로 다시 정리.
        name = str(member.get("name") or "")
        if real:
            return _costume_guard(plan, name, _scene_costume(plan, name, take_location, fallback=raw.get(name, ""),
                                                             base=bases.get(name, ""), limit=state["costume_limit"]),
                                  bases.get(name, ""), limit=state["costume_limit"], logged=costume_logged)
        costume = _clip_words(_txt(member.get("costume")), state["costume_limit"])
        scene = _scene_costume(plan, name, take_location, fallback=str(member.get("costume") or ""),
                               base=bases.get(name, ""), limit=state["costume_limit"], real=False)
        # 작업 164: 제출 전 검사 — 조각이면 costume_invalid 로그 후 캐릭터 기본 옷.
        return _costume_guard(plan, name, scene or costume, bases.get(name, ""), limit=state["costume_limit"], real=False,
                              logged=costume_logged)

    def _location_line() -> str:
        if not location_no:
            return ""
        # 작업 143: 실사 장소는 사람을 지운 실사 사진(웹툰 뷰 편집) — live.reference_lead(place=True) 문구. 애니 줄은 그대로.
        if anchor is not None:
            return TAKE_ANCHOR_LOCATION_LINE.format(no=location_no)  # 작업 155: 조각·대체 제출의 장소 고정
        if real:
            # 작업 161: 상표 금지는 vibe(159, 기본 켬) 장소 줄에만. 이 옛 경로(VNEW_VIBE_PROMPT 끔 = 되돌리기)는 바이트 그대로 둔다.
            return (f"@Image{location_no} = the location (real photo, no people in it); "
                    "keep this place, its background and light in every shot")
        return f"@Image{location_no} = the location; keep its structure and props"

    def _assets_compact() -> str:
        # 작업 157: ASSETS 짧은 형 — 시트·사진 문장은 모든 사람에 1번, 155 의상 고정은 그 사람 COSTUME 문장에 합친다,
        # 외형·목소리 없음. 인물 매핑(색·이름·@Image·COSTUME)·프레임·장소 줄은 원형과 같다.
        parts = ["@Video1 = rough 3D previz (layout, blocking, camera and cuts only)"]
        sheets: list[int] = []
        fronts: list[int] = []
        photos = False
        for member in cast:
            if not real or member.get("look") == "anime":
                continue
            sheet_no = member.get("sheetNo")
            if sheet_no:
                sheets.append(sheet_no)
                fronts.extend(n for n in (member.get("refNos") or []) if n != sheet_no)
            elif member.get("frontNo"):
                photos = True
        if sheets:
            parts.append(REAL_TAKE_SHEETS_COMPACT.format(sheets=",".join(f"@Image{n}" for n in sheets)))
            if fronts:
                parts.append(REAL_TAKE_FRONTS_COMPACT.format(fronts=",".join(f"@Image{n}" for n in fronts)))
        if photos:
            parts.append(REAL_TAKE_PHOTOS_COMPACT)
        for member in cast:
            ko = str(member.get("name") or "").strip() or "a character"
            roman = _disp_name(ko)
            color = member.get("colorName") or "grey"
            label = f"{ko} ({roman})" if roman and roman != ko else ko
            front_no = member.get("frontNo")
            sheet_no = member.get("sheetNo") if real and member.get("look") != "anime" else None
            refs = [n for n in (member.get("refNos") or []) if n] or ([front_no] if front_no else [])
            if front_no and real and member.get("look") == "anime":
                sentence = f"The {color} mannequin is {label}: character design in {_image_refs(refs)}; {REAL_TAKE_ANIME_SENTENCE}"
            elif sheet_no:
                sentence = f"The {color} mannequin is {label} ({','.join(f'@Image{n}' for n in refs)})"
            elif front_no:
                sentence = f"The {color} mannequin is {label}: face and hair from " + (
                    ",".join(f"@Image{n}" for n in refs) if real else f"@Image{front_no}")
            else:
                sentence = f"The {color} mannequin is {label}"
            costume = _member_costume(member).rstrip(" .")
            if costume:
                lock = _costume_lock_tail(costume) if anchor is not None else ""
                sentence += f". COSTUME: {costume}" + (f"; {lock}" if lock else "")
            elif real:
                if front_no and not sheet_no:
                    sentence += f". COSTUME: as worn in @Image{front_no}"
                elif sheet_no:
                    sentence += f". {REAL_TAKE_NO_SHEET_CLOTHES}"
            else:
                sentence += f". COSTUME: exactly as in the original panels {panel_ref}"
            parts.append(sentence)
        if anchor is not None and anchor.get("frameNo"):
            parts.append(TAKE_ANCHOR_FRAME_LINE.format(no=anchor["frameNo"]))
        if _location_line():
            parts.append(_location_line())
        return "ASSETS: " + ". ".join(parts) + "."

    def _assets() -> str:
        # ASSETS 인물 문장은 어떤 경우에도 유지한다(색·이름·@Image·COSTUME). 작업 157: 짧은 형은 _assets_compact.
        if state["assets_compact"]:
            return _assets_compact()
        parts = ["@Video1 = rough 3D previz (layout, blocking, camera and cuts only)"]
        locks: list[str] = []  # 작업 155: 조각·대체 제출의 인물별 의상 고정 구절(anchor 가 있을 때만)
        if real and any(m.get("frontNo") and m.get("look") != "anime" for m in cast):
            # 작업 126: 3D 모델용 실사 사진과 같은 사람(ASSETS 머리 — 모든 줄이기·최소형 후보에 남는다).
            parts.append(REAL_TAKE_SHEET_SENTENCE)
        for member in cast:
            ko = str(member.get("name") or "").strip() or "a character"
            roman = _disp_name(ko)
            color = member.get("colorName") or "grey"
            appearance = (
                # 실사는 줄일 때도 조각 단위(머리 조각 유지, 중간 잘림 없음), 웹툰은 지금 그대로(단어 경계).
                (vnew_casting.fit_appearance if real else _clip)(_txt(member.get("appearance")), state["appearance_limit"])
                if state["appearance_limit"] > 0 else ""
            )
            front_no = member.get("frontNo")
            costume = _member_costume(member)
            if anchor is not None and costume:
                locks.append(_costume_lock(roman or ko, costume))
            voice = (
                _clip_words(_txt(member.get("voice")), state["voice_limit"])
                if state["voice_limit"] > 0 else ""
            )
            # 이름 소개: "{한국 이름} ({로마자})". 로마자가 이름과 같으면(이미 영문) 괄호 생략.
            label = f"{ko} ({roman})" if roman and roman != ko else ko
            sheet_no = member.get("sheetNo") if real and member.get("look") != "anime" else None
            # 얼굴·머리는 참조 정면 1장에서만(후면 제외 — 다른 의상 증거를 줄인다). 실사는 인물 사진 1장(photo).
            if front_no and real and member.get("look") == "anime":
                # 작업 126 인물 바꾸기 [애니]: 웹툰 정면·시트를 디자인 참고로, 실사 인물로 옮긴다.
                sentence = (f"The {color} mannequin is {label}: character design in "
                            f"{_image_refs(member.get('refNos') or [front_no])}; {REAL_TAKE_ANIME_SENTENCE}")
            elif sheet_no:
                # 작업 142: AI 캐릭터 시트(+정면) 문장은 외형 뒤에 붙인다(아래). 옷은 장면 COSTUME 이 이긴다.
                sentence = f"The {color} mannequin is {label}"
            elif front_no:
                sentence = f"The {color} mannequin is {label}: face and hair from @Image{front_no} " + (
                    "(photo)" if real else "(front)")
                more = [n for n in (member.get("refNos") or []) if n != front_no] if real else []
                if more:
                    sentence += f", same person in {_image_refs(more)}"
            else:
                sentence = f"The {color} mannequin is {label}"
            if appearance:
                sentence += f", {appearance.rstrip('.')}"
            if sheet_no:
                portrait = next((n for n in (member.get("refNos") or []) if n != sheet_no), None)
                sentence += ". " + _real_sheet_sentences(roman or ko, sheet_no, portrait, ref="@Image{}").rstrip(".")
            # COSTUME: override·패널에서 읽은 이 장면의 의상이 시트 복장을 덮어쓴다. 비면 패널을 따르게.
            # 실사: 사진의 옷을 덮어쓰고, 비면 사진에 입은 그대로(사진이 없으면 COSTUME 문장 없음).
            # 작업 142: 시트 인물은 시트 옷을 쓰지 않는다(장면 의상이 없으면 COSTUME 문장 없음).
            if costume:
                source = "photo" if real else "sheet"
                sentence += f". COSTUME in this scene (overrides the {source}'s clothing): {costume.rstrip('.')}"
            elif real:
                if front_no and not sheet_no:
                    sentence += f". COSTUME: as worn in @Image{front_no}"
                elif sheet_no:
                    sentence += f". {REAL_TAKE_NO_SHEET_CLOTHES}"
            else:
                sentence += f". COSTUME: exactly as in the original panels {panel_ref}"
            if voice:
                sentence += f". Voice: {voice.rstrip('.')}"
            parts.append(sentence)
        if anchor is not None and anchor.get("frameNo"):
            parts.append(TAKE_ANCHOR_FRAME_LINE.format(no=anchor["frameNo"]))  # 작업 155: 앞 샷 마지막 프레임
        if _location_line():
            parts.append(_location_line())
        parts.extend(locks)
        return "ASSETS: " + ". ".join(parts) + "."

    def _entry_gaze(entry: dict) -> str:
        # 기본 시선(화자↔듣는 사람 상호)이면 생략, 예외만 로마자로 렌더. gaze 문자열은 로마자 매핑 적용.
        if entry.get("gazeDefault"):
            return ""
        eyelines = entry.get("eyelines") or []
        return _gaze_text(eyelines, name_map)

    def _on_screen_text(entry: dict) -> str:
        # ON SCREEN 줄: 이 컷에 보이는 사람(로마자)과 how. 형식(설계자 2026-10-26, 067):
        #   1명 → "ON SCREEN: {A} only"
        #   main + 특수 how(full 아님) → "ON SCREEN: {A} (main), {B} (hands only)"
        #   여럿 full → "ON SCREEN: {A}, {B}"
        on_list = entry.get("onScreen") or []
        if not on_list:
            return "ON SCREEN: no one"
        if len(on_list) == 1:
            return f"ON SCREEN: {_disp_name(on_list[0].get('name') or '')} only"
        if state["on_plain"]:
            # 작업 147: 대사보다 먼저 줄이는 ON SCREEN 세부(부분 노출 괄호). 이름은 그대로 남는다.
            return "ON SCREEN: " + ", ".join(_disp_name(i.get("name") or "") for i in on_list)
        # how 가 full/main 아닌(부분 노출) 사람이 있으면 괄호로 표기한다.
        special = any(str(i.get("how") or "full") not in ("full", "main") for i in on_list)
        parts = []
        for item in on_list:
            name = _disp_name(item.get("name") or "")
            how = str(item.get("how") or "full")
            if how == "main" and special:
                parts.append(f"{name} (main)")
            elif how not in ("full", "main"):
                parts.append(f"{name} ({how})")
            else:
                parts.append(name)
        return "ON SCREEN: " + ", ".join(parts)

    def _line_piece(line: dict, off_speaker: str, limit: int) -> str:
        # 대사 한 줄 표기(지시서 117·118a): 속마음 > 화면 밖 > 보통. 글자 상한은 줄마다 적용한다.
        # 작업 147: 상한은 _fit_dialogue(뒤 문장부터 통째로, 첫 문장 유지, 문장 안 자르기 없음). 컷을 넘는 말(run)은
        # 첫 컷에 말 전체 + "이어짐", 다음 컷에 "이어서 끝맺음" + 이 컷 조각, 나눈 컷의 같은 대사는 "계속 말하는 중"(글 없음).
        speaker_ko = str(line.get("speaker") or "").strip()
        run = str(line.get("run") or "")
        who = _disp_name(speaker_ko) or "A character"
        tags = []
        if line.get("thought"):
            # 지시서 117: 속마음은 화면 밖 화자 표기보다 우선(입 다문 채 목소리만).
            tags.append("inner voice-over, lips closed")
        elif off_speaker and off_speaker == speaker_ko:
            tags.append("off-screen voice")
        if run == "still":
            return f"{who} ({'; '.join(tags + ['still speaking the same line'])})"
        source = line.get("utterance") if run == "start" else line.get("dialogue")
        dialogue = _fit_dialogue(str(source or "").strip(), limit)
        if not dialogue:
            return ""
        if run == "start":
            tags.append("one continuous line that carries over the next cut, no pause at the cut")
        elif run == "cont":
            tags.append("continues the same line from the previous shot, finishing with")
        if no_text:
            # 작업 156: 대사는 소리로만(화면 글자 검사로 다시 만들 때). 따옴표 대사를 글자로 그리던 test3 Wan 대응.
            who_tags = f'{who} ({"; ".join(tags)})' if tags else who
            return f'{who_tags}, {TEXT_RETRY_SPOKEN}: "{dialogue}"'
        if tags:
            return f'{who} ({"; ".join(tags)}): "{dialogue}"'
        return f'{who}: "{dialogue}"'

    def _contact_text(entry: dict) -> str:
        # 작업 154: 닿는 연기 1문장 = "{이름} {콘티 절}." 한국 이름은 ASSETS 로마자(긴 이름부터). 실사는 _real_action,
        # 순화(153)는 _soften_text — 그래도 노골적이거나 한글이 남으면 넣지 않는다.
        contact = entry.get("contact")
        if not state["contact_on"] or not isinstance(contact, dict) or not contact.get("text"):
            return ""
        text = str(contact.get("text") or "")
        for ko in sorted(set(contact.get("names") or []) | set(name_map), key=len, reverse=True):
            if ko:
                text = text.replace(ko, _fx_name(ko))
        sentence = f"{_fx_name(contact.get('name') or '')} {text}".strip()
        if real:
            sentence = _real_action(sentence)
        if soften:
            sentence = _soften_text(sentence, _strong(soften))
            if _explicit_text(sentence, _strong(soften)):
                return ""
        if vnew_fx.HANGUL.search(sentence):
            return ""
        return sentence.rstrip(" .") + "."

    def _entry_action(entry: dict) -> str:
        # 작업 157: 연기 = 앞 문장 action_sentences 개 통째로(_take_action). 실사는 애니와 같은 연기에 낱말 바꾸기만 한다:
        # 홍조 선·눈물 줄기를 실사 말로 바꾸고 연기 뒤에 실사 표현을 붙인다(FX 조각 없음, RC3.2).
        if real:
            return _with_real_expressions(
                _take_action(_real_action(entry.get("action") or ""), state["action_sentences"]), entry.get("fx"),
                entry.get("fxWho"), _present(entry), _fx_name)
        return _take_action(entry.get("action") or "", state["action_sentences"])

    def _entry_text(entry: dict) -> str:
        # 형식: "{s}-{e}s {샷 약어}: ON SCREEN …; {연기}; {화자}: \"{대사}\"[; gaze …]".
        # ON SCREEN 줄을 컷 항목 맨 앞(타임스탬프·샷 뒤)에 둔다. 화면 밖 화자 대사는 "(off-screen voice)".
        shot = str(entry.get("shot") or "").strip()
        head = f"{entry['start']}-{entry['end']}s"
        if shot:
            head += " " + shot
        pieces: list[str] = [_on_screen_text(entry)]
        if _bg_piece(entry):
            pieces.append(_bg_piece(entry))
        action = _entry_action(entry)
        if action:
            pieces.append(action)
        contact = _contact_text(entry)
        if contact:
            pieces.append(contact)
        # 작업 148: 사람마다 짧은 포즈 절(프리비즈 hands, 쓰는 손이 있을 때만). 대사보다 먼저 뺀다(poseDrop).
        if state["pose_on"]:
            for pose in entry.get("poses") or []:
                clause = _pose_clause(pose, _disp_name)
                if clause and _strong(soften):
                    clause = _soften_text(clause, True)  # 작업 163: 더 센 순화는 포즈 절의 애정 표현도 바꾼다
                if clause and not (soften and _explicit_text(clause, _strong(soften))):  # 작업 153: 순화 때 노골적인 포즈 절만 뺀다
                    pieces.append(clause)
        # vnew-live-fx RA4.1: 연기 → 분위기 → 기호 → 대사 → 시선. 끔이면 moodText·fx 가 비어 아무것도 붙지 않는다.
        mood = _clip(str(entry.get("moodText") or ""), state["mood_limit"]) if state["mood_on"] else ""
        if mood:
            pieces.append(f"mood: {mood}")
        fx_texts = [] if real else vnew_fx.who_sentences(
            entry.get("fx"), entry.get("fxWho"), _present(entry), short=state["fx_short"], name=_fx_name)
        if fx_texts:
            pieces.append("FX: " + (", " if state["fx_short"] else " / ").join(fx_texts))
        off_speaker = str(entry.get("offScreenSpeaker") or "").strip()
        # 지시서 118a: 컷의 모든 대사 줄(lines)을 순서대로. 표기 규칙은 줄마다 속마음 > 화면 밖 > 보통.
        for ln in _entry_lines(entry):
            piece = _line_piece(ln, off_speaker, state["dialogue_limit"])
            if piece:
                pieces.append(piece)
        # gaze(시선)는 기본과 다른 예외 컷에서만.
        gaze = _clip(_entry_gaze(entry), state["gaze_limit"]) if state["gaze_on"] else ""
        if gaze:
            pieces.append(gaze)
        body = "; ".join(pieces)
        return f"{head}: {body}" if body else head

    def _timeline() -> str:
        if not entries:
            return ""
        return "TIMELINE (follow @Video1's cuts exactly): " + " ".join(_entry_text(e) for e in entries)

    def _rules() -> str:
        # RULES 는 필수 문장(재현·인물 교체+의상·기본 시선 Eyes)을 항상 유지한다(설계자 2026-10-26, 067).
        # vnew-live-fx: 묘사 규칙(body)은 추가 금지 문장 뒤. 작업 157: 짧은 형은 같은 필수 내용을 1줄로(rules_compact).
        if state["rules_compact"]:
            return rules_compact
        return rules_head + state["body"] + state["audio"]

    def _style() -> str:
        return state["style"]

    def _assemble() -> str:
        # 순서: [실사 AI 고지(140, 늘 원문)] → [FIXED(127, 켬일 때만)] → STYLE → ASSETS → TIMELINE → MOTION → HARMONY
        # → RULES → AVOID. 작업 157: 맨 앞 두 줄도 예산 안에서 잰다(고정부 짧은 형 = BACKGROUND 1문장).
        head = fixed_head_compact if state["bg_compact"] else fixed_head
        blocks = [REAL_REF_AI_NOTE if real else "", head, _style(), _assets(), _timeline(), state["motion"],
                  state["harmony"], _rules(), state["avoid"]]
        return "\n".join(b for b in blocks if b)

    # 작업 157: 줄이기 순서. 먼저 고정 부분을 하나씩 짧은 형으로(TAKE_COMPACT_ORDER), 모두 짧은 형인데도 넘칠 때만 컷 내용을
    # 시선 → 분위기·기호 → 연기 첫 문장 순으로 줄인다. 대사·닿는 연기·포즈 절·ON SCREEN 은 줄이지 않는다. 매 단계 뒤 길이를
    # 재서 예산 안이면 멈춘다. 바뀌는 것이 없는 단계(짧은 형 = 원형, 시선·분위기 없는 테이크)는 건너뛴다.
    compact_steps = {
        "style": ("style", style_compact),
        "avoid": ("avoid", avoid_compact),
        "motion": ("motion", motion_compact),
        "harmony": ("harmony", harmony_compact),
        "rules": ("rules_compact", True),
        "background": ("bg_compact", True),
        "assets": ("assets_compact", True),
    }
    steps: list[tuple[str, object, str, str]] = []
    for section in TAKE_COMPACT_ORDER:
        field, value = compact_steps[section]
        same = (fixed_head == fixed_head_compact and bg_change == bg_change_compact) if section == "background" else (
            field in ("style", "avoid", "motion", "harmony") and state[field] == value)
        # 짧은 형이 원형과 같으면(고정부 없음 등) 이미 짧은 형이다 — 로그 단계 없이 compact 목록에만 적는다.
        steps.append((field, value, "" if same else section + "Compact", section))
    if any(_entry_gaze(e) for e in entries):
        steps.append(("gaze_on", False, "gazeDrop", ""))
    if any(e.get("moodText") for e in entries):
        steps.append(("mood_on", False, "moodDrop", ""))
    if not real and any(e.get("fx") for e in entries):
        steps.append(("fx_short", True, "fxShort", ""))
    if any(len(_split_sentences(str(e.get("action") or ""))) > 1 for e in entries):
        steps.append(("action_sentences", 1, "actionFirst", ""))

    prompt = _assemble()
    applied: list[str] = []
    compacted: list[str] = []
    for field, value, label, section in steps:
        if _prompt_len(prompt) <= budget:
            break
        state[field] = value
        if section:
            compacted.append(section)
        if not label:
            continue
        applied.append(label)
        prompt = _assemble()
    fits = _prompt_len(prompt) <= budget
    if not fits:
        # 작업 157: 대사·연기를 빼지 않는다. 제출 쪽(_prompt_split_parts)이 테이크를 조각으로 더 나눈다.
        applied.append("overBudget")

    lines_conti = lines_prompt = actions_prompt = 0
    actions: list[str] = []
    for entry in entries:
        off_speaker = str(entry.get("offScreenSpeaker") or "").strip()
        for ln in _all_entry_lines(entry):
            lines_conti += 1
            piece = _line_piece(ln, off_speaker, 0)
            if piece and piece in prompt:
                lines_prompt += 1
        action = _entry_action(entry)
        actions.append(action)
        if action and action in prompt:
            actions_prompt += 1
    if audit is not None:
        audit.update(cuts=len(entries), linesInConti=lines_conti, linesInPrompt=lines_prompt,
                     actionsInPrompt=actions_prompt, compact=list(compacted), chars=_prompt_len(prompt), budget=budget,
                     fits=fits, actions=actions)
    if quiet:
        return prompt
    if not fits:
        log.warning("vnew.gen.take_prompt_over_budget take=%s chars=%d budget=%d", str(take.get("id") or ""),
                    _prompt_len(prompt), budget)
    if real:
        # vnew-live-fx C2: 실사 프롬프트 바이트·줄이기 단계(개발계에서 실사 회차가 얼마나 줄었는지 본다).
        log.info("vnew.gen.take_prompt variant=real bytes=%d steps=%s", _utf8_len(prompt), ",".join(applied) or "none")
        return prompt
    log.info("vnew.gen.take_prompt bytes=%d steps=%s", _utf8_len(prompt), ",".join(applied) or "none")
    return prompt


# ---------------------------------------------------------------- 작업 159: video-vibecode 형식 통 테이크 프롬프트
#
# 참고 구현(whynot-20261029 vpoc/compose.py _cuts_video_prompt · live.py live_master/live_video_prompt/reference_lead)의 글자를
# 그대로 옮겼다. VNEW_VIBE_PROMPT 켬이면 _take_payload 가 _vibe_take_payload 로 간다(끄면 지금 _take_prompt 그대로).
VIBE_STYLE = {
    "live_action": (
        "Cinematic live-action film footage, photorealistic, natural skin texture, "
        "35mm lens, soft natural light, romance K-drama cinematography, soft glow, shallow depth of field, "
        "warm pastel color grading"
    ),
    "webtoon": (
        "Korean webtoon art style exactly as in the uploaded webtoon images: the same line work, "
        "coloring, shading and character designs"
    ),
}
VIBE_WEBTOON_PHRASE = ", with Korean webtoon visual symbols."  # live.WEBTOON_PHRASE (실사는 빠진다)
VIBE_MULTI_SHOT = (
    "A multi-shot sequence of {n} shots edited like a TV drama, with a hard cut at the start of "
    "each new shot. Each shot keeps one framing and the camera stays almost still within the shot. "
    "The same characters, outfits and setting appear in every shot, and each character's hair color and "
    "clothing colors stay exactly as described above in every shot. Each person has exactly two hands; "
    "only the hands described in a shot are visible."
)
# 줄이기 마지막 단계(head 단락 짧은 형). 컷 수·하드 컷·같은 인물/옷/장소·손 두 개를 남긴다.
VIBE_MULTI_SHOT_COMPACT = ("A multi-shot sequence of {n} shots with a hard cut at the start of each new shot; the same "
                           "characters, outfits and setting in every shot. Each person has exactly two hands.")
VIBE_STYLE_COMPACT = {"live_action": "Photoreal live-action K-drama footage, natural skin, soft natural light, "
                                     "shallow depth of field",
                      "webtoon": "Korean webtoon art style exactly as in the uploaded webtoon images"}
VIBE_LOCATION_LEAD = "@Image{no} is the location (no people in it); keep this place, its background and light in every shot"
VIBE_FRAME_LEAD = "@Image{no} is the previous shot's last frame: same background, lighting, outfits and hair; continue from it"
VIBE_PANEL_LEAD = "{refs} {be} the uploaded webtoon {images} (art style reference only)"  # fix1: 애니 원작 패널
VIBE_SHEET_CLAUSE = "AI-generated turnaround sheet of a fictional character; match face/hair/build, clothes from the look line"
# 작업 160: 정면 자리가 장면 옷 편집 그림(_costume_refs)인 인물의 시트 절·이름표.
VIBE_SHEET_COSTUME_CLAUSE = "AI-generated turnaround sheet of a fictional character; match face/hair/build, clothes come from @Image{no}"
REAL_COSTUME_REF_LABEL = "@Image{no} is {name} in this scene's clothes"
VIBE_VIDEO_LINE = ("@Video1 is the 3D previz: follow its camera, positions, cuts and timing exactly; "
                   "replace each mannequin with its person")
VIBE_SPOKEN = "spoken (audio only, never written on screen)"  # = TEXT_RETRY_SPOKEN(156, 아래에서 정의)
VIBE_COMPACT_ORDER = ("style", "background", "assets", "paragraph")  # 157 순서(고정 부분 먼저, head 단락 마지막)
_VIBE_ANGLE_EN = {"OTS": "over-the-shoulder", "profile": "profile", "3/4": "three-quarter view", "front": "front view",
                  "back": "from behind"}


def _join_names(names: list[str]) -> str:
    """compose._join_names: ["A"] → "A", ["A", "B", "C"] → "A, B and C"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _vibe_who(on_list: list[dict], handles: list[str], disp) -> str:
    """compose.who_in_shot 형식: "Only A appears in this shot; B is not visible. ". 모두 보이면 "" (부분 노출이 있으면
    "A and B (hands only) appear in this shot. "), 아무도 없으면 "No person appears in this shot. "."""
    shown: list[str] = []
    labels: list[str] = []
    for item in on_list or []:
        name = disp(str(item.get("name") or ""))
        if not name or name in shown:
            continue
        shown.append(name)
        how = str(item.get("how") or "full")
        labels.append(f"{name} ({how})" if how not in ("full", "main") else name)
    if not shown:
        return "No person appears in this shot. " if handles else ""
    rest = [h for h in handles if h and h not in shown]
    verb = "appears" if len(labels) == 1 else "appear"
    if not rest:
        return f"{_join_names(labels)} {verb} in this shot. " if labels != shown else ""
    be = "is" if len(rest) == 1 else "are"
    return f"Only {_join_names(labels)} {verb} in this shot; {_join_names(rest)} {be} not visible. "


# 작업 162: 손만 들어오는 화면 밖 인물(콘티 positionEn = prompts.OFF_FRAME_HAND_POSITION). "<주인>'s hand (off-frame) …" 머리를 뗀다.
_HAND_ONLY_HEAD = re.compile(
    r"^\s*(?P<name>.+?)['\u2019]s\s+(?P<noun>(?:[A-Za-z]+\s+){0,2}?(?:hands?|arms?|fingers?|palms?|thumbs?))\s*"
    r"\(\s*off[\s-]?(?:frame|screen)\s*\)\s*,?\s*", re.IGNORECASE)


def _hands_only_owners(cut: dict) -> list[dict]:
    """콘티 컷 characters 중 손만 보이는 화면 밖 인물 [{name, actionEn}] (콘티 순서). 없으면 [].
    작업 162 fix1: presence 가 있으면 presence == hand_only, 없으면(옛 콘티) positionEn == 고정 글."""
    return [ch for ch in cut.get("characters") or [] if isinstance(ch, dict) and str(ch.get("name") or "").strip()
            and (ch.get("presence") == "hand_only" if ch.get("presence")
                 else str(ch.get("positionEn") or "").strip() == _OFF_FRAME_HAND)]


def _hands_only_clause(owner: dict, names, disp, *, real: bool = False, soften: bool = False) -> str:
    """"Beomgeonu's hand enters from off-frame and strokes Yeouijun's cheek"(마침표 없음). 연기 글이 없거나 한글·노골적이면
    "Beomgeonu's hand enters from off-frame" 까지만."""
    who = disp(str(owner.get("name") or ""))
    action = " ".join(str(owner.get("actionEn") or "").split()).rstrip(" .")
    noun = "hand"
    head = _HAND_ONLY_HEAD.match(action)
    if head:
        noun = " ".join(head.group("noun").lower().split())
        action = action[head.end():]
    for ko in sorted({str(n) for n in names if n}, key=len, reverse=True):
        action = action.replace(ko, disp(ko))
    if real:
        action = _real_action(action)
    if soften:
        action = _soften_text(action, _strong(soften))
    action = action.strip(" .")
    plural = noun.endswith("s") and not noun.endswith("ss")
    base = f"{who}'s {noun} {'enter' if plural else 'enters'} from off-frame"
    if not action or vnew_fx.HANGUL.search(action) or (soften and _explicit_text(action, _strong(soften))):
        return base
    starts_with_name = any(action.startswith(disp(str(n))) for n in names if n)
    if action[1:2].islower() and not starts_with_name:
        action = action[0].lower() + action[1:]  # "Strokes …" → "strokes …"(이름으로 시작하면 그대로)
    return f"{base} and {action}"


def _vibe_who_hands(on_list: list[dict], handles: list[str], disp, clauses: list[str], owner_names: set[str]) -> str:
    """작업 162: 159 who_in_shot + 손만 들어오는 절. "Only A appears in this shot; B's hand enters from off-frame and …. "
    손 주인은 보이는 사람·안 보이는 사람 목록에서 뺀다(손 절이 따로 말한다)."""
    shown: list[str] = []
    labels: list[str] = []
    for item in on_list or []:
        name = disp(str(item.get("name") or ""))
        if not name or name in shown or name in owner_names:
            continue
        shown.append(name)
        how = str(item.get("how") or "full")
        labels.append(f"{name} ({how})" if how not in ("full", "main") else name)
    rest = [h for h in handles if h and h not in shown and h not in owner_names]
    parts = []
    if labels:
        parts.append(f"Only {_join_names(labels)} {'appears' if len(labels) == 1 else 'appear'} in this shot")
    if rest:
        parts.append(f"{_join_names(rest)} {'is' if len(rest) == 1 else 'are'} not visible")
    return "; ".join(parts + clauses) + ". "


# 작업 166: 컷마다 주인공·샷 크기·손 주인을 SHOT 줄 머리에 고정한다(애니·실사, 실사 홍조 문구는 실사만).
VIBE_INSERT_SIZE = "extreme close-up insert of hands only; no face, no head, no full body in frame"
VIBE_CLOSEUP_LOCK = "Close-up of {name}{cue} filling the frame"
VIBE_EDGE_PERSON = "{name} only partly visible at the frame edge"
VIBE_OUT_PERSON = "{name} not in frame"
VIBE_OWN_HANDS = "{name}'s own hands stay lowered, out of frame, and never touch {pos} own {part}"
VIBE_OWN_HANDS_OWNER = "; the only hand on {pos} {part} is {owner} entering from the frame edge"
REAL_BLUSH_LOCK = "a faint natural flush on {name}'s cheeks only; no red eyelids, no eye makeup"
REAL_BLUSH_LOCK_ANY = "a faint natural flush on the cheeks only; no red eyelids, no eye makeup"
_INSERT_WORD = re.compile(r"\binsert\b", re.IGNORECASE)
_CLOSEUP_WORD = re.compile(r"\b(?:extreme\s+)?close[\s-]?up\b", re.IGNORECASE)
_MEDIUM_CLOSEUP = re.compile(r"\bmedium\s+close[\s-]?up\b", re.IGNORECASE)
_NO_OWN_HANDS_EN = re.compile(r"\b(?:does\s+not|doesn['\u2019]t|do\s+not|don['\u2019]t|never)\s+use\s+(?:his|her|their)\s+own\s+"
                              r"hands?\b|\bwithout\s+using\s+(?:his|her|their)\s+own\s+hands?\b", re.IGNORECASE)
_NO_OWN_HANDS_KO = re.compile(r"(?:자기|자신의?|제)\s*손[은을]?\s*(?:쓰지|사용하지)\s*않")
_FACE_PARTS = frozenset(("cheek", "cheeks", "eye", "eyes", "face", "chin", "jaw", "forehead", "mouth", "lip", "lips", "ear",
                         "ears", "nose", "brow", "brows", "temple", "temples", "head", "hair"))
_HAND_PARTS = frozenset(("hand", "hands", "wrist", "wrists", "arm", "arms", "finger", "fingers", "palm", "palms"))
_HAIR_COLOR = re.compile(r"^(?:pale|light|dark|ash|platinum|golden|honey|jet|charcoal|silver|white|grey|gray|black|blond|blonde|"
                         r"brown|red|auburn|ginger|pink|blue|green|purple|violet|navy|chestnut|copper|sandy)(?:-[a-z]+)?$")
_REAL_FLUSH_WHO = re.compile(r"a faint natural flush on (?:(?P<who>[^,;.]+?)['\u2019]s|the) cheeks(?: only)?"
                             r"(?P<tail>; no red eyelids, no eye makeup)?")
_BLUSH_NEG = r"\b(?:does|do)\s+not\s+blush\b|\bno\s+blush\w*"
_BLUSH_MOD = r"(?:deeply|deep|faintly|faint|slightly|slight|brightly|bright|lightly|light|visibly|pink|red|crimson)"
_BLUSH_ANY = (
    r"\b(?:(?P<pro1>his|her|their)\s+)?(?:(?:a|an)\s+)?(?:" + _BLUSH_MOD + r"\s+)*(?:flush(?:ed|es|ing)?|blush(?:ed|es|ing)?|"
    r"red(?:dened)?)\s+(?:on\s+(?:his|her|their|the)\s+)?(?P<noun1>cheeks?|face)\b"
    r"|\b(?:(?:his|her|their)\s+)?(?:cheeks?|face)\s+(?:(?:" + _BLUSH_MOD + r"|turning|turns|turn|goes|go)\s+)*"
    r"(?:flushed|flush(?:es|ing)?|blush(?:es|ing)?|red(?:dened)?|crimson|pink)\b"
    r"|\b(?:(?:a|an)\s+)?(?:" + _BLUSH_MOD + r"\s+)*(?:flush(?:ed|es|ing)?|blush(?:ed|es|ing)?)(?:\s+(?:lines?|marks?))?"
    r"(?:\s+(?:on|across)\s+(?:his|her|their|the)\s+(?:cheeks?|face))?\b")
_BLUSH_SCAN = re.compile(r"(?P<real>" + _REAL_FLUSH_WHO.pattern + r")|(?P<neg>" + _BLUSH_NEG + r")|(?P<any>" + _BLUSH_ANY + r")",
                         re.IGNORECASE)


def _cut_named(cut: dict) -> list[dict]:
    """콘티 컷 characters 중 이름이 있는 dict(콘티 순서)."""
    return [ch for ch in cut.get("characters") or [] if isinstance(ch, dict) and str(ch.get("name") or "").strip()]


def _cut_on_screen_chars(cut: dict, owners: list[dict]) -> list[dict]:
    """작업 166: 화면에 보이는 콘티 인물. presence 가 있으면 on_screen, 없으면(옛 콘티) 손만 들어오는 사람이 아닌 사람."""
    owner_ids = {id(o) for o in owners}
    return [ch for ch in _cut_named(cut)
            if (ch.get("presence") == "on_screen" if ch.get("presence") else id(ch) not in owner_ids)]


def _vibe_is_insert(cut: dict, owners: list[dict]) -> bool:
    """작업 166: 인서트 컷 = 콘티 cameraEn·shotSize 에 "insert", 또는 보이는 사람 없이 손만 들어오는 사람이 있다."""
    if _INSERT_WORD.search(str(cut.get("cameraEn") or "")) or _INSERT_WORD.search(str(cut.get("shotSize") or "")):
        return True
    return bool(owners) and not _cut_on_screen_chars(cut, owners)


_ONLY_APPEARS = re.compile(r"^Only (?P<names>.+?) (?:appears|appear) in this shot(?=[;.])")


def _insert_who(who: str, cut: dict, owners: list[dict], disp) -> str:
    """작업 168: 인서트 컷 who 글의 "Only X appears in this shot" → "X's chest and hands only"(손만 들어오는 사람의 손이 X 몸에
    닿을 때 — 닿는 곳 = 콘티 hands "act>other_<곳>"), 아니면 "hands only". 그 뒤 절(손 절·안 보이는 사람)은 그대로.
    "Only …" 로 시작하지 않으면 그대로."""
    match = _ONLY_APPEARS.match(str(who or ""))
    if not match:
        return who
    on_screen = _cut_on_screen_chars(cut, owners)
    touched: list[str] = []
    for ch in on_screen:
        for owner in owners:
            if owner is ch:
                continue
            part = _hand_touch_part(owner, ch, on_screen, disp)
            if part and part.split(" ")[-1] not in _HAND_PARTS:
                touched.append(f"{disp(str(ch.get('name') or '').strip())}'s {part}")
                break
    head = _join_names(touched) + " and hands only" if touched else "hands only"
    return head + who[match.end():]


def _vibe_closeup_subject(cut: dict, shot: str, owners: list[dict], disp) -> str:
    """작업 166: 클로즈업 주인공(콘티 한글 이름) 또는 "". cameraEn 이 한 사람의 얼굴·클로즈업을 부르거나("closeup on X's …
    face", "X's … face"), 클로즈업에 presence on_screen 이 정확히 1명이면 그 사람."""
    named = _cut_named(cut)
    camera = " ".join(str(cut.get("cameraEn") or "").split())
    hits: list[str] = []
    for ch in named:
        ko = str(ch.get("name") or "").strip()
        for form in {ko, disp(ko)}:
            name = re.escape(form)
            if form and (re.search(r"\bclose[\s-]?up\s+(?:shot\s+)?(?:on|of)\s+" + name, camera, re.IGNORECASE)
                         or re.search(name + r"['\u2019]s\b[^.;]*?\bface\b", camera, re.IGNORECASE)):
                if ko not in hits:
                    hits.append(ko)
    owner_names = {str(o.get("name") or "").strip() for o in owners}
    mentioned = [ch for ch in named if any(form and form in camera for form in
                                           (str(ch.get("name") or "").strip(), disp(str(ch.get("name") or "").strip())))]
    if len(hits) == 1 and hits[0] not in owner_names and len(mentioned) == 1:
        return hits[0]
    size = str(cut.get("shotSize") or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    closeup = (size in ("closeup", "extremecloseup") or str(shot or "").split(" ")[0].upper() in ("CU", "ECU")
               or bool(_CLOSEUP_WORD.search(camera) and not _MEDIUM_CLOSEUP.search(camera)))
    on = [ch for ch in named if ch.get("presence") == "on_screen"]
    return str(on[0].get("name") or "").strip() if closeup and len(on) == 1 else ""


def _identity_cue(appearance: str) -> str:
    """작업 166: 외형 글의 머리색 짧은 신원 단서(" (pale blonde hair)"). 머리색 낱말이 없으면 ""."""
    words = re.findall(r"[A-Za-z][A-Za-z-]*", str(appearance or ""))
    for at, word in enumerate(words):
        if word.lower() == "hair":
            colors = [w.lower() for w in words[max(0, at - 4):at] if _HAIR_COLOR.match(w.lower())]
            if colors:
                return " (" + " ".join(colors) + " hair)"
    return ""


def _possessive(ch: dict, appearance: str) -> str:
    """작업 166: 인물 소유격(his/her/their) — 외형 글의 man/woman, 없으면 연기 글의 his/her."""
    look = str(appearance or "").lower()
    if re.search(r"\b(?:woman|girl|female|lady)\b", look):
        return "her"
    if re.search(r"\b(?:man|boy|male)\b", look):
        return "his"
    action = " " + str(ch.get("actionEn") or "").lower() + " "
    his, her = len(re.findall(r"\bhis\b", action)), len(re.findall(r"\bher\b", action))
    return "his" if his >= her and his else "her" if her else "their"


def _hand_touch_part(owner: dict, target: dict, on_screen: list[dict], disp) -> str:
    """작업 166: 손만 들어오는 사람(owner)의 손이 target 에 닿는 곳("cheek" …) 또는 "". 콘티 hands "act>other_<곳>" 을 읽고,
    "other" 는 보이는 사람이 1명이면 그 사람, 아니면 owner 연기 글이 부르는 사람."""
    ko = str(target.get("name") or "").strip()
    action = str(owner.get("actionEn") or "")
    meant = len(on_screen) == 1 or (ko and (ko in action or disp(ko) in action))
    hands = owner.get("hands") if isinstance(owner.get("hands"), dict) else {}
    for value in hands.values():
        _act, _, where = str(value or "").partition(">")
        if where.startswith("other_") and meant:
            return where[len("other_"):].replace("_", " ").strip()
    return ""


def _sleeve_cue(costume: str) -> str:
    """작업 166: 장면 옷 첫 옷 구절의 짧은 형 + " sleeve"("dark navy/black dress shirt sleeve"). 소매 길이·단추 낱말과
    with/in 뒤 설명은 뺀다. 옷이 없으면 ""."""
    first = re.split(r"[;,]", " ".join(str(costume or "").split()))[0].strip(" .")
    first = re.sub(r"^wearing\s+", "", first, flags=re.IGNORECASE)
    first = re.split(r"\s+(?:with|in|featuring|under|over)\s+", first, maxsplit=1)[0]
    first = " ".join(w for w in first.split()
                     if not re.match(r"^(?:long|short|half|three-quarter)-sleeved?$|^sleeveless$|^button-(?:up|down)$", w, re.I))
    if first[:1].isupper() and first[1:2].islower():
        first = first[0].lower() + first[1:]
    first = _clip_words(first, 40).rstrip(".")
    if not first:
        return ""
    return first if re.search(r"\b(?:sleeves?|cuffs?)$", first) else first + " sleeve"


def _vibe_own_hands(cut: dict, owners: list[dict], disp, appearance_of, costume_of) -> list[tuple[str, str]]:
    """작업 166: 자기 손 잠금 [(콘티 한글 이름, 문장(마침표 없음))]. 대상 = presence on_screen 이면서 연기 글이 자기 손을 쓰지
    않는다고 하거나, 손만 들어오는 다른 사람의 손이 닿고 자기 hands 항목이 없는 사람. 손 주인 옷 소매 단서는 장면 옷에서."""
    out: list[tuple[str, str]] = []
    on_screen = [ch for ch in _cut_named(cut) if ch.get("presence") == "on_screen"]
    for ch in on_screen:
        ko = str(ch.get("name") or "").strip()
        refuses = bool(_NO_OWN_HANDS_EN.search(str(ch.get("actionEn") or ""))
                       or _NO_OWN_HANDS_KO.search(str(ch.get("actionKo") or "")))
        owner, part = None, ""
        for cand in owners:
            if str(cand.get("name") or "").strip() == ko:
                continue
            where = _hand_touch_part(cand, ch, on_screen, disp)
            if where:
                owner, part = cand, where
                break
        if not refuses and (owner is None or ch.get("hands")):
            continue
        if not refuses and part.split(" ")[-1] in _HAND_PARTS:
            continue  # 손을 잡는 손은 자기 손 혼동이 아니다
        if owner is None and owners:
            owner = next((o for o in owners if str(o.get("name") or "").strip() != ko), None)
        part = "face" if refuses or not part or part.split(" ")[-1] in _FACE_PARTS else part
        pos = _possessive(ch, appearance_of(ko))
        text = VIBE_OWN_HANDS.format(name=disp(ko), pos=pos, part=part)
        if owner is not None:
            owner_ko = str(owner.get("name") or "").strip()
            sleeve = _sleeve_cue(costume_of(owner_ko))
            who = f"{disp(owner_ko)}'s ({sleeve})" if sleeve else f"{disp(owner_ko)}'s hand"
            text += VIBE_OWN_HANDS_OWNER.format(pos=pos, part=part, owner=who)
        out.append((ko, text))
    return out


def _blush_target(text: str, start: int, people: dict[str, str]) -> str:
    """작업 166: 홍조 구절의 사람(로마자 handle) — 바로 앞이 "<이름>'s " 면 그 사람, 아니면 같은 문장에서 처음 불린 사람(주어).
    없으면 ""."""
    stop = text.rfind(". ", 0, start)
    sentence_at = stop + 2 if stop >= 0 else 0
    before = text[sentence_at:start]
    found: list[tuple[int, int, str]] = []
    for token, handle in people.items():
        for m in re.finditer(r"(?<![A-Za-z\uac00-\ud7a3])" + re.escape(token) + r"(?![A-Za-z\uac00-\ud7a3])", before):
            found.append((m.start(), m.end(), handle))
    for _s, end, handle in sorted(found, key=lambda f: -f[1]):
        if re.fullmatch(r"['\u2019]s\s+", before[end:]):
            return handle
    return min(found)[2] if found else ""


def _real_blush_pieces(pieces: list[str], who: str, people: dict[str, str] | None = None) -> list[str]:
    """작업 166: 실사 SHOT 조각들의 홍조 글 → REAL_BLUSH_LOCK(이름 붙은 사람만). 실사 표현 문장("a faint natural flush on X's
    cheeks only")은 꼬리를 붙이고, 그 밖의 홍조 구절("cheeks deeply flushed" …)은 지운다. 표현 문장이 없으면 처음 지운 조각 끝에
    ", " + 문구 — 이름 = 그 구절 앞 같은 문장에서 불린 사람(people: 이름 조각 → handle), 없으면 who, 그것도 없으면 이름 없는 형.
    "does not blush" 는 그대로. 홍조 글이 없으면 그대로."""
    has_who = any(_REAL_FLUSH_WHO.search(p or "") for p in pieces)
    out: list[str] = []
    added = has_who
    for piece in pieces:
        text = str(piece or "")
        spans = []
        target = ""
        for m in _BLUSH_SCAN.finditer(text):
            if m.group("real") is not None:
                spans.append((m.start(), m.end(), REAL_BLUSH_LOCK.format(name=m.group("who")) if m.group("who")
                              else REAL_BLUSH_LOCK_ANY))
            elif m.group("any") is not None:
                if not target and not added:
                    target = _blush_target(text, m.start(), people or {}) or who
                if m.group("noun1") and (m.group("pro1") or re.search(r"['\u2019]s\s+$", text[:m.start()])):
                    # "Uijun's flushed cheek" → "Uijun's cheek": 소유격 뒤 명사는 남긴다(문장이 깨지지 않게)
                    spans.append((m.start(), m.end(), "\0" + (m.group("pro1") + " " if m.group("pro1") else "")
                                  + m.group("noun1")))
                else:
                    spans.append((m.start(), m.end(), None))
        removed = False
        for start, end, repl in reversed(spans):
            if repl is not None:
                removed = removed or repl.startswith("\0")
                text = text[:start] + repl.lstrip("\0") + text[end:]
                continue
            removed = True
            before, after = text[:start], text[end:]
            if re.search(r"\bwith\s+$", before) and re.match(r"\s*(?:[,.;]|$)", after):
                before = re.sub(r"\s*\bwith\s+$", "", before)
            elif after.startswith(", ") and not before.endswith(", "):
                after = after[2:]
            elif before.endswith(", "):
                before = before[:-2]
            elif after.startswith(" and "):
                after = after[5:]
            elif re.search(r"\band\s+$", before):
                before = re.sub(r"\s*\band\s+$", "", before)
            if (not before.strip() or before.rstrip().endswith(".")) and after.lstrip()[:1].islower():
                lead = after[: len(after) - len(after.lstrip())]
                after = lead + after.lstrip()[0].upper() + after.lstrip()[1:]
            text = before + after
        if removed:
            text = re.sub(r"\s{2,}", " ", text)
            text = re.sub(r"\s+([,.;])", r"\1", text)
            text = re.sub(r"([,;])(?=[,.;])", "", text)
            text = re.sub(r"\.(?:\s*\.)+", ".", text).strip()
            text = re.sub(r"^[,;.]\s*", "", text)
            if not added:
                lock = REAL_BLUSH_LOCK.format(name=target) if target else REAL_BLUSH_LOCK_ANY
                text = (text.rstrip(" .,;") + ", " if text.strip(" .,;") else "") + lock
                added = True
        out.append(text)
    return out


def _vibe_size(shot: str) -> str:
    """샷 약어("MCU front") → 참고 구현 샷 크기 표기("medium close-up, front view")."""
    size, _, angle = str(shot or "").strip().partition(" ")
    words = SHOT_SIZE_EN.get(size.upper(), "medium shot")
    return words + (", " + _VIBE_ANGLE_EN.get(angle, angle) if angle else "")


def _vibe_take_prompt(plan: dict, take: dict, *, real: bool, cast: list[dict], location_no=None, frame_no=None,
                      panel_nos: list[int] | None = None, caps: dict | None = None, soften: bool = False,
                      anchor: bool = False, no_text: bool = False, audit: dict | None = None, quiet: bool = False,
                      bases: dict | None = None, lines_fix: dict | None = None, framing: dict | None = None) -> str:
    """작업 159: video-vibecode 형식 통 테이크 프롬프트(참고 구현 _cuts_video_prompt · live_video_prompt).

    순서: 140 AI 고지 → 화풍(STYLE webtoon + 웹툰 기호 / live_action) → 인물별 "{handle}: {look} (keep exactly this look)." →
    컷 지시 단락(참고 원문) → "Setting (keep identical in every shot): …" → "Shot i (t0-t1s), size: " + who_in_shot + 연기
    (+154 닿는 연기 + 148 포즈) + FX + 카메라 + 조명 + 대사(147/157 모든 줄, spoken (audio only …)) → @Image 설명(reference_lead
    문구, 시트 절 1번, fix1: 애니 원작 패널 = STYLE 이 부르는 "the uploaded webtoon images") → @Video1 줄. look = 우리 인물 외형 + 146 장면 옷(실사는 실사 외모 문장). 실사는 live_master 처럼 화풍
    live_action, 연기 _real_action + REAL_FX(fx.REAL_FX), FX 조각 없음.
    예산(157 글자): 화풍 → 배경 → 인물(외형 빼고 옷만) → 컷 지시 단락 순으로 짧은 형. 샷 문장은 줄이지 않는다. 그래도 넘치면
    overBudget → 제출 쪽이 157 조각으로 나눈다. audit 는 _take_prompt 와 같은 키.
    """
    budget = _take_prompt_budget(caps)
    take_cut_ids = [str(c) for c in _seg_cut_ids(take)]
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    entries = _take_timeline_entries(plan, take)
    raw = {str(m.get("name") or ""): str(m.get("costume") or "") for m in cast if m.get("name")}
    # 작업 160: 장면 옷 = 장소 단위 1벌(_scene_costume, 테이크 첫 장소). 모든 테이크·조각·대체가 같은 값(애니는 그림 낱말을 지우지
    # 않는다). 애니는 쓸 만한 옷이 없을 때만 지금 값(200자) 그대로.
    take_location = _take_location(plan, take)
    bases = {str(k): str(v or "") for k, v in (bases or {}).items()}  # 작업 164: 캐릭터 기본 옷(콘티·대본 다음 후보, 검사 실패 대체)
    costumes = {name: _scene_costume(plan, name, take_location, fallback=value, base=bases.get(name, ""), real=real)
                for name, value in raw.items()}
    if not real:
        costumes = {name: costumes.get(name) or _clip_words(value, 200) for name, value in raw.items()}
    # 작업 164: 제출 전 검사 — look 줄 옷이 조각이면 costume_invalid 로그 후 캐릭터 기본 옷.
    logged: set | None = None if quiet else set()
    costumes = {name: _costume_guard(plan, name, value, bases.get(name, ""), real=real, logged=logged)
                for name, value in costumes.items()}
    if framing is not None:
        framing["costumes"] = dict(costumes)  # 작업 169: Kling 컷 COSTUME 고정(같은 장면 옷)
    if real:
        # 136: 실사 연기의 그림 문장·옷 구절 → 장면 옷.
        for entry, cut_id in zip(entries, take_cut_ids):
            entry["action"] = _real_brief_text(entry.get("action"),
                                               _scene_costume_map(plan, [cut_id], take_location, costumes, bases)).rstrip(".")
    if soften:
        _soften_entries(entries, _strong(soften))  # 153
    _apply_lines_fix(entries, lines_fix)  # 작업 168 5단계(없으면 그대로)

    name_map: dict[str, str] = {}
    for member in cast:
        ko = str(member.get("name") or "").strip()
        if ko:
            name_map[ko] = str(member.get("nameEn") or member.get("roman") or "").strip() or _romanize_name(ko) or ko

    def _disp(ko: str) -> str:
        ko = str(ko or "").strip()
        return name_map[ko] if ko in name_map else (_romanize_name(ko) or ko)

    def _txt(value) -> str:
        text = " ".join(str(value or "").split())
        return vnew_fx.strip_drawing_words(text) if real else text

    handles = [_disp(str(m.get("name") or "")) for m in cast if str(m.get("name") or "").strip()]
    # 작업 166: 실사 홍조 대상 찾기용 이름 조각(한글 이름 · handle · 이름만(성 뺌) 한글/로마자) → handle.
    people: dict[str, str] = {}
    for member in cast:
        ko = str(member.get("name") or "").strip()
        if ko:
            for token in (ko, _disp(ko), ko[1:] if len(ko) >= 3 else "", _romanize_name(ko[1:]) if len(ko) >= 3 else ""):
                if token and len(token) >= 2:
                    people.setdefault(token, _disp(ko))
    state = {"style": False, "background": False, "assets": False, "paragraph": False}

    def _style() -> str:
        key = "live_action" if real else "webtoon"
        if state["style"]:
            text = VIBE_STYLE_COMPACT[key] + ("" if real else VIBE_WEBTOON_PHRASE.rstrip("."))
        else:
            text = VIBE_STYLE[key] + ("" if real else VIBE_WEBTOON_PHRASE.rstrip("."))
        return text + "." + (" " + TEXT_RETRY_RULE[0].upper() + TEXT_RETRY_RULE[1:] if no_text else "")

    def _look_line(member: dict) -> str:
        ko = str(member.get("name") or "").strip()
        if not ko:
            return ""
        appearance = "" if state["assets"] else _txt(member.get("appearance")).rstrip(" .")
        costume = _txt(costumes.get(ko) or "").rstrip(" .")
        if costume[:1].isupper() and costume[1:2].islower():
            costume = costume[0].lower() + costume[1:]  # "wearing black …" (머리글자만, 약어는 그대로)
        bits = [appearance, f"wearing {costume}" if costume else ""]
        if real and member.get("look") == "anime":
            bits.append(REAL_TAKE_ANIME_SENTENCE)  # 126 인물 바꾸기 [애니]
        look = ", ".join(b for b in bits if b)
        if anchor and costume:
            look += "; " + _costume_lock_tail(costume)  # 155 조각·대체: 옷 고정
        return f"{_disp(ko)}: {look} (keep exactly this look)." if look else ""

    def _paragraph() -> str:
        return (VIBE_MULTI_SHOT_COMPACT if state["paragraph"] else VIBE_MULTI_SHOT).format(n=len(entries))

    first_loc = next((str(cuts_by_id.get(cid, {}).get("locationEn") or "") for cid in take_cut_ids
                      if cuts_by_id.get(cid, {}).get("locationEn")), "")

    def _bg(where: str) -> str:
        text = _background_en(plan, where, real=real)
        return _background_sentence(text) if state["background"] else text

    def _setting() -> str:
        return f"Setting (keep identical in every shot): {_bg(first_loc)}."

    from . import locations as loc_mod  # noqa: PLC0415

    moves: dict[int, str] = {}
    previous = loc_mod.normalize_name(first_loc)
    for index, cut_id in enumerate(take_cut_ids):
        where = str(cuts_by_id.get(cut_id, {}).get("locationEn") or "")
        if where and loc_mod.normalize_name(where) != previous:
            moves[index] = where
            previous = loc_mod.normalize_name(where)

    def _line_piece(line: dict, off_speaker: str) -> str:
        # 147/157: 모든 줄, 한 줄 한 컷, 이어지는 말(run). 대사·속마음은 늘 소리로만(spoken (audio only …)).
        speaker_ko = str(line.get("speaker") or "").strip()
        run = str(line.get("run") or "")
        who = _disp(speaker_ko) or "A character"
        tags = []
        if line.get("thought"):
            tags.append("inner voice-over, lips closed")
        elif off_speaker and off_speaker == speaker_ko:
            tags.append("off-screen voice")
        if run == "still":
            return f"{who} ({'; '.join(tags + ['still speaking the same line'])})"
        source = line.get("utterance") if run == "start" else line.get("dialogue")
        dialogue = _fit_dialogue(str(source or "").strip(), 0)
        if not dialogue:
            return ""
        if run == "start":
            tags.append("one continuous line that carries over the next cut, no pause at the cut")
        elif run == "cont":
            tags.append("continues the same line from the previous shot, finishing with")
        who_tags = f'{who} ({"; ".join(tags)})' if tags else who
        return f'{who_tags}, {VIBE_SPOKEN}: "{dialogue}"'

    def _entry_lines(entry: dict) -> list[dict]:
        if "lines" in entry:
            return list(entry.get("lines") or [])
        if str(entry.get("dialogue") or "").strip():
            return [{"speaker": entry.get("speaker"), "dialogue": entry.get("dialogue"), "thought": bool(entry.get("thought"))}]
        return []

    def _present(entry: dict) -> list[str]:
        return [str(item.get("name") or "") for item in entry.get("onScreen") or [] if item.get("name")]

    def _contact(entry: dict) -> str:
        contact = entry.get("contact")
        if not isinstance(contact, dict) or not contact.get("text"):
            return ""
        text = str(contact.get("text") or "")
        for ko in sorted(set(contact.get("names") or []) | set(name_map), key=len, reverse=True):
            if ko:
                text = text.replace(ko, _disp(ko))
        sentence = f"{_disp(contact.get('name') or '')} {text}".strip()
        if real:
            sentence = _real_action(sentence)
        if soften:
            sentence = _soften_text(sentence, _strong(soften))
            if _explicit_text(sentence, _strong(soften)):
                return ""
        if vnew_fx.HANGUL.search(sentence):
            return ""
        return sentence.rstrip(" .")

    def _blush_who(entry: dict) -> str:
        # 작업 166: 실사 홍조 문구가 붙는 사람 = 기호 대상(fxWho blush lines) 첫 사람, 없으면 화면에 온전히 보이는 1명.
        targets = [str(n) for n in ((entry.get("fxWho") or {}).get("blush lines") or []) if str(n or "").strip()]
        if targets:
            return _disp(targets[0])
        full = [str(i.get("name") or "") for i in entry.get("onScreen") or []
                if i.get("name") and str(i.get("how") or "full") in ("full", "main")]
        return _disp(full[0]) if len(full) == 1 else ""

    def _action(entry: dict) -> str:
        # 실사 = live.real_action(_real_action + REAL_FX 실사 표현), 애니 = 지시서 연기 그대로(157 앞 2문장).
        if real:
            text = _with_real_expressions(_take_action(_real_action(entry.get("action") or "")), entry.get("fx"),
                                          entry.get("fxWho"), _present(entry), _disp)
            text = _real_blush_pieces([str(text or "")], _blush_who(entry), people)[0]  # 작업 166
        else:
            text = _take_action(entry.get("action") or "")
        return str(text or "").strip().rstrip(".")

    appearances = {str(m.get("name") or "").strip(): str(m.get("appearance") or "") for m in cast}

    def _closeup_who(subject: str, entry: dict, cut: dict, clauses: list[str], owner_names: set[str]) -> str:
        # 작업 166: "Close-up of A (cue) filling the frame; B only partly visible at the frame edge; C not in frame; <손 절>. "
        name = _disp(subject)
        visible = {_disp(str(i.get("name") or "")) for i in entry.get("onScreen") or [] if i.get("name")}
        visible |= {_disp(str(ch.get("name") or "")) for ch in _cut_named(cut) if ch.get("presence") == "on_screen"}
        pieces = [VIBE_CLOSEUP_LOCK.format(name=name, cue=_identity_cue(appearances.get(subject, "")))]
        seen = {name} | set(owner_names)
        for other in [_disp(str(ch.get("name") or "")) for ch in _cut_named(cut)] + handles:
            if other and other not in seen:
                seen.add(other)
                pieces.append((VIBE_EDGE_PERSON if other in visible else VIBE_OUT_PERSON).format(name=other))
        return "; ".join(pieces + clauses) + ". "

    def _shot(index: int, entry: dict, cut_id: str) -> str:
        cut = cuts_by_id.get(cut_id) or {}
        # 작업 162: 손만 들어오는 화면 밖 인물이 있으면 who_in_shot 에 손 절을 붙인다(없으면 159 그대로).
        hand_owners = _hands_only_owners(cut)
        # 작업 166: 인서트 크기 · 클로즈업 주인공 · 자기 손 잠금(해당 없는 컷은 지금 그대로).
        insert = _vibe_is_insert(cut, hand_owners)
        subject = "" if insert else _vibe_closeup_subject(cut, entry.get("shot"), hand_owners, _disp)
        owner_names = {_disp(str(o.get("name") or "")) for o in hand_owners}
        clauses: list[str] = []
        if hand_owners:
            names = [str(c.get("name") or "") for c in cut.get("characters") or [] if isinstance(c, dict)] + list(name_map)
            clauses = [_hands_only_clause(o, names, _disp, real=real, soften=soften) for o in hand_owners]
        if subject:
            who = _closeup_who(subject, entry, cut, clauses, owner_names)
        elif hand_owners:
            who = _vibe_who_hands(entry.get("onScreen") or [], handles, _disp, clauses, owner_names)
        else:
            who = _vibe_who(entry.get("onScreen") or [], handles, _disp)
        if insert:
            who = _insert_who(who, cut, hand_owners, _disp)  # 작업 168: 인서트는 "Only X appears" 대신 몸·손만
        locks = _vibe_own_hands(cut, hand_owners, _disp, lambda ko: appearances.get(ko, ""), lambda ko: costumes.get(ko) or "")
        locked = {ko for ko, _text in locks}
        own = "".join(text + ". " for _ko, text in locks)
        size = VIBE_INSERT_SIZE if insert else _vibe_size(entry.get("shot"))
        prefix = f"Shot {index + 1} ({entry['start']}-{entry['end']}s), {size}: " + (own + who if real else who + own)
        if framing is not None:
            # 작업 169: 컷 단위 Kling 샷 고정 줄의 재료(크기 · 자기 손 + who, 화면에 보이는 인물)
            framing.setdefault("shots", {})[cut_id] = {"size": size, "lead": own + who if real else who + own,
                                                       "names": _present(entry)}
        parts = []
        if index in moves:
            parts.append(f"Setting now: {_bg(moves[index])}")
        parts.append(_action(entry))
        contact_name = str((entry.get("contact") or {}).get("name") or "").strip()
        if not any(str(o.get("name") or "").strip() == contact_name for o in hand_owners) and contact_name not in locked:
            parts.append(_contact(entry))  # 162: 손 주인의 닿는 연기는 손 절이 이미 말한다(166: 자기 손 잠금 인물의 손 연기도 뺀다)
        for pose in entry.get("poses") or []:
            if any(str(o.get("name") or "").strip() == str(pose.get("name") or "").strip() for o in hand_owners):
                continue  # 162 fix1: 손만 들어오는 사람의 손은 손 절이 말한다
            if str(pose.get("name") or "").strip() in locked:
                continue  # 166: 자기 손을 쓰지 않는 사람의 손 포즈는 넣지 않는다
            clause = _pose_clause(pose, _disp)
            if clause and _strong(soften):
                clause = _soften_text(clause, True)  # 작업 163
            if clause and not (soften and _explicit_text(clause, _strong(soften))):
                parts.append(clause)
        if not real:
            fx_texts = vnew_fx.who_sentences(entry.get("fx"), entry.get("fxWho"), _present(entry), name=_disp)
            parts.append(" / ".join(fx_texts))
        for field in ("cameraEn", "lightEn"):
            value = _txt(cut.get(field)).rstrip(" .")
            if value and not vnew_fx.HANGUL.search(value):
                parts.append(value)
        if real:
            parts = _real_blush_pieces(parts, _blush_who(entry) or (_disp(subject) if subject else ""), people)  # 작업 166
        off_speaker = str(entry.get("offScreenSpeaker") or "").strip()
        parts.extend(_line_piece(ln, off_speaker) for ln in _entry_lines(entry))
        body = ""
        for piece in (p.strip() for p in parts if p and p.strip()):
            # 문장 사이는 ". "(참고 구현), 따옴표 대사 뒤에는 마침표를 붙이지 않는다.
            body += ("" if not body else " " if body.endswith(('"', ")")) else ". ") + piece
        return (prefix + body).rstrip() + ("" if body.endswith(('"', ")")) else ".")

    shots = [_shot(i, e, cid) for i, (e, cid) in enumerate(zip(entries, take_cut_ids))]

    def _labels() -> str:
        rows: list[tuple[int, str]] = []
        sheets: list[int] = []
        costume_sheets: list[str] = []
        for member in cast:
            handle = _disp(str(member.get("name") or ""))
            nos = [n for n in (member.get("refNos") or []) if n] or ([member["frontNo"]] if member.get("frontNo") else [])
            costume_no = member.get("costumeNo")
            for no in nos:
                # 작업 160: 정면 자리의 장면 옷 편집 그림은 "in this scene's clothes"
                rows.append((no, REAL_COSTUME_REF_LABEL.format(no=no, name=handle) if no == costume_no
                             else f"@Image{no} is {handle}"))
            if member.get("sheetNo") and costume_no in nos:
                costume_sheets.append(f"@Image{int(member['sheetNo'])}: "
                                      + VIBE_SHEET_COSTUME_CLAUSE.format(no=costume_no) + ".")
            elif member.get("sheetNo"):
                sheets.append(int(member["sheetNo"]))
        if frame_no:
            rows.append((frame_no, VIBE_FRAME_LEAD.format(no=frame_no)))
        if location_no:
            rows.append((location_no, VIBE_LOCATION_LEAD.format(no=location_no)
                         + (REAL_TAKE_NO_BRAND if real else "")))  # 작업 161: 실사 장소만 상표 금지
        panels = [n for n in (panel_nos or []) if n] if not real else []
        if panels:
            # fix1: 원작 패널(옛 _take_prompt 와 같은 패널·순서)을 STYLE 의 "the uploaded webtoon images" 로 부른다.
            refs = f"@Image{panels[0]}" if len(panels) == 1 else f"@Image{panels[0]}\u2013@Image{panels[-1]}"
            rows.append((panels[0], VIBE_PANEL_LEAD.format(refs=refs, be="is" if len(panels) == 1 else "are",
                                                           images="image" if len(panels) == 1 else "images")))
        if not rows:
            return ""
        text = "; ".join(t for _n, t in sorted(rows, key=lambda r: r[0])) + "."
        if sheets:
            text += " " + ", ".join(f"@Image{n}" for n in sorted(sheets)) + f": {VIBE_SHEET_CLAUSE}."
        if costume_sheets:
            text += " " + " ".join(costume_sheets)
        return text

    colors = ", ".join(f"{m.get('colorName') or 'grey'} = {_disp(str(m.get('name') or ''))}"
                       for m in cast if str(m.get("name") or "").strip())

    def _assemble() -> str:
        head = [REAL_REF_AI_NOTE, _style(), *[_look_line(m) for m in cast], _paragraph(), _setting()]
        video = VIBE_VIDEO_LINE + (f" ({colors})" if colors else "") + "."
        return "\n".join(b for b in [*head, *shots, _labels(), video] if b)

    prompt = _assemble()
    applied: list[str] = []
    for section in VIBE_COMPACT_ORDER:
        if _prompt_len(prompt) <= budget:
            break
        state[section] = True
        applied.append(section + "Compact")
        prompt = _assemble()
    fits = _prompt_len(prompt) <= budget
    if not fits:
        applied.append("overBudget")  # 샷 문장은 줄이지 않는다 — 제출 쪽(_prompt_split_parts)이 조각으로 나눈다

    lines_conti = lines_prompt = actions_prompt = 0
    actions: list[str] = []
    for entry in entries:
        off_speaker = str(entry.get("offScreenSpeaker") or "").strip()
        for ln in _entry_lines(entry):
            lines_conti += 1
            piece = _line_piece(ln, off_speaker)
            if piece and piece in prompt:
                lines_prompt += 1
        action = _action(entry)
        actions.append(action)
        if action and action in prompt:
            actions_prompt += 1
    if audit is not None:
        audit.update(cuts=len(entries), linesInConti=lines_conti, linesInPrompt=lines_prompt,
                     actionsInPrompt=actions_prompt, compact=[s[:-len("Compact")] for s in applied if s.endswith("Compact")],
                     chars=_prompt_len(prompt), budget=budget, fits=fits, actions=actions, builder="vibe")
    if quiet:
        return prompt
    if not fits:
        log.warning("vnew.gen.take_prompt_over_budget take=%s chars=%d budget=%d", str(take.get("id") or ""),
                    _prompt_len(prompt), budget)
    log.info("vnew.gen.take_prompt builder=vibe%s bytes=%d steps=%s", " variant=real" if real else "",
             _utf8_len(prompt), ",".join(applied) or "none")
    return prompt


def _vibe_anime_cast(actors: list[dict], char_by_id: dict, resolved: dict, cap: int) -> tuple[list[bytes], list[dict]]:
    """작업 159 애니 신원 참고: 인물마다 [웹툰 시트, 웹툰 정면](읽히는 것만, 상한은 _ref_counts — 정면부터 빠진다)."""
    rows = []
    loaded: dict[str, bytes | None] = {}
    for actor in actors:
        char_id = str(actor.get("charId") or "")
        char = char_by_id.get(char_id) if char_id else None
        keys: list[str] = []
        for field in ("webtoonSheetKey", "webtoonFrontKey"):
            key = str((char or {}).get(field) or "")
            if key and key not in keys:
                if key not in loaded:
                    loaded[key] = _load_jpeg(key)
                if loaded[key] is not None:
                    keys.append(key)
        rows.append((actor, char, keys))
    counts = _ref_counts([len(r[2]) for r in rows], cap)
    images: list[bytes] = []
    cast: list[dict] = []
    for (actor, char, keys), count in zip(rows, counts):
        name = actor.get("name") or "a character"
        nos: list[int] = []
        sheet_no = None
        for key in keys[:count]:
            images.append(_fit_ref(loaded[key]))
            nos.append(len(images))
            if char and key == str(char.get("webtoonSheetKey") or ""):
                sheet_no = len(images)
        member = resolved.get(str(name)) or {}
        appearance = str((char or {}).get("appearanceEn") or "") or str(actor.get("appearanceEn") or "")
        cast.append({
            "name": name,
            "colorName": actor.get("colorName") or "grey",
            "frontNo": next((n for n in nos if n != sheet_no), None) or (nos[0] if nos else None),
            "refNos": nos,
            "look": "webtoon",
            "appearance": _short_appearance(appearance, 170),
            "costume": str(member.get("costume") or ""),
            "voice": str(member.get("voice") or ""),
        })
        if sheet_no:
            cast[-1]["sheetNo"] = sheet_no
    return images, cast


def _vibe_take_payload(owner: str, pid: str, plan: dict, take: dict, caps: dict, *, variant: str, fallback_step: int,
                       soften: bool, continuity: dict | None, no_text: bool) -> dict:
    """작업 159: _take_payload 의 video-vibecode 판(VNEW_VIBE_PROMPT 켬). 반환 모양은 _take_payload 와 같다.

    참고 이미지(순서 고정): 인물별 신원 참고(실사 = _real_take_cast — 142 시트+정면, 130 1단계 시트만; 애니 = 웹툰 시트+정면) →
    (155 조각·대체) 앞 샷 마지막 프레임 → 143 장소 뷰 → (fix1, 애니만) 원작 패널 최대 VNEW_TAKE_MAX_PANELS 장(옛 _take_prompt 와
    같은 패널·순서). 상한에 걸리면 패널부터 빠진다(신원 참고·장소는 빼지 않는다). 실사는 패널이 없다.
    장소·프레임 자리를 먼저 남기고, 대체(origMax)는 원래 요청보다 신원 참고를 적게 넣지 않는다(155 규칙 그대로).
    """
    real = variant == "real"
    drop = _drop_refs(soften)  # 작업 168 4단계: 앞 조각 마지막 프레임·장면 옷 편집 그림을 넣지 않는다(이음 글은 남긴다)
    max_images = int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX)
    actors = _segment_actors(plan, take)
    char_by_id = _char_by_id(owner, plan)
    _read_location_cast(owner, pid, plan, take)  # 작업 160: 같은 장소 모든 테이크의 패널 읽기 먼저(캐시면 호출 0)
    resolved = _resolve_cast(owner, pid, plan, take, read=True)
    anchor = continuity if isinstance(continuity, dict) else None
    loc_take = (anchor.get("take") or take) if anchor is not None else take
    loc_jpeg = _take_location_jpeg(plan, loc_take, variant="real" if real else "webtoon")
    frame = anchor.get("frame") if anchor is not None and not drop else None
    frame_jpeg = bytes(frame) if isinstance(frame, (bytes, bytearray)) and frame else None
    if frame_jpeg is not None:
        _ref_note_load(str(anchor.get("frameKey") or ""), frame_jpeg)
    n_loc, n_frame = int(loc_jpeg is not None), int(frame_jpeg is not None)
    person_cap = max(0, max_images - n_loc - n_frame)
    if anchor is not None and int(anchor.get("origMax") or 0):
        orig_cap = int(anchor["origMax"]) - n_loc - (n_frame if anchor.get("origFrame") else 0)
        person_cap = max(0, min(orig_cap, max_images - n_loc))
    if real:
        # 작업 168 4단계: 인물당 신원 참고 1장(시트, 없으면 정면 = 130 1단계 키)·장면 옷 편집 끔.
        image_urls, cast = _real_take_cast(owner, plan, actors, char_by_id, resolved, person_cap,
                                           fallback_step=1 if drop else fallback_step,
                                           costume_location=None if drop else _take_location(plan, take), pid=pid)
    else:
        image_urls, cast = _vibe_anime_cast(actors, char_by_id, resolved, person_cap)
    frame_no = None
    if frame_jpeg is not None and len(image_urls) < max_images - n_loc:
        image_urls.append(_fit_ref(frame_jpeg))
        frame_no = len(image_urls)
    location_no = None
    if loc_jpeg is not None and len(image_urls) < max_images:
        image_urls.append(_fit_ref(loc_jpeg))
        location_no = len(image_urls)
    panel_nos: list[int] = []
    if not real:
        # fix1: 애니는 옛 _take_prompt 와 같은 원작 패널(같은 패널·순서)을 신원 참고·장소 뒤에 남는 자리만큼(패널이 먼저 빠진다).
        for panel in _segment_panel_jpegs(owner, pid, plan, take, int(vnew_settings.VNEW_TAKE_MAX_PANELS or 3)):
            if len(image_urls) >= max_images:
                break
            image_urls.append(_fit_ref(panel))
            panel_nos.append(len(image_urls))
    prompt_plan = _soften_plan(owner, pid, plan, take, _strong(soften), _rewrite_on(soften)) if soften else plan
    kwargs = {"real": real, "cast": cast, "location_no": location_no, "frame_no": frame_no, "panel_nos": panel_nos,
              "caps": caps, "soften": soften, "anchor": anchor is not None, "no_text": no_text,
              "bases": _base_outfits(actors, char_by_id)}
    lines_note: dict = {}
    if _rewrite_on(soften):
        # 작업 168 5단계: 그 조각 대사 LLM 1번(실패 → 대사 빼기). 기록은 제출 쪽이 조각·칸에 남긴다.
        lines_fix, lines_note = _rewrite_part_lines(owner, pid, prompt_plan, take)
        if lines_fix:
            kwargs["lines_fix"] = lines_fix
    audit: dict = {}
    prompt = _vibe_take_prompt(prompt_plan, take, audit=audit, **kwargs)

    def probe(sub_take: dict) -> dict:
        result: dict = {}
        _vibe_take_prompt(prompt_plan, sub_take, audit=result, quiet=True, **kwargs)
        return result

    out = {"imageUrls": image_urls[:max_images], "prompt": prompt, "cast": cast, "panelNos": panel_nos,
           "locationNo": location_no,
           "audit": audit, "probe": probe}
    out.update(lines_note)
    if anchor is not None:
        out["frameNo"] = frame_no
    return out


def _real_look(owner: str, char_id: str) -> str:
    """실사 외모 문장(casting.lookEn, 없으면 자동 문장 look_sentence). 캐릭터를 못 읽으면 ""(제출을 막지 않는다)."""
    if not char_id:
        return ""
    try:
        char, _ = characters.load_char(owner, char_id)
        return vnew_casting.current_look(char) if vnew_settings.VNEW_CASTING else vnew_casting.auto_look(char)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_look_unreadable char=%s err=%s", char_id, type(exc).__name__)
        return ""


def _real_appearance(look: str, limit: int = 170) -> str:
    """실사 외모 문장 → ASSETS 외형(얼굴·머리, ≤limit자). 그림 낱말을 지우고 옷 조각을 뺀 뒤 조각 단위로 줄인다.

    look_sentence 는 옷까지 한 문장이라 _short_appearance(문장 단위)를 그대로 쓰면 늘 빈 값이 된다. 조각마다
    _short_appearance 의 옷 판정을 빌린다(옷 낱말이 있으면 그 조각의 요약이 빈 값). 옷은 COSTUME 이 맡는다.
    2026-10-30 피드백: 170자에 맞출 때 캐스팅과 같은 조각 규칙(vnew_casting.fit_appearance — 순위·잇기)을 쓴다. 머리 조각을
    끝까지 남기고 덜 중요한 조각부터 뺀다(전에는 단어 경계에서 잘라 "… over his." 처럼 반쪽 조각이 남았다).
    """
    return vnew_casting.fit_appearance(vnew_fx.strip_drawing_words(look), limit,
                                       is_outfit=lambda piece: not _short_appearance(piece, 1000))


def _take_payload(owner: str, pid: str, plan: dict, take: dict, caps: dict, variant: str = "webtoon",
                  fallback_step: int = 0, soften: bool = False, continuity: dict | None = None,
                  no_text: bool = False) -> dict:
    """통 테이크(웹툰) 참조 이미지 목록·프롬프트(설계자 2026-10-26, 064).

    image_urls(순서 고정 — 프롬프트 번호와 일치):
      ① 테이크에 등장하는 인물별(등장 많은 순) 웹툰 기본복장 **정면 1장만**(후면 제외 — 다른 의상
         증거를 줄여 장면 의상이 먹히게).
      ② 원작 그림체 패널 최대 VNEW_TAKE_MAX_PANELS 장.
      ③ 장소 시트(webtoon back 뷰) — 있으면 1장.
    전부 061 축소 규칙(긴 변 1024, JPEG q85). 총 ≤ caps.imageListMax(기본 10).

    vnew-live-fx C.5(variant="real"): ① 인물 사진(작업 126: _real_take_cast — 고른 캐스팅 사진 → 실사 정면 → 실사 시트 →
    실사 후면, 상한 안에서 뒤 사진부터 뺌, 회차 인물 바꾸기 반영) → 원작 패널
    없음 → ③ 실사 장소 뷰(locations real viewKeys.back). 외형은 실사 외모 문장(_real_look → _real_appearance).
    이미지는 모두 _fit_ref 를 지나고 제출 때 data URI 가 된다(_submit_take). 웹툰(기본)은 지금 그대로다.

    장면 의상·목소리는 _resolve_cast(원작 패널 읽기 Claude 1회·캐시, override 우선)로 인물별 costume·
    voice 를 구해 cast 에 담는다(ASSETS 의 COSTUME/Voice 문구가 쓴다).
    fallback_step(작업 130): 실사 likeness 1단계(인물당 정면 1장)면 1 을 _real_take_cast 에 넘긴다. 0 이면 지금 그대로.
    continuity(작업 155, 152 조각·153 Seedance 대체 제출만): {take: 통 테이크, frame: 앞 샷 마지막 프레임|None, frameKey,
    origMax: 대체 전 모델 imageListMax(대체일 때), origFrame: 대체 전 요청에도 프레임 자리가 있었나}. 있으면 장소 = 통 테이크의
    장소 뷰(모든 조각 같은 1장, 늘 맨 끝), 그 앞에 프레임, ASSETS 에 장소 고정·프레임 줄·인물별 의상 고정 구절. 상한에 걸리면
    인물 둘째 사진부터 뺀다(장소는 빼지 않는다). 대체는 신원 참고를 원래 요청보다 적게 넣지 않는다(자리가 모자라면 프레임을 뺀다).
    None 이면 지금과 바이트 단위로 같다.
    반환: {imageUrls:[bytes], prompt, cast, panelNos, locationNo, audit, probe}(continuity 가 있으면 frameNo 도).
    작업 157: audit = _take_prompt 의 프롬프트 감사(대사 줄·연기·짧은 형·fits), probe(sub_take) = 같은 인물·참고 번호로 다른 컷
    묶음의 프롬프트를 만들어 본 audit(로그 없음, _prompt_split_parts 가 쓴다).
    작업 159: VNEW_VIBE_PROMPT 켬이면 _vibe_take_payload(video-vibecode 형식 프롬프트·시트+정면 → 장소). 끄면 아래 그대로.
    """
    if vnew_settings.VNEW_VIBE_PROMPT:
        return _vibe_take_payload(owner, pid, plan, take, caps, variant=variant, fallback_step=fallback_step,
                                  soften=soften, continuity=continuity, no_text=no_text)
    real = variant == "real"
    max_images = int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX)
    actors = _segment_actors(plan, take)
    char_by_id = _char_by_id(owner, plan)
    # 장면 의상·목소리(override 우선 + 원작 패널 읽기). 생성 경로라 read=True(캐시 없으면 1회 읽음).
    _read_location_cast(owner, pid, plan, take)  # 작업 160: 같은 장소 모든 테이크의 패널 읽기 먼저(캐시면 호출 0)
    resolved = _resolve_cast(owner, pid, plan, take, read=True)

    anchor = continuity if isinstance(continuity, dict) else None
    drop = _drop_refs(soften)  # 작업 168 4단계: 앞 조각 마지막 프레임을 넣지 않는다(실사 인물은 1장씩)
    person_limit = panel_limit = max_images
    anchor_loc = anchor_frame = None
    if anchor is not None:
        # 작업 155: 장소는 조각이 아니라 통 테이크 기준(조각마다 첫 컷 장소가 달라도 같은 1장). 장소·프레임 자리를 먼저 남긴다.
        anchor_loc = _take_location_jpeg(plan, anchor.get("take") or take, variant="real" if real else "webtoon")
        frame = None if drop else anchor.get("frame")
        anchor_frame = bytes(frame) if isinstance(frame, (bytes, bytearray)) and frame else None
        if anchor_frame is not None:
            _ref_note_load(str(anchor.get("frameKey") or ""), anchor_frame)
        n_loc, n_frame = int(anchor_loc is not None), int(anchor_frame is not None)
        person_limit = panel_limit = max(0, max_images - n_loc - n_frame)
        orig_max = int(anchor.get("origMax") or 0)
        if orig_max:
            # 대체: 신원 참고는 원래 요청의 인물 자리만큼(이 모델 상한 안에서). 프레임은 남는 자리가 있을 때만.
            orig_cap = orig_max - n_loc - (n_frame if anchor.get("origFrame") else 0)
            person_limit = max(0, min(orig_cap, max_images - n_loc))

    image_urls: list[bytes] = []
    cast: list[dict] = []
    real_loc = None
    if real:
        # 작업 126: 인물 참고 = 3D 모델용 실사 사진(고른 캐스팅 → 정면 → 시트 → 후면, 회차 인물 바꾸기 반영). 장소 1장 자리를
        # 먼저 남기고(있을 때) 그 안에서 인물마다 첫 장부터 돌아가며 채운다. 외형은 실사 외모 문장의 얼굴·머리 조각.
        real_loc = anchor_loc if anchor is not None else _take_location_jpeg(plan, take, variant="real")
        person_cap = person_limit if anchor is not None else max(0, max_images - (1 if real_loc is not None else 0))
        image_urls, cast = _real_take_cast(owner, plan, actors, char_by_id, resolved, person_cap,
                                           fallback_step=1 if drop else fallback_step)
    for actor in [] if real else actors:
        if len(image_urls) >= person_limit and anchor is None:
            break  # 작업 155: 조각·대체 제출은 자리가 없어도 인물 문장(의상 고정)을 남긴다(사진만 뺀다)
        name = actor.get("name") or "a character"
        color_name = actor.get("colorName") or "grey"
        char_id = actor.get("charId") or ""
        char = char_by_id.get(char_id) if char_id else None
        front_no = None
        appearance = actor.get("appearanceEn") or ""
        if char:
            # 정면 1장만 — 후면은 넣지 않는다(설계자 2026-10-26, 064).
            front = _load_jpeg(str(char.get("webtoonFrontKey") or ""))
            if front is not None and len(image_urls) < person_limit:
                image_urls.append(_fit_ref(front))
                front_no = len(image_urls)
                # 외형은 appearanceEn(얼굴·머리). 옷은 COSTUME 이 담당하므로 outfitEn 은 섞지 않는다.
                appearance = str(char.get("appearanceEn") or "") or appearance
        member = resolved.get(str(name)) or {}
        cast.append({
            "name": name,
            "colorName": color_name,
            "frontNo": front_no,
            # 외형은 참조 정면 + 요약(얼굴·머리만, 옷 문구 제거).
            "appearance": _short_appearance(appearance, 170),
            "costume": str(member.get("costume") or ""),
            "voice": str(member.get("voice") or ""),
        })

    panel_nos: list[int] = []
    # 실사는 원작 웹툰 패널을 넣지 않는다(RC2.2).
    panels = [] if real else _segment_panel_jpegs(owner, pid, plan, take, int(vnew_settings.VNEW_TAKE_MAX_PANELS or 3))
    for panel in panels:
        if len(image_urls) >= panel_limit:
            break
        image_urls.append(_fit_ref(panel))
        panel_nos.append(len(image_urls))

    frame_no = None
    if anchor_frame is not None and len(image_urls) < max_images - int(anchor_loc is not None):
        # 작업 155: 앞 샷 마지막 프레임은 장소 바로 앞(장소가 늘 맨 끝).
        image_urls.append(_fit_ref(anchor_frame))
        frame_no = len(image_urls)

    location_no = None
    loc_jpeg = anchor_loc if anchor is not None else real_loc if real else _take_location_jpeg(plan, take)
    if loc_jpeg is not None and len(image_urls) < max_images:
        image_urls.append(_fit_ref(loc_jpeg))
        location_no = len(image_urls)

    # 웹툰은 지금과 같은 호출(variant 인자 없음), 실사만 variant="real".
    prompt_kwargs = {"variant": "real"} if real else {}
    bases = _base_outfits(actors, char_by_id)
    if bases:
        prompt_kwargs["bases"] = bases  # 작업 164: 캐릭터 기본 옷(콘티·대본 다음 후보 · 검사 실패 대체)
    if anchor is not None:
        prompt_kwargs["anchor"] = {"frameNo": frame_no}  # 작업 155: 장소 고정·프레임 줄·의상 고정
    prompt_plan = plan
    lines_note: dict = {}
    if soften:
        # 작업 153: 콘텐츠 검사 거절 뒤 다시 만들기 — 테이크 컷 글(대사·연기)을 순화한 계획 사본으로 프롬프트를 다시 만든다.
        prompt_plan = _soften_plan(owner, pid, plan, take, _strong(soften), _rewrite_on(soften))
        prompt_kwargs["soften"] = SOFTEN_STRONG if _strong(soften) else True  # 작업 163: 3단계면 더 센 순화
        if _rewrite_on(soften):
            lines_fix, lines_note = _rewrite_part_lines(owner, pid, prompt_plan, take)  # 작업 168 5단계
            if lines_fix:
                prompt_kwargs["lines_fix"] = lines_fix
    if no_text:
        prompt_kwargs["no_text"] = True  # 작업 156: 화면 글자 검사로 다시 만들 때만(아니면 지금과 같은 호출)
    audit: dict = {}
    prompt = _take_prompt(prompt_plan, take, cast=cast, panel_nos=panel_nos, location_no=location_no, caps=caps,
                          audit=audit, **prompt_kwargs)
    if len(image_urls) > max_images:
        image_urls = image_urls[:max_images]

    def probe(sub_take: dict) -> dict:
        # 작업 157: 같은 인물·참고 번호로 다른 컷 묶음(조각 후보)의 프롬프트를 만들어 본다(로그·LLM 없음). audit 를 돌려준다.
        result: dict = {}
        _take_prompt(prompt_plan, sub_take, cast=cast, panel_nos=panel_nos, location_no=location_no, caps=caps,
                     audit=result, quiet=True, **prompt_kwargs)
        return result

    out = {
        "imageUrls": image_urls,
        "prompt": prompt,
        "cast": cast,
        "panelNos": panel_nos,
        "locationNo": location_no,
        "audit": audit,
        "probe": probe,
    }
    out.update(lines_note)
    if anchor is not None:
        out["frameNo"] = frame_no
    return out


def _take_duration_value(caps: dict, duration_int: int):
    """duration 정수를 caps.durations(enum) 에 맞춘다. enum 이 있으면 ≤ 값 중 가장 큰 것, 없으면 그대로."""
    durations = caps.get("durations") or []
    allowed = []
    for d in durations:
        try:
            allowed.append(int(d))
        except (TypeError, ValueError):
            continue
    if not allowed:
        return duration_int
    allowed.sort()
    chosen = allowed[0]
    for value in allowed:
        if value <= duration_int:
            chosen = value
    return chosen


def _submit_take(*, endpoint: str, caps: dict, video_mp4: bytes, prompt: str, output_prefix: str,
                 image_urls: list[bytes], seconds: float, resolution: str, audio: bool | None = None,
                 seed: int | None = None) -> dict:
    """통 테이크 reference-to-video 제출(fal 큐, 설계자 2026-10-26). 참조 영상·이미지는 data URI.

    caps 의 필드 이름으로 본문을 만든다: videoListField(배열), imageListField(배열), taskField,
    draftField, generateAudioField, durationAsString. seed 는 보내지 않는다(작업 169: likeness 0b 다시 내기만 seed 를
    caps.seedField 가 있을 때 보낸다). caps 에 없는 필드는
    보내지 않는다. 반환 모양은 _submit_previz_edit 과 같다(poll/_find_output_key 재사용).
    audio(vnew-live-fx C.6): None 이면 VNEW_TAKE_AUDIO, bool 이면 그 값(칸 state.audio — "소리 없이 다시 만들기").
    """
    endpoint = str(endpoint or "").strip()
    if not vpoc_settings.FAL_KEY:
        raise VpocError("vnew.generate", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    if not endpoint:
        raise VpocError("vnew.generate", "BadEndpoint", "통 테이크 모델 endpoint 가 비어 있습니다.", 400)
    if not prompt.strip():
        raise VpocError("vnew.generate", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)
    if not video_mp4:
        raise VpocError("vnew.generate", "EmptyVideo", "참조 영상이 비어 있습니다.", 400)
    _assert_ref_video(endpoint, video_mp4, seconds)  # 작업 152: 참고 영상 상한을 넘으면 유료 호출 전에 막는다

    def _img_uri(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

    video_list_field = str(caps.get("videoListField") or "video_urls")
    image_list_field = str(caps.get("imageListField") or "image_urls")
    task_field = str(caps.get("taskField") or "")
    draft_field = str(caps.get("draftField") or "")
    audio_field = str(caps.get("generateAudioField") or "")
    duration_as_string = bool(caps.get("durationAsString"))

    body: dict = {
        "prompt": prompt,
        video_list_field: [vpoc_fal._video_data_uri(video_mp4)],
    }
    if image_urls and image_list_field:
        body[image_list_field] = [_img_uri(bytes(j)) for j in image_urls if isinstance(j, (bytes, bytearray))]
    if task_field:
        body[task_field] = "reference"
    body["aspect_ratio"] = "16:9"
    body["resolution"] = str(resolution)
    duration_int = max(4, min(30, int(round(float(seconds or 4)))))
    duration_value = _take_duration_value(caps, duration_int)
    if duration_value is not None:
        body["duration"] = str(duration_value) if duration_as_string else duration_value
    if audio_field:
        body[audio_field] = bool(vnew_settings.VNEW_TAKE_AUDIO if audio is None else audio)
    if draft_field:
        body[draft_field] = bool(vnew_settings.VNEW_TAKE_DRAFT)
    if seed is not None and caps.get("seedField"):
        body[str(caps["seedField"])] = int(seed)  # 작업 169: likeness 0b 다시 내기(그 밖은 보내지 않는다)

    url = f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}"
    result = _previz_request("POST", url, body)
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.generate", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)
    draft_id = str(result.get("draft_id") or result.get("draftId") or "")
    return {
        "provider": vpoc_settings.FAL_ID_PREFIX + endpoint,
        "invocationArn": f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}",
        "clientRequestToken": "fal-take-" + request_id,
        "outputPrefix": output_prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
        "draftId": draft_id,
    }


def _charge_take(owner: str, pid: str, gen_id: str, result: dict, seconds: float, resolution: str) -> None:
    """통 테이크 제출 1회 계상(토큰 공식). 키 = 작업 토큰(중복 방지)."""
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    provider = str(result.get("provider") or "")
    endpoint = provider.split(":", 1)[-1] if provider else str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    usd = _take_usd(endpoint, seconds, resolution)
    try:
        vpoc_ledger.charge(
            token,
            kind="video",
            model=endpoint or vnew_settings.VNEW_PREVIZ_EDIT_MODEL,
            units=float(seconds or 0),
            usd=usd,
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_take_failed pid=%s err=%s", pid, str(exc)[:200])


# ---------------------------------------------------------------- 작업 152: 참고 영상 상한 → 테이크 조각(part)
#
# fal 영상 입력 모델은 출력 길이(caps.durations, Wan 3.0 r2v 30초)와 따로 입력(참고) 영상 상한(maxRefVideoS, 기본 15초)이 있다.
# 테이크 녹화가 그보다 길면 컷 경계에서 상한 이하 조각으로 나눠(_take_parts) 조각마다 녹화를 잘라(_cut_clip) 그 컷들만으로 만든
# 프롬프트와 함께 제출하고, 원장도 조각마다 1번 남긴다. 조각 기록은 칸 state.parts. 모두 끝나면 134 규칙(_conform_concat)으로
# 이어 붙여 칸 결과(outputKey) 하나로 둔다(길이 = 조각 합 = 테이크 녹화 길이라 ⑦ 구간 시각이 그대로 맞는다).
# Seedance 계열(상한 0)·컷 단위 Kling 경로는 바뀌지 않는다. 제출 전 검사(_assert_ref_video)는 상한을 넘는 참고 영상을 보내지 않는다.

TAKE_PART_TOL_S = 0.05
REF_TOO_LONG_TEXT = ("참고 영상이 {seconds:.1f}초라 이 영상 모델이 받는 최대 길이({limit:g}초)를 넘어 제출하지 않았습니다. "
                     "⑦ 에서 {limit:g}초 이하 구간을 골라 다시 만들거나 콘티를 더 짧게 나눠 주세요.")
REF_SPLIT_FAIL_TEXT = ("테이크 녹화({seconds:.1f}초)가 이 영상 모델의 참고 영상 최대 길이({limit:g}초)를 넘는데 컷 경계를 찾지 못해 "
                       "나눌 수 없습니다. 콘티를 다시 나눠 녹화해 주세요.")


def _take_ref_limit(endpoint: str) -> float:
    """그 테이크 모델의 참고(입력) 영상 상한(초, vpoc ark.video_options maxRefVideoS). Seedance 계열·영상 입력 없음·모름은 0(검사 안 함)."""
    endpoint = str(endpoint or "").strip()
    model = vpoc_settings.FAL_ID_PREFIX + endpoint
    if not endpoint or vpoc_settings.is_fal_seedance_family(model):
        return 0.0
    try:
        options = vpoc_ark.video_options(model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.ref_limit_failed endpoint=%s err=%s", endpoint, type(exc).__name__)
        return 0.0
    if not options.get("videoInput"):
        return 0.0
    return max(0.0, float(options.get("maxRefVideoS") or 0))


def _mp4_seconds(mp4: bytes) -> float:
    """mp4 바이트의 길이(초). 못 재면 0(stitcher.probe_duration 규칙)."""
    if not mp4:
        return 0.0
    try:
        with tempfile.TemporaryDirectory(prefix="vnew-refprobe-") as workdir:
            path = os.path.join(workdir, "ref.mp4")
            with open(path, "wb") as handle:
                handle.write(mp4)
            return float(stitcher.probe_duration(path) or 0.0)
    except Exception:  # noqa: BLE001 — 못 재면 호출부가 넘긴 길이를 쓴다.
        return 0.0


def _assert_ref_video(endpoint: str, video_mp4: bytes, seconds: float) -> None:
    """제출 전 검사(작업 152): 참고 영상(잰 길이, 못 재면 seconds)이 상한을 넘으면 유료 호출 전에 한국어 오류."""
    limit = _take_ref_limit(endpoint)
    if limit <= 0:
        return
    measured = _mp4_seconds(video_mp4) or float(seconds or 0)
    if measured > limit + TAKE_PART_TOL_S:
        raise VpocError("vnew.generate", "RefVideoTooLong", REF_TOO_LONG_TEXT.format(seconds=measured, limit=limit), 409)


def _take_parts(plan: dict, take: dict, limit: float) -> list[dict]:
    """테이크를 참고 영상 상한 이하 조각으로 나눈다(작업 152). 나눌 필요가 없으면 [].

    컷(_take_cut_units, 테이크 시각 = _take_rec_offsets + 녹화 안 시각)을 앞에서부터 담고(탐욕), 넘치면 닫는다. 닫는 자리가 이어지는
    말(작업 147 _dialogue_runs 의 cont·still) 가운데면 그 조각 안의 말 사이 경계로 당긴다(남은 컷 + 다음 컷이 상한 안일 때). 한 컷이
    상한보다 길면 상한에서 자른다. 조각: {no, cutIds, start, end, seconds}(테이크 시각, 초). 컷을 못 찾으면 한국어 오류(유료 호출 전).
    """
    total = float(take.get("seconds") or 0)
    if limit <= 0 or total <= limit + TAKE_PART_TOL_S:
        return []
    units = _take_cut_units(plan, take)
    if not units:
        raise VpocError("vnew.generate", "RefSplitFailed", REF_SPLIT_FAIL_TEXT.format(seconds=total, limit=limit), 409)
    offsets = _take_rec_offsets(plan, take)
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    runs = _dialogue_runs([cuts_by_id.get(str(u.get("cutId") or "")) or {} for u in units], plan)
    pieces: list[dict] = []
    for unit, lines in zip(units, runs):
        base = float(offsets.get(str(unit.get("recSegId") or ""), 0.0))
        start = base + float(unit.get("startSec") or 0)
        end = base + float(unit.get("endSec") or 0)
        soft = not any(ln.get("run") in ("cont", "still") for ln in lines)  # 이 컷 앞이 말 사이 경계인가
        clock = start
        while end - clock > limit + TAKE_PART_TOL_S:
            pieces.append({"cutId": str(unit.get("cutId") or ""), "start": clock, "end": clock + limit, "soft": soft})
            clock += limit
            soft = False  # 한 컷을 나눈 자리는 늘 말 가운데로 본다
        if end - clock > 0.001 or not pieces or pieces[-1]["cutId"] != str(unit.get("cutId") or ""):
            pieces.append({"cutId": str(unit.get("cutId") or ""), "start": clock, "end": max(end, clock), "soft": soft})
    groups: list[list[dict]] = []
    current: list[dict] = []
    for piece in pieces:
        while current and piece["end"] - current[0]["start"] > limit + TAKE_PART_TOL_S:
            cut_at = len(current)
            if not piece["soft"]:
                soft_at = [i for i in range(1, len(current)) if current[i]["soft"]]
                if soft_at and piece["end"] - current[soft_at[-1]]["start"] <= limit + TAKE_PART_TOL_S:
                    cut_at = soft_at[-1]
            groups.append(current[:cut_at])
            current = current[cut_at:]
        current.append(piece)
    if current:
        groups.append(current)
    parts = []
    for number, group in enumerate(groups, start=1):
        cut_ids: list[str] = []
        for piece in group:
            if piece["cutId"] not in cut_ids:
                cut_ids.append(piece["cutId"])
        start, end = round(group[0]["start"], 3), round(group[-1]["end"], 3)
        parts.append({"no": number, "cutIds": cut_ids, "start": start, "end": end, "seconds": round(end - start, 3)})
    if any(p["seconds"] > limit + TAKE_PART_TOL_S or p["seconds"] <= 0 for p in parts):
        raise VpocError("vnew.generate", "RefSplitFailed", REF_SPLIT_FAIL_TEXT.format(seconds=total, limit=limit), 409)
    return parts


def _take_parts_for(plan: dict, take: dict, endpoint: str, *, real: bool = False, anime: str = "") -> list[dict]:
    """그 모델로 제출할 때의 조각(작업 152). 나누기가 꺼져 있거나 상한이 없으면 [](그때는 _assert_ref_video 가 막는다).

    작업 156: 실사가 애니(anime)와 다른 모델이면 그 모델의 참고 영상 상한으로 나눈다(_take_split_limit — Seedance 도 상한이 있다).
    """
    if not vnew_settings.VNEW_TAKE_REF_SPLIT:
        return []
    return _take_parts(plan, take, _take_split_limit(endpoint, real, anime))


def _part_take(plan: dict, take: dict, part: dict) -> dict:
    """조각 하나의 테이크 사본(프롬프트·참고 이미지용): 그 조각의 컷만, 길이 = 조각 길이."""
    no_by_id = {str(c.get("id")): c.get("no") for c in (plan.get("conti") or {}).get("cuts") or []}
    cut_ids = [str(c) for c in part.get("cutIds") or []]
    nos = [no_by_id.get(c) for c in cut_ids if no_by_id.get(c) is not None]
    return {**take, "cutIds": cut_ids, "seconds": float(part.get("seconds") or 0),
            "startCutNo": nos[0] if nos else take.get("startCutNo"), "endCutNo": nos[-1] if nos else take.get("endCutNo")}


def _part_sig(part: dict, step: int) -> list:
    """조각이 같은지 가르는 값(재시도 때 끝난 조각을 다시 내지 않는다)."""
    return [list(part.get("cutIds") or []), round(float(part.get("start") or 0), 3), round(float(part.get("end") or 0), 3),
            int(step or 0)]


def _take_cut_spans(plan: dict, take: dict) -> dict[str, dict]:
    """테이크 컷의 테이크 시각 {cutId: {start, end, soft}}(작업 157, _take_parts 와 같은 시각). soft = 그 컷 앞이 말 사이 경계."""
    units = _take_cut_units(plan, take)
    offsets = _take_rec_offsets(plan, take)
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    runs = _dialogue_runs([cuts_by_id.get(str(u.get("cutId") or "")) or {} for u in units], plan)
    spans: dict[str, dict] = {}
    for unit, lines in zip(units, runs):
        cut_id = str(unit.get("cutId") or "")
        base = float(offsets.get(str(unit.get("recSegId") or ""), 0.0))
        if cut_id and cut_id not in spans:
            spans[cut_id] = {"start": base + float(unit.get("startSec") or 0), "end": base + float(unit.get("endSec") or 0),
                             "soft": not any(ln.get("run") in ("cont", "still") for ln in lines)}
    return spans


def _prompt_split_parts(plan: dict, take: dict, parts: list[dict], built: dict | None) -> list[dict]:
    """작업 157: 프롬프트가 예산을 넘는 테이크(parts 가 비면 통 테이크)·조각을 컷 경계에서 더 나눈다. 나눌 것이 없으면 [].

    built = 그 테이크(조각)의 _take_payload 결과(audit·probe). audit.fits 가 거짓일 때만 나눈다: 반으로(컷 경계, 147 이어지는
    말 가운데는 피한다) 나누고, 반쪽마다 같은 인물·참고 번호로 프롬프트를 만들어 보아(probe, 로그 없음) 아직 넘치면 또 나눈다.
    컷 1개만 남은 조각은 그대로 둔다(대사를 빼지 않고 낸다). VNEW_TAKE_REF_SPLIT 끔이면 나누지 않는다.
    반환 조각: {no, cutIds, start, end, seconds}(테이크 시각, 초) — 152 조각과 같은 모양이라 같은 제출·조회·잇기를 탄다.
    """
    audit = (built or {}).get("audit") or {}
    probe = (built or {}).get("probe")
    if audit.get("fits", True) or not callable(probe) or not vnew_settings.VNEW_TAKE_REF_SPLIT:
        return []
    spans = _take_cut_spans(plan, take)
    if parts:
        groups = [dict(p) for p in parts]
    else:
        ids = [str(c) for c in _seg_cut_ids(take)]
        groups = [{"cutIds": ids, "start": 0.0, "end": float(take.get("seconds") or 0)}]

    def _fits(group: dict) -> bool:
        sub = _part_take(plan, take, {"cutIds": group["cutIds"], "seconds": group["end"] - group["start"]})
        return bool(probe(sub).get("fits", True))

    def _split(group: dict, over: bool) -> list[dict]:
        ids = list(group["cutIds"])
        if len(ids) < 2 or (not over and _fits(group)):
            return [group]
        cands = [k for k in range(1, len(ids)) if ids[k] in spans
                 and group["start"] < round(spans[ids[k]]["start"], 3) < group["end"]]
        pool = [k for k in cands if spans[ids[k]]["soft"]] or cands
        if not pool:
            return [group]
        k = min(pool, key=lambda n: (abs(2 * n - len(ids)), n))
        at = round(spans[ids[k]]["start"], 3)
        return (_split({"cutIds": ids[:k], "start": group["start"], "end": at}, False)
                + _split({"cutIds": ids[k:], "start": at, "end": group["end"]}, False))

    out: list[dict] = []
    for group in groups:
        out.extend(_split(group, len(groups) == 1))
    if len(out) <= len(groups):
        return []
    return [{"no": n, "cutIds": list(p["cutIds"]), "start": round(float(p["start"]), 3), "end": round(float(p["end"]), 3),
             "seconds": round(float(p["end"]) - float(p["start"]), 3)} for n, p in enumerate(out, start=1)]


def _log_prompt_audit(pid: str, take_id: str, variant: str, part: str, audit) -> None:
    """작업 157: 제출마다 1줄 vnew.gen.prompt_audit — 컷 수·콘티 대사 줄·프롬프트에 든 대사 줄·연기가 든 컷·짧은 형 고정 부분."""
    audit = audit if isinstance(audit, dict) else {}
    if not audit:
        return
    level = logging.WARNING if int(audit.get("linesInPrompt") or 0) < int(audit.get("linesInConti") or 0) else logging.INFO
    log.log(level, "vnew.gen.prompt_audit pid=%s take=%s variant=%s%s cuts=%d lines_in_conti=%d lines_in_prompt=%d "
            "actions_in_prompt=%d compact=%s chars=%d budget=%d fits=%d", pid, take_id, variant,
            f" part={part}" if part else "", int(audit.get("cuts") or 0), int(audit.get("linesInConti") or 0),
            int(audit.get("linesInPrompt") or 0), int(audit.get("actionsInPrompt") or 0),
            ",".join(audit.get("compact") or []) or "none", int(audit.get("chars") or 0), int(audit.get("budget") or 0),
            1 if audit.get("fits", True) else 0)


def _submit_take_parts(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, *, plan: dict, take: dict,
                       state: dict, parts: list[dict], video_mp4: bytes, endpoint: str, caps: dict, resolution: str,
                       audio, attempt: int, step: int, rec_keys: list[str], run_n) -> list[dict]:
    """조각마다 잘라·프롬프트·제출·원장(작업 152). _PREPARE_SEM 안에서 부른다. 조각 기록 목록을 돌려준다.

    같은 조각(_part_sig)이 앞 시도에서 completed 면 그대로 둔다(재시도 때 다시 내지 않는다). 유료 제출 전마다 하트비트(작업 144).
    중간에 실패하면 이미 낸 조각을 취소 시도하고 오류를 다시 낸다(낸 조각 원장은 이미 남았다).
    작업 155: 모든 조각에 통 테이크 장소·의상 고정(_take_payload continuity). VNEW_PART_CONTINUITY 켬이면 끝나지 않은 첫 조각만 내고
    (앞 조각 결과의 마지막 프레임을 1장 더해) 뒤 조각은 "pending" 으로 둔다 — 조회가 앞 조각이 끝나면 칸을 다시 queued 로 둔다.
    """
    real = variant == "real"
    take_id = str(take.get("id") or index)
    limit = _take_ref_limit(endpoint)
    previous = {_json_key(p.get("sig")): p for p in (state.get("parts") or []) if isinstance(p, dict)}
    records: list[dict] = []
    in_order = bool(vnew_settings.VNEW_PART_CONTINUITY)
    submitted_n = 0
    parts = [dict(p) for p in parts]
    kling_ctx: dict | None = None  # 작업 169: likeness 2단계 조각의 컷 단위 Kling 재료(처음 쓸 때 1번)
    try:
        at = 0
        while at < len(parts):
            part = parts[at]
            at += 1
            if part.get("kind") == "kling":
                # 작업 169: likeness 2단계로 바뀐 조각의 컷 하나(Kling). 끝났으면 그대로, 앞이 아직이면 pending, 아니면 낸다.
                sig = _kling_part_sig(part)
                old = previous.get(_json_key(sig))
                if old and old.get("status") == "completed" and old.get("outputKey"):
                    records.append(dict(old))
                    continue
                if in_order and submitted_n:
                    records.append({**part, "sig": sig, "status": "pending", "invocationArn": "", "outputPrefix": "",
                                    "outputKey": "", "error": ""})
                    continue
                if kling_ctx is None:
                    kling_ctx = _kling_part_ctx(owner, pid, gen_id, index, claim, plan, take)
                used = _used_parts(records)
                records.append(_submit_kling_part_cut(owner, pid, gen_id, plan, kling_ctx, take, part, sig,
                                                      used[-1] if used and in_order else None))
                submitted_n += 1
                log.info("vnew.gen.take_part_submit pid=%s take=%s part=%d/%d start=%.3f end=%.3f cuts=1 variant=real kling=1%s",
                         pid, take_id, int(part.get("no") or 0), len(parts), part["start"], part["end"],
                         " frame=1" if records[-1].get("startImage") == "prevFrame" else "")
                continue
            sig = _part_sig(part, step)
            old = previous.get(_json_key(sig))
            if old and old.get("status") == "completed" and old.get("outputKey"):
                records.append(dict(old))
                continue
            lk_step, lk_retry, _lk_extra = _part_likeness(old) if real else (0, 0, 0)
            if lk_step >= 2:
                # 작업 169: 이 조각은 컷 단위 Kling 으로 바뀌었다 — 조각 기록은 superseded 로 남기고 그 컷들을 바로 뒤에 둔다.
                records.append({**old, "sig": sig, "status": PART_SUPERSEDED, "replacedBy": KLING_CUTS_REPLACED,
                                "invocationArn": "", "outputKey": "", "error": ""})
                records[-1].pop("failureKind", None)
                parts[at:at] = _kling_part_items(plan, take, part)
                continue
            if in_order and submitted_n:
                # 작업 155: 앞 조각이 끝난 뒤에 낸다(그 마지막 프레임을 이어 받는다). 콘텐츠 단계는 그대로 넘긴다.
                waiting = {**part, "sig": sig, "status": "pending", "invocationArn": "", "outputPrefix": "", "outputKey": "",
                           "error": ""}
                for field in ("contentStep", "contentFallback", "qc") + LIKENESS_PART_FIELDS:
                    if old and field in old:
                        waiting[field] = old[field]
                records.append(waiting)
                continue
            sub = _part_take(plan, take, part)
            # 작업 153: 콘텐츠 검사로 거절된 조각은 기록된 단계로(1 = 순화, 2 = 순화 + Seedance). 0 이면 지금 그대로.
            content_step = _content_step(old) if old else 0
            part_endpoint, part_caps, part_limit = endpoint, caps, limit
            if content_step >= 2 and _content_fallback_model(endpoint):
                part_endpoint = _content_fallback_model(endpoint)
                part_caps = _previz_caps(part_endpoint)
                part_limit = _take_ref_limit(part_endpoint)
            # 작업 155: 이음 프레임 — 앞 조각 결과의 마지막 프레임, 첫 조각의 Seedance 대체는 앞 테이크 결과의 마지막 프레임.
            frame, frame_key = None, ""
            used = _used_parts(records)  # 작업 169: Kling 으로 바뀐(superseded) 조각은 이음 프레임·순서에서 뺀다
            before = used[-1] if used else None
            if content_step >= CONTENT_STEP_DROP_REFS:
                pass  # 작업 168 4단계: 앞 조각·앞 테이크 마지막 프레임을 넣지 않는다(이음 글은 continuity 로 남는다)
            elif in_order and before and before.get("status") == "completed" and before.get("outputKey"):
                frame = _video_last_frame(str(before["outputKey"]), float(before.get("seconds") or 0))
                frame_key = str(before["outputKey"]) + "#last"
            elif in_order and not used and part_endpoint != endpoint:
                frame, frame_key = _prev_take_frame(plan, index, variant)
            continuity = {"take": take, "frame": frame, "frameKey": frame_key}
            if part_endpoint != endpoint:
                continuity.update(origMax=int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX),
                                  origFrame=in_order and bool(used))
            no_text = bool(old) and _qc_no_text(old)  # 작업 156: 화면 글자 검사로 다시 만드는 조각
            part_fb = 1 if lk_step == 1 else step  # 작업 169: likeness 1단계 조각 = 인물당 정면 1장(130 1단계)
            part_seed = old.get("seed") if old and lk_step == 0 and lk_retry and old.get("seed") is not None else None
            with _RefTrace() as trace:
                built = _take_payload(owner, pid, plan, sub, part_caps, variant="real", fallback_step=part_fb,
                                      soften=_soften_level(content_step), continuity=continuity, no_text=no_text) if real else _take_payload(
                    owner, pid, plan, sub, part_caps, soften=_soften_level(content_step), continuity=continuity, no_text=no_text)
            del frame
            # 작업 157: 이 조각 프롬프트도 다 줄여서 넘치면(컷 2개 이상) 그 자리에서 더 나눠 다시 본다(대사를 빼지 않는다).
            finer = [] if content_step or lk_step or lk_retry else _prompt_split_parts(plan, take, [part], built)
            if finer:
                del built
                parts[at - 1:at] = finer
                number = 0
                for item in parts:
                    if item.get("kind") != "kling":  # 작업 169: Kling 컷은 원래 조각 번호를 둔다
                        number += 1
                        item["no"] = number
                at -= 1
                continue
            refs = trace.refs(built)
            framed = bool(built.get("frameNo"))
            prompt = str(built["prompt"])
            lines_note = {k: built[k] for k in ("dialogueSoftened", "audioDropped") if k in built}  # 작업 168 5단계
            images_n = len(built.get("imageUrls") or [])
            if real:
                _assert_no_webtoon(prompt)
            clip = _cut_clip(video_mp4, {"startSec": part["start"], "endSec": part["end"]})
            sent = float(clip.get("sentSeconds") or 0) or float(part["seconds"])
            if part_limit > 0 and sent > part_limit + TAKE_PART_TOL_S:
                raise VpocError("vnew.generate", "RefVideoTooLong", REF_TOO_LONG_TEXT.format(seconds=sent, limit=part_limit), 409)
            prefix = _seg_output_prefix(owner, pid, gen_id, f"{take_id}-{variant}-p{part['no']}", attempt)
            if not _touch_prepare_claim(owner, pid, gen_id, index, variant, claim):  # 작업 144: 조각(유료) 제출 전마다
                raise _Discard()
            _log_prompt_audit(pid, take_id, variant, f"{part['no']}/{len(parts)}", built.get("audit"))  # 작업 157
            result = _submit_take(endpoint=part_endpoint, caps=part_caps, video_mp4=clip["bytes"], prompt=prompt,
                                  output_prefix=prefix, image_urls=built["imageUrls"], seconds=float(part["seconds"]),
                                  resolution=resolution, audio=audio, seed=part_seed)
            del clip, built
            submitted_n += 1
            _charge_take(owner, pid, gen_id, result, float(part["seconds"]), resolution)
            params = {**_take_params(part_caps, float(part["seconds"]), resolution, audio), "part": part["no"],
                      "parts": len(parts), "rangeStart": part["start"], "rangeEnd": part["end"]}
            if framed:
                params["continuityFrame"] = True  # 작업 155
            if lk_step or lk_retry:
                params["likenessStep"] = lk_step  # 작업 169
                if part_seed is not None:
                    params["seed"] = part_seed
            _log_attempt(owner, pid, gen_id, source="gen", mode="previz-take", variant=variant, cell_id=take_id, index=index,
                         attempt=attempt, run=run_n, endpoint=part_endpoint, params=params, refs=refs, video_keys=rec_keys,
                         result=result, prompt=prompt)
            records.append({**part, "sig": sig, "status": "submitted", "invocationArn": str(result.get("invocationArn") or ""),
                            "outputPrefix": str(result.get("outputPrefix") or prefix), "outputKey": "", "error": "",
                            "promptEn": prompt, "promptBytes": len(prompt.encode("utf-8")),
                            "clientRequestToken": str(result.get("clientRequestToken") or ""), "sentSeconds": round(sent, 3)})
            if content_step:
                records[-1]["contentStep"] = content_step
                if isinstance(old.get("contentFallback"), dict):
                    records[-1]["contentFallback"] = dict(old["contentFallback"])
                records[-1].update(lines_note)
                log.info("vnew.gen.content_step take=%s part=%s step=%d pid=%s name=%s images=%d%s", take_id, part["no"],
                         content_step, pid, CONTENT_STEP_NAMES.get(content_step, ""), images_n,
                         (f" softened={len(lines_note.get('dialogueSoftened') or [])}" if "dialogueSoftened" in lines_note
                          else " audioDropped=1" if lines_note.get("audioDropped") else ""))
            if old and _qc_pending(old):
                records[-1]["qc"] = dict(old["qc"])  # 작업 156: 첫 결과와 비교할 검사 기록
            if old and (lk_step or lk_retry):
                for field in LIKENESS_PART_FIELDS:  # 작업 169: 조각 likeness 단계 기록
                    if field in old:
                        records[-1][field] = old[field]
            log.info("vnew.gen.take_part_submit pid=%s take=%s part=%d/%d start=%.3f end=%.3f cuts=%d%s%s%s", pid, take_id,
                     part["no"], len(parts), part["start"], part["end"], len(part["cutIds"]), " variant=real" if real else "",
                     f" content={CONTENT_STEP_NAMES.get(content_step, '')}" if content_step else "", " frame=1" if framed else "")
    except BaseException:
        for record in records:
            if record.get("status") == "submitted" and record.get("invocationArn"):
                _cancel_video(record["invocationArn"])
        raise
    return records


def _json_key(value) -> str:
    """목록·dict 를 dict 키로 쓰는 문자열(순서 유지)."""
    return repr(value)


def _do_poll_take_parts(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """조각 칸 조회(작업 152): 제출된 조각을 모두 조회해 저장하고, 하나라도 실패면 칸 실패, 모두 완료면 이어 붙인다."""
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if not _claim_ok_variant(gen, index, variant, claim):
        return
    parts = [dict(p) for p in (_previz_cells(gen)[index][variant].get("parts") or [])]
    updates: dict[int, dict] = {}
    for number, part in enumerate(parts):
        if part.get("status") != "submitted":
            continue
        arn, prefix = str(part.get("invocationArn") or ""), str(part.get("outputPrefix") or "")
        result = vpoc_fal.poll(arn, prefix)
        status = str(result.get("status") or "")
        if part.get("kind") == "kling":
            # 작업 169: likeness 2단계 조각의 Kling 컷 — 결과 검사·콘텐츠 단계 없음(되돌아가지 않는다). 기록 줄은 컷 칸.
            if status == "InProgress":
                continue
            if status == "Completed":
                updates[number] = {"status": "completed", "outputKey": _find_output_key(prefix, arn), "error": ""}
            else:
                updates[number] = {"status": "failed", "error": str(result.get("failureMessage") or "영상 생성 실패")[:300],
                                   "failureKind": str(result.get("failureKind") or "")}
            _log_kling_cut_result(owner, pid, gen_id, str(_previz_cells(gen)[index].get("id") or index),
                                  {**part, **updates[number], "invocationArn": arn})
            continue
        if status == "Completed":
            key = _find_output_key(prefix, arn)
            updates[number] = {"status": "completed", "outputKey": key, "error": ""}
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "completed", output_key=key)
            # 작업 156: 조각 결과 검사(화면 글자·멈춤) → 1번 다시(status qcRetry) 또는 둘 중 고르기.
            verdict = _qc_verdict(owner, pid, gen_id, part, key, float(part.get("seconds") or 0),
                                  f"take={index} part={part.get('no')}")
            if verdict.get("retry"):
                updates[number] = {"status": "qcRetry", "outputKey": "", "invocationArn": "", "error": "",
                                   "qc": verdict["retry"]}
            elif verdict.get("keep"):
                updates[number].update(outputKey=verdict["keep"], qc=verdict["qc"], qcNote=bool(verdict.get("note")))
        elif status != "InProgress" and _qc_pending(part):
            # 작업 156: 검사로 다시 만든 조각이 실패 → 첫 결과를 쓴다.
            kept = _qc_first_kept(part)
            updates[number] = {"status": "completed", "outputKey": kept["keep"], "error": "", "qc": kept["qc"],
                               "qcNote": bool(kept.get("note"))}
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "failed",
                        error=str(result.get("failureMessage") or "영상 생성 실패")[:300])
        elif status != "InProgress":
            error = str(result.get("failureMessage") or "영상 생성 실패")[:300]
            kind = str(result.get("failureKind") or "") or _content_kind(error)  # 작업 153: 콘텐츠 검사 거절도 조각에 적는다
            updates[number] = {"status": "failed", "error": error, "failureKind": kind}
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "failed", error=error)
    if updates:
        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, variant, claim):
                raise _Discard()
            stored = _previz_cells(g)[index][variant].get("parts") or []
            for number, change in updates.items():
                if number < len(stored):
                    stored[number].update(change)

        _commit(owner, pid, mutator)
        for number, change in updates.items():
            parts[number].update(change)
    failed = [p for p in parts if p.get("status") == "failed"]
    if failed:
        error = str(failed[0].get("error") or "")
        if variant == "real" and _likeness_rejected(error):
            # 작업 169: 조각 테이크는 거절된 조각만 조각 사다리로(끝난 조각은 그대로). 아직 도는 조각을 기다린다(이중 제출 없음).
            if not any(p.get("status") == "submitted" for p in parts):
                _escalate_likeness_parts(owner, pid, gen_id, index, claim)
            return
        if all(p.get("failureKind") == CONTENT_FLAG_KIND for p in failed):
            # 작업 153: 콘텐츠 검사 거절만 있으면 아직 도는 조각을 기다렸다가(이중 제출 없음) 거절된 조각만 다음 단계로.
            if not any(p.get("status") == "submitted" for p in parts):
                _escalate_content_parts(owner, pid, gen_id, index, variant, claim)
            return
        message = f"테이크 조각 {failed[0].get('no')}/{len(parts)} 이 실패했습니다: {error}"
        _fail_variant(owner, pid, gen_id, index, variant, claim, message,
                      kind=str(failed[0].get("failureKind") or "") or _audio_kind(error))
        return
    used = _used_parts(parts)  # 작업 169: Kling 컷으로 바뀐 조각(superseded)은 잇지 않는다
    if used and all(p.get("status") == "completed" and p.get("outputKey") for p in used):
        _merge_take_parts(owner, pid, gen_id, index, variant, claim, used)
    elif any(p.get("status") in ("pending", "qcRetry") for p in parts) and not any(p.get("status") == "submitted" for p in parts):
        _queue_next_part(owner, pid, gen_id, index, variant, claim)  # 작업 155: 앞 조각이 끝났다 → 다음 조각을 낸다(156: 검사 다시)


def _merge_take_parts(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, parts: list[dict]) -> None:
    """조각 결과를 순서대로 134 규칙(_conform_concat: 같은 fps·크기·소리)으로 이어 칸 결과로 둔다. 조각 길이로 자른다."""
    plan, _ = plan_mod.load_plan(owner, pid)
    take = _previz_cells(plan.get("gen") or {})[index]
    pieces = [(_fetch_bytes(str(p.get("outputKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET), float(p.get("seconds") or 0))
              for p in parts]
    try:
        merged = _conform_concat(pieces, hold=True)  # 작업 156: 조각마다 계획 길이(긴 것은 자르고 짧은 것은 마지막 프레임)
    except Exception as exc:  # noqa: BLE001 — 맞추지 못하면 예전 방식(길면 자르고 잇기)
        log.warning("vnew.gen.take_part_conform_failed pid=%s take=%s err=%s", pid, take.get("id"), _msg(exc))
        clips = []
        for clip, seconds in pieces:
            try:
                clip = _trim_clip(clip, seconds) if seconds > 0 else clip
            except Exception:  # noqa: BLE001
                pass
            clips.append(clip)
        merged = clips[0] if len(clips) == 1 else _concat(clips)
    del pieces
    attempt = int((take.get(variant) or {}).get("attempt") or 1)
    key = f"{_seg_output_prefix(owner, pid, gen_id, str(take.get('id') or index) + '-' + variant + '-parts', attempt).rstrip('/')}/merged.mp4"
    STORE.put_bytes(key, merged, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)
    del merged

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        s = _previz_cells(g)[index][variant]
        s.update(status="completed", outputKey=key, error="", claim="", claimedAt="")
        _supersede_unused(s, {_json_key(p.get("sig")) for p in parts})  # 작업 169: 쓰지 않은 조각은 superseded
        if any(p.get("qcNote") for p in parts):
            s["note"] = TEXT_LEFT_NOTE  # 작업 156: 다시 만들어도 화면 글자가 남은 조각이 있다

    _commit(owner, pid, mutator)
    log.info("vnew.gen.take_parts_merged pid=%s take=%s parts=%d", pid, take.get("id"), len(parts))


# ---------------------------------------------------------------- 작업 153: fal 콘텐츠 검사 거절 → 순화 → Seedance
#
# fal 이 콘텐츠 검사로 거절하면(실패 문구에 CONTENT_FLAG_WORDS 중 하나) 그 칸(또는 152 조각)의 failureKind = "contentFlag".
# 1단계: 같은 모델에 순화한 프롬프트(soften)로 1번. 2단계: 또 거절이면 순화한 그대로 VNEW_CONTENT_FALLBACK_MODEL(Seedance 테이크
# 경로)로 1번. 3단계: 그래도 거절이면 CONTENT_FLAG_FAIL_TEXT 로 칸 실패. 단계 = 칸(조각) contentStep(0·1·2), 2단계 기록 =
# contentFallback {from, to}. 원장은 실제로 낸 제출마다 제출 경로가 1번 남긴다. 순화하지 않은 프롬프트는 지금과 바이트 단위로 같다.
# 사용자의 다시 시도(retry_segment)는 contentFlag 칸을 1단계부터 다시 한다.
# 작업 163: fal 결과 영상 "sensitive content" 거절(fal.rejection 의 한국어 문구 포함)도 contentFlag(소리 거절은 빼고 audio 그대로).
# 3단계 = 더 센 순화(SOFTEN_STRONG_EN·노골적·애정 표현 대사 LLM 1번) — 2단계(Seedance 대체) 뒤, 또는 지금 모델이 이미 대체 모델이라
# 2단계가 없을 때 1단계 뒤. 모델은 그 앞 단계 그대로. 그래도 거절이면 CONTENT_FLAG_FAIL_TEXT. contentStep = 0·1·2·3.

CONTENT_FLAG_KIND = "contentFlag"
CONTENT_FLAG_WORDS = ("content checker", "flagged", "content policy", "safety", "moderation", "sensitive content", "sensitive",
                      "결과 영상에 민감한 내용")
CONTENT_FLAG_FAIL_TEXT = ("fal 콘텐츠 검사에서 거절되었습니다(컷 {cuts}). 대사·동작 표현을 순화했지만 계속 거절되어 "
                          "이 테이크를 만들지 못했습니다.")
CONTENT_STEP_NAMES = {1: "soften", 2: "fallback", 3: "strongSoften", 4: "dropRefs", 5: "rewriteLines"}
CONTENT_STEP_STRONG = 3
# 작업 168: 실사만 3단계 뒤 2단계 더(제출 1번씩, 칸 attempt 로 센다 — 조각당 추가 제출 최대 2번, 되돌아가지 않는다).
# 4단계 = 위험한 참고 이미지 빼기(앞 조각 마지막 프레임·장면 옷 편집 그림, 시트·장소 뷰는 남김), 5단계 = 대사 LLM 1번 다시 쓰기
# (실패하면 그 조각 대사를 뺀다)·닿는 연기 중립 낱말. 모델·순화는 앞 단계 그대로. 그래도 거절이면 CONTENT_FLAG_FAIL_TEXT.
CONTENT_STEP_DROP_REFS = 4
CONTENT_STEP_REWRITE = 5
CONTENT_STEP_MAX = CONTENT_STEP_REWRITE
SOFTEN_STRONG = 2  # _take_payload(soften=SOFTEN_STRONG) = 더 센 순화(작업 163). True 는 지금 순화 그대로.
SOFTEN_DROP_REFS = 3  # 작업 168: 4단계 = 더 센 순화 + 위험한 참고 이미지 빼기
SOFTEN_REWRITE = 4  # 작업 168: 5단계 = 4단계 + 대사 다시 쓰기·닿는 연기 중립 낱말
# 대사(한국어): 노골적·비속한 말 → 같은 뜻·말투의 순한 말. 앞에서부터 차례로 바꾼다(긴 것 먼저).
SOFTEN_KO = (
    ("붙어 먹으면", "함께 자면"), ("붙어먹으면", "함께 자면"), ("붙어 먹고", "함께 자고"), ("붙어먹고", "함께 자고"),
    ("붙어 먹는", "함께 자는"), ("붙어먹는", "함께 자는"), ("붙어 먹었", "함께 잤"), ("붙어먹었", "함께 잤"),
    ("붙어 먹", "함께 자"), ("붙어먹", "함께 자"), ("따먹", "꼬시"), ("좆같", "엿같"), ("좆", "물건"),
    ("씨발", "젠장"), ("씨팔", "젠장"), ("존나", "진짜"), ("개새끼", "나쁜 놈"), ("섹스", "잠자리"),
    ("꼴려", "끌려"), ("꼴리", "끌리"),
)
# 연기·효과 글(영어): 성적·신체 밀착 표현 → 중립 블로킹. (정규식, 바꿀 말). 첫 글자 대소문자는 원문을 따른다.
SOFTEN_EN = (
    (r"\bguides (?:it|his hand|her hand) (?:downwards?|down|lower)\b", "holds his hand against his chest"),
    (r"\bguiding (?:it|his hand|her hand) (?:downwards?|down|lower)\b", "holding his hand against his chest"),
    (r"\bguided (?:it|his hand|her hand) (?:downwards?|down|lower)\b", "held his hand against his chest"),
    (r"\b(?:lips|mouths) (?:separate|part)s? by a few (?:centimet(?:re|er)s|inches)\b", "faces stay very close"),
    (r"\b(?:lips|mouths) (?:separate|part)\b", "faces stay very close"),
    (r"\b(?:deep|passionate|french) kiss(?:es|ing)?\b", "close embrace"),
    (r"\bstraddles\b", "sits facing"), (r"\bstraddling\b", "sitting facing"),
    (r"\bgrinds against\b", "leans against"), (r"\bgrinding against\b", "leaning against"),
    (r"\bmoans\b", "sighs"), (r"\bmoaning\b", "sighing"), (r"\bmoan\b", "sigh"),
    (r"\b(?:crotch|groin)\b", "waist"), (r"\b(?:naked|nude)\b", "bare-shouldered"), (r"\berection\b", "tension"),
    (r"\bthrusts\b", "leans in"), (r"\bthrusting\b", "leaning in"),
    (r"\bundresses\b", "loosens his collar"), (r"\bundressing\b", "loosening his collar"),
)
_SOFTEN_EN_RE = tuple((re.compile(pattern, re.IGNORECASE), repl) for pattern, repl in SOFTEN_EN)
# 지도로 바꾸고도 남으면 LLM 1번(_soften_rewrite)으로 고칠 낱말(지도 왼쪽 + 지도에 없는 노골적인 말).
_EXPLICIT_KO = re.compile("|".join([re.escape(k) for k, _ in SOFTEN_KO] + ["박아", "빨아", "자지", "정액", "발기", "애무",
                                                                         "떡치", "사까시"]))
_EXPLICIT_EN = re.compile(
    "|".join([p for p, _ in SOFTEN_EN] + [
        r"\bsex(?:ual(?:ly)?|y)?\b", r"\bpenis\b", r"\bgenitals?\b", r"\borgasm\w*", r"\bejaculat\w*", r"\barous(?:ed|al)\b",
        r"\berotic\w*", r"\blick(?:s|ing|ed)?\b",
        r"\b(?:hand|hands|it) (?:slides?|sliding|moves?|travels?) (?:down|downwards?|lower)\b"]),
    re.IGNORECASE)
# 작업 163: 더 센 순화(3단계) — 연기·포즈·닿는 연기 글의 입맞춤·애무·친밀 표현 → "faces close" / 뺨에 손. (정규식, 바꿀 말).
# 바꿀 말의 {who} = 원문 소유격(없으면 "the"). 지도(SOFTEN_EN) 다음에 차례로 바꾼다.
_STRONG_PART = r"(?:lips|mouth|neck|nape|throat|ears?|earlobes?|collarbones?|chest|thighs?|waist|hips?|body|skin)"
_STRONG_WHO = r"(?:(?P<who>[A-Za-z][\w-]*'s|his|her|their|the)\s+)?"
_STRONG_TOUCH = _STRONG_WHO + _STRONG_PART + r"(?:\s+and\s+(?:[A-Za-z][\w-]*'s\s+|his\s+|her\s+|their\s+)?" + _STRONG_PART + r")?\b"
SOFTEN_STRONG_EN = (
    (r"\b(?:kisses|caresses|strokes|nuzzles|licks|nibbles)\s+" + _STRONG_TOUCH, "rests a hand on {who} cheek"),
    (r"\b(?:kissing|caressing|stroking|nuzzling|licking|nibbling)\s+" + _STRONG_TOUCH, "resting a hand on {who} cheek"),
    (r"\b(?:kissed|caressed|stroked|nuzzled|licked|nibbled)\s+" + _STRONG_TOUCH, "rested a hand on {who} cheek"),
    (r"\b(?:kiss|caress|stroke|nuzzle|lick|nibble)\s+" + _STRONG_TOUCH, "rest a hand on {who} cheek"),
    (r"\b(?:(?:their|his|her|[A-Za-z][\w-]*'s)\s+)?(?:lips|mouths)\s+(?:almost\s+|nearly\s+|gently\s+|softly\s+)?"
     r"(?:meet|meets|meeting|met|touch|touches|touching|touched|lock|locks|locking|locked|brush|brushes|brushing|brushed"
     r"|press|presses|pressing|pressed)\b", "faces close"),
    (r"\bkisses\s+(?P<obj>him|her|them|[A-Z][\w-]*)\b", "leans in, faces close to {obj}"),
    (r"\bkissing\s+(?P<obj>him|her|them|[A-Z][\w-]*)\b", "leaning in, faces close to {obj}"),
    (r"\bkissed\s+(?P<obj>him|her|them|[A-Z][\w-]*)\b", "leaned in, faces close to {obj}"),
    (r"\b(?:share|shares|sharing|shared|in|into|for|with)\s+(?:a|the|their|his|her)\s+(?:\w+\s+)?kiss(?:es)?\b",
     "with faces close"),
    (r"\b(?:(?:a|the|their|his|her)\s+)?kiss(?:es|ed|ing)?\b", "faces close"),
    (r"\bmak(?:es|ing|e)\s+out\b", "stays with faces close"),
    (r"\bcaresses\b", "rests a hand on"), (r"\bcaressing\b", "resting a hand on"), (r"\bcaressed\b", "rested a hand on"),
    (r"\bcaress\b", "touch"),
    (r"\bintimately\b", "closely"), (r"\bintimate\b", "close"), (r"\bintimacy\b", "closeness"),
    (r"\bsensually\b", "gently"), (r"\bsensual\b", "gentle"), (r"\bseductively\b", "calmly"), (r"\bseductive\b", "calm"),
    (r"\blustfully\b", "intently"), (r"\blustful\b", "intent"),
)
_SOFTEN_STRONG_RE = tuple((re.compile(pattern, re.IGNORECASE), repl) for pattern, repl in SOFTEN_STRONG_EN)
# 더 센 순화 때 LLM 1번(_soften_rewrite)으로 고칠 애정 표현(한국어 대사·지도에 없는 영어).
_INTIMATE_RE = re.compile(
    r"키스|입맞|뽀뽀|애무|핥|야한|야하게|흥분|벗겨|만져 줘|만져줘|"
    r"\bkiss\w*|\bcaress\w*|\bintima\w*|\bsensual\w*|\bseduc\w*|\blustful\w*|\bmak(?:es|ing|e) out\b",
    re.IGNORECASE)
_SOFTEN_STRONG_RULE = ("Stronger rewrite: remove every kiss, caress or intimate wording. Use \"faces close\" or "
                       "\"a hand on the cheek\" instead; Korean dialogue must not mention kissing or touching the body.")
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_SOFTEN_CACHE: dict[str, dict[str, str]] = {}
_SOFTEN_CACHE_MAX = 64
_SOFTEN_SYSTEM = (
    "You rewrite short pieces of a drama script so that a video model's content checker accepts them. "
    "For every item keep the same meaning, emotion, length and speaker; make it broadcast-safe. "
    "Items with lang \"ko\" are Korean dialogue: Korean only, keep every \" / \" separator and any \"name:\" speaker prefix. "
    "Items with lang \"en\" are English action text: English only, describe neutral blocking (where people stand, faces close, "
    "hands on shoulders or chest) with no sexual or explicit wording. Items with lang \"mixed\" keep their names as written. "
    "Return every item id exactly once with its rewritten text."
)
_SOFTEN_TOOL = {
    "name": "emit_softened",
    "description": "Return the broadcast-safe rewrite of every item.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}}, "required": ["id", "text"]}}},
        "required": ["items"],
    }},
}


def _content_kind(message) -> str:
    """fal 실패 문구가 콘텐츠 검사 거절이면 "contentFlag", 아니면 ""(작업 153, 웹툰·실사 테이크·152 조각이 같이 쓴다).

    작업 163: 결과 영상 sensitive 거절(원문·fal.rejection 한국어 문구)도 contentFlag. 소리 거절(audio)은 늘 "" — 그 길은 따로다.
    """
    if _audio_kind(str(message or "")):
        return ""
    low = str(message or "").lower()
    return CONTENT_FLAG_KIND if any(word in low for word in CONTENT_FLAG_WORDS) else ""


def _content_step(state) -> int:
    """칸·조각의 콘텐츠 단계(0 = 처음, 1 = 순화·같은 모델, 2 = 순화·Seedance, 3 = 더 센 순화·앞 단계 모델,
    작업 168 실사만: 4 = 위험한 참고 이미지 빼기, 5 = 대사 다시 쓰기)."""
    try:
        return max(0, min(CONTENT_STEP_MAX, int((state or {}).get("contentStep") or 0))) if isinstance(state, dict) else 0
    except (TypeError, ValueError):
        return 0


def _content_next(step: int, fallback: str, real: bool = False) -> int:
    """다음 콘텐츠 단계(작업 163). 0→1, 1→2(대체 모델이 있을 때) 또는 3(지금 모델이 이미 대체 모델), 2→3, 3→0(= 칸 실패).
    작업 168: 실사(real)는 3→4→5, 5→0. 단계는 늘 앞으로만 가서 조각당 추가 제출은 4·5단계 2번뿐이다."""
    if step == 0:
        return 1
    if step == 1:
        return 2 if fallback else CONTENT_STEP_STRONG
    if step == 2:
        return CONTENT_STEP_STRONG
    if real and CONTENT_STEP_STRONG <= step < CONTENT_STEP_MAX:
        return step + 1
    return 0


def _soften_level(step: int):
    """제출의 soften 인자: 0 → False(지금 그대로), 1·2 → True(153 순화), 3 → SOFTEN_STRONG(작업 163),
    4 → SOFTEN_DROP_REFS, 5 → SOFTEN_REWRITE(작업 168)."""
    if step >= CONTENT_STEP_REWRITE:
        return SOFTEN_REWRITE
    if step >= CONTENT_STEP_DROP_REFS:
        return SOFTEN_DROP_REFS
    if step >= CONTENT_STEP_STRONG:
        return SOFTEN_STRONG
    return step >= 1


def _strong(soften) -> bool:
    """soften 인자가 더 센 순화(SOFTEN_STRONG 이상 — 작업 168 4·5단계도 센 순화)인가. True(=1)는 아니다."""
    return not isinstance(soften, bool) and isinstance(soften, int) and soften >= SOFTEN_STRONG


def _drop_refs(soften) -> bool:
    """작업 168: 4단계 이상 — 앞 조각 마지막 프레임·장면 옷 편집 그림을 넣지 않는다."""
    return not isinstance(soften, bool) and isinstance(soften, int) and soften >= SOFTEN_DROP_REFS


def _rewrite_on(soften) -> bool:
    """작업 168: 5단계 — 대사 LLM 다시 쓰기·닿는 연기 중립 낱말."""
    return not isinstance(soften, bool) and isinstance(soften, int) and soften >= SOFTEN_REWRITE


def _content_fallback_model(original: str) -> str:
    """2단계 모델 endpoint. 비었거나 원래 모델과 같으면 ""(2단계를 건너뛴다)."""
    fallback = str(vnew_settings.VNEW_CONTENT_FALLBACK_MODEL or "").strip()
    prefix = vpoc_settings.FAL_ID_PREFIX
    if fallback.startswith(prefix):
        fallback = fallback[len(prefix):]
    original = str(original or "").strip()
    if original.startswith(prefix):
        original = original[len(prefix):]
    return "" if not fallback or fallback == original else fallback


def _soften_text(text: str, strong: bool = False) -> str:
    """대사·연기 글 한 조각을 상수 지도로 순화한다(한국어 지도 → 영어 지도). 바뀔 말이 없으면 같은 글자.

    strong(작업 163, 3단계)이면 그 뒤에 SOFTEN_STRONG_EN(입맞춤·애무·친밀 표현 → faces close·뺨에 손)도 바꾼다.
    """
    out = str(text or "")
    if not out:
        return out
    for old, new in SOFTEN_KO:
        if old in out:
            out = out.replace(old, new)

    def _repl(repl: str):
        def inner(match: re.Match) -> str:
            first = match.group(0)[:1]
            groups = {k: (v or "the") for k, v in match.groupdict().items()}
            value = repl.format(**groups) if groups else repl
            return value[:1].upper() + value[1:] if first.isupper() else value
        return inner

    for pattern, repl in _SOFTEN_EN_RE:
        out = pattern.sub(_repl(repl), out)
    if strong:
        for pattern, repl in _SOFTEN_STRONG_RE:
            out = pattern.sub(_repl(repl), out)
    return out


def _explicit_text(text, strong: bool = False) -> bool:
    """지도로 순화한 뒤에도 노골적인 말이 남았는가(LLM 1번 대상·노골적인 포즈 절). strong 이면 애정 표현도(작업 163)."""
    value = str(text or "")
    return bool(value) and bool(_EXPLICIT_KO.search(value) or _EXPLICIT_EN.search(value)
                                or (strong and _INTIMATE_RE.search(value)))


def _soften_entries(entries: list[dict], strong: bool = False) -> None:
    """테이크 타임라인 항목의 대사·연기·분위기를 지도로 순화한다(제자리). 줄 수·화자·시각·run 표시는 그대로."""
    for entry in entries:
        for field in ("action", "dialogue", "moodEn"):
            if isinstance(entry.get(field), str):
                entry[field] = _soften_text(entry[field], strong)
        if isinstance(entry.get("lines"), list):
            entry["lines"] = [{**ln, "dialogue": _soften_text(ln.get("dialogue") or "", strong)} if isinstance(ln, dict) else ln
                              for ln in entry["lines"]]


def _soften_lang(text: str) -> str:
    hangul, latin = bool(_HANGUL_RE.search(text)), bool(_LATIN_RE.search(text))
    return "ko" if hangul and not latin else ("en" if latin and not hangul else "mixed")


def _soften_ok(source: str, new: str, lang: str) -> bool:
    """LLM 순화 결과를 받을지: 빈 글·" / " 수·화자 머리·언어·길이(0.5~2배)가 원문과 맞아야 한다."""
    if not new or source.count(" / ") != new.count(" / "):
        return False
    if lang == "ko" and _LATIN_RE.search(new):
        return False
    if lang == "en" and _HANGUL_RE.search(new):
        return False
    if ": " in source:
        head = source.split(": ", 1)[0]
        if len(head) <= 20 and new.split(": ", 1)[0] != head:
            return False
    return len(source) * 0.5 - 4 <= len(new) <= len(source) * 2 + 8


def _read_soften(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 emit_softened 를 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_SOFTEN_SYSTEM, blocks=blocks, tool=_SOFTEN_TOOL, max_tokens=1500,
        where="vnew.generate.soften", model=model,
    )


def _soften_rewrite(owner: str, pid: str, plan_id: str, texts: list[str], strong: bool = False) -> dict[str, str]:
    """지도로 순화하고도 노골적인 글들을 LLM 1번으로 고친다(작업 153). {원문: 고친 글}. 실패·안 맞는 답은 빼고(지도 결과 유지).

    같은 글 묶음은 프로세스 안에서 다시 묻지 않는다(_SOFTEN_CACHE). 원장 charge_text 1번(토큰이 있을 때).
    strong(작업 163): 요청 본문에 _SOFTEN_STRONG_RULE 을 붙이고 답도 센 지도로 한 번 더 바꾼다(캐시 키가 따로다).
    """
    import json  # noqa: PLC0415

    texts = [t for t in dict.fromkeys(texts) if t]
    if not texts:
        return {}
    cache_key = hashlib.sha256(("\x01strong\x00" if strong else "").encode("utf-8")
                               + "\x00".join(texts).encode("utf-8")).hexdigest()
    if cache_key in _SOFTEN_CACHE:
        return dict(_SOFTEN_CACHE[cache_key])
    items = [{"id": f"t{i}", "lang": _soften_lang(t), "text": t} for i, t in enumerate(texts, start=1)]
    body = {"items": items, "rule": _SOFTEN_STRONG_RULE} if strong else {"items": items}
    blocks = [{"text": json.dumps(body, ensure_ascii=False)}]
    model = str(vnew_settings.ANALYZE_FALLBACK_MODEL)
    try:
        payload, usage = _read_soften(blocks, model=model)
    except Exception as exc:  # noqa: BLE001 — 고치지 못하면 지도 결과로 낸다
        log.warning("vnew.gen.soften_rewrite_failed pid=%s err=%s", pid, _msg(exc))
        return {}
    used = str((usage or {}).get("model") or model)
    try:
        input_tokens = int((usage or {}).get("inputTokens") or 0)
        output_tokens = int((usage or {}).get("outputTokens") or 0)
        if input_tokens or output_tokens:
            vpoc_ledger.charge_text("vnew-soften-" + secrets.token_hex(6), model=used, input_tokens=input_tokens,
                                    output_tokens=output_tokens, user=owner, project_id=pid, plan_id=plan_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_soften_failed pid=%s err=%s", pid, str(exc)[:200])
    by_id = {item["id"]: item for item in items}
    out: dict[str, str] = {}
    for got in (payload.get("items") or []) if isinstance(payload, dict) else []:
        if not isinstance(got, dict) or str(got.get("id") or "") not in by_id:
            continue
        item = by_id[str(got.get("id"))]
        new = _soften_text(str(got.get("text") or "").strip(), strong)
        if _soften_ok(item["text"], new, item["lang"]):
            out[item["text"]] = new
    log.info("vnew.gen.soften_rewrite pid=%s items=%d kept=%d%s", pid, len(items), len(out), " strong=1" if strong else "")
    if len(_SOFTEN_CACHE) >= _SOFTEN_CACHE_MAX:
        _SOFTEN_CACHE.clear()
    _SOFTEN_CACHE[cache_key] = dict(out)
    return out


def _soften_plan(owner: str, pid: str, plan: dict, take: dict, strong: bool = False, rewrite: bool = False) -> dict:
    """테이크 컷 글(콘티 컷·촬영 지시서 컷·겹치는 대본 줄)을 순화한 계획 사본(작업 153). 원본 계획은 바꾸지 않는다.

    모든 글 조각을 지도(_soften_text)로 바꾸고, 그래도 노골적인 조각이 있으면 한꺼번에 LLM 1번(_soften_rewrite).
    strong(작업 163, 3단계): 센 지도까지 바꾸고, 애정 표현이 남은 조각(대사 포함)도 LLM 1번 대상이다.
    rewrite(작업 168, 5단계): 지도 뒤에 닿는 연기 중립 낱말(_contact_neutral — 아래로 끄는 방향 빼기·"pressed flat" → 손 얹기).
    """
    import copy  # noqa: PLC0415

    cut_ids = {str(c) for c in _seg_cut_ids(take)}
    out = dict(plan)
    conti = dict(plan.get("conti") or {})
    conti["cuts"] = copy.deepcopy(conti.get("cuts") or [])
    out["conti"] = conti
    script = dict(plan.get("script") or {})
    script["entries"] = copy.deepcopy(script.get("entries") or [])
    out["script"] = script
    nodes: list = []
    panels: set[int] = set()
    for cut in conti["cuts"]:
        if isinstance(cut, dict) and str(cut.get("id")) in cut_ids:
            nodes.append(cut)
            panels.update(int(p) for p in cut.get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool))
    for entry in script["entries"]:
        if isinstance(entry, dict) and panels & {int(p) for p in entry.get("panels") or []
                                                 if isinstance(p, (int, float)) and not isinstance(p, bool)}:
            nodes.append(entry)
    previz = plan.get("previz") if isinstance(plan.get("previz"), dict) else None
    brief = (previz or {}).get("shotBrief")
    if isinstance(brief, dict) and isinstance(brief.get("cuts"), dict):
        brief = {**brief, "cuts": copy.deepcopy(brief["cuts"])}
        out["previz"] = {**previz, "shotBrief": brief}
        nodes.extend(item for cid, item in brief["cuts"].items() if str(cid) in cut_ids and isinstance(item, dict))

    leaves: list[tuple] = []

    def walk(node) -> None:
        keys = node.keys() if isinstance(node, dict) else range(len(node)) if isinstance(node, list) else ()
        for key in list(keys):
            value = node[key]
            if isinstance(value, str):
                node[key] = _soften_text(value, strong)
                if rewrite:
                    node[key] = _contact_neutral(node[key])
                leaves.append((node, key))
            elif isinstance(value, (dict, list)):
                walk(value)

    for node in nodes:
        walk(node)
    explicit = [node[key] for node, key in leaves if _explicit_text(node[key], strong)]
    if explicit:
        fixed = _soften_rewrite(owner, pid, str(plan.get("planId") or ""), explicit, strong)
        for node, key in leaves:
            if node[key] in fixed:
                node[key] = fixed[node[key]]
    return out


# ---------------------------------------------------------------- 작업 168: 5단계 — 닿는 연기 중립 낱말 · 대사 다시 쓰기
# 닿는 연기(영어): 아래로 끄는 방향은 빼고(허리·벨트 쪽 목적지도), 가슴에 눌러 대는 말은 손을 얹는 말로. (정규식, 바꿀 말 — \1 가능).
_DOWN_VERBS = (r"(?:pulled|pulls|pull|pulling|drawn|draws|draw|drawing|dragged|drags|drag|dragging|tugged|tugs|tug|tugging|"
               r"guided|guides|guide|guiding|led|leads|leading|slides|slide|slid|sliding|moves|move|moved|moving|travels|"
               r"travel|travelled|traveling|travelling)")
_LOW_PART = r"(?:belt|belt line|waist|waistband|hips?|crotch|groin|lap|thighs?|stomach|abdomen|pelvis|trousers|pants|jeans)"
_OWN = r"(?:the\s+|his\s+|her\s+|their\s+|[A-Z][\w-]*['\u2019]s\s+)?"
CONTACT_NEUTRAL_EN = (
    (r"\bfrom\s+" + _OWN + r"chest\s+(?:downwards?|down|lower)\b", "at chest level"),
    (r"\b(?:slow\s+)?tilt(?:s|ing)?[\s-]?down(?:wards?)?\s+following\b", "static framing on"),
    (r"\s*,?\s*(?:\b(?:and|then)\s+)?(?:\b(?:is|are|gets|being)\s+)?\b" + _DOWN_VERBS
     + r"\s+(?:(?:it|them)\s+|" + _OWN + r"hands?\s+)?(?:downwards?|down|lower)\b"
     + r"(?:\s+(?:toward|towards|to|over|past|along)\s+" + _OWN + r"(?:[\w-]+\s+)?" + _LOW_PART + r"\b)?", ""),
    (r"\s+(?:toward|towards|down to|over)\s+" + _OWN + r"(?:[\w-]+\s+)?(?:belt|belt line|waistband|crotch|groin|pelvis|lap)\b", ""),
    (r"\bpresses\s+(it|them|" + _OWN + r"hands?)\s+flat\s+against\b", r"holds \1 against"),
    (r"\bpressing\s+(it|them|" + _OWN + r"hands?)\s+flat\s+against\b", r"holding \1 against"),
    (r"\bis\s+pressed\s+flat\b", "rests"),
    (r"\bpressed\s+flat\b", "resting"),
)
_CONTACT_NEUTRAL_RE = tuple((re.compile(pattern, re.IGNORECASE), repl) for pattern, repl in CONTACT_NEUTRAL_EN)


def _contact_neutral(text: str) -> str:
    """작업 168 5단계: 닿는 연기 글의 아래로 끄는 방향을 빼고 가슴에 눌러 대는 말을 손 얹기로(지도 뒤). 바뀔 말이 없으면 같은 글자."""
    out = str(text or "")
    if not out or not _LATIN_RE.search(out):
        return out
    before = out
    for pattern, repl in _CONTACT_NEUTRAL_RE:
        out = pattern.sub(repl, out)
    if out == before:
        return out
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"([,;])(?=[,.;])", "", out)
    out = re.sub(r"\.(?:\s*\.)+", ".", out)
    return out.strip()


_LINES_SYSTEM = (
    "You check the spoken lines of one part of a drama so that a video model's content checker accepts them. "
    "Rewrite every line that is sexual, an innuendo or explicit (Korean or English) into a neutral line with the same intent, "
    "emotion, speaker and about the same length; for example \"물건 큰 놈이랑 함께 자면\" becomes \"나랑 한번 만나 보면\". "
    "Never mention bodies, sleeping together or touching. Return every other line exactly as given. "
    "Items with lang \"ko\" stay Korean only; keep every \" / \" separator and any \"name:\" speaker prefix. "
    "Return every item id exactly once with its text."
)


def _read_line_rewrite(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """작업 168: llm._converse 로 emit_softened(대사 다시 쓰기)를 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_LINES_SYSTEM, blocks=blocks, tool=_SOFTEN_TOOL, max_tokens=1200,
        where="vnew.generate.rewrite_lines", model=model,
    )


def _part_line_texts(entries: list[dict]) -> list[str]:
    """테이크(조각) 항목들의 대사 글(첫 줄·모든 줄·이어지는 말 utterance), 처음 나온 순서·한 번씩."""
    texts: list[str] = []
    for entry in entries:
        values = [entry.get("dialogue")]
        for line in entry.get("lines") or []:
            if isinstance(line, dict):
                values.extend([line.get("dialogue"), line.get("utterance")])
        for value in values:
            text = str(value or "").strip() if isinstance(value, str) else ""
            if text and text not in texts:
                texts.append(text)
    return texts


def _rewrite_part_lines(owner: str, pid: str, plan: dict, take: dict) -> tuple[dict | None, dict]:
    """작업 168 5단계: 그 조각의 대사를 LLM 1번으로 다시 쓴다(노골적·성적·암시만 바뀐다). (lines_fix, 기록).

    lines_fix = {"map": {순화한 원문: 새 글}}(빌더가 _soften_entries 뒤에 바꾼다) 또는 {"drop": True}(대사를 뺀다).
    기록 = {"dialogueSoftened": [{orig, new}]}(바뀐 줄만) 또는 {"audioDropped": True}. 대사가 없으면 (None, {}) — 호출 0.
    LLM 이 실패하거나 어느 줄이든 답이 없거나 맞지 않으면(언어·" / "·화자·길이, 아직 노골적) 실패로 보고 대사를 뺀다.
    원장 charge_text 1번(토큰이 있을 때). 같은 조각을 다시 묻지 않게 캐시하지 않는다(5단계는 조각당 1번뿐).
    """
    import json  # noqa: PLC0415

    entries = _take_timeline_entries(plan, take)
    _soften_entries(entries, True)
    texts = _part_line_texts(entries)
    if not texts:
        return None, {}
    items = [{"id": f"t{i}", "lang": _soften_lang(t), "text": t} for i, t in enumerate(texts, start=1)]
    model = str(vnew_settings.ANALYZE_FALLBACK_MODEL)
    fixed: dict[str, str] = {}
    failed = ""
    try:
        payload, usage = _read_line_rewrite([{"text": json.dumps({"items": items}, ensure_ascii=False)}], model=model)
    except Exception as exc:  # noqa: BLE001 — 다시 쓰지 못하면 대사를 뺀다
        payload, usage, failed = {}, {}, _msg(exc)
    if not failed:
        used = str((usage or {}).get("model") or model)
        try:
            input_tokens = int((usage or {}).get("inputTokens") or 0)
            output_tokens = int((usage or {}).get("outputTokens") or 0)
            if input_tokens or output_tokens:
                vpoc_ledger.charge_text("vnew-lines-" + secrets.token_hex(6), model=used, input_tokens=input_tokens,
                                        output_tokens=output_tokens, user=owner, project_id=pid,
                                        plan_id=str(plan.get("planId") or ""))
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.gen.charge_lines_failed pid=%s err=%s", pid, str(exc)[:200])
        got = {str(g.get("id") or ""): str(g.get("text") or "").strip()
               for g in ((payload or {}).get("items") or []) if isinstance(g, dict)} if isinstance(payload, dict) else {}
        for item in items:
            new = _soften_text(got.get(item["id"], ""), True)
            if not _soften_ok(item["text"], new, item["lang"]) or _explicit_text(new, True):
                failed = f"bad answer {item['id']}"
                break
            fixed[item["text"]] = new
    if failed:
        log.warning("vnew.gen.rewrite_lines_failed pid=%s take=%s lines=%d err=%s", pid, take.get("id"), len(texts), failed)
        return {"drop": True}, {"audioDropped": True}
    changed = [{"orig": old, "new": new} for old, new in fixed.items() if new != old]
    log.info("vnew.gen.rewrite_lines pid=%s take=%s lines=%d changed=%d", pid, take.get("id"), len(texts), len(changed))
    return {"map": {old: new for old, new in fixed.items() if new != old}}, {"dialogueSoftened": changed}


def _apply_lines_fix(entries: list[dict], fix: dict | None) -> None:
    """작업 168: 타임라인 항목 대사에 5단계 결과를 넣는다(제자리). drop 이면 대사 줄을 모두 뺀다(연기·화면은 그대로)."""
    if not isinstance(fix, dict):
        return
    if fix.get("drop"):
        for entry in entries:
            entry["dialogue"] = ""
            entry["speaker"] = ""
            entry["lines"] = []
            entry.pop("thought", None)
        return
    mapping = fix.get("map") or {}
    if not mapping:
        return
    for entry in entries:
        if isinstance(entry.get("dialogue"), str) and entry["dialogue"].strip() in mapping:
            entry["dialogue"] = mapping[entry["dialogue"].strip()]
        if isinstance(entry.get("lines"), list):
            entry["lines"] = [{**ln, **{k: mapping[ln[k].strip()] for k in ("dialogue", "utterance")
                                        if isinstance(ln.get(k), str) and ln[k].strip() in mapping}}
                              if isinstance(ln, dict) else ln for ln in entry["lines"]]


def _cut_range_text(plan: dict, cut_ids) -> str:
    """컷 번호 표기(작업 153 실패 문구): "11" 또는 "9…13". 번호를 모르면 "?"."""
    no_by_id = {str(c.get("id")): c.get("no") for c in (plan.get("conti") or {}).get("cuts") or [] if isinstance(c, dict)}
    nos = [no_by_id.get(str(c)) for c in cut_ids or []]
    nos = [n for n in nos if n is not None]
    if not nos:
        return "?"
    return str(nos[0]) if len(nos) == 1 else f"{nos[0]}…{nos[-1]}"


def _escalate_content(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """통째 테이크 칸의 콘텐츠 검사 거절 → 다음 단계로 같은 칸을 queued(0→1, 1→2). 마지막이면 한국어 문구로 칸 실패."""
    box: dict = {}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        take = _previz_cells(g)[index]
        state = take[variant]
        step = _content_step(state)
        original = _real_take_model(g) if variant == "real" else str(g.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
        fallback = _content_fallback_model(original)
        nxt = _content_next(step, fallback, variant == "real")  # 작업 163: 2단계 뒤(또는 대체 모델이 없으면 1단계 뒤) 더 센 순화, 168: 실사 4·5단계
        box.update(step=step, next=nxt, cutIds=list(_seg_cut_ids(take)))
        if not nxt:
            return
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")
        state.pop("pendingCharge", None)
        state["contentStep"] = nxt
        if nxt == 2:
            state["contentFallback"] = {"from": original, "to": fallback}

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    name = CONTENT_STEP_NAMES.get(box.get("next") or 0, "failed")
    log.info("vnew.gen.content_flag step=%s pid=%s take=%d from=%d%s", name, pid, index, box.get("step", 0),
             " variant=real" if variant == "real" else "")
    if not box.get("next"):
        plan, _ = plan_mod.load_plan(owner, pid)
        _fail_variant(owner, pid, gen_id, index, variant, claim,
                      CONTENT_FLAG_FAIL_TEXT.format(cuts=_cut_range_text(plan, box.get("cutIds"))), kind=CONTENT_FLAG_KIND)


def _escalate_content_parts(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """152 조각 칸: 콘텐츠 검사로 거절된 조각만 다음 단계로(조각마다 0→1→2). 끝난 조각은 재시도 때 그대로 쓴다.

    어느 조각이든 마지막 단계에서 또 거절이면 그 조각 컷 번호로 칸 실패. 아니면 칸을 queued 로 둔다(제출이 조각 단계를 읽는다).
    """
    box: dict = {}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        state = _previz_cells(g)[index][variant]
        original = _real_take_model(g) if variant == "real" else str(g.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
        fallback = _content_fallback_model(original)
        flagged = [p for p in state.get("parts") or [] if isinstance(p, dict) and p.get("status") == "failed"
                   and p.get("failureKind") == CONTENT_FLAG_KIND]
        moves = []
        for part in flagged:
            step = _content_step(part)
            nxt = _content_next(step, fallback, variant == "real")  # 작업 163, 168: 실사 4·5단계
            moves.append((part, step, nxt))
            if not nxt:
                box.update(failed=list(part.get("cutIds") or []), no=part.get("no"))
                return
        for part, _step, nxt in moves:
            part["contentStep"] = nxt
            if nxt == 2:
                part["contentFallback"] = {"from": original, "to": fallback}
                state["contentFallback"] = {"from": original, "to": fallback}
        box["moves"] = [(p.get("no"), s, n) for p, s, n in moves]
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    if "failed" in box:
        log.info("vnew.gen.content_flag step=failed pid=%s take=%d part=%s", pid, index, box.get("no"))
        plan, _ = plan_mod.load_plan(owner, pid)
        _fail_variant(owner, pid, gen_id, index, variant, claim,
                      CONTENT_FLAG_FAIL_TEXT.format(cuts=_cut_range_text(plan, box["failed"])), kind=CONTENT_FLAG_KIND)
        return
    for no, step, nxt in box.get("moves") or []:
        log.info("vnew.gen.content_flag step=%s pid=%s take=%d part=%s from=%d", CONTENT_STEP_NAMES.get(nxt, "failed"), pid,
                 index, no, step)


# ---------------------------------------------------------------- 작업 155: 조각·Seedance 대체의 장소·이음 프레임·의상 고정
#
# 152 조각과 153 Seedance 대체는 따로 만드는 생성이라 배경·옷이 바뀌었다(test3: 조각 2 회색 벽, 대체에서 지퍼 트랙 재킷).
# ① 장소: 모든 조각·대체 제출에 통 테이크의 장소 뷰(143 실사/웹툰, ASSETS 와 같은 1장)를 맨 끝 이미지로 + TAKE_ANCHOR_LOCATION_LINE.
# ② 이음 프레임(VNEW_PART_CONTINUITY): 조각 k(>1)는 조각 k−1 결과의 마지막 프레임(길이 − TAKE_FRAME_BACK_S)을 1장 더하고
#    차례로 낸다(조각 k 는 k−1 이 끝난 뒤, 기다리는 조각은 status "pending"). 앞 테이크 뒤의 대체는 앞 테이크 결과의 마지막 프레임.
# ③ 의상: 보이는 인물마다 ASSETS COSTUME(146 정리) + _costume_lock 구절. 조각·대체가 아닌 통째 요청은 지금과 바이트 단위로 같다.

TAKE_FRAME_BACK_S = 0.05
TAKE_ANCHOR_LOCATION_LINE = "@Image{no} = the location; keep exactly this place, background and light in every shot"
TAKE_ANCHOR_FRAME_LINE = ("@Image{no} = the previous shot's last frame: same background, lighting, outfits and hair; "
                          "continue from it")
# 의상 고정 "no …" 목록의 옷 낱말(한 목록). 그 인물 옷 문장에 든 낱말(정규식)은 빼고 쓴다.
COSTUME_LOCK_GARMENTS = (
    ("jacket", r"\bjackets?\b"),
    ("zipper", r"\bzip(?:per(?:s|ed)?|ped|-up|s)?\b"),
    ("hoodie", r"\bhood(?:ie|ies|ed|s)?\b"),
)
_COSTUME_LOCK_RE = tuple((word, re.compile(pattern, re.IGNORECASE)) for word, pattern in COSTUME_LOCK_GARMENTS)


def _costume_lock_tail(costume: str) -> str:
    """의상 고정 구절의 뒤쪽("same garment in every shot, no …"). 작업 157 ASSETS 짧은 형은 이것을 COSTUME 문장에 붙인다."""
    costume = str(costume or "").strip().rstrip(" .")
    if not costume:
        return ""
    banned = [word for word, pattern in _COSTUME_LOCK_RE if not pattern.search(costume)]
    head = (", ".join(banned) + " or ") if banned else ""
    return f"same garment in every shot, no {head}other garment change"


def _costume_lock(name: str, costume: str) -> str:
    """인물 1명의 의상 고정 구절(끝 마침표 없음). no 목록 = COSTUME_LOCK_GARMENTS 중 그 옷 문장에 없는 낱말."""
    costume = str(costume or "").strip().rstrip(" .")
    if not costume:
        return ""
    return f"{name} wears exactly {costume}; {_costume_lock_tail(costume)}"


def _video_last_frame(key: str, seconds: float) -> bytes | None:
    """끝난 결과 영상(S3 키)의 마지막 프레임 JPEG(_cut_first_frame, 길이 − 0.05초). 못 뽑으면 None(프레임 없이 낸다)."""
    if not key:
        return None
    try:
        mp4 = _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.last_frame_fetch_failed key=%s err=%s", key, type(exc).__name__)
        return None
    duration = _mp4_seconds(mp4) or float(seconds or 0)
    frame = _cut_first_frame(mp4, max(0.0, duration - TAKE_FRAME_BACK_S)) if duration > 0 else None
    del mp4
    return frame


def _prev_take_frame(plan: dict, index: int, variant: str) -> tuple[bytes | None, str]:
    """앞 테이크(index − 1) 같은 그림체 결과의 마지막 프레임과 그 키. 앞 테이크가 없거나 아직 안 끝났으면 (None, "")."""
    if not vnew_settings.VNEW_PART_CONTINUITY or index <= 0:
        return None, ""
    cells = _previz_cells(plan.get("gen") or {})
    if index - 1 >= len(cells):
        return None, ""
    prev = cells[index - 1]
    state = prev.get(variant) or {}
    key = str(state.get("outputKey") or "")
    if state.get("status") != "completed" or not key:
        return None, ""
    return _video_last_frame(key, float(prev.get("seconds") or 0)), key + "#last"


def _queue_next_part(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """이음 프레임 조각 칸: 낸 조각이 모두 끝났고 기다리는 조각이 있으면 칸을 queued 로(제출이 끝난 조각을 다시 쓰고 다음 조각을 낸다)."""
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        state = _previz_cells(g)[index][variant]
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    log.info("vnew.gen.take_part_next pid=%s take=%d%s", pid, index, " variant=real" if variant == "real" else "")


# ---------------------------------------------------------------- 작업 156: 실사 테이크 모델 · 결과 검사(화면 글자·멈춘 화면)
#
# ① 실사 테이크(start_real·autoReal·실사 다시 시도·인물 바꾸기)는 늘 VNEW_REAL_TAKE_MODEL(기본 Seedance 2.5 r2v)로 낸다 — 애니 테이크
#    모델과 따로다(test3: Wan 3.0 실사가 길이·자막·멈춤·의상이 틀렸다). 금액·realRun.endpoint·152 조각 상한도 이 모델 기준이다.
# ② 테이크·조각 결과마다(애니·실사) 검사 1번: 화면 글자(VNEW_TEXT_CHECK, 25·50·75% 프레임 3장 vision 1번, charge_text)와 멈춘 화면
#    (ffmpeg 로 FROZEN_FPS·FROZEN_SIZE 회색 프레임의 평균 절대 차이, FROZEN_DIFF 아래인 구간이 FROZEN_SHARE 넘음). 걸리면 그 테이크
#    (조각)만 1번 다시 만든다 — 글자면 대사를 TEXT_RETRY_SPOKEN 으로 적고 RULES 맨 앞에 TEXT_RETRY_RULE. 기록 = 칸(조각) qc
#    {reasons, first{outputKey, text, motion, frozenShare, frozen}[, done, used, second]}. 둘 중 글자 프레임이 적은 쪽, 같으면(멈춤이
#    이유일 때) 더 움직이는 쪽, 그래도 같으면 첫 것. 고른 쪽에 글자가 남으면 note = TEXT_LEFT_NOTE. 다시 만든 것이 실패하면 첫 것을 쓴다.
#    영상(mp4)이 아니거나 못 읽으면 검사하지 않는다(지금 그대로).

TEXT_CHECK_AT = (0.25, 0.5, 0.75)
TEXT_CHECK_QUESTION = "Is there any visible text, subtitle or caption overlaid on the picture?"
TEXT_CHECK_SYSTEM = (
    "You inspect frames taken from a generated drama video. For every image answer one question: " + TEXT_CHECK_QUESTION
    + " Count lettering drawn over the picture (subtitles, captions, titles, speech text). Writing that belongs to an object "
    "in the scene (a sign, a book, a screen) does not count."
)
_TEXT_CHECK_TOOL = {
    "name": "emit_text_check",
    "description": "Return, for every image, whether text, a subtitle or a caption is overlaid on the picture.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"frames": {"type": "array", "items": {
            "type": "object", "properties": {"image": {"type": "integer"}, "text": {"type": "boolean"}},
            "required": ["image", "text"]}}},
        "required": ["frames"],
    }},
}
TEXT_RETRY_SPOKEN = "spoken (audio only, never written on screen)"
TEXT_RETRY_RULE = "no captions, no burned-in subtitles, no on-screen lettering."
TEXT_LEFT_NOTE = "화면에 글자가 남았습니다"
# 멈춘 화면: 초당 4장·64x36 회색으로 줄인 프레임의 이웃 차이 평균(0~255). 말하는 샷(입·고개·숨)은 이 축소에서도 1 을 넘고,
# test3 take2 처럼 같은 옆얼굴이 몇 초 서 있는 화면은 압축 잡음뿐이라 0.5 아래다. 그 구간이 길이의 절반을 넘으면 멈춤.
FROZEN_FPS = 4
FROZEN_SIZE = (64, 36)
FROZEN_DIFF = 0.5
FROZEN_SHARE = 0.5


def _real_take_model(gen: dict | None = None) -> str:
    """실사 테이크 모델 endpoint(작업 156): VNEW_REAL_TAKE_MODEL. 비었거나 fal 등록표에 테이크 모델(videoListField)로 없으면 애니 모델."""
    gen = gen if isinstance(gen, dict) else {}
    anime = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    model = str(vnew_settings.VNEW_REAL_TAKE_MODEL or "").strip()
    if model.startswith(vpoc_settings.FAL_ID_PREFIX):
        model = model[len(vpoc_settings.FAL_ID_PREFIX):]
    if not model or model == anime:
        return anime
    if not _previz_caps(model).get("videoListField"):
        log.warning("vnew.gen.real_take_model_missing model=%s fallback=%s", model, anime)
        return anime
    return model


def _take_split_limit(endpoint: str, real: bool, anime: str = "") -> float:
    """152 조각 상한. 실사가 애니와 다른 모델(작업 156)이면 그 모델의 참고 영상 상한(Seedance = maxRefVideoS = maxDurationS)."""
    limit = _take_ref_limit(endpoint)
    if limit > 0 or not real or not anime or str(endpoint) == str(anime):
        return limit
    try:
        options = vpoc_ark.video_options(vpoc_settings.FAL_ID_PREFIX + str(endpoint))
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.ref_limit_failed endpoint=%s err=%s", endpoint, type(exc).__name__)
        return 0.0
    return max(0.0, float(options.get("maxRefVideoS") or 0)) if options.get("videoInput") else 0.0


def _is_mp4(data) -> bool:
    """mp4(ISO BMFF) 바이트인가(앞 상자 ftyp). 아니면 결과 검사를 하지 않는다."""
    return isinstance(data, (bytes, bytearray)) and len(data) > 12 and bytes(data[4:8]) == b"ftyp"


def _motion_diffs(mp4: bytes) -> list[float]:
    """이웃 프레임 평균 절대 차이 목록(FROZEN_FPS 초당 장수, FROZEN_SIZE 회색). 못 읽으면 []."""
    width, height = FROZEN_SIZE
    size = width * height
    with tempfile.TemporaryDirectory(prefix="vnew-motion-") as workdir:
        src = os.path.join(workdir, "clip.mp4")
        with open(src, "wb") as handle:
            handle.write(mp4)
        result = stitcher._run([stitcher.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", src, "-an",  # noqa: SLF001
                                "-vf", f"fps={FROZEN_FPS},scale={width}:{height},format=gray",
                                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
    raw = result.stdout if result.returncode == 0 else b""
    frames = [raw[at:at + size] for at in range(0, len(raw) - size + 1, size)]
    return [sum(abs(a - b) for a, b in zip(one, two)) / size for one, two in zip(frames, frames[1:])]


def _read_text_check(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 emit_text_check 를 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=TEXT_CHECK_SYSTEM, blocks=blocks, tool=_TEXT_CHECK_TOOL, max_tokens=200,
        where="vnew.generate.text_check", model=model,
    )


def _text_frames(owner: str, pid: str, gen_id: str, frames: list[bytes]) -> int | None:
    """프레임들에 글자가 얹힌 장수(vision 1번, charge_text). 호출이 실패하면 None(판정 없음)."""
    blocks: list[dict] = []
    for number, frame in enumerate(frames, 1):
        blocks += [{"text": f"Image {number}:"}, {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(frame)}}}]
    blocks.append({"text": TEXT_CHECK_QUESTION + " Answer for every image."})
    model = str(vnew_settings.PEOPLE_COUNT_MODEL)
    try:
        payload, usage = _read_text_check(blocks, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.text_check_failed pid=%s err=%s", pid, _msg(exc))
        return None
    plan_mod._charge(owner, pid, gen_id, usage or {}, str((usage or {}).get("model") or model))  # noqa: SLF001
    items = (payload or {}).get("frames") if isinstance(payload, dict) else None
    hits = sum(1 for item in items or [] if isinstance(item, dict) and _flag(item.get("text")))
    return min(hits, len(frames))


def _qc_measure(owner: str, pid: str, gen_id: str, key: str, seconds: float) -> dict | None:
    """결과 1개 검사 → {text(글자 프레임 수, 끔·실패면 None), motion, frozenShare, frozen}. mp4 가 아니면 None(검사 안 함)."""
    try:
        mp4 = _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.qc_fetch_failed pid=%s key=%s err=%s", pid, key, type(exc).__name__)
        return None
    if not _is_mp4(mp4):
        return None
    frames: list[bytes] = []
    diffs: list[float] = []
    _PREPARE_SEM.acquire()  # 061: 영상 바이트·ffmpeg 는 준비 세마포어 안에서
    try:
        duration = _mp4_seconds(mp4) or float(seconds or 0)
        if vnew_settings.VNEW_TEXT_CHECK and duration > 0:
            frames = [f for f in (_cut_first_frame(mp4, duration * at) for at in TEXT_CHECK_AT) if f]
        try:
            diffs = _motion_diffs(mp4)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.gen.motion_failed pid=%s err=%s", pid, type(exc).__name__)
    finally:
        _PREPARE_SEM.release()
    del mp4
    text = _text_frames(owner, pid, gen_id, frames) if frames else None
    share = (sum(1 for d in diffs if d < FROZEN_DIFF) / len(diffs)) if diffs else None
    return {"text": text, "motion": round(sum(diffs) / len(diffs), 3) if diffs else None,
            "frozenShare": round(share, 3) if share is not None else None,
            "frozen": share is not None and share > FROZEN_SHARE}


def _qc_reasons(measured: dict | None) -> list[str]:
    """다시 만들 이유(text·frozen). 검사가 없으면 []."""
    measured = measured or {}
    return [name for name, hit in (("text", int(measured.get("text") or 0) > 0), ("frozen", bool(measured.get("frozen")))) if hit]


def _qc_pending(record) -> bool:
    """검사로 다시 만드는 중인가(첫 결과가 남아 있고 아직 고르지 않았다)."""
    qc = record.get("qc") if isinstance(record, dict) else None
    return isinstance(qc, dict) and bool((qc.get("first") or {}).get("outputKey")) and not qc.get("done")


def _qc_no_text(record) -> bool:
    """다시 만드는 제출에 글자 없는 프롬프트(TEXT_RETRY_*)를 쓰는가."""
    return _qc_pending(record) and "text" in ((record.get("qc") or {}).get("reasons") or [])


def _qc_retry_better(first: dict, second: dict | None, reasons) -> bool:
    """다시 만든 것을 쓰는가: 글자 프레임이 적으면, 같고 멈춤이 이유면 더 움직이면. 다시 만든 것 검사가 없으면 True(135 규칙)."""
    if second is None:
        return True
    one, two = int(first.get("text") or 0), int(second.get("text") or 0)
    if one != two:
        return two < one
    if "frozen" in (reasons or []):
        return float(second.get("motion") or 0) > float(first.get("motion") or 0)
    return False


def _qc_verdict(owner: str, pid: str, gen_id: str, record: dict, key: str, seconds: float, label: str) -> dict:
    """끝난 결과 1개의 검사 판정. {} = 그대로, {"retry": qc} = 1번 다시, {"keep": 쓸 키, "qc": 기록, "note": bool} = 둘 중 고름."""
    qc = record.get("qc") if isinstance(record.get("qc"), dict) else None
    if qc and qc.get("done"):
        return {}
    measured = _qc_measure(owner, pid, gen_id, key, seconds)
    if not _qc_pending(record):
        reasons = _qc_reasons(measured)
        if not reasons:
            return {}
        log.info("vnew.gen.qc_retry pid=%s %s reasons=%s text=%s motion=%s frozen=%s", pid, label, ",".join(reasons),
                 measured.get("text"), measured.get("motion"), measured.get("frozenShare"))
        return {"retry": {"reasons": reasons, "first": {"outputKey": key, **measured}}}
    first = dict(qc["first"])
    use_retry = _qc_retry_better(first, measured, qc.get("reasons"))
    chosen = (measured or {}) if use_retry else first
    note = int(chosen.get("text") or 0) > 0
    done = {**qc, "done": True, "used": "retry" if use_retry else "first", "second": {"outputKey": key, **(measured or {})}}
    log.info("vnew.gen.qc_pick pid=%s %s used=%s text=%s/%s motion=%s/%s", pid, label, done["used"], first.get("text"),
             (measured or {}).get("text"), first.get("motion"), (measured or {}).get("motion"))
    return {"keep": key if use_retry else str(first["outputKey"]), "qc": done, "note": note}


def _qc_first_kept(record: dict) -> dict:
    """다시 만든 것이 실패했을 때 첫 결과를 쓰는 판정(_qc_verdict 의 keep 모양)."""
    qc = dict(record.get("qc") or {})
    first = dict(qc.get("first") or {})
    return {"keep": str(first.get("outputKey") or ""), "qc": {**qc, "done": True, "used": "first", "retryFailed": True},
            "note": int(first.get("text") or 0) > 0}


def _qc_requeue(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, qc: dict) -> None:
    """통째 테이크 칸을 검사 기록(qc)과 함께 다시 queued(1번 다시 만들기, 제출 경로가 qc 를 읽는다)."""
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        s = _previz_cells(g)[index][variant]
        s.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")
        s.pop("pendingCharge", None)  # 첫 결과 금액은 이미 남겼다
        s["qc"] = qc

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _qc_store(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, verdict: dict, key: str) -> None:
    """통째 테이크 칸 완료 기록(작업 156 판정 반영: 쓸 키·qc·note)."""
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        s = _previz_cells(g)[index][variant]
        s["status"] = "completed"
        s["outputKey"] = key
        s["error"] = ""
        s["claim"] = ""
        s["claimedAt"] = ""
        if verdict.get("qc"):
            s["qc"] = verdict["qc"]
            if verdict.get("note"):
                s["note"] = TEXT_LEFT_NOTE

    _commit(owner, pid, mutator)


def _do_submit_take(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """통 테이크 1개(웹툰)를 Seedance 2.5 reference-to-video 로 제출한다(설계자 2026-10-26, 063).

    video_urls = [테이크 녹화 이어붙인 mp4 data URI]. image_urls = 인물 정면·후면 → 패널 → 장소.
    준비(녹화 받기·이어붙이기·payload·제출)는 _PREPARE_SEM 안에서(061 OOM 규칙). draft_id 가 있으면
    cell.draftId 로 저장한다.

    vnew-live-fx C.5: 실사 칸은 gen.realRun(실사 회차)이 있을 때만 같은 녹화·같은 세마포어로 제출한다. 실사
    payload(_take_payload variant="real")는 제출 직전 _assert_no_webtoon 을 지나야 한다(걸리면 칸 failed·제출·원장 0).
    칸 state.audio(bool)가 있으면 generate_audio 로 보낸다("소리 없이 다시 만들기"). 실사 draft_id 는 칸에 둔다.
    """
    if variant not in ("webtoon", "real"):
        return
    real = variant == "real"
    tag = " variant=real" if real else ""  # 실사 로그 꼬리표(웹툰 로그 형식은 그대로)
    stage = "start"
    started = time.monotonic()
    prompt = ""
    prompt_bytes = 0
    prefix = ""
    result = None
    take_seconds = 0.0
    resolution = str(vnew_settings.VNEW_TAKE_RESOLUTION)
    prepared = False
    log_ctx: dict = {}  # 지시서 116: 프롬프트 기록 한 줄의 재료(제출 직전에 채운다)
    _PREPARE_SEM.acquire()
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, variant, claim):
            return
        if real and not gen.get("realRun"):
            # 실사 회차가 없는데 실사 칸이 선점됐다(옛 데이터·수동 수정). 제출하지 않고 실패로 끝낸다(무한 재선점 방지).
            raise VpocError("vnew.generate", "RealNotReady", "실사 회차가 없어 제출하지 않았습니다.", 409)
        take = _previz_cells(gen)[index]
        state = take[variant]
        attempt = int(state.get("attempt") or 1)
        audio_choice = state.get("audio") if isinstance(state.get("audio"), bool) else None
        take_id = str(take.get("id") or index)
        rec_keys = [str(k) for k in (take.get("recKeys") or []) if k]
        if not rec_keys:
            raise VpocError("vnew.generate", "NoRecording", "테이크 녹화가 없습니다.", 409)

        endpoint = _real_take_model(gen) if real else str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
        caps = _previz_caps(endpoint)
        resolution = str(gen.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION)

        stage = "stitch"
        video_mp4 = _stitch_recordings(rec_keys)

        # 작업 152: 녹화가 참고 영상 상한보다 길면 컷 경계 조각으로 나눠 낸다(조각마다 원장 1번, 결과는 조회가 잇는다).
        stage = "split"
        parts = _take_parts_for(plan, take, endpoint, real=real,
                                anime=str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL))
        built = None
        if not parts:
            stage = "payload"
            trace = _RefTrace.start()  # 지시서 116: 이 스레드의 참고 이미지 읽기·맞춤을 따라간다(바이트는 붙잡지 않는다)
            step = _fallback_step(state) if real else 0  # 작업 130: likeness 1단계면 인물당 정면 1장
            content_step = _content_step(state)  # 작업 153: 1 = 순화(같은 모델), 2 = 순화 + Seedance, 163: 3 = 더 센 순화
            continuity = None
            if content_step >= 2 and _content_fallback_model(endpoint):
                # 작업 155: Seedance 대체 = 같은 장소 1장(맨 끝)·의상 고정·원래 모델과 같은 신원 참고·앞 테이크 마지막 프레임(있을 때).
                frame, frame_key = (None, "") if content_step >= CONTENT_STEP_DROP_REFS else _prev_take_frame(plan, index, variant)
                continuity = {"take": take, "frame": frame, "frameKey": frame_key, "origFrame": False,
                              "origMax": int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX)}
                del frame
                endpoint = _content_fallback_model(endpoint)
                caps = _previz_caps(endpoint)
            no_text = _qc_no_text(state)  # 작업 156: 화면 글자 검사로 다시 만드는 제출(대사 audio only·RULES 맨 앞 글자 금지)
            built = _take_payload(owner, pid, plan, take, caps, variant="real", fallback_step=step,
                                  soften=_soften_level(content_step), continuity=continuity, no_text=no_text) if real else _take_payload(
                owner, pid, plan, take, caps, soften=_soften_level(content_step), continuity=continuity, no_text=no_text)
            continuity = None
            # 작업 157: 고정 부분을 다 짧은 형으로 하고 컷 내용까지 줄여도 넘치면(대사는 빼지 않는다) 컷 경계 조각으로 나눠 낸다.
            # 콘텐츠 단계(153)가 걸린 칸은 그 단계 그대로 통 테이크로 낸다(조각 단계 기록이 없다).
            fit_parts = [] if content_step else _prompt_split_parts(plan, take, [], built)
            if fit_parts:
                trace.stop(built)
                built = None
                parts = fit_parts
        if parts:
            run_n = (int((gen.get("realRun") or {}).get("n") or 0) or None) if real else None
            stage = "submit"
            part_records = _submit_take_parts(
                owner, pid, gen_id, index, variant, claim, plan=plan, take=take, state=state, parts=parts,
                video_mp4=video_mp4, endpoint=endpoint, caps=caps, resolution=resolution, audio=audio_choice,
                attempt=attempt, step=_fallback_step(state) if real else 0, rec_keys=rec_keys, run_n=run_n)
            del video_mp4
            prepared = True
            log.info("vnew.gen.take_submit pid=%s take=%s parts=%d ms=%d%s", pid, take_id, len(part_records),
                     int((time.monotonic() - started) * 1000), tag)

            def parts_mutator(data: dict) -> None:
                g = data.get("gen") or {}
                if not _claim_ok_variant(g, index, variant, claim):
                    raise _Discard()
                s = _previz_cells(g)[index][variant]
                s.update(status="submitted", invocationArn="", outputPrefix="", outputKey="", error="")
                s["parts"] = part_records
                s["promptEn"] = "\n\n".join(str(p.get("promptEn") or "") for p in part_records if p.get("promptEn"))
                s["promptBytes"] = sum(int(p.get("promptBytes") or 0) for p in part_records)
                s.pop("pendingCharge", None)  # 조각은 제출 때 원장을 남긴다

            stage = "commit"
            _commit(owner, pid, parts_mutator)
            return  # finally 에서 세마포어 해제.

        if content_step:
            tag += f" content={CONTENT_STEP_NAMES.get(content_step, '')}"
        if built.get("frameNo"):
            tag += " frame=1"
        if _qc_pending(state):
            tag += " qc=" + ",".join((state.get("qc") or {}).get("reasons") or [])
        refs = trace.stop(built)
        prompt = built["prompt"]
        image_urls = built["imageUrls"]
        lines_note = {k: built[k] for k in ("dialogueSoftened", "audioDropped") if k in built}  # 작업 168 5단계
        if content_step:
            log.info("vnew.gen.content_step take=%s part=- step=%d pid=%s name=%s images=%d%s", take_id, content_step, pid,
                     CONTENT_STEP_NAMES.get(content_step, ""), len(image_urls),
                     (f" softened={len(lines_note.get('dialogueSoftened') or [])}" if "dialogueSoftened" in lines_note
                      else " audioDropped=1" if lines_note.get("audioDropped") else ""))
        refs_kind = _person_refs_kind(built.get("cast")) if real else ""
        prompt_bytes = len(prompt.encode("utf-8"))
        prefix = _seg_output_prefix(owner, pid, gen_id, take_id + "-" + variant, attempt)
        take_seconds = float(take.get("seconds") or 0)
        if real:
            stage = "check"
            _assert_no_webtoon(prompt)
        log_ctx = {"mode": "previz-take", "variant": variant, "cell_id": take_id, "index": index, "attempt": attempt,
                   "run": (int((gen.get("realRun") or {}).get("n") or 0) or None) if real else None, "endpoint": endpoint,
                   "params": _take_params(caps, take_seconds, resolution, audio_choice), "refs": refs, "video_keys": rec_keys}

        # 작업 144: 하트비트 + 유료 제출 전 선점 확인(취소·재선점이면 제출하지 않는다).
        if not _touch_prepare_claim(owner, pid, gen_id, index, variant, claim):
            return  # finally 에서 세마포어 해제.
        _log_prompt_audit(pid, take_id, variant, "", built.get("audit"))  # 작업 157
        stage = "submit"
        result = _submit_take(
            endpoint=endpoint,
            caps=caps,
            video_mp4=video_mp4,
            prompt=prompt,
            output_prefix=prefix,
            image_urls=image_urls,
            seconds=take_seconds,
            resolution=resolution,
            audio=audio_choice,
        )
        del video_mp4, built, image_urls
        prepared = True
        log.info(
            "vnew.gen.take_submit pid=%s take=%s bytes_body=%d ms=%d%s%s",
            pid, take_id, prompt_bytes, int((time.monotonic() - started) * 1000),
            f" refs={refs_kind}" if real else "", tag,
        )
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "vnew.gen.take_submit_failed pid=%s take=%s stage=%s err=%s%s",
            pid, index, stage, _msg(exc), tag,
        )
        if stage == "submit":  # 지시서 116: 본문을 보내다 실패한 시도도 기록한다(그 앞 단계는 제출이 아니다)
            _log_attempt(owner, pid, gen_id, source="gen", result=None, prompt=prompt, status="failed", error=_msg(exc), **log_ctx)
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))
        return
    finally:
        _PREPARE_SEM.release()

    if not prepared:
        return

    defer = real and bool(vnew_settings.VNEW_TAKE_REAL_LIKENESS_LADDER)
    if not defer:
        _charge_take(owner, pid, gen_id, result, take_seconds, resolution)
    _log_attempt(owner, pid, gen_id, source="gen", result=result, prompt=prompt, **log_ctx)  # 지시서 116

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        cell = _previz_cells(g)[index]
        s = cell[variant]
        s["status"] = "submitted"
        s["invocationArn"] = result.get("invocationArn") or ""
        s["outputPrefix"] = result.get("outputPrefix") or prefix
        s["error"] = ""
        s["promptEn"] = prompt
        s["promptBytes"] = prompt_bytes
        s.pop("parts", None)  # 작업 152: 조각 없이 낸 시도(옛 조각 기록이 조회를 가르지 않게)
        s.pop("dialogueSoftened", None)
        s.pop("audioDropped", None)
        s.update(lines_note)  # 작업 168 5단계: 바꾼 대사·대사 뺌 기록
        if defer:
            # 작업 130: likeness 거절(생성 전 거절)은 원장 0 — 결과가 정해질 때 _settle_take_charge 가 1번 남긴다.
            s["pendingCharge"] = {"clientRequestToken": str(result.get("clientRequestToken") or ""),
                                  "provider": str(result.get("provider") or ""), "seconds": take_seconds,
                                  "resolution": resolution}
        draft_id = str(result.get("draftId") or "")
        if draft_id and real:
            s["draftId"] = draft_id  # 웹툰 테이크의 cell.draftId 를 덮어쓰지 않는다.
        elif draft_id:
            cell["draftId"] = draft_id

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        if defer:  # 칸이 바뀌어(취소 등) 결과를 기다리지 않는다 — 제출한 것은 보수적으로 남긴다.
            _charge_take(owner, pid, gen_id, result, take_seconds, resolution)
        return
    except Exception as exc:  # noqa: BLE001
        if defer:
            _charge_take(owner, pid, gen_id, result, take_seconds, resolution)
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))


def _do_submit_previz(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """프리비즈 컷 한 그림체(real/webtoon)를 fal video-to-video/edit 로 제출한다(설계자 2026-10-22, ⑤).

    video_url = 그 컷 구간을 녹화 mp4 에서 잘라(3초 미만은 채워) data URI 로. keep_audio false.
    elements = 그 컷에 보이는 인물만(숨긴 인물 제외)의 그림체별 기본 복장 정면 + 후면(비율 맞춤).
    웹툰은 image_urls 1장(원작 웹툰 컷) 추가. prompt 는 CAST/SHOT/.../CONTINUITY(촬영 지시서 사용).
    실제 보낸 프롬프트는 promptEn 으로 기록한다(바이트 수 포함).

    ⑤ OOM 완화(설계자 2026-10-23): "녹화 받기 → 클립 자르기 → payload → 본문 직렬화·제출(HTTP 요청
    보내기까지)" 구간을 프로세스 전역 세마포어(_PREPARE_SEM, 기본 1칸) 안에서 한다. 제출이 끝나면
    즉시 큰 변수(video_mp4·clip·built·result 입력)를 del 로 참조를 끊어 GC 가 봉우리 메모리를
    바로 회수하게 한다. 조회(poll)는 이 세마포어 밖(가볍다). 단계별 실패는 log.warning 에 단계
    이름(fetch/clip/payload/submit)으로 남긴다.
    """
    stage = "start"
    started = time.monotonic()
    bytes_video = 0
    prompt_bytes = 0
    result = None
    prefix = ""
    prompt = ""
    unit_seconds = 0.0
    clip_meta: dict = {}
    prepared = False  # 세마포어 안에서 제출까지 성공했는가.
    log_ctx: dict = {}  # 지시서 116: 프롬프트 기록 한 줄의 재료(제출 직전에 채운다)
    # 통 테이크 모드(설계자 2026-10-26)는 전용 경로로 제출한다(참조 영상 통째 + 참조 이미지 순서).
    try:
        plan0, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    if (plan0.get("gen") or {}).get("mode") == "previz-take":
        cells0 = _previz_cells(plan0.get("gen") or {})
        state0 = (cells0[index].get("real") or {}) if variant == "real" and 0 <= index < len(cells0) else {}
        if _fallback_step(state0) == 2:
            _do_submit_real_cuts(owner, pid, gen_id, index, claim)  # 작업 130 보완 1: 컷 단위 Kling
            return
        _do_submit_take(owner, pid, gen_id, index, variant, claim)
        return
    _PREPARE_SEM.acquire()
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, variant, claim):
            return  # finally 에서 세마포어 해제.
        unit = _previz_cells(gen)[index]
        state = unit[variant]
        attempt = int(state.get("attempt") or 1)
        unit_id = str(unit.get("id") or unit.get("cutId") or index)
        rec_key = str(unit.get("recordingKey") or "")
        if not rec_key:
            raise VpocError("vnew.generate", "NoRecording", "컷 녹화가 없습니다.", 409)

        # fetch — 녹화 mp4 바이트.
        stage = "fetch"
        video_mp4 = _fetch_bytes(rec_key, bucket=vpoc_settings.VIDEO_BUCKET)
        bytes_video = len(video_mp4 or b"")

        # clip — 컷 구간만 잘라(3초 미만은 채워) 준비. 원본 녹화 바이트는 바로 끊는다.
        stage = "clip"
        clip = _cut_clip(video_mp4, unit)
        del video_mp4
        clip_meta = {
            "origSeconds": clip.get("origSeconds"),
            "sentSeconds": clip.get("sentSeconds"),
            "padded": bool(clip.get("padded")),
        }

        # payload — 참고 이미지·프롬프트.
        stage = "payload"
        endpoint = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
        caps = _previz_caps(endpoint)
        trace = _RefTrace.start()  # 지시서 116: 이 스레드의 참고 이미지 읽기·맞춤을 따라간다(바이트는 붙잡지 않는다)
        # 작업 167: Kling 2500 여유 + (크기 거절 뒤 1번) fal 이 알려 준 상한 N 으로 다시 만든다.
        size_cap = int((state.get("sizeRetry") or {}).get("cap") or 0) if isinstance(state.get("sizeRetry"), dict) else 0
        built = _previz_payload(owner, pid, plan, unit, variant, caps=caps, endpoint=endpoint, size_cap=size_cap)
        refs = trace.stop(built)
        prompt = built["prompt"]
        elements = built["elements"]
        image_urls = built["imageUrls"]
        prompt_bytes = len(prompt.encode("utf-8"))
        prefix = _seg_output_prefix(owner, pid, gen_id, unit_id + "-" + variant, attempt)
        log_ctx = {"mode": "previz", "variant": variant, "cell_id": unit_id, "index": index, "attempt": attempt, "run": None,
                   "endpoint": endpoint, "params": _cut_params(unit, clip_meta, caps), "refs": refs, "video_keys": [rec_key]}

        # submit — 본문 직렬화·HTTP 제출(여기까지가 봉우리 메모리). 끝나면 큰 변수 참조를 끊는다.
        stage = "submit"
        result = _submit_previz_edit(
            endpoint=endpoint,
            caps=caps,
            video_mp4=clip["bytes"],
            prompt=prompt,
            output_prefix=prefix,
            elements=elements,
            image_urls=image_urls,
        )
        # 큰 바이트(클립·참고 이미지·payload)만 끊는다. prompt(문자열)는 작아 기록용으로 남긴다.
        del clip, built, elements, image_urls
        unit_seconds = float(unit.get("seconds") or 0)
        prepared = True

        rss = _rss_mb()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "vnew.gen.submit pid=%s cut=%s variant=%s bytes_video=%d bytes_body=%d rss_mb=%.0f ms=%d",
            pid, unit_id, variant, bytes_video, prompt_bytes, rss, elapsed_ms,
        )
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "vnew.gen.submit_failed pid=%s cut=%s variant=%s stage=%s rss_mb=%.0f err=%s",
            pid, index, variant, stage, _rss_mb(), _msg(exc),
        )
        if stage == "submit":  # 지시서 116: 본문을 보내다 실패한 시도도 기록한다(그 앞 단계는 제출이 아니다)
            _log_attempt(owner, pid, gen_id, source="gen", result=None, prompt=prompt, status="failed", error=_msg(exc), **log_ctx)
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))
        return
    finally:
        _PREPARE_SEM.release()

    if not prepared:
        return

    # 제출 성공 → 금액 계상·결과 기록(세마포어 밖, 가볍다).
    _charge_previz(owner, pid, gen_id, result, max(PREVIZ_MIN_CLIP_S, unit_seconds))
    _log_attempt(owner, pid, gen_id, source="gen", result=result, prompt=prompt, **log_ctx)  # 지시서 116

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        s = _previz_cells(g)[index][variant]
        s["status"] = "submitted"
        s["invocationArn"] = result.get("invocationArn") or ""
        s["outputPrefix"] = result.get("outputPrefix") or prefix
        s["error"] = ""
        s["promptEn"] = prompt
        s["promptBytes"] = prompt_bytes
        s["origSeconds"] = clip_meta.get("origSeconds")
        s["sentSeconds"] = clip_meta.get("sentSeconds")
        s["padded"] = bool(clip_meta.get("padded"))
        # claim 은 submitted 조회 때도 쓰므로 유지한다.

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))


def _do_poll_previz(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """프리비즈 컷 한 그림체 조회. 완료면 completed + outputKey."""
    state: dict = {}
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, variant, claim):
            return
        state = _previz_cells(gen)[index][variant]
        arn = str(state.get("invocationArn") or "")
        prefix = str(state.get("outputPrefix") or "")
        take_real = gen.get("mode") == "previz-take" and variant == "real"
        if take_real and _fallback_step(state) == 2:
            _do_poll_real_cuts(owner, pid, gen_id, index, claim)  # 작업 130 보완 1: 컷마다 조회
            return
        if gen.get("mode") == "previz-take" and state.get("parts"):
            _do_poll_take_parts(owner, pid, gen_id, index, variant, claim)  # 작업 152: 조각마다 조회 → 잇기
            return
        poll_result = vpoc_fal.poll(arn, prefix)
        status = str(poll_result.get("status") or "")
        if status == "InProgress":
            return
        if status != "Completed" and gen.get("mode") == "previz-take" and _qc_pending(state):
            # 작업 156: 검사로 다시 만든 테이크가 실패 → 첫 결과를 쓴다(거절 금액 규칙은 130 그대로).
            if take_real and not _likeness_rejected(poll_result.get("failureMessage")):
                _settle_take_charge(owner, pid, gen_id, state)
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "failed",
                        error=str(poll_result.get("failureMessage") or "영상 생성 실패"))
            kept = _qc_first_kept(state)
            _qc_store(owner, pid, gen_id, index, variant, claim, kept, kept["keep"])
            return
        if take_real and status != "Completed" and state.get("pendingCharge") \
                and _likeness_rejected(poll_result.get("failureMessage")):
            # 작업 130: 생성 전 거절이라 원장 0. 다음 단계(정면 1장 → 컷 단위 Kling)로 같은 회차 안에서 다시 만든다.
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "failed", error=str(poll_result.get("failureMessage")))
            log.info("vnew.gen.real_likeness_reject step=%d note=1", _fallback_step(state))  # 작업 140: AI 캐릭터 고지를 보낸 뒤의 거절
            _escalate_likeness(owner, pid, gen_id, index, claim)
            return
        if take_real:
            _settle_take_charge(owner, pid, gen_id, state)
        if status != "Completed":
            message = poll_result.get("failureMessage") or "영상 생성 실패"
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "failed", error=message)  # 지시서 116
            # vnew-live-fx C.6: 소리 검사 거절이면 칸에 failureKind "audio"(화면이 '소리 없이 다시 만들기'를 보인다).
            kind = str(poll_result.get("failureKind") or "") or _audio_kind(message)
            if not kind and gen.get("mode") == "previz" and _prompt_size_cap(message) and not state.get("sizeRetry"):
                # 작업 167: fal 프롬프트 크기 거절 → 더 짧은 예산으로 같은 칸을 1번 다시 낸다(두 번째 거절은 아래 그대로 실패).
                _size_requeue(owner, pid, gen_id, index, variant, claim, _prompt_size_cap(message))
                return
            if not kind and gen.get("mode") == "previz-take" and _content_kind(message):
                # 작업 153: fal 콘텐츠 검사 거절 → 순화(같은 모델) → Seedance → 한국어 문구로 실패.
                _escalate_content(owner, pid, gen_id, index, variant, claim)
                return
            _fail_variant(owner, pid, gen_id, index, variant, claim, message, kind=kind)
            return
        output_key = _find_output_key(prefix, arn)
        verdict: dict = {}
        if gen.get("mode") == "previz-take":
            # 작업 156: 테이크 결과 검사(화면 글자·멈춤) → 1번 다시 만들기 또는 둘 중 고르기. 영상이 아니면 {}(지금 그대로).
            verdict = _qc_verdict(owner, pid, gen_id, state, output_key, float(_previz_cells(gen)[index].get("seconds") or 0),
                                  f"take={index}{' variant=real' if variant == 'real' else ''}")
        if verdict.get("retry"):
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "completed", output_key=output_key)
            _qc_requeue(owner, pid, gen_id, index, variant, claim, verdict["retry"])
            return
        if verdict.get("keep"):
            _qc_store(owner, pid, gen_id, index, variant, claim, verdict, verdict["keep"])
            _log_result(owner, pid, gen_id, gen, index, variant, arn, "completed", output_key=output_key)
            return

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, variant, claim):
                raise _Discard()
            s = _previz_cells(g)[index][variant]
            s["status"] = "completed"
            s["outputKey"] = output_key
            s["error"] = ""
            s["claim"] = ""
            s["claimedAt"] = ""

        _commit(owner, pid, mutator)
        _log_result(owner, pid, gen_id, gen, index, variant, arn, "completed", output_key=output_key)  # 지시서 116
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        if variant == "real":
            _settle_take_charge(owner, pid, gen_id, state)  # 작업 130: 결과를 모르면 보수적으로 남긴다(없으면 아무것도 안 함)
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))


def _size_requeue(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, size_cap: int) -> None:
    """작업 167: 컷 단위 칸의 프롬프트 크기 거절 → state.sizeRetry={cap:N} 를 남기고 같은 칸을 queued(153 처럼 드라이버가
    다시 선점하며 attempt 를 올린다). 제출은 sizeRetry 를 읽어 int(N × 0.85) − 태그 여유로 프롬프트를 다시 만든다. 1번만."""

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        state = _previz_cells(g)[index][variant]
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")
        state["sizeRetry"] = {"cap": int(size_cap)}

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    log.info("vnew.gen.prompt_size_retry pid=%s cell=%d variant=%s cap=%d", pid, index, variant, int(size_cap))


# ================================================================ 작업 130·보완 1: 실사 likeness 거절 → 정면 1장 → 컷 단위 Kling
#
# 실사 통 테이크(Seedance)가 fal likeness 로 거절되면(fal.poll 의 문구 = vpoc_fal._REJECT_LIKENESS_MSG, fal.py 그대로) 같은 칸을
# 다음 단계로 다시 queued 로 둔다. 단계마다 1번. 칸 state.fallback = {step, reason, model, tried[, cuts, total]},
# 회차 realRun.realFallback = {step, reason, model}. 1단계는 _do_submit_take(fallback_step=1), 2단계는 _do_submit_real_cuts.
# Seedance 실사 금액은 결과가 정해질 때 _settle_take_charge 가 남긴다(거절 0). 2단계는 시작 그림·Kling 컷마다 제출 때 남긴다.

REAL_FALLBACK_TRIED = {0: "Seedance", 1: "Seedance 정면 1장", 2: "컷 단위 Kling"}
REAL_FALLBACK_FAIL = "실존 인물 검사에 걸려 실사 영상을 만들지 못했습니다(시도한 단계: {steps}). 인물 사진을 바꿔 다시 만들어 주세요."
REAL_FALLBACK_NO_KLING = " 컷 단위 Kling 모델이 fal 등록표에 없어 그 단계는 건너뛰었습니다."
_SEEDREAM_MAX_REFS = 10


def _fallback_step(state) -> int:
    """칸의 likeness 단계(0 = 기본 Seedance, 1 = 정면 1장, 2 = 컷 단위 Kling)."""
    fallback = (state or {}).get("fallback") if isinstance(state, dict) else None
    try:
        return int((fallback or {}).get("step") or 0) if isinstance(fallback, dict) else 0
    except (TypeError, ValueError):
        return 0


def _likeness_rejected(message) -> bool:
    """fal.poll 실패 문구가 likeness 거절 문구인가(fal.py 의 반환값을 그대로 쓴다)."""
    return str(message or "") == vpoc_fal._REJECT_LIKENESS_MSG


def _settle_take_charge(owner: str, pid: str, gen_id: str, state: dict) -> None:
    """미뤄 둔 Seedance 실사 테이크 금액(state.pendingCharge)을 원장에 1번 남긴다(키 = 작업 토큰이라 두 번 불러도 1건)."""
    pending = (state or {}).get("pendingCharge")
    if not isinstance(pending, dict) or not pending.get("clientRequestToken"):
        return
    _charge_take(owner, pid, gen_id, {"clientRequestToken": pending["clientRequestToken"], "provider": pending.get("provider")},
                 float(pending.get("seconds") or 0), str(pending.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION))


def _kling_cut_model() -> tuple[str, dict] | None:
    """2단계 모델(endpoint, caps). fal 등록표에 있고 시작 그림(startImageField)을 받을 때만, 아니면 None(2단계 건너뜀)."""
    endpoint = str(vnew_settings.VNEW_TAKE_REAL_KLING_MODEL or "").strip()
    if not endpoint:
        return None
    item = vpoc_fal.find(vpoc_settings.FAL_ID_PREFIX + endpoint)
    caps = (item or {}).get("caps") or {}
    if not item or not caps.get("startImageField") or caps.get("available") is False:
        return None
    return endpoint, caps


def _kling_duration(caps: dict, seconds: float) -> int:
    """컷 길이 → 허용 길이(VNEW_TAKE_REAL_KLING_DURATIONS 와 caps.durations 의 교집합) 중 컷 이상인 가장 작은 값, 넘으면 가장 큰 값."""
    allowed = set()
    for value in (caps or {}).get("durations") or []:
        try:
            allowed.add(int(value))
        except (TypeError, ValueError):
            continue
    base = [int(d) for d in vnew_settings.VNEW_TAKE_REAL_KLING_DURATIONS]
    choices = sorted(set(base) & allowed) or sorted(base)
    return next((d for d in choices if d >= float(seconds or 0) - 0.01), choices[-1])


def _take_cut_units(plan: dict, take: dict) -> list[dict]:
    """테이크의 컷 단위 작업(_previz_units 와 같은 모양: cutId·recordingKey·startSec·endSec·seconds), 테이크 컷 순서."""
    by_cut = {str(u.get("cutId") or ""): u for u in _previz_units(plan)}
    return [by_cut[c] for c in (str(c) for c in (take.get("cutIds") or [])) if c in by_cut]


def _cut_first_frame(mp4: bytes, start_sec: float) -> bytes | None:
    """녹화 mp4 의 start_sec 프레임 1장(JPEG). 못 뽑으면 None(시작 그림은 인물 사진·문장만으로 만든다)."""
    try:
        with tempfile.TemporaryDirectory(prefix="vnew-cutframe-") as workdir:
            src = os.path.join(workdir, "rec.mp4")
            out = os.path.join(workdir, "first.jpg")
            with open(src, "wb") as handle:
                handle.write(mp4)
            result = stitcher._run([stitcher.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",  # noqa: SLF001
                                    "-ss", f"{max(0.0, float(start_sec or 0)):.3f}", "-i", src, "-frames:v", "1", "-q:v", "2", out])
            if result.returncode != 0 or not os.path.exists(out) or not os.path.getsize(out):
                return None
            with open(out, "rb") as handle:
                return handle.read()
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_frame_failed err=%s", type(exc).__name__)
        return None


# 작업 132: 컷 단위 Kling 실사 프롬프트 끝 negative 1구절(원문). 이 구절 밖에는 그림 낱말이 없다.
REAL_CUT_NEGATIVE = "no anime, no cartoon, no illustration, keep the same outfit and location as the start frame"
# 작업 135: 컷 시작 그림(Seedream) 고정 구절. 실사 그림체 구절은 문장 끝, 배경 문장은 공간 시트 뷰가 있을 때(Image 번호 = 그 뷰).
REAL_STILL_LOOK = "35mm film photo, natural skin texture, no illustration, no anime, no digital painting"
REAL_STILL_BACKGROUND = ("Background and location must match Image {no} exactly (same buildings, railing, street, time of day); "
                         "only the framing follows Image 1.")
REAL_STILL_BACKGROUND_ONLY = ("Background and location must match Image 1 exactly (same buildings, railing, street, "
                              "time of day).")
REAL_STILL_NO_PHOTO_CLOTHES = "Do not copy the default clothes worn in the cast photos (such as a plain white shirt)."
# 작업 160: 컷 단위 Kling(130 2단계) 이어짐 — test3 take1 컷마다 하늘(낮·밤)·가게·샷 크기가 달랐다.
REAL_STILL_NO_WIDEN = "Do not widen the shot; close-up stays close-up."
# 작업 161: test3 take1 — 앞 컷 시작 그림이 해 질 녘으로 나오자 그 참고가 다음 컷들로 해 질 녘을 옮겼다. 시간대·빛의 기준은 장소 뷰(143)다.
REAL_STILL_PREV = ("Image {no} = the previous shot, for continuity of place and props only; time of day and light follow "
                   "the location image.")
REAL_TIME_OF_DAY = {"day": "Bright daytime, blue sky, no dusk, no night, no sunset light.",
                    "night": "Night, dark night sky, no daylight, no dusk, no sunset light."}
_NIGHT_WORDS = re.compile(r"\b(?:night|night-?time|midnight)\b|밤|심야|자정", re.IGNORECASE)
# 작업 161: 시작 그림 검사(139 _check_cut_still)에 접는 시간대 항목. 기준 글(Image 2 가 이 시간대여야 한다).
REAL_STILL_TOD_REQUIRED = {"day": "Required time of day for Image 2: bright daytime with a blue sky (dusk, blue hour, "
                                  "sunset light or night are wrong).",
                           "night": "Required time of day for Image 2: night with a dark sky (daylight, dusk or sunset "
                                    "light are wrong)."}
REAL_STILL_TOD_RULE = ("- timeOfDayMatch: false if the time of day and light in Image 2 are not the required time of day "
                       "given in the text (Image 1 is not the reference for time of day).")
REAL_STILL_TOD_FIX = {"day": "make it bright daytime with a blue sky, no dusk, no night, no sunset light",
                      "night": "make it night with a dark sky, no daylight, no dusk, no sunset light"}


def _real_tod(plan: dict) -> str:
    """작업 160/161: 실사 시간대 "day"|"night". 콘티(setting·컷 lightEn/locationEn)·대본 setting.timeKo·장소 빛 가운데 밤 낱말이
    있으면 "night", 아니면 "day"(같은 plan 이면 언제나 같은 값)."""
    conti = plan.get("conti") or {}
    setting = (plan.get("script") or {}).get("setting") or {}
    texts = [str(v) for v in (conti.get("setting") or {}).values() if isinstance(v, str)] if isinstance(
        conti.get("setting"), dict) else []
    texts.append(_time_of_day_en(setting.get("timeKo") or ""))
    texts.append(str(setting.get("timeKo") or ""))
    for cut in conti.get("cuts") or []:
        if isinstance(cut, dict):
            texts += [str(cut.get("lightEn") or ""), str(cut.get("locationEn") or ""), str(cut.get("timeKo") or "")]
    for loc in plan.get("locations") or []:
        if isinstance(loc, dict):
            texts.append(str(((loc.get("design") or {}) if isinstance(loc.get("design"), dict) else {}).get("lightEn") or ""))
    return "night" if any(_NIGHT_WORDS.search(t) for t in texts if t) else "day"


def _real_time_sentence(plan: dict) -> str:
    """작업 160: 실사 시작 그림·Kling 프롬프트의 시간대 고정 1문장(_real_tod). 작업 161: 낮 문장을 분명하게
    ("Bright daytime, blue sky, no dusk, no night, no sunset light."), 밤은 같은 꼴의 거울 문장."""
    return REAL_TIME_OF_DAY[_real_tod(plan)]
# 작업 139: 애니 프레임 읽기(animeLayout)가 있을 때 시작 그림 문장. 맨 앞 = 구도 고정, MS 이상의 시트 뷰 문장 = 보이는 배경만.
REAL_STILL_FRAMING = ("Keep Image 1's exact framing and shot size ({shot}): do not zoom out, do not add people, "
                      "do not show more background than Image 1.")
REAL_STILL_BACKGROUND_PLACE = ("Match Image {no}'s place (buildings, railing, light) only where Image 1 shows background; "
                               "framing comes from Image 1.")
SHOT_SIZE_EN = {"ECU": "extreme close-up", "CU": "close-up", "MCU": "medium close-up", "MS": "medium shot",
                "MLS": "medium long shot", "LS": "long shot"}
REAL_STILL_CLOSE = ("ECU", "CU", "MCU")  # 이 샷이면 공간 시트 뷰를 참고로 넣지 않는다(배경은 애니 프레임)
REAL_STILL_TIGHT = ("ECU", "CU")  # 이 샷이면 SHOT·BLOCKING·SETTING 을 160자로(넓은 장소 묘사가 구도를 끌어간다)
REAL_STILL_BRIEF_TIGHT = 160
REAL_STILL_SHOWN = ("face", "partial")  # 정면 사진을 참고로 넣는 visible
# 작업 139: 컷 단위 Kling 실사 프롬프트 고정 구절(머리, 줄이기에서 빠지지 않는다).
REAL_CUT_KEEP_FRAMING = ("Keep the start frame's framing and shot size; the camera does not pull back; "
                         "no new person enters the frame.")
_CEL_WORDS = re.compile(r"\bcel(?:[- ]shad\w*)?\b", re.IGNORECASE)
# 콘티 shotKo(한국어 샷 이름) → 영어. 앞에 있는 긴 이름이 먼저(익스트림 클로즈업 ⊃ 클로즈업).
_SHOT_KO_EN = (
    ("익스트림 클로즈업", "extreme close-up"), ("클로즈업", "close-up"), ("바스트", "medium close-up"),
    ("웨이스트", "medium shot"), ("미디엄", "medium shot"), ("니샷", "medium long shot"), ("풀샷", "full shot"),
    ("롱샷", "long shot"), ("투샷", "two-shot"), ("오버더숄더", "over-the-shoulder shot"), ("인서트", "insert shot"),
    ("설정샷", "establishing shot"), ("부감", "high angle"), ("앙각", "low angle"),
)


def _real_words(text) -> str:
    """실사 문장: 그림 낱말(strip_drawing_words) + cel(셀 음영) 낱말을 지운다(작업 132)."""
    return vnew_fx.strip_drawing_words(_CEL_WORDS.sub(" ", str(text or "")))


# 작업 136: 실사 프롬프트에 넣는 지시서 문장 정리(_real_brief_text). 이 낱말이 든 문장(; 로 나뉜 절 포함)은 통째로 뺀다.
# 낱말만 지우면 "like an background"·"in style" 같은 조각이 남아서다. 그림자는 색이 붙은 것(peach … facial shadows)만.
_SHADOW_COLORS = r"(?:peach|pink|brown|purple|violet|lavender|blue|grey|gray|red|orange|tan|beige|rose)"
REAL_BRIEF_DRAWING_WORDS = re.compile(
    r"\b(?:web-?toons?|manhwa|manga|anime|cartoon\w*|illustrat\w*|drawn|drawing style|art style|sketch\w*|"
    r"ink(?:ed|ing|s)?|screen-?tones?|cel(?:[- ]shad\w*)?|contours?|outlines?|outlined|line-?art|linework|"
    r"line (?:art|work|weight)s?|(?:thin|fine|clean|bold|crisp|ink|pen) lines?|flat[- ]colou?r\w*|flat[- ]shad\w*|"
    r"shading(?! (?:his|her|their|its) )|cross-?hatch\w*|brush ?strokes?|painterly|watercolou?r\w*|facial shadows?|"
    + _SHADOW_COLORS + r"(?:-(?:toned|tinted|colou?red))?(?: (?:and|or) " + _SHADOW_COLORS + r")?"
    r"(?: (?:facial|face|skin|cheek|soft))? shadows?)\b",
    re.IGNORECASE,
)
# 작업 136: 옷 낱말. 이 낱말이 든 구절은 그 인물의 장면 옷 문장으로 바꾸거나(없으면) 뺀다. 머리·얼굴 구절은 남는다.
REAL_BRIEF_CLOTHING_WORDS = re.compile(
    r"\b(?:(?:t-)?shirts?|overshirts?|blouses?|jackets?|blazers?|coats?|overcoats?|collars?|collared|sleeves?|sleeved|"
    r"sleeveless|cuffs?|lapels?|sweaters?|cardigans?|hoodies?|sweatshirts?|jumpers?|vests?|pants|trousers|jeans|slacks|"
    r"skirts?|dress(?:es|ed)?|uniforms?(?! (?:light|lighting|colou?r|spacing|distance|height|pace))|suit|neckties?|"
    r"ties?|scarf|scarves|aprons?|outfits?|clothes|clothing|garments?|attire|shoes|sneakers|boots|loafers)\b",
    re.IGNORECASE,
)
# 옷 구절 경계: 쉼표(뒤 and·with·wearing·while 포함), 또는 and·with·wearing·while.
_BRIEF_PIECES = re.compile(r"(,\s*(?:(?:and|with|wearing|while)\s+)?|\s+(?:and|with|wearing|while)\s+)", re.IGNORECASE)
_BRIEF_DANGLING = re.compile(r"(?:[\s,]+(?:and|with|in|of|a|an|the|his|her|their|its|wearing|while|has|have|is|are))+$",
                             re.IGNORECASE)
# 작업 137/138: 옷 색 낱말(1곳). 장면 옷 인물이 1명 이상인 컷에서 몸 낱말 바로 앞의 이 색("ivory-white shoulder")을 뺀다(_real_brief_text).
REAL_BRIEF_OUTFIT_COLORS = (r"(?:off-white|white|ivory|cream|beige|black|navy|grey|gray|charcoal|blue|red|pink|green|brown|"
                            r"khaki|olive|camel|yellow|purple|maroon|burgundy)")
_BRIEF_BODY_COLOR = re.compile(
    r"\b" + REAL_BRIEF_OUTFIT_COLORS + r"(?:[- ]" + REAL_BRIEF_OUTFIT_COLORS + r")*\s+"
    r"(?=(?:shoulders?|chest|back(?!\s+(?:walls?|doors?|ground|rows?|seats?|rooms?|lights?|lit|alley|yard|stage)\b)|"
    r"arms?|forearms?|upper arms?|collarbones?|torso|neck|waist)\b)",
    re.IGNORECASE,
)
# 작업 137: 장면 옷 문장 정리(_scene_outfit_text) — 괄호 메모, 관사·전치사로 잘린 꼬리("collar open at the").
_OUTFIT_NOTE = re.compile(r"\s*\([^()]*\)\s*")
_OUTFIT_DANGLING = re.compile(r"(?:[\s,;/]+(?:at|of|with|in|on|to|for|from|by|into|onto|over|under|around|near|a|an|the|"
                              r"and|or|his|her|their|its))+$", re.IGNORECASE)


# 작업 146: 패널 읽기 메모 문장("Identified as the blond young man", "speaks politely") — 옷 문장이 아니라서 뺀다.
_OUTFIT_ANALYSIS = re.compile(r"\b(?:identified as|speaks?|speaking)\b", re.IGNORECASE)
# 작업 146: 쓸 만한 장면 옷인지 보는 옷 낱말 = REAL_BRIEF_CLOTHING_WORDS + 윗옷 이름(crew-neck 등). 옷 구절 빼기(_real_brief_text)는 그대로.
_OUTFIT_GARMENT = re.compile(
    REAL_BRIEF_CLOTHING_WORDS.pattern + r"|\b(?:crew-?necks?|turtlenecks?|v-necks?|polos?|tees?|knit(?:wear|s)?|tops?|"
    r"tank-?tops?|hanbok|robes?|gowns?|parkas?|windbreakers?|tuxedos?|tracksuits?|button-?(?:ups?|downs?))\b",
    re.IGNORECASE,
)


def _scene_outfit_text(text, limit: int = 140, *, real: bool = True) -> str:
    """작업 137: 장면 옷 1개 → 실사 문장 조각(끝 마침표 없음). 괄호 메모를 빼고, 실사 낱말 정리·140자 상한(지금과 같다) 뒤
    관사·전치사로 잘린 꼬리를 뺀다. 작업 146: "Identified as …"·"speaks …" 문장도 빼고, 상한(limit)은 통 테이크 줄이기가 정한다.
    작업 160: real=False(애니)면 실사 낱말 정리(_real_words — 그림 낱말 빼기, 실사만)만 건너뛴다. 나머지 정리는 같다."""
    text = _OUTFIT_NOTE.sub(" ", str(text or "")).strip()
    text = " ".join(s for s in re.split(r"(?<=[.!?;])\s+", text) if s and not _OUTFIT_ANALYSIS.search(s))
    text = _clip_words(_real_words(text) if real else " ".join(text.split()), limit).rstrip(" .")
    return _OUTFIT_DANGLING.sub("", text).rstrip(" ,;")


def _scene_outfit_pick(candidates, limit: int = 140) -> str:
    """작업 146: 후보 옷 문장을 앞에서부터 _scene_outfit_text 로 정리해, 2낱말 이상이고 옷 낱말(_OUTFIT_GARMENT)이 든 첫 것.
    없으면 ""("Plain." 같은 잘린 조각은 다음 출처로 넘어간다)."""
    for value in candidates or []:
        text = _scene_outfit_text(value, limit)
        if len(text.split()) >= 2 and _OUTFIT_GARMENT.search(text):
            return text
    return ""


# ---------------------------------------------------------------- 작업 164: 옷 글 검사(조각 "black sleeve" · "shirt cuff)" 막기)
# test3: 162 손 주인 메모("in dark navy shirt cuff")가 콘티 연기 글에서 장면 옷으로 읽혀 모든 테이크 look 줄이 조각이 됐다.
# 쓸 만한 옷 = 3낱말 이상 · 괄호 짝이 맞음 · 부분 낱말(소매·소맷부리·깃·단)이 아닌 옷 낱말 · 앞 명사구가 부분 낱말로 끝나지 않음.
_COSTUME_PART_WORDS = re.compile(r"\b(?:sleeves?|cuffs?|collars?|hems?|hemlines?|lapels?|necklines?|pockets?|buttons?|"
                                 r"zippers?|waistbands?|straps?)\b", re.IGNORECASE)
# 앞 명사구 끝: 쉼표·쌍반점·괄호, 또는 with·over·under·and 같은 이음말 앞.
_COSTUME_HEAD_END = re.compile(r"\s*[,;:(]|\s+(?:with|over|under|and|plus|featuring|showing|that|which|worn|paired|tucked|"
                               r"untucked|rolled|open|half|buttoned|unbuttoned|zipped|at|on|in|to|from|down|around|across|"
                               r"along|near|by)\b", re.IGNORECASE)
_COSTUME_MIN_WORDS = 3
_COSTUME_OPEN_TAIL = re.compile(r"\s*[(\[{][^()\[\]{}]*$")


def _costume_brackets_ok(text: str) -> bool:
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in text:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs and (not stack or stack.pop() != pairs[ch]):
            return False
    return not stack


def _costume_part_only(text: str) -> bool:
    """옷 낱말이 부분 낱말뿐("black sleeve")이거나 앞 명사구가 부분 낱말로 끝나면("dark navy shirt cuff") True."""
    if not _OUTFIT_GARMENT.search(_COSTUME_PART_WORDS.sub(" ", text)):
        return True
    head = _COSTUME_HEAD_END.split(text, 1)[0].split()
    return not head or bool(_COSTUME_PART_WORDS.fullmatch(head[-1].strip(".,;:!?\"'")))


def _costume_valid(text) -> bool:
    """작업 164: 옷 글 1개가 쓸 만한가(3낱말 이상 · 괄호 짝 · 부분 낱말 아닌 옷 낱말 · 앞 명사구 끝이 부분 낱말 아님)."""
    text = " ".join(str(text or "").split()).rstrip(" .")
    return (len(text.split()) >= _COSTUME_MIN_WORDS and _costume_brackets_ok(text)
            and not _costume_part_only(text))


def _costume_candidate(value, limit: int = 140, *, real: bool = True) -> str:
    """작업 164: 옷 후보 1개 → 정리한 옷 글(쓸 수 없으면 ""). 146/137 정리(_scene_outfit_text)가 3낱말 미만을 남기면 정리 전
    후보의 첫 문장을 쓴다(옷 구절을 반으로 자르지 않는다). 고르기는 애니·실사가 같다(검사는 실사 정리 140자로)."""
    raw = " ".join(str(value or "").split())
    # 작업 164 fix1: 패널 읽기는 140자에서 잘려 "… from the shoulder (plain black." 처럼 닫히지 않은 괄호 꼬리가 남는다 — 그 꼬리만 뗀다.
    raw = _COSTUME_OPEN_TAIL.sub("", raw).rstrip(" ,;")
    if not raw:
        return ""
    probe = _scene_outfit_text(raw)
    text = _scene_outfit_text(raw, limit, real=real)
    if len(probe.split()) < _COSTUME_MIN_WORDS or len(text.split()) < _COSTUME_MIN_WORDS:
        first = re.split(r"(?<=[.!?])\s+", raw)[0].rstrip(" .")
        probe = _clip_words(first, 140).rstrip(" .")
        text = _clip_words(vnew_fx.strip_drawing_words(first) if real else first, limit).rstrip(" .")
    return text if _costume_valid(probe) and _costume_valid(text) else ""


def _override_costume(plan: dict, name: str) -> str:
    """사용자 장면 의상 수정(castOverrides[이름].costume, 한국어여도). 없으면 ""."""
    override = ((plan.get("input") or {}).get("castOverrides") or {}).get(str(name or "").strip()) or {}
    return str(override.get("costume") or "").strip() if isinstance(override, dict) else ""


def _base_outfits(actors, char_by_id: dict) -> dict[str, str]:
    """작업 164: {인물 이름: 캐릭터 분석 기본 옷(_char_entries outfitEn = 142 시트 baseOutfitEn)}. 모르면 키 없음."""
    out: dict[str, str] = {}
    for actor in actors or []:
        name = str(actor.get("name") or "").strip()
        outfit = str((char_by_id.get(str(actor.get("charId") or "")) or {}).get("outfitEn") or "").strip()
        if name and outfit and name not in out:
            out[name] = outfit
    return out


def _plan_bases(plan: dict, char_by_id: dict) -> dict[str, str]:
    """작업 164 fix1: 계획의 모든 인물(input.characters 역할명 + 프리비즈 배우) {이름: 캐릭터 기본 옷}. 컷 단위 실사(시작 그림 · Kling ·
    장면 옷 편집)가 쓴다."""
    people = [{"name": str((c or {}).get("roleName") or ""), "charId": str((c or {}).get("charId") or "")}
              for c in (plan.get("input") or {}).get("characters") or [] if isinstance(c, dict)]
    for scene in (plan.get("previz") or {}).get("scenes") or []:
        people += [a for a in (scene or {}).get("actors") or [] if isinstance(a, dict)]
    return _base_outfits(people, char_by_id or {})


def _costume_guard(plan: dict, name: str, costume: str, base: str = "", *, limit: int = 140, real: bool = True,
                   logged: set | None = None) -> str:
    """작업 164: 제출 전 look/COSTUME 줄의 옷 글 검사. 쓸 수 없으면 vnew.gen.costume_invalid 로그(logged 가 None 이면 없음, 이름당 1번)
    후 캐릭터 기본 옷(base, 정리 · 검사 — 그것도 못 쓰면 ""). 빈 값 · 사용자 장면 의상 수정은 그대로."""
    costume = str(costume or "").strip()
    if not costume or _costume_valid(costume) or _override_costume(plan, name):
        return costume
    fixed = _costume_candidate(base, limit, real=real)
    if logged is not None and name not in logged:
        logged.add(name)
        log.warning("vnew.gen.costume_invalid name=%s costume=%r base=%s", str(name)[:40], costume[:80],
                    "used" if fixed else "none")
    return fixed


def _brief_name_forms(name: str) -> list[str]:
    """작업 138: 이름 인식에 쓰는 표기들. 한글 3글자 이상이면 로마자 마지막 두 음절(이름만)도 더한다.
    예: 범건우 → [범건우, Beomgeonu, Beom Geonu, Geonu]."""
    name = str(name or "").strip()
    forms = [name, _romanize_name(name)]
    if len(name) >= 3 and all("\uac00" <= ch <= "\ud7a3" for ch in name):
        given = _romanize_name(name[-2:])
        forms += [_romanize_name(name[:-2]) + " " + given, given]
    return list(dict.fromkeys(f for f in forms if f))


def _brief_name_hits(text: str, aliases: dict[str, list[str]]) -> list[str]:
    """문장 안에 나온 인물 이름(키) — 나온 순서. aliases = {이름: [이름, 로마자]}."""
    hits: list[tuple[int, str]] = []
    for name, forms in aliases.items():
        spots = []
        for form in forms:
            if form.isascii():
                m = re.search(r"(?<![A-Za-z])" + re.escape(form) + r"(?![A-Za-z])", text, re.IGNORECASE)
                if m:
                    spots.append(m.start())
            elif form in text:
                spots.append(text.find(form))
        if spots:
            hits.append((min(spots), name))
    return [name for _pos, name in sorted(hits)]


def _real_brief_text(text, scene_outfits: dict | None = None) -> str:
    """작업 136: 실사 프롬프트에 넣을 지시서 문장. 애니(웹툰) 프롬프트는 쓰지 않는다(지시서 원문은 그대로).

    - 그림·화풍 낱말(REAL_BRIEF_DRAWING_WORDS)이 든 문장은 통째로 뺀다(조각을 남기지 않는다).
    - 옷 낱말(REAL_BRIEF_CLOTHING_WORDS)이 든 구절은 뺀다. 그 구절의 인물(구절 안 이름 → 같은 문장 앞 이름 → 앞 문장의
      주어 → 장면 인물이 1명이면 그 사람)의 장면 옷(scene_outfits[이름])이 있으면 그 자리에 "{이름} wears {옷}." 1번.
      남은 구절이 문장이 못 되면(2낱말 미만) 그 문장도 뺀다. 머리·얼굴 구절은 옷 낱말이 없어 남는다.
    """
    outfits = {str(k).strip(): str(v or "").strip() for k, v in (scene_outfits or {}).items() if str(k or "").strip()}
    aliases = {name: _brief_name_forms(name) for name in outfits}
    only = next(iter(outfits)) if len(outfits) == 1 else ""
    out: list[str] = []
    emitted: set[str] = set()
    subject = ""
    for sentence in re.split(r"(?<=[.!?;])\s+", " ".join(str(text or "").split())):
        if not sentence or REAL_BRIEF_DRAWING_WORDS.search(sentence):
            continue
        named = _brief_name_hits(sentence, aliases)
        if any(outfits.values()):
            # 작업 137/138: "ivory-white shoulder" → "shoulder". 장면 옷 인물이 1명 이상이면 주어와 상관없이.
            sentence = _BRIEF_BODY_COLOR.sub("", sentence)
        if not REAL_BRIEF_CLOTHING_WORDS.search(sentence):
            out.append(sentence)
            subject = named[0] if named else subject
            continue
        end = sentence[-1] if sentence[-1] in ".!?;" else "."
        parts = _BRIEF_PIECES.split(sentence[:-1] if sentence[-1] in ".!?;" else sentence)
        kept: list[str] = []
        owners: list[str] = []
        last = subject
        dropped_after_comma = False
        for i in range(0, len(parts), 2):
            piece = parts[i]
            here = _brief_name_hits(piece, aliases)
            if REAL_BRIEF_CLOTHING_WORDS.search(piece):
                owners.append(here[0] if here else (last or only))
                dropped_after_comma = bool(i) and parts[i - 1].lstrip().startswith(",")
                continue
            if here:
                last = here[-1]
            delim = parts[i - 1] if i and kept else ""
            if dropped_after_comma and delim.strip() == ",":
                delim = " "  # "A, in a coat, faces B" → "A faces B"(쉼표로 끼운 옷 구절이 빠지면 닫는 쉼표도 뺀다)
            dropped_after_comma = False
            kept.append(delim + piece)
        rest = _BRIEF_DANGLING.sub("", "".join(kept).strip(" ,;")).strip(" ,;")
        if len(rest.split()) >= 2:
            out.append(rest[:1].upper() + rest[1:] + end)
        subject = named[0] if named else subject
        for owner in owners:
            costume = _scene_outfit_text(outfits.get(owner))
            if owner and costume and owner not in emitted:
                out.append(f"{owner} wears {costume}.")
                emitted.add(owner)
    joined = " ".join(out)
    return _real_words(joined[:-1] + "." if joined.endswith(";") else joined)


def _outfit_of(item: dict) -> str:
    """콘티 컷·대본 entry 의 characters 항목 1개의 영어 옷 표현(작업 136). 한글이면 쓰지 않는다. 없으면 "".
    작업 164 fix1: 옷 필드(outfitEn·costumeEn·costume)만 본다. positionEn·actionEn·actionKo·blocking 같은 글은 옷 낱말("dark-sleeved",
    "shirt cuff")이 있어도 옷 후보가 아니다(prod test3: positionEn "in panel 1 only his jaw and his dark-sleeved arm …" 이 옷이 됐다)."""
    for field in ("outfitEn", "costumeEn", "costume"):
        value = str(item.get(field) or "").strip()
        if value and not vnew_fx.HANGUL.search(value):
            return value
    return ""


def _scene_outfits(plan: dict, cut_ids, cast_outfits: dict | None = None, *, sources: bool = False) -> dict:
    """작업 136: 컷 인물별 장면 옷 {이름: 영어 옷}. 우선순위 ① 콘티 컷 characters ② 대본 entries(그 컷 panels 와 겹치는 것)
    characters ③ cast_outfits(= 132/135 의 outfits, _resolve_cast: 원작 패널 읽기 + 장면 의상 수정) ④ "".
    작업 146: sources=True 면 {이름: [① 콘티, ② 대본, ③ cast_outfits]}(빈 값 포함, 같은 순서) — _scene_outfit_pick 의 후보.

    cast_outfits 가 None 이면 _resolve_cast(read=False, 캐시만 — 외부 호출 없음). 이름 = 그 컷에 보이는 인물 + 콘티 인물,
    없으면 모든 출처의 이름. 캐릭터 기본 옷(analysis outfitEn, 3D 기본 옷)은 쓰지 않는다.
    """
    ids = [str(c) for c in cut_ids or [] if str(c or "")]
    if cast_outfits is None:
        try:
            cast_outfits = {n: str(v.get("costume") or "")
                            for n, v in _resolve_cast("", "", plan, {"cutIds": ids}, read=False).items()}
        except Exception:  # noqa: BLE001
            cast_outfits = {}
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    entries = [e for e in (plan.get("script") or {}).get("entries") or [] if isinstance(e, dict)]
    tiers: list[dict[str, str]] = [{}, {}]
    names: list[str] = [str(a.get("name") or "") for a in _segment_actors(plan, {"cutIds": ids})]

    def note(tier: dict, item) -> None:
        name = str((item or {}).get("name") or "").strip() if isinstance(item, dict) else ""
        value = _outfit_of(item) if name else ""
        if value and not tier.get(name):
            tier[name] = value

    for cut_id in ids:
        cut = cuts.get(cut_id) or {}
        for ch in cut.get("characters") or []:
            note(tiers[0], ch)
            names.append(str((ch or {}).get("name") or "") if isinstance(ch, dict) else "")
        panels = {int(p) for p in cut.get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool)}
        for entry in entries:
            entry_panels = {int(p) for p in entry.get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool)}
            if panels & entry_panels:
                for ch in entry.get("characters") or []:
                    note(tiers[1], ch)
    names = [n for n in dict.fromkeys(n.strip() for n in names) if n]
    if not names:
        names = list(dict.fromkeys([*tiers[0], *tiers[1], *(str(n) for n in cast_outfits or {})]))
    if sources:
        return {name: [tiers[0].get(name) or "", tiers[1].get(name) or "", str((cast_outfits or {}).get(name) or "").strip()]
                for name in names}
    return {name: tiers[0].get(name) or tiers[1].get(name) or str((cast_outfits or {}).get(name) or "").strip()
            for name in names}


# ---------------------------------------------------------------- 작업 160: 장소마다 인물 옷 1벌(_scene_costume)
# test3: 테이크마다 그 테이크 패널 읽기로 옷을 골라 take1 "track top"·take2 "zip-up track jacket" 이 갈렸다. 장면 옷은 장소 단위로
# 한 번 고른다: ① 사용자 장면 의상 수정(input.castOverrides) ② 그 장소 모든 컷의 콘티 → 대본 옷(컷 순) ③ 그 장소 모든 컷의 패널 읽기
# 가운데 가장 많은 것(같으면 앞 컷) ④ 부른 쪽의 지금 값(fallback). 정리는 146 _scene_outfit_pick/_scene_outfit_text.
# 패널 읽기는 테이크마다 캐시(previz.castNotes[hash])라 컷 → 캐시 키 표(previz.castNotesCuts, 처음 읽은 테이크가 정한다)로 찾는다.


def _cut_location(plan: dict, cut_id) -> str:
    """콘티 컷의 locationEn(없으면 "")."""
    cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == str(cut_id)), {})
    return str(cut.get("locationEn") or "")


def _take_location(plan: dict, take: dict) -> str:
    """테이크(조각) 첫 장소 = 그 컷들 가운데 처음 locationEn 이 있는 컷(_vibe_take_prompt first_loc 과 같다)."""
    return next((loc for loc in (_cut_location(plan, c) for c in _seg_cut_ids(take)) if loc), "")


def _location_cut_ids(plan: dict, location) -> list[str]:
    """그 장소(이름 정규화가 같은) 콘티 컷 id, 콘티 순. 하나도 없으면 모든 컷."""
    from . import locations as loc_mod  # noqa: PLC0415

    want = loc_mod.normalize_name(str(location or ""))
    cuts = [c for c in (plan.get("conti") or {}).get("cuts") or [] if isinstance(c, dict)]
    ids = [str(c.get("id")) for c in cuts if loc_mod.normalize_name(str(c.get("locationEn") or "")) == want]
    return ids or [str(c.get("id")) for c in cuts]


def _cut_cast_readings(plan: dict) -> dict[str, dict[str, str]]:
    """컷마다 패널 읽기 옷 {컷 id: {이름: costumeEn}} (previz.castNotesCuts → castNotes). 표가 없으면 {}."""
    previz = plan.get("previz") or {}
    store = previz.get("castNotes") or {}
    out: dict[str, dict[str, str]] = {}
    for cut_id, key in (previz.get("castNotesCuts") or {}).items():
        notes = store.get(key)
        if isinstance(notes, dict):
            out[str(cut_id)] = {str(n): str((v or {}).get("costumeEn") or "") for n, v in notes.items() if isinstance(v, dict)}
    return out


def _scene_costume(plan: dict, name: str, location, *, fallback: str = "", base: str = "", limit: int = 140,
                   real: bool = True) -> str:
    """작업 160: 그 장소(scene/location)에서 그 인물의 옷 1벌(끝 마침표 없음, 146 정리). 모든 테이크·조각·대체·시작 그림·Kling 이 같은 값.

    사용자 장면 의상 수정(castOverrides, 한국어여도)이 있으면 그것. 없으면 후보 순서: 그 장소 컷들의 콘티 characters 옷 → 대본 entries
    옷(컷 순) → 그 장소 모든 컷의 패널 읽기 옷 가운데 가장 많은 것(같으면 앞 컷) → fallback. 첫 쓸 만한 후보(146 규칙: 실사 정리 뒤
    2낱말 이상·옷 낱말)를 limit 으로 정리한다. 고르기는 애니·실사가 같고, real=False(애니)면 그 후보를 그림 낱말 빼기 없이 정리한다
    (그림 낱말은 실사만 지운다 — lr-e).
    작업 165: 순서 = 콘티 → 대본 → 패널 읽기 다수결(웹툰에 그려진 그 장면 옷) → base(캐릭터 분석 기본 옷, 142 시트) → fallback.
    (164 는 base 를 패널 앞에 두어 장면 옷(검은 트랙 상의) 대신 기본 옷(아이보리 셔츠)이 나왔다.) 쓸 만한 후보 = _costume_candidate
    (3낱말 이상 · 괄호 짝 · 부분 낱말 아닌 옷 낱말, 정리가 3낱말 미만이면 정리 전 첫 문장). 쓸 수 없는 패널 읽기는 표도 없다.
    """
    name = str(name or "").strip()
    if not name:
        return ""
    override = ((plan.get("input") or {}).get("castOverrides") or {}).get(name) or {}
    if isinstance(override, dict) and str(override.get("costume") or "").strip():
        value = str(override.get("costume"))
        return _scene_outfit_text(value, limit, real=real) or _clip_words(value, limit)
    ids = _location_cut_ids(plan, location)
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or [] if isinstance(c, dict)}
    entries = [e for e in (plan.get("script") or {}).get("entries") or [] if isinstance(e, dict)]

    def panels_of(item: dict) -> set[int]:
        return {int(p) for p in item.get("panels") or [] if isinstance(p, (int, float)) and not isinstance(p, bool)}

    conti_values: list[str] = []
    script_values: list[str] = []
    for cut_id in ids:
        cut = cuts.get(cut_id) or {}
        conti_values += [_outfit_of(ch) for ch in cut.get("characters") or []
                         if isinstance(ch, dict) and str(ch.get("name") or "").strip() == name]
        panels = panels_of(cut)
        for entry in entries:
            if panels & panels_of(entry):
                script_values += [_outfit_of(ch) for ch in entry.get("characters") or []
                                  if isinstance(ch, dict) and str(ch.get("name") or "").strip() == name]
    readings = _cut_cast_readings(plan)
    votes: dict[str, list] = {}
    for order, cut_id in enumerate(ids):
        value = (readings.get(cut_id) or {}).get(name)
        text = _costume_candidate(value)
        if text:
            vote = votes.setdefault(text.lower(), [0, order, value])
            vote[0] += 1
    majority = [v[2] for v in sorted(votes.values(), key=lambda v: (-v[0], v[1]))]
    for value in [*conti_values, *script_values, *majority, base, fallback]:
        text = _costume_candidate(value, limit, real=real)
        if text:
            return text
    return ""


def _scene_costume_map(plan: dict, cut_ids, location, fallback: dict | None = None,
                       bases: dict | None = None) -> dict[str, str]:
    """작업 160: 컷 인물(_scene_outfits 의 이름)마다 _scene_costume. _real_brief_text 의 scene_outfits 자리에 쓴다(컷마다 콘티 옷이
    장면 옷을 덮지 않게). fallback = {이름: 지금 값}(없으면 _scene_outfits 값). 작업 164: bases = {이름: 캐릭터 기본 옷}."""
    base = _scene_outfits(plan, cut_ids, fallback)
    fallback = fallback if fallback is not None else base
    names = list(dict.fromkeys([*base, *fallback]))
    return {name: _scene_costume(plan, name, location, fallback=str(fallback.get(name) or base.get(name) or ""),
                                 base=str((bases or {}).get(name) or ""))
            for name in names}


def _read_location_cast(owner: str, pid: str, plan: dict, take: dict) -> None:
    """작업 160: 테이크(조각) 프롬프트 전에 같은 장소 컷이 있는 모든 테이크의 패널 읽기를 채운다(_scene_cast_notes — 캐시면 호출 0).
    그래야 첫 테이크도 장소 전체 패널 읽기로 옷을 고른다. 컷 → 캐시 키 표가 이미 다 있는 테이크는 건너뛴다."""
    from . import locations as loc_mod  # noqa: PLC0415

    def locs(ids) -> set[str]:
        return {loc_mod.normalize_name(_cut_location(plan, c)) for c in ids}

    mine = locs(_seg_cut_ids(take))
    for unit in _take_units(plan):
        ids = _seg_cut_ids(unit)
        mapped = (plan.get("previz") or {}).get("castNotesCuts") or {}
        if not ids or all(c in mapped for c in ids) or not (locs(ids) & mine):
            continue
        try:
            _scene_cast_notes(owner, pid, plan, unit)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.gen.location_cast_failed pid=%s take=%s err=%s", pid, unit.get("id"), _msg(exc))


def _shot_ko_en(shot_ko: str) -> str:
    """콘티 shotKo 를 영어 샷 이름으로(작업 132). 표에 없으면 ""."""
    rest = str(shot_ko or "")
    found: list[str] = []
    for needle, english in _SHOT_KO_EN:
        if needle in rest:
            rest = rest.replace(needle, " ")
            if english not in found:
                found.append(english)
    return ", ".join(found)


def _take_anime_mp4(take: dict) -> bytes | None:
    """같은 gen 그 테이크의 애니(웹툰) 결과 mp4. 완료·키가 없거나 못 받으면 None(작업 132)."""
    state = take.get("webtoon") if isinstance(take.get("webtoon"), dict) else {}
    key = str(state.get("outputKey") or "")
    if state.get("status") != "completed" or not key:
        return None
    try:
        return _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET) or None
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_anime_fetch_failed err=%s", type(exc).__name__)
        return None


def _take_rec_offsets(plan: dict, take: dict) -> dict[str, float]:
    """테이크(녹화 이어 붙인 영상) 안에서 녹화 구간마다 시작 초(작업 132). _take_units 의 길이 규칙과 같다.

    _take_cut_units 의 startSec 은 그 녹화 mp4 안 시각이라, 애니 테이크 결과에서는 앞 녹화 길이만큼 더한다
    (녹화 1개 테이크면 0 이라 startSec 그대로).
    """
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    segs = {str(s.get("id") or ""): s for s in (plan.get("conti") or {}).get("segments") or []}
    offsets: dict[str, float] = {}
    clock = 0.0
    for seg_id in (str(s) for s in take.get("recSegIds") or []):
        offsets[seg_id] = round(clock, 3)
        rec = recordings.get(seg_id) or {}
        clock += float(rec.get("seconds") or 0) or float((segs.get(seg_id) or {}).get("seconds") or 0)
    return offsets


def _cut_view_side(plan: dict, cut_id: str) -> str:
    """작업 135: 그 컷 카메라가 보는 쪽 공간 시트 뷰. 프리비즈 timeline 카메라(pos→target)가 주로 +z(south 벽)를 보면 "front",
    그 밖(north·옆 벽, 카메라 없음)은 "back"(뒤 보기 = 왼쪽·뒤·오른쪽). 좌표는 previz_schema 와 같다(north 벽 z = -depth/2)."""
    scene = _scene_by_cut(plan).get(str(cut_id)) or {}
    entry = next((e for e in scene.get("timeline") or [] if str(e.get("cutId") or "") == str(cut_id)), {})
    camera = entry.get("camera") if isinstance(entry.get("camera"), dict) else {}
    try:
        pos, target = [float(v) for v in camera["pos"][:3]], [float(v) for v in camera["target"][:3]]
    except (KeyError, TypeError, ValueError, IndexError):
        return "back"
    if len(pos) < 3 or len(target) < 3:
        return "back"
    dx, dz = target[0] - pos[0], target[2] - pos[2]
    return "front" if dz > abs(dx) else "back"


def _cut_location_view(plan: dict, cut_id: str) -> tuple[bytes, str] | None:
    """작업 135: 그 컷 장소 공간 시트의 실사 뷰 이미지 (jpeg, "back"|"front"). 없으면 None(시작 그림·QA 는 지금과 같다).

    장소 = 콘티 cut.locationEn(_location_by_name), 못 찾으면 프리비즈 scene.locationId 와 같은 id 의 location.
    뷰 = 카메라가 보는 쪽(_cut_view_side), 그 키가 없으면 다른 쪽. locations real variant viewKeys 를 읽기만 한다.
    """
    conti_cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == str(cut_id)), {})
    loc = _location_by_name(plan, str(conti_cut.get("locationEn") or "")) if conti_cut.get("locationEn") else None
    if loc is None:
        loc_id = str((_scene_by_cut(plan).get(str(cut_id)) or {}).get("locationId") or "")
        loc = next((l for l in plan.get("locations") or [] if loc_id and str(l.get("id") or "") == loc_id), None)
    if not loc:
        return None
    keys = (((loc.get("variants") or {}).get("real") or {}).get("viewKeys")) or {}
    side = _cut_view_side(plan, cut_id)
    for view in (side, "front" if side == "back" else "back"):
        key = str(keys.get(view) or "")
        jpeg = _load_jpeg(key) if key else None
        if jpeg:
            return jpeg, view
    return None


def _layout_listed(layout: dict | None) -> list[tuple[str, str, str]]:
    """작업 139: 애니 프레임 읽기에서 화면에 있는 인물 [(이름, side, visible)] — visible face·partial·back, 왼쪽 → 오른쪽."""
    order = {"left": 0, "center": 1, "right": 2}
    rows = [(str(p.get("name") or ""), str(p.get("side") or "center"), str(p.get("visible") or "none"))
            for p in (layout or {}).get("people") or [] if isinstance(p, dict)]
    rows = [r for r in rows if r[0] and r[2] in (*REAL_STILL_SHOWN, "back")]
    return sorted(rows, key=lambda r: order.get(r[1], 1))


def _layout_people_sentence(listed: list[tuple[str, str, str]]) -> str:
    """작업 139: "Exactly N person(s) in frame: 이름 (side)…; no other people." 좌우는 애니 프레임 기준. back 은 사진 없이 문장만."""
    if not listed:
        return "No people in frame; do not add any person."
    who = "; ".join(f"{name} ({side}{', seen from behind, face not visible' if visible == 'back' else ''})"
                    for name, side, visible in listed)
    count = len(listed)
    return f"Exactly {count} {'person' if count == 1 else 'people'} in frame: {who}; no other people."


def _drop_named(text: str, names) -> str:
    """작업 139: 문장(; 로 나뉜 절 포함) 가운데 names(한글·로마자 표기, _brief_name_forms)가 든 것을 뺀다. 화면에 없는 인물 이름 빼기."""
    aliases = {n: _brief_name_forms(n) for n in names if str(n or "").strip()}
    if not aliases or not text:
        return text
    kept = [s for s in re.split(r"(?<=[.!?;])\s+", str(text).strip()) if s and not _brief_name_hits(s, aliases)]
    joined = " ".join(kept)
    return joined[:-1] + "." if joined.endswith(";") else joined


def _cut_still_prompt(plan: dict, cut: dict, people: list[tuple[str, str, int]], has_frame: bool, *,
                      source: str = "previz", outfits: dict | None = None, background_no: int = 0,
                      layout: dict | None = None, fix: str = "", sheets: dict | None = None,
                      costumes: dict | None = None, prev_no: int = 0, bases: dict | None = None) -> str:
    """컷 시작 그림(Seedream) 문장: 장면·인물 위치·카메라 + photoreal. people = [(색, 이름, 이미지 번호)].

    작업 132: source="anime"(Image 1 = 같은 gen 애니 테이크 결과의 그 컷 첫 프레임)이면 Image 1 의 구도·카메라·인물 위치·
    옷·배경을 그대로 photoreal live-action 으로 다시 그리고 얼굴만 Image 2… 인물로 한다. 그 밖(프리비즈 첫 프레임·프레임 없음)은
    마네킹 문장 + 장소(배경 문장)를 넣는다. 그림 낱말·cel 은 고정 구절(REAL_STILL_LOOK) 밖에 넣지 않는다.
    작업 135: background_no(공간 시트 실사 뷰의 이미지 번호, 0 = 없음)면 배경은 그 그림을 따른다(REAL_STILL_BACKGROUND).
    대본·콘티의 옷(outfits = {이름: 장면 의상})과 사진 옷을 따르지 말라는 구절은 언제나, 끝은 REAL_STILL_LOOK.
    작업 139: layout(애니 프레임 읽기, source="anime" 일 때만)이 있으면 맨 앞 = 구도 고정(REAL_STILL_FRAMING), 인물 문장
    "Exactly N …"(back 은 문장만), 화면에 없는 인물 이름은 문장에서 뺀다. 시트 뷰가 있으면 REAL_STILL_BACKGROUND_PLACE, 없으면
    짧은 LOCATION 1구절. ECU·CU 면 SHOT·BLOCKING·SETTING 160자. fix(시작 그림 검사 교정 문장)는 끝 구절 앞. layout 이 없고
    fix 가 없으면 138 과 글자 하나 다르지 않다.
    작업 142: sheets = {이름: 정면 번호(0 = 없음)} — 그 인물의 people 번호는 AI 캐릭터 시트다. FACES/CAST 바로 뒤에 인물마다
    _real_sheet_sentences 1줄. sheets 가 없으면 140 과 같다. 옷은 OUTFITS·REAL_STILL_NO_PHOTO_CLOTHES 가 그대로 정한다.
    작업 160: OUTFITS 옷 = 그 컷 장소의 _scene_costume(모든 컷·테이크 같은 1벌). costumes = {이름: 장면 옷 편집 그림 번호} — 시트 문장이
    "clothes come from Image N", 시트 없는 인물은 이름표 1문장. prev_no = 앞 컷 시작 그림 번호(REAL_STILL_PREV). 늘 시간대 1문장
    (_real_time_sentence)·"Do not widen the shot"(REAL_STILL_NO_WIDEN, 구도 줄 바로 뒤).
    """
    sheets = {str(k): int(v or 0) for k, v in (sheets or {}).items()}
    costumes = {str(k): int(v or 0) for k, v in (costumes or {}).items() if v}
    sheet_lines = [_real_sheet_sentences(name, no, sheets[name] or None, costume=bool(sheets[name])
                                         and costumes.get(name) == sheets[name])
                   for _color, name, no in people if name in sheets]
    sheet_lines += [REAL_COSTUME_REF_SENTENCE.format(img=f"Image {costumes[name]}", name=name)
                    for _color, name, _no in people if name in costumes and name not in sheets]
    cut_id = str(cut.get("cutId") or "")
    brief = _cut_brief(plan, cut_id)
    conti_cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == cut_id), {})
    anime = has_frame and source == "anime"
    layout = layout if anime and isinstance(layout, dict) and layout.get("shotSize") else None
    shot = str((layout or {}).get("shotSize") or "")
    listed = _layout_listed(layout)
    hidden: set[str] = set()
    if layout:
        known = {str(p.get("name") or "") for p in layout.get("people") or [] if isinstance(p, dict)}
        known |= {str(a.get("name") or "") for a in _segment_actors(plan, {"cutIds": [cut_id]})}
        known |= {str(ch.get("name") or "") for ch in conti_cut.get("characters") or [] if isinstance(ch, dict)}
        hidden = {n for n in known if n.strip()} - {name for name, _side, _visible in listed}
    parts = [REAL_REF_AI_NOTE]  # 작업 140: 첫 줄
    if layout:
        parts.append(REAL_STILL_FRAMING.format(shot=SHOT_SIZE_EN.get(shot, shot)))
    parts.append("Photoreal live-action film still, 16:9, a single frame of a Korean drama.")
    parts.append(REAL_STILL_NO_WIDEN)  # 작업 160: 139 샷 크기 유지(넓히지 않는다)
    if anime:
        parts.append("Image 1 is this exact shot: redraw Image 1 as a photoreal live-action frame with the same composition, "
                     "camera angle, framing, lens, character positions and poses, "
                     + ("and outfits. " if background_no > 1 else "outfits and background. ")
                     + "Change only the rendering, not the content.")
        if people:
            parts.append("FACES: " + "; ".join(
                f"{name} in Image 1 has the face and hair of the person in Image {no}" if name in sheets else
                f"{name} in Image 1 has the face and hair of the real person in Image {no}"
                for _color, name, no in people) + ". Take only faces from those photos; clothing comes from Image 1.")
            parts.extend(sheet_lines)
        if layout:
            parts.append(_layout_people_sentence(listed))
    else:
        if has_frame:
            parts.append("Image 1 is a rough 3D previz frame: keep its camera angle, framing, lens, positions and poses exactly; "
                         "replace every mannequin with a real person, no mannequins remain.")
        if people:
            parts.append("CAST: " + "; ".join(
                f"the {color} mannequin is {name}, the person in Image {no}" if name in sheets else
                f"the {color} mannequin is {name}, the real person in Image {no}"
                for color, name, no in people) + ".")
            parts.extend(sheet_lines)
    if background_no:
        if layout and background_no > 1:
            parts.append(REAL_STILL_BACKGROUND_PLACE.format(no=background_no))
        else:
            parts.append(REAL_STILL_BACKGROUND.format(no=background_no) if has_frame and background_no > 1
                         else REAL_STILL_BACKGROUND_ONLY)
    if prev_no:
        parts.append(REAL_STILL_PREV.format(no=prev_no))  # 작업 160/161: 앞 컷 시작 그림 = 장소·소품 이어짐만(시간대·빛은 장소 뷰)
    parts.append(_real_time_sentence(plan))  # 작업 160: 시간대 고정(161: 낮 문장 분명하게)
    parts.append(REAL_NO_BRAND_SENTENCE)  # 작업 161: 실제 상표·로고·읽히는 간판 글자 없음
    worn = []
    # 작업 136: 장면 옷 = 콘티 → 대본 → outfits(웹툰 패널 읽기) 순(_scene_outfits). 지시서 문장은 _real_brief_text 를 지난다.
    # 작업 160: 그 컷 장소의 장면 옷 1벌(_scene_costume_map — 수정 → 장소 콘티·대본 → 장소 패널 읽기 다수 → outfits).
    scene_outfits = _scene_costume_map(plan, [cut_id], _cut_location(plan, cut_id), dict(outfits or {}), bases)
    worn_names = [name for _color, name, _no in people]
    if layout:  # 작업 139: 뒷모습 인물도 옷은 보인다
        worn_names += [name for name, _side, visible in listed if visible == "back" and name not in worn_names]
    for name in worn_names:
        # 작업 164 fix1: 장면 옷(기본 옷 포함)이 없을 때의 패널 읽기 값도 옷 글 검사(_costume_candidate)를 지나야 한다.
        costume = _scene_outfit_text(scene_outfits.get(name)) or _costume_candidate((outfits or {}).get(name))
        if costume:
            worn.append(f"{name} wears {costume}")
    if worn:
        parts.append("OUTFITS (from the script, not the photos): " + "; ".join(worn) + ".")
    parts.append(REAL_STILL_NO_PHOTO_CLOTHES)
    if not anime:
        parts.append("LOCATION: " + _background_en(plan, str(conti_cut.get("locationEn") or ""), real=True) + ".")
    elif layout and not background_no:
        place = _clip_words(_real_words(conti_cut.get("locationEn")), 80).rstrip(" .")
        if place:
            parts.append("LOCATION: " + place + ".")
    tight = REAL_STILL_BRIEF_TIGHT if shot in REAL_STILL_TIGHT else 0
    for label, text in (("SHOT", brief.get("shotEn")), ("BLOCKING", brief.get("blockingEn"))):
        cleaned = _real_brief_text(text, scene_outfits)
        if layout:
            cleaned = _drop_named(cleaned, hidden)
        if tight:
            cleaned = _clip_words(cleaned, tight)
        if cleaned:
            parts.append(f"{label}: {cleaned}")
    if layout:
        scene = _scene_summary(_drop_named(_real_brief_text(_scene_brief_text(plan, cut).strip(), scene_outfits), hidden),
                               tight or 300)
    else:
        scene = _scene_summary(_real_brief_text(_scene_brief_text(plan, cut).strip(), scene_outfits), 300)
    if scene:
        parts.append("SETTING: " + scene)
    if anime:
        parts.append("Natural skin texture, real fabric, realistic lighting and shadows. No outline strokes, no flat-color "
                     "shading. No text, no subtitles, no speech bubbles.")
    else:
        parts.append("Natural skin texture, realistic lighting and shadows, photoreal. No text, no subtitles, no speech bubbles.")
    if fix:
        parts.append(fix.strip())
    parts.append(REAL_STILL_LOOK + ".")  # 작업 135: 실사 그림체 고정 구절(끝)
    return "\n".join(parts)


def _charge_cut_still(owner: str, pid: str, gen_id: str, key: str) -> None:
    """컷 시작 그림 1장(Seedream) 계상. 금액은 지금 공급자 장당 금액(vpoc_seedream.unit_usd)."""
    try:
        vpoc_ledger.charge(key, kind="image", model=vnew_settings.SEEDREAM_MODEL, units=1, usd=float(vpoc_seedream.unit_usd()),
                           user=owner, project_id=pid, plan_id=gen_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_cut_still_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_kling_cut(owner: str, pid: str, gen_id: str, token: str, endpoint: str, seconds: int) -> None:
    """Kling 컷 제출 1회 계상(보낸 길이 × 그 모델 초당 단가). 키 = 작업 토큰."""
    try:
        vpoc_ledger.charge(token, kind="video", model=endpoint, units=float(seconds),
                           usd=round(_previz_model_unit_price(endpoint) * float(seconds), 4), user=owner, project_id=pid,
                           plan_id=gen_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_kling_cut_failed pid=%s err=%s", pid, str(exc)[:200])


def _keep_cut_still(pid: str, out_prefix: str, still: bytes) -> str:
    """작업 131: 컷 시작 그림을 {컷 결과 접두사}/start.jpg 로 남긴다(영상 버킷). 반환 키, 실패면 ""(경고 1줄 — 제출은 막지 않는다)."""
    key = f"{str(out_prefix or '').rstrip('/')}/start.jpg"
    try:
        STORE.put_bytes(key, still, "image/jpeg", bucket=vpoc_settings.VIDEO_BUCKET)
        return key
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_still_keep_failed pid=%s err=%s", pid, type(exc).__name__)
        return ""


def _log_kling_cut_result(owner: str, pid: str, gen_id: str, take_id: str, cut: dict) -> None:
    """작업 131: 조회가 끝난 Kling 컷의 기록(칸 "real:테이크id#컷id", 같은 invocationArn)을 completed·failed 로. 예외를 내지 않는다."""
    status = str((cut or {}).get("status") or "")
    arn = str((cut or {}).get("invocationArn") or "")
    if not vnew_settings.VNEW_EDIT_RETAKE or not arn or status not in ("completed", "failed"):
        return
    try:
        from . import retake as vnew_retake  # noqa: PLC0415 — retake 가 generate 를 import 한다(순환 피함).

        vnew_retake.log_result(owner, pid, gen_id, f"real:{take_id}#{cut.get('cutId') or ''}", arn, status,
                               output_key=str(cut.get("outputKey") or ""), error=str(cut.get("error") or ""))
    except Exception as exc:  # noqa: BLE001 — 기록은 영상 생성을 막지 않는다.
        log.warning("vnew.gen.prompt_log_failed pid=%s cell=%s stage=result err=%s", pid, take_id, type(exc).__name__)


def _kling_cut_record(gen: dict, variant: str, cell_id) -> tuple[int, dict, dict] | None:
    """작업 131: ⑦ 컷 단위 Kling 실사 재료의 칸 "테이크id#컷id"(edit._material_items) → (테이크 index, 테이크 칸, 컷 기록 fallback.cuts 한 줄).
    실사·통 테이크이고 그 테이크 칸에 그 컷 기록이 있을 때만, 아니면 None."""
    text = str(cell_id or "")
    gen = gen if isinstance(gen, dict) else {}
    if variant != "real" or str(gen.get("mode") or "") != "previz-take" or "#" not in text:
        return None
    take_id, _, cut_id = text.partition("#")
    for index, take in enumerate(_previz_cells(gen)):
        if not isinstance(take, dict) or str(take.get("id") or "") != take_id:
            continue
        state = take.get("real") if isinstance(take.get("real"), dict) else {}
        fallback = state.get("fallback") if isinstance(state.get("fallback"), dict) else {}
        record = next((c for c in fallback.get("cuts") or [] if isinstance(c, dict) and str(c.get("cutId") or "") == cut_id), None)
        return (index, take, record) if record is not None else None
    return None


def _kling_cut_model_of(take: dict) -> str:
    """작업 131: 그 테이크 컷 단위 Kling 단계에 쓴 모델(칸 fallback.model, 없으면 지금 설정)."""
    state = (take or {}).get("real") if isinstance((take or {}).get("real"), dict) else {}
    fallback = state.get("fallback") if isinstance(state.get("fallback"), dict) else {}
    return str(fallback.get("model") or vnew_settings.VNEW_TAKE_REAL_KLING_MODEL or "")


def _escalate_likeness(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """likeness 거절 → 다음 단계로 같은 칸을 queued(0→1, 1→2). 2단계 모델이 없거나 이미 마지막이면 시도한 단계를 적고 실패."""
    kling = _kling_cut_model()
    box: dict = {}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, "real", claim):
            raise _Discard()
        state = _previz_cells(g)[index]["real"]
        step = _fallback_step(state)
        tried = list((state.get("fallback") or {}).get("tried") or []) + [REAL_FALLBACK_TRIED.get(step, "")]
        nxt = 1 if step == 0 else (2 if step == 1 and kling else 0)
        state.pop("pendingCharge", None)
        box.update(step=step, next=nxt, tried=tried)
        if not nxt:
            return
        model = kling[0] if nxt == 2 else _real_take_model(g)  # 작업 156: 1단계 = 실사 테이크 모델(정면 1장)
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")
        state["fallback"] = {"step": nxt, "reason": "likeness", "model": model, "tried": tried}
        if isinstance(g.get("realRun"), dict):
            g["realRun"]["realFallback"] = {"step": nxt, "reason": "likeness", "model": model}

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    log.info("vnew.gen.real_fallback pid=%s take=%d from=%d to=%d", pid, index, box.get("step", 0), box.get("next", 0))
    if not box.get("next"):
        message = REAL_FALLBACK_FAIL.format(steps=" → ".join(box.get("tried") or []))
        if box.get("step") == 1 and not kling:
            message += REAL_FALLBACK_NO_KLING
        _fail_variant(owner, pid, gen_id, index, "real", claim, message, kind="likeness")


# ---------------------------------------------------------------- 작업 169: 152 조각 실사 테이크의 조각 단위 likeness 사다리
#
# 조각 테이크(state.parts)에서 조각 N 이 likeness 로 거절되면 끝난 조각은 그대로 두고 그 조각만 다음 단계로 같은 칸을 queued.
# 0b = 같은 조각 Seedance 다시(LIKENESS_PART_RETRIES 번, 요청 seed 만 바꿈 — caps.seedField 가 있을 때) → 1 = 인물당 정면 1장(130 1단계,
# 그 조각만 1번) → 2 = 그 조각의 컷마다 Kling(_kling_part_items, 조각 기록은 superseded·replacedBy "kling-cuts"). 뒤 조각은 다시
# 0단계 Seedance 이고 155 이음 프레임 = 마지막 Kling 컷 결과의 마지막 프레임. 조각 기록: likenessStep(0·1·2)·likenessRetry(0b 횟수)·
# likenessExtra(추가 제출 수, 상한 LIKENESS_PART_MAX_EXTRA = 0b 2 + 1단계 1 + 2단계 1). 칸 attempt 는 드라이버가 선점마다 올린다
# (Kling 컷은 컷마다 1번 선점·제출). Kling 컷이 또 거절이면 칸 실패(되돌아가지 않는다). 통 테이크(조각 없음)는 _escalate_likeness 그대로.
LIKENESS_PART_RETRIES = 2
LIKENESS_PART_MAX_EXTRA = 4
LIKENESS_PART_FIELDS = ("likenessStep", "likenessRetry", "likenessExtra", "seed")
PART_SUPERSEDED = "superseded"
KLING_CUTS_REPLACED = "kling-cuts"
REAL_PART_FALLBACK_TRIED = ("Seedance", "Seedance 다시", "Seedance 정면 1장", "컷 단위 Kling")


def _part_likeness(part) -> tuple[int, int, int]:
    """조각 기록의 (likenessStep, likenessRetry, likenessExtra). 없으면 (0, 0, 0)."""
    part = part if isinstance(part, dict) else {}
    out = []
    for field in ("likenessStep", "likenessRetry", "likenessExtra"):
        try:
            out.append(max(0, int(part.get(field) or 0)))
        except (TypeError, ValueError):
            out.append(0)
    return out[0], out[1], out[2]


def _used_parts(parts) -> list[dict]:
    """잇는 데 쓰는 조각 기록(superseded 뺌). superseded 가 없으면 그대로."""
    return [p for p in parts or [] if isinstance(p, dict) and p.get("status") != PART_SUPERSEDED]


def _supersede_unused(state: dict, used_sigs: set, replaced_by: str = "") -> None:
    """칸이 끝날 때: 잇는 데 쓰지 않은 조각(sig 가 used_sigs 에 없거나 completed 가 아님)을 superseded 로. pending·failed 가 남지 않는다."""
    for part in (state or {}).get("parts") or []:
        if not isinstance(part, dict) or part.get("status") == PART_SUPERSEDED:
            continue
        if _json_key(part.get("sig")) in used_sigs and part.get("status") == "completed":
            continue
        part["status"] = PART_SUPERSEDED
        for field in ("invocationArn", "error", "failureKind"):
            part.pop(field, None)
        if replaced_by:
            part["replacedBy"] = replaced_by


def _kling_part_sig(item: dict) -> list:
    """likeness 2단계 조각의 Kling 컷 하나를 가르는 값(재시도 때 끝난 컷을 다시 내지 않는다)."""
    return ["kling", str(item.get("cutId") or ""), round(float(item.get("start") or 0), 3), round(float(item.get("end") or 0), 3)]


def _kling_part_items(plan: dict, take: dict, part: dict) -> list[dict]:
    """조각의 컷마다 Kling 항목 {kind, no, fromPart, cutId, cutIds, start, end, seconds}(테이크 시각 = 컷 ∩ 조각 구간)."""
    spans = _take_cut_spans(plan, take)
    items = []
    for cut_id in [str(c) for c in part.get("cutIds") or []]:
        span = spans.get(cut_id)
        if not span:
            continue
        start = max(float(span["start"]), float(part.get("start") or 0))
        end = min(float(span["end"]), float(part.get("end") or 0))
        if end - start <= 0.001:
            continue
        items.append({"kind": "kling", "no": part.get("no"), "fromPart": part.get("no"), "cutId": cut_id, "cutIds": [cut_id],
                      "start": round(start, 3), "end": round(end, 3), "seconds": round(end - start, 3)})
    return items


def _kling_part_ctx(owner: str, pid: str, gen_id: str, index: int, claim: str, plan: dict, take: dict) -> dict:
    """조각 Kling 컷 재료(_real_cut_ctx + 테이크 id·index·하트비트). 2단계 모델이 없으면 오류(사다리가 2단계로 보내지 않는다)."""
    model = _kling_cut_model()
    if not model:
        raise VpocError("vnew.generate", "KlingMissing", "컷 단위 Kling 모델이 fal 등록표에 없습니다.", 409)
    ctx = _real_cut_ctx(owner, pid, plan, take, *model)
    ctx["takeId"], ctx["index"] = str(take.get("id") or index), index

    def beat() -> None:
        if not _touch_prepare_claim(owner, pid, gen_id, index, "real", claim):
            raise _Discard()

    ctx["beat"] = beat
    return ctx


def _submit_kling_part_cut(owner: str, pid: str, gen_id: str, plan: dict, ctx: dict, take: dict, item: dict, sig: list,
                           before: dict | None) -> dict:
    """조각 Kling 컷 하나 제출. 시작 그림 = 앞 조각·앞 컷 결과의 마지막 프레임(있을 때), 없으면 139 시작 그림(Seedream)."""
    cut_id = str(item.get("cutId") or "")
    unit = next((u for u in _take_cut_units(plan, take) if str(u.get("cutId") or "") == cut_id), None)
    if unit is None:
        raise VpocError("vnew.generate", "NoCuts", "이 테이크의 컷을 찾지 못했습니다.", 409)
    span = _take_cut_spans(plan, take).get(cut_id) or {"start": item.get("start") or 0}
    shift = max(0.0, float(item.get("start") or 0) - float(span.get("start") or 0))
    cut = {**unit, "startSec": float(unit.get("startSec") or 0) + shift, "seconds": float(item.get("seconds") or 0)}
    start, start_key = None, ""
    if before and before.get("status") == "completed" and before.get("outputKey"):
        start = _video_last_frame(str(before["outputKey"]), float(before.get("seconds") or 0))
        start_key = str(before["outputKey"]) + "#last" if start else ""
    record = _submit_real_cut(owner, pid, gen_id, plan, ctx, cut, start=start, start_key=start_key)
    del start
    return {**item, **record, "sig": sig, "kind": "kling", "cutIds": [cut_id], "seconds": float(item.get("seconds") or 0)}


def _escalate_likeness_parts(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """조각 테이크 likeness 거절 → 거절된 조각만 다음 단계(0b → 1 → 2)로 칸 queued. Kling 컷 거절·단계 끝·상한이면 칸 실패."""
    kling = _kling_cut_model()
    box: dict = {"moves": []}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, "real", claim):
            raise _Discard()
        take = _previz_cells(g)[index]
        state = take["real"]
        box["take"] = str(take.get("id") or index)
        rejected = [p for p in state.get("parts") or [] if isinstance(p, dict) and p.get("status") == "failed"
                    and _likeness_rejected(p.get("error"))]
        moves = []
        for part in rejected:
            step, retry, extra = _part_likeness(part)
            tried = list(REAL_PART_FALLBACK_TRIED[:1 + (retry > 0) + (step >= 1) + (step >= 2)])
            if part.get("kind") == "kling":
                tried = list(REAL_PART_FALLBACK_TRIED)
                nxt = None
            elif step == 0 and retry < LIKENESS_PART_RETRIES:
                nxt = (0, retry + 1)
            elif step == 0:
                nxt = (1, retry)
            elif step == 1 and kling:
                nxt = (2, retry)
            else:
                nxt = None
            if nxt is None or extra + 1 > LIKENESS_PART_MAX_EXTRA:
                box.update(fail=True, no=part.get("fromPart") or part.get("no"), step=step, tried=tried,
                           noKling=step == 1 and not kling)
                return
            moves.append((part, nxt, extra + 1))
        if not moves:
            box["fail"] = True
            box["tried"] = [REAL_PART_FALLBACK_TRIED[0]]
            return
        for part, (step, retry), extra in moves:
            part.update(likenessStep=step, likenessRetry=retry, likenessExtra=extra)
            if step == 0:
                part["seed"] = secrets.randbelow(2 ** 31 - 1) + 1  # 0b: 요청 seed 만 바꾼다
            else:
                part.pop("seed", None)
            if step == 2:
                part["klingModel"] = kling[0]
            box["moves"].append((part.get("no"), "0b" if step == 0 else str(step), retry, extra))
        state.pop("pendingCharge", None)
        state.update(status="queued", invocationArn="", outputKey="", error="", claim="", claimedAt="")

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return
    if box.get("fail"):
        log.info("vnew.gen.likeness_part take=%s part=%s step=failed pid=%s from=%s", box.get("take"), box.get("no"), pid,
                 box.get("step", 0))
        message = REAL_FALLBACK_FAIL.format(steps=" → ".join(box.get("tried") or []))
        if box.get("noKling"):
            message += REAL_FALLBACK_NO_KLING
        _fail_variant(owner, pid, gen_id, index, "real", claim, message, kind="likeness")
        return
    for no, step, retry, extra in box["moves"]:
        log.info("vnew.gen.likeness_part take=%s part=%s step=%s pid=%s retry=%d extra=%d", box.get("take"), no, step, pid,
                 retry, extra)


# 작업 169: 컷 단위 Kling 프롬프트의 샷 고정(Seedance SHOT 줄과 같은 166 크기·인서트·클로즈업 주인공·자기 손, 162 손 주인 글,
# 160/165 장면 옷). 머리(고지 다음 첫 줄)라 줄이기에서 빠지지 않는다. 시작 그림이 앞 샷 마지막 프레임이면 139 구도 유지 대신 이 줄.
REAL_CUT_FRAMING_LINE = "FRAMING (storyboard, keep exactly; this decides the shot size and who is in frame) - {size}: {lead}"
REAL_CUT_COSTUME_LINE = "COSTUME: {locks}."
REAL_CUT_FROM_PREV = ("The first frame is the previous shot's last frame: keep its place, light, people and outfits, and "
                      "move to the FRAMING above right away.")


def _kling_shot_lock(owner: str, plan: dict, ctx: dict, cut_id: str, seconds: float) -> str:
    """Kling 컷 1개의 샷 고정 줄(FRAMING + COSTUME). _vibe_take_prompt(실사, 그 컷만)의 SHOT 앞부분과 같은 글. 못 만들면 ""."""
    take = ctx.get("take") if isinstance(ctx.get("take"), dict) else {"id": ctx.get("takeId"), "cutIds": [cut_id]}
    try:
        sub = _part_take(plan, take, {"cutIds": [cut_id], "seconds": float(seconds or 0)})
        overrides = ctx.get("overrides") or {}
        char_by_id = ctx.get("charById") or {}
        outfits = ctx.get("outfits") or {}
        cast = []
        for actor in _segment_actors(plan, sub):
            name = str(actor.get("name") or "").strip()
            if not name:
                continue
            pick = overrides.get(name)
            char_id = str(pick["charId"]) if pick else str(actor.get("charId") or "")
            look = pick["look"] if pick else "real"
            char = _cast_entry(owner, char_id, name) if pick else char_by_id.get(char_id)
            if not char:
                appearance = ""
            elif look == "anime":
                appearance = _real_appearance(str(char.get("appearanceEn") or ""))
            else:
                appearance = _real_appearance(_real_look(owner, char_id))
            # 이름은 Kling ACTION·DIALOGUE 와 같은 콘티 이름(nameEn = 그 이름)
            cast.append({"name": name, "nameEn": name, "colorName": actor.get("colorName") or "grey", "look": look,
                         "appearance": appearance, "costume": vnew_fx.strip_drawing_words(outfits.get(name) or ""),
                         "refNos": []})
        framing: dict = {}
        _vibe_take_prompt(plan, sub, real=True, cast=cast, quiet=True, bases=ctx.get("bases"), framing=framing)
        shot = (framing.get("shots") or {}).get(cut_id)
        if not shot:
            return ""
        lead = str(shot["lead"]).strip().rstrip(" .")
        lines = [REAL_CUT_FRAMING_LINE.format(size=shot["size"], lead=lead) if lead
                 else REAL_CUT_FRAMING_LINE.split(": {lead}", 1)[0].format(size=shot["size"]) + "."]
        costumes = framing.get("costumes") or {}
        locks = [_costume_lock(name, str(costumes.get(name) or "")) for name in shot.get("names") or []]
        locks = [lock for lock in locks if lock]
        if locks:
            lines.append(REAL_CUT_COSTUME_LINE.format(locks="; ".join(locks)))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — 고정 줄이 없어도 지금 프롬프트로 낸다
        log.warning("vnew.gen.kling_shot_lock_failed take=%s cut=%s err=%s", ctx.get("takeId"), cut_id, _msg(exc))
        return ""


def _real_cut_ctx(owner: str, pid: str, plan: dict, take: dict, endpoint: str, caps: dict) -> dict:
    """컷 단위 Kling 제출 재료(작업 132·133): 캐스팅·애니 테이크 결과·녹화 오프셋·장면 의상·프롬프트 상한. 컷 제출·QA 다시 만들기가 같이 쓴다."""
    anime_mp4 = _take_anime_mp4(take)
    try:
        outfits = {name: str(item.get("costume") or "")
                   for name, item in _resolve_cast(owner, pid, plan, take, read=False).items()}
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_outfits_failed pid=%s err=%s", pid, _msg(exc))
        outfits = {}
    char_by_id = _char_by_id(owner, plan)
    return {
        "endpoint": endpoint, "caps": caps, "modelId": vpoc_settings.FAL_ID_PREFIX + endpoint,
        "promptMax": vpoc_settings.fal_prompt_max(endpoint, caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX),
        "charById": char_by_id, "overrides": _real_cast_overrides(plan), "anime": anime_mp4,
        "offsets": _take_rec_offsets(plan, take), "outfits": outfits, "takeId": str(take.get("id") or ""),
        "attempt": int((take.get("real") or {}).get("attempt") or 1), "stage": "start",
        "bases": _plan_bases(plan, char_by_id),  # 작업 164 fix1: 캐릭터 기본 옷(시작 그림 · Kling · 장면 옷 편집)
        "take": take,  # 작업 169: Kling 컷 프롬프트의 샷 고정(_kling_shot_lock)
    }


# ---------------------------------------------------------------- 작업 139: 애니 프레임 읽기·시작 그림 검사(Kling 전, 값쌈)
# 컷 단위 Kling 실사에서 시작 그림(Seedream)이 애니 프레임과 구도가 다르면 Kling 이 고칠 수 없다(다시 만들어도 같은 시작 그림).
# 애니 프레임을 대본 분석 모델(Claude, _judge_real_cut 과 같은 _converse 경로)로 1번 읽어 샷 크기·보이는 인물을 받고, 시작 그림을
# 애니 프레임과 나란히 1번 비교한다. 원장은 vision 호출마다 plan._charge(charge_text), Seedream 장마다 _charge_cut_still.
REAL_STILL_CHECK_ITEMS = ("shot", "count", "side", "animeStyle", "timeOfDay")  # 작업 161: timeOfDay(시간대) 접음


def _read_anime_layout(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 emit_anime_layout 을 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    from .prompts import ANIME_LAYOUT_SYSTEM, EMIT_ANIME_LAYOUT  # noqa: PLC0415

    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=ANIME_LAYOUT_SYSTEM, blocks=blocks, tool=EMIT_ANIME_LAYOUT, max_tokens=500,
        where="vnew.generate.anime_layout", model=model,
    )


def _still_check_tool() -> tuple[str, dict]:
    """작업 161: emit_still_check(139) + timeOfDayMatch(boolean, 필수) 1항목. prompts 의 139 원본은 그대로 두고 여기서 덧붙인다."""
    from .prompts import EMIT_STILL_CHECK, STILL_CHECK_SYSTEM  # noqa: PLC0415

    schema = dict(EMIT_STILL_CHECK["inputSchema"]["json"])
    schema["properties"] = {**schema["properties"], "timeOfDayMatch": {"type": "boolean"}}
    schema["required"] = [*schema["required"], "timeOfDayMatch"]
    tool = {**EMIT_STILL_CHECK, "inputSchema": {"json": schema}}
    return STILL_CHECK_SYSTEM.replace("\nnote:", "\n" + REAL_STILL_TOD_RULE + "\nnote:", 1), tool


def _read_still_check(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 emit_still_check 를 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다.
    작업 161: 같은 1번 호출에 시간대(timeOfDayMatch)를 접는다(_still_check_tool)."""
    system_text, tool = _still_check_tool()
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=system_text, blocks=blocks, tool=tool, max_tokens=400,
        where="vnew.generate.still_check", model=model,
    )


def _shot_size(value) -> str:
    """샷 크기 약어(prompts.STILL_SHOT_SIZES 안), 아니면 ""."""
    text = str(value or "").strip().upper()
    return text if text in _SHOT_SIZES else ""


def _anime_frame_layout(owner: str, pid: str, gen_id: str, plan: dict, cut_id: str, frame: bytes | None,
                        names) -> dict | None:
    """애니 프레임 1장을 1번 읽는다 → {shotSize, people[{name, side, visible}], count}. 실패·샷 크기 모름이면 None(138 과 같게).

    names = 컷 등장인물 이름(그 이름으로만 받는다. 로마자로 와도 그 이름으로 맞춘다). 답에 없는 이름은 visible none.
    """
    names = [n for n in dict.fromkeys(str(n or "").strip() for n in names or []) if n]
    if not frame:
        return None
    blocks = [{"text": "Image 1 (anime frame of this shot):"}, {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(frame)}}},
              {"text": "Cast names in this shot (use exactly these names): " + (", ".join(names) or "(none)") + "."}]
    model = str(plan_mod._script_model())  # noqa: SLF001 — 대본 분석과 같은 모델 체인
    try:
        payload, usage = _read_anime_layout(blocks, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.anime_layout_failed pid=%s cut=%s err=%s", pid, cut_id, _msg(exc))
        return None
    plan_mod._charge(owner, pid, gen_id, usage or {}, str((usage or {}).get("model") or model))  # noqa: SLF001
    payload = payload if isinstance(payload, dict) else {}
    shot = _shot_size(payload.get("shotSize"))
    if not shot:
        log.warning("vnew.gen.anime_layout_bad pid=%s cut=%s shot=%s", pid, cut_id, str(payload.get("shotSize"))[:20])
        return None
    alias = {form.lower(): name for name in names for form in _brief_name_forms(name)}
    got: dict[str, dict] = {}
    for item in payload.get("people") or []:
        if not isinstance(item, dict):
            continue
        name = alias.get(str(item.get("name") or "").strip().lower())
        if not name or name in got:
            continue
        side = str(item.get("side") or "").strip().lower()
        visible = str(item.get("visible") or "").strip().lower()
        got[name] = {"name": name, "side": side if side in ("left", "center", "right") else "center",
                     "visible": visible if visible in (*REAL_STILL_SHOWN, "back", "none") else "none"}
    people = [got.get(n) or {"name": n, "side": "center", "visible": "none"} for n in names]
    try:
        count = max(0, int(payload.get("count")))
    except (TypeError, ValueError):
        count = sum(1 for p in people if p["visible"] != "none")
    layout = {"shotSize": shot, "people": people, "count": count}
    log.info("vnew.gen.anime_layout pid=%s cut=%s shot=%s people=%s", pid, cut_id, shot,
             ",".join(f"{p['name']}:{p['side']}:{p['visible']}" for p in people) or "none")
    return layout


def _layout_reading(layout: dict) -> str:
    """시작 그림 검사에 주는 Image 1 읽기 글(샷 크기·화면 인물·좌우)."""
    shot = str(layout.get("shotSize") or "")
    listed = _layout_listed(layout)
    who = "; ".join(f"{name} {side}" + (" (from behind)" if visible == "back" else "") for name, side, visible in listed)
    people = f"{len(listed)} {'person' if len(listed) == 1 else 'people'}: {who}" if listed else "no people"
    return f"Image 1 reading: shot size {shot} ({SHOT_SIZE_EN.get(shot, shot)}); {people}."


def _check_cut_still(owner: str, pid: str, gen_id: str, frame: bytes, still: bytes, layout: dict, *,
                     tod: str = "") -> dict | None:
    """시작 그림을 애니 프레임과 나란히 vision 1번으로 비교한다 → {ok, items, note, shotSize, count} 또는 None(호출 실패).

    items(REAL_STILL_CHECK_ITEMS 순): shot = 샷 크기가 두 단계 이상 다름(한 단계는 허용), count = 인물 수가 읽기와 다름,
    side = 좌우가 다름, animeStyle = 그림 느낌. 기준(Image 1)은 애니 프레임 읽기(layout).
    작업 161: tod("day"|"night")가 있으면 같은 호출에 기준 시간대 글 1줄(REAL_STILL_TOD_REQUIRED) — timeOfDayMatch 가 false 면
    timeOfDay. 기준은 plan 시간대(_real_tod)이지 애니 프레임이 아니다.
    """
    blocks = [{"text": "Image 1 (anime frame):"}, {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(frame)}}},
              {"text": "Image 2 (live-action start frame made from it):"},
              {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(still)}}},
              {"text": _layout_reading(layout)}]
    if tod in REAL_STILL_TOD_REQUIRED:
        blocks.append({"text": REAL_STILL_TOD_REQUIRED[tod]})
    model = str(plan_mod._script_model())  # noqa: SLF001
    try:
        payload, usage = _read_still_check(blocks, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.still_check_failed pid=%s err=%s", pid, _msg(exc))
        return None
    plan_mod._charge(owner, pid, gen_id, usage or {}, str((usage or {}).get("model") or model))  # noqa: SLF001
    payload = payload if isinstance(payload, dict) else {}
    want = _shot_size(layout.get("shotSize"))
    shot = _shot_size(payload.get("shotSize"))
    try:
        count = max(0, int(payload.get("count")))
    except (TypeError, ValueError):
        count = None
    wrong = {
        "shot": bool(shot and want) and abs(_SHOT_SIZES.index(shot) - _SHOT_SIZES.index(want)) > 1,
        "count": count is not None and count != len(_layout_listed(layout)),
        "side": payload.get("sideMatch") is not None and not _flag(payload.get("sideMatch")),
        "animeStyle": payload.get("animeStyle") is not None and _flag(payload.get("animeStyle")),
        "timeOfDay": tod in REAL_STILL_TOD_REQUIRED and payload.get("timeOfDayMatch") is not None
        and not _flag(payload.get("timeOfDayMatch")),
    }
    items = [i for i in REAL_STILL_CHECK_ITEMS if wrong[i]]
    return {"ok": not items, "items": items, "note": " ".join(str(payload.get("note") or "").split())[:80],
            "shotSize": shot, "count": count}


def _still_fix(items, layout: dict, *, tod: str = "day") -> str:
    """시작 그림 검사의 틀린 항목 → Seedream 교정 문장 1개("FIX: …."). 그림 낱말은 쓰지 않는다. 항목이 없으면 "".
    작업 161: timeOfDay 는 tod("day"|"night")의 REAL_STILL_TOD_FIX 조각(맨 뒤)."""
    shot = str(layout.get("shotSize") or "")
    listed = _layout_listed(layout)
    names = ", ".join(name for name, _side, _visible in listed)
    pieces = []
    if "shot" in (items or []):
        pieces.append(f"keep exactly Image 1's shot size ({SHOT_SIZE_EN.get(shot, shot)}) and crop, do not zoom out")
    if "count" in (items or []):
        pieces.append(f"show exactly {len(listed)} {'person' if len(listed) == 1 else 'people'} ({names}) and no one else"
                      if listed else "show no people")
    if "side" in (items or []) and listed:
        pieces.append("keep each person on the same side as in Image 1 ("
                      + ", ".join(f"{name} {side}" for name, side, _visible in listed) + ")")
    if "animeStyle" in (items or []):
        pieces.append("make it a real photograph with real skin, real hair and real fabric")
    if "timeOfDay" in (items or []):
        pieces.append(REAL_STILL_TOD_FIX.get(tod, REAL_STILL_TOD_FIX["day"]))
    return "FIX: " + "; ".join(pieces) + "." if pieces else ""


def _retry_better(first_items, retry_items) -> bool:
    """작업 135 규칙(실사 컷 QA·시작 그림 검사 같이 씀): 다시 만든 것을 쓰는가. 다시 만든 것 검사가 안 됐거나(None) 맞았거나
    틀린 항목이 첫 것보다 적으면 True, 같거나 많으면 False(첫 것)."""
    return retry_items is None or not retry_items or len(retry_items) < len(first_items or [])


def _checked_cut_still(owner: str, pid: str, gen_id: str, plan: dict, cut: dict, ctx: dict, *, frame: bytes, still: bytes,
                       layout: dict, refs: list[bytes], people: list[tuple[str, str, int]], background_no: int,
                       charge_key: str, sheets: dict | None = None, costumes: dict | None = None,
                       prev_no: int = 0) -> tuple[bytes, dict]:
    """시작 그림 검사(_check_cut_still) → 틀리면 Seedream 만 1번 다시(시트 뷰 없이 [애니 프레임 + 보이는 인물 정면], 교정 문장).

    둘 다 틀리면 틀린 항목이 적은 쪽(_retry_better, 같으면 첫 것). 돌려주는 값 = (쓸 시작 그림, 컷 기록 stillCheck
    {ok, items, note, retried, firstItems, used}). 검사가 안 되면 첫 그림 그대로(ok None).
    작업 142: sheets(이름 → 정면 번호, 인물 첫 장이 AI 캐릭터 시트)도 같은 규칙으로 번호를 당긴다.
    작업 160: costumes(이름 → 장면 옷 그림 번호)·prev_no(앞 컷 시작 그림 번호)도 같은 규칙으로 당긴다.
    작업 161: 같은 검사 호출에 시간대(_real_tod)를 접는다. 시간대가 틀리면 다시 그리기(1번 그대로)에서 앞 컷 시작 그림 참고를 뺀다
    (prev_no 0). 시트 뷰(시간대·빛의 기준)는 구도 항목이 틀렸을 때만 뺀다(139) — 시간대만 틀리면 남긴다.
    """
    tod = _real_tod(plan)
    first = _check_cut_still(owner, pid, gen_id, frame, still, layout, tod=tod)
    if first is None or first["ok"]:
        return still, {"ok": None if first is None else True, "items": [], "note": (first or {}).get("note", ""),
                       "retried": False, "firstItems": [], "used": "first"}
    framing = any(i != "timeOfDay" for i in first["items"])
    drop_prev = "timeOfDay" in first["items"] and bool(prev_no)
    dropped = {no for no in ((background_no if framing else 0), (prev_no if drop_prev else 0)) if no}
    refs2 = [ref for no, ref in enumerate(refs, 1) if no not in dropped]

    def shift(no: int) -> int:
        return no - sum(1 for d in dropped if d < no)

    people2 = [(color, name, shift(no)) for color, name, no in people]
    sheets2 = {name: shift(no) for name, no in (sheets or {}).items()}
    costumes2 = {name: shift(no) for name, no in (costumes or {}).items()}
    prompt2 = _cut_still_prompt(plan, cut, people2, True, source="anime", outfits=ctx["outfits"], bases=ctx.get("bases"),
                                background_no=0 if framing else background_no,
                                layout=layout, fix=_still_fix(first["items"], layout, tod=tod), sheets=sheets2 or None,
                                costumes=costumes2 or None, prev_no=shift(prev_no) if prev_no and not drop_prev else 0)
    still2 = _seedream(prompt2, refs2, size=vpoc_seedream.SIZE)
    del refs2
    _charge_cut_still(owner, pid, gen_id, f"{charge_key}-{secrets.token_hex(3)}")
    second = _check_cut_still(owner, pid, gen_id, frame, still2, layout, tod=tod)
    use_retry = _retry_better(first["items"], None if second is None else second["items"])
    final = (second or {"ok": None, "items": [], "note": ""}) if use_retry else first
    record = {"ok": final["ok"], "items": list(final["items"]), "note": final["note"], "retried": True,
              "firstItems": list(first["items"]), "used": "retry" if use_retry else "first"}
    return (still2 if use_retry else still), record


def _pack_still_people(rows, refs: list, cap: int) -> tuple[list[tuple[str, str, int]], dict[str, int], str]:
    """작업 142: 시작 그림 인물 참고 채우기. rows = [(색, 이름, [(kind "sheet"|"front", jpeg)])], refs 뒤에 이어 붙인다.

    장수는 _ref_counts(cap): 모두의 첫 장(시트)을 먼저 주고 남으면 둘째 장(정면). 한 인물의 장은 이어진 번호다.
    반환 (people [(색, 이름, 첫 장 번호)], sheets {이름: 정면 번호(0 = 없음)} — 첫 장이 시트인 인물만, 로그 refs=sheet|front|both).
    """
    people: list[tuple[str, str, int]] = []
    sheets: dict[str, int] = {}
    kinds: set[str] = set()
    for (color, name, photos), count in zip(rows, _ref_counts([len(r[2]) for r in rows], cap)):
        nos: list[tuple[str, int]] = []
        for kind, photo in photos[:count]:
            refs.append(_fit_ref(photo))
            nos.append((kind, len(refs)))
            kinds.add(kind)
        if not nos:
            continue
        people.append((color, name, nos[0][1]))
        if nos[0][0] == "sheet":
            sheets[name] = nos[1][1] if len(nos) > 1 else 0
    return people, sheets, "both" if len(kinds) > 1 else (next(iter(kinds)) if kinds else "front")


def _submit_real_cut(owner: str, pid: str, gen_id: str, plan: dict, ctx: dict, cut: dict, *, fix: str = "",
                     tag: str = "", start: bytes | None = None, start_key: str = "") -> dict:
    """컷 1개: 시작 그림(Seedream) 1장 + Kling 1번 제출(작업 132). 원장은 시작 그림·Kling 제출 성공 때. 돌려주는 값 = 칸 fallback.cuts 항목.

    작업 133: fix(QA 교정 문장)·tag(출력 경로·원장 키 꼬리, 다시 만들기 "-qa")가 있으면 그 컷 다시 만들기다. takeAt = 테이크 안 컷 시작 초.
    작업 131: 시작 그림을 출력 접두사 아래 start.jpg 로 남기고(stillKey, 실패해도 제출은 한다) 보낸 프롬프트를 promptEn·promptBytes 로,
    제출을 컷 칸("테이크id#컷id", tag 없음) 기록 한 줄(_log_attempt)로 남긴다. 다시 만들기는 같은 칸에 한 줄 더, 접두사는 tag 꼬리.
    ctx["index"] = 테이크 index(기록 cellIndex).
    작업 139: 애니 프레임 + 시트 뷰가 있으면 애니 프레임을 1번 읽어(_anime_frame_layout) 얼굴이 보이는 인물(face·partial) 정면만
    참고로 넣고, ECU·CU·MCU 면 시트 뷰를 넣지 않는다. 시작 그림은 Kling 전에 검사하고(_checked_cut_still, VNEW_REAL_STILL_CHECK)
    틀리면 Seedream 만 1번 다시. 컷 기록 animeLayout·stillCheck. 읽기 실패(None)면 138 과 같다.
    """
    endpoint, caps, take_id, attempt = ctx["endpoint"], ctx["caps"], ctx["takeId"], ctx["attempt"]
    beat = ctx.get("beat") or (lambda: None)  # 작업 144: 2단계 첫 제출만 — 하트비트 + 선점 확인(잃으면 _Discard)
    cut_id = str(cut.get("cutId") or "")
    ctx["stage"] = "frame"
    start_sec = float(cut.get("startSec") or 0)
    take_at = round(ctx["offsets"].get(str(cut.get("recSegId") or ""), 0.0) + start_sec, 3)
    from_prev = isinstance(start, (bytes, bytearray)) and bool(start)  # 작업 169: 앞 조각·앞 컷 결과의 마지막 프레임
    frame, source = None, ("prevFrame" if from_prev else "previz")
    if not from_prev:
        if ctx["anime"]:
            frame = _cut_first_frame(ctx["anime"], take_at)
            source = "anime" if frame else "previz"
        if frame is None:
            frame = _cut_first_frame(_fetch_bytes(str(cut.get("recordingKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET), start_sec)
    ctx["stage"] = "still"
    duration = _kling_duration(caps, float(cut.get("seconds") or 0))
    # 작업 132: 실사 컷 프롬프트(그림체 real). 시작 그림(유료) 전에 만들고, "webtoon" 이 남으면 제출하지 않는다.
    lock = _kling_shot_lock(owner, plan, ctx, cut_id, float(cut.get("seconds") or 0))  # 작업 169: 샷 고정
    kling_prompt = _segment_prompt(plan, {"cutIds": [cut_id]}, first_segment=True, model=ctx["modelId"], real=True,
                                   prompt_max=ctx["promptMax"], fix=fix, bases=ctx.get("bases"), lock=lock,
                                   from_prev=from_prev)
    _assert_no_webtoon(kling_prompt)
    layout = still_check = None
    people: list = []
    refs_kind, sheet_view = "prevFrame", "none"
    if from_prev:
        still = bytes(start)  # 작업 169: 시작 그림 = 앞 샷 마지막 프레임(Seedream·시작 그림 검사 없음)
    else:
        refs = [_fit_ref(frame)] if frame else []
        # 작업 135: 공간 시트 실사 뷰(배경 기준) = 프레임 다음(Image 2). 없으면 지금과 같다.
        sheet = _cut_location_view(plan, cut_id)
        actors = _segment_actors(plan, cut)
        # 작업 139: 애니 프레임 + 시트 뷰가 있으면 애니 프레임을 1번 읽는다(샷 크기·보이는 인물). 실패(None)면 138 과 같다.
        layout = (_anime_frame_layout(owner, pid, gen_id, plan, cut_id, frame, [a.get("name") for a in actors])
                  if sheet is not None and source == "anime" and frame else None)
        shown = {name: visible for name, _side, visible in _layout_listed(layout)} if layout else None
        background_no = 0
        if sheet is not None and not (layout and layout["shotSize"] in REAL_STILL_CLOSE):  # 작업 139: 가까운 샷은 시트 뷰 없음
            refs.append(_fit_ref(sheet[0]))
            background_no = len(refs)
        # 작업 142: 보이는 인물마다 _real_identity_refs(실사 시트 → 정면, 시트 없으면 140 과 같은 정면 1장). Seedream 상한 안에서
        # _ref_counts 로 첫 장(시트)을 모두에게 먼저 — 넘치면 정면부터 빠지고 보이는 인물은 빠지지 않는다(_pack_still_people).
        rows: list[tuple[str, str, list[tuple[str, bytes]]]] = []
        picked: list[tuple[str, dict | None, str]] = []
        for actor in actors:
            name = str(actor.get("name") or "")
            if shown is not None and shown.get(name) not in REAL_STILL_SHOWN:
                continue  # 작업 139: 애니 프레임에 얼굴이 안 보이는 인물(back·none)은 정면 사진을 넣지 않는다
            pick = ctx["overrides"].get(name)
            char = _cast_entry(owner, pick["charId"], name) if pick else ctx["charById"].get(str(actor.get("charId") or ""))
            picked.append((actor, char, pick["look"] if pick else "real"))
        # 작업 160: 보이는 인물의 장면 옷 편집 그림(메모리만) — 실사 정면 자리를 바꾼다. 그 컷 장소의 _scene_costume.
        swaps = _costume_refs(owner, pid, plan, [(str(a.get("name") or ""), c, lk) for a, c, lk in picked],
                              _cut_location(plan, cut_id), ctx.get("outfits") or {}, bases=ctx.get("bases"))
        costume_names: set[str] = set()
        for actor, char, look in picked:
            name = str(actor.get("name") or "")
            keys = _real_identity_refs(char, look) if char else []
            sheet_key = str(char.get("realSheetKey") or "") if char and look != "anime" else ""
            front_key = str(char.get("realFrontKey") or "") if char and look != "anime" else ""
            photos: list[tuple[str, bytes]] = []
            for key in keys:
                photo = _load_jpeg(key)
                if photo is not None:
                    if front_key and key == front_key and swaps.get(name):
                        photo = swaps[name]
                        costume_names.add(name or "a person")
                    photos.append(("sheet" if sheet_key and key == sheet_key else "front", photo))
            if sheet_key and keys[:1] == [sheet_key] and not photos[:1]:
                photos = [("front", p) for p in (_load_jpeg(k) for k in _fallback_front_keys(char, "real")) if p is not None]
            if photos:
                rows.append((str(actor.get("colorName") or "grey"), name or "a person", photos))
        # 작업 160: 컷 k(>1)는 앞 컷 시작 그림을 참고 맨 뒤에 1장(같은 장소·시간대·빛). 자리를 먼저 남긴다.
        prev_still = ctx.get("prevStill") if isinstance(ctx.get("prevStill"), (bytes, bytearray)) else None
        people, sheets, refs_kind = _pack_still_people(rows, refs, _SEEDREAM_MAX_REFS - len(refs) - int(prev_still is not None))
        # 장면 옷 그림이 실제로 들어간 인물만: 시트 인물은 정면 번호(둘째 장)가 남았을 때, 시트 없는 인물은 첫 장.
        costumes = {name: (sheets[name] if name in sheets else no) for _color, name, no in people
                    if name in costume_names and (sheets.get(name) or name not in sheets)}
        prev_no = 0
        if prev_still is not None:
            refs.append(_fit_ref(bytes(prev_still)))
            prev_no = len(refs)
        beat()  # 작업 144: 시작 그림(유료) 전
        still = _seedream(_cut_still_prompt(plan, cut, people, frame is not None, source=source, outfits=ctx["outfits"],
                                            bases=ctx.get("bases"),
                                            background_no=background_no, layout=layout, sheets=sheets or None,
                                            costumes=costumes or None, prev_no=prev_no), refs,
                          size=vpoc_seedream.SIZE)
        sheet_view = sheet[1] if sheet else "none"
        still_key_base = f"vnew-gen-{pid}-{gen_id}-{take_id}-{cut_id}{tag}"
        _charge_cut_still(owner, pid, gen_id, f"{still_key_base}-still-{attempt}-{secrets.token_hex(3)}")
        still_check = None
        if layout and vnew_settings.VNEW_REAL_STILL_CHECK:  # 작업 139: Kling(비싼 단계) 전에 시작 그림 검사, 틀리면 Seedream 만 1번 다시
            beat()  # 작업 144: 시작 그림 검사(다시 그리기 유료) 전
            still, still_check = _checked_cut_still(owner, pid, gen_id, plan, cut, ctx, frame=frame, still=still, layout=layout,
                                                    refs=refs, people=people, background_no=background_no, sheets=sheets,
                                                    charge_key=f"{still_key_base}-still2-{attempt}",
                                                    costumes=costumes or None, prev_no=prev_no)
        del frame, refs, sheet
    ctx["prevStill"] = still  # 작업 160: 다음 컷 시작 그림의 참고(메모리, 이 컷 루프 동안만)
    beat()  # 작업 144: Kling 제출(유료) 전
    ctx["stage"] = "submit"
    out_prefix = _seg_output_prefix(owner, pid, gen_id, f"{take_id}-real-{cut_id}{tag}", attempt)
    still_key = _keep_cut_still(pid, out_prefix, still)  # 작업 131: ⑦ AI로 고치기가 같은 시작 그림으로 다시 낸다
    body = vpoc_fal.build_payload({**caps, "durations": [duration]}, prompt=kling_prompt,
                                  keyframe_jpeg_b64=base64.b64encode(still).decode("ascii"), duration_s=duration,
                                  prompt_max=ctx["promptMax"])
    del still
    sent_prompt = str(body.get(caps.get("promptField") or "prompt") or "")
    result = _previz_request("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}", body)
    del body
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.generate", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)
    _charge_kling_cut(owner, pid, gen_id, "fal-kcut-" + request_id, endpoint, duration)
    arn = f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}"
    gen = plan.get("gen") or {}
    # 작업 131: 컷 칸("테이크id#컷id") 기록 한 줄 — ⑦ AI로 고치기의 이전 프롬프트·시도
    _log_attempt(owner, pid, gen_id, source="gen", mode=str(gen.get("mode") or ""), variant="real",
                 cell_id=f"{take_id}#{cut_id}", index=int(ctx.get("index") or 0), attempt=attempt,
                 run=int((gen.get("realRun") or {}).get("n") or 0) or None, endpoint=endpoint,
                 params={"seconds": round(float(cut.get("seconds") or 0), 3), "duration": duration,
                         "startImage": "prevFrame" if from_prev else "still"},
                 refs=[{"slot": "startImage", "key": still_key}], video_keys=[str(cut.get("recordingKey") or "")],
                 result={"invocationArn": arn, "outputPrefix": out_prefix}, prompt=sent_prompt)
    extra = f" layout={layout['shotSize']}" if layout else ""
    if still_check is not None:
        extra += f" still={','.join(still_check['items']) or 'ok'}" + (" still_retry=1" if still_check["retried"] else "")
    if lock:
        extra += " lock=1"  # 작업 169
    log.info("vnew.gen.real_cut_submit pid=%s take=%s cut=%s endpoint=%s duration=%d people=%d refs=%s frame=%s sheet=%s%s%s",
             pid, take_id, cut_id, endpoint, duration, len(people), refs_kind, source, sheet_view, " qa_retry=1" if tag else "",
             extra)
    record = {"cutId": cut_id, "seconds": round(float(cut.get("seconds") or 0), 3), "duration": duration, "takeAt": take_at,
              "status": "submitted", "invocationArn": arn, "outputPrefix": out_prefix, "outputKey": "", "error": "",
              "promptEn": sent_prompt, "promptBytes": len(sent_prompt.encode("utf-8")), "stillKey": still_key}
    if from_prev:
        record.update(startImage="prevFrame", startKey=str(start_key or ""))  # 작업 169
    if layout:
        record["animeLayout"] = layout  # 작업 139
    if still_check is not None:
        record["stillCheck"] = still_check
    return record


def _do_submit_real_cuts(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """2단계: 테이크를 컷마다 시작 그림(Seedream) 1장 + Kling image-to-video 1번으로 낸다(_submit_real_cut).

    작업 132: 시작 그림 참고 = [같은 gen 애니(웹툰) 테이크 결과의 그 컷 시작 프레임(구도·옷·배경 기준), 실사 정면…]. 애니 결과가
    없거나 프레임을 못 뽑으면 프리비즈 첫 프레임(+ 프롬프트에 대본·콘티의 옷·장소 문장). Kling 프롬프트는 _segment_prompt(real=True).
    작업 135: 그 장소 공간 시트 실사 뷰가 있으면 프레임 다음에 넣는다([프레임, 시트 뷰(배경), 실사 정면…], _cut_location_view).
    시작 그림은 컷 결과 접두사 아래 start.jpg 로 남긴다(작업 131 — ⑦ AI로 고치기가 같은 그림으로 다시 낸다, 실패해도 제출은 한다).
    길이는 _kling_duration. 준비~제출은 _PREPARE_SEM 안. 원장은 시작 그림·Kling 컷마다 제출 성공 때. 칸 fallback.cuts 에 컷마다
    {cutId, seconds, duration, takeAt, status, invocationArn, outputPrefix, outputKey, error, promptEn, promptBytes, stillKey}.
    작업 131: 컷마다 프롬프트 기록 한 줄(칸 "테이크id#컷id", _log_attempt), 조회가 끝나면 그 줄의 상태(_log_kling_cut_result).
    """
    records: list[dict] = []
    ctx: dict = {"stage": "start"}
    _PREPARE_SEM.acquire()
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "real", claim):
            return
        model = _kling_cut_model()
        if not model:
            raise VpocError("vnew.generate", "KlingMissing", "컷 단위 Kling 모델이 fal 등록표에 없습니다.", 409)
        take = _previz_cells(gen)[index]
        cuts = _take_cut_units(plan, take)
        if not cuts:
            raise VpocError("vnew.generate", "NoCuts", "이 테이크의 컷을 찾지 못했습니다.", 409)
        ctx = _real_cut_ctx(owner, pid, plan, take, *model)
        ctx["takeId"], ctx["index"] = str(take.get("id") or index), index

        def beat() -> None:
            # 작업 144: 컷 루프가 300초를 넘어도 stale 로 되돌려지지 않게 claimedAt 을 갱신하고, 선점을 잃었으면(취소·재선점) 멈춘다.
            if not _touch_prepare_claim(owner, pid, gen_id, index, "real", claim):
                raise _Discard()

        ctx["beat"] = beat
        for cut in cuts:
            records.append(_submit_real_cut(owner, pid, gen_id, plan, ctx, cut))
            beat()  # 작업 144: 컷마다
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_submit_failed pid=%s take=%s stage=%s err=%s", pid, index, ctx.get("stage"), _msg(exc))
        _fail_variant(owner, pid, gen_id, index, "real", claim, "컷 단위 Kling 으로 만들지 못했습니다: " + _msg(exc))
        return
    finally:
        _PREPARE_SEM.release()

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, "real", claim):
            raise _Discard()
        state = _previz_cells(g)[index]["real"]
        state.update(status="submitted", invocationArn="", outputKey="", error="")
        state["fallback"] = {**(state.get("fallback") or {}), "cuts": records, "total": len(records)}

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


# ---------------------------------------------------------------- 작업 133: 실사 컷 자동 품질 검사(QA)
# 컷 단위 Kling 실사 컷이 끝나면 실사 결과 가운데 프레임과 같은 시각의 애니 테이크 프레임을 Claude(대본 분석 모델 체인)로 비교한다.
# 틀린 항목이 있으면 그 컷만 같은 방식(시작 그림 + Kling)으로 1번 다시 만들고(교정 문장 1개), 다시 만든 것도 검사해 맞았거나 틀린
# 항목이 더 적으면 그것을 쓴다(작업 135, 같으면 첫 결과). 공간 시트 실사 뷰가 있으면 Image 3 으로 함께 준다(장소 판정 기준).
# 판정 기준 문구는 prompts.CUT_QA_RULES 한 곳. 컷 기록 qa = {ok, items[], note, retried, firstItems[]}.
REAL_CUT_QA_ITEMS = ("animeStyle", "outfit", "location", "people")
# 교정 문장 조각(틀린 항목 → 한 문장 "FIX: …, …."). 그림 낱말(webtoon·anime·illustration·cel)은 쓰지 않는다.
REAL_CUT_QA_FIX = {
    "animeStyle": "render every frame as photoreal live-action footage with real skin and real fabric",
    "outfit": "keep exactly the same outfits (garments and colors) as the start frame",
    "location": "keep the same location as the start frame (same building, railing, street and background)",
    "people": "keep the same people in the same left-right positions as the start frame, no one added or removed",
}


def _real_cut_fix(items) -> str:
    """틀린 항목 → Kling 프롬프트 교정 문장 1개. 항목이 없으면 ""."""
    pieces = [REAL_CUT_QA_FIX[i] for i in REAL_CUT_QA_ITEMS if i in (items or [])]
    if not pieces:
        return ""
    return "FIX: " + "; ".join(pieces) + "."


def _read_cut_qa(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 emit_cut_qa 를 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    from .prompts import CUT_QA_SYSTEM, EMIT_CUT_QA  # noqa: PLC0415

    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=CUT_QA_SYSTEM, blocks=blocks, tool=EMIT_CUT_QA, max_tokens=800, where="vnew.generate.cut_qa",
        model=model,
    )


def _cut_qa_text(plan: dict, cut_id: str) -> str:
    """QA 에 넣는 그 컷 콘티 글(인물·동작·카메라)."""
    cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == str(cut_id)), {})
    people = "; ".join(f"{ch.get('name', '')}: {ch.get('actionEn', '')}".strip(": ")
                       for ch in cut.get("characters") or [] if isinstance(ch, dict))
    parts = [f"Storyboard cut {cut.get('no') or cut_id}."]
    for label, value in (("Shot", _shot_ko_en(str(cut.get("shotKo") or "")) or _shot_abbrev(plan, str(cut_id))),
                         ("Camera", cut.get("cameraEn")), ("People", people), ("Action", cut.get("promptEn"))):
        value = " ".join(str(value or "").split())
        if value:
            parts.append(f"{label}: {value.rstrip('.')}.")
    return " ".join(parts)


def _judge_real_cut(owner: str, pid: str, gen_id: str, plan: dict, cut: dict, anime_mp4: bytes | None) -> dict | None:
    """실사 컷 결과 1개를 QA 한다. {ok, items, note} 또는 None(애니 결과·프레임이 없거나 호출 실패 → 검사 건너뜀, 다시 만들지 않는다).

    프레임: 실사 = 컷 결과의 가운데(min(컷 길이, Kling 길이)/2), 애니 = 애니 테이크의 takeAt + 같은 초. 원장 charge_text(plan._charge).
    """
    if not anime_mp4:
        return None
    seconds = float(cut.get("seconds") or 0) or float(cut.get("duration") or 0)
    mid = round(min(seconds, float(cut.get("duration") or seconds) or seconds) / 2.0, 3)
    try:
        real_frame = _cut_first_frame(_fetch_bytes(str(cut.get("outputKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET), mid)
        anime_frame = _cut_first_frame(anime_mp4, float(cut.get("takeAt") or 0) + mid)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_qa_frame_failed pid=%s cut=%s err=%s", pid, cut.get("cutId"), type(exc).__name__)
        return None
    if not real_frame or not anime_frame:
        return None
    blocks = [{"text": "Image 1 (anime reference frame):"}, {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(anime_frame)}}},
              {"text": "Image 2 (live-action result at the same moment):"},
              {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(real_frame)}}}]
    sheet = _cut_location_view(plan, str(cut.get("cutId") or ""))  # 작업 135: 애니 프레임 배경이 흐릴 수 있어 장소 기준을 함께 준다
    if sheet is not None:
        blocks += [{"text": "Image 3 (live-action location sheet view of the place this shot must be in):"},
                   {"image": {"format": "jpeg", "source": {"bytes": _fit_ref(sheet[0])}}}]
    del sheet
    blocks.append({"text": _cut_qa_text(plan, str(cut.get("cutId") or ""))})
    model = str(plan_mod._script_model())  # noqa: SLF001 — 대본 분석과 같은 모델 체인
    try:
        payload, usage = _read_cut_qa(blocks, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.real_cut_qa_failed pid=%s cut=%s err=%s", pid, cut.get("cutId"), _msg(exc))
        return None
    plan_mod._charge(owner, pid, gen_id, usage or {}, str((usage or {}).get("model") or model))  # noqa: SLF001
    payload = payload if isinstance(payload, dict) else {}

    def flag(key: str, default: bool) -> bool:
        value = payload.get(key)
        return _flag(value) if value is not None else default

    wrong = {"animeStyle": flag("animeStyle", False), "outfit": not flag("outfitMatch", True),
             "location": not flag("locationMatch", True), "people": not flag("peopleMatch", True)}
    items = [i for i in REAL_CUT_QA_ITEMS if wrong[i]]
    note = " ".join(str(payload.get("note") or "").split())[:80]
    log.info("vnew.gen.real_cut_qa pid=%s cut=%s items=%s", pid, cut.get("cutId"), ",".join(items) or "none")
    return {"ok": not items, "items": items, "note": note}


def _prev_cut_still(cuts: list[dict], number: int) -> bytes | None:
    """작업 160: 컷 기록 목록에서 number 앞 컷의 시작 그림(131 stillKey, 영상 버킷 읽기만). 첫 컷·키 없음·못 읽음이면 None."""
    if number <= 0 or number > len(cuts):
        return None
    key = str((cuts[number - 1] or {}).get("stillKey") or "")
    if not key:
        return None
    try:
        return _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET) or None
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.prev_cut_still_failed err=%s", type(exc).__name__)
        return None


def _qa_real_cuts(owner: str, pid: str, gen_id: str, index: int, claim: str, plan: dict, cuts: list[dict]) -> dict[int, dict]:
    """완료됐고 qa 가 없는 컷을 검사한다(작업 133). 돌려주는 값 = {컷 번호: 바꿀 값}. 첫 검사에서 틀리면 그 컷만 1번 다시 제출한다.

    다시 만든 결과(qaFirst 있음)는 다시 검사해, 맞았거나(틀린 항목 0) 틀린 항목 수가 첫 결과보다 적으면 그것을, 같거나 많으면
    첫 결과(outputKey)를 쓴다(작업 135 — 둘 다 틀리면 틀린 항목이 적은 쪽, 같으면 첫 결과). 다시 검사가 안 되면(None) 다시 만든 것.
    다시 제출이 실패하면 첫 결과 그대로 qa ok false. 다시 제출은 _PREPARE_SEM 안.
    """
    take = _previz_cells(plan.get("gen") or {})[index]
    anime_mp4 = _take_anime_mp4(take)
    changes: dict[int, dict] = {}
    for number, cut in enumerate(cuts):
        if cut.get("status") != "completed" or "qa" in cut:
            continue
        judge = _judge_real_cut(owner, pid, gen_id, plan, cut, anime_mp4)
        first = cut.get("qaFirst") if isinstance(cut.get("qaFirst"), dict) else None
        if first is not None:
            first_items = list(first.get("items") or [])
            if _retry_better(first_items, None if judge is None else judge["items"]):
                final = judge or {"ok": None, "items": [], "note": ""}
                changes[number] = {"qa": {**final, "retried": True, "firstItems": first_items}}
            else:  # 다시 만든 것도 틀렸고 틀린 항목이 적지 않다 → 첫 결과(작업 135)
                changes[number] = {**_real_cut_first(first),
                                   "qa": {"ok": False, "items": first_items, "note": str(first.get("note") or ""),
                                          "retried": True, "firstItems": first_items}}
            continue
        if judge is None:
            changes[number] = {"qa": {"ok": None, "items": [], "note": "", "retried": False, "firstItems": []}}
            continue
        if judge["ok"]:
            changes[number] = {"qa": {**judge, "retried": False, "firstItems": []}}
            continue
        model = _kling_cut_model()
        record = None
        if model:
            _PREPARE_SEM.acquire()
            try:
                ctx = _real_cut_ctx(owner, pid, plan, take, *model)
                ctx["takeId"], ctx["index"] = str(take.get("id") or index), index
                ctx["prevStill"] = _prev_cut_still(cuts, number)  # 작업 160: 다시 만들 때도 앞 컷 시작 그림 참고
                unit = next((u for u in _take_cut_units(plan, take) if str(u.get("cutId")) == str(cut.get("cutId"))), None)
                if unit is not None:
                    record = _submit_real_cut(owner, pid, gen_id, plan, ctx, unit, fix=_real_cut_fix(judge["items"]), tag="-qa")
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.gen.real_cut_qa_retry_failed pid=%s cut=%s err=%s", pid, cut.get("cutId"), _msg(exc))
            finally:
                _PREPARE_SEM.release()
        if record is None:
            changes[number] = {"qa": {**judge, "retried": False, "firstItems": list(judge["items"])}}
        else:
            changes[number] = {**record, "qaFirst": {"items": judge["items"], "note": judge["note"],
                                                     "outputKey": str(cut.get("outputKey") or ""),
                                                     **{k: cut[k] for k in _REAL_CUT_FIRST_KEYS if k in cut}}}
    return changes


# 작업 131·133: 첫 결과로 되돌릴 때 함께 되돌리는 컷 기록(⑦ AI로 고치기가 읽는 그 결과의 제출·프롬프트·시작 그림).
# 작업 139: 그 시작 그림의 애니 프레임 읽기·시작 그림 검사도 함께.
_REAL_CUT_FIRST_KEYS = ("invocationArn", "outputPrefix", "promptEn", "promptBytes", "stillKey", "animeLayout", "stillCheck")


def _real_cut_first(first: dict) -> dict:
    """qaFirst → 첫 결과로 되돌리는 값(outputKey + 남겨 둔 _REAL_CUT_FIRST_KEYS)."""
    return {"outputKey": str(first.get("outputKey") or ""), **{k: first[k] for k in _REAL_CUT_FIRST_KEYS if k in first}}


def _do_poll_real_cuts(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """2단계 칸 조회: 제출된 컷을 모두 조회해 저장하고, 하나라도 실패면 칸 실패, 모두 완료면 이어 붙인다(_merge_real_cuts).

    작업 133(VNEW_REAL_CUT_QA 켬): 완료된 컷마다 QA(_qa_real_cuts)를 지나야 이어 붙인다. QA 로 다시 만든 컷이 실패하면 칸 실패가
    아니라 첫 결과로 되돌린다(qa ok false). 끄면 지금과 같다.
    작업 131: 조회가 끝난 제출마다 컷 칸 기록 줄의 상태를 고친다(_log_kling_cut_result — QA 다시 만들기 실패는 그 제출 failed).
    """
    qa_on = bool(vnew_settings.VNEW_REAL_CUT_QA)
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if not _claim_ok_variant(gen, index, "real", claim):
        return
    state = _previz_cells(gen)[index]["real"]
    cuts = list((state.get("fallback") or {}).get("cuts") or [])
    updates: dict[int, dict] = {}
    outcomes: dict[int, dict] = {}  # 작업 131: 조회가 끝난 제출(그 invocationArn)의 결과 → 기록 줄
    for number, cut in enumerate(cuts):
        if cut.get("status") != "submitted":
            continue
        arn, prefix = str(cut.get("invocationArn") or ""), str(cut.get("outputPrefix") or "")
        result = vpoc_fal.poll(arn, prefix)
        status = str(result.get("status") or "")
        if status == "Completed":
            updates[number] = {"status": "completed", "outputKey": _find_output_key(prefix, arn)}
            outcomes[number] = {**cut, **updates[number]}
        elif status != "InProgress":
            error = str(result.get("failureMessage") or "영상 생성 실패")[:300]
            outcomes[number] = {**cut, "status": "failed", "outputKey": "", "error": error}
            first = cut.get("qaFirst") if isinstance(cut.get("qaFirst"), dict) else None
            if first is not None:  # 작업 133: QA 다시 만들기 실패 → 첫 결과로
                items = list(first.get("items") or [])
                updates[number] = {"status": "completed", **_real_cut_first(first),
                                   "qa": {"ok": False, "items": items, "note": str(first.get("note") or ""),
                                          "retried": True, "firstItems": items, "retryError": error[:120]}}
            else:
                updates[number] = {"status": "failed", "error": error}

    def apply(changes: dict[int, dict]) -> None:
        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "real", claim):
                raise _Discard()
            stored = (_previz_cells(g)[index]["real"].get("fallback") or {}).get("cuts") or []
            for number, change in changes.items():
                if number < len(stored):
                    stored[number].update(change)

        _commit(owner, pid, mutator)
        for number, change in changes.items():
            cuts[number] = {**cuts[number], **change}

    if updates:
        apply(updates)
        take_id = str(_previz_cells(gen)[index].get("id") or index)
        for outcome in outcomes.values():
            _log_kling_cut_result(owner, pid, gen_id, take_id, outcome)  # 작업 131
    failed = [c for c in cuts if c.get("status") == "failed"]
    if failed:
        error = str(failed[0].get("error") or "")
        if _likeness_rejected(error):
            log.info("vnew.gen.real_likeness_reject step=2 note=1")  # 작업 140: 컷 단위 Kling 프롬프트에도 고지를 보냈다
            tried = list((state.get("fallback") or {}).get("tried") or []) + [REAL_FALLBACK_TRIED[2]]
            message, kind = REAL_FALLBACK_FAIL.format(steps=" → ".join(tried)), "likeness"
        else:
            message, kind = f"컷 단위 Kling 의 컷 {failed[0].get('cutId')} 이 실패했습니다: {error}", _audio_kind(error)
        _fail_variant(owner, pid, gen_id, index, "real", claim, message, kind=kind)
        return
    if qa_on and any(c.get("status") == "completed" and "qa" not in c for c in cuts):
        changes = _qa_real_cuts(owner, pid, gen_id, index, claim, plan, cuts)
        if changes:
            try:
                apply(changes)
            except _Discard:
                return
    if cuts and all(c.get("status") == "completed" and (not qa_on or "qa" in c) for c in cuts):
        _merge_real_cuts(owner, pid, gen_id, index, claim, cuts)


def _merge_real_cuts(owner: str, pid: str, gen_id: str, index: int, claim: str, cuts: list[dict]) -> None:
    """컷 결과를 이어 붙여 그 테이크 실사 결과(outputKey)로 둔다. 컷 결과 파일은 그대로 남는다.

    작업 134: 컷마다 같은 형식으로 맞추고 콘티 길이(_real_cut_seconds)로 자른 뒤 잇는다(_conform_concat). 맞추지 못하면(못 읽는
    컷) 경고를 남기고 예전 방식(길면 _trim_clip, 그다음 _concat)으로 잇는다.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    take = _previz_cells(plan.get("gen") or {})[index]
    pieces = [(_fetch_bytes(str(cut.get("outputKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET), _real_cut_seconds(cut))
              for cut in cuts]
    try:
        merged = _conform_concat(pieces)
    except Exception as exc:  # noqa: BLE001 — 맞추지 못하면 예전 방식으로 잇는다.
        log.warning("vnew.gen.real_cut_conform_failed pid=%s take=%s err=%s", pid, take.get("id"), _msg(exc))
        clips = []
        for cut, (clip, _seconds) in zip(cuts, pieces):
            seconds = float(cut.get("seconds") or 0)
            if seconds > 0 and float(cut.get("duration") or 0) > seconds + 0.05:
                try:
                    clip = _trim_clip(clip, seconds)
                except Exception as trim_exc:  # noqa: BLE001 — 자르지 못하면 Kling 길이 그대로 잇는다(⑦ 재료는 trimTo 로 맞춘다).
                    log.warning("vnew.gen.real_cut_trim_failed pid=%s cut=%s err=%s", pid, cut.get("cutId"),
                                type(trim_exc).__name__)
            clips.append(clip)
        merged = clips[0] if len(clips) == 1 else _concat(clips)
    del pieces
    attempt = int((take.get("real") or {}).get("attempt") or 1)
    key = f"{_seg_output_prefix(owner, pid, gen_id, str(take.get('id') or index) + '-real-cuts', attempt).rstrip('/')}/merged.mp4"
    STORE.put_bytes(key, merged, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)
    del merged

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, "real", claim):
            raise _Discard()
        state = _previz_cells(g)[index]["real"]
        state.update(status="completed", outputKey=key, error="", claim="", claimedAt="")
        _supersede_unused(state, set(), KLING_CUTS_REPLACED)  # 작업 169: 통 테이크 Kling 이 대신한 옛 조각 기록

    _commit(owner, pid, mutator)
    log.info("vnew.gen.real_cut_merged pid=%s take=%s cuts=%d", pid, take.get("id"), len(cuts))


# ================================================================ 작업 134: 끝난 실사 회차 다시 이어 붙이기
#
# 컷 단위 실사 회차의 병합본이 깨졌을 때(형식이 다른 컷을 스트림 복사로 이음) 새 생성 없이 남아 있는 컷 결과로 다시 잇는다. 유료 호출 0.

_REAL_CUTS_MERGED = re.compile(r"/([^/]+)-real-cuts-(\d+)/merged\.mp4$")


def _is_qa_cut_key(key: str, cut_id: str) -> bool:
    """그 컷 결과 키가 QA 로 다시 만든 쪽(출력 접두사 꼬리 "-qa", _submit_real_cut tag)인지."""
    return f"-real-{cut_id}-qa-" in str(key or "")


def _real_cut_list(cuts) -> list[dict]:
    """칸 fallback.cuts → 이어 붙인 컷 목록 [{cutId, outputKey, qa}](완료·키 있는 것만, 컷 순서)."""
    out: list[dict] = []
    for cut in cuts or []:
        if isinstance(cut, dict) and cut.get("status") == "completed" and cut.get("outputKey"):
            cut_id, key = str(cut.get("cutId") or ""), str(cut["outputKey"])
            out.append({"cutId": cut_id, "outputKey": key, "qa": _is_qa_cut_key(key, cut_id)})
    return out


def _find_real_cut_key(owner: str, pid: str, gen_id: str, take_id: str, cut_id: str, attempt: int) -> str:
    """S3 에서 그 컷 결과(…/output.mp4)를 찾는다. QA 로 다시 만든 쪽(<take>-real-<cutId>-qa-<attempt>/)을 먼저, 없으면 첫 결과
    (<take>-real-<cutId>-<attempt>/). 여럿이면 키 이름 순 마지막. 없으면 ""."""
    for tag in ("-qa", ""):
        prefix = _seg_output_prefix(owner, pid, gen_id, f"{take_id}-real-{cut_id}{tag}", attempt).rstrip("/") + "/"
        keys = sorted(str(item.get("key") or "") for item in STORE.list_keys(prefix, bucket=vpoc_settings.VIDEO_BUCKET)
                      if str(item.get("key") or "").endswith("/output.mp4"))
        if keys:
            return keys[-1]
    return ""


def _run_n(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def remerge_real_cuts(owner: str, pid: str, gen_id: str, run_n: int) -> dict:
    """끝난 실사 회차 run_n 을 새 생성 없이 다시 이어 붙인다(작업 134, 관리자 경로). 유료 호출·원장 0.

    회차 = 지금 회차(gen.realRun.n) 또는 gen.realHistory 의 n. 테이크마다 컷 결과 키를 기록(지금 회차는 칸 mergedCuts·fallback.cuts,
    이전 회차는 history units[].cuts)에서 찾고, 없으면 테이크 결과 키(…/<take>-real-cuts-<attempt>/merged.mp4)의 attempt 로 S3 에서
    찾는다(_find_real_cut_key, QA 쪽 우선). 컷 길이는 _real_cut_seconds(기록, 없으면 콘티 컷). 컷 단위가 아닌 테이크는 테이크 결과를
    그 길이 그대로 쓴다. 전부 _conform_concat 으로 맞춰 이어 final-real-r{n}-fixed.mp4 에 둔다(원래 병합본은 그대로 남는다).
    기록: 지금 회차는 finalReal{key=새 키, finalKeyBroken=원래 키, status done}·칸 real.mergedCuts, 이전 회차는 그 항목
    finalKey·finalKeyBroken·status done·units[].cuts. finalKeyBroken 은 처음 고칠 때의 원래 키를 지킨다.
    반환 {n, key, finalKeyBroken, bytes, takes[{id, cuts[{cutId, outputKey, qa}]}]}.
    """
    where = "vnew.generate.remerge"
    n = _run_n(run_n)
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if n <= 0 or not gen_id or str(gen.get("genId") or "") != gen_id or gen.get("mode") != "previz-take":
        raise VpocError(where, "RunNotFound", "그 실사 회차를 찾을 수 없습니다.", 404)
    current = _run_n((gen.get("realRun") or {}).get("n")) == n
    if current:
        if gen.get("status") == "running" and not _gen_stale(gen):
            raise VpocError(where, "RealRunning", "영상 생성이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.", 409)
        rows = [(str(u.get("id") or ""), u.get("real") if isinstance(u.get("real"), dict) else {})
                for u in gen.get("units") or [] if isinstance(u, dict)]
        old_key = str((gen.get("finalReal") or {}).get("key") or "")
    else:
        item = next((h for h in gen.get("realHistory") or [] if isinstance(h, dict) and _run_n(h.get("n")) == n), None)
        if item is None:
            raise VpocError(where, "RunNotFound", "그 실사 회차를 찾을 수 없습니다.", 404)
        rows = [(str(u.get("id") or ""), u) for u in item.get("units") or [] if isinstance(u, dict)]
        old_key = str(item.get("finalKey") or "")
    if not rows:
        raise VpocError(where, "NoClips", "이어 붙일 영상이 없습니다.", 409)
    takes = {str(u.get("id") or ""): u for u in gen.get("units") or [] if isinstance(u, dict)}
    pieces: list[tuple[bytes, float]] = []
    used: list[dict] = []
    for take_id, state in rows:
        if state.get("status") != "completed":
            raise VpocError(where, "RunIncomplete", f"테이크 {take_id} 이 끝나지 않은 회차입니다.", 409)
        take = takes.get(take_id)
        seconds = {str(c.get("cutId") or ""): _real_cut_seconds(c) for c in (_take_cut_units(plan, take) if take else [])}
        fallback_cuts = [c for c in (state.get("fallback") or {}).get("cuts") or [] if isinstance(c, dict)] if current else []
        seconds.update({str(c.get("cutId") or ""): _real_cut_seconds(c) for c in fallback_cuts})
        listed = (state.get("mergedCuts") or _real_cut_list(fallback_cuts)) if current else state.get("cuts")
        cuts = [{"cutId": str(r.get("cutId") or ""), "outputKey": str(r["outputKey"]),
                 "qa": _is_qa_cut_key(str(r["outputKey"]), str(r.get("cutId") or ""))}
                for r in listed or [] if isinstance(r, dict) and r.get("outputKey")]
        take_key = str(state.get("outputKey") or "")
        match = _REAL_CUTS_MERGED.search(take_key)
        if not cuts and match:  # 기록에 컷 목록이 없다(작업 134 전 회차) → S3
            for unit in _take_cut_units(plan, take) if take else []:
                cut_id = str(unit.get("cutId") or "")
                key = _find_real_cut_key(owner, pid, gen_id, take_id, cut_id, int(match.group(2)))
                if not key:
                    raise VpocError(where, "CutMissing", f"테이크 {take_id} 컷 {cut_id} 결과를 찾지 못했습니다.", 409)
                cuts.append({"cutId": cut_id, "outputKey": key, "qa": _is_qa_cut_key(key, cut_id)})
            if not cuts:
                raise VpocError(where, "CutMissing", f"테이크 {take_id} 의 컷을 찾지 못했습니다.", 409)
        if cuts:
            for cut in cuts:
                pieces.append((_fetch_bytes(cut["outputKey"], bucket=vpoc_settings.VIDEO_BUCKET), seconds.get(cut["cutId"], 0.0)))
        elif take_key:
            pieces.append((_fetch_bytes(take_key, bucket=vpoc_settings.VIDEO_BUCKET), 0.0))
        else:
            raise VpocError(where, "NoClips", f"테이크 {take_id} 결과가 없습니다.", 409)
        used.append({"id": take_id, "cuts": cuts})
    merged = _conform_concat(pieces)
    del pieces
    key = final_real_fixed_key(owner, pid, gen_id, n)
    STORE.put_bytes(key, merged, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)
    size = len(merged)
    del merged
    by_take = {t["id"]: t["cuts"] for t in used if t["cuts"]}
    box = {"broken": old_key}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        changed = VpocError(where, "RunChanged", "회차 기록이 바뀌었습니다. 다시 눌러 주세요.", 409)
        if str(g.get("genId") or "") != gen_id:
            raise changed
        if current:
            if _run_n((g.get("realRun") or {}).get("n")) != n:
                raise changed
            final = dict(g.get("finalReal") or {})
            box["broken"] = str(final.get("finalKeyBroken") or "") or old_key
            final.update(status="done", key=key, error="", n=n, finalKeyBroken=box["broken"])
            g["finalReal"] = final
            for unit in g.get("units") or []:
                if isinstance(unit, dict) and isinstance(unit.get("real"), dict) and by_take.get(str(unit.get("id") or "")):
                    unit["real"]["mergedCuts"] = [dict(c) for c in by_take[str(unit.get("id") or "")]]
            return
        entry = next((h for h in g.get("realHistory") or [] if isinstance(h, dict) and _run_n(h.get("n")) == n), None)
        if entry is None:
            raise changed
        box["broken"] = str(entry.get("finalKeyBroken") or "") or old_key
        entry.update(status="done", finalKey=key, finalKeyBroken=box["broken"])
        for unit in entry.get("units") or []:
            if isinstance(unit, dict) and by_take.get(str(unit.get("id") or "")):
                unit["cuts"] = [dict(c) for c in by_take[str(unit.get("id") or "")]]

    _commit(owner, pid, mutator)
    log.info("vnew.gen.real_remerged pid=%s n=%d takes=%d cuts=%d qa=%d bytes=%d", pid, n, len(used),
             sum(len(t["cuts"]) for t in used), sum(1 for t in used for c in t["cuts"] if c["qa"]), size)
    return {"n": n, "key": key, "finalKeyBroken": box["broken"], "bytes": size, "takes": used}


def real_fallback_max(plan: dict, gen: dict) -> dict | None:
    """확인창 안내: 거절되면 컷 단위 Kling 으로 바뀔 때의 최대 금액. 테이크마다 max(Seedance 테이크, Σ컷(시작 그림 + Kling 길이 × 단가)).

    거절은 0 이므로 한 테이크의 실제 금액은 둘 중 하나다. 2단계를 쓸 수 없으면(끔·모델 없음·통 테이크 아님) None.
    """
    gen = gen if isinstance(gen, dict) else {}
    if not vnew_settings.VNEW_TAKE_REAL_LIKENESS_LADDER or gen.get("mode") != "previz-take":
        return None
    model = _kling_cut_model()
    if not model:
        return None
    endpoint, caps = model
    per_second = _previz_model_unit_price(endpoint)
    still = float(vpoc_seedream.unit_usd())
    seedance = _real_take_model(gen)  # 작업 156: 실사 테이크 모델
    resolution = str(gen.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION)
    total = 0.0
    qa_on = bool(vnew_settings.VNEW_REAL_CUT_QA)
    # 작업 133: QA 켬이면 컷마다 다시 만들기 1번(시작 그림 + Kling)과 QA 텍스트 2번(추정 토큰)까지 넣는다.
    tries, qa_usd = (2, 2 * _real_cut_qa_usd()) if qa_on else (1, 0.0)
    for unit in gen.get("units") or []:
        cut_usd = sum(tries * (still + per_second * _kling_duration(caps, float(c.get("seconds") or 0))) + qa_usd
                      for c in _take_cut_units(plan, unit))
        total += max(_take_usd(seedance, float(unit.get("seconds") or 0), resolution), cut_usd)
    out = {"usd": round(total, 2), "model": endpoint}
    if qa_on:
        out["qa"] = True  # 화면 문구 "QA로 다시 만든 컷 포함 최대 $X"
    return out


def _real_cut_qa_usd() -> float:
    """QA 텍스트 호출 1번 추정 금액(VNEW_REAL_CUT_QA_EST_TOKENS × 대본 분석 모델 단가, ledger.charge_text 와 같은 단가표)."""
    tokens_in, tokens_out = (int(v) for v in vnew_settings.VNEW_REAL_CUT_QA_EST_TOKENS)
    opus = "opus" in str(plan_mod._script_model())  # noqa: SLF001
    per_in = vpoc_settings.OPUS_USD_PER_MTOK_IN if opus else vpoc_settings.TEXT_USD_PER_MTOK_IN
    per_out = vpoc_settings.OPUS_USD_PER_MTOK_OUT if opus else vpoc_settings.TEXT_USD_PER_MTOK_OUT
    return tokens_in / 1_000_000 * float(per_in) + tokens_out / 1_000_000 * float(per_out)


def _maybe_finalize_previz(owner: str, pid: str, gen_id: str) -> None:
    """프리비즈 모드 병합·상태 확정(컷 단위).

    - 실사·웹툰 각각 모든 컷 completed 면 concat → finalReal/finalWebtoon.
    - 어느 한쪽이 전부 끝(completed/failed)이고 진행 중이 없으면 더 기다리지 않는다.
    - 실사·웹툰 모두 더 진행할 일이 없으면 done(두 final 다 done) 또는 partial(실패가 있으면).
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
        return
    cells = _previz_cells(gen)
    if not cells:
        return

    for variant in ("real", "webtoon"):
        states = [seg[variant]["status"] for seg in cells]
        # 통 테이크(설계자 2026-10-26): 실사 칸은 skipped — concat 대상이 아니다(완료로 본다).
        if states and all(s == "skipped" for s in states):
            continue
        all_done = all(s == "completed" for s in states)
        final_field = "finalReal" if variant == "real" else "finalWebtoon"
        if all_done and (gen.get(final_field) or {}).get("status") not in ("done", "running", "skipped"):
            _do_concat(owner, pid, gen_id, variant)

    # 상태 확정.
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        return

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or g.get("status") != "running":
            raise _Discard()
        segs = _previz_cells(g)
        reals = [s["real"]["status"] for s in segs]
        webs = [s["webtoon"]["status"] for s in segs]
        pending = any(x in ("waiting", "queued", "preparing", "submitted") for x in reals + webs)
        if pending:
            return
        # 더 진행할 일이 없다. 실패가 하나라도 있으면 partial, 아니면 done.
        any_fail = any(x in ("failed", "cancelled") for x in reals + webs)
        # finalReal/finalWebtoon 이 done 이거나 skipped(통 테이크 실사)면 그 쪽은 '끝'으로 본다.
        # 통 테이크(previz-take): 실사 칸은 skipped 라 fr 은 항상 skipped=끝. 웹툰이 done 이면 gen done
        # 이어야 한다(설계자 2026-10-26, 067 — 실사 skipped·웹툰 completed 인데 partial 로 남던 버그).
        fr_status = (g.get("finalReal") or {}).get("status")
        fw_status = (g.get("finalWebtoon") or {}).get("status")
        if "running" in (fr_status, fw_status):
            return  # 다른 드라이버가 합치는 중 — 끝난 뒤 그 드라이버가 상태를 확정한다(partial 오표시 방지)
        fr_done = fr_status in ("done", "skipped")
        fw_done = fw_status in ("done", "skipped")
        if any_fail or not (fr_done and fw_done):
            g["status"] = "partial"
        else:
            g["status"] = "done"

    try:
        _mutate_locked(owner, pid, mutator)
    except _Discard:
        return


# ---------------------------------------------------------------- 프리비즈 payload · prompt


# 마네킹 색(hex) → 사람이 읽는 색 이름(프롬프트의 "The {color} mannequin"). 팔레트 이름을 먼저
# 쓰고(previz_schema.PALETTE_NAME_BY_HEX), 팔레트에 없는 옛 데이터(임의 hex)는 RGB 거리로 가장
# 가까운 기본 색 이름을 고른다. 설계자 2026-10-22: 절대 hex 를 프롬프트에 넣지 않는다.
#
# 기본 색 사전(이름 → RGB). 영상 모델이 확실히 아는 평범한 색 단어만 둔다. 팔레트 밖 hex(예:
# #00e5ff=cyan, #ff8a00=orange)를 사람이 읽는 색으로 근사하는 데 쓴다.
_BASIC_COLORS = (
    ("black", (0, 0, 0)),
    ("white", (255, 255, 255)),
    ("grey", (128, 128, 128)),
    ("red", (220, 20, 20)),
    ("orange", (245, 130, 50)),
    ("yellow", (255, 225, 25)),
    ("green", (60, 180, 75)),
    ("cyan", (70, 220, 250)),
    ("teal", (70, 153, 144)),
    ("blue", (67, 99, 216)),
    ("navy", (0, 0, 128)),
    ("purple", (145, 30, 180)),
    ("magenta", (240, 50, 230)),
    ("pink", (250, 190, 212)),
    ("brown", (154, 99, 36)),
    ("lime", (191, 239, 69)),
    ("lavender", (220, 190, 255)),
    ("olive", (128, 128, 0)),
    ("maroon", (128, 0, 0)),
)


def _hex_to_rgb(hex_color: str):
    """#rrggbb → (r, g, b). 짧거나 잘못된 값이면 None."""
    text = str(hex_color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _color_name(hex_color: str) -> str:
    """#rrggbb → 색 이름. 팔레트에 있으면 그 이름, 없으면 RGB 거리로 가장 가까운 기본 색 이름.

    설계자 2026-10-22: 영상 모델이 hex 를 못 읽어 인물이 뒤바뀌므로 **절대 hex 를 돌려주지 않는다**.
    값이 비거나 못 읽으면 "grey".
    """
    key = str(hex_color or "").strip().lower()
    named = previz_schema.PALETTE_NAME_BY_HEX.get(key)
    if named:
        return named
    rgb = _hex_to_rgb(key)
    if rgb is None:
        return "grey"
    best_name = "grey"
    best_dist = None
    for name, (r, g, b) in _BASIC_COLORS:
        dist = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _blank_line(text: str) -> bool:
    """대사가 사실상 비었는지(…·공백·점만). silent 판정에 쓴다."""
    stripped = str(text or "").strip()
    if not stripped:
        return True
    # "…", "… ….", "...", "⋯" 처럼 점·말줄임만 남은 것.
    for ch in ("…", ".", "⋯", " ", "\t", "\u200b"):
        stripped = stripped.replace(ch, "")
    return stripped == ""


def _panel_ints(value) -> list[int]:
    """패널 번호 목록(정수만, 순서 유지)."""
    return [int(p) for p in (value or []) if isinstance(p, (int, float)) and not isinstance(p, bool)]


def _line_owner(entry: dict, conti_cuts: list[dict]) -> str:
    """작업 157: 대본 줄 하나가 속하는 컷 id(모르면 "").

    패널이 겹치는 컷이 하나면 그 컷. 여럿이면 ① 콘티 dialogueKo 에 그 대사가 든 컷이 하나뿐이면 그 컷(test3 e24 "한 번 시험 해
    보자고." 패널 20 = cut-12 [19,20]·cut-13 [20] → 콘티대로 cut-13), ② 아니면 그 줄의 첫 패널을 담은 첫 컷, ③ 그것도 없으면
    가장 많이 겹치는 컷(같으면 앞 컷).
    """
    entry_panels = _panel_ints(entry.get("panels"))
    if not entry_panels:
        return ""
    hits = [c for c in conti_cuts if set(entry_panels) & set(_panel_ints(c.get("panels")))]
    if len(hits) == 1:
        return str(hits[0].get("id") or "")
    text = "".join(str(entry.get("lineKo") or "").split())
    if text and not _blank_line(text):
        said = [c for c in hits if text in "".join(str(c.get("dialogueKo") or "").split())]
        if len(said) == 1:
            return str(said[0].get("id") or "")
    first = min(entry_panels)
    for cut in hits:
        if first in _panel_ints(cut.get("panels")):
            return str(cut.get("id") or "")
    best, best_n = "", 0
    for cut in hits:
        n = len(set(entry_panels) & set(_panel_ints(cut.get("panels"))))
        if n > best_n:
            best, best_n = str(cut.get("id") or ""), n
    return best


def _spoken_pairs(lines: list[dict]) -> list[tuple[str, str]]:
    return [(str(ln.get("speaker") or ""), str(ln.get("lineKo") or "")) for ln in lines
            if not ln.get("silent") and str(ln.get("lineKo") or "").strip()]


def _cut_lines(plan: dict, cut: dict) -> list[dict]:
    """한 컷의 대사 귀속 → [{speaker, lineKo, silent}] (_cut_lines_src + 작업 157 한 줄 한 컷).

    작업 157: 패널이 여러 컷에 걸친 대본 줄은 **한 컷**에만 둔다(_line_owner: 콘티 dialogueKo 에 그 대사가 든 컷 → 첫 패널을 담은
    첫 컷 → 가장 많이 겹치는 컷). test3 cut-01 [0,1]·cut-02 [1,2] 에서 패널 1 의 "…아냐. 재밌네. 계속 해 봐." 가 두 컷에 들어가 시간이 어긋났다.
    콘티 dialogueKo 로 읽은 컷도 같다: 앞 컷과 패널이 겹치면(_split_cut_pair) 앞 컷에 이미 있는 대사는 뺀다.
    단, 한 패널을 나눈 컷이 대사까지 앞 컷과 똑같으면(작업 147 "still") 그대로 둔다 — _dialogue_runs 가 말을 한 번만 쓴다.
    plan 의 콘티에 없는 컷(검사용 단독 호출)은 지금 그대로다.
    """
    lines, sources = _cut_lines_src(plan, cut)
    conti_cuts = [c for c in (plan.get("conti") or {}).get("cuts") or [] if isinstance(c, dict)]
    cut_id = str(cut.get("id") or "")
    pos = next((i for i, c in enumerate(conti_cuts) if cut_id and str(c.get("id") or "") == cut_id), -1)
    if pos < 0 or not lines:
        return lines
    prev = conti_cuts[pos - 1] if pos > 0 else None
    held = prev is not None and _split_cut_pair(prev, cut)
    prev_lines = _cut_lines_src(plan, prev)[0] if held else []
    if held and _spoken_pairs(lines) and _spoken_pairs(prev_lines) == _spoken_pairs(lines):
        return lines  # 작업 147: 나눈 컷의 같은 대사(계속 말하는 중)
    if sources is not None:
        kept = [ln for ln, entry in zip(lines, sources) if _line_owner(entry, conti_cuts) in ("", cut_id)]
        return kept
    if held:
        before = {text for _, text in _spoken_pairs(prev_lines)}
        return [ln for ln in lines if ln.get("silent") or str(ln.get("lineKo") or "") not in before]
    return lines


def _cut_lines_src(plan: dict, cut: dict) -> tuple[list[dict], list[dict] | None]:
    """한 컷의 대사 귀속 → ([{speaker, lineKo}], 줄마다 대본 entry | None) (설계자 2026-10-22, ⑤ 대사 섞임 수정).

    - script.entries 중 그 컷 panels 와 **겹치는** entry 의 speaker·lineKo 를 순서대로 모은다.
    - 겹치는 entry 가 없으면 cut.dialogueKo 를 `이름: "…"` / 구분자(" / ")로 파싱한다(둘째 값 None).
    - 빈 대사("… ….", "…", 공백)는 speaker 유지하고 lineKo="" + silent=True 로 표시한다.
    반환: ([{speaker, lineKo, silent}], 대본에서 읽었으면 줄과 같은 순서의 entry 목록).
    """
    cut_panels = set(int(p) for p in (cut.get("panels") or []) if isinstance(p, (int, float)) and not isinstance(p, bool))
    out: list[dict] = []

    entries = (plan.get("script") or {}).get("entries") or []
    matched = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_panels = set(int(p) for p in (entry.get("panels") or []) if isinstance(p, (int, float)) and not isinstance(p, bool))
        if cut_panels and entry_panels and (cut_panels & entry_panels):
            matched.append(entry)

    if matched:
        for entry in matched:
            speaker = str(entry.get("speaker") or "").strip()
            line = str(entry.get("lineKo") or "").strip()
            silent = _blank_line(line)
            out.append({"speaker": speaker, "lineKo": "" if silent else line, "silent": silent})
            if _is_thought(entry):
                # 지시서 117: 속마음 대사(입 다문 채 목소리만). spoken 줄은 키를 더하지 않는다(지금 모양 그대로).
                out[-1]["thought"] = True
        # 겹치는 entry 가 모두 빈 대사(speaker·lineKo 없음)면 컷 dialogueKo 로 보강한다(옛 대본 호환).
        if any((not o.get("silent")) or o.get("speaker") for o in out):
            return out, matched
        out = []

    # 겹치는 entry 가 없거나(또는 모두 빈 entry) → cut.dialogueKo 를 파싱한다.
    dialogue = str(cut.get("dialogueKo") or "").strip()
    if not dialogue:
        return [], None
    # " / " 로 여러 대사 구분. 각 조각은 "이름: \"…\"" 모양일 수 있다.
    pieces = [p.strip() for p in dialogue.split(" / ") if p.strip()]
    if not pieces:
        pieces = [dialogue]
    for piece in pieces:
        speaker = ""
        line = piece
        # "이름: 대사" 로 화자를 가른다(첫 콜론 기준, 한국어 콜론 포함).
        for sep in (": ", "：", ":"):
            if sep in piece:
                head, tail = piece.split(sep, 1)
                head = head.strip()
                # 이름이 너무 길면(문장) 화자가 아니다.
                if head and len(head) <= 20 and '"' not in head and "“" not in head:
                    speaker = head
                    line = tail.strip()
                    break
        # 바깥 따옴표 제거.
        line = line.strip().strip('"').strip("“”").strip()
        silent = _blank_line(line)
        out.append({"speaker": speaker, "lineKo": "" if silent else line, "silent": silent})
    return out, None


# 작업 147: 대사 줄이기 단위(문장) 경계 — 끝 문장부호(. ! ?, 뒤 닫는 따옴표 포함) 뒤 공백, 또는 " / " 구분자.
# 말줄임표 "…" 만으로는 문장을 끊지 않는다("음… 이제 …" 는 한 문장).
_DIALOGUE_BREAK = re.compile(r"[.!?。！？]+[\"”’」]*(?=\s)|(?=\s+/\s+)")
# 작업 147: 문장을 시작할 수 없는 한국어 어미·조사(한 곳). 다음 컷 대사 첫 어절이 이것들로만 이루어지면 앞 컷 말의 끝이다.
DIALOGUE_CONT_ENDINGS = ("요", "죠", "다", "까", "는데", "고", "서", "지", "네", "는", "을", "를")
_DIALOGUE_ELLIPSES = ("…", "..", "⋯")
_DIALOGUE_TERMINALS = (".", "!", "?", "。", "！", "？")


def _fit_dialogue(text: str, limit: int = 0) -> str:
    """프롬프트에 넣는 대사 한 줄(작업 147, 대사를 넣는 모든 프롬프트가 이 함수 하나를 쓴다).

    limit <= 0 이거나 글자 수가 limit 이하면 그대로. 넘으면 **뒤 문장부터 통째로** 뺀다(첫 문장은 늘 남는다).
    문장 안에서 자르지 않고, 원문에 없는 마침표를 붙이지 않는다(남는 글은 늘 원문의 앞부분이다). 따옴표 짝이
    맞지 않는 자리(`이름: "가. 나." / "다"` 의 "가." 뒤)에서는 끊지 않는다.
    """
    body = str(text or "").strip()
    if limit <= 0 or len(body) <= limit:
        return body
    ends = [m.end() for m in _DIALOGUE_BREAK.finditer(body)]
    candidates = []
    for end in ends:
        head = body[:end].rstrip()
        if head and head.count('"') % 2 == 0 and head.count("“") == head.count("”"):
            candidates.append(head)
    if not candidates:
        return body
    fitting = [c for c in candidates if len(c) <= limit]
    return fitting[-1] if fitting else candidates[0]


def _dialogue_open_end(text: str) -> bool:
    """앞 조각이 말을 끝내지 않았는가: 끝 문장부호가 없거나 말줄임표("…"·"..")로 끝난다."""
    tail = str(text or "").rstrip().rstrip("\"”’」").rstrip()
    if not tail:
        return False
    if tail.endswith(_DIALOGUE_ELLIPSES):
        return True
    return not tail.endswith(_DIALOGUE_TERMINALS)


def _dialogue_cont_start(text: str) -> bool:
    """다음 조각이 앞말의 끝인가: "…" 로 시작하거나 첫 어절(한글 이어진 글자)이 DIALOGUE_CONT_ENDINGS 로만 이루어진다.

    "요…?" "까요?" "는데요…" 는 이어짐, "지금" "고마워" "다들" 처럼 어미 뒤에 다른 글자가 붙은 낱말은 새 문장이다.
    """
    head = str(text or "").lstrip().lstrip("\"“‘「").lstrip()
    if head.startswith(_DIALOGUE_ELLIPSES):
        return True
    word = re.match(r"[가-힣]+", head)
    if not word:
        return False
    word = word.group(0)
    ok = [False] * (len(word) + 1)
    ok[0] = True
    for i in range(len(word)):
        if ok[i]:
            for ending in DIALOGUE_CONT_ENDINGS:
                if word.startswith(ending, i):
                    ok[i + len(ending)] = True
    return ok[len(word)]


def _split_cut_pair(a: dict, b: dict) -> bool:
    """한 패널을 두 컷으로 나눈 사이인가(같은 패널이 겹치거나, id 가 cut07-1 · cut07-2 처럼 뒤 번호만 다르다)."""
    def _panels(cut: dict) -> set:
        return {int(p) for p in (cut.get("panels") or []) if isinstance(p, (int, float)) and not isinstance(p, bool)}

    pa, pb = _panels(a), _panels(b)
    if pa and pb and (pa & pb):
        return True
    ia, ib = str(a.get("id") or ""), str(b.get("id") or "")
    base_a, base_b = re.sub(r"-\d+$", "", ia), re.sub(r"-\d+$", "", ib)
    return bool(base_a) and base_a == base_b and base_a != ia and base_b != ib


def _dialogue_runs(conti_cuts: list[dict], plan: dict | None = None) -> list[list[dict]]:
    """컷을 넘어 이어지는 한 사람의 말(작업 147). 컷 순서대로, 컷마다 말한 줄 목록을 돌려준다.

    줄: {speaker, lineKo(이 컷 몫), thought, run, utterance}. run 은
      ""      — 보통 줄(지금 그대로),
      "start" — 다음 컷으로 이어지는 말의 첫 컷. utterance = 이어진 말 전체,
      "cont"  — 앞 컷 말을 이어 끝맺는 조각(lineKo = 이 컷 몫),
      "still" — 한 패널을 나눈 컷의 같은 대사(말은 첫 컷에 한 번만, 이 컷은 계속 말하는 중).
    잇는 조건(모두): 같은 화자 · 이웃 컷 · 앞 조각이 끝 문장부호 없이(또는 "…"·".." 로) 끝남 · 다음 조각이 "…" 나
    DIALOGUE_CONT_ENDINGS 로 시작. 이웃한 나눈 컷(_split_cut_pair)의 대사가 글자까지 같으면 "still".
    한 컷 안에서 화자 없는 조각(`이름: "가" / "나"` 의 "나")은 앞 화자의 말로 이어 붙인다. 속마음 여부는 첫 조각을 따른다.
    """
    per_cut: list[list[dict]] = []
    for cut in conti_cuts or []:
        merged: list[dict] = []
        for ln in _cut_lines(plan or {}, cut or {}):
            text = str(ln.get("lineKo") or "").strip()
            if ln.get("silent") or not text:
                continue
            speaker = str(ln.get("speaker") or "").strip()
            if not speaker and merged and merged[-1]["speaker"]:
                speaker = merged[-1]["speaker"]  # `이름: "가" / "나"` 의 "나" = 같은 사람
            merged.append({"speaker": speaker, "lineKo": text, "thought": bool(_is_thought(ln)), "run": "",
                           "utterance": ""})
        per_cut.append(merged)
    for k in range(1, len(per_cut)):
        prev, cur = per_cut[k - 1], per_cut[k]
        if not prev or not cur:
            continue
        same = [(p["speaker"], p["lineKo"]) for p in prev] == [(c["speaker"], c["lineKo"]) for c in cur]
        if same and _split_cut_pair(conti_cuts[k - 1] or {}, conti_cuts[k] or {}):
            for p, c in zip(prev, cur):
                if not p["run"]:
                    p["run"], p["utterance"] = "start", p["lineKo"]
                c["run"], c["thought"], c["source"] = "still", p["thought"], p.get("source") or p
            continue
        a, b = prev[-1], cur[0]
        if a["run"] == "still":
            continue
        if a["speaker"] and a["speaker"] == b["speaker"] and _dialogue_open_end(a["lineKo"]) \
                and _dialogue_cont_start(b["lineKo"]):
            head = a.get("source")
            if head is None:
                # 첫 컷: 끝에 붙은 같은 화자의 줄들(한 컷 안 연달은 말)을 한 줄로 묶어 이어지는 말의 머리로 둔다.
                at = len(prev) - 1
                while at > 0 and prev[at - 1]["speaker"] == a["speaker"] and not prev[at - 1]["run"]:
                    at -= 1
                head = prev[at]
                if not head["run"]:
                    head["lineKo"] = " ".join(p["lineKo"] for p in prev[at:])
                    head["run"], head["utterance"] = "start", head["lineKo"]
                    del prev[at + 1:]
            sep = " " if b["lineKo"].lstrip().startswith(_DIALOGUE_ELLIPSES) else ""
            head["utterance"] = head["utterance"].rstrip() + sep + b["lineKo"].lstrip()
            b["run"], b["thought"], b["source"] = "cont", head["thought"], head
    for lines in per_cut:
        for ln in lines:
            ln.pop("source", None)
    return per_cut


def _scene_by_cut(plan: dict) -> dict:
    """cutId → 그 컷이 속한 프리비즈 scene. 배우 색·등장 여부를 찾을 때 쓴다."""
    out: dict[str, dict] = {}
    for scene in (plan.get("previz") or {}).get("scenes") or []:
        for entry in scene.get("timeline") or []:
            cid = str(entry.get("cutId") or "")
            if cid:
                out[cid] = scene
    return out


def _segment_actors(plan: dict, seg: dict) -> list[dict]:
    """이 구간에 등장하는 프리비즈 배우 목록(화면 노출 순 ≈ 등장 컷 수 많은 순).

    각 배우: {id, name, color, colorName, charId, appearanceEn, exposure(등장 컷 수)}. 구간의 cutIds
    를 돌며 각 컷이 속한 scene 의 timeline 에서 그 컷의 actors(등장 인물)를 모으고, scene.actors
    에서 색·이름·charId 를 찾는다.
    """
    scene_by_cut = _scene_by_cut(plan)
    exposure: dict[str, int] = {}
    actor_meta: dict[str, dict] = {}
    cut_ids = _seg_cut_ids(seg)
    for cut_id in cut_ids:
        scene = scene_by_cut.get(str(cut_id))
        if not scene:
            continue
        actors_by_id = {str(a.get("id")): a for a in (scene.get("actors") or [])}
        for entry in scene.get("timeline") or []:
            if str(entry.get("cutId") or "") != str(cut_id):
                continue
            for ca in entry.get("actors") or []:
                aid = str(ca.get("id") or "")
                if not aid:
                    continue
                exposure[aid] = exposure.get(aid, 0) + 1
                if aid not in actor_meta and aid in actors_by_id:
                    meta = actors_by_id[aid]
                    actor_meta[aid] = {
                        "id": aid,
                        "name": str(meta.get("name") or ""),
                        "color": str(meta.get("color") or ""),
                        "colorName": _color_name(str(meta.get("color") or "")),
                        "charId": str(meta.get("charId") or ""),
                        "appearanceEn": str(meta.get("appearanceEn") or ""),
                    }
    out = []
    for aid, meta in actor_meta.items():
        meta = dict(meta)
        meta["exposure"] = exposure.get(aid, 0)
        out.append(meta)
    # 화면에 오래·크게 나오는 순(등장 컷 수 내림차순, 같으면 이름).
    out.sort(key=lambda m: (-int(m.get("exposure") or 0), m.get("name") or ""))
    return out


def _char_by_id(owner: str, plan: dict) -> dict:
    """charId → _char_entries 항목."""
    out: dict[str, dict] = {}
    for c in _char_entries(owner, plan.get("input") or {}):
        cid = str(c.get("charId") or "")
        if cid:
            out[cid] = c
    return out


def _fit_ref(jpeg: bytes) -> bytes:
    """참고 이미지 비율 맞춤 + 긴 변 축소(⑤ OOM 완화, 설계자 2026-10-23).

    payload 에 넣는 모든 참고 이미지(캐릭터 정면·후면·원작 웹툰 컷)는 이 함수를 지난다. fit_ratio
    로 비율을 kling 허용 범위(0.4~2.5)에 맞춘 뒤 긴 변을 VNEW_REF_MAX_PX(기본 1024) 이하로 줄여
    JPEG q=85 로 다시 인코딩한다. base64·PIL 봉우리 메모리를 줄인다.
    """
    out = imaging.fit_and_downscale(jpeg, max_px=int(vnew_settings.VNEW_REF_MAX_PX or 0))
    _ref_note_fit(jpeg, out)  # 지시서 116: _RefTrace 안이면 맞춘 바이트 → 원래 S3 키(기록용, 바이트는 붙잡지 않는다)
    return out


def _fit_b64(jpeg: bytes) -> str:
    """참고 이미지 비율(0.4~2.5)을 맞추고 긴 변을 줄인 뒤 base64 로(⑤ OOM 완화, 설계자 2026-10-23)."""
    return base64.b64encode(_fit_ref(jpeg)).decode("ascii")


def _load_jpeg(key: str) -> bytes | None:
    if not key:
        return None
    try:
        data = STORE.get_bytes(key)
    except Exception:  # noqa: BLE001
        return None
    _ref_note_load(key, data)  # 지시서 116: _RefTrace 안이면 읽은 바이트 → S3 키(기록용)
    return data


def _seg_cut_ids(seg: dict) -> list[str]:
    """unit(cutId 하나) · 옛 seg(cutIds 여럿) 모두에서 컷 id 목록을 뽑는다."""
    ids = seg.get("cutIds")
    if ids:
        return [str(c) for c in ids]
    cid = seg.get("cutId")
    return [str(cid)] if cid else []


def _previz_payload(owner: str, pid: str, plan: dict, seg: dict, variant: str, *, caps: dict | None = None,
                    endpoint: str = "", size_cap: int = 0) -> dict:
    """한 컷·한 그림체의 제출 payload(elements · imageUrls · prompt)를 만든다(설계자 2026-10-22).

    모델별 어댑터(caps):
    - elementsField 가 있으면(또는 caps 가 비어 기본 Kling): 지금 방식 — elements[{frontal_image_url,
      reference_image_urls:[후면]}] + CAST 의 @Element 매핑. 웹툰은 imageUrls 1장(원작 웹툰 컷).
    - elements 가 없고 imageListField 가 있으면: 참고 이미지 = 그 컷에 보이는 인물의 기본 복장 정면
      (실사/웹툰) 순서대로 + (웹툰) 그림체 참고 1장, imageListMax 이내. CAST 는 "Image {n} is {name}"
      형식(이미지 번호 = 실제 순서), 마네킹 색 매핑 "The {color} mannequin becomes the person in Image {n}".
    elements+imageUrls 합계는 PREVIZ_MAX_INPUTS(4) 이내. charId 없는 인물은 CAST 색 문장에만.
    작업 167: endpoint·size_cap 을 주면 프롬프트 예산이 _cut_prompt_budget(Kling 여유·크기 거절 재제출)을 따른다. 안 주면 지금 그대로.
    """
    caps = caps or {}
    fit = {"endpoint": endpoint, "caps": caps, "size_cap": size_cap} if (endpoint or size_cap) else None
    # elementsField 가 명시돼 있으면 elements 모드. imageListField 만 있으면 imageList 모드.
    # caps 가 비면(기본 Kling 미등록) elements 모드로 본다(기존 동작 유지).
    use_image_list = bool(caps.get("imageListField")) and not caps.get("elementsField")
    if use_image_list:
        return _previz_payload_image_list(owner, pid, plan, seg, variant, caps, fit=fit)
    return _previz_payload_elements(owner, pid, plan, seg, variant, fit=fit)


def _previz_payload_elements(owner: str, pid: str, plan: dict, seg: dict, variant: str, *, fit: dict | None = None) -> dict:
    """elements 방식(기존 Kling O3, 설계자 2026-10-22). @Element 매핑."""
    is_real = variant == "real"
    max_elements = vnew_settings.PREVIZ_REAL_MAX_ELEMENTS if is_real else vnew_settings.PREVIZ_WEBTOON_MAX_ELEMENTS
    max_image_urls = 0 if is_real else vnew_settings.PREVIZ_WEBTOON_MAX_IMAGE_URLS

    actors = _segment_actors(plan, seg)
    char_by_id = _char_by_id(owner, plan)

    elements: list[dict] = []
    # CAST 줄 재료: {colorName, name, elementRef(@Element{n} 또는 ""), appearance}.
    cast: list[dict] = []

    element_index = 0
    for actor in actors:
        color_name = actor.get("colorName") or "grey"
        name = actor.get("name") or "a character"
        char_id = actor.get("charId") or ""
        char = char_by_id.get(char_id) if char_id else None
        if char and element_index < max_elements:
            front_key = char.get("realFrontKey") if is_real else char.get("webtoonFrontKey")
            back_key = char.get("realBackKey") if is_real else char.get("webtoonBackKey")
            front = _load_jpeg(str(front_key or ""))
            if front:
                element_index += 1
                element = {"frontal_image_url": _fit_ref(front)}
                back = _load_jpeg(str(back_key or ""))
                if back:
                    element["reference_image_urls"] = [_fit_ref(back)]
                else:
                    element["reference_image_urls"] = []
                elements.append(element)
                appearance = ((char.get("appearanceEn") or "") + " " + (char.get("outfitEn") or "")).strip()
                cast.append({"colorName": color_name, "name": name, "elementRef": f"@Element{element_index}", "appearance": appearance})
                continue
        # charId 없음(또는 정면 로드 실패/정원 초과) → CAST 색 문장만.
        cast.append({"colorName": color_name, "name": name, "elementRef": "", "appearance": actor.get("appearanceEn") or ""})

    image_urls: list[bytes] = []
    if max_image_urls > 0 and len(elements) + max_image_urls <= vnew_settings.PREVIZ_MAX_INPUTS:
        panel = _segment_panel_jpeg(owner, pid, plan, seg)
        if panel is not None:
            image_urls.append(_fit_ref(panel))

    prompt = _cut_prompt(plan, seg, variant, cast=cast, has_image_ref=bool(image_urls), **(fit or {}))
    # 합계 4 넘지 않게(안전) 자른다.
    total_allowed = vnew_settings.PREVIZ_MAX_INPUTS
    if len(elements) > max_elements:
        elements = elements[:max_elements]
    if len(elements) + len(image_urls) > total_allowed:
        image_urls = image_urls[: max(0, total_allowed - len(elements))]

    return {"elements": elements, "imageUrls": image_urls, "prompt": prompt}


def _previz_payload_image_list(owner: str, pid: str, plan: dict, seg: dict, variant: str, caps: dict, *,
                               fit: dict | None = None) -> dict:
    """imageList 방식(설계자 2026-10-22). 참고 이미지 = 보이는 인물 정면 순서 + (웹툰) 그림체 참고 1장.

    elements 는 비우고, image_urls(imageListField) 에 참고 이미지를 담는다. CAST 는 "Image {n} is
    {name}" 번호와 "The {color} mannequin becomes the person in Image {n}" 매핑을 쓴다. 번호는 실제
    image_urls 순서와 일치시킨다(이미지 1부터). imageListMax 이내.
    """
    is_real = variant == "real"
    max_images = int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX)

    actors = _segment_actors(plan, seg)
    char_by_id = _char_by_id(owner, plan)

    image_urls: list[bytes] = []
    cast: list[dict] = []
    # (웹툰) 그림체 참고 1장 자리를 남겨 둔다.
    style_slots = 1 if not is_real else 0
    image_budget = max(0, max_images - style_slots)

    for actor in actors:
        color_name = actor.get("colorName") or "grey"
        name = actor.get("name") or "a character"
        char_id = actor.get("charId") or ""
        char = char_by_id.get(char_id) if char_id else None
        placed = False
        if char and len(image_urls) < image_budget:
            front_key = char.get("realFrontKey") if is_real else char.get("webtoonFrontKey")
            front = _load_jpeg(str(front_key or ""))
            if front:
                image_urls.append(_fit_ref(front))
                image_no = len(image_urls)  # 1부터, 실제 순서와 일치
                appearance = ((char.get("appearanceEn") or "") + " " + (char.get("outfitEn") or "")).strip()
                cast.append({"colorName": color_name, "name": name, "imageNo": image_no, "appearance": appearance})
                placed = True
        if not placed:
            cast.append({"colorName": color_name, "name": name, "imageNo": None, "appearance": actor.get("appearanceEn") or ""})

    # (웹툰) 그림체 참고 1장(원작 웹툰 컷). 인물 이미지 뒤에 둔다(번호는 인물에만 쓴다).
    has_style_ref = False
    if style_slots and len(image_urls) < max_images:
        panel = _segment_panel_jpeg(owner, pid, plan, seg)
        if panel is not None:
            image_urls.append(_fit_ref(panel))
            has_style_ref = True

    prompt = _cut_prompt(plan, seg, variant, cast=cast, has_image_ref=has_style_ref, **(fit or {}))
    # imageListMax 이내로 자른다(안전).
    if len(image_urls) > max_images:
        image_urls = image_urls[:max_images]

    return {"elements": [], "imageUrls": image_urls, "prompt": prompt}


def _location_name_en(plan: dict, seg: dict) -> str:
    """이 컷(또는 구간 첫 컷)의 장소 이름(영어). 콘티 cut.locationEn 을 쓴다."""
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    for cut_id in _seg_cut_ids(seg):
        cut = cuts.get(str(cut_id))
        if cut and cut.get("locationEn"):
            return str(cut.get("locationEn"))
    return "the scene"


def _brief_bundle(plan: dict) -> dict:
    """저장된 촬영 지시서(previz.shotBrief). 없으면 빈 dict."""
    brief = (plan.get("previz") or {}).get("shotBrief")
    return brief if isinstance(brief, dict) else {}


def _scene_brief_text(plan: dict, seg: dict) -> str:
    """이 컷이 속한 장소의 촬영 지시서 scene 공통 문장(sceneEn). 장소 id 로 찾는다."""
    brief = _brief_bundle(plan)
    scene_map = brief.get("scene") if isinstance(brief.get("scene"), dict) else {}
    if not scene_map:
        return ""
    # 컷 → 장소 id: 프리비즈 scene 의 locationId 를 쓴다(cut 이 속한 scene).
    first_ids = _seg_cut_ids(seg)
    scene = _scene_by_cut(plan).get(str(first_ids[0] if first_ids else ""))
    loc_id = str((scene or {}).get("locationId") or "")
    if loc_id and loc_id in scene_map:
        return str(scene_map.get(loc_id) or "")
    # 장소 id 를 못 맞추면 첫 scene 문장을 쓴다(장소가 하나뿐일 때).
    if len(scene_map) == 1:
        return str(next(iter(scene_map.values())) or "")
    return ""


def _cut_brief(plan: dict, cut_id: str) -> dict:
    """이 컷의 촬영 지시서(shotEn·blockingEn·actionEn·moodEn·backgroundEn·dialogue). 없으면 빈 dict."""
    brief = _brief_bundle(plan)
    cuts = brief.get("cuts") if isinstance(brief.get("cuts"), dict) else {}
    item = cuts.get(str(cut_id))
    return item if isinstance(item, dict) else {}


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _trim_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 바이트 상한에 맞춰 뒤에서 자른다(글자 경계 유지)."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    # 멀티바이트 글자가 잘리지 않게 뒤에서 깎는다.
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    # 마지막 리드바이트도 불완전하면 제거.
    if cut and (cut[-1] & 0x80) and not (cut[-1] & 0x40):
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore")


def _split_sentences(text: str) -> list[str]:
    """영어 문장을 마침표·물음표·느낌표 뒤 공백 기준으로 나눈다(간단 규칙, 설계자 2026-10-22).

    각 조각은 끝 구두점을 포함한다. 소수점·약어를 완벽히 가르지는 않지만, 촬영 지시서 문장 단위
    줄이기에는 충분하다. 빈 조각은 버린다.
    """
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return []
    parts = _re.split(r"(?<=[.!?])\s+", body)
    return [p.strip() for p in parts if p.strip()]


def _drop_tail_sentences(text: str, over_bytes: int, *, keep_first: bool = False) -> str:
    """UTF-8 바이트가 over_bytes 만큼 넘칠 때, 뒤 문장부터 하나씩 지워 그만큼(이상)만 줄인다.

    설계자 2026-10-22: 각 단계는 '넘친 만큼만' 문장 단위로 줄이고, 한도 안에 들어오면 즉시 멈춘다.
    keep_first=True 면 첫 문장은 절대 지우지 않는다(ACTION 첫 문장 보호). 더 지울 게 없으면 남은
    것을 그대로 돌려준다(상위에서 다른 단계로 넘어간다).
    """
    if over_bytes <= 0:
        return text
    sentences = _split_sentences(text)
    if not sentences:
        return text
    floor = 1 if keep_first else 0
    removed = 0
    while len(sentences) > floor and removed < over_bytes:
        dropped = sentences.pop()
        removed += len((" " + dropped).encode("utf-8"))
    return " ".join(sentences)


def _scene_summary(scene_en: str, max_chars: int = 350) -> str:
    """장소 문단(sceneEn)의 앞 2~3문장을 뽑아 장소 요약을 만든다(최대 max_chars 자, 설계자 2026-10-22).

    장소 문단 전체를 컷 프롬프트에 넣지 않고, 공간 요약만 넣어 ACTION 이 밀리지 않게 한다.
    """
    body = str(scene_en or "").strip()
    if not body:
        return ""
    sentences = _split_sentences(body)
    picked: list[str] = []
    for sentence in sentences[:3]:
        candidate = (" ".join(picked + [sentence])).strip()
        if len(candidate) > max_chars:
            break
        picked.append(sentence)
    summary = " ".join(picked).strip()
    if not summary:
        # 첫 문장이 이미 max_chars 를 넘으면 글자 단위로 자른다.
        summary = body[:max_chars].strip()
    return summary[:max_chars].strip()


def _cut_prompt(plan: dict, seg: dict, variant: str, *, cast: list[dict], has_image_ref: bool,
                endpoint: str = "", caps: dict | None = None, size_cap: int = 0) -> str:
    """컷 프롬프트(영상 모델 전달문, 설계자 2026-10-22, ⑤). 영어, UTF-8 2,500바이트 이내.

    순서 고정: ASSETS(자료 번호 목록) → @Video1 안내 → CAST → SHOT → BLOCKING → ACTION → MOTION(short) → MOOD → SETTING → HARMONY(short) → DIALOGUE → CONTINUITY.
    SETTING = 컷별 backgroundEn + 장소 요약(sceneEn 앞 2~3문장, 최대 350자). 장소 문단 전체는 넣지 않는다.
    촬영 지시서(shotBrief)를 쓰되, 없으면 그 자리는 비운다(ASSETS·CAST·SHOT·CONTINUITY 는 반드시 유지).

    바이트 초과 시 줄이는 순서(각 단계는 **넘친 만큼만** 문장 단위로 줄이고, 한도 안에 들어오면 즉시
    멈춘다): ① SETTING 의 장소 요약 → ② SETTING 의 backgroundEn → ③ MOOD → ④ BLOCKING 뒤쪽 문장
    → ⑤ DIALOGUE 의 deliveryEn → ⑥ SHOT 뒤쪽 문장 → ⑦ 마지막에만 ACTION 뒤쪽 문장 → ⑧ (작업 147) 대사 25(_fit_dialogue).
    ASSETS·CAST·CONTINUITY·ACTION 첫 문장은 절대 지우지 않는다. **한도 안이면 아무 것도 지우지 않는다.**

    vnew-live-fx RA5(VNEW_FX 켬일 때만): 웹툰은 ACTION 다음 `FX: {기호 문장; …}.`, 실사는 `EXPRESSION: {실사 표현}.`
    (배경 기호처럼 빈 값만 있으면 줄 없음). 컷 moodEn 은 MOOD 맨 앞(비면 넣지 않는다, moodKo 는 쓰지 않는다 — 결정 6).
    이름은 CAST 처럼 한국 역할명 그대로다. CONTINUITY 의 "The mannequin colors …" 앞에 손 두 개·머리색·무늬 위치 문장
    (CUT_RULE_BODY, ≤ 90B). 줄이기: ⓪ bodyDrop(묘사 문장 빼기, 장소 요약보다 먼저 — 090 필수 수정 2) → ① … ⑥ →
    ⑥-1 FX 짧은 형(웹툰만) → ⑦ ACTION. 끔이면 지금과 바이트 단위로 같다(fx-i).
    돌려주기 전에 로그 `vnew.gen.cut_prompt cut= variant= bytes= steps=`(적용 단계, 없으면 none)를 한 줄 남긴다.

    작업 127(VNEW_FX 켬일 때만): 맨 앞에 고정부 1줄(_fixed_head: 화풍·배경·overall tone, 같은 장소면 통 테이크와 같은
    글자)을 두고 예산에서 미리 뺀다(어떤 줄이기에도 남는다). SETTING 의 장소 요약(sceneEn)은 고정부가 대신해 넣지 않는다.
    CAST 다음에 "Only A and B appear."(보이는 인물만),
    MOOD 는 컷 moodEn 만(촬영 지시서 moodEn 은 쓰지 않는다), FX 는 대상 인물(fxWho)이 있으면 그 사람에게만.

    작업 167: endpoint(·caps)·size_cap 을 주면 예산 = _cut_prompt_budget(Kling 2500 여유 · fal 크기 거절 뒤 재제출). 그 예산이
    PREVIZ_PROMPT_TARGET 보다 작을 때만 ⑦ ACTION 앞에 CAST 외형 문장 줄이기(castAppearance — @Element·Image 매핑은 남는다)를 더한다.
    안 주면(또는 예산이 그대로면) 지금과 바이트 단위로 같다.
    """
    is_real = variant == "real"
    fx_on = bool(vnew_settings.VNEW_FX)
    cut_ids = _seg_cut_ids(seg)
    cut_id = cut_ids[0] if cut_ids else ""
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    cut = cuts_by_id.get(str(cut_id)) or {}
    brief = _cut_brief(plan, cut_id)
    cut_fx = vnew_fx.fx_list(cut.get("fx"), vnew_fx.FX_MAX) if fx_on else []
    cut_mood = vnew_fx.clean_text(cut.get("moodEn"), vnew_fx.MOOD_EN_MAX) if fx_on else ""
    # 작업 127: 기호 대상 인물 · 맨 앞 고정부(화풍·배경·overall tone) · 보이는 인물만 "Only … appear". 켬일 때만.
    cut_who = vnew_fx.fx_who(cut.get("fxWho"), vnew_fx.cut_names(cut)) if fx_on else {}
    fixed_head = _fixed_head(plan, str(cut.get("locationEn") or ""), real=is_real) if fx_on else ""
    cast_names = [str(m.get("name") or "").strip() for m in cast if str(m.get("name") or "").strip()]
    only_line = _only_appear(cast_names) if fx_on else ""

    if is_real:
        style = "photorealistic live-action film"
    elif has_image_ref:
        style = (
            "a Korean webtoon animation in exactly the drawing style of @Image1 "
            "(line, coloring, shading; not generic anime)"
        )
    else:
        style = "a Korean webtoon animation (hand-drawn line, coloring and shading; not generic anime)"

    head = (
        "@Video1 is a rough 3D previz (blocking and camera only). "
        "Re-render this single shot as " + style + "."
    )

    # CAST — 그 컷에 보이는 인물만. 마네킹 색은 "The {color} mannequin is @ElementN, {name}" 에만
    # 쓰고, 외형은 캐릭터 데이터(appearance = appearanceEn + outfitEn)로 쓴다. "mannequin" 이 든
    # 외형 문장은 지우고, 문장 끝 마침표 중복("..")은 없앤다(설계자 2026-10-22).
    def _clean_appearance(text: str) -> str:
        cleaned = []
        for sentence in _split_sentences(text):
            if "mannequin" in sentence.lower():
                continue
            cleaned.append(sentence)
        body = " ".join(cleaned).strip()
        # 끝 마침표 중복 제거.
        while body.endswith(".."):
            body = body[:-1]
        return body.strip()

    cast_heads: list[str] = []  # 작업 167: 매핑 부분(외형 앞). 외형은 cast_apps(줄이기에서 뒤 문장부터 뺀다).
    cast_apps: list[str] = []
    # ASSETS 줄 재료(설계자 2026-10-22): 번호로 매긴 입력 자료 목록. elements 모델은 @Element{n},
    # 이미지 목록 모델은 Image {n}, 웹툰 그림체 참고는 @Image{k}. 모델이 자료를 번호로 읽게 한다.
    asset_refs: list[str] = []
    for member in cast:
        color = member.get("colorName") or "grey"
        name = member.get("name") or "a character"
        ref = member.get("elementRef") or ""
        image_no = member.get("imageNo")
        appearance = _clean_appearance(member.get("appearance") or "")
        if image_no:
            # imageList 모델(설계자 2026-10-22): "Image {n} is {name}" + 마네킹 색 매핑.
            # "The {color} mannequin becomes the person in Image {n}".
            sentence = (
                f"Image {image_no} is {name}; the {color} mannequin becomes the person in Image {image_no}"
            )
            asset_refs.append(f"Image {image_no} = {name} reference sheet")
        elif ref:
            sentence = f"The {color} mannequin is {ref}, {name}"
            asset_refs.append(f"{ref} = {name} reference sheet")
        else:
            sentence = f"The {color} mannequin is {name}"
        cast_heads.append(sentence)
        cast_apps.append(appearance)

    def _cast_line(apps: list[str]) -> str:
        cast_sentences = []
        for head_part, appearance in zip(cast_heads, apps):
            sentence = head_part
            if appearance:
                sentence += f": {appearance}"
            # 끝 마침표 하나만.
            sentence = sentence.rstrip(".").rstrip() + "."
            cast_sentences.append(sentence)
        return "CAST — only these people are in this shot: " + (" ".join(cast_sentences) if cast_sentences else "no one is visible.")

    cast_line = _cast_line(cast_apps)

    # ASSETS — 맨 앞 줄(설계자 2026-10-22, 뉴테이크 프롬프트 규칙). 자료를 번호로 나열한다.
    # @Video1 = 이 샷의 러프 3D 프리비즈, @Element{n}/Image{n} = 각 인물 참고 시트,
    # @Image{k} = 원작 웹툰 컷(그림체 참고, 웹툰만).
    asset_parts = ["@Video1 = rough 3D previz of this shot (blocking and camera only)"]
    asset_parts.extend(asset_refs)
    if has_image_ref and not is_real:
        # 웹툰: 그림체 참고 원작 컷은 인물 참고 다음 번호(@Image{k}).
        style_no = len([m for m in cast if m.get("imageNo")]) + 1
        asset_parts.append(f"@Image{style_no} = original webtoon panel for drawing style")
    assets_line = "ASSETS: " + "; ".join(asset_parts) + "."

    # SHOT — 촬영 지시서 카메라 문장. 없으면 최소 안내(@Video1 따름)만.
    shot_en = str(brief.get("shotEn") or "").strip()
    if not shot_en:
        shot_en = "Match the framing, lens, camera height and angle exactly as shown in @Video1."
    blocking_en = str(brief.get("blockingEn") or "").strip()
    # 시선(눈동자 방향) 한 문장을 BLOCKING 끝에 붙인다(설계자 2026-10-26, 065-E).
    gaze_en = _gaze_text(_cut_eyelines(plan, cut))
    if gaze_en:
        blocking_en = (blocking_en + " " + gaze_en + ".").strip() if blocking_en else (gaze_en + ".")
    action_en = str(brief.get("actionEn") or "").strip()
    mood_en = str(brief.get("moodEn") or "").strip()
    if fx_on:
        # 작업 127: MOOD 는 컷 moodEn 만(촬영 지시서 moodEn 은 장면 전체 분위기를 옮겨 와 모든 컷에 붙었다).
        # 장면 전체 톤은 고정부 overall tone 1번이다. 컷 moodEn 이 비면 MOOD 줄이 없다.
        mood_en = cut_mood
    elif cut_mood:
        # vnew-live-fx RA5.1: 컷 분위기를 MOOD 맨 앞에(줄이기는 뒤 문장 = 지시서 moodEn 부터 뗀다).
        mood_en = (cut_mood.rstrip(".") + ". " + mood_en).strip()
    background_en = str(brief.get("backgroundEn") or "").strip()
    scene_en = _scene_brief_text(plan, seg).strip()

    # DIALOGUE — _cut_lines 로 귀속한 대사(색/화자 정확). 촬영 지시서 deliveryEn 을 붙인다.
    lines = _cut_lines(plan, cut)
    delivery_by_speaker: dict[str, str] = {}
    for d in (brief.get("dialogue") or []):
        if isinstance(d, dict):
            sp = str(d.get("speaker") or "").strip()
            delivery_by_speaker[sp] = str(d.get("deliveryEn") or "").strip()
    spoken = [line for line in lines if not line.get("silent") and str(line.get("lineKo") or "").strip()]
    any_spoken = bool(spoken)

    def _dialogue_line(with_delivery: bool, limit: int = 0) -> str:
        if not any_spoken:
            return "DIALOGUE: No one speaks; keep mouths closed."
        parts = []
        for line in spoken:
            speaker = str(line.get("speaker") or "").strip()
            # 작업 147: 대사는 자르지 않는다(맨 마지막 단계에서만 _fit_dialogue 로 뒤 문장부터 통째로).
            text = _fit_dialogue(str(line.get("lineKo") or "").strip(), limit)
            piece = (f'{speaker or "A character"} (inner thought, voice-over only — mouth stays closed, lips do not move): "{text}"'
                     if _is_thought(line) else f'{speaker or "A character"} (Korean, lips move): "{text}"')
            if with_delivery and not _is_thought(line):
                # 지시서 118a: 속마음 줄에는 deliveryEn 을 붙이지 않는다(옛 촬영 지시서는 말하기 연기를 적었다).
                delivery = delivery_by_speaker.get(speaker, "")
                if delivery:
                    piece += f" — {delivery}"
            piece += "."
            parts.append(piece)
        return "DIALOGUE: " + " ".join(parts)

    # CONTINUITY — @Video1 재현 + 인물 교체 + 임의 추가 금지 + 고퀄 실사/웹툰 마감(설계자 2026-10-22,
    # 뉴테이크 실전 프롬프트). 웹툰판은 live-action 대신 원작 웹툰 그림체로 마감한다.
    if is_real:
        finish_clause = (
            "Turn the rough 3D frame into a high-end live-action shot: natural skin texture, detailed "
            "fabric, realistic lighting and shadows, and a richly detailed real location."
        )
    else:
        finish_clause = (
            "Turn the rough 3D frame into the original webtoon's drawing style: natural skin texture, "
            "detailed fabric, realistic lighting and shadows, and a richly detailed location, all drawn "
            "in that webtoon style."
        )
    continuity_head = (
        "CONTINUITY: Reproduce @Video1's composition, camera angle, camera movement, positions, blocking, "
        "actions, timing and background structure as exactly as possible; mannequin stiffness is NOT "
        "acting — perform the ACTION naturally. Replace each mannequin with the referenced person, keeping "
        "face, hairstyle, body type, costume and accessories identical to the reference in every shot. "
    )
    continuity_tail = (
        "The mannequin colors are only placeholders — dress each person exactly as their reference image, "
        "not in the mannequin color; no mannequins remain. Do not add any action, person or scene that is "
        "not in @Video1. " + finish_clause + " "
        "No text, no subtitles, no speech bubbles."
    )

    def _continuity(with_body: bool) -> str:
        # vnew-live-fx RA5.2: 손 두 개·머리색·무늬 위치 문장은 "The mannequin colors" 앞(끔·bodyDrop 이면 없음).
        return continuity_head + (CUT_RULE_BODY if with_body else "") + continuity_tail

    # vnew-live-fx RA5.1: 웹툰 FX 줄(긴·짧은 형) / 실사 EXPRESSION 줄(빈 값 제외, 짧은 형 없음). 끔이면 cut_fx 가 비어 줄이 없다.
    fx_full = vnew_fx.who_sentences(cut_fx, cut_who, cast_names, real=is_real)
    fx_short_texts = [] if is_real else vnew_fx.who_sentences(cut_fx, cut_who, cast_names, short=True)

    def _fx_line(short: bool) -> str:
        if not fx_full:
            return ""
        if is_real:
            return "EXPRESSION: " + "; ".join(fx_full) + "."
        return "FX: " + (", ".join(fx_short_texts) if short else "; ".join(fx_full)) + "."

    # SETTING = 컷별 backgroundEn + 장소 요약(sceneEn 앞 2~3문장, 최대 350자). 장소 문단 전체는 X.
    scene_summary_full = _scene_summary(scene_en, 350)
    if fixed_head:
        # 작업 127: 장소 전체 설명은 고정부 BACKGROUND 1문장이 맡는다(장소 요약은 줄이기 첫 단계라 컷마다 달라졌다).
        # SETTING 에는 이 프레이밍에서 보이는 배경(지시서 backgroundEn)만 남긴다.
        scene_summary_full = ""

    # MOTION·HARMONY short(설계자 2026-10-26 재지시 v2). 컷 단위(Kling)에서도 마네킹 흔들림을 따라
    # 하지 말고 살아 움직이는 연기·2차 동작·패럴랙스를 하라고, 인물·배경 빛을 맞추라고 지시한다. 둘 다
    # **지우지 않는 등급** — 줄이기 단계에서 건드리지 않는다(기존 대사·gaze 등이 먼저 줄어든다).
    # 지시서 118a: 이 컷 대사 줄의 목소리(속마음만·섞임이면 입 모양 구절을 바꾼다, spoken·none 은 지금 문구).
    motion_short = _motion_line(False, _prompt_voices(spoken))
    harmony_short = _harmony_line(plan, False)

    def _setting_line(bg: str, scene_summary: str) -> str:
        parts = [p for p in (scene_summary, bg) if p]
        body = " ".join(parts).strip()
        return ("SETTING: " + body) if body else ""

    def _build(*, scene_summary: str, background: str, mood: str, blocking: str,
               with_delivery: bool, shot: str, action: str, fx_short: bool = False, body: bool = False,
               dialogue_limit: int = 0, apps: list[str] | None = None) -> str:
        shot_line = ("SHOT: " + shot) if shot else "SHOT: Match the framing, lens, camera height and angle exactly as shown in @Video1."
        blocking_line = ("BLOCKING: " + blocking) if blocking else ""
        action_line = ("ACTION: " + action) if action else ""
        mood_line = ("MOOD: " + mood) if mood else ""
        setting_line = _setting_line(background, scene_summary)
        dlg = _dialogue_line(with_delivery, dialogue_limit)
        # 순서(설계자 2026-10-26 재지시 v2): … ACTION → MOTION(short) → MOOD → SETTING → HARMONY(short)
        # → DIALOGUE → CONTINUITY. MOTION·HARMONY short 는 고정 블록(지우지 않는 등급).
        # vnew-live-fx: FX/EXPRESSION 줄은 ACTION 바로 다음(끔이면 빈 값이라 빠진다).
        blocks = [
            assets_line, head, cast_line if apps is None else _cast_line(apps), only_line, shot_line, blocking_line,
            action_line, _fx_line(fx_short),
            motion_short, mood_line, setting_line, harmony_short, dlg, _continuity(body),
        ]
        return "\n".join(b for b in blocks if b)

    budget = vnew_settings.PREVIZ_PROMPT_TARGET  # 바이트 상한(2500).
    if fixed_head:
        # 작업 127: 고정부는 줄이지 않는다(예산에서 미리 빼고 _done 이 맨 앞에 붙인다).
        budget -= _utf8_len(fixed_head) + 1

    # 현재 값(줄이기 단계마다 하나씩 바꾼다). ACTION 첫 문장은 절대 지우지 않는다.
    state = {
        "scene_summary": scene_summary_full,
        "background": background_en,
        "mood": mood_en,
        "blocking": blocking_en,
        "with_delivery": True,
        "shot": shot_en,
        "action": action_en,
        "fx_short": False,
        "body": fx_on,
        "dialogue_limit": 0,  # 작업 147: 대사 상한(0 = 없음, byteTrim 바로 앞에서만 25)
        "apps": None,  # 작업 167: CAST 외형(None = 지금 그대로, 좁은 예산의 castAppearance 단계에서만 목록)
    }

    def _current() -> str:
        return _build(
            scene_summary=state["scene_summary"],
            background=state["background"],
            mood=state["mood"],
            blocking=state["blocking"],
            with_delivery=state["with_delivery"],
            shot=state["shot"],
            action=state["action"],
            fx_short=state["fx_short"],
            body=state["body"],
            dialogue_limit=state["dialogue_limit"],
            apps=state["apps"],
        )

    # 작업 167: Kling 2500 여유·크기 거절 재제출 예산(태그 수는 줄이기 전 전체 프롬프트에서 센다 — 줄이기는 ASSETS·CAST 매핑을 지우지 않는다).
    tight = False
    if endpoint or size_cap:
        fit_budget = _cut_prompt_budget(endpoint, caps, _prompt_tags(fixed_head + "\n" + _current()), size_cap)
        if fit_budget < int(vnew_settings.PREVIZ_PROMPT_TARGET):
            budget = fit_budget - ((_utf8_len(fixed_head) + 1) if fixed_head else 0)  # 고정부는 지금처럼 예산에서 미리 뺀다
            tight = True

    # 090 필수 수정 2: 적용한 줄이기 단계를 로그 한 줄로 남긴다(운영 확인용, 프롬프트 글자는 바꾸지 않는다).
    applied: list[str] = []
    cut_label = vnew_fx.clean_text(cut_id, 40) or "-"

    def _done(text: str) -> str:
        if fixed_head:
            text = fixed_head + "\n" + text
        log.info(
            "vnew.gen.cut_prompt cut=%s variant=%s bytes=%d steps=%s",
            cut_label, "real" if is_real else "webtoon", _utf8_len(text), ",".join(applied) or "none",
        )
        return text

    # 1) 한도 안이면 아무 것도 지우지 않는다(설계자 2026-10-22, 지금 버그 수정).
    prompt = _current()
    if _utf8_len(prompt) <= budget:
        return _done(prompt)

    # ⓪ (vnew-live-fx, 090 필수 수정 2) 묘사 규칙 문장을 장소 요약보다 먼저 뺀다. 끔이면 문장이 없어 건너뛴다.
    if state["body"]:
        state["body"] = False
        applied.append("bodyDrop")
        if _utf8_len(_current()) <= budget:
            return _done(_current())

    # 줄이는 순서. 각 단계는 '넘친 만큼만' 문장 단위로 줄이고, 한도 안이면 즉시 멈춘다.
    # ① SETTING 의 장소 요약 → ② SETTING 의 backgroundEn → ③ MOOD → ④ BLOCKING 뒤쪽 문장 →
    # ⑤ DIALOGUE 의 deliveryEn → ⑥ SHOT 뒤쪽 문장. (⑦ ACTION 은 마지막에만.)
    def _reduce_field(field: str, *, keep_first: bool = False, label: str = "") -> bool:
        """그 필드를 넘친 만큼 문장 단위로 줄인다. 한도 안에 들면 True. 프롬프트가 실제로 바뀌었으면 label 을 단계 로그에 적는다."""
        before = _current()
        over = _utf8_len(before) - budget
        if over <= 0:
            return True
        state[field] = _drop_tail_sentences(state[field], over, keep_first=keep_first)
        after = _current()
        if label and after != before:
            applied.append(label)
        return _utf8_len(after) <= budget

    # ① SETTING 장소 요약.
    if _reduce_field("scene_summary", label="settingSummary"):
        return _done(_current())
    # ② SETTING backgroundEn.
    if _reduce_field("background", label="settingBackground"):
        return _done(_current())
    # ③ MOOD.
    if _reduce_field("mood", label="mood"):
        return _done(_current())
    # ④ BLOCKING 뒤쪽 문장.
    if _reduce_field("blocking", label="blocking"):
        return _done(_current())
    # ⑤ DIALOGUE deliveryEn(대사 연기 설명) 제거.
    over = _utf8_len(_current()) - budget
    if over > 0:
        before = _current()
        state["with_delivery"] = False
        if _current() != before:
            applied.append("delivery")
        if _utf8_len(_current()) <= budget:
            return _done(_current())
    # ⑥ SHOT 뒤쪽 문장.
    if _reduce_field("shot", label="shot"):
        return _done(_current())
    # ⑥-1 (vnew-live-fx RA5.3) 웹툰 FX 줄을 짧은 형으로(실사 EXPRESSION 은 그대로). 기호가 없으면(끔 포함) 건너뛴다.
    if fx_short_texts:
        state["fx_short"] = True
        applied.append("fxShort")
        if _utf8_len(_current()) <= budget:
            return _done(_current())
    # ⑥-1-1 (작업 167, 좁은 예산만) CAST 외형 문장을 뒤 인물부터 뒤 문장부터 뺀다. 매핑("The red mannequin is @Element1, Kim")은 남는다.
    if tight and any(cast_apps):
        before = _current()
        state["apps"] = list(cast_apps)
        for i in reversed(range(len(cast_apps))):
            over = _utf8_len(_current()) - budget
            if over <= 0:
                break
            state["apps"][i] = _drop_tail_sentences(state["apps"][i], over)
        if _current() != before:
            applied.append("castAppearance")
        if _utf8_len(_current()) <= budget:
            return _done(_current())
    # ⑦ 마지막에만 ACTION 뒤쪽 문장(첫 문장은 절대 지우지 않는다).
    if _reduce_field("action", keep_first=True, label="action"):
        return _done(_current())
    # ⑧ (작업 147) 맨 마지막에만 대사: 줄마다 25자 안으로 뒤 문장부터 통째로 뺀다(첫 문장 유지, 문장 안 자르기 없음).
    if any_spoken:
        before = _current()
        state["dialogue_limit"] = 25
        if _current() != before:
            applied.append("dialogue")
        if _utf8_len(_current()) <= budget:
            return _done(_current())
    # 그래도 넘치면 전체를 바이트로 자른다(최후). CAST·CONTINUITY 가 이미 들어간 뼈대 기준.
    applied.append("byteTrim")
    return _done(_trim_utf8(_current(), budget))


def _previz_timeline_text(plan: dict, seg: dict) -> str:
    """구간 타임라인(간결): 시간 범위 + 말하는 사람(색 마네킹)·대사 + 짧은 감정.

    화면 구성(구도·카메라·컷)은 @Video1 만 따른다 → 컷의 promptEn·cameraEn 은 넣지 않는다.
    남기는 것:
    - 시간 범위("0-4s").
    - 그 시간에 말하는 사람: 색 마네킹 기준 "the {color} mannequin ({roleName})". 대사(dialogueKo
      40자 이내)를 함께.
    - 짧은 감정 1~3단어: conti/script 의 emotion(emotionKo) 이 있으면 그것, 없으면 생략. situationKo
      에서 고르지 않는다.
    """
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    # 이 구간 배우: 이름 → 색(colorName). 말하는 사람을 색 마네킹으로 가리키려고 쓴다.
    color_by_name: dict[str, str] = {}
    for actor in _segment_actors(plan, seg):
        name = str(actor.get("name") or "").strip()
        color = str(actor.get("colorName") or "").strip()
        if name and color:
            color_by_name.setdefault(name, color)
    lines = []
    start = 0.0
    for cut_id in seg.get("cutIds") or []:
        cut = cuts.get(str(cut_id))
        if not cut:
            continue
        seconds = float(cut.get("seconds") or 0)
        end = start + seconds
        dialogue = str(cut.get("dialogueKo") or "").strip()
        emotion = str(cut.get("emotionKo") or cut.get("emotionEn") or "").strip()
        speaker_name = ""
        for ch in cut.get("characters") or []:
            if ch.get("name"):
                speaker_name = str(ch.get("name"))
                break
        part = f"{int(start)}-{int(end)}s"
        if dialogue:
            color = color_by_name.get(speaker_name)
            if color:
                who = f"the {color} mannequin ({speaker_name})"
            elif speaker_name:
                who = speaker_name
            else:
                who = "the speaker"
            part += f' {who} speaks (Korean): "{_fit_dialogue(dialogue, 40)}"'  # 작업 147: 문장 단위(자르기 없음)
        if emotion:
            part += f" ({emotion})"
        lines.append(part + ".")
        start = end
    return " ".join(lines)


def _previz_prompt(plan: dict, seg: dict, variant: str, *, element_lines: list[str], extra_lines: list[str], has_image_ref: bool) -> str:
    """프리비즈 → 그림체 프롬프트(영어, 2,500자 이내). maxLength 자르기 재사용.

    실사: "photorealistic live-action film". 웹툰: "a Korean webtoon animation in exactly the same
    drawing style as @Image1". 마네킹 색 ↔ @Element 매핑·타임라인·연속성·"Do not add or remove anyone".
    """
    is_real = variant == "real"
    location = _location_name_en(plan, seg)
    place = _previz_place_sentence(plan, seg, location)

    if is_real:
        style = "photorealistic live-action film"
    elif has_image_ref:
        style = (
            "a Korean webtoon animation in exactly the same drawing style as @Image1 "
            "(line, coloring, shading; not generic anime)"
        )
    else:
        style = "a Korean webtoon animation (hand-drawn line, coloring and shading; not generic anime)"

    head = (
        "@Video1 is a rough 3D previz with colored mannequins. Re-render it as "
        + style + " set in " + place + ". Mannequins: "
    )
    mapping = " ".join(element_lines + extra_lines)
    timeline = _previz_timeline_text(plan, seg)
    timeline_text = (" Timeline: " + timeline) if timeline else ""
    tail = (
        " Camera angles, framing, cuts, positions and movements come ONLY from @Video1 — do not invent "
        "new shots. Keep exactly the same room, number of people, timing and light direction as @Video1. "
        "Do not add or remove anyone. Every colored mannequin must become a person; no mannequins, "
        "boxes or flat colored figures may remain. No text, no speech bubbles."
    )
    prompt = head + mapping + timeline_text + tail
    budget = _previz_prompt_budget()
    if len(prompt) > budget:
        # 머리(스타일·매핑)와 꼬리(연속성 지시)는 반드시 남기고 타임라인만 줄인다.
        fixed = head + mapping + tail
        room = max(0, budget - len(fixed) - len(" Timeline: "))
        trimmed_tl = vpoc_fal._trim_prompt(timeline, room) if room > 0 else ""
        prompt = head + mapping + ((" Timeline: " + trimmed_tl) if trimmed_tl else "") + tail
        if len(prompt) > budget:
            prompt = vpoc_fal._trim_prompt(prompt, budget)
    return prompt


def _previz_place_sentence(plan: dict, seg: dict, location: str) -> str:
    """장소를 한 문장으로: 이름 + 실내/실외 + 빛 방향.

    - 이름: location(콘티 cut.locationEn).
    - 실내/실외: 대본 setting.enclosed(닫힌 방=indoor, 열린 공간=outdoor).
    - 빛 방향: 그 장소 설계(design.lightEn) 한 문장(있으면).
    """
    script_setting = (plan.get("script") or {}).get("setting") or {}
    indoor = "indoor" if script_setting.get("enclosed") else "outdoor"
    light = ""
    loc = _location_by_name(plan, location)
    if loc:
        light = str((loc.get("design") or {}).get("lightEn") or "").strip()
    sentence = f"{location} ({indoor})"
    if light:
        sentence += f", {light.rstrip('.')}"
    return sentence


def _location_by_name(plan: dict, name_en: str) -> dict | None:
    """콘티 locationEn 에 맞는 plan["locations"] 항목(key 정규화 매칭). 없으면 None."""
    from . import locations as loc_mod  # noqa: PLC0415

    key = loc_mod.normalize_name(name_en)
    for loc in plan.get("locations") or []:
        if str(loc.get("key") or loc_mod.normalize_name(loc.get("nameEn") or "")) == key:
            return loc
    return None


# ---------------------------------------------------------------- 작업 127: 맨 앞 고정부(화풍 · 배경 · overall tone)
#
# 통 테이크와 컷 프롬프트 맨 앞에 같은 글자의 고정부를 둔다(참고 구현 compose._cuts_video_prompt 방식). 같은 장소면
# 바이트 단위로 같은 문장이고, 장소가 바뀔 때만 배경 문장이 바뀐다. 줄이기는 이 줄을 건드리지 않는다(예산에서 미리 뺀다).

# 대본 setting.moodKo → overall tone 영어 낱말(부분일치, 표 순서, 최대 3개). 한글은 프롬프트에 넣지 않는다.
_TONE_EN = (
    ("설레", "fluttering"), ("설렘", "fluttering"), ("로맨", "romantic"), ("달달", "sweet"), ("달콤", "sweet"),
    ("긴장", "tense"), ("불안", "uneasy"), ("어색", "awkward"), ("당황", "flustered"), ("코믹", "comic"),
    ("유쾌", "cheerful"), ("경쾌", "upbeat"), ("활기", "lively"), ("밝", "bright"), ("따뜻", "warm"),
    ("훈훈", "warm"), ("잔잔", "calm"), ("평온", "calm"), ("평화", "calm"), ("진지", "serious"), ("비장", "solemn"),
    ("슬", "sad"), ("우울", "gloomy"), ("감동", "moving"), ("분노", "angry"), ("공포", "eerie"), ("무서", "eerie"),
    ("신비", "mysterious"),
)
# 배경 문장에서 사람을 말하는 문장을 거른다(대본 layoutEn 은 누가 어디 서는지를 적는다).
_PERSON_WORDS = re.compile(
    r"\b(?:stands?|standing|sits?|sitting|seated|leans?|leaning|he|she|they|his|her|their|him|man|woman|men|women|"
    r"boy|girl|people|person|character|characters)\b",
    re.IGNORECASE,
)
FIXED_WEBTOON_STYLE = "Korean webtoon animation"
FIXED_REAL_STYLE = "photorealistic live-action film"
BACKGROUND_MAX = 260


def _overall_tone(plan: dict) -> str:
    """대본 setting.moodKo 를 짧은 영어 톤 1구절로(작업 127). 못 맞추면 ""."""
    mood_ko = str(((plan.get("script") or {}).get("setting") or {}).get("moodKo") or "")
    words: list[str] = []
    for needle, english in _TONE_EN:
        if needle in mood_ko and english not in words:
            words.append(english)
        if len(words) >= 3:
            break
    return ", ".join(words)


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _people_names(plan: dict) -> list[str]:
    """배경 문장에서 거를 인물 이름(입력 역할명·콘티 인물·대본 화자, 로마자 포함)."""
    names: list[str] = []
    raw = [item.get("roleName") for item in ((plan.get("input") or {}).get("characters") or []) if isinstance(item, dict)]
    for cut in (plan.get("conti") or {}).get("cuts") or []:
        raw.extend(c.get("name") for c in (cut.get("characters") or []) if isinstance(c, dict))
    raw.extend(e.get("speaker") for e in ((plan.get("script") or {}).get("entries") or []) if isinstance(e, dict))
    for value in raw:
        name = str(value or "").strip()
        for form in (name, _romanize_name(name) if name else ""):
            if form and form not in names:
                names.append(form)
    return names


def _background_en(plan: dict, location_en: str, *, real: bool = False) -> str:
    """장소의 사람 없는 배경 1문장(작업 127). 같은 plan·같은 장소면 언제나 같은 글자다.

    장소 시트(locations)의 사실 정리(facts)·설계(design)가 있으면 그것으로(장소 이름 + 실내/실외 + 벽·바닥·빛),
    없으면 장소 이름 + 대본 setting.layoutEn 중 사람이 없는 문장(그 장소가 대본 첫 장소일 때만). BACKGROUND_MAX 자.
    """
    setting = (plan.get("script") or {}).get("setting") or {}
    location = str(location_en or "").strip() or str(setting.get("locationEn") or "").strip() or "the scene"
    loc = _location_by_name(plan, location)
    design = (loc or {}).get("design") if isinstance((loc or {}).get("design"), dict) else {}
    facts = (loc or {}).get("facts") if isinstance((loc or {}).get("facts"), dict) else {}
    enclosed = _flag(design.get("enclosed")) if "enclosed" in design else _flag(setting.get("enclosed"))
    where = "indoor" if enclosed else "outdoor"
    bits: list[str] = []
    if loc and (facts or design):
        from . import locations as loc_mod  # noqa: PLC0415

        name = str(loc.get("nameEn") or "").strip() or location
        surfaces: list[str] = []
        for side in loc_mod.SIDES:
            wall = (facts.get("walls") or {}).get(side) or {}
            surface = " ".join(p for p in (str(wall.get("wallColorEn") or "").strip(),
                                           str(wall.get("wallMaterialEn") or "").strip()) if p)
            if surface and surface not in surfaces:
                surfaces.append(surface)
        if surfaces:
            bits.append("walls: " + " / ".join(surfaces[:2]))
        floor = str(facts.get("floorEn") or design.get("floorEn") or "").strip().rstrip(".")
        if floor:
            bits.append("floor: " + floor)
        light = str(facts.get("lightEn") or design.get("lightEn") or "").strip()
        if light:
            first = _split_sentences(light)
            bits.append("light: " + (first[0] if first else light).rstrip("."))
    else:
        name = location
        from . import locations as loc_mod  # noqa: PLC0415

        opening = str(setting.get("locationEn") or "").strip()
        same_as_opening = not opening or loc_mod.normalize_name(opening) == loc_mod.normalize_name(location)
        if not same_as_opening:
            # 대본 setting(실내/실외·배치)은 첫 장소 것이라 다른 장소에는 붙이지 않는다.
            where = ""
        if same_as_opening:
            people = _people_names(plan)
            for sentence in _split_sentences(str(setting.get("layoutEn") or "")):
                if vnew_fx.HANGUL.search(sentence) or _PERSON_WORDS.search(sentence):
                    continue
                if any(person and person in sentence for person in people):
                    continue
                bits.append(sentence.rstrip("."))
    body = (f"{name} ({where})" if where else name) + ("; " + "; ".join(bits) if bits else "")
    body = " ".join(body.split())
    if real:
        body = vnew_fx.strip_drawing_words(body)
    return _clip_clause(body, BACKGROUND_MAX)


# 작업 168: 잘린 장소 글 끝에 남으면 안 되는 낱말(전치사·접속사·관사).
_DANGLING_TAIL = re.compile(r"(?:[\s,;:/]+(?:with|of|and|in|on|at|under|or|to|for|by|from|into|onto|over|near|behind|beside|"
                            r"toward|towards|the|a|an))+$", re.IGNORECASE)


def _clip_clause(text: str, limit: int) -> str:
    """장소 글을 limit 자로(작업 168). 넘지 않거나 단어 경계 자른 자리가 원래 절 경계(; , . 또는 끝)면 지금과 같은 글자
    (_clip_words 뒤 " ,;:." 떼기). 아니면 그 안의 마지막 문장·절 경계("; " ", " ". ")에서 자르고(경계가 너무 앞이면 단어 경계 그대로),
    끝의 전치사·접속사(with/of/and/in/on/at/under …)를 뗀다."""
    text = str(text or "").strip()
    out = _clip_words(text, limit).rstrip(" ,;:.")
    if len(text) <= limit or re.match(r"\s*(?:[;,.]|$)", text[len(out):]):
        return out
    cut = max(out.rfind("; "), out.rfind(", "), out.rfind(". "))
    if cut >= limit * 0.4:
        out = out[:cut]
    return _DANGLING_TAIL.sub("", out).rstrip(" ,;:./")


def _background_sentence(text: str) -> str:
    """작업 157: 배경 문장의 짧은 형 = 첫 조각(장소 이름 + 실내/실외, "; " 앞)."""
    return str(text or "").split("; ", 1)[0].strip().rstrip(" ,;:.")


def _fixed_head(plan: dict, location_en: str, *, real: bool = False, compact: bool = False) -> str:
    """프롬프트 맨 앞 고정부 1줄(작업 127): [화풍 1구절] + [배경 1문장] + [overall tone(있으면 1번)].

    compact(작업 157, 통 테이크 줄이기): 배경은 첫 조각(장소 이름 + 실내/실외)만.
    """
    style = FIXED_REAL_STYLE if real else FIXED_WEBTOON_STYLE
    background = _background_en(plan, location_en, real=real)
    if compact:
        background = _background_sentence(background)
    text = f"FIXED: {style}. BACKGROUND (no people, same in every shot): {background}."
    tone = _overall_tone(plan)
    if tone:
        text += f" Overall tone: {tone}."
    return text


def _only_appear(names) -> str:
    """"Only A appears." / "Only A and B appear."(작업 127, 그 컷에 보이는 인물만). 없으면 ""."""
    picked: list[str] = []
    for name in names or []:
        name = str(name or "").strip()
        if name and name not in picked:
            picked.append(name)
    if not picked:
        return ""
    if len(picked) == 1:
        return f"Only {picked[0]} appears."
    return "Only " + ", ".join(picked[:-1]) + " and " + picked[-1] + " appear."


def _previz_prompt_budget() -> int:
    """프리비즈 edit 프롬프트 글자 상한(목표 PREVIZ_PROMPT_TARGET, 모델 maxLength 가 작으면 그 안으로).

    edit 모델은 보통 등록 레지스트리에 없다(고정 endpoint) → find 가 없으면 fal 기본 상한(2500)과
    목표값 중 작은 값을 쓴다.
    """
    budget = vnew_settings.PREVIZ_PROMPT_TARGET
    model = vpoc_settings.FAL_ID_PREFIX + str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    item = vpoc_fal.find(model)
    if item:
        caps = item.get("caps") or {}
        model_max = int(caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
        budget = min(budget, model_max)
    else:
        budget = min(budget, vpoc_fal.DEFAULT_PROMPT_MAX)
    return budget


# 작업 167: fal 프롬프트 크기 거절("prompt: size must be between 0 and 2500") · Kling 태그 여유.
_PROMPT_TAG_RE = re.compile(r"@(?:Element|Image|Video)\d+")
_PROMPT_SIZE_RE = re.compile(r"prompt\s*:\s*size must be between\s+0\s+and\s+(\d+)", re.IGNORECASE)
KLING_PROMPT_CAP = 2500       # 여유를 두는 Kling 상한(fal 스키마 maxLength)
SIZE_RETRY_RATIO = 0.85       # 크기 거절 뒤 1번 다시 낼 때 예산 = int(N × 0.85) − 태그 여유


def _prompt_tags(text: str) -> int:
    """프롬프트의 서로 다른 @Element·@Image·@Video 태그 수(같은 태그가 여러 번 나와도 1)."""
    return len(set(_PROMPT_TAG_RE.findall(str(text or ""))))


def _prompt_size_cap(message) -> int:
    """fal 거절 문구의 "prompt: size must be between 0 and N" 에서 N. 그 문구가 아니면 0."""
    match = _PROMPT_SIZE_RE.search(str(message or ""))
    return int(match.group(1)) if match else 0


def _cut_prompt_budget(endpoint: str, caps: dict | None, tags: int, size_cap: int = 0) -> int:
    """컷 프롬프트 바이트 예산(작업 167). 기본 PREVIZ_PROMPT_TARGET.

    endpoint 에 kling-video 가 있고 상한(caps.promptMaxLength, 없으면 fal 기본)이 2500 이면
    cap − PREVIZ_KLING_PROMPT_RESERVE − PREVIZ_KLING_TAG_RESERVE × tags(바닥 PREVIZ_KLING_PROMPT_FLOOR).
    size_cap(fal 이 알려 준 N)이 있으면 int(N × 0.85) − 태그 여유와 위 값 중 작은 값(모델 무관).
    """
    budget = int(vnew_settings.PREVIZ_PROMPT_TARGET)
    tag_reserve = int(vnew_settings.PREVIZ_KLING_TAG_RESERVE) * max(0, int(tags or 0))
    cap = int((caps or {}).get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
    if "kling-video" in str(endpoint or "").lower() and cap == KLING_PROMPT_CAP:
        budget = min(budget, max(int(vnew_settings.PREVIZ_KLING_PROMPT_FLOOR),
                                 cap - int(vnew_settings.PREVIZ_KLING_PROMPT_RESERVE) - tag_reserve))
    if size_cap > 0:
        budget = min(budget, max(1, int(size_cap * SIZE_RETRY_RATIO) - tag_reserve))
    return budget


def _segment_panel_jpeg(owner: str, pid: str, plan: dict, seg: dict) -> bytes | None:
    """그 구간(장소)의 원작 웹툰 컷 1장(그림체 참고). _segment_panel_jpegs 재사용(첫 장)."""
    jpegs = _segment_panel_jpegs(owner, pid, plan, seg, 1)
    return jpegs[0] if jpegs else None


def _submit_previz_edit(*, endpoint: str = "", caps: dict | None = None, video_mp4: bytes, prompt: str, output_prefix: str, elements: list[dict], image_urls: list[bytes] | None = None) -> dict:
    """프리비즈 영상 → 그림체 변환 제출(fal video-to-video/edit, elements 지원).

    vpoc.fal.submit_edit 는 image_urls 만 받고 elements 는 못 보내므로(수정 금지), vpoc.fal._request
    를 직접 불러 elements(frontal_image_url·reference_image_urls)+image_urls 를 담은 본문을 보낸다.
    반환 모양은 submit_edit 과 같다(ARN 접두사 fal/requests/ 동일 → poll/_find_output_key 재사용).

    이미지·영상은 모두 data URI 로 보낸다(외부 저장소에 올리지 않는다, 설계자 보안 규칙).
    """
    endpoint = str(endpoint or vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "").strip()
    caps = caps or {}
    if not vpoc_settings.FAL_KEY:
        raise VpocError("vnew.generate", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    if not endpoint:
        raise VpocError("vnew.generate", "BadEndpoint", "변환 모델 endpoint 가 비어 있습니다.", 400)
    if not prompt.strip():
        raise VpocError("vnew.generate", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)
    if not video_mp4:
        raise VpocError("vnew.generate", "EmptyVideo", "변환할 영상이 비어 있습니다.", 400)

    def _img_uri(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

    # caps 로 필드 이름을 고른다(모델별). caps 가 비면(기본 Kling 미등록) 기존 고정 이름을 쓴다.
    video_field = str(caps.get("videoField") or "video_url")
    elements_field = str(caps.get("elementsField") or ("elements" if not caps else ""))
    image_list_field = str(caps.get("imageListField") or ("image_urls" if not caps else ""))
    keep_audio_field = str(caps.get("keepAudioField") or ("keep_audio" if not caps else ""))

    body: dict = {
        "prompt": prompt,
        video_field: vpoc_fal._video_data_uri(video_mp4),
    }
    if keep_audio_field:
        body[keep_audio_field] = False
    payload_elements = []
    for element in elements or []:
        front = element.get("frontal_image_url")
        if not isinstance(front, (bytes, bytearray)):
            continue
        item = {"frontal_image_url": _img_uri(bytes(front))}
        refs = [_img_uri(bytes(r)) for r in (element.get("reference_image_urls") or []) if isinstance(r, (bytes, bytearray))]
        if refs:
            item["reference_image_urls"] = refs
        payload_elements.append(item)
    if payload_elements and elements_field:
        body[elements_field] = payload_elements
    if image_urls and image_list_field:
        body[image_list_field] = [_img_uri(bytes(j)) for j in image_urls if isinstance(j, (bytes, bytearray))]

    url = f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}"
    result = _previz_request("POST", url, body)
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.generate", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)
    return {
        "provider": vpoc_settings.FAL_ID_PREFIX + endpoint,
        "invocationArn": f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}",
        "clientRequestToken": "fal-previz-" + request_id,
        "outputPrefix": output_prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
    }


def _previz_request(method: str, url: str, payload: dict) -> dict:
    """vpoc.fal._request 로 fal 큐 제출(검사가 generate._previz_request 를 가짜로 바꿔 끼운다)."""
    return vpoc_fal._request(method, url, payload, where="vnew.generate.previz_edit")


def _charge_previz(owner: str, pid: str, gen_id: str, result: dict, seconds: float) -> None:
    """프리비즈 edit 제출 1회 계상(제출 성공 시점). 키 = 작업 토큰(중복 방지).

    모델은 result.provider("fal:<endpoint>")의 endpoint, 단가는 그 모델의 초당 단가(설계자 2026-10-22).
    """
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    provider = str(result.get("provider") or "")
    endpoint = provider.split(":", 1)[-1] if provider else str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    per_sec = _previz_model_unit_price(endpoint)
    secs = float(seconds or vpoc_settings.DURATION_S)
    try:
        vpoc_ledger.charge(
            token,
            kind="video",
            model=endpoint or vnew_settings.VNEW_PREVIZ_EDIT_MODEL,
            units=secs,
            usd=round(per_sec * secs, 4),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_previz_failed pid=%s err=%s", pid, str(exc)[:200])


# ================================================================ 개별 동작


def _claim_ok_variant(gen: dict, index: int, variant: str, claim: str) -> bool:
    try:
        cells = _previz_cells(gen) if _is_previz_mode(gen) else (gen.get("segments") or [])
        return cells[index][variant].get("claim") == claim
    except Exception:  # noqa: BLE001
        return False


def _do_opening(owner: str, pid: str, gen_id: str, claim: str) -> None:
    """실사 시작 장면(Seedream). 성공하면 opening.status done + imageKey."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or (gen.get("opening") or {}).get("claim") != claim:
            return
        inp = plan.get("input") or {}
        conti = plan.get("conti") or {}
        chars = _char_entries(owner, inp)
        prompt, refs = _opening_prompt(owner, pid, plan, chars)
        size = _opening_size(gen.get("videoModel") or "")
        image = _seedream(prompt, refs, size=size)
        key = opening_key(owner, pid, gen_id)
        STORE.put_bytes(key, image, "image/jpeg")
        _charge_seedream(owner, pid, gen_id)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id or (g.get("opening") or {}).get("claim") != claim:
                raise _Discard()
            g["opening"] = {"status": "done", "imageKey": key, "error": "", "claim": "", "claimedAt": ""}

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_opening(owner, pid, gen_id, claim, _msg(exc))


def _do_submit_real(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 제출(첫 프레임 = firstFrameKey 의 이미지)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "real", claim):
            return
        seg = (gen.get("segments") or [])[index]
        real = seg["real"]
        attempt = int(real.get("attempt") or 1)
        first_key = str(real.get("firstFrameKey") or "")
        first_jpeg = _fetch_bytes(first_key)
        b64 = base64.b64encode(first_jpeg).decode("ascii")

        prompt = _segment_prompt(plan, seg, first_segment=(index == 0), model=str(gen.get("videoModel") or ""))
        model = str(gen.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
        resolution = str(gen.get("resolution") or "")
        prefix = _seg_output_prefix(owner, pid, gen_id, seg["id"], attempt)
        result = _submit_video(
            model=model,
            owner=owner,
            project_id=pid,
            plan_id=f"vnew-{gen_id}",
            shot_id=seg["id"],
            attempt=attempt,
            prompt=prompt,
            keyframe_jpeg_b64=b64,
            gen_id=gen_id,
            resolution=resolution,
            duration_s=int(seg.get("seconds") or 0) or None,
        )
        _charge_video(owner, pid, gen_id, result, model, resolution, int(seg.get("seconds") or 0))

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "real", claim):
                raise _Discard()
            r = (g.get("segments") or [])[index]["real"]
            r["status"] = "submitted"
            r["invocationArn"] = result.get("invocationArn") or ""
            r["outputPrefix"] = result.get("outputPrefix") or prefix
            r["error"] = ""
            # claim 은 submitted 조회 때도 쓰므로 유지한다.

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "real", claim, _msg(exc))


def _do_poll_real(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 조회. 완료면 completed + outputKey, 다음 구간 firstFrameKey 저장."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "real", claim):
            return
        seg = (gen.get("segments") or [])[index]
        real = seg["real"]
        arn = str(real.get("invocationArn") or "")
        prefix = str(real.get("outputPrefix") or "")
        state = _poll_video(arn, prefix)
        status = str(state.get("status") or "")
        if status == "InProgress":
            return  # 아직. 다음 조회에서 다시 본다.
        if status != "Completed":
            _fail_variant(owner, pid, gen_id, index, "real", claim, state.get("failureMessage") or "영상 생성 실패")
            return
        output_key = str(state.get("outputKey") or _find_output_key(prefix, arn))
        # 완료 → 마지막 프레임을 뽑아 다음 구간 firstFrameKey 로.
        next_first_key = ""
        if index + 1 < len(gen.get("segments") or []):
            mp4 = _fetch_bytes(output_key, bucket=vpoc_settings.VIDEO_BUCKET)
            last = _last_frame(mp4)
            next_first_key = first_frame_key(owner, pid, gen_id, seg["id"] + "-to-next")
            STORE.put_bytes(next_first_key, last, "image/jpeg")

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "real", claim):
                raise _Discard()
            segs = g.get("segments") or []
            r = segs[index]["real"]
            r["status"] = "completed"
            r["outputKey"] = output_key
            r["error"] = ""
            r["claim"] = ""
            r["claimedAt"] = ""
            # 다음 구간 실사의 firstFrameKey 를 채운다.
            if next_first_key and index + 1 < len(segs):
                segs[index + 1]["real"]["firstFrameKey"] = next_first_key

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "real", claim, _msg(exc))


def _do_submit_webtoon(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 → 웹툰 변환 제출(fal.submit_edit). 참고 이미지 = 그 구간 웹툰 패널 + 캐릭터 웹툰 시트."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "webtoon", claim):
            return
        seg = (gen.get("segments") or [])[index]
        web = seg["webtoon"]
        real = seg["real"]
        attempt = int(web.get("attempt") or 1)
        source_key = str(real.get("outputKey") or "")
        src_mp4 = _fetch_bytes(source_key, bucket=vpoc_settings.VIDEO_BUCKET)
        prepared = _prepare_anime(src_mp4)

        refs_b64 = _anime_reference_b64(owner, pid, plan, seg)
        prompt = _anime_prompt(owner, plan, len(refs_b64))
        prefix = _seg_output_prefix(owner, pid, gen_id, seg["id"] + "-webtoon", attempt)
        result = _submit_anime(
            video_mp4=prepared["bytes"],
            prompt=prompt,
            output_prefix=prefix,
            reference_jpegs_b64=refs_b64,
        )
        _charge_anime(owner, pid, gen_id, result, prepared.get("seconds") or 0)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "webtoon", claim):
                raise _Discard()
            w = (g.get("segments") or [])[index]["webtoon"]
            w["status"] = "submitted"
            w["invocationArn"] = result.get("invocationArn") or ""
            w["outputPrefix"] = result.get("outputPrefix") or prefix
            w["error"] = ""

        _commit(owner, pid, mutator)
    except stitcher.AnimeTooShort as exc:
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, str(exc))
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, _msg(exc))


def _do_poll_webtoon(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """웹툰 변환 조회. 완료면 completed + outputKey."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "webtoon", claim):
            return
        seg = (gen.get("segments") or [])[index]
        web = seg["webtoon"]
        arn = str(web.get("invocationArn") or "")
        prefix = str(web.get("outputPrefix") or "")
        state = vpoc_fal.poll(arn, prefix)
        status = str(state.get("status") or "")
        if status == "InProgress":
            return
        if status != "Completed":
            _fail_variant(owner, pid, gen_id, index, "webtoon", claim, state.get("failureMessage") or "변환 실패")
            return
        output_key = _find_output_key(prefix, arn)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "webtoon", claim):
                raise _Discard()
            w = (g.get("segments") or [])[index]["webtoon"]
            w["status"] = "completed"
            w["outputKey"] = output_key
            w["error"] = ""
            w["claim"] = ""
            w["claimedAt"] = ""

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, _msg(exc))


def _find_output_key(prefix: str, invocation_arn: str) -> str:
    """제출 결과 영상이 저장된 키. {prefix}/{request_id|task_id}/output.mp4 규칙."""
    ident = ""
    if vpoc_fal.is_fal_job(invocation_arn):
        _eid, ident = vpoc_fal._parse_arn(invocation_arn)
    elif invocation_arn.startswith(vpoc_ark.ARN_PREFIX):
        ident = invocation_arn[len(vpoc_ark.ARN_PREFIX):]
    else:
        ident = str(invocation_arn or "").rsplit("/", 1)[-1]
    if ident:
        return f"{prefix.rstrip('/')}/{ident}/output.mp4"
    return f"{prefix.rstrip('/')}/output.mp4"


# ================================================================ 병합(finalize)


def _maybe_finalize(owner: str, pid: str, gen_id: str) -> None:
    """실사·웹툰 구간 상태를 보고 병합·gen.status 를 확정한다.

    - 실사 구간이 모두 completed → finalReal concat.
    - 웹툰 구간이 모두 completed → finalWebtoon concat(웹툰 변환 실패는 실사를 막지 않는다).
    - 실사 구간 중 하나라도 failed/cancelled → gen.status partial(그 뒤 구간 미진행).
    - 실사·웹툰 다 끝나면 done.
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") not in ("running",):
        return
    segments = gen.get("segments") or []
    if not segments:
        return

    real_states = [seg["real"]["status"] for seg in segments]
    web_states = [seg["webtoon"]["status"] for seg in segments]

    real_all_done = all(s == "completed" for s in real_states)
    real_failed = any(s in ("failed", "cancelled") for s in real_states)

    # 실사 병합(모두 완료, 아직 안 함).
    if real_all_done and (gen.get("finalReal") or {}).get("status") not in ("done", "running"):
        _do_concat(owner, pid, gen_id, "real")

    # 웹툰: 완료된 실사 구간에 대응하는 웹툰이 전부 끝났는지(completed/failed) 보고, 하나라도
    # completed 가 있고 아직 진행 중(submitted/preparing/queued/waiting)이 없으면 병합.
    web_pending = any(s in ("waiting", "queued", "preparing", "submitted") for s in web_states)
    web_any_done = any(s == "completed" for s in web_states)
    if real_all_done and not web_pending and web_any_done and (gen.get("finalWebtoon") or {}).get("status") not in ("done", "running"):
        _do_concat(owner, pid, gen_id, "webtoon")

    # 상태 확정.
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        return

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or g.get("status") != "running":
            raise _Discard()
        segs = g.get("segments") or []
        reals = [s["real"]["status"] for s in segs]
        webs = [s["webtoon"]["status"] for s in segs]
        fr = g.get("finalReal") or {}
        fw = g.get("finalWebtoon") or {}
        real_done = all(x == "completed" for x in reals)
        real_fail = any(x in ("failed", "cancelled") for x in reals)
        web_pending2 = any(x in ("waiting", "queued", "preparing", "submitted") for x in webs)

        if real_fail:
            g["status"] = "partial"
            return
        if real_done and not web_pending2:
            # 실사 병합은 반드시 done 이어야(실패면 partial).
            if fr.get("status") == "done":
                g["status"] = "done"
            else:
                g["status"] = "partial"

    try:
        _, _ = plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        return


def _fit_takes(clips: list[bytes], planned: list[float], variant: str) -> bytes | None:
    """통 테이크 최종 잇기 전 계획 길이 맞추기(작업 156). 테이크 결과 길이가 계획(테이크 seconds)과 1프레임 넘게 다르면 모든 테이크를
    _conform_concat(hold=True)로 맞춰 잇는다(긴 것은 끝을 자르고, 짧은 것은 마지막 프레임을 잡는다 — 최종 = 계획 합 ±1프레임).
    모두 맞거나 길이를 못 재면(mp4 아님) None(지금 방식: 1개 복사·여러 개 concat). 맞추기가 실패해도 None(경고 로그).
    """
    lengths = [_mp4_seconds(clip) if _is_mp4(clip) else 0.0 for clip in clips]
    off = [i for i, (got, want) in enumerate(zip(lengths, planned)) if got > 0 and want > 0 and abs(got - want) > 1.0 / REAL_CUT_FPS]
    if not off:
        return None
    try:
        merged = _conform_concat(list(zip(clips, planned)), hold=True)
    except Exception as exc:  # noqa: BLE001 — 맞추지 못하면 예전 방식
        log.warning("vnew.gen.final_fit_failed variant=%s err=%s", variant, _msg(exc))
        return None
    log.info("vnew.gen.final_fit variant=%s takes=%d off=%s got=%s want=%.3f", variant, len(clips),
             ",".join(str(i + 1) for i in off), ",".join(f"{x:.2f}" for x in lengths), sum(planned))
    return merged


def _do_concat(owner: str, pid: str, gen_id: str, variant: str) -> None:
    """실사/웹툰 구간 영상을 순서대로 이어 붙여 final-*.mp4 로 저장한다."""
    final_field = "finalReal" if variant == "real" else "finalWebtoon"

    # running 선점(중복 병합 방지).
    picked = {"go": False}

    def claim_mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id:
            raise _Discard()
        field = g.get(final_field) or {}
        if field.get("status") in ("done", "running"):
            picked["go"] = False
            return
        field["status"] = "running"
        g[final_field] = field
        picked["go"] = True

    try:
        plan_mod.mutate_plan(owner, pid, claim_mutator)
    except _Discard:
        return
    if not picked["go"]:
        return

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        is_previz = gen.get("mode") == "previz"  # 컷 단위 previz 만 3초 채움 트림 대상.
        cells = _previz_cells(gen) if _is_previz_mode(gen) else (gen.get("segments") or [])
        clips = []
        planned: list[float] = []  # 작업 156: 통 테이크의 계획 길이(테이크 seconds)
        for seg in cells:
            state = seg[variant]
            key = str(state.get("outputKey") or "")
            if state.get("status") == "completed" and key:
                clip_bytes = _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
                # 프리비즈 컷 단위: 3초로 채워 제출한 컷은 원래 길이로 잘라서 합친다.
                if is_previz and state.get("padded"):
                    orig = float(state.get("origSeconds") or 0)
                    if orig > 0:
                        clip_bytes = _trim_clip(clip_bytes, orig)
                clips.append(clip_bytes)
                planned.append(float(seg.get("seconds") or 0))
        if not clips:
            raise VpocError("vnew.generate", "NoClips", "이어 붙일 영상이 없습니다.", 502)
        fitted = _fit_takes(clips, planned, variant) if gen.get("mode") == "previz-take" else None
        # 통 테이크(설계자 2026-10-26, 064): 클립이 1개면 결과 mp4 를 **그대로 복사**한다(재인코딩·-an
        # 없음 → 오디오 스트림 보존). 여러 개면 stitcher.concat(오디오 맞춤 지원)으로 이어 붙인다.
        if fitted is not None:
            merged = fitted
        elif len(clips) == 1:
            merged = clips[0]
            log.info("vnew.gen.final_copy variant=%s clips=1 bytes=%d (no re-encode)", variant, len(merged or b""))
        else:
            merged = _concat(clips)
            log.info("vnew.gen.final_concat variant=%s clips=%d bytes=%d", variant, len(clips), len(merged or b""))
        # 결과에 오디오 스트림이 있는지 로그(막지 않는다 — 진단용).
        has_audio = _has_audio_stream(merged)
        log.info("vnew.gen.final_audio variant=%s has_audio=%s", variant, has_audio)
        key = final_real_key(owner, pid, gen_id) if variant == "real" else final_webtoon_key(owner, pid, gen_id)
        # vnew-live-fx C.7: 통 테이크 실사 회차는 회차마다 다른 파일(final-real-r{n}.mp4)에 두고 결과에 n 을 둔다.
        run_n = int((gen.get("realRun") or {}).get("n") or 0)
        take_real = variant == "real" and gen.get("mode") == "previz-take" and run_n > 0
        if take_real:
            key = final_real_run_key(owner, pid, gen_id, run_n)
        STORE.put_bytes(key, merged, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)

        def done_mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id:
                raise _Discard()
            g[final_field] = {"status": "done", "key": key, "error": ""}
            if take_real:
                g[final_field]["n"] = run_n

        _commit(owner, pid, done_mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        def fail_mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id:
                raise _Discard()
            g[final_field] = {"status": "failed", "key": "", "error": _msg(exc)}
            run = (g.get("realRun") or {}).get("n")
            if variant == "real" and g.get("mode") == "previz-take" and run:
                g[final_field]["n"] = int(run)

        try:
            _commit(owner, pid, fail_mutator)
        except _Discard:
            return


# ================================================================ 프롬프트


def _opening_size(video_model: str) -> str:
    """시작 장면 크기. 모델 화면비에 맞춰 1920x1080(16:9) 기본."""
    return vpoc_seedream.SIZE


def _positions_text(cut: dict) -> str:
    parts = []
    for ch in cut.get("characters") or []:
        name = str(ch.get("name") or "")
        pos = str(ch.get("positionEn") or "")
        if name or pos:
            parts.append((name + " " + pos).strip())
    return ", ".join(parts) if parts else "the characters as drawn"


def _opening_prompt(owner: str, pid: str, plan: dict, chars: list[dict]) -> tuple[str, list[bytes]]:
    """1구간 실사 시작 장면 프롬프트 + 참고 이미지(첫 컷 웹툰 패널 + 등장 캐릭터 실사 정면·시트).

    프롬프트: 웹툰 패널의 구도를 그대로 실사로 재현 — 같은 장소·카메라 각도·인물 위치, 캐릭터는
    시트 속 배우와 똑같이. 자연광 영화 톤. 글자·말풍선 없음.
    """
    conti = plan.get("conti") or {}
    cuts = conti.get("cuts") or []
    segments = conti.get("segments") or []
    first_cut = cuts[0] if cuts else {}
    # 첫 구간 첫 컷의 장소·위치.
    if segments:
        first_seg = segments[0]
        cut_ids = first_seg.get("cutIds") or []
        by_id = {c["id"]: c for c in cuts}
        if cut_ids and cut_ids[0] in by_id:
            first_cut = by_id[cut_ids[0]]

    location = str(first_cut.get("locationEn") or (conti.get("setting") or {}).get("locationEn") or "the scene")
    positions = _positions_text(first_cut)

    refs: list[bytes] = []
    # Image 1 = 첫 컷 웹툰 패널. 패널 이미지는 업로드 웹툰에서 가져온다(첫 컷 panels 의 첫 패널 이미지).
    panel_jpeg = _panel_jpeg_for_cut(owner, pid, plan, first_cut)
    if panel_jpeg:
        refs.append(panel_jpeg)
    # Image 2.. = 등장 캐릭터 실사 정면·시트.
    for c in chars:
        front_key = c.get("realFrontKey") or ""
        if front_key:
            try:
                refs.append(STORE.get_bytes(front_key))
            except Exception:  # noqa: BLE001
                pass
        if len(refs) >= 1 + MAX_ANIME_REFS:
            break

    prompt = (
        "Photorealistic live-action film still that recreates the composition of the webtoon panel "
        "(Image 1): same place (" + location + ", enclosed or open as drawn), same camera angle, "
        "same character positions (" + positions + "). The characters look exactly like the actors in "
        "the character sheets (Image 2..). Natural cinematic lighting. No text, no speech bubbles."
    )
    return prompt, refs


def _panel_jpeg_for_cut(owner: str, pid: str, plan: dict, cut: dict) -> bytes | None:
    """그 컷이 맡은 웹툰 패널 이미지(업로드 웹툰 order 에서 첫 panel 인덱스에 해당하는 파일).

    panels 는 타일 인덱스일 수 있어 정확한 매핑이 어렵다 — 여기서는 order 의 첫 이미지를 쓴다
    (구도 참고용이므로 근사치면 충분하다). order 가 비면 None.
    """
    inp = plan.get("input") or {}
    order = inp.get("order") or []
    if not order:
        return None
    try:
        return STORE.get_bytes(plan_mod.upload_key(owner, pid, order[0]))
    except Exception:  # noqa: BLE001
        return None


def _cut_timeline(plan: dict, seg: dict) -> str:
    """구간의 컷들을 시간표로("0-5s: …"). 컷당 1~2문장으로 간결히(설계자 2026-10-20).

    컷마다 움직임(promptEn)·카메라·인물 행동을 짧게 한 줄. 카메라·장소가 앞 컷과 같으면 되풀이하지
    않는다(프롬프트 길이 절감). 캐릭터 외형은 여기 넣지 않는다 — 첫 프레임 이미지가 책임진다.
    """
    conti = plan.get("conti") or {}
    by_id = {c["id"]: c for c in (conti.get("cuts") or [])}
    lines = []
    start = 0
    prev_camera = ""
    prev_location = ""
    for cut_id in seg.get("cutIds") or []:
        cut = by_id.get(cut_id)
        if not cut:
            continue
        seconds = int(cut.get("seconds") or 0)
        end = start + seconds
        # 인물: 이름 + 행동만(위치·외형은 첫 프레임 이미지가 책임진다).
        actors = ", ".join(
            f"{ch.get('name', '')} {ch.get('actionEn', '')}".strip()
            for ch in (cut.get("characters") or [])
            if (ch.get("name") or ch.get("actionEn"))
        )
        dialogue = str(cut.get("dialogueKo") or "").strip()
        body = str(cut.get("promptEn") or "").strip()
        camera = str(cut.get("cameraEn") or "").strip()
        location = str(cut.get("locationEn") or "").strip()

        parts = [f"{start}-{end}s: {body}"]
        if actors:
            parts.append(actors + ".")
        # 카메라·장소는 바뀔 때만 적는다.
        if camera and camera != prev_camera:
            parts.append("Camera: " + camera + ".")
            prev_camera = camera
        if location and location != prev_location:
            parts.append("Location: " + location + ".")
            prev_location = location
        if dialogue:
            parts.append("Speaks Korean, lip movement only.")
        lines.append(" ".join(parts))
        start = end
    return "\n".join(lines)


def _appearance_text(plan: dict, owner: str) -> str:
    chars = _char_entries(owner, plan.get("input") or {})
    parts = []
    for c in chars:
        role = c.get("roleName") or c.get("name") or ""
        appearance = c.get("appearanceEn") or ""
        outfit = c.get("outfitEn") or ""
        parts.append(f"{role}: {appearance} {outfit}".strip())
    return " ".join(parts)


def _prompt_budget(model: str) -> int:
    """이 모델에 보낼 프롬프트 글자 상한. 목표는 1,800자 이하이되, fal 모델 maxLength 가 더 작으면
    그 안으로 맞춘다(설계자 2026-10-20). fal 이 아닌 모델(Luma·Seedance)은 목표 1,800 만 쓴다.
    """
    budget = SEGMENT_PROMPT_TARGET
    if model and vpoc_settings.is_fal(model):
        item = vpoc_fal.find(model) or {}
        caps = item.get("caps") or {}
        model_max = int(caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
        budget = min(budget, model_max)
    return budget


def _real_cut_prompt(plan: dict, seg: dict, *, model: str = "", prompt_max: int = 0, fix: str = "",
                     bases: dict | None = None, lock: str = "", from_prev: bool = False) -> str:
    """컷 단위 Kling 실사 프롬프트(작업 132, _segment_prompt real=True). 컷 1개.

    순서: 127 고정부(실사 화풍 + 같은 배경 문장, _fixed_head real) → 첫 프레임에서 이어 가기 → REAL_TAKE_STYLE(실사 통 테이크와
    같은 문구) → REAL_CUT_KEEP_FRAMING(작업 139, 구도 유지) → SHOT(shotKo 영어, 없으면 샷 약어)·Camera(cameraEn) → ACTION(promptEn + 인물 actionEn, 실사 연기) →
    DIALOGUE(컷 대사 그대로) → 자막 없음 → NEGATIVE(REAL_CUT_NEGATIVE). 머리·꼬리는 남기고 넘치면 가운데(샷~대사)만 문장
    경계에서 줄인다(UTF-8 바이트, 모델 상한 · prompt_max 중 작은 값). negative 구절 밖에는 webtoon·anime·illustration·cel 이 없다.
    """
    cut_ids = _seg_cut_ids(seg)
    cut_id = cut_ids[0] if cut_ids else ""
    cut = next((c for c in (plan.get("conti") or {}).get("cuts") or [] if str(c.get("id")) == cut_id), {})
    head = "\n".join([
        REAL_REF_AI_NOTE,  # 작업 140: 첫 줄(머리라 줄이기에서 빠지지 않는다)
        *([str(lock).strip()] if str(lock or "").strip() else []),  # 작업 169: 샷 고정(구도 먼저, 없으면 줄 없음 — 지금과 같다)
        _fixed_head(plan, str(cut.get("locationEn") or ""), real=True),
        "Start from the first frame (a live-action still) and continue the action smoothly, keeping the "
        "same place, lighting, characters, positions and outfits.",
        REAL_TAKE_STYLE,
        # 작업 139: 시작 그림 구도 유지(카메라가 물러나지 않고 새 인물이 들어오지 않는다). 169: 시작 그림이 앞 샷 마지막 프레임이면
        # 그 구도를 지키지 않고 FRAMING 으로 간다.
        REAL_CUT_FROM_PREV if from_prev else REAL_CUT_KEEP_FRAMING,
        _real_time_sentence(plan),  # 작업 160: 시간대 고정(머리라 줄이기에서 빠지지 않는다)
    ])
    tail = "No text, no subtitles, no speech bubbles.\nNEGATIVE: " + REAL_CUT_NEGATIVE + "."
    if fix:  # 작업 133: QA 교정 문장 1개(줄이기에서 빠지지 않는다)
        tail = fix.strip() + "\n" + tail
    shot = _shot_ko_en(str(cut.get("shotKo") or "")) or _shot_abbrev(plan, cut_id)
    camera = _real_words(cut.get("cameraEn")).rstrip(".")
    lines = ["SHOT: " + shot + "." + (f" Camera: {camera}." if camera else "")]
    actors = ", ".join(f"{ch.get('name', '')} {ch.get('actionEn', '')}".strip()
                       for ch in (cut.get("characters") or []) if (ch.get("name") or ch.get("actionEn")))
    action = ". ".join(p for p in (_real_action(cut.get("promptEn")).rstrip("."), _real_action(actors).rstrip(".")) if p)
    # 작업 136: 그림 문장 빼기·옷 구절 → 장면 옷. 작업 160: 장면 옷 = 그 컷 장소 1벌(_scene_costume_map).
    action = _real_brief_text(action, _scene_costume_map(plan, [cut_id], str(cut.get("locationEn") or ""), None, bases))
    if action:
        lines.append("ACTION: " + action + ".")
    spoken = [ln for ln in _cut_lines(plan, cut) if not ln.get("silent") and str(ln.get("lineKo") or "").strip()]

    def _said(limit: int) -> str:
        # 작업 147: 대사는 _fit_dialogue 로만(문장 안 자르기 없음). 예산이 모자라면 샷·연기부터 줄인다.
        if not spoken:
            return ""
        return "DIALOGUE (Korean, lip-sync): " + " / ".join(
            f'{str(ln.get("speaker") or "").strip() or "A character"}: "{_fit_dialogue(str(ln.get("lineKo")).strip(), limit)}"'
            for ln in spoken)

    dialogue = _said(0)
    rest = "\n".join(lines)
    body = "\n".join(p for p in (rest, dialogue) if p)
    budget = _prompt_budget(model)
    if prompt_max:
        budget = min(budget, int(prompt_max))
    room = budget - _utf8_len(head) - _utf8_len(tail) - 2
    if _utf8_len(body) > room:
        # 작업 147: 대사는 맨 마지막 — SHOT 줄이 통째로 남을 자리가 없을 때만 대사를 25(문장 단위)로 줄인다.
        if dialogue and room - _utf8_len(dialogue) - 1 < _utf8_len(lines[0]):
            dialogue = _said(25)
        room_rest = room - (_utf8_len(dialogue) + 1 if dialogue else 0)
        rest = vpoc_fal._trim_prompt(rest, room_rest) if room_rest > 0 else ""  # noqa: SLF001
        body = "\n".join(p for p in (rest, dialogue) if p)
        if _utf8_len(body) > room:
            body = vpoc_fal._trim_prompt(body, max(0, room))  # noqa: SLF001 — 최후(거의 없음)
    return "\n".join(p for p in (head, body, tail) if p)


def _segment_prompt(plan: dict, seg: dict, *, first_segment: bool, model: str = "", real: bool = False,
                    prompt_max: int = 0, fix: str = "", bases: dict | None = None, lock: str = "",
                    from_prev: bool = False) -> str:
    """구간 영상 프롬프트(간결, 설계자 2026-10-20). 목표 1,800자 이하 — 넘으면 시간표 뒤쪽을
    문장 경계에서 자른다(모델 maxLength 가 더 작으면 그 안으로). 첫 구간이 아니면 '앞 프레임에서
    이어진다' 첫 줄을 붙인다. 캐릭터 외형은 첫 프레임 이미지가 책임지므로 넣지 않는다.

    작업 132: real=True 면 컷 단위 Kling 실사 프롬프트(_real_cut_prompt). 기본(False)은 지금과 글자 하나 다르지 않다.
    """
    if real:
        return _real_cut_prompt(plan, seg, model=model, prompt_max=prompt_max, fix=fix, bases=bases, lock=lock,
                                from_prev=from_prev)  # 작업 169: 샷 고정 · 앞 샷 프레임에서 시작
    conti = plan.get("conti") or {}
    setting = conti.get("setting") or {}
    timeline = _cut_timeline(plan, seg)
    layout = str(setting.get("layoutEn") or "")

    head = []
    if not first_segment:
        head.append(
            "Continue seamlessly from the first frame: same room, lighting, characters, positions and outfits."
        )
    else:
        head.append(
            "Start from the first frame (a live-action still) and continue the action smoothly, keeping the "
            "same place, lighting, characters, positions and outfits."
        )
    head.append("Photorealistic live-action film. No text, no speech bubbles.")
    if layout:
        head.append("Layout: " + layout + ".")
    head_text = "\n".join(head)

    budget = _prompt_budget(model)
    body = head_text + "\n" + timeline
    if len(body) > budget:
        # 머리(연속성·화풍 지시)는 반드시 남기고 시간표만 줄인다.
        room = max(0, budget - len(head_text) - 1)
        body = head_text + "\n" + vpoc_fal._trim_prompt(timeline, room)
    return body


def _anime_ref_labels(count: int) -> str:
    """참고 이미지 개수에 맞춘 "@Image1, @Image2, ..." 나열(프롬프트 {refs})."""
    return ", ".join(f"@Image{index}" for index in range(1, count + 1))


def _anime_prompt(owner: str, plan: dict, reference_count: int = 0) -> str:
    """실사 → 웹툰 변환 프롬프트(설계자 2026-10-20).

    일반 화풍(clean line art·cel shading·flat soft colors·2D anime)을 지시하지 않는다. 대신 참고
    웹툰 이미지의 선·채색·명암·색·얼굴/눈/머리 그리는 방식을 그대로 베끼라고 한다. 참고 이미지가
    있으면 그 이미지들을 가리키고, 분석된 등장 캐릭터 styleEn(지시서 029)이 있으면 "Style notes" 로
    덧붙인다. 참고 이미지가 없으면(웹툰을 안 올린 경우) 일반 "Korean webtoon style" 문장을 쓴다.
    """
    if reference_count <= 0:
        return (
            "Redraw @Video1 in a hand-drawn Korean webtoon style. Keep every character, the camera "
            "movement, composition, timing and action exactly the same as @Video1. "
            "No text, no speech bubbles."
        )
    refs = _anime_ref_labels(reference_count)
    prompt = (
        f"Redraw @Video1 in exactly the same drawing style as the reference webtoon images ({refs}): "
        "copy their line quality and thickness, coloring and shading, color palette and the way faces, "
        "eyes and hair are drawn. Do NOT use a generic anime, cartoon or glossy style. Keep every "
        "character, the camera movement, composition, timing and action exactly the same as @Video1. "
        "No text, no speech bubbles."
    )
    style_en = _first_style_en(owner, plan)
    if style_en:
        prompt += " Style notes: " + style_en
    return prompt


def _first_style_en(owner: str, plan: dict) -> str:
    """등장 캐릭터 중 첫 번째의 분석 styleEn(지시서 029). 300자 이내. 없으면 빈 문자열."""
    for item in (plan.get("input") or {}).get("characters") or []:
        char_id = str(item.get("charId") or "")
        if not char_id:
            continue
        try:
            char, _ = characters.load_char(owner, char_id)
        except Exception:  # noqa: BLE001
            continue
        style_en = str(((char or {}).get("analysis") or {}).get("styleEn") or "").strip()
        if style_en:
            return style_en[:300]
    return ""


def _segment_panel_jpegs(owner: str, pid: str, plan: dict, seg: dict, limit: int) -> list[bytes]:
    """그 구간이 맡은 웹툰 패널 이미지(업로드 원본) 최대 limit 장(설계자 2026-10-20).

    panels→업로드 파일 매핑이 정확하지 않아(타일 인덱스), 구간 순서에 맞춰 업로드 원본을 고르게
    나눠 그 구간 몫을 돌려준다(판단 어려우면 순서대로). 말풍선이 많은 장/인물이 큰 장을 가릴 수
    없으므로 순서대로 쓴다. 읽기 실패는 건너뛴다.
    """
    inp = plan.get("input") or {}
    order = list(inp.get("order") or [])
    if not order or limit <= 0:
        return []
    segments = (plan.get("conti") or {}).get("segments") or []
    seg_ids = [s.get("id") for s in segments]
    try:
        index = seg_ids.index(seg.get("id"))
    except ValueError:
        index = 0
    count = len(segments) or 1
    start = index * len(order) // count
    end = max(start + 1, (index + 1) * len(order) // count)
    file_ids = order[start:end][:limit]
    out: list[bytes] = []
    for file_id in file_ids:
        try:
            key = plan_mod.upload_key(owner, pid, file_id)
            data = STORE.get_bytes(key)
        except Exception:  # noqa: BLE001
            continue
        _ref_note_load(key, data)  # 지시서 116: _RefTrace 안이면 읽은 바이트 → S3 키(기록용)
        out.append(data)
    return out


def _anime_reference_b64(owner: str, pid: str, plan: dict, seg: dict) -> list[str]:
    """웹툰 변환 참고 이미지(최대 4장, 설계자 2026-10-20): 원작 웹툰 패널을 먼저.

    ① 그 구간 컷들의 웹툰 이미지(업로드 원본) 최대 3장 — 순서대로.
    ② 등장 캐릭터 웹툰 시트 1장.
    원작 그림체가 기준이므로 패널을 앞에 둔다(프롬프트의 @Image1.. 순서와 같다).
    """
    refs: list[bytes] = []
    # ① 원작 웹툰 패널 먼저(최대 3장).
    refs.extend(_segment_panel_jpegs(owner, pid, plan, seg, 3))
    # ② 등장 캐릭터 웹툰 시트 1장(남는 자리 안에서).
    inp = plan.get("input") or {}
    for c in _char_entries(owner, inp):
        if len(refs) >= MAX_ANIME_REFS:
            break
        sheet_key = c.get("webtoonSheetKey") or ""
        if sheet_key:
            try:
                refs.append(STORE.get_bytes(sheet_key))
            except Exception:  # noqa: BLE001
                pass
            break  # 캐릭터 시트는 1장만.
    refs = [r for r in refs if r][:MAX_ANIME_REFS]
    return [base64.b64encode(r).decode("ascii") for r in refs]


# ================================================================ 취소 · select


def cancel_gen(owner: str, pid: str) -> dict:
    """진행 중 gen 을 취소한다(014 규칙). 진행 중 대상은 cancelled, fal/ark 취소 시도.

    Luma 는 unsupported. 반환: 취소 후 gen(없으면 빈 dict). 진행 중 gen 이 없으면 409.
    """
    # 먼저 취소 대상(submitted)의 ARN 을 모아 공급자 취소를 시도한다(저장 전에 바깥 호출).
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if gen.get("status") not in ("running", "partial"):
        raise VpocError("vnew.generate", "NothingToCancel", "진행 중인 영상 생성이 없습니다.", 409)

    remote: dict[str, str] = {}
    cancel_cells = _previz_cells(gen) if _is_previz_mode(gen) else (gen.get("segments") or [])
    for seg in cancel_cells:
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "submitted" and state.get("invocationArn"):
                remote[state["invocationArn"]] = _cancel_video(state["invocationArn"])
            if state.get("status") == "submitted":
                # 작업 152: 테이크 조각도 취소한다.
                for part in state.get("parts") or []:
                    if isinstance(part, dict) and part.get("status") == "submitted" and part.get("invocationArn"):
                        remote[part["invocationArn"]] = _cancel_video(part["invocationArn"])
            if state.get("status") == "submitted" and variant == "real":
                # 작업 130: 컷 단위 Kling 컷도 취소하고, 미뤄 둔 Seedance 금액은 남긴다(이미 생성 중일 수 있다).
                for cut in (state.get("fallback") or {}).get("cuts") or []:
                    if cut.get("status") == "submitted" and cut.get("invocationArn"):
                        remote[cut["invocationArn"]] = _cancel_video(cut["invocationArn"])
                _settle_take_charge(owner, pid, str(gen.get("genId") or ""), state)

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if g.get("status") not in ("running", "partial"):
            raise VpocError("vnew.generate", "NothingToCancel", "진행 중인 영상 생성이 없습니다.", 409)
        opening = g.get("opening") or {}
        if opening.get("status") in ("waiting", "preparing"):
            opening["status"] = "cancelled"
            opening["claim"] = ""
        cells = _previz_cells(g) if _is_previz_mode(g) else (g.get("segments") or [])
        for seg in cells:
            for variant in ("real", "webtoon"):
                state = seg.get(variant) or {}
                if state.get("status") in ("waiting", "queued", "preparing", "submitted"):
                    arn = str(state.get("invocationArn") or "")
                    state["status"] = "cancelled"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    state["note"] = "사용자가 취소했습니다"
                    if arn and arn in remote:
                        state["cancelRemote"] = remote[arn]
                    if arn:
                        state["cancelledAt"] = now_iso()
        auto = g.get("autoReal")  # 작업 123: 취소한 gen 은 실사를 자동으로 시작하지 않는다.
        if isinstance(auto, dict) and not auto.get("startedAt") and not auto.get("error"):
            auto["error"] = AUTO_REAL_CANCEL_TEXT
        g["status"] = "cancelled"

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    return updated.get("gen") or {}


def retry_segment(owner: str, pid: str, seg_id: str, variant: str = "real", audio: bool | None = None) -> dict:
    """실패한 구간부터 다시(attempt+1). variant="real" 이면 그 구간부터 실사 재개,
    variant="webtoon" 이면 그 구간 웹툰 변환만 재시도(실사 진행을 막지 않았던 것).

    vnew-live-fx C.6·C.10: audio 가 bool 이면 그 칸 state.audio(통 테이크 generate_audio)로 둔다("소리 없이 다시
    만들기" = False). 다시 시도는 failureKind·error 를 비우고 gen.activeSince 를 지금으로 둔다(_gen_stale 이 본다).
    통 테이크는 실사 회차(realRun)가 있을 때만 실사 칸을 다시 하고, 없으면 지금처럼 웹툰이다.

    gen 이 없거나 seg_id 를 못 찾으면 404. 진행 재개 후 드라이버를 깨운다.
    """
    gen_id_box = {"id": ""}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not g:
            raise VpocError("vnew.generate", "NoGen", "영상 생성 기록이 없습니다.", 404)
        gen_id_box["id"] = str(g.get("genId") or "")
        segments = g.get("segments") or []

        # 프리비즈 계열: 그 컷/테이크·그 그림체만 다시 queued. 통 테이크는 실사 회차가 있으면 실사도, 없으면 웹툰만.
        if _is_previz_mode(g):
            if g.get("mode") == "previz-take":
                target = "real" if variant == "real" and g.get("realRun") else "webtoon"
            else:
                target = str(variant if variant in ("real", "webtoon") else "real")
            cells = _previz_cells(g)
            found = False
            for unit in cells:
                if str(unit.get("id")) != str(seg_id):
                    continue
                found = True
                st = unit[target]
                was_failed = st.get("status") == "failed"
                st["status"] = "queued"
                st["error"] = ""
                st["claim"] = ""
                st["claimedAt"] = ""
                st["invocationArn"] = ""
                st["outputKey"] = ""
                content_retry = st.get("failureKind") == CONTENT_FLAG_KIND
                st.pop("failureKind", None)
                st.pop("fallback", None)  # 작업 130: 사용자 다시 시도는 기본 Seedance 부터
                st.pop("pendingCharge", None)
                st.pop("contentFallback", None)
                st.pop("qc", None)  # 작업 156: 사용자 다시 시도는 결과 검사도 처음부터
                st.pop("sizeRetry", None)  # 작업 167: 사용자 다시 시도는 크기 거절 재제출도 처음부터
                if st.get("note") == TEXT_LEFT_NOTE:
                    st.pop("note", None)
                if content_retry:
                    # 작업 153: 콘텐츠 검사로 끝난 칸은 1단계(순화한 프롬프트, 같은 모델)부터 다시. 조각도 거절된 조각만 1단계로.
                    st["contentStep"] = 1
                    for part in st.get("parts") or []:
                        if isinstance(part, dict) and part.get("failureKind") == CONTENT_FLAG_KIND:
                            part["contentStep"] = 1
                            part.pop("contentFallback", None)
                else:
                    st.pop("contentStep", None)
                if isinstance(audio, bool):
                    st["audio"] = audio
                auto = g.get("autoReal")
                if g.get("mode") == "previz-take" and target == "webtoon" and was_failed and isinstance(auto, dict) \
                        and not auto.get("startedAt") and auto.get("error") == AUTO_REAL_SKIP_TEXT:
                    # 작업 163: 실패한 웹툰 칸을 다시 시작하면 건너뜀 표시를 지운다 → 웹툰이 다 끝나면 실사를 1번 자동 시작(startedAt 선점).
                    auto.pop("error", None)
                break
            if not found:
                raise VpocError("vnew.generate", "SegmentNotFound", "컷을 찾을 수 없습니다.", 404)
            # 그 그림체 병합만 다시(다른 그림체는 그대로).
            if target == "real":
                g["finalReal"] = {"status": "waiting", "key": "", "error": ""}
                if g.get("mode") == "previz-take":
                    g["finalReal"]["n"] = int((g.get("realRun") or {}).get("n") or 0)
            else:
                g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
            g["status"] = "running"
            g["activeSince"] = now_iso()
            return

        found = False
        for index, seg in enumerate(segments):
            if seg.get("id") != seg_id:
                continue
            found = True
            if variant == "webtoon":
                web = seg["webtoon"]
                web["status"] = "queued"
                web["error"] = ""
                web["claim"] = ""
                web["claimedAt"] = ""
                web["invocationArn"] = ""
                web["outputKey"] = ""
            else:
                # 실사: 이 구간부터 다시. 이 구간과 그 뒤 구간의 실사를 queued 로 되돌린다.
                for later in segments[index:]:
                    r = later["real"]
                    r["status"] = "queued"
                    r["error"] = ""
                    r["claim"] = ""
                    r["claimedAt"] = ""
                    r["invocationArn"] = ""
                    r["outputKey"] = ""
                    # firstFrameKey 는 이 구간만 비운다(1구간=opening, 그 외는 앞 구간 완료 때 다시 채움).
                    if later is seg:
                        r["firstFrameKey"] = ""
                # 뒤 구간 웹툰도 함께 되돌린다(실사가 다시 만들어지므로).
                for later in segments[index:]:
                    w = later["webtoon"]
                    if w["status"] != "completed":
                        w["status"] = "waiting"
                    w["claim"] = ""
                    w["claimedAt"] = ""
            break
        if not found:
            raise VpocError("vnew.generate", "SegmentNotFound", "구간을 찾을 수 없습니다.", 404)
        # opening 이 실패해 멈춘 경우, 실사 재시도는 opening 부터 다시 그릴 수 있게 되돌린다.
        if variant != "webtoon":
            opening = g.get("opening") or {}
            if opening.get("status") in ("failed", "cancelled"):
                g["opening"] = {"status": "waiting", "imageKey": "", "error": "", "claim": "", "claimedAt": ""}
        # 병합 상태 초기화(다시 만들어야 하므로).
        g["finalReal"] = {"status": "waiting", "key": "", "error": ""}
        if variant == "webtoon":
            g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
        else:
            g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
        g["status"] = "running"
        g["activeSince"] = now_iso()  # vnew-live-fx C.10: 다시 시작한 gen 이 createdAt 기준으로 곧바로 멈춤 판정되지 않게.

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    if gen_id_box["id"]:
        _spawn_driver(owner, pid, gen_id_box["id"])
    return updated.get("gen") or {}


def select_variant(owner: str, pid: str, variant: str) -> dict:
    """사용자가 실사/웹툰 중 하나를 고른다(done 일 때만). gen.selected 를 세운다."""
    if variant not in ("real", "webtoon"):
        raise VpocError("vnew.generate", "BadVariant", "실사(real) 또는 웹툰(webtoon)만 고를 수 있습니다.", 400)

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not g:
            raise VpocError("vnew.generate", "NoGen", "영상 생성 기록이 없습니다.", 404)
        if g.get("mode") == "previz-take":
            # vnew-live-fx C.8: 통 테이크는 gen 이 진행 중이 아니고 그 그림체 final 이 done 이면 고른다
            # (실사 회차가 실패해 partial 이어도 웹툰을 고를 수 있다).
            if g.get("status") == "running":
                raise VpocError("vnew.generate", "NotDone", "영상 생성이 끝난 뒤에 고를 수 있습니다.", 409)
        elif g.get("status") != "done":
            raise VpocError("vnew.generate", "NotDone", "영상 생성이 끝난 뒤에 고를 수 있습니다.", 409)
        final = g.get("finalReal") if variant == "real" else g.get("finalWebtoon")
        if (final or {}).get("status") != "done":
            raise VpocError("vnew.generate", "VariantNotReady", "그 그림체 결과가 아직 준비되지 않았습니다.", 409)
        g["selected"] = variant

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    return updated.get("gen") or {}


# ================================================================ 보기(gen_view)


def gen_view(owner: str, pid: str, plan: dict) -> dict | None:
    """GET /plan 의 gen 보기. 영상·이미지 presigned URL, estimate 포함. gen 이 없으면 None."""
    gen = plan.get("gen")
    est = estimate(plan)
    if est.get("mode") == "previz-take":
        # 작업 123: ⑤ 시작 확인창의 [실사] 체크 — 실사 회차 1번 금액(real_estimate 와 같은 계산)과 못 만드는 이유.
        # 진행 중에는 확인창을 열지 않으므로 인물을 읽지 않는다(조회마다 S3 읽기를 늘리지 않는다).
        running = bool(gen) and gen.get("status") == "running"
        est = {
            **est,
            "realUsd": real_estimate({"videoModel": est.get("videoModel"), "resolution": est.get("resolution"),
                                      "units": _take_units(plan)})["usd"],
            "realBlock": "영상 생성이 진행 중입니다." if running else _take_start_real_block(owner, plan),
        }
    if not gen:
        view = {"status": "none", "estimate": est}
        # ⑦ 편집(지시서 115): 켜져 있으면 gen 이 없어도 요약(잠긴 ⑦ 칩과 이유)을 둔다. 꺼져 있으면 키가 없다.
        if vnew_settings.VNEW_EDIT:
            from . import edit as vnew_edit  # noqa: PLC0415 — edit 이 generate 를 import 한다(순환 피함).

            view["edit"] = vnew_edit.summary(gen)
        return view

    gen = _reconcile_gen_stale(gen)

    def _presign_image(key: str) -> str:
        return _presign(key)

    def _presign_video(key: str) -> str:
        return _presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")

    opening = gen.get("opening") or {}
    mode = str(gen.get("mode") or "")
    # 프리비즈 모드: 구간별 프리비즈 녹화 미리보기 URL(작게 보여 준다).
    previz_recordings = (plan.get("previz") or {}).get("recordings") or {}
    segments_out = []
    for seg in gen.get("segments") or []:
        real = seg.get("real") or {}
        web = seg.get("webtoon") or {}
        rec = previz_recordings.get(str(seg.get("id") or "")) or {}
        previz_url = _presign_video(str(rec.get("key") or "")) if mode == "previz" else ""
        segments_out.append(
            {
                "id": seg.get("id"),
                "cutIds": seg.get("cutIds") or [],
                "seconds": seg.get("seconds"),
                "previzUrl": previz_url,
                "real": {
                    "status": real.get("status"),
                    "attempt": real.get("attempt"),
                    "error": real.get("error") or "",
                    "videoUrl": _presign_video(real.get("outputKey") or ""),
                },
                "webtoon": {
                    "status": web.get("status"),
                    "attempt": web.get("attempt"),
                    "error": web.get("error") or "",
                    "videoUrl": _presign_video(web.get("outputKey") or ""),
                },
            }
        )

    # 컷 단위(units): 프리비즈 미리보기 URL = 그 컷이 속한 녹화 구간 mp4. promptEn(보낸 지시서)·바이트 포함.
    def _variant_out(state: dict) -> dict:
        out = {
            "status": state.get("status"),
            "attempt": state.get("attempt"),
            "error": state.get("error") or "",
            "note": state.get("note") or "",
            "videoUrl": _presign_video(state.get("outputKey") or ""),
            "promptEn": state.get("promptEn") or "",
            "promptBytes": state.get("promptBytes") or 0,
            "padded": bool(state.get("padded")),
            # vnew-live-fx C.2: 소리 거절이면 "audio"(화면 '소리 없이 다시 만들기'), 칸 소리 설정(없으면 None).
            "failureKind": state.get("failureKind") or "",
            "audio": state.get("audio") if isinstance(state.get("audio"), bool) else None,
        }
        content_fb = state.get("contentFallback")
        if isinstance(content_fb, dict) and content_fb.get("to"):
            # 작업 153: 콘텐츠 검사로 Seedance 로 만든 칸(화면 한 줄). 없으면 키 없음(옛 보기와 같다).
            out["contentFallback"] = {"from": str(content_fb.get("from") or ""), "to": str(content_fb.get("to") or "")}
        fallback = state.get("fallback")
        if isinstance(fallback, dict) and fallback.get("step"):
            # 작업 130: likeness 단계(⑤ 실사 행 문구). 컷 단위는 끝난 컷 수 / 전체 컷 수. 없으면 키 없음(옛 보기와 같다).
            cuts = [c for c in fallback.get("cuts") or [] if isinstance(c, dict)]
            out["fallback"] = {"step": _fallback_step(state), "reason": str(fallback.get("reason") or ""),
                               "model": str(fallback.get("model") or ""), "total": len(cuts),
                               "done": sum(1 for c in cuts if c.get("status") == "completed")}
            # 작업 133: 컷별 QA 결과 중 문제가 있었던 컷만(처음 틀림·다시 만듦·끝까지 틀림). 없으면 키 없음.
            numbers = {str(c.get("id")): c.get("no") for c in (plan.get("conti") or {}).get("cuts") or []}
            qa_rows = []
            for c in cuts:
                qa = c.get("qa") if isinstance(c.get("qa"), dict) else None
                if qa and (qa.get("ok") is False or qa.get("retried") or qa.get("firstItems")):
                    qa_rows.append({"cutId": str(c.get("cutId") or ""), "no": numbers.get(str(c.get("cutId") or "")),
                                    "ok": qa.get("ok"), "items": list(qa.get("items") or []),
                                    "firstItems": list(qa.get("firstItems") or []), "retried": bool(qa.get("retried"))})
            if qa_rows:
                out["fallback"]["qa"] = qa_rows
        return out

    # 콘티 cut.no(콘티 컷 번호) 를 cutId 로 찾는다(행 머리 "컷 {no}" 표시용).
    cut_no_by_id = {str(c.get("id")): c.get("no") for c in (plan.get("conti") or {}).get("cuts") or []}

    is_take = mode == "previz-take"
    units_out = []
    for unit in gen.get("units") or []:
        if is_take:
            # 통 테이크(설계자 2026-10-26): 프리비즈 미리보기는 테이크 첫 녹화 구간. 컷 범위·draftId 표시.
            rec_keys = [str(k) for k in (unit.get("recKeys") or []) if k]
            previz_url = _presign_video(rec_keys[0]) if rec_keys else ""
            units_out.append(
                {
                    "id": unit.get("id"),
                    "cutIds": list(unit.get("cutIds") or []),
                    "recKeys": rec_keys,
                    "recSegIds": list(unit.get("recSegIds") or []),
                    "seconds": unit.get("seconds"),
                    "startCutNo": unit.get("startCutNo"),
                    "endCutNo": unit.get("endCutNo"),
                    "draftId": unit.get("draftId") or "",
                    "clipped": True,  # 테이크는 녹화 통째라 #t 되감기가 필요 없다.
                    "previzUrl": previz_url,
                    "real": _variant_out(unit.get("real") or {}),
                    "webtoon": _variant_out(unit.get("webtoon") or {}),
                }
            )
            continue
        rec = previz_recordings.get(str(unit.get("recSegId") or "")) or {}
        # 이미 그 컷만 잘라 둔 클립(clipKey)이 있으면 그것을, 없으면 녹화 구간 전체 mp4(#t 로 구간만 재생).
        clip_key = str(unit.get("clipKey") or "")
        source_key = clip_key or str(rec.get("key") or "")
        previz_url = _presign_video(source_key) if mode == "previz" else ""
        units_out.append(
            {
                "id": unit.get("id"),
                "cutId": unit.get("cutId"),
                "cutNo": cut_no_by_id.get(str(unit.get("cutId") or "")),
                "recSegId": unit.get("recSegId"),
                "seconds": unit.get("seconds"),
                "startSec": float(unit.get("startSec") or 0.0),
                "endSec": float(unit.get("endSec") or 0.0),
                "clipKey": clip_key,
                # clipKey 가 있으면 이미 그 컷만 담긴 클립이라 화면이 #t 로 되감을 필요가 없다.
                "clipped": bool(clip_key),
                "previzUrl": previz_url,
                "real": _variant_out(unit.get("real") or {}),
                "webtoon": _variant_out(unit.get("webtoon") or {}),
            }
        )

    final_real = gen.get("finalReal") or {}
    final_webtoon = gen.get("finalWebtoon") or {}
    # vnew-live-fx C.2: 통 테이크 실사 버전 보기. 이전 회차 영상은 presigned(브라우저 표시용, 모델에는 보내지 않는다).
    real_history_out = []
    for item in gen.get("realHistory") or []:
        if not isinstance(item, dict):
            continue
        real_history_out.append({
            "n": item.get("n"),
            "startedAt": item.get("startedAt") or "",
            "status": item.get("status") or "",
            "audio": bool(item.get("audio")),
            "finalUrl": _presign_video(str(item.get("finalKey") or "")),
            "units": [
                {"id": u.get("id"), "status": u.get("status"), "error": u.get("error") or ""}
                for u in item.get("units") or [] if isinstance(u, dict)
            ],
        })
        if isinstance(item.get("cast"), list) and item.get("cast"):
            # 작업 126: 인물 바꾸기 회차의 머리 "인물: 이름→캐릭터(실사/애니)"(없으면 키 없음 — 옛 보기와 같다).
            real_history_out[-1]["cast"] = [
                {k: str(c.get(k) or "") for k in ("name", "charId", "look", "charName")}
                for c in item["cast"] if isinstance(c, dict)
            ]
    real_block = _real_block_text(owner, plan, gen) if is_take else ""
    auto_real = gen.get("autoReal")
    view = {
        "genId": gen.get("genId"),
        "mode": mode,
        "status": gen.get("status"),
        "videoModel": gen.get("videoModel"),
        "resolution": gen.get("resolution"),
        "createdAt": gen.get("createdAt"),
        "opening": {
            "status": opening.get("status"),
            "error": opening.get("error") or "",
            "imageUrl": _presign_image(opening.get("imageKey") or ""),
        },
        "segments": segments_out,
        "units": units_out,
        "finalReal": {
            "status": final_real.get("status"),
            "error": final_real.get("error") or "",
            "note": final_real.get("note") or "",
            "videoUrl": _presign_video(final_real.get("key") or ""),
            "n": final_real.get("n"),
        },
        "finalWebtoon": {
            "status": final_webtoon.get("status"),
            "error": final_webtoon.get("error") or "",
            "note": final_webtoon.get("note") or "",
            "videoUrl": _presign_video(final_webtoon.get("key") or ""),
        },
        "selected": gen.get("selected"),
        "costUsd": gen.get("costUsd", 0.0),
        "estimate": est,
        # 프리비즈가 바뀌어 이 gen 의 계획(녹화·지시서)이 지금과 맞지 않으면 True(설계자 2026-10-22, ⑤).
        # 화면은 stale 이면 '새로 만들기'로 유도하고 이전 결과는 접이식으로만 보인다.
        "stale": _gen_previz_stale(gen, plan),
        # vnew-live-fx C.2: 실사 회차(realRun)·이전 회차(최대 3)·금액·시작 가능 여부와 못 하는 이유(한국어).
        "realRun": dict(gen.get("realRun")) if isinstance(gen.get("realRun"), dict) else None,
        "realHistory": real_history_out,
        "realEstimate": real_estimate(gen) if is_take else None,
        "canStartReal": bool(is_take and not real_block),
        "realBlock": real_block,
        "realEnabled": bool(vnew_settings.VNEW_TAKE_REAL),  # vnew-live-fx C3: 화면이 실사 열을 그릴지(끔 + 회차 없음이면 지금 화면)
        # 작업 123: 처음부터 실사도 함께 고른 gen 이면 {confirmUsd, audio, at, startedAt, error}, 아니면 None.
        "autoReal": {k: auto_real.get(k) for k in ("confirmUsd", "audio", "at", "startedAt", "error")}
        if isinstance(auto_real, dict) else None,
    }
    # ⑦ 편집(지시서 110): 켜져 있으면 끝에 요약 {available, variants}(S3 를 읽지 않는다). 꺼져 있으면 키가 없다(⑦ 단계 없음).
    if is_take:
        fallback_max = real_fallback_max(plan, gen)
        if fallback_max:
            view["realFallbackMax"] = fallback_max  # 작업 130 보완 1: 확인창 "최대 $X" 안내(2단계를 쓸 수 없으면 키 없음)
    if vnew_settings.VNEW_EDIT:
        from . import edit as vnew_edit  # noqa: PLC0415 — edit 이 generate 를 import 한다(순환 피함).

        view["edit"] = vnew_edit.summary(gen)
    return view


def _reconcile_gen_stale(gen: dict) -> dict:
    """조회 때: gen 이 오래 running 이면 뷰에서 partial 로 본다(저장하지 않음)."""
    if gen.get("status") == "running" and _gen_stale(gen):
        gen = dict(gen)
        gen["status"] = "partial"
    return gen


def _gen_stale(gen: dict) -> bool:
    if gen.get("status") != "running":
        return False
    # vnew-live-fx C.10: 실사 회차 시작·다시 시도 때 둔 activeSince 를 먼저 본다(없으면 지금처럼 createdAt).
    # 30분 지난 gen 에서 다시 시작한 작업이 곧바로 멈춘 것으로 보이던 결함을 고쳤다.
    created = plan_mod._parse_iso(gen.get("activeSince") or gen.get("createdAt") or "")
    if not created:
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() > GEN_STALL_S


def _gen_previz_stale(gen: dict, plan: dict) -> bool:
    """이 previz 모드 gen 이 **지금 프리비즈와 맞지 않는지**(설계자 2026-10-22, ⑤).

    사용자가 영상을 만들다 취소/완료한 뒤 프리비즈를 다시 설계·녹화하면, 옛 gen 의 계획(컷·녹화)은
    새 녹화와 어긋난다. 그때 화면이 '새로 만들기'로 유도하도록 True 를 돌려준다. 판정 순서:
      1) previz 모드가 아니면 stale 아님(옛 i2v 모드는 해당 없음).
      2) gen 에 previzRevision 이 있으면 현재 previz.revision 과 비교 — 다르면 stale.
      3) gen 에 briefUpdatedAt 이 있으면 현재 shotBrief.updatedAt 과 비교 — 다르면 stale.
      4) previzRevision 이 아예 없는 옛 gen 은 녹화 키로 본다 — 각 컷(unit)의 recordingKey 가
         현재 previz.recordings 의 그 구간 키와 하나라도 다르면 stale(혹은 지금 녹화가 비었으면 stale).
    """
    if gen.get("mode") != "previz":
        return False
    previz = plan.get("previz") or {}
    cur_rev = int(previz.get("revision") or 0)
    cur_brief = str((previz.get("shotBrief") or {}).get("updatedAt") or "")

    if "previzRevision" in gen:
        if int(gen.get("previzRevision") or 0) != cur_rev:
            return True
        # 지시서가 새로 만들어졌으면(값이 있을 때만 비교) stale.
        gen_brief = str(gen.get("briefUpdatedAt") or "")
        if gen_brief and cur_brief and gen_brief != cur_brief:
            return True
        return False

    # 옛 gen(previzRevision 없음): 녹화 키 비교.
    recordings = previz.get("recordings") or {}
    cells = _previz_cells(gen)
    for cell in cells:
        seg_id = str(cell.get("recSegId") or cell.get("id") or "")
        stored_key = str(cell.get("recordingKey") or "")
        cur_key = str((recordings.get(seg_id) or {}).get("key") or "")
        if stored_key and cur_key and stored_key != cur_key:
            return True
        if stored_key and not cur_key:
            return True
    return False


# ================================================================ 원장 · presign · 저장


def _charge_seedream(owner: str, pid: str, gen_id: str) -> None:
    key = f"vnew-gen-{pid}-{gen_id}-opening-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="image",
            model=vnew_settings.SEEDREAM_MODEL,
            units=1,
            usd=float(SEEDREAM_USD),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_seedream_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_video(owner: str, pid: str, gen_id: str, result: dict, model: str, resolution: str, seconds: int) -> None:
    """영상 제출 1회 계상(제출 성공 시점). 키 = 작업 토큰(중복 방지)."""
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    try:
        ark_id = vpoc_settings.ARK_MODEL_IDS.get(model) if model else None
        vpoc_ledger.charge(
            token,
            kind="video",
            model=ark_id or vpoc_settings.VIDEO_MODEL,
            units=seconds or vpoc_settings.DURATION_S,
            usd=vpoc_settings.shot_usd(model, resolution, seconds),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_video_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_anime(owner: str, pid: str, gen_id: str, result: dict, seconds: float) -> None:
    """웹툰 변환 제출 1회 계상. 키 = 작업 토큰."""
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    try:
        vpoc_ledger.charge(
            token,
            kind="video",
            model=vpoc_settings.VPOC_ANIME_MODEL,
            units=seconds or vpoc_settings.DURATION_S,
            usd=vpoc_settings.anime_usd(seconds or None),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_anime_failed pid=%s err=%s", pid, str(exc)[:200])


def _presign(key: str, *, bucket: str = "", content_type: str = "") -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, bucket=bucket, content_type=content_type, expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.presign_failed key=%s err=%s", key, str(exc)[:150])
        return ""


def _commit(owner: str, pid: str, mutator) -> None:
    # 계획별 락 안에서 수행한다(프리비즈 동시 제출의 갱신 유실 방지). 옛 모드는 단일 스레드라
    # 락이 비어 있어 영향이 없다.
    _mutate_locked(owner, pid, mutator)


def _fail_opening(owner: str, pid: str, gen_id: str, claim: str, message: str) -> None:
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or (g.get("opening") or {}).get("claim") != claim:
            raise _Discard()
        g["opening"] = {"status": "failed", "imageKey": "", "error": message, "claim": "", "claimedAt": ""}
        g["status"] = "partial"

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _user_facing_error(message: str) -> str:
    """구간 실패 메시지를 사용자용으로 바꾼다(설계자 2026-10-20).

    Seedance(BytePlus) 실존 인물 필터의 'may contain real person' 오류는 무엇을 고칠지 모를
    영어 원문이라, 모델을 바꾸라는 한국어 문구로 바꿔 저장한다. 그 밖은 원문 그대로.
    """
    low = str(message or "").lower()
    if "may contain real person" in low or "real person" in low:
        return "실사 인물 시작 이미지를 이 모델이 받지 않습니다. 모델을 바꿔 다시 만들어 주세요."
    return message


def _fail_variant(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, message: str,
                  kind: str = "") -> None:
    """칸 하나를 failed 로 둔다. kind(vnew-live-fx C.6, 예 "audio")가 있으면 state.failureKind, 없으면 그 키를 지운다."""
    message = _user_facing_error(message)

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        cells = _previz_cells(g) if _is_previz_mode(g) else (g.get("segments") or [])
        state = cells[index][variant]
        state["status"] = "failed"
        state["error"] = message
        state["claim"] = ""
        state["claimedAt"] = ""
        if kind:
            state["failureKind"] = str(kind)
        else:
            state.pop("failureKind", None)
        # 작업 163: 조각 칸이 실패로 끝나면 아직 내지 않은(pending) 뒤 조각은 cancelled — pending 으로 남지 않는다.
        for part in state.get("parts") or []:
            if isinstance(part, dict) and part.get("status") == "pending":
                part["status"] = "cancelled"
        # 옛 순차 모드: 실사 구간 실패는 그 뒤 진행을 막고 gen partial. 웹툰 실패는 실사를 막지 않는다.
        # 프리비즈 컷 단위·통 테이크: 칸은 서로 독립 — 한 칸 실패가 다른 칸을 막지 않는다(실패 칸만 retry).
        # vnew-live-fx C.10: previz-take 실사 실패가 gen 을 바로 partial 로 바꿔 드라이버를 멈추던 결함을 고쳤다.
        if variant == "real" and g.get("mode") not in ("previz", "previz-take"):
            g["status"] = "partial"

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _audio_kind(message: str) -> str:
    """실패 문구가 소리 검사 거절(fal 안내 문구 또는 audio+sensitive 원문)이면 "audio", 아니면 ""(vnew-live-fx C.6)."""
    text = str(message or "")
    if text == vpoc_fal._REJECT_AUDIO_MSG or vpoc_fal._is_audio_rejection(text):
        return "audio"
    return ""


def _msg(exc: BaseException) -> str:
    return exc.message if isinstance(exc, VpocError) else str(exc)[:300]


class _Discard(Exception):
    """늦게 온 결과(claim 불일치·회차 바뀜)를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


# ================================================================ ⑦ AI로 고치기 — 프롬프트 기록·다시 제출(지시서 116, 2026-11-02)
#
# 제출 지점(_do_submit_previz·_do_submit_take)과 조회 결과(_do_poll_previz)마다 기록 한 줄(_log_attempt·_log_result → retake.py,
# {gen 루트}/prompts.json). 참고 이미지 S3 키는 payload 를 만드는 동안 같은 스레드에서만 따라간다: _load_jpeg·_segment_panel_jpegs 가 읽은
# 바이트(id → 키)와 _fit_ref 가 맞춘 바이트(id → 원래 키)를 _RefTrace 에 적고, 실제로 보낸 elements·imageUrls 순서대로 키를 찾는다(바이트는
# 붙잡지 않는다 — 정수 id 와 키 글자만). 기록은 VNEW_EDIT_RETAKE 가 켜져 있을 때만 쓰고, 기록 실패는 제출을 막지 않는다(경고 1줄).
# resubmit_cell 은 ⑦ 다시 만들기를 그 칸의 원래 경로로 제출한다(칸 상태 gen.units 는 읽기만 한다).

_REF_TRACE = threading.local()


class _RefTrace:
    """이 스레드의 참고 이미지 읽기·맞춤을 따라간다. with 블록 또는 start() … stop(built). refs(built) = 보낸 순서의 [{slot, key}].

    start() 는 이 스레드의 앞 추적을 늘 바꿔 끼운다(payload 가 예외로 끝나 stop 을 못 불러도 다음 start 가 정리한다 — 남는 것은 정수 id·키
    글자뿐이다). ⑤ 제출 함수는 들여쓰기를 바꾸지 않으려고 start/stop 을, resubmit_cell 은 with 를 쓴다.
    """

    def __init__(self) -> None:
        self.loads: dict[int, str] = {}
        self.fits: dict[int, str] = {}
        self._prev = None

    @classmethod
    def start(cls) -> "_RefTrace":
        box = cls()
        _REF_TRACE.box = box
        return box

    def stop(self, built) -> list[dict]:
        if getattr(_REF_TRACE, "box", None) is self:
            _REF_TRACE.box = None
        return self.refs(built)

    def __enter__(self) -> "_RefTrace":
        self._prev = getattr(_REF_TRACE, "box", None)
        _REF_TRACE.box = self
        return self

    def __exit__(self, *_exc) -> bool:
        _REF_TRACE.box = self._prev
        return False

    def refs(self, built) -> list[dict]:
        """elements(정면 → 뒤) 다음 imageUrls 순서. 키를 모르는 바이트(검사 가짜 등)는 빈 키다."""
        out: list[dict] = []
        built = built if isinstance(built, dict) else {}
        for number, element in enumerate(built.get("elements") or [], 1):
            if not isinstance(element, dict):
                continue
            out.append({"slot": f"element{number}.front", "key": self.fits.get(id(element.get("frontal_image_url")), "")})
            for ref_no, ref in enumerate(element.get("reference_image_urls") or [], 1):
                out.append({"slot": f"element{number}.ref{ref_no}", "key": self.fits.get(id(ref), "")})
        for number, image in enumerate(built.get("imageUrls") or [], 1):
            out.append({"slot": f"image{number}", "key": self.fits.get(id(image), "")})
        return out


def _ref_note_load(key: str, data) -> None:
    """_RefTrace 안이면 읽은 바이트의 id → S3 키(_load_jpeg·_segment_panel_jpegs). 밖이면 아무것도 하지 않는다."""
    box = getattr(_REF_TRACE, "box", None)
    if box is not None and data is not None:
        box.loads[id(data)] = str(key or "")


def _ref_note_fit(source, out) -> None:
    """_RefTrace 안이면 맞춘 바이트의 id → 원래 바이트의 키(_fit_ref). 맞춤이 원본을 그대로 돌려주면 같은 id 다."""
    box = getattr(_REF_TRACE, "box", None)
    if box is not None and out is not None:
        box.fits[id(out)] = box.loads.get(id(source), "")


def _take_params(caps: dict | None, seconds: float, resolution: str, audio) -> dict:
    """통 테이크 제출 본문의 매개변수(_submit_take 와 같은 규칙, 기록용). 프롬프트·영상·이미지는 넣지 않는다."""
    caps = caps or {}
    params: dict = {"seconds": round(float(seconds or 0), 3), "resolution": str(resolution), "aspectRatio": "16:9"}
    duration_int = max(4, min(30, int(round(float(seconds or 4)))))
    duration_value = _take_duration_value(caps, duration_int)
    if duration_value is not None:
        params["duration"] = str(duration_value) if bool(caps.get("durationAsString")) else duration_value
    if caps.get("taskField"):
        params["task"] = "reference"
    if caps.get("generateAudioField"):
        params["audio"] = bool(vnew_settings.VNEW_TAKE_AUDIO if audio is None else audio)
    if caps.get("draftField"):
        params["draft"] = bool(vnew_settings.VNEW_TAKE_DRAFT)
    return params


def _cut_params(unit: dict, clip_meta, caps: dict | None) -> dict:
    """컷 단위 제출의 매개변수(_submit_previz_edit 와 같은 규칙, 기록용): 컷 길이·녹화 안 구간·보낸 길이·채움·keep_audio."""
    caps = caps or {}
    meta = clip_meta if isinstance(clip_meta, dict) else {}
    params: dict = {
        "seconds": round(float(unit.get("seconds") or 0), 3),
        "startSec": round(float(unit.get("startSec") or 0), 3),
        "endSec": round(float(unit.get("endSec") or 0), 3),
        "sentSeconds": meta.get("sentSeconds"),
        "origSeconds": meta.get("origSeconds"),
        "padded": bool(meta.get("padded")),
    }
    if caps.get("keepAudioField") or not caps:
        params["keepAudio"] = False
    return params


def _log_attempt(owner: str, pid: str, gen_id: str, *, source: str = "gen", mode: str = "", variant: str = "", cell_id: str = "",
                 index: int = 0, attempt: int = 0, run=None, endpoint: str = "", params: dict | None = None,
                 refs: list | None = None, video_keys: list | None = None, result: dict | None = None, prompt: str = "",
                 version=None, status: str = "submitted", error: str = "") -> str:
    """제출 한 번의 프롬프트 기록 한 줄(retake.log_submit). 끔이면 쓰지 않는다. 예외를 내지 않는다(경고 1줄). 반환 기록 id 또는 ""."""
    if not vnew_settings.VNEW_EDIT_RETAKE or not mode:
        return ""
    try:
        from . import retake as vnew_retake  # noqa: PLC0415 — retake 가 generate 를 import 한다(순환 피함).

        result = result if isinstance(result, dict) else {}
        provider = str(result.get("provider") or vpoc_settings.FAL_ID_PREFIX + str(endpoint or ""))
        return vnew_retake.log_submit(owner, pid, gen_id, {
            "source": source, "mode": mode, "variant": variant, "cellId": str(cell_id), "cellIndex": index,
            "attempt": attempt, "run": run, "version": version, "provider": provider.split(":", 1)[0],
            "model": str(endpoint or ""), "params": params or {}, "refs": refs or [], "videoKeys": video_keys or [],
            "prompt": prompt, "invocationArn": str(result.get("invocationArn") or ""),
            "outputPrefix": str(result.get("outputPrefix") or ""), "status": status, "error": error,
        })
    except Exception as exc:  # noqa: BLE001 — 기록은 영상 생성을 막지 않는다.
        log.warning("vnew.gen.prompt_log_failed pid=%s cell=%s stage=submit err=%s", pid, cell_id, type(exc).__name__)
        return ""


def _log_result(owner: str, pid: str, gen_id: str, gen: dict, index: int, variant: str, arn: str, status: str, *,
                output_key: str = "", error: str = "") -> None:
    """조회가 끝난 칸의 기록(같은 invocationArn)을 completed·failed 로 고친다(retake.log_result). 끔이면 아무것도 하지 않는다."""
    if not vnew_settings.VNEW_EDIT_RETAKE or not arn:
        return
    try:
        from . import retake as vnew_retake  # noqa: PLC0415

        cell = _previz_cells(gen)[index]
        cell_id = str(cell.get("id") or cell.get("cutId") or index)
        vnew_retake.log_result(owner, pid, gen_id, f"{variant}:{cell_id}", arn, status, output_key=output_key, error=error)
    except Exception as exc:  # noqa: BLE001 — 기록은 영상 생성을 막지 않는다.
        log.warning("vnew.gen.prompt_log_failed pid=%s cell=%s stage=result err=%s", pid, index, type(exc).__name__)


def resubmit_cell(owner: str, pid: str, gen_id: str, *, variant: str, cell_id: str, prompt: str, tag: str,
                  attempt: int = 1, version=None) -> dict:
    """⑦ AI로 고치기: 칸 하나를 원래 경로(같은 모델·caps·녹화·참고 이미지 규칙·매개변수)로 새 프롬프트만 바꿔 제출한다.

    통 테이크 = _stitch_recordings + _take_payload(실사는 variant="real" + _assert_no_webtoon) + _submit_take(칸 state.audio),
    컷 단위 = _fetch_bytes(recordingKey) + _cut_clip + _previz_payload(caps) + _submit_previz_edit. payload 가 만든 프롬프트는 버린다.
    준비~제출은 _PREPARE_SEM 안(⑤ 와 같은 메모리 규칙), 원장은 _charge_take·_charge_previz(작업 토큰 키, 한 번), 기록 source=retake.
    결과 접두사는 "{cellId}-{variant}-{tag}"(예 take1-webtoon-rt2) + attempt 라 원래 결과와 겹치지 않는다. gen 상태는 바꾸지 않는다.
    반환 {invocationArn, outputPrefix, provider, usd, logId}.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        raise VpocError("vnew.retake", "RetakeGenChanged", "영상 생성 기록이 바뀌었습니다. ⑦ 편집을 다시 열어 주세요.", 409)
    mode = str(gen.get("mode") or "")
    if variant not in ("real", "webtoon") or mode not in ("previz", "previz-take"):
        raise VpocError("vnew.retake", "RetakeNotTarget", "이 영상은 AI로 고칠 수 없습니다.", 409)
    cells = _previz_cells(gen)
    index = next((i for i, c in enumerate(cells) if str(c.get("id") or c.get("cutId") or i) == str(cell_id)), -1)
    if index < 0:
        raise VpocError("vnew.retake", "RetakeNotTarget", "고칠 칸을 찾을 수 없습니다.", 409)
    cell = cells[index]
    state = cell.get(variant) if isinstance(cell.get(variant), dict) else {}
    take = mode == "previz-take"
    real = variant == "real"
    endpoint = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    caps = _previz_caps(endpoint)
    seconds = float(cell.get("seconds") or 0)
    resolution = str(gen.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION)
    audio_choice = state.get("audio") if isinstance(state.get("audio"), bool) else None
    run_n = (int((gen.get("realRun") or {}).get("n") or 0) or None) if take and real else None
    attempt = max(1, int(attempt or 1))
    prefix = _seg_output_prefix(owner, pid, gen_id, f"{cell_id}-{variant}-{tag}", attempt)
    started = time.monotonic()
    _PREPARE_SEM.acquire()
    try:
        if take:
            video_keys = [str(k) for k in (cell.get("recKeys") or []) if k]
            if not video_keys:
                raise VpocError("vnew.generate", "NoRecording", "테이크 녹화가 없습니다.", 409)
            if real:
                _assert_no_webtoon(prompt)
            video_mp4 = _stitch_recordings(video_keys)
            with _RefTrace() as trace:
                built = _take_payload(owner, pid, plan, cell, caps, variant="real") if real else _take_payload(
                    owner, pid, plan, cell, caps)
            refs = trace.refs(built)
            params = _take_params(caps, seconds, resolution, audio_choice)
            result = _submit_take(endpoint=endpoint, caps=caps, video_mp4=video_mp4, prompt=prompt, output_prefix=prefix,
                                  image_urls=built["imageUrls"], seconds=seconds, resolution=resolution, audio=audio_choice)
            del video_mp4
        else:
            rec_key = str(cell.get("recordingKey") or "")
            if not rec_key:
                raise VpocError("vnew.generate", "NoRecording", "컷 녹화가 없습니다.", 409)
            video_keys = [rec_key]
            video_mp4 = _fetch_bytes(rec_key, bucket=vpoc_settings.VIDEO_BUCKET)
            clip = _cut_clip(video_mp4, cell)
            del video_mp4
            with _RefTrace() as trace:
                built = _previz_payload(owner, pid, plan, cell, variant, caps=caps)
            refs = trace.refs(built)
            params = _cut_params(cell, clip, caps)
            result = _submit_previz_edit(endpoint=endpoint, caps=caps, video_mp4=clip["bytes"], prompt=prompt,
                                         output_prefix=prefix, elements=built["elements"], image_urls=built["imageUrls"])
            del clip
        del built
    finally:
        _PREPARE_SEM.release()
    if take:
        _charge_take(owner, pid, gen_id, result, seconds, resolution)
        usd = _take_usd(endpoint, seconds, resolution)
    else:
        billed = max(PREVIZ_MIN_CLIP_S, seconds)
        _charge_previz(owner, pid, gen_id, result, billed)
        usd = round(_previz_model_unit_price(endpoint) * billed, 4)
    log_id = _log_attempt(owner, pid, gen_id, source="retake", mode=mode, variant=variant, cell_id=str(cell_id), index=index,
                          attempt=attempt, run=run_n, endpoint=endpoint, params=params, refs=refs, video_keys=video_keys,
                          result=result, prompt=prompt, version=version)
    log.info("vnew.gen.resubmit pid=%s cell=%s variant=%s tag=%s attempt=%d bytes=%d refs=%d ms=%d rss_mb=%.0f", pid, cell_id,
             variant, tag, attempt, len(prompt.encode("utf-8")), len(refs), int((time.monotonic() - started) * 1000), _rss_mb())
    return {"invocationArn": str(result.get("invocationArn") or ""), "outputPrefix": str(result.get("outputPrefix") or prefix),
            "provider": str(result.get("provider") or ""), "usd": usd, "logId": log_id}



# ================================================================ ⑦ AI로 고치기 보완 1 — 모델 고르기·구간(지시서 116 보완 1, 2026-11-02)
#
# 고른 영상 모델·범위(원본 칸 전체 | 이 구간만)로 다시 만드는 새 길이다. retake.py 가 116 같은 경로(원래 모델·원본 칸 전체)가 아닐 때만 부른다
# (design D18). 경로(path)는 모델이 영상을 받는 방식이다: 참고 영상 경로 "take"(caps videoListField → _submit_take)와 영상 편집 경로 "cut"
# (그 밖 → _submit_previz_edit). 원래 모델은 칸 종류(테이크·컷)를 따른다. 견적(retake_plan)은 retake.py 보기와 이 제출이 같은 함수를 쓴다.
# 위 116 함수(resubmit_cell 등)는 바꾸지 않는다.

RETAKE_EDIT_MAX_S = float(stitcher.ANIME_MAX_SECONDS)  # 영상 편집 모델(Kling edit) 입력 상한(3~15초 규격)
RETAKE_TAKE_MIN_S = 4  # 참고 영상 경로 duration 범위(_submit_take 가 4~30 으로 자른다)
RETAKE_TAKE_MAX_S = 30
_RETAKE_RES_ORDER = ("480p", "720p", "1080p")
RETAKE_FORMAT_CHANGED_TEXT = "참고 이미지 구성이 새 프롬프트를 만들 때와 달라 제출하지 않았습니다. 새 프롬프트를 다시 만들어 주세요."
RETAKE_MODEL_GONE_TEXT = "고른 영상 모델을 지금 이 칸에 쓸 수 없어 제출하지 않았습니다. 모델을 다시 골라 주세요."


def retake_path(model: str, *, original: str, kind: str) -> str:
    """다시 만들 경로. 원래 모델은 칸 종류 그대로("take"|"cut" — 116 과 같다), 그 밖은 caps.videoListField 가 있으면 "take", 없으면 "cut"."""
    if str(model or "") == str(original or ""):
        return "take" if kind == "take" else "cut"
    return "take" if _previz_caps(model).get("videoListField") else "cut"


def _retake_resolution(caps: dict | None, wanted: str) -> tuple[str, str] | None:
    """참고 영상 경로 해상도 (비교·원장용 소문자, 보낼 스키마 표기). gen 해상도를 받으면 그것, 아니면 480p·720p·1080p 중 받는 가장 낮은 값.

    caps 에 해상도 목록이 없으면 gen 해상도. 원장 공식(TAKE_RESOLUTION_WH)에 없는 값이면 None(고를 수 없다).
    """
    caps = caps or {}
    wanted = str(wanted or vnew_settings.VNEW_TAKE_RESOLUTION or "").lower()
    values = [str(v).lower() for v in caps.get("resolutions") or []]
    pick = wanted if (not values or wanted in values) else next((r for r in _RETAKE_RES_ORDER if r in values), "")
    if not pick or pick not in vnew_settings.TAKE_RESOLUTION_WH:
        return None
    raw_map = caps.get("resolutionValues") if isinstance(caps.get("resolutionValues"), dict) else {}
    return pick, str(raw_map.get(pick) or pick)


def _retake_price(model: str, path: str, seconds: float, resolution: str) -> tuple[float, bool]:
    """다시 만들기 금액(USD 소수 4자리)과 단가를 아는지(design D20).

    참고 영상 경로: 단가 unit "seconds" → 단가 × 초, 그 밖은 _take_usd(토큰 공식 — "1000 tokens" 단가, 모르면 기본 토큰 단가).
    영상 편집 경로: _previz_model_unit_price(⑤ 와 같다 — 기본 모델·초당 단가, 모르면 기본 초당 단가) × 초.
    """
    endpoint = str(model or "")
    default = endpoint == str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    price = None
    if path == "take" or not default:
        price = vpoc_fal.pricing_for(vpoc_settings.FAL_ID_PREFIX + endpoint) or vpoc_fal.pricing(endpoint)
    unit = str((price or {}).get("unit") or "").lower().replace("-", " ")
    unit_price = float((price or {}).get("unitPrice") or 0.0)
    if path == "take":
        if unit == "seconds" and unit_price > 0:
            return round(unit_price * float(seconds or 0), 4), True
        return _take_usd(endpoint, seconds, resolution), unit == "1000 tokens" and unit_price > 0
    known = default or (unit == "seconds" and unit_price > 0)
    return round(_previz_model_unit_price(endpoint) * float(seconds or 0), 4), known


def retake_plan(gen: dict, *, model: str, path: str, seconds: float, original: bool) -> dict:
    """고른 모델·길이로 다시 만들 때의 견적(design D20). ok 가 False 면 reason(고정 문구 + 서버 숫자).

    참고 영상 경로: duration = enum(4~30, enum 이 없으면 4~30 정수) 중 필요 길이 이상인 가장 짧은 값(올림), 과금 = 그 길이, 해상도 = 원래 모델은
    gen 해상도, 그 밖은 _retake_resolution. 영상 편집 경로: 입력 = max(3, 길이)(채움), 원래 모델이 아니면 입력 15초 이하. trimTo = 필요 길이.
    """
    gen = gen if isinstance(gen, dict) else {}
    caps = _previz_caps(model)
    length = round(max(0.0, float(seconds or 0)), 3)
    out: dict = {"ok": False, "reason": "", "path": path, "seconds": length, "sentSeconds": None, "billedSeconds": None,
                 "durations": [], "durationValue": None, "resolution": "", "resolutionValue": "", "usd": None, "priceKnown": False,
                 "maxBytes": 0, "trimTo": length}
    if path == "take":
        enum: set[int] = set()
        for value in caps.get("durations") or []:
            try:
                enum.add(int(value))
            except (TypeError, ValueError):
                continue
        allowed = sorted(d for d in enum if RETAKE_TAKE_MIN_S <= d <= RETAKE_TAKE_MAX_S) if enum else \
            list(range(RETAKE_TAKE_MIN_S, RETAKE_TAKE_MAX_S + 1))
        out["durations"] = allowed
        fits = [d for d in allowed if d >= length - 0.05]
        if not fits:
            out["reason"] = f"이 모델은 최대 {max(allowed) if allowed else 0}초까지 만듭니다(필요 {length:g}초)."
            return out
        sent = fits[0]
        wanted = str(gen.get("resolution") or vnew_settings.VNEW_TAKE_RESOLUTION)
        res = (wanted, wanted) if original else _retake_resolution(caps, wanted)
        if not res:
            out["reason"] = "이 모델의 해상도를 정할 수 없습니다."
            return out
        usd, known = _retake_price(model, "take", sent, res[0])
        value = _take_duration_value(caps, sent)
        out.update(ok=True, sentSeconds=float(sent), billedSeconds=float(sent),
                   durationValue=str(value) if caps.get("durationAsString") else value, resolution=res[0], resolutionValue=res[1],
                   usd=usd, priceKnown=known, maxBytes=int(_take_prompt_budget(caps)))
        return out
    if not original and length > RETAKE_EDIT_MAX_S + 0.05:
        out["reason"] = f"이 모델은 영상을 {RETAKE_EDIT_MAX_S:g}초까지 받습니다(필요 {length:g}초)."
        return out
    billed = round(max(PREVIZ_MIN_CLIP_S, length), 3)
    usd, known = _retake_price(model, "cut", billed, "")
    max_bytes = int(vnew_settings.PREVIZ_PROMPT_TARGET)
    if not original and int(caps.get("promptMaxLength") or 0) > 0:
        max_bytes = min(max_bytes, int(caps.get("promptMaxLength")))
    out.update(ok=True, sentSeconds=billed, billedSeconds=billed, usd=usd, priceKnown=known, maxBytes=max_bytes)
    return out


def _retake_cell(plan: dict, gen: dict, gen_id: str, variant: str, cell_id: str) -> tuple[int, dict, str]:
    """다시 만들 칸(resubmit_cell 과 같은 거절 규칙·문구). 반환 (index, 칸, gen 모드)."""
    if str(gen.get("genId") or "") != gen_id:
        raise VpocError("vnew.retake", "RetakeGenChanged", "영상 생성 기록이 바뀌었습니다. ⑦ 편집을 다시 열어 주세요.", 409)
    mode = str(gen.get("mode") or "")
    if variant not in ("real", "webtoon") or mode not in ("previz", "previz-take"):
        raise VpocError("vnew.retake", "RetakeNotTarget", "이 영상은 AI로 고칠 수 없습니다.", 409)
    cells = _previz_cells(gen)
    index = next((i for i, c in enumerate(cells) if str(c.get("id") or c.get("cutId") or i) == str(cell_id)), -1)
    if index < 0:
        raise VpocError("vnew.retake", "RetakeNotTarget", "고칠 칸을 찾을 수 없습니다.", 409)
    return index, cells[index], mode


def _retake_like(cell: dict, *, kind: str, path: str, span: dict | None, cut_ids: list | None) -> dict:
    """payload 빌더에 줄 칸 사본(design D22). cutIds = 구간 안 컷(없으면 칸 전체), seconds = 범위 길이.

    참고 영상 경로는 _take_payload 가 읽는 테이크 꼴(id·cutIds·recKeys·recSegIds·seconds), 영상 편집 경로는 _previz_payload 가 읽는 컷 꼴
    (id·cutId·cutIds·recordingKey·startSec·endSec·seconds — 컷 칸은 녹화 안 시각, 테이크 칸은 이어붙인 영상 안 시각).
    """
    ids = [str(c) for c in (cut_ids or []) if c] or _seg_cut_ids(cell)
    start = float((span or {}).get("start") or 0.0) if span else 0.0
    end = float((span or {}).get("end") or 0.0) if span else float(cell.get("seconds") or 0.0)
    seconds = round(max(0.0, end - start), 3)
    cell_id = str(cell.get("id") or cell.get("cutId") or "")
    if path == "take":
        rec_keys = [str(k) for k in cell.get("recKeys") or [] if k] or ([str(cell["recordingKey"])] if cell.get("recordingKey") else [])
        seg_ids = [str(s) for s in cell.get("recSegIds") or [] if s] or ([str(cell["recSegId"])] if cell.get("recSegId") else [])
        return {"id": cell_id, "cutIds": ids, "recKeys": rec_keys, "recSegIds": seg_ids, "seconds": seconds}
    base = float(cell.get("startSec") or 0.0) if kind == "cut" else 0.0
    return {"id": cell_id, "cutId": ids[0] if ids else "", "cutIds": ids, "recordingKey": str(cell.get("recordingKey") or ""),
            "recSegId": str(cell.get("recSegId") or ""), "startSec": round(base + start, 3), "endSec": round(base + start + seconds, 3),
            "seconds": seconds}


def _pad_clip(video_mp4: bytes, seconds: float) -> bytes:
    """영상을 seconds 초까지 마지막 프레임으로 채운다(stitcher.pad_to_seconds, -threads 1, 소리 없음). 임시 폴더는 그 자리에서 지운다."""
    with tempfile.TemporaryDirectory(prefix="vnew-retake-pad-") as workdir:
        source = os.path.join(workdir, "in.mp4")
        target = os.path.join(workdir, "out.mp4")
        with open(source, "wb") as handle:
            handle.write(video_mp4)
        stitcher.pad_to_seconds(source, target, target_sec=float(seconds), threads=1)
        with open(target, "rb") as handle:
            return handle.read()


def _retake_video(cell: dict, *, kind: str, path: str, span: dict | None, pad_to: float) -> tuple[bytes, dict, list[str]]:
    """다시 만들 입력 영상(design D22 표). 반환 (mp4, {origSeconds, sentSeconds, padded}, 녹화 키).

    테이크 칸: 녹화를 이어붙이고, 구간이거나 영상 편집 경로면 _cut_clip(3초 채움·720 높이). 컷 칸: 녹화에서 컷 시작 + 구간을 _cut_clip.
    참고 영상 경로는 보낼 길이(pad_to)까지 _pad_clip 으로 채운다(결과 시각이 녹화 시각과 맞게 — 뒤는 trimTo 로 자른다).
    """
    length = float(cell.get("seconds") or 0.0)
    start = float((span or {}).get("start") or 0.0) if span else 0.0
    end = float((span or {}).get("end") or 0.0) if span else length
    if kind == "take":
        keys = [str(k) for k in cell.get("recKeys") or [] if k]
        if not keys:
            raise VpocError("vnew.generate", "NoRecording", "테이크 녹화가 없습니다.", 409)
        video = _stitch_recordings(keys)
        if span or path == "cut":
            clip = _cut_clip(video, {"id": str(cell.get("id") or ""), "startSec": start, "endSec": end, "seconds": end - start})
            del video
            video = clip["bytes"]
            meta = {"origSeconds": clip.get("origSeconds"), "sentSeconds": clip.get("sentSeconds"), "padded": bool(clip.get("padded"))}
        else:
            meta = {"origSeconds": round(length, 3), "sentSeconds": round(length, 3), "padded": False}
    else:
        key = str(cell.get("recordingKey") or "")
        if not key:
            raise VpocError("vnew.generate", "NoRecording", "컷 녹화가 없습니다.", 409)
        keys = [key]
        raw = _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
        base = float(cell.get("startSec") or 0.0)
        unit = {**cell, "startSec": base + start, "endSec": base + end, "seconds": end - start} if span else cell
        clip = _cut_clip(raw, unit)
        del raw
        video = clip["bytes"]
        meta = {"origSeconds": clip.get("origSeconds"), "sentSeconds": clip.get("sentSeconds"), "padded": bool(clip.get("padded"))}
    if path == "take" and pad_to and float(pad_to) > float(meta.get("sentSeconds") or 0.0) + 0.05:
        video = _pad_clip(video, float(pad_to))
        meta = {**meta, "sentSeconds": round(float(pad_to), 3), "padded": True}
    return video, meta, keys


def _retake_payload(owner: str, pid: str, plan: dict, like: dict, *, path: str, caps: dict, variant: str) -> dict:
    """경로에 맞는 ⑤ payload 빌더(참고 영상 _take_payload — 실사는 variant="real", 영상 편집 _previz_payload(caps))."""
    if path == "take":
        return _take_payload(owner, pid, plan, like, caps, variant="real") if variant == "real" else _take_payload(owner, pid, plan, like, caps)
    return _previz_payload(owner, pid, plan, like, variant, caps=caps)


def retake_template(owner: str, pid: str, gen_id: str, *, variant: str, cell_id: str, model: str, span: dict | None = None,
                    cut_ids: list | None = None) -> dict:
    """형식 본보기(design D23): 고른 모델·범위로 ⑤ 빌더가 만들 프롬프트. 영상 모델에는 보내지 않는다.

    참고 이미지 번호가 실제로 읽힌 이미지 순서를 따르므로 payload 를 그대로 만들고(세마포어 안 — ⑤ 와 같은 메모리 규칙) 이미지 바이트는 버린다.
    반환 {prompt, refCount, path}.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    kcut = _retake_kcut_cell(plan, gen, gen_id, variant, cell_id)  # 작업 131: 컷 단위 Kling 실사 재료 = 그 컷의 녹화 구간
    if kcut:
        _index, cell, kind, original, _mode = kcut
    else:
        _index, cell, mode = _retake_cell(plan, gen, gen_id, variant, cell_id)
        kind = "take" if mode == "previz-take" else "cut"
        original = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    path = retake_path(model, original=original, kind=kind)
    like = _retake_like(cell, kind=kind, path=path, span=span, cut_ids=cut_ids)
    _PREPARE_SEM.acquire()
    try:
        built = _retake_payload(owner, pid, plan, like, path=path, caps=_previz_caps(model), variant=variant)
        prompt = str(built.get("prompt") or "")
        count = len(built.get("elements") or []) + len(built.get("imageUrls") or [])
        del built
    finally:
        _PREPARE_SEM.release()
    return {"prompt": prompt, "refCount": count, "path": path}


def _charge_retake(owner: str, pid: str, gen_id: str, result: dict, model: str, seconds: float, usd: float) -> None:
    """새 길 다시 만들기 제출 1회 계상(견적 금액, 키 = 작업 토큰 — 중복 방지). 실패는 경고만."""
    token = str((result or {}).get("clientRequestToken") or "")
    if not token:
        return
    try:
        vpoc_ledger.charge(token, kind="video", model=str(model or ""), units=float(seconds or 0), usd=float(usd or 0), user=owner,
                           project_id=pid, plan_id=gen_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_retake_failed pid=%s err=%s", pid, type(exc).__name__)


def resubmit_model(owner: str, pid: str, gen_id: str, *, variant: str, cell_id: str, prompt: str, tag: str, model: str,
                   attempt: int = 1, version=None, span: dict | None = None, cut_ids: list | None = None,
                   expect_refs: list | None = None) -> dict:
    """⑦ AI로 고치기 새 길(design D22): 고른 모델·범위로 칸 하나를 제출한다. 칸 상태(gen.units)는 읽기만 한다.

    span = {start, end}(칸 녹화 시간, None 이면 원본 칸 전체), cut_ids = 구간 안 컷, expect_refs = 초안 본보기의 참고 표시(다르면 제출하지 않는다).
    준비~제출은 _PREPARE_SEM 안, 원장은 견적 금액(_charge_retake), 기록 source=retake·model=고른 모델. 결과 접두사는 116 과 같다.
    반환 {invocationArn, outputPrefix, provider, usd, logId, sentSeconds, trimTo}.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    kcut = _retake_kcut_cell(plan, gen, gen_id, variant, cell_id)  # 작업 131: 컷 단위 Kling 실사 재료 = 그 컷의 녹화 구간
    if kcut:
        index, cell, kind, original, mode = kcut
        if str(model or original) == original:  # 원래 모델(시작 그림 경로)은 resubmit_kling_cut 만 낸다
            raise VpocError("vnew.retake", "RetakeBadModel", RETAKE_MODEL_GONE_TEXT, 409)
    else:
        index, cell, mode = _retake_cell(plan, gen, gen_id, variant, cell_id)
        kind = "take" if mode == "previz-take" else "cut"
        original = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    model = str(model or original)
    path = retake_path(model, original=original, kind=kind)
    cell_seconds = float(cell.get("seconds") or 0.0)
    seconds = (float(span.get("end") or 0.0) - float(span.get("start") or 0.0)) if span else cell_seconds
    info = retake_plan(gen, model=model, path=path, seconds=seconds, original=model == original)
    if not info["ok"]:
        raise VpocError("vnew.retake", "RetakeBadModel", RETAKE_MODEL_GONE_TEXT, 409)
    caps = _previz_caps(model)
    like = _retake_like(cell, kind=kind, path=path, span=span, cut_ids=cut_ids)
    state = cell.get(variant) if isinstance(cell.get(variant), dict) else {}
    audio_choice = state.get("audio") if isinstance(state.get("audio"), bool) else None
    run_n = (int((gen.get("realRun") or {}).get("n") or 0) or None) if kind == "take" and variant == "real" else None
    attempt = max(1, int(attempt or 1))
    prefix = _seg_output_prefix(owner, pid, gen_id, f"{str(cell_id).replace('#', '-')}-{variant}-{tag}", attempt)  # 131: S3 키에 # 없이
    started = time.monotonic()
    _PREPARE_SEM.acquire()
    try:
        if variant == "real":
            _assert_no_webtoon(prompt)
        video, meta, video_keys = _retake_video(cell, kind=kind, path=path, span=span,
                                                pad_to=float(info["sentSeconds"] or 0) if path == "take" else 0.0)
        with _RefTrace() as trace:
            built = _retake_payload(owner, pid, plan, like, path=path, caps=caps, variant=variant)
        refs = trace.refs(built)
        if expect_refs is not None:
            from . import retake as vnew_retake  # noqa: PLC0415 — retake 가 generate 를 import 한다(순환 피함).

            if vnew_retake._refs(str(built.get("prompt") or "")) != {str(r) for r in expect_refs}:  # noqa: SLF001
                raise VpocError("vnew.retake", "RetakeFormatChanged", RETAKE_FORMAT_CHANGED_TEXT, 409)
        if path == "take":
            result = _submit_take(endpoint=model, caps=caps, video_mp4=video, prompt=prompt, output_prefix=prefix,
                                  image_urls=built["imageUrls"], seconds=float(info["sentSeconds"]), resolution=info["resolutionValue"],
                                  audio=audio_choice)
            params = _take_params(caps, float(info["sentSeconds"]), info["resolutionValue"], audio_choice)
        else:
            result = _submit_previz_edit(endpoint=model, caps=caps, video_mp4=video, prompt=prompt, output_prefix=prefix,
                                         elements=built["elements"], image_urls=built["imageUrls"])
            params = _cut_params(like, meta, caps)
        del video, built
    finally:
        _PREPARE_SEM.release()
    params.update(cellSeconds=round(cell_seconds, 3), rangeStart=round(float(span["start"]), 3) if span else None,
                  rangeEnd=round(float(span["end"]), 3) if span else None, trimTo=info["trimTo"])
    _charge_retake(owner, pid, gen_id, result, model, float(info["billedSeconds"] or 0), float(info["usd"] or 0))
    log_id = _log_attempt(owner, pid, gen_id, source="retake", mode=mode, variant=variant, cell_id=str(cell_id), index=index,
                          attempt=attempt, run=run_n, endpoint=model, params=params, refs=refs, video_keys=video_keys, result=result,
                          prompt=prompt, version=version)
    log.info("vnew.gen.resubmit_model pid=%s cell=%s variant=%s tag=%s attempt=%d path=%s sent_s=%.3f trim_s=%.3f bytes=%d refs=%d ms=%d "
             "rss_mb=%.0f", pid, cell_id, variant, tag, attempt, path, float(meta.get("sentSeconds") or 0), float(info["trimTo"] or 0),
             len(prompt.encode("utf-8")), len(refs), int((time.monotonic() - started) * 1000), _rss_mb())
    return {"invocationArn": str(result.get("invocationArn") or ""), "outputPrefix": str(result.get("outputPrefix") or prefix),
            "provider": str(result.get("provider") or ""), "usd": info["usd"], "logId": log_id,
            "sentSeconds": meta.get("sentSeconds"), "trimTo": info["trimTo"]}


# ================================================================ 작업 131: 컷 단위 Kling 실사 재료의 ⑦ AI로 고치기
#
# 130 의 컷 단위 Kling 실사 결과는 ⑦ 재료 kind=cut·칸 "테이크id#컷id" 다(edit._material_items). 원래 모델(Kling image-to-video)로 다시 낼 때는
# resubmit_kling_cut(같은 길이·저장된 시작 그림, 없으면 그 컷 결과 첫 프레임), 다른 모델(영상 편집·참고 영상)은 retake_template·resubmit_model 이
# 그 컷의 프리비즈 녹화 구간(_take_cut_units 한 줄)을 컷 칸처럼 쓴다. 칸 상태(gen.units)는 읽기만 한다.

RETAKE_NO_START_TEXT = "이 컷의 시작 그림을 찾지 못해 제출하지 않았습니다."


def _retake_kcut_cell(plan: dict, gen: dict, gen_id: str, variant: str, cell_id: str) -> tuple[int, dict, str, str, str] | None:
    """컷 단위 Kling 실사 재료 칸이면 (테이크 index, 그 컷의 컷 단위 작업 사본, 칸 종류 "cut", 원래 모델, gen 모드). 아니면 None."""
    found = _kling_cut_record(gen, variant, cell_id)
    if not found:
        return None
    if str(gen.get("genId") or "") != gen_id:
        raise VpocError("vnew.retake", "RetakeGenChanged", "영상 생성 기록이 바뀌었습니다. ⑦ 편집을 다시 열어 주세요.", 409)
    index, take, record = found
    unit = next((u for u in _take_cut_units(plan, take) if str(u.get("cutId") or "") == str(record.get("cutId") or "")), None)
    if not unit or not unit.get("recordingKey"):
        raise VpocError("vnew.retake", "RetakeNotTarget", "고칠 칸을 찾을 수 없습니다.", 409)
    return index, dict(unit), "cut", _kling_cut_model_of(take), str(gen.get("mode") or "")


def resubmit_kling_cut(owner: str, pid: str, gen_id: str, *, cell_id: str, prompt: str, tag: str, attempt: int = 1,
                       version=None) -> dict:
    """컷 단위 Kling 실사 재료 하나를 원래 경로(같은 Kling image-to-video·같은 길이)로 새 프롬프트만 바꿔 제출한다.

    시작 그림 = 그 컷의 저장된 시작 그림(stillKey), 없거나 못 읽으면 그 컷 결과의 첫 프레임(_cut_first_frame). 준비~제출은 _PREPARE_SEM 안,
    원장은 130 과 같은 _charge_kling_cut(작업 id 키, 보낸 길이 × 그 모델 초당 단가), 기록 source=retake·칸 "테이크id#컷id".
    결과 접두사 "{테이크}-real-{컷}-{tag}" + attempt. 반환 {invocationArn, outputPrefix, provider, usd, logId, startImage(still|firstFrame)}.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        raise VpocError("vnew.retake", "RetakeGenChanged", "영상 생성 기록이 바뀌었습니다. ⑦ 편집을 다시 열어 주세요.", 409)
    found = _kling_cut_record(gen, "real", cell_id)
    if not found or found[2].get("status") != "completed" or not found[2].get("outputKey"):
        raise VpocError("vnew.retake", "RetakeNotTarget", "고칠 칸을 찾을 수 없습니다.", 409)
    index, take, record = found
    endpoint = _kling_cut_model_of(take)
    caps = _previz_caps(endpoint)
    if not caps.get("startImageField"):
        raise VpocError("vnew.retake", "RetakeBadModel", RETAKE_MODEL_GONE_TEXT, 409)
    take_id, _, cut_id = str(cell_id).partition("#")
    seconds = float(record.get("seconds") or 0)
    try:
        duration = int(record.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    duration = duration if duration > 0 else _kling_duration(caps, seconds)
    run_n = int((gen.get("realRun") or {}).get("n") or 0) or None
    attempt = max(1, int(attempt or 1))
    prefix = _seg_output_prefix(owner, pid, gen_id, f"{take_id}-real-{cut_id}-{tag}", attempt)
    prompt_max = vpoc_settings.fal_prompt_max(endpoint, caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
    still_key = str(record.get("stillKey") or "")
    started = time.monotonic()
    _PREPARE_SEM.acquire()
    try:
        _assert_no_webtoon(prompt)
        start, source = None, ""
        if still_key:
            try:
                start, source = _fetch_bytes(still_key, bucket=vpoc_settings.VIDEO_BUCKET), "still"
            except Exception as exc:  # noqa: BLE001 — 저장된 그림을 못 읽으면 결과 첫 프레임으로
                log.warning("vnew.gen.resubmit_kling_still_failed pid=%s cell=%s err=%s", pid, cell_id, type(exc).__name__)
                start = None
        if not start:
            start = _cut_first_frame(_fetch_bytes(str(record.get("outputKey") or ""), bucket=vpoc_settings.VIDEO_BUCKET), 0.0)
            source = "firstFrame"
        if not start:
            raise VpocError("vnew.retake", "RetakeNoStartImage", RETAKE_NO_START_TEXT, 409)
        body = vpoc_fal.build_payload({**caps, "durations": [duration]}, prompt=prompt,
                                      keyframe_jpeg_b64=base64.b64encode(start).decode("ascii"), duration_s=duration,
                                      prompt_max=prompt_max)
        del start
        sent = str(body.get(caps.get("promptField") or "prompt") or "")
        result = _previz_request("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}", body)
        del body
    finally:
        _PREPARE_SEM.release()
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.generate", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)
    _charge_kling_cut(owner, pid, gen_id, "fal-kcut-" + request_id, endpoint, duration)
    arn = f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}"
    usd = round(_previz_model_unit_price(endpoint) * float(duration), 4)
    log_id = _log_attempt(owner, pid, gen_id, source="retake", mode=str(gen.get("mode") or ""), variant="real", cell_id=str(cell_id),
                          index=index, attempt=attempt, run=run_n, endpoint=endpoint,
                          params={"seconds": round(seconds, 3), "duration": duration, "startImage": source},
                          refs=[{"slot": "startImage", "key": still_key if source == "still" else ""}],
                          video_keys=[] if source == "still" else [str(record.get("outputKey") or "")],
                          result={"invocationArn": arn, "outputPrefix": prefix}, prompt=sent, version=version)
    log.info("vnew.gen.resubmit_kling_cut pid=%s cell=%s tag=%s attempt=%d duration=%d start=%s bytes=%d ms=%d", pid, cell_id, tag,
             attempt, duration, source, len(sent.encode("utf-8")), int((time.monotonic() - started) * 1000))
    return {"invocationArn": arn, "outputPrefix": prefix, "provider": "fal", "usd": usd, "logId": log_id, "startImage": source}
