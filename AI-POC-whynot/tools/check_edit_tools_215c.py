"""작업 215c 검사: ⑦ 편집 프리미어식 도구·단축키. 가짜 저장소·가짜 ffmpeg, 유료·네트워크 0(로컬 ffmpeg 가 있으면 실측도).

  (et-a) 서버 검증: 새 클립 값(빈틈·사용 안 함·역재생·음높이·게인·라벨 색·연결 해제) 저장 꼴 · 틀린 값 400 · 역재생 15초 넘음 막힘(한국어) ·
         빈틈 앞 디졸브 규칙 · 전체 길이에 빈틈 · 마커·트랙 정리 · 숨긴 트랙 → 내보내기 사본
  (et-b) 내보내기 계획: 빈틈 = 검은 클립 · 사용 안 함 = 검은 클립(자리 그대로) · 역재생 in/out 바뀜 · 프레임 합 = 문서 길이 ·
         ffmpeg 명령(reverse 조각 · areverse · volume · asetrate · lavfi 검정) · 파생에만 쓴 원본 지움
  (et-c) 실측(로컬 ffmpeg): 역재생 파생 원본 길이 · 소리 · edit_segment 로 자르기
  (et-d) 화면 순수 함수(node): 잔물결·롤링·밀기·밀어넣기 수학(전체 길이 · 이웃 in/out · 겹침 없음) · 들어내기 vs 뽑아내기 ·
         I/O 표시 구간 · 속도/역재생 검증 · 단축키 표(두 묶음, ⑦ 기존 키는 그대로 넘김)
  (et-e) 화면 동작(node): 도구 바꾸기 · 끌기(밀어넣기·밀기·롤링) · 자르기 도구 누르기 · 마커 실행 취소 · 트랙 잠금 · J/K/L
  (et-f) 옛 문서 그대로: 저장 꼴 · 해시(215c 전 계산과 같다) · 내보내기 계획 없음 · 저장 바디 · LF · 연결 줄·블록 표시

실행: python -X utf8 -B tools/check_edit_tools_215c.py   (node 가 없으면 et-d·et-e 는 경고만, ffmpeg 가 없으면 et-c 경고만)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "et-a": "서버 검증 · 마커 · 트랙",
    "et-b": "내보내기 계획 · ffmpeg 명령",
    "et-c": "실측 역재생(로컬 ffmpeg)",
    "et-d": "화면 순수 함수 · 단축키 표(node)",
    "et-e": "화면 동작 · 끌기 · 실행 취소(node)",
    "et-f": "옛 문서 그대로 · LF · 연결 줄",
}
OWNER, PID = "tester", "prj_et"
BASE = f"users/{OWNER}/video/{PID}/vnew/gen/g1/"
K1, K2 = BASE + "a.mp4", BASE + "b.mp4"
ALLOWED = {K1, K2}
DURS = {K1: 20.0, K2: 10.0}
META = {K1: {"duration": 20.0, "fps": 24, "width": 1280, "height": 720, "hasAudio": True},
        K2: {"duration": 10.0, "fps": 24, "width": 1280, "height": 720, "hasAudio": True}}
OLD = [
    {"id": "c1", "sourceKey": K1, "in": 0.0, "out": 3.0, "muted": False, "transition": {"type": "dissolve", "seconds": 0.5}},
    {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0, "muted": False, "speed": 0.5, "audioTail": 0.5},
    {"id": "c3", "sourceKey": K1, "in": 4.0, "out": 6.0, "muted": True, "label": "컷 3"},
]
NEW = [
    {"id": "c1", "sourceKey": K1, "in": 0.0, "out": 3.0, "muted": False, "labelColor": "blue", "gain": 3},
    {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0, "muted": False, "gapBefore": 1.5, "reverse": True, "keepPitch": False,
     "speed": 2},
    {"id": "c3", "sourceKey": K1, "in": 4.0, "out": 6.0, "muted": False, "disabled": True, "unlinked": True},
]
EXPECTED_BLOCK = ("/* ==== 작업 215c 시작 ==== */", "/* ==== 작업 215c 끝 ==== */")


def _old_hash(variant: str, clips: list[dict]) -> str:
    """215c 전 _doc_hash 와 같은 계산(검사용 사본)."""
    body = []
    for c in clips:
        item = {"sourceKey": c["sourceKey"], "in": c["in"], "out": c["out"], "muted": bool(c.get("muted"))}
        if c.get("speed", 1) != 1:
            item["speed"] = c["speed"]
        for key in ("audioLead", "audioTail", "audioSlip"):
            if c.get(key):
                item[key] = c[key]
        if c.get("transition"):
            item["transition"] = c["transition"]
        body.append(item)
    text = json.dumps({"variant": variant, "clips": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_NODE = r"""
var R = {};
function C(id, key, a, b, extra) { var c = { id: id, sourceKey: key, in: a, out: b, muted: false }; Object.keys(extra || {}).forEach(function (k) { c[k] = extra[k]; }); return c; }
function lens(list) { return list.map(function (c) { return editRound3(editClipLen(c)); }); }
function tot(list) { return editRound3(editStarts(list).total); }
function noOverlap(list) {
  var info = editStarts(list);
  for (var i = 1; i < list.length; i++) {
    if (info.starts[i] < info.starts[i - 1] + editClipLen(list[i - 1]) - editDissolveOf(list[i - 1]) - 1e-6) return false;
  }
  return true;
}
function io(list) { return list.map(function (c) { return [c.id, c.in, c.out, c.gapBefore || 0]; }); }
var A = [C("c1", "k1", 0, 3), C("c2", "k2", 0, 2), C("c3", "k1", 1, 4)];
// 롤링: 전체 길이 그대로, 앞 out · 뒤 in 함께
var r1 = edit215cRoll(A, 0, 0.5, 10, 24, 0.04);
var r2 = edit215cRoll(A, 0, 5, 10, 24, 0.04);
var r3 = edit215cRoll(A, 1, -0.25, 10, 24, 0.04);
R.roll = [io(r1), tot(r1), io(r2).slice(0, 2), tot(r2), io(r3), tot(r3), noOverlap(r1) && noOverlap(r2), edit215cRoll(A, 2, 1, 10, 24, 0.04)];
// 밀기: 클립 in/out 그대로, 이웃 길이 합 그대로
var s1 = edit215cSlide(A, 1, 0.5, 10, 24, 0.04);
var s2 = edit215cSlide(A, 1, -1, 10, 24, 0.04);
R.slide = [io(s1), tot(s1), editStarts(s1).starts, io(s2), tot(s2), edit215cSlide(A, 0, 1, 10, 24, 0.04), edit215cSlide(A, 2, 1, 10, 24, 0.04),
           noOverlap(s1) && noOverlap(s2)];
// 밀어넣기: 자리 · 길이 그대로
var p1 = edit215cSlip(A[2], 0.5, 10, 24);
var p2 = edit215cSlip(A[2], -20, 10, 24);
R.slip = [[p1.in, p1.out], [p2.in, p2.out], edit215cSlip(A[1], 1, 10, 24)];
// 잔물결(기존 끝 끌기와 같은 길): out 을 줄이면 뒤가 당겨진다
var rp = A.slice(); rp[0] = editTrimClip(A[0], "out", 2.5, 10, 24, 0.04);
R.ripple = [tot(rp), editStarts(rp).starts];
// 들어내기 vs 지우고 당기기
var lift = edit215cLift(A, [1]);
var rdel = edit215cRippleDelete(A, [1]);
R.lift = [io(lift), tot(lift), io(rdel), tot(rdel), io(edit215cRippleDelete(lift, [1])), io(edit215cCloseGap(lift, 1)), edit215cCloseGap(A, 1)];
var D = [C("d1", "k1", 0, 3, { transition: { type: "dissolve", seconds: 0.5 } }), C("d2", "k2", 0, 2), C("d3", "k1", 1, 4)];
var dl = edit215cLift(D, [1]);
R.liftDissolve = [tot(D), io(dl), tot(dl), dl[0].transition === undefined];
// 표시 구간: 들어내기(빈틈 남김) vs 뽑아내기(당김), 클립 안 구간은 둘로 나눔(새 id)
var n = 0; function mk() { n += 1; return "n" + n; }
var g1 = edit215cRange(A, 2, 5, false, mk, 24, 0.04);
var g2 = edit215cRange(A, 2, 5, true, mk, 24, 0.04);
var g3 = edit215cRange(A, 1, 2, false, mk, 24, 0.04);
var g4 = edit215cRange(A, 1, 2, true, mk, 24, 0.04);
var g5 = edit215cRange(lift, 3, 5, true, mk, 24, 0.04);
R.range = [io(g1), tot(g1), io(g2), tot(g2), io(g3), tot(g3), io(g4), tot(g4), io(g5), tot(g5), edit215cRange(A, 9, 9, false, mk, 24, 0.04),
           [g1, g2, g3, g4, g5].every(noOverlap)];
var lay = edit215cLayout(A);
R.markClip = [lay[1].start, lay[1].end, edit215cEditPoints(A), edit215cEditPoints(lift)];
R.pack = [edit215cPack([{ clip: A[0], start: 0 }, { clip: A[1], start: 2 }]), io(edit215cPack([{ clip: A[0], start: 1 }]))];
// 속도 · 역재생 · 음높이 · 속성
var long = C("L", "k1", 0, 16);
var sp1 = edit215cSpeedApply(long, 1, true, true, 0.04);
var sp2 = edit215cSpeedApply(C("S", "k1", 0, 10), 2, true, false, 0.08);
var sp3 = edit215cSpeedApply(C("S", "k1", 0, 0.05), 2, false, true, 0.08);
R.speed = [sp1.error, sp2.clip, sp3.error, edit215cSpeedApply(C("S", "k1", 0, 15), 1, true, true, 0.04).clip.reverse];
var at = edit215cCopyAttrs(C("x", "k", 0, 4, { labelColor: "red", speed: 2, gain: -3 }));
R.attrs = [at, edit215cPasteAttrs(C("y", "k", 0, 0.06), at, 0.08), edit215cPasteAttrs(C("z", "k", 0, 4, { labelColor: "blue", gain: 6 }), { color: "", speed: 1, gain: 0 }, 0.04)];
R.clone = [editCloneClip(C("a", "k", 0, 1, { gapBefore: 0.5, disabled: true, reverse: true, keepPitch: false, gain: 2.5, labelColor: "teal", unlinked: true, bogus: 1 })),
           editCloneClip(C("b", "k", 0, 1, { gapBefore: 0, disabled: false, keepPitch: true, gain: 99, labelColor: "x", unlinked: "yes" })),
           editSaveBody("e", "webtoon", [C("o", "k", 0, 1)])];
R.splitGap = editSplit([C("a", "k", 0, 4, { gapBefore: 1 })], 2, "n9", 24, 0.04).clips.map(function (c) { return [c.id, c.gapBefore || 0]; });
R.startsGap = editStarts([C("a", "k", 0, 2, { gapBefore: 1 }), C("b", "k", 0, 2, { gapBefore: 0.5 })]);
// 단축키 표
var KEYS = [["v", "KeyV"], ["ㅍ", "KeyV"], ["c", "KeyC"], ["b", "KeyB"], ["n", "KeyN"], ["y", "KeyY"], ["u", "KeyU"], ["i", "KeyI"], ["o", "KeyO"],
            ["x", "KeyX"], [";", "Semicolon"], ["'", "Quote"], ["j", "KeyJ"], ["k", "KeyK"], ["l", "KeyL"], ["ArrowUp", ""], ["ArrowDown", ""],
            ["\\", "Backslash"], ["?", "Slash", 0, 1], ["M", "KeyM", 0, 1], ["M", "KeyM", 1, 1], ["E", "KeyE", 0, 1], ["r", "KeyR", 1],
            ["c", "KeyC", 1, 0, 1], ["v", "KeyV", 1, 0, 1], ["m", "KeyM", 0, 0, 1], ["ArrowLeft", "", 0, 0, 1], ["Delete", "", 0, 0, 1],
            ["Delete", "", 0, 1], ["m", "KeyM"], ["Delete", ""], ["Backspace", ""], ["ArrowRight", "", 0, 1], ["ArrowRight", ""], [" ", "Space"],
            ["s", "KeyS"], ["b", "KeyB", 1], ["z", "KeyZ", 1], ["y", "KeyY", 1], ["=", "Equal"], ["[", "BracketLeft"], ["Enter", "Enter"], ["Escape", "Escape"]];
function km(p) { return KEYS.map(function (k) { return edit215cKeyAction(k[0], k[1], !!k[2], !!k[3], !!k[4], p); }); }
R.keys = { vnew: km("vnew"), premiere: km("premiere") };
R.keymapRows = EDIT215C_KEYMAP.length;
console.log(JSON.stringify(R));
"""

_NODE_UI = r"""
var R = {};
function C(id, key, a, b, extra) { var c = { id: id, sourceKey: key, in: a, out: b, muted: false }; Object.keys(extra || {}).forEach(function (k) { c[k] = extra[k]; }); return c; }
// 무거운 그리기 · 재생은 가볍게 바꾼다(같은 이름 선언이 뒤에 오면 이긴다). 215c 함수와 editCommit · editUndoRedo · editTrack 은 진짜.
var notes = [];
function editChanged(ctx) { ctx.rev += 1; }
function editSeek(ctx, t) { ctx.t = Math.max(0, Math.min(editStarts(ctx.clips).total, Number(t) || 0)); }
function editPause(ctx) { ctx.playing = false; edit215cShuttleStop(ctx); }
function editPlay(ctx) { ctx.playing = true; }
function editNote(ctx, m) { if (m) notes.push(m); }
function editPlaceFx(ctx) {}
function editPaintClip(ctx, parts, clip) { parts.painted = (parts.painted || 0) + 1; }
function editAlive(ctx) { return true; }
function editSelect(ctx, i) { ctx.sel = i; ctx.multi = i >= 0 ? [i] : []; }
var content = new FakeNode("div");
var ctx = { clips: [C("c1", "k1", 0, 3), C("c2", "k2", 0, 2), C("c3", "k1", 1, 4)], pps: 100, t: 0, sel: -1, multi: [], rev: 0, busy: false,
            hist: { undo: [], redo: [] }, variant: "webtoon", materials: { k1: { duration: 10, fps: 24 }, k2: { duration: 10, fps: 24 } },
            dom: { content: content, scroller: new FakeNode("div") }, clipEls: [], limits: {}, vids: [], playing: false, root: new FakeNode("div") };
function press(k, code, extra) {
  var ev = { key: k, code: code || "", target: document.body, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };
  Object.keys(extra || {}).forEach(function (n) { ev[n] = extra[n]; });
  ev.preventDefault = function () { ev.defaultPrevented = true; };
  var used = edit215cOnKey(ctx, ev);
  return [used, !!ev.defaultPrevented];
}
function drag(target, x0, x1, partsIndex, fn) {
  var parts = { index: partsIndex, box: target, inEdge: target, outEdge: target };
  fn(parts, { clientX: x0, pointerId: 1, pointerType: "mouse", button: 0 });
  target.fire("pointermove", { clientX: x1 });
  target.fire("pointerup", {});
}
function io() { return ctx.clips.map(function (c) { return [c.id, c.in, c.out, c.gapBefore || 0]; }); }
var s = edit215cState(ctx);
R.toolKeys = [press("y", "KeyY"), s.tool, press("u", "KeyU"), s.tool, press("n", "KeyN"), s.tool, press("c", "KeyC"), s.tool,
              press("v", "KeyV"), s.tool, press("m", "KeyM"), press("Delete", "Delete"), press("ArrowRight", "ArrowRight", { shiftKey: true })];
// 밀어넣기(Y): c3 몸통을 오른쪽으로 50px = 0.5초 → in/out 0.5초 앞
press("y", "KeyY");
var box = new FakeNode("div");
drag(box, 700, 750, 2, function (parts, ev) { edit215cClipPointer(ctx, parts, ev); });
R.slip = [io(), ctx.hist.undo.length, ctx.sel];
// 밀기(U): c2 를 오른쪽으로 0.5초 → c1.out 3.5 · c3.in 1.5
press("u", "KeyU");
drag(new FakeNode("div"), 400, 450, 1, function (parts, ev) { edit215cClipPointer(ctx, parts, ev); });
R.slide = [io(), editRound3(editStarts(ctx.clips).total), ctx.hist.undo.length];
// 롤링(N): c1 끝 손잡이를 오른쪽으로 0.25초
press("n", "KeyN");
drag(new FakeNode("div"), 350, 375, 0, function (parts, ev) { edit215cEdgePointer(ctx, parts, "out", ev); });
R.roll = [io(), editRound3(editStarts(ctx.clips).total), ctx.hist.undo.length];
// 자르기 도구(C): c1 안 1.0초를 누르면 거기서 나눈다
press("c", "KeyC");
var cbox = new FakeNode("div");
edit215cClipPointer(ctx, { index: 0, box: cbox }, { clientX: 100, pointerId: 2, pointerType: "mouse", button: 0 });
R.razor = [ctx.clips.length, ctx.clips[0].out, ctx.clips[1].in, ctx.sel];
// 실행 취소 네 번이면 처음으로
for (var u = 0; u < 4; u++) editUndoRedo(ctx, false);
R.undo = io();
// 마커(Premiere 묶음 M) · 실행 취소로 지움 · 다시 실행으로 되살림 · Shift+M 이동
s.preset = "premiere";
ctx.t = 1.5;
press("m", "KeyM");
var afterAdd = s.markers.map(function (m) { return [m.t, m.name, m.color]; });
ctx.t = 0;
press("M", "KeyM", { shiftKey: true });
var jumped = ctx.t;
editUndoRedo(ctx, false);
var afterUndo = s.markers.length;
editUndoRedo(ctx, true);
R.markers = [afterAdd, jumped, afterUndo, s.markers.length, edit215cBody(ctx, { clips: [] }).markers.length];
// 표시(I/O) → 뽑아내기(')
ctx.t = 1; press("i", "KeyI"); ctx.t = 2; press("o", "KeyO");
var marks = [s.markIn, s.markOut];
press("'", "Quote");
R.extract = [marks, io(), editRound3(editStarts(ctx.clips).total), s.markIn];
// 들어내기(Premiere Delete) → 빈틈 · 지우고 당기기(Shift+Delete)
editSelect(ctx, 0);
press("Delete", "Delete");
var lifted = io();
editSelect(ctx, 0);
press("Delete", "Delete", { shiftKey: true });
R.lift = [lifted, io()];
// 트랙 잠금이면 바꾸는 키가 막힌다
s.tracks = { v1: { locked: true } };
var before = JSON.stringify(ctx.clips);
editSelect(ctx, 0);
R.locked = [edit215cBlocked(ctx, "split"), edit215cBlocked(ctx, "play"), press("E", "KeyE", { shiftKey: true })[0], JSON.stringify(ctx.clips) === before,
            notes[notes.length - 1]];
s.tracks = {};
// J/K/L
ctx.playing = false;
press("l", "KeyL"); var l1 = [ctx.playing, edit215cRate];
press("l", "KeyL"); var l2 = edit215cRate;
press("l", "KeyL"); press("l", "KeyL"); var l4 = edit215cRate;
press("k", "KeyK"); var k1 = [ctx.playing, edit215cRate, s.kDown];
ctx.t = 1; press("l", "KeyL"); var kl = editRound3(ctx.t);
edit215cKeyUp(ctx, { code: "KeyK", key: "k" });
ctx.t = 2; press("j", "KeyJ"); var j1 = [s.rev, editRound3(ctx.t), !!s.revTimer];
press("k", "KeyK"); var j2 = [s.rev, s.revTimer];
R.shuttle = [l1, l2, l4, k1, kl, j1, j2];
// 메뉴 · 창이 열린 동안은 ⑦ 단축키를 막고 Esc 로 닫는다
edit215cKeymapPanel(ctx);
R.panel = [!!s.panel, press(" ", "Space")[0], press("Escape", "Escape"), s.panel];
console.log(JSON.stringify(R));
"""


def _node_run(node: str, script: str) -> tuple[dict | None, str]:
    result = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout)[-1500:]
    try:
        return json.loads(result.stdout.strip().splitlines()[-1]), ""
    except (ValueError, IndexError) as exc:
        return None, f"출력 읽기 실패 {exc!r}: {result.stdout[-500:]}"


def check_edit_tools_215c() -> bool:  # noqa: C901 — 한 검사 함수(다른 작업 검사와 같은 모양)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    from src import stitcher  # noqa: PLC0415
    from src.vnew import edit as E  # noqa: PLC0415
    from src.vnew import edit_tools as T  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def clean(clips):
        return E._clean_clips(json.loads(json.dumps(clips)), ALLOWED, DURS, "t")  # noqa: SLF001

    def reject(clips) -> list:
        try:
            clean(clips)
            return ["통과"]
        except VpocError as exc:
            return [exc.kind, exc.status, exc.message]

    # ================= (et-a) 서버 검증 =================
    try:
        got = clean(NEW)
        expect("et-a", [{k: v for k, v in c.items() if k in T.CLIP_KEYS} for c in got],
               [{"labelColor": "blue", "gain": 3.0}, {"gapBefore": 1.5, "reverse": True, "keepPitch": False},
                {"disabled": True, "unlinked": True}], "새 값은 기본값이 아닐 때만 저장(게인 dB · 빈틈 초 · 역재생 · 음높이 false · 사용 안 함)")
        expect("et-a", [E._total(got), E._total(OLD)], [3 + 1.5 + 1 + 2, 3 + 4 + 2 - 0.5], "전체 길이 = 클립(속도 반영) + 빈틈 − 디졸브")  # noqa: SLF001
        quiet = clean([dict(NEW[0], gain=0, gapBefore=0, disabled=False, reverse=False, keepPitch=True, unlinked=False, labelColor=None)])
        expect("et-a", sorted(quiet[0]), ["id", "in", "muted", "out", "sourceKey"], "0 · false · true(음높이) 는 저장하지 않는다")
        cases = [dict(NEW[0], gapBefore=-1), dict(NEW[0], gapBefore="1"), dict(NEW[0], gapBefore=301), dict(NEW[0], disabled="yes"),
                 dict(NEW[0], reverse=1), dict(NEW[0], keepPitch="no"), dict(NEW[0], gain=0.3), dict(NEW[0], gain=13), dict(NEW[0], gain=True),
                 dict(NEW[0], labelColor="black"), dict(NEW[0], unlinked=0)]
        expect("et-a", [reject([c])[:2] for c in cases], [["BadClip", 400]] * len(cases), "틀린 값 11경우 400 BadClip")
        long_rev = reject([{"id": "r", "sourceKey": K1, "in": 0.0, "out": 15.5, "muted": False, "reverse": True}])
        ok_rev = reject([{"id": "r", "sourceKey": K1, "in": 0.0, "out": 15.0, "muted": False, "reverse": True}])
        expect("et-a", [long_rev[:2], "15초 이하" in long_rev[2] and "역재생" in long_rev[2], ok_rev],
               [["BadClipRange", 400], True, ["통과"]], "역재생은 원본 구간 15초 이하만(넘으면 한국어 문구로 막힘)")
        gap_rule = reject([dict(OLD[0], transition={"type": "dissolve", "seconds": 1.0}), dict(NEW[0], id="z", gapBefore=2.0)])
        gap_ok = reject([dict(OLD[0], transition={"type": "dissolve", "seconds": 0.5}), dict(NEW[0], id="z", gapBefore=2.0)])
        too_long = reject([dict(NEW[0], gapBefore=298.0)])
        expect("et-a", [gap_rule[:2], gap_ok, too_long[:2]], [["BadTransition", 400], ["통과"], ["TooLong", 400]],
               "빈틈 앞 디졸브 ≤ 빈틈 45% · 빈틈도 300초 한도에 센다")
        extras = T.clean_doc_extras({"markers": [{"id": "m2", "t": 4.0, "name": "끝\x07", "color": "red"},
                                                 {"id": "m1", "t": 1.23456, "name": "x" * 50}],
                                     "tracks": {"v1": {"locked": True, "hidden": False}, "a1": {"hidden": False}}}, "t")
        expect("et-a", extras, {"markers": [{"id": "m1", "t": 1.235, "name": "x" * 40, "color": "green"},
                                            {"id": "m2", "t": 4.0, "name": "끝", "color": "red"}], "tracks": {"v1": {"locked": True}}},
               "마커 정렬 · 이름 40자 · 제어 문자 빼기 · 기본 색 · 트랙 false 는 저장 안 함")
        bad_docs = [{"markers": "x"}, {"markers": [{"id": "a", "t": -1}]}, {"markers": [{"id": "a", "t": 1}, {"id": "a", "t": 2}]},
                    {"markers": [{"id": "a", "t": 1, "color": "black"}]}, {"tracks": {"v2": {}}}, {"tracks": {"v1": {"locked": 1}}}]
        kinds = []
        for raw in bad_docs:
            try:
                T.clean_doc_extras(raw, "t")
                kinds.append("통과")
            except VpocError as exc:
                kinds.append(exc.kind)
        expect("et-a", [kinds, T.clean_doc_extras({}, "t"), T.clean_doc_extras({"markers": [], "tracks": {}}, "t")],
               [["BadMarkers"] * 4 + ["BadTracks"] * 2, {}, {}], "틀린 마커·트랙 400 · 비었으면 문서에 넣지 않는다")
        hidden = T.apply_tracks(got, {"tracks": {"v1": {"hidden": True}, "a1": {"hidden": True}}})
        expect("et-a", [[c.get("hideVideo"), c["muted"]] for c in hidden] + [T.apply_tracks(got, {"tracks": {"v1": {"locked": True}}}) is got],
               [[True, True]] * 3 + [True], "숨긴 영상·소리 트랙 → 내보내기 사본(화면 검게 · 소리 끔), 잠금만이면 그대로")
        expect("et-a", [E._doc_hash("webtoon", hidden) != E._doc_hash("webtoon", got),  # noqa: SLF001
                        E._doc_hash("webtoon", [dict(got[0], labelColor="red")]) == E._doc_hash("webtoon", [got[0]])], [True, True],  # noqa: SLF001
               "숨긴 트랙 · 빈틈 등은 해시에 들어가고 라벨 색은 안 들어간다")
    except Exception as exc:  # noqa: BLE001
        crash("et-a", exc)

    # ================= (et-b) 내보내기 계획 · 명령 =================
    try:
        got = clean(NEW)
        run, meta, plan = T.export_plan(got, META, 24, 1280, 720)
        expect("et-b", [[c["sourceKey"].replace(BASE, ""), c["in"], c["out"], c["muted"]] for c in run],
               [["215c:d0", 0.0, 3.0, False], ["215c:black", 0.0, 1.5, True], ["215c:d1", 4.0, 6.0, False], ["215c:black", 0.0, 2.0, True]],
               "게인 → 파생 원본 · 빈틈 → 검은 클립 · 역재생 in/out = 파생 원본 시각(여유 4초) · 사용 안 함 → 검은 클립(자리 그대로)")
        spec = plan["derived"]
        expect("et-b", [[spec[k][f] for f in ("from", "reverse", "gain", "varispeed", "a", "b", "audio")] for k in sorted(spec)],
               [[K1, False, 3.0, 1.0, 0.0, 20.0, True], [K2, True, 0.0, 2.0, 0.0, 7.0, True]], "파생 원본 계획(원본 · 역재생 구간 · 게인 · 음높이)")
        frames = E._clip_frames(run, 24)  # noqa: SLF001
        expect("et-b", [sum(frames), round(E._total(got) * 24), any(k in c for c in run for k in T.CLIP_KEYS)],  # noqa: SLF001
               [180, 180, False], "프레임 합 = 문서 길이 × fps · 내보내기 클립에는 215c 값이 남지 않는다")
        commands: list[list[str]] = []
        saved_run = T._run_ffmpeg  # noqa: SLF001

        def fake(cmd):
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"x")

        T._run_ffmpeg = fake  # noqa: SLF001
        try:
            with tempfile.TemporaryDirectory() as work:
                local: dict = {}
                fetched = []

                def fetch(key, name):
                    fetched.append(key)
                    path = os.path.join(work, name)
                    Path(path).write_bytes(b"src")
                    local[key] = path
                    return path

                T.export_prepare(plan, local, fetch, work, fps=24, width=1280, height=720)
                left = sorted(k.replace(BASE, "") for k in local)
                files = sorted(os.listdir(work))
        finally:
            T._run_ffmpeg = saved_run  # noqa: SLF001
        text = [" ".join(c) for c in commands]
        reverse_cmds = [t for t in text if ",reverse " in t + " "]
        expect("et-b", [len(commands), len(reverse_cmds), sum("areverse" in t for t in text), sum("volume=3dB" in t for t in text),
                        sum("asetrate=96000" in t and "atempo=0.5" in t for t in text), sum("color=black:s=1280x720:r=24" in t for t in text),
                        sum("-f concat" in t for t in text)],
               [1 + 1 + 4 + 1, 4, 1, 1, 1, 1, 1], "명령: 검정 1 · 게인(화면 복사) 1 · 역재생 2초 조각 4개 + 잇기(areverse · 음높이 asetrate)")
        expect("et-b", [left, files, fetched], [["215c:black", "215c:d0", "215c:d1"], ["215c-black.mp4", "215c-d000.mp4", "215c-d001.mp4"],
                                                [K1, K2]], "파생에만 쓴 원본 파일은 지우고 파생 · 검정 파일만 남긴다(조각 · 목록 파일도 지움)")
        hide_run, _m, hide_plan = T.export_plan(T.apply_tracks(clean(OLD), {"tracks": {"v1": {"hidden": True}}}), META, 24, 1280, 720)
        expect("et-b", [sorted(hide_plan["derived"]), [hide_plan["derived"][k]["black"] for k in sorted(hide_plan["derived"])],
                        [c["in"] for c in hide_run], hide_run[0].get("transition")],
               [["215c:d0", "215c:d1", "215c:d2"], [True] * 3, [0.0, 1.0, 4.0], {"type": "dissolve", "seconds": 0.5}],
               "영상 트랙 숨김 = 검은 화면 + 원본 소리(자리 · 디졸브 · J/L 그대로)")
    except Exception as exc:  # noqa: BLE001
        crash("et-b", exc)

    # ================= (et-c) 실측 =================
    try:
        binary = stitcher.ffmpeg_path()
    except Exception:  # noqa: BLE001
        binary = ""
    if not binary or not (os.path.exists(binary) or shutil.which(binary)):
        warns.append("ffmpeg 가 없어 et-c 실측을 건너뜁니다")
        seen.add("et-c")
    else:
        try:
            with tempfile.TemporaryDirectory() as work:
                src = os.path.join(work, "src.mp4")
                subprocess.run([binary, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=5",
                                "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                                "-shortest", src], check=True, capture_output=True, timeout=120)
                clips = [{"id": "r", "sourceKey": "k", "in": 1.0, "out": 3.0, "muted": False, "reverse": True, "gapBefore": 0.5}]
                run, meta, plan = T.export_plan(clips, {"k": {"duration": 5.0, "fps": 24, "width": 320, "height": 180, "hasAudio": True}},
                                                24, 320, 180)
                local: dict = {}
                T.export_prepare(plan, local, lambda key, name: src, work, fps=24, width=320, height=180)
                info = stitcher.probe_media(local["215c:d0"])
                black = stitcher.probe_media(local[T.BLACK_KEY])
                seg = os.path.join(work, "seg.mp4")
                stitcher.edit_segment(local["215c:d0"], seg, start_sec=run[1]["in"], frames=48, fps=24, width=320, height=180)
                out = stitcher.probe_media(seg)
            expect("et-c", [round(info["duration"], 1), info["hasAudio"], [info["width"], info["height"]], round(black["duration"], 1),
                            [run[1]["in"], run[1]["out"]], round(out["duration"], 2)],
                   [5.0, True, [320, 180], 1.5, [2.0, 4.0], 2.0], "역재생 파생 원본 5초(여유 포함 · 소리 있음) · 검정 1.5초 · 2초 자르기")
        except Exception as exc:  # noqa: BLE001
            crash("et-c", exc)

    # ================= (et-d · et-e) node =================
    node = shutil.which("node")
    vjs = (ROOT / "frontend" / "public" / "vnew.js").read_text(encoding="utf-8")
    if not node:
        warns.append("node 가 없어 et-d · et-e 화면 실행을 건너뜁니다")
        seen.update(("et-d", "et-e"))
    else:
        try:
            import check_vpoc as CV  # noqa: PLC0415 — 읽기만(화면 하네스 · ⑦ 구역 자르기)

            code, problems = CV._edu_code(vjs, CV._EDU_SLICES)  # noqa: SLF001
            expect("et-d", problems, [], "⑦ 구역을 자를 수 있다")
            got, err = _node_run(node, CV._EDU_NODE_HARNESS + code + _NODE)  # noqa: SLF001
            if err:
                bad["et-d"].append("node 실행 실패: " + err)
                seen.add("et-d")
            else:
                io = lambda rows: [list(r) for r in rows]  # noqa: E731
                expect("et-d", got["roll"], [
                    io([["c1", 0, 3.5, 0], ["c2", 0.5, 2, 0], ["c3", 1, 4, 0]]), 8, io([["c1", 0, 4.958, 0], ["c2", 1.958, 2, 0]]), 8,
                    io([["c1", 0, 3, 0], ["c2", 0, 1.75, 0], ["c3", 0.75, 4, 0]]), 8, True, None],
                    "롤링: 앞 out · 뒤 in 함께 · 끝까지 끌면 한 프레임 남기고 멈춤 · 전체 8초 그대로 · 마지막 클립 뒤는 없음")
                expect("et-d", got["slide"], [
                    io([["c1", 0, 3.5, 0], ["c2", 0, 2, 0], ["c3", 1.5, 4, 0]]), 8, [0, 3.5, 5.5],
                    io([["c1", 0, 2, 0], ["c2", 0, 2, 0], ["c3", 0, 4, 0]]), 8, None, None, True],
                    "밀기: 클립 in/out 그대로 · 앞 이웃 out · 뒤 이웃 in(원본 0 에서 멈춤) · 전체 그대로 · 처음/끝 클립은 못 민다")
                expect("et-d", got["slip"], [[0.5, 3.5], [7, 10], None], "밀어넣기: 오른쪽 끌기 = 앞 원본 · 원본 끝에서 멈춤 · 원본 0 이면 그대로")
                expect("et-d", got["ripple"], [7.5, [0, 2.5, 4.5]], "잔물결: 끝을 줄이면 뒤 클립이 당겨진다")
                expect("et-d", got["lift"], [io([["c1", 0, 3, 0], ["c3", 1, 4, 2]]), 8, io([["c1", 0, 3, 0], ["c3", 1, 4, 0]]), 6,
                                             io([["c1", 0, 3, 0]]), io([["c1", 0, 3, 0], ["c3", 1, 4, 0]]), None],
                       "들어내기 = 빈틈 2초(전체 8초) · 지우고 당기기 = 6초 · 빈틈 뒤 클립을 지우면 빈틈은 남지 않음 · 빈틈 지우기")
                expect("et-d", got["liftDissolve"], [7.5, io([["d1", 0, 3, 0], ["d3", 1, 4, 1.5]]), 7.5, True],
                       "디졸브로 겹친 클립을 들어내면 앞 전환을 지우고 뒤 클립 자리 그대로(빈틈 1.5초)")
                expect("et-d", got["range"], [
                    io([["c1", 0, 2, 0], ["c3", 1, 4, 3]]), 8, io([["c1", 0, 2, 0], ["c3", 1, 4, 0]]), 5,
                    io([["c1", 0, 1, 0], ["n1", 2, 3, 1], ["c2", 0, 2, 0], ["c3", 1, 4, 0]]), 8,
                    io([["c1", 0, 1, 0], ["n2", 2, 3, 0], ["c2", 0, 2, 0], ["c3", 1, 4, 0]]), 7,
                    io([["c1", 0, 3, 0], ["c3", 1, 4, 0]]), 6, None, True],
                    "표시 구간: 들어내기(빈틈) · 뽑아내기(당김) · 클립 안 구간은 나눔(새 id) · 빈틈이 든 구간 뽑아내기 · 클립 없는 구간 null")
                expect("et-d", got["markClip"], [3, 5, [0, 3, 5, 8], [0, 3, 5, 8]], "X = 재생헤드 아래 클립 시작·끝 · 편집점(빈틈 포함)")
                expect("et-d", got["pack"], [None, io([["c1", 0, 3, 1]])], "겹치면 null · 앞이 비면 첫 클립 빈틈")
                sp = got["speed"]
                expect("et-d", [sp[0] and "15초 이하" in sp[0], sp[1], sp[2], sp[3]],
                       [True, {"id": "S", "sourceKey": "k1", "in": 0, "out": 10, "muted": False, "speed": 2, "reverse": True, "keepPitch": False},
                        "이 속도로는 클립이 너무 짧아집니다.", True],
                       "속도/지속 시간: 역재생 15초 넘으면 한국어 문구 · 역재생·음높이 저장 · 너무 짧은 속도 거절")
                expect("et-d", got["attrs"], [{"color": "red", "speed": 2, "gain": -3},
                                              {"id": "y", "sourceKey": "k", "in": 0, "out": 0.06, "muted": False, "labelColor": "red", "gain": -3},
                                              {"id": "z", "sourceKey": "k", "in": 0, "out": 4, "muted": False}],
                       "속성 복사/붙여넣기(색 · 속도 · 게인, 너무 짧아지는 속도는 그대로) · 빈 속성은 지운다")
                expect("et-d", got["clone"], [
                    {"id": "a", "sourceKey": "k", "in": 0, "out": 1, "muted": False, "gapBefore": 0.5, "disabled": True, "reverse": True,
                     "keepPitch": False, "gain": 2.5, "labelColor": "teal", "unlinked": True},
                    {"id": "b", "sourceKey": "k", "in": 0, "out": 1, "muted": False},
                    {"etag": "e", "variant": "webtoon", "clips": [{"id": "o", "sourceKey": "k", "in": 0, "out": 1, "muted": False}], "audio": []}],
                    "사본 · 저장 바디: 새 값은 맞을 때만, 옛 클립은 215c 전 모양")
                expect("et-d", [got["splitGap"], got["startsGap"]], [[["a", 1], ["n9", 0]], {"starts": [1, 3.5], "total": 5.5}],
                       "자르면 빈틈은 앞 조각에만 · editStarts 가 빈틈을 센다")
                common = ["tool:select", "tool:select", "tool:razor", "tool:ripple", "tool:roll", "tool:slip", "tool:slide", "markIn", "markOut",
                          "markClip", "liftRange", "extractRange", "shuttleJ", "shuttleK", "shuttleL", "prevEdit", "nextEdit", "fit", "keymap",
                          "nextMarker", "prevMarker", "toggleEnable", "speedDialog", "copyAttrs", "pasteAttrs", "marker", "step5Back", "lift",
                          "rippleDelete"]
                tail = [""] * 14
                expect("et-d", got["keys"], {"vnew": common + ["", "", "", ""] + tail[4:],
                                             "premiere": common + ["marker", "lift", "lift", "step5Fwd"] + tail[4:]},
                       "단축키 표: 두 묶음 공통 · ⑦ 기본은 M · Delete · Shift+→ 를 기존 길로 넘기고 Premiere 는 마커 · 들어내기 · 5프레임 · "
                       "Space · S · Ctrl+B/Z/Y · = · [ · Enter · Esc 는 늘 기존 길")
                expect("et-d", got["keymapRows"] >= 30, True, "? 창 단축키 표 30줄 이상")
            got, err = _node_run(node, CV._EDU_NODE_HARNESS + code + _NODE_UI)  # noqa: SLF001
            if err:
                bad["et-e"].append("node 실행 실패: " + err)
                seen.add("et-e")
            else:
                expect("et-e", got["toolKeys"], [[True, True], "slip", [True, True], "slide", [True, True], "roll", [True, True], "razor",
                                                 [True, True], "select", [False, False], [False, False], [False, False]],
                       "Y·U·N·C·V 로 도구 바꾸기 · ⑦ 기본 묶음의 M · Delete · Shift+→ 는 기존 처리기로 넘긴다")
                expect("et-e", got["slip"], [[["c1", 0, 3, 0], ["c2", 0, 2, 0], ["c3", 0.5, 3.5, 0]], 1, 2], "밀어넣기 끌기 50px → in/out −0.5초 · 실행 취소 1")
                expect("et-e", got["slide"], [[["c1", 0, 3.5, 0], ["c2", 0, 2, 0], ["c3", 1, 3.5, 0]], 8, 2],
                       "밀기 끌기: 앞 out +0.5 · 뒤 in +0.5 · 전체 8초")
                expect("et-e", got["roll"], [[["c1", 0, 3.75, 0], ["c2", 0.25, 2, 0], ["c3", 1, 3.5, 0]], 8, 3],
                       "롤링 끌기 +0.25초: 앞 out · 뒤 in 함께 · 전체 그대로(뒤 클립이 원본 0 이면 왼쪽으로는 못 민다 — et-d)")
                expect("et-e", got["razor"], [4, 1, 1, 1], "자르기 도구: 누른 1초에서 나누고 오른쪽 조각 고름")
                expect("et-e", got["undo"], [["c1", 0, 3, 0], ["c2", 0, 2, 0], ["c3", 1, 4, 0]], "실행 취소 네 번 = 처음 문서")
                expect("et-e", got["markers"], [[[1.5, "마커 1", "green"]], 1.5, 0, 1, 1], "M 마커 · Shift+M 이동 · 실행 취소/다시 실행 · 저장 바디")
                ex = got["extract"]
                expect("et-e", [ex[0], [r[1:] for r in ex[1]], ex[2], ex[3]],
                       [[1, 2], [[0, 1, 0], [2, 3, 0], [0, 2, 0], [1, 4, 0]], 7, None], "I · O 표시 → ' 뽑아내기(1초 당김) · 표시 지움")
                lf = got["lift"]
                expect("et-e", [[r[1:] for r in lf[0]], [r[1:] for r in lf[1]]],
                       [[[2, 3, 1], [0, 2, 0], [1, 4, 0]], [[0, 2, 1], [1, 4, 0]]],
                       "Premiere Delete = 들어내기(앞 빈틈 1초) · Shift+Delete = 지우고 당기기(앞 빈틈은 남는다)")
                expect("et-e", got["locked"], [True, False, True, True, "영상 트랙이 잠겨 있습니다. 트랙 이름 칸의 🔒 를 눌러 풀어 주세요."],
                       "영상 트랙 잠금: 바꾸는 키는 막고 안내, 재생은 그대로")
                expect("et-e", got["shuttle"], [[True, 1], 2, 4, [False, 1, True], 1.042, [1, 1.9, True], [0, 0]],
                       "L 1× → 2× → 4× · K 멈춤 · K+L 한 프레임 · J 뒤로(0.1초씩) · K 로 멈춤")
                expect("et-e", got["panel"], [True, True, [True, True], None], "단축키 창이 열린 동안 Space 막음 · Esc 로 닫기")
        except Exception as exc:  # noqa: BLE001
            crash("et-d", exc)

    # ================= (et-f) 옛 문서 그대로 · LF · 연결 줄 =================
    try:
        old = clean(OLD)
        expect("et-f", old, [dict(c, **{"in": float(c["in"]), "out": float(c["out"])}) for c in OLD], "옛 문서 정리 결과 = 받은 그대로")
        expect("et-f", [E._doc_hash("webtoon", old) == _old_hash("webtoon", old), T.export_plan(old, META, 24, 1280, 720)[2],  # noqa: SLF001
                        T.export_plan(old, META, 24, 1280, 720)[0] is old, T.apply_tracks(old, {}) is old, T.gap_total(old)],
               [True, None, True, True, 0], "옛 문서 해시 = 215c 전 계산 · 내보내기 계획 없음(명령 그대로) · 트랙 없음")
        files = [ROOT / "app" / "src" / "vnew" / "edit_tools.py", ROOT / "app" / "src" / "vnew" / "edit.py",
                 ROOT / "frontend" / "public" / "vnew.js", ROOT / "frontend" / "public" / "vnew.css", Path(__file__).resolve()]
        expect("et-f", [p.name for p in files if b"\r" in p.read_bytes()], [], "LF 줄 끝")
        css = (ROOT / "frontend" / "public" / "vnew.css").read_text(encoding="utf-8")
        edit_py = (ROOT / "app" / "src" / "vnew" / "edit.py").read_text(encoding="utf-8")
        expect("et-f", [vjs.count(EXPECTED_BLOCK[0]), vjs.count(EXPECTED_BLOCK[1]), css.count(EXPECTED_BLOCK[0]), css.count(EXPECTED_BLOCK[1]),
                        vjs.count("// 작업 215c"), edit_py.count("edit_tools."), "innerHTML" in vjs[vjs.find(EXPECTED_BLOCK[0]):vjs.find(EXPECTED_BLOCK[1])]],
               [1, 1, 1, 1, 17, 9, False], "JS · CSS 블록 하나씩 · JS 연결 줄 17 · edit.py 연결 9")
    except Exception as exc:  # noqa: BLE001
        crash("et-f", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 215c ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 215c ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 215c ({group}) {title}")
    for text in warns:
        print(f"[warn] {text}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_edit_tools_215c() else 1)
