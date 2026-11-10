"""작업 213: ④ 프리비즈 포즈 쓰기 편하게 — 스크러버 vs 키 마름모 · 고리 겹침 · 고를 때 화면 밀림(실제 마우스 시험에서 찾은 것).

  (u-a) 정적: 밀림 없음 — 글이 바뀌는 칸(조준 줄 안내 · 키 줄 안내 · 자세 키 안내)은 한 줄 고정 높이 · 마름모 줄 높이 고정 ·
        고를 때 scrollIntoView(페이지까지 굴림) 대신 패널 안만 굴림(previzScrollPanelTo)
  (u-b) 정적: 마름모 = 트랙 위 얇은 줄(스크러버 앞) · 누른 채 CLICK_SLOP 넘게 움직이면 스크러버 끌기(window 에서 받음) ·
        그냥 누르면 그 키로 · 마우스 click 은 무시(키보드 Enter·Space 만) · 키 줄 Home / End
  (u-c) 정적: 관절 고르기 판(몸 모양 격자 16개 = 편집기 POSE_PICK_ORDER) · 포즈 모드 패널에 · 누르면 selectJoint · CSS
  (u-d) 정적: 고리 40% 얇게(0.008 → 0.0048) · 반지름 = 팔다리 굵기 × 1.3 · 고른 것만 진하게(나머지 0.35) · 커서 아래 밝게 + 이름표 ·
        겹치면 카메라에 가까운 것 · 같은 자리 다시 누르면 다음 · Tab / Shift+Tab
  (u-e) 고친 파일 LF
  (u-f) node --check vnew-previz.js · vnew.js (node 없으면 WARN)
  (u-g) node DOM(vnew.js 키 줄·도구 줄 그대로 · 가짜 DOM 의 쌓기 배치 모형): 고르기·풀기·포즈 모드에서 도구 줄·뷰포트·스크러버·키 줄
        getBoundingClientRect().top 그대로 · 구조 그대로 · 마름모 끌기 = 스크러버 · 누르기 = 그 키 · Home / End · 패널만 굴림
  (u-h) node 편집기(뒤돌아선 인물): 고리 굵기·반지름 · 불투명도 · 커서 아래 밝게 + 이름표 · 겹친 자리 가까운 순 + 다시 누르면 다음 ·
        끌기는 고른 것 그대로 · Tab / Shift+Tab · 손(IK) = 손목 + 네모 밝힘

실행: python -X utf8 -B tools/check_previz_pose_ux_213.py
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
    "u-a": "밀림 없음: 한 줄 고정 칸 · 마름모 줄 높이 고정 · 패널 안만 굴림",
    "u-b": "마름모 줄(트랙 위) · 끌면 스크러버 · 누르면 그 키 · Home / End",
    "u-c": "관절 고르기 판(16개 · 편집기 순서 · selectJoint)",
    "u-d": "고리: 얇게 · 팔다리 비례 · 고른 것만 진하게 · 밝힘·이름표 · 가까운 것 · 다시 누르면 다음 · Tab",
    "u-e": "고친 파일 LF",
    "u-f": "node --check 두 파일",
    "u-g": "node DOM: 고르기·풀기·포즈에도 top 그대로 · 마름모 끌기 · Home/End · 패널만 굴림",
    "u-h": "node 편집기: 고리 · 불투명도 · 밝힘·이름표 · 겹침 순환 · Tab",
}
THREE_LINE = 'import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";'
PICK_ORDER = ["head", "neck", "spine", "shoulderL", "shoulderR", "elbowL", "elbowR", "wristL", "wristR",
              "root", "hipL", "hipR", "kneeL", "kneeR", "ankleL", "ankleR"]
FIXED_SLOTS = ["vnew-previz-caminfo", "vnew-previz-keys-note", "vnew-previz-posekeys-hint"]
VNEW_TOP = ("el", "clear", "previzScrollPanelTo")
VNEW_NESTED = ("toolButton", "previzEditorBar", "previzKeyStrip")
VNEW_VARS = ("PREVIZ_LENS_PRESETS", "PREVIZ_KEY_CLICK_SLOP_PX", "PREVIZ_SCRUB_THUMB_INSET_PX")


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


def _css_rule(css: str, selector: str) -> str:
    """선택자 목록에 selector 가 (그대로) 들어 있는 규칙 본문들을 이어 붙인다."""
    out = []
    for m in re.finditer(r"(^|\n)([^{}@/]+?)\{([^}]*)\}", css):
        sels = [s.strip() for s in m.group(2).split(",")]
        if selector in sels:
            out.append(m.group(3))
    return "\n".join(out)


def _fixed_heights(css: str) -> dict:
    """클래스 하나짜리 선택자에 height: Npx 가 있으면 {클래스: N} (min-height 는 아님)."""
    out: dict = {}
    for m in re.finditer(r"(^|\n)([^{}@/]+?)\{([^}]*)\}", css):
        h = re.search(r"(?:^|\n)\s*height:\s*(\d+)px;", m.group(3))
        if not h:
            continue
        for s in m.group(2).split(","):
            s = s.strip()
            if re.fullmatch(r"\.[a-z0-9-]+", s):
                out[s[1:]] = int(h.group(1))
    return out


_DOM_RUNNER = r"""
const FIXED = JSON.parse(process.argv[2]);
const winL = {};
let pageScrolls = 0;
globalThis.window = globalThis;
globalThis.addEventListener = (t, f) => { (winL[t] = winL[t] || []).push(f); };
globalThis.removeEventListener = (t, f) => { winL[t] = (winL[t] || []).filter((x) => x !== f); };
globalThis.scrollTo = () => { pageScrolls += 1; };
globalThis.scrollBy = () => { pageScrolls += 1; };
const winFire = (t, ev) => (winL[t] || []).slice().forEach((f) => f(ev));
class El {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.parentNode = null; this.style = {}; this.listeners = {};
    this._cls = new Set(); this._text = ""; this.attrs = {}; this.disabled = false; this.title = ""; this.value = ""; this.checked = false; this.scrollTop = 0; }
  get className() { return [...this._cls].join(" "); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() { const s = this._cls; return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c),
    toggle: (c, on) => { const w = on === undefined ? !s.has(c) : !!on; if (w) s.add(c); else s.delete(c); return w; } }; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children.forEach((c) => { c.parentNode = null; }); this.children = []; this._text = String(v); }
  get firstChild() { return this.children[0] || null; }
  get childNodes() { return this.children; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); this.children.push(c); c.parentNode = this; return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatch(t, ev) { (this.listeners[t] || []).slice().forEach((f) => f(ev)); let p = this.parentNode;
    while (p && !(ev && ev._stop)) { (p.listeners[t] || []).slice().forEach((f) => f(ev)); p = p.parentNode; } }
  find(cls) { if (this._cls.has(cls)) return this; for (const c of this.children) { const r = c.find(cls); if (r) return r; } return null; }
  findAll(cls, acc) { acc = acc || []; if (this._cls.has(cls)) acc.push(this); this.children.forEach((c) => c.findAll(cls, acc)); return acc; }
  _fixed() { let h = null; for (const c of this._cls) if (FIXED[c] !== undefined) h = Math.max(h === null ? 0 : h, FIXED[c]); return h; }
  // 쌓기 배치 모형: 높이 = 고정 높이 칸(CSS height) · 아니면 자식 높이 합 · 글만 있으면 40자마다 한 줄(18px).
  _h() { const f = this._fixed(); if (f !== null) return f; if (this.style.display === "none") return 0;
    if (this.children.length) return this.children.reduce((a, c) => a + c._h(), 0);
    return this._text.length ? 18 * Math.ceil(this._text.length / 40) : (this.tagName === "INPUT" ? 20 : 0); }
  _top() { if (!this.parentNode) return 0; let y = this.parentNode._top(); for (const s of this.parentNode.children) { if (s === this) break; y += s._h(); } return y; }
  getBoundingClientRect() { const top = this._top(); const h = this._h(); const left = this._left || 0; const width = this._width || 1000;
    return { left, width, right: left + width, top, height: h, bottom: top + h }; }
}
globalThis.document = { createElement: (t) => new El(t), activeElement: null };
function previzLabelsOn() { return true; }
function previzSetLabelsPref() {}
let previzCtl = null;
/*__VNEW__*/
const r3 = (x) => Math.round(x * 1000) / 1000;
const calls = [];
const S = { sel: null, pose: false, keys: [], live: false, u: 0 };
const ctl = {
  get poseMode() { return S.pose; },
  state() { return { view: "shot", tool: "select", snap: false, canUndo: false, canRedo: false, aim: false, pose: S.pose,
    selection: S.sel, poseKeys: S.sel ? { keys: S.keys, ghosts: S.keys.length >= 2 } : null }; },
  cameraInfo() { return { lensSet: false, lensMm: 35, fov: 40, heightM: 1.5, hasEndKey: false, editingEnd: false, keyU: S.u }; },
  poseKeyInfo() { return S.sel ? { id: "a", keys: S.keys.slice(), selected: null, onKey: S.keys.indexOf(S.u) >= 0, live: S.live, u: S.u, max: 8, msg: "",
    hint: S.live ? "키 찍기(S)를 눌러 이 시점에 저장하세요" : "" } : null; },
  scrubKey(u) { calls.push(["scrub", r3(u)]); S.u = u; },
  jumpToPoseKey(id, u) { calls.push(["jump", id, u]); S.u = u; },
  setKey() {}, clearEndKey() {}, gotoPoseKey() {}, deletePoseKey() {}, setGhosts() {}, setSnap() {}, setLabels() {},
};
previzCtl = ctl;
const bar = previzEditorBar();
const keys = previzKeyStrip();
const left = new El("div"); left.className = "vnew-previz-left";
left.appendChild(bar.root);
const viewport = left.appendChild(new El("div")); viewport.className = "vnew-previz-viewport";
const transport = left.appendChild(new El("div")); transport.className = "vnew-previz-transport";
left.appendChild(keys.root);
const scrub = keys.root.find("vnew-previz-keys-scrub");
const track = keys.root.find("vnew-previz-keys-track");
function skel(e) { const cls = [...e._cls].filter((c) => !c.startsWith("is-")).sort().join(".");
  if (e._fixed() !== null) return "[" + e.tagName + "." + cls + "]"; return e.tagName + "." + cls + "(" + e.children.map(skel).join(",") + ")"; }
function texts(e, path, acc) { if (e._fixed() !== null) return acc; if (!e.children.length) acc[path] = e._text;
  e.children.forEach((c, i) => texts(c, path + "/" + i, acc)); return acc; }
function snap(name) { bar.sync(ctl.state()); keys.sync();
  return { name, tops: { toolbar: r3(bar.root.getBoundingClientRect().top), aim: r3(bar.root.children[1].getBoundingClientRect().top),
    viewport: r3(viewport.getBoundingClientRect().top), scrub: r3(scrub.getBoundingClientRect().top), keys: r3(keys.root.getBoundingClientRect().top),
    poseRow: r3(keys.root.find("vnew-previz-posekeys").getBoundingClientRect().top), below: r3(keys.root.getBoundingClientRect().bottom) },
    skel: skel(left), texts: texts(left, "", {}), info: bar.root.find("vnew-previz-caminfo").textContent,
    hint: keys.root.find("vnew-previz-posekeys-hint").textContent }; }
const states = [snap("none")];
S.sel = { type: "actor", id: "a" }; states.push(snap("actor"));
S.keys = [0, 0.5]; S.pose = true; S.live = true; S.u = 0.25; states.push(snap("pose+keys+live"));
S.pose = false; S.live = false; S.u = 0; states.push(snap("keys"));
S.sel = null; S.keys = []; states.push(snap("deselect"));
const base = states[0];
const textMoved = [];
states.forEach((s) => Object.keys(s.texts).forEach((p) => { if (s.texts[p] !== base.texts[p]) textMoved.push(s.name + ":" + p); }));
const out = { layout: { tops: states.map((s) => s.tops), skelSame: states.every((s) => s.skel === base.skel), textMoved,
  infoChanged: states[2].info !== base.info, hintChanged: states[1].hint !== base.hint,
  diamondsFirst: track.children[0] && track.children[0]._cls.has("vnew-previz-keys-diamonds"), scrubSecond: track.children[1] === scrub } };
// 마름모: 누른 채 끌기 = 스크러버 · 누르기 = 그 키 · 마우스 click 무시 · 키보드 click = 그 키
S.sel = { type: "actor", id: "a" }; S.keys = [0, 0.5]; S.u = 0; keys.sync();
scrub._left = 100; scrub._width = 416; // 손잡이 반폭 8px 안쪽 = 400px
const dia = () => keys.root.findAll("vnew-previz-key-diamond").filter((d) => d._cls.has("is-pose"));
const ev = (x, y, extra) => Object.assign({ clientX: x, clientY: y, button: 0, detail: 1, preventDefault() {}, stopPropagation() { this._stop = true; } }, extra || {});
const d = {};
calls.length = 0;
let dm = dia()[0];
dm.dispatch("pointerdown", ev(108, 5));
winFire("pointermove", ev(110, 5)); // 2px = 아직 누르기
d.beforeSlop = calls.slice();
winFire("pointermove", ev(308, 6));
keys.sync(); // 실제로는 scrubKey 가 state 를 내 마름모를 다시 그린다
winFire("pointermove", ev(600, 6));
winFire("pointerup", ev(600, 6));
dm.dispatch("click", ev(600, 6));
d.drag = calls.slice(); d.scrubValue = scrub.value; d.listenersLeft = (winL.pointermove || []).length + (winL.pointerup || []).length;
calls.length = 0; S.u = 0; keys.sync();
dm = dia()[1];
dm.dispatch("pointerdown", ev(308, 5));
winFire("pointermove", ev(310, 6)); // slop 안
winFire("pointerup", ev(310, 6));
dm.dispatch("click", ev(310, 6));
d.click = calls.slice();
calls.length = 0; keys.sync();
dia()[1].dispatch("click", ev(0, 0, { detail: 0 }));
d.keyboard = calls.slice();
calls.length = 0;
keys.root.dispatch("keydown", { key: "End", target: scrub, preventDefault() {} });
keys.root.dispatch("keydown", { key: "Home", target: scrub, preventDefault() {} });
keys.root.dispatch("keydown", { key: "a", target: scrub, preventDefault() {} });
d.homeEnd = calls.slice();
out.diamond = d;
// 패널 안만 굴림
const panel = { scrollTop: 300, getBoundingClientRect: () => ({ top: 100, bottom: 600 }) };
previzScrollPanelTo(panel, { getBoundingClientRect: () => ({ top: 40, bottom: 240 }) });
const up = panel.scrollTop;
previzScrollPanelTo(panel, { getBoundingClientRect: () => ({ top: 500, bottom: 700 }) });
const down = panel.scrollTop;
previzScrollPanelTo(panel, { getBoundingClientRect: () => ({ top: 200, bottom: 300 }) });
out.scroll = { up, down, still: panel.scrollTop, page: pageScrolls };
console.log(JSON.stringify(out));
"""

_EDITOR_RUNNER = r"""
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
const mod = await import(new URL("./previz213.mjs", import.meta.url));
class FakeTC extends THREE.EventDispatcher {
  constructor(cam) { super(); this.camera = cam; this.object = null; this.dragging = false; this.axis = null; this.helper = new THREE.Object3D(); }
  getHelper() { return this.helper; } attach(o) { this.object = o; } detach() { this.object = null; } setMode() {} setSpace() {}
  setTranslationSnap() {} setRotationSnap() {} setSize() {} dispose() {}
}
class FakeOrbit extends THREE.EventDispatcher {
  constructor(cam) { super(); this.object = cam; this.target = new THREE.Vector3(); this.enabled = true; }
  update() {} dispose() {}
}
const sd = { room: { width: 8, depth: 8, height: 3, walls: {} }, actors: [{ id: "a", name: "A", heightM: 1.7, color: "#ff0000" }], props: [], light: {},
  timeline: [{ cutId: "c1", start: 0, end: 2, camera: { pos: [0, 1.5, 4], target: [0, 1.1, 0], fov: 40 },
    actors: [{ id: "a", pos: [0, 0, 0], facingDeg: 180, pose: "stand" }] }] }; // 뒤돌아선 인물(실제 시험 상황)
const renderer = { domElement: new FakeEl("canvas"), shadowMap: {}, setPixelRatio() {}, setSize() {}, dispose() {}, render() {} };
const ctl = mod.createController(new FakeEl("div"), { scenes: [sd], renderer, controls: { OrbitControls: FakeOrbit, TransformControls: FakeTC }, width: 1280, height: 720 });
await ctl.ready();
const E = ctl._editor;
const r4 = (x) => Math.round(x * 10000) / 10000;
const upd = () => { E.helpers.parent.updateMatrixWorld(true); E.shotCamera.updateMatrixWorld(true); };
const scr = (v) => { const p = v.clone().project(E.shotCamera); return [(p.x * 0.5 + 0.5) * 1280, (-p.y * 0.5 + 0.5) * 720]; };
const ptr = (type, x, y, extra) => E.canvas.dispatch(type, Object.assign({ clientX: x, clientY: y, button: 0, pointerId: 1, pointerType: "mouse",
  shiftKey: false, altKey: false, preventDefault() {} }, extra || {}));
const click = (x, y) => { upd(); ptr("pointerdown", x, y); ptr("pointerup", x, y); return ctl.poseJoint; };
const key = (code, shift) => ctl.handleKey({ code, key: code, shiftKey: !!shift, target: E.stage, preventDefault() {} });
const out = {};
let commits = 0;
ctl.onEdit((e) => { if (e.kind === "commit") commits += 1; });
ctl.select({ type: "actor", id: "a" });
out.tabOff = { done: key("Tab"), joint: ctl.poseJoint };
ctl.setPoseMode(true);
upd();
const rings = E.poseControls.rings;
const pickTube = (r) => r.mesh.children[0].geometry.parameters.tube;
out.rings = { radius: Object.fromEntries(rings.map((r) => [r.joint, r4(r.mesh.geometry.parameters.radius)])),
  tube: [...new Set(rings.map((r) => r4(r.mesh.geometry.parameters.tube)))], pickElbow: r4(pickTube(rings.find((r) => r.joint === "elbowL"))),
  pickHead: r4(pickTube(rings.find((r) => r.joint === "head"))), dimAll: rings.every((r) => r.mat.opacity === 0.35) };
ctl.selectJoint("neck");
const sel = 0xffd54f;
out.opacity = { neck: rings.find((r) => r.joint === "neck").mat.opacity, neckSel: rings.find((r) => r.joint === "neck").mat.color.getHex() === sel,
  others: [...new Set(rings.filter((r) => r.joint !== "neck").map((r) => r.mat.opacity))] };
ctl.selectJoint("wristL");
const hh = (n) => E.poseControls.handles.find((h) => h.name === n).mat.color.getHex() === sel;
out.ik = { joint: ctl.poseJoint, handL: hh("handL"), handR: hh("handR") };
// 겹친 자리 찾기(뒤돌아선 인물 · 5px 격자)
let xs = [], ys = [];
rings.forEach((r) => { const [x, y] = scr(r.mesh.position); xs.push(x); ys.push(y); });
let best = null, overlapPts = 0;
for (let y = Math.min(...ys) - 30; y <= Math.max(...ys) + 30; y += 5) {
  for (let x = Math.min(...xs) - 30; x <= Math.max(...xs) + 30; x += 5) {
    const c = E.posePickAll(x, y);
    if (c.length >= 2) overlapPts += 1;
    if (c.length >= 2 && (!best || c.length > best.c.length)) best = { x, y, c };
  }
}
out.overlapPts = overlapPts;
if (best) {
  const IKJ = { handL: "elbowL", handR: "elbowR", footL: "kneeL", footR: "kneeR" };
  const jointOf = (c) => c.kind === "fk" ? c.name : c.kind === "root" ? "root" : IKJ[c.name];
  const cands = best.c;
  ctl.setPoseMode(false); ctl.setPoseMode(true); upd();
  const seq = [];
  for (let i = 0; i <= cands.length; i++) seq.push(click(best.x, best.y));
  const want = cands.map(jointOf).concat([jointOf(cands[0])]);
  const atEnd = ctl.poseJoint;
  const c0 = commits;
  upd(); ptr("pointerdown", best.x, best.y); ptr("pointermove", best.x, best.y + 10); ptr("pointermove", best.x, best.y + 20); ptr("pointerup", best.x, best.y + 20);
  const dragKeep = ctl.poseJoint === atEnd && commits === c0 + 1;
  ctl.undo(); upd();
  const after = click(best.x, best.y); // 끈 뒤 = 처음부터(가장 가까운 것)
  ptr("pointermove", best.x, best.y); // 같은 자리 = 다음에 고를 것을 밝힌다
  const hover = E.poseHover;
  const tip = E.poseTip.textContent;
  const tipShown = E.poseTip.style.display !== "none";
  ptr("pointerleave", -1, -1);
  out.cycle = { n: cands.length, sorted: cands.every((c, i) => i === 0 || cands[i - 1].dist <= c.dist), seqOk: JSON.stringify(seq) === JSON.stringify(want),
    seq, want, dragKeep, after: after === want[0], hoverNext: hover === cands[1 % cands.length].name, tipCount: tip.includes("겹침 2/" + cands.length),
    tipShown, leaveHidden: E.poseTip.style.display === "none" && E.poseHover === null };
}
// 커서 아래 밝게 + 이름표(겹치지 않는 자리 하나)
ctl.setPoseMode(false); ctl.setPoseMode(true); upd();
let solo = null;
for (const r of rings) {
  for (let a = 0; a < 16 && !solo; a++) {
    const p = r.mesh.localToWorld(new THREE.Vector3(Math.cos(a / 16 * Math.PI * 2) * r.mesh.geometry.parameters.radius, Math.sin(a / 16 * Math.PI * 2) * r.mesh.geometry.parameters.radius, 0));
    const [x, y] = scr(p);
    const c = E.posePickAll(x, y);
    if (c.length === 1 && c[0].name === r.joint) solo = { x, y, joint: r.joint };
  }
  if (solo) break;
}
if (solo) {
  ptr("pointermove", solo.x, solo.y);
  const ring = rings.find((r) => r.joint === solo.joint);
  out.hover = { joint: solo.joint, hover: E.poseHover, opacity: ring.mat.opacity, color: ring.mat.color.getHex() === 0xffffff,
    tip: E.poseTip.textContent, names: Object.fromEntries(ctl.poseJointList().map((j) => [j.id, j.nameKo])) };
  ptr("pointerleave", -1, -1);
}
// Tab / Shift+Tab
ctl.setPoseMode(false); ctl.setPoseMode(true);
const tabs = [];
key("Tab"); tabs.push(ctl.poseJoint); key("Tab"); tabs.push(ctl.poseJoint); key("Tab", true); tabs.push(ctl.poseJoint);
key("Tab", true); tabs.push(ctl.poseJoint); key("Tab"); tabs.push(ctl.poseJoint);
out.tabs = { seq: tabs, done: key("Tab") };
out.list = ctl.poseJointList().map((j) => j.id);
console.log(JSON.stringify(out));
process.exit(0);
"""


def _node(node: str, work: Path, script: str, args: list[str]) -> tuple[dict, str]:
    (work / "run.mjs").write_text(script, encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run([node, str(work / "run.mjs"), *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    except subprocess.TimeoutExpired:
        return {}, "node 검사가 120초 안에 끝나지 않았습니다"
    lines = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not lines:
        return {}, f"node 실패: {(proc.stderr or '').strip()[-500:]}"
    return json.loads(lines[-1]), ""


def _vnew_funcs(vjs: str) -> str:
    parts = [_extract_js(vjs, n, "  ") for n in VNEW_TOP] + [_extract_js(vjs, n, "    ") for n in VNEW_NESTED]
    for name in VNEW_VARS:
        m = re.search(r"\n  var " + name + r" = [^\n]*;", vjs)
        if not m:
            raise AssertionError(f"var {name} 이 없습니다")
        parts.append(m.group(0).strip())
    return "\n".join(parts)


def check_previz_pose_ux_213() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-600:])

    def missing(src: str, needles: dict) -> list[str]:
        return [k for k, v in needles.items() if (v not in src if isinstance(v, str) else not all(x in src for x in v))]

    pjs = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    css = (PUBLIC / "vnew.css").read_text(encoding="utf-8")
    node = shutil.which("node")

    try:  # (u-a)
        slot = _css_rule(css, ".vnew-previz-caminfo")
        rows = _css_rule(css, ".vnew-previz-keys-diamonds")
        on_sel = vjs[vjs.find("previzCtl.onSelect(function (sel) {"):][:400]
        expect("u-a", [[c for c in FIXED_SLOTS if not (re.search(r"(?:^|\n)\s*height: 18px;", _css_rule(css, "." + c)) and all(
                         x in _css_rule(css, "." + c) for x in ("white-space: nowrap;", "overflow: hidden;", "text-overflow: ellipsis;")))],
                       "min-width: 0;" in _css_rule(css, ".vnew-previz-posekeys-hint") and "min-width: 0;" in slot,
                       "height: 23px;" in rows and "position: relative;" in rows,
                       "card.scrollIntoView(" in on_sel, "previzScrollPanelTo(right, card);" in on_sel,
                       "panel.scrollTop +=" in _body(vjs, "previzScrollPanelTo") and "scrollIntoView" not in _body(vjs, "previzScrollPanelTo"),
                       missing(vjs, {"titles": ("info.title = info.textContent;", "poseHint.title = poseHint.textContent;")})],
               [[], True, True, False, True, True, []],
               "글이 바뀌는 칸 3개 = 한 줄 고정 높이(…) · 마름모 줄 23px · 고를 때 페이지 대신 패널 안만 굴림 · 전체 글은 툴팁")
    except Exception as exc:  # noqa: BLE001
        crash("u-a", exc)

    try:  # (u-b)
        strip = _body(vjs, "previzKeyStrip", "    ")
        dia = _body(strip, "diamond", "      ")
        expect("u-b", [strip.find("scrubWrap.appendChild(diamonds);") < strip.find("scrubWrap.appendChild(scrub);"),
                       missing(dia, {"down": 'd.addEventListener("pointerdown"', "window": ('window.addEventListener("pointermove", onDiamondMove)',
                                     'window.addEventListener("pointerup", onDiamondUp)'), "keyboard only": "if (event && event.detail) return;"}),
                       missing(_body(strip, "onDiamondMove", "      "), {"slop": "< PREVIZ_KEY_CLICK_SLOP_PX", "scrub": "previzCtl.scrubKey(u)"}),
                       "if (d && !d.moved && previzCtl) d.onClick();" in _body(strip, "onDiamondUp", "      "),
                       missing(strip, {"home/end": ('k !== "Home" && k !== "End"', 'previzCtl.scrubKey(k === "Home" ? 0 : 1)'),
                                       "jump": "previzCtl.jumpToPoseKey(pk.id, u)"}),
                       "var PREVIZ_KEY_CLICK_SLOP_PX = 4;" in vjs and "const CLICK_SLOP_PX = 4;" in pjs,
                       "margin: 0 8px;" in _css_rule(css, ".vnew-previz-keys-diamonds") and "var PREVIZ_SCRUB_THUMB_INSET_PX = 8;" in vjs,
                       "top: 15px;" in _css_rule(css, ".vnew-previz-key-diamond.is-camera") and "margin: -16px" not in css],
               [True, [], [], True, [], True, True, True],
               "마름모 줄이 스크러버 앞(위) · 누름 → window 움직임(4px 넘으면 scrubKey) · 그냥 떼면 그 키 · 마우스 click 무시 · Home / End · 안쪽 8px 맞춤")
    except Exception as exc:  # noqa: BLE001
        crash("u-b", exc)

    try:  # (u-c)
        picker_ids = re.findall(r'\["(\w+)", \d, \d, "[^"]+"\]', vjs[vjs.find("var PREVIZ_JOINT_PICKER = ["):][:900])
        order = re.search(r"const POSE_PICK_ORDER = \[([^\]]*)\]", pjs)
        order_ids = re.findall(r'"(\w+)"', order.group(1)) if order else []
        jp = _body(vjs, "previzJointPicker", "    ")
        expect("u-c", [sorted(picker_ids) == sorted(PICK_ORDER), order_ids,
                       missing(jp, {"select": "previzCtl.selectJoint(spec[0])", "grid": ("b.style.gridRow", "b.style.gridColumn"),
                                    "on": '(joint === spec[0] ? " is-on" : "")', "list": "ctl.poseJointList()"}),
                       "if (ctl && ctl.poseMode && ctl.poseJointList) box.appendChild(previzJointPicker(ctl, joint));"
                       in _body(vjs, "previzPoseSection", "    "),
                       "grid-template-columns: repeat(3, minmax(0, 1fr));" in _css_rule(css, ".vnew-previz-joint-picker"),
                       ".vnew-previz-joint-btn.is-on {" in css],
               [True, PICK_ORDER, [], True, True, True],
               "판 단추 16개(머리·목·가슴·어깨·팔꿈치·손(IK)·골반·엉덩이·무릎·발(IK)) = 편집기 순서 · 누르면 selectJoint · 포즈 모드 패널 · 3칸 격자")
    except Exception as exc:  # noqa: BLE001
        crash("u-c", exc)

    try:  # (u-d)
        sync = _body(pjs, "syncPose")
        key = _body(pjs, "handleKey")
        expect("u-d", ["const POSE_RING_TUBE = 0.0048;" in pjs, "const POSE_RING_FIT = 1.3;" in pjs, "const POSE_RING_DIM = 0.35;" in pjs,
                       "new THREE.TorusGeometry(radius, POSE_RING_TUBE, 6, 40)" in _body(pjs, "ensurePoseControls"),
                       missing(sync, {"dim": "ring.mat.opacity = on || hover === ring.joint ? 1 : POSE_RING_DIM;", "hover": "POSE_HOVER_COLOR",
                                      "ik": "edit.joint === POSE_IK_JOINT[h.name]", "tip off": "showPoseTip(null);"}),
                       missing(_body(pjs, "posePickAll"), {"dedupe": "if (!out.some((o) => o.name === info.name))", "dist": "dist: hit.distance"}),
                       missing(_body(pjs, "posePickAt"), {"same spot": "< CLICK_SLOP_PX", "same joint": "c.joint === edit.joint",
                                                          "next": "(idx + 1) % all.length"}),
                       missing(_body(pjs, "endPoseDrag"), {"cycle": "pk && pk.cycling ? pk.next : null", "reset": "poseCycle = null;"}),
                       "setPoseHover(pk, event.clientX, event.clientY);" in _body(pjs, "hoverCursor"),
                       'poseTip.className = "vnew-previz-joint-tip";' in pjs and ".vnew-previz-joint-tip {" in css,
                       missing(key, {"tab": ('(code === "Tab" || key === "tab") && edit.pose', "stepPoseJoint(event.shiftKey ? -1 : 1);")}),
                       missing(pjs, {"api": ("    poseJointList,\n", "    stepPoseJoint,\n")})],
               [True, True, True, True, [], [], [], [], True, True, [], []],
               "고리 선 0.0048(40% 얇게) · 반지름 = 팔다리 × 1.3 · 고른 것만 1 · 나머지 0.35 · 밝힘 · 손(IK) 네모 · 가까운 순(한 번씩) · 같은 자리 다시 = 다음 · Tab")
    except Exception as exc:  # noqa: BLE001
        crash("u-d", exc)

    files = [PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js", PUBLIC / "vnew.css", TOOLS / "check_previz_pose_ux_213.py"]
    expect("u-e", [p.name for p in files if b"\r" in p.read_bytes()], [], "고친 파일 LF")

    try:  # (u-f)
        if not node:
            warns.append("node 가 없어 node --check(u-f)를 건너뜁니다")
            seen.add("u-f")
        else:
            for path in (PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js"):
                proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
                expect("u-f", [path.name, proc.returncode, (proc.stderr or "")[-300:]], [path.name, 0, ""], "node --check")
    except Exception as exc:  # noqa: BLE001
        crash("u-f", exc)

    if not node:
        warns.append("node 가 없어 실행 검사(u-g·u-h)를 건너뜁니다")
        seen.update({"u-g", "u-h"})
    else:
        work = Path(tempfile.mkdtemp(prefix="chk213-"))
        try:
            try:  # (u-g)
                got, err = _node(node, work, _DOM_RUNNER.replace("/*__VNEW__*/", _vnew_funcs(vjs)), [json.dumps(_fixed_heights(css))])
                if err:
                    expect("u-g", err, "", "node DOM 하네스")
                else:
                    _expect_dom(expect, got)
            except Exception as exc:  # noqa: BLE001
                crash("u-g", exc)
            try:  # (u-h)
                if THREE_LINE not in pjs:
                    expect("u-h", "three import 줄 없음", "", "vnew-previz.js 의 three import 줄")
                else:
                    three = (PUBLIC / "vendor" / "three" / "three.module.min.js").resolve().as_uri()
                    (work / "previz213.mjs").write_text(pjs.replace(THREE_LINE, f'import * as THREE from "{three}";')
                                                        + "\nexport { createController };\n", encoding="utf-8", newline="\n")
                    got, err = _node(node, work, _EDITOR_RUNNER, [three])
                    if err:
                        expect("u-h", err, "", "node 편집기 하네스")
                    else:
                        _expect_editor(expect, got)
            except Exception as exc:  # noqa: BLE001
                crash("u-h", exc)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    for text in warns:
        print(f"[WARN] 작업 213 {text}")
    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 213 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 213 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 213 ({group}) {title}")
    return passed


def _expect_dom(expect, got: dict) -> None:
    lay = got["layout"]
    tops = lay["tops"]
    expect("u-g", [all(t == tops[0] for t in tops), lay["skelSame"], lay["textMoved"], lay["infoChanged"], lay["hintChanged"],
                   lay["diamondsFirst"], lay["scrubSecond"]],
           [True, True, [], True, True, True, True],
           f"고르기 · 포즈+키+미리보기 · 풀기에서 도구 줄·조준 줄·뷰포트·스크러버·키 줄 top 그대로({tops[0]} ↔ {tops[2]}) · 구조 그대로 · "
           "바뀐 글은 고정 칸 안 · 마름모 줄이 스크러버 위")
    d = got["diamond"]
    expect("u-g", [d["beforeSlop"], d["drag"], d["scrubValue"], d["listenersLeft"], d["click"], d["keyboard"], d["homeEnd"]],
           [[], [["scrub", 0.5], ["scrub", 1]], "1", 0, [["jump", "a", 0.5]], [["jump", "a", 0.5]], [["scrub", 1], ["scrub", 0]]],
           "마름모 누르고 2px = 아무것도 · 200px·492px 끌기 = 스크러버 0.5 → 1(그 키로 안 감 · 마우스 click 무시) · 그냥 누르기 = 그 키 · "
           "키보드 = 그 키 · End / Home")
    s = got["scroll"]
    expect("u-g", s, {"up": 232, "down": 340, "still": 340, "page": 0}, "고른 카드가 패널 밖이면 패널만 굴림(페이지는 그대로)")


def _expect_editor(expect, got: dict) -> None:
    r = got["rings"]
    expect("u-h", [r["radius"], r["tube"], r["pickElbow"], r["pickHead"], r["dimAll"]],
           [{"head": 0.169, "neck": 0.091, "spine": 0.221, "shoulderL": 0.0715, "shoulderR": 0.0715, "elbowL": 0.065, "elbowR": 0.065,
             "hipL": 0.104, "hipR": 0.104, "kneeL": 0.091, "kneeR": 0.091}, [0.0048], 0.022, 0.035, True],
           "고리 반지름 = 팔다리 굵기 × 1.3 · 선 0.0048(0.008 의 60%) · 고르는 띠도 비례(팔꿈치 좁게) · 아무것도 안 골랐으면 모두 0.35")
    expect("u-h", [got["opacity"], got["ik"], got["tabOff"]],
           [{"neck": 1, "neckSel": True, "others": [0.35]}, {"joint": "wristL", "handL": True, "handR": False}, {"done": False, "joint": None}],
           "고른 고리만 진하게(나머지 0.35) · 왼손(IK) = 손목 + 왼손 네모 밝힘 · 포즈 모드가 아니면 Tab 은 그대로(포커스 이동)")
    c = got.get("cycle")
    expect("u-h", [got["overlapPts"] > 0, bool(c)] + ([c["n"] >= 2, c["sorted"], c["seqOk"], c["dragKeep"], c["after"], c["hoverNext"],
                                                       c["tipCount"], c["tipShown"], c["leaveHidden"]] if c else []),
           [True, True] + ([True] * 9 if c else []),
           f"뒤돌아선 인물의 겹친 자리({got['overlapPts']}곳): 카메라에 가까운 순 · 다시 누를 때마다 다음({c and c['seq']}) · 끌기는 고른 것 그대로 · "
           "끈 뒤 처음부터 · 커서 = 다음 것 밝힘 · 이름표 '겹침 2/n' · 벗어나면 숨김")
    h = got.get("hover")
    expect("u-h", [bool(h)] + ([h["hover"] == h["joint"], h["opacity"], h["color"], h["tip"] == h["names"].get(h["joint"])] if h else []),
           [True] + ([True, 1, True, True] if h else []), "커서 아래 고리 = 흰색·진하게 · 한국어 이름표")
    expect("u-h", [got["tabs"], got["list"]],
           [{"seq": ["head", "neck", "head", "ankleR", "head"], "done": True}, PICK_ORDER],
           "Tab = 다음 · Shift+Tab = 앞 · 끝에서 돌아감 · 관절 목록 16개(판 순서)")


if __name__ == "__main__":
    ok = check_previz_pose_ux_213()
    print('hook (check_vpoc.py main, 작업 212 다음 줄): if not __import__("check_previz_pose_ux_213").check_previz_pose_ux_213(): '
          'fail("작업 213: 프리비즈 포즈 쓰기(마름모 끌기 · 고리 겹침 · 화면 밀림) 검사 실패")')
    sys.exit(0 if ok else 1)
