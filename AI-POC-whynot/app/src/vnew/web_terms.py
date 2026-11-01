"""일반 인터넷 외모 용어 → 실사 캐스팅 사진 묘사(지시서 112, 2026-11-01).

## 이 모듈이 하는 일

사용자가 캐릭터 설명(char.extraPromptKo)에 쓰는 인터넷 용어·신조어 가운데 외모·체격을 뜻하는 것을 실사 캐스팅 문장(casting._cast_text)에
더할 영어 조각으로 둔다. 웹툰을 영상으로 만들 때 쓰는 여러 인터넷 용어의 자리이고, BL 유형(genre_bl)은 그 가운데 사용자가 직접 쓴 경우에만
참고하는 한 갈래다(사용자 보충 지시 2026-11-01: "BL이 주가 되면 안 된다").

해석 원칙(사용자 지시 2026-11-01):
- 외모·체격·인상을 뜻하는 부분만 사진 묘사로 바꾼다. 포지션·성격·관계 뜻(예: BL 의 공·수)은 외모를 더하지 않는다.
- 실사 캐스팅 문장에만 쓴다. 웹툰 외모 문장(lookEn)·영상 프롬프트·그림 쪽은 바꾸지 않는다.
- 사용자가 직접 쓴 체격 용어는 사용자 의도다. 그래서 체격 조각은 외모 문장(그림에서 읽은 사실)에 체격 낱말이 있어도 늘 넣고
  (genre_bl.hint_pieces 의 forced), 외모 문장의 부딪히는 체격 낱말은 캐스팅 문장을 만들 때만 뺀다(strip_conflicts). 원본 lookEn 은 그대로다.

## 표

떡대(ttokdae): 인터넷 용어로 체격과 비슷한 말. 주로 남성의 크고 넓적하며 건장한 몸집(사용자 정의). 사용자가 준 참고 사진 2장(Pinterest "떡대"
검색 결과)의 공통 특징을 한 조각에 1:1 로 옮겼다(design D2 대응표). 얼굴·표정은 특정하지 않는다. 비만·지방으로 읽힐 낱말(fat·chubby·heavy-set·
heavily·stocky·bulky 등)은 쓰지 않는다. masculine 은 steering genre-bl 의 "'수' 유형 조각에 masculine·adult man 낱말" 규칙 때문이다 — 떡대수도
이 조각 하나만 쓴다.

113(웹 검색 용어 풀이)이 같은 자리(TERMS·known_terms·term_pieces)를 쓸 수 있게 이름·조각·종류를 한 표에 둔다. 이 모듈은 순수 함수와 표만
갖는다(import 는 re 뿐, 저장·외부 호출 없음).
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- 종류
PHYSIQUE = "physique"  # 몸(체격) 용어. 캐스팅 구도를 허리 위로 바꾸고(casting.base_text) 부딪히는 체격 낱말을 뺀다

# --------------------------------------------------------------------------- 떡대(design D2)
TTOKDAE = "ttokdae"
# 참고 사진 특징 → 영어(design D2 대응표):
#   보디빌더처럼 크고 묵직한 근육형 몸(비만 아님) → a big, powerfully built muscular masculine body
#   매우 넓은 어깨 → very broad shoulders · 두꺼운 목 → a thick neck · 넓고 근육이 두드러진 등 → a wide muscular back
#   두툼한 가슴 → a broad, thick chest · 두꺼운 팔(이두·삼두) → thick muscular arms
TTOKDAE_BUILD = (
    "a big, powerfully built muscular masculine body with very broad shoulders, a thick neck, a wide muscular back, "
    "a broad, thick chest and thick muscular arms"
)
# 떡대와 부딪히는 체격 낱말(design D5). 같은 명사구에서 사람·몸 명사(_BODY_HEADS) 앞에 올 때만 뺀다("thin lips"·"slim-fit trousers"·
# "a slender angular face"·"narrow eyes" 는 그대로). 두 낱말 꼴(lightly built)과 붙임표 꼴(slim-built)은 한 낱말로 본다.
_TTOKDAE_CONFLICTS = (
    "slim", "slimmer", "slender", "lean", "leaner", "skinny", "petite", "thin", "thinner", "slight", "lanky", "wiry", "willowy",
    "frail", "scrawny", "delicate", "gaunt", "bony", "small", "narrow", "average", "medium", "small-framed", "narrow-shouldered",
    "slim-built", "slight-framed", "lightly built", "lightly-built", "slightly built", "slightly-built",
)

TERMS: dict[str, dict] = {
    TTOKDAE: {
        "label": "떡대",
        "kind": PHYSIQUE,
        "lookEn": (("build", TTOKDAE_BUILD),),
        "conflicts": _TTOKDAE_CONFLICTS,
    },
}


def is_term(term_id) -> bool:
    return isinstance(term_id, str) and term_id in TERMS


def names() -> dict[str, str]:
    """설명에서 찾을 이름(label) → id."""
    return {item["label"]: term_id for term_id, item in TERMS.items()}


def known_terms(value) -> list[str]:
    """용어 id 하나(글) 또는 id 목록(list·tuple) → 아는 id 만, 순서대로 중복 없이. 그 밖의 값은 []."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    for item in items:
        if is_term(item) and item not in out:
            out.append(item)
    return out


def term_pieces(term_ids) -> list[tuple[str, str]]:
    """용어들의 (분류, 영어 조각)을 표 순서대로, 같은 조각은 한 번만."""
    out: list[tuple[str, str]] = []
    for term_id in known_terms(term_ids):
        for pair in TERMS[term_id]["lookEn"]:
            if pair not in out:
                out.append(pair)
    return out


def physique_of(term_ids) -> str:
    """몸 용어 id 를 "+" 로 이은 값(로그 physique=, 구도 바꾸기 판단). 없으면 ""."""
    return "+".join(term_id for term_id in known_terms(term_ids) if TERMS[term_id]["kind"] == PHYSIQUE)


# --------------------------------------------------------------------------- 부딪히는 체격 낱말 빼기(design D5)
#
# 캐스팅 문장을 만들 때만 쓴다(원본 외모 문장은 그대로). 순서:
#   1. 낱말 찾기: 부딪히는 낱말 뒤 같은 명사구(세 낱말 안, _PHRASE_STOP 을 넘지 않음)에 사람·몸 명사가 오면 그 낱말이다.
#   2. 낱말 빼기: 뒤에 ", "·" and "·" yet "·" but " 가 오면 낱말과 그 나눔을("slender, tall" → "tall"). 앞이 ", "·" and " 이고 그 앞이
#      같은 명사구의 꾸밈말뿐이면(관사 + 명사·전치사가 아닌 낱말) 앞 나눔과 낱말을("a tall, slender Korean" → "a tall Korean",
#      "a tall and slim build" → "a tall build"). 아니면 낱말과 뒤 빈칸을 뺀다("a slim Korean man" → "a Korean man",
#      "short hair and narrow shoulders" → "short hair and shoulders" → 4 에서 정리).
#   3. 뺀 자리 앞 관사 a/an 을 다음 낱말에 맞춘다("a lean, athletic build" → "an athletic build").
#   4. 뺀 자리에 꾸밈말 없는 몸 명사구만 남으면("with a build"·"and shoulders"·"of build") 그 목록 항목을 나눔째 뺀다.
_PERSON_HEADS = r"man|men|woman|women|person|people|guy|guys|boy|girl|gentleman|lady"
_BODY_NOUNS = r"build|frame|figure|physique|body|stature|shoulders|torso|chest|arms|neck|limbs|silhouette"
_BODY_HEADS = _PERSON_HEADS + "|" + _BODY_NOUNS
_PHRASE_STOP = (
    r"a|an|the|with|without|of|on|in|at|over|under|beneath|below|above|behind|across|near|around|from|to|into|by|for|his|her|"
    r"their|its|my|or|while|whereas|although|though|is|are|was|were|has|have|wearing|wears|who|that|which|face|faces|features|"
    r"visage|nose|lips|mouth|eyes?|eyebrows|brows?|jaw|jawline|chin|cheeks?|cheekbones|forehead|ears?|teeth|smile|skin|"
    r"complexion|hair|bangs|fringe|beard|mustache|moustache|stubble|fingers|hands?|nails|wrists?|legs|waist|hips|glasses|"
    r"spectacles|rims?|lenses|shirt|jacket|coat|shoes|boots|pants|trousers|jeans|tie|belt|suit|sweater|hoodie|uniform|dress|"
    r"skirt|watch|necklace|scars?|moles?|tattoos?"
)
_SEP_AFTER = re.compile(r"\s*,\s*(?:and\s+)?|\s+(?:and|yet|but)\s+", re.IGNORECASE)
_SEP_BEFORE = re.compile(r"(?:\s*,\s*|\s+and\s+)$", re.IGNORECASE)
# 앞 나눔 앞의 조각(마지막 쉼표·with·and 뒤)이 꾸밈말뿐인지 볼 때, 이 낱말이 있으면 목록 항목이다("short hair and …").
_SEGMENT_CUT = re.compile(r"[,;.]|\b(?:with|and|wearing|but|yet)\b", re.IGNORECASE)
_NOT_MODIFIER = re.compile(rf"\b(?:(?!(?:a|an|the)\b)(?:{_PHRASE_STOP})|{_BODY_HEADS})\b", re.IGNORECASE)
_ARTICLE_END = re.compile(r"\b(a|an)\s+$", re.IGNORECASE)
_OWNER_END = re.compile(r"\b(?:a|an|his|her|their)\s+$", re.IGNORECASE)
_BARE_BODY = re.compile(rf"(?:{_BODY_NOUNS})\b(?=\s*(?:[,;.]|$)|\s+(?:and|with)\b)", re.IGNORECASE)
_ITEM_LEAD = re.compile(r"(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s+with\s+|\s+of\s+)$|^$", re.IGNORECASE)
_ITEM_TAIL = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+", re.IGNORECASE)


def _conflict_re(words) -> re.Pattern:
    # 앞에 붙임표 머리말이 붙은 꼴("ultra-slim"·"extra-thin")도 한 낱말로 뺀다. 뒤에 붙임표가 붙은 꼴("slim-fit")은 다른 낱말이다.
    alts = "|".join(re.escape(word).replace(r"\ ", r"[\s-]+") for word in sorted(set(words), key=len, reverse=True))
    return re.compile(
        rf"(?<![\w-])(?:[a-z]+-)?(?:{alts})(?![\w-])"
        rf"(?=(?:,?\s+(?:and\s+)?(?!(?:{_PHRASE_STOP})\b)[\w'-]+){{0,3}}?,?\s+(?:and\s+)?(?:{_BODY_HEADS})\b)",
        re.IGNORECASE,
    )


_CONFLICT_RE: dict[str, re.Pattern] = {term_id: _conflict_re(item["conflicts"]) for term_id, item in TERMS.items()
                                       if item.get("conflicts")}


def _fix_article(text: str, at: int) -> str:
    """text[:at] 끝의 관사 a/an 을 text[at:] 첫 글자에 맞춘다(모음 a·e·i·o 앞 an)."""
    found = _ARTICLE_END.search(text, 0, at)
    nxt = text[at:at + 1]
    if not found or not nxt.isalpha():
        return text
    word = found.group(1)
    want = "an" if nxt.lower() in "aeio" else "a"
    if word.lower() == want:
        return text
    want = want.capitalize() if word[0].isupper() else want
    return text[:found.start(1)] + want + text[found.end(1):]


def _drop_bare(text: str, at: int) -> str:
    """text[at:] 가 꾸밈말 없는 몸 명사구로 시작하는 목록 항목이면(앞 관사·소유격 포함) 그 항목을 나눔째 뺀다."""
    noun = _BARE_BODY.match(text, at)
    if not noun:
        return text
    owner = _OWNER_END.search(text, 0, at)
    start = owner.start() if owner else at
    lead = _ITEM_LEAD.search(text, 0, start)
    if not lead:
        return text
    tail = _ITEM_TAIL.match(text, noun.end())
    if lead.group(0).strip().lower() == "with" and tail:
        return text[:start] + text[tail.end():]  # "with a build, short hair" → "with short hair"
    if lead.group(0):
        return text[:lead.start()] + text[noun.end():]  # ", a build" · " and shoulders" · " with a build" · " of build" 를 뺀다
    return text[tail.end():] if tail else text[noun.end():]  # 글 처음의 항목


def _modifiers_only(text: str, end: int) -> bool:
    """text[:end] 의 마지막 조각(쉼표·with·and 뒤)이 비지 않았고 관사와 꾸밈말뿐인가("a tall" 예, "short hair"·"in his 30s" 아니오)."""
    cuts = [found.end() for found in _SEGMENT_CUT.finditer(text, 0, end)]
    segment = text[(cuts[-1] if cuts else 0):end]
    return bool(segment.strip()) and not _NOT_MODIFIER.search(segment)


def strip_conflicts(look_en, term_ids) -> str:
    """외모 문장에서 몸 용어(term_ids)와 부딪히는 체격 낱말(구)을 뺀다. 몸 용어가 없거나 맞는 낱말이 없으면 글 그대로."""
    text = look_en if isinstance(look_en, str) else ""
    patterns = [_CONFLICT_RE[term_id] for term_id in known_terms(term_ids) if term_id in _CONFLICT_RE]
    for pattern in patterns:
        while True:
            found = pattern.search(text)
            if not found:
                break
            start, end = found.span()
            after = _SEP_AFTER.match(text, end)
            before = _SEP_BEFORE.search(text, 0, start)
            if after:
                text, at = text[:start] + text[after.end():], start
            elif before and _modifiers_only(text, before.start()):
                text, at = text[:before.start()] + text[end:], before.start()
            else:
                text, at = text[:start] + text[end:].lstrip(" "), start
            text = _fix_article(text, at)
            text = _drop_bare(text, at)
    return text
