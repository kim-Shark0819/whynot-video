"""⑦ 편집 오디오 트랙(작업 215a): 내 소리(배경음·효과음) 트랙 · 이득 · 페이드 · 볼륨 키프레임 · 대사 자동 덕킹 · 섞기 · 스템.

편집 문서(edit-<variant>.json)에 두 값을 더한다. 둘 다 없으면(옛 문서) 215a 전과 같은 문서 · 해시 · 내보내기다.
  audioTracks: 트랙 최대 MAX_TRACKS 개(A2 배경음 · A3 효과음 · …). 클립 소리는 늘 A1(대사)이다.
    {id, name, mute, solo, gainDb(−60~12), items: [{id, assetId, start(타임라인 초), in, out(원본 초), gainDb, fadeIn, fadeOut,
     keys: [{t(아이템 시작부터 초), db}]}]}
  a1: A1(대사) 트랙 {gainDb, mute, solo, duck(bool), duckDb(기본 8)} — 기본값이면 저장하지 않는다.
내 소리 파일(사용자가 올린 mp3·wav·m4a·aac·ogg, 50MB · 10분 이하)은 프로젝트 폴더 영상 버킷에 둔다(edit.py 가 저장한다).
이 파일은 어떤 AI 모델에도 보내지 않는다(이 모듈은 ffmpeg 만 부른다).

섞기(mix_plan → mix_graph → render_mix): ffmpeg 한 번. 아이템마다 atrim → 키프레임 볼륨(volume eval=frame, dB 직선 보간을
clip() 합으로 — 중첩 if 없음) → afade(직선) → adelay(샘플) → 트랙마다 amix(normalize=0) → 트랙 이득 → 덕킹(미리 센 포락선, 공격 ·
놓기 0.15초) → 들리는 트랙(mute · solo)을 amix → 4배 오버샘플 alimiter(−2 dBFS, 자동 레벨 끔) → 48kHz. 그래서 결과의 참 피크가
−1 dBTP 아래이고 넘친 값이 잘리지 않는다. 스템은 리미터 앞의 트랙 버스(32비트 float WAV — 잘림 없음), mix.wav 는 리미터 뒤(24비트).
덕킹 구간은 내보낸 A1 소리에서 센다(edit.sound_ranges 와 같은 규칙 — 작업 185 ⑦ 소리 구간).
"""

from __future__ import annotations

import math
import os
import re

from ..vpoc.errors import VpocError

# ================================================================ 한도

MAX_TRACKS = 4
TRACK_NAMES = ("A2 배경음", "A3 효과음", "A4 효과음 2", "A5 소리")
STEM_NAMES = ("배경음.wav", "효과음.wav", "효과음2.wav", "소리.wav")
DIALOGUE_STEM = "대사만.wav"
MIX_STEM = "mix.wav"
GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 12.0
DUCK_DEFAULT_DB = 8.0
DUCK_MAX_DB = 40.0
DUCK_ATTACK_S = 0.15
DUCK_RELEASE_S = 0.15
MAX_ITEMS_PER_TRACK = 100
MAX_ITEMS = 200
MAX_KEYS = 64
MIN_ITEM_S = 0.04
MAX_NAME_LEN = 24
CEILING_DBTP = -1.0
LIMIT_LINEAR = 0.79  # −2 dBFS(4배 오버샘플 뒤 리미터) — 48kHz 로 내린 뒤 넘침(약 0.6 dB)까지 참 피크 −1 dBTP 아래
OVERSAMPLE_RATE = 192000
RATE = 48000
ENVELOPE_FRAME = 480  # 볼륨 포락선을 다시 세는 간격(샘플, 10ms)

# 내 소리 파일.
MAX_ASSET_BYTES = 50 * 1024 * 1024
MAX_ASSET_S = 600.0
MAX_ASSETS = 50
ASSET_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
}
_ASSET_ID_RE = re.compile(r"^a[0-9a-f]{16}$")
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bad(where: str, kind: str, message: str, status: int = 400) -> VpocError:
    return VpocError(where, kind, message, status)


def valid_asset_id(value) -> bool:
    return isinstance(value, str) and bool(_ASSET_ID_RE.match(value))


# ================================================================ 내 소리 올리기 검사


def sniff_ext(head: bytes) -> str:
    """파일 앞 바이트로 실제 형식(확장자)을 고른다. 모르면 ""."""
    head = bytes(head or b"")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:3] == b"ID3":
        return "mp3"
    if head[:4] == b"ADIF":
        return "aac"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
        return "aac"  # ADTS(MPEG-4/2 AAC, layer 00)
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"  # MPEG 오디오 프레임 동기
    return ""


def check_upload(filename, size, head: bytes, where: str = "vnew.edit.audio") -> tuple[str, str, str]:
    """올린 파일 이름 · 크기 · 앞 바이트 검사. 반환 (확장자, Content-Type, 보일 이름). 틀리면 400 · 413."""
    name = _CONTROL_RE.sub("", str(filename or "")).strip().replace("\\", "/").split("/")[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ASSET_TYPES:
        raise _bad(where, "BadAudioType", "소리 파일은 mp3 · wav · m4a · aac · ogg 만 올릴 수 있습니다.")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise _bad(where, "EmptyAudio", "빈 소리 파일입니다.")
    if size > MAX_ASSET_BYTES:
        raise _bad(where, "AudioTooLarge", f"소리 파일은 {MAX_ASSET_BYTES // (1024 * 1024)}MB 까지 올릴 수 있습니다.", 413)
    real = sniff_ext(head)
    if real not in ({"m4a", "aac"} if ext in ("m4a", "aac") else {ext}):
        raise _bad(where, "BadAudioType", "파일 내용이 소리 파일(mp3 · wav · m4a · aac · ogg)이 아닙니다.")
    label = name[: -(len(ext) + 1)].strip()[:60] or "소리"
    return ext, ASSET_TYPES[ext], label


def check_probe(info: dict, where: str = "vnew.edit.audio") -> float:
    """ffmpeg 로 잰 정보 검사. 반환 길이(초). 소리 없음 · 못 읽음 400, 10분 넘음 400."""
    duration = _num((info or {}).get("duration")) or 0.0
    if duration <= 0 or not (info or {}).get("hasAudio"):
        raise _bad(where, "AudioUnreadable", "소리 파일을 읽지 못했습니다. 다른 파일로 올려 주세요.")
    if duration > MAX_ASSET_S + 0.5:
        raise _bad(where, "AudioTooLong", f"소리 파일은 {MAX_ASSET_S / 60:.0f}분까지 올릴 수 있습니다.")
    return round(duration, 3)


# ================================================================ 문서 정리


def default_a1() -> dict:
    return {"gainDb": 0.0, "mute": False, "solo": False, "duck": False, "duckDb": DUCK_DEFAULT_DB}


def _gain(raw, where: str, what: str, default: float = 0.0) -> float:
    if raw is None:
        return default
    value = _num(raw)
    if value is None or value < GAIN_MIN_DB - 1e-9 or value > GAIN_MAX_DB + 1e-9:
        raise _bad(where, "BadAudioGain", f"{what} 이득은 {GAIN_MIN_DB:g}~{GAIN_MAX_DB:g} dB 사이 숫자여야 합니다.")
    return round(value, 2)


def _flag(raw, where: str, what: str) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise _bad(where, "BadAudioTrack", f"{what} 값은 참/거짓이어야 합니다.")
    return raw


def clean_a1(raw, where: str) -> dict:
    """A1(대사) 트랙 설정. 없으면 기본값."""
    if raw is None:
        return default_a1()
    if not isinstance(raw, dict):
        raise _bad(where, "BadAudioTrack", "대사 트랙(a1) 형식이 올바르지 않습니다.")
    duck_db = _num(raw.get("duckDb")) if raw.get("duckDb") is not None else DUCK_DEFAULT_DB
    if duck_db is None or duck_db < 0 or duck_db > DUCK_MAX_DB:
        raise _bad(where, "BadAudioDuck", f"자동 덕킹 깊이는 0~{DUCK_MAX_DB:g} dB 사이여야 합니다.")
    return {"gainDb": _gain(raw.get("gainDb"), where, "대사 트랙"), "mute": _flag(raw.get("mute"), where, "대사 트랙 mute"),
            "solo": _flag(raw.get("solo"), where, "대사 트랙 solo"), "duck": _flag(raw.get("duck"), where, "자동 덕킹"),
            "duckDb": round(duck_db, 2)}


def a1_is_default(a1: dict | None) -> bool:
    return not a1 or a1 == default_a1()


def item_length(item: dict) -> float:
    return float(item["out"]) - float(item["in"])


def _clean_item(raw, number: str, assets: dict, total_max: float, where: str, seen: set) -> dict:
    if not isinstance(raw, dict):
        raise _bad(where, "BadAudioItem", f"{number} 형식이 올바르지 않습니다.")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not _ITEM_ID_RE.match(item_id) or item_id in seen:
        raise _bad(where, "BadAudioItem", f"{number} id 가 올바르지 않거나 겹칩니다.")
    seen.add(item_id)
    asset_id = raw.get("assetId")
    asset = assets.get(asset_id) if valid_asset_id(asset_id) else None
    if not isinstance(asset, dict):
        raise _bad(where, "AudioAssetMissing", f"{number}의 소리 파일이 내 소리 목록에 없습니다.")
    duration = _num(asset.get("duration")) or 0.0
    start, src_in, src_out = _num(raw.get("start")), _num(raw.get("in")), _num(raw.get("out"))
    if start is None or src_in is None or src_out is None:
        raise _bad(where, "BadAudioItem", f"{number}의 시작 · 구간 값이 올바르지 않습니다.")
    start, src_in, src_out = round(start, 3), round(src_in, 3), round(min(src_out, duration), 3)
    if start < 0 or src_in < 0 or src_out - src_in < MIN_ITEM_S - 1e-9 or src_out > duration + 0.001:
        raise _bad(where, "BadAudioItem", f"{number} 구간은 소리 파일 길이({duration:.2f}초) 안에서 {MIN_ITEM_S}초 이상이어야 합니다.")
    length = src_out - src_in
    if start + length > total_max + 1e-6:
        raise _bad(where, "BadAudioItem", f"{number}가 타임라인 한도({total_max:.0f}초)를 넘습니다.")
    fade_in, fade_out = _num(raw.get("fadeIn") or 0), _num(raw.get("fadeOut") or 0)
    if fade_in is None or fade_out is None or fade_in < 0 or fade_out < 0 or fade_in + fade_out > length + 1e-6:
        raise _bad(where, "BadAudioFade", f"{number}의 페이드 인 · 아웃 합은 0초 이상 아이템 길이 이하여야 합니다.")
    keys_raw = raw.get("keys") or []
    if not isinstance(keys_raw, list) or len(keys_raw) > MAX_KEYS:
        raise _bad(where, "BadAudioKeys", f"{number}의 볼륨 키프레임은 {MAX_KEYS}개까지입니다.")
    keys = []
    for key in keys_raw:
        t = _num(key.get("t")) if isinstance(key, dict) else None
        db = _num(key.get("db")) if isinstance(key, dict) else None
        if t is None or db is None or t < -1e-9 or t > length + 1e-6 or db < GAIN_MIN_DB - 1e-9 or db > GAIN_MAX_DB + 1e-9:
            raise _bad(where, "BadAudioKeys", f"{number}의 볼륨 키프레임은 아이템 안 시각과 {GAIN_MIN_DB:g}~{GAIN_MAX_DB:g} dB 여야 합니다.")
        keys.append({"t": round(min(max(t, 0.0), length), 3), "db": round(db, 2)})
    keys.sort(key=lambda k: k["t"])
    out = {"id": item_id, "assetId": asset_id, "start": start, "in": src_in, "out": src_out,
           "gainDb": _gain(raw.get("gainDb"), where, number), "fadeIn": round(fade_in, 3), "fadeOut": round(fade_out, 3),
           "keys": keys}
    return out


def clean_tracks(raw, assets: dict, total_max: float, where: str) -> list[dict]:
    """audioTracks 검사 · 정리. assets = {assetId: {duration, …}}(내 소리 목록). 없으면 []."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad(where, "BadAudioTrack", "오디오 트랙(audioTracks)은 목록이어야 합니다.")
    if len(raw) > MAX_TRACKS:
        raise _bad(where, "TooManyAudioTracks", f"오디오 트랙은 대사 트랙 밖에 {MAX_TRACKS}개까지 둘 수 있습니다.")
    tracks: list[dict] = []
    seen_items: set = set()
    seen_tracks: set = set()
    count = 0
    for index, track in enumerate(raw):
        label = f"A{index + 2} 트랙"
        if not isinstance(track, dict):
            raise _bad(where, "BadAudioTrack", f"{label} 형식이 올바르지 않습니다.")
        track_id = track.get("id")
        if not isinstance(track_id, str) or not _TRACK_ID_RE.match(track_id) or track_id in seen_tracks:
            raise _bad(where, "BadAudioTrack", f"{label} id 가 올바르지 않거나 겹칩니다.")
        seen_tracks.add(track_id)
        name = track.get("name")
        name = _CONTROL_RE.sub("", name).strip()[:MAX_NAME_LEN] if isinstance(name, str) else ""
        items_raw = track.get("items") or []
        if not isinstance(items_raw, list) or len(items_raw) > MAX_ITEMS_PER_TRACK:
            raise _bad(where, "TooManyAudioItems", f"{label}에는 소리 아이템을 {MAX_ITEMS_PER_TRACK}개까지 둘 수 있습니다.")
        count += len(items_raw)
        if count > MAX_ITEMS:
            raise _bad(where, "TooManyAudioItems", f"소리 아이템은 모두 {MAX_ITEMS}개까지 둘 수 있습니다.")
        items = [_clean_item(item, f"{label} {n}번째 소리", assets, total_max, where, seen_items)
                 for n, item in enumerate(items_raw, 1)]
        items.sort(key=lambda i: (i["start"], i["id"]))
        tracks.append({"id": track_id, "name": name or TRACK_NAMES[index], "mute": _flag(track.get("mute"), where, f"{label} mute"),
                       "solo": _flag(track.get("solo"), where, f"{label} solo"),
                       "gainDb": _gain(track.get("gainDb"), where, label), "items": items})
    return tracks


def lenient_tracks(raw) -> list[dict]:
    """저장본 읽기용(검사 없이 꼴만): 목록이 아니면 []."""
    return [t for t in raw if isinstance(t, dict)][:MAX_TRACKS] if isinstance(raw, list) else []


def mix_active(tracks: list[dict] | None, a1: dict | None) -> bool:
    """내보내기 결과를 바꾸는 오디오 설정이 있는가(아이템이 있는 트랙 또는 기본값이 아닌 A1)."""
    return any(t.get("items") for t in tracks or []) or not a1_is_default(a1)


def hash_body(tracks: list[dict], a1: dict) -> dict:
    """문서 해시에 넣을 값(트랙 이름 · id 는 넣지 않는다)."""
    return {"a1": dict(a1 or default_a1()),
            "tracks": [{"mute": bool(t.get("mute")), "solo": bool(t.get("solo")), "gainDb": t.get("gainDb", 0.0),
                        "items": [{k: i[k] for k in ("assetId", "start", "in", "out", "gainDb", "fadeIn", "fadeOut", "keys")}
                                  for i in t.get("items") or []]} for t in tracks or []]}


def assets_used(tracks) -> set[str]:
    out: set[str] = set()
    for track in tracks or []:
        items = track.get("items") if isinstance(track, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and valid_asset_id(item.get("assetId")):
                out.add(item["assetId"])
    return out


# ================================================================ 들리는 트랙(mute · solo)


def audible(tracks: list[dict], a1: dict) -> tuple[bool, list[bool]]:
    """(A1 이 들리는가, 트랙별 들리는가). solo 가 하나라도 있으면 solo 인 트랙만, 그 밖은 mute 가 아닌 트랙."""
    a1 = a1 or default_a1()
    any_solo = bool(a1.get("solo")) or any(t.get("solo") for t in tracks)
    if any_solo:
        return bool(a1.get("solo")), [bool(t.get("solo")) for t in tracks]
    return not a1.get("mute"), [not t.get("mute") for t in tracks]


# ================================================================ 포락선 수학


def key_points(item: dict) -> list[tuple[float, float]]:
    """아이템 볼륨(dB) 꺾은선 [(아이템 시각, dB)]: 아이템 이득 + 키프레임(첫 키 앞 · 마지막 키 뒤는 그 값 그대로)."""
    gain = float(item.get("gainDb") or 0.0)
    keys = sorted(((float(k["t"]), float(k["db"])) for k in item.get("keys") or []), key=lambda p: p[0])
    if not keys:
        return [(0.0, gain)]
    return [(t, gain + db) for t, db in keys]


def points_value(points: list[tuple[float, float]], t: float) -> float:
    """꺾은선 값(dB). 첫 점 앞은 첫 값, 마지막 뒤는 마지막 값."""
    if not points:
        return 0.0
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t <= t1:
            return v0 if t1 <= t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def fade_gain(item: dict, t: float) -> float:
    """페이드 인 · 아웃 직선 이득(0~1, 아이템 시각 t). 끝점은 0(−inf dB)."""
    length = item_length(item)
    gain = 1.0
    fade_in, fade_out = float(item.get("fadeIn") or 0), float(item.get("fadeOut") or 0)
    if fade_in > 0:
        gain = min(gain, max(0.0, t / fade_in))
    if fade_out > 0:
        gain = min(gain, max(0.0, (length - t) / fade_out))
    return gain


def item_gain(item: dict, t: float) -> float:
    """아이템 시각 t 의 선형 이득 = 10^(키프레임 dB / 20) × 페이드."""
    return 10 ** (points_value(key_points(item), t) / 20.0) * fade_gain(item, t)


def merge_spans(spans, gap: float = 0.0) -> list[list[float]]:
    clean = sorted([float(a), float(b)] for a, b in spans or [] if float(b) > float(a))
    out: list[list[float]] = []
    for a, b in clean:
        if out and a <= out[-1][1] + gap + 1e-9:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def duck_points(spans, depth_db: float, *, attack: float = DUCK_ATTACK_S, release: float = DUCK_RELEASE_S) -> list[tuple[float, float]]:
    """대사 구간 → 덕킹 꺾은선 [(타임라인 초, dB)]. 대사 시작 attack 초 전부터 −depth 로 내려가 끝난 뒤 release 초에 0 으로 돌아온다.

    두 구간 사이가 attack + release 보다 짧으면 하나로 잇는다(그 사이에서 올라갔다 내려가지 않는다). 구간이 없거나 깊이가 0 이면 [].
    """
    depth = float(depth_db or 0.0)
    merged = merge_spans(spans, attack + release)
    if depth <= 0 or not merged:
        return []
    points: list[tuple[float, float]] = []
    for a, b in merged:
        down = max(0.0, a - attack)
        if points and down <= points[-1][0]:
            down = points[-1][0]
        if not points or down > points[-1][0] or points[-1][1] != 0.0:
            points.append((round(down, 4), 0.0))
        points.append((round(max(a, down), 4), -depth))
        points.append((round(b, 4), -depth))
        points.append((round(b + release, 4), 0.0))
    return points


def piecewise_expr(points: list[tuple[float, float]], var: str = "t") -> str:
    """꺾은선(dB) → ffmpeg 식. y0 + Σ (y[i+1] − y[i]) × clip((t − t[i]) / (t[i+1] − t[i]), 0, 1). 같은 시각 두 점은 계단(gte)."""
    if not points:
        return "0"
    terms = [f"{points[0][1]:.3f}"]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        delta = v1 - v0
        if abs(delta) < 1e-9:
            continue
        if t1 - t0 <= 1e-6:
            terms.append(f"{delta:+.3f}*gte({var},{t1:.4f})")
        else:
            terms.append(f"{delta:+.3f}*clip(({var}-{t0:.4f})/{t1 - t0:.4f},0,1)")
    return "".join(terms)


def db_volume(points: list[tuple[float, float]]) -> str:
    """volume 필터 인자(eval=frame). 상수면 '<dB>dB' 한 줄."""
    if len(points) <= 1 or all(abs(v - points[0][1]) < 1e-9 for _, v in points):
        return f"volume={(points[0][1] if points else 0.0):.3f}dB"
    return f"volume='pow(10,({piecewise_expr(points)})/20)':eval=frame"


# ================================================================ 대사 구간(A1 PCM → 피크)


def pcm_peaks(path: str, per_second: int = 20, rate: int = RATE) -> list[float]:
    """48kHz 스테레오 s16le 원시 PCM → 1초 per_second 개 피크(0~1, 소수 3자리). 파일 단위로 읽는다."""
    import array  # noqa: PLC0415
    import sys  # noqa: PLC0415

    block = (rate // per_second) * 4
    peaks: list[float] = []
    if not path or not os.path.exists(path):
        return peaks
    with open(path, "rb") as handle:
        while True:
            data = handle.read(block)
            if len(data) < 2:
                break
            data = data[: len(data) - len(data) % 2]
            samples = array.array("h")
            samples.frombytes(data)
            if sys.byteorder == "big":
                samples.byteswap()
            peak = max(max(samples), -min(samples)) if samples else 0
            peaks.append(round(min(1.0, peak / 32768.0), 3))
    return peaks


# ================================================================ 섞기 계획 · 필터 그래프


def mix_plan(tracks: list[dict], a1: dict, *, total_samples: int, spans=None, stems: bool = False) -> dict:
    """섞기 계획(순수). 트랙 · 아이템 중 타임라인 안에서 들리는 것만, 쓰는 소리 파일(입력 순서), 덕킹 꺾은선, 스템 이름."""
    a1 = a1 or default_a1()
    a1_on, track_on = audible(tracks, a1)
    total_s = total_samples / RATE
    inputs: list[str] = []  # assetId 순서(ffmpeg 입력 1번부터)
    buses = []
    for index, track in enumerate(tracks):
        items = []
        for item in track.get("items") or []:
            if float(item["start"]) >= total_s - 1e-6:
                continue
            if item["assetId"] not in inputs:
                inputs.append(item["assetId"])
            items.append(item)
        if not track_on[index] or not items:
            continue
        buses.append({"index": index, "track": track, "items": items,
                      "stem": STEM_NAMES[index] if index < len(STEM_NAMES) else f"트랙{index + 2}.wav"})
    used = {i["assetId"] for b in buses for i in b["items"]}
    inputs = [a for a in inputs if a in used]
    duck = duck_points(spans or [], float(a1.get("duckDb") or 0.0)) if a1.get("duck") and a1_on else []
    return {"a1": a1, "a1On": a1_on, "inputs": inputs, "buses": buses, "duck": duck, "totalSamples": int(total_samples),
            "stems": bool(stems)}


def mix_graph(plan: dict) -> tuple[str, list[tuple[str, str]]]:
    """계획 → filter_complex 글과 출력 [(라벨, 종류)]. 종류: "raw"(48k s16le — mp4 소리), "mix"(mix.wav), 그 밖은 스템 파일 이름.

    입력 0 = A1 원시 PCM, 입력 k(1부터) = plan.inputs[k−1] 소리 파일.
    """
    total = int(plan["totalSamples"])
    tail = f"apad=whole_len={total},atrim=end_sample={total}"
    fmt = f"aresample={RATE},aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=stereo"
    lines: list[str] = []
    outputs: list[tuple[str, str]] = []
    uses: dict[str, int] = {}
    for bus in plan["buses"]:
        for item in bus["items"]:
            uses[item["assetId"]] = uses.get(item["assetId"], 0) + 1
    split_no: dict[str, int] = {}
    for k, asset in enumerate(plan["inputs"], 1):
        count = uses.get(asset, 0)
        if count > 1:
            lines.append(f"[{k}:a]asplit={count}" + "".join(f"[s{k}_{j}]" for j in range(count)))
        split_no[asset] = 0

    def source_label(asset: str) -> str:
        k = plan["inputs"].index(asset) + 1
        if uses.get(asset, 0) > 1:
            j = split_no[asset]
            split_no[asset] = j + 1
            return f"[s{k}_{j}]"
        return f"[{k}:a]"

    a1 = plan["a1"]
    buses_out: list[tuple[str, str]] = []  # (라벨, 스템 이름)
    if plan["a1On"]:
        lines.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=stereo,"
                     f"volume={float(a1.get('gainDb') or 0.0):.3f}dB,{tail}[d]")
        buses_out.append(("[d]", DIALOGUE_STEM))
    for bus in plan["buses"]:
        t = bus["index"]
        labels = []
        for n, item in enumerate(bus["items"]):
            length = item_length(item)
            chain = [fmt, f"atrim=start={float(item['in']):.4f}:end={float(item['out']):.4f}", "asetpts=PTS-STARTPTS",
                     f"asetnsamples=n={ENVELOPE_FRAME}:p=0", db_volume(key_points(item))]
            if float(item.get("fadeIn") or 0) > 0:
                chain.append(f"afade=t=in:st=0:d={float(item['fadeIn']):.4f}:curve=tri")
            if float(item.get("fadeOut") or 0) > 0:
                chain.append(f"afade=t=out:st={length - float(item['fadeOut']):.4f}:d={float(item['fadeOut']):.4f}:curve=tri")
            delay = int(round(float(item["start"]) * RATE))
            if delay > 0:
                chain.append(f"adelay=delays={delay}S:all=1")
            label = f"[i{t}_{n}]"
            lines.append(source_label(item["assetId"]) + ",".join(chain) + label)
            labels.append(label)
        mixed = ("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0,"
                 if len(labels) > 1 else labels[0] + "anull,")
        chain = [f"volume={float(bus['track'].get('gainDb') or 0.0):.3f}dB"]
        if plan["duck"]:
            chain += [f"asetnsamples=n={ENVELOPE_FRAME}:p=0", db_volume(plan["duck"])]
        chain.append(tail)
        lines.append(mixed + ",".join(chain) + f"[t{t}]")
        buses_out.append((f"[t{t}]", bus["stem"]))
    stems = plan["stems"]
    mix_inputs = []
    for label, stem in buses_out:
        if stems:
            name = label.strip("[]")
            lines.append(f"{label}asplit=2[{name}m][{name}s]")
            mix_inputs.append(f"[{name}m]")
            outputs.append((f"[{name}s]", stem))
        else:
            mix_inputs.append(label)
    limiter = (f"aresample={OVERSAMPLE_RATE},alimiter=limit={LIMIT_LINEAR}:attack=5:release=50:level=0,"
               f"aresample={RATE},{tail}")
    if not mix_inputs:
        lines.append(f"[0:a]volume=0,{tail}[mx]")
    elif len(mix_inputs) == 1:
        lines.append(f"{mix_inputs[0]}{limiter}[mx]")
    else:
        lines.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0:duration=longest:dropout_transition=0,"
                     f"{limiter}[mx]")
    if stems:
        lines.append("[mx]asplit=2[mxr][mxw]")
        outputs = [("[mxr]", "raw"), ("[mxw]", "mix")] + outputs
    else:
        outputs = [("[mx]", "raw")] + outputs
    return ";\n".join(lines), outputs


def stem_list(plan: dict) -> list[str]:
    """스템 zip 에 들어갈 파일 이름(순서대로): 대사만 · 트랙들 · mix.wav. 스템을 고르지 않았으면 []."""
    if not plan.get("stems"):
        return []
    names = ([DIALOGUE_STEM] if plan.get("a1On") else []) + [b["stem"] for b in plan.get("buses") or []]
    return names + [MIX_STEM]


def mix_command(binary: str, plan: dict, a1_raw: str, asset_paths: dict, graph_path: str, workdir: str) -> tuple[list[str], dict]:
    """ffmpeg 명령과 출력 경로 {종류: 경로}. 필터 그래프는 graph_path 파일로(-filter_complex_script — 명령 줄 길이 한도를 피한다)."""
    graph, outputs = mix_graph(plan)
    with open(graph_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(graph)
    command = [binary, "-y", "-hide_banner", "-loglevel", "error", "-threads", "1",
               "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", a1_raw]
    for asset in plan["inputs"]:
        command += ["-i", asset_paths[asset]]
    command += ["-filter_complex_script", graph_path]
    paths: dict[str, str] = {}
    for n, (label, kind) in enumerate(outputs):
        if kind == "raw":
            path = os.path.join(workdir, "mix.raw")
            command += ["-map", label, "-c:a", "pcm_s16le", "-f", "s16le", path]
        elif kind == "mix":
            path = os.path.join(workdir, "stem-mix.wav")
            command += ["-map", label, "-c:a", "pcm_s24le", path]
        else:
            path = os.path.join(workdir, f"stem-{n:02d}.wav")
            command += ["-map", label, "-c:a", "pcm_f32le", path]
        paths[kind] = path
    return command, paths


def render_mix(plan: dict, a1_raw: str, asset_paths: dict, workdir: str) -> dict:
    """ffmpeg 한 번으로 섞는다(stitcher._edit_run — nice 10, 시간 상한). 반환 {"raw": 경로, "stems": [(이름, 경로)]}."""
    from .. import stitcher  # noqa: PLC0415

    command, paths = mix_command(stitcher.ffmpeg_path(), plan, a1_raw, asset_paths, os.path.join(workdir, "mix-graph.txt"), workdir)
    result = stitcher._edit_run(command)  # noqa: SLF001
    raw = paths.get("raw", "")
    if result.returncode != 0 or not raw or not os.path.exists(raw):
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise stitcher.StitchError(f"오디오 트랙을 섞지 못했습니다: {detail}")
    stitcher._fit_pcm(raw, int(plan["totalSamples"]) * 4)  # noqa: SLF001
    stems = []
    for name in stem_list(plan):
        kind = "mix" if name == MIX_STEM else name
        if kind in paths and os.path.exists(paths[kind]):
            stems.append((name, paths[kind]))
    return {"raw": raw, "stems": stems}
