"""vnew 프리비즈 — 순수 파이썬 카메라 계산기(설계자 2026-10-21, 지시서 049).

패널을 "연출 문법"(샷 크기·샷 종류·얼굴 각도·시선·상대 쪽·높이)으로 읽은 결과(previz_grammar)를
받아 **정석 카메라 자리**(.kiro/steering/camera-grammar.md)를 닫힌 식으로 계산하고, 작은 보정
(refine, 순수 Nelder-Mead)으로 화면 구도(머리 위치·보이는 부분)를 맞춘다.

실험 계산기 ref-camera-solver/solver2.py 를 numpy 없이 옮긴 것이다(앱에는 numpy 가 없다). 외부
호출·네트워크 없이 숫자만 다루는 순수 함수 모듈이다. 좌표계는 previz_schema·previz_fit 과 같다:
x 오른쪽, y 위, z 카메라 쪽(+). fov 는 세로 화각(도), 종횡비 16:9.

핵심(설계자):
- 컷 크기별 렌즈·거리 표(LENS). 클로즈업은 "가까이"가 아니라 "긴 렌즈로 멀리서"(fov 하한 ~8°).
- 정면성(vantage)은 주인공 시선(상대 쪽)에서 잰 방위각(VANTAGE_DEG).
- 장면마다 두 핵심 인물을 잇는 라인(액션 라인). 카메라는 그 한쪽(side)에. 웹툰은 감정 장면에서
  라인을 넘으니 막지 않고 crossed 로 표시만 한다.
- 배우 위치 속이기(cheat, 최대 0.6m)는 정석이다(closeapex). 화면에 없어야 할 상대는 숨기고,
  가장자리에 걸리는 상대(hands_only 등)는 살짝 끌어온다.
"""

from __future__ import annotations

import math

from .previz_fit import body_y, match_visible, project_point  # noqa: F401 — match_visible 재노출

# 종횡비 16:9(가로/세로). fov 는 세로 화각.
ASPECT = 16.0 / 9.0

# 컷 크기별 렌즈표: shotSize → (세로 화각°, 렌즈 이름, 화면 아래에 오는 몸 지점).
# camera-grammar.md 렌즈표. 클로즈업일수록 긴 렌즈(좁은 화각)로 멀리서.
LENS = {
    "ECU": (9.4, "85mm", "chin"),
    "CU": (11.4, "70mm", "shoulder"),
    "MCU": (15.9, "50mm", "chest"),
    "MS": (22.6, "35mm", "waist"),
    "MLS": (32.0, "24mm", "knee"),
    "FS": (32.0, "24mm", "foot"),
    "LS": (42.0, "18mm", "foot"),
}

# 정면성(vantage) → 주인공 시선(상대 쪽)에서 카메라 쪽으로 돌린 방위각(도).
VANTAGE_DEG = {
    "front": 12.0,
    "front3q": 25.0,
    "3q": 40.0,
    "side": 90.0,
    "back3q": 150.0,
    "back": 175.0,
}

# 카메라 높이(cameraHeight) → 주인공 눈높이에 더할 오프셋(m).
HEIGHT_OFFSET = {
    "low": -0.45,   # 앙각(올려다봄)
    "eye": 0.0,     # 아이레벨
    "high": 0.55,   # 부감(내려다봄)
    "overhead": 1.6,  # 버즈아이
}

# 카메라를 인물 몸 축에서 이만큼 밖에 둔다(최소 거리, m).
MIN_AXIS_DIST_M = 0.35
# 배우 조정(cheat) 최대 이동(m).
MAX_CHEAT_M = 0.6
# 화면에 없어야 할 상대가 카메라에 이보다 가까우면 숨긴다(m).
HIDE_NEAR_M = 0.4
# 얼굴이 붙는 샷(키스 등)에서 두 머리 최소 간격(m).
KISS_HEAD_GAP_M = 0.22
# 두 배우의 발 위치 평면 거리를 이보다 가깝게 만드는 배우 조정(move)은 금지한다(m).
# 얼굴이 붙는 샷(키스 KISS_HEAD_GAP)도 발은 이 간격을 지키고, 남는 차이는 그대로 둔다
# (몸을 기울이는 자세로 얼굴만 붙이는 것은 나중 과제 — fit 에 needsLean:true 로 표시).
MIN_BODY_GAP_M = 0.45
# 가림 판정에 쓰는 머리 원 반지름(m)·몸통 폭(m).
HEAD_RADIUS_M = 0.12
BODY_WIDTH_M = 0.45
# 벽 안쪽 여유(m).
WALL_MARGIN_M = 0.1

# single 로 보는 샷 종류.
SINGLE_TYPES = ("clean_single", "dirty_single", "insert", "pov")
# 숨기지 않고 가장자리에 걸리게 두는 상대의 등장 방식(others.how).
EDGE_HOWS = ("hands_only", "partial_edge", "shoulder_or_back", "lips_or_face_part")


# ================================================================ 벡터 도우미(순수)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def _vec3(value, default=(0.0, 0.0, 0.0)):
    out = list(default)
    if isinstance(value, (list, tuple)):
        for i in range(3):
            if i < len(value) and isinstance(value[i], (int, float)) and not isinstance(value[i], bool):
                out[i] = float(value[i])
    return (out[0], out[1], out[2])


def _planar_dist(a, b):
    """바닥면(x,z) 거리."""
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _grammar_num(value, default):
    """숫자(0.0 포함)를 그대로, None·비수치는 기본값. (0.0 이 falsy 라 `or` 를 못 쓴다.)"""
    if isinstance(value, bool) or value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ================================================================ 카메라 표현
#
# 내부는 (pos, yaw, pitch, fov) 로 다루고(refine 이 yaw·pitch 를 흔든다), 밖으로는
# previz_fit 과 같은 {"pos","target","fov"} 로 돌려준다. yaw: 0 이 -z 를 보고 +x 로 돈다.
# pitch: + 가 위.


def _look(pos, target, fov):
    d = _sub(target, pos)
    yaw = math.atan2(d[0], -d[2])
    pitch = math.atan2(d[1], math.hypot(d[0], d[2]))
    return (pos, yaw, pitch, float(fov))


def _forward(yaw, pitch):
    return (
        math.sin(yaw) * math.cos(pitch),
        math.sin(pitch),
        -math.cos(yaw) * math.cos(pitch),
    )


def _to_camera_dict(cam) -> dict:
    """내부 (pos, yaw, pitch, fov) → {"pos","target","fov"}(target 은 앞 1m 점)."""
    pos, yaw, pitch, fov = cam
    fwd = _forward(yaw, pitch)
    target = _add(pos, fwd)
    return {
        "pos": [round(pos[0], 4), round(pos[1], 4), round(pos[2], 4)],
        "target": [round(target[0], 4), round(target[1], 4), round(target[2], 4)],
        "fov": round(float(fov), 3),
    }


# ================================================================ 순수 Nelder-Mead


def nelder_mead(func, x0, step, iters=2500):
    """순수 파이썬 Nelder-Mead(solver.py 와 같은 규칙, numpy 없이)."""
    n = len(x0)
    pts = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step[i]
        pts.append(p)
    vals = [func(p) for p in pts]

    def centroid(points):
        c = [0.0] * n
        for p in points:
            for i in range(n):
                c[i] += p[i]
        return [v / len(points) for v in c]

    for _ in range(iters):
        order = sorted(range(len(pts)), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        c = centroid(pts[:-1])
        worst = pts[-1]
        xr = [c[i] + (c[i] - worst[i]) for i in range(n)]
        fr = func(xr)
        if fr < vals[0]:
            xe = [c[i] + 2.0 * (c[i] - worst[i]) for i in range(n)]
            fe = func(xe)
            if fe < fr:
                pts[-1], vals[-1] = xe, fe
            else:
                pts[-1], vals[-1] = xr, fr
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [c[i] + 0.5 * (worst[i] - c[i]) for i in range(n)]
            fc = func(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                best = pts[0]
                pts = [[best[i] + 0.5 * (p[i] - best[i]) for i in range(n)] for p in pts]
                vals = [func(p) for p in pts]
    best_i = min(range(len(pts)), key=lambda i: vals[i])
    return pts[best_i], vals[best_i]


# ================================================================ 라인(액션 라인)의 쪽


def _grammar_partner_side(grammar: dict) -> str:
    """이 패널에서 상대(안 보이거나 가장자리)가 주인공의 화면 어느 쪽인지. 'left'|'right'|''."""
    side = str(grammar.get("offscreenPartnerSide") or "").strip().lower()
    if side in ("left", "right"):
        return side
    for other in grammar.get("others") or []:
        if not isinstance(other, dict):
            continue
        oside = str(other.get("side") or "").strip().lower()
        if oside in ("left", "right"):
            return oside
    return ""


def line_side(grammar, subject_name, a_name, b_name, a_pos, b_pos) -> int:
    """두 핵심 인물 A·B 를 잇는 라인 기준, 이 패널에서 카메라가 어느 반원에 있는지.

    +1 = 기준(established) 쪽, -1 = 라인 넘음, 0 = 근거 없음(장면 다수결을 따름).
    기준 쪽의 약속: A(a_name)가 화면 왼쪽, B(b_name)가 화면 오른쪽으로 보이는 반원이 +1 이다.
    상대가 주인공의 화면 오른쪽에 있으면 "주인공의 오른쪽에 상대" 가 되는 쪽을 고른다.
    """
    partner = _grammar_partner_side(grammar)
    if partner not in ("left", "right"):
        return 0
    subject = str(subject_name or "")
    # 기준 쪽(+1): a 가 왼쪽·b 가 오른쪽. 그 쪽에서 주인공이 a 면 상대(b)는 주인공의 오른쪽,
    # 주인공이 b 면 상대(a)는 주인공의 왼쪽이다.
    if subject == str(a_name):
        established_partner = "right"
    elif subject == str(b_name):
        established_partner = "left"
    else:
        return 0
    return 1 if partner == established_partner else -1


def scene_line(cuts_grammar, a_name, b_name, a_pos, b_pos) -> dict:
    """장면의 기본 쪽을 다수결로 정하고, 반대쪽 컷은 crossed 로 표시(막지 않음).

    cuts_grammar: {cutId: grammar}. 반환 {side(+1|-1), crossed:[cutId...], perCut:{cutId:side}}.
    """
    per_cut: dict = {}
    votes = {1: 0, -1: 0}
    for cut_id, grammar in (cuts_grammar or {}).items():
        subject = (grammar or {}).get("mainSubject") or ""
        side = line_side(grammar or {}, subject, a_name, b_name, a_pos, b_pos)
        per_cut[cut_id] = side
        if side in (1, -1):
            votes[side] += 1
    base = 1 if votes[1] >= votes[-1] else -1
    crossed = [cut_id for cut_id, side in per_cut.items() if side == -base]
    return {"side": base, "crossed": crossed, "perCut": per_cut}


# ================================================================ 인물 자리(마네킹)


def _actor_points(actor: dict, foot, pose: str):
    """한 인물의 몸 지점 계산 도우미. foot=[x,0,z], actor.heightM, actor.hipY(앉은 엉덩이, 084 R1.6)."""
    height = float(actor.get("heightM") or 1.7)
    foot = _vec3(foot)
    hip_raw = actor.get("hipY")
    hip_y = float(hip_raw) if isinstance(hip_raw, (int, float)) and not isinstance(hip_raw, bool) else None

    def y(point: str) -> float:
        return body_y(point, height, pose, hip_y)

    return {
        "foot": foot,
        "height": height,
        "pose": pose,
        "y": y,
        "eyeY": y("eye"),
        "off": (0.0, 0.0, 0.0),  # cheat 이동(x,z)
    }


def _world(point_info, y_value):
    """인물(cheat 이동 포함)의 (x, y_value, z) 세계 좌표."""
    foot = point_info["foot"]
    off = point_info["off"]
    return (foot[0] + off[0], y_value, foot[2] + off[2])


# ================================================================ 벽 안쪽 제한


def _room_bounds(room: dict | None):
    """방 {width, depth} → (half_w, half_d) 또는 None(열린 공간)."""
    if not isinstance(room, dict):
        return None
    width = room.get("width")
    depth = room.get("depth")
    if not isinstance(width, (int, float)) or not isinstance(depth, (int, float)):
        return None
    return (abs(float(width)) / 2.0 - WALL_MARGIN_M, abs(float(depth)) / 2.0 - WALL_MARGIN_M)


def _inside_room(pos, bounds) -> bool:
    if not bounds:
        return True
    half_w, half_d = bounds
    return abs(pos[0]) <= half_w and abs(pos[2]) <= half_d


def _pull_inside(pos, bounds):
    if not bounds:
        return pos
    half_w, half_d = bounds
    x = min(max(pos[0], -half_w), half_w)
    z = min(max(pos[2], -half_d), half_d)
    return (x, pos[1], z)


# ================================================================ single 자리 계산


def _wider_lens(shot_size: str) -> str:
    """렌즈를 한 단계 넓힌다(벽 밖이면 다시 계산). 더 넓힐 수 없으면 그대로."""
    order = ["ECU", "CU", "MCU", "MS", "MLS", "FS", "LS"]
    if shot_size not in order:
        return shot_size
    index = order.index(shot_size)
    return order[min(index + 1, len(order) - 1)]


def _single_camera(grammar, subject, partner, side_normal, side, shot_size):
    """single 샷의 1차 카메라(보정 전). solver2 place_single 과 같은 방식."""
    fov, _lens, bottom = LENS.get(shot_size, LENS["MCU"])
    vantage = str(grammar.get("vantage") or "3q")
    ang = math.radians(VANTAGE_DEG.get(vantage, VANTAGE_DEG["3q"]))
    height_key = str(grammar.get("cameraHeight") or "eye")
    top_y = _grammar_num(grammar.get("headTopY"), 0.1)

    # 주인공 → 상대 시선 방향 u(바닥면), 라인 쪽 법선 n.
    subj_foot = _world(subject, 0.0)
    part_foot = _world(partner, 0.0)
    u = _norm((part_foot[0] - subj_foot[0], 0.0, part_foot[2] - subj_foot[2]))
    if u == (0.0, 0.0, 0.0):
        u = (0.0, 0.0, -1.0)
    n = _scale(side_normal, float(side) if side in (1, -1) else 1.0)
    direction = _norm(_add(_scale(u, math.cos(ang)), _scale(n, math.sin(ang))))

    # 보이는 길이 → 화면 높이 → 거리.
    extent = subject["y"]("top") - subject["y"](bottom)
    frame_h = extent / max(0.2, 1.0 - top_y)
    dist = frame_h / (2.0 * math.tan(math.radians(fov) / 2.0))

    eye_world = (subj_foot[0], subject["eyeY"], subj_foot[2])
    pos = _add(eye_world, _scale(direction, dist))
    pos = (pos[0], subject["eyeY"] + HEIGHT_OFFSET.get(height_key, 0.0), pos[2])
    mid = (subj_foot[0], (subject["y"]("top") + subject["y"](bottom)) / 2.0, subj_foot[2])
    return _look(pos, mid, fov), dist, direction


def _refine(cam0, fov, residual_fn, free_dist_dir):
    """문법 자리 주변의 작은 보정: yaw·pitch ±0.35, 축 방향 거리, 옆 ±0.3, 높이 ±0.3."""
    pos0, yaw0, pitch0, _ = cam0
    lateral = (math.cos(yaw0), 0.0, math.sin(yaw0))

    def make(v):
        pos = _add(pos0, _scale(free_dist_dir, v[2]))
        pos = _add(pos, _scale(lateral, v[3]))
        pos = (pos[0], pos[1] + v[4], pos[2])
        return (pos, yaw0 + v[0], pitch0 + v[1], fov)

    def cost(v):
        if abs(v[0]) > 0.35 or abs(v[1]) > 0.35 or abs(v[3]) > 0.3 or abs(v[4]) > 0.3:
            return 1e6
        res = residual_fn(make(v))
        return sum(r * r for r in res) + 0.02 * (v[2] ** 2 + v[3] ** 2)

    v, _c = nelder_mead(cost, [0, 0, 0, 0, 0], [0.05, 0.05, 0.3, 0.1, 0.1], iters=2500)
    return make(v)


def _subject_residual(cam, point_info, top_t, x_t, bottom_key):
    """머리 x·머리 위 y·화면 아래 몸 지점 y(목표 1.0) 오차 3개."""
    cam_dict = _to_camera_dict(cam)
    top_sp = project_point(cam_dict, _world(point_info, point_info["y"]("top")))
    bottom_sp = project_point(cam_dict, _world(point_info, point_info["y"](bottom_key)))
    if top_sp is None or bottom_sp is None:
        return [2.0, 2.0, 2.0]
    return [top_sp["screenX"] - x_t, top_sp["screenY"] - top_t, bottom_sp["screenY"] - 1.0]


# ================================================================ 가림(occlusion) 판정


def _screen_span(cam_dict, foot, y_low, y_high, half_width):
    """blocker 의 (어깨~엉덩이 또는 머리) 투영이 화면에서 차지하는 x 범위·y 범위·깊이.

    몸통은 좌우로 half_width(m) 만큼 퍼져 있으니 중심·좌·우 세 점을 투영해 x 범위를 구한다.
    카메라 뒤면 None. 반환 {x0,x1,y0,y1,depth}.
    """
    # 카메라에서 본 가로 방향(right) 단위 벡터(바닥면).
    cpos = _vec3(cam_dict.get("pos"))
    ctgt = _vec3(cam_dict.get("target"))
    fwd = _norm((ctgt[0] - cpos[0], 0.0, ctgt[2] - cpos[2]))
    if fwd == (0.0, 0.0, 0.0):
        fwd = (0.0, 0.0, -1.0)
    right = _norm((-fwd[2], 0.0, fwd[0]))
    xs: list[float] = []
    ys: list[float] = []
    depths: list[float] = []
    for s in (-half_width, 0.0, half_width):
        base = (foot[0] + right[0] * s, 0.0, foot[2] + right[2] * s)
        for y in (y_low, y_high):
            sp = project_point(cam_dict, (base[0], y, base[2]))
            if sp is None:
                return None
            xs.append(sp["screenX"])
            ys.append(sp["screenY"])
            depths.append(sp["depth"])
    return {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys), "depth": min(depths)}


# 가림 겹침 비율 하한(지시서 2차 051 이후): blocker 가 주인공 "보이는 영역"의 이 비율 이상을
# 덮으면 가림으로 본다(영역 면적 대비).
OCCLUDE_RATIO = 0.15
# 일부만 보여야 하는 사람(shoulder_or_back/partial_edge)의 화면 안 면적 상한(프레임 대비).
EDGE_AREA_MAX = 0.35
# 화면 가장자리(머리가 화면 밖인지) 판정에 쓰는 프레임 여유.
FRAME_EPS = 1e-6


def _clip_box_to_screen(box):
    """화면 사각형 {x0,x1,y0,y1} 을 [0,1]x[0,1] 로 자른다. 교집합이 없으면 None."""
    if box is None:
        return None
    x0 = max(0.0, box["x0"])
    x1 = min(1.0, box["x1"])
    y0 = max(0.0, box["y0"])
    y1 = min(1.0, box["y1"])
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "depth": box.get("depth", 0.0)}


def _box_area(box) -> float:
    if box is None:
        return 0.0
    return max(0.0, box["x1"] - box["x0"]) * max(0.0, box["y1"] - box["y0"])


def _box_overlap_area(a, b) -> float:
    """두 화면 사각형의 겹치는 면적(화면 비율)."""
    if a is None or b is None:
        return 0.0
    x0 = max(a["x0"], b["x0"])
    x1 = min(a["x1"], b["x1"])
    y0 = max(a["y0"], b["y0"])
    y1 = min(a["y1"], b["y1"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _subject_visible_box(cam, subject, grammar) -> dict | None:
    """화면 안에 실제로 보이는 주인공 영역의 투영 사각형([0,1] 로 자름).

    위 끝은 머리 위(body_y top), 아래 끝은 그 샷의 화면 아래가 자르는 몸 지점(bottom). 좌우는
    몸통 폭(BODY_WIDTH_M). 투영 사각형을 화면 [0,1]x[0,1] 로 잘라 "실제 보이는" 부분만 남긴다.
    카메라 뒤면 None.

    cam 은 내부 (pos,yaw,pitch,fov) 또는 {"pos","target","fov"} 둘 다 받는다.
    """
    cam_dict = cam if isinstance(cam, dict) and "target" in cam else _to_camera_dict(cam)
    bottom_key = str((grammar or {}).get("_bottom") or "")
    if bottom_key not in ("chin", "shoulder", "chest", "waist", "belly", "hips", "knee", "foot"):
        shot_size = str((grammar or {}).get("shotSize") or "MCU")
        bottom_key = LENS.get(shot_size, LENS["MCU"])[2]
    box = _screen_span(
        cam_dict,
        _world(subject, 0.0),
        subject["y"](bottom_key),
        subject["y"]("top"),
        BODY_WIDTH_M / 2.0,
    )
    return _clip_box_to_screen(box)


def _blocker_box(cam_dict, blocker):
    """blocker(머리+몸통)의 화면 사각형과 깊이. 카메라 뒤면 None. 반환 {x0,x1,y0,y1,depth}."""
    blk_foot = _world(blocker, 0.0)
    body = _screen_span(cam_dict, blk_foot, blocker["y"]("hips"), blocker["y"]("top"), BODY_WIDTH_M / 2.0)
    head = _screen_span(cam_dict, blk_foot, blocker["y"]("chin"), blocker["y"]("top"), HEAD_RADIUS_M)
    boxes = [b for b in (body, head) if b is not None]
    if not boxes:
        return None
    return {
        "x0": min(b["x0"] for b in boxes),
        "x1": max(b["x1"] for b in boxes),
        "y0": min(b["y0"] for b in boxes),
        "y1": max(b["y1"] for b in boxes),
        "depth": min(b["depth"] for b in boxes),
    }


def _occludes(cam, blocker, subject, grammar=None) -> bool:
    """카메라에서 볼 때 blocker 가 주인공(subject)의 "보이는 영역"을 가리는지.

    얼굴 원이 아니라 _subject_visible_box(머리 위~화면 아래 몸 지점, 화면 [0,1] 로 자름)과의
    겹침 면적이 그 영역 면적의 OCCLUDE_RATIO(15%) 이상이고, blocker 가 카메라에 더 가까우면 True.

    cam 은 내부 (pos,yaw,pitch,fov) 또는 {"pos","target","fov"} 둘 다 받는다.
    blocker·subject 는 _actor_points 가 만든 point_info(또는 foot·y·eyeY 를 가진 딕셔너리).
    grammar 가 없으면 보이는 영역의 아래 끝을 가슴(MCU)으로 본다.
    """
    cam_dict = cam if isinstance(cam, dict) and "target" in cam else _to_camera_dict(cam)
    vis = _subject_visible_box(cam_dict, subject, grammar or {})
    if vis is None:
        return False
    vis_area = _box_area(vis)
    if vis_area <= 1e-9:
        return False

    # 주인공 영역의 깊이(가장 가까운 지점)와 비교할 blocker 깊이.
    subj_box = _blocker_box(cam_dict, subject)
    subj_depth = subj_box["depth"] if subj_box is not None else 0.0

    blk = _blocker_box(cam_dict, blocker)
    if blk is None:
        return False
    # blocker 가 주인공보다 카메라에 더 가까워야 가린다.
    if blk["depth"] >= subj_depth:
        return False
    overlap = _box_overlap_area(_clip_box_to_screen(blk), vis)
    return overlap >= vis_area * OCCLUDE_RATIO


def _hypot2(a, b):
    return a * a + b * b


def _person_screen_box(cam_dict, info):
    """한 사람(머리+몸통)의 화면 사각형(자르지 않은 원본). 카메라 뒤면 None."""
    return _blocker_box(cam_dict, info)


def _head_box(cam_dict, info):
    """한 사람 머리(정수리~턱)의 화면 사각형. 카메라 뒤면 None."""
    return _screen_span(cam_dict, _world(info, 0.0), info["y"]("chin"), info["y"]("top"), HEAD_RADIUS_M)


def _overlaps_frame(box) -> bool:
    """사각형이 화면 [0,1]x[0,1] 와 조금이라도 겹치면 True."""
    if box is None:
        return False
    return box["x1"] > FRAME_EPS and box["x0"] < 1.0 - FRAME_EPS and box["y1"] > FRAME_EPS and box["y0"] < 1.0 - FRAME_EPS


def _head_onscreen(cam_dict, info) -> bool:
    """사람의 머리가 화면 안에 조금이라도 보이면 True."""
    return _overlaps_frame(_head_box(cam_dict, info))


def _move_candidate_dirs(cam_dict, side_dir):
    """이동 방향 후보(3방향): 카메라 시선 수직 좌우(우선순위 side_dir 먼저) + 카메라에서 멀어지는
    방향(뒤로). 각 방향은 바닥면 단위 벡터.
    """
    cpos = _vec3(cam_dict.get("pos"))
    ctgt = _vec3(cam_dict.get("target"))
    fwd = _norm((ctgt[0] - cpos[0], 0.0, ctgt[2] - cpos[2]))
    if fwd == (0.0, 0.0, 0.0):
        fwd = (0.0, 0.0, -1.0)
    right = _norm((-fwd[2], 0.0, fwd[0]))
    dirs = []
    dirs.append((right[0] * side_dir, 0.0, right[2] * side_dir))
    dirs.append((-right[0] * side_dir, 0.0, -right[2] * side_dir))
    dirs.append((fwd[0], 0.0, fwd[2]))  # 카메라에서 멀어지는 방향(앞쪽 = 카메라에서 멀어짐).
    return dirs


def _move_to_edge(cam_dict, subject, info, target_screen_x, side_dir, grammar=None, accept=None):
    """info(상대)를 세 방향(카메라 시선 수직 좌우 + 뒤로, 0.05m 간격)으로 옮겨 조건을 만족하는
    자리를 고른다.

    후보 선택 순서(지시서 052): (1) accept 를 만족(머리 밖·가림 없음·간격 등) (2) others.side 와
    같은 쪽으로 나간 후보(side_dir>0 이면 투영 x>0.5 쪽, <0 이면 x<0.5 쪽) (3) 그중 이동 거리 최소.
    같은 쪽 후보가 하나도 없을 때만 반대쪽 후보를 쓴다.

    accept(cam_dict, subject, info) → bool: 받을 만한 자리인지 판단(기본은 "가림 없음").
    0~MAX_CHEAT_M 범위, MIN_BODY_GAP_M 유지. 조건을 만족하는 후보가 하나도 없으면 None.
    side_dir: +1/-1(나가야 할 화면 쪽. +면 오른쪽). 반환 (best_off_xz, best_x) 또는 None.
    """
    if accept is None:
        def accept(cam_d, subj, inf):  # 기본: 가림이 없어야 받는다.
            return not _occludes(cam_d, inf, subj, grammar)
    subj_foot = _world(subject, 0.0)
    base_foot = (info["foot"][0], 0.0, info["foot"][2])
    dirs = _move_candidate_dirs(cam_dict, side_dir)
    saved_off = info["off"]
    best = None
    best_key = None  # (같은쪽=0/반대=1, 이동거리) — 작을수록 좋다.
    step_m = 0.05
    steps = int(MAX_CHEAT_M / step_m)
    try:
        for direction in dirs:
            for i in range(steps + 1):
                move = step_m * i
                off = (direction[0] * move, 0.0, direction[2] * move)
                cand_foot = (base_foot[0] + off[0], 0.0, base_foot[2] + off[2])
                if _planar_dist(cand_foot, subj_foot) < MIN_BODY_GAP_M - 1e-6:
                    continue
                sp = project_point(cam_dict, (cand_foot[0], info["eyeY"], cand_foot[2]))
                if sp is None:
                    continue
                info["off"] = off
                if not accept(cam_dict, subject, info):
                    continue
                # 나간 쪽(투영 x 가 0.5 보다 큰가/작은가)이 others.side 와 같으면 우선.
                exit_right = sp["screenX"] >= 0.5
                want_right = side_dir > 0
                same_side = 0 if exit_right == want_right else 1
                key = (same_side, round(move, 6))
                if best_key is None or key < best_key:
                    best_key = key
                    best = (off, sp["screenX"])
    finally:
        info["off"] = saved_off
    return best


# ================================================================ 카메라 여유(작업 151)
#
# 카메라가 마네킹 머리·몸통 안이거나 너무 가까우면(prod test3 컷 7: 화면이 여의준 머리 안쪽) 옮긴다.
# 마네킹 = 머리 구(중심 눈높이, 반지름 0.16m × 키/1.7) + 몸통 캡슐(어깨 → 엉덩이, 반지름 0.22m).
# 1) 시선 반대로 물린다(pullback, 0.05m 간격, 방 안에서만). 2) 안 되면 주인공 축을 돌아 ±15° → ±30°
# (orbit, 같은 쌍에서 일치도가 가장 좋은 자리). 어깨 너머(over_shoulder)는 앞사람이 화면의 35% 이하여야 한다.
# 렌더러(vnew-previz.js)도 같은 크기로 0.25m 안이면 그리는 카메라만 물린다(설계는 그대로).

CAMERA_MIN_CLEAR_M = 0.35
HEAD_CLEAR_RADIUS_M = 0.16  # × (키 / 1.7)
BODY_CLEAR_RADIUS_M = 0.22
OTS_FOREGROUND_MAX = 0.35
CLEAR_STEP_M = 0.05
CLEAR_MAX_PULL_M = 3.0
CLEAR_ORBIT_DEG = ((15.0, -15.0), (30.0, -30.0))
# 일치도가 이보다 낮은 컷은 다른 시작점 3개로 다시 보정한다(previz.APPROVE_MIN_MATCH 와 같은 기본값).
APPROVE_MIN_MATCH = 0.7
# 다시 보정 시작점: (주인공에서 카메라까지 거리 배율, 주인공 축을 도는 yaw°).
RETRY_SEEDS = ((1.25, 0.0), (0.8, 12.0), (1.1, -12.0))


def _mannequin_volumes(info):
    """한 인물의 (머리 구 중심, 반지름), (몸통 캡슐 위·아래 끝, 반지름). cheat 이동 포함."""
    scale = float(info.get("height") or 1.7) / 1.7
    foot = _world(info, 0.0)
    head = (foot[0], info["y"]("eye"), foot[2])
    shoulder = (foot[0], info["y"]("shoulder"), foot[2])
    hips = (foot[0], info["y"]("hips"), foot[2])
    return (head, HEAD_CLEAR_RADIUS_M * scale), (shoulder, hips, BODY_CLEAR_RADIUS_M)


def _segment_dist(p, a, b) -> float:
    ab = _sub(b, a)
    denom = _dot(ab, ab)
    t = 0.0 if denom < 1e-12 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / denom))
    q = _add(a, _scale(ab, t))
    d = _sub(p, q)
    return math.sqrt(_dot(d, d))


def camera_clearance(pos, infos) -> float:
    """카메라 위치에서 가장 가까운 머리·몸통 표면까지 거리(m). 안이면 음수. 인물이 없으면 inf."""
    best = math.inf
    for info in infos or []:
        (head, head_r), (top, bottom, body_r) = _mannequin_volumes(info)
        d = _sub(pos, head)
        best = min(best, math.sqrt(_dot(d, d)) - head_r, _segment_dist(pos, top, bottom) - body_r)
    return best


def _foreground_area(cam_dict, info) -> float:
    """앞사람(머리+몸통)이 화면에서 차지하는 비율(화면 [0,1] 로 자른 _blocker_box 넓이)."""
    return _box_area(_clip_box_to_screen(_blocker_box(cam_dict, info)))


def _subject_mid(subject, bottom):
    foot = _world(subject, 0.0)
    return (foot[0], (subject["y"]("top") + subject["y"](bottom)) / 2.0, foot[2])


def _subject_fits(cam, subject) -> bool:
    """주인공 머리 위·눈이 화면 안에 있으면 True."""
    cam_dict = _to_camera_dict(cam)
    for point in ("top", "eye"):
        sp = project_point(cam_dict, _world(subject, subject["y"](point)))
        if sp is None or not (-FRAME_EPS <= sp["screenX"] <= 1.0 + FRAME_EPS and -FRAME_EPS <= sp["screenY"] <= 1.0 + FRAME_EPS):
            return False
    return True


def _rotate_xz(vec, deg):
    """바닥면에서 yaw 가 deg 만큼 늘어나는 쪽으로 돌린다(_forward 의 yaw 와 같은 방향)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (vec[0] * c - vec[2] * s, vec[1], vec[0] * s + vec[2] * c)


def _match_score(cam, subject, top_y, subject_x, bottom, others) -> float:
    """place_cut 과 같은 보이는 부분 일치도(남은 가림이 있으면 × 0.5)."""
    cam_dict = _to_camera_dict(cam)
    hip = (subject.get("actor") or {}).get("hipY")
    hip = hip if isinstance(hip, (int, float)) and not isinstance(hip, bool) else None
    score = match_visible(cam_dict, _world(subject, 0.0), subject["height"], subject["pose"], top_y, subject_x, bottom, hip)
    grammar = {"_bottom": bottom}
    if any(_occludes(cam_dict, info, subject, grammar) for info in others):
        score *= 0.5
    return score


def _pose_ok(cam, infos, bounds, foreground, check_room=True, keep=None) -> bool:
    if check_room and bounds is not None and not _inside_room(cam[0], bounds):
        return False
    if camera_clearance(cam[0], infos) < CAMERA_MIN_CLEAR_M - 1e-9:
        return False
    if foreground is not None and _foreground_area(_to_camera_dict(cam), foreground) > OTS_FOREGROUND_MAX + 1e-9:
        return False
    if keep is not None and keep(cam) > 0:
        return False
    return True


def _rule_keeper(cam0, subject, others, bottom):
    """처음 자리의 화면 규칙을 지키는지 세는 함수: 새로 생긴 가림 + 화면 밖이던 머리가 화면 안에 들어온 수.

    (hands_only 상대 머리는 화면 밖, 가림 없음 — _place_single 이 맞춘 것을 옮긴 카메라도 지키게 한다.)
    """
    grammar = {"_bottom": bottom}
    d0 = _to_camera_dict(cam0)
    occluded0 = {id(i) for i in others if _occludes(d0, i, subject, grammar)}
    head_off0 = [i for i in others if not _head_onscreen(d0, i)]

    def broken(cam) -> int:
        d = _to_camera_dict(cam)
        count = sum(1 for i in others if id(i) not in occluded0 and _occludes(d, i, subject, grammar))
        return count + sum(1 for i in head_off0 if _head_onscreen(d, i))

    return broken


def _pose_badness(cam, infos, foreground, keep=None):
    """규칙을 얼마나 못 지켰는지(작을수록 좋다): (여유 부족 m, 화면 규칙 깨짐 수, 앞사람 넓이 초과)."""
    lack = max(0.0, CAMERA_MIN_CLEAR_M - camera_clearance(cam[0], infos))
    excess = 0.0
    if foreground is not None:
        excess = max(0.0, _foreground_area(_to_camera_dict(cam), foreground) - OTS_FOREGROUND_MAX)
    return (round(lack, 6), keep(cam) if keep is not None else 0, round(excess, 6))


def _pull_back(cam, infos, bounds, foreground, tried, keep=None):
    """시선 반대로 0.05m 씩 물린다(방 밖이면 멈춤). 규칙을 지키는 첫 자리 또는 None. tried 에 후보를 모은다."""
    pos0, yaw, pitch, fov = cam
    fwd = _forward(yaw, pitch)
    for k in range(1, int(round(CLEAR_MAX_PULL_M / CLEAR_STEP_M)) + 1):
        pos = _sub(pos0, _scale(fwd, CLEAR_STEP_M * k))
        if bounds is not None and not _inside_room(pos, bounds):
            break
        cand = (pos, yaw, pitch, fov)
        tried.append(cand)
        if _pose_ok(cand, infos, bounds, foreground, keep=keep):
            return cand
    return None


def _clear_search(cam, size, infos, subject, bounds, score_fn, foreground):
    """카메라 여유 규칙(작업 151). 반환 (cam, size, fix). 이미 지키면 (cam, size, None) — 그대로."""
    if subject is None or _pose_ok(cam, infos, bounds, foreground, check_room=False):
        return cam, size, None
    pos0, yaw0, pitch0, fov = cam
    others = [i for i in infos if i is not subject]
    keep = _rule_keeper(cam, subject, others, LENS[size][2])
    fits0 = _subject_fits(cam, subject)
    tried: list = []
    chosen = _pull_back(cam, infos, bounds, foreground, tried, keep)
    kind = "pullback"
    if chosen is None:
        kind = "orbit"
        axis = _world(subject, 0.0)
        fwd = _forward(yaw0, pitch0)
        look_pt = _add(pos0, _scale(fwd, max(0.5, _dot(_sub(_subject_mid(subject, LENS[size][2]), pos0), fwd))))
        for pair in CLEAR_ORBIT_DEG:
            accepted = []
            for deg in pair:
                rel = _rotate_xz(_sub(pos0, axis), deg)
                pos = _add(axis, rel)
                cand = (pos, yaw0 + math.radians(deg), pitch0, fov)
                if bounds is not None and not _inside_room(pos, bounds):
                    pulled = _pull_inside(pos, bounds)
                    look_rot = _add(axis, _rotate_xz(_sub(look_pt, axis), deg))
                    cand = _look(pulled, look_rot, fov)
                tried.append(cand)
                if not _pose_ok(cand, infos, bounds, foreground, keep=keep):
                    cand = _pull_back(cand, infos, bounds, foreground, tried, keep)
                if cand is not None:
                    accepted.append(cand)
            if accepted:
                chosen = max(accepted, key=score_fn)  # 같으면 먼저 시도한 쪽(+)
                break
    if chosen is None:
        # 규칙을 지키는 자리가 없다: 가장 덜 어긴 자리(지금보다 나을 때만).
        best = None
        best_key = _pose_badness(cam, infos, foreground, keep)
        for cand in tried:
            key = _pose_badness(cand, infos, foreground, keep)
            if key < best_key:
                best, best_key = cand, key
        if best is None:
            return cam, size, None
        chosen = best
        kind = "pullback" if abs(chosen[1] - yaw0) < 1e-9 and abs(chosen[2] - pitch0) < 1e-9 else "orbit"
    if fits0 and not _subject_fits(chosen, subject):
        wider = _wider_lens(size)
        if wider != size:
            size = wider
            chosen = (chosen[0], chosen[1], chosen[2], LENS[size][0])
    moved = _sub(chosen[0], pos0)
    return chosen, size, {"kind": kind, "dist": round(math.sqrt(_dot(moved, moved)), 3)}


def _size_for_lens(grammar, lens) -> str:
    """문법 shotSize(ECU 는 CU)에서 fit.lens 와 맞을 때까지 넓힌 샷 크기(previz._rematch_cut 과 같은 순서)."""
    size = str((grammar or {}).get("shotSize") or "MCU")
    if size == "ECU":
        size = "CU"
    if size not in LENS:
        size = "MCU"
    for _ in range(len(LENS)):
        if not lens or LENS[size][1] == lens:
            break
        wider = _wider_lens(size)
        if wider == size:
            break
        size = wider
    return size


def clear_cut_camera(camera: dict, actors_in_cut: list[dict], room: dict | None, grammar: dict, lens: str | None = None):
    """저장된 컷 카메라에 여유 규칙을 다시 건다(previz 3.5 배우 조정 뒤). 지키면 None.

    actors_in_cut: previz._actors_in_cut 모양({id,name,heightM,foot,pose,hipY}). 반환
    {camera, cameraFix, lens} — camera 는 {"pos","target","fov"}.
    """
    camera = camera or {}
    if not camera.get("pos") or not camera.get("target") or not actors_in_cut:
        return None
    grammar = grammar or {}
    infos = []
    for actor in actors_in_cut:
        info = _actor_points(actor, actor.get("foot") or [0, 0, 0], str(actor.get("pose") or "stand"))
        info["actor"] = actor
        info["id"] = actor.get("id")
        info["name"] = str(actor.get("name") or actor.get("id") or "")
        infos.append(info)
    subject_name = str(grammar.get("mainSubject") or "")
    subject = next((i for i in infos if i["name"] == subject_name), infos[0])
    others = [i for i in infos if i is not subject]
    size = _size_for_lens(grammar, lens)
    fov = _grammar_num(camera.get("fov"), LENS[size][0])
    cam = _look(_vec3(camera.get("pos")), _vec3(camera.get("target")), fov)
    foreground = None
    if str(grammar.get("shotType") or "") == "over_shoulder" and others:
        foreground = others[0]
    top_y = _grammar_num(grammar.get("headTopY"), 0.1)
    subject_x = _grammar_num(grammar.get("subjectX"), 0.5)
    bottom = LENS[size][2]

    def score(c):
        return _match_score(c, subject, top_y, subject_x, bottom, others)

    new_cam, new_size, fix = _clear_search(cam, size, infos, subject, _room_bounds(room), score, foreground)
    if fix is None:
        return None
    return {"camera": _to_camera_dict(new_cam), "cameraFix": fix, "lens": LENS[new_size][1]}


# ================================================================ place_cut


def place_cut(grammar: dict, actors_in_cut: list[dict], room: dict | None, side: int, *, min_match: float | None = None) -> dict:
    """한 컷의 카메라·배우 조정·일치도를 계산한다.

    actors_in_cut: [{id, name, heightM, foot:[x,0,z], pose, facingDeg?}]. grammar 는 normalize_grammar
    결과. side 는 scene_line 의 기본 쪽(+1|-1); 이 컷이 crossed 면 호출자가 반대 부호를 준다.
    min_match(작업 151): 주면 일치도가 이보다 낮은 컷을 시작점 3개로 다시 보정한다(previz 는 APPROVE_MIN_MATCH).
    반환 {camera, actors, cheats, fit, retry?}. fit.cameraFix = 카메라 여유 규칙으로 옮겼을 때 {kind, dist}.
    """
    grammar = grammar or {}
    shot_type = str(grammar.get("shotType") or "clean_single")
    shot_size = str(grammar.get("shotSize") or "MCU")
    if shot_size not in LENS:
        shot_size = "MCU"
    subject_name = str(grammar.get("mainSubject") or "")
    top_y = _grammar_num(grammar.get("headTopY"), 0.1)
    subject_x = _grammar_num(grammar.get("subjectX"), 0.5)
    _fov, _lens_name, bottom = LENS[shot_size]
    bounds = _room_bounds(room)

    # 인물 자리(이름 → point_info). pose·facing 은 입력 그대로.
    people: dict = {}
    by_id: dict = {}
    for actor in actors_in_cut or []:
        name = str(actor.get("name") or actor.get("id") or "")
        pose = str(actor.get("pose") or "stand")
        info = _actor_points(actor, actor.get("foot") or [0, 0, 0], pose)
        info["actor"] = actor
        info["id"] = actor.get("id")
        info["name"] = name
        people[name] = info
        if actor.get("id") is not None:
            by_id[actor.get("id")] = info

    # 라인 법선: 두 핵심 인물(주인공 + 첫 상대) 기준. 상대가 없으면 주인공 정면(-z)을 라인으로 본다.
    subject = people.get(subject_name)
    if subject is None and people:
        subject = next(iter(people.values()))
        subject_name = subject["name"]
    others_names = [n for n in people if n != subject_name]
    partner = people.get(others_names[0]) if others_names else None

    if subject is not None and partner is not None:
        line = _norm(_sub(_world(partner, 0.0), _world(subject, 0.0)))
    else:
        line = (0.0, 0.0, -1.0)
    side_normal = _norm((-line[2], 0.0, line[0]))
    if side_normal == (0.0, 0.0, 0.0):
        side_normal = (1.0, 0.0, 0.0)
    if side_normal[2] < 0:  # 기준 쪽은 +z 반원(여의준 화면 왼쪽).
        side_normal = _scale(side_normal, -1.0)

    crossed = bool(grammar.get("_crossed"))

    # --- 가짜 상대 숨기기/끌어오기(clean_single 류) ---
    others_grammar = {str(o.get("name") or ""): o for o in (grammar.get("others") or []) if isinstance(o, dict)}

    def _place_for_size(size):
        """한 렌즈(size)로 카메라·cheats 를 계산한다. (cam, cheats)."""
        cheats_local: list[dict] = []
        _fov2, _lens2, bottom2 = LENS[size]
        grammar["_bottom"] = bottom2
        grammar["_extraVisible"] = []  # 이 렌즈 계산의 extraVisible(재계산마다 비운다).
        if shot_type in ("two_shot", "profile_two_shot"):
            cam_local, _ = _place_two_shot(
                grammar, people, subject, partner, side_normal, side, size, bottom2, top_y, subject_x
            )
        elif shot_type == "over_shoulder":
            cam_local, _ = _place_over_shoulder(
                grammar, people, subject, partner, side_normal, side, size, bottom2, top_y, subject_x
            )
        else:  # single(clean/dirty/insert/pov)
            cam_local, _ = _place_single(
                grammar, people, subject, partner, side_normal, side, size, bottom2, top_y, subject_x,
                others_grammar, cheats_local,
            )
        return cam_local, cheats_local, bottom2

    # --- 벽 안쪽 제한: 밖이면 렌즈를 한 단계 넓혀 재계산 → 그래도 밖이면 끌어오고 표시 ---
    size = shot_size
    cam, cheats, bottom = _place_for_size(size)
    wall_clamped = False
    if bounds is not None:
        while not _inside_room(cam[0], bounds):
            wider = _wider_lens(size)
            if wider == size:  # 더 넓힐 수 없음 → 벽 안으로 끌어오고 표시.
                cam = _enforce_room(cam, subject, bounds)
                wall_clamped = True
                break
            # cheat 으로 옮긴 배우 위치를 초기화하고 넓힌 렌즈로 다시 계산.
            for info in people.values():
                info["off"] = (0.0, 0.0, 0.0)
            size = wider
            cam, cheats, bottom = _place_for_size(size)

    # --- 카메라 여유(작업 151): 머리·몸통 안이나 0.35m 안이면 물리거나 돌린다. 어깨 너머 앞사람은 화면 35% 이하 ---
    hidden_pre = {c["actor"] for c in cheats if c.get("type") == "hide"}
    visible_infos = [info for info in people.values() if info.get("id") not in hidden_pre]
    others_visible = [info for info in visible_infos if info is not subject]
    foreground = None
    if shot_type == "over_shoulder" and partner is not None and partner.get("id") not in hidden_pre:
        foreground = partner
    camera_fix = None
    retry = None
    if subject is not None:
        def score_fn(c, _bottom=bottom):
            return _match_score(c, subject, top_y, subject_x, _bottom, others_visible)

        cam, new_size, camera_fix = _clear_search(cam, size, visible_infos, subject, bounds, score_fn, foreground)
        if new_size != size:
            size = new_size
            bottom = LENS[size][2]

        # --- 일치도 낮은 컷 다시 보정(작업 151): 시작점 3개, 여유 규칙을 지키는 가장 좋은 자리만 ---
        if min_match is not None:
            before = _match_score(cam, subject, top_y, subject_x, bottom, others_visible)
            if before < float(min_match) - 1e-9:
                best_cam, after = _retry_low_match(
                    cam, grammar, shot_type, subject, partner, visible_infos, others_visible, bounds, foreground,
                    top_y, subject_x, bottom, before,
                )
                retry = {"before": round(before, 4), "after": round(after, 4)}
                if best_cam is not None:
                    cam = best_cam
                    camera_fix = None  # 다시 보정한 자리는 처음부터 규칙을 지킨다.

    # --- 얼굴 방향(facingDeg): faceTurn 으로 그 컷 actors 의 facingDeg 를 바꾼다(cheat 아님) ---
    _apply_facing(grammar, people, subject, partner, cam)

    # --- 배우 조정 결과(cheats)와 숨긴 인물을 actors 목록에 반영 ---
    hidden_ids = {c["actor"] for c in cheats if c.get("type") == "hide"}
    out_actors = []
    for actor in actors_in_cut or []:
        if actor.get("id") in hidden_ids:
            continue
        info = by_id.get(actor.get("id"))
        copy = dict(actor)
        if info is not None and info["off"] != (0.0, 0.0, 0.0):
            foot = info["foot"]
            copy["foot"] = [foot[0] + info["off"][0], foot[1], foot[2] + info["off"][2]]
        if info is not None and info.get("facingDeg") is not None:
            copy["facingDeg"] = info["facingDeg"]
        out_actors.append(copy)

    # --- 일치도(보이는 부분 기준) ---
    cam_dict = _to_camera_dict(cam)
    if subject is not None:
        subject_hip = (subject.get("actor") or {}).get("hipY")
        match = match_visible(
            cam_dict,
            _world(subject, 0.0),
            subject["height"],
            subject["pose"],
            top_y,
            subject_x,
            bottom,
            subject_hip if isinstance(subject_hip, (int, float)) and not isinstance(subject_hip, bool) else None,
        )
        dist = _planar_dist(cam[0], _world(subject, 0.0))
    else:
        match = 0.0
        dist = 0.0

    # --- 옮긴 뒤 남은 가림 검사(숨기지 않은 비주인공 전원)·발 간격·기울임 필요 ---
    grammar["_bottom"] = bottom
    hidden_now = {c["actor"] for c in cheats if c.get("type") == "hide"}
    occluded = False
    min_gap = None
    if subject is not None:
        subj_foot = _world(subject, 0.0)
        for name, info in people.items():
            if info is subject:
                continue
            if info.get("id") in hidden_now:
                continue  # 숨긴 사람은 화면에 없으니 가림·간격 대상이 아니다.
            gap = _planar_dist(_world(info, 0.0), subj_foot)
            min_gap = gap if min_gap is None else min(min_gap, gap)
            if _occludes(cam_dict, info, subject, grammar):
                occluded = True

    # 규칙을 못 지킨(화면에 더 보이는) 사람 id 목록.
    extra_visible = [aid for aid in (grammar.get("_extraVisible") or []) if aid not in hidden_now]

    # 남은 가림·extraVisible 이 있으면 match 에 0.5 를 곱해 낮은 컷으로 잡히게 한다.
    if occluded or extra_visible:
        match *= 0.5

    # 기울임 필요: 얼굴 붙는 샷(ECU/CU two_shot)에서 발 간격을 지키느라 머리가 KISS_HEAD_GAP 보다
    # 멀리 남았으면 fit 에 표시(기울이기 자세는 나중 과제).
    needs_lean = bool(grammar.get("_needsLean"))

    fit = {
        "match": round(match, 4),
        "lens": LENS[size][1],
        "shotSize": size,
        "distanceM": round(dist, 3),
        "vantageDeg": VANTAGE_DEG.get(str(grammar.get("vantage") or "3q"), VANTAGE_DEG["3q"]),
        "side": int(side) if side in (1, -1) else 0,
        "crossed": crossed,
        "wallClamped": wall_clamped,
        "occluded": occluded,
        "extraVisible": extra_visible,
    }
    if min_gap is not None:
        fit["minBodyGapM"] = round(min_gap, 3)
    if needs_lean:
        fit["needsLean"] = True
    if camera_fix is not None:
        fit["cameraFix"] = camera_fix

    out = {
        "camera": cam_dict,
        "actors": out_actors,
        "cheats": cheats,
        "fit": fit,
    }
    if retry is not None:
        out["retry"] = retry  # 로그용(설계에 저장하지 않는다).
    return out


def _retry_low_match(cam, grammar, shot_type, subject, partner, infos, others, bounds, foreground,
                     top_y, subject_x, bottom, before):
    """일치도 낮은 컷: RETRY_SEEDS 시작점마다 _refine 을 다시 돌려 가장 좋은 자리. (cam|None, 가장 좋은 점수).

    받는 자리: 방 안·여유 규칙(머리·몸통 0.35m, 어깨 너머 앞사람 35%)·지금 화면 밖인 사람은 계속 화면 밖.
    지금보다 좋을 때만 바꾼다(나빠지지 않는다). 순수 계산(LLM·유료 호출 없음).
    """
    pos0, _yaw0, _pitch0, fov = cam
    aim = _subject_mid(subject, bottom)
    rel0 = _sub(pos0, aim)
    if _dot(rel0, rel0) < 1e-9:
        return None, before
    cam_dict0 = _to_camera_dict(cam)
    offscreen0 = [info for info in others if not _overlaps_frame(_person_screen_box(cam_dict0, info))]

    x2 = None
    if shot_type in ("two_shot", "profile_two_shot") and partner is not None:
        x2 = 1.0 - subject_x
        for other in grammar.get("others") or []:
            if isinstance(other, dict) and str(other.get("name") or "") == partner.get("name"):
                x2 = float(other.get("screenX") or (1.0 - subject_x))
                break

    def residual(c):
        base = _subject_residual(c, subject, top_y, subject_x, bottom)
        if x2 is not None:
            top_sp = project_point(_to_camera_dict(c), _world(partner, partner["y"]("top")))
            base.append((top_sp["screenX"] if top_sp else 9.0) - x2)
        return base

    best_cam, best = None, before
    for dist_scale, yaw_deg in RETRY_SEEDS:
        seed_pos = _add(aim, _scale(_rotate_xz(rel0, yaw_deg), dist_scale))
        seed = _look(seed_pos, aim, fov)
        cand = _refine(seed, fov, residual, _norm(_sub(aim, seed_pos)))
        if not _pose_ok(cand, infos, bounds, foreground):
            continue
        cand_dict = _to_camera_dict(cand)
        if any(_overlaps_frame(_person_screen_box(cand_dict, info)) for info in offscreen0):
            continue
        score = _match_score(cand, subject, top_y, subject_x, bottom, others)
        if score > best + 1e-9:
            best_cam, best = cand, score
    return best_cam, best


def _place_single(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x, others_grammar, cheats):
    """clean/dirty/insert/pov 단독샷.

    - others 에 없는 사람(화면에 나오면 안 됨): 투영(머리+몸통)이 화면과 조금이라도 겹치면 화면 밖으로
      나가는 가장 가까운 자리로 옮긴다(최대 MAX_CHEAT_M). 안 되면 hide.
    - others 에 있는 사람(how 제약): hands_only/lips_or_face_part → 머리가 화면 밖이어야 한다.
      shoulder_or_back/partial_edge → 화면 안 면적 ≤ EDGE_AREA_MAX·주인공 가림 < OCCLUDE_RATIO.
      full → 가림 규칙만. 조건을 못 지키면 others.screenX 에 가장 가까운 자리로 옮기고, 그래도
      안 되면 그대로 두되 grammar['_extraVisible'] 에 id 를 모은다(hide 하지 않는다).
    """
    if subject is None:
        # 인물이 전혀 없으면 방 가운데를 보는 기본 카메라.
        return _look((0.0, 1.5, 3.0), (0.0, 1.0, 0.0), LENS[shot_size][0]), []
    # 보이는 영역의 아래 끝(이 샷의 화면 아래 몸 지점)을 grammar 에 실어 가림 판정이 쓰게 한다.
    grammar["_bottom"] = bottom
    if partner is None:
        # 상대가 없으면 주인공 정면(-z)을 상대 방향처럼 둔다.
        partner = {"foot": (subject["foot"][0], 0.0, subject["foot"][2] - 1.0), "off": (0.0, 0.0, 0.0),
                   "y": subject["y"], "eyeY": subject["eyeY"], "height": subject["height"], "pose": "stand"}
    fov = LENS[shot_size][0]
    cam0, dist, direction = _single_camera(grammar, subject, partner, side_normal, side, shot_size)

    def residual(cam):
        return _subject_residual(cam, subject, top_y, subject_x, bottom)

    cam = _refine(cam0, fov, residual, _scale(direction, -1.0))

    cam_dict = _to_camera_dict(cam)
    extra_visible = grammar.setdefault("_extraVisible", [])
    for name, info in list(people.items()):
        if info is subject:
            continue
        other_spec = others_grammar.get(name)
        how = str((other_spec or {}).get("how") or "")
        actor_id = info.get("id")
        if actor_id is None:
            continue
        if (info.get("actor") or {}).get("locked"):
            continue  # 작업 200: 사용자가 직접 놓은 인물은 옮기거나 숨기지 않는다(배우 조정 없음).

        if other_spec is None or how not in EDGE_HOWS:
            # 규칙 2 — 화면에 나오면 안 되는 사람(others 에 없음).
            dcam = _planar_dist(cam[0], _world(info, 0.0))
            offscreen = not _overlaps_frame(_person_screen_box(cam_dict, info))
            if dcam >= HIDE_NEAR_M and offscreen:
                continue  # 이미 화면 밖이고 카메라에서 충분히 멀다 — 그대로 둔다.
            # 주인공을 가리거나(블로커) 카메라에 바싹 붙은 경우: 끌어내도 깨끗이 못 비키므로 숨긴다.
            if dcam < HIDE_NEAR_M or _occludes(cam_dict, info, subject, grammar):
                info["off"] = (0.0, 0.0, 0.0)
                cheats.append({"type": "hide", "actor": actor_id})
                continue

            # 그 밖에 화면 가장자리에만 걸치는 사람: 화면 밖으로 나가는 가장 가까운 자리로 옮긴다.
            def accept_offscreen(cam_d, subj, inf):
                return not _overlaps_frame(_person_screen_box(cam_d, inf))

            side_str = str((other_spec or {}).get("side") or "").strip().lower()
            side_dir = 1.0 if side_str == "right" else -1.0
            moved = _move_to_edge(cam_dict, subject, info, 1.5 if side_dir > 0 else -0.5,
                                  side_dir, grammar, accept=accept_offscreen)
            if moved is not None:
                info["off"] = moved[0]
                dist_moved = _planar_dist(
                    (info["foot"][0] + moved[0][0], 0.0, info["foot"][2] + moved[0][2]), info["foot"]
                )
                cheats.append({"type": "move", "actor": actor_id, "dist": round(dist_moved, 3), "reason": "offscreen"})
            else:
                info["off"] = (0.0, 0.0, 0.0)
                cheats.append({"type": "hide", "actor": actor_id})
            continue

        # 규칙 3 — 일부만 보여야 하는 사람(how 제약). 목표 화면 가장자리.
        side_str = str((other_spec or {}).get("side") or "").strip().lower()
        screen_x = other_spec.get("screenX")
        if isinstance(screen_x, (int, float)) and not isinstance(screen_x, bool):
            target_x = 0.85 if float(screen_x) >= 0.8 else (0.15 if float(screen_x) <= 0.2 else float(screen_x))
        else:
            target_x = 0.85 if side_str == "right" else (0.15 if side_str == "left" else 0.85)
        side_dir = 1.0 if target_x >= 0.5 else -1.0

        def accept_for_how(cam_d, subj, inf, _how=how):
            # 공통: 주인공 보이는 영역 가림이 OCCLUDE_RATIO 미만이어야 한다.
            if _occludes(cam_d, inf, subj, grammar):
                return False
            if _how in ("hands_only", "lips_or_face_part"):
                # 머리는 화면 밖이어야 한다(손·얼굴 일부만 걸림).
                return not _head_onscreen(cam_d, inf)
            if _how in ("shoulder_or_back", "partial_edge"):
                # 화면 안 면적이 프레임의 EDGE_AREA_MAX 이하.
                clipped = _clip_box_to_screen(_person_screen_box(cam_d, inf))
                return _box_area(clipped) <= EDGE_AREA_MAX + 1e-6
            return True  # full: 가림 규칙만.

        def accept_no_occlude(cam_d, subj, inf):
            # 완화 조건: 적어도 주인공을 가리지는 않는다(how 제약은 못 지켜도 가림은 피한다).
            return not _occludes(cam_d, inf, subj, grammar)

        # 지금 자리가 이미 조건을 만족하면 옮기지 않는다(051: 있는 그대로 둔다).
        if accept_for_how(cam_dict, subject, info):
            continue
        moved = _move_to_edge(cam_dict, subject, info, target_x, side_dir, grammar, accept=accept_for_how)
        if moved is not None:
            info["off"] = moved[0]
            dist_moved = _planar_dist(
                (info["foot"][0] + moved[0][0], 0.0, info["foot"][2] + moved[0][2]), info["foot"]
            )
            cheats.append({"type": "move", "actor": actor_id, "dist": round(dist_moved, 3)})
            continue
        # how 제약을 지키는 자리가 없다. 적어도 가림만은 피하도록 완화 조건으로 다시 옮긴다(가림이
        # 남으면 match 가 더 깎인다). 그래도 how 제약은 못 지켰으니 extraVisible 로 표시한다.
        relaxed = _move_to_edge(cam_dict, subject, info, target_x, side_dir, grammar, accept=accept_no_occlude)
        if relaxed is not None:
            info["off"] = relaxed[0]
            dist_moved = _planar_dist(
                (info["foot"][0] + relaxed[0][0], 0.0, info["foot"][2] + relaxed[0][2]), info["foot"]
            )
            cheats.append({"type": "move", "actor": actor_id, "dist": round(dist_moved, 3), "reason": "extraVisible"})
        else:
            info["off"] = (0.0, 0.0, 0.0)
        if actor_id not in extra_visible:
            extra_visible.append(actor_id)
    return cam, [subject["name"]]


def _place_two_shot(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x):
    """two_shot / profile_two_shot: 두 사람 중점에서 라인 수직 방향."""
    fov = LENS[shot_size][0]
    if subject is None or partner is None:
        if subject is not None:
            return _place_single(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x, {}, [])
        return _look((0.0, 1.5, 3.0), (0.0, 1.0, 0.0), fov), []

    # 얼굴이 붙는 샷(ECU/CU)은 서로 당기되 발 간격은 MIN_BODY_GAP_M 밑으로 내려가지 않는다
    # (closeapex). 그 아래로는 못 붙이므로 남는 차이는 그대로 두고 fit 에 needsLean 으로 표시한다.
    if shot_size in ("ECU", "CU"):
        gap = _planar_dist(_world(subject, 0.0), _world(partner, 0.0))
        target_gap = max(KISS_HEAD_GAP_M, MIN_BODY_GAP_M)
        move = max(0.0, (gap - target_gap) / 2.0)
        if move > 0:
            dirv = _norm(_sub(_world(partner, 0.0), _world(subject, 0.0)))
            subject["off"] = _scale(dirv, move)
            partner["off"] = _scale(dirv, -move)
        # 발 간격을 MIN_BODY_GAP 까지밖에 못 좁혀 머리가 KISS_HEAD_GAP 보다 멀면 기울임이 필요하다.
        final_gap = _planar_dist(_world(subject, 0.0), _world(partner, 0.0))
        if final_gap > KISS_HEAD_GAP_M + 1e-6:
            grammar["_needsLean"] = True

    sa = _world(subject, 0.0)
    pb = _world(partner, 0.0)
    mid = ((sa[0] + pb[0]) / 2.0, (subject["eyeY"] + partner["eyeY"]) / 2.0, (sa[2] + pb[2]) / 2.0)
    n = _scale(side_normal, float(side) if side in (1, -1) else 1.0)
    extent = subject["y"]("top") - subject["y"](bottom)
    dist = (extent / max(0.2, 1.0 - top_y)) / (2.0 * math.tan(math.radians(fov) / 2.0))
    pos = _add(mid, _scale(n, dist))
    cam0 = _look(pos, mid, fov)

    # 목표 화면 x: 주인공 subjectX, 상대 others.screenX(없으면 1-subjectX).
    x2 = subject_x
    for other in grammar.get("others") or []:
        if isinstance(other, dict) and str(other.get("name") or "") == partner.get("name"):
            x2 = float(other.get("screenX") or (1.0 - subject_x))
            break
    else:
        x2 = 1.0 - subject_x

    def residual(cam):
        base = _subject_residual(cam, subject, top_y, subject_x, bottom)
        cam_dict = _to_camera_dict(cam)
        top_sp = project_point(cam_dict, _world(partner, partner["y"]("top")))
        base.append((top_sp["screenX"] if top_sp else 9.0) - x2)
        return base

    cam = _refine(cam0, fov, residual, _scale(n, -1.0))
    return cam, [subject["name"], partner["name"]]


def _place_over_shoulder(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x):
    """over_shoulder: 앞사람(상대) 머리 뒤에서 뒷사람(주인공) 얼굴을 향한다."""
    fov = LENS[shot_size][0]
    if subject is None:
        return _look((0.0, 1.5, 3.0), (0.0, 1.0, 0.0), fov), []
    if partner is None:
        return _place_single(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x, {}, [])

    subj_foot = _world(subject, 0.0)
    part_foot = _world(partner, 0.0)
    # 상대(앞사람) 머리 뒤 0.5m·바깥쪽 0.25m·어깨 높이.
    to_subject = _norm((subj_foot[0] - part_foot[0], 0.0, subj_foot[2] - part_foot[2]))
    n = _scale(side_normal, float(side) if side in (1, -1) else 1.0)
    shoulder_y = partner["y"]("shoulder")
    behind = _add(part_foot, _scale(to_subject, -0.5))
    behind = _add(behind, _scale(n, 0.25))
    pos = (behind[0], shoulder_y, behind[2])
    mid = (subj_foot[0], (subject["y"]("top") + subject["y"](bottom)) / 2.0, subj_foot[2])
    cam0 = _look(pos, mid, fov)
    direction = _norm(_sub(mid, pos))

    def residual(cam):
        return _subject_residual(cam, subject, top_y, subject_x, bottom)

    cam = _refine(cam0, fov, residual, _scale(direction, -1.0))
    return cam, [subject["name"], partner["name"]]


# ================================================================ 벽 안쪽 보정·얼굴 방향


def _enforce_room(cam, subject, bounds):
    """카메라가 벽 밖이면 벽 안으로 끌어온다(간단 보정)."""
    if bounds is None or subject is None:
        return cam
    pos = cam[0]
    if _inside_room(pos, bounds):
        return cam
    pulled = _pull_inside(pos, bounds)
    return _look(pulled, _add(pulled, _forward(cam[1], cam[2])), cam[3])


def _apply_facing(grammar, people, subject, partner, cam):
    """faceTurn 으로 주인공(필요하면 상대) facingDeg 를 바꾼다. facingDeg: 0=+z, 시계 방향(도).

    - toward_camera: 카메라 쪽. away: 반대. screen_left/right·그 외: 상대 쪽(없으면 그대로).
    """
    if subject is None:
        return
    if (subject.get("actor") or {}).get("locked"):
        return  # 작업 200: 사용자가 직접 정한 방향(facingDeg)은 바꾸지 않는다.

    def facing_to(target_pos, from_pos):
        dx = target_pos[0] - from_pos[0]
        dz = target_pos[2] - from_pos[2]
        return round(math.degrees(math.atan2(dx, dz)) % 360.0, 2)

    turn = str(grammar.get("faceTurn") or "")
    subj_foot = _world(subject, 0.0)
    if turn == "toward_camera":
        subject["facingDeg"] = facing_to(cam[0], subj_foot)
    elif turn == "away":
        deg = facing_to(cam[0], subj_foot)
        subject["facingDeg"] = round((deg + 180.0) % 360.0, 2)
    elif partner is not None:
        subject["facingDeg"] = facing_to(_world(partner, 0.0), subj_foot)
