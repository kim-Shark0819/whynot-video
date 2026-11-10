"""⑦ 편집 → Premiere Pro 프로젝트 내보내기(작업 215d). 백엔드만(화면은 다른 작업).

그림체(variant)마다 저장된 ⑦ 편집 문서(edit.py)를 Premiere Pro 가 가져오는 두 형식으로 옮긴다:
  - build_xmeml: Final Cut Pro 7 XML(xmeml version="4"). V1 = 클립(자르기·속도 = Time Remap·교차 디졸브),
    A1/A2 = 클립 소리 왼쪽·오른쪽(J·L 컷·대사 타이밍은 소리 클립의 in/out·start/end 로 따로 — 앞뒤 소리가 겹치면 A3/A4… 로
    밀린다), 문서에 audioTracks(작업 215a, 아직 없을 수 있다)가 있으면 트랙을 더한다(Audio Levels·Cross Fade (+3dB)).
    파일은 상대 경로 media/<이름>, <file id> 는 처음 한 번만 다 적고 다음 clipitem 은 id 만 쓴다.
  - build_edl: CMX3600(비디오 V·소리 AA 이벤트, 디졸브 D nnn, 속도 M2). 예비용이다(추가 소리 트랙은 넣지 않는다).
  - package: 쓴 재료를 S3 에서 받아 <project>.xml·<project>.edl·README.txt(한국어 여는 법)·media/* 를 zip 하나로 묶어
    영상 버킷 gen 루트 premiere-<n>.zip 에 올리고 내려받기 URL(presigned, attachment)을 준다.
배치 규칙은 ⑦ 내보내기(edit._clip_frames·디졸브 프레임·J/L)와 같다. 속도가 바뀐 클립의 clipitem in/out/duration 은 FCP7 관례대로
속도를 반영한 프레임(원본 초 × fps ÷ 속도)이라 out − in = end − start 다. 디졸브는 겹침 가운데(cut)를 두 클립 경계로 둔다.
⑦ 의 화면 밖 소리 곡선(OFFSCREEN_GAIN)·대체 소리(지시서 116 보완 10)는 옮기지 않는다(클립 자기 소리 그대로).

백그라운드: start_premiere(POST) → 스레드 run_premiere(claim·하트비트, edit 내보내기와 같은 방식) → premiere_view(GET 상태·URL).
상태는 gen 루트 edit-premiere.json. 라우트는 register_routes(bp, deps, project_for, fail) 한 줄로 붙인다(routes.py 는 고치지 않았다):
  from . import edit_xml; edit_xml.register_routes(bp, deps, _project_for, _fail)
edit.py 는 요청 때 읽는다(블루프린트를 만들 때 무거운 모듈을 읽지 않는다 — routes.py 와 같다).
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
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

from ..vpoc.errors import VpocError, error_gist, now_iso

log = logging.getLogger("vpoc")

STATE_NAME = "edit-premiere.json"
ZIP_CONTENT_TYPE = "application/zip"
PREMIERE_MAX_S = 1200  # 한 번 시도 상한(받기·묶기·올리기)
PREMIERE_STALL_S = 300  # 하트비트가 이보다 오래 없으면 이어받는다
PREMIERE_MAX_ATTEMPTS = 3
PREMIERE_HISTORY_MAX = 3
PREMIERE_BEAT_S = 5.0
PREMIERE_WAIT_BEAT_S = 30.0
MAX_BODY_BYTES = 4096
MAX_AUDIO_TRACKS = 8
MAX_AUDIO_ITEMS = 200
MAX_GAIN = 3.98109  # Audio Levels 상한(+12 dB)
AUDIO_RATE = 48000
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".mp4")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LIVE = ("queued", "running")

_SEM = threading.BoundedSemaphore(1)  # 프로세스당 묶기 하나(디스크·대역)
_threads: dict[str, threading.Thread] = {}
_threads_guard = threading.Lock()


class _ClaimLost(Exception):
    """다른 실행이 이어받았다."""


class _Timeout(Exception):
    """PREMIERE_MAX_S 를 넘었다."""


def _e():
    from . import edit  # noqa: PLC0415 — 요청 때 읽는다

    return edit


# ================================================================ 작은 도구


def _tc(frames: int, fps: int) -> str:
    """비드롭 타임코드 HH:MM:SS:FF."""
    seconds, ff = divmod(max(0, int(frames)), int(fps))
    hours, rest = divmod(seconds, 3600)
    minutes, ss = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{ss:02d}:{ff:02d}"


def _first_num(obj: dict, *keys: str) -> float | None:
    for key in keys:
        value = _e()._num(obj.get(key))  # noqa: SLF001
        if value is not None:
            return value
    return None


def _gain(obj: dict) -> float:
    """선형 이득. gainDb·volumeDb(dB)가 먼저, 없으면 gain·volume(선형, 1 = 0 dB), 둘 다 없으면 1."""
    db = _first_num(obj, "gainDb", "volumeDb")
    if db is not None:
        return 10 ** (max(-96.0, min(12.0, db)) / 20)
    linear = _first_num(obj, "gain", "volume")
    return max(0.0, min(MAX_GAIN, linear)) if linear is not None else 1.0


def _label(value, fallback: str) -> str:
    text = _CONTROL_RE.sub("", value).strip()[:40] if isinstance(value, str) else ""
    return text or fallback


def clean_audio_tracks(raw, prefix: str = "") -> list[dict]:
    """문서 audioTracks(작업 215a — 모양이 정해지기 전이라 너그럽게 읽는다)를 [{name, items}] 로. 없거나 틀리면 [].

    트랙: {name|label, gainDb|gain, muted, items|clips: [...]}. 항목: {sourceKey|key, start|at(타임라인 초), in, out
    (없으면 in + seconds|duration), gainDb|gain, fadeIn, fadeOut(초), muted, label|name}. prefix 를 주면 그 밑 키만 받는다.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for t_index, track in enumerate(raw[:MAX_AUDIO_TRACKS]):
        if not isinstance(track, dict):
            continue
        items_raw = track.get("items") if isinstance(track.get("items"), list) else track.get("clips")
        if not isinstance(items_raw, list):
            continue
        t_gain, t_muted = _gain(track), track.get("muted") is True
        items: list[dict] = []
        for item in items_raw[:MAX_AUDIO_ITEMS]:
            if not isinstance(item, dict):
                continue
            key = item.get("sourceKey") or item.get("key")
            if not isinstance(key, str) or not key or ".." in key or (prefix and not key.startswith(prefix)):
                continue
            start = _first_num(item, "start", "at", "timelineStart")
            src_in = _first_num(item, "in", "sourceIn") or 0.0
            src_out = _first_num(item, "out", "sourceOut")
            if src_out is None:
                length = _first_num(item, "seconds", "duration")
                src_out = src_in + length if length else None
            if start is None or src_out is None or start < 0 or src_in < 0 or src_out - src_in < 0.04:
                continue
            items.append({
                "sourceKey": key, "start": round(start, 3), "in": round(src_in, 3), "out": round(src_out, 3),
                "gain": round(min(MAX_GAIN, t_gain * _gain(item)), 6),
                "fadeIn": max(0.0, _first_num(item, "fadeIn") or 0.0), "fadeOut": max(0.0, _first_num(item, "fadeOut") or 0.0),
                "muted": t_muted or item.get("muted") is True, "label": _label(item.get("label") or item.get("name"), ""),
            })
        if items:
            out.append({"name": _label(track.get("name") or track.get("label"), f"Audio {t_index + 1}"), "items": items})
    return out


# ================================================================ 배치(⑦ 내보내기와 같은 프레임 규칙)


def _layout(clips: list[dict], media: dict, fps: int) -> dict:
    """클립별 화면·소리 자리(시퀀스 프레임). starts·frames·dissolve 는 edit._fx_plan 과 같은 값이다.

    row: speed, realIn(원본 프레임), adjIn·adjDur(속도 반영 프레임), v(start, end, in, out — 디졸브 가운데를 경계로),
    audio(start, end, in, out, enabled, xfade(다음 소리로 가는 크로스 페이드 [t0, t1]) 또는 None).
    """
    E = _e()
    frames = E._clip_frames(clips, fps)  # noqa: SLF001
    count = len(clips)
    dissolve: list[int] = []
    for index in range(count):
        seconds = E._dissolve_of(clips[index]) if index < count - 1 else 0.0  # noqa: SLF001
        number = int(round(seconds * fps)) if seconds else 0
        if number:
            number = min(number, (min(frames[index], frames[index + 1]) - 1) // 2)
        dissolve.append(max(0, number))
    starts, position = [], 0
    for index in range(count):
        starts.append(position)
        position += frames[index] - dissolve[index]
    rows: list[dict] = []
    for index, clip in enumerate(clips):
        speed = E._speed_of(clip)  # noqa: SLF001
        info = media.get(clip["sourceKey"]) or {}
        seconds = E._num(info.get("duration")) or float(clip["out"])  # noqa: SLF001
        adj_in = int(round(float(clip["in"]) * fps / speed))
        adj_dur = max(int(round(seconds * fps / speed)), adj_in + frames[index])
        begin = starts[index]
        v_start = begin + dissolve[index - 1] // 2 if index and dissolve[index - 1] else begin
        v_end = starts[index + 1] + dissolve[index] // 2 if dissolve[index] else begin + frames[index]
        v_in = adj_in + (v_start - begin)
        muted = bool(clip.get("muted"))
        lead = 0 if index == 0 or muted else int(round(E._shift_of(clip, "audioLead") * fps))  # noqa: SLF001
        tail = 0 if index == count - 1 or muted else int(round(E._shift_of(clip, "audioTail") * fps))  # noqa: SLF001
        slip = 0 if muted else int(round(E._slip_of(clip) * fps))  # noqa: SLF001
        a_start = begin - lead if lead else v_start
        a_end = begin + frames[index] + tail if tail else v_end
        a_in = adj_in + (a_start - begin) - slip
        if a_in < 0:
            a_start, a_in = a_start - a_in, 0
        a_out = a_in + (a_end - a_start)
        if a_out > adj_dur:
            a_end, a_out = a_end - (a_out - adj_dur), adj_dur
        audio = None
        if info.get("hasAudio") is not False and a_end > a_start:
            audio = {"start": a_start, "end": a_end, "in": a_in, "out": a_out, "enabled": not muted, "xfade": None,
                     "tail": tail, "lead": lead}
        rows.append({"speed": speed, "realIn": int(round(float(clip["in"]) * fps)), "adjIn": adj_in, "adjDur": adj_dur,
                     "v": (v_start, v_end, v_in, v_in + (v_end - v_start)), "audio": audio})
    for index in range(count - 1):  # 소리도 디졸브 가운데에서 만나면(J·L 없음) 크로스 페이드
        a, b = rows[index]["audio"], rows[index + 1]["audio"]
        if dissolve[index] and a and b and not a["tail"] and not b["lead"] and a["end"] == b["start"]:
            a["xfade"] = (starts[index + 1], starts[index + 1] + dissolve[index])
    return {"frames": frames, "dissolve": dissolve, "starts": starts, "total": position, "rows": rows}


def _place(lanes: list[dict], start: int, end: int) -> int:
    """겹치지 않게 소리 줄(스테레오 한 쌍)을 고른다. 앞 줄부터, 끝난 줄에 넣는다."""
    for index, lane in enumerate(lanes):
        if lane["end"] <= start:
            lane["end"] = end
            return index
    lanes.append({"end": end})
    return len(lanes) - 1


# ================================================================ FCP7 XML


def _sub(parent, tag: str, text=None, **attrs):
    element = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        element.text = str(text)
    return element


def _rate(parent, fps: int) -> None:
    rate = _sub(parent, "rate")
    _sub(rate, "timebase", fps)
    _sub(rate, "ntsc", "FALSE")


def _timecode(parent, fps: int) -> None:
    tc = _sub(parent, "timecode")
    _rate(tc, fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")


def _file(parent, ref: dict, fps: int, defined: set[str]) -> None:
    """<file id>: 처음 쓰일 때만 내용을 다 적는다(Premiere 는 같은 id 를 한 원본으로 묶는다)."""
    if ref["id"] in defined:
        _sub(parent, "file", id=ref["id"])
        return
    defined.add(ref["id"])
    element = _sub(parent, "file", id=ref["id"])
    _sub(element, "name", ref["name"])
    _sub(element, "pathurl", "media/" + ref["name"])
    _rate(element, fps)
    _sub(element, "duration", ref["frames"])
    _timecode(element, fps)
    media = _sub(element, "media")
    if ref["video"]:
        chars = _sub(_sub(media, "video"), "samplecharacteristics")
        _rate(chars, fps)
        _sub(chars, "width", ref["width"])
        _sub(chars, "height", ref["height"])
        _sub(chars, "anamorphic", "FALSE")
        _sub(chars, "pixelaspectratio", "square")
        _sub(chars, "fielddominance", "none")
    if ref["audio"]:
        audio = _sub(media, "audio")
        chars = _sub(audio, "samplecharacteristics")
        _sub(chars, "depth", 16)
        _sub(chars, "samplerate", AUDIO_RATE)
        _sub(audio, "channelcount", 2)


def _param(effect, pid: str, value, **bounds) -> None:
    param = _sub(effect, "parameter")
    _sub(param, "parameterid", pid)
    _sub(param, "name", pid)
    for key in ("valuemin", "valuemax"):
        if key in bounds:
            _sub(param, key, bounds[key])
    _sub(param, "value", value)


def _time_remap(parent, speed: float, mediatype: str) -> None:
    effect = _sub(_sub(parent, "filter"), "effect")
    for tag, text in (("name", "Time Remap"), ("effectid", "timeremap"), ("effectcategory", "motion"),
                      ("effecttype", "motion"), ("mediatype", mediatype)):
        _sub(effect, tag, text)
    _param(effect, "variablespeed", 0, valuemin=0, valuemax=1)
    _param(effect, "speed", f"{speed * 100:g}", valuemin=-100000, valuemax=100000)
    _param(effect, "reverse", "FALSE")
    _param(effect, "frameblending", "FALSE")


def _levels(parent, gain: float) -> None:
    effect = _sub(_sub(parent, "filter"), "effect")
    for tag, text in (("name", "Audio Levels"), ("effectid", "audiolevels"), ("effectcategory", "audiolevels"),
                      ("effecttype", "audiolevels"), ("mediatype", "audio")):
        _sub(effect, tag, text)
    param = _sub(effect, "parameter")
    _sub(param, "parameterid", "level")
    _sub(param, "name", "Level")
    _sub(param, "valuemin", 0)
    _sub(param, "valuemax", MAX_GAIN)
    _sub(param, "value", f"{gain:.6g}")


def _transition(track, start: int, end: int, fps: int, *, video: bool, alignment: str) -> None:
    item = _sub(track, "transitionitem")
    _sub(item, "start", start)
    _sub(item, "end", end)
    _sub(item, "alignment", alignment)
    _rate(item, fps)
    effect = _sub(item, "effect")
    if video:
        fields = (("name", "Cross Dissolve"), ("effectid", "Cross Dissolve"), ("effectcategory", "Dissolve"),
                  ("effecttype", "transition"), ("mediatype", "video"), ("wipecode", 0), ("wipeaccuracy", 100),
                  ("startratio", 0), ("endratio", 1), ("reverse", "FALSE"))
    else:
        fields = (("name", "Cross Fade (+3dB)"), ("effectid", "KGAudioTransCrossFade3dB"), ("effectcategory", "audio"),
                  ("effecttype", "transition"), ("mediatype", "audio"))
    for tag, text in fields:
        _sub(effect, tag, text)


def _file_refs(clips: list[dict], tracks: list[dict], media: dict, fps: int, width: int, height: int) -> dict[str, dict]:
    """원본 키 → {id, name, frames, width, height, video, audio}. id 는 처음 나온 순서(file-1…)."""
    E = _e()
    refs: dict[str, dict] = {}
    longest: dict[str, float] = {}
    for clip in clips:
        longest[clip["sourceKey"]] = max(longest.get(clip["sourceKey"], 0.0), float(clip["out"]))
    for track in tracks:
        for item in track["items"]:
            longest[item["sourceKey"]] = max(longest.get(item["sourceKey"], 0.0), float(item["out"]))
    video_keys = {c["sourceKey"] for c in clips}
    for key in longest:
        info = media.get(key) or {}
        is_video = key in video_keys
        seconds = max(E._num(info.get("duration")) or 0.0, longest[key])  # noqa: SLF001
        refs[key] = {
            "id": f"file-{len(refs) + 1}", "name": str(info.get("name") or E.media_id(key) + ".mp4"),
            "frames": int(math.ceil(seconds * fps - 1e-6)), "width": int(info.get("width") or width),
            "height": int(info.get("height") or height), "video": is_video,
            "audio": (info.get("hasAudio") is not False) if is_video else True,
        }
    return refs


def _clipitem(track, ids: dict, name: str, ref: dict, fps: int, defined: set[str], *, duration: int, start: int, end: int,
              src_in: int, src_out: int, speed: float, enabled: bool, mediatype: str, channel: int = 0, gain=None) -> None:
    ids["n"] += 1
    item = _sub(track, "clipitem", id=f"clipitem-{ids['n']}")
    _sub(item, "name", name)
    _sub(item, "enabled", "TRUE" if enabled else "FALSE")
    _sub(item, "duration", duration)
    _rate(item, fps)
    for tag, value in (("start", start), ("end", end), ("in", src_in), ("out", src_out)):
        _sub(item, tag, value)
    _file(item, ref, fps, defined)
    if speed != 1:
        _time_remap(item, speed, mediatype)
    if gain is not None and abs(gain - 1.0) > 1e-6:
        _levels(item, gain)
    if channel:
        source = _sub(item, "sourcetrack")
        _sub(source, "mediatype", "audio")
        _sub(source, "trackindex", channel)


def _audio_lanes(audio_el, lanes: list[list[dict]], fps: int, ids: dict, defined: set[str]) -> int:
    """소리 줄마다 트랙 두 개(왼쪽 = 원본 소리 1, 오른쪽 = 2). 반환: 만든 트랙 수."""
    made = 0
    for lane in lanes:
        for channel in (1, 2):
            track = _sub(audio_el, "track")
            events = sorted(lane, key=lambda e: (e["start"], 0 if e["kind"] == "transition" else 1))
            for event in events:
                if event["kind"] == "transition":
                    _transition(track, event["start"], event["end"], fps, video=False, alignment=event["alignment"])
                else:
                    _clipitem(track, ids, event["name"], event["ref"], fps, defined, duration=event["duration"],
                              start=event["start"], end=event["end"], src_in=event["in"], src_out=event["out"],
                              speed=event["speed"], enabled=event["enabled"], mediatype="audio", channel=channel,
                              gain=event.get("gain"))
            _sub(track, "enabled", "TRUE")
            _sub(track, "locked", "FALSE")
            _sub(track, "outputchannelindex", channel)
            made += 1
    return made


def build_xmeml(doc: dict, media: dict, *, fps: int, width: int, height: int, name: str) -> str:
    """FCP7 XML(xmeml v4) 시퀀스 하나. media: 원본 키 → {name, duration, width, height, hasAudio}."""
    fps = int(fps)
    clips = [c for c in (doc or {}).get("clips") or [] if isinstance(c, dict)]
    tracks = clean_audio_tracks((doc or {}).get("audioTracks"))
    tracks = [{**t, "items": [i for i in t["items"] if i["sourceKey"] in media]} for t in tracks]
    tracks = [t for t in tracks if t["items"]]
    lay = _layout(clips, media, fps)
    refs = _file_refs(clips, tracks, media, fps, width, height)
    root = ET.Element("xmeml", version="4")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "name", name)
    total = lay["total"]
    for track in tracks:
        for item in track["items"]:
            total = max(total, int(round(item["start"] * fps)) + int(round((item["out"] - item["in"]) * fps)))
    _sub(seq, "duration", total)
    _rate(seq, fps)
    _timecode(seq, fps)
    media_el = _sub(seq, "media")
    video = _sub(media_el, "video")
    chars = _sub(_sub(video, "format"), "samplecharacteristics")
    _rate(chars, fps)
    for tag, value in (("width", width), ("height", height), ("anamorphic", "FALSE"), ("pixelaspectratio", "square"),
                       ("fielddominance", "none")):
        _sub(chars, tag, value)
    v1 = _sub(video, "track")
    ids, defined = {"n": 0}, set()
    for index, (clip, row) in enumerate(zip(clips, lay["rows"])):
        ref = refs[clip["sourceKey"]]
        v_start, v_end, v_in, v_out = row["v"]
        _clipitem(v1, ids, str(clip.get("label") or ref["name"]), ref, fps, defined, duration=row["adjDur"],
                  start=v_start, end=v_end, src_in=v_in, src_out=v_out, speed=row["speed"], enabled=True, mediatype="video")
        if lay["dissolve"][index]:
            t0 = lay["starts"][index + 1]
            _transition(v1, t0, t0 + lay["dissolve"][index], fps, video=True, alignment="center")
    _sub(v1, "enabled", "TRUE")
    _sub(v1, "locked", "FALSE")
    audio = _sub(media_el, "audio")
    _sub(audio, "numOutputChannels", 2)
    a_chars = _sub(_sub(audio, "format"), "samplecharacteristics")
    _sub(a_chars, "depth", 16)
    _sub(a_chars, "samplerate", AUDIO_RATE)
    outputs = _sub(audio, "outputs")
    for channel in (1, 2):
        group = _sub(outputs, "group")
        _sub(group, "index", channel)
        _sub(group, "numchannels", 1)
        _sub(group, "downmix", 0)
        _sub(_sub(group, "channel"), "index", channel)
    # 클립 소리: 겹치지 않으면 A1/A2, J·L 로 겹치면 다음 쌍.
    lanes_end: list[dict] = []
    lanes: list[list[dict]] = []
    for clip, row in zip(clips, lay["rows"]):
        a = row["audio"]
        if not a:
            continue
        lane = _place(lanes_end, a["start"], a["end"])
        if lane == len(lanes):
            lanes.append([])
        ref = refs[clip["sourceKey"]]
        lanes[lane].append({"kind": "clip", "name": str(clip.get("label") or ref["name"]), "ref": ref,
                            "duration": row["adjDur"], "start": a["start"], "end": a["end"], "in": a["in"], "out": a["out"],
                            "speed": row["speed"], "enabled": a["enabled"]})
        if a["xfade"]:
            lanes[lane].append({"kind": "transition", "start": a["xfade"][0], "end": a["xfade"][1], "alignment": "center"})
    count = _audio_lanes(audio, lanes, fps, ids, defined)
    # 추가 소리 트랙(작업 215a): 트랙마다 따로(그 안에서 겹치면 쌍을 더한다). 볼륨 = Audio Levels, 페이드 = Cross Fade (+3dB).
    for track in tracks:
        t_end: list[dict] = []
        t_lanes: list[list[dict]] = []
        for item in track["items"]:
            ref = refs[item["sourceKey"]]
            start, src_in = int(round(item["start"] * fps)), int(round(item["in"] * fps))
            length = int(round((item["out"] - item["in"]) * fps))
            if length < 1:
                continue
            lane = _place(t_end, start, start + length)
            if lane == len(t_lanes):
                t_lanes.append([])
            t_lanes[lane].append({"kind": "clip", "name": item["label"] or ref["name"], "ref": ref, "duration": ref["frames"],
                                  "start": start, "end": start + length, "in": src_in, "out": src_in + length, "speed": 1.0,
                                  "enabled": not item["muted"], "gain": item["gain"]})
            fade_in = min(int(round(item["fadeIn"] * fps)), length // 2)
            fade_out = min(int(round(item["fadeOut"] * fps)), length // 2)
            if fade_in:
                t_lanes[lane].append({"kind": "transition", "start": start, "end": start + fade_in, "alignment": "start-black"})
            if fade_out:
                t_lanes[lane].append({"kind": "transition", "start": start + length - fade_out, "end": start + length,
                                      "alignment": "end-black"})
        count += _audio_lanes(audio, t_lanes, fps, ids, defined)
    if not count:  # 소리가 하나도 없어도 A1/A2 는 둔다(Premiere 기본 시퀀스 모양)
        _audio_lanes(audio, [[]], fps, ids, defined)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ================================================================ CMX3600 EDL


def _edl_title(name: str) -> str:
    return re.sub(r"[^\x20-\x7e]", "", str(name or "")).strip()[:70] or "AI-POC EDIT"


def build_edl(doc: dict, media: dict, *, fps: int, name: str) -> str:
    """CMX3600(비드롭). 이벤트 V(클립 화면)·AA(클립 소리, 스테레오). 디졸브 = 같은 번호 두 줄(나가는 클립 0 길이 C + 들어오는 클립
    D nnn), 속도 = M2(릴, 원본 fps × 속도, 원본 시작). 원본 out = 원본 in + 기록 길이(CMX 관례). 기록 시작 00:00:00:00."""
    fps = int(fps)
    clips = [c for c in (doc or {}).get("clips") or [] if isinstance(c, dict)]
    lay = _layout(clips, media, fps)
    rows, starts, frames, dissolve = lay["rows"], lay["starts"], lay["frames"], lay["dissolve"]
    names = [str((media.get(c["sourceKey"]) or {}).get("name") or _e().media_id(c["sourceKey"]) + ".mp4") for c in clips]

    def edit_line(chan: str, trans: str, dur, s_in: int, s_out: int, r_in: int, r_out: int) -> str:
        dur_text = "" if dur == "" else f"{int(dur):03d}"
        return (f"{{n}}  {'AX':<8} {chan:<5} {trans:<4} {dur_text:>3} "
                f"{_tc(s_in, fps)} {_tc(s_out, fps)} {_tc(r_in, fps)} {_tc(r_out, fps)}")

    def m2(speed: float, s_in: int) -> str:
        return f"M2   {'AX':<8} {fps * speed:05.1f}{' ' * 16}{_tc(s_in, fps)}"

    events: list[tuple[int, int, list[str]]] = []
    for index, row in enumerate(rows):
        rec_in = starts[index]
        rec_out = rec_in + frames[index] - dissolve[index]
        src_in, speed = row["realIn"], row["speed"]
        if index and dissolve[index - 1]:
            prev = rows[index - 1]
            p_src = prev["realIn"] + int(round((rec_in - starts[index - 1]) * prev["speed"]))
            lines = [edit_line("V", "C", "", p_src, p_src, rec_in, rec_in),
                     edit_line("V", "D", dissolve[index - 1], src_in, src_in + rec_out - rec_in, rec_in, rec_out)]
            lines += [m2(prev["speed"], p_src)] if prev["speed"] != 1 else []
            lines += [m2(speed, src_in)] if speed != 1 else []
            lines += [f"* FROM CLIP NAME: {names[index - 1]}", f"* TO CLIP NAME: {names[index]}"]
        else:
            lines = [edit_line("V", "C", "", src_in, src_in + rec_out - rec_in, rec_in, rec_out)]
            lines += [m2(speed, src_in)] if speed != 1 else []
            lines += [f"* FROM CLIP NAME: {names[index]}"]
        events.append((rec_in, 0, lines))
        audio = row["audio"]
        if audio and audio["enabled"]:
            a_src = int(round(audio["in"] * speed))
            lines = [edit_line("AA", "C", "", a_src, a_src + audio["end"] - audio["start"], audio["start"], audio["end"])]
            lines += [m2(speed, a_src)] if speed != 1 else []
            lines += [f"* FROM CLIP NAME: {names[index]}"]
            events.append((audio["start"], 1, lines))
    events.sort(key=lambda e: (e[0], e[1]))
    out = [f"TITLE: {_edl_title(name)}", "FCM: NON-DROP FRAME", ""]
    for number, (_rec, _kind, lines) in enumerate(events, 1):
        out.extend(line.replace("{n}", f"{number:03d}") for line in lines)
        out.append("")
    return "\n".join(out)


# ================================================================ 묶기(package)


def state_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_e()._root(owner, pid, gen_id)}/{STATE_NAME}"  # noqa: SLF001


def zip_key(owner: str, pid: str, gen_id: str, n) -> str:
    return f"{_e()._root(owner, pid, gen_id)}/premiere-{n}.zip"  # noqa: SLF001


def _project_name(pid: str, variant: str, n) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{pid}-{variant}-{n}").strip("-")[:60]
    return text or "premiere"


def _job(owner: str, pid: str, variant, where: str) -> dict:
    """저장된 문서를 내보내기와 같은 규칙(edit._clean_clips)으로 검사한 사본. 문서가 없으면 409 EditNotSaved."""
    E = _e()
    if variant not in (None, "") and variant not in E.VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    _plan, gen, gen_id = E._load_gen(owner, pid)  # noqa: SLF001
    docs = E._saved_docs(owner, pid, gen_id)  # noqa: SLF001
    variant = variant or E._recent_variant(docs, gen)  # noqa: SLF001
    if variant is None or docs[variant]["doc"] is None:
        raise VpocError(where, "EditNotSaved", "편집 문서를 먼저 저장해 주세요.", 409)
    doc = docs[variant]["doc"]
    allowed = {m["sourceKey"] for m in E._all_items(gen, variant, E._retake_state(owner, pid, gen_id))}  # noqa: SLF001
    referenced = [c.get("sourceKey") for c in doc["clips"] if isinstance(c, dict) and c.get("sourceKey") in allowed]
    meta, _ = E._ensure_meta(owner, pid, gen_id, referenced)  # noqa: SLF001
    clips = E._clean_clips(doc["clips"], allowed, {k: (meta.get(k) or {}).get("duration") for k in allowed}, where)  # noqa: SLF001
    if not clips:
        raise VpocError(where, "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
    tracks = clean_audio_tracks(doc.get("audioTracks"), f"users/{owner}/video/{pid}/")
    body = E._doc_hash(variant, clips) + json.dumps(tracks, sort_keys=True, ensure_ascii=False)  # noqa: SLF001
    return {"genId": gen_id, "variant": variant, "docVersion": E._doc_version(doc), "clips": clips,  # noqa: SLF001
            "audioTracks": tracks, "docHash": hashlib.sha256(body.encode("utf-8")).hexdigest()}


def _readme(project: str, seq_name: str, *, fps: int, width: int, height: int, tracks: int) -> str:
    extra = f"- 추가 소리 트랙 {tracks}개: 볼륨(오디오 레벨)과 페이드(크로스 페이드 +3dB).\n" if tracks else ""
    return (
        f"{project} - Premiere Pro 로 여는 방법\n\n"
        "이 압축 파일은 ⑦ 편집 타임라인을 Premiere Pro 에서 이어 편집할 수 있게 만든 것입니다.\n"
        f"  {project}.xml   Final Cut Pro 7 XML 시퀀스. 이 파일로 여는 것을 권합니다.\n"
        f"  {project}.edl   CMX3600 EDL. 예비용입니다(비디오 1트랙과 클립 소리만 들어 있습니다).\n"
        "  media/          타임라인에 쓴 원본 영상과 소리.\n\n"
        "여는 순서\n"
        f"1. 압축을 모두 풉니다. {project}.xml 과 media 폴더가 같은 폴더에 있어야 합니다.\n"
        "2. Premiere Pro 에서 프로젝트를 새로 만들거나 엽니다.\n"
        f"3. 파일 > 가져오기(Windows Ctrl+I, Mac Cmd+I)에서 {project}.xml 을 고릅니다.\n"
        f"4. 프로젝트 패널에 생긴 시퀀스 \"{seq_name}\"를 두 번 눌러 엽니다.\n"
        "5. 클립이 '미디어 오프라인'으로 보이면 클립을 고르고 오른쪽 클릭 > 미디어 연결(Link Media)에서\n"
        "   media 폴더의 같은 이름 파일을 고릅니다. 하나를 연결하면 나머지는 대개 자동으로 찾습니다.\n\n"
        f"시퀀스: {width}x{height}, {fps} fps, 소리 48 kHz 스테레오.\n\n"
        "들어 있는 것\n"
        "- V1: 클립 순서와 자르기, 속도(시간 다시 매핑), 디졸브(교차 디졸브).\n"
        "- A1/A2: 클립 소리(왼쪽/오른쪽). J·L 컷처럼 앞뒤 소리가 겹치면 그 클립 소리는 A3/A4 이후 트랙에 놓입니다.\n"
        "- 소리를 끈 클립은 소리 클립이 '사용 안 함' 상태로 들어 있습니다.\n"
        f"{extra}\n"
        "알아 둘 점\n"
        "- ⑦ 내보내기가 겹치는 소리에 주던 볼륨 곡선은 옮기지 않았습니다. 필요하면 Premiere 에서 조절하세요.\n"
        "- EDL 로 열 때는 미디어 연결이 필요하고 추가 소리 트랙은 들어 있지 않습니다.\n"
    )


def package(owner: str, pid: str, variant, *, job: dict | None = None, beat=None) -> dict:
    """재료를 받아 zip(<project>.xml·<project>.edl·README.txt·media/*)을 만들어 영상 버킷에 올린다. 반환 {key, url, fileName, bytes,
    entries, fps, width, height}. job 이 없으면 지금 저장 문서(_job)로 한다. beat(**fields) 는 파일마다 부른다(스레드의 하트비트)."""
    E = _e()
    where = "vnew.edit.premiere"
    job = job or _job(owner, pid, variant, where)
    gen_id, variant, clips = job["genId"], job["variant"], [c for c in job["clips"] if isinstance(c, dict)]
    beat = beat or (lambda **_fields: None)
    prefix = f"users/{owner}/video/{pid}/"
    meta, _ = E._ensure_meta(owner, pid, gen_id, [c["sourceKey"] for c in clips])  # noqa: SLF001
    for clip in clips:
        if not str(clip["sourceKey"]).startswith(prefix) or not E._num((meta.get(clip["sourceKey"]) or {}).get("duration")):  # noqa: SLF001
            raise VpocError(where, "SourceUnreadable", "원본 영상을 읽지 못했습니다.", 409)
    if not clips:
        raise VpocError(where, "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
    width, height, fps = E._export_geometry(meta[clips[0]["sourceKey"]])  # noqa: SLF001
    tracks = clean_audio_tracks(job.get("audioTracks"), prefix)
    media: dict[str, dict] = {}
    for clip in clips:
        if clip["sourceKey"] not in media:
            media[clip["sourceKey"]] = {**(meta.get(clip["sourceKey"]) or {}), "name": f"clip-{len(media) + 1:02d}.mp4",
                                        "bucket": E.vpoc_settings.VIDEO_BUCKET}
    sounds = 0
    for track in tracks:
        for item in track["items"]:
            key = item["sourceKey"]
            if key not in media:
                sounds += 1
                ext = os.path.splitext(key)[1].lower()
                media[key] = {"name": f"audio-{sounds:02d}{ext if ext in AUDIO_EXTS else '.mp4'}", "hasAudio": True,
                              "bucket": None}
    n = job.get("n") or secrets.token_hex(4)
    project = _project_name(pid, variant, n)
    seq_name = f"{E.VARIANT_LABELS.get(variant, variant)} 편집 v{job.get('docVersion') or 0}"
    doc = {"clips": clips, "audioTracks": tracks}
    xml_text = build_xmeml(doc, media, fps=fps, width=width, height=height, name=seq_name)
    edl_text = build_edl(doc, media, fps=fps, name=project)
    readme = _readme(project, seq_name, fps=fps, width=width, height=height, tracks=len(tracks))
    key = zip_key(owner, pid, gen_id, n)
    entries = [f"{project}.xml", f"{project}.edl", "README.txt"]
    with tempfile.TemporaryDirectory(prefix="vnew-premiere-") as workdir:
        zip_path = os.path.join(workdir, f"{project}.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(entries[0], xml_text)
            archive.writestr(entries[1], edl_text)
            archive.writestr(entries[2], readme)
            for done, (source, info) in enumerate(media.items(), 1):
                path = os.path.join(workdir, "download.bin")
                if info["bucket"] is None:  # 추가 소리: 영상 버킷 먼저, 없으면 메인 버킷
                    try:
                        E._download(source, path, bucket=E.vpoc_settings.VIDEO_BUCKET)  # noqa: SLF001
                    except Exception:  # noqa: BLE001
                        E._download(source, path, bucket="")  # noqa: SLF001
                else:
                    E._download(source, path, bucket=info["bucket"])  # noqa: SLF001
                archive.write(path, "media/" + info["name"], compress_type=zipfile.ZIP_STORED)  # mp4 는 이미 압축돼 있다
                os.unlink(path)
                entries.append("media/" + info["name"])
                beat(progress={"done": done, "total": len(media)})
        size = os.path.getsize(zip_path)
        beat(force=True)  # 올리기 전에 claim 확인
        E._upload(zip_path, key, bucket=E.vpoc_settings.VIDEO_BUCKET, content_type=ZIP_CONTENT_TYPE)  # noqa: SLF001
    file_name = f"{project}.zip"
    log.info("vnew.edit.premiere_packaged pid=%s gen=%s variant=%s clips=%d media=%d tracks=%d bytes=%d", pid, gen_id,
             variant, len(clips), len(media), len(tracks), size)
    return {"key": key, "url": _presign_zip(key, file_name), "fileName": file_name, "bytes": size, "entries": entries,
            "fps": fps, "width": width, "height": height}


def _presign_zip(key: str, file_name: str) -> str:
    """zip 내려받기 URL(영상 버킷 presigned + attachment). 검사용 저장소는 store.presign. 실패하면 ""."""
    E = _e()
    store = E.STORE
    if not key:
        return ""
    if not isinstance(store, E.vpoc_store.Store):
        return E._presign(key, bucket=E.vpoc_settings.VIDEO_BUCKET, content_type=ZIP_CONTENT_TYPE)  # noqa: SLF001
    try:
        target = E.vpoc_settings.VIDEO_BUCKET or store.main_bucket
        region = store.video_region if target == store.video_bucket else store.main_region
        return E.vpoc_store.s3_client(region).generate_presigned_url(
            "get_object",
            Params={"Bucket": target, "Key": key, "ResponseContentType": ZIP_CONTENT_TYPE,
                    "ResponseContentDisposition": f'attachment; filename="{file_name}"'},
            ExpiresIn=E.URL_EXPIRES_S,
        )
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다
        log.warning("vnew.edit.premiere_presign_failed err=%s", type(exc).__name__)
        return ""


# ================================================================ 백그라운드(claim · 하트비트 · 상태)


def _empty_state() -> dict:
    return {"v": 1, "n": 0, "current": None, "history": []}


def _out(entry: dict | None, doc_hash: str = "") -> dict:
    if not isinstance(entry, dict) or not entry:
        return {"status": "none"}
    done = entry.get("status") == "done"
    return {
        "status": entry.get("status"), "n": entry.get("n"), "variant": entry.get("variant"),
        "docVersion": entry.get("docVersion"), "matchesDoc": bool(doc_hash) and entry.get("docHash") == doc_hash,
        "progress": entry.get("progress") or {}, "attempts": entry.get("attempts"),
        "createdAt": entry.get("createdAt") or "", "startedAt": entry.get("startedAt") or "",
        "finishedAt": entry.get("finishedAt") or "", "bytes": entry.get("bytes"), "error": entry.get("error") or "",
        "fileName": entry.get("fileName") or "",
        "url": _presign_zip(str(entry.get("key") or ""), str(entry.get("fileName") or "premiere.zip")) if done else "",
    }


def _summary(entry: dict) -> dict:
    keep = ("n", "status", "variant", "docVersion", "docHash", "key", "fileName", "bytes", "finishedAt", "error")
    return {k: entry.get(k) for k in keep}


def _latest(state: dict, variant: str | None) -> dict | None:
    items = [h for h in (state.get("history") or []) if isinstance(h, dict)]
    if isinstance(state.get("current"), dict):
        items.append(state["current"])
    items = [h for h in items if variant in (None, "") or h.get("variant") == variant]
    return max(items, key=lambda h: h.get("n") if isinstance(h.get("n"), int) else -1) if items else None


def _spawn(owner: str, pid: str, gen_id: str, n: int, claim: str) -> None:
    thread = threading.Thread(target=run_premiere, args=(owner, pid, gen_id, int(n), claim), daemon=True,
                              name=f"vnew-edit-premiere-{pid}-{int(n)}")
    with _threads_guard:
        for done in [k for k, t in _threads.items() if not t.is_alive()]:
            _threads.pop(done, None)
        _threads[claim] = thread
    thread.start()


def _alive_here(claim) -> bool:
    with _threads_guard:
        thread = _threads.get(str(claim or ""))
        return bool(thread is not None and thread.is_alive())


def _beat(owner: str, pid: str, gen_id: str, token: str, **fields) -> dict:
    box: dict = {}

    def mutate(data):
        cur = (data or {}).get("current")
        if not isinstance(cur, dict) or cur.get("claim") != token or cur.get("status") not in _LIVE:
            raise _ClaimLost()
        cur.update(fields)
        cur["heartbeatAt"] = now_iso()
        box["current"] = copy.deepcopy(cur)
        return data

    _e()._mutate_json(state_key(owner, pid, gen_id), mutate)  # noqa: SLF001
    return box["current"]


def reconcile(owner: str, pid: str, gen_id: str) -> bool:
    """멈춘 묶기 이어받기(파드 재시작 등). 하트비트가 PREMIERE_STALL_S 넘게 없고 이 프로세스에서 돌지 않으면 새 claim 으로 다시,
    PREMIERE_MAX_ATTEMPTS 번이면 failed."""
    from . import plan as plan_mod  # noqa: PLC0415

    E = _e()
    key = state_key(owner, pid, gen_id)
    state, _ = E._read_json(key)  # noqa: SLF001
    current = (state or {}).get("current")
    if not isinstance(current, dict) or current.get("status") not in _LIVE or _alive_here(current.get("claim")):
        return False
    beat = plan_mod._parse_iso(str(current.get("heartbeatAt") or current.get("claimedAt") or ""))  # noqa: SLF001
    if beat and (datetime.now(timezone.utc) - beat).total_seconds() <= PREMIERE_STALL_S:
        return False
    new_claim = secrets.token_hex(8)
    box: dict = {}

    def mutate(data):
        cur = (data or {}).get("current")
        if not isinstance(cur, dict) or cur.get("claim") != current.get("claim") or cur.get("status") not in _LIVE:
            return None
        attempts = int(cur.get("attempts") or 1)
        if attempts >= PREMIERE_MAX_ATTEMPTS:
            cur.update(status="failed", claim="", finishedAt=now_iso(), error="Premiere 내보내기가 여러 번 중단됐습니다. 다시 눌러 주세요.")
        else:
            cur.update(status="queued", claim=new_claim, claimedAt=now_iso(), heartbeatAt=now_iso(), attempts=attempts + 1,
                       startedAt="", error="", progress={"done": 0, "total": 0})
            box["resume"] = cur["n"]
        return data

    E._mutate_json(key, mutate)  # noqa: SLF001
    if "resume" in box:
        log.warning("vnew.edit.premiere_resume pid=%s gen=%s n=%s", pid, gen_id, box["resume"])
        _spawn(owner, pid, gen_id, int(box["resume"]), new_claim)
    return True


def start_premiere(owner: str, pid: str, variant=None) -> dict:
    """POST /p/<pid>/edit/premiere. 같은 문서로 진행 중이거나 이미 끝난 묶음이 있으면 그것을 준다(started false).
    다른 문서로 진행 중이면 409 PremiereRunning. 반환 {started, premiere}."""
    where = "vnew.edit.premiere"
    job = _job(owner, pid, variant, where)
    gen_id = job["genId"]
    reconcile(owner, pid, gen_id)
    claim = secrets.token_hex(8)
    box: dict = {}

    def mutate(state):
        state = state if isinstance(state, dict) else _empty_state()
        current = state.get("current") if isinstance(state.get("current"), dict) else None
        if current and current.get("status") in _LIVE:
            if current.get("docHash") == job["docHash"]:
                box["reused"] = current
                return None
            raise VpocError(where, "PremiereRunning", "Premiere 내보내기가 진행 중입니다. 끝난 뒤 다시 눌러 주세요.", 409)
        latest = _latest(state, job["variant"])
        if latest and latest.get("status") == "done" and latest.get("docHash") == job["docHash"] and latest.get("key"):
            box["reused"] = latest
            return None
        history = [h for h in (state.get("history") or []) if isinstance(h, dict)]
        if current:
            history.append(_summary(current))
        n = int(state.get("n") or 0) + 1
        now = now_iso()
        started = {**job, "n": n, "status": "queued", "claim": claim, "claimedAt": now, "heartbeatAt": now,
                   "createdAt": now, "startedAt": "", "finishedAt": "", "attempts": 1,
                   "progress": {"done": 0, "total": 0}, "key": "", "fileName": "", "bytes": 0, "error": ""}
        box["started"] = started
        return {"v": 1, "n": n, "current": started, "history": history[-PREMIERE_HISTORY_MAX:]}

    _e()._mutate_json(state_key(owner, pid, gen_id), mutate)  # noqa: SLF001
    if "started" in box:
        log.info("vnew.edit.premiere_start pid=%s gen=%s n=%d clips=%d", pid, gen_id, box["started"]["n"], len(job["clips"]))
        _spawn(owner, pid, gen_id, box["started"]["n"], claim)
        return {"started": True, "premiere": _out(box["started"], job["docHash"])}
    return {"started": False, "premiere": _out(box.get("reused"), job["docHash"])}


def premiere_view(owner: str, pid: str, variant=None) -> dict:
    """GET /p/<pid>/edit/premiere. 그 그림체(없으면 아무 그림체)의 가장 최근 묶기 상태와 내려받기 URL."""
    E = _e()
    if variant not in (None, "") and variant not in E.VARIANTS:
        raise VpocError("vnew.edit.premiere", "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    _plan, _gen, gen_id = E._load_gen(owner, pid)  # noqa: SLF001
    try:
        reconcile(owner, pid, gen_id)
    except Exception as exc:  # noqa: BLE001 — 보기는 실패하지 않는다
        log.warning("vnew.edit.premiere_reconcile_failed pid=%s err=%s", pid, type(exc).__name__)
    state, _ = E._read_json(state_key(owner, pid, gen_id))  # noqa: SLF001
    latest = _latest(state or {}, variant)
    return {"variant": variant or (latest or {}).get("variant"), "premiere": _out(latest)}


def run_premiere(owner: str, pid: str, gen_id: str, n: int, claim: str) -> None:
    """묶기 스레드 본체. 프로세스당 하나(_SEM, 기다리는 동안 하트비트), 파일마다 하트비트·시간 상한, 끝나면 done·failed."""
    acquired = False
    try:
        while not _SEM.acquire(timeout=PREMIERE_WAIT_BEAT_S):
            _beat(owner, pid, gen_id, claim)
        acquired = True
        current = _beat(owner, pid, gen_id, claim, status="running", startedAt=now_iso(), error="")
        started = last = time.monotonic()

        def beat(force: bool = False, **fields) -> None:
            nonlocal last
            if time.monotonic() - started > PREMIERE_MAX_S:
                raise _Timeout()
            if force or time.monotonic() - last >= PREMIERE_BEAT_S:
                _beat(owner, pid, gen_id, claim, **fields)
                last = time.monotonic()

        result = package(owner, pid, current.get("variant"), job={**current, "genId": gen_id, "n": n}, beat=beat)
        _beat(owner, pid, gen_id, claim, claim="", status="done", key=result["key"], fileName=result["fileName"],
              bytes=result["bytes"], finishedAt=now_iso(), error="", progress={"done": 1, "total": 1})
    except _ClaimLost:
        log.info("vnew.edit.premiere_claim_lost pid=%s gen=%s n=%d", pid, gen_id, n)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, _Timeout):
            message = f"Premiere 내보내기가 {PREMIERE_MAX_S // 60}분을 넘어 멈췄습니다."
        elif isinstance(exc, VpocError):
            message = exc.message
        else:
            message = "Premiere 프로젝트를 만들지 못했습니다."
        log.warning("vnew.edit.premiere_failed pid=%s gen=%s n=%d err=%s", pid, gen_id, n,
                    error_gist(f"{type(exc).__name__}: {exc}", 300))
        try:
            _beat(owner, pid, gen_id, claim, claim="", status="failed", error=message, finishedAt=now_iso())
        except Exception:  # noqa: BLE001
            pass
    finally:
        if acquired:
            _SEM.release()


# ================================================================ 라우트


def _body(where: str) -> dict:
    """작은 JSON 바디(MAX_BODY_BYTES 까지 — 넘으면 413). voices_routes._body 와 같은 방식."""
    from flask import request  # noqa: PLC0415
    from werkzeug.exceptions import RequestEntityTooLarge  # noqa: PLC0415

    too_large = VpocError(where, "BodyTooLarge", "요청 본문이 너무 큽니다.", 413)
    request.max_content_length = MAX_BODY_BYTES + 1
    try:
        data = request.get_data(cache=True)
    except RequestEntityTooLarge:
        raise too_large from None
    if len(data) > MAX_BODY_BYTES:
        raise too_large
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def register_routes(bp, deps, project_for, fail) -> None:
    """bp(vnew 블루프린트 또는 앱)에 두 라우트를 더한다. project_for(pid, write=…) → 소유자 접두사(routes._project_for),
    fail(exc) → 오류 응답(routes._fail). 둘 다 쓰기 권한(프로젝트 소유자)이고 VNEW_EDIT 이 꺼져 있으면 프로젝트를 보기 전에 404.
      POST /p/<pid>/edit/premiere   {variant?} → 202 {ok, started, premiere}
      GET  /p/<pid>/edit/premiere   ?variant= → {variant, premiere: {status, progress, url, fileName, …}}
    """
    from flask import jsonify, request  # noqa: PLC0415

    @bp.post("/p/<pid>/edit/premiere")
    @deps.require_auth
    def edit_premiere_start(pid: str):
        try:
            _e().require_enabled("vnew.edit.premiere")
            owner = project_for(pid, write=True)
            payload = _body("vnew.edit.premiere")
            return jsonify({"ok": True, **start_premiere(owner, pid, payload.get("variant"))}), 202
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.get("/p/<pid>/edit/premiere")
    @deps.require_auth
    def edit_premiere_status(pid: str):
        try:
            _e().require_enabled("vnew.edit.premiere")
            owner = project_for(pid, write=True)
            return jsonify(premiere_view(owner, pid, request.args.get("variant")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
