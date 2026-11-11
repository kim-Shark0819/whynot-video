"""작업 226 검사 — 실외 장소(design.enclosed = false)는 상자 방이 아니라 열린 바깥으로 그린다.
네트워크 0 · LLM 0 · 유료 0(locations._author · _charge_text 를 가짜로 바꾼다).

  (od-a) 실외 고정물(편의점 앞마당 · 볼라드 · 아파트 · 가게 · 낮 lightEn · enclosed False): Astra 에 주는 글(system · user_text) ·
         Astra 결과 마무리 · 템플릿 대체 · 실사 편집 문장에 방 · 상자 · 천장 · 구조물 벽 · 실내 낱말이 없고,
         open-air · sky · 면 이름(MAIN BACKGROUND · LEFT SIDE · RIGHT SIDE · SKY) · 사실 물건(bollard · apartment)이 있다
  (od-b) 같은 고정물 enclosed True(+ enclosed 키가 없는 설계)는 226 전 코드와 바이트 그대로(sha256 상수)
  (od-c) 낮 실외 + 사실에 빗금 패널의 "night sky" → 최종 글에 밤 · 저녁 · 어두운 하늘 낱말 없음. lightEn 이 진짜 밤이면 night 유지
  (od-d) 면별 글(벽 텍스처가 잘려 나오는 면 단위 글)이 실외면 열린 경치, 벽 표면 아님. 실내 면별 글은 그대로(sha256)
  (od-e) _FACTS_SYSTEM 에 스크린톤 규칙 1문장, 그 문장 말고는 226 전과 같다(sha256)
  (od-f) 변이 "실외도 방 낱말"을 (od-a) 가 잡는다
  (od-g) 바꾼 파일 LF 줄 끝
실행: python -X utf8 -B tools/check_outdoor_location_226.py
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_outdoor_location_226").check_outdoor_location_226(): '
        'fail("작업 226: 실외 장소(enclosed false) 열린 바깥 지시문 검사 실패")')
GROUPS = {
    "od-a": "실외 지시문(Astra 글 · 마무리 · 템플릿 · 실사 편집)에 방/상자/천장/벽/실내 낱말 없음 · open-air · sky · 면 이름 · 물건",
    "od-b": "실내(enclosed True · 키 없음) 지시문 226 전과 바이트 그대로(sha256)",
    "od-c": "낮 실외: 빗금 'night sky' 사실 → 밤 낱말 없음 · 진짜 밤 lightEn 은 night 유지",
    "od-d": "실외 면별 글 = 열린 경치(벽 표면 아님) · 실내 면별 글 그대로(sha256)",
    "od-e": "_FACTS_SYSTEM 스크린톤 규칙 1문장 · 나머지 그대로(sha256)",
    "od-f": "변이(실외도 방 낱말)를 (od-a) 가 잡는다",
    "od-g": "바꾼 파일 LF",
}
CHANGED = ["app/src/vnew/locations.py", "tools/check_outdoor_location_226.py"]

# 226 전 코드(2026-11-12)로 잰 값.
INDOOR_SHA256 = "c3c61dda08436b391c89204a71a75ebca55b1cbe742dfa8ef4be150b2758d048"
INDOOR_SIDES_SHA256 = "81273253957e4e87c164d46f78f4a87d2e0a543f829cd447cb6cd3ccb25984ad"
FACTS_SYSTEM_OLD_SHA256 = "f3d9e45843c9d4ccd0cda26aab78a289e278b0e1ef91405b2c5adff7b334f08e"
SCREENTONE_RULE = (
    "Dark hatching, screentone, sparkle or gradient backgrounds behind a character in an emotional close-up "
    "are mood effects, not the time of day or the place. Take time of day and sky from the wide or establishing "
    "panels. If any panel shows a bright sky, it is daytime."
)

DAY_LIGHT = "Bright midday daylight from a clear blue sky, sun from the upper left, short shadows fall to the right."
NIGHT_LIGHT = "Night: the cool white glow of the store front and orange street lamps under a black night sky."
NIGHT_WORDS_RE = re.compile(
    r"\b(?:night\w*|evening\w*|dusk|twilight|moon\w*|starlit|starry|dark\w*)\b", re.IGNORECASE)


def _obj(kind, count, color, material, position, size, attached="floor") -> dict:
    return {"kind": kind, "count": count, "colorEn": color, "materialEn": material,
            "position": position, "sizeEn": size, "attachedTo": attached}


def _loc(enclosed=False, *, light: str = DAY_LIGHT, hatched: bool = False, drop_enclosed: bool = False) -> dict:
    """prod 꼴 고정물: 편의점 앞마당. north(뒤) = 볼라드 · 도로 · 뒤 흰 아파트, east = 도로 + 아파트, west = 볼라드 · 골목,
    south(앞) = 편의점 유리 앞면. 벽은 손으로 고친 그대로 'no wall: open air …'."""
    design = {
        "enclosed": enclosed, "widthM": 10, "depthM": 8, "heightM": 4,
        "floorEn": "light grey asphalt with a low concrete kerb",
        "ceilingEn": "no ceiling: open blue sky",
        "lightEn": light,
        "walls": {
            "north": {"descEn": "No wall: open air; low black bollards, a road and white apartment blocks behind",
                      "featuresEn": "three low black U-shaped steel bollards with yellow bands, two-lane road, white apartment blocks",
                      "hasWindow": False, "hasDoor": False},
            "east": {"descEn": "No wall: open air; the road continues past white apartment blocks",
                     "featuresEn": "asphalt road, concrete kerb, white apartment blocks, green street lamp",
                     "hasWindow": False, "hasDoor": False},
            "south": {"descEn": "No wall: open air; the glass front of a convenience store",
                      "featuresEn": "glass storefront, automatic sliding glass door, snack shelves inside",
                      "hasWindow": True, "hasDoor": True},
            "west": {"descEn": "No wall: open air; more bollards along a side street",
                     "featuresEn": "two low black U-shaped steel bollards with yellow bands, side street, parked white car",
                     "hasWindow": False, "hasDoor": False},
        },
        "propsEn": [{"labelEn": "low black U-shaped steel bollard with yellow bands", "wall": "center", "sizeM": 0.6}],
    }
    if drop_enclosed:
        design.pop("enclosed")
    facts = {
        "walls": {
            "north": {"wallColorEn": "", "wallMaterialEn": "no wall: open air", "objects": [
                _obj("U-shaped steel bollard with yellow bands", 3, "black", "steel", "center low", "about 0.6 m high"),
                _obj("two-lane asphalt road", 1, "dark grey", "asphalt", "center low", "full width"),
                _obj("apartment block", 3, "white", "concrete", "center high", "about 15 storeys")]},
            "east": {"wallColorEn": "", "wallMaterialEn": "no wall: open air", "objects": [
                _obj("apartment block", 2, "white", "concrete", "right high", "about 15 storeys"),
                _obj("street lamp", 1, "green", "metal", "left mid", "about 6 m high")]},
            "south": {"wallColorEn": "white and blue", "wallMaterialEn": "glass storefront of a convenience store",
                      "objects": [_obj("snack shelf", 4, "white", "metal", "center mid", "about 1.8 m high")]},
            "west": {"wallColorEn": "", "wallMaterialEn": "no wall: open air", "estimated": True, "objects": [
                _obj("U-shaped steel bollard with yellow bands", 2, "black", "steel", "left low", "about 0.6 m high"),
                _obj("compact car", 1, "white", "painted metal", "right low", "about 4 m long")]},
        },
        "floorEn": "light grey asphalt with a low concrete kerb",
        "ceilingEn": "open blue sky",
        "lightEn": light,
        "windows": [{"wall": "south", "position": "center mid", "sizeEn": "full width", "frameEn": "aluminium",
                     "outsideEn": "rows of snack shelves"}],
        "doors": [{"wall": "south", "position": "right low", "sizeEn": "about 2 m high", "materialEn": "glass",
                   "colorEn": "clear"}],
        "eraEn": "2020s", "countryEn": "South Korea",
    }
    if hatched:
        # 감정 클로즈업 패널의 어두운 빗금 스크린톤을 사실 LLM 이 '밤하늘'로 읽은 꼴(prod).
        facts["ceilingEn"] = "dark night sky with cross-hatching"
        facts["walls"]["north"]["wallColorEn"] = "dark cross-hatched night sky behind the apartments"
        facts["walls"]["east"]["objects"].append(_obj("night sky with sparkles", 1, "dark blue", "screentone",
                                                      "center high", "full width"))
    return {"id": "loc_od226", "key": "convenience store forecourt", "nameEn": "convenience store forecourt",
            "design": design, "facts": facts}


@contextmanager
def _fake_author(lm, *, fail: bool = False, reply: str = "Wide view of the forecourt with the bollards."):
    calls: list[dict] = []

    def author(system_text, user_text, *, model):
        calls.append({"system": system_text, "user": user_text, "model": model})
        if fail:
            raise RuntimeError("fake author down")
        return {"promptEn": reply}, {"model": model, "inputTokens": 1, "outputTokens": 1}

    saved = (lm._author, lm._charge_text)  # noqa: SLF001
    lm._author = author  # noqa: SLF001
    lm._charge_text = lambda *a, **k: None  # noqa: SLF001
    try:
        yield calls
    finally:
        lm._author, lm._charge_text = saved  # noqa: SLF001


def _view_texts(lm, loc: dict) -> dict[str, str]:
    """(variant, view) 마다 Astra 글 · Astra 결과 마무리 · 템플릿 대체 + 실사 편집 문장."""
    out: dict[str, str] = {}
    for variant in lm.VARIANTS:
        for view in lm.VIEWS:
            with _fake_author(lm) as calls:
                out[f"{variant}/{view}/author"] = lm._author_prompt("o", "p", copy.deepcopy(loc), variant=variant, view=view)  # noqa: SLF001
            out[f"{variant}/{view}/system"] = calls[0]["system"]
            out[f"{variant}/{view}/user"] = calls[0]["user"]
            with _fake_author(lm, fail=True):
                out[f"{variant}/{view}/fallback"] = lm._author_prompt("o", "p", copy.deepcopy(loc), variant=variant, view=view)  # noqa: SLF001
            out[f"{variant}/{view}/prompt_for"] = lm._prompt_for(copy.deepcopy(loc), variant, view)  # noqa: SLF001
    return out


def _indoor_blob(lm) -> str:
    parts: list[str] = []
    no_design = _loc(True)
    no_design.pop("design")  # 설계 없음 = 실내(지금과 같다)
    for loc in (_loc(True), _loc(True, hatched=True), _loc(False, drop_enclosed=True), no_design):
        texts = _view_texts(lm, loc)
        parts += [f"{k}={texts[k]}" for k in sorted(texts)]
        parts.append(lm._room_anchor(loc))  # noqa: SLF001
        parts += [lm._layout_clause(loc, view) for view in lm.VIEWS]  # noqa: SLF001
        parts.append(lm._place_edit_prompt(lm._place_text(loc)))  # noqa: SLF001
    return "\n".join(parts)


def _indoor_sides_blob(lm) -> str:
    parts: list[str] = []
    for loc in (_loc(True), _loc(False, drop_enclosed=True)):
        parts += [lm._wall_facts_text(side, loc) for side in lm.SIDES]  # noqa: SLF001
        parts += [lm._facts_context(side, loc) for side in lm.SIDES]  # noqa: SLF001
        for view in lm.VIEWS:
            faces = lm._view_face_facts(loc, view)  # noqa: SLF001
            parts += [f"{view}.{k}={faces[k]}" for k in sorted(faces)]
    return "\n".join(parts)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _modules():
    from src.vnew import locations as lm  # noqa: PLC0415
    return lm


# 구조물 낱말(방 · 상자 · 천장 · 벽 · 실내). 꼭 넣는 부정 문장 OUTDOOR_OPEN_AIR("no walls, no ceiling, no room, no box")는 빼고 센다.
FORBIDDEN_RE = re.compile(r"\b(?:rooms?|box(?:es)?|ceilings?|indoors?|walls?)\b", re.IGNORECASE)
SIDE_LABELS = ("MAIN BACKGROUND", "LEFT SIDE", "RIGHT SIDE", "SKY")


def _forbidden(lm, text: str) -> list[str]:
    body = text.replace(lm.OUTDOOR_OPEN_AIR, " ")
    return sorted({m.group(0).lower() for m in FORBIDDEN_RE.finditer(body)})


def group_a() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    loc = _loc(False)
    texts = _view_texts(lm, loc)
    texts["edit"] = lm._edit_prompt_for(copy.deepcopy(loc))  # noqa: SLF001
    for key, text in texts.items():
        words = _forbidden(lm, text)
        if words:
            bad.append(f"{key}: 구조물 낱말 {words}")
        need = ["open-air", "sky"]
        if not key.endswith("system"):
            need += ["bollard", "apartment"]
        if not key.endswith(("system", "edit")):
            need += list(SIDE_LABELS)
        if key.endswith(("author", "fallback", "prompt_for")):
            need += ["No people", "continuous" if "/back/" in key else "opposite side", lm.OUTDOOR_OPEN_AIR]
        missing = [w for w in need if (w.lower() not in text.lower() if w.islower() else w not in text)]
        if missing:
            bad.append(f"{key}: 없음 {missing}")
    if lm.ASTRA_PROMPT_INSTRUCTION in texts["real/back/system"]:
        bad.append("실외 Astra system 이 실내 지침(ASTRA_PROMPT_INSTRUCTION)")
    return bad


def group_b() -> list[str]:
    lm = _modules()
    got = _sha(_indoor_blob(lm))
    return [] if got == INDOOR_SHA256 else [f"실내 지시문 sha256 {got} != {INDOOR_SHA256}"]


def group_c() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    day = _loc(False, hatched=True)
    if "night sky" not in str(day["facts"]):
        bad.append("고정물: 사실에 'night sky' 가 없다")
    reply = "Wide view of the forecourt under a dark night sky in the evening, moonlight on the bollards."
    for variant in lm.VARIANTS:
        for view in lm.VIEWS:
            with _fake_author(lm, reply=reply) as calls:
                author = lm._author_prompt("o", "p", copy.deepcopy(day), variant=variant, view=view)  # noqa: SLF001
            for name, text in (("user", calls[0]["user"]), ("author", author),
                               ("fallback", lm._prompt_for(copy.deepcopy(day), variant, view))):  # noqa: SLF001
                hits = sorted({m.group(0).lower() for m in NIGHT_WORDS_RE.finditer(text)})
                if hits:
                    bad.append(f"낮 {variant}/{view}/{name}: 밤 낱말 {hits}")
    hits = NIGHT_WORDS_RE.findall(lm._edit_prompt_for(copy.deepcopy(day)))  # noqa: SLF001
    if hits:
        bad.append(f"낮 실사 편집 문장: 밤 낱말 {hits}")
    night = _loc(False, light=NIGHT_LIGHT)
    for variant in lm.VARIANTS:
        for view in lm.VIEWS:
            text = lm._prompt_for(copy.deepcopy(night), variant, view)  # noqa: SLF001
            if "night" not in text.lower():
                bad.append(f"밤 lightEn {variant}/{view}: night 가 사라졌다")
    return bad


def group_d() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    loc = _loc(False)
    for side in lm.SIDES:
        text = lm._outdoor_side_text(side, loc)  # noqa: SLF001
        if not text.startswith(f"Open view toward the {side}"):
            bad.append(f"{side}: 'Open view toward the {side}' 로 시작하지 않음: {text[:80]!r}")
        if _forbidden(lm, text) or "surface" in text.lower():
            bad.append(f"{side}: 벽 표면 낱말 {_forbidden(lm, text)}: {text[:120]!r}")
        if lm._wall_facts_text(side, loc)[:40] and "open air" not in text.lower():  # noqa: SLF001
            bad.append(f"{side}: 'open air' 없음")
    for view in lm.VIEWS:
        faces = lm._view_face_facts(loc, view)  # noqa: SLF001
        for face in ("back", "left", "right"):
            if not faces[face].startswith("Open view toward the "):
                bad.append(f"{view}.{face}: 실외 면 글이 아님: {faces[face][:60]!r}")
        if _forbidden(lm, faces["ceiling"]) or "sky" not in faces["ceiling"].lower():
            bad.append(f"{view}.ceiling: 하늘 글이 아님: {faces['ceiling']!r}")
    if "Wall surface" not in lm._facts_context("south", loc):  # noqa: SLF001
        bad.append("검증용 사실 글(_facts_context 기본값)이 바뀌었다")
    got = _sha(_indoor_sides_blob(lm))
    if got != INDOOR_SIDES_SHA256:
        bad.append(f"실내 면별 글 sha256 {got} != {INDOOR_SIDES_SHA256}")
    return bad


def group_e() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    text = lm._FACTS_SYSTEM  # noqa: SLF001
    if text.count(SCREENTONE_RULE) != 1:
        bad.append(f"_FACTS_SYSTEM 에 스크린톤 규칙 {text.count(SCREENTONE_RULE)}번(1번이어야)")
    rest = _sha(text.replace(SCREENTONE_RULE + " ", "", 1))
    if rest != FACTS_SYSTEM_OLD_SHA256:
        bad.append(f"_FACTS_SYSTEM 규칙 말고도 바뀜: {rest}")
    if lm._DESIGN_SYSTEM.count(SCREENTONE_RULE) != 1:  # noqa: SLF001
        bad.append("_DESIGN_SYSTEM(lightEn 을 읽는다)에 스크린톤 규칙 1번이 아님")
    return bad


def group_f(caught: list[str]) -> list[str]:
    lm = _modules()
    saved = lm._is_outdoor  # noqa: SLF001
    lm._is_outdoor = lambda loc: False  # noqa: SLF001 — 변이: 실외도 방 낱말
    try:
        got = group_a()
    finally:
        lm._is_outdoor = saved  # noqa: SLF001
    if got:
        caught.append("room-wording→od-a")
        return []
    return ["변이(실외도 방 낱말)를 (od-a) 가 잡지 못했습니다"]


def check_outdoor_location_226() -> bool:
    import logging  # noqa: PLC0415
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    logging.disable(logging.WARNING)  # 가짜 작성 모델 실패 경고 줄
    try:
        for group, fn in (("od-a", group_a), ("od-b", group_b), ("od-c", group_c), ("od-d", group_d),
                          ("od-e", group_e), ("od-f", lambda: group_f(caught))):
            try:
                results[group] = fn()
            except Exception:  # noqa: BLE001
                results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    finally:
        logging.disable(logging.NOTSET)
    results["od-g"] = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 226 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "od-f" else ""
            print(f"[ok  ] 작업 226 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    _lm = _modules()
    print("INDOOR_SHA256", _sha(_indoor_blob(_lm)))
    print("INDOOR_SIDES_SHA256", _sha(_indoor_sides_blob(_lm)))
    print("FACTS_SYSTEM_OLD_SHA256", _sha(_lm._FACTS_SYSTEM.replace(SCREENTONE_RULE + " ", "", 1)))  # noqa: SLF001
    sys.exit(0)

if __name__ == "__main__":
    _ok = check_outdoor_location_226()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
