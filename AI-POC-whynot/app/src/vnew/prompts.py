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
