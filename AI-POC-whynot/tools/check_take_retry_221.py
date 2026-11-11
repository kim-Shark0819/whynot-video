"""작업 221 검사 — 테이크별 [실사 다시] 뒤 ⑤ 실사 열이 "멈췄습니다"·회차 전체 다시 만들기($)를 보이던 결함. 유료·네트워크 0.

  (r-a) partial gen + 테이크 하나 [실사 다시] → 저장 status running · finalReal waiting(n) · 드라이버가 그 칸만 내고 끝나면
        병합(finalReal done · final-real-r1) · gen done. 둘 다 다시 했고 하나가 아직 만드는 중이면 병합하지 않고 running
  (r-b) 테이크 실사 칸이 만드는 중(queued·preparing·submitted)이면 회차 전체 시작(start_real) 409 RealRunning(한국어) —
        gen partial · failed · 멈춘 running · 확인 뒤 저장 직전에 생긴 칸도. 보기 canStartReal false. 만드는 칸이 없으면 지금처럼 시작
  (r-c) 멈춤 보기(_reconcile_gen_stale): activeSince 가 2시간 전이어도 만드는 테이크 칸이 최근 선점됐으면 running ·
        오래된 선점 · 선점 없는 칸 · 끝난 칸 · 컷 단위 previz 는 지금처럼 partial · gen_view 도 running
  (r-d) 화면(node): g.status 와 상관없이 만드는 칸이 있으면 takeRealState running · 실사 열에 "실사 버전을 만드는 중… (1/2)" ·
        "멈췄습니다"·[실사 버전 다시 만들기 ($9.25)] 없음(canStartReal true 인 옛 보기도) · 만드는 칸이 없으면 버튼 그대로
  (r-e) 옛 동작: 고치기 전 함수(_real_ready_error · _gen_stale · takeRealState · schedulePlanPoll)와 다시 시도가 status 를 그대로 두는 변이를
        넣으면 위 검사가 실패한다(검사 안에서만 바꾼다 — 파일은 그대로)
  (r-f) node --check vnew.js · 바꾼 파일 LF 줄 끝
  (r-g) 화면(node): schedulePlanPoll 은 통 테이크 실사 칸을 만드는 중이면 gen partial · failed · cancelled 여도 다시 불러오고(5초 ·
        제출 직전 3초) 만드는 칸이 없으면 멈춘다 · running · 컷 단위 previz · ⑦ 편집은 지금처럼

node 가 없으면(또는 --no-node · CHECK_221_NO_NODE=1) node 묶음은 [WARN] 이고 FAIL 하지 않는다.
실행: python -X utf8 -B tools/check_take_retry_221.py
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_take_retry_221").check_take_retry_221(): fail("작업 221: 테이크별 실사 다시 뒤 진행 상태 · 회차 전체 다시 만들기 막기 검사 실패")
"""

from __future__ import annotations

import __future__ as _future
import copy
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_take_retry_221").check_take_retry_221(): '
        'fail("작업 221: 테이크별 실사 다시 뒤 진행 상태 · 회차 전체 다시 만들기 막기 검사 실패")')
GROUPS = {
    "r-a": "partial gen 테이크 실사 다시 → running · 드라이버 · 병합 finalReal done",
    "r-b": "만드는 테이크 실사 칸이 있으면 회차 전체 시작 409(한국어) · 없으면 시작",
    "r-c": "최근 선점된 테이크 칸 → 멈춤(partial)으로 보지 않음",
    "r-d": "화면: 만드는 칸 → running · 진행 문구 · 멈춤 문구 · 전체 다시 만들기 없음",
    "r-g": "화면: 만드는 칸이 있으면 gen partial 이어도 다시 불러옴 · 없으면 멈춤",
    "r-e": "옛 동작 변이를 검사가 잡음",
    "r-f": "node --check vnew.js · LF 줄 끝",
}
OWNER, PID, GEN = "u221", "c" * 32, "g221"
ACTIVE_TEXT = "실사 테이크를 아직 만드는 중입니다. 끝난 뒤에 다시 눌러 주세요."
RUNNING_TEXT = "영상 생성이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요."
NO_NODE = False  # __main__ 의 --no-node

# ---- 고치기 전(작업 221 전) 함수 원문 — 변이 old-guard · old-stale · old-ui 가 그대로 되살린다 ----
OLD_REAL_READY_ERROR = '''def _real_ready_error(gen: dict) -> VpocError | None:
    gen = gen if isinstance(gen, dict) else {}
    if gen.get("mode") != "previz-take" or (gen.get("finalWebtoon") or {}).get("status") != "done":
        return VpocError(
            "vnew.generate.real",
            "RealNotReady",
            "웹툰 통 테이크가 끝난 뒤에 실사 버전을 만들 수 있습니다.",
            409,
        )
    if gen.get("status") == "running" and not _gen_stale(gen):
        return VpocError("vnew.generate.real", "RealRunning", "영상 생성이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.", 409)
    return None
'''
OLD_GEN_STALE = '''def _gen_stale(gen: dict) -> bool:
    if gen.get("status") != "running":
        return False
    created = plan_mod._parse_iso(gen.get("activeSince") or gen.get("createdAt") or "")
    if not created:
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() > GEN_STALL_S
'''
OLD_TAKE_REAL_STATE = '''function takeRealState(gen) {
    var g = gen || {};
    if (!g.realRun) return "none";
    var final = g.finalReal || {};
    if (final.status === "done") return "done";
    if (final.status === "failed") return "failed";
    var counts = takeRealCounts(g);
    if (g.status === "running" && (counts.active > 0 || counts.completed === counts.total)) return "running";
    return counts.cancelled > 0 && counts.failed === 0 ? "cancelled" : "failed";
  }'''
RETRY_RUNNING = '            g["status"] = "running"\n            g["activeSince"] = now_iso()\n            return'
RETRY_NO_RUNNING = '            g["activeSince"] = now_iso()\n            return'
POLL_NEW = 'var genRunning = gen.status === "running" || takeRealActive(gen);'
POLL_OLD = 'var genRunning = gen.status === "running";'

# 변이: 이름 → (설명, 실패해야 할 묶음, 적용)
MUTANTS = {
    "old-guard": ("고치기 전 _real_ready_error(만드는 테이크 칸을 보지 않음)", "r-b", ("pydef", "_real_ready_error", OLD_REAL_READY_ERROR)),
    "old-stale": ("고치기 전 _gen_stale(activeSince 만 봄)", "r-c", ("pydef", "_gen_stale", OLD_GEN_STALE)),
    "no-running": ("다시 시도가 gen status 를 그대로 둠(회귀 변이)", "r-a", ("pyline", "retry_segment", (RETRY_RUNNING, RETRY_NO_RUNNING))),
    "old-ui": ("고치기 전 takeRealState(g.status running 일 때만 running)", "r-d", ("js", "takeRealState", OLD_TAKE_REAL_STATE)),
    "old-poll": ("고치기 전 schedulePlanPoll(gen.status running 일 때만 다시 불러옴)", "r-g",
                 ("js", "schedulePlanPoll", (POLL_NEW, POLL_OLD))),
}


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan() -> dict:
    """구간 3개(12초) → 테이크 1 = 구간 1·2(컷 1–6), 테이크 2 = 구간 3(컷 7–9). check_take_stale_205 와 같은 모양."""
    cuts = [{"id": f"c{i}", "no": i} for i in range(1, 10)]
    segs = [{"id": f"s{n}", "cutIds": [f"c{3 * (n - 1) + j}" for j in (1, 2, 3)], "seconds": 12.0} for n in (1, 2, 3)]
    recordings = {seg["id"]: {"status": "ready", "key": f"previz/3/{seg['id']}.mp4", "seconds": 12.0} for seg in segs}
    previz = {"revision": 3, "approved": True, "recordings": recordings, "shotBrief": {"status": "ready", "updatedAt": "T1"}}
    return {"conti": {"cuts": cuts, "segments": segs}, "previz": previz, "input": {"videoModel": "fal:take-model"}}


class _Patch:
    def __init__(self) -> None:
        self.saved: list[tuple] = []

    def set(self, obj, name: str, value) -> None:
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        while self.saved:
            obj, name, value = self.saved.pop()
            setattr(obj, name, value)


class _Store:
    def __init__(self, blobs: dict) -> None:
        self.blobs = blobs

    def put_bytes(self, key, data, content_type="", bucket=""):
        self.blobs[key] = bytes(data)


class _Env:
    """메모리 계획 저장소(etag 조건부 쓰기 흉내) + 영상·S3 가짜. 실제 mutate_plan · _mutate_locked · 드라이버를 그대로 쓴다."""

    def __init__(self, gm, pm, verr) -> None:
        self.gm, self.pm, self.verr = gm, pm, verr
        self.store: dict = {"plan": None, "ver": 0}
        self.lock = threading.Lock()
        self.spawned: list[str] = []
        self.calls: list[tuple] = []
        self.blobs: dict = {}
        self.p = _Patch()

    def __enter__(self):
        gm, pm, p = self.gm, self.pm, self.p
        p.set(pm, "load_plan", self._load)
        p.set(pm, "save_plan", self._save)
        p.set(gm, "_spawn_driver", lambda owner, pid, gen_id: self.spawned.append(gen_id))
        p.set(gm, "_gen_mode", lambda plan: "previz-take")
        p.set(gm, "_previz_model_endpoint", lambda plan: "take-model")
        p.set(gm, "_real_take_model", lambda gen=None: "take-model")
        p.set(gm, "_take_unit_price", lambda endpoint: 0.01)
        p.set(gm, "_take_parts_for", lambda *a, **k: [])
        p.set(gm, "check_preconditions", lambda *a, **k: [])
        p.set(gm, "_presign", lambda key, **k: ("https://signed.example/" + key) if key else "")
        p.set(gm, "_take_start_real_block", lambda owner, plan: "")
        p.set(gm, "real_fallback_max", lambda plan, gen: None)
        p.set(gm, "_real_missing_names", lambda *a, **k: [])
        p.set(gm, "_fetch_bytes", lambda key, bucket="": b"MP4:" + str(key).encode())
        p.set(gm, "_fit_takes", lambda clips, planned, variant: None)
        p.set(gm, "_concat", lambda clips: b"|".join(clips))
        p.set(gm, "_has_audio_stream", lambda mp4: True)
        p.set(gm, "STORE", _Store(self.blobs))
        p.set(gm, "_do_submit_previz", self._submit)
        p.set(gm, "_do_poll_previz", self._poll)
        p.set(gm.vnew_settings, "VNEW_TAKE_REAL", True)
        p.set(gm.vnew_settings, "POLL_INTERVAL_S", 0)
        p.set(gm.vpoc_settings, "FAL_KEY", "test-key-221")
        return self

    def __exit__(self, *exc) -> None:
        self.p.undo()

    def _load(self, owner, pid):
        with self.lock:
            if self.store["plan"] is None:
                raise self.verr("chk221", "NoPlan", "계획이 없습니다.", 404)
            return copy.deepcopy(self.store["plan"]), str(self.store["ver"])

    def _save(self, owner, pid, data, etag=""):
        with self.lock:
            if etag and etag != str(self.store["ver"]):
                raise self.verr("chk221", "PreconditionFailed", "경합", 412)
            self.store["plan"] = copy.deepcopy(data)
            self.store["ver"] += 1
            return str(self.store["ver"])

    def _cell_write(self, index: int, variant: str, owned: str, **values) -> bool:
        def mutator(data: dict) -> None:
            state = self.gm._previz_cells(data.get("gen") or {})[index][variant]  # noqa: SLF001
            if state.get("claim") != owned:
                raise self.gm._Discard()  # noqa: SLF001
            state.update(values)

        try:
            self.gm._commit(OWNER, PID, mutator)  # noqa: SLF001
        except self.gm._Discard:  # noqa: SLF001
            return False
        return True

    def _submit(self, owner, pid, gen_id, index, variant, claim):  # 유료 제출 대신
        if self._cell_write(index, variant, claim, status="submitted", invocationArn=f"fal:221/{index}/{variant}"):
            self.calls.append(("submit", index, variant))

    def _poll(self, owner, pid, gen_id, index, variant, claim):  # 조회: 바로 끝남
        if self._cell_write(index, variant, claim, status="completed", claim="", claimedAt="",
                            outputKey=f"out/{gen_id}/take{index + 1}-{variant}-retry.mp4"):
            self.calls.append(("poll", index, variant))

    def put(self, plan: dict, gen: dict) -> None:
        with self.lock:
            self.store["plan"] = copy.deepcopy({**plan, "gen": gen})
            self.store["ver"] += 1

    def gen(self) -> dict:
        return copy.deepcopy(self.store["plan"]["gen"])

    def edit(self, fn) -> None:
        with self.lock:
            fn(self.store["plan"]["gen"])
            self.store["ver"] += 1

    def drive(self, timeout: float = 60.0) -> str:
        box: dict = {}

        def run() -> None:
            try:
                self.gm._drive(OWNER, PID, GEN)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                box["err"] = traceback.format_exc()[-600:]

        worker = threading.Thread(target=run, daemon=True, name="chk221-drive")
        worker.start()
        worker.join(timeout)
        return "멈추지 않음" if worker.is_alive() else box.get("err", "")


def _take_gen(gm, plan: dict, status: str, reals: tuple, active_since: str, claimed: str = "") -> dict:
    """실사 회차 1 이 있는 통 테이크 gen. reals = 테이크별 실사 칸 상태. 웹툰은 모두 completed · finalWebtoon done."""
    g = gm._new_take_gen(plan)  # noqa: SLF001
    g.update(genId=GEN, status=status, createdAt=_ago(10800), activeSince=active_since, selected=None, realHistory=[],
             finalWebtoon={"status": "done", "key": f"out/{GEN}/final-web.mp4", "error": ""},
             finalReal={"status": "waiting", "key": "", "error": "", "n": 1},
             realRun={"n": 1, "startedAt": _ago(10800), "audio": True, "usd": 9.25, "endpoint": "take-model", "resolution": "480p"})
    for i, (unit, real) in enumerate(zip(g["units"], reals), start=1):
        unit["webtoon"].update(status="completed", outputKey=f"out/{GEN}/take{i}-webtoon.mp4")
        state = {**gm._empty_variant_state(), "status": real, "audio": True, "failureKind": "", "promptEn": "", "promptBytes": 0}  # noqa: SLF001
        if real == "completed":
            state["outputKey"] = f"out/{GEN}/take{i}-real.mp4"
        elif real == "failed":
            state["error"] = "fal 잔액이 부족합니다."
        elif real in ("preparing", "submitted"):
            state.update(claim=f"k{i}", claimedAt=claimed or _ago(60))
        unit["real"] = state
    return g


def _err(exc) -> tuple:
    return (getattr(exc, "status", None), getattr(exc, "kind", type(exc).__name__), getattr(exc, "message", str(exc)))


# ================================================================ 묶음


def _group_retry(gm, pm, verr) -> list[str]:
    bad: list[str] = []

    def expect(got, want, what: str) -> None:
        if got != want:
            bad.append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    plan = _plan()
    final_key = gm.final_real_run_key(OWNER, PID, GEN, 1)
    # (가) 테이크 1 실사 실패(잔액) · 테이크 2 완료 · gen partial → 테이크 1 만 [실사 다시]
    with _Env(gm, pm, verr) as env:
        env.put(plan, _take_gen(gm, plan, "partial", ("failed", "completed"), _ago(10800)))
        gm.retry_segment(OWNER, PID, "take1", "real")
        g = env.gen()
        expect([g["status"], g["finalReal"], g["units"][0]["real"]["status"], g["units"][1]["real"]["status"], env.spawned],
               ["running", {"status": "waiting", "key": "", "error": "", "n": 1}, "queued", "completed", [GEN]],
               "partial gen 테이크 1 실사 다시 → 저장 status running · finalReal waiting(n 1) · 드라이버 1번")
        expect(gm._gen_stale(g), False, "다시 시도 직후 멈춤 아님")  # noqa: SLF001
        why = env.drive()
        g = env.gen()
        expect([why, env.calls], ["", [("submit", 0, "real"), ("poll", 0, "real")]],
               "드라이버: 테이크 1 실사만 내고 조회(테이크 2 는 다시 내지 않음 — 원장 그대로)")
        expect([g["status"], g["finalReal"], env.blobs.get(final_key)],
               ["done", {"status": "done", "key": final_key, "error": "", "n": 1},
                f"MP4:out/{GEN}/take1-real-retry.mp4|MP4:out/{GEN}/take2-real.mp4".encode()],
               "다 끝나면 병합: finalReal done(final-real-r1, 테이크 순서) · gen done")
    # (나) 운영 모양: 둘 다 잔액 실패 → 둘 다 [실사 다시] → 테이크 2 완료 · 테이크 1 은 아직 만드는 중(Kling 컷 단위)
    with _Env(gm, pm, verr) as env:
        env.put(plan, _take_gen(gm, plan, "partial", ("failed", "failed"), _ago(10800)))
        gm.retry_segment(OWNER, PID, "take1", "real")
        gm.retry_segment(OWNER, PID, "take2", "real")
        g = env.gen()
        expect([g["status"], [u["real"]["status"] for u in g["units"]], env.spawned],
               ["running", ["queued", "queued"], [GEN, GEN]], "둘 다 다시 → running · 두 칸 queued")

        def midway(gen: dict) -> None:
            gen["activeSince"] = _ago(7200)
            gen["units"][1]["real"].update(status="completed", outputKey=f"out/{GEN}/take2-real-retry.mp4")
            gen["units"][0]["real"].update(status="submitted", claim="k1", claimedAt=_ago(60),
                                           fallback={"step": 2, "reason": "likeness", "cuts": []})

        env.edit(midway)
        gm._maybe_finalize_previz(OWNER, PID, GEN)  # noqa: SLF001
        g = env.gen()
        expect([g["status"], g["finalReal"]["status"], env.blobs.get(final_key)], ["running", "waiting", None],
               "테이크 1 이 아직 만드는 중 → 병합하지 않고 running 그대로")
        env.edit(lambda gen: gen["units"][0]["real"].update(status="completed", claim="", claimedAt="",
                                                            outputKey=f"out/{GEN}/take1-real-retry.mp4"))
        gm._maybe_finalize_previz(OWNER, PID, GEN)  # noqa: SLF001
        g = env.gen()
        expect([g["status"], g["finalReal"]["status"], g["finalReal"]["n"], env.blobs.get(final_key)],
               ["done", "done", 1, f"MP4:out/{GEN}/take1-real-retry.mp4|MP4:out/{GEN}/take2-real-retry.mp4".encode()],
               "테이크 1 도 끝나면 병합 finalReal done · gen done")
    return bad


def _group_guard(gm, pm, verr) -> list[str]:
    bad: list[str] = []

    def expect(got, want, what: str) -> None:
        if got != want:
            bad.append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    plan = _plan()
    refused = (409, "RealRunning", ACTIVE_TEXT)
    cases = (
        ("partial · 테이크 1 submitted(2시간 전 선점)", "partial", "submitted", _ago(10800), _ago(7200)),
        ("partial · 테이크 1 queued", "partial", "queued", _ago(10800), ""),
        ("partial · 테이크 1 preparing", "partial", "preparing", _ago(10800), _ago(30)),
        ("failed · 테이크 1 queued", "failed", "queued", _ago(10800), ""),
        ("멈춘 running(activeSince·선점 2시간 전) · 테이크 1 submitted", "running", "submitted", _ago(7200), _ago(7200)),
    )
    for label, status, take1, since, claimed in cases:
        with _Env(gm, pm, verr) as env:
            gen = _take_gen(gm, plan, status, (take1, "completed"), since, claimed)
            env.put(plan, gen)
            ready = gm._real_ready_error(env.gen())  # noqa: SLF001
            usd = gm.real_estimate(env.gen())["usd"]
            try:
                gm.start_real(OWNER, PID, usd)
                got = "시작함"
            except Exception as exc:  # noqa: BLE001
                got = _err(exc)
            g = env.gen()
            expect([None if ready is None else _err(ready), got, g["realRun"]["n"], g["units"][1]["real"].get("outputKey"),
                    g["units"][0]["real"]["status"], env.spawned],
                   [refused, refused, 1, f"out/{GEN}/take2-real.mp4", take1, []],
                   f"{label}: 회차 전체 시작 409 RealRunning(한국어) · 회차 1 · 칸 그대로 · 드라이버 없음")
            if status == "partial" and take1 == "submitted":
                view = gm.gen_view(OWNER, PID, env.store["plan"])
                expect([view.get("canStartReal"), view.get("realBlock")], [False, ACTIVE_TEXT],
                       "보기: 만드는 칸이 있으면 canStartReal false · realBlock 같은 문구")
    # 확인(첫 검사) 뒤 저장 직전에 테이크 칸이 다시 만들어지기 시작함 → 시작하지 않는다(옛 화면에서 누른 경우).
    with _Env(gm, pm, verr) as env:
        env.put(plan, _take_gen(gm, plan, "partial", ("failed", "completed"), _ago(10800)))
        orig = gm._mutate_locked  # noqa: SLF001

        def racing(owner, pid, mutator):
            env.edit(lambda gen: gen["units"][0]["real"].update(status="queued", error=""))
            return orig(owner, pid, mutator)

        env.p.set(gm, "_mutate_locked", racing)
        try:
            gm.start_real(OWNER, PID, gm.real_estimate(env.gen())["usd"])
            got = "시작함"
        except Exception as exc:  # noqa: BLE001
            got = _err(exc)
        expect([got, env.gen()["realRun"]["n"]], [refused, 1], "저장 직전에 생긴 만드는 칸 → 409 · 회차 1 그대로")
    # 만드는 칸이 없으면(실패·완료만) 지금처럼 시작할 수 있다.
    with _Env(gm, pm, verr) as env:
        env.put(plan, _take_gen(gm, plan, "partial", ("failed", "completed"), _ago(10800)))
        view = gm.gen_view(OWNER, PID, env.store["plan"])
        ready = gm._real_ready_error(env.gen())  # noqa: SLF001
        run = gm.start_real(OWNER, PID, gm.real_estimate(env.gen())["usd"])
        g = env.gen()
        expect([view.get("canStartReal"), ready, run["n"], g["status"], [u["real"]["status"] for u in g["units"]],
                len(g["realHistory"]), env.spawned],
               [True, None, 2, "running", ["queued", "queued"], 1, [GEN]],
               "만드는 칸 없음(실패 · 완료) → canStartReal true · 회차 2 시작(지금처럼)")
    return bad


def _group_stale(gm, pm, verr) -> list[str]:
    bad: list[str] = []

    def expect(got, want, what: str) -> None:
        if got != want:
            bad.append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    plan = _plan()
    with _Env(gm, pm, verr) as env:
        def shown(reals, claimed="", since=None, mode="previz-take", web=None) -> str:
            g = _take_gen(gm, plan, "running", reals, since or _ago(7200), claimed)
            if web:
                g["units"][0]["webtoon"].update(status=web, claim="w1", claimedAt=_ago(60))
            if mode != "previz-take":
                g["mode"] = mode
            return gm._reconcile_gen_stale(g)["status"]  # noqa: SLF001

        expect([shown(("submitted", "completed"), _ago(60)), shown(("preparing", "completed"), _ago(60)),
                shown(("completed", "completed"), web="submitted")],
               ["running", "running", "running"],
               "activeSince 2시간 전 · 테이크 칸이 1분 전 선점(실사 submitted · preparing · 웹툰 submitted) → running")
        expect([shown(("submitted", "completed"), _ago(7200)), shown(("queued", "completed")),
                shown(("completed", "completed"))],
               ["partial", "partial", "partial"],
               "선점이 2시간 전 · 선점 없는 queued · 만드는 칸 없음 → 지금처럼 멈춤(partial)")
        expect([shown(("submitted", "completed"), _ago(60), mode="previz"), shown(("failed", "completed"), since=_ago(5))],
               ["partial", "running"], "컷 단위 previz 는 지금처럼 · activeSince 5초 전은 running")
        # 운영 모양: 저장 running · activeSince 2시간 전 · 테이크 1 Kling 컷 단위로 1분 전 선점 · 테이크 2 완료
        env.put(plan, _take_gen(gm, plan, "running", ("submitted", "completed"), _ago(7200), _ago(60)))
        view = gm.gen_view(OWNER, PID, env.store["plan"])
        expect([view.get("status"), view.get("canStartReal"), view.get("realBlock"), view["finalReal"]["status"]],
               ["running", False, RUNNING_TEXT, "waiting"],
               "gen_view: 테이크가 움직이는 동안 status running · 회차 전체 시작 막힘")
    return bad


# ================================================================ 화면(node)

_JS_FUNCS = ("el", "takeRealUsdText", "takeRealStartLabel", "takeRealCounts", "takeRealState", "takeRealProgress",
             "takeRealFailedText", "takeRealHistoryLabel", "takeRealAutoPending", "takeRealCastText", "takeRealHistory",
             "takeRealColumn")
_JS_VARS = ("TAKE_REAL_LEAD", "TAKE_REAL_HISTORY_STATUS", "TAKE_REAL_AUTO_TEXT", "TAKE_REAL_CAST_BUTTON")
_JS_HEAD = r"""
function FakeNode(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.className = ""; this.textContent = "";
  this.style = {}; this.attrs = {}; this.type = ""; this.disabled = false; }
FakeNode.prototype.appendChild = function (c) { this.children.push(c); return c; };
FakeNode.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
FakeNode.prototype.addEventListener = function () {};
var document = { createElement: function (tag) { return new FakeNode(tag); } };
function walk(n, out) { out.push(n); n.children.forEach(function (c) { walk(c, out); }); return out; }
function texts(n) { return walk(n, []).map(function (x) { return x.textContent; }).filter(Boolean); }
function buttons(n) { return walk(n, []).filter(function (x) { return x.tagName === "BUTTON"; }).map(function (x) { return x.textContent; }); }
function downloadLink(url, label) { return el("a", "vnew-download", label); }
function takePickButton() { return el("button", "primary-button vnew-pick-btn", "이걸로 고르기"); }
function openRealConfirm() {}
function openRealCastPicker() {}
"""
_JS_CASES = r"""
var RUN = { n: 1, startedAt: "", audio: true, usd: 9.25, endpoint: "e", resolution: "480p" };
function cell(status) {
  return { status: status, attempt: 1, error: status === "failed" ? "fal 잔액이 부족합니다." : "", note: "",
           videoUrl: status === "completed" ? "https://signed.example/r.mp4" : "", failureKind: "", audio: true };
}
function genOf(status, r1, r2) {
  return { mode: "previz-take", status: status, realEnabled: true, realRun: RUN, realHistory: [], canStartReal: true, realBlock: "",
           realEstimate: { usd: 9.25, seconds: 36, takes: 2, resolution: "480p" },
           finalWebtoon: { status: "done", videoUrl: "https://signed.example/w.mp4" },
           finalReal: { status: "waiting", error: "", note: "", videoUrl: "", n: 1 },
           units: [{ id: "take1", real: cell(r1), webtoon: cell("completed") }, { id: "take2", real: cell(r2), webtoon: cell("completed") }] };
}
var prod = genOf("partial", "submitted", "completed");
var col = takeRealColumn(prod, null);
var idle = takeRealColumn(genOf("partial", "failed", "completed"), null);
console.log(JSON.stringify({
  states: ["partial", "failed", "cancelled", "done", "running", undefined].map(function (s) { return takeRealState(genOf(s, "submitted", "completed")); }),
  kinds: ["queued", "preparing", "submitted"].map(function (k) { return takeRealState(genOf("partial", k, "completed")); }),
  idle: [takeRealState(genOf("partial", "failed", "completed")), takeRealState(genOf("running", "completed", "completed")),
         takeRealState(genOf("partial", "cancelled", "completed"))],
  progress: takeRealProgress(prod).text,
  colTexts: texts(col), colButtons: buttons(col), idleButtons: buttons(idle)
}));
"""


def _extract_js(source: str, name: str) -> str:
    """vnew.js 에서 function name(...) { ... } 원문(문자열 · 주석 안 괄호는 건너뛴다). 없거나 둘 이상이면 AssertionError."""
    found = list(re.finditer(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source))
    if len(found) != 1:
        raise AssertionError(f"vnew.js 에 function {name} 이 {len(found)}곳입니다(기대 1)")
    start = found[0].start()
    j = source.index("{", found[0].end())
    depth, quote = 0, ""
    while j < len(source):
        ch = source[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif source.startswith("//", j):
            j = source.index("\n", j)
            continue
        elif source.startswith("/*", j):
            j = source.index("*/", j) + 2
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
        j += 1
    raise AssertionError(f"function {name} 의 끝을 찾지 못했습니다")


def _js_var(source: str, name: str) -> str:
    found = re.findall(r"^\s*(var " + re.escape(name) + r" = .*;)\s*(?://.*)?$", source, re.M)
    if len(found) != 1:
        raise AssertionError(f"vnew.js 에 var {name} 이 {len(found)}곳입니다(기대 1)")
    return found[0]


def _group_ui(node: str, replace: dict | None = None) -> list[str]:
    bad: list[str] = []
    source = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    funcs = {name: _extract_js(source, name) for name in _JS_FUNCS}
    for name, text in (replace or {}).items():
        funcs[name] = text
    code = _JS_HEAD + "\n".join(_js_var(source, n) for n in _JS_VARS) + "\n" + "\n".join(funcs.values()) + _JS_CASES
    with tempfile.TemporaryDirectory(prefix="chk221-") as tmp:
        path = Path(tmp) / "take_real.js"
        path.write_text(code, encoding="utf-8", newline="\n")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        return ["node 실패: " + (proc.stderr or "")[-600:]]
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    def expect(value, want, what: str) -> None:
        if value != want:
            bad.append(f"{what}\n      결과 {str(value)[:300]}\n      기대 {str(want)[:300]}")

    progress = "실사 버전을 만드는 중… (1/2)"
    expect(got["states"], ["running"] * 6, "만드는 칸이 있으면 g.status(partial · failed · cancelled · done · running · 없음)와 상관없이 running")
    expect(got["kinds"], ["running"] * 3, "queued · preparing · submitted 모두 만드는 중")
    expect(got["idle"], ["failed", "running", "cancelled"], "만드는 칸이 없으면 지금처럼(실패 · 이어 붙이는 중 · 취소)")
    expect(got["progress"], progress, "진행 문구(완료 테이크 / 전체)")
    expect([progress in got["colTexts"], any("멈췄습니다" in t for t in got["colTexts"]), got["colButtons"]], [True, False, []],
           "운영 모양(partial · 테이크 1 만드는 중 · canStartReal true 옛 보기): 진행 문구 · '멈췄습니다' 없음 · 버튼 없음")
    expect(got["idleButtons"], ["실사 버전 다시 만들기 ($9.25)", "인물 바꿔 다시 만들기"], "만드는 칸이 없으면 다시 만들기 버튼 그대로")
    return bad


# 작업 221 보완: schedulePlanPoll 은 통 테이크 실사 칸을 만드는 중이면 gen.status 가 partial 이어도 다시 불러온다.
_POLL_FUNCS = ("schedulePlanPoll", "locationsBuilding", "genBusyFast", "takeRealActive", "takeRealCounts")
_POLL_VARS = ("PLAN_POLL_MS", "GEN_POLL_FAST_MS", "GEN_POLL_SLOW_MS")
_POLL_HEAD = r"""
var timers = [], loads = [];
var window = { setTimeout: function (fn, ms) { timers.push([fn, ms]); return timers.length; }, clearTimeout: function () {} };
var state = { planStep: 5, plan: null, planPollTimer: 0, mounted: true };
function stopPlanPolling() { state.planPollTimer = 0; }
function loadPlan(a, b) { loads.push([a, b]); }
"""
_POLL_CASES = r"""
function genOf(mode, status, r1, r2) {
  return { mode: mode, status: status, realRun: { n: 1 }, finalReal: { status: "waiting", n: 1 },
           units: [{ id: "take1", real: { status: r1 }, webtoon: { status: "completed" } },
                   { id: "take2", real: { status: r2 }, webtoon: { status: "completed" } }] };
}
function poll(gen, step) {
  timers.length = 0; state.planStep = step || 5; state.plan = { gen: gen }; schedulePlanPoll();
  return timers.length ? timers[0][1] : 0;
}
var T = "previz-take";
var out = {
  active: [poll(genOf(T, "partial", "submitted", "completed")), poll(genOf(T, "partial", "queued", "completed")),
           poll(genOf(T, "partial", "preparing", "failed")), poll(genOf(T, "failed", "submitted", "failed")),
           poll(genOf(T, "cancelled", "submitted", "completed"))],
  idle: [poll(genOf(T, "partial", "failed", "completed")), poll(genOf(T, "partial", "completed", "completed")),
         poll(genOf(T, "done", "completed", "completed")), poll(genOf(T, "partial", "cancelled", "failed"))],
  same: [poll(genOf(T, "running", "completed", "completed")), poll(genOf("previz", "partial", "submitted", "completed")),
         poll(genOf(T, "partial", "submitted", "completed"), 7)]
};
loads.length = 0; poll(genOf(T, "partial", "submitted", "completed")); if (timers.length) timers[0][0]();
out.fire = loads.slice();
var S = ["queued", "preparing", "submitted", "completed", "failed", "cancelled", "skipped", "waiting"];
out.parity = [];
S.forEach(function (a) { S.forEach(function (b) {
  var g = genOf(T, "partial", a, b);
  if (takeRealActive(g) !== (takeRealCounts(g).active > 0)) out.parity.push(a + "," + b);
}); });
out.edge = [takeRealActive(null), takeRealActive(genOf("previz", "partial", "submitted", "queued")), takeRealActive({ mode: T })];
console.log(JSON.stringify(out));
"""


def _group_poll(node: str, replace: dict | None = None) -> list[str]:
    bad: list[str] = []
    source = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    funcs = {name: _extract_js(source, name) for name in _POLL_FUNCS}
    for name, text in (replace or {}).items():
        funcs[name] = text
    code = _POLL_HEAD + "\n".join(_js_var(source, n) for n in _POLL_VARS) + "\n" + "\n".join(funcs.values()) + _POLL_CASES
    with tempfile.TemporaryDirectory(prefix="chk221-") as tmp:
        path = Path(tmp) / "poll.js"
        path.write_text(code, encoding="utf-8", newline="\n")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        return ["node 실패: " + (proc.stderr or "")[-600:]]
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    def expect(value, want, what: str) -> None:
        if value != want:
            bad.append(f"{what}\n      결과 {str(value)[:300]}\n      기대 {str(want)[:300]}")

    expect(got["active"], [5000, 3000, 3000, 5000, 5000],
           "만드는 실사 칸이 있으면 gen partial · failed · cancelled 여도 다시 불러옴(submitted 5초 · queued·preparing 3초)")
    expect(got["idle"], [0, 0, 0, 0], "만드는 칸이 없으면(실패 · 완료 · 취소) 멈춤")
    expect(got["same"], [5000, 0, 0], "지금처럼: running(이어 붙이는 중) 5초 · 컷 단위 previz partial 멈춤 · ⑦ 편집 멈춤")
    expect(got["fire"], [[False, True]], "타이머가 loadPlan(false, true) 를 부른다")
    expect([got["parity"], got["edge"]], [[], [False, False, False]],
           "takeRealActive = takeRealCounts(gen).active > 0(통 테이크 · 상태 64쌍) · null · 컷 단위 · 칸 없음은 false")
    return bad


# ================================================================ 실행


def _apply_py(gm, patch: _Patch, kind: str, name: str, payload) -> None:
    current = textwrap.dedent(inspect.getsource(getattr(gm, name)))
    if kind == "pydef":
        if current == payload:
            raise RuntimeError(f"{name} 이 고치기 전과 같습니다(변이가 의미 없음)")
        src = payload
    else:
        old, new = payload
        if current.count(old) != 1:
            raise RuntimeError(f"변이 자리 {current.count(old)}곳(기대 1): {name}: {old[:60]!r}")
        src = current.replace(old, new)
    patch.saved.append((gm, name, getattr(gm, name)))
    code = compile(src, f"<221 {name}>", "exec", flags=_future.annotations.compiler_flag, dont_inherit=True)
    exec(code, gm.__dict__)  # noqa: S102 — 검사 안에서만 옛 동작 함수로 바꾼다(파일은 그대로)


def _run(fn, *args) -> list[str]:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return ["예외: " + traceback.format_exc()[-1200:]]


def check_take_retry_221() -> bool:  # noqa: C901 — 묶음마다 한 곳에서 판정
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import plan as pm  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    no_node = NO_NODE or os.environ.get("CHECK_221_NO_NODE") == "1"
    node = "" if no_node else (shutil.which("node") or "")
    vlog = logging.getLogger("vpoc")
    saved_disabled = vlog.disabled
    vlog.disabled = True  # 드라이버 info 로그를 숨긴다(출력 40줄 안)
    py_groups = {"r-a": _group_retry, "r-b": _group_guard, "r-c": _group_stale}
    results: dict[str, list[str] | None] = {}
    caught: list[str] = []
    try:
        for name, fn in py_groups.items():
            results[name] = _run(fn, gm, pm, VpocError)
        results["r-d"] = _run(_group_ui, node) if node else None
        results["r-g"] = _run(_group_poll, node) if node else None
        js_groups = {"r-d": _group_ui, "r-g": _group_poll}
        # (r-e) 옛 동작 변이마다 그 묶음이 실패해야 한다.
        problems: list[str] = []
        for mname, (desc, group, (kind, target, payload)) in MUTANTS.items():
            if kind == "js":
                if not node:
                    continue
                try:
                    current = _extract_js((PUBLIC / "vnew.js").read_text(encoding="utf-8"), target)
                    if isinstance(payload, tuple):  # (지금 글, 옛 글) — 함수 안 한 곳만 옛 글로
                        if current.count(payload[0]) != 1:
                            raise RuntimeError(f"변이 자리 {current.count(payload[0])}곳(기대 1): {target}: {payload[0][:60]!r}")
                        payload = current.replace(payload[0], payload[1])
                    if current.strip() == payload.strip():
                        raise RuntimeError(f"{target} 이 고치기 전과 같습니다")
                    got = js_groups[group](node, {target: payload})
                except Exception:  # noqa: BLE001
                    problems.append(f"변이 {mname}({desc})를 돌리지 못했습니다: {traceback.format_exc()[-400:]}")
                    continue
            else:
                patch = _Patch()
                try:
                    _apply_py(gm, patch, kind, target, payload)
                    got = _run(py_groups[group], gm, pm, VpocError)
                except Exception:  # noqa: BLE001
                    problems.append(f"변이 {mname}({desc})를 돌리지 못했습니다: {traceback.format_exc()[-400:]}")
                    continue
                finally:
                    patch.undo()
            if got:
                caught.append(f"{mname}→{group}")
            else:
                problems.append(f"옛 동작 변이 {mname}({desc})를 ({group}) 검사가 잡지 못했습니다")
        results["r-e"] = problems
    finally:
        vlog.disabled = saved_disabled

    # (r-f) node --check · LF
    lf: list[str] = []
    for rel in ("app/src/vnew/generate.py", "frontend/public/vnew.js", "tools/check_take_retry_221.py"):
        if b"\r" in (ROOT / rel).read_bytes():
            lf.append(f"{rel} 에 CR 이 있습니다(LF 줄 끝이어야 함)")
    if node:
        proc = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode != 0:
            lf.append("node --check vnew.js 실패: " + (proc.stderr or "")[-400:])
    results["r-f"] = lf

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group)
        if items:
            passed = False
            print(f"[FAIL] 작업 221 ({group}) {title}")
            for item in items[:3]:
                print("    " + item[:700].replace("\n", "\n    "))
        elif items is None or (not node and group in ("r-e", "r-f")):
            print(f"[WARN] 작업 221 ({group}) {title} — node 없음, 화면 검사{'' if items is None else ' 일부'}를 건너뜀")
        else:
            extra = f" ({' '.join(caught)})" if group == "r-e" else ""
            print(f"[ok  ] 작업 221 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__":
    NO_NODE = "--no-node" in sys.argv
    _ok = check_take_retry_221()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
