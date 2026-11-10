"""작업 207 검사 — ④ 프리비즈 편집기를 쉽게: 인물 직접 끌기 · 휠은 뷰포트를 누른 뒤만 · 방향키/[ ] · 이름표 · dirty 는 실제 상태. 유료·네트워크 0.

  (p-a) 정적: 인물 직접 끌기(pickAt actor → 바닥 광선 → moveActorTo · 놓으면 commit 한 번 · 회전 setActorFacing · 궤도 끔 · 커서)
  (p-b) 정적: 휠 포커스 가드(포커스 전엔 preventDefault 없이 돌아감 · orbit.enableZoom · 안내 글)
  (p-c) 정적: 방향키 0.1m / Shift 0.5m · [ ] 15° · 도움말 한 줄(작업 201 문구 그대로 + 덧붙임)
  (p-d) 정적: [이름표] 켜고 끄기(localStorage try/catch · setLabels · 인물 이름표는 그대로)
  (p-e) 정적: dirty = 정리한 JSON 비교(불러올 때·저장할 때 기준) · 떠날 때 확인도 같은 값
  (p-f) 정적: 기즈모 1.5배 · 맨 위에 그림(vendor depthTest 끔)
  (p-g) 고친 파일 LF
  (p-h) node --check vnew-previz.js · vnew.js(node 없으면 WARN)
  (p-i) node 실행: 인물 끌기 → pos·한 단계 · 빈 곳 끌기는 카메라 · 작업 시점 궤도 끔 · 포커스 없는 휠 → 카메라 그대로 ·
        ArrowRight 0.1m · [ ] · 이름표 · 되돌리면 dirty 풀림(node 없으면 WARN)

실행: python -X utf8 -B tools/check_previz_easy_207.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"

GROUPS = {
    "p-a": "인물 직접 끌기 경로(pickAt actor → 바닥 광선 → moveActorTo · commit 한 번)",
    "p-b": "휠 포커스 가드(포커스 전 preventDefault 없음)",
    "p-c": "방향키 · [ ] · 도움말 한 줄",
    "p-d": "[이름표] 켜고 끄기",
    "p-e": "dirty = 정리한 JSON 비교 · 떠날 때 확인도 같은 값",
    "p-f": "기즈모 1.5배 · 맨 위",
    "p-g": "고친 파일 LF",
    "p-h": "node --check 두 파일",
    "p-i": "node 실행: 끌기·휠·방향키·이름표·되돌리면 dirty 풀림",
}
THREE_LINE = 'import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";'
HELP_201 = '"Q 선택 · W 이동 · E 회전 · F 맞춰 보기 · 휠 확대 · Shift+드래그 이동 · Ctrl+Z 되돌리기"'
VNEW_FUNCS = ("previzNormJson", "previzEditSnapshot", "previzMarkSaved", "previzRefreshDirty", "previzConfirmLeave")


def _extract_js(source: str, name: str, indent: str = "  ") -> str:
    """function name(...) { ... } 원문(중괄호 짝 맞춤)."""
    m = re.search(r"\n" + indent + r"function\s+" + re.escape(name) + r"\s*\(", source)
    if not m:
        raise AssertionError(f"function {name} 이 없습니다")
    i = source.index("{", m.end())
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start() + 1: j + 1]
    raise AssertionError(f"function {name} 의 끝을 찾지 못했습니다")


def _body(source: str, name: str, indent: str = "  ") -> str:
    try:
        return _extract_js(source, name, indent)
    except AssertionError:
        return ""



_RUNNER = r"""
const THREE = await import(process.argv[2]);
class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.style = {}; this.children = []; this.listeners = {};
    this.className = ""; this.innerHTML = ""; this.textContent = ""; this.clientWidth = 1280; this.clientHeight = 720; this.parentNode = null;
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  get firstChild() { return this.children[0] || null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatch(t, ev) { (this.listeners[t] || []).forEach((f) => f(ev)); }
  focus() { globalThis.document.activeElement = this; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1280, height: 720 }; }
}
globalThis.window = globalThis;
globalThis.document = { createElement: (t) => new FakeEl(t), addEventListener() {}, removeEventListener() {}, visibilityState: "visible", activeElement: null };
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 4);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
class FakeTC extends THREE.EventDispatcher {
  constructor(cam) { super(); this.camera = cam; this.object = null; this.dragging = false; this.axis = null; this.size = 1; this.helper = new THREE.Object3D(); FakeTC.last = this; }
  getHelper() { return this.helper; } attach(o) { this.object = o; } detach() { this.object = null; } setMode() {} setSpace() {}
  setTranslationSnap() {} setRotationSnap() {} dispose() {}
}
class FakeOrbit extends THREE.EventDispatcher {
  constructor(cam) { super(); this.object = cam; this.target = new THREE.Vector3(); this.enabled = true; FakeOrbit.last = this; }
  update() {} dispose() {}
}
const mod = await import(new URL("./previz207.mjs", import.meta.url));

var previzDirty = false; var previzCtl = null; var PREVIZ_LEAVE_TEXT = "leave?";
var previzSaved = { scenes: null, snap: null };
var asked = 0;
globalThis.confirm = () => { asked += 1; return false; };
/*__VNEW_FUNCS__*/

function makeScene() {
  return {
    room: { width: 6, depth: 6, height: 3, walls: {} },
    actors: [{ id: "a", name: "A", heightM: 1.7, color: "#ff0000" }, { id: "b", name: "B", heightM: 1.7, color: "#00ff00" }],
    props: [{ id: "p1", labelKo: "상자", pos: [1.5, 0, -1.5], size: [0.5, 0.5, 0.5] }], light: {},
    timeline: [{ cutId: "c1", start: 0, end: 2, camera: { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 40 },
      actors: [{ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" }, { id: "b", pos: [2, 0, -2], facingDeg: 180, pose: "stand" }] }],
  };
}
async function make() {
  const sd = makeScene();
  const canvas = new FakeEl("canvas");
  const renderer = { domElement: canvas, shadowMap: {}, setPixelRatio() {}, setSize() {}, dispose() {}, render() {} };
  const ctl = mod.createController(new FakeEl("div"), { scenes: [sd], renderer, controls: { OrbitControls: FakeOrbit, TransformControls: FakeTC }, width: 1280, height: 720 });
  await ctl.ready();
  const ev = { live: 0, commit: 0 };
  ctl.onEdit((e) => { if (e.kind === "live") ev.live += 1; else if (e.kind === "commit") ev.commit += 1; });
  return { sd, ctl, E: ctl._editor, orbit: FakeOrbit.last, tc: FakeTC.last, c1: sd.timeline[0], ev };
}
function upd(E) { E.helpers.parent.updateMatrixWorld(true); E.shotCamera.updateMatrixWorld(true); E.workCamera.updateMatrixWorld(true); }
function scr(cam, x, y, z) { const v = new THREE.Vector3(x, y, z).project(cam); return [(v.x * 0.5 + 0.5) * 1280, (-v.y * 0.5 + 0.5) * 720]; }
function ptr(E, type, x, y) { E.canvas.dispatch(type, { clientX: x, clientY: y, button: 0, pointerId: 1, pointerType: "mouse", shiftKey: false, preventDefault() {} }); }
function dragFrom(E, x, y, dx, steps) { upd(E); ptr(E, "pointerdown", x, y); const mid = []; (steps || [5, dx]).forEach((s) => { ptr(E, "pointermove", x + s, y); mid.push(1); }); ptr(E, "pointerup", x + dx, y); }
function key(E, code, k, extra) { E.stage.dispatch("keydown", Object.assign({ code, key: k, target: E.stage, preventDefault() {} }, extra || {})); }
const r3 = (a) => a.map((x) => Math.round(x * 1000) / 1000);
const out = {};

// (w) 휠: 포커스 전엔 카메라 그대로·preventDefault 없음·안내 → 누른 뒤 앞뒤로 → blur 뒤 다시 그대로
{
  const { ctl, E, orbit, c1 } = await make();
  let pd = 0;
  const wheel = () => E.canvas.dispatch("wheel", { deltaY: 120, clientX: 640, clientY: 360, preventDefault() { pd += 1; } });
  const cam0 = JSON.stringify(c1.camera);
  wheel();
  out.wheel = { camSame: JSON.stringify(c1.camera) === cam0, pd, hint: E.wheelHint.style.display === "", hintText: E.wheelHint.textContent,
    zoom0: orbit.enableZoom, dirty0: ctl.canUndo };
  upd(E); ptr(E, "pointerdown", 20, 20); ptr(E, "pointerup", 20, 20);
  wheel();
  out.wheel.focusedMoves = JSON.stringify(c1.camera) !== cam0; out.wheel.pd1 = pd; out.wheel.zoom1 = orbit.enableZoom;
  E.stage.dispatch("blur", {});
  const cam1 = JSON.stringify(c1.camera);
  wheel();
  out.wheel.blurSame = JSON.stringify(c1.camera) === cam1; out.wheel.pd2 = pd; out.wheel.zoom2 = orbit.enableZoom;
  ctl.destroy();
}

// (d) 끌기: 카메라 시점 이동(W) → pos · manual · 카메라 그대로 · live 여러 번 · commit 1 · 되돌리기 한 단계
{
  const { ctl, E, tc, c1, ev } = await make();
  const a = c1.actors[0];
  ctl.setTool("translate");
  upd(E);
  const [sx, sy] = scr(E.shotCamera, 0, 1.0, 0);
  const hit = ctl.pickAt(sx, sy);
  const cam0 = JSON.stringify(c1.camera);
  const undo0 = ctl.canUndo;
  upd(E); ptr(E, "pointerdown", sx, sy); ptr(E, "pointermove", sx + 5, sy);
  const early = a.pos.slice();
  const cursorDrag = E.canvas.style.cursor;
  ptr(E, "pointermove", sx + 120, sy); ptr(E, "pointerup", sx + 120, sy);
  out.drag = { hit, sel: ctl.selection, undo0, movedX: a.pos[0] > 0.3, noJump: Math.abs(early[0]) < 0.05 && Math.abs(early[2]) < 0.05,
    zSame: Math.abs(a.pos[2]) < 0.05, manual: !!a.manual, otherManual: !!c1.actors[1].manual, camSame: JSON.stringify(c1.camera) === cam0,
    live: ev.live >= 2, commit: ev.commit, cursorDrag, gizmo: tc.size };
  const undone = ctl.undo();
  out.drag.undo = { undone, pos: r3(c1.actors[0].pos), manual: !!c1.actors[0].manual, canUndo: ctl.canUndo };
  // 회전(E): 오른쪽으로 40px = +20°
  ctl.setTool("rotate");
  ev.commit = 0;
  dragFrom(E, sx, sy, 40, [40]);
  out.drag.rotate = { facing: c1.actors[0].facingDeg, pos: r3(c1.actors[0].pos), commit: ev.commit };
  // 빈 곳 끌기 = 지금처럼 카메라 돌기
  ctl.setTool("translate");
  const cam1 = JSON.stringify(c1.camera);
  const pos1 = JSON.stringify(c1.actors[0].pos);
  dragFrom(E, 20, 20, 60);
  out.drag.empty = { camMoved: JSON.stringify(c1.camera) !== cam1, actorStill: JSON.stringify(c1.actors[0].pos) === pos1 };
  ctl.destroy();
}

// (o) 작업 시점: 인물 끄는 동안 궤도 끔 · 놓으면 켬 · 클릭(움직임 없음)은 고르기
{
  const { ctl, E, orbit, c1 } = await make();
  const b = c1.actors[1];
  ctl.setViewMode("work");
  ctl.setTool("select");
  upd(E);
  const [sx, sy] = scr(E.workCamera, 2, 1.0, -2);
  ptr(E, "pointerdown", sx, sy);
  const during = orbit.enabled;
  ptr(E, "pointermove", sx + 2, sy); ptr(E, "pointermove", sx + 80, sy);
  ptr(E, "pointerup", sx + 80, sy);
  out.work = { during, after: orbit.enabled, moved: JSON.stringify(b.pos) !== "[2,0,-2]", sel: ctl.selection, steps: 0 };
  while (ctl.undo()) out.work.steps += 1;
  ctl.select(null);
  upd(E);
  ptr(E, "pointerdown", sx, sy); ptr(E, "pointerup", sx + 1, sy);
  out.work.click = { sel: ctl.selection, pos: r3(c1.actors[1].pos), canUndo: ctl.canUndo };
  ctl.destroy();
}

// (k) 방향키·[ ] · 한 번 = 한 단계 · 고른 인물 없으면 그대로 · 입력 중 무시 / (l) 이름표
{
  const { ctl, E, c1 } = await make();
  const a = c1.actors[0];
  ctl.select({ type: "actor", id: "a" });
  key(E, "ArrowRight", "ArrowRight");
  const right = r3(a.pos);
  key(E, "ArrowUp", "ArrowUp", { shiftKey: true });
  const up = r3(a.pos);
  key(E, "BracketRight", "]");
  const turnR = a.facingDeg;
  key(E, "BracketLeft", "[");
  key(E, "BracketLeft", "[");
  const turnL = a.facingDeg;
  key(E, "ArrowLeft", "ArrowLeft", { target: new FakeEl("input") });
  const typed = r3(a.pos);
  let steps = 0;
  while (ctl.undo()) steps += 1;
  ctl.select(null);
  key(E, "ArrowRight", "ArrowRight");
  out.keys = { right, up, turnR, turnL, typed, steps, noSel: r3(c1.actors[0].pos), manual: !!c1.actors[0].manual };
  upd(E);
  ctl.setLabels(true);
  const on = E.overlay.innerHTML;
  ctl.setLabels(false);
  const off = E.overlay.innerHTML;
  out.labels = { onProp: on.includes("is-prop"), offProp: off.includes("is-prop") || off.includes("is-light"), offActor: off.includes("is-actor"),
    flag: ctl.labelsVisible };
  ctl.destroy();
}

// (u) dirty: 끌기 → dirty · 떠날 때 묻기 → 되돌리기 → dirty 풀림 · 묻지 않음 · 같은 값 다시 입력도 풀림
{
  const { sd, ctl, E, c1 } = await make();
  const scenes = [sd];
  previzMarkSaved(scenes);
  previzCtl = ctl;
  const a = c1.actors[0];
  ctl.setTool("translate");
  upd(E);
  const [sx, sy] = scr(E.shotCamera, 0, 1.0, 0);
  dragFrom(E, sx, sy, 100);
  const afterDrag = previzRefreshDirty();
  const leave1 = previzConfirmLeave();
  const asked1 = asked;
  ctl.undo();
  const afterUndo = previzRefreshDirty();
  const leave2 = previzConfirmLeave();
  previzMarkSaved(scenes);
  const a2 = c1.actors[0];
  const keep = a2.pos[2];
  a2.pos[2] = 1.25;
  const typedOther = previzRefreshDirty();
  a2.pos[2] = keep + 1e-9;
  const typedBack = previzRefreshDirty();
  out.dirty = { afterDrag, leave1, asked1, afterUndo, leave2, asked2: asked, typedOther, typedBack };
  ctl.destroy();
}
console.log(JSON.stringify(out));
"""



def _run_node(node: str, pjs: str, vjs: str) -> tuple[dict, str]:
    """편집기 하네스를 node 로 돌린다(60초). (결과, 오류)."""
    three = (PUBLIC / "vendor" / "three" / "three.module.min.js").resolve().as_uri()
    if THREE_LINE not in pjs:
        return {}, "vnew-previz.js 의 three import 줄을 찾지 못했습니다"
    funcs = "\n".join(_extract_js(vjs, n) for n in VNEW_FUNCS)
    work = Path(tempfile.mkdtemp(prefix="chk207-"))
    try:
        (work / "previz207.mjs").write_text(pjs.replace(THREE_LINE, f'import * as THREE from "{three}";')
                                            + "\nexport { createController };\n", encoding="utf-8", newline="\n")
        (work / "run.mjs").write_text(_RUNNER.replace("/*__VNEW_FUNCS__*/", funcs), encoding="utf-8", newline="\n")
        try:
            proc = subprocess.run([node, str(work / "run.mjs"), three], capture_output=True, text=True, encoding="utf-8", timeout=60)
        except subprocess.TimeoutExpired:
            return {}, "node 검사가 60초 안에 끝나지 않았습니다"
        lines = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0 or not lines:
            return {}, f"node 실패: {(proc.stderr or '').strip()[-300:]}"
        return json.loads(lines[-1]), ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_previz_easy_207() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-800:])

    pjs = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    css = (PUBLIC / "vnew.css").read_text(encoding="utf-8")
    node = shutil.which("node")

    def missing(src: str, needles: dict) -> list[str]:
        return [k for k, v in needles.items() if (v not in src if isinstance(v, str) else not all(x in src for x in v))]

    try:
        begin, move, end = _body(pjs, "beginActorDrag"), _body(pjs, "moveDraggedActor"), _body(pjs, "endActorDrag")
        ground = _body(pjs, "groundAt")
        down, up = _body(pjs, "onPointerDown"), _body(pjs, "onPointerUp")
        expect("p-a", [missing(begin, {"pickAt": "pickAt(event.clientX, event.clientY)", "actor": 'hit.type !== "actor"',
                                       "aim": "edit.aim", "grab": "drag.grab =", "orbit off": "orbit.enabled = false",
                                       "select": "select(hit)"}),
                       missing(ground, {"plane": "intersectPlane(groundPlane", "view cam": "rayFrom("}),
                       "viewCamera()" in _body(pjs, "rayFrom"),
                       missing(move, {"moveActorTo": "moveActorTo(cut, drag.actor, g.x + drag.grab[0], g.z + drag.grab[1])",
                                      "rotate": "setActorFacing(cut, drag.actor", "live": 'kind: "live"', "grabbing": '"grabbing"'}),
                       [end.count("commitEdit(cut)"), end.count('kind: "commit"'), "clickAt(" in end, "orbit.enabled =" in end],
                       ["recording" in down, "beginActorDrag(event)" in down, "endActorDrag(event, true)" in up,
                        "CLICK_SLOP_PX" in _body(pjs, "onPointerMove"), '"grab"' in _body(pjs, "hoverCursor")],
                       "const ACTOR_TURN_DEG_PER_PX = 0.5;" in pjs],
               [[], [], True, [], [1, 1, True, True], [True, True, True, True, True], True],
               "vnew-previz.js: pickAt actor → 바닥 광선 → moveActorTo(잡은 차이) · 놓으면 commit 한 번 · 회전 0.5°/px · 궤도 끔 · 커서")
    except Exception as exc:  # noqa: BLE001
        crash("p-a", exc)

    try:
        wheel = _body(pjs, "onWheel")
        guard = wheel.find("if (!edit.focused)")
        prevent = wheel.find("event.preventDefault")
        focus = _body(pjs, "setStageFocus")
        expect("p-b", [guard >= 0, 0 <= guard < prevent, "showWheelHint();\n      return;" in wheel[guard:prevent] if guard >= 0 else False,
                       "orbit.enableZoom = edit.focused" in focus, "orbit.enableZoom = false;" in pjs,
                       'stage.addEventListener("blur", onStageBlur)' in pjs, "setStageFocus(true)" in _body(pjs, "onPointerDown"),
                       'const WHEEL_HINT_TEXT = "화면을 한 번 누르면 휠로 확대·축소할 수 있습니다";' in pjs,
                       "const WHEEL_HINT_MS = 1500;" in pjs, "now - wheelHintAt < WHEEL_HINT_GAP_MS" in _body(pjs, "showWheelHint"),
                       ".vnew-previz-wheel-hint {" in css],
               [True] * 11, "휠: 포커스 전엔 preventDefault 전에 돌아감 · orbit.enableZoom 포커스 따라 · 안내 1.5초 · 몰아치기 막음 · CSS")
    except Exception as exc:  # noqa: BLE001
        crash("p-b", exc)

    try:
        nudge = _body(pjs, "nudgeKey")
        help_line = re.search(r'"vnew-hint vnew-previz-help", ("Q 선택[^"]*") \+\n\s*"([^"]*)"\)', vjs)
        expect("p-c", [missing(nudge, {"arrows": "NUDGE_ARROWS[code.toLowerCase()]", "brackets": ('"BracketLeft"', '"BracketRight"', '"["', '"]"'),
                                       "shift": "event.shiftKey ? NUDGE_BIG_M : NUDGE_M", "view cam": "viewCamera()",
                                       "horizontal": "fwd.y = 0", "one step": "applyOnce(", "move": "moveActorTo(cut, id",
                                       "turn": "setActorFacing(cut, id"}),
                       "nudgeKey(event, code, key)" in _body(pjs, "handleKey"),
                       ["const NUDGE_M = 0.1;" in pjs, "const NUDGE_BIG_M = 0.5;" in pjs, "const NUDGE_DEG = 15;" in pjs],
                       HELP_201 in vjs, bool(help_line) and "방향키" in help_line.group(2) and "[ ]" in help_line.group(2)
                       and len(help_line.group(1)) + len(help_line.group(2)) < 120],
               [[], True, [True, True, True], True, True],
               "방향키 0.1m / Shift 0.5m(보는 카메라 수평) · [ ] 15° · 키 한 번 = 한 단계 · 도움말 한 줄(201 문구 + 덧붙임)")
    except Exception as exc:  # noqa: BLE001
        crash("p-c", exc)

    try:
        on_fn, set_fn = _body(vjs, "previzLabelsOn"), _body(vjs, "previzSetLabelsPref")
        overlay = _body(pjs, "drawOverlay")
        expect("p-d", ["try {" in on_fn and "localStorage.getItem(PREVIZ_LABELS_KEY)" in on_fn,
                       "try {" in set_fn and "localStorage.setItem(PREVIZ_LABELS_KEY" in set_fn,
                       'el("span", null, "이름표")' in vjs, "previzCtl.setLabels(labelsBox.checked)" in vjs,
                       "previzCtl.setLabels(previzLabelsOn())" in vjs, 'if (!edit.labelsOn && item.type !== "actor") return;' in overlay,
                       "labelsOn: true," in pjs],
               [True] * 7, "[이름표]: localStorage(try/catch) · 도구 줄 · 켜진 채 시작 · 인물 이름표는 그대로")
    except Exception as exc:  # noqa: BLE001
        crash("p-d", exc)

    try:
        refresh, leave = _body(vjs, "previzRefreshDirty"), _body(vjs, "previzConfirmLeave")
        expect("p-e", ["Object.keys(value).sort()" in _body(vjs, "previzNormJson"),
                       "previzEditSnapshot(previzSaved.scenes) !== previzSaved.snap" in refresh,
                       "previzMarkSaved(scenes); // 새로 그린 장면은 서버 저장본이다" in vjs,
                       "previzRefreshDirty()" in leave, "if (previzCtl && previzRefreshDirty()) {" in vjs,
                       "previzRefreshDirty();" in _body(vjs, "markDirty", "    "), "previzDirty = true" not in vjs,
                       "previzRefreshDirty();\n    if (previzDirty) {" in vjs],
               [True] * 8, "dirty = 정리한 JSON ≠ 불러온·저장한 기준 · 떠날 때·창 닫을 때·녹화 막기 모두 같은 값")
    except Exception as exc:  # noqa: BLE001
        crash("p-e", exc)

    try:
        tc = (PUBLIC / "vendor" / "three" / "addons" / "TransformControls.js").read_text(encoding="utf-8")
        expect("p-f", ["const GIZMO_SIZE = 1.5;" in pjs, "gizmo.setSize(GIZMO_SIZE)" in pjs, "depthTest: false" in tc,
                       "renderOrder = Infinity" in tc], [True] * 4, "기즈모 1.5배 · vendor 기즈모는 depthTest 끔·renderOrder 맨 위")
    except Exception as exc:  # noqa: BLE001
        crash("p-f", exc)

    files = [PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js", PUBLIC / "vnew.css", TOOLS / "check_previz_easy_207.py"]
    expect("p-g", [p.name for p in files if b"\r" in p.read_bytes()], [], "고친 파일 LF")

    try:
        if not node:
            print("[WARN] node 가 없어 작업 207 node --check(p-h)를 건너뜁니다")
            seen.add("p-h")
        else:
            for path in (PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js"):
                proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
                expect("p-h", [path.name, proc.returncode, (proc.stderr or "")[-300:]], [path.name, 0, ""], "node --check")
    except Exception as exc:  # noqa: BLE001
        crash("p-h", exc)

    try:
        if not node:
            print("[WARN] node 가 없어 작업 207 편집기 실행 검사(p-i)를 건너뜁니다")
            seen.add("p-i")
        else:
            got, err = _run_node(node, pjs, vjs)
            if err:
                expect("p-i", err, "", "node 하네스")
            else:
                _expect_node(expect, got)
    except Exception as exc:  # noqa: BLE001
        crash("p-i", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 207 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 207 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 207 ({group}) {title}")
    return passed



def _expect_node(expect, got: dict) -> None:
    w = got["wheel"]
    expect("p-i", w, {"camSame": True, "pd": 0, "hint": True, "hintText": "화면을 한 번 누르면 휠로 확대·축소할 수 있습니다", "zoom0": False,
                      "dirty0": False, "focusedMoves": True, "pd1": 1, "zoom1": True, "blurSame": True, "pd2": 1, "zoom2": False},
           "휠: 누르기 전 카메라 그대로·preventDefault 없음·안내 → 누른 뒤 앞뒤로 → blur 뒤 다시 그대로(작업 시점 enableZoom 도)")
    d = got["drag"]
    d.setdefault("cursorDrag", None)
    expect("p-i", {k: d[k] for k in ("hit", "sel", "undo0", "movedX", "noJump", "zSame", "manual", "otherManual", "camSame", "live",
                                     "commit", "cursorDrag", "gizmo")},
           {"hit": {"type": "actor", "id": "a"}, "sel": {"type": "actor", "id": "a"}, "undo0": False, "movedX": True, "noJump": True,
            "zSame": True, "manual": True, "otherManual": False, "camSame": True, "live": True, "commit": 1, "cursorDrag": "grabbing",
            "gizmo": 1.5},
           "카메라 시점 인물 끌기: 고름 · 바닥에서 옆으로(튀지 않음) · 그 인물만 manual · 카메라 그대로 · live 여러 번 · commit 1 · 기즈모 1.5")
    expect("p-i", d["undo"], {"undone": True, "pos": [0, 0, 0], "manual": False, "canUndo": False}, "끌기 = 되돌리기 한 단계")
    expect("p-i", [d["rotate"], d["empty"]], [{"facing": 20, "pos": [0, 0, 0], "commit": 1}, {"camMoved": True, "actorStill": True}],
           "회전(E) 끌기 40px = +20° 한 단계 · 빈 곳 끌기는 지금처럼 카메라")
    expect("p-i", got["work"], {"during": False, "after": True, "moved": True, "sel": {"type": "actor", "id": "b"}, "steps": 1,
                                "click": {"sel": {"type": "actor", "id": "b"}, "pos": [2, 0, -2], "canUndo": False}},
           "작업 시점: 끄는 동안 궤도 끔·놓으면 켬 · 한 단계 · 클릭은 고르기만")
    expect("p-i", got["keys"], {"right": [0.1, 0, 0], "up": [0.1, 0, -0.5], "turnR": 15, "turnL": -15, "typed": [0.1, 0, -0.5], "steps": 5,
                                "noSel": [0, 0, 0], "manual": False},
           "ArrowRight 0.1m · Shift+ArrowUp 0.5m 앞 · ] +15° · [ −15° · 키 한 번 = 한 단계 · 입력 중·안 고름 무시")
    expect("p-i", got["labels"], {"onProp": True, "offProp": False, "offActor": True, "flag": False}, "이름표 끔 = 소품·빛만 숨김")
    expect("p-i", got["dirty"], {"afterDrag": True, "leave1": False, "asked1": 1, "afterUndo": False, "leave2": True, "asked2": 1,
                                 "typedOther": True, "typedBack": False},
           "끌기 → dirty · 떠날 때 물음 → 되돌리면 dirty 풀림·묻지 않음 · 같은 값 다시 입력도 풀림")


if __name__ == "__main__":
    ok = check_previz_easy_207()
    print('hook (check_vpoc.py main, 작업 206 다음 줄): if not __import__("check_previz_easy_207").check_previz_easy_207(): '
          'fail("작업 207: 프리비즈 직접 끌기 · 휠 포커스 · 방향키 · 이름표 · dirty 검사 실패")')
    sys.exit(0 if ok else 1)
