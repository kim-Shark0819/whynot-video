"""여러 영상 클립을 하나의 MP4로 이어 붙인다.

## 왜 필요한가

Bedrock에서 한 번에 만들 수 있는 영상은 최대 9초다(Luma Ray2). 1분이든 10분이든 긴 영상은
여러 컷을 만들어 이어 붙이는 수밖에 없다.

## 왜 ffmpeg를 pip로 넣는가

apt로 ffmpeg를 설치하면 slim 이미지에 200MB 이상이 붙는다. imageio-ffmpeg 휠에 들어 있는
정적 바이너리를 쓰면 훨씬 가볍고 Dockerfile에 apt 레이어를 추가하지 않아도 된다.

## 왜 재인코딩하지 않는가

모든 클립을 같은 모델, 같은 해상도, 같은 코덱으로 만들기 때문에 스트림 복사(-c copy)로 붙일 수
있다. 재인코딩하면 파드 CPU를 오래 잡고 화질도 떨어진다. 복사가 실패하는 경우에만 재인코딩으로
넘어간다(컷 사이 코덱 파라미터가 미묘하게 다를 수 있다).
"""

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger("toon-change-ai-pj-app.stitcher")

STITCH_TIMEOUT_SECONDS = int(os.getenv("STITCH_TIMEOUT_SECONDS", "600"))


class StitchError(RuntimeError):
    """이어 붙이기 실패. 사용자에게 보여줄 수 있는 메시지를 담는다."""


def ffmpeg_path() -> str:
    """사용할 ffmpeg 실행 파일 경로."""
    explicit = os.getenv("FFMPEG_BINARY")
    if explicit and os.path.exists(explicit):
        return explicit
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as error:  # noqa: BLE001 - 설치 형태가 환경마다 다르다
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise StitchError(f"ffmpeg를 찾을 수 없습니다: {error}")


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=STITCH_TIMEOUT_SECONDS,
        check=False,
    )


def _threads_args(threads: int | None) -> list[str]:
    """ffmpeg 입력 앞에 둘 `-threads N` 인자(설계자 2026-10-23, ⑤ OOM 완화).

    threads 가 None(기본)이면 빈 리스트 → 기존과 완전히 동일(ffmpeg 가 스스로 스레드 수를 고른다).
    vnew 컷 자르기처럼 호출자가 1 을 주면 `-threads 1` 로 ffmpeg 가 디코드·인코드에서 워커 스레드를
    덜 띄워 봉우리 메모리가 준다. 0 이하면 무시(기본과 같다).
    """
    if threads is None or int(threads) <= 0:
        return []
    return ["-threads", str(int(threads))]


def _audio_params(binary: str, path: str) -> tuple[int, str] | None:
    """(샘플레이트, 채널 레이아웃) 또는 오디오 없음이면 None. ffprobe 가 없어 ffmpeg -i 출력을 읽는다."""
    import re

    result = _run([binary, "-hide_banner", "-i", path])
    text = result.stderr.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "Stream #" in line and "Audio:" in line:
            match = re.search(r"(\d+) Hz, ([a-z0-9.()]+)", line)
            if match:
                layout = match.group(2).split("(")[0]
                return int(match.group(1)), ("stereo" if layout not in ("mono", "stereo") else layout)
            return 44100, "stereo"
    return None


def _match_audio(binary: str, paths: list[str], workdir: str) -> list[str]:
    """오디오가 있는 클립과 없는 클립이 섞이면 없는 쪽에 무음 트랙을 넣는다(설계자 2026-10-14).

    concat 은 첫 클립의 트랙 구성을 따른다. 실측: 소리 있음→없음 이면 오디오가 앞 클립 길이에서 끝나고,
    소리 없음→있음 이면 오디오 트랙이 통째로 사라졌다. 모두 있거나 모두 없으면 그대로 둔다.
    """
    params = [_audio_params(binary, path) for path in paths]
    present = [item for item in params if item]
    if not present or len(present) == len(paths):
        return paths
    rate, layout = present[0]
    out: list[str] = []
    for index, (path, param) in enumerate(zip(paths, params)):
        if param:
            out.append(path)
            continue
        padded = os.path.join(workdir, f"with-silence-{index:02d}.mp4")
        result = _run([
            binary, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
            "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={layout}",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", padded,
        ])
        if result.returncode != 0:
            raise StitchError("무음 트랙을 넣지 못했습니다: " + result.stderr.decode("utf-8", errors="replace")[:200])
        out.append(padded)
    return out


def concat(clip_paths: list[str], output_path: str) -> str:
    """클립들을 순서대로 이어 붙여 output_path에 쓴다.

    clip_paths의 순서가 그대로 영상 순서가 된다. 호출자가 컷 번호로 정렬해서 넘겨야 한다.
    """
    usable = [path for path in clip_paths if path and os.path.exists(path) and os.path.getsize(path) > 0]
    if not usable:
        raise StitchError("이어 붙일 클립이 없습니다.")
    if len(usable) == 1:
        shutil.copyfile(usable[0], output_path)
        return output_path

    binary = ffmpeg_path()
    padding_dir = tempfile.mkdtemp(prefix="stitch-audio-")
    usable = _match_audio(binary, usable, padding_dir)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as listing:
        for path in usable:
            # concat demuxer 형식. 경로의 작은따옴표는 이스케이프해야 한다.
            escaped = path.replace("'", "'\\''")
            listing.write(f"file '{escaped}'\n")
        list_path = listing.name

    try:
        copy_command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", "-movflags", "+faststart", output_path,
        ]
        result = _run(copy_command)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info("Concat by stream copy clips=%s bytes=%s", len(usable), os.path.getsize(output_path))
            return output_path

        logger.warning(
            "Stream copy concat failed (rc=%s), re-encoding: %s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[:300],
        )
        # 코덱 파라미터가 어긋난 경우를 위한 대비. 느리지만 확실하다.
        encode_command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", output_path,
        ]
        result = _run(encode_command)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise StitchError(f"영상을 이어 붙이지 못했습니다: {detail}")
        logger.info("Concat by re-encode clips=%s bytes=%s", len(usable), os.path.getsize(output_path))
        return output_path
    except subprocess.TimeoutExpired:
        raise StitchError("영상을 이어 붙이는 데 시간이 너무 오래 걸렸습니다.")
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
        shutil.rmtree(padding_dir, ignore_errors=True)


def trim(source_path: str, output_path: str, *, start_sec: float, end_sec: float, threads: int | None = None) -> str:
    """`[start_sec, end_sec)` 구간만 잘라 `output_path` 에 쓴다.

    ## 왜 필요한가

    모델에 요청하는 길이와 **의미가 성립하는 편집 구간**은 다르다 (PRD 11.1). 모델이 5초를
    주더라도 그 샷이 전달해야 하는 동작은 1.5초일 수 있고, 나머지는 군더더기다. 승인된
    `editStart`~`editEnd` 를 그대로 쓰지 않으면 계획과 결과물이 어긋난다.

    ## 왜 재인코딩하는가

    `concat` 과 반대다. 스트림 복사로 자르면 키프레임 경계로 스냅되어 **요청한 지점이 아닌
    곳**에서 잘린다. 5초 클립에서 1.5초를 정확히 떼려면 프레임 단위 정확도가 필요하다.
    클립이 짧아(5~9초) 재인코딩 비용이 크지 않다.

    구간이 클립 길이를 넘으면 클립 끝까지만 쓴다 — 없는 프레임을 만들어 채우지 않는다.
    """
    if not source_path or not os.path.exists(source_path):
        raise StitchError("자를 클립이 없습니다.")
    start = max(0.0, float(start_sec or 0.0))
    end = float(end_sec or 0.0)
    if end <= start:
        raise StitchError(f"편집 구간이 비어 있습니다: {start}~{end}")
    binary = ffmpeg_path()
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        # `-ss` 를 `-i` **앞**에 두면 빠르지만 키프레임으로 스냅한다. 정확도가 목적이라 뒤에 둔다.
        "-i", source_path, "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        # `-i` 앞 -threads 는 디코더 옵션이다. 인코더(libx264)에도 같은 값을 건다(073 R13.2, 리뷰 P-5).
        *_threads_args(threads),
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", output_path,
    ]
    result = _run(command)
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"클립을 자르지 못했습니다({start:.2f}~{end:.2f}s): {detail}")
    logger.info("Trim %.2f~%.2fs bytes=%s", start, end, os.path.getsize(output_path))
    return output_path


def last_frame_jpeg(path: str) -> bytes:
    """영상의 마지막 프레임을 JPEG 바이트로 뽑는다(설계자 2026-10-20).

    구간을 이어 만들 때 다음 구간의 첫 프레임(이미지→영상)으로 쓴다 — 앞 구간 실사 영상의
    마지막 프레임을 첫 프레임으로 넣으면 "배경·캐릭터·위치가 이어지는" 다음 영상을 만든다.

    `-sseof -0.1` 로 끝에서 0.1초 뒤로 이동해 마지막 프레임 1장(`-frames:v 1`)만 뽑는다. 재인코딩
    없이 프레임만 JPEG 로 쓰므로 빠르다. 실패하면 StitchError.
    """
    if not path or not os.path.exists(path):
        raise StitchError("마지막 프레임을 뽑을 영상이 없습니다.")
    binary = ffmpeg_path()
    with tempfile.TemporaryDirectory(prefix="last-frame-") as workdir:
        out_path = os.path.join(workdir, "last.jpg")
        command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-sseof", "-0.1", "-i", path,
            "-frames:v", "1", "-q:v", "2", out_path,
        ]
        result = _run(command)
        if result.returncode != 0 or not os.path.exists(out_path) or not os.path.getsize(out_path):
            # 짧은 영상에서 -sseof 가 빗나갈 수 있다. 처음부터 읽어 마지막 프레임을 고른다.
            fallback = [
                binary, "-y", "-hide_banner", "-loglevel", "error",
                "-i", path, "-vf", "select=eof", "-frames:v", "1", "-q:v", "2", out_path,
            ]
            result = _run(fallback)
            if result.returncode != 0 or not os.path.exists(out_path) or not os.path.getsize(out_path):
                detail = result.stderr.decode("utf-8", errors="replace")[:300]
                raise StitchError(f"마지막 프레임을 뽑지 못했습니다: {detail}")
        with open(out_path, "rb") as handle:
            return handle.read()


def probe_duration(path: str) -> float:
    """영상 길이(초). 실패하면 0을 돌려준다. 표시용이라 실패가 치명적이지 않다."""
    try:
        binary = ffmpeg_path()
    except StitchError:
        return 0.0
    result = _run([binary, "-hide_banner", "-i", path])
    text = result.stderr.decode("utf-8", errors="replace")
    marker = "Duration:"
    if marker not in text:
        return 0.0
    try:
        chunk = text.split(marker, 1)[1].split(",", 1)[0].strip()
        hours, minutes, seconds = chunk.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, IndexError):
        return 0.0


def probe_height(path: str) -> int:
    """영상 세로 픽셀(높이). 실패하면 0. fal video-to-video 는 높이 ≥720px 를 요구한다(설계자 2026-10-20).

    ffprobe 없이 ffmpeg -i 의 비디오 스트림 줄("… 1264x724 …")에서 WxH 를 읽는다.
    """
    import re

    try:
        binary = ffmpeg_path()
    except StitchError:
        return 0
    result = _run([binary, "-hide_banner", "-i", path])
    text = result.stderr.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "Stream #" in line and "Video:" in line:
            # 해상도는 "WxH" 로 나온다(SAR/DAR 의 괄호 표기는 제외하려 공백/쉼표 경계를 쓴다).
            match = re.search(r"(?<![\d])(\d{2,5})x(\d{2,5})(?![\d])", line)
            if match:
                return int(match.group(2))
    return 0


def scale_to_min_height(source_path: str, output_path: str, *, min_height: int = 720, threads: int | None = None) -> str:
    """영상 높이를 min_height 이상으로 키운다(설계자 2026-10-20).

    실측: 480p(864x496)는 fal video-to-video 가 422 video_too_small 로 거절한다.
    scale=-2:720:flags=lanczos 로 키우면 성공(출력 1264x724). -2 는 가로를 2의 배수로
    자동 맞춰 홀수 폭 인코딩 오류를 막는다. 이미 충분히 크면 그대로 복사한다.
    """
    if not source_path or not os.path.exists(source_path):
        raise StitchError("키울 영상이 없습니다.")
    binary = ffmpeg_path()
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", source_path,
        "-vf", f"scale=-2:{int(min_height)}:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *_threads_args(threads),  # 인코더 스레드(073 R13.2)
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", output_path,
    ]
    result = _run(command)
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"영상 높이를 키우지 못했습니다(→{min_height}p): {detail}")
    logger.info("Scale to %dp bytes=%s", min_height, os.path.getsize(output_path))
    return output_path


# 실사 → 애니 변환(fal video-to-video)에 보낼 수 있는 길이·높이(설계자 2026-10-20 실측).
ANIME_MIN_SECONDS = 3.0
ANIME_MAX_SECONDS = 15.0
ANIME_MIN_HEIGHT = 720


class AnimeTooShort(StitchError):
    """3초보다 짧아 변환할 수 없는 실사 영상."""


def prepare_for_anime(source_path: str, output_path: str, workdir: str) -> dict:
    """실사 mp4 를 fal video-to-video 가 받는 형태로 맞춘다(설계자 2026-10-20).

    규칙:
      - 길이 < 3초  → AnimeTooShort (변환하지 않는다 — anime 오류 "3초보다 짧아 변환할 수 없습니다")
      - 길이 > 15초 → 앞 15초만 자른다
      - 높이 < 720  → scale=-2:720 로 키운다

    돌려주는 dict: {"path": 준비된 파일 경로, "seconds": 준비된 길이(초), "height": 준비된 높이}.
    준비 과정에서 자르기/키우기가 없으면 원본 경로를 그대로 쓴다(복사하지 않는다).
    """
    if not source_path or not os.path.exists(source_path):
        raise StitchError("변환할 영상이 없습니다.")
    seconds = probe_duration(source_path)
    if seconds and seconds < ANIME_MIN_SECONDS:
        raise AnimeTooShort("3초보다 짧아 변환할 수 없습니다")

    current = source_path
    # 15초 초과면 앞 15초만. trim 은 재인코딩(프레임 정확)이라 높이 유지.
    if seconds and seconds > ANIME_MAX_SECONDS:
        trimmed = os.path.join(workdir, "anime-trim.mp4")
        trim(current, trimmed, start_sec=0.0, end_sec=ANIME_MAX_SECONDS)
        current = trimmed
        seconds = ANIME_MAX_SECONDS

    height = probe_height(current)
    if height and height < ANIME_MIN_HEIGHT:
        scaled = output_path
        scale_to_min_height(current, scaled, min_height=ANIME_MIN_HEIGHT)
        current = scaled
        height = ANIME_MIN_HEIGHT

    final_seconds = probe_duration(current) or seconds
    final_height = probe_height(current) or height
    return {"path": current, "seconds": final_seconds, "height": final_height}



def pad_to_seconds(source_path: str, output_path: str, *, target_sec: float, threads: int | None = None) -> str:
    """영상을 마지막 프레임 복제(freeze)로 `target_sec` 까지 늘린다(설계자 2026-10-22, ⑤ 컷 단위).

    ## 왜 필요한가

    컷 단위 생성에서 3초보다 짧은 컷은 영상 모델(fal video-to-video)이 거절한다(3~15초만 받음).
    그래서 짧은 컷을 마지막 프레임으로 3초까지 채워 제출하고, 결과는 합칠 때 원래 길이로 자른다.

    tpad=stop_mode=clone 으로 마지막 프레임을 복제해 늘린다(무음, 재인코딩). 원본이 이미 target
    이상이면 그대로 복사한다.
    """
    if not source_path or not os.path.exists(source_path):
        raise StitchError("길이를 채울 영상이 없습니다.")
    target = float(target_sec or 0.0)
    current = probe_duration(source_path)
    if target <= 0 or (current and current >= target - 0.05):
        shutil.copyfile(source_path, output_path)
        return output_path
    binary = ffmpeg_path()
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", source_path,
        "-vf", f"tpad=stop_mode=clone:stop_duration={max(0.0, target - (current or 0.0)):.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *_threads_args(threads),  # 인코더 스레드(073 R13.2)
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", output_path,
    ]
    result = _run(command)
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"영상 길이를 채우지 못했습니다(→{target:.2f}s): {detail}")
    logger.info("Pad to %.2fs bytes=%s", target, os.path.getsize(output_path))
    return output_path


# ================================================================ ⑦ 편집(지시서 110, 2026-11-01)
#
# 아래 함수는 vnew ⑦ 편집(app/src/vnew/edit.py)만 쓴다. 위 함수들의 동작은 바꾸지 않았다.
# - 클립마다 ffmpeg 한 번(edit_segment): 정해진 크기·fps·프레임 수로 다시 인코딩한 영상(소리 없음)과, 정확히 같은 길이의
#   원시 PCM(48kHz 스테레오 s16le). 조각들의 코덱 설정이 모두 같아 이어 붙이기(edit_join)는 스트림 복사이고, 소리는 PCM 을
#   이은 뒤 한 번만 AAC 로 인코딩한다. 그래서 클립 경계마다 영상·소리 길이가 프레임 단위로 맞고(밀림 없음), 클립이 많아도
#   전체 길이가 편집 문서의 합과 한 프레임 안에서 맞는다.
# - 파일 단위로만 다룬다(영상 바이트를 메모리에 모으지 않는다). ffmpeg 는 -threads 1, 리눅스면 nice 10 으로 띄워
#   같은 파드의 API 응답을 덜 막는다.

EDIT_AUDIO_RATE = 48000  # 편집 내보내기 소리 샘플레이트(Hz). 아래 fps 의 한 프레임이 정수 샘플이 된다.
EDIT_AUDIO_BYTES_PER_SAMPLE = 4  # s16le 스테레오 = 2바이트 × 2채널
EDIT_FPS_CHOICES = (12, 15, 16, 20, 24, 25, 30, 40, 48, 50, 60)  # 48000 을 나누어떨어지게 하는 fps
EDIT_PEAKS_RATE = 8000  # 파형 피크를 셀 때 디코드하는 샘플레이트(모노)
# 편집 클립 속도(지시서 115). 영상 setpts·소리 atempo(피치 유지)로 늘이고 줄인다. atempo 한 번이 받는 범위(0.5~2) 안이다.
EDIT_SPEED_CHOICES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def _edit_nice() -> list[str]:
    """리눅스(nice 가 있으면)에서 ffmpeg 를 nice 10 으로 띄운다. 그 밖(윈도 개발 PC 등)은 빈 리스트."""
    if os.name != "posix":
        return []
    found = shutil.which("nice")
    return [found, "-n", "10"] if found else []


def _edit_run(command: list[str]) -> subprocess.CompletedProcess:
    """편집용 ffmpeg 실행. 시간 초과(STITCH_TIMEOUT_SECONDS)면 StitchError."""
    try:
        return _run(_edit_nice() + command)
    except subprocess.TimeoutExpired:
        raise StitchError("영상 편집 처리 시간이 너무 오래 걸렸습니다.")


def probe_media(path: str) -> dict:
    """영상 한 개의 길이·화면 크기·fps·소리 유무(지시서 110). ffprobe 없이 ffmpeg -i 출력을 한 번 읽는다.

    반환 {"duration": 초, "width", "height", "fps", "hasAudio"}. 못 읽으면 duration 0 이다(호출자가 그 재료를 못 쓰는 것으로 본다).
    """
    import re

    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "hasAudio": False}
    if not path or not os.path.exists(path):
        return info
    try:
        binary = ffmpeg_path()
        result = _run([binary, "-hide_banner", "-i", path])
    except (StitchError, subprocess.TimeoutExpired):
        return info
    text = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        info["duration"] = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    for line in text.splitlines():
        if "Stream #" not in line:
            continue
        if "Video:" in line and not info["width"]:
            size = re.search(r"(?<![\d])(\d{2,5})x(\d{2,5})(?![\d])", line)
            if size:
                info["width"] = int(size.group(1))
                info["height"] = int(size.group(2))
            rate = re.search(r"(\d+(?:\.\d+)?) fps", line)
            if rate:
                info["fps"] = float(rate.group(1))
        elif "Audio:" in line:
            info["hasAudio"] = True
    return info


def _fit_pcm(path: str, size: int) -> None:
    """원시 PCM 파일을 정확히 size 바이트로 맞춘다(길면 자르고, 짧거나 없으면 0 = 무음으로 채운다). 1MB 씩 쓴다."""
    current = os.path.getsize(path) if os.path.exists(path) else 0
    with open(path, "r+b" if os.path.exists(path) else "wb") as handle:
        if current > size:
            handle.truncate(size)
            return
        handle.seek(0, os.SEEK_END)
        remaining = size - current
        zeros = b"\0" * min(remaining, 1 << 20)
        while remaining > 0:
            step = min(remaining, len(zeros))
            handle.write(zeros[:step])
            remaining -= step


def _edit_video_chain(start: float, speed: float, fps: int, width: int, height: int) -> str:
    """edit_segment 의 영상 필터 사슬(trim → setpts(속도) → fps → 비율 맞춰 줄이기·여백 → 끝 복제). 작업 181: edit_dissolve 도 같은
    사슬을 써서 디졸브 경계 프레임이 몸통 조각과 같은 프레임 격자에 놓인다(글자는 181 전 edit_segment 와 같다)."""
    return (
        f"trim=start={start:.3f},"
        + (f"setpts=(PTS-STARTPTS)/{speed:g}," if speed != 1.0 else "setpts=PTS-STARTPTS,")
        + f"fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
        "tpad=stop_mode=clone:stop_duration=1"
    )


def edit_segment(
    source_path: str,
    video_out: str,
    *,
    start_sec: float,
    frames: int,
    fps: int,
    width: int,
    height: int,
    audio_out: str = "",
    audio_from_source: bool = False,
    speed: float = 1.0,
    threads: int | None = 1,
    audio_source: str = "",
    audio_start_sec: float | None = None,
    audio_end_sec: float | None = None,
    skip_frames: int = 0,
) -> dict:
    """편집 내보내기의 클립 한 개(지시서 110). 원본의 start_sec 부터 frames 장을 width×height·fps 로 다시 인코딩한다.

    - 영상: trim(프레임 정확) → fps → 비율을 지켜 줄이고 남는 곳은 검은 여백 → 원본 끝이 모자라면 마지막 프레임 복제 →
      정확히 frames 장. 소리 트랙은 넣지 않는다.
    - 소리(audio_out 이 있을 때): audio_from_source 면 같은 구간을 48kHz 스테레오 s16le 원시 PCM 으로, 아니면 무음.
      어느 쪽이든 길이를 정확히 frames/fps 초(샘플 = frames × 48000 / fps)로 맞춘다(남으면 자르고 모자라면 무음).
    - speed(지시서 115, EDIT_SPEED_CHOICES): 영상 setpts=(PTS-STARTPTS)/speed, 소리 atempo=speed(피치 유지). 0.5 면 원본
      1초가 2초가 되고 2 면 원본 2초가 1초가 된다. frames·PCM 길이 규칙은 그대로다. 1 이면 명령이 115 전과 같다.
    - audio_source(지시서 116 보완 10, 소리 없는 다시 만든 버전): 소리를 그 파일(두 번째 입력)의 audio_start_sec~audio_end_sec
      (없으면 끝까지)에서 가져온다. atempo·길이 맞춤은 같다. 비어 있으면 명령이 이 보완 전과 같다.
    - skip_frames(작업 181, 디졸브 경계의 몸통 조각): 같은 영상 사슬의 앞 skip_frames 장을 건너뛰고 frames 장을 쓴다(그 장들은
      edit_dissolve 가 섞는다). 영상만 받는다(audio_out 과 함께 주면 StitchError). 0 이면 명령이 181 전과 같다.
    반환 {"video": video_out, "audio": audio_out 또는 "", "frames": frames, "samples": 샘플 수}.
    """
    import math

    if not source_path or not os.path.exists(source_path):
        raise StitchError("자를 클립이 없습니다.")
    frames = int(frames)
    fps = int(fps)
    width = int(width)
    height = int(height)
    start = float(start_sec or 0.0)
    speed = float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else 0.0
    if (
        frames < 1
        or fps not in EDIT_FPS_CHOICES
        or width < 2
        or height < 2
        or width % 2
        or height % 2
        or not math.isfinite(start)
        or start < 0
        or speed not in EDIT_SPEED_CHOICES
    ):
        raise StitchError("편집 구간 값이 올바르지 않습니다.")
    pcm_from_source = bool(audio_out) and bool(audio_from_source)
    alt_audio = pcm_from_source and bool(audio_source)
    if alt_audio:
        a_start = audio_start_sec if isinstance(audio_start_sec, (int, float)) and not isinstance(audio_start_sec, bool) else -1.0
        a_end = audio_end_sec if isinstance(audio_end_sec, (int, float)) and not isinstance(audio_end_sec, bool) else None
        if (
            not os.path.exists(audio_source)
            or not math.isfinite(a_start)
            or a_start < 0
            or (audio_end_sec is not None and (a_end is None or not math.isfinite(a_end) or a_end <= a_start))
        ):
            raise StitchError("편집 구간 값이 올바르지 않습니다.")
    skip = skip_frames if isinstance(skip_frames, int) and not isinstance(skip_frames, bool) else -1
    if skip < 0 or (skip and audio_out):
        # 작업 181: 앞 프레임 건너뛰기는 영상만(디졸브 경계의 몸통 조각). 소리는 edit_audio 가 따로 뽑는다.
        raise StitchError("편집 구간 값이 올바르지 않습니다.")
    seconds = frames / fps
    samples = frames * EDIT_AUDIO_RATE // fps
    binary = ffmpeg_path()
    retimed = speed != 1.0
    video_filter = _edit_video_chain(start, speed, fps, width, height)
    if skip:
        video_filter += f",trim=start_frame={skip},setpts=PTS-STARTPTS"
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", source_path,
        *(["-i", audio_source] if alt_audio else []),
        "-map", "0:v:0", "-vf", video_filter, "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *_threads_args(threads),
        "-pix_fmt", "yuv420p", video_out,
    ]
    if pcm_from_source:
        if alt_audio:
            trim = f"atrim=start={float(a_start):.3f}" + (f":end={float(a_end):.3f}" if a_end is not None else "")
        else:
            trim = f"atrim=start={start:.3f}"
        audio_filter = trim + ",asetpts=PTS-STARTPTS" + (f",atempo={speed:g}" if retimed else "")
        command += [
            "-map", "1:a:0" if alt_audio else "0:a:0", "-vn", "-af", audio_filter,
            "-t", f"{seconds:.6f}", "-ac", "2", "-ar", str(EDIT_AUDIO_RATE),
            "-c:a", "pcm_s16le", "-f", "s16le", audio_out,
        ]
    result = _edit_run(command)
    if result.returncode != 0 or not os.path.exists(video_out) or not os.path.getsize(video_out):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"클립을 편집하지 못했습니다({start:.2f}s, {frames}프레임): {detail}")
    if audio_out:
        if not pcm_from_source and os.path.exists(audio_out):
            os.unlink(audio_out)
        _fit_pcm(audio_out, samples * EDIT_AUDIO_BYTES_PER_SAMPLE)
    logger.info("Edit segment start=%.3fs frames=%d fps=%d size=%dx%d audio=%s speed=%g", start, frames, fps, width, height,
                "source" if pcm_from_source else ("silence" if audio_out else "none"), speed)
    return {"video": video_out, "audio": audio_out or "", "frames": frames, "samples": samples if audio_out else 0}


def edit_join(segment_paths: list[str], output_path: str, *, audio_path: str = "") -> str:
    """edit_segment 조각들을 순서대로 이어(스트림 복사) mp4 하나로 쓴다(지시서 110).

    audio_path(edit_segment 들이 쓴 원시 PCM 을 이은 파일)가 있으면 AAC 192k 로 넣고, 없으면 영상만 둔다.
    조각이 하나라도 비었으면 StitchError.
    """
    if not segment_paths or any(not path or not os.path.exists(path) or not os.path.getsize(path) for path in segment_paths):
        raise StitchError("이어 붙일 편집 조각이 없습니다.")
    if audio_path and not os.path.exists(audio_path):
        raise StitchError("편집 소리 파일이 없습니다.")
    binary = ffmpeg_path()
    list_path = output_path + ".txt"
    with open(list_path, "w", encoding="utf-8") as listing:
        for path in segment_paths:
            # concat demuxer 형식. 경로의 작은따옴표는 이스케이프해야 한다(concat 과 같다).
            escaped = path.replace("'", "'\\''")
            listing.write(f"file '{escaped}'\n")
    command = [binary, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path]
    if audio_path:
        command += [
            "-f", "s16le", "-ar", str(EDIT_AUDIO_RATE), "-ac", "2", "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        command += ["-map", "0:v:0", "-c:v", "copy"]
    command += ["-movflags", "+faststart", output_path]
    try:
        result = _edit_run(command)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"편집 영상을 이어 붙이지 못했습니다: {detail}")
    logger.info("Edit join segments=%d audio=%s bytes=%s", len(segment_paths), bool(audio_path), os.path.getsize(output_path))
    return output_path


# ---------------------------------------------------------------- 작업 181: 디졸브 · J/L 컷 소리
#
# 디졸브 경계(edit_dissolve): 나가는 클립의 마지막 frames 장과 들어오는 클립의 처음 frames 장을 xfade(fade)로 섞어 frames 장 한
# 조각으로 다시 인코딩한다. 두 입력은 edit_segment 와 같은 영상 사슬(_edit_video_chain)이라 프레임 격자가 같고, 몸통 조각
# (edit_segment skip_frames)과 코덱 설정이 같아 edit_join 의 스트림 복사 잇기가 그대로 된다. 한 프레임은 한 번만 인코딩된다.
# 소리(edit_audio · edit_mix): 클립마다 소리 창(앞당김 + 화면 길이 + 이어 감)을 원시 PCM 으로 뽑고, 타임라인 위치에 놓아 구간마다
# 직선 이득으로 섞는다(파일 단위, 겹치는 구간만 파이썬으로 계산하고 한 클립만 1.0 으로 들리는 구간은 바이트 복사).

EDIT_MIX_CHUNK = 24000  # 섞기 한 번에 다루는 스테레오 샘플 수(0.5초)


def edit_dissolve(
    a_path: str,
    b_path: str,
    video_out: str,
    *,
    a_start_sec: float,
    a_skip_frames: int,
    b_start_sec: float,
    frames: int,
    fps: int,
    width: int,
    height: int,
    a_speed: float = 1.0,
    b_speed: float = 1.0,
    threads: int | None = 1,
) -> dict:
    """디졸브 경계 조각(작업 181). A 사슬의 a_skip_frames 번째부터 frames 장과 B 사슬의 처음 frames 장을 xfade=transition=fade
    (duration = frames/fps, offset 0)로 섞어 frames 장을 쓴다(소리 없음). 사슬은 edit_segment 와 같다(start·speed·크기·fps).

    반환 {"video": video_out, "frames": frames}. 값이 틀리거나 ffmpeg 가 실패하면 StitchError.
    """
    import math

    def speed_of(value) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    def int_of(value) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    frames, skip = int_of(frames), int_of(a_skip_frames)
    fps, width, height = int_of(fps), int_of(width), int_of(height)
    a_speed, b_speed = speed_of(a_speed), speed_of(b_speed)
    starts = [float(a_start_sec or 0.0), float(b_start_sec or 0.0)]
    if (
        not a_path or not os.path.exists(a_path) or not b_path or not os.path.exists(b_path)
        or frames < 1 or skip < 0 or fps not in EDIT_FPS_CHOICES
        or width < 2 or height < 2 or width % 2 or height % 2
        or any(not math.isfinite(s) or s < 0 for s in starts)
        or a_speed not in EDIT_SPEED_CHOICES or b_speed not in EDIT_SPEED_CHOICES
    ):
        raise StitchError("편집 구간 값이 올바르지 않습니다.")
    binary = ffmpeg_path()
    # trim 뒤에는 프레임 레이트 정보가 사라져 xfade 가 거절하므로 fps 를 한 번 더 둔다(이미 그 격자라 프레임은 그대로).
    chain_a = (_edit_video_chain(starts[0], a_speed, fps, width, height)
               + f",trim=start_frame={skip}:end_frame={skip + frames},setpts=PTS-STARTPTS,fps={fps}")
    chain_b = (_edit_video_chain(starts[1], b_speed, fps, width, height)
               + f",trim=end_frame={frames},setpts=PTS-STARTPTS,fps={fps}")
    graph = (f"[0:v:0]{chain_a}[a];[1:v:0]{chain_b}[b];"
             f"[a][b]xfade=transition=fade:duration={frames / fps:.6f}:offset=0,format=yuv420p[v]")
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", a_path,
        *_threads_args(threads),
        "-i", b_path,
        "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *_threads_args(threads),
        "-pix_fmt", "yuv420p", video_out,
    ]
    result = _edit_run(command)
    if result.returncode != 0 or not os.path.exists(video_out) or not os.path.getsize(video_out):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"디졸브를 만들지 못했습니다({frames}프레임): {detail}")
    logger.info("Edit dissolve frames=%d fps=%d size=%dx%d skip=%d", frames, fps, width, height, skip)
    return {"video": video_out, "frames": frames}


def edit_audio(
    source_path: str,
    audio_out: str,
    *,
    start_sec: float,
    samples: int,
    speed: float = 1.0,
    end_sec: float | None = None,
    pad_front: int = 0,
    threads: int | None = 1,
) -> dict:
    """클립 소리 창 하나(작업 181). 48kHz 스테레오 s16le 원시 PCM 을 정확히 samples 개(스테레오 샘플) 쓴다.

    앞 pad_front 개는 무음(원본 0초보다 앞을 달라는 앞당김), 그 뒤는 원본 start_sec(~end_sec)에서 atempo=speed 로 뽑은 소리,
    원본이 모자라면 끝은 무음. 소리 필터는 edit_segment 와 같다(atrim → asetpts → atempo). 반환 {"audio", "samples"}.
    """
    import math

    samples = samples if isinstance(samples, int) and not isinstance(samples, bool) else -1
    pad = pad_front if isinstance(pad_front, int) and not isinstance(pad_front, bool) else -1
    speed = float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else 0.0
    start = float(start_sec) if isinstance(start_sec, (int, float)) and not isinstance(start_sec, bool) else -1.0
    end = float(end_sec) if isinstance(end_sec, (int, float)) and not isinstance(end_sec, bool) else None
    if (
        not source_path or not os.path.exists(source_path) or not audio_out
        or samples < 1 or pad < 0 or pad > samples or speed not in EDIT_SPEED_CHOICES
        or not math.isfinite(start) or start < 0
        or (end_sec is not None and (end is None or not math.isfinite(end) or end <= start))
    ):
        raise StitchError("편집 구간 값이 올바르지 않습니다.")
    size = samples * EDIT_AUDIO_BYTES_PER_SAMPLE
    body = audio_out + ".body"
    if samples > pad:
        trim = f"atrim=start={start:.3f}" + (f":end={end:.3f}" if end is not None else "")
        audio_filter = trim + ",asetpts=PTS-STARTPTS" + (f",atempo={speed:g}" if speed != 1.0 else "")
        command = [
            ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
            *_threads_args(threads),
            "-i", source_path,
            "-map", "0:a:0", "-vn", "-af", audio_filter,
            "-t", f"{(samples - pad) / EDIT_AUDIO_RATE:.6f}", "-ac", "2", "-ar", str(EDIT_AUDIO_RATE),
            "-c:a", "pcm_s16le", "-f", "s16le", body,
        ]
        result = _edit_run(command)
        if result.returncode != 0 or not os.path.exists(body):
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise StitchError(f"클립 소리를 뽑지 못했습니다({start:.2f}s): {detail}")
    try:
        with open(audio_out, "wb") as out:
            remaining = pad * EDIT_AUDIO_BYTES_PER_SAMPLE
            zeros = b"\0" * min(remaining, 1 << 20)
            while remaining > 0:
                step = min(remaining, len(zeros))
                out.write(zeros[:step])
                remaining -= step
            if os.path.exists(body):
                limit = size - pad * EDIT_AUDIO_BYTES_PER_SAMPLE
                with open(body, "rb") as src:
                    while limit > 0:
                        block = src.read(min(limit, 1 << 20))
                        if not block:
                            break
                        out.write(block)
                        limit -= len(block)
    finally:
        if os.path.exists(body):
            os.unlink(body)
    _fit_pcm(audio_out, size)
    return {"audio": audio_out, "samples": samples}


def _mix_envelope_ok(layer, total: int) -> bool:
    if not isinstance(layer, dict) or not os.path.exists(str(layer.get("path") or "")):
        return False
    at = layer.get("at")
    if not isinstance(at, int) or isinstance(at, bool):
        return False
    length = os.path.getsize(layer["path"]) // EDIT_AUDIO_BYTES_PER_SAMPLE
    for seg in layer.get("envelope") or []:
        if not isinstance(seg, (list, tuple)) or len(seg) != 4:
            return False
        t0, t1, g0, g1 = seg
        if not (isinstance(t0, int) and isinstance(t1, int)) or t1 <= t0 or t0 < at or t1 > at + length:
            return False
        if not all(isinstance(g, (int, float)) and not isinstance(g, bool) and 0.0 <= g <= 1.0 for g in (g0, g1)):
            return False
    return True


def edit_mix(layers: list[dict], output_path: str, *, samples: int) -> dict:
    """타임라인 소리 섞기(작업 181). 결과는 정확히 samples 개(스테레오 샘플)의 48kHz s16le 원시 PCM.

    layers 항목 {"path": edit_audio 결과, "at": 그 파일 0번 샘플의 타임라인 위치(음수 가능), "envelope": [(t0, t1, g0, g1), …]}.
    envelope 는 타임라인 샘플 [t0, t1) 에서 이득을 g0 → g1 로 직선으로 바꾼다(그 밖은 0, 구간끼리 겹치지 않는다). 타임라인 [0, samples)
    밖은 버린다. 구간 경계마다 나눠: 들리는 층이 없으면 무음, 하나가 1.0 그대로면 바이트 복사, 그 밖은 더해 s16 범위로 자른다
    (넘친 값이 반대 부호로 감기지 않는다). 반환 {"audio": output_path, "samples": samples, "mixed": 계산한 샘플 수}.
    """
    import array
    import sys

    samples = samples if isinstance(samples, int) and not isinstance(samples, bool) else -1
    if samples < 1 or not isinstance(layers, list) or any(not _mix_envelope_ok(layer, samples) for layer in layers):
        raise StitchError("편집 소리 섞기 값이 올바르지 않습니다.")
    width = EDIT_AUDIO_BYTES_PER_SAMPLE
    segments = []  # (t0, t1, g0, g1, path, at)
    points = {0, samples}
    for layer in layers:
        for t0, t1, g0, g1 in layer.get("envelope") or []:
            if g0 == 0 and g1 == 0:
                continue
            segments.append((t0, t1, float(g0), float(g1), layer["path"], layer["at"]))
            points.update(p for p in (t0, t1) if 0 < p < samples)
    bounds = sorted(points)
    swap = sys.byteorder == "big"
    mixed = 0

    def read(path: str, offset: int, count: int) -> bytes:
        with open(path, "rb") as handle:
            handle.seek(offset * width)
            data = handle.read(count * width)
        return data + b"\0" * (count * width - len(data))

    with open(output_path, "wb") as out:
        for a, b in zip(bounds, bounds[1:]):
            active = [s for s in segments if s[0] <= a and s[1] >= b]
            if not active:
                remaining = (b - a) * width
                zeros = b"\0" * min(remaining, 1 << 20)
                while remaining > 0:
                    step = min(remaining, len(zeros))
                    out.write(zeros[:step])
                    remaining -= step
                continue
            if len(active) == 1 and active[0][2] == 1.0 and active[0][3] == 1.0:
                _t0, _t1, _g0, _g1, path, at = active[0]
                position = a
                while position < b:
                    step = min(b - position, 1 << 18)
                    out.write(read(path, position - at, step))
                    position += step
                continue
            position = a
            while position < b:
                count = min(b - position, EDIT_MIX_CHUNK)
                acc = None
                for t0, t1, g0, g1, path, at in active:
                    data = array.array("h")
                    data.frombytes(read(path, position - at, count))
                    if swap:
                        data.byteswap()
                    slope = (g1 - g0) / (t1 - t0)
                    first = g0 + slope * (position - t0)
                    if slope == 0.0:
                        values = [v * first for v in data]
                    else:
                        values = [v * (first + slope * (i >> 1)) for i, v in enumerate(data)]
                    acc = values if acc is None else [x + y for x, y in zip(acc, values)]
                block = array.array("h", [32767 if v >= 32767 else (-32768 if v <= -32768 else int(round(v))) for v in acc])
                if swap:
                    block.byteswap()
                out.write(block.tobytes())
                mixed += count
                position += count
    _fit_pcm(output_path, samples * width)
    logger.info("Edit mix layers=%d samples=%d mixed=%d", len(layers), samples, mixed)
    return {"audio": output_path, "samples": samples, "mixed": mixed}


def filmstrip_jpeg(
    source_path: str, output_path: str, *, interval: float, tiles: int, tile_height: int = 72, threads: int | None = 1,
) -> str:
    """필름스트립(지시서 110). 영상에서 interval 초마다 한 장씩 tiles 장을 높이 tile_height 로 줄여 가로 한 줄 JPEG 로 쓴다.

    영상이 짧아 장이 모자라면 남는 칸은 ffmpeg tile 이 검게 채운다. 실패하면 StitchError.
    """
    import math

    if not source_path or not os.path.exists(source_path):
        raise StitchError("필름스트립을 만들 영상이 없습니다.")
    interval = float(interval)
    tiles = int(tiles)
    tile_height = int(tile_height)
    if not math.isfinite(interval) or interval <= 0 or not 1 <= tiles <= 500 or not 16 <= tile_height <= 360 or tile_height % 2:
        raise StitchError("필름스트립 값이 올바르지 않습니다.")
    binary = ffmpeg_path()
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", source_path,
        "-map", "0:v:0", "-an",
        "-vf", f"fps=1/{interval:.3f},scale=-2:{tile_height},tile={tiles}x1",
        "-frames:v", "1", "-q:v", "5", output_path,
    ]
    result = _edit_run(command)
    if result.returncode != 0 or not os.path.exists(output_path) or not os.path.getsize(output_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"필름스트립을 만들지 못했습니다: {detail}")
    return output_path


def audio_peaks(source_path: str, workdir: str, *, per_second: int = 20, threads: int | None = 1) -> list[float]:
    """소리 파형 피크(지시서 110). 1초에 per_second 개, 값은 그 구간 최대 절댓값 / 32768(0~1, 소수 3자리).

    소리를 8kHz 모노 원시 PCM 파일로 풀고(workdir 안) 구간마다 읽어 센다(전부를 메모리에 올리지 않는다).
    소리 스트림이 없거나 못 풀면 StitchError(호출자가 소리 유무를 먼저 본다).
    """
    import array
    import sys

    per_second = int(per_second)
    if per_second < 1 or EDIT_PEAKS_RATE % per_second:
        raise StitchError("파형 피크 간격이 올바르지 않습니다.")
    if not source_path or not os.path.exists(source_path):
        raise StitchError("파형을 만들 영상이 없습니다.")
    binary = ffmpeg_path()
    raw_path = os.path.join(workdir, "peaks.raw")
    command = [
        binary, "-y", "-hide_banner", "-loglevel", "error",
        *_threads_args(threads),
        "-i", source_path,
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(EDIT_PEAKS_RATE), "-c:a", "pcm_s16le", "-f", "s16le", raw_path,
    ]
    result = _edit_run(command)
    if result.returncode != 0 or not os.path.exists(raw_path):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise StitchError(f"파형을 만들지 못했습니다: {detail}")
    block_bytes = (EDIT_PEAKS_RATE // per_second) * 2
    peaks: list[float] = []
    try:
        with open(raw_path, "rb") as handle:
            while True:
                block = handle.read(block_bytes)
                if len(block) < 2:
                    break
                if len(block) % 2:
                    block = block[:-1]
                samples = array.array("h")
                samples.frombytes(block)
                if sys.byteorder == "big":
                    samples.byteswap()
                peak = max(max(samples), -min(samples))
                peaks.append(round(min(1.0, peak / 32768.0), 3))
    finally:
        try:
            os.unlink(raw_path)
        except OSError:
            pass
    return peaks
