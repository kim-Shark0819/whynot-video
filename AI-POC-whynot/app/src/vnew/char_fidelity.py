"""캐릭터 충실도(char-fidelity, 2026-10-30) — 한쪽 소품 좌우·기준 사진·참고 순서·좌우 문장의 순수 함수.

네트워크·S3·LLM·설정을 import 하지 않는다(fx.py 와 같은 방식). characters.py 가 부른다.

좌우 규칙(design §5):
- 분석은 소품이 그 그림의 '화면' 어느 쪽에 보이는지(imageSide)와 인물이 보는 사람을 향하는지(facing)만 적는다.
- 캐릭터 쪽은 코드가 정한다: toward → 화면 쪽의 반대, away → 같은 쪽, unclear → 버림. 소품마다 다수결, 동률이면 버림.
- 정한 소품은 기본 복장 글의 좌우 표현을 지우고 "Worn on one side only: … on the RIGHT wrist only." 꼬리로 다시 쓴다.
"""

from __future__ import annotations

import re

# partEn → (단수 낱말, 복수 낱말, 한국어, 지우기 정규식에 쓰는 낱말들). 분석 도구 partEn enum 은 이 키 목록이다.
PARTS = {
    "wrist": ("wrist", "wrists", "손목", ("wrist",)),
    "hand": ("hand", "hands", "손", ("hand", "finger", "ring finger")),
    "arm": ("arm", "arms", "팔", ("arm", "forearm", "upper arm")),
    "shoulder": ("shoulder", "shoulders", "어깨", ("shoulder",)),
    "ear": ("ear", "ears", "귀", ("ear", "earlobe")),
    "eye": ("eye", "eyes", "눈", ("eye",)),
    "cheek": ("cheek", "cheeks", "볼", ("cheek",)),
    "chest": ("side of the chest", "sides of the chest", "가슴", ("chest", "breast")),
    "hip": ("hip", "hips", "허리 옆", ("hip", "waist")),
    "leg": ("leg", "legs", "다리", ("leg", "thigh", "knee")),
    "ankle": ("ankle", "ankles", "발목", ("ankle",)),
    "foot": ("foot", "feet", "발", ("foot", "feet", "shoe")),
    "hair": ("side", "sides", "머리", ("hair", "head", "parting")),
}
SIDES = ("left", "right")
VIEWS = ("front", "back", "left", "right", "sheet")
FACINGS = ("toward", "away", "unclear")
BACK_MAY_HIDE = frozenset({"eye", "cheek"})  # 후면에서 안 보여도 맞다고 보는 부위(결정 S7)
MAX_ITEMS, MAX_SIGHTINGS = 6, 8
SHEET_VIEWS = ("front", "three-quarter front", "side", "three-quarter back", "back")
SHEET_FACE_LEFT = (1, 2, 3)  # 0부터: 2·3·4번째 인물은 얼굴이 화면 왼쪽

# 제어 문자·줄바꿈·보이지 않는 방향 표시(모델 글이 프롬프트에 새 단락이나 뒤집힌 글을 만들지 못하게 한 칸으로 바꾼다).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
_SPACES = re.compile(r"\s+")
_TAIL_MARK = "Worn on one side only:"
# 이미 붙인 꼬리는 늘 글 끝에 있다. 끝까지 떼어야 소품 이름에 마침표가 있어도 두 번 적용한 결과가 같다(멱등).
_TAIL_RE = re.compile(r"\s*Worn on one side only:.*\Z", re.IGNORECASE | re.DOTALL)
_SIDE_WORD = r"(?:left|right)"
_ALL_PART_WORDS = sorted({w for value in PARTS.values() for w in value[3]}, key=len, reverse=True)


def clean_line(text, limit: int) -> str:
    """모델이 쓴 글을 한 줄로: 제어 문자·줄바꿈 → 한 칸, 공백 줄임, 앞뒤 공백 제거, 길이(단어 경계). 글이 아니면 ""."""
    if not isinstance(text, str):
        return ""
    return clip_words(_one_line(text), limit)


def _one_line(text: str) -> str:
    """제어 문자·줄바꿈 → 한 칸, 연속 공백 → 한 칸, 앞뒤 공백 제거(길이 제한 없음).

    좌우 정규식(\\s*,?\\s* 등)은 긴 공백 줄에서 되짚기가 폭발한다(공백 2,000칸에 28초 실측). 정규식 전에 늘 이 함수로 줄인다.
    """
    return _SPACES.sub(" ", _CONTROL.sub(" ", text or "")).strip()


def clip_words(text: str, limit: int) -> str:
    """limit 자 안으로 단어 경계에서 자른다(마침표를 붙이지 않는다). 한 낱말이 limit 보다 길면 그 자리에서 자른다."""
    text = text or ""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace() and " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


def pick_index(value, n: int) -> int | None:
    """그림 번호(0부터). 정수(bool 제외)나 숫자 글자이고 0 ≤ i < n 이면 그 값, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        index = value
    elif isinstance(value, str) and re.fullmatch(r"\s*\d{1,4}\s*", value):
        index = int(value)
    else:
        return None
    return index if 0 <= index < n else None


def _enum(value, allowed) -> str:
    key = value.strip().lower() if isinstance(value, str) else ""
    return key if key in allowed else ""


def _tidy(text: str) -> str:
    """좌우 낱말을 지운 뒤 남는 빈칸·쉼표를 정리한다."""
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = _SPACES.sub(" ", text).strip()
    return text.strip(" ,;:")


def _words_re(words) -> str:
    """낱말 묶음 정규식(긴 것 먼저, 낱말 사이 빈칸은 \\s+)."""
    escaped = [re.escape(w).replace(r"\ ", r"\s+") for w in sorted(set(words), key=len, reverse=True)]
    return "(?:" + "|".join(escaped) + ")"


_ITEM_PHRASE = re.compile(
    r"\s*,?\s*\b(?:worn\s+)?(?:on|at|around|in|over|across)\s+(?:the|his|her|their|its)\s+" + _SIDE_WORD
    + r"(?:[\s-]+hand(?:ed)?)?(?:\s+side(?:\s+of\s+(?:the|his|her|their)\s+" + _words_re(_ALL_PART_WORDS) + r"s?)?|\s+"
    + _words_re(_ALL_PART_WORDS) + r"s?)?\b",
    re.IGNORECASE,
)
_ITEM_WORD = re.compile(r"\b" + _SIDE_WORD + r"(?:-hand(?:ed)?)?\b[\s-]*", re.IGNORECASE)


def strip_item_side_words(text: str) -> str:
    """itemEn 의 left/right(-hand) 낱말과 "on the left wrist" 같은 구를 지운다(소품 이름에는 좌우가 없어야 한다)."""
    text = _ITEM_PHRASE.sub("", _one_line(text))
    return _tidy(_ITEM_WORD.sub("", text))


def character_side(image_side: str, facing: str) -> str:
    """본 것 하나 → 캐릭터 쪽(design §5.2). toward → 화면 쪽의 반대, away → 같은 쪽, unclear·모름 → ""."""
    if image_side not in SIDES:
        return ""
    if facing == "toward":
        return "right" if image_side == "left" else "left"
    if facing == "away":
        return image_side
    return ""


def resolve_sided_items(raw, n_images: int) -> list[dict]:
    """분석 sidedItemsEn → 캐릭터 쪽을 정한 소품 목록(design §5.3). 동률(0:0 포함)·틀린 값은 버린다."""
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_en = strip_item_side_words(clean_line(entry.get("itemEn"), 80))
        part = _enum(entry.get("partEn"), PARTS)
        if not item_en or not part:
            continue
        name = item_en.lower()
        if name in names:
            continue
        names.add(name)
        votes = {"left": 0, "right": 0}
        seen_in: set[int] = set()
        sightings = entry.get("sightings") if isinstance(entry.get("sightings"), list) else []
        for sighting in sightings[:MAX_SIGHTINGS]:
            if not isinstance(sighting, dict):
                continue
            index = pick_index(sighting.get("sourceIndex"), n_images)
            image_side = _enum(sighting.get("imageSide"), SIDES)
            facing = _enum(sighting.get("facing"), FACINGS)
            if index is None or not image_side or not facing:
                continue
            seen_in.add(index)
            side = character_side(image_side, facing)
            if side:
                votes[side] += 1
        if votes["left"] == votes["right"]:
            continue
        items.append({
            "itemEn": item_en,
            "labelKo": clean_line(entry.get("labelKo"), 12),
            "partEn": part,
            "side": "left" if votes["left"] > votes["right"] else "right",
            "votes": votes,
            "seenIn": sorted(seen_in),
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


def strip_side_words(text: str, parts: set[str]) -> str:
    """정한 소품의 부위에 붙은 좌우 표현만 지운다(design §5.4 표). 다른 부위의 좌우 낱말은 그대로 둔다.

    먼저 한 줄로 줄인다(_one_line — 긴 공백 줄 정규식 되짚기 방지, 결과의 줄바꿈 제거).
    """
    text = _one_line(text)
    words = [w for part in sorted(parts or ()) if part in PARTS for w in PARTS[part][3]]
    if not words or not text:
        return text
    word = _words_re(words)
    if "hair" in parts:
        # 가르마는 "한쪽"이라는 뜻을 남긴다(규칙 4·5). 규칙 2 가 "parting" 만 남기기 전에 먼저 바꾼다.
        text = re.sub(r"\bparted\s+(?:on|to)\s+the\s+" + _SIDE_WORD + r"\b", "parted to one side", text, flags=re.I)
        text = re.sub(r"\b" + _SIDE_WORD + r"[\s-]+part(?:ing|ed)?\b", "side parting", text, flags=re.I)
    text = re.sub(
        r"\s*,?\s*\b(?:worn\s+)?(?:on|at|around|in|over|across)\s+(?:the|his|her|their|its)\s+" + _SIDE_WORD
        + r"(?:[\s-]+hand)?\s+(?:side\s+of\s+(?:the|his|her|their)\s+)?" + word + r"s?\b",
        "", text, flags=re.I,
    )
    text = re.sub(r"\b" + _SIDE_WORD + r"(?:-hand)?[\s-]+(" + word + r"s?)\b", r"\1", text, flags=re.I)
    text = re.sub(r"\b(" + word + r"s?)\s*\(\s*" + _SIDE_WORD + r"\s*\)", r"\1", text, flags=re.I)
    return _tidy(text)


def apply_sides(text: str, items: list[dict]) -> str:
    """기본 복장 글의 좌우를 계산한 좌우로 다시 쓴다(design §5.4). 이미 붙인 꼬리는 먼저 뗀다(멱등).

    본문은 한 줄로 줄인 뒤 다룬다(_one_line — 모델 글의 줄바꿈 제거·긴 공백 줄 정규식 되짚기 방지).
    """
    body = _TAIL_RE.sub("", _one_line(text))
    if not items:
        return body.strip()
    body = strip_side_words(body, {item["partEn"] for item in items}).rstrip().rstrip(".").rstrip()
    pieces = [f"{item['itemEn']} on the {item['side'].upper()} {PARTS[item['partEn']][0]} only" for item in items]
    tail = _TAIL_MARK + " " + "; ".join(pieces) + "."
    return f"{body}. {tail}" if body else tail


def photo_layers(raw, file_ids: list[str], items: list[dict], *, limit: int = 12) -> list[dict]:
    """분석 outfitLayersEn(다른 그림의 옷차림 한 벌씩) 정리(design §4.5 5). promptEn 이 비면 버린다. 최대 limit 개.

    각 항목: labelKo·promptEn(그 그림에서 본 소품으로 apply_sides)·sourceIndex·sourceFileId·whereEn·sided(items 순번).
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"promptEn": entry}
        if not isinstance(entry, dict):
            continue
        prompt_en = clean_line(entry.get("promptEn"), 400)
        if not prompt_en:
            continue
        index = pick_index(entry.get("sourceIndex"), len(file_ids))
        sided = [k for k, item in enumerate(items) if index is not None and index in (item.get("seenIn") or [])]
        if sided:
            prompt_en = apply_sides(prompt_en, [items[k] for k in sided])
        out.append({
            "labelKo": clean_line(entry.get("labelKo"), 60) or clip_words(prompt_en, 60),
            "promptEn": prompt_en,
            "sourceIndex": index,
            "sourceFileId": file_ids[index] if index is not None else "",
            "whereEn": clean_line(entry.get("whereEn"), 60),
            "sided": sided,
        })
        if len(out) >= limit:
            break
    return out


def reference_order(file_ids: list[str], base_id: str, limit: int) -> tuple[list[str], bool]:
    """Seedream 참고 그림 순서(design §8): 기준 사진이 목록에 있으면 맨 앞 + 나머지 업로드 순, 아니면 업로드 순."""
    ids = list(file_ids or [])
    limit = max(0, int(limit))
    if base_id and base_id in ids and limit > 0:
        return ([base_id] + [fid for fid in ids if fid != base_id])[:limit], True
    return ids[:limit], False


def pronoun(gender: str) -> str:
    """male → his, female → her, 그 밖 → their."""
    return {"male": "his", "female": "her"}.get(gender, "their")


def side_sentence(item: dict, view: str, pron: str) -> str:
    """그림 하나에 넣는 소품 좌우 문장(design §5.5 원문). 모양이 틀리거나 모르는 보기면 ""."""
    if not isinstance(item, dict) or item.get("side") not in SIDES or item.get("partEn") not in PARTS:
        return ""
    name = item.get("itemEn") or ""
    if not name:
        return ""
    side = item["side"]
    other = "right" if side == "left" else "left"
    s_up, o_up = side.upper(), other.upper()
    part, parts = PARTS[item["partEn"]][0], PARTS[item["partEn"]][1]
    if view == "front":
        return (f"The {name} is on {pron} {s_up} {part} only — in this front view it appears on the {o_up} side of the "
                f"image; {pron} {o_up} {part} has nothing like it; never on both {parts}.")
    if view == "back":
        return (f"The {name} is on {pron} {s_up} {part} only — in this back view it appears on the {s_up} side of the "
                f"image; {pron} {o_up} {part} has nothing like it; never on both {parts}.")
    if view in ("left", "right"):
        if side == view:
            return (f"The {name} is on {pron} {s_up} {part}, the {part} nearest the viewer in this {view} side view — "
                    f"show it clearly there; the far {part} has nothing like it; never on both {parts}.")
        return (f"The {name} is on {pron} {s_up} {part}, the far {part} in this {view} side view — it is hidden behind "
                f"the body or barely visible; the near {part} closest to the viewer has nothing like it; never on both {parts}.")
    if view == "sheet":
        return (f"The {name} is on {pron} {s_up} {part} only — in the front figure it appears on the {o_up} side of the "
                f"image and in the back figure on the {s_up} side of the image; never on both {parts}.")
    return ""


def side_sentences(items: list[dict], view: str, pron: str) -> str:
    """소품 문장을 한 칸으로 잇는다. 소품이 없으면 ""."""
    return " ".join(s for s in (side_sentence(item, view, pron) for item in items or []) if s)


# ---------------------------------------------------------------- 그림 검사 기대값·반전·고칠 문장(char-fidelity T2, design §5.6~§5.8·§7)
#
# 검사 모델은 그림의 '화면' 쪽(정면·후면) 또는 가까운 쪽·먼 쪽(옆모습)만 적는다. 맞는지 판단·반전·고칠 문장은 아래 순수 함수가 정한다.

SEEN_FLAT = ("left", "right", "both", "none")  # 정면·후면 검사의 보이는 쪽(화면 기준)
SEEN_SIDE = ("near", "far", "both", "none")  # 옆모습 검사의 보이는 쪽(가까운 쪽·먼 쪽)
VIEW_FACINGS = ("front", "back", "left", "right", "other")
SHEET_VIEW_VALUES = SHEET_VIEWS + ("other",)
SHEET_FACE_SIDES = ("left", "right", "center", "hidden")
SIDE_KO = {"left": "왼쪽", "right": "오른쪽"}
MAX_PROBLEMS = 5
_SWAP = {"left": "right", "right": "left"}
# 정면·후면 그림이 기대와 다를 때 다시 그리는 방향 문장(design §5.8 원문).
_FACING_FIX = {
    "front": " Fix: the person must face the viewer directly (front view).",
    "back": " Fix: show the person from directly behind (back view); the face must not be visible.",
    "left": " Fix: exact side profile with the face pointing to the LEFT edge of the image and the person's LEFT side toward "
            "the viewer.",
    "right": " Fix: exact side profile with the face pointing to the RIGHT edge of the image and the person's RIGHT side "
             "toward the viewer.",
}
_PEOPLE_FIX = " Only one person. Remove the extra figures."  # 지금 _gen_checked 문장 그대로
# 시트 n번째 인물이 그려져야 하는 모습(design §5.8 DESC, 1부터).
SHEET_DESC = (
    "a FRONT view facing the viewer",
    "a THREE-QUARTER FRONT view turned 45° toward the left of the image",
    "a SIDE PROFILE facing the left edge of the image",
    "a THREE-QUARTER BACK view turned 135°, mostly the back, head turned to the upper left so only the cheek and ear outline "
    "show",
    "a BACK view from directly behind",
)
# warning 문구(design §7.5). 화면에 보이는 한국어다.
WARN_PEOPLE = "정면/후면에 여러 사람이 보일 수 있습니다"
WARN_ITEM = "소품 위치가 다를 수 있습니다"
WARN_FACING = "정면/후면 방향이 다를 수 있습니다"
WARN_SHEET_OUTFIT = "복장이 면마다 다를 수 있습니다"
WARN_SHEET_ROTATION = "시트의 도는 순서가 어색할 수 있습니다"
_WARN_SIDE = {
    "dropped": "옆모습({sides})이 맞지 않아 3D 에 넣지 않았습니다",
    "unchecked": "옆모습({sides})을 확인하지 못해 3D 에 넣지 않았습니다",
}


def seen_values(view: str) -> tuple[str, ...]:
    """그 보기에서 검사가 쓰는 보이는 쪽 열거값. 옆모습은 near·far, 그 밖은 화면 left·right."""
    return SEEN_SIDE if view in SIDES else SEEN_FLAT


def expected_facing(view: str) -> str:
    """보기의 인물 방향 기대값(front→front, back→back, left→left, right→right). 모르는 보기면 ""."""
    return view if view in ("front", "back", "left", "right") else ""


def expected_seen(view: str, side: str, part: str) -> frozenset[str]:
    """캐릭터 쪽 side 의 소품이 그 보기에서 보여야 하는 쪽(design §5.6 표). both 는 어느 보기에서도 틀린 값이다.

    정면은 화면 반대쪽, 후면은 같은 쪽(눈·볼은 안 보여도 맞다, 결정 S7), 옆모습은 가까운 쪽이면 near, 먼 쪽이면 far·none.
    소품 쪽이 틀린 값이면 그 보기의 모든 값을 맞다고 본다(검사하지 않는다).
    """
    if side not in SIDES:
        return frozenset(seen_values(view))
    if view == "front":
        return frozenset({_SWAP[side]})
    if view == "back":
        return frozenset({side, "none"}) if part in BACK_MAY_HIDE else frozenset({side})
    if view in SIDES:
        return frozenset({"near"}) if side == view else frozenset({"far", "none"})
    return frozenset(seen_values(view))


def _count(value) -> int | None:
    """사람 수: 정수(bool 제외)나 숫자 글자면 정수, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,3}\s*", value):
        return int(value)
    return None


def normalize_view_obs(payload, view: str, n_items: int) -> dict | None:
    """그림 검사 응답 → {people, facing, lettering, items{i: seen}}(design §7.1). payload 가 dict 가 아니면 None.

    facing 은 열거값 밖이면 other, lettering 은 True 일 때만 참, items 는 번호가 범위 안이고 seen 이 그 보기의 열거값인 것만
    (같은 번호는 뒤 값이 이긴다).
    """
    if not isinstance(payload, dict):
        return None
    allowed = seen_values(view)
    items: dict[int, str] = {}
    raw = payload.get("items") if isinstance(payload.get("items"), list) else []
    for entry in raw[: 4 * MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        index = pick_index(entry.get("index"), n_items)
        seen = _enum(entry.get("seen"), allowed)
        if index is not None and seen:
            items[index] = seen
    return {
        "people": _count(payload.get("people")),
        "facing": _enum(payload.get("facing"), VIEW_FACINGS) or "other",
        "lettering": payload.get("lettering") is True,
        "items": items,
    }


def view_mismatches(obs: dict, view: str, items: list[dict]) -> list[tuple]:
    """관찰값이 기대와 다른 것(design §5.6): ("people", n) · ("facing", got) · ("item", i, got). 사람 수를 모르면 건너뛴다."""
    out: list[tuple] = []
    people = obs.get("people")
    if isinstance(people, int) and not isinstance(people, bool) and people != 1:
        out.append(("people", people))
    if obs.get("facing") != expected_facing(view):
        out.append(("facing", obs.get("facing")))
    seen = obs.get("items") or {}
    for index, item in enumerate(items or []):
        got = seen.get(index, "none")
        if got not in expected_seen(view, item.get("side"), item.get("partEn")):
            out.append(("item", index, got))
    return out


def flip_observation(obs: dict, view: str) -> dict:
    """그림을 좌우로 뒤집었을 때의 관찰값(design §5.7). 정면·후면은 소품 left↔right, 옆모습은 방향 left↔right(near·far 그대로)."""
    flipped = dict(obs)
    seen = dict(obs.get("items") or {})
    if view in SIDES:
        flipped["facing"] = _SWAP.get(obs.get("facing"), obs.get("facing"))
        flipped["items"] = seen
    else:
        flipped["items"] = {index: _SWAP.get(value, value) for index, value in seen.items()}
    return flipped


def can_flip(obs: dict, view: str, items: list[dict]) -> bool:
    """반전한 그림을 쓸 수 있는가: 틀린 것이 있고, 사람 1명, 읽히는 글자 없음(결정 S6), 반전한 관찰값이 기대와 모두 맞음."""
    if not view_mismatches(obs, view, items):
        return False
    people = obs.get("people")
    if not (isinstance(people, int) and not isinstance(people, bool) and people == 1):
        return False
    if obs.get("lettering"):
        return False
    return not view_mismatches(flip_observation(obs, view), view, items)


def _item_fix(item: dict, got: str, view: str, pron: str) -> str:
    """소품 하나의 고칠 문장(design §5.8 원문)."""
    if not isinstance(item, dict) or item.get("side") not in SIDES or item.get("partEn") not in PARTS:
        return ""
    name = item.get("itemEn") or ""
    side = item["side"]
    part, parts = PARTS[item["partEn"]][0], PARTS[item["partEn"]][1]
    s_up = side.upper()
    if view in ("front", "back"):
        x_up = (_SWAP[side] if view == "front" else side).upper()
        if got == "both":
            return (f" Fix: the {name} was drawn on both {parts}; keep it only on {pron} {s_up} {part} ({x_up} side of the "
                    f"image) and remove it from the other {part}.")
        if got == "none":
            return f" Fix: the {name} is missing; draw it on {pron} {s_up} {part} ({x_up} side of the image)."
        return (f" Fix: the {name} was on the wrong {part}; it must be on {pron} {s_up} {part}, which is on the {x_up} side "
                "of the image in this view.")
    if view in SIDES:
        if got == "both":
            return f" Fix: the {name} must be on one {part} only, never on both."
        if side == view:
            return f" Fix: the {name} must be clearly visible on the {part} nearest the viewer."
        return f" Fix: the {name} must not be on the near {part}; it is on the far {part}, hidden behind the body."
    return ""


def view_fix_text(mismatches, view: str, items: list[dict], pron: str) -> str:
    """틀린 것마다 고칠 문장 한 개를 사람 수 → 방향 → 소품(번호 순) 순서로 잇는다(design §5.8). 문장마다 앞에 한 칸이 있다."""
    order = {"people": 0, "facing": 1, "item": 2}
    pieces: list[str] = []
    for mm in sorted(mismatches or [], key=lambda m: (order.get(m[0], 9), m[1] if m[0] == "item" else 0)):
        if mm[0] == "people":
            pieces.append(_PEOPLE_FIX)
        elif mm[0] == "facing":
            pieces.append(_FACING_FIX.get(view, ""))
        elif mm[0] == "item" and 0 <= mm[1] < len(items or []):
            pieces.append(_item_fix(items[mm[1]], mm[2], view, pron))
    return "".join(p for p in pieces if p)


def normalize_sheet_obs(payload) -> dict | None:
    """시트 검사 응답 → {figures[{view, faceSide}], consistent, problems}(design §7.3). payload 가 dict 가 아니면 None.

    figures 는 앞 8개(열거값 밖·dict 아님은 other·hidden — 자리를 지킨다), problems 는 한 줄 120자 최대 5개.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("figures") if isinstance(payload.get("figures"), list) else []
    figures = []
    for entry in raw[:8]:
        entry = entry if isinstance(entry, dict) else {}
        figures.append({
            "view": _enum(entry.get("view"), SHEET_VIEW_VALUES) or "other",
            "faceSide": _enum(entry.get("faceSide"), SHEET_FACE_SIDES) or "hidden",
        })
    consistent = payload.get("consistent")
    problems_raw = payload.get("problems") if isinstance(payload.get("problems"), list) else []
    problems = [p for p in (clean_line(x, 120) for x in problems_raw[: 4 * MAX_PROBLEMS]) if p][:MAX_PROBLEMS]
    return {
        "figures": figures,
        "consistent": consistent is True or (isinstance(consistent, str) and consistent.strip().lower() == "true"),
        "problems": problems,
    }


def sheet_mismatches(obs: dict) -> list[tuple]:
    """시트가 기대와 다른 것(design §7.3): ("count", n) · ("figure", k, view, faceSide) · ("outfit",).

    인물 수가 5가 아니면 인물별 비교는 하지 않는다. k 는 1부터, 2·3·4번째는 얼굴이 화면 왼쪽이어야 한다.
    """
    out: list[tuple] = []
    figures = obs.get("figures") or []
    if len(figures) != len(SHEET_VIEWS):
        out.append(("count", len(figures)))
    else:
        for k, figure in enumerate(figures):
            view, face = figure.get("view"), figure.get("faceSide")
            if view != SHEET_VIEWS[k] or (k in SHEET_FACE_LEFT and face != "left"):
                out.append(("figure", k + 1, view, face))
    if not obs.get("consistent"):
        out.append(("outfit",))
    return out


def _drawn_as(view: str, face: str) -> str:
    """'a back view' · 'a side view facing the right of the image'(얼굴이 화면 왼쪽·오른쪽을 볼 때만 붙인다)."""
    view = view or "other"
    article = "an" if view[:1] in "aeiou" else "a"
    text = f"{article} {view} view"
    if face in SIDES:
        text += f" facing the {face} of the image"
    return text


def sheet_fix_text(mismatches) -> str:
    """시트 회전 고칠 문장(design §5.8). 복장 고칠 문장은 characters 가 지금 문장으로 붙인다."""
    pieces: list[str] = []
    for mm in mismatches or []:
        if mm[0] == "count":
            pieces.append(" Fix: draw exactly five full-body figures in one row.")
        elif mm[0] == "figure" and 1 <= mm[1] <= len(SHEET_DESC):
            pieces.append(f" Fix figure {mm[1]} (counting from the left): it must be {SHEET_DESC[mm[1] - 1]} — it was drawn "
                          f"as {_drawn_as(mm[2], mm[3])}.")
    return "".join(pieces)


def sheet_figures_text(obs: dict | None) -> str:
    """로그용 인물 방향 약어(front,3qf,side,3qb,back,other). 관찰값이 없으면 "-"."""
    short = {"three-quarter front": "3qf", "three-quarter back": "3qb"}
    if not obs:
        return "-"
    return ",".join(short.get(f.get("view"), f.get("view") or "other") for f in obs.get("figures") or []) or "-"


def view_warnings(mismatches, view: str) -> list[str]:
    """다시 그려도 틀린 그림의 warning(design §7.5). 정면·후면은 사람 수 → 소품 쪽 → 방향, 옆모습은 '3D 에 넣지 않았습니다'."""
    if not mismatches:
        return []
    if view in SIDES:
        return [_WARN_SIDE["dropped"].format(sides=SIDE_KO[view])]
    kinds = {mm[0] for mm in mismatches}
    out = []
    if "people" in kinds:
        out.append(WARN_PEOPLE)
    if "item" in kinds:
        out.append(WARN_ITEM)
    if "facing" in kinds:
        out.append(WARN_FACING)
    return out


def side_warnings(statuses: dict) -> list[str]:
    """옆모습 결과({left|right: status}) → warning. 같은 이유로 둘 다 빠지면 '왼쪽·오른쪽' 한 문구(design §7.5)."""
    out: list[str] = []
    for status in ("dropped", "unchecked"):
        sides = [SIDE_KO[side] for side in SIDES if (statuses or {}).get(side) == status]
        if sides:
            out.append(_WARN_SIDE[status].format(sides="·".join(sides)))
    return out


def merge_warnings(*groups) -> list[str]:
    """warning 목록들을 순서대로 잇고 같은 문구는 한 번만 둔다(빈 글은 뺀다)."""
    out: list[str] = []
    for group in groups:
        for text in group or []:
            if text and text not in out:
                out.append(text)
    return out
