"""vnew ⑦ 편집 — 영상 편집 타임라인(지시서 110, 2026-11-01).

⑤에서 만든 영상(컷·테이크 결과)을 클립으로 늘어놓고 자르기·나누기·순서·소리 끄기를 고친 편집 문서(EDL)를 gen·그림체 마다
하나 저장하고, 문서대로 서버 ffmpeg 로 잘라 이어 붙여 mp4 하나로 내보낸다. 유료 생성 API 는 부르지 않는다.

재료(허용 목록): 지금 gen 의 그 그림체(variant) 결과 영상만 클립 원본(sourceKey)이 될 수 있다(_material_items).
  - 컷 단위(previz): units[i].<variant>.outputKey(completed). 3초로 채워 만든 컷(padded)은 기본 끝 = origSeconds.
  - 통 테이크(previz-take): units[i].<variant>.outputKey. 기본 문서는 녹화 컷 경계로 나눈다(경계를 믿을 수 없으면 테이크
    통째). 실사 회차 기록(realHistory)의 결과도 허용한다(새 회차가 시작돼도 저장된 문서가 깨지지 않게).
  - 옛 순차 모드: segments[i].<variant>.outputKey.
  - 합친 영상(finalReal·finalWebtoon done): 위 결과가 없을 때 기본 문서에 통째 1개.
저장(메인 버킷, gen 루트 = generate._gen_root):
  edit-<variant>.json(그림체별 편집 문서, 조건부 쓰기 → 409, 작업 189 — 옛 edit.json 은 읽기만, _saved_docs) ·
  edit-export.json(내보내기 상태, 두 그림체가 함께 쓴다) · edit-media/index.json(길이·소리·크기·fps 캐시) ·
  edit-media/{mid}-strip.jpg · edit-media/{mid}-film.json(필름스트립·파형 피크, 처음 요청 때 한 번 만든다).
  내보낸 결과는 영상 버킷 {gen 루트}/final-edit-{n}.mp4(소리 있음 — 화면 재생 · ⑥ 원본). 작업 216-fix1b: 내려받기는 같은 번호의
  final-edit-{n}.zip(소리 없는 mp4 · 섞은 소리 mp3 · README.txt, edit_audio.export_package — 영상은 한 번만 만들어 나눈다).
외부 호출 래퍼(검사가 가짜로 바꾼다): _download · _upload · _probe_file · _film_file · _export_segment · _export_join ·
_spawn_export(작업 181: · _export_dissolve · _export_audio · _export_mix, 작업 216-fix1b: · _export_package). 파드 안 내보내기 잠금 파일: _pod_lock_try · _pod_lock_release(지시서 111). STORE 는 vpoc.store.STORE 다
(검사가 모듈 변수로 바꾼다).
지시서 115: 클립 속도 speed(SPEEDS, 없으면 1 — 1 은 저장하지 않는다). 타임라인 길이 = (out − in) / speed(_clip_seconds).
재료가 없을 때 요약에 잠김 이유(summary → reason, gen 이 없어도 gen_view 가 부른다).
지시서 116: VNEW_EDIT_RETAKE 가 켜져 있으면 ⑦ AI로 고치기(retake.py)로 다시 만든 버전(v2…)도 재료다 — 그 칸 지금 결과 바로 뒤
(_all_items, group "retake", 이름 끝 " v{n}"). 꺼져 있으면 _retake_state 가 None 이라 재료·보기가 지시서 115 와 같다.
지시서 116 보완 10: 소리 없는 버전은 그 칸 v1 의 같은 녹화 시각 소리를 쓴다(_audio_sources — 재료 audioFrom·내보내기 audio_source).
작업 193 fix1: AI로 고치기 result 버전은 늘 fromKey 의 range.in + 클립 시각 소리(이어진 버전은 원천까지), 원천에 소리가 없으면 무음.
옛 길 버전은 필름 캐시 파형이 −60 dBFS 아래면 소리 없음으로 본다(_mark_silent).
작업 181: 클립 audioLead(J 컷)·audioTail(L 컷)·transition(다음 클립으로 디졸브). 셋 다 없는 문서는 181 전과 같은 해시·같은 내보내기 명령이다.
쓰는 문서의 내보내기는 _fx_plan(프레임·샘플 배치) → 몸통 조각 + 디졸브 경계 조각 잇기, 소리는 클립별 창을 타임라인에서 섞는다.
작업 185: 클립 audioSlip(대사 타이밍 — 소리 창을 원본에서 옮겨 읽는다, 넷 다 없으면 181 전 경로)과 빈틈 다듬기(sound_ranges — 재료 보기의
soundRanges, tighten_proposal·tighten_view — POST /edit/tighten 제안, 저장하지 않는다).
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
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone

from boto3.s3.transfer import TransferConfig

try:  # 파드 안 내보내기 잠금(리눅스·CI). 윈도 개발 PC 는 msvcrt.
    import fcntl as _fcntl
except ImportError:
    _fcntl = None
try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

from .. import stitcher
from ..vpoc import settings as vpoc_settings
from ..vpoc import store as vpoc_store
from ..vpoc.errors import VpocError, error_gist, now_iso
from ..vpoc.store import STORE

from . import edit_audio
from . import edit_tools  # 작업 215c
from . import generate
from . import plan as plan_mod
from . import settings as vnew_settings
from . import edit_text  # 작업 215b: 자막 · 타이틀 · 블랙/화이트 전환 · 시작/끝 페이드 · 색 보정(edit_text.py)

log = logging.getLogger("vpoc")

# ================================================================ 상수

VARIANTS = ("real", "webtoon")
VARIANT_LABELS = {"real": "실사", "webtoon": "웹툰"}
# 문서 한도. 클립 수·전체 길이는 내보내기 시간(dev 파드 CPU 0.5)과 문서 크기를 묶는다.
MAX_CLIPS = 200
MAX_TOTAL_S = 300.0
MIN_CLIP_S = 0.04  # 25fps 한 프레임
MAX_LABEL_LEN = 40
# 클립 속도(지시서 115). 0.5× 는 2배로 늘고 2× 는 반으로 준다. 내보내기는 ffmpeg setpts·atempo 한 번(0.5~2)이다.
SPEEDS = stitcher.EDIT_SPEED_CHOICES
# 작업 181: J 컷(audioLead — 소리가 화면보다 먼저)·L 컷(audioTail — 소리가 화면 뒤로 이어 감)과 다음 클립으로 가는 디졸브(transition).
# 앞당김·이어 감은 타임라인 초(0~2, 0.05 단위, 원본 구간 = 초 × 속도), 디졸브는 4값이고 두 클립 중 짧은 쪽(속도 반영)의 45% 까지다.
# 화면에 없는 클립의 소리(앞당김·이어 감 구간)는 OFFSCREEN_GAIN 까지 직선으로 들어오고 나간다(화면에 있는 클립은 1.0).
AUDIO_SHIFT_MAX_S = 2.0
AUDIO_SHIFT_STEP_S = 0.05
DISSOLVE_SECONDS = (0.25, 0.5, 0.75, 1.0)
DISSOLVE_MAX_SHARE = 0.45
OFFSCREEN_GAIN = 0.6
# 작업 185: 대사 타이밍(audioSlip) — 클립 소리만 화면 안에서 앞뒤로 민다(타임라인 초, −1~1, 0.05 단위, 양수 = 소리가 늦게).
AUDIO_SLIP_MAX_S = 1.0
# 작업 185: 소리 구간(빈틈 다듬기). 파형 피크(1초 PEAKS_PER_SECOND 개, 0~1)에서 또렷한 소리(주로 대사) 구간을 찾는다.
# 한 칸이 소리 = 피크 ≥ max(SOUND_MIN_PEAK, 잡음 바닥 × SOUND_FLOOR_RATIO, 가장 큰 피크 × SOUND_MAX_RATIO). 잡음 바닥은 피크의 아래
# SOUND_FLOOR_PERCENTILE 값이다. 검사(185 gs-a) 합성 소리로 맞췄다: 0.04 백색 잡음(피크 ≈ 0.04)은 SOUND_MIN_PEAK 아래, 늘 깔린 0.1 잡음은
# 바닥 × 3 아래, 0.5 소리 끝의 AAC 번짐(피크의 수 %)은 최대 × 0.12 아래라 구간이 소리 시작·끝 칸에서 끊긴다.
SOUND_FLOOR_PERCENTILE = 0.20
SOUND_MIN_PEAK = 0.06
SOUND_FLOOR_RATIO = 3.0
SOUND_MAX_RATIO = 0.12
SOUND_MERGE_GAP_S = 0.35  # 이보다 짧은 쉼(말 사이 숨)은 한 구간으로 잇는다
SOUND_MIN_RANGE_S = 0.15  # 이은 뒤 이보다 짧은 구간(딸깍·숨소리)은 버린다
SOUND_ROUND_S = 0.05  # 구간 경계 반올림(피크 한 칸)
# 작업 185: 빈틈 다듬기 제안(tighten_proposal) 기본값. 남길 여유(소리 앞·뒤)와 최소 클립 길이는 타임라인 초다. 요청 값은 0~1초로 묶는다.
TIGHTEN_KEEP_HEAD_S = 0.25
TIGHTEN_KEEP_TAIL_S = 0.35
TIGHTEN_MIN_CLIP_S = 1.0
TIGHTEN_KEEP_MAX_S = 1.0
# 재료가 없을 때 gen_view 요약의 잠김 이유(지시서 115, 화면 ⑦ 칩의 title·안내). 키는 _lock_reason 이 고른다.
LOCK_REASONS = {
    "none": "⑤에서 영상을 먼저 만들면 편집할 수 있습니다.",
    "running": "⑤에서 영상을 만드는 중입니다. 컷이 하나라도 완성되면 편집할 수 있습니다.",
    "cancelled": "영상 만들기가 취소됐습니다. ⑤에서 다시 만들면 편집할 수 있습니다.",
    "failed": "영상을 만들지 못했습니다. ⑤에서 다시 만들면 편집할 수 있습니다.",
    "other": "편집할 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들면 편집할 수 있습니다.",
}
# 저장·내보내기 요청 바디 상한(앱 전체 한도는 영상 업로드용 100MB). 클립 200개 문서는 약 70KB 다.
MAX_BODY_BYTES = 256 * 1024
_TIME_EPS = 0.001  # 원본 길이와 비교할 때 허용하는 반올림 오차(초)
# 한 번의 GET 에서 길이를 재는(내려받아 ffmpeg -i) 시간 예산. 넘으면 남은 재료는 다음 GET 으로 미룬다(probing true).
PROBE_BUDGET_S = 20.0
# 내보내기. 한 번 시도의 시간 상한, 하트비트 끊김 판단, 시도 횟수(처음 + 이어받기 2번), 지난 결과 보관 수.
EXPORT_MAX_S = 1200
EXPORT_STALL_S = 300
EXPORT_MAX_ATTEMPTS = 3
EXPORT_HISTORY_MAX = 3
EXPORT_HEARTBEAT_S = 5.0  # 클립 사이 하트비트 쓰기 최소 간격
EXPORT_WAIT_BEAT_S = 30.0  # 순서를 기다리는 동안 하트비트 간격
EXPORT_FPS_DEFAULT = 24
EXPORT_MAX_WIDTH = 1920
EXPORT_MAX_HEIGHT = 1080
# 필름스트립·파형.
STRIP_TILE_HEIGHT = 72
STRIP_MAX_TILES = 120
PEAKS_PER_SECOND = 20
# 작업 193 fix1: 옛 길 다시 만든 버전의 소리 트랙이 이 피크(−60 dBFS = 0.001) 아래뿐이면 소리 없음으로 본다(_mark_silent — 필름 캐시 파형).
# 피크는 소수 3자리로 반올림돼 있어 실제로는 약 −66 dBFS 아래가 걸린다.
SILENT_PEAK = 0.001
URL_EXPIRES_S = 3600  # presigned URL 수명(초). 기존 ⑤·⑥ 영상과 같다.

_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_MID_RE = re.compile(r"^m[0-9a-f]{16}$")
_GEN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LIVE = ("queued", "running")

# S3 ↔ 파일 스트리밍(한 스레드, 8MB 조각). 영상 바이트를 통째로 메모리에 올리지 않는다.
_TRANSFER = TransferConfig(
    use_threads=False, max_concurrency=1, multipart_chunksize=8 * 1024 * 1024, io_chunksize=256 * 1024,
)

# 프로세스당 동시에 하나씩만: 재료 내려받기·ffmpeg(길이 재기·필름스트립), 내보내기.
_MEDIA_SEM = threading.BoundedSemaphore(1)
_EXPORT_SEM = threading.BoundedSemaphore(1)
# 파드당 내보내기 하나(지시서 111): gunicorn worker(프로세스) 2개가 각자 _EXPORT_SEM 을 가지므로, 같은 컨테이너의 임시
# 디렉터리에 있는 잠금 파일을 함께 잡는다(리눅스 flock, 윈도 msvcrt — 프로세스가 죽으면 OS 가 푼다). 컨테이너마다 /tmp 가
# 따로라 다른 파드와는 나누지 않는다. 잠금을 기다리는 동안 상태는 queued 그대로, EXPORT_WAIT_BEAT_S 마다 하트비트.
EXPORT_LOCK_NAME = "vnew-edit-export.lock"
EXPORT_LOCK_POLL_S = 2.0  # 잠금이 풀렸는지 다시 보는 간격
EXPORT_LOCK_WAIT_MAX_S = 3600  # 이보다 오래 기다리면 failed(앞 내보내기 한 번 시도 상한 1200초 + ffmpeg 600초보다 넉넉히)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_export_threads: dict[str, threading.Thread] = {}
_export_threads_guard = threading.Lock()


class _ClaimLost(Exception):
    """다른 실행이 내보내기를 이어받았다(이 실행은 결과를 쓰지 않고 끝낸다)."""


class _ExportTimeout(Exception):
    """한 번 시도의 시간 상한(EXPORT_MAX_S)을 넘었다."""


class _PodBusy(Exception):
    """파드 안 다른 내보내기가 EXPORT_LOCK_WAIT_MAX_S 넘게 끝나지 않았다."""


# ================================================================ 키 규칙


def _root(owner: str, pid: str, gen_id: str) -> str:
    return generate._gen_root(owner, pid, gen_id)  # noqa: SLF001


def doc_key(owner: str, pid: str, gen_id: str, variant: str) -> str:
    """작업 189: 그림체마다 따로 둔 편집 문서 edit-<variant>.json(실사·웹툰 편집이 서로 덮어쓰지 않는다)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    return f"{_root(owner, pid, gen_id)}/edit-{variant}.json"


def legacy_doc_key(owner: str, pid: str, gen_id: str) -> str:
    """작업 189 전 gen 하나에 하나뿐이던 편집 문서 edit.json. 읽기만 한다(고치거나 지우지 않는다 — _saved_docs 참고)."""
    return f"{_root(owner, pid, gen_id)}/edit.json"


def export_state_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_root(owner, pid, gen_id)}/edit-export.json"


def media_index_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_root(owner, pid, gen_id)}/edit-media/index.json"


def strip_key(owner: str, pid: str, gen_id: str, mid: str) -> str:
    return f"{_root(owner, pid, gen_id)}/edit-media/{mid}-strip.jpg"


def film_key(owner: str, pid: str, gen_id: str, mid: str) -> str:
    return f"{_root(owner, pid, gen_id)}/edit-media/{mid}-film.json"


def export_key(owner: str, pid: str, gen_id: str, n: int) -> str:
    return f"{_root(owner, pid, gen_id)}/final-edit-{int(n)}.mp4"


def export_zip_key(owner: str, pid: str, gen_id: str, n: int) -> str:
    """작업 216-fix1b: 내려받기 zip(소리 없는 mp4 · 소리 mp3 · README.txt)."""
    return f"{_root(owner, pid, gen_id)}/final-edit-{int(n)}.zip"


def export_stems_key(owner: str, pid: str, gen_id: str, n: int) -> str:
    """작업 215a: 내보내기 n 의 스템 zip(대사만 · 배경음 · 효과음 · mix.wav). 영상 버킷."""
    return f"{_root(owner, pid, gen_id)}/final-edit-{int(n)}-stems.zip"


def audio_root(owner: str, pid: str) -> str:
    """작업 215a: 내 소리(사용자가 올린 배경음 · 효과음) 폴더. gen 이 바뀌어도 남게 프로젝트 편집 폴더에 둔다."""
    return f"users/{owner}/video/{pid}/vnew/edit-audio"


def audio_index_key(owner: str, pid: str) -> str:
    return f"{audio_root(owner, pid)}/index.json"


def audio_file_key(owner: str, pid: str, asset_id: str, ext: str) -> str:
    return f"{audio_root(owner, pid)}/{asset_id}.{ext}"


def audio_peaks_key(owner: str, pid: str, asset_id: str) -> str:
    return f"{audio_root(owner, pid)}/{asset_id}-peaks.json"


def media_id(source_key: str) -> str:
    """재료 id(경로·로그에 S3 키 대신 쓴다). sha256 앞 16자."""
    return "m" + hashlib.sha256(str(source_key).encode("utf-8")).hexdigest()[:16]


# ================================================================ 외부 호출 래퍼(검사가 가짜로 바꾼다)


def _download(key: str, path: str, *, bucket: str) -> int:
    """S3 → 파일. 실제 저장소면 boto3 스트리밍(한 스레드), 검사용 저장소면 get_bytes. 반환: 바이트 수."""
    store = STORE
    if isinstance(store, vpoc_store.Store):
        target = bucket or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        with open(path, "wb") as handle:
            vpoc_store.s3_client(region).download_fileobj(target, key, handle, Config=_TRANSFER)
    else:
        data = store.get_bytes(key, bucket=bucket)
        with open(path, "wb") as handle:
            handle.write(data)
        del data
    return os.path.getsize(path)


def _upload(path: str, key: str, *, bucket: str, content_type: str) -> None:
    """파일 → S3(SSE AES256). 실제 저장소면 boto3 스트리밍(8MB 조각 멀티파트), 검사용 저장소면 put_bytes."""
    store = STORE
    if isinstance(store, vpoc_store.Store):
        target = bucket or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        with open(path, "rb") as handle:
            vpoc_store.s3_client(region).upload_fileobj(
                handle, target, key,
                ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
                Config=_TRANSFER,
            )
    else:
        with open(path, "rb") as handle:
            store.put_bytes(key, handle.read(), content_type, bucket=bucket)


def _probe_file(path: str) -> dict:
    """길이·크기·fps·소리 유무(stitcher.probe_media)."""
    return stitcher.probe_media(path)


def _film_file(path: str, workdir: str, *, interval: float, tiles: int, has_audio: bool) -> dict:
    """필름스트립 JPEG(workdir 안)과 파형 피크. 반환 {stripPath, tileWidth, tileHeight, peaks}."""
    strip = os.path.join(workdir, "strip.jpg")
    stitcher.filmstrip_jpeg(path, strip, interval=interval, tiles=tiles, tile_height=STRIP_TILE_HEIGHT, threads=1)
    from PIL import Image  # noqa: PLC0415

    with Image.open(strip) as image:
        width, height = image.size
    peaks: list[float] = []
    if has_audio:
        try:
            peaks = stitcher.audio_peaks(path, workdir, per_second=PEAKS_PER_SECOND, threads=1)
        except stitcher.StitchError as exc:
            log.warning("vnew.edit.peaks_failed err=%s", error_gist(exc, 120))
    return {"stripPath": strip, "tileWidth": max(1, width // max(1, tiles)), "tileHeight": height, "peaks": peaks}


def _export_segment(source_path: str, video_out: str, **kwargs) -> dict:
    """내보내기 클립 한 개(stitcher.edit_segment, -threads 1)."""
    return stitcher.edit_segment(source_path, video_out, threads=1, **kwargs)


def _export_join(segments: list[str], output_path: str, *, audio_path: str) -> str:
    """조각 잇기(stitcher.edit_join)."""
    return stitcher.edit_join(segments, output_path, audio_path=audio_path)


def _export_package(video_path: str, audio_raw: str, zip_path: str, **kwargs) -> dict:
    """작업 216-fix1b: 이은 mp4 · 잇기에 넣은 소리 → 내려받기 zip(edit_audio.export_package)."""
    return edit_audio.export_package(video_path, audio_raw, zip_path, **kwargs)


def _export_dissolve(a_path: str, b_path: str, video_out: str, **kwargs) -> dict:
    """작업 181: 디졸브 경계 조각(stitcher.edit_dissolve, -threads 1)."""
    return stitcher.edit_dissolve(a_path, b_path, video_out, threads=1, **kwargs)


def _export_audio(source_path: str, audio_out: str, **kwargs) -> dict:
    """작업 181: 클립 소리 창 하나(stitcher.edit_audio, -threads 1)."""
    return stitcher.edit_audio(source_path, audio_out, threads=1, **kwargs)


def _export_mix(layers: list[dict], output_path: str, *, samples: int) -> dict:
    """작업 181: 타임라인 소리 섞기(stitcher.edit_mix)."""
    return stitcher.edit_mix(layers, output_path, samples=samples)


def _export_tracks(plan: dict, a1_raw: str, asset_paths: dict, workdir: str) -> dict:
    """작업 215a: A1 + 오디오 트랙 섞기 · 스템(edit_audio.render_mix, ffmpeg 한 번)."""
    return edit_audio.render_mix(plan, a1_raw, asset_paths, workdir)


def _probe_audio(path: str) -> dict:
    """작업 215a: 내 소리 파일 길이 · 소리 유무(stitcher.probe_media)."""
    return stitcher.probe_media(path)


def _audio_peaks_file(path: str, workdir: str) -> list[float]:
    """작업 215a: 내 소리 파형 피크(재료 파형과 같은 stitcher.audio_peaks)."""
    return stitcher.audio_peaks(path, workdir, per_second=PEAKS_PER_SECOND, threads=1)


def _delete_object(key: str, *, bucket: str) -> None:
    """작업 215a: 내 소리 지우기. 실제 저장소면 delete_object, 검사용 저장소면 delete_key(있을 때)."""
    store = STORE
    if isinstance(store, vpoc_store.Store):
        target = bucket or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        vpoc_store.s3_client(region).delete_object(Bucket=target, Key=key)
        return
    remover = getattr(store, "delete_key", None)
    if callable(remover):
        remover(key, bucket=bucket)


def _spawn_export(owner: str, pid: str, gen_id: str, n: int, claim: str) -> None:
    """내보내기 스레드를 띄운다. claim 마다 하나(이 프로세스에서 돌고 있는지 _alive_here 가 본다)."""
    thread = threading.Thread(
        target=run_export, args=(owner, pid, gen_id, int(n), claim), daemon=True, name=f"vnew-edit-export-{pid}-{int(n)}",
    )
    with _export_threads_guard:
        for done in [k for k, t in _export_threads.items() if not t.is_alive()]:
            _export_threads.pop(done, None)
        _export_threads[claim] = thread
    thread.start()


def _alive_here(claim: str) -> bool:
    with _export_threads_guard:
        thread = _export_threads.get(str(claim or ""))
        return bool(thread is not None and thread.is_alive())


def _pod_lock_path() -> str:
    return os.path.join(tempfile.gettempdir(), EXPORT_LOCK_NAME)


def _pod_lock_try() -> tuple[bool, object]:
    """파드 안 내보내기 잠금을 기다리지 않고 잡아 본다. 반환 (잡았는가, 손잡이).

    잡으면 (True, 열린 파일) — 끝나면 _pod_lock_release. 다른 프로세스(또는 다른 손잡이)가 잡고 있으면 (False, None).
    잠금 파일을 열 수 없으면 (True, None)이고 파일 잠금 기능이 없는 OS 면 잠그지 않은 (True, 파일)이다 — 그때는 프로세스
    세마포어만으로 진행한다(잠금 때문에 내보내기가 멈추지 않게).
    """
    try:
        handle = open(_pod_lock_path(), "a+b")  # noqa: SIM115 — 잡은 동안 열어 둔다(닫으면 풀린다)
    except OSError as exc:
        log.warning("vnew.edit.export_lock_unavailable err=%s", type(exc).__name__)
        return True, None
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        elif _msvcrt is not None:
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False, None
    return True, handle


def _pod_lock_release(handle) -> None:
    if handle is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        elif _msvcrt is not None:
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
    except OSError as exc:
        log.warning("vnew.edit.export_lock_release_failed err=%s", type(exc).__name__)
    finally:
        handle.close()


# ================================================================ 작은 도구


def require_enabled(where: str = "vnew.edit") -> None:
    """VNEW_EDIT 이 꺼져 있으면 404 EditDisabled(라우트가 없는 것과 같다). 프로젝트를 보기 전에 부른다."""
    if not vnew_settings.VNEW_EDIT:
        raise VpocError(where, "EditDisabled", "영상 편집 기능이 꺼져 있습니다.", 404)


def _num(value) -> float | None:
    """JSON 숫자(int·float, bool 아님)이고 유한하면 float, 아니면 None. 글자 숫자는 받지 않는다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _lock(name: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _locks[name] = lock
        return lock


def _presign(key: str, *, bucket: str = "", content_type: str = "") -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, bucket=bucket, content_type=content_type, expires=URL_EXPIRES_S)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다.
        log.warning("vnew.edit.presign_failed err=%s", type(exc).__name__)
        return ""


def _presign_video(key: str) -> str:
    return _presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")


def _download_name(n) -> str:
    """내려받기 파일 이름 edit-<n>.mp4(n 은 서버가 매긴 정수). 꼴이 틀리면 edit.mp4."""
    number = n if isinstance(n, int) and not isinstance(n, bool) else 0
    return f"edit-{number}.mp4" if number > 0 else "edit.mp4"


def _package_name(n) -> str:
    """작업 216-fix1b: 내려받기 zip 과 그 안 파일 이름 앞부분 edit-<n>(n 은 서버가 매긴 정수). 꼴이 틀리면 edit."""
    return _download_name(n)[: -len(".mp4")]


def _export_download(entry: dict) -> tuple[str, str]:
    """작업 216-fix1b: 끝난 내보내기 하나의 (내려받기 URL, 파일 이름). zip 이 있으면 zip(edit-<n>.zip), 옛 내보내기(zipKey 없음)는
    예전 그대로 mp4(edit-<n>.mp4)."""
    zip_key = str(entry.get("zipKey") or "")
    if zip_key:
        name = _package_name(entry.get("n")) + ".zip"
        return _presign_attachment(zip_key, name, "application/zip"), name
    return _presign_download(str(entry.get("key") or ""), entry.get("n")), _download_name(entry.get("n"))


def _presign_download(key: str, n) -> str:
    """결과 영상 내려받기 URL(지시서 111): 영상 버킷 presigned 1시간 + 응답 헤더 Content-Disposition attachment.

    재생 URL(_presign_video)은 그대로 둔다. vpoc store.presign 은 ResponseContentType 하나만 받으므로(공용 파일이라 고치지
    않는다) 실제 저장소면 같은 버킷·리전 규칙으로 직접 서명한다. 검사용 저장소(Store 가 아님)는 재생 URL 과 같다.
    파일 이름은 서버 정수 n 으로만 만든다(사용자 글이 헤더에 들어가지 않는다). 실패하면 "" (보기는 실패하지 않는다).
    """
    if not key:
        return ""
    store = STORE
    if not isinstance(store, vpoc_store.Store):
        return _presign_video(key)
    try:
        target = vpoc_settings.VIDEO_BUCKET or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        return vpoc_store.s3_client(region).generate_presigned_url(
            "get_object",
            Params={
                "Bucket": target,
                "Key": key,
                "ResponseContentType": "video/mp4",
                "ResponseContentDisposition": f'attachment; filename="{_download_name(n)}"',
            },
            ExpiresIn=URL_EXPIRES_S,
        )
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다.
        log.warning("vnew.edit.presign_failed err=%s", type(exc).__name__)
        return ""


def _speed_of(clip) -> float:
    """클립 속도(지시서 115). 없거나 허용 목록(SPEEDS) 밖이면 1."""
    value = _num(clip.get("speed")) if isinstance(clip, dict) else None
    return value if value in SPEEDS else 1.0


def _clip_seconds(clip: dict) -> float:
    """클립이 타임라인에서 차지하는 길이(초) = 원본 구간(out − in) ÷ 속도."""
    return (float(clip.get("out") or 0) - float(clip.get("in") or 0)) / _speed_of(clip)


def _shift_of(clip, key: str) -> float:
    """작업 181: 클립의 소리 앞당김(audioLead)·이어 감(audioTail) 초. 없거나 범위(0~2) 밖이면 0."""
    value = _num(clip.get(key)) if isinstance(clip, dict) else None
    return value if value is not None and 0 < value <= AUDIO_SHIFT_MAX_S + 1e-9 else 0.0


def _slip_of(clip) -> float:
    """작업 185: 클립의 대사 타이밍(audioSlip) 초. 없거나 범위(−1~1) 밖이면 0."""
    value = _num(clip.get("audioSlip")) if isinstance(clip, dict) else None
    return value if value is not None and abs(value) <= AUDIO_SLIP_MAX_S + 1e-9 else 0.0


def _dissolve_of(clip) -> float:
    """작업 181: 다음 클립으로 가는 디졸브 초. 컷(없음·{"type": "cut"})이거나 허용 값(DISSOLVE_SECONDS) 밖이면 0."""
    raw = clip.get("transition") if isinstance(clip, dict) else None
    if not isinstance(raw, dict) or raw.get("type") != "dissolve":
        return 0.0
    value = _num(raw.get("seconds"))
    return value if value in DISSOLVE_SECONDS else 0.0


def _total(clips: list[dict]) -> float:
    """타임라인 길이 합. 작업 181: 디졸브마다 두 화면이 겹쳐 그 초만큼 짧아진다(마지막 클립의 전환은 세지 않는다)."""
    clips = [c for c in clips if isinstance(c, dict)]
    return round(sum(_clip_seconds(c) for c in clips) - sum(_dissolve_of(c) for c in clips[:-1])
                 + edit_tools.gap_total(clips), 3)  # 작업 215c


def _changed(where: str) -> VpocError:
    return VpocError(where, "EditChanged", "다른 곳에서 편집 문서가 먼저 저장됐습니다. 다시 불러온 뒤 고쳐 주세요.", 409)


def _read_json(key: str) -> tuple[dict | None, str]:
    data, etag = STORE.read_json(key)
    return (data if isinstance(data, dict) else None), str(etag or "")


def _mutate_json(key: str, fn, *, attempts: int = 5) -> dict | None:
    """읽고-고치고-조건부쓰기. fn(사본) 이 None 을 주면 쓰지 않는다. 경합(PreconditionFailed)이면 다시 읽는다."""
    last: BaseException | None = None
    for _ in range(attempts):
        data, etag = _read_json(key)
        result = fn(copy.deepcopy(data) if data is not None else None)
        if result is None:
            return data
        try:
            STORE.write_json(key, result, etag=etag, create_only=not etag)
            return result
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise
            last = exc
    raise last if last else RuntimeError("edit mutate failed")


def _load_gen(owner: str, pid: str) -> tuple[dict, dict, str]:
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") if isinstance(plan.get("gen"), dict) else {}
    gen_id = str(gen.get("genId") or "")
    if not gen_id:
        raise VpocError("vnew.edit", "NoGen", "영상 생성 기록이 없습니다. ⑤에서 영상을 먼저 만들어 주세요.", 404)
    if not _GEN_ID_RE.match(gen_id):
        raise VpocError("vnew.edit", "BadGen", "영상 생성 기록을 읽을 수 없습니다.", 409)
    return plan, gen, gen_id


# ================================================================ 재료(허용 목록)


def _cells(gen: dict) -> tuple[str, list]:
    """(모드, 칸 목록). generate._previz_cells 와 같은 규칙(units 가 있으면 units, 없으면 segments)."""
    mode = str(gen.get("mode") or "")
    units = gen.get("units")
    if mode in ("previz", "previz-take") and isinstance(units, list) and units:
        return mode, units
    return mode, gen.get("segments") if isinstance(gen.get("segments"), list) else []


def _material_items(gen: dict, variant: str) -> list[dict]:
    """그 그림체의 재료(허용 목록). plan 없이 gen 만 본다(gen_view 요약·검증 공통). 같은 키는 한 번만.

    각 항목: {sourceKey, mid, kind(cut|take|segment|final), group(current|final|r{n}|r{n}-final), index, cellId, cutIds,
    trimTo(채운 컷의 원래 길이 또는 None), run(실사 회차 n 또는 None)}.
    """
    gen = gen if isinstance(gen, dict) else {}
    if variant not in VARIANTS:
        return []
    mode, cells = _cells(gen)
    has_units = isinstance(gen.get("units"), list) and bool(gen.get("units"))
    kind = {"previz-take": "take", "previz": "cut"}.get(mode, "segment") if has_units else "segment"
    units_by_id = {str(u.get("id") or ""): u for u in (gen.get("units") or []) if isinstance(u, dict)} if has_units else {}
    items: list[dict] = []
    seen: set[str] = set()

    def add(key, **fields) -> None:
        key = str(key or "")
        if not key or key in seen:
            return
        seen.add(key)
        items.append({"sourceKey": key, "mid": media_id(key), **fields})

    def cut_ids(cell: dict) -> list[str]:
        if cell.get("cutIds"):
            return [str(c) for c in cell.get("cutIds") or []]
        return [str(cell.get("cutId"))] if cell.get("cutId") else []

    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        state = cell.get(variant) if isinstance(cell.get(variant), dict) else {}
        if state.get("status") != "completed":
            continue
        cut_clips = [c for c in ((state.get("fallback") or {}).get("cuts") or [])
                     if isinstance(c, dict) and c.get("status") == "completed" and c.get("outputKey")] \
            if kind == "take" and variant == "real" and isinstance(state.get("fallback"), dict) else []
        if cut_clips:
            # 작업 130 보완 1: Seedance 거절로 컷 단위 Kling 으로 만든 실사 테이크는 컷 결과를 컷 재료(kind=cut)로 둔다. Kling 길이
            # (5·10초)가 컷보다 길면 trimTo = 컷 길이. cellId 는 "테이크id#컷id"(테이크 결과·AI로 고치기 칸과 섞이지 않는다).
            for clip in cut_clips:
                seconds, duration = _num(clip.get("seconds")), _num(clip.get("duration"))
                trim_cut = seconds if seconds and duration and duration > seconds + 0.05 else None
                add(clip.get("outputKey"), kind="cut", group="current", index=index,
                    cellId=f"{cell.get('id') or ''}#{clip.get('cutId') or ''}", cutIds=[str(clip.get("cutId") or "")],
                    trimTo=trim_cut, run=None)
            continue
        trim_to = None
        if kind == "cut" and state.get("padded"):
            orig = _num(state.get("origSeconds"))
            trim_to = orig if orig and orig > 0 else None
        add(state.get("outputKey"), kind=kind, group="current", index=index, cellId=str(cell.get("id") or ""),
            cutIds=cut_ids(cell), trimTo=trim_to, run=None)
    final = gen.get("finalReal" if variant == "real" else "finalWebtoon")
    final = final if isinstance(final, dict) else {}
    if final.get("status") == "done":
        add(final.get("key"), kind="final", group="final", index=0, cellId="", cutIds=[], trimTo=None, run=None)
    if variant == "real" and mode == "previz-take":
        history = [h for h in (gen.get("realHistory") or []) if isinstance(h, dict)]
        for item in reversed(history):  # 최근 회차부터
            try:
                run = int(item.get("n") or 0)
            except (TypeError, ValueError):
                continue
            history_units = item.get("units") if isinstance(item.get("units"), list) else []
            for index, unit in enumerate(history_units):
                if not isinstance(unit, dict) or unit.get("status") != "completed":
                    continue
                cell_id = str(unit.get("id") or "")
                add(unit.get("outputKey"), kind="take", group=f"r{run}", index=index, cellId=cell_id,
                    cutIds=cut_ids(units_by_id.get(cell_id) or {}), trimTo=None, run=run)
            add(item.get("finalKey"), kind="final", group=f"r{run}-final", index=0, cellId="", cutIds=[], trimTo=None, run=run)
    return items


def _default_group(items: list[dict]) -> list[dict]:
    """기본 문서에 쓸 재료 = 재료 목록의 첫 묶음(group). _material_items 가 지금 결과 → 합친 영상 → 최근 실사 회차 결과 →
    그 회차 합친 영상 순서로 넣으므로, 지금 결과가 있으면 그것, 없으면 합친 영상(묶음에 1개), 그것도 없으면 최근 회차다."""
    if not items:
        return []
    return [m for m in items if m["group"] == items[0]["group"]]


def _retake_state(owner: str, pid: str, gen_id: str) -> dict | None:
    """⑦ AI로 고치기 상태(retakes.json, 지시서 116). 켬이면 정리된 상태(못 읽으면 빈 상태 + 경고), 끔이면 None(S3 를 읽지 않는다)."""
    if not vnew_settings.VNEW_EDIT_RETAKE:
        return None
    from . import retake  # noqa: PLC0415 — retake 가 edit 를 import 한다(순환 피함).

    try:
        return retake.read_state(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 보기·저장은 버전 없이 계속한다.
        log.warning("vnew.edit.retake_state_failed pid=%s err=%s", pid, type(exc).__name__)
        return {"v": 1, "cells": {}}


def _all_items(gen: dict, variant: str, retakes: dict | None) -> list[dict]:
    """재료 허용 목록 = _material_items + 다시 만든 버전(retake.material_items, 지시서 116). 버전은 그 칸 지금 결과(current) 바로 뒤에,
    지금 결과가 없는 칸의 버전은 끝에 둔다. retakes 가 None(끔)이면 _material_items 그대로다. 첫 묶음(기본 문서)은 바뀌지 않는다."""
    items = _material_items(gen, variant)
    if retakes is None:
        return items
    from . import retake  # noqa: PLC0415

    seen = {m["sourceKey"] for m in items}
    by_cell: dict[str, list[dict]] = {}
    for item in retake.material_items(gen, variant, retakes):
        if item["sourceKey"] in seen:
            continue
        seen.add(item["sourceKey"])
        by_cell.setdefault(item["cellId"], []).append(item)
    if not by_cell:
        return items
    out: list[dict] = []
    for item in items:
        out.append(item)
        if item["group"] == "current":
            out.extend(by_cell.pop(item["cellId"], []))
    for rest in by_cell.values():
        out.extend(rest)
    return out


def _audio_sources(gen: dict, items: list[dict], meta: dict[str, dict]) -> dict[str, dict]:
    """소리 없는 다시 만든 버전의 대체 소리(지시서 116 보완 10). 반환 {버전 sourceKey: {sourceKey, mid, offset, scale, end}}.

    조건: AI로 고치기 버전(group "retake")이고 meta hasAudio 가 False, 같은 칸 지금 결과(group "current", v1)의 hasAudio 가 True.
    시각 대응은 retake._time_map(화면 editFixTimeMap 과 같은 규칙)으로 버전 시각 → 칸 녹화 시각 → v1 시각을 잇는다:
    v1 시각 = offset + 버전 시각 × scale, offset = (버전 offset − v1 offset) ÷ v1 scale, scale = 버전 scale ÷ v1 scale.
    구간 버전이고 v1 이 칸과 1:1 이면 offset = range.start, 전체 버전은 0. end = v1 에서 쓸 수 있는 끝(그 뒤는 무음).
    저장값은 읽기만 한다(판정은 읽을 때마다 계산).
    작업 193 fix1: meta silent True(필름 캐시 파형이 −60 dBFS 아래, _retake_meta)인 옛 길 버전도 소리 없음으로 본다. videoSource result 버전은
    자기 소리를 늘 버리고 _result_audio(fromKey 의 range.in + 버전 시각, scale 1, end = range.out — fromKey 가 다시 result 버전이거나
    소리를 대체한 옛 길 버전이면 그 원천까지 따라간다)를 쓴다.
    """
    from . import retake  # noqa: PLC0415 — retake 가 edit 를 import 한다(순환 피함).

    current = {m.get("cellId"): m for m in items if m.get("group") == "current"}
    cells = retake._cell_index(gen)  # noqa: SLF001
    out: dict[str, dict] = {}
    for item in items:
        if item.get("group") != "retake" or item.get("videoSource") == retake.RESULT_SOURCE:
            continue
        own = meta.get(item["sourceKey"]) or {}
        base = current.get(item.get("cellId"))
        silent = own.get("hasAudio") is False or (own.get("hasAudio") is True and own.get("silent") is True)
        if not silent or not base:
            continue
        info = meta.get(base["sourceKey"]) or {}
        if info.get("hasAudio") is not True:
            continue
        found = cells.get(str(item.get("cellId") or ""))
        cell_seconds = float(((found[1] if found else {}) or {}).get("seconds") or 0.0)
        kind = str(item.get("kind") or "")
        off2, sc2, _ = retake._time_map(item.get("trimTo"), retake._valid_range(item.get("range")), kind, cell_seconds,  # noqa: SLF001
                                        own.get("duration"))
        off1, sc1, end1 = retake._time_map(base.get("trimTo"), None, kind, cell_seconds, info.get("duration"))  # noqa: SLF001
        if sc1 <= 0 or sc2 <= 0 or end1 <= 0:
            continue
        offset = (off2 - off1) / sc1
        if offset >= end1 - MIN_CLIP_S:
            continue
        out[item["sourceKey"]] = {"sourceKey": base["sourceKey"], "mid": base["mid"], "offset": round(offset, 3),
                                  "scale": round(sc2 / sc1, 6), "end": round(end1, 3)}
    by_key = {m["sourceKey"]: m for m in items}
    found: dict[str, dict] = {}
    for item in items:
        if item.get("group") == "retake" and item.get("videoSource") == retake.RESULT_SOURCE:
            source, _why = _result_audio(item, by_key, out, meta)
            if source:
                found[item["sourceKey"]] = source
    out.update(found)
    return out


def _result_audio(item: dict, by_key: dict[str, dict], old: dict[str, dict], meta: dict[str, dict]) -> tuple[dict | None, str]:
    """작업 193 fix1: videoSource result 버전 하나의 소리 원천. 반환 (원천 {sourceKey, mid, offset, scale, end} | None, 까닭).

    버전 시각 t → fromKey 시각 range.in + t(scale 1), 끝 range.out. fromKey 가 다시 result 버전이면 같은 규칙을 이어 붙이고(순환이면 멈춤),
    소리를 대체한 옛 길 버전(old = 옛 길 대체 결과)이면 그 대체(offset·scale·end)까지 잇는다. 마지막 원천의 meta hasAudio 가
    True 면 "ok", False 면 (None, "silent" — 원천에 소리가 없다), 모르면 (None, "unknown"), 구간·fromKey 가 없거나 순환이면 (None, "bad").
    """
    from . import retake  # noqa: PLC0415

    offset, scale, end = 0.0, 1.0, math.inf
    seen = {item["sourceKey"]}
    node = item
    source = ""
    while True:
        rng = node.get("range") if isinstance(node.get("range"), dict) else {}
        r_in, r_out = _num(rng.get("in")), _num(rng.get("out"))
        source = str(node.get("fromKey") or "")
        if r_in is None or r_out is None or r_in < 0 or r_out <= r_in or not source or source in seen:
            return None, "bad"
        offset, end = r_in + offset, min(r_in + end, r_out)
        seen.add(source)
        nxt = by_key.get(source)
        if nxt and nxt.get("group") == "retake" and nxt.get("videoSource") == retake.RESULT_SOURCE:
            node = nxt
            continue
        sub = old.get(source)
        if sub:  # 옛 길 버전이 그 칸 v1 소리를 쓴다 → 그 v1 까지
            offset, scale = float(sub["offset"]) + offset * float(sub["scale"]), scale * float(sub["scale"])
            end = min(float(sub["offset"]) + end * float(sub["scale"]), float(sub["end"]))
            source = str(sub["sourceKey"])
        break
    has_audio = (meta.get(source) or {}).get("hasAudio")
    if has_audio is not True:
        return None, "silent" if has_audio is False else "unknown"
    if offset >= end - MIN_CLIP_S:
        return None, "silent"
    return {"sourceKey": source, "mid": media_id(source), "offset": round(offset, 3), "scale": round(scale, 6),
            "end": round(end, 3)}, "ok"


def _result_chain(items: list[dict], keys) -> list[str]:
    """작업 193 fix1: keys 중 videoSource result 버전에서 fromKey 를 따라간 키(자기 제외, 처음 나온 순서, 순환 멈춤)."""
    from . import retake  # noqa: PLC0415

    by_key = {m["sourceKey"]: m for m in items}
    out: list[str] = []
    for key in keys:
        node, seen = by_key.get(key), {key}
        while node and node.get("group") == "retake" and node.get("videoSource") == retake.RESULT_SOURCE:
            nxt = str(node.get("fromKey") or "")
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            if nxt not in out:
                out.append(nxt)
            node = by_key.get(nxt)
    return out


def _result_mutes(items: list[dict], meta: dict[str, dict], sources: dict[str, dict]) -> set[str]:
    """작업 193 fix1: 소리 원천에 소리가 없는(meta hasAudio False) result 버전 키 — 자기(v2v) 소리를 쓰지 않고 무음으로 둔다.
    원천을 모르면(meta 없음) 넣지 않는다(보기는 캐시만 보므로 판정을 미룬다 — 내보내기는 재고 나서 본다)."""
    from . import retake  # noqa: PLC0415

    by_key = {m["sourceKey"]: m for m in items}
    old = {k: v for k, v in sources.items() if (by_key.get(k) or {}).get("videoSource") != retake.RESULT_SOURCE}
    out: set[str] = set()
    for item in items:
        if item.get("group") == "retake" and item.get("videoSource") == retake.RESULT_SOURCE and item["sourceKey"] not in sources:
            if _result_audio(item, by_key, old, meta)[1] == "silent":
                out.add(item["sourceKey"])
    return out


def _clip_audio(clip: dict, source: dict | None) -> tuple[float, float] | None:
    """클립 한 개의 대체 소리 구간 (v1 시작, v1 끝)(_audio_sources 한 항목). 소리 끈 클립·남은 소리가 없으면 None."""
    if not source or clip.get("muted"):
        return None
    start = max(0.0, float(source["offset"]) + float(clip.get("in") or 0.0) * float(source["scale"]))
    end = float(source["end"])
    if start >= end - MIN_CLIP_S:
        return None
    return round(start, 3), round(end, 3)


def _retake_meta(owner: str, pid: str, gen_id: str, items: list[dict], meta: dict[str, dict], *, probe: bool,
                 silent_keys=None) -> dict[str, dict]:
    """_audio_sources 에 넘길 meta: 버전과 그 칸 v1 중 meta 에 없는 것을 채운다(probe 면 _ensure_meta, 아니면 길이 캐시만).
    작업 193 fix1: result 버전이 따라가는 fromKey(_result_chain)도 채운다. silent_keys 를 주면 그 키와 fromKey 중 옛 길 버전(소리 있음)에
    필름 캐시(film.json — 이미 만든 파형, 새로 재지 않음)가 있으면 silent(피크 최대 < SILENT_PEAK)를 단다."""
    cells = {m.get("cellId") for m in items if m.get("group") == "retake"}
    chain = _result_chain(items, [m["sourceKey"] for m in items if m.get("group") == "retake"])
    want = [m["sourceKey"] for m in items if m.get("cellId") in cells and m.get("group") in ("current", "retake")] + chain
    keys = [k for k in dict.fromkeys(want) if not isinstance((meta.get(k) or {}).get("hasAudio"), bool)]
    if keys:
        if probe:
            extra, _ = _ensure_meta(owner, pid, gen_id, keys)
        else:
            cached = _read_index(owner, pid, gen_id)
            extra = {k: cached[media_id(k)] for k in keys
                     if isinstance(cached.get(media_id(k)), dict) and cached[media_id(k)].get("sourceKey") == k}
        meta = {**extra, **{k: v for k, v in meta.items() if isinstance((v or {}).get("hasAudio"), bool) or k not in extra}}
    if silent_keys:
        meta = _mark_silent(owner, pid, gen_id, items, meta, set(silent_keys) | set(chain))
    return meta


def _peaks_silent(values) -> bool | None:
    """작업 193 fix1: 파형 피크(0~1, 소수 3자리)가 모두 SILENT_PEAK(−60 dBFS) 아래인가. 숫자가 없으면 None(모름)."""
    numbers = [float(v) for v in values if not isinstance(v, bool) and isinstance(v, (int, float))] if isinstance(values, list) else []
    return max(numbers) < SILENT_PEAK if numbers else None


def _mark_silent(owner: str, pid: str, gen_id: str, items: list[dict], meta: dict[str, dict], keys: set) -> dict[str, dict]:
    """작업 193 fix1: keys 중 옛 길 다시 만든 버전(소리 트랙 있음)의 필름 캐시 파형으로 meta silent 를 단 사본. 캐시가 없으면 그대로."""
    from . import retake  # noqa: PLC0415

    targets = [m["sourceKey"] for m in items if m.get("group") == "retake" and m.get("videoSource") != retake.RESULT_SOURCE
               and m["sourceKey"] in keys and (meta.get(m["sourceKey"]) or {}).get("hasAudio") is True
               and "silent" not in (meta.get(m["sourceKey"]) or {})]
    if not targets:
        return meta
    out = dict(meta)
    for key in dict.fromkeys(targets):
        try:
            data, _ = _read_json(film_key(owner, pid, gen_id, media_id(key)))
        except Exception as exc:  # noqa: BLE001 — 캐시라 못 읽으면 지금 판정(소리 있음) 그대로
            log.warning("vnew.edit.silent_read_failed pid=%s mid=%s err=%s", pid, media_id(key), type(exc).__name__)
            continue
        if not _film_ok(data, key) or not data.get("hasAudio"):
            continue
        silent = _peaks_silent((data.get("peaks") or {}).get("values"))
        if silent is not None:
            out[key] = {**out[key], "silent": silent}
    return out


def _number_takes(gen: dict, variant: str, items: list[dict]) -> None:
    """재료에 테이크 번호를 붙인다(작업 145 — ⑦ 재료 [테이크 고르기] 메뉴). 저장값·허용 목록·기본 문서는 바꾸지 않는다.

    항목마다 takeNo(테이크면 번호, 아니면 None)·takeLabel("테이크 n" 또는 "")·takeAt(만든 시각 ISO 또는 "")·
    takeOf(그 테이크의 컷 재료면 테이크 번호, 아니면 None)를 둔다.
      - 실사 통 테이크(회차가 있다): 끝난 회차마다 그 회차 병합본(final-real-r<n>.mp4, 다시 이어 붙였으면 -fixed — 회차 기록 키 그대로)
        = "테이크 n"(회차 번호 n 그대로). 그 회차의 칸 결과·컷·다시 만든 버전은 takeOf n. 끝나지 않은·실패·취소 회차는 테이크가 아니다.
      - 그 밖(웹툰·회차 없는 실사): 통 테이크 결과(지금 결과 → 다시 만든 버전 v2… 순서, 오래된 것부터) = 테이크 1, 2, ….
        통 테이크 결과가 없으면(컷 모드·순차 모드) 합친 영상이 테이크 1 이고 컷·구간 결과는 takeOf 1.
    """
    for item in items:
        item.update(takeNo=None, takeLabel="", takeAt="", takeOf=None)
    gen = gen if isinstance(gen, dict) else {}

    def run_no(value) -> int:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    def mark(item: dict, number: int, at: str = "") -> None:
        item.update(takeNo=number, takeLabel=f"테이크 {number}", takeAt=str(at or ""))

    run = gen.get("realRun") if isinstance(gen.get("realRun"), dict) else {}
    history = [h for h in (gen.get("realHistory") or []) if isinstance(h, dict)]
    if variant == "real" and str(gen.get("mode") or "") == "previz-take" and (run_no(run.get("n")) or history):
        current_n = run_no(run.get("n")) or run_no((gen.get("finalReal") or {}).get("n"))
        done_runs = {run_no(h.get("n")): h for h in history if h.get("status") == "done" and h.get("finalKey")}
        current_done = False
        for item in items:
            group = str(item.get("group") or "")
            if group == "final" and current_n:
                mark(item, current_n, run.get("startedAt"))
                current_done = True
            elif group.endswith("-final") and run_no(item.get("run")) in done_runs:
                mark(item, run_no(item["run"]), done_runs[run_no(item["run"])].get("startedAt"))
        for item in items:
            group = str(item.get("group") or "")
            if group in ("current", "retake") and current_done:
                item["takeOf"] = current_n
            elif group == f"r{run_no(item.get('run'))}" and run_no(item.get("run")) in done_runs:
                item["takeOf"] = run_no(item["run"])
        return
    takes = [m for m in items if m.get("kind") == "take" and m.get("group") == "current"]
    takes += sorted((m for m in items if m.get("kind") == "take" and m.get("group") == "retake"),
                    key=lambda m: (int(m.get("version") or 0), int(m.get("index") or 0)))
    if takes:
        for number, item in enumerate(takes, 1):
            mark(item, number)
        by_cell = {str(m.get("cellId") or ""): m["takeNo"] for m in takes if m.get("group") == "current"}
        for item in items:  # 실사 컷 단위 Kling 칸("테이크id#컷id")은 그 테이크의 컷
            cell = str(item.get("cellId") or "")
            if item.get("takeNo") is None and item.get("kind") == "cut" and "#" in cell:
                item["takeOf"] = by_cell.get(cell.split("#", 1)[0])
        return
    final = next((m for m in items if m.get("group") == "final"), None)
    if final is None:
        return
    mark(final, 1)
    for item in items:
        if item is not final and item.get("group") in ("current", "retake"):
            item["takeOf"] = 1


def _cut_numbers(plan: dict) -> dict[str, int]:
    cuts = ((plan or {}).get("conti") or {}).get("cuts") or []
    out: dict[str, int] = {}
    for cut in cuts:
        if isinstance(cut, dict) and isinstance(cut.get("no"), int) and not isinstance(cut.get("no"), bool):
            out[str(cut.get("id"))] = int(cut["no"])
    return out


def _describe(plan: dict, items: list[dict]) -> None:
    """재료마다 화면 이름(label)을 붙인다(컷 3 · 테이크 1 · 컷 1–4 · 구간 2 · 합친 영상, 실사 회차면 앞에 '실사 2회차 · ')."""
    numbers = _cut_numbers(plan)
    for item in items:
        nums = [numbers[c] for c in item.get("cutIds") or [] if c in numbers]
        index = int(item.get("index") or 0) + 1
        if item["kind"] == "cut":
            label = f"컷 {nums[0]}" if nums else f"컷 {index}"
        elif item["kind"] == "take":
            label = f"테이크 {index}" + (f" · 컷 {min(nums)}–{max(nums)}" if len(nums) > 1 else (f" · 컷 {nums[0]}" if nums else ""))
        elif item["kind"] == "segment":
            label = f"구간 {index}"
        else:
            label = "합친 영상"
        if item.get("run") and item["group"] not in ("current", "final"):
            label = f"실사 {item['run']}회차 · " + label
        if item.get("version"):  # 지시서 116: AI로 고쳐 다시 만든 버전(v2…)
            suffix = f" v{item['version']}"
            label = label[:MAX_LABEL_LEN - len(suffix)] + suffix
        item["label"] = label[:MAX_LABEL_LEN]


def _take_spans(plan: dict, unit: dict | None, duration: float) -> list[tuple[float, float, str]] | None:
    """통 테이크 결과를 녹화 컷 경계로 나눈 [(in, out, cutId)]. 경계를 믿을 수 없으면 None(테이크 통째).

    경계 = 테이크의 녹화 구간 순서(recSegIds)대로 각 구간 안 컷 범위(generate._previz_units)를 이어 놓은 것을 결과 길이에
    비례해 늘이거나 줄인 값. 아래 중 하나면 None: 지금 녹화 키가 테이크를 만들 때와 다르다(다시 녹화), 컷 순서가 테이크의
    cutIds 와 다르다, 결과 길이가 녹화 합계와 0.5초·10% 넘게 다르다, 컷이 하나뿐이다.
    """
    if not isinstance(unit, dict) or duration <= 0:
        return None
    seg_ids = [str(s) for s in unit.get("recSegIds") or []]
    rec_keys = [str(k) for k in unit.get("recKeys") or []]
    if not seg_ids or len(seg_ids) != len(rec_keys):
        return None
    recordings = ((plan or {}).get("previz") or {}).get("recordings") or {}
    by_seg: dict[str, list[dict]] = {}
    for item in generate._previz_units(plan):  # noqa: SLF001
        by_seg.setdefault(str(item.get("recSegId") or ""), []).append(item)
    spans: list[tuple[float, float, str]] = []
    offset = 0.0
    for seg_id, rec_key in zip(seg_ids, rec_keys):
        rec = recordings.get(seg_id) if isinstance(recordings.get(seg_id), dict) else {}
        seg_seconds = _num(rec.get("seconds")) or 0.0
        cuts = by_seg.get(seg_id) or []
        if str(rec.get("key") or "") != rec_key or not cuts or seg_seconds <= 0:
            return None
        for cut in cuts:
            spans.append((offset + float(cut.get("startSec") or 0.0), offset + float(cut.get("endSec") or 0.0),
                          str(cut.get("cutId") or "")))
        offset += seg_seconds
    if len(spans) < 2 or [s[2] for s in spans] != [str(c) for c in unit.get("cutIds") or []]:
        return None
    if abs(duration - offset) > max(0.5, 0.1 * offset):
        return None
    scale = duration / offset
    out: list[tuple[float, float, str]] = []
    for index, (start, end, cut_id) in enumerate(spans):
        lo = 0.0 if index == 0 else round(start * scale, 3)
        hi = round(duration, 3) if index == len(spans) - 1 else min(round(end * scale, 3), round(duration, 3))
        if hi - lo >= MIN_CLIP_S:
            out.append((lo, hi, cut_id))
    return out if len(out) >= 2 else None


def _default_clips(plan: dict, gen: dict, items: list[dict], meta: dict[str, dict]) -> list[dict]:
    """처음 열 때의 기본 문서 클립(저장은 첫 수정 때). 길이를 못 읽은 재료는 뺀다. 한도를 넘으면 거기서 멈춘다."""
    units_by_id = {str(u.get("id") or ""): u for u in (gen.get("units") or []) if isinstance(u, dict)}
    numbers = _cut_numbers(plan)
    clips: list[dict] = []
    total = 0.0
    for item in _default_group(items):
        duration = _num((meta.get(item["sourceKey"]) or {}).get("duration")) or 0.0
        if duration < MIN_CLIP_S:
            continue
        pieces = _take_spans(plan, units_by_id.get(item["cellId"]), duration) if item["kind"] == "take" else None
        if pieces is None:
            end = min(duration, item["trimTo"]) if item.get("trimTo") else duration
            pieces = [(0.0, round(end, 3), "")]
        for start, end, cut_id in pieces:
            if len(clips) >= MAX_CLIPS or total + (end - start) > MAX_TOTAL_S + 1e-6:
                return clips
            label = f"컷 {numbers[cut_id]}" if cut_id and cut_id in numbers else item.get("label") or ""
            clips.append({"id": f"c{len(clips) + 1}", "sourceKey": item["sourceKey"], "in": round(start, 3),
                          "out": round(end, 3), "muted": False, "label": label[:MAX_LABEL_LEN]})
            total += end - start
    return clips


def summary(gen: dict | None) -> dict:
    """gen_view 의 편집 단계 요약(S3 를 읽지 않는다). {available, variants: 재료가 있는 그림체}.

    재료가 없으면 한국어 reason 을 더한다(지시서 115 — 화면이 잠긴 ⑦ 칩의 title·안내로 보인다). gen 이 없어도(None) 부른다.
    재료가 있으면 reason 키가 없다(지시서 110 보기 그대로).
    """
    try:
        variants = [v for v in VARIANTS if _material_items(gen, v)]
    except Exception:  # noqa: BLE001 — 보기는 실패하지 않는다.
        variants = []
    out = {"available": bool(variants), "variants": variants}
    if not variants:
        out["reason"] = _lock_reason(gen)
    return out


def _lock_reason(gen) -> str:
    """재료가 없을 때 ⑦ 이 잠긴 이유(LOCK_REASONS): gen 없음 · 만드는 중 · 취소 · 실패 · 그 밖."""
    if not isinstance(gen, dict) or not gen:
        return LOCK_REASONS["none"]
    status = str(gen.get("status") or "")
    return LOCK_REASONS[status] if status in ("running", "cancelled", "failed") else LOCK_REASONS["other"]


# ================================================================ 길이 재기(probe) 캐시


def _read_index(owner: str, pid: str, gen_id: str) -> dict:
    data, _ = _read_json(media_index_key(owner, pid, gen_id))
    items = (data or {}).get("items")
    return dict(items) if isinstance(items, dict) else {}


def _probe_source(key: str) -> dict:
    """재료 하나를 임시 파일로 받아 길이·소리·크기·fps 를 잰다(프로세스당 한 번에 하나). 못 읽으면 duration 0 + error."""
    with _MEDIA_SEM:
        with tempfile.TemporaryDirectory(prefix="vnew-edit-probe-") as workdir:
            path = os.path.join(workdir, "source.mp4")
            try:
                _download(key, path, bucket=vpoc_settings.VIDEO_BUCKET)
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.edit.probe_failed mid=%s stage=download err=%s", media_id(key), type(exc).__name__)
                return {"duration": 0.0, "error": "원본 영상을 받지 못했습니다."}
            info = _probe_file(path)
    duration = _num((info or {}).get("duration")) or 0.0
    if duration <= 0:
        log.warning("vnew.edit.probe_failed mid=%s stage=probe", media_id(key))
        return {"duration": 0.0, "error": "원본 영상 길이를 읽지 못했습니다."}
    fps = _num(info.get("fps")) or 0.0
    return {
        "duration": round(duration, 3),
        "hasAudio": bool(info.get("hasAudio")),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "fps": round(fps, 3),
    }


def _merge_index(owner: str, pid: str, gen_id: str, found: dict[str, dict]) -> None:
    def merge(data):
        data = data if isinstance(data, dict) else {}
        items = data.get("items") if isinstance(data.get("items"), dict) else {}
        items.update(found)
        return {"v": 1, "items": items}

    try:
        _mutate_json(media_index_key(owner, pid, gen_id), merge)
    except Exception as exc:  # noqa: BLE001 — 캐시라 실패해도 다음 요청이 다시 잰다.
        log.warning("vnew.edit.index_write_failed pid=%s err=%s", pid, type(exc).__name__)


def _ensure_meta(owner: str, pid: str, gen_id: str, keys: list[str], *, budget_s: float | None = None) -> tuple[dict, bool]:
    """키별 {duration, hasAudio, width, height, fps}(캐시 먼저). 반환 (키 → 정보, 다 읽었는가).

    캐시에 없는 키만 차례로 잰다. budget_s 를 주면 그 시간을 넘긴 뒤에는 더 재지 않는다(그 키는 duration None).
    못 읽은 키는 {"duration": 0.0, "error": …}이고 캐시에 남기지 않는다(다음 요청이 다시 잰다).
    """
    ordered = list(dict.fromkeys(str(k) for k in keys if k))
    items = _read_index(owner, pid, gen_id)
    failed: dict[str, dict] = {}
    complete = True
    if any(media_id(k) not in items for k in ordered):
        with _lock(f"probe:{owner}/{pid}/{gen_id}"):
            items = _read_index(owner, pid, gen_id)
            missing = [k for k in ordered if media_id(k) not in items]
            found: dict[str, dict] = {}
            started = time.monotonic()
            for key in missing:
                if budget_s is not None and (found or failed) and time.monotonic() - started > budget_s:
                    complete = False
                    break
                info = _probe_source(key)
                if info.get("duration"):
                    found[media_id(key)] = {"sourceKey": key, **info, "at": now_iso()}
                else:
                    failed[key] = info
            if found:
                _merge_index(owner, pid, gen_id, found)
                items.update(found)
            if missing:
                log.info("vnew.edit.probe pid=%s gen=%s probed=%d failed=%d complete=%s ms=%d", pid, gen_id, len(found),
                         len(failed), complete, int((time.monotonic() - started) * 1000))
    out: dict[str, dict] = {}
    for key in ordered:
        entry = items.get(media_id(key))
        if isinstance(entry, dict) and entry.get("sourceKey") == key:
            out[key] = entry
        elif key in failed:
            out[key] = failed[key]
        else:
            out[key] = {"duration": None}
    return out, complete


# ================================================================ 문서 검증


def _check_clip_list(raw, where: str) -> list:
    if not isinstance(raw, list):
        raise VpocError(where, "BadClips", "클립 목록(clips)이 필요합니다.", 400)
    if len(raw) > MAX_CLIPS:
        raise VpocError(where, "TooManyClips", f"클립은 {MAX_CLIPS}개까지 둘 수 있습니다.", 400)
    return raw


def _clean_clips(raw, allowed: set[str], durations: dict[str, float | None], where: str) -> list[dict]:
    """클립 목록을 검사해 저장할 꼴로 정리한다. 틀리면 400(몇 번째 클립인지 문구에 넣는다).

    - id: 영문·숫자·_·- 1~40자, 문서 안에서 겹치지 않는다.
    - sourceKey: 이 gen 그 그림체의 재료(allowed)만. 원본 길이를 못 읽었으면 409 SourceUnreadable.
    - in·out: 유한한 JSON 숫자(bool·글자 아님), 밀리초로 반올림, 0 ≤ in, out ≤ 원본 길이, out − in ≥ 0.04.
    - speed(지시서 115): 없거나 null 이면 1, 있으면 SPEEDS 중 하나(JSON 숫자). 타임라인 길이((out − in) / speed)도 0.04 이상.
      1 이면 저장하지 않는다(속도를 안 쓴 문서는 115 전 모양 그대로).
    - muted: 없으면 false, 있으면 bool. label: 글자면 제어 문자를 빼고 40자, 그 밖은 버린다.
    - 작업 181: audioLead·audioTail(0~2초, 0.05초 단위, 0·null 은 저장 안 함 — _clean_shift), transition(컷이면 저장 안 함, 디졸브는
      {"type": "dissolve", "seconds": 0.25·0.5·0.75·1} — _clean_transition)과 자리 규칙(_check_overlaps). 틀리면 400
      BadClipAudioLead·BadClipAudioTail·BadTransition. 소리 끈 클립의 앞당김·이어 감은 받은 그대로 저장한다(내보내기가 무시한다).
    - 작업 185: audioSlip(−1~1초, 0.05초 단위, 0·null 은 저장 안 함 — _clean_slip). 틀리면 400 BadClipAudioSlip. 소리 끈 클립도 그대로 저장.
    - 전체 길이(타임라인 길이 합 − 디졸브 합) ≤ MAX_TOTAL_S.
    """
    raw = _check_clip_list(raw, where)
    clips: list[dict] = []
    seen: set[str] = set()
    total = 0.0
    for number, clip in enumerate(raw, 1):
        if not isinstance(clip, dict):
            raise VpocError(where, "BadClip", f"{number}번째 클립 형식이 올바르지 않습니다.", 400)
        clip_id = clip.get("id")
        if not isinstance(clip_id, str) or not _CLIP_ID_RE.match(clip_id):
            raise VpocError(where, "BadClipId", f"{number}번째 클립 id 가 올바르지 않습니다.", 400)
        if clip_id in seen:
            raise VpocError(where, "DuplicateClipId", f"{number}번째 클립 id 가 앞 클립과 같습니다.", 400)
        seen.add(clip_id)
        source = clip.get("sourceKey")
        if not isinstance(source, str) or source not in allowed:
            raise VpocError(where, "SourceNotAllowed", f"{number}번째 클립의 원본이 이 영상의 재료가 아닙니다.", 400)
        duration = _num(durations.get(source))
        if not duration or duration <= 0:
            raise VpocError(where, "SourceUnreadable",
                            f"{number}번째 클립의 원본 길이를 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409)
        start, end = _num(clip.get("in")), _num(clip.get("out"))
        if start is None or end is None:
            raise VpocError(where, "BadClipTime", f"{number}번째 클립의 시작·끝 시간이 올바르지 않습니다.", 400)
        start, end = round(start, 3), round(end, 3)
        raw_speed = clip.get("speed")
        speed = 1.0 if raw_speed is None else _num(raw_speed)
        if speed not in SPEEDS:
            raise VpocError(where, "BadClip", f"{number}번째 클립의 속도(speed)는 0.5·0.75·1·1.25·1.5·2 중 하나여야 합니다.", 400)
        if start < 0 or end > duration + _TIME_EPS or end - start < MIN_CLIP_S - 1e-9:
            raise VpocError(where, "BadClipRange",
                            f"{number}번째 클립 구간은 원본 길이({duration:.2f}초) 안에서 {MIN_CLIP_S}초 이상이어야 합니다.", 400)
        end = min(end, round(duration, 3))
        if speed != 1 and (end - start) / speed < MIN_CLIP_S - 1e-9:
            raise VpocError(where, "BadClipRange", f"{number}번째 클립은 속도를 반영한 길이가 {MIN_CLIP_S}초 이상이어야 합니다.", 400)
        muted = clip.get("muted", False)
        if not isinstance(muted, bool):
            raise VpocError(where, "BadClipMuted", f"{number}번째 클립의 소리 끄기(muted) 값이 올바르지 않습니다.", 400)
        item = {"id": clip_id, "sourceKey": source, "in": start, "out": end, "muted": muted}
        if speed != 1:
            item["speed"] = speed
        # 작업 181: 앞당김·이어 감·디졸브. 기본값(0·컷)은 저장하지 않는다(181 전 문서 모양 그대로). 소리 끈 클립도 받은 그대로 둔다.
        for key, code, name in (("audioLead", "BadClipAudioLead", "소리 먼저 시작(audioLead)은"),
                                ("audioTail", "BadClipAudioTail", "소리 이어 가기(audioTail)는")):
            shift = _clean_shift(clip.get(key), number, where, code, name)
            if shift:
                item[key] = shift
        slip = _clean_slip(clip.get("audioSlip"), number, where)  # 작업 185: 0·null 은 저장 안 함, 소리 끈 클립도 받은 그대로
        if slip:
            item["audioSlip"] = slip
        dissolve = 0.0 if edit_text.is_dip(clip.get("transition")) else _clean_transition(clip.get("transition"), number, where)  # 작업 215b
        if dissolve:
            item["transition"] = {"type": "dissolve", "seconds": dissolve}
        item.update(edit_text.clean_clip_fx(clip, number, where))  # 작업 215b: 블랙/화이트 전환 · 색 보정(없으면 그대로)
        label = clip.get("label")
        if isinstance(label, str):
            label = _CONTROL_RE.sub("", label).strip()[:MAX_LABEL_LEN]
            if label:
                item["label"] = label
        total += (end - start) / speed
        total += edit_tools.clean_extras(clip, item, number, where, end - start)  # 작업 215c
        clips.append(item)
    _check_overlaps(clips, where)
    edit_text.fit_dips(clips)  # 작업 215b: 짧아진 클립의 블랙/화이트 전환은 맞는 길이로
    edit_tools.check_rules(clips, where)  # 작업 215c
    total -= sum(_dissolve_of(c) for c in clips[:-1])  # 작업 181: 한도는 디졸브로 겹친 뒤의 길이
    if total > MAX_TOTAL_S + 1e-6:
        raise VpocError(where, "TooLong", f"타임라인 전체 길이는 {MAX_TOTAL_S:.0f}초까지입니다.", 400)
    return clips


def _clean_shift(raw, number: int, where: str, code: str, name: str) -> float:
    """작업 181: audioLead·audioTail 한 값. 없거나 null 이면 0, 있으면 0~2초 0.05초 단위 JSON 숫자(밀리초 반올림 전 기준)."""
    if raw is None:
        return 0.0
    value = _num(raw)
    steps = value / AUDIO_SHIFT_STEP_S if value is not None else 0.0
    if value is None or value < 0 or value > AUDIO_SHIFT_MAX_S + 1e-9 or abs(steps - round(steps)) > 1e-6:
        raise VpocError(where, code, f"{number}번째 클립의 {name} 0~{AUDIO_SHIFT_MAX_S:g}초 사이 {AUDIO_SHIFT_STEP_S:g}초 단위여야 합니다.", 400)
    return round(round(steps) * AUDIO_SHIFT_STEP_S, 2)


def _clean_slip(raw, number: int, where: str) -> float:
    """작업 185: audioSlip 한 값. 없거나 null 이면 0, 있으면 −1~1초 0.05초 단위 JSON 숫자(양수 = 소리가 화면보다 늦게)."""
    if raw is None:
        return 0.0
    value = _num(raw)
    steps = value / AUDIO_SHIFT_STEP_S if value is not None else 0.0
    if value is None or abs(value) > AUDIO_SLIP_MAX_S + 1e-9 or abs(steps - round(steps)) > 1e-6:
        raise VpocError(where, "BadClipAudioSlip",
                        f"{number}번째 클립의 대사 타이밍(audioSlip)은 -{AUDIO_SLIP_MAX_S:g}~{AUDIO_SLIP_MAX_S:g}초 사이 "
                        f"{AUDIO_SHIFT_STEP_S:g}초 단위여야 합니다.", 400)
    return round(round(steps) * AUDIO_SHIFT_STEP_S, 2) or 0.0


def _clean_transition(raw, number: int, where: str) -> float:
    """작업 181: transition 한 값. 없음·null·{"type": "cut"} 이면 0(컷), {"type": "dissolve", "seconds": 4값 중 하나} 면 그 초."""
    if raw is None:
        return 0.0
    if isinstance(raw, dict) and raw.get("type") == "cut":
        return 0.0
    value = _num(raw.get("seconds")) if isinstance(raw, dict) and raw.get("type") == "dissolve" else None
    if value not in DISSOLVE_SECONDS:
        raise VpocError(where, "BadTransition",
                        f"{number}번째 클립의 전환(transition)은 컷 또는 디졸브(0.25·0.5·0.75·1초)여야 합니다.", 400)
    return value


def _check_overlaps(clips: list[dict], where: str) -> None:
    """작업 181: 자리 규칙. 첫 클립은 앞당김 없음, 마지막 클립은 이어 감·전환 없음, 디졸브는 앞뒤 두 클립 중 짧은 쪽의 45% 이하."""
    if not clips:
        return
    if clips[0].get("audioLead"):
        raise VpocError(where, "BadClipAudioLead", "첫 클립에는 소리 먼저 시작(audioLead)을 둘 수 없습니다(앞 클립이 없습니다).", 400)
    if clips[-1].get("audioTail"):
        raise VpocError(where, "BadClipAudioTail", "마지막 클립에는 소리 이어 가기(audioTail)를 둘 수 없습니다(뒤 클립이 없습니다).", 400)
    if clips[-1].get("transition"):
        raise VpocError(where, "BadTransition", "마지막 클립에는 다음 클립으로 가는 전환(transition)을 둘 수 없습니다.", 400)
    for number, (item, after) in enumerate(zip(clips, clips[1:]), 1):
        seconds = _dissolve_of(item)
        shorter = min(_clip_seconds(item), _clip_seconds(after))
        if seconds and seconds > DISSOLVE_MAX_SHARE * shorter + 1e-9:
            raise VpocError(where, "BadTransition",
                            f"{number}번째 클립의 디졸브({seconds:g}초)는 두 클립 중 짧은 쪽 길이({shorter:.2f}초)의 "
                            f"45%({DISSOLVE_MAX_SHARE * shorter:.2f}초) 이하여야 합니다.", 400)


def _doc_hash(variant: str, clips: list[dict], mix: dict | None = None) -> str:
    """내보내기 결과를 정하는 값만(그림체·원본·구간·소리·속도)의 sha256. 클립 id·이름은 넣지 않는다.

    속도는 1 이 아닐 때만 넣는다(지시서 115 — 속도를 안 쓴 문서·진행 중 내보내기의 해시가 115 전과 같다).
    작업 181: audioLead·audioTail·transition(디졸브)도 기본값(0·컷)이 아닐 때만 넣는다(181 전 문서의 해시 그대로).
    작업 185: audioSlip 도 0 이 아닐 때만 넣는다.
    """
    body = []
    for c in clips:
        item = {"sourceKey": c["sourceKey"], "in": c["in"], "out": c["out"], "muted": bool(c.get("muted"))}
        speed = _speed_of(c)
        if speed != 1:
            item["speed"] = speed
        for key in ("audioLead", "audioTail"):
            if _shift_of(c, key):
                item[key] = _shift_of(c, key)
        if _slip_of(c):  # 작업 185: 0 이 아닐 때만(185 전 문서의 해시 그대로)
            item["audioSlip"] = _slip_of(c)
        if _dissolve_of(c):
            item["transition"] = {"type": "dissolve", "seconds": _dissolve_of(c)}
        edit_tools.hash_extras(c, item)  # 작업 215c
        body.append(item)
    payload = {"variant": variant, "clips": body}
    if isinstance(mix, dict) and edit_audio.mix_active(mix.get("tracks"), mix.get("a1")):
        # 작업 215a: 오디오 트랙 · A1 설정이 결과를 바꿀 때만(트랙 없는 옛 문서의 해시는 그대로).
        payload["audio"] = edit_audio.hash_body(mix.get("tracks") or [], mix.get("a1") or edit_audio.default_a1())
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _doc_mix(doc: dict | None) -> dict:
    """작업 215a: 저장 문서의 오디오 설정 {tracks, a1}(저장 때 검사한 값 — 꼴만 본다). 없으면 빈 트랙 · 기본 A1."""
    doc = doc if isinstance(doc, dict) else {}
    try:
        a1 = edit_audio.clean_a1(doc.get("a1"), "vnew.edit")
    except VpocError:
        a1 = edit_audio.default_a1()
    return {"tracks": edit_audio.lenient_tracks(doc.get("audioTracks")), "a1": a1}


def _valid_doc(doc: dict | None, variant: str) -> bool:
    return isinstance(doc, dict) and doc.get("variant") == variant and isinstance(doc.get("clips"), list)


def _saved_docs(owner: str, pid: str, gen_id: str) -> dict[str, dict]:
    """작업 189: 두 그림체의 저장 문서. {variant: {doc, etag, source, version}}.

    그림체마다 edit-<variant>.json(doc_key)이 정본이다. 작업 189 전 문서 edit.json(legacy_doc_key)은 읽기만 하고 절대 고치거나
    지우지 않는다. 규칙(보기·저장·내보내기·사운드가 모두 이 함수를 거친다):
      - edit-<v>.json 이 있으면 그것이 v 의 문서다(source "own"). 그 뒤로 v 에는 edit.json 을 보지 않는다.
        파일은 있는데 꼴이 틀리면 doc None(저장 안 됨)이고 etag·version 은 그 파일 것이다(저장은 그 파일 기준 조건부 쓰기).
      - edit-<v>.json 이 없고 edit.json 의 variant 가 v 면 edit.json 이 v 의 저장 문서다(source "legacy", etag·version 도 그 파일 것).
        v 를 처음 저장하면 edit-<v>.json 을 새로 만든다(If-None-Match). 충돌 검사는 화면이 받은 기준 = edit.json 의 etag·version.
      - 둘 다 아니면(edit.json 이 다른 그림체이거나 없음) v 는 기본 문서다(doc None, etag "", version 0). 첫 저장은 만들기만 된다.
    """
    legacy, legacy_etag = _read_json(legacy_doc_key(owner, pid, gen_id))
    out: dict[str, dict] = {}
    for variant in VARIANTS:
        own, etag = _read_json(doc_key(owner, pid, gen_id, variant))
        if own is not None or etag:
            ok = _valid_doc(own, variant)
            out[variant] = {"doc": own if ok else None, "etag": etag, "source": "own", "version": _doc_version(own)}
        elif _valid_doc(legacy, variant):
            out[variant] = {"doc": legacy, "etag": legacy_etag, "source": "legacy", "version": _doc_version(legacy)}
        else:
            out[variant] = {"doc": None, "etag": "", "source": "", "version": 0}
    return out


def _doc_time(doc: dict | None) -> datetime | None:
    """문서 updatedAt(작업 189 부터 밀리초까지, 그 전은 초까지)을 UTC 시각으로. 없거나 꼴이 틀리면 None."""
    text = str((doc or {}).get("updatedAt") or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _now_ms() -> str:
    """편집 문서 updatedAt(작업 189: 밀리초까지 — 두 그림체를 잇달아 저장해도 어느 쪽이 나중인지 가린다)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _variant_order(gen: dict) -> list[str]:
    """그림체 기본 순서(작업 189 전 보기와 같다): gen.selected → 웹툰 → 실사."""
    order = [gen.get("selected")] if gen.get("selected") in VARIANTS else []
    return order + [v for v in ("webtoon", "real") if v not in order]


def _recent_variant(docs: dict[str, dict], gen: dict) -> str | None:
    """작업 189: 저장 문서가 있는 그림체 중 updatedAt 이 가장 최근인 것(edit.json 에서 온 문서 포함). 시각이 없거나 같으면
    기본 순서(_variant_order)가 앞선 쪽. 저장 문서가 하나도 없으면 None."""
    order = _variant_order(gen)
    saved = [v for v in order if docs[v]["doc"] is not None]
    if not saved:
        return None
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return max(saved, key=lambda v: (_doc_time(docs[v]["doc"]) or oldest, -order.index(v)))


def _doc_version(doc: dict | None) -> int:
    value = (doc or {}).get("version")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# ================================================================ 보기 · 저장


def _material_out(item: dict, info: dict | None, default_mids: set[str]) -> dict:
    info = info or {}
    duration = info.get("duration")
    return {
        "mid": item["mid"],
        "sourceKey": item["sourceKey"],
        "kind": item["kind"],
        "group": item["group"],
        "label": item.get("label") or "",
        "cutIds": list(item.get("cutIds") or []),
        "run": item.get("run"),
        # 작업 145: 테이크 고르기 메뉴(_number_takes). 테이크가 아니면 takeNo None·takeLabel "", 테이크의 컷이면 takeOf = 그 번호.
        "takeNo": item.get("takeNo"),
        "takeLabel": str(item.get("takeLabel") or ""),
        "takeAt": str(item.get("takeAt") or ""),
        "takeOf": item.get("takeOf"),
        "inDefault": item["mid"] in default_mids,
        "duration": duration if duration is None else float(duration),
        "hasAudio": info.get("hasAudio") if isinstance(info.get("hasAudio"), bool) else None,
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "fps": float(info.get("fps") or 0),
        "probeError": str(info.get("error") or ""),
        "videoUrl": _presign_video(item["sourceKey"]),
    }


def _take_cuts(plan: dict, gen: dict, variant: str, item: dict, duration) -> list[dict]:
    """작업 149: 통 테이크 재료 하나의 컷 [{cutId, no, label("컷 n", 번호 없으면 "장면 n"), start, end, seconds}](그 재료 원본 시간, 초).

    지시서 119 고르기 창과 같은 계산(retake._flag_cuts → _material_cuts: 칸 녹화 시각을 결과 길이로 역변환)이라 start·end 를 그대로
    retake 구간(?in=&out=)에 넣을 수 있다. 저장하지 않는다. 테이크가 아니거나 계산이 실패하면 [].
    """
    if item.get("kind") != "take":
        return []
    from . import retake  # noqa: PLC0415 — retake 가 edit 를 읽는다(순환 import 를 피한다)

    out: list[dict] = []
    for index, cut in enumerate(retake._flag_cuts(plan, gen, item, duration, variant=variant)):  # noqa: SLF001
        no = cut.get("no")
        start, end = float(cut["in"]), float(cut["out"])
        out.append({"cutId": str(cut.get("cutId") or ""), "no": no,
                    "label": f"컷 {no}" if isinstance(no, int) and not isinstance(no, bool) else f"장면 {index + 1}",
                    "start": round(start, 3), "end": round(end, 3), "seconds": round(end - start, 3)})
    return out


def _invalid_clips(clips: list, allowed: set[str], meta: dict[str, dict]) -> list[dict]:
    """저장된 문서에서 지금 쓸 수 없는 클립(재료가 아님·원본보다 김). 저장값은 바꾸지 않는다."""
    out = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        source = clip.get("sourceKey")
        if source not in allowed:
            out.append({"id": clip.get("id"), "reason": "notAllowed"})
            continue
        duration = _num((meta.get(source) or {}).get("duration"))
        end = _num(clip.get("out"))
        if duration and end is not None and end > duration + _TIME_EPS:
            out.append({"id": clip.get("id"), "reason": "outOfRange"})
    return out


def _history_item(current: dict) -> dict:
    return {key: current.get(key) for key in ("n", "status", "key", "seconds", "bytes", "hasAudio", "docVersion",
                                               "variant", "finishedAt", "error")}


def _history_out(h: dict) -> dict:
    """지난 결과 한 줄. 작업 216-fix1b: 내려받기는 zip 이 있으면 zip(downloadName 도), 옛 내보내기는 예전 그대로 mp4."""
    done = h.get("status") == "done"
    download, name = _export_download(h) if done else ("", "")
    out = {**_history_item(h), "videoUrl": _presign_video(str(h.get("key") or "")) if done else "", "downloadUrl": download}
    if done and h.get("zipKey"):
        out["downloadName"] = name
    return out


def _export_out(current: dict | None, doc_hash: str) -> dict:
    current = current if isinstance(current, dict) else {}
    if not current:
        return {"status": "none"}
    done = current.get("status") == "done"
    url = _presign_video(str(current.get("key") or "")) if done else ""
    download, download_name = _export_download(current) if done else ("", "")
    out = {
        "status": current.get("status"),
        "n": current.get("n"),
        "variant": current.get("variant"),
        "docVersion": current.get("docVersion"),
        "matchesDoc": bool(doc_hash) and current.get("docHash") == doc_hash,
        "progress": current.get("progress") or {},
        "attempts": current.get("attempts"),
        "createdAt": current.get("createdAt") or "",
        "startedAt": current.get("startedAt") or "",
        "finishedAt": current.get("finishedAt") or "",
        "seconds": current.get("seconds"),
        "bytes": current.get("bytes"),
        "hasAudio": current.get("hasAudio"),
        "error": current.get("error") or "",
        "videoUrl": url,
        "downloadUrl": download,
    }
    if current.get("zipKey"):  # 작업 216-fix1b: 내려받기 = zip(소리 없는 mp4 · 소리 mp3 · README.txt). 옛 내보내기는 키 그대로
        out["downloadName"] = download_name
        out["files"] = list(current.get("files") if isinstance(current.get("files"), list) else [])
    if current.get("stems"):  # 작업 215a: 스템을 고른 내보내기만(그 밖은 215a 전과 같은 키)
        out["stemsRequested"] = True
        if done and current.get("stemsKey"):
            out["stems"] = list(current.get("stems") if isinstance(current.get("stems"), list) else [])
            out["stemsUrl"] = _presign_attachment(str(current["stemsKey"]), f"edit-{int(current.get('n') or 0)}-stems.zip",
                                                  "application/zip")
    return out


def _variant_exports(state: dict, variant: str) -> tuple[dict | None, list[dict]]:
    """작업 189: 내보내기 상태(파일 하나, 번호 n 은 두 그림체가 함께 센다) 중 그 그림체 것만. 반환 (가장 최근 항목, 그 앞 기록).

    가장 최근 = 그 그림체 항목(current·history) 중 n 이 가장 큰 것. 기록은 history 순서 그대로, 가장 최근 항목은 뺀다.
    """
    items = [h for h in (state.get("history") or []) if isinstance(h, dict) and h.get("variant") == variant]
    current = state.get("current")
    if isinstance(current, dict) and current.get("variant") == variant:
        items.append(current)
    if not items:
        return None, []
    latest = max(items, key=lambda h: h.get("n") if isinstance(h.get("n"), int) and not isinstance(h.get("n"), bool) else -1)
    return latest, [h for h in items if h is not latest]


def edit_view(owner: str, pid: str, variant: str | None = None) -> dict:
    """GET /p/<pid>/edit. 문서(저장본, 없으면 기본 문서)·재료(길이·재생 URL)·내보내기 상태.

    작업 189: 문서는 그림체마다 따로다(_saved_docs). 그림체: variant 인자 → 저장 문서가 있으면 updatedAt 이 가장 최근인 그림체 →
    gen.selected → 웹툰 → 실사 중 재료가 있는 것. doc·saved·etag 는 고른 그림체 것이고(저장 문서가 없으면 기본 문서·saved false·
    etag "" — 첫 PUT 은 만들기), savedVariants 는 두 그림체의 저장 여부다. export·exportHistory·matchesDoc 도 고른 그림체의
    내보내기만 본다(_variant_exports).
    """
    where = "vnew.edit"
    if variant not in (None, "") and variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    plan, gen, gen_id = _load_gen(owner, pid)
    try:
        reconcile_export(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다(다음 조회가 다시 본다).
        log.warning("vnew.edit.reconcile_failed pid=%s err=%s", pid, type(exc).__name__)
    docs = _saved_docs(owner, pid, gen_id)
    retakes = _retake_state(owner, pid, gen_id)  # 지시서 116: 켬이면 다시 만든 버전(재료)·끊긴 다시 만들기 이어받기, 끔이면 None
    if retakes is not None:
        retakes = _retake_reconcile(owner, pid, gen_id, retakes)
    available = {v: _all_items(gen, v, retakes) for v in VARIANTS}
    recent = _recent_variant(docs, gen)
    if variant:
        chosen = variant
    elif recent is not None:
        chosen = recent
    else:
        order = _variant_order(gen)
        chosen = next((v for v in order if available[v]), order[0])
    items = available[chosen]
    if not items:
        raise VpocError(where, "NoMaterials",
                        f"편집할 {VARIANT_LABELS[chosen]} 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들어 주세요.", 409)
    _describe(plan, items)
    _number_takes(gen, chosen, items)  # 작업 145
    allowed = {m["sourceKey"] for m in items}
    mine = docs[chosen]
    doc, etag = mine["doc"], mine["etag"]
    saved = doc is not None
    default_items = _default_group(items)
    probe_keys = [m["sourceKey"] for m in default_items]
    if saved:
        probe_keys += [c.get("sourceKey") for c in doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, complete = _ensure_meta(owner, pid, gen_id, probe_keys, budget_s=PROBE_BUDGET_S)
    defaults = _default_clips(plan, gen, items, meta) if complete else []
    if saved:
        out_doc = doc
    else:
        out_doc = {"version": mine["version"], "variant": chosen, "updatedAt": "", "clips": defaults, "audio": []}
    state, _ = _read_json(export_state_key(owner, pid, gen_id))
    state = state or {}
    latest, older = _variant_exports(state, chosen)
    clean = [c for c in out_doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    clean = edit_tools.apply_tracks(clean, out_doc)  # 작업 215c: 트랙 숨김(내보내기에서 빼기)
    doc_hash = _doc_hash(chosen, [{**c, "in": _num(c.get("in")) or 0.0, "out": _num(c.get("out")) or 0.0} for c in clean],
                         _doc_mix(out_doc)) if saved else ""
    doc_hash = edit_text.extend_hash(doc_hash, out_doc, clean)  # 작업 215b: 효과가 없으면 그대로
    default_mids = {m["mid"] for m in default_items}
    materials = [_material_out(m, meta.get(m["sourceKey"]), default_mids) for m in items]
    for item, out in zip(items, materials):  # 작업 149: 통 테이크 재료의 컷 목록(⑦ 재료 [컷 고르기])
        if item.get("kind") == "take":
            out["cuts"] = _take_cuts(plan, gen, chosen, item, out.get("duration"))
    if retakes is not None:  # 지시서 116: 재료마다 AI로 고치기 표식(프롬프트는 넣지 않는다 — 이 보기는 읽기 권한)
        from . import retake  # noqa: PLC0415

        for item, out in zip(items, materials):
            out["retake"] = retake.material_flag(chosen, item, gen, retakes, plan=plan, duration=out.get("duration"))  # 지시서 119: 컷 고르기
        # 지시서 116 보완 10: 소리 없는 버전은 그 칸 v1 소리를 쓴다(내보내기와 같은 _audio_sources). 새로 재지 않고 길이 캐시만 본다.
        # 작업 193 fix1: result 버전은 늘 fromKey 소리(원천에 소리가 없으면 hasAudio false), 타임라인 클립의 옛 길 버전은 무음 판정(필름 캐시).
        used = {c.get("sourceKey") for c in out_doc["clips"] if isinstance(c, dict)}
        r_meta = _retake_meta(owner, pid, gen_id, items, meta, probe=False, silent_keys=used)
        audio_from = _audio_sources(gen, items, r_meta)
        mutes = _result_mutes(items, r_meta, audio_from)
        for item, out in zip(items, materials):
            if item["sourceKey"] in audio_from:
                out["audioFrom"] = dict(audio_from[item["sourceKey"]])
            elif item["sourceKey"] in mutes:
                out["hasAudio"] = False
    view = {
        "enabled": True,
        "genId": gen_id,
        "variant": chosen,
        "variants": [
            {"key": v, "label": VARIANT_LABELS[v], "available": bool(available[v]), "materials": len(available[v])}
            for v in VARIANTS
        ],
        "selected": gen.get("selected") if gen.get("selected") in VARIANTS else None,
        "doc": out_doc,
        "saved": saved,
        "etag": etag,
        "savedVariants": {v: docs[v]["doc"] is not None for v in VARIANTS},  # 작업 189: 탭 표시(그림체마다 저장 문서가 있는가)
        "defaultClips": defaults,
        "invalidClips": _invalid_clips(out_doc["clips"], allowed, meta) if saved else [],
        "probing": not complete,
        "totalSeconds": _total(out_doc["clips"]),
        "materials": materials,
        "limits": {"maxClips": MAX_CLIPS, "maxTotalSeconds": MAX_TOTAL_S, "minClipSeconds": MIN_CLIP_S},
        "export": _export_out(latest, doc_hash),
        "exportHistory": [_history_out(h) for h in older],
    }
    if retakes is not None:
        view["retake"] = {"enabled": True}
    view["textFx"] = edit_text.view_info()  # 작업 215b: 자막 굽기 가능 · 글꼴 · 한도
    return view


def _retake_reconcile(owner: str, pid: str, gen_id: str, retakes: dict) -> dict:
    """끊긴 다시 만들기·초안 이어받기(retake.reconcile_state, 지시서 116). 보기는 실패하지 않는다(그대로 돌려준다)."""
    from . import retake  # noqa: PLC0415

    try:
        return retake.reconcile_state(owner, pid, gen_id, retakes)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.edit.retake_reconcile_failed pid=%s err=%s", pid, type(exc).__name__)
        return retakes


def _validated_clips(owner: str, pid: str, variant: str, raw: list, where: str) -> tuple[dict, str, list[dict]]:
    """저장(PUT)과 빈틈 다듬기(작업 185)가 함께 쓰는 클립 검증. 반환 (gen, gen_id, 정리된 클립).

    그 그림체 재료(다시 만든 버전 포함)만 허용하고, 쓰인 원본 길이를 재(캐시 먼저) _clean_clips 규칙으로 검사한다. 틀리면 400·409.
    """
    _plan, gen, gen_id = _load_gen(owner, pid)
    allowed = {m["sourceKey"] for m in _all_items(gen, variant, _retake_state(owner, pid, gen_id))}
    referenced = [c.get("sourceKey") for c in raw if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, _ = _ensure_meta(owner, pid, gen_id, referenced)
    durations = {key: (meta.get(key) or {}).get("duration") for key in allowed}
    return gen, gen_id, _clean_clips(raw, allowed, durations, where)


def _clean_mix(owner: str, pid: str, payload: dict, where: str) -> tuple[list[dict], dict]:
    """작업 215a: 바디 audioTracks · a1 검사(edit_audio). 트랙 아이템은 이 프로젝트 내 소리(_audio_assets)만. 반환 (트랙, A1)."""
    raw_tracks = payload.get("audioTracks")
    a1 = edit_audio.clean_a1(payload.get("a1"), where)
    if raw_tracks is None or raw_tracks == []:
        return [], a1
    assets = _audio_assets(owner, pid) if edit_audio.assets_used(raw_tracks if isinstance(raw_tracks, list) else []) else {}
    return edit_audio.clean_tracks(raw_tracks, assets, MAX_TOTAL_S, where), a1


def save_doc(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/edit. 바디 {etag | version, variant, clips, audio?}. 반환 {doc, etag, saved, totalSeconds}.

    기준: etag(글자, 첫 저장은 "") 또는 version(정수, 첫 저장은 0). 지금 저장본과 다르면 409 EditChanged. 쓰기는 S3 조건부
    쓰기(첫 저장은 If-None-Match, 그 뒤 If-Match)라 동시에 같은 기준으로 저장하면 하나만 성공하고 나머지는 409 다.
    작업 189: 그림체(variant)마다 따로 저장한다(edit-<variant>.json). 기준·충돌은 그 그림체 문서끼리만 본다(_saved_docs 규칙).
    """
    where = "vnew.edit.save"
    payload = payload if isinstance(payload, dict) else {}
    has_etag = "etag" in payload
    etag = payload.get("etag")
    version = payload.get("version")
    if has_etag and not isinstance(etag, str):
        raise VpocError(where, "NoEtag", "저장 기준(etag 또는 version)이 올바르지 않습니다.", 400)
    if not has_etag and (not isinstance(version, int) or isinstance(version, bool) or version < 0):
        raise VpocError(where, "NoEtag", "저장 기준(etag 또는 version)이 필요합니다.", 400)
    variant = payload.get("variant")
    if variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    audio = payload.get("audio", [])
    if not isinstance(audio, list):
        raise VpocError(where, "BadAudio", "사운드 트랙(audio)은 목록이어야 합니다.", 400)
    if audio:
        raise VpocError(where, "AudioTrackUnsupported", "사운드 트랙은 아직 넣을 수 없습니다(⑥ 사운드 결과가 없습니다).", 400)
    raw = _check_clip_list(payload.get("clips"), where)
    _gen, gen_id, clips = _validated_clips(owner, pid, variant, raw, where)
    # 작업 215a: 오디오 트랙 · A1 설정. 둘 다 없으면(옛 화면) 215a 전과 같은 문서다. 아이템 소리는 이 프로젝트 내 소리만.
    tracks, a1 = _clean_mix(owner, pid, payload, where)
    # 작업 189: 그 그림체 파일(edit-<variant>.json)에만 쓴다. 다른 그림체 문서와 edit.json 은 건드리지 않는다.
    # 기준(화면이 받은 것, _saved_docs 규칙): 파일이 있으면 그 파일, 없으면 edit.json 이 같은 그림체일 때 edit.json, 아니면 없음("" · 0).
    key = doc_key(owner, pid, gen_id, variant)
    current, current_etag = _read_json(key)
    exists = current is not None or bool(current_etag)
    base_etag, base_version = current_etag, _doc_version(current)
    if not exists:
        legacy, legacy_etag = _read_json(legacy_doc_key(owner, pid, gen_id))
        base_etag, base_version = (legacy_etag, _doc_version(legacy)) if _valid_doc(legacy, variant) else ("", 0)
    if has_etag:
        if etag != base_etag:  # 작업 190: 화면이 그림체마다 자기 etag 를 보낸다(다른 그림체 문서의 etag 는 받지 않는다)
            raise _changed(where)
    elif version != base_version:
        raise _changed(where)
    doc = {"version": base_version + 1, "variant": variant, "updatedAt": _now_ms(), "clips": clips, "audio": []}
    if tracks:
        doc["audioTracks"] = tracks
    if not edit_audio.a1_is_default(a1):
        doc["a1"] = a1
    doc.update(edit_text.clean_doc(payload, where))  # 작업 215b: captions · titles · fadeIn · fadeOut · captionBurnIn(있을 때만)
    doc.update(edit_tools.clean_doc_extras(payload, where))  # 작업 215c
    try:
        new_etag = STORE.write_json(key, doc, etag=current_etag if exists else "", create_only=not exists)
    except VpocError as exc:
        if exc.kind == "PreconditionFailed":
            raise _changed(where) from exc
        raise
    log.info("vnew.edit.saved pid=%s gen=%s version=%d variant=%s clips=%d seconds=%.2f", pid, gen_id, doc["version"],
             variant, len(clips), _total(clips))
    return {"doc": doc, "etag": str(new_etag or ""), "saved": True, "totalSeconds": _total(clips)}


# ================================================================ 필름스트립 · 파형


def _film_ok(data: dict | None, source: str) -> bool:
    return (
        isinstance(data, dict)
        and data.get("sourceKey") == source
        and isinstance(data.get("strip"), dict)
        and isinstance(data.get("peaks"), dict)
        and isinstance((data.get("peaks") or {}).get("values"), list)
    )


def _make_film(owner: str, pid: str, gen_id: str, mid: str, source: str) -> dict:
    started = time.monotonic()
    meta, _ = _ensure_meta(owner, pid, gen_id, [source])
    info = meta.get(source) or {}
    duration = _num(info.get("duration")) or 0.0
    if duration <= 0:
        raise VpocError("vnew.edit.media", "SourceUnreadable", "원본 영상을 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요.", 409)
    interval = float(max(1, math.ceil(duration / STRIP_MAX_TILES)))
    tiles = max(1, min(STRIP_MAX_TILES, math.ceil(duration / interval - 1e-9)))
    with _MEDIA_SEM:
        with tempfile.TemporaryDirectory(prefix="vnew-edit-film-") as workdir:
            path = os.path.join(workdir, "source.mp4")
            _download(source, path, bucket=vpoc_settings.VIDEO_BUCKET)
            film = _film_file(path, workdir, interval=interval, tiles=tiles, has_audio=bool(info.get("hasAudio")))
            with open(film["stripPath"], "rb") as handle:
                STORE.put_bytes(strip_key(owner, pid, gen_id, mid), handle.read(), "image/jpeg")
    data = {
        "v": 1,
        "sourceKey": source,
        "duration": duration,
        "hasAudio": bool(info.get("hasAudio")),
        "strip": {"key": strip_key(owner, pid, gen_id, mid), "tiles": tiles, "interval": interval,
                  "tileWidth": int(film["tileWidth"]), "tileHeight": int(film["tileHeight"])},
        "peaks": {"perSecond": PEAKS_PER_SECOND, "values": [float(v) for v in film.get("peaks") or []]},
        "createdAt": now_iso(),
    }
    STORE.write_json(film_key(owner, pid, gen_id, mid), data)
    log.info("vnew.edit.film pid=%s mid=%s tiles=%d peaks=%d ms=%d", pid, mid, tiles, len(data["peaks"]["values"]),
             int((time.monotonic() - started) * 1000))
    return data


def media_view(owner: str, pid: str, mid: str) -> dict:
    """GET /p/<pid>/edit/media/<mid>. 재료 하나의 필름스트립(presigned JPEG + 칸 크기)·파형 피크.

    처음 요청 때 만들고(내려받기 1번 + ffmpeg 2번) S3 에 둔다. 그 뒤에는 저장본만 읽는다(generated false).
    mid 는 지금 gen 의 재료(두 그림체 어느 쪽이든)여야 한다. 아니면 404.
    """
    where = "vnew.edit.media"
    if not isinstance(mid, str) or not _MID_RE.match(mid):
        raise VpocError(where, "MediaNotFound", "편집 재료를 찾을 수 없습니다.", 404)
    _plan, gen, gen_id = _load_gen(owner, pid)
    retakes = _retake_state(owner, pid, gen_id)  # 지시서 116: 다시 만든 버전도 재료(끔이면 None)
    source = next((m["sourceKey"] for v in VARIANTS for m in _all_items(gen, v, retakes) if m["mid"] == mid), "")
    if not source:
        raise VpocError(where, "MediaNotFound", "편집 재료를 찾을 수 없습니다.", 404)
    data, _ = _read_json(film_key(owner, pid, gen_id, mid))
    generated = False
    if not _film_ok(data, source):
        with _lock(f"film:{owner}/{pid}/{gen_id}/{mid}"):
            data, _ = _read_json(film_key(owner, pid, gen_id, mid))
            if not _film_ok(data, source):
                data = _make_film(owner, pid, gen_id, mid, source)
                generated = True
    strip = data["strip"]
    return {
        "mid": mid,
        "duration": data.get("duration"),
        "hasAudio": bool(data.get("hasAudio")),
        "strip": {
            "url": _presign(str(strip.get("key") or ""), content_type="image/jpeg"),
            "tiles": strip.get("tiles"),
            "interval": strip.get("interval"),
            "tileWidth": strip.get("tileWidth"),
            "tileHeight": strip.get("tileHeight"),
        },
        "peaks": {"perSecond": data["peaks"].get("perSecond"), "values": data["peaks"]["values"]},
        # 작업 185: 소리 구간(빈틈 다듬기). 저장하지 않고 읽을 때마다 피크에서 센다(옛 필름 캐시도 그대로 된다, ffmpeg 0).
        "soundRanges": _film_ranges(data),
        "generated": generated,
    }


# ================================================================ 작업 215a: 내 소리(사용자가 올린 배경음 · 효과음)
#
# 파일은 영상 버킷 audio_file_key, 목록(index.json)과 파형 피크(-peaks.json)는 메인 버킷 audio_root 아래. 프로젝트 소유자만(라우트가
# 쓰기 권한으로 부른다). 이 파일은 어떤 AI 모델에도 보내지 않는다 — ffmpeg 로 길이 · 파형만 재고, 내보내기 섞기에만 쓴다.


def _audio_assets(owner: str, pid: str) -> dict[str, dict]:
    data, _ = _read_json(audio_index_key(owner, pid))
    items = (data or {}).get("items")
    return {k: v for k, v in items.items() if edit_audio.valid_asset_id(k) and isinstance(v, dict)} \
        if isinstance(items, dict) else {}


def _asset_out(entry: dict, used: set[str]) -> dict:
    return {"assetId": entry.get("assetId"), "name": str(entry.get("name") or ""), "ext": entry.get("ext"),
            "bytes": int(entry.get("bytes") or 0), "duration": entry.get("duration"), "createdAt": entry.get("createdAt") or "",
            "used": entry.get("assetId") in used}


def _assets_in_use(owner: str, pid: str) -> set[str]:
    """지금 gen 의 저장된 편집 문서(두 그림체)가 쓰는 내 소리 id. gen 이 없으면 빈 집합."""
    try:
        _plan, _gen, gen_id = _load_gen(owner, pid)
    except VpocError:
        return set()
    used: set[str] = set()
    for item in _saved_docs(owner, pid, gen_id).values():
        used |= edit_audio.assets_used((item.get("doc") or {}).get("audioTracks"))
    return used


def audio_assets_view(owner: str, pid: str) -> dict:
    """GET /p/<pid>/edit/audio-assets. 내 소리 목록(새것부터, 피크 없음) · 한도."""
    used = _assets_in_use(owner, pid)
    items = sorted(_audio_assets(owner, pid).values(), key=lambda e: str(e.get("createdAt") or ""), reverse=True)
    return {"assets": [_asset_out(e, used) for e in items],
            "limits": {"maxBytes": edit_audio.MAX_ASSET_BYTES, "maxSeconds": edit_audio.MAX_ASSET_S, "maxAssets": edit_audio.MAX_ASSETS,
                       "maxTracks": edit_audio.MAX_TRACKS, "types": sorted(edit_audio.ASSET_TYPES)}}


def add_audio_asset(owner: str, pid: str, filename, path: str) -> dict:
    """POST /p/<pid>/edit/audio-assets(라우트가 올린 파일을 path 에 받아 둔다). 형식 · 크기 · 길이 검사 → 피크 → 영상 버킷.

    반환 {asset, assets(목록)}. 틀리면 400 · 413(edit_audio.check_upload · check_probe), 목록이 가득이면 409 TooManyAudioAssets.
    """
    where = "vnew.edit.audio"
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    with open(path, "rb") as handle:
        head = handle.read(16)
    ext, content_type, label = edit_audio.check_upload(filename, size, head, where)
    if len(_audio_assets(owner, pid)) >= edit_audio.MAX_ASSETS:
        raise VpocError(where, "TooManyAudioAssets", f"내 소리는 {edit_audio.MAX_ASSETS}개까지 둘 수 있습니다. 안 쓰는 소리를 지워 주세요.", 409)
    started = time.monotonic()
    with _MEDIA_SEM:
        duration = edit_audio.check_probe(_probe_audio(path), where)
        with tempfile.TemporaryDirectory(prefix="vnew-edit-audio-") as workdir:
            try:
                peaks = _audio_peaks_file(path, workdir)
            except stitcher.StitchError as exc:
                log.warning("vnew.edit.audio_peaks_failed pid=%s err=%s", pid, error_gist(exc, 120))
                peaks = []
    asset_id = "a" + secrets.token_hex(8)
    _upload(path, audio_file_key(owner, pid, asset_id, ext), bucket=vpoc_settings.VIDEO_BUCKET, content_type=content_type)
    STORE.write_json(audio_peaks_key(owner, pid, asset_id),
                     {"v": 1, "assetId": asset_id, "perSecond": PEAKS_PER_SECOND, "values": [float(v) for v in peaks]})
    entry = {"assetId": asset_id, "name": label, "ext": ext, "contentType": content_type, "bytes": size, "duration": duration,
             "key": audio_file_key(owner, pid, asset_id, ext), "createdAt": now_iso()}

    def add(data):
        data = data if isinstance(data, dict) else {}
        items = data.get("items") if isinstance(data.get("items"), dict) else {}
        items[asset_id] = entry
        return {"v": 1, "items": items}

    _mutate_json(audio_index_key(owner, pid), add)
    log.info("vnew.edit.audio_added pid=%s asset=%s ext=%s bytes=%d seconds=%.2f peaks=%d ms=%d", pid, asset_id, ext, size,
             duration, len(peaks), int((time.monotonic() - started) * 1000))
    return {"asset": _asset_out(entry, set()), **audio_assets_view(owner, pid)}


def _asset_or_404(owner: str, pid: str, asset_id, where: str) -> dict:
    entry = _audio_assets(owner, pid).get(asset_id) if edit_audio.valid_asset_id(asset_id) else None
    if not isinstance(entry, dict):
        raise VpocError(where, "AudioAssetNotFound", "내 소리를 찾을 수 없습니다.", 404)
    return entry


def audio_asset_view(owner: str, pid: str, asset_id) -> dict:
    """GET /p/<pid>/edit/audio-assets/<aid>. 정보 · 파형 피크."""
    entry = _asset_or_404(owner, pid, asset_id, "vnew.edit.audio")
    data, _ = _read_json(audio_peaks_key(owner, pid, asset_id))
    values = (data or {}).get("values") if isinstance((data or {}).get("values"), list) else []
    return {"asset": _asset_out(entry, _assets_in_use(owner, pid)), "peaks": {"perSecond": PEAKS_PER_SECOND, "values": values}}


def audio_asset_file(owner: str, pid: str, asset_id) -> tuple[object, str, int]:
    """GET /p/<pid>/edit/audio-assets/<aid>/file(미리듣기 WebAudio — 같은 출처라 버킷 CORS 가 필요 없다). 반환 (바이트 또는 조각
    반복자, Content-Type, 크기). 실제 저장소면 S3 를 1MB 조각으로 흘려 보낸다(통째로 메모리에 올리지 않는다)."""
    entry = _asset_or_404(owner, pid, asset_id, "vnew.edit.audio")
    key = str(entry.get("key") or "")
    ctype = str(entry.get("contentType") or "application/octet-stream")
    store = STORE
    if isinstance(store, vpoc_store.Store):
        target = vpoc_settings.VIDEO_BUCKET or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        body = vpoc_store.s3_client(region).get_object(Bucket=target, Key=key)["Body"]
        return body.iter_chunks(chunk_size=1 << 20), ctype, int(entry.get("bytes") or 0)
    data = store.get_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
    return data, ctype, len(data)


def delete_audio_asset(owner: str, pid: str, asset_id) -> dict:
    """DELETE /p/<pid>/edit/audio-assets/<aid>. 저장된 편집 문서가 쓰고 있으면 409 AudioAssetInUse(타임라인에서 먼저 빼야 한다)."""
    where = "vnew.edit.audio"
    entry = _asset_or_404(owner, pid, asset_id, where)
    if asset_id in _assets_in_use(owner, pid):
        raise VpocError(where, "AudioAssetInUse", "타임라인에서 쓰는 소리는 지울 수 없습니다. 먼저 타임라인에서 빼 주세요.", 409)

    def drop(data):
        items = (data or {}).get("items")
        if not isinstance(items, dict) or asset_id not in items:
            return None
        items.pop(asset_id, None)
        return {"v": 1, "items": items}

    _mutate_json(audio_index_key(owner, pid), drop)
    for key, bucket in ((str(entry.get("key") or ""), vpoc_settings.VIDEO_BUCKET), (audio_peaks_key(owner, pid, asset_id), "")):
        try:
            if key:
                _delete_object(key, bucket=bucket)
        except Exception as exc:  # noqa: BLE001 — 목록에서는 이미 빠졌다(다시 보이지 않는다).
            log.warning("vnew.edit.audio_delete_failed pid=%s asset=%s err=%s", pid, asset_id, type(exc).__name__)
    log.info("vnew.edit.audio_deleted pid=%s asset=%s", pid, asset_id)
    return audio_assets_view(owner, pid)


def stems_view(owner: str, pid: str, variant=None) -> dict:
    """GET /p/<pid>/edit/stems(?variant=). 그 그림체 가장 최근 내보내기의 스템 zip {n, stems, downloadUrl}. 없으면 404 NoStems."""
    where = "vnew.edit.stems"
    if variant not in (None, "") and variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    _plan, gen, gen_id = _load_gen(owner, pid)
    state, _ = _read_json(export_state_key(owner, pid, gen_id))
    chosen = variant or _recent_variant(_saved_docs(owner, pid, gen_id), gen) or "webtoon"
    latest, _older = _variant_exports(state or {}, chosen)
    if not latest or latest.get("status") != "done" or not latest.get("stemsKey"):
        raise VpocError(where, "NoStems", "스템이 없습니다. [스템도 함께]를 켜고 내보내 주세요.", 404)
    return {"n": latest.get("n"), "variant": chosen, "stems": list(latest.get("stems") or []),
            "downloadUrl": _presign_attachment(str(latest["stemsKey"]), f"edit-{int(latest.get('n') or 0)}-stems.zip",
                                               "application/zip")}


def _separated_audio(owner: str, pid: str, source_key: str, duration=None) -> dict | None:
    """작업 217: 그 원본의 섞인 소리 분리 결과 {voiceKey, bgKey}([분리된 목소리 쓰기] 켬일 때만, bgKey 는 배경을 A2 에 끔이면 "").
    없으면 None. 내보내기는 대사(A1)에 voiceKey, 배경음(A2)에 bgKey 를 쓴다(audio_separate.export_sources · export_mix_doc)."""
    from . import audio_separate  # noqa: PLC0415 — audio_separate 가 이 모듈을 import 한다(순환 피함)

    return audio_separate.separated_audio(owner, pid, source_key, duration)


def _presign_attachment(key: str, filename: str, content_type: str) -> str:
    """작업 215a: 영상 버킷 presigned 내려받기(attachment). filename 은 서버가 만든 ASCII 이름만. 검사용 저장소는 presign."""
    if not key:
        return ""
    store = STORE
    if not isinstance(store, vpoc_store.Store):
        return _presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type=content_type)
    try:
        target = vpoc_settings.VIDEO_BUCKET or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        return vpoc_store.s3_client(region).generate_presigned_url(
            "get_object",
            Params={"Bucket": target, "Key": key, "ResponseContentType": content_type,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"'},
            ExpiresIn=URL_EXPIRES_S,
        )
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다.
        log.warning("vnew.edit.presign_failed err=%s", type(exc).__name__)
        return ""


# ================================================================ 작업 185: 빈틈 다듬기(소리 구간 · 제안)


def sound_ranges(peaks, per_second: int = PEAKS_PER_SECOND) -> list[list[float]]:
    """파형 피크(1초 per_second 개, 0~1) → 또렷한 소리 구간 [[시작, 끝], …](원본 초, SOUND_ROUND_S 반올림). 순수 함수.

    칸 i 는 [i/per_second, (i+1)/per_second). 소리 칸 = 피크 ≥ max(SOUND_MIN_PEAK, 바닥 × SOUND_FLOOR_RATIO, 최대 × SOUND_MAX_RATIO)
    (바닥 = 아래 SOUND_FLOOR_PERCENTILE 값). 이어진 소리 칸을 구간으로, SOUND_MERGE_GAP_S 보다 짧은 쉼은 잇고, 이은 뒤
    SOUND_MIN_RANGE_S 보다 짧은 구간은 버린다. 피크가 없거나·꼴이 틀리거나·문턱을 넘는 칸이 없으면 [].
    """
    if not isinstance(per_second, int) or isinstance(per_second, bool) or per_second < 1 or not isinstance(peaks, list):
        return []
    values = []
    for peak in peaks:
        value = _num(peak)
        values.append(max(0.0, min(1.0, value)) if value is not None else 0.0)
    if not values:
        return []
    ordered = sorted(values)
    floor = ordered[min(len(ordered) - 1, int(len(ordered) * SOUND_FLOOR_PERCENTILE))]
    loudest = ordered[-1]
    threshold = max(SOUND_MIN_PEAK, floor * SOUND_FLOOR_RATIO, loudest * SOUND_MAX_RATIO)
    if loudest < threshold:
        return []
    runs: list[list[int]] = []  # [첫 칸, 끝 칸 + 1]
    for index, value in enumerate(values):
        if value < threshold:
            continue
        if runs and index - runs[-1][1] < SOUND_MERGE_GAP_S * per_second - 1e-9:
            runs[-1][1] = index + 1
        else:
            runs.append([index, index + 1])
    step = SOUND_ROUND_S
    out: list[list[float]] = []
    for first, last in runs:
        if (last - first) / per_second < SOUND_MIN_RANGE_S - 1e-9:
            continue
        start = round(round(first / per_second / step) * step, 2)
        end = round(round(last / per_second / step) * step, 2)
        if end > start:
            out.append([start, end])
    return out


def _film_ranges(data: dict | None) -> list[list[float]]:
    """작업 185: 필름 캐시(_make_film 저장본) 하나의 소리 구간. 소리 없는 원본·피크 없음이면 []."""
    if not isinstance(data, dict) or not data.get("hasAudio"):
        return []
    peaks = data.get("peaks") if isinstance(data.get("peaks"), dict) else {}
    rate = peaks.get("perSecond")
    return sound_ranges(peaks.get("values"), rate if isinstance(rate, int) and not isinstance(rate, bool) else PEAKS_PER_SECOND)


def _fit_dissolve(seconds: float, first: dict, second: dict) -> float:
    """작업 185: 디졸브 초를 두 클립(속도 반영) 중 짧은 쪽의 45% 안으로 내린다(허용 값 중 가장 큰 것, 없으면 0 = 컷)."""
    limit = DISSOLVE_MAX_SHARE * min(_clip_seconds(first), _clip_seconds(second)) + 1e-9
    if seconds <= limit:
        return seconds
    return max((d for d in DISSOLVE_SECONDS if d <= limit), default=0.0)


def tighten_proposal(clips: list[dict], ranges_by_media: dict, *, keep_head: float = TIGHTEN_KEEP_HEAD_S,
                     keep_tail: float = TIGHTEN_KEEP_TAIL_S, min_clip: float = TIGHTEN_MIN_CLIP_S) -> dict:
    """작업 185: 클립마다 소리 구간 밖의 앞뒤 빈 시간을 줄인 in·out 제안(순수 함수, 저장하지 않는다).

    clips 는 정리된 클립(_clean_clips 꼴), ranges_by_media 는 {sourceKey: 소리 구간 목록(sound_ranges)}. 키가 없으면 그 원본의 필름이
    아직 없다는 뜻이다(그 클립은 그대로, skipped "noFilm"). 클립마다(화면 원본 [in, out] 과 겹치는 소리 구간만 본다):
      새 in = max(in, 첫 소리 시작 − keep_head × 속도), 새 out = min(out, 마지막 소리 끝 + keep_tail × 속도). 늘리지 않는다.
      타임라인 길이(속도 반영)가 min_clip 보다 짧아지면 앞뒤를 같은 만큼 덜 줄인다(한쪽이 0 이면 남은 만큼 다른 쪽). 이미 min_clip
      보다 짧은 클립은 그대로("short").
      대사 타이밍(audioSlip)이 있으면 화면에서 들리는 자리(원본 + slip × 속도)로 소리 구간을 옮겨 본다.
      소리 끈 클립("muted")·안에 소리가 없는 클립("noSound" — 그림만 넣은 컷일 수 있다)은 그대로.
    줄인 뒤 디졸브는 45% 규칙을 다시 보고 넘으면 허용 값 중 가장 큰 것으로 내리거나 뺀다(perClip transition {from, to}).
    앞당김·이어 감(J/L)은 클립 길이에 묶이지 않으므로 그대로 둔다.
    반환 {clips(새 사본), savedSeconds(줄인 타임라인 초 합), totalSeconds(줄인 뒤 길이), perClip [{id, savedHead, savedTail, skipped}]}.
    """
    out_clips: list[dict] = []
    per_clip: list[dict] = []
    for clip in clips:
        item = copy.deepcopy(clip)
        out_clips.append(item)
        info = {"id": clip.get("id"), "savedHead": 0.0, "savedTail": 0.0, "skipped": ""}
        per_clip.append(info)
        speed = _speed_of(clip)
        cur_in, cur_out = float(clip["in"]), float(clip["out"])
        if clip.get("muted"):
            info["skipped"] = "muted"
            continue
        ranges = ranges_by_media.get(clip.get("sourceKey"))
        if ranges is None:
            info["skipped"] = "noFilm"
            continue
        shift = _slip_of(clip) * speed  # 들리는 자리 = 원본 소리 + slip × 속도
        inside = [(float(a) + shift, float(b) + shift) for a, b in ranges
                  if float(b) + shift > cur_in + _TIME_EPS and float(a) + shift < cur_out - _TIME_EPS]
        if not inside:
            info["skipped"] = "noSound"
            continue
        new_in = max(cur_in, inside[0][0] - keep_head * speed)
        new_out = min(cur_out, inside[-1][1] + keep_tail * speed)
        head, tail = max(0.0, new_in - cur_in), max(0.0, cur_out - new_out)
        allowed = (cur_out - cur_in) - min_clip * speed  # 줄일 수 있는 원본 초
        if allowed <= _TIME_EPS:
            info["skipped"] = "short" if head + tail > _TIME_EPS else ""
            continue
        if head + tail > allowed:
            give = (head + tail - allowed) / 2  # 앞뒤를 같은 만큼 덜 줄인다
            head, tail = head - give, tail - give
            if head < 0:
                head, tail = 0.0, tail + head
            if tail < 0:
                head, tail = head + tail, 0.0
        # 밀리초로 내림(덜 줄이는 쪽) — 반올림이 min_clip 아래로 내려가지 않게.
        head, tail = math.floor(head * 1000 + 1e-6) / 1000, math.floor(tail * 1000 + 1e-6) / 1000
        item["in"] = round(cur_in + head, 3)
        item["out"] = round(cur_out - tail, 3)
        info["savedHead"] = round((item["in"] - cur_in) / speed, 3)
        info["savedTail"] = round((cur_out - item["out"]) / speed, 3)
    for index, (item, after) in enumerate(zip(out_clips, out_clips[1:])):
        seconds = _dissolve_of(item)
        if not seconds:
            continue
        fitted = _fit_dissolve(seconds, item, after)
        if fitted == seconds:
            continue
        if fitted:
            item["transition"] = {"type": "dissolve", "seconds": fitted}
        else:
            item.pop("transition", None)
        per_clip[index]["transition"] = {"from": seconds, "to": fitted}
    saved = round(sum(p["savedHead"] + p["savedTail"] for p in per_clip), 3)
    return {"clips": out_clips, "savedSeconds": saved, "totalSeconds": _total(out_clips), "perClip": per_clip}


def _keep_value(raw, default: float, name: str, where: str) -> float:
    """작업 185: keepHead·keepTail 한 값. 없거나 null 이면 기본값, JSON 숫자면 0~TIGHTEN_KEEP_MAX_S 로 묶는다. 그 밖은 400."""
    if raw is None:
        return default
    value = _num(raw)
    if value is None:
        raise VpocError(where, "BadTightenKeep", f"남길 여유({name})는 숫자(초)여야 합니다.", 400)
    return round(max(0.0, min(TIGHTEN_KEEP_MAX_S, value)), 3)


def tighten_view(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/edit/tighten(작업 185). 바디 {variant, clips, keepHead?, keepTail?}. 저장하지 않는다.

    클립은 저장(PUT)과 같은 규칙(_validated_clips)으로 검사한다. 소리 구간은 이미 만든 필름 캐시(media_view 저장본)의 피크에서만
    센다 — 이 요청은 원본을 내려받거나 ffmpeg 를 돌리지 않는다(요청 시간이 재료 수에 묶이지 않고 내보내기·필름 만들기 세마포어를
    잡지 않는다). 필름이 아직 없는 원본의 클립은 그대로 두고 perClip skipped "noFilm" 으로 알린다(화면이 재료 파형을 받은 뒤 다시 부른다).
    반환 {clips, savedSeconds, totalSeconds, perClip, keepHead, keepTail}.
    """
    where = "vnew.edit.tighten"
    payload = payload if isinstance(payload, dict) else {}
    variant = payload.get("variant")
    if variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    keep_head = _keep_value(payload.get("keepHead"), TIGHTEN_KEEP_HEAD_S, "keepHead", where)
    keep_tail = _keep_value(payload.get("keepTail"), TIGHTEN_KEEP_TAIL_S, "keepTail", where)
    raw = _check_clip_list(payload.get("clips"), where)
    _gen, gen_id, clips = _validated_clips(owner, pid, variant, raw, where)
    ranges: dict[str, list] = {}
    for source in dict.fromkeys(c["sourceKey"] for c in clips if not c.get("muted")):
        data, _ = _read_json(film_key(owner, pid, gen_id, media_id(source)))
        if _film_ok(data, source):
            ranges[source] = _film_ranges(data)
    result = tighten_proposal(clips, ranges, keep_head=keep_head, keep_tail=keep_tail)
    log.info("vnew.edit.tighten pid=%s gen=%s clips=%d trimmed=%d no_film=%d saved=%.2f", pid, gen_id, len(clips),
             sum(1 for p in result["perClip"] if p["savedHead"] or p["savedTail"]),
             sum(1 for p in result["perClip"] if p["skipped"] == "noFilm"), result["savedSeconds"])
    return {**result, "keepHead": keep_head, "keepTail": keep_tail}


# ================================================================ 내보내기


def _empty_state() -> dict:
    return {"v": 1, "n": 0, "current": None, "history": []}


def start_export(owner: str, pid: str, etag, variant=None, stems=False) -> dict:
    """POST /p/<pid>/edit/export. 저장된 문서(etag 가 지금 저장본과 같아야 한다)를 내보내기 시작한다.

    같은 문서(_doc_hash)로 진행 중(queued·running)인 내보내기가 있으면 새로 시작하지 않고 그것을 준다(started false).
    다른 문서로 진행 중이면 409 ExportRunning. 문서 클립은 시작 때 사본(snapshot)으로 남겨, 그 뒤 저장이 결과를 바꾸지 않는다.
    반환 {started, export}.
    작업 189: variant(바디)의 문서를 내보낸다. 없으면(189 전 화면) etag 가 같은 그림체 문서, 그것도 없으면 가장 최근에 저장한
    그림체(_recent_variant). 상태 파일은 하나·번호 n 은 두 그림체가 함께 센다. gen 하나에 한 번에 하나만 — 다른 그림체가
    내보내는 중이어도 409 ExportRunning(그 그림체 이름을 말한다).
    """
    where = "vnew.edit.export"
    if variant not in (None, "") and variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    _plan, gen, gen_id = _load_gen(owner, pid)
    docs = _saved_docs(owner, pid, gen_id)
    if not variant:
        matched = [v for v in VARIANTS if docs[v]["doc"] is not None and isinstance(etag, str) and etag and docs[v]["etag"] == etag]
        variant = matched[0] if matched else _recent_variant(docs, gen)
    if variant is None or docs[variant]["doc"] is None:
        raise VpocError(where, "EditNotSaved", "편집 문서를 먼저 저장해 주세요.", 409)
    doc, doc_etag = docs[variant]["doc"], docs[variant]["etag"]
    if not isinstance(etag, str) or etag != doc_etag:
        raise _changed(where)
    allowed = {m["sourceKey"] for m in _all_items(gen, variant, _retake_state(owner, pid, gen_id))}
    referenced = [c.get("sourceKey") for c in doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, _ = _ensure_meta(owner, pid, gen_id, referenced)
    clips = _clean_clips(doc["clips"], allowed, {k: (meta.get(k) or {}).get("duration") for k in allowed}, where)
    clips = edit_tools.apply_tracks(clips, doc)  # 작업 215c
    if not clips:
        raise VpocError(where, "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
    stems = stems is True
    # 작업 215a: 오디오 트랙 · A1 설정(저장 때와 같은 검사 — 그 뒤 지운 소리면 400). 트랙 · 스템이 없으면 215a 전과 같은 상태 · 해시.
    a1 = edit_audio.clean_a1(doc.get("a1"), where)
    raw_tracks = doc.get("audioTracks") or []
    assets = _audio_assets(owner, pid) if edit_audio.assets_used(raw_tracks) else {}
    tracks = edit_audio.clean_tracks(raw_tracks, assets, MAX_TOTAL_S, where) if raw_tracks else []
    doc_hash = _doc_hash(variant, clips, {"tracks": tracks, "a1": a1})
    audio_snapshot = None
    if edit_audio.mix_active(tracks, a1) or stems:
        audio_snapshot = {"tracks": tracks, "a1": a1,
                          "assets": {aid: {"key": assets[aid].get("key"), "ext": assets[aid].get("ext"),
                                           "duration": assets[aid].get("duration")} for aid in edit_audio.assets_used(tracks)}}
    doc_hash = edit_text.extend_hash(doc_hash, doc, clips)  # 작업 215b: 효과가 없으면 그대로
    edit_text.check_export(doc, where)  # 작업 215b: 구울 자막·타이틀이 있는데 글꼴이 없으면 409
    reconcile_export(owner, pid, gen_id)
    claim = secrets.token_hex(8)
    box: dict = {}

    def mutate(state):
        state = state if isinstance(state, dict) else _empty_state()
        current = state.get("current") if isinstance(state.get("current"), dict) else None
        if current and current.get("status") in _LIVE:
            if current.get("docHash") == doc_hash and (not stems or current.get("stems")):
                box["reused"] = current
                return None
            busy = current.get("variant")
            if busy in VARIANTS and busy != variant:  # 작업 189: 다른 그림체가 내보내는 중(gen 하나에 한 번에 하나)
                raise VpocError(where, "ExportRunning",
                                f"{VARIANT_LABELS[busy]} 내보내기가 진행 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
            raise VpocError(where, "ExportRunning", "다른 내보내기가 진행 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        history = [h for h in (state.get("history") or []) if isinstance(h, dict)]
        if current:
            # 작업 189: 기록에 docHash 도 남긴다(그 그림체의 가장 최근 결과가 기록으로 밀려도 보기의 matchesDoc 이 맞게).
            # 작업 216-fix1b: 내려받기 zip 키도(지난 결과 내려받기 = zip). 옛 항목은 키가 없어 예전 그대로 mp4.
            history.append({**_history_item(current), "docHash": current.get("docHash"),
                            **({"zipKey": current["zipKey"]} if current.get("zipKey") else {})})
        n = int(state.get("n") or 0) + 1
        now = now_iso()
        started = {
            "n": n, "status": "queued", "variant": variant, "docVersion": _doc_version(doc), "docHash": doc_hash,
            "clips": clips, "seconds": _total(clips), "claim": claim, "claimedAt": now, "heartbeatAt": now,
            "createdAt": now, "startedAt": "", "finishedAt": "", "attempts": 1,
            "progress": {"done": 0, "total": len(clips)}, "key": "", "bytes": 0, "hasAudio": None, "error": "",
        }
        if audio_snapshot is not None:  # 작업 215a
            started["audio"] = audio_snapshot
        if stems:
            started["stems"] = True
        started.update(edit_text.export_extras(doc))  # 작업 215b: textFx(자막·타이틀·시작/끝 페이드 사본, 있을 때만)
        box["started"] = started
        return {"v": 1, "n": n, "current": started, "history": history[-EXPORT_HISTORY_MAX:]}

    _mutate_json(export_state_key(owner, pid, gen_id), mutate)
    if "started" in box:
        started = box["started"]
        log.info("vnew.edit.export_start pid=%s gen=%s n=%d clips=%d seconds=%.2f", pid, gen_id, started["n"],
                 len(clips), started["seconds"])
        _spawn_export(owner, pid, gen_id, started["n"], claim)
        return {"started": True, "export": _export_out(started, doc_hash)}
    return {"started": False, "export": _export_out(box.get("reused"), doc_hash)}


def reconcile_export(owner: str, pid: str, gen_id: str) -> bool:
    """멈춘 내보내기 이어받기(GET /edit·내보내기 시작 때). 바뀌었으면 True.

    queued·running 인데 하트비트가 EXPORT_STALL_S 넘게 없고 이 프로세스에서 돌지 않으면(파드 재시작 등) 새 claim 으로
    처음부터 다시 한다(임시 파일은 파드와 함께 사라졌다). 시도가 EXPORT_MAX_ATTEMPTS 번이면 failed 로 끝낸다.
    """
    key = export_state_key(owner, pid, gen_id)
    state, _ = _read_json(key)
    current = (state or {}).get("current")
    if not isinstance(current, dict) or current.get("status") not in _LIVE or _alive_here(current.get("claim")):
        return False
    beat = plan_mod._parse_iso(str(current.get("heartbeatAt") or current.get("claimedAt") or ""))  # noqa: SLF001
    if beat and (datetime.now(timezone.utc) - beat).total_seconds() <= EXPORT_STALL_S:
        return False
    new_claim = secrets.token_hex(8)
    box: dict = {}

    def mutate(data):
        cur = (data or {}).get("current")
        if (not isinstance(cur, dict) or cur.get("n") != current.get("n") or cur.get("claim") != current.get("claim")
                or cur.get("status") not in _LIVE):
            return None
        now = now_iso()
        attempts = int(cur.get("attempts") or 1)
        if attempts >= EXPORT_MAX_ATTEMPTS:
            cur.update(status="failed", claim="", finishedAt=now,
                       error="내보내기가 여러 번 중단돼 멈췄습니다. 다시 내보내 주세요.")
            box["failed"] = cur["n"]
        else:
            cur.update(status="queued", claim=new_claim, claimedAt=now, heartbeatAt=now, attempts=attempts + 1,
                       startedAt="", error="", progress={"done": 0, "total": len(cur.get("clips") or [])})
            box["resume"] = cur["n"]
        return data

    _mutate_json(key, mutate)
    if "resume" in box:
        log.warning("vnew.edit.export_resume pid=%s gen=%s n=%s", pid, gen_id, box["resume"])
        _spawn_export(owner, pid, gen_id, int(box["resume"]), new_claim)
    elif "failed" in box:
        log.warning("vnew.edit.export_abandoned pid=%s gen=%s n=%s", pid, gen_id, box["failed"])
    return bool(box)


def _beat(owner: str, pid: str, gen_id: str, token: str, **fields) -> dict:
    """claim 이 token 그대로면 하트비트(+ fields)를 쓰고 current 사본을 준다. 다른 실행이 이어받았으면 _ClaimLost."""
    box: dict = {}

    def mutate(data):
        cur = (data or {}).get("current")
        if not isinstance(cur, dict) or cur.get("claim") != token or cur.get("status") not in _LIVE:
            raise _ClaimLost()
        cur.update(fields)
        cur["heartbeatAt"] = now_iso()
        box["current"] = copy.deepcopy(cur)
        return data

    _mutate_json(export_state_key(owner, pid, gen_id), mutate)
    return box["current"]


def _finish(owner: str, pid: str, gen_id: str, token: str, **fields) -> None:
    """끝 상태(done·failed)를 쓰고 claim 을 비운다(claim 이 token 그대로일 때만)."""
    _beat(owner, pid, gen_id, token, claim="", **fields)


def _export_geometry(first: dict) -> tuple[int, int, int]:
    """내보내기 크기·fps = 첫 클립 원본(짝수, 1920×1080 안). fps 는 48000 을 나누는 정수면 그대로, 아니면 24."""
    width, height = int(first.get("width") or 0), int(first.get("height") or 0)
    if width < 16 or height < 16:
        width, height = 1280, 720
    scale = min(1.0, EXPORT_MAX_WIDTH / width, EXPORT_MAX_HEIGHT / height)
    width = max(2, int(round(width * scale / 2)) * 2)
    height = max(2, int(round(height * scale / 2)) * 2)
    fps = int(round(_num(first.get("fps")) or 0))
    if fps not in stitcher.EDIT_FPS_CHOICES:
        fps = EXPORT_FPS_DEFAULT
    return width, height, fps


def _clip_frames(clips: list[dict], fps: int) -> list[int]:
    """클립별 프레임 수. 누적 시간을 반올림해 나눠 클립이 많아도 전체가 문서 합과 한 프레임 안에서 맞는다(최소 1장).

    시간은 타임라인 길이(속도 반영, 지시서 115)다.
    """
    frames: list[int] = []
    clock = 0.0
    placed = 0
    for clip in clips:
        clock += _clip_seconds(clip)
        count = max(1, int(round(clock * fps)) - placed)
        frames.append(count)
        placed += count
    return frames


def _fx_plan(clips: list[dict], frames: list[int], fps: int) -> dict | None:
    """작업 181: 디졸브·J/L 컷 배치(프레임·샘플). 셋 다 없으면(소리 끈 클립의 앞당김·이어 감은 없는 것으로 본다) None — 181 전 경로.
    작업 185: 대사 타이밍(audioSlip)이 있는 소리 켠 클립이 하나라도 있으면 None 이 아니다(넷 다 없을 때만 181 전 경로).

    dissolve[i]: 클립 i → i+1 디졸브 프레임 = round(초 × fps)(마지막은 0). 두 디졸브가 한 클립을 다 덮지 않게 짧은 쪽 클립의
    (프레임 − 1) // 2 로 묶는다(검증한 45% 안이면 묶이지 않는다). start[i] = 화면 시작 프레임(앞 디졸브만큼 당겨진다),
    skip[i] = 앞 디졸브 프레임(몸통 조각이 건너뛰는 장 수), body[i] = 몸통 조각 장 수, total = 전체 프레임 = Σ frames − Σ dissolve.
    lead·tail: 앞당김·이어 감 샘플(48kHz, 첫 클립 lead·마지막 클립 tail·소리 끈 클립은 0). spf = 프레임당 샘플.
    """
    count = len(clips)
    rate = stitcher.EDIT_AUDIO_RATE
    dissolve = []
    for index in range(count):
        seconds = _dissolve_of(clips[index]) if index < count - 1 else 0.0
        number = int(round(seconds * fps)) if seconds else 0
        if number:
            number = min(number, (min(frames[index], frames[index + 1]) - 1) // 2)
        dissolve.append(max(0, number))
    lead = [0 if index == 0 or clip.get("muted") else int(round(_shift_of(clip, "audioLead") * rate))
            for index, clip in enumerate(clips)]
    tail = [0 if index == count - 1 or clip.get("muted") else int(round(_shift_of(clip, "audioTail") * rate))
            for index, clip in enumerate(clips)]
    # 작업 185: 대사 타이밍(audioSlip, 소리 끈 클립은 없는 것으로 본다)만 있어도 소리 창 경로. 값은 run_export 가 클립에서 읽는다.
    slip = any(not clip.get("muted") and _slip_of(clip) for clip in clips)
    if not any(dissolve) and not any(lead) and not any(tail) and not slip:
        return None
    skip = [0] + dissolve[:-1]
    start, position = [], 0
    for index in range(count):
        start.append(position)
        position += frames[index] - dissolve[index]
    body = [frames[index] - skip[index] - dissolve[index] for index in range(count)]
    return {"dissolve": dissolve, "skip": skip, "body": body, "start": start, "total": position, "lead": lead, "tail": tail,
            "spf": rate // fps}


def _audio_window(seg_start: float, speed: float, lead_samples: int, length: int,
                  alt: tuple[float, float] | None, slip: float = 0.0) -> dict:
    """작업 181: 클립 소리 창의 원본 구간. 반환 edit_audio 인자 {start_sec, end_sec, pad_front, samples}.

    소리 시작 = 화면 원본 시작(seg_start, 대체 소리면 alt 시작) − 앞당김 초 × 속도. 원본 0초보다 앞이면 0초에서 자르고 모자란 만큼
    (타임라인 샘플) 앞을 무음으로 채운다. 끝은 원본 끝(대체 소리면 alt 끝)까지 — 이어 감이 원본보다 길면 뒤가 무음이다.
    작업 185: slip(대사 타이밍 초, 양수 = 소리가 늦게)만큼 창 전체를 원본에서 − slip × 속도 옮겨 읽는다(화면 시각 t 에 원본 t − slip × 속도).
    앞당김과 같은 규칙이라 원본 앞은 무음으로 채우고, 원본 끝 뒤는 edit_audio 가 무음으로 채운다. 0 이면 181 과 같다.
    """
    rate = stitcher.EDIT_AUDIO_RATE
    begin = float(alt[0]) if alt is not None else float(seg_start)
    want = begin - lead_samples / rate * speed - float(slip) * speed
    pad = 0
    if want < 0:
        pad = min(length, int(round(-want / speed * rate)))
        want = 0.0
    if alt is not None and want >= float(alt[1]) - 1e-6:
        # 작업 185: 대사 타이밍(음수)으로 옮긴 창의 시작이 대체 소리 끝을 넘었다(남은 소리 없음) — 창 전체가 무음(ffmpeg 를 부르지 않는다).
        return {"start_sec": 0.0, "end_sec": None, "pad_front": length, "samples": length}
    return {"start_sec": round(want, 6), "end_sec": float(alt[1]) if alt is not None else None, "pad_front": pad,
            "samples": length}


def _audio_envelope(fx: dict, index: int) -> tuple[int, int, list[tuple[int, int, float, float]]]:
    """작업 181: 클립 index 소리 층의 (타임라인 시작 샘플, 길이, 이득 구간 [(t0, t1, g0, g1)]).

    화면에 있는 동안 1.0. 앞당김 구간(앞 클립 화면 아래)은 0 → OFFSCREEN_GAIN, 이어 감 구간(뒤 클립 화면 아래)은 OFFSCREEN_GAIN → 0
    직선. 디졸브 구간은 두 클립 소리를 엇갈려 바꾼다: 들어오는 쪽 (앞당김이 있으면 0.6, 없으면 0) → 1, 나가는 쪽 1 → (이어 감이 있으면
    0.6, 없으면 0). 그래서 이득이 구간 경계에서 끊기지 않는다(앞당김·이어 감과 디졸브가 함께 있어도).
    """
    spf = fx["spf"]
    lead, tail = fx["lead"][index], fx["tail"][index]
    picture = fx["start"][index] * spf
    end = (fx["start"][index] + fx["skip"][index] + fx["body"][index] + fx["dissolve"][index]) * spf
    fade_in, fade_out = fx["skip"][index] * spf, fx["dissolve"][index] * spf
    gain = OFFSCREEN_GAIN
    segments: list[tuple[int, int, float, float]] = []
    if lead:
        segments.append((picture - lead, picture, 0.0, gain))
    body_from, body_to = picture, end
    if fade_in:
        segments.append((picture, picture + fade_in, gain if lead else 0.0, 1.0))
        body_from = picture + fade_in
    if fade_out:
        body_to = end - fade_out
    if body_to > body_from:
        segments.append((body_from, body_to, 1.0, 1.0))
    if fade_out:
        segments.append((end - fade_out, end, 1.0, gain if tail else 0.0))
    if tail:
        segments.append((end, end + tail, gain, 0.0))
    return picture - lead, (end - picture) + lead + tail, segments


def _seg_start(clip: dict, info: dict, fps: int) -> float:
    """클립 영상 사슬의 원본 시작(초). 가장 가까운 프레임부터: 밀리초 반올림으로 프레임 시각을 조금 넘긴 in 이 한 프레임을
    건너뛰지 않게 반 프레임 당긴다(181 전 run_export 안의 계산을 옮겼다 — 값은 같다)."""
    src_fps = _num((info or {}).get("fps")) or float(fps)
    return max(0.0, float(clip["in"]) - 0.5 / src_fps)


def _append_file(target: str, source: str) -> None:
    with open(target, "ab") as out, open(source, "rb") as src:
        shutil.copyfileobj(src, out, 1 << 20)


def _wait_pod_lock(owner: str, pid: str, gen_id: str, n: int, claim: str):
    """파드 안 다른 worker 의 내보내기가 끝나기를 queued 그대로 기다린다(지시서 111). 반환 _pod_lock_try 의 손잡이.

    EXPORT_LOCK_POLL_S 마다 다시 잡아 보고, EXPORT_WAIT_BEAT_S 마다 하트비트(claim 확인 — 다른 실행이 이어받았으면
    _ClaimLost)를 쓴다. 차례는 먼저 잡는 쪽이다(엄밀한 줄 순서는 아니다). EXPORT_LOCK_WAIT_MAX_S 넘게 못 잡으면 _PodBusy.
    """
    locked, handle = _pod_lock_try()
    if locked:
        return handle
    log.info("vnew.edit.export_wait pid=%s gen=%s n=%d reason=pod", pid, gen_id, n)
    started = last_beat = time.monotonic()
    while True:
        time.sleep(EXPORT_LOCK_POLL_S)
        locked, handle = _pod_lock_try()
        if locked:
            return handle
        now = time.monotonic()
        if now - started > EXPORT_LOCK_WAIT_MAX_S:
            raise _PodBusy()
        if now - last_beat >= EXPORT_WAIT_BEAT_S:
            _beat(owner, pid, gen_id, claim)
            last_beat = now


_STAGE_MESSAGES = {
    "wait": "내보내기를 시작하지 못했습니다.",
    "probe": "원본 영상을 읽지 못했습니다.",
    "download": "원본 영상을 받지 못했습니다.",
    "clip": "클립을 자르지 못했습니다.",
    "join": "영상을 이어 붙이지 못했습니다.",
    "upload": "결과 영상을 저장하지 못했습니다.",
    "mix": "오디오 트랙을 섞지 못했습니다.",  # 작업 215a
    "package": "내려받기 묶음(소리 없는 영상 · 소리 mp3)을 만들지 못했습니다.",  # 작업 216-fix1b
}


def _export_audio_from(owner: str, pid: str, gen_id: str, variant: str, clips: list[dict], meta: dict[str, dict]) -> tuple[dict[str, dict], set[str]]:
    """내보내기 클립 원본 중 소리를 v1 에서 가져올 것(_audio_sources, 지시서 116 보완 10). 반환 (원본 키 → 원천, 무음으로 둘 키).

    gen 이 바뀌었으면(내보내기를 시작한 gen 이 아님) 대체하지 않는다. v1 길이·소리는 _ensure_meta(캐시 먼저)로 읽는다.
    작업 193 fix1: result 버전은 소리가 있어도 fromKey 소리(_result_audio)라 늘 AI로 고치기 상태를 읽는다. 원천에 소리가 없는 result 버전은
    무음 키(_result_mutes — v2v 결과 소리를 쓰지 않는다). 옛 길 버전은 필름 캐시 파형이 −60 dBFS 아래면 소리 없음으로 본다.
    """
    used = {c["sourceKey"] for c in clips if not c.get("muted")}
    if variant not in VARIANTS or not used:
        return {}, set()
    retakes = _retake_state(owner, pid, gen_id)
    if retakes is None:
        return {}, set()
    _plan, gen, now_gen = _load_gen(owner, pid)
    if now_gen != gen_id:
        return {}, set()
    every = _all_items(gen, variant, retakes)
    chain = set(_result_chain(every, used))
    items = [m for m in every if m.get("group") == "current" or m["sourceKey"] in used or m["sourceKey"] in chain]
    r_meta = _retake_meta(owner, pid, gen_id, items, meta, probe=True, silent_keys=used)
    found = _audio_sources(gen, items, r_meta)
    mutes = _result_mutes(items, r_meta, found)
    return {k: v for k, v in found.items() if k in used}, {k for k in mutes if k in used}


def _drop_files(paths) -> None:
    """작업 219: 있는 파일만 지운다(내보내기 스템 · 임시 소리)."""
    for path in paths:
        if path and os.path.exists(path):
            os.unlink(path)


class _dropping:  # noqa: N801 — with 문에 쓰는 작은 도우미
    """작업 219: with 를 나갈 때(성공 · 실패 모두) paths 에 남은 파일을 지운다. 지우기 실패는 원래 예외를 가리지 않는다."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths

    def __enter__(self) -> list[str]:
        return self.paths

    def __exit__(self, *_exc) -> bool:
        try:
            _drop_files(self.paths)
        except OSError:
            pass
        return False


def _export_plan_sources(owner: str, pid: str, clips: list[dict], meta: dict, audio_from: dict, fps: int, width: int,
                         height: int) -> tuple[list[dict], dict, dict | None, dict, list[dict]]:
    """작업 219: 215c 계획(빈틈 · 사용 안 함 → 검은 클립, 역재생 · 게인 · 음높이 · 숨긴 화면 → 파생 원본) 먼저, 그 뒤 클립 · meta 로
    217 분리 결과(목소리 → audio_from, 배경 → 자리 index)를 고른다. 순서가 반대면 배경 자리 · audio_from 키가 어긋난다.
    반환 (클립, meta, 215c 계획, audio_from, 분리 배경)."""
    from . import audio_separate  # noqa: PLC0415 — audio_separate 가 edit 를 가져온다

    clips, meta, tools_plan = edit_tools.export_plan(clips, meta, fps, width, height)  # 작업 215c
    audio_from, sep_bg = audio_separate.export_sources(owner, pid, clips, audio_from, meta,
                                                       derived=(tools_plan or {}).get("derived"))  # 작업 217
    return clips, meta, tools_plan, audio_from, sep_bg


def run_export(owner: str, pid: str, gen_id: str, n: int, claim: str) -> None:
    """내보내기 스레드 본체. 임시 디렉터리에서 클립마다 다시 인코딩 → 잇기 → 영상 버킷에 올린다. 끝나면 임시 파일을 지운다.

    프로세스당 한 번에 하나(_EXPORT_SEM), 파드당 한 번에 하나(잠금 파일, _wait_pod_lock). 기다리는 동안·클립 사이에 하트비트를
    쓰고 claim 을 확인한다(이어받긴 실행이 있으면 결과를 쓰지 않고 끝낸다). 원본은 처음 쓰일 때 내려받고 마지막으로 쓰인 뒤
    지운다. 시간 상한 EXPORT_MAX_S(잠금을 기다린 시간은 빼고 잰다).
    """
    acquired = False
    pod_handle = None
    stage = "wait"
    clip_no = 0
    started = time.monotonic()
    try:
        while not _EXPORT_SEM.acquire(timeout=EXPORT_WAIT_BEAT_S):
            _beat(owner, pid, gen_id, claim)
        acquired = True
        pod_handle = _wait_pod_lock(owner, pid, gen_id, n, claim)
        current = _beat(owner, pid, gen_id, claim, status="running", startedAt=now_iso(), error="")
        attempt_started = time.monotonic()
        clips = [c for c in current.get("clips") or [] if isinstance(c, dict)]
        stage = "probe"
        prefix = f"users/{owner}/video/{pid}/"
        meta, _ = _ensure_meta(owner, pid, gen_id, [c.get("sourceKey") for c in clips])
        for clip in clips:
            duration = _num((meta.get(clip.get("sourceKey")) or {}).get("duration"))
            if not str(clip.get("sourceKey") or "").startswith(prefix) or not duration:
                raise VpocError("vnew.edit.export", "SourceUnreadable", "원본 영상을 읽지 못했습니다.", 409)
        if not clips:
            raise VpocError("vnew.edit.export", "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
        width, height, fps = _export_geometry(meta[clips[0]["sourceKey"]])
        frames = _clip_frames(clips, fps)
        audio_from, mutes = _export_audio_from(owner, pid, gen_id, str(current.get("variant") or ""), clips, meta)
        if mutes:  # 작업 193 fix1: 원천에 소리가 없는 result 버전 클립은 v2v 소리 대신 무음(이 실행 안에서만 — 문서는 그대로)
            clips = [dict(c, muted=True) if c["sourceKey"] in mutes else c for c in clips]
        clips, meta, tools_plan, audio_from, sep_bg = _export_plan_sources(owner, pid, clips, meta, audio_from, fps, width, height)
        frames = _clip_frames(clips, fps) if tools_plan else frames  # 작업 215c
        # 지시서 116 보완 10: 클립별 대체 소리 (v1 시작, 끝). 작업 219: audio_from 은 215c 계획 뒤 클립 키(파생 원본 포함) 기준.
        alt = [_clip_audio(c, audio_from.get(c["sourceKey"])) for c in clips]
        with_audio = any(not c.get("muted") and ((meta.get(c["sourceKey"]) or {}).get("hasAudio") or alt[i] is not None)
                         for i, c in enumerate(clips))
        last_use: dict[str, int] = {}
        for index, clip in enumerate(clips):
            last_use[clip["sourceKey"]] = index
            if alt[index] is not None:
                last_use[audio_from[clip["sourceKey"]]["sourceKey"]] = index
        if any(a is not None for a in alt):
            log.info("vnew.edit.export_audio_from pid=%s gen=%s n=%d clips=%d", pid, gen_id, n, sum(a is not None for a in alt))
        fx = _fx_plan(clips, frames, fps)  # 작업 181: 디졸브·J/L 컷이 없으면 None(아래는 181 전 경로 그대로)
        if fx is not None:
            log.info("vnew.edit.export_fx pid=%s gen=%s n=%d dissolves=%d leads=%d tails=%d frames=%d", pid, gen_id, n,
                     sum(1 for d in fx["dissolve"] if d), sum(1 for v in fx["lead"] if v), sum(1 for v in fx["tail"] if v),
                     fx["total"])
        scratch: list[str] = []  # 작업 219: 스템 wav · 무음 대사 — 성공이든 실패든 workdir 을 지우기 전에 지운다
        with tempfile.TemporaryDirectory(prefix="vnew-edit-export-") as workdir, _dropping(scratch):
            local: dict[str, str] = {}
            segments: list[str] = []
            layers: list[dict] = []  # 작업 181: 소리 층(edit_mix)
            audio_path = os.path.join(workdir, "audio.raw") if with_audio else ""
            if audio_path:
                open(audio_path, "wb").close()
            last_beat = time.monotonic()

            def fetch(key: str, name: str) -> str:
                path = local.get(key)
                if path is None:
                    path = os.path.join(workdir, name)
                    _download(key, path, bucket=vpoc_settings.VIDEO_BUCKET)
                    local[key] = path
                return path

            edit_tools.export_prepare(tools_plan, local, fetch, workdir, fps=fps, width=width, height=height)  # 작업 215c
            for index, clip in enumerate(clips):
                clip_no = index + 1
                if time.monotonic() - attempt_started > EXPORT_MAX_S:
                    raise _ExportTimeout()
                source = clip["sourceKey"]
                info = meta.get(source) or {}
                if source not in local:
                    stage = "download"
                path = fetch(source, f"src-{index:03d}.mp4")
                extra: dict = {}
                if audio_path and alt[index] is not None:
                    a_key = audio_from[source]["sourceKey"]
                    if a_key not in local:
                        stage = "download"
                    a_path = fetch(a_key, f"src-{index:03d}a.mp4")
                    extra = {"audio_source": a_path, "audio_start_sec": alt[index][0], "audio_end_sec": alt[index][1]}
                stage = "clip"
                start = _seg_start(clip, info, fps)
                seg_video = os.path.join(workdir, f"seg-{index:03d}.mp4")
                seg_audio = os.path.join(workdir, f"seg-{index:03d}.raw") if audio_path else ""
                has_sound = bool(audio_path) and not clip.get("muted") and (bool(info.get("hasAudio")) or bool(extra))
                # 클립 인코딩(720p x264 ≈ 140MB, ⑤ 컷 자르기 trim 과 같은 크기)은 ⑤ 준비 단계와 같은 세마포어 안에서 한다
                # (같은 프로세스에서 ffmpeg 인코딩 두 개가 겹치지 않게, 512Mi). 클립 하나씩이라 ⑤ 제출을 오래 막지 않는다.
                generate._PREPARE_SEM.acquire()  # noqa: SLF001
                try:
                    if fx is None:
                        _export_segment(
                            path, seg_video, start_sec=start, frames=frames[index], fps=fps, width=width, height=height,
                            audio_out=seg_audio,
                            audio_from_source=has_sound,
                            speed=_speed_of(clip),  # 지시서 115: 영상 setpts·소리 atempo(1 이면 115 전 명령 그대로)
                            **extra,
                        )
                    else:
                        # 작업 181: 몸통(앞뒤 디졸브 장을 뺀 가운데)만 영상으로, 소리는 앞당김·이어 감을 더한 창을 따로 뽑는다.
                        _export_segment(path, seg_video, start_sec=start, frames=fx["body"][index], fps=fps, width=width,
                                        height=height, audio_out="", audio_from_source=False, speed=_speed_of(clip),
                                        skip_frames=fx["skip"][index])
                        if has_sound:
                            at, length, envelope = _audio_envelope(fx, index)
                            window = _audio_window(start, _speed_of(clip), fx["lead"][index], length, alt[index],
                                                   slip=_slip_of(clip))  # 작업 185: 대사 타이밍(대체 소리도 같은 규칙)
                            layer_path = os.path.join(workdir, f"aud-{index:03d}.raw")
                            _export_audio(extra.get("audio_source") or path, layer_path, speed=_speed_of(clip), **window)
                            layers.append({"path": layer_path, "at": at, "envelope": envelope})
                finally:
                    generate._PREPARE_SEM.release()  # noqa: SLF001
                if fx is None and seg_audio:
                    _append_file(audio_path, seg_audio)
                    os.unlink(seg_audio)
                segments.append(seg_video)
                if fx is not None and fx["dissolve"][index]:
                    # 작업 181: 이 클립의 마지막 장들과 다음 클립의 처음 장들을 섞은 경계 조각(다음 클립 원본을 미리 받는다).
                    after = clips[index + 1]
                    if after["sourceKey"] not in local:
                        stage = "download"
                    after_path = fetch(after["sourceKey"], f"src-{index + 1:03d}.mp4")
                    stage = "clip"
                    blend = os.path.join(workdir, f"dis-{index:03d}.mp4")
                    generate._PREPARE_SEM.acquire()  # noqa: SLF001
                    try:
                        _export_dissolve(
                            path, after_path, blend, a_start_sec=start, a_speed=_speed_of(clip),
                            a_skip_frames=frames[index] - fx["dissolve"][index],
                            b_start_sec=_seg_start(after, meta.get(after["sourceKey"]) or {}, fps), b_speed=_speed_of(after),
                            frames=fx["dissolve"][index], fps=fps, width=width, height=height,
                        )
                    finally:
                        generate._PREPARE_SEM.release()  # noqa: SLF001
                    segments.append(blend)
                for used in dict.fromkeys([source] + ([audio_from[source]["sourceKey"]] if extra else [])):
                    if last_use[used] == index and used in local:
                        os.unlink(local.pop(used))
                if time.monotonic() - last_beat >= EXPORT_HEARTBEAT_S or index == len(clips) - 1:
                    _beat(owner, pid, gen_id, claim, progress={"done": index + 1, "total": len(clips)})
                    last_beat = time.monotonic()
            stage = "join"
            if fx is not None and audio_path:
                _export_mix(layers, audio_path, samples=fx["total"] * fx["spf"])  # 작업 181: 소리 층을 타임라인에서 섞는다
                for layer in layers:
                    os.unlink(layer["path"])
            stems_out: list[tuple[str, str]] = []
            dialogue_wav, bg_wavs, pkg_stems = "", [], []  # 216-fix2
            dialogue_raw = ""  # 작업 219: A1 대사 스템이 없을 때(A1 끔 · 다른 트랙 솔로) 대사.mp3 = 무음
            mix_doc = current.get("audio") if isinstance(current.get("audio"), dict) else None
            from . import audio_separate  # noqa: PLC0415 — audio_separate 가 edit 를 가져온다
            mix_doc = audio_separate.export_mix_doc(mix_doc, sep_bg, clips, frames, fx, fps)  # 작업 217: 분리된 배경 → A2(배경음)
            if mix_doc is not None:
                # 작업 215a: A1(위에서 만든 클립 소리) + 오디오 트랙을 섞는다 · 스템. 클립 소리가 없으면 A1 은 무음.
                stage = "mix"
                spf = stitcher.EDIT_AUDIO_RATE // fps
                total_samples = (fx["total"] if fx is not None else sum(frames)) * spf
                if not audio_path:
                    audio_path = os.path.join(workdir, "audio.raw")
                    stitcher._fit_pcm(audio_path, total_samples * stitcher.EDIT_AUDIO_BYTES_PER_SAMPLE)  # noqa: SLF001
                a1 = mix_doc.get("a1") or edit_audio.default_a1()
                spans = sound_ranges(edit_audio.pcm_peaks(audio_path, PEAKS_PER_SECOND), PEAKS_PER_SECOND) if a1.get("duck") else []
                mix_plan = edit_audio.mix_plan(mix_doc.get("tracks") or [], a1, total_samples=total_samples, spans=spans,
                                               stems=True)  # 216-fix2: 대사 · 배경음 mp3 를 위해 늘 스템을 만든다(스템 zip 은 고른 때만)
                asset_paths: dict[str, str] = {}
                for asset_id in mix_plan["inputs"]:
                    info = (mix_doc.get("assets") or {}).get(asset_id) or {}
                    if not str(info.get("key") or "").startswith(prefix):
                        raise VpocError("vnew.edit.export", "AudioAssetMissing", "내 소리 파일을 찾지 못했습니다.", 409)
                    stage = "download"
                    asset_paths[asset_id] = fetch(str(info["key"]), f"snd-{asset_id}.{info.get('ext') or 'bin'}")
                stage = "mix"
                mixed = _export_tracks(mix_plan, audio_path, asset_paths, workdir)
                if mixed["raw"] != audio_path:
                    os.unlink(audio_path)
                    audio_path = mixed["raw"]
                for path in asset_paths.values():
                    os.unlink(path)
                stems_all = list(mixed.get("stems") or [])
                scratch.extend(path for _name, path in stems_all)  # 작업 219: 실패해도 남기지 않는다
                # 216-fix2: 내려받기 zip 의 대사(대사만 스템) · 배경음(나머지 트랙 스템). 스템 zip 은 사용자가 고른 때만.
                dialogue_wav = next((path for name, path in stems_all if name == edit_audio.DIALOGUE_STEM), "")
                bg_wavs = [path for name, path in stems_all if name not in (edit_audio.DIALOGUE_STEM, edit_audio.MIX_STEM)]
                stems_out = stems_all if current.get("stems") else []
                pkg_stems = [path for _name, path in stems_all if not current.get("stems")]
                if not dialogue_wav:
                    # 작업 219: 대사만 스템이 없으면(A1 끔 · 다른 트랙 솔로) 대사.mp3 는 전체 소리가 아니라 같은 길이 무음
                    dialogue_raw = os.path.join(workdir, "dialogue-silence.raw")
                    scratch.append(dialogue_raw)
                    stitcher._fit_pcm(dialogue_raw, total_samples * stitcher.EDIT_AUDIO_BYTES_PER_SAMPLE)  # noqa: SLF001
                # 작업 219: 섞은 소리에 들리는 것이 있을 때만 소리 있음 — A1 은 켜져 있고 클립 소리가 있을 때, 그 밖은 들리는 트랙
                with_audio = (with_audio and bool(mix_plan["a1On"])) or bool(mix_plan["buses"])
                log.info("vnew.edit.export_tracks pid=%s gen=%s n=%d buses=%d items=%d duck=%d stems=%d", pid, gen_id, n,
                         len(mix_plan["buses"]), sum(len(b["items"]) for b in mix_plan["buses"]), len(spans), len(stems_out))
                stage = "join"
            output = os.path.join(workdir, "edit.mp4")
            _export_join(segments, output, audio_path=audio_path)
            edit_text.finish(current, clips, frames, fx, fps, width, height, workdir, output)  # 작업 215b: 효과 마무리 패스(없으면 그대로)
            for segment in segments:
                os.unlink(segment)
            out_info = _probe_file(output) or {}
            seconds = round(_num(out_info.get("duration")) or 0.0, 3)
            if seconds <= 0:
                raise stitcher.StitchError("이어 붙인 영상 길이를 읽지 못했습니다.")
            # 작업 216-fix1b: 내려받기 zip — 위에서 한 번 만든 영상을 나눈다(영상 스트림 복사 -an · 잇기에 넣은 소리 그대로 mp3).
            # 소리가 없으면 영상 길이(프레임 × 프레임당 샘플)만큼 무음 mp3.
            stage = "package"
            zip_path = os.path.join(workdir, "package.zip")
            package = _export_package(output, audio_path, zip_path, name=_package_name(n),
                                      samples=(fx["total"] if fx is not None else sum(frames)) * (stitcher.EDIT_AUDIO_RATE // fps),
                                      seconds=seconds, has_audio=with_audio, dialogue_raw=dialogue_raw, dialogue_wav=dialogue_wav,
                                      bg_wavs=bg_wavs)
            # 216-fix2 · 작업 219: 내려받기 zip 을 쓴 뒤 그 스템은 바로 지운다(스템 zip 을 고르지 않았으면 전부 · 무음 대사도).
            _drop_files(pkg_stems + ([dialogue_raw] if dialogue_raw else []))
            stems_zip = ""
            if stems_out:
                # 작업 215a: 스템 zip(압축 없이 — wav 는 거의 줄지 않는다). 파일 이름은 서버가 정한 글자만(대사만.wav · 배경음.wav …).
                # 작업 219: 올리기 전에 묶어 스템 wav 를 하나씩 바로 지운다(영상 · zip 을 올리는 동안 디스크에 두지 않는다).
                import zipfile  # noqa: PLC0415

                stems_zip = os.path.join(workdir, "stems.zip")
                with zipfile.ZipFile(stems_zip, "w", compression=zipfile.ZIP_STORED) as archive:
                    for name, path in stems_out:
                        archive.write(path, arcname=name)
                        os.unlink(path)
            if audio_path:
                os.unlink(audio_path)
            size = os.path.getsize(output)
            _beat(owner, pid, gen_id, claim)  # 올리기 전에 claim 확인
            stage = "upload"
            key = export_key(owner, pid, gen_id, n)
            _upload(output, key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")
            zip_key = export_zip_key(owner, pid, gen_id, n)
            _upload(zip_path, zip_key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="application/zip")
            os.unlink(zip_path)
            stem_fields: dict = {}
            if stems_zip:
                _beat(owner, pid, gen_id, claim)
                stems_key = export_stems_key(owner, pid, gen_id, n)
                _upload(stems_zip, stems_key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="application/zip")
                os.unlink(stems_zip)
                stem_fields = {"stemsKey": stems_key, "stems": [name for name, _ in stems_out]}
        _finish(owner, pid, gen_id, claim, status="done", key=key, seconds=seconds, bytes=size, hasAudio=with_audio,
                finishedAt=now_iso(), error="", zipKey=zip_key, zipBytes=package.get("bytes"),
                files=list(package.get("entries") or []), **stem_fields)
        log.info("vnew.edit.export_done pid=%s gen=%s n=%d clips=%d seconds=%.3f bytes=%d fps=%d size=%dx%d audio=%s "
                 "ms=%d rss_mb=%.0f", pid, gen_id, n, len(clips), seconds, size, fps, width, height, with_audio,
                 int((time.monotonic() - started) * 1000), generate._rss_mb())  # noqa: SLF001
    except _ClaimLost:
        log.info("vnew.edit.export_claim_lost pid=%s gen=%s n=%d stage=%s", pid, gen_id, n, stage)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, _ExportTimeout):
            message = f"내보내기가 {EXPORT_MAX_S // 60}분을 넘어 멈췄습니다. 클립을 줄여 다시 내보내 주세요."
        elif isinstance(exc, _PodBusy):
            message = "앞의 내보내기가 끝나지 않아 시작하지 못했습니다. 잠시 뒤 다시 내보내 주세요."
        elif isinstance(exc, VpocError):
            message = exc.message
        else:
            message = _STAGE_MESSAGES.get(stage, "내보내지 못했습니다.")
            if stage == "clip" and clip_no:
                message = f"{clip_no}번째 클립을 자르지 못했습니다."
        log.warning("vnew.edit.export_failed pid=%s gen=%s n=%d stage=%s clip=%d err=%s", pid, gen_id, n, stage, clip_no,
                    error_gist(f"{type(exc).__name__}: {exc}", 300))
        try:
            _finish(owner, pid, gen_id, claim, status="failed", error=message, finishedAt=now_iso())
        except _ClaimLost:
            pass
        except Exception as write_exc:  # noqa: BLE001
            log.warning("vnew.edit.export_state_failed pid=%s n=%d err=%s", pid, n, type(write_exc).__name__)
    finally:
        _pod_lock_release(pod_handle)
        if acquired:
            _EXPORT_SEM.release()
