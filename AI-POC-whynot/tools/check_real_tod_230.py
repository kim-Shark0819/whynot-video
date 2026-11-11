"""작업 230 검사 — (A) 실사 시간대가 부정된 밤 낱말로 밤이 되지 않는다 · (B) 죽은 Seedance 를 실사 likeness 사다리에서 건너뛴다.
가짜만(네트워크 0).

  (td-a) 낮: test4 장소 빛 줄("… it is daytime, never evening or night.") · "no dusk, no night" · "not at night" · "밤이 아닌 낮"
  (td-b) 밤: "Night, street lamps" · "a dark night sky over the city" · timeKo 밤
  (td-c) 우선순위: timeKo 낮 > 장소 lightEn "night scene" · timeKo 빈 값 + 콘티 setting "night" → 밤
  (td-d) 160 · 161 검사(옛 fixture) 그대로 통과
  (td-e) 변이 "밤 낱말 하나라도 있으면 밤" 을 td-a 가 잡는다
  (ls-a) 실사 테이크 조각 1 · 2 가 Seedance likeness 거절 뒤 Kling → 조각 3 · 4 는 Seedance 0번, 바로 Kling · seedanceSkipped · 노트
  (ls-b) 사이에 Seedance 성공이 있으면 셈이 0 으로(조각 4 는 Seedance 로 낸다)
  (ls-c) VNEW_REAL_LIKENESS_SKIP_AFTER = 0 → 지금 사다리(호출 순서 sha = 고치기 전)
  (ls-d) 애니 테이크는 그대로(호출 순서 sha = 고치기 전)
  (ls-e) 변이 "첫 조각부터 건너뜀" 을 ls-a 가 잡는다
  (ls-f) 바꾼 파일 LF 줄 끝

실행: python -X utf8 -B tools/check_real_tod_230.py   (고치기 전 sha: --baseline)
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import hashlib
import json
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import check_anime_kling_229 as ak  # noqa: E402

HOOK = ('    if not __import__("check_real_tod_230").check_real_tod_230(): '
        'fail("작업 230: 실사 시간대 부정 낱말 · 우선순위 · 죽은 Seedance 건너뛰기 검사 실패")')
GROUPS = {
    "td-a": "부정된 밤 낱말 → 낮(test4 빛 줄 · no dusk, no night · not at night · 밤이 아닌 낮)",
    "td-b": "진짜 밤 낱말 → 밤(Night, street lamps · dark night sky · timeKo 밤)",
    "td-c": "우선순위: timeKo > 콘티 setting > 컷 > 장소 빛",
    "td-d": "160 · 161 옛 fixture 그대로",
    "td-e": "변이(밤 낱말 하나라도 → 밤)를 td-a 가 잡음",
    "ls-a": "조각 1 · 2 Kling 뒤 조각 3 · 4 는 바로 Kling(Seedance 0) · seedanceSkipped · 노트",
    "ls-b": "사이의 Seedance 성공이 셈을 0 으로",
    "ls-c": "설정 0 → 지금 사다리(sha 고치기 전)",
    "ls-d": "애니 테이크 그대로(sha 고치기 전)",
    "ls-e": "변이(첫 조각부터 건너뜀)를 ls-a 가 잡음",
    "ls-f": "LF 줄 끝",
}
CHANGED = ("app/src/vnew/generate.py", "tools/check_real_tod_230.py")
SKIP_NAME = "VNEW_REAL_LIKENESS_SKIP_AFTER"
TEST4_LIGHT = ("Bright, soft daytime light from a lightly overcast pale-blue sky \u2026; it is daytime, never evening or "
               "night.")
# 고치기 전(작업 230 전) 코드에서 --baseline 으로 뽑은 호출 순서 sha. 바꾸면 안 된다.
BASE_SHA = {
    "ls-c": "d2a15862bfeb6cee4cc8fa59687e319359fad61f537910e232c80989ab257283",  # 조각마다 Seedance 5번 → Kling(8컷)
    "ls-d": "2aab457873d87ae33dc0a555a1e21b14905aa025602e1ee9b4506e808d4fa83f",
}
_CACHE: dict = {}


# ---------------------------------------------------------------- 시간대(td)
def _plan(time_ko: str = "", conti_setting: dict | None = None, cuts: list[dict] | None = None,
          lights: list[str] | None = None) -> dict:
    return {"script": {"setting": {"timeKo": time_ko}},
            "conti": {"setting": dict(conti_setting or {}), "cuts": list(cuts or [])},
            "locations": [{"no": n + 1, "design": {"lightEn": text}} for n, text in enumerate(lights or [])]}


def _tod_cases(want: str) -> list[tuple[str, dict]]:
    if want == "day":
        return [("test4 장소 빛 줄", _plan(lights=[TEST4_LIGHT])),
                ("no dusk, no night(컷 lightEn)", _plan(cuts=[{"id": "c1", "lightEn": "no dusk, no night"}])),
                ("not at night(콘티 setting)", _plan(conti_setting={"lightEn": "Bright street, not at night"})),
                ("밤이 아닌 낮(timeKo)", _plan(time_ko="밤이 아닌 낮")),
                ("밤이 아닌 낮(컷 timeKo)", _plan(cuts=[{"id": "c1", "timeKo": "밤이 아닌 낮"}])),
                ("without night sky", _plan(lights=["Open street without night sky"])),
                ("226 낮 지킴 글", _plan(lights=["bright daytime sky, no night, no dusk"]))]
    return [("Night, street lamps(컷 lightEn)", _plan(cuts=[{"id": "c1", "lightEn": "Night, street lamps"}])),
            ("a dark night sky over the city(장소 빛)", _plan(lights=["a dark night sky over the city"])),
            ("timeKo 밤", _plan(time_ko="밤"))]


def group_td_a() -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    return [f"{name}: {genmod._real_tod(plan)!r} != 'day'" for name, plan in _tod_cases("day")  # noqa: SLF001
            if genmod._real_tod(plan) != "day"]  # noqa: SLF001


def group_td_b() -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    return [f"{name}: {genmod._real_tod(plan)!r} != 'night'" for name, plan in _tod_cases("night")  # noqa: SLF001
            if genmod._real_tod(plan) != "night"]  # noqa: SLF001


def group_td_c() -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    cases = [("timeKo 낮 + 장소 night scene", _plan(time_ko="낮", lights=["night scene"]), "day"),
             ("timeKo 빈 값 + 콘티 setting night", _plan(conti_setting={"timeEn": "night"}), "night"),
             ("timeKo 밤 + 장소 daylight", _plan(time_ko="밤", lights=["daylight"]), "night"),
             ("콘티 setting daytime + 컷 night", _plan(conti_setting={"lightEn": "daytime"},
                                                     cuts=[{"id": "c1", "lightEn": "night"}]), "day"),
             ("컷 night + 장소 daylight", _plan(cuts=[{"id": "c1", "lightEn": "night"}], lights=["daylight"]), "night"),
             ("같은 층 낮 + 진짜 밤 → 낮", _plan(cuts=[{"id": "c1", "lightEn": "daylight"}, {"id": "c2", "lightEn": "night"}]),
              "day"),
             ("timeKo 새벽 → 밤", _plan(time_ko="새벽"), "night"),
             ("timeKo 오후 → 낮", _plan(time_ko="오후", cuts=[{"id": "c1", "lightEn": "night"}]), "day"),
             ("빈 plan → 낮", {}, "day")]
    bad = [f"{name}: {genmod._real_tod(plan)!r} != {want!r}" for name, plan, want in cases  # noqa: SLF001
           if genmod._real_tod(plan) != want]  # noqa: SLF001
    for key in ("day", "night"):
        if genmod.REAL_TIME_OF_DAY[key] != {"day": "Bright daytime, blue sky, no dusk, no night, no sunset light.",
                                            "night": "Night, dark night sky, no daylight, no dusk, no sunset light."}[key]:
            bad.append(f"REAL_TIME_OF_DAY[{key}] 글이 바뀌었습니다")
    return bad


def group_td_d() -> list[str]:
    import check_vpoc as cv  # noqa: PLC0415

    bad: list[str] = []
    for name in ("check_vnew_real_costume_160", "check_vnew_real_daylight_161"):
        before = len(cv.failures)
        try:
            getattr(cv, name)()
        except Exception:  # noqa: BLE001
            bad.append(f"{name} 예외:\n" + traceback.format_exc()[-600:])
        bad += [f"{name}: {m[:300]}" for m in cv.failures[before:]]
        del cv.failures[before:]
    return bad


def _old_real_tod(plan: dict) -> str:
    """작업 230 전 _real_tod(변이용): 밤 낱말이 하나라도 있으면 밤."""
    from src.vnew import generate as genmod  # noqa: PLC0415

    conti = plan.get("conti") or {}
    setting = (plan.get("script") or {}).get("setting") or {}
    texts = [str(v) for v in (conti.get("setting") or {}).values() if isinstance(v, str)] if isinstance(
        conti.get("setting"), dict) else []
    texts += [str(setting.get("timeKo") or "")]
    for cut in conti.get("cuts") or []:
        texts += [str(cut.get(k) or "") for k in ("lightEn", "locationEn", "timeKo")]
    for loc in plan.get("locations") or []:
        texts.append(str((loc.get("design") or {}).get("lightEn") or ""))
    return "night" if any(genmod._NIGHT_WORDS.search(t) for t in texts if t) else "day"  # noqa: SLF001


def group_td_e(caught: list[str]) -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    real = genmod._real_tod  # noqa: SLF001
    genmod._real_tod = _old_real_tod  # noqa: SLF001
    try:
        found = group_td_a()
    finally:
        genmod._real_tod = real  # noqa: SLF001
    if found:
        caught.append("anyNight")
        return []
    return ["변이(밤 낱말 하나라도 → 밤)를 td-a 가 잡지 못했습니다"]


# ---------------------------------------------------------------- 건너뛰기(ls)
def _all_seed(req: dict):
    """실사 Seedance 요청은 모두 likeness 거절(Kling 은 받는다)."""
    return ak.LIKENESS_RAW if ak._is(req, ak._seed()) and ak._real_req(req) else None  # noqa: SLF001


def _seed_but_4(req: dict):
    """실사 Seedance 조각 2(4초~)만 받고 나머지는 likeness 거절."""
    return None if ak._start_of(req) == 4.0 else _all_seed(req)  # noqa: SLF001


def _real_run(rule, *, skip: int | None = None, pid: str = "prj_ls") -> dict:
    """애니(Seedance) 테이크 4조각 끝 → 실사 회차(Seedance) · rule 로 거절. 실사 호출 · 조각 · 회차 · 노트 · sha."""
    pins = {}
    with ak._env("u230", VNEW_REAL_TAKE_MODEL=ak._seed()) as (env, client, genmod, policy):  # noqa: SLF001
        if skip is not None and hasattr(genmod, SKIP_NAME):
            env.set(genmod, SKIP_NAME, skip)
        split = genmod._take_split_limit  # noqa: SLF001
        # 실사 조각 상한 4초(가짜 Seedance maxRefVideoS 는 12초라 조각 2개뿐) → 실사 4조각(c1·c2 | c3·c4 | c5·c6 | c7·c8)
        env.set(genmod, "_take_split_limit", lambda endpoint, real, anime="": 4.0 if real else split(endpoint, real, anime))
        policy["rule"] = lambda req: None
        ak._make(env, genmod, pid)  # noqa: SLF001 — 애니 = Wan, 실사 = Seedance(실사 조각 상한 = Seedance maxRefVideoS)
        g0 = ak._go(env, client, genmod, pid)  # noqa: SLF001
        if ak._web(genmod, g0).get("status") != "completed":  # noqa: SLF001
            raise AssertionError(f"{pid}: 애니 테이크가 끝나지 않았습니다 {ak._web(genmod, g0).get('status')}")  # noqa: SLF001
        policy["rule"] = rule
        since, led0 = len(env.requests), len(env.ledger)
        resp = client.post(f"/api/vnew/p/{pid}/generate/real", json={"confirmUsd": genmod.real_estimate(g0)["usd"]})
        g = env.drive(pid)
        policy["rule"] = lambda req: None
        reqs = env.requests[since:]
        real = genmod._previz_cells(g)[0].get("real") or {}  # noqa: SLF001
        notes = [n for n in genmod.vnew_model_notes.read_notes(env.owner, pid, str(g.get("genId") or ""))
                 if n.get("variant") == "real"]
        calls = [(ak._names([r])[0], ak._start_of(r), ak._real_req(r),  # noqa: SLF001
                  hashlib.sha256(ak._prompt(r).encode("utf-8")).hexdigest()[:16],  # noqa: SLF001
                  len((r.get("payload") or {}).get("image_urls") or [])) for r in reqs]
        blob = {"status": resp.status_code, "calls": calls,
                "ledger": [(c.get("kind"), c.get("model"), round(float(c.get("usd") or 0), 4)) for c in env.ledger[led0:]],
                "cell": {k: real.get(k) for k in ("status", "error", "failureKind", "attempt")},
                "parts": [(p.get("status"), p.get("kind"), p.get("cutIds"), p.get("likenessStep"), p.get("replacedBy"))
                          for p in real.get("parts") or []],
                "final": (g.get("finalReal") or {}).get("status")}
        sha = hashlib.sha256(json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return {"blob": blob, "sha": sha, "run": dict(g.get("realRun") or {}), "notes": notes, "pins": pins,
                "seed_at": {s: sum(1 for c in calls if c[0] == "seedance" and c[2] and c[1] == s)
                            for s in (0.0, 4.0, 8.0, 12.0)},
                "kling": sum(1 for c in calls if c[0] == "kling"), "cell": real.get("status"),
                "final": (g.get("finalReal") or {}).get("status")}


def _anime_run() -> tuple[str, dict]:
    """229 ak-a 애니 흐름(Wan 콘텐츠 → Seedance likeness → Kling 컷) + 애니 Seedance 테이크 likeness(228 Wan)."""
    out: dict = {}
    with ak._env("u230a") as (env, client, genmod, policy):  # noqa: SLF001
        r = ak._run(env, client, genmod, policy, "prj_ls_d", ak._rule_a)  # noqa: SLF001
        out["a"] = {"calls": [(ak._names([q])[0], ak._start_of(q)) for q in r["reqs"]],  # noqa: SLF001
                    "parts": ak._shape(r["parts"]), "status": r["web"].get("status"),  # noqa: SLF001
                    "ledger": [(c.get("kind"), c.get("model"), round(float(c.get("usd") or 0), 4)) for c in r["ledger"]]}
        rule = lambda req: ak.LIKENESS_RAW if ak._is(req, ak._seed()) else None  # noqa: E731, SLF001
        r = ak._run(env, client, genmod, policy, "prj_ls_d2", rule, model=ak._seed())  # noqa: SLF001
        out["b"] = {"calls": [(ak._names([q])[0], ak._start_of(q)) for q in r["reqs"]],  # noqa: SLF001
                    "parts": ak._shape(r["parts"]), "status": r["web"].get("status")}  # noqa: SLF001
    return hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(), out


def _baseline() -> None:
    r = _real_run(_all_seed, skip=0, pid="prj_ls_c")
    print(f"ls-c: {r['sha']} seed_at={r['seed_at']} kling={r['kling']} cell={r['cell']} final={r['final']}")
    sha, out = _anime_run()
    print(f"ls-d: {sha} a={out['a']['status']} b={out['b']['status']} calls={len(out['a']['calls'])}+{len(out['b']['calls'])}")


if __name__ == "__main__" and "--baseline" in sys.argv:
    _baseline()
    sys.exit(0)


def _ls_a_run() -> dict:
    if "a" not in _CACHE:
        _CACHE["a"] = _real_run(_all_seed, pid="prj_ls_a")
    return _CACHE["a"]


def _ls_a_bad(r: dict) -> list[str]:
    bad: list[str] = []
    seed = r["seed_at"]
    if not (seed[0.0] >= 2 and seed[4.0] >= 2):
        bad.append(f"조각 1 · 2 가 Seedance 사다리를 거치지 않았습니다: {seed}")
    if seed[8.0] or seed[12.0]:
        bad.append(f"조각 3 · 4 에 Seedance 요청이 있습니다(건너뛰어야 함): {seed}")
    if r["kling"] != 8:
        bad.append(f"Kling 컷 요청이 8번(c1~c8)이 아닙니다: {r['kling']}")
    if r["cell"] != "completed" or r["final"] != "done":
        bad.append(f"실사 테이크 · 최종이 끝나지 않았습니다: {r['cell']} {r['final']}")
    skipped = r["run"].get("seedanceSkipped") or {}
    if not isinstance(skipped, dict) or skipped.get("after") != 2 or not skipped.get("at"):
        bad.append(f"realRun.seedanceSkipped 가 {{at, after: 2}} 가 아닙니다: {skipped!r}")
    steps = [p[3] for p in r["blob"]["parts"] if p[4] == "kling-cuts"]
    if len(steps) != 4:
        bad.append(f"Kling 으로 바뀐 조각이 4개가 아닙니다: {r['blob']['parts']}")
    notes = [n for n in r["notes"] if "skip seedance" in str(n.get("providerMessage") or "").lower()]
    if len(notes) != 1 or not str(notes[0].get("action") or "").startswith("fallbackModel:"):
        bad.append(f"건너뛰기 모델 노트가 1개(fallbackModel:Kling)가 아닙니다: {[(n.get('action'), n.get('providerMessage')) for n in r['notes']][-4:]}")
    return bad


def group_ls_a() -> list[str]:
    return _ls_a_bad(_ls_a_run())


def group_ls_b() -> list[str]:
    r = _real_run(_seed_but_4, pid="prj_ls_b")
    bad: list[str] = []
    if r["seed_at"][4.0] != 1 or r["seed_at"][8.0] < 2 or r["seed_at"][12.0] < 1:
        bad.append(f"사이의 성공 뒤에도 셈이 남았습니다(조각 4 는 Seedance 로 내야 함): {r['seed_at']}")
    if r["cell"] != "completed":
        bad.append(f"실사 테이크가 끝나지 않았습니다: {r['cell']}")
    return bad


def group_ls_c() -> list[str]:
    r = _real_run(_all_seed, skip=0, pid="prj_ls_c")
    bad = [] if r["sha"] == BASE_SHA["ls-c"] else [
        f"설정 0 호출 순서 sha 가 바뀌었습니다: {r['sha']} != {BASE_SHA['ls-c']} seed_at={r['seed_at']}"]
    if r["run"].get("seedanceSkipped"):
        bad.append(f"설정 0 인데 seedanceSkipped 가 있습니다: {r['run'].get('seedanceSkipped')}")
    return bad


def group_ls_d() -> list[str]:
    sha, out = _anime_run()
    return [] if sha == BASE_SHA["ls-d"] else [
        f"애니 테이크 호출 순서 sha 가 바뀌었습니다: {sha} != {BASE_SHA['ls-d']} {json.dumps(out, default=str)[:300]}"]


def group_ls_e(caught: list[str]) -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    real = genmod._real_likeness_skip  # noqa: SLF001
    genmod._real_likeness_skip = lambda gen: int(genmod.VNEW_REAL_LIKENESS_SKIP_AFTER) > 0  # noqa: SLF001
    try:
        found = _ls_a_bad(_real_run(_all_seed, pid="prj_ls_e"))
    except Exception as exc:  # noqa: BLE001
        found = [f"예외 {type(exc).__name__}"]
    finally:
        genmod._real_likeness_skip = real  # noqa: SLF001
    if found:
        caught.append("skipFirst")
        return []
    return ["변이(첫 조각부터 건너뜀)를 ls-a 가 잡지 못했습니다"]


def group_ls_f() -> list[str]:
    return [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]


def check_real_tod_230() -> bool:
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    for group, fn in (("td-a", group_td_a), ("td-b", group_td_b), ("td-c", group_td_c), ("td-d", group_td_d),
                      ("td-e", lambda: group_td_e(caught)), ("ls-a", group_ls_a), ("ls-b", group_ls_b),
                      ("ls-c", group_ls_c), ("ls-d", group_ls_d), ("ls-e", lambda: group_ls_e(caught)),
                      ("ls-f", group_ls_f)):
        try:
            results[group] = fn()
        except Exception:  # noqa: BLE001
            results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    _CACHE.clear()
    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 230 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(c for c in caught if (c == 'anyNight') == (group == 'td-e'))})" \
                if group in ("td-e", "ls-e") else ""
            print(f"[ok  ] 작업 230 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__":
    _ok = check_real_tod_230()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
