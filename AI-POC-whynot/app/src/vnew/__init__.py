"""vnew — 영상만들기_신규(설계자 2026-10-20). 지금은 틀만이다.

웹툰 이미지만 넣으면 콘티·영상까지 만드는 새 영상 기능의 자리를 잡아 둔다. vpoc2 와
이름·파일·라우트가 겹치지 않게 패키지 이름을 vnew 로 둔다(vpoc2 는 이미 다른 기능이 쓴다).

server.py 등록 (한 곳, VNEW_ENABLED 가 켜졌을 때만):
    import vnew
    app.register_blueprint(vnew.create_blueprint(vnew.VnewDependencies(
        require_auth=require_auth)))
"""
from .routes import VnewDependencies, create_blueprint

__all__ = ["VnewDependencies", "create_blueprint"]
