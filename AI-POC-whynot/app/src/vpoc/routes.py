"""vpoc1 라우트 — PRD 7장. 응답 키는 7.3 · 7.4 예시와 글자까지 같게 한다.

권한 순서: 인증 → 프로젝트 가시성(보기) → 소유자(쓰기) → 본 처리.
모델 호출은 전부 202 + 조회 방식이다(ALB 유휴 60초 · gunicorn timeout 60초).
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

from flask import Blueprint, jsonify, request

from . import ark, compose, images, ledger, llm, regen, settings, store, video
from .errors import VpocError, envelope, now_iso, status_for

log = logging.getLogger("vpoc")

STEP_NAMES = {1: "입력", 2: "확인", 3: "계획 승인", 4: "영상"}


@dataclass
class VpocDependencies:
    """server.py 가 주입한다. `g` 에 의존하는 것은 전부 호출자에서 받는다."""

    require_auth: Callable
    current_username: Callable[[], str]
    get_project: Callable[[str], dict | None]
    can_read_project: Callable[[dict], bool]
    can_write_project: Callable[[dict], bool]
    graph_run: Callable[[str, dict], list]


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
        """③에서 고른 화질. 고르지 않았거나 그 모델에 없는 값이면 모델 기본값."""
        allowed = settings.VIDEO_RESOLUTIONS.get(model) or [settings.RESOLUTION]
        chosen = str(step3.get("resolution") or "")
        if chosen in allowed:
            return chosen
        default = settings.default_resolution(model)
        return default if default in allowed else allowed[0]

    def _shot_seconds(model: str, shot: dict) -> int:
        """샷 길이(초). 고르지 않았으면 5초, 모델 범위를 벗어나면 범위 안으로 맞춘다."""
        low, high = settings.VIDEO_DURATION_RANGE.get(model, (settings.DURATION_S, settings.DURATION_S))
        try:
            seconds = int(shot.get("durationS") or settings.DURATION_S)
        except (TypeError, ValueError):
            seconds = settings.DURATION_S
        return max(low, min(high, seconds))

    def _reference_characters(steps: dict, plan_shot: dict) -> list[tuple[str, str]]:
        """샷에 나오는 캐릭터 중 이미지가 준비된 것 → [(handleEn, 이미지 키)] (샷의 캐릭터 순서).

        이미지는 사용자가 올린 것(source=user)이 있으면 그것, 없으면 ③에서 만든 AI 이미지다
        (do_prepare 가 이미 그렇게 고른다). 둘 다 "이 캐릭터 그대로"의 기준이다.
        """
        sheets = {item.get("id"): item for item in steps["2"].get("characters") or []}
        images_by_id = {
            item.get("id"): item.get("image") or {} for item in steps["3"].get("characters") or []
        }
        refs: list[tuple[str, str]] = []
        for char_id in plan_shot.get("characters") or []:
            image = images_by_id.get(char_id) or {}
            if image.get("status") != "ready" or not image.get("key"):
                continue
            sheet = sheets.get(char_id) or {}
            handle = str(sheet.get("handleEn") or sheet.get("nameEn") or "the character").strip()
            refs.append((handle, image["key"]))
        return refs

    def _shot4_view(
        shot: dict, plan_shot: dict | None = None, gen_id: str = ""
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
            "regen": regen.regen_view(shot),
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
            if ark.is_ark(model) and shot.get("promptEn"):
                # API 를 부르기 전에 보낼 문장을 그대로 보여 준다(설계자 2026-10-07 1-3).
                refs = _reference_characters(steps, shot)
                row["sendPromptEn"] = ark.reference_prompt(
                    shot.get("promptEn") or "", [name for name, _ in refs]
                )
                row["referenceCount"] = len(refs)
            step3["shots"].append(row)
        if step3.get("cost"):
            total = sum(row["usd"] for row in step3["shots"])
            step3["cost"] = {
                **step3["cost"],
                "model": model,
                "resolution": resolution,
                "durationS": sum(row["durationS"] for row in step3["shots"]),
                "priceKnown": not ark.is_ark(model)
                or settings.ark_usd_per_sec(model, resolution) is not None,
                "perShotUsd": step3["shots"][0]["usd"] if step3["shots"] else 0,
                "totalUsd": round(total, 2),
            }

        plan_shots = {item.get("id"): item for item in steps["3"].get("shots", [])}
        step4 = dict(steps["4"])
        gen_id = str(steps["4"].get("genId") or "")
        step4["shots"] = [
            _shot4_view(shot, plan_shots.get(shot.get("id")), gen_id)
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
            },
            "regenEnabled": regen.enabled(),
            # ② 영상 모델 고르기(dev 만). 꺼져 있으면 화면에 목록이 없다.
            "modelPicker": settings.MODEL_PICKER_ENABLED,
            "videoModels": ark.choices() if settings.MODEL_PICKER_ENABLED else [],
            "videoModel": model,
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

        def _mark_start(state: dict):
            state["task"] = {
                "name": name,
                "status": "running",
                "startedAt": now_iso(),
                "finishedAt": None,
                "error": None,
                "shotId": shot_id or None,
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
                if store.revision_of(state, watch_step) != revision:
                    log.info("vpoc.task.discarded name=%s plan=%s", name, plan_id)
                    state["task"] = {
                        "name": None,
                        "status": "idle",
                        "startedAt": None,
                        "finishedAt": now_iso(),
                        "error": None,
                        "shotId": None,
                    }
                    return
                state["task"] = {
                    "name": name,
                    "status": "error" if error else "done",
                    "startedAt": (state.get("task") or {}).get("startedAt"),
                    "finishedAt": now_iso(),
                    "error": error,
                    "shotId": shot_id or None,
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
                }
            )
            state["currentStep"] = 3
            state["maxReachedStep"] = max(int(state.get("maxReachedStep") or 1), 3)
        elif name == "redraw":
            by_id = {item["id"]: item for item in state["steps"]["3"].get("characters", [])}
            for item in result.get("characters", []):
                by_id[item["id"]] = item
            state["steps"]["3"]["characters"] = list(by_id.values())
        elif name == "revise":
            regen.apply_revision(state, result)

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
        )
        if total > len(jpegs):
            result["story"]["notesKo"] = (
                f"이미지 {total}장 중 {len(jpegs)}장만 분석했습니다. "
                + (result["story"].get("notesKo") or "")
            ).strip()
        return result

    def do_prepare(owner: str, pid: str, plan_id: str, user: str, step2: dict) -> dict:
        style = compose.style_of((step2.get("story") or {}).get("style") or "live_action")
        characters = step2.get("characters") or []
        story = step2.get("story") or {}
        user_images = step2.get("userImages") or {}

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
            prompt, negative = compose.image_prompt(character, story, style)
            seed = secrets.randbelow(4294967295)
            for attempt in (1, 2):
                try:
                    made = images.generate_character_image(
                        owner=owner,
                        pid=pid,
                        char_id=char_id,
                        prompt=prompt,
                        negative_prompt=negative,
                        seed=seed,
                    )
                except Exception as exc:  # noqa: BLE001
                    body = envelope("stability.invoke_model", exc)
                    if attempt == 1 and body.get("type") == "ImageFiltered":
                        # E8: 시드만 바꿔 1회 재시도.
                        seed = secrets.randbelow(4294967295)
                        continue
                    with lock:
                        image_results[char_id] = {
                            "status": "error",
                            "source": "ai",
                            "imageId": None,
                            "seed": seed,
                            "error": body,
                        }
                    return
                ledger.charge_image(
                    f"img-{plan_id}-{char_id}-{made['imageId']}",
                    user=user,
                    project_id=pid,
                    plan_id=plan_id,
                )
                with lock:
                    image_results[char_id] = {
                        "status": "ready",
                        "source": "ai",
                        "imageId": made["imageId"],
                        "key": made["key"],
                        "seed": made["seed"],
                        "error": None,
                    }
                return

        plan_box: dict = {}

        def make_plan() -> None:
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

        plan_thread.join()
        payload = plan_box.get("payload")
        if payload is None:
            raise VpocError("claude.plan", "PlanMissing", "샷 계획을 받지 못했습니다.", 502)

        shots = []
        for shot in payload.get("shots") or []:
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

    def do_redraw(owner: str, pid: str, plan_id: str, user: str, state: dict, char_id: str) -> dict:
        step2 = state["steps"]["2"]
        character = next(
            (item for item in step2.get("characters") or [] if item.get("id") == char_id), None
        )
        if not character:
            raise VpocError("routes.redraw", "NotFound", "캐릭터를 찾을 수 없습니다.", 404)
        style = compose.style_of((step2.get("story") or {}).get("style") or "live_action")
        prompt, negative = compose.image_prompt(character, step2.get("story") or {}, style)
        made = images.generate_character_image(
            owner=owner,
            pid=pid,
            char_id=char_id,
            prompt=prompt,
            negative_prompt=negative,
            seed=secrets.randbelow(4294967295),
        )
        ledger.charge_image(
            f"img-{plan_id}-{char_id}-{made['imageId']}",
            user=user,
            project_id=pid,
            plan_id=plan_id,
        )
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
        if not plan_shot:
            target["status"] = "failed"
            target["error"] = {
                "where": "routes.submit_shot",
                "type": "ShotMissing",
                "message": "계획에서 이 샷을 찾을 수 없습니다.",
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
        use_ark = ark.is_ark(video_model)
        video_resolution = str(step4.get("resolution") or settings.default_resolution(video_model))
        seconds = int(shot.get("durationS") or _shot_seconds(video_model, plan_shot))
        try:
            if use_ark:
                # 설계자 2026-10-07: 샷의 캐릭터 이미지(사용자 업로드 또는 ③에서 승인한 AI 이미지)를
                # 참고 이미지로 함께 보내고, 그 캐릭터 그대로 만들라는 지시를 프롬프트 앞에 붙인다.
                references: list[str] = []
                names: list[str] = []
                for handle, key in _reference_characters(state["steps"], plan_shot):
                    try:
                        references.append(images.keyframe_base64(key))
                        names.append(handle)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("vpoc.reference.failed shot=%s err=%s", shot["id"], exc)
                if references:
                    mode = "reference"
                job = ark.submit(
                    model=video_model,
                    owner=owner,
                    project_id=pid,
                    plan_id=plan_id,
                    shot_id=shot["id"],
                    attempt=attempt,
                    prompt=ark.reference_prompt(prompt, names),
                    keyframe_jpeg_b64=keyframe,
                    gen_id=gen_id,
                    reference_jpegs_b64=references,
                    resolution=video_resolution,
                    duration_s=seconds,
                )
            else:
                job = video.submit(
                    owner=owner,
                    project_id=pid,
                    plan_id=plan_id,
                    shot_id=shot["id"],
                    attempt=attempt,
                    prompt=prompt,
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
        )
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
            }
        )
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
                "resolution": video_resolution,
                "durationS": seconds,
                # Seedance 에 실제로 보낸 문장(참고 이미지 지시 포함). Luma 는 prompt 와 같다.
                "sentPrompt": ark.reference_prompt(prompt, names) if use_ark else prompt,
            },
        )

    def refresh_shots(state: dict, owner: str, pid: str, plan_id: str, user: str) -> bool:
        """진행 중 샷 상태를 갱신하고, 대기 샷을 제출한다. 바뀐 것이 있으면 True."""
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

        # 동시 한도는 샷 본체와 재생성이 함께 쓴다.
        def all_targets() -> list[tuple[dict, dict]]:
            pairs: list[tuple[dict, dict]] = []
            for item in shots:
                for target in regen.poll_targets(item):
                    pairs.append((item, target))
            return pairs

        inflight = sum(1 for _, target in all_targets() if target.get("status") == "submitted")
        for shot, target in all_targets():
            if target.get("status") != "queued":
                continue
            if inflight >= settings.MAX_INFLIGHT:
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

        gen_id_now = str(step4.get("genId") or "")
        for shot in shots:
            changed = regen.sync_versions(shot, gen_id_now) or changed

        # ④ 전체 상태는 **샷 본체만** 본다. 재생성이 진행 중이어도 ④ 상태가 바뀌지 않는다(G2).
        statuses = {shot.get("status") for shot in shots}
        if statuses & {"queued", "submitted"}:
            step4["status"] = "running"
        elif "failed" in statuses:
            step4["status"] = "partial"
        else:
            step4["status"] = "done"

        if not (statuses & {"queued", "submitted"}):
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
            }
        )

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
        if can_edit and state["steps"]["4"].get("shots"):
            try:
                changed = refresh_shots(state, owner, pid, plan_id, deps.current_username()) or changed
            except Exception as exc:  # noqa: BLE001
                log.error("vpoc.refresh.failed plan=%s err=%s", plan_id, exc)
        if changed:
            try:
                store.save_state(owner, pid, plan_id, state, etag)
            except VpocError as exc:
                if exc.kind != "PreconditionFailed":
                    raise
                state, _ = store.load_state(owner, pid, plan_id)
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
            if before != after:
                store.bump_revision(state, number)
                store.mark_stale(state, number)
            state["currentStep"] = max(number, 1)
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
                [step.get("story"), step.get("characters"), step.get("answers"), step.get("extraKo")],
                ensure_ascii=False,
                sort_keys=True,
            )
        return _json.dumps(
            [step.get("shots"), step.get("useKeyframes"), step.get("resolution")],
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
            user_images = dict(step.get("userImages") or {})
            for key, value in (body.get("userImages") or {}).items():
                if value and value.get("fileId"):
                    user_images[key] = {"fileId": value["fileId"]}
            step["userImages"] = user_images
            # 영상 모델은 ③ 계획(샷·이미지)을 바꾸지 않으므로 _fingerprint 에 넣지 않는다 —
            # 모델만 바꿨는데 ③ 을 다시 만들게(이미지 비용) 하지 않으려는 것이다.
            if settings.MODEL_PICKER_ENABLED and "videoModel" in body:
                wanted = str(body.get("videoModel") or "")
                allowed = {item["id"] for item in ark.choices() if item["available"]}
                if wanted not in allowed:
                    raise VpocError(
                        "routes.save_step", "ModelUnavailable", f"고를 수 없는 영상 모델입니다: {wanted}", 400
                    )
                step["videoModel"] = wanted
            return

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
            if incoming and "durationS" in incoming:
                low, high = settings.VIDEO_DURATION_RANGE.get(model, (5, 5))
                try:
                    seconds = int(incoming["durationS"])
                except (TypeError, ValueError):
                    seconds = -1
                if not low <= seconds <= high:
                    raise VpocError(
                        "routes.save_step",
                        "BadDuration",
                        f"샷 길이는 {low}~{high}초 사이로 정해 주세요 (받은 값 {incoming['durationS']}).",
                        400,
                    )
                shot["durationS"] = seconds
        if "resolution" in body:
            wanted = str(body.get("resolution") or "")
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
            run_task(
                "prepare",
                owner,
                pid,
                plan_id,
                lambda: do_prepare(owner, pid, plan_id, user, snapshot),
            )
            return jsonify({"ok": True, "task": "prepare"}), 202

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
            seconds_by_shot = {shot["id"]: _shot_seconds(video_model, shot) for shot in shots}
            expected = round(
                sum(
                    settings.shot_usd(video_model, video_resolution, seconds_by_shot[shot["id"]])
                    for shot in shots
                ),
                2,
            )
            confirm = body.get("confirmUsd")
            if confirm is None or abs(float(confirm) - expected) > 0.005:
                raise VpocError(
                    "routes.run",
                    "CostMismatch",
                    f"화면 금액({confirm})과 서버 계산({expected:.2f} USD)이 다릅니다. 새로고침해 주세요.",
                    409,
                )

            def mutator(inner: dict):
                # 생성 회차를 새로 만든다. 토큰·jobs 키·출력 접두사가 이 값으로 갈라지므로
                # ③을 고쳐 다시 승인하면 Bedrock 이 이전 작업을 돌려주지 않는다.
                inner["steps"]["4"] = {
                    "status": "running",
                    "genId": store.new_gen_id(),
                    # 이 회차가 쓰는 모델. 생성 중에 ② 를 바꿔도 진행 중인 샷은 이 값을 따른다.
                    "videoModel": video_model,
                    "resolution": video_resolution,
                    "shots": [
                        {
                            "id": shot["id"],
                            "status": "queued",
                            "attempt": 1,
                            "durationS": seconds_by_shot[shot["id"]],
                            "mode": "keyframe" if shot.get("useKeyframe") else "text",
                            "submittedAt": None,
                            "completedAt": None,
                            "videoUrl": None,
                            "note": None,
                            "error": None,
                        }
                        for shot in shots
                    ],
                    "final": {"status": "none", "url": None, "error": None},
                }
                inner.pop("finalizedAt", None)
                inner["currentStep"] = 4
                inner["maxReachedStep"] = 4
                refresh_shots(inner, owner, pid, plan_id, user)

            state, _ = store.mutate_state(owner, pid, plan_id, mutator)
            update_graph(pid, "generating", plan_id, "", "")
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
            return jsonify(plan_view(state, project, can_edit=True))

        if action == "stitch":
            return jsonify(stitch(owner, pid, plan_id))

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

    def stitch(owner: str, pid: str, plan_id: str) -> dict:
        """샷을 하나로 이어 붙인다(P1). 실패하면 합본 없이 순차 재생으로 간다(E15)."""
        import os
        import tempfile

        from .. import stitcher

        state, etag = store.load_state(owner, pid, plan_id)
        done = [
            shot
            for shot in state["steps"]["4"].get("shots", [])
            if shot.get("status") == "completed" and shot.get("outputKey")
        ]
        if not done:
            raise VpocError("routes.stitch", "PreconditionFailed", "이어 붙일 샷이 없습니다.", 409)

        with tempfile.TemporaryDirectory(prefix="vpoc-stitch-") as workdir:
            paths = []
            for index, shot in enumerate(done, 1):
                raw = store.STORE.get_bytes(shot["outputKey"], bucket=settings.VIDEO_BUCKET)
                path = os.path.join(workdir, f"{index:02d}.mp4")
                with open(path, "wb") as handle:
                    handle.write(raw)
                paths.append(path)
            output = os.path.join(workdir, "final.mp4")
            stitcher.concat(paths, output)
            with open(output, "rb") as handle:
                body = handle.read()

        key = store.final_mp4_key(owner, pid, plan_id)
        store.STORE.put_bytes(key, body, "video/mp4")

        def mutator(inner: dict):
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

    return bp
