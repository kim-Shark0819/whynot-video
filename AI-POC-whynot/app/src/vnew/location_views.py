"""vnew ③ 공간 — 무대 세트(box set) 컷어웨이 뷰 순수 함수(설계자 2026-10-21, 048).

벽을 4장 따로 그리지 않고, 앞 벽을 떼어 낸 상자를 정면 바깥에서 본 1점 투시 '컷어웨이 뷰' 두 장
(뷰 A=뒤 보기, 뷰 B=앞 보기)으로 공간을 그린다. 이 모듈은 외부 호출(LLM·Seedream·S3) 없이:

- box_view_geometry: 방 치수로 화면 꼭짓점(back/left/right/floor/ceiling 사다리꼴)을 계산한다.
- draw_guide: 그 꼭짓점 위에 회색 면·굵은 모서리선·창문/문/계단·광원 명암을 PIL 로 그려 JPEG 로 준다.
- rectify: 알고 있는 사다리꼴(quad)을 직사각형으로 원근 보정해(벽/바닥 텍스처) JPEG 로 준다.

좌표계: 화면은 (x 오른쪽+, y 아래+). 꼭짓점 순서는 '텍스처 기준' 좌상·우상·우하·좌하로 고정한다.
의존성은 PIL 만. 원근 보정 8계수는 순수 파이썬 가우스 소거로 푼다(numpy 없이).
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

# 면 꼭짓점 순서: 텍스처 기준 (좌상, 우상, 우하, 좌하). rectify 가 이 순서를 그대로 쓴다.
FACE_ORDER = ("back", "left", "right", "floor", "ceiling")


# ================================================================ 1점 투시 투영


def _project(point, cam, focal, size):
    """월드 좌표(x,y,z)를 핀홀 카메라로 화면 좌표(sx, sy)로 투영한다.

    카메라는 앞 열린 면 중앙 바깥(cam=(cx,cy,cz))에서 +z(방 안쪽)를 본다. 화면 중심 (W/2, H/2).
    y 는 위가 +(월드) → 화면은 아래가 + 이므로 뒤집는다.
    """
    width, height = size
    x, y, z = point
    cx, cy, cz = cam
    dz = z - cz
    if dz < 1e-4:
        dz = 1e-4  # 카메라 평면 뒤/위는 방지(앞 열린 면이 가장 가까우므로 보통 안 걸린다).
    sx = width / 2.0 + focal * (x - cx) / dz
    sy = height / 2.0 - focal * (y - cy) / dz
    return (sx, sy)


def box_view_geometry(width_m, depth_m, height_m, view="back", size=(2560, 1440)):
    """방 치수로 컷어웨이 뷰의 면별 화면 꼭짓점을 돌려준다.

    반환: {back:[4pt], left:[4pt], right:[4pt], floor:[4pt], ceiling:[4pt]} — 각 면은 텍스처
    기준 (좌상, 우상, 우하, 좌하) 순서의 화면 좌표. view="back" 은 뒤 벽이 가운데(north), 왼쪽=west,
    오른쪽=east. view="front" 는 같은 방을 반대에서 본 것(south 가 가운데, 왼쪽=east, 오른쪽=west).

    카메라: 앞 열린 면 중앙 바깥, 눈높이≈min(1.6, 0.55*height). 앞 열린 면 사각형이 화면의 약 94%,
    뒤 벽 사각형이 화면 가로의 35~55% 가 되도록 거리·초점을 잡는다(핀홀 1점 투시).
    """
    width = max(0.5, float(width_m or 4.0))
    depth = max(0.5, float(depth_m or 4.0))
    height = max(0.5, float(height_m or 2.7))
    out_w, out_h = int(size[0]), int(size[1])

    eye = min(1.6, 0.55 * height)
    # 월드: x 0..width(왼→오), y 0..height(아래→위), z 0(앞 열린 면)..depth(뒤 벽).
    # 카메라는 앞 열린 면 바깥(z<0)에서 방 가운데를 본다.
    cx = width / 2.0
    cy = eye
    # 거리·초점: 앞 열린 면이 화면의 ~94% 를 채우게. 초점(focal)은 가로/세로 중 꽉 차는 쪽으로.
    # 앞 열린 면(z=0)까지 거리 d 에서 가로 width 가 화면의 fill 배가 되려면 focal = fill*out_w*d/width.
    fill = 0.94
    # 가로·세로 둘 다 넘지 않게, 더 작은 초점을 고른다.
    # 거리 d 는 방 깊이에 비례해 적당히(뒤 벽이 화면의 35~55% 가 되게 아래서 보정).
    dist = max(1.2, 0.9 * max(width, depth))
    cz = -dist
    focal_w = fill * out_w * dist / width
    focal_h = fill * out_h * dist / height
    focal = min(focal_w, focal_h)

    # 뒤 벽 사각형의 화면 가로 폭을 35~55% 로 맞춘다. 뒤 벽(z=depth)에서 가로 width 의 화면 폭:
    #   back_px = focal * width / (depth - cz) = focal*width/(depth+dist).
    # 목표 비율 0.45 중심. 벗어나면 거리(dist)를 조절해 다시 계산(한 번).
    for _ in range(6):
        back_ratio = (focal * width / (depth + dist)) / out_w
        if back_ratio < 0.35:
            dist *= 0.85  # 더 가까이 → 뒤 벽이 커진다.
        elif back_ratio > 0.55:
            dist *= 1.18  # 더 멀리 → 뒤 벽이 작아진다.
        else:
            break
        cz = -dist
        focal = min(fill * out_w * dist / width, fill * out_h * dist / height)

    cam = (cx, cy, cz)

    def proj(x, y, z):
        return _project((x, y, z), cam, focal, (out_w, out_h))

    # 앞 열린 면(z=0)·뒤 벽(z=depth)의 네 모서리(월드).
    # 화면 좌/우 기준: view="back" 은 x=0 이 화면 왼쪽. view="front" 는 좌우가 바뀐다(반대편에서 봄).
    flip = view == "front"

    def wx(x):
        return (width - x) if flip else x

    # 앞 열린 면 코너(z=0): 좌하·좌상·우하·우상.
    front_bl = proj(wx(0), 0, 0)
    front_tl = proj(wx(0), height, 0)
    front_br = proj(wx(width), 0, 0)
    front_tr = proj(wx(width), height, 0)
    # 뒤 벽 코너(z=depth).
    back_bl = proj(wx(0), 0, depth)
    back_tl = proj(wx(0), height, depth)
    back_br = proj(wx(width), 0, depth)
    back_tr = proj(wx(width), height, depth)

    # back(가운데 벽): 텍스처 기준 좌상·우상·우하·좌하.
    back = [back_tl, back_tr, back_br, back_bl]
    # left(화면 왼쪽 사다리꼴): 바깥(앞 열린 면)쪽 가장자리가 텍스처 왼쪽, 뒤 벽과 만나는 모서리가 오른쪽.
    #   방 안에서 그 벽을 마주 봤을 때 그대로가 되도록(west: 오른쪽 끝이 north 모서리).
    left = [front_tl, back_tl, back_bl, front_bl]
    # right(화면 오른쪽 사다리꼴): 뒤 벽과 만나는 모서리가 텍스처 왼쪽, 바깥쪽 가장자리가 오른쪽.
    #   east: 왼쪽 끝이 north 모서리.
    right = [back_tr, front_tr, front_br, back_br]
    # floor(아래 사다리꼴): 텍스처 기준 좌상=뒤벽 왼쪽, 우상=뒤벽 오른쪽, 우하=앞 오른쪽, 좌하=앞 왼쪽.
    floor = [back_bl, back_br, front_br, front_bl]
    # ceiling(위 사다리꼴): 좌상=앞 왼쪽 위, 우상=앞 오른쪽 위, 우하=뒤벽 오른쪽 위, 좌하=뒤벽 왼쪽 위.
    ceiling = [front_tl, front_tr, back_tr, back_tl]

    return {
        "back": back,
        "left": left,
        "right": right,
        "floor": floor,
        "ceiling": ceiling,
        # 디버그·verify 용 부가 정보(앞 열린 면·뒤 벽 꼭짓점·화면 크기).
        "_front": [front_tl, front_tr, front_br, front_bl],
        "_size": (out_w, out_h),
        "_view": view,
    }


# ================================================================ 가이드 그리기


def _poly(points):
    """[(x,y),...] → PIL polygon 용 정수 좌표 리스트."""
    return [(int(round(px)), int(round(py))) for (px, py) in points]


def _lerp_pt(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _quad_point(quad, u, v):
    """사다리꼴(좌상·우상·우하·좌하) 안을 (u,v)∈[0,1]×[0,1] 로 보간한 점. u=가로, v=세로(위→아래)."""
    tl, tr, br, bl = quad
    top = _lerp_pt(tl, tr, u)
    bottom = _lerp_pt(bl, br, u)
    return _lerp_pt(top, bottom, v)


def _sub_quad(quad, u0, u1, v0, v1):
    """면(quad) 안의 (u0..u1, v0..v1) 부분 사다리꼴(좌상·우상·우하·좌하)."""
    return [
        _quad_point(quad, u0, v0),
        _quad_point(quad, u1, v0),
        _quad_point(quad, u1, v1),
        _quad_point(quad, u0, v1),
    ]


_POS_U = {"left": (0.08, 0.34), "center": (0.37, 0.63), "right": (0.66, 0.92)}
_POS_V = {"high": (0.08, 0.34), "mid": (0.36, 0.62), "low": (0.64, 0.9)}


def _opening_uv(position):
    """'left high' 같은 위치 → (u0,u1,v0,v1). 못 읽으면 중앙."""
    text = str(position or "").strip().lower()
    horiz = next((h for h in _POS_U if h in text), "center")
    vert = next((v for v in _POS_V if v in text), "mid")
    u0, u1 = _POS_U[horiz]
    v0, v1 = _POS_V[vert]
    return u0, u1, v0, v1


def draw_guide(geometry, openings, light, size=(2560, 1440)):
    """컷어웨이 뷰 가이드 이미지를 그려 JPEG bytes 로 돌려준다(글자 없음).

    - 면마다 다른 회색 톤(back·left·right·floor·ceiling), 모서리 검은 굵은 선(8px).
    - openings: {"windows":[{wall, position}], "doors":[{wall, position}], "stairs":[{wall}]}
      wall 은 화면 기준 'left'|'center'|'right'|'floor'|'ceiling'(가이드가 그릴 면). 창문=흰 사각형,
      문=어두운 사각형, 계단=바닥에서 올라가는 밝은 띠. 해당 면 사다리꼴 좌표로 투영해 그린다.
    - light: {"wall": 'left'|'center'|'right'} — 그 면을 밝게, 반대쪽에 그라데이션 그림자.
    - geometry 에 '_sky' True(또는 enclosed False)면 ceiling 을 하늘색으로 칠한다.
    """
    out_w, out_h = int(size[0]), int(size[1])
    img = Image.new("RGB", (out_w, out_h), (90, 90, 92))
    draw = ImageDraw.Draw(img)

    sky = bool((geometry or {}).get("_sky"))
    tones = {
        "ceiling": (150, 180, 210) if sky else (120, 120, 124),
        "back": (108, 108, 112),
        "left": (88, 88, 92),
        "right": (96, 96, 100),
        "floor": (70, 70, 72),
    }
    # 면을 뒤→앞 순으로 칠한다(사다리꼴이 겹쳐도 앞 면이 위에 오게).
    for face in ("ceiling", "back", "left", "right", "floor"):
        quad = (geometry or {}).get(face)
        if quad:
            draw.polygon(_poly(quad), fill=tones[face])

    # 광원: 지정한 면을 밝게, 반대쪽 면을 어둡게(단색 톤 보정).
    lit = str((light or {}).get("wall") or "").strip().lower()
    if lit in ("left", "center", "back", "right"):
        bright = "left" if lit in ("left",) else ("right" if lit == "right" else "back")
        dark = "right" if bright == "left" else ("left" if bright == "right" else "right")
        for face, delta in ((bright, 46), (dark, -34)):
            quad = (geometry or {}).get(face)
            if quad:
                tone = tones.get(face, (100, 100, 100))
                shade = tuple(max(0, min(255, c + delta)) for c in tone)
                draw.polygon(_poly(quad), fill=shade)

    # 창문·문·계단을 해당 면 위에 사다리꼴 좌표로 투영해 그린다.
    op = openings or {}
    for win in op.get("windows") or []:
        face = str(win.get("wall") or "").strip().lower()
        quad = (geometry or {}).get(face)
        if not quad:
            continue
        u0, u1, v0, v1 = _opening_uv(win.get("position"))
        draw.polygon(_poly(_sub_quad(quad, u0, u1, v0, v1)), fill=(235, 238, 245))
    for door in op.get("doors") or []:
        face = str(door.get("wall") or "").strip().lower()
        quad = (geometry or {}).get(face)
        if not quad:
            continue
        u0, u1, _, _ = _opening_uv(door.get("position"))
        # 문은 바닥까지 닿게(v0 ~ 0.95).
        draw.polygon(_poly(_sub_quad(quad, u0, u1, 0.3, 0.95)), fill=(34, 34, 38))
    for stair in op.get("stairs") or []:
        face = str(stair.get("wall") or "").strip().lower()
        quad = (geometry or {}).get(face)
        if not quad:
            continue
        # 계단: 바닥에서 올라가는 밝은 띠 몇 줄.
        for i in range(4):
            v0 = 0.55 - i * 0.12
            draw.polygon(_poly(_sub_quad(quad, 0.3, 0.7, v0, v0 + 0.05)), fill=(190, 190, 195))

    # 모서리 검은 굵은 선(면 경계). 각 면 테두리를 그린다.
    line_w = max(8, out_w // 300)
    for face in ("ceiling", "back", "left", "right", "floor"):
        quad = (geometry or {}).get(face)
        if quad:
            draw.line(_poly(quad) + [_poly(quad)[0]], fill=(8, 8, 10), width=line_w)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


# ================================================================ 원근 보정(rectify)


def _solve_linear(matrix, vector):
    """A x = b 를 가우스 소거로 푼다(순수 파이썬, numpy 없이). matrix=8x8, vector=8."""
    n = len(vector)
    # 증강 행렬.
    aug = [list(row) + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        # 부분 피벗(가장 큰 절댓값을 위로).
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("특이 행렬: 사다리꼴 꼭짓점이 올바르지 않습니다.")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= pivot_val
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _perspective_coeffs(dst_quad, src_quad):
    """출력(dst) 사각형의 네 꼭짓점을 원본(src) 사다리꼴 네 꼭짓점으로 보내는 8계수를 푼다.

    PIL Image.transform(PERSPECTIVE) 는 '출력 픽셀 → 원본 픽셀' 매핑 계수를 받는다. 즉 dst=출력
    좌표, src=원본(생성 그림) 좌표. 두 사각형 모두 (좌상·우상·우하·좌하) 순서.
    """
    matrix = []
    vector = []
    for (dx, dy), (sx, sy) in zip(dst_quad, src_quad):
        matrix.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        vector.append(sx)
        matrix.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        vector.append(sy)
    return _solve_linear(matrix, vector)


def rectify(image_bytes, quad, out_size):
    """원본 그림(image_bytes)에서 사다리꼴(quad)을 떼어 직사각형 텍스처로 원근 보정해 JPEG 로 준다.

    quad: 원본 그림 픽셀 좌표의 (좌상·우상·우하·좌하). out_size=(W,H). 8계수는 순수 파이썬으로 푼다.
    """
    out_w, out_h = int(out_size[0]), int(out_size[1])
    with Image.open(io.BytesIO(image_bytes)) as src:
        src = src.convert("RGB")
        # 출력 사각형(좌상·우상·우하·좌하)을 원본 quad 로 보내는 계수.
        dst_rect = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
        coeffs = _perspective_coeffs(dst_rect, [(float(x), float(y)) for (x, y) in quad])
        result = src.transform((out_w, out_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    buffer = io.BytesIO()
    result.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# ================================================================ 면 → 벽 매핑 · 유효성

# 뷰 A(뒤 보기)에서 가이드 면 → 렌더러 벽 키. back=north(가운데), left=west, right=east, floor=floor.
# 뷰 B(앞 보기)에서는 가운데 면(back)이 south 다(앞 벽). 좌우는 같은 east/west 를 반대편에서 본 것이라
# 텍스처로는 쓰지 않는다(뷰 B 에서는 south 만 떼어 쓴다 — 설계자 048).
VIEW_FACE_TO_WALL = {
    "back": {"back": "north", "left": "west", "right": "east"},
    "front": {"back": "south"},
}


def face_to_wall(view, face):
    """컷어웨이 뷰의 가이드 면(face)이 렌더러의 어느 벽(north/east/south/west)에 해당하는지.

    view="back": back→north, left→west, right→east. view="front": back→south. 없으면 "".
    """
    return VIEW_FACE_TO_WALL.get(str(view or "back"), {}).get(str(face or ""), "")


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def is_sane_quad(quad, size):
    """꼭짓점 4개가 볼록 사각형이고 화면 안(여유 밖 벗어나지 않음)이면 True(verify 에서 쓴다).

    quad=[(x,y)×4] 정규화(0..1) 또는 픽셀 좌표 모두 받되, size 로 픽셀 범위를 본다. 볼록성은 네 모서리
    외적 부호가 모두 같은지로 판정한다. 하나라도 어긋나면(자기교차·화면 밖) False → 가이드 좌표로 복귀.
    """
    if not quad or len(quad) != 4:
        return False
    out_w, out_h = float(size[0]), float(size[1])
    pts = []
    for p in quad:
        if not p or len(p) < 2:
            return False
        x, y = float(p[0]), float(p[1])
        # 정규화 좌표면 픽셀로 올린다(0..1 범위면).
        if -0.01 <= x <= 1.01 and -0.01 <= y <= 1.01 and max(abs(x), abs(y)) <= 1.01:
            x, y = x * out_w, y * out_h
        pts.append((x, y))
    margin_x, margin_y = out_w * 0.08, out_h * 0.08
    for x, y in pts:
        if x < -margin_x or x > out_w + margin_x or y < -margin_y or y > out_h + margin_y:
            return False
    signs = []
    for i in range(4):
        o = pts[i]
        a = pts[(i + 1) % 4]
        b = pts[(i + 2) % 4]
        c = _cross(o, a, b)
        if abs(c) < 1e-6:
            return False  # 세 점 일직선 → 넓이 없는 사각형.
        signs.append(c > 0)
    return all(signs) or not any(signs)
