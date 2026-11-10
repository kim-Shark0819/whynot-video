"""작업 208: 사용자가 ④ 프리비즈에서 직접 잡은 마네킹 자세(rig)·자세 키(keys)를 영어 문장으로 옮긴다.

LLM 없이 결정적으로 계산한다. 정방향 운동학(FK)은 frontend/public/vnew-previz.js 마네킹과 같은 치수·축을 쓴다.
- group: 발 위치 pos, rotation.y = facingDeg(라디안), 크기 heightM / 1.7. 정면은 로컬 +z, 인물의 왼쪽은 로컬 +x.
- hips y 0.9(+rootY) → spine → neck(0, 0.6, 0) → head(머리 중심 0.13 위).
- 어깨 pivot (±0.22, 0.52, 0) → elbow (0, -0.3, 0) → 손(wrist) (0, -0.29, 0).
- 엉덩이 pivot (±0.1, 0, 0) → knee (0, -0.46, 0) → 발목(ankle) (0, -0.44, 0).
- 회전은 three.js Euler XYZ(행렬 Rx·Ry·Rz). spine·neck rx 가 + 면 앞으로 숙임, ry 가 + 면 인물 왼쪽으로 돈다.
"""

from __future__ import annotations

import math

SCALE_BASE_M = 1.7
HIPS_Y = 0.9
NECK_Y = 0.6
HEAD_CENTER_Y = 0.13
SHOULDER = (0.22, 0.52)
UPPER_ARM_M = 0.3
FORE_ARM_M = 0.29
HIP_X = 0.1
THIGH_M = 0.46
SHIN_M = 0.44
NEAR_M = 0.25
MAX_WORDS = 60
WALK_MIN_M = 0.3
TURN_MIN_DEG = 20.0

_ZERO = (0.0, 0.0, 0.0)


# ---------------------------------------------------------------- 작은 벡터 계산

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dist(a, b):
    return math.dist(a, b)


def _mat_xyz(r):
    """three.js Euler 'XYZ' 행렬(Rx·Ry·Rz)."""
    x, y, z = (float(v) for v in r)
    a, b = math.cos(x), math.sin(x)
    c, d = math.cos(y), math.sin(y)
    e, f = math.cos(z), math.sin(z)
    return (
        (c * e, -c * f, d),
        (a * f + b * e * d, a * e - b * f * d, -b * c),
        (b * f - a * e * d, b * e + a * f * d, a * c),
    )


def _mm(m, n):
    return tuple(tuple(sum(m[i][k] * n[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _mv(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


_IDENT = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _yaw(deg):
    return _mat_xyz((0.0, math.radians(float(deg)), 0.0))


def _wrap(deg: float) -> float:
    return (float(deg) + 180.0) % 360.0 - 180.0


def _num(value, default=0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return float(default)
    return float(value)


# ---------------------------------------------------------------- 시간 보간(209 렌더 규칙과 같다)

def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp_rig(a: dict, b: dict, t: float) -> dict:
    out: dict = {}
    for joint in set(a) | set(b):
        if joint == "rootY":
            out[joint] = _num(a.get(joint)) + (_num(b.get(joint)) - _num(a.get(joint))) * t
            continue
        va, vb = a.get(joint) or _ZERO, b.get(joint) or _ZERO
        out[joint] = [float(va[i]) + (float(vb[i]) - float(va[i])) * t for i in range(3)]
    return out


def _key_state(actor: dict, key: dict) -> tuple:
    pos = key.get("pos") or actor.get("pos") or [0, 0, 0]
    rig = key.get("rig") if isinstance(key.get("rig"), dict) else (actor.get("rig") or {})
    return ([_num(pos[0]), 0.0, _num(pos[2]) if len(pos) > 2 else 0.0], _num(key.get("facingDeg"), _num(actor.get("facingDeg"))), rig)


def state_at(actor: dict, u: float) -> tuple[list[float], float, dict]:
    """시각 u(0..1)의 (pos, facingDeg, rig). keys 가 있으면 이웃 키 사이 smoothstep, 방향은 가까운 쪽으로."""
    keys = [k for k in actor.get("keys") or [] if isinstance(k, dict)]
    if not keys:
        pos = actor.get("pos") or [0, 0, 0]
        return ([_num(pos[0]), 0.0, _num(pos[2]) if len(pos) > 2 else 0.0], _num(actor.get("facingDeg")), actor.get("rig") or {})
    keys = sorted(keys, key=lambda k: _num(k.get("u")))
    if u <= _num(keys[0].get("u")):
        return _key_state(actor, keys[0])
    if u >= _num(keys[-1].get("u")):
        return _key_state(actor, keys[-1])
    for ka, kb in zip(keys, keys[1:]):
        ua, ub = _num(ka.get("u")), _num(kb.get("u"))
        if ua <= u <= ub:
            t = _smooth((u - ua) / (ub - ua)) if ub > ua else 1.0
            pa, fa, ra = _key_state(actor, ka)
            pb, fb, rb = _key_state(actor, kb)
            pos = [pa[0] + (pb[0] - pa[0]) * t, 0.0, pa[2] + (pb[2] - pa[2]) * t]
            return pos, fa + _wrap(fb - fa) * t, _lerp_rig(ra, rb, t)
    return _key_state(actor, keys[-1])


# ---------------------------------------------------------------- FK

def body_points(pos, facing_deg: float, rig: dict, height_m: float = SCALE_BASE_M) -> dict:
    """세계 좌표 관절 위치. 키: pelvis, chest, shoulderL/R, neck, head, face, chin, cheekL/R, handL/R, kneeL/R, footL/R, fwd, left."""
    rig = rig or {}
    s = (_num(height_m, SCALE_BASE_M) or SCALE_BASE_M) / SCALE_BASE_M
    root = _yaw(facing_deg)
    origin = (_num(pos[0]), 0.0, _num(pos[2]) if len(pos) > 2 else 0.0)

    def world(local):
        return _add(origin, _mv(root, _mul(local, s)))

    def r(joint):
        v = rig.get(joint)
        return _mat_xyz(v) if isinstance(v, (list, tuple)) and len(v) == 3 else _IDENT

    hips = (0.0, HIPS_Y + _num(rig.get("rootY")), 0.0)
    spine = r("spine")
    pts: dict = {"pelvis": world(hips)}
    pts["chest"] = world(_add(hips, _mv(spine, (0.0, 0.32, 0.12))))
    neck_l = _add(hips, _mv(spine, (0.0, NECK_Y, 0.0)))
    neck_m = _mm(spine, r("neck"))
    head_m = _mm(neck_m, r("head"))
    pts["neck"] = world(_add(neck_l, _mv(neck_m, (0.0, -0.02, 0.04))))
    head_c = _add(neck_l, _mv(head_m, (0.0, HEAD_CENTER_Y, 0.0)))
    pts["head"] = world(head_c)
    pts["headTop"] = world(_add(head_c, _mv(head_m, (0.0, 0.13, 0.0))))
    pts["face"] = world(_add(head_c, _mv(head_m, (0.0, 0.0, 0.1))))
    pts["chin"] = world(_add(head_c, _mv(head_m, (0.0, -0.11, 0.06))))
    pts["cheekL"] = world(_add(head_c, _mv(head_m, (0.09, -0.03, 0.06))))
    pts["cheekR"] = world(_add(head_c, _mv(head_m, (-0.09, -0.03, 0.06))))
    for side, sign in (("L", 1.0), ("R", -1.0)):
        sh_l = _add(hips, _mv(spine, (sign * SHOULDER[0], SHOULDER[1], 0.0)))
        sh_m = _mm(spine, r("shoulder" + side))
        el_l = _add(sh_l, _mv(sh_m, (0.0, -UPPER_ARM_M, 0.0)))
        el_m = _mm(sh_m, r("elbow" + side))
        pts["shoulder" + side] = world(sh_l)
        pts["hand" + side] = world(_add(el_l, _mv(el_m, (0.0, -FORE_ARM_M, 0.0))))
        hip_l = _add(hips, (sign * HIP_X, 0.0, 0.0))
        hip_m = r("hip" + side)
        knee_l = _add(hip_l, _mv(hip_m, (0.0, -THIGH_M, 0.0)))
        pts["knee" + side] = world(knee_l)
        pts["foot" + side] = world(_add(knee_l, _mv(_mm(hip_m, r("knee" + side)), (0.0, -SHIN_M, 0.0))))
    pts["fwd"] = _mv(root, (0.0, 0.0, 1.0))
    pts["left"] = _mv(root, (1.0, 0.0, 0.0))
    pts["scale"] = s
    return pts


# ---------------------------------------------------------------- 문구

def _band(y: float, p: dict) -> str:
    s = p["scale"]
    if y < p["pelvis"][1] - 0.05 * s:
        return "below waist"
    if y < p["pelvis"][1] + 0.2 * s:
        return "waist"
    if y < (p["shoulderL"][1] + p["shoulderR"][1]) / 2 - 0.08 * s:
        return "chest"
    if y < p["head"][1] - 0.08 * s:
        return "shoulder"
    if y <= p["headTop"][1] + 0.03 * s:
        return "face"
    return "above head"


def _band_text(band: str) -> str:
    if band == "above head":
        return "above head"
    return "own face height" if band == "face" else f"{band} height"


def _touch_phrases(other: str) -> dict:
    """작업 173·174 Touch 낱말(generate.FACE_TOUCH_*)을 그대로 쓴다. 턱·목 가까운 손 = 부드러운 턱 받침."""
    try:
        from . import generate as gen_mod  # noqa: PLC0415 — generate 가 이 모듈을 부른다(순환 피함)

        chin = gen_mod.FACE_TOUCH_CHIN.format(lift="lifts", name=other)
        cheek = gen_mod.FACE_TOUCH_CHEEK.format(cup="cups", name=other)
    except Exception:  # noqa: BLE001
        chin = f"gently lifts {other}'s chin with his fingertips, thumb resting lightly on the chin near the lower lip"
        cheek = f"softly cups {other}'s cheek"
    return {
        "chin": f"{chin} (a soft, loving chin hold, no grip)",
        "cheek": cheek,
        "head": f"rests lightly on {other}'s head",
        "shoulder": f"rests on {other}'s shoulder",
        "hand": f"holds {other}'s hand",
    }


_OTHER_PARTS = (("chin", ("chin", "neck")), ("cheek", ("cheekL", "cheekR")), ("head", ("head", "headTop", "face")),
                ("shoulder", ("shoulderL", "shoulderR")), ("hand", ("handL", "handR")))
_OWN_PARTS = (("own chin", ("chin",)), ("own cheek", ("cheekL", "cheekR")), ("own face", ("face",)),
              ("own head", ("head", "headTop")), ("own chest", ("chest",)))


def _hand_phrase(side: str, p: dict, others: list[tuple[str, dict]]) -> str:
    label = "left hand" if side == "L" else "right hand"
    hand = p["hand" + side]
    band = _band(hand[1], p)
    # 1) 상대 몸에 닿음(0.25 m 안). 턱·목은 늘 부드러운 턱 받침.
    best = None
    for other, op in others:
        for kind, parts in _OTHER_PARTS:
            d = min(_dist(hand, op[k]) for k in parts)
            if d <= NEAR_M and (best is None or d < best[0]):
                best = (d, other, kind)
    if best is not None:
        return f"{label} {_touch_phrases(best[1])[best[2]]}"
    # 2) 자기 얼굴·머리·가슴 가까이.
    own = None
    for kind, parts in _OWN_PARTS:
        d = min(_dist(hand, p[k]) for k in parts)
        if d <= NEAR_M and (own is None or d < own[0]):
            own = (d, kind)
    raised = band in ("shoulder", "face", "above head")
    if own is not None:
        verb = "raised to" if raised else "at"
        return f"{label} {verb} {_band_text(band)}, close to {own[1]}"
    # 3) 상대 쪽으로 뻗음.
    shoulder = p["shoulder" + side]
    reach = _sub(hand, shoulder)
    flat = math.hypot(reach[0], reach[2])
    if flat >= 0.3 * p["scale"]:
        for other, op in others:
            to = _sub(op["chest"], shoulder)
            norm = math.hypot(to[0], to[2])
            if norm > 0 and (reach[0] * to[0] + reach[2] * to[2]) / (flat * norm) >= math.cos(math.radians(35)):
                return f"{label} reaching forward toward {other} at {_band_text(band)}"
        if (reach[0] * p["fwd"][0] + reach[2] * p["fwd"][2]) / flat >= 0.7:
            return f"{label} reaching forward at {_band_text(band)}"
        return f"{label} stretched out to the side at {_band_text(band)}"
    if raised:
        return f"{label} raised {_band_text(band) if band == 'above head' else 'to ' + _band_text(band)}"
    return f"{label} at {_band_text(band)}"


def _deg(v: float) -> int:
    return int(round(abs(math.degrees(v)) / 5.0) * 5)


def _joint(rig: dict, name: str) -> tuple[float, float, float]:
    v = rig.get(name)
    return tuple(float(x) for x in v) if isinstance(v, (list, tuple)) and len(v) == 3 else _ZERO


def _arm_posed(rig: dict, side: str) -> bool:
    return any(abs(x) > 0.05 for j in ("shoulder", "elbow") for x in _joint(rig, j + side))


def pose_phrases(rig: dict, p: dict, others: list[tuple[str, dict]]) -> list[str]:
    """한 시각의 자세 문구(중요한 순: 손 → 하체 → 몸통 → 머리 → 발)."""
    rig = rig or {}
    out: list[str] = []
    for side in ("R", "L"):
        if _arm_posed(rig, side):
            out.append(_hand_phrase(side, p, others))
    root_y = _num(rig.get("rootY"))
    knees = (p["kneeL"][1], p["kneeR"][1])
    s = p["scale"]
    if max(knees) < 0.15 * s:
        out.append("kneeling on both knees")
    elif min(knees) < 0.15 * s:
        out.append("kneeling on one knee")
    elif root_y <= -0.15:
        bent = any(abs(_joint(rig, k)[0]) > 0.5 for k in ("kneeL", "kneeR"))
        out.append(f"{'knees bent, crouching' if bent else 'body lowered'} (pelvis {abs(root_y):.1f} m lower)")
    sx, sy, sz = _joint(rig, "spine")
    if _deg(sx) >= 10:
        out.append(f"torso leaning {'forward' if sx > 0 else 'back'} about {_deg(sx)}°")
    if _deg(sz) >= 10:
        out.append(f"torso tilted to own {'right' if sz > 0 else 'left'} about {_deg(sz)}°")
    if _deg(sy) >= 15:
        out.append(f"torso twisted {_deg(sy)}° to own {'left' if sy > 0 else 'right'}")
    nx, ny, nz = (a + b for a, b in zip(_joint(rig, "neck"), _joint(rig, "head")))
    head = []
    if _deg(ny) >= 10:
        head.append(f"turned {_deg(ny)}° to own {'left' if ny > 0 else 'right'}")
    if _deg(nx) >= 10:
        head.append(f"tilted {'down' if nx > 0 else 'up'}")
    if _deg(nz) >= 10:
        head.append(f"cocked to own {'right' if nz > 0 else 'left'}")
    if head:
        out.append("head " + ", ".join(head))
    for side, label in (("L", "left"), ("R", "right")):
        if p["foot" + side][1] > 0.15 * s and min(knees) >= 0.15 * s:
            out.append(f"{label} foot lifted")
    return out


def _others_at(others: list[dict], u: float, other_names: dict) -> list[tuple[str, dict]]:
    out = []
    for o in others or []:
        if not isinstance(o, dict):
            continue
        pos, facing, rig = state_at(o, u)
        name = str((other_names or {}).get(str(o.get("id") or "")) or o.get("name") or o.get("id") or "the other")
        out.append((name, body_points(pos, facing, rig, _num(o.get("heightM"), SCALE_BASE_M))))
    return out


def _camera_frame(camera) -> tuple | None:
    if not isinstance(camera, dict):
        return None
    pos, target = camera.get("pos"), camera.get("target")
    if not (isinstance(pos, (list, tuple)) and isinstance(target, (list, tuple)) and len(pos) >= 3 and len(target) >= 3):
        return None
    fx, fz = _num(target[0]) - _num(pos[0]), _num(target[2]) - _num(pos[2])
    n = math.hypot(fx, fz)
    if n < 1e-6:
        return None
    return (_num(pos[0]), _num(pos[2])), (fx / n, fz / n), (-fz / n, fx / n)  # 카메라 자리, 보는 쪽, 화면 오른쪽


def _move_phrase(pa, pb, others_a, cam) -> str:
    dx, dz = pb[0] - pa[0], pb[2] - pa[2]
    dist = math.hypot(dx, dz)
    if dist < WALK_MIN_M:
        return ""
    ux, uz = dx / dist, dz / dist
    rel = ""
    for name, op in others_a:
        ox, oz = op["pelvis"][0] - pa[0], op["pelvis"][2] - pa[2]
        n = math.hypot(ox, oz)
        if n > 1e-6:
            c = (ux * ox + uz * oz) / n
            if c >= 0.7:
                rel = f" toward {name}"
            elif c <= -0.7:
                rel = f" away from {name}"
            if rel:
                break
    cam_bits = []
    if cam is not None:
        _, (fx, fz), (rx, rz) = cam
        along, side = ux * fx + uz * fz, ux * rx + uz * rz
        if along <= -0.5:
            cam_bits.append("toward camera")
        elif along >= 0.5:
            cam_bits.append("away from camera")
        if side >= 0.5:
            cam_bits.append("screen right")
        elif side <= -0.5:
            cam_bits.append("screen left")
    tail = f" ({', '.join(cam_bits)})" if cam_bits else ""
    return f"walks {dist:.1f} m{rel}{tail}"


def _turn_phrase(fa: float, fb: float, pb, others_b, cam) -> str:
    delta = _wrap(fb - fa)
    if abs(delta) < TURN_MIN_DEG:
        return ""
    deg = int(round(abs(delta) / 5.0) * 5)
    fwd = (math.sin(math.radians(fb)), math.cos(math.radians(fb)))
    if cam is not None:
        (cx, cz), _, _ = cam
        tx, tz = cx - pb[0], cz - pb[2]
        n = math.hypot(tx, tz)
        if n > 1e-6 and (fwd[0] * tx + fwd[1] * tz) / n >= math.cos(math.radians(30)):
            return f"turns {deg}° to face camera"
    for name, op in others_b:
        tx, tz = op["pelvis"][0] - pb[0], op["pelvis"][2] - pb[2]
        n = math.hypot(tx, tz)
        if n > 1e-6 and (fwd[0] * tx + fwd[1] * tz) / n >= math.cos(math.radians(30)):
            return f"turns {deg}° to face {name}"
    return f"turns {deg}° to own {'left' if delta > 0 else 'right'}"


def _limit_words(parts: list[str], limit: int = MAX_WORDS) -> str:
    out, words = [], 0
    for part in parts:
        n = len(part.split())
        if out and words + n > limit:
            break
        out.append(part)
        words += n
    text = "; ".join(out)
    if words > limit:
        text = " ".join(text.split()[:limit])
    return text


def describe_actor_motion(actor: dict, *, cut_seconds: float, others: list[dict], name: str, other_names: dict,
                          camera: dict | None = None) -> str:
    """컷 인물의 사용자 자세·움직임 영어 설명(약 60 낱말 안). rig·keys 가 없으면 ""."""
    if not isinstance(actor, dict) or not (actor.get("rig") or actor.get("keys")):
        return ""
    height = _num(actor.get("heightM"), SCALE_BASE_M)
    others = [o for o in others or [] if isinstance(o, dict) and str(o.get("id") or "") != str(actor.get("id") or "")]
    cam = _camera_frame(camera)
    keys = sorted((k for k in actor.get("keys") or [] if isinstance(k, dict)), key=lambda k: _num(k.get("u")))
    seconds = max(0.0, _num(cut_seconds))
    if len(keys) < 2:
        u = _num(keys[0].get("u")) if keys else 1.0
        pos, facing, rig = state_at(actor, u)
        p = body_points(pos, facing, rig, height)
        return _limit_words(pose_phrases(rig, p, _others_at(others, u, other_names)))
    beats: list[str] = []
    pos0, f0, rig0 = state_at(actor, _num(keys[0].get("u")))
    prev_phrases = pose_phrases(rig0, body_points(pos0, f0, rig0, height), _others_at(others, _num(keys[0].get("u")), other_names))
    if prev_phrases:
        beats.append(f"{_num(keys[0].get('u')) * seconds:.1f} s: " + ", ".join(prev_phrases))
    for ka, kb in zip(keys, keys[1:]):
        ua, ub = _num(ka.get("u")), _num(kb.get("u"))
        pa, fa, _ = state_at(actor, ua)
        pb, fb, rb = state_at(actor, ub)
        others_a, others_b = _others_at(others, ua, other_names), _others_at(others, ub, other_names)
        now = pose_phrases(rb, body_points(pb, fb, rb, height), others_b)
        bits = [b for b in (_move_phrase(pa, pb, others_a, cam), _turn_phrase(fa, fb, pb, others_b, cam)) if b]
        bits += [ph for ph in now if ph not in prev_phrases]
        prev_phrases = now
        if bits:
            beats.append(f"{ua * seconds:.1f}–{ub * seconds:.1f} s: " + ", ".join(bits))
    if not beats:
        beats.append("holds still")
    return _limit_words(beats)



# ---------------------------------------------------------------- plan 에서 컷의 사용자 자세 찾기(지시서·영상 프롬프트가 쓴다)

def cut_user_poses(plan: dict, cut_id: str) -> list[tuple[str, str]]:
    """그 컷 timeline 에서 rig·keys 가 있는 인물의 [(이름, 설명)]. 없으면 []. 순서 = timeline actors 순서."""
    cut_id = str(cut_id or "")
    for scene in ((plan or {}).get("previz") or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for cut in scene.get("timeline") or []:
            if not isinstance(cut, dict) or str(cut.get("cutId") or "") != cut_id:
                continue
            meta = {str(a.get("id") or ""): a for a in scene.get("actors") or [] if isinstance(a, dict)}
            names = {aid: str(a.get("name") or aid) for aid, a in meta.items()}
            actors = []
            for ca in cut.get("actors") or []:
                if isinstance(ca, dict):
                    height = (meta.get(str(ca.get("id") or "")) or {}).get("heightM")
                    actors.append(dict(ca, heightM=height) if height is not None and "heightM" not in ca else ca)
            seconds = _num(cut.get("end")) - _num(cut.get("start"))
            out = []
            for ca in actors:
                if not (ca.get("rig") or ca.get("keys")):
                    continue
                aid = str(ca.get("id") or "")
                name = names.get(aid, aid)
                text = describe_actor_motion(ca, cut_seconds=seconds, others=[o for o in actors if o is not ca],
                                             name=name, other_names=names, camera=cut.get("camera"))
                if text:
                    out.append((name, text))
            return out
    return []
