"""vnew 라우트 (/api/vnew). 인증은 앱에서 주입받는다 — g 를 직접 읽지 않는다.

지금은 틀만이다. 연결 확인용 health 하나뿐이고, 화면(frontend/public/vnew.js)이 이걸
불러 서버가 켜져 있는지 보인다. 기능은 뒤 단계에서 붙인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, jsonify


@dataclass
class VnewDependencies:
    require_auth: Callable[[Callable], Callable]


def create_blueprint(deps: VnewDependencies) -> Blueprint:
    bp = Blueprint("vnew", __name__, url_prefix="/api/vnew")

    @bp.get("/health")
    @deps.require_auth
    def health():
        # 틀 확인용. 인증을 통과하면 서버에 vnew 블루프린트가 등록돼 있다는 뜻이다.
        return jsonify({"ok": True})

    return bp
