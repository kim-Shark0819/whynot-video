/* 영상만들기_신규 프리비즈(3D) 렌더러·컨트롤러 (설계자 2026-10-21, 지시서 038).

   Astra 가 설계한 3D 장면 JSON(previz_schema.normalize_scene 모양)을 three.js 로 그려
   재생·탐색(seek)·장면 전환하고, 구간을 실시간 재생하며 MediaRecorder 로 녹화한다.
   이렇게 만든 '마네킹 영상'을 Kling O3 편집에 넣으면 배치·카메라·인원이 그대로 유지된다
   (Claude 실측). 참고 구현: .kiro/handoff/ref-previz-prototype.html.

   설계자 규칙:
   - 배경은 하나의 덩어리(바닥·벽 인식), 빛이 어디서 오는지 편집 화면 오버레이로 표시.
   - 인물은 고유의 밝은 색, 관절이 이어진 사람 모양 마네킹(머리·몸통·팔2·다리2).
     무릎 꿇기를 '몸통 + 떨어진 상자'로 만들면 그 상자가 결과에 남는다 → 관절로 굽힌다.
   - 사물은 디테일 없이 무엇인지 알아볼 정도(box/cylinder). 화질은 낮아도 됨.

   전역: window.vnewPreviz.create(host, {scenes, textureFetch, width, height}) → Promise<controller>.
   textureFetch(apiUrl) → Promise<objectURL|null>: 벽·바닥 텍스처를 vnew 의 Authorization 헤더로
   받아(fetch) blob → URL.createObjectURL 로 바꿔 돌려준다. 같은 출처 blob 이라 캔버스가 오염되지
   않아 captureStream 녹화가 된다(버킷에 CORS 가 없어 S3 직통은 쓰지 않는다). create 는 텍스처를
   받아 첫 프레임을 그린 뒤 controller 를 resolve 한다.
   controller = { play(), pause(), seek(t), setScene(i), duration, onTime(cb),
                  record(fromT, toT) → Promise<Blob>,
                  renderFrames(fromT, toT, fps=24, onProgress) → Promise<Blob[]>, destroy() }.

   좌표: x 오른쪽, y 위, z 카메라 쪽(+). north 벽은 z = -depth/2. 단위 m·초.

   vendor 수정 금지 — three 만 쓴다. */

import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";

const SIDES = ["north", "east", "south", "west"];
const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;
const RECORD_FPS = 24;
/* 작업 201 편집기. 카메라 범위·렌즈는 작업 200(previz_schema·vnew.js)과 같은 값. */
const SNAP_M = 0.1; // 눈금 맞춤 10cm
const SNAP_DEG = 15; // 눈금 맞춤 15°
const UNDO_LIMIT = 50;
const CAMERA_MARGIN_M = 3;
const ACTOR_WALL_M = 0.2;
const LENS_MIN = 6;
const LENS_MAX = 144;
const TUMBLE_RAD_PER_PX = 0.008;
const CLICK_SLOP_PX = 4;
const WHEEL_COMMIT_MS = 400;
/* 작업 207: 인물 직접 끌기·방향키·휠 안내·기즈모 크기. */
const ACTOR_TURN_DEG_PER_PX = 0.5; // 회전(E) 도구로 인물을 좌우로 끌 때
const NUDGE_M = 0.1; // 방향키 한 번
const NUDGE_BIG_M = 0.5; // Shift+방향키
const NUDGE_DEG = 15; // [ / ]
const GIZMO_SIZE = 1.5; // TransformControls 기본 1 의 1.5배
const WHEEL_HINT_TEXT = "화면을 한 번 누르면 휠로 확대·축소할 수 있습니다";
const WHEEL_HINT_MS = 1500;
const WHEEL_HINT_GAP_MS = 4000; // 페이지를 굴리는 동안 안내가 깜빡이지 않게
const CAM_ICON_COLOR = 0x4fc3f7;
const WORK_FRAME_PITCH_RAD = 40 * Math.PI / 180; // 작업 209a fix 1: 작업 시점 첫 화면을 내려다보는 각
const SEL_COLOR = 0xffd54f;
/* vendor addons(작업 201). vnew-previz.js 와 같은 three URL 을 import 하므로 THREE 인스턴스가 하나다.
   Dockerfile 이 __ASSET_VERSION__ 을 치환한다. create() 에서만 불러온다(검사의 node 실행은 이 파일만 읽는다). */
const ORBIT_CONTROLS_URL = "/vendor/three/addons/OrbitControls.js?v=__ASSET_VERSION__";
const TRANSFORM_CONTROLS_URL = "/vendor/three/addons/TransformControls.js?v=__ASSET_VERSION__";

/* 제스처(연기 동작) 목록 — previz_schema.GESTURES 와 **문자열이 같아야** 한다(검사 mf-a).
   앞 4개는 옛 동작, 나머지는 2026-10-22 추가한 연기 동작. 상대가 필요한 동작은 cut actor 의
   gestureTarget(상대 id)으로 팔·기울기·목 방향을 그 사람 쪽으로 계산한다. */
const GESTURES = [
  "point",
  "bow",
  "hands_on_knees",
  "cover_face",
  "hand_to_own_face",
  "wipe_tears",
  "hand_on_other_cheek",
  "both_hands_other_cheeks",
  "hand_on_chest",
  "press_hand_to_other_chest",
  "lean_in",
  "laugh",
  "look_down",
  "look_up",
  "nod",
  "turn_head_to_other",
];
/* 작업 148: 패널에서 읽은 손·자세·머리·눈(cut actor 의 posture·hands·head·eyes). previz_schema 의 같은 이름 튜플과
   **문자열이 같아야** 한다(검사 mf-a). 왼쪽·오른쪽은 인물 자신의 손(마네킹 +x 가 왼쪽). hands 가 있는 인물은 팔마다
   목표 신체 부위의 세계 위치로 2관절 IK(어깨 → 팔꿈치 → 손)를 푼다. other_* 는 gestureTarget 마네킹의 같은 프레임 뼈. */
const POSE_POSTURES = ["stand", "sit", "lean_forward", "lean_back", "kneel", "lie"];
const HAND_ACTS = ["rest", "grab", "hold", "touch", "cup", "cover", "rest_on", "point", "none"];
const HAND_TARGETS = [
  "own_face", "own_eyes", "own_cheek", "own_chin", "own_mouth", "own_chest", "own_knee",
  "other_chin", "other_cheek", "other_face", "other_shoulder", "other_neck", "other_hand",
  "other_chest", "other_head", "other_arm", "none",
];
const HEAD_TURNS = ["toward_other", "away", "down", "up", "front"];
const HEAD_TILTS = ["none", "left", "right"];
const EYE_STATES = ["open", "closed", "half"];
/* 작업 209a: 마네킹 관절 치수(마네킹 로컬 m, 키 1.7m 기준 — group 이 키/1.7 로 커진다). app/src/vnew/pose_describe.py 의
   HIPS_Y·NECK_Y·HEAD_CENTER_Y·SHOULDER·UPPER_ARM_M·FORE_ARM_M·HIP_X·THIGH_M·SHIN_M 과 같은 값이어야 한다. 치수는 여기서만 고친다.
   피벗: pelvis(rootY) → hips(y hipsY) → spine → neck(y neckY) → head(목과 같은 자리, 머리 중심은 headCenterY 위)
   · 어깨(±shoulderX, shoulderY) → 팔꿈치(-upperArm) → 손목(-foreArm) · 엉덩이(±hipX, 0) → 무릎(-thigh) → 발목(-shin).
   +x 가 인물 자신의 왼쪽(L), 정면은 +z. 뼈는 쉬는 자세에서 -y 로 뻗는다. */
export const RIG_DIMS = Object.freeze({
  baseHeightM: 1.7,
  hipsY: 0.9,
  neckY: 0.6,
  headCenterY: 0.13,
  shoulderX: 0.22,
  shoulderY: 0.52,
  upperArm: 0.3,
  foreArm: 0.29,
  hipX: 0.1,
  thigh: 0.46,
  shin: 0.44,
});
// 팔 길이(마네킹 로컬, 키 1.7m 기준): 어깨 → 팔꿈치 0.3, 팔꿈치 → 손끝 0.29(아래팔 캡슐 끝).
const UPPER_ARM_M = RIG_DIMS.upperArm;
const FORE_ARM_M = RIG_DIMS.foreArm;
/* 작업 208/209a: cut actor 의 rig = {관절: [rx, ry, rz] 로컬 Euler XYZ 라디안, rootY: 골반 높이 차(m, 마네킹 로컬)}.
   previz_schema.RIG_JOINTS 와 같은 이름. 목록 자세(자세·동작·손·머리)를 먼저 적용한 뒤 rig 에 적힌 관절만 그 값으로 덮어쓴다. */
const RIG_JOINTS = [
  "spine", "neck", "head",
  "shoulderL", "elbowL", "wristL", "shoulderR", "elbowR", "wristR",
  "hipL", "kneeL", "ankleL", "hipR", "kneeR", "ankleR",
];
const RIG_ROOT_Y_RANGE = [-1.0, 0.3]; // previz_schema.RIG_ROOT_Y_RANGE 와 같다
/* 관절 한계(도, [x 숙이기, y 돌리기, z 기울이기] 마다 [최소, 최대]) — 사람이 할 수 있는 범위. 한계는 이 표 하나만 쓴다.
   무릎은 +x(정강이가 뒤로)로만, 팔꿈치는 -x(아래팔이 앞으로)로만 굽는다. 머리 돌리기 ±80°, 끄덕이기 ±60°.
   왼쪽(L) z+ = 팔·다리를 바깥(+x)으로 벌림, 오른쪽(R)은 부호가 반대(좌우 반전이 한계 안에 그대로 남는다).
   작업 209a fix 1 — 경첩 방향을 이 리그에서 잰 값: 뼈는 -y 로 뻗고 Rx(θ)·(0,-1,0) = (0, -cosθ, -sinθ) 이므로 팔꿈치 -x 는
   아래팔을 위팔의 앞면(쉬는 자세 +z) 쪽으로, 무릎 +x 는 정강이를 허벅지 뒷면(-z) 쪽으로 굽힌다. 목록 동작·손·자세가 만드는
   값은 모두 이 표 안이다(검사 q-l 이 전부 돌려 본다). 가슴: 웃기(-0.1rad) + 뒤로 기대기(-25°) = -31° 라 x 는 -35 까지,
   앉아 몸을 돌린 자세(sit.turn ±90°, 다리는 그대로)는 이 리그에서 가슴 y 하나로 돌리므로 y 는 ±90. */
export const RIG_LIMITS = Object.freeze({
  spine: [[-35, 90], [-90, 90], [-40, 40]],
  neck: [[-50, 60], [-80, 80], [-40, 40]],
  head: [[-60, 60], [-80, 80], [-40, 40]],
  shoulderL: [[-180, 70], [-90, 90], [-90, 180]],
  shoulderR: [[-180, 70], [-90, 90], [-180, 90]],
  elbowL: [[-150, 0], [-90, 90], [-10, 10]],
  elbowR: [[-150, 0], [-90, 90], [-10, 10]],
  wristL: [[-80, 80], [-90, 90], [-45, 45]],
  wristR: [[-80, 80], [-90, 90], [-45, 45]],
  hipL: [[-125, 45], [-45, 45], [-25, 80]],
  hipR: [[-125, 45], [-45, 45], [-80, 25]],
  kneeL: [[0, 172], [-30, 30], [-10, 10]],
  kneeR: [[0, 172], [-30, 30], [-10, 10]],
  ankleL: [[-45, 50], [-30, 30], [-30, 30]],
  ankleR: [[-45, 50], [-30, 30], [-30, 30]],
});
const DEG = Math.PI / 180;
/* 2관절 IK 의 pole(가운데 관절이 나갈 쪽, 부모 = 가슴·엉덩이 기준, x 는 인물 왼쪽 L 기준 — R 은 x 부호를 뒤집는다).
   팔꿈치는 뒤·아래·조금 바깥, 무릎은 앞(조금 바깥). */
export const RIG_IK_POLES = Object.freeze({ arm: [0.5, -0.6, -0.6], leg: [0.15, 0, 1] });

/* 관절 값(라디안 3개)을 RIG_LIMITS 안으로 자른다. 숫자가 아니면 0. 모르는 관절은 ±π(서버 RIG_ANGLE_MAX).
   from(라디안 3개, 선택) = 편집을 시작한 값. 그 값이 한계 밖이면 그 축은 from 까지 넓혀 자른다 — 첫 편집에 값이 한계로
   튀지 않고, 한계 쪽으로는 움직이고 더 바깥으로는 못 간다(작업 209a fix 1). */
export function clampRigJoint(joint, values, from) {
  const lim = Object.prototype.hasOwnProperty.call(RIG_LIMITS, joint) ? RIG_LIMITS[joint] : null;
  return [0, 1, 2].map((i) => {
    let v = Number(values && values[i]);
    if (!isFinite(v)) v = 0;
    let lo = lim ? lim[i][0] * DEG : -Math.PI;
    let hi = lim ? lim[i][1] * DEG : Math.PI;
    const f = Number(from && from[i]);
    if (from && isFinite(f)) {
      lo = Math.min(lo, f);
      hi = Math.max(hi, f);
    }
    return Math.max(lo, Math.min(hi, v)) || 0;
  });
}

/* 좌우 반전: L ↔ R 관절을 바꾸고 y·z 부호를 뒤집는다(x = 인물 왼쪽 평면 거울). 가운데 관절(spine·neck·head)도 y·z 를 뒤집는다.
   rootY 는 그대로. 두 번 하면 처음 그대로. */
export function mirrorRig(rig) {
  const src = rig && typeof rig === "object" ? rig : {};
  const out = {};
  Object.keys(src).forEach((k) => {
    const v = src[k];
    if (k === "rootY") {
      out.rootY = v;
      return;
    }
    if (!Array.isArray(v) || v.length !== 3) return;
    const to = /L$/.test(k) ? k.slice(0, -1) + "R" : /R$/.test(k) ? k.slice(0, -1) + "L" : k;
    out[to] = [Number(v[0]) || 0, -Number(v[1]) || 0, -Number(v[2]) || 0];
  });
  return out;
}

/* 2관절 IK(작업 209a, 해석해). 뿌리 관절(어깨·엉덩이) 부모 좌표계에서 뿌리 피벗을 원점으로 둔 목표점 target 에
   뿌리 → 가운데(팔꿈치·무릎) → 끝(손목·발목)이 닿게 한다. 뼈는 쉬는 자세에서 -y. pole = 가운데 관절이 나갈 쪽
   (팔꿈치는 뒤·아래, 무릎은 앞). 가운데 관절은 로컬 x 축 경첩 하나로만 굽는다: flexSign +1 = +x(무릎), -1 = -x(팔꿈치).
   maxBendRad = 가운데 관절이 굽을 수 있는 최대 각. 닿지 않으면 목표 쪽으로 쭉 편다(굽힘 0).
   돌려준 값 = {root: [x,y,z], mid: [x,0,0] (Euler XYZ 라디안), reached, midPos, endPos} — rig 에는 FK 값만 남긴다. */
export function solveTwoBoneIK(target, lenA, lenB, pole, flexSign, maxBendRad) {
  const a = Number(lenA) || 0;
  const b = Number(lenB) || 0;
  const sign = flexSign < 0 ? -1 : 1;
  const maxBend = Math.max(0, Math.min(Math.PI - 0.01, Number(maxBendRad) || Math.PI - 0.01));
  const t = new THREE.Vector3(Number(target.x) || 0, Number(target.y) || 0, Number(target.z) || 0);
  let dist = t.length();
  const dir = dist > 1e-9 ? t.clone().divideScalar(dist) : new THREE.Vector3(0, -1, 0);
  const minD = Math.sqrt(Math.max(1e-12, a * a + b * b + 2 * a * b * Math.cos(maxBend)));
  const maxD = a + b;
  const reach = Math.max(minD, Math.min(maxD, dist));
  // 굽힘 평면: pole 에서 dir 성분을 뺀 방향(가운데 관절이 나갈 쪽). pole 이 dir 과 나란하면 앞(+z)/위(+y)로.
  const pv = new THREE.Vector3(Number(pole && pole.x) || 0, Number(pole && pole.y) || 0, Number(pole && pole.z) || 0);
  let side = pv.clone().addScaledVector(dir, -pv.dot(dir));
  if (side.lengthSq() < 1e-10) side = new THREE.Vector3(0, 0, 1).addScaledVector(dir, -dir.z);
  if (side.lengthSq() < 1e-10) side = new THREE.Vector3(0, 1, 0).addScaledVector(dir, -dir.y);
  side.normalize();
  const cosA = Math.max(-1, Math.min(1, (a * a + reach * reach - b * b) / (2 * a * Math.max(1e-9, reach))));
  const sinA = Math.sqrt(Math.max(0, 1 - cosA * cosA));
  const midPos = dir.clone().multiplyScalar(a * cosA).addScaledVector(side, a * sinA);
  const endPos = dir.clone().multiplyScalar(reach);
  const u = midPos.clone().normalize(); // 위 뼈 방향
  const v = endPos.clone().sub(midPos).normalize(); // 아래 뼈 방향
  // 뿌리 회전: 로컬 -y → u, 로컬 z → sign·(u 에 수직인 굽힘 쪽). 경첩(로컬 x)이 굽힘 평면에 수직이 된다.
  let zAxis = side.clone().addScaledVector(u, -side.dot(u));
  if (zAxis.lengthSq() < 1e-10) zAxis = new THREE.Vector3(0, 0, 1).addScaledVector(u, -u.z);
  zAxis.normalize().multiplyScalar(sign);
  const yAxis = u.clone().negate();
  const xAxis = new THREE.Vector3().crossVectors(yAxis, zAxis).normalize();
  const basis = new THREE.Matrix4().makeBasis(xAxis, yAxis, zAxis);
  const e = new THREE.Euler().setFromRotationMatrix(basis, "XYZ");
  const bend = Math.acos(Math.max(-1, Math.min(1, u.dot(v))));
  return {
    root: [e.x || 0, e.y || 0, e.z || 0],
    mid: [sign * bend || 0, 0, 0],
    reached: dist <= maxD + 1e-6 && dist >= minD - 1e-6,
    midPos: midPos.toArray(),
    endPos: endPos.toArray(),
  };
}
// lean_forward / lean_back 척추 기울기(25°).
const LEAN_RAD = 25 * Math.PI / 180;
const DEFAULT_HAIR_COLOR = "#2b2b2b";
/* 작업 151: 그리는 카메라 여유. 마네킹 머리 구(반지름 0.16m × 키/1.7)·몸통 캡슐(어깨 → 엉덩이, 반지름 0.22m) 표면에서
   이 거리(m) 안이면 시선 반대로 물려 그린다(서버 previz_camera 와 같은 크기). 저장된 설계는 바꾸지 않는다. */
const RENDER_MIN_CLEAR_M = 0.25;
const HEAD_CLEAR_RADIUS_M = 0.16;
const BODY_CLEAR_RADIUS_M = 0.22;
const RENDER_CLEAR_STEP_M = 0.02;
const RENDER_CLEAR_MAX_M = 3;

/* 점 p 에서 마네킹 부피(구 {center, r}·캡슐 {a, b, r}) 표면까지 가장 가까운 거리. 안이면 음수, 부피가 없으면 Infinity. */
function volumeClearance(p, volumes) {
  let best = Infinity;
  const ab = new THREE.Vector3();
  const ap = new THREE.Vector3();
  const q = new THREE.Vector3();
  (volumes || []).forEach((v) => {
    if (v.b) {
      ab.subVectors(v.b, v.a);
      const len2 = ab.lengthSq();
      const t = len2 < 1e-12 ? 0 : Math.max(0, Math.min(1, ap.subVectors(p, v.a).dot(ab) / len2));
      q.copy(v.a).addScaledVector(ab, t);
      best = Math.min(best, p.distanceTo(q) - v.r);
    } else {
      best = Math.min(best, p.distanceTo(v.center) - v.r);
    }
  });
  return best;
}

/* 카메라 위치(position, 제자리 수정)가 부피 표면에서 minClear 안이면 시선(direction, 단위 벡터) 반대로 0.02m 씩 물린다.
   결정적(같은 입력이면 같은 결과 — 녹화가 화면과 같다). 물린 거리(m)를 돌려준다(그대로면 0). */
function clearCameraPosition(position, direction, volumes, minClear) {
  if (!(volumeClearance(position, volumes) < minClear)) return 0;
  const start = position.clone();
  const steps = Math.round(RENDER_CLEAR_MAX_M / RENDER_CLEAR_STEP_M);
  const p = new THREE.Vector3();
  for (let k = 1; k <= steps; k += 1) {
    p.copy(start).addScaledVector(direction, -RENDER_CLEAR_STEP_M * k);
    if (volumeClearance(p, volumes) >= minClear) {
      position.copy(p);
      return RENDER_CLEAR_STEP_M * k;
    }
  }
  position.copy(start).addScaledVector(direction, -RENDER_CLEAR_MAX_M);
  return RENDER_CLEAR_MAX_M;
}

/* 벽 한 면의 위치·회전. north 는 z=-depth/2 를 바라보는 안쪽 면.
   (x, z, rotationY) 를 돌려준다. 평면(PlaneGeometry)은 +z 를 향하므로 방 안쪽을 보게 돌린다. */
function wallTransform(side, width, depth) {
  const halfW = width / 2;
  const halfD = depth / 2;
  switch (side) {
    case "north":
      return { x: 0, z: -halfD, ry: 0, w: width };
    case "south":
      return { x: 0, z: halfD, ry: Math.PI, w: width };
    case "west":
      return { x: -halfW, z: 0, ry: Math.PI / 2, w: depth };
    case "east":
      return { x: halfW, z: 0, ry: -Math.PI / 2, w: depth };
    default:
      return { x: 0, z: -halfD, ry: 0, w: width };
  }
}

function vec3(arr, dx, dy, dz) {
  if (Array.isArray(arr) && arr.length >= 3) {
    return new THREE.Vector3(Number(arr[0]) || 0, Number(arr[1]) || 0, Number(arr[2]) || 0);
  }
  return new THREE.Vector3(dx || 0, dy || 0, dz || 0);
}

function colorOf(value, fallback) {
  try {
    return new THREE.Color(value || fallback || "#999999");
  } catch (error) {
    return new THREE.Color(fallback || "#999999");
  }
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp01(t) {
  if (t < 0) return 0;
  if (t > 1) return 1;
  return t;
}

/* 작업 201: 컷 카메라의 frac(0~1) 자세 {pos, target}(Vector3). moveTo(= 끝 키)가 있으면 시작 → 끝으로 움직인다 —
   사용자가 놓은 카메라(manual)는 ease in-out(3u²−2u³, 부드럽게 출발·정지), 설계가 만든 이동은 지금처럼 선형. 없으면 고정. */
function easeInOut(u) {
  const x = clamp01(u);
  return x * x * (3 - 2 * x);
}

function cameraPoseAt(cam, frac, roomDepth) {
  const c = cam || {};
  const pos = vec3(c.pos, 0, 1.5, (Number(roomDepth) || 6) / 2 + 1);
  const target = vec3(c.target, 0, 1.2, 0);
  if (!c.moveTo) return { pos, target };
  const u = c.manual ? easeInOut(frac) : clamp01(frac);
  const pos2 = vec3(c.moveTo.pos, pos.x, pos.y, pos.z);
  const tgt2 = vec3(c.moveTo.target, target.x, target.y, target.z);
  return {
    pos: new THREE.Vector3(lerp(pos.x, pos2.x, u), lerp(pos.y, pos2.y, u), lerp(pos.z, pos2.z, u)),
    target: new THREE.Vector3(lerp(target.x, tgt2.x, u), lerp(target.y, tgt2.y, u), lerp(target.z, tgt2.z, u)),
  };
}

/* ================================================================ 마네킹

   관절이 이어진 사람 모양. 각 부위는 Group(피벗)에 Mesh 를 매달아, 피벗을 돌리면
   무릎·팔꿈치가 자연스럽게 굽는다. 떨어진 상자를 쓰지 않는다. heightM 로 전체를 스케일. */
class Mannequin {
  constructor(color, heightM, hairColor) {
    this.group = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial({ color: colorOf(color), roughness: 0.6, metalness: 0.0 });
    this.mat = mat;
    // 얼굴 표식·머리카락은 몸 색과 대비되는 재질. 머리카락은 actor.hairColor(없으면 기본 어두운 회색).
    this.faceMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5 });
    this.darkMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.5 });
    this.hairMat = new THREE.MeshStandardMaterial({ color: colorOf(hairColor, DEFAULT_HAIR_COLOR), roughness: 0.85 });

    // 기준 키 1.7m 에 맞춘 치수. heightM 로 전체 group 을 비례 조정한다.
    const scale = (Number(heightM) || 1.7) / 1.7;
    this.group.scale.setScalar(scale);

    // 작업 209a: 골반(pelvis) — rig.rootY 만큼 엉덩이 아래 전체를 올리고 내린다(기본 0 = 지금 모양 그대로).
    this.pelvis = new THREE.Group();
    this.group.add(this.pelvis);

    // 엉덩이(hips): 다리·몸통이 매달리는 뿌리. 서 있을 때 y≈0.9.
    this.hips = new THREE.Group();
    this.hips.position.y = RIG_DIMS.hipsY;
    this.pelvis.add(this.hips);

    // 몸통(척추) — 엉덩이에서 위로.
    this.spine = new THREE.Group();
    this.hips.add(this.spine);
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.42, 4, 10), mat);
    torso.position.y = 0.31;
    torso.castShadow = true;
    this.spine.add(torso);

    // 목·머리 — 몸통 위. 작업 209a: 머리 관절(headJoint)은 목과 같은 자리 피벗, 머리 구는 그 위 headCenterY.
    this.neck = new THREE.Group();
    this.neck.position.y = RIG_DIMS.neckY;
    this.spine.add(this.neck);
    this.headJoint = new THREE.Group();
    this.neck.add(this.headJoint);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 16, 12), mat);
    head.position.y = RIG_DIMS.headCenterY;
    head.castShadow = true;
    this.headJoint.add(head);

    // --- 얼굴 표식(정면 +z): 머리의 앞·뒤를 영상 모델이 구분하게 한다(설계자 2026-10-22) ---
    // 머리 반지름 r=0.13. 눈·코·입을 머리 앞면(+z)에 붙인다. 머리 중심(head.position)을 기준으로.
    const r = 0.13;
    // 흰 눈 2개: 지름 0.035(반지름 0.0175), 좌우 간격 0.07(±0.035), 머리 중심 높이 +0.02, 앞으로 r 近처.
    const eyeGeo = new THREE.SphereGeometry(0.0175, 10, 8);
    const eyeL = new THREE.Mesh(eyeGeo, this.faceMat);
    eyeL.position.set(0.035, 0.02, r * 0.93);
    head.add(eyeL);
    const eyeR = new THREE.Mesh(eyeGeo, this.faceMat);
    eyeR.position.set(-0.035, 0.02, r * 0.93);
    head.add(eyeR);
    // 눈 감기(작업 148): 눈꺼풀이 없어 흰 눈을 세로로 눌러 감은 눈·반쯤 감은 눈을 흉내 낸다.
    this.eyeMeshes = [eyeL, eyeR];
    // 어두운 코: 작은 원뿔이 앞으로 0.03 돌출(눈 아래).
    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.022, 0.05, 10), this.darkMat);
    nose.rotation.x = Math.PI / 2; // 끝이 +z 를 향하게
    nose.position.set(0, -0.005, r + 0.03);
    head.add(nose);
    // 입 선: 얇은 어두운 막대(가로로 긴 상자), 코 아래.
    const mouth = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.012, 0.01), this.darkMat);
    mouth.position.set(0, -0.055, r * 0.95);
    head.add(mouth);
    // --- 머리카락 캡: 머리 위·뒤쪽 반구를 hairColor 로 덮는다(앞머리 선은 이마까지) ---
    // 위 반구(phi 0~절반)를 덮고, 뒤로 더 내려 뒤통수·목덜미까지 가린다. 앞은 눈 위(이마)까지만.
    // thetaStart=0(위), thetaLength≈3/4π 로 위·뒤를 덮고 앞면 아래(얼굴)는 비운다. phi 는 뒤쪽으로 치우침.
    // 정수리 전체 캡(이마 위까지) + 뒤쪽 반(phi π~2π = -z 쪽)만 목덜미까지 내린 조각. 앞 얼굴은 비운다.
    const hairTop = new THREE.Mesh(new THREE.SphereGeometry(r * 1.04, 16, 8, 0, Math.PI * 2, 0, Math.PI * 0.4), this.hairMat);
    const hairBack = new THREE.Mesh(new THREE.SphereGeometry(r * 1.04, 16, 12, Math.PI, Math.PI, 0, Math.PI * 0.72), this.hairMat);
    [hairTop, hairBack].forEach((hair) => {
      hair.position.set(0, 0.01, 0);
      hair.castShadow = true;
      head.add(hair);
    });
    this.head = head;

    // 팔(어깨 피벗 → 위팔 → 팔꿈치 → 아래팔). 좌우.
    this.shoulderL = this._makeArm(mat, RIG_DIMS.shoulderX);
    this.shoulderR = this._makeArm(mat, -RIG_DIMS.shoulderX);
    this.spine.add(this.shoulderL.pivot);
    this.spine.add(this.shoulderR.pivot);

    // 다리(엉덩이 피벗 → 허벅지 → 무릎 → 정강이 → 발목). 좌우.
    this.hipL = this._makeLeg(mat, RIG_DIMS.hipX);
    this.hipR = this._makeLeg(mat, -RIG_DIMS.hipX);
    this.hips.add(this.hipL.pivot);
    this.hips.add(this.hipR.pivot);

    this.group.traverse((obj) => {
      if (obj.isMesh) obj.castShadow = true;
    });
  }

  _makeArm(mat, xOffset) {
    const pivot = new THREE.Group();
    pivot.position.set(xOffset, RIG_DIMS.shoulderY, 0);
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.055, 0.26, 4, 8), mat);
    upper.position.y = -0.15;
    pivot.add(upper);
    const elbow = new THREE.Group();
    elbow.position.y = -RIG_DIMS.upperArm;
    pivot.add(elbow);
    const fore = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.24, 4, 8), mat);
    fore.position.y = -0.14;
    elbow.add(fore);
    // 손 끝 표식(빈 Group, 보이지 않음) — IK 목표·검사가 손 위치를 읽는다(작업 148). 작업 209a: 손목(wrist) 피벗도 이것.
    const hand = new THREE.Group();
    hand.position.y = -FORE_ARM_M;
    elbow.add(hand);
    return { pivot, elbow, hand };
  }

  _makeLeg(mat, xOffset) {
    const pivot = new THREE.Group();
    pivot.position.set(xOffset, 0, 0);
    const thigh = new THREE.Mesh(new THREE.CapsuleGeometry(0.08, 0.4, 4, 8), mat);
    thigh.position.y = -0.24;
    pivot.add(thigh);
    const knee = new THREE.Group();
    knee.position.y = -RIG_DIMS.thigh;
    pivot.add(knee);
    const shin = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.4, 4, 8), mat);
    shin.position.y = -0.24;
    knee.add(shin);
    // 작업 209a: 발목(ankle) 피벗(빈 Group) — 정강이 끝. 발 IK 손잡이·발 위치를 읽는다.
    const ankle = new THREE.Group();
    ankle.position.y = -RIG_DIMS.shin;
    knee.add(ankle);
    return { pivot, elbow: knee, ankle };
  }

  /* 포즈·제스처·발화를 매 프레임 적용한다. t 는 장면 시각(초) — 걷기·끄덕임·반복 동작 위상에 쓴다.
     targetYaw 는 gestureTarget(상대) 쪽으로 돌릴 마네킹 로컬 yaw(rad, 없으면 null) — 상대가 필요한
     동작(뺨 감싸기·가슴에 손·기울이기·목 돌리기)의 팔·기울기·목 방향을 그 사람 쪽으로 잡는다.
     sit(084 R1.5·R1.17, 없으면 지금과 같다) = {hipY, legPitchDeg, turn} — 서버가 정한 앉을 자리. 엉덩이를
     hipY(m)에, 허벅지를 legPitchDeg 만큼 아래로, 정강이는 수직. turn(rad)은 다리 방향에서 몸통을 돌린 각. */
  apply(pose, gesture, speaking, t, targetYaw, sit) {
    // 기본 자세로 되돌린 뒤 포즈별로 굽힌다.
    this.pelvis.position.y = 0;
    this.hips.position.y = RIG_DIMS.hipsY;
    this.hips.rotation.set(0, 0, 0);
    this.spine.rotation.set(0, 0, 0);
    this.neck.rotation.set(0, 0, 0);
    this.headJoint.rotation.set(0, 0, 0);
    this._resetLimb(this.hipL, this.hipR, this.shoulderL, this.shoulderR);
    this.eyeMeshes.forEach((eye) => eye.scale.set(1, 1, 1));
    const seated = pose === "sit" && sit && typeof sit.hipY === "number" && isFinite(sit.hipY);

    switch (pose) {
      case "sit": {
        if (seated) {
          // 앉을 소품 윗면(sit.hipY, m)에 엉덩이. group 이 키 비율로 커지므로 로컬 단위로 나눈다.
          const scale = this.group.scale.y || 1;
          const theta = (Number(sit.legPitchDeg) || 0) * Math.PI / 180;
          this.hips.position.y = sit.hipY / scale;
          this._bendLeg(this.hipL, -(Math.PI / 2 - theta), Math.PI / 2 - theta);
          this._bendLeg(this.hipR, -(Math.PI / 2 - theta), Math.PI / 2 - theta);
          break;
        }
        // 의자 높이(≈0.45). 허벅지 앞으로, 무릎 굽힘.
        this.hips.position.y = 0.5;
        this._bendLeg(this.hipL, -Math.PI / 2, Math.PI / 2);
        this._bendLeg(this.hipR, -Math.PI / 2, Math.PI / 2);
        break;
      }
      case "kneel": {
        // 무릎이 바닥, 정강이 뒤로(관절로 굽힘 — 떨어진 상자 금지).
        this.hips.position.y = 0.55;
        this._bendLeg(this.hipL, -Math.PI / 2, Math.PI * 0.95);
        this._bendLeg(this.hipR, -Math.PI / 2, Math.PI * 0.95);
        break;
      }
      case "lie": {
        // 바닥에 누움: 엉덩이를 낮추고 몸 전체를 눕힌다.
        this.hips.position.y = 0.12;
        this.group.rotation.x = -Math.PI / 2;
        break;
      }
      case "walk": {
        // 다리를 번갈아 흔든다(위상).
        const swing = Math.sin(t * 4) * 0.5;
        this.hipL.pivot.rotation.x = swing;
        this.hipR.pivot.rotation.x = -swing;
        this.shoulderL.pivot.rotation.x = -swing * 0.6;
        this.shoulderR.pivot.rotation.x = swing * 0.6;
        break;
      }
      case "stand":
      default:
        break;
    }
    if (pose !== "lie") this.group.rotation.x = 0;

    this._applyGesture(gesture, t, targetYaw);

    if (speaking) {
      // 머리를 살짝 끄덕임.
      this.neck.rotation.x = Math.sin(t * 8) * 0.12;
    }

    if (seated && typeof sit.turn === "number" && isFinite(sit.turn)) {
      // 앉아서 몸을 돌린 자세: 다리는 그대로, 몸통·팔·머리만 facing 쪽으로(±90° 안, update 가 자른다).
      // 기울이기(lean_in)의 상대 쪽 돌림과 합쳐도 가슴 y 한계(±90°) 안.
      this.spine.rotation.y = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, this.spine.rotation.y + sit.turn));
    }
  }

  _resetLimb() {
    [this.hipL, this.hipR, this.shoulderL, this.shoulderR].forEach((limb) => {
      limb.pivot.rotation.set(0, 0, 0);
      limb.elbow.rotation.set(0, 0, 0);
      if (limb.hand) limb.hand.rotation.set(0, 0, 0);
      if (limb.ankle) limb.ankle.rotation.set(0, 0, 0);
    });
    // 팔은 기본적으로 몸 옆으로 내린다.
    this.shoulderL.pivot.rotation.z = 0.1;
    this.shoulderR.pivot.rotation.z = -0.1;
  }

  _bendLeg(leg, hipX, kneeX) {
    leg.pivot.rotation.x = hipX;
    leg.elbow.rotation.x = kneeX;
  }

  /* 작업 209a: rig 관절 이름 → 피벗(Object3D). 모르는 이름은 null. */
  rigObject(joint) {
    switch (joint) {
      case "spine": return this.spine;
      case "neck": return this.neck;
      case "head": return this.headJoint;
      case "shoulderL": return this.shoulderL.pivot;
      case "elbowL": return this.shoulderL.elbow;
      case "wristL": return this.shoulderL.hand;
      case "shoulderR": return this.shoulderR.pivot;
      case "elbowR": return this.shoulderR.elbow;
      case "wristR": return this.shoulderR.hand;
      case "hipL": return this.hipL.pivot;
      case "kneeL": return this.hipL.elbow;
      case "ankleL": return this.hipL.ankle;
      case "hipR": return this.hipR.pivot;
      case "kneeR": return this.hipR.elbow;
      case "ankleR": return this.hipR.ankle;
      default: return null;
    }
  }

  /* 지금 그려진 관절 값(로컬 Euler XYZ 라디안). 목록 자세에서 온 값이든 rig 값이든 보이는 그대로. */
  readJoint(joint) {
    const obj = this.rigObject(joint);
    if (!obj) return [0, 0, 0];
    const e = new THREE.Euler().setFromQuaternion(obj.quaternion, "XYZ");
    return [e.x || 0, e.y || 0, e.z || 0];
  }

  /* 작업 209a fix 1: 목록 자세(동작·손·자세·머리)가 만든 관절 값을 RIG_LIMITS 안으로(applyRig 앞). 안쪽 값은 건드리지 않는다
     (목록 동작·자세는 처음부터 한계 안 — 검사 q-l). 손 IK 의 드문 조합(자기 몸을 가리키기·옆·뒤 상대에 닿기)만 바뀐다.
     각도는 원으로 보고 가까운 한계로 자른다(어깨 166° = -194° 는 -180° 로). 돌려준 값 = 자른 관절 수(this.clamped 에도). */
  clampToLimits() {
    let count = 0;
    RIG_JOINTS.forEach((joint) => {
      const v = this.readJoint(joint);
      const lim = RIG_LIMITS[joint];
      let changed = false;
      const c = v.map((x, i) => {
        const lo = lim[i][0] * DEG;
        const hi = lim[i][1] * DEG;
        if (x >= lo - 1e-9 && x <= hi + 1e-9) return x;
        changed = true;
        const gap = (a) => Math.abs(Math.atan2(Math.sin(x - a), Math.cos(x - a)));
        return gap(lo) <= gap(hi) ? lo : hi;
      });
      if (changed) {
        this.rigObject(joint).rotation.set(c[0], c[1], c[2], "XYZ");
        count += 1;
      }
    });
    this.clamped = count;
    return count;
  }

  /* 작업 209a: 목록 자세(apply·applyBody·applyHands) 뒤에 부른다. rig 에 적힌 관절만 그 값으로 덮어쓰고 rootY 만큼 골반을 옮긴다.
     rig 가 없으면 아무것도 바꾸지 않는다(옛 계획은 지금과 같은 모양). */
  applyRig(rig) {
    if (!rig || typeof rig !== "object") return;
    RIG_JOINTS.forEach((joint) => {
      const v = rig[joint];
      if (!Array.isArray(v) || v.length !== 3) return;
      const x = Number(v[0]);
      const y = Number(v[1]);
      const z = Number(v[2]);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) return;
      this.rigObject(joint).rotation.set(x, y, z, "XYZ");
    });
    const rootY = Number(rig.rootY);
    if (typeof rig.rootY === "number" && isFinite(rootY)) this.pelvis.position.y = rootY;
  }

  _applyGesture(gesture, t, targetYaw) {
    const time = Number(t) || 0;
    // 상대 쪽으로 돌릴 각도(로컬 yaw, -π..π). 없으면 정면(0). 오른팔 뻗기를 그 각도로 흔든다.
    // 작업 209a fix 1: 관절마다 RIG_LIMITS 안으로(뒤에 있는 상대 쪽으로 몸이 한 바퀴 돌지 않게).
    const aim = typeof targetYaw === "number" && isFinite(targetYaw) ? Math.atan2(Math.sin(targetYaw), Math.cos(targetYaw)) : 0;
    const armAim = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, aim));
    // 자기 몸에 닿는 동작(작업 209a fix 1): 손이 그 부위에 닿게 2관절 IK(팔꿈치는 -x 로만 굽는다). 행렬을 먼저 갱신한다.
    const reachOwn = (arm, sideSign, point, pole) => {
      this.group.updateWorldMatrix(true, true);
      this._reachArm(arm, sideSign, point, pole);
    };
    const onHead = (x, y, z) => {
      this.group.updateWorldMatrix(true, true);
      return this.head.localToWorld(new THREE.Vector3(x, y, z));
    };
    switch (gesture) {
      case "point":
        // 오른팔을 앞으로 뻗는다.
        this.shoulderR.pivot.rotation.set(-Math.PI / 2, 0, -0.1);
        this.shoulderR.elbow.rotation.x = 0;
        break;
      case "bow":
        // 허리를 숙인다.
        this.spine.rotation.x = 0.7;
        break;
      case "hands_on_knees":
        // 두 손을 무릎 위에(서 있으면 닿는 데까지 무릎 쪽으로 뻗는다). 팔이 거의 곧아도 팔꿈치는 바깥·뒤(편집기 pole).
        this.group.updateWorldMatrix(true, true);
        reachOwn(this.shoulderL, 1, this.bodyPoint("knee", "left"), RIG_IK_POLES.arm);
        reachOwn(this.shoulderR, -1, this.bodyPoint("knee", "right"), RIG_IK_POLES.arm);
        break;
      case "cover_face":
        // 두 손바닥으로 얼굴(왼손은 왼쪽 반, 오른손은 오른쪽 반)을 가린다.
        reachOwn(this.shoulderL, 1, onHead(0.045, 0, 0.17));
        reachOwn(this.shoulderR, -1, onHead(-0.045, 0, 0.17));
        break;
      case "hand_to_own_face":
        // 오른손을 자기 오른뺨에.
        reachOwn(this.shoulderR, -1, onHead(-0.09, -0.03, 0.1));
        break;
      case "wipe_tears": {
        // 손등으로 오른눈 아래를 짧게 왕복(주기 ~0.7초).
        const wob = Math.sin(time * 9) * 0.025;
        reachOwn(this.shoulderR, -1, onHead(-0.045 + wob, -0.01, 0.17));
        break;
      }
      case "hand_on_other_cheek":
        // 오른팔을 앞·옆(상대 쪽)으로 뻗어 상대 얼굴 높이로(아래팔은 위로 조금 — 팔꿈치 -x).
        this.shoulderR.pivot.rotation.set(-Math.PI / 2, armAim, -0.1);
        this.shoulderR.elbow.rotation.x = -0.3;
        break;
      case "both_hands_other_cheeks":
        // 두 팔을 앞으로 뻗어 상대 얼굴을 감싼다.
        this.shoulderL.pivot.rotation.set(-Math.PI / 2, armAim, 0.15);
        this.shoulderR.pivot.rotation.set(-Math.PI / 2, armAim, -0.15);
        this.shoulderL.elbow.rotation.x = -0.3;
        this.shoulderR.elbow.rotation.x = -0.3;
        break;
      case "hand_on_chest":
        // 오른손을 자기 가슴에.
        this.group.updateWorldMatrix(true, true);
        reachOwn(this.shoulderR, -1, this.bodyPoint("chest"));
        break;
      case "press_hand_to_other_chest":
        // 한 손을 앞 상대 가슴 높이로 뻗는다(위팔 조금 아래, 아래팔 수평).
        this.shoulderR.pivot.rotation.set(-Math.PI / 2 + 0.1, armAim, -0.1);
        this.shoulderR.elbow.rotation.x = -0.1;
        break;
      case "lean_in":
        // 상체를 상대 쪽으로 0.35rad 기울이고 머리 약간 숙임.
        this.spine.rotation.x = 0.35;
        this.spine.rotation.y = aim * 0.5;
        this.neck.rotation.x = 0.2;
        break;
      case "laugh": {
        // 머리 뒤로 젖혔다 오기 + 어깨 들썩(반복, 주기 ~0.8초).
        const ph = Math.sin(time * 8);
        this.neck.rotation.x = -0.35 - ph * 0.15;
        this.spine.rotation.x = -0.1;
        this.shoulderL.pivot.rotation.x = ph * 0.15;
        this.shoulderR.pivot.rotation.x = ph * 0.15;
        break;
      }
      case "look_down":
        // 머리 숙임.
        this.neck.rotation.x = 0.4;
        break;
      case "look_up":
        // 머리 듦.
        this.neck.rotation.x = -0.4;
        break;
      case "nod": {
        // 끄덕임(반복, 주기 ~0.7초).
        this.neck.rotation.x = 0.15 + Math.sin(time * 9) * 0.2;
        break;
      }
      case "turn_head_to_other":
        // 목을 상대 쪽으로 돌린다(목 돌리기 한계 ±80° 안).
        this.neck.rotation.y = Math.max(-80 * DEG, Math.min(80 * DEG, aim));
        break;
      default:
        break;
    }
  }

  /* 작업 148: 자세(posture 기울기)·머리(head.turn/tilt)·눈(eyes). apply() 뒤에 부른다. 키가 없으면 아무것도 바꾸지 않는다
     (옛 cut actor 는 지금과 같은 회전). targetYaw 는 gestureTarget 쪽 로컬 yaw(없으면 null). */
  applyBody(ca, t, targetYaw) {
    const data = ca || {};
    const posture = POSE_POSTURES.indexOf(data.posture) >= 0 ? data.posture : "";
    if (posture === "lean_forward") this.spine.rotation.x += LEAN_RAD;
    else if (posture === "lean_back") this.spine.rotation.x -= LEAN_RAD;
    const head = data.head && typeof data.head === "object" ? data.head : null;
    if (head) {
      const aim = typeof targetYaw === "number" && isFinite(targetYaw)
        ? Math.atan2(Math.sin(targetYaw), Math.cos(targetYaw)) : null;
      const nod = data.speaking ? Math.sin((Number(t) || 0) * 8) * 0.12 : 0;
      switch (HEAD_TURNS.indexOf(head.turn) >= 0 ? head.turn : "") {
        case "toward_other":
          if (aim !== null) this.neck.rotation.y = Math.max(-1.3, Math.min(1.3, aim));
          break;
        case "away":
          this.neck.rotation.y = aim !== null && aim > 0 ? -0.9 : 0.9;
          break;
        case "down":
          this.neck.rotation.x = 0.45 + nod;
          break;
        case "up":
          this.neck.rotation.x = -0.45 + nod;
          break;
        case "front":
          this.neck.rotation.y = 0;
          break;
        default:
          break;
      }
      // 기울기: 인물 자신의 왼쪽(+x) 으로 = z 음수, 오른쪽 = z 양수.
      if (head.tilt === "left") this.neck.rotation.z = -0.25;
      else if (head.tilt === "right") this.neck.rotation.z = 0.25;
    }
    const eyes = EYE_STATES.indexOf(data.eyes) >= 0 ? data.eyes : "";
    const squash = eyes === "closed" ? 0.15 : eyes === "half" ? 0.5 : 1;
    this.eyeMeshes.forEach((eye) => eye.scale.set(1, squash, 1));
  }

  /* 카메라 여유 부피(작업 151): 머리 구(머리 중심, 0.16m × 키/1.7)·몸통 캡슐(두 어깨 가운데 → 엉덩이, 0.22m). 행렬은 호출자가 갱신해 둔다. */
  clearVolumes() {
    const scale = this.group.scale.y || 1;
    const center = this.head.getWorldPosition(new THREE.Vector3());
    const a = this.shoulderL.pivot.getWorldPosition(new THREE.Vector3())
      .add(this.shoulderR.pivot.getWorldPosition(new THREE.Vector3())).multiplyScalar(0.5);
    const b = this.hips.getWorldPosition(new THREE.Vector3());
    return [{ center, r: HEAD_CLEAR_RADIUS_M * scale }, { a, b, r: BODY_CLEAR_RADIUS_M }];
  }

  /* 신체 부위의 세계 위치(작업 148). side = 인물 자신의 "left"|"right"(뺨·무릎·어깨). near(세계 점)를 주면 좌우가 있는
     부위는 그 점에 가까운 쪽을 고른다(상대를 잡는 손이 가까운 뺨·어깨로 가게). 행렬은 호출자가 갱신해 둔다. */
  bodyPoint(part, side, near) {
    const pick = (makeL, makeR) => {
      const left = makeL();
      const right = makeR();
      if (near) return left.distanceToSquared(near) <= right.distanceToSquared(near) ? left : right;
      return side === "left" ? left : right;
    };
    const onHead = (x, y, z) => this.head.localToWorld(new THREE.Vector3(x, y, z));
    switch (part) {
      case "face":
        return onHead(0, 0, 0.16);
      case "eyes":
        return onHead(0, 0.02, 0.17);
      case "mouth":
        return onHead(0, -0.055, 0.15);
      case "chin":
        return onHead(0, -0.1, 0.09);
      case "head":
        return onHead(0, 0.15, 0.02);
      case "cheek":
        return pick(() => onHead(0.09, -0.03, 0.09), () => onHead(-0.09, -0.03, 0.09));
      case "cheek_under":
        // 뺨을 손에 괸 자세: 손이 뺨·턱선 아래.
        return pick(() => onHead(0.07, -0.12, 0.06), () => onHead(-0.07, -0.12, 0.06));
      case "neck":
        return this.neck.localToWorld(new THREE.Vector3(0, -0.02, 0.07));
      case "chest":
        return this.spine.localToWorld(new THREE.Vector3(0, 0.42, 0.19));
      case "shoulder":
        return pick(() => this.shoulderL.pivot.localToWorld(new THREE.Vector3(0, 0.06, 0)),
          () => this.shoulderR.pivot.localToWorld(new THREE.Vector3(0, 0.06, 0)));
      case "arm":
        return pick(() => this.shoulderL.pivot.localToWorld(new THREE.Vector3(0, -0.15, 0.06)),
          () => this.shoulderR.pivot.localToWorld(new THREE.Vector3(0, -0.15, 0.06)));
      case "hand":
        return pick(() => this.shoulderL.hand.localToWorld(new THREE.Vector3()),
          () => this.shoulderR.hand.localToWorld(new THREE.Vector3()));
      case "knee":
        // 무릎 쪽 허벅지 위(엉덩이에서 0.34m). 앉은 자세에서 손이 닿는 '무릎에 손' 자리.
        return pick(() => this.hipL.pivot.localToWorld(new THREE.Vector3(0, -0.34, 0.09)),
          () => this.hipR.pivot.localToWorld(new THREE.Vector3(0, -0.34, 0.09)));
      default:
        return null;
    }
  }

  /* 2관절 IK(작업 148): 어깨(피벗) → 팔꿈치 → 손끝이 세계 점 targetWorld 에서 끝나게 위팔·아래팔을 돌린다. 팔꿈치는
     아래·바깥으로 굽는다(pole). 닿지 않으면 그 방향으로 쭉 편다. 돌려준 값 = 목표까지 남은 거리(마네킹 로컬 m, 0 이면 닿음).
     작업 209a fix 1: 편집기와 같은 해석해(solveTwoBoneIK) — 팔꿈치는 로컬 x 경첩 하나로 -x(앞)로만, RIG_LIMITS 굽힘 안. */
  _reachArm(arm, sideSign, targetWorld, poleArg) {
    const parent = arm.pivot.parent;
    parent.updateMatrixWorld(true);
    const rel = parent.worldToLocal(targetWorld.clone()).sub(arm.pivot.position);
    const side = sideSign > 0 ? "L" : "R";
    const pl = poleArg || [0.6, -1, 0.1];
    const pole = new THREE.Vector3(sideSign * pl[0], pl[1], pl[2]);
    const res = solveTwoBoneIK(rel, UPPER_ARM_M, FORE_ARM_M, pole, -1, -RIG_LIMITS["elbow" + side][0][0] * DEG);
    arm.pivot.rotation.set(res.root[0], res.root[1], res.root[2]);
    arm.elbow.rotation.set(res.mid[0], res.mid[1], res.mid[2]);
    return Math.max(0, rel.length() - (UPPER_ARM_M + FORE_ARM_M));
  }

  /* 팔을 쭉 펴서 세계 점 쪽을 가리킨다(point). */
  _aimArm(arm, targetWorld) {
    const parent = arm.pivot.parent;
    parent.updateMatrixWorld(true);
    const rel = parent.worldToLocal(targetWorld.clone()).sub(arm.pivot.position);
    if (rel.lengthSq() < 1e-8) return;
    arm.pivot.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), rel.normalize());
    arm.elbow.rotation.set(0, 0, 0);
  }

  _restArm(arm, sideSign) {
    arm.pivot.rotation.set(0, 0, sideSign * 0.1);
    arm.elbow.rotation.set(0, 0, 0);
  }

  /* 손(작업 148): hands = {left?, right?: {act, target}}. ctx = {other: 상대 Mannequin|null, seated}. 행렬은 호출자가
     이 프레임 자세로 갱신해 둔다(apply·applyBody 뒤). 쉬는 손(rest·none·없음) = 팔 내림, 앉았으면 손을 무릎에. */
  applyHands(hands, ctx) {
    const opts = ctx || {};
    const other = opts.other || null;
    const order = [["right", this.shoulderR, -1], ["left", this.shoulderL, 1]];
    order.forEach(([side, arm]) => this._restArm(arm, side === "left" ? 1 : -1));
    order.forEach(([side, arm, sideSign]) => {
      const hand = hands && hands[side] && typeof hands[side] === "object" ? hands[side] : {};
      const act = HAND_ACTS.indexOf(hand.act) >= 0 ? hand.act : "rest";
      const target = HAND_TARGETS.indexOf(hand.target) >= 0 ? hand.target : "none";
      this.group.updateMatrixWorld(true);
      const shoulder = arm.pivot.localToWorld(new THREE.Vector3());
      let point = null;
      if (act !== "rest" && act !== "none" && target !== "none") {
        const own = target.indexOf("own_") === 0;
        let part = target.slice(own ? 4 : 6);
        if (own && act === "rest_on" && (part === "cheek" || part === "chin")) part = "cheek_under";
        const body = own ? this : other;
        if (body) {
          if (body !== this) body.group.updateMatrixWorld(true);
          point = body.bodyPoint(part, side, own ? null : shoulder);
        }
      }
      if (act === "point") {
        if (point) this._aimArm(arm, point);
        else this._aimArm(arm, this.spine.localToWorld(new THREE.Vector3(arm.pivot.position.x, arm.pivot.position.y, 1)));
        return;
      }
      if (point) {
        const short = this._reachArm(arm, sideSign, point);
        if (short > 0 && target.indexOf("other_") === 0) {
          // 닿지 않으면 상체를 상대 쪽으로 숙여(최대 0.45rad) 한 번 더 뻗는다.
          this.spine.rotation.x += Math.min(0.45, short * 1.5);
          this.group.updateMatrixWorld(true);
          this._reachArm(arm, sideSign, other.bodyPoint(target.slice(6), side, arm.pivot.localToWorld(new THREE.Vector3())));
        }
        return;
      }
      if (opts.seated) {
        this.group.updateMatrixWorld(true);
        this._reachArm(arm, sideSign, this.bodyPoint("knee", side, null));
      }
    });
  }

  dispose() {
    this.group.traverse((obj) => {
      if (obj.isMesh) {
        if (obj.geometry) obj.geometry.dispose();
      }
    });
    if (this.mat) this.mat.dispose();
    [this.faceMat, this.darkMat, this.hairMat].forEach((m) => {
      if (m && typeof m.dispose === "function") m.dispose();
    });
  }
}

/* ================================================================ 한 장면(Scene) 빌더

   방·빛·소품·인물 메시를 만들고, 시각(초)에 맞춰 카메라·인물을 갱신한다.
   오버레이(빛 출처·소품·인물 라벨)는 별도 레이어(여기서는 데이터만 노출)로 둔다. */
class PrevizScene {
  constructor(sceneData, resolveTexture, loader) {
    this.data = sceneData || {};
    this.resolveTexture = typeof resolveTexture === "function" ? resolveTexture : function () { return null; };
    this.loader = loader;
    this.root = new THREE.Group();
    this.actors = {}; // id → {mannequin, data}
    this.timeline = (this.data.timeline || []).slice();
    this.disposables = [];
    this.overlayItems = []; // 편집 오버레이용(빛·소품·인물) 3D 위치 + 라벨

    this._buildRoom();
    this._buildLight();
    this._buildProps();
    this._buildActors();
  }

  get startTime() {
    if (!this.timeline.length) return 0;
    return Number(this.timeline[0].start) || 0;
  }

  get endTime() {
    if (!this.timeline.length) return 0;
    return Number(this.timeline[this.timeline.length - 1].end) || 0;
  }

  _track(obj) {
    this.disposables.push(obj);
    return obj;
  }

  _texturedMaterial(url, fallbackColor, side) {
    // url 은 서버 텍스처 API 경로(또는 빈 값). resolveTexture 가 미리 받아 둔 같은 출처 blob
    // 오브젝트 URL 로 바꾼다 — S3 직통(crossOrigin)로 불러오면 버킷에 CORS 가 없어 실패하거나
    // 캔버스가 오염돼 녹화(captureStream)가 막히기 때문. 없거나 실패하면 색으로 둔다.
    const mat = new THREE.MeshStandardMaterial({ color: colorOf(fallbackColor), roughness: 0.95, side });
    this._track(mat);
    const blobUrl = url ? this.resolveTexture(url) : null;
    if (blobUrl && this.loader) {
      this.loader.load(
        blobUrl,
        (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          mat.map = tex;
          mat.color.set(0xffffff);
          mat.needsUpdate = true;
          this._track(tex);
        },
        undefined,
        () => {
          // 같은 출처 blob 이므로 거의 실패하지 않지만, 혹시 디코드 실패하면 색 유지 + 경고.
          console.warn("[previz] 텍스처 디코드에 실패해 색으로 대체합니다:", url);
        }
      );
    } else if (url && !blobUrl) {
      // 텍스처를 못 받았다(아래 preloadTextures 에서 경고 1회). 색으로 보이게 둔다.
    }
    return mat;
  }

  _buildRoom() {
    const room = this.data.room || {};
    const width = Number(room.width) || 6;
    const depth = Number(room.depth) || 6;
    const height = Number(room.height) || 2.8;
    this.room = { width, depth, height };

    // 바닥
    const floorMat = this._texturedMaterial(room.floorTextureUrl, room.floorColor || "#8a8a8a", THREE.FrontSide);
    const floor = new THREE.Mesh(this._track(new THREE.PlaneGeometry(width, depth)), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.root.add(floor);

    // 벽 4면(안쪽을 본다). 텍스처 있으면 이미지, 없으면 색. 천장 없음(위에서 빛 보기 쉽게).
    const walls = room.walls || {};
    SIDES.forEach((side) => {
      const wall = walls[side] || {};
      const t = wallTransform(side, width, depth);
      const mat = this._texturedMaterial(wall.textureUrl, wall.color || "#cfcfcf", THREE.FrontSide);
      const mesh = new THREE.Mesh(this._track(new THREE.PlaneGeometry(t.w, height)), mat);
      mesh.position.set(t.x, height / 2, t.z);
      mesh.rotation.y = t.ry;
      mesh.receiveShadow = true;
      this.root.add(mesh);

      // openings(창문·문): 밝은 면/어두운 면으로 표시. 창문은 밝게, 문은 어둡게.
      (wall.openings || []).forEach((op) => {
        const ow = Math.max(0.2, Number(op.w) || 1);
        const oh = Math.max(0.2, Number(op.h) || 1);
        const isWindow = (op.kind || "window") === "window";
        const opMat = new THREE.MeshBasicMaterial({ color: isWindow ? 0xfff6d8 : 0x3a3a3a });
        this._track(opMat);
        const panel = new THREE.Mesh(this._track(new THREE.PlaneGeometry(ow, oh)), opMat);
        // 벽 평면 로컬 좌표(가로 x, 세로 y). 벽면에서 살짝 안쪽으로 띄운다.
        panel.position.set(Number(op.x) || 0, Number(op.y) || oh / 2, 0.02);
        mesh.add(panel);
      });
    });
  }

  _buildLight() {
    const light = this.data.light || {};
    const from = vec3(light.from, 0, this.room.height, -this.room.depth / 2);
    const to = vec3(light.to, 0, 0, 0);

    const ambient = new THREE.AmbientLight(0xffffff, 0.4);
    this.root.add(ambient);

    const dir = new THREE.DirectionalLight(colorOf(light.color, "#fff4e0"), Math.max(0.6, Number(light.intensity) || 1.2) * 1.6);
    dir.position.copy(from);
    dir.target.position.copy(to);
    dir.castShadow = true;
    dir.shadow.mapSize.set(1024, 1024);
    const span = Math.max(this.room.width, this.room.depth);
    dir.shadow.camera.left = -span;
    dir.shadow.camera.right = span;
    dir.shadow.camera.top = span;
    dir.shadow.camera.bottom = -span;
    dir.shadow.camera.near = 0.1;
    dir.shadow.camera.far = span * 4;
    this.root.add(dir);
    this.root.add(dir.target);

    // 편집 오버레이: 빛 출처(해 아이콘)와 방향 화살표. labelKo. 녹화엔 미포함.
    this.overlayItems.push({
      type: "light",
      from: from.clone(),
      to: to.clone(),
      labelKo: light.labelKo || "빛",
    });
  }

  _buildProps() {
    (this.data.props || []).forEach((prop) => {
      const size = Array.isArray(prop.size) ? prop.size : [0.5, 0.5, 0.5];
      const w = Math.max(0.05, Number(size[0]) || 0.5);
      const h = Math.max(0.05, Number(size[1]) || 0.5);
      const d = Math.max(0.05, Number(size[2]) || 0.5);
      const mat = new THREE.MeshStandardMaterial({ color: colorOf(prop.color, "#b0a080"), roughness: 0.85 });
      this._track(mat);
      let geo;
      if ((prop.shape || "box") === "cylinder") {
        geo = new THREE.CylinderGeometry(w / 2, w / 2, h, 16);
      } else {
        geo = new THREE.BoxGeometry(w, h, d);
      }
      this._track(geo);
      const mesh = new THREE.Mesh(geo, mat);
      const pos = vec3(prop.pos, 0, h / 2, 0);
      mesh.position.copy(pos);
      mesh.rotation.y = Number(prop.rotY) || 0;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.root.add(mesh);

      this.overlayItems.push({
        type: "prop",
        pos: new THREE.Vector3(pos.x, pos.y + h / 2 + 0.1, pos.z),
        labelKo: prop.labelKo || prop.labelEn || "",
      });
    });
  }

  _buildActors() {
    (this.data.actors || []).forEach((actor) => {
      const mannequin = new Mannequin(actor.color, actor.heightM, actor.hairColor);
      this.root.add(mannequin.group);
      this.actors[actor.id] = { mannequin, data: actor };
      this.disposables.push({ dispose: () => mannequin.dispose() });
    });
  }

  /* 시각 t(초, 콘티 전체 기준)에 맞는 컷을 찾는다. 경계 밖이면 가장 가까운 컷. */
  _cutAt(t) {
    if (!this.timeline.length) return null;
    for (const cut of this.timeline) {
      if (t >= Number(cut.start) && t < Number(cut.end)) return cut;
    }
    // 마지막 컷 끝이면 마지막 컷.
    const last = this.timeline[this.timeline.length - 1];
    if (t >= Number(last.end)) return last;
    return this.timeline[0];
  }

  /* 카메라·인물을 시각 t 에 맞춰 갱신한다. 컷 경계는 하드 컷(즉시 전환).
     작업 201: forcedCut 이 있으면 그 컷을 그린다(편집기의 키 스크러버 — 컷 끝 t 에서도 그 컷의 끝 자세). */
  update(t, camera, forcedCut) {
    const cut = forcedCut || this._cutAt(t);
    if (!cut) return;
    const start = Number(cut.start) || 0;
    const end = Number(cut.end) || start;
    const span = Math.max(0.001, end - start);
    const frac = clamp01((t - start) / span);

    // 카메라: moveTo(끝 키) 있으면 보간(cameraPoseAt), 없으면 고정.
    const cam = cut.camera || {};
    const pose = cameraPoseAt(cam, frac, this.room.depth);
    const look = pose.target.clone();
    camera.position.copy(pose.pos);
    camera.lookAt(look);
    this.cameraLook = look.clone();
    this.drawnCut = cut;
    // 작업 200: 기울기(roll, 도). lookAt 이 매 프레임 방향을 새로 정하므로 쌓이지 않는다.
    const roll = Number(cam.roll) || 0;
    if (roll) camera.rotateZ(Math.max(-45, Math.min(45, roll)) * Math.PI / 180);
    if (cam.fov && camera.fov !== cam.fov) {
      camera.fov = Number(cam.fov);
      camera.updateProjectionMatrix();
    }

    // 인물: 위치(moveTo 선형 이동)·방향·포즈·제스처·발화.
    const cutActors = cut.actors || [];
    const seen = {};
    // 먼저 각 인물의 이번 프레임 발 위치(이동 보간 포함)를 모은다 — gestureTarget 방향 계산에 쓴다.
    const posById = {};
    cutActors.forEach((ca) => {
      if (!this.actors[ca.id]) return;
      const base = vec3(ca.pos, 0, 0, 0);
      let x = base.x;
      let z = base.z;
      if (Array.isArray(ca.moveTo)) {
        const to = vec3(ca.moveTo, base.x, 0, base.z);
        x = lerp(base.x, to.x, frac);
        z = lerp(base.z, to.z, frac);
      }
      posById[ca.id] = { x, z };
    });
    const handJobs = [];
    cutActors.forEach((ca) => {
      const entry = this.actors[ca.id];
      if (!entry) return;
      seen[ca.id] = true;
      const self = posById[ca.id] || { x: 0, z: 0 };
      const px = self.x;
      const pz = self.z;
      const facing = (Number(ca.facingDeg) || 0) * Math.PI / 180;
      entry.mannequin.group.position.set(px, 0, pz);
      // 앉을 자리(084 R1.17): 엉덩이·다리는 sit.legYawDeg, 몸통·머리는 facing 쪽(다리 방향 ±90° 안).
      // 카메라는 서버가 facingDeg 로 계산했다. sit 이 없으면 지금처럼 몸 전체가 facing.
      let bodyYaw = facing;
      let sitArg = null;
      const sitData = ca.sit;
      if ((ca.pose || "stand") === "sit" && sitData && typeof sitData.hipY === "number" && isFinite(sitData.hipY)) {
        const legYaw = (typeof sitData.legYawDeg === "number" && isFinite(sitData.legYawDeg))
          ? sitData.legYawDeg * Math.PI / 180
          : facing;
        let turn = Math.atan2(Math.sin(facing - legYaw), Math.cos(facing - legYaw));
        turn = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, turn));
        bodyYaw = legYaw + turn;
        entry.mannequin.group.rotation.y = legYaw;
        sitArg = { hipY: sitData.hipY, legPitchDeg: Number(sitData.legPitchDeg) || 0, turn: turn };
      } else {
        entry.mannequin.group.rotation.y = facing;
      }
      // gestureTarget 이 있으면 그 상대 쪽으로 돌릴 마네킹 로컬 yaw 를 구한다(세계 방위 - 내 몸통 방향).
      let targetYaw = null;
      const tgt = ca.gestureTarget ? posById[ca.gestureTarget] : null;
      if (tgt) {
        // three 의 +z 를 '앞'으로 쓰므로 atan2(dx, dz) 가 세계 yaw. 내 몸통 방향을 빼 로컬로.
        const worldYaw = Math.atan2(tgt.x - px, tgt.z - pz);
        targetYaw = worldYaw - bodyYaw;
      }
      entry.mannequin.apply(ca.pose || "stand", ca.gesture || null, !!ca.speaking, t, targetYaw, sitArg);
      // 작업 148: 자세 기울기·머리·눈(키가 없으면 그대로). 손은 모든 인물의 이번 프레임 자세가 정해진 뒤(아래) 푼다.
      entry.mannequin.applyBody(ca, t, targetYaw);
      if (ca.hands && typeof ca.hands === "object") handJobs.push({ entry, ca });

      // 인물 라벨 오버레이 위치(머리 위). 앉을 자리가 있으면 엉덩이 + 상체 높이.
      const heightM = Number(entry.data.heightM) || 1.7;
      const labelY = sitArg ? sitArg.hipY + 0.86 * heightM / 1.7 + 0.2 : heightM + 0.2;
      entry._overlayPos = new THREE.Vector3(px, labelY, pz);
    });
    if (handJobs.length) {
      // 손 IK: 내 몸(own_*)과 상대(other_* = gestureTarget, 이 컷에 있는 인물만)의 같은 프레임 뼈 위치를 쓴다.
      this.root.updateMatrixWorld(true);
      handJobs.forEach(({ entry, ca }) => {
        const otherEntry = ca.gestureTarget && posById[ca.gestureTarget] ? this.actors[ca.gestureTarget] : null;
        const seated = (ca.pose || "stand") === "sit" || ca.posture === "sit";
        entry.mannequin.applyHands(ca.hands, { other: otherEntry ? otherEntry.mannequin : null, seated });
      });
    }
    // 작업 209a fix 1: 목록 자세가 만든 관절 값은 RIG_LIMITS 안으로(포즈 편집을 시작해도 값이 튀지 않는다).
    cutActors.forEach((ca) => {
      const entry = this.actors[ca.id];
      if (entry) entry.mannequin.clampToLimits();
    });
    // 작업 209a: 직접 잡은 관절(cut actor 의 rig)은 목록 자세·손·머리·동작 위에 덮어쓴다. 재생·녹화도 이 경로다.
    // rig 가 없는 옛 계획은 아무것도 바뀌지 않는다.
    cutActors.forEach((ca) => {
      const entry = this.actors[ca.id];
      if (entry && ca.rig && typeof ca.rig === "object") entry.mannequin.applyRig(ca.rig);
    });
    // 이 컷에 없는 인물은 숨긴다.
    Object.keys(this.actors).forEach((id) => {
      this.actors[id].mannequin.group.visible = !!seen[id];
    });
    // 작업 151: 카메라가 보이는 마네킹 머리·몸통 안이거나 0.25m 안이면 시선 반대로 물려 그린다(설계 cut.camera 는 그대로).
    // 작업 200: 사용자가 직접 놓은 카메라(cam.manual)는 물리지 않는다 — 놓은 자리 그대로 그리고, 경고는 편집 패널이 보인다.
    this.root.updateMatrixWorld(true);
    const volumes = [];
    Object.keys(this.actors).forEach((id) => {
      const mannequin = this.actors[id].mannequin;
      if (mannequin.group.visible) mannequin.clearVolumes().forEach((v) => volumes.push(v));
    });
    const viewDir = look.clone().sub(camera.position);
    if (!cam.manual && volumes.length && viewDir.lengthSq() > 1e-12) {
      this.cameraPullback = clearCameraPosition(camera.position, viewDir.normalize(), volumes, RENDER_MIN_CLEAR_M);
    } else {
      this.cameraPullback = 0;
    }
  }

  /* 편집 오버레이용 항목(빛·소품·인물 라벨 + 3D 위치). 녹화에는 쓰지 않는다. */
  overlays() {
    const items = this.overlayItems.slice();
    Object.keys(this.actors).forEach((id) => {
      const entry = this.actors[id];
      if (entry.mannequin.group.visible && entry._overlayPos) {
        items.push({
          type: "actor",
          pos: entry._overlayPos.clone(),
          labelKo: entry.data.name || "",
          color: entry.data.color || "#ffffff",
        });
      }
    });
    return items;
  }

  dispose() {
    this.disposables.forEach((obj) => {
      try {
        if (obj && typeof obj.dispose === "function") obj.dispose();
      } catch (error) {
        /* 무시 */
      }
    });
    this.disposables = [];
    this.actors = {};
  }
}

/* ================================================================ 컨트롤러

   여러 장면(장소)을 담고, 현재 장면 하나를 그린다. 재생·탐색·장면 전환·녹화를 제공한다.
   오버레이(빛·소품·인물 라벨)는 host 위에 HTML 레이어로 그리고, 녹화 중에는 숨긴다. */
function createController(host, options) {
  const opts = options || {};
  const width = Number(opts.width) || DEFAULT_WIDTH;
  const height = Number(opts.height) || DEFAULT_HEIGHT;
  const scenesData = Array.isArray(opts.scenes) ? opts.scenes : [];

  // --- 렌더러 --- (작업 201: opts.renderer 는 검사용 가짜 렌더러 — 없으면 WebGL)
  const renderer = opts.renderer || new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height, false);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const canvas = renderer.domElement;
  canvas.className = "vnew-previz-canvas";
  // 뷰포트에 꽉 차게(16:9 는 host 쪽 CSS 가 맞춘다).
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  canvas.style.display = "block";

  // --- 오버레이 레이어(HTML) ---
  const overlay = document.createElement("div");
  overlay.className = "vnew-previz-overlay";

  // host 정리 후 붙인다.
  const stage = document.createElement("div");
  stage.className = "vnew-previz-stage";
  // 작업 201: 단축키는 뷰포트에 포커스가 있을 때만(stage 의 keydown) 받는다.
  stage.tabIndex = 0;
  stage.appendChild(canvas);
  stage.appendChild(overlay);
  while (host.firstChild) host.removeChild(host.firstChild);
  host.appendChild(stage);

  const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 200);
  // 같은 출처 blob URL 에서만 읽는다 — crossOrigin 을 켜지 않는다(S3 직통 금지).
  const loader = new THREE.TextureLoader();

  // 텍스처는 vnew 쪽 textureFetch(Authorization 헤더 → blob → objectURL)로 미리 받아 둔다.
  // apiUrl(서버 텍스처 경로) → objectURL. 실패하면 null(색으로 대체). dispose 때 revoke 한다.
  const textureFetch = typeof opts.textureFetch === "function" ? opts.textureFetch : null;
  const textureObjectUrls = {}; // apiUrl → objectURL(blob) | null

  function collectTextureUrls() {
    const urls = {};
    scenesData.forEach((sd) => {
      const room = (sd && sd.room) || {};
      const walls = room.walls || {};
      SIDES.forEach((side) => {
        const u = (walls[side] || {}).textureUrl;
        if (u) urls[u] = true;
      });
      if (room.floorTextureUrl) urls[room.floorTextureUrl] = true;
    });
    return Object.keys(urls);
  }

  function preloadTextures() {
    const urls = collectTextureUrls();
    if (!urls.length || !textureFetch) return Promise.resolve();
    return Promise.all(
      urls.map((apiUrl) =>
        Promise.resolve()
          .then(() => textureFetch(apiUrl))
          .then((objectUrl) => {
            textureObjectUrls[apiUrl] = objectUrl || null;
            if (!objectUrl) console.warn("[previz] 텍스처를 불러오지 못해 색으로 대체합니다:", apiUrl);
          })
          .catch((error) => {
            textureObjectUrls[apiUrl] = null;
            console.warn("[previz] 텍스처를 불러오지 못해 색으로 대체합니다:", apiUrl, error);
          })
      )
    );
  }

  function resolveTexture(apiUrl) {
    // 미리 받아 둔 같은 출처 blob objectURL. 없으면 null(색 유지).
    return Object.prototype.hasOwnProperty.call(textureObjectUrls, apiUrl)
      ? textureObjectUrls[apiUrl]
      : null;
  }

  function revokeTextures() {
    Object.keys(textureObjectUrls).forEach((apiUrl) => {
      const objectUrl = textureObjectUrls[apiUrl];
      if (objectUrl) {
        try {
          URL.revokeObjectURL(objectUrl);
        } catch (error) {
          /* 무시 */
        }
      }
      delete textureObjectUrls[apiUrl];
    });
  }

  const threeScene = new THREE.Scene();
  threeScene.background = new THREE.Color(0x1b1d21);

  // --- 장면들 빌드(필요할 때만 교체해 메모리 절약) ---
  let current = null; // {index, scene}
  let totalDuration = 0;
  scenesData.forEach((sd) => {
    const tl = sd.timeline || [];
    if (tl.length) totalDuration = Math.max(totalDuration, Number(tl[tl.length - 1].end) || 0);
  });

  const listeners = [];
  let playing = false;
  let recording = false;
  let clockT = 0; // 현재 재생 시각(콘티 전체 기준)
  let rafId = 0;
  let lastFrame = 0;

  function buildScene(index) {
    if (current && current.index === index) return;
    if (current) {
      threeScene.remove(current.scene.root);
      current.scene.dispose();
      current = null;
    }
    const sd = scenesData[index];
    if (!sd) return;
    const scene = new PrevizScene(sd, resolveTexture, loader);
    threeScene.add(scene.root);
    current = { index, scene };
  }

  function sceneIndexForTime(t) {
    // 시각에 해당하는 장면(timeline start~end 포함)을 찾는다.
    for (let i = 0; i < scenesData.length; i++) {
      const tl = scenesData[i].timeline || [];
      if (!tl.length) continue;
      const s = Number(tl[0].start) || 0;
      const e = Number(tl[tl.length - 1].end) || 0;
      if (t >= s && t < e) return i;
    }
    // 끝을 넘겼으면 마지막 장면.
    return Math.max(0, scenesData.length - 1);
  }

  function renderAt(t) {
    const idx = sceneIndexForTime(t);
    buildScene(idx);
    // 작업 201: 편집기 키 스크러버가 컷을 고정했으면(재생·녹화 중이 아닐 때) 그 컷의 그 시각을 그린다.
    const pin = !recording && !playing && edit.pinCut ? edit.pinCut : null;
    if (current) {
      if (pin) current.scene.update(pinTime(pin), camera, pin);
      else current.scene.update(t, camera);
    }
    syncHelpers();
    // 녹화(record·renderFrames)는 언제나 촬영 카메라로, 편집 도우미(격자·카메라 아이콘·시야·기즈모·선택 표시) 없이 그린다.
    helpers.visible = edit.helpersOn && !recording;
    renderer.render(threeScene, recording ? camera : viewCamera());
    if (!recording) drawOverlay();
  }

  // 초기 장면·첫 프레임은 텍스처를 받은 뒤(create 의 Promise) 그린다 — 텍스처 없이 먼저 그리면
  // 한 프레임 동안 색만 보이다 바뀌는 깜빡임이 생긴다.

  function emitTime(t) {
    listeners.forEach((cb) => {
      try {
        cb(t);
      } catch (error) {
        /* 무시 */
      }
    });
  }

  /* 오버레이: 빛 출처(작은 해+화살표·라벨), 소품·인물 라벨. 3D 위치를 화면 좌표로 투영한다. */
  function drawOverlay() {
    if (!current) {
      overlay.innerHTML = "";
      return;
    }
    const items = current.scene.overlays();
    const rect = { w: canvas.clientWidth || width, h: canvas.clientHeight || height };
    const html = [];
    items.forEach((item) => {
      // 작업 207: [이름표] 끔 = 장소·소품·빛 이름표를 숨긴다. 인물 이름표는 그대로.
      if (!edit.labelsOn && item.type !== "actor") return;
      const anchor = item.pos || item.from;
      if (!anchor) return;
      const projected = anchor.clone().project(viewCamera());
      if (projected.z > 1) return; // 카메라 뒤
      const x = (projected.x * 0.5 + 0.5) * rect.w;
      const y = (-projected.y * 0.5 + 0.5) * rect.h;
      if (x < -40 || y < -40 || x > rect.w + 40 || y > rect.h + 40) return;
      let cls = "vnew-previz-tag";
      let icon = "";
      if (item.type === "light") {
        cls += " is-light";
        icon = "☀ ";
      } else if (item.type === "actor") {
        cls += " is-actor";
      } else {
        cls += " is-prop";
      }
      const style = `left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;`;
      const dot =
        item.type === "actor" && item.color
          ? `<i class="vnew-previz-dot" style="background:${escapeAttr(item.color)}"></i>`
          : "";
      html.push(
        `<span class="${cls}" style="${style}">${dot}${icon}${escapeHtml(item.labelKo || "")}</span>`
      );
    });
    overlay.innerHTML = html.join("");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }
  function escapeAttr(s) {
    return String(s).replace(/["<>]/g, "");
  }

  function loop(now) {
    if (!playing) return;
    if (!lastFrame) lastFrame = now;
    const dt = (now - lastFrame) / 1000;
    lastFrame = now;
    clockT += dt;
    if (clockT >= totalDuration) {
      clockT = totalDuration;
      renderAt(clockT);
      emitTime(clockT);
      pause();
      return;
    }
    renderAt(clockT);
    emitTime(clockT);
    rafId = window.requestAnimationFrame(loop);
  }

  function play() {
    if (playing || recording) return;
    unpin();
    leavePose(); // 작업 209a: 재생 중엔 포즈 모드가 꺼진다
    if (clockT >= totalDuration) clockT = 0;
    playing = true;
    lastFrame = 0;
    rafId = window.requestAnimationFrame(loop);
  }

  function pause() {
    playing = false;
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  function seek(t) {
    unpin();
    clockT = Math.max(0, Math.min(totalDuration, Number(t) || 0));
    renderAt(clockT);
    emitTime(clockT);
  }

  function setScene(index) {
    const tl = (scenesData[index] || {}).timeline || [];
    if (tl.length) seek(Number(tl[0].start) || 0);
  }

  /* 녹화: fromT~toT 를 실시간 재생하며 canvas.captureStream(24) + MediaRecorder 로 webm 을 만든다.
     오버레이는 숨긴다(녹화에 안 나오게). 재생 중이면 멈추고 녹화 끝나면 원복한다. */
  function record(fromT, toT) {
    return new Promise((resolve, reject) => {
      if (recording) {
        reject(new Error("이미 녹화 중입니다."));
        return;
      }
      const from = Math.max(0, Number(fromT) || 0);
      const to = Math.min(totalDuration, Number(toT) || totalDuration);
      if (to <= from) {
        reject(new Error("녹화 구간이 비었습니다."));
        return;
      }
      if (!canvas.captureStream) {
        reject(new Error("이 브라우저는 화면 녹화를 지원하지 않습니다."));
        return;
      }

      pause();
      recording = true;
      leavePose(); // 작업 209a
      overlay.style.display = "none";

      let stream;
      try {
        stream = canvas.captureStream(RECORD_FPS);
      } catch (error) {
        recording = false;
        overlay.style.display = "";
        // 캔버스가 오염되면(텍스처를 교차 출처로 불러온 경우) captureStream 이 SecurityError 를 던진다.
        // 이 구현은 텍스처를 같은 출처 blob 으로만 입히므로 정상 경로에선 일어나지 않지만, 혹시
        // 오염됐으면 사용자에게 원인을 알린다.
        const tainted = error && (error.name === "SecurityError" || /secur|taint/i.test(String(error.message || "")));
        reject(tainted ? new Error("텍스처를 불러오지 못해 녹화할 수 없습니다.") : error);
        return;
      }

      // vp9 우선, 안 되면 vp8, 그마저 안 되면 기본 webm.
      let mime = "";
      const candidates = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
      for (const c of candidates) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) {
          mime = c;
          break;
        }
      }
      let rec;
      try {
        rec = mime ? new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 6e6 }) : new MediaRecorder(stream);
      } catch (error) {
        recording = false;
        overlay.style.display = "";
        reject(error);
        return;
      }

      const chunks = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      rec.onstop = () => {
        recording = false;
        overlay.style.display = "";
        const blob = new Blob(chunks, { type: "video/webm" });
        // 녹화 끝 상태를 화면에 반영(오버레이 복구).
        renderAt(clockT);
        resolve(blob);
      };
      rec.onerror = (event) => {
        recording = false;
        overlay.style.display = "";
        reject((event && event.error) || new Error("녹화 중 오류가 발생했습니다."));
      };

      // 실시간 재생 루프로 구간을 돌린다.
      clockT = from;
      renderAt(clockT);
      rec.start();
      const t0 = performance.now();
      const recLoop = (now) => {
        if (!recording) return;
        const t = from + (now - t0) / 1000;
        if (t >= to) {
          clockT = to;
          renderAt(clockT);
          try {
            rec.stop();
          } catch (error) {
            /* onstop 이 처리 */
          }
          return;
        }
        clockT = t;
        renderAt(clockT);
        window.requestAnimationFrame(recLoop);
      };
      window.requestAnimationFrame(recLoop);
    });
  }

  /* 프레임 단위 오프라인 렌더: fromT~toT 를 fps 로 쪼개, 각 시각 t = fromT + i/fps 에 장면을 그 시각으로
     '설정해 한 번 그리고' canvas.toBlob('image/jpeg', 0.85) 로 프레임을 모은다. 실시간·rAF·captureStream
     과 무관하므로 창이 가려지거나 느린 PC 여도 프레임이 빠지지 않는다. 오버레이는 숨긴다(결과에 안 나오게).
     프레임 수 = round((toT-fromT)*fps). onProgress(done, total) 를 프레임마다 부른다(선택).
     반환: Promise<Blob[]>(jpeg blob 배열, 순서대로). */
  function renderFrames(fromT, toT, fps, onProgress) {
    var rate = Number(fps) || RECORD_FPS;
    if (rate < 1) rate = 1;
    var from = Math.max(0, Number(fromT) || 0);
    var to = Math.min(totalDuration, Number(toT) || totalDuration);
    var count = Math.round((to - from) * rate);
    var progress = typeof onProgress === "function" ? onProgress : function () {};
    if (!(count > 0)) {
      return Promise.reject(new Error("렌더할 프레임이 없습니다."));
    }

    // 재생 중이면 멈추고, 오버레이를 숨긴다(renderAt 가 recording 일 때 오버레이를 안 그린다).
    pause();
    var wasRecording = recording;
    recording = true;
    leavePose(); // 작업 209a
    overlay.style.display = "none";

    var frames = [];
    var index = 0;

    function toBlobAsync() {
      return new Promise(function (resolve, reject) {
        try {
          // canvas.toBlob 가 없는 환경은 dataURL 로 대체한다.
          if (canvas.toBlob) {
            canvas.toBlob(
              function (blob) {
                if (blob) resolve(blob);
                else reject(new Error("프레임을 만들지 못했습니다."));
              },
              "image/jpeg",
              0.85
            );
          } else {
            var dataUrl = canvas.toDataURL("image/jpeg", 0.85);
            var binary = atob(dataUrl.split(",")[1]);
            var bytes = new Uint8Array(binary.length);
            for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            resolve(new Blob([bytes], { type: "image/jpeg" }));
          }
        } catch (error) {
          reject(error);
        }
      });
    }

    function step() {
      if (index >= count) {
        return Promise.resolve(frames);
      }
      var t = from + index / rate;
      // 마지막 프레임이 경계를 살짝 넘지 않게 한다.
      if (t > to) t = to;
      renderAt(t);
      return toBlobAsync().then(function (blob) {
        frames.push(blob);
        index += 1;
        progress(index, count);
        // 다음 프레임은 매크로태스크로 넘겨 UI 가 멈추지 않게 한다(실시간 재생이 아님).
        return new Promise(function (resolve) {
          window.setTimeout(resolve, 0);
        }).then(step);
      });
    }

    return step()
      .then(function (result) {
        recording = wasRecording;
        overlay.style.display = "";
        renderAt(clockT);
        return result;
      })
      .catch(function (error) {
        recording = wasRecording;
        overlay.style.display = "";
        renderAt(clockT);
        throw error;
      });
  }

  /* ================================================================ 작업 201: 편집기

     작업 시점(자유 궤도 카메라, OrbitControls) / 카메라 시점(촬영 카메라로 보기 — 녹화되는 화면).
     선택(Q)·이동(W)·회전(E)·맞춰 보기(F), 눈금 맞춤(10cm / 15°), 카메라 시점 드래그 = 대상 둘레로 돌기 ·
     Shift+드래그 = 평행 이동 · 휠 = 앞뒤로. 시작/끝 키(cut.camera.pos·target / cut.camera.moveTo).
     되돌리기는 컷 JSON 스냅숏(최대 50단계, 한 번 끈 것 = 한 단계). 고친 값만 manual 표시(작업 200).
     도우미(격자·카메라 아이콘·시야선·바라보는 곳 십자·기즈모·선택 상자)는 helpers 묶음 하나 — 녹화 중엔 숨긴다. */
  const edit = {
    view: "shot",
    tool: "select",
    snap: false,
    sel: null, // {type: "actor", id} | {type: "camera"} | {type: "target"} | null
    aim: false, // [이 인물 보기]: 다음에 누른 인물 머리로 바라보는 곳을 옮긴다
    keyU: 0, // 키 스크러버(컷 안 0~1). 끝 키가 있는 컷에서 0.5 이상이면 끝 키를 고친다
    pinCut: null,
    dragging: false,
    helpersOn: true,
    labelsOn: true, // 작업 207: 장소·소품·빛 이름표(인물 이름표는 늘 보인다)
    focused: false, // 작업 207: 마지막으로 뷰포트를 누른 뒤 아직 포커스가 있다 — 이때만 휠이 확대·축소한다
    undo: [],
    redo: [],
    shadows: new Map(), // cut → 마지막으로 기록한 JSON(되돌리기 기준)
    listeners: { select: [], edit: [], state: [], pose: [] },
    workInit: false,
    workFramed: new WeakSet(), // 작업 209a fix 1: 작업 시점 첫 화면을 이미 맞춘 컷
    workMoved: false, // 작업 209a fix 1: 사용자가 작업 카메라를 움직였다(orbit start · F) — 그 뒤로는 자동으로 옮기지 않는다
    pose: false, // 작업 209a: 포즈 모드(고른 인물 하나에 관절 손잡이)
    joint: null, // 작업 209a: 포즈 모드에서 고른 관절(RIG_JOINTS 하나 또는 "root" = 골반)
  };
  scenesData.forEach((sd) => ((sd && sd.timeline) || []).forEach((cut) => edit.shadows.set(cut, JSON.stringify(cut))));

  const workCamera = new THREE.PerspectiveCamera(50, width / height, 0.05, 400);
  const helpers = new THREE.Group();
  helpers.name = "previz-editor-helpers";
  threeScene.add(helpers);

  const grid = new THREE.GridHelper(30, 60, 0x9aa0a6, 0x4a4f57);
  grid.position.y = 0.003;
  grid.name = "previz-grid";
  helpers.add(grid);

  const camIconMat = new THREE.MeshBasicMaterial({ color: CAM_ICON_COLOR });
  const camIcon = new THREE.Group();
  camIcon.name = "previz-camera-icon";
  const camBody = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.12, 0.22), camIconMat);
  camBody.position.z = 0.13;
  camIcon.add(camBody);
  const camLens = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.1, 12, 1, true), camIconMat);
  camLens.rotation.x = Math.PI / 2;
  camLens.position.z = -0.03;
  camIcon.add(camLens);
  const frustumGeo = new THREE.BufferGeometry();
  frustumGeo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(16 * 3), 3));
  const frustumMat = new THREE.LineBasicMaterial({ color: CAM_ICON_COLOR });
  const frustum = new THREE.LineSegments(frustumGeo, frustumMat);
  frustum.frustumCulled = false;
  camIcon.add(frustum);
  const pickMat = new THREE.MeshBasicMaterial({ visible: false });
  const camPick = new THREE.Mesh(new THREE.SphereGeometry(0.22, 8, 6), pickMat);
  camPick.userData.pick = { type: "camera" };
  camIcon.add(camPick);
  helpers.add(camIcon);

  const crossMat = new THREE.LineBasicMaterial({ color: CAM_ICON_COLOR });
  const crossGeo = new THREE.BufferGeometry();
  crossGeo.setAttribute("position", new THREE.Float32BufferAttribute(
    [-0.12, 0, 0, 0.12, 0, 0, 0, -0.12, 0, 0, 0.12, 0, 0, 0, -0.12, 0, 0, 0.12], 3));
  const cross = new THREE.LineSegments(crossGeo, crossMat);
  cross.name = "previz-target-cross";
  const crossPick = new THREE.Mesh(new THREE.SphereGeometry(0.14, 8, 6), pickMat);
  crossPick.userData.pick = { type: "target" };
  cross.add(crossPick);
  helpers.add(cross);

  const selBox = new THREE.BoxHelper(undefined, SEL_COLOR);
  selBox.visible = false;
  helpers.add(selBox);

  // 기즈모는 대리 물체(proxy)를 움직이고, objectChange 에서 그 값을 컷 데이터로 옮긴다.
  const proxy = new THREE.Object3D();
  proxy.name = "previz-gizmo-proxy";
  helpers.add(proxy);

  const lib = opts.controls || null; // {OrbitControls, TransformControls} — create() 가 vendor addons 를 불러 넣는다
  let orbit = null;
  let gizmo = null;
  if (lib && typeof lib.TransformControls === "function") {
    gizmo = new lib.TransformControls(camera, canvas);
    // 작업 207: 화살표가 작아(약 10px) 잡기 어려웠다 — 1.5배. 기즈모는 원래 depthTest 끔·renderOrder 맨 위라 인물 위에 그려진다.
    if (typeof gizmo.setSize === "function") gizmo.setSize(GIZMO_SIZE);
    else gizmo.size = GIZMO_SIZE;
    const gizmoHelper = typeof gizmo.getHelper === "function" ? gizmo.getHelper() : gizmo;
    helpers.add(gizmoHelper);
    gizmo.addEventListener("dragging-changed", (event) => {
      edit.dragging = !!(event && event.value);
      if (orbit) orbit.enabled = edit.view === "work" && !edit.dragging;
      if (!edit.dragging) {
        const cut = editCut();
        commitEdit(cut);
        renderAt(clockT);
        emit("edit", { cut, kind: "commit" });
      }
    });
    gizmo.addEventListener("objectChange", onGizmoChange);
  }
  if (lib && typeof lib.OrbitControls === "function") {
    orbit = new lib.OrbitControls(workCamera, canvas);
    orbit.enableDamping = false;
    orbit.screenSpacePanning = true;
    orbit.enabled = false;
    orbit.enableZoom = false; // 작업 207: 뷰포트를 누른 뒤에만(setStageFocus)
    orbit.addEventListener("change", () => {
      if (!playing && !recording) renderAt(clockT);
    });
    orbit.addEventListener("start", () => {
      edit.workMoved = true;
    });
  }

  function viewCamera() {
    return edit.view === "work" ? workCamera : camera;
  }

  function emit(kind, payload) {
    edit.listeners[kind].forEach((cb) => {
      try {
        cb(payload);
      } catch (error) {
        console.warn("[previz] 편집기 알림 처리 중 오류:", error);
      }
    });
  }

  function stateInfo() {
    return {
      view: edit.view, tool: edit.tool, snap: edit.snap, aim: edit.aim, selection: edit.sel ? Object.assign({}, edit.sel) : null,
      keyU: edit.keyU, canUndo: edit.undo.length > 0, canRedo: edit.redo.length > 0, pose: edit.pose, joint: edit.joint,
    };
  }

  function emitState() {
    emit("state", stateInfo());
  }

  function unpin() {
    edit.pinCut = null;
    edit.keyU = 0;
  }

  function pinTime(cut) {
    const start = Number(cut.start) || 0;
    const end = Number(cut.end) || start;
    return start + (end - start) * edit.keyU;
  }

  /* 편집 대상 컷: 키 스크러버가 고정한 컷, 아니면 지금 시각의 컷. */
  function editCut() {
    if (edit.pinCut) return edit.pinCut;
    return current ? current.scene._cutAt(clockT) : null;
  }

  function cutActor(cut, id) {
    return ((cut && cut.actors) || []).find((ca) => ca.id === id) || null;
  }

  function actorMeta(id) {
    const entry = current && current.scene.actors[id];
    return (entry && entry.data) || {};
  }

  function room() {
    return (current && current.scene.room) || { width: 6, depth: 6, height: 2.8 };
  }

  function r3(v) {
    return Math.round(Number(v) * 1000) / 1000;
  }

  function snapTo(v, step) {
    return step ? Math.round(v / step) * step : v;
  }

  /* 배열 값을 제자리에서 바꾼다(패널 입력이 같은 배열을 보고 있다). */
  function setVec(obj, key, values) {
    const next = values.map(r3);
    if (Array.isArray(obj[key]) && obj[key].length >= 3) {
      for (let i = 0; i < 3; i++) obj[key][i] = next[i];
    } else {
      obj[key] = next;
    }
  }

  function clampCam(v) {
    const rm = room();
    const hw = rm.width / 2 + CAMERA_MARGIN_M;
    const hd = rm.depth / 2 + CAMERA_MARGIN_M;
    return [Math.max(-hw, Math.min(hw, v.x)), Math.max(0.05, Math.min(rm.height + CAMERA_MARGIN_M, v.y)), Math.max(-hd, Math.min(hd, v.z))];
  }

  function cutCamera(cut) {
    if (!cut.camera || typeof cut.camera !== "object") cut.camera = { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 50 };
    return cut.camera;
  }

  /* 지금 고치는 카메라 키(시작 = cut.camera, 끝 = cut.camera.moveTo). 끝 키가 있고 스크러버가 뒤쪽 절반이면 끝 키. */
  function keyPose(cut) {
    const cam = cutCamera(cut);
    return cam.moveTo && edit.keyU >= 0.5 ? cam.moveTo : cam;
  }

  /* 끝 키가 있는 컷에서 키 사이를 고치면 가까운 키로 스크러버를 붙인다(고친 값이 어느 키인지 분명하게). */
  function settleKey(cut) {
    const cam = cutCamera(cut);
    if (cam.moveTo && edit.keyU > 0 && edit.keyU < 1) {
      edit.keyU = edit.keyU >= 0.5 ? 1 : 0;
      edit.pinCut = cut;
    }
  }

  /* 카메라 키에 위치(pos)·바라보는 곳(target)을 쓴다. 방 + 3m 안으로 자르고 카메라에 manual 표시. */
  function writeCam(cut, pos, target) {
    settleKey(cut);
    const pose = keyPose(cut);
    if (pos) setVec(pose, "pos", clampCam(pos));
    if (target) setVec(pose, "target", clampCam(target));
    cutCamera(cut).manual = true;
  }

  function poseVectors(cut) {
    const pose = keyPose(cut);
    return { pos: vec3(pose.pos, 0, 1.5, 4), target: vec3(pose.target, 0, 1.2, 0) };
  }

  function moveActorTo(cut, id, x, z) {
    const ca = cutActor(cut, id);
    if (!ca) return;
    const rm = room();
    const step = edit.snap ? SNAP_M : 0;
    const hw = rm.width / 2 - ACTOR_WALL_M;
    const hd = rm.depth / 2 - ACTOR_WALL_M;
    const nx = r3(Math.max(-hw, Math.min(hw, snapTo(x, step))));
    const nz = r3(Math.max(-hd, Math.min(hd, snapTo(z, step))));
    const old = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
    const dx = nx - (Number(old[0]) || 0);
    const dz = nz - (Number(old[2]) || 0);
    setVec(ca, "pos", [nx, Number(old[1]) || 0, nz]);
    // 걷는 인물(moveTo)은 끝 자리도 같은 만큼 옮긴다(옛 끝으로 되돌아가지 않게).
    if (Array.isArray(ca.moveTo)) setVec(ca, "moveTo", [(Number(ca.moveTo[0]) || 0) + dx, Number(ca.moveTo[1]) || 0, (Number(ca.moveTo[2]) || 0) + dz]);
    ca.manual = true;
  }

  function setActorFacing(cut, id, deg) {
    const ca = cutActor(cut, id);
    if (!ca) return;
    let d = snapTo(deg, edit.snap ? SNAP_DEG : 0);
    d = Math.atan2(Math.sin(d * Math.PI / 180), Math.cos(d * Math.PI / 180)) * 180 / Math.PI;
    ca.facingDeg = Math.round(d * 10) / 10;
    // 앉은 인물은 다리 방향도 같이(패널과 같다 — 저장하면 서버가 앉은 모양을 다시 잡는다).
    if (ca.sit && typeof ca.sit === "object") ca.sit.legYawDeg = ca.facingDeg;
    ca.manual = true;
  }

  function moveCameraTo(cut, p) {
    const step = edit.snap ? SNAP_M : 0;
    writeCam(cut, new THREE.Vector3(snapTo(p.x, step), snapTo(p.y, step), snapTo(p.z, step)), null);
  }

  function moveTargetTo(cut, p) {
    const step = edit.snap ? SNAP_M : 0;
    writeCam(cut, null, new THREE.Vector3(snapTo(p.x, step), snapTo(p.y, step), snapTo(p.z, step)));
  }

  /* 카메라 방향(dir, 단위 벡터)을 바꾼다 — 바라보는 곳을 같은 거리에서 그 방향으로(팬·틸트). 눈금 맞춤이면 15° 단위. */
  function aimCamera(cut, dir) {
    const v = poseVectors(cut);
    const dist = Math.max(0.3, v.target.distanceTo(v.pos));
    let yaw = Math.atan2(dir.x, dir.z);
    let pitch = Math.asin(Math.max(-1, Math.min(1, dir.y)));
    if (edit.snap) {
      const s = SNAP_DEG * Math.PI / 180;
      yaw = snapTo(yaw, s);
      pitch = snapTo(pitch, s);
    }
    pitch = Math.max(-1.55, Math.min(1.55, pitch));
    const d = new THREE.Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
    writeCam(cut, null, v.pos.clone().addScaledVector(d, dist));
  }

  function onGizmoChange() {
    const cut = editCut();
    const sel = edit.sel;
    if (!cut || !sel) return;
    // 작업 209a: 포즈 모드에서 고른 관절을 회전(E) 기즈모로 돌린다 — 부모 기준 로컬 회전을 rig 에 쓴다(한계 안으로).
    const joint = poseGizmoJoint();
    if (joint && sel.type === "actor") {
      const obj = poseEntry().mannequin.rigObject(joint);
      const parentQ = obj.parent.getWorldQuaternion(new THREE.Quaternion());
      const e = new THREE.Euler().setFromQuaternion(parentQ.invert().multiply(proxy.quaternion), "XYZ");
      writeJoint(cut, sel.id, joint, [e.x, e.y, e.z], jointNow(cut, sel.id, joint));
      renderAt(clockT);
      emit("edit", { cut, kind: "live" });
      return;
    }
    if (sel.type === "actor") {
      if (edit.tool === "rotate") {
        const e = new THREE.Euler().setFromQuaternion(proxy.quaternion, "YXZ");
        setActorFacing(cut, sel.id, e.y * 180 / Math.PI);
      } else {
        moveActorTo(cut, sel.id, proxy.position.x, proxy.position.z);
      }
    } else if (sel.type === "camera") {
      if (edit.tool === "rotate") aimCamera(cut, new THREE.Vector3(0, 0, -1).applyQuaternion(proxy.quaternion).normalize());
      else moveCameraTo(cut, proxy.position);
    } else if (sel.type === "target") {
      moveTargetTo(cut, proxy.position);
    }
    renderAt(clockT);
    emit("edit", { cut, kind: "live" });
  }

  /* 기즈모를 선택한 것에 붙인다. 카메라 시점에서 촬영 카메라 자신은 기즈모 대신 드래그(돌기·이동·휠)로 움직인다. */
  function syncProxy() {
    if (!gizmo || edit.dragging) return;
    const cut = editCut();
    const sel = edit.sel;
    let attach = false;
    if (cut && sel && edit.tool !== "select" && !recording) {
      proxy.rotation.set(0, 0, 0);
      const joint = poseGizmoJoint();
      if (joint) {
        // 작업 209a: 고른 관절 자리·방향에 회전 기즈모(세 축).
        const obj = poseEntry().mannequin.rigObject(joint);
        obj.getWorldPosition(proxy.position);
        obj.getWorldQuaternion(proxy.quaternion);
        gizmo.showX = true;
        gizmo.showY = true;
        gizmo.showZ = true;
        attach = true;
      } else if (edit.pose && edit.joint) {
        // 포즈 모드에서 관절·골반을 골랐으면 인물 이동 기즈모는 띄우지 않는다(손잡이를 직접 끈다).
      } else if (sel.type === "actor") {
        const ca = cutActor(cut, sel.id);
        const entry = current && current.scene.actors[sel.id];
        if (ca && entry && entry.mannequin.group.visible) {
          const p = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
          proxy.position.set(Number(p[0]) || 0, 0, Number(p[2]) || 0);
          proxy.rotation.set(0, (Number(ca.facingDeg) || 0) * Math.PI / 180, 0);
          gizmo.showX = edit.tool !== "rotate";
          gizmo.showY = edit.tool === "rotate";
          gizmo.showZ = edit.tool !== "rotate";
          attach = true;
        }
      } else if (sel.type === "camera" && edit.view === "work") {
        const v = poseVectors(cut);
        proxy.position.copy(v.pos);
        proxy.quaternion.setFromRotationMatrix(new THREE.Matrix4().lookAt(v.pos, v.target, new THREE.Vector3(0, 1, 0)));
        gizmo.showX = true;
        gizmo.showY = true;
        gizmo.showZ = edit.tool !== "rotate";
        attach = true;
      } else if (sel.type === "target" && edit.tool === "translate") {
        proxy.position.copy(poseVectors(cut).target);
        gizmo.showX = true;
        gizmo.showY = true;
        gizmo.showZ = true;
        attach = true;
      }
    }
    proxy.updateMatrixWorld(true);
    gizmo.camera = viewCamera();
    if (typeof gizmo.setMode === "function") gizmo.setMode(edit.tool === "rotate" ? "rotate" : "translate");
    if (typeof gizmo.setSpace === "function") gizmo.setSpace(edit.tool === "rotate" ? "local" : "world");
    if (typeof gizmo.setTranslationSnap === "function") gizmo.setTranslationSnap(edit.snap ? SNAP_M : null);
    if (typeof gizmo.setRotationSnap === "function") gizmo.setRotationSnap(edit.snap ? SNAP_DEG * Math.PI / 180 : null);
    if (attach) {
      if (gizmo.object !== proxy) gizmo.attach(proxy);
    } else if (gizmo.object) {
      gizmo.detach();
    }
  }

  /* 도우미 위치: 카메라 아이콘·시야선(작업 시점만), 바라보는 곳 십자, 격자(작업 시점만), 선택 표시. */
  function syncHelpers() {
    const work = edit.view === "work";
    grid.visible = work;
    camIcon.visible = work;
    camIcon.position.copy(camera.position);
    camIcon.quaternion.copy(camera.quaternion);
    const look = current && current.scene.cameraLook ? current.scene.cameraLook : new THREE.Vector3(0, 1.2, 0);
    const d = Math.max(0.4, Math.min(6, camera.position.distanceTo(look)));
    const h = Math.tan((camera.fov * Math.PI / 180) / 2) * d;
    const w = h * camera.aspect;
    const c = [[-w, -h], [w, -h], [w, h], [-w, h]];
    const pts = [];
    c.forEach((p) => pts.push(0, 0, 0, p[0], p[1], -d));
    for (let i = 0; i < 4; i++) {
      const a = c[i];
      const b = c[(i + 1) % 4];
      pts.push(a[0], a[1], -d, b[0], b[1], -d);
    }
    const attr = frustumGeo.getAttribute("position");
    attr.array.set(pts);
    attr.needsUpdate = true;
    cross.position.copy(look);
    const sel = edit.sel;
    camIconMat.color.setHex(sel && sel.type === "camera" ? SEL_COLOR : CAM_ICON_COLOR);
    frustumMat.color.setHex(sel && sel.type === "camera" ? SEL_COLOR : CAM_ICON_COLOR);
    crossMat.color.setHex(sel && sel.type === "target" ? SEL_COLOR : CAM_ICON_COLOR);
    const entry = sel && sel.type === "actor" && current ? current.scene.actors[sel.id] : null;
    if (entry && entry.mannequin.group.visible) {
      selBox.setFromObject(entry.mannequin.group);
      selBox.visible = true;
    } else {
      selBox.visible = false;
    }
    syncPose();
    syncProxy();
  }

  /* ================================================================ 작업 209a: 포즈 모드(Maya 식 관절 잡기)

     인물 하나를 고른 뒤 [포즈](P): 그 인물에만 손잡이를 보인다 — FK 고리(머리·목·가슴·어깨·팔꿈치·엉덩이·무릎),
     IK 네모(두 손·두 발), 골반 네모. 고리를 누르면 그 관절을 고르고(패널 채널 박스), 고리를 끌면 가로 = 돌리기(y),
     세로 = 숙이기(x), Shift+가로 = 기울이기(z). 회전(E) 기즈모로도 돌린다(자식 관절이 따라 돈다).
     손·발 네모를 끌면 보는 카메라를 마주 보는 면에서 움직이고(Alt+세로 = 앞뒤), 2관절 IK(solveTwoBoneIK)를 풀어
     어깨·팔꿈치(엉덩이·무릎) FK 값으로 rig 에 쓴다 — 저장되는 것은 FK 값뿐. 골반 네모를 세로로 끌면 rootY(바닥에 닿은 발은
     그 자리에 둔 채 무릎을 굽힌다). 모든 값은 RIG_LIMITS 안으로. 끈 것 한 번 = 되돌리기 한 단계(commitEdit), 그 인물은 manual.
     재생·녹화 중엔 꺼진다(손잡이는 helpers 묶음 안 — 녹화에 나오지 않는다). */
  const POSE_FK_COLOR = 0x64b5f6;
  const POSE_IK_COLOR = 0xff8a65;
  const POSE_ROOT_COLOR = 0xce93d8;
  const POSE_ROT_RAD_PER_PX = 0.01; // 고리 끌기 1px = 0.57°
  const POSE_PLANT_M = 0.15; // 골반을 끌 때 이 높이 아래의 발은 바닥에 붙여 둔다
  // [관절, 고리 반지름 m(키 1.7 기준), 피벗 로컬 자리]. 고리는 뼈(로컬 y)를 감싼다.
  const POSE_RINGS = [
    ["head", 0.18, [0, RIG_DIMS.headCenterY, 0]],
    ["neck", 0.09, [0, 0, 0]],
    ["spine", 0.26, [0, 0.32, 0]],
    ["shoulderL", 0.08, [0, 0, 0]],
    ["shoulderR", 0.08, [0, 0, 0]],
    ["elbowL", 0.065, [0, 0, 0]],
    ["elbowR", 0.065, [0, 0, 0]],
    ["hipL", 0.095, [0, -0.1, 0]],
    ["hipR", 0.095, [0, -0.1, 0]],
    ["kneeL", 0.085, [0, 0, 0]],
    ["kneeR", 0.085, [0, 0, 0]],
  ];
  // IK 손잡이 → [팔/다리, 쪽]
  const POSE_IK = { handL: ["arm", "L"], handR: ["arm", "R"], footL: ["leg", "L"], footR: ["leg", "R"] };
  const RING_TILT = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
  const poseGroup = new THREE.Group();
  poseGroup.name = "previz-pose-controls";
  poseGroup.visible = false;
  helpers.add(poseGroup);
  let poseCtl = null; // {rings, handles, root, picks} — 처음 켤 때 만든다

  function poseMaterial(color) {
    return new THREE.MeshBasicMaterial({ color, depthTest: false, depthWrite: false, transparent: true, opacity: 0.95 });
  }

  function ensurePoseControls() {
    if (poseCtl) return poseCtl;
    const ctl = { rings: [], handles: [], root: null, picks: [] };
    const addPick = (mesh, geo, info) => {
      const pick = new THREE.Mesh(geo, pickMat);
      pick.userData.pose = info;
      mesh.add(pick);
      ctl.picks.push(pick);
    };
    POSE_RINGS.forEach(([joint, radius, offset]) => {
      const mat = poseMaterial(POSE_FK_COLOR);
      const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.008, 6, 40), mat);
      mesh.renderOrder = 990;
      addPick(mesh, new THREE.TorusGeometry(radius, 0.035, 6, 24), { kind: "fk", name: joint });
      poseGroup.add(mesh);
      ctl.rings.push({ joint, mesh, mat, offset: new THREE.Vector3(offset[0], offset[1], offset[2]) });
    });
    Object.keys(POSE_IK).forEach((name) => {
      const mat = poseMaterial(POSE_IK_COLOR);
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.07, 0.07), mat);
      mesh.renderOrder = 991;
      addPick(mesh, new THREE.BoxGeometry(0.11, 0.11, 0.11), { kind: "ik", name });
      poseGroup.add(mesh);
      ctl.handles.push({ name, mesh, mat });
    });
    // 골반: 엉덩이 둘레 네모(얇은 막대 4개).
    const rootMat = poseMaterial(POSE_ROOT_COLOR);
    const root = new THREE.Group();
    const hx = 0.24;
    const hz = 0.17;
    [[0, hz, 2 * hx, true], [0, -hz, 2 * hx, true], [hx, 0, 2 * hz, false], [-hx, 0, 2 * hz, false]].forEach(([x, z, len, alongX]) => {
      const size = (t) => (alongX ? [len, t, t] : [t, t, len]);
      const bar = new THREE.Mesh(new THREE.BoxGeometry(...size(0.012)), rootMat);
      bar.position.set(x, 0, z);
      bar.renderOrder = 990;
      addPick(bar, new THREE.BoxGeometry(...size(0.05)), { kind: "root", name: "root" });
      root.add(bar);
    });
    poseGroup.add(root);
    ctl.root = { mesh: root, mat: rootMat };
    poseCtl = ctl;
    return ctl;
  }

  /* 포즈 모드의 인물(이 컷에 보이는 고른 인물). 아니면 null. */
  function poseEntry() {
    const sel = edit.sel;
    if (!edit.pose || !sel || sel.type !== "actor" || !current) return null;
    const entry = current.scene.actors[sel.id];
    return entry && entry.mannequin.group.visible ? entry : null;
  }

  function poseGizmoJoint() {
    return edit.pose && edit.tool === "rotate" && RIG_JOINTS.indexOf(edit.joint) >= 0 && poseEntry() ? edit.joint : null;
  }

  function limbOf(m, kind, side) {
    if (kind === "arm") return side === "L" ? m.shoulderL : m.shoulderR;
    return side === "L" ? m.hipL : m.hipR;
  }

  function handleObject(m, name) {
    const limb = limbOf(m, POSE_IK[name][0], POSE_IK[name][1]);
    return POSE_IK[name][0] === "arm" ? limb.hand : limb.ankle;
  }

  /* 손잡이를 지금 그린 마네킹 관절 자리로 옮긴다(renderAt → syncHelpers). */
  function syncPose() {
    const entry = poseEntry();
    const show = !!entry && edit.helpersOn && !playing && !recording;
    poseGroup.visible = show;
    if (!show) return;
    const ctl = ensurePoseControls();
    const m = entry.mannequin;
    const s = m.group.scale.y || 1;
    const q = new THREE.Quaternion();
    m.group.updateMatrixWorld(true);
    ctl.rings.forEach((ring) => {
      const obj = m.rigObject(ring.joint);
      ring.mesh.position.copy(obj.localToWorld(ring.offset.clone()));
      obj.getWorldQuaternion(q);
      ring.mesh.quaternion.copy(q).multiply(RING_TILT);
      ring.mesh.scale.setScalar(s);
      ring.mat.color.setHex(edit.joint === ring.joint ? SEL_COLOR : POSE_FK_COLOR);
    });
    m.group.getWorldQuaternion(q);
    ctl.handles.forEach((h) => {
      handleObject(m, h.name).getWorldPosition(h.mesh.position);
      h.mesh.quaternion.copy(q);
      h.mesh.scale.setScalar(s);
      h.mat.color.setHex(drag.pose && drag.pose.name === h.name ? SEL_COLOR : POSE_IK_COLOR);
    });
    m.hips.getWorldPosition(ctl.root.mesh.position);
    ctl.root.mesh.quaternion.copy(q);
    ctl.root.mesh.scale.setScalar(s);
    ctl.root.mat.color.setHex(edit.joint === "root" ? SEL_COLOR : POSE_ROOT_COLOR);
    poseGroup.updateMatrixWorld(true);
  }

  /* 화면 좌표 아래 포즈 손잡이 {kind: "fk"|"ik"|"root", name}. 없으면 null. */
  function pickPose(clientX, clientY) {
    if (!poseCtl || !poseGroup.visible || !poseEntry() || playing || recording) return null;
    rayFrom(clientX, clientY);
    const hits = raycaster.intersectObjects(poseCtl.picks, false);
    return hits.length ? Object.assign({}, hits[0].object.userData.pose) : null;
  }

  function poseInfo() {
    return { on: edit.pose, joint: edit.joint, id: edit.pose && edit.sel ? edit.sel.id : null };
  }

  function emitPose() {
    emit("pose", poseInfo());
    emitState();
  }

  /* [포즈](P): 인물 하나를 골랐을 때만 켠다. 재생·녹화 중엔 켜지 않는다. */
  function setPoseMode(on) {
    const want = !!on && !playing && !recording && !!(edit.sel && edit.sel.type === "actor");
    if (want === edit.pose && (want || edit.joint === null)) return edit.pose;
    edit.pose = want;
    edit.joint = null;
    if (!playing) renderAt(clockT);
    emitPose();
    return edit.pose;
  }

  /* 재생·녹화가 시작되면 포즈 모드를 끈다(그리지 않는다 — 부른 쪽이 그린다). */
  function leavePose() {
    if (!edit.pose && edit.joint === null) return;
    edit.pose = false;
    edit.joint = null;
    emitPose();
  }

  function selectJoint(name) {
    if (!edit.pose) return false;
    const next = name === "root" || RIG_JOINTS.indexOf(name) >= 0 ? name : null;
    if (next !== edit.joint) {
      edit.joint = next;
      if (!playing) renderAt(clockT);
      emitPose();
    }
    return true;
  }

  function r4(v) {
    return Math.round(Number(v) * 10000) / 10000 || 0;
  }

  function actorRig(ca) {
    if (!ca.rig || typeof ca.rig !== "object" || Array.isArray(ca.rig)) ca.rig = {};
    return ca.rig;
  }

  /* 관절 하나(라디안 3개)를 한계 안으로 잘라 cut actor 의 rig 에 쓴다. 그 인물은 직접 고침(manual).
     from = 편집을 시작한 값(끌기 시작·채널 박스의 지금 값). 한계 밖이던 축은 움직인 쪽으로만 자른다(작업 209a fix 1). */
  function writeJoint(cut, id, joint, values, from) {
    const ca = cutActor(cut, id);
    if (!ca || RIG_JOINTS.indexOf(joint) < 0) return null;
    const v = clampRigJoint(joint, values, from).map(r4);
    actorRig(ca)[joint] = v;
    ca.manual = true;
    return v;
  }

  /* 지금 이 관절 값(라디안): rig 에 있으면 그 값, 없으면 보이는 값. 모르면 null. */
  function jointNow(cut, id, joint) {
    const ca = cutActor(cut, id);
    if (ca && ca.rig && Array.isArray(ca.rig[joint])) return ca.rig[joint].map((x) => Number(x) || 0);
    const entry = current && current.scene.actors[id];
    return entry && current.scene.drawnCut === cut ? entry.mannequin.readJoint(joint) : null;
  }

  function writeRootY(cut, id, value) {
    const ca = cutActor(cut, id);
    if (!ca) return null;
    const v = r4(Math.max(RIG_ROOT_Y_RANGE[0], Math.min(RIG_ROOT_Y_RANGE[1], Number(value) || 0)));
    actorRig(ca).rootY = v;
    ca.manual = true;
    return v;
  }

  /* 손·발을 세계 점 targetWorld 로: 2관절 IK 를 풀어 뿌리·가운데 관절 FK 값을 rig 에 쓴다.
     팔꿈치는 뒤·아래(·바깥)로, 무릎은 앞으로 굽는다(pole 은 부모 = 가슴·엉덩이 기준). */
  function solveLimb(cut, id, m, kind, side, targetWorld) {
    const limb = limbOf(m, kind, side);
    const parent = limb.pivot.parent;
    parent.updateMatrixWorld(true);
    const local = parent.worldToLocal(targetWorld.clone()).sub(limb.pivot.position);
    const out = side === "L" ? 1 : -1;
    const arm = kind === "arm";
    const rootJoint = (arm ? "shoulder" : "hip") + side;
    const midJoint = (arm ? "elbow" : "knee") + side;
    const bendLim = RIG_LIMITS[midJoint][0];
    const pl = RIG_IK_POLES[arm ? "arm" : "leg"];
    const pole = new THREE.Vector3(out * pl[0], pl[1], pl[2]);
    const res = arm
      ? solveTwoBoneIK(local, RIG_DIMS.upperArm, RIG_DIMS.foreArm, pole, -1, -bendLim[0] * DEG)
      : solveTwoBoneIK(local, RIG_DIMS.thigh, RIG_DIMS.shin, pole, 1, bendLim[1] * DEG);
    writeJoint(cut, id, rootJoint, res.root);
    writeJoint(cut, id, midJoint, res.mid);
    return res;
  }

  function metersPerPixel(point) {
    const cam = viewCamera();
    const rectH = canvas.clientHeight || height;
    const dist = Math.max(0.1, cam.position.distanceTo(point));
    return (2 * dist * Math.tan((cam.fov * Math.PI / 180) / 2)) / Math.max(1, rectH);
  }

  function rayPlane(clientX, clientY, plane) {
    const p = new THREE.Vector3();
    return rayFrom(clientX, clientY).ray.intersectPlane(plane, p) ? p : null;
  }

  /* 포즈 손잡이를 눌렀다: 고리 = 그 관절 고르기 + 돌리기 시작, 손·발 = IK 끌기, 골반 = rootY 끌기. */
  function beginPoseDrag(event) {
    if (edit.aim) return false;
    const hit = pickPose(event.clientX, event.clientY);
    if (!hit) return false;
    const entry = poseEntry();
    const cut = editCut();
    const id = edit.sel.id;
    if (!entry || !cut || !cutActor(cut, id)) return false;
    const m = entry.mannequin;
    const cam = viewCamera();
    cam.updateMatrixWorld(true);
    m.group.updateMatrixWorld(true);
    const p = { kind: hit.kind, name: hit.name, cut, id, m, x0: event.clientX, y0: event.clientY, lx: event.clientX, ly: event.clientY };
    if (hit.kind === "fk") {
      selectJoint(hit.name);
      p.start = m.readJoint(hit.name);
      // 인물이 카메라를 등지면 가로 끌기 방향을 뒤집는다(화면에서 끈 쪽으로 돈다).
      const fwd = new THREE.Vector3(0, 0, 1).applyQuaternion(m.group.getWorldQuaternion(new THREE.Quaternion()));
      p.face = fwd.dot(cam.position.clone().sub(m.group.getWorldPosition(new THREE.Vector3()))) >= 0 ? 1 : -1;
    } else if (hit.kind === "ik") {
      p.point = handleObject(m, hit.name).getWorldPosition(new THREE.Vector3());
      p.normal = cam.getWorldDirection(new THREE.Vector3());
      p.mpp = metersPerPixel(p.point);
    } else {
      selectJoint("root");
      p.rootY0 = m.pelvis.position.y;
      p.scale = m.group.scale.y || 1;
      p.mpp = metersPerPixel(m.hips.getWorldPosition(new THREE.Vector3()));
      const ca = cutActor(cut, id);
      p.planted = [];
      ["L", "R"].forEach((side) => {
        const at = limbOf(m, "leg", side).ankle.getWorldPosition(new THREE.Vector3());
        const posed = ca.rig && Array.isArray(ca.rig["hip" + side]);
        if (at.y < POSE_PLANT_M * p.scale || posed) p.planted.push({ side, at });
      });
    }
    drag.pose = p;
    if (orbit) orbit.enabled = false;
    return true;
  }

  function movePoseDrag(event) {
    const p = drag.pose;
    if (!p || !cutActor(p.cut, p.id)) return;
    pause();
    if (p.kind === "fk") {
      const dx = event.clientX - p.x0;
      const dy = event.clientY - p.y0;
      let v = p.start.slice();
      if (event.shiftKey) v[2] = p.start[2] - dx * POSE_ROT_RAD_PER_PX * p.face;
      else {
        v[0] = p.start[0] + dy * POSE_ROT_RAD_PER_PX;
        v[1] = p.start[1] + dx * POSE_ROT_RAD_PER_PX * p.face;
      }
      if (edit.snap) v = v.map((x, i) => (x === p.start[i] ? x : snapTo(x, SNAP_DEG * DEG)));
      writeJoint(p.cut, p.id, p.name, v, p.start);
    } else if (p.kind === "ik") {
      if (event.altKey) {
        // Alt+세로 끌기 = 앞뒤(위로 끌면 카메라에서 멀어진다).
        p.point.addScaledVector(p.normal, -(event.clientY - p.ly) * p.mpp);
      } else {
        const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(p.normal, p.point);
        const from = rayPlane(p.lx, p.ly, plane);
        const to = rayPlane(event.clientX, event.clientY, plane);
        if (from && to) p.point.add(to.sub(from));
      }
      solveLimb(p.cut, p.id, p.m, POSE_IK[p.name][0], POSE_IK[p.name][1], p.point);
    } else {
      const rootY = writeRootY(p.cut, p.id, p.rootY0 - (event.clientY - p.y0) * p.mpp / p.scale);
      p.m.pelvis.position.y = rootY;
      p.m.group.updateMatrixWorld(true);
      p.planted.forEach((f) => solveLimb(p.cut, p.id, p.m, "leg", f.side, f.at));
    }
    p.lx = event.clientX;
    p.ly = event.clientY;
    setCursor("grabbing");
    renderAt(clockT);
    emit("edit", { cut: p.cut, kind: "live" });
  }

  /* 놓았다: 움직였으면 한 단계(commit). 손·발 네모를 누르기만 했으면 팔꿈치·무릎을 고른다(채널 박스). */
  function endPoseDrag(event, click) {
    const p = drag.pose;
    const moved = drag.moved;
    drag.pose = null;
    if (orbit) orbit.enabled = edit.view === "work" && !edit.dragging;
    setCursor("");
    if (!p) return;
    if (moved) {
      commitEdit(p.cut);
      renderAt(clockT);
      emit("edit", { cut: p.cut, kind: "commit" });
    } else if (click && p.kind === "ik") {
      selectJoint((POSE_IK[p.name][0] === "arm" ? "elbow" : "knee") + POSE_IK[p.name][1]);
    }
  }

  /* 패널 편집(채널 박스·버튼). commit 이면 바로 한 단계, 아니면 다시 그리기만(패널이 잠시 뒤 commitEdit). */
  function poseEdit(cutArg, fn, commit) {
    const cut = cutArg || editCut();
    if (!cut || recording) return false;
    fn(cut);
    if (commit) {
      commitEdit(cut);
      renderAt(clockT);
      emit("edit", { cut, kind: "commit" });
    } else if (!playing) {
      renderAt(clockT);
    }
    return true;
  }

  /* 채널 박스: 관절 값(도, [숙이기 x, 돌리기 y, 기울이기 z]). rig 에 있으면 그 값, 없으면 지금 보이는 값. */
  function jointDeg(id, joint, cutArg) {
    if (RIG_JOINTS.indexOf(joint) < 0) return null;
    const ca = cutActor(cutArg || editCut(), id);
    const entry = current && current.scene.actors[id];
    let v = ca && ca.rig && Array.isArray(ca.rig[joint]) ? ca.rig[joint] : null;
    if (!v && entry) v = entry.mannequin.readJoint(joint);
    return v ? v.map((x) => Math.round((Number(x) || 0) / DEG * 10) / 10) : null;
  }

  function setJointDeg(id, joint, deg, commit, cutArg) {
    return poseEdit(cutArg, (cut) => writeJoint(cut, id, joint, [0, 1, 2].map((i) => (Number(deg && deg[i]) || 0) * DEG),
      jointNow(cut, id, joint)), commit);
  }

  function rootYOf(id, cutArg) {
    const ca = cutActor(cutArg || editCut(), id);
    return ca && ca.rig && typeof ca.rig.rootY === "number" ? ca.rig.rootY : 0;
  }

  function setRootY(id, meters, commit, cutArg) {
    return poseEdit(cutArg, (cut) => writeRootY(cut, id, meters), commit);
  }

  function dropRigIfEmpty(ca) {
    if (ca.rig && typeof ca.rig === "object" && !Object.keys(ca.rig).length) delete ca.rig;
  }

  /* [이 관절 초기화]: rig 에서 그 관절을 뺀다(목록 자세 값으로 돌아간다). "root" 면 골반 높이. */
  function resetJoint(id, joint, cutArg) {
    return poseEdit(cutArg, (cut) => {
      const ca = cutActor(cut, id);
      if (!ca || !ca.rig || typeof ca.rig !== "object") return;
      delete ca.rig[joint === "root" ? "rootY" : joint];
      dropRigIfEmpty(ca);
      ca.manual = true;
    }, true);
  }

  /* [포즈 초기화]: rig 를 지운다(목록에서 고른 자세 그대로). */
  function resetPose(id, cutArg) {
    return poseEdit(cutArg, (cut) => {
      const ca = cutActor(cut, id);
      if (!ca || !ca.rig) return;
      delete ca.rig;
      ca.manual = true;
    }, true);
  }

  /* [좌우 반전]: 좌우 관절을 바꾸고 y·z 부호를 뒤집는다(mirrorRig). rig 에 없는 좌우 관절도 지금 보이는 값이 다르면
     먼저 굳혀(목록 동작의 한쪽 팔도) 반대편으로 옮긴다. */
  function mirrorPose(id, cutArg) {
    return poseEdit(cutArg, (cut) => {
      const ca = cutActor(cut, id);
      if (!ca) return;
      const base = Object.assign({}, ca.rig && typeof ca.rig === "object" ? ca.rig : {});
      const entry = current && current.scene.actors[id];
      if (entry && current.scene.drawnCut === cut) {
        RIG_JOINTS.filter((j) => /L$/.test(j)).forEach((left) => {
          const right = left.slice(0, -1) + "R";
          const l = Array.isArray(base[left]) ? base[left] : entry.mannequin.readJoint(left);
          const r = Array.isArray(base[right]) ? base[right] : entry.mannequin.readJoint(right);
          const asym = Math.abs(l[0] - r[0]) + Math.abs(l[1] + r[1]) + Math.abs(l[2] + r[2]) > 0.01;
          if (asym || Array.isArray(base[left]) || Array.isArray(base[right])) {
            base[left] = l;
            base[right] = r;
          }
        });
      }
      const next = mirrorRig(base);
      Object.keys(next).forEach((k) => {
        if (k !== "rootY") next[k] = clampRigJoint(k, next[k]).map(r4);
      });
      ca.rig = next;
      dropRigIfEmpty(ca);
      ca.manual = true;
    }, true);
  }

  /* ---- 되돌리기: 컷 JSON 스냅숏. commitEdit 이 마지막 기록과 다르면 한 단계를 쌓는다. ---- */
  function commitEdit(cut) {
    const c = cut || editCut();
    if (!c) return false;
    const now = JSON.stringify(c);
    const before = edit.shadows.has(c) ? edit.shadows.get(c) : now;
    edit.shadows.set(c, now);
    if (now === before) return false;
    edit.undo.push({ cut: c, before, after: now });
    if (edit.undo.length > UNDO_LIMIT) edit.undo.shift();
    edit.redo.length = 0;
    emitState();
    return true;
  }

  function restoreCut(cut, json) {
    Object.keys(cut).forEach((k) => {
      delete cut[k];
    });
    Object.assign(cut, JSON.parse(json));
    edit.shadows.set(cut, json);
  }

  function stepHistory(from, to, key) {
    commitEdit(editCut());
    const step = from.pop();
    if (!step) return false;
    restoreCut(step.cut, step[key]);
    to.push(step);
    renderAt(clockT);
    emit("edit", { cut: step.cut, kind: key === "before" ? "undo" : "redo" });
    emitState();
    return true;
  }

  function undo() {
    return stepHistory(edit.undo, edit.redo, "before");
  }

  function redo() {
    return stepHistory(edit.redo, edit.undo, "after");
  }

  /* 한 번에 끝나는 편집(버튼·키): 바꾸고 → 한 단계 기록 → 다시 그림 → 알림. */
  function applyOnce(fn) {
    const cut = editCut();
    if (!cut || recording) return false;
    fn(cut);
    commitEdit(cut);
    renderAt(clockT);
    emit("edit", { cut, kind: "commit" });
    return true;
  }

  function select(sel) {
    let next = null;
    if (sel && sel.type === "actor" && sel.id) next = { type: "actor", id: String(sel.id) };
    else if (sel && (sel.type === "camera" || sel.type === "target")) next = { type: sel.type };
    const prevId = edit.sel && edit.sel.type === "actor" ? edit.sel.id : null;
    edit.sel = next;
    // 작업 209a: 인물이 아닌 것을 고르면 포즈 모드를 끄고, 다른 인물로 바꾸면 고른 관절만 뗀다.
    let poseChanged = false;
    if (edit.pose && (!next || next.type !== "actor")) {
      edit.pose = false;
      edit.joint = null;
      poseChanged = true;
    } else if (edit.pose && next.id !== prevId && edit.joint !== null) {
      edit.joint = null;
      poseChanged = true;
    }
    if (!playing) renderAt(clockT);
    emit("select", next ? Object.assign({}, next) : null);
    if (poseChanged) emit("pose", poseInfo());
    emitState();
  }

  function setTool(tool) {
    edit.tool = ["select", "translate", "rotate"].indexOf(tool) >= 0 ? tool : "select";
    if (!playing) renderAt(clockT);
    emitState();
  }

  function setSnap(on) {
    edit.snap = !!on;
    if (!playing) renderAt(clockT);
    emitState();
  }

  function setAim(on) {
    edit.aim = !!on;
    emitState();
  }

  function initWorkCamera() {
    const look = current && current.scene.cameraLook ? current.scene.cameraLook.clone() : new THREE.Vector3(0, 1.2, 0);
    const back = camera.position.clone().sub(look);
    if (back.lengthSq() < 1e-6) back.set(0, 0, 1);
    back.normalize().multiplyScalar(camera.position.distanceTo(look) + 3);
    workCamera.position.copy(look).add(back).add(new THREE.Vector3(0, 1.5, 0));
    workCamera.lookAt(look);
    if (orbit) {
      orbit.target.copy(look);
      orbit.update();
    }
    edit.workInit = true;
  }

  /* 작업 209a fix 1: 작업 시점 첫 화면 — 그 컷의 인물(발 → 키)과 촬영 카메라·바라보는 곳을 모두 담는다(F 처럼 가운데를 보고
     물러난다). 방향은 촬영 카메라 쪽에서 40° 위 — 카메라가 인물을 보는 쪽이라 건물 뒤에서 시작하지 않는다. */
  function frameWorkView(cut) {
    const box = new THREE.Box3();
    ((cut && cut.actors) || []).forEach((ca) => {
      const p = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
      const x = Number(p[0]) || 0;
      const z = Number(p[2]) || 0;
      const h = Number(actorMeta(ca.id).heightM) || 1.7;
      box.expandByPoint(new THREE.Vector3(x - 0.3, 0, z - 0.3));
      box.expandByPoint(new THREE.Vector3(x + 0.3, h, z + 0.3));
    });
    const v = poseVectors(cut);
    box.expandByPoint(v.pos);
    box.expandByPoint(v.target);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const halfFov = Math.min(workCamera.fov, workCamera.fov * Math.min(1, workCamera.aspect || 1)) * DEG / 2;
    const dist = Math.max(2.5, (sphere.radius * 1.1) / Math.sin(halfFov));
    const dir = v.pos.clone().sub(v.target);
    dir.y = 0;
    if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
    dir.normalize().multiplyScalar(Math.cos(WORK_FRAME_PITCH_RAD)).setY(Math.sin(WORK_FRAME_PITCH_RAD));
    workCamera.position.copy(sphere.center).addScaledVector(dir, dist);
    workCamera.lookAt(sphere.center);
    if (orbit) {
      orbit.target.copy(sphere.center);
      orbit.update();
    }
    edit.workInit = true;
    edit.workFramed.add(cut);
  }

  function setViewMode(mode) {
    edit.view = mode === "work" ? "work" : "shot";
    if (edit.view === "work") {
      // 처음 보는 컷이면 그 컷을 담는다. 사용자가 작업 카메라를 한 번 움직였으면(orbit start · F) 그 자리 그대로.
      const cut = editCut();
      if (cut && !edit.workMoved && !edit.workFramed.has(cut)) frameWorkView(cut);
      else if (!edit.workInit) initWorkCamera();
    }
    if (orbit) orbit.enabled = edit.view === "work" && !edit.dragging;
    if (!playing) renderAt(clockT);
    emitState();
  }

  /* F: 선택한 것(없으면 방 전체)에 맞춰 작업 카메라를 옮긴다. 카메라 시점이면 작업 시점으로 바꾼 뒤. */
  function frameSelection() {
    if (edit.view !== "work") setViewMode("work");
    edit.workMoved = true;
    const sel = edit.sel;
    const cut = editCut();
    let center = new THREE.Vector3(0, 1, 0);
    let dist = Math.max(room().width, room().depth) * 1.1;
    if (sel && sel.type === "actor" && current && current.scene.actors[sel.id]) {
      new THREE.Box3().setFromObject(current.scene.actors[sel.id].mannequin.group).getCenter(center);
      dist = 3;
    } else if (sel && cut && (sel.type === "camera" || sel.type === "target")) {
      const v = poseVectors(cut);
      center = sel.type === "camera" ? v.pos : v.target;
      dist = 2.5;
    }
    const pivot = orbit ? orbit.target : center;
    const dir = workCamera.position.clone().sub(pivot);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0.5, 1);
    workCamera.position.copy(center).addScaledVector(dir.normalize(), dist);
    workCamera.lookAt(center);
    if (orbit) {
      orbit.target.copy(center);
      orbit.update();
    }
    renderAt(clockT);
  }

  /* 인물 머리 높이(바라보는 곳). 앉은 인물은 엉덩이 높이 + 상체. vnew.js 의 [○○ 보기]도 이 함수를 쓴다(구현 하나). */
  function headHeight(ca, meta) {
    const h = Number(meta.heightM) || 1.7;
    if ((ca.pose || "stand") === "sit") {
      const hip = ca.sit && Number(ca.sit.hipY) > 0 ? Number(ca.sit.hipY) : 0.5;
      return r3(hip + 0.75 * h / 1.7);
    }
    return r3(h * 0.94);
  }

  function lookAtActor(id) {
    return applyOnce((cut) => {
      const ca = cutActor(cut, id);
      if (!ca) return;
      const p = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
      writeCam(cut, null, new THREE.Vector3(Number(p[0]) || 0, headHeight(ca, actorMeta(id)), Number(p[2]) || 0));
    });
  }

  /* [카메라를 여기로]: 작업 카메라 자리·방향으로 촬영 카메라를 옮긴다(바라보는 곳은 같은 거리, 없으면 3m 앞). */
  function placeCameraHere() {
    if (edit.view !== "work") return false;
    return applyOnce((cut) => {
      const v = poseVectors(cut);
      const dist = Math.max(0.5, v.target.distanceTo(v.pos) || 3);
      const dir = new THREE.Vector3();
      workCamera.getWorldDirection(dir);
      const pos = workCamera.position.clone();
      writeCam(cut, pos, pos.clone().addScaledVector(dir, dist));
    });
  }

  /* 렌즈 프리셋(작업 200 lensMm — 16:9 세로 화각 = 2·atan(10.125/mm)). */
  function setLens(mm) {
    const lens = Math.max(LENS_MIN, Math.min(LENS_MAX, Number(mm) || 50));
    return applyOnce((cut) => {
      const cam = cutCamera(cut);
      cam.lensMm = lens;
      cam.fov = Math.round(2 * Math.atan(10.125 / lens) * 180 / Math.PI * 10) / 10;
      cam.manual = true;
    });
  }

  function cameraInfo() {
    const cut = editCut();
    const cam = (cut && cut.camera) || {};
    const fov = Number(cam.fov) || camera.fov;
    return {
      lensMm: Number(cam.lensMm) || Math.round(10.125 / Math.tan(fov * Math.PI / 360)),
      lensSet: !!cam.lensMm,
      fov: Math.round(fov * 10) / 10,
      heightM: Math.round(camera.position.y * 100) / 100,
      hasEndKey: !!cam.moveTo,
      keyU: edit.keyU,
      editingEnd: !!cam.moveTo && edit.keyU >= 0.5,
    };
  }

  /* 시작/끝 키: 지금 보이는 촬영 카메라 자세를 시작(cut.camera) 또는 끝(cut.camera.moveTo)으로 저장한다. */
  function setKey(which) {
    const cut = editCut();
    if (!cut || recording) return false;
    const pinned = !!edit.pinCut;
    const frac = pinned ? edit.keyU : clamp01((clockT - (Number(cut.start) || 0)) / Math.max(0.001, (Number(cut.end) || 0) - (Number(cut.start) || 0)));
    const pose = cameraPoseAt(cutCamera(cut), frac, room().depth);
    const ok = applyOnce((c) => {
      const cam = cutCamera(c);
      if (which === "end") {
        cam.moveTo = { pos: clampCam(pose.pos).map(r3), target: clampCam(pose.target).map(r3) };
        edit.keyU = 1;
      } else {
        setVec(cam, "pos", clampCam(pose.pos));
        setVec(cam, "target", clampCam(pose.target));
        edit.keyU = 0;
      }
      edit.pinCut = c;
      cam.manual = true;
    });
    emitState();
    return ok;
  }

  function clearEndKey() {
    const ok = applyOnce((cut) => {
      const cam = cutCamera(cut);
      delete cam.moveTo;
      cam.manual = true;
      edit.keyU = 0;
      edit.pinCut = cut;
    });
    emitState();
    return ok;
  }

  /* 키 스크러버(0~1): 데이터는 바꾸지 않고 그 컷의 그 시점을 보여 준다. */
  function scrubKey(u) {
    const cut = editCut();
    if (!cut || recording) return;
    pause();
    edit.pinCut = cut;
    edit.keyU = clamp01(Number(u) || 0);
    const start = Number(cut.start) || 0;
    const end = Number(cut.end) || start;
    clockT = Math.min(start + (end - start) * edit.keyU, Math.max(start, end - 1e-3));
    renderAt(clockT);
    emitState();
  }

  /* ---- 카메라 시점 드래그: 대상 둘레로 돌기(tumble) · Shift = 평행 이동(truck/pedestal) · 휠 = 앞뒤로(dolly) ---- */
  function tumble(cut, dx, dy) {
    const v = poseVectors(cut);
    const off = v.pos.clone().sub(v.target);
    const sph = new THREE.Spherical().setFromVector3(off);
    sph.theta -= dx * TUMBLE_RAD_PER_PX;
    sph.phi = Math.max(0.05, Math.min(Math.PI - 0.05, sph.phi - dy * TUMBLE_RAD_PER_PX));
    off.setFromSpherical(sph);
    writeCam(cut, v.target.clone().add(off), null);
  }

  function truck(cut, dx, dy) {
    const v = poseVectors(cut);
    const rectH = canvas.clientHeight || height;
    const s = (2 * v.pos.distanceTo(v.target) * Math.tan((camera.fov * Math.PI / 180) / 2)) / Math.max(1, rectH);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
    const delta = right.multiplyScalar(-dx * s).add(up.multiplyScalar(dy * s));
    writeCam(cut, v.pos.clone().add(delta), v.target.clone().add(delta));
  }

  function dolly(cut, deltaY) {
    const v = poseVectors(cut);
    const off = v.pos.clone().sub(v.target);
    const dist = Math.max(0.3, Math.min(40, off.length() * Math.exp(deltaY * 0.0015)));
    writeCam(cut, v.target.clone().add(off.normalize().multiplyScalar(dist)), null);
  }

  const raycaster = new THREE.Raycaster();
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);

  /* 화면 좌표(client) → 지금 보는 카메라(viewCamera)의 광선. */
  function rayFrom(clientX, clientY) {
    const rect = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : { left: 0, top: 0, width, height };
    const ndc = new THREE.Vector2(((clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1, -((clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1);
    raycaster.setFromCamera(ndc, viewCamera());
    return raycaster;
  }

  /* 작업 207: 화면 좌표 아래 수평면(높이 y, 기본 바닥 0) 점. 그 면을 못 맞히면(지평선 위) null. */
  function groundAt(clientX, clientY, y) {
    const p = new THREE.Vector3();
    groundPlane.constant = -(Number(y) || 0);
    return rayFrom(clientX, clientY).ray.intersectPlane(groundPlane, p) ? p : null;
  }

  let lastPickPoint = null; // 작업 207: pickAt 이 맞힌 3D 점(인물을 잡은 높이)

  /* 화면 좌표(client)에서 인물·카메라 아이콘·바라보는 곳 십자를 고른다. 없으면 null. */
  function pickAt(clientX, clientY) {
    lastPickPoint = null;
    rayFrom(clientX, clientY);
    const targets = [];
    if (current) {
      Object.keys(current.scene.actors).forEach((id) => {
        const g = current.scene.actors[id].mannequin.group;
        if (g.visible) {
          g.userData.pick = { type: "actor", id };
          targets.push(g);
        }
      });
    }
    if (camIcon.visible) targets.push(camPick);
    targets.push(crossPick);
    const hits = raycaster.intersectObjects(targets, true);
    for (const hit of hits) {
      let o = hit.object;
      while (o && !(o.userData && o.userData.pick)) o = o.parent;
      if (o) {
        lastPickPoint = hit.point ? hit.point.clone() : null;
        return Object.assign({}, o.userData.pick);
      }
    }
    return null;
  }

  function clickAt(clientX, clientY) {
    const hit = pickAt(clientX, clientY);
    if (edit.aim) {
      // [이 인물 보기]: 인물을 누르면 그 머리 높이를 바라본다. 빈 곳은 무시(카메라 선택 유지).
      if (hit && hit.type === "actor") {
        lookAtActor(hit.id);
        setAim(false);
      }
      return hit;
    }
    select(hit);
    return hit;
  }

  const drag = { on: false, x: 0, y: 0, moved: false, shift: false, id: null,
    actor: null, cut: null, x0: 0, grab: null, facing0: 0, planeY: 0, // 작업 207: actor = 끌고 있는 인물 id
    pose: null, still: false }; // 작업 209a: pose = 끌고 있는 포즈 손잡이, still = 포즈 중 몸통을 눌렀다(끌어도 아무것도 안 함)

  /* 작업 207: 뷰포트를 눌렀다 = 휠 확대·축소를 받는다. 포커스를 잃으면(blur) 휠은 페이지 스크롤로 돌려준다. */
  function setStageFocus(on) {
    edit.focused = !!on;
    if (orbit) orbit.enableZoom = edit.focused;
  }

  function setCursor(value) {
    if (canvas.style && canvas.style.cursor !== value) canvas.style.cursor = value;
  }

  /* 작업 207: 인물을 누르면 고르고, 끌면 바닥면(y=0)에서 옮긴다(회전 도구면 좌우로 방향). 잡은 자리 차이(grab)를 지켜 튀지 않게. */
  function beginActorDrag(event) {
    if (edit.aim) return false; // [이 인물 보기] 조준은 클릭으로
    const hit = pickAt(event.clientX, event.clientY);
    if (!hit || hit.type !== "actor") return false;
    // 작업 209a: 포즈 중인 인물의 몸통을 끌면 아무것도 하지 않는다(손잡이를 놓친 끌기가 인물·카메라를 옮기지 않게).
    if (edit.pose && edit.sel && edit.sel.type === "actor" && edit.sel.id === hit.id) {
      drag.still = true;
      return true;
    }
    const cut = editCut();
    const ca = cutActor(cut, hit.id);
    if (!cut || !ca) return false;
    if (!edit.sel || edit.sel.type !== "actor" || edit.sel.id !== hit.id) select(hit);
    const p = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
    // 잡은 높이의 수평면에서 끈다(몸통을 잡아도 인물이 커서를 따라간다 — 바닥 y=0 면은 멀리 맞아 3배쯤 빨라진다).
    // 카메라 눈높이 가까이는 면이 거의 나란해 튀므로 카메라 아래 0.3m까지만. 높이는 바뀌지 않는다(바닥 위 이동).
    const camY = viewCamera().position.y;
    const hitY = lastPickPoint ? lastPickPoint.y : 0;
    drag.planeY = Math.max(0, Math.min(hitY, camY - 0.3));
    const g = groundAt(event.clientX, event.clientY, drag.planeY);
    drag.actor = hit.id;
    drag.cut = cut;
    drag.x0 = event.clientX;
    drag.grab = g ? [(Number(p[0]) || 0) - g.x, (Number(p[2]) || 0) - g.z] : [0, 0];
    drag.facing0 = Number(ca.facingDeg) || 0;
    if (orbit) orbit.enabled = false; // 인물을 끄는 동안 작업 카메라는 돌지 않는다
    return true;
  }

  function moveDraggedActor(event) {
    const cut = drag.cut;
    if (!cut || !cutActor(cut, drag.actor)) return;
    pause();
    if (edit.tool === "rotate") {
      setActorFacing(cut, drag.actor, drag.facing0 + (event.clientX - drag.x0) * ACTOR_TURN_DEG_PER_PX);
    } else {
      const g = groundAt(event.clientX, event.clientY, drag.planeY);
      if (!g) return;
      moveActorTo(cut, drag.actor, g.x + drag.grab[0], g.z + drag.grab[1]);
    }
    setCursor("grabbing");
    renderAt(clockT);
    emit("edit", { cut, kind: "live" });
  }

  /* 끈 인물을 놓는다: 움직였으면 한 단계(commit), 아니면 클릭(고르기). */
  function endActorDrag(event, click) {
    const cut = drag.cut;
    const moved = drag.moved;
    drag.actor = null;
    drag.cut = null;
    if (orbit) orbit.enabled = edit.view === "work" && !edit.dragging;
    setCursor("grab");
    if (moved) {
      commitEdit(cut);
      renderAt(clockT);
      emit("edit", { cut, kind: "commit" });
    } else if (click) {
      clickAt(event.clientX, event.clientY);
    }
  }

  /* 고를 수 있는 인물 위에서는 손 모양 커서. */
  function hoverCursor(event) {
    if (recording || playing) return;
    if (edit.pose && pickPose(event.clientX, event.clientY)) {
      setCursor("pointer");
      return;
    }
    const hit = pickAt(event.clientX, event.clientY);
    setCursor(hit && hit.type === "actor" && !edit.aim ? "grab" : "");
  }

  function onPointerDown(event) {
    if (recording || (event.button !== undefined && event.button !== 0)) return;
    setStageFocus(true);
    if (stage.focus) stage.focus({ preventScroll: true });
    if (gizmo && (gizmo.dragging || gizmo.axis)) return; // 기즈모를 잡았다
    drag.on = true;
    drag.x = event.clientX;
    drag.y = event.clientY;
    drag.moved = false;
    drag.shift = !!event.shiftKey;
    drag.id = event.pointerId;
    drag.pose = null;
    drag.still = false;
    const onPose = beginPoseDrag(event); // 작업 209a: 포즈 손잡이가 먼저
    const onActor = onPose || beginActorDrag(event);
    if ((onActor || edit.view === "shot") && canvas.setPointerCapture && event.pointerId !== undefined) {
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (error) {
        /* 무시 */
      }
    }
  }

  function onPointerMove(event) {
    if (!drag.on) {
      hoverCursor(event);
      return;
    }
    if (recording) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < CLICK_SLOP_PX) return;
    drag.moved = true;
    if (drag.pose) {
      movePoseDrag(event);
      return;
    }
    if (drag.still) return;
    if (drag.actor) {
      moveDraggedActor(event);
      return;
    }
    if (edit.view !== "shot") return; // 작업 시점 드래그는 OrbitControls
    const cut = editCut();
    if (!cut) return;
    pause();
    drag.x = event.clientX;
    drag.y = event.clientY;
    if (drag.shift || event.shiftKey) truck(cut, dx, dy);
    else tumble(cut, dx, dy);
    renderAt(clockT);
    emit("edit", { cut, kind: "live" });
  }

  function onPointerUp(event) {
    if (!drag.on) return;
    drag.on = false;
    if (canvas.releasePointerCapture && drag.id !== undefined && drag.id !== null) {
      try {
        canvas.releasePointerCapture(drag.id);
      } catch (error) {
        /* 무시 */
      }
    }
    if (drag.pose) {
      endPoseDrag(event, true);
      return;
    }
    if (drag.still) {
      drag.still = false;
      if (!drag.moved) clickAt(event.clientX, event.clientY);
      return;
    }
    if (drag.actor) {
      endActorDrag(event, true);
      return;
    }
    if (gizmo && gizmo.dragging) return;
    if (drag.moved) {
      if (edit.view === "shot") {
        const cut = editCut();
        commitEdit(cut);
        emit("edit", { cut, kind: "commit" });
      }
      return;
    }
    clickAt(event.clientX, event.clientY);
  }

  let wheelTimer = 0;
  // 작업 207: 휠을 무시했을 때 뷰포트 위 짧은 안내(1.5초, 4초에 한 번).
  const wheelHint = document.createElement("div");
  wheelHint.className = "vnew-previz-wheel-hint";
  wheelHint.textContent = WHEEL_HINT_TEXT;
  wheelHint.style.display = "none";
  stage.appendChild(wheelHint);
  let wheelHintTimer = 0;
  let wheelHintAt = -Infinity;
  function showWheelHint() {
    const now = Date.now();
    if (wheelHintTimer || now - wheelHintAt < WHEEL_HINT_GAP_MS) return;
    wheelHintAt = now;
    wheelHint.style.display = "";
    wheelHintTimer = window.setTimeout(() => {
      wheelHintTimer = 0;
      wheelHint.style.display = "none";
    }, WHEEL_HINT_MS);
  }

  function onWheel(event) {
    if (recording) return;
    // 작업 207: 뷰포트를 누르기 전 휠은 페이지 스크롤이다 — preventDefault 하지 않고 샷도 고치지 않는다.
    if (!edit.focused) {
      showWheelHint();
      return;
    }
    if (edit.view !== "shot") return; // 작업 시점 휠은 OrbitControls
    const cut = editCut();
    if (!cut) return;
    if (event.preventDefault) event.preventDefault();
    pause();
    dolly(cut, Number(event.deltaY) || 0);
    renderAt(clockT);
    emit("edit", { cut, kind: "live" });
    // 연달아 굴린 휠은 한 단계로 묶는다.
    if (wheelTimer) window.clearTimeout(wheelTimer);
    wheelTimer = window.setTimeout(() => {
      wheelTimer = 0;
      commitEdit(cut);
      emit("edit", { cut, kind: "commit" });
    }, WHEEL_COMMIT_MS);
  }

  /* 작업 207: 고른 인물을 방향키로 0.1m(Shift 0.5m) — 지금 보는 카메라의 수평 앞/오른쪽 기준. [ / ] = −15° / +15°.
     키 한 번 = 되돌리기 한 단계. 인물을 고르지 않았으면 false(페이지 기본 동작). */
  const NUDGE_ARROWS = { arrowup: [0, 1], arrowdown: [0, -1], arrowleft: [-1, 0], arrowright: [1, 0] };
  function nudgeKey(event, code, key) {
    const sel = edit.sel;
    if (!sel || sel.type !== "actor") return false;
    const arrow = NUDGE_ARROWS[code.toLowerCase()] || NUDGE_ARROWS[key];
    const turn = code === "BracketLeft" || key === "[" ? -NUDGE_DEG : code === "BracketRight" || key === "]" ? NUDGE_DEG : 0;
    if (!arrow && !turn) return false;
    const id = sel.id;
    applyOnce((cut) => {
      const ca = cutActor(cut, id);
      if (!ca) return;
      if (turn) {
        setActorFacing(cut, id, (Number(ca.facingDeg) || 0) + turn);
        return;
      }
      const cam = viewCamera();
      cam.updateMatrixWorld(true);
      const fwd = new THREE.Vector3();
      cam.getWorldDirection(fwd);
      fwd.y = 0;
      if (fwd.lengthSq() < 1e-8) { // 바로 내려다보는 카메라: 화면 위쪽 = 앞
        fwd.set(0, 1, 0).applyQuaternion(cam.quaternion);
        fwd.y = 0;
      }
      if (fwd.lengthSq() < 1e-8) fwd.set(0, 0, -1);
      fwd.normalize();
      const right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0)).normalize();
      const step = event.shiftKey ? NUDGE_BIG_M : NUDGE_M;
      const move = right.multiplyScalar(arrow[0] * step).addScaledVector(fwd, arrow[1] * step);
      const p = Array.isArray(ca.pos) ? ca.pos : [0, 0, 0];
      moveActorTo(cut, id, (Number(p[0]) || 0) + move.x, (Number(p[2]) || 0) + move.z);
    });
    return true;
  }

  function isTyping(target) {
    const t = target || null;
    if (!t) return false;
    const tag = String(t.tagName || "").toUpperCase();
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!t.isContentEditable;
  }

  /* 단축키(뷰포트 포커스일 때만, 입력 중엔 무시): Q 선택 · W 이동 · E 회전 · F 맞춰 보기 · Ctrl+Z / Ctrl+Shift+Z · Esc.
     한글 입력 상태에서도 되게 event.code(KeyW 등)를 먼저 본다. */
  function handleKey(event) {
    if (!event || recording || isTyping(event.target)) return false;
    const code = String(event.code || "");
    const key = String(event.key || "").toLowerCase();
    const mod = !!(event.ctrlKey || event.metaKey);
    const is = (letter) => code === "Key" + letter.toUpperCase() || key === letter;
    let done = true;
    if (mod && is("z")) {
      if (event.shiftKey) redo();
      else undo();
    } else if (mod && is("y")) {
      redo();
    } else if (mod || event.altKey) {
      done = false;
    } else if (nudgeKey(event, code, key)) {
      done = true;
    } else if (is("q")) {
      setTool("select");
    } else if (is("w")) {
      setTool("translate");
    } else if (is("e")) {
      setTool("rotate");
    } else if (is("f")) {
      frameSelection();
    } else if (is("p")) {
      setPoseMode(!edit.pose); // 작업 209a: 포즈 모드 켜고 끄기(인물 하나를 골랐을 때)
    } else if (key === "escape") {
      if (edit.pose) {
        setPoseMode(false);
      } else {
        setAim(false);
        select(null);
      }
    } else {
      done = false;
    }
    if (done && event.preventDefault) event.preventDefault();
    return done;
  }

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  stage.addEventListener("keydown", handleKey);
  // 작업 207: 손가락·펜이 끊기면(pointercancel) 끌던 인물을 그 자리에 놓는다(클릭은 아님). 포커스를 잃으면 휠을 돌려준다.
  function onPointerCancel(event) {
    if (!drag.on) return;
    drag.on = false;
    drag.still = false;
    if (drag.pose) endPoseDrag(event, false);
    if (drag.actor) endActorDrag(event, false);
  }
  function onStageBlur() {
    setStageFocus(false);
  }
  canvas.addEventListener("pointercancel", onPointerCancel);
  stage.addEventListener("blur", onStageBlur);

  function onVisibility() {
    // 탭이 숨겨지면 재생을 멈춘다(브라우저가 rAF 를 멈춰 시각이 어긋나는 것 방지).
    if (document.visibilityState === "hidden" && playing) pause();
  }
  document.addEventListener("visibilitychange", onVisibility);

  function destroy() {
    pause();
    recording = false;
    document.removeEventListener("visibilitychange", onVisibility);
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointermove", onPointerMove);
    canvas.removeEventListener("pointerup", onPointerUp);
    canvas.removeEventListener("wheel", onWheel);
    stage.removeEventListener("keydown", handleKey);
    canvas.removeEventListener("pointercancel", onPointerCancel);
    stage.removeEventListener("blur", onStageBlur);
    if (wheelTimer) window.clearTimeout(wheelTimer);
    if (wheelHintTimer) window.clearTimeout(wheelHintTimer);
    [gizmo, orbit].forEach((ctrl) => {
      try {
        if (ctrl && typeof ctrl.dispose === "function") ctrl.dispose();
      } catch (error) {
        /* 무시 */
      }
    });
    gizmo = null;
    orbit = null;
    Object.keys(edit.listeners).forEach((k) => {
      edit.listeners[k].length = 0;
    });
    if (current) {
      threeScene.remove(current.scene.root);
      current.scene.dispose();
      current = null;
    }
    try {
      renderer.dispose();
    } catch (error) {
      /* 무시 */
    }
    revokeTextures();
    if (stage.parentNode) stage.parentNode.removeChild(stage);
    listeners.length = 0;
  }

  return {
    get duration() {
      return totalDuration;
    },
    get currentTime() {
      return clockT;
    },
    // 텍스처를 받은 뒤 첫 장면·첫 프레임을 그린다. create() 가 이 Promise 를 기다린 뒤 controller 를
    // 돌려주므로, 호출자는 텍스처가 입혀진 상태의 뷰포트를 받는다.
    ready() {
      return Promise.resolve()
        .then(preloadTextures)
        .then(() => {
          buildScene(0);
          renderAt(0);
        })
        .catch((error) => {
          // 텍스처 준비가 통째로 실패해도 색으로라도 그린다.
          console.warn("[previz] 텍스처 준비 중 오류 — 색으로 그립니다:", error);
          buildScene(0);
          renderAt(0);
        });
    },
    play,
    pause,
    seek,
    setScene,
    record,
    renderFrames,
    onTime(cb) {
      if (typeof cb === "function") listeners.push(cb);
    },
    get isPlaying() {
      return playing;
    },
    get isRecording() {
      return recording;
    },
    // --- 작업 201 편집기 API ---
    setViewMode,
    get viewMode() {
      return edit.view;
    },
    setTool,
    get tool() {
      return edit.tool;
    },
    setSnap,
    get snap() {
      return edit.snap;
    },
    select,
    get selection() {
      return edit.sel ? Object.assign({}, edit.sel) : null;
    },
    setAimMode: setAim,
    lookAtActor,
    headHeight,
    placeCameraHere,
    setLens,
    cameraInfo,
    frameSelection,
    setKey,
    clearEndKey,
    scrubKey,
    get keyU() {
      return edit.keyU;
    },
    editCut,
    // 패널에서 고친 뒤: 지금 보이는 자세 그대로 다시 그린다(키 스크러버 유지).
    refresh() {
      if (!playing && !recording) renderAt(clockT);
    },
    commitEdit,
    undo,
    redo,
    get canUndo() {
      return edit.undo.length > 0;
    },
    get canRedo() {
      return edit.redo.length > 0;
    },
    setHelpers(on) {
      edit.helpersOn = !!on;
      if (!playing && !recording) renderAt(clockT);
    },
    get helpersVisible() {
      return helpers.visible;
    },
    // 작업 207: [이름표] — 장소·소품·빛 이름표 보이기/숨기기(인물 이름표는 늘 보인다).
    setLabels(on) {
      edit.labelsOn = !!on;
      if (!playing && !recording) renderAt(clockT);
    },
    get labelsVisible() {
      return edit.labelsOn;
    },
    get stageFocused() {
      return edit.focused;
    },
    get hasGizmo() {
      return !!gizmo;
    },
    handleKey,
    pickAt,
    clickAt,
    onSelect(cb) {
      if (typeof cb === "function") edit.listeners.select.push(cb);
    },
    onEdit(cb) {
      if (typeof cb === "function") edit.listeners.edit.push(cb);
    },
    onState(cb) {
      if (typeof cb === "function") edit.listeners.state.push(cb);
    },
    state: stateInfo,
    // --- 작업 209a 포즈 모드 API(패널 채널 박스·버튼) ---
    setPoseMode,
    get poseMode() {
      return edit.pose;
    },
    selectJoint,
    get poseJoint() {
      return edit.joint;
    },
    jointDeg,
    setJointDeg,
    rootYOf,
    setRootY,
    resetJoint,
    resetPose,
    mirrorPose,
    onPose(cb) {
      if (typeof cb === "function") edit.listeners.pose.push(cb);
    },
    // 검사용(가짜 렌더러·가짜 기즈모): 내부 물체.
    _editor: { helpers, proxy, workCamera, shotCamera: camera, stage, canvas, overlay, wheelHint,
      get gizmo() { return gizmo; }, get orbit() { return orbit; }, poseGroup, get poseControls() { return poseCtl; },
      get scene() { return current ? current.scene : null; } },
    destroy,
  };
}

window.vnewPreviz = {
  /* host 요소에 프리비즈 뷰포트를 만든다. 텍스처(벽·바닥)를 opts.textureFetch 로 미리 받아 입힌
     뒤 첫 프레임을 그리므로, controller 를 담은 Promise 를 돌려준다.
     opts.textureFetch(apiUrl) → Promise<objectURL|null>: Authorization 헤더로 받아 blob →
     URL.createObjectURL. 같은 출처 blob 이라 캔버스가 오염되지 않아 captureStream 녹화가 된다. */
  create(host, options) {
    if (!host) throw new Error("프리비즈를 그릴 host 요소가 없습니다.");
    const opts = Object.assign({}, options || {});
    // 작업 201: 편집기 조작(OrbitControls·TransformControls)을 불러온다. 실패해도 뷰어·녹화는 그대로(기즈모·궤도만 없음).
    const controls = opts.controls
      ? Promise.resolve(opts.controls)
      : Promise.all([import(ORBIT_CONTROLS_URL), import(TRANSFORM_CONTROLS_URL)])
        .then(([orbitMod, transformMod]) => ({ OrbitControls: orbitMod.OrbitControls, TransformControls: transformMod.TransformControls }))
        .catch((error) => {
          console.warn("[previz] 편집 조작(OrbitControls·TransformControls)을 불러오지 못했습니다:", error);
          return null;
        });
    return controls.then((lib) => {
      opts.controls = lib;
      const controller = createController(host, opts);
      return controller.ready().then(() => controller);
    });
  },
};
