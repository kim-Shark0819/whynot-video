"""vnew 만화 기호·분위기(vnew-live-fx 묶음 A, 2026-10-30) — 순수 함수만 둔다.

네트워크·S3·LLM 을 쓰지 않는다. plan.py(저장·채우기), generate.py(영상 프롬프트), 검사가 이 모듈을 읽는다.

  - 기호 열거값·문장은 참고 구현(video-vibecode vpoc/compose.py:329-336)과 글자 하나 다르지 않게 둔다.
  - 실사 표현은 참고 구현 vpoc/live.py:19-25 그대로다. 배경 기호는 빈 값이다.
  - 컷 필드: fx(적용 0~2) · fxSuggest(추천 0~2, 키가 있으면 채운 컷) · fxDrawn(패널에 그려진 기호 0~5) ·
    moodKo(60자) · moodEn(80자). 모르는 기호·중복은 버리고, 글은 제어 문자·줄바꿈을 한 칸으로 바꾼 뒤 자른다.
"""

from __future__ import annotations

import json
import re
import unicodedata

# ================================================================ 기호 표

FX_KEYS = ("tear streams", "sweat drop", "blush lines", "pink sparkle background", "speed lines")
# 참고 구현 vpoc/compose.py:329-336 그대로(글자 하나 다르지 않게).
FX_SENTENCE = {
    "tear streams": "Webtoon-style tear streams run down the cheeks",
    "sweat drop": "A single anime sweat drop beside the head, not an anger mark",
    "blush lines": "Pink webtoon blush lines on the cheeks",
    "pink sparkle background": "A soft pink sparkle background behind the characters",
    "speed lines": "Manga speed lines in the background",
}
# 바이트가 모자랄 때만 쓰는 짧은 형(신규). sweat drop 은 핏대 사고 때문에 "not an anger mark" 를 남긴다.
FX_SHORT = {
    "tear streams": "tear streams",
    "sweat drop": "one sweat drop, not an anger mark",
    "blush lines": "pink blush lines",
    "pink sparkle background": "pink sparkle background",
    "speed lines": "speed lines",
}
# 참고 구현 vpoc/live.py:19-25 그대로.
REAL_FX = {
    "blush lines": "a faint natural flush on the cheeks",
    "tear streams": "tears run down the cheeks",
    "sweat drop": "a bead of sweat on the temple",
    "speed lines": "",
    "pink sparkle background": "",
}
# 화면 라벨(vnew.js FX_LABEL_KO 와 같은 글자, fx-l 이 비교한다).
FX_LABEL_KO = {
    "tear streams": "눈물 줄기",
    "sweat drop": "땀 한 방울",
    "blush lines": "볼 홍조",
    "pink sparkle background": "분홍 반짝이 배경",
    "speed lines": "속도선",
}
FX_FIELDS = ("fx", "fxSuggest", "fxDrawn", "moodKo", "moodEn")
FX_MAX, DRAWN_MAX, MOOD_KO_MAX, MOOD_EN_MAX, SUGGEST_CAP, FILL_MAX_CUTS = 2, 5, 60, 80, 3, 40

# 한글(음절·자모·호환 자모). 영상 프롬프트에 한글이 남았는지 볼 때 쓴다.
HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")

# 실사 문장에서 지우는 그림 낱말(묶음 C, 참고 구현 vpoc/live.py:133 규칙 확장).
_DRAWING_WORDS = re.compile(r"\b(?:web-?toons?|anime|manga|cartoons?|illustrat\w*|drawn)\b", re.IGNORECASE)

# 채우기 1회 금액 추정(화면 확인창용, Claude 결정 1). 실제 금액은 원장(사용 토큰)이다.
# 입력: SYSTEM·머리 ≈ 1,000 + 컷 줄 ≈ 80/컷 + 패널 이미지 ≈ 1,600/장(design §A.3: 20장 ≈ 32K).
# 출력: 도구 앞 글 ≈ 500 + 채울 컷 ≈ 140 + 영어 옮기기 컷 ≈ 40.
EST_BASE_IN, EST_CUT_IN, EST_PANEL_IN = 1000, 80, 1600
EST_BASE_OUT, EST_FILL_OUT, EST_TRANSLATE_OUT = 500, 140, 40


# ================================================================ 정리


def fx_list(value, limit: int) -> list[str]:
    """기호 목록을 정리한다. 문자열이면 한 원소 목록. 소문자·공백 정리, none·빈 값·모르는 기호·중복을 버리고 limit 개."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if len(out) >= max(0, int(limit)):
            break
        if not isinstance(item, str):
            continue
        key = " ".join(item.lower().split())
        if key in FX_KEYS and key not in out:
            out.append(key)
    return out


def clean_text(value, limit: int) -> str:
    """제어·서식 문자와 줄바꿈을 한 칸으로 바꾸고 공백을 합친 뒤 limit 자에서 자른다(끝 공백 제거). 글이 아니면 ""."""
    if not isinstance(value, str):
        return ""
    chars = []
    for ch in value:
        category = unicodedata.category(ch)
        chars.append(" " if category.startswith("C") or category in ("Zl", "Zp") else ch)
    text = " ".join("".join(chars).split())
    return text[: max(0, int(limit))].rstrip()


def normalize_cut_fx(cut: dict) -> dict:
    """컷 사본을 돌려준다. FX_FIELDS 중 들어온 키만 정리하고 없는 키는 만들지 않는다(멱등)."""
    out = dict(cut)
    if "fx" in out:
        out["fx"] = fx_list(out.get("fx"), FX_MAX)
    if "fxSuggest" in out:
        out["fxSuggest"] = fx_list(out.get("fxSuggest"), FX_MAX)
    if "fxDrawn" in out:
        out["fxDrawn"] = fx_list(out.get("fxDrawn"), DRAWN_MAX)
    if "moodKo" in out:
        out["moodKo"] = clean_text(out.get("moodKo"), MOOD_KO_MAX)
    if "moodEn" in out:
        out["moodEn"] = clean_text(out.get("moodEn"), MOOD_EN_MAX)
    return out


def strip_fx(cut: dict) -> dict:
    """기호·분위기 키를 뺀 컷 사본."""
    return {key: value for key, value in (cut or {}).items() if key not in FX_FIELDS}


def only_fx_changed(old_cuts: list, new_cuts: list) -> bool:
    """두 컷 목록이 기호·분위기 필드 말고는 같으면 True(아무것도 안 바뀐 경우 포함)."""

    def dump(cuts) -> str:
        rows = [strip_fx(cut) if isinstance(cut, dict) else cut for cut in (cuts or [])]
        return json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)

    return dump(old_cuts) == dump(new_cuts)


# ================================================================ 채우기 대상


def is_empty(cut: dict) -> bool:
    """빈 컷: fxSuggest 키가 없고, fx 가 비고, moodKo 가 비었다(채우기 대상)."""
    if not isinstance(cut, dict):
        return False
    return (
        "fxSuggest" not in cut
        and not fx_list(cut.get("fx"), FX_MAX)
        and not clean_text(cut.get("moodKo"), MOOD_KO_MAX)
    )


def needs_translation(cut: dict) -> bool:
    """번역 대상 컷: moodKo 는 있는데 moodEn 이 비었다."""
    if not isinstance(cut, dict):
        return False
    return bool(clean_text(cut.get("moodKo"), MOOD_KO_MAX)) and not clean_text(cut.get("moodEn"), MOOD_EN_MAX)


def fill_targets(cuts: list) -> tuple[list[str], list[str]]:
    """(빈 컷 id — 앞에서 FILL_MAX_CUTS 개, 번역 대상 id). id 가 없는 컷은 건너뛴다."""
    empty: list[str] = []
    translate: list[str] = []
    for cut in cuts or []:
        if not isinstance(cut, dict):
            continue
        cid = str(cut.get("id") or "")
        if not cid:
            continue
        if is_empty(cut):
            if len(empty) < FILL_MAX_CUTS:
                empty.append(cid)
        elif needs_translation(cut):
            translate.append(cid)
    return empty, translate


def estimate_tokens(panels: int, fill: int, translate: int, cuts: int) -> tuple[int, int]:
    """채우기 1회 (입력, 출력) 토큰 추정. 대상이 없으면 (0, 0)."""
    if fill <= 0 and translate <= 0:
        return 0, 0
    tokens_in = EST_BASE_IN + EST_CUT_IN * max(0, int(cuts)) + EST_PANEL_IN * max(0, int(panels))
    tokens_out = EST_BASE_OUT + EST_FILL_OUT * max(0, int(fill)) + EST_TRANSLATE_OUT * max(0, int(translate))
    return tokens_in, tokens_out


def estimate_usd(panels: int, fill: int, translate: int, cuts: int, *, usd_in_per_mtok: float, usd_out_per_mtok: float) -> float:
    """채우기 1회 금액 추정(USD, 소수 2자리, 대상이 있으면 최소 0.01)."""
    tokens_in, tokens_out = estimate_tokens(panels, fill, translate, cuts)
    if not tokens_in and not tokens_out:
        return 0.0
    usd = tokens_in / 1_000_000 * float(usd_in_per_mtok) + tokens_out / 1_000_000 * float(usd_out_per_mtok)
    return max(0.01, round(usd, 2))


# ================================================================ 합치기 · 한도


def merge_fill(cuts: list, items: list, empty_ids: list, sent_mood: dict) -> tuple[list[dict], dict]:
    """모델 결과(items)를 지금 컷에 합친다(design §A.3). (새 cuts, {filled, translated, missed, filledIds}).

    - FILL 컷(empty_ids 이고 지금도 빈 컷): drawn = drawnFx, 추천 = drawn + fx + fxSuggest 중복 제거 앞 2개,
      fx = 모델 fx 중 추천 안의 것, moodKo·moodEn 은 clean_text. 결과에 그 id 가 없으면 그대로 두고 missed.
    - TRANSLATE 컷(sent_mood 에 있음): 지금 moodKo 가 보낸 값과 같고 moodEn 이 빌 때만 moodEn 을 넣는다.
    - 그 사이 빈 컷이 아니게 됐거나 moodKo 가 바뀐 컷, 다른 키·다른 컷은 그대로 둔다.
    """
    by_id: dict[str, dict] = {}
    for item in items or []:
        if isinstance(item, dict):
            cid = str(item.get("id") or "").strip()
            if cid and cid not in by_id:
                by_id[cid] = item
    fill_set = {str(cid) for cid in empty_ids or []}
    sent = {str(cid): str(mood) for cid, mood in (sent_mood or {}).items()}
    stats = {"filled": 0, "translated": 0, "missed": 0, "filledIds": []}
    out: list[dict] = []
    for cut in cuts or []:
        if not isinstance(cut, dict):
            out.append(cut)
            continue
        cut = dict(cut)
        cid = str(cut.get("id") or "")
        if cid in fill_set and is_empty(cut):
            item = by_id.get(cid)
            if item is None:
                stats["missed"] += 1
            else:
                drawn = fx_list(item.get("drawnFx"), DRAWN_MAX)
                applied = fx_list(item.get("fx"), FX_MAX)
                suggest: list[str] = []
                for key in drawn + applied + fx_list(item.get("fxSuggest"), FX_MAX):
                    if key not in suggest:
                        suggest.append(key)
                suggest = suggest[:FX_MAX]
                cut["fxDrawn"] = drawn
                cut["fxSuggest"] = suggest
                cut["fx"] = [key for key in applied if key in suggest]
                cut["moodKo"] = clean_text(item.get("moodKo"), MOOD_KO_MAX)
                cut["moodEn"] = clean_text(item.get("moodEn"), MOOD_EN_MAX)
                stats["filled"] += 1
                stats["filledIds"].append(cid)
        elif cid in sent:
            current = clean_text(cut.get("moodKo"), MOOD_KO_MAX)
            if current and current == sent[cid] and not clean_text(cut.get("moodEn"), MOOD_EN_MAX):
                item = by_id.get(cid)
                english = clean_text(item.get("moodEn"), MOOD_EN_MAX) if item else ""
                if english:
                    cut["moodEn"] = english
                    stats["translated"] += 1
                else:
                    stats["missed"] += 1
        out.append(cut)
    return out, stats


def apply_cap(cuts: list, filled_ids: list) -> tuple[list[dict], int]:
    """같은 기호 추천을 콘티 전체에서 SUGGEST_CAP 컷까지로 줄인다(design §A.3, Claude 결정 3). (새 cuts, 뺀 추천 수).

    먼저 이번에 채우지 않은 컷의 fxSuggest·fxDrawn 과 채운 컷의 fxDrawn 을 센다. 그다음 채운 컷을 컷 순서대로 보며
    그 컷에 그려진 기호는 그대로 두고(이미 셌다), 그려지지 않은 기호는 이미 한도면 fxSuggest·fx 에서 빼고 아니면 센다.
    사용자가 정한 컷(채우지 않은 컷)은 한도를 넘어도 고치지 않는다.
    """
    filled = {str(cid) for cid in filled_ids or []}
    counts = {key: 0 for key in FX_KEYS}
    for cut in cuts or []:
        if not isinstance(cut, dict):
            continue
        seen = set(fx_list(cut.get("fxDrawn"), DRAWN_MAX))
        if str(cut.get("id") or "") not in filled:
            seen.update(fx_list(cut.get("fxSuggest"), FX_MAX))
        for key in seen:
            counts[key] += 1
    capped = 0
    out: list[dict] = []
    for cut in cuts or []:
        if not isinstance(cut, dict) or str(cut.get("id") or "") not in filled:
            out.append(cut)
            continue
        cut = dict(cut)
        drawn = set(fx_list(cut.get("fxDrawn"), DRAWN_MAX))
        keep: list[str] = []
        for key in fx_list(cut.get("fxSuggest"), FX_MAX):
            if key in drawn:
                keep.append(key)
            elif counts[key] >= SUGGEST_CAP:
                capped += 1
            else:
                counts[key] += 1
                keep.append(key)
        cut["fxSuggest"] = keep
        cut["fx"] = [key for key in fx_list(cut.get("fx"), FX_MAX) if key in keep]
        out.append(cut)
    return out, capped


# ================================================================ 문장


def sentences(keys, *, real: bool = False, short: bool = False) -> list[str]:
    """기호 문장 목록. real 이면 실사 표현(빈 값 제외), short 이면 짧은 형."""
    out: list[str] = []
    for key in fx_list(keys, FX_MAX):
        if real:
            text = REAL_FX.get(key) or ""
        else:
            text = (FX_SHORT if short else FX_SENTENCE).get(key) or ""
        if text:
            out.append(text)
    return out


def has_content(entries: list) -> bool:
    """어느 항목에 기호(fx)나 분위기(moodEn)가 있으면 True(줄이기 단계를 더할지)."""
    return any(isinstance(entry, dict) and (entry.get("fx") or entry.get("moodEn")) for entry in entries or [])


def strip_drawing_words(text) -> str:
    """그림 낱말(webtoon·anime·manga·cartoon·illustrated·drawn)을 지우고 남은 쉼표·겹공백을 정리한다(묶음 C)."""
    out = _DRAWING_WORDS.sub(" ", str(text or ""))
    out = re.sub(r"(?<![\w])-(?=[A-Za-z])", "", out)  # "webtoon-style" 을 지우고 남은 "-style" 의 붙임표
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", out)
    out = " ".join(out.split())
    return out.strip(" ,;:")
