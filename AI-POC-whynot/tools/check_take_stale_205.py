"""작업 205 검사 — ⑤ 통 테이크 gen 의 프리비즈 stale · 바뀐 테이크만 다시 만들기 · 상단 프로젝트 이름. 유료·네트워크 0.

  (t-a) 통 테이크 gen: previzEditRev 기록 · 장면 고침(editRev + 구간 stale) → stale · staleUnits = 그 테이크
  (t-b) 다시 녹화한 구간 키 → stale(그 테이크만) · 안 바뀜 → stale 아님 · 보기 stale/staleUnits
  (t-c) 컷 단위 previz gen 은 지금처럼(revision·editRev·옛 녹화 키) · staleUnits []
  (t-d) 옛 gen(previzEditRev 없음): editRev 만 달라도 거짓 stale 아님
  (t-e) onlyChanged: 안 바뀐 테이크 결과 재사용(키 참조) · 바뀐 테이크만 queued · 금액 = 그 테이크만 · 실사 회차도 그 테이크만
  (t-f) onlyChanged 409: 이전 gen 미완료 · 테이크 나눔 바뀜 · 금액 다름
  (t-g) 상단 바: 같은 id 의 이름 없는 갱신은 이름 유지 · 이름 모르면 "프로젝트"(pid 안 보임)
  (t-h) node --check vnew.js · app.js

실행: python -X utf8 -B tools/check_take_stale_205.py
"""

from __future__ import annotations

import copy
import json
import os
import re
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
    "t-a": "통 테이크 고친 장면 → stale · staleUnits",
    "t-b": "다시 녹화 키 → stale · 안 바뀜 → 아님",
    "t-c": "컷 단위 previz gen 은 지금처럼",
    "t-d": "옛 gen(previzEditRev 없음) 거짓 stale 아님",
    "t-e": "바뀐 테이크만: 재사용 · 금액 · 실사",
    "t-f": "바뀐 테이크만 409(미완료 · 나눔 바뀜 · 금액)",
    "t-g": "상단 바 이름 유지 · pid 안 보임",
    "t-h": "node --check vnew.js · app.js",
}
OWNER, PID = "u205", "a" * 32
PUBLIC = ROOT / "frontend" / "public"


def _plan(seg_secs=(12.0, 12.0, 12.0), keys=None, edit_rev=0) -> dict:
    """구간 3개(컷 3개씩, 12초) → 테이크 1 = 구간 1·2(컷 1–6), 테이크 2 = 구간 3(컷 7–9)."""
    cuts = [{"id": f"c{i}", "no": i} for i in range(1, 10)]
    segs = [{"id": f"s{n}", "cutIds": [f"c{3 * (n - 1) + j}" for j in (1, 2, 3)], "seconds": s}
            for n, s in enumerate(seg_secs, start=1)]
    keys = keys or {}
    recordings = {seg["id"]: {"status": "ready", "key": keys.get(seg["id"], f"previz/3/{seg['id']}.mp4"),
                              "seconds": seg["seconds"]} for seg in segs}
    previz = {"revision": 3, "approved": True, "recordings": recordings, "shotBrief": {"status": "ready", "updatedAt": "T1"}}
    if edit_rev:
        previz["editRev"] = edit_rev
    return {"conti": {"cuts": cuts, "segments": segs}, "previz": previz, "input": {"videoModel": "fal:take-model"}}


def _done(gen: dict, real: bool = False) -> dict:
    """gen 을 완료로(웹툰 칸 completed + outputKey, 실사 회차가 있으면 실사도)."""
    g = copy.deepcopy(gen)
    for unit in g["units"]:
        unit["webtoon"].update(status="completed", outputKey=f"out/{g['genId']}/{unit['id']}-web.mp4")
        if real:
            unit["real"] = {**unit["real"], "status": "completed", "outputKey": f"out/{g['genId']}/{unit['id']}-real.mp4"}
    g["status"] = "done"
    g["finalWebtoon"] = {"status": "done", "key": f"out/{g['genId']}/final-web.mp4", "error": ""}
    if real:
        g["realRun"] = {"n": 1, "audio": True, "usd": 1.0}
        g["finalReal"] = {"status": "done", "key": f"out/{g['genId']}/final-real-r1.mp4", "error": "", "n": 1}
    return g


def _extract_js(source: str, name: str) -> str:
    """app.js 에서 function name(...) { ... } 원문(중괄호 짝 맞춤, 문자열·템플릿 안 괄호는 이 함수들에 없다)."""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    if not m:
        raise AssertionError(f"app.js 에 function {name} 이 없습니다")
    i = source.index("{", m.end())
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start(): j + 1]
    raise AssertionError(f"function {name} 의 끝을 찾지 못했습니다")


def check_take_stale_205() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import plan as plan_mod  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    # ---- 가짜 계획 저장소 · 모델 caps · 금액(유료·네트워크 0) ----
    store: dict = {}
    spawned: list[str] = []
    patches = {
        (gm, "_gen_mode"): lambda plan: "previz-take",
        (gm, "_previz_model_endpoint"): lambda plan: "take-model",
        (gm, "_real_take_model"): lambda gen=None: "take-model",
        (gm, "_take_unit_price"): lambda endpoint: 0.01,
        (gm, "_take_parts_for"): lambda *a, **k: [],
        (gm, "check_preconditions"): lambda plan, owner: [],
        (gm, "_spawn_driver"): lambda owner, pid, gen_id: spawned.append(gen_id),
        (plan_mod, "load_plan"): lambda owner, pid: (copy.deepcopy(store["plan"]), "etag"),
    }

    def _mutate(owner, pid, mutator):
        data = copy.deepcopy(store["plan"])
        mutator(data)
        store["plan"] = data
        return data

    patches[(plan_mod, "mutate_plan")] = _mutate
    saved = {key: getattr(*key) for key in patches}
    for (mod, name), fn in patches.items():
        setattr(mod, name, fn)
    try:
        # ================= (t-a) 고친 장면 → stale · staleUnits =================
        try:
            plan = _plan()
            gen = gm._new_take_gen(plan)
            expect("t-a", [[u["id"], u["startCutNo"], u["endCutNo"], u["recSegIds"]] for u in gen["units"]],
                   [["take1", 1, 6, ["s1", "s2"]], ["take2", 7, 9, ["s3"]]], "테이크 나눔(take1 = 컷 1–6)")
            expect("t-a", ["previzEditRev" in gen, gen.get("previzEditRev")], [True, 0], "_new_take_gen 이 previzEditRev 를 기록")
            expect("t-a", gm._gen_previz_stale(gen, plan), False, "만든 직후 stale 아님")
            edited = copy.deepcopy(plan)
            edited["previz"]["editRev"] = 1
            edited["previz"]["recordings"]["s1"]["stale"] = True  # previz._mark_stale 와 같은 모양(컷 1 고침)
            expect("t-a", [gm._gen_previz_stale(gen, edited), gm.gen_stale_units(gen, edited)], [True, ["take1"]],
                   "컷 1 카메라 고침(editRev+1, 구간 1 stale) → stale · staleUnits = [take1]")
            only_rev = copy.deepcopy(plan)
            only_rev["previz"]["editRev"] = 1
            expect("t-a", [gm._gen_previz_stale(gen, only_rev), gm.gen_stale_units(gen, only_rev)],
                   [True, ["take1", "take2"]], "editRev 만 달라짐(바뀐 구간 모름) → 모든 테이크")
            gen_e = gm._new_take_gen(_plan(edit_rev=2))
            expect("t-a", gen_e.get("previzEditRev"), 2, "editRev 2 인 계획의 gen 은 previzEditRev 2")
        except Exception as exc:  # noqa: BLE001
            crash("t-a", exc)

        # ================= (t-b) 다시 녹화 키 · 안 바뀜 · 보기 =================
        try:
            plan = _plan()
            gen = _done(gm._new_take_gen(plan))
            rerec = _plan(keys={"s1": "previz/3/s1-e1.mp4"}, edit_rev=1)
            expect("t-b", [gm._gen_previz_stale(gen, rerec), gm.gen_stale_units(gen, rerec)], [True, ["take1"]],
                   "구간 1 다시 녹화(seg1-e1.mp4, editRev 1) → stale · [take1]")
            rerec3 = _plan(keys={"s3": "previz/3/s3-e1.mp4"}, edit_rev=1)
            expect("t-b", gm.gen_stale_units(gen, rerec3), ["take2"], "구간 3 다시 녹화 → [take2]")
            expect("t-b", [gm._gen_previz_stale(gen, plan), gm.gen_stale_units(gen, plan)], [False, []], "안 바뀜 → stale 아님")
            brief = copy.deepcopy(plan)
            brief["previz"]["shotBrief"]["updatedAt"] = "T2"
            expect("t-b", gm.gen_stale_units(gen, brief), ["take1", "take2"], "지시서 새로 만듦 → 모든 테이크")
            # 보기(gen_view): stale · staleUnits · onlyChanged 금액
            gm_presign = gm._presign
            gm._presign = lambda key, **k: ("https://x/" + key) if key else ""
            saved_view = {n: getattr(gm, n) for n in ("_take_start_real_block", "real_fallback_max", "_real_block_text")}
            gm._take_start_real_block = lambda owner, plan: ""
            gm.real_fallback_max = lambda plan, gen: None
            gm._real_block_text = lambda owner, plan, gen: ""
            try:
                view = gm.gen_view(OWNER, PID, {**rerec, "gen": gen})
            finally:
                gm._presign = gm_presign
                for n, fn in saved_view.items():
                    setattr(gm, n, fn)
            oc = view.get("onlyChanged") or {}
            expect("t-b", [view.get("stale"), view.get("staleUnits"), oc.get("available"), oc.get("takes")],
                   [True, ["take1"], True, ["take1"]], "보기: stale · staleUnits · onlyChanged(available, takes)")
        except Exception as exc:  # noqa: BLE001
            crash("t-b", exc)

        # ================= (t-c) 컷 단위 previz gen 은 지금처럼 =================
        try:
            plan = _plan()
            cut_gen = {"mode": "previz", "previzRevision": 3, "briefUpdatedAt": "T1", "units": []}
            expect("t-c", gm._gen_previz_stale(cut_gen, plan), False, "컷 단위: 같은 revision · editRev 0 → 아님")
            expect("t-c", gm._gen_previz_stale(cut_gen, _plan(edit_rev=1)), True, "컷 단위: editRev 올라감 → stale(작업 200 그대로)")
            expect("t-c", gm._gen_previz_stale({**cut_gen, "previzRevision": 2}, plan), True, "컷 단위: revision 다름 → stale")
            old_cut = {"mode": "previz", "units": [{"recSegId": "s1", "recordingKey": "previz/3/s1.mp4"}]}
            expect("t-c", [gm._gen_previz_stale(old_cut, plan), gm._gen_previz_stale(old_cut, _plan(keys={"s1": "k2"}))],
                   [False, True], "컷 단위 옛 gen: 녹화 키 비교")
            expect("t-c", [gm.gen_stale_units(cut_gen, _plan(edit_rev=1)), gm._gen_previz_stale({"mode": "i2v"}, plan)],
                   [[], False], "컷 단위 staleUnits [] · 옛 i2v 는 stale 아님")
        except Exception as exc:  # noqa: BLE001
            crash("t-c", exc)

        # ================= (t-d) 옛 gen(previzEditRev 없음) =================
        try:
            plan = _plan()
            old = gm._new_take_gen(plan)
            old.pop("previzEditRev")
            expect("t-d", [gm._gen_previz_stale(old, _plan(edit_rev=2)), gm.gen_stale_units(old, _plan(edit_rev=2))],
                   [False, []], "205 이전 통 테이크 gen: 키 같으면 editRev 만으로 stale 아님")
            expect("t-d", gm.gen_stale_units(old, _plan(keys={"s3": "k3"}, edit_rev=2)), ["take2"],
                   "205 이전 gen 도 녹화 키가 바뀌면 그 테이크")
        except Exception as exc:  # noqa: BLE001
            crash("t-d", exc)

        # ================= (t-e) onlyChanged: 재사용 · 금액 · 실사 =================
        try:
            base = _plan()
            prev = _done(gm._new_take_gen(base))
            now = _plan(keys={"s1": "previz/3/s1-e1.mp4"}, edit_rev=1)
            store["plan"] = {**copy.deepcopy(now), "gen": copy.deepcopy(prev)}
            est = gm.estimate(now)
            rows = {r["id"]: r["webtoonUsd"] for r in est["takes"]}
            quote = gm.only_changed_quote(now, prev)
            expect("t-e", [quote["available"], quote["takes"], quote["usd"], quote["real"]],
                   [True, ["take1"], round(rows["take1"], 2), False], "금액 = 바뀐 테이크(take1) 웹툰 몫만")
            expect("t-e", quote["usd"] < est["totalUsd"], True, "금액이 전체 새로 만들기보다 작다")
            spawned.clear()
            gid = gm.start_generate(OWNER, PID, quote["usd"], only_changed=True)
            g = store["plan"]["gen"]
            u1, u2 = g["units"]
            expect("t-e", [gid == g["genId"] != prev["genId"], spawned == [gid], g["status"], g["mode"]],
                   [True, True, "running", "previz-take"], "새 gen 시작 · 드라이버 1번")
            expect("t-e", [u1["webtoon"]["status"], u1["webtoon"].get("outputKey"), u1["recKeys"]],
                   ["queued", "", ["previz/3/s1-e1.mp4", "previz/3/s2.mp4"]], "바뀐 take1 은 새 녹화로 queued")
            expect("t-e", [u2["webtoon"]["status"], u2["webtoon"]["outputKey"], u2.get("reusedFrom")],
                   ["completed", prev["units"][1]["webtoon"]["outputKey"], prev["genId"]],
                   "안 바뀐 take2 는 이전 결과 키 그대로(복사·내려받기 없음)")
            queued = [(u["id"], v) for u in g["units"] for v in ("real", "webtoon") if u[v]["status"] in ("queued", "waiting")]
            expect("t-e", [queued, g["finalWebtoon"]["status"], g.get("onlyChanged")],
                   [[("take1", "webtoon")], "waiting", {"fromGenId": prev["genId"], "takes": ["take1"]}],
                   "제출(원장) 대상은 take1 웹툰 하나 · 병합은 평소대로 waiting")
            expect("t-e", [len(store["plan"].get("genHistory") or []), store["plan"]["genHistory"][-1]["genId"]],
                   [1, prev["genId"]], "이전 gen 은 genHistory 요약")
            # 실사 회차가 있던 gen → 실사도 바뀐 테이크만
            prev_r = _done(gm._new_take_gen(base), real=True)
            store["plan"] = {**copy.deepcopy(now), "gen": copy.deepcopy(prev_r)}
            q2 = gm.only_changed_quote(now, prev_r)
            real_take1 = round(gm._take_usd("take-model", 24.0, "480p"), 2)
            expect("t-e", [q2["real"], q2["realUsd"], q2["usd"]], [True, real_take1, round(q2["webtoonUsd"] + real_take1, 2)],
                   "실사 회차가 있던 gen: 실사 금액도 take1 만")
            gm.start_generate(OWNER, PID, q2["usd"], only_changed=True)
            g2 = store["plan"]["gen"]
            expect("t-e", [g2["autoReal"]["confirmUsd"], gm.real_estimate(g2)["usd"], g2["units"][1]["realReused"]["status"]],
                   [real_take1, real_take1, "completed"], "autoReal = take1 실사 몫 · 재사용 실사 칸 보관")
            saved_real = {n: getattr(gm, n) for n in ("_require_take_real", "_real_ready_error", "_validate_real_cast",
                                                     "_real_missing_names", "_mutate_locked")}
            fal_key = gm.vpoc_settings.FAL_KEY
            gm._require_take_real = lambda *a, **k: None
            gm._real_ready_error = lambda gen: None
            gm._validate_real_cast = lambda *a, **k: None
            gm._real_missing_names = lambda *a, **k: []
            gm._mutate_locked = _mutate
            gm.vpoc_settings.FAL_KEY = "test-key"
            try:
                run = gm.start_real(OWNER, PID, real_take1, audio=True)
            finally:
                gm.vpoc_settings.FAL_KEY = fal_key
                for n, fn in saved_real.items():
                    setattr(gm, n, fn)
            g3 = store["plan"]["gen"]
            expect("t-e", [run["usd"], g3["units"][0]["real"]["status"], g3["units"][1]["real"]["status"],
                           g3["units"][1]["real"]["outputKey"], "realReused" in g3["units"][1]],
                   [real_take1, "queued", "completed", prev_r["units"][1]["real"]["outputKey"], False],
                   "실사 회차 1: take1 만 queued · take2 는 이전 실사 결과 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("t-e", exc)

        # ================= (t-f) onlyChanged 409 =================
        try:
            base = _plan()
            now = _plan(keys={"s1": "previz/3/s1-e1.mp4"}, edit_rev=1)

            def code_of(prev_gen, plan_now, usd):
                store["plan"] = {**copy.deepcopy(plan_now), "gen": copy.deepcopy(prev_gen)}
                try:
                    gm.start_generate(OWNER, PID, usd, only_changed=True)
                except VpocError as err:
                    return [err.status, err.kind, "새로 영상 만들기" in err.message or err.kind == "CostMismatch"]
                return "시작됨"

            prev = _done(gm._new_take_gen(base))
            partial = {**copy.deepcopy(prev), "status": "partial"}
            expect("t-f", code_of(partial, now, 0.0), [409, "OnlyChangedUnavailable", True],
                   "이전 gen 미완료 → 409 · '새로 영상 만들기' 안내")
            split = _plan(seg_secs=(12.0, 20.0, 12.0), keys={"s1": "previz/3/s1-e1.mp4"}, edit_rev=1)
            expect("t-f", code_of(prev, split, 0.0), [409, "OnlyChangedUnavailable", True], "테이크 나눔 바뀜(구간 2 길어짐) → 409")
            expect("t-f", gm.gen_stale_units(prev, split), ["take1", "take2"], "나눔 바뀜 → 모든 테이크 stale")
            expect("t-f", code_of(prev, now, gm.estimate(now)["totalUsd"]), [409, "CostMismatch", True],
                   "전체 금액으로 확인 → 409 CostMismatch")
            expect("t-f", code_of(prev, base, 0.0)[:2], [409, "OnlyChangedUnavailable"], "안 바뀜 → 409")
            q = gm.only_changed_quote(base, prev)
            expect("t-f", [q["available"], q["block"]], [False, "바뀐 테이크가 없습니다."], "안 바뀜 → 보기 onlyChanged 쓸 수 없음")
        except Exception as exc:  # noqa: BLE001
            crash("t-f", exc)
    finally:
        for (mod, name), fn in saved.items():
            setattr(mod, name, fn)

    # ================= (t-g) 상단 바(node) =================
    node = shutil.which("node")
    try:
        app_src = (PUBLIC / "app.js").read_text(encoding="utf-8")
        render_src = _extract_js(app_src, "renderTopProject")
        expect("t-g", ["project.title || project.id" in render_src, '"프로젝트"' in render_src], [False, True],
               "renderTopProject: pid 대신 '프로젝트'")
        if not node:
            bad["t-g"].append("node 가 없어 상단 바 동작을 검사하지 못했습니다")
        else:
            funcs = "\n".join(_extract_js(app_src, n) for n in ("setCurrentProject", "topProject", "renderTopProject"))
            harness = """
const el = {textContent: "", dataset: {}, title: "", classList: {toggle() {}}, removeAttribute() {}, setAttribute() {}};
const $ = (s) => (s === "#topProjectName" ? el : {classList: {toggle() {}}});
const state = {token: "t", activeView: "vnew", currentProject: null};
const refills = [];
function getWorkingProject() { return null; }
function setWorkingProject() {}
function refillProjectTitle(id) { refills.push(id); }
function pathForView(v, id) { return "/p/" + id; }
""" + funcs + """
const P = "%s";
const out = [];
setCurrentProject({id: P, title: "test3 복사본", track: "video_new"}); out.push(el.textContent);
setCurrentProject({id: P, track: "video_new"}); out.push(el.textContent, state.currentProject.title);
setCurrentProject({id: P, title: ""}); out.push(el.textContent);
setCurrentProject({id: "b".repeat(32), title: "다른 것"}); out.push(el.textContent);
state.currentProject = null; refills.length = 0;
setCurrentProject({id: P}); out.push(el.textContent, refills.length);
console.log(JSON.stringify(out));
""" % PID
            with tempfile.TemporaryDirectory(prefix="chk205-") as tmp:
                path = Path(tmp) / "top.js"
                path.write_text(harness, encoding="utf-8", newline="\n")
                proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            if proc.returncode != 0:
                bad["t-g"].append("node 실패: " + (proc.stderr or "")[-600:])
            else:
                got = json.loads(proc.stdout.strip().splitlines()[-1])
                expect("t-g", got, ["test3 복사본", "test3 복사본", "test3 복사본", "test3 복사본", "다른 것", "프로젝트", 1],
                       "같은 id 이름 없는 갱신 → 이름 유지 · 다른 id → 새 이름 · 이름 모름 → '프로젝트' + 다시 읽기")
                expect("t-g", any(PID in str(x) for x in got), False, "pid 가 상단에 보이지 않음")
    except Exception as exc:  # noqa: BLE001
        crash("t-g", exc)

    # ================= (t-h) node --check =================
    try:
        if not node:
            bad["t-h"].append("node 가 없습니다")
        for name in ("vnew.js", "app.js"):
            if not node:
                break
            proc = subprocess.run([node, "--check", str(PUBLIC / name)], capture_output=True, text=True, encoding="utf-8", timeout=120)
            expect("t-h", [name, proc.returncode, (proc.stderr or "")[-300:]], [name, 0, ""], f"node --check {name}")
        vsrc = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        expect("t-h", ["바뀐 테이크만 다시 만들기 (" in vsrc, "프리비즈가 바뀌었습니다 — " in vsrc, "onlyChanged: true" in vsrc],
               [True, True, True], "vnew.js: 안내 문구 · 버튼 · onlyChanged 요청")
        if node:
            js = "\n".join(_extract_js(vsrc, n) for n in ("takeStaleNames", "takeStaleText")) + """
const gen = {units: [{id: "take1", startCutNo: 1, endCutNo: 6}, {id: "take2", startCutNo: 7, endCutNo: 9}], staleUnits: ["take1"]};
console.log(JSON.stringify([takeStaleText(gen), takeStaleNames(gen, ["take2", "take1"])]));
"""
            with tempfile.TemporaryDirectory(prefix="chk205-") as tmp:
                path = Path(tmp) / "notice.js"
                path.write_text(js, encoding="utf-8", newline="\n")
                proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            got = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else proc.stderr[-300:]
            expect("t-h", got, ["프리비즈가 바뀌었습니다 — 테이크 1(컷 1–6)이 지금 영상과 다릅니다.", "테이크 1(컷 1–6), 테이크 2(컷 7–9)"],
                   "⑤ 안내 문구(바뀐 테이크 이름 · 컷 범위)")
    except Exception as exc:  # noqa: BLE001
        crash("t-h", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 205 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 205 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 205 ({group}) {title}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_take_stale_205() else 1)
