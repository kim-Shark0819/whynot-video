"""vpoc1 라우트 — PRD 7장. 응답 키는 7.3 · 7.4 예시와 글자까지 같게 한다.

권한 순서: 인증 → 프로젝트 가시성(보기) → 소유자(쓰기) → 본 처리.
모델 호출은 전부 202 + 조회 방식이다(ALB 유휴 60초 · gunicorn timeout 60초).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

from flask import Blueprint, jsonify, request

from . import seedream  # noqa: F401  (키 장면)
from . import ark, compose, fal, images, ledger, llm, regen, settings, store, video
from .errors import VpocError, envelope, now_iso, status_for

log = logging.getLogger("vpoc")

STEP_NAMES = {1: "입력", 2: "확인", 3: "계획 승인", 4: "영상"}

# dual(실사+애니, 016 수정 2026-10-20): mutator 안에서는 외부 호출을 하지 않는다. queued anime 를
# 보면 mutator 안에서는 "선점"(status=preparing, claim, claimedAt)만 하고, 그 선점을 아래 thread-local
# 목록에 모은다. mutate_state 가 **저장에 성공한 뒤** 호출부가 _claims_take() 로 목록을 꺼내
# _start_anime_jobs 로 실제 제출(다운로드·ffmpeg·fal.submit_edit)을 백그라운드에서 돌린다.
# mutate_state 는 저장 경합이면 mutator 를 다시 실행하므로 refresh_shots 는 매 시도 시작에서
# _claims_reset() 을 호출해 이전 시도의 선점 목록을 버린다 — 성공한 마지막 시도의 선점만 남는다.
# preparing 이 15분 넘게 그대로면(파드 재시작 등) 선점이 버려진 것으로 보고 다시 queued 로 되돌린다.
_ANIME_PREPARE_STALE_S = 15 * 60
_anime_claims = threading.local()


def _claims_reset() -> None:
    _anime_claims.items = []


def _claims_add(entry: dict) -> None:
    items = getattr(_anime_claims, "items", None)
    if items is None:
        items = []
        _anime_claims.items = items
    items.append(entry)


def _claims_take() -> list[dict]:
    """모은 선점 목록을 꺼내고 비운다(두 번 돌리지 않게)."""
    items = getattr(_anime_claims, "items", None) or []
    _anime_claims.items = []
    return list(items)


def _no_admin() -> bool:
    """기본 관리자 판별: 늘 아니다(073 X7, fail-closed). server.py 는 g.role 로 판별하는 함수를 주입한다."""
    return False


@dataclass
class VpocDependencies:
    """server.py 가 주입한다. `g` 에 의존하는 것은 전부 호출자에서 받는다."""

    require_auth: Callable
    current_username: Callable[[], str]
    get_project: Callable[[str], dict | None]
    can_read_project: Callable[[dict], bool]
    can_write_project: Callable[[dict], bool]
    graph_run: Callable[[str, dict], list]
    # 073 R10.1, X7: 관리자 판별. 주입하지 않으면 거부(_no_admin) — 기존 6개 인자 호출은 그대로 동작한다.
    is_admin: Callable[[], bool] = _no_admin


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed_s(started: str, finished: str = "") -> int:
    begin = _parse_iso(started)
    if not begin:
        return 0
    end = _parse_iso(finished) or datetime.now(timezone.utc)
    return max(0, int((end - begin).total_seconds()))


# ---------------------------------------------------------------- dual-style(실사+애니)
#
# 설계자 2026-10-20: 화풍을 고르지 않는다. 항상 실사 영상을 먼저 만들고 그 실사 영상을
# 애니(웹툰풍)로 변환해 두 결과를 함께 본다. 아래 helper 가 "생성 경로의 화풍"을 한 곳에서 정한다.


def _dual_enabled() -> bool:
    """이 배포가 dual-style 을 켰는가(설계자 2026-10-20)."""
    return bool(settings.VPOC_DUAL_STYLE_ENABLED)


def _gen_style(story_style: str) -> str:
    """생성 경로(③ 계획·그림, 영상 제출)에서 쓸 화풍.

    dual 이 켜져 있으면 ② 저장값(story.style)이 무엇이든 항상 실사(live_action)다. 꺼져 있으면
    기존대로 compose.style_of(story.style). 옛 계획(이미 ③/④ 가 있는 것)의 저장 데이터는
    바꾸지 않는다 — 이 함수는 새로 만들 때(③ 그림·영상 제출 프롬프트)만 쓰인다.
    """
    if _dual_enabled():
        return "live_action"
    return compose.style_of(story_style or "live_action")


def _anime_endpoint() -> str:
    """실사 → 애니 변환 모델 endpoint_id(settings.VPOC_ANIME_MODEL)."""
    return str(settings.VPOC_ANIME_MODEL or "").strip()


def _anime_prompt(panel_count: int, identity_count: int = 0) -> str:
    """fal video-to-video 변환 프롬프트(영어, 설계자 2026-10-20, 073 R8·design.md §7.3).

    일반 화풍(clean line art·셀 음영·평면 색·일반 애니)을 지시하지 않는다. 이미지 번호는 패널 1..P,
    인물 P+1..P+I 이고 _anime_reference_b64 가 보내는 순서(패널 먼저, 인물 그림 최대 1장)와 같다.
    - 패널 ≥1: 그 패널(@Image1..P)의 선·채색·명암·색·얼굴/눈/머리 그리는 방식을 그대로 베끼라고 한다(원작 그림체).
    - 패널 0: 일반 "2D Korean webtoon style" 앵커 문장(실사·3D 로 남지 않게).
    - 인물 ≥1: 그 이미지는 누구인지(얼굴·머리·의상)만 알려 주는 identity reference 다. 실사 사진의 질감·조명은
      베끼지 말라고 한다(dual 의 인물 그림은 실사다, O-8).
    어느 쪽이든 인물·카메라·구도·타이밍·동작은 @Video1 그대로다. 옛 호출 _anime_prompt(n) 은 패널 n 장으로 본다.
    """
    panels = max(0, int(panel_count or 0))
    identities = max(0, int(identity_count or 0))
    parts: list[str] = []
    if panels:
        refs = ", ".join(f"@Image{index}" for index in range(1, panels + 1))
        parts.append(
            f"Redraw @Video1 in exactly the same drawing style as the reference webtoon panels ({refs}): "
            "copy their line quality and thickness, coloring and shading, color palette and the way faces, "
            "eyes and hair are drawn. Do NOT use a generic anime, cartoon or glossy style."
        )
    else:
        parts.append(
            "Redraw @Video1 in a hand-drawn 2D Korean webtoon style: ink line art and webtoon coloring, "
            "not photorealistic and not 3D."
        )
    if identities:
        ids = ", ".join(f"@Image{index}" for index in range(panels + 1, panels + identities + 1))
        verb = "is" if identities == 1 else "are"
        parts.append(
            f"{ids} {verb} an identity reference only (who each person is: face, hairstyle and outfit). "
            "Do not copy its photographic look, lighting or rendering."
        )
    parts.append(
        "Keep every character, the camera movement, composition, timing and action exactly the same as "
        "@Video1. No text, no speech bubbles."
    )
    return " ".join(parts)


def _anime_output_prefix(shot_output_key: str) -> str:
    """그 샷 실사 출력(outputKey) 옆 `anime/` 접두사. fal.poll 이 여기에 결과를 복사한다.

    예) users/.../shots/g../s1-1/<id>/output.mp4 → users/.../shots/g../s1-1/<id>/anime
    """
    key = str(shot_output_key or "").rstrip("/")
    base = key.rsplit("/", 1)[0] if "/" in key else key
    return f"{base}/anime"


def create_blueprint(deps: VpocDependencies) -> Blueprint:
    bp = Blueprint("whynot_vpoc", __name__, url_prefix="/api/vpoc")

    # 앱 밖(워크스테이션)에서 이미 쓴 금액을 원장에 넣는다. 같은 키는 한 번만 계상되므로
    # 파드가 여러 개여도 중복되지 않는다. 자격 증명이나 버킷이 없는 환경(CI 임포트 스모크)에서는
    # 조용히 건너뛴다 — 라우트 등록을 막을 이유가 없다.
    if settings.MAIN_BUCKET:
        try:
            ledger.seed()
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.ledger.seed_skipped err=%s", exc)

    # ---------------------------------------------------------------- 공통

    def enveloped(where: str):
        """모든 라우트를 감싼다. 예외는 오류 봉투 + 로그 한 줄로 바뀐다(PRD 6.7)."""

        def decorator(handler):
            @wraps(handler)
            def wrapped(*args, **kwargs):
                try:
                    return handler(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    body = envelope(where, exc)
                    return jsonify({"ok": False, "error": body}), status_for(body)

            return wrapped

        return decorator

    def project_for(pid: str, *, write: bool) -> dict:
        project = deps.get_project(pid)
        if not project:
            raise VpocError("routes.project_for", "NotFound", "프로젝트를 찾을 수 없습니다.", 404)
        if not deps.can_read_project(project):
            raise VpocError("routes.project_for", "Forbidden", "이 프로젝트를 볼 수 없습니다.", 403)
        if write and not deps.can_write_project(project):
            # D-L12: 팀원은 보기만. 입력·생성·되돌리기는 소유자만.
            raise VpocError(
                "routes.project_for",
                "Forbidden",
                "이 프로젝트를 바꿀 수 있는 사람은 소유자뿐입니다.",
                403,
            )
        return project

    def owner_of(project: dict) -> str:
        """저장 경로의 주인은 프로젝트 소유자다(보는 사람이 아니다)."""
        return str(project.get("owner") or "")

    def require_idle(state: dict) -> None:
        if (state.get("task") or {}).get("status") == "running":
            raise VpocError(
                "routes.require_idle",
                "TaskRunning",
                f"'{(state['task'].get('name') or '작업')}' 이 진행 중입니다. 끝난 뒤에 다시 시도해 주세요.",
                409,
            )

    # ---------------------------------------------------------------- 보기 조립

    def _character_view(step2_char: dict, step3_char: dict, owner: str, pid: str) -> dict:
        image = dict(step3_char.get("image") or {})
        key = image.pop("key", "")
        url = None
        if key:
            try:
                url = store.STORE.presign(key, content_type="image/jpeg")
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.presign.failed key=%s err=%s", key, exc)
        image["url"] = url
        image.setdefault("status", "none")
        image.setdefault("source", None)
        image.setdefault("imageId", None)
        image.setdefault("seed", None)
        image.setdefault("error", None)
        return {"id": step3_char.get("id") or step2_char.get("id"), "image": image}

    # ------------------------------------------------ ③ 영상 설정 (설계자 2026-10-07)

    def _plan_resolution(step3: dict, model: str) -> str:
        """③에서 고른 화질. 고르지 않았거나 그 모델에 없는 값이면 모델 기본값.

        073 R4.1-4.2: fal Seedance 계열은 caps 화질(ark.video_options)과 기본 720p. 그 밖(계열이 아닌 fal 포함)은
        073 이전과 같다(VIDEO_RESOLUTIONS, 계열이 아닌 fal 은 540p, R14.3).
        """
        if settings.is_fal_seedance_family(model):
            options = ark.video_options(model)
            chosen = str(step3.get("resolution") or "")
            return chosen if chosen in options["resolutions"] else str(options["defaultResolution"])
        allowed = settings.VIDEO_RESOLUTIONS.get(model) or [settings.RESOLUTION]
        chosen = str(step3.get("resolution") or "")
        if chosen in allowed:
            return chosen
        default = settings.default_resolution(model)
        return default if default in allowed else allowed[0]

    def _shot_seconds(model: str, shot: dict) -> int:
        """샷 길이(초). 고르지 않았으면 5초, 모델 범위를 벗어나면 범위 안으로 맞춘다.

        073 R3.3·X5: ark.snap_seconds 하나로 계산한다(fal Seedance 계열은 caps 값으로 올림, 그 밖은 지금과 같다).
        """
        return ark.snap_seconds(model, shot.get("durationS"))

    def _make_mode(step2: dict, model: str, step3: dict | None = None) -> str:
        """이 계획의 제작 방식(설계자 2026-10-19).

        1) ② 저장값(single/keyframes/shots)이 있으면 그대로 — 가장 먼저 본다.
        2) step3 가 주어졌고 ③ 계획이 이미 있으면(makeMode 저장값 · shots · keyframes 중 하나라도 있으면)
           옛 계획이다 — ③ makeMode 저장값을 따르고, 없으면 keyframes 가 있으면 "keyframes" 아니면 "shots".
           10/14 이전 계획은 ② 에 makeMode 가 없어도 ③ 가 이미 만들어져 있어, 이것을 single 로 덮으면
           옛 화면·진행이 깨진다(데이터 영향 금지).
        3) 그 밖(새로 시작한 계획)은 모델 고르기가 켜진 배포면 single, 아니면 shots.
        """
        saved = str(step2.get("makeMode") or "")
        if saved in ("single", "keyframes", "shots"):
            return saved
        if step3 is not None:
            step3_saved = str(step3.get("makeMode") or "")
            has_shots = bool(step3.get("shots"))
            has_keyframes = bool(step3.get("keyframes"))
            if step3_saved in ("single", "keyframes", "shots") or has_shots or has_keyframes:
                if step3_saved in ("single", "keyframes", "shots"):
                    return step3_saved
                return "keyframes" if has_keyframes else "shots"
        if settings.MODEL_PICKER_ENABLED:
            return "single"
        return "shots"

    def _single_total(step2: dict, model: str) -> int:
        """single 영상 1개의 길이(초). ② 에서 고른 totalSeconds 를 모델 범위로 맞춘다(없으면 기본값)."""
        options = ark.video_options(model)
        low = int(options.get("minDurationS") or settings.DURATION_S)
        high = int(options.get("maxDurationS") or settings.DURATION_S)
        try:
            total = int(step2.get("totalSeconds") or 0)
        except (TypeError, ValueError):
            total = 0
        if not total:
            return settings.single_default_duration(low, high)
        return max(low, min(high, total))

    def _reference_characters(steps: dict, plan_shot: dict) -> list[tuple[str, str, str, str]]:
        """샷에 나오는 캐릭터 중 이미지가 준비된 것 → [(id, handleEn, 이미지 키, source)] (샷의 캐릭터 순서).

        이미지는 사용자가 올린 것(source=user)이 있으면 그것, 없으면 ③에서 만든 AI 이미지다
        (do_prepare 가 이미 그렇게 고른다). 둘 다 "이 캐릭터 그대로"의 기준이다.
        """
        sheets = {item.get("id"): item for item in steps["2"].get("characters") or []}
        images_by_id = {
            item.get("id"): item.get("image") or {} for item in steps["3"].get("characters") or []
        }
        refs: list[tuple[str, str, str, str]] = []
        for char_id in plan_shot.get("characters") or []:
            image = images_by_id.get(char_id) or {}
            if image.get("status") != "ready" or not image.get("key"):
                continue
            sheet = sheets.get(char_id) or {}
            handle = str(sheet.get("handleEn") or sheet.get("nameEn") or "the character").strip()
            refs.append((char_id, handle, image["key"], str(image.get("source") or "")))
        return refs

    def _style_references(steps: dict, owner: str, pid: str) -> tuple[list[str], bool]:
        """화풍 그대로(설계자 2026-10-08): ([화풍 참고 이미지 키 최대 2장], 화풍 고정 여부).

        사용자가 올린 장면 이미지가 있거나 캐릭터 이미지가 사용자 것이면 그 그림의 화풍을 따른다 —
        고정 화풍 문장("실사 영화"/"애니") 대신 "참고 이미지와 같은 화풍" 지시를 보낸다.
        """
        # 설계자 2026-10-13: 모든 샷에 앞쪽 장면 2장을 보내면 영상이 그 장면에만 머물렀다("특정 구간의
        # 장면만 보인다"). 장면 그림은 이제 샷마다 맡은 구간만 보낸다(_shot_panels). 여기서는 화풍 고정만 정한다.
        has_scenes = any(item.get("fileId") for item in steps["1"].get("sceneFiles") or [])
        user_chars = any(
            (item.get("image") or {}).get("source") in ("user", "webtoon")
            for item in steps["3"].get("characters") or []
        )
        return [], has_scenes or user_chars

    # BytePlus Seedance 2.0 r2v 는 참고 이미지 9장까지 받는다(실측 2026-10-13: 10장이면 거절).
    MAX_REFERENCE_IMAGES = 9
    # 사용자가 한 인물에 여러 장(예: 외형 · 전신)을 올리면 2장까지 보낸다. 전신 한 장만 보내면 얼굴이
    # 작아 얼굴 생김새가 흔한 얼굴로 바뀌었다(운영 2026-10-13 "사진 속 남성과 영상 속 남성이 다르다").
    PER_CHARACTER_IMAGES = 2

    def _card_keys(steps: dict, owner: str, pid: str, char_id: str) -> list[str]:
        """② 인물과 이름이 같은 ① 카드의 이미지 키 전부(올린 순서)."""
        character = next(
            (item for item in steps["2"].get("characters") or [] if item.get("id") == char_id), {}
        )
        names = {
            str(character.get(field) or "").strip().lower()
            for field in ("nameKo", "nameEn")
            if character.get(field)
        }
        for card in steps["1"].get("characters") or []:
            if str(card.get("name") or "").strip().lower() in names:
                return [
                    store.upload_key(owner, pid, item["fileId"])
                    for item in card.get("files") or []
                    if (item or {}).get("fileId")
                ]
        return []

    def _drop_appearances(steps: dict, prompt: str, char_ids: list[str]) -> str:
        """참고 이미지가 사용자 그림인 인물의 글 외모 설명(appearanceEn)을 문장에서 뺀다.

        글 설명("warm smile, strong jawline…")이 그림과 겨뤄 얼굴을 흔한 얼굴로 끌고 갔다(2026-10-13).
        생김새는 그림이 정하고, 문장에는 장소 · 행동 · 카메라만 남긴다.
        """
        sheets = {item.get("id"): item for item in steps["2"].get("characters") or []}
        for char_id in char_ids:
            text = str((sheets.get(char_id) or {}).get("appearanceEn") or "").strip().rstrip(".")
            if text:
                prompt = prompt.replace(text, "")
        prompt = re.sub(r"\s*;\s*(?=[;.]|$)", "", prompt)
        prompt = re.sub(r"(?<=[.:])\s*;\s*", " ", prompt)
        prompt = re.sub(r"\.(\s*\.)+", ".", prompt)
        prompt = re.sub(r"\s{2,}", " ", prompt)
        return prompt.strip()

    def _story_panels(steps: dict, owner: str, pid: str) -> list[list]:
        """① 장면 이미지를 올린 순서대로 펼친 장면 조각 목록 [[fileId, 조각 번호], ...].

        세로로 긴 웹툰은 분석과 같은 규칙(1,568px)으로 여러 조각이 된다. 업로드 메타의 높이로 센다.
        """
        panels: list[list] = []
        for item in steps["1"].get("sceneFiles") or []:
            file_id = item.get("fileId")
            if not file_id:
                continue
            meta, _ = store.STORE.read_json(store.upload_meta_key(owner, pid, file_id))
            count = images.panel_count(int((meta or {}).get("height") or 0))
            panels += [[file_id, index] for index in range(count)]
        return panels

    def _assign_panels(panels: list[list], shot_ids: list[str]) -> dict[str, list[list]]:
        """장면 조각을 샷 순서대로 고르게 나눈다(1번 샷 = 처음, 마지막 샷 = 끝). 샷이 더 많으면 겹친다."""
        out: dict[str, list[list]] = {}
        total, count = len(panels), len(shot_ids)
        if not total or not count:
            return {shot_id: [] for shot_id in shot_ids}
        for index, shot_id in enumerate(shot_ids):
            start = index * total // count
            end = max(start + 1, (index + 1) * total // count)
            out[shot_id] = panels[start:end]
        return out

    def _spread(items: list, limit: int) -> list:
        """처음 · 끝을 포함해 고르게 limit 개."""
        if len(items) <= limit:
            return list(items)
        if limit == 1:
            return [items[0]]
        return [items[round(index * (len(items) - 1) / (limit - 1))] for index in range(limit)]

    def _reference_bundle(
        steps: dict,
        owner: str,
        pid: str,
        plan_shot: dict,
        prompt: str,
        *,
        skip: frozenset = frozenset(),
        panels: list[list] | None = None,
        brief: str = "",
        tag_style: str = "plain",
    ) -> tuple[str, list[tuple[str | None, str]]]:
        """Seedance 로 보낼 (최종 문장, [(인물 지칭 또는 None=화풍 참고, 이미지 키)]).

        ③ 미리보기와 실제 제출이 같은 것을 쓴다. 이미지 순서 = 문장 속 "Image N" 순서.
        skip: 읽지 못해 빼는 이미지 키(제출 때만).
        tag_style: 지칭 방식(073 R5.3·R5.5, ark.tag_style). 미리보기와 제출이 같은 값을 넘긴다.
        """
        entries: list[tuple[str | None, str]] = []
        user_ids: list[str] = []
        for char_id, handle, key, source in _reference_characters(steps, plan_shot):
            keys = [key]
            if source == "webtoon":
                user_ids.append(char_id)
            if source == "user":
                user_ids.append(char_id)
                keys += [extra for extra in _card_keys(steps, owner, pid, char_id) if extra != key]
            for item in [k for k in keys if k not in skip][:PER_CHARACTER_IMAGES]:
                if len(entries) < MAX_REFERENCE_IMAGES:
                    entries.append((handle, item))
        style_keys, lock = _style_references(steps, owner, pid)
        for key in style_keys:
            if key not in skip and len(entries) < MAX_REFERENCE_IMAGES:
                entries.append((None, key))
        for file_id, index in _spread(panels or [], settings.PANELS_PER_SHOT_REFS):
            key = f"panel:{file_id}:{index}"
            if key not in skip and len(entries) < MAX_REFERENCE_IMAGES:
                entries.append((ark.PANEL_LABEL, key))
        if lock:
            style = compose.STYLE[_gen_style((steps["2"].get("story") or {}).get("style") or "")]
            prompt = ark.strip_style(prompt, style)
        prompt = _drop_appearances(steps, prompt, user_ids)
        if brief:
            prompt = f"{prompt} {brief}".strip()
        return (
            ark.reference_prompt(
                prompt, [name for name, _ in entries], style_lock=lock, tag_style=tag_style
            ),
            entries,
        )

    def _reference_b64(owner: str, pid: str, key: str, cache: dict) -> str:
        if key.startswith("panel:"):
            _, file_id, index = key.split(":", 2)
            return images.jpeg_for_reference(images.panel_jpeg(owner, pid, file_id, int(index), cache))
        return images.keyframe_base64(key)

    def _one_take(steps: dict, model: str) -> dict | None:
        """A안 "한 번에 이어서 만들기"(설계자 2026-10-08). 쓸 수 없으면 None(샷별로 만든다).

        조건: Seedance · ③ 에서 끄지 않음 · 샷 길이 합계가 모델 최대 이하 · 모든 샷에 문장이 있음.
        돌려주는 plan_shot 은 submit_shot 이 샷 하나처럼 쓴다(캐릭터 = 모든 샷의 합집합).
        """
        if not settings.MODEL_PICKER_ENABLED or not ark.is_ark(model):
            return None
        step3 = steps["3"]
        # single(영상 1개) · keyframes 는 _one_take 를 쓰지 않는다. _one_take 는 옛 샷별(shots) 전용이다.
        if step3.get("oneTake") is False or step3.get("makeMode") in ("keyframes", "single"):
            return None
        shots = step3.get("shots") or []
        if not shots or any(not shot.get("promptEn") for shot in shots):
            return None
        # 073 R3.4: 상한은 ark.one_take_max_s(ARK·fal Seedance 계열은 duration_range 최대, 그 밖은 지금 값).
        high = ark.one_take_max_s(model)
        beats = [(_shot_seconds(model, shot), shot.get("promptEn") or "") for shot in shots]
        total = sum(seconds for seconds, _ in beats)
        if total > high:
            return None
        style = compose.STYLE[_gen_style((steps["2"].get("story") or {}).get("style") or "")]
        characters: list[str] = []
        for shot in shots:
            for char_id in shot.get("characters") or []:
                if char_id not in characters:
                    characters.append(char_id)
        prompt = ark.one_take_prompt(
            [(seconds, ark.strip_style(text, style)) for seconds, text in beats], style
        )
        return {
            "id": ark.ONE_TAKE_ID,
            "characters": characters,
            "promptEn": prompt,
            "durationS": total,
        }

    def _single_take(steps: dict, model: str) -> dict | None:
        """영상 1개 한 번에(single, 설계자 2026-10-19). 쓸 수 없으면 None.

        ③ 키 장면 콘티의 구간(seg) 문장을 시간 순서 한 프롬프트로 합치고, 완성 키 장면을 시간 순서
        참고/시작·끝 이미지로 쓴다(submit_shot 이 모델별로 고른다). 돌려주는 plan_shot 은 submit_shot 이
        샷 하나처럼 쓴다. keyframes = 키 장면 id(시간 순서), keyframeTimes = 그 초.
        """
        if not settings.MODEL_PICKER_ENABLED:
            return None
        step3 = steps["3"]
        if _make_mode(steps["2"], model, steps["3"]) != "single":
            return None
        keyframes = step3.get("keyframes") or []
        if len(keyframes) < 2:
            return None
        # 구간 문장(③ 순서)을 [(시작 초, 끝 초, 문장)] 으로.
        shots = step3.get("shots") or []
        kf_by_id = {item.get("id"): item for item in keyframes}
        beats: list[tuple[float, float, str]] = []
        for shot in shots:
            kf_ids = shot.get("keyframes") or []
            a = kf_by_id.get(kf_ids[0]) if kf_ids else None
            b = kf_by_id.get(kf_ids[1]) if len(kf_ids) > 1 else None
            start = float((a or {}).get("t") or 0)
            end = float((b or {}).get("t") or start)
            beats.append((start, end, shot.get("promptEn") or ""))
        if not beats:
            return None
        style = compose.STYLE[_gen_style((steps["2"].get("story") or {}).get("style") or "")]
        prompt = ark.single_take_prompt(
            [(start, end, ark.strip_style(text, style)) for start, end, text in beats], style
        )
        characters: list[str] = []
        for item in keyframes:
            for char_id in item.get("characters") or []:
                if char_id not in characters:
                    characters.append(char_id)
        total = _single_total(steps["2"], model)
        return {
            "id": ark.ONE_TAKE_ID,
            "characters": characters,
            "promptEn": prompt,
            "durationS": total,
            # 완성 키 장면(시간 순서). submit_shot 이 모델별 이미지 입력으로 고른다.
            "keyframes": [item.get("id") for item in keyframes],
            "keyframeTimes": [float(item.get("t") or 0) for item in keyframes],
        }

    def _anime_view(shot: dict) -> dict | None:
        """④ 샷의 애니(변환) 보기(설계자 2026-10-20). 옛 회차(anime 없음)는 null.

        {status, videoUrl(서명 URL), error, note}. status 는 queued/submitted/completed/failed/cancelled.
        """
        anime = shot.get("anime")
        if not isinstance(anime, dict):
            return None
        key = anime.get("outputKey") or ""
        url = None
        if key:
            try:
                url = video.video_url(key)
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.presign.anime.failed key=%s err=%s", key, exc)
        return {
            "status": anime.get("status") or "queued",
            "videoUrl": url,
            "error": anime.get("error"),
            "note": anime.get("note"),
        }

    def _shot4_view(
        shot: dict, plan_shot: dict | None = None, gen_id: str = "", step4: dict | None = None
    ) -> dict:
        key = shot.get("outputKey") or ""
        url = None
        if key:
            try:
                url = video.video_url(key)
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.presign.video.failed key=%s err=%s", key, exc)
        return {
            "id": shot.get("id"),
            "status": shot.get("status"),
            "attempt": int(shot.get("attempt") or 1),
            "mode": shot.get("mode") or "text",
            "submittedAt": shot.get("submittedAt"),
            "completedAt": shot.get("completedAt"),
            "elapsedS": (
                _elapsed_s(shot.get("submittedAt") or "", shot.get("completedAt") or "")
                if shot.get("submittedAt")
                else 0
            ),
            "videoUrl": url,
            "note": shot.get("note"),
            "error": shot.get("error"),
            # --- 아래 네 키만 vpoc-regen 에서 추가했다. 위 10개 키는 그대로다(G1·G2). ---
            "selectedAttempt": int(
                shot.get("selectedAttempt") or shot.get("attempt") or 1
            ),
            "versions": regen.versions_view(shot, plan_shot, gen_id),
            "revision": regen.revision_view(shot),
            "sentPromptEn": shot.get("sentPromptEn"),  # single: 승인한 마스터 프롬프트 그대로 보낸 문장(설계자 2026-10-19)
            "regen": regen.regen_view(shot),
            # dual(실사+애니, 설계자 2026-10-20): 변환 결과. 옛 회차는 null.
            "anime": _anime_view(shot),
            # 073 X1: 재생성 확인 금액. 서버(regen.start_regenerate)와 같은 회차 값(steps.4 의 모델·화질 +
            # 이 샷 durationS)으로 계산한다. ② 에서 모델을 바꿨거나 ARK 가 목록에서 빠져도 409 가 나지 않는다.
            "regenUsd": (
                settings.shot_usd(
                    str(step4.get("videoModel") or ""),
                    str(step4.get("resolution") or ""),
                    shot.get("durationS"),
                )
                if step4 is not None
                else None
            ),
            # 073 R5.2: fal 이 프롬프트를 잘랐으면 {fromBytes, toBytes}(화면 E8 안내), 아니면 None.
            "promptTrimmed": shot.get("promptTrimmed"),
        }

    def plan_view(state: dict, project: dict, *, can_edit: bool) -> dict:
        owner = owner_of(project)
        pid = str(project.get("id") or "")
        steps = state["steps"]
        step2_chars = {item.get("id"): item for item in steps["2"].get("characters", [])}

        step3 = dict(steps["3"])
        step3["characters"] = [
            _character_view(step2_chars.get(item.get("id"), {}), item, owner, pid)
            for item in steps["3"].get("characters", [])
        ]
        # ③ 영상 설정(화질 · 샷 길이)은 ② 에서 고른 모델 기준이다. 금액도 그 값으로 다시 계산한다
        # (저장된 cost 는 고르기 이전 값일 수 있다). 금액은 화면에 보이지 않고 확인용으로만 간다.
        model = ark.selected(state)
        resolution = _plan_resolution(steps["3"], model)
        step3["resolution"] = resolution
        step3["keyframes"] = [_keyframe_view(item) for item in steps["3"].get("keyframes") or []]
        step3["shots"] = []
        for shot in steps["3"].get("shots", []):
            seconds = _shot_seconds(model, shot)
            row = {
                "id": shot.get("id"),
                "durationS": seconds,
                "usd": settings.shot_usd(model, resolution, seconds),
                "summaryKo": shot.get("summaryKo", ""),
                "cameraKo": shot.get("cameraKo", ""),
                "characters": shot.get("characters", []),
                "promptEn": shot.get("promptEn", ""),
                "promptChars": len(shot.get("promptEn") or ""),
                "useKeyframe": bool(shot.get("useKeyframe")),
                "reviewKo": shot.get("reviewKo"),
            }
            if shot.get("keyframes"):
                row["keyframes"] = shot["keyframes"]
            if ark.is_ark(model) and shot.get("promptEn") and not shot.get("keyframes"):
                # API 를 부르기 전에 보낼 문장을 그대로 보여 준다(설계자 2026-10-07 1-3).
                row["sendPromptEn"], entries = _reference_bundle(
                    steps, owner, pid, shot, shot.get("promptEn") or "", tag_style=ark.tag_style(model)
                )
                row["referenceCount"] = len(entries)
            step3["shots"].append(row)
        # 이 계획의 제작 방식(설계자 2026-10-19). single = 영상 1개 한 번에.
        # 옛 계획(② makeMode 없음 + ③ 가 이미 있음)을 single 로 덮지 않도록 저장된 원본 steps["3"] 을 넘긴다
        # (step3 는 view 용 복사본이라 이 시점에 shots 가 비어 있어 옛 계획 판정에 쓸 수 없다).
        make_mode = _make_mode(steps["2"], model, steps["3"])
        step3["makeMode"] = make_mode
        single_mode = make_mode == "single"
        if single_mode:
            # 영상 1개(single): 길이는 ② totalSeconds 하나이고, 금액도 그 1개 기준이다.
            options = ark.video_options(model)
            # 073 R3.6: 길이는 ③ 마스터가 정한 master.totalS(generate·제출과 같은 값). 마스터가 없으면 ② 값.
            single_total = int(
                (steps["3"].get("master") or {}).get("totalS") or _single_total(steps["2"], model)
            )
            step3["singleTotalS"] = single_total
            step3["singleMinS"] = int(options.get("minDurationS") or settings.DURATION_S)
            step3["singleMaxS"] = int(options.get("maxDurationS") or settings.DURATION_S)
        if step3.get("cost"):
            # dual(실사+애니, 설계자 2026-10-20): 변환 비용 = 영상 초 × 단가. anime_usd 는 초에 선형이라
            # 샷별 합 = 영상 1개(합계 초) 변환 비용과 같다. generate 의 confirmUsd 서버 계산과 같은 식이다.
            dual = _dual_enabled()
            if single_mode:
                single_usd = settings.shot_usd(model, resolution, single_total)
                anime_usd = round(settings.anime_usd(single_total), 2) if dual else 0.0
                step3["cost"] = {
                    **step3["cost"],
                    "model": model,
                    "resolution": resolution,
                    "durationS": single_total,
                    "priceKnown": not ark.is_ark(model)
                    or settings.ark_usd_per_sec(model, resolution) is not None,
                    "perShotUsd": single_usd,
                    "animeUsd": anime_usd,
                    "totalUsd": round(single_usd + anime_usd, 2),
                }
            else:
                total = sum(row["usd"] for row in step3["shots"])
                anime_usd = (
                    round(sum(settings.anime_usd(row["durationS"]) for row in step3["shots"]), 2)
                    if dual
                    else 0.0
                )
                step3["cost"] = {
                    **step3["cost"],
                    "model": model,
                    "resolution": resolution,
                    "durationS": sum(row["durationS"] for row in step3["shots"]),
                    "priceKnown": not ark.is_ark(model)
                    or settings.ark_usd_per_sec(model, resolution) is not None,
                    "perShotUsd": step3["shots"][0]["usd"] if step3["shots"] else 0,
                    "animeUsd": anime_usd,
                    "totalUsd": round(total + anime_usd, 2),
                }
        if single_mode:
            # 영상 1개(설계자 2026-10-19): 마스터 프롬프트 1개 + 고정 인물 + 고정 배경.
            # 최종 영상 프롬프트는 compose.master_video_prompt 하나뿐이다(미리보기 · 영상 제출이 같다).
            master = steps["3"].get("master") or {}
            step3["single"] = bool(master)
            if master:
                master_view = _master_view(master)
                step3["master"] = master_view
                step3["masterPromptEn"] = compose.master_video_prompt(
                    master, max_chars=ark.MAX_PROMPT_CHARS
                )
                step3["singlePromptEn"] = step3["masterPromptEn"]
                step3["singleReferenceCount"] = len(master_view.get("characters") or []) + (
                    1 if (master.get("background") or {}).get("imageKey") else 0
                )
        # A안(옛) 한 번에 이어서 만들기 — single 에서는 숨긴다(토글 없음).
        elif settings.MODEL_PICKER_ENABLED and ark.is_ark(model):
            take = _one_take(steps, model)
            step3["oneTake"] = steps["3"].get("oneTake") is not False
            step3["oneTakeActive"] = bool(take)
            step3["oneTakeTotalS"] = sum(row["durationS"] for row in step3["shots"])
            step3["oneTakeMaxS"] = ark.one_take_max_s(model)
            if take:
                step3["oneTakePromptEn"], entries = _reference_bundle(
                    steps, owner, pid, take, take["promptEn"], tag_style=ark.tag_style(model)
                )
                step3["oneTakeReferenceCount"] = len(entries)

        plan_shots = {item.get("id"): item for item in steps["3"].get("shots", [])}
        step4 = dict(steps["4"])
        gen_id = str(steps["4"].get("genId") or "")
        step4["shots"] = [
            _shot4_view(shot, plan_shots.get(shot.get("id")), gen_id, step4=steps["4"])
            for shot in steps["4"].get("shots", [])
        ]
        final = dict(steps["4"].get("final") or {"status": "none", "url": None, "error": None})
        if final.get("status") == "ready" and final.get("key"):
            try:
                final["url"] = store.STORE.presign(final["key"], content_type="video/mp4")
            except Exception:  # noqa: BLE001
                final["url"] = None
        final.pop("key", None)
        step4["final"] = final

        # dual(실사+애니, 설계자 2026-10-20): 애니 합본 보기. 옛 회차는 none/null.
        final_anime = dict(
            steps["4"].get("finalAnime") or {"status": "none", "url": None, "error": None}
        )
        if final_anime.get("status") == "ready" and final_anime.get("key"):
            try:
                final_anime["url"] = store.STORE.presign(
                    final_anime["key"], content_type="video/mp4"
                )
            except Exception:  # noqa: BLE001
                final_anime["url"] = None
        final_anime.pop("key", None)
        step4["finalAnime"] = final_anime

        task = dict(state.get("task") or {})
        return {
            "ok": True,
            "planId": state["planId"],
            "projectId": pid,
            "canEdit": can_edit,
            "currentStep": int(state.get("currentStep") or 1),
            "maxReachedStep": int(state.get("maxReachedStep") or 1),
            "budget": ledger.summary(),
            "task": {
                "name": task.get("name"),
                "status": task.get("status") or "idle",
                "startedAt": task.get("startedAt"),
                "finishedAt": task.get("finishedAt"),
                "error": task.get("error"),
                # vpoc-regen 추가: revise 가 어느 샷에 대한 작업인지. 다른 작업에서는 null.
                "shotId": task.get("shotId"),
                # 진행 중 작업 취소(설계자 2026-10-20): 작업 고유 id. 취소하면 null.
                "id": task.get("id"),
            },
            "regenEnabled": regen.enabled(),
            # dual(실사+애니, 설계자 2026-10-20): 켜져 있으면 프런트가 화풍 선택을 숨긴다.
            "dualStyle": _dual_enabled(),
            # ② "몇 개의 샷으로 나눌까요?" 선택지(0 = 자동).
            "shotCountRange": [settings.MIN_SHOTS, settings.MAX_SHOTS],
            # ② 영상 모델 고르기(dev 만). 꺼져 있으면 화면에 목록이 없다.
            "modelPicker": settings.MODEL_PICKER_ENABLED,
            "videoModels": ark.choices() if settings.MODEL_PICKER_ENABLED else [],
            "videoModel": model,
            # 073 R12.3: ② 저장값. videoModel(실제로 쓸 모델)과 다르면 화면이 E6 안내를 보인다.
            "videoModelRequested": steps["2"].get("videoModel"),
            # ③ 영상 설정(화질 · 샷 길이 범위). 고르기가 꺼져 있으면 Luma 값 그대로다.
            "videoOptions": ark.video_options(model),
            "steps": {
                "1": _step1_view(steps["1"], owner, pid),
                "2": steps["2"],
                "3": step3,
                "4": step4,
            },
        }

    def _file_view(owner: str, pid: str, item: dict) -> dict:
        file_id = item.get("fileId")
        url = None
        if file_id:
            try:
                url = store.STORE.presign(
                    store.upload_key(owner, pid, file_id), content_type="image/jpeg"
                )
            except Exception:  # noqa: BLE001
                url = None
        return {
            "fileId": file_id,
            "kind": item.get("kind", "scene"),
            "name": item.get("name", ""),
            "url": url,
            "thumbUrl": url,
        }

    def _step1_view(step1: dict, owner: str, pid: str) -> dict:
        out = dict(step1)
        out["characters"] = [
            {
                "name": card.get("name", ""),
                "description": card.get("description", ""),
                "files": [_file_view(owner, pid, item) for item in card.get("files") or []],
            }
            for card in step1.get("characters") or []
        ]
        out["sceneFiles"] = [_file_view(owner, pid, item) for item in step1.get("sceneFiles") or []]
        return out

    # ---------------------------------------------------------------- 작업 스레드

    def run_task(
        name: str,
        owner: str,
        pid: str,
        plan_id: str,
        worker: Callable[[], dict],
        *,
        shot_id: str = "",
    ) -> None:
        """202 로 먼저 답하고 스레드에서 실행한다.

        스레드에는 요청 컨텍스트를 넘기지 않는다. 끝날 때 단계 revision 이 바뀌었으면
        결과를 버린다 — 되돌리기가 곧 취소가 된다(PRD 6.4).
        """
        # revise 는 ③을 고치지 않는다. ③ revision 을 지켜보면 되돌리기 때 결과를 버린다.
        watch_step = {"analyze": 1, "prepare": 2, "redraw": 3, "revise": 3}.get(name, 1)

        # 이 작업 고유 id(설계자 2026-10-20). 취소하면 state["task"].id 가 바뀌므로, 스레드가 끝날 때
        # id 가 다르면 결과를 버린다 — 스레드 안 모델 호출은 멈출 수 없어(이미 보낸 요청은 끝까지 돈다)
        # 결과만 버리는 방식이다. revision 비교(되돌리기)는 그대로 유지한다.
        task_id = secrets.token_hex(6)

        def _mark_start(state: dict):
            state["task"] = {
                "name": name,
                "status": "running",
                "startedAt": now_iso(),
                "finishedAt": None,
                "error": None,
                "shotId": shot_id or None,
                "id": task_id,
            }
            return store.revision_of(state, watch_step)

        _, revision = store.mutate_state(owner, pid, plan_id, _mark_start)

        def body() -> None:
            try:
                result = worker()
                error = None
            except Exception as exc:  # noqa: BLE001
                result = {}
                error = envelope(f"task.{name}", exc)

            def _finish(state: dict):
                # 되돌리기(revision 변경) 또는 취소(task id 변경, 취소는 id 를 None 으로 바꾼다) 중
                # 하나라도 맞으면 결과를 버린다. 우리가 시작한 작업의 task_id 는 항상 non-None 이므로,
                # 현재 task.id 가 None(취소·리셋)이거나 다른 id(다른 작업)면 불일치로 버린다.
                current_id = (state.get("task") or {}).get("id")
                discard = store.revision_of(state, watch_step) != revision or current_id != task_id
                if discard:
                    log.info("vpoc.task.discarded name=%s plan=%s", name, plan_id)
                    # 취소·다른 작업이 현재 task 를 들고 있으면(현재 id 가 내 id 가 아님) 그 기록을
                    # 지우지 않는다(취소 상태를 보존). 되돌리기로 버린 경우(현재 id 가 아직 내 것)만 idle 로.
                    if current_id != task_id:
                        return
                    state["task"] = {
                        "name": None,
                        "status": "idle",
                        "startedAt": None,
                        "finishedAt": now_iso(),
                        "error": None,
                        "shotId": None,
                        "id": None,
                    }
                    return
                state["task"] = {
                    "name": name,
                    "status": "error" if error else "done",
                    "startedAt": (state.get("task") or {}).get("startedAt"),
                    "finishedAt": now_iso(),
                    "error": error,
                    "shotId": shot_id or None,
                    "id": task_id,
                }
                if not error:
                    apply_result(state, name, result)

            try:
                store.mutate_state(owner, pid, plan_id, _finish)
            except Exception as exc:  # noqa: BLE001
                log.error("vpoc.task.save_failed name=%s plan=%s err=%s", name, plan_id, exc)

        threading.Thread(target=body, name=f"vpoc-{name}-{plan_id}", daemon=True).start()

    def apply_result(state: dict, name: str, result: dict) -> None:
        if name == "analyze":
            step2 = state["steps"]["2"]
            step2.update(
                {
                    "status": "ready",
                    "story": result["story"],
                    "characters": result["characters"],
                    "questions": result["questions"],
                    "answers": result["answers"],
                }
            )
            step2.setdefault("extraKo", "")
            state["currentStep"] = 2
            state["maxReachedStep"] = max(int(state.get("maxReachedStep") or 1), 2)
        elif name == "prepare":
            step3 = state["steps"]["3"]
            step3.update(
                {
                    "status": "ready",
                    "characters": result["characters"],
                    "shots": result["shots"],
                    "cost": result["cost"],
                    "useKeyframes": bool(result.get("useKeyframes")),
                    "makeMode": result.get("makeMode") or "shots",
                    "keyframes": result.get("keyframes") or [],
                }
            )
            # 영상 1개(single)는 마스터 데이터를 함께 넣는다(설계자 2026-10-19). 아니면 옛 master 를 지운다.
            if result.get("master"):
                step3["master"] = result["master"]
            else:
                step3.pop("master", None)
            state["currentStep"] = 3
            state["maxReachedStep"] = max(int(state.get("maxReachedStep") or 1), 3)
        elif name == "redraw":
            by_id = {item["id"]: item for item in state["steps"]["3"].get("characters", [])}
            for item in result.get("characters", []):
                by_id[item["id"]] = item
            state["steps"]["3"]["characters"] = list(by_id.values())
            # single: 인물 사진을 다시 그렸으면 master.characters[].imageKey 도 갱신한다(설계자 2026-10-19).
            master = state["steps"]["3"].get("master")
            if master and result.get("characters"):
                new_keys = {
                    item["id"]: (item.get("image") or {}).get("key") or ""
                    for item in result["characters"]
                }
                for mchar in master.get("characters") or []:
                    if mchar.get("id") in new_keys and new_keys[mchar["id"]]:
                        mchar["imageKey"] = new_keys[mchar["id"]]
                # 그 인물이 나오는 비트 미리보기는 stale(사진이 바뀌었으니 다시 그려야 맞다).
                _mark_master_stale(state, char_ids=list(new_keys.keys()))
            # 배경 그림을 다시 그렸으면(redraw target=background) master.background 를 갱신한다.
            if master and result.get("background"):
                master["background"].update(result["background"])
            if result.get("keyframes"):
                kf_by_id = {item["id"]: item for item in state["steps"]["3"].get("keyframes") or []}
                for item in result["keyframes"]:
                    kf_by_id[item["id"]] = item
                state["steps"]["3"]["keyframes"] = [
                    kf_by_id[item["id"]] for item in state["steps"]["3"].get("keyframes") or []
                ]
        elif name == "revise":
            regen.apply_revision(state, result)

    def _repair_review_shots(state: dict) -> bool:
        """이미 '검토 필요'로 저장된 ③ 샷 중 길이만 넘은 것을 고친다(모델 호출 없음, 설계자 2026-10-13)."""
        step3 = state["steps"]["3"]
        if not any(shot.get("reviewKo") for shot in step3.get("shots") or []):
            return False
        step2 = state["steps"]["2"]
        characters = step2.get("characters") or []
        style = _gen_style((step2.get("story") or {}).get("style") or "live_action")
        changed = False
        for index, shot in enumerate(step3.get("shots") or []):
            if not shot.get("reviewKo"):
                continue
            fixed = compose.fit_shot(shot)
            if compose.check_shot(fixed, characters, style):
                continue
            fixed["promptEn"] = compose.build_prompt(fixed, characters, style)
            fixed["reviewKo"] = None
            step3["shots"][index] = fixed
            changed = True
        return changed

    def stall_check(state: dict) -> bool:
        """running 이 10분을 넘으면 조회 때 error 로 바꾼다(E11)."""
        task = state.get("task") or {}
        if task.get("status") != "running":
            return False
        if _elapsed_s(task.get("startedAt") or "") < settings.TASK_STALL_S:
            return False
        task["status"] = "error"
        task["finishedAt"] = now_iso()
        task["error"] = {
            "where": f"task.{task.get('name')}",
            "type": "TaskStalled",
            "message": "중단됨: 파드 재시작 추정. 다시 실행해 주세요.",
            "requestId": "",
            "httpStatus": 500,
            "at": now_iso(),
        }
        return True

    # ---------------------------------------------------------------- 작업 본체

    def do_analyze(owner: str, pid: str, plan_id: str, user: str, step1: dict) -> dict:
        file_ids: list[str] = []
        for card in step1.get("characters") or []:
            file_ids.extend(item.get("fileId") for item in card.get("files") or [] if item.get("fileId"))
        file_ids.extend(
            item.get("fileId") for item in step1.get("sceneFiles") or [] if item.get("fileId")
        )
        jpegs, total = images.load_analysis_images(owner, pid, file_ids)
        result, usage = llm.analyze(step1, jpegs, total_images=total)
        ledger.charge_text(
            f"analyze-{plan_id}-{secrets.token_hex(3)}",
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            user=user,
            project_id=pid,
            plan_id=plan_id,
            model=str(usage.get("model") or ""),
        )
        if total > len(jpegs):
            result["story"]["notesKo"] = (
                f"이미지 {total}장 중 {len(jpegs)}장만 분석했습니다. "
                + (result["story"].get("notesKo") or "")
            ).strip()
        return result

    def _card_image(step1: dict | None, character: dict) -> dict | None:
        """① 캐릭터 카드 중 이름이 같은 카드의 첫 이미지(설계자 2026-10-08).

        화면이 ② userImages 를 보내지 않아, 사용자가 캐릭터 이미지를 올려도 ③ 에서 AI 가 다른 화풍으로
        다시 그렸다. 카드 이름이 ② 인물의 nameKo/nameEn 과 같으면 그 이미지를 캐릭터 이미지로 쓴다.
        """
        names = {
            str(character.get(field) or "").strip().lower()
            for field in ("nameKo", "nameEn")
            if character.get(field)
        }
        for card in (step1 or {}).get("characters") or []:
            if str(card.get("name") or "").strip().lower() in names and card.get("files"):
                file_id = (card["files"][0] or {}).get("fileId")
                if file_id:
                    return {"fileId": file_id}
        return None

    def do_prepare(
        owner: str, pid: str, plan_id: str, user: str, step2: dict, *, step1: dict | None = None
    ) -> dict:
        style = _gen_style((step2.get("story") or {}).get("style") or "live_action")
        characters = step2.get("characters") or []
        story = step2.get("story") or {}
        user_images = dict(step2.get("userImages") or {})
        for character in characters:
            if character.get("id") and character["id"] not in user_images:
                found = _card_image(step1, character)
                if found:
                    user_images[character["id"]] = found

        # 캐릭터 이미지와 샷 계획을 함께 돌린다(동시 최대 3장).
        image_results: dict[str, dict] = {}
        lock = threading.Lock()

        def make_image(character: dict) -> None:
            char_id = character["id"]
            existing = user_images.get(char_id)
            if existing:
                # 사용자가 올린 이미지가 있으면 생성하지 않고 그것을 쓴다.
                with lock:
                    image_results[char_id] = {
                        "status": "ready",
                        "source": "user",
                        "imageId": existing.get("fileId"),
                        "key": store.upload_key(owner, pid, existing["fileId"]),
                        "seed": None,
                        "error": None,
                    }
                return
            if style == "webtoon":
                try:
                    made = _draw_from_webtoon(owner, pid, plan_id, user, step1 or {}, character)
                    with lock:
                        image_results[char_id] = {
                            "status": "ready",
                            "source": "webtoon",
                            "imageId": made["imageId"],
                            "key": made["key"],
                            "seed": None,
                            "error": None,
                        }
                    return
                except Exception as exc:  # noqa: BLE001 — 안 되면 아래 일반 그림으로
                    log.warning("vpoc.webtoon_char.failed char=%s err=%s", char_id, exc)
            try:
                made = _draw_character(owner, pid, plan_id, user, character, story, style)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    image_results[char_id] = {
                        "status": "error",
                        "source": "ai",
                        "imageId": None,
                        "seed": None,
                        "error": envelope("stability.invoke_model", exc),
                    }
                return
            with lock:
                image_results[char_id] = {
                    "status": "ready",
                    "source": "ai",
                    "imageId": made["imageId"],
                    "key": made["key"],
                    "seed": made["seed"],
                    "error": None,
                }

        plan_box: dict = {}

        # 설계자 2026-10-19: 모델 고르기가 켜진 Seedance/fal 모델은 영상 1개(single)가 기본·유일이다.
        # single = 마스터 프롬프트(_build_master). keyframes = 옛 키 장면 콘티(_make_storyboard).
        # shots(옛)만 샷별 계획이다.
        make_mode = _make_mode(step2, ark.plan_model(step2))
        board_mode = make_mode == "keyframes"
        master_mode = make_mode == "single"

        def make_plan() -> None:
            try:
                _make_plan_inner()
            except Exception as exc:  # noqa: BLE001 — 원인을 본 스레드로 넘긴다(PlanMissing 로 가려졌다)
                plan_box["error"] = exc

        def _make_plan_inner() -> None:
            if board_mode:
                plan_box["board"] = _make_storyboard(
                    owner, pid, plan_id, user, step1 or {}, step2, make_mode=make_mode
                )
                return
            payload, usage = llm.plan(step2)
            ledger.charge_text(
                f"plan-{plan_id}-{secrets.token_hex(3)}",
                input_tokens=int(usage.get("inputTokens") or 0),
                output_tokens=int(usage.get("outputTokens") or 0),
                user=user,
                project_id=pid,
                plan_id=plan_id,
            )
            plan_box["payload"] = payload

        # single 은 미리보기가 인물 사진 · 배경을 참고하므로 인물 그림이 끝난 뒤에 마스터를 만든다
        # (병렬 계획 스레드를 쓰지 않는다). keyframes/shots 는 기존대로 병렬로 돌린다.
        plan_thread = None
        if not master_mode:
            plan_thread = threading.Thread(target=make_plan, name=f"vpoc-plan-{plan_id}")
            plan_thread.start()

        pending = [item for item in characters]
        while pending:
            batch = pending[:3]
            pending = pending[3:]
            threads = [
                threading.Thread(target=make_image, args=(item,), name=f"vpoc-img-{item['id']}")
                for item in batch
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        if master_mode:
            # 인물 그림이 준비된 뒤 마스터(배경 그림 + 미리보기 콘티)를 만든다.
            board = _build_master(
                owner, pid, plan_id, user, step1 or {}, step2, image_results
            )
            return {
                "characters": [
                    {"id": item["id"], "image": image_results.get(item["id"], {"status": "none"})}
                    for item in characters
                ],
                "shots": board["shots"],
                "keyframes": board["keyframes"],
                "master": board["master"],
                "makeMode": make_mode,
                "useKeyframes": False,
                "cost": {"shots": len(board["shots"])},
            }

        if plan_thread is not None:
            plan_thread.join()
        if plan_box.get("error") is not None:
            raise plan_box["error"]
        if board_mode:
            board = plan_box.get("board")
            if not board:
                raise VpocError("claude.storyboard", "PlanMissing", "키 장면 콘티를 받지 못했습니다.", 502)
            return {
                "characters": [
                    {"id": item["id"], "image": image_results.get(item["id"], {"status": "none"})}
                    for item in characters
                ],
                "shots": board["shots"],
                "keyframes": board["keyframes"],
                "makeMode": make_mode,
                "useKeyframes": False,
                "cost": {"shots": len(board["shots"])},
            }
        payload = plan_box.get("payload")
        if payload is None:
            raise VpocError("claude.plan", "PlanMissing", "샷 계획을 받지 못했습니다.", 502)

        shots = []
        for shot in payload.get("shots") or []:
            shot = compose.fit_shot(shot)
            problems = compose.check_shot(shot, characters, style)
            row = {
                "id": shot.get("id"),
                "summaryKo": shot.get("summaryKo", ""),
                "cameraKo": shot.get("cameraKo", ""),
                "cameraEn": shot.get("cameraEn", ""),
                "locationEn": shot.get("locationEn", ""),
                "actionEn": shot.get("actionEn", ""),
                "characters": shot.get("characters") or [],
                "useKeyframe": False,
                "reviewKo": None,
                "promptEn": "",
            }
            if problems:
                row["reviewKo"] = "검토 필요: " + " / ".join(problems[:4])
            else:
                row["promptEn"] = compose.build_prompt(shot, characters, style)
            shots.append(row)

        shot_count = len(shots)
        return {
            "characters": [
                {"id": item["id"], "image": image_results.get(item["id"], {"status": "none"})}
                for item in characters
            ],
            "shots": shots,
            "useKeyframes": False,
            "cost": {
                "shots": shot_count,
                "durationS": settings.DURATION_S,
                "resolution": settings.RESOLUTION,
                "perShotUsd": settings.shot_usd(),
                "totalUsd": round(settings.shot_usd() * shot_count, 2),
            },
        }

    # ---------------------------------------------------------------- 키 장면(콘티) 방식

    def _keyframe_view(item: dict) -> dict:
        view = {key: value for key, value in item.items() if key not in ("rough", "final")}
        for kind in ("rough", "final"):
            image = dict(item.get(kind) or {})
            if image.get("key"):
                try:
                    image["url"] = store.STORE.presign(image["key"], content_type="image/jpeg")
                except Exception:  # noqa: BLE001
                    image["url"] = None
            image.pop("key", None)
            view[kind] = image
        return view

    def _master_view(master: dict) -> dict:
        """영상 1개(single) 마스터를 화면용으로. 이미지 키는 presigned imageUrl 로 바꾼다(설계자 2026-10-19).

        background.imageKey · characters[].imageKey 를 imageUrl 로 옮기고, promptEn 등 나머지는 그대로 둔다.
        """
        def _url(key: str) -> str | None:
            if not key:
                return None
            try:
                return store.STORE.presign(key, content_type="image/jpeg")
            except Exception:  # noqa: BLE001
                return None

        view = dict(master or {})
        background = dict(master.get("background") or {})
        background["imageUrl"] = _url(background.get("imageKey") or "")
        view["background"] = background
        view["characters"] = []
        for item in master.get("characters") or []:
            entry = dict(item)
            entry["imageUrl"] = _url(entry.get("imageKey") or "")
            view["characters"].append(entry)
        return view

    def _draw_rough(owner: str, pid: str, plan_id: str, user: str, keyframe: dict, handles: dict) -> dict:
        """러프 콘티 1장(Seedream Flash, 연필 스케치)."""
        scene = keyframe.get("sceneEn") or ""
        prompt = (
            "Rough animation storyboard keyframe, loose black pencil sketch lines on white paper, simple shapes, "
            "no color, no text, no speech bubbles, 16:9 frame. Characters: "
            + (", ".join(handles.get(cid, cid) for cid in keyframe.get("characters") or []) or "none")
            + ". Scene: "
            + scene
        )
        try:
            jpeg = seedream.generate_safe(prompt, None, model=settings.SEEDREAM_ROUGH_MODEL, where="seedream.rough")
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": envelope("seedream.rough", exc)}
        image_id = secrets.token_hex(4)
        key = store.keyframe_key(owner, pid, plan_id, keyframe["id"], "rough", image_id)
        store.STORE.put_bytes(key, jpeg, "image/jpeg")
        ledger.charge_seedream(
            f"kf-rough-{plan_id}-{keyframe['id']}-{image_id}",
            model=settings.SEEDREAM_ROUGH_MODEL, user=user, project_id=pid, plan_id=plan_id,
        )
        return {"status": "ready", "key": key, "imageId": image_id, "error": None}

    def _make_storyboard(
        owner: str, pid: str, plan_id: str, user: str, step1: dict, step2: dict, *, make_mode: str = "keyframes"
    ) -> dict:
        """웹툰 전체 → 키 장면 + 구간 움직임 → 러프 콘티 그림.

        keyframes(옛): 5초 간격, 키 장면 수 = total//5 + 1.
        single(설계자 2026-10-19): 영상 1개를 위한 키 장면 수 = clamp(ceil(초/5)+1, 2, 4),
        간격 = 초/(수-1)(실수). t 는 그 간격으로 정한다(마지막 키 장면 = total 초).
        """
        steps_view = {"1": step1, "2": step2, "3": {}}
        # 073 R3.7: ② 저장값이 아니라 실제로 쓸 모델(resolve_model)의 범위로 길이를 정한다.
        model = ark.plan_model(step2)
        if make_mode == "single":
            total = _single_total(step2, model)
            count = settings.single_keyframe_count(total)
            interval = settings.single_interval_s(total)  # 실수
            # 키 장면의 초. 마지막은 정확히 total 로 맞춘다(실수 반올림 오차 방지).
            times = [round(index * interval, 2) for index in range(count)]
            times[-1] = float(total)
        else:
            total = int(step2.get("totalSeconds") or settings.KEYFRAME_TOTAL_DEFAULT)
            interval = settings.KEYFRAME_INTERVAL_S
            count = total // interval + 1
            times = [index * interval for index in range(count)]
        panels = _spread(_story_panels(steps_view, owner, pid), 12)
        cache: dict = {}
        panel_bytes = [images.panel_jpeg(owner, pid, f, i, cache) for f, i in panels]
        characters = step2.get("characters") or []
        story = step2.get("story") or {}
        story_text = " ".join(
            str(story.get(field) or "") for field in ("titleKo", "loglineKo", "settingKo")
        ).strip() or str(step1.get("text") or "")
        board, usage = llm.storyboard(
            panel_bytes, characters, story_text=story_text, total_s=total,
            interval_s=max(1, int(round(interval))), count=count,
        )
        ledger.charge_text(
            f"board-{plan_id}-{secrets.token_hex(3)}",
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            user=user, project_id=pid, plan_id=plan_id, model=str(usage.get("model") or ""),
        )
        known = {item.get("id") for item in characters}
        handles = {item.get("id"): item.get("handleEn") or item.get("nameEn") or item.get("id") for item in characters}
        keyframes = []
        for index, item in enumerate(board["keyframes"]):
            t_value = times[index] if index < len(times) else times[-1]
            keyframes.append(
                {
                    "id": f"kf_{index:02d}",
                    "t": t_value,
                    "summaryKo": str(item.get("summaryKo") or "")[:80],
                    "sceneEn": str(item.get("sceneEn") or "")[:600],
                    "characters": [cid for cid in item.get("characters") or [] if cid in known],
                    "rough": {"status": "none"},
                    "final": {"status": "none"},
                }
            )
        results: dict = {}

        def draw(item: dict) -> None:
            results[item["id"]] = _draw_rough(owner, pid, plan_id, user, item, handles)

        workers = [threading.Thread(target=draw, args=(item,), daemon=True) for item in keyframes]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=240)
        for item in keyframes:
            item["rough"] = results.get(item["id"]) or {"status": "error", "error": {"message": "시간 초과"}}
        shots = []
        for index, segment in enumerate(board["segments"]):
            a, b = keyframes[index], keyframes[index + 1]
            cast = []
            for cid in a["characters"] + b["characters"]:
                if cid not in cast:
                    cast.append(cid)
            shots.append(
                {
                    "id": f"seg_{index + 1:02d}",
                    "summaryKo": f"{a['t']}~{b['t']}초 · " + str(segment.get("summaryKo") or "")[:70],
                    "cameraKo": "",
                    "cameraEn": "",
                    "locationEn": "",
                    "actionEn": "",
                    "characters": cast[:4],
                    "useKeyframe": False,
                    "reviewKo": None,
                    "promptEn": str(segment.get("motionEn") or "")[:900],
                    # 구간 길이 = 두 키 장면 사이 초(single 은 실수 간격, keyframes 는 5초).
                    "durationS": round(float(b["t"]) - float(a["t"]), 2),
                    "keyframes": [a["id"], b["id"]],
                }
            )
        return {"keyframes": keyframes, "shots": shots}

    def _draw_final(owner: str, pid: str, plan_id: str, user: str, steps: dict, keyframe: dict) -> dict:
        """완성 키 장면(Seedream Pro): 러프 구도 + 인물 그림 + 웹툰 화풍."""
        rough_key = (keyframe.get("rough") or {}).get("key")
        refs: list[bytes] = []
        labels: list[str] = []
        if rough_key:
            refs.append(store.STORE.get_bytes(rough_key))
            labels.append(f"Image {len(refs)} is the rough storyboard sketch: copy its composition, poses and camera")
        sheets = {item.get("id"): item for item in steps["2"].get("characters") or []}
        for cid in keyframe.get("characters") or []:
            handle = (sheets.get(cid) or {}).get("handleEn") or cid
            keys = []
            image = next(
                ((item.get("image") or {}) for item in steps["3"].get("characters") or [] if item.get("id") == cid),
                {},
            )
            if image.get("source") == "user":
                keys = _card_keys(steps, owner, pid, cid)[:2]
            if not keys and image.get("key"):
                keys = [image["key"]]
            for key in keys:
                try:
                    refs.append(store.STORE.get_bytes(key))
                    labels.append(f"Image {len(refs)} is {handle}")
                except Exception:  # noqa: BLE001
                    continue
        cache: dict = {}
        all_panels = _story_panels(steps, owner, pid)
        if all_panels:
            count = len(steps["3"].get("keyframes") or []) or 1
            index = min(len(all_panels) - 1, int(keyframe.get("t", 0) / max(1, (count - 1) * settings.KEYFRAME_INTERVAL_S) * (len(all_panels) - 1)))
            for f, i in all_panels[index: index + 2]:
                if len(refs) >= 10:
                    break
                refs.append(images.panel_jpeg(owner, pid, f, i, cache))
                labels.append(f"Image {len(refs)} is a webtoon panel: match its art style, line work and coloring")
        prompt = (
            "; ".join(labels)
            + ". Draw ONE finished 16:9 webtoon-style animation keyframe. Keep the composition, poses and camera "
            "of the rough sketch exactly. Draw every character exactly like their reference images (same face, "
            "hair, outfit, body) and use exactly the same art style as the webtoon panels. No text, no speech "
            "bubbles, no captions, no panel borders. Scene: "
            + (keyframe.get("sceneEn") or "")
        )
        jpeg = seedream.generate_safe(prompt, refs, model=settings.SEEDREAM_FINAL_MODEL, where="seedream.final")
        image_id = secrets.token_hex(4)
        key = store.keyframe_key(owner, pid, plan_id, keyframe["id"], "final", image_id)
        store.STORE.put_bytes(key, jpeg, "image/jpeg")
        ledger.charge_seedream(
            f"kf-final-{plan_id}-{keyframe['id']}-{image_id}",
            model=settings.SEEDREAM_FINAL_MODEL, user=user, project_id=pid, plan_id=plan_id,
        )
        return {"status": "ready", "key": key, "imageId": image_id, "error": None, "fromRough": rough_key}

    def _finalize_keyframes(owner: str, pid: str, plan_id: str, user: str, state: dict) -> dict:
        """승인 직후: 키 장면 전부를 완성 그림으로(최대 3장 동시). {kf_id: final}."""
        steps = state["steps"]
        keyframes = steps["3"].get("keyframes") or []
        out: dict = {}
        lock = threading.Lock()

        def one(item: dict) -> None:
            try:
                result = _draw_final(owner, pid, plan_id, user, steps, item)
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.keyframe.final_failed kf=%s err=%s", item.get("id"), exc)
                result = {"status": "error", "error": envelope("seedream.final", exc)}
            with lock:
                out[item["id"]] = result

        # ③ 으로 돌아갔다 다시 승인하면, 러프가 그대로인 장면은 이미 그린 완성 그림을 그대로 쓴다(설계자 2026-10-14).
        pending = []
        for item in keyframes:
            final = item.get("final") or {}
            rough_key = (item.get("rough") or {}).get("key")
            if final.get("status") == "ready" and final.get("key") and final.get("fromRough") == rough_key:
                out[item["id"]] = final
            else:
                pending.append(item)
        while pending:
            batch, pending = pending[:3], pending[3:]
            workers = [threading.Thread(target=one, args=(item,), daemon=True) for item in batch]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=320)
        return out

    def _keyframe_inputs(state: dict, plan_shot: dict, prompt: str) -> tuple[str, str, str]:
        """구간(키 장면 a → b) 제출용: (문장, 시작 장면 b64, 끝 장면 b64)."""
        by_id = {item["id"]: item for item in state["steps"]["3"].get("keyframes") or []}
        first_id, last_id = (plan_shot.get("keyframes") or [None, None])[:2]
        frames = []
        for kf_id in (first_id, last_id):
            item = by_id.get(kf_id) or {}
            key = (item.get("final") or {}).get("key") or (item.get("rough") or {}).get("key")
            if not key:
                raise VpocError("routes.submit_shot", "KeyframeMissing", f"키 장면 그림이 없습니다: {kf_id}", 409)
            frames.append(images.jpeg_for_reference(store.STORE.get_bytes(key)))
        text = (
            "Animate smoothly from the first frame to the last frame as one continuous shot. Keep every character "
            "and the art style exactly as in the two frames; no new characters, no text. Motion: "
            + (prompt or "")
        )
        return text, frames[0], frames[1]

    def _single_inputs(
        state: dict, owner: str, pid: str, plan_shot: dict, prompt: str
    ) -> tuple[str, list[str], str, str]:
        """영상 1개(single) 제출용: (문장, 참고 이미지 b64 목록, 첫 장면 b64, 끝 장면 b64).

        설계자 2026-10-19: 완성 키 장면(없으면 러프)을 시간 순서 참고 이미지로 쓴다. Seedance/ fal
        (referenceImagesField) 는 이 목록을, 참고 이미지가 없는 fal 은 첫·끝 장면을, Luma 는 첫 장면을
        쓴다(submit_shot 이 모델별로 고른다). 남는 자리에는 인물 그림을 더할 수 있다(최대 9장).
        문장 앞에 "Image N = n초 장면" 순서 지시를 붙인다.
        """
        by_id = {item["id"]: item for item in state["steps"]["3"].get("keyframes") or []}
        kf_ids = plan_shot.get("keyframes") or []
        times = plan_shot.get("keyframeTimes") or []
        frames: list[str] = []
        used_times: list[float] = []
        for index, kf_id in enumerate(kf_ids):
            item = by_id.get(kf_id) or {}
            key = (item.get("final") or {}).get("key") or (item.get("rough") or {}).get("key")
            if not key:
                raise VpocError("routes.submit_shot", "KeyframeMissing", f"키 장면 그림이 없습니다: {kf_id}", 409)
            if len(frames) >= MAX_REFERENCE_IMAGES:
                break
            frames.append(images.jpeg_for_reference(store.STORE.get_bytes(key)))
            used_times.append(float(times[index]) if index < len(times) else float(item.get("t") or 0))
        references = list(frames)
        # 남는 자리(최대 9장)에 인물 그림을 더한다(있으면). 키 장면이 이미 인물을 담지만 보강용.
        for _char_id, _handle, key, _source in _reference_characters(state["steps"], plan_shot):
            if len(references) >= MAX_REFERENCE_IMAGES:
                break
            try:
                references.append(images.keyframe_base64(key))
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.single.char_ref_failed key=%s err=%s", key, exc)
        sent_prompt = (ark.keyframe_order_prefix(used_times) + " " + (prompt or "")).strip()
        first = frames[0] if frames else ""
        last = frames[-1] if frames else ""
        return sent_prompt, references, first, last

    def _draw_character(
        owner: str, pid: str, plan_id: str, user: str, character: dict, story: dict, style: str
    ) -> dict:
        """캐릭터 그림 1장. 안전 필터 오탐(ImageFiltered)에도 그림이 나오게 단계를 밟는다(설계자 2026-10-13).

        1) 원래 문장 → 2) 오탐 표현 치환 + 새 시드 → 3) Claude 가 같은 외모를 무난한 말로 다시 쓴 문장
        → 4) 다시 쓴 문장 + 새 시드 → 5) 화풍 + 사람 + 단순 배경만. 필터가 아닌 오류(혼잡 등)는 한 번 쉬고
        같은 단계를 다시 한다. 모두 실패하면 마지막 오류를 올린다(보통 여기까지 오지 않는다).
        """
        char_id = character["id"]
        prompt, negative = compose.image_prompt(character, story, style)
        ladder: list[tuple[str, str]] = [(prompt, negative)]
        safer = compose.safe_image_text(prompt)
        ladder.append((safer, negative))
        last: BaseException | None = None
        step = 0
        rewritten = False
        transient_retry = True
        while step < len(ladder):
            text, neg = ladder[step]
            try:
                made = images.generate_character_image(
                    owner=owner,
                    pid=pid,
                    char_id=char_id,
                    prompt=text,
                    negative_prompt=neg,
                    seed=secrets.randbelow(4294967295),
                )
            except VpocError as exc:
                last = exc
                if exc.kind != "ImageFiltered":
                    raise
                log.warning("vpoc.image.filtered char=%s step=%d reason=%s", char_id, step + 1, exc.message)
                if not rewritten and step == 1:
                    rewritten = True
                    try:
                        better, usage = llm.safe_image_prompt(safer)
                        ledger.charge_text(
                            f"safeimg-{plan_id}-{char_id}-{secrets.token_hex(3)}",
                            input_tokens=int(usage.get("inputTokens") or 0),
                            output_tokens=int(usage.get("outputTokens") or 0),
                            user=user,
                            project_id=pid,
                            plan_id=plan_id,
                            model=str(usage.get("model") or ""),
                        )
                        better = compose.safe_image_text(better)
                        ladder += [(better, negative), (better, negative)]
                    except Exception as rewrite_exc:  # noqa: BLE001
                        log.warning("vpoc.image.rewrite_failed char=%s err=%s", char_id, rewrite_exc)
                    ladder.append(compose.minimal_image_prompt(character, style))
                step += 1
                continue
            except Exception as exc:  # noqa: BLE001
                last = exc
                if transient_retry:
                    transient_retry = False
                    time.sleep(3)
                    continue
                raise
            ledger.charge_image(
                f"img-{plan_id}-{char_id}-{made['imageId']}",
                user=user,
                project_id=pid,
                plan_id=plan_id,
            )
            if step:
                log.info("vpoc.image.recovered char=%s step=%d", char_id, step + 1)
            return made
        raise last or VpocError("routes.draw", "ImageFiltered", "그림을 만들지 못했습니다.", 502)

    def _draw_from_webtoon(owner: str, pid: str, plan_id: str, user: str, step1: dict, character: dict) -> dict:
        """웹툰 원작 그대로(설계자 2026-10-14): 올린 웹툰 컷 속 그 인물을 그대로 옮겨 그린 인물 그림.

        Seedream 에 웹툰 장면(최대 8장, 처음~끝 고르게)을 주고, 그 인물을 같은 얼굴 · 머리 · 옷 · 화풍으로
        단순 배경에 전신으로 그리게 한다. ③ 인물 그림 = 이 그림이고, 영상 참고 이미지로 그대로 쓰인다.
        """
        steps_view = {"1": step1, "2": {}, "3": {}}
        cache: dict = {}
        panels = [
            images.panel_jpeg(owner, pid, f, i, cache)
            for f, i in _spread(_story_panels(steps_view, owner, pid), 8)
        ]
        if not panels:
            raise VpocError("routes.webtoon_char", "NoPanels", "웹툰 장면 이미지가 없습니다.", 409)
        handle = character.get("handleEn") or character.get("nameEn") or "the character"
        look = (character.get("appearanceEn") or "").strip()
        prompt = (
            f"The reference images are panels from one webtoon. Find {handle}"
            + (f" ({look})" if look else "")
            + " in them and draw that SAME character once more, exactly as drawn in the webtoon: same face, "
            "eyes, hairstyle, hair color, outfit, body type, line work and coloring. Upper body from the waist up, facing "
            "the viewer, face large and clearly visible, neutral expression, plain light background, 16:9, only this one "
            "character, no text, no speech bubbles."
        )
        jpeg = seedream.generate_safe(prompt, panels, model=settings.SEEDREAM_FINAL_MODEL, where="seedream.webtoon_char")
        image_id = f"img_{character['id']}_{secrets.token_hex(3)}"
        key = store.character_key(owner, pid, character["id"], image_id)
        store.STORE.put_bytes(key, jpeg, "image/jpeg")
        ledger.charge_seedream(
            f"char-webtoon-{plan_id}-{character['id']}-{image_id}",
            model=settings.SEEDREAM_FINAL_MODEL, user=user, project_id=pid, plan_id=plan_id,
        )
        return {"imageId": image_id, "key": key}

    def do_redraw(owner: str, pid: str, plan_id: str, user: str, state: dict, char_id: str) -> dict:
        step2 = state["steps"]["2"]
        character = next(
            (item for item in step2.get("characters") or [] if item.get("id") == char_id), None
        )
        if not character:
            raise VpocError("routes.redraw", "NotFound", "캐릭터를 찾을 수 없습니다.", 404)
        style = _gen_style((step2.get("story") or {}).get("style") or "live_action")
        if style == "webtoon":
            try:
                made = _draw_from_webtoon(owner, pid, plan_id, user, state["steps"]["1"], character)
                return {
                    "characters": [
                        {
                            "id": char_id,
                            "image": {
                                "status": "ready",
                                "source": "webtoon",
                                "imageId": made["imageId"],
                                "key": made["key"],
                                "seed": None,
                                "error": None,
                            },
                        }
                    ]
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.webtoon_char.redraw_failed char=%s err=%s", char_id, exc)
        made = _draw_character(owner, pid, plan_id, user, character, step2.get("story") or {}, style)
        return {
            "characters": [
                {
                    "id": char_id,
                    "image": {
                        "status": "ready",
                        "source": "ai",
                        "imageId": made["imageId"],
                        "key": made["key"],
                        "seed": made["seed"],
                        "error": None,
                    },
                }
            ]
        }
    # ---------------------------------------------------------------- 마스터 프롬프트(영상 1개, single)

    # 설계자 2026-10-19: 영상 1개(single)는 "마스터 프롬프트 1개 + 고정 인물 + 고정 배경"이다.
    # steps["3"]["master"] 에 데이터를 두고, 미리보기 콘티는 기존 keyframes 배열을 재사용한다
    # (각 keyframe 에 beatId 를 연결). 최종 영상 프롬프트는 compose.master_video_prompt 하나뿐이다.
    # 그림은 확인용이고, 수정 기준은 프롬프트다.

    def _beat_mid_time(beat: dict) -> float:
        """비트의 중간 시점(미리보기 콘티를 뽑을 초)."""
        t0 = float(beat.get("t0") or 0)
        t1 = float(beat.get("t1") or t0)
        return round((t0 + t1) / 2.0, 2)

    def _master_background_prompt(master: dict) -> str:
        """배경 그림 1장(인물 없음)의 Seedream 프롬프트. styleEn + 배경 프롬프트 + 빈 장면 지시."""
        style = str(master.get("styleEn") or "").strip().rstrip(".")
        bg = str((master.get("background") or {}).get("promptEn") or "").strip().rstrip(".")
        head = (style + ". ") if style else ""
        return f"{head}{bg}, empty scene, no people, no text, 16:9"

    def _draw_background(owner: str, pid: str, plan_id: str, user: str, master: dict) -> dict:
        """배경 그림 1장(Seedream Final, 인물 없음). 성공하면 {status,key,imageId}."""
        prompt = _master_background_prompt(master)
        try:
            jpeg = seedream.generate_safe(
                prompt, None, model=settings.SEEDREAM_FINAL_MODEL, where="seedream.master_background"
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": envelope("seedream.master_background", exc)}
        image_id = secrets.token_hex(4)
        key = store.master_background_key(owner, pid, plan_id, image_id)
        store.STORE.put_bytes(key, jpeg, "image/jpeg")
        ledger.charge_seedream(
            f"master-bg-{plan_id}-{image_id}",
            model=settings.SEEDREAM_FINAL_MODEL, user=user, project_id=pid, plan_id=plan_id,
        )
        return {"status": "ready", "key": key, "imageId": image_id, "error": None}

    def _beat_preview_prompt(master: dict, beat: dict) -> str:
        """비트 미리보기 콘티(Seedream Flash)의 프롬프트.

        styleEn + 배경 프롬프트 + 그 비트 인물들의 외모(promptEn) + 그 비트 actionEn/cameraEn.
        """
        style = str(master.get("styleEn") or "").strip().rstrip(".")
        bg = str((master.get("background") or {}).get("promptEn") or "").strip().rstrip(".")
        by_id = {item.get("id"): item for item in master.get("characters") or []}
        looks = []
        for cid in beat.get("characters") or []:
            item = by_id.get(cid) or {}
            handle = str(item.get("handleEn") or "the character").strip()
            look = str(item.get("promptEn") or "").strip().rstrip(".")
            if look:
                looks.append(f"{handle}: {look}")
        action = str(beat.get("actionEn") or "").strip().rstrip(".")
        camera = str(beat.get("cameraEn") or "").strip().rstrip(".")
        parts = []
        if style:
            parts.append(style + ".")
        if bg:
            parts.append(f"Setting: {bg}.")
        if looks:
            parts.append("; ".join(looks) + ".")
        if action:
            parts.append(action + ".")
        if camera:
            parts.append(camera + ".")
        parts.append("16:9, no text, no speech bubbles.")
        return " ".join(parts)

    def _beat_preview_refs(owner: str, pid: str, master: dict, beat: dict) -> list[bytes]:
        """미리보기 참고 이미지 = 배경 그림 + 그 비트 인물 사진(있는 것만, 읽기 실패는 건너뜀)."""
        refs: list[bytes] = []
        bg_key = (master.get("background") or {}).get("imageKey") or ""
        if bg_key:
            try:
                refs.append(store.STORE.get_bytes(bg_key))
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.master.bg_ref_failed key=%s err=%s", bg_key, exc)
        by_id = {item.get("id"): item for item in master.get("characters") or []}
        for cid in beat.get("characters") or []:
            key = (by_id.get(cid) or {}).get("imageKey") or ""
            if not key:
                continue
            try:
                refs.append(store.STORE.get_bytes(key))
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.master.char_ref_failed key=%s err=%s", key, exc)
        return refs

    def _draw_beat_preview(
        owner: str, pid: str, plan_id: str, user: str, master: dict, keyframe: dict
    ) -> dict:
        """비트 미리보기 콘티 1장(Seedream Flash). 참고 = 배경 + 그 비트 인물 사진."""
        beat = next(
            (item for item in master.get("beats") or [] if item.get("id") == keyframe.get("beatId")),
            {},
        )
        prompt = _beat_preview_prompt(master, beat)
        refs = _beat_preview_refs(owner, pid, master, beat)
        try:
            jpeg = seedream.generate_safe(
                prompt, refs or None, model=settings.SEEDREAM_ROUGH_MODEL, where="seedream.master_preview"
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": envelope("seedream.master_preview", exc)}
        image_id = secrets.token_hex(4)
        key = store.keyframe_key(owner, pid, plan_id, keyframe["id"], "rough", image_id)
        store.STORE.put_bytes(key, jpeg, "image/jpeg")
        ledger.charge_seedream(
            f"master-prev-{plan_id}-{keyframe['id']}-{image_id}",
            model=settings.SEEDREAM_ROUGH_MODEL, user=user, project_id=pid, plan_id=plan_id,
        )
        return {"status": "ready", "key": key, "imageId": image_id, "error": None}

    def _build_master(
        owner: str, pid: str, plan_id: str, user: str, step1: dict, step2: dict,
        image_results: dict,
    ) -> dict:
        """do_prepare 의 single 경로: 마스터 데이터 + 배경 그림 + 미리보기 콘티.

        image_results 는 인물 그림 준비 결과({char_id: {key, source, ...}})다 — 이미 끝난 뒤에 부른다.
        배경 그림이 먼저 있어야 미리보기가 그 배경을 참고하므로 배경 → 미리보기 순서로 그린다.
        돌려주는 dict 는 apply_result('prepare') 가 steps["3"] 에 넣는다(master + keyframes + shots=[]).
        """
        # 073 R3.7: 실제로 쓸 모델(resolve_model)의 범위로 영상 1개 길이를 정한다.
        model = ark.plan_model(step2)
        total = _single_total(step2, model)
        style_en = compose.STYLE[_gen_style((step2.get("story") or {}).get("style") or "")]
        characters = step2.get("characters") or []
        story = step2.get("story") or {}
        story_text = " ".join(
            str(story.get(field) or "") for field in ("titleKo", "loglineKo", "settingKo")
        ).strip() or str(step1.get("text") or "")
        steps_view = {"1": step1, "2": step2, "3": {}}
        panels = _spread(_story_panels(steps_view, owner, pid), 12)
        cache: dict = {}
        panel_bytes = [images.panel_jpeg(owner, pid, f, i, cache) for f, i in panels]

        plan, usage = llm.master_plan(
            panel_bytes, characters, story_text=story_text, total_s=total,
            min_beats=settings.SINGLE_MIN_KEYFRAMES, max_beats=settings.SINGLE_MAX_KEYFRAMES,
        )
        ledger.charge_text(
            f"master-{plan_id}-{secrets.token_hex(3)}",
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            user=user, project_id=pid, plan_id=plan_id, model=str(usage.get("model") or ""),
        )

        # 비트 정규화: 2~4개, 시간 구간이 0~total 을 빈틈없이 덮게 한다(마지막 t1 = total).
        known = {item.get("id") for item in characters}
        handles = {
            item.get("id"): item.get("handleEn") or item.get("nameEn") or item.get("id")
            for item in characters
        }
        raw_beats = [b for b in (plan.get("beats") or []) if isinstance(b, dict)]
        count = max(settings.SINGLE_MIN_KEYFRAMES, min(settings.SINGLE_MAX_KEYFRAMES, len(raw_beats) or settings.SINGLE_MIN_KEYFRAMES))
        raw_beats = raw_beats[:count]
        while len(raw_beats) < count:
            raw_beats.append(dict(raw_beats[-1]) if raw_beats else {})
        step = float(total) / count
        beats = []
        for index, beat in enumerate(raw_beats):
            t0 = round(index * step, 2)
            t1 = round((index + 1) * step, 2) if index < count - 1 else float(total)
            cast = [cid for cid in (beat.get("characters") or []) if cid in known] or list(known)
            beats.append(
                {
                    "id": f"b{index + 1}",
                    "t0": t0,
                    "t1": t1,
                    "actionEn": str(beat.get("actionEn") or "")[:240],
                    "cameraEn": str(beat.get("cameraEn") or "")[:80],
                    "descKo": str(beat.get("descKo") or "")[:70],
                    "characters": cast,
                }
            )

        # 인물: promptEn 기본값 = ② appearanceEn, 없으면 master_plan 이 보강한 문장. imageKey = ③ 인물 그림.
        plan_looks = {
            str(item.get("id")): str(item.get("appearanceEn") or "").strip()
            for item in (plan.get("characters") or [])
            if isinstance(item, dict)
        }
        master_chars = []
        for item in characters:
            cid = item.get("id")
            look = str(item.get("appearanceEn") or "").strip() or plan_looks.get(str(cid), "")
            master_chars.append(
                {
                    "id": cid,
                    "handleEn": str(item.get("handleEn") or item.get("nameEn") or "the character"),
                    "promptEn": look[:200],
                    "imageKey": (image_results.get(cid) or {}).get("key") or "",
                }
            )

        master = {
            "styleEn": style_en,
            "background": {
                "promptEn": str(plan.get("backgroundEn") or "")[:300],
                "descKo": str(plan.get("backgroundKo") or "")[:120],
                "imageKey": "",
                "status": "drawing",
                "error": None,
            },
            "characters": master_chars,
            "beats": beats,
            "totalS": total,
            "version": 1,
        }

        # 배경 그림 먼저(미리보기가 참고로 쓴다).
        bg = _draw_background(owner, pid, plan_id, user, master)
        master["background"]["imageKey"] = bg.get("key") or ""
        master["background"]["status"] = bg.get("status") or "failed"
        master["background"]["error"] = bg.get("error")

        # 미리보기 콘티 = 비트마다 1장(중간 시점). 기존 keyframes 배열을 재사용한다(각각 beatId 연결).
        keyframes = []
        for beat in beats:
            keyframes.append(
                {
                    "id": "kf_" + beat["id"],
                    "beatId": beat["id"],
                    "t": _beat_mid_time(beat),
                    "summaryKo": beat.get("descKo") or "",
                    "characters": list(beat.get("characters") or []),
                    "rough": {"status": "none"},
                    "final": {"status": "none"},
                    "stale": False,
                }
            )
        results: dict = {}

        def draw(item: dict) -> None:
            results[item["id"]] = _draw_beat_preview(owner, pid, plan_id, user, master, item)

        workers = [threading.Thread(target=draw, args=(item,), daemon=True) for item in keyframes]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=240)
        for item in keyframes:
            item["rough"] = results.get(item["id"]) or {"status": "error", "error": {"message": "시간 초과"}}

        # shots 는 single 에서 쓰지 않지만, generate 의 샷 없음 검사(③ 샷 필요)를 통과하도록 비트당 1개를
        # 둔다(영상 제출은 master_video_prompt 로만 한다 — 이 shots 의 promptEn 은 쓰지 않는다).
        shots = [
            {
                "id": "seg_" + beat["id"],
                "summaryKo": beat.get("descKo") or "",
                "cameraKo": "",
                "cameraEn": beat.get("cameraEn") or "",
                "locationEn": "",
                "actionEn": beat.get("actionEn") or "",
                "characters": list(beat.get("characters") or []),
                "useKeyframe": False,
                "reviewKo": None,
                "promptEn": beat.get("actionEn") or "-",
                "durationS": round(float(beat["t1"]) - float(beat["t0"]), 2),
                "beatId": beat["id"],
            }
            for beat in beats
        ]
        return {"master": master, "keyframes": keyframes, "shots": shots}

    def _master_video_references(
        state: dict, owner: str, pid: str
    ) -> tuple[list[tuple[str, str]], str]:
        """영상 제출용 참고: ([(handle, 인물 사진 키)] 등장 순서, 배경 그림 키).

        설계자 2026-10-19: 인물 사진들(등장 순서) + 배경 그림을 참고 이미지로 쓴다. 키 장면 그림은
        넣지 않는다. imageKey 가 없는 인물은 건너뛴다.
        """
        master = state["steps"]["3"].get("master") or {}
        chars: list[tuple[str, str]] = []
        for item in master.get("characters") or []:
            key = str(item.get("imageKey") or "")
            if key:
                chars.append((str(item.get("handleEn") or "the character"), key))
        bg_key = str((master.get("background") or {}).get("imageKey") or "")
        return chars, bg_key

    def _master_submit_inputs(
        state: dict, owner: str, pid: str, video_model: str, *, tag_style: str = "plain"
    ) -> tuple[str, str, list[str], str]:
        """영상 1개(single) 제출 인자: (마스터 문장, 참고 이미지 설명 접두, 참고 이미지 b64 목록, 첫 이미지 b64).

        마스터 문장 = compose.master_video_prompt(master) 그대로. 참고 이미지 설명("Image 1 is ...")은
        따로 돌려주고, **참고 이미지를 실제로 보낼 때만** 호출부가 앞에 붙인다(설계자 2026-10-19 수정).
        참고 이미지가 없는 경우(Luma 시작 이미지 · fal start-only · fal 텍스트→영상)에는 틀린 설명이
        붙지 않도록 접두를 빼고 마스터 문장만 보낸다. 참고 이미지 = 인물 사진(등장 순서) + 배경 그림.
        tag_style: 지칭 방식(073 R5.3, ark.tag_style). "at" 이면 "@Image1 is ...". 번호는 실제로 읽은 그림 순서다.
        """
        master = state["steps"]["3"].get("master") or {}
        base_prompt = compose.master_video_prompt(master, max_chars=ark.MAX_PROMPT_CHARS)
        chars, bg_key = _master_video_references(state, owner, pid)
        labels = []
        references: list[str] = []
        index = 1
        for handle, key in chars:
            if len(references) >= MAX_REFERENCE_IMAGES:
                break
            try:
                references.append(images.keyframe_base64(key))
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.master.ref_failed key=%s err=%s", key, exc)
                continue
            labels.append(f"{ark.image_label(index, index, tag_style)} is {handle}")
            index += 1
        if bg_key and len(references) < MAX_REFERENCE_IMAGES:
            try:
                references.append(images.keyframe_base64(bg_key))
                labels.append(f"{ark.image_label(index, index, tag_style)} is the background setting")
                index += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.master.bg_failed key=%s err=%s", bg_key, exc)
        lead = ("; ".join(labels) + ". ") if labels else ""
        # 시작 이미지(참고 이미지가 없는 모델용): 첫 비트 미리보기 그림.
        first = ""
        keyframes = state["steps"]["3"].get("keyframes") or []
        if keyframes:
            kf_key = (keyframes[0].get("final") or {}).get("key") or (keyframes[0].get("rough") or {}).get("key") or ""
            if kf_key:
                try:
                    first = images.keyframe_base64(kf_key)
                except Exception as exc:  # noqa: BLE001
                    log.warning("vpoc.master.first_failed key=%s err=%s", kf_key, exc)
        return base_prompt, lead, references, first

    def _mark_master_stale(state: dict, *, background: bool = False, char_ids=None, beat_ids=None) -> None:
        """수정이 들어오면 영향받는 미리보기를 stale 로 표시한다(설계자 2026-10-19).

        배경 수정 → 배경 그림 + 전체 미리보기 stale. 인물 수정 → 그 인물이 나오는 비트. 비트 수정 → 그 비트.
        """
        master = state["steps"]["3"].get("master") or {}
        char_ids = set(char_ids or [])
        beat_ids = set(beat_ids or [])
        if background:
            master.setdefault("background", {})["status"] = "stale"
        beats_by_id = {b.get("id"): b for b in master.get("beats") or []}
        for kf in state["steps"]["3"].get("keyframes") or []:
            beat_id = kf.get("beatId")
            if beat_id is None:
                continue
            beat = beats_by_id.get(beat_id) or {}
            hit = background or beat_id in beat_ids or (char_ids & set(beat.get("characters") or []))
            if hit:
                kf["stale"] = True


    # ---------------------------------------------------------------- 제출 · 조회

    def submit_shot(
        state: dict,
        owner: str,
        pid: str,
        plan_id: str,
        user: str,
        shot: dict,
        *,
        target: dict | None = None,
        prompt_en: str | None = None,
        want_keyframe: bool | None = None,
    ) -> None:
        """샷 하나를 제출한다.

        `target` 을 주면 제출 결과를 그 딕셔너리에 쓴다. 재생성(`shot.regen`)이 샷 본체를
        건드리지 않고 같은 제출 경로를 타기 위한 것이다(regen-plan G2·G5). 주지 않으면
        기존 동작 그대로 `shot` 에 쓴다.
        """
        target = shot if target is None else target
        # 생성 회차. 옛 계획에는 없고, 그때는 옛 키 규칙으로 간다.
        gen_id = str(state["steps"]["4"].get("genId") or "")
        plan_shot = next(
            (item for item in state["steps"]["3"].get("shots", []) if item["id"] == shot["id"]),
            None,
        )
        if shot["id"] == ark.ONE_TAKE_ID and state["steps"]["4"].get("single"):
            # 영상 1개(single, 설계자 2026-10-19): 마스터 프롬프트 하나로 만든다. plan_shot 은 길이·인물만
            # 담는 껍데기이고, 실제 프롬프트·참고 이미지는 _master_submit_inputs 가 마스터에서 만든다.
            master = state["steps"]["3"].get("master") or {}
            if master:
                plan_shot = {
                    "id": ark.ONE_TAKE_ID,
                    "characters": [c.get("id") for c in master.get("characters") or []],
                    "promptEn": compose.master_video_prompt(master, max_chars=ark.MAX_PROMPT_CHARS),
                    "durationS": int(master.get("totalS") or _single_total(state["steps"]["2"], str(state["steps"]["4"].get("videoModel") or ""))),
                }
            else:
                plan_shot = None
        elif shot["id"] == ark.ONE_TAKE_ID and state["steps"]["4"].get("oneTake"):
            # A안(옛): ③ 의 샷 전체를 시간대별 지시 하나로 합친 영상 1개.
            plan_shot = _one_take(
                state["steps"], str(state["steps"]["4"].get("videoModel") or "")
            )
        if not plan_shot:
            target["status"] = "failed"
            target["error"] = {
                "where": "routes.submit_shot",
                "type": "ShotMissing",
                "message": (
                    # 한 번에 이어 만들기 조건(모델 최대 길이 등)이 바뀌어 더는 만들 수 없는 옛 회차.
                    "한 번에 이어 만들 수 없는 길이입니다(모델 최대 길이 초과). "
                    "③ 계획 승인에서 다시 승인해 주세요 — 샷별로 나눠 만듭니다."
                    if shot["id"] == ark.ONE_TAKE_ID
                    else "계획에서 이 샷을 찾을 수 없습니다."
                ),
                "requestId": "",
                "httpStatus": 400,
                "at": now_iso(),
            }
            return

        attempt = int(target.get("attempt") or shot.get("attempt") or 1)
        prompt = plan_shot.get("promptEn") or "" if prompt_en is None else str(prompt_en)
        allow_keyframe = plan_shot.get("useKeyframe") if want_keyframe is None else want_keyframe
        keyframe = ""
        mode = "text"
        if target.get("mode") == "keyframe" and allow_keyframe:
            char_ids = plan_shot.get("characters") or []
            if len(char_ids) == 1:
                entry = next(
                    (
                        item
                        for item in state["steps"]["3"].get("characters", [])
                        if item.get("id") == char_ids[0]
                    ),
                    None,
                )
                image = (entry or {}).get("image") or {}
                if image.get("source") == "ai" and image.get("key"):
                    try:
                        keyframe = images.keyframe_base64(image["key"])
                        mode = "keyframe"
                    except Exception as exc:  # noqa: BLE001
                        log.warning("vpoc.keyframe.failed shot=%s err=%s", shot["id"], exc)

        # 이 회차의 모델 · 화질 · 샷 길이(generate 가 정한다). 옛 계획에는 없고, 그때는 Luma 기본값이다.
        step4 = state["steps"]["4"]
        video_model = str(step4.get("videoModel") or ark.DEFAULT_MODEL)
        # 073 R11.1·R11.4: 외부 공급자 판정은 등록 여부와 상관없다. 미등록 fal 회차가 Luma 로 새지 않는다.
        use_ark = ark.is_external(video_model)
        # 073 R4.3-4.4: fal Seedance 계열은 caps 에 없는 저장 화질(070 이전 540p 등)을 ③ 기본(720p)으로 바꿔
        # 명시해 보낸다. 그 밖(계열이 아닌 fal 포함)은 지금처럼 저장값 또는 모델 기본값.
        video_resolution, adjusted_from = ark.submit_resolution(video_model, step4.get("resolution"))
        if adjusted_from:
            log.info(
                "vpoc.fal.resolution_adjusted model=%s from=%s to=%s plan=%s shot=%s",
                video_model,
                adjusted_from,
                video_resolution,
                plan_id,
                shot["id"],
            )
        seconds = int(shot.get("durationS") or _shot_seconds(video_model, plan_shot))
        try:
            if use_ark:
                if settings.is_fal(video_model) and not fal.is_registered(video_model):
                    # E5(073 R11.1, D11): 이 회차의 fal 모델이 해제됐다. Luma·다른 엔드포인트로 몰래 보내지 않는다.
                    # 아래 except VpocError 가 샷을 failed 로 두고, 원장에는 계상하지 않는다.
                    raise VpocError(
                        "routes.submit_shot",
                        "ModelUnavailable",
                        f"{video_model}: 등록되지 않은 모델입니다. 이 회차의 fal 모델이 해제되어 만들 수 없습니다. "
                        "관리자에게 다시 등록을 요청하거나 ③에서 다시 승인해 주세요.",
                        400,
                    )
                # 073 R3.8·R14.3: 길이 검사는 BytePlus 와 fal Seedance 계열만. 계열이 아닌 fal 은 지금처럼 건너뛴다.
                if video_model in settings.ARK_MODEL_IDS or settings.is_fal_seedance_family(video_model):
                    low, high = ark.duration_range(video_model)
                else:
                    low, high = seconds, seconds
                if not low <= seconds <= high:
                    # 상한을 고치기 전에 승인된 계획(예: 20초 한 번에 이어 만들기)은 다시 보내도 거절된다.
                    raise VpocError(
                        "routes.submit_shot",
                        "BadDuration",
                        f"{ark.label(video_model)}는 한 번에 {low}~{high}초만 만들 수 있습니다"
                        f"(이 영상 {seconds}초). ③ 계획 승인에서 다시 승인해 주세요 — 길면 샷별로 나눠 만듭니다.",
                        400,
                    )
                # 설계자 2026-10-07: 샷의 캐릭터 이미지(사용자 업로드 또는 ③에서 승인한 AI 이미지)를
                # 참고 이미지로 함께 보내고, 그 캐릭터 그대로 만들라는 지시를 프롬프트 앞에 붙인다.
                # 설계자 2026-10-08/13: 인물 그림(한 인물 최대 2장) + 화풍 참고 그림을 함께 보내고,
                # 문장은 ③ 미리보기와 같은 _reference_bundle 규칙으로 만든다. 읽지 못한 그림은 빼고
                # 문장도 실제로 보낸 그림 순서대로 다시 만든다.
                # 설계자 2026-10-13: 이 샷이 맡은 웹툰 장면(순서대로)과 그 장면을 Claude 가 읽은 설명
                # (분위기 · 배경 · 인물별 행동 · 상황 · 움직임 순서)을 함께 보낸다 — generate 때 정해 둔다.
                last_frame = ""
                is_single = shot["id"] == ark.ONE_TAKE_ID and state["steps"]["4"].get("single")
                if is_single:
                    # 영상 1개(single, 설계자 2026-10-19): 마스터 프롬프트 + 인물 사진 + 배경 그림.
                    # 키 장면 그림은 영상에 넣지 않는다. Seedance 와 referenceImagesField 가 있는 fal 은
                    # 참고 이미지, startImageField 만 있는 fal(이미지→영상 전용)은 첫 비트 미리보기를
                    # 시작 이미지로(끝 이미지 없음), 텍스트→영상 fal 은 이미지 없이.
                    base_prompt, lead, references, first_master = _master_submit_inputs(
                        state, owner, pid, video_model, tag_style=ark.tag_style(video_model)
                    )
                    use_reference = True
                    start_only = False
                    text_only = False
                    if settings.is_fal(video_model):
                        # fal Seedance 계열 얼굴은 짝(r2v) caps 로 참고 방식을 정한다(073 R2.3). 그 밖은 자기 caps.
                        caps = fal.reference_caps(video_model)
                        use_reference = bool(caps.get("referenceImagesField"))
                        start_only = not use_reference and bool(caps.get("startImageField"))
                        text_only = not use_reference and not start_only
                    if use_reference:
                        # 참고 이미지를 실제로 보낼 때만 "Image N is ..." 설명을 앞에 붙인다.
                        sent_prompt = (lead + base_prompt).strip()
                        keyframe, last_frame = "", ""
                        # 참고 그림을 하나도 읽지 못했으면 글만 보낸 것이다(073 §4: mode=text).
                        mode = "reference" if references else "text"
                    elif start_only:
                        # 참고 이미지 없음 → 틀린 "Image N" 설명을 붙이지 않는다(마스터 문장만).
                        sent_prompt = base_prompt
                        references, keyframe, last_frame = [], first_master, ""
                        mode = "keyframe"
                    else:  # text_only
                        sent_prompt = base_prompt
                        references, keyframe, last_frame = [], "", ""
                        mode = "text"
                else:
                    step4_now = state["steps"]["4"]
                    shot_panels = (step4_now.get("panels") or {}).get(shot["id"]) or []
                    shot_brief = (step4_now.get("briefs") or {}).get(shot["id"]) or ""
                    failed: set[str] = set()
                    encoded: dict[str, str] = {}
                    tile_cache: dict = {}
                    # 073 R5.3: 지칭 방식(계열 얼굴이면 설정, 그 밖은 "Image N"). ③ 미리보기와 같은 값이다.
                    shot_tag_style = ark.tag_style(video_model)
                    for _, key in _reference_bundle(
                        state["steps"],
                        owner,
                        pid,
                        plan_shot,
                        prompt,
                        panels=shot_panels,
                        brief=shot_brief,
                        tag_style=shot_tag_style,
                    )[1]:
                        try:
                            encoded[key] = _reference_b64(owner, pid, key, tile_cache)
                        except Exception as exc:  # noqa: BLE001
                            failed.add(key)
                            log.warning("vpoc.reference.failed shot=%s key=%s err=%s", shot["id"], key, exc)
                    sent_prompt, entries = _reference_bundle(
                        state["steps"],
                        owner,
                        pid,
                        plan_shot,
                        prompt,
                        skip=frozenset(failed),
                        panels=shot_panels,
                        brief=shot_brief,
                        tag_style=shot_tag_style,
                    )
                    references = []
                    for _, key in entries:
                        if key not in encoded:  # 앞 그림이 빠져 뒤 그림이 자리를 얻은 경우
                            encoded[key] = _reference_b64(owner, pid, key, tile_cache)
                        references.append(encoded[key])
                    if plan_shot.get("keyframes"):
                        # 키 장면(옛 keyframes) 방식: 완성 키 장면 a → b 를 시작 · 끝 장면으로.
                        sent_prompt, keyframe, last_frame = _keyframe_inputs(state, plan_shot, prompt)
                        references = []
                        mode = "keyframe"
                    if references:
                        mode = "reference"
                # 엔드포인트 선택(073 §4·R2.3): fal Seedance 계열 얼굴이면 참고·시작 유무로 i2v/r2v 를 고른다.
                # 그 밖(ARK·계열이 아닌 fal)은 회차 모델 그대로. 회차 저장값(steps.4.videoModel)은 바꾸지 않는다.
                submit_model = video_model
                if settings.is_fal(video_model):
                    submit_model = fal.route_model(
                        video_model, has_references=bool(references), has_start=bool(keyframe)
                    )
                    if submit_model != video_model and not fal.is_registered(submit_model):
                        # E5(R11.1): 고른 엔드포인트가 회차 도중 해제됐다. 다른 엔드포인트로 몰래 보내지 않는다.
                        raise VpocError(
                            "routes.submit_shot",
                            "ModelUnavailable",
                            f"{submit_model}: 등록되지 않은 모델입니다. 이 회차의 fal 모델이 해제되어 만들 수 없습니다. "
                            "관리자에게 다시 등록을 요청하거나 ③에서 다시 승인해 주세요.",
                            400,
                        )
                    if settings.is_fal_seedance_family(video_model):
                        # R2.7: 어느 엔드포인트로 보냈는지(id·개수만, 프롬프트 본문은 남기지 않는다).
                        log.info(
                            "vpoc.fal.route model=%s endpoint=%s mode=%s refs=%d plan=%s shot=%s",
                            video_model,
                            submit_model,
                            mode,
                            len(references),
                            plan_id,
                            shot["id"],
                        )
                job = ark.submit(
                    model=submit_model,
                    owner=owner,
                    project_id=pid,
                    plan_id=plan_id,
                    shot_id=shot["id"],
                    attempt=attempt,
                    prompt=sent_prompt,
                    keyframe_jpeg_b64=keyframe,
                    gen_id=gen_id,
                    reference_jpegs_b64=references,
                    resolution=video_resolution,
                    duration_s=seconds,
                    last_frame_jpeg_b64=last_frame,
                )
            else:
                single_prompt = prompt
                if shot["id"] == ark.ONE_TAKE_ID and state["steps"]["4"].get("single"):
                    # Luma Ray2: 마스터 프롬프트(참고 이미지 설명 없이) + 첫 비트 미리보기를 시작 이미지로
                    # (설계자 2026-10-19). Luma 는 참고 이미지를 안 보내므로 "Image N" 접두를 붙이지 않는다.
                    base_prompt, _lead, _refs, first_master = _master_submit_inputs(
                        state, owner, pid, video_model
                    )
                    single_prompt = base_prompt
                    keyframe = first_master
                    sent_prompt = single_prompt
                    if keyframe:
                        mode = "keyframe"
                job = video.submit(
                    owner=owner,
                    project_id=pid,
                    plan_id=plan_id,
                    shot_id=shot["id"],
                    attempt=attempt,
                    prompt=single_prompt,
                    keyframe_jpeg_b64=keyframe,
                    gen_id=gen_id,
                    resolution=video_resolution,
                )
        except video.Throttled as exc:
            # E4: 대기로 두고 다음 조회 때 재제출. 과금 없음.
            target["status"] = "queued"
            target["note"] = f"동시 제출 한도로 대기 중 ({exc.code})"
            return
        except VpocError as exc:
            if use_ark and exc.kind == "HTTP429":
                target["status"] = "queued"
                target["note"] = "동시 제출 한도로 대기 중 (HTTP429)"
                return
            target["status"] = "failed"
            target["error"] = envelope("ark.submit" if use_ark else "luma.start_async_invoke", exc)
            target["note"] = None
            return
        except Exception as exc:  # noqa: BLE001
            body = envelope("ark.submit" if use_ark else "luma.start_async_invoke", exc)
            target["status"] = "failed"
            target["error"] = body
            target["note"] = None
            return

        ledger.charge_video(
            job["clientRequestToken"],
            user=user,
            project_id=pid,
            plan_id=plan_id,
            model=video_model,
            resolution=video_resolution,
            seconds=seconds,
            # 073 X2: fal 이면 원장 model 칸에 실제로 보낸 엔드포인트(i2v·r2v·그 모델)를 남긴다. 금액은 회차 모델 단가.
            endpoint=job["provider"] if settings.is_fal(video_model) else "",
        )
        # 073 R5.2: fal 이 실제로 보낸 문장·잘린 크기. ARK·Luma 반환에는 없다(None).
        job_sent_prompt = job.get("sentPrompt")
        prompt_trimmed = job.get("promptTrimmed") or None
        target.update(
            {
                "status": "submitted",
                "mode": mode,
                "provider": job["provider"],
                "invocationArn": job["invocationArn"],
                "outputPrefix": job["outputPrefix"],
                "submittedAt": job["submittedAt"],
                "promptChars": job["promptChars"],
                "note": None,
                "error": None,
                "lastPolledAt": "",
                # 이번 제출 기준으로 늘 다시 쓴다(앞 시도가 잘렸어도 이번에 안 잘렸으면 None).
                "promptTrimmed": prompt_trimmed,
            }
        )
        # 영상 1개(single): 승인한 프롬프트 그대로 보냈음을 ④ 화면에서 확인할 수 있게 기록한다(설계자 2026-10-19).
        # 073 R5.2: 실제로 보낸 문장(fal 이 잘랐으면 잘린 문장). 잘린 샷은 single 이 아니어도 ④ 에 보인다(E8).
        is_single_record = shot["id"] == ark.ONE_TAKE_ID and state["steps"]["4"].get("single")
        if is_single_record:
            master = state["steps"]["3"].get("master") or {}
            target["sentPromptEn"] = job_sent_prompt or sent_prompt
            target["masterVersion"] = int(master.get("version") or 1)
        elif prompt_trimmed:
            target["sentPromptEn"] = job_sent_prompt or sent_prompt
        elif target.get("sentPromptEn"):
            # 앞 시도가 잘려 남은 문장을 지운다(이번 제출은 잘리지 않았다).
            target["sentPromptEn"] = None
        # 샷별 작업 기록. 조회(GET)는 state.json 대신 이 파일만 써도 되는 감사 기록이다.
        store.STORE.write_json(
            store.job_key(owner, pid, plan_id, shot["id"], attempt, gen_id),
            {
                "planId": plan_id,
                "genId": gen_id,
                "shotId": shot["id"],
                "attempt": attempt,
                "provider": job["provider"],
                "invocationArn": job["invocationArn"],
                "mode": mode,
                "prompt": prompt,
                "promptChars": job["promptChars"],
                "status": "submitted",
                "submittedAt": job["submittedAt"],
                "outputPrefix": job["outputPrefix"],
                "usd": settings.shot_usd(video_model, video_resolution, seconds),
                "videoModel": video_model,
                # 실제로 보낸 엔드포인트(073 R2.5). fal Seedance 계열이면 i2v 또는 r2v, 그 밖은 provider 와 같다.
                "endpointModel": job["provider"],
                "resolution": video_resolution,
                "durationS": seconds,
                # Seedance 에 실제로 보낸 문장(참고 이미지 지시 포함). fal 은 잘린 뒤 문장(073 R5.2). Luma 는 prompt 와 같다.
                "sentPrompt": job_sent_prompt or (sent_prompt if use_ark else prompt),
                # fal 이 잘랐으면 {fromBytes, toBytes}(073 R5.2), 아니면 None.
                "promptTrimmed": prompt_trimmed,
                # single 은 승인한 마스터 프롬프트 · 버전을 기록한다.
                "sentPromptEn": (job_sent_prompt or sent_prompt) if is_single_record else None,
                "masterVersion": (
                    int((state["steps"]["3"].get("master") or {}).get("version") or 1)
                    if is_single_record
                    else None
                ),
            },
        )

    # ---------------------------------------------------------------- 실사 → 애니 변환(dual)

    def _anime_reference_b64(state: dict, owner: str, pid: str) -> tuple[list[str], int]:
        """변환 참고 이미지(원작 그림체) 최대 4장(data URI b64)과 그중 패널 수 (refs, panel_count).

        설계자 2026-10-20: ① sceneFiles(웹툰 장면 조각) 최대 3장을 먼저, ② ③ 캐릭터 그림 1장.
        원작 그림체가 기준이므로 패널을 앞에 둔다(프롬프트의 @Image1.. 순서와 같다). 없으면 생략.
        073 R8.4: 패널 수를 함께 돌려줘 _anime_prompt 가 패널(그림체)·인물(identity only) 번호를 나눠 부른다.
        기존 이미지 로더(images.panel_jpeg·images.jpeg_for_reference·images.keyframe_base64)를 재사용한다.
        읽기 실패는 조용히 건너뛴다(실패한 그림은 번호에 들어가지 않는다).
        """
        steps = state["steps"]
        out: list[str] = []
        cache: dict = {}
        panel_count = 0
        # ① 웹툰 장면 조각(앞에서부터 고르게 최대 3장 — 원작 그림체 참고, 먼저).
        panels = _story_panels(steps, owner, pid)
        for file_id, index in _spread(panels, 3):
            if len(out) >= 4:
                break
            try:
                out.append(images.jpeg_for_reference(images.panel_jpeg(owner, pid, file_id, index, cache)))
                panel_count += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.anime.panel_ref_failed file=%s err=%s", file_id, exc)
        # ② ③ 캐릭터 그림 1장(준비된 것 중 첫 번째).
        for item in steps["3"].get("characters") or []:
            if len(out) >= 4:
                break
            image = item.get("image") or {}
            key = image.get("key") if image.get("status") == "ready" else ""
            if not key:
                continue
            try:
                out.append(images.keyframe_base64(key))
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.anime.char_ref_failed key=%s err=%s", key, exc)
            break  # 캐릭터 그림은 1장만.
        refs = out[:4]
        return refs, min(panel_count, len(refs))

    def _run_anime_job(owner: str, pid: str, plan_id: str, shot_id: str, claim: str, user: str) -> None:
        """선점(preparing+claim)된 anime 하나를 **mutator 밖에서** 실제 제출한다(016 수정 2026-10-20).

        mutate_state 가 저장에 성공한 뒤 _start_anime_jobs 가 스레드로 부른다. 가짜 경합으로 mutator 가
        여러 번 돌아도 선점은 성공한 마지막 시도의 것 하나뿐이고, 그 선점에 대해서만 이 함수가 뜬다.
        순서:
          1) state 를 다시 읽어 그 샷의 anime.claim 이 내 claim 이고 status 가 preparing 일 때만 진행한다.
             아니면(경합으로 버려진 선점·취소·재생성) 아무것도 하지 않는다.
          2) 다운로드·ffmpeg·fal.submit_edit 를 여기(mutator 밖)서 한다.
          3) 제출 성공 직후 원장을 1회 계상한다(키 = clientRequestToken — 중복 키는 원장이 한 번만 계상).
          4) 결과 기록은 짧은 mutate_state 로: 다시 claim 확인 → submitted(작업 정보) 또는 failed.
             claim 이 더는 내 것이 아니면(취소·재생성·stale) 결과를 버린다.
        """
        import os
        import tempfile

        from .. import stitcher

        def _find_anime(state: dict) -> dict | None:
            for shot in state["steps"]["4"].get("shots") or []:
                if str(shot.get("id") or "") == shot_id:
                    anime = shot.get("anime")
                    return anime if isinstance(anime, dict) else None
            return None

        try:
            state, _ = store.load_state(owner, pid, plan_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.anime.load_failed plan=%s shot=%s err=%s", plan_id, shot_id, exc)
            return
        anime0 = _find_anime(state)
        if not anime0 or anime0.get("claim") != claim or anime0.get("status") != "preparing":
            # 경합으로 버려진 선점(다른 claim 이 이겼거나 취소·재생성으로 사라짐) — 아무것도 하지 않는다.
            return

        source_key = str(anime0.get("sourceOutputKey") or "")
        if not source_key:
            # 선점 시 sourceOutputKey 를 함께 넣는다 — 없으면 실사 출력이 사라진 것.
            shot0 = next(
                (s for s in state["steps"]["4"].get("shots") or [] if str(s.get("id") or "") == shot_id),
                {},
            )
            source_key = str(shot0.get("outputKey") or "")

        def _fail(body: dict) -> None:
            def mutator(inner: dict):
                anime = _find_anime(inner)
                if not anime or anime.get("claim") != claim or anime.get("status") != "preparing":
                    return
                anime["status"] = "failed"
                anime["sourceOutputKey"] = source_key
                anime["error"] = body
                anime["note"] = None
            try:
                store.mutate_state(owner, pid, plan_id, mutator)
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.anime.fail_save plan=%s shot=%s err=%s", plan_id, shot_id, exc)

        if not source_key:
            _fail({
                "where": "routes.anime", "type": "NoSource",
                "message": "변환할 실사 영상이 없습니다.", "requestId": "", "httpStatus": 409, "at": now_iso(),
            })
            return

        try:
            raw = store.STORE.get_bytes(source_key, bucket=settings.VIDEO_BUCKET)
        except Exception as exc:  # noqa: BLE001
            _fail(envelope("routes.anime", exc))
            return

        try:
            with tempfile.TemporaryDirectory(prefix="vpoc-anime-") as workdir:
                src = os.path.join(workdir, "src.mp4")
                with open(src, "wb") as handle:
                    handle.write(raw)
                prepared_path = os.path.join(workdir, "prepared.mp4")
                try:
                    prepared = stitcher.prepare_for_anime(src, prepared_path, workdir)
                except stitcher.AnimeTooShort:
                    _fail({
                        "where": "routes.anime", "type": "AnimeTooShort",
                        "message": "3초보다 짧아 변환할 수 없습니다",
                        "requestId": "", "httpStatus": 400, "at": now_iso(),
                    })
                    return
                with open(prepared["path"], "rb") as handle:
                    video_mp4 = handle.read()
                seconds = float(prepared.get("seconds") or 0) or float(
                    anime0.get("durationS") or settings.DURATION_S
                )

                references, panel_count = _anime_reference_b64(state, owner, pid)
                # 073 R8: 패널(그림체 기준) 1..P, 인물 그림(identity only) P+1.. — 보낸 순서와 번호가 같다.
                prompt = _anime_prompt(panel_count, identity_count=len(references) - panel_count)
                output_prefix = _anime_output_prefix(source_key)
                job = fal.submit_edit(
                    endpoint=_anime_endpoint(),
                    video_mp4=video_mp4,
                    prompt=prompt,
                    output_prefix=output_prefix,
                    reference_jpegs_b64=references,
                    keep_audio=True,
                )
        except VpocError as exc:
            _fail(envelope("fal.submit_edit", exc))
            return
        except Exception as exc:  # noqa: BLE001
            _fail(envelope("fal.submit_edit", exc))
            return

        # 제출 성공: 초 × 단가를 1회 계상(키 = 작업 토큰). 키가 clientRequestToken 이라 어떤 이유로든
        # 같은 토큰이 두 번 와도 원장은 한 번만 계상한다.
        ledger.charge(
            job["clientRequestToken"],
            kind="video",
            model=settings.VPOC_ANIME_MODEL,
            units=seconds,
            usd=settings.anime_usd(seconds),
            user=user,
            project_id=pid,
            plan_id=plan_id,
        )

        def record(inner: dict):
            anime = _find_anime(inner)
            if not anime or anime.get("claim") != claim or anime.get("status") != "preparing":
                # 그 사이 취소·재생성·stale 로 선점이 사라졌다 — 결과를 버린다(원장은 그대로 둔다).
                return
            anime.update(
                {
                    "status": "submitted",
                    "mode": "anime",
                    "provider": job["provider"],
                    "invocationArn": job["invocationArn"],
                    "outputPrefix": job["outputPrefix"],
                    "submittedAt": job["submittedAt"],
                    "sourceOutputKey": source_key,
                    "seconds": round(seconds, 2),
                    "note": None,
                    "error": None,
                    "lastPolledAt": "",
                    "outputKey": "",
                }
            )

        try:
            store.mutate_state(owner, pid, plan_id, record)
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.anime.record_save plan=%s shot=%s err=%s", plan_id, shot_id, exc)

    def _start_anime_jobs(claims: list[dict]) -> None:
        """mutate_state 가 성공한 뒤 선점 목록을 백그라운드 스레드로 실제 제출한다(016 수정 2026-10-20).

        mutator 안에서 스레드를 띄우면 재시도 때 두 번 뜨므로, 반드시 저장 성공 뒤에만 부른다.
        """
        for claim in claims or []:
            threading.Thread(
                target=_run_anime_job,
                args=(
                    claim["owner"],
                    claim["pid"],
                    claim["planId"],
                    claim["shotId"],
                    claim["claim"],
                    claim["user"],
                ),
                name=f"vpoc-anime-{claim.get('shotId')}",
                daemon=True,
            ).start()

    def _sync_anime_targets(state: dict) -> None:
        """dual 회차에서 완료된 실사 샷마다 anime 를 queued 로 준비하고, 실사가 바뀌면 다시 queued.

        - 실사 샷이 completed 이고 anime 가 없으면 anime = {status:"queued", sourceOutputKey}.
        - 실사 outputKey 가 anime.sourceOutputKey 와 다르면(재생성·선택으로 바뀜) 옛 결과를 버리고
          다시 queued. 실사가 completed 가 아니면 anime 를 두지 않는다(실사 실패면 anime 없음).
        - preparing(선점)이 15분 넘게 그대로면(파드 재시작 등으로 제출 스레드가 사라진 것) 선점을
          해제하고 다시 queued 로 되돌린다(016 수정 2026-10-20). claim 을 지워 옛 제출 스레드가
          뒤늦게 깨어나도 claim 확인에서 버려지게 한다.
        """
        if not state["steps"]["4"].get("dual"):
            return
        for shot in state["steps"]["4"].get("shots") or []:
            if shot.get("status") != "completed" or not shot.get("outputKey"):
                # 실사가 완료가 아니면 anime 를 만들지 않는다(실패·취소면 버린다).
                if shot.get("anime") and shot["anime"].get("status") in ("queued",):
                    shot.pop("anime", None)
                continue
            anime = shot.get("anime")
            source = str(shot.get("outputKey") or "")
            if not isinstance(anime, dict):
                shot["anime"] = {"status": "queued", "sourceOutputKey": source, "note": None, "error": None}
                continue
            # 선점(preparing)이 15분 넘게 그대로면 제출 스레드가 사라진 것 — 선점 해제 후 다시 queued.
            if anime.get("status") == "preparing":
                claimed = str(anime.get("claimedAt") or "")
                if claimed and _elapsed_s(claimed) >= _ANIME_PREPARE_STALE_S:
                    shot["anime"] = {"status": "queued", "sourceOutputKey": source, "note": None, "error": None}
                    continue
            # 실사가 바뀌면(재생성·선택) 옛 anime 를 버리고 다시 queued.
            if anime.get("sourceOutputKey") and anime.get("sourceOutputKey") != source:
                shot["anime"] = {"status": "queued", "sourceOutputKey": source, "note": None, "error": None}

    def refresh_shots(state: dict, owner: str, pid: str, plan_id: str, user: str) -> bool:
        """진행 중 샷 상태를 갱신하고, 대기 샷을 제출한다. 바뀐 것이 있으면 True.

        016 수정 2026-10-20: queued anime 는 여기(mutator 안)서 **선점만** 한다(preparing+claim).
        실제 제출(다운로드·ffmpeg·fal.submit_edit·원장)은 mutate_state 가 저장에 성공한 뒤 호출부가
        _claims_take()·_start_anime_jobs 로 백그라운드에서 한다. 저장 경합으로 mutator 가 다시 돌면
        이전 시도의 선점 목록은 버려야 하므로 매 호출 시작에서 _claims_reset() 한다.
        """
        _claims_reset()
        step4 = state["steps"]["4"]
        shots = step4.get("shots") or []
        if not shots:
            return False
        changed = False

        def poll_target(target: dict, shot_id: str) -> bool:
            """제출된 작업 하나의 상태를 갱신한다.

            `target` 은 샷 본체이거나 `shot.regen` 이다. 둘이 같은 규칙(10초 1회 · 출력 찾기 ·
            E5 자동 대체)을 타야 하므로 본문을 여기 한 곳에 둔다.
            """
            if target.get("status") != "submitted" or not target.get("invocationArn"):
                return False
            last = target.get("lastPolledAt") or ""
            if last and _elapsed_s(last) < settings.POLL_MIN_INTERVAL_S:
                return False
            try:
                if ark.is_ark_job(target["invocationArn"]):
                    # 완료면 결과를 우리 버킷으로 옮긴 뒤 Completed 를 준다 — 아래 출력 찾기가 그대로 동작한다.
                    result = ark.poll(target["invocationArn"], target.get("outputPrefix") or "")
                else:
                    result = video.poll(target["invocationArn"])
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.poll.failed shot=%s err=%s", shot_id, exc)
                return False
            target["lastPolledAt"] = now_iso()
            status = result["status"]
            # 오류 위치 표시. Seedance 실패가 luma.* 로 보여 원인을 헷갈리게 했다(2026-10-07).
            where = "ark" if ark.is_ark_job(target["invocationArn"]) else "luma"
            if where == "ark" and result.get("usage") and status in ("Completed", "Failed"):
                # BytePlus 가 알려 준 토큰. 작업 하나에 한 번만 남는다(키 = 작업 식별자).
                try:
                    ledger.record_video_tokens(
                        target["invocationArn"] + "-usage",
                        model=ark.model_id(str(state["steps"]["4"].get("videoModel") or "")),
                        usage=result["usage"],
                        user=user,
                        project_id=pid,
                        plan_id=plan_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("vpoc.ledger.tokens.failed shot=%s err=%s", shot_id, exc)
            if status == "Completed":
                found = video.find_output_mp4(
                    target.get("outputPrefix") or "", target.get("invocationArn") or ""
                )
                if found:
                    target["status"] = "completed"
                    target["outputKey"] = found["key"]
                    target["outputBytes"] = found["size"]
                    target["completedAt"] = now_iso()
                    target["error"] = None
                else:
                    target["status"] = "failed"
                    target["completedAt"] = now_iso()
                    target["error"] = {
                        "where": f"{where}.output",
                        "type": "OutputMissing",
                        "message": f"출력 접두사에 .mp4 가 없습니다: {target.get('outputPrefix')}",
                        "requestId": "",
                        "httpStatus": 200,
                        "at": now_iso(),
                    }
            elif status == "Failed":
                target["completedAt"] = now_iso()
                body = {
                    "where": "ark.poll" if where == "ark" else "luma.get_async_invoke",
                    "type": "AsyncInvokeFailed",
                    "message": result.get("failureMessage") or "실패 원문이 비어 있습니다.",
                    "requestId": "",
                    "httpStatus": 200,
                    "at": now_iso(),
                }
                if target.get("mode") == "keyframe":
                    # E5: 키프레임 샷 실패는 텍스트 전용으로 1회 자동 재제출한다.
                    target["status"] = "queued"
                    target["mode"] = "text"
                    target["attempt"] = int(target.get("attempt") or 1) + 1
                    target["note"] = "키프레임이 막혀 텍스트로 다시 만듦"
                    target["error"] = body
                    target["invocationArn"] = ""
                    target["submittedAt"] = None
                else:
                    target["status"] = "failed"
                    target["error"] = body
            return True

        for shot in shots:
            for target in regen.poll_targets(shot):
                changed = poll_target(target, shot.get("id") or "") or changed

        # dual(실사+애니, 설계자 2026-10-20): 실사가 끝난 샷마다 anime 를 준비(queued)하고,
        # 제출된 anime 는 실사 샷과 같은 규칙(poll_target)으로 조회한다. 실사가 재생성·선택으로
        # 바뀌면 _sync_anime_targets 가 anime 를 다시 queued 로 되돌린다(옛 결과는 버린다).
        if step4.get("dual"):
            _sync_anime_targets(state)
            for shot in shots:
                anime = shot.get("anime")
                if isinstance(anime, dict) and anime.get("status") == "submitted":
                    changed = poll_target(anime, (shot.get("id") or "") + ":anime") or changed

        # 동시 한도는 샷 본체와 재생성이 함께 쓴다.
        def all_targets() -> list[tuple[dict, dict]]:
            pairs: list[tuple[dict, dict]] = []
            for item in shots:
                for target in regen.poll_targets(item):
                    pairs.append((item, target))
            return pairs

        inflight = sum(1 for _, target in all_targets() if target.get("status") == "submitted")
        limit = (
            settings.ARK_MAX_INFLIGHT
            # 073 R11.4: 미등록 fal 회차도 외부 공급자 한도로 센다(is_external 은 등록 여부를 보지 않는다).
            if ark.is_external(str(state["steps"]["4"].get("videoModel") or ""))
            else settings.MAX_INFLIGHT
        )
        briefing = str(state["steps"]["4"].get("briefing") or "")
        wait_s = 1200 if state["steps"]["3"].get("makeMode") in ("keyframes", "single") else 300
        if briefing and _elapsed_s(briefing) < wait_s:
            # 장면 설명을 쓰는 중(generate 직후 1분 안팎). 다 쓰면 그 스레드가 제출한다.
            for _, target in all_targets():
                if target.get("status") == "queued":
                    target["note"] = "웹툰 장면 설명 준비 중"
            limit = 0
        for shot, target in all_targets():
            if target.get("status") != "queued":
                continue
            if inflight >= limit:
                target["note"] = target.get("note") or "동시 제출 한도로 대기 중"
                continue
            if target is shot:
                submit_shot(state, owner, pid, plan_id, user, shot)
            else:
                submit_shot(
                    state,
                    owner,
                    pid,
                    plan_id,
                    user,
                    shot,
                    target=target,
                    prompt_en=target.get("promptEn") or "",
                    want_keyframe=bool(target.get("useKeyframe")),
                )
            changed = True
            if target.get("status") == "submitted":
                inflight += 1

        # dual: 준비된(queued) anime 를 **선점만** 한다(016 수정 2026-10-20). mutator 안에서는
        # 외부 호출을 하지 않는다 — status=preparing, claim, claimedAt 로 바꾸고 선점 목록에 담는다.
        # 저장 성공 뒤 호출부가 _start_anime_jobs 로 실제 제출(다운로드·ffmpeg·fal.submit_edit)을 한다.
        # 변환 키가 없으면 선점하지 않고 즉시 실패로 둔다(실사는 막지 않는다 — 설계자 2026-10-20).
        if step4.get("dual"):
            for shot in shots:
                anime = shot.get("anime")
                if not (isinstance(anime, dict) and anime.get("status") == "queued"):
                    continue
                if not settings.FAL_KEY:
                    anime["status"] = "failed"
                    anime["error"] = {
                        "where": "routes.anime", "type": "FalKeyMissing",
                        "message": "변환 키 없음: 관리자에게 FAL_KEY 설정을 요청해 주세요.",
                        "requestId": "", "httpStatus": 409, "at": now_iso(),
                    }
                    anime["sourceOutputKey"] = str(shot.get("outputKey") or "")
                    anime["note"] = None
                    changed = True
                    continue
                claim = secrets.token_hex(6)
                anime["status"] = "preparing"
                anime["claim"] = claim
                anime["claimedAt"] = now_iso()
                anime["sourceOutputKey"] = str(shot.get("outputKey") or "")
                anime["note"] = None
                anime["error"] = None
                _claims_add(
                    {
                        "owner": owner,
                        "pid": pid,
                        "planId": plan_id,
                        "shotId": str(shot.get("id") or ""),
                        "claim": claim,
                        "user": user,
                    }
                )
                changed = True

        gen_id_now = str(step4.get("genId") or "")
        for shot in shots:
            changed = regen.sync_versions(shot, gen_id_now) or changed

        # ④ 전체 상태는 **샷 본체만** 본다. 재생성이 진행 중이어도 ④ 상태가 바뀌지 않는다(G2).
        # anime(변환)는 실사 완료를 막지 않도록 ④ 상태·완료 판정에 넣지 않는다(설계자 2026-10-20).
        statuses = {shot.get("status") for shot in shots}
        if statuses & {"queued", "submitted"}:
            step4["status"] = "running"
        elif "cancelled" in statuses:
            # 취소된 샷이 있고 진행 중(queued/submitted)이 없으면 ④ 는 취소 상태로 둔다(설계자 2026-10-20).
            # done 으로 덮어 완료 처리(finish_plan)로 넘어가지 않게 한다. final 은 그대로.
            step4["status"] = "cancelled"
        elif "failed" in statuses:
            step4["status"] = "partial"
        else:
            step4["status"] = "done"

        # 취소된 샷이 있으면 완료 처리를 하지 않는다 — 취소한 회차를 확정본으로 남기지 않는다.
        # (완료 판정은 실사 기준이다 — anime 는 실사 완료 처리에 영향을 주지 않는다.)
        if not (statuses & {"queued", "submitted", "cancelled"}):
            changed = finish_plan(state, owner, pid, plan_id) or changed
        return changed

    def finish_plan(state: dict, owner: str, pid: str, plan_id: str) -> bool:
        """생성 회차 하나의 완료 처리. 회차마다 한 번만 한다.

        `final.json` 을 If-None-Match 로만 쓰던 탓에, 2차 생성이 끝나도 1차 기록이 그대로 남고
        `project.json`·Neptune 갱신 코드에 도달하지 않았다(2026-09-23,
        docs/vpoc1/fix-regen-reload.md). 이제 회차마다 `finals/{UTC}.json` 을 새로 쓰고
        `project.json` 과 Neptune 은 **항상** 갱신한다. `final.json` 은 옛 데이터 호환용으로
        없을 때만 만든다.
        """
        step3 = state["steps"]["3"]
        step4 = state["steps"]["4"]
        done = [shot for shot in step4.get("shots", []) if shot.get("status") == "completed"]
        if not done:
            return False
        if state.get("finalizedAt"):
            return False

        record = {
            "planId": plan_id,
            "genId": step4.get("genId") or "",
            "projectId": pid,
            "owner": owner,
            "completedAt": now_iso(),
            "model": (
                ark.model_id(step4.get("videoModel") or "")
                if ark.is_ark(step4.get("videoModel") or "")
                else settings.VIDEO_MODEL
            ),
            "provider": step4.get("videoModel") or video.PROVIDER,
            "params": {
                "resolution": step4.get("resolution")
                or settings.default_resolution(step4.get("videoModel") or ""),
                "durationsS": [int(item.get("durationS") or settings.DURATION_S) for item in step4.get("shots", [])],
                "aspect": settings.ASPECT,
            },
            "style": (state["steps"]["2"].get("story") or {}).get("style"),
            "settingEn": (state["steps"]["2"].get("story") or {}).get("settingEn"),
            "costUsd": round(
                sum(
                    settings.shot_usd(
                        step4.get("videoModel") or "",
                        step4.get("resolution") or "",
                        item.get("durationS"),
                    )
                    for item in step4.get("shots", [])
                ),
                2,
            ),
            "shots": [
                {
                    "id": shot.get("id"),
                    "status": shot.get("status"),
                    "attempt": shot.get("attempt"),
                    "mode": shot.get("mode"),
                    "invocationArn": shot.get("invocationArn"),
                    "outputKey": shot.get("outputKey"),
                    "promptEn": next(
                        (
                            item.get("promptEn")
                            for item in step3.get("shots", [])
                            if item.get("id") == shot.get("id")
                        ),
                        "",
                    ),
                    "summaryKo": next(
                        (
                            item.get("summaryKo")
                            for item in step3.get("shots", [])
                            if item.get("id") == shot.get("id")
                        ),
                        "",
                    ),
                    "submittedAt": shot.get("submittedAt"),
                    "completedAt": shot.get("completedAt"),
                    "error": shot.get("error"),
                }
                for shot in step4.get("shots", [])
            ],
            "characters": state["steps"]["2"].get("characters", []),
            "characterImages": step3.get("characters", []),
            "input": state["steps"]["1"],
        }
        # 회차 기록. 키에 UTC 시각이 들어가므로 회차마다 새 파일이고, 같은 회차를 두 파드가
        # 동시에 끝내도 create_only 로 한쪽만 쓴다.
        finals_key = store.finals_key(owner, pid, plan_id, record["completedAt"])
        try:
            store.STORE.write_json(finals_key, record, create_only=True)
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise
            log.info("vpoc.finals.exists key=%s", finals_key)
        # 옛 데이터 호환용. 이미 있으면 1차 기록을 그대로 둔다 — 정본은 finals/ 최신이다.
        try:
            store.STORE.write_json(
                store.final_key(owner, pid, plan_id), record, create_only=True
            )
        except VpocError as exc:
            if exc.kind != "PreconditionFailed":
                raise

        thumb_key = next(
            (
                (item.get("image") or {}).get("key")
                for item in step3.get("characters", [])
                if (item.get("image") or {}).get("key")
            ),
            "",
        )
        project_doc = {
            "projectId": pid,
            "owner": owner,
            "planId": plan_id,
            "updatedAt": now_iso(),
            "style": record["style"],
            "settingEn": record["settingEn"],
            "settingKo": (state["steps"]["2"].get("story") or {}).get("settingKo"),
            "titleKo": (state["steps"]["2"].get("story") or {}).get("titleKo"),
            "characters": [
                {
                    **character,
                    "imageKey": next(
                        (
                            (item.get("image") or {}).get("key")
                            for item in step3.get("characters", [])
                            if item.get("id") == character.get("id")
                        ),
                        "",
                    ),
                    "imageSource": next(
                        (
                            (item.get("image") or {}).get("source")
                            for item in step3.get("characters", [])
                            if item.get("id") == character.get("id")
                        ),
                        None,
                    ),
                }
                for character in state["steps"]["2"].get("characters", [])
            ],
        }
        # 확정본과 그래프 속성은 **회차마다 항상** 갱신한다. 화풍을 바꿔 다시 만들면
        # project.json 의 style 도 따라가야 한다.
        store.STORE.write_json(store.project_key(owner, pid), project_doc)
        update_graph(pid, "done", plan_id, finals_key, thumb_key)
        state["finalizedAt"] = now_iso()
        log.info(
            "vpoc.finalized plan=%s gen=%s shots=%d finals=%s",
            plan_id,
            step4.get("genId") or "-",
            len(done),
            finals_key,
        )
        return True

    def update_graph(pid: str, status: str, plan_id: str, final_key: str, thumb_key: str) -> None:
        """project 정점의 속성 5개만 갱신한다. 새 정점·간선은 만들지 않는다(PRD 6.3)."""
        try:
            deps.graph_run(
                "MATCH (p:project {id: $id}) "
                "SET p.vpocStatus = $status, p.vpocPlanId = $planId, "
                "p.vpocFinalKey = $finalKey, p.vpocThumbKey = $thumbKey, "
                "p.vpocUpdatedAt = $at RETURN p.id AS id",
                {
                    "id": pid,
                    "status": status,
                    "planId": plan_id,
                    "finalKey": final_key,
                    "thumbKey": thumb_key,
                    "at": now_iso(),
                },
            )
        except Exception as exc:  # noqa: BLE001
            # 메인 목록 배지가 늦게 붙는 것은 영상 완료를 막을 이유가 아니다.
            log.warning("vpoc.graph.update_failed pid=%s err=%s", pid, exc)

    # ---------------------------------------------------------------- 라우트

    @bp.get("/p/<pid>")
    @deps.require_auth
    @enveloped("routes.project_view")
    def project_view(pid: str):
        project = project_for(pid, write=False)
        owner = owner_of(project)
        can_edit = deps.can_write_project(project)

        plan_ids = [
            item.rstrip("/").rsplit("/", 1)[-1]
            for item in store.STORE.list_prefixes(store.plans_prefix(owner, pid))
        ]
        videos = []
        active_plan_id = None
        latest_state: dict | None = None
        for plan_id in sorted(plan_ids, reverse=True):
            # vpoc-regen: 버전을 바꿨으면 `finals/` 최신이 정본이다. 없으면 `final.json`.
            record = regen.latest_final(owner, pid, plan_id)
            if record:
                shots = [
                    {
                        "id": shot.get("id"),
                        "summaryKo": shot.get("summaryKo", ""),
                        "videoUrl": (
                            video.video_url(shot["outputKey"]) if shot.get("outputKey") else None
                        ),
                    }
                    for shot in record.get("shots", [])
                    if shot.get("status") == "completed"
                ]
                final_mp4 = store.final_mp4_key(owner, pid, plan_id)
                videos.append(
                    {
                        "planId": plan_id,
                        "completedAt": record.get("completedAt"),
                        "finalUrl": (
                            store.STORE.presign(final_mp4, content_type="video/mp4")
                            if store.STORE.exists(final_mp4)
                            else None
                        ),
                        "shots": shots,
                    }
                )
                if active_plan_id is None:
                    active_plan_id = plan_id
            elif latest_state is None:
                state, _ = store.STORE.read_json(store.state_key(owner, pid, plan_id))
                if state:
                    latest_state = state
                    if active_plan_id is None:
                        active_plan_id = plan_id

        confirmed, _ = store.STORE.read_json(store.project_key(owner, pid))
        if confirmed:
            characters = {
                "status": "final",
                "items": [
                    {
                        "id": item.get("id"),
                        "nameKo": item.get("nameKo"),
                        "nameEn": item.get("nameEn"),
                        "image": {
                            "url": (
                                store.STORE.presign(item["imageKey"], content_type="image/jpeg")
                                if item.get("imageKey")
                                else None
                            ),
                            "source": item.get("imageSource"),
                        },
                        "sheet": {
                            "roleKo": item.get("roleKo", ""),
                            "appearanceKo": item.get("appearanceKo", ""),
                            "bodyKo": item.get("bodyKo", ""),
                            "personalityKo": item.get("personalityKo", ""),
                            "traitsKo": item.get("traitsKo", ""),
                            "likesKo": item.get("likesKo", ""),
                        },
                    }
                    for item in confirmed.get("characters", [])
                ],
            }
        elif latest_state:
            step2 = latest_state["steps"]["2"]
            step3_by_id = {
                item.get("id"): item for item in latest_state["steps"]["3"].get("characters", [])
            }
            characters = {
                "status": "in_progress",
                "items": [
                    {
                        "id": item.get("id"),
                        "nameKo": item.get("nameKo"),
                        "nameEn": item.get("nameEn"),
                        "image": {
                            "url": (
                                store.STORE.presign(
                                    ((step3_by_id.get(item.get("id")) or {}).get("image") or {})[
                                        "key"
                                    ],
                                    content_type="image/jpeg",
                                )
                                if ((step3_by_id.get(item.get("id")) or {}).get("image") or {}).get(
                                    "key"
                                )
                                else None
                            ),
                            "source": (
                                (step3_by_id.get(item.get("id")) or {}).get("image") or {}
                            ).get("source"),
                        },
                        "sheet": {
                            "roleKo": item.get("roleKo", ""),
                            "appearanceKo": item.get("appearanceKo", ""),
                            "bodyKo": item.get("bodyKo", ""),
                            "personalityKo": item.get("personalityKo", ""),
                            "traitsKo": item.get("traitsKo", ""),
                            "likesKo": item.get("likesKo", ""),
                        },
                    }
                    for item in step2.get("characters", [])
                ],
            }
        else:
            characters = {"status": "none", "items": []}

        inputs = []
        prompts = []
        # 이 프로젝트의 영상 계획 전체(진행 중 포함). 프로젝트 상세 화면이 목록으로 보인다(설계자 2026-10-08).
        plans: list[dict] = []
        for plan_id in sorted(plan_ids, reverse=True):
            state, _ = store.STORE.read_json(store.state_key(owner, pid, plan_id))
            if not state:
                continue
            step1 = _step1_view(state["steps"]["1"], owner, pid)
            files = list(step1.get("sceneFiles") or [])
            for card in step1.get("characters") or []:
                files.extend(card.get("files") or [])
            inputs.append(
                {
                    "planId": plan_id,
                    "savedAt": state.get("createdAt"),
                    "text": step1.get("text", ""),
                    "characters": [
                        {"name": card.get("name", ""), "description": card.get("description", "")}
                        for card in step1.get("characters") or []
                    ],
                    "files": files,
                }
            )
            for shot in state["steps"]["3"].get("shots", []):
                if shot.get("promptEn"):
                    prompts.append(
                        {"planId": plan_id, "shotId": shot.get("id"), "promptEn": shot["promptEn"]}
                    )
            plans.append(_plan_summary(state, plan_id))

        return jsonify(
            {
                "ok": True,
                "projectId": pid,
                "title": project.get("title") or "",
                "visibility": project.get("visibility") or "private",
                "canEdit": can_edit,
                "activePlanId": active_plan_id,
                "videos": videos,
                "characters": characters,
                "inputs": inputs,
                "prompts": prompts,
                "plans": plans,
            }
        )

    def _prepare_briefs(owner: str, pid: str, plan_id: str, user: str) -> None:
        """generate 직후: 샷마다 맡을 웹툰 장면을 나누고, Claude 가 장면 설명을 쓴 뒤 제출한다."""
        try:
            state0, _ = store.load_state(owner, pid, plan_id)
        except Exception:  # noqa: BLE001
            state0 = None
        if state0 and state0["steps"]["3"].get("makeMode") == "single":
            # 영상 1개(single, 설계자 2026-10-19): 키 장면 그림을 완성하지 않는다(영상에 넣지 않는다).
            # 승인 시 미리보기가 stale 여도 막지 않는다 — 단 배경 그림이 없으면 먼저 그린다.
            master = state0["steps"]["3"].get("master") or {}
            bg = master.get("background") or {}
            drawn = None
            if not bg.get("imageKey"):
                drawn = _draw_background(owner, pid, plan_id, user, master)

            def single_mutator(inner: dict):
                imaster = inner["steps"]["3"].get("master") or {}
                if drawn and drawn.get("status") == "ready":
                    imaster.setdefault("background", {})
                    imaster["background"]["imageKey"] = drawn.get("key") or ""
                    imaster["background"]["status"] = "ready"
                    imaster["background"]["error"] = None
                step4 = inner["steps"]["4"]
                step4.pop("briefing", None)
                for item in step4.get("shots") or []:
                    if item.get("note") == "웹툰 장면 설명 준비 중":
                        item["note"] = None
                refresh_shots(inner, owner, pid, plan_id, user)

            try:
                store.mutate_state(owner, pid, plan_id, single_mutator)
                _start_anime_jobs(_claims_take())
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.single.save_failed plan=%s err=%s", plan_id, exc)
            return
        if state0 and state0["steps"]["3"].get("makeMode") == "keyframes":
            # 옛 keyframes: 완성 키 장면을 먼저 그린 뒤 제출한다.
            finals = _finalize_keyframes(owner, pid, plan_id, user, state0)

            def kf_mutator(inner: dict):
                for item in inner["steps"]["3"].get("keyframes") or []:
                    if item["id"] in finals:
                        item["final"] = finals[item["id"]]
                step4 = inner["steps"]["4"]
                step4.pop("briefing", None)
                for item in step4.get("shots") or []:
                    if item.get("note") == "웹툰 장면 설명 준비 중":
                        item["note"] = None
                refresh_shots(inner, owner, pid, plan_id, user)

            try:
                store.mutate_state(owner, pid, plan_id, kf_mutator)
                _start_anime_jobs(_claims_take())
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.keyframe.save_failed plan=%s err=%s", plan_id, exc)
            return
        panels_by_shot: dict = {}
        briefs: dict = {}
        try:
            state, _ = store.load_state(owner, pid, plan_id)
            steps = state["steps"]
            step4 = steps["4"]
            plan_shots = steps["3"].get("shots") or []
            ids = [item["id"] for item in step4.get("shots") or []]
            panels = _story_panels(steps, owner, pid)
            if step4.get("oneTake"):
                panels_by_shot = {ark.ONE_TAKE_ID: panels}
            else:
                panels_by_shot = _assign_panels(panels, ids)
            story = steps["2"].get("story") or {}
            story_text = " ".join(
                str(story.get(field) or "") for field in ("titleKo", "loglineKo", "summaryKo", "synopsisKo")
            ).strip() or str(steps["1"].get("text") or "")
            by_id = {item.get("id"): item for item in plan_shots}
            tile_cache: dict = {}

            def one(shot4: dict) -> None:
                shot_id = shot4["id"]
                plan_shot = by_id.get(shot_id)
                if shot_id == ark.ONE_TAKE_ID:
                    plan_shot = _one_take(steps, str(step4.get("videoModel") or ""))
                if not plan_shot:
                    return
                picked = _spread(panels_by_shot.get(shot_id) or [], settings.PANELS_PER_SHOT_BRIEF)
                panel_bytes = [images.panel_jpeg(owner, pid, f, i, tile_cache) for f, i in picked]
                chars = []
                for _, handle, key, _ in _reference_characters(steps, plan_shot)[:4]:
                    try:
                        chars.append((handle, store.STORE.get_bytes(key)))
                    except Exception:  # noqa: BLE001
                        continue
                if not panel_bytes and not chars:
                    return
                brief, usage = llm.scene_brief(
                    panel_bytes,
                    chars,
                    shot_text=" ".join(
                        str(plan_shot.get(field) or "")
                        for field in ("summaryKo", "locationEn", "actionEn", "cameraEn", "promptEn")
                    ),
                    story_text=story_text,
                    seconds=int(shot4.get("durationS") or settings.DURATION_S),
                )
                ledger.charge_text(
                    f"brief-{plan_id}-{shot_id}-{secrets.token_hex(3)}",
                    input_tokens=int(usage.get("inputTokens") or 0),
                    output_tokens=int(usage.get("outputTokens") or 0),
                    user=user,
                    project_id=pid,
                    plan_id=plan_id,
                    model=str(usage.get("model") or ""),
                )
                briefs[shot_id] = llm.brief_text(brief)

            workers = []
            for shot4 in step4.get("shots") or []:
                def run(item=shot4):
                    try:
                        one(item)
                    except Exception as exc:  # noqa: BLE001 - 설명이 없어도 영상은 만든다
                        log.warning("vpoc.brief.failed plan=%s shot=%s err=%s", plan_id, item.get("id"), exc)
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                workers.append(worker)
            for worker in workers:
                worker.join(timeout=280)
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.brief.prepare_failed plan=%s err=%s", plan_id, exc)

        def mutator(inner: dict):
            step4 = inner["steps"]["4"]
            step4["panels"] = panels_by_shot
            step4["briefs"] = dict(briefs)
            step4.pop("briefing", None)
            for item in step4.get("shots") or []:
                if item.get("note") == "웹툰 장면 설명 준비 중":
                    item["note"] = None
            refresh_shots(inner, owner, pid, plan_id, user)

        try:
            store.mutate_state(owner, pid, plan_id, mutator)
            _start_anime_jobs(_claims_take())
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.brief.save_failed plan=%s err=%s", plan_id, exc)

    _DRIVERS: set = set()
    _DRIVERS_LOCK = threading.Lock()

    def _ensure_driver(owner: str, pid: str, plan_id: str, user: str) -> None:
        """④ 를 화면 없이도 끝까지 진행한다(설계자 2026-10-14 점검: 창을 닫으면 다음 샷이 안 나갔다).

        20초마다 상태를 보고, 다른 곳(열린 화면)이 최근 15초 안에 확인했으면 건너뛴다(중복 제출 방지).
        ④ 가 끝나거나 3시간이 지나면 멈춘다. 파드마다 계획당 1개.
        """
        marker = (owner, pid, plan_id)
        with _DRIVERS_LOCK:
            if marker in _DRIVERS:
                return
            _DRIVERS.add(marker)

        def loop() -> None:
            try:
                deadline = time.time() + 3 * 3600
                while time.time() < deadline:
                    time.sleep(20)
                    state, _ = store.load_state(owner, pid, plan_id)
                    step4 = state["steps"]["4"]
                    shots = step4.get("shots") or []
                    busy = [t for item in shots for t in regen.poll_targets(item) if t.get("status") in ("queued", "submitted")]
                    # dual: 변환(anime)도 진행 중이면 드라이버가 계속 돈다(설계자 2026-10-20).
                    # preparing(선점)도 진행 중으로 본다(016 수정 2026-10-20).
                    if step4.get("dual"):
                        for item in shots:
                            anime = item.get("anime")
                            if isinstance(anime, dict) and anime.get("status") in ("queued", "preparing", "submitted"):
                                busy.append(anime)
                    if step4.get("status") != "running" and not busy:
                        return
                    recent = any(
                        t.get("lastPolledAt") and _elapsed_s(t["lastPolledAt"]) < 15
                        for t in busy
                        if t.get("status") == "submitted"
                    )
                    if recent:
                        continue
                    try:
                        store.mutate_state(
                            owner, pid, plan_id, lambda inner: refresh_shots(inner, owner, pid, plan_id, user)
                        )
                        _start_anime_jobs(_claims_take())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("vpoc.driver.refresh_failed plan=%s err=%s", plan_id, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("vpoc.driver.stopped plan=%s err=%s", plan_id, exc)
            finally:
                with _DRIVERS_LOCK:
                    _DRIVERS.discard(marker)

        threading.Thread(target=loop, name=f"vpoc-driver-{plan_id}", daemon=True).start()

    def _submit_queued(owner: str, pid: str, plan_id: str, user: str) -> None:
        try:
            store.mutate_state(
                owner, pid, plan_id, lambda inner: refresh_shots(inner, owner, pid, plan_id, user)
            )
            _start_anime_jobs(_claims_take())
        except Exception as exc:  # noqa: BLE001
            log.warning("vpoc.submit_queued.failed plan=%s err=%s", plan_id, exc)

    STEP_NAMES = {1: "① 입력", 2: "② 확인", 3: "③ 계획 승인", 4: "④ 영상"}

    def _plan_summary(state: dict, plan_id: str) -> dict:
        """프로젝트 화면의 계획 카드 한 장. 저장된 state 만 읽는다(모델 호출 · 상태 갱신 없음)."""
        steps = state["steps"]
        story = steps["2"].get("story") or {}
        step4 = steps["4"]
        by_id = {item.get("id"): item for item in step4.get("shots") or []}
        model = str(step4.get("videoModel") or steps["2"].get("videoModel") or ark.DEFAULT_MODEL)
        shots = []
        take = by_id.get(ark.ONE_TAKE_ID) if (step4.get("oneTake") or step4.get("single")) else None
        for shot in steps["3"].get("shots") or []:
            # 한 번에 만든(single·oneTake) 계획은 샷들이 영상 하나를 같이 쓴다 — 그 영상의 상태를 따른다.
            made = take or by_id.get(shot.get("id")) or {}
            shots.append(
                {
                    "id": shot.get("id"),
                    "summaryKo": shot.get("summaryKo", ""),
                    "durationS": int(shot.get("durationS") or settings.DURATION_S),
                    "status": made.get("status") or ("planned" if steps["3"].get("status") else "none"),
                }
            )
        videos = []
        for item in step4.get("shots") or []:
            if item.get("status") == "completed" and item.get("outputKey"):
                try:
                    url = video.video_url(item["outputKey"])
                except Exception:  # noqa: BLE001
                    url = None
                if url:
                    label = "영상 1개" if item.get("id") == ark.ONE_TAKE_ID else item.get("id")
                    videos.append({"id": item.get("id"), "label": label, "videoUrl": url})
        current = int(state.get("currentStep") or 1)
        text = str((steps["1"] or {}).get("text") or "")
        return {
            "planId": plan_id,
            "createdAt": state.get("createdAt"),
            "title": story.get("titleKo") or (text[:40] + ("…" if len(text) > 40 else "")) or "제목 없음",
            "currentStep": current,
            "stepName": STEP_NAMES.get(current, ""),
            "step4Status": step4.get("status") or "empty",
            "videoModel": model,
            "oneTake": bool(step4.get("oneTake")),
            "single": bool(step4.get("single")),
            "shots": shots,
            "totalSeconds": sum(item["durationS"] for item in shots),
            "videos": videos,
        }

    @bp.post("/p/<pid>/plans")
    @deps.require_auth
    @enveloped("routes.create_plan")
    def create_plan(pid: str):
        project = project_for(pid, write=True)
        owner = owner_of(project)
        plan_id = store.new_id("pl")
        state = store.new_state(owner, pid, plan_id)
        store.STORE.write_json(store.state_key(owner, pid, plan_id), state, create_only=True)
        update_graph(pid, "draft", plan_id, "", "")
        return jsonify({"ok": True, "planId": plan_id})

    @bp.get("/p/<pid>/plans/<plan_id>")
    @deps.require_auth
    @enveloped("routes.get_plan")
    def get_plan(pid: str, plan_id: str):
        project = project_for(pid, write=False)
        owner = owner_of(project)
        can_edit = deps.can_write_project(project)
        state, etag = store.load_state(owner, pid, plan_id)

        changed = stall_check(state)
        if can_edit and state["steps"]["4"].get("status") == "running":
            _ensure_driver(owner, pid, plan_id, deps.current_username())
        if can_edit:
            changed = _repair_review_shots(state) or changed
        if can_edit and state["steps"]["4"].get("shots"):
            try:
                changed = refresh_shots(state, owner, pid, plan_id, deps.current_username()) or changed
            except Exception as exc:  # noqa: BLE001
                log.error("vpoc.refresh.failed plan=%s err=%s", plan_id, exc)
        if changed:
            try:
                store.save_state(owner, pid, plan_id, state, etag)
                # 저장 성공 뒤에만 선점 제출을 띄운다(016 수정 2026-10-20). 경합(PreconditionFailed)이면
                # 이 시도의 선점은 저장되지 않았으므로 버린다 — 다음 조회·드라이버가 다시 선점한다.
                _start_anime_jobs(_claims_take())
            except VpocError as exc:
                if exc.kind != "PreconditionFailed":
                    raise
                _claims_take()  # 저장 실패 → 이 시도의 선점 목록은 버린다.
                state, _ = store.load_state(owner, pid, plan_id)
        else:
            _claims_take()  # 바뀐 것이 없으면 선점도 없다(방어적으로 비운다).
        return jsonify(plan_view(state, project, can_edit=can_edit))

    @bp.post("/p/<pid>/uploads")
    @deps.require_auth
    @enveloped("routes.upload")
    def upload(pid: str):
        project = project_for(pid, write=True)
        owner = owner_of(project)
        handle = request.files.get("file")
        if handle is None:
            raise VpocError("routes.upload", "MissingFile", "file 이 필요합니다.", 400)
        kind = (request.form.get("kind") or "scene").strip()
        if kind not in ("character", "scene"):
            raise VpocError("routes.upload", "BadKind", "kind 는 character 또는 scene 입니다.", 400)
        try:
            char_index = int(request.form.get("charIndex") or -1)
        except ValueError:
            char_index = -1
        meta = images.save_upload(
            owner,
            pid,
            handle.read(),
            name=handle.filename or "image.jpg",
            kind=kind,
            char_index=char_index,
        )
        url = store.STORE.presign(
            store.upload_key(owner, pid, meta["fileId"]), content_type="image/jpeg"
        )
        return jsonify(
            {
                "ok": True,
                "fileId": meta["fileId"],
                "url": url,
                "thumbUrl": url,
                "width": meta["width"],
                "height": meta["height"],
                "name": meta["name"],
            }
        )

    @bp.put("/p/<pid>/plans/<plan_id>/steps/<int:number>")
    @deps.require_auth
    @enveloped("routes.save_step")
    def save_step(pid: str, plan_id: str, number: int):
        if number not in (1, 2, 3):
            raise VpocError("routes.save_step", "BadStep", "저장할 수 있는 단계는 1·2·3 입니다.", 400)
        project = project_for(pid, write=True)
        owner = owner_of(project)
        body = request.get_json(silent=True) or {}

        def mutator(state: dict):
            require_idle(state)
            step = state["steps"][str(number)]
            before = _fingerprint(step, number)
            _apply_step(step, number, body, model=ark.selected(state))
            step["status"] = "saved" if number == 1 else step.get("status") or "saved"
            after = _fingerprint(step, number)
            state["currentStep"] = max(number, 1)
            if before != after:
                store.bump_revision(state, number)
                store.mark_stale(state, number)
                # 뒤 단계는 다시 만들어야 하므로 거기로 건너뛰지 못하게 한다(설계자 2026-10-14 되돌리기 점검:
                # ① 을 고친 뒤 단계 막대로 ④ 에 가면 빈 화면이 나왔다). 앞으로 진행하면 다시 늘어난다.
                state["maxReachedStep"] = number
            else:
                state["maxReachedStep"] = max(int(state.get("maxReachedStep") or 1), number)

        state, _ = store.mutate_state(owner, pid, plan_id, mutator)
        return jsonify(plan_view(state, project, can_edit=True))

    def _fingerprint(step: dict, number: int) -> str:
        import json as _json

        if number == 1:
            return _json.dumps(
                [step.get("text"), step.get("characters"), step.get("sceneFiles")],
                ensure_ascii=False,
                sort_keys=True,
            )
        if number == 2:
            return _json.dumps(
                [
                    step.get("story"),
                    step.get("characters"),
                    step.get("answers"),
                    step.get("extraKo"),
                    # 샷 수를 바꾸면 ③ 계획을 다시 만들어야 한다.
                    int(step.get("shotCount") or 0),
                    step.get("makeMode") or "shots",
                    int(step.get("totalSeconds") or 0),
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
        return _json.dumps(
            [
                step.get("shots"),
                step.get("useKeyframes"),
                step.get("resolution"),
                step.get("oneTake"),
                # 영상 1개(single): 마스터(프롬프트·배경·인물·비트·version)가 바뀌면 ④ 를 다시 만들어야 한다.
                step.get("master"),
            ],
            ensure_ascii=False,
            sort_keys=True,
        )

    def _apply_step(step: dict, number: int, body: dict, *, model: str = "luma-ray2") -> None:
        if number == 1:
            step["text"] = str(body.get("text") or "")
            step["characters"] = [
                {
                    "name": str(card.get("name") or ""),
                    "description": str(card.get("description") or ""),
                    "files": [
                        {"fileId": item.get("fileId"), "name": item.get("name", ""), "kind": "character"}
                        for item in card.get("files") or []
                        if item.get("fileId")
                    ],
                }
                for card in body.get("characters") or []
            ]
            step["sceneFiles"] = [
                {"fileId": item.get("fileId"), "name": item.get("name", ""), "kind": "scene"}
                for item in body.get("sceneFiles") or []
                if item.get("fileId")
            ]
            return
        if number == 2:
            story = dict(step.get("story") or {})
            incoming = body.get("story") or {}
            for field in ("titleKo", "loglineKo", "settingKo", "settingEn", "style", "notesKo"):
                if field in incoming:
                    story[field] = incoming[field]
            story["style"] = compose.style_of(story.get("style") or "live_action")
            step["story"] = story

            by_id = {item.get("id"): item for item in step.get("characters") or []}
            for incoming_char in body.get("characters") or []:
                target = by_id.get(incoming_char.get("id"))
                if not target:
                    continue
                for field in (
                    "nameKo",
                    "nameEn",
                    "roleKo",
                    "appearanceKo",
                    "appearanceEn",
                    "handleEn",
                    "bodyKo",
                    "bodyEn",
                    "personalityKo",
                    "traitsKo",
                    "likesKo",
                    "blPosition",
                ):
                    if field in incoming_char:
                        target[field] = incoming_char[field]
            answers = dict(step.get("answers") or {})
            for key, value in (body.get("answers") or {}).items():
                answers[key] = {
                    "index": int((value or {}).get("index") or 0),
                    "text": (value or {}).get("text") or None,
                }
            step["answers"] = answers
            step["extraKo"] = str(body.get("extraKo") or step.get("extraKo") or "")
            # "몇 개의 샷으로 나눌까요?" (설계자 2026-10-08). 0 = 자동, 아니면 MIN~MAX_SHOTS.
            if "shotCount" in body:
                try:
                    count = int(body.get("shotCount") or 0)
                except (TypeError, ValueError):
                    count = -1
                if count and not settings.MIN_SHOTS <= count <= settings.MAX_SHOTS:
                    raise VpocError(
                        "routes.save_step",
                        "BadShotCount",
                        f"샷 수는 {settings.MIN_SHOTS}~{settings.MAX_SHOTS}개 중에서 골라 주세요.",
                        400,
                    )
                step["shotCount"] = count
            # 제작 방식: single = 영상 1개 한 번에(설계자 2026-10-19, 모델 고르기 켜진 배포의 기본·유일).
            # keyframes · shots = 옛 계획 호환(설계자 2026-10-14/이전). 옛 값은 그대로 받아 그대로 돈다.
            if "makeMode" in body:
                mode = str(body.get("makeMode") or "")
                step["makeMode"] = mode if mode in ("single", "keyframes", "shots") else "shots"
            # 영상 모델은 ③ 계획(샷·이미지)을 바꾸지 않으므로 _fingerprint 에 넣지 않는다 —
            # 모델만 바꿨는데 ③ 을 다시 만들게(이미지 비용) 하지 않으려는 것이다.
            # totalSeconds 검사보다 먼저 본다: 모델과 길이를 같이 바꾸면 이전 모델 범위로 검사되던 버그(설계자 2026-10-20).
            check_model = model
            if settings.MODEL_PICKER_ENABLED and "videoModel" in body:
                wanted = str(body.get("videoModel") or "")
                allowed = {item["id"] for item in ark.choices() if item["available"]}
                if wanted not in allowed:
                    raise VpocError(
                        "routes.save_step", "ModelUnavailable", f"고를 수 없는 영상 모델입니다: {wanted}", 400
                    )
                step["videoModel"] = wanted
                check_model = wanted
            if "totalSeconds" in body:
                # single 은 고른 모델이 한 번에 만들 수 있는 범위(VIDEO_DURATION_RANGE · fal caps)로 검증한다.
                # 옛 계획(keyframes/shots)은 20·30·40·60 선택지 그대로 둔다(데이터 영향 금지).
                # 이번 요청에서 받아들인 모델로 검사한다(위 videoModel 블록이 먼저 돈다).
                options = ark.video_options(check_model)
                low = int(options.get("minDurationS") or settings.DURATION_S)
                high = int(options.get("maxDurationS") or settings.DURATION_S)
                legacy_mode = str(step.get("makeMode") or "") in ("keyframes", "shots")
                try:
                    total = int(body.get("totalSeconds") or settings.KEYFRAME_TOTAL_DEFAULT)
                except (TypeError, ValueError):
                    total = settings.KEYFRAME_TOTAL_DEFAULT
                if legacy_mode:
                    if total not in settings.KEYFRAME_TOTAL_CHOICES:
                        raise VpocError(
                            "routes.save_step",
                            "BadTotalSeconds",
                            f"영상 길이는 {', '.join(str(v) for v in settings.KEYFRAME_TOTAL_CHOICES)}초 중에서 골라 주세요.",
                            400,
                        )
                elif not low <= total <= high:
                    raise VpocError(
                        "routes.save_step",
                        "BadDuration",
                        f"{ark.label(check_model)}는 한 번에 {low}~{high}초만 만들 수 있습니다 (받은 값 {total}).",
                        400,
                    )
                step["totalSeconds"] = total
            user_images = dict(step.get("userImages") or {})
            for key, value in (body.get("userImages") or {}).items():
                if value and value.get("fileId"):
                    user_images[key] = {"fileId": value["fileId"]}
            step["userImages"] = user_images
            return

        # 영상 1개(single, 설계자 2026-10-19): 마스터 프롬프트 수정. 그림이 아니라 프롬프트가 기준이다.
        # 받는 수정: master.background.promptEn|descKo, master.characters[i].promptEn,
        # master.beats[i].actionEn|cameraEn|descKo. 수정이 들어오면 version += 1 하고 영향받는 미리보기를
        # stale 로 표시한다(배경 → 전체, 인물 → 그 인물 비트, 비트 → 그 비트).
        master = step.get("master")
        master_in = body.get("master") if isinstance(body.get("master"), dict) else None
        if master and master_in:
            changed_master = False
            stale_background = False
            stale_char_ids: list[str] = []
            stale_beat_ids: list[str] = []
            bg_in = master_in.get("background") if isinstance(master_in.get("background"), dict) else None
            if bg_in:
                background = master.setdefault("background", {})
                for field in ("promptEn", "descKo"):
                    if field in bg_in and str(bg_in[field]) != str(background.get(field) or ""):
                        background[field] = str(bg_in[field])[:300 if field == "promptEn" else 120]
                        changed_master = True
                        stale_background = True
            char_edits = {
                str(item.get("id")): item
                for item in (master_in.get("characters") or [])
                if isinstance(item, dict) and item.get("id")
            }
            for mchar in master.get("characters") or []:
                incoming = char_edits.get(str(mchar.get("id")))
                if incoming and "promptEn" in incoming and str(incoming["promptEn"]) != str(mchar.get("promptEn") or ""):
                    mchar["promptEn"] = str(incoming["promptEn"])[:200]
                    changed_master = True
                    stale_char_ids.append(mchar.get("id"))
            beat_edits = {
                str(item.get("id")): item
                for item in (master_in.get("beats") or [])
                if isinstance(item, dict) and item.get("id")
            }
            for beat in master.get("beats") or []:
                incoming = beat_edits.get(str(beat.get("id")))
                if not incoming:
                    continue
                for field, limit in (("actionEn", 240), ("cameraEn", 80), ("descKo", 70)):
                    if field in incoming and str(incoming[field]) != str(beat.get(field) or ""):
                        beat[field] = str(incoming[field])[:limit]
                        changed_master = True
                        if beat.get("id") not in stale_beat_ids:
                            stale_beat_ids.append(beat.get("id"))
            if changed_master:
                master["version"] = int(master.get("version") or 1) + 1
                if stale_background:
                    master.setdefault("background", {})["status"] = "stale"
                beats_by_id = {b.get("id"): b for b in master.get("beats") or []}
                char_set = set(stale_char_ids)
                beat_set = set(stale_beat_ids)
                for kf in step.get("keyframes") or []:
                    beat_id = kf.get("beatId")
                    if beat_id is None:
                        continue
                    beat = beats_by_id.get(beat_id) or {}
                    if stale_background or beat_id in beat_set or (char_set & set(beat.get("characters") or [])):
                        kf["stale"] = True

        keep = {str(item) for item in (body.get("keepShotIds") or [])}
        shots = step.get("shots") or []
        if keep:
            shots = [shot for shot in shots if str(shot.get("id")) in keep]
        edits = {str(item.get("id")): item for item in body.get("shots") or []}
        characters = []  # 검사에 필요한 캐릭터는 아래에서 받는다
        for shot in shots:
            incoming = edits.get(str(shot.get("id")))
            if incoming and "promptEn" in incoming:
                # 고급 편집: 사용자가 직접 고친 영어 프롬프트는 그대로 쓴다(자르지 않는다).
                text = str(incoming["promptEn"] or "").strip()
                shot["promptEn"] = text
                shot["reviewKo"] = (
                    None
                    if text and len(text) <= compose.MAX_PROMPT_CHARS
                    else f"검토 필요: 프롬프트가 비었거나 {compose.MAX_PROMPT_CHARS}자를 넘습니다"
                )
            # ③ 영상 설정: 샷 길이(초). 모델 범위를 벗어나면 저장하지 않고 알린다.
            # 영상 설정은 dev 전용이다(모델 고르기가 꺼진 운영은 Luma 540p · 5초 그대로).
            if settings.MODEL_PICKER_ENABLED and incoming and "durationS" in incoming:
                # 073 fix1(R3.9): 범위는 ark.duration_range(ARK·Luma·비계열 fal 은 073 이전 값과 같다).
                # fal Seedance 계열은 범위 안의 caps durations(③ 선택지 durationChoices)에 있어야 한다.
                low, high = ark.duration_range(model)
                try:
                    seconds = int(incoming["durationS"])
                except (TypeError, ValueError):
                    seconds = -1
                allowed_seconds = low <= seconds <= high
                if allowed_seconds and settings.is_fal_seedance_family(model):
                    allowed_seconds = seconds in (ark.video_options(model).get("durationChoices") or [])
                if not allowed_seconds:
                    raise VpocError(
                        "routes.save_step",
                        "BadDuration",
                        f"샷 길이는 {low}~{high}초 사이로 정해 주세요 (받은 값 {incoming['durationS']}).",
                        400,
                    )
                shot["durationS"] = seconds
        if settings.MODEL_PICKER_ENABLED and "oneTake" in body:
            # A안 "한 번에 이어서 만들기" 켜기/끄기(기본 켜짐).
            step["oneTake"] = bool(body.get("oneTake"))
        if settings.MODEL_PICKER_ENABLED and "resolution" in body:
            wanted = str(body.get("resolution") or "")
            # 073 fix1(R4.7): fal Seedance 계열은 ③ 선택지와 같은 caps 화질(ark.video_options). 그 밖은 지금과 같다.
            if settings.is_fal_seedance_family(model):
                allowed = [str(item) for item in ark.video_options(model)["resolutions"]]
            else:
                allowed = settings.VIDEO_RESOLUTIONS.get(model) or [settings.RESOLUTION]
            if wanted not in allowed:
                raise VpocError(
                    "routes.save_step",
                    "BadResolution",
                    f"이 모델에서 고를 수 있는 화질이 아닙니다: {wanted} (가능: {', '.join(allowed)})",
                    400,
                )
            step["resolution"] = wanted
        step["shots"] = shots
        step["useKeyframes"] = bool(body.get("useKeyframes"))
        resolution = _plan_resolution(step, model)
        per_shot = [settings.shot_usd(model, resolution, _shot_seconds(model, shot)) for shot in shots]
        step["cost"] = {
            "shots": len(shots),
            "durationS": sum(_shot_seconds(model, shot) for shot in shots),
            "resolution": resolution,
            "perShotUsd": per_shot[0] if per_shot else 0,
            "totalUsd": round(sum(per_shot), 2),
        }

    @bp.post("/p/<pid>/plans/<plan_id>/back")
    @deps.require_auth
    @enveloped("routes.back")
    def back(pid: str, plan_id: str):
        project = project_for(pid, write=True)
        owner = owner_of(project)
        body = request.get_json(silent=True) or {}
        target = int(body.get("toStep") or 1)

        def mutator(state: dict):
            if not 1 <= target <= int(state.get("maxReachedStep") or 1):
                raise VpocError(
                    "routes.back", "PreconditionFailed", "아직 가 보지 않은 단계입니다.", 409
                )
            state["currentStep"] = target

        state, _ = store.mutate_state(owner, pid, plan_id, mutator)
        return jsonify(plan_view(state, project, can_edit=True))

    def _cancel_video(state: dict, owner: str, pid: str, plan_id: str) -> bool:
        """영상 생성(④) 취소(설계자 2026-10-20). 취소한 것이 있으면 True.

        steps["4"].status == "running" 이거나 샷/regen 중 queued·submitted 가 있으면 취소한다.
        - queued 대상 → status "cancelled", note "사용자가 취소했습니다".
        - submitted 대상 → 공급자 취소를 시도(실패해도 진행)하고 status "cancelled", note 동일,
          cancelledAt 기록, cancelRemote 에 결과("ok"|"rejected"|"unsupported")를 남긴다.
            · ark/tasks · fal/requests → ark.cancel(대기 중일 때만 실제 취소). 예외는 "rejected".
            · Luma(Bedrock async) → 취소 API 가 없다. 호출하지 않고 "unsupported".
        취소 뒤 나중에 도착한 결과는 무시된다(refresh_shots 가 submitted/queued 만 다루므로
        cancelled 는 다시 조회·제출되지 않는다). 원장은 되돌리지 않는다(이미 계상된 것은 그대로).
        """
        step4 = state["steps"]["4"]
        shots = step4.get("shots") or []
        # 샷 본체와 진행 중 regen 모두 본다(regen.poll_targets 와 같은 대상 집합).
        targets: list[dict] = []
        for shot in shots:
            for target in regen.poll_targets(shot):
                targets.append(target)
            # dual(실사+애니): 진행 중 anime(queued/preparing/submitted)도 취소 대상.
            # preparing(선점)을 cancelled 로 바꾸면 제출 스레드가 claim 확인에서 status 가 preparing 이
            # 아니므로 fal.submit_edit 를 하지 않는다(016 수정 2026-10-20).
            anime = shot.get("anime")
            if isinstance(anime, dict) and anime.get("status") in ("queued", "preparing", "submitted"):
                targets.append(anime)
        active = [t for t in targets if t.get("status") in ("queued", "preparing", "submitted")]
        if not active and step4.get("status") != "running":
            return False

        for target in active:
            if target.get("status") == "submitted" and target.get("invocationArn"):
                arn = str(target.get("invocationArn") or "")
                if ark.is_ark_job(arn):
                    try:
                        ark.cancel(arn)
                        target["cancelRemote"] = "ok"
                    except Exception as exc:  # noqa: BLE001 — 거절·오류여도 우리 쪽은 cancelled
                        log.warning("vpoc.cancel.remote_failed arn=%s err=%s", arn, exc)
                        target["cancelRemote"] = "rejected"
                else:
                    # Luma(Bedrock async)는 취소 API 가 없다 — 호출하지 않는다.
                    target["cancelRemote"] = "unsupported"
                target["cancelledAt"] = now_iso()
            target["status"] = "cancelled"
            target["note"] = "사용자가 취소했습니다"

        # ④ 전체 상태를 cancelled 로 둔다. final 은 그대로(이미 완료된 샷·합본은 유지).
        step4["status"] = "cancelled"
        return True

    @bp.post("/p/<pid>/plans/<plan_id>/cancel")
    @deps.require_auth
    @enveloped("routes.cancel")
    def cancel(pid: str, plan_id: str):
        """진행 중 작업 취소(설계자 2026-10-20).

        단계 작업(task.status == "running")과 영상 생성(steps["4"] 진행 중)이 함께 있으면 둘 다
        취소한다. 취소할 것이 없으면 409 NothingToCancel.
        """
        project = project_for(pid, write=True)
        owner = owner_of(project)

        def mutator(state: dict):
            cancelled_any = False

            # 1) 단계 작업 취소. 스레드 안 모델 호출은 멈출 수 없다(이미 보낸 요청은 끝까지 돈다) —
            #    task.id 를 바꿔(여기서 None 으로) 스레드가 끝날 때 _finish 가 결과를 버리게 한다.
            task = state.get("task") or {}
            if task.get("status") == "running":
                state["task"] = {
                    "name": task.get("name"),
                    "status": "cancelled",
                    "startedAt": task.get("startedAt"),
                    "finishedAt": now_iso(),
                    "error": None,
                    "shotId": task.get("shotId"),
                    "id": None,
                }
                cancelled_any = True

            # 2) 영상 생성 취소(큐/제출 대상 + 공급자 취소 시도).
            if _cancel_video(state, owner, pid, plan_id):
                cancelled_any = True

            if not cancelled_any:
                raise VpocError(
                    "routes.cancel", "NothingToCancel", "취소할 진행 중 작업이 없습니다.", 409
                )

        state, _ = store.mutate_state(owner, pid, plan_id, mutator)
        # update_graph: generate 가 "generating" 을 쓴다. 취소에 맞는 상태 값이 따로 없으므로
        # 건드리지 않는다(추측으로 새 값을 만들지 않는다 — 설계자 2026-10-20).
        return jsonify(plan_view(state, project, can_edit=True))

    @bp.post("/p/<pid>/plans/<plan_id>/run/<action>")
    @deps.require_auth
    @enveloped("routes.run")
    def run(pid: str, plan_id: str, action: str):
        project = project_for(pid, write=True)
        owner = owner_of(project)
        user = deps.current_username()
        body = request.get_json(silent=True) or {}
        state, etag = store.load_state(owner, pid, plan_id)
        require_idle(state)

        if action == "analyze":
            step1 = state["steps"]["1"]
            if step1.get("status") == "empty":
                raise VpocError("routes.run", "PreconditionFailed", "①을 먼저 저장해 주세요.", 409)
            has_text = bool((step1.get("text") or "").strip())
            has_files = bool(step1.get("sceneFiles")) or any(
                card.get("files") for card in step1.get("characters") or []
            )
            if not (has_text or has_files):
                raise VpocError(
                    "routes.run", "PreconditionFailed", "글 또는 이미지가 하나는 있어야 합니다.", 409
                )
            snapshot = dict(step1)
            run_task(
                "analyze",
                owner,
                pid,
                plan_id,
                lambda: do_analyze(owner, pid, plan_id, user, snapshot),
            )
            return jsonify({"ok": True, "task": "analyze"}), 202

        if action == "prepare":
            step2 = state["steps"]["2"]
            if step2.get("status") != "ready":
                raise VpocError(
                    "routes.run", "PreconditionFailed", "②가 준비되지 않았습니다.", 409
                )
            snapshot = dict(step2)
            step1_snapshot = dict(state["steps"]["1"])
            run_task(
                "prepare",
                owner,
                pid,
                plan_id,
                lambda: do_prepare(owner, pid, plan_id, user, snapshot, step1=step1_snapshot),
            )
            return jsonify({"ok": True, "task": "prepare"}), 202

        if action == "redraw" and str(body.get("target") or "") == "background":
            # 영상 1개(single, 설계자 2026-10-19): 배경 그림 다시 그리기. 현재 마스터 기준.
            master0 = state["steps"]["3"].get("master")
            if not master0:
                raise VpocError("routes.run", "PreconditionFailed", "마스터가 없습니다.", 409)

            def redraw_background() -> dict:
                st, _ = store.load_state(owner, pid, plan_id)
                master = st["steps"]["3"].get("master") or {}
                drawn = _draw_background(owner, pid, plan_id, user, master)
                bg = dict(master.get("background") or {})
                if drawn.get("status") == "ready":
                    bg.update({"imageKey": drawn.get("key") or "", "status": "ready", "error": None})
                else:
                    bg["status"] = "failed"
                    bg["error"] = drawn.get("error")
                return {"background": bg}

            run_task("redraw", owner, pid, plan_id, redraw_background)
            return jsonify({"ok": True, "task": "redraw"}), 202

        if action == "redraw" and body.get("keyframeId"):
            kf_id = str(body.get("keyframeId") or "")
            scene_edit = body.get("sceneEn")
            target = next(
                (item for item in state["steps"]["3"].get("keyframes") or [] if item.get("id") == kf_id), None
            )
            if not target:
                raise VpocError("routes.run", "NotFound", "키 장면을 찾을 수 없습니다.", 404)
            item = dict(target)
            # 영상 1개(single): 그 비트 미리보기를 현재 마스터 기준으로 다시 그린다(설계자 2026-10-19).
            if state["steps"]["3"].get("master"):
                def redraw_preview() -> dict:
                    st, _ = store.load_state(owner, pid, plan_id)
                    master = st["steps"]["3"].get("master") or {}
                    fresh = next(
                        (k for k in st["steps"]["3"].get("keyframes") or [] if k.get("id") == kf_id),
                        item,
                    )
                    result = _draw_beat_preview(owner, pid, plan_id, user, master, fresh)
                    out = dict(fresh)
                    out["rough"] = result
                    out["final"] = {"status": "none"}
                    out["stale"] = False
                    return {"characters": [], "keyframes": [out]}

                run_task("redraw", owner, pid, plan_id, redraw_preview)
                return jsonify({"ok": True, "task": "redraw"}), 202
            if scene_edit is not None and str(scene_edit).strip():
                item["sceneEn"] = str(scene_edit).strip()[:600]
            handles = {
                c.get("id"): c.get("handleEn") or c.get("id") for c in state["steps"]["2"].get("characters") or []
            }

            def redraw_keyframe() -> dict:
                item["rough"] = _draw_rough(owner, pid, plan_id, user, item, handles)
                item["final"] = {"status": "none"}
                return {"characters": [], "keyframes": [item]}

            run_task("redraw", owner, pid, plan_id, redraw_keyframe)
            return jsonify({"ok": True, "task": "redraw"}), 202

        if action == "redraw":
            char_id = str(body.get("charId") or "")
            if not char_id:
                raise VpocError("routes.run", "BadRequest", "charId 가 필요합니다.", 400)
            snapshot = state
            run_task(
                "redraw",
                owner,
                pid,
                plan_id,
                lambda: do_redraw(owner, pid, plan_id, user, snapshot, char_id),
            )
            return jsonify({"ok": True, "task": "redraw"}), 202

        if action == "generate":
            step3 = state["steps"]["3"]
            if step3.get("status") not in ("ready", "saved"):
                raise VpocError(
                    "routes.run", "PreconditionFailed", "③이 준비되지 않았습니다.", 409
                )
            shots = step3.get("shots") or []
            if not shots:
                raise VpocError("routes.run", "PreconditionFailed", "샷이 없습니다.", 409)
            if any(shot.get("reviewKo") for shot in shots):
                raise VpocError(
                    "routes.run",
                    "PreconditionFailed",
                    "'검토 필요' 샷이 있습니다. 고치거나 지운 뒤 승인해 주세요.",
                    409,
                )
            video_model = ark.selected(state)
            # ③ 영상 설정(설계자 2026-10-07): 화질 하나, 샷마다 길이.
            video_resolution = _plan_resolution(step3, video_model)
            # 영상 1개(single, 설계자 2026-10-19)면 마스터 프롬프트 1개로 만든다. 길이는 master.totalS,
            # 금액도 그 1개 기준이다. single 판정은 makeMode + 마스터 유무로 한다.
            single_master = (
                step3.get("master")
                if _make_mode(state["steps"]["2"], video_model, step3) == "single"
                else None
            )
            seconds_by_shot = {shot["id"]: _shot_seconds(video_model, shot) for shot in shots}
            # dual(실사+애니, 설계자 2026-10-20): 변환 비용 = 만들 영상 초 합 × 단가. single·oneTake 는
            # 영상 1개라 그 길이 1건, 샷별은 샷마다 1건. 화면 금액과 같은 함수로 계산해 CostMismatch 방지.
            dual = _dual_enabled()
            one_take_for_cost = None if single_master else _one_take(state["steps"], video_model)
            if single_master:
                single_total = int(single_master.get("totalS") or _single_total(state["steps"]["2"], video_model))
                # 073 R3.5·X6(E2): 영상 1개 길이가 이 모델이 한 번에 만들 수 있는 범위(_single_total 과 같은 출처
                # ark.video_options) 밖이면 금액 검사보다 먼저 막는다. 계열이 아닌 fal 은 caps 최소~최대(지금과 같다).
                single_options = ark.video_options(video_model)
                single_low = int(single_options.get("minDurationS") or settings.DURATION_S)
                single_high = int(single_options.get("maxDurationS") or settings.DURATION_S)
                if not single_low <= single_total <= single_high:
                    raise VpocError(
                        "routes.run",
                        "BadDuration",
                        f"{ark.label(video_model)}는 한 번에 {single_low}~{single_high}초만 만들 수 있습니다"
                        f"(이 영상 {single_total}초). ③ 계획을 다시 만들어 주세요.",
                        400,
                    )
                expected = round(settings.shot_usd(video_model, video_resolution, single_total), 2)
                anime_seconds = [single_total] if dual else []
            elif one_take_for_cost:
                take_total = int(one_take_for_cost.get("durationS") or 0)
                expected = round(settings.shot_usd(video_model, video_resolution, take_total), 2)
                anime_seconds = [take_total] if dual else []
            else:
                expected = round(
                    sum(
                        settings.shot_usd(video_model, video_resolution, seconds_by_shot[shot["id"]])
                        for shot in shots
                    ),
                    2,
                )
                anime_seconds = [seconds_by_shot[shot["id"]] for shot in shots] if dual else []
            anime_expected = round(sum(settings.anime_usd(sec) for sec in anime_seconds), 2)
            expected = round(expected + anime_expected, 2)
            confirm = body.get("confirmUsd")
            if confirm is None or abs(float(confirm) - expected) > 0.005:
                raise VpocError(
                    "routes.run",
                    "CostMismatch",
                    f"화면 금액({confirm})과 서버 계산({expected:.2f} USD)이 다릅니다. 새로고침해 주세요.",
                    409,
                )
            # single 이면 take 하나(마스터), 아니면 A안(옛) _one_take(조건 맞으면). 둘 다 아니면 샷별.
            if single_master:
                take = {"id": ark.ONE_TAKE_ID, "durationS": single_total, "mode": "reference"}
            else:
                take = _one_take(state["steps"], video_model)
            one_take = take

            def mutator(inner: dict):
                # 생성 회차를 새로 만든다. 토큰·jobs 키·출력 접두사가 이 값으로 갈라지므로
                # ③을 고쳐 다시 승인하면 Bedrock 이 이전 작업을 돌려주지 않는다.
                inner["steps"]["4"] = {
                    "status": "running",
                    "genId": store.new_gen_id(),
                    # 이 회차가 쓰는 모델. 생성 중에 ② 를 바꿔도 진행 중인 샷은 이 값을 따른다.
                    "videoModel": video_model,
                    "resolution": video_resolution,
                    "oneTake": bool(one_take) and not single_master,
                    # 영상 1개(single) 회차 표시. submit_shot 이 이 값으로 마스터 프롬프트 · 참고 이미지를 고른다.
                    "single": bool(single_master),
                    # dual(실사+애니, 설계자 2026-10-20) 회차 표시. refresh_shots 가 실사 완료 샷마다
                    # anime 변환을 준비·제출한다. 옛 회차에는 이 키가 없다(= 변환 없음).
                    "dual": dual,
                    "shots": [
                        {
                            "id": shot["id"],
                            "status": "queued",
                            "attempt": 1,
                            "durationS": shot["durationS"],
                            "mode": shot["mode"],
                            "submittedAt": None,
                            "completedAt": None,
                            "videoUrl": None,
                            "note": None,
                            "error": None,
                        }
                        for shot in (
                            [{"id": one_take["id"], "durationS": one_take["durationS"], "mode": "reference"}]
                            if one_take
                            else [
                                {
                                    "id": item["id"],
                                    "durationS": seconds_by_shot[item["id"]],
                                    "mode": "keyframe" if item.get("useKeyframe") else "text",
                                }
                                for item in shots
                            ]
                        )
                    ],
                    "final": {"status": "none", "url": None, "error": None},
                }
                if ark.is_ark(video_model):
                    inner["steps"]["4"]["briefing"] = now_iso()
                inner.pop("finalizedAt", None)
                inner["currentStep"] = 4
                inner["maxReachedStep"] = 4

            state, _ = store.mutate_state(owner, pid, plan_id, mutator)
            update_graph(pid, "generating", plan_id, "", "")
            # 샷 제출(모델 호출)은 응답 뒤에 한다(설계자 2026-10-13). 요청 안에서 하면 모델 쪽이 느릴 때
            # 화면이 HTTP 504 를 받았다 — 영상은 만들어지는데 ③ 에 오류가 남았다. ④ 조회도 대기 샷을 제출한다.
            threading.Thread(
                target=_prepare_briefs if ark.is_ark(video_model) else _submit_queued,
                args=(owner, pid, plan_id, user),
                daemon=True,
            ).start()
            _ensure_driver(owner, pid, plan_id, user)
            shots4 = state["steps"]["4"]["shots"]
            return jsonify(
                {
                    "ok": True,
                    "submitted": sum(1 for s in shots4 if s.get("status") == "submitted"),
                    "queued": sum(1 for s in shots4 if s.get("status") == "queued"),
                }
            )

        if action == "retry":
            shot_id = str(body.get("shotId") or "")

            def mutator(inner: dict):
                shot = next(
                    (item for item in inner["steps"]["4"].get("shots", []) if item["id"] == shot_id),
                    None,
                )
                if not shot:
                    raise VpocError("routes.run", "NotFound", "샷을 찾을 수 없습니다.", 404)
                if shot.get("status") != "failed":
                    raise VpocError(
                        "routes.run", "PreconditionFailed", "실패한 샷만 다시 만들 수 있습니다.", 409
                    )
                shot.update(
                    {
                        "status": "queued",
                        "attempt": int(shot.get("attempt") or 1) + 1,
                        "mode": "text",
                        "invocationArn": "",
                        "submittedAt": None,
                        "completedAt": None,
                        "outputKey": "",
                        "note": None,
                    }
                )
                inner.pop("finalizedAt", None)
                refresh_shots(inner, owner, pid, plan_id, user)

            state, _ = store.mutate_state(owner, pid, plan_id, mutator)
            _start_anime_jobs(_claims_take())
            return jsonify(plan_view(state, project, can_edit=True))

        if action == "stitch":
            variant = "anime" if str(body.get("variant") or "") == "anime" else "final"
            return jsonify(stitch(owner, pid, plan_id, variant=variant))

        # --- vpoc-regen: "이 샷만 의견 반영해 다시 만들기" 액션 3개. 위 액션은 건드리지 않았다(G1). ---

        if action == "revise":
            regen.require_enabled("routes.run")
            shot_id = str(body.get("shotId") or "")
            feedback = str(body.get("feedbackKo") or "")
            use_keyframe = bool(body.get("useKeyframe"))
            regen.check_revise_preconditions(state, shot_id, feedback)
            snapshot = state
            run_task(
                "revise",
                owner,
                pid,
                plan_id,
                lambda: regen.do_revise(
                    state=snapshot,
                    shot_id=shot_id,
                    feedback=feedback,
                    use_keyframe=use_keyframe,
                    user=user,
                    pid=pid,
                    plan_id=plan_id,
                ),
                shot_id=shot_id,
            )
            return jsonify({"ok": True, "task": "revise"}), 202

        if action == "regenerate":
            regen.require_enabled("routes.run")
            shot_id = str(body.get("shotId") or "")
            revision_id = str(body.get("revisionId") or "")
            prompt_en = body.get("promptEn")
            confirm_usd = body.get("confirmUsd")
            box: dict = {}

            def mutator(inner: dict):
                box.update(
                    regen.start_regenerate(
                        inner,
                        shot_id=shot_id,
                        revision_id=revision_id,
                        prompt_en=prompt_en,
                        confirm_usd=confirm_usd,
                        owner=owner,
                        pid=pid,
                        plan_id=plan_id,
                        user=user,
                        refresh_shots=refresh_shots,
                    )
                )

            store.mutate_state(owner, pid, plan_id, mutator)
            _start_anime_jobs(_claims_take())
            return jsonify({"ok": True, **box})

        if action == "select":
            regen.require_enabled("routes.run")
            shot_id = str(body.get("shotId") or "")
            try:
                attempt = int(body.get("attempt"))
            except (TypeError, ValueError) as exc:
                raise VpocError(
                    "routes.run", "BadRequest", "attempt 가 필요합니다.", 400
                ) from exc
            box = {}

            def mutator(inner: dict):
                box.update(regen.apply_select(inner, shot_id=shot_id, attempt=attempt))

            state, _ = store.mutate_state(owner, pid, plan_id, mutator)
            # 계획이 완료된 뒤의 버전 전환만 기록에 남긴다(R12).
            if state.get("finalizedAt"):
                base, _ = store.STORE.read_json(store.final_key(owner, pid, plan_id))
                if base:
                    key = regen.finals_record(
                        state, owner=owner, pid=pid, plan_id=plan_id, base=base
                    )
                    if key:
                        # thumbKey 는 finish_plan 과 같은 규칙으로 다시 구해 덮어쓰지 않는다.
                        thumb_key = next(
                            (
                                (item.get("image") or {}).get("key")
                                for item in state["steps"]["3"].get("characters", [])
                                if (item.get("image") or {}).get("key")
                            ),
                            "",
                        )
                        update_graph(pid, "done", plan_id, key, thumb_key)
            return jsonify(plan_view(state, project, can_edit=True))

        raise VpocError("routes.run", "BadAction", f"알 수 없는 동작입니다: {action}", 400)

    def stitch(owner: str, pid: str, plan_id: str, *, variant: str = "final") -> dict:
        """샷을 하나로 이어 붙인다(P1). 실패하면 합본 없이 순차 재생으로 간다(E15).

        variant="final"(실사, 기본): 샷 본체 outputKey 를 이어 `final.mp4`·steps[4].final 에.
        variant="anime"(실사+애니, 설계자 2026-10-20): 완료된 anime outputKey 를 이어
        `final-anime.mp4`·steps[4].finalAnime 에. single(샷 1개)은 클립 1개라 그대로 복사된다.
        """
        import os
        import tempfile

        from .. import stitcher

        is_anime = variant == "anime"
        state, etag = store.load_state(owner, pid, plan_id)
        if is_anime:
            done = [
                shot
                for shot in state["steps"]["4"].get("shots", [])
                if (shot.get("anime") or {}).get("status") == "completed"
                and (shot.get("anime") or {}).get("outputKey")
            ]
        else:
            done = [
                shot
                for shot in state["steps"]["4"].get("shots", [])
                if shot.get("status") == "completed" and shot.get("outputKey")
            ]
        if not done:
            raise VpocError(
                "routes.stitch",
                "PreconditionFailed",
                "이어 붙일 애니 샷이 없습니다." if is_anime else "이어 붙일 샷이 없습니다.",
                409,
            )

        with tempfile.TemporaryDirectory(prefix="vpoc-stitch-") as workdir:
            paths = []
            for index, shot in enumerate(done, 1):
                source_key = (shot.get("anime") or {}).get("outputKey") if is_anime else shot["outputKey"]
                raw = store.STORE.get_bytes(source_key, bucket=settings.VIDEO_BUCKET)
                path = os.path.join(workdir, f"{index:02d}.mp4")
                with open(path, "wb") as handle:
                    handle.write(raw)
                paths.append(path)
            output = os.path.join(workdir, "final-anime.mp4" if is_anime else "final.mp4")
            stitcher.concat(paths, output)
            with open(output, "rb") as handle:
                body = handle.read()

        if is_anime:
            # final.mp4 옆 final-anime.mp4 (store 에 전용 키 함수를 더하지 않고 접두사 재사용).
            key = store.plan_prefix(owner, pid, plan_id) + "/final-anime.mp4"
        else:
            key = store.final_mp4_key(owner, pid, plan_id)
        store.STORE.put_bytes(key, body, "video/mp4")

        def mutator(inner: dict):
            if is_anime:
                inner["steps"]["4"]["finalAnime"] = {"status": "ready", "key": key, "error": None}
            else:
                inner["steps"]["4"]["final"] = {"status": "ready", "key": key, "error": None}

        store.mutate_state(owner, pid, plan_id, mutator)
        return {"ok": True, "url": store.STORE.presign(key, content_type="video/mp4")}

    # ---------------------------------------------------------------- smoke

    @bp.post("/smoke")
    @deps.require_auth
    @enveloped("routes.smoke_start")
    def smoke_start():
        if not settings.SMOKE_ENABLED:
            raise VpocError("routes.smoke_start", "NotFound", "테스트 경로가 꺼져 있습니다.", 404)
        user = deps.current_username()
        body = request.get_json(silent=True) or {}

        if body.get("errorTest"):
            # 과금 없이 AWS 원문 오류를 한 번 받아 본다. 일부러 잘못된 멱등 키를 보내고
            # API 검증 계층에서 거절당한다 — 작업이 만들어지지 않는다(video.error_probe 주석).
            try:
                video.error_probe()
            except Exception as exc:  # noqa: BLE001
                return jsonify(
                    {
                        "ok": True,
                        "id": "error-probe",
                        "status": "failed",
                        "elapsedS": 0,
                        "videoUrl": None,
                        "error": envelope("luma.start_async_invoke", exc),
                        "promptChars": 0,
                    }
                )
            raise VpocError(
                "routes.smoke_start",
                "ErrorProbeAccepted",
                "오류 시험이 거절되지 않고 받아들여졌습니다. 과금 위험이 있으니 확인이 필요합니다.",
                500,
            )

        prompt = body.get("prompt")
        if prompt is None:
            prompt = compose.STYLE["live_action"] + (
                ". A floodlit racing pit lane at night right after a race, wet asphalt reflecting "
                "lights. A tall man with short white hair and Western features, wearing a "
                "black-and-yellow racing suit. The white-haired racer pulls off his helmet, shakes "
                "out his hair and fixes a cold stare on someone off-screen. Slow push-in, "
                "eye-level medium shot."
            )
        prompt = str(prompt)
        smoke_id = store.short_id("sm")
        key = f"{store.SMOKE_PREFIX.format(user=user)}/{smoke_id}.json"

        record = {
            "id": smoke_id,
            "user": user,
            "prompt": prompt,
            "promptChars": len(prompt),
            "status": "submitted",
            "startedAt": now_iso(),
            "error": None,
        }
        # 빈 프롬프트도 과금 대상이다(AWS 가 거절하지 않고 작업으로 받는다) — video.submit 이 막는다.
        try:
            job = video.submit(
                owner=user,
                project_id="_smoke",
                plan_id=smoke_id,
                shot_id="s1",
                attempt=1,
                prompt=prompt,
            )
        except Exception as exc:  # noqa: BLE001
            # 제출 자체가 거절되면 작업이 만들어지지 않으므로 과금이 없다.
            record["status"] = "failed"
            record["error"] = envelope("luma.start_async_invoke", exc)
            store.STORE.write_json(key, record)
            return jsonify(
                {
                    "ok": True,
                    "id": smoke_id,
                    "status": "failed",
                    "elapsedS": 0,
                    "videoUrl": None,
                    "error": record["error"],
                }
            )

        ledger.charge_video(
            job["clientRequestToken"], user=user, project_id="_smoke", plan_id=smoke_id
        )
        record.update(
            {
                "invocationArn": job["invocationArn"],
                "outputPrefix": job["outputPrefix"],
                "submittedAt": job["submittedAt"],
            }
        )
        store.STORE.write_json(key, record)
        return jsonify(
            {
                "ok": True,
                "id": smoke_id,
                "status": "submitted",
                "elapsedS": 0,
                "videoUrl": None,
                "error": None,
            }
        )

    @bp.get("/smoke/<smoke_id>")
    @deps.require_auth
    @enveloped("routes.smoke_status")
    def smoke_status(smoke_id: str):
        if not settings.SMOKE_ENABLED:
            raise VpocError("routes.smoke_status", "NotFound", "테스트 경로가 꺼져 있습니다.", 404)
        user = deps.current_username()
        key = f"{store.SMOKE_PREFIX.format(user=user)}/{smoke_id}.json"
        record, _ = store.STORE.read_json(key)
        if not record:
            raise VpocError("routes.smoke_status", "NotFound", "테스트 기록이 없습니다.", 404)

        if record.get("status") == "submitted" and record.get("invocationArn"):
            result = video.poll(record["invocationArn"])
            if result["status"] == "Completed":
                found = video.find_output_mp4(record.get("outputPrefix") or "")
                if found:
                    record["status"] = "completed"
                    record["outputKey"] = found["key"]
                    record["bytes"] = found["size"]
                else:
                    record["status"] = "failed"
                    record["error"] = {
                        "where": "luma.output",
                        "type": "OutputMissing",
                        "message": f"출력 접두사에 .mp4 가 없습니다: {record.get('outputPrefix')}",
                        "requestId": "",
                        "httpStatus": 200,
                        "at": now_iso(),
                    }
                record["finishedAt"] = now_iso()
                store.STORE.write_json(key, record)
            elif result["status"] == "Failed":
                record["status"] = "failed"
                record["finishedAt"] = now_iso()
                record["error"] = {
                    "where": "luma.get_async_invoke",
                    "type": "AsyncInvokeFailed",
                    "message": result.get("failureMessage") or "",
                    "requestId": "",
                    "httpStatus": 200,
                    "at": now_iso(),
                }
                store.STORE.write_json(key, record)

        return jsonify(
            {
                "ok": True,
                "id": smoke_id,
                "status": record.get("status"),
                "elapsedS": _elapsed_s(
                    record.get("startedAt") or "", record.get("finishedAt") or ""
                ),
                "videoUrl": (
                    video.video_url(record["outputKey"]) if record.get("outputKey") else None
                ),
                "error": record.get("error"),
                "promptChars": record.get("promptChars"),
            }
        )

    # ---------------------------------------------------------------- fal.ai 모델 (설계자 2026-10-18)
    #
    # ② 영상 모델에 fal 모델을 추가한다. MODEL_PICKER_ENABLED 가 꺼져 있으면 이 API 들도 404 다
    # (고르기 자체가 없는 배포에서 fal 등록 경로만 열려 있을 이유가 없다).

    def _require_picker(where: str) -> None:
        if not settings.MODEL_PICKER_ENABLED:
            raise VpocError(where, "NotFound", "영상 모델 고르기가 꺼져 있습니다.", 404)

    def _require_fal_key(where: str) -> None:
        if not settings.FAL_KEY:
            raise VpocError(
                where,
                "FalKeyMissing",
                "fal API 키가 설정되지 않았습니다. 관리자에게 FAL_KEY 설정을 요청해 주세요.",
                409,
            )

    @bp.get("/fal/catalog")
    @deps.require_auth
    @enveloped("routes.fal_catalog")
    def fal_catalog():
        """fal 카탈로그 한 페이지. category 기본값은 image-to-video."""
        _require_picker("routes.fal_catalog")
        _require_fal_key("routes.fal_catalog")
        category = str(request.args.get("category") or "image-to-video")
        if category not in settings.FAL_BROWSE_CATEGORIES:
            raise VpocError(
                "routes.fal_catalog",
                "BadCategory",
                f"알 수 없는 카테고리입니다: {category}",
                400,
            )
        page = fal.catalog(
            category,
            q=str(request.args.get("q") or ""),
            cursor=str(request.args.get("cursor") or ""),
        )
        return jsonify(
            {
                "ok": True,
                "category": category,
                # 영상 단계에서 실제로 쓸 수 있는 카테고리인지(그 밖은 목록만 보이고 등록 불가).
                "usable": category in settings.FAL_VIDEO_CATEGORIES,
                "models": page["models"],
                "nextCursor": page["nextCursor"],
                "hasMore": page["hasMore"],
            }
        )

    @bp.get("/fal/models")
    @deps.require_auth
    @enveloped("routes.fal_models")
    def fal_models():
        """등록된 fal 모델 목록 + available/reason."""
        _require_picker("routes.fal_models")
        models = []
        for item in fal.registered():
            model_id = str(item.get("id") or "")
            reason = fal.unavailable_reason(model_id) if settings.FAL_KEY else "fal API 키 미설정"
            models.append(
                {
                    "id": model_id,
                    "endpointId": item.get("endpointId"),
                    "label": item.get("label"),
                    "category": item.get("category"),
                    "description": item.get("description"),
                    "thumbnailUrl": item.get("thumbnailUrl"),
                    "addedBy": item.get("addedBy"),
                    "addedAt": item.get("addedAt"),
                    "available": not reason,
                    "reason": reason,
                }
            )
        return jsonify({"ok": True, "configured": bool(settings.FAL_KEY), "models": models})

    @bp.post("/fal/models")
    @deps.require_auth
    @enveloped("routes.fal_register")
    def fal_register():
        """fal 모델 등록. body {endpointId}. 스키마를 받아 caps 를 만들고 목록에 더한다."""
        _require_picker("routes.fal_register")
        _require_fal_key("routes.fal_register")
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get("endpointId") or "").strip()
        if not endpoint:
            raise VpocError("routes.fal_register", "BadEndpoint", "endpointId 가 필요합니다.", 400)
        item = fal.register(endpoint, added_by=deps.current_username())
        return jsonify({"ok": True, "model": item}), 201

    @bp.delete("/fal/models/<path:endpoint_id>")
    @deps.require_auth
    @enveloped("routes.fal_unregister")
    def fal_unregister(endpoint_id: str):
        """등록 해제(관리자만, 073 R10.1-10.2). 이미 그 모델을 고른 계획은 selected() 가 Luma 로 되돌리고,
        진행 중 회차의 제출은 submit_shot 이 E5 로 실패시킨다(R11.1)."""
        _require_picker("routes.fal_unregister")
        user = deps.current_username()
        # 로그용 id: URL 경로에서 온 값이라 줄바꿈 등 제어 문자를 지우고 길이를 묶는다(로그 위조 방지).
        endpoint = "".join(ch for ch in str(endpoint_id or "").strip()[:200] if ch.isprintable())
        if not deps.is_admin():
            # 레지스트리는 전 사용자 공용이다. 해제 한 번에 운영 Seedance 전체가 바뀌므로 관리자만(D10).
            log.warning("vpoc.fal.unregister.denied user=%s endpoint=%s", user, endpoint)
            raise VpocError(
                "routes.fal_unregister",
                "AdminOnly",
                "fal 모델 해제는 관리자만 할 수 있습니다.",
                403,
            )
        removed = fal.unregister(endpoint_id)
        log.info("vpoc.fal.unregistered endpoint=%s by=%s removed=%s", endpoint, user, removed)
        return jsonify({"ok": True, "removed": removed})

    return bp
