"""작업 209a 검사 — ④ 프리비즈 편집기의 Maya 식 관절 잡기(FK 고리 · IK 손발 · 골반) + 채널 박스.

  (q-a) 정적: 마네킹 관절 피벗(15개 + 골반 pelvis) · RIG_DIMS 한 곳 · pose_describe.py 치수와 같음(없으면 WARN)
  (q-b) 정적: 목록 자세(apply · applyBody · applyHands) 뒤에 rig 적용 · 옛 계획(rig 없음)은 건너뜀
  (q-c) 정적: 포즈 모드 [포즈 (P)] · P / Esc · 재생·녹화·프레임 렌더에서 꺼짐 · 몸통 끌기는 아무것도 안 함
  (q-d) 정적: 2관절 IK 해석해(solveTwoBoneIK) · 팔꿈치 뒤·아래 / 무릎 앞 pole · 관절 한계 표(RIG_LIMITS) 하나
  (q-e) 정적: 채널 박스(돌리기·숙이기·기울이기 · ±5° · 이 관절 초기화) · 포즈 초기화 · 좌우 반전 · 안내 한 줄 · 한국어 관절 이름
  (q-f) 정적: 끈 것 = commitEdit 한 단계 · manual · 저장 본문 scenes 안 cut actor 의 rig · dirty 비교도 같은 scenes
  (q-g) 고친 파일 LF
  (q-h) node --check vnew-previz.js · vnew.js (node 없으면 WARN)
  (q-i) node 순수 함수: IK 닿는 목표 1cm 안 + 팔꿈치 뒤로 · 무릎 앞으로 · 못 닿으면 쭉 펴서 그쪽 · 한계 자르기 · 좌우 반전 두 번 = 그대로
  (q-j) node 렌더: rig 없는 옛 계획 모양 그대로 · rig 가 동작·손 위에 덮어씀 · rootY · rig 없는 컷으로 가면 되돌아감 ·
        pose_describe.body_points 와 같은 관절 위치(가져오지 못하면 WARN)
  (q-k) node 편집기: P 켜기 · 고리 누르기 = 관절 고르기 · 끌기 = rig·manual·한 단계·되돌리기 · 기즈모 회전 · IK 손 끌기(커서 따라감,
        팔꿈치 뒤) · Alt 앞뒤 · 골반 끌기(발 그대로) · 한계 · 좌우 반전 두 번 · 포즈 초기화 · Esc · 재생하면 꺼짐
  (q-l) fix 1 node 한계: 경첩 방향(팔꿈치 -x = 아래팔 앞, 무릎 +x = 정강이 뒤) · 자세 6 × 동작 17 × posture · 말하기 · 머리 ·
        손(쪽 × act × target) × 상대 앞/옆/뒤 를 전부 돌려 모든 관절 값이 RIG_LIMITS 안 · 동작·자세·머리는 자르기 없이 안 ·
        서기/앉기 + 앞 상대의 손 닿기(가리키기·상대 머리 빼고)도 자르기 없음 · 얼굴·가슴·무릎 동작의 손이 그 부위에
  (q-m) fix 1 node 첫 편집: 목록 동작(cover_face) 팔꿈치 고리 끌기 첫 값이 그대로 이어짐 · 한계 밖 값은 안쪽으로만 움직이고
        바깥으로는 그 자리 · 채널 박스(다른 축 입력)도 한계 밖 축을 그대로 둠
  (q-n) fix 1 node 작업 시점: 처음 들어가면 그 컷 인물(발·머리)·촬영 카메라가 화면 안, 카메라 쪽에서 내려다봄 · 처음 보는 다른 컷도 ·
        사용자가 궤도를 움직인 뒤에는 컷을 바꿔 들어가도 그 자리 그대로

실행: python -X utf8 -B tools/check_pose_controls_209a.py
"""

from __future__ import annotations

import json
import math
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
POSE_DESCRIBE = ROOT / "app" / "src" / "vnew" / "pose_describe.py"

GROUPS = {
    "q-a": "마네킹 관절 피벗 · RIG_DIMS · pose_describe 치수",
    "q-b": "목록 자세 뒤 rig 적용 · 옛 계획 그대로",
    "q-c": "포즈 모드 켜고 끄기(P · Esc · 재생·녹화)",
    "q-d": "2관절 IK · pole · 관절 한계 표",
    "q-e": "채널 박스 · 포즈 초기화 · 좌우 반전 · 안내",
    "q-f": "한 단계 · manual · 저장 본문에 rig",
    "q-g": "고친 파일 LF",
    "q-h": "node --check 두 파일",
    "q-i": "node 순수 함수: IK · 한계 · 좌우 반전",
    "q-j": "node 렌더: 옛 계획 그대로 · rig 덮어쓰기 · pose_describe 와 같은 위치",
    "q-k": "node 편집기: 고리·IK·골반 끌기 · 한 단계 · Esc · 재생",
    "q-l": "fix 1 node 한계: 경첩 방향 · 모든 목록 자세·동작·손 값이 한계 안 · 손이 얼굴·가슴·무릎에",
    "q-m": "fix 1 node 첫 편집에 값이 튀지 않음(끌기 · 채널 박스)",
    "q-n": "fix 1 node 작업 시점 첫 화면 = 그 컷 인물·카메라 · 움직인 궤도는 그대로",
}
THREE_LINE = 'import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";'
JOINTS = ["spine", "neck", "head", "shoulderL", "elbowL", "wristL", "shoulderR", "elbowR", "wristR",
          "hipL", "kneeL", "ankleL", "hipR", "kneeR", "ankleR"]
# pose_describe.py 상수 → RIG_DIMS 키
DESCRIBE_DIMS = {"SCALE_BASE_M": "baseHeightM", "HIPS_Y": "hipsY", "NECK_Y": "neckY", "HEAD_CENTER_Y": "headCenterY",
                 "UPPER_ARM_M": "upperArm", "FORE_ARM_M": "foreArm", "HIP_X": "hipX", "THIGH_M": "thigh", "SHIN_M": "shin"}
# q-j 의 pose_describe 비교용 rig(모든 관절)
PARITY_RIG = {"spine": [0.2, 0.3, -0.1], "neck": [0.1, -0.2, 0.05], "head": [-0.15, 0.4, 0.1],
              "shoulderL": [-0.8, 0.2, 0.3], "elbowL": [-0.9, 0.1, 0], "wristL": [0.2, 0, 0],
              "shoulderR": [-0.3, -0.4, -0.5], "elbowR": [-0.4, 0, 0.05], "wristR": [0, 0.3, 0],
              "hipL": [-0.6, 0.1, 0.2], "kneeL": [0.9, 0, 0], "ankleL": [0.1, 0, 0],
              "hipR": [0.2, -0.1, -0.1], "kneeR": [0.3, 0.05, 0], "ankleR": [0, 0, 0.1], "rootY": -0.12}
PARITY_POINTS = ["pelvis", "head", "shoulderL", "shoulderR", "handL", "handR", "kneeL", "kneeR", "footL", "footR"]


def _extract_js(source: str, name: str, indent: str = "  ") -> str:
    """function name(...) { ... } 원문(중괄호 짝 맞춤). 없으면 ""."""
    m = re.search(r"\n" + indent + r"(?:export\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    if not m:
        return ""
    i = source.index("{", m.end())
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start() + 1: j + 1]
    return ""


def _method(source: str, name: str) -> str:
    """class 메서드 'name(...) {' 원문(2칸 들여쓰기)."""
    m = re.search(r"\n  " + re.escape(name) + r"\([^)]*\)\s*\{", source)
    if not m:
        return ""
    i = source.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start() + 1: j + 1]
    return ""


def _rig_dims(pjs: str) -> dict:
    m = re.search(r"export const RIG_DIMS = Object\.freeze\(\{(.*?)\}\);", pjs, re.S)
    if not m:
        return {}
    return {k: float(v) for k, v in re.findall(r"(\w+):\s*(-?[\d.]+)", m.group(1))}


def _rig_limits(pjs: str) -> dict:
    m = re.search(r"export const RIG_LIMITS = Object\.freeze\(\{(.*?)\n\}\);", pjs, re.S)
    if not m:
        return {}
    out = {}
    for name, body in re.findall(r"(\w+): \[(\[.*?\])\],", m.group(1)):
        out[name] = json.loads("[" + body + "]")
    return out


def _describe_consts() -> dict:
    """pose_describe.py 의 치수 상수(정규식 — 가져오지 않는다). 파일이 없으면 {}."""
    if not POSE_DESCRIBE.exists():
        return {}
    text = POSE_DESCRIBE.read_text(encoding="utf-8")
    out = {k: float(v) for k, v in re.findall(r"^([A-Z_]+) = (-?[\d.]+)\s*$", text, re.M)}
    sh = re.search(r"^SHOULDER = \((-?[\d.]+), (-?[\d.]+)\)", text, re.M)
    if sh:
        out["SHOULDER_X"], out["SHOULDER_Y"] = float(sh.group(1)), float(sh.group(2))
    return out


def _parity_points() -> tuple[dict | None, str]:
    """pose_describe.body_points(원점, 정면, PARITY_RIG). 가져오지 못하면 (None, 이유)."""
    try:
        if str(ROOT / "app") not in sys.path:
            sys.path.insert(0, str(ROOT / "app"))
        from src.vnew import pose_describe  # noqa: PLC0415
        pts = pose_describe.body_points([0, 0, 0], 0.0, PARITY_RIG, 1.7)
        return {k: [round(float(x), 4) for x in pts[k]] for k in PARITY_POINTS}, ""
    except Exception as exc:  # noqa: BLE001
        return None, repr(exc)[:200]


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
  getHelper() { return this.helper; } attach(o) { this.object = o; } detach() { this.object = null; } setMode(m) { this.mode = m; } setSpace() {}
  setTranslationSnap() {} setRotationSnap() {} setSize(s) { this.size = s; } dispose() {}
}
class FakeOrbit extends THREE.EventDispatcher {
  constructor(cam) { super(); this.object = cam; this.target = new THREE.Vector3(); this.enabled = true; }
  update() {} dispose() {}
}
const fs = await import("node:fs");
const mod = await import(new URL("./previz209.mjs", import.meta.url));
const cfg = JSON.parse(fs.readFileSync(new URL("./cfg.json", import.meta.url), "utf8"));
const D = Math.PI / 180;
const r4 = (a) => a.map((x) => Math.round(x * 10000) / 10000);
const wp = (o) => r4(o.getWorldPosition(new THREE.Vector3()).toArray());
const out = {};

// ---- (i) 순수 함수 ----
{
  const fk = (res, a, b) => {
    const m1 = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(res.root[0], res.root[1], res.root[2], "XYZ"));
    const m2 = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(res.mid[0], res.mid[1], res.mid[2], "XYZ"));
    const mid = new THREE.Vector3(0, -a, 0).applyMatrix4(m1);
    const end = mid.clone().add(new THREE.Vector3(0, -b, 0).applyMatrix4(new THREE.Matrix4().multiplyMatrices(m1, m2)));
    return { mid, end };
  };
  const off = (p, end) => { // 가운데 관절이 뿌리→끝 직선에서 벗어난 방향
    const d = end.clone().normalize();
    return p.clone().sub(d.multiplyScalar(p.dot(d)));
  };
  const t = new THREE.Vector3(0.05, -0.25, 0.3);
  const arm = mod.solveTwoBoneIK(t, 0.3, 0.29, new THREE.Vector3(...mod.RIG_IK_POLES.arm), -1, 150 * D);
  const a = fk(arm, 0.3, 0.29);
  const legT = new THREE.Vector3(0, -0.7, 0.25);
  const leg = mod.solveTwoBoneIK(legT, 0.46, 0.44, new THREE.Vector3(...mod.RIG_IK_POLES.leg), 1, 172 * D);
  const l = fk(leg, 0.46, 0.44);
  const far = new THREE.Vector3(0.2, 0.1, 1.5);
  const st = mod.solveTwoBoneIK(far, 0.3, 0.29, new THREE.Vector3(...mod.RIG_IK_POLES.arm), -1, 150 * D);
  const s = fk(st, 0.3, 0.29);
  out.ik = {
    errCm: Math.round(a.end.distanceTo(t) * 1000) / 10, reached: arm.reached, elbowFlexNeg: arm.mid[0] < -0.05,
    elbowHinge: arm.mid[1] === 0 && arm.mid[2] === 0, elbowBack: off(a.mid, a.end).z < -0.01,
    legErrCm: Math.round(l.end.distanceTo(legT) * 1000) / 10, kneeFlexPos: leg.mid[0] > 0.05, kneeFwd: off(l.mid, l.end).z > 0.01,
    farReached: st.reached, farStraight: Math.abs(st.mid[0]) < 1e-6, farAngleDeg: Math.round(s.end.angleTo(far) / D * 100) / 100,
    farLen: Math.round(s.end.length() * 1000) / 1000,
  };
  const c = mod.clampRigJoint;
  out.clamp = {
    head: r4(c("head", [2, -2, 0])), kneeL: r4(c("kneeL", [-0.5, 0.2, 0])), elbowR: r4(c("elbowR", [0.4, 0, 0])),
    inside: r4(c("spine", [0.3, -0.2, 0.1])), unknown: r4(c("tail", [5, -5, "x"])),
  };
  const rig = { shoulderL: [0.1, 0.2, 0.3], elbowR: [-0.5, 0.1, 0], spine: [0.1, 0.2, 0.3], kneeL: [0.4, 0, 0], rootY: -0.2 };
  const once = mod.mirrorRig(rig);
  out.mirror = { once, twice: JSON.stringify(mod.mirrorRig(once)) === JSON.stringify(rig) || JSON.stringify(sortKeys(mod.mirrorRig(once))) === JSON.stringify(sortKeys(rig)) };
  out.dims = mod.RIG_DIMS;
}
function sortKeys(o) { const r = {}; Object.keys(o).sort().forEach((k) => { r[k] = o[k]; }); return r; }

// ---- (j) 렌더 ----
{
  const scene = (cuts, actors) => ({ room: { width: 6, depth: 6, height: 3, walls: {} }, actors: actors || [{ id: "a", name: "A", heightM: 1.7, color: "#f00" }],
    props: [], light: {}, timeline: cuts });
  const cam = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 200);
  const shot = { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 40, manual: true };
  const look = (ca, t) => {
    const sc = new mod.PrevizScene(scene([{ cutId: "c1", start: 0, end: 1, camera: shot, actors: [ca] }]), null, null);
    sc.update(t || 0.5, cam);
    const m = sc.actors.a.mannequin;
    return { head: wp(m.head), handL: wp(m.shoulderL.hand), handR: wp(m.shoulderR.hand), ankleL: wp(m.hipL.ankle), pelvis: m.pelvis.position.y,
      hips: wp(m.hips), spineX: r4([m.spine.rotation.x])[0], m };
  };
  const stand = look({ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" });
  const sit = look({ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "sit" });
  const joints = {};
  ["spine", "neck", "head", "shoulderL", "elbowL", "wristL", "hipL", "kneeL", "ankleL"].forEach((j) => { joints[j] = !!stand.m.rigObject(j); });
  out.old = { stand: { head: stand.head, handL: stand.handL, ankleL: stand.ankleL, pelvis: stand.pelvis }, sit: { ankleL: sit.ankleL, hips: sit.hips },
    joints, headParent: stand.m.head.parent === stand.m.headJoint, headJointAtNeck: stand.m.headJoint.position.length() === 0 };
  // 동작(bow) + 손(own_face) 위에 rig 가 덮어쓴다 · rootY · 다음 컷(rig 없음)은 되돌아감
  const rig = { spine: [0.2, 0, 0], shoulderR: [-1, 0, 0], elbowR: [-0.5, 0, 0], kneeL: [1, 0, 0], rootY: -0.3 };
  const base = { id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand", gesture: "bow", hands: { right: { act: "touch", target: "own_face" } } };
  const sc = new mod.PrevizScene(scene([
    { cutId: "c1", start: 0, end: 1, camera: shot, actors: [Object.assign({}, base, { rig })] },
    { cutId: "c2", start: 1, end: 2, camera: shot, actors: [Object.assign({}, base)] },
  ]), null, null);
  sc.update(0.5, cam);
  const m = sc.actors.a.mannequin;
  const on = { spineX: m.spine.rotation.x, shoulderR: r4(m.shoulderR.pivot.rotation.toArray().slice(0, 3)), elbowR: r4(m.shoulderR.elbow.rotation.toArray().slice(0, 3)),
    kneeL: m.hipL.elbow.rotation.x, kneeR: m.hipR.elbow.rotation.x, hipsY: wp(m.hips)[1] };
  sc.update(1.5, cam);
  const off = { spineX: r4([m.spine.rotation.x])[0], kneeL: m.hipL.elbow.rotation.x, pelvis: m.pelvis.position.y, handTouch: m.shoulderR.pivot.rotation.x !== -1 };
  out.rig = { on, off };
  // pose_describe 와 같은 관절 위치(모든 관절이 rig 에 있는 자세)
  const pr = look({ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand", rig: cfg.parityRig });
  const pm = pr.m;
  const sh = (arm) => arm.pivot;
  out.parity = { pelvis: wp(pm.hips), head: wp(pm.head), shoulderL: wp(sh(pm.shoulderL)), shoulderR: wp(sh(pm.shoulderR)),
    handL: wp(pm.shoulderL.hand), handR: wp(pm.shoulderR.hand), kneeL: wp(pm.hipL.elbow), kneeR: wp(pm.hipR.elbow),
    footL: wp(pm.hipL.ankle), footR: wp(pm.hipR.ankle) };
}

// ---- (k) 편집기 ----
{
  const sd = { room: { width: 6, depth: 6, height: 3, walls: {} },
    actors: [{ id: "a", name: "A", heightM: 1.7, color: "#ff0000" }, { id: "b", name: "B", heightM: 1.7, color: "#00ff00" }],
    props: [], light: {},
    timeline: [{ cutId: "c1", start: 0, end: 2, camera: { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 40 },
      actors: [{ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" }, { id: "b", pos: [2, 0, -2], facingDeg: 180, pose: "stand" }] }] };
  const canvas = new FakeEl("canvas");
  const renderer = { domElement: canvas, shadowMap: {}, setPixelRatio() {}, setSize() {}, dispose() {}, render() {} };
  const ctl = mod.createController(new FakeEl("div"), { scenes: [sd], renderer, controls: { OrbitControls: FakeOrbit, TransformControls: FakeTC }, width: 1280, height: 720 });
  await ctl.ready();
  const E = ctl._editor;
  const c1 = sd.timeline[0];
  const A = () => c1.actors[0]; // 되돌리기는 컷 안 물체를 새로 만든다
  const ev = { live: 0, commit: 0, pose: 0 };
  ctl.onEdit((e) => { if (e.kind === "live") ev.live += 1; else if (e.kind === "commit") ev.commit += 1; });
  ctl.onPose(() => { ev.pose += 1; });
  const upd = () => { E.helpers.parent.updateMatrixWorld(true); E.shotCamera.updateMatrixWorld(true); };
  const scr = (v) => { const p = v.clone().project(E.shotCamera); return [(p.x * 0.5 + 0.5) * 1280, (-p.y * 0.5 + 0.5) * 720]; };
  const ptr = (type, x, y, extra) => E.canvas.dispatch(type, Object.assign({ clientX: x, clientY: y, button: 0, pointerId: 1, pointerType: "mouse",
    shiftKey: false, altKey: false, preventDefault() {} }, extra || {}));
  const key = (code, k) => E.stage.dispatch("keydown", { code, key: k, target: E.stage, preventDefault() {} });
  const drag = (x, y, dx, dy, extra) => { upd(); ptr("pointerdown", x, y, extra); ptr("pointermove", x + dx / 2, y + dy / 2, extra);
    ptr("pointermove", x + dx, y + dy, extra); ptr("pointerup", x + dx, y + dy, extra); };
  const m = () => E.scene.actors.a.mannequin;
  const k = {};
  const ringPt = (joint) => { const r = E.poseControls.rings.find((x) => x.joint === joint);
    return scr(r.mesh.localToWorld(new THREE.Vector3(0, r.mesh.geometry.parameters.radius, 0))); }; // 고리의 앞쪽 점
  key("KeyP", "p");
  k.noSel = ctl.poseMode;
  ctl.select({ type: "actor", id: "a" });
  key("KeyP", "ㅔ");
  upd();
  k.on = { on: ctl.poseMode, visible: E.poseGroup.visible, rings: E.poseControls.rings.map((r) => r.joint).sort(),
    handles: E.poseControls.handles.map((h) => h.name).sort(), events: ev.pose, state: ctl.state().pose };
  // 머리 고리(앞쪽 점) 누르기 = 머리 고르기 · 가로 50px = 돌리기 0.5rad · 한 단계 · manual
  upd();
  const [hx, hy] = ringPt("head");
  const pick = ctl.pickAt(hx, hy);
  const eye0 = wp(m().eyeMeshes[0]);
  const undo0 = ctl.canUndo;
  drag(hx, hy, 50, 0);
  k.fk = { joint: ctl.poseJoint, head: A().rig && r4(A().rig.head), manual: !!A().manual, commit: ev.commit, live: ev.live >= 1, undo0,
    childMoved: JSON.stringify(wp(m().eyeMeshes[0])) !== JSON.stringify(eye0), posSame: JSON.stringify(A().pos) === "[0,0,0]",
    camSame: JSON.stringify(c1.camera.pos) === "[0,1.5,4]", pickActor: pick && pick.type };
  ctl.undo();
  k.fkUndo = { rig: A().rig === undefined, manual: !!A().manual };
  // 세로 = 숙이기(x), Shift = 기울이기(z)
  upd();
  const [nx, ny] = ringPt("head");
  drag(nx, ny, 0, 30);
  k.nod = A().rig && r4(A().rig.head);
  upd();
  const [sx, sy] = ringPt("head");
  drag(sx, sy, 20, 0, { shiftKey: true });
  k.tilt = A().rig && r4(A().rig.head);
  // 회전(E) 기즈모로 고른 관절 돌리기
  ctl.setTool("rotate");
  const tc = FakeTC.last;
  k.gizmo = { attached: tc.object === E.proxy, at: r4(E.proxy.position.toArray()), headPivot: wp(m().headJoint) };
  ctl.selectJoint("neck");
  E.proxy.quaternion.copy(m().neck.getWorldQuaternion(new THREE.Quaternion()).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0.3, 0))));
  tc.dispatchEvent({ type: "objectChange" });
  k.gizmo.neck = A().rig && A().rig.neck && r4(A().rig.neck);
  ctl.setTool("select");
  // IK: 오른손 네모를 60px 위로 → 손이 커서를 따라감 · 어깨·팔꿈치 FK 값 · 팔꿈치는 뒤로(-x 경첩)
  upd();
  const hand = E.poseControls.handles.find((h) => h.name === "handR");
  const [ux, uy] = scr(hand.mesh.position);
  const handY0 = wp(m().shoulderR.hand)[1];
  ev.commit = 0;
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(E.shotCamera.getWorldDirection(new THREE.Vector3()), hand.mesh.position.clone());
  const rc = new THREE.Raycaster();
  rc.setFromCamera(new THREE.Vector2((ux / 1280) * 2 - 1, -((uy - 60) / 720) * 2 + 1), E.shotCamera);
  const want = rc.ray.intersectPlane(plane, new THREE.Vector3());
  drag(ux, uy, 0, -60);
  upd();
  const handNow = m().shoulderR.hand.getWorldPosition(new THREE.Vector3());
  const [fx, fy] = scr(handNow);
  const elbowW = m().shoulderR.elbow.getWorldPosition(new THREE.Vector3());
  const shW = m().shoulderR.pivot.getWorldPosition(new THREE.Vector3());
  k.ik = { up: Math.round((handNow.y - handY0) * 100) / 100, errCm: Math.round(handNow.distanceTo(want) * 1000) / 10, cursorPx: Math.round(Math.hypot(fx - ux, fy - (uy - 60)) * 10) / 10,
    shoulder: !!(A().rig && A().rig.shoulderR), elbowX: A().rig && A().rig.elbowR ? A().rig.elbowR[0] : null,
    elbowHinge: !!(A().rig && A().rig.elbowR && A().rig.elbowR[1] === 0 && A().rig.elbowR[2] === 0),
    elbowBehind: elbowW.z < Math.min(shW.z, handNow.z) - 0.01, commit: ev.commit, onlyA: !c1.actors[1].rig && !c1.actors[1].manual };
  // Alt+세로 = 앞뒤(위로 끌면 카메라에서 멀어진다 = z 가 줄어든다)
  upd();
  const [ax, ay] = scr(hand.mesh.position);
  const z0 = handNow.z;
  drag(ax, ay, 0, -30, { altKey: true });
  k.ik.altBack = m().shoulderR.hand.getWorldPosition(new THREE.Vector3()).z < z0 - 0.03;
  // 손 네모를 누르기만 = 팔꿈치 고르기
  upd();
  const [bx, by] = scr(hand.mesh.position);
  ptr("pointerdown", bx, by); ptr("pointerup", bx, by);
  k.ik.clickJoint = ctl.poseJoint;
  // 골반 네모(앞쪽 막대) 60px 아래 → rootY < 0 · 발은 바닥 그 자리 · 무릎이 앞으로 굽음
  upd();
  const hipsW = m().hips.getWorldPosition(new THREE.Vector3());
  const [px, py] = scr(hipsW.clone().add(new THREE.Vector3(0, 0, 0.17)));
  const footL0 = wp(m().hipL.ankle);
  const footR0 = wp(m().hipR.ankle);
  drag(px, py, 0, 60);
  const footL = wp(m().hipL.ankle);
  const footR = wp(m().hipR.ankle);
  const moved = (p, q) => Math.round(Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) * 1000) / 1000;
  k.root = { joint: ctl.poseJoint, rootY: A().rig ? A().rig.rootY : null, footL: moved(footL, footL0), footR: moved(footR, footR0),
    kneeL: A().rig && A().rig.kneeL ? A().rig.kneeL[0] > 0.2 : false, kneeFwd: wp(m().hipL.elbow)[2] > footL[2] + 0.02 };
  // 몸통을 끌면 아무것도 바뀌지 않는다(인물·카메라 그대로)
  const before = JSON.stringify(c1);
  const [tx, ty] = scr(new THREE.Vector3(0, 1.12, 0.2));
  drag(tx, ty, 80, 0);
  k.bodyStill = JSON.stringify(c1) === before;
  // 패널 API: 한계 · 좌우 반전 두 번 · 이 관절 초기화 · 포즈 초기화
  ctl.setJointDeg("a", "kneeR", [-30, 0, 0], true);
  ctl.setJointDeg("a", "head", [0, 120, 0], true);
  k.limits = { kneeR: A().rig.kneeR[0], headY: Math.round(A().rig.head[1] / D) };
  k.deg = ctl.jointDeg("a", "head");
  const shR = A().rig.shoulderR.slice();
  ctl.mirrorPose("a");
  const once = { shoulderL: r4(A().rig.shoulderL), mirroredFromR: r4([shR[0], -shR[1], -shR[2]]) };
  const rig1 = JSON.stringify(sortKeys(A().rig));
  ctl.mirrorPose("a");
  const rig2 = JSON.stringify(sortKeys(A().rig));
  ctl.mirrorPose("a");
  k.mirror = { once, changed: rig2 !== rig1, twice: JSON.stringify(sortKeys(A().rig)) === rig1 };
  ctl.resetJoint("a", "head");
  k.resetJoint = A().rig.head === undefined;
  const steps0 = ctl.canUndo;
  ctl.resetPose("a");
  k.resetPose = { rig: A().rig === undefined, manual: !!A().manual, canUndo: steps0 && ctl.canUndo };
  ctl.undo();
  k.resetPose.undoBack = !!A().rig;
  // Esc = 포즈 모드 끝(고른 인물은 그대로) · P 다시 · 재생하면 꺼짐 · 다른 걸 고르면 꺼짐
  key("Escape", "Escape");
  k.esc = { on: ctl.poseMode, sel: ctl.selection, visible: (upd(), ctl.refresh(), E.poseGroup.visible) };
  key("KeyP", "p");
  const onAgain = ctl.poseMode;
  ctl.play();
  k.play = { onAgain, off: !ctl.poseMode };
  ctl.pause();
  key("KeyP", "p");
  ctl.select({ type: "camera" });
  k.otherSel = ctl.poseMode;
  ctl.destroy();
  out.editor = k;
}

// ---- (l) fix 1: 경첩 방향 · 모든 목록 자세·동작·손 값이 한계 안 · 얼굴·가슴·무릎 동작의 손 ----
{
  const room = { width: 8, depth: 8, height: 3, walls: {} };
  const two = (aCa, bPos) => ({ room, actors: [{ id: "a", name: "A", heightM: 1.7 }, { id: "b", name: "B", heightM: 1.6 }], props: [], light: {},
    timeline: [{ cutId: "c", start: 0, end: 9, camera: { pos: [0, 1.5, 4], target: [0, 1, 0], fov: 40 },
      actors: [Object.assign({ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" }, aCa), { id: "b", pos: bPos || [0, 0, 0.7], facingDeg: 180, pose: "stand" }] }] });
  const cam = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  const W = (o) => o.getWorldPosition(new THREE.Vector3());
  // 경첩: 쉬는 팔·다리에 rig 로 굽혀 본다(인물은 +z 를 본다).
  const bend = (rig) => { const sc = new mod.PrevizScene(two({ rig }), null, null); sc.update(0.5, cam); sc.root.updateMatrixWorld(true); return sc.actors.a.mannequin; };
  const fl = bend({ elbowL: [-0.6, 0, 0], kneeL: [0.6, 0, 0] });
  const hx = bend({ elbowL: [0.6, 0, 0], kneeL: [-0.6, 0, 0] });
  const hinge = { elbowNegFwd: W(fl.shoulderL.hand).z - W(fl.shoulderL.elbow).z > 0.1, kneePosBack: W(fl.hipL.ankle).z - W(fl.hipL.elbow).z < -0.1,
    elbowPosBack: W(hx.shoulderL.hand).z - W(hx.shoulderL.elbow).z < -0.1, kneeNegFwd: W(hx.hipL.ankle).z - W(hx.hipL.elbow).z > 0.1 };
  // 전부 돌리기
  const GESTURES = [null].concat(cfg.gestures);
  const POSES = ["stand", "sit", "seat", "kneel", "lie", "walk"];
  const POSTURES = [null].concat(cfg.postures);
  const OTHERS = [[0, 0, 0.7], [0.7, 0, 0], [-0.5, 0, -0.6]]; // 앞 · 왼쪽 옆 · 뒤
  const sd = two({}, OTHERS[0]);
  const sc = new mod.PrevizScene(sd, null, null);
  const [A, B] = sd.timeline[0].actors;
  const bad = [];
  const clamped = [];
  let n = 0;
  const run = (label) => {
    for (const t of [0.3, 1.3]) {
      sc.update(t, cam);
      n += 1;
      const m = sc.actors.a.mannequin;
      if (m.clamped) clamped.push(label);
      cfg.joints.forEach((j) => {
        const lim = mod.RIG_LIMITS[j];
        m.readJoint(j).forEach((x, i) => {
          if (x < lim[i][0] * D - 1e-6 || x > lim[i][1] * D + 1e-6) bad.push(`${label} t${t} ${j}[${i}]=${Math.round(x / D)}`);
        });
      });
    }
  };
  OTHERS.forEach((other, oi) => {
    B.pos = other;
    POSES.forEach((p) => {
      A.pose = p === "seat" ? "sit" : p;
      if (p === "seat") A.sit = { hipY: 0.45, legPitchDeg: 75, legYawDeg: 120 }; else delete A.sit;
      GESTURES.forEach((g) => {
        A.gesture = g; A.gestureTarget = "b";
        POSTURES.forEach((post) => [false, true].forEach((speaking) => {
          A.posture = post; A.speaking = speaking; run(`list|${p}|${g}|${post}|${speaking}|${oi}`);
        }));
        A.posture = "lean_back"; A.speaking = true;
        cfg.headTurns.forEach((turn) => cfg.headTilts.forEach((tilt) => { A.head = { turn, tilt }; run(`list|${p}|${g}|head ${turn} ${tilt}|${oi}`); }));
        delete A.head; delete A.posture; A.speaking = false;
      });
      A.gesture = null;
      ["left", "right"].forEach((side) => cfg.acts.forEach((act) => cfg.targets.forEach((target) => {
        A.hands = { [side]: { act, target } }; run(`hands|${p}|${act}|${target}|${oi}`);
      })));
      delete A.hands;
    });
  });
  // 서기·앉기 + 앞 상대에게 닿는 손(가리키기·상대 머리 위 빼고)은 자르기 없이 한계 안이어야 한다.
  const reachClamped = clamped.filter((s) => { const [kind, p, act, target, oi] = s.split("|");
    return kind === "hands" && (p === "stand" || p === "sit") && oi === "0" && act !== "point" && target !== "other_head"; });
  // 손 닿기(인물 a, 앞 상대 b)
  const reach = {};
  const handAt = (ca, fn) => { const s = new mod.PrevizScene(two(ca), null, null); s.update(0.5, cam); s.root.updateMatrixWorld(true);
    const m = s.actors.a.mannequin; return fn(m, W(m.shoulderL.hand), W(m.shoulderR.hand)); };
  const cm = (a, b) => Math.round(a.distanceTo(b) * 1000) / 10;
  reach.coverFace = handAt({ gesture: "cover_face" }, (m, l, r) => [cm(l, m.bodyPoint("face")), cm(r, m.bodyPoint("face"))]);
  reach.ownCheek = handAt({ gesture: "hand_to_own_face" }, (m, l, r) => cm(r, m.bodyPoint("cheek", "right")));
  reach.tears = handAt({ gesture: "wipe_tears" }, (m, l, r) => cm(r, m.bodyPoint("eyes")));
  reach.chest = handAt({ gesture: "hand_on_chest" }, (m, l, r) => cm(r, m.bodyPoint("chest")));
  reach.knees = handAt({ gesture: "hands_on_knees", pose: "sit" }, (m, l, r) => [cm(l, m.bodyPoint("knee", "left")), cm(r, m.bodyPoint("knee", "right"))]);
  reach.elbowX = ["cover_face", "hand_to_own_face", "wipe_tears", "hand_on_chest", "hand_on_other_cheek", "both_hands_other_cheeks",
    "press_hand_to_other_chest", "hands_on_knees"].map((g) => handAt({ gesture: g }, (m) => Math.max(m.readJoint("elbowL")[0], m.readJoint("elbowR")[0]) <= 0));
  out.limits = { hinge, n, badCount: bad.length, bad: bad.slice(0, 4), listClamped: clamped.filter((s) => s.indexOf("list|") === 0).slice(0, 4),
    reachClamped: reachClamped.slice(0, 4), reach };
}

// ---- (m)(n) fix 1: 첫 편집에 값이 튀지 않음 · 작업 시점 첫 화면 ----
{
  const sd = { room: { width: 12, depth: 12, height: 3, walls: {} },
    actors: [{ id: "a", name: "A", heightM: 1.7, color: "#ff0000" }, { id: "b", name: "B", heightM: 1.6, color: "#00ff00" }],
    props: [], light: {},
    timeline: [
      { cutId: "c1", start: 0, end: 2, camera: { pos: [1, 1.6, 2], target: [3.5, 1.2, -2.5], fov: 40 },
        actors: [{ id: "a", pos: [3, 0, -2], facingDeg: 200, pose: "stand", gesture: "cover_face" }, { id: "b", pos: [4, 0, -3], facingDeg: 20, pose: "stand" }] },
      { cutId: "c2", start: 2, end: 4, camera: { pos: [-1, 1.6, -1], target: [-3.5, 1.2, 2.5], fov: 40 },
        actors: [{ id: "a", pos: [-3, 0, 2], facingDeg: 30, pose: "stand" }, { id: "b", pos: [-4, 0, 3], facingDeg: 210, pose: "stand" }] },
      { cutId: "c3", start: 4, end: 6, camera: { pos: [0, 1.6, 4], target: [0, 1.2, 0], fov: 40 },
        actors: [{ id: "a", pos: [0, 0, 0], facingDeg: 0, pose: "stand" }, { id: "b", pos: [1, 0, -1], facingDeg: 180, pose: "stand" }] }] };
  const canvas = new FakeEl("canvas");
  const renderer = { domElement: canvas, shadowMap: {}, setPixelRatio() {}, setSize() {}, dispose() {}, render() {} };
  const ctl = mod.createController(new FakeEl("div"), { scenes: [sd], renderer, controls: { OrbitControls: FakeOrbit, TransformControls: FakeTC }, width: 1280, height: 720 });
  await ctl.ready();
  const E = ctl._editor;
  const c1 = sd.timeline[0];
  const A = () => c1.actors[0];
  const m = () => E.scene.actors.a.mannequin;
  const upd = () => { E.helpers.parent.updateMatrixWorld(true); E.shotCamera.updateMatrixWorld(true); };
  const scr = (v) => { const p = v.clone().project(E.shotCamera); return [(p.x * 0.5 + 0.5) * 1280, (-p.y * 0.5 + 0.5) * 720]; };
  const ptr = (type, x, y) => E.canvas.dispatch(type, { clientX: x, clientY: y, button: 0, pointerId: 1, pointerType: "mouse", shiftKey: false, altKey: false, preventDefault() {} });
  const drag = (x, y, dx, dy) => { upd(); ptr("pointerdown", x, y); ptr("pointermove", x + dx / 2, y + dy / 2); ptr("pointermove", x + dx, y + dy); ptr("pointerup", x + dx, y + dy); };
  const ringPt = (joint) => { upd(); const r = E.poseControls.rings.find((x) => x.joint === joint);
    return scr(r.mesh.localToWorld(new THREE.Vector3(0, r.mesh.geometry.parameters.radius, 0))); };
  const nm = {};
  ctl.seek(0.5);
  ctl.select({ type: "actor", id: "a" });
  ctl.setPoseMode(true);
  upd();
  ctl.refresh();
  // 목록 동작(cover_face) 그대로에서 팔꿈치 고리를 위로 10px = 숙이기 -0.1 만(다른 축은 그대로)
  const start = m().readJoint("elbowR");
  const [ex, ey] = ringPt("elbowR");
  drag(ex, ey, 0, -10);
  nm.listJoint = ctl.poseJoint;
  nm.listDelta = A().rig && A().rig.elbowR ? r4(A().rig.elbowR.map((v, i) => v - start[i])) : null;
  nm.listStartInside = start[0] <= 0;
  // 한계 밖 값(팔꿈치 +0.6 = 뒤로 꺾임): 바깥(+)으로 끌면 그 자리, 안쪽(-)으로 끌면 그만큼만
  A().rig = { elbowR: [0.6, 0, 0] };
  ctl.refresh();
  let [ox, oy] = ringPt("elbowR");
  drag(ox, oy, 0, 20);
  nm.outward = r4(A().rig.elbowR);
  [ox, oy] = ringPt("elbowR");
  drag(ox, oy, 0, -30);
  nm.inward = r4(A().rig.elbowR);
  // 채널 박스: 한계 밖 x 를 둔 채 y 만 바꿈 → x 그대로 · x 를 더 바깥으로 입력 → 그 자리 · 안쪽 입력 → 그 값
  A().rig = { elbowR: [0.6, 0, 0] };
  ctl.refresh();
  ctl.setJointDeg("a", "elbowR", [0.6 / D, 20, 0], true);
  nm.boxOther = r4(A().rig.elbowR);
  ctl.setJointDeg("a", "elbowR", [60, 20, 0], true);
  nm.boxOut = r4(A().rig.elbowR);
  ctl.setJointDeg("a", "elbowR", [-40, 20, 0], true);
  nm.boxIn = r4(A().rig.elbowR);
  ctl.setPoseMode(false);
  out.noSnap = nm;
  // (n) 작업 시점: 그 컷의 인물(발·머리)·촬영 카메라가 화면 안 · 카메라 쪽에서 내려다봄
  const view = (cut) => {
    const wc = E.workCamera;
    wc.updateMatrixWorld(true);
    wc.updateProjectionMatrix();
    const pts = [];
    cut.actors.forEach((ca) => { pts.push(new THREE.Vector3(ca.pos[0], 0, ca.pos[2]), new THREE.Vector3(ca.pos[0], 1.7, ca.pos[2])); });
    pts.push(new THREE.Vector3(...cut.camera.pos));
    const inside = pts.every((p) => { const q = p.clone().project(wc); return Math.abs(q.x) <= 1 && Math.abs(q.y) <= 1 && q.z > -1 && q.z < 1; });
    const c = E.orbit.target.clone();
    const camSide = wc.position.clone().sub(c).dot(new THREE.Vector3(...cut.camera.pos).sub(new THREE.Vector3(...cut.camera.target))) > 0;
    return { inside, camSide, above: wc.position.y > c.y + 0.5, view: ctl.viewMode };
  };
  const wv = {};
  ctl.seek(0.5);
  ctl.setViewMode("work");
  wv.c1 = view(sd.timeline[0]);
  ctl.setViewMode("shot");
  ctl.seek(3);
  ctl.setViewMode("work");
  wv.c2 = view(sd.timeline[1]);
  // 사용자가 궤도를 움직였다(OrbitControls start) → 처음 보는 c3 로 가도 그 자리
  E.orbit.dispatchEvent({ type: "start" });
  E.workCamera.position.set(9, 9, 9);
  ctl.setViewMode("shot");
  ctl.seek(5);
  ctl.setViewMode("work");
  wv.kept = r4(E.workCamera.position.toArray());
  ctl.destroy();
  out.workView = wv;
}
console.log(JSON.stringify(out));
"""


def _js_strings(pjs: str, name: str) -> list[str]:
    """vnew-previz.js 의 `const NAME = [ "...", ... ];` 문자열 목록(q-l 이 그 목록을 전부 돌린다)."""
    m = re.search(r"const " + name + r" = \[(.*?)\];", pjs, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def _list_cfg(pjs: str) -> dict:
    return {"gestures": _js_strings(pjs, "GESTURES"), "postures": _js_strings(pjs, "POSE_POSTURES"), "joints": _js_strings(pjs, "RIG_JOINTS"),
            "acts": _js_strings(pjs, "HAND_ACTS"), "targets": _js_strings(pjs, "HAND_TARGETS"), "headTurns": _js_strings(pjs, "HEAD_TURNS"),
            "headTilts": _js_strings(pjs, "HEAD_TILTS")}


def _run_node(node: str, pjs: str) -> tuple[dict, str]:
    three = (PUBLIC / "vendor" / "three" / "three.module.min.js").resolve().as_uri()
    if THREE_LINE not in pjs:
        return {}, "vnew-previz.js 의 three import 줄을 찾지 못했습니다"
    work = Path(tempfile.mkdtemp(prefix="chk209a-"))
    try:
        (work / "previz209.mjs").write_text(pjs.replace(THREE_LINE, f'import * as THREE from "{three}";')
                                            + "\nexport { PrevizScene, createController };\n", encoding="utf-8", newline="\n")
        (work / "cfg.json").write_text(json.dumps({"parityRig": PARITY_RIG, **_list_cfg(pjs)}), encoding="utf-8")
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


def check_pose_controls_209a() -> bool:
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
    dims = _rig_dims(pjs)

    try:  # (q-a)
        ctor = _method(pjs, "constructor")
        rig_obj = _method(pjs, "rigObject")
        want_dims = {"baseHeightM": 1.7, "hipsY": 0.9, "neckY": 0.6, "headCenterY": 0.13, "shoulderX": 0.22, "shoulderY": 0.52,
                     "upperArm": 0.3, "foreArm": 0.29, "hipX": 0.1, "thigh": 0.46, "shin": 0.44}
        expect("q-a", [missing(ctor, {"pelvis": ("this.pelvis = new THREE.Group();", "this.pelvis.add(this.hips);"),
                                      "headJoint": ("this.headJoint = new THREE.Group();", "this.headJoint.add(head);"),
                                      "dims": ("RIG_DIMS.hipsY", "RIG_DIMS.neckY", "RIG_DIMS.headCenterY", "RIG_DIMS.shoulderX", "RIG_DIMS.hipX")}),
                       missing(_method(pjs, "_makeArm"), {"arm": ("RIG_DIMS.shoulderY", "-RIG_DIMS.upperArm", "-FORE_ARM_M")}),
                       missing(_method(pjs, "_makeLeg"), {"leg": ("-RIG_DIMS.thigh", "-RIG_DIMS.shin", "return { pivot, elbow: knee, ankle };")}),
                       [j for j in JOINTS if f'case "{j}":' not in rig_obj], dims == want_dims,
                       "const UPPER_ARM_M = RIG_DIMS.upperArm;" in pjs and "const FORE_ARM_M = RIG_DIMS.foreArm;" in pjs,
                       pjs.count("export const RIG_DIMS")],
               [[], [], [], [], True, True, 1], "피벗 15개 + 골반 · 머리 관절 · 발목 · RIG_DIMS 하나(치수는 거기서만)")
        consts = _describe_consts()
        if not consts:
            warns.append("pose_describe.py 가 없어 치수 비교(q-a)는 RIG_DIMS 고정값으로만 했습니다")
        else:
            got = {k: consts.get(k) for k in DESCRIBE_DIMS}
            got["SHOULDER_X"], got["SHOULDER_Y"] = consts.get("SHOULDER_X"), consts.get("SHOULDER_Y")
            want = {k: dims.get(v) for k, v in DESCRIBE_DIMS.items()}
            want["SHOULDER_X"], want["SHOULDER_Y"] = dims.get("shoulderX"), dims.get("shoulderY")
            expect("q-a", got, want, "pose_describe.py 치수 = RIG_DIMS")
    except Exception as exc:  # noqa: BLE001
        crash("q-a", exc)

    try:  # (q-b)
        update = _method(pjs, "update")
        hands_at = update.find("entry.mannequin.applyHands(")
        rig_at = update.find("entry.mannequin.applyRig(ca.rig)")
        apply_rig = _method(pjs, "applyRig")
        apply_fn = _method(pjs, "apply")
        expect("q-b", [hands_at > 0 and rig_at > hands_at, 'if (entry && ca.rig && typeof ca.rig === "object")' in update,
                       missing(apply_rig, {"guard": 'if (!rig || typeof rig !== "object") return;', "joints": "RIG_JOINTS.forEach(",
                                           "set": 'this.rigObject(joint).rotation.set(x, y, z, "XYZ")', "rootY": "this.pelvis.position.y = rootY"}),
                       missing(apply_fn, {"reset pelvis": "this.pelvis.position.y = 0;", "reset head": "this.headJoint.rotation.set(0, 0, 0);"}),
                       "if (limb.ankle) limb.ankle.rotation.set(0, 0, 0);" in _method(pjs, "_resetLimb")],
               [True, True, [], [], True], "rig 는 손 IK 뒤에 · rig 없으면 건너뜀 · 매 프레임 기본으로 되돌린 뒤 적용(컷이 바뀌면 풀림)")
    except Exception as exc:  # noqa: BLE001
        crash("q-b", exc)

    try:  # (q-c)
        key = _extract_js(pjs, "handleKey")
        expect("q-c", [missing(key, {"P": 'is("p")', "toggle": "setPoseMode(!edit.pose)", "esc": "if (edit.pose) {\n        setPoseMode(false);"}),
                       "leavePose();" in _extract_js(pjs, "play"), pjs.count("leavePose(); // 작업 209a") >= 3,
                       "!playing && !recording" in _extract_js(pjs, "setPoseMode"),
                       "edit.helpersOn && !playing && !recording" in _extract_js(pjs, "syncPose"),
                       "helpers.add(poseGroup);" in pjs, "drag.still = true;" in _extract_js(pjs, "beginActorDrag"),
                       "if (drag.still) return;" in _extract_js(pjs, "onPointerMove"),
                       "beginPoseDrag(event)" in _extract_js(pjs, "onPointerDown"),
                       missing(vjs, {"button": '"포즈 (P)"', "wire": "previzCtl.setPoseMode(!previzCtl.poseMode)",
                                     "sync": 'poseBtn.classList.toggle("is-on", !!st.pose)', "onPose": "previzCtl.onPose(",
                                     "help": " · P 포즈\""}),
                       ".vnew-previz-pose {" in css],
               [[], True, True, True, True, True, True, True, True, [], True],
               "P 켜고 끄기 · Esc 는 포즈만 끔 · 재생·녹화·프레임 렌더에서 꺼짐 · 손잡이는 helpers 안 · 몸통 끌기 무시 · 도구 줄 단추")
    except Exception as exc:  # noqa: BLE001
        crash("q-c", exc)

    try:  # (q-d)
        ik = _extract_js(pjs, "solveTwoBoneIK", "")
        limb = _extract_js(pjs, "solveLimb")
        limits = _rig_limits(pjs)
        expect("q-d", [missing(ik, {"law of cosines": "(a * a + reach * reach - b * b) / (2 * a", "pole plane": "pv.dot(dir)",
                                    "basis": "makeBasis(xAxis, yAxis, zAxis)", "hinge": "mid: [sign * bend || 0, 0, 0]",
                                    "straight": "Math.min(maxD, dist)"}),
                       "export function solveTwoBoneIK(" in pjs,
                       "export const RIG_IK_POLES = Object.freeze({ arm: [0.5, -0.6, -0.6], leg: [0.15, 0, 1] });" in pjs,
                       missing(limb, {"poles": ('RIG_IK_POLES[arm ? "arm" : "leg"]', "new THREE.Vector3(out * pl[0], pl[1], pl[2])"),
                                      "hinge sign": ("pole, -1, -bendLim[0] * DEG", "pole, 1, bendLim[1] * DEG"),
                                      "fk only": ("writeJoint(cut, id, rootJoint, res.root)", "writeJoint(cut, id, midJoint, res.mid)")}),
                       sorted(limits) == sorted(JOINTS), limits.get("head", [None, None])[:2], limits.get("kneeL", [[None]])[0][0],
                       limits.get("elbowR", [[None, None]])[0][1], pjs.count("export const RIG_LIMITS"),
                       "clampRigJoint(joint, values, from)" in _extract_js(pjs, "writeJoint")],
               [[], True, True, [], True, [[-60, 60], [-80, 80]], 0, 0, 1, True],
               "해석해 IK(코사인 법칙·pole 평면·경첩) · 팔꿈치 뒤 / 무릎 앞 · 결과는 FK 값 · 한계 표 하나(머리 ±60/±80 · 무릎·팔꿈치 한쪽)")
    except Exception as exc:  # noqa: BLE001
        crash("q-d", exc)

    try:  # (q-e)
        sec = _extract_js(vjs, "previzPoseSection", "    ")
        expect("q-e", [missing(vjs, {"axes": 'var PREVIZ_JOINT_AXES = [["돌리기", 1], ["숙이기", 0], ["기울이기", 2]];',
                                     "step": "var PREVIZ_JOINT_STEP_DEG = 5;",
                                     "note": 'var PREVIZ_POSE_NOTE = "목록에서 고른 자세 위에 직접 바꾼 관절이 덮어씁니다";',
                                     "names": ('head: "머리"', 'neck: "목"', 'spine: "가슴"', 'shoulderL: "왼어깨"', 'kneeR: "오른무릎"'),
                                     "card": "card.appendChild(previzPoseSection(scene, cut, ca));"}),
                       missing(sec, {"inputs": ('input.type = "number"', "ctl.setJointDeg(ca.id, joint, v, false, cut)", "applyEdit(scene, cut)"),
                                     "±5": ('"−" + PREVIZ_JOINT_STEP_DEG', '"+" + PREVIZ_JOINT_STEP_DEG', "ctl.setJointDeg(ca.id, joint, v, true, cut)"),
                                     "reset joint": ('"이 관절 초기화"', "ctl.resetJoint(ca.id, joint, cut)"),
                                     "reset pose": ('"포즈 초기화"', "previzCtl.resetPose(ca.id, cut)"),
                                     "mirror": ('"좌우 반전"', "previzCtl.mirrorPose(ca.id, cut)"),
                                     "name": "PREVIZ_JOINT_KO[joint]", "refresh": "fieldRefreshers.push("}),
                       missing(pjs, {"mirrorRig": ("export function mirrorRig(", "[Number(v[0]) || 0, -Number(v[1]) || 0, -Number(v[2]) || 0]"),
                                     "mirrorPose": "const next = mirrorRig(base);"})],
               [[], [], []], "채널 박스(도 · ±5° · 이 관절 초기화) · 포즈 초기화 · 좌우 반전(L↔R · y,z 부호) · 안내 · 한국어 이름")
    except Exception as exc:  # noqa: BLE001
        crash("q-e", exc)

    try:  # (q-f)
        save_at = vjs.find("// 작업 209a: 인물이 직접 잡은 관절(cut actor 의 rig)도 이 scenes 안에 그대로 실린다")
        save = vjs[save_at: save_at + 400] if save_at >= 0 else ""
        expect("q-f", [missing(_extract_js(pjs, "endPoseDrag"), {"commit": "commitEdit(p.cut);", "event": 'kind: "commit"'}),
                       missing(_extract_js(pjs, "writeJoint"), {"rig": "actorRig(ca)[joint] = v;", "manual": "ca.manual = true;"}),
                       "ca.manual = true;" in _extract_js(pjs, "writeRootY"),
                       'emit("edit", { cut: p.cut, kind: "live" });' in _extract_js(pjs, "movePoseDrag"),
                       "commitEdit(cut);" in _extract_js(pjs, "poseEdit"),
                       '"/previz/scene", {' in save and "method: \"PUT\"" in save and "body: JSON.stringify({ scenes: scenes })" in save,
                       "previzEditSnapshot(previzSaved.scenes) !== previzSaved.snap" in _extract_js(vjs, "previzRefreshDirty")],
               [[], [], True, True, True, True, True],
               "끈 것 = live 여러 번 + commit 한 번 · rig·manual 은 cut actor 에 · 저장 본문 = scenes(rig 포함) · dirty 도 같은 scenes")
    except Exception as exc:  # noqa: BLE001
        crash("q-f", exc)

    files = [PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js", PUBLIC / "vnew.css", TOOLS / "check_pose_controls_209a.py"]
    expect("q-g", [p.name for p in files if b"\r" in p.read_bytes()], [], "고친 파일 LF")

    try:  # (q-h)
        if not node:
            warns.append("node 가 없어 node --check(q-h)를 건너뜁니다")
            seen.add("q-h")
        else:
            for path in (PUBLIC / "vnew-previz.js", PUBLIC / "vnew.js"):
                proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
                expect("q-h", [path.name, proc.returncode, (proc.stderr or "")[-300:]], [path.name, 0, ""], "node --check")
    except Exception as exc:  # noqa: BLE001
        crash("q-h", exc)

    try:  # (q-i)(q-j)(q-k)
        if not node:
            warns.append("node 가 없어 실행 검사(q-i·q-j·q-k)를 건너뜁니다")
            seen.update({"q-i", "q-j", "q-k"})
        else:
            got, err = _run_node(node, pjs)
            if err:
                expect("q-i", err, "", "node 하네스")
            else:
                _expect_node(expect, warns, got)
    except Exception as exc:  # noqa: BLE001
        crash("q-i", exc)

    for text in warns:
        print(f"[WARN] 작업 209a {text}")
    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 209a ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 209a ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 209a ({group}) {title}")
    return passed


def _expect_node(expect, warns: list, got: dict) -> None:
    d = math.pi / 180
    r4 = lambda v: round(v, 4)  # noqa: E731
    ik = got["ik"]
    expect("q-i", [ik["errCm"] <= 1.0, ik["reached"], ik["elbowFlexNeg"], ik["elbowHinge"], ik["elbowBack"],
                   ik["legErrCm"] <= 1.0, ik["kneeFlexPos"], ik["kneeFwd"]],
           [True] * 8, f"닿는 목표 1cm 안({ik['errCm']}cm · 다리 {ik['legErrCm']}cm) · 팔꿈치 -x 경첩·뒤로 · 무릎 +x·앞으로")
    expect("q-i", [ik["farReached"], ik["farStraight"], ik["farAngleDeg"] <= 0.5, ik["farLen"]], [False, True, True, 0.59],
           f"못 닿는 목표 = 쭉 편 팔이 그쪽({ik['farAngleDeg']}°)")
    expect("q-i", got["clamp"], {"head": [r4(60 * d), r4(-80 * d), 0], "kneeL": [0, r4(0.2), 0], "elbowR": [0, 0, 0],
                                 "inside": [0.3, -0.2, 0.1], "unknown": [r4(math.pi), r4(-math.pi), 0]},
           "한계: 머리 끄덕임 ±60° · 돌리기 ±80° · 무릎·팔꿈치 한쪽만 · 안쪽 값 그대로 · 모르는 관절 ±π")
    expect("q-i", [got["mirror"]["once"], got["mirror"]["twice"]],
           [{"shoulderR": [0.1, -0.2, -0.3], "elbowL": [-0.5, -0.1, 0], "spine": [0.1, -0.2, -0.3], "kneeR": [0.4, 0, 0], "rootY": -0.2}, True],
           "좌우 반전 = L↔R · y,z 부호 · 두 번 = 그대로")

    old = got["old"]
    hand_l = [r4(0.22 + 0.59 * math.sin(0.1)), r4(1.42 - 0.59 * math.cos(0.1)), 0]
    expect("q-j", [old["stand"], old["sit"], old["joints"], old["headParent"], old["headJointAtNeck"]],
           [{"head": [0, 1.63, 0], "handL": hand_l, "ankleL": [0.1, 0, 0], "pelvis": 0}, {"ankleL": [0.1, 0.06, 0.46], "hips": [0, 0.5, 0]},
            {j: True for j in ("spine", "neck", "head", "shoulderL", "elbowL", "wristL", "hipL", "kneeL", "ankleL")}, True, True],
           "rig 없는 옛 계획 = 지금 모양(머리 1.63 · 손 · 발목 바닥 · 앉기) · 관절 피벗")
    rig = got["rig"]
    expect("q-j", [rig["on"], rig["off"]],
           [{"spineX": 0.2, "shoulderR": [-1, 0, 0], "elbowR": [-0.5, 0, 0], "kneeL": 1, "kneeR": 0, "hipsY": 0.6},
            {"spineX": 0.7, "kneeL": 0, "pelvis": 0, "handTouch": True}],
           "rig 가 동작(bow)·손(own_face) 위에 덮어씀 · rootY -0.3 · rig 없는 컷은 목록 자세로 되돌아감")
    want, why = _parity_points()
    if want is None:
        warns.append("pose_describe.body_points 를 가져오지 못해 관절 위치 비교(q-j)를 건너뜁니다: " + why)
    else:
        diff = {k: [round(a - b, 4) for a, b in zip(got["parity"][k], want[k])] for k in PARITY_POINTS
                if max(abs(a - b) for a, b in zip(got["parity"][k], want[k])) > 0.002}
        expect("q-j", diff, {}, "마네킹 관절 위치 = pose_describe.body_points(같은 rig, 2mm 안)")
    expect("q-j", got["dims"], {"baseHeightM": 1.7, "hipsY": 0.9, "neckY": 0.6, "headCenterY": 0.13, "shoulderX": 0.22, "shoulderY": 0.52,
                                "upperArm": 0.3, "foreArm": 0.29, "hipX": 0.1, "thigh": 0.46, "shin": 0.44}, "export 한 RIG_DIMS")

    k = got["editor"]
    expect("q-k", [k["noSel"], k["on"]],
           [False, {"on": True, "visible": True, "rings": sorted(["head", "neck", "spine", "shoulderL", "shoulderR", "elbowL", "elbowR",
                                                                  "hipL", "hipR", "kneeL", "kneeR"]),
                    "handles": ["footL", "footR", "handL", "handR"], "events": 1, "state": True}],
           "인물을 안 고르면 P 무시 · 고른 뒤 P(한글 입력은 code) = 고리 11개 · 손발 네모 4개")
    expect("q-k", [k["fk"], k["fkUndo"]],
           [{"joint": "head", "head": [0, 0.5, 0], "manual": True, "commit": 1, "live": True, "undo0": False, "childMoved": True,
             "posSame": True, "camSame": True, "pickActor": "actor"}, {"rig": True, "manual": False}],
           "머리 고리 누르기 = 머리 · 가로 50px = 돌리기 0.5rad(자식이 따라 돎) · 한 단계 · manual · 되돌리면 처음 그대로")
    expect("q-k", [k["nod"], k["tilt"]], [[0.3, 0, 0], [0.3, 0, -0.2]], "세로 30px = 숙이기 0.3 · Shift+가로 20px = 기울이기 -0.2")
    g = k["gizmo"]
    expect("q-k", [g["attached"], g["at"] == g["headPivot"], g["neck"]], [True, True, [0, 0.3, 0]], "회전(E) 기즈모가 고른 관절에 · 돌리면 rig")
    ik = k["ik"]
    expect("q-k", [ik["up"] >= 0.15, ik["errCm"] <= 1.0, ik["cursorPx"] <= 3, ik["shoulder"], ik["elbowX"] is not None and ik["elbowX"] < 0, ik["elbowHinge"],
                   ik["elbowBehind"], ik["commit"], ik["onlyA"], ik["altBack"], ik["clickJoint"]],
           [True, True, True, True, True, True, True, 1, True, True, "elbowR"],
           f"오른손 네모 끌기: 손이 커서를 따라감({ik['errCm']}cm · {ik['cursorPx']}px · {ik['up']}m 위) · 어깨·팔꿈치 FK · 팔꿈치 뒤 · 한 단계 · Alt 앞뒤 · 누르면 팔꿈치")
    rt = k["root"]
    expect("q-k", [rt["joint"], rt["rootY"] is not None and rt["rootY"] < -0.1, rt["footL"] <= 0.01, rt["footR"] <= 0.01, rt["kneeL"],
                   rt["kneeFwd"], k["bodyStill"]],
           ["root", True, True, True, True, True, True],
           f"골반 네모 아래로: rootY {rt['rootY']} · 발 그대로({rt['footL']}m) · 무릎 앞으로 굽음 · 몸통 끌기는 아무것도 안 함")
    expect("q-k", [k["limits"], k["deg"], k["mirror"]["once"]["shoulderL"] == k["mirror"]["once"]["mirroredFromR"], k["mirror"]["changed"],
                   k["mirror"]["twice"], k["resetJoint"], k["resetPose"]],
           [{"kneeR": 0, "headY": 80}, [0, 80.0, 0], True, True, True, True, {"rig": True, "manual": True, "canUndo": True, "undoBack": True}],
           "패널: 한계(무릎 -30° → 0 · 머리 120° → 80°) · 도 값 · 좌우 반전 · 두 번 = 그대로 · 이 관절 초기화 · 포즈 초기화(되돌리기)")
    expect("q-k", [k["esc"], k["play"], k["otherSel"]],
           [{"on": False, "sel": {"type": "actor", "id": "a"}, "visible": False}, {"onAgain": True, "off": True}, False],
           "Esc = 포즈만 끔(인물 그대로) · 재생하면 꺼짐 · 카메라를 고르면 꺼짐")

    lm = got["limits"]
    expect("q-l", lm["hinge"], {"elbowNegFwd": True, "kneePosBack": True, "elbowPosBack": True, "kneeNegFwd": True},
           "경첩: 팔꿈치 -x = 아래팔 앞(+x 는 뒤로 꺾임) · 무릎 +x = 정강이 뒤(-x 는 앞으로 꺾임)")
    expect("q-l", [lm["n"] >= 20000, lm["badCount"], lm["bad"], lm["listClamped"], lm["reachClamped"]], [True, 0, [], [], []],
           f"목록 자세·동작·posture·말하기·머리·손 × 상대 앞/옆/뒤 {lm['n']}번: 모든 관절 값이 RIG_LIMITS 안 · 동작·자세는 자르기 없이 · "
           "서기/앉기 + 앞 상대의 손 닿기도 자르기 없이")
    r = lm["reach"]
    expect("q-l", [max(r["coverFace"]) <= 6, r["ownCheek"] <= 3, r["tears"] <= 8, r["chest"] <= 2, max(r["knees"]) <= 2, r["elbowX"]],
           [True, True, True, True, True, [True] * 8],
           f"손 닿기(cm): 얼굴 가리기 {r['coverFace']} · 자기 뺨 {r['ownCheek']} · 눈물 {r['tears']} · 가슴 {r['chest']} · 앉아 무릎 {r['knees']} · "
           "팔을 쓰는 동작의 팔꿈치는 모두 -x")
    nm = got["noSnap"]
    delta = nm["listDelta"] or [9, 9, 9]
    expect("q-m", [nm["listJoint"], nm["listStartInside"], abs(delta[0] + 0.1) < 2e-4, abs(delta[1]) < 2e-4, abs(delta[2]) < 2e-4],
           ["elbowR", True, True, True, True], f"목록 동작(cover_face) 팔꿈치 고리 위로 10px = 숙이기 -0.1 만(첫 값에서 이어짐, 차이 {delta})")
    expect("q-m", [nm["outward"], nm["inward"], nm["boxOther"], nm["boxOut"], nm["boxIn"]],
           [[0.6, 0, 0], [0.3, 0, 0], [0.6, r4(20 * d), 0], [0.6, r4(20 * d), 0], [r4(-40 * d), r4(20 * d), 0]],
           "한계 밖 팔꿈치 +0.6: 바깥으로 끌면 그 자리 · 안쪽으로 30px = 0.3 · 채널 박스 다른 축만 바꾸면 x 그대로 · 더 바깥 입력은 그 자리 · 안쪽 입력은 그 값")
    wv = got["workView"]
    first = {"inside": True, "camSide": True, "above": True, "view": "work"}
    expect("q-n", [wv["c1"], wv["c2"], wv["kept"]], [first, first, [9, 9, 9]],
           "작업 시점 첫 화면: 그 컷 인물(발·머리)·촬영 카메라가 화면 안 · 카메라 쪽 위에서 · 처음 보는 c2 도 · 궤도를 움직인 뒤 c3 은 그 자리")


if __name__ == "__main__":
    ok = check_pose_controls_209a()
    print('hook (check_vpoc.py main, 작업 207 다음 줄): if not __import__("check_pose_controls_209a").check_pose_controls_209a(): '
          'fail("작업 209a: 프리비즈 포즈(관절 고리 · IK 손발 · 골반 · 채널 박스 · 좌우 반전) 검사 실패")')
    sys.exit(0 if ok else 1)
