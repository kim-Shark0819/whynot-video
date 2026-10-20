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
from . import plan as plan_mod
from . import settings as vnew_settings

log = logging.getLogger("vpoc")

# ================================================================ 상수

# preparing(선점) 이 이 시간을 넘게 그대로면 queued 로 되돌린다(016-fix2 와 같은 15분).
PREPARE_STALE_S = 900
# gen 전체가 이 시간을 넘게 running 이면 멈춘 것으로 본다(파드 재시작 대비, 30분).
GEN_STALL_S = 1800
# 웹툰 변환 참고 이미지 최대 장수(그 구간 웹툰 패널 + 캐릭터 웹툰 시트).
MAX_ANIME_REFS = 4
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


# ================================================================ 금액(estimate)


def estimate(plan: dict) -> dict:
    """금액 미리보기(설계자 2026-10-20).

    구간별 shot_usd(model, resolution, seconds) 합 + 시작 장면 Seedream 1장(VNEW_SEEDREAM_USD)
    + 웹툰 변환 anime_usd(seconds) 합. 반환: {realUsd, openingUsd, webtoonUsd, totalUsd, segments}.
    """
    inp = plan.get("input") or {}
    conti = plan.get("conti") or {}
    video_model = str(inp.get("videoModel") or vpoc_ark.DEFAULT_MODEL)
    resolution = _resolution_for(video_model, inp)
    segments = conti.get("segments") or []

    real_usd = 0.0
    webtoon_usd = 0.0
    rows = []
    for seg in segments:
        seconds = int(seg.get("seconds") or 0)
        shot = round(vpoc_settings.shot_usd(video_model, resolution, seconds), 4)
        anime = round(vpoc_settings.anime_usd(seconds), 4)
        real_usd += shot
        webtoon_usd += anime
        rows.append({"id": seg.get("id"), "seconds": seconds, "realUsd": shot, "webtoonUsd": anime})

    opening_usd = round(float(SEEDREAM_USD), 4)
    total = round(real_usd + opening_usd + webtoon_usd, 2)
    return {
        "videoModel": video_model,
        "resolution": resolution,
        "realUsd": round(real_usd, 4),
        "openingUsd": opening_usd,
        "webtoonUsd": round(webtoon_usd, 4),
        "totalUsd": total,
        "segments": rows,
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
                "realFrontKey": str(real.get("frontKey") or ""),
                "realSheetKey": str(real.get("sheetKey") or ""),
                "webtoonSheetKey": str(webtoon.get("sheetKey") or ""),
                "webtoonFrontKey": str(webtoon.get("frontKey") or ""),
                "missing": False,
            }
        )
    return out


def check_preconditions(plan: dict, owner: str) -> list[dict]:
    """generate 선행 조건. 어기면 VpocError(409). 통과하면 캐릭터 목록을 돌려준다.

    - conti 가 ready 가 아니면 409.
    - 등장 캐릭터 중 실사 front 이 없는 캐릭터가 있으면 409(메시지에 캐릭터 이름).
    - 이미 진행 중(gen.status running, stale 아님)이면 409.
    """
    conti = plan.get("conti") or {}
    if conti.get("status") != "ready" or not (conti.get("segments")):
        raise VpocError("vnew.generate", "ContiNotReady", "먼저 콘티를 완성해 주세요.", 409)

    gen = plan.get("gen") or {}
    if gen.get("status") == "running" and not _gen_stale(gen):
        raise VpocError("vnew.generate", "GenRunning", "이미 영상 생성이 진행 중입니다.", 409)

    chars = _char_entries(owner, plan.get("input") or {})
    missing_names = [
        (c.get("roleName") or c.get("name") or c.get("charId"))
        for c in chars
        if c.get("missing") or not c.get("realFrontKey")
    ]
    if missing_names:
        raise VpocError(
            "vnew.generate",
            "CharacterRealMissing",
            "실사 캐릭터가 없는 등장인물이 있습니다: " + ", ".join(str(n) for n in missing_names),
            409,
        )
    return chars


def start_generate(owner: str, pid: str, confirm_usd: float) -> str:
    """새 genId 로 gen 을 만들고 백그라운드 진행을 시작한다. genId 를 돌려준다.

    금액이 서버 계산과 다르면 409 CostMismatch(vpoc 방식). 선행 조건 위반도 409.
    """
    plan, _ = plan_mod.load_plan(owner, pid)
    chars = check_preconditions(plan, owner)

    expected = estimate(plan)["totalUsd"]
    if round(float(confirm_usd), 2) != expected:
        raise VpocError(
            "vnew.generate",
            "CostMismatch",
            f"금액이 서버 계산과 다릅니다. 서버 {expected} USD, 화면 {round(float(confirm_usd), 2)} USD.",
            409,
        )

    new = _new_gen(plan)

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
    for seg in gen.get("segments") or []:
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


# ================================================================ 개별 동작


def _claim_ok_variant(gen: dict, index: int, variant: str, claim: str) -> bool:
    try:
        return (gen.get("segments") or [])[index][variant].get("claim") == claim
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

        prompt = _segment_prompt(plan, seg, first_segment=(index == 0))
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

        refs_b64 = _anime_reference_b64(owner, plan)
        prompt = _anime_prompt(plan)
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
        segments = gen.get("segments") or []
        clips = []
        for seg in segments:
            state = seg[variant]
            key = str(state.get("outputKey") or "")
            if state.get("status") == "completed" and key:
                clips.append(_fetch_bytes(key, bucket=vpoc_settings.VIDEO_BUCKET))
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
    """구간의 컷들을 시간표로("0-5s: …, 5-11s: …"). 컷 promptEn·cameraEn·인물·대사 상황."""
    conti = plan.get("conti") or {}
    by_id = {c["id"]: c for c in (conti.get("cuts") or [])}
    lines = []
    start = 0
    for cut_id in seg.get("cutIds") or []:
        cut = by_id.get(cut_id)
        if not cut:
            continue
        seconds = int(cut.get("seconds") or 0)
        end = start + seconds
        chars = ", ".join(
            f"{ch.get('name', '')} ({ch.get('positionEn', '')}; {ch.get('actionEn', '')})".strip()
            for ch in (cut.get("characters") or [])
        )
        dialogue = str(cut.get("dialogueKo") or "").strip()
        dialogue_note = " Character speaks (Korean dialogue), lip movement only." if dialogue else ""
        body = str(cut.get("promptEn") or "").strip()
        camera = str(cut.get("cameraEn") or "").strip()
        location = str(cut.get("locationEn") or "").strip()
        lines.append(
            f"{start}-{end}s: {body} Camera: {camera}. Location: {location}."
            + (f" Characters: {chars}." if chars else "")
            + dialogue_note
        )
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


def _segment_prompt(plan: dict, seg: dict, *, first_segment: bool) -> str:
    """구간 영상 프롬프트. 첫 구간이 아니면 '앞 프레임에서 이어진다' 첫 줄을 붙인다."""
    conti = plan.get("conti") or {}
    setting = conti.get("setting") or {}
    timeline = _cut_timeline(plan, seg)
    layout = str(setting.get("layoutEn") or "")

    head = []
    if not first_segment:
        head.append(
            "Continue seamlessly from the first frame: same room, same lighting, same characters in the "
            "same positions and outfits."
        )
    else:
        head.append(
            "Start from the first frame (a live-action still). Continue the action smoothly, keeping the "
            "same place, lighting, characters, positions and outfits."
        )
    head.append("Photorealistic live-action film. No text, no speech bubbles.")
    if layout:
        head.append("Layout: " + layout + ".")
    body = "\n".join(head) + "\n" + timeline
    return body


def _anime_prompt(plan: dict) -> str:
    """실사 → 웹툰 변환 프롬프트(016 과 같은 방식). @Video1 을 참고 이미지의 한국 웹툰 그림체로."""
    return (
        "Convert @Video1 into the exact Korean webtoon art style shown in the reference images "
        "(@Image1 and the following): clean ink line art, cel shading, the same color palette and "
        "rendering. Keep the same motion, composition, characters, positions and timing as @Video1. "
        "No text, no speech bubbles."
    )


def _anime_reference_b64(owner: str, plan: dict) -> list[str]:
    """웹툰 변환 참고 이미지(최대 4장): 등장 캐릭터 웹툰 시트.

    그 구간 웹툰 패널도 참고에 넣을 수 있으나 panels→업로드 파일 매핑이 근사치라, 여기서는
    캐릭터 웹툰 시트를 우선으로 쓴다(그림체·인물 기준). 최대 MAX_ANIME_REFS 장.
    """
    refs: list[bytes] = []
    inp = plan.get("input") or {}
    chars = _char_entries(owner, inp)
    for c in chars:
        sheet_key = c.get("webtoonSheetKey") or ""
        if sheet_key:
            try:
                refs.append(STORE.get_bytes(sheet_key))
            except Exception:  # noqa: BLE001
                pass
        if len(refs) >= MAX_ANIME_REFS:
            break
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
    for seg in gen.get("segments") or []:
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
        for seg in g.get("segments") or []:
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
    segments_out = []
    for seg in gen.get("segments") or []:
        real = seg.get("real") or {}
        web = seg.get("webtoon") or {}
        segments_out.append(
            {
                "id": seg.get("id"),
                "cutIds": seg.get("cutIds") or [],
                "seconds": seg.get("seconds"),
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

    final_real = gen.get("finalReal") or {}
    final_webtoon = gen.get("finalWebtoon") or {}
    return {
        "genId": gen.get("genId"),
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
    plan_mod.mutate_plan(owner, pid, mutator)


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


def _fail_variant(owner: str, pid: str, gen_id: str, index: int, variant: str, claim: str, message: str) -> None:
    def mutator(data: dict) -> None:
        g = data.get("gen") or {}
        if not _claim_ok_variant(g, index, variant, claim):
            raise _Discard()
        state = (g.get("segments") or [])[index][variant]
        state["status"] = "failed"
        state["error"] = message
        state["claim"] = ""
        state["claimedAt"] = ""
        # 실사 구간 실패는 그 뒤 진행을 막고 gen partial. 웹툰 실패는 실사를 막지 않는다.
        if variant == "real":
            g["status"] = "partial"

    try:
        _commit(owner, pid, mutator)
    except _Discard:
        return


def _msg(exc: BaseException) -> str:
    return exc.message if isinstance(exc, VpocError) else str(exc)[:300]


class _Discard(Exception):
    """늦게 온 결과(claim 불일치·회차 바뀜)를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""
