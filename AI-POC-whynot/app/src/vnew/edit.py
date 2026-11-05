"""vnew ⑦ 편집 — 영상 편집 타임라인(지시서 110, 2026-11-01).

⑤에서 만든 영상(컷·테이크 결과)을 클립으로 늘어놓고 자르기·나누기·순서·소리 끄기를 고친 편집 문서(EDL)를 gen 마다
하나 저장하고, 문서대로 서버 ffmpeg 로 잘라 이어 붙여 mp4 하나로 내보낸다. 유료 생성 API 는 부르지 않는다.

재료(허용 목록): 지금 gen 의 그 그림체(variant) 결과 영상만 클립 원본(sourceKey)이 될 수 있다(_material_items).
  - 컷 단위(previz): units[i].<variant>.outputKey(completed). 3초로 채워 만든 컷(padded)은 기본 끝 = origSeconds.
  - 통 테이크(previz-take): units[i].<variant>.outputKey. 기본 문서는 녹화 컷 경계로 나눈다(경계를 믿을 수 없으면 테이크
    통째). 실사 회차 기록(realHistory)의 결과도 허용한다(새 회차가 시작돼도 저장된 문서가 깨지지 않게).
  - 옛 순차 모드: segments[i].<variant>.outputKey.
  - 합친 영상(finalReal·finalWebtoon done): 위 결과가 없을 때 기본 문서에 통째 1개.
저장(메인 버킷, gen 루트 = generate._gen_root):
  edit.json(편집 문서, 조건부 쓰기 → 409) · edit-export.json(내보내기 상태) · edit-media/index.json(길이·소리·크기·fps 캐시) ·
  edit-media/{mid}-strip.jpg · edit-media/{mid}-film.json(필름스트립·파형 피크, 처음 요청 때 한 번 만든다).
  내보낸 결과는 영상 버킷 {gen 루트}/final-edit-{n}.mp4.
외부 호출 래퍼(검사가 가짜로 바꾼다): _download · _upload · _probe_file · _film_file · _export_segment · _export_join ·
_spawn_export. 파드 안 내보내기 잠금 파일: _pod_lock_try · _pod_lock_release(지시서 111). STORE 는 vpoc.store.STORE 다
(검사가 모듈 변수로 바꾼다).
지시서 115: 클립 속도 speed(SPEEDS, 없으면 1 — 1 은 저장하지 않는다). 타임라인 길이 = (out − in) / speed(_clip_seconds).
재료가 없을 때 요약에 잠김 이유(summary → reason, gen 이 없어도 gen_view 가 부른다).
지시서 116: VNEW_EDIT_RETAKE 가 켜져 있으면 ⑦ AI로 고치기(retake.py)로 다시 만든 버전(v2…)도 재료다 — 그 칸 지금 결과 바로 뒤
(_all_items, group "retake", 이름 끝 " v{n}"). 꺼져 있으면 _retake_state 가 None 이라 재료·보기가 지시서 115 와 같다.
지시서 116 보완 10: 소리 없는 버전은 그 칸 v1 의 같은 녹화 시각 소리를 쓴다(_audio_sources — 재료 audioFrom·내보내기 audio_source).
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

from . import generate
from . import plan as plan_mod
from . import settings as vnew_settings

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


def doc_key(owner: str, pid: str, gen_id: str) -> str:
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


def _total(clips: list[dict]) -> float:
    return round(sum(_clip_seconds(c) for c in clips if isinstance(c, dict)), 3)


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
    """
    from . import retake  # noqa: PLC0415 — retake 가 edit 를 import 한다(순환 피함).

    current = {m.get("cellId"): m for m in items if m.get("group") == "current"}
    cells = retake._cell_index(gen)  # noqa: SLF001
    out: dict[str, dict] = {}
    for item in items:
        if item.get("group") != "retake":
            continue
        own = meta.get(item["sourceKey"]) or {}
        base = current.get(item.get("cellId"))
        if own.get("hasAudio") is not False or not base:
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


def _retake_meta(owner: str, pid: str, gen_id: str, items: list[dict], meta: dict[str, dict], *, probe: bool) -> dict[str, dict]:
    """_audio_sources 에 넘길 meta: 버전과 그 칸 v1 중 meta 에 없는 것을 채운다(probe 면 _ensure_meta, 아니면 길이 캐시만)."""
    cells = {m.get("cellId") for m in items if m.get("group") == "retake"}
    keys = [m["sourceKey"] for m in items if m.get("cellId") in cells and m.get("group") in ("current", "retake")
            and not isinstance((meta.get(m["sourceKey"]) or {}).get("hasAudio"), bool)]
    if not keys:
        return meta
    if probe:
        extra, _ = _ensure_meta(owner, pid, gen_id, keys)
    else:
        cached = _read_index(owner, pid, gen_id)
        extra = {k: cached[media_id(k)] for k in keys
                 if isinstance(cached.get(media_id(k)), dict) and cached[media_id(k)].get("sourceKey") == k}
    return {**extra, **{k: v for k, v in meta.items() if isinstance((v or {}).get("hasAudio"), bool) or k not in extra}}


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
    - 전체 길이(타임라인 길이 합) ≤ MAX_TOTAL_S.
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
        label = clip.get("label")
        if isinstance(label, str):
            label = _CONTROL_RE.sub("", label).strip()[:MAX_LABEL_LEN]
            if label:
                item["label"] = label
        total += (end - start) / speed
        clips.append(item)
    if total > MAX_TOTAL_S + 1e-6:
        raise VpocError(where, "TooLong", f"타임라인 전체 길이는 {MAX_TOTAL_S:.0f}초까지입니다.", 400)
    return clips


def _doc_hash(variant: str, clips: list[dict]) -> str:
    """내보내기 결과를 정하는 값만(그림체·원본·구간·소리·속도)의 sha256. 클립 id·이름은 넣지 않는다.

    속도는 1 이 아닐 때만 넣는다(지시서 115 — 속도를 안 쓴 문서·진행 중 내보내기의 해시가 115 전과 같다).
    """
    body = []
    for c in clips:
        item = {"sourceKey": c["sourceKey"], "in": c["in"], "out": c["out"], "muted": bool(c.get("muted"))}
        speed = _speed_of(c)
        if speed != 1:
            item["speed"] = speed
        body.append(item)
    text = json.dumps({"variant": variant, "clips": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _saved_doc(owner: str, pid: str, gen_id: str) -> tuple[dict | None, str]:
    doc, etag = _read_json(doc_key(owner, pid, gen_id))
    if doc is None or doc.get("variant") not in VARIANTS or not isinstance(doc.get("clips"), list):
        return None, etag
    return doc, etag


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


def _export_out(current: dict | None, doc_hash: str) -> dict:
    current = current if isinstance(current, dict) else {}
    if not current:
        return {"status": "none"}
    done = current.get("status") == "done"
    url = _presign_video(str(current.get("key") or "")) if done else ""
    download = _presign_download(str(current.get("key") or ""), current.get("n")) if done else ""
    return {
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


def edit_view(owner: str, pid: str, variant: str | None = None) -> dict:
    """GET /p/<pid>/edit. 문서(저장본, 없으면 기본 문서)·재료(길이·재생 URL)·내보내기 상태.

    그림체: variant 인자 → 저장된 문서의 그림체 → gen.selected → 웹툰 → 실사 중 재료가 있는 것. 저장된 문서와 다른 그림체를
    물으면 그 그림체의 기본 문서를 준다(saved false, etag 는 저장본 것 — PUT 하면 바뀐다).
    """
    where = "vnew.edit"
    if variant not in (None, "") and variant not in VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    plan, gen, gen_id = _load_gen(owner, pid)
    try:
        reconcile_export(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다(다음 조회가 다시 본다).
        log.warning("vnew.edit.reconcile_failed pid=%s err=%s", pid, type(exc).__name__)
    doc, etag = _saved_doc(owner, pid, gen_id)
    retakes = _retake_state(owner, pid, gen_id)  # 지시서 116: 켬이면 다시 만든 버전(재료)·끊긴 다시 만들기 이어받기, 끔이면 None
    if retakes is not None:
        retakes = _retake_reconcile(owner, pid, gen_id, retakes)
    available = {v: _all_items(gen, v, retakes) for v in VARIANTS}
    if variant:
        chosen = variant
    elif doc is not None:
        chosen = doc["variant"]
    else:
        order = [gen.get("selected")] if gen.get("selected") in VARIANTS else []
        order += ["webtoon", "real"]
        chosen = next((v for v in order if available[v]), order[0])
    items = available[chosen]
    if not items:
        raise VpocError(where, "NoMaterials",
                        f"편집할 {VARIANT_LABELS[chosen]} 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들어 주세요.", 409)
    _describe(plan, items)
    _number_takes(gen, chosen, items)  # 작업 145
    allowed = {m["sourceKey"] for m in items}
    saved = doc is not None and doc["variant"] == chosen
    default_items = _default_group(items)
    probe_keys = [m["sourceKey"] for m in default_items]
    if saved:
        probe_keys += [c.get("sourceKey") for c in doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, complete = _ensure_meta(owner, pid, gen_id, probe_keys, budget_s=PROBE_BUDGET_S)
    defaults = _default_clips(plan, gen, items, meta) if complete else []
    if saved:
        out_doc = doc
    else:
        out_doc = {"version": _doc_version(doc), "variant": chosen, "updatedAt": "", "clips": defaults, "audio": []}
    state, _ = _read_json(export_state_key(owner, pid, gen_id))
    state = state or {}
    clean = [c for c in out_doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    doc_hash = _doc_hash(chosen, [{**c, "in": _num(c.get("in")) or 0.0, "out": _num(c.get("out")) or 0.0} for c in clean]) \
        if saved else ""
    default_mids = {m["mid"] for m in default_items}
    materials = [_material_out(m, meta.get(m["sourceKey"]), default_mids) for m in items]
    if retakes is not None:  # 지시서 116: 재료마다 AI로 고치기 표식(프롬프트는 넣지 않는다 — 이 보기는 읽기 권한)
        from . import retake  # noqa: PLC0415

        for item, out in zip(items, materials):
            out["retake"] = retake.material_flag(chosen, item, gen, retakes, plan=plan, duration=out.get("duration"))  # 지시서 119: 컷 고르기
        # 지시서 116 보완 10: 소리 없는 버전은 그 칸 v1 소리를 쓴다(내보내기와 같은 _audio_sources). 새로 재지 않고 길이 캐시만 본다.
        audio_from = _audio_sources(gen, items, _retake_meta(owner, pid, gen_id, items, meta, probe=False))
        for item, out in zip(items, materials):
            if item["sourceKey"] in audio_from:
                out["audioFrom"] = dict(audio_from[item["sourceKey"]])
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
        "defaultClips": defaults,
        "invalidClips": _invalid_clips(out_doc["clips"], allowed, meta) if saved else [],
        "probing": not complete,
        "totalSeconds": _total(out_doc["clips"]),
        "materials": materials,
        "limits": {"maxClips": MAX_CLIPS, "maxTotalSeconds": MAX_TOTAL_S, "minClipSeconds": MIN_CLIP_S},
        "export": _export_out(state.get("current"), doc_hash),
        "exportHistory": [
            {**_history_item(h), "videoUrl": _presign_video(str(h.get("key") or "")) if h.get("status") == "done" else "",
             "downloadUrl": _presign_download(str(h.get("key") or ""), h.get("n")) if h.get("status") == "done" else ""}
            for h in (state.get("history") or []) if isinstance(h, dict)
        ],
    }
    if retakes is not None:
        view["retake"] = {"enabled": True}
    return view


def _retake_reconcile(owner: str, pid: str, gen_id: str, retakes: dict) -> dict:
    """끊긴 다시 만들기·초안 이어받기(retake.reconcile_state, 지시서 116). 보기는 실패하지 않는다(그대로 돌려준다)."""
    from . import retake  # noqa: PLC0415

    try:
        return retake.reconcile_state(owner, pid, gen_id, retakes)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.edit.retake_reconcile_failed pid=%s err=%s", pid, type(exc).__name__)
        return retakes


def save_doc(owner: str, pid: str, payload) -> dict:
    """PUT /p/<pid>/edit. 바디 {etag | version, variant, clips, audio?}. 반환 {doc, etag, saved, totalSeconds}.

    기준: etag(글자, 첫 저장은 "") 또는 version(정수, 첫 저장은 0). 지금 저장본과 다르면 409 EditChanged. 쓰기는 S3 조건부
    쓰기(첫 저장은 If-None-Match, 그 뒤 If-Match)라 동시에 같은 기준으로 저장하면 하나만 성공하고 나머지는 409 다.
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
    plan, gen, gen_id = _load_gen(owner, pid)
    allowed = {m["sourceKey"] for m in _all_items(gen, variant, _retake_state(owner, pid, gen_id))}
    referenced = [c.get("sourceKey") for c in raw if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, _ = _ensure_meta(owner, pid, gen_id, referenced)
    durations = {key: (meta.get(key) or {}).get("duration") for key in allowed}
    clips = _clean_clips(raw, allowed, durations, where)
    key = doc_key(owner, pid, gen_id)
    current, current_etag = _read_json(key)
    current_version = _doc_version(current)
    if has_etag:
        if (current is None and etag) or (current is not None and etag != current_etag):
            raise _changed(where)
    elif version != current_version:
        raise _changed(where)
    doc = {"version": current_version + 1, "variant": variant, "updatedAt": now_iso(), "clips": clips, "audio": []}
    try:
        new_etag = STORE.write_json(key, doc, etag=current_etag if current is not None else "", create_only=current is None)
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
        "generated": generated,
    }


# ================================================================ 내보내기


def _empty_state() -> dict:
    return {"v": 1, "n": 0, "current": None, "history": []}


def start_export(owner: str, pid: str, etag) -> dict:
    """POST /p/<pid>/edit/export. 저장된 문서(etag 가 지금 저장본과 같아야 한다)를 내보내기 시작한다.

    같은 문서(_doc_hash)로 진행 중(queued·running)인 내보내기가 있으면 새로 시작하지 않고 그것을 준다(started false).
    다른 문서로 진행 중이면 409 ExportRunning. 문서 클립은 시작 때 사본(snapshot)으로 남겨, 그 뒤 저장이 결과를 바꾸지 않는다.
    반환 {started, export}.
    """
    where = "vnew.edit.export"
    _plan, gen, gen_id = _load_gen(owner, pid)
    doc, doc_etag = _saved_doc(owner, pid, gen_id)
    if doc is None:
        raise VpocError(where, "EditNotSaved", "편집 문서를 먼저 저장해 주세요.", 409)
    if not isinstance(etag, str) or etag != doc_etag:
        raise _changed(where)
    variant = doc["variant"]
    allowed = {m["sourceKey"] for m in _all_items(gen, variant, _retake_state(owner, pid, gen_id))}
    referenced = [c.get("sourceKey") for c in doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, _ = _ensure_meta(owner, pid, gen_id, referenced)
    clips = _clean_clips(doc["clips"], allowed, {k: (meta.get(k) or {}).get("duration") for k in allowed}, where)
    if not clips:
        raise VpocError(where, "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
    doc_hash = _doc_hash(variant, clips)
    reconcile_export(owner, pid, gen_id)
    claim = secrets.token_hex(8)
    box: dict = {}

    def mutate(state):
        state = state if isinstance(state, dict) else _empty_state()
        current = state.get("current") if isinstance(state.get("current"), dict) else None
        if current and current.get("status") in _LIVE:
            if current.get("docHash") == doc_hash:
                box["reused"] = current
                return None
            raise VpocError(where, "ExportRunning", "다른 내보내기가 진행 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        history = [h for h in (state.get("history") or []) if isinstance(h, dict)]
        if current:
            history.append(_history_item(current))
        n = int(state.get("n") or 0) + 1
        now = now_iso()
        started = {
            "n": n, "status": "queued", "variant": variant, "docVersion": _doc_version(doc), "docHash": doc_hash,
            "clips": clips, "seconds": _total(clips), "claim": claim, "claimedAt": now, "heartbeatAt": now,
            "createdAt": now, "startedAt": "", "finishedAt": "", "attempts": 1,
            "progress": {"done": 0, "total": len(clips)}, "key": "", "bytes": 0, "hasAudio": None, "error": "",
        }
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
}


def _export_audio_from(owner: str, pid: str, gen_id: str, variant: str, clips: list[dict], meta: dict[str, dict]) -> dict[str, dict]:
    """내보내기 클립 원본 중 소리를 v1 에서 가져올 것(_audio_sources, 지시서 116 보완 10). 소리 없는 원본이 없으면 아무것도 읽지 않는다.

    gen 이 바뀌었으면(내보내기를 시작한 gen 이 아님) 대체하지 않는다. v1 길이·소리는 _ensure_meta(캐시 먼저)로 읽는다.
    """
    used = {c["sourceKey"] for c in clips if not c.get("muted")}
    if variant not in VARIANTS or not any((meta.get(k) or {}).get("hasAudio") is False for k in used):
        return {}
    retakes = _retake_state(owner, pid, gen_id)
    if retakes is None:
        return {}
    _plan, gen, now_gen = _load_gen(owner, pid)
    if now_gen != gen_id:
        return {}
    items = [m for m in _all_items(gen, variant, retakes) if m.get("group") == "current" or m["sourceKey"] in used]
    found = _audio_sources(gen, items, _retake_meta(owner, pid, gen_id, items, meta, probe=True))
    return {k: v for k, v in found.items() if k in used}


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
        audio_from = _export_audio_from(owner, pid, gen_id, str(current.get("variant") or ""), clips, meta)
        alt = [_clip_audio(c, audio_from.get(c["sourceKey"])) for c in clips]  # 지시서 116 보완 10: 클립별 대체 소리 (v1 시작, 끝)
        with_audio = any(not c.get("muted") and ((meta.get(c["sourceKey"]) or {}).get("hasAudio") or alt[i] is not None)
                         for i, c in enumerate(clips))
        last_use: dict[str, int] = {}
        for index, clip in enumerate(clips):
            last_use[clip["sourceKey"]] = index
            if alt[index] is not None:
                last_use[audio_from[clip["sourceKey"]]["sourceKey"]] = index
        if any(a is not None for a in alt):
            log.info("vnew.edit.export_audio_from pid=%s gen=%s n=%d clips=%d", pid, gen_id, n, sum(a is not None for a in alt))
        with tempfile.TemporaryDirectory(prefix="vnew-edit-export-") as workdir:
            local: dict[str, str] = {}
            segments: list[str] = []
            audio_path = os.path.join(workdir, "audio.raw") if with_audio else ""
            if audio_path:
                open(audio_path, "wb").close()
            last_beat = time.monotonic()
            for index, clip in enumerate(clips):
                clip_no = index + 1
                if time.monotonic() - attempt_started > EXPORT_MAX_S:
                    raise _ExportTimeout()
                source = clip["sourceKey"]
                info = meta.get(source) or {}
                path = local.get(source)
                if path is None:
                    stage = "download"
                    path = os.path.join(workdir, f"src-{index:03d}.mp4")
                    _download(source, path, bucket=vpoc_settings.VIDEO_BUCKET)
                    local[source] = path
                extra: dict = {}
                if audio_path and alt[index] is not None:
                    a_key = audio_from[source]["sourceKey"]
                    a_path = local.get(a_key)
                    if a_path is None:
                        stage = "download"
                        a_path = os.path.join(workdir, f"src-{index:03d}a.mp4")
                        _download(a_key, a_path, bucket=vpoc_settings.VIDEO_BUCKET)
                        local[a_key] = a_path
                    extra = {"audio_source": a_path, "audio_start_sec": alt[index][0], "audio_end_sec": alt[index][1]}
                stage = "clip"
                src_fps = _num(info.get("fps")) or float(fps)
                # 가장 가까운 프레임부터: 밀리초 반올림으로 프레임 시각을 조금 넘긴 in 이 한 프레임을 건너뛰지 않게 반 프레임 당긴다.
                start = max(0.0, float(clip["in"]) - 0.5 / src_fps)
                seg_video = os.path.join(workdir, f"seg-{index:03d}.mp4")
                seg_audio = os.path.join(workdir, f"seg-{index:03d}.raw") if audio_path else ""
                # 클립 인코딩(720p x264 ≈ 140MB, ⑤ 컷 자르기 trim 과 같은 크기)은 ⑤ 준비 단계와 같은 세마포어 안에서 한다
                # (같은 프로세스에서 ffmpeg 인코딩 두 개가 겹치지 않게, 512Mi). 클립 하나씩이라 ⑤ 제출을 오래 막지 않는다.
                generate._PREPARE_SEM.acquire()  # noqa: SLF001
                try:
                    _export_segment(
                        path, seg_video, start_sec=start, frames=frames[index], fps=fps, width=width, height=height,
                        audio_out=seg_audio,
                        audio_from_source=bool(audio_path) and not clip.get("muted") and (bool(info.get("hasAudio")) or bool(extra)),
                        speed=_speed_of(clip),  # 지시서 115: 영상 setpts·소리 atempo(1 이면 115 전 명령 그대로)
                        **extra,
                    )
                finally:
                    generate._PREPARE_SEM.release()  # noqa: SLF001
                if seg_audio:
                    _append_file(audio_path, seg_audio)
                    os.unlink(seg_audio)
                segments.append(seg_video)
                for used in dict.fromkeys([source] + ([audio_from[source]["sourceKey"]] if extra else [])):
                    if last_use[used] == index and used in local:
                        os.unlink(local.pop(used))
                if time.monotonic() - last_beat >= EXPORT_HEARTBEAT_S or index == len(clips) - 1:
                    _beat(owner, pid, gen_id, claim, progress={"done": index + 1, "total": len(clips)})
                    last_beat = time.monotonic()
            stage = "join"
            output = os.path.join(workdir, "edit.mp4")
            _export_join(segments, output, audio_path=audio_path)
            for segment in segments:
                os.unlink(segment)
            if audio_path:
                os.unlink(audio_path)
            out_info = _probe_file(output) or {}
            seconds = round(_num(out_info.get("duration")) or 0.0, 3)
            if seconds <= 0:
                raise stitcher.StitchError("이어 붙인 영상 길이를 읽지 못했습니다.")
            size = os.path.getsize(output)
            _beat(owner, pid, gen_id, claim)  # 올리기 전에 claim 확인
            stage = "upload"
            key = export_key(owner, pid, gen_id, n)
            _upload(output, key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")
        _finish(owner, pid, gen_id, claim, status="done", key=key, seconds=seconds, bytes=size, hasAudio=with_audio,
                finishedAt=now_iso(), error="")
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
