"""vnew 라우트 (/api/vnew). 인증·소유자는 앱에서 주입받는다 — g 를 직접 읽지 않는다.

캐릭터 만들기(설계자 2026-10-20): 웹툰 이미지 → 분석 → 실사·웹툰 시트 → 3D 모델.
저장·파이프라인은 characters.py 에 있다. 여기서는 HTTP 입출력만 다룬다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, Response, jsonify, request

from ..vpoc.errors import VpocError, envelope, status_for

from . import characters


@dataclass
class VnewDependencies:
    """server.py 가 주입한다. `g` 에 의존하는 것은 전부 호출자에서 받는다.

    current_owner: S3 키의 소유자 접두사(safe_user_prefix(username)). 쓰기·조회가 이 값으로
    users/{owner}/... 아래에 갇힌다 — 다른 사용자 데이터에 닿지 않는다.
    """

    require_auth: Callable[[Callable], Callable]
    current_owner: Callable[[], str] | None = None


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

    return bp
