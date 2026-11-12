"""작업 231: 프리비즈 승인은 시점 일치도가 낮아도(0% 여도) 막히지 않는다.

(ng-a) 관문 끔(기본): 컷 match 0.0 · null · 0.5 → approve(force=False) 승인 · approvedForced.lowCuts 세 컷 · 지시서 자동 시작
(ng-b) 관문 끔: 모든 컷 ≥ 0.7 → 승인 · approvedForced None
(ng-c) 관문 켬: 409 LowMatch(옛 메시지 sha) · force → 승인(옛 기록 sha) · 다시 승인 통과
(ng-d) 보기: matchGate 기본 false · approveMinMatch 0.7 · lowMatchCuts 그대로
(ng-e) 화면(정적 + node 하네스, node 없으면 WARN): 관문 끔 → 버튼 켜짐 · 안내 한 줄 · 링크·확인 창 없음 / 관문 켬 → 지금 DOM
(ng-f) 돌연변이 "관문 기본 켬" → ng-a 가 잡는다
(ng-g) 고친 파일 LF · node --check vnew.js

가짜만 쓴다(메모리 STORE · 가짜 plan). 네트워크·AWS·유료 API 없음.
실행: python -X utf8 -B tools/check_previz_nogate_231.py   (고치기 전 sha: --baseline)
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "ng-a": "관문 끔: 0.0·null·0.5 컷도 승인 · approvedForced.lowCuts 세 컷",
    "ng-b": "관문 끔: 모든 컷 ≥ 0.7 → approvedForced None",
    "ng-c": "관문 켬: 409 LowMatch · force 통과(옛 sha)",
    "ng-d": "보기: matchGate false 기본 · approveMinMatch · lowMatchCuts 그대로",
    "ng-e": "화면: 관문 끔 → 버튼 켜짐·안내·링크/확인 창 없음 · 관문 켬 → 지금 DOM",
    "ng-f": "돌연변이(관문 기본 켬) → ng-a 실패",
    "ng-g": "LF · node --check vnew.js",
}
OWNER, PID = "tester", "prj_ng231"
FIXED_AT = "2026-11-12T00:00:00Z"
INFO_TEXT = "% 미만 컷: \" + infoNums +\n        \" — 그대로 진행해도 됩니다. 결과가 웹툰과 다를 수 있어요.\""
LOW_FIT = {"c1": {"match": 0.0}, "c2": {"match": None}, "c3": {"match": 0.5}}
GOOD_FIT = {"c1": {"match": 0.7}, "c2": {"match": 0.91}, "c3": {"match": 1.0}}
# 고치기 전(작업 231 전) 코드에서 --baseline 으로 뽑은 sha. 바꾸면 안 된다.
OLD_SHA = {
    "err": "ffa8288924d35ac2",
    "forced": "05564394e67605ee",
}
CHANGED = ("app/src/vnew/settings.py", "app/src/vnew/previz.py", "frontend/public/vnew.js",
           "tools/check_vpoc.py", "tools/check_previz_nogate_231.py")


class _Store:
    """메모리 저장소(조건부 쓰기 흉내)."""

    def __init__(self) -> None:
        self.json: dict = {}
        self.n = 0

    def read_json(self, key: str):
        item = self.json.get(key)
        return (copy.deepcopy(item[0]), item[1]) if item else (None, "")

    def write_json(self, key: str, data: dict, *, etag: str = "", create_only: bool = False) -> str:
        from src.vpoc.errors import VpocError  # noqa: PLC0415

        current = self.json.get(key)
        if (create_only and current) or (etag and (not current or current[1] != etag)):
            raise VpocError("fake.store", "PreconditionFailed", "etag", 412)
        self.n += 1
        self.json[key] = (copy.deepcopy(data), f"e{self.n}")
        return f"e{self.n}"

    def presign(self, key: str, **_kw) -> str:
        return "https://fake/" + key


class _Env:
    """plan·previz STORE · now_iso · 지시서 시작 · 관문 설정을 바꿔 끼우고 되돌린다."""

    def __init__(self, gate: bool | None = None) -> None:
        from src.vnew import plan as planmod  # noqa: PLC0415
        from src.vnew import previz as pvmod  # noqa: PLC0415
        from src.vnew import shot_brief as briefmod  # noqa: PLC0415

        self.planmod, self.pvmod, self.briefmod = planmod, pvmod, briefmod
        self.gate = gate
        self.store = _Store()
        self.briefs: list[tuple[str, str]] = []

    def __enter__(self):
        m = self
        self.orig = (m.planmod.STORE, m.pvmod.STORE, m.pvmod.now_iso, m.briefmod.start_brief_safe,
                     m.pvmod.vnew_settings.VNEW_PREVIZ_MATCH_GATE)
        m.planmod.STORE = m.store
        m.pvmod.STORE = m.store
        m.pvmod.now_iso = lambda: FIXED_AT
        m.briefmod.start_brief_safe = lambda o, p: m.briefs.append((o, p))
        if self.gate is not None:
            m.pvmod.vnew_settings.VNEW_PREVIZ_MATCH_GATE = self.gate
        return self

    def __exit__(self, *_exc) -> None:
        m = self
        (m.planmod.STORE, m.pvmod.STORE, m.pvmod.now_iso, m.briefmod.start_brief_safe,
         m.pvmod.vnew_settings.VNEW_PREVIZ_MATCH_GATE) = self.orig

    def seed(self, fit: dict, **previz_extra) -> dict:
        plan = self.planmod.new_plan()
        previz = self.pvmod.new_previz()
        previz.update({"status": "ready", "fit": copy.deepcopy(fit), "scenes": [{
            "locationId": "L1", "room": {},
            "timeline": [{"cutId": "c1", "actors": []}, {"cutId": "c2", "actors": []}, {"cutId": "c3", "actors": []}],
        }]}, **previz_extra)
        plan["previz"] = previz
        self.store.json[self.planmod.plan_key(OWNER, PID)] = (plan, "e0")
        return plan

    def saved(self) -> dict:
        return self.store.read_json(self.planmod.plan_key(OWNER, PID))[0] or {}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _err_blob(exc) -> list:
    return [exc.where, exc.kind, exc.message, exc.status]


def _ng_a_problems(gate: bool | None = None) -> list[str]:
    """관문 기본값으로 낮은 컷(0.0·null·0.5) 승인. 문제 목록(비면 통과). gate 를 주면 그 값으로 돌린다(ng-f)."""
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    problems: list[str] = []
    with _Env(gate) as env:
        env.seed(LOW_FIT)
        try:
            pv = env.pvmod.approve(OWNER, PID, force=False)
        except VpocError as exc:
            return [f"approve(force=False) 가 {exc.status} {exc.kind} 로 막혔습니다: {exc.message}"]
        saved = (env.saved().get("previz") or {})
        forced = pv.get("approvedForced") or {}
        got = [pv.get("approved"), saved.get("approved"), [c.get("cutId") for c in forced.get("lowCuts") or []],
               forced.get("lowCuts"), forced.get("at"), saved.get("approvedForced") == pv.get("approvedForced"),
               env.briefs]
        want = [True, True, ["c1", "c2", "c3"],
                [{"cutId": "c1", "no": 1, "match": 0.0}, {"cutId": "c2", "no": 2, "match": None},
                 {"cutId": "c3", "no": 3, "match": 0.5}], FIXED_AT, True, [(OWNER, PID)]]
        if got != want:
            problems.append(f"결과 {got}\n      기대 {want}")
    return problems


def _forced_run() -> dict:
    """관문 켬: 409 · force 승인 · 다시 승인(force 없이). sha 원본."""
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    out: dict = {}
    with _Env(True) as env:
        env.seed(LOW_FIT)
        try:
            env.pvmod.approve(OWNER, PID, force=False)
            out["err"] = None
        except VpocError as exc:
            out["err"] = _err_blob(exc)
        out["afterErr"] = [(env.saved().get("previz") or {}).get("approved"), list(env.briefs)]
        pv = env.pvmod.approve(OWNER, PID, force=True)
        out["forced"] = {"approved": pv.get("approved"), "approvedForced": pv.get("approvedForced")}
        pv2 = env.pvmod.approve(OWNER, PID, force=False)  # 이미 강제 승인 → 다시 승인 통과
        out["again"] = [pv2.get("approved"), pv2.get("approvedForced") == pv.get("approvedForced"), len(env.briefs)]
    with _Env(True) as env:
        env.seed(GOOD_FIT)
        pv = env.pvmod.approve(OWNER, PID, force=False)
        out["good"] = [pv.get("approved"), pv.get("approvedForced")]
    return out


def _baseline() -> None:
    r = _forced_run()
    print(f"err: {_sha(r['err'])} {r['err']}")
    print(f"forced: {_sha(r['forced'])} {r['forced']}")
    print(f"afterErr={r['afterErr']} again={r['again']} good={r['good']}")


_RUNNER = r"""
const fs = await import("node:fs");
class FakeEl {
  constructor(tag) { this.tagName = tag; this.className = ""; this.textContent = ""; this.children = [];
    this.disabled = false; this.type = ""; this.listeners = {}; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); }
  click() { (this.listeners.click || []).forEach((f) => f({})); }
}
globalThis.document = { createElement: (t) => new FakeEl(t) };
function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function all(n) { return [n].concat(...n.children.map(all)); }
const BLOCK = fs.readFileSync(process.argv[2], "utf-8");
const make = new Function("el", "ctx", `
  var host = ctx.host, previz = ctx.previz, previzRecording = ctx.previzRecording, segments = [];
  var state = { plan: { gen: { estimate: { totalUsd: 1.5 } } } };
  var PREVIZ_STALE_TEXT = "stale";
  var window = { confirm: function (m) { ctx.confirms.push(m); return true; } };
  function showError() {}
  function previzApproveAndRecord(force, all) { ctx.calls.push([force, all]); }
  ${BLOCK}
  return btn;
`);
const out = {};
const cases = {
  offLow: { matchGate: false, approved: false, low: true },
  offLowBusy: { matchGate: false, approved: false, low: true, busy: true },
  offNone: { matchGate: false, approved: false, low: false },
  offMissing: { approved: false, low: true },
  onLow: { matchGate: true, approved: false, low: true },
};
for (const [name, c] of Object.entries(cases)) {
  const ctx = { host: new FakeEl("div"), confirms: [], calls: [],
    previzRecording: c.busy ? { aborted: false, done: 0, total: 2 } : null,
    previz: { approved: c.approved, approveMinMatch: 0.7, staleSegments: [], approvedForced: null,
      lowMatchCuts: c.low ? [{ cutId: "c12", no: 12, match: 0 }, { cutId: "c13", no: 13, match: null }] : [] } };
  if ("matchGate" in c) ctx.previz.matchGate = c.matchGate;
  const btn = make(el, ctx);
  const nodes = all(ctx.host);
  const r = { disabled: btn.disabled, label: btn.textContent, classes: nodes.map((n) => n.className).filter(Boolean),
    texts: nodes.map((n) => n.textContent).filter(Boolean) };
  btn.click();
  r.afterClick = { calls: ctx.calls.slice(), confirms: ctx.confirms.length };
  const force = nodes.find((n) => n.className.indexOf("vnew-previz-force") >= 0);
  if (force) { force.click(); r.afterForce = { calls: ctx.calls.slice(), confirms: ctx.confirms.length }; }
  out[name] = r;
}
console.log(JSON.stringify(out));
"""

BLOCK_START = "    // 승인하고 녹화 버튼\n"
BLOCK_END = "    host.appendChild(btn);\n"


def _approve_block(vjs: str) -> str:
    i = vjs.index(BLOCK_START)
    j = vjs.index(BLOCK_END, i) + len(BLOCK_END)
    if vjs.count(BLOCK_START) != 1:
        raise AssertionError("승인 버튼 블록 시작 표시가 하나가 아닙니다")
    return vjs[i:j]


def _run_node(node: str, block: str) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "block.js").write_text(block, encoding="utf-8")
        (work / "run.mjs").write_text(_RUNNER, encoding="utf-8")
        try:
            proc = subprocess.run([node, str(work / "run.mjs"), str(work / "block.js")], capture_output=True,
                                  text=True, encoding="utf-8", timeout=60)
        except subprocess.TimeoutExpired:
            return {}, "node 검사가 60초 안에 끝나지 않았습니다"
    if proc.returncode != 0:
        return {}, f"node 실패: {(proc.stderr or '').strip()[-300:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1]), ""


def check_previz_nogate_231() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-800:])

    node = shutil.which("node")
    warns: list[str] = []

    try:
        from src.vnew import settings as vs  # noqa: PLC0415

        expect("ng-a", [vs.VNEW_PREVIZ_MATCH_GATE, vs.VNEW_PREVIZ_APPROVE_MIN], [False, 0.7], "설정 기본값")
        expect("ng-a", _ng_a_problems(), [], "관문 끔 + 0.0·null·0.5 → approve(force=False) 승인·기록")
    except Exception as exc:  # noqa: BLE001
        crash("ng-a", exc)

    try:
        with _Env() as env:
            env.seed(GOOD_FIT)
            pv = env.pvmod.approve(OWNER, PID, force=False)
            saved = env.saved().get("previz") or {}
            expect("ng-b", [pv.get("approved"), pv.get("approvedForced"), saved.get("approvedForced"), len(env.briefs)],
                   [True, None, None, 1], "모든 컷 ≥ 0.7 → 승인 · approvedForced None · 지시서 시작")
            # 앞서 기록된 approvedForced 도 지운다(옛 동작과 같다).
            env.seed(GOOD_FIT, approvedForced={"at": "x", "lowCuts": []})
            expect("ng-b", env.pvmod.approve(OWNER, PID).get("approvedForced"), None, "좋은 컷 재승인 → 기록 지움")
    except Exception as exc:  # noqa: BLE001
        crash("ng-b", exc)

    try:
        r = _forced_run()
        msg = "시점 일치도 70% 미만 컷이 있습니다: 컷 1(0%), 컷 2(판정 불가), 컷 3(50%)"
        expect("ng-c", r["err"], ["vnew.previz.approve", "LowMatch", msg, 409], "관문 켬 → 409 LowMatch")
        expect("ng-c", [_sha(r["err"]), _sha(r["forced"])], [OLD_SHA["err"], OLD_SHA["forced"]], "옛 메시지·기록 sha")
        expect("ng-c", [r["afterErr"], r["again"], r["good"]], [[False, []], [True, True, 2], [True, None]],
               "409 는 저장 안 함·지시서 없음 · 다시 승인 통과 · 좋은 컷 None")
    except Exception as exc:  # noqa: BLE001
        crash("ng-c", exc)

    try:
        for gate in (None, True):
            with _Env(gate) as env:
                plan = env.seed(LOW_FIT)
                view = env.pvmod.previz_view(OWNER, PID, plan)
                want_low = [{"cutId": "c1", "match": 0.0, "no": 1}, {"cutId": "c2", "match": None, "no": 2},
                            {"cutId": "c3", "match": 0.5, "no": 3}]
                expect("ng-d", [view.get("matchGate"), view.get("approveMinMatch"), view.get("lowMatchCuts")],
                       [bool(gate), 0.7, want_low], f"보기(관문 {gate})")
        src = (ROOT / "app/src/vnew/previz.py").read_text(encoding="utf-8")
        expect("ng-d", ['"matchGate": bool(vnew_settings.VNEW_PREVIZ_MATCH_GATE)' in src,
                        "min_match=APPROVE_MIN_MATCH" in src, "APPROVE_MIN_MATCH = vnew_settings.VNEW_PREVIZ_APPROVE_MIN" in src],
               [True, True, True], "보기 키 · 카메라 자동 보정은 하한 그대로")
    except Exception as exc:  # noqa: BLE001
        crash("ng-d", exc)

    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    try:
        block = _approve_block(vjs)
        expect("ng-e", [INFO_TEXT in block, "if (lowCuts.length && !alreadyApproved && previz.matchGate) {" in block,
                        block.count("window.confirm("), block.count('"link-button vnew-previz-force"'),
                        vjs.count("previzApproveAndRecord(!!previz.approvedForced, false, showError"),
                        "doApprove(!!(alreadyApproved && previz.approvedForced));" in block],
               [True, True, 1, 1, 2, True], "정적: 안내 문구 · 관문 조건 · 링크·확인 창은 관문 켬 쪽 하나 · 호출 모양 그대로")
        if not node:
            warns.append("[WARN] node 가 없어 작업 231 화면 하네스(ng-e)를 건너뜁니다")
        else:
            got, err = _run_node(node, block)
            expect("ng-e", err, "", "node 하네스")
            if got:
                info = "시점 일치도 70% 미만 컷: 컷 12, 컷 13 — 그대로 진행해도 됩니다. 결과가 웹툰과 다를 수 있어요."
                for name in ("offLow", "offMissing"):
                    r = got[name]
                    expect("ng-e", [r["disabled"], r["label"], info in r["texts"], "vnew-hint vnew-previz-lowmatch-info" in r["classes"],
                                    any("vnew-previz-force" in c for c in r["classes"]), "vnew-previz-lowmatch" in r["classes"],
                                    r["afterClick"], "afterForce" in r],
                           [False, "승인하고 녹화", True, True, False, False, {"calls": [[False, False]], "confirms": 0}, False],
                           f"관문 끔({name}) + 낮은 컷 → 버튼 켜짐 · 안내 · 링크·확인 창 없음 · force 없이 승인")
                r = got["offLowBusy"]
                expect("ng-e", [r["disabled"], info in r["texts"]], [True, True], "관문 끔 + 녹화 중 → 버튼 꺼짐")
                r = got["offNone"]
                expect("ng-e", [r["disabled"], [t for t in r["texts"] if "일치도" in t]], [False, []], "낮은 컷 없음 → 안내 없음")
                r = got["onLow"]
                old_msg = "시점 일치도 70% 미만 컷: 컷 12, 컷 13 — 다시 만들기 지시 또는 카메라를 직접 조정하세요"
                expect("ng-e", [r["disabled"], old_msg in r["texts"], "그래도 승인" in r["texts"], "vnew-previz-lowmatch" in r["classes"],
                                "vnew-previz-lowmatch-info" in " ".join(r["classes"]), r.get("afterForce")],
                       [True, True, True, True, False, {"calls": [[False, False], [True, False]], "confirms": 1}],
                       "관문 켬 → 지금 DOM(버튼 꺼짐 · 경고 · [그래도 승인] → 확인 창 → force)")
    except Exception as exc:  # noqa: BLE001
        crash("ng-e", exc)

    try:
        caught = _ng_a_problems(gate=True)
        expect("ng-f", [bool(caught), "LowMatch" in " ".join(caught)], [True, True], "돌연변이: 관문 기본 켬 → ng-a 실패")
    except Exception as exc:  # noqa: BLE001
        crash("ng-f", exc)

    try:
        crlf = [p for p in CHANGED if b"\r\n" in (ROOT / p).read_bytes()]
        expect("ng-g", crlf, [], "고친 파일 LF")
        if not node:
            warns.append("[WARN] node 가 없어 작업 231 node --check(ng-g)를 건너뜁니다")
        else:
            proc = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True,
                                  encoding="utf-8", timeout=120)
            expect("ng-g", [proc.returncode, (proc.stderr or "")[-300:]], [0, ""], "node --check vnew.js")
    except Exception as exc:  # noqa: BLE001
        crash("ng-g", exc)

    for w in warns:
        print(w)
    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 231 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 231 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 231 ({group}) {title}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    _baseline()
    sys.exit(0)

if __name__ == "__main__":
    ok = check_previz_nogate_231()
    print('hook (check_vpoc.py main, 작업 230 다음 줄): if not __import__("check_previz_nogate_231").check_previz_nogate_231(): '
          'fail("작업 231: 프리비즈 일치도 관문 끔(0%도 승인) · 안내만 검사 실패")')
    sys.exit(0 if ok else 1)
