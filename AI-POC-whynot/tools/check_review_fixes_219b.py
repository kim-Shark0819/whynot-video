"""작업 219b: ⑦ 코드 리뷰 고침 검사(키 · 지우기 · 잠금 · 숨긴 프리미어 컨트롤). 가짜 저장소, 유료·네트워크 0.

  (rf-a) 붙기(node): N 은 215c 롤링 도구 · Shift+N = 붙기 · 215a 🧲 · 215c 🧲 · Shift+N 이 같은 상태 하나 · 안내 글 Shift+N
  (rf-b) 지우기(node): Premiere Delete(들어내기) · Shift+Delete(지우고 당기기)가 고른 자막 → 고른 소리 아이템을 먼저 지운다
  (rf-c) V1 잠금(node): 고른 소리 아이템 지우기 · 소리 끄기는 지나간다 · S 는 소리 아이템만 나눈다 · 클립 동작은 막는다
  (rf-d) Ctrl+R(node): 클립 하나를 골랐을 때만 속도 창(그 밖에는 브라우저 새로 고침 그대로)
  (rf-e) 잠금 검사는 동작 길마다 한 번(editDo · edit215cDo 본문 각 1, 서로 부르지 않음)
  (rf-f) 프리미어 컨트롤(node + 글): EDIT_PREMIERE_UI = false 하나 · 끄면 215b 묶음 칸 · 215c Pr 버튼 없음 · /premiere 호출 0 ·
         켜면 둘 다 있음 · CSS 숨김 규칙 없음
  (rf-g) 서버 save_doc(읽기만): PUT 이 markers · tracks · audioTracks 를 바꿔 쓴다(지우면 저장본에서도 빠진다)
  (rf-h) node --check · LF

실행: python -X utf8 -B tools/check_review_fixes_219b.py   (node 를 숨기려면 --no-node 또는 CHECK_219B_NO_NODE=1)
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "rf-a": "붙기 Shift+N · 상태 하나(node)",
    "rf-b": "Delete · Shift+Delete 가 고른 자막 · 소리 먼저(node)",
    "rf-c": "V1 잠금은 V1 클립만(node)",
    "rf-d": "Ctrl+R 은 클립 하나일 때만(node)",
    "rf-e": "잠금 검사 동작 길마다 한 번",
    "rf-f": "프리미어 컨트롤 만들지 않음(EDIT_PREMIERE_UI)",
    "rf-g": "서버 save_doc 바꿔 쓰기(읽기만)",
    "rf-h": "node --check · LF",
}
NODE_GROUPS = ("rf-a", "rf-b", "rf-c", "rf-d")
HOOK = ('    if not __import__("check_review_fixes_219b").check_review_fixes_219b(): '
        'fail("작업 219b: ⑦ 리뷰 고침(키 · 지우기 · 잠금 · 프리미어 컨트롤) 검사 실패")')
CHANGED = [PUBLIC / "vnew.js", PUBLIC / "vnew.css", Path(__file__).resolve(), TOOLS / "check_edit_text_215b.py",
           TOOLS / "check_edit_tools_215c.py"]

_NODE = r"""
var R = {};
var notes = [];
// 무거운 그리기 · 재생은 가볍게(같은 이름 선언이 뒤에 오면 이긴다). 단축키 · editDo · 215a/215b/215c 동작은 진짜.
function editChanged(ctx) { ctx.rev = (ctx.rev || 0) + 1; }
function editSeek(ctx, t) { ctx.t = Math.max(0, Math.min(editStarts(ctx.clips).total, Number(t) || 0)); }
function editPause(ctx) { ctx.playing = false; edit215cShuttleStop(ctx); }
function editPlay(ctx) { ctx.playing = true; }
function editNote(ctx, m) { if (m) notes.push(m); }
function editPlaceFx(ctx) {}
function editPaintClip(ctx, parts, clip) {}
function editAlive(ctx) { return true; }
function editSelect(ctx, i) { ctx.sel = i; ctx.multi = i >= 0 ? [i] : []; }
var speedCalls = [];
function edit215cSpeedDialog(ctx, i) { speedCalls.push(i); }
function C(id, a, b) { return { id: id, sourceKey: "k1", in: a, out: b, muted: false }; }
var asset = { assetId: "a1111111111111111", duration: 5, name: "bgm" };
function fresh() {
  var ctx = editNewCtx();
  var labels = new FakeNode("div");
  [0, 1, 2].forEach(function () { labels.appendChild(new FakeNode("div")); });
  var tl = new FakeNode("div");
  tl.appendChild(labels);
  Object.assign(ctx, { clips: [C("c1", 0, 3), C("c2", 0, 2), C("c3", 1, 4)], pps: 100, t: 1, sel: -1, multi: [], rev: 0, busy: false,
    hist: { undo: [], redo: [] }, variant: "webtoon", materials: { k1: { duration: 10, fps: 24 } },
    dom: { content: new FakeNode("div"), scroller: new FakeNode("div"), tl: tl, vtrack: new FakeNode("div"), mixer: new FakeNode("div") },
    clipEls: [], limits: {}, vids: [], playing: false, root: new FakeNode("div"),
    mix: editMixAddItem(editMixAddTrack(editMixNorm({})), 0, asset, 0, 300), mixSel: null, mixKey: null,
    tx: { captions: [{ id: "s1", start: 0.5, end: 1.5, text: "안녕", style: {} }], titles: [], fadeIn: 0, fadeOut: 0, burnIn: false },
    txSel: null });
  ctx.e215 = null;
  return ctx;
}
function press(ctx, k, code, extra) {
  var ev = { key: k, code: code || "", target: document.body, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };
  Object.keys(extra || {}).forEach(function (n) { ev[n] = extra[n]; });
  ev.preventDefault = function () { ev.defaultPrevented = true; };
  editOnKey(ctx, ev);
  return !!ev.defaultPrevented;
}
function ids(ctx) { return ctx.clips.map(function (c) { return c.id; }); }
function items(ctx) { return ctx.mix.tracks[0].items.length; }
function btn(root, text) { return walk(root).filter(function (n) { return n.tagName === "BUTTON" && n.textContent === text; })[0] || null; }
var out = { a: {}, b: {}, c: {}, d: {}, f: {} };
// ---- (rf-a) 붙기
var ctx = fresh();
editMixRenderMixer(ctx);
edit215cBuild(ctx);
var s = edit215cState(ctx);
var mixBtn = btn(ctx.dom.mixer, "🧲 붙기");
out.a.start = [s.snap, editMixSnapTol(ctx) > 0, mixBtn.title, mixBtn.getAttribute("aria-pressed"), s.snapBtn.title,
  s.snapBtn.getAttribute("aria-pressed")];
out.a.n = [press(ctx, "n", "KeyN"), s.tool, s.snap];
out.a.shiftN = [press(ctx, "N", "KeyN", { shiftKey: true }), s.snap, editMixSnapTol(ctx), storage["vnew.edit.snap215c"],
  s.snapBtn.getAttribute("aria-pressed"), btn(ctx.dom.mixer, "🧲 붙기").getAttribute("aria-pressed"), notes[notes.length - 1]];
btn(ctx.dom.mixer, "🧲 붙기").fire("click", {});
out.a.mixClick = [s.snap, editMixSnapTol(ctx) > 0, s.snapBtn.getAttribute("aria-pressed")];
s.snapBtn.fire("click", {});
out.a.barClick = [s.snap, editMixSnapTol(ctx), btn(ctx.dom.mixer, "🧲 붙기").getAttribute("aria-pressed"), storage["vnew.edit.snap215c"]];
s.snapBtn.fire("click", {});
out.a.keymap = EDIT215C_KEYMAP.some(function (r) { return r[1] === "Shift+N"; });
// ---- (rf-b) Premiere Delete · Shift+Delete
ctx = fresh(); s = edit215cState(ctx); s.preset = "premiere";
editSelect(ctx, 0); ctx.txSel = { kind: "caption", id: "s1" };
out.b.liftCaption = [press(ctx, "Delete", "Delete"), ctx.tx.captions.length, ids(ctx), (ctx.clips[1] || {}).gapBefore || 0, ctx.txSel];
editSelect(ctx, 0); ctx.mixSel = { t: 0, i: 0 };
out.b.rippleMix = [press(ctx, "Delete", "Delete", { shiftKey: true }), items(ctx), ids(ctx)];
editSelect(ctx, 0);
out.b.liftClip = [press(ctx, "Delete", "Delete"), ids(ctx), (ctx.clips[0] || {}).gapBefore || 0];
ctx = fresh(); s = edit215cState(ctx);
editSelect(ctx, 1); ctx.mixSel = { t: 0, i: 0 };
out.b.altDeleteMix = [press(ctx, "Delete", "Delete", { altKey: true }), items(ctx), ids(ctx)];
ctx.txSel = { kind: "caption", id: "s1" };
out.b.vnewShiftCaption = [press(ctx, "Delete", "Delete", { shiftKey: true }), ctx.tx.captions.length, ids(ctx)];
// 오른쪽 클릭 메뉴(클립을 겨눔)는 고른 자막이 있어도 클립을 지운다
ctx = fresh(); editSelect(ctx, 0); ctx.txSel = { kind: "caption", id: "s1" };
edit215cDo(ctx, "rippleDelete");
out.b.menu = [ids(ctx), ctx.tx.captions.length];
// ---- (rf-c) V1 잠금
ctx = fresh(); s = edit215cState(ctx); s.tracks = { v1: { locked: true } };
editSelect(ctx, 0); ctx.mixSel = { t: 0, i: 0 };
notes.length = 0;
out.c.mute = [press(ctx, "m", "KeyM"), ctx.mix.tracks[0].mute === true, ids(ctx), ctx.clips[0].muted];
ctx.t = 2;
out.c.split = [press(ctx, "s", "KeyS"), items(ctx), ids(ctx)];
ctx.mixSel = { t: 0, i: 0 };
out.c.del = [press(ctx, "Delete", "Delete"), items(ctx), ids(ctx), notes.slice()];
ctx.mixSel = null; editSelect(ctx, 0);
out.c.blocked = [press(ctx, "Delete", "Delete"), ids(ctx), notes[notes.length - 1] === EDIT215C_LOCKED_NOTE, edit215cBlocked(ctx, "split"),
  edit215cBlocked(ctx, "mute"), edit215cBlocked(ctx, "play")];
ctx.t = 4.5; ctx.mix = editMixNorm({}); notes.length = 0;
editDo(ctx, "split");
out.c.splitNoAudio = [ids(ctx), notes[0] === EDIT215C_LOCKED_NOTE];
s.tracks = {}; ctx.t = 1;
editDo(ctx, "split");
out.c.unlocked = ctx.clips.length;
// ---- (rf-d) Ctrl+R
ctx = fresh();
out.d.none = [press(ctx, "r", "KeyR", { ctrlKey: true }), speedCalls.length];
editSelect(ctx, 1);
out.d.one = [press(ctx, "r", "KeyR", { ctrlKey: true }), speedCalls.slice()];
ctx.multi = [0, 1];
out.d.two = [press(ctx, "r", "KeyR", { ctrlKey: true }), speedCalls.length];
ctx.multi = [2]; ctx.sel = 2;
out.d.meta = [press(ctx, "r", "KeyR", { metaKey: true }), speedCalls.slice()];
// ---- (rf-f) 프리미어 컨트롤
calls.length = 0;
ctx = fresh(); edit215cBuild(ctx);
var box = new FakeNode("div");
editTxExportTools(ctx, box);
out.f.off = [EDIT_PREMIERE_UI, !!btn(ctx.dom.tl, "Pr"), byClass(box, "vnew-edit-txprem").length, ctx.txPrem === undefined || ctx.txPrem === null,
  !!ctx.dom.txPrem, byClass(box, "vnew-edit-txexport").length];
EDIT_PREMIERE_UI = true;
ctx = fresh(); edit215cBuild(ctx);
box = new FakeNode("div");
editTxExportTools(ctx, box);
var pr = btn(ctx.dom.tl, "Pr");
out.f.on = [!!pr, pr ? pr.tabIndex === -1 : null, byClass(box, "vnew-edit-txprem").length, byClass(box, "vnew-edit-txprem-btn").length];
EDIT_PREMIERE_UI = false;
out.f.calls = calls.filter(function (c) { return String(c[1]).indexOf("/premiere") >= 0; }).length;
R = out;
console.log(JSON.stringify(R));
"""


def _node_bin(argv) -> str:
    if "--no-node" in argv or os.environ.get("CHECK_219B_NO_NODE") == "1":
        return ""
    return shutil.which("node") or ""


def _body(source: str, name: str) -> str:
    """vnew.js 에서 function name(...) { ... } 본문(중괄호 짝)."""
    m = re.search(r"\n  function " + re.escape(name) + r"\(", source)
    if not m:
        return ""
    i, depth = source.index("{", m.end()), 0
    for j in range(i, len(source)):
        depth += {"{": 1, "}": -1}.get(source[j], 0)
        if depth == 0:
            return source[i:j + 1]
    return ""


def check_review_fixes_219b(argv=None) -> bool:  # noqa: C901, PLR0915 — 묶음마다 한 곳에서 본다
    argv = list(sys.argv[1:] if argv is None else argv)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:500]}\n      기대 {str(want)[:500]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    css = (PUBLIC / "vnew.css").read_text(encoding="utf-8")

    # ================= (rf-e) 잠금 검사 한 번 · (rf-f) 글 =================
    try:
        do, do215 = _body(vjs, "editDo"), _body(vjs, "edit215cDo")
        onkey215, other = _body(vjs, "edit215cOnKey"), _body(vjs, "edit215cDeleteOther")
        expect("rf-e", [do.count("edit215cBlocked("), do215.count("edit215cBlocked("), "editDo(" in do215, "edit215cDo(" in do,
                        "editDo(" in other or "edit215cDo(" in other, onkey215.count("edit215cBlocked(")],
               [1, 1, False, False, False, 0], "editDo · edit215cDo 본문에 잠금 검사 하나씩 · 서로 · 지우기 돌림이 다시 부르지 않음")
        expect("rf-e", [vjs.count("mixSnap"), "(N)" in vjs, vjs.count("prBtn.tabIndex")], [0, False, 0],
               "따로 놀던 ctx.mixSnap 없음 · '(N)' 안내 없음 · tabIndex 숨김 없음")
    except Exception as exc:  # noqa: BLE001
        crash("rf-e", exc)
    try:
        expect("rf-f", [len(re.findall(r"\n  var EDIT_PREMIERE_UI = false;", vjs)), vjs.count("var EDIT_PREMIERE_UI"),
                        vjs.count("if (EDIT_PREMIERE_UI)"), 'title^="Premiere Pro"' in css,
                        "clip-path: inset(50%) !important" in css],
               [1, 1, 2, False, False], "상수 하나(false) · 215b · 215c 두 곳을 막음 · CSS 숨김 규칙 없앰")
    except Exception as exc:  # noqa: BLE001
        crash("rf-f", exc)

    # ================= (rf-g) 서버 save_doc(읽기만 — 가짜 저장소) =================
    try:
        import check_edit_text_215b as B  # noqa: PLC0415 — 가짜 저장소 · 클립 재사용
        from src.vnew import edit as E  # noqa: PLC0415
        from src.vnew import edit_audio  # noqa: PLC0415

        names = ("STORE", "_load_gen", "_all_items", "_retake_state", "_ensure_meta", "_clean_mix")
        saved = {n: getattr(E, n) for n in names}
        try:
            store = B._Store()  # noqa: SLF001
            E.STORE = store
            E._load_gen = lambda owner, pid: ({}, {}, "g1")  # noqa: SLF001
            E._all_items = lambda gen, variant, retakes: [{"sourceKey": B.K1}, {"sourceKey": B.K2}]  # noqa: SLF001
            E._retake_state = lambda *a: None  # noqa: SLF001
            E._ensure_meta = lambda owner, pid, gen_id, keys, budget_s=None: ({k: B.META[k] for k in keys if k in B.META}, True)  # noqa: SLF001
            # 오디오 아이템 검사(내 소리 자산)는 edit_audio 몫 — 여기서는 save_doc 이 받은 트랙을 어떻게 쓰는지만 본다
            E._clean_mix = lambda owner, pid, payload, where: (  # noqa: SLF001
                copy.deepcopy(payload.get("audioTracks") or []), edit_audio.default_a1())
            key = E.doc_key(B.OWNER, B.PID, "g1", "webtoon")
            first = {"version": 0, "variant": "webtoon", "clips": copy.deepcopy(B.OLD_CLIPS),
                     "markers": [{"id": "k1", "t": 1.0, "name": "m", "color": "green"}], "tracks": {"v1": {"locked": True}},
                     "audioTracks": [{"id": "a2", "items": []}]}
            res = E.save_doc(B.OWNER, B.PID, first)
            had = sorted(k for k in store.json[key][0] if k in ("markers", "tracks", "audioTracks"))
            res = E.save_doc(B.OWNER, B.PID, {"etag": res["etag"], "variant": "webtoon", "clips": copy.deepcopy(B.OLD_CLIPS)})
            gone = sorted(k for k in store.json[key][0] if k in ("markers", "tracks", "audioTracks"))
            E.save_doc(B.OWNER, B.PID, dict(first, version=None, etag=res["etag"]))
            stored = store.json[key][0]
            res2 = E.save_doc(B.OWNER, B.PID, {"etag": store.json[key][1], "variant": "webtoon", "clips": copy.deepcopy(B.OLD_CLIPS),
                                               "markers": [], "tracks": {"v1": {"locked": False}}, "audioTracks": []})
            empty = sorted(k for k in store.json[key][0] if k in ("markers", "tracks", "audioTracks"))
            expect("rf-g", [had, gone, sorted(k for k in stored if k in ("markers", "tracks", "audioTracks")), empty, res2["saved"]],
                   [["audioTracks", "markers", "tracks"], [], ["audioTracks", "markers", "tracks"], [], True],
                   "PUT 은 문서를 새로 만든다: 빠진 · 빈 markers · tracks(꺼진 깃발) · audioTracks 는 저장본에서도 빠진다(합치지 않음)")
        finally:
            for n, v in saved.items():
                setattr(E, n, v)
        edit_src = (ROOT / "app" / "src" / "vnew" / "edit.py").read_text(encoding="utf-8")
        start = edit_src.index("\ndef save_doc(")
        src = edit_src[start:edit_src.index("\ndef ", start + 1)]
        expect("rf-g", ['doc = {"version": base_version + 1' in src, "current.get(" in src, "current[" in src], [True, False, False],
               "save_doc 은 지난 저장본(current)에서 값을 가져오지 않는다(기준 etag · version 만)")
    except Exception as exc:  # noqa: BLE001
        crash("rf-g", exc)

    # ================= (rf-a · rf-b · rf-c · rf-d · rf-f) node =================
    node = _node_bin(argv)
    if not node:
        seen.update(NODE_GROUPS)
        seen.add("rf-h")
        warns.append("node 가 없어 rf-a · rf-b · rf-c · rf-d 화면 실행 · rf-f 화면 · rf-h node --check 를 건너뜁니다")
    else:
        try:
            checked = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True, encoding="utf-8",
                                     timeout=120)
            expect("rf-h", [checked.returncode, (checked.stderr or "")[-300:]], [0, ""], "node --check vnew.js")
            import check_vpoc as CV  # noqa: PLC0415 — 읽기만(화면 하네스 · ⑦ 구역 자르기)

            code, problems = CV._edu_code(vjs, CV._EDU_SLICES)  # noqa: SLF001
            expect("rf-a", problems, [], "⑦ 구역 자르기")
            proc = subprocess.run([node, "-"], input=CV._EDU_NODE_HARNESS + code + _NODE, capture_output=True, text=True,  # noqa: SLF001
                                  encoding="utf-8", timeout=120)
            if proc.returncode != 0:
                bad["rf-a"].append("node 실행 실패: " + (proc.stderr or proc.stdout)[-1500:])
                seen.update(NODE_GROUPS)
            else:
                got = json.loads(proc.stdout.strip().splitlines()[-1])
                a, b, c, d, f = got["a"], got["b"], got["c"], got["d"], got["f"]
                expect("rf-a", a["start"], [True, True, "클립 경계 · 재생헤드 · 다른 소리에 붙기 (Shift+N)", "true",
                                            "스냅 켜기/끄기 — 끌 때 편집점·재생헤드·마커·표시 · 소리 아이템에 붙습니다 (Shift+N)", "true"],
                       "처음: 붙기 켬 · 두 🧲 툴팁 Shift+N · 둘 다 눌림")
                expect("rf-a", a["n"], [True, "roll", True], "그냥 N = 롤링 도구(붙기는 그대로)")
                expect("rf-a", a["shiftN"], [True, False, 0, "0", "false", "false", "붙기 끔 (Shift+N)"],
                       "Shift+N = 붙기 끔: 215c 상태 · 215a 허용 폭 0 · 저장 · 두 🧲 모두 꺼짐 · 안내 Shift+N")
                expect("rf-a", [a["mixClick"], a["barClick"], a["keymap"]], [[True, True, "true"], [False, 0, "false", "0"], True],
                       "215a 🧲 를 누르면 215c 🧲 도 · 215c 🧲 를 누르면 215a 붙기도 · 단축키 표에 Shift+N")
                expect("rf-b", [b["liftCaption"], b["rippleMix"], b["liftClip"]],
                       [[True, 0, ["c1", "c2", "c3"], 0, None], [True, 0, ["c1", "c2", "c3"]], [True, ["c2", "c3"], 3]],
                       "Premiere: Delete = 고른 자막 지움(클립 그대로) · Shift+Delete = 고른 소리 아이템 지움 · 고른 것이 클립뿐이면 들어내기")
                expect("rf-b", [b["altDeleteMix"], b["vnewShiftCaption"], b["menu"]],
                       [[True, 0, ["c1", "c2", "c3"]], [True, 0, ["c1", "c2", "c3"]], [["c2", "c3"], 1]],
                       "⑦ 기본: Alt+Delete · Shift+Delete 도 고른 소리 · 자막 먼저 · 오른쪽 클릭 메뉴는 클립을 지운다")
                expect("rf-c", [c["mute"], c["split"], c["del"]],
                       [[True, True, ["c1", "c2", "c3"], False], [True, 2, ["c1", "c2", "c3"]], [True, 1, ["c1", "c2", "c3"], []]],
                       "V1 잠김: 고른 소리 M = 그 트랙 mute · S = 소리 아이템만 나눔 · Delete = 소리 아이템 지움(안내 없음)")
                expect("rf-c", [c["blocked"], c["splitNoAudio"], c["unlocked"]],
                       [[True, ["c1", "c2", "c3"], True, True, True, False], [["c1", "c2", "c3"], True], 4],
                       "V1 잠김 + 클립만 고름: Delete 막고 안내 · 걸친 소리가 없으면 S 도 안내 · 풀면 클립 자르기")
                expect("rf-d", [d["none"], d["one"], d["two"], d["meta"]], [[False, 0], [True, [1]], [False, 1], [True, [1, 2]]],
                       "Ctrl+R: 고른 클립 없음 · 둘이면 가로채지 않음(새로 고침) · 하나면 속도 창(Cmd+R 도)")
                expect("rf-f", [f["off"], f["on"], f["calls"]], [[False, False, 0, True, False, 1], [True, False, 1, 1], 0],
                       "끔: Pr 버튼 · 프리미어 칸 · ctx.txPrem 없음(SRT 칸은 그대로) · 켬: 둘 다 있음(tabIndex 숨김 없음) · /premiere 호출 0")
        except Exception as exc:  # noqa: BLE001
            crash("rf-a", exc)

    # ================= (rf-h) LF =================
    try:
        expect("rf-h", [p.name for p in CHANGED if b"\r" in p.read_bytes()], [], "LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("rf-h", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 219b ({group}) {item}"[:1800])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 219b ({group}) {title}: 검사하지 못했습니다")
        elif not node and group in NODE_GROUPS:
            print(f"[WARN] 작업 219b ({group}) {title} — node 없음, 돌지 않음")
        else:
            print(f"[ok  ] 작업 219b ({group}) {title}")
    for text in warns:
        print(f"[WARN] 작업 219b {text}")
    return passed


if __name__ == "__main__":
    ok = check_review_fixes_219b()
    print("check_vpoc.py 연결 줄(이 작업은 고치지 않음):")
    print(HOOK)
    sys.exit(0 if ok else 1)
