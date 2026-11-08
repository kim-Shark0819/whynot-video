"""vnew 라우트 (/api/vnew). 인증·소유자는 앱에서 주입받는다 — g 를 직접 읽지 않는다.

캐릭터 만들기(설계자 2026-10-20): 웹툰 이미지 → 분석 → 실사·웹툰 시트 → 3D 모델.
저장·파이프라인은 characters.py 에 있다. 여기서는 HTTP 입출력만 다룬다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, Response, jsonify, request

from ..vpoc.errors import VpocError, envelope, status_for

from .. import project_tracks
from . import casting
from . import characters
from . import plan as plan_mod


def _no_admin() -> bool:
    """is_admin 이 주입되지 않으면 관리자가 아니다(fail-closed)."""
    return False


@dataclass
class VnewDependencies:
    """server.py 가 주입한다. `g` 에 의존하는 것은 전부 호출자에서 받는다.

    current_owner: S3 키의 소유자 접두사(safe_user_prefix(username)). 쓰기·조회가 이 값으로
    users/{owner}/... 아래에 갇힌다 — 다른 사용자 데이터에 닿지 않는다.
    """

    require_auth: Callable[[Callable], Callable]
    current_owner: Callable[[], str] | None = None
    # 아래 셋은 계획 API(/api/vnew/p/<pid>/...) 가 쓴다. 틀만 쓰던 옛 등록(health · 캐릭터)과
    # 호환되게 선택값으로 둔다 — 주입되지 않으면 계획 API 가 500(권한 확인 불가)을 낸다.
    get_project: Callable[[str], dict | None] | None = None
    can_read_project: Callable[[dict], bool] | None = None
    can_write_project: Callable[[dict], bool] | None = None
    # 프로젝트 소유자 username → S3 키 소유자 접두사(safe_user_prefix). 주입되지 않으면 그대로 쓴다
    # (검사에서는 이미 안전한 값을 넣으므로 항등이면 된다).
    project_owner_prefix: Callable[[str], str] | None = None
    # 작업 134: 관리자 판별(다시 이어 붙이기 경로). 주입하지 않으면 거부(_no_admin) — vpoc 와 같다.
    is_admin: Callable[[], bool] = _no_admin


def create_blueprint(deps: VnewDependencies) -> Blueprint:
    bp = Blueprint("vnew", __name__, url_prefix="/api/vnew")

    def _owner() -> str:
        # current_owner 가 주입되지 않은 옛 등록(health 만 쓰던 때)과도 호환되게 둔다.
        if deps.current_owner is None:
            raise VpocError("vnew", "NoOwner", "소유자 정보를 확인할 수 없습니다.", 500)
        owner = (deps.current_owner() or "").strip()
        if not owner:
            raise VpocError("vnew", "NoOwner", "소유자 정보를 확인할 수 없습니다.", 500)
        return owner

    def _fail(exc: BaseException):
        body = envelope("vnew", exc)
        return jsonify({"error": body}), status_for(body)

    def _safe_prefix(owner: str) -> str:
        """프로젝트 소유자 username 을 S3 키 소유자 접두사로. 주입이 없으면 그대로 쓴다."""
        if deps.project_owner_prefix is None:
            return owner
        return deps.project_owner_prefix(owner)

    @bp.get("/health")
    @deps.require_auth
    def health():
        # 틀 확인용. 인증을 통과하면 서버에 vnew 블루프린트가 등록돼 있다는 뜻이다.
        return jsonify({"ok": True})

    # ------------------------------------------------------------ 목록 · 생성

    @bp.get("/characters")
    @deps.require_auth
    def list_characters():
        try:
            return jsonify({"characters": characters.list_characters(_owner())})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/characters")
    @deps.require_auth
    def create_character():
        try:
            owner = _owner()
            payload = request.get_json(silent=True) or {}
            char = characters.new_char(str(payload.get("name") or ""))
            characters.save_char(owner, char["id"], char)
            characters.upsert_index(owner, char)
            return jsonify({"id": char["id"], "name": char["name"], "status": char["status"]}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 상세 · 삭제

    @bp.get("/characters/<char_id>")
    @deps.require_auth
    def get_character(char_id: str):
        try:
            return jsonify(characters.detail_view(_owner(), char_id))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.delete("/characters/<char_id>")
    @deps.require_auth
    def delete_character(char_id: str):
        try:
            owner = _owner()
            # 존재 확인(없으면 404). S3 파일은 지우지 않고 index 에서만 숨긴다.
            characters.load_char(owner, char_id)
            characters.hide_in_index(owner, char_id)
            return jsonify({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 업로드

    @bp.post("/characters/<char_id>/uploads")
    @deps.require_auth
    def add_uploads(char_id: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            files = request.files.getlist("files") or request.files.getlist("file")
            if not files:
                raise VpocError("vnew.uploads", "NoFiles", "업로드할 이미지가 없습니다.", 400)
            added = []
            for storage in files:
                raw = storage.read()
                if not raw:
                    continue
                added.append(characters.add_source(owner, char_id, raw, storage.filename or ""))
            return jsonify({"sources": added}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.delete("/characters/<char_id>/uploads/<file_id>")
    @deps.require_auth
    def delete_upload(char_id: str, file_id: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)
            characters.remove_source(owner, char_id, file_id)
            return jsonify({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 빌드

    @bp.post("/characters/<char_id>/build")
    @deps.require_auth
    def build_character(char_id: str):
        try:
            owner = _owner()
            payload = request.get_json(silent=True) or {}
            # extraPromptKo 가 body 에 있으면(없으면 None=이전 값 유지) ≤500 로 자른다.
            extra = None
            if "extraPromptKo" in payload:
                raw = payload.get("extraPromptKo")
                extra = str(raw or "")[: characters.settings.MAX_EXTRA_PROMPT_LEN]
            version = characters.start_build(owner, char_id, extra)
            return jsonify({"ok": True, "version": version, "status": "building"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 3D만 다시(retry3d)

    @bp.post("/characters/<char_id>/variants/<variant>/retry3d")
    @deps.require_auth
    def retry3d_character(char_id: str, variant: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            version = characters.start_retry3d(owner, char_id, variant)
            return jsonify({"ok": True, "version": version, "status": "building"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 그림체만 다시(variant rebuild)

    @bp.post("/characters/<char_id>/variants/<variant>/rebuild")
    @deps.require_auth
    def rebuild_variant(char_id: str, variant: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            payload = request.get_json(silent=True) or {}
            # extraPromptKo 가 body 에 있으면(없으면 None=이전 값 유지) ≤최대 길이로 자른다.
            extra = None
            if "extraPromptKo" in payload:
                raw = payload.get("extraPromptKo")
                extra = str(raw or "")[: characters.settings.MAX_EXTRA_PROMPT_LEN]
            version = characters.start_rebuild_variant(owner, char_id, variant, extra)
            return jsonify({"ok": True, "version": version, "status": "building"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 추가 복장(outfits)

    @bp.post("/characters/<char_id>/outfits")
    @deps.require_auth
    def add_outfit(char_id: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            payload = request.get_json(silent=True) or {}
            outfit = characters.add_outfit(
                owner,
                char_id,
                str(payload.get("labelKo") or ""),
                str(payload.get("promptKo") or ""),
            )
            return jsonify({"ok": True, "outfit": outfit}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/characters/<char_id>/outfits/<outfit_id>/build")
    @deps.require_auth
    def build_outfit(char_id: str, outfit_id: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            characters.start_build_outfit(owner, char_id, outfit_id)
            return jsonify({"ok": True, "status": "building"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.delete("/characters/<char_id>/outfits/<outfit_id>")
    @deps.require_auth
    def delete_outfit(char_id: str, outfit_id: str):
        try:
            owner = _owner()
            characters.load_char(owner, char_id)  # 존재·소유 확인
            characters.delete_outfit(owner, char_id, outfit_id)
            return jsonify({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ 실사 캐스팅(vnew-live-fx 묶음 B)
    #
    # 설정(VNEW_CASTING)이 꺼져 있으면 캐릭터를 보기 전에 404 CastingDisabled(새 라우트가 없는 것과 같다, R0.3).
    # 저장 경로의 주인은 지금 사용자(_owner)다 — 다른 사용자 캐릭터는 404.

    @bp.put("/characters/<char_id>/casting")
    @deps.require_auth
    def casting_settings(char_id: str):
        try:
            casting.require_enabled("vnew.casting")
            owner = _owner()
            payload = request.get_json(silent=True)
            payload = payload if isinstance(payload, dict) else {}
            # blRole·blType(지시서 103 화면 선택값)은 넘기지 않는다(지시서 104 — 옛 화면이 보내도 무시, 400 없음).
            view = casting.update_settings(
                owner, char_id, preset=payload.get("preset"), gender=payload.get("gender"), look_en=payload.get("lookEn"),
            )
            return jsonify({"casting": view})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/characters/<char_id>/casting/draw")
    @deps.require_auth
    def casting_draw(char_id: str):
        try:
            casting.require_enabled("vnew.casting.draw")
            owner = _owner()
            payload = request.get_json(silent=True)
            payload = payload if isinstance(payload, dict) else {}
            confirm = casting.parse_confirm_usd(payload.get("confirmUsd"))
            # blRole·blType 은 넘기지 않는다(지시서 104). BL 유형은 캐릭터 설명(extraPromptKo)에서 서버가 찾는다.
            view = casting.start_draw(
                owner, char_id, confirm,
                preset=payload.get("preset"), gender=payload.get("gender"), look_en=payload.get("lookEn"),
            )
            return jsonify({"casting": view}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/characters/<char_id>/casting/choose")
    @deps.require_auth
    def casting_choose(char_id: str):
        try:
            casting.require_enabled("vnew.casting.choose")
            owner = _owner()
            payload = request.get_json(silent=True)
            payload = payload if isinstance(payload, dict) else {}
            candidate = payload.get("candidateId")
            if not isinstance(candidate, str):
                # 고르기 풀기는 빈 문자열을 명시해야 한다(키가 빠진 요청이 고른 사진을 지우지 않게).
                raise VpocError("vnew.casting.choose", "NoCandidateId", "고를 사진(candidateId)이 필요합니다.", 400)
            return jsonify({"casting": casting.choose(owner, char_id, candidate)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ GLB 스트리밍

    @bp.get("/characters/<char_id>/model/<variant>.glb")
    @deps.require_auth
    def stream_glb(char_id: str, variant: str):
        try:
            if variant not in characters.settings.VARIANTS:
                raise VpocError("vnew.glb", "BadVariant", "알 수 없는 그림체입니다.", 404)
            data = characters.glb_bytes(_owner(), char_id, variant)
            return Response(
                data,
                mimetype="model/gltf-binary",
                headers={
                    "Content-Type": "model/gltf-binary",
                    "Content-Length": str(len(data)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ============================================================ 계획 API (①입력 ②대본 ③콘티)
    #
    # /api/vnew/p/<pid>/... 프로젝트 권한은 vpoc 와 같은 방식(get_project → read/write 확인).
    # 트랙이 video_new 인 프로젝트만 받는다. 저장 경로의 주인은 프로젝트 소유자다.

    def _project_for(pid: str, *, write: bool) -> str:
        """프로젝트를 확인하고 저장 경로 소유자(owner 접두사)를 돌려준다.

        - 주입 deps 가 없으면 500(권한 확인 불가).
        - 없으면 404, 읽기 불가 403, 쓰기 요청인데 쓰기 불가 403.
        - 트랙이 video_new 가 아니면 403(영상만들기_신규 프로젝트만).
        """
        if deps.get_project is None or deps.can_read_project is None or deps.can_write_project is None:
            raise VpocError("vnew.plan", "NoDeps", "프로젝트 권한을 확인할 수 없습니다.", 500)
        project = deps.get_project(pid)
        if not project:
            raise VpocError("vnew.plan", "NotFound", "프로젝트를 찾을 수 없습니다.", 404)
        if not deps.can_read_project(project):
            raise VpocError("vnew.plan", "Forbidden", "이 프로젝트를 볼 수 없습니다.", 403)
        if write and not deps.can_write_project(project):
            raise VpocError("vnew.plan", "Forbidden", "이 프로젝트를 바꿀 수 있는 사람은 소유자뿐입니다.", 403)
        if str(project.get("track") or "") != project_tracks.TRACK_VIDEO_NEW:
            raise VpocError("vnew.plan", "TrackMismatch", "영상만들기_신규 프로젝트가 아닙니다.", 403)
        owner = str(project.get("owner") or "")
        if not owner:
            raise VpocError("vnew.plan", "NoOwner", "프로젝트 소유자를 알 수 없습니다.", 500)
        return _safe_prefix(owner)

    @bp.get("/p/<pid>/plan")
    @deps.require_auth
    def get_plan(pid: str):
        try:
            owner = _project_for(pid, write=False)
            data, _ = plan_mod.load_or_create(owner, pid)
            # gen 이 진행 중이면 드라이버가 살아 있게 한다(파드 재시작 대비).
            try:
                from . import generate  # noqa: PLC0415

                generate.ensure_driver(owner, pid)
            except Exception:  # noqa: BLE001
                pass
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/uploads")
    @deps.require_auth
    def plan_uploads(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            storages = request.files.getlist("files") or request.files.getlist("file")
            if not storages:
                raise VpocError("vnew.plan.uploads", "NoFiles", "업로드할 이미지가 없습니다.", 400)
            files = [(storage.read(), storage.filename or "") for storage in storages]
            added = plan_mod.add_uploads(owner, pid, files)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"added": added, "plan": plan_mod.plan_view(owner, pid, data)}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.delete("/p/<pid>/uploads/<file_id>")
    @deps.require_auth
    def plan_delete_upload(pid: str, file_id: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.delete_upload(owner, pid, file_id)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/input")
    @deps.require_auth
    def plan_input(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            payload = request.get_json(silent=True) or {}
            plan_mod.update_input(owner, pid, payload)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/input/sort")
    @deps.require_auth
    def plan_input_sort(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            order = plan_mod.sort_natural(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"order": order, "plan": plan_mod.plan_view(owner, pid, data)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/script")
    @deps.require_auth
    def plan_script_run(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            task_id = plan_mod.start_script(owner, pid)
            return jsonify({"ok": True, "taskId": task_id}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/script")
    @deps.require_auth
    def plan_script_edit(pid: str):
        try:
            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            plan_mod.update_script(owner, pid, payload)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/script/voices")
    @deps.require_auth
    def plan_script_voices(pid: str):
        """② [속마음 찾기](지시서 118b). 202 {ok, taskId}. 진행 중 작업·대사 없음이면 409."""
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            task_id = plan_mod.start_voice_scan(owner, pid)
            return jsonify({"ok": True, "taskId": task_id}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/conti")
    @deps.require_auth
    def plan_conti_run(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            task_id = plan_mod.start_conti(owner, pid)
            return jsonify({"ok": True, "taskId": task_id}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/conti")
    @deps.require_auth
    def plan_conti_edit(pid: str):
        try:
            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            updated = plan_mod.update_conti(owner, pid, payload)
            view = plan_mod.plan_view(owner, pid, updated)
            view["fit"] = updated.get("_fit") or {}
            return jsonify(view)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/conti/fit")
    @deps.require_auth
    def plan_conti_fit(pid: str):
        try:
            owner = _project_for(pid, write=True)
            plan_mod.fit_conti(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/conti/fx")
    @deps.require_auth
    def plan_conti_fx(pid: str):
        """③ [기호·분위기 채우기](vnew-live-fx A.3). 빈 컷만 채우고 번역 대상 컷은 moodEn 만. 202 {ok, taskId, targets, translate}.

        설정이 꺼져 있으면 프로젝트를 보기 전에 404 FxDisabled(새 라우트가 없는 것과 같다).
        """
        try:
            if not plan_mod.vnew_settings_module().VNEW_FX:
                raise VpocError("vnew.conti.fx", "FxDisabled", "기호·분위기 기능이 꺼져 있습니다.", 404)
            owner = _project_for(pid, write=True)
            info = plan_mod.start_fx_fill(owner, pid)
            return jsonify({"ok": True, **info}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # 작업 162: 콘티 연기 다시 읽기(손 주인). 컷마다 패널을 다시 보고 characters[]·패널 연출 문법 actions 만 바꾼다(컷 id·초·대사·
    # 기호·카메라·프리비즈 승인·녹화 그대로, previz.posesStale). 소유자, 그리고 관리자(읽기 권한으로 소유자 저장소에). 202 {ok, taskId, cuts}.
    @bp.post("/p/<pid>/conti/reread-actions")
    @deps.require_auth
    def plan_conti_reread_actions(pid: str):
        try:
            owner = _project_for(pid, write=not deps.is_admin())
            info = plan_mod.start_reread_actions(owner, pid)
            return jsonify({"ok": True, **info}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/cancel")
    @deps.require_auth
    def plan_cancel(pid: str):
        try:
            owner = _project_for(pid, write=True)
            # gen 이 진행 중이면 gen 을 취소하고, 아니면 (대본·콘티) task 를 취소한다.
            data, _ = plan_mod.load_plan(owner, pid)
            gen = data.get("gen") or {}
            if gen.get("status") in ("running", "partial"):
                from . import generate  # noqa: PLC0415

                gen_out = generate.cancel_gen(owner, pid)
                return jsonify({"ok": True, "gen": gen_out})
            task = plan_mod.cancel_task(owner, pid)
            return jsonify({"ok": True, "task": task})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ③ 공간 디테일 시트(locations)
    #
    # 콘티의 장소마다 4면 입면도 시트(실사·웹툰)를 만든다. 콘티를 만들면 목록만 갱신하고 자동으로
    # 그리지는 않는다(비용) — 화면에서 [공간 시트 만들기]. locations.py 가 파이프라인을 맡는다.

    @bp.post("/p/<pid>/locations/<lid>/build")
    @deps.require_auth
    def location_build(pid: str, lid: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            payload = request.get_json(silent=True) or {}
            extra = None
            if "extraPromptKo" in payload:
                extra = str(payload.get("extraPromptKo") or "")[: locations.MAX_EXTRA_PROMPT_LEN]
            task_id = locations.start_build(owner, pid, lid, extra)
            return jsonify({"ok": True, "taskId": task_id, "status": "building"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/locations/build-all")
    @deps.require_auth
    def location_build_all(pid: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            started = locations.start_build_all(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "started": started, "plan": plan_mod.plan_view(owner, pid, data)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/locations/<lid>")
    @deps.require_auth
    def location_update(pid: str, lid: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            locations.update_location(owner, pid, lid, payload)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ③ 공간 참고 사진(photos)
    #
    # 장소마다 사용자가 실제 참고 사진을 붙인다(설계자 2026-10-22). 설계·사실·그림·확인에서 웹툰
    # 패널보다 먼저 쓴다. 이미지는 vpoc.images.normalize_upload 로 JPEG 로만 저장(외부 금지). 미리보기는
    # 같은 출처 API(아래 GET)로 받는다(버킷 CORS 없음). 빌드 중에는 변경이 409 다.

    @bp.post("/p/<pid>/locations/<lid>/photos")
    @deps.require_auth
    def location_add_photos(pid: str, lid: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            storages = request.files.getlist("files") or request.files.getlist("file")
            if not storages:
                raise VpocError("vnew.location.photos", "NoFiles", "올릴 사진이 없습니다.", 400)
            files = [(storage.read(), storage.filename or "") for storage in storages]
            side = request.form.get("side")
            view = locations.add_photos(owner, pid, lid, files, side)
            return jsonify({"ok": True, "location": view}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/locations/<lid>/photos/<photo_id>")
    @deps.require_auth
    def location_update_photo(pid: str, lid: str, photo_id: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            view = locations.update_photo(owner, pid, lid, photo_id, payload)
            return jsonify({"ok": True, "location": view})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.delete("/p/<pid>/locations/<lid>/photos/<photo_id>")
    @deps.require_auth
    def location_delete_photo(pid: str, lid: str, photo_id: str):
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            view = locations.delete_photo(owner, pid, lid, photo_id)
            return jsonify({"ok": True, "location": view})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.get("/p/<pid>/locations/<lid>/photos/<photo_id>")
    @deps.require_auth
    def location_photo_bytes(pid: str, lid: str, photo_id: str):
        # 참고 사진 미리보기(같은 출처). 읽기 권한이면 된다. 다른 사용자·다른 프로젝트 사진은 404.
        try:
            from . import locations  # noqa: PLC0415

            owner = _project_for(pid, write=False)
            body, content_type = locations.photo_bytes(owner, pid, lid, photo_id)
            return Response(
                body,
                mimetype=content_type,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ③→④ 프리비즈(previz)
    #
    # 3D 장면 설계 JSON(Astra). 브라우저 three.js 가 그려 재생·녹화한다(설계자 2026-10-21).
    # design(202 백그라운드) · PUT scene(사용자 수정) · approve · 녹화 업로드(변환·VIDEO_BUCKET).

    @bp.post("/p/<pid>/previz/design")
    @deps.require_auth
    def previz_design(pid: str):
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            # 본문(선택): 다시 만들기 지시 {extraPromptKo, cutId}. 첫 설계는 본문 없이.
            payload = request.get_json(silent=True) or {}
            extra_prompt_ko = str(payload.get("extraPromptKo") or "")
            cut_id = str(payload.get("cutId") or "")
            task_id = previz.start_design(owner, pid, extra_prompt_ko=extra_prompt_ko, cut_id=cut_id)
            return jsonify({"ok": True, "taskId": task_id, "status": "designing"}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/previz/scene")
    @deps.require_auth
    def previz_scene_edit(pid: str):
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            previz.update_scenes(owner, pid, payload)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # 작업 162: [포즈 다시 맞추기] — 저장된 패널 연출 문법 actions 로 장면 인물 포즈만 다시(카메라·배치 그대로, 승인 해제 → 다시 녹화).
    @bp.post("/p/<pid>/previz/refresh-poses")
    @deps.require_auth
    def previz_refresh_poses(pid: str):
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=not deps.is_admin())
            previz.refresh_poses(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/previz/approve")
    @deps.require_auth
    def previz_approve(pid: str):
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            force = bool(payload.get("force"))
            previz.approve(owner, pid, force=force)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify(plan_mod.plan_view(owner, pid, data))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/previz/brief")
    @deps.require_auth
    def previz_brief(pid: str):
        """촬영 지시서 (다시) 만들기(⑤, 설계자 2026-10-22). 진행 중이면 409 Briefing."""
        try:
            from . import shot_brief  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            task_id = shot_brief.start_brief(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "taskId": task_id, "plan": plan_mod.plan_view(owner, pid, data)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/previz/recordings/<seg_id>")
    @deps.require_auth
    def previz_recording(pid: str, seg_id: str):
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            body = request.get_data(cache=False) or b""
            result = previz.save_recording(owner, pid, seg_id, body, request.content_type or "")
            return jsonify({"ok": True, "recording": result}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/previz/recordings/<seg_id>/frames")
    @deps.require_auth
    def previz_frames(pid: str, seg_id: str):
        # 오프라인 렌더 프레임(jpeg) 한 묶음 업로드(multipart, 필드 f 여러 개, 순서대로). ?start=<index>.
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            try:
                start = int(request.args.get("start") or 0)
            except (TypeError, ValueError):
                raise VpocError("vnew.previz.frames", "BadStart", "start 값이 올바르지 않습니다.", 400)
            files = request.files.getlist("f")
            frames = [f.read() for f in files]
            result = previz.save_frames(owner, pid, seg_id, start, frames)
            return jsonify({"ok": True, "frames": result}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/previz/recordings/<seg_id>/finish")
    @deps.require_auth
    def previz_finish(pid: str, seg_id: str):
        # 올라온 프레임을 ffmpeg -framerate 로 묶어 mp4 를 만든다. body {frames, fps}.
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            payload = request.get_json(silent=True) or {}
            frames = payload.get("frames")
            fps = payload.get("fps", previz.REC_FPS)
            result = previz.finish_recording(owner, pid, seg_id, frames, fps)
            return jsonify({"ok": True, "recording": result}), 201
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.get("/p/<pid>/previz/texture")
    @deps.require_auth
    def previz_texture(pid: str):
        # 프리비즈 벽·바닥 텍스처를 같은 출처로 돌려준다(S3 직통·CORS 회피). 읽기 권한이면 된다.
        # key 가 이 프로젝트의 locations 접두사 밖이면 texture_bytes 가 404 를 낸다.
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=False)
            key = str(request.args.get("key") or "")
            body, content_type = previz.texture_bytes(owner, pid, key)
            return Response(
                body,
                mimetype=content_type,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.get("/p/<pid>/previz/panel")
    @deps.require_auth
    def previz_panel(pid: str):
        # 한 컷의 웹툰 패널 조각(분석에 쓴 그림 큰 조각)을 같은 출처 썸네일로 돌려준다. 읽기 권한이면 된다.
        try:
            from . import previz  # noqa: PLC0415

            owner = _project_for(pid, write=False)
            cut_id = str(request.args.get("cutId") or "")
            body, content_type = previz.panel_thumb_bytes(owner, pid, cut_id)
            return Response(
                body,
                mimetype=content_type,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ④ 영상 생성

    @bp.post("/p/<pid>/cast-notes")
    @deps.require_auth
    def plan_cast_notes(pid: str):
        """장면 의상·목소리 미리 읽기(설계자 2026-10-26, 064). 원작 패널에서 Claude 1회·캐시.

        [의상 읽기] 버튼. 통 테이크 모드일 때만 동작하고, 결과(인물별 자동 읽기 + override 합친 표)를
        돌려준다. 유료에 가까운 텍스트 비용(캐시되면 0).
        """
        try:
            from . import generate  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            cast = generate.read_cast_notes_all(owner, pid)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "sceneCast": cast, "plan": plan_mod.plan_view(owner, pid, data)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/generate")
    @deps.require_auth
    def plan_generate(pid: str):
        try:
            from . import generate  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            plan_mod.load_or_create(owner, pid)
            payload = request.get_json(silent=True) or {}
            confirm = payload.get("confirmUsd")
            if confirm is None:
                raise VpocError("vnew.generate", "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400)
            # 작업 123: styles(선택) ["webtoon"] | ["webtoon","real"] — "real" 은 통 테이크 + VNEW_TAKE_REAL 만(그 밖 400).
            gen_id = generate.start_generate(owner, pid, float(confirm), styles=payload.get("styles"))
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "genId": gen_id, "plan": plan_mod.plan_view(owner, pid, data)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # vnew-live-fx C.2: 웹툰 통 테이크가 끝난 gen 에서 같은 프리비즈 녹화로 실사 버전(회차)을 시작한다.
    # 설정(VNEW_TAKE_REAL)이 꺼져 있으면 프로젝트를 보기 전에 404 TakeRealDisabled(새 라우트가 없는 것과 같다, R0.3).
    @bp.post("/p/<pid>/generate/real")
    @deps.require_auth
    def plan_generate_real(pid: str):
        try:
            from . import generate  # noqa: PLC0415

            generate._require_take_real()  # noqa: SLF001
            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True)
            payload = payload if isinstance(payload, dict) else {}
            confirm = payload.get("confirmUsd")
            # 숫자(문자열 숫자 포함)만 받는다. 없거나 bool·NaN·무한이면 400(고정 문구, USER_FACING_KINDS).
            try:
                confirm_usd = float("nan") if isinstance(confirm, bool) else float(confirm)
            except (TypeError, ValueError):
                confirm_usd = float("nan")
            if not math.isfinite(confirm_usd):
                raise VpocError("vnew.generate.real", "NoConfirmUsd", "확인 금액(confirmUsd)이 필요합니다.", 400)
            audio = payload.get("audio") if isinstance(payload.get("audio"), bool) else None
            # 작업 126: cast(선택) [{name, charId, look}] — 이 회차만 인물을 바꾼다(검증은 start_real, 400 BadCast).
            run = generate.start_real(owner, pid, confirm_usd, audio=audio, cast=payload.get("cast"))
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "run": run, "plan": plan_mod.plan_view(owner, pid, data)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # 작업 134: 끝난 실사 회차 n 을 새 생성 없이 다시 이어 붙인다(컷마다 형식 맞춤). 관리자만, 소유자 확인, 유료 호출 0.
    # 새 키 final-real-r{n}-fixed.mp4 에 두고 회차 기록 finalKey 를 바꾼다(원래 키는 finalKeyBroken).
    @bp.post("/p/<pid>/generate/real/<int:n>/remerge")
    @deps.require_auth
    def plan_generate_real_remerge(pid: str, n: int):
        try:
            from . import generate  # noqa: PLC0415

            if not deps.is_admin():
                raise VpocError("vnew.generate.remerge", "AdminOnly", "관리자만 다시 이어 붙일 수 있습니다.", 403)
            owner = _project_for(pid, write=True)
            data, _ = plan_mod.load_plan(owner, pid)
            gen_id = str((data.get("gen") or {}).get("genId") or "")
            result = generate.remerge_real_cuts(owner, pid, gen_id, n)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "remerge": result, "plan": plan_mod.plan_view(owner, pid, data)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # 작업 126: [인물 바꿔 다시 만들기] 창 재료(이 회차 인물·소유자 캐릭터와 모습별 못 고르는 이유). 창을 열 때만 부른다.
    @bp.get("/p/<pid>/generate/real/cast")
    @deps.require_auth
    def plan_generate_real_cast(pid: str):
        try:
            from . import generate  # noqa: PLC0415

            generate._require_take_real()  # noqa: SLF001
            owner = _project_for(pid, write=False)
            return jsonify(generate.real_cast_options(owner, pid))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/generate/retry")
    @deps.require_auth
    def plan_generate_retry(pid: str):
        try:
            from . import generate  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            seg_id = str(payload.get("segId") or "")
            if not seg_id:
                raise VpocError("vnew.generate", "NoSegId", "구간 id(segId)가 필요합니다.", 400)
            variant = str(payload.get("variant") or "real")
            # vnew-live-fx C.6: "소리 없이 다시 만들기"는 audio:false. bool 일 때만 넘긴다(그 밖은 칸 설정 그대로).
            audio = payload.get("audio") if isinstance(payload.get("audio"), bool) else None
            generate.retry_segment(owner, pid, seg_id, variant, audio=audio)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "plan": plan_mod.plan_view(owner, pid, data)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/select")
    @deps.require_auth
    def plan_select(pid: str):
        try:
            from . import generate  # noqa: PLC0415

            owner = _project_for(pid, write=True)
            payload = request.get_json(silent=True) or {}
            variant = str(payload.get("variant") or "")
            generate.select_variant(owner, pid, variant)
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "plan": plan_mod.plan_view(owner, pid, data)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ⑦ 편집(영상 편집 타임라인, 지시서 110)
    #
    # ⑤ 결과 영상을 클립으로 늘어놓은 편집 문서(gen·그림체마다 1개, 작업 189) 보기·저장·내보내기와 재료 필름스트립·파형. edit.py 가 맡는다.
    # 설정(VNEW_EDIT)이 꺼져 있으면 프로젝트를 보기 전에 404 EditDisabled(새 라우트가 없는 것과 같다).
    # 보기·재료는 읽기 권한, 저장·내보내기는 쓰기 권한(프로젝트 소유자). 저장 경로의 주인은 프로젝트 소유자다.

    @bp.get("/p/<pid>/edit")
    @deps.require_auth
    def edit_get(pid: str):
        try:
            from . import edit  # noqa: PLC0415

            edit.require_enabled("vnew.edit")
            owner = _project_for(pid, write=False)
            variant = request.args.get("variant")
            return jsonify(edit.edit_view(owner, pid, variant))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    def _edit_body(where: str) -> dict:
        """편집 저장·내보내기 바디(JSON). 앱 전체 한도(영상 업로드용 100MB) 대신 edit.MAX_BODY_BYTES 까지만 읽는다(413)."""
        from werkzeug.exceptions import RequestEntityTooLarge  # noqa: PLC0415

        from . import edit  # noqa: PLC0415

        too_large = VpocError(where, "BodyTooLarge", "편집 문서가 너무 큽니다.", 413)
        # 한도 + 1바이트까지만 읽는다. 길이를 아는 요청이 그보다 크면 werkzeug 가 RequestEntityTooLarge 를 내고, 길이를 모르는
        # 요청(chunked)은 거기서 잘린다. 어느 쪽이든 한도를 넘으면 413.
        request.max_content_length = edit.MAX_BODY_BYTES + 1
        try:
            data = request.get_data(cache=True)
        except RequestEntityTooLarge:
            raise too_large from None
        if len(data) > edit.MAX_BODY_BYTES:
            raise too_large
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    @bp.put("/p/<pid>/edit")
    @deps.require_auth
    def edit_put(pid: str):
        try:
            from . import edit  # noqa: PLC0415

            edit.require_enabled("vnew.edit.save")
            owner = _project_for(pid, write=True)
            return jsonify(edit.save_doc(owner, pid, _edit_body("vnew.edit.save")))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/edit/export")
    @deps.require_auth
    def edit_export(pid: str):
        try:
            from . import edit  # noqa: PLC0415

            edit.require_enabled("vnew.edit.export")
            owner = _project_for(pid, write=True)
            payload = _edit_body("vnew.edit.export")
            result = edit.start_export(owner, pid, payload.get("etag"), payload.get("variant"))  # 작업 189: variant 없으면 etag 로 고른다
            return jsonify({"ok": True, **result}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.get("/p/<pid>/edit/media/<mid>")
    @deps.require_auth
    def edit_media(pid: str, mid: str):
        try:
            from . import edit  # noqa: PLC0415

            edit.require_enabled("vnew.edit.media")
            owner = _project_for(pid, write=False)
            return jsonify(edit.media_view(owner, pid, mid))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # 작업 185: 빈틈 다듬기 제안(저장하지 않는다 — 화면이 한 번에 되돌릴 수 있게 적용하고 PUT 으로 저장). 저장과 같은 쓰기 권한(소유자).
    @bp.post("/p/<pid>/edit/tighten")
    @deps.require_auth
    def edit_tighten(pid: str):
        try:
            from . import edit  # noqa: PLC0415

            edit.require_enabled("vnew.edit.tighten")
            owner = _project_for(pid, write=True)
            return jsonify(edit.tighten_view(owner, pid, _edit_body("vnew.edit.tighten")))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ⑦ AI로 고치기(지시서 116)
    #
    # ⑦ 재료(테이크·컷) 하나를 이전 프롬프트 + 결과 프레임 + 수정 요청으로 고쳐 같은 경로로 새 버전을 만든다. retake.py 가 맡는다.
    # VNEW_EDIT·VNEW_EDIT_RETAKE 가 꺼져 있으면 프로젝트를 보기 전에 404(라우트가 없는 것과 같다). 프롬프트 기록·초안·비용이 보이므로
    # 셋 다 프로젝트 소유자(쓰기 권한)만. 미리보기·제출은 202 + GET 조회(LLM·영상 모델은 백그라운드 스레드).

    @bp.get("/p/<pid>/edit/retake/<mid>")
    @deps.require_auth
    def edit_retake_view(pid: str, mid: str):
        try:
            from . import edit, retake  # noqa: PLC0415

            edit.require_enabled("vnew.retake")
            retake.require_enabled("vnew.retake")
            owner = _project_for(pid, write=True)
            return jsonify(retake.retake_view(owner, pid, mid, request.args.get("in"), request.args.get("out")))  # 보완 1: 고른 클립 구간
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/edit/retake/<mid>/preview")
    @deps.require_auth
    def edit_retake_preview(pid: str, mid: str):
        try:
            from . import edit, retake  # noqa: PLC0415

            edit.require_enabled("vnew.retake.preview")
            retake.require_enabled("vnew.retake.preview")
            owner = _project_for(pid, write=True)
            payload = _edit_body("vnew.retake.preview")
            return jsonify({"ok": True, "retake": retake.start_preview(owner, pid, mid, payload)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/edit/retake/<mid>/compose")
    @deps.require_auth
    def edit_retake_compose(pid: str, mid: str):
        # 작업 191: 이전 프롬프트 + 수정 사항 → 고른 컷 구간(in·out)만 영상→영상 초안. 번역 한 문장만 LLM(최대 20초) — 초안은 바로 done(200).
        try:
            from . import edit, retake  # noqa: PLC0415

            edit.require_enabled("vnew.retake.compose")
            retake.require_enabled("vnew.retake.compose")
            owner = _project_for(pid, write=True)
            payload = _edit_body("vnew.retake.compose")
            return jsonify({"ok": True, "retake": retake.compose(owner, pid, mid, payload)})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/edit/retake/<mid>")
    @deps.require_auth
    def edit_retake_submit(pid: str, mid: str):
        try:
            from . import edit, retake  # noqa: PLC0415

            edit.require_enabled("vnew.retake.submit")
            retake.require_enabled("vnew.retake.submit")
            owner = _project_for(pid, write=True)
            payload = _edit_body("vnew.retake.submit")
            return jsonify({"ok": True, "retake": retake.submit(owner, pid, mid, payload)}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    # ------------------------------------------------------------ ⑥ 사운드(작업 122)
    #
    # 대사마다 실제 말/속마음을 정하고(GET 이 처음 만든다·PUT 이 고친 값만 저장) 원래 소리의 속마음 구간에만 효과를 건 새 mp4 를
    # 만든다(POST apply, 백그라운드·영상은 복사만). sound.py 가 맡는다. VNEW_SOUND 가 꺼져 있으면 프로젝트를 보기 전에 404.
    # GET 도 문서를 만들고(쓰기) 원본을 내려받으므로 셋 다 프로젝트 소유자(쓰기 권한)만 — 그 밖 사용자에게는 404(있는지 감춘다).

    def _sound_project(pid: str) -> str:
        try:
            return _project_for(pid, write=True)
        except VpocError as exc:
            if exc.kind == "Forbidden":
                raise VpocError("vnew.sound", "NotFound", "프로젝트를 찾을 수 없습니다.", 404) from None
            raise

    @bp.get("/p/<pid>/sound")
    @deps.require_auth
    def sound_get(pid: str):
        try:
            from . import sound  # noqa: PLC0415

            sound.require_enabled("vnew.sound")
            owner = _sound_project(pid)
            return jsonify(sound.sound_view(owner, pid, request.args.get("variant")))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.put("/p/<pid>/sound")
    @deps.require_auth
    def sound_put(pid: str):
        try:
            from . import sound  # noqa: PLC0415

            sound.require_enabled("vnew.sound.save")
            owner = _sound_project(pid)
            return jsonify(sound.save_lines(owner, pid, _edit_body("vnew.sound.save")))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    @bp.post("/p/<pid>/sound/apply")
    @deps.require_auth
    def sound_apply(pid: str):
        try:
            from . import sound  # noqa: PLC0415

            sound.require_enabled("vnew.sound.apply")
            owner = _sound_project(pid)
            return jsonify({"ok": True, **sound.start_apply(owner, pid, _edit_body("vnew.sound.apply"))}), 202
        except Exception as exc:  # noqa: BLE001
            return _fail(exc)

    return bp
