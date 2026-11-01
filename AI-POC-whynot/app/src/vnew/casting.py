"""vnew 실사 캐스팅(vnew-live-fx 묶음 B, 2026-10-30) — 캐스팅 문장·성별(B1)과 뽑기·이어하기(B2).

캐릭터마다 실사 배우 사진을 **글만으로** 뽑는다(Seedream 4.5 text-to-image). 웹툰 그림은 모델에 보내지 않는다
(참고 구현 vpoc/live.py:27-32 근거: 그림 편집은 웹툰 선·회색 눈·그림 포즈를 남겼다).

  - 문장(B1): 참고 구현 vpoc/live.py 의 캐스팅 문장(design §B.2)을 옮기고, 남성 고정 버그를 성별 낱말 표로 고친다.
    male 이면 프리셋 문구가 참고 구현 원문과 글자까지 같다. 모든 결과에서 그림 낱말(webtoon 등)을 지운다.
  - 성별: 사용자 지정 → 분석 genderEn → 외모 문장 주어(man·boy → male, woman·girl → female) → unknown.
    이미 분석된 캐릭터(genderEn 없음)는 다시 분석하지 않고 genderEn 을 unknown 으로 본다.
  - 이 파일의 문장 함수는 순수 함수다(네트워크·S3·LLM 없음). 뽑기(B2)는 아래 절에 있다.
  - 뽑기(B2): 상태는 char.json 의 casting 에 둔다. fal 큐에 칸마다 1장씩 **글만** 제출하고(본문 정확히
    {prompt, num_images, image_size}), 결과를 우리 버킷 users/{owner}/characters/{id}/casting/ 에 JPEG 로 옮긴다.
    외부 호출은 _fal_request · _fetch_url 두 래퍼만 쓴다(검사가 가짜로 바꿔 끼운다).
  - BL 유형(지시서 103·104, VNEW_BL_LOOK): 사용자가 쓴 캐릭터 설명(char.extraPromptKo)에 유형 이름(떡대공 등)이 있으면 캐스팅 문장
    끝에 genre_bl 의 참고 외모 문장 하나를 더한다(104 — 화면 선택값 casting.blRole·blType 은 읽지 않는다). 외모 문장(그림 사실)에
    이미 있는 형태 분류는 빠진다. 300자 외모 문장과 영상 쪽은 바꾸지 않는다.
  - 실사 사진 지키기(지시서 107, 2026-11-01): 캐스팅 문장 맨 끝에 사진 고정 문장(photo_anchor)을 늘 붙인다. VNEW_CAST_PHOTO_CHECK
    켬이면 결과 사진마다 저장 전에 비전 LLM 1회(_photo_check)로 실사인지 묻고, 그림체면 그 사진을 버리고(S3 저장 없음, 원장 1건)
    같은 칸을 같은 문장으로 두 번까지 다시 제출한다(칸 redraws, 지시서 108). 두 번째로 다시 뽑은 사진도 그림체면 그 칸만 실패(NotPhoto).
  - 아이돌풍 모델(지시서 109, 2026-11-01): 아이돌풍(preset idol)은 VNEW_CASTING_IDOL_MODEL(기본 FLUX.2 [pro])로, 현실 배우풍은
    지금처럼 VNEW_CASTING_MODEL(Seedream)로 뽑는다(preset_choice). 본문은 두 모델 모두 같은 3키다. start_draw 가 고른 모델·장당
    금액·크기를 회차(casting.model·unitUsd·imageSize)에 남기고, 뽑기·다시 제출·이어 조회는 그 값을 쓴다(_round_choice).
  - 떡대 체격(지시서 112, 2026-11-01, VNEW_BL_LOOK 을 따른다): 캐릭터 설명에 떡대공·떡대수(genre_bl.TYPE_TERMS) 또는 "떡대"만
    (genre_bl.detect_terms) 있으면 web_terms 의 떡대 체격 조각을 참고 문장 맨 앞에 늘 넣고(그림 우선·1500자 줄이기에서 사진 고정 문장
    다음으로 마지막까지 남김), 외모 문장의 부딪히는 체격 낱말(slim·slender …)을 캐스팅 문장에서만 빼고(web_terms.strip_conflicts),
    프리셋 구도를 허리 위로 바꾼다(base_text physique — 아이돌풍 "slim V-line face" 뺌). 떡대가 없으면 문장은 112 전과 글자까지 같다.
    공·수는 포지션이라 외모를 더하지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from PIL import Image

from ..vpoc import fal as vpoc_fal
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso
from . import characters
from . import genre_bl
from . import settings
from . import web_terms
from .fx import clean_text, strip_drawing_words

log = logging.getLogger("vpoc")

# ================================================================ 프리셋 · 성별 낱말(design §B.2)

GENDERS = ("male", "female", "unknown")
# 성별 낱말 표. male 은 참고 구현 원문 낱말이다(그래서 male 결과는 원문과 같다).
_WORDS = {
    "male": {"idol": "male K-pop idol", "noun": "man", "pos": "his", "subj": "He is", "actor": "actor"},
    "female": {"idol": "female K-pop idol", "noun": "woman", "pos": "her", "subj": "She is", "actor": "actress"},
    "unknown": {"idol": "K-pop idol", "noun": "person", "pos": "their", "subj": "They are", "actor": "actor"},
}
# 참고 구현 vpoc/live.py:33-39 CAST_BASE(아이돌풍)·:83-89 CAST_BASE_REAL(현실 배우풍). 성별 낱말만 {…} 로 바꿨다.
_IDOL = ("Official profile photo of a {idol}, adult Korean {noun} in {pos} 20s, real photograph of a real person, "
         "photorealistic, slim V-line face, clear skin with natural skin texture (lightly retouched), subtle idol makeup, "
         "glossy styled hair, calm expression, facing the camera, chest-up framing, soft studio lighting, plain light "
         "background, full frame, no border, no illustration, no anime, not a painting, fully clothed. Clothing and its "
         "pattern exactly as described, including where the pattern is.")
_REAL = ("Unretouched photo of a Korean {actor} on a K-drama set, natural East Asian facial features, dark brown eyes, "
         "natural skin texture with pores and slight imperfections, no heavy makeup, soft natural light, calm neutral "
         "expression, facing the camera, chest-up framing, hands not visible, plain background, full frame, no border, "
         "no illustration, fully clothed, not a model photoshoot. Clothing and its pattern exactly as described, "
         "including where the pattern is.")
# 참고 구현 vpoc/live.py:90-99 PRESETS·DEFAULT_PRESET. 라벨은 화면 문구다.
PRESETS = (
    {"key": "idol", "label": "아이돌풍"},
    {"key": "real", "label": "현실 배우풍"},
)
DEFAULT_PRESET = "idol"
_BASES = {"idol": _IDOL, "real": _REAL}
# 지시서 112(design D4): 몸 용어(떡대)가 있으면 체격이 보이게 프리셋 문구 일부만 바꾼다(성별 낱말을 채우기 전 글에서 바꾼다). 떡대가 없으면
# 프리셋 문구는 그대로다. 구도는 어깨·위팔(이두·삼두)·가슴이 다 들어오는 허리 위. "upper arms" 라 현실 배우풍 "hands not visible" 과
# 부딪히지 않는다. 아이돌풍 "slim V-line face" 는 체격과 부딪혀 빼기만 한다(떡대는 얼굴을 정하지 않는다 — 얼굴은 외모 문장이 정한다).
# "fully clothed"·"Clothing and its pattern exactly as described" 는 그대로 둔다(옷 맞음새를 덧붙이면 설명한 옷과 부딪힐 수 있다).
PHYSIQUE_FRAMING = "waist-up framing with the shoulders, upper arms and chest fully in frame"
_PHYSIQUE_SWAPS = {
    "idol": (("slim V-line face, ", ""), ("chest-up framing", PHYSIQUE_FRAMING)),
    "real": (("chest-up framing", PHYSIQUE_FRAMING),),
}
# 외모 문장 상한(참고 구현 vpoc/live_service.py:294 LOOK_MAX, design §B.1 "≤300").
LOOK_MAX = 300
# 이번 회차 실제 문장 상한(design §B.1 prompt ≤1500).
PROMPT_MAX = 1500
# 사진 고정 문장(지시서 107, cast-photo-guard design D1). 두 프리셋 모두 캐스팅 문장 맨 끝에 붙인다(VNEW_CAST_PHOTO_CHECK 와 상관없이).
# 문장 앞부분에만 있던 사진 단서를 긴 외모·BL 문장 뒤에 한 번 더 둔다. 1500자를 넘으면 참고 문장 → 외모 순으로 줄이고 이 문장은
# 자르지 않는다(_cast_text). "webtoon"·"manga" 는 넣지 않는다 — 캐스팅 문장에 그림 낱말 webtoon 을 남기지 않는 B1 규칙(cast-a)이고,
# 금지하려는 그림체 이름을 끝에 두면 오히려 그쪽으로 끌 수 있다. "painting" 은 아이돌풍 프리셋의 "not a painting" 과 같은 낱말이다.
# {noun} 은 성별 낱말 표의 명사(man·woman·person)다 — 문장 끝에 성 중립 "person" 이 와서 BL 남성 인물이 흐려지지 않게(steering genre-bl).
PHOTO_ANCHOR = (
    "A real photograph of a real {noun} taken with a camera; not a drawing, illustration, anime, cartoon, painting or 3D render."
)


def preset_of(value) -> str:
    """프리셋 키를 정리한다. 모르는 값은 기본(idol)."""
    key = str(value or "").strip().lower()
    return key if key in _BASES else DEFAULT_PRESET


def normalize_gender(value) -> str:
    """분석·저장값 성별을 male | female | unknown 으로 정리한다(모르는 값 unknown)."""
    key = str(value or "").strip().lower() if isinstance(value, str) else ""
    return key if key in ("male", "female") else "unknown"


def _gender(value) -> str:
    key = str(value or "").strip().lower() if isinstance(value, str) else ""
    return key if key in GENDERS else "unknown"


def base_text(preset, gender, physique="") -> str:
    """프리셋 문구(성별 낱말 표 적용). male 이면 참고 구현 원문과 같다.

    physique(몸 용어 id, 지시서 112 — 예 "ttokdae")가 있으면 그 프리셋의 _PHYSIQUE_SWAPS 를 적용한다(허리 위 구도, 아이돌풍 V라인 뺌).
    """
    key = preset_of(preset)
    template = _BASES[key]
    if physique:
        for old, new in _PHYSIQUE_SWAPS[key]:
            template = template.replace(old, new)
    return template.format(**_WORDS[_gender(gender)])


def photo_anchor(gender) -> str:
    """사진 고정 문장(지시서 107). 명사만 성별 낱말 표를 따른다(male man · female woman · 그 밖 person)."""
    return PHOTO_ANCHOR.format(noun=_WORDS[_gender(gender)]["noun"])


# ================================================================ 성별 정하기

# 참고 구현 vpoc/live.py:41-46 _SUBJECT 자리(원문은 이 저장소에 없어 같은 뜻으로 새로 썼다 — 결과 보고 "남은 이슈").
_SUBJECT = re.compile(r"\b(man|woman|boy|girl)\b", re.IGNORECASE)
_SUBJECT_GENDER = {"man": "male", "boy": "male", "woman": "female", "girl": "female"}


def auto_gender(analysis) -> str:
    """사용자 지정 없이 정한 성별: 분석 genderEn → 외모 문장 주어 → unknown. genderEn 이 없거나 unknown 이면 다음 단계로 간다."""
    analysis = analysis if isinstance(analysis, dict) else {}
    gender = normalize_gender(analysis.get("genderEn"))
    if gender != "unknown":
        return gender
    found = _SUBJECT.search(clean_text(analysis.get("appearanceEn"), 600))
    if found:
        return _SUBJECT_GENDER[found.group(1).lower()]
    return "unknown"


def resolve_gender(casting, analysis) -> str:
    """캐스팅 문장에 쓸 성별. 사용자 지정(male·female·unknown)이 있으면 그 값, 비면 auto_gender."""
    casting = casting if isinstance(casting, dict) else {}
    user = str(casting.get("gender") or "").strip().lower() if isinstance(casting.get("gender"), str) else ""
    if user in GENDERS:
        return user
    return auto_gender(analysis)


# ================================================================ 나이(참고 구현 vpoc/live.py:49-59 _AGE·age_phrase 자리)
#
# descriptionKo 의 나이 표현 → "in his 20s" 식. 캐스팅 프리셋이 "adult … in his 20s" 라 10대·학생은 성인
# "early 20s" 로 올린다(미성년 사진을 만들지 않는다). 못 찾으면 "".

_AGE_DECADE = re.compile(r"([1-9])0\s*대\s*(초반|중반|후반)?")
_AGE_YEARS = re.compile(r"(\d{1,2})\s*(?:살|세)")
_AGE_QUAL = {"초반": "early", "중반": "mid", "후반": "late"}
_AGE_WORDS = (
    (re.compile(r"고등학생|고교생|중학생|초등학생|소년|소녀|학생|십대"), "early", 20),
    (re.compile(r"청년|신입"), "", 20),
    (re.compile(r"장년"), "", 50),
    (re.compile(r"중년|아저씨|아주머니|아줌마"), "", 40),
    (re.compile(r"노년|노인|할아버지|할머니|어르신"), "", 60),
)


def _age_parts(desc_ko) -> tuple[str, int]:
    text = clean_text(desc_ko, 600)
    found = _AGE_DECADE.search(text)
    if found:
        decade = int(found.group(1)) * 10
        qual = _AGE_QUAL.get(found.group(2) or "", "")
        return ("early", 20) if decade < 20 else (qual, decade)
    found = _AGE_YEARS.search(text)
    if found:
        years = int(found.group(1))
        if years < 20:
            return "early", 20
        unit = years % 10
        return ("early" if unit <= 3 else "mid" if unit <= 6 else "late"), years // 10 * 10
    for pattern, qual, decade in _AGE_WORDS:
        if pattern.search(text):
            return qual, decade
    return "", 0


def age_phrase(desc_ko, gender) -> str:
    """descriptionKo 의 나이 → "in his 20s"·"in her late 30s"·"in their 40s". 없으면 ""."""
    qual, decade = _age_parts(desc_ko)
    if not decade:
        return ""
    pos = _WORDS[_gender(gender)]["pos"]
    return f"in {pos} {qual + ' ' if qual else ''}{decade}s"


# ================================================================ 외모 문장(참고 구현 vpoc/live.py:62-80 look_sentence 자리)

# 표정 조각(프리셋이 표정을 정한다). "frown lines"(주름)는 표정이 아니라 남긴다.
_EXPRESSION = re.compile(
    r"(?:,\s*|\s+(?:and|with)\s+)?(?:an?\s+)?(?:[\w-]+\s+){0,2}?"
    r"(?:expression|smile|smiling|grin|grinning|frown|frowning|scowl|scowling|pout|pouting|smirk|smirking|"
    r"blush|blushing|tears|crying|laughing)\b(?!\s+lines)(?:\s+on\s+(?:his|her|their)\s+face)?",
    re.IGNORECASE,
)
# 눈 색(웹툰의 회색·파란 눈). 캐스팅 문장 끝 "with dark brown eyes" 와 부딪히지 않게 색 낱말만 뺀다(2026-10-30 피드백:
# 색이 눈 모양 낱말과 섞여 있어도 — "light gray-blue almond-shaped eyes" → "almond-shaped eyes"). 합성 색(gray-blue·
# near-black·blue-gray)도 색이다. "dark eyes" 의 dark 는 톤 낱말이라 색이 뒤따를 때만 뺀다.
_COLOR = (
    r"(?:gr[ae]y|blue|green|red|golden|gold|amber|violet|purple|pink|silver|silvery|hazel|brown|black|teal|aqua|yellow|"
    r"orange|white|emerald|sapphire|crimson|scarlet|turquoise|cyan|jade|ruby|azure|cerulean|indigo|lavender|lilac|"
    r"magenta|copper|bronze|honey|chocolate|chestnut|coffee|steel|slate|ice|sky|sea|ocean|olive|mint|ash|charcoal|"
    r"ebony|onyx|obsidian|jet|ink|inky|coal)"
)
_TONE = r"(?:light|pale|bright|deep|dark|vivid|striking|icy|soft|rich|warm|cool|clear|cold|dull|muted|faded|near|almost|nearly|very)"
_COLOR_RE = rf"(?:{_TONE}-)?{_COLOR}(?:-?d?ish)?(?:-(?:{_TONE}-)?{_COLOR}(?:-?d?ish)?)*"
_COLOR_WORD = re.compile(rf"{_COLOR_RE}(?:-(?:colou?red|tinted|toned|hued))?", re.IGNORECASE)
_TONE_WORD = re.compile(_TONE, re.IGNORECASE)
_COLOR_SUFFIX_WORD = re.compile(r"(?:colou?red|tinted|toned|hued)", re.IGNORECASE)
_EYE_NOUN = re.compile(r"\b(?:eyes?|eyeballs?)\b", re.IGNORECASE)
# 눈 명사 앞 같은 명사구를 거꾸로 읽다 멈추는 낱말(접속·관사·소유격·전치사·다른 명사).
_EYE_STOP = frozenset({
    "with", "and", "or", "of", "has", "have", "a", "an", "the", "his", "her", "their", "its", "in", "on", "over", "under",
    "behind", "above", "below", "beneath", "hair", "glasses",
})
_EYES_OF_COLOR = re.compile(
    rf"\b(eyes?)\s+(?:of|in)\s+(?:an?\s+)?(?:{_TONE}\s+)*{_COLOR_RE}(?:\s+(?:colou?r|hue|shade|tone))?\b", re.IGNORECASE,
)
_COLOR_EYED = re.compile(rf"(?<![\w-])(?:{_TONE}[- ])*{_COLOR_RE}-eyed\b\s*", re.IGNORECASE)
# 눈동자(irises·pupils) 조각은 거의 색 설명이라 통째로 뺀다("… eyes with tiny near-black irises", "… and gray irises").
_IRIS = r"(?:irises|irides|iris|pupils?)"
_IRIS_WORD = re.compile(rf"\b{_IRIS}\b", re.IGNORECASE)
_IRIS_PIECE = re.compile(
    rf"(?:\s*,\s*|\s+)(?:with|and)\s+(?:(?!\b(?:with|and|or|eyes?)\b)[^,;.])*?\b{_IRIS}\b", re.IGNORECASE,
)
# 머리색 낱말(참고 구현 _HAIR 자리). 검은 머리가 아니면 "dyed" 를 붙인다.
_HAIR_COLORS = {
    "blonde", "blond", "silver", "white", "gray", "grey", "pink", "red", "orange", "brown", "auburn", "purple",
    "violet", "lavender", "blue", "green", "mint", "teal", "copper", "golden", "gold", "chestnut", "black", "ginger",
    "burgundy", "platinum", "ash", "ashy", "honey", "strawberry", "rose", "pastel", "light", "pale", "dark", "bright",
    "jet", "raven", "dirty", "deep", "colored", "coloured", "toned", "tinted",
}
_HAIR_BASES = {
    "blonde", "blond", "silver", "white", "gray", "grey", "pink", "red", "orange", "brown", "auburn", "purple",
    "violet", "lavender", "blue", "green", "mint", "teal", "copper", "golden", "gold", "chestnut", "black", "ginger",
    "burgundy", "platinum", "ash", "raven",
}
_OLD_WORDS = re.compile(r"\b(?:elderly|old|older|aged|graying|greying|salt-and-pepper|wrinkled)\b", re.IGNORECASE)
# 머리 앞 수식어로 보지 않는 낱말(주어 앞 형용사 정리용).
_PREFIX_DROP = re.compile(r"\b(?:a|an|the|one|korean|east\s+asian|asian)\b", re.IGNORECASE)
_PREFIX_AGE = re.compile(  # "young-looking"·"youthful-looking" 은 붙임표 꼬리까지 뺀다("a -looking Korean man" 이 되지 않게)
    r"\b(?:young|youthful|middle-aged|elderly|old|older|teenage|adult|aged|mature|\d{1,2}[- ]years?[- ]old)(?:-looking)?\b",
    re.IGNORECASE,
)
_PREFIX_GENDER = re.compile(r"\b(?:fe)?male\b", re.IGNORECASE)
# 글 속 나이("in his twenties"·"in her late 30s"). 2026-10-30 피드백: 낱말 나이(twenties …)도 본다.
_AGE_DECADE_WORDS = {
    "teens": 10, "twenties": 20, "thirties": 30, "forties": 40, "fifties": 50, "sixties": 60, "seventies": 70,
    "eighties": 80, "nineties": 90,
}
# 나이 앞 "appearing"·"looking"·"who appears to be" 도 나이와 함께 뺀다(2026-10-30 피드백: "An adult man appearing in his
# thirties has …" 에 appearing 이 남지 않게). "youthful-looking" 의 looking 은 붙임표 뒤라 남긴다.
_TEXT_AGE = re.compile(
    r"\s*\b(?:(?<![\w-])(?:who\s+(?:appears|looks|seems)|appearing|looking|seeming)(?:\s+to\s+be)?\s+)?"
    r"(?:aged\s+)?in\s+(?:his|her|their)\s+(?:(early|mid|late)[- ])?(\d0s|teens|twenties|thirties|forties|fifties|"
    r"sixties|seventies|eighties|nineties)\b",
    re.IGNORECASE,
)
# 나이 낱말(주어 앞이 아니어도). 나이(age_phrase)가 있으면 뺀다 — 결과에 나이 표현이 한 번만 남게.
_TEXT_AGE_WORDS = re.compile(
    r"\b(?:middle-aged|young[- ]adult|\d{1,2}[- ]years?[- ]old|(?:twenty|thirty|forty|fifty|sixty)-something)\b", re.IGNORECASE,
)
# 주어 명사(머리말 "a … Korean {명사} {나이}" 를 만들 자리). _SUBJECT(성별 정하기)보다 넓다 — person 등은 성별을 모른다.
_HEAD_NOUN = re.compile(r"\b(man|woman|boy|girl|person|guy|lady|gentleman)\b", re.IGNORECASE)
# 문장 첫머리 주어+동사("He is a …"·"She has …"·"They are …"·"This man is …"). has·have 는 with 로 바꾸고 나머지는 지운다.
_LEAD_SUBJECT = re.compile(
    r"(^|[.;!?]\s+)(?:(?:he|she|they)\s+(is|are|was|were|has|have)|he's|she's|they're|this\s+(?:man|woman|person)\s+(is|has))\s+",
    re.IGNORECASE,
)
# 문장 첫머리 옷 동사("He wears"·"She is wearing"·"They are dressed in"). 외모 문장에서는 "wearing", 기본 복장에서는 지운다.
_LEAD_WEAR = re.compile(
    r"(^|[.;!?]\s+)(?:(?:he|she|they)\s+(?:wears?|is\s+wearing|are\s+wearing|is\s+dressed\s+in|are\s+dressed\s+in)|"
    r"(?:he's|she's|they're)\s+(?:wearing|dressed\s+in))\s+",
    re.IGNORECASE,
)
_OUTFIT_VERB = re.compile(r"^(?:wears?|wearing|dressed\s+in)\s+", re.IGNORECASE)
# 주어 뒤 소유 동사("A … man in his twenties has fair skin" → "… with fair skin").
_HAS = re.compile(r"\s+(?:(?:who|that|which)\s+)?(?:has|have)(?:\s+got)?\s+", re.IGNORECASE)
# 기본 복장의 보이지 않는다는 꼬리("while his shoes are not visible")·부정 꼬리("no visible jewelry").
_NOT_SHOWN = re.compile(
    r"\b(?:not|never)\s+(?:visible|shown|seen|pictured|in\s+(?:view|frame))\b|\bout\s+of\s+(?:frame|view|shot)\b|"
    r"\b(?:no|none|without)\s+visible\b|\bcropped\s+out\b",
    re.IGNORECASE,
)
# 조각 빼기용 나눔(쉼표·세미콜론·문장 끝·"while/but …" 꼬리 앞). 나눔 글자는 남겨 다시 잇는다.
_PIECE_SEP = re.compile(r"(\s*[,;]\s*|\.\s+|\s+(?=(?:while|whereas|but|though|although)\s))", re.IGNORECASE)
# 300자 줄이기(2026-10-30 피드백): 쉼표·and 로 나눈 조각 단위로, 덜 중요한 조각부터 뺀다.
_LIST_SEP = r"\s*[,;]\s*(?:and\s+)?|\s+and\s+(?=(?:an?|the|his|her|their)\s)"
_LOOK_SPLIT = re.compile(rf"({_LIST_SEP})", re.IGNORECASE)
_OUTFIT_SPLIT = re.compile(rf"({_LIST_SEP}|\s+(?=with\s))", re.IGNORECASE)  # 옷은 "… suit with matching trousers" 도 나눈다
# 목록 항목이 아니라 덧붙는 조각(with·without·wearing·"tucked into …" 같은 분사구).
_CLAUSE_HEAD = re.compile(
    r"^(?:with|without|wearing|while|worn)\b|^[a-z]+ed\s+(?:into|in|over|under|at|around|up|down|by|to|behind|across|"
    r"through|from|on|back)\b",
    re.IGNORECASE,
)
_ARTICLE_HEAD = re.compile(r"^(?:an?|the|his|her|their|with|without|and)\b", re.IGNORECASE)
_ADJ_TAIL = re.compile(r"(?:ed|ish|ful|ous|ic|al|ive|less|tall|slim|lean|broad|thin|solid|strong)$", re.IGNORECASE)
# 빼는 순서(작을수록 먼저 빠짐): 몸 세부 → 피부 → 옷 두 번째 이후 → 코·턱·눈썹(그 밖 조각도 여기) → 체격·키 → 안경·수염 → 얼굴형.
# 끝까지 남기는 것(_RANK_KEEP): 주어(나이)·머리·눈 모양·옷 첫 항목.
_RANK_BODY, _RANK_SKIN, _RANK_OUTFIT, _RANK_FEATURE, _RANK_BUILD, _RANK_GLASSES, _RANK_FACE, _RANK_KEEP = 1, 2, 3, 4, 5, 6, 7, 99
# 머리 조각 낱말. 실사 테이크 외형(fit_appearance)은 남기는 조각 중 머리 조각을 눈 조각보다 오래 남긴다.
_HAIR_WORDS = r"hair|bangs|fringe|ponytail|pigtails|braids?|buns?|curls|locks|strands|hairstyle|haircut|undercut|mohawk|bald"
_HAIR_PIECE = re.compile(rf"\b(?:{_HAIR_WORDS})\b", re.IGNORECASE)
_PIECE_RANKS = (  # 위에서부터 처음 맞는 줄의 순위(더 오래 남는 쪽을 먼저 본다)
    (_RANK_KEEP, re.compile(rf"\b(?:{_HAIR_WORDS}|eyes?|eyelids?|eyelashes|lashes)\b", re.IGNORECASE)),
    (_RANK_FACE, re.compile(r"\b(?:face|features|cheekbones|visage)\b", re.IGNORECASE)),
    (_RANK_GLASSES, re.compile(
        r"\b(?:glasses|spectacles|eyeglasses|sunglasses|monocle|beard|mustache|moustache|stubble|goatee|clean-shaven|"
        r"sideburns)\b", re.IGNORECASE)),
    (_RANK_BUILD, re.compile(
        r"\b(?:build|physique|frame|figure|stature|height|tall|muscular|stocky|petite|lanky|athletic|broad-shouldered|"
        r"heavyset|burly|wiry)\b", re.IGNORECASE)),
    (_RANK_FEATURE, re.compile(
        r"\b(?:nose|jaw|jawline|chin|eyebrows|brows?|lips|mouth|cheeks?|forehead|ears?|teeth|dimples?|scars?|moles?|"
        r"freckles|wrinkles|lines|tattoos?)\b", re.IGNORECASE)),
    (_RANK_SKIN, re.compile(r"\b(?:skin|complexion|tan|tanned)\b", re.IGNORECASE)),
    (_RANK_BODY, re.compile(
        r"\b(?:neck|limbs|arms?|legs?|hands?|fingers|waist|hips?|shoulders?|chest|torso|wrists?|ankles?|feet|thighs?|"
        r"posture)\b", re.IGNORECASE)),
)
# 자른 끝에 매달리면 안 되는 낱말(전치사·접속사·관사·소유격).
_DANGLING = re.compile(
    r"(?:[\s,;:]+|\s+(?:with|without|or|and|of|a|an|the|in|on|at|to|into|over|across|by|for|from|his|her|their|its|"
    r"while|wearing))$",
    re.IGNORECASE,
)
# 관사·소유격으로 시작하는 조각은 명사구다. 머리말 바로 뒤에 빈칸으로 붙으면 with 가 빠진 것이다(_join_pieces).
_NOUN_LEAD = re.compile(r"^(?:an?|the|his|her|their)\s", re.IGNORECASE)
# 외모 문장 머리말 끝의 나이("a Korean man in his 30s"). fit_appearance 가 머리말을 나눌 때 쓴다.
_HEAD_AGE = re.compile(r"\s+in\s+(?:his|her|their)\s+(?:(?:early|mid|late)[- ])?\d0s\b", re.IGNORECASE)
_STRANDS = " with realistic hair strands"


def _keep_case(word: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if word[:1].isupper() else repl


def _sub_words(text: str, mapping: dict[str, str]) -> str:
    pattern = re.compile(r"\b(" + "|".join(re.escape(key) for key in mapping) + r")\b", re.IGNORECASE)
    return pattern.sub(lambda m: _keep_case(m.group(1), mapping[m.group(1).lower()]), text)


def align_gender(text, gender) -> str:
    """외모 문장 안의 성별 낱말(대명사·남녀 명사·male/female)을 정한 성별에 맞춘다.

    female·unknown 문장에 "male"·"He is"·"his" 가 남지 않게 한다(남성 고정 버그 수정). boy·girl 은 성별이 맞으면 둔다.
    """
    gender = _gender(gender)
    out = str(text or "")
    if gender == "male":
        out = re.sub(r"\bfemale\b", lambda m: _keep_case(m.group(0), "male"), out, flags=re.IGNORECASE)
        return _sub_words(out, {"she": "he", "her": "his", "hers": "his", "herself": "himself", "woman": "man",
                                "girl": "boy", "women": "men"})
    if gender == "female":
        out = re.sub(r"\bmale\b", lambda m: _keep_case(m.group(0), "female"), out, flags=re.IGNORECASE)
        return _sub_words(out, {"he": "she", "his": "her", "him": "her", "himself": "herself", "man": "woman",
                                "boy": "girl", "men": "women"})
    out = re.sub(r"\b(?:fe)?male\s+(?=[A-Za-z])", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?:fe)?male\b", lambda m: _keep_case(m.group(0), "person"), out, flags=re.IGNORECASE)
    out = _sub_words(out, {"he": "they", "she": "they", "his": "their", "her": "their", "hers": "theirs",
                           "him": "them", "himself": "themselves", "herself": "themselves", "man": "person",
                           "woman": "person", "boy": "person", "girl": "person", "men": "people", "women": "people"})
    # 대명사를 바꿔 생긴 문법만 고친다("They is" → "They are").
    return _sub_words(out, {"they is": "they are", "they has": "they have", "they was": "they were"}) \
        if re.search(r"\bthey (?:is|has|was)\b", out, re.IGNORECASE) else out


# 지시서 107: webtoon 분석 문장에서 자주 오는데 fx.strip_drawing_words 표에 없는 그림체 낱말(cast-photo-guard design D2). 캐스팅 외모
# 문장(_no_drawing)에서만 지운다. 낱말 경계로만 맞춘다 — "stylish"·"styled"·"comical"·"flat cap"·"neckline"·"digital watch" 는 남는다.
# 그림 방식 명사구(_STYLE_HEAD: "manhwa style"·"line art"·"cel shading"·"digital painting" …)가 구의 끝이면(뒤가 문장부호·끝·접속사·전치사)
# 앞 전치사구 "in/with/and/using (+ 관사)(+ 꾸밈말 2개까지)" 또는 관사째 지운다("drawn in a clean manhwa style" 에 "in a clean" 이,
# "…, a digital artwork" 에 "a" 가 남지 않게). 뒤에 명사가 오면("line art details") 낱말만 지운다. 지운 자리의 "a, …" 는 "a …" 로.
_STYLE_HEAD = (
    r"(?:man(?:hwa|hua)|comic(?:-book)?s?|chibi|art|drawing)[- ]style[sd]?|line[- ]?art|cel[- ]?shading"
    r"|digital[- ](?:painting|artwork|art|illustration)|flat[- ]shading|flat[- ]colou?rs"
)
_STYLE_PHRASE = re.compile(
    rf"\b(?:(?:in|with|and|using)\s+(?:(?:an?|the)\s+)?(?:[a-z]+(?:-[a-z]+)?\s+){{0,2}}?|(?:an?|the)\s+)(?:{_STYLE_HEAD})\b"
    r"(?=\s*(?:[,.;:]|$|\b(?:and|or|with|in|on|of|while|but)\b))",
    re.IGNORECASE,
)
_STYLE_WORDS = re.compile(
    rf"\b(?:{_STYLE_HEAD}|man(?:hwa|hua)|comic-books?|comics?|chibi|cel[- ]?shaded|styli[sz]ed|semi[- ]realistic|2d"
    r"|flat[- ]shaded|flat[- ]colou?red|sketch(?:ed|y)?)\b",
    re.IGNORECASE,
)
_ARTICLE_COMMA = re.compile(r"\b(an?|the)\s*(?:,\s*)+(?=[A-Za-z])", re.IGNORECASE)


def _no_drawing(text) -> str:
    """그림 낱말을 지운다(fx.strip_drawing_words). 'webtoon-style' 은 '-style' 까지, 그래도 'webtoon' 조각이 남으면(webtoonish 등) 그 조각도 지운다.

    지시서 107: 먼저 _STYLE_PHRASE·_STYLE_WORDS(manhwa·cel-shaded·line art·digital painting·stylized 등)를 지운다. 맞는 낱말이 없으면
    결과가 107 전과 같다.
    """
    out = str(text or "")
    if _STYLE_WORDS.search(out):
        out = _ARTICLE_COMMA.sub(r"\1 ", _STYLE_WORDS.sub(" ", _STYLE_PHRASE.sub(" ", out)))
    out = re.sub(r"\b(?:web-?toons?|anime|manga|cartoons?|illustrat\w*|drawn)[- ]style\b", " ", out,
                 flags=re.IGNORECASE)
    out = strip_drawing_words(out)
    if re.search(r"web-?toon", out, re.IGNORECASE):
        out = re.sub(r"[\w-]*web-?toon[\w-]*", " ", out, flags=re.IGNORECASE)
        out = strip_drawing_words(out)
    return out


def _tidy(text: str) -> str:
    out = re.sub(r"\s+([,.;:])", r"\1", str(text or ""))
    out = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", out)
    out = re.sub(r"\b(with|and)\s*,", ",", out, flags=re.IGNORECASE)
    out = " ".join(out.split())
    return out.strip(" ,;:")


def _clip_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.")


def _lower_article(text: str) -> str:
    return re.sub(r"^(A|An|The)\b", lambda m: m.group(1).lower(), text)


def _older(text: str, decade: int) -> bool:
    return decade >= 50 or bool(_OLD_WORDS.search(text))


def _mark_dyed(text: str, decade: int) -> str:
    """첫 'hair' 앞(같은 조각, 4낱말 안)의 머리색이 검정(또는 짙은 갈색·나이 든 회색)이 아니면 그 앞에 'dyed ' 를 붙인다."""
    found = re.search(r"\bhair\b", text, re.IGNORECASE)
    if not found:
        return text
    head = text[: found.start()]
    # 같은 조각만 본다(쉼표·with·and 앞의 옷 색 같은 다른 조각은 머리색이 아니다).
    cut = max([head.rfind(mark) for mark in (",", ";")] + [m.end() for m in re.finditer(r"\b(?:with|and)\b", head)] + [-1])
    window = [w for w in re.finditer(r"[A-Za-z][A-Za-z-]*", head) if w.start() > cut][-4:]
    colors = [w for w in window if any(part.lower() in _HAIR_COLORS for part in w.group(0).split("-"))]
    if not colors or any(w.group(0).lower() == "dyed" for w in window):
        return text
    parts = [part.lower() for w in colors for part in w.group(0).split("-")]
    bases = [part for part in parts if part in _HAIR_BASES]
    if not bases:
        return text
    natural = all(part in ("black", "raven") for part in bases) or (bases == ["brown"] and "dark" in parts)
    if not natural and all(part in ("gray", "grey", "white", "silver") for part in bases) and _older(text, decade):
        natural = True
    if natural:
        return text
    start = colors[0].start()
    return text[:start] + "dyed " + text[start:]


# ---------------------------------------------------------------- 외모 문장 다듬기(2026-10-30 피드백: 실제 분석 문장 꼴)
#
# 분석 appearanceEn·baseOutfitEn 은 "He is a …", "A … man in his twenties has …", "He wears …" 꼴이다. 아래 함수는
# 순수 함수이고 look_sentence 만 부른다(사용자가 고친 문장은 prompt_look 정리만 지난다).


def _strip_lead_verbs(text: str, wear: str) -> str:
    """문장 첫머리 주어+동사를 뺀다. 옷 동사는 wear 로(외모 "wearing ", 기본 복장 ""), 소유 동사(has·have)는 "with "."""
    out = _LEAD_WEAR.sub(lambda m: m.group(1) + wear, str(text or ""))
    out = _LEAD_SUBJECT.sub(
        lambda m: m.group(1) + ("with " if (m.group(2) or m.group(3) or "").lower() in ("has", "have") else ""), out,
    )
    return _HAS.sub(" with ", out)


def _drop_pieces(text: str, drop) -> str:
    """쉼표·세미콜론·문장·while 꼬리로 나눈 조각 중 drop(조각) 이 참인 것을 앞 나눔 글자와 함께 뺀다.

    목록 끝 "and …" 조각이 빠지면 남은 마지막 조각에 "and" 를 다시 붙인다("a, b, and none visible" → "a and b").
    """
    parts = _PIECE_SEP.split(str(text or ""))
    kept: list[list[str]] = []
    tail_and = False
    for index in range(0, len(parts), 2):
        sep = parts[index - 1] if index else ""
        piece = parts[index]
        if piece.strip() and drop(piece):
            tail_and = bool(re.match(r"\s*and\b", piece, re.IGNORECASE))
            continue
        if piece.strip():
            tail_and = False
        kept.append([sep, piece])
    if kept:
        kept[0][0] = ""
    if tail_and and len(kept) >= 2 and "," in kept[-1][0] and not re.match(r"\s*and\b", kept[-1][1], re.IGNORECASE):
        kept[-1][0] = " " if len(kept) == 2 else kept[-1][0]
        kept[-1][1] = "and " + kept[-1][1].lstrip()
    return "".join(sep + piece for sep, piece in kept)


def _strip_eye_colors(text: str) -> str:
    """눈(eyes) 앞 같은 명사구의 색 낱말(합성 색·톤 낱말 포함)만 빼고 눈 모양 낱말은 남긴다. 눈동자 조각은 통째로 뺀다."""
    out = _IRIS_PIECE.sub("", str(text or ""))
    out = _drop_pieces(out, lambda piece: bool(_IRIS_WORD.search(piece)) and not _EYE_NOUN.search(piece))
    out = _EYES_OF_COLOR.sub(r"\1", out)
    out = _COLOR_EYED.sub("", out)
    spans: set[tuple[int, int]] = set()
    for eye in _EYE_NOUN.finditer(out):
        head = out[: eye.start()]
        cut = max(head.rfind(","), head.rfind(";"), head.rfind("."))
        window: list[re.Match] = []
        for word in reversed(list(re.finditer(r"[A-Za-z][A-Za-z-]*", head))):
            low = word.group(0).lower()
            if word.start() <= cut or low in _EYE_STOP or low.endswith("ing") or len(window) >= 5:
                break
            window.insert(0, word)
        for index, word in enumerate(window):
            if not _COLOR_WORD.fullmatch(word.group(0)):
                continue
            spans.add(word.span())
            back = index - 1
            while back >= 0 and _TONE_WORD.fullmatch(window[back].group(0)):
                spans.add(window[back].span())
                back -= 1
            if index + 1 < len(window) and _COLOR_SUFFIX_WORD.fullmatch(window[index + 1].group(0)):
                spans.add(window[index + 1].span())
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + out[end:].lstrip(" ")
    return out


def _look_text(text: str) -> str:
    """분석 외모 문장 정리: 주어+동사·그림 낱말·표정 조각·눈 색·눈동자 조각을 뺀다(나이·머리말은 look_sentence)."""
    out = _strip_lead_verbs(text, "wearing ")
    out = _no_drawing(out)
    out = _EXPRESSION.sub("", out)
    out = _strip_eye_colors(out)
    # 색만 있던 눈("with blue eyes and …")은 색을 빼면 낱말 "eyes" 만 남는다 — 그 조각도 뺀다.
    out = re.sub(r"\bwith eyes and\s+", "with ", out, flags=re.IGNORECASE)
    out = re.sub(r"(?:,\s*|\s+and\s+|\s+with\s+)eyes\b(?=\s*(?:[,.;]|$))", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\bwith\s+(?:and|with)\b", "with", out, flags=re.IGNORECASE)
    return _tidy(out)


def _outfit_text(text: str) -> str:
    """기본 복장 정리: 앞 동사("He wears …")·보이지 않는다는 꼬리("while his shoes are not visible")·부정 꼬리("no visible
    jewelry")·그림 낱말·표정 조각을 빼고 첫 관사를 소문자로."""
    out = _OUTFIT_VERB.sub("", _strip_lead_verbs(text, "").strip())
    out = _drop_pieces(out, lambda piece: bool(_NOT_SHOWN.search(piece)))
    out = _EXPRESSION.sub("", _no_drawing(out))
    return _lower_article(_tidy(out.rstrip(" .")))


def _text_age(text: str, gender: str) -> tuple[str, int]:
    """글 속 나이("in his twenties"·"in her late 30s") → ("in his 20s" 꼴, 십 단위). 10대는 "early 20s"(D3). 없으면 ("", 0)."""
    found = _TEXT_AGE.search(text)
    if not found:
        return "", 0
    word = found.group(2).lower()
    decade = int(word[0]) * 10 if word[0].isdigit() else _AGE_DECADE_WORDS[word]
    qual = (found.group(1) or "").lower()
    if decade < 20:
        qual, decade = "early", 20
    pos = _WORDS[_gender(gender)]["pos"]
    return f"in {pos} {qual + ' ' if qual else ''}{decade}s", decade


def _look_head(text: str, gender: str, age: str) -> tuple[str, str]:
    """머리말 "a {형용사} Korean {명사} {나이}" 와 나머지(앞 나눔 글자 포함)."""
    words = _WORDS[gender]
    found = _HEAD_NOUN.search(text)
    prefix = text[: found.start()] if found else ""
    if found and len(prefix.split()) <= 6:
        matched = found.group(1).lower()
        noun = matched if (gender, matched) in (("male", "boy"), ("female", "girl")) else words["noun"]
        adjectives = _PREFIX_GENDER.sub(" ", _PREFIX_DROP.sub(" ", prefix))
        if age:
            adjectives = _PREFIX_AGE.sub(" ", adjectives)
        adjectives = _tidy(adjectives)
        head = (adjectives + " " if adjectives else "") + "Korean " + noun + (" " + age if age else "")
        article = "an" if head[:1].lower() in "aeiou" else "a"
        return f"{article} {head}", text[found.end():]
    else:
        lead = f"a Korean {words['noun']}" + (" " + age if age else "")
        rest = text[:1].lower() + text[1:] if text[:1].isupper() and not text[:2].isupper() else text
    if not rest:
        return lead, ""
    return lead, (" " if rest.lower().startswith(("with ", "wearing ")) else ", ") + rest


def _piece_rank(body: str) -> int | None:
    """조각 순위(_PIECE_RANKS). "facial hair" 는 머리가 아니라 수염이다. 맞는 줄이 없으면 None."""
    probe = re.sub(r"\bfacial\s+hair\b", "beard", body, flags=re.IGNORECASE)
    if re.match(r"(?:wearing|dressed)\b", probe, re.IGNORECASE):
        return _RANK_OUTFIT
    for rank, pattern in _PIECE_RANKS:
        if pattern.search(probe):
            return rank
    return None


def _look_pieces(text: str, outfit: bool, start: int) -> dict:
    """외모 나머지·기본 복장을 조각으로 나눈다. 조각: body·kind(item|clause|attached)·rank·index·and·drop.

    등위 형용사("a broad-shouldered, solid build")는 한 조각으로 붙인다. 옷은 첫 조각만 끝까지 남긴다.
    """
    parts = (_OUTFIT_SPLIT if outfit else _LOOK_SPLIT).split(text)
    pieces: list[dict] = []
    for index in range(0, len(parts), 2):
        sep = parts[index - 1] if index else ""
        body = _tidy(parts[index])
        if not body:
            continue
        piece = {"body": body, "and": bool(re.search(r"\band\b", sep, re.IGNORECASE)),
                 "attached": outfit and bool(pieces) and not sep.strip(), "drop": False}
        last = pieces[-1] if pieces else None
        if last is not None and not outfit and last.get("open") and not piece["and"] and not _ARTICLE_HEAD.match(body) \
                and not _CLAUSE_HEAD.match(body):
            last["body"] += ", " + body
            last["open"] = False
            continue
        words = body.split()
        # "a broad-shouldered" 처럼 관사 + 형용사로 끝나는 짧은 조각은 다음 조각("solid build")과 한 덩어리다.
        piece["open"] = (not outfit and len(words) <= 3 and words[0].lower() in ("a", "an")
                         and bool(_ADJ_TAIL.search(words[-1])))
        pieces.append(piece)
    for offset, piece in enumerate(pieces):
        piece["index"] = start + offset
        piece["kind"] = "attached" if piece["attached"] else ("clause" if _CLAUSE_HEAD.match(piece["body"]) else "item")
        keep_rank = _RANK_KEEP if offset == 0 else _RANK_OUTFIT
        piece["rank"] = keep_rank if outfit else (_piece_rank(piece["body"]) or _RANK_FEATURE)
    return {"pieces": pieces, "and": any(p["and"] for p in pieces if p["kind"] == "item")}


def _join_pieces(group: dict, lead: str, force_lead: bool = False) -> str:
    """남은 조각을 다시 잇는다. 첫 조각은 lead, 목록 항목은 쉼표, 원래 and 가 있었으면 마지막 항목 앞에 and.

    lead 가 빈칸 하나(" ")인데 첫 항목이 명사구이면 " with " 로 잇는다(2026-10-30 피드백: 원래 첫 조각이 빠져 목록 항목이
    머리말 바로 뒤에 오거나, 관사·소유격으로 시작하는 항목 — "a Korean man in his 30s an angular face" 가 되지 않게).
    """
    kept = [piece for piece in group["pieces"] if not piece["drop"]]
    items = [piece for piece in kept if piece["kind"] == "item"]
    last = items[-1] if group["and"] and len(items) >= 2 else None
    out = ""
    for index, piece in enumerate(kept):
        sep = ", "
        if index == 0:
            sep = lead if force_lead or piece["kind"] == "item" else (", " if lead.startswith(",") else " ")
            if sep == " " and piece["kind"] == "item" and (piece is not group["pieces"][0] or _NOUN_LEAD.match(piece["body"])):
                sep = " with "
        elif piece["kind"] == "attached":
            sep = " "
        elif piece is last:
            sep = " and " if len(items) == 2 else ", and "
        out += sep + piece["body"]
    return out


def _clip_tail(text: str, limit: int) -> str:
    """단어 경계에서 limit 자로 자르고 끝에 매달린 낱말(with·or·and·of·without·관사·소유격·쉼표)을 지운다."""
    out = _clip_words(text, limit)
    while True:
        trimmed = _DANGLING.sub("", out)
        if trimmed == out:
            return out
        out = trimmed


def _fit_look(head: str, rest: str, outfit: str) -> str:
    """300자가 넘을 때: 조각 단위로 덜 중요한 조각부터 뺀다(_PIECE_RANKS 순서, 같은 순위는 뒤 조각부터).

    머리말·머리·눈 모양·옷 첫 항목은 남긴다. 그것만으로도 넘으면 단어 경계에서 자르고 매달린 낱말을 지운다.
    """
    lead = re.match(r"\s*(,|with\b)?\s*", rest, re.IGNORECASE)
    token = (lead.group(1) or "").lower()
    look_lead = " with " if token == "with" else (", " if token == "," else " ")
    look = _look_pieces(rest[lead.end():], False, 0)
    wear = _look_pieces(outfit, True, len(look["pieces"]))

    def render() -> str:
        text = head + _join_pieces(look, look_lead)
        if any(not piece["drop"] for piece in wear["pieces"]):
            text += _join_pieces(wear, ", wearing " if text != head else " wearing ", force_lead=True)
        return _tidy(text)

    text = render()
    order = sorted((p for p in look["pieces"] + wear["pieces"] if p["rank"] < _RANK_KEEP), key=lambda p: (p["rank"], -p["index"]))
    for piece in order:
        if len(text) <= LOOK_MAX:
            break
        piece["drop"] = True
        text = render()
    while len(text) > LOOK_MAX:
        # 남기는 조각만으로도 넘친다: 가장 긴 조각을 넘친 만큼(한 번에 절반까지) 단어 경계에서 줄인다 — 조각마다 앞부분이 남고
        # 끝의 옷 첫 항목이 통째로 잘려 나가지 않는다. 더 줄일 수 없으면 전체를 단어 경계에서 자른다.
        kept = [piece for piece in look["pieces"] + wear["pieces"] if not piece["drop"]]
        longest = max(kept, key=lambda piece: len(piece["body"])) if kept else None
        size = len(longest["body"]) if longest else 0
        clipped = _clip_tail(longest["body"], max(size - (len(text) - LOOK_MAX), size // 2)) if longest else ""
        if not clipped or len(clipped) >= size:
            break
        longest["body"] = clipped
        text = render()
    return text if len(text) <= LOOK_MAX else _clip_tail(text, LOOK_MAX)


def _split_head(text: str) -> tuple[str, str]:
    """외모 문장 → (머리말 "… Korean man in his 30s", 나머지). 앞 6낱말 안(쉼표·마침표 없이)에 주어 명사가 없으면 ("", 글)."""
    found = _HEAD_NOUN.search(text)
    if not found or len(text[: found.start()].split()) > 6 or re.search(r"[,;.]", text[: found.start()]):
        return "", text
    age = _HEAD_AGE.match(text, found.end())
    end = age.end() if age else found.end()
    return text[:end], text[end:]


def _is_hair_piece(body: str) -> bool:
    return bool(_HAIR_PIECE.search(re.sub(r"\bfacial\s+hair\b", "beard", body, flags=re.IGNORECASE)))


def fit_appearance(text, limit: int, is_outfit=None) -> str:
    """외모 문장(look_sentence 꼴 "머리말 + 조각 + , wearing …" 또는 사용자가 고친 문장) → 옷을 뺀 얼굴·머리 문장(≤limit자).

    실사 테이크 ASSETS 외형(generate._real_appearance)이 쓴다. 조각 나누기·순위·잇기는 _fit_look 과 같다(_look_pieces·
    _piece_rank·_join_pieces). 문장 사이 마침표는 쉼표로 본다. 끝 마침표 없음.
    - 옷 조각: "wearing/dressed …" 조각, is_outfit(조각)이 참인 조각(머리·눈 조각은 이것으로 빼지 않는다 — "hair tied …"),
      옷 조각 뒤의 조각 중 코·턱·눈썹 순위 이상 낱말(체격·안경·얼굴·머리·눈 포함)이 없는 조각("a white pocket square"·
      "black ankle socks"). and 는 남은 외모 항목만으로 다시 센다.
    - 줄이기: _PIECE_RANKS 순서(같은 순위는 뒤 조각부터) → 눈 조각(뒤부터). 머리말·머리 조각은 끝까지 남긴다. 그래도 넘치면
      단어 경계에서 자르고 매달린 낱말을 지운다(_clip_tail).
    남은 조각이 없으면 머리말만, 머리말도 없으면 "".
    """
    text = _tidy(re.sub(r"\.\s+(?=\S)", ", ", str(text or "")).rstrip(" ."))
    head, rest = _split_head(text)
    lead = re.match(r"\s*(,|with\b)?\s*", rest, re.IGNORECASE)
    token = (lead.group(1) or "").lower()
    look_lead = " with " if token == "with" else (", " if token == "," else (" " if head else ""))
    group = _look_pieces(rest[lead.end():], False, 0)
    worn = False
    for piece in group["pieces"]:
        rank = _piece_rank(piece["body"])
        if rank == _RANK_OUTFIT or (worn and (rank or 0) < _RANK_FEATURE) \
                or (rank != _RANK_KEEP and is_outfit is not None and is_outfit(piece["body"])):
            piece["drop"] = worn = True
    group["and"] = any(piece["and"] for piece in group["pieces"] if piece["kind"] == "item" and not piece["drop"])

    def render() -> str:
        return _tidy(head + _join_pieces(group, look_lead))

    text = render()
    kept = [piece for piece in group["pieces"] if not piece["drop"]]
    order = sorted((p for p in kept if p["rank"] < _RANK_KEEP), key=lambda p: (p["rank"], -p["index"]))
    order += [p for p in reversed(kept) if p["rank"] >= _RANK_KEEP and not _is_hair_piece(p["body"])]
    for piece in order:
        if len(text) <= limit:
            break
        piece["drop"] = True
        text = render()
    return text if len(text) <= limit else _clip_tail(text, limit)


def look_sentence(appearance_en, desc_ko, gender, outfit_en="") -> str:
    """분석 외모 문장 → 실사 캐스팅 외모 문장(≤300자, 끝 마침표 없음).

    순서: 외모 정리(문장 첫머리 "He is"·"She has"·"They are" 등 빼기, 주어 뒤 has → with, 그림 낱말·표정 조각·눈 색·눈동자
    조각 제거) · 기본 복장 정리("He wears" 등·보이지 않는다는 꼬리·부정 꼬리 제거) → 나이(descriptionKo, 없으면 글 속 나이)가
    있으면 글 속 나이 낱말 제거 → 주어 "a Korean {명사} {나이}" → 성별 낱말 맞춤 → 검은 머리가 아니면 "dyed" → 문장 사이
    마침표를 쉼표로 → ", wearing {기본 복장}" → 300자가 넘으면 조각 단위로 덜 중요한 조각부터 뺀다(_fit_look).
    """
    gender = _gender(gender)
    look = _look_text(clean_text(appearance_en, 600))
    outfit = _outfit_text(clean_text(outfit_en, 400))
    decade = _age_parts(desc_ko)[1]
    age = age_phrase(desc_ko, gender)
    if not age:
        age, decade = _text_age(look, gender)
    if age:
        look = _tidy(_TEXT_AGE_WORDS.sub(" ", _TEXT_AGE.sub("", look)))
    head, rest = _look_head(look, gender, age)
    body = _mark_dyed(align_gender(head + rest, gender), decade)
    body = re.sub(r"\.\s+(?=\S)", ", ", body).rstrip(" .")
    head = align_gender(head, gender)
    if body.startswith(head):
        rest = body[len(head):]
    else:  # 머리말 안에 "hair" 가 있어 dyed 가 머리말에 들어간 경우 — 통째로 머리말로 본다
        head, rest = body, ""
    outfit = re.sub(r"\.\s+(?=\S)", ", ", align_gender(outfit, gender)).rstrip(" .")
    joiner = (", wearing " if rest.strip(" ,;:") else " wearing ") if outfit else ""
    text = _tidy(head + rest + joiner + outfit)
    if len(text) <= LOOK_MAX:
        return text
    return _fit_look(head, rest, outfit)


# ================================================================ 무늬 위치 경고(참고 구현 vpoc/live.py:102-111 자리)

_PATTERN = re.compile(
    r"\b(?:stripes?|striped|pattern(?:ed|s)?|prints?|printed|logos?|checks?|checked|checkered|plaid|tartan|"
    r"polka|dots?|dotted|floral|graphic|lettering|emblem|motif|camouflage|camo|houndstooth|argyle)\b",
    re.IGNORECASE,
)
_POSITION = re.compile(
    r"\b(?:chest|sleeves?|collar|cuffs?|hem|shoulders?|back|front|pocket|waist|waistband|neckline|neck|body|torso|"
    r"sides?|legs?|knees?|hood|lapels?|placket|left|right|center|centre|middle|top|bottom|along|across|down|around)\b",
    re.IGNORECASE,
)
PATTERN_WARNING = (
    "옷 무늬가 어디에 있는지 적혀 있지 않습니다. 무늬 위치(예: '소매에 흰 줄 하나, 몸판은 검정', '가슴에 로고')를 "
    "외모 문장에 적어 주세요. 위치가 없으면 사진마다 무늬 자리가 달라질 수 있습니다."
)


def pattern_warning(look_en) -> str:
    """외모 문장에 옷 무늬 낱말이 있는데 위치 낱말이 없으면 경고 문구, 아니면 ""."""
    text = clean_text(look_en, 2000)
    if _PATTERN.search(text) and not _POSITION.search(text):
        return PATTERN_WARNING
    return ""


# ================================================================ 캐스팅 문장(참고 구현 vpoc/live.py:114-118 cast_prompt)


def prompt_look(look_en, gender) -> str:
    """캐스팅 문장에 넣을 외모: 한 줄·≤300자·그림 낱말 제거·성별 낱말 맞춤·끝 마침표 없음, 첫 "dyed … hair" 뒤 strands."""
    text = _tidy(align_gender(_no_drawing(clean_text(look_en, LOOK_MAX)), gender).rstrip(" ."))
    found = re.search(r"\bdyed\b[^,;.]*?\bhair\b", text, re.IGNORECASE)
    if found and "hair strands" not in text.lower():
        text = text[: found.end()] + _STRANDS + text[found.end():]
    return text


def cast_prompt(look_en, preset, gender, bl_types="", terms=()) -> str:
    """프리셋 문구 + " {He is|She is|They are} {외모}, with dark brown eyes." + " " + photo_anchor(성별) (≤1500자).

    bl_types(BL 유형 id 하나 또는 id 목록, 지시서 103·104)가 있으면 눈 색 문장 뒤에 참고 문장 " He has …." 을 더한다(_cast_text).
    없거나 모르는 값이면 참고 문장이 없다. 사진 고정 문장(지시서 107)은 늘 맨 끝이다.
    terms(일반 외모 용어 id 하나 또는 목록, 지시서 112 — 설명에서 따로 찾은 "떡대")는 _cast_text 를 본다.
    """
    return _cast_text(look_en, preset, gender, bl_types, terms)[0]


# ================================================================ BL 유형 참고 문장(지시서 103·104, 2026-10-31)
#
# 유형 조각(genre_bl.hint_pieces)을 외모 뒤 한 문장 "He has A, B, and C." 로 더한다. 외모 문장(그림 사실)에 이미 있는 형태 분류의
# 조각은 빠진다(그림 우선). 외모 문장(look_sentence·lookEn)과 영상 쪽은 바꾸지 않는다 — 캐스팅 문장에만 더한다.
# 유형은 사용자가 쓴 캐릭터 설명(char.extraPromptKo)에서 찾는다(_bl_types_now, 지시서 104).
# 지시서 112: 일반 외모 용어(web_terms — 떡대)도 같은 문장 맨 앞에 더한다(_term_ids·_terms_now). 그 조각은 그림 우선에서 빠지지 않는다.

# 유형(112 부터 일반 외모 용어도)이 있고 성별이 unknown 이면 male 로 쓴다(steering genre-bl: 두 인물 모두 man). align_gender(male) 은 중립 낱말을
# 바꾸지 않으므로 외모 문장의 person·their 등도 male 낱말로 바꾼다(design D6).
_NEUTRAL_TO_MALE = {"person": "man", "people": "men", "their": "his", "theirs": "his", "them": "him", "themselves": "himself"}
_HINT_SUBJECT = {"male": "He has", "female": "She has", "unknown": "They have"}


def _hint_sentence(pieces: list[str], gender) -> str:
    """조각 → "He has A." / "He has A and B." / "He has A, B, and C." (조각은 1개 이상)."""
    if len(pieces) == 1:
        body = pieces[0]
    elif len(pieces) == 2:
        body = f"{pieces[0]} and {pieces[1]}"
    else:
        body = ", ".join(pieces[:-1]) + ", and " + pieces[-1]
    return f"{_HINT_SUBJECT[_gender(gender)]} {body}."


def _term_ids(bl_types, terms) -> list[str]:
    """이 문장에 쓸 일반 외모 용어(지시서 112): 유형이 뜻하는 용어(genre_bl.TYPE_TERMS — 떡대공·떡대수) + 설명에서 따로 찾은 용어, 중복 없이."""
    return web_terms.known_terms(genre_bl.type_terms(bl_types) + web_terms.known_terms(terms))


def _cast_text(look_en, preset, gender, bl_types="", terms=()) -> tuple[str, str]:
    """(캐스팅 문장, 더한 참고 문장). 유형·용어가 없거나 모르는 값이면 참고 문장은 "" 이다.

    bl_types 는 유형 id 하나 또는 id 목록이다(지시서 104: 설명에서 찾은 최대 2개, 같은 분류는 앞의 유형이 이긴다 —
    genre_bl.merged_pieces). 문장 끝은 늘 " " + photo_anchor(성별)이다(지시서 107, 유형 + unknown 이면 male 로 바꾼 뒤의 성별). PROMPT_MAX
    에서 그 몫을 뺀 예산을 넘으면 참고 문장 조각을 끝에서부터 빼고(design D7), 다 빼도 넘으면 외모를 단어 경계에서 줄인다(_fit_cast_head).
    사진 고정 문장은 자르지 않는다.

    terms(지시서 112): 유형이 뜻하는 용어(떡대공·떡대수 → 떡대)와 합친 일반 외모 용어(_term_ids). 있으면 그 조각(떡대 체격)을 참고 문장
    맨 앞에 늘 넣는다(genre_bl.hint_pieces forced — 그림 우선에서 빠지지 않음). 성별 unknown 이면 유형과 같이 male. 몸 용어면 외모 문장의
    부딪히는 체격 낱말을 빼고(web_terms.strip_conflicts, 이 문장에서만) 프리셋 구도를 바꾼다(base_text physique). 1500자를 넘으면 다른
    조각 → 외모(단어 경계) 순으로 줄이고 체격 조각은 사진 고정 문장 다음으로 마지막까지 남긴다(_fit_physique). 용어가 없으면 112 전과 같다.
    """
    gender = _gender(gender)
    types = genre_bl.known_types(bl_types)
    term_ids = _term_ids(types, terms)
    physique = web_terms.physique_of(term_ids)
    forced = bool(types or term_ids) and gender == "unknown"
    if forced:
        gender = "male"
    words = _WORDS[gender]
    look = prompt_look(look_en, gender)
    if forced:
        look = _sub_words(look, _NEUTRAL_TO_MALE)
    if physique:
        look = _tidy(web_terms.strip_conflicts(look, term_ids))
    look = look or f"a Korean {words['noun']}"
    text = f"{base_text(preset, gender, physique)} {words['subj']} {look}, with dark brown eyes."
    anchor = photo_anchor(gender)
    budget = PROMPT_MAX - len(anchor) - 1
    fixed = web_terms.term_pieces(term_ids)
    pieces = genre_bl.hint_pieces(types, look, forced=fixed)
    sentence = ""
    while pieces:
        sentence = _hint_sentence(pieces, gender)
        if len(text) + 1 + len(sentence) <= budget or len(pieces) <= len(fixed):
            break
        pieces = pieces[:-1]
        sentence = ""
    if sentence and len(text) + 1 + len(sentence) > budget:
        text, sentence = _fit_physique(preset, gender, look, budget, sentence, physique)
    elif sentence:
        text = f"{text} {sentence}"
    if len(text) > budget:
        text = _fit_cast_head(preset, gender, look, budget, physique)
    return (f"{text} {anchor}" if text else anchor[:PROMPT_MAX]), sentence


def _fit_physique(preset, gender: str, look: str, budget: int, sentence: str, physique: str) -> tuple[str, str]:
    """체격 조각만 남은 참고 문장(sentence)을 붙여도 budget 을 넘을 때(지시서 112): 체격 문장을 두고 그 앞(프리셋·주어·외모·눈 색)을 줄인다.

    외모를 단어 경계에서 먼저 줄이고(_fit_cast_head), 그래도 자리가 없으면 체격 문장만, 체격 문장도 넘치면 체격 문장을 빼고 107 꼴로 줄인다.
    """
    room = budget - 1 - len(sentence)
    if room > 0:
        return f"{_fit_cast_head(preset, gender, look, room, physique)} {sentence}", sentence
    if len(sentence) <= budget:
        return sentence, sentence
    return _fit_cast_head(preset, gender, look, budget, physique), ""


def _fit_cast_head(preset, gender: str, look: str, budget: int, physique: str = "") -> str:
    """참고 문장 없이도 budget 을 넘을 때(지시서 107): 프리셋 문구·주어·", with dark brown eyes." 는 두고 외모만 단어 경계에서 줄인다
    (_clip_tail — 끝에 매달린 with·and·쉼표를 지운다). 외모 자리가 없으면 앞에서부터 budget 자(107 전 자르기와 같은 꼴).
    physique(지시서 112)는 base_text 에 그대로 넘긴다."""
    head = f"{base_text(preset, gender, physique)} {_WORDS[gender]['subj']} "
    tail = ", with dark brown eyes."
    room = budget - len(head) - len(tail)
    clipped = _clip_tail(look, room) if room > 0 else ""
    if clipped:
        return head + clipped + tail
    return (head + look + tail)[:max(0, budget)].rstrip()



# ================================================================ 뽑기(B2, design §B.3·§B.4)
#
# start_draw 가 확인 금액·진행 중 여부를 보고 casting 을 drawing 으로 바꾼 뒤 백그라운드 스레드(run_draw)를 띄운다.
# 여러 파드 중복은 casting.claim(조건부 쓰기, mutate_char)으로, 같은 파드 중복은 _CAST_RUNNING 으로 막는다.
# 캐릭터 status·variants·시트·3D 는 건드리지 않는다(casting 키만 고친다, RB1.2·RB1.3).
#
# 과금: 만든 사진 1장마다 원장 1건(키 vnew-cast-{cid}-{후보 id}). 후보 id 는 fal 요청 id 에서 정해지므로, 파드가 죽어
# 같은 요청을 다시 받아도 같은 S3 키·같은 원장 키라 두 번 계상하지 않는다. fal 이 결과를 주지 않은 칸은 과금하지 않는다.
# 그림체로 버린 사진(지시서 107)도 fal 이 만든 1장이라 같은 키 규칙으로 1건 남긴다(S3 에는 두지 않는다). 다시 뽑은 요청은 새 요청 id
# 라 새 키다 — 원장 건수 = 결과 사진을 받은 fal 요청 수.
# 원장 금액은 회차에 남긴 장당 금액(unitUsd), 모델은 그 요청의 endpoint(requestArn)다. 아이돌풍 FLUX.2 [pro] 회차는 장당 $0.075,
# 현실 배우풍·되돌린 아이돌풍·109 전 회차는 Seedream 장당 VNEW_CASTING_USD(지시서 109).

_CAST_RUNNING: set[tuple[str, str]] = set()
_CAST_LOCK = threading.Lock()
HISTORY_MAX = 12  # 이전 후보 최대(오래된 것부터 버린다, 파일은 지우지 않는다)
ERROR_MAX = 300
STATUSES = ("none", "drawing", "ready", "failed")
_FETCH_TIMEOUT_S = 120
_FETCH_MAX_BYTES = 25 * 1024 * 1024  # 결과 파일 상한(512Mi 파드 보호). 1920x2560 PNG 도 10MB 안팎이다.
_OUT_MAX_PX = 1536  # 저장 JPEG 긴 변(참고 구현과 같다)
_OUT_QUALITY = 90
# 디코드 전 픽셀 상한. 작은 PNG 도 픽셀이 많으면 RGB 로 풀 때 수백 MB 가 된다(1920x2560 = 4.9M 의 약 4배).
_MAX_PIXELS = 20_000_000
# 사진 받기·디코드는 프로세스 전체에서 한 장씩(여러 캐릭터를 동시에 뽑아도 봉우리 ≈ 25MB + 15MB, 512Mi 파드 보호).
_PHOTO_SEM = threading.BoundedSemaphore(1)
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_MSG_ABORTED = "중단돼 제출하지 않았습니다."
_MSG_TIMEOUT = "시간 안에 사진을 받지 못했습니다."
_MSG_NO_IMAGE = "fal이 사진을 주지 않았습니다."
_MSG_BAD_IMAGE = "받은 사진을 읽지 못했습니다."
# fal 4xx 거절(vpoc.fal._REJECT_HTTP) 칸 문구(091 남은 이슈 5, PM 결정). vpoc.fal.rejection 의 영상용 문구("이 샷을")
# 대신 캐스팅용 문구를 쓴다. fal 거절 상세는 error_gist(URL 쿼리·data URI·키 제거, 120자)를 지나 괄호로 붙인다.
_MSG_REJECTED = "fal이 이 캐스팅 문장을 거절했습니다. 외모 문장을 바꿔 다시 뽑아 주세요."
_REJECT_GIST_MAX = 120
# 실사 사진 검사(지시서 107). 다시 뽑은 사진도 그림체인 칸의 문구, 보기 안내 한 줄(VNEW_CAST_PHOTO_CHECK 켬일 때만 보기에 둔다).
_MSG_NOT_PHOTO = "실사 사진이 아니라 그림체로 나와 이 칸을 버렸습니다. 다시 뽑아 주세요."
_MSG_PHOTO_NOTE = "그림체로 나온 칸은 최대 두 번 다시 뽑으며 그 비용이 더해질 수 있습니다."
# 칸마다 다시 제출하는 최대 횟수(지시서 108: 두 번까지). 칸의 redraws 에 남겨 재시작 뒤에도 넘지 않는다.
_REDRAW_MAX = 2


class _ClaimLost(Exception):
    """이 실행의 claim 이 더는 casting.claim 이 아니거나 뽑기가 끝났다. 쓰지 않고 접는다."""


class _BadImage(Exception):
    """받은 결과를 사진으로 읽지 못했다(형식·픽셀 상한). 그 칸만 실패, 과금하지 않는다."""


def _mark(owner: str, cid: str) -> None:
    with _CAST_LOCK:
        _CAST_RUNNING.add((owner, cid))


def _unmark(owner: str, cid: str) -> None:
    with _CAST_LOCK:
        _CAST_RUNNING.discard((owner, cid))


def _running(owner: str, cid: str) -> bool:
    with _CAST_LOCK:
        return (owner, cid) in _CAST_RUNNING


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# ---------------------------------------------------------------- 외부 호출 래퍼(검사가 바꿔 끼운다)


def _fal_request(method: str, url: str, payload: dict | None = None) -> dict:
    """fal 큐 HTTP(제출·조회). vpoc.fal._request 를 그대로 쓴다(키는 거기서만 다룬다)."""
    return vpoc_fal._request(method, url, payload, where="vnew.casting")  # noqa: SLF001


def _fetch_url(url: str) -> bytes:
    """fal 결과 사진을 받는다(https 만, 120초, 25MB 상한). 오류는 VpocError 로 바꾼다(4xx 는 영구, 그 밖은 일시)."""
    if not str(url or "").startswith("https://"):
        raise VpocError("vnew.casting", "BadResultUrl", _MSG_NO_IMAGE, 502)
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as response:  # noqa: S310 — https 만
            raw = response.read(_FETCH_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise VpocError("vnew.casting", f"HTTP{exc.code}", f"결과 사진을 받지 못했습니다(HTTP {exc.code}).", 502) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise VpocError("vnew.casting", "FalUnreachable", "결과 사진을 받는 중 연결이 끊겼습니다.", 502) from exc
    if len(raw) > _FETCH_MAX_BYTES:
        raise VpocError("vnew.casting", "ResultTooLarge", "결과 사진이 너무 큽니다.", 502)
    return raw


# ---------------------------------------------------------------- 저장값 · 설정


def _empty_casting() -> dict:
    return {
        "preset": DEFAULT_PRESET, "gender": "", "lookEn": "", "lookEdited": False, "blRole": "", "blType": "",
        "status": "none", "error": "", "round": 0, "usd": 0.0,
        "startedAt": "", "heartbeatAt": "", "claim": "", "claimedAt": "", "prompt": "",
        "slots": [], "candidates": [], "history": [], "chosen": "",
    }


def _casting_of(char) -> dict:
    """캐릭터의 casting 을 기본값을 채운 사본으로(없으면 캐스팅 없음 = 기본값)."""
    raw = char.get("casting") if isinstance(char, dict) else None
    out = _empty_casting()
    if isinstance(raw, dict):
        out.update(raw)
    for key in ("slots", "candidates", "history"):
        out[key] = [dict(item) for item in out.get(key) or [] if isinstance(item, dict)]
    return out


def _ensure(data: dict) -> dict:
    """char 의 casting 을 기본값을 채운 dict 로 두고 그 dict 를 돌려준다(mutate 안 제자리 수정용)."""
    data["casting"] = _casting_of(data)
    return data["casting"]


def require_enabled(where: str) -> None:
    """VNEW_CASTING 이 꺼져 있으면 404 CastingDisabled(새 라우트가 없는 것과 같다, R0.3)."""
    if not settings.VNEW_CASTING:
        raise VpocError(where, "CastingDisabled", "실사 캐스팅 기능이 꺼져 있습니다.", 404)


def count() -> int:
    return max(1, min(4, int(settings.VNEW_CASTING_COUNT)))


def total_usd(preset=None) -> float:
    """뽑기 1회 금액 = 장당 금액 × 장 수(소수 2자리, 결정 1).

    preset 을 주면 그 프리셋의 장당 금액(preset_choice, 지시서 109), 없으면 지금처럼 장당 VNEW_CASTING_USD.
    """
    unit = float(settings.VNEW_CASTING_USD) if preset is None else preset_choice(preset)[1]
    return round(count() * unit, 2)


def _parse_size(value, default: tuple[int, int]) -> tuple[int, int]:
    """"WxH"(3~4자리, 각 256~4096) → (W, H). 꼴이 틀리면 default."""
    found = re.fullmatch(r"\s*(\d{3,4})\s*[xX]\s*(\d{3,4})\s*", str(value or ""))
    if found:
        width, height = int(found.group(1)), int(found.group(2))
        if 256 <= width <= 4096 and 256 <= height <= 4096:
            return width, height
    return default


def _image_size() -> tuple[int, int]:
    return _parse_size(settings.VNEW_CASTING_SIZE, (1920, 2560))


def preset_choice(preset) -> tuple[str, float, tuple[int, int]]:
    """프리셋별 (모델, 장당 금액, 크기)(지시서 109).

    아이돌풍은 VNEW_CASTING_IDOL_MODEL·VNEW_CASTING_IDOL_USD·VNEW_CASTING_IDOL_SIZE, 현실 배우풍은 지금 값(VNEW_CASTING_MODEL·
    VNEW_CASTING_USD·VNEW_CASTING_SIZE). VNEW_CASTING_IDOL_MODEL 이 빈 값이면(되돌리기) 아이돌풍도 지금 값 셋을 쓴다.
    """
    idol_model = str(settings.VNEW_CASTING_IDOL_MODEL or "").strip()
    if preset_of(preset) == "idol" and idol_model:
        return idol_model, float(settings.VNEW_CASTING_IDOL_USD), _parse_size(settings.VNEW_CASTING_IDOL_SIZE, (1760, 2352))
    return settings.VNEW_CASTING_MODEL, float(settings.VNEW_CASTING_USD), _image_size()


def _round_choice(cast: dict) -> tuple[str, float, tuple[int, int]]:
    """이 회차의 (모델, 장당 금액, 크기). start_draw 가 회차에 남긴 값을 쓴다(지시서 109 — 설정이 바뀌어도 한 회차 안에서 섞이지 않게).

    남긴 모델·장당 금액이 없거나 꼴이 틀린 회차(109 전에 시작한 107·108 회차)는 지금처럼 VNEW_CASTING_MODEL·VNEW_CASTING_USD·
    VNEW_CASTING_SIZE 를 쓴다. 크기만 꼴이 틀리면 VNEW_CASTING_SIZE.
    """
    model = cast.get("model")
    unit = cast.get("unitUsd")
    if isinstance(model, str) and model.strip() and isinstance(unit, (int, float)) and not isinstance(unit, bool) \
            and math.isfinite(unit) and unit >= 0:
        return model.strip(), float(unit), _parse_size(cast.get("imageSize"), _image_size())
    return settings.VNEW_CASTING_MODEL, float(settings.VNEW_CASTING_USD), _image_size()


def _preset_items() -> list[dict]:
    """보기의 presets. 아이돌풍 모델이 켜져 있으면(지시서 109) 항목마다 unitUsd·totalUsd 를 더한다(화면이 느낌을 바꾸면 금액을 바꾼다).

    VNEW_CASTING_IDOL_MODEL 이 빈 값이면(되돌리기) 두 프리셋 금액이 같으므로 108 과 같은 {key, label} 만 둔다.
    """
    items = [dict(item) for item in PRESETS]
    if str(settings.VNEW_CASTING_IDOL_MODEL or "").strip():
        for item in items:
            item.update(unitUsd=preset_choice(item["key"])[1], totalUsd=total_usd(item["key"]))
    return items


def _check_cost(where: str, preset, shown: float) -> float:
    """화면 확인 금액(shown)이 그 프리셋 총액(장당 × 장 수)과 같은지. 다르면 409 CostMismatch. 서버 총액을 돌려준다."""
    expected = total_usd(preset)
    if shown != expected:
        raise VpocError(where, "CostMismatch", f"금액이 서버 계산과 다릅니다. 서버 {expected} USD, 화면 {shown} USD.", 409)
    return expected


def _apply_settings(cast: dict, preset, gender, look_en, where: str) -> None:
    """들어온 값만 정리해 casting 에 둔다(None 이면 그대로). 400 BadPreset·BadGender·BadLook.

    BL 역할·유형(103 화면 선택값)은 받지 않는다(지시서 104 — 유형은 캐릭터 설명에서 찾는다, _bl_types_now).
    """
    if preset is not None:
        key = preset.strip().lower() if isinstance(preset, str) else None
        if key not in _BASES:
            raise VpocError(where, "BadPreset", "알 수 없는 느낌입니다(아이돌풍·현실 배우풍).", 400)
        cast["preset"] = key
    if gender is not None:
        key = gender.strip().lower() if isinstance(gender, str) else None
        key = "" if key == "auto" else key
        if key not in ("",) + GENDERS:
            raise VpocError(where, "BadGender", "알 수 없는 성별입니다(자동·남·여·모름).", 400)
        cast["gender"] = key
    if look_en is not None:
        if not isinstance(look_en, str):
            raise VpocError(where, "BadLook", "외모 문장은 글이어야 합니다.", 400)
        text = clean_text(look_en, LOOK_MAX)
        # 빈 값이면 자동 문장으로 되돌린다(참고 구현 set_look 과 같다).
        cast["lookEn"] = text
        cast["lookEdited"] = bool(text)


# ---------------------------------------------------------------- BL 유형(지시서 104)
#
# 유형은 사용자가 쓴 캐릭터 설명(char.extraPromptKo)에서만 찾는다. 103 의 화면 선택값(casting.blRole·blType)은 읽지도 지우지도 않는다
# (design D22). 그 값을 다루던 103 함수와 문구 상수는 104 피드백(Claude 승인)으로 지웠다(design D31).


def _bl_types_now(char) -> list[str]:
    """뽑기 문장·보기에 쓸 BL 유형(지시서 104, design D18): 사용자가 쓴 캐릭터 설명(char.extraPromptKo)에서 찾은 유형 id(최대 2개).

    analysis.descriptionKo(LLM 이 쓴 글)·외모 문장·저장된 blRole·blType 은 보지 않는다. VNEW_BL_LOOK 끔이면 [].
    """
    if not settings.VNEW_BL_LOOK or not isinstance(char, dict):
        return []
    return genre_bl.detect_types(char.get("extraPromptKo"))


def _terms_now(char) -> list[str]:
    """뽑기 문장·보기에 쓸 일반 외모 용어(지시서 112, design D6): 사용자가 쓴 캐릭터 설명에서 유형 이름 밖에 따로 적힌 용어 id("떡대"만).

    근거·끔 규칙은 _bl_types_now 와 같다(extraPromptKo 만, VNEW_BL_LOOK 끔이면 []). 찾은 유형이 이미 뜻하는 용어(떡대공 → 떡대)는 없다.
    """
    if not settings.VNEW_BL_LOOK or not isinstance(char, dict):
        return []
    return genre_bl.detect_terms(char.get("extraPromptKo"))


def auto_look(char) -> str:
    """분석 appearanceEn + 기본 복장 + descriptionKo 나이로 만든 자동 외모 문장. 분석이 없으면 ""."""
    analysis = (char.get("analysis") if isinstance(char, dict) else None) or {}
    appearance = clean_text(analysis.get("appearanceEn"), 600)
    outfit = clean_text(analysis.get("baseOutfitEn") or analysis.get("outfitEn"), 400)
    if not appearance and not outfit:
        return ""
    gender = resolve_gender(_casting_of(char), analysis)
    return look_sentence(appearance, analysis.get("descriptionKo"), gender, outfit)


def current_look(char) -> str:
    """지금 쓸 외모 문장: 사용자가 고친 문장(lookEdited)이 있으면 그것, 아니면 자동 문장."""
    cast = _casting_of(char)
    edited = clean_text(cast.get("lookEn"), LOOK_MAX)
    if cast.get("lookEdited") and edited:
        return edited
    return auto_look(char)


def _find(cast: dict, cand_id: str) -> dict | None:
    for item in list(cast.get("candidates") or []) + list(cast.get("history") or []):
        if isinstance(item, dict) and item.get("id") == cand_id:
            return item
    return None


def chosen_key(char) -> str:
    """고른 캐스팅 사진의 S3 키(candidates·history 에서 찾음). 없으면 ""."""
    cast = _casting_of(char)
    chosen = str(cast.get("chosen") or "")
    item = _find(cast, chosen) if chosen else None
    return str(item.get("key") or "") if item else ""


def _trim_history(items: list[dict], chosen: str) -> list[dict]:
    """최신 먼저 HISTORY_MAX 개. 고른 사진(chosen)이 잘려 나가면 마지막 자리에 남긴다(결정 5: 다시 뽑아도 chosen 유지)."""
    kept = items[:HISTORY_MAX]
    if chosen and not any(item.get("id") == chosen for item in kept):
        picked = next((item for item in items if item.get("id") == chosen), None)
        if picked is not None:
            kept = items[: HISTORY_MAX - 1] + [picked]
    return kept


def _prompt_preset(prompt: str) -> str:
    """회차 문장이 어느 프리셋으로 만들어졌는지(도중에 느낌을 바꿔도 후보의 느낌은 그 회차 값)."""
    for key, base in _BASES.items():
        if str(prompt or "").startswith(base.split("{", 1)[0]):
            return key
    return DEFAULT_PRESET


# ---------------------------------------------------------------- 공개 동작


def update_settings(owner: str, cid: str, *, preset=None, gender=None, look_en=None) -> dict:
    """느낌·성별·외모 문장 저장(PUT /casting). 빈 외모 문장은 자동 문장으로 되돌린다. 캐스팅 보기를 돌려준다.

    BL 역할·유형은 받지 않는다(지시서 104 — 라우트가 넘기지 않는다. 저장된 103 값은 그대로 둔다).
    """
    where = "vnew.casting"
    require_enabled(where)

    def mutator(data: dict) -> None:
        _apply_settings(_ensure(data), preset, gender, look_en, where)

    char, _ = characters.mutate_char(owner, cid, mutator)
    return view(char)


def start_draw(owner: str, cid: str, confirm_usd: float, *, preset=None, gender=None, look_en=None) -> dict:
    """뽑기 시작(POST /casting/draw). 404 CastingDisabled · 409 FalKeyMissing·CostMismatch·CastingRunning·NoLook · 400 형식.

    이전 후보는 history 로 옮기고(최신 먼저 12개), 고른 사진(chosen)은 그대로 둔다(결정 5). 캐스팅 보기를 돌려준다.
    사용자가 쓴 캐릭터 설명(extraPromptKo)에 BL 유형 이름이 있으면 회차 문장 끝에 참고 문장을 더한다(지시서 104,
    VNEW_BL_LOOK 끔이면 더하지 않는다). 떡대(떡대공·떡대수·"떡대"만)면 체격 조각·허리 위 구도가 붙고 로그 physique=ttokdae(지시서 112).
    확인 금액(confirm_usd)은 고른 느낌(preset, 없으면 저장된 느낌)의 총액이어야 하고, 고른 모델·장당 금액·크기를 회차에 남긴다(지시서 109).
    """
    where = "vnew.casting.draw"
    require_enabled(where)
    if not vpoc_settings.FAL_KEY:
        raise VpocError(where, "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    _apply_settings(_empty_casting(), preset, gender, look_en, where)  # 형식만 먼저 본다
    slots = count()
    shown = round(float(confirm_usd), 2)
    if preset is not None:
        # 확인 금액 = 고른 프리셋의 총액(지시서 109). 화면은 늘 느낌을 보내므로 캐릭터를 읽기 전에 본다(지금 순서).
        _check_cost(where, preset, shown)
    token = secrets.token_hex(6)
    picked = {}

    def mutator(data: dict) -> None:
        cast = _ensure(data)
        if preset is None:
            # 느낌을 보내지 않은 요청은 저장된 느낌의 총액과 맞아야 한다(지시서 109).
            _check_cost(where, cast.get("preset"), shown)
        if cast.get("status") == "drawing":
            # 멈춘 뽑기는 조회 때 이어하기가 끝낸다(시작부터 최대 VNEW_CASTING_POLL_MAX_S).
            raise VpocError(where, "CastingRunning", "이미 실사 캐스팅 사진을 만들고 있습니다.", 409)
        _apply_settings(cast, preset, gender, look_en, where)
        look = current_look(data)
        if not look:
            raise VpocError(where, "NoLook", "먼저 캐릭터를 만들어 분석해 주세요.", 409)
        gender_now = resolve_gender(cast, data.get("analysis"))
        # 이 회차의 모델·장당 금액·크기(지시서 109). 뽑기·다시 제출·이어 조회는 설정 대신 이 값을 쓴다(_round_choice).
        model, unit_usd, size = preset_choice(cast["preset"])
        picked["usd"] = total_usd(cast["preset"])
        now = now_iso()
        cast["history"] = _trim_history(cast["candidates"] + cast["history"], str(cast.get("chosen") or ""))
        cast["candidates"] = []
        cast["round"] = int(cast.get("round") or 0) + 1
        cast["slots"] = [
            {"slot": index, "status": "queued", "requestArn": "", "candidateId": "", "error": ""} for index in range(slots)
        ]
        cast.update(
            status="drawing", error="", usd=picked["usd"], claim=token, claimedAt=now, startedAt=now, heartbeatAt=now,
            prompt=cast_prompt(look, cast["preset"], gender_now, _bl_types_now(data), _terms_now(data)),
            model=model, unitUsd=unit_usd, imageSize=f"{size[0]}x{size[1]}",
        )

    char, _ = characters.mutate_char(owner, cid, mutator)
    cast = _casting_of(char)
    bl_types = _bl_types_now(char)
    log.info(
        "vnew.casting.draw_start cid=%s round=%d slots=%d usd=%.2f preset=%s gender=%s bl=%s physique=%s",
        cid, int(cast.get("round") or 0), slots, picked["usd"], cast.get("preset"), resolve_gender(cast, char.get("analysis")),
        "+".join(bl_types) or "-", web_terms.physique_of(_term_ids(bl_types, _terms_now(char))) or "-",
    )
    _spawn(owner, cid, token)
    return view(char)


def _spawn(owner: str, cid: str, token: str) -> None:
    """뽑기 스레드를 띄운다(이 파드에서 실행 중으로 먼저 표시해 조회 이어하기가 끼어들지 않게)."""
    _mark(owner, cid)
    try:
        thread = threading.Thread(target=run_draw, args=(owner, cid, token), daemon=True, name=f"vnew-cast-{cid}")
        thread.start()
    except Exception:
        _unmark(owner, cid)
        raise


def choose(owner: str, cid: str, candidate_id: str) -> dict:
    """후보·이전 후보 중 하나를 chosen 으로(빈 값 = 고르기 풀기, 실사 정면 사용). 404 CandidateNotFound."""
    where = "vnew.casting.choose"
    require_enabled(where)
    wanted = candidate_id.strip() if isinstance(candidate_id, str) else ""

    def mutator(data: dict) -> None:
        cast = _ensure(data)
        if wanted and _find(cast, wanted) is None:
            raise VpocError(where, "CandidateNotFound", "고를 사진을 찾을 수 없습니다.", 404)
        cast["chosen"] = wanted

    char, _ = characters.mutate_char(owner, cid, mutator)
    return view(char)


def _photo(item: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "url": characters._presign(item.get("key")),  # noqa: SLF001 — 브라우저 표시용(모델에는 보내지 않는다)
        "preset": preset_of(item.get("preset")),
        "round": int(item.get("round") or 0),
        "createdAt": str(item.get("createdAt") or ""),
    }


def view(char) -> dict:
    """캐릭터 상세의 casting 보기(design §B.4). 설정이 꺼져 있으면 {enabled: false}.

    VNEW_BL_LOOK 켬이면 끝에 blDetected([{id, label}] — 사용자가 쓴 캐릭터 설명에서 찾은 BL 유형)·blHintsEn(지금 외모 문장·느낌·
    성별로 뽑기 문장에 더해질 참고 문장)을 더한다(지시서 104, 103 의 blRole·blType·blRoles·blTypes 는 없앴다). 설명에 유형 이름 밖의
    "떡대"가 있으면 그 뒤에 termsDetected([{id, label}], 지시서 112)를 더한다(없으면 키가 없다). 끔이면 이 기능 전과
    키가 같다. VNEW_CAST_PHOTO_CHECK 켬이면 맨 끝에 photoCheckNote(다시 뽑기 비용 안내 한 줄, 지시서 107)를 더한다.
    unitUsd·totalUsd 는 지금 고른 느낌의 금액이다. 아이돌풍 모델이 켜져 있으면 presets 항목마다 unitUsd·totalUsd 를 더한다(지시서 109,
    화면이 느낌을 바꾸면 금액을 바꾼다. 되돌리기 = 108 과 같은 보기).
    """
    if not settings.VNEW_CASTING:
        return {"enabled": False}
    cast = _casting_of(char)
    analysis = (char.get("analysis") if isinstance(char, dict) else None) or {}
    look = current_look(char)
    gender = str(cast.get("gender") or "") if isinstance(cast.get("gender"), str) else ""
    status = cast.get("status") if cast.get("status") in STATUSES else "none"
    preset = preset_of(cast.get("preset"))
    out = {
        "enabled": True,
        "preset": preset,
        "presets": _preset_items(),
        "gender": gender if gender in GENDERS else "",
        "genderAuto": auto_gender(analysis),
        "lookEn": look,
        "lookAuto": auto_look(char),
        "lookEdited": bool(cast.get("lookEdited") and clean_text(cast.get("lookEn"), LOOK_MAX)),
        "patternWarning": pattern_warning(look),
        "status": status,
        "error": clean_text(cast.get("error"), ERROR_MAX),
        "slots": [
            {"slot": int(slot.get("slot") or 0), "status": str(slot.get("status") or ""), "error": str(slot.get("error") or "")}
            for slot in cast["slots"]
        ],
        "unitUsd": preset_choice(preset)[1],
        "count": count(),
        "totalUsd": total_usd(preset),
        "candidates": [_photo(item) for item in cast["candidates"]],
        "history": [_photo(item) for item in cast["history"]],
        "chosen": str(cast.get("chosen") or ""),
        "chosenUrl": characters._presign(chosen_key(char)),  # noqa: SLF001
    }
    if settings.VNEW_BL_LOOK:
        bl_types = _bl_types_now(char)
        terms = _terms_now(char)
        out.update(
            blDetected=[{"id": type_id, "label": genre_bl.TYPES[type_id]["label"]} for type_id in bl_types],
            blHintsEn=_cast_text(look, cast.get("preset"), resolve_gender(cast, analysis), bl_types, terms)[1],
        )
        if terms:
            # 지시서 112(design D7): 설명에서 유형 이름 밖에 따로 찾은 일반 외모 용어("떡대"만). 찾았을 때만 둔다 — 떡대가 없으면 보기는
            # 112 전과 같다. 떡대공·떡대수의 떡대는 blDetected 가 보인다.
            out["termsDetected"] = [{"id": term_id, "label": web_terms.TERMS[term_id]["label"]} for term_id in terms]
    if settings.VNEW_CAST_PHOTO_CHECK:
        # 지시서 107(design D9): 총비용(totalUsd)은 그대로 두고, 드물게 더해지는 다시 뽑기 비용을 한 줄로 알린다. 끔이면 키가 없다.
        out["photoCheckNote"] = _MSG_PHOTO_NOTE
    return out


# ---------------------------------------------------------------- 이어하기(조회 때)


def _age_s(stamp: str) -> float | None:
    parsed = characters._parse_iso(stamp or "")  # noqa: SLF001
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _stale(cast: dict) -> bool:
    age = _age_s(cast.get("heartbeatAt") or cast.get("startedAt") or "")
    return age is None or age > settings.VNEW_CASTING_STALL_S


def _expired(cast: dict, poll_max: int) -> bool:
    age = _age_s(cast.get("startedAt") or "")
    return age is None or age > poll_max


def reconcile(owner: str, cid: str, char: dict) -> dict:
    """조회 때 호출: drawing 인데 하트비트가 VNEW_CASTING_STALL_S 보다 오래됐고 이 파드에서 돌지 않으면 이어 조회한다.

    새 claim 을 잡고(조건부 쓰기 — 여러 파드 중 하나만), 아직 제출하지 않은 칸(queued)은 다시 제출하지 않고 실패로 둔다
    (뜻밖의 과금 방지, RB4.2). 그 뒤 제출한 칸만 조회하는 run_draw 를 띄운다. 설정과 상관없이 끝까지 진행한다(R0.5).
    """
    cast = _casting_of(char)
    if cast.get("status") != "drawing" or _running(owner, cid) or not _stale(cast):
        return char
    token = secrets.token_hex(6)
    aborted = {"n": 0}

    def mutator(data: dict) -> None:
        current = _ensure(data)
        if current.get("status") != "drawing" or not _stale(current):
            raise _ClaimLost()  # 다른 파드가 먼저 이어받았거나 끝났다
        aborted["n"] = 0
        for slot in current["slots"]:
            if slot.get("status") == "queued":
                slot["status"] = "failed"
                slot["error"] = _MSG_ABORTED
                aborted["n"] += 1
        now = now_iso()
        current.update(claim=token, claimedAt=now, heartbeatAt=now)

    try:
        updated, _ = characters.mutate_char(owner, cid, mutator)
    except _ClaimLost:
        return char
    log.warning("vnew.casting.resume cid=%s queued_failed=%d", cid, aborted["n"])
    _spawn(owner, cid, token)
    return updated


# ---------------------------------------------------------------- 뽑기 스레드


def _load_cast(owner: str, cid: str, token: str) -> dict:
    char, _ = characters.load_char(owner, cid)
    cast = _casting_of(char)
    if cast.get("claim") != token or cast.get("status") != "drawing":
        raise _ClaimLost()
    return cast


def _mutate_cast(owner: str, cid: str, token: str, change):
    """claim 을 쥐고 있을 때만 casting 을 고친다(하트비트 갱신 포함). 아니면 _ClaimLost(쓰지 않는다)."""
    out = {}

    def mutator(data: dict) -> None:
        cast = _ensure(data)
        if cast.get("claim") != token or cast.get("status") != "drawing":
            raise _ClaimLost()
        cast["heartbeatAt"] = now_iso()
        out["value"] = change(cast)

    characters.mutate_char(owner, cid, mutator)
    return out.get("value")


def _set_slot(cast: dict, index: int, **patch) -> None:
    for slot in cast["slots"]:
        if int(slot.get("slot") or 0) == index:
            slot.update(patch)


def _slot_error(exc: BaseException) -> str:
    """칸 실패 문구(한국어). 4xx 거절은 캐스팅용 문구 + fal 상세 요지, 그 밖은 원문 요지(URL 쿼리·data URI·키 제거)."""
    if isinstance(exc, VpocError):
        if exc.kind in vpoc_fal._REJECT_HTTP:  # noqa: SLF001
            gist = error_gist(exc.message, _REJECT_GIST_MAX)
            return f"{_MSG_REJECTED} (fal: {gist})" if gist else _MSG_REJECTED
        return error_gist(exc.message, 200) or exc.kind
    return "사진을 만들지 못했습니다: " + (error_gist(str(exc), 200) or type(exc).__name__)


def _fail_slot(owner: str, cid: str, token: str, index: int, message: str, kind: str) -> None:
    log.warning("vnew.casting.slot_failed cid=%s slot=%d kind=%s", cid, index, kind)
    _mutate_cast(owner, cid, token, lambda cast: _set_slot(cast, index, status="failed", error=message[:ERROR_MAX]))


def _submit_slot(owner: str, cid: str, token: str, index: int, prompt: str, model: str, size: tuple[int, int]) -> None:
    """칸 하나 제출. 본문은 정확히 {prompt, num_images, image_size} — 이미지·URL 을 보내지 않는다(RB3.1)."""
    _load_cast(owner, cid, token)  # 유료 제출 직전 claim 다시 확인
    body = {"prompt": prompt, "num_images": 1, "image_size": {"width": size[0], "height": size[1]}}
    try:
        result = _fal_request("POST", f"{vpoc_settings.FAL_QUEUE_URL}/{model}", body)
        request_id = str((result or {}).get("request_id") or "") if isinstance(result, dict) else ""
        if not _REQUEST_ID.fullmatch(request_id):
            raise VpocError("vnew.casting", "NoRequestId", "fal이 요청 ID를 주지 않았습니다.", 502)
    except Exception as exc:  # noqa: BLE001 — 제출 실패는 그 칸만 실패(다시 제출하지 않는다: 이중 과금 방지)
        _fail_slot(owner, cid, token, index, _slot_error(exc), getattr(exc, "kind", type(exc).__name__))
        return
    arn = f"{vpoc_fal.ARN_PREFIX}{model}/{request_id}"
    _mutate_cast(owner, cid, token, lambda cast: _set_slot(cast, index, status="submitted", requestArn=arn))
    log.info("vnew.casting.submitted cid=%s slot=%d request=%s model=%s", cid, index, request_id, model)


def _image_url(result) -> str:
    images = result.get("images") if isinstance(result, dict) else None
    if isinstance(images, list) and images and isinstance(images[0], dict):
        url = images[0].get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return ""


def _to_jpeg(raw: bytes) -> bytes:
    """결과 사진 → RGB JPEG(긴 변 ≤1536, q90). 픽셀이 _MAX_PIXELS 를 넘으면 풀기 전에 거절한다."""
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if width * height > _MAX_PIXELS:
            raise ValueError(f"too many pixels: {width}x{height}")
        rgb = image.convert("RGB")
    rgb.thumbnail((_OUT_MAX_PX, _OUT_MAX_PX))
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=_OUT_QUALITY)
    return buffer.getvalue()


def _download(url: str) -> bytes:
    """결과 사진 받기 + JPEG 로 줄이기를 한 장씩(_PHOTO_SEM). 읽지 못하면 _BadImage, 받기 오류는 그대로 올린다."""
    with _PHOTO_SEM:
        raw = _fetch_url(url)
        try:
            return _to_jpeg(raw)
        except Exception as exc:  # noqa: BLE001
            raise _BadImage(str(exc)[:100]) from exc


def _charge(owner: str, cid: str, cand_id: str, model: str, usd: float) -> None:
    try:
        vpoc_ledger.charge(
            f"vnew-cast-{cid}-{cand_id}", kind="image", model=model, units=1, usd=usd,
            user=owner, project_id=cid, plan_id="casting",
        )
    except Exception as exc:  # noqa: BLE001 — 원장 실패가 사진 저장을 막지 않게(기존 _charge_seedream 과 같다)
        log.warning("vnew.casting.ledger_failed cid=%s err=%s", cid, error_gist(str(exc), 200))


# ---------------------------------------------------------------- 실사 사진 검사(지시서 107, cast-photo-guard design §3)
#
# characters._count_people·_inspect_view 와 같은 꼴(vpoc.llm._converse + 도구 1개). 사진 1장 = LLM 1회. 모델은 보이는 것(실사인지·
# 그림 종류)만 적고, 버릴지·다시 뽑을지는 _photo_passes 가 정한다. 모델 오류·도구 미사용·모양이 틀리면 None(사진을 버리지 않는다).

_PHOTO_CHECK_MAX_TOKENS = 400  # 5.x 모델이 도구 앞에 글을 쓰면 120 은 잘렸다(_count_people 과 같은 값)
_PHOTO_CHECK_MAX_PX = 1024  # 검사에 보낼 긴 변. 저장 JPEG(1536)를 줄여 이미지 입력 토큰을 약 절반으로
_PHOTO_STYLES = ("photo", "illustration", "webtoon", "anime", "cartoon", "painting", "3d-render", "other")
_PHOTO_TOOL = {
    "name": "emit_photo_check",
    "description": "Judge whether one image is a real camera photograph of a real person or a drawn or rendered picture.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "photo": {"type": "boolean"},
                "style": {"type": "string", "enum": list(_PHOTO_STYLES)},
            },
            "required": ["photo", "style"],
        }
    },
}
_PHOTO_SYSTEM = (
    "You check ONE generated portrait. photo is true only when it is a real photograph of a real person taken with a camera "
    "(real skin texture, real hair strands, camera lighting). photo is false when it is a drawing, illustration, webtoon or "
    "comic art, anime, cartoon, digital painting or 3D render, even when it is detailed or semi-realistic. style names what you "
    "see: 'photo', 'illustration', 'webtoon', 'anime', 'cartoon', 'painting', '3d-render' or 'other'. "
    "Answer only by calling the emit_photo_check tool exactly once."
)
_PHOTO_PROMPT = "Is this image a real photograph of a real person, or a drawing / illustration / webtoon / anime / 3D render?"


def _check_jpeg(jpeg: bytes) -> bytes:
    """검사에 보낼 JPEG: 긴 변이 _PHOTO_CHECK_MAX_PX 이하면 그대로, 넘으면 줄여 q85 로 다시 인코딩한다(메모리에서만)."""
    with Image.open(io.BytesIO(jpeg)) as image:
        if max(image.size) <= _PHOTO_CHECK_MAX_PX:
            return jpeg
        rgb = image.convert("RGB")
    rgb.thumbnail((_PHOTO_CHECK_MAX_PX, _PHOTO_CHECK_MAX_PX))
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _photo_check(jpeg: bytes) -> dict | None:
    """결과 사진 1장이 실사 사진인지 비전 LLM 1회로 묻는다. {photo: bool, style: _PHOTO_STYLES 중 하나}, 못 정하면 None.

    photo 는 bool 또는 글 "true"·"false" 만 받는다. 모르는 style 은 "other". 로그에는 오류 요지 200자만 남긴다(사진·문장 없음).
    줄이기(디코드)는 받기와 같은 _PHOTO_SEM 안에서 한 장씩 하고(512Mi 봉우리 그대로), LLM 호출은 그 밖에서 한다.
    """
    try:
        with _PHOTO_SEM:
            small = _check_jpeg(jpeg)
        blocks = [{"text": _PHOTO_PROMPT}, {"image": {"format": "jpeg", "source": {"bytes": small}}}]
        payload, _usage = vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다(characters 검사와 같다)
            system_text=_PHOTO_SYSTEM,
            blocks=blocks,
            tool=_PHOTO_TOOL,
            max_tokens=_PHOTO_CHECK_MAX_TOKENS,
            where="vnew.cast_photo_check",
            model=settings.VNEW_CAST_PHOTO_CHECK_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.cast.photo_check_failed err=%s", error_gist(str(exc), 200) or type(exc).__name__)
        return None
    raw = payload.get("photo") if isinstance(payload, dict) else None
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        raw = raw.strip().lower() == "true"
    if not isinstance(raw, bool):
        log.warning("vnew.cast.photo_check_failed err=bad_payload")
        return None
    style = str(payload.get("style") or "").strip().lower()
    return {"photo": raw, "style": style if style in _PHOTO_STYLES else "other"}


def _photo_passes(owner: str, cid: str, token: str, slot: dict, cand_id: str, model: str, unit_usd: float,
                  jpeg: bytes) -> bool:
    """받은 사진을 후보로 넣어도 되는지(design §3.2). 실사이거나 검사가 None(fail-open, 경고 로그)이면 True.

    그림체면 그 사진은 메모리에서만 쓰고 버린다(S3 저장 없음). 이미 fal 이 만든 1장이라 원장에는 같은 키 규칙(후보 id)으로 1건 남긴다.
    그 칸의 다시 제출이 _REDRAW_MAX 보다 적으면 같은 문장으로 다시 제출하도록 queued 로 되돌리고(redraws + 1), 아니면 그 칸만
    실패(NotPhoto)로 끝낸다. 다른 칸은 건드리지 않는다.
    """
    index = int(slot.get("slot") or 0)
    redraws = int(slot.get("redraws") or 0)
    check = _photo_check(jpeg)
    if check is None:
        log.warning("vnew.cast.photo_check slot=%d photo=none style=- redraw=%d cid=%s", index, redraws, cid)
        return True
    log.log(logging.INFO if check["photo"] else logging.WARNING, "vnew.cast.photo_check slot=%d photo=%s style=%s redraw=%d cid=%s",
            index, str(check["photo"]).lower(), check["style"], redraws, cid)
    if check["photo"]:
        return True
    _charge(owner, cid, cand_id, model, unit_usd)
    if redraws >= _REDRAW_MAX:
        _fail_slot(owner, cid, token, index, _MSG_NOT_PHOTO, "NotPhoto")
        return False

    def again(cast: dict) -> None:
        _set_slot(cast, index, status="queued", requestArn="", candidateId="", error="", redraws=redraws + 1)

    _mutate_cast(owner, cid, token, again)
    log.info("vnew.cast.redraw cid=%s slot=%d redraws=%d", cid, index, redraws + 1)
    return False


def _poll_slot(owner: str, cid: str, token: str, slot: dict, unit_usd: float, *, photo_check: bool = False) -> None:
    """제출한 칸 하나 조회. 진행 중·일시 오류면 그대로(다음 순회), 완료면 사진을 옮기고 원장 1건, 거절·결과 없음이면 실패.

    photo_check(VNEW_CAST_PHOTO_CHECK, 지시서 107)가 참이면 저장 전에 _photo_passes 를 지난다. 거짓이면 107 전과 같다.
    """
    index = int(slot.get("slot") or 0)
    endpoint, request_id = vpoc_fal._parse_arn(str(slot.get("requestArn") or ""))  # noqa: SLF001
    if not endpoint or not _REQUEST_ID.fullmatch(request_id):
        _fail_slot(owner, cid, token, index, _MSG_NO_IMAGE, "BadArn")
        return
    try:
        status = _fal_request("GET", characters._status_url(endpoint, request_id))  # noqa: SLF001
        state = str((status or {}).get("status") or "").upper() if isinstance(status, dict) else ""
        if state in ("IN_QUEUE", "IN_PROGRESS"):
            return
        if state != "COMPLETED":
            detail = error_gist(json.dumps(status, ensure_ascii=False, default=str), 200)
            _fail_slot(owner, cid, token, index, "fal이 사진을 만들지 못했습니다: " + detail, state or "NoStatus")
            return
        result = _fal_request("GET", characters._response_url(endpoint, request_id))  # noqa: SLF001
        url = _image_url(result)
        if not url:
            _fail_slot(owner, cid, token, index, _MSG_NO_IMAGE, "NoImage")
            return
        jpeg = _download(url)
    except _ClaimLost:
        raise
    except _BadImage:
        _fail_slot(owner, cid, token, index, _MSG_BAD_IMAGE, "BadImage")
        return
    except Exception as exc:  # noqa: BLE001
        if characters._is_transient_3d_error(exc):  # noqa: SLF001 — 5xx·429·연결·시간초과는 다음 순회에 다시
            log.warning("vnew.casting.transient cid=%s slot=%d kind=%s", cid, index, getattr(exc, "kind", type(exc).__name__))
            return
        _fail_slot(owner, cid, token, index, _slot_error(exc), getattr(exc, "kind", type(exc).__name__))
        return
    cand_id = "k_" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:10]
    if photo_check and not _photo_passes(owner, cid, token, slot, cand_id, endpoint, unit_usd, jpeg):
        return
    key = characters.casting_key(owner, cid, cand_id)
    characters.STORE.put_bytes(key, jpeg, "image/jpeg")
    _charge(owner, cid, cand_id, endpoint, unit_usd)

    def add(cast: dict) -> None:
        if not any(item.get("id") == cand_id for item in cast["candidates"]):
            cast["candidates"].append({
                "id": cand_id, "key": key, "preset": _prompt_preset(cast.get("prompt") or ""),
                "round": int(cast.get("round") or 0), "createdAt": now_iso(),
            })
        _set_slot(cast, index, status="ready", candidateId=cand_id, error="")

    _mutate_cast(owner, cid, token, add)
    log.info("vnew.casting.saved cid=%s slot=%d cand=%s bytes=%d", cid, index, cand_id, len(jpeg))


def _finish(owner: str, cid: str, token: str, *, timed_out: bool = False) -> None:
    """끝내기: 남은 칸 실패(시간 초과) → ready(후보 ≥ 1) / failed, 오류 요약, 고른 사진이 없으면 첫 후보, claim 해제."""

    def done(cast: dict) -> tuple[int, int]:
        for slot in cast["slots"]:
            if slot.get("status") in ("queued", "submitted"):
                slot["status"] = "failed"
                slot["error"] = _MSG_TIMEOUT if timed_out else (slot.get("error") or _MSG_NO_IMAGE)
        failed = [slot for slot in cast["slots"] if slot.get("status") == "failed"]
        ready = len(cast["candidates"])
        cast["status"] = "ready" if ready else "failed"
        cast["error"] = ""
        if failed:
            first = str(failed[0].get("error") or _MSG_NO_IMAGE)
            cast["error"] = f"사진 {len(cast['slots'])}장 중 {len(failed)}장을 만들지 못했습니다: {first}"[:ERROR_MAX]
        if not cast.get("chosen") and cast["candidates"]:
            cast["chosen"] = cast["candidates"][0]["id"]
        cast["claim"] = ""
        cast["claimedAt"] = ""
        return ready, len(failed)

    ready, failed = _mutate_cast(owner, cid, token, done)
    log.info("vnew.casting.done cid=%s status=%s ready=%d failed=%d timeout=%s",
             cid, "ready" if ready else "failed", ready, failed, str(timed_out).lower())


def run_draw(owner: str, cid: str, token: str) -> None:
    """뽑기 스레드: queued 칸 제출 → submitted 칸 조회(5초 간격) → 모두 끝나거나 POLL_MAX 가 지나면 끝내기.

    설정은 시작 때 한 번 읽는다. 모델·장당 금액·크기는 설정이 아니라 회차에 남긴 값(_round_choice, 지시서 109)이라 재시작 뒤 이어받아도
    같다. claim 을 잃으면(다른 실행이 이어받음) 쓰지 않고 접는다. 뜻밖의 예외는 상태를 그대로 두고
    끝낸다 — 하트비트가 멈추면 조회 이어하기가 제출한 칸을 다시 조회하고, POLL_MAX 가 끝을 보장한다.
    """
    started = time.time()
    _mark(owner, cid)
    poll_max = int(settings.VNEW_CASTING_POLL_MAX_S)
    interval = max(0, int(settings.POLL_INTERVAL_S))
    photo_check = bool(settings.VNEW_CAST_PHOTO_CHECK)  # 지시서 107. 다른 설정처럼 이 실행이 시작할 때 한 번 읽는다
    try:
        while True:
            cast = _load_cast(owner, cid, token)
            if _expired(cast, poll_max):
                _finish(owner, cid, token, timed_out=True)
                return
            model, unit_usd, size = _round_choice(cast)
            for slot in cast["slots"]:
                if slot.get("status") == "queued":
                    _submit_slot(owner, cid, token, int(slot.get("slot") or 0), str(cast.get("prompt") or ""), model, size)
            cast = _load_cast(owner, cid, token)
            for slot in cast["slots"]:
                if slot.get("status") == "submitted":
                    _poll_slot(owner, cid, token, slot, unit_usd, photo_check=photo_check)
            cast = _load_cast(owner, cid, token)
            if not any(slot.get("status") in ("queued", "submitted") for slot in cast["slots"]):
                _finish(owner, cid, token)
                return
            _mutate_cast(owner, cid, token, lambda _cast: None)  # 하트비트
            _sleep(interval)
    except _ClaimLost:
        log.info("vnew.casting.claim_lost cid=%s", cid)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.casting.run_failed cid=%s err=%s", cid, error_gist(str(exc), 200))
    finally:
        _unmark(owner, cid)
        log.info("vnew.casting.run_end cid=%s ms=%d", cid, int((time.time() - started) * 1000))


def parse_confirm_usd(value) -> float:
    """라우트 본문 confirmUsd → float. 없거나 숫자가 아니면 400 NoConfirmUsd."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise VpocError("vnew.casting.draw", "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400)
    try:
        number = float(value)
    except ValueError as exc:
        raise VpocError("vnew.casting.draw", "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400) from exc
    if not math.isfinite(number):
        raise VpocError("vnew.casting.draw", "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400)
    return number
