"""vnew 영상만들기_신규 — ④ 영상 생성(설계자 2026-10-20).

모델링된 캐릭터 + 결정된 콘티의 컷 + 대본을 참고해 **하나의 영상이 이어지도록** 만든다.
모델 최대 길이에 닿으면 자동으로 끊고(③ 콘티 segments), 다음 구간을 만들 때 배경·캐릭터·상황·
위치가 이어지게 한다. 결과는 **실사**와 **웹툰 그림체** 두 개, 사용자가 하나를 골라 ⑤ 사운드로.

연속성 설계(Claude 결정 2026-10-20):
- Seedance 는 참고 이미지와 시작 이미지를 같이 못 쓴다(ark.py). 그래서 **모든 구간을 첫 프레임
  (이미지→영상) 방식**으로 만든다.
- 1구간 첫 프레임 = "실사 시작 장면": Seedream 5.0 Pro 로 그린다(그 구간 첫 컷 웹툰 패널 +
  등장 캐릭터 실사 시트·정면 참고).
- 2구간부터 첫 프레임 = 앞 구간 실사 영상의 마지막 프레임(stitcher.last_frame_jpeg).
- 웹툰 버전: 실사 구간이 끝날 때마다 016 처럼 변환(stitcher.prepare_for_anime + fal.submit_edit).
- 병합: 실사·웹툰 각각 stitcher.concat 으로 final-real.mp4 · final-webtoon.mp4.

재사용(수정 금지): vpoc.ark/fal/video(제출·조회·취소) · vpoc.seedream(시작 장면) · vpoc.store(S3) ·
vpoc.ledger(원장) · vpoc.settings(shot_usd·anime_usd) · vnew.characters(실사/웹툰 시트).

외부 호출은 아래 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _seedream · _submit_video · _poll_video ·
_cancel_video · _submit_anime · _last_frame · _prepare_anime · _concat · _fetch_bytes.
STORE 는 vpoc.store.STORE 를 쓴다(검사가 모듈 변수로 바꿔 끼운다).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import ark as vpoc_ark
from ..vpoc import fal as vpoc_fal
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import seedream as vpoc_seedream
from ..vpoc import settings as vpoc_settings
from ..vpoc import video as vpoc_video
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE, video_output_prefix

from . import characters
from . import imaging
from . import plan as plan_mod
from . import previz_schema
from . import settings as vnew_settings

log = logging.getLogger("vpoc")

# ================================================================ 상수

# preparing(선점) 이 이 시간을 넘게 그대로면 queued 로 되돌린다(016-fix2 와 같은 15분).
PREPARE_STALE_S = 900
# gen 전체가 이 시간을 넘게 running 이면 멈춘 것으로 본다(파드 재시작 대비, 30분).
GEN_STALL_S = 1800
# 웹툰 변환 참고 이미지 최대 장수(그 구간 웹툰 패널 + 캐릭터 웹툰 시트).
MAX_ANIME_REFS = 4
# 구간 영상 프롬프트 목표 상한(설계자 2026-10-20). 모델 maxLength 가 더 작으면 그 안으로.
SEGMENT_PROMPT_TARGET = 1800
# 시작 장면 Seedream 단가 설정 키(설계자: VNEW_SEEDREAM_USD 재사용).
SEEDREAM_USD = vnew_settings.SEEDREAM_USD

# 상태 이름.
_VARIANT_STATES = ("waiting", "queued", "preparing", "submitted", "completed", "failed", "cancelled")


# ================================================================ 키 규칙
#
# 출력 접두사(영상)는 VIDEO_BUCKET 아래 users/{owner}/video/{pid}/vnew/gen/{genId}/{segId}-{attempt}/.
# 메인 버킷에는 시작 장면 이미지·마지막 프레임·최종 병합본을 둔다.


def _gen_root(owner: str, pid: str, gen_id: str) -> str:
    return f"users/{owner}/video/{pid}/vnew/gen/{gen_id}"


def opening_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/opening.jpg"


def first_frame_key(owner: str, pid: str, gen_id: str, seg_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/{seg_id}-firstframe.jpg"


def final_real_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/final-real.mp4"


def final_webtoon_key(owner: str, pid: str, gen_id: str) -> str:
    return f"{_gen_root(owner, pid, gen_id)}/final-webtoon.mp4"


def _seg_output_prefix(owner: str, pid: str, gen_id: str, seg_id: str, attempt: int) -> str:
    # 영상 제출 출력 접두사. 재사용 함수들(ark/fal/video)의 규칙과 같게 video_output_prefix 를 쓴다.
    return video_output_prefix(owner, pid, f"vnew-{gen_id}", seg_id, attempt, gen_id)


def _gen_id() -> str:
    return "g" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 함수들을 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _seedream(prompt: str, references: list[bytes], *, size: str) -> bytes:
    """Seedream 5.0 Pro 로 시작 장면 1장."""
    return vpoc_seedream.generate_safe(
        prompt,
        references,
        model=vnew_settings.SEEDREAM_MODEL,
        where="vnew.generate.seedream",
        size=size,
    )


def _submit_video(**kwargs) -> dict:
    """영상 제출(ark/fal/video 로 분기). model 로 공급자를 가른다 — ark.submit 이 fal 로 넘긴다.

    Luma 는 ark 가 처리하지 않으므로 video.submit 을 쓴다(keyframe_jpeg_b64 로 첫 프레임).
    """
    model = str(kwargs.get("model") or "")
    if model == vpoc_ark.DEFAULT_MODEL or not vpoc_ark.is_ark(model):
        # Luma Ray2(Bedrock async). ark 가 모르는 모델이면 Luma 로 본다.
        return vpoc_video.submit(
            owner=kwargs["owner"],
            project_id=kwargs["project_id"],
            plan_id=kwargs["plan_id"],
            shot_id=kwargs["shot_id"],
            attempt=kwargs["attempt"],
            prompt=kwargs["prompt"],
            keyframe_jpeg_b64=kwargs.get("keyframe_jpeg_b64", ""),
            gen_id=kwargs.get("gen_id", ""),
            resolution=kwargs.get("resolution", ""),
        )
    return vpoc_ark.submit(**kwargs)


def _poll_video(invocation_arn: str, output_prefix: str) -> dict:
    """영상 상태 조회(ark/fal/video 로 분기). ark.poll 과 같은 반환 모양으로 맞춘다."""
    if vpoc_ark.is_ark_job(invocation_arn):
        return vpoc_ark.poll(invocation_arn, output_prefix)
    # Luma(Bedrock async). video.poll 은 status·failureMessage 를 준다. 완료면 mp4 를 찾아 outputKey.
    result = vpoc_video.poll(invocation_arn)
    status = str(result.get("status") or "")
    if status == "Completed":
        item = vpoc_video.find_output_mp4(output_prefix, invocation_arn)
        if not item:
            return {"status": "Failed", "failureMessage": "완료됐지만 영상 파일을 찾지 못했습니다."}
        return {"status": "Completed", "failureMessage": "", "outputKey": item["key"]}
    return {"status": status or "InProgress", "failureMessage": result.get("failureMessage") or ""}


def _cancel_video(invocation_arn: str) -> str:
    """진행 중 영상 취소(014 규칙). ark/fal 은 cancel, Luma 는 취소 API 없음 → unsupported.

    반환: "ok"(성공) · "rejected"(공급자 거절) · "unsupported"(Luma).
    """
    if not invocation_arn:
        return "unsupported"
    if not vpoc_ark.is_ark_job(invocation_arn):
        # Luma(Bedrock async)는 취소 API 가 없다.
        return "unsupported"
    try:
        vpoc_ark.cancel(invocation_arn)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        log.info("vnew.gen.cancel_rejected arn=%s err=%s", invocation_arn, str(exc)[:200])
        return "rejected"


def _submit_anime(*, video_mp4: bytes, prompt: str, output_prefix: str, reference_jpegs_b64: list[str]) -> dict:
    """실사 구간 → 웹툰(애니) 변환 제출(fal.submit_edit)."""
    return vpoc_fal.submit_edit(
        endpoint=vpoc_settings.VPOC_ANIME_MODEL,
        video_mp4=video_mp4,
        prompt=prompt,
        output_prefix=output_prefix,
        reference_jpegs_b64=reference_jpegs_b64,
        keep_audio=True,
    )


def _last_frame(mp4: bytes) -> bytes:
    """실사 구간 영상의 마지막 프레임 JPEG(다음 구간 첫 프레임)."""
    with tempfile.TemporaryDirectory(prefix="vnew-lastframe-") as workdir:
        path = os.path.join(workdir, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(mp4)
        return stitcher.last_frame_jpeg(path)


def _prepare_anime(mp4: bytes) -> dict:
    """실사 mp4 를 fal video-to-video 가 받는 형태(3~15초·높이 ≥720)로 맞춘다.

    반환: {"bytes": 준비된 mp4 bytes, "seconds": 초}. 3초 미만이면 stitcher.AnimeTooShort.
    """
    with tempfile.TemporaryDirectory(prefix="vnew-anime-") as workdir:
        src = os.path.join(workdir, "src.mp4")
        with open(src, "wb") as handle:
            handle.write(mp4)
        out = os.path.join(workdir, "prepared.mp4")
        info = stitcher.prepare_for_anime(src, out, workdir)
        with open(info["path"], "rb") as handle:
            return {"bytes": handle.read(), "seconds": float(info.get("seconds") or 0)}


def _concat(clips: list[bytes]) -> bytes:
    """여러 mp4 바이트를 순서대로 이어 붙인다(stitcher.concat)."""
    with tempfile.TemporaryDirectory(prefix="vnew-concat-") as workdir:
        paths = []
        for index, data in enumerate(clips):
            path = os.path.join(workdir, f"clip-{index:03d}.mp4")
            with open(path, "wb") as handle:
                handle.write(data)
            paths.append(path)
        out = os.path.join(workdir, "final.mp4")
        stitcher.concat(paths, out)
        with open(out, "rb") as handle:
            return handle.read()


def _fetch_bytes(key: str, *, bucket: str = "") -> bytes:
    """우리 버킷의 파일 바이트(완료된 구간 영상 등)."""
    return STORE.get_bytes(key, bucket=bucket)


def _trim_clip(mp4: bytes, seconds: float) -> bytes:
    """mp4 를 앞에서 seconds 만큼 잘라 돌려준다(설계자 2026-10-22, ⑤ — 채운 컷 원복).

    3초로 채워 제출한 컷의 결과를 합칠 때 원래 길이로 자른다. stitcher.trim(프레임 정확) 재사용.
    """
    with tempfile.TemporaryDirectory(prefix="vnew-trim-") as workdir:
        src = os.path.join(workdir, "src.mp4")
        with open(src, "wb") as handle:
            handle.write(mp4)
        out = os.path.join(workdir, "trimmed.mp4")
        stitcher.trim(src, out, start_sec=0.0, end_sec=max(0.1, float(seconds)))
        with open(out, "rb") as handle:
            return handle.read()


# ================================================================ gen 상태 모델


def _empty_variant_state() -> dict:
    return {
        "status": "waiting",  # waiting|queued|preparing|submitted|completed|failed|cancelled
        "attempt": 0,
        "invocationArn": "",
        "outputKey": "",
        "outputPrefix": "",
        "firstFrameKey": "",
        "error": "",
        "claim": "",
        "claimedAt": "",
        "cancelRemote": None,
    }


def _empty_segment(seg: dict) -> dict:
    return {
        "id": seg["id"],
        "cutIds": list(seg.get("cutIds") or []),
        "seconds": int(seg.get("seconds") or 0),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


def _new_gen(plan: dict) -> dict:
    inp = plan.get("input") or {}
    conti = plan.get("conti") or {}
    video_model = str(inp.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
    resolution = _resolution_for(video_model, inp)
    segments = [_empty_segment(seg) for seg in (conti.get("segments") or [])]
    return {
        "genId": _gen_id(),
        "status": "running",  # running|done|partial|failed|cancelled
        "videoModel": video_model,
        "resolution": resolution,
        "createdAt": now_iso(),
        "opening": {"status": "waiting", "imageKey": "", "error": "", "claim": "", "claimedAt": ""},
        "segments": segments,
        "finalReal": {"status": "waiting", "key": "", "error": ""},
        "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
        "selected": None,  # None|"real"|"webtoon"
        "costUsd": 0.0,
    }


def _resolution_for(video_model: str, inp: dict) -> str:
    """이 모델의 기본 해상도(③에서 따로 고르지 않으므로 모델 기본)."""
    try:
        options = vpoc_ark.video_options(video_model)
        return str(options.get("defaultResolution") or vpoc_settings.default_resolution(video_model))
    except Exception:  # noqa: BLE001
        return vpoc_settings.default_resolution(video_model)


# ================================================================ 프리비즈 모드 gen(설계자 2026-10-21)
#
# 승인된 프리비즈 녹화 구간마다 실사·웹툰 두 작업을 **동시에**(구간끼리도 병렬) fal
# video-to-video/edit 로 제출한다. 옛 모드(mode 없음)의 opening·순서 진행과 달리, 프리비즈가
# 연속성(배치·카메라·인원·타이밍)을 보장하므로 앞 구간을 기다릴 필요가 없다.


def _empty_previz_segment(seg: dict) -> dict:
    return {
        "id": seg["id"],
        "cutIds": list(seg.get("cutIds") or []),
        "seconds": float(seg.get("seconds") or 0),
        "recordingKey": str(seg.get("recordingKey") or ""),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


# 영상 모델(fal video-to-video)이 받는 최소 길이(초). 3초보다 짧은 컷은 마지막 프레임 복제로 채운다.
PREVIZ_MIN_CLIP_S = 3.0


def _empty_previz_unit(unit: dict) -> dict:
    """컷 단위 작업 1개(설계자 2026-10-22, ⑤). 실사·웹툰 두 변형을 담는다."""
    return {
        "id": str(unit.get("id") or ""),
        "cutId": str(unit.get("cutId") or ""),
        "recSegId": str(unit.get("recSegId") or ""),
        "recordingKey": str(unit.get("recordingKey") or ""),
        "startSec": float(unit.get("startSec") or 0.0),
        "endSec": float(unit.get("endSec") or 0.0),
        "seconds": float(unit.get("seconds") or 0.0),
        "real": _empty_variant_state(),
        "webtoon": _empty_variant_state(),
    }


def _new_previz_gen(plan: dict) -> dict:
    """프리비즈 기반 gen(설계자 2026-10-22, ⑤). units = 컷 1개 = 작업 1개.

    기존 segments 필드는 옛 데이터 보기용으로만 남긴다(새 gen 은 빈 리스트). 모든 variant 가 바로
    queued(병렬 제출). 컷 클립은 제출 때 녹화 mp4 에서 컷 시간만큼 잘라 쓴다.
    """
    units = []
    for unit in _previz_units(plan):
        item = _empty_previz_unit(unit)
        item["real"]["status"] = "queued"
        item["webtoon"]["status"] = "queued"
        units.append(item)
    return {
        "genId": _gen_id(),
        "mode": "previz",
        "status": "running",  # running|done|partial|failed|cancelled
        "videoModel": _previz_model_endpoint(plan),
        "createdAt": now_iso(),
        "units": units,
        "segments": [],  # 옛 데이터 보기용(새 gen 은 units 를 쓴다).
        "finalReal": {"status": "waiting", "key": "", "error": ""},
        "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
        "selected": None,  # None|"real"|"webtoon"
        "costUsd": 0.0,
    }


# ---------------------------------------------------------------- 영상 모델 선택(설계자 2026-10-22)
#
# 프리비즈 방식은 ①의 input.videoModel(검증됨)로 고른 모델로 만든다. 모델은 "fal:<endpoint>" id 로
# 저장되며, 못 고른/옛 값은 기본(VNEW_PREVIZ_EDIT_MODEL)으로 되돌린다. fal 큐·caps 조회는 endpoint
# (fal: 접두사 없는) 문자열을 쓴다.


def _previz_model_endpoint(plan: dict) -> str:
    """이 계획이 쓸 프리비즈 영상 모델 endpoint(설계자 2026-10-22).

    input.videoModel 이 기본 모델 또는 등록된 previzCapable fal 모델이면 그 endpoint, 아니면 기본.
    """
    default_endpoint = str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    raw = str((plan.get("input") or {}).get("videoModel") or "")
    if not raw:
        return default_endpoint
    endpoint = raw.split(":", 1)[-1] if raw.startswith(vpoc_settings.FAL_ID_PREFIX) else raw
    if endpoint == default_endpoint:
        return default_endpoint
    # 등록된 previzCapable fal 모델만 허용. 그 밖은 기본으로 되돌린다.
    item = vpoc_fal.find(vpoc_settings.FAL_ID_PREFIX + endpoint)
    if item and (item.get("caps") or {}).get("previzCapable"):
        return endpoint
    return default_endpoint


def _previz_caps(endpoint: str) -> dict:
    """그 endpoint 의 caps(등록돼 있으면 레지스트리 caps). 기본 모델처럼 미등록이면 빈 dict.

    빈 dict 는 '기본 Kling 방식(elements)'으로 다룬다(아래 _previz_payload).
    """
    item = vpoc_fal.find(vpoc_settings.FAL_ID_PREFIX + str(endpoint or ""))
    return (item or {}).get("caps") or {}


# ================================================================ 금액(estimate)


def _previz_segments(plan: dict) -> list[dict]:
    """프리비즈 녹화 구간 목록 = conti.segments 중 녹화(mp4)가 된 것.

    각 항목: {id, cutIds, seconds(녹화 실측 초, 없으면 conti seconds), recordingKey}.
    녹화가 없는 구간은 제외하지 않고 recordingKey="" 로 둔다(선행 조건에서 recordedAll 로 거른다).
    """
    conti = plan.get("conti") or {}
    previz = plan.get("previz") or {}
    recordings = previz.get("recordings") or {}
    out: list[dict] = []
    for seg in conti.get("segments") or []:
        seg_id = str(seg.get("id") or "")
        rec = recordings.get(seg_id) or {}
        rec_seconds = float(rec.get("seconds") or 0) or float(seg.get("seconds") or 0)
        out.append(
            {
                "id": seg_id,
                "cutIds": list(seg.get("cutIds") or []),
                "seconds": rec_seconds,
                "recordingKey": str(rec.get("key") or ""),
            }
        )
    return out


def _previz_units(plan: dict) -> list[dict]:
    """컷 단위 작업 목록(설계자 2026-10-22, ⑤). 컷 1개 = 작업 1개.

    각 항목: {id(=cutId), cutId, recSegId, recordingKey, startSec, endSec, seconds}.
    - recSegId/recordingKey: 그 컷이 속한 녹화 구간(conti.segments)과 그 mp4 키.
    - startSec/endSec: 그 구간 **녹화 mp4 안**에서 이 컷의 시간 범위. 콘티 컷 seconds 를 녹화 실측
      길이에 비례 배분해 구한다(녹화가 콘티와 조금 달라도 컷 경계가 맞게).
    - seconds: 이 컷의 실제 길이(endSec − startSec).
    녹화가 없는 구간의 컷은 recordingKey="" 로 둔다(선행 조건에서 recordedAll 로 거른다).
    """
    conti = plan.get("conti") or {}
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    cuts_by_id = {str(c.get("id")): c for c in (conti.get("cuts") or [])}
    out: list[dict] = []
    for seg in conti.get("segments") or []:
        seg_id = str(seg.get("id") or "")
        rec = recordings.get(seg_id) or {}
        rec_key = str(rec.get("key") or "")
        cut_ids = [str(cid) for cid in (seg.get("cutIds") or [])]
        # 콘티 컷 seconds 합과 녹화 실측 길이의 비(녹화가 조금 다를 수 있다).
        conti_sum = sum(float((cuts_by_id.get(cid) or {}).get("seconds") or 0) for cid in cut_ids)
        rec_seconds = float(rec.get("seconds") or 0) or conti_sum or float(seg.get("seconds") or 0)
        scale = (rec_seconds / conti_sum) if conti_sum > 0 else 1.0
        clock = 0.0
        for cid in cut_ids:
            cut = cuts_by_id.get(cid) or {}
            cut_seconds = float(cut.get("seconds") or 0) * scale
            start = clock
            end = clock + cut_seconds
            clock = end
            out.append(
                {
                    "id": cid,
                    "cutId": cid,
                    "recSegId": seg_id,
                    "recordingKey": rec_key,
                    "startSec": round(start, 3),
                    "endSec": round(end, 3),
                    "seconds": round(cut_seconds, 3),
                }
            )
    return out


def _previz_model_unit_price(endpoint: str) -> float:
    """그 프리비즈 모델의 초당 단가(USD, 설계자 2026-10-22).

    기본 모델은 vnew_settings.previz_edit_usd(1초) 로 계산(fal pricing 우선, 실패 시 기본값).
    등록된 fal 모델은 fal pricing(초당)을, 못 받으면 기본 단가를 쓴다.
    """
    default_endpoint = str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    if not endpoint or endpoint == default_endpoint:
        return float(vnew_settings.previz_edit_usd(1))
    price = vpoc_fal.pricing_for(vpoc_settings.FAL_ID_PREFIX + endpoint)
    if not price:
        price = vpoc_fal.pricing(endpoint)
    if price and str(price.get("unit") or "").lower() == "seconds":
        unit_price = float(price.get("unitPrice") or 0.0)
        if unit_price > 0:
            return unit_price
    return float(vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S)


def estimate(plan: dict) -> dict:
    """금액 미리보기(설계자 2026-10-22, ⑤ — 컷 단위).

    컷마다 max(3, seconds) × 고른 모델의 초당 단가 × 2(실사+웹툰). 반환:
    {mode:"previz", videoModel, realUsd, webtoonUsd, totalUsd, units:[{id,cutId,seconds,realUsd,webtoonUsd}],
     segments:[...]}(segments 는 옛 화면 호환). 녹화가 없으면 totalUsd 0.
    """
    endpoint = _previz_model_endpoint(plan)
    per_sec = _previz_model_unit_price(endpoint)
    rows = []
    real_usd = 0.0
    webtoon_usd = 0.0
    for unit in _previz_units(plan):
        seconds = float(unit.get("seconds") or 0)
        billed = max(PREVIZ_MIN_CLIP_S, seconds)
        cost = round(per_sec * billed, 4)
        real_usd += cost
        webtoon_usd += cost
        rows.append(
            {
                "id": unit.get("id"),
                "cutId": unit.get("cutId"),
                "seconds": round(seconds, 3),
                "realUsd": cost,
                "webtoonUsd": cost,
            }
        )
    total = round(real_usd + webtoon_usd, 2)
    return {
        "mode": "previz",
        "videoModel": endpoint,
        "realUsd": round(real_usd, 4),
        "webtoonUsd": round(webtoon_usd, 4),
        "totalUsd": total,
        "units": rows,
        "segments": rows,  # 옛 화면 호환(구간 합계 자리에 컷 합계를 보여 준다).
    }



# ================================================================ 선행 조건 · 시작


def _char_entries(owner: str, inp: dict) -> list[dict]:
    """계획에 연결된 캐릭터를 로드해 외형·시트 키를 모은다(실사 front 없으면 그대로 둔다)."""
    out = []
    for item in inp.get("characters") or []:
        char_id = str(item.get("charId") or "")
        role = str(item.get("roleName") or "").strip()
        try:
            char, _ = characters.load_char(owner, char_id)
        except Exception:  # noqa: BLE001
            out.append({"charId": char_id, "roleName": role, "name": role, "missing": True})
            continue
        variants = char.get("variants") or {}
        real = variants.get("real") or {}
        webtoon = variants.get("webtoon") or {}
        analysis = char.get("analysis") or {}
        out.append(
            {
                "charId": char_id,
                "roleName": role or (char.get("name") or ""),
                "name": char.get("name") or role,
                "appearanceEn": str(analysis.get("appearanceEn") or ""),
                "outfitEn": str(analysis.get("outfitEn") or ""),
                "styleEn": str(analysis.get("styleEn") or ""),
                "realFrontKey": str(real.get("frontKey") or ""),
                "realSheetKey": str(real.get("sheetKey") or ""),
                "realBackKey": str(real.get("backKey") or ""),
                "webtoonSheetKey": str(webtoon.get("sheetKey") or ""),
                "webtoonFrontKey": str(webtoon.get("frontKey") or ""),
                "webtoonBackKey": str(webtoon.get("backKey") or ""),
                "missing": False,
            }
        )
    return out

def _model_accepts_real_start(model: str) -> bool:
    """이 모델이 실사 인물 시작 이미지를 받는지(설계자 2026-10-20).

    Seedance(byteplus, ARK_MODEL_IDS)만 실존 인물 필터로 거절한다 → false. Luma·fal 은 true.
    모델이 비거나 모르면 막지 않는다(true) — 기존 흐름을 깨지 않기 위해서다.
    """
    return str(model or "") not in vpoc_settings.ARK_MODEL_IDS




def check_preconditions(plan: dict, owner: str) -> list[dict]:
    """generate 선행 조건(설계자 2026-10-21, 지시서 038 — 프리비즈 기반). 어기면 VpocError(409).

    - 콘티가 ready 여야 한다(구간이 있어야 프리비즈·녹화가 가능).
    - 프리비즈가 **승인(approved)·모든 구간 녹화(recordedAll)** 돼야 한다. 아니면 409 PrevizRequired.
    - 등장 캐릭터 중 실사·웹툰 기본 복장 정면(front)이 없는 캐릭터가 있으면 409(이름 표기).
    - 이미 진행 중(gen.status running, stale 아님)이면 409.
    통과하면 캐릭터 목록을 돌려준다.
    """
    conti = plan.get("conti") or {}
    if conti.get("status") != "ready" or not (conti.get("segments")):
        raise VpocError("vnew.generate", "ContiNotReady", "먼저 콘티를 완성해 주세요.", 409)

    previz = plan.get("previz") or {}
    if not previz.get("approved") or not _previz_recorded_all(plan):
        raise VpocError(
            "vnew.generate",
            "PrevizRequired",
            "프리비즈를 승인하고 녹화해 주세요.",
            409,
        )

    brief = previz.get("shotBrief") or {}
    if brief.get("status") != "ready":
        raise VpocError(
            "vnew.generate",
            "BriefNotReady",
            "촬영 지시서를 만드는 중입니다. 잠시 후 다시 시도해 주세요.",
            409,
        )

    gen = plan.get("gen") or {}
    if gen.get("status") == "running" and not _gen_stale(gen):
        raise VpocError("vnew.generate", "GenRunning", "이미 영상 생성이 진행 중입니다.", 409)

    chars = _char_entries(owner, plan.get("input") or {})
    missing_names = [
        (c.get("roleName") or c.get("name") or c.get("charId"))
        for c in chars
        if c.get("missing") or not c.get("realFrontKey") or not c.get("webtoonFrontKey")
    ]
    if missing_names:
        raise VpocError(
            "vnew.generate",
            "CharacterRealMissing",
            "실사·웹툰 기본 복장 정면이 없는 등장인물이 있습니다: " + ", ".join(str(n) for n in missing_names),
            409,
        )
    return chars


def _previz_recorded_all(plan: dict) -> bool:
    """콘티 구간이 모두 녹화됐는지(previz.recordings 에 key 가 있는지)."""
    conti = plan.get("conti") or {}
    segments = conti.get("segments") or []
    if not segments:
        return False
    recordings = (plan.get("previz") or {}).get("recordings") or {}
    for seg in segments:
        rec = recordings.get(str(seg.get("id") or "")) or {}
        if not rec.get("key"):
            return False
    return True


def start_generate(owner: str, pid: str, confirm_usd: float) -> str:
    """새 genId 로 프리비즈 gen 을 만들고 백그라운드 진행을 시작한다. genId 를 돌려준다.

    금액이 서버 계산과 다르면 409 CostMismatch(vpoc 방식). 선행 조건 위반도 409.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    check_preconditions(plan, owner)

    expected = estimate(plan)["totalUsd"]
    if round(float(confirm_usd), 2) != expected:
        raise VpocError(
            "vnew.generate",
            "CostMismatch",
            f"금액이 서버 계산과 다릅니다. 서버 {expected} USD, 화면 {round(float(confirm_usd), 2)} USD.",
            409,
        )

    new = _new_previz_gen(plan)

    def mutator(data: dict) -> None:
        current = data.get("gen") or {}
        if current.get("status") == "running" and not _gen_stale(current):
            raise VpocError("vnew.generate", "GenRunning", "이미 영상 생성이 진행 중입니다.", 409)
        data["gen"] = new

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn_driver(owner, pid, new["genId"])
    return new["genId"]


# ================================================================ 진행 드라이버
#
# vpoc _ensure_driver 개념: 파드당 계획 1개. 드라이버 스레드가 한 번에 하나씩 진행한다.
# mutate 안에서는 preparing+claim 만 하고, 외부 호출(Seedream·영상·ffmpeg·fal)은 저장 성공 뒤
# 스레드에서, 결과는 claim 확인 후 기록한다(016-fix2 중복 제출 방지).

_drivers: dict[str, threading.Thread] = {}
_drivers_lock = threading.Lock()

# 프리비즈 모드는 한 순회에서 여러 칸을 **동시에** 제출/조회하므로, 같은 계획(plan)에 대한
# 읽기-수정-조건부쓰기(mutate_plan)가 스레드끼리 겹친다. 실제 S3 는 etag 조건부쓰기로 경합을
# 재시도하지만, 그 창(window) 안에서도 안전하도록 **같은 계획(owner,pid)별 프로세스 락**으로
# mutate 를 직렬화한다(이중 제출은 claim 확인이 막고, 락은 갱신 유실을 막는다).
_plan_locks: dict[str, threading.Lock] = {}
_plan_locks_guard = threading.Lock()


def _plan_lock(owner: str, pid: str) -> threading.Lock:
    key = f"{owner}/{pid}"
    with _plan_locks_guard:
        lock = _plan_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _plan_locks[key] = lock
        return lock


def _mutate_locked(owner: str, pid: str, mutator):
    """mutate_plan 을 계획별 락 안에서 수행(프리비즈 동시 제출의 갱신 유실 방지)."""
    with _plan_lock(owner, pid):
        return plan_mod.mutate_plan(owner, pid, mutator)



def _driver_name(owner: str, pid: str) -> str:
    return f"vnew-gen-{owner}-{pid}"


def _spawn_driver(owner: str, pid: str, gen_id: str) -> None:
    name = _driver_name(owner, pid)
    with _drivers_lock:
        existing = _drivers.get(name)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(target=_drive, args=(owner, pid, gen_id), daemon=True, name=name)
        _drivers[name] = thread
        thread.start()


def ensure_driver(owner: str, pid: str) -> None:
    """조회 때 호출: gen 이 running 이면 드라이버가 살아 있게 한다(파드 재시작 대비)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if gen.get("status") == "running":
        _spawn_driver(owner, pid, str(gen.get("genId") or ""))


def _drive(owner: str, pid: str, gen_id: str) -> None:
    """드라이버 본체. 한 단계씩 진행하며, 외부 호출을 하나 수행하고 결과를 기록한 뒤 다시 돈다.

    각 반복에서: (1) preparing 선점 해제(stale) (2) opening → 각 실사 구간 순서대로 →
    실사 완료마다 웹툰 변환 → 모두 끝나면 병합. 더 할 일이 없으면 종료.

    submitted 구간을 조회(poll)했는데 아직 InProgress 면 상태가 바뀌지 않아 같은 일을 또 고른다
    — 그때는 잠깐 쉰 뒤 다시 조회한다(바쁜 대기 방지). 상태가 바뀌면 바로 다음 일을 잇는다.
    """
    import time  # noqa: PLC0415

    for _ in range(100000):  # 안전 상한(무한 루프 방지).
        try:
            plan, _ = plan_mod.load_plan(owner, pid)
        except VpocError:
            return
        gen = plan.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            return

        if gen.get("mode") == "previz":
            # 프리비즈 모드: 한 순회에서 submitted 전부 조회 + queued 를 상한까지 동시 제출(병렬).
            progressed = _drive_previz_pass(owner, pid, gen_id)
            if progressed is None:
                return  # 더 할 일 없음(병합·상태 확정까지 끝).
            if progressed == "poll_only":
                # 새로 제출한 칸이 없고 submitted 만 남았다 → 잠깐 쉬고 다시 조회(각 칸 POLL 간격).
                if _still_in_progress(owner, pid, gen_id):
                    time.sleep(vnew_settings.POLL_INTERVAL_S)
            continue

        action = _next_action(owner, pid, gen_id)
        if action is None:
            return  # 더 할 일 없음(완료·partial — poll 로 조회할 submitted 도 없음).
        if action in ("poll_real", "poll_webtoon"):
            # 조회 직후 아직 완료 전이면 상태가 그대로다. 잠깐 쉰다(설정값 재사용).
            if _still_in_progress(owner, pid, gen_id):
                time.sleep(vnew_settings.POLL_INTERVAL_S)


def _still_in_progress(owner: str, pid: str, gen_id: str) -> bool:
    """submitted(조회 대기) 구간이 아직 남아 있으면 True(쉬고 다시 조회해야 함)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return False
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
        return False
    cells = _previz_cells(gen) if gen.get("mode") == "previz" else (gen.get("segments") or [])
    for seg in cells:
        for variant in ("real", "webtoon"):
            if (seg.get(variant) or {}).get("status") == "submitted":
                return True
    return False


def _reconcile_prepare_stale(gen: dict) -> bool:
    """preparing 이 15분 초과면 queued 로 되돌린다(선점 해제). 바뀌면 True."""
    changed = False
    now = datetime.now(timezone.utc)
    for seg in gen.get("segments") or []:
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "preparing":
                claimed = plan_mod._parse_iso(state.get("claimedAt") or "")
                if not claimed or (now - claimed).total_seconds() > PREPARE_STALE_S:
                    state["status"] = "queued"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    changed = True
    opening = gen.get("opening") or {}
    if opening.get("status") == "preparing":
        claimed = plan_mod._parse_iso(opening.get("claimedAt") or "")
        if not claimed or (now - claimed).total_seconds() > PREPARE_STALE_S:
            opening["status"] = "waiting"
            opening["claim"] = ""
            opening["claimedAt"] = ""
            changed = True
    return changed


def _next_action(owner: str, pid: str, gen_id: str):
    """다음에 할 일 하나를 골라 선점(claim)하고 수행한다. 수행했으면 action 이름, 없으면 None.

    순서(설계자 2026-10-20):
      1. opening(실사 시작 장면) 그리기
      2. 실사 구간을 순서대로 하나씩: 앞 구간 완료 전에는 다음 구간을 건드리지 않는다.
         - 완료된 실사 구간은 last frame 을 다음 구간 firstFrameKey 로 저장한다.
      3. submitted 실사·웹툰 구간은 조회(poll)로 완료/실패 확정.
      4. 실사 완료마다 그 구간 webtoon queued → 제출(병렬 가능).
      5. 모두 끝나면 병합.
    """
    claim = secrets.token_hex(8)
    # --- 1) stale 선점 해제 + opening 선점 ---
    picked = {"kind": None}

    def mutator(data: dict) -> None:
        gen = data.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            picked["kind"] = None
            return
        _reconcile_prepare_stale(gen)

        opening = gen.get("opening") or {}
        segments = gen.get("segments") or []

        # opening 먼저.
        if opening.get("status") in ("waiting", ""):
            opening["status"] = "preparing"
            opening["claim"] = claim
            opening["claimedAt"] = now_iso()
            picked["kind"] = "opening"
            return
        # opening 이 완료돼야 구간을 시작한다. preparing(다른 호출 진행 중)·failed 면 여기서 멈춘다
        # (failed opening 은 retry 로만 다시 시도).
        if opening.get("status") != "done":
            picked["kind"] = None
            return

        # --- 실사 구간: 순서대로 하나씩 ---
        # submitted 실사가 있으면 그것부터 조회한다(완료 전에는 다음으로 안 간다).
        for index, seg in enumerate(segments):
            real = seg["real"]
            if real["status"] == "submitted":
                picked["kind"] = "poll_real"
                picked["segIndex"] = index
                picked["claim"] = real.get("claim") or ""
                return
            if real["status"] in ("failed", "cancelled"):
                # 실패한 구간 뒤로는 진행하지 않는다(partial). retry 로만.
                break
            if real["status"] in ("waiting", "queued"):
                # 첫 프레임이 준비됐는지 확인(1구간=opening, 그 뒤=앞 구간 last frame).
                first_key = _first_frame_for(gen, index)
                if not first_key:
                    # 앞 구간이 아직 안 끝났다 → 여기서 멈춘다(순서 보장).
                    picked["kind"] = None
                    return
                real["status"] = "preparing"
                real["claim"] = claim
                real["claimedAt"] = now_iso()
                real["attempt"] = int(real.get("attempt") or 0) + 1
                real["firstFrameKey"] = first_key
                picked["kind"] = "submit_real"
                picked["segIndex"] = index
                return
            # completed 면 다음 구간으로.

        # --- 웹툰 변환: 실사 완료 구간 중 webtoon 이 할 일 있는 것(병렬) ---
        for index, seg in enumerate(segments):
            real = seg["real"]
            web = seg["webtoon"]
            if real["status"] != "completed":
                continue
            if web["status"] == "submitted":
                picked["kind"] = "poll_webtoon"
                picked["segIndex"] = index
                picked["claim"] = web.get("claim") or ""
                return
            if web["status"] in ("waiting", "queued"):
                web["status"] = "preparing"
                web["claim"] = claim
                web["claimedAt"] = now_iso()
                web["attempt"] = int(web.get("attempt") or 0) + 1
                picked["kind"] = "submit_webtoon"
                picked["segIndex"] = index
                return

        picked["kind"] = None

    plan_mod.mutate_plan(owner, pid, mutator)
    kind = picked.get("kind")
    if kind is None:
        _maybe_finalize(owner, pid, gen_id)
        return None

    # --- 저장 성공 뒤, 외부 호출을 스레드(현재) 에서 수행하고 결과를 claim 확인 후 기록 ---
    if kind == "opening":
        _do_opening(owner, pid, gen_id, claim)
    elif kind == "submit_real":
        _do_submit_real(owner, pid, gen_id, picked["segIndex"], claim)
    elif kind == "poll_real":
        _do_poll_real(owner, pid, gen_id, picked["segIndex"], picked.get("claim") or "")
    elif kind == "submit_webtoon":
        _do_submit_webtoon(owner, pid, gen_id, picked["segIndex"], claim)
    elif kind == "poll_webtoon":
        _do_poll_webtoon(owner, pid, gen_id, picked["segIndex"], picked.get("claim") or "")
    return kind


def _first_frame_for(gen: dict, index: int) -> str:
    """index 구간 실사의 첫 프레임 키. 1구간=opening, 그 뒤=앞 구간 last frame(firstFrameKey)."""
    if index == 0:
        opening = gen.get("opening") or {}
        return str(opening.get("imageKey") or "") if opening.get("status") == "done" else ""
    prev = (gen.get("segments") or [])[index - 1]
    prev_real = prev.get("real") or {}
    if prev_real.get("status") != "completed":
        return ""
    # 앞 구간 완료 시 저장해 둔 다음 구간 firstFrameKey.
    cur = (gen.get("segments") or [])[index]
    return str((cur.get("real") or {}).get("firstFrameKey") or "")


# ================================================================ 프리비즈 모드 드라이버(설계자 2026-10-21)
#
# 옛 모드와 달리 순서·opening 이 없다. 구간×그림체(real/webtoon) 를 **모두 병렬로** 제출한다.
# 각 variant 는 queued → preparing(선점) → submitted → completed/failed. 모두 completed 면
# 구간 순서대로 concat → finalReal/finalWebtoon.


def _previz_cells(gen: dict) -> list[dict]:
    """프리비즈 gen 의 작업 목록. 새 gen 은 units(컷 단위), 옛 gen 은 segments(구간 단위).

    설계자 2026-10-22(⑤): 생성 단위를 컷으로 바꿨다. units 가 있으면 그것을, 없으면 옛 segments 를
    쓴다(옛 데이터 보기·진행 호환). 반환 리스트 각 항목은 {real, webtoon} variant 상태를 담는다.
    """
    units = gen.get("units")
    if isinstance(units, list) and units:
        return units
    return gen.get("segments") or []


def _drive_previz_pass(owner: str, pid: str, gen_id: str):
    """프리비즈 모드 한 순회(설계자 2026-10-21, 지시서 041 · 2026-10-22 컷 단위). 반환:

    - None: 더 할 일이 없다(병합·상태 확정까지 끝냈다 → 드라이버 종료).
    - "work": 새로 제출한 칸이 있다(바로 다음 순회로).
    - "poll_only": 새 제출은 없고 submitted(조회 대기)만 있었다(쉬고 다시 조회).

    한 순회에서:
      1) preparing stale 선점 해제.
      2) submitted(조회 대기) 칸을 **전부** 모은다(각 칸 스레드로 조회 — 각 칸 POLL 간격 준수).
      3) queued/waiting 칸을 **동시 실행 상한(VNEW_GEN_PARALLEL) 안에서 선점해 동시에 제출**한다.
         상한은 "진행 중(preparing+submitted)" 합이 넘지 않게 한다 → 이미 submitted 가 많으면
         이번 순회엔 적게(또는 0) 제출한다. 제출·조회는 칸별 스레드로 병렬 수행하고 모두 끝날 때까지
         기다린다(이 순회가 동기적으로 끝나야 다음 순회가 상태를 다시 본다).
      선점·claim 확인·원장은 기존 규칙 그대로(이중 제출 금지). 칸 = 컷×그림체(units).
    """
    cap = max(1, int(vnew_settings.VNEW_GEN_PARALLEL or 1))
    polls: list[dict] = []    # [{index, variant, claim}]
    submits: list[dict] = []  # [{index, variant, claim}]

    def mutator(data: dict) -> None:
        gen = data.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
            return
        _reconcile_prepare_stale_previz(gen)
        cells = _previz_cells(gen)

        # 2) submitted 전부 조회 대상으로.
        active = 0  # 진행 중(preparing+submitted) — 상한 계산용.
        for index, seg in enumerate(cells):
            for variant in ("real", "webtoon"):
                state = seg.get(variant) or {}
                status = state.get("status")
                if status == "submitted":
                    active += 1
                    polls.append({"index": index, "variant": variant, "claim": state.get("claim") or ""})
                elif status == "preparing":
                    active += 1

        # 3) 상한 안에서 queued/waiting 선점(칸마다 새 claim).
        budget = max(0, cap - active)
        for index, seg in enumerate(cells):
            if budget <= 0:
                break
            for variant in ("real", "webtoon"):
                if budget <= 0:
                    break
                state = seg.get(variant) or {}
                if state.get("status") in ("waiting", "queued"):
                    claim = secrets.token_hex(8)
                    state["status"] = "preparing"
                    state["claim"] = claim
                    state["claimedAt"] = now_iso()
                    state["attempt"] = int(state.get("attempt") or 0) + 1
                    submits.append({"index": index, "variant": variant, "claim": claim})
                    budget -= 1

    _mutate_locked(owner, pid, mutator)

    if not polls and not submits:
        _maybe_finalize_previz(owner, pid, gen_id)
        return None

    # 칸별 스레드로 병렬 수행하고 모두 끝날 때까지 기다린다.
    workers: list[threading.Thread] = []
    for job in polls:
        workers.append(
            threading.Thread(
                target=_do_poll_previz,
                args=(owner, pid, gen_id, job["index"], job["variant"], job["claim"]),
                daemon=True,
                name=f"vnew-gen-poll-{pid}-{job['index']}-{job['variant']}",
            )
        )
    for job in submits:
        workers.append(
            threading.Thread(
                target=_do_submit_previz,
                args=(owner, pid, gen_id, job["index"], job["variant"], job["claim"]),
                daemon=True,
                name=f"vnew-gen-submit-{pid}-{job['index']}-{job['variant']}",
            )
        )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    return "work" if submits else "poll_only"


def _reconcile_prepare_stale_previz(gen: dict) -> bool:
    """preparing 이 15분 초과면 queued 로 되돌린다(선점 해제, 프리비즈 모드)."""
    changed = False
    now = datetime.now(timezone.utc)
    for seg in _previz_cells(gen):
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "preparing":
                claimed = plan_mod._parse_iso(state.get("claimedAt") or "")
                if not claimed or (now - claimed).total_seconds() > PREPARE_STALE_S:
                    state["status"] = "queued"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    changed = True
    return changed


def _cut_clip(video_mp4: bytes, unit: dict) -> dict:
    """녹화 mp4 에서 이 컷 구간을 잘라 fal 가 받는 형태로 만든다(설계자 2026-10-22, ⑤).

    - 컷 [startSec, endSec) 를 trim(프레임 정확 재인코딩)으로 자른다.
    - 3초 미만이면 마지막 프레임 복제(pad_to_seconds)로 3초까지 채운다. 채운 길이는 origSeconds 로
      기록해 합칠 때 원래 길이로 자른다.
    - 높이 720 미만이면 scale_to_min_height 로 키운다(기존 규칙 재사용).
    반환: {"bytes": 준비된 mp4, "origSeconds": 원래 컷 길이, "sentSeconds": 제출한 길이, "padded": bool}.
    """
    start = float(unit.get("startSec") or 0.0)
    end = float(unit.get("endSec") or 0.0)
    orig_seconds = max(0.0, end - start)
    with tempfile.TemporaryDirectory(prefix="vnew-cut-") as workdir:
        src = os.path.join(workdir, "rec.mp4")
        with open(src, "wb") as handle:
            handle.write(video_mp4)
        # 녹화 실측 길이로 end 를 clamp(비례 배분 오차 방지).
        rec_dur = stitcher.probe_duration(src) or end
        clip_end = min(end, rec_dur) if rec_dur else end
        if clip_end <= start:
            clip_end = start + orig_seconds if orig_seconds > 0 else start + PREVIZ_MIN_CLIP_S
        cut_path = os.path.join(workdir, "cut.mp4")
        stitcher.trim(src, cut_path, start_sec=start, end_sec=clip_end)
        current = cut_path
        cut_dur = stitcher.probe_duration(current) or orig_seconds

        padded = False
        sent_seconds = cut_dur
        if cut_dur < PREVIZ_MIN_CLIP_S - 0.01:
            padded_path = os.path.join(workdir, "padded.mp4")
            stitcher.pad_to_seconds(current, padded_path, target_sec=PREVIZ_MIN_CLIP_S)
            current = padded_path
            sent_seconds = stitcher.probe_duration(current) or PREVIZ_MIN_CLIP_S
            padded = True

        height = stitcher.probe_height(current)
        if height and height < stitcher.ANIME_MIN_HEIGHT:
            scaled_path = os.path.join(workdir, "scaled.mp4")
            stitcher.scale_to_min_height(current, scaled_path, min_height=stitcher.ANIME_MIN_HEIGHT)
            current = scaled_path

        with open(current, "rb") as handle:
            return {
                "bytes": handle.read(),
                "origSeconds": round(orig_seconds if orig_seconds > 0 else cut_dur, 3),
                "sentSeconds": round(sent_seconds, 3),
                "padded": padded,
            }


def _do_submit_previz(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """프리비즈 컷 한 그림체(real/webtoon)를 fal video-to-video/edit 로 제출한다(설계자 2026-10-22, ⑤).

    video_url = 그 컷 구간을 녹화 mp4 에서 잘라(3초 미만은 채워) data URI 로. keep_audio false.
    elements = 그 컷에 보이는 인물만(숨긴 인물 제외)의 그림체별 기본 복장 정면 + 후면(비율 맞춤).
    웹툰은 image_urls 1장(원작 웹툰 컷) 추가. prompt 는 CAST/SHOT/.../CONTINUITY(촬영 지시서 사용).
    실제 보낸 프롬프트는 promptEn 으로 기록한다(바이트 수 포함).
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, variant, claim):
            return
        unit = _previz_cells(gen)[index]
        state = unit[variant]
        attempt = int(state.get("attempt") or 1)
        rec_key = str(unit.get("recordingKey") or "")
        if not rec_key:
            raise VpocError("vnew.generate", "NoRecording", "컷 녹화가 없습니다.", 409)
        video_mp4 = _fetch_bytes(rec_key, bucket=vpoc_settings.VIDEO_BUCKET)
        clip = _cut_clip(video_mp4, unit)

        # 이 gen 이 쓸 모델 endpoint 와 그 caps(모델별 payload 어댑터, 설계자 2026-10-22).
        endpoint = str(gen.get("videoModel") or vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
        caps = _previz_caps(endpoint)

        built = _previz_payload(owner, pid, plan, unit, variant, caps=caps)
        prompt = built["prompt"]
        elements = built["elements"]
        image_urls = built["imageUrls"]
        prompt_bytes = len(prompt.encode("utf-8"))
        unit_id = str(unit.get("id") or unit.get("cutId") or index)
        prefix = _seg_output_prefix(owner, pid, gen_id, unit_id + "-" + variant, attempt)
        result = _submit_previz_edit(
            endpoint=endpoint,
            caps=caps,
            video_mp4=clip["bytes"],
            prompt=prompt,
            output_prefix=prefix,
            elements=elements,
            image_urls=image_urls,
        )
        _charge_previz(owner, pid, gen_id, result, max(PREVIZ_MIN_CLIP_S, float(unit.get("seconds") or 0)))

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, variant, claim):
                raise _Discard()
            s = _previz_cells(g)[index][variant]
            s["status"] = "submitted"
            s["invocationArn"] = result.get("invocationArn") or ""
            s["outputPrefix"] = result.get("outputPrefix") or prefix
            s["error"] = ""
            s["promptEn"] = prompt
            s["promptBytes"] = prompt_bytes
            s["origSeconds"] = clip.get("origSeconds")
            s["sentSeconds"] = clip.get("sentSeconds")
            s["padded"] = bool(clip.get("padded"))
            # claim 은 submitted 조회 때도 쓰므로 유지한다.

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))


def _do_poll_previz(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str) -> None:
    """프리비즈 컷 한 그림체 조회. 완료면 completed + outputKey."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, variant, claim):
            return
        state = _previz_cells(gen)[index][variant]
        arn = str(state.get("invocationArn") or "")
        prefix = str(state.get("outputPrefix") or "")
        poll_result = vpoc_fal.poll(arn, prefix)
        status = str(poll_result.get("status") or "")
        if status == "InProgress":
            return
        if status != "Completed":
            _fail_variant(owner, pid, gen_id, index, variant, claim, poll_result.get("failureMessage") or "영상 생성 실패")
            return
        output_key = _find_output_key(prefix, arn)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, variant, claim):
                raise _Discard()
            s = _previz_cells(g)[index][variant]
            s["status"] = "completed"
            s["outputKey"] = output_key
            s["error"] = ""
            s["claim"] = ""
            s["claimedAt"] = ""

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, variant, claim, _msg(exc))


def _maybe_finalize_previz(owner: str, pid: str, gen_id: str) -> None:
    """프리비즈 모드 병합·상태 확정(컷 단위).

    - 실사·웹툰 각각 모든 컷 completed 면 concat → finalReal/finalWebtoon.
    - 어느 한쪽이 전부 끝(completed/failed)이고 진행 중이 없으면 더 기다리지 않는다.
    - 실사·웹툰 모두 더 진행할 일이 없으면 done(두 final 다 done) 또는 partial(실패가 있으면).
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") != "running":
        return
    cells = _previz_cells(gen)
    if not cells:
        return

    for variant in ("real", "webtoon"):
        states = [seg[variant]["status"] for seg in cells]
        all_done = all(s == "completed" for s in states)
        final_field = "finalReal" if variant == "real" else "finalWebtoon"
        if all_done and (gen.get(final_field) or {}).get("status") not in ("done", "running"):
            _do_concat(owner, pid, gen_id, variant)

    # 상태 확정.
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        return

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or g.get("status") != "running":
            raise _Discard()
        segs = _previz_cells(g)
        reals = [s["real"]["status"] for s in segs]
        webs = [s["webtoon"]["status"] for s in segs]
        pending = any(x in ("waiting", "queued", "preparing", "submitted") for x in reals + webs)
        if pending:
            return
        # 더 진행할 일이 없다. 실패가 하나라도 있으면 partial, 아니면 done.
        any_fail = any(x in ("failed", "cancelled") for x in reals + webs)
        fr_done = (g.get("finalReal") or {}).get("status") == "done"
        fw_done = (g.get("finalWebtoon") or {}).get("status") == "done"
        if any_fail or not (fr_done and fw_done):
            g["status"] = "partial"
        else:
            g["status"] = "done"

    try:
        _mutate_locked(owner, pid, mutator)
    except _Discard:
        return


# ---------------------------------------------------------------- 프리비즈 payload · prompt


# 마네킹 색(hex) → 사람이 읽는 색 이름(프롬프트의 "The {color} mannequin"). 팔레트 이름을 먼저
# 쓰고(previz_schema.PALETTE_NAME_BY_HEX), 팔레트에 없는 옛 데이터(임의 hex)는 RGB 거리로 가장
# 가까운 기본 색 이름을 고른다. 설계자 2026-10-22: 절대 hex 를 프롬프트에 넣지 않는다.
#
# 기본 색 사전(이름 → RGB). 영상 모델이 확실히 아는 평범한 색 단어만 둔다. 팔레트 밖 hex(예:
# #00e5ff=cyan, #ff8a00=orange)를 사람이 읽는 색으로 근사하는 데 쓴다.
_BASIC_COLORS = (
    ("black", (0, 0, 0)),
    ("white", (255, 255, 255)),
    ("grey", (128, 128, 128)),
    ("red", (220, 20, 20)),
    ("orange", (245, 130, 50)),
    ("yellow", (255, 225, 25)),
    ("green", (60, 180, 75)),
    ("cyan", (70, 220, 250)),
    ("teal", (70, 153, 144)),
    ("blue", (67, 99, 216)),
    ("navy", (0, 0, 128)),
    ("purple", (145, 30, 180)),
    ("magenta", (240, 50, 230)),
    ("pink", (250, 190, 212)),
    ("brown", (154, 99, 36)),
    ("lime", (191, 239, 69)),
    ("lavender", (220, 190, 255)),
    ("olive", (128, 128, 0)),
    ("maroon", (128, 0, 0)),
)


def _hex_to_rgb(hex_color: str):
    """#rrggbb → (r, g, b). 짧거나 잘못된 값이면 None."""
    text = str(hex_color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _color_name(hex_color: str) -> str:
    """#rrggbb → 색 이름. 팔레트에 있으면 그 이름, 없으면 RGB 거리로 가장 가까운 기본 색 이름.

    설계자 2026-10-22: 영상 모델이 hex 를 못 읽어 인물이 뒤바뀌므로 **절대 hex 를 돌려주지 않는다**.
    값이 비거나 못 읽으면 "grey".
    """
    key = str(hex_color or "").strip().lower()
    named = previz_schema.PALETTE_NAME_BY_HEX.get(key)
    if named:
        return named
    rgb = _hex_to_rgb(key)
    if rgb is None:
        return "grey"
    best_name = "grey"
    best_dist = None
    for name, (r, g, b) in _BASIC_COLORS:
        dist = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _blank_line(text: str) -> bool:
    """대사가 사실상 비었는지(…·공백·점만). silent 판정에 쓴다."""
    stripped = str(text or "").strip()
    if not stripped:
        return True
    # "…", "… ….", "...", "⋯" 처럼 점·말줄임만 남은 것.
    for ch in ("…", ".", "⋯", " ", "\t", "\u200b"):
        stripped = stripped.replace(ch, "")
    return stripped == ""


def _cut_lines(plan: dict, cut: dict) -> list[dict]:
    """한 컷의 대사 귀속 → [{speaker, lineKo}] (설계자 2026-10-22, ⑤ 대사 섞임 수정).

    - script.entries 중 그 컷 panels 와 **겹치는** entry 의 speaker·lineKo 를 순서대로 모은다.
    - 겹치는 entry 가 없으면 cut.dialogueKo 를 `이름: "…"` / 구분자(" / ")로 파싱한다.
    - 빈 대사("… ….", "…", 공백)는 speaker 유지하고 lineKo="" + silent=True 로 표시한다.
    반환: [{speaker, lineKo, silent}].
    """
    cut_panels = set(int(p) for p in (cut.get("panels") or []) if isinstance(p, (int, float)) and not isinstance(p, bool))
    out: list[dict] = []

    entries = (plan.get("script") or {}).get("entries") or []
    matched = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_panels = set(int(p) for p in (entry.get("panels") or []) if isinstance(p, (int, float)) and not isinstance(p, bool))
        if cut_panels and entry_panels and (cut_panels & entry_panels):
            matched.append(entry)

    if matched:
        for entry in matched:
            speaker = str(entry.get("speaker") or "").strip()
            line = str(entry.get("lineKo") or "").strip()
            silent = _blank_line(line)
            out.append({"speaker": speaker, "lineKo": "" if silent else line, "silent": silent})
        # 겹치는 entry 가 모두 빈 대사(speaker·lineKo 없음)면 컷 dialogueKo 로 보강한다(옛 대본 호환).
        if any((not o.get("silent")) or o.get("speaker") for o in out):
            return out
        out = []

    # 겹치는 entry 가 없거나(또는 모두 빈 entry) → cut.dialogueKo 를 파싱한다.
    dialogue = str(cut.get("dialogueKo") or "").strip()
    if not dialogue:
        return []
    # " / " 로 여러 대사 구분. 각 조각은 "이름: \"…\"" 모양일 수 있다.
    pieces = [p.strip() for p in dialogue.split(" / ") if p.strip()]
    if not pieces:
        pieces = [dialogue]
    for piece in pieces:
        speaker = ""
        line = piece
        # "이름: 대사" 로 화자를 가른다(첫 콜론 기준, 한국어 콜론 포함).
        for sep in (": ", "：", ":"):
            if sep in piece:
                head, tail = piece.split(sep, 1)
                head = head.strip()
                # 이름이 너무 길면(문장) 화자가 아니다.
                if head and len(head) <= 20 and '"' not in head and "“" not in head:
                    speaker = head
                    line = tail.strip()
                    break
        # 바깥 따옴표 제거.
        line = line.strip().strip('"').strip("“”").strip()
        silent = _blank_line(line)
        out.append({"speaker": speaker, "lineKo": "" if silent else line, "silent": silent})
    return out


def _scene_by_cut(plan: dict) -> dict:
    """cutId → 그 컷이 속한 프리비즈 scene. 배우 색·등장 여부를 찾을 때 쓴다."""
    out: dict[str, dict] = {}
    for scene in (plan.get("previz") or {}).get("scenes") or []:
        for entry in scene.get("timeline") or []:
            cid = str(entry.get("cutId") or "")
            if cid:
                out[cid] = scene
    return out


def _segment_actors(plan: dict, seg: dict) -> list[dict]:
    """이 구간에 등장하는 프리비즈 배우 목록(화면 노출 순 ≈ 등장 컷 수 많은 순).

    각 배우: {id, name, color, colorName, charId, appearanceEn, exposure(등장 컷 수)}. 구간의 cutIds
    를 돌며 각 컷이 속한 scene 의 timeline 에서 그 컷의 actors(등장 인물)를 모으고, scene.actors
    에서 색·이름·charId 를 찾는다.
    """
    scene_by_cut = _scene_by_cut(plan)
    exposure: dict[str, int] = {}
    actor_meta: dict[str, dict] = {}
    cut_ids = _seg_cut_ids(seg)
    for cut_id in cut_ids:
        scene = scene_by_cut.get(str(cut_id))
        if not scene:
            continue
        actors_by_id = {str(a.get("id")): a for a in (scene.get("actors") or [])}
        for entry in scene.get("timeline") or []:
            if str(entry.get("cutId") or "") != str(cut_id):
                continue
            for ca in entry.get("actors") or []:
                aid = str(ca.get("id") or "")
                if not aid:
                    continue
                exposure[aid] = exposure.get(aid, 0) + 1
                if aid not in actor_meta and aid in actors_by_id:
                    meta = actors_by_id[aid]
                    actor_meta[aid] = {
                        "id": aid,
                        "name": str(meta.get("name") or ""),
                        "color": str(meta.get("color") or ""),
                        "colorName": _color_name(str(meta.get("color") or "")),
                        "charId": str(meta.get("charId") or ""),
                        "appearanceEn": str(meta.get("appearanceEn") or ""),
                    }
    out = []
    for aid, meta in actor_meta.items():
        meta = dict(meta)
        meta["exposure"] = exposure.get(aid, 0)
        out.append(meta)
    # 화면에 오래·크게 나오는 순(등장 컷 수 내림차순, 같으면 이름).
    out.sort(key=lambda m: (-int(m.get("exposure") or 0), m.get("name") or ""))
    return out


def _char_by_id(owner: str, plan: dict) -> dict:
    """charId → _char_entries 항목."""
    out: dict[str, dict] = {}
    for c in _char_entries(owner, plan.get("input") or {}):
        cid = str(c.get("charId") or "")
        if cid:
            out[cid] = c
    return out


def _fit_b64(jpeg: bytes) -> str:
    """참고 이미지 비율(0.4~2.5)을 맞추고 base64 로."""
    return base64.b64encode(imaging.fit_ratio(jpeg)).decode("ascii")


def _load_jpeg(key: str) -> bytes | None:
    if not key:
        return None
    try:
        return STORE.get_bytes(key)
    except Exception:  # noqa: BLE001
        return None


def _seg_cut_ids(seg: dict) -> list[str]:
    """unit(cutId 하나) · 옛 seg(cutIds 여럿) 모두에서 컷 id 목록을 뽑는다."""
    ids = seg.get("cutIds")
    if ids:
        return [str(c) for c in ids]
    cid = seg.get("cutId")
    return [str(cid)] if cid else []


def _previz_payload(owner: str, pid: str, plan: dict, seg: dict, variant: str, *, caps: dict | None = None) -> dict:
    """한 컷·한 그림체의 제출 payload(elements · imageUrls · prompt)를 만든다(설계자 2026-10-22).

    모델별 어댑터(caps):
    - elementsField 가 있으면(또는 caps 가 비어 기본 Kling): 지금 방식 — elements[{frontal_image_url,
      reference_image_urls:[후면]}] + CAST 의 @Element 매핑. 웹툰은 imageUrls 1장(원작 웹툰 컷).
    - elements 가 없고 imageListField 가 있으면: 참고 이미지 = 그 컷에 보이는 인물의 기본 복장 정면
      (실사/웹툰) 순서대로 + (웹툰) 그림체 참고 1장, imageListMax 이내. CAST 는 "Image {n} is {name}"
      형식(이미지 번호 = 실제 순서), 마네킹 색 매핑 "The {color} mannequin becomes the person in Image {n}".
    elements+imageUrls 합계는 PREVIZ_MAX_INPUTS(4) 이내. charId 없는 인물은 CAST 색 문장에만.
    """
    caps = caps or {}
    # elementsField 가 명시돼 있으면 elements 모드. imageListField 만 있으면 imageList 모드.
    # caps 가 비면(기본 Kling 미등록) elements 모드로 본다(기존 동작 유지).
    use_image_list = bool(caps.get("imageListField")) and not caps.get("elementsField")
    if use_image_list:
        return _previz_payload_image_list(owner, pid, plan, seg, variant, caps)
    return _previz_payload_elements(owner, pid, plan, seg, variant)


def _previz_payload_elements(owner: str, pid: str, plan: dict, seg: dict, variant: str) -> dict:
    """elements 방식(기존 Kling O3, 설계자 2026-10-22). @Element 매핑."""
    is_real = variant == "real"
    max_elements = vnew_settings.PREVIZ_REAL_MAX_ELEMENTS if is_real else vnew_settings.PREVIZ_WEBTOON_MAX_ELEMENTS
    max_image_urls = 0 if is_real else vnew_settings.PREVIZ_WEBTOON_MAX_IMAGE_URLS

    actors = _segment_actors(plan, seg)
    char_by_id = _char_by_id(owner, plan)

    elements: list[dict] = []
    # CAST 줄 재료: {colorName, name, elementRef(@Element{n} 또는 ""), appearance}.
    cast: list[dict] = []

    element_index = 0
    for actor in actors:
        color_name = actor.get("colorName") or "grey"
        name = actor.get("name") or "a character"
        char_id = actor.get("charId") or ""
        char = char_by_id.get(char_id) if char_id else None
        if char and element_index < max_elements:
            front_key = char.get("realFrontKey") if is_real else char.get("webtoonFrontKey")
            back_key = char.get("realBackKey") if is_real else char.get("webtoonBackKey")
            front = _load_jpeg(str(front_key or ""))
            if front:
                element_index += 1
                element = {"frontal_image_url": imaging.fit_ratio(front)}
                back = _load_jpeg(str(back_key or ""))
                if back:
                    element["reference_image_urls"] = [imaging.fit_ratio(back)]
                else:
                    element["reference_image_urls"] = []
                elements.append(element)
                appearance = ((char.get("appearanceEn") or "") + " " + (char.get("outfitEn") or "")).strip()
                cast.append({"colorName": color_name, "name": name, "elementRef": f"@Element{element_index}", "appearance": appearance})
                continue
        # charId 없음(또는 정면 로드 실패/정원 초과) → CAST 색 문장만.
        cast.append({"colorName": color_name, "name": name, "elementRef": "", "appearance": actor.get("appearanceEn") or ""})

    image_urls: list[bytes] = []
    if max_image_urls > 0 and len(elements) + max_image_urls <= vnew_settings.PREVIZ_MAX_INPUTS:
        panel = _segment_panel_jpeg(owner, pid, plan, seg)
        if panel is not None:
            image_urls.append(imaging.fit_ratio(panel))

    prompt = _cut_prompt(plan, seg, variant, cast=cast, has_image_ref=bool(image_urls))
    # 합계 4 넘지 않게(안전) 자른다.
    total_allowed = vnew_settings.PREVIZ_MAX_INPUTS
    if len(elements) > max_elements:
        elements = elements[:max_elements]
    if len(elements) + len(image_urls) > total_allowed:
        image_urls = image_urls[: max(0, total_allowed - len(elements))]

    return {"elements": elements, "imageUrls": image_urls, "prompt": prompt}


def _previz_payload_image_list(owner: str, pid: str, plan: dict, seg: dict, variant: str, caps: dict) -> dict:
    """imageList 방식(설계자 2026-10-22). 참고 이미지 = 보이는 인물 정면 순서 + (웹툰) 그림체 참고 1장.

    elements 는 비우고, image_urls(imageListField) 에 참고 이미지를 담는다. CAST 는 "Image {n} is
    {name}" 번호와 "The {color} mannequin becomes the person in Image {n}" 매핑을 쓴다. 번호는 실제
    image_urls 순서와 일치시킨다(이미지 1부터). imageListMax 이내.
    """
    is_real = variant == "real"
    max_images = int(caps.get("imageListMax") or vpoc_fal.DEFAULT_IMAGE_LIST_MAX)

    actors = _segment_actors(plan, seg)
    char_by_id = _char_by_id(owner, plan)

    image_urls: list[bytes] = []
    cast: list[dict] = []
    # (웹툰) 그림체 참고 1장 자리를 남겨 둔다.
    style_slots = 1 if not is_real else 0
    image_budget = max(0, max_images - style_slots)

    for actor in actors:
        color_name = actor.get("colorName") or "grey"
        name = actor.get("name") or "a character"
        char_id = actor.get("charId") or ""
        char = char_by_id.get(char_id) if char_id else None
        placed = False
        if char and len(image_urls) < image_budget:
            front_key = char.get("realFrontKey") if is_real else char.get("webtoonFrontKey")
            front = _load_jpeg(str(front_key or ""))
            if front:
                image_urls.append(imaging.fit_ratio(front))
                image_no = len(image_urls)  # 1부터, 실제 순서와 일치
                appearance = ((char.get("appearanceEn") or "") + " " + (char.get("outfitEn") or "")).strip()
                cast.append({"colorName": color_name, "name": name, "imageNo": image_no, "appearance": appearance})
                placed = True
        if not placed:
            cast.append({"colorName": color_name, "name": name, "imageNo": None, "appearance": actor.get("appearanceEn") or ""})

    # (웹툰) 그림체 참고 1장(원작 웹툰 컷). 인물 이미지 뒤에 둔다(번호는 인물에만 쓴다).
    has_style_ref = False
    if style_slots and len(image_urls) < max_images:
        panel = _segment_panel_jpeg(owner, pid, plan, seg)
        if panel is not None:
            image_urls.append(imaging.fit_ratio(panel))
            has_style_ref = True

    prompt = _cut_prompt(plan, seg, variant, cast=cast, has_image_ref=has_style_ref)
    # imageListMax 이내로 자른다(안전).
    if len(image_urls) > max_images:
        image_urls = image_urls[:max_images]

    return {"elements": [], "imageUrls": image_urls, "prompt": prompt}


def _location_name_en(plan: dict, seg: dict) -> str:
    """이 컷(또는 구간 첫 컷)의 장소 이름(영어). 콘티 cut.locationEn 을 쓴다."""
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    for cut_id in _seg_cut_ids(seg):
        cut = cuts.get(str(cut_id))
        if cut and cut.get("locationEn"):
            return str(cut.get("locationEn"))
    return "the scene"


def _brief_bundle(plan: dict) -> dict:
    """저장된 촬영 지시서(previz.shotBrief). 없으면 빈 dict."""
    brief = (plan.get("previz") or {}).get("shotBrief")
    return brief if isinstance(brief, dict) else {}


def _scene_brief_text(plan: dict, seg: dict) -> str:
    """이 컷이 속한 장소의 촬영 지시서 scene 공통 문장(sceneEn). 장소 id 로 찾는다."""
    brief = _brief_bundle(plan)
    scene_map = brief.get("scene") if isinstance(brief.get("scene"), dict) else {}
    if not scene_map:
        return ""
    # 컷 → 장소 id: 프리비즈 scene 의 locationId 를 쓴다(cut 이 속한 scene).
    first_ids = _seg_cut_ids(seg)
    scene = _scene_by_cut(plan).get(str(first_ids[0] if first_ids else ""))
    loc_id = str((scene or {}).get("locationId") or "")
    if loc_id and loc_id in scene_map:
        return str(scene_map.get(loc_id) or "")
    # 장소 id 를 못 맞추면 첫 scene 문장을 쓴다(장소가 하나뿐일 때).
    if len(scene_map) == 1:
        return str(next(iter(scene_map.values())) or "")
    return ""


def _cut_brief(plan: dict, cut_id: str) -> dict:
    """이 컷의 촬영 지시서(shotEn·blockingEn·actionEn·moodEn·backgroundEn·dialogue). 없으면 빈 dict."""
    brief = _brief_bundle(plan)
    cuts = brief.get("cuts") if isinstance(brief.get("cuts"), dict) else {}
    item = cuts.get(str(cut_id))
    return item if isinstance(item, dict) else {}


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _trim_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 바이트 상한에 맞춰 뒤에서 자른다(글자 경계 유지)."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    # 멀티바이트 글자가 잘리지 않게 뒤에서 깎는다.
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    # 마지막 리드바이트도 불완전하면 제거.
    if cut and (cut[-1] & 0x80) and not (cut[-1] & 0x40):
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore")


def _split_sentences(text: str) -> list[str]:
    """영어 문장을 마침표·물음표·느낌표 뒤 공백 기준으로 나눈다(간단 규칙, 설계자 2026-10-22).

    각 조각은 끝 구두점을 포함한다. 소수점·약어를 완벽히 가르지는 않지만, 촬영 지시서 문장 단위
    줄이기에는 충분하다. 빈 조각은 버린다.
    """
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return []
    parts = _re.split(r"(?<=[.!?])\s+", body)
    return [p.strip() for p in parts if p.strip()]


def _drop_tail_sentences(text: str, over_bytes: int, *, keep_first: bool = False) -> str:
    """UTF-8 바이트가 over_bytes 만큼 넘칠 때, 뒤 문장부터 하나씩 지워 그만큼(이상)만 줄인다.

    설계자 2026-10-22: 각 단계는 '넘친 만큼만' 문장 단위로 줄이고, 한도 안에 들어오면 즉시 멈춘다.
    keep_first=True 면 첫 문장은 절대 지우지 않는다(ACTION 첫 문장 보호). 더 지울 게 없으면 남은
    것을 그대로 돌려준다(상위에서 다른 단계로 넘어간다).
    """
    if over_bytes <= 0:
        return text
    sentences = _split_sentences(text)
    if not sentences:
        return text
    floor = 1 if keep_first else 0
    removed = 0
    while len(sentences) > floor and removed < over_bytes:
        dropped = sentences.pop()
        removed += len((" " + dropped).encode("utf-8"))
    return " ".join(sentences)


def _scene_summary(scene_en: str, max_chars: int = 350) -> str:
    """장소 문단(sceneEn)의 앞 2~3문장을 뽑아 장소 요약을 만든다(최대 max_chars 자, 설계자 2026-10-22).

    장소 문단 전체를 컷 프롬프트에 넣지 않고, 공간 요약만 넣어 ACTION 이 밀리지 않게 한다.
    """
    body = str(scene_en or "").strip()
    if not body:
        return ""
    sentences = _split_sentences(body)
    picked: list[str] = []
    for sentence in sentences[:3]:
        candidate = (" ".join(picked + [sentence])).strip()
        if len(candidate) > max_chars:
            break
        picked.append(sentence)
    summary = " ".join(picked).strip()
    if not summary:
        # 첫 문장이 이미 max_chars 를 넘으면 글자 단위로 자른다.
        summary = body[:max_chars].strip()
    return summary[:max_chars].strip()


def _cut_prompt(plan: dict, seg: dict, variant: str, *, cast: list[dict], has_image_ref: bool) -> str:
    """컷 프롬프트(영상 모델 전달문, 설계자 2026-10-22, ⑤). 영어, UTF-8 2,500바이트 이내.

    순서 고정: ASSETS(자료 번호 목록) → @Video1 안내 → CAST → SHOT → BLOCKING → ACTION → MOOD → SETTING → DIALOGUE → CONTINUITY.
    SETTING = 컷별 backgroundEn + 장소 요약(sceneEn 앞 2~3문장, 최대 350자). 장소 문단 전체는 넣지 않는다.
    촬영 지시서(shotBrief)를 쓰되, 없으면 그 자리는 비운다(ASSETS·CAST·SHOT·CONTINUITY 는 반드시 유지).

    바이트 초과 시 줄이는 순서(각 단계는 **넘친 만큼만** 문장 단위로 줄이고, 한도 안에 들어오면 즉시
    멈춘다): ① SETTING 의 장소 요약 → ② SETTING 의 backgroundEn → ③ MOOD → ④ BLOCKING 뒤쪽 문장
    → ⑤ DIALOGUE 의 deliveryEn → ⑥ SHOT 뒤쪽 문장 → ⑦ 마지막에만 ACTION 뒤쪽 문장.
    ASSETS·CAST·CONTINUITY·ACTION 첫 문장은 절대 지우지 않는다. **한도 안이면 아무 것도 지우지 않는다.**
    """
    is_real = variant == "real"
    cut_ids = _seg_cut_ids(seg)
    cut_id = cut_ids[0] if cut_ids else ""
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    cut = cuts_by_id.get(str(cut_id)) or {}
    brief = _cut_brief(plan, cut_id)

    if is_real:
        style = "photorealistic live-action film"
    elif has_image_ref:
        style = (
            "a Korean webtoon animation in exactly the drawing style of @Image1 "
            "(line, coloring, shading; not generic anime)"
        )
    else:
        style = "a Korean webtoon animation (hand-drawn line, coloring and shading; not generic anime)"

    head = (
        "@Video1 is a rough 3D previz (blocking and camera only). "
        "Re-render this single shot as " + style + "."
    )

    # CAST — 그 컷에 보이는 인물만. 마네킹 색은 "The {color} mannequin is @ElementN, {name}" 에만
    # 쓰고, 외형은 캐릭터 데이터(appearance = appearanceEn + outfitEn)로 쓴다. "mannequin" 이 든
    # 외형 문장은 지우고, 문장 끝 마침표 중복("..")은 없앤다(설계자 2026-10-22).
    def _clean_appearance(text: str) -> str:
        cleaned = []
        for sentence in _split_sentences(text):
            if "mannequin" in sentence.lower():
                continue
            cleaned.append(sentence)
        body = " ".join(cleaned).strip()
        # 끝 마침표 중복 제거.
        while body.endswith(".."):
            body = body[:-1]
        return body.strip()

    cast_sentences = []
    # ASSETS 줄 재료(설계자 2026-10-22): 번호로 매긴 입력 자료 목록. elements 모델은 @Element{n},
    # 이미지 목록 모델은 Image {n}, 웹툰 그림체 참고는 @Image{k}. 모델이 자료를 번호로 읽게 한다.
    asset_refs: list[str] = []
    for member in cast:
        color = member.get("colorName") or "grey"
        name = member.get("name") or "a character"
        ref = member.get("elementRef") or ""
        image_no = member.get("imageNo")
        appearance = _clean_appearance(member.get("appearance") or "")
        if image_no:
            # imageList 모델(설계자 2026-10-22): "Image {n} is {name}" + 마네킹 색 매핑.
            # "The {color} mannequin becomes the person in Image {n}".
            sentence = (
                f"Image {image_no} is {name}; the {color} mannequin becomes the person in Image {image_no}"
            )
            asset_refs.append(f"Image {image_no} = {name} reference sheet")
        elif ref:
            sentence = f"The {color} mannequin is {ref}, {name}"
            asset_refs.append(f"{ref} = {name} reference sheet")
        else:
            sentence = f"The {color} mannequin is {name}"
        if appearance:
            sentence += f": {appearance}"
        # 끝 마침표 하나만.
        sentence = sentence.rstrip(".").rstrip() + "."
        cast_sentences.append(sentence)
    cast_line = "CAST — only these people are in this shot: " + (" ".join(cast_sentences) if cast_sentences else "no one is visible.")

    # ASSETS — 맨 앞 줄(설계자 2026-10-22, 뉴테이크 프롬프트 규칙). 자료를 번호로 나열한다.
    # @Video1 = 이 샷의 러프 3D 프리비즈, @Element{n}/Image{n} = 각 인물 참고 시트,
    # @Image{k} = 원작 웹툰 컷(그림체 참고, 웹툰만).
    asset_parts = ["@Video1 = rough 3D previz of this shot (blocking and camera only)"]
    asset_parts.extend(asset_refs)
    if has_image_ref and not is_real:
        # 웹툰: 그림체 참고 원작 컷은 인물 참고 다음 번호(@Image{k}).
        style_no = len([m for m in cast if m.get("imageNo")]) + 1
        asset_parts.append(f"@Image{style_no} = original webtoon panel for drawing style")
    assets_line = "ASSETS: " + "; ".join(asset_parts) + "."

    # SHOT — 촬영 지시서 카메라 문장. 없으면 최소 안내(@Video1 따름)만.
    shot_en = str(brief.get("shotEn") or "").strip()
    if not shot_en:
        shot_en = "Match the framing, lens, camera height and angle exactly as shown in @Video1."
    blocking_en = str(brief.get("blockingEn") or "").strip()
    action_en = str(brief.get("actionEn") or "").strip()
    mood_en = str(brief.get("moodEn") or "").strip()
    background_en = str(brief.get("backgroundEn") or "").strip()
    scene_en = _scene_brief_text(plan, seg).strip()

    # DIALOGUE — _cut_lines 로 귀속한 대사(색/화자 정확). 촬영 지시서 deliveryEn 을 붙인다.
    lines = _cut_lines(plan, cut)
    delivery_by_speaker: dict[str, str] = {}
    for d in (brief.get("dialogue") or []):
        if isinstance(d, dict):
            sp = str(d.get("speaker") or "").strip()
            delivery_by_speaker[sp] = str(d.get("deliveryEn") or "").strip()
    spoken = [line for line in lines if not line.get("silent") and str(line.get("lineKo") or "").strip()]
    any_spoken = bool(spoken)

    def _dialogue_line(with_delivery: bool) -> str:
        if not any_spoken:
            return "DIALOGUE: No one speaks; keep mouths closed."
        parts = []
        for line in spoken:
            speaker = str(line.get("speaker") or "").strip()
            text = str(line.get("lineKo") or "").strip()
            piece = f'{speaker or "A character"} (Korean, lips move): "{text}"'
            if with_delivery:
                delivery = delivery_by_speaker.get(speaker, "")
                if delivery:
                    piece += f" — {delivery}"
            piece += "."
            parts.append(piece)
        return "DIALOGUE: " + " ".join(parts)

    # CONTINUITY — @Video1 재현 + 인물 교체 + 임의 추가 금지 + 고퀄 실사/웹툰 마감(설계자 2026-10-22,
    # 뉴테이크 실전 프롬프트). 웹툰판은 live-action 대신 원작 웹툰 그림체로 마감한다.
    if is_real:
        finish_clause = (
            "Turn the rough 3D frame into a high-end live-action shot: natural skin texture, detailed "
            "fabric, realistic lighting and shadows, and a richly detailed real location."
        )
    else:
        finish_clause = (
            "Turn the rough 3D frame into the original webtoon's drawing style: natural skin texture, "
            "detailed fabric, realistic lighting and shadows, and a richly detailed location, all drawn "
            "in that webtoon style."
        )
    continuity = (
        "CONTINUITY: Reproduce @Video1's composition, camera angle, camera movement, positions, blocking, "
        "actions, timing and background structure as exactly as possible; mannequin stiffness is NOT "
        "acting — perform the ACTION naturally. Replace each mannequin with the referenced person, keeping "
        "face, hairstyle, body type, costume and accessories identical to the reference in every shot. "
        "The mannequin colors are only placeholders — dress each person exactly as their reference image, "
        "not in the mannequin color; no mannequins remain. Do not add any action, person or scene that is "
        "not in @Video1. " + finish_clause + " "
        "No text, no subtitles, no speech bubbles."
    )

    # SETTING = 컷별 backgroundEn + 장소 요약(sceneEn 앞 2~3문장, 최대 350자). 장소 문단 전체는 X.
    scene_summary_full = _scene_summary(scene_en, 350)

    def _setting_line(bg: str, scene_summary: str) -> str:
        parts = [p for p in (scene_summary, bg) if p]
        body = " ".join(parts).strip()
        return ("SETTING: " + body) if body else ""

    def _build(*, scene_summary: str, background: str, mood: str, blocking: str,
               with_delivery: bool, shot: str, action: str) -> str:
        shot_line = ("SHOT: " + shot) if shot else "SHOT: Match the framing, lens, camera height and angle exactly as shown in @Video1."
        blocking_line = ("BLOCKING: " + blocking) if blocking else ""
        action_line = ("ACTION: " + action) if action else ""
        mood_line = ("MOOD: " + mood) if mood else ""
        setting_line = _setting_line(background, scene_summary)
        dlg = _dialogue_line(with_delivery)
        blocks = [assets_line, head, cast_line, shot_line, blocking_line, action_line, mood_line, setting_line, dlg, continuity]
        return "\n".join(b for b in blocks if b)

    budget = vnew_settings.PREVIZ_PROMPT_TARGET  # 바이트 상한(2500).

    # 현재 값(줄이기 단계마다 하나씩 바꾼다). ACTION 첫 문장은 절대 지우지 않는다.
    state = {
        "scene_summary": scene_summary_full,
        "background": background_en,
        "mood": mood_en,
        "blocking": blocking_en,
        "with_delivery": True,
        "shot": shot_en,
        "action": action_en,
    }

    def _current() -> str:
        return _build(
            scene_summary=state["scene_summary"],
            background=state["background"],
            mood=state["mood"],
            blocking=state["blocking"],
            with_delivery=state["with_delivery"],
            shot=state["shot"],
            action=state["action"],
        )

    # 1) 한도 안이면 아무 것도 지우지 않는다(설계자 2026-10-22, 지금 버그 수정).
    prompt = _current()
    if _utf8_len(prompt) <= budget:
        return prompt

    # 줄이는 순서. 각 단계는 '넘친 만큼만' 문장 단위로 줄이고, 한도 안이면 즉시 멈춘다.
    # ① SETTING 의 장소 요약 → ② SETTING 의 backgroundEn → ③ MOOD → ④ BLOCKING 뒤쪽 문장 →
    # ⑤ DIALOGUE 의 deliveryEn → ⑥ SHOT 뒤쪽 문장. (⑦ ACTION 은 마지막에만.)
    def _reduce_field(field: str, *, keep_first: bool = False) -> bool:
        """그 필드를 넘친 만큼 문장 단위로 줄인다. 한도 안에 들면 True."""
        over = _utf8_len(_current()) - budget
        if over <= 0:
            return True
        state[field] = _drop_tail_sentences(state[field], over, keep_first=keep_first)
        return _utf8_len(_current()) <= budget

    # ① SETTING 장소 요약.
    if _reduce_field("scene_summary"):
        return _current()
    # ② SETTING backgroundEn.
    if _reduce_field("background"):
        return _current()
    # ③ MOOD.
    if _reduce_field("mood"):
        return _current()
    # ④ BLOCKING 뒤쪽 문장.
    if _reduce_field("blocking"):
        return _current()
    # ⑤ DIALOGUE deliveryEn(대사 연기 설명) 제거.
    over = _utf8_len(_current()) - budget
    if over > 0:
        state["with_delivery"] = False
        if _utf8_len(_current()) <= budget:
            return _current()
    # ⑥ SHOT 뒤쪽 문장.
    if _reduce_field("shot"):
        return _current()
    # ⑦ 마지막에만 ACTION 뒤쪽 문장(첫 문장은 절대 지우지 않는다).
    if _reduce_field("action", keep_first=True):
        return _current()
    # 그래도 넘치면 전체를 바이트로 자른다(최후). CAST·CONTINUITY 가 이미 들어간 뼈대 기준.
    return _trim_utf8(_current(), budget)


def _previz_timeline_text(plan: dict, seg: dict) -> str:
    """구간 타임라인(간결): 시간 범위 + 말하는 사람(색 마네킹)·대사 + 짧은 감정.

    화면 구성(구도·카메라·컷)은 @Video1 만 따른다 → 컷의 promptEn·cameraEn 은 넣지 않는다.
    남기는 것:
    - 시간 범위("0-4s").
    - 그 시간에 말하는 사람: 색 마네킹 기준 "the {color} mannequin ({roleName})". 대사(dialogueKo
      40자 이내)를 함께.
    - 짧은 감정 1~3단어: conti/script 의 emotion(emotionKo) 이 있으면 그것, 없으면 생략. situationKo
      에서 고르지 않는다.
    """
    cuts = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}
    # 이 구간 배우: 이름 → 색(colorName). 말하는 사람을 색 마네킹으로 가리키려고 쓴다.
    color_by_name: dict[str, str] = {}
    for actor in _segment_actors(plan, seg):
        name = str(actor.get("name") or "").strip()
        color = str(actor.get("colorName") or "").strip()
        if name and color:
            color_by_name.setdefault(name, color)
    lines = []
    start = 0.0
    for cut_id in seg.get("cutIds") or []:
        cut = cuts.get(str(cut_id))
        if not cut:
            continue
        seconds = float(cut.get("seconds") or 0)
        end = start + seconds
        dialogue = str(cut.get("dialogueKo") or "").strip()
        emotion = str(cut.get("emotionKo") or cut.get("emotionEn") or "").strip()
        speaker_name = ""
        for ch in cut.get("characters") or []:
            if ch.get("name"):
                speaker_name = str(ch.get("name"))
                break
        part = f"{int(start)}-{int(end)}s"
        if dialogue:
            color = color_by_name.get(speaker_name)
            if color:
                who = f"the {color} mannequin ({speaker_name})"
            elif speaker_name:
                who = speaker_name
            else:
                who = "the speaker"
            part += f' {who} speaks (Korean): "{dialogue[:40]}"'
        if emotion:
            part += f" ({emotion})"
        lines.append(part + ".")
        start = end
    return " ".join(lines)


def _previz_prompt(plan: dict, seg: dict, variant: str, *, element_lines: list[str], extra_lines: list[str], has_image_ref: bool) -> str:
    """프리비즈 → 그림체 프롬프트(영어, 2,500자 이내). maxLength 자르기 재사용.

    실사: "photorealistic live-action film". 웹툰: "a Korean webtoon animation in exactly the same
    drawing style as @Image1". 마네킹 색 ↔ @Element 매핑·타임라인·연속성·"Do not add or remove anyone".
    """
    is_real = variant == "real"
    location = _location_name_en(plan, seg)
    place = _previz_place_sentence(plan, seg, location)

    if is_real:
        style = "photorealistic live-action film"
    elif has_image_ref:
        style = (
            "a Korean webtoon animation in exactly the same drawing style as @Image1 "
            "(line, coloring, shading; not generic anime)"
        )
    else:
        style = "a Korean webtoon animation (hand-drawn line, coloring and shading; not generic anime)"

    head = (
        "@Video1 is a rough 3D previz with colored mannequins. Re-render it as "
        + style + " set in " + place + ". Mannequins: "
    )
    mapping = " ".join(element_lines + extra_lines)
    timeline = _previz_timeline_text(plan, seg)
    timeline_text = (" Timeline: " + timeline) if timeline else ""
    tail = (
        " Camera angles, framing, cuts, positions and movements come ONLY from @Video1 — do not invent "
        "new shots. Keep exactly the same room, number of people, timing and light direction as @Video1. "
        "Do not add or remove anyone. Every colored mannequin must become a person; no mannequins, "
        "boxes or flat colored figures may remain. No text, no speech bubbles."
    )
    prompt = head + mapping + timeline_text + tail
    budget = _previz_prompt_budget()
    if len(prompt) > budget:
        # 머리(스타일·매핑)와 꼬리(연속성 지시)는 반드시 남기고 타임라인만 줄인다.
        fixed = head + mapping + tail
        room = max(0, budget - len(fixed) - len(" Timeline: "))
        trimmed_tl = vpoc_fal._trim_prompt(timeline, room) if room > 0 else ""
        prompt = head + mapping + ((" Timeline: " + trimmed_tl) if trimmed_tl else "") + tail
        if len(prompt) > budget:
            prompt = vpoc_fal._trim_prompt(prompt, budget)
    return prompt


def _previz_place_sentence(plan: dict, seg: dict, location: str) -> str:
    """장소를 한 문장으로: 이름 + 실내/실외 + 빛 방향.

    - 이름: location(콘티 cut.locationEn).
    - 실내/실외: 대본 setting.enclosed(닫힌 방=indoor, 열린 공간=outdoor).
    - 빛 방향: 그 장소 설계(design.lightEn) 한 문장(있으면).
    """
    script_setting = (plan.get("script") or {}).get("setting") or {}
    indoor = "indoor" if script_setting.get("enclosed") else "outdoor"
    light = ""
    loc = _location_by_name(plan, location)
    if loc:
        light = str((loc.get("design") or {}).get("lightEn") or "").strip()
    sentence = f"{location} ({indoor})"
    if light:
        sentence += f", {light.rstrip('.')}"
    return sentence


def _location_by_name(plan: dict, name_en: str) -> dict | None:
    """콘티 locationEn 에 맞는 plan["locations"] 항목(key 정규화 매칭). 없으면 None."""
    from . import locations as loc_mod  # noqa: PLC0415

    key = loc_mod.normalize_name(name_en)
    for loc in plan.get("locations") or []:
        if str(loc.get("key") or loc_mod.normalize_name(loc.get("nameEn") or "")) == key:
            return loc
    return None


def _previz_prompt_budget() -> int:
    """프리비즈 edit 프롬프트 글자 상한(목표 PREVIZ_PROMPT_TARGET, 모델 maxLength 가 작으면 그 안으로).

    edit 모델은 보통 등록 레지스트리에 없다(고정 endpoint) → find 가 없으면 fal 기본 상한(2500)과
    목표값 중 작은 값을 쓴다.
    """
    budget = vnew_settings.PREVIZ_PROMPT_TARGET
    model = vpoc_settings.FAL_ID_PREFIX + str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "")
    item = vpoc_fal.find(model)
    if item:
        caps = item.get("caps") or {}
        model_max = int(caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
        budget = min(budget, model_max)
    else:
        budget = min(budget, vpoc_fal.DEFAULT_PROMPT_MAX)
    return budget


def _segment_panel_jpeg(owner: str, pid: str, plan: dict, seg: dict) -> bytes | None:
    """그 구간(장소)의 원작 웹툰 컷 1장(그림체 참고). _segment_panel_jpegs 재사용(첫 장)."""
    jpegs = _segment_panel_jpegs(owner, pid, plan, seg, 1)
    return jpegs[0] if jpegs else None


def _submit_previz_edit(*, endpoint: str = "", caps: dict | None = None, video_mp4: bytes, prompt: str, output_prefix: str, elements: list[dict], image_urls: list[bytes] | None = None) -> dict:
    """프리비즈 영상 → 그림체 변환 제출(fal video-to-video/edit, elements 지원).

    vpoc.fal.submit_edit 는 image_urls 만 받고 elements 는 못 보내므로(수정 금지), vpoc.fal._request
    를 직접 불러 elements(frontal_image_url·reference_image_urls)+image_urls 를 담은 본문을 보낸다.
    반환 모양은 submit_edit 과 같다(ARN 접두사 fal/requests/ 동일 → poll/_find_output_key 재사용).

    이미지·영상은 모두 data URI 로 보낸다(외부 저장소에 올리지 않는다, 설계자 보안 규칙).
    """
    endpoint = str(endpoint or vnew_settings.VNEW_PREVIZ_EDIT_MODEL or "").strip()
    caps = caps or {}
    if not vpoc_settings.FAL_KEY:
        raise VpocError("vnew.generate", "FalKeyMissing", "fal API 키가 설정되지 않았습니다.", 409)
    if not endpoint:
        raise VpocError("vnew.generate", "BadEndpoint", "변환 모델 endpoint 가 비어 있습니다.", 400)
    if not prompt.strip():
        raise VpocError("vnew.generate", "EmptyPrompt", "프롬프트가 비어 있습니다.", 400)
    if not video_mp4:
        raise VpocError("vnew.generate", "EmptyVideo", "변환할 영상이 비어 있습니다.", 400)

    def _img_uri(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

    # caps 로 필드 이름을 고른다(모델별). caps 가 비면(기본 Kling 미등록) 기존 고정 이름을 쓴다.
    video_field = str(caps.get("videoField") or "video_url")
    elements_field = str(caps.get("elementsField") or ("elements" if not caps else ""))
    image_list_field = str(caps.get("imageListField") or ("image_urls" if not caps else ""))
    keep_audio_field = str(caps.get("keepAudioField") or ("keep_audio" if not caps else ""))

    body: dict = {
        "prompt": prompt,
        video_field: vpoc_fal._video_data_uri(video_mp4),
    }
    if keep_audio_field:
        body[keep_audio_field] = False
    payload_elements = []
    for element in elements or []:
        front = element.get("frontal_image_url")
        if not isinstance(front, (bytes, bytearray)):
            continue
        item = {"frontal_image_url": _img_uri(bytes(front))}
        refs = [_img_uri(bytes(r)) for r in (element.get("reference_image_urls") or []) if isinstance(r, (bytes, bytearray))]
        if refs:
            item["reference_image_urls"] = refs
        payload_elements.append(item)
    if payload_elements and elements_field:
        body[elements_field] = payload_elements
    if image_urls and image_list_field:
        body[image_list_field] = [_img_uri(bytes(j)) for j in image_urls if isinstance(j, (bytes, bytearray))]

    url = f"{vpoc_settings.FAL_QUEUE_URL}/{endpoint}"
    result = _previz_request("POST", url, body)
    request_id = str(result.get("request_id") or "")
    if not request_id:
        raise VpocError("vnew.generate", "NoRequestId", f"요청 ID 가 없습니다: {str(result)[:300]}", 502)
    return {
        "provider": vpoc_settings.FAL_ID_PREFIX + endpoint,
        "invocationArn": f"{vpoc_fal.ARN_PREFIX}{endpoint}/{request_id}",
        "clientRequestToken": "fal-previz-" + request_id,
        "outputPrefix": output_prefix,
        "submittedAt": now_iso(),
        "promptChars": len(prompt),
    }


def _previz_request(method: str, url: str, payload: dict) -> dict:
    """vpoc.fal._request 로 fal 큐 제출(검사가 generate._previz_request 를 가짜로 바꿔 끼운다)."""
    return vpoc_fal._request(method, url, payload, where="vnew.generate.previz_edit")


def _charge_previz(owner: str, pid: str, gen_id: str, result: dict, seconds: float) -> None:
    """프리비즈 edit 제출 1회 계상(제출 성공 시점). 키 = 작업 토큰(중복 방지).

    모델은 result.provider("fal:<endpoint>")의 endpoint, 단가는 그 모델의 초당 단가(설계자 2026-10-22).
    """
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    provider = str(result.get("provider") or "")
    endpoint = provider.split(":", 1)[-1] if provider else str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    per_sec = _previz_model_unit_price(endpoint)
    secs = float(seconds or vpoc_settings.DURATION_S)
    try:
        vpoc_ledger.charge(
            token,
            kind="video",
            model=endpoint or vnew_settings.VNEW_PREVIZ_EDIT_MODEL,
            units=secs,
            usd=round(per_sec * secs, 4),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_previz_failed pid=%s err=%s", pid, str(exc)[:200])


# ================================================================ 개별 동작


def _claim_ok_variant(gen: dict, index: int, variant: str, claim: str) -> bool:
    try:
        cells = _previz_cells(gen) if gen.get("mode") == "previz" else (gen.get("segments") or [])
        return cells[index][variant].get("claim") == claim
    except Exception:  # noqa: BLE001
        return False


def _do_opening(owner: str, pid: str, gen_id: str, claim: str) -> None:
    """실사 시작 장면(Seedream). 성공하면 opening.status done + imageKey."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if str(gen.get("genId") or "") != gen_id or (gen.get("opening") or {}).get("claim") != claim:
            return
        inp = plan.get("input") or {}
        conti = plan.get("conti") or {}
        chars = _char_entries(owner, inp)
        prompt, refs = _opening_prompt(owner, pid, plan, chars)
        size = _opening_size(gen.get("videoModel") or "")
        image = _seedream(prompt, refs, size=size)
        key = opening_key(owner, pid, gen_id)
        STORE.put_bytes(key, image, "image/jpeg")
        _charge_seedream(owner, pid, gen_id)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id or (g.get("opening") or {}).get("claim") != claim:
                raise _Discard()
            g["opening"] = {"status": "done", "imageKey": key, "error": "", "claim": "", "claimedAt": ""}

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_opening(owner, pid, gen_id, claim, _msg(exc))


def _do_submit_real(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 제출(첫 프레임 = firstFrameKey 의 이미지)."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "real", claim):
            return
        seg = (gen.get("segments") or [])[index]
        real = seg["real"]
        attempt = int(real.get("attempt") or 1)
        first_key = str(real.get("firstFrameKey") or "")
        first_jpeg = _fetch_bytes(first_key)
        b64 = base64.b64encode(first_jpeg).decode("ascii")

        prompt = _segment_prompt(plan, seg, first_segment=(index == 0), model=str(gen.get("videoModel") or ""))
        model = str(gen.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
        resolution = str(gen.get("resolution") or "")
        prefix = _seg_output_prefix(owner, pid, gen_id, seg["id"], attempt)
        result = _submit_video(
            model=model,
            owner=owner,
            project_id=pid,
            plan_id=f"vnew-{gen_id}",
            shot_id=seg["id"],
            attempt=attempt,
            prompt=prompt,
            keyframe_jpeg_b64=b64,
            gen_id=gen_id,
            resolution=resolution,
            duration_s=int(seg.get("seconds") or 0) or None,
        )
        _charge_video(owner, pid, gen_id, result, model, resolution, int(seg.get("seconds") or 0))

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "real", claim):
                raise _Discard()
            r = (g.get("segments") or [])[index]["real"]
            r["status"] = "submitted"
            r["invocationArn"] = result.get("invocationArn") or ""
            r["outputPrefix"] = result.get("outputPrefix") or prefix
            r["error"] = ""
            # claim 은 submitted 조회 때도 쓰므로 유지한다.

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "real", claim, _msg(exc))


def _do_poll_real(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 조회. 완료면 completed + outputKey, 다음 구간 firstFrameKey 저장."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "real", claim):
            return
        seg = (gen.get("segments") or [])[index]
        real = seg["real"]
        arn = str(real.get("invocationArn") or "")
        prefix = str(real.get("outputPrefix") or "")
        state = _poll_video(arn, prefix)
        status = str(state.get("status") or "")
        if status == "InProgress":
            return  # 아직. 다음 조회에서 다시 본다.
        if status != "Completed":
            _fail_variant(owner, pid, gen_id, index, "real", claim, state.get("failureMessage") or "영상 생성 실패")
            return
        output_key = str(state.get("outputKey") or _find_output_key(prefix, arn))
        # 완료 → 마지막 프레임을 뽑아 다음 구간 firstFrameKey 로.
        next_first_key = ""
        if index + 1 < len(gen.get("segments") or []):
            mp4 = _fetch_bytes(output_key, bucket=vpoc_settings.VIDEO_BUCKET)
            last = _last_frame(mp4)
            next_first_key = first_frame_key(owner, pid, gen_id, seg["id"] + "-to-next")
            STORE.put_bytes(next_first_key, last, "image/jpeg")

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "real", claim):
                raise _Discard()
            segs = g.get("segments") or []
            r = segs[index]["real"]
            r["status"] = "completed"
            r["outputKey"] = output_key
            r["error"] = ""
            r["claim"] = ""
            r["claimedAt"] = ""
            # 다음 구간 실사의 firstFrameKey 를 채운다.
            if next_first_key and index + 1 < len(segs):
                segs[index + 1]["real"]["firstFrameKey"] = next_first_key

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "real", claim, _msg(exc))


def _do_submit_webtoon(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """실사 구간 → 웹툰 변환 제출(fal.submit_edit). 참고 이미지 = 그 구간 웹툰 패널 + 캐릭터 웹툰 시트."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "webtoon", claim):
            return
        seg = (gen.get("segments") or [])[index]
        web = seg["webtoon"]
        real = seg["real"]
        attempt = int(web.get("attempt") or 1)
        source_key = str(real.get("outputKey") or "")
        src_mp4 = _fetch_bytes(source_key, bucket=vpoc_settings.VIDEO_BUCKET)
        prepared = _prepare_anime(src_mp4)

        refs_b64 = _anime_reference_b64(owner, pid, plan, seg)
        prompt = _anime_prompt(owner, plan, len(refs_b64))
        prefix = _seg_output_prefix(owner, pid, gen_id, seg["id"] + "-webtoon", attempt)
        result = _submit_anime(
            video_mp4=prepared["bytes"],
            prompt=prompt,
            output_prefix=prefix,
            reference_jpegs_b64=refs_b64,
        )
        _charge_anime(owner, pid, gen_id, result, prepared.get("seconds") or 0)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "webtoon", claim):
                raise _Discard()
            w = (g.get("segments") or [])[index]["webtoon"]
            w["status"] = "submitted"
            w["invocationArn"] = result.get("invocationArn") or ""
            w["outputPrefix"] = result.get("outputPrefix") or prefix
            w["error"] = ""

        _commit(owner, pid, mutator)
    except stitcher.AnimeTooShort as exc:
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, str(exc))
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, _msg(exc))


def _do_poll_webtoon(owner: str, pid: str, gen_id: str, index: int, claim: str) -> None:
    """웹툰 변환 조회. 완료면 completed + outputKey."""
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        if not _claim_ok_variant(gen, index, "webtoon", claim):
            return
        seg = (gen.get("segments") or [])[index]
        web = seg["webtoon"]
        arn = str(web.get("invocationArn") or "")
        prefix = str(web.get("outputPrefix") or "")
        state = vpoc_fal.poll(arn, prefix)
        status = str(state.get("status") or "")
        if status == "InProgress":
            return
        if status != "Completed":
            _fail_variant(owner, pid, gen_id, index, "webtoon", claim, state.get("failureMessage") or "변환 실패")
            return
        output_key = _find_output_key(prefix, arn)

        def mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if not _claim_ok_variant(g, index, "webtoon", claim):
                raise _Discard()
            w = (g.get("segments") or [])[index]["webtoon"]
            w["status"] = "completed"
            w["outputKey"] = output_key
            w["error"] = ""
            w["claim"] = ""
            w["claimedAt"] = ""

        _commit(owner, pid, mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        _fail_variant(owner, pid, gen_id, index, "webtoon", claim, _msg(exc))


def _find_output_key(prefix: str, invocation_arn: str) -> str:
    """제출 결과 영상이 저장된 키. {prefix}/{request_id|task_id}/output.mp4 규칙."""
    ident = ""
    if vpoc_fal.is_fal_job(invocation_arn):
        _eid, ident = vpoc_fal._parse_arn(invocation_arn)
    elif invocation_arn.startswith(vpoc_ark.ARN_PREFIX):
        ident = invocation_arn[len(vpoc_ark.ARN_PREFIX):]
    else:
        ident = str(invocation_arn or "").rsplit("/", 1)[-1]
    if ident:
        return f"{prefix.rstrip('/')}/{ident}/output.mp4"
    return f"{prefix.rstrip('/')}/output.mp4"


# ================================================================ 병합(finalize)


def _maybe_finalize(owner: str, pid: str, gen_id: str) -> None:
    """실사·웹툰 구간 상태를 보고 병합·gen.status 를 확정한다.

    - 실사 구간이 모두 completed → finalReal concat.
    - 웹툰 구간이 모두 completed → finalWebtoon concat(웹툰 변환 실패는 실사를 막지 않는다).
    - 실사 구간 중 하나라도 failed/cancelled → gen.status partial(그 뒤 구간 미진행).
    - 실사·웹툰 다 끝나면 done.
    """
    try:
        plan, _ = plan_mod.load_plan(owner, pid)
    except VpocError:
        return
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id or gen.get("status") not in ("running",):
        return
    segments = gen.get("segments") or []
    if not segments:
        return

    real_states = [seg["real"]["status"] for seg in segments]
    web_states = [seg["webtoon"]["status"] for seg in segments]

    real_all_done = all(s == "completed" for s in real_states)
    real_failed = any(s in ("failed", "cancelled") for s in real_states)

    # 실사 병합(모두 완료, 아직 안 함).
    if real_all_done and (gen.get("finalReal") or {}).get("status") not in ("done", "running"):
        _do_concat(owner, pid, gen_id, "real")

    # 웹툰: 완료된 실사 구간에 대응하는 웹툰이 전부 끝났는지(completed/failed) 보고, 하나라도
    # completed 가 있고 아직 진행 중(submitted/preparing/queued/waiting)이 없으면 병합.
    web_pending = any(s in ("waiting", "queued", "preparing", "submitted") for s in web_states)
    web_any_done = any(s == "completed" for s in web_states)
    if real_all_done and not web_pending and web_any_done and (gen.get("finalWebtoon") or {}).get("status") not in ("done", "running"):
        _do_concat(owner, pid, gen_id, "webtoon")

    # 상태 확정.
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if str(gen.get("genId") or "") != gen_id:
        return

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or g.get("status") != "running":
            raise _Discard()
        segs = g.get("segments") or []
        reals = [s["real"]["status"] for s in segs]
        webs = [s["webtoon"]["status"] for s in segs]
        fr = g.get("finalReal") or {}
        fw = g.get("finalWebtoon") or {}
        real_done = all(x == "completed" for x in reals)
        real_fail = any(x in ("failed", "cancelled") for x in reals)
        web_pending2 = any(x in ("waiting", "queued", "preparing", "submitted") for x in webs)

        if real_fail:
            g["status"] = "partial"
            return
        if real_done and not web_pending2:
            # 실사 병합은 반드시 done 이어야(실패면 partial).
            if fr.get("status") == "done":
                g["status"] = "done"
            else:
                g["status"] = "partial"

    try:
        _, _ = plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        return


def _do_concat(owner: str, pid: str, gen_id: str, variant: str) -> None:
    """실사/웹툰 구간 영상을 순서대로 이어 붙여 final-*.mp4 로 저장한다."""
    final_field = "finalReal" if variant == "real" else "finalWebtoon"

    # running 선점(중복 병합 방지).
    picked = {"go": False}

    def claim_mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id:
            raise _Discard()
        field = g.get(final_field) or {}
        if field.get("status") in ("done", "running"):
            picked["go"] = False
            return
        field["status"] = "running"
        g[final_field] = field
        picked["go"] = True

    try:
        plan_mod.mutate_plan(owner, pid, claim_mutator)
    except _Discard:
        return
    if not picked["go"]:
        return

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        gen = plan.get("gen") or {}
        is_previz = gen.get("mode") == "previz"
        cells = _previz_cells(gen) if is_previz else (gen.get("segments") or [])
        clips = []
        for seg in cells:
            state = seg[variant]
            key = str(state.get("outputKey") or "")
            if state.get("status") == "completed" and key:
                clip_bytes = _fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET)
                # 프리비즈 컷 단위: 3초로 채워 제출한 컷은 원래 길이로 잘라서 합친다.
                if is_previz and state.get("padded"):
                    orig = float(state.get("origSeconds") or 0)
                    if orig > 0:
                        clip_bytes = _trim_clip(clip_bytes, orig)
                clips.append(clip_bytes)
        if not clips:
            raise VpocError("vnew.generate", "NoClips", "이어 붙일 영상이 없습니다.", 502)
        merged = _concat(clips)
        key = final_real_key(owner, pid, gen_id) if variant == "real" else final_webtoon_key(owner, pid, gen_id)
        STORE.put_bytes(key, merged, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)

        def done_mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id:
                raise _Discard()
            g[final_field] = {"status": "done", "key": key, "error": ""}

        _commit(owner, pid, done_mutator)
    except _Discard:
        return
    except Exception as exc:  # noqa: BLE001
        def fail_mutator(data: dict) -> None:
            g = data.get("gen") or {}
            if str(g.get("genId") or "") != gen_id:
                raise _Discard()
            g[final_field] = {"status": "failed", "key": "", "error": _msg(exc)}

        try:
            _commit(owner, pid, fail_mutator)
        except _Discard:
            return


# ================================================================ 프롬프트


def _opening_size(video_model: str) -> str:
    """시작 장면 크기. 모델 화면비에 맞춰 1920x1080(16:9) 기본."""
    return vpoc_seedream.SIZE


def _positions_text(cut: dict) -> str:
    parts = []
    for ch in cut.get("characters") or []:
        name = str(ch.get("name") or "")
        pos = str(ch.get("positionEn") or "")
        if name or pos:
            parts.append((name + " " + pos).strip())
    return ", ".join(parts) if parts else "the characters as drawn"


def _opening_prompt(owner: str, pid: str, plan: dict, chars: list[dict]) -> tuple[str, list[bytes]]:
    """1구간 실사 시작 장면 프롬프트 + 참고 이미지(첫 컷 웹툰 패널 + 등장 캐릭터 실사 정면·시트).

    프롬프트: 웹툰 패널의 구도를 그대로 실사로 재현 — 같은 장소·카메라 각도·인물 위치, 캐릭터는
    시트 속 배우와 똑같이. 자연광 영화 톤. 글자·말풍선 없음.
    """
    conti = plan.get("conti") or {}
    cuts = conti.get("cuts") or []
    segments = conti.get("segments") or []
    first_cut = cuts[0] if cuts else {}
    # 첫 구간 첫 컷의 장소·위치.
    if segments:
        first_seg = segments[0]
        cut_ids = first_seg.get("cutIds") or []
        by_id = {c["id"]: c for c in cuts}
        if cut_ids and cut_ids[0] in by_id:
            first_cut = by_id[cut_ids[0]]

    location = str(first_cut.get("locationEn") or (conti.get("setting") or {}).get("locationEn") or "the scene")
    positions = _positions_text(first_cut)

    refs: list[bytes] = []
    # Image 1 = 첫 컷 웹툰 패널. 패널 이미지는 업로드 웹툰에서 가져온다(첫 컷 panels 의 첫 패널 이미지).
    panel_jpeg = _panel_jpeg_for_cut(owner, pid, plan, first_cut)
    if panel_jpeg:
        refs.append(panel_jpeg)
    # Image 2.. = 등장 캐릭터 실사 정면·시트.
    for c in chars:
        front_key = c.get("realFrontKey") or ""
        if front_key:
            try:
                refs.append(STORE.get_bytes(front_key))
            except Exception:  # noqa: BLE001
                pass
        if len(refs) >= 1 + MAX_ANIME_REFS:
            break

    prompt = (
        "Photorealistic live-action film still that recreates the composition of the webtoon panel "
        "(Image 1): same place (" + location + ", enclosed or open as drawn), same camera angle, "
        "same character positions (" + positions + "). The characters look exactly like the actors in "
        "the character sheets (Image 2..). Natural cinematic lighting. No text, no speech bubbles."
    )
    return prompt, refs


def _panel_jpeg_for_cut(owner: str, pid: str, plan: dict, cut: dict) -> bytes | None:
    """그 컷이 맡은 웹툰 패널 이미지(업로드 웹툰 order 에서 첫 panel 인덱스에 해당하는 파일).

    panels 는 타일 인덱스일 수 있어 정확한 매핑이 어렵다 — 여기서는 order 의 첫 이미지를 쓴다
    (구도 참고용이므로 근사치면 충분하다). order 가 비면 None.
    """
    inp = plan.get("input") or {}
    order = inp.get("order") or []
    if not order:
        return None
    try:
        return STORE.get_bytes(plan_mod.upload_key(owner, pid, order[0]))
    except Exception:  # noqa: BLE001
        return None


def _cut_timeline(plan: dict, seg: dict) -> str:
    """구간의 컷들을 시간표로("0-5s: …"). 컷당 1~2문장으로 간결히(설계자 2026-10-20).

    컷마다 움직임(promptEn)·카메라·인물 행동을 짧게 한 줄. 카메라·장소가 앞 컷과 같으면 되풀이하지
    않는다(프롬프트 길이 절감). 캐릭터 외형은 여기 넣지 않는다 — 첫 프레임 이미지가 책임진다.
    """
    conti = plan.get("conti") or {}
    by_id = {c["id"]: c for c in (conti.get("cuts") or [])}
    lines = []
    start = 0
    prev_camera = ""
    prev_location = ""
    for cut_id in seg.get("cutIds") or []:
        cut = by_id.get(cut_id)
        if not cut:
            continue
        seconds = int(cut.get("seconds") or 0)
        end = start + seconds
        # 인물: 이름 + 행동만(위치·외형은 첫 프레임 이미지가 책임진다).
        actors = ", ".join(
            f"{ch.get('name', '')} {ch.get('actionEn', '')}".strip()
            for ch in (cut.get("characters") or [])
            if (ch.get("name") or ch.get("actionEn"))
        )
        dialogue = str(cut.get("dialogueKo") or "").strip()
        body = str(cut.get("promptEn") or "").strip()
        camera = str(cut.get("cameraEn") or "").strip()
        location = str(cut.get("locationEn") or "").strip()

        parts = [f"{start}-{end}s: {body}"]
        if actors:
            parts.append(actors + ".")
        # 카메라·장소는 바뀔 때만 적는다.
        if camera and camera != prev_camera:
            parts.append("Camera: " + camera + ".")
            prev_camera = camera
        if location and location != prev_location:
            parts.append("Location: " + location + ".")
            prev_location = location
        if dialogue:
            parts.append("Speaks Korean, lip movement only.")
        lines.append(" ".join(parts))
        start = end
    return "\n".join(lines)


def _appearance_text(plan: dict, owner: str) -> str:
    chars = _char_entries(owner, plan.get("input") or {})
    parts = []
    for c in chars:
        role = c.get("roleName") or c.get("name") or ""
        appearance = c.get("appearanceEn") or ""
        outfit = c.get("outfitEn") or ""
        parts.append(f"{role}: {appearance} {outfit}".strip())
    return " ".join(parts)


def _prompt_budget(model: str) -> int:
    """이 모델에 보낼 프롬프트 글자 상한. 목표는 1,800자 이하이되, fal 모델 maxLength 가 더 작으면
    그 안으로 맞춘다(설계자 2026-10-20). fal 이 아닌 모델(Luma·Seedance)은 목표 1,800 만 쓴다.
    """
    budget = SEGMENT_PROMPT_TARGET
    if model and vpoc_settings.is_fal(model):
        item = vpoc_fal.find(model) or {}
        caps = item.get("caps") or {}
        model_max = int(caps.get("promptMaxLength") or vpoc_fal.DEFAULT_PROMPT_MAX)
        budget = min(budget, model_max)
    return budget


def _segment_prompt(plan: dict, seg: dict, *, first_segment: bool, model: str = "") -> str:
    """구간 영상 프롬프트(간결, 설계자 2026-10-20). 목표 1,800자 이하 — 넘으면 시간표 뒤쪽을
    문장 경계에서 자른다(모델 maxLength 가 더 작으면 그 안으로). 첫 구간이 아니면 '앞 프레임에서
    이어진다' 첫 줄을 붙인다. 캐릭터 외형은 첫 프레임 이미지가 책임지므로 넣지 않는다.
    """
    conti = plan.get("conti") or {}
    setting = conti.get("setting") or {}
    timeline = _cut_timeline(plan, seg)
    layout = str(setting.get("layoutEn") or "")

    head = []
    if not first_segment:
        head.append(
            "Continue seamlessly from the first frame: same room, lighting, characters, positions and outfits."
        )
    else:
        head.append(
            "Start from the first frame (a live-action still) and continue the action smoothly, keeping the "
            "same place, lighting, characters, positions and outfits."
        )
    head.append("Photorealistic live-action film. No text, no speech bubbles.")
    if layout:
        head.append("Layout: " + layout + ".")
    head_text = "\n".join(head)

    budget = _prompt_budget(model)
    body = head_text + "\n" + timeline
    if len(body) > budget:
        # 머리(연속성·화풍 지시)는 반드시 남기고 시간표만 줄인다.
        room = max(0, budget - len(head_text) - 1)
        body = head_text + "\n" + vpoc_fal._trim_prompt(timeline, room)
    return body


def _anime_ref_labels(count: int) -> str:
    """참고 이미지 개수에 맞춘 "@Image1, @Image2, ..." 나열(프롬프트 {refs})."""
    return ", ".join(f"@Image{index}" for index in range(1, count + 1))


def _anime_prompt(owner: str, plan: dict, reference_count: int = 0) -> str:
    """실사 → 웹툰 변환 프롬프트(설계자 2026-10-20).

    일반 화풍(clean line art·cel shading·flat soft colors·2D anime)을 지시하지 않는다. 대신 참고
    웹툰 이미지의 선·채색·명암·색·얼굴/눈/머리 그리는 방식을 그대로 베끼라고 한다. 참고 이미지가
    있으면 그 이미지들을 가리키고, 분석된 등장 캐릭터 styleEn(지시서 029)이 있으면 "Style notes" 로
    덧붙인다. 참고 이미지가 없으면(웹툰을 안 올린 경우) 일반 "Korean webtoon style" 문장을 쓴다.
    """
    if reference_count <= 0:
        return (
            "Redraw @Video1 in a hand-drawn Korean webtoon style. Keep every character, the camera "
            "movement, composition, timing and action exactly the same as @Video1. "
            "No text, no speech bubbles."
        )
    refs = _anime_ref_labels(reference_count)
    prompt = (
        f"Redraw @Video1 in exactly the same drawing style as the reference webtoon images ({refs}): "
        "copy their line quality and thickness, coloring and shading, color palette and the way faces, "
        "eyes and hair are drawn. Do NOT use a generic anime, cartoon or glossy style. Keep every "
        "character, the camera movement, composition, timing and action exactly the same as @Video1. "
        "No text, no speech bubbles."
    )
    style_en = _first_style_en(owner, plan)
    if style_en:
        prompt += " Style notes: " + style_en
    return prompt


def _first_style_en(owner: str, plan: dict) -> str:
    """등장 캐릭터 중 첫 번째의 분석 styleEn(지시서 029). 300자 이내. 없으면 빈 문자열."""
    for item in (plan.get("input") or {}).get("characters") or []:
        char_id = str(item.get("charId") or "")
        if not char_id:
            continue
        try:
            char, _ = characters.load_char(owner, char_id)
        except Exception:  # noqa: BLE001
            continue
        style_en = str(((char or {}).get("analysis") or {}).get("styleEn") or "").strip()
        if style_en:
            return style_en[:300]
    return ""


def _segment_panel_jpegs(owner: str, pid: str, plan: dict, seg: dict, limit: int) -> list[bytes]:
    """그 구간이 맡은 웹툰 패널 이미지(업로드 원본) 최대 limit 장(설계자 2026-10-20).

    panels→업로드 파일 매핑이 정확하지 않아(타일 인덱스), 구간 순서에 맞춰 업로드 원본을 고르게
    나눠 그 구간 몫을 돌려준다(판단 어려우면 순서대로). 말풍선이 많은 장/인물이 큰 장을 가릴 수
    없으므로 순서대로 쓴다. 읽기 실패는 건너뛴다.
    """
    inp = plan.get("input") or {}
    order = list(inp.get("order") or [])
    if not order or limit <= 0:
        return []
    segments = (plan.get("conti") or {}).get("segments") or []
    seg_ids = [s.get("id") for s in segments]
    try:
        index = seg_ids.index(seg.get("id"))
    except ValueError:
        index = 0
    count = len(segments) or 1
    start = index * len(order) // count
    end = max(start + 1, (index + 1) * len(order) // count)
    file_ids = order[start:end][:limit]
    out: list[bytes] = []
    for file_id in file_ids:
        try:
            out.append(STORE.get_bytes(plan_mod.upload_key(owner, pid, file_id)))
        except Exception:  # noqa: BLE001
            continue
    return out


def _anime_reference_b64(owner: str, pid: str, plan: dict, seg: dict) -> list[str]:
    """웹툰 변환 참고 이미지(최대 4장, 설계자 2026-10-20): 원작 웹툰 패널을 먼저.

    ① 그 구간 컷들의 웹툰 이미지(업로드 원본) 최대 3장 — 순서대로.
    ② 등장 캐릭터 웹툰 시트 1장.
    원작 그림체가 기준이므로 패널을 앞에 둔다(프롬프트의 @Image1.. 순서와 같다).
    """
    refs: list[bytes] = []
    # ① 원작 웹툰 패널 먼저(최대 3장).
    refs.extend(_segment_panel_jpegs(owner, pid, plan, seg, 3))
    # ② 등장 캐릭터 웹툰 시트 1장(남는 자리 안에서).
    inp = plan.get("input") or {}
    for c in _char_entries(owner, inp):
        if len(refs) >= MAX_ANIME_REFS:
            break
        sheet_key = c.get("webtoonSheetKey") or ""
        if sheet_key:
            try:
                refs.append(STORE.get_bytes(sheet_key))
            except Exception:  # noqa: BLE001
                pass
            break  # 캐릭터 시트는 1장만.
    refs = [r for r in refs if r][:MAX_ANIME_REFS]
    return [base64.b64encode(r).decode("ascii") for r in refs]


# ================================================================ 취소 · select


def cancel_gen(owner: str, pid: str) -> dict:
    """진행 중 gen 을 취소한다(014 규칙). 진행 중 대상은 cancelled, fal/ark 취소 시도.

    Luma 는 unsupported. 반환: 취소 후 gen(없으면 빈 dict). 진행 중 gen 이 없으면 409.
    """
    # 먼저 취소 대상(submitted)의 ARN 을 모아 공급자 취소를 시도한다(저장 전에 바깥 호출).
    plan, _ = plan_mod.load_plan(owner, pid)
    gen = plan.get("gen") or {}
    if gen.get("status") not in ("running", "partial"):
        raise VpocError("vnew.generate", "NothingToCancel", "진행 중인 영상 생성이 없습니다.", 409)

    remote: dict[str, str] = {}
    cancel_cells = _previz_cells(gen) if gen.get("mode") == "previz" else (gen.get("segments") or [])
    for seg in cancel_cells:
        for variant in ("real", "webtoon"):
            state = seg.get(variant) or {}
            if state.get("status") == "submitted" and state.get("invocationArn"):
                remote[state["invocationArn"]] = _cancel_video(state["invocationArn"])

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if g.get("status") not in ("running", "partial"):
            raise VpocError("vnew.generate", "NothingToCancel", "진행 중인 영상 생성이 없습니다.", 409)
        opening = g.get("opening") or {}
        if opening.get("status") in ("waiting", "preparing"):
            opening["status"] = "cancelled"
            opening["claim"] = ""
        cells = _previz_cells(g) if g.get("mode") == "previz" else (g.get("segments") or [])
        for seg in cells:
            for variant in ("real", "webtoon"):
                state = seg.get(variant) or {}
                if state.get("status") in ("waiting", "queued", "preparing", "submitted"):
                    arn = str(state.get("invocationArn") or "")
                    state["status"] = "cancelled"
                    state["claim"] = ""
                    state["claimedAt"] = ""
                    state["note"] = "사용자가 취소했습니다"
                    if arn and arn in remote:
                        state["cancelRemote"] = remote[arn]
                    if arn:
                        state["cancelledAt"] = now_iso()
        g["status"] = "cancelled"

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    return updated.get("gen") or {}


def retry_segment(owner: str, pid: str, seg_id: str, variant: str = "real") -> dict:
    """실패한 구간부터 다시(attempt+1). variant="real" 이면 그 구간부터 실사 재개,
    variant="webtoon" 이면 그 구간 웹툰 변환만 재시도(실사 진행을 막지 않았던 것).

    gen 이 없거나 seg_id 를 못 찾으면 404. 진행 재개 후 드라이버를 깨운다.
    """
    gen_id_box = {"id": ""}

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not g:
            raise VpocError("vnew.generate", "NoGen", "영상 생성 기록이 없습니다.", 404)
        gen_id_box["id"] = str(g.get("genId") or "")
        segments = g.get("segments") or []

        # 프리비즈 모드: 그 컷(unit)·그 그림체만 다시 queued. 다른 컷은 건드리지 않는다(컷은 독립).
        if g.get("mode") == "previz":
            target = str(variant if variant in ("real", "webtoon") else "real")
            cells = _previz_cells(g)
            found = False
            for unit in cells:
                if str(unit.get("id")) != str(seg_id):
                    continue
                found = True
                st = unit[target]
                st["status"] = "queued"
                st["error"] = ""
                st["claim"] = ""
                st["claimedAt"] = ""
                st["invocationArn"] = ""
                st["outputKey"] = ""
                break
            if not found:
                raise VpocError("vnew.generate", "SegmentNotFound", "컷을 찾을 수 없습니다.", 404)
            # 그 그림체 병합만 다시(다른 그림체는 그대로).
            if target == "real":
                g["finalReal"] = {"status": "waiting", "key": "", "error": ""}
            else:
                g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
            g["status"] = "running"
            return

        found = False
        for index, seg in enumerate(segments):
            if seg.get("id") != seg_id:
                continue
            found = True
            if variant == "webtoon":
                web = seg["webtoon"]
                web["status"] = "queued"
                web["error"] = ""
                web["claim"] = ""
                web["claimedAt"] = ""
                web["invocationArn"] = ""
                web["outputKey"] = ""
            else:
                # 실사: 이 구간부터 다시. 이 구간과 그 뒤 구간의 실사를 queued 로 되돌린다.
                for later in segments[index:]:
                    r = later["real"]
                    r["status"] = "queued"
                    r["error"] = ""
                    r["claim"] = ""
                    r["claimedAt"] = ""
                    r["invocationArn"] = ""
                    r["outputKey"] = ""
                    # firstFrameKey 는 이 구간만 비운다(1구간=opening, 그 외는 앞 구간 완료 때 다시 채움).
                    if later is seg:
                        r["firstFrameKey"] = ""
                # 뒤 구간 웹툰도 함께 되돌린다(실사가 다시 만들어지므로).
                for later in segments[index:]:
                    w = later["webtoon"]
                    if w["status"] != "completed":
                        w["status"] = "waiting"
                    w["claim"] = ""
                    w["claimedAt"] = ""
            break
        if not found:
            raise VpocError("vnew.generate", "SegmentNotFound", "구간을 찾을 수 없습니다.", 404)
        # opening 이 실패해 멈춘 경우, 실사 재시도는 opening 부터 다시 그릴 수 있게 되돌린다.
        if variant != "webtoon":
            opening = g.get("opening") or {}
            if opening.get("status") in ("failed", "cancelled"):
                g["opening"] = {"status": "waiting", "imageKey": "", "error": "", "claim": "", "claimedAt": ""}
        # 병합 상태 초기화(다시 만들어야 하므로).
        g["finalReal"] = {"status": "waiting", "key": "", "error": ""}
        if variant == "webtoon":
            g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
        else:
            g["finalWebtoon"] = {"status": "waiting", "key": "", "error": ""}
        g["status"] = "running"

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    if gen_id_box["id"]:
        _spawn_driver(owner, pid, gen_id_box["id"])
    return updated.get("gen") or {}


def select_variant(owner: str, pid: str, variant: str) -> dict:
    """사용자가 실사/웹툰 중 하나를 고른다(done 일 때만). gen.selected 를 세운다."""
    if variant not in ("real", "webtoon"):
        raise VpocError("vnew.generate", "BadVariant", "실사(real) 또는 웹툰(webtoon)만 고를 수 있습니다.", 400)

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not g:
            raise VpocError("vnew.generate", "NoGen", "영상 생성 기록이 없습니다.", 404)
        if g.get("status") != "done":
            raise VpocError("vnew.generate", "NotDone", "영상 생성이 끝난 뒤에 고를 수 있습니다.", 409)
        final = g.get("finalReal") if variant == "real" else g.get("finalWebtoon")
        if (final or {}).get("status") != "done":
            raise VpocError("vnew.generate", "VariantNotReady", "그 그림체 결과가 아직 준비되지 않았습니다.", 409)
        g["selected"] = variant

    updated, _ = plan_mod.mutate_plan(owner, pid, mutator)
    return updated.get("gen") or {}


# ================================================================ 보기(gen_view)


def gen_view(owner: str, pid: str, plan: dict) -> dict | None:
    """GET /plan 의 gen 보기. 영상·이미지 presigned URL, estimate 포함. gen 이 없으면 None."""
    gen = plan.get("gen")
    est = estimate(plan)
    if not gen:
        return {"status": "none", "estimate": est}

    gen = _reconcile_gen_stale(gen)

    def _presign_image(key: str) -> str:
        return _presign(key)

    def _presign_video(key: str) -> str:
        return _presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4")

    opening = gen.get("opening") or {}
    mode = str(gen.get("mode") or "")
    # 프리비즈 모드: 구간별 프리비즈 녹화 미리보기 URL(작게 보여 준다).
    previz_recordings = (plan.get("previz") or {}).get("recordings") or {}
    segments_out = []
    for seg in gen.get("segments") or []:
        real = seg.get("real") or {}
        web = seg.get("webtoon") or {}
        rec = previz_recordings.get(str(seg.get("id") or "")) or {}
        previz_url = _presign_video(str(rec.get("key") or "")) if mode == "previz" else ""
        segments_out.append(
            {
                "id": seg.get("id"),
                "cutIds": seg.get("cutIds") or [],
                "seconds": seg.get("seconds"),
                "previzUrl": previz_url,
                "real": {
                    "status": real.get("status"),
                    "attempt": real.get("attempt"),
                    "error": real.get("error") or "",
                    "videoUrl": _presign_video(real.get("outputKey") or ""),
                },
                "webtoon": {
                    "status": web.get("status"),
                    "attempt": web.get("attempt"),
                    "error": web.get("error") or "",
                    "videoUrl": _presign_video(web.get("outputKey") or ""),
                },
            }
        )

    # 컷 단위(units): 프리비즈 미리보기 URL = 그 컷이 속한 녹화 구간 mp4. promptEn(보낸 지시서)·바이트 포함.
    def _variant_out(state: dict) -> dict:
        return {
            "status": state.get("status"),
            "attempt": state.get("attempt"),
            "error": state.get("error") or "",
            "videoUrl": _presign_video(state.get("outputKey") or ""),
            "promptEn": state.get("promptEn") or "",
            "promptBytes": state.get("promptBytes") or 0,
            "padded": bool(state.get("padded")),
        }

    units_out = []
    for unit in gen.get("units") or []:
        rec = previz_recordings.get(str(unit.get("recSegId") or "")) or {}
        previz_url = _presign_video(str(rec.get("key") or "")) if mode == "previz" else ""
        units_out.append(
            {
                "id": unit.get("id"),
                "cutId": unit.get("cutId"),
                "recSegId": unit.get("recSegId"),
                "seconds": unit.get("seconds"),
                "previzUrl": previz_url,
                "real": _variant_out(unit.get("real") or {}),
                "webtoon": _variant_out(unit.get("webtoon") or {}),
            }
        )

    final_real = gen.get("finalReal") or {}
    final_webtoon = gen.get("finalWebtoon") or {}
    return {
        "genId": gen.get("genId"),
        "mode": mode,
        "status": gen.get("status"),
        "videoModel": gen.get("videoModel"),
        "resolution": gen.get("resolution"),
        "createdAt": gen.get("createdAt"),
        "opening": {
            "status": opening.get("status"),
            "error": opening.get("error") or "",
            "imageUrl": _presign_image(opening.get("imageKey") or ""),
        },
        "segments": segments_out,
        "units": units_out,
        "finalReal": {
            "status": final_real.get("status"),
            "error": final_real.get("error") or "",
            "videoUrl": _presign_video(final_real.get("key") or ""),
        },
        "finalWebtoon": {
            "status": final_webtoon.get("status"),
            "error": final_webtoon.get("error") or "",
            "videoUrl": _presign_video(final_webtoon.get("key") or ""),
        },
        "selected": gen.get("selected"),
        "costUsd": gen.get("costUsd", 0.0),
        "estimate": est,
    }


def _reconcile_gen_stale(gen: dict) -> dict:
    """조회 때: gen 이 오래 running 이면 뷰에서 partial 로 본다(저장하지 않음)."""
    if gen.get("status") == "running" and _gen_stale(gen):
        gen = dict(gen)
        gen["status"] = "partial"
    return gen


def _gen_stale(gen: dict) -> bool:
    if gen.get("status") != "running":
        return False
    created = plan_mod._parse_iso(gen.get("createdAt") or "")
    if not created:
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() > GEN_STALL_S


# ================================================================ 원장 · presign · 저장


def _charge_seedream(owner: str, pid: str, gen_id: str) -> None:
    key = f"vnew-gen-{pid}-{gen_id}-opening-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="image",
            model=vnew_settings.SEEDREAM_MODEL,
            units=1,
            usd=float(SEEDREAM_USD),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_seedream_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_video(owner: str, pid: str, gen_id: str, result: dict, model: str, resolution: str, seconds: int) -> None:
    """영상 제출 1회 계상(제출 성공 시점). 키 = 작업 토큰(중복 방지)."""
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    try:
        ark_id = vpoc_settings.ARK_MODEL_IDS.get(model) if model else None
        vpoc_ledger.charge(
            token,
            kind="video",
            model=ark_id or vpoc_settings.VIDEO_MODEL,
            units=seconds or vpoc_settings.DURATION_S,
            usd=vpoc_settings.shot_usd(model, resolution, seconds),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_video_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_anime(owner: str, pid: str, gen_id: str, result: dict, seconds: float) -> None:
    """웹툰 변환 제출 1회 계상. 키 = 작업 토큰."""
    token = str(result.get("clientRequestToken") or "")
    if not token:
        return
    try:
        vpoc_ledger.charge(
            token,
            kind="video",
            model=vpoc_settings.VPOC_ANIME_MODEL,
            units=seconds or vpoc_settings.DURATION_S,
            usd=vpoc_settings.anime_usd(seconds or None),
            user=owner,
            project_id=pid,
            plan_id=gen_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.charge_anime_failed pid=%s err=%s", pid, str(exc)[:200])


def _presign(key: str, *, bucket: str = "", content_type: str = "") -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, bucket=bucket, content_type=content_type, expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.gen.presign_failed key=%s err=%s", key, str(exc)[:150])
        return ""


def _commit(owner: str, pid: str, mutator) -> None:
    # 계획별 락 안에서 수행한다(프리비즈 동시 제출의 갱신 유실 방지). 옛 모드는 단일 스레드라
    # 락이 비어 있어 영향이 없다.
    _mutate_locked(owner, pid, mutator)


def _fail_opening(owner: str, pid: str, gen_id: str, claim: str, message: str) -> None:
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if str(g.get("genId") or "") != gen_id or (g.get("opening") or {}).get("claim") != claim:
            raise _Discard()
        g["opening"] = {"status": "failed", "imageKey": "", "error": message, "claim": "", "claimedAt": ""}
        g["status"] = "partial"

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _user_facing_error(message: str) -> str:
    """구간 실패 메시지를 사용자용으로 바꾼다(설계자 2026-10-20).

    Seedance(BytePlus) 실존 인물 필터의 'may contain real person' 오류는 무엇을 고칠지 모를
    영어 원문이라, 모델을 바꾸라는 한국어 문구로 바꿔 저장한다. 그 밖은 원문 그대로.
    """
    low = str(message or "").lower()
    if "may contain real person" in low or "real person" in low:
        return "실사 인물 시작 이미지를 이 모델이 받지 않습니다. 모델을 바꿔 다시 만들어 주세요."
    return message


def _fail_variant(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, message: str) -> None:
    message = _user_facing_error(message)

    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        cells = _previz_cells(g) if g.get("mode") == "previz" else (g.get("segments") or [])
        state = cells[index][variant]
        state["status"] = "failed"
        state["error"] = message
        state["claim"] = ""
        state["claimedAt"] = ""
        # 옛 순차 모드: 실사 구간 실패는 그 뒤 진행을 막고 gen partial. 웹툰 실패는 실사를 막지 않는다.
        # 프리비즈 컷 단위: 컷은 서로 독립 — 한 컷 실패가 다른 컷을 막지 않는다(실패 컷만 retry).
        if variant == "real" and g.get("mode") != "previz":
            g["status"] = "partial"

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _msg(exc: BaseException) -> str:
    return exc.message if isinstance(exc, VpocError) else str(exc)[:300]


class _Discard(Exception):
    """늦게 온 결과(claim 불일치·회차 바뀜)를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""
