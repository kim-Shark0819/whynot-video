"""vnew 프리비즈 — 인물↔소품 겹침 보정·앉을 자리(084 R1, 2026-10-28).

인물 몸 부피(자세별 근사)와 소품 부피(box·cylinder, rotY 반영)를 겹치지 않게 맞춘다.

- 앉은 인물(pose "sit"): 앉을 소품을 결정적 규칙으로 고르고(pick_seat), 엉덩이 높이 = 소품 윗면,
  다리 방향(choose_leg_yaw)·각도(solve_legs)를 정해 `sit = {propId, hipY, legPitchDeg, legYawDeg}` 를 둔다.
  앉은 인물은 소품 바닥면 안으로 조금 당길 뿐 밀지 않는다.
- 그 밖의 인물: 겹치면 바닥면(x·z)에서 최소 이동으로 밀어낸다(push_out). 못 밀면 그대로 두고 unresolved.

스펙: .kiro/specs/previz-camera-only/design.md §4.2-4.5. 상수·순서는 그 표와 같다. 난간 전용 코드는 없다.
좌표계는 previz_schema 와 같다(x 오른쪽, y 위, z 카메라 쪽 +). facingDeg 는 도(0 = +z), 소품 rotY 는
라디안(three.js rotation.y 와 같다). 순수 함수 모듈이다(네트워크·S3·LLM·numpy 없음). 로그만 남긴다.
"""

from __future__ import annotations

import logging
import math

from . import previz_schema
from .previz_camera import MIN_BODY_GAP_M

log = logging.getLogger("vpoc")

# ================================================================ 상수(design.md §4.2-4.5)

# 렌더러 Mannequin 기준 키. s = heightM / BASE_HEIGHT_M.
BASE_HEIGHT_M = 1.7
# 서기·걷기: 세로 원기둥(반지름 BODY_R·s, y 0 ~ STAND_TOP·s).
BODY_R = 0.22
STAND_TOP = 1.76
# 앉기(앉을 소품 있음): 몸통 원기둥(반지름 TORSO_R·s, y hipY+TORSO_GAP ~ hipY+TORSO_TOP·s).
TORSO_R = 0.20
TORSO_TOP = 0.86
TORSO_GAP = 0.05
# 다리: 허벅지·정강이 구(반지름·s), 좌우 간격 ±LEG_X·s, 길이 L1·s·L2·s.
THIGH_R = 0.08
SHIN_R = 0.07
LEG_X = 0.10
THIGH_L = 0.46
SHIN_L = 0.53
THIGH_KS = (0.25, 0.5, 0.75, 1.0)
SHIN_KS = (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)
# 앉기(앉을 소품 없음): 세로 원기둥(반지름 0.30·s, y 0 ~ 0.5 + 0.86·s).
LOOSE_SIT_R = 0.30
LOOSE_SIT_HIP = 0.5
# 무릎 꿇기: 세로 원기둥(반지름 0.30·s, y 0 ~ 1.41·s).
KNEEL_R = 0.30
KNEEL_TOP = 1.41
# 눕기: 인물 기준 상자(x ±0.28·s, 앞뒤 z −1.80·s ~ +0.15·s, y 0 ~ 0.40·s).
LIE_HALF_X = 0.28
LIE_BACK = 1.80
LIE_FRONT = 0.15
LIE_TOP = 0.40

# 침투 깊이가 이 값(m)을 넘을 때만 겹침이다.
EPS = 0.01
# 다리 각도 후보(도, 0 = 허벅지 수평).
LEG_PITCHES = (0, 15, 30, 45, 60, 75)

# 앉을 소품 후보: 윗면 높이 범위(m)와 등급별 거리(m).
SEAT_TOP_MIN = 0.25
SEAT_TOP_MAX = 1.30
SIT_ON_MAX_D = 0.6  # 배우 조정 상한(previz_camera.MAX_CHEAT_M)과 같다.
SEAT_NEAR_D = 0.25
SEAT_WORDS_KO = ("앉", "걸터", "의자", "벤치", "소파", "스툴", "계단", "좌판")
SEAT_WORDS_EN = ("sit", "seat", "chair", "bench", "sofa", "stool", "step", "stair", "ledge", "perch")
# 앉을 소품 바닥면 안쪽 여유(m)와 자리 맞춤 fix 를 남기는 최소 이동(m).
SEAT_INSET = 0.03
SEAT_FIX_MIN = 0.005
# 바닥면 긴 변이 짧은 변의 이 배수 이상이면 "길쭉한 자리"(난간·벤치·턱·계단).
ELONGATED_RATIO = 2.0

# 밀어내기: MTV 반복 횟수·여유(m), 고리 탐색 간격·최대 거리(m)·방향 수.
PUSH_ITERS = 8
PUSH_EXTRA = 0.02
RING_STEP = 0.05
RING_STEPS = 20  # 0.05 ~ 1.00
RING_DIRS = 16
# 인물에서 바닥면 거리 이만큼 안의 소품만 본다(성능).
NEAR_PROP_M = 3.0

POSE_SIT = "sit"


# ================================================================ 작은 도우미


def _num(value, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return float(default)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(out) or math.isinf(out):
        return float(default)
    return out


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def wrap_deg(deg: float) -> float:
    """각도(도)를 (−180, 180] 로 감는다."""
    out = math.fmod(float(deg), 360.0)
    if out <= -180.0:
        out += 360.0
    elif out > 180.0:
        out -= 360.0
    return out


def _angle_diff(a: float, b: float) -> float:
    """두 방향각(도)의 차 0~180."""
    return abs(wrap_deg(a - b))


def _yaw_dir(yaw_deg: float) -> tuple[float, float]:
    """방향각(도, 0 = +z) → 바닥면 단위 벡터 (x, z). facingDeg 와 같은 기준."""
    rad = math.radians(yaw_deg)
    return (math.sin(rad), math.cos(rad))


def _foot(ca: dict) -> tuple[float, float]:
    pos = ca.get("pos") if isinstance(ca.get("pos"), (list, tuple)) else []
    x = _num(pos[0], 0.0) if len(pos) > 0 else 0.0
    z = _num(pos[2], 0.0) if len(pos) > 2 else 0.0
    return (x, z)


def _log_id(value) -> str:
    """로그에 넣는 id(LLM·사용자 값)를 안전한 글자만 64자 이하로(줄바꿈 등으로 로그 줄을 꾸미지 못하게)."""
    text = "".join(ch if (ch.isalnum() or ch in "-_.:") else "_" for ch in str(value or ""))
    return text[:64] or "-"


# ================================================================ 소품 부피


def prop_volume(prop: dict, index: int = 0) -> dict:
    """소품 dict → 판정용 부피. box 는 회전 상자, cylinder 는 세로 원기둥(렌더러와 같은 해석)."""
    size = prop.get("size") if isinstance(prop.get("size"), (list, tuple)) else []

    def _dim(i: int) -> float:
        value = _num(size[i], 0.5) if i < len(size) else 0.5
        return max(0.05, value if value else 0.5)

    w, h, d = _dim(0), _dim(1), _dim(2)
    pos = prop.get("pos") if isinstance(prop.get("pos"), (list, tuple)) else []
    cx = _num(pos[0], 0.0) if len(pos) > 0 else 0.0
    cy = _num(pos[1], h / 2.0) if len(pos) > 1 else h / 2.0
    cz = _num(pos[2], 0.0) if len(pos) > 2 else 0.0
    rot = _num(prop.get("rotY"), 0.0)
    shape = "cylinder" if str(prop.get("shape") or "").strip().lower() == "cylinder" else "box"
    label = (str(prop.get("labelKo") or "") + " " + str(prop.get("labelEn") or "")).lower()
    return {
        "id": str(prop.get("id") or ""),
        "index": int(index),
        "shape": shape,
        "cx": cx,
        "cy": cy,
        "cz": cz,
        "w": w,
        "d": d,
        "hx": w / 2.0,
        "hy": h / 2.0,
        "hz": d / 2.0,
        "r": w / 2.0,
        "rot": rot,
        "cos": math.cos(rot),
        "sin": math.sin(rot),
        "label": label,
        "sittable": bool(prop.get("sittable") is True),
    }


def _scene_vols(scene: dict) -> list[dict]:
    vols = [prop_volume(p, i) for i, p in enumerate(scene.get("props") or []) if isinstance(p, dict)]
    vols.sort(key=lambda v: (v["id"], v["index"]))
    return vols


def _to_local(vol: dict, x: float, z: float) -> tuple[float, float]:
    """세계 (x, z) → 소품 로컬. three.js rotation.y 의 역회전."""
    dx = x - vol["cx"]
    dz = z - vol["cz"]
    c, s = vol["cos"], vol["sin"]
    return (c * dx - s * dz, s * dx + c * dz)


def _to_world_dir(vol: dict, lx: float, lz: float) -> tuple[float, float]:
    """소품 로컬 방향 → 세계 방향(rotation.y 회전)."""
    c, s = vol["cos"], vol["sin"]
    return (c * lx + s * lz, -s * lx + c * lz)


def footprint_dist(vol: dict, x: float, z: float) -> float:
    """점 (x, z) 에서 소품 바닥면(회전 사각형·원)까지 수평 거리. 안이면 0."""
    if vol["shape"] == "cylinder":
        return max(0.0, math.hypot(x - vol["cx"], z - vol["cz"]) - vol["r"])
    lx, lz = _to_local(vol, x, z)
    return math.hypot(max(0.0, abs(lx) - vol["hx"]), max(0.0, abs(lz) - vol["hz"]))


def _y_overlap(vol: dict, y0: float, y1: float) -> float:
    return min(y1, vol["cy"] + vol["hy"]) - max(y0, vol["cy"] - vol["hy"])


def _sphere_depth(vol: dict, center: tuple[float, float, float], radius: float) -> float:
    """구 ↔ 소품 침투 깊이(반지름 − 최근접점 거리). > EPS 면 겹침."""
    px, py, pz = center
    if vol["shape"] == "cylinder":
        ex = max(0.0, math.hypot(px - vol["cx"], pz - vol["cz"]) - vol["r"])
    else:
        lx, lz = _to_local(vol, px, pz)
        ex = math.hypot(max(0.0, abs(lx) - vol["hx"]), max(0.0, abs(lz) - vol["hz"]))
    ey = max(0.0, (vol["cy"] - vol["hy"]) - py, py - (vol["cy"] + vol["hy"]))
    return radius - math.hypot(ex, ey)


def _circle_mtv(vol: dict, x: float, z: float, radius: float) -> tuple[float, tuple[float, float]]:
    """바닥면 원(중심 x·z, 반지름) ↔ 소품 바닥면. (침투 깊이, 원을 밖으로 미는 단위 방향)."""
    if vol["shape"] == "cylinder":
        dx = x - vol["cx"]
        dz = z - vol["cz"]
        dist = math.hypot(dx, dz)
        direction = (1.0, 0.0) if dist < 1e-9 else (dx / dist, dz / dist)
        return radius + vol["r"] - dist, direction
    lx, lz = _to_local(vol, x, z)
    qx = _clamp(lx, -vol["hx"], vol["hx"])
    qz = _clamp(lz, -vol["hz"], vol["hz"])
    ox, oz = lx - qx, lz - qz
    dist = math.hypot(ox, oz)
    if dist > 1e-9:
        depth = radius - dist
        ldx, ldz = ox / dist, oz / dist
    else:
        # 원 중심이 사각형 안: 가장 가까운 변 쪽(로컬 x·z 중 침투가 작은 축).
        pen_x = vol["hx"] - abs(lx)
        pen_z = vol["hz"] - abs(lz)
        if pen_x <= pen_z:
            ldx, ldz = (1.0 if lx >= 0 else -1.0), 0.0
            depth = radius + pen_x
        else:
            ldx, ldz = 0.0, (1.0 if lz >= 0 else -1.0)
            depth = radius + pen_z
    return depth, _to_world_dir(vol, ldx, ldz)


def _rect_axes(rect: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    """회전 사각형의 로컬 x·z 축(세계 단위 벡터)."""
    return _to_world_dir(rect, 1.0, 0.0), _to_world_dir(rect, 0.0, 1.0)


def _rect_rect_mtv(person: dict, vol: dict) -> tuple[float, tuple[float, float]]:
    """바닥면 사각형 2개(분리축). (침투 깊이, person 을 미는 방향). 깊이 ≤ 0 이면 떨어져 있다."""
    pa = _rect_axes(person)
    qa = _rect_axes(vol)
    cdx = person["cx"] - vol["cx"]
    cdz = person["cz"] - vol["cz"]
    best = None
    for axis in (pa[0], pa[1], qa[0], qa[1]):
        rp = person["hx"] * abs(axis[0] * pa[0][0] + axis[1] * pa[0][1]) + person["hz"] * abs(axis[0] * pa[1][0] + axis[1] * pa[1][1])
        rq = vol["hx"] * abs(axis[0] * qa[0][0] + axis[1] * qa[0][1]) + vol["hz"] * abs(axis[0] * qa[1][0] + axis[1] * qa[1][1])
        dist = cdx * axis[0] + cdz * axis[1]
        overlap = rp + rq - abs(dist)
        if overlap <= 0:
            return overlap, (1.0, 0.0)
        sign = 1.0 if dist >= 0 else -1.0
        if best is None or overlap < best[0] - 1e-12:
            best = (overlap, (axis[0] * sign, axis[1] * sign))
    return best if best is not None else (0.0, (1.0, 0.0))


# ================================================================ 인물 부피


def _scale(height_m) -> float:
    return _num(height_m, BASE_HEIGHT_M) / BASE_HEIGHT_M


def leg_spheres(x: float, z: float, hip_y: float, s: float, pitch_deg: float, yaw_deg: float) -> list[tuple[str, tuple, float]]:
    """앉은 인물 다리 구 16개 [(thigh|shin, 중심, 반지름)]. 방향 = yaw_deg(다리 방향, §4.2)."""
    gx, gz = _yaw_dir(yaw_deg)
    rx, rz = gz, -gx  # 다리 오른쪽 R_G = (cos g, −sin g)
    th = math.radians(pitch_deg)
    l1 = THIGH_L * s
    l2 = SHIN_L * s
    hip = (x, hip_y, z)
    knee = (x + gx * l1 * math.cos(th), hip_y - l1 * math.sin(th), z + gz * l1 * math.cos(th))
    foot = (knee[0], max(0.0, knee[1] - l2), knee[2])
    out: list[tuple[str, tuple, float]] = []
    for side in (-1.0, 1.0):
        ox = rx * LEG_X * s * side
        oz = rz * LEG_X * s * side
        for k in THIGH_KS:
            out.append(("thigh", (hip[0] + (knee[0] - hip[0]) * k + ox, hip[1] + (knee[1] - hip[1]) * k, hip[2] + (knee[2] - hip[2]) * k + oz), THIGH_R * s))
        for k in SHIN_KS:
            out.append(("shin", (knee[0] + (foot[0] - knee[0]) * k + ox, knee[1] + (foot[1] - knee[1]) * k, knee[2] + (foot[2] - knee[2]) * k + oz), SHIN_R * s))
    return out


def actor_parts(ca: dict, height_m, *, seated: bool | None = None) -> dict:
    """컷 인물의 몸 부피(§4.2). seated 가 None 이면 ca["sit"] 유무로 정한다.

    반환 {"kind": "cyl", x, z, r, y0, y1} | {"kind": "rect", cx, cz, hx, hz, cos, sin, y0, y1}
    | {"kind": "seated", "torso": cyl, "legs": [(tag, 중심, 반지름)], "propId"}.
    """
    s = _scale(height_m)
    x, z = _foot(ca)
    pose = str(ca.get("pose") or "stand").strip().lower()
    sit = ca.get("sit") if isinstance(ca.get("sit"), dict) else None
    if seated is None:
        seated = pose == POSE_SIT and sit is not None
    if pose == POSE_SIT and seated and sit is not None:
        hip = _num(sit.get("hipY"), LOOSE_SIT_HIP)
        facing = _num(ca.get("facingDeg"), 0.0)
        yaw = _num(sit.get("legYawDeg"), facing)
        return {
            "kind": "seated",
            "propId": str(sit.get("propId") or ""),
            "torso": {"kind": "cyl", "x": x, "z": z, "r": TORSO_R * s, "y0": hip + TORSO_GAP, "y1": hip + TORSO_TOP * s},
            "legs": leg_spheres(x, z, hip, s, _num(sit.get("legPitchDeg"), 0.0), yaw),
        }
    if pose == POSE_SIT:
        return {"kind": "cyl", "x": x, "z": z, "r": LOOSE_SIT_R * s, "y0": 0.0, "y1": LOOSE_SIT_HIP + TORSO_TOP * s}
    if pose == "kneel":
        return {"kind": "cyl", "x": x, "z": z, "r": KNEEL_R * s, "y0": 0.0, "y1": KNEEL_TOP * s}
    if pose == "lie":
        f = math.radians(_num(ca.get("facingDeg"), 0.0))
        fx, fz = math.sin(f), math.cos(f)
        mid = (LIE_FRONT - LIE_BACK) / 2.0 * s
        return {
            "kind": "rect",
            "shape": "box",
            "cx": x + fx * mid,
            "cz": z + fz * mid,
            "hx": LIE_HALF_X * s,
            "hz": (LIE_BACK + LIE_FRONT) / 2.0 * s,
            "cos": math.cos(f),
            "sin": math.sin(f),
            "y0": 0.0,
            "y1": LIE_TOP * s,
        }
    return {"kind": "cyl", "x": x, "z": z, "r": BODY_R * s, "y0": 0.0, "y1": STAND_TOP * s}


def _body_at(body: dict, x: float, z: float, x0: float, z0: float) -> dict:
    """발을 (x0, z0) → (x, z) 로 옮긴 몸 부피(서기·무릎·눕기·소품 없는 앉기)."""
    moved = dict(body)
    if body["kind"] == "rect":
        moved["cx"] = body["cx"] + (x - x0)
        moved["cz"] = body["cz"] + (z - z0)
    else:
        moved["x"] = x
        moved["z"] = z
    return moved


def _body_hits(body: dict, vols: list[dict]) -> list[tuple[float, str, tuple[float, float], int]]:
    """몸(원기둥·사각형) ↔ 소품 겹침 [(깊이, 소품 id, 미는 방향, index)] — 소품 id 순."""
    hits = []
    for vol in vols:
        if _y_overlap(vol, body["y0"], body["y1"]) <= EPS:
            continue
        if body["kind"] == "rect":
            if vol["shape"] == "cylinder":
                depth, direction = _circle_mtv(body, vol["cx"], vol["cz"], vol["r"])
                direction = (-direction[0], -direction[1])
            else:
                depth, direction = _rect_rect_mtv(body, vol)
        else:
            depth, direction = _circle_mtv(vol, body["x"], body["z"], body["r"])
        if depth > EPS:
            hits.append((depth, vol["id"], direction, vol["index"]))
    return hits


def _near_vols(vols: list[dict], x: float, z: float) -> list[dict]:
    return [v for v in vols if footprint_dist(v, x, z) <= NEAR_PROP_M]


# ================================================================ 앉을 소품(§4.3)


def _has_seat_word(label: str) -> bool:
    return any(word in label for word in SEAT_WORDS_KO) or any(word in label for word in SEAT_WORDS_EN)


def pick_seat(ca: dict, vols: list[dict]) -> tuple[dict | None, bool]:
    """앉을 소품(부피 dict) 또는 None. 두 번째 값은 sitOn 지정이 무시됐는지."""
    x, z = _foot(ca)
    sit_on = str(ca.get("sitOn") or "").strip()
    hint_sit = ca.get("sit") if isinstance(ca.get("sit"), dict) else {}
    hints = {h for h in (sit_on, str(hint_sit.get("propId") or "").strip()) if h}
    cands = []
    for vol in vols:
        top = vol["cy"] + vol["hy"]
        if not (SEAT_TOP_MIN <= top <= SEAT_TOP_MAX):
            continue
        dist = footprint_dist(vol, x, z)
        if vol["id"] in hints and dist <= SIT_ON_MAX_D:
            grade = 1
        elif vol["sittable"] and dist <= SEAT_NEAR_D:
            grade = 2
        elif _has_seat_word(vol["label"]) and dist <= SEAT_NEAR_D:
            grade = 3
        elif dist <= 1e-9:
            grade = 4
        else:
            continue
        cands.append(((grade, round(dist, 3), -round(top, 4), vol["id"], vol["index"]), vol))
    cands.sort(key=lambda item: item[0])
    ignored = bool(sit_on) and not any(key[0] == 1 and vol["id"] == sit_on for key, vol in cands)
    return (cands[0][1] if cands else None), ignored


def _inset_point(vol: dict, x: float, z: float) -> tuple[float, float]:
    """발을 앉을 소품 바닥면(각 변 SEAT_INSET 줄인 안쪽, 반 크기가 더 작으면 중심선) 안으로."""
    if vol["shape"] == "cylinder":
        dx, dz = x - vol["cx"], z - vol["cz"]
        dist = math.hypot(dx, dz)
        limit = max(0.0, vol["r"] - SEAT_INSET)
        if dist <= limit:
            return (x, z)
        if dist < 1e-9 or limit <= 0:
            return (vol["cx"], vol["cz"])
        return (vol["cx"] + dx / dist * limit, vol["cz"] + dz / dist * limit)
    lx, lz = _to_local(vol, x, z)
    ax = vol["hx"] - SEAT_INSET if vol["hx"] > SEAT_INSET else 0.0
    az = vol["hz"] - SEAT_INSET if vol["hz"] > SEAT_INSET else 0.0
    qx = _clamp(lx, -ax, ax)
    qz = _clamp(lz, -az, az)
    if qx == lx and qz == lz:
        return (x, z)
    wx, wz = _to_world_dir(vol, qx, qz)
    return (vol["cx"] + wx, vol["cz"] + wz)


# ================================================================ 다리 방향·각도(§4.4)


def _legs_hit(x, z, hip_y, s, pitch, yaw, seat: dict, vols: list[dict]) -> str:
    """다리 구 ↔ 소품 첫 겹침 id(소품 id 순). 허벅지는 앉은 소품 제외, 정강이는 포함. 없으면 ""."""
    spheres = leg_spheres(x, z, hip_y, s, pitch, yaw)
    for vol in vols:
        for tag, center, radius in spheres:
            if tag == "thigh" and vol is seat:
                continue
            if _sphere_depth(vol, center, radius) > EPS:
                return vol["id"]
    return ""


def solve_legs(x: float, z: float, hip_y: float, s: float, yaw_deg: float, seat: dict, vols: list[dict], *, feet: str = "") -> tuple[int, bool, str]:
    """다리 방향 하나에 대한 각도. (각도, 풀림 여부, 처음 겹친 소품 id).

    feet(previz-support-reasoning R6): 패널에서 읽은 발 위치. "dangling"(발을 띄움)이면 목표 각도를 0°(허벅지 수평,
    정강이 늘어뜨림)로 둔다. 그 밖(floor·on_prop·빈 값)은 발이 바닥에 닿는 각도 θ* 다(지금 동작).
    """
    if feet == "dangling":
        theta_star = 0.0
    else:
        theta_star = math.degrees(math.asin(_clamp((hip_y - SHIN_L * s) / (THIGH_L * s), 0.0, 1.0)))
    order = sorted(LEG_PITCHES, key=lambda p: (abs(p - theta_star), p))
    first_hit = ""
    for pitch in order:
        hit = _legs_hit(x, z, hip_y, s, pitch, yaw_deg, seat, vols)
        if not hit:
            return pitch, True, ""
        if not first_hit:
            first_hit = hit
    return order[0], False, first_hit


def is_elongated(vol: dict) -> bool:
    """길쭉한 자리: box 이고 바닥면 긴 변 ≥ 짧은 변 × ELONGATED_RATIO."""
    if vol["shape"] != "box":
        return False
    return max(vol["w"], vol["d"]) >= ELONGATED_RATIO * min(vol["w"], vol["d"])


def _room_side(vol: dict, yaw_deg: float) -> bool:
    """법선(yaw_deg)이 소품 중심 → 방 중심(0, 0) 쪽이면 True."""
    nx, nz = _yaw_dir(yaw_deg)
    return (nx * -vol["cx"] + nz * -vol["cz"]) > 1e-9


def choose_leg_yaw(x: float, z: float, hip_y: float, s: float, facing_deg: float, seat: dict, vols: list[dict], *, feet: str = "") -> tuple[float, int, bool, str]:
    """다리 방향(도)·각도. (legYawDeg (−180,180], legPitchDeg, 풀림 여부, 처음 겹친 소품 id). feet 는 solve_legs 에 넘긴다."""
    facing = wrap_deg(facing_deg)
    r = math.degrees(seat["rot"])
    if is_elongated(seat):
        if seat["w"] >= seat["d"]:
            cands = [(r, True), (r + 180.0, False)]  # 짧은 축 = 로컬 z
        else:
            cands = [(r + 90.0, True), (r + 270.0, False)]  # 짧은 축 = 로컬 x
        cands.sort(key=lambda c: (round(_angle_diff(facing, c[0]), 3), 0 if _room_side(seat, c[0]) else 1, 0 if c[1] else 1))
        results = [(yaw, solve_legs(x, z, hip_y, s, yaw, seat, vols, feet=feet)) for yaw, _plus in cands]
        for yaw, (pitch, ok, _hit) in results:
            if ok:
                return wrap_deg(yaw), pitch, True, ""
        yaw, (pitch, _ok, hit) = results[0]
        return wrap_deg(yaw), pitch, False, hit
    pitch, ok, hit = solve_legs(x, z, hip_y, s, facing, seat, vols, feet=feet)
    if ok:
        return facing, pitch, True, ""
    axes = [(r + 90.0 * i, i) for i in range(4)]  # +z, +x, −z, −x
    axes = [a for a in axes if _angle_diff(facing, a[0]) > 1e-6]
    axes.sort(key=lambda a: (round(_angle_diff(facing, a[0]), 3), 0 if _room_side(seat, a[0]) else 1, a[1]))
    for yaw, _i in axes:
        pitch2, ok2, _hit2 = solve_legs(x, z, hip_y, s, yaw, seat, vols, feet=feet)
        if ok2:
            return wrap_deg(yaw), pitch2, True, ""
    return facing, pitch, False, hit


def _torso_hit(torso: dict, seat: dict, vols: list[dict]) -> str:
    for vol in vols:
        if vol is seat:
            continue
        if _y_overlap(vol, torso["y0"], torso["y1"]) <= EPS:
            continue
        depth, _dir = _circle_mtv(vol, torso["x"], torso["z"], torso["r"])
        if depth > EPS:
            return vol["id"]
    return ""


# ================================================================ 밀어내기(§4.5)


def _inside(bounds, x: float, z: float) -> bool:
    xmin, xmax, zmin, zmax = bounds
    return xmin - 1e-9 <= x <= xmax + 1e-9 and zmin - 1e-9 <= z <= zmax + 1e-9


def push_out(body: dict, x0: float, z0: float, vols: list[dict], bounds, others: list[tuple[float, float, float]]) -> tuple[tuple[float, float] | None, str]:
    """겹친 몸을 밀어낸 새 발 위치(겹침 없으면 원래 자리). 못 밀면 (None, 첫 겹친 소품 id).

    others = [(x, z, 지켜야 할 최소 간격)] — 같은 컷 다른 인물.
    """
    first = _body_hits(body, vols)
    if not first:
        return (x0, z0), ""
    first_id = first[0][1]
    xmin, xmax, zmin, zmax = bounds

    def valid(x: float, z: float) -> bool:
        if not _inside(bounds, x, z):
            return False
        for ox, oz, need in others:
            if math.hypot(x - ox, z - oz) < need - 1e-6:
                return False
        return not _body_hits(_body_at(body, x, z, x0, z0), vols)

    x, z = x0, z0
    for _ in range(PUSH_ITERS):
        hits = _body_hits(_body_at(body, x, z, x0, z0), vols)
        if not hits:
            break
        depth, _pid, direction, _idx = sorted(hits, key=lambda h: (-round(h[0], 6), h[1], h[3]))[0]
        x = _clamp(x + direction[0] * (depth + PUSH_EXTRA), xmin, xmax)
        z = _clamp(z + direction[1] * (depth + PUSH_EXTRA), zmin, zmax)
    cand = (round(x, 4), round(z, 4))
    if valid(*cand):
        return cand, first_id
    for step in range(1, RING_STEPS + 1):
        dist = RING_STEP * step
        for k in range(RING_DIRS):
            ang = math.radians(360.0 / RING_DIRS * k)  # 0 = +x, 위에서 보아 반시계(rotY 와 같다)
            cand = (round(x0 + math.cos(ang) * dist, 4), round(z0 - math.sin(ang) * dist, 4))
            if valid(*cand):
                return cand, first_id
    return None, first_id


# ================================================================ 컷·장면 보정


def _heights(scene: dict) -> dict[str, float]:
    return {str(a.get("id")): _num(a.get("heightM"), BASE_HEIGHT_M) for a in scene.get("actors") or [] if isinstance(a, dict)}


def resolve_cut(scene: dict, cut: dict, *, log_fixes: bool = True, pass_name: str = "A", _vols: list[dict] | None = None) -> dict:
    """한 컷의 인물을 제자리에서 보정한다. 반환 {fixes, unresolved}.

    - pose "sit": 앉을 소품을 고르고 sit 을 다시 계산한다(들어온 sit 은 힌트로만). 없으면 sit 을 지운다.
    - 그 밖: sit 을 지우고, 겹치면 밀어낸다.
    처리 순서: 앉을 소품이 있는 인물 먼저(움직이지 않는다), 그다음 인물 id 순.
    """
    vols = _vols if _vols is not None else _scene_vols(scene)
    bounds = previz_schema._room_bounds(scene.get("room") or {})  # noqa: SLF001
    heights = _heights(scene)
    cut_id = str(cut.get("cutId") or "")
    actors = [ca for ca in (cut.get("actors") or []) if isinstance(ca, dict)]
    orig = {id(ca): _foot(ca) for ca in actors}
    fixes: list[dict] = []
    unresolved: list[dict] = []

    def _add_unresolved(actor_id: str, prop_id: str) -> None:
        item = {"actor": actor_id, "prop": prop_id}
        if item in unresolved:
            return
        unresolved.append(item)
        if log_fixes:
            log.warning("vnew.previz.collision.unresolved cut=%s actor=%s prop=%s", _log_id(cut_id), _log_id(actor_id), _log_id(prop_id))

    def _add_fix(actor_id: str, prop_id: str, dist: float, kind: str) -> None:
        fixes.append({"actor": actor_id, "prop": prop_id, "dist": round(dist, 3), "kind": kind, "pass": pass_name})
        if log_fixes:
            log.info(
                "vnew.previz.collision.fix cut=%s actor=%s prop=%s dist=%.3f kind=%s",
                _log_id(cut_id), _log_id(actor_id), _log_id(prop_id), dist, kind,
            )

    seated: list[tuple[dict, dict]] = []
    rest: list[dict] = []
    for ca in actors:
        pose = str(ca.get("pose") or "stand").strip().lower()
        if pose != POSE_SIT:
            ca.pop("sit", None)
            rest.append(ca)
            continue
        x, z = _foot(ca)
        seat, ignored = pick_seat(ca, _near_vols(vols, x, z))
        if ignored and log_fixes:
            log.info(
                "vnew.previz.collision.sit_on_ignored cut=%s actor=%s prop=%s",
                _log_id(cut_id), _log_id(ca.get("id")), _log_id(ca.get("sitOn")),
            )
        if seat is None:
            ca.pop("sit", None)
            rest.append(ca)
        else:
            seated.append((ca, seat))

    for ca, seat in sorted(seated, key=lambda item: str(item[0].get("id") or "")):
        actor_id = str(ca.get("id") or "")
        x, z = _foot(ca)
        nx, nz = _inset_point(seat, x, z)
        moved = math.hypot(nx - x, nz - z)
        if moved > SEAT_FIX_MIN:
            x, z = round(nx, 4), round(nz, 4)
            ca["pos"] = [x, 0.0, z]
            _add_fix(actor_id, seat["id"], moved, "seat")
        s = _scale(heights.get(actor_id, BASE_HEIGHT_M))
        hip = round(seat["cy"] + seat["hy"], 4)
        near = _near_vols(vols, x, z)
        # 패널에서 읽은 발 위치(previz-support-reasoning R6). support 가 없으면 "" — 지금 결과와 같다.
        support = ca.get("support") if isinstance(ca.get("support"), dict) else {}
        feet = str(support.get("feet") or "")
        yaw, pitch, ok, hit = choose_leg_yaw(x, z, hip, s, _num(ca.get("facingDeg"), 0.0), seat, near, feet=feet)
        ca["sit"] = {"propId": seat["id"], "hipY": hip, "legPitchDeg": int(pitch), "legYawDeg": round(yaw, 2)}
        if not ok:
            _add_unresolved(actor_id, hit)
        torso = {"x": x, "z": z, "r": TORSO_R * s, "y0": hip + TORSO_GAP, "y1": hip + TORSO_TOP * s}
        torso_hit = _torso_hit(torso, seat, near)
        if torso_hit:
            _add_unresolved(actor_id, torso_hit)

    for ca in sorted(rest, key=lambda c: str(c.get("id") or "")):
        actor_id = str(ca.get("id") or "")
        x0, z0 = _foot(ca)
        body = actor_parts(ca, heights.get(actor_id, BASE_HEIGHT_M), seated=False)
        others = []
        for other in actors:
            if other is ca:
                continue
            ox, oz = _foot(other)
            before = math.hypot(orig[id(ca)][0] - orig[id(other)][0], orig[id(ca)][1] - orig[id(other)][1])
            others.append((ox, oz, min(MIN_BODY_GAP_M, before)))
        new_pos, first_id = push_out(body, x0, z0, _near_vols(vols, x0, z0), bounds, others)
        if not first_id:
            continue
        if new_pos is None:
            _add_unresolved(actor_id, first_id)
            continue
        ca["pos"] = [new_pos[0], 0.0, new_pos[1]]
        _add_fix(actor_id, first_id, math.hypot(new_pos[0] - x0, new_pos[1] - z0), "push")
    return {"fixes": fixes, "unresolved": unresolved}


def resolve_scene(scene: dict, *, log_fixes: bool = True, pass_name: str = "A") -> dict:
    """장면 timeline 컷마다 resolve_cut(timeline 순). 반환 {cutId: {fixes, unresolved}}."""
    vols = _scene_vols(scene)
    out: dict[str, dict] = {}
    for cut in scene.get("timeline") or []:
        if not isinstance(cut, dict):
            continue
        out[str(cut.get("cutId") or "")] = resolve_cut(scene, cut, log_fixes=log_fixes, pass_name=pass_name, _vols=vols)
    return out


def resolve_scenes(scenes: list[dict], **kw) -> dict:
    """여러 장면. 반환 {cutId: {fixes, unresolved}}(컷 id 는 계획 전체에서 하나다)."""
    out: dict[str, dict] = {}
    for scene in scenes or []:
        if isinstance(scene, dict):
            out.update(resolve_scene(scene, **kw))
    return out


def _overlap_ids(parts: dict, vols: list[dict]) -> list[str]:
    """몸 부피(actor_parts 결과) ↔ 소품 겹침 id 목록(앉은 소품과의 접촉 — 허벅지·몸통 — 은 뺀다)."""
    hit: set[str] = set()
    if parts["kind"] == "seated":
        seat_id = parts["propId"]
        torso = parts["torso"]
        for vol in vols:
            for tag, center, radius in parts["legs"]:
                if tag == "thigh" and vol["id"] == seat_id:
                    continue
                if _sphere_depth(vol, center, radius) > EPS:
                    hit.add(vol["id"])
            if vol["id"] != seat_id and _y_overlap(vol, torso["y0"], torso["y1"]) > EPS:
                if _circle_mtv(vol, torso["x"], torso["z"], torso["r"])[0] > EPS:
                    hit.add(vol["id"])
        return sorted(hit)
    return sorted({h[1] for h in _body_hits(parts, vols)})


def actor_overlaps(scene: dict, ca: dict) -> list[str]:
    """검사·진단용: 지금 자리·sit 기준으로 몸이 겹치는 소품 id 목록(앉은 소품과의 접촉은 뺀다)."""
    heights = _heights(scene)
    vols = _scene_vols(scene)
    parts = actor_parts(ca, heights.get(str(ca.get("id")), BASE_HEIGHT_M))
    return _overlap_ids(parts, vols)


# ================================================================ 옛 장면 겹침 검출(위치를 바꾸지 않음, 084 R1.11)

# 옛 렌더러가 sit 을 그리는 모양: 엉덩이 0.5, 허벅지 수평(0°), 정강이 수직, 다리 = facing.
# 앉은 소품 윗면이 이 엉덩이 + OLD_SEAT_TOL 이하일 때만 그 소품과의 접촉을 허용한다(의자 좌판 0.45 등).
OLD_SEAT_TOL = 0.1


def _legacy_parts(ca: dict, height_m, vols: list[dict]) -> dict:
    """sit 정보가 없는 옛 컷 인물을 지금 화면에 그려지는 모양 그대로의 부피로(검출 전용)."""
    pose = str(ca.get("pose") or "stand").strip().lower()
    if pose != POSE_SIT or isinstance(ca.get("sit"), dict):
        return actor_parts(ca, height_m)
    x, z = _foot(ca)
    seat, _ignored = pick_seat(ca, _near_vols(vols, x, z))
    seat_id = ""
    if seat is not None and seat["cy"] + seat["hy"] <= LOOSE_SIT_HIP + OLD_SEAT_TOL:
        seat_id = seat["id"]
    facing = _num(ca.get("facingDeg"), 0.0)
    probe = dict(ca)
    probe["sit"] = {"propId": seat_id, "hipY": LOOSE_SIT_HIP, "legPitchDeg": 0, "legYawDeg": facing}
    return actor_parts(probe, height_m)


def detect_cut(scene: dict, cut: dict, *, _vols: list[dict] | None = None) -> list[dict]:
    """한 컷의 인물↔소품 겹침을 검출만 한다(장면·컷을 바꾸지 않는다). [{actor, prop}] — 인물 id·소품 id 순."""
    vols = _vols if _vols is not None else _scene_vols(scene)
    heights = _heights(scene)
    items: list[dict] = []
    for ca in sorted((c for c in (cut.get("actors") or []) if isinstance(c, dict)), key=lambda c: str(c.get("id") or "")):
        actor_id = str(ca.get("id") or "")
        x, z = _foot(ca)
        parts = _legacy_parts(ca, heights.get(actor_id, BASE_HEIGHT_M), vols)
        for prop_id in _overlap_ids(parts, _near_vols(vols, x, z)):
            items.append({"actor": actor_id, "prop": prop_id})
    return items


def detect_scene(scene: dict) -> dict:
    """장면의 컷마다 detect_cut. 겹침이 있는 컷만 {cutId: [{actor, prop}]}. 로그를 남기지 않는다(보기 전용)."""
    vols = _scene_vols(scene)
    out: dict[str, list[dict]] = {}
    for cut in scene.get("timeline") or []:
        if not isinstance(cut, dict):
            continue
        items = detect_cut(scene, cut, _vols=vols)
        if items:
            out[str(cut.get("cutId") or "")] = items
    return out
