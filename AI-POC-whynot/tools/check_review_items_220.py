#!/usr/bin/env python3
"""작업 220: 코드 리뷰 결함 1~14 항목별 검증 — 고침이 지금 코드에 있고, 그 검사가 옛(고치기 전) 동작에서 실패하는지 함께 본다.

항목마다 (가) 지금 코드에서 검사가 모두 통과하고 (나) 옛 동작을 되살린 변이(mutant)마다 그 검사가 실패해야 [ok  ] 다.
변이는 검사할 때만 프로세스 안에서 함수를 바꾼다(파일은 그대로): 파이썬은 함수 소스 한 줄을 옛 줄로 바꿔 다시 정의하거나
속성을 옛 동작 함수로 바꾸고, vnew.js 는 임시 폴더 사본의 한 줄을 바꾼다. 변이 자리를 찾지 못하면(코드가 바뀌었다) [FAIL].
변이마다 따로 하위 프로세스(병렬)라 서로 섞이지 않는다.

검사 묶음: 219:rf-N = check_review_fixes_219, 219b:rf-x = check_review_fixes_219b, x:xN = 이 파일의 새 검사.
  (x1) 215c 파생 원본(정방향 · 속도 1)도 116 대체 소리(alt)를 계획 뒤 키로 받는다 · 역재생은 받지 않는다 · run_export 는 도우미 하나로 부른다
  (x2) A1 끔 · 다른 트랙 솔로면 대사만 스템이 없다(그래서 run_export 가 rf-2 의 무음 대사 길을 탄다)
  (x5) Delete · Shift+Delete · Backspace · Alt+Delete × ⑦/Premiere 프리셋 × 고른 자막 · 타이틀 · 소리 아이템 → 그것만 지움 · 고른 것은 하나
  (x6) 분리: 시간 상한을 넘긴 running(GET 없이)에 다시 눌러도 requestId 로 이어 봄(제출 · 원장 그대로)
  (x7) V1 잠김: 같은 표에서 자막 · 타이틀 · 소리 아이템 지우기 통과 · 자막 · 타이틀 S 자르기 통과
  (x12) 분리 원장: 단가를 한 번도 못 읽음 → 원장 항목 priceUnknown: True · usd = FALLBACK_USD_PER_S × 길이(0 아님) · 보통 계상엔 priceUnknown 칸 없음
  (x12b) 작업 220b 목소리 바꾸기(voices.convert) · 새 길 다시 만들기(_charge_retake): 단가 모름 → 원장 usd 추정(0 아님) · priceUnknown: True ·
         아는 단가 → 견적 그대로 · priceUnknown 칸 없음 · resubmit_model 이 도우미로 계상
  (x13) 대사 고침: running 인데 retake 상태에 버전이 ORPHAN_S 넘게 없으면 failed(끝없는 running 없음) · 젊은 것 · 찾은 것 · 옛 기록은 그대로

node 가 없으면(또는 --no-node · CHECK_220_NO_NODE=1) node 묶음 항목은 [WARN] 이고 FAIL 하지 않는다.
실행: python -X utf8 -B tools/check_review_items_220.py
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_review_items_220").check_review_items_220(): fail("작업 220 리뷰 항목 1~14 검증 실패")
"""

from __future__ import annotations

import __future__ as _future
import concurrent.futures
import contextlib
import copy
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_review_items_220").check_review_items_220(): '
        'fail("작업 220 리뷰 항목 1~14 검증 실패")')
NODE_GROUPS = {"219b:rf-a", "219b:rf-b", "219b:rf-c", "219b:rf-d", "x:x5", "x:x7"}
TAG = "@@220 "

# (항목, 제목, 지금 코드에서 통과할 묶음, 옛 동작 변이)
ITEMS = [
    (1, "내보내기: 215c 계획 뒤 분리(빈틈 · 사용 안 함 · 역재생 · 빈틈 둘 · alt)", ["219:rf-1", "x:x1"], ["m1a", "m1b"]),
    (2, "A1 끔 · 다른 트랙 솔로 → 대사.mp3 무음", ["219:rf-2", "x:x2"], ["m2"]),
    (3, "대사 고침 판 키 고유(잇기 · rebuild_revision)", ["219:rf-6"], ["m3"]),
    (4, "붙기 Shift+N · 두 🧲 툴팁 · 안내 글 · 215a/215c 상태 하나", ["219b:rf-a"], ["m4a", "m4b"]),
    (5, "고른 자막 · 타이틀 · 소리 아이템 → Delete · Shift+Delete 는 그것만(두 프리셋)", ["219b:rf-b", "x:x5"], ["m5", "m5b"]),
    (6, "분리: 결과 받기 5xx · 429 → RUN_MAX_S 뒤 failed · requestId 가 있으면 다시 내지 않음",
     ["219:rf-8", "219:rf-9", "x:x6"], ["m6a", "m6b", "m6c"]),
    (7, "V1 잠금은 V1 클립만(소리 · 자막 지우기 · 소리 끄기 통과, S 는 소리만)", ["219b:rf-c", "x:x7"], ["m7a", "m7b"]),
    (8, "hasAudio: 들리는 것이 없으면 False · README 무음 안내", ["219:rf-3"], ["m8"]),
    (9, "스템 · 임시 파일 지움(성공 · 실패) · 분리 임시 inputKey(fail · start except)", ["219:rf-4", "219:rf-10"], ["m9a", "m9b"]),
    (10, "Ctrl+R 은 클립 하나일 때만(그 밖은 새로 고침)", ["219b:rf-d"], ["m10"]),
    (11, "프리미어 컨트롤 만들지 않음(EDIT_PREMIERE_UI) · /premiere GET 없음", ["219b:rf-f"], ["m11"]),
    (12, "가격 모름 → 추정 금액 + priceUnknown(조용히 0 아님)", ["219:rf-11", "x:x12", "x:x12b"], ["m12", "m12b", "m12c", "m12d"]),
    (13, "제출된 retake: quoted 로 남지 않음 · 다시 누르면 이어받음 · 끝없는 running 없음", ["219:rf-7", "x:x13"],
     ["m13a", "m13b", "m13c"]),
    (14, "옛 color 글자 · 바뀐 원본 분리 None · 잠금 검사 하나 · save_doc 바꿔 쓰기",
     ["219:rf-5", "219:rf-12", "219b:rf-e", "219b:rf-g"], ["m14a", "m14b", "m14c", "m14d"]),
]

# 변이: 이름 → (설명, 실패해야 할 묶음, 적용)  적용 = ("py", 모듈, 함수, 옛 글, 새 글) · ("attr", 모듈, 이름, 값 이름) ·
# ("js", 옛 글, 새 글) · ("jsline", 지울 줄에 든 글)
MUTANTS: dict[str, tuple[str, list[str], list[tuple]]] = {
    "m1a": ("분리 결과를 215c 계획 앞에서 고름(옛 순서)", ["219:rf-1"], [("attr", "E", "_export_plan_sources", "old_plan_sources")]),
    "m1b": ("파생 원본에 116 대체 소리를 옮기지 않음", ["x:x1"],
            [("py", "sep", "export_sources", "if entry and key != lookup:", "if False:")]),
    "m2": ("대사 스템이 없어도 무음 대사를 만들지 않음(전체 소리)", ["219:rf-2"],
           [("py", "E", "run_export", "if not dialogue_wav:", "if False:")]),
    "m3": ("판 키에 token 없음(r 만)", ["219:rf-6"],
           [("py", "LR", "run_splice", "revision_key(owner, pid, gen_id, variant, r, secrets.token_hex(3))",
             "revision_key(owner, pid, gen_id, variant, r)"),
            ("py", "LR", "rebuild_revision", "revision_key(owner, pid, gen_id, variant, r, secrets.token_hex(3))",
             "revision_key(owner, pid, gen_id, variant, r)")]),
    "m4a": ("툴팁 · 안내 글이 (N)", ["219b:rf-a"], [("js", "(Shift+N)", "(N)")]),
    "m4b": ("215a 붙기가 따로 상태(ctx.mixSnap)", ["219b:rf-a"],
            [("js", "return edit215cSnapOn(ctx) ? EDIT_MIX_SNAP_PX", "return ctx.mixSnap !== false ? EDIT_MIX_SNAP_PX")]),
    "m5": ("들어내기 · 지우고 당기기가 바로 클립으로", ["219b:rf-b", "x:x5"],
           [("jsline", '(action === "lift" || action === "rippleDelete") && edit215cDeleteOther(ctx)')]),
    "m5b": ("자막 · 소리 아이템을 함께 고른 채로 둠", ["x:x5"],
            [("jsline", "if (t !== null && ctx.txSel) editTxSelect(ctx, null);"), ("js", "if (kind && ctx.mixSel) {", "if (false) {")]),
    "m6a": ("결과 받기 5xx · 429 에 시간 상한 없음", ["219:rf-8"],
            [("py", "sep", "_advance", 'if _age_s(state.get("startedAt") or "") > RUN_MAX_S:  # 작업 219', "if False:  #")]),
    "m6b": ("실패 뒤 다시 누르면 새로 제출", ["219:rf-9"], [("attr", "sep", "_resumable", "never")]),
    "m6c": ("시간 넘긴 running 에 다시 누르면 새로 제출", ["x:x6"],
            [("py", "sep", "_resumable", 'stale = status == "running" and _age_s(state.get("startedAt") or "") >= RUN_MAX_S',
              "stale = False")]),
    "m7a": ("V1 잠금이 소리 아이템 지우기 · 소리 끄기도 막음", ["219b:rf-c", "x:x7"],
            [("js", "return !!(ctx.mix && editMixItemRef(ctx));", "return false;")]),
    "m7b": ("V1 잠김 S 가 막힘(소리 아이템도 안 나눔)", ["219b:rf-c"],
            [("jsline", 'if (action === "split" && edit215cLocked(ctx)) return edit215cSplitLocked(ctx);')]),
    "m8": ("소리 문서가 있으면 hasAudio 늘 True", ["219:rf-3"],
           [("py", "E", "run_export", 'with_audio = (with_audio and bool(mix_plan["a1On"])) or bool(mix_plan["buses"])',
             "with_audio = True")]),
    "m9a": ("스템 · 무음 대사 파일을 지우지 않음", ["219:rf-4"], [("attr", "E", "_drop_files", "noop")]),
    "m9b": ("fail() · start except 에서 임시 입력을 지우지 않음", ["219:rf-10"],
            [("py", "sep", "_advance", '_drop_input(state.get("inputKey"))\n        return _finish(key, claim, {"status": "failed"',
              'return _finish(key, claim, {"status": "failed"'),
             ("py", "sep", "start", '_drop_input(box.get("inputKey"))  # 작업 219', "pass  #")]),
    "m10": ("Ctrl+R 을 늘 가로챔", ["219b:rf-d"], [("jsline", 'action === "speedDialog" && edit215cSel(ctx).length !== 1')]),
    "m11": ("프리미어 컨트롤을 만듦", ["219b:rf-f"], [("js", "var EDIT_PREMIERE_UI = false;", "var EDIT_PREMIERE_UI = true;")]),
    "m12": ("가격 모름 → 조용히 0", ["219:rf-11", "x:x12"], [("attr", "sep", "_charge_usd", "silent_zero")]),
    "m12b": ("목소리 원장: 가격 모름 → 조용히 0(220b 전 convert 줄)", ["x:x12b"],
             [("py", "V", "convert", "usd, unknown = _charge_usd(item, seconds)", 'usd, unknown = float(item.get("usd") or 0.0), False')]),
    "m12c": ("다시 만들기 원장: resubmit_model 이 견적을 그대로 · 표시 없이(220b 전 줄)", ["x:x12b"],
             [("py", "G", "resubmit_model", "usd, unknown = _retake_charge_usd(info)", 'usd, unknown = float(info["usd"] or 0), False')]),
    "m12d": ("다시 만들기 원장 금액: 가격 모름 → 견적 그대로(0 이어도) · 표시 없음", ["x:x12b"],
             [("attr", "G", "_retake_charge_usd", "retake_old")]),
    "m13a": ("견적 · 시작이 나간 retake 를 이어받지 않음(다시 제출)", ["219:rf-7"], [("attr", "LR", "_adopt_submitted", "none")]),
    "m13b": ("제출 뒤 running 쓰기를 한 번만", ["219:rf-7"], [("attr", "LR", "START_WRITE_TRIES", "one")]),
    "m13c": ("버전 없는 running 을 끝내지 않음", ["x:x13"], [("attr", "LR", "ORPHAN_S", "inf")]),
    "m14a": ("옛 color 글자 → 400 BadClipColor", ["219:rf-5"],
             [("py", "ET", "clean_clip_fx", "if isinstance(raw_color, str):", "if False:")]),
    "m14b": ("원본이 바뀌어도 분리 결과를 씀", ["219:rf-12"], [("attr", "sep", "_source_changed", "never")]),
    "m14c": ("edit215cDo 잠금 검사 두 번", ["219b:rf-e"],
             [("js", "    if (edit215cBlocked(ctx, action)) return;\n    var fps = EDIT_DEFAULT_FPS;",
               "    if (edit215cBlocked(ctx, action)) return;\n    if (edit215cBlocked(ctx, action)) return;\n    var fps = EDIT_DEFAULT_FPS;")]),
    "m14d": ("save_doc 이 지난 저장본의 markers · tracks · audioTracks 를 합침", ["219b:rf-g"],
             [("py", "E", "save_doc", "doc.update(edit_tools.clean_doc_extras(payload, where))  # 작업 215c",
               "doc.update(edit_tools.clean_doc_extras(payload, where)); doc.update({k: v for k, v in (current or {}).items() "
               "if k in ('markers', 'tracks', 'audioTracks') and k not in doc})")]),
}

_EXTRA_JS = r"""
var X = { x5: [], x7: [], sel: null, split: null, err: "" };
try {
  var setup = function (preset, locked, target) {
    var c = fresh();
    var st = edit215cState(c);
    st.preset = preset;
    st.tracks = locked ? { v1: { locked: true } } : {};
    c.tx.titles = [{ id: "t1", start: 0.2, end: 2.5, text: "제목", style: {} }];
    editSelect(c, 0);
    if (target === "caption") c.txSel = { kind: "caption", id: "s1" };
    if (target === "title") c.txSel = { kind: "title", id: "t1" };
    if (target === "mix") c.mixSel = { t: 0, i: 0 };
    return c;
  };
  var KEYS = [["Delete", {}], ["Delete", { shiftKey: true }], ["Backspace", {}], ["Backspace", { shiftKey: true }], ["Delete", { altKey: true }]];
  ["vnew", "premiere"].forEach(function (preset) {
    [false, true].forEach(function (locked) {
      KEYS.forEach(function (k) {
        ["caption", "title", "mix"].forEach(function (target) {
          var c = setup(preset, locked, target);
          notes.length = 0;
          press(c, k[0], k[0], k[1]);
          var got = [c.tx.captions.length, c.tx.titles.length, items(c), ids(c).join(","), notes.indexOf(EDIT215C_LOCKED_NOTE) >= 0];
          var want = [target === "caption" ? 0 : 1, target === "title" ? 0 : 1, target === "mix" ? 0 : 1, "c1,c2,c3", false];
          var name = preset + (locked ? " V1잠김 " : " ") + (k[1].shiftKey ? "Shift+" : k[1].altKey ? "Alt+" : "") + k[0] + " " + target;
          (locked ? X.x7 : X.x5).push([name, got, want]);
        });
      });
    });
  });
  var c = setup("vnew", false, "none"); c.sel = -1; c.multi = [];
  editTxSelect(c, "caption", "s1"); editMixPick(c, 0, 0);
  var a = [c.txSel, !!c.mixSel]; press(c, "Delete", "Delete");
  var a2 = [c.tx.captions.length, items(c), ids(c).join(",")];
  c = setup("premiere", false, "none"); c.sel = -1; c.multi = [];
  editMixPick(c, 0, 0); editTxSelect(c, "caption", "s1");
  var b = [!!c.txSel, c.mixSel]; press(c, "Delete", "Delete");
  var b2 = [c.tx.captions.length, items(c), ids(c).join(",")];
  X.sel = [a, a2, b, b2];
  c = setup("vnew", true, "caption"); c.t = 1.0; notes.length = 0;
  press(c, "s", "KeyS");
  var cap = [c.tx.captions.length, ids(c).join(","), items(c), notes.length];
  c = setup("premiere", true, "title"); c.t = 1.0; notes.length = 0;
  press(c, "s", "KeyS");
  X.split = [cap, [c.tx.titles.length, ids(c).join(","), items(c), notes.length]];
} catch (e) {
  X.err = String(e && e.stack || e).slice(0, 800);
}
console.log(JSON.stringify(X));
"""


# ================================================================ 하위 프로세스(변이 하나 또는 base)


def _modules() -> dict:
    from src.vnew import audio_separate as sep  # noqa: PLC0415
    from src.vnew import edit as E  # noqa: PLC0415
    from src.vnew import edit_text as ET  # noqa: PLC0415
    from src.vnew import edit_tools as T  # noqa: PLC0415
    from src.vnew import generate as G  # noqa: PLC0415
    from src.vnew import line_redo as LR  # noqa: PLC0415
    from src.vnew import voices as V  # noqa: PLC0415

    return {"E": E, "sep": sep, "ET": ET, "T": T, "LR": LR, "V": V, "G": G}


def _attr_values(mods: dict) -> dict:
    sep, T = mods["sep"], mods["T"]

    def old_plan_sources(owner, pid, clips, meta, audio_from, fps, width, height):
        audio_from, sep_bg = sep.export_sources(owner, pid, clips, audio_from, meta)  # 219 전: 계획 앞 클립으로 고름
        clips, meta, plan = T.export_plan(clips, meta, fps, width, height)
        return clips, meta, plan, audio_from, sep_bg

    return {"old_plan_sources": old_plan_sources, "never": lambda *a, **k: False, "noop": lambda *a, **k: None,
            "none": lambda *a, **k: None, "one": 1, "inf": float("inf"),
            "silent_zero": lambda est, seconds: (float(est.get("rawUsd") or 0.0), False),
            "retake_old": lambda info: (float(info.get("usd") or 0), False)}


def _py_patch(module, func: str, old: str, new: str) -> None:
    fn = getattr(module, func)
    src = textwrap.dedent(inspect.getsource(fn))
    if src.count(old) != 1:
        raise RuntimeError(f"변이 자리 {src.count(old)}곳(기대 1): {module.__name__}.{func}: {old[:70]!r}")
    code = compile(src.replace(old, new), inspect.getsourcefile(fn) or "<220>", "exec",
                   flags=_future.annotations.compiler_flag, dont_inherit=True)
    exec(code, module.__dict__)  # noqa: S102 — 검사 안에서만 옛 동작 함수로 바꾼다(파일은 그대로)


def _js_patch(text: str, steps: list[tuple]) -> str:
    for step in steps:
        if step[0] == "js":
            if text.count(step[1]) < 1:
                raise RuntimeError(f"vnew.js 변이 자리 없음: {step[1][:70]!r}")
            text = text.replace(step[1], step[2])
        else:
            lines = text.split("\n")
            hit = [i for i, line in enumerate(lines) if step[1] in line]
            if len(hit) != 1:
                raise RuntimeError(f"vnew.js 지울 줄 {len(hit)}곳(기대 1): {step[1][:70]!r}")
            del lines[hit[0]]
            text = "\n".join(lines)
    return text


def _run_219() -> dict:
    import check_review_fixes_219 as A  # noqa: PLC0415

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        A.check_review_fixes_219()
    return _parse(buf.getvalue(), r"^\[(ok  |FAIL|WARN)\] 작업 219 (rf-\d+)", "219:")


def _run_219b(public: Path, no_node: bool) -> dict:
    import check_review_fixes_219b as B  # noqa: PLC0415

    saved = B.PUBLIC
    B.PUBLIC = public
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            B.check_review_fixes_219b(["--no-node"] if no_node else [])
    finally:
        B.PUBLIC = saved
    return _parse(buf.getvalue(), r"^\[(ok  |FAIL|WARN)\] 작업 219b \((rf-[a-h])\)", "219b:")


def _parse(text: str, pattern: str, prefix: str) -> dict:
    rank = {"ok": 0, "warn": 1, "fail": 2}
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(pattern, line)
        if m:
            status = {"ok  ": "ok", "FAIL": "fail", "WARN": "warn"}[m.group(1)]
            name = prefix + m.group(2)
            if rank[status] >= rank.get(out.get(name, "ok"), 0):
                out[name] = status
    return out


def _x_python(mods: dict, wanted: set[str], groups: dict, details: dict) -> None:  # noqa: C901, PLR0915
    E, sep, LR = mods["E"], mods["sep"], mods["LR"]

    def verdict(name: str, got, want, what: str) -> None:
        if got == want:
            groups.setdefault(name, "ok")
        else:
            groups[name] = "fail"
            details.setdefault(name, []).append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(name: str) -> None:
        groups[name] = "fail"
        details.setdefault(name, []).append("예외: " + traceback.format_exc()[-1200:])

    if "x:x1" in wanted:
        saved = E._separated_audio  # noqa: SLF001
        try:
            K, V1 = "users/u/video/p/r.mp4", "users/u/video/p/v1.mp4"
            meta = {K: {"duration": 4.0, "hasAudio": False, "fps": 24, "width": 160, "height": 120},
                    V1: {"duration": 4.0, "hasAudio": True, "fps": 24, "width": 160, "height": 120}}
            entry = {"sourceKey": V1, "mid": "m1", "offset": 0.5, "scale": 1.0, "end": 4.0}
            E._separated_audio = lambda *a, **k: None  # noqa: SLF001

            def run(**kw):
                clip = {"id": "x", "sourceKey": K, "in": 0.0, "out": 2.0, "muted": False, **kw}
                cl, _mt, _plan, af, bg = E._export_plan_sources("u", "p", [clip], dict(meta), {K: entry}, 24, 160, 120)  # noqa: SLF001
                return cl[0]["sourceKey"], af.get(cl[0]["sourceKey"]), E._clip_audio(cl[0], af.get(cl[0]["sourceKey"])), bg  # noqa: SLF001

            hide, rev = run(hideVideo=True), run(reverse=True)
            verdict("x:x1", [hide[0].startswith("215c:d"), hide[1], hide[2] is not None, hide[3]],
                    [True, entry, True, []], "화면만 숨긴 소리 없는 버전: 116 대체 소리를 계획 뒤 키로(alt 있음)")
            verdict("x:x1", [rev[0].startswith("215c:d"), rev[1], rev[2], rev[3]], [True, None, None, []],
                    "역재생: 대체 소리를 거꾸로 맞출 수 없어 넣지 않음(정방향 소리 없음)")
            src = inspect.getsource(E.run_export)
            verdict("x:x1", [src.count("_export_plan_sources("), "export_sources(" in src.replace("_export_plan_sources(", ""),
                             "edit_tools.export_plan(" in src], [1, False, False],
                    "run_export 는 도우미(계획 → 분리) 하나로만 부른다")
        except Exception:  # noqa: BLE001
            crash("x:x1")
        finally:
            E._separated_audio = saved  # noqa: SLF001

    if "x:x2" in wanted:
        try:
            from src.vnew import edit_audio as ea  # noqa: PLC0415

            item = {"id": "i1", "assetId": "a" * 17, "start": 0.0, "in": 0.0, "out": 1.0, "gainDb": 0.0, "fadeIn": 0.0, "fadeOut": 0.0}

            def plan(track_kw, a1_kw):
                p = ea.mix_plan([{"id": "t1", "mute": False, "solo": False, "gainDb": 0.0, "items": [item], **track_kw}],
                                dict(ea.default_a1(), **a1_kw), total_samples=96000, stems=True)
                return [p["a1On"], len(p["buses"]), ea.DIALOGUE_STEM in ea.stem_list(p)]

            verdict("x:x2", [plan({"solo": True}, {}), plan({}, {"mute": True}), plan({}, {})],
                    [[False, 1, False], [False, 1, False], [True, 1, True]],
                    "A2 솔로 · A1 끔 → 대사만 스템 없음(A2 는 들림) · 기본은 대사 스템 있음")
            src = inspect.getsource(E.run_export)
            body = src[src.index("if not dialogue_wav:"):]
            verdict("x:x2", ["_fit_pcm(dialogue_raw" in body[:600], "dialogue_raw=dialogue_raw" in src], [True, True],
                    "대사 스템이 없으면(이유와 무관) 같은 길이 무음 PCM 을 dialogue_raw 로 넘긴다")
        except Exception:  # noqa: BLE001
            crash("x:x2")

    if "x:x6" in wanted:
        try:
            import check_vpoc as cv  # noqa: PLC0415

            with cv._LogCapture(), cv._EdtEnv() as env, cv._pin_vnew(VNEW_EDIT=True, VNEW_SOUND=True):  # noqa: SLF001
                env.client()
                env.inline = True
                env.store.delete_key = lambda k, bucket="": env.store.blobs.pop(k, None)
                fal = {"posts": 0}
                charges: list = []

                def fake_fal(method, url, payload, *, where):
                    if method == "POST":
                        fal["posts"] += 1
                        return {"request_id": f"req-220-{fal['posts']}"}
                    return {"status": "IN_PROGRESS"}

                def fake_ff(args, what):
                    with open(args[-1], "wb") as handle:
                        handle.write(b"FF")

                env.set(sep, "_fal_call", fake_fal)
                env.set(sep, "_ffmpeg", fake_ff)
                env.set(sep, "_price", lambda m: {"unitPrice": 0.02, "unit": "seconds"})
                env.set(sep, "_charge", lambda k, **kw: charges.append(k))
                env.set(sep, "_fal_ready", lambda: True)
                pid = "prj_x6"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                body = {"scope": "edit", "variant": "webtoon", "sourceKey": c1, "confirmUsd": 0.06}
                first = sep.start(env.owner, pid, dict(body))
                young = None
                try:
                    sep.start(env.owner, pid, dict(body))
                except Exception as exc:  # noqa: BLE001
                    young = getattr(exc, "kind", type(exc).__name__)
                sep._write_state(c1, lambda s: {**s, "startedAt": "2000-01-01T00:00:00+00:00"})  # noqa: SLF001 — GET 없이 시간만 지남
                again = sep.start(env.owner, pid, dict(body))
                st = sep._read_state(c1)  # noqa: SLF001
                verdict("x:x6", [first["state"]["status"], young, again.get("resumed"), again["state"]["status"], fal["posts"],
                                 charges, st.get("requestId")],
                        ["running", "SeparateRunning", True, "running", 1, ["vnew-sep-req-220-1"], "req-220-1"],
                        "젊은 running 은 409 · 시간 넘긴 running 에 다시 누르면 같은 requestId 로 이어 봄(제출 1 · 원장 1)")
        except Exception:  # noqa: BLE001
            crash("x:x6")

    if "x:x12" in wanted:
        try:
            import check_vpoc as cv  # noqa: PLC0415
            from src.vpoc import ledger as L  # noqa: PLC0415, N812

            saved_store, saved_last = L.STORE, dict(sep._LAST_PRICE)  # noqa: SLF001
            fake = cv._FakeStore()  # noqa: SLF001 — 메모리 저장소(실제 S3 없음)
            try:
                L.STORE = fake
                sep._LAST_PRICE.clear()  # noqa: SLF001 — 이 프로세스가 단가를 한 번도 못 읽음
                usd, unknown = sep._charge_usd({"rawUsd": None}, 3.0)  # noqa: SLF001
                sep._charge("vnew-sep-x12-unknown", seconds=3.0, usd=usd, owner="u", pid="p", gen_id="g", price_unknown=unknown)  # noqa: SLF001
                sep._charge("vnew-sep-x12-known", seconds=3.0, usd=0.06, owner="u", pid="p", gen_id="g")  # noqa: SLF001
                data, _etag = fake.read_json(L.LEDGER_KEY)
            finally:
                L.STORE = saved_store
                sep._LAST_PRICE.clear()  # noqa: SLF001
                sep._LAST_PRICE.update(saved_last)  # noqa: SLF001
            entries = {e.get("key"): e for e in (data or {}).get("entries", [])}
            u, k = entries.get("vnew-sep-x12-unknown", {}), entries.get("vnew-sep-x12-known", {})
            verdict("x:x12", [u.get("priceUnknown"), float(u.get("usd") or 0) > 0, u.get("usd"), "priceUnknown" in k, k.get("usd")],
                    [True, True, round(sep.FALLBACK_USD_PER_S * 3.0, 6), False, 0.06],
                    "단가 못 읽음 → 원장 항목 priceUnknown: True · usd = 공개 단가 × 3초(0 아님) · 보통 계상은 priceUnknown 칸 없음")
        except Exception:  # noqa: BLE001
            crash("x:x12")

    if "x:x12b" in wanted:  # 작업 220b
        try:
            import check_vpoc as cv  # noqa: PLC0415
            from src.vpoc import ledger as L  # noqa: PLC0415, N812

            V, G = mods["V"], mods["G"]
            saved_store = L.STORE
            saved_v = {n: getattr(V, n) for n in ("_fal_call", "_price")}
            saved_fal = {n: getattr(G.vpoc_fal, n) for n in ("pricing_for", "pricing")}
            saved_unit = G.vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S
            fake = cv._FakeStore()  # noqa: SLF001 — 메모리 저장소(실제 S3 없음)
            posts = {"n": 0}

            def fake_fal(method, url, payload, *, where):
                if method == "POST":
                    posts["n"] += 1
                    return {"request_id": f"req-x12b-{posts['n']}"}
                raise RuntimeError("x12b: 원장 뒤 조회 전에 멈춤")

            def voice(price):
                V._price = lambda m: price  # noqa: SLF001
                item = {"lineId": "e1", "speaker": "s", "model": V.MODEL_ELEVEN, "mode": V.MODE_PRESET, "preset": "Aria",
                        "padStart": 1.0, "padEnd": 3.0, "refKey": "", "scope": "variant"}
                est = V.estimate([item])
                charged: list = []
                with contextlib.suppress(RuntimeError):
                    V.convert("u", "p", "g", "webtoon", item, b"RIFF", wait_s=1.0, where="x12b", charged=charged)
                return est["priceKnown"], f"vnew-voice-req-x12b-{posts['n']}", charged

            def retake(tag, model, path, seconds, resolution):
                usd, known = G._retake_price(model, path, seconds, resolution)  # noqa: SLF001
                info = {"path": path, "billedSeconds": seconds, "resolution": resolution, "usd": usd, "priceKnown": known}
                usd2, unknown = G._retake_charge_usd(info)  # noqa: SLF001 — resubmit_model 과 같은 두 줄
                G._charge_retake("u", "p", "g", {"clientRequestToken": f"x12b-{tag}"}, model, seconds, usd2,  # noqa: SLF001
                                 price_unknown=unknown)
                return known, round(float(usd), 4)

            try:
                L.STORE = fake
                V._fal_call = fake_fal  # noqa: SLF001
                vu = voice(None)
                vk = voice({"unitPrice": 0.01, "unit": "seconds"})
                G.vpoc_fal.pricing_for = lambda *a, **k: None
                G.vpoc_fal.pricing = lambda *a, **k: None
                rt = retake("take-u", "fal-ai/x12b-take", "take", 5.0, "480p")  # 기본 단가표(_take_usd) 추정
                rc = retake("cut-u", "fal-ai/x12b-cut", "cut", 3.0, "")  # 기본 초당 단가 추정
                G.vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S = 0.0  # 설정 기본값도 0 → 옛 코드는 0 USD
                rz = retake("cut-z", "fal-ai/x12b-cut", "cut", 3.0, "")
                G.vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S = saved_unit
                G.vpoc_fal.pricing_for = lambda *a, **k: {"unitPrice": 0.1, "unit": "seconds"}
                rk = retake("take-k", "fal-ai/x12b-take", "take", 5.0, "480p")
                data, _etag = fake.read_json(L.LEDGER_KEY)
            finally:
                L.STORE = saved_store
                for n, v in saved_v.items():
                    setattr(V, n, v)
                for n, v in saved_fal.items():
                    setattr(G.vpoc_fal, n, v)
                G.vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S = saved_unit
            entries = {e.get("key"): e for e in (data or {}).get("entries", [])}

            def row(key):
                e = entries.get(key) or {}
                return [e.get("usd"), e.get("priceUnknown", "없음")]

            verdict("x:x12b", [vu[0], row(vu[1]), vu[2], vk[0], row(vk[1]), vk[2]],
                    [False, [round(V.FALLBACK_USD_PER_S[V.MODEL_ELEVEN] * 2.0, 6), True], [round(V.FALLBACK_USD_PER_S[V.MODEL_ELEVEN] * 2.0, 6)],
                     True, [0.02, "없음"], [0.02]],
                    "목소리: 단가 모름 → 원장 공개 단가 × 2초(0 아님) · priceUnknown True / 앎 → 0.01 × 2초 그대로 · 칸 없음")
            verdict("x:x12b", [rt[0], rt[1] > 0, row("x12b-take-u"), rc[0], row("x12b-cut-u"), rz[1], row("x12b-cut-z"), rk[0],
                               row("x12b-take-k")],
                    [False, True, [rt[1], True], False, [0.42, True], 0.0, [round(G.RETAKE_FALLBACK_USD_PER_S * 3.0, 6), True], True,
                     [0.5, "없음"]],
                    "다시 만들기: 단가 모름 → 기본 단가표 추정 · priceUnknown True · 기본 단가도 0 → 공개 단가 × 3초 / 앎 → 0.1 × 5초 · 칸 없음")
            src = inspect.getsource(G.resubmit_model)
            verdict("x:x12b", [src.count("usd, unknown = _retake_charge_usd(info)"), src.count("usd, price_unknown=unknown)"),
                               'float(info["usd"] or 0))' in src],
                    [1, 1, False], "resubmit_model 은 _retake_charge_usd 금액 · price_unknown 으로 _charge_retake 를 부른다")
        except Exception:  # noqa: BLE001
            crash("x:x12b")

    if "x:x13" in wanted:
        from src.vnew import retake  # noqa: PLC0415

        names = ("_retake_state", "_submitted_version", "_mutate_redo", "read_redo", "_spawn")
        saved = {n: getattr(LR, n) for n in names}
        saved_find = retake._find_version  # noqa: SLF001
        try:
            old_at = "2000-01-01T00:00:00Z"  # now_iso 꼴
            doc = {"v": 1, "variant": "webtoon", "current": 0, "revisions": [], "jobs": {
                "old": {"status": "running", "n": None, "cellKey": "c", "draftId": "d1", "startedAt": old_at},
                "young": {"status": "running", "n": None, "cellKey": "c", "draftId": "d2", "startedAt": LR.now_iso()},
                "found": {"status": "running", "n": 3, "cellKey": "c", "draftId": "d3", "startedAt": old_at},
                "legacy": {"status": "running", "n": 2, "cellKey": "c", "draftId": "d4"}}}
            LR._retake_state = lambda *a: {"cells": {}}  # noqa: SLF001
            retake._find_version = lambda state, cell, n: {"status": "running"} if n == 3 else None  # noqa: SLF001
            LR._submitted_version = lambda *a, **k: None  # noqa: SLF001
            LR._mutate_redo = lambda o, p, g, v, fn: fn(doc) or doc  # noqa: SLF001
            LR.read_redo = lambda *a: doc
            LR._spawn = lambda *a, **k: None  # noqa: SLF001
            LR._refresh("u", "p", "g", "webtoon", copy.deepcopy(doc))  # noqa: SLF001
            jobs = doc["jobs"]
            verdict("x:x13", [jobs["old"]["status"], jobs["old"].get("error"), jobs["young"]["status"], jobs["found"]["status"],
                              jobs["legacy"]["status"]],
                    ["failed", LR.ORPHAN_TEXT, "running", "running", "running"],
                    "버전 없는 running: ORPHAN_S 지나면 failed · 젊은 것 · 버전을 찾은 것 · startedAt 없는 옛 기록은 그대로")
        except Exception:  # noqa: BLE001
            crash("x:x13")
        finally:
            for n, v in saved.items():
                setattr(LR, n, v)
            retake._find_version = saved_find  # noqa: SLF001


def _x_node(vjs: str, node: str, wanted: set[str], groups: dict, details: dict) -> None:
    import check_review_fixes_219b as B  # noqa: PLC0415
    import check_vpoc as CV  # noqa: PLC0415

    code, problems = CV._edu_code(vjs, CV._EDU_SLICES)  # noqa: SLF001
    proc = subprocess.run([node, "-"], input=CV._EDU_NODE_HARNESS + code + B._NODE + _EXTRA_JS, capture_output=True,  # noqa: SLF001
                          text=True, encoding="utf-8", timeout=180)
    if problems or proc.returncode != 0:
        for name in wanted:
            groups[name] = "fail"
            details.setdefault(name, []).append(f"node 실행 실패: {problems} {(proc.stderr or proc.stdout)[-800:]}")
        return
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    for name, key in (("x:x5", "x5"), ("x:x7", "x7")):
        if name not in wanted:
            continue
        bad = [f"{case}: 결과 {g} 기대 {w}" for case, g, w in got[key] if g != w]
        if got["err"]:
            bad.append("예외: " + got["err"])
        if len(got[key]) != 30:
            bad.append(f"경우 {len(got[key])}개(기대 30)")
        if name == "x:x5" and got["sel"] != [[None, True], [1, 0, "c1,c2,c3"], [True, None], [0, 1, "c1,c2,c3"]]:
            bad.append(f"고른 것은 하나(방금 고른 것을 지움): 결과 {got['sel']}")
        if name == "x:x7" and got["split"] != [[2, "c1,c2,c3", 1, 0], [2, "c1,c2,c3", 1, 0]]:
            bad.append(f"V1 잠김 자막 · 타이틀 S: 결과 {got['split']} 기대 둘로 나뉨 · 클립 · 소리 그대로 · 안내 없음")
        groups[name] = "fail" if bad else "ok"
        if bad:
            details.setdefault(name, []).extend(bad[:4])


def _child(name: str, no_node: bool) -> dict:
    groups: dict[str, str] = {}
    details: dict[str, list[str]] = {}
    try:
        mods = _modules()
        values = _attr_values(mods)
        if name == "base":
            wanted = {g for _n, _t, gs, _m in ITEMS for g in gs}
            steps: list[tuple] = []
        else:
            _desc, wanted_list, steps = MUTANTS[name]
            wanted = set(wanted_list)
        vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        js_steps = [s for s in steps if s[0] in ("js", "jsline")]
        for step in steps:
            if step[0] == "py":
                _py_patch(mods[step[1]], step[2], step[3], step[4])
            elif step[0] == "attr":
                setattr(mods[step[1]], step[2], values[step[3]])
        if js_steps:
            vjs = _js_patch(vjs, js_steps)
        node = "" if no_node else (shutil.which("node") or "")
        with tempfile.TemporaryDirectory(prefix="chk220-") as tmp:
            public = PUBLIC
            if js_steps:
                public = Path(tmp)
                (public / "vnew.js").write_text(vjs, encoding="utf-8", newline="\n")
                shutil.copyfile(PUBLIC / "vnew.css", public / "vnew.css")
            if any(g.startswith("219:") for g in wanted):
                groups.update(_run_219())
            if any(g.startswith("219b:") for g in wanted):
                groups.update(_run_219b(public, no_node))
            _x_python(mods, wanted, groups, details)
            node_x = {g for g in wanted if g in ("x:x5", "x:x7")}
            if node_x:
                if node:
                    _x_node(vjs, node, node_x, groups, details)
                else:
                    groups.update({g: "warn" for g in node_x})
        return {"groups": groups, "details": details, "error": ""}
    except Exception:  # noqa: BLE001
        return {"groups": groups, "details": details, "error": traceback.format_exc()[-1500:]}


# ================================================================ 본 검사


def _spawn(name: str, no_node: bool) -> dict:
    argv = [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--child", name] + (["--no-node"] if no_node else [])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=600, env=env, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return {"groups": {}, "details": {}, "error": "하위 검사 시간 초과(600초)"}
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(TAG)), "")
    if not line:
        return {"groups": {}, "details": {}, "error": f"하위 검사 결과 없음(코드 {proc.returncode}): {(proc.stderr or proc.stdout)[-1200:]}"}
    return json.loads(line[len(TAG):])


def check_review_items_220(argv=None) -> bool:  # noqa: C901 — 항목마다 한 곳에서 판정
    argv = list(sys.argv[1:] if argv is None else argv)
    no_node = "--no-node" in argv or os.environ.get("CHECK_220_NO_NODE") == "1" or not shutil.which("node")
    names = ["base"] + list(MUTANTS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2))) as pool:
        results = dict(zip(names, pool.map(lambda n: _spawn(n, no_node), names)))
    base = results["base"]
    passed = True
    if base["error"]:
        print("[FAIL] 작업 220 지금 코드 검사가 돌지 않았습니다\n    " + base["error"][-1200:].replace("\n", "\n    "))
        return False
    for number, title, groups, mutants in ITEMS:
        problems: list[str] = []
        warn = False
        for group in groups:
            status = base["groups"].get(group, "missing")
            if status == "warn" or (no_node and group in NODE_GROUPS):
                warn = True
            elif status != "ok":
                problems.append(f"지금 코드에서 {group} = {status}: " + " / ".join(base["details"].get(group, []))[:900])
        caught: list[str] = []
        for name in mutants:
            desc, expect_fail, _steps = MUTANTS[name]
            res = results[name]
            if res["error"]:
                problems.append(f"변이 {name}({desc})를 돌리지 못했습니다: {res['error'][-600:]}")
                continue
            live = [g for g in expect_fail if not (no_node and g in NODE_GROUPS)]
            if not live:
                warn = True
                continue
            hits = [g for g in live if res["groups"].get(g) == "fail"]
            if hits:
                caught.append(f"{name}→{','.join(hits)}")
            else:
                problems.append(f"옛 동작 변이 {name}({desc})를 검사가 잡지 못했습니다: "
                                + ", ".join(f"{g}={res['groups'].get(g, 'missing')}" for g in live))
        if problems:
            passed = False
            print(f"[FAIL] 작업 220 항목 {number} {title}")
            for line in problems[:4]:
                print("    " + line)
        elif warn:
            print(f"[WARN] 작업 220 항목 {number} {title} — node 없음, 화면 검사 · 변이 일부를 건너뜀(옛 동작 잡음: {' '.join(caught) or '-'})")
        else:
            print(f"[ok  ] 작업 220 항목 {number} {title} — {' · '.join(groups)} 통과 · 옛 동작 {len(caught)}개 모두 잡음({' '.join(caught)})")
    return passed


if __name__ == "__main__":
    if "--child" in sys.argv:
        _name = sys.argv[sys.argv.index("--child") + 1]
        _out = _child(_name, "--no-node" in sys.argv)
        print(TAG + json.dumps(_out, ensure_ascii=False))
        sys.exit(0)
    _ok = check_review_items_220()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
