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
