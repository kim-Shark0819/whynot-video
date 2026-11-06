"""vnew 영상만들기_신규 — 시스템 프롬프트 · 도구 스키마(설계자 2026-10-20).

두 단계의 LLM 호출을 위한 프롬프트와 vpoc.llm._converse 가 쓸 도구 스키마를 모아 둔다.

  ② 대본(script): 웹툰 컷을 그대로 옮긴 대사·상황 로그. 배경·장소는 그려진 그대로 적고
     추측해 바꾸지 않는다(원작은 밀폐된 교장실인데 AI 가 개방된 복도로 바꾼 사고 재발 방지).
  ③ 콘티(conti): 대본 + 입력(총 길이·부가 설명·캐릭터)으로 드라마 컷을 쓴다. 최소 5컷,
     컷 seconds 합은 totalSeconds 가 되게 맞춘다.

plan.py 가 이 상수들을 읽어 _converse(system_text=..., tool=...) 로 호출한다. 호출·모델 체인·
broker 는 vpoc.llm 이 그대로 맡는다(여기서는 프롬프트·스키마만 둔다).
"""

from __future__ import annotations

import re

# 기호 열거값은 순수 모듈 fx.py 한 곳에서 읽는다(네트워크·S3 없음).
from .fx import DRAWN_MAX, FX_KEYS, FX_MAX, MOOD_EN_MAX, MOOD_KO_MAX, PERSON_FX

# 작업 127: 기호마다 대상 인물({인물 표현 기호: [이름]}). 콘티·채우기 도구가 같은 모양을 쓴다.
FX_WHO_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "array", "items": {"type": "string"}} for key in PERSON_FX},
}
# 대상 인물 규칙(콘티·채우기 시스템 문장이 같이 쓴다).
FX_WHO_RULE = (
    "fxWho: for each applied or suggested person symbol (" + " | ".join(PERSON_FX) + ") list who it belongs to, "
    "with the character names exactly as given. Name only the characters the symbol is actually drawn on in the panels. "
    "If the panels do not show it, use the character whose emotionKo or situationKo in the script entries for this "
    "cut's panels states that feeling. If you still cannot tell, leave that symbol out of fxWho. Background symbols "
    "(pink sparkle background, speed lines) never go in fxWho."
)
# 컷 분위기 규칙 1문장(작업 127): 그 컷의 감정만, 짧은 반응·코믹한 순간은 가벼운 톤.
MOOD_CUT_RULE = (
    "Write this cut's own feeling, not the whole scene's: for a short reaction (e.g. \"어?\", \"!?\") or a comic "
    "moment write a light tone such as \"comic beat — puzzled blink\", and do not carry over the scene-wide "
    "feeling (flutter, tension)."
)

# 작업 162: 손 주인 규칙 1개(콘티 CONTI_SYSTEM · 158 샷 계획 사용자 글 · 148 패널 연출 문법 SYSTEM · 콘티 연기 다시 읽기가 같은 글을 쓴다).
# test3 cut-03: 범건우는 화면 밖이고 손만 들어오는데, 보이는 사람이 여의준 하나라 모든 손을 여의준 손으로 읽었다.
HAND_OWNER_RULE = (
    "A hand or arm entering the frame from an edge, or whose sleeve/cuff does not match the visible person's outfit, "
    "belongs to ANOTHER character. Decide whose hand it is by sleeve, cuff, skin, watch and outfit, using the outfit notes "
    "given for each character. Never describe it as the visible person's own hand. If its owner is off-frame, say so: "
    "'<Owner>'s hand (off-frame) …'."
)
# 손만 보이는 화면 밖 인물의 콘티 positionEn(정확히 이 글). generate 가 이 값으로 "손만 들어온다" 절을 만든다.
OFF_FRAME_HAND_POSITION = "off-frame, hand only"
# 콘티 characters[] 에 손 주인을 적는 방법(CONTI_SYSTEM 규칙 13 · 콘티 연기 다시 읽기).
HAND_OWNER_CONTI_RULE = (
    "When only the hand or arm of a character is in the frame, also list that character in characters, with positionEn \""
    + OFF_FRAME_HAND_POSITION + "\" and an actionEn for the hand only (e.g. \"<Owner>'s hand (off-frame) strokes "
    "<Visible>'s cheek.\"). The visible person's actionEn describes only what that person does."
)
# 158 샷 계획(VIBE_CUTS_SYSTEM 은 참고 글 그대로 둔다)에는 사용자 글 끝에 이 줄을 붙인다.
VIBE_HAND_OWNER_NOTE = (
    "Hands: " + HAND_OWNER_RULE + " In a shot where only such a hand of an off-frame character shows, keep that "
    "character out of the shot's characters and write the hand in actionEn as \"<Owner>'s hand (off-frame) …\"."
)
# 인물별 옷·소매 메모 머리줄(호출마다 "- 이름: 옷" 줄이 뒤따른다). 메모가 없으면 이 줄도 없다.
OUTFIT_NOTES_HEAD = "Outfit notes per character (use the sleeves, cuffs, skin, watch and clothes to tell whose hand is whose):"


def outfit_notes_text(notes) -> str:
    """{이름: 옷} → OUTFIT_NOTES_HEAD + "- 이름: 옷" 줄들. 비면 ""(호출 글이 지금과 같다)."""
    rows = [(str(k).strip(), " ".join(str(v or "").split())) for k, v in (notes or {}).items()]
    rows = [(k, v) for k, v in rows if k and v]
    if not rows:
        return ""
    return OUTFIT_NOTES_HEAD + "\n" + "\n".join(f"- {k}: {v}" for k, v in rows)


_OFF_FRAME_RE = re.compile(r"\boff[\s-]?(?:frame|screen)\b", re.IGNORECASE)


def is_off_frame(position) -> bool:
    """콘티 positionEn 이 화면 밖(손만)을 뜻하는지(off-frame · off frame · offscreen)."""
    return bool(_OFF_FRAME_RE.search(str(position or "")))


# 작업 162 fix1: 콘티 컷마다 그 장면의 모든 인물과 그 사람이 하는 일(화면 밖이어도). presence 값·손 짧은 표기(148 낱말).
from .previz_schema import HAND_ACTS as _HAND_ACTS, HAND_TARGETS as _HAND_TARGETS  # noqa: E402

PRESENCE_VALUES = ("on_screen", "hand_only", "voice_only", "off_screen")
# 손을 쓰는 동작(쉬는 손 rest·none 은 적지 않는다).
CAST_HAND_ACTS = tuple(a for a in _HAND_ACTS if a not in ("rest", "none"))
CAST_HAND_TARGETS = tuple(_HAND_TARGETS)
CAST_CHAR_SCHEMA = {
    "type": "object",
    "required": ["name", "presence", "actionEn"],
    "properties": {
        "name": {"type": "string"},
        "presence": {"type": "string", "enum": list(PRESENCE_VALUES)},
        "positionEn": {"type": "string"},
        "actionEn": {"type": "string"},
        "actionKo": {"type": "string"},
        # 손마다 "act>target"(예: "touch>other_cheek"). 쉬는 손은 뺀다.
        "hands": {"type": "object", "properties": {"left": {"type": "string"}, "right": {"type": "string"}}},
    },
}
CAST_RULE = (
    "characters lists EVERY character who is in this scene (this location) in every cut, not only the visible ones. "
    "For each: presence = on_screen (body or face in the frame) | hand_only (only a hand or arm enters the frame) | "
    "voice_only (not in the frame, but speaks) | off_screen (not in the frame and silent); actionEn = what THIS person "
    "does in this cut, also when off screen (e.g. \"watches Yeouijun silently\", \"reaches in and covers Yeouijun's eyes "
    "with his right hand\"); actionKo = a short Korean version of actionEn; hands = which of the person's OWN hands "
    "touches what, as \"act>target\" per hand, act: " + " | ".join(CAST_HAND_ACTS) + ", target: "
    + " | ".join(CAST_HAND_TARGETS) + " (own_* = the person's own body, other_* = the body of the person they touch), "
    "e.g. {\"right\": \"touch>other_cheek\"}; leave resting hands out. Whoever's hand touches, holds or covers something "
    "has presence on_screen or hand_only, never off_screen. " + HAND_OWNER_RULE
)

# ================================================================ ② 대본(script)

# 지시서 117: 대사 목소리 종류. spoken = 입을 움직여 말함(기본), thought = 속마음(입 다문 채 voice-over).
VOICE_VALUES = ("spoken", "thought")


def is_thought(entry) -> bool:
    """대본 줄·대사 귀속 줄이 속마음(thought)인지. 판정은 이 함수 한 곳에서만 한다(지시서 117).

    voice == "thought"(대소문자·공백 무시) 또는 thought is True 면 참. 값이 없거나 다른 값이면 spoken(거짓).
    """
    if not isinstance(entry, dict):
        return False
    return str(entry.get("voice") or "").strip().lower() == "thought" or entry.get("thought") is True


EMIT_SCRIPT = {
    "name": "emit_script",
    "description": "Return the dialogue-and-situation log read straight from the webtoon panels.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["setting", "entries"],
            "properties": {
                "setting": {
                    "type": "object",
                    "required": ["locationKo", "locationEn", "enclosed"],
                    "properties": {
                        # 장소는 웹툰에 그려진 그대로. 추측해 바꾸지 않는다.
                        "locationKo": {"type": "string"},
                        "locationEn": {"type": "string"},
                        # 닫힌 방이면 true, 열린 공간이면 false.
                        "enclosed": {"type": "boolean"},
                        "timeKo": {"type": "string"},
                        "moodKo": {"type": "string"},
                        # 장면 전체의 공간 배치(누가 어디에 서/앉아 있는지).
                        "layoutKo": {"type": "string"},
                        "layoutEn": {"type": "string"},
                    },
                },
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "panels", "lineKo", "situationKo"],
                        "properties": {
                            "id": {"type": "string"},
                            # 이 항목이 걸친 컷(타일) 번호들(0부터, order 순).
                            "panels": {"type": "array", "items": {"type": "integer"}},
                            "speaker": {"type": "string"},
                            # 말풍선 대사를 정확히 옮긴다(OCR). 대사가 없으면 빈 문자열.
                            "lineKo": {"type": "string"},
                            # 지시서 117: 꼬리 없는 둥근·구름·점선 말풍선 = thought(속마음, 입 다문 채 목소리만).
                            # 꼬리가 화자를 가리키는 보통 말풍선 = spoken. 없으면 spoken 으로 읽는다(is_thought).
                            "voice": {"type": "string", "enum": list(VOICE_VALUES)},
                            "situationKo": {"type": "string"},
                            "emotionKo": {"type": "string"},
                            # 이 대사·상황이 일어나는 장소(그 컷에 그려진 그대로). 웹툰이 장소를 바꾸지
                            # 않으면 setting 과 같은 값. 바뀌면(예: 뒤쪽 컷이 노을 진 거리) 그 장소를 적는다.
                            "locationKo": {"type": "string"},
                            "locationEn": {"type": "string"},
                            "characters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        # 인물이 같은 자리에 있으면 '같은 자리'로 적는다.
                                        "positionKo": {"type": "string"},
                                        "positionEn": {"type": "string"},
                                        "actionKo": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


# 지시서 117 규칙 9(말풍선 모양 → voice) 문구. 대본 분석(SCRIPT_SYSTEM)과 속마음 찾기(VOICES_SYSTEM, 118b)가 이 한 곳을 같이 쓴다.
VOICE_RULE = """a line in a round or cloud-shaped speech bubble with no tail, or in a dotted bubble, is the
   character's inner thought: set voice to "thought" (heard as their voice, mouth stays closed). A normal
   bubble whose tail points at the speaker is "spoken" (the default). Keep square narration boxes as you
   handle them now."""


SCRIPT_SYSTEM = """You are a drama scripter who transfers a Korean webtoon to screen EXACTLY as drawn.
You are given the webtoon panels in story order (tiles cut from vertical strips). Read them and write a
dialogue-and-situation log, cut by cut, by calling the emit_script tool exactly once.

Rules you must follow:
1. Copy the speech-bubble dialogue precisely (OCR). Put it in lineKo in Korean, as written. If a panel
   has no dialogue, leave lineKo empty and describe only the situation.
2. For each entry write speaker, lineKo, situationKo, the characters with their position and action, and
   emotionKo, in cut order. panels lists the tile indices (0-based, in the given order) this entry covers.
3. The background and location are whatever the webtoon DRAWS — indoor or outdoor, a closed room or an
   open space. Do NOT guess or change it. If the scene is a closed principal's office, say so; never turn
   it into an open corridor. Set setting.enclosed to true for a closed room, false for an open space.
4. If a character stays in the same spot across panels, write '같은 자리' (the same spot) for positionKo.
   Do not invent movement that the panels do not show.
5. setting.layoutKo / layoutEn describe the whole scene's spatial layout: who stands or sits where,
   and how they face each other. Keep it consistent with the panels.
6. setting.locationKo / locationEn name the place the SCENE OPENS in (as drawn). timeKo is the time of
   day, moodKo the mood. Each entry ALSO has its own locationKo / locationEn: the place drawn in that
   entry's panels. If the webtoon does not change place, copy the setting location into every entry. If
   the webtoon moves somewhere else later (e.g. later panels show a city street at sunset), write that
   new place in those entries' locationKo / locationEn. Never invent a place the panels do not show.
7. Use the Korean role names the user gives you for speaker and character name (e.g. 김부장). Do not
   rename characters or invent names that are not in the panels or the given roster.
8. Write Korean for every *Ko field and plain English for every *En field. No speech-bubble text inside
   *En fields. Answer only with the emit_script tool.
9. voice: """ + VOICE_RULE


# ================================================================ ② 속마음 찾기(지시서 118b)
#
# 이미 정리된 대본의 줄마다 spoken/thought 만 다시 판정한다. 다른 필드는 받지 않는다(plan.run_voice_scan 이 id 가 맞는 줄의
# voice 만 바꾼다).

EMIT_VOICES = {
    "name": "emit_voices",
    "description": "Return spoken/thought for each given script line, judged from the speech-bubble shapes in the panels.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "voice"],
                        "properties": {
                            "id": {"type": "string"},
                            "voice": {"type": "string", "enum": list(VOICE_VALUES)},
                        },
                    },
                }
            },
        }
    },
}


VOICES_SYSTEM = """You check an existing Korean webtoon script against its panels. You are given the webtoon panels in
story order (tiles cut from vertical strips) and the script lines (id, panels, speaker, lineKo). Call the
emit_voices tool exactly once with one item per given line id whose bubble you can see.

Rules you must follow:
1. Find each line's speech bubble in the panels listed for it and look only at the bubble's shape.
2. voice: """ + VOICE_RULE + """
3. Use only the ids you are given. Do not add, drop, merge, rewrite or reorder lines; return only id and voice.
4. If you cannot find the bubble for a line, leave that id out (its current voice stays)."""


# ================================================================ ③ 콘티(conti)

# 지시서 120: 컷의 샷 크기(원작 패널 구도에서 고른다). 화면 이름표는 vnew.js SHOT_SIZE_KO 와 같은 순서다.
SHOT_SIZES = ("wide", "medium", "closeup", "extreme_closeup", "over_shoulder", "profile", "insert")

EMIT_CONTI = {
    "name": "emit_conti",
    "description": "Return the drama storyboard (conti) cuts for the whole video.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["cuts"],
            "properties": {
                "cuts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "seconds", "shotKo", "promptEn"],
                        "properties": {
                            "id": {"type": "string"},
                            "no": {"type": "integer"},
                            # 이 컷의 길이(초). 합이 totalSeconds 가 되게 하려고 서버가 다시 맞춘다.
                            "seconds": {"type": "number"},
                            # 이 컷이 옮기는 대본/타일 번호들.
                            "panels": {"type": "array", "items": {"type": "integer"}},
                            "shotKo": {"type": "string"},
                            "cameraEn": {"type": "string"},
                            # 그 컷이 맡은 script entries 의 locationEn(웹툰이 바꾼 장소를 따른다).
                            "locationEn": {"type": "string"},
                            "characters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "positionEn": {"type": "string"},
                                        "actionEn": {"type": "string"},
                                    },
                                },
                            },
                            "dialogueKo": {"type": "string"},
                            "situationKo": {"type": "string"},
                            # 이 컷을 영상 모델에 줄 영어 문장(장소·인물 위치·동작·카메라·대사 상황).
                            "promptEn": {"type": "string"},
                            # 지시서 120(전부 선택): 대표 패널 번호·그 구도의 샷 크기·그려진 기호·추천 기호·분위기 한 줄.
                            "sourcePanel": {"type": "integer"},
                            "shotSize": {"type": "string", "enum": list(SHOT_SIZES)},
                            "fxDrawn": {"type": "array", "maxItems": DRAWN_MAX,
                                        "items": {"type": "string", "enum": list(FX_KEYS)}},
                            "fxSuggest": {"type": "array", "maxItems": FX_MAX,
                                          "items": {"type": "string", "enum": list(FX_KEYS)}},
                            "moodKo": {"type": "string", "maxLength": MOOD_KO_MAX},
                            "moodEn": {"type": "string", "maxLength": MOOD_EN_MAX},
                            "fxWho": FX_WHO_SCHEMA,
                        },
                    },
                }
            },
        }
    },
}


CONTI_SYSTEM = """You are a drama director turning a webtoon script into a storyboard (conti) for a short
video. You are given the script (setting + dialogue/situation entries), the total video length in seconds,
the user's extra notes, and the character roster. Call the emit_conti tool exactly once.

Rules you must follow:
1. Write AT LEAST 5 cuts. If the script has many situations, use MORE cuts (medium detail, roughly
   3-8 seconds per cut). Spread the story evenly across the cuts in order.
2. Each cut's locationEn MUST be the location drawn in the script entries that cut covers (match the
   locationEn of the entries whose panels this cut uses). The place changes only where the webtoon itself
   changes place; a cut set in later, moved-to panels keeps that new place. Do NOT invent a place that is
   not in any script entry. When a cut covers the opening entries, that is the setting location.
3. Each character's positionEn carries over from the script's positions. If the script says a character
   stays in the same spot, keep them there; do not drift them around the scene.
4. seconds is this cut's length. Give a sensible split; the server will rescale the cuts so their sum
   equals the total length exactly, so keep the relative proportions reasonable (min 1 second each).
5. promptEn is one English sentence (or two) to hand the video model for this cut: the place, where each
   character stands, what they do, the camera, and the situation/mood of the dialogue. Refer to characters
   by their Korean role name (e.g. 김부장). Keep the drawn setting (indoor/outdoor, closed/open) intact.
6. shotKo is a short Korean description of the cut for the user. dialogueKo carries the relevant line(s)
   from the script for this cut. situationKo is what happens. cameraEn is one positive English camera
   instruction (e.g. static camera, slow push-in, over-the-shoulder).
7. Write Korean for every *Ko field and plain English for every *En field.
8. Webtoon panel images may follow the script, each one right after a label "Panel N of M:". N is the same
   0-based tile number the script entries' panels use, and M is how many panels were sent. When panels are
   given, judge each cut's framing from the drawing itself, not from the text alone.
9. sourcePanel is the one panel (from this cut's panels) that best stands for the cut. Its drawn framing sets
   shotSize: a face that fills the panel -> closeup (only the eyes or one small feature -> extreme_closeup);
   figures from the waist up -> medium; whole bodies or a broad background -> wide; a face seen past another
   character's shoulder -> over_shoulder; a face in side view -> profile; only hands or an object -> insert.
   shotKo and cameraEn must agree with that framing.
10. fxDrawn lists the webtoon symbols that are actually drawn in the source panel, only from: """ + " | ".join(FX_KEYS) + """.
   Drawn blush -> blush lines; a drawn sweat drop -> sweat drop; drawn tears -> tear streams; a sparkle or
   flower background -> pink sparkle background; speed or focus lines -> speed lines. Leave it empty when
   nothing is drawn or when no panel images are given; never invent a symbol.
11. fxSuggest: when the cut's emotion is clear, at most """ + str(FX_MAX) + """ symbols from the same list that suit it, best
   first (a drawn one first); otherwise empty. moodKo (<= """ + str(MOOD_KO_MAX) + """ characters, Korean) and moodEn
   (<= """ + str(MOOD_EN_MAX) + """ characters, English) give the mood and the visible characters' expressions in one line,
   with the character names exactly as given. Never describe hair, skin or clothing colors there. """ + MOOD_CUT_RULE + """
12. """ + FX_WHO_RULE + """
13. Hands: """ + HAND_OWNER_RULE + " " + HAND_OWNER_CONTI_RULE + """
Answer only with the emit_conti tool."""


# ================================================================ ③ 기호·분위기 채우기(vnew-live-fx 묶음 A, 2026-10-30)
#
# 콘티 도구와 따로 부른다(콘티 호출은 글만 보내고, 기호에는 그림이 필요하다). 규칙 문장은 참고 구현
# vpoc/llm.py:1217-1222(fxEn·fxSuggest)와 :1246-1253(패널 → 기호 표)을 컷 단위 말로 옮긴 것이다(design §A.3 원문).

EMIT_FX = {
    "name": "emit_fx",
    "description": "Return webtoon visual symbols and a one-line mood for the requested storyboard cuts.",
    "inputSchema": {"json": {"type": "object", "required": ["cuts"], "properties": {"cuts": {"type": "array", "items": {
        "type": "object", "required": ["id"], "properties": {
            "id": {"type": "string"},
            "drawnFx": {"type": "array", "items": {"type": "string", "enum": list(FX_KEYS)}},
            "fxSuggest": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": list(FX_KEYS)}},
            "fx": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": list(FX_KEYS)}},
            "moodKo": {"type": "string"},
            "moodEn": {"type": "string"},
            "fxWho": FX_WHO_SCHEMA}}}}}},
}

FX_SYSTEM = """You read Korean webtoon panels and add webtoon visual symbols and a one-line mood to storyboard cuts.
Call the emit_fx tool exactly once.
You are given the character names and every cut of the storyboard. Cuts marked FILL need symbols and a mood;
cuts marked TRANSLATE need only moodEn; the other cuts are context. Each image is labeled "Panel N", the same
numbers as the cuts' panels.
For each FILL cut:
- drawnFx: the symbols actually drawn in this cut's panels, only from: tear streams | sweat drop | blush lines |
  pink sparkle background | speed lines. Drawn blush lines -> blush lines; a drawn sweat drop -> sweat drop;
  drawn tears -> tear streams; a sparkle or flower background -> pink sparkle background; speed or focus lines
  -> speed lines. Empty when none is drawn.
- fxSuggest: one or two symbols from the same list (never a new symbol) that would suit this cut's emotion, best
  first. If a symbol is drawn in this cut's panels, list it first. If the cut has no clear emotion (a neutral
  look, a plain action, an object), give an empty list. Do not suggest the same symbol for more than three cuts
  in the whole storyboard, counting the context cuts.
- fx: the symbols to apply, zero to two, taken from fxSuggest: the drawn ones and one the emotion clearly calls
  for. Usually zero or one.
- moodKo (<= 60 characters, Korean): the mood and each visible character's facial expression in one line,
  e.g. "민지가 당황해 굳은 웃음, 어색한 공기". Use the given character names.
- moodEn (<= 80 characters, English): the same line for a video model, e.g. "민지's frozen, embarrassed smile;
  awkward air". Keep the character names exactly as given; do not translate or romanize them.
- """ + MOOD_CUT_RULE + """
- """ + FX_WHO_RULE + """ Each FILL line lists the cut's characters and the script feeling of each speaker.
For each TRANSLATE cut: only id and moodEn (the English of its moodKo, <= 80 characters, names kept as given).
Describe only what the panels and the cut text show. Never describe hair, skin or clothing colors. No dialogue
text. Answer only with the emit_fx tool."""


# ================================================================ ③ 콘티 — video-vibecode 샷 계획(작업 158)
#
# 참고 구현 video-vibecode vpoc/llm.py 의 EMIT_MASTER_CUTS(= _with_shot_fields(EMIT_MASTER)) · MASTER_CUTS_SYSTEM 을
# 글자 그대로 옮겼다(_SHOT_RULES → VIBE_SHOT_RULES, 이름만 바꿈). 열거값은 참고 compose.SHOT_SIZES · SHOT_KINDS · FX 와 같은
# 값(기호는 fx.FX_KEYS — 참고 FX 키와 글자 하나 다르지 않다). 바뀐 것은 둘뿐이다:
#   - 입력: SYSTEM 의 자리표시({min_beats} {max_beats} {target_beats} {total})를 우리 총 길이로 채우고, 사용자 블록에 우리 대본 줄·
#     panels·부가 설명·캐릭터 명단(이름·외형)·120 타일·캐릭터 시트를 준다(plan._vibe_prompt · _vibe_blocks).
#   - 호출: vpoc.llm._converse(상위 모델 체인, plan._conti_model) · 원장은 지금처럼 plan._charge.
# plan.run_conti 가 VNEW_VIBE_CONTI 일 때만 쓴다. 결과는 plan._vibe_cuts 가 우리 conti.cuts 모양으로 옮긴다.

# 참고 compose.SHOT_SIZES(키) · SHOT_KINDS. 우리 SHOT_SIZES 의 "insert" 는 참고에서 kind="insert" 다.
VIBE_SHOT_SIZES = ("wide", "medium", "closeup", "extreme_closeup", "over_shoulder", "profile")
VIBE_SHOT_KINDS = ("character", "insert")

_VIBE_MASTER = {
    "name": "emit_master",
    "description": "Return the master plan for one continuous video.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "backgroundEn": {"type": "string"},
                "backgroundKo": {"type": "string"},
                "beats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "t0": {"type": "number"},
                            "t1": {"type": "number"},
                            "actionEn": {"type": "string"},
                            "cameraEn": {"type": "string"},
                            "descKo": {"type": "string"},
                            "characters": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["t0", "t1", "actionEn", "cameraEn", "descKo", "characters"],
                    },
                },
                "characters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "appearanceEn": {"type": "string"},
                        },
                        "required": ["id", "appearanceEn"],
                    },
                },
            },
            "required": ["backgroundEn", "beats"],
        }
    },
}


# 참고 llm._SHOT_PROPS · _with_shot_fields 그대로(beats 항목에 샷 필드를 더한 사본).
_VIBE_SHOT_PROPS = {
    "shotSize": {"type": "string", "enum": list(VIBE_SHOT_SIZES)},
    "kind": {"type": "string", "enum": list(VIBE_SHOT_KINDS)},
    "fxEn": {"type": "string", "enum": ["none", *FX_KEYS]},
    "lightEn": {"type": "string"},
    "sourcePanel": {"type": "integer"},
}


def _vibe_with_shot_fields(tool: dict) -> dict:
    """beats 항목에 샷 필드를 더한 도구 스키마 사본(참고 llm._with_shot_fields 와 같다)."""
    import copy  # noqa: PLC0415

    out = copy.deepcopy(tool)
    item = out["inputSchema"]["json"]["properties"]["beats"]["items"]
    item["properties"].update(copy.deepcopy(_VIBE_SHOT_PROPS))
    item["required"] = [*item["required"], *list(_VIBE_SHOT_PROPS)]
    item["properties"]["fxSuggest"] = {"type": "array", "maxItems": 2,
                                       "items": {"type": "string", "enum": list(FX_KEYS)}}
    return out


VIBE_SHOT_RULES = """  Each shot is ONE framing; between shots there is a hard cut, like a TV drama edit of the webtoon.
  Each shot lasts 2-4 seconds, average about 3s. Cut on every new line of dialogue or reaction. About one shot in four is an
  insert (hands, sleeves, an object or a small detail). Most shots keep the camera still. For each shot:
    - shotSize: wide | medium | closeup | extreme_closeup | over_shoulder | profile. Vary the sizes; open
      with a wide shot that shows where everyone is.
    - kind: "character" or "insert". For an insert of hands or sleeves, characters lists the owner(s) of
      the hands; for an object-only insert, characters is [].
    - actionEn (<= 240 chars): only what is visible in this one framing - a small, readable motion (a glance,
      a hand tightening, tears falling), not a long continuous action.
    - cameraEn (<= 80 chars): "Static camera" for most shots; otherwise one small move (e.g. "slow push-in").
    - fxEn: exactly one of none | tear streams | sweat drop | blush lines | pink sparkle background |
      speed lines. Use a symbol only when the webtoon panel shows it or the emotion clearly calls for it.
    - fxSuggest: one or two symbols from the same list (never none, never a new symbol) that would suit this shot's
      emotion, best first; may include the fxEn you chose. If the symbol is actually drawn in this shot's source
      webtoon panel, list it first. If the shot has no clear emotion (a neutral look, a plain action, an object),
      give an empty list. Do not suggest the same symbol for more than three shots in the whole sequence.
    - lightEn (<= 80 chars): direction and quality of the light only (e.g. "soft window light from the
      left"). Never a color tint or color grade (no "blue tint", no "warm orange grade").
    - descKo (<= 70 chars): what happens, in Korean.
    - characters: ids of the characters visible in this shot.
    - sourcePanel: the number of the webtoon panel this shot comes from (one of the given panel numbers).
    - hands: whenever hands are visible in a shot, say whose hands they are, how many ("one hand",
      "both hands") and where they are. Every other character in that frame keeps "hands resting down,
      out of frame". Each person has exactly two hands; never show extra hands.
  Refer to characters ONLY by their handles. Never describe faces, hair, skin or clothing colors inside
  shots: each character's hair color and clothing colors come only from the character appearance and must
  stay the same in every shot. (An insert may say whose sleeve it is, e.g. "the black-haired man's hand".)"""

EMIT_VIBE_CUTS = _vibe_with_shot_fields(_VIBE_MASTER)

VIBE_CUTS_SYSTEM = """You plan a short video made from a webtoon as a sequence of SHOTS. The whole video keeps ONE
fixed setting and the SAME characters throughout. The webtoon panels are in story order; follow them.
Write English for every *En field, Korean for *Ko.
- backgroundEn (<= 300 chars): the single setting shared by the whole video - place, time of day, lighting,
  key props. One empty location only, with NO people in it (a background plate). Never mention characters.
- backgroundKo (<= 120 chars): the same, in Korean, for the planner to read.
- beats: {min_beats}-{max_beats} shots (aim for about {target_beats}) whose [t0,t1) seconds tile 0..{total}
  with NO gaps and NO overlaps (first t0=0, last t1={total}, each t0 equals the previous t1).
""" + VIBE_SHOT_RULES + """
- Turn the webtoon's panel direction into drama shots. Each image is labeled "Panel N of M"; shots follow
  the panel order, and each shot's framing follows its sourcePanel:
    a face filling the panel -> closeup or extreme_closeup; full bodies or a wide background -> wide;
    upper bodies -> medium; a face seen past another character's shoulder -> over_shoulder; a face in
    side view -> profile; only hands or an object -> kind insert; speed or focus lines -> speed lines;
    drawn blush lines -> blush lines; a drawn sweat drop -> sweat drop; drawn tears -> tear streams;
    a sparkle or flower background -> pink sparkle background.
  One panel may become several shots (for example a reaction shot plus an insert of the hands). Panels
  that add nothing to the story (repeated scenery, text only) may be skipped.
- characters: for EACH given character, appearanceEn (<= 200 chars) - a clean English look description
  (hair, build, clothing, colors). Name the hair color exactly as drawn (e.g. "ash blonde" rather than
  "light brown" when the drawing is pale blonde). For clothing patterns, say where the pattern is
  (e.g. "a white stripe down each sleeve, plain black body", "a logo on the chest"). If the character
  already has an appearance, keep its meaning.
No dialogue text, no speech bubbles, no captions. Answer only with the emit_master tool."""

# 작업 158: 캐릭터 시트 그림(정면 · 턴어라운드)을 보낼 때 그 앞에 붙이는 1줄(사용자 지시 문구 그대로).
VIBE_SHEET_NOTE = "These are AI-generated character sheets of fictional characters; use them for looks"


# ---------------------------------------------------------------- 작업 162 fix1: 컷마다 모든 인물(VNEW_CONTI_CAST)
# 켜면 콘티 도구의 characters 항목이 CAST_CHAR_SCHEMA(presence·actionEn 필수)이고, 옛 경로 SYSTEM 에 규칙 14 를 더한다. 샷 계획은
# 참고 도구(EMIT_VIBE_CUTS)·SYSTEM 을 그대로 두고 샷마다 cast[] 필드를 더한 사본·사용자 글 끝 VIBE_CAST_NOTE 를 쓴다. 끄면 162 그대로.
import copy as _copy  # noqa: E402


def _conti_cast_tool() -> dict:
    tool = _copy.deepcopy(EMIT_CONTI)
    tool["inputSchema"]["json"]["properties"]["cuts"]["items"]["properties"]["characters"]["items"] = _copy.deepcopy(CAST_CHAR_SCHEMA)
    return tool


def _vibe_cast_tool() -> dict:
    tool = _copy.deepcopy(EMIT_VIBE_CUTS)
    item = tool["inputSchema"]["json"]["properties"]["beats"]["items"]
    item["properties"]["cast"] = {"type": "array", "items": _copy.deepcopy(CAST_CHAR_SCHEMA)}
    item["required"] = [*item["required"], "cast"]
    return tool


EMIT_CONTI_CAST = _conti_cast_tool()
EMIT_VIBE_CUTS_CAST = _vibe_cast_tool()
CONTI_CAST_RULE_LINE = "14. Cast: " + CAST_RULE + "\n"
CONTI_CAST_SYSTEM = CONTI_SYSTEM.replace("Answer only with the emit_conti tool.",
                                         CONTI_CAST_RULE_LINE + "Answer only with the emit_conti tool.")
VIBE_CAST_NOTE = ("Cast: for every shot also give cast[] (the beat's characters still lists only the visible ones): "
                  + CAST_RULE.replace("characters lists", "cast lists"))

# 검사가 고친 뒤(characters 만 고치기) 1회 부르는 도구·시스템.
EMIT_CAST_FIX = {
    "name": "emit_cast_fix",
    "description": "Return the corrected characters[] for the requested storyboard cuts only.",
    "inputSchema": {"json": {"type": "object", "required": ["cuts"], "properties": {"cuts": {"type": "array", "items": {
        "type": "object", "required": ["id", "characters"], "properties": {
            "id": {"type": "string"},
            "characters": {"type": "array", "items": _copy.deepcopy(CAST_CHAR_SCHEMA)}}}}}}},
}
CAST_FIX_SYSTEM = (
    "You fix only the characters[] of the storyboard cuts you are given. Do not change anything else (no new cuts, no "
    "timing, dialogue, camera or symbols). Each cut comes with its problems; fix every one. Rules: " + CAST_RULE
    + " Answer only with the emit_cast_fix tool."
)



# ---------------------------------------------------------------- 작업 133: 실사 컷 자동 품질 검사(emit_cut_qa)
# 판정 기준 문구는 이 상수 한 곳에만 둔다(generate._read_cut_qa 가 CUT_QA_SYSTEM 으로 쓴다).
CUT_QA_RULES = (
    "- animeStyle: true if Image 2 shows anime or cartoon line art, drawn outlines or cel shading anywhere "
    "(on people or background). A real photographic frame is false.",
    "- outfitMatch: false if any person's clothing color or garment type in Image 2 differs from the same person "
    "in Image 1 (e.g. white shirt vs black jacket).",
    "- locationMatch: false if the place in Image 2 differs from Image 1: a different building, railing, street, "
    "walls or kind of space (e.g. a seaside road instead of a villa entrance). When Image 3 (the location sheet view) "
    "is given, judge the place against Image 3, because the background of Image 1 may be blurred.",
    "- peopleMatch: false if the number of visible people or their left-right positions differ from Image 1.",
)

EMIT_CUT_QA = {
    "name": "emit_cut_qa",
    "description": "Report whether the live-action frame matches the anime reference frame of the same shot.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["animeStyle", "outfitMatch", "locationMatch", "peopleMatch", "note"],
            "properties": {
                "animeStyle": {"type": "boolean"},
                "outfitMatch": {"type": "boolean"},
                "locationMatch": {"type": "boolean"},
                "peopleMatch": {"type": "boolean"},
                "note": {"type": "string", "maxLength": 80},
            },
        }
    },
}

CUT_QA_SYSTEM = (
    "You check one shot of a Korean drama made from an anime version. Image 1 is the anime reference frame; "
    "Image 2 is the live-action frame at the same moment. Image 2 should be the same shot rendered as real "
    "photography: same people, outfits, location and positions. The change from drawing to photo, faces, "
    "small lighting and small pose differences are expected and are not errors. Judge:\n"
    + "\n".join(CUT_QA_RULES)
    + "\nnote: at most 80 characters, English, naming what differs (empty when everything matches). "
    "Call the emit_cut_qa tool exactly once."
)


# ---------------------------------------------------------------- 작업 139: 애니 프레임 구도 읽기(emit_anime_layout)·시작 그림 검사(emit_still_check)
# generate._read_anime_layout / generate._read_still_check 가 쓴다. 샷 크기 눈금은 STILL_SHOT_SIZES 한 곳.
STILL_SHOT_SIZES = ("ECU", "CU", "MCU", "MS", "MLS", "LS")
SHOT_SIZE_SCALE = ("ECU = only part of the face (eyes, mouth); CU = the face fills the frame, head and neck; "
                   "MCU = head and shoulders to the chest; MS = from the waist up; MLS = from the knees up; "
                   "LS = the full body or wider.")
LAYOUT_SIDES = ("left", "center", "right")
LAYOUT_VISIBLE = ("face", "partial", "back", "none")

EMIT_ANIME_LAYOUT = {
    "name": "emit_anime_layout",
    "description": "Report the shot size of the anime frame and where each cast member is in it.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["shotSize", "people", "count"],
            "properties": {
                "shotSize": {"type": "string", "enum": list(STILL_SHOT_SIZES)},
                "people": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "side", "visible"],
                        "properties": {
                            "name": {"type": "string"},
                            "side": {"type": "string", "enum": list(LAYOUT_SIDES)},
                            "visible": {"type": "string", "enum": list(LAYOUT_VISIBLE)},
                        },
                    },
                },
                "count": {"type": "integer", "minimum": 0},
            },
        }
    },
}

ANIME_LAYOUT_SYSTEM = (
    "You read one frame of an anime shot that will be remade as a live-action frame with the same framing. "
    "Report:\n"
    "- shotSize: " + SHOT_SIZE_SCALE + "\n"
    "- people: one entry for each cast name given in the text, using exactly that name. side = where that person "
    "is in the frame (left, center or right, as seen by the viewer). visible = face (the face is clearly seen), "
    "partial (only part of the face or body is in frame, or a profile), back (seen from behind, the face is not "
    "visible), none (not in this frame at all).\n"
    "- count: the number of people seen in the frame (face, partial or back).\n"
    "Call the emit_anime_layout tool exactly once."
)

STILL_CHECK_RULES = (
    "- shotSize: the shot size of Image 2 on the same scale (" + ", ".join(STILL_SHOT_SIZES) + ").",
    "- count: the number of people seen in Image 2 (face, partial or back view).",
    "- sideMatch: false if any person in Image 2 is on a different side (left, center, right) than in Image 1, "
    "or the left-right order of the people is swapped.",
    "- animeStyle: true if Image 2 looks drawn anywhere (anime or cartoon outlines, cel shading, flat colors). "
    "A real photograph is false.",
)

EMIT_STILL_CHECK = {
    "name": "emit_still_check",
    "description": "Report whether the live-action start frame keeps the framing of the anime frame.",
    "inputSchema": {
        "json": {
            "type": "object",
            "required": ["shotSize", "count", "sideMatch", "animeStyle", "note"],
            "properties": {
                "shotSize": {"type": "string", "enum": list(STILL_SHOT_SIZES)},
                "count": {"type": "integer", "minimum": 0},
                "sideMatch": {"type": "boolean"},
                "animeStyle": {"type": "boolean"},
                "note": {"type": "string", "maxLength": 80},
            },
        }
    },
}

STILL_CHECK_SYSTEM = (
    "You check a live-action start frame (Image 2) made from an anime frame (Image 1) of the same shot. Image 2 "
    "must keep Image 1's framing: the same shot size and the same people on the same sides. Faces, the change "
    "from drawing to photo and small pose differences are expected and are not errors. The text gives the shot "
    "size and the people read from Image 1. Shot sizes: " + SHOT_SIZE_SCALE + " Report:\n"
    + "\n".join(STILL_CHECK_RULES)
    + "\nnote: at most 80 characters, English, naming what differs (empty when everything matches). "
    "Call the emit_still_check tool exactly once."
)
