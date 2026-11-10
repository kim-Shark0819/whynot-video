"""작업 208 검사 — ④ 프리비즈 직접 잡은 자세(rig)·자세 키(keys): 저장 · 보존 · 영어 설명 · 지시서/영상 프롬프트. 유료·네트워크 0.

  (p-a) 정리: 각도 ±3.1416·소수 3자리 · rootY [-1.0, 0.3] · 모르는 관절 버림 · 0 관절 남김 · 빈 rig 없음 ·
        keys 최대 8 · u 순 · 같은 u 는 마지막 · pos 방 안·y 0 · 맞는 키가 없으면 없음
  (p-b) 옛 데이터(rig·keys 없음) → 정리 결과 바이트 그대로 · 키 없음 · LLM 설계 스키마에 rig·keys 없음
  (p-c) 보존: place_cut(locked) · _apply_cut_result · 보정(resolve_cut) · 연기 배정 · 다시 만들기(_restore_manual) · 고침 표시
  (p-d) FK: 오른 어깨 150° → 머리 위 · 팔꿈치 접어 얼굴 쪽 → own face height · own cheek · 쪼그림 · 한쪽 무릎
  (p-e) 키 2개 1.5 m 걷기 → "walks 1.5 m toward 지아" · 카메라 기준 화면 방향 · 60 낱말 안 · 결정적
  (p-f) 상대 턱 가까운 손 → 부드러운 턱 받침(173 글) · 움켜쥠 낱말 없음
  (p-g) 지시서 입력: userPoseEn 은 자세 잡은 인물만 · 지시 한 줄 · 자세 없는 장소는 지금 글 그대로
  (p-h) 테이크·컷 프롬프트: "User-set pose (follow exactly):" 는 그 컷에만 · 설명만 줄인다
  (p-i) pvSig: rig 만 바뀐 컷만 지문이 바뀐다
  (p-k) 목록 자세(pose sit·kneel·posture lean) 위 rig: 골반 = 자세 엉덩이 높이 + rootY · 손 높이 띠 JS 와 같음 · 0 관절 남김
  (p-j) LF 줄 끝(바꾼 파일)

실행: python -X utf8 -B tools/check_pose_rig_208.py
"""

from __future__ import annotations

import copy
import json
import re
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "p-a": "rig·keys 정리(자르기·반올림·모르는 관절·8개·정렬·같은 u)",
    "p-b": "옛 데이터 바이트 그대로 · 설계 스키마에 없음",
    "p-c": "보존: place_cut locked · 보정 · 연기 · 다시 만들기 · 고침 표시",
    "p-d": "FK: 머리 위 · 얼굴 높이 · 쪼그림 · 한쪽 무릎",
    "p-e": "키: 상대 쪽으로 걷기 · 화면 방향 · 60 낱말",
    "p-f": "턱 가까운 손 → 부드러운 턱 받침",
    "p-g": "지시서 입력 userPoseEn(자세 인물만) · 지시 한 줄",
    "p-h": "테이크·컷 프롬프트 User-set pose(그 컷만) · 설명만 줄임",
    "p-i": "pvSig: rig 만 바뀐 컷만",
    "p-k": "목록 자세 위 rig: 앉은 골반 높이 · 손 높이 · 0 관절 남김",
    "p-j": "LF 줄 끝",
}
ROOM = {"width": 6.0, "depth": 6.0, "height": 3.0}
CHANGED = ["app/src/vnew/previz_schema.py", "app/src/vnew/previz.py", "app/src/vnew/previz_collision.py",
           "app/src/vnew/previz_support.py", "app/src/vnew/shot_brief.py", "app/src/vnew/generate.py",
           "app/src/vnew/pose_describe.py", "tools/check_pose_rig_208.py"]
HOOK = ('    if not __import__("check_pose_rig_208").check_pose_rig_208(): '
        'fail("작업 208: 직접 잡은 자세(rig·keys) 보존 · 설명 · 프롬프트 검사 실패")')


def _scene(rig_cut2: dict | None = None, keys_cut2: list | None = None) -> dict:
    """장면 1개 · 컷 c1·c2(3초씩) · 민수(a1, 0,0,0 → +z) · 지아(a2, 0,0,2 → -z). 카메라는 +x 에서 -x 쪽을 본다."""
    timeline = []
    for i in (1, 2):
        timeline.append({"cutId": f"c{i}", "start": 3.0 * (i - 1), "end": 3.0 * i,
                         "camera": {"pos": [2.5, 1.5, 1.0], "target": [0.0, 1.2, 1.0], "fov": 54.4, "lensMm": 35},
                         "actors": [{"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0, "pose": "stand"},
                                    {"id": "a2", "pos": [0.0, 0.0, 2.0], "facingDeg": 180.0, "pose": "stand"}]})
    if rig_cut2 is not None:
        timeline[1]["actors"][0]["rig"] = rig_cut2
    if keys_cut2 is not None:
        timeline[1]["actors"][0]["keys"] = keys_cut2
    return {"locationId": "loc-office", "room": dict(ROOM),
            "actors": [{"id": "a1", "name": "민수", "charId": "ch1", "heightM": 1.7},
                       {"id": "a2", "name": "지아", "charId": "ch2", "heightM": 1.7}],
            "props": [], "timeline": timeline}


def _plan(scene: dict) -> dict:
    cuts = [{"id": f"c{i}", "no": i, "seconds": 3.0, "panels": [i], "locationEn": "A quiet office",
             "characters": [{"name": "민수", "actionEn": "looks at Jia"}, {"name": "지아", "actionEn": "smiles"}],
             "situationKo": f"상황 {i}", "dialogueKo": ""} for i in (1, 2)]
    return {"planId": "pl208", "conti": {"status": "ready", "cuts": cuts,
                                         "segments": [{"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}]},
            "previz": {"scenes": [scene]}, "script": {"entries": []}, "input": {}}


def _ca(scene: dict, cut_id: str, actor_id: str) -> dict:
    cut = next(c for c in scene["timeline"] if c["cutId"] == cut_id)
    return next(a for a in cut["actors"] if a["id"] == actor_id)


def check_pose_rig_208() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-900:])

    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import pose_describe as pd  # noqa: PLC0415
    from src.vnew import previz as pm  # noqa: PLC0415
    from src.vnew import previz_camera as pcam  # noqa: PLC0415
    from src.vnew import previz_collision as pcol  # noqa: PLC0415
    from src.vnew import previz_grammar as pgram  # noqa: PLC0415
    from src.vnew import previz_schema as ps  # noqa: PLC0415
    from src.vnew import shot_brief as sb  # noqa: PLC0415

    saved_char = gm._char_by_id
    gm._char_by_id = lambda owner, plan: {}  # noqa: SLF001 — 캐릭터 저장소 없이
    names = {"a1": "민수", "a2": "지아"}
    jia = {"id": "a2", "pos": [0.0, 0.0, 2.0], "facingDeg": 180.0}

    def describe(actor: dict, others=(jia,), camera=None) -> str:
        return pd.describe_actor_motion(actor, cut_seconds=3.0, others=list(others), name="민수", other_names=names, camera=camera)

    try:
        # ================= (p-a) 정리 =================
        try:
            rig = ps.normalize_rig({"spine": [0.12345, 5.0, -9.0], "elbowL": [0, 0, 0], "tail": [1, 1, 1],
                                    "head": [0.1, "x", 0], "rootY": -3.0, "neck": [-0.0, 0.2, 0.0004]})
            expect("p-a", rig, {"spine": [0.123, 3.142, -3.142], "elbowL": [0.0, 0.0, 0.0], "neck": [0.0, 0.2, 0.0], "rootY": -1.0},
                   "각도 ±3.1416 · 3자리 · 0 관절 남김 · 모르는 관절·잘못된 관절 버림 · rootY -1.0")
            expect("p-a", [ps.normalize_rig({"rootY": 0.9}), ps.normalize_rig({}), ps.normalize_rig({"hipL": [0, 0, 0]}),
                           ps.normalize_rig({"rootY": 0}), ps.normalize_rig("x")],
                   [{"rootY": 0.3}, None, {"hipL": [0.0, 0.0, 0.0]}, None, None], "rootY 0.3 상한 · 빈 rig·rootY 0 뿐 → None")
            bounds = ps._room_bounds(ROOM)  # noqa: SLF001
            raw_keys = [{"u": 0.9, "pos": [9, 5, 0], "facingDeg": 10}, {"u": 0.1, "pos": [0, 0, 0], "facingDeg": 0},
                        {"u": 0.1, "pos": [1, 0, 1], "facingDeg": 45, "rig": {"head": [0.3, 0, 0]}}, {"pos": [0, 0, 0]}]
            raw_keys += [{"u": i / 20, "pos": [0, 0, 0]} for i in range(5, 15)]
            keys = ps.normalize_pose_keys(raw_keys, bounds)
            expect("p-a", [len(keys), [k["u"] for k in keys][:3], keys[0], keys[-1]["u"] <= 0.65],
                   [8, [0.1, 0.25, 0.3], {"u": 0.1, "pos": [1.0, 0.0, 1.0], "facingDeg": 45.0, "rig": {"head": [0.3, 0.0, 0.0]}}, True],
                   "keys: u 순 · 같은 u 는 마지막 · 최대 8 · u 없는 키 버림")
            far = ps.normalize_pose_keys([{"u": 1.7, "pos": [9, 5, -9], "facingDeg": 20}], bounds)
            expect("p-a", far, [{"u": 1.0, "pos": [bounds[1], 0.0, bounds[2]], "facingDeg": 20.0}], "u 0..1 · pos 방 안·y 0")
            expect("p-a", [ps.normalize_pose_keys([{"pos": [0, 0, 0]}], bounds), ps.normalize_pose_keys([], bounds)],
                   [None, None], "맞는 키가 없으면 None")
            actors = ps._normalize_cut_actors([{"id": "a1", "pos": [0, 0, 0], "rig": {"shoulderR": [-2.6181, 0, 0]},  # noqa: SLF001
                                                "keys": [{"u": 0.5, "pos": [0, 0, 1]}]}], ["a1"], ROOM)
            expect("p-a", [actors[0].get("rig"), len(actors[0].get("keys") or []), "manual" in actors[0]],
                   [{"shoulderR": [-2.618, 0.0, 0.0]}, 1, False], "컷 인물 정리가 rig·keys 를 남긴다(manual 표시는 붙이지 않음)")
        except Exception as exc:  # noqa: BLE001
            crash("p-a", exc)

        # ================= (p-b) 옛 데이터 =================
        try:
            specs = [{"cutId": "c1", "seconds": 3.0}, {"cutId": "c2", "seconds": 3.0}]
            old = _scene()
            once = ps.normalize_scene(copy.deepcopy(old), location_id="loc-office", cut_specs=specs, user=True)
            twice = ps.normalize_scene(copy.deepcopy(once), location_id="loc-office", cut_specs=specs, user=True)
            posed = _scene(rig_cut2={"head": [0.3, 0, 0]}, keys_cut2=[{"u": 0, "pos": [0, 0, 0]}])
            posed_n = ps.normalize_scene(copy.deepcopy(posed), location_id="loc-office", cut_specs=specs, user=True)
            for cut in posed_n["timeline"]:
                for ca in cut["actors"]:
                    ca.pop("rig", None)
                    ca.pop("keys", None)
            text = json.dumps(once, sort_keys=True, ensure_ascii=False)
            expect("p-b", [json.dumps(twice, sort_keys=True, ensure_ascii=False) == text, '"rig"' in text, '"keys"' in text,
                           json.dumps(posed_n, sort_keys=True, ensure_ascii=False) == text],
                   [True, False, False, True], "옛 장면: 정리 결과 같음 · rig·keys 키 없음 · rig·keys 를 빼면 옛 결과와 바이트 같음")
            design = json.dumps(pm._DESIGN_CUT_ACTOR, ensure_ascii=False)  # noqa: SLF001
            expect("p-b", [bool(re.search(r'"(rig|keys)"', design))], [False], "LLM 설계 스키마 _DESIGN_CUT_ACTOR 에 rig·keys 없음")
        except Exception as exc:  # noqa: BLE001
            crash("p-b", exc)

        # ================= (p-c) 보존 =================
        try:
            rig_up = {"shoulderR": [-2.618, 0.0, 0.0]}
            scene = _scene(rig_cut2=rig_up)
            cut2 = scene["timeline"][1]
            ins = pm._actors_in_cut(scene, cut2)  # noqa: SLF001
            expect("p-c", [a.get("locked", False) for a in ins], [True, False], "_actors_in_cut: rig 가진 인물은 locked(manual 없이도)")
            g_c = pgram.normalize_grammar({"mainSubject": "민수", "shotType": "clean_single", "shotSize": "MCU", "vantage": "3q",
                                           "headTopY": 0.1, "subjectX": 0.5, "faceTurn": "toward_camera"})
            result = pcam.place_cut(copy.deepcopy(g_c), ins, ROOM, 1, min_match=0.7)
            pm._apply_cut_result(scene, cut2, result)  # noqa: SLF001
            a1 = _ca(scene, "c2", "a1")
            expect("p-c", [a1.get("rig"), a1["pos"], a1["facingDeg"]], [rig_up, [0.0, 0.0, 0.0], 0.0],
                   "place_cut(locked) + _apply_cut_result: rig 그대로 · 옮기지도 돌리지도 않는다")

            sc = _scene(rig_cut2=rig_up)
            c2 = sc["timeline"][1]
            c2["actors"][1]["pos"] = [0.05, 0.0, 0.05]  # 지아가 민수와 겹침
            pcol.resolve_cut(sc, c2, log_fixes=False)
            expect("p-c", [_ca(sc, "c2", "a1")["pos"], _ca(sc, "c2", "a1").get("rig")], [[0.0, 0.0, 0.0], rig_up],
                   "보정(resolve_cut): 자세 잡은 인물은 밀지 않고 rig 그대로")

            sg = _scene(rig_cut2=rig_up)
            _ca(sg, "c2", "a1")["gesture"] = "look_down"
            pm._apply_gestures([sg], [], {"c2": {"actions": [{"name": "민수", "gesture": "nod", "target": "none"}]}}, None)  # noqa: SLF001
            expect("p-c", [_ca(sg, "c2", "a1")["gesture"], _ca(sg, "c2", "a1").get("rig")], ["look_down", rig_up],
                   "연기 배정(_apply_gestures): 자세 잡은 인물은 그대로")

            prev = [_scene(rig_cut2=rig_up, keys_cut2=[{"u": 0.0, "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0}])]
            fresh = [_scene()]
            _ca(fresh[0], "c2", "a1")["pos"] = [1.0, 0.0, 1.0]
            count = pm._restore_manual(fresh, prev)  # noqa: SLF001
            got = _ca(fresh[0], "c2", "a1")
            expect("p-c", [count, got.get("rig"), len(got.get("keys") or []), got["pos"], "rig" in _ca(fresh[0], "c1", "a1")],
                   [1, rig_up, 1, [0.0, 0.0, 0.0], False], "다시 만들기(_restore_manual): manual 없이도 rig·keys 인물을 되살린다")

            before = [_scene()]
            after = [_scene(rig_cut2=rig_up)]
            pm._mark_manual(before, after)  # noqa: SLF001
            expect("p-c", [_ca(after[0], "c2", "a1").get("manual"), _ca(after[0], "c2", "a2").get("manual")], [True, None],
                   "고침 표시(_mark_manual): rig 만 바뀐 인물에 manual")
        except Exception as exc:  # noqa: BLE001
            crash("p-c", exc)

        # ================= (p-d) FK =================
        try:
            me = {"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0}
            pts = pd.body_points([0, 0, 0], 0.0, {"shoulderR": [-2.618, 0.0, 0.0]})
            expect("p-d", [pts["handR"][1] > pts["headTop"][1], describe(dict(me, rig={"shoulderR": [-2.618, 0, 0]}))],
                   [True, "right hand raised above head"], "오른 어깨 150° → 손이 머리 위")
            face = describe(dict(me, rig={"shoulderR": [-1.2, 0.0, 0.3], "elbowR": [-1.9, 0.0, 0.0]}))
            expect("p-d", [face], ["right hand raised to own face height, close to own cheek"], "팔꿈치 접어 얼굴 쪽 → face height")
            crouch = describe(dict(me, rig={"spine": [0.35, 0, 0], "neck": [0.3, 0.52, 0], "hipL": [-1.4, 0, 0], "hipR": [-1.4, 0, 0],
                                            "kneeL": [1.4, 0, 0], "kneeR": [1.4, 0, 0], "rootY": -0.4}), others=())
            expect("p-d", [crouch], ["knees bent, crouching (pelvis 0.4 m lower); torso leaning forward about 20°; "
                                     "head turned 30° to own left, tilted down"], "쪼그림 · 기울기 · 머리 돌림")
            kneel = describe(dict(me, rig={"kneeL": [1.5708, 0, 0], "hipR": [-1.5708, 0, 0], "kneeR": [1.5708, 0, 0], "rootY": -0.46}))
            expect("p-d", [kneel, describe(dict(me)), describe(dict(me, rig={}))], ["kneeling on one knee", "", ""],
                   "한쪽 무릎 · rig·keys 없으면 \"\"")
        except Exception as exc:  # noqa: BLE001
            crash("p-d", exc)

        # ================= (p-e) 키 =================
        try:
            walk = {"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0,
                    "keys": [{"u": 0.0, "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0},
                             {"u": 0.4, "pos": [0.0, 0.0, 1.5], "facingDeg": 0.0},
                             {"u": 1.0, "pos": [0.0, 0.0, 1.5], "facingDeg": 90.0}]}
            cam = {"pos": [2.5, 1.5, 1.0], "target": [0.0, 1.2, 1.0]}
            text = describe(walk, camera=cam)
            again = describe(copy.deepcopy(walk), camera=copy.deepcopy(cam))
            expect("p-e", [bool(re.search(r"0\.0–1\.2 s: walks 1\.5 m toward 지아 \(screen left\)", text)),
                           "1.2–3.0 s: turns 90° to face camera" in text, len(text.split()) <= 60, text == again],
                   [True, True, True, True], f"키 2개 걷기 → 상대 쪽 · 화면 왼쪽 · 카메라 보기 · 60 낱말 · 결정적: {text}")
            state = pd.state_at(walk, 0.2)
            expect("p-e", [round(state[0][2], 3), pd.state_at(walk, -1)[0][2], pd.state_at(walk, 2)[1]], [0.75, 0.0, 90.0],
                   "보간: smoothstep 가운데 · 첫 키 앞은 첫 키 · 끝 키 뒤는 끝 키")
            turn = pd.state_at({"keys": [{"u": 0, "pos": [0, 0, 0], "facingDeg": 170}, {"u": 1, "pos": [0, 0, 0], "facingDeg": -170}]}, 0.5)
            expect("p-e", [abs(round(pd._wrap(turn[1]), 3))], [180.0], "방향은 가까운 쪽으로(170° → -170° 가운데 ±180°)")  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            crash("p-e", exc)

        # ================= (p-f) 턱 =================
        try:
            near = {"id": "a1", "pos": [0.0, 0.0, 1.6], "facingDeg": 0.0, "rig": {"shoulderR": [-0.95, 0.0, 0.75], "elbowR": [-1.5, 0.0, 0.0]}}
            text = describe(near)
            expect("p-f", [gm.FACE_TOUCH_CHIN.format(lift="lifts", name="지아") in text, "soft, loving chin hold" in text,
                           bool(re.search(r"\b(grab|grabs|grips|gripping|squeez\w*|chok\w*|clutch\w*)\b", text))],
                   [True, True, False], f"상대 턱 가까운 손 → 173 턱 받침 글: {text}")
        except Exception as exc:  # noqa: BLE001
            crash("p-f", exc)

        # ================= (p-g) 지시서 입력 =================
        try:
            plan = _plan(_scene(rig_cut2={"shoulderR": [-2.618, 0.0, 0.0]}))
            tl = plan["previz"]["scenes"][0]["timeline"]
            text = sb._location_prompt(plan, "o", "office", {}, tl)  # noqa: SLF001
            pose_lines = [ln.strip() for ln in text.splitlines() if "userPoseEn" in ln and "—" in ln]
            c2_block = text.split("CUT c2:")[1]
            expect("p-g", [pose_lines, "userPoseEn" in text.split("CUT c2:")[0].split("CUT c1:")[1],
                           "userPoseEn — 민수" in c2_block, text.count("posed by the user")],
                   [["userPoseEn — 민수: right hand raised above head"], False, True, 1],
                   "userPoseEn 은 자세 잡은 인물(c2 민수)만 · 지시 한 줄")
            plain = _plan(_scene())
            text0 = sb._location_prompt(plain, "o", "office", {}, plain["previz"]["scenes"][0]["timeline"])  # noqa: SLF001
            expect("p-g", ["userPoseEn" in text0, "posed by the user" in text0], [False, False], "자세 없는 장소: 지금 입력 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("p-g", exc)

        # ================= (p-h) 테이크·컷 프롬프트 =================
        try:
            plan = _plan(_scene(rig_cut2={"shoulderR": [-2.618, 0.0, 0.0]}))
            seg = plan["conti"]["segments"][0]
            cast = [{"name": "민수", "colorName": "blue"}, {"name": "지아", "colorName": "red"}]
            label = "User-set pose (follow exactly):"
            take = gm._take_prompt(plan, seg, cast=cast, panel_nos=[], location_no=None, quiet=True)  # noqa: SLF001
            first, _, second = take.partition("3-6s")
            vibe = gm._vibe_take_prompt(plan, seg, real=False, cast=cast)  # noqa: SLF001
            vibe = vibe if isinstance(vibe, str) else str(vibe)
            shot2 = [ln for ln in vibe.splitlines() if ln.startswith("Shot 2")]
            expect("p-h", [take.count(label), label in first, f"{label} Minsu: right hand raised above head" in second,
                           vibe.count(label), bool(shot2) and label in shot2[0]],
                   [1, False, True, 1, True], "통 테이크(옛·vibe): 그 컷 줄에만 · 이름은 로마자")
            cut1 = gm._cut_prompt(plan, {"id": "x1", "cutIds": ["c1"]}, "webtoon", cast=cast, has_image_ref=False)  # noqa: SLF001
            cut2 = gm._cut_prompt(plan, {"id": "x2", "cutIds": ["c2"]}, "webtoon", cast=cast, has_image_ref=False)  # noqa: SLF001
            act2 = [ln for ln in cut2.splitlines() if ln.startswith("ACTION:")]
            expect("p-h", [label in cut1, bool(act2) and label in act2[0]], [False, True], "컷 프롬프트: 그 컷 ACTION 줄에만")
            plain = _plan(_scene())
            take0 = gm._take_prompt(plain, plain["conti"]["segments"][0], cast=cast, panel_nos=[], location_no=None, quiet=True)  # noqa: SLF001
            expect("p-h", [label in take0], [False], "자세 없는 테이크: 조각 없음")
            long = {"text": "민수: " + " ".join(["word"] * 200), "names": ["민수"]}
            piece = gm._user_pose_piece(long, gm.USER_POSE_SHORT_CHARS, lambda ko: "Minsu")  # noqa: SLF001
            expect("p-h", [piece.startswith(label + " Minsu: word"), len(piece) <= len(label) + 1 + gm.USER_POSE_SHORT_CHARS],
                   [True, True], "줄이기: 설명만 상한까지 자른다(머리말은 남는다)")
        except Exception as exc:  # noqa: BLE001
            crash("p-h", exc)

        # ================= (p-i) pvSig =================
        try:
            p0 = _plan(_scene())
            p1 = _plan(_scene(rig_cut2={"head": [0.3, 0.0, 0.0]}))
            expect("p-i", [sb.cut_pv_sig(p0, "c1") == sb.cut_pv_sig(p1, "c1"), sb.cut_pv_sig(p0, "c2") != sb.cut_pv_sig(p1, "c2")],
                   [True, True], "rig 만 바뀐 컷 c2 만 pvSig 가 바뀐다")
        except Exception as exc:  # noqa: BLE001
            crash("p-i", exc)

        # ================= (p-k) 목록 자세 위 rig(JS Mannequin.apply·applyBody·applyRig) =================
        try:
            def rnd(v):
                return [round(x, 3) for x in v]

            seated = {"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0, "pose": "sit",
                      "rig": {"shoulderR": [-1.5708, 0.0, 0.0], "rootY": -0.05}}
            pts = pd.body_points([0, 0, 0], 0.0, seated["rig"], 1.7, seated)
            # JS: 골반 = sit hips 0.5 + rootY · 오른 어깨 (-0.22, 골반+0.52, 0) · 팔 앞으로 0.3+0.29
            expect("p-k", [rnd(pts["pelvis"]), rnd(pts["handR"]), pd._band(pts["handR"][1], pts)],  # noqa: SLF001
                   [[0.0, 0.45, 0.0], [-0.22, 0.97, 0.59], "shoulder"], "앉은 rig: 골반 0.5+rootY · 손 위치·높이 띠가 JS 와 같다")
            jia_near = {"id": "a2", "pos": [0.0, 0.0, 0.75], "facingDeg": 180.0}
            expect("p-k", [describe(seated, others=()), describe(seated, others=(jia_near,)),
                           describe(dict(seated, pose="stand"), others=(jia_near,))],
                   ["right hand reaching forward at shoulder height", "right hand holds 지아's hand",
                    "right hand rests on 지아's shoulder"], "앉은 손은 서 있는 상대의 손 높이(서 있으면 어깨)")
            seat = dict(seated, sit={"hipY": 0.6, "legPitchDeg": 0.0}, heightM=1.87)
            expect("p-k", [round(pd.body_points([0, 0, 0], 0.0, seat["rig"], 1.87, seat)["pelvis"][1], 3),
                           describe(dict(seated, pose="kneel"), others=())],
                   [0.545, "right hand reaching forward at shoulder height"], "앉을 자리 hipY + rootY·키 · 무릎 자세에 '발 듦' 없음")
            zero_leg = pd.body_points([0, 0, 0], 0.0, {"hipL": [0.0, 0.0, 0.0]}, 1.7, {"pose": "sit"})
            zero_spine = pd.body_points([0, 0, 0], 0.0, {"spine": [0.0, 0.0, 0.0]}, 1.7, {"posture": "lean_forward"})
            lean = pd.body_points([0, 0, 0], 0.0, {}, 1.7, {"posture": "lean_forward"})
            expect("p-k", [round(zero_leg["kneeL"][1], 3), round(zero_leg["kneeR"][1], 3), round(zero_spine["head"][2], 3),
                           round(lean["head"][2], 3) > 0.2],
                   [0.04, 0.5, 0.0, True], "0 관절이 목록 자세를 덮어쓴다(앉은 왼 엉덩이 0 → 다리 곧게 · 기울기 0)")
            kept = ps._normalize_cut_actors([{"id": "a1", "pos": [0, 0, 0], "rig": {"elbowR": [0, 0, 0], "rootY": 0}}],  # noqa: SLF001
                                            ["a1"], ROOM)
            expect("p-k", [kept[0].get("rig")], [{"elbowR": [0.0, 0.0, 0.0]}], "정리 뒤에도 0 관절이 남는다(rootY 0 은 뺀다)")
        except Exception as exc:  # noqa: BLE001
            crash("p-k", exc)

        # ================= (p-j) LF =================
        try:
            crlf = [f for f in CHANGED if b"\r\n" in (ROOT / f).read_bytes()]
            expect("p-j", crlf, [], "바꾼 파일은 LF 줄 끝")
        except Exception as exc:  # noqa: BLE001
            crash("p-j", exc)
    finally:
        gm._char_by_id = saved_char  # noqa: SLF001

    ok = True
    for group, title in GROUPS.items():
        if group not in seen:
            print(f"[WARN] 작업 208 ({group}) {title} — 돌지 않음")
            continue
        if bad[group]:
            ok = False
            print(f"[FAIL] 작업 208 ({group}) {title}")
            for line in bad[group][:3]:
                print("    " + line[:300])
        else:
            print(f"[ok  ] 작업 208 ({group}) {title}")
    return ok


if __name__ == "__main__":
    result = check_pose_rig_208()
    print("check_vpoc.py 에 붙일 줄(main, check_previz_easy_207 다음):")
    print(HOOK)
    sys.exit(0 if result else 1)
