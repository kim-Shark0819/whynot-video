"""작업 215c: ⑦ 편집 프리미어식 도구의 서버 쪽(클립·문서 새 값 검사, 해시, 내보내기 준비).

새 값은 모두 기본값이 아닐 때만 저장한다. 그래서 215c 전 문서는 저장 모양·해시·내보내기 명령이 그대로다.
클립(edit._clean_clips 가 clean_extras 를 부른다):
  gapBefore(빈틈 초, 들어내기·범위 들어내기가 남긴다 — 클립 앞 검은 화면·무음), disabled(사용 안 함 — 자리는 그대로, 검은 화면·무음),
  reverse(역재생, 원본 구간 REVERSE_MAX_S 초 이하), keepPitch(false 만 저장 — 속도를 바꿔도 음높이를 지키지 않는다),
  gain(dB, GAIN_MIN_DB~GAIN_MAX_DB, 0.5 단위), labelColor(라벨 색, COLORS 중 하나 — color 는 215b 색 보정 {..}이고,
  예전 문서의 color 글자는 edit_text.clean_clip_fx 가 labelColor 로 옮긴다), unlinked(소리 연결 해제 — 화면 전용, 내보내기와 무관).
문서(edit.save_doc 가 clean_doc_extras 를 부른다): markers [{id, t, name, color}], tracks {v1|a1: {locked, hidden}}.
  숨긴 트랙은 내보내기에서 빠진다(apply_tracks: v1 숨김 = 화면 검게, a1 숨김 = 소리 끔). 잠금은 화면 전용.
내보내기(edit.run_export 가 export_plan → export_prepare 를 부른다): 빈틈은 검은 클립, 사용 안 함·숨긴 화면·역재생·게인·음높이는
  그 클립만 쓰는 파생 원본(임시 디렉터리)으로 바꿔 나머지 경로(속도·디졸브·J/L 컷·대사 타이밍)를 그대로 쓴다.
  역재생은 ffmpeg reverse(REVERSE_CHUNK_S 초 조각마다 — 파드 메모리 512Mi)와 areverse. 바뀐 원본은 지시서 116 보완 10 의 v1 소리
  대체를 쓰지 않는다(파생 원본 자체 소리).
"""

from __future__ import annotations

import math
import os
import re
import subprocess

from .. import stitcher
from ..vpoc.errors import VpocError

GAP_MAX_S = 300.0
GAIN_MIN_DB = -24.0
GAIN_MAX_DB = 12.0
GAIN_STEP_DB = 0.5
REVERSE_MAX_S = 15.0
REVERSE_MARGIN_S = 4.0  # 앞당김·이어 감·대사 타이밍(최대 2초 × 속도 2)이 읽을 원본 여유
REVERSE_CHUNK_S = 2.0
COLORS = ("red", "orange", "yellow", "green", "teal", "blue", "violet", "pink", "gray")
MAX_MARKERS = 100
MAX_MARKER_NAME = 40
TRACKS = ("v1", "a1")
FFMPEG_TIMEOUT_S = 600
BLACK_KEY = "215c:black"
CLIP_KEYS = ("gapBefore", "disabled", "reverse", "keepPitch", "gain", "labelColor", "unlinked", "hideVideo")
_MARKER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def gap_of(clip) -> float:
    value = _num(clip.get("gapBefore")) if isinstance(clip, dict) else None
    return value if value is not None and 0 < value <= GAP_MAX_S + 1e-9 else 0.0


def gain_of(clip) -> float:
    value = _num(clip.get("gain")) if isinstance(clip, dict) else None
    return value if value is not None and GAIN_MIN_DB <= value <= GAIN_MAX_DB else 0.0


def gap_total(clips) -> float:
    return sum(gap_of(c) for c in clips or [] if isinstance(c, dict))


def clean_extras(raw: dict, item: dict, number: int, where: str, source_len: float) -> float:
    """클립 한 개의 215c 값을 검사해 item 에 넣는다(기본값은 넣지 않는다). 반환 빈틈 초(전체 길이 한도에 더한다)."""
    gap = raw.get("gapBefore")
    seconds = 0.0
    if gap is not None:
        value = _num(gap)
        if value is None or value < 0 or value > GAP_MAX_S + 1e-9:
            raise VpocError(where, "BadClip", f"{number}번째 클립의 앞 빈틈(gapBefore)은 0~300초여야 합니다.", 400)
        seconds = round(value, 3)
        if seconds:
            item["gapBefore"] = seconds
    for key in ("disabled", "reverse", "unlinked"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, bool):
            raise VpocError(where, "BadClip", f"{number}번째 클립의 사용 안 함·역재생·소리 연결 해제 값이 올바르지 않습니다.", 400)
        if value:
            item[key] = True
    pitch = raw.get("keepPitch")
    if pitch is not None and not isinstance(pitch, bool):
        raise VpocError(where, "BadClip", f"{number}번째 클립의 음높이 유지(keepPitch) 값이 올바르지 않습니다.", 400)
    if pitch is False:
        item["keepPitch"] = False
    if item.get("reverse") and source_len > REVERSE_MAX_S + 1e-6:
        raise VpocError(where, "BadClipRange",
                        f"{number}번째 클립은 역재생할 수 없습니다. 역재생은 원본 구간 15초 이하 클립만 됩니다. 클립을 나누거나 줄여 주세요.",
                        400)
    gain = raw.get("gain")
    if gain is not None:
        value = _num(gain)
        steps = value / GAIN_STEP_DB if value is not None else 0.5
        if value is None or not GAIN_MIN_DB <= value <= GAIN_MAX_DB or abs(steps - round(steps)) > 1e-6:
            raise VpocError(where, "BadClip", f"{number}번째 클립의 오디오 게인(gain)은 -24~12dB, 0.5dB 단위여야 합니다.", 400)
        if value:
            item["gain"] = round(round(steps) * GAIN_STEP_DB, 1)
    color = raw.get("labelColor")
    if color is not None:
        if color not in COLORS:
            raise VpocError(where, "BadClip", f"{number}번째 클립의 라벨 색(color)이 올바르지 않습니다.", 400)
        item["labelColor"] = color
    if raw.get("hideVideo") is True:  # apply_tracks 가 만든 내보내기 사본만(화면은 보내지 않는다)
        item["hideVideo"] = True
    return seconds


def check_rules(clips: list[dict], where: str) -> None:
    """자리 규칙: 빈틈 바로 앞 클립의 디졸브는 빈틈 길이의 45% 이하(검은 화면으로 녹아드는 길이를 내보내기가 줄이지 않게)."""
    for number, (item, after) in enumerate(zip(clips, clips[1:]), 1):
        tr = item.get("transition") or {}
        gap = gap_of(after)
        if gap and tr.get("type") == "dissolve" and float(tr.get("seconds") or 0) > 0.45 * gap + 1e-9:
            raise VpocError(where, "BadTransition", f"{number}번째 클립의 디졸브는 뒤 빈틈 길이의 45% 이하여야 합니다.", 400)


def hash_extras(clip: dict, item: dict) -> None:
    """내보내기 결과를 바꾸는 215c 값만 해시 몸에 넣는다(없으면 215c 전 해시 그대로). 색·연결 해제는 넣지 않는다."""
    if gap_of(clip):
        item["gapBefore"] = gap_of(clip)
    for key in ("disabled", "reverse", "hideVideo"):
        if clip.get(key) is True:
            item[key] = True
    if clip.get("keepPitch") is False:
        item["keepPitch"] = False
    if gain_of(clip):
        item["gain"] = gain_of(clip)


def clean_doc_extras(payload: dict, where: str) -> dict:
    """문서의 markers·tracks 를 검사해 저장할 꼴로(비었으면 넣지 않는다 — 215c 전 문서 모양 그대로)."""
    out: dict = {}
    raw = payload.get("markers")
    if raw is not None:
        if not isinstance(raw, list) or len(raw) > MAX_MARKERS:
            raise VpocError(where, "BadMarkers", f"마커(markers)는 {MAX_MARKERS}개까지의 목록이어야 합니다.", 400)
        markers, seen = [], set()
        for number, mark in enumerate(raw, 1):
            if not isinstance(mark, dict):
                raise VpocError(where, "BadMarkers", f"{number}번째 마커 형식이 올바르지 않습니다.", 400)
            mid, t = mark.get("id"), _num(mark.get("t"))
            if not isinstance(mid, str) or not _MARKER_ID_RE.match(mid) or mid in seen:
                raise VpocError(where, "BadMarkers", f"{number}번째 마커 id 가 올바르지 않습니다.", 400)
            if t is None or t < 0 or t > GAP_MAX_S + 1e-9:
                raise VpocError(where, "BadMarkers", f"{number}번째 마커 시각이 올바르지 않습니다.", 400)
            color = mark.get("color", "green")
            if color not in COLORS:
                raise VpocError(where, "BadMarkers", f"{number}번째 마커 색이 올바르지 않습니다.", 400)
            name = mark.get("name") if isinstance(mark.get("name"), str) else ""
            seen.add(mid)
            markers.append({"id": mid, "t": round(t, 3), "name": _CONTROL_RE.sub("", name).strip()[:MAX_MARKER_NAME],
                            "color": color})
        if markers:
            out["markers"] = sorted(markers, key=lambda m: m["t"])
    tracks = payload.get("tracks")
    if tracks is not None:
        if not isinstance(tracks, dict) or any(k not in TRACKS for k in tracks):
            raise VpocError(where, "BadTracks", "트랙 설정(tracks)이 올바르지 않습니다.", 400)
        clean: dict = {}
        for key, value in tracks.items():
            if not isinstance(value, dict) or any(f not in ("locked", "hidden") or not isinstance(v, bool) for f, v in value.items()):
                raise VpocError(where, "BadTracks", "트랙 설정(tracks)이 올바르지 않습니다.", 400)
            flags = {f: True for f, v in value.items() if v}
            if flags:
                clean[key] = flags
        if clean:
            out["tracks"] = clean
    return out


def apply_tracks(clips: list[dict], doc: dict | None) -> list[dict]:
    """숨긴 트랙을 내보내기 사본에 반영한다(v1 숨김 → hideVideo, a1 숨김 → muted). 숨긴 트랙이 없으면 받은 목록 그대로."""
    tracks = (doc or {}).get("tracks") if isinstance(doc, dict) else None
    tracks = tracks if isinstance(tracks, dict) else {}
    hide_v = (tracks.get("v1") or {}).get("hidden") is True
    hide_a = (tracks.get("a1") or {}).get("hidden") is True
    if not hide_v and not hide_a:
        return clips
    out = []
    for clip in clips:
        clip = dict(clip)
        if hide_v:
            clip["hideVideo"] = True
        if hide_a:
            clip["muted"] = True
        out.append(clip)
    return out


def _needs(clip: dict, info: dict) -> bool:
    if clip.get("disabled") or clip.get("hideVideo") or clip.get("reverse"):
        return True
    sound = not clip.get("muted") and bool(info.get("hasAudio"))
    speed = _num(clip.get("speed")) or 1.0
    return sound and (bool(gain_of(clip)) or (clip.get("keepPitch") is False and speed != 1))


def export_plan(clips: list[dict], meta: dict, fps: int, width: int, height: int):
    """내보내기 클립 목록을 215c 값 없이 다시 쓴다. 반환 (클립, meta, plan). 215c 값이 하나도 없으면 (받은 그대로, meta, None).

    빈틈 → 검은 클립(BLACK_KEY, 소리 없음). 사용 안 함 → 검은 클립(자리 그대로, 소리 끔). 그 밖(숨긴 화면·역재생·게인·음높이) →
    클립마다 파생 원본(plan["derived"][키]), 역재생은 in/out 을 파생 원본 시각으로 바꾼다.
    """
    if not any(isinstance(c, dict) and any(k in c for k in CLIP_KEYS) for c in clips):
        return clips, meta, None
    meta = dict(meta)
    run: list[dict] = []
    plan: dict = {"black": 0.0, "derived": {}, "used": set()}
    for index, clip in enumerate(clips):
        gap = gap_of(clip)
        if gap:
            run.append({"id": f"gap{index}", "sourceKey": BLACK_KEY, "in": 0.0, "out": gap, "muted": True})
            plan["black"] = max(plan["black"], gap)
        item = {k: v for k, v in clip.items() if k not in CLIP_KEYS}
        info = meta.get(clip["sourceKey"]) or {}
        length = float(clip["out"]) - float(clip["in"])
        if clip.get("disabled"):
            item.update(sourceKey=BLACK_KEY, muted=True, out=round(length, 3), **{"in": 0.0})
            plan["black"] = max(plan["black"], length)
        elif _needs(clip, info):
            key = f"215c:d{index}"
            duration = float(info.get("duration") or clip["out"])
            spec = {"from": clip["sourceKey"], "black": bool(clip.get("hideVideo")), "reverse": bool(clip.get("reverse")),
                    "gain": gain_of(clip), "audio": not clip.get("muted") and bool(info.get("hasAudio")),
                    "varispeed": (_num(clip.get("speed")) or 1.0) if clip.get("keepPitch") is False else 1.0,
                    "a": 0.0, "b": duration}
            if spec["reverse"]:
                spec["a"] = max(0.0, float(clip["in"]) - REVERSE_MARGIN_S)
                spec["b"] = min(duration, float(clip["out"]) + REVERSE_MARGIN_S)
                item["in"] = round(spec["b"] - float(clip["out"]), 3)
                item["out"] = round(spec["b"] - float(clip["in"]), 3)
            plan["derived"][key] = spec
            item["sourceKey"] = key
            meta[key] = {"duration": round(spec["b"] - spec["a"], 3), "fps": fps, "width": width, "height": height,
                         "hasAudio": spec["audio"]}
        run.append(item)
    if plan["black"]:
        meta[BLACK_KEY] = {"duration": round(plan["black"] + 1.0, 3), "fps": fps, "width": width, "height": height,
                           "hasAudio": False}
    plan["used"] = {c["sourceKey"] for c in run}
    return run, meta, plan


def _run_ffmpeg(command: list[str]) -> None:
    """ffmpeg 한 번(검사가 가짜로 바꾼다). 실패하면 StitchError(내보내기 '클립을 자르지 못했습니다')."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise stitcher.StitchError("영상 편집 처리 시간이 너무 오래 걸렸습니다.") from exc
    if result.returncode != 0:
        raise stitcher.StitchError("파생 원본을 만들지 못했습니다: " + result.stderr.decode("utf-8", "replace")[:300])


def _fit(width: int, height: int) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p")


_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]


def _audio_filter(spec: dict) -> str:
    parts = ["areverse"] if spec["reverse"] else []
    speed = spec["varispeed"]
    if speed != 1:  # 음높이를 지키지 않는 속도: 뒤의 atempo(speed)와 합쳐 바늘 속도처럼 음높이도 바뀐다
        parts.append(f"aresample=48000,asetrate={48000 * speed:g},aresample=48000,atempo={1 / speed:.6g}")
    if spec["gain"]:
        parts.append(f"volume={spec['gain']:g}dB")
    return ",".join(parts) or "anull"


def export_prepare(plan: dict | None, local: dict, fetch, workdir: str, *, fps: int, width: int, height: int) -> None:
    """plan 의 검은 원본·파생 원본을 workdir 에 만들고 local[키] 에 둔다. 다 만든 뒤 파생에만 쓰인 원본 파일은 지운다."""
    if not plan:
        return
    binary = stitcher.ffmpeg_path()
    base = [binary, "-y", "-hide_banner", "-loglevel", "error", "-threads", "1"]
    if plan["black"]:
        path = os.path.join(workdir, "215c-black.mp4")
        _run_ffmpeg(base + ["-f", "lavfi", "-i", f"color=black:s={width}x{height}:r={fps}:d={plan['black'] + 1.0:.3f}",
                            "-an", *_X264, path])
        local[BLACK_KEY] = path
    origins: set[str] = set()
    for number, (key, spec) in enumerate(sorted(plan["derived"].items())):
        source = fetch(spec["from"], f"215c-src-{number:03d}.mp4")
        origins.add(spec["from"])
        out = os.path.join(workdir, f"215c-d{number:03d}.mp4")
        a, b = spec["a"], spec["b"]
        audio_args: list[str] = []
        if spec["audio"]:
            audio_args = ["-af", _audio_filter(spec), "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "192k"]
        if spec["black"]:
            inputs = ["-f", "lavfi", "-i", f"color=black:s={width}x{height}:r={fps}:d={b - a:.3f}"]
            if spec["audio"]:
                inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", source, "-map", "0:v:0", "-map", "1:a:0"]
            _run_ffmpeg(base + inputs + ["-t", f"{b - a:.3f}", *_X264, *(audio_args or ["-an"]), out])
        elif spec["reverse"]:
            # 화면: REVERSE_CHUNK_S 초 조각마다 거꾸로(reverse 는 조각 전체를 메모리에 둔다), 뒤 조각부터 잇는다.
            pieces, start, k = [], a, 0
            while start < b - 1e-6:
                end = min(b, start + REVERSE_CHUNK_S)
                piece = os.path.join(workdir, f"215c-d{number:03d}-r{k:03d}.mp4")
                _run_ffmpeg(base + ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", source, "-an",
                                    "-vf", f"fps={fps},{_fit(width, height)},reverse", *_X264, piece])
                pieces.append(piece)
                start, k = end, k + 1
            listing = out + ".txt"
            with open(listing, "w", encoding="utf-8") as handle:
                for piece in reversed(pieces):
                    handle.write("file '" + piece.replace("'", "'\\''") + "'\n")
            mux = base + ["-f", "concat", "-safe", "0", "-i", listing]
            if spec["audio"]:
                mux += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", source, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                        *audio_args]
            else:
                mux += ["-map", "0:v:0", "-c:v", "copy", "-an"]
            _run_ffmpeg(mux + [out])
            for piece in pieces:
                os.unlink(piece)
            os.unlink(listing)
        else:  # 소리만(게인·음높이): 화면은 복사
            _run_ffmpeg(base + ["-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", *audio_args, out])
        local[key] = out
    for origin in origins - plan["used"]:
        path = local.pop(origin, None)
        if path and os.path.exists(path):
            os.unlink(path)
