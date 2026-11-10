"""CSS 괄호 균형 검사(11-11). 합치기 뒤 규칙이 반쯤 잘려 `{` 가 남으면 그 뒤 CSS 가 통째로 무시된다(215b·215c 합치기 사고).

frontend/public 의 *.css 마다 주석을 뺀 뒤 `{` 와 `}` 개수가 같고, 어느 지점에서도 `}` 가 더 많지 않으며,
@media · @supports · @keyframes · @font-face 밖에서 규칙 안에 규칙이 열리지 않는지 본다. 네트워크 0.
"""
from __future__ import annotations

import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[1] / "frontend" / "public"
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_AT_GROUP = re.compile(r"@(media|supports|keyframes|-webkit-keyframes|layer|container|document)\b[^{;]*$")


def _problems(text: str) -> list[str]:
    stripped = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    depth = 0
    group_depths: list[int] = []
    out: list[str] = []
    line = 1
    start = 0
    for i, ch in enumerate(stripped):
        if ch == "\n":
            line += 1
        elif ch == "{":
            head = stripped[start:i].strip().split("}")[-1].split(";")[-1]
            is_group = bool(_AT_GROUP.search(head))
            inside_rule = depth > len(group_depths)
            if inside_rule and not is_group and len(out) < 5:
                out.append(f"{line}번째 줄: 규칙 안에서 새 규칙이 열립니다(앞 규칙이 닫히지 않았을 수 있음)")
            if is_group:
                group_depths.append(depth)
            depth += 1
            start = i + 1
        elif ch == "}":
            depth -= 1
            if group_depths and group_depths[-1] == depth:
                group_depths.pop()
            if depth < 0:
                out.append(f"{line}번째 줄: 여는 괄호 없이 닫힙니다")
                depth = 0
            start = i + 1
        elif ch == ";":
            start = i + 1
    if depth:
        out.append(f"끝까지 닫히지 않은 괄호 {depth}개")
    return out


def check_css_balance() -> bool:
    ok = True
    for path in sorted(PUBLIC.glob("*.css")):
        problems = _problems(path.read_text(encoding="utf-8"))
        if problems:
            ok = False
            print(f"[FAIL] CSS 괄호 균형 {path.name}: " + " · ".join(problems[:5]))
    if ok:
        print("[ok  ] CSS 괄호 균형(frontend/public/*.css: 닫히지 않은 규칙 · 규칙 안 규칙 없음)")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if check_css_balance() else 1)
