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
from .fx import FX_KEYS

# ================================================================ ② 대본(script)

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
   *En fields. Answer only with the emit_script tool."""


# ================================================================ ③ 콘티(conti)

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
7. Write Korean for every *Ko field and plain English for every *En field. Answer only with the
   emit_conti tool."""


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
            "moodEn": {"type": "string"}}}}}}},
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
For each TRANSLATE cut: only id and moodEn (the English of its moodKo, <= 80 characters, names kept as given).
Describe only what the panels and the cut text show. Never describe hair, skin or clothing colors. No dialogue
text. Answer only with the emit_fx tool."""
