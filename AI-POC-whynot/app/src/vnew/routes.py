"""vnew 라우트 (/api/vnew). 인증·소유자는 앱에서 주입받는다 — g 를 직접 읽지 않는다.

캐릭터 만들기(설계자 2026-10-20): 웹툰 이미지 → 분석 → 실사·웹툰 시트 → 3D 모델.
저장·파이프라인은 characters.py 에 있다. 여기서는 HTTP 입출력만 다룬다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, Response, jsonify, request

from ..vpoc.errors import VpocError, envelope, status_for

from .. import project_tracks
from . import characters
from . import plan as plan_mod


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
            version = characters.start_build(owner, char_id)
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

    # ------------------------------------------------------------ ④ 영상 생성

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
            gen_id = generate.start_generate(owner, pid, float(confirm))
            data, _ = plan_mod.load_plan(owner, pid)
            return jsonify({"ok": True, "genId": gen_id, "plan": plan_mod.plan_view(owner, pid, data)}), 202
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
            generate.retry_segment(owner, pid, seg_id, variant)
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

    return bp
