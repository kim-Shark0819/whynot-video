"""BL 장르 역할·유형 → 실사 캐스팅 외모 참고 조각(지시서 103, 2026-10-31).

## 이 모듈이 하는 일

실사 캐스팅 사진 문장(casting.cast_prompt)에 더할 **참고** 외모 조각을 준다. 사용자 단서(2026-10-31): "참고만 하는 것이다.
외모 프롬프트를 고도화하고 정보를 많이 주기 위함이다." 그래서

- 그림·사진에서 읽은 사실(외모 문장)이 늘 이긴다. 다만 조각을 빼는 것은 그림이 보여 주는 **형태** 사실과 부딪칠 때뿐이다
  (103 피드백, Claude 결정 2026-10-31 — 목적이 "정보를 많이 주기"라서).
  - 형태 분류(체격·키·얼굴 모양·눈 모양): 외모 문장에 같은 분류가 있으면 그 조각은 내지 않는다(hint_pieces).
  - 인상 분류(얼굴 인상·눈빛·표정·자세·몸가짐): 늘 더한다. look_sentence 가 표정 조각을 이미 지우므로 그림 사실과 부딪치지 않는다.
- 머리카락·옷·나이·피부색 조각은 두지 않는다(그림이 정한다).

이 모듈은 순수 함수와 표만 갖는다. 아무것도 저장하지 않고 외부 호출이 없다.

## 옛 지식에서 옮긴 것

옛 저장소(이 저장소 밖 `C:/dev/ai/AI-POC-whynot`, 읽기만)의 BL 장르 모듈 TAGS 10종(id·label·desc 그대로)과 옛 steering
`genre-bl.md` 규칙을 옮겼다. 떡대공(ttokdae_gong)·무심공(musim_gong)은 이번에 더했다. 옛 모듈의 성격·연기·관계 단계 표는
캐스팅 사진에 쓰지 않아 옮기지 않았다.

## 영어 조각 규칙(옛 steering)

- 이미지 모델은 팬덤 은어(공/수·seme/uke·top/bottom·BL)를 모른다. 관찰할 수 있는 외모 말로만 쓴다.
- 두 인물 모두 성인 남성이다. '수' 유형은 여성 체형으로 흐르는 경향이 있어 masculine·adult man 계열 낱말을 한 조각 이상 넣는다.
- 15세 등급: 성적·노출 표현과 폭력 묘사를 쓰지 않는다(광공도 "imposing presence" 정도까지). 미성년으로 읽히는 낱말도 쓰지 않는다.
- 한 유형의 조각을 다 이으면 350자 이하(LOOK_EN_MAX).
- 조각마다 관사를 넣어 "He has …" 뒤에 바로 읽히게 했다(design D5).
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- 역할
ROLE_NONE = ""
ROLE_TOP = "top"  # 공(攻)
ROLE_BOTTOM = "bottom"  # 수(受)
ROLE_REVERSIBLE = "reversible"  # 리버서블 - 유형은 공·수 어느 쪽이든 고를 수 있다
ROLES = (ROLE_NONE, ROLE_TOP, ROLE_BOTTOM, ROLE_REVERSIBLE)
_ROLE_LABEL_KO = {ROLE_NONE: "없음", ROLE_TOP: "공", ROLE_BOTTOM: "수", ROLE_REVERSIBLE: "리버서블"}

# --------------------------------------------------------------------------- 분류(103 피드백, Claude 결정 2026-10-31)
#
# 형태: 그림이 실제로 보여 주는 모양 사실(체격·키·얼굴 모양·눈 모양). 외모 문장에 같은 분류가 있으면 그 조각을 뺀다(그림 우선).
# 인상: 얼굴 인상·눈빛·표정·자세·몸가짐. 그림 사실과 부딪치지 않으므로(look_sentence 는 표정 조각을 이미 지운다) 늘 더한다.
FORM_CATEGORIES = ("build", "height", "faceShape", "eyeShape")
IMPRESSION_CATEGORIES = ("faceImpression", "gaze", "expression", "posture", "grooming")
CATEGORIES = FORM_CATEGORIES + IMPRESSION_CATEGORIES
LOOK_EN_MAX = 350  # 한 유형의 조각을 ", " 로 이은 길이 상한

# --------------------------------------------------------------------------- 유형 표
#
# lookEn: (분류, 영어 조각). 순서가 중요하다 — 캐스팅 문장이 1500자를 넘으면 끝에서부터 뺀다(체격·키를 가장 오래 남긴다).
# label·desc 는 화면 문구다(옛 TAGS 10종은 글자 그대로). 영어 조각만 모델에 간다.
# 형태 조각(build·height·faceShape·eyeShape)은 모양 사실만, 인상 조각은 모양 사실 없이 쓴다(bl-a 가 둘 다 본다). 한 조각에
# 모양과 인상이 섞이면 둘로 나눈다(103 피드백: "steady, deep-set eyes" → "deep-set eyes" + "a steady gaze").
TYPES: dict[str, dict] = {
    # ---- 공 ----
    "gwanggong": {
        "role": ROLE_TOP,
        "label": "광공 / 집착공",
        "desc": "집착 강함, 거칠고 폭력적 면모, 큰 신장·체격",
        "lookEn": (
            ("height", "a tall frame"),
            ("build", "a broad-shouldered, powerful build"),
            ("faceShape", "sharp features"),
            ("faceImpression", "striking good looks"),
            ("gaze", "an intense, unwavering gaze"),
            ("posture", "an upright stance with an imposing presence"),
        ),
    },
    "tsundere_gong": {
        "role": ROLE_TOP,
        "label": "츤데레공",
        "desc": "겉은 차갑고 속은 다정",
        "lookEn": (
            ("faceShape", "well-defined features"),
            ("faceImpression", "a cool, reserved look"),
            ("eyeShape", "sharp eyes"),
            ("gaze", "a slightly averted gaze"),
            ("expression", "an aloof expression with a hint of softness"),
            ("posture", "a relaxed yet guarded posture"),
        ),
    },
    "dajeong_gong": {
        "role": ROLE_TOP,
        "label": "다정공",
        "desc": "따뜻하고 배려",
        "lookEn": (
            ("faceImpression", "warm, kind features"),
            ("gaze", "soft, attentive eyes"),
            ("expression", "a gentle, warm expression"),
            ("posture", "a relaxed, open posture"),
            ("grooming", "neat, clean grooming"),
        ),
    },
    "ttokdae_gong": {
        "role": ROLE_TOP,
        "label": "떡대공",
        "desc": "큰 키와 넓은 어깨, 건장한 체격의 공",
        "lookEn": (
            ("build", "broad shoulders and a solid, muscular build"),
            ("height", "a tall frame"),
            ("faceShape", "a firm square jawline"),
            ("faceImpression", "a rugged, masculine look"),
            ("eyeShape", "deep-set eyes"),
            ("gaze", "a steady gaze"),
            ("expression", "a calm, confident expression"),
            ("posture", "an upright, imposing posture"),
        ),
    },
    "musim_gong": {
        "role": ROLE_TOP,
        "label": "무심공",
        "desc": "감정 표현이 적고 무심한 태도의 공",
        "lookEn": (
            ("build", "a lean, tall build"),
            ("faceShape", "sharply defined features"),
            ("faceImpression", "a cool, detached air"),
            ("eyeShape", "half-lidded eyes"),
            ("gaze", "an indifferent gaze"),
            ("expression", "a calm, unreadable face"),
            ("grooming", "neat, minimal grooming"),
        ),
    },
    # ---- 수 ----
    "minyu_su": {
        "role": ROLE_BOTTOM,
        "label": "미인수",
        "desc": "아름다운 외모의 수",
        "lookEn": (
            ("build", "a slim yet clearly masculine adult build"),
            ("faceImpression", "strikingly handsome, delicate and refined masculine features"),
            ("faceImpression", "a clean, clear complexion"),
            ("eyeShape", "long lashes and clear eyes"),
            ("expression", "a gentle, composed expression"),
        ),
    },
    "kkotsu": {
        "role": ROLE_BOTTOM,
        "label": "꽃수",
        "desc": "여성적으로 예쁘고 작은 체격",
        "lookEn": (
            ("build", "a slender, lightly built adult man's frame"),
            ("height", "a modest height"),
            ("faceImpression", "soft, delicate features on a clearly masculine face"),
            ("eyeShape", "large eyes"),
            ("gaze", "a clear gaze"),
            ("expression", "a shy, gentle expression"),
            ("posture", "a slightly reserved posture"),
        ),
    },
    "ttokdaesu": {
        "role": ROLE_BOTTOM,
        "label": "떡대수",
        "desc": "큰 체격의 수",
        "lookEn": (
            ("build", "broad shoulders and a sturdy, muscular adult man's build"),
            ("height", "a tall frame"),
            ("faceShape", "strong features"),
            ("faceImpression", "a good-natured, masculine look"),
            ("gaze", "calm, gentle eyes"),
            ("expression", "a soft, slightly shy expression"),
            ("posture", "a relaxed, unassuming posture"),
        ),
    },
    "musim_su": {
        "role": ROLE_BOTTOM,
        "label": "무심수",
        "desc": "감정 표현이 적음",
        "lookEn": (
            ("faceImpression", "calm, clean-cut masculine features"),
            ("gaze", "a quiet, indifferent gaze"),
            ("expression", "a calm, unreadable expression"),
            ("posture", "a still, composed posture"),
            ("grooming", "neat, understated grooming"),
        ),
    },
    "neungryeok_su": {
        "role": ROLE_BOTTOM,
        "label": "능력수",
        "desc": "능력이 뛰어나 스스로 해결",
        "lookEn": (
            ("faceShape", "sharp features"),
            ("faceImpression", "an intelligent, masculine look"),
            ("gaze", "focused, confident eyes"),
            ("expression", "a composed, self-assured expression"),
            ("posture", "an upright, confident posture"),
            ("grooming", "a polished, well-groomed appearance"),
        ),
    },
    "gullim_su": {
        "role": ROLE_BOTTOM,
        "label": "굴림수",
        "desc": "환경이나 공 때문에 어려운 상황",
        "lookEn": (
            ("build", "a lean adult man's build"),
            ("faceImpression", "a tired yet handsome masculine face"),
            ("gaze", "weary, guarded eyes"),
            ("expression", "a subdued, patient expression"),
            ("posture", "a slightly withdrawn posture"),
        ),
    },
    "yeonha_su": {
        "role": ROLE_BOTTOM,
        "label": "연하수",
        "desc": "공보다 연하",
        "lookEn": (
            ("faceImpression", "fresh, bright features on a clearly adult male face"),
            ("gaze", "bright, lively eyes"),
            ("expression", "an earnest, open expression"),
            ("posture", "an energetic, open posture"),
            ("grooming", "clean, natural grooming"),
        ),
    },
}

# --------------------------------------------------------------------------- 형태 판정 낱말 표(design §3)
#
# 형태 분류만 판정한다(인상 분류는 늘 더하므로 판정하지 않는다).
# 체격·키 낱말 바로 뒤(쉼표 전, 세 낱말 안)에 얼굴·머리·피부·손·옷 명사가 오면 그 낱말은 체격·키가 아니다(design D4:
# "thin lips"·"a slender angular face"·"warm-toned skin"·"tall boots"·"thin black-rimmed glasses").
_NOT_BODY_NOUNS = (
    r"face|faces|nose|lips|mouth|eyes?|eyebrows|brows?|jaw|jawline|chin|cheeks?|cheekbones|forehead|ears?|teeth|smile|grin|"
    r"lines|wrinkles|eyelids?|lashes|eyelashes|skin|complexion|hair|bangs|fringe|ponytail|braids?|beard|mustache|moustache|"
    r"stubble|fingers|hands|nails|glasses|spectacles|rims?|lenses|shirt|jacket|coat|shoes|sneakers|boots|heels|pants|"
    r"trousers|jeans|shorts|socks|hat|cap|collar|lapels?|sleeves?|tie|belt|bag|scarf|dress|skirt|watch|strap|straps|chain|"
    r"necklace|stripes?|cuffs?|hood|hoodie|sweater|cardigan|vest|uniform|suit"
)
# 명사구를 잇거나 끊는 낱말. 아래 두 낱말 창(_NOT_BODY·_before)은 이 낱말을 넘지 않는다("a slim build and narrow eyes" 의 slim·build 는
# 체격이다 — 103 피드백 중 찾은 결함).
_JOIN_STOP = (
    r"a|an|the|and|or|with|without|of|on|in|at|over|under|beneath|below|above|behind|across|his|her|their|its|is|are|has|"
    r"have|wearing"
)
_NOT_BODY = rf"(?![\s-]+(?:(?!(?:{_JOIN_STOP})\b)[\w']+[\s-]+){{0,3}}?(?:{_NOT_BODY_NOUNS})\b)"
_BUILD_WORDS = (
    r"slim|slender|lean|broad|muscular|stocky|petite|burly|athletic|chubby|thin|sturdy|skinny|plump|wiry|lanky|brawny|"
    r"bulky|heavyset|toned|build|built|physique|figure|well-built|broad-shouldered"
)
_HEIGHT_WORDS = r"tall|taller|tallest|towering|petite|lanky|height|stature|statuesque"
# "very short hair"·"short crew cut" 의 short 는 키가 아니다.
_SHORT_NOT = r"hair|bangs|fringe|crop|cut|haircut|buzz|crew|undercut|bob|ponytail|sleeves?|skirt|shorts|beard|stubble|nails|neck"
# 얼굴 모양·눈 모양 낱말은 같은 명사구 안(세 낱말 안, 관사·전치사·다른 명사를 넘지 않음)에서 얼굴·눈 명사 앞에 올 때만 형태다
# ("long hair, a calm face" 의 long·"a slim build and narrow eyes" 의 slim 은 아님).
_PHRASE_STOP = (
    _JOIN_STOP + r"|man|woman|person|guy|build|frame|figure|body|physique|stature|shoulders?|neck|limbs|arms?|legs?|"
    + _NOT_BODY_NOUNS
)


def _before(adjs: str, nouns: str) -> str:
    """모양 낱말(adjs) 뒤 세 낱말 안에 명사(nouns)가 오는 같은 명사구. 사이 낱말은 _PHRASE_STOP 이 아니어야 한다."""
    return (rf"\b(?:{adjs})\b(?:,?\s+(?:and\s+)?(?!(?:{_PHRASE_STOP})\b)[\w'-]+){{0,3}}?"
            rf",?\s+(?:and\s+)?(?:{nouns})\b")


_FACE_SHAPE_WORDS = (
    r"angular|oval|round|rounded|roundish|square|squarish|squared|long|narrow|slender|thin|slim|wide|broad|small|"
    r"heart-shaped|v-line|v-shaped|diamond-shaped|oblong|chiseled|chiselled|sculpted|defined|well-defined|sharp|sharply|"
    r"strong|bony|gaunt|chubby|plump"
)
_EYE_SHAPE_WORDS = (
    r"narrow|almond|almond-shaped|round|rounded|large|big|small|wide|wide-set|close-set|deep-set|hooded|monolid|"
    r"monolidded|single-lidded|double-lidded|upturned|downturned|droopy|drooping|slanted|slanting|sharp|elongated|doe|"
    r"cat-like|catlike|fox-like|foxlike|crescent|phoenix|sleepy|half-lidded|heavy-lidded"
)
_CATEGORY_RE: dict[str, re.Pattern] = {
    "build": re.compile(
        rf"\b(?:{_BUILD_WORDS})\b{_NOT_BODY}"
        r"|\b(?:broad|narrow|wide|sloping|square|strong|small|big|large|rounded)[\s-]+shoulder(?:s|ed)\b",
        re.IGNORECASE,
    ),
    "height": re.compile(
        rf"\b(?:{_HEIGHT_WORDS})\b{_NOT_BODY}"
        # "short" 는 키 명사가 뒤따르거나("a short Korean man", "a short, slim man", "short stature") "is short" 일 때만 키다
        # ("short black hair"·"short hair, a man" 아님).
        r"|\bshort(?:er)?(?=,?\s+(?:and\s+)?(?:[\w-]+\s+){0,2}?(?:man|woman|person|guy|boy|girl|stature|build|frame|figure|"
        r"body|height)\b)"
        rf"|(?<!hair )(?<!bangs )\b(?:is|are|looks?|quite|rather|very|fairly|slightly)\s+short\b"
        rf"(?![\s-]+(?:[\w-]+\s+){{0,2}}?(?:{_SHORT_NOT})\b)",
        re.IGNORECASE,
    ),
    # 얼굴 모양: 턱·턱선·광대·얼굴형("an angular face", "a soft oval face", "round-faced", "sharp features"). 코·입술·눈썹은
    # 얼굴 모양이 아니라 따로 그린 부위라 보지 않는다(유형 조각이 그 부위를 말하지 않는다). "chin-length hair" 는 머리 길이다.
    "faceShape": re.compile(
        r"\b(?:jaws?|jawlines?|jawed|chins?|cheekbones?)\b(?!-length)"
        rf"|\b(?:{_FACE_SHAPE_WORDS})-(?:faced|featured)\b"
        r"|\b(?:face|facial|bone)\s+(?:shape|structure)\b"
        rf"|{_before(_FACE_SHAPE_WORDS, r'faces?|features|visage')}",
        re.IGNORECASE,
    ),
    # 눈 모양: 눈꺼풀·속눈썹·눈 모양 낱말("narrow eyes", "large doe eyes", "deep-set eyes", "almond-eyed"). 눈빛("piercing eyes",
    # "calm, gentle eyes")은 인상이라 보지 않는다. "hooded" 는 옷(hooded jacket)에도 쓰여 eyes 앞에서만 본다.
    "eyeShape": re.compile(
        r"\b(?:eyelids?|eyelashes|lashes|lids|lidded|monolids?|monolidded)\b"
        r"|\b(?:wide|doe|almond|round|big|large|narrow|cat|fox|hooded|slant|deep|sharp|sleepy)-eyed\b"
        rf"|{_before(_EYE_SHAPE_WORDS, r'eyes?')}",
        re.IGNORECASE,
    ),
}


def roles() -> list[dict]:
    """화면 역할 고르기 목록(없음·공·수·리버서블)."""
    return [{"key": role, "label": _ROLE_LABEL_KO[role]} for role in ROLES]


def is_type(bl_type) -> bool:
    return isinstance(bl_type, str) and bl_type in TYPES


def fits(bl_type, role) -> bool:
    """유형이 역할과 맞는가. 유형 "" 는 늘 맞고, 리버서블은 모든 유형을 받는다. 역할이 없음("")이나 모르는 값이면 유형을 둘 수 없다."""
    if bl_type == "":
        return True
    return is_type(bl_type) and (role == ROLE_REVERSIBLE or TYPES[bl_type]["role"] == role)


def _joined(bl_type: str) -> str:
    return ", ".join(text for _cat, text in TYPES[bl_type]["lookEn"])


def list_types() -> list[dict]:
    """화면용 유형 목록. lookEn 은 조각을 ", " 로 이은 미리보기다(외모 문장으로 빼기 전)."""
    return [
        {"id": type_id, "role": item["role"], "label": item["label"], "desc": item["desc"], "lookEn": _joined(type_id)}
        for type_id, item in TYPES.items()
    ]


def look_categories(look_en) -> set[str]:
    """외모 문장에 이미 있는 형태 분류(FORM_CATEGORIES 중, design §3 낱말 표). 인상 분류는 판정하지 않는다."""
    text = look_en if isinstance(look_en, str) else ""
    return {cat for cat in FORM_CATEGORIES if _CATEGORY_RE[cat].search(text)}


def hint_pieces(bl_type, look_en) -> list[str]:
    """그 유형의 조각(표 순서). 인상 조각은 늘 넣고, 형태 조각은 외모 문장에 같은 형태 분류가 없을 때만 넣는다(그림 우선).

    형태 조각의 분류 = 단 분류 ∪ 조각 글이 스스로 담은 형태 분류(design D3: "a lean, tall build" 는 그림의 "short stature" 와
    부딪힌다). 모르는 유형이면 [].
    """
    if not is_type(bl_type):
        return []
    taken = look_categories(look_en)
    out = []
    for cat, text in TYPES[bl_type]["lookEn"]:
        if cat in IMPRESSION_CATEGORIES or not ({cat} | look_categories(text)) & taken:
            out.append(text)
    return out


def look_hints(bl_type, look_en) -> str:
    """hint_pieces 를 ", " 로 이은 영어. 모르는 유형이거나 남는 조각이 없으면 ""."""
    return ", ".join(hint_pieces(bl_type, look_en))
