"""작업 215b: ⑦ 편집 — 자막(captions)·타이틀(titles)·전환(블랙/화이트로 바꾸기 · 시작/끝 페이드)·클립 색 보정(Lumetri-lite).

유료 호출·AI 호출은 없다. 편집 문서(edit.py, 그림체마다 edit-<variant>.json)에 아래 키를 더한다. 모두 선택이고, 없거나 기본값이면
저장하지 않는다 — 이 키가 없는 문서(작업 215b 전 문서)는 저장 모양 · 해시(edit._doc_hash) · 내보내기 명령이 그대로다.
  문서: captions [{id, start, end, text, style{size, color, back, pos, italic}, lineId?}] — 타임라인 초. lineId 는 ⑥ 대사 줄.
        titles [{id, start, end, text, size, pos(1~9, 왼쪽 위부터 읽는 순서), fadeIn, fadeOut, box, color}] — V2 겹침 트랙.
        fadeIn · fadeOut(초) — 처음은 검은 화면에서 밝아지기, 끝은 검은 화면으로 어두워지기. captionBurnIn false = 자막을 굽지 않는다.
  클립: transition {type: "dipBlack" | "dipWhite", seconds} — 다음 클립으로 갈 때 검은(흰) 화면으로 사라졌다 나타나기(겹치지 않는다,
        길이 그대로 — 디졸브와 달리 타임라인이 짧아지지 않는다). color {brightness, contrast, saturation, temperature, tint} — −100~100.
내보내기: edit.run_export 가 클립을 이은 뒤(edit_join) 효과가 있으면 finish() 가 ffmpeg 한 번으로 마무리 패스를 한다(소리는 복사):
  색 보정 eq · colortemperature · colorbalance(클립 프레임 구간 enable) → 블랙/화이트 전환 fade(경계 앞 out · 뒤 in, enable) →
  시작/끝 fade → 자막·타이틀 ass(libass, 글꼴 폴더). 프레임 수는 바뀌지 않는다.
글꼴: 자막을 구우려면 한글 글꼴 파일이 필요하다. 찾는 순서 = 환경변수 VNEW_CAPTION_FONT → app/src/vnew/fonts/ 의 NotoSansKR*·*.ttf·*.otf
  (Dockerfile 이 src/ 를 통째로 복사하므로 여기에 두면 이미지에 들어간다) → 시스템 글꼴(Noto CJK·나눔·맑은 고딕). 지금 저장소에는 글꼴 파일이
  없고 앱 이미지(python:3.13-slim)에도 한글 글꼴이 없다 — 글꼴이 없으면 굽기가 필요한 내보내기는 409 CaptionFontMissing(SRT 내려받기는 된다).
자막 자동 만들기(auto_captions): ⑥ 사운드 문서(sound.json)의 대사 줄 시간 → ⑥ 원본 조각(line_redo._pieces, 기준 원본) → 재료 초 →
  지금 ⑦ 클립의 타임라인 초. 글은 작업 212 대사 고침(sound/lines.json overrides)이 대본보다 먼저다. 속마음은 기울임꼴이 기본.
라우트(register_routes, routes.py 한 줄): POST /p/<pid>/edit/captions/auto · POST /p/<pid>/edit/captions/srt.
외부 호출 래퍼(검사가 바꾼다): _run_ffmpeg · _has_ass · _find_font.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess

from ..vpoc.errors import VpocError

log = logging.getLogger("vpoc")

# ================================================================ 상수

DIP_TYPES = ("dipBlack", "dipWhite")
DIP_COLORS = {"dipBlack": "black", "dipWhite": "white"}
DIP_SECONDS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
DIP_MAX_SHARE = 0.9  # 블랙/화이트 전환 길이 ≤ 앞뒤 두 클립 중 짧은 쪽 × 0.9(경계 앞뒤로 반씩 — 한쪽 45%, 디졸브와 같은 몫)
EDGE_FADE_SECONDS = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)
COLOR_KEYS = ("brightness", "contrast", "saturation", "temperature", "tint")
COLOR_MAX = 100
# 색 보정 → ffmpeg 값(슬라이더 ±100 끝). eq brightness ±0.25(범위 −1~1) · contrast 0.5~1.5(−1000~1000) · saturation 0~2(0~3) ·
# colortemperature 3500~9500K(1000~40000, 낮을수록 따뜻하다 — 검사 tx-d 가 회색 화면으로 확인) · colorbalance 그림자·중간·밝은 곳
# 초록-자홍 ∓0.15(−1~1, 양수 tint = 자홍 = 초록을 뺀다. 중간 톤만 바꾸면 회색 0.5 근처가 거의 그대로라 세 곳을 함께 민다).
COLOR_BRIGHTNESS = 0.25
COLOR_CONTRAST = 0.5
COLOR_TEMP_K = 6500
COLOR_TEMP_STEP = 30
COLOR_TINT = 0.15
MAX_CAPTIONS = 400
MAX_TITLES = 40
CAPTION_TEXT_MAX = 200
TITLE_TEXT_MAX = 120
TEXT_LINES_MAX = 3
MIN_ITEM_S = 0.1
TITLE_FADE_MAX = 3.0
CAPTION_SIZES = {"s": 0.045, "m": 0.055, "l": 0.07}  # 글자 높이 = 화면 높이 × 값
TITLE_SIZES = {"s": 0.05, "m": 0.075, "l": 0.1, "xl": 0.14}
CAPTION_BACKS = ("outline", "box")
CAPTION_POS = ("bottom", "top")
CAPTION_STYLE = {"size": "m", "color": "#ffffff", "back": "outline", "pos": "bottom", "italic": False}
TITLE_DEFAULT = {"size": "l", "pos": 5, "fadeIn": 0.5, "fadeOut": 0.5, "box": False, "color": "#ffffff"}
ASS_ALIGN = {1: 7, 2: 8, 3: 9, 4: 4, 5: 5, 6: 6, 7: 1, 8: 2, 9: 3}  # 9칸(읽는 순서) → ASS \an(숫자판)
CAPTION_MARGIN = 0.06  # 자막 위·아래 여백(화면 높이 몫)
TITLE_MARGIN = 0.05
BOX_ALPHA = "60"  # 상자 투명도(ASS 알파, 00 = 불투명)
MERGE_GAP_S = 0.25  # 자동 자막: 같은 줄이 이만큼 안에서 끊기면 한 자막으로 본다
FONT_ENV = "VNEW_CAPTION_FONT"
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
SYSTEM_FONTS = (
    "/usr/share/fonts/**/NotoSansKR*.[ot]tf", "/usr/share/fonts/**/NotoSansCJK*.tt[cf]", "/usr/share/fonts/**/NotoSansCJK*.otf",
    "/usr/share/fonts/**/Nanum*Gothic*.ttf", "C:/Windows/Fonts/malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)
FONT_MISSING_TEXT = "자막·타이틀을 영상에 넣을 한글 글꼴이 서버에 없습니다. 자막 굽기를 끄고 SRT 로 내려받거나 관리자에게 글꼴 설치를 요청해 주세요."
ASS_MISSING_TEXT = "이 서버의 ffmpeg 에 자막 필터(ass)가 없어 자막·타이틀을 영상에 넣을 수 없습니다. 자막 굽기를 끄고 SRT 로 내려받아 주세요."
MAX_BODY_BYTES = 256 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_cache: dict = {}


def _e():
    from . import edit  # noqa: PLC0415 — 요청 때 읽는다(edit 가 이 모듈을 import 한다)

    return edit


def _r3(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _num(value):
    return _e()._num(value)  # noqa: SLF001


# ================================================================ 클립 효과(전환 · 색)


def is_dip(raw) -> bool:
    """transition 이 블랙/화이트 전환 모양인가(값 검사는 clean_clip_fx)."""
    return isinstance(raw, dict) and raw.get("type") in DIP_TYPES


def dip_of(clip) -> tuple[str, float]:
    """클립의 블랙/화이트 전환 (type, 초). 없거나 틀리면 ("", 0)."""
    raw = clip.get("transition") if isinstance(clip, dict) else None
    if not is_dip(raw):
        return "", 0.0
    value = _num(raw.get("seconds"))
    return (raw["type"], value) if value in DIP_SECONDS else ("", 0.0)


def _clean_color(raw, number: int, where: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise VpocError(where, "BadClipColor", f"{number}번째 클립의 색 보정(color) 값이 올바르지 않습니다.", 400)
    out = {}
    for key in COLOR_KEYS:
        if raw.get(key) is None:
            continue
        value = _num(raw.get(key))
        if value is None or abs(value) > COLOR_MAX:
            raise VpocError(where, "BadClipColor", f"{number}번째 클립의 색 보정 {key} 는 −{COLOR_MAX}~{COLOR_MAX} 사이 숫자여야 합니다.", 400)
        value = int(round(value))
        if value:
            out[key] = value
    return out


def color_of(clip) -> dict:
    """클립 색 보정(0 이 아닌 값만, 범위 밖·글자는 버린다). 없으면 {}."""
    raw = clip.get("color") if isinstance(clip, dict) else None
    try:
        return _clean_color(raw, 0, "vnew.edit") if isinstance(raw, dict) else {}
    except VpocError:
        return {}


def clean_clip_fx(clip: dict, number: int, where: str) -> dict:
    """edit._clean_clips 가 부른다: 저장할 블랙/화이트 전환·색 보정 {transition?, color?}. 틀리면 400(BadTransition·BadClipColor)."""
    out: dict = {}
    raw = clip.get("transition")
    if is_dip(raw):
        value = _num(raw.get("seconds"))
        if value not in DIP_SECONDS:
            raise VpocError(where, "BadTransition",
                            f"{number}번째 클립의 블랙/화이트 전환 길이는 {'·'.join(f'{s:g}' for s in DIP_SECONDS)}초 중 하나여야 합니다.", 400)
        out["transition"] = {"type": raw["type"], "seconds": value}
    color = _clean_color(clip.get("color"), number, where)
    if color:
        out["color"] = color
    return out


def fit_dips(clips: list[dict]) -> None:
    """블랙/화이트 전환이 앞뒤 두 클립 중 짧은 쪽 × DIP_MAX_SHARE 보다 길면 맞는 가장 긴 값으로 줄이고, 없으면 컷(화면 vnew.js
    editTxTidy 와 같은 규칙 — 끝 다듬기로 클립이 짧아져도 저장이 400 이 되지 않는다). 마지막 클립 전환은 edit._check_overlaps 가 막는다."""
    E = _e()
    for item, after in zip(clips, clips[1:]):
        kind, seconds = dip_of(item)
        if not kind:
            continue
        limit = DIP_MAX_SHARE * min(E._clip_seconds(item), E._clip_seconds(after)) + 1e-9  # noqa: SLF001
        if seconds <= limit:
            continue
        fit = [s for s in DIP_SECONDS if s <= limit]
        if fit:
            item["transition"] = {"type": kind, "seconds": fit[-1]}
        else:
            item.pop("transition", None)


def color_filters(color: dict) -> list[str]:
    """색 보정 → ffmpeg 필터 글(enable 없이). 0 인 항목은 넣지 않는다. 값은 COLOR_* 몫(범위 안 — 검사 tx-e)."""
    c = {k: max(-COLOR_MAX, min(COLOR_MAX, int(color.get(k) or 0))) for k in COLOR_KEYS}
    out = []
    if c["brightness"] or c["contrast"] or c["saturation"]:
        out.append(f"eq=brightness={c['brightness'] / 100 * COLOR_BRIGHTNESS:.4f}:contrast={1 + c['contrast'] / 100 * COLOR_CONTRAST:.4f}"
                   f":saturation={1 + c['saturation'] / 100:.4f}")
    if c["temperature"]:
        out.append(f"colortemperature=temperature={COLOR_TEMP_K - c['temperature'] * COLOR_TEMP_STEP}")
    if c["tint"]:
        g = -c["tint"] / 100 * COLOR_TINT
        out.append(f"colorbalance=gs={g:.4f}:gm={g:.4f}:gh={g:.4f}")
    return out


# ================================================================ 문서 키(자막 · 타이틀 · 시작/끝 페이드)


def _text(raw, limit: int) -> str:
    """글: 제어 문자 빼기(줄바꿈은 남김) · 줄마다 앞뒤 공백 · 빈 줄 빼기 · 최대 TEXT_LINES_MAX 줄 · limit 자."""
    if not isinstance(raw, str):
        return ""
    lines = [line.strip() for line in _CONTROL_RE.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).split("\n")]
    return "\n".join([line for line in lines if line][:TEXT_LINES_MAX])[:limit]


def _clean_times(item: dict, number: int, where: str, kind: str, name: str) -> tuple[float, float]:
    E = _e()
    start, end = _num(item.get("start")), _num(item.get("end"))
    if start is None or end is None or start < 0 or end > E.MAX_TOTAL_S + 1e-6 or end - start < MIN_ITEM_S - 1e-9:
        raise VpocError(where, kind, f"{number}번째 {name}의 시작·끝은 0~{E.MAX_TOTAL_S:.0f}초 안에서 {MIN_ITEM_S}초 이상이어야 합니다.", 400)
    return _r3(start), _r3(end)


def _clean_id(item: dict, number: int, where: str, kind: str, name: str, seen: set) -> str:
    value = item.get("id")
    if not isinstance(value, str) or not _ID_RE.match(value) or value in seen:
        raise VpocError(where, kind, f"{number}번째 {name} id 가 올바르지 않거나 겹칩니다.", 400)
    seen.add(value)
    return value


def _clean_hex(raw, fallback: str, number: int, where: str, kind: str, name: str) -> str:
    if raw is None:
        return fallback
    if not isinstance(raw, str) or not _HEX_RE.match(raw):
        raise VpocError(where, kind, f"{number}번째 {name}의 색은 #rrggbb 여야 합니다.", 400)
    return raw.lower()


def clean_style(raw, number: int = 0, where: str = "vnew.edit", kind: str = "BadCaption") -> dict:
    """자막 모양. 없는 항목은 기본값(CAPTION_STYLE)."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(CAPTION_STYLE)
    for key, allowed in (("size", tuple(CAPTION_SIZES)), ("back", CAPTION_BACKS), ("pos", CAPTION_POS)):
        if raw.get(key) is not None:
            if raw.get(key) not in allowed:
                raise VpocError(where, kind, f"{number}번째 자막 모양({key})이 올바르지 않습니다.", 400)
            out[key] = raw[key]
    out["color"] = _clean_hex(raw.get("color"), out["color"], number, where, kind, "자막")
    if raw.get("italic") is not None:
        if not isinstance(raw.get("italic"), bool):
            raise VpocError(where, kind, f"{number}번째 자막 기울임(italic)은 참/거짓이어야 합니다.", 400)
        out["italic"] = raw["italic"]
    return out


def clean_captions(raw, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CAPTIONS:
        raise VpocError(where, "BadCaption", f"자막(captions)은 {MAX_CAPTIONS}개까지의 목록이어야 합니다.", 400)
    out, seen = [], set()
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise VpocError(where, "BadCaption", f"{number}번째 자막 형식이 올바르지 않습니다.", 400)
        cap_id = _clean_id(item, number, where, "BadCaption", "자막", seen)
        start, end = _clean_times(item, number, where, "BadCaption", "자막")
        text = _text(item.get("text"), CAPTION_TEXT_MAX)
        if not text:
            raise VpocError(where, "BadCaption", f"{number}번째 자막 글이 비었습니다.", 400)
        cap = {"id": cap_id, "start": start, "end": end, "text": text, "style": clean_style(item.get("style"), number, where)}
        line_id = item.get("lineId")
        if isinstance(line_id, str) and _ID_RE.match(line_id):
            cap["lineId"] = line_id
        out.append(cap)
    return sorted(out, key=lambda c: (c["start"], c["end"]))


def clean_titles(raw, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_TITLES:
        raise VpocError(where, "BadTitle", f"타이틀(titles)은 {MAX_TITLES}개까지의 목록이어야 합니다.", 400)
    out, seen = [], set()
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise VpocError(where, "BadTitle", f"{number}번째 타이틀 형식이 올바르지 않습니다.", 400)
        title_id = _clean_id(item, number, where, "BadTitle", "타이틀", seen)
        start, end = _clean_times(item, number, where, "BadTitle", "타이틀")
        text = _text(item.get("text"), TITLE_TEXT_MAX)
        if not text:
            raise VpocError(where, "BadTitle", f"{number}번째 타이틀 글이 비었습니다.", 400)
        title = {"id": title_id, "start": start, "end": end, "text": text}
        size = item.get("size", TITLE_DEFAULT["size"])
        pos = item.get("pos", TITLE_DEFAULT["pos"])
        if size not in TITLE_SIZES or not isinstance(pos, int) or isinstance(pos, bool) or pos not in ASS_ALIGN:
            raise VpocError(where, "BadTitle", f"{number}번째 타이틀 크기·자리(1~9)가 올바르지 않습니다.", 400)
        title.update(size=size, pos=pos)
        for key in ("fadeIn", "fadeOut"):
            value = _num(item.get(key, TITLE_DEFAULT[key]))
            if value is None or value < 0 or value > TITLE_FADE_MAX + 1e-9:
                raise VpocError(where, "BadTitle", f"{number}번째 타이틀 페이드는 0~{TITLE_FADE_MAX:g}초여야 합니다.", 400)
            title[key] = round(round(value / 0.05) * 0.05, 2)
        box = item.get("box", False)
        if not isinstance(box, bool):
            raise VpocError(where, "BadTitle", f"{number}번째 타이틀 상자(box)는 참/거짓이어야 합니다.", 400)
        title["box"] = box
        title["color"] = _clean_hex(item.get("color"), TITLE_DEFAULT["color"], number, where, "BadTitle", "타이틀")
        out.append(title)
    return sorted(out, key=lambda t: (t["start"], t["end"]))


def _clean_edge(raw, where: str, name: str) -> float:
    if raw in (None, 0):
        return 0.0
    value = _num(raw)
    if value not in EDGE_FADE_SECONDS:
        raise VpocError(where, "BadFade", f"{name}은 {'·'.join(f'{s:g}' for s in EDGE_FADE_SECONDS)}초 중 하나여야 합니다.", 400)
    return value


def clean_doc(payload: dict, where: str) -> dict:
    """edit.save_doc 가 부른다: 저장할 문서 키(있고 기본값이 아닌 것만). 틀리면 400 BadCaption·BadTitle·BadFade."""
    out: dict = {}
    captions = clean_captions(payload.get("captions"), where)
    if captions:
        out["captions"] = captions
    titles = clean_titles(payload.get("titles"), where)
    if titles:
        out["titles"] = titles
    for key, name in (("fadeIn", "시작 페이드(fadeIn)"), ("fadeOut", "끝 페이드(fadeOut)")):
        value = _clean_edge(payload.get(key), where, name)
        if value:
            out[key] = value
    burn = payload.get("captionBurnIn")
    if burn is not None and not isinstance(burn, bool):
        raise VpocError(where, "BadCaption", "자막 굽기(captionBurnIn)는 참/거짓이어야 합니다.", 400)
    if burn is False:
        out["captionBurnIn"] = False
    return out


def _lenient(fn, raw):
    try:
        return fn(raw, "vnew.edit")
    except VpocError:
        return []


def text_fx(doc: dict | None) -> dict:
    """내보내기가 쓰는 문서 효과(틀린 값은 버린다): {captions(굽기 켬일 때만), titles, fadeIn, fadeOut}. 없으면 {}."""
    doc = doc if isinstance(doc, dict) else {}
    out: dict = {}
    if doc.get("captionBurnIn") is not False:
        captions = _lenient(clean_captions, doc.get("captions"))
        if captions:
            out["captions"] = captions
    titles = _lenient(clean_titles, doc.get("titles"))
    if titles:
        out["titles"] = titles
    for key in ("fadeIn", "fadeOut"):
        value = _num(doc.get(key))
        if value in EDGE_FADE_SECONDS:
            out[key] = value
    return out


def clip_fx(clips: list) -> dict:
    """해시용 클립 효과 {dips: [[번호, type, 초]], colors: [[번호, {…}]]}. 없으면 {}."""
    dips, colors = [], []
    for index, clip in enumerate(clips or []):
        kind, seconds = dip_of(clip)
        if kind and index < len(clips) - 1:
            dips.append([index, kind, seconds])
        color = color_of(clip)
        if color:
            colors.append([index, color])
    return {k: v for k, v in (("dips", dips), ("colors", colors)) if v}


def extend_hash(doc_hash: str, doc: dict | None, clips: list) -> str:
    """edit._doc_hash 에 이 작업의 효과를 더한 해시. 효과가 하나도 없으면 받은 해시 그대로(215b 전 문서·내보내기 재사용 그대로)."""
    extra = {**text_fx(doc), **clip_fx(clips)}
    if not doc_hash or not extra:
        return doc_hash
    text = json.dumps({"base": doc_hash, "fx215b": extra}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def export_extras(doc: dict | None) -> dict:
    """edit.start_export 의 시작 사본에 더할 키 {textFx}(효과가 있을 때만). 클립 효과는 사본 clips 안에 있다."""
    fx = text_fx(doc)
    return {"textFx": fx} if fx else {}


def needs_text(fx: dict) -> bool:
    return bool(fx.get("captions") or fx.get("titles"))


def check_export(doc: dict | None, where: str) -> None:
    """굽기가 필요한데(자막 굽기 켬 + 자막, 또는 타이틀) 글꼴·ass 필터가 없으면 409(내보내기를 시작하지 않는다)."""
    if not needs_text(text_fx(doc)):
        return
    info = burn_info()
    if not info["available"]:
        raise VpocError(where, "CaptionFontMissing", info["reason"], 409)


# ================================================================ 글꼴 · ffmpeg


def _find_font() -> str:
    """한글 글꼴 파일 경로(없으면 ""). 환경변수 → app/src/vnew/fonts → 시스템."""
    explicit = os.getenv(FONT_ENV, "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    patterns = [os.path.join(FONT_DIR, "NotoSansKR*.[ot]tf"), os.path.join(FONT_DIR, "*.[ot]tf"), os.path.join(FONT_DIR, "*.ttc")]
    for pattern in patterns + list(SYSTEM_FONTS):
        found = sorted(glob.glob(pattern, recursive=True))
        if found:
            return found[0]
    return ""


def _font_family(path: str) -> str:
    try:
        from PIL import ImageFont  # noqa: PLC0415

        family = ImageFont.truetype(path, 12).getname()[0]
        if family:
            return str(family)
    except Exception:  # noqa: BLE001 — 이름을 못 읽으면 파일 이름
        pass
    return os.path.splitext(os.path.basename(path))[0]


def _has_ass() -> bool:
    """ffmpeg 에 ass 필터가 있는가(한 번 재고 기억한다)."""
    from .. import stitcher  # noqa: PLC0415

    try:
        proc = subprocess.run([stitcher.ffmpeg_path(), "-hide_banner", "-filters"], capture_output=True, timeout=30, check=False)
    except Exception:  # noqa: BLE001
        return False
    return bool(re.search(rb"^\s*\S+\s+ass\s", proc.stdout or b"", re.M))


def burn_info() -> dict:
    """자막·타이틀 굽기 가능 여부 {available, font(파일 이름), family, reason}. 프로세스마다 한 번 잰다."""
    if "burn" not in _cache:
        path = _find_font()
        ass = _has_ass() if path else False
        reason = "" if path and ass else (FONT_MISSING_TEXT if not path else ASS_MISSING_TEXT)
        _cache["burn"] = {"available": bool(path and ass), "path": path, "font": os.path.basename(path) if path else "",
                          "family": _font_family(path) if path else "", "reason": reason}
        log.info("vnew.edit.text_font available=%s font=%s ass=%s", bool(path and ass), os.path.basename(path) or "-", ass)
    return dict(_cache["burn"])


def view_info() -> dict:
    """edit.edit_view 의 textFx(화면이 굽기 가능·글꼴·한도를 안다)."""
    info = burn_info()
    return {
        "burnIn": {"available": info["available"], "font": info["family"] or info["font"], "reason": info["reason"]},
        "limits": {"maxCaptions": MAX_CAPTIONS, "maxTitles": MAX_TITLES, "captionTextMax": CAPTION_TEXT_MAX,
                   "titleTextMax": TITLE_TEXT_MAX, "minItemSeconds": MIN_ITEM_S},
        "dipSeconds": list(DIP_SECONDS), "edgeFadeSeconds": list(EDGE_FADE_SECONDS),
    }


def _run_ffmpeg(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """마무리 패스 ffmpeg(작업 폴더 안 상대 경로 — 필터 글에 경로 이스케이프가 필요 없다)."""
    from .. import stitcher  # noqa: PLC0415

    nice = stitcher._edit_nice()  # noqa: SLF001
    return subprocess.run(nice + [stitcher.ffmpeg_path(), *args], cwd=cwd, capture_output=True,
                          timeout=stitcher.STITCH_TIMEOUT_SECONDS, check=False)


# ================================================================ SRT · ASS


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, float(seconds)) * 1000))
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(captions: list[dict]) -> str:
    """SRT(번호 · 시각 --> 시각 · 글 · 빈 줄). 기울임 자막은 <i>…</i>. 틀린 자막은 버린다."""
    blocks = []
    for number, cap in enumerate(_lenient(clean_captions, captions), 1):
        text = cap["text"]
        if cap["style"].get("italic"):
            text = "\n".join(f"<i>{line}</i>" for line in text.split("\n"))
        blocks.append(f"{number}\n{_srt_time(cap['start'])} --> {_srt_time(cap['end'])}\n{text}\n")
    return "\n".join(blocks)


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, float(seconds)) * 100))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    value = hex_color.lstrip("#")
    return f"&H{alpha}{value[4:6]}{value[2:4]}{value[0:2]}".upper()


def _ass_escape(text: str) -> str:
    """ASS 글: 역슬래시·중괄호는 전각으로(덧붙임 태그가 되지 않게), 줄바꿈은 \\N."""
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")


def ass_text(captions: list[dict], titles: list[dict], width: int, height: int, family: str) -> str:
    """자막(층 0)·타이틀(층 1) ASS. 화면 크기 = PlayRes(픽셀). 모양 조합마다 스타일 하나. 타이틀 페이드 = \\fad."""
    styles: dict[tuple, str] = {}
    lines = []

    def style_name(key: tuple) -> str:
        if key not in styles:
            styles[key] = f"S{len(styles)}"
        return styles[key]

    margin_c = int(round(height * CAPTION_MARGIN))
    for cap in captions:
        st = cap["style"]
        key = ("c", st["size"], st["color"], st["back"], st["pos"], bool(st["italic"]))
        lines.append(f"Dialogue: 0,{_ass_time(cap['start'])},{_ass_time(cap['end'])},{style_name(key)},,0,0,0,,{_ass_escape(cap['text'])}")
    for title in titles:
        key = ("t", title["size"], title["color"], "box" if title["box"] else "outline", title["pos"], False)
        fade = f"{{\\fad({int(round(title['fadeIn'] * 1000))},{int(round(title['fadeOut'] * 1000))})}}" \
            if title["fadeIn"] or title["fadeOut"] else ""
        lines.append(f"Dialogue: 1,{_ass_time(title['start'])},{_ass_time(title['end'])},{style_name(key)},,0,0,0,,"
                     f"{fade}{_ass_escape(title['text'])}")
    rows = []
    for key, name in styles.items():
        kind, size, color, back, pos, italic = key
        ratio = CAPTION_SIZES[size] if kind == "c" else TITLE_SIZES[size]
        font_px = max(8, int(round(height * ratio)))
        align = (8 if pos == "top" else 2) if kind == "c" else ASS_ALIGN[pos]
        box = back == "box"
        border = 3 if box else 1
        outline = max(1, int(round(height * (0.008 if box else 0.004)))) + (0 if box else 1)
        edge = _ass_color("#000000", BOX_ALPHA if box else "00")
        margin_v = margin_c if kind == "c" else int(round(height * TITLE_MARGIN))
        margin_h = int(round(width * (0.05 if kind == "c" else TITLE_MARGIN)))
        rows.append(f"Style: {name},{family},{font_px},{_ass_color(color)},{_ass_color(color)},{edge},{edge},0,{-1 if italic else 0},"
                    f"0,0,100,100,0,0,{border},{outline},0,{align},{margin_h},{margin_h},{margin_v},1")
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {int(width)}", f"PlayResY: {int(height)}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *rows, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *lines, "",
    ])


# ================================================================ 마무리 패스(내보내기)


def finish_plan(clips: list[dict], frames: list[int], fx: dict | None, fps: int, textfx: dict | None) -> dict | None:
    """마무리 패스 배치(프레임). 클립 자리는 edit 내보내기와 같다(fx 가 있으면 fx start·dissolve, 없으면 frames 누적).
    colors [[첫 장, 끝 장, 색]](디졸브는 겹침 가운데에서 나눈다) · dips [[경계 장, 반 길이 장, black|white]] · fadeIn·fadeOut(장) ·
    captions · titles · total(장). 아무 효과도 없으면 None."""
    count = len(clips)
    textfx = textfx or {}
    if fx:
        starts, dissolve, total = list(fx["start"]), list(fx["dissolve"]), int(fx["total"])
    else:
        starts, position = [], 0
        for value in frames:
            starts.append(position)
            position += value
        dissolve, total = [0] * count, position
    colors, dips = [], []
    for index, clip in enumerate(clips):
        color = color_of(clip)
        if color:
            lo = starts[index] + ((dissolve[index - 1] + 1) // 2 if index else 0)
            hi = starts[index + 1] + dissolve[index] // 2 - 1 if index < count - 1 else total - 1
            if hi >= lo:
                colors.append([lo, hi, color])
        kind, seconds = dip_of(clip)
        if kind and index < count - 1 and not dissolve[index]:
            half = max(1, int(round(seconds * fps / 2)))
            half = min(half, frames[index] - 1, frames[index + 1] - 1)
            if half >= 1:
                dips.append([starts[index + 1], half, DIP_COLORS[kind]])
    fade_in = min(total, int(round(float(textfx.get("fadeIn") or 0) * fps)))
    fade_out = min(total - 1, int(round(float(textfx.get("fadeOut") or 0) * fps))) if total > 1 else 0
    plan = {"total": total, "fps": fps, "colors": colors, "dips": dips, "fadeIn": fade_in, "fadeOut": max(0, fade_out),
            "captions": list(textfx.get("captions") or []), "titles": list(textfx.get("titles") or [])}
    if not (colors or dips or plan["fadeIn"] or plan["fadeOut"] or plan["captions"] or plan["titles"]):
        return None
    return plan


def video_filter(plan: dict, ass_name: str = "subs.ass", fonts_dir: str = "fonts") -> str:
    """마무리 패스 -vf 글: 색(enable) → 블랙/화이트 전환(경계 앞 fade out · 경계 장부터 fade in, enable) → 시작/끝 fade → ass.
    fade out 은 n 장 뒤가 다 검으므로 enable 로 그 구간만 건다. 경계 장(B)은 fade in 첫 장이라 완전히 검다(희다)."""
    parts = []
    for lo, hi, color in plan["colors"]:
        for item in color_filters(color):
            parts.append(f"{item}:enable='between(n,{lo},{hi})'")
    for at, half, tone in plan["dips"]:
        parts.append(f"fade=t=out:s={at - half}:n={half}:c={tone}:enable='between(n,{at - half},{at - 1})'")
        parts.append(f"fade=t=in:s={at}:n={half}:c={tone}:enable='between(n,{at},{at + half - 1})'")
    if plan["fadeIn"]:
        parts.append(f"fade=t=in:s=0:n={plan['fadeIn']}:c=black")
    if plan["fadeOut"]:
        # 마지막 장(total − 1)이 완전히 검게: fade out 은 s + n 장부터 검다.
        parts.append(f"fade=t=out:s={max(0, plan['total'] - 1 - plan['fadeOut'])}:n={plan['fadeOut']}:c=black")
    if plan["captions"] or plan["titles"]:
        parts.append(f"ass=filename={ass_name}:fontsdir={fonts_dir}")
    return ",".join(parts)


def finish(current: dict, clips: list[dict], frames: list[int], fx: dict | None, fps: int, width: int, height: int,
           workdir: str, output: str) -> bool:
    """edit.run_export 가 이은 뒤 부른다. 효과가 있으면 output 을 마무리 패스 결과로 바꾸고 True, 없으면 아무것도 하지 않고 False
    (215b 전 문서는 명령이 그대로다). 실패하면 VpocError(내보내기 failed — 그 문구가 화면에 간다)."""
    plan = finish_plan(clips, frames, fx, fps, current.get("textFx") if isinstance(current.get("textFx"), dict) else None)
    if plan is None:
        return False
    where = "vnew.edit.export"
    if plan["captions"] or plan["titles"]:
        info = burn_info()
        if not info["available"]:
            raise VpocError(where, "CaptionFontMissing", info["reason"], 409)
        os.makedirs(os.path.join(workdir, "fonts"), exist_ok=True)
        shutil.copyfile(info["path"], os.path.join(workdir, "fonts", os.path.basename(info["path"])))
        with open(os.path.join(workdir, "subs.ass"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(ass_text(plan["captions"], plan["titles"], width, height, info["family"]))
    target = os.path.join(workdir, "edit-fx.mp4")
    args = ["-y", "-hide_banner", "-loglevel", "error", "-threads", "1", "-i", os.path.basename(output),
            "-map", "0:v:0", "-map", "0:a?", "-vf", video_filter(plan), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", os.path.basename(target)]
    from . import generate  # noqa: PLC0415 — 클립 인코딩과 같은 세마포어(ffmpeg 인코딩 두 개가 겹치지 않게)

    generate._PREPARE_SEM.acquire()  # noqa: SLF001
    try:
        result = _run_ffmpeg(args, workdir)
    except subprocess.TimeoutExpired:
        raise VpocError(where, "FinishFailed", "자막·전환·색 보정을 입히는 데 너무 오래 걸렸습니다.", 500) from None
    finally:
        generate._PREPARE_SEM.release()  # noqa: SLF001
    if result.returncode != 0 or not os.path.exists(target) or not os.path.getsize(target):
        log.warning("vnew.edit.finish_failed err=%s", (result.stderr or b"").decode("utf-8", "replace")[:300])
        raise VpocError(where, "FinishFailed", "자막·전환·색 보정을 입히지 못했습니다.", 500)
    os.replace(target, output)
    log.info("vnew.edit.finish colors=%d dips=%d fade_in=%d fade_out=%d captions=%d titles=%d frames=%d", len(plan["colors"]),
             len(plan["dips"]), plan["fadeIn"], plan["fadeOut"], len(plan["captions"]), len(plan["titles"]), plan["total"])
    return True


# ================================================================ 자막 자동 만들기


def timeline_starts(clips: list[dict]) -> list[float]:
    """클립 타임라인 시작(초) — 디졸브만큼 당겨진다(vnew.js editStarts · edit._total 과 같다)."""
    E = _e()
    starts, clock = [], 0.0
    for index, clip in enumerate(clips):
        starts.append(clock)
        clock += E._clip_seconds(clip)  # noqa: SLF001
        if index < len(clips) - 1:
            clock -= E._dissolve_of(clip)  # noqa: SLF001
    return starts


def map_span(start: float, end: float, pieces: list[dict], clips: list[dict]) -> tuple[float, float] | None:
    """⑥ 원본 시각 [start, end] → ⑦ 타임라인 [a, b]. 원본 조각(t0·t1·sourceKey·in0·rate)으로 재료 초를 구하고, 같은 재료를 쓰는
    클립이 그 재료 초를 덮는 곳을 타임라인으로 옮긴다. 조각이 여러 곳이면 MERGE_GAP_S 안은 잇고 가장 긴 덩어리. 못 찾으면 None."""
    E = _e()
    starts = timeline_starts(clips)
    parts = []
    for piece in pieces:
        t0, t1 = float(piece.get("t0") or 0), float(piece.get("t1") or 0)
        lo, hi = max(start, t0), min(end, t1)
        if hi - lo <= 1e-3:
            continue
        rate = float(piece.get("rate") or 1.0)
        m0 = float(piece.get("in0") or 0) + (lo - t0) * rate
        m1 = float(piece.get("in0") or 0) + (hi - t0) * rate
        for index, clip in enumerate(clips):
            if clip.get("sourceKey") != piece.get("sourceKey"):
                continue
            a, b = max(m0, float(clip["in"])), min(m1, float(clip["out"]))
            if b - a <= 1e-3:
                continue
            speed = E._speed_of(clip)  # noqa: SLF001
            parts.append((starts[index] + (a - float(clip["in"])) / speed, starts[index] + (b - float(clip["in"])) / speed))
    if not parts:
        return None
    parts.sort()
    groups = [list(parts[0])]
    for a, b in parts[1:]:
        if a - groups[-1][1] <= MERGE_GAP_S:
            groups[-1][1] = max(groups[-1][1], b)
        else:
            groups.append([a, b])
    best = max(groups, key=lambda g: g[1] - g[0])
    return _r3(best[0]), _r3(best[1])


def _cap_id(line_id: str, used: set) -> str:
    base = "cap-" + re.sub(r"[^A-Za-z0-9_-]", "", line_id)[:30]
    if len(base) <= 4:
        base = "cap-" + hashlib.sha256(line_id.encode("utf-8")).hexdigest()[:10]
    value, n = base, 2
    while value in used:
        value, n = f"{base[:36]}-{n}", n + 1
    used.add(value)
    return value


def build_captions(lines: list[dict], overrides: dict, pieces: list[dict], clips: list[dict]) -> tuple[list[dict], int]:
    """⑥ 줄(id·lineKo·voice·start·end) + 대사 고침(overrides: lineId → textKo·thought) → 자막 목록과 못 놓은 줄 수.
    고친 대사가 대본보다 먼저, 속마음 = 기울임. 앞 자막이 뒤 자막 시작을 넘으면 앞 끝을 줄인다(최소 MIN_ITEM_S)."""
    out, used, skipped = [], set(), 0
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("id") or "")
        ov = overrides.get(line_id) if isinstance(overrides.get(line_id), dict) else None
        text = _text((ov or {}).get("textKo") or line.get("lineKo"), CAPTION_TEXT_MAX)
        start, end = _num(line.get("start")), _num(line.get("end"))
        span = map_span(start, end, pieces, clips) if text and start is not None and end is not None and end > start else None
        if not span or span[1] - span[0] < MIN_ITEM_S - 1e-9:
            skipped += 1
            continue
        thought = bool(ov.get("thought")) if ov and isinstance(ov.get("thought"), bool) else line.get("voice") == "thought"
        cap = {"id": _cap_id(line_id, used), "start": span[0], "end": span[1], "text": text,
               "style": dict(CAPTION_STYLE, italic=thought)}
        if _ID_RE.match(line_id):
            cap["lineId"] = line_id
        out.append(cap)
    out.sort(key=lambda c: (c["start"], c["end"]))
    for before, after in zip(out, out[1:]):
        if before["end"] > after["start"] and after["start"] - before["start"] >= MIN_ITEM_S:
            before["end"] = after["start"]
    return out, skipped


def _loose_clips(raw) -> list[dict]:
    """자동 자막 요청의 클립(저장 전 화면 상태). 재료·시간 모양만 본다(틀린 클립은 버린다 — 저장 검사는 PUT 이 한다)."""
    E = _e()
    out = []
    for clip in (raw if isinstance(raw, list) else [])[:E.MAX_CLIPS]:
        if not isinstance(clip, dict) or not isinstance(clip.get("sourceKey"), str):
            continue
        a, b = _num(clip.get("in")), _num(clip.get("out"))
        if a is None or b is None or b <= a:
            continue
        item = {"sourceKey": clip["sourceKey"], "in": a, "out": b}
        if E._speed_of(clip) != 1:  # noqa: SLF001
            item["speed"] = E._speed_of(clip)  # noqa: SLF001
        if E._dissolve_of(clip):  # noqa: SLF001
            item["transition"] = {"type": "dissolve", "seconds": E._dissolve_of(clip)}  # noqa: SLF001
        out.append(item)
    return out


def auto_captions(owner: str, pid: str, payload) -> dict:
    """POST /p/<pid>/edit/captions/auto {variant, clips}. 저장하지 않는다(화면이 실행 취소 한 단계로 넣는다).
    반환 {captions, skipped, lines, source}. ⑥ 사운드 문서가 없으면 만든다(⑥ 을 연 것과 같다 — sound._ensure_doc)."""
    where = "vnew.edit.captions"
    payload = payload if isinstance(payload, dict) else {}
    E = _e()
    from . import line_redo  # noqa: PLC0415
    from . import sound  # noqa: PLC0415

    sound.require_enabled(where)
    variant = payload.get("variant")
    if variant not in E.VARIANTS:
        raise VpocError(where, "BadVariant", "그림체(variant)는 real 또는 webtoon 이어야 합니다.", 400)
    clips = _loose_clips(payload.get("clips"))
    if not clips:
        raise VpocError(where, "EmptyTimeline", "타임라인에 클립이 없습니다.", 409)
    plan, gen, gen_id = E._load_gen(owner, pid)  # noqa: SLF001
    doc, _rebuilt = sound._ensure_doc(owner, pid, gen_id, plan, gen, variant)  # noqa: SLF001
    base = sound._base_source(owner, pid, gen_id, gen, variant)  # noqa: SLF001
    duration = _num((doc.get("source") or {}).get("duration")) or 0.0
    pieces = line_redo._pieces(owner, pid, gen_id, plan, gen, variant, base, duration)  # noqa: SLF001
    if base.get("key") and duration > 0:
        pieces = pieces + [{"t0": 0.0, "t1": duration, "sourceKey": base["key"], "in0": 0.0, "rate": 1.0}]  # 합친 영상 재료
    overrides = line_redo.read_overrides(owner, pid, gen_id) if line_redo.enabled() else {}
    lines = [line for line in doc.get("lines") or [] if isinstance(line, dict)]
    captions, skipped = build_captions(lines, overrides, pieces, clips)
    log.info("vnew.edit.captions_auto pid=%s gen=%s variant=%s lines=%d captions=%d skipped=%d overrides=%d", pid, gen_id, variant,
             len(lines), len(captions), skipped, len(overrides))
    return {"captions": captions, "skipped": skipped, "lines": len(lines), "source": str((doc.get("source") or {}).get("kind") or "")}


def srt_view(payload) -> dict:
    """POST /p/<pid>/edit/captions/srt {captions, variant?} → {fileName, text}(저장하지 않는다 — 화면 상태 그대로)."""
    payload = payload if isinstance(payload, dict) else {}
    captions = clean_captions(payload.get("captions"), "vnew.edit.captions.srt")
    variant = payload.get("variant") if payload.get("variant") in ("real", "webtoon") else "edit"
    return {"fileName": f"captions-{variant}.srt", "text": srt_text(captions), "count": len(captions)}


# ================================================================ 라우트


def _body(where: str) -> dict:
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
    """routes.create_blueprint 가 한 줄로 부른다. VNEW_EDIT 이 꺼져 있으면 프로젝트를 보기 전에 404.
      POST /p/<pid>/edit/captions/auto  {variant, clips} → {captions, skipped, lines, source}  (쓰기 권한 — ⑥ 문서를 만들 수 있다)
      POST /p/<pid>/edit/captions/srt   {captions, variant?} → {fileName, text, count}          (읽기 권한)
    """
    from flask import jsonify  # noqa: PLC0415

    @bp.post("/p/<pid>/edit/captions/auto")
    @deps.require_auth
    def edit_captions_auto(pid: str):
        try:
            _e().require_enabled("vnew.edit.captions")
            owner = project_for(pid, write=True)
            return jsonify(auto_captions(owner, pid, _body("vnew.edit.captions")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.post("/p/<pid>/edit/captions/srt")
    @deps.require_auth
    def edit_captions_srt(pid: str):
        try:
            _e().require_enabled("vnew.edit.captions.srt")
            project_for(pid, write=False)
            return jsonify(srt_view(_body("vnew.edit.captions.srt")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
