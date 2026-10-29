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
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, error_gist, now_iso
from . import characters
from . import settings
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
# 외모 문장 상한(참고 구현 vpoc/live_service.py:294 LOOK_MAX, design §B.1 "≤300").
LOOK_MAX = 300
# 이번 회차 실제 문장 상한(design §B.1 prompt ≤1500).
PROMPT_MAX = 1500


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


def base_text(preset, gender) -> str:
    """프리셋 문구(성별 낱말 표 적용). male 이면 참고 구현 원문과 같다."""
    return _BASES[preset_of(preset)].format(**_WORDS[_gender(gender)])


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
# 눈 색(웹툰의 회색·파란 눈). 캐스팅 문장 끝 "with dark brown eyes" 와 부딪히지 않게 색 낱말만 뺀다.
_EYE_COLOR = re.compile(
    r"\b(?:(?:light|pale|bright|deep|dark|striking|vivid)\s+)?"
    r"(?:(?:gr[ae]y|blue|green|red|golden|gold|amber|violet|purple|pink|silver|hazel|brown|black|teal|aqua|yellow|orange)-)?"
    r"(?:gr[ae]y|blue|green|red|golden|gold|amber|violet|purple|pink|silver|hazel|brown|black|teal|aqua|yellow|orange)"
    r"(?:[- ](?:colou?red|tinted))?\s+(?=eyes?\b)",
    re.IGNORECASE,
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
_PREFIX_AGE = re.compile(r"\b(?:young|youthful|middle-aged|elderly|old|older|teenage|adult|aged|mature)\b", re.IGNORECASE)
_PREFIX_GENDER = re.compile(r"\b(?:fe)?male\b", re.IGNORECASE)
_TEXT_AGE = re.compile(r"\s*\bin\s+(?:his|her|their)\s+(?:(?:early|mid|late)[- ])?(?:\d0s|teens)\b", re.IGNORECASE)
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


def _no_drawing(text) -> str:
    """그림 낱말을 지운다(fx.strip_drawing_words). 'webtoon-style' 은 '-style' 까지, 그래도 'webtoon' 조각이 남으면(webtoonish 등) 그 조각도 지운다."""
    out = re.sub(r"\b(?:web-?toons?|anime|manga|cartoons?|illustrat\w*|drawn)[- ]style\b", " ", str(text or ""),
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


def look_sentence(appearance_en, desc_ko, gender, outfit_en="") -> str:
    """분석 외모 문장 → 실사 캐스팅 외모 문장(≤300자, 끝 마침표 없음).

    순서: 옷 덧붙임(", wearing {baseOutfitEn}") → 그림 낱말 제거 → 표정 조각·눈 색 제거 → 주어 "a Korean {명사} {나이}"
    → 성별 낱말 맞춤 → 검은 머리가 아니면 "dyed" → 문장 사이 마침표를 쉼표로 → 300자 단어 경계에서 자름.
    """
    gender = _gender(gender)
    text = clean_text(appearance_en, 600)
    outfit = clean_text(outfit_en, 400).rstrip(" .")
    if outfit:
        text = (text.rstrip(" .") + ", wearing " + _lower_article(outfit)) if text else "wearing " + _lower_article(outfit)
    text = _no_drawing(text)
    text = _EXPRESSION.sub("", text)
    text = _EYE_COLOR.sub("", text)
    # 색만 있던 눈("with blue eyes and …")은 색을 빼면 낱말 "eyes" 만 남는다 — 그 조각도 뺀다.
    text = re.sub(r"\bwith eyes and\s+", "with ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:,\s*|\s+and\s+|\s+with\s+)eyes\b(?=\s*(?:[,.;]|$))", "", text, flags=re.IGNORECASE)
    text = _tidy(text)
    qual_decade = _age_parts(desc_ko)
    age = age_phrase(desc_ko, gender)
    if age:
        text = _TEXT_AGE.sub("", text)
    words = _WORDS[gender]
    found = _SUBJECT.search(text)
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
        text = f"{article} {head}{text[found.end():]}"
    else:
        lead = f"a Korean {words['noun']}" + (" " + age if age else "")
        rest = text[:1].lower() + text[1:] if text[:1].isupper() and not text[:2].isupper() else text
        text = lead + ((" " if rest.lower().startswith(("with ", "wearing ")) else ", ") + rest if rest else "")
    text = align_gender(text, gender)
    text = _mark_dyed(text, qual_decade[1])
    text = re.sub(r"\.\s+(?=\S)", ", ", text)
    return _clip_words(_tidy(text.rstrip(" .")), LOOK_MAX)


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


def cast_prompt(look_en, preset, gender) -> str:
    """프리셋 문구 + " {He is|She is|They are} {외모}, with dark brown eyes." (≤1500자)."""
    gender = _gender(gender)
    words = _WORDS[gender]
    look = prompt_look(look_en, gender) or f"a Korean {words['noun']}"
    text = f"{base_text(preset, gender)} {words['subj']} {look}, with dark brown eyes."
    return text[:PROMPT_MAX]



# ================================================================ 뽑기(B2, design §B.3·§B.4)
#
# start_draw 가 확인 금액·진행 중 여부를 보고 casting 을 drawing 으로 바꾼 뒤 백그라운드 스레드(run_draw)를 띄운다.
# 여러 파드 중복은 casting.claim(조건부 쓰기, mutate_char)으로, 같은 파드 중복은 _CAST_RUNNING 으로 막는다.
# 캐릭터 status·variants·시트·3D 는 건드리지 않는다(casting 키만 고친다, RB1.2·RB1.3).
#
# 과금: 만든 사진 1장마다 원장 1건(키 vnew-cast-{cid}-{후보 id}). 후보 id 는 fal 요청 id 에서 정해지므로, 파드가 죽어
# 같은 요청을 다시 받아도 같은 S3 키·같은 원장 키라 두 번 계상하지 않는다. fal 이 결과를 주지 않은 칸은 과금하지 않는다.

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
        "preset": DEFAULT_PRESET, "gender": "", "lookEn": "", "lookEdited": False,
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


def total_usd() -> float:
    """뽑기 1회 금액 = 장당 VNEW_CASTING_USD × 장 수(소수 2자리, 결정 1)."""
    return round(count() * float(settings.VNEW_CASTING_USD), 2)


def _image_size() -> tuple[int, int]:
    found = re.fullmatch(r"\s*(\d{3,4})\s*[xX]\s*(\d{3,4})\s*", str(settings.VNEW_CASTING_SIZE or ""))
    if found:
        width, height = int(found.group(1)), int(found.group(2))
        if 256 <= width <= 4096 and 256 <= height <= 4096:
            return width, height
    return 1920, 2560


def _apply_settings(cast: dict, preset, gender, look_en, where: str) -> None:
    """들어온 값만 정리해 casting 에 둔다(None 이면 그대로). 400 BadPreset·BadGender·BadLook."""
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
    """느낌·성별·외모 문장 저장(PUT /casting). 빈 외모 문장은 자동 문장으로 되돌린다. 캐스팅 보기를 돌려준다."""
    where = "vnew.casting"
    require_enabled(where)

    def mutator(data: dict) -> None:
        _apply_settings(_ensure(data), preset, gender, look_en, where)

    char, _ = characters.mutate_char(owner, cid, mutator)
    return view(char)


def start_draw(owner: str, cid: str, confirm_usd: float, *, preset=None, gender=None, look_en=None) -> dict:
    """뽑기 시작(POST /casting/draw). 404 CastingDisabled · 409 FalKeyMissing·CostMismatch·CastingRunning·NoLook · 400 형식.

    이전 후보는 history 로 옮기고(최신 먼저 12개), 고른 사진(chosen)은 그대로 둔다(결정 5). 캐스팅 보기를 돌려준다.
    """
    where = "vnew.casting.draw"
    require_enabled(where)
    if not vpoc_settings.FAL_KEY:
        raise VpocError(where, "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    _apply_settings(_empty_casting(), preset, gender, look_en, where)  # 형식만 먼저 본다
    slots = count()
    expected = total_usd()
    shown = round(float(confirm_usd), 2)
    if shown != expected:
        raise VpocError(where, "CostMismatch", f"금액이 서버 계산과 다릅니다. 서버 {expected} USD, 화면 {shown} USD.", 409)
    token = secrets.token_hex(6)

    def mutator(data: dict) -> None:
        cast = _ensure(data)
        if cast.get("status") == "drawing":
            # 멈춘 뽑기는 조회 때 이어하기가 끝낸다(시작부터 최대 VNEW_CASTING_POLL_MAX_S).
            raise VpocError(where, "CastingRunning", "이미 실사 캐스팅 사진을 만들고 있습니다.", 409)
        _apply_settings(cast, preset, gender, look_en, where)
        look = current_look(data)
        if not look:
            raise VpocError(where, "NoLook", "먼저 캐릭터를 만들어 분석해 주세요.", 409)
        gender_now = resolve_gender(cast, data.get("analysis"))
        now = now_iso()
        cast["history"] = _trim_history(cast["candidates"] + cast["history"], str(cast.get("chosen") or ""))
        cast["candidates"] = []
        cast["round"] = int(cast.get("round") or 0) + 1
        cast["slots"] = [
            {"slot": index, "status": "queued", "requestArn": "", "candidateId": "", "error": ""} for index in range(slots)
        ]
        cast.update(
            status="drawing", error="", usd=expected, claim=token, claimedAt=now, startedAt=now, heartbeatAt=now,
            prompt=cast_prompt(look, cast["preset"], gender_now),
        )

    char, _ = characters.mutate_char(owner, cid, mutator)
    cast = _casting_of(char)
    log.info(
        "vnew.casting.draw_start cid=%s round=%d slots=%d usd=%.2f preset=%s gender=%s",
        cid, int(cast.get("round") or 0), slots, expected, cast.get("preset"), resolve_gender(cast, char.get("analysis")),
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
    """캐릭터 상세의 casting 보기(design §B.4). 설정이 꺼져 있으면 {enabled: false}."""
    if not settings.VNEW_CASTING:
        return {"enabled": False}
    cast = _casting_of(char)
    analysis = (char.get("analysis") if isinstance(char, dict) else None) or {}
    look = current_look(char)
    gender = str(cast.get("gender") or "") if isinstance(cast.get("gender"), str) else ""
    status = cast.get("status") if cast.get("status") in STATUSES else "none"
    return {
        "enabled": True,
        "preset": preset_of(cast.get("preset")),
        "presets": [dict(item) for item in PRESETS],
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
        "unitUsd": float(settings.VNEW_CASTING_USD),
        "count": count(),
        "totalUsd": total_usd(),
        "candidates": [_photo(item) for item in cast["candidates"]],
        "history": [_photo(item) for item in cast["history"]],
        "chosen": str(cast.get("chosen") or ""),
        "chosenUrl": characters._presign(chosen_key(char)),  # noqa: SLF001
    }


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
    log.info("vnew.casting.submitted cid=%s slot=%d request=%s", cid, index, request_id)


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


def _poll_slot(owner: str, cid: str, token: str, slot: dict, unit_usd: float) -> None:
    """제출한 칸 하나 조회. 진행 중·일시 오류면 그대로(다음 순회), 완료면 사진을 옮기고 원장 1건, 거절·결과 없음이면 실패."""
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

    설정은 시작 때 한 번 읽는다. claim 을 잃으면(다른 실행이 이어받음) 쓰지 않고 접는다. 뜻밖의 예외는 상태를 그대로 두고
    끝낸다 — 하트비트가 멈추면 조회 이어하기가 제출한 칸을 다시 조회하고, POLL_MAX 가 끝을 보장한다.
    """
    started = time.time()
    _mark(owner, cid)
    model = settings.VNEW_CASTING_MODEL
    unit_usd = float(settings.VNEW_CASTING_USD)
    size = _image_size()
    poll_max = int(settings.VNEW_CASTING_POLL_MAX_S)
    interval = max(0, int(settings.POLL_INTERVAL_S))
    try:
        while True:
            cast = _load_cast(owner, cid, token)
            if _expired(cast, poll_max):
                _finish(owner, cid, token, timed_out=True)
                return
            for slot in cast["slots"]:
                if slot.get("status") == "queued":
                    _submit_slot(owner, cid, token, int(slot.get("slot") or 0), str(cast.get("prompt") or ""), model, size)
            cast = _load_cast(owner, cid, token)
            for slot in cast["slots"]:
                if slot.get("status") == "submitted":
                    _poll_slot(owner, cid, token, slot, unit_usd)
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
