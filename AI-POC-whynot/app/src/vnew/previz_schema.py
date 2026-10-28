"""vnew 프리비즈 — 장면 설계 JSON 정리·검증(설계자 2026-10-21, 지시서 037).

Astra 가 쓴 3D 장면 JSON(브라우저 three.js 가 그려 재생·녹화)을 정리한다. 모델 출력은 숫자가
범위를 벗어나거나, 인물 좌표가 방 밖에 있거나, 색이 겹치거나, 컷이 콘티와 1:1 이 아니거나,
시간이 겹치거나 합이 안 맞을 수 있다. normalize_scene 이 그런 것을 모두 보정한다.

단위: m·초. 좌표: x 오른쪽, y 위, z 카메라 쪽(+). north 벽은 z = -depth/2.

이 모듈은 순수 함수만 둔다(네트워크·S3·LLM 호출 없음). previz.py 가 Astra 결과와 사용자 PUT 을
이 함수에 통과시킨다. check 가 단위로도 부른다.
"""

from __future__ import annotations

import copy

# ================================================================ 상수(범위)

# 방 크기(m). 너무 작거나 큰 값을 이 범위로 자른다.
ROOM_MIN_M = 3.0
ROOM_MAX_M = 30.0
ROOM_HEIGHT_MIN_M = 2.0
ROOM_HEIGHT_MAX_M = 10.0
# 카메라 화각(도).
FOV_MIN = 20.0
FOV_MAX = 90.0
# 인물 키(m).
ACTOR_HEIGHT_MIN_M = 0.5
ACTOR_HEIGHT_MAX_M = 2.5
# 인물이 벽에 바짝 붙지 않도록 방 안쪽으로 두는 여유(m).
WALL_MARGIN_M = 0.2

VERSION = 1

SIDES = ("north", "east", "south", "west")
LIGHT_KINDS = ("sun", "lamp")
PROP_SHAPES = ("box", "cylinder")
POSES = ("stand", "sit", "kneel", "walk", "lie")
# 제스처(연기 동작). 렌더러 vnew-previz.js 의 _applyGesture 와 **문자열이 같아야** 한다(검사 mf-a).
# 앞 4개는 옛 동작(point·bow·hands_on_knees·cover_face), 나머지는 2026-10-22 추가한 연기 동작.
# 상대가 필요한 동작(hand_on_other_cheek·both_hands_other_cheeks·press_hand_to_other_chest·
# lean_in·turn_head_to_other)은 timeline actor 의 gestureTarget(상대 actor id)으로 방향을 잡는다.
GESTURES = (
    "point",
    "bow",
    "hands_on_knees",
    "cover_face",
    "hand_to_own_face",
    "wipe_tears",
    "hand_on_other_cheek",
    "both_hands_other_cheeks",
    "hand_on_chest",
    "press_hand_to_other_chest",
    "lean_in",
    "laugh",
    "look_down",
    "look_up",
    "nod",
    "turn_head_to_other",
)
# 기본 머리카락 색(hairColor 가 비거나 잘못된 값이면). 몸 색보다 어두운 회색.
DEFAULT_HAIR_COLOR = "#2b2b2b"

# 인물 고유 원색 팔레트(서로 확실히 다르게 — 설계자 "등장 인물은 고유의 색으로"). 색이 겹치면
# 이 순서대로 아직 안 쓴 색을 배정한다. 밝은 원색 위주로 배경(회색조)과 구분되게.
#
# 설계자 2026-10-22(⑤ 색 이름 고정): 영상 모델이 "The #00e5ff mannequin" 같은 hex 를 못 읽어
# 인물이 뒤바뀌는 문제가 있었다. 그래서 팔레트를 **이름 있는 색 목록** [(hex, name)] 으로 두고,
# 배우 색은 Astra 가 준 값을 쓰지 않고 **등장 순서대로** 이 팔레트에서 중복 없이 배정한다.
# 이름은 영상 모델이 확실히 아는 평범한 색 단어(blue·orange·green…)만 쓴다.
ACTOR_PALETTE_NAMED = (
    ("#4363d8", "blue"),
    ("#f58231", "orange"),
    ("#3cb44b", "green"),
    ("#e6194b", "red"),
    ("#911eb4", "purple"),
    ("#ffe119", "yellow"),
    ("#f032e6", "magenta"),
    ("#469990", "teal"),
    ("#42d4f4", "cyan"),
    ("#9a6324", "brown"),
    ("#fabed4", "pink"),
    ("#bfef45", "lime"),
    ("#dcbeff", "lavender"),
    ("#000075", "navy"),
    ("#808000", "olive"),
)

# 옛 코드 호환: 색 hex 만 쓰는 곳(있다면)을 위해 hex 튜플도 둔다(이름 순서와 같다).
ACTOR_PALETTE = tuple(hex_color for hex_color, _name in ACTOR_PALETTE_NAMED)

# hex(소문자) → 팔레트 이름. generate._color_name 이 먼저 본다.
PALETTE_NAME_BY_HEX = {hex_color.lower(): name for hex_color, name in ACTOR_PALETTE_NAMED}

# 기본 색(벽·바닥·소품). 배경은 '하나의 덩어리'로 보이게 무채색 계열.
DEFAULT_FLOOR_COLOR = "#8a8a8a"
DEFAULT_WALL_COLOR = "#cfcfcf"
DEFAULT_PROP_COLOR = "#b0a080"
DEFAULT_LIGHT_COLOR = "#fff4e0"


# ================================================================ 작은 도우미


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return False


def _str(value) -> str:
    return str(value or "").strip()


def _color(value, default: str) -> str:
    """색 문자열을 받는다(#rrggbb · 색 이름). 비면 기본값."""
    text = _str(value)
    return text or default


def _hex_color(value, default: str) -> str:
    """#rrggbb(또는 #rgb) hex 만 받는다. 아니면 default. hairColor 정리에 쓴다(mf-b)."""
    text = _str(value).lower()
    if len(text) in (4, 7) and text.startswith("#"):
        hexpart = text[1:]
        if all(c in "0123456789abcdef" for c in hexpart):
            return text
    return default


def _vec3(value, default: tuple[float, float, float]) -> list[float]:
    """[x, y, z] 3원소 숫자 리스트로. 모자라거나 숫자 아니면 기본값으로 채운다."""
    out = list(default)
    if isinstance(value, (list, tuple)):
        for i in range(3):
            if i < len(value) and _is_num(value[i]):
                out[i] = float(value[i])
    return [_round(out[0]), _round(out[1]), _round(out[2])]


# ================================================================ 방(room)


def _normalize_room(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    width = _clamp(_num(raw.get("width"), 6.0), ROOM_MIN_M, ROOM_MAX_M)
    depth = _clamp(_num(raw.get("depth"), 6.0), ROOM_MIN_M, ROOM_MAX_M)
    height = _clamp(_num(raw.get("height"), 2.8), ROOM_HEIGHT_MIN_M, ROOM_HEIGHT_MAX_M)
    walls_in = raw.get("walls") if isinstance(raw.get("walls"), dict) else {}
    walls: dict[str, dict] = {}
    for side in SIDES:
        wall_raw = walls_in.get(side) if isinstance(walls_in.get(side), dict) else {}
        openings = []
        raw_openings = wall_raw.get("openings")
        if isinstance(raw_openings, list):
            for item in raw_openings:
                if not isinstance(item, dict):
                    continue
                kind = _str(item.get("kind")).lower()
                if kind not in ("window", "door"):
                    kind = "window"
                openings.append(
                    {
                        "kind": kind,
                        "x": _round(_num(item.get("x"), 0.0)),
                        "y": _round(_num(item.get("y"), 0.0)),
                        "w": _round(max(0.1, _num(item.get("w"), 1.0))),
                        "h": _round(max(0.1, _num(item.get("h"), 1.0))),
                    }
                )
        wall = {
            "color": _color(wall_raw.get("color"), DEFAULT_WALL_COLOR),
            "openings": openings,
        }
        # textureKey 는 location 이미지 키로 채운다(previz.py 가 넣는다). 모델이 준 값은 버린다.
        if wall_raw.get("textureKey"):
            wall["textureKey"] = _str(wall_raw.get("textureKey"))
        walls[side] = wall
    return {
        "width": _round(width),
        "depth": _round(depth),
        "height": _round(height),
        "floorColor": _color(raw.get("floorColor"), DEFAULT_FLOOR_COLOR),
        "walls": walls,
    }


def _room_bounds(room: dict) -> tuple[float, float, float, float]:
    """인물·카메라가 들어갈 수 있는 x·z 범위(여유 WALL_MARGIN_M). (xmin, xmax, zmin, zmax)."""
    width = _num(room.get("width"), 6.0)
    depth = _num(room.get("depth"), 6.0)
    half_w = width / 2.0
    half_d = depth / 2.0
    margin = WALL_MARGIN_M
    return (-half_w + margin, half_w - margin, -half_d + margin, half_d - margin)


# ================================================================ 빛(light)


def _normalize_light(raw: dict, room: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    kind = _str(raw.get("kind")).lower()
    if kind not in LIGHT_KINDS:
        kind = "sun"
    width = _num(room.get("width"), 6.0)
    depth = _num(room.get("depth"), 6.0)
    height = _num(room.get("height"), 2.8)
    # 기본: north 벽(z = -depth/2) 위쪽에서 방 중앙으로 비춘다.
    default_from = (0.0, height, -depth / 2.0)
    default_to = (0.0, 0.0, 0.0)
    intensity = _clamp(_num(raw.get("intensity"), 1.0), 0.0, 10.0)
    return {
        "kind": kind,
        "from": _vec3(raw.get("from"), default_from),
        "to": _vec3(raw.get("to"), default_to),
        "color": _color(raw.get("color"), DEFAULT_LIGHT_COLOR),
        "intensity": _round(intensity),
        "labelKo": _str(raw.get("labelKo")) or "빛",
    }


# ================================================================ 소품(props)


def _normalize_props(raw_props, room: dict) -> list[dict]:
    props: list[dict] = []
    if not isinstance(raw_props, list):
        return props
    xmin, xmax, zmin, zmax = _room_bounds(room)
    height = _num(room.get("height"), 2.8)
    for index, item in enumerate(raw_props):
        if not isinstance(item, dict):
            continue
        shape = _str(item.get("shape")).lower()
        if shape not in PROP_SHAPES:
            shape = "box"
        size = item.get("size")
        size_vec = _vec3(size, (0.5, 0.5, 0.5))
        size_vec = [max(0.05, s) for s in size_vec]
        pos = _vec3(item.get("pos"), (0.0, size_vec[1] / 2.0, 0.0))
        # 소품도 방 안에 둔다(x·z 보정, y 는 그대로).
        pos[0] = _round(_clamp(pos[0], xmin, xmax))
        pos[2] = _round(_clamp(pos[2], zmin, zmax))
        pos[1] = _round(_clamp(pos[1], 0.0, height))
        props.append(
            {
                "id": _str(item.get("id")) or f"prop{index + 1}",
                "labelKo": _str(item.get("labelKo")),
                "labelEn": _str(item.get("labelEn")),
                "shape": shape,
                "size": [_round(s) for s in size_vec],
                "pos": pos,
                "rotY": _round(_num(item.get("rotY"), 0.0)),
                "color": _color(item.get("color"), DEFAULT_PROP_COLOR),
            }
        )
        # 앉을 수 있는 면(084 R1.1). 참일 때만 둔다(거짓이면 키 없음 — 옛 출력과 같다).
        if _as_bool(item.get("sittable")):
            props[-1]["sittable"] = True
    return props


# ================================================================ 인물(actors)


def _normalize_actors(raw_actors) -> list[dict]:
    """장면 인물 목록. 색은 **Astra 가 준 값을 버리고** 등장 순서대로 팔레트에서 배정한다.

    설계자 2026-10-22(⑤): 영상 모델이 임의 hex(예: #00e5ff)를 못 읽어 인물이 뒤바뀌므로, 배우 색은
    모델이 뭘 주든 무시하고 ACTOR_PALETTE_NAMED 에서 **순서대로 중복 없이** 배정한다. 팔레트를 다
    쓰면(인물이 아주 많으면) 회색으로 둔다.
    """
    actors: list[dict] = []
    if not isinstance(raw_actors, list):
        raw_actors = []

    palette_index = 0

    def _next_palette() -> str:
        nonlocal palette_index
        if palette_index < len(ACTOR_PALETTE):
            color = ACTOR_PALETTE[palette_index]
            palette_index += 1
            return color
        # 팔레트를 다 썼으면(인물이 많으면) 회색으로.
        return "#a9a9a9"

    for index, item in enumerate(raw_actors):
        if not isinstance(item, dict):
            continue
        color = _next_palette()
        actor = {
            "id": _str(item.get("id")) or f"a{index + 1}",
            "name": _str(item.get("name")),
            "color": color,
            "hairColor": _hex_color(item.get("hairColor"), DEFAULT_HAIR_COLOR),
            "heightM": _round(_clamp(_num(item.get("heightM"), 1.7), ACTOR_HEIGHT_MIN_M, ACTOR_HEIGHT_MAX_M)),
        }
        char_id = _str(item.get("charId"))
        if char_id:
            actor["charId"] = char_id
        appearance_en = _str(item.get("appearanceEn"))
        if appearance_en:
            actor["appearanceEn"] = appearance_en
        actors.append(actor)
    return actors


# ================================================================ 타임라인(timeline)


def _normalize_camera(raw: dict, room: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    depth = _num(room.get("depth"), 6.0)
    height = _num(room.get("height"), 2.8)
    # 기본 카메라: 방 앞쪽(z = +depth/2 바깥) 눈높이에서 중앙을 본다.
    default_pos = (0.0, max(1.2, height * 0.6), depth / 2.0 + 1.0)
    default_target = (0.0, 1.2, 0.0)
    camera = {
        "pos": _vec3(raw.get("pos"), default_pos),
        "target": _vec3(raw.get("target"), default_target),
        "fov": _round(_clamp(_num(raw.get("fov"), 50.0), FOV_MIN, FOV_MAX)),
    }
    move = raw.get("moveTo")
    if isinstance(move, dict):
        camera["moveTo"] = {
            "pos": _vec3(move.get("pos"), tuple(camera["pos"])),
            "target": _vec3(move.get("target"), tuple(camera["target"])),
        }
    return camera


def _normalize_sit(raw_sit, facing_deg: float, prop_ids: list[str]) -> dict | None:
    """컷 인물 sit(084 R1.2·R1.18). propId 가 장면 소품, 0.2 ≤ hipY ≤ 2.0, legPitchDeg 가 후보일 때만.

    legYawDeg 는 (−180, 180] 로 감는다. 없거나 숫자가 아니면 facingDeg(옛 데이터).
    """
    from . import previz_collision  # noqa: PLC0415 — previz_collision 이 이 모듈을 import 한다(순환 피함)

    if not isinstance(raw_sit, dict):
        return None
    prop_id = _str(raw_sit.get("propId"))
    hip = raw_sit.get("hipY")
    pitch = raw_sit.get("legPitchDeg")
    if not prop_id or prop_id not in prop_ids or not _is_num(hip) or not (0.2 <= float(hip) <= 2.0):
        return None
    if not _is_num(pitch) or float(pitch) not in [float(p) for p in previz_collision.LEG_PITCHES]:
        return None
    yaw_raw = raw_sit.get("legYawDeg")
    yaw = float(yaw_raw) if _is_num(yaw_raw) and abs(float(yaw_raw)) < 1e6 else float(facing_deg)
    return {
        "propId": prop_id,
        "hipY": round(float(hip), 4),
        "legPitchDeg": int(float(pitch)),
        "legYawDeg": round(previz_collision.wrap_deg(yaw), 2),
    }


def _normalize_cut_actors(raw_actors, actor_ids: list[str], room: dict, prop_ids: list[str] | None = None) -> list[dict]:
    """컷 안 인물 상태(위치·방향·포즈). 방 밖 좌표는 방 안으로 보정.

    prop_ids(장면 소품 id)를 주면 앉은 인물의 sitOn·sit 을 검증해 둔다(084 R1). None 이면 지금과 같다.
    """
    xmin, xmax, zmin, zmax = _room_bounds(room)
    out: list[dict] = []
    if not isinstance(raw_actors, list):
        raw_actors = []
    for item in raw_actors:
        if not isinstance(item, dict):
            continue
        actor_id = _str(item.get("id"))
        pos = _vec3(item.get("pos"), (0.0, 0.0, 0.0))
        pos[0] = _round(_clamp(pos[0], xmin, xmax))
        pos[1] = 0.0  # 인물은 바닥(y=0).
        pos[2] = _round(_clamp(pos[2], zmin, zmax))
        pose = _str(item.get("pose")).lower()
        if pose not in POSES:
            pose = "stand"
        gesture = _str(item.get("gesture")).lower()
        if gesture not in GESTURES:
            gesture = None
        cut_actor = {
            "id": actor_id,
            "pos": pos,
            "facingDeg": _round(_num(item.get("facingDeg"), 0.0)),
            "pose": pose,
            "gesture": gesture,
            "speaking": _as_bool(item.get("speaking")),
        }
        # gestureTarget: 상대 actor id(존재하는 id 만, 자기 자신 제외). 그 외엔 넣지 않는다(mf-b).
        target = _str(item.get("gestureTarget"))
        if target and target in (actor_ids or []) and target != actor_id:
            cut_actor["gestureTarget"] = target
        move = item.get("moveTo")
        if isinstance(move, (list, tuple)):
            mv = _vec3(move, (pos[0], 0.0, pos[2]))
            mv[0] = _round(_clamp(mv[0], xmin, xmax))
            mv[1] = 0.0
            mv[2] = _round(_clamp(mv[2], zmin, zmax))
            cut_actor["moveTo"] = mv
        # 앉을 자리(084 R1): sit 자세 + 장면에 있는 소품 id 만.
        if prop_ids and pose == "sit":
            sit_on = _str(item.get("sitOn"))
            if sit_on and sit_on in prop_ids:
                cut_actor["sitOn"] = sit_on
            sit = _normalize_sit(item.get("sit"), cut_actor["facingDeg"], prop_ids)
            if sit is not None:
                cut_actor["sit"] = sit
        out.append(cut_actor)
    return out


def _normalize_timeline(raw_timeline, room: dict, actor_ids: list[str], cut_specs: list[dict], start_clock: float = 0.0, prop_ids: list[str] | None = None) -> tuple[list[dict], float]:
    """타임라인을 콘티 컷과 1:1 로 맞춘다.

    cut_specs = [{cutId, seconds}] (콘티 순서). 모델 timeline 을 cutId 로 맞추되,
    - 빠진 컷은 앞 컷 상태(카메라·인물)를 복사해 채운다.
    - start/end 는 **콘티 전체 기준 누적 시간**이다(start_clock 에서 이어 센다 — 장면이 바뀌어도
      0 으로 되돌아가지 않는다). 각 컷 길이는 cut_specs 의 seconds 를 그대로 쓴다(모델이 준 start/end 는
      버린다 — 콘티 seconds 를 강제).
    - 모델이 cutId 를 안 주면 순서대로 매핑한다.
    반환: (timeline, 끝난 시각 clock).
    """
    raw_list = raw_timeline if isinstance(raw_timeline, list) else []
    # cutId → 모델 컷(먼저 나온 것 우선).
    by_id: dict[str, dict] = {}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        cid = _str(item.get("cutId"))
        if cid and cid not in by_id:
            by_id[cid] = item
    # cutId 없는 모델 컷(순서 매핑용).
    positional = [item for item in raw_list if isinstance(item, dict)]

    timeline: list[dict] = []
    clock = float(start_clock)
    prev_camera: dict | None = None
    prev_actors: list[dict] | None = None
    for index, spec in enumerate(cut_specs):
        cut_id = _str(spec.get("cutId")) or f"c{index + 1}"
        seconds = max(0.0, _num(spec.get("seconds"), 0.0))
        raw_cut = by_id.get(cut_id)
        if raw_cut is None and index < len(positional):
            # cutId 매칭 실패 시 순서로 받는다(단, 이미 id 로 쓴 것은 건너뛴다).
            candidate = positional[index]
            if _str(candidate.get("cutId")) in ("", cut_id):
                raw_cut = candidate

        if raw_cut is not None:
            camera = _normalize_camera(raw_cut.get("camera") or {}, room)
            cut_actors = _normalize_cut_actors(raw_cut.get("actors") or [], actor_ids, room, prop_ids)
        elif prev_camera is not None:
            # 빠진 컷: 앞 컷 상태를 복사(카메라만 이어지고 인물 자리는 이어진다).
            camera = copy.deepcopy(prev_camera)
            camera.pop("moveTo", None)  # 복사 컷은 이동 없이 정지.
            cut_actors = copy.deepcopy(prev_actors or [])
            for ca in cut_actors:
                ca.pop("moveTo", None)
        else:
            camera = _normalize_camera({}, room)
            cut_actors = []

        start = _round(clock)
        end = _round(clock + seconds)
        clock = clock + seconds
        timeline.append(
            {
                "cutId": cut_id,
                "start": start,
                "end": end,
                "camera": camera,
                "actors": cut_actors,
            }
        )
        prev_camera = camera
        prev_actors = cut_actors
    return timeline, _round(clock)


# ================================================================ 공개: 장면 하나 정리


def normalize_scene(raw: dict, *, location_id: str, cut_specs: list[dict], textures: dict | None = None, start_clock: float = 0.0) -> dict:
    """장면 JSON 하나를 정리한다.

    - raw: Astra·사용자 PUT 결과(dict).
    - location_id: 이 장면의 장소 id.
    - cut_specs: [{cutId, seconds}] — 이 장면에 속한 콘티 컷(순서). timeline 을 이것과 1:1 로 맞춘다.
    - textures: {floorKey, walls:{side: wallKey}} — location 이미지 키(previz.py 가 035 에서 뽑아 넣는다).
      room.floorColor 는 그대로 두고, 벽·바닥 textureKey 를 채운다.
    - start_clock: 이 장면의 timeline 이 시작하는 '콘티 전체 기준' 시각(앞 장면 끝에서 이어 센다).
    """
    raw = raw if isinstance(raw, dict) else {}
    room = _normalize_room(raw.get("room") or {})
    light = _normalize_light(raw.get("light") or {}, room)
    props = _normalize_props(raw.get("props"), room)
    actors = _normalize_actors(raw.get("actors"))
    actor_ids = [a["id"] for a in actors]
    prop_ids = [p["id"] for p in props]
    timeline, _ = _normalize_timeline(raw.get("timeline"), room, actor_ids, cut_specs, start_clock, prop_ids=prop_ids)

    scene = {
        "version": VERSION,
        "locationId": _str(location_id) or _str(raw.get("locationId")),
        "room": room,
        "light": light,
        "props": props,
        "actors": actors,
        "timeline": timeline,
        "notesKo": _str(raw.get("notesKo")),
    }
    if textures:
        _apply_textures(scene, textures)
    return scene


def _apply_textures(scene: dict, textures: dict) -> None:
    """location 이미지 키를 room 에 채운다. floorKey → room.floorTextureKey, walls[side] → textureKey."""
    walls = (scene.get("room") or {}).get("walls") or {}
    tex_walls = textures.get("walls") if isinstance(textures.get("walls"), dict) else {}
    for side in SIDES:
        key = _str(tex_walls.get(side))
        if key:
            walls.setdefault(side, {})["textureKey"] = key
    floor_key = _str(textures.get("floorKey"))
    if floor_key:
        scene["room"]["floorTextureKey"] = floor_key


# ================================================================ 공개: 전체(scenes[]) 정리


def normalize_scenes(raw_scenes, *, scene_specs: list[dict]) -> list[dict]:
    """여러 장면을 정리한다.

    scene_specs = [{locationId, cuts:[{cutId, seconds}], textures?}] — 장소가 바뀌는 컷마다 새 장면.
    raw_scenes 는 모델/사용자가 준 scenes 리스트(장면 수가 안 맞으면 spec 기준으로 맞춘다).
    """
    raw_list = raw_scenes if isinstance(raw_scenes, list) else []
    out: list[dict] = []
    clock = 0.0  # 콘티 전체 기준 누적 시각. 장면이 바뀌어도 이어 센다(0 으로 되돌아가지 않는다).
    for index, spec in enumerate(scene_specs):
        location_id = _str(spec.get("locationId"))
        cut_specs = spec.get("cuts") or []
        textures = spec.get("textures") or {}
        # location 으로 맞추되, 없으면 순서로.
        raw = None
        for item in raw_list:
            if isinstance(item, dict) and _str(item.get("locationId")) == location_id and location_id:
                raw = item
                break
        if raw is None and index < len(raw_list) and isinstance(raw_list[index], dict):
            raw = raw_list[index]
        scene = normalize_scene(
            raw or {}, location_id=location_id, cut_specs=cut_specs, textures=textures, start_clock=clock
        )
        out.append(scene)
        # 다음 장면은 이 장면의 마지막 컷 end 에서 이어 센다.
        timeline = scene.get("timeline") or []
        if timeline:
            clock = _num(timeline[-1].get("end"), clock)
    return out


# ================================================================ 검증 요약(뷰·검사용)


def scene_total_seconds(scene: dict) -> float:
    """장면 자체의 길이(초) = 마지막 컷 end − 첫 컷 start. 빈 타임라인은 0.

    timeline 의 start/end 는 콘티 전체 기준 누적 시각이므로, 장면 길이는 end 그대로가 아니라
    (end − start) 로 구한다(두 번째 장면부터 start 가 0 이 아니다).
    """
    timeline = scene.get("timeline") or []
    if not timeline:
        return 0.0
    start = _num(timeline[0].get("start"), 0.0)
    end = _num(timeline[-1].get("end"), 0.0)
    return _round(end - start)


def scenes_total_seconds(scenes: list[dict]) -> float:
    """모든 장면의 총 길이(초) = 마지막 장면 마지막 컷의 end(= 콘티 totalSeconds).

    각 장면 end 는 누적 시각이므로 합이 아니라 마지막 end 를 쓴다.
    """
    last_end = 0.0
    for scene in scenes or []:
        timeline = scene.get("timeline") or []
        if timeline:
            last_end = _num(timeline[-1].get("end"), last_end)
    return _round(last_end)


def scene_cut_ids(scene: dict) -> list[str]:
    return [_str(c.get("cutId")) for c in (scene.get("timeline") or [])]
