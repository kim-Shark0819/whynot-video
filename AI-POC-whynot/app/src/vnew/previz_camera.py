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
    """한 인물의 몸 지점 계산 도우미. foot=[x,0,z], actor.heightM."""
    height = float(actor.get("heightM") or 1.7)
    foot = _vec3(foot)

    def y(point: str) -> float:
        return body_y(point, height, pose)

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


def _occludes(cam, blocker, subject) -> bool:
    """카메라에서 볼 때 blocker 가 주인공(subject) 얼굴을 가리는지.

    blocker 의 머리(반지름 HEAD_RADIUS_M)·몸통(폭 BODY_WIDTH_M, 어깨~엉덩이) 투영이 주인공
    얼굴(머리 원) 투영과 화면에서 겹치고, blocker 가 카메라에 더 가까우면 True.

    cam 은 내부 (pos,yaw,pitch,fov) 또는 {"pos","target","fov"} 둘 다 받는다.
    blocker·subject 는 _actor_points 가 만든 point_info(또는 foot·y·eyeY 를 가진 딕셔너리).
    """
    cam_dict = cam if isinstance(cam, dict) and "target" in cam else _to_camera_dict(cam)

    subj_foot = _world(subject, 0.0)
    # 주인공 얼굴(머리 원): 눈~정수리 투영 + 반지름.
    subj_top = project_point(cam_dict, (subj_foot[0], subject["y"]("top"), subj_foot[2]))
    subj_eye = project_point(cam_dict, (subj_foot[0], subject["eyeY"], subj_foot[2]))
    if subj_top is None or subj_eye is None:
        return False
    subj_depth = min(subj_top["depth"], subj_eye["depth"])
    sx = (subj_top["screenX"] + subj_eye["screenX"]) / 2.0
    sy = (subj_top["screenY"] + subj_eye["screenY"]) / 2.0
    # 머리 원 화면 반지름: 정수리~눈 세로 거리에 HEAD_RADIUS_M 여유를 더해 대략 잡는다.
    face_r = max(abs(subj_top["screenY"] - subj_eye["screenY"]), 0.02) + 0.03

    blk_foot = _world(blocker, 0.0)
    # 몸통: 어깨~엉덩이. 머리: 정수리~턱.
    body = _screen_span(cam_dict, blk_foot, blocker["y"]("hips"), blocker["y"]("shoulder"), BODY_WIDTH_M / 2.0)
    head = _screen_span(cam_dict, blk_foot, blocker["y"]("chin"), blocker["y"]("top"), HEAD_RADIUS_M)
    for span in (body, head):
        if span is None:
            continue
        # blocker 가 주인공보다 카메라에 더 가까워야 가린다.
        if span["depth"] >= subj_depth:
            continue
        # 얼굴 원(중심 sx,sy, 반지름 face_r)과 blocker 사각형이 화면에서 겹치는가.
        nearest_x = min(max(sx, span["x0"]), span["x1"])
        nearest_y = min(max(sy, span["y0"]), span["y1"])
        if _hypot2(nearest_x - sx, nearest_y - sy) <= face_r * face_r:
            return True
    return False


def _hypot2(a, b):
    return a * a + b * b


def _move_to_edge(cam_dict, subject, info, target_screen_x, side_dir):
    """info(상대)를 카메라 시선 수직 방향으로 옮겨 "가림이 없는" 자리 중 투영 x 가 target_screen_x
    에 가장 가까운 자리를 고른다.

    우선순위(지시서 051): (1) 0~MAX_CHEAT_M 범위에서 가림이 없고 MIN_BODY_GAP_M 을 지키는 후보들
    중에서 (2) 투영 x 가 target_screen_x 에 가장 가까운 것. 양쪽 방향 모두 탐색하되 side_dir(그 사람
    others.side) 쪽을 먼저 본다(동점이면 먼저 찾은 쪽 유지). 가림 없는 후보가 하나도 없으면 None.
    side_dir: +1/-1. 반환 (best_off_xz, best_x) 또는 None.
    """
    subj_foot = _world(subject, 0.0)
    base_foot = (info["foot"][0], 0.0, info["foot"][2])
    # 카메라 시선(바닥면)에 수직인 좌우 이동 축.
    cpos = _vec3(cam_dict.get("pos"))
    ctgt = _vec3(cam_dict.get("target"))
    fwd = _norm((ctgt[0] - cpos[0], 0.0, ctgt[2] - cpos[2]))
    if fwd == (0.0, 0.0, 0.0):
        fwd = (0.0, 0.0, -1.0)
    right = _norm((-fwd[2], 0.0, fwd[0]))
    saved_off = info["off"]
    best = None
    best_err = None
    steps = 24
    try:
        for order_dir in (side_dir, -side_dir):
            for i in range(steps + 1):
                move = MAX_CHEAT_M * i / steps
                off = (right[0] * move * order_dir, 0.0, right[2] * move * order_dir)
                cand_foot = (base_foot[0] + off[0], 0.0, base_foot[2] + off[2])
                if _planar_dist(cand_foot, subj_foot) < MIN_BODY_GAP_M - 1e-6:
                    continue
                sp = project_point(cam_dict, (cand_foot[0], info["eyeY"], cand_foot[2]))
                if sp is None:
                    continue
                # 가림이 없는 후보만 받는다.
                info["off"] = off
                if _occludes(cam_dict, info, subject):
                    continue
                err = abs(sp["screenX"] - target_screen_x)
                if best_err is None or err < best_err - 1e-9:
                    best_err = err
                    best = (off, sp["screenX"])
    finally:
        info["off"] = saved_off
    return best


# ================================================================ place_cut


def place_cut(grammar: dict, actors_in_cut: list[dict], room: dict | None, side: int) -> dict:
    """한 컷의 카메라·배우 조정·일치도를 계산한다.

    actors_in_cut: [{id, name, heightM, foot:[x,0,z], pose, facingDeg?}]. grammar 는 normalize_grammar
    결과. side 는 scene_line 의 기본 쪽(+1|-1); 이 컷이 crossed 면 호출자가 반대 부호를 준다.
    반환 {camera, actors, cheats, fit}.
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
        match = match_visible(
            cam_dict,
            _world(subject, 0.0),
            subject["height"],
            subject["pose"],
            top_y,
            subject_x,
            bottom,
        )
        dist = _planar_dist(cam[0], _world(subject, 0.0))
    else:
        match = 0.0
        dist = 0.0

    # --- 옮긴 뒤 남은 가림 검사(숨기지 않은 비주인공 전원)·발 간격·기울임 필요 ---
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
            if _occludes(cam_dict, info, subject):
                occluded = True

    # 남은 가림이 있으면 match 에 0.5 를 곱해 낮은 컷으로 잡히게 한다.
    if occluded:
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
    }
    if min_gap is not None:
        fit["minBodyGapM"] = round(min_gap, 3)
    if needs_lean:
        fit["needsLean"] = True

    return {
        "camera": cam_dict,
        "actors": out_actors,
        "cheats": cheats,
        "fit": fit,
    }


def _place_single(grammar, people, subject, partner, side_normal, side, shot_size, bottom, top_y, subject_x, others_grammar, cheats):
    """clean/dirty/insert/pov 단독샷."""
    if subject is None:
        # 인물이 전혀 없으면 방 가운데를 보는 기본 카메라.
        return _look((0.0, 1.5, 3.0), (0.0, 1.0, 0.0), LENS[shot_size][0]), []
    if partner is None:
        # 상대가 없으면 주인공 정면(-z)을 상대 방향처럼 둔다.
        partner = {"foot": (subject["foot"][0], 0.0, subject["foot"][2] - 1.0), "off": (0.0, 0.0, 0.0),
                   "y": subject["y"], "eyeY": subject["eyeY"], "height": subject["height"], "pose": "stand"}
    fov = LENS[shot_size][0]
    cam0, dist, direction = _single_camera(grammar, subject, partner, side_normal, side, shot_size)

    def residual(cam):
        return _subject_residual(cam, subject, top_y, subject_x, bottom)

    cam = _refine(cam0, fov, residual, _scale(direction, -1.0))

    # 가짜 상대 숨기기/끌어오기(비주인공 전원 가림 검사).
    cam_dict = _to_camera_dict(cam)
    for name, info in list(people.items()):
        if info is subject:
            continue
        other_spec = others_grammar.get(name)
        how = str((other_spec or {}).get("how") or "")
        actor_id = info.get("id")
        if actor_id is None:
            continue
        if other_spec is None or how not in EDGE_HOWS:
            # others 에 없는 사람(화면에 안 나와야 함): 카메라에 너무 가깝거나 주인공을 가리면 숨긴다.
            dcam = _planar_dist(cam[0], _world(info, 0.0))
            if dcam < HIDE_NEAR_M or _occludes(cam_dict, info, subject):
                cheats.append({"type": "hide", "actor": actor_id})
            continue
        # others 에 있는 사람(hands_only/partial_edge/shoulder_or_back/lips_or_face_part):
        # 가리면 카메라 시선 수직 방향으로 가리지 않는 가장 가까운 자리로 옮긴다.
        side_str = str((other_spec or {}).get("side") or "").strip().lower()
        screen_x = other_spec.get("screenX")
        # 화면 가장자리 목표: screenX 0.8 이상이면 오른쪽 가장자리(≈0.85), 0.2 이하면 왼쪽(≈0.15).
        if isinstance(screen_x, (int, float)) and not isinstance(screen_x, bool):
            target_x = 0.85 if float(screen_x) >= 0.8 else (0.15 if float(screen_x) <= 0.2 else float(screen_x))
        else:
            target_x = 0.85 if side_str == "right" else (0.15 if side_str == "left" else 0.85)
        # 우선 탐색 방향: others.side 쪽(right=+화면오른쪽). 투영 x 가 큰 쪽으로 가려면 right 축 +.
        side_dir = 1.0 if target_x >= 0.5 else -1.0
        # 지금 자리에서 가리지 않으면 옮기지 않는다(있는 그대로 둔다).
        if not _occludes(cam_dict, info, subject):
            continue
        # 가린다면: 0~MAX_CHEAT_M 에서 가림 없고 간격 지키는 후보 중 투영 x 가 target 에 가장 가까운 자리로.
        moved = _move_to_edge(cam_dict, subject, info, target_x, side_dir)
        if moved is not None:
            info["off"] = moved[0]
            dist_moved = _planar_dist((info["foot"][0] + moved[0][0], 0.0, info["foot"][2] + moved[0][2]), info["foot"])
            cheats.append({"type": "move", "actor": actor_id, "dist": round(dist_moved, 3)})
        else:
            # 가림 없는 후보가 하나도 없으면 숨긴다.
            info["off"] = (0.0, 0.0, 0.0)
            cheats.append({"type": "hide", "actor": actor_id, "reason": "occlusion"})
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
