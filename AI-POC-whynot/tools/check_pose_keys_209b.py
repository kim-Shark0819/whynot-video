"""작업 209b: ④ 프리비즈 편집기 — Maya 식 자세 키(S) · 키 마름모 · , / . · 잔상 · 이동 궤적 · 포즈 모음.

  (k-a) 정적: 키 찍기 — S 단축키(handleKey) · 도구 줄 [키 찍기 (S)] · 키 없던 인물은 u=0 키도 · 같은 u 는 바꿈 · 키 사이 고침 = 미리보기 + 안내
  (k-b) 정적: 스크러버 마름모(자세 키 주황 · 카메라 키 파랑) · 누르면 그 키로 · , / . · [키 지우기] · Delete
  (k-c) 정적: 보간 함수 하나(sampleKeys)를 재생·녹화가 같이 쓴다(PrevizScene.update → _keyedState · _applyKeyed) · 미리보기는 녹화 안 됨
  (k-d) 정적: 잔상·이동 궤적은 helpers 안(녹화 중 숨김) · 고를 수 없음(raycast 없음) · [잔상] 켜고 끄기(키 2개 이상이면 저절로)
  (k-e) 정적: 포즈 모음(기본 11개 · 이 포즈 저장 → localStorage try/catch · 지우기 · 좌우 반전 붙여넣기)
  (k-f) 정적: 키 넣기·지우기·고치기 = 한 단계(commitEdit) · manual · 저장 본문 scenes 에 keys(rig 옆) · dirty 도 같은 scenes
  (k-g) 고친 파일 LF
  (k-h) node --check vnew-previz.js · vnew.js (node 없으면 WARN)
  (k-i) node 순수 함수: 키 사이 smoothstep · 방향은 가까운 쪽 · 첫 키 앞/마지막 키 뒤 그대로 · 최대 8개 · 같은 u 바꿈 · 좌우 반전 포즈 ·
        궤적 21점 · 키 관절 맞춤(0 값 관절 유지)
  (k-j) node 렌더: 키 있는 인물 = 보간 자리·방향·rig · 0.3m 넘게 옮기면 걸음 · 0.3m 안은 걸음 없음 · 키 없는 옛 인물은 그대로(moveTo 직선)
  (k-k) node 편집기: S(u=0) · 키 사이 고침 = 미리보기(데이터 그대로 · 안내) · S = 두 번째 키 · 잔상·궤적 · 녹화 프레임에 움직임 ·
        녹화엔 도우미·미리보기 없음 · , / . · Delete · 되돌리기 · 키 위 고침 = 그 키 · 같은 u 다시 S · 포즈 모음 붙이기(키에) · 반전

실행: python -X utf8 -B tools/check_pose_keys_209b.py
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
    "k-a": "키 찍기(S) · 도구 줄 단추 · u=0 시작 키 · 같은 u 바꿈 · 키 사이 미리보기 안내",
    "k-b": "키 마름모 · 누르면 그 키로 · , / . · 키 지우기 · Delete",
    "k-c": "보간 함수 하나를 재생·녹화가 같이 씀 · 미리보기는 녹화 안 됨",
    "k-d": "잔상·이동 궤적은 녹화에서 빠짐 · 고를 수 없음 · [잔상]",
    "k-e": "포즈 모음(기본 · 저장 · 지우기 · 좌우 반전 붙여넣기)",
    "k-f": "한 단계 · manual · 저장 본문·dirty 에 keys",
    "k-g": "고친 파일 LF",
    "k-h": "node --check 두 파일",
    "k-i": "node 순수 함수: 보간 · 그대로 · 8개 · 바꿈 · 반전 · 궤적",
    "k-j": "node 렌더: 키 보간 · 걸음 · 옛 인물 그대로",
    "k-k": "node 편집기: S · 미리보기 · 잔상·궤적 · 녹화 · , . Delete · 되돌리기 · 포즈 모음",
}
THREE_LINE = 'import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";'
PRESET_IDS = ["stand", "sit", "lean_forward", "lean_back", "kneel", "lie", "hand_own_cheek", "hand_other_shoulder",
              "reach_other_chin", "arms_crossed", "hands_in_pockets"]
HINT = "키 찍기(S)를 눌러 이 시점에 저장하세요"


def _body(source: str, name: str) -> str:
    """function name(...) {...} 또는 메서드 name(...) {...} 원문(중괄호 짝 맞춤). 없으면 ""."""
    m = re.search(r"\n\s*(?:export\s+)?(?:function\s+)?" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    if not m:
        return ""
    i = m.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start() + 1: j + 1]
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
const mod = await import(new URL("./previz209b.mjs", import.meta.url));
const r3 = (x) => Math.round(x * 1000) / 1000;
const r3a = (a) => a.map(r3);
const out = {};

// ---- (i) 순수 함수 ----
{
  const keys = [
    { u: 0.2, pos: [0, 0, 0], facingDeg: 170, rig: { spine: [0.2, 0, 0], rootY: 0 } },
    { u: 0.8, pos: [2, 0, -1], facingDeg: -170, rig: { spine: [0.6, 0, 0], rootY: -0.2 } },
  ];
  const mid = mod.sampleKeys(keys, 0.5);
  const q = mod.sampleKeys(keys, 0.35); // lin 0.25 → smoothstep 0.15625
  const before = mod.sampleKeys(keys, 0.05);
  const after = mod.sampleKeys(keys, 0.95);
  const one = mod.sampleKeys([keys[1]], 0.1);
  let full = [];
  for (let i = 0; i < 8; i++) full = mod.upsertKey(full, { u: i / 10, pos: [i, 0, 0], facingDeg: 0, rig: {} }).keys;
  const ninth = mod.upsertKey(full, { u: 0.95, pos: [9, 0, 0], facingDeg: 0, rig: {} });
  const re = mod.upsertKey(full, { u: 0.3004, pos: [7, 0, 0], facingDeg: 10, rig: {} });
  const unsorted = mod.upsertKey([{ u: 0.5, pos: [0, 0, 0], facingDeg: 0 }], { u: 0.1, pos: [1, 0, 0], facingDeg: 0 });
  const presets = mod.buildPosePresets();
  const cheek = presets.find((p) => p.id === "hand_own_cheek");
  const mir = mod.mirrorPreset(cheek);
  const mirrorOk = ["shoulder", "elbow", "wrist", "hip", "knee", "ankle"].every((j) => {
    const want = mod.clampRigJoint(j + "L", [cheek.rig[j + "R"][0], -cheek.rig[j + "R"][1], -cheek.rig[j + "R"][2]]);
    return want.every((v, i) => Math.abs(v - mir.rig[j + "L"][i]) < 0.0015);
  });
  const twice = mod.mirrorPreset(mir);
  const twiceOk = Object.keys(cheek.rig).every((k) => k === "rootY" ? twice.rig.rootY === cheek.rig.rootY
    : cheek.rig[k].every((v, i) => Math.abs(v - twice.rig[k][i]) < 0.0015));
  const uk = [{ u: 0, pos: [0, 0, 0], facingDeg: 0, rig: { neck: [0, 0, 0] } }, { u: 1, pos: [0, 0, 0], facingDeg: 0, rig: { spine: [0.3, 0, 0] } }];
  mod.unifyKeyRigs(uk, { spine: [0.05, 0, 0], neck: [0.1, 0, 0] });
  const trail = mod.trailPoints(keys, 20);
  const cr = presets.find((p) => p.id === "arms_crossed");
  const pockets = presets.find((p) => p.id === "hands_in_pockets");
  out.pure = {
    mid: { pos: r3a(mid.pos), facing: r3(Math.abs(mid.facingDeg)), spine: r3a(mid.rig.spine), rootY: r3(mid.rig.rootY) },
    q: { x: r3(q.pos[0]), t: r3(q.t), facing: r3(q.facingDeg) },
    before: { pos: r3a(before.pos), facing: r3(before.facingDeg), spine: r3a(before.rig.spine) },
    after: { pos: r3a(after.pos), facing: r3(after.facingDeg), spine: r3a(after.rig.spine) },
    one: r3a(one.pos), none: mod.sampleKeys([], 0.5),
    max: { n: full.length, ninthFull: ninth.full, ninthN: ninth.keys.length, maxConst: mod.POSE_KEYS_MAX },
    rekey: { replaced: re.replaced, n: re.keys.length, pos: re.keys[3].pos[0], u: re.keys[3].u, facing: re.keys[3].facingDeg },
    sorted: unsorted.keys.map((k) => k.u), removed: mod.removeKey(full, 0.3).length,
    neighbor: [mod.keyNeighbor(full, 0.3, -1), mod.keyNeighbor(full, 0.3, 1), mod.keyNeighbor(full, 0, -1)],
    presets: presets.map((p) => p.id), presetJoints: Object.keys(cheek.rig).length,
    mirror: { ok: mirrorOk, twice: twiceOk, name: mir.nameKo.endsWith("(반전)") },
    sitRoot: presets.find((p) => p.id === "sit").rig.rootY, liePose: presets.find((p) => p.id === "lie").pose || null,
    crossedBent: cr.rig.elbowL[0] < -0.5 && cr.rig.elbowR[0] < -0.5, pocketsDown: pockets.rig.shoulderL[0] > -0.6,
    unify: { k0: uk[0].rig, k1: uk[1].rig },
    trail: { n: trail.length, first: r3a(trail[0]), last: r3a(trail[20]) },
  };
}

// ---- (j) 렌더 ----
{
  const cam = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 200);
  const shot = { pos: [0, 1.5, 5], target: [0, 1.2, 0], fov: 40, manual: true };
  const scene = (actors) => new mod.PrevizScene({ room: { width: 8, depth: 8, height: 3, walls: {} },
    actors: [{ id: "a", name: "A", heightM: 1.7, color: "#f00" }, { id: "b", name: "B", heightM: 1.7, color: "#0f0" }],
    props: [], light: {}, timeline: [{ cutId: "c1", start: 0, end: 2, camera: shot, actors }] }, null, null);
  const walk = { id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand", rig: { spine: [0.9, 0, 0] },
    keys: [{ u: 0, pos: [0, 0, 0], facingDeg: 0, rig: { spine: [0, 0, 0] } }, { u: 1, pos: [1.5, 0, 0], facingDeg: 90, rig: { spine: [0.4, 0, 0] } }] };
  const short = { id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand",
    keys: [{ u: 0, pos: [0, 0, 0], facingDeg: 0, rig: {} }, { u: 1, pos: [0.2, 0, 0], facingDeg: 0, rig: {} }] };
  const old = { id: "b", pos: [1, 0, 1], moveTo: [2, 0, 1], facingDeg: 30, pose: "stand", rig: { neck: [0.2, 0, 0] } };
  const s1 = scene([walk, old]);
  s1.update(0.5, cam); // u 0.25
  const a = s1.actors.a.mannequin;
  const b = s1.actors.b.mannequin;
  const r2 = (x) => Math.round(x * 100) / 100;
  const q = { x: r3(a.group.position.x), yawDeg: r2(a.group.rotation.y * 180 / Math.PI), spine: r2(a.spine.rotation.x),
    hipL: r3(a.hipL.pivot.rotation.x), pelvis: r3(a.pelvis.position.y) };
  s1.update(1, cam); // u 0.5 — 키 없는 b 는 moveTo 직선
  const oldAt = { x: r3(b.group.position.x), yawDeg: r3(b.group.rotation.y * 180 / Math.PI), neck: r3(b.neck.rotation.x) };
  s1.update(2, cam); // 컷 끝 = 마지막 키 그대로(걸음 없음)
  const end = { x: r3(a.group.position.x), spine: r3(a.spine.rotation.x), hipL: r3(a.hipL.pivot.rotation.x) };
  const s2 = scene([short]);
  s2.update(1, cam);
  const m2 = s2.actors.a.mannequin;
  out.render = { q, end, oldAt, short: { x: r3(m2.group.position.x), hipL: r3(m2.hipL.pivot.rotation.x), pelvis: r3(m2.pelvis.position.y) } };
}

// ---- (k) 편집기 ----
{
  class FakeTC extends THREE.EventDispatcher {
    constructor(cam) { super(); this.camera = cam; this.object = null; this.dragging = false; this.axis = null; this.helper = new THREE.Object3D(); }
    getHelper() { return this.helper; } attach(o) { this.object = o; } detach() { this.object = null; } setMode() {} setSpace() {}
    setTranslationSnap() {} setRotationSnap() {} setSize() {} dispose() {}
  }
  class FakeOrbit extends THREE.EventDispatcher {
    constructor(cam) { super(); this.object = cam; this.target = new THREE.Vector3(); this.enabled = true; }
    update() {} dispose() {}
  }
  const sd = { room: { width: 8, depth: 8, height: 3, walls: {} },
    actors: [{ id: "a", name: "A", heightM: 1.7, color: "#ff0000" }, { id: "b", name: "B", heightM: 1.7, color: "#00ff00" }],
    props: [], light: {},
    timeline: [{ cutId: "c1", start: 0, end: 2, camera: { pos: [0, 1.5, 5], target: [0, 1.2, 0], fov: 40 },
      actors: [{ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" }, { id: "b", pos: [2, 0, -2], facingDeg: 180, pose: "stand" }] }] };
  const canvas = new FakeEl("canvas");
  const frames = [];
  let E = null;
  canvas.toBlob = (cb) => {
    const m = E.scene.actors.a.mannequin;
    frames.push({ x: r3(m.group.position.x), hipL: r3(m.hipL.pivot.rotation.x), helpers: E.helpers.visible });
    cb(new Blob(["x"]));
  };
  const renderer = { domElement: canvas, shadowMap: {}, setPixelRatio() {}, setSize() {}, dispose() {}, render() {} };
  const ctl = mod.createController(new FakeEl("div"), { scenes: [sd], renderer, controls: { OrbitControls: FakeOrbit, TransformControls: FakeTC }, width: 1280, height: 720 });
  await ctl.ready();
  E = ctl._editor;
  const c1 = sd.timeline[0];
  const A = () => c1.actors.find((x) => x.id === "a");
  const ev = { get commit() { return E.undoDepth; } }; // 되돌리기 단계 수
  const key = (code, k, extra) => E.stage.dispatch("keydown", Object.assign({ code, key: k, target: E.stage, preventDefault() {} }, extra || {}));
  const k = {};
  key("KeyS", "s");
  k.noSel = A().keys === undefined;
  ctl.select({ type: "actor", id: "a" });
  ctl.scrubKey(0);
  key("KeyS", "ㄴ");
  k.first = { n: (A().keys || []).length, u: A().keys && A().keys[0].u, commit: ev.commit, manual: !!A().manual, undo: ctl.canUndo };
  // 키 사이(u=1)에서 고치기 = 미리보기만
  ctl.scrubKey(1);
  const before = JSON.stringify(c1);
  const info0 = ctl.poseKeyInfo();
  for (let i = 0; i < 3; i++) key("ArrowRight", "arrowright", { shiftKey: true });
  const info1 = ctl.poseKeyInfo();
  k.live = { same: JSON.stringify(c1) === before, hint: info0.hint, live: info1.live, x: r3(E.scene.actors.a.mannequin.group.position.x),
    commit: ev.commit, onKey: info1.onKey };
  key("KeyS", "s");
  k.second = { n: A().keys.length, u: A().keys[1].u, x: A().keys[1].pos[0], live: !!E.live, commit: ev.commit, key0x: A().keys[0].pos[0],
    staticX: A().pos[0], info: ctl.poseKeyInfo().onKey };
  // 중간(u 0.5): 보간 · 잔상 2개 · 궤적
  ctl.scrubKey(0.5);
  const ghosts = E.ghosts.filter((g) => g.m.group.visible);
  const trailAttr = E.trailGroup.children[0].geometry;
  k.mid = { x: r3(E.scene.actors.a.mannequin.group.position.x), ghostsOn: E.ghostGroup.visible, ghosts: ghosts.length,
    ghostX: ghosts.map((g) => r3(g.m.group.position.x)).sort(), ghostOpacity: ghosts.every((g) => g.mats[0].opacity < 0.4 && g.mats[0].transparent),
    ghostNoPick: ghosts.every((g) => { let ok = true; g.m.group.traverse((o) => { if (o.isMesh && o.raycast !== undefined && o.raycast.length !== 0) ok = false; }); return ok; }),
    inHelpers: E.ghostGroup.parent === E.helpers && E.trailGroup.parent === E.helpers, trailOn: E.trailGroup.visible,
    trailN: trailAttr.drawRange.count, info: ctl.poseKeyInfo() };
  // 잔상 끄기
  ctl.setGhosts(false);
  k.ghostOff = E.ghostGroup.visible;
  ctl.setGhosts(true);
  // 미리보기를 만든 뒤 녹화: 프레임은 키 그대로(미리보기 없음) · 도우미 없음
  key("ArrowLeft", "arrowleft", { shiftKey: true });
  k.liveMid = r3(E.scene.actors.a.mannequin.group.position.x);
  await ctl.renderFrames(0, 2, 4);
  k.rec = { xs: frames.map((f) => f.x), helpers: frames.every((f) => f.helpers === false), bob: frames.some((f) => Math.abs(f.hipL) > 0.05) };
  // , / .
  ctl.scrubKey(0.5);
  key("Comma", ",");
  const atPrev = ctl.keyU;
  key("Period", ".");
  const atNext = ctl.keyU;
  const sel = ctl.poseKeyInfo().selected;
  const c0 = ev.commit;
  key("Delete", "Delete");
  k.nav = { prev: atPrev, next: atNext, sel, afterDelete: (A().keys || []).length, commit: ev.commit - c0 };
  ctl.undo();
  k.undo = (A().keys || []).length;
  // 키 위(u=1)에서 고치기 = 그 키 · 한 단계
  ctl.scrubKey(1);
  const c2 = ev.commit;
  key("ArrowRight", "arrowright");
  k.onKey = { x: A().keys[1].pos[0], commit: ev.commit - c2, live: !!E.live, key0: A().keys[0].pos[0] };
  key("KeyS", "s");
  k.rekey = A().keys.length;
  // 포즈 모음: 지금 키(u=1)에 붙이기 · 반전
  const preset = ctl.posePresets().find((p) => p.id === "hand_own_cheek");
  const c3 = ev.commit;
  ctl.applyPosePreset("a", preset, false);
  const k1 = A().keys[1].rig;
  k.preset = { shoulderR: r3a(k1.shoulderR), want: r3a(mod.clampRigJoint("shoulderR", preset.rig.shoulderR)), commit: ev.commit - c3,
    joints0: Object.keys(A().keys[0].rig).length, joints1: Object.keys(k1).length };
  ctl.applyPosePreset("a", preset, true);
  k.presetMirror = { shoulderL: r3a(A().keys[1].rig.shoulderL), want: r3a(mod.clampRigJoint("shoulderL",
    [preset.rig.shoulderR[0], -preset.rig.shoulderR[1], -preset.rig.shoulderR[2]])) };
  const cap = ctl.capturePose("a");
  k.capture = cap && Object.keys(cap.rig).length;
  k.other = c1.actors.find((x) => x.id === "b").keys === undefined;
  out.editor = k;
}
console.log(JSON.stringify(out));
process.exit(0);
"""


def _run_node(node: str, pjs: str) -> tuple[dict, str]:
    three = (PUBLIC / "vendor" / "three" / "three.module.min.js").resolve().as_uri()
    if THREE_LINE not in pjs:
        return {}, "vnew-previz.js 의 three import 줄을 찾지 못했습니다"
    work = Path(tempfile.mkdtemp(prefix="chk209b-"))
    try:
        (work / "previz209b.mjs").write_text(pjs.replace(THREE_LINE, f'import * as THREE from "{three}";')
                                             + "\nexport { PrevizScene, createController };\n", encoding="utf-8", newline="\n")
        (work / "run.mjs").write_text(_RUNNER, encoding="utf-8", newline="\n")
        try:
            proc = subprocess.run([node, str(work / "run.mjs"), three], capture_output=True, text=True, encoding="utf-8", timeout=90)
        except subprocess.TimeoutExpired:
            return {}, "node 검사가 90초 안에 끝나지 않았습니다"
        lines = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0 or not lines:
            return {}, f"node 실패: {(proc.stderr or '').strip()[-400:]}"
        return json.loads(lines[-1]), ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_pose_keys_209b() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-600:])

    def missing(src: str, needles: dict) -> list[str]:
        return [k for k, v in needles.items() if (v not in src if isinstance(v, str) else not all(x in src for x in v))]

    pjs = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    css = (PUBLIC / "vnew.css").read_text(encoding="utf-8")
    node = shutil.which("node")

    try:  # (k-a)
        key = _body(pjs, "handleKey")
        setk = _body(pjs, "setPoseKey")
        expect("k-a", [missing(key, {"S": ('is("s") && selectedActorId()', "setPoseKey();")}),
                       missing(setk, {"u": "const u = roundU(cutU(cut));", "start key": 'upsertKey(keys, Object.assign({ u: 0 }, staticPose(ca, 0)))',
                                      "upsert": "upsertKey(keys, Object.assign({ u }, snap))", "full": "res.full",
                                      "clear live": "edit.live = null;", "scrubber": "pinAt(cut, u);"}),
                       missing(_body(pjs, "upsertKey"), {"replace": "list[i] = k;", "max": "list.length >= limit"}),
                       missing(_body(pjs, "poseTarget"), {"key": "return ca.keys[i];", "live": "edit.live = { cut, id, u, pose, changed: false };"}),
                       f'const KEY_HINT_TEXT = "{HINT}";' in pjs,
                       missing(vjs, {"button": '"키 찍기 (S)"', "wire": "previzCtl.setPoseKey()", "hint": "pk.hint",
                                     "live": 'poseRow.classList.toggle("is-live"',
                                     "fields": ("previzCtl.poseFieldTarget(ca.id, cut)", "previzCtl.poseFieldChanged(ca.id, cut)")}),
                       ".vnew-previz-posekeys.is-live .vnew-previz-posekeys-hint {" in css],
               [[], [], [], [], True, [], True],
               "S = 키 찍기(키 스크러버 u) · 키 없던 인물은 u=0 도 · 같은 u 는 바꿈 · 키 위 = 그 키 · 키 사이 = 미리보기 + 안내 · 도구 줄 단추")
    except Exception as exc:  # noqa: BLE001
        crash("k-a", exc)

    try:  # (k-b)
        key = _body(pjs, "handleKey")
        expect("k-b", [missing(key, {"comma": ('code === "Comma"', "gotoPoseKey(-1)"), "period": ('code === "Period"', "gotoPoseKey(1)"),
                                     "delete": ('code === "Delete"', "edit.keySel", "deletePoseKey();")}),
                       missing(vjs, {"diamonds": ("vnew-previz-key-diamond is-", 'diamond(u, "pose"', 'diamond(0, "camera"'),
                                     "jump": "previzCtl.jumpToPoseKey(pk.id, u)", "nav": ("previzCtl.gotoPoseKey(-1)", "previzCtl.gotoPoseKey(1)"),
                                     "delete": ('"키 지우기"', "previzCtl.deletePoseKey()")}),
                       missing(css, {"pose": ".vnew-previz-key-diamond.is-pose {", "camera": ".vnew-previz-key-diamond.is-camera {"}),
                       re.search(r"\.is-pose \{\s*background: (#[0-9a-f]+)", css).group(1) != re.search(r"\.is-camera \{\s*background: (#[0-9a-f]+)", css).group(1)],
               [[], [], [], True], "마름모(자세 키 · 카메라 키 다른 색) · 누르면 그 키 · , / . · 키 지우기 · Delete(고른 마름모)")
    except Exception as exc:  # noqa: BLE001
        crash("k-b", exc)

    try:  # (k-c)
        update = _body(pjs, "update")
        render_at = _body(pjs, "renderAt")
        expect("k-c", [missing(update, {"state": "this._keyedState(ca, cut, frac)", "apply": "this._applyKeyed(entry.mannequin, ca, keyed, frac)",
                                        "static rig skipped": "if (!(posById[ca.id] && posById[ca.id].keyed)) entry.mannequin.applyRig(ca.rig);"}),
                       "return sampleKeys(ca.keys, frac);" in _body(pjs, "_keyedState"),
                       "const s = sampleKeys(ca.keys, frac, m.baseRig);" in _body(pjs, "_applyKeyed"),
                       "current.scene.livePose = !recording && !playing ? edit.live : null;" in render_at,
                       pjs.count("export function sampleKeys("), "renderAt(" in _body(pjs, "record"), "renderAt(t);" in _body(pjs, "renderFrames"),
                       "smoothstep01(lin)" in _body(pjs, "sampleKeys"), "wrapDeg(fa + wrapDeg(fb - fa) * t)" in _body(pjs, "sampleKeys"),
                       "clampRigJoint(j," in _body(pjs, "sampleKeys")],
               [[], True, True, True, 1, True, True, True, True, True],
               "보간(sampleKeys, smoothstep · 가까운 각 · clampRigJoint) 하나 · update 가 재생·record·renderFrames 공통 · 미리보기는 재생·녹화 때 빠짐")
    except Exception as exc:  # noqa: BLE001
        crash("k-c", exc)

    try:  # (k-d)
        expect("k-d", [missing(pjs, {"helpers": ("helpers.add(ghostGroup);", "helpers.add(trailGroup);",
                                                 "helpers.visible = edit.helpersOn && !recording;"),
                                     "no pick": ("o.raycast = noRaycast;", "trailLine.raycast = noRaycast;", "dot.raycast = noRaycast;")}),
                       "!recording" in _body(pjs, "syncGhosts") and "!recording" in _body(pjs, "syncTrail"),
                       "edit.ghosts === null ? n >= 2" in _body(pjs, "ghostsOn"),
                       "trailPoints(ca.keys, TRAIL_SAMPLES)" in _body(pjs, "syncTrail"),
                       missing(vjs, {"toggle": ('"잔상"', "previzCtl.setGhosts(ghostBox.checked)", "st.poseKeys.ghosts")})],
               [[], True, True, True, []], "잔상·궤적 = helpers(녹화 중 숨김) · raycast 없음 · 키 2개 이상이면 잔상 저절로 · [잔상] 체크")
    except Exception as exc:  # noqa: BLE001
        crash("k-d", exc)

    try:  # (k-e)
        lib = _body(vjs, "previzPoseLibrary")
        expect("k-e", [[i for i in PRESET_IDS if f'id: "{i}"' not in pjs],
                       missing(lib, {"head": '"포즈 모음"', "save": ('"이 포즈 저장"', "window.prompt(", "previzCtl.capturePose(ca.id)"),
                                     "apply": "previzCtl.applyPosePreset(ca.id, preset, false, cut)",
                                     "mirror": ("좌우 반전 붙여넣기", "previzCtl.applyPosePreset(ca.id, preset, true, cut)"),
                                     "delete": "previzSaveUserPoses(previzUserPoses().filter("}),
                       missing(_body(vjs, "previzUserPoses"), {"try": ("try {", "window.localStorage.getItem(PREVIZ_POSE_LIBRARY_KEY)", "catch (error)")}),
                       missing(_body(vjs, "previzSaveUserPoses"), {"try": ("try {", "window.localStorage.setItem(PREVIZ_POSE_LIBRARY_KEY", "catch (error)")}),
                       "box.appendChild(previzPoseLibrary(cut, ca));" in _body(vjs, "previzPoseSection"),
                       ".vnew-previz-poselib {" in css],
               [[], [], [], [], True, True], "포즈 모음: 기본 11개 · 이 포즈 저장(이름 → localStorage try/catch) · 지우기 · 좌우 반전 붙여넣기")
    except Exception as exc:  # noqa: BLE001
        crash("k-e", exc)

    try:  # (k-f)
        save_at = vjs.find("// 작업 209b: 자세 키(cut actor 의 keys")
        save = vjs[save_at: save_at + 400] if save_at >= 0 else ""
        norm = _body(vjs, "previzNormJson")
        expect("k-f", [missing(_body(pjs, "finishKeyEdit"), {"commit": "commitEdit(cut);", "event": 'kind: "commit"'}),
                       "finishKeyEdit(cut);" in _body(pjs, "setPoseKey") and "finishKeyEdit(cut);" in _body(pjs, "deletePoseKey"),
                       "ca.manual = true;" in _body(pjs, "setPoseKey") and "ca.manual = true;" in _body(pjs, "deletePoseKey"),
                       missing(_body(pjs, "touchPose"), {"manual": "ca.manual = true;", "live": "if (edit.live && tgt === edit.live.pose) {",
                                                        "unify": "unifyKeyRigs(ca.keys"}),
                       "touchPose(cut, owner, ca)" in _body(pjs, "writeJoint") or "else touchPose(cut, owner, ca);" in _body(pjs, "writeJoint"),
                       '"/previz/scene", {' in save and "body: JSON.stringify({ scenes: scenes })" in save,
                       "previzEditSnapshot(previzSaved.scenes) !== previzSaved.snap" in _body(vjs, "previzRefreshDirty"),
                       '"keys"' not in norm and "Object.keys(value).sort()" in norm],
               [[], True, True, [], True, True, True, True],
               "키 넣기·지우기·고치기 = commitEdit 한 단계 · manual · 미리보기는 기록 안 함 · 저장 본문 scenes 에 keys · dirty 도 같은 scenes")
    except Exception as exc:  # noqa: BLE001
        crash("k-f", exc)

    files = [PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js", PUBLIC / "vnew.css", TOOLS / "check_pose_keys_209b.py"]
    expect("k-g", [p.name for p in files if b"\r" in p.read_bytes()], [], "고친 파일 LF")

    try:  # (k-h)
        if not node:
            warns.append("node 가 없어 node --check(k-h)를 건너뜁니다")
            seen.add("k-h")
        else:
            for path in (PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js"):
                proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
                expect("k-h", [path.name, proc.returncode, (proc.stderr or "")[-300:]], [path.name, 0, ""], "node --check")
    except Exception as exc:  # noqa: BLE001
        crash("k-h", exc)

    try:  # (k-i)(k-j)(k-k)
        if not node:
            warns.append("node 가 없어 실행 검사(k-i·k-j·k-k)를 건너뜁니다")
            seen.update({"k-i", "k-j", "k-k"})
        else:
            got, err = _run_node(node, pjs)
            if err:
                expect("k-i", err, "", "node 하네스")
            else:
                _expect_node(expect, got)
    except Exception as exc:  # noqa: BLE001
        crash("k-i", exc)

    for text in warns:
        print(f"[WARN] 작업 209b {text}")
    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 209b ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 209b ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 209b ({group}) {title}")
    return passed


def _expect_node(expect, got: dict) -> None:
    p = got["pure"]
    expect("k-i", [p["mid"], p["q"], p["before"], p["after"], p["one"], p["none"]],
           [{"pos": [1, 0, -0.5], "facing": 180, "spine": [0.4, 0, 0], "rootY": -0.1},
            {"x": 0.312, "t": 0.156, "facing": 173.125},
            {"pos": [0, 0, 0], "facing": 170, "spine": [0.2, 0, 0]}, {"pos": [2, 0, -1], "facing": -170, "spine": [0.6, 0, 0]},
            [2, 0, -1], None],
           "키 사이 smoothstep(u 0.35 → 0.156) · 170° → -170° 는 180° 쪽(가까운 각) · 첫 키 앞·마지막 키 뒤 그대로 · 키 하나 · 키 없음 null")
    expect("k-i", [p["max"], p["rekey"], p["sorted"], p["removed"], p["neighbor"]],
           [{"n": 8, "ninthFull": True, "ninthN": 8, "maxConst": 8}, {"replaced": True, "n": 8, "pos": 7, "u": 0.3, "facing": 10},
            [0.1, 0.5], 7, [0.2, 0.4, None]],
           "최대 8개(9번째 거절) · 같은 u(3자리) 다시 = 바꿈 · u 순 · 지우기 · 앞/뒤 키")
    expect("k-i", [p["presets"], p["presetJoints"], p["mirror"], p["sitRoot"], p["liePose"], p["crossedBent"], p["pocketsDown"]],
           [PRESET_IDS, 16, {"ok": True, "twice": True, "name": True}, -0.4, "lie", True, True],
           "기본 포즈 11개(15관절 + rootY) · 좌우 반전 = L↔R · y,z 부호 · 두 번 = 그대로 · 앉기 rootY -0.4 · 눕기 = 목록 자세 · 팔짱 굽힘")
    expect("k-i", [p["unify"], p["trail"]],
           [{"k0": {"neck": [0, 0, 0], "spine": [0.05, 0, 0]}, "k1": {"spine": [0.3, 0, 0], "neck": [0.1, 0, 0]}},
            {"n": 21, "first": [0, 0.02, 0], "last": [2, 0.02, -1]}],
           "키 관절 맞춤(0 값 관절은 그대로 · 없는 관절 = 목록 자세 값) · 궤적 21점(바닥 위)")
    r = got["render"]
    expect("k-j", [r["q"]["x"], r["q"]["yawDeg"], r["q"]["spine"], abs(r["q"]["hipL"]) > 0.05, r["q"]["pelvis"] < 0,
                   r["end"], r["oldAt"], r["short"]],
           [0.234, 14.06, 0.06, True, True, {"x": 1.5, "spine": 0.4, "hipL": 0}, {"x": 1.5, "yawDeg": 30, "neck": 0.2},
            {"x": 0.1, "hipL": 0, "pelvis": 0}],
           "키 보간 자리·방향·rig(정적 rig 대신) · 1.5m 걷기 = 다리 흔들기·골반 · 끝 = 마지막 키 · 옛 인물(moveTo 직선·rig) 그대로 · 0.2m 는 걸음 없음")
    k = got["editor"]
    expect("k-k", [k["noSel"], k["first"]], [True, {"n": 1, "u": 0, "commit": 1, "manual": True, "undo": True}],
           "인물을 안 고르면 S 무시 · u=0 에서 S(한글 ㄴ) = 키 하나 · 한 단계 · manual")
    expect("k-k", [k["live"], k["second"]],
           [{"same": True, "hint": HINT, "live": True, "x": 1.5, "commit": 1, "onKey": False},
            {"n": 2, "u": 1, "x": 1.5, "live": False, "commit": 2, "key0x": 0, "staticX": 0, "info": True}],
           "키 사이 고침 = 미리보기(데이터 그대로 · 안내 · 화면은 움직임) · S = 두 번째 키(u=1) · 한 단계 · 정적 자리 = 첫 키")
    m = k["mid"]
    expect("k-k", [m["x"], m["ghostsOn"], m["ghosts"], m["ghostX"], m["ghostOpacity"], m["ghostNoPick"], m["inHelpers"], m["trailOn"], m["trailN"],
                   m["info"]["hint"], k["ghostOff"]],
           [0.75, True, 2, [0, 1.5], True, True, True, True, 21, HINT, False],
           "u 0.5 = 0.75m · 잔상 2개(키 자리 · 반투명 · 고를 수 없음) · 궤적 21점 · 둘 다 helpers 안 · [잔상] 끄기")
    rec = k["rec"]
    xs = rec["xs"]
    expect("k-k", [k["liveMid"], len(xs), xs[0], xs[4], xs == sorted(xs), rec["helpers"], rec["bob"]],
           [0.25, 8, 0, 0.75, True, True, True],
           f"녹화 프레임 = 키 움직임({xs}) · 미리보기(0.25m)는 녹화에 없음 · 도우미 숨김 · 걸음")
    expect("k-k", [k["nav"], k["undo"], k["onKey"], k["rekey"], k["other"]],
           [{"prev": 0, "next": 1, "sel": 1, "afterDelete": 1, "commit": 1}, 2, {"x": 1.6, "commit": 1, "live": False, "key0": 0}, 2, True],
           ", = 앞 키 · . = 뒤 키(고름) · Delete = 그 키 지우기(한 단계) · 되돌리기 · 키 위 고침 = 그 키(한 단계) · 같은 u 다시 S = 바꿈 · 다른 인물 그대로")
    pr = k["preset"]
    expect("k-k", [pr["shoulderR"] == pr["want"], pr["commit"], pr["joints0"], pr["joints1"],
                   k["presetMirror"]["shoulderL"] == k["presetMirror"]["want"], k["capture"]],
           [True, 1, 16, 16, True, 16],
           "포즈 모음 붙이기 = 지금 키 rig(한 단계 · 다른 키도 같은 관절) · 좌우 반전 붙여넣기 · 이 포즈 저장 = 15관절 + rootY")


if __name__ == "__main__":
    ok = check_pose_keys_209b()
    print('hook (check_vpoc.py main, 작업 209a 다음 줄): if not __import__("check_pose_keys_209b").check_pose_keys_209b(): '
          'fail("작업 209b: 프리비즈 자세 키(S · 마름모 · , .) · 잔상 · 이동 궤적 · 포즈 모음 검사 실패")')
    sys.exit(0 if ok else 1)
