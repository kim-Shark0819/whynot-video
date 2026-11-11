"""작업 225 검사 — 긴 JSON 호출(대본 · 속마음 · 콘티 · 기호 · 샷 계획 · 인물 고치기 · 장면 설계)이 8000 출력 토큰에서 잘리지 않는다.
네트워크 0 · LLM 0 · 유료 0(vpoc.llm._converse 를 가짜로 바꾼다).

  (lj-a) 래퍼 7개(225 지시 6개 + 같은 꼴 vnew.script.voices)가 max_tokens == settings.VNEW_LONG_JSON_MAX_TOKENS(기본 32000),
         system_text · tool · where · model 은 그대로
  (lj-b) 설정을 12000 으로 바꾸면 7개 모두 12000(호출 때 읽음). env "abc" · "7999" · "40000" → 32000 + 경고 1줄씩,
         "" → 32000(빈 값 = 설정 없음, _checked_int 규칙이라 경고 없음), "8000" · "32000" 은 받는다
  (lj-c) run_conti 끝까지(prod 꼴: 타일 21장 · 대본 23줄 · 45초): 샷 계획 호출 예산 32000, 8000 이면 잘리고 32000 이면 되는
         가짜 → conti.status "ready". 예산을 8000 으로 고정하면 지금 prod 처럼 실패(재현)
  (lj-d) config.env.example · config.dev.env · config.prod.env · k8s/workloads.yaml.tmpl 바이트 그대로(sha256 상수), 새 env 이름 없음
  (lj-e) 변이 "샷 계획만 8000 으로 되돌림"을 (lj-a) · (lj-c) 가 잡는다
  (lj-f) 바꾼 파일 LF 줄 끝
실행: python -X utf8 -B tools/check_long_json_225.py
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import inspect
import logging
import os
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_long_json_225").check_long_json_225(): '
        'fail("작업 225: 긴 JSON 호출 출력 예산(VNEW_LONG_JSON_MAX_TOKENS) 검사 실패")')
GROUPS = {
    "lj-a": "래퍼 7개 max_tokens = VNEW_LONG_JSON_MAX_TOKENS(기본 32000) · 나머지 인자 그대로",
    "lj-b": "설정 12000 → 7개 모두 12000 · 틀린 env → 32000 + 경고 1줄 · 8000 · 32000 받음",
    "lj-c": "run_conti(21타일 · 23줄 · 45초): 샷 계획 32000 → ready, 8000 고정이면 실패(prod 재현)",
    "lj-d": "설정 파일 4개 바이트 그대로(sha256 상수) · 새 env 이름 없음",
    "lj-e": "변이(샷 계획 8000 되돌림)를 검사가 잡는다",
    "lj-f": "바꾼 파일 LF",
}
CHANGED = ["app/src/vnew/settings.py", "app/src/vnew/plan.py", "app/src/vnew/previz.py",
           "tools/check_long_json_225.py", "tools/check_vpoc.py"]
ENV_NAME = "VNEW_LONG_JSON_MAX_TOKENS"
DEFAULT = 32000
# 작업 225 시작 때(2026-11-12) 잰 값. 이 작업은 이 파일들을 바꾸지 않는다.
PINNED_SHA256 = {
    "deploy/config.env.example": "b8b02c1cc3ee1ec76be603aecfe9e66dece1b689584b149dc44b183464c2101f",
    "deploy/config.dev.env": "b1557147d466fc79419811e25effade979178245c32fefc54bfd68cdac9b4515",
    "deploy/config.prod.env": "755555d9c6b57e9b19da894f998fb77572ec84d4bd1d33d57be7e54194230a49",
    "k8s/workloads.yaml.tmpl": "0d12c1f7b16325cb602a4f0c0298994026cd43a198927ad2bf8fda515f3e12be",
}
# prod 실패 꼴(작업 225 배경): 웹툰 패널 21장 · 대본 23줄 · 45초.
PROD_TILES, PROD_LINES, PROD_SECONDS = 21, 23, 45
NEED_BUDGET = 20000  # 가짜 모델이 샷 계획을 끝까지 쓰는 데 드는 출력 토큰(8000 < 이 값 ≤ 32000)


def _modules():
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import previz as previzmod  # noqa: PLC0415
    from src.vnew import prompts  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vpoc import llm as vpoc_llm  # noqa: PLC0415

    return planmod, previzmod, prompts, vset, vpoc_llm


@contextlib.contextmanager
def _patched(obj, **values):
    saved = {name: getattr(obj, name) for name in values}
    try:
        for name, value in values.items():
            setattr(obj, name, value)
        yield obj
    finally:
        for name, value in saved.items():
            setattr(obj, name, value)


def _wrapper_cases(planmod, previzmod, prompts) -> list[tuple[str, object, dict]]:
    """(where, 부르기, 기대 인자(max_tokens 뺌)). 도구 · SYSTEM 기대값은 래퍼와 같은 규칙(_cast_on)으로 고른다."""
    cast = planmod._cast_on()  # noqa: SLF001
    blocks = [{"text": "x"}]
    return [
        ("vnew.script", lambda: planmod._analyze_script(blocks, model="m-script"),  # noqa: SLF001
         {"system_text": prompts.SCRIPT_SYSTEM, "tool": prompts.EMIT_SCRIPT, "model": "m-script"}),
        ("vnew.conti", lambda: planmod._analyze_conti(blocks, model="m-conti"),  # noqa: SLF001
         {"system_text": prompts.CONTI_CAST_SYSTEM if cast else prompts.CONTI_SYSTEM,
          "tool": prompts.EMIT_CONTI_CAST if cast else prompts.EMIT_CONTI, "model": "m-conti"}),
        ("vnew.script.voices", lambda: planmod._analyze_voices(blocks, model="m-voices"),  # noqa: SLF001
         {"system_text": prompts.VOICES_SYSTEM, "tool": prompts.EMIT_VOICES, "model": "m-voices"}),
        ("vnew.conti.fx", lambda: planmod._analyze_fx(blocks, model="m-fx"),  # noqa: SLF001
         {"system_text": prompts.FX_SYSTEM, "tool": prompts.EMIT_FX, "model": "m-fx"}),
        ("vnew.conti.vibe", lambda: planmod._analyze_vibe(blocks, model="m-vibe", system_text="SYS-VIBE"),  # noqa: SLF001
         {"system_text": "SYS-VIBE", "tool": prompts.EMIT_VIBE_CUTS_CAST if cast else prompts.EMIT_VIBE_CUTS, "model": "m-vibe"}),
        ("vnew.conti.cast_fix", lambda: planmod._analyze_cast_fix(blocks, model="m-fix"),  # noqa: SLF001
         {"system_text": prompts.CAST_FIX_SYSTEM, "tool": prompts.EMIT_CAST_FIX, "model": "m-fix"}),
        ("vnew.previz.design", lambda: previzmod._design(blocks, model="m-design"),  # noqa: SLF001
         {"system_text": previzmod._DESIGN_SYSTEM, "tool": previzmod._DESIGN_TOOL, "model": "m-design"}),  # noqa: SLF001
    ]


def _run_wrappers(expected_budget: int) -> list[str]:
    """래퍼 7개를 가짜 _converse 로 부르고 인자를 비교한다. 문제 목록."""
    planmod, previzmod, prompts, _vset, vpoc_llm = _modules()
    problems: list[str] = []
    for where, call, want in _wrapper_cases(planmod, previzmod, prompts):
        seen: list[dict] = []

        def fake(**kwargs):
            seen.append(kwargs)
            return {}, {"model": kwargs.get("model")}

        with _patched(vpoc_llm, _converse=fake):
            call()
        if len(seen) != 1:
            problems.append(f"{where}: _converse 호출 {len(seen)}회")
            continue
        got = seen[0]
        if got.get("where") != where:
            problems.append(f"{where}: where={got.get('where')!r}")
        if got.get("max_tokens") != expected_budget:
            problems.append(f"{where}: max_tokens={got.get('max_tokens')!r} (기대 {expected_budget})")
        for key, value in want.items():
            if got.get(key) is not value and got.get(key) != value:
                problems.append(f"{where}: {key} 가 다릅니다")
        if got.get("blocks") != [{"text": "x"}]:
            problems.append(f"{where}: blocks 가 다릅니다")
    return problems


def group_a() -> list[str]:
    _planmod, _previzmod, _prompts, vset, _llm = _modules()
    problems = []
    if vset.VNEW_LONG_JSON_MAX_TOKENS != DEFAULT and not os.getenv(ENV_NAME, "").strip():
        problems.append(f"기본값 {vset.VNEW_LONG_JSON_MAX_TOKENS!r} != {DEFAULT}")
    return problems + _run_wrappers(vset.VNEW_LONG_JSON_MAX_TOKENS)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _settings_with_env(raw: str | None) -> tuple[object, list[str]]:
    """vnew/settings.py 를 새 모듈로 한 번 더 읽는다(env 를 바꾼 채로). (값, 이 키의 경고 줄)."""
    path = ROOT / "app/src/vnew/settings.py"
    spec = importlib.util.spec_from_file_location("src.vnew._settings_probe_225", path)
    module = importlib.util.module_from_spec(spec)
    saved = os.environ.get(ENV_NAME)
    handler = _Capture()
    logger = logging.getLogger("vpoc")
    logger.addHandler(handler)
    try:
        if raw is None:
            os.environ.pop(ENV_NAME, None)
        else:
            os.environ[ENV_NAME] = raw
        spec.loader.exec_module(module)
    finally:
        logger.removeHandler(handler)
        if saved is None:
            os.environ.pop(ENV_NAME, None)
        else:
            os.environ[ENV_NAME] = saved
    warns = [line for line in handler.lines if "vnew.settings.invalid" in line and f"key={ENV_NAME}" in line]
    return getattr(module, ENV_NAME, None), warns


def group_b() -> list[str]:
    _planmod, _previzmod, _prompts, vset, _llm = _modules()
    problems = []
    with _patched(vset, VNEW_LONG_JSON_MAX_TOKENS=12000):
        problems += [f"12000: {p}" for p in _run_wrappers(12000)]
    cases = [(None, DEFAULT, 0), ("abc", DEFAULT, 1), ("7999", DEFAULT, 1), ("40000", DEFAULT, 1), ("", DEFAULT, 0),
             ("8000", 8000, 0), ("32000", 32000, 0), (" 12000 ", 12000, 0)]
    for raw, want, want_warns in cases:
        value, warns = _settings_with_env(raw)
        if value != want or len(warns) != want_warns:
            problems.append(f"env {raw!r}: 값 {value!r} 경고 {len(warns)} (기대 {want} · 경고 {want_warns})")
    return problems


def _prod_plan() -> dict:
    roles = ["민지", "준호"]
    entries = [{"id": f"e{i + 1}", "panels": [min(i * PROD_TILES // PROD_LINES, PROD_TILES - 1)], "speaker": roles[i % 2],
                "lineKo": f"대사 {i + 1}", "situationKo": "본다", "locationEn": "classroom",
                "characters": [{"name": roles[i % 2], "positionEn": "by the window"}]} for i in range(PROD_LINES)]
    return {"planId": "plan_lj", "revision": 1,
            "input": {"order": ["f1"], "totalSeconds": PROD_SECONDS, "notes": "",
                      "characters": [{"roleName": "민지", "charId": "c1"}, {"roleName": "준호", "charId": "c2"}]},
            "task": {"name": "conti", "status": "running", "startedAt": None, "id": "t1"},
            "script": {"status": "ready", "setting": {"locationKo": "교실", "locationEn": "classroom", "enclosed": True},
                       "entries": entries}}


def _vibe_reply() -> dict:
    def person(name: str) -> dict:
        return {"name": name, "presence": "on_screen", "actionEn": f"{name} looks up", "positionEn": "by the window"}

    beats = [{"t0": t, "t1": t + 3, "actionEn": "Minji looks up.", "cameraEn": "Static camera", "descKo": "본다",
              "characters": ["민지", "준호"], "sourcePanel": min(t * PROD_TILES // PROD_SECONDS, PROD_TILES - 1),
              "shotSize": "closeup", "kind": "character", "cast": [person("민지"), person("준호")],
              "fxSuggest": [], "moodKo": "", "moodEn": ""} for t in range(0, PROD_SECONDS, 3)]
    return {"backgroundEn": "classroom", "beats": beats}


def _run_conti_prod(vibe_wrapper=None) -> tuple[dict, list[tuple[str, int]], list[str]]:
    """prod 꼴 run_conti 1회. (conti, [(where, max_tokens)], 예상 밖 호출). 가짜 _converse 는 NEED_BUDGET 미만이면 잘림."""
    planmod, _previzmod, _prompts, _vset, vpoc_llm = _modules()
    from src.vnew import characters as charmod  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    saved: dict = {}
    calls: list[tuple[str, int]] = []
    odd: list[str] = []

    def fake_converse(*, system_text, blocks, tool, max_tokens, where, model):  # noqa: ARG001
        calls.append((where, max_tokens))
        usage = {"model": model, "inputTokens": 44234, "outputTokens": min(max_tokens, NEED_BUDGET)}
        if where == "vnew.conti.vibe":
            if max_tokens < NEED_BUDGET:  # 진짜 llm._converse 가 체인 전부 잘렸을 때 내는 오류와 같은 꼴
                raise VpocError(where, "SchemaViolation", f"모델 출력이 출력 한도({max_tokens} 토큰)에서 잘렸습니다. 출력 앞부분: ", 502)
            return copy.deepcopy(_vibe_reply()), usage
        if where in ("vnew.conti.cast_fix", "vnew.conti.fx"):
            return {"cuts": []}, usage
        odd.append(where)
        raise RuntimeError("unexpected " + where)

    names = ("load_plan", "mutate_plan", "ordered_tiles", "_charge", "_sync_locations_after_conti",
             "_unapprove_previz_after_conti", "_max_segment_seconds")
    fakes = {
        "load_plan": lambda owner, pid: (saved, "etag"),
        "mutate_plan": lambda owner, pid, mutator, **kw: (saved, mutator(saved)),
        "ordered_tiles": lambda owner, pid, order: ([f"tile{i}".encode() for i in range(PROD_TILES)], PROD_TILES, PROD_TILES),
        "_charge": lambda *a, **kw: None,
        "_sync_locations_after_conti": lambda owner, pid: None,
        "_unapprove_previz_after_conti": lambda owner, pid: None,
        "_max_segment_seconds": lambda video_model: 15,
    }
    if vibe_wrapper is not None:
        names += ("_analyze_vibe",)
        fakes["_analyze_vibe"] = vibe_wrapper
    orig = {n: getattr(planmod, n) for n in names}
    orig_conv, orig_load = vpoc_llm._converse, charmod.load_char  # noqa: SLF001
    vlog = logging.getLogger("vpoc")
    saved_disabled = vlog.disabled
    try:
        for n in names:
            setattr(planmod, n, fakes[n])
        vpoc_llm._converse = fake_converse  # noqa: SLF001
        charmod.load_char = lambda owner, cid: ({"analysis": {"appearanceEn": f"look of {cid}"}}, "e")
        vlog.disabled = True
        saved.update(_prod_plan())
        planmod.run_conti("o", "p", "t1")
    finally:
        for n, f in orig.items():
            setattr(planmod, n, f)
        vpoc_llm._converse, charmod.load_char = orig_conv, orig_load  # noqa: SLF001
        vlog.disabled = saved_disabled
    return saved.get("conti") or {}, calls, odd


def group_c(vibe_wrapper=None) -> list[str]:
    _planmod, _previzmod, _prompts, vset, _llm = _modules()
    problems = []
    with _patched(vset, VNEW_LONG_JSON_MAX_TOKENS=DEFAULT, VNEW_VIBE_CONTI=True, VNEW_CONTI_IMAGES=True):
        conti, calls, odd = _run_conti_prod(vibe_wrapper)
    vibe = [budget for where, budget in calls if where == "vnew.conti.vibe"]
    long_calls = [(where, budget) for where, budget in calls if budget != DEFAULT]
    if vibe != [DEFAULT]:
        problems.append(f"샷 계획 호출 예산 {vibe} (기대 [{DEFAULT}])")
    if long_calls:
        problems.append(f"32000 이 아닌 긴 호출: {long_calls}")
    if conti.get("status") != "ready" or len(conti.get("cuts") or []) < 5:
        problems.append(f"conti.status={conti.get('status')!r} error={str(conti.get('error'))[:120]!r} "
                        f"cuts={len(conti.get('cuts') or [])}")
    if odd:
        problems.append(f"예상 밖 호출: {odd}")
    if vibe_wrapper is None:
        # 8000 고정 = 225 전 동작 → prod 처럼 잘려서 실패(그림 · 시트 · 글만 3번 다 잘림)
        with _patched(vset, VNEW_LONG_JSON_MAX_TOKENS=8000, VNEW_VIBE_CONTI=True, VNEW_CONTI_IMAGES=True):
            conti8, calls8, _odd8 = _run_conti_prod()
        vibe8 = [budget for where, budget in calls8 if where == "vnew.conti.vibe"]
        if conti8.get("status") != "failed" or not vibe8 or set(vibe8) != {8000} \
                or "8000 토큰" not in str(conti8.get("error") or ""):
            problems.append(f"8000 고정 재현이 다릅니다: status={conti8.get('status')!r} vibe={vibe8}")
    return problems


def group_d() -> list[str]:
    problems = []
    for rel, want in PINNED_SHA256.items():
        data = (ROOT / rel).read_bytes()
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            problems.append(f"{rel}: sha256 {got[:16]}… != 고정값 {want[:16]}…")
        if ENV_NAME.encode() in data or b"LONG_JSON" in data:
            problems.append(f"{rel}: {ENV_NAME} 가 들어 있습니다")
    return problems


def _vibe_mutant():
    """진짜 _analyze_vibe 소스에서 예산만 8000 으로 되돌린 함수(변이)."""
    planmod = _modules()[0]
    src = inspect.getsource(planmod._analyze_vibe)  # noqa: SLF001
    new = src.replace("vnew_settings_module().VNEW_LONG_JSON_MAX_TOKENS", "8000")
    if new == src:
        raise AssertionError("변이를 만들 수 없습니다(예산 식을 찾지 못함)")
    namespace = dict(vars(planmod))
    exec(compile(new, "<vibe-8000-mutant>", "exec"), namespace)  # noqa: S102 — 검사 변이(우리 소스)
    return namespace["_analyze_vibe"]


def group_e(caught: list[str]) -> list[str]:
    planmod = _modules()[0]
    mutant = _vibe_mutant()
    problems = []
    with _patched(planmod, _analyze_vibe=mutant):
        got_a = _run_wrappers(DEFAULT)
    if any(p.startswith("vnew.conti.vibe: max_tokens=8000") for p in got_a):
        caught.append("vibe8000→lj-a")
    else:
        problems.append(f"변이 vibe8000 을 (lj-a) 가 잡지 못했습니다: {got_a}")
    got_c = group_c(vibe_wrapper=mutant)
    if any("샷 계획 호출 예산" in p for p in got_c) and any("conti.status='failed'" in p for p in got_c):
        caught.append("vibe8000→lj-c")
    else:
        problems.append(f"변이 vibe8000 을 (lj-c) 가 잡지 못했습니다: {got_c}")
    return problems


def check_long_json_225() -> bool:
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    for group, fn in (("lj-a", group_a), ("lj-b", group_b), ("lj-c", group_c), ("lj-d", group_d),
                      ("lj-e", lambda: group_e(caught))):
        try:
            results[group] = fn()
        except Exception:  # noqa: BLE001
            results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    results["lj-f"] = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 225 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "lj-e" else ""
            print(f"[ok  ] 작업 225 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__":
    _ok = check_long_json_225()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
