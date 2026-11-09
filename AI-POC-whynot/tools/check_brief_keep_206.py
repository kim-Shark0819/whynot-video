"""작업 206 검사 — 지시서는 바뀐 컷만 다시 쓴다 · 테이크별 지시서 stale · 지시서 만드는 중 409 · 확인창 금액 바뀜. 유료·네트워크 0.

  (b-a) 처음 지시서: 장소마다 1회 호출 · 모든 컷 항목에 pvSig · changedCuts = 모든 컷
  (b-b) 안 바뀜 → LLM 호출 0 · 컷 항목·장소 문장 바이트 그대로
  (b-c) 컷 8만 고침 → 그 장소 1회(그 장소 컷 전부 문맥으로) · 컷 8 항목만 교체 · 나머지·장소 문장 바이트 그대로
  (b-d) 옛 지시서(pvSig 없음) → 전부 새로(지금과 같다)
  (b-e) 테이크 gen(briefSig): 컷 8 고침 + 다시 녹화 + 지시서 다시 → staleUnits = [take2] · 금액 = take2 몫 · 바뀐 테이크만 시작
  (b-f) 옛 테이크 gen(briefSig 없음) → 작업 205 그대로(지시서 updatedAt 이 바뀌면 모든 테이크)
  (b-g) 지시서 만드는 중 → /generate(보통·onlyChanged) 409 BriefBuilding(한국어) · 지시서 없음은 BriefNotReady 그대로
  (b-h) 확인창 409 CostMismatch → 금액·테이크 목록 제자리 갱신 · 한국어 안내 · 코드 안 보임 · 창 유지(node 없으면 WARN)
  (b-i) node --check vnew.js
  (b-j) fix 1: 지시서 작성 중 컷 8 고침(+ 승인 → 409, start_skipped kind=Briefing) → 끝난 뒤 따라잡기 1번 · 컷 8만 교체
  (b-k) fix 1: 매번 바뀌는 컷 → 따라잡기 최대 2번에서 멈춤 · 새 승인(진행 중 409 여도)이 횟수를 되돌림
  (b-l) fix 1: 지시서 ready 인데 옛 컷 → /generate(보통·onlyChanged) 409 BriefBuilding · 지시서 작성 시작

실행: python -X utf8 -B tools/check_brief_keep_206.py
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "b-a": "처음 지시서: 장소마다 1회 · pvSig · changedCuts",
    "b-b": "안 바뀜 → LLM 0 · 바이트 그대로",
    "b-c": "컷 8만 → 그 장소 1회 · 컷 8만 교체",
    "b-d": "옛 지시서(pvSig 없음) → 전부 새로",
    "b-e": "테이크별 지시서 stale → [take2] · 금액 · 바뀐 테이크만",
    "b-f": "옛 테이크 gen(briefSig 없음) → 작업 205 그대로",
    "b-g": "지시서 만드는 중 → 409 BriefBuilding",
    "b-h": "확인창 CostMismatch → 금액 갱신 · 한국어 · 코드 안 보임",
    "b-i": "node --check vnew.js",
    "b-j": "작성 중 고침 → 끝난 뒤 따라잡기 1번 · 바뀐 컷만 교체 · 승인 409 로그",
    "b-k": "따라잡기 고리 막기(최대 2번) · 새 승인이 되돌림",
    "b-l": "옛 컷 지시서로 시작 → 409 BriefBuilding · 지시서 시작",
}
OWNER, PID = "u206", "b" * 32
PUBLIC = ROOT / "frontend" / "public"
OFFICE, STREET = "A quiet office", "A rainy street"


def _plan() -> dict:
    """컷 13개: 1–6 사무실, 7–13 거리. 구간 s1(1–3)·s2(4–6)·s3(7–13) 12초씩 → take1 = s1·s2, take2 = s3."""
    cuts, scenes = [], []
    for i in range(1, 14):
        cuts.append({"id": f"c{i}", "no": i, "seconds": 4.0, "panels": [i], "locationEn": OFFICE if i <= 6 else STREET,
                     "characters": [{"name": "민수", "actionEn": "looks at the door"}], "situationKo": f"상황 {i}",
                     "dialogueKo": ""})
    for loc_id, ids in (("loc-office", range(1, 7)), ("loc-street", range(7, 14))):
        timeline = [{"cutId": f"c{i}", "start": 4.0 * (i - 1), "end": 4.0 * i,
                     "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 54.4, "lensMm": 35},
                     "actors": [{"id": "a1", "pos": [0, 0, 0], "facingDeg": 0}]} for i in ids]
        scenes.append({"locationId": loc_id, "actors": [{"id": "a1", "name": "민수", "charId": "ch1", "color": "#4363d8"}],
                       "timeline": timeline})
    segs = [{"id": "s1", "cutIds": ["c1", "c2", "c3"], "seconds": 12.0},
            {"id": "s2", "cutIds": ["c4", "c5", "c6"], "seconds": 12.0},
            {"id": "s3", "cutIds": [f"c{i}" for i in range(7, 14)], "seconds": 12.0}]
    recordings = {s["id"]: {"status": "ready", "key": f"previz/3/{s['id']}.mp4", "seconds": 12.0} for s in segs}
    previz = {"revision": 3, "approved": True, "recordedAll": True, "recordings": recordings, "scenes": scenes}
    return {"planId": "pl206", "conti": {"status": "ready", "cuts": cuts, "segments": segs}, "previz": previz,
            "script": {"entries": []}, "input": {"videoModel": "fal:take-model"}}


def _edit_cut8(plan: dict, rerecord: bool = True) -> dict:
    """컷 8 렌즈 50mm 로 고침(+ 구간 3 다시 녹화, editRev 1)."""
    p = copy.deepcopy(plan)
    for scene in p["previz"]["scenes"]:
        for entry in scene["timeline"]:
            if entry["cutId"] == "c8":
                entry["camera"].update(lensMm=50, fov=39.6, manual=True)
    if rerecord:
        p["previz"]["editRev"] = 1
        p["previz"]["recordings"]["s3"]["key"] = "previz/3/s3-e1.mp4"
    return p


def _done(gen: dict) -> dict:
    g = copy.deepcopy(gen)
    for unit in g["units"]:
        unit["webtoon"].update(status="completed", outputKey=f"out/{g['genId']}/{unit['id']}-web.mp4")
    g["status"] = "done"
    g["finalWebtoon"] = {"status": "done", "key": f"out/{g['genId']}/final-web.mp4", "error": ""}
    return g


def _extract_js(source: str, name: str) -> str:
    """vnew.js 에서 function name(...) { ... } 원문(중괄호 짝 맞춤)."""
    m = re.search(r"\n  function\s+" + re.escape(name) + r"\s*\(", source)
    if not m:
        raise AssertionError(f"vnew.js 에 function {name} 이 없습니다")
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


_DIALOG_JS = r"""
function Node(tag) { this.tag = tag; this.children = []; this.style = {}; this.className = ""; this._text = "";
  this.listeners = {}; this.parentNode = null; this.disabled = false; this.attrs = {}; }
Object.defineProperty(Node.prototype, "textContent", {
  get: function () { return this._text + this.children.map(function (c) { return c.textContent; }).join(""); },
  set: function (v) { this._text = String(v); this.children = []; } });
Object.defineProperty(Node.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
Node.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
Node.prototype.removeChild = function (c) { this.children = this.children.filter(function (x) { return x !== c; }); c.parentNode = null; return c; };
Node.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
var document = { createElement: function (t) { return new Node(t); }, body: new Node("body") };
function walk(n, out) { out.push(n); n.children.forEach(function (c) { walk(c, out); }); return out; }
var GEN_WEBTOON_TITLE = "애니(웹툰)";
var sent = [], rendered = 0;
function renderPlan() { rendered += 1; }
function loadPlan() {}
function goToCharacters() {}
function previzRerecordButton() { return new Node("button"); }
function takePlanHintText(kind, n) { return "테이크 " + n + "개"; }
function takeRealEnabledOf() { return false; }
function genStylesCostText() { return ""; }
function genStylesBox() { return { node: new Node("div"), real: function () { return false; } }; }
function takeUsdSum(a, b) { return Math.round(((Number(a) || 0) + (Number(b) || 0)) * 100) / 100; }
var units2 = [{ id: "take1", startCutNo: 1, endCutNo: 6 }, { id: "take2", startCutNo: 7, endCutNo: 13 }];
function runCase(gen, fresh, opts) {
  state = { projectId: "p206", mounted: true, plan: { gen: gen } };
  sent = []; rendered = 0; document.body = new Node("body");
  api = function (path, init) {
    if (init && init.method === "POST") {
      sent.push(JSON.parse(init.body));
      if (sent.length === 1) return Promise.reject({ type: "CostMismatch", message: "처리 중 오류가 발생했습니다 (코드: CostMismatch)" });
      return Promise.resolve({ plan: fresh });
    }
    return Promise.resolve(JSON.parse(JSON.stringify(fresh)));
  };
  openGenerateConfirm(null, opts(gen));
  var scrim = document.body.children[0];
  var ok = walk(scrim, []).filter(function (n) { return n.tag === "button" && n.textContent === "만들기 시작"; })[0];
  ok.listeners.click[0]();
  return new Promise(function (r) { setTimeout(r, 20); }).then(function () {
    var nodes = walk(scrim, []);
    var err = nodes.filter(function (n) { return /vnew-plan-error/.test(n.className); })[0];
    var cost = nodes.filter(function (n) { return n.className === "vnew-gen-cost"; })[0];
    var takes = nodes.filter(function (n) { return n.className === "vnew-only-changed-takes"; })[0];
    var res = { open: scrim.parentNode === document.body, err: err ? err.textContent : "", cost: cost ? cost.textContent : "",
      takes: takes ? takes.textContent : "", okDisabled: ok.disabled, rendered: rendered > 0,
      code: /CostMismatch|코드:/.test(scrim.textContent) };
    ok.listeners.click[0]();
    return new Promise(function (r) { setTimeout(r, 20); }).then(function () {
      res.second = sent[1] || null; res.closed = scrim.parentNode !== document.body; return res; });
  });
}
var state, api;
var onlyGen = { mode: "previz-take", units: units2, estimate: { mode: "previz-take", takes: [] },
  onlyChanged: { available: true, takes: ["take2"], usd: 11.52, real: false } };
var onlyFresh = { gen: { mode: "previz-take", units: units2, estimate: { mode: "previz-take", takes: [] },
  onlyChanged: { available: true, takes: ["take1", "take2"], usd: 23.44, real: false } } };
var plainGen = { mode: "previz", units: [], estimate: { mode: "previz", totalUsd: 5 } };
var plainFresh = { gen: { mode: "previz", units: [], estimate: { mode: "previz", totalUsd: 6.25 } } };
runCase(onlyGen, onlyFresh, function (g) { return { onlyChanged: g.onlyChanged }; }).then(function (a) {
  return runCase(plainGen, plainFresh, function () { return {}; }).then(function (b) {
    console.log(JSON.stringify([a, b]));
  });
}).catch(function (e) { console.log(JSON.stringify(["ERR", String(e && e.stack || e)])); });
"""


def check_brief_keep_206() -> bool:
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
    from src.vnew import previz as pm  # noqa: PLC0415
    from src.vnew import shot_brief as sb  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    store: dict = {}
    calls: list[dict] = []
    counter = {"n": 0}
    on_call: list = []  # fix 1: LLM 호출 때마다 부르는 갈고리(작성 중 고침 흉내)
    spawned: list[tuple] = []  # fix 1: previz._spawn 대신 기록(검사가 차례로 돌린다)
    logs: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record):  # noqa: D401
            logs.append(record.getMessage())

    grab = _Grab(level=logging.INFO)
    vlog = logging.getLogger("vpoc")
    saved_level = vlog.level
    vlog.addHandler(grab)
    vlog.setLevel(logging.INFO)

    def fake_brief(blocks, *, model):
        counter["n"] += 1
        for hook in list(on_call):
            hook()
        text = blocks[0]["text"]
        loc = text.splitlines()[0]
        ids = re.findall(r"^CUT (\S+):$", text, re.M)
        calls.append({"loc": loc, "cuts": ids})
        n = counter["n"]
        return ({"sceneEn": f"Scene v{n} of {loc}. Soft grey light.",
                 "cuts": [{"cutId": cid, "shotEn": f"MS 35mm {cid} v{n}", "actionEn": f"looks v{n}",
                           "dialogue": []} for cid in ids]}, {"model": "fake-brief"})

    def _mutate(owner, pid, mutator):
        data = copy.deepcopy(store["plan"])
        mutator(data)
        store["plan"] = data
        return data, None

    patches = {
        (sb, "_brief"): fake_brief,
        (gm, "_char_by_id"): lambda owner, plan: {"ch1": {"appearanceEn": "short black hair", "outfitEn": "grey suit"}},
        (pm, "_location_for"): lambda plan, name: {"id": "loc-office" if "office" in str(name) else "loc-street"},
        (pm, "_location_id_for"): lambda loc, name: (loc or {}).get("id") or "",
        (gm, "_gen_mode"): lambda plan: "previz-take",
        (gm, "_previz_model_endpoint"): lambda plan: "take-model",
        (gm, "_take_unit_price"): lambda endpoint: 0.01,
        (gm, "_take_parts_for"): lambda *a, **k: [],
        (gm, "_spawn_driver"): lambda owner, pid, gen_id: None,
        (plan_mod, "load_plan"): lambda owner, pid: (copy.deepcopy(store["plan"]), "etag"),
        (plan_mod, "mutate_plan"): _mutate,
        (pm, "_spawn"): lambda func, owner, pid, task_id, *, name: spawned.append((func, owner, pid, task_id)),
    }
    saved = {key: getattr(*key) for key in patches}
    real_pre = gm.check_preconditions
    for (mod, name), fn in patches.items():
        setattr(mod, name, fn)

    def rerun(plan: dict) -> dict:
        """지시서 작업을 running 으로 두고 run_brief 를 바로 돈다. 결과 plan."""
        data = copy.deepcopy(plan)
        brief = data["previz"].get("shotBrief") or sb._empty_brief()
        brief["status"] = "running"
        brief["task"] = {"status": "running", "startedAt": "2026-11-10T00:00:00Z", "id": "t" + str(len(calls))}
        data["previz"]["shotBrief"] = brief
        store["plan"] = data
        calls.clear()
        sb.run_brief(OWNER, PID, brief["task"]["id"])
        return copy.deepcopy(store["plan"])

    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    try:
        all_ids = [f"c{i}" for i in range(1, 14)]
        # ================= (b-a) 처음 지시서 =================
        p0 = p1 = None
        try:
            p0 = rerun(_plan())
            b0 = p0["previz"]["shotBrief"]
            expect("b-a", [b0["status"], len(calls), sorted(b0["cuts"]) == sorted(all_ids),
                           all(len(str(e.get("pvSig") or "")) == 16 for e in b0["cuts"].values()), b0.get("changedCuts"),
                           sorted(b0["scene"])],
                   ["ready", 2, True, True, all_ids, ["loc-office", "loc-street"]],
                   "status · 장소마다 1회 · 13컷 · pvSig 16자 · changedCuts · 장소 문장 2개")
        except Exception as exc:  # noqa: BLE001
            crash("b-a", exc)

        # ================= (b-b) 안 바뀜 → LLM 0 =================
        try:
            again = rerun(p0)
            b1 = again["previz"]["shotBrief"]
            expect("b-b", [len(calls), b1["status"], dump(b1["cuts"]) == dump(p0["previz"]["shotBrief"]["cuts"]),
                           dump(b1["scene"]) == dump(p0["previz"]["shotBrief"]["scene"]), b1.get("changedCuts"),
                           b1["model"]],
                   [0, "ready", True, True, [], "fake-brief"], "LLM 호출 0 · 컷·장소 문장 그대로 · changedCuts [] · model 유지")
        except Exception as exc:  # noqa: BLE001
            crash("b-b", exc)

        # ================= (b-c) 컷 8만 고침 =================
        try:
            p1 = rerun(_edit_cut8(p0))
            old, new = p0["previz"]["shotBrief"], p1["previz"]["shotBrief"]
            same_others = all(dump(new["cuts"][c]) == dump(old["cuts"][c]) for c in all_ids if c != "c8")
            expect("b-c", [len(calls), calls[0]["loc"] if calls else "", calls[0]["cuts"] if calls else []],
                   [1, f"LOCATION: {STREET}", [f"c{i}" for i in range(7, 14)]], "거리 장소만 1회 · 그 장소 컷 전부 문맥으로")
            expect("b-c", [new["cuts"]["c8"]["shotEn"] != old["cuts"]["c8"]["shotEn"],
                           new["cuts"]["c8"]["pvSig"] != old["cuts"]["c8"]["pvSig"], same_others,
                           dump(new["scene"]) == dump(old["scene"]), new.get("changedCuts")],
                   [True, True, True, True, ["c8"]], "컷 8 항목만 교체 · 나머지 컷·장소 문장 바이트 그대로 · changedCuts [c8]")
            # 'manual' 표시만 다른 것은 바뀐 것이 아니다(카메라 값이 같으면 같은 지문).
            flag = copy.deepcopy(p1)
            flag["previz"]["scenes"][0]["timeline"][1]["camera"]["manual"] = True
            rerun(flag)
            expect("b-c", len(calls), 0, "'manual' 표시만 다름 → 호출 0")
        except Exception as exc:  # noqa: BLE001
            crash("b-c", exc)

        # ================= (b-d) 옛 지시서(pvSig 없음) =================
        try:
            legacy = copy.deepcopy(p0)
            for entry in legacy["previz"]["shotBrief"]["cuts"].values():
                entry.pop("pvSig", None)
            legacy["previz"]["shotBrief"].pop("changedCuts", None)
            p_l = rerun(legacy)
            bl = p_l["previz"]["shotBrief"]
            expect("b-d", [len(calls), all(bl["cuts"][c]["shotEn"] != legacy["previz"]["shotBrief"]["cuts"][c]["shotEn"] for c in all_ids),
                           all(bl["scene"][k] != legacy["previz"]["shotBrief"]["scene"][k] for k in bl["scene"]),
                           all(e.get("pvSig") for e in bl["cuts"].values())],
                   [2, True, True, True], "모든 장소 다시 호출 · 모든 컷·장소 문장 새로 · pvSig 생김")
        except Exception as exc:  # noqa: BLE001
            crash("b-d", exc)

        # ================= (b-e) 테이크별 지시서 stale =================
        try:
            gen = _done(gm._new_take_gen(p0))
            expect("b-e", [[u["id"], u["startCutNo"], u["endCutNo"], len(str(u.get("briefSig") or ""))] for u in gen["units"]],
                   [["take1", 1, 6, 16], ["take2", 7, 13, 16]], "테이크 나눔 · 테이크마다 briefSig")
            edited = _edit_cut8(p0)
            expect("b-e", gm.gen_stale_units(gen, edited), ["take2"], "지시서 다시 전(녹화 키만) → [take2]")
            p2 = rerun(edited)
            expect("b-e", [gm._gen_previz_stale(gen, p2), gm.gen_stale_units(gen, p2)],
                   [True, ["take2"]], "컷 8 고침 + 다시 녹화 + 지시서 다시 → stale · [take2](take1 아님)")
            est = gm.estimate(p2)
            rows = {r["id"]: r for r in est.get("takes") or []}
            quote = gm.only_changed_quote(p2, gen, est)
            expect("b-e", [quote.get("available"), quote.get("takes"), quote.get("usd")],
                   [True, ["take2"], round(float(rows["take2"]["webtoonUsd"]), 2)], "금액 = take2 몫만")
            same = copy.deepcopy(p0)
            same["previz"]["shotBrief"]["updatedAt"] = "2099-01-01T00:00:00Z"
            expect("b-e", gm.gen_stale_units(gen, same), [], "지시서 updatedAt 만 바뀜(내용 같음) → []")
            brief_only = copy.deepcopy(p0)
            brief_only["previz"]["shotBrief"]["cuts"]["c2"]["shotEn"] = "CU 85mm c2 edited"
            expect("b-e", gm.gen_stale_units(gen, brief_only), ["take1"], "take1 컷 지시서 내용만 바뀜 → [take1]")
            # 바뀐 테이크만 시작: take1 재사용(briefSig 지금 값) · take2 만 queued · 그 뒤 stale 아님.
            store["plan"] = {**copy.deepcopy(p2), "gen": gen}
            gm.check_preconditions = lambda plan, owner: []
            try:
                gm.start_generate(OWNER, PID, quote["usd"], only_changed=True)
            finally:
                gm.check_preconditions = real_pre
            new_gen = store["plan"]["gen"]
            u = {x["id"]: x for x in new_gen["units"]}
            expect("b-e", [u["take1"].get("reusedFrom") == gen["genId"], u["take2"]["webtoon"]["status"],
                           all(len(str(x.get("briefSig") or "")) == 16 for x in new_gen["units"]),
                           gm.gen_stale_units(_done(new_gen), p2)],
                   [True, "queued", True, []], "바뀐 테이크만: take1 재사용 · take2 queued · briefSig · 그 뒤 stale 아님")
        except Exception as exc:  # noqa: BLE001
            crash("b-e", exc)

        # ================= (b-f) 옛 테이크 gen(briefSig 없음) =================
        try:
            old_gen = _done(gm._new_take_gen(p0))
            for unit in old_gen["units"]:
                unit.pop("briefSig", None)
            same = copy.deepcopy(p0)
            same["previz"]["shotBrief"]["updatedAt"] = "2099-01-01T00:00:00Z"
            expect("b-f", [gm.gen_stale_units(old_gen, p0), gm.gen_stale_units(old_gen, same)], [[], ["take1", "take2"]],
                   "briefSig 없음: 지시서 그대로 → [] · updatedAt 바뀜 → 모든 테이크(205)")
            legacy_plan = copy.deepcopy(p0)
            for entry in legacy_plan["previz"]["shotBrief"]["cuts"].values():
                entry.pop("pvSig", None)
            expect("b-f", all("briefSig" not in x for x in gm._new_take_gen(legacy_plan)["units"]), True,
                   "옛 지시서(pvSig 없음)로 만든 gen 은 briefSig 를 두지 않는다(205 판정)")
            cut_gen = gm._new_previz_gen(p0)
            expect("b-f", ["briefSig" in x for x in cut_gen["units"]] == [False] * len(cut_gen["units"]), True,
                   "컷 단위 previz gen 은 그대로(briefSig 없음)")
        except Exception as exc:  # noqa: BLE001
            crash("b-f", exc)

        # ================= (b-g) 지시서 만드는 중 → 409 BriefBuilding =================
        try:
            building = copy.deepcopy(p0)
            building["previz"]["shotBrief"]["status"] = "running"
            building["previz"]["shotBrief"]["task"] = {"status": "running", "id": "tx",
                                                       "startedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            got = []
            for only in (False, True):
                store["plan"] = {**copy.deepcopy(building), "gen": _done(gm._new_take_gen(p0))}
                try:
                    gm.start_generate(OWNER, PID, 0.0, only_changed=only)
                    got.append("통과")
                except VpocError as exc:
                    got.append([exc.kind, exc.status, exc.message])
            want = ["BriefBuilding", 409, "촬영 지시서를 만드는 중입니다. 잠시 뒤 다시 눌러 주세요."]
            expect("b-g", got, [want, want], "보통 · onlyChanged 시작 → 409 BriefBuilding(한국어)")
            none = copy.deepcopy(p0)
            none["previz"]["shotBrief"] = sb._empty_brief()
            try:
                gm.check_preconditions(none, OWNER)
                got_none = "통과"
            except VpocError as exc:
                got_none = exc.kind
            expect("b-g", got_none, "BriefNotReady", "지시서 없음 → BriefNotReady 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("b-g", exc)

        def running(plan: dict, task_id: str, catch_up: int = 0) -> dict:
            data = copy.deepcopy(plan)
            brief = data["previz"].get("shotBrief") or sb._empty_brief()
            brief.update(status="running", catchUp=catch_up)
            brief["task"] = {"status": "running", "id": task_id,
                             "startedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            data["previz"]["shotBrief"] = brief
            return data

        def bump_c8() -> None:
            for scene in store["plan"]["previz"]["scenes"]:
                for entry in scene["timeline"]:
                    if entry["cutId"] == "c8":
                        entry["camera"]["lensMm"] = int(entry["camera"].get("lensMm") or 35) + 1

        def outdated() -> list:
            return sb.brief_outdated_cuts(store["plan"], owner=OWNER)

        # ================= (b-j) 작성 중 고침 → 따라잡기 1번 · 바뀐 컷만 =================
        try:
            expect("b-j", [sb.brief_outdated_cuts(p0, owner=OWNER), sb.brief_outdated_cuts(_edit_cut8(p0), owner=OWNER)],
                   [[], ["c8"]], "brief_outdated_cuts: 안 바뀜 [] · 컷 8 고침 [c8]")
            legacy = copy.deepcopy(_edit_cut8(p0))
            for entry in legacy["previz"]["shotBrief"]["cuts"].values():
                entry.pop("pvSig", None)
            expect("b-j", sb.brief_outdated_cuts(legacy, owner=OWNER), [], "pvSig 없는 옛 지시서는 세지 않는다")
            store["plan"] = running(_plan(), "tj1")
            calls.clear()
            spawned.clear()
            logs.clear()
            state = {"edited": False}

            def edit_and_approve() -> None:  # 첫 호출 중: 컷 8 고침 → 다시 승인(approve 훅 = start_brief_safe)
                if not state["edited"]:
                    state["edited"] = True
                    store["plan"] = _edit_cut8(store["plan"])
                    sb.start_brief_safe(OWNER, PID)

            on_call[:] = [edit_and_approve]
            sb.run_brief(OWNER, PID, "tj1")
            on_call.clear()
            mid = copy.deepcopy(store["plan"]["previz"]["shotBrief"])
            expect("b-j", [any("start_skipped" in m and "kind=Briefing" in m for m in logs),
                           any("start_failed" in m for m in logs)], [True, False],
                   "작성 중 승인 → start_skipped kind=Briefing 로그(AttributeError 아님)")
            expect("b-j", [len(calls), len(spawned), mid["status"], mid.get("catchUp"),
                           bool(spawned) and spawned[0][3] == mid["task"]["id"], outdated()],
                   [2, 1, "running", 1, True, ["c8"]], "끝난 뒤 다시 읽어 옛 컷 [c8] → 따라잡기 1번 시작(catchUp 1)")
            calls.clear()
            func, owner, pid, tid = spawned.pop(0)
            func(owner, pid, tid)
            fin = store["plan"]["previz"]["shotBrief"]
            same_others = all(dump(fin["cuts"][c]) == dump(mid["cuts"][c]) for c in all_ids if c != "c8")
            expect("b-j", [[c["loc"] for c in calls], fin["status"], fin.get("changedCuts"),
                           fin["cuts"]["c8"]["shotEn"] != mid["cuts"]["c8"]["shotEn"], same_others,
                           dump(fin["scene"]) == dump(mid["scene"]), outdated(), len(spawned)],
                   [[f"LOCATION: {STREET}"], "ready", ["c8"], True, True, True, [], 0],
                   "따라잡기: 거리 1회 · 컷 8만 교체 · 나머지·장소 문장 그대로 · 옛 컷 없음 · 더 안 돈다")
        except Exception as exc:  # noqa: BLE001
            crash("b-j", exc)
        finally:
            on_call.clear()

        # ================= (b-k) 따라잡기 고리 막기 =================
        try:
            ready = copy.deepcopy(_edit_cut8(p0))
            ready["previz"]["shotBrief"]["catchUp"] = 2  # 지난 승인 몫을 다 쓴 상태 — 새 시작이 되돌린다
            store["plan"] = ready
            calls.clear()
            spawned.clear()
            logs.clear()
            on_call[:] = [bump_c8]  # 지시서를 쓸 때마다 컷 8 이 또 바뀐다(끝나지 않는 고리)
            sb.start_brief(OWNER, PID)
            runs = 0
            while spawned and runs < 6:
                func, owner, pid, tid = spawned.pop(0)
                func(owner, pid, tid)
                runs += 1
            on_call.clear()
            fin = store["plan"]["previz"]["shotBrief"]
            expect("b-k", [runs, len(calls), fin["status"], fin.get("catchUp"), fin["task"].get("id"), outdated(),
                           any("catch_up_skipped" in m and "CatchUpLimit" in m for m in logs)],
                   [3, 3, "ready", 2, None, ["c8"], True],
                   "시작 1번 + 따라잡기 2번에서 멈춤(catchUp 2) · CatchUpLimit 로그")
            store["plan"] = running(store["plan"], "tk-run", catch_up=2)
            sb.start_brief_safe(OWNER, PID)
            b = store["plan"]["previz"]["shotBrief"]
            expect("b-k", [b.get("catchUp"), b["task"]["id"], len(spawned)], [0, "tk-run", 0],
                   "진행 중 새 승인 → 409 여도 catchUp 0 으로 되돌림 · 진행 중 작업 그대로 · 새로 안 띄움")
            store["plan"]["previz"]["shotBrief"].update(status="ready", catchUp=2,
                                                        task={"status": "done", "startedAt": None, "id": None})
            sb.start_brief_safe(OWNER, PID)
            b = store["plan"]["previz"]["shotBrief"]
            expect("b-k", [b.get("catchUp"), b["status"], len(spawned)], [0, "running", 1], "새 승인 → catchUp 0 · 1번 시작")
            spawned.clear()
        except Exception as exc:  # noqa: BLE001
            crash("b-k", exc)
        finally:
            on_call.clear()

        # ================= (b-l) 옛 컷 지시서로 시작 → 409 BriefBuilding · 지시서 시작 =================
        try:
            got = []
            for only in (False, True):
                store["plan"] = {**copy.deepcopy(_edit_cut8(p0)), "gen": _done(gm._new_take_gen(p0))}
                spawned.clear()
                try:
                    gm.start_generate(OWNER, PID, 0.0, only_changed=only)
                    got.append("통과")
                except VpocError as exc:
                    b = store["plan"]["previz"]["shotBrief"]
                    got.append([exc.kind, exc.status, exc.message, len(spawned), b["status"],
                                bool(spawned) and spawned[0][3] == b["task"]["id"]])
            want = ["BriefBuilding", 409, "촬영 지시서를 만드는 중입니다. 잠시 뒤 다시 눌러 주세요.", 1, "running", True]
            expect("b-l", got, [want, want], "보통 · onlyChanged: 옛 컷 → 409 BriefBuilding(한국어) · 지시서 1번 시작")
            spawned.clear()
        except Exception as exc:  # noqa: BLE001
            crash("b-l", exc)
    finally:
        for (mod, name), fn in saved.items():
            setattr(mod, name, fn)
        gm.check_preconditions = real_pre
        vlog.removeHandler(grab)
        vlog.setLevel(saved_level)

    # ================= (b-h) 확인창 CostMismatch · (b-i) node --check =================
    node = shutil.which("node")
    vsrc = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    try:
        expect("b-h", ["function costChangedText(" in vsrc, "isCostMismatch(err)) return applyFreshPrice(" in vsrc,
                       "촬영 지시서 마무리 중…" in vsrc],
               [True, True, True], "vnew.js: 공통 도우미 · 확인창 연결 · 지시서 마무리 중 글")
        if not node:
            print("[WARN] node 가 없어 작업 206 확인창 실행 검사(b-h)를 건너뜁니다")
        else:
            names = ("el", "planError", "errMessage", "castingUsdText", "takeStaleNames", "isCostMismatch",
                     "costChangedText", "rereadPlanView", "openGenerateConfirm")
            js = "\n".join(_extract_js(vsrc, n) for n in names) + _DIALOG_JS
            with tempfile.TemporaryDirectory(prefix="chk206-") as tmp:
                path = Path(tmp) / "dialog.js"
                path.write_text(js, encoding="utf-8", newline="\n")
                proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            lines = (proc.stdout or "").strip().splitlines()
            got = json.loads(lines[-1]) if proc.returncode == 0 and lines else ["ERR", (proc.stderr or "")[-300:]]
            if got and got[0] == "ERR":
                expect("b-h", got, "결과", "확인창 실행")
            else:
                a, b = got
                expect("b-h", [a["open"], a["err"], a["cost"], a["takes"], a["code"], a["okDisabled"], a["rendered"]],
                       [True, "금액이 바뀌었습니다: $11.52 → $23.44. 내용을 확인한 뒤 다시 눌러 주세요.",
                        "예상 비용 $23.44 (바뀐 테이크 몫만)",
                        "테이크 1(컷 1–6), 테이크 2(컷 7–13)만 새로 만들고, 나머지 테이크는 이전 영상을 그대로 씁니다.",
                        False, False, True],
                       "바뀐 테이크만 확인창: 창 유지 · 한국어 안내 · 금액·테이크 목록 갱신 · 코드 안 보임")
                expect("b-h", [a["second"], a["closed"]], [{"confirmUsd": 23.44, "onlyChanged": True}, True],
                       "다시 누르면 새 금액으로 보내고 닫힌다")
                expect("b-h", [b["open"], b["err"], b["code"], (b["second"] or {}).get("confirmUsd")],
                       [True, "금액이 바뀌었습니다: $5.00 → $6.25. 내용을 확인한 뒤 다시 눌러 주세요.", False, 6.25],
                       "보통 확인창도 같은 처리")
    except Exception as exc:  # noqa: BLE001
        crash("b-h", exc)
    try:
        if not node:
            print("[WARN] node 가 없어 작업 206 node --check(b-i)를 건너뜁니다")
            seen.add("b-i")
        else:
            proc = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True,
                                  encoding="utf-8", timeout=120)
            expect("b-i", [proc.returncode, (proc.stderr or "")[-300:]], [0, ""], "node --check vnew.js")
    except Exception as exc:  # noqa: BLE001
        crash("b-i", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 206 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 206 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 206 ({group}) {title}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_brief_keep_206() else 1)
