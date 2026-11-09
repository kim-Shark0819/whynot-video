"""vnew ⑥ 사운드 — 인물 목소리 라우트(작업 202). voices.py 가 맡는다. 여기서는 HTTP 입출력만 다룬다.

routes.create_blueprint 안 ⑥ 사운드 라우트 뒤에서 한 줄로 붙인다: voices_routes.register(bp, deps, _sound_project, _fail)
  PUT  /p/<pid>/sound/voices          인물 목소리(프로필) 저장 → {voices}
  POST /p/<pid>/sound/voice-preview   한 줄 들어보기(dryRun 이면 견적만) → {preview, estimate, charge} | {estimate}
  POST /p/<pid>/sound/voice-apply     전체 반영 시작(dryRun 이면 견적만) → 202 {ok, started, job, estimate} | 200 {estimate}
VNEW_SOUND·VNEW_SOUND_VOICES 가 꺼져 있으면 프로젝트를 보기 전에 404. 권한은 사운드 라우트와 같다(project_for = 쓰기 권한, 없으면 404).
"""

from __future__ import annotations

from flask import jsonify, request

from ..vpoc.errors import VpocError

MAX_BODY_BYTES = 16384  # 목소리 라우트 바디 상한. voices 는 요청 때 import 한다(사운드 라우트와 같다 — 블루프린트를 만들 때 무거운 모듈을 읽지 않는다).


def _body(where: str) -> dict:
    """작은 JSON 바디(MAX_BODY_BYTES 까지만 읽는다 — 넘으면 413). routes._edit_body 와 같은 방식."""
    from werkzeug.exceptions import RequestEntityTooLarge  # noqa: PLC0415

    too_large = VpocError(where, "BodyTooLarge", "요청 본문이 너무 큽니다.", 413)
    request.max_content_length = MAX_BODY_BYTES + 1
    try:
        data = request.get_data(cache=True)
    except RequestEntityTooLarge:
        raise too_large from None
    if len(data) > MAX_BODY_BYTES:
        raise too_large
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def register(bp, deps, project_for, fail) -> None:
    """bp(vnew 블루프린트)에 목소리 라우트 3개를 더한다. project_for(pid) → 소유자 접두사, fail(exc) → 오류 응답."""

    @bp.put("/p/<pid>/sound/voices")
    @deps.require_auth
    def sound_voices_put(pid: str):
        try:
            from . import voices  # noqa: PLC0415

            voices.require_enabled("vnew.sound.voices")
            owner = project_for(pid)
            return jsonify(voices.save_profile(owner, pid, _body("vnew.sound.voices")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.post("/p/<pid>/sound/voice-preview")
    @deps.require_auth
    def sound_voice_preview(pid: str):
        try:
            from . import voices  # noqa: PLC0415

            voices.require_enabled("vnew.sound.voice_preview")
            owner = project_for(pid)
            return jsonify(voices.preview(owner, pid, _body("vnew.sound.voice_preview")))
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    @bp.post("/p/<pid>/sound/voice-apply")
    @deps.require_auth
    def sound_voice_apply(pid: str):
        try:
            from . import voices  # noqa: PLC0415

            voices.require_enabled("vnew.sound.voice_apply")
            owner = project_for(pid)
            result = voices.start_voice_apply(owner, pid, _body("vnew.sound.voice_apply"))
            if result.get("started"):
                return jsonify({"ok": True, **result}), 202
            return jsonify(result)
        except Exception as exc:  # noqa: BLE001
            return fail(exc)
