"""vnew 프리비즈 — 투영 검산·수치 보정(설계자 2026-10-21, 지시서 045).

Astra 가 쓴 카메라(pos·target·fov)가 웹툰 패널 샷(screenX·screenY·heightFrac)과 얼마나
맞는지 **렌더 없이** 숫자로 검산한다. 핀홀 카메라로 각 인물의 머리·발을 화면 좌표(0..1)로
투영해 패널 분석 목표와의 오차를 구하고, 오차가 크면 카메라 위치를 작은 격자로 탐색해(결정적)
오차가 가장 작은 자리로 바꾼다.

좌표계(previz_schema 와 같다): x 오른쪽, y 위, z 카메라 쪽(+). north 벽은 z = -depth/2.
화면 좌표: screenX 0=왼쪽·1=오른쪽, screenY 0=위쪽(머리 위)·1=아래쪽. heightFrac = 인물 키 /
화면 높이. 종횡비 16:9(가로/세로), fov 는 세로 화각(도).

이 모듈은 순수 함수만 둔다(네트워크·S3·LLM 없음). previz.py 가 Astra 결과를 이 함수에
통과시켜 컷별 오차를 재고, 임계를 넘으면 Astra 재요청(그 컷만)이나 grid_refine_camera 로 보정한다.
check 가 단위로도 부른다.
"""

from __future__ import annotations

import math

# 16:9(가로/세로). 세로 fov 를 기준으로 가로 fov 를 넓힌다.
ASPECT = 16.0 / 9.0

# 오차 임계(설계자). 위치(screenX·screenY) 합이 이 값을 넘거나, 크기(heightFrac) 오차가
# 이 값을 넘으면 그 컷은 "다르다"로 보고 Astra 재요청·격자 보정 대상이 된다.
POS_THRESHOLD = 0.12
SIZE_THRESHOLD = 0.15

# 격자 탐색: 각 축(카메라→target 거리·좌우·높이)을 ±GRID_SPAN_M 범위에서 GRID_STEPS 단계로.
GRID_SPAN_M = 1.0
GRID_STEPS = 7


# ================================================================ 벡터 도우미(순수)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


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


def _num(value, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ================================================================ 카메라 기저


def _camera_basis(pos, target):
    """카메라의 정규직교 기저 (right, up, forward). forward 는 pos→target 방향(바라보는 쪽)."""
    forward = _norm(_sub(target, pos))
    if forward == (0.0, 0.0, 0.0):
        forward = (0.0, 0.0, -1.0)
    world_up = (0.0, 1.0, 0.0)
    right = _cross(forward, world_up)
    if _dot(right, right) < 1e-9:
        # forward 가 수직(위·아래)일 때는 world_up 을 z 축으로 바꿔 right 를 구한다.
        right = _cross(forward, (0.0, 0.0, 1.0))
    right = _norm(right)
    up = _norm(_cross(right, forward))
    return right, up, forward


# ================================================================ 한 점 투영


def project_point(camera: dict, world: tuple) -> dict | None:
    """세계 좌표 한 점을 화면 좌표(screenX·screenY 0..1)로 투영한다.

    카메라 뒤(또는 평면 위)면 None. fov 는 세로 화각(도), 종횡비 16:9.
    screenX 0=왼쪽·1=오른쪽, screenY 0=위(머리 쪽)·1=아래.
    """
    pos = _vec3(camera.get("pos"))
    target = _vec3(camera.get("target"))
    fov = _num(camera.get("fov"), 50.0)
    right, up, forward = _camera_basis(pos, target)
    rel = _sub(world, pos)
    depth = _dot(rel, forward)  # 카메라 앞쪽(+forward) 거리
    if depth <= 1e-4:
        return None
    x_cam = _dot(rel, right)
    y_cam = _dot(rel, up)
    half_v = math.tan(math.radians(fov) / 2.0)
    half_h = half_v * ASPECT
    if half_v < 1e-9 or half_h < 1e-9:
        return None
    # 정규화 장치 좌표(-1..1), y 위가 +.
    ndc_x = (x_cam / depth) / half_h
    ndc_y = (y_cam / depth) / half_v
    screen_x = 0.5 + ndc_x * 0.5
    screen_y = 0.5 - ndc_y * 0.5  # y 위 → screenY 0(위)
    return {"screenX": screen_x, "screenY": screen_y, "depth": depth}


# ================================================================ 인물 머리·발 투영


def project_actor(camera: dict, foot_pos: tuple, height_m: float) -> dict | None:
    """인물(발 위치 foot_pos=[x,0,z], 키 height_m)을 화면에 투영한다.

    반환 {screenX, headY, footY, heightFrac} 또는 None(카메라 뒤·범위 밖).
    - screenX: 머리·발의 평균 x(보통 같다).
    - headY: 머리 꼭대기(y=height_m)의 screenY. 패널 분석 subjects.screenY(머리 위치)와 비교.
    - footY: 발(y=0)의 screenY.
    - heightFrac: |footY − headY| = 화면 높이 대비 인물 키 비율.
    """
    foot = _vec3(foot_pos)
    head_world = (foot[0], foot[1] + float(height_m), foot[2])
    foot_world = (foot[0], foot[1], foot[2])
    head = project_point(camera, head_world)
    foot_sp = project_point(camera, foot_world)
    if head is None or foot_sp is None:
        return None
    screen_x = (head["screenX"] + foot_sp["screenX"]) / 2.0
    height_frac = abs(foot_sp["screenY"] - head["screenY"])
    return {
        "screenX": screen_x,
        "headY": head["screenY"],
        "footY": foot_sp["screenY"],
        "heightFrac": height_frac,
    }


# ================================================================ 컷 오차


def actor_error(projected: dict | None, target: dict) -> dict:
    """한 인물의 투영값 vs 목표(screenX·screenY·heightFrac) 오차.

    반환 {posErr, sizeErr, err, visible}. projected 가 None(화면 밖)이면 큰 벌점.
    posErr = |ΔscreenX| + |ΔscreenY(머리)|, sizeErr = |ΔheightFrac|, err = posErr + sizeErr.
    """
    tgt_x = _num(target.get("screenX"), 0.5)
    tgt_y = _num(target.get("screenY"), 0.5)
    tgt_h = _num(target.get("heightFrac"), 0.5)
    if projected is None:
        # 화면에 안 보이면(카메라 뒤·범위 밖) 큰 벌점 — 재요청·보정을 유도.
        return {"posErr": 2.0, "sizeErr": 1.0, "err": 3.0, "visible": False}
    pos_err = abs(projected["screenX"] - tgt_x) + abs(projected["headY"] - tgt_y)
    size_err = abs(projected["heightFrac"] - tgt_h)
    return {"posErr": pos_err, "sizeErr": size_err, "err": pos_err + size_err, "visible": True}


def cut_error(camera: dict, subjects: list[dict]) -> dict:
    """한 컷의 종합 오차. subjects = [{foot:[x,0,z], heightM, target:{screenX,screenY,heightFrac}}].

    반환 {posErr, sizeErr, err, perSubject:[...]}. 여러 인물이면 평균.
    인물이 없으면(패널에 인물 없음) 오차 0.
    """
    per: list[dict] = []
    for subj in subjects or []:
        projected = project_actor(camera, subj.get("foot") or [0, 0, 0], _num(subj.get("heightM"), 1.7))
        per.append(actor_error(projected, subj.get("target") or {}))
    if not per:
        return {"posErr": 0.0, "sizeErr": 0.0, "err": 0.0, "perSubject": []}
    pos_err = sum(p["posErr"] for p in per) / len(per)
    size_err = sum(p["sizeErr"] for p in per) / len(per)
    return {"posErr": pos_err, "sizeErr": size_err, "err": pos_err + size_err, "perSubject": per}


def exceeds_threshold(err: dict, *, pos_threshold: float = POS_THRESHOLD, size_threshold: float = SIZE_THRESHOLD) -> bool:
    """컷 오차가 임계를 넘으면 True(위치 합 또는 크기 중 하나라도)."""
    return bool(err.get("posErr", 0.0) > pos_threshold or err.get("sizeErr", 0.0) > size_threshold)


# ================================================================ 격자 보정(결정적)


def _frange(center: float, span: float, steps: int) -> list[float]:
    """center ±span 을 steps 단계로. steps 가 홀수면 center 를 반드시 포함한다."""
    steps = max(1, int(steps))
    if steps == 1:
        return [center]
    out = []
    for i in range(steps):
        t = i / (steps - 1)  # 0..1
        out.append(center - span + t * (2.0 * span))
    return out


def grid_refine_camera(
    camera: dict,
    subjects: list[dict],
    *,
    span_m: float = GRID_SPAN_M,
    steps: int = GRID_STEPS,
    bounds: dict | None = None,
) -> dict:
    """카메라를 target 방향 거리·좌우·높이로 작은 격자 탐색해 오차가 가장 작은 pos 로 바꾼다.

    결정적(determistic): 같은 입력이면 항상 같은 결과. target·fov 는 그대로 두고 pos 만 움직인다.
    축은 카메라 기저(right·up·forward). forward 축(거리)은 target 에 가까워지지 않게 뒤로만
    (현재 거리 이내) 조금 움직일 수 있게 하되, 격자는 ±span 대칭으로 평가하고 유효한 pos 만 본다.
    bounds = {xmin,xmax,zmin,zmax,ymin,ymax} 가 있으면 그 안으로 제한한다(방 안).
    반환: 새 camera(얕은 복사, pos 만 바뀜) — err 가 줄지 않으면 원본 pos 그대로.
    """
    pos = _vec3(camera.get("pos"))
    target = _vec3(camera.get("target"))
    right, up, forward = _camera_basis(pos, target)

    base_err = cut_error(camera, subjects)["err"]
    best_pos = pos
    best_err = base_err

    d_values = _frange(0.0, span_m, steps)  # forward(거리) 오프셋
    s_values = _frange(0.0, span_m, steps)  # right(좌우) 오프셋
    h_values = _frange(0.0, span_m, steps)  # up(높이) 오프셋
    for d in d_values:
        for s in s_values:
            for h in h_values:
                cand = _add(pos, _add(_scale(forward, d), _add(_scale(right, s), _scale(up, h))))
                cand = _clamp_bounds(cand, bounds)
                trial = dict(camera)
                trial["pos"] = [cand[0], cand[1], cand[2]]
                err = cut_error(trial, subjects)["err"]
                # 결정적 tie-break: 오차가 더 작을 때만 갱신(같으면 먼저 찾은 것 유지).
                if err < best_err - 1e-9:
                    best_err = err
                    best_pos = cand
    out = dict(camera)
    out["pos"] = [round(best_pos[0], 3), round(best_pos[1], 3), round(best_pos[2], 3)]
    return out


def _clamp_bounds(pos: tuple, bounds: dict | None) -> tuple:
    if not bounds:
        return pos
    x = min(max(pos[0], _num(bounds.get("xmin"), -1e9)), _num(bounds.get("xmax"), 1e9))
    y = min(max(pos[1], _num(bounds.get("ymin"), -1e9)), _num(bounds.get("ymax"), 1e9))
    z = min(max(pos[2], _num(bounds.get("zmin"), -1e9)), _num(bounds.get("zmax"), 1e9))
    return (x, y, z)



# ================================================================ 보이는 부분 기준 일치도(설계자 2026-10-21)
#
# 지시서 049. 옛 project_actor 는 "전신 키"를 화면에 투영해 heightFrac 를 쟀다. 하지만 패널의
# 샷은 보통 인물을 자른다(클로즈업은 가슴 위, ECU 는 얼굴만). 그래서 "전신 키"로 비교하면
# 클로즈업은 절대 맞지 않는다(10/21 실험 버그). 여기서는 **보이는 부분**(머리 위 ~ 화면 아래가
# 자르는 몸 지점)만 투영해 일치도를 잰다.
#
# body_y 는 experiment solver.py 의 같은 이름 함수와 같은 규칙이다(서 있음/앉음 sit). 마네킹 키를
# 비율로 나눠 각 몸 지점의 높이(m)를 돌려준다. 앉으면 상체는 엉덩이(SEAT_Y) 위로 길이를 유지한다.

# 몸 지점의 키 대비 비율(서 있을 때). solver.py BODY 와 같다.
BODY_FRACTION = {
    "top": 1.0,
    "eye": 0.94,
    "chin": 0.87,
    "shoulder": 0.82,
    "chest": 0.72,
    "waist": 0.60,
    "belly": 0.55,
    "hips": 0.52,
    "knee": 0.28,
    "foot": 0.0,
}
# 앉은 자세의 엉덩이 높이(m). solver.py SEAT_Y 와 같다.
SEAT_Y = 0.45


def body_y(point: str, height_m: float, pose: str, hip_y: float | None = None) -> float:
    """몸 지점(top·eye·chin·shoulder·chest·waist·knee·foot 등)의 세계 y(높이 m).

    pose 가 sit/seated/sitting 이면 상체는 엉덩이(SEAT_Y+0.05) 위로 길이를 유지하고, 하체는
    바닥~엉덩이로 눌린다(solver.py body_y 와 같은 규칙). 모르는 지점은 chest 로 본다.
    hip_y(084 R1.6)를 주면 앉은 엉덩이 높이로 그 값을 쓴다(앉을 소품 윗면, previz_collision).
    """
    frac = BODY_FRACTION.get(point, BODY_FRACTION["chest"])
    height = float(height_m or 1.7)
    if str(pose or "").lower() in ("sit", "seated", "sitting"):
        hip = float(hip_y) if hip_y is not None else SEAT_Y + 0.05
        if frac >= 0.5:  # 상체는 엉덩이 위로 제 길이를 유지한다.
            return hip + (frac - 0.5) * height
        return frac / 0.5 * hip
    return frac * height


def match_visible(
    camera: dict,
    foot: tuple,
    height_m: float,
    pose: str,
    top_y: float,
    subject_x: float,
    bottom_key: str,
    hip_y: float | None = None,
) -> float:
    """보이는 부분 기준 일치도(0..1). 전신 키가 아니라 "머리 위~자르는 지점"을 화면에 맞춘다.

    - 머리 위(body_y top) 투영 y 가 목표 top_y 와 얼마나 가까운지.
    - 화면 아래 몸 지점(bottom_key, 예: chest) 투영 y 가 화면 아래(1.0)에 얼마나 가까운지.
    - 머리 x 투영이 목표 subject_x 와 얼마나 가까운지.
    세 오차 중 가장 큰 것(worst)을 보고 max(0, 1 - worst/0.25) 로 환산한다(0.25 벗어나면 0).
    카메라 뒤로 가려 투영이 안 되면 0. hip_y 는 body_y 와 같다(앉은 엉덩이 높이, 없으면 지금 값).
    """
    foot = _vec3(foot)
    height = float(height_m or 1.7)
    top_world = (foot[0], body_y("top", height, pose, hip_y), foot[2])
    bottom_world = (foot[0], body_y(bottom_key or "chest", height, pose, hip_y), foot[2])
    top_sp = project_point(camera, top_world)
    bottom_sp = project_point(camera, bottom_world)
    if top_sp is None or bottom_sp is None:
        return 0.0
    err_top_y = abs(top_sp["screenY"] - float(top_y))
    err_bottom_y = abs(bottom_sp["screenY"] - 1.0)
    err_x = abs(top_sp["screenX"] - float(subject_x))
    worst = max(err_top_y, err_bottom_y, err_x)
    return max(0.0, 1.0 - worst / 0.25)
