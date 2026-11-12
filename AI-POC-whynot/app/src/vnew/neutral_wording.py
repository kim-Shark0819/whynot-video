"""작업 233 — 콘텐츠 검사 다시 내기의 입맞춤 · 입술 · 신음 글 → 중립 연출(LLM 없음, 결정적, 두 번 써도 같다).

fal 콘텐츠 검사는 부정한 키스 낱말("No kiss: lips never touch.", "nothing between their lips")에도 거절한다. 다시 내는 프롬프트
(⑦ 다시 만들기 195 순화 · 232 패널 없이, 본 생성 콘텐츠 사다리 3단계 이상)만 이 함수를 지난다. 처음 제출은 쓴 글 그대로다.

- 부정만 하는 절(no kiss · lips do not meet · a small gap between mouths · nothing between their lips)은 뺀다.
- "the kiss just ended" · "after the kiss" · "just before the kiss" → "faces close together".
- "kisses him" · "presses his lips to …" · "lips meet" → "leans in close, faces close together".
- "lips (slightly) parted / apart" → "mouth slightly open", "away from both mouths" → "resting on the cheek".
- "(e.g. a face turned toward camera in a kiss)" → "(e.g. a face turned away)".
- 숨 · 신음 글(small uneven breaths, catches his breath, gasps, moans) → "breathes softly".
- 대사: 신음 같은 한국어 감탄(하읏 · 하아 · 흐읏 · 읏 …)만 든 따옴표 대사 조각 · 줄은 뺀다. 다른 대사는 둔다.
- 그대로 두는 것: @표시 · 숫자 · 시간 "(0-3s)" · 카메라 · 샷 크기 · MANDATORY / 화풍 / look / Setting / @Video 줄, 닿는 연기 안전 글
  (palm away from the throat …), 말하기 입 모양 글(lip sync · lips closed · lips do not move).
- 앞 순화(generate._soften_text 센 순화)가 남긴 글("No faces close", "faces close just ended", "just before faces close")도 고친다.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 지키는 줄 · 지키는 말
_PROTECTED_LINE = re.compile(
    r"^\s*(?:MANDATORY\b|STYLE\b|LOOK\b|SETTING\b|Setting\b|ASSETS\b|LOCATION\b|COSTUME\b|@Video\d"
    r"|Korean webtoon art style|Cinematic live-action|Photoreal live-action|Modern TV-anime)"
    r"|\(keep exactly this look\)")
# 말하기 입 모양 지시(속마음 · 립싱크)는 입맞춤 글이 아니다 — 가려 두고 끝에 되돌린다.
_SPEECH_SAFE = re.compile(
    r"\blip[- ]?sync\w*|\blip\s+movements?\b|\b(?:mouth\s+or\s+)?(?:lips|mouths?)\s+(?:(?:stay|stays|are|kept|remain|remains)\s+)?"
    r"closed\b|\blips\s+(?:do\s+not\s+|don't\s+|never\s+)?mov\w*|\blips\s+move\b", re.IGNORECASE)
# 절을 뺄 때 지킬 말(@표시 · 숫자 · Shot 머리). 이런 절은 빼지 않고 낱말만 바꾼다.
_KEEP_TOKEN = re.compile(r"@\w|\d|\bShot\b")

# ---------------------------------------------------------------- 찾는 말
INTIMATE_RE = re.compile(r"\bkiss\w*|\blips?\b|\bmouths\b|\bmak(?:e|es|ing)\s+out\b|\bmoan\w*|입맞|키스|입술|뽀뽀", re.IGNORECASE)
_NEG = re.compile(r"\b(?:no|not|never|nothing|without|nor|neither|gap|width)\b|n't\b", re.IGNORECASE)
_RESIDUE = re.compile(r"\bfaces\s+(?:close|stay\s+very\s+close)\b", re.IGNORECASE)  # 앞 순화가 키스를 바꾼 말

FACES = "faces close together"
LEAN = {"": "lean in close", "s": "leans in close", "es": "leans in close", "ed": "leaned in close", "ing": "leaning in close",
        "e": "lean in close"}
BREATHE = {"": "breathe softly", "s": "breathes softly", "es": "breathes softly", "ed": "breathed softly",
           "ing": "breathing softly", "caught": "breathed softly"}
_NAME = r"(?-i:[A-Z][\w-]*)"  # 이름(대문자로 시작) — 규칙은 대소문자를 가리지 않지만 이름 자리는 가린다
_OWN = r"(?:his|her|their|the|" + _NAME + r"['\u2019]s)"
_ADJ = r"(?:(?:deep|soft|gentle|long|quick|first|brief|light|tender|passionate|french|stolen|sudden|small|little)\s+)?"
_ADV = r"(?:\s+(?:softly|gently|deeply|tenderly|lightly|slowly|passionately|briefly|quickly|quietly|faintly|again))?"
_PART = (r"(?:lips|mouth|neck|nape|throat|ears?|earlobes?|cheeks?|forehead|hands?|fingers?|collarbones?|chest|shoulders?|hair|"
         r"temple|jaw(?:line)?|chin|nose|eyelids?|wrists?|skin|face)")
_KISS_OBJ = (r"(?:" + _OWN + r"\s+(?:\w+\s+)?" + _PART + r"(?:\s+and\s+(?:" + _OWN + r"\s+)?" + _PART + r")?"
             r"|each\s+other|him|her|them|" + _NAME + r")")
_BREATHS = r"(?:(?:small|short|uneven|shallow|ragged|quick|heavy|little),?\s+(?:and\s+)?)+breaths\b"


def _tense(table: dict, key: str | None) -> str:
    return table.get((key or "").lower(), table[""])


def _their_lean(match: re.Match) -> str:
    own = (match.group("own") or "").lower()
    return ("they lean in close, " if own == "their" else "leans in close, ") + FACES


# Phase A — 구절 규칙(차례대로). 바꿀 말이 함수면 match 를 받는다. 첫 글자 대소문자는 원문을 따른다.
_PHRASE_RULES: tuple = tuple((re.compile(p, re.IGNORECASE), r) for p, r in (
    # 그림 이름표 괄호
    (r"\(\s*e\.g\.[^()]*\b(?:kiss\w*|faces\s+close)[^()]*\)", "(e.g. a face turned away)"),  # faces close = 앞 순화가 남긴 말
    # 키스가 끝난 · 앞뒤
    (r"\b(?:the|their|a)\s+" + _ADJ + r"kiss(?:\s+(?:has|had|is|was|just))*\s+(?:ended|ends|finished|over|broken|broke|breaks)\b",
     FACES),
    (r"\b(?:(?:just|right|shortly|moments?|seconds?)\s+)?(?:after|before)\s+(?:the|their|a|his|her)\s+" + _ADJ + r"kiss(?:es)?\b",
     FACES),
    (r"\bfaces\s+close(?:\s+(?:has|had|is|was|just))*\s+(?:ended|ends|finished|over)\b", FACES),  # 앞 순화가 남긴 말
    (r"\b(?:(?:just|right|shortly)\s+)?(?:after|before)\s+faces\s+close\b(?!\s+together)", FACES),
    # 입술을 대다 · 키스하다(대상 있음) · 입술이 닿다
    (r"\b(?:press(?P<t>es|ed|ing)?)\s+(?:his|her|their)\s+lips\s+(?:to|against|on(?:to)?)\s+"
     r"(?:" + _OWN + r"\s+\w+|his|hers|theirs|him|her|them|" + _NAME + r")" + _ADV,
     lambda m: _tense(LEAN, m.group("t")) + ", " + FACES),
    (r"\bkiss(?P<t>es|ed|ing)?\s+" + _KISS_OBJ + r"\b" + _ADV, lambda m: _tense(LEAN, m.group("t")) + ", " + FACES),
    (r"\b(?:(?P<own>their|his|her|" + _NAME + r"['\u2019]s)\s+)?lips\s+(?:(?:almost|nearly|gently|softly|finally|briefly)\s+)?"
     r"(?:meet|meets|met|meeting|touch|touches|touched|touching|lock|locks|locked|locking|brush|brushes|brushed|brushing)\b"
     r"(?:\s+(?:together|again))?(?:\s+" + _OWN + r"(?:\s+lips)?)?", _their_lean),
    (r"\bmak(?P<t>e|es|ing)\s+out\b", lambda m: _tense(LEAN, m.group("t")) + ", " + FACES),
    # 입술이 벌어진 · 떨어진
    (r"\btheir\s+(?:lips|mouths)\s+(?:(?:are|stay|remain|still|already|just|slightly|now)\s+)*(?:apart|parted|separated|separate)\b",
     FACES),
    (r"\b(?:" + _OWN + r"\s+)?(?:lips|mouths)\s+(?:(?:are|stay|remain|still|already|just|now)\s+)*(?:a\s+few|a\s+couple\s+of|\d+)\s+"
     r"(?:centimet(?:re|er)s|inches|cm)\s+apart\b", FACES),
    (r"\b(?:their\s+)?mouths\s+(?:(?:are|stay|very|so|almost|nearly|now)\s+)*close\b(?:\s+together)?", FACES),
    (r"\b(?P<own>(?:his|her|the|" + _NAME + r"['\u2019]s)\s+)?lips\s+(?:(?:are|stay|remain|remains|still|already|just|now|kept)\s+)*"
     r"(?:slightly\s+|a\s+little\s+|softly\s+|gently\s+)?(?:parted|apart|part|parting|open|opened|separated)\b",
     lambda m: (m.group("own") or "") + "mouth slightly open"),
    (r"\b(?:(?:slightly|softly|gently)\s+)?parted\s+lips\b", "a slightly open mouth"),
    # 손 위치
    (r"\baway\s+from\s+(?:both|their|the|his|her)\s+(?:mouths|lips|mouth)\b", "resting on the cheek"),
    (r"\s*,?\s*\b(?:near|by|at|against|beside|below|under)\s+(?:the|his|her)\s+(?:lower\s+|upper\s+|bottom\s+)?lip\b", ""),
    # 숨 · 신음
    (r"\b(?:catch(?P<t>es|ing)?|(?P<c>caught))\s+(?:his|her|their)\s+breath\b(?:\s+(?:in|with)\s+" + _BREATHS + r")?",
     lambda m: _tense(BREATHE, "caught" if m.group("c") else m.group("t"))),
    (r"\b(?:(?:takes|draws|takes in)\s+)?(?:(?:in|with)\s+)?" + _BREATHS, "breathes softly"),
    (r"\b(?:a|one)\s+(?:(?:soft|small|low|quiet|faint|sharp|little)\s+)?(?:gasp|moan)\b", "a soft breath"),
    (r"\b(?:gasp|moan)(?P<t>s|ing|ed)?\b(?:\s+for\s+(?:air|breath))?" + _ADV, lambda m: _tense(BREATHE, m.group("t"))),
    (r"\bpanting\b", "breathing softly"),
))
# Phase C — 부정 절을 뺀 뒤 남은 키스 낱말(대상 없는 동사 · 명사).
_KISS_RULES: tuple = tuple((re.compile(p, re.IGNORECASE), r) for p, r in (
    (r"\b(?:in|into|for|with)\s+(?:a|the|their|his|her)\s+" + _ADJ + r"kiss(?:es)?\b", "with " + FACES),
    (r"\bshar(?P<t>e|es|ed|ing)\s+(?:a|the|their)\s+" + _ADJ + r"kiss(?:es)?\b",
     lambda m: _tense(LEAN, {"e": "", "es": "s", "ed": "ed", "ing": "ing"}[m.group("t").lower()]) + ", " + FACES),
    (r"\b(?:a|the|their|his|her|one)\s+" + _ADJ + r"kiss(?:es)?\b", FACES),
    (r"\bkiss(?P<t>es|ed|ing)?\b" + _ADV, lambda m: _tense(LEAN, m.group("t"))),
))
# 절을 뺄 수 없을 때(@표시 · 숫자가 든 절) 낱말만.
_WORD_RULES: tuple = tuple((re.compile(p, re.IGNORECASE), r) for p, r in (
    (r"\bmak(?:e|es|ing)\s+out\b", "stay close"), (r"\bmoan\w*", "soft breath"), (r"\blips\b", "mouth"), (r"\blip\b", "mouth"),
    (r"\bmouths\b", "faces"),
))
_KO_RULES = (("입맞춤", "포옹"), ("입맞췄", "안았"), ("입맞춰", "안아"), ("입맞추", "안아 주"), ("키스", "포옹"), ("뽀뽀", "포옹"),
             ("입술", "입"))
# 신음 같은 한국어 감탄: 이 글자만(문장부호 · 빈칸 빼고), 그중 하나는 꼭.
_MOAN_SYL = frozenset("하아흐읏흣앗앙으읍흡핫")
_MOAN_MUST = frozenset("읏흐흣앙읍흡")
_MOAN_PUNCT = re.compile(r"[\s.,!?~\u2026\u00b7\u2661\u2665\u2014\u2013\-\"'\u201c\u201d]+")
_MOAN_PIECE = re.compile(
    r"(?<![^\s;:.,/(])(?P<who>[^\s;:.,\"()/\n][^;:.,\"()/\n]{0,60}?(?:[ \t]*\([^()\n]{0,160}\))?"
    r"(?:,[ \t]*spoken[ \t]*\([^()\n]{0,80}\))?:[ \t]*)?[\"\u201c](?P<q>[^\"\u201c\u201d\n]{1,40})[\"\u201d]")
_LABEL_ONLY = re.compile(r"^\s*(?:[A-Z][^:\n\d@]{0,60}:)?\s*$")  # "DIALOGUE:" · "Mina:" 만 남은 줄(숫자 · @ 가 든 머리는 아니다)
_SEP_RANK = {",": 1, ":": 2, ";": 3}
_REPLACEMENTS = frozenset(s.casefold() for s in (FACES, "resting on the cheek", "breathes softly", "mouth slightly open"))


def is_moan(text: str) -> bool:
    """신음 같은 한국어 감탄만 든 글인가(하읏… · 하아 · 흐읏 · 읏). "응." · "아…" 같은 대답 · 감탄은 아니다."""
    core = _MOAN_PUNCT.sub("", str(text or ""))
    if not core or len(core) > 12 or not set(core) <= _MOAN_SYL:
        return False
    return bool(set(core) & _MOAN_MUST) or "하아" in core or "하앗" in core


def _case_like(src: str, value: str) -> str:
    return value[:1].upper() + value[1:] if src[:1].isupper() and value[:1].islower() else value


def _apply(rules, text: str) -> str:
    for pattern, repl in rules:
        def inner(match: re.Match, repl=repl) -> str:
            value = repl(match) if callable(repl) else repl
            if not value:
                return value
            src = match.group(0).lstrip(" ,")
            return _case_like(src, value)
        text = pattern.sub(inner, text)
    return text


def _split(text: str, seps: str, sentence: bool) -> list[tuple[str, str]]:
    """괄호 · 따옴표 밖에서 나눈다. [(조각, 뒤 구분자)]. sentence 면 [.!?] + 빈칸(구분자 = 문장부호 + 빈칸)."""
    out: list[tuple[str, str]] = []
    depth, quote, start, i, n = 0, False, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"\u201c\u201d":
            quote = not quote if ch == '"' else ch == "\u201c"
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth = max(0, depth - 1)
        elif not quote and depth == 0 and ch in seps and i + 1 < n and text[i + 1] in " \t":
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if sentence:
                out.append((text[start:i + 1], text[i + 1:j]))
            else:
                out.append((text[start:i], text[i:j]))
            start = i = j
            continue
        i += 1
    out.append((text[start:], ""))
    return out


def _clause(text: str) -> str | None:
    """절 하나(Phase A 뒤). 키스 · 입술 말이 든 부정 절은 None(뺀다). 남은 키스 낱말은 바꾸고, 그래도 남으면 None(지킬 말이 있으면 낱말만)."""
    if not text.strip():
        return text
    hit = INTIMATE_RE.search(text)
    if (hit or _RESIDUE.search(text)) and _NEG.search(text) and not _KEEP_TOKEN.search(text):
        return None
    if not hit:
        return text
    out = _apply(_KISS_RULES, text)
    if not INTIMATE_RE.search(out):
        return out
    if _KEEP_TOKEN.search(out):
        return _apply(_WORD_RULES, out)
    return None


def _sentence(text: str) -> str | None:
    """문장 하나. 바뀐 것이 없으면 같은 글자, 다 빠지면 None."""
    body, end = text, ""
    m = re.search(r"[.!?]+$", body)
    if m and not body.rstrip().endswith(("\"", "\u201d")):
        body, end = body[:m.start()], m.group(0)
    parts = _split(body, ",;:", sentence=False)
    kept: list[list[str]] = []  # [절, 뒤 구분자]
    changed = False
    for at, (piece, sep) in enumerate(parts):
        new = _clause(piece)
        if new != piece:
            changed = True
        if new is None or not new.strip():
            # 빠진 절: 앞 절의 뒤 구분자를 둘 중 센 것으로
            if kept and sep and _SEP_RANK.get(sep.strip(), 0) > _SEP_RANK.get(kept[-1][1].strip(), 0):
                kept[-1][1] = sep
            continue
        lower = new.strip().casefold()
        if lower in _REPLACEMENTS and any(lower in k[0].casefold() for k in kept):
            changed = True
            if kept and sep and _SEP_RANK.get(sep.strip(), 0) > _SEP_RANK.get(kept[-1][1].strip(), 0):
                kept[-1][1] = sep
            continue
        if at > 0 and not kept and parts[0][0][:1].isupper() and new[:1].islower():
            new = new[:1].upper() + new[1:]
        kept.append([new, sep])
    if not changed:
        return text
    if not kept:
        return None
    kept[-1][1] = ""
    return "".join(c + s for c, s in kept).rstrip(" \t,;:") + end


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\([ \t]*\)", "", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])[ \t]*(?=[.;,])", "", text)
    text = re.sub(r"\.(?:[ \t]+\.)+", ".", text)
    text = re.sub(r":[ \t]*/[ \t]*", ": ", text)
    text = re.sub(r"(?:[ \t]*/[ \t]*){2,}", " / ", text)
    text = re.sub(r"[ \t]*/[ \t]*$", "", text)
    text = re.sub(r"^[ \t]*/[ \t]*", "", text)
    text = re.sub(r"\b(breath\w* softly)(?:,?\s+and\s+breath\w* softly)+", r"\1", text)
    return text.strip()


def _line(line: str) -> str | None:
    """줄 하나. 지키는 줄 · 바뀔 말이 없는 줄은 같은 글자. 신음만 든 대사 줄 · 다 빠진 줄은 None."""
    if not line.strip() or _PROTECTED_LINE.search(line):
        return line
    if is_moan(line):
        return None
    saved: list[str] = []

    def hide(match: re.Match) -> str:
        saved.append(match.group(0))
        return "\x01" + chr(0xE000 + len(saved) - 1) + "\x02"  # 숫자 없는 자리표(지킬 말 \d 에 걸리지 않게)

    work = _SPEECH_SAFE.sub(hide, line)
    removed_moan = False

    def drop_moan(match: re.Match) -> str:
        nonlocal removed_moan
        if is_moan(match.group("q")):
            removed_moan = True
            return ""
        return match.group(0)

    work = _MOAN_PIECE.sub(drop_moan, work)
    for old, new in _KO_RULES:
        work = work.replace(old, new)
    work = _apply(_PHRASE_RULES, work)
    lead = re.match(r"^[ \t]*", work).group(0)
    pieces = _split(work[len(lead):], ".!?", sentence=True)
    out: list[str] = []
    for piece, gap in pieces:
        new = _sentence(piece)
        if new is None:
            if out and not gap:
                out[-1] = out[-1].rstrip()
            continue
        if new != piece and out and new[:1].islower() and out[-1].rstrip().endswith((".", "!", "?")):
            new = new[:1].upper() + new[1:]
        out.append(new + gap)
    result = lead + "".join(out)
    for at, value in enumerate(saved):
        result = result.replace("\x01" + chr(0xE000 + at) + "\x02", value)
    if result == line:
        return line
    result = _tidy(result)
    if not result.strip() or (_LABEL_ONLY.match(result) and not _LABEL_ONLY.match(line)):
        return None
    result = result.rstrip(" \t,;:")
    if line.rstrip()[-1:] in ".!?\"\u201d)" and result and result[-1:] not in ".!?\"\u201d)\u2026":
        result += "."
    return result


def intimacy_neutral(text: str) -> str:
    """작업 233: 입맞춤 · 입술 · 신음 글을 중립 연출로(줄마다). 바뀔 말이 없으면 같은 글자, 두 번 써도 같다."""
    value = str(text or "")
    if not value or not (INTIMATE_RE.search(value) or _RESIDUE.search(value) or re.search(r"[\uac00-\ud7a3]", value)
                         or re.search(r"\b(?:breath|gasp|panting)", value, re.IGNORECASE)):
        return value
    lines = value.split("\n")
    out: list[str] = []
    for line in lines:
        new = _line(line)
        if new is not None:
            out.append(new)
    return "\n".join(out)
