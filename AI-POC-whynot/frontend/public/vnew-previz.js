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

   전역: window.vnewPreviz.create(host, {scenes, textures, width, height}) → controller.
   controller = { play(), pause(), seek(t), setScene(i), duration, onTime(cb),
                  record(fromT, toT) → Promise<Blob>, destroy() }.

   좌표: x 오른쪽, y 위, z 카메라 쪽(+). north 벽은 z = -depth/2. 단위 m·초.

   vendor 수정 금지 — three 만 쓴다. */

import * as THREE from "/vendor/three/three.module.min.js?v=__ASSET_VERSION__";

const SIDES = ["north", "east", "south", "west"];
const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;
const RECORD_FPS = 24;

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

/* ================================================================ 마네킹

   관절이 이어진 사람 모양. 각 부위는 Group(피벗)에 Mesh 를 매달아, 피벗을 돌리면
   무릎·팔꿈치가 자연스럽게 굽는다. 떨어진 상자를 쓰지 않는다. heightM 로 전체를 스케일. */
class Mannequin {
  constructor(color, heightM) {
    this.group = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial({ color: colorOf(color), roughness: 0.6, metalness: 0.0 });
    this.mat = mat;

    // 기준 키 1.7m 에 맞춘 치수. heightM 로 전체 group 을 비례 조정한다.
    const scale = (Number(heightM) || 1.7) / 1.7;
    this.group.scale.setScalar(scale);

    // 엉덩이(hips): 다리·몸통이 매달리는 뿌리. 서 있을 때 y≈0.9.
    this.hips = new THREE.Group();
    this.hips.position.y = 0.9;
    this.group.add(this.hips);

    // 몸통(척추) — 엉덩이에서 위로.
    this.spine = new THREE.Group();
    this.hips.add(this.spine);
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.42, 4, 10), mat);
    torso.position.y = 0.31;
    torso.castShadow = true;
    this.spine.add(torso);

    // 목·머리 — 몸통 위.
    this.neck = new THREE.Group();
    this.neck.position.y = 0.6;
    this.spine.add(this.neck);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 16, 12), mat);
    head.position.y = 0.13;
    head.castShadow = true;
    this.neck.add(head);

    // 팔(어깨 피벗 → 위팔 → 팔꿈치 → 아래팔). 좌우.
    this.shoulderL = this._makeArm(mat, 0.22);
    this.shoulderR = this._makeArm(mat, -0.22);
    this.spine.add(this.shoulderL.pivot);
    this.spine.add(this.shoulderR.pivot);

    // 다리(엉덩이 피벗 → 허벅지 → 무릎 → 정강이). 좌우.
    this.hipL = this._makeLeg(mat, 0.1);
    this.hipR = this._makeLeg(mat, -0.1);
    this.hips.add(this.hipL.pivot);
    this.hips.add(this.hipR.pivot);

    this.group.traverse((obj) => {
      if (obj.isMesh) obj.castShadow = true;
    });
  }

  _makeArm(mat, xOffset) {
    const pivot = new THREE.Group();
    pivot.position.set(xOffset, 0.52, 0);
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.055, 0.26, 4, 8), mat);
    upper.position.y = -0.15;
    pivot.add(upper);
    const elbow = new THREE.Group();
    elbow.position.y = -0.3;
    pivot.add(elbow);
    const fore = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.24, 4, 8), mat);
    fore.position.y = -0.14;
    elbow.add(fore);
    return { pivot, elbow };
  }

  _makeLeg(mat, xOffset) {
    const pivot = new THREE.Group();
    pivot.position.set(xOffset, 0, 0);
    const thigh = new THREE.Mesh(new THREE.CapsuleGeometry(0.08, 0.4, 4, 8), mat);
    thigh.position.y = -0.24;
    pivot.add(thigh);
    const knee = new THREE.Group();
    knee.position.y = -0.46;
    pivot.add(knee);
    const shin = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.4, 4, 8), mat);
    shin.position.y = -0.24;
    knee.add(shin);
    return { pivot, elbow: knee };
  }

  /* 포즈·제스처·발화를 매 프레임 적용한다. t 는 장면 시각(초) — 걷기·끄덕임 위상에 쓴다. */
  apply(pose, gesture, speaking, t) {
    // 기본 자세로 되돌린 뒤 포즈별로 굽힌다.
    this.hips.position.y = 0.9;
    this.hips.rotation.set(0, 0, 0);
    this.spine.rotation.set(0, 0, 0);
    this.neck.rotation.set(0, 0, 0);
    this._resetLimb(this.hipL, this.hipR, this.shoulderL, this.shoulderR);

    switch (pose) {
      case "sit": {
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

    this._applyGesture(gesture);

    if (speaking) {
      // 머리를 살짝 끄덕임.
      this.neck.rotation.x = Math.sin(t * 8) * 0.12;
    }
  }

  _resetLimb() {
    [this.hipL, this.hipR, this.shoulderL, this.shoulderR].forEach((limb) => {
      limb.pivot.rotation.set(0, 0, 0);
      limb.elbow.rotation.set(0, 0, 0);
    });
    // 팔은 기본적으로 몸 옆으로 내린다.
    this.shoulderL.pivot.rotation.z = 0.1;
    this.shoulderR.pivot.rotation.z = -0.1;
  }

  _bendLeg(leg, hipX, kneeX) {
    leg.pivot.rotation.x = hipX;
    leg.elbow.rotation.x = kneeX;
  }

  _applyGesture(gesture) {
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
        this.shoulderL.pivot.rotation.x = -0.9;
        this.shoulderR.pivot.rotation.x = -0.9;
        this.shoulderL.elbow.rotation.x = 0.6;
        this.shoulderR.elbow.rotation.x = 0.6;
        break;
      case "cover_face":
        // 두 팔을 얼굴 쪽으로.
        this.shoulderL.pivot.rotation.set(-Math.PI * 0.8, 0, 0.5);
        this.shoulderR.pivot.rotation.set(-Math.PI * 0.8, 0, -0.5);
        this.shoulderL.elbow.rotation.x = 1.2;
        this.shoulderR.elbow.rotation.x = 1.2;
        break;
      default:
        break;
    }
  }

  dispose() {
    this.group.traverse((obj) => {
      if (obj.isMesh) {
        if (obj.geometry) obj.geometry.dispose();
      }
    });
    if (this.mat) this.mat.dispose();
  }
}

/* ================================================================ 한 장면(Scene) 빌더

   방·빛·소품·인물 메시를 만들고, 시각(초)에 맞춰 카메라·인물을 갱신한다.
   오버레이(빛 출처·소품·인물 라벨)는 별도 레이어(여기서는 데이터만 노출)로 둔다. */
class PrevizScene {
  constructor(sceneData, textures, loader) {
    this.data = sceneData || {};
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
    // 텍스처 URL 이 있으면 이미지를 입히고, 없으면 색. 로드 실패해도 색으로 보인다.
    const mat = new THREE.MeshStandardMaterial({ color: colorOf(fallbackColor), roughness: 0.95, side });
    this._track(mat);
    if (url && this.loader) {
      this.loader.load(
        url,
        (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          mat.map = tex;
          mat.color.set(0xffffff);
          mat.needsUpdate = true;
          this._track(tex);
        },
        undefined,
        () => {
          /* 실패 시 색 유지 */
        }
      );
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
      const mannequin = new Mannequin(actor.color, actor.heightM);
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

  /* 카메라·인물을 시각 t 에 맞춰 갱신한다. 컷 경계는 하드 컷(즉시 전환). */
  update(t, camera) {
    const cut = this._cutAt(t);
    if (!cut) return;
    const start = Number(cut.start) || 0;
    const end = Number(cut.end) || start;
    const span = Math.max(0.001, end - start);
    const frac = clamp01((t - start) / span);

    // 카메라: moveTo 있으면 선형 보간, 없으면 고정.
    const cam = cut.camera || {};
    const pos = vec3(cam.pos, 0, 1.5, this.room.depth / 2 + 1);
    const target = vec3(cam.target, 0, 1.2, 0);
    if (cam.moveTo) {
      const pos2 = vec3(cam.moveTo.pos, pos.x, pos.y, pos.z);
      const tgt2 = vec3(cam.moveTo.target, target.x, target.y, target.z);
      camera.position.set(lerp(pos.x, pos2.x, frac), lerp(pos.y, pos2.y, frac), lerp(pos.z, pos2.z, frac));
      camera.lookAt(lerp(target.x, tgt2.x, frac), lerp(target.y, tgt2.y, frac), lerp(target.z, tgt2.z, frac));
    } else {
      camera.position.copy(pos);
      camera.lookAt(target);
    }
    if (cam.fov && camera.fov !== cam.fov) {
      camera.fov = Number(cam.fov);
      camera.updateProjectionMatrix();
    }

    // 인물: 위치(moveTo 선형 이동)·방향·포즈·제스처·발화.
    const cutActors = cut.actors || [];
    const seen = {};
    cutActors.forEach((ca) => {
      const entry = this.actors[ca.id];
      if (!entry) return;
      seen[ca.id] = true;
      const basePos = vec3(ca.pos, 0, 0, 0);
      let px = basePos.x;
      let pz = basePos.z;
      if (Array.isArray(ca.moveTo)) {
        const to = vec3(ca.moveTo, basePos.x, 0, basePos.z);
        px = lerp(basePos.x, to.x, frac);
        pz = lerp(basePos.z, to.z, frac);
      }
      entry.mannequin.group.position.set(px, 0, pz);
      entry.mannequin.group.rotation.y = ((Number(ca.facingDeg) || 0) * Math.PI) / 180;
      entry.mannequin.apply(ca.pose || "stand", ca.gesture || null, !!ca.speaking, t);

      // 인물 라벨 오버레이 위치(머리 위).
      entry._overlayPos = new THREE.Vector3(px, (Number(entry.data.heightM) || 1.7) + 0.2, pz);
    });
    // 이 컷에 없는 인물은 숨긴다.
    Object.keys(this.actors).forEach((id) => {
      this.actors[id].mannequin.group.visible = !!seen[id];
    });
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

  // --- 렌더러 ---
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
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
  stage.appendChild(canvas);
  stage.appendChild(overlay);
  while (host.firstChild) host.removeChild(host.firstChild);
  host.appendChild(stage);

  const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 200);
  const loader = new THREE.TextureLoader();
  loader.crossOrigin = "anonymous";

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
    const scene = new PrevizScene(sd, opts.textures || {}, loader);
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
    if (current) current.scene.update(t, camera);
    renderer.render(threeScene, camera);
    if (!recording) drawOverlay();
  }

  // 초기 장면.
  buildScene(0);
  renderAt(0);

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
      const anchor = item.pos || item.from;
      if (!anchor) return;
      const projected = anchor.clone().project(camera);
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
      overlay.style.display = "none";

      let stream;
      try {
        stream = canvas.captureStream(RECORD_FPS);
      } catch (error) {
        recording = false;
        overlay.style.display = "";
        reject(error);
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

  function onVisibility() {
    // 탭이 숨겨지면 재생을 멈춘다(브라우저가 rAF 를 멈춰 시각이 어긋나는 것 방지).
    if (document.visibilityState === "hidden" && playing) pause();
  }
  document.addEventListener("visibilitychange", onVisibility);

  function destroy() {
    pause();
    recording = false;
    document.removeEventListener("visibilitychange", onVisibility);
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
    play,
    pause,
    seek,
    setScene,
    record,
    onTime(cb) {
      if (typeof cb === "function") listeners.push(cb);
    },
    get isPlaying() {
      return playing;
    },
    get isRecording() {
      return recording;
    },
    destroy,
  };
}

window.vnewPreviz = {
  create(host, options) {
    if (!host) throw new Error("프리비즈를 그릴 host 요소가 없습니다.");
    return createController(host, options || {});
  },
};
