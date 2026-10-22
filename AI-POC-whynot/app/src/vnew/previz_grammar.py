"""vnew 프리비즈 — 패널 "연출 문법" 읽기(설계자 2026-10-21, 지시서 049).

Astra 가 카메라 좌표 숫자를 직접 정하면 안 맞는다(실측 8컷 0%). 대신 Claude(분석 모델)가 패널을
**영화 연출 문법**(샷 크기·샷 종류·얼굴 각도·시선·상대 쪽·높이)으로 읽고, 그 값으로 previz_camera 가
정석 카메라 자리를 계산한다(실측 8컷 98~100%). 이 모듈은 그 "읽기"만 맡는다.

프롬프트·스키마는 실험 read_test.py 의 SYSTEM·TOOL 을 그대로 옮긴 것이다. 모델은 패널 분석과 같은
previz.PANEL_ANALYZE_MODEL(= vnew_settings.ANALYZE_FALLBACK_MODEL, Claude 분석 체인)을 쓴다. 외부
호출은 모듈 함수 _read(blocks, model=...) 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다). 사용량은 호출자가
준 charge(usage, model) 콜백으로 넘긴다. 캐시(tiles_hash)로 같은 패널이면 다시 부르지 않는다(연결 050).
"""

from __future__ import annotations

import hashlib
import json

from ..vpoc import llm as vpoc_llm
from . import settings as vnew_settings

# 패널 분석과 같은 모델(Claude 분석 체인). previz.PANEL_ANALYZE_MODEL 과 같은 값.
GRAMMAR_MODEL = vnew_settings.ANALYZE_FALLBACK_MODEL

# ================================================================ 시스템·도구(read_test.py 원본)

SYSTEM = (
    "You are a film director and storyboard artist reading one webtoon panel as if it were a frame from a film. "
    "Describe the CAMERA SHOT using standard film grammar. Answer ONLY by calling emit_shot once.\n"
    "Definitions:\n"
    "- frameBottomCut: where the bottom edge of the frame cuts the MAIN subject's body: eyes | chin | shoulders | chest | belly | hips | knees | feet | below_feet. "
    "shotSize follows from it: eyes/chin=ECU, shoulders=CU, chest=MCU, belly/hips=MS, knees=MLS, feet=FS, below_feet=LS.\n"
    "- headTopY: vertical position of the top of the MAIN subject's head in the frame, 0=top edge, 1=bottom edge; negative if the head is cut off above the frame (estimate how far, e.g. -0.3). subjectX: horizontal centre of the main subject's face, 0=left, 1=right.\n"
    "- shotType: clean_single (only the main subject) | dirty_single (main subject plus a sliver/shoulder/back/hand of another person at the frame edge) | over_shoulder (camera behind one person's shoulder looking at the other; the near person's back/shoulder fills about a third) | two_shot | profile_two_shot (both in side view facing each other) | pov | insert (hands/object detail) | establishing.\n"
    "- vantage: which side of the MAIN subject's head the camera sees: front | front3q (slightly turned, both eyes visible) | 3q (three-quarter) | side (profile, one eye) | back3q (mostly back of head, ear and cheek line) | back.\n"
    "- faceTurn: where the main subject's face/eyes point on screen: screen_left | screen_right | toward_camera | away | up | down (pick the dominant).\n"
    "- cameraHeight: low (looking up at the subject) | eye | high (looking down) | overhead.\n"
    "- others: every other person visible, with how they appear: full | partial_edge | shoulder_or_back | hands_only | lips_or_face_part, their side of the frame (left|right|center|bottom), and their screenX.\n"
    "- offscreenPartnerSide: if the subject is talking to / looking at someone not in frame, on which side that person must be: left | right | front_of_camera | none.\n"
    "- actions: what each visible person is physically DOING with hands/head/body (acting beats). For each, give {name (the person), actionEn (one sentence on the hand/head/body action), gesture (ONE of: none | point | bow | hands_on_knees | cover_face | hand_to_own_face | wipe_tears | hand_on_other_cheek | both_hands_other_cheeks | hand_on_chest | press_hand_to_other_chest | lean_in | laugh | look_down | look_up | nod | turn_head_to_other), target (the other person the action is directed at, or none)}. Pick the gesture that best matches the drawn action; use none if the person just stands/sits still.\n"
    "- expressionEn: the MAIN subject's facial expression in one short phrase (e.g. 'teary-eyed, trembling lips').\n"
    "Identify people by name using the cut description given. Ignore speech bubbles and sound effects. Be precise; do not guess beyond what is drawn."
)

TOOL = {
    "name": "emit_shot",
    "description": "camera shot of the panel",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "mainSubject": {"type": "string"},
                "frameBottomCut": {"type": "string"},
                "shotSize": {"type": "string"},
                "headTopY": {"type": "number"},
                "subjectX": {"type": "number"},
                "shotType": {"type": "string"},
                "vantage": {"type": "string"},
                "faceTurn": {"type": "string"},
                "cameraHeight": {"type": "string"},
                "others": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "how": {"type": "string"},
                            "side": {"type": "string"},
                            "screenX": {"type": "number"},
                        },
                    },
                },
                "offscreenPartnerSide": {"type": "string"},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "actionEn": {"type": "string"},
                            "gesture": {"type": "string"},
                            "target": {"type": "string"},
                        },
                    },
                },
                "expressionEn": {"type": "string"},
                "reasonKo": {"type": "string"},
            },
            "required": ["mainSubject", "frameBottomCut", "shotType", "vantage"],
        }
    },
}

# ================================================================ 열거값·기본값

# frameBottomCut → shotSize.
CUT_TO_SIZE = {
    "eyes": "ECU",
    "chin": "ECU",
    "shoulders": "CU",
    "chest": "MCU",
    "belly": "MS",
    "hips": "MS",
    "knees": "MLS",
    "feet": "FS",
    "below_feet": "LS",
}
SHOT_SIZES = ("ECU", "CU", "MCU", "MS", "MLS", "FS", "LS")
SHOT_TYPES = (
    "clean_single", "dirty_single", "over_shoulder", "two_shot",
    "profile_two_shot", "pov", "insert", "establishing",
)
VANTAGES = ("front", "front3q", "3q", "side", "back3q", "back")
FACE_TURNS = ("screen_left", "screen_right", "toward_camera", "away", "up", "down")
CAMERA_HEIGHTS = ("low", "eye", "high", "overhead")
OTHER_HOWS = ("full", "partial_edge", "shoulder_or_back", "hands_only", "lips_or_face_part")
OTHER_SIDES = ("left", "right", "center", "bottom")
PARTNER_SIDES = ("left", "right", "front_of_camera", "none")

# actions[].gesture 열거값 = previz_schema.GESTURES + "none". 모르는 값은 "none".
from .previz_schema import GESTURES as _SCHEMA_GESTURES  # noqa: E402

ACTION_GESTURES = ("none",) + tuple(_SCHEMA_GESTURES)

# headTopY 범위(머리 위가 화면 위로 잘릴 수 있으니 음수 허용), subjectX 0~1.
HEAD_TOP_MIN, HEAD_TOP_MAX = -0.8, 0.9
SUBJECT_X_MIN, SUBJECT_X_MAX = 0.0, 1.0

# 화면용 한국어 라벨.
LABELS_KO = {
    "shotSize": {
        "ECU": "익스트림 클로즈업", "CU": "클로즈업", "MCU": "바스트샷", "MS": "웨이스트샷",
        "MLS": "니샷", "FS": "풀샷", "LS": "롱샷",
    },
    "shotType": {
        "clean_single": "클린 싱글", "dirty_single": "더티 싱글", "over_shoulder": "오버더숄더",
        "two_shot": "투샷", "profile_two_shot": "프로필 투샷", "pov": "주관샷",
        "insert": "인서트", "establishing": "설정샷",
    },
    "vantage": {
        "front": "정면", "front3q": "살짝 돌린 정면", "3q": "3/4", "side": "옆(프로필)",
        "back3q": "뒤 3/4", "back": "뒤",
    },
    "cameraHeight": {"low": "앙각", "eye": "아이레벨", "high": "부감", "overhead": "버즈아이"},
}


# ================================================================ 숫자·열거 도우미


def _enum(value, allowed, default):
    v = str(value or "").strip()
    return v if v in allowed else default


def _num(value, default):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value, low, high):
    return max(low, min(high, value))


# ================================================================ 캐시 해시


def tiles_hash(tiles) -> str:
    """패널 조각들의 sha1 앞 16자. 같은 패널이면 같은 해시(연결 050 캐시 키)."""
    hasher = hashlib.sha1()
    for tile in tiles or []:
        if isinstance(tile, (bytes, bytearray)):
            hasher.update(bytes(tile))
        else:
            hasher.update(str(tile).encode("utf-8"))
        hasher.update(b"|")
    return hasher.hexdigest()[:16]


# ================================================================ 정규화


def normalize_grammar(payload: dict) -> dict:
    """모델 출력(emit_shot)을 안전한 연출 문법 dict 로 정리한다.

    - 열거값은 모르는 값이면 기본값. frameBottomCut → shotSize(모델이 shotSize 를 직접 줘도 우선은
      frameBottomCut 에서 도출; frameBottomCut 이 없거나 모르면 모델 shotSize, 그래도 없으면 MCU).
    - headTopY -0.8~0.9, subjectX 0~1 로 clamp.
    - others 는 name·how·side·screenX 만 남긴다.
    반환 키: mainSubject, frameBottomCut, shotSize, shotType, vantage, faceTurn, cameraHeight,
    headTopY, subjectX, others[], offscreenPartnerSide, reasonKo.
    """
    payload = payload if isinstance(payload, dict) else {}

    bottom_cut = str(payload.get("frameBottomCut") or "").strip()
    if bottom_cut in CUT_TO_SIZE:
        shot_size = CUT_TO_SIZE[bottom_cut]
    else:
        shot_size = _enum(payload.get("shotSize"), SHOT_SIZES, "MCU")
        bottom_cut = bottom_cut if bottom_cut else ""

    others = []
    for item in payload.get("others") or []:
        if not isinstance(item, dict):
            continue
        others.append({
            "name": str(item.get("name") or "").strip(),
            "how": _enum(item.get("how"), OTHER_HOWS, "full"),
            "side": _enum(item.get("side"), OTHER_SIDES, "center"),
            "screenX": round(_clamp(_num(item.get("screenX"), 0.5), 0.0, 1.0), 4),
        })

    # actions: 보이는 인물의 연기 동작. gesture 는 ACTION_GESTURES 열거값(모르면 "none"),
    # target 은 상대 이름(없으면 "none"). name 이 비면 버린다.
    actions = []
    for item in payload.get("actions") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        actions.append({
            "name": name,
            "actionEn": str(item.get("actionEn") or "").strip(),
            "gesture": _enum(item.get("gesture"), ACTION_GESTURES, "none"),
            "target": str(item.get("target") or "none").strip() or "none",
        })

    return {
        "mainSubject": str(payload.get("mainSubject") or "").strip(),
        "frameBottomCut": bottom_cut,
        "shotSize": shot_size,
        "shotType": _enum(payload.get("shotType"), SHOT_TYPES, "clean_single"),
        "vantage": _enum(payload.get("vantage"), VANTAGES, "3q"),
        "faceTurn": _enum(payload.get("faceTurn"), FACE_TURNS, "toward_camera"),
        "cameraHeight": _enum(payload.get("cameraHeight"), CAMERA_HEIGHTS, "eye"),
        "headTopY": round(_clamp(_num(payload.get("headTopY"), 0.1), HEAD_TOP_MIN, HEAD_TOP_MAX), 4),
        "subjectX": round(_clamp(_num(payload.get("subjectX"), 0.5), SUBJECT_X_MIN, SUBJECT_X_MAX), 4),
        "others": others,
        "offscreenPartnerSide": _enum(payload.get("offscreenPartnerSide"), PARTNER_SIDES, "none"),
        "actions": actions,
        "expressionEn": str(payload.get("expressionEn") or "").strip(),
        "reasonKo": str(payload.get("reasonKo") or "").strip(),
    }


# ================================================================ 외부 호출 래퍼(검사가 가짜로)


def _read(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 패널 연출 문법(emit_shot)을 받는다. (payload, usage). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=SYSTEM,
        blocks=blocks,
        tool=TOOL,
        max_tokens=900,
        where="vnew.previz.grammar",
        model=model,
    )


# ================================================================ 한 컷 읽기


def read_cut(owner, pid, plan_id, cut, tiles, role_names, *, charge) -> dict | None:
    """한 컷의 웹툰 조각(첫 장)을 Claude 분석 체인에 보내 연출 문법을 읽는다. 없으면 None.

    - cut 의 shotKo·characters 를 함께 줘서 모델이 한국 인물 이름으로 식별하게 한다.
    - role_names(이 계획의 인물 이름 목록)도 힌트로 넣는다.
    - charge(usage, model) 콜백으로 사용량을 넘긴다(원장은 호출자가).
    반환은 normalize_grammar 결과 dict.
    """
    tiles = list(tiles or [])
    if not tiles:
        return None
    first = tiles[0]
    if not isinstance(first, (bytes, bytearray)):
        return None

    desc = json.dumps(
        {
            "shotKo": (cut or {}).get("shotKo"),
            "characters": (cut or {}).get("characters"),
            "roleNames": list(role_names or []),
        },
        ensure_ascii=False,
    )[:900]
    blocks = [
        {"text": "Cut description (who is who): " + desc},
        {"image": {"format": "jpeg", "source": {"bytes": bytes(first)}}},
    ]
    payload, usage = _read(blocks, model=GRAMMAR_MODEL)
    if callable(charge):
        charge(usage or {}, GRAMMAR_MODEL)
    return normalize_grammar(payload)
