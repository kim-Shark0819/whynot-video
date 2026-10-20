"""vnew 이미지 보정 — 참고 이미지 비율 맞춤(설계자 2026-10-21, 지시서 038).

프리비즈 기반 ⑤ 영상 생성에서 캐릭터 정면·후면·원작 웹툰 컷을 fal kling
video-to-video/edit 에 참고 이미지로 넣는다. 그 모델은 참고 이미지 **비율(가로/세로)이
0.4~2.5** 범위를 벗어나면 거절한다(Claude 실측 2026-10-21: 세로로 긴 웹툰 컷·512x1440 후면
크롭은 거절). 범위를 벗어나는 이미지는 흰 여백을 더해 비율을 범위 안으로 맞춘다.

순수 함수(네트워크·S3 없음). Pillow(PIL)만 쓴다 — characters._square_pad 와 같은 방식
(Image.open → RGB → 흰 캔버스에 가운데 정렬 → JPEG q92). generate.py 가 참고 이미지마다
이 함수를 통과시켜 payload 에 넣는다. check 가 단위로도 부른다.
"""

from __future__ import annotations

import io

from PIL import Image

# fal kling video-to-video/edit 참고 이미지 비율(가로/세로) 허용 범위(실측 2026-10-21).
MIN_RATIO = 0.4
MAX_RATIO = 2.5
# JPEG 품질(characters._square_pad 와 같게).
JPEG_QUALITY = 92


def aspect_ratio(width: int, height: int) -> float:
    """가로/세로 비율. 세로가 0 이면 0(이상치)."""
    if height <= 0:
        return 0.0
    return float(width) / float(height)


def target_size(width: int, height: int, *, min_ratio: float = MIN_RATIO, max_ratio: float = MAX_RATIO) -> tuple[int, int]:
    """원본(width×height)을 비율 [min_ratio, max_ratio] 안으로 넣는 흰 캔버스 크기(w,h).

    - 비율이 이미 범위 안이면 원본 크기 그대로(여백 없음).
    - 너무 세로로 길면(ratio < min_ratio) 가로를 늘려(세로 유지) ratio = min_ratio 로.
    - 너무 가로로 길면(ratio > max_ratio) 세로를 늘려(가로 유지) ratio = max_ratio 로.
    원본보다 작아지지 않게(캔버스는 원본을 담아야 한다) 올림한다.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    ratio = aspect_ratio(width, height)
    if ratio <= 0:
        return width, height
    if ratio < min_ratio:
        # 세로가 너무 길다 → 가로를 넓힌다(세로 유지).
        new_w = max(width, int(round(height * min_ratio)))
        return new_w, height
    if ratio > max_ratio:
        # 가로가 너무 길다 → 세로를 넓힌다(가로 유지).
        new_h = max(height, int(round(width / max_ratio)))
        return width, new_h
    return width, height


def fit_ratio(jpeg: bytes, *, min_ratio: float = MIN_RATIO, max_ratio: float = MAX_RATIO) -> bytes:
    """참고 이미지 비율을 [min_ratio, max_ratio] 안으로 맞춘다(흰 여백, 가운데 정렬).

    비율이 이미 범위 안이면 **원본 바이트를 그대로** 돌려준다(불필요한 재인코딩 방지).
    범위를 벗어나면 흰 캔버스(target_size) 가운데에 얹어 JPEG 로 돌려준다.
    열지 못하면 원본을 그대로 돌려준다(막지 않는다).
    """
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            image = image.convert("RGB")
            width, height = image.width, image.height
            ratio = aspect_ratio(width, height)
            if ratio <= 0 or (min_ratio <= ratio <= max_ratio):
                return jpeg
            canvas_w, canvas_h = target_size(width, height, min_ratio=min_ratio, max_ratio=max_ratio)
            canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
            offset = ((canvas_w - width) // 2, (canvas_h - height) // 2)
            canvas.paste(image, offset)
            buffer = io.BytesIO()
            canvas.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 — Pillow 예외 종류가 다양하다. 못 열면 원본 그대로.
        return jpeg
