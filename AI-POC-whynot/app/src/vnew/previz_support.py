"""vnew 프리비즈 — 받침 판단(previz-support-reasoning, 2026-10-29).

설계자 요청: "캐릭터가 앉아 있다 → 어디에? 주차봉 → 주차봉은 무엇이고 어떻게 앉았나?(주차를 막는 기둥, 보통
무릎 위 높이라 앉을 수 있다. 캐릭터는 그 위에 걸터앉아 있다.)" 그림을 보는 유일한 단계(previz_grammar.read_cut)가
인물마다 이 순서로 받침을 읽고(grammar.supports), 이 모듈이 그 값을 정리·환산해 장면 설계 프롬프트와 서버 보정에
넘긴다.

- normalize_supports: 모델 출력 supports[] 정리(열거값·길이·한 줄·이름 중복). seen(받침과 닿는 몸이 그려졌는지)이
  hidden(모르는 값 포함)이면 받침 값을 none 으로 비운다.
- support_height_m: heightCue(서 있는 사람 몸 기준 말) → 윗면 높이(m). 숫자는 서버가 정한다(모델 m 값은 대비책).
- prompt_line·PROMPT_GUIDE_KO: 장면 설계 프롬프트의 [받침 판단] 줄·지시 단락.
- apply_supports: 컷 인물 자세·앉을 소품(sitOn·sittable)·소품 윗면 높이를 받침 판단에 맞춘다(084 보정 1차 전).
  seen 이 visible·partial 인 읽기만 쓴다.

스펙: .kiro/specs/previz-support-reasoning/design.md §3. 순수 함수 모듈이다(네트워크·S3·LLM·numpy 없음). 로그만 남긴다.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata

from . import previz_collision

log = logging.getLogger("vpoc")

# ================================================================ 열거값(requirements R1.1)

# seen(필수 수정 A, 2026-10-29): 받침과 그 받침에 닿는 몸(엉덩이·허벅지·발)이 그림에 실제로 그려졌는지.
# Claude 실측(test3, Fable 5.1): 다리·받침이 화면 밖인데도 컷 설명·맥락으로 stand/floor·난간을 짐작해 썼다.
# 그래서 hidden(모르는 값·빈 값 포함)이면 서버가 자세·앉을 소품·소품 높이를 바꾸지 않는다.
SEEN = ("visible", "partial", "hidden")
SEEN_APPLY = ("visible", "partial")
POSTURES = ("stand", "sit", "kneel", "lie", "crouch", "lean", "walk")
SUPPORT_KINDS = (
    "floor", "chair", "sofa", "bench", "stool", "step", "stairs", "rail", "bollard", "ledge",
    "box", "table", "counter", "vehicle", "bed", "wall", "pole", "other", "none",
)
HEIGHT_CUES = ("floor", "ankle", "shin", "knee", "above_knee", "mid_thigh", "hip", "waist", "chest", "shoulder")
CONTACTS = ("perch", "seat", "straddle", "lean", "kneel_on", "lie_on", "stand_on", "floor", "none")
FEET = ("floor", "dangling", "on_prop", "tucked", "none")

# 모델 출력 문자열 길이 상한(R1.3). 프롬프트·화면에 한 줄로 들어간다.
NAME_MAX = 40
SUPPORT_EN_MAX = 80
SUPPORT_WHAT_MAX = 200
REASON_MAX = 200
# 한 컷 받침 판단 항목 수 상한(보이는 인물마다 1개 — 웹툰 한 패널에 이보다 많은 인물은 드물다).
MAX_SUPPORTS = 12
# 윗면 높이 범위(m, R1.3·R3.2).
HEIGHT_MAX_M = 1.5

# ================================================================ 높이 환산(R3.1)

# heightCue → 서 있는 사람 키 대비 받침 윗면 비율. 몸 모델은 카메라 계산과 같다(previz_fit.BODY_FRACTION):
# knee 0.28 · waist 0.60 · chest 0.72 · shoulder 0.82 는 그 표 그대로다. 나머지는 그 사이를 나눈 근사다.
# - hip 0.50: previz_fit.body_y 가 앉은 상체를 재는 기준점(엉덩이 관절 = 키의 절반)과 같다.
# - ankle 0.06: 복사뼈(키의 약 4~6%). shin 0.18: 정강이 가운데(무릎 0.28 의 약 2/3).
# - above_knee 0.33: 무릎 바로 위(키 1.7m 에서 0.56m — 보통 주차봉·볼라드 0.5~0.6m, 걸터앉는 높이).
# - mid_thigh 0.40: 무릎(0.28)과 엉덩이(0.50) 가운데 근처(1.7m 에서 0.68m — 높은 스툴·낮은 난간).
HEIGHT_CUE_RATIO = {
    "floor": 0.0,
    "ankle": 0.06,
    "shin": 0.18,
    "knee": 0.28,
    "above_knee": 0.33,
    "mid_thigh": 0.40,
    "hip": 0.50,
    "waist": 0.60,
    "chest": 0.72,
    "shoulder": 0.82,
}
BASE_HEIGHT_M = previz_collision.BASE_HEIGHT_M

# ================================================================ 한국어 라벨(R3.3, 화면 vnew.js 와 같은 글자 — 검사 sr-k)

POSTURE_KO = {
    "stand": "서 있음", "sit": "앉음", "kneel": "무릎 꿇음", "lie": "누움",
    "crouch": "웅크림", "lean": "기댐", "walk": "걷기",
}
CONTACT_KO = {
    "perch": "걸터앉기", "seat": "의자처럼 앉기", "straddle": "올라타기", "lean": "기대기",
    "kneel_on": "무릎 꿇기", "lie_on": "눕기", "stand_on": "올라서기", "floor": "바닥",
}
FEET_KO = {"floor": "바닥에 닿음", "dangling": "띄움", "on_prop": "받침에 올림", "tucked": "접음"}
HEIGHT_CUE_KO = {
    "floor": "바닥", "ankle": "발목", "shin": "정강이", "knee": "무릎", "above_knee": "무릎 위",
    "mid_thigh": "허벅지 중간", "hip": "엉덩이", "waist": "허리", "chest": "가슴", "shoulder": "어깨",
}
SUPPORT_KIND_KO = {
    "floor": "바닥", "chair": "의자", "sofa": "소파", "bench": "벤치", "stool": "스툴", "step": "디딤",
    "stairs": "계단", "rail": "난간", "bollard": "주차봉", "ledge": "턱", "box": "상자", "table": "탁자",
    "counter": "카운터", "vehicle": "차량", "bed": "침대", "wall": "벽", "pole": "기둥", "other": "물체",
}

# ================================================================ 받침 맞춤 상수(R5)

# 소품 윗면과 목표 높이 차이가 이 값(m)보다 클 때만 소품 높이를 바꾼다.
SUPPORT_HEIGHT_TOL = 0.15
# 아래면이 이 높이(m) 이하면 "바닥에 놓인 소품"(높이를 바꾼다), 아니면 "떠 있는 소품"(위아래로 옮긴다).
FLOOR_PROP_BOTTOM_M = 0.05
# 앉을 소품을 고르는 앉는 방식(나머지 — floor·lean 등 — 은 앉을 소품을 찾지 않는다).
SEATED_CONTACTS = ("perch", "seat", "straddle")
# 읽은 자세 → 컷 인물 pose. lean 은 서서 기대므로 stand. crouch·walk 는 바꾸지 않는다(R5.3).
POSE_FROM_POSTURE = {"sit": "sit", "stand": "stand", "kneel": "kneel", "lie": "lie", "lean": "stand"}
STANDING_POSTURES = ("stand", "walk", "lean")

# 소품 라벨에서 찾는 받침 종류 낱말(한국어는 부분 문자열, 영어는 낱말). 한 글자 한국어는 오탐이 많아 거의 쓰지 않는다
# (예: "차" 는 "주차봉"에도 들어간다).
KIND_WORDS = {
    "chair": ("의자", "chair", "seat"),
    "sofa": ("소파", "sofa", "couch"),
    "bench": ("벤치", "bench"),
    "stool": ("스툴", "stool"),
    "step": ("계단", "디딤", "step", "stair"),
    "stairs": ("계단", "stair", "step"),
    "rail": ("난간", "가로대", "rail", "railing"),
    "bollard": ("주차봉", "볼라드", "말뚝", "차단봉", "bollard", "post"),
    "ledge": ("턱", "경계석", "ledge", "kerb", "curb"),
    "box": ("상자", "박스", "box", "crate"),
    "table": ("탁자", "테이블", "책상", "table", "desk"),
    "counter": ("카운터", "계산대", "counter"),
    "vehicle": ("자동차", "차량", "오토바이", "자전거", "vehicle", "car", "bike", "motorcycle", "scooter"),
    "bed": ("침대", "bed"),
    "wall": ("벽", "담장", "wall"),
    "pole": ("기둥", "전봇대", "pole", "post", "column"),
}
# supportEn 에서 뺄 수식어(재질·크기·위치·색). 남은 낱말(3자 이상)로 소품 라벨을 찾는다.
SUPPORT_EN_STOPWORDS = frozenset((
    "the", "and", "with", "for", "from", "its", "top", "bar", "edge", "side", "part", "piece", "upper", "lower",
    "front", "back", "metal", "steel", "iron", "wood", "wooden", "plastic", "stone", "concrete", "short", "tall",
    "low", "high", "small", "big", "large", "long", "old", "new", "parking", "street", "road", "black", "white",
    "grey", "gray", "red", "blue", "green", "yellow", "orange", "brown", "silver",
))

_WS = re.compile(r"\s+")
_EN_WORD = re.compile(r"[a-z]+")


# ================================================================ 정리 도우미


def _text(value, limit: int) -> str:
    """모델 문자열을 한 줄로(제어 문자·공백류 → 한 칸) 정리하고 limit 자로 자른다. 문자열·숫자가 아니면 ""."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    text = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in str(value))
    # 자른 뒤에도 끝 공백을 지운다(다시 정리해도 같은 값 — 멱등).
    return _WS.sub(" ", text).strip()[:limit].rstrip()


def _enum(value, allowed, default: str) -> str:
    """열거값: 소문자, 공백·하이픈 → '_'. 모르는 값이면 default."""
    key = _WS.sub("_", _text(value, 40).lower()).replace("-", "_")
    return key if key in allowed else default


def _height(value):
    """윗면 높이 숫자(m)를 0~HEIGHT_MAX_M 로 자르고 3자리. 숫자가 아니면 None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return round(min(HEIGHT_MAX_M, max(0.0, number)), 3)


def _kind(value) -> str:
    """supportKind: 빈 값이면 none, 모르는 값이면 other."""
    return "none" if not _text(value, 40) else _enum(value, SUPPORT_KINDS, "other")


def _name_key(value) -> str:
    return _text(value, NAME_MAX)


# ================================================================ 공개: 정리(R1.3·R5.7)


def normalize_supports(raw) -> list[dict]:
    """모델 출력 supports[] 를 정리한다. 이름이 비면 버리고, 같은 이름은 첫 항목만 쓴다. 늘 리스트.

    seen 이 모르는 값·빈 값이면 hidden 이다(보수적). hidden 이면 supportKind·contact·feet 는 none, heightCue 는 "",
    supportHeightM 은 None 으로 둔다(그림으로 판단하지 않은 값을 쓰지 않는다). 문자열(supportEn 등)은 그대로 둔다.
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    names: set[str] = set()
    for item in raw:
        if len(out) >= MAX_SUPPORTS:
            break
        if not isinstance(item, dict):
            continue
        name = _name_key(item.get("name"))
        if not name or name in names:
            continue
        names.add(name)
        entry = {
            "name": name,
            "seen": _enum(item.get("seen"), SEEN, "hidden"),
            "posture": _enum(item.get("posture"), POSTURES, "stand"),
            "supportEn": _text(item.get("supportEn"), SUPPORT_EN_MAX),
            "supportWhatEn": _text(item.get("supportWhatEn"), SUPPORT_WHAT_MAX),
            "supportKind": _kind(item.get("supportKind")),
            "heightCue": _enum(item.get("heightCue"), HEIGHT_CUES, ""),
            "supportHeightM": _height(item.get("supportHeightM")),
            "contact": _enum(item.get("contact"), CONTACTS, "none"),
            "feet": _enum(item.get("feet"), FEET, "none"),
            "reasonEn": _text(item.get("reasonEn"), REASON_MAX),
        }
        if entry["seen"] == "hidden":
            entry.update({"supportKind": "none", "heightCue": "", "supportHeightM": None, "contact": "none", "feet": "none"})
        out.append(entry)
    return out


def normalize_actor_support(raw) -> dict | None:
    """컷 인물 support(apply_supports 가 붙인 값)를 정리한다. dict 가 아니면 None.

    seen 은 열거값으로 검사한다(모르는 값은 hidden). 키가 없으면 두지 않는다(seen 이전 저장본 그대로).
    """
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    if "seen" in raw:
        out["seen"] = _enum(raw.get("seen"), SEEN, "hidden")
    out.update({
        "posture": _enum(raw.get("posture"), POSTURES, "stand"),
        "supportKind": _kind(raw.get("supportKind")),
        "supportEn": _text(raw.get("supportEn"), SUPPORT_EN_MAX),
        "contact": _enum(raw.get("contact"), CONTACTS, "none"),
        "feet": _enum(raw.get("feet"), FEET, "none"),
        "heightM": _height(raw.get("heightM")),
    })
    return out


def support_height_m(support, height_m=None):
    """받침 윗면 높이(m). heightCue 가 있으면 비율 × 키(없으면 BASE_HEIGHT_M), 없으면 supportHeightM, 둘 다 없으면 None.

    결과는 0~HEIGHT_MAX_M, 소수 3자리.
    """
    if not isinstance(support, dict):
        return None
    cue = _enum(support.get("heightCue"), HEIGHT_CUES, "")
    if cue:
        height = height_m if isinstance(height_m, (int, float)) and not isinstance(height_m, bool) and height_m > 0 else BASE_HEIGHT_M
        return _height(HEIGHT_CUE_RATIO[cue] * float(height))
    return _height(support.get("supportHeightM"))


def _is_floor(support: dict) -> bool:
    return support.get("supportKind") == "floor" or support.get("contact") == "floor"


def _skipped(support: dict) -> bool:
    return support.get("supportKind") == "none" and support.get("contact") == "none"


# ================================================================ 공개: 프롬프트(R4)

PROMPT_GUIDE_KO = (
    "[받침 판단 반영] 받침 판단은 패널 그림에서 읽은 근거입니다. 앉거나 기대거나 올라선 받침은 그 종류의 소품으로, "
    "윗면 높이를 위 값에 맞춰 만드세요. 다른 물체로 바꾸지 마세요(예: 기둥을 난간으로). 앉은 인물은 pose:'sit', "
    "sitOn=그 소품 id, 기대는 인물은 그 소품 바로 옆에 서게 하세요. 같은 받침을 여러 컷이 쓰면 한 소품을 공유하세요."
)


def prompt_line(support) -> str | None:
    """[받침 판단] 한 줄(생각 순서: 자세 → 받침 → 정체·보통 크기 → 윗면 높이 → 앉는 방식, 발). 판단 불가·hidden 이면 None.

    키는 BASE_HEIGHT_M 기준이다(설계 전이라 인물 키를 모른다). 문자열은 normalize_supports 로 한 줄·길이 제한된 값이다.
    """
    if not isinstance(support, dict):
        return None
    sup = (normalize_supports([support]) or [None])[0]
    if sup is None or sup["seen"] == "hidden" or _skipped(sup):
        return None
    parts = [f"{sup['name']}: {POSTURE_KO.get(sup['posture'], sup['posture'])}"]
    cue_ko = HEIGHT_CUE_KO.get(sup["heightCue"], "")
    if _is_floor(sup):
        parts.append("받침=바닥(ground)")
    else:
        label = sup["supportEn"] or SUPPORT_KIND_KO.get(sup["supportKind"], "") or sup["supportKind"]
        what = [bit for bit in (sup["supportWhatEn"], f"보통 {cue_ko}" if cue_ko else "") if bit]
        parts.append(f"받침={label}" + (f"({'; '.join(what)})" if what else ""))
        top = support_height_m(sup, BASE_HEIGHT_M)
        if top is not None:
            basis = f"키 {BASE_HEIGHT_M:g}m 기준 {cue_ko}" if cue_ko else "그림 추정"
            parts.append(f"윗면 높이≈{top:.2f}m({basis})")
    tail = []
    if sup["contact"] not in ("floor", "none"):
        tail.append(CONTACT_KO[sup["contact"]])
    if sup["feet"] in FEET_KO:
        tail.append(f"발={FEET_KO[sup['feet']]}")
    if tail:
        parts.append(", ".join(tail))
    return "   [받침 판단] " + " → ".join(parts)


# ================================================================ 앉을 소품 고르기(R5.4)


def _label_text(prop: dict) -> str:
    return " ".join((
        str(prop.get("labelKo") or ""), str(prop.get("labelEn") or ""), str(prop.get("id") or "").replace("_", " "),
    )).lower()


def _en_match(word: str, tokens: set[str]) -> bool:
    return any(token in (word, word + "s", word + "es", word + "ing") for token in tokens)


def _support_words(support: dict) -> list[str]:
    """받침 종류·영어 이름에서 소품 라벨로 찾을 낱말(중복 없이, 순서 고정)."""
    kind = support.get("supportKind") or ""
    words: list[str] = []
    for word in (kind,) + KIND_WORDS.get(kind, ()) + tuple(_EN_WORD.findall(str(support.get("supportEn") or "").lower())):
        if word in ("floor", "other", "none") or word in words:
            continue
        if word.isascii() and (len(word) < 3 or word in SUPPORT_EN_STOPWORDS):
            continue
        words.append(word)
    return words


def _word_hit(label: str, words: list[str]) -> bool:
    tokens = set(_EN_WORD.findall(label))
    for word in words:
        if word.isascii():
            if _en_match(word, tokens):
                return True
        elif word in label:
            return True
    return False


def _pick_prop(ca: dict, support: dict, items: list[tuple[dict, dict]]) -> tuple[dict, dict] | None:
    """앉을 소품 (prop, vol) 또는 None.

    후보 = 윗면 SEAT_TOP_MIN~SEAT_TOP_MAX 이고 발 → 바닥면 거리 ≤ SIT_ON_MAX_D(보정 pick_seat 등급 1 과 같은 조건 —
    여기서 고른 소품을 보정이 sitOn 으로 실제로 앉힌다). sitOn 소품이 후보면 그것, 아니면 (1) 받침 낱말이 라벨에 든 소품
    (2) sittable (3) 나머지 순서. 같은 단계 안에서는 (거리, 높은 윗면, id) 순.
    """
    x, z = previz_collision._foot(ca)  # noqa: SLF001 — 같은 발 위치 규칙
    cands = []
    for prop, vol in items:
        top = vol["cy"] + vol["hy"]
        if not (previz_collision.SEAT_TOP_MIN <= top <= previz_collision.SEAT_TOP_MAX):
            continue
        dist = previz_collision.footprint_dist(vol, x, z)
        if dist > previz_collision.SIT_ON_MAX_D:
            continue
        cands.append(((round(dist, 3), -round(top, 4), vol["id"], vol["index"]), prop, vol))
    if not cands:
        return None
    cands.sort(key=lambda item: item[0])
    sit_on = str(ca.get("sitOn") or "").strip()
    for _key, prop, vol in cands:
        if sit_on and vol["id"] == sit_on:
            return prop, vol
    words = _support_words(support)
    for tier in (
        lambda prop, vol: _word_hit(_label_text(prop), words),
        lambda prop, vol: vol["sittable"],
        lambda prop, vol: True,
    ):
        for _key, prop, vol in cands:
            if tier(prop, vol):
                return prop, vol
    return None  # pragma: no cover — 마지막 단계가 모든 후보를 받는다


# ================================================================ 소품 높이 맞춤(R5.5)


def _prop_top(prop: dict) -> float:
    vol = previz_collision.prop_volume(prop)
    return vol["cy"] + vol["hy"]


def _fit_height(prop: dict, target: float) -> tuple[float, float] | None:
    """소품 윗면을 target 으로. 바꿨으면 (전 윗면, 새 윗면), 허용오차 안이면 None."""
    vol = previz_collision.prop_volume(prop)
    top = vol["cy"] + vol["hy"]
    if abs(top - target) <= SUPPORT_HEIGHT_TOL:
        return None
    height = vol["hy"] * 2.0
    size = list(prop.get("size") or [])[:3]
    while len(size) < 3:
        size.append(0.5)
    pos = list(prop.get("pos") or [])[:3]
    while len(pos) < 3:
        pos.append(0.0)
    if vol["cy"] - vol["hy"] <= FLOOR_PROP_BOTTOM_M or target - height < 0.0:
        # 바닥에 놓인 소품(또는 옮기면 바닥 아래로 가는 소품): 높이 = 목표, 중심 = 목표/2.
        size[1] = round(target, 3)
        pos[1] = round(target / 2.0, 3)
    else:
        # 떠 있는 소품(가로대 등): 두께는 그대로, 위아래로만 옮긴다.
        pos[1] = round(target - height / 2.0, 3)
    prop["size"] = size
    prop["pos"] = pos
    return top, _prop_top(prop)


# ================================================================ 공개: 받침 맞춤(R5)


def _apply_actor(ca: dict, sup: dict, height_m: float, items: list[tuple[dict, dict]]) -> tuple[dict, dict | None]:
    """컷 인물 하나에 받침 판단을 붙인다. (결과 항목, 앉힌 prop 또는 None).

    seen 이 visible·partial 일 때만 자세·앉을 소품을 맞추고 높이 맞춤 읽기에 넣는다. hidden 이면 support 만 붙이고
    pose·sitOn·소품은 그대로 둔다(그림에 없는 받침을 짐작해 컷마다 앉았다 섰다 하지 않게, 필수 수정 A).
    """
    entry = {
        "actorId": str(ca.get("id") or ""),
        "name": sup["name"],
        "seen": sup["seen"],
        "posture": sup["posture"],
        "supportKind": sup["supportKind"],
        "supportEn": sup["supportEn"],
        "contact": sup["contact"],
        "feet": sup["feet"],
        "heightM": support_height_m(sup, height_m),
        "propId": "",
        "propTopM": None,
    }
    ca["support"] = {key: entry[key] for key in ("seen", "posture", "supportKind", "supportEn", "contact", "feet", "heightM")}
    if sup["seen"] not in SEEN_APPLY or _skipped(sup):
        # 판단 불가. 정규화 뒤 hidden 은 늘 supportKind·contact none 이다 — 상태 이름만 hidden 으로 구분한다.
        entry["status"] = "hidden" if sup["seen"] == "hidden" else "skipped"
        return entry, None
    new_pose = POSE_FROM_POSTURE.get(sup["posture"])
    old_pose = str(ca.get("pose") or "stand")
    if new_pose and new_pose != old_pose:
        ca["pose"] = new_pose
        entry["poseFrom"] = old_pose
        if new_pose != "sit":
            ca.pop("sitOn", None)  # 앉지 않는 인물에 앉을 소품 힌트를 남기지 않는다.
    if sup["posture"] in STANDING_POSTURES:
        entry["status"] = "standing"
        return entry, None
    if sup["posture"] != "sit" or sup["contact"] not in SEATED_CONTACTS:
        entry["status"] = "ok"
        return entry, None
    picked = _pick_prop(ca, sup, items)
    if picked is None:
        entry["status"] = "noProp"
        return entry, None
    prop, vol = picked
    prop["sittable"] = True
    ca["sitOn"] = vol["id"]
    entry["propId"] = vol["id"]
    entry["status"] = "ok"
    return entry, prop


def _apply_scene(scene: dict, grammar_by_cut: dict) -> dict:
    props = [p for p in (scene.get("props") or []) if isinstance(p, dict)]
    items = [(prop, previz_collision.prop_volume(prop, i)) for i, prop in enumerate(props)]
    id_by_name: dict[str, str] = {}
    height_by_id: dict[str, float] = {}
    for actor in scene.get("actors") or []:
        if not isinstance(actor, dict):
            continue
        actor_id = str(actor.get("id") or "")
        height_by_id[actor_id] = previz_collision._num(actor.get("heightM"), BASE_HEIGHT_M)  # noqa: SLF001
        name = _name_key(actor.get("name"))
        if name and name not in id_by_name:
            id_by_name[name] = actor_id
    result: dict[str, list[dict]] = {}
    seated: list[tuple[dict, dict]] = []  # (entry, prop)
    for cut in scene.get("timeline") or []:
        if not isinstance(cut, dict):
            continue
        cut_id = str(cut.get("cutId") or "")
        grammar = grammar_by_cut.get(cut_id)
        supports = normalize_supports(grammar.get("supports")) if isinstance(grammar, dict) else []
        if not supports:
            continue
        by_id = {str(ca.get("id") or ""): ca for ca in (cut.get("actors") or []) if isinstance(ca, dict)}
        entries: list[dict] = []
        for sup in supports:
            actor_id = id_by_name.get(sup["name"])
            ca = by_id.get(actor_id) if actor_id else None
            if ca is None or ca.get("manual"):
                continue  # 이름이 장면 인물과 맞지 않거나 이 컷에 없다(무시). 작업 200: 직접 고친 인물은 그대로 둔다.
            entry, prop = _apply_actor(ca, sup, height_by_id.get(actor_id, BASE_HEIGHT_M), items)
            entries.append(entry)
            if prop is not None:
                seated.append((entry, prop))
        if entries:
            result[cut_id] = entries

    # 소품 높이: 같은 소품에 앉은 읽기들의 heightM 중앙값(장면 단위). 소품은 장면 props 순서로 본다(결정적).
    for prop in props:
        group = [entry for entry, p in seated if p is prop]
        heights = [entry["heightM"] for entry in group if entry["heightM"] is not None]
        if not heights:
            continue
        target = min(previz_collision.SEAT_TOP_MAX, max(previz_collision.SEAT_TOP_MIN, statistics.median(heights)))
        changed = _fit_height(prop, target)
        if changed is None:
            continue
        fix = {"propId": str(prop.get("id") or ""), "fromTopM": round(changed[0], 3), "toTopM": round(changed[1], 3)}
        for entry in group:
            entry["heightFix"] = dict(fix)
        log.info(
            "vnew.previz.support.height loc=%s prop=%s from=%.3f to=%.3f n=%d",
            previz_collision._log_id(scene.get("locationId")), previz_collision._log_id(fix["propId"]),  # noqa: SLF001
            fix["fromTopM"], fix["toTopM"], len(heights),
        )
    for entry, prop in seated:
        entry["propTopM"] = round(_prop_top(prop), 3)
    # 키 순서를 고정한다(poseFrom·heightFix 는 있을 때만, status 는 끝).
    for entries in result.values():
        for entry in entries:
            entry["status"] = entry.pop("status")
    return result


def apply_supports(scenes, grammar_by_cut) -> dict:
    """장면마다 컷 인물에 받침 판단을 맞춘다(제자리에서 고친다). 반환 {cutId: [항목]} — 받침 판단이 짝지어진 컷만.

    항목 = {actorId, name, seen, posture, supportKind, supportEn, contact, feet, heightM, propId, propTopM, poseFrom?,
    heightFix?, status}. status: ok | noProp | standing | skipped | hidden. 컷 인물에 support·pose·sitOn, 소품에 sittable·높이.
    """
    out: dict[str, list[dict]] = {}
    grammar_by_cut = grammar_by_cut if isinstance(grammar_by_cut, dict) else {}
    for scene in scenes or []:
        if isinstance(scene, dict):
            out.update(_apply_scene(scene, grammar_by_cut))
    return out
