"""작업 212 검사 — ⑥ 대사 고치기 · 그 대사 구간 영상 다시 만들기(line_redo.py · sound.py · routes.py · vnew.js). 가짜 저장소·가짜
retake·가짜 LLM·가짜 ffmpeg, 유료·네트워크 0.

  (lr-a) 구간: 모델 최소 길이로 늘림 · 조각 안으로 밀기 · 컷 경계 붙이기 · 재료 초(in0·속도) · 너무 짧은 조각 400 · 판 조각 바꾸기
  (lr-b) 프롬프트: 새 대사 · 속마음(입 다묾) · 말투 · 대사만 소리 규칙(작업 211) · retake compose 결과 안 · 옛 대사 → 새 대사
  (lr-c) 저장(대본 그대로) · 견적(compose 바디 · 금액) · 409 CostMismatch(작업 상태 그대로) · 시작(submit 바디 · running)
  (lr-d) 완료 → 새 판(새 키) · 원래 완성본 그대로 · ⑥ 원본 = 새 판 · 줄 시간 그대로 · 목소리 반영 outdated · 두 번째 줄은 첫 판 위에
  (lr-e) 되돌리기: 원래 영상 · 앞 판 · 없는 판 404 · outdated 표시 맞춤 · 파일 그대로
  (lr-f) [대본에도 반영] → 대본 줄 · revision + 1 · 승인 그대로 · 그 컷 pvSig 다름 · 바뀐 테이크만 [take2]
  (lr-g) AI로 다시 쓰기: 후보 3줄(가짜 LLM) · 첫 모델 실패 → 대체 모델 · 원장 1건
  (lr-h) 라우트: 남의 프로젝트 404(6개) · 끔 404 · 소유자는 200
  (lr-i) node --check vnew.js · 화면: 열기 · 저장 · 다시 만들기 단추 상태(node 없으면 WARN)
  (lr-j) LF 줄 끝(새·고친 파일)
  (lr-k) fix1: 대사 다시 만들기 EDIT 틀(구도·카메라 그대로 · 한국어로만 · 영어는 (meaning: …)) · 속마음 · 소리 규칙 1번 ·
         실패한 작업 [다시 시도] = 견적부터 새 틀 · ⑦ AI로 고치기 머리 그대로
  (lr-l) fix2: 기본 모델 = 가장 싼 영상→영상 모델(Seedance 는 다른 모델이 없을 때만 · 같으면 원래 모델) · 모델 고르면 견적 다시 ·
         submit 에 고른 모델 · 콘텐츠 검사 거절 → 다음으로 싼 모델(stepUp, 새 금액 확인 전에는 내지 않음) · 형식별 프롬프트 · 화면 고르기
  (lr-m) fix3: 늘린 구간 안 컷 → 잇기는 컷에서 멈춤(뒤는 원래 프레임·소리 · 20ms 크로스페이드) · 프롬프트 컷 줄 · 줄의 샷 안에서 먼저 늘림 ·
         장면 바뀜으로 콘티 컷 다듬기 · 샷이 최소보다 짧으면 넘음(컷 줄) · rebuild_revision(길이 그대로 · 샷 안만 · 모델 호출 0)

실행: python -X utf8 -B tools/check_line_redo_212.py
"""

from __future__ import annotations

import copy
import json
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
    "lr-a": "구간: 늘림 · 밀기 · 컷 경계 · 재료 초 · 판 조각",
    "lr-b": "프롬프트: 새 대사 · 속마음 · 말투 · 대사만 소리",
    "lr-c": "저장 · 견적 · CostMismatch · 시작",
    "lr-d": "완료 → 새 판 · 옛 영상 그대로 · ⑥ 원본 · outdated",
    "lr-e": "되돌리기",
    "lr-f": "대본에도 반영 → pvSig · 바뀐 테이크만",
    "lr-g": "AI로 다시 쓰기 3줄",
    "lr-h": "라우트 소유자 검사",
    "lr-i": "node --check · 단추 상태",
    "lr-j": "LF 줄 끝",
    "lr-k": "fix1: 대사 틀(구도 그대로 · 한국어로만 · 뜻) · 소리 규칙 1번 · 다시 시도 = 새 견적 · ⑦ 그대로",
    "lr-l": "fix2: 가장 싼 모델 기본 · Seedance 마지막 · 고르면 다시 견적 · 고른 모델 제출 · 거절 → 확인 뒤 다음 모델",
    "lr-m": "fix3: 잇기는 줄의 샷 안만 · 컷 줄 · 샷 안에서 먼저 늘림 · 짧은 샷은 넘음 · rebuild_revision",
}
SEED, KLING, WAN = ("bytedance/seedance-2.5/reference-to-video", "fal-ai/kling-video/o3/standard/video-to-video/edit",
                    "alibaba/wan-3.0/reference-to-video")
PRICES = {"fal:edit": (1.23, "Kling Edit"), SEED: (0.82, "Seedance 2.5"), KLING: (0.42, "Kling O3 Edit"), WAN: (0.20, "Wan 3.0")}


def _row(model: str, usd, *, original: bool = False, fmt: str = "reference", ok: bool = True, path: str = "take") -> dict:
    """retake 모델 줄(_choices 모양) — 구간 견적 range."""
    return {"id": model, "label": PRICES.get(model, (0, model))[1], "original": original, "path": path, "format": fmt,
            "range": {"ok": ok, "usd": usd}, "full": {"ok": False}}


SAMPLE_ROWS = [_row(SEED, 0.82, original=True), _row(KLING, 0.42, fmt="elements", path="v2v"), _row(WAN, 0.20)]
OWNER, PID = "tester", "prj_lr"
PUBLIC = ROOT / "frontend" / "public"
FILES = [ROOT / "app" / "src" / "vnew" / n for n in ("line_redo.py", "sound.py", "routes.py", "retake.py")] + \
        [PUBLIC / "vnew.js", PUBLIC / "vnew.css", TOOLS / "check_line_redo_212.py"]


def _fake(dur: float, *, audio: bool = True, w: int = 160, h: int = 120, fps: int = 24) -> bytes:
    return f"FAKE:dur={dur};audio={int(audio)};w={w};h={h};fps={fps}".encode()


def _probe_bytes(path: str) -> dict:
    with open(path, "rb") as handle:
        head = handle.read(120)
    if not head.startswith(b"FAKE:"):
        return {"duration": 0.0, "hasAudio": False, "width": 0, "height": 0, "fps": 0}
    fields = dict(part.split("=", 1) for part in head[5:].decode().split(";"))
    return {"duration": float(fields["dur"]), "hasAudio": fields["audio"] == "1", "width": int(fields["w"]),
            "height": int(fields["h"]), "fps": float(fields["fps"])}


class _Store:
    """메모리 저장소(조건부 쓰기 흉내)."""

    def __init__(self) -> None:
        self.json: dict[str, tuple[dict, str]] = {}
        self.blobs: dict[str, bytes] = {}
        self.n = 0

    def read_json(self, key: str):
        item = self.json.get(key)
        return (copy.deepcopy(item[0]), item[1]) if item else (None, "")

    def write_json(self, key: str, data: dict, *, etag: str = "", create_only: bool = False) -> str:
        from src.vpoc.errors import VpocError  # noqa: PLC0415

        current = self.json.get(key)
        if (create_only and current) or (etag and (not current or current[1] != etag)):
            raise VpocError("store", "PreconditionFailed", "conflict", 409)
        self.n += 1
        self.json[key] = (json.loads(json.dumps(data)), f"e{self.n}")
        return f"e{self.n}"

    def put_bytes(self, key: str, body: bytes, content_type: str, *, bucket: str = "") -> None:
        self.blobs[key] = bytes(body)

    def get_bytes(self, key: str, *, bucket: str = "") -> bytes:
        return self.blobs[key]

    def presign(self, key: str, **kwargs) -> str:
        return "https://signed.example/" + key

    def exists(self, key: str, **kwargs) -> bool:
        return key in self.blobs or key in self.json


def _key(name: str) -> str:
    return f"users/{OWNER}/video/{PID}/vnew/gen/g1/{name}"


def _sound_plan() -> dict:
    from src.vnew import plan as planmod  # noqa: PLC0415

    plan = planmod.new_plan()
    plan["revision"] = 7
    plan["previz"]["approved"] = True
    plan["script"] = {"status": "ready", "setting": {}, "entries": [
        {"id": "e1", "panels": [1], "speaker": "민지", "lineKo": "안녕", "voice": "spoken"},
        {"id": "e2", "panels": [2], "speaker": "준호", "lineKo": "설마…", "voice": "thought"}]}
    plan["conti"] = {"status": "ready", "segments": [{"id": "s1", "cutIds": ["c1", "c2"], "seconds": 4}],
                     "cuts": [{"id": "c1", "no": 1, "panels": [1], "seconds": 2, "characters": [{"name": "민지"}]},
                              {"id": "c2", "no": 2, "panels": [2], "seconds": 2, "characters": [{"name": "준호"}]}]}
    cell = lambda key: {"status": "completed", "outputKey": key, "attempt": 1, "invocationArn": "", "error": ""}  # noqa: E731
    plan["gen"] = {"genId": "g1", "mode": "previz", "status": "done", "createdAt": "2026-11-10T00:00:00Z", "segments": [],
                   "units": [{"id": c, "cutId": c, "recSegId": "s1", "seconds": 2.0, "webtoon": cell(_key(f"{c}-webtoon-1.mp4")),
                              "real": {"status": "failed"}} for c in ("c1", "c2")],
                   "finalWebtoon": {"status": "done", "key": _key("final-webtoon.mp4"), "error": ""},
                   "finalReal": {"status": "failed", "key": "", "error": "x"}, "selected": "webtoon", "costUsd": 0.0}
    return plan


# ---------------------------------------------------------------- 206 고정물(lr-f): 컷 13개, take1 = 컷 1–6, take2 = 컷 7–13
def _take_plan() -> dict:
    cuts, scenes = [], []
    for i in range(1, 14):
        cuts.append({"id": f"c{i}", "no": i, "seconds": 4.0, "panels": [i], "locationEn": "A quiet office" if i <= 6 else "A rainy street",
                     "characters": [{"name": "민수", "actionEn": "looks at the door"}], "situationKo": f"상황 {i}", "dialogueKo": ""})
    for loc_id, ids in (("loc-office", range(1, 7)), ("loc-street", range(7, 14))):
        timeline = [{"cutId": f"c{i}", "start": 4.0 * (i - 1), "end": 4.0 * i,
                     "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 54.4, "lensMm": 35},
                     "actors": [{"id": "a1", "pos": [0, 0, 0], "facingDeg": 0}]} for i in ids]
        scenes.append({"locationId": loc_id, "actors": [{"id": "a1", "name": "민수", "charId": "ch1", "color": "#4363d8"}],
                       "timeline": timeline})
    segs = [{"id": "s1", "cutIds": ["c1", "c2", "c3"], "seconds": 12.0}, {"id": "s2", "cutIds": ["c4", "c5", "c6"], "seconds": 12.0},
            {"id": "s3", "cutIds": [f"c{i}" for i in range(7, 14)], "seconds": 12.0}]
    recordings = {s["id"]: {"status": "ready", "key": f"previz/3/{s['id']}.mp4", "seconds": 12.0} for s in segs}
    previz = {"revision": 3, "approved": True, "recordedAll": True, "recordings": recordings, "scenes": scenes}
    entries = [{"id": "e8", "panels": [8], "speaker": "민수", "lineKo": "문 닫아.", "voice": "spoken"},
               {"id": "e2", "panels": [2], "speaker": "민수", "lineKo": "누구야?", "voice": "spoken"}]
    return {"planId": "pl212", "revision": 3, "conti": {"status": "ready", "cuts": cuts, "segments": segs}, "previz": previz,
            "script": {"status": "ready", "entries": entries}, "input": {"videoModel": "fal:take-model"}}


def _done(gen: dict) -> dict:
    g = copy.deepcopy(gen)
    for unit in g["units"]:
        unit["webtoon"].update(status="completed", outputKey=f"out/{g['genId']}/{unit['id']}-web.mp4")
    g["status"] = "done"
    g["finalWebtoon"] = {"status": "done", "key": f"out/{g['genId']}/final-web.mp4", "error": ""}
    return g


def _extract_js(source: str, name: str) -> str:
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


_UI_JS = r"""
function Node(tag) { this.tag = tag; this.children = []; this.style = {}; this.className = ""; this._text = ""; this.value = "";
  this.listeners = {}; this.parentNode = null; this.disabled = false; this.attrs = {}; this.checked = false; var self = this;
  this.classList = { add: function (c) { if ((" " + self.className + " ").indexOf(" " + c + " ") < 0) self.className += " " + c; },
    remove: function (c) { self.className = (" " + self.className + " ").replace(" " + c + " ", " ").trim(); } }; }
Object.defineProperty(Node.prototype, "textContent", {
  get: function () { return this._text + this.children.map(function (c) { return c.textContent; }).join(""); },
  set: function (v) { this._text = String(v); this.children = []; } });
Object.defineProperty(Node.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
Node.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
Node.prototype.removeChild = function (c) { this.children = this.children.filter(function (x) { return x !== c; }); c.parentNode = null; return c; };
Node.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
var document = { createElement: function (t) { return new Node(t); } };
function walk(n, out) { out.push(n); n.children.forEach(function (c) { walk(c, out); }); return out; }
function find(root, cls) { return walk(root, []).filter(function (n) { return (" " + n.className + " ").indexOf(" " + cls + " ") >= 0; })[0]; }
var SOUND_VOICES = [["spoken", "실제 말"], ["thought", "속마음"]];
var state = {}, window = { confirm: function () { return true; } };
function renderPlan() {}
var out = {};
out.jobs = [lrdJobText(null).text, lrdJobText({ status: "quoted", usd: 1.23, modelLabel: "Kling" }).text,
  lrdJobText({ status: "running", usd: 1.23, modelLabel: "Kling" }).text, lrdJobText({ status: "failed", error: "거절" })];
var info = { enabled: true, cast: ["민지", "준호"], lines: {}, limits: { textMax: 200, deliveryMax: 80 }, takeRedoText: "⑤ 안내" };
var ctx = { view: { lineRedo: info }, variant: "webtoon" };
var line = { id: "e1", speaker: "민지", lineKo: "안녕", voice: "spoken" };
var row = new Node("div"), lineCell = new Node("span"), cell = new Node("span");
lrdRowTools(ctx, line, row, lineCell, cell);
var open = find(cell, "vnew-lrd-open");
out.closed = [open.textContent, !!find(row, "vnew-lrd-editor"), lineCell.textContent];
open.listeners.click[0]();
var ed = ctx.lrd.rows[0].editor;
function snap() { return { open: open.textContent, save: ed.save.disabled, redo: ed.redo.disabled, label: ed.redo.textContent,
  take: ed.take.style.display, hint: ed.hint.textContent, text: ed.text.value, speaker: ed.speaker.value, voice: ed.voice.value }; }
out.opened = snap();
ed.text.value = ""; ed.text.listeners.input[0]();
out.empty = snap();
ed.text.value = "잘 가"; ed.text.listeners.input[0]();
out.typed = snap();
info.lines.e1 = { override: { lineId: "e1", textKo: "잘 가", speaker: "민지", thought: false, deliveryKo: "작게",
  original: { lineKo: "안녕" }, scriptApplied: false } };
ed.form.dirty = false; lrdPaintRow(ctx, ctx.lrd.rows[0]);
out.saved = snap();
out.lineCell = [find(lineCell, "vnew-lrd-original").textContent, find(lineCell, "vnew-lrd-new").textContent];
info.lines.e1.job = { status: "quoted", usd: 1.23, modelLabel: "Kling" };
lrdPaintRow(ctx, ctx.lrd.rows[0]);
out.quoted = snap();
info.lines.e1.job = { status: "running", usd: 1.23, modelLabel: "Kling" };
lrdPaintRow(ctx, ctx.lrd.rows[0]);
out.running = snap();
out.status = ctx.lrd.rows[0].status.textContent;
info.lines.e1.job = { status: "done", usd: 1.23 }; info.lines.e1.override.scriptApplied = true;
lrdPaintRow(ctx, ctx.lrd.rows[0]);
out.applied = snap();
out.changed = [lrdChanged({ lineRedo: { busy: true, current: 0 }, source: { label: "a" } }, { lineRedo: { busy: true, current: 0 }, source: { label: "a" } }),
  lrdChanged({ lineRedo: { busy: true, current: 0 } }, { lineRedo: { busy: false, current: 1 } })];
console.log(JSON.stringify(out));
"""

# 작업 212 fix2 화면: 확인 창 모델 고르기(라디오) · 고르면 pick · 시작 단추 금액 · 거절 뒤 안내 · 견적 바디
_UI_JS_L = r"""
var out = {};
var job = { status: "quoted", model: "W", modelLabel: "Wan 3.0", usd: 0.2, range: { start: 0, end: 3 }, stepUp: null,
  models: [{ id: "W", label: "Wan 3.0", usd: 0.2, original: false }, { id: "K", label: "Kling O3 Edit", usd: 0.42, original: false },
           { id: "S", label: "Seedance 2.5", usd: 0.82, original: true }] };
var ed = { box: new Node("div") }, picks = [], starts = 0;
var on = { pick: function (id) { picks.push(id); }, start: function () { starts += 1; }, cancel: function () {} };
var line = { id: "e1", speaker: "민지" };
lrdConfirmPaint(ed, line, job, on, false);
var radios = walk(ed.confirm, []).filter(function (n) { return n.type === "radio"; });
out.radios = radios.map(function (r) { return [r.value, r.checked, r.name, r.parentNode.textContent]; });
radios[1].listeners.change[0](); radios[0].listeners.change[0]();
find(ed.confirm, "vnew-lrd-go").listeners.click[0]();
out.picks = picks; out.starts = starts; out.go = find(ed.confirm, "vnew-lrd-go").textContent; out.stepup = !!find(ed.confirm, "vnew-lrd-stepup");
job = Object.assign({}, job, { model: "K", modelLabel: "Kling O3 Edit", usd: 0.42, stepUp: { fromModel: "W", fromLabel: "Wan 3.0" } });
lrdConfirmPaint(ed, line, job, on, false);
out.up = [find(ed.confirm, "vnew-lrd-stepup").textContent, find(ed.confirm, "vnew-lrd-go").textContent, ed.box.children.length];
lrdConfirmPaint(ed, line, job, on, true);
out.busy = [find(ed.confirm, "vnew-lrd-go").disabled, walk(ed.confirm, []).filter(function (n) { return n.type === "radio"; })[0].disabled];
lrdConfirmClose(ed);
out.closed = [ed.confirm, ed.box.children.length];
out.body = [lrdQuoteBody({ variant: "real" }, "e1", ""), lrdQuoteBody({ variant: "real" }, "e1", "K")];
console.log(JSON.stringify(out));
"""


def check_line_redo_212() -> bool:  # noqa: C901 — 한 검사 함수(다른 작업 검사와 같은 모양)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1000:])

    from src.vnew import edit as editmod  # noqa: PLC0415
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import line_redo as lr  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vnew import sound as snd  # noqa: PLC0415
    from src.vpoc import ledger as ledger_mod  # noqa: PLC0415
    from src.vpoc import settings as vpoc_set  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def err_of(fn) -> list:
        try:
            fn()
            return ["통과"]
        except VpocError as exc:
            return [exc.kind, exc.status]

    # ================= (lr-a) 구간 =================
    try:
        pieces = [{"t0": 0.0, "t1": 6.0, "sourceKey": "k1", "in0": 0.0, "rate": 1.0, "cuts": [[0.0, 2.5, "c1"], [2.5, 6.0, "c2"]]},
                  {"t0": 6.0, "t1": 10.0, "sourceKey": "k2", "in0": 1.5, "rate": 2.0, "cuts": []}]
        pick = lambda r: [r["start"], r["end"], r["piece"], r["in"], r["out"], r["padded"], r["snapped"]]  # noqa: E731
        expect("lr-a", lr.MIN_SEGMENT_S, 3.0, "모델 최소 길이 = generate.PREVIZ_MIN_CLIP_S(3초)")
        expect("lr-a", pick(lr.line_range(1.0, 1.8, pieces)), [0.0, 3.0, 0, 0.0, 3.0, True, [False, False]],
               "0.8초 줄 → 가운데 기준 3초로 늘리고 조각 시작(0초) 안으로 민다")
        expect("lr-a", pick(lr.line_range(2.7, 5.9, pieces)), [2.5, 6.0, 0, 2.5, 6.0, False, [True, True]],
               "경계가 0.35초 안: 시작 → 컷 경계 2.5 · 끝 → 조각 끝 6.0")
        expect("lr-a", pick(lr.line_range(3.0, 6.2, pieces))[:3], [3.0, 6.0, 0], "두 조각에 걸치면 더 많이 겹치는 조각 안으로")
        expect("lr-a", pick(lr.line_range(7.0, 9.0, pieces)), [6.5, 9.5, 1, 2.5, 8.5, True, [False, False]],
               "재료 초 = in0 + (시각 − 조각 시작) × 속도")
        expect("lr-a", pick(lr.line_range(0.5, 1.0, [{"t0": 0, "t1": 2, "sourceKey": "k"}])), [0.0, 2.0, 0, 0.0, 2.0, True, [False, False]],
               "조각이 최소 길이보다 짧으면 조각 전체")
        expect("lr-a", err_of(lambda: lr.line_range(0.1, 0.3, [{"t0": 0, "t1": 0.5, "sourceKey": "k"}])), ["LineRangeShort", 400],
               "1초보다 짧은 조각 → 400")
        swapped = lr._replace_piece(pieces, {"start": 5.0, "end": 7.0}, "new")  # noqa: SLF001
        expect("lr-a", [[p["t0"], p["t1"], p["sourceKey"], p["in0"]] for p in swapped],
               [[0.0, 5.0, "k1", 0.0], [5.0, 7.0, "new", 0.0], [7.0, 10.0, "k2", 3.5]], "판 조각: 바꾼 구간만 새 결과 · 뒤 조각 재료 초 옮김")
    except Exception as exc:  # noqa: BLE001
        crash("lr-a", exc)

    # ================= (lr-b) 프롬프트 =================
    try:
        spoken = {"speaker": "민지", "textKo": "잘 가", "thought": False, "deliveryKo": "작게, 떨리는 목소리"}
        inner = {"speaker": "준호", "textKo": "설마 그럴 리가", "thought": True, "deliveryKo": ""}
        req_s, req_t = lr.redo_request(spoken), lr.redo_request(inner)
        edit_s = lr.redo_edit_lines(spoken, 'Change Minji\'s line to "Bye." softly.', 4000)
        edit_t = lr.redo_edit_lines(inner, "", 4000)
        expect("lr-b", ['"잘 가"' in req_s, "작게, 떨리는 목소리" in req_s, "입은 다문 채" in req_t, len(edit_s), len(edit_t),
                        "'작게, 떨리는 목소리'" in edit_s[0], edit_s[1], edit_t[1], lr.LINE_INNER in edit_t[0],
                        "mouth stays closed" in edit_t[0], "(meaning:" in edit_t[0]],
               [True, True, True, 2, 2, True, gm.AUDIO_DIALOGUE_ONLY_SPOKEN, gm.AUDIO_DIALOGUE_ONLY, True, True, False],
               "수정 사항(한국어) · EDIT 한 줄 + 소리 규칙 한 줄(실제 말 = inner 없는 형 · 속마음 = inner 형) · 말투 · 번역 없으면 뜻 없음")
        long = dict(spoken, textKo="가" * lr.TEXT_MAX, deliveryKo="나" * lr.DELIVERY_MAX)
        edit_l = lr.redo_edit_lines(long, "", 1500)
        expect("lr-b", ["가" * lr.TEXT_MAX in edit_l[0], edit_l[1]], [True, gm.AUDIO_DIALOGUE_ONLY_COMPACT],
               "긴 대사: 새 대사 그대로 · 작은 상한이면 소리 규칙 짧은 형")
        base = ("SCENE: A quiet office at night.\nDIALOGUE: 준호 (inner voice): \"설마…\" — mouth stays closed.\n"
                + gm.AUDIO_DIALOGUE_ONLY)
        template = "@Video1 is the shot to edit. " + gm.AUDIO_DIALOGUE_ONLY
        prompt, _meta = rt._compose_prompt(base, template, {"start": 0.5, "end": 3.5, "in": 0.5, "out": 3.5}, 6.0,  # noqa: SLF001
                                           request_ko=req_t, extra_ko="", change_en="", max_bytes=4000, result=True, edit_lines=edit_t)
        final = lr._finish_prompt(prompt, "설마…", inner["textKo"], 4000)  # noqa: SLF001
        expect("lr-b", ['"설마 그럴 리가"' in final, "mouth stays closed" in final, lr.audio_rule_count(final), "설마…" in final,
                        final.split("\n")[0].startswith(lr.LINE_EDIT_HEAD + "준호."), any(l.startswith("EXTRA:") for l in final.split("\n"))],
               [True, True, 1, False, True, False], "retake compose 프롬프트: 머리 EDIT = 대사 틀 · 소리 규칙 1번 · EXTRA 없음 · 옛 대사 없음")
        expect("lr-b", lr._retarget("x 안녕 y", "안녕", "잘 가" * 50, 10), "x 안녕 y", "바이트 상한을 넘으면 초안 그대로")  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        crash("lr-b", exc)

    # ================= (lr-k) fix1: 대사 다시 만들기 프롬프트 틀 · 소리 규칙 한 번 · ⑦ 그대로 =================
    try:
        rng0 = {"start": 0.5, "end": 3.5, "in": 0.5, "out": 3.5}
        en = 'Change Minji\'s line to "Bye, take care." and match lip-sync.'
        base_k = ("SCENE: An office at night.\nDIALOGUE: 민지: \"안녕\" (lip sync).\nRULES: Keep faces. " + gm.AUDIO_DIALOGUE_ONLY_SPOKEN
                  + "\n" + gm.AUDIO_DIALOGUE_ONLY_COMPACT)
        tmpl_k = "@Video1 is the shot to edit. " + gm.AUDIO_DIALOGUE_ONLY_COMPACT

        def build(override, base_text, change_en=en, max_bytes=4000):
            lines = lr.redo_edit_lines(override, change_en, max_bytes)
            p, _m = rt._compose_prompt(base_text, tmpl_k, rng0, 6.0, request_ko=lr.redo_request(override), extra_ko="",  # noqa: SLF001
                                       change_en=change_en, max_bytes=max_bytes, result=True, edit_lines=lines)
            return lr._finish_prompt(p, "안녕", override["textKo"], max_bytes)  # noqa: SLF001

        sp = {"speaker": "민지", "textKo": "잘 가", "thought": False, "deliveryKo": ""}
        fk = build(sp, base_k)
        first = fk.split("\n")[0]
        no_meaning = re.sub(r"\(meaning: [^()]*\)", "", fk)
        expect("lr-k", ["even where it changes framing" in fk, first.startswith(lr.LINE_EDIT_HEAD + "민지. " + lr.LINE_EDIT_KEEP),
                        'in Korean: "잘 가" (meaning: Bye, take care).' in first, "never speak English" in first, fk.count("Bye, take care"),
                        "Bye" in no_meaning, "Korean original" in fk, lr.audio_rule_count(fk), "안녕" in fk, "Keep faces." in fk],
               [False, True, True, True, 1, False, False, 1, False, True],
               "실제 말: 고정 머리(구도 그대로) · 한국어 따옴표 · never speak English · 영어는 (meaning: …) 안에만 · 소리 규칙 1번 · 옛 대사 없음")
        th = {"speaker": "민지", "textKo": "잘 가", "thought": True, "deliveryKo": "속삭이듯"}
        ft = build(th, base_k)
        expect("lr-k", [lr.LINE_INNER in ft, "the mouth stays closed; the Korean line is heard as an inner voice." in ft,
                        "in Korean" in ft, lr.audio_rule_count(ft), gm.AUDIO_DIALOGUE_ONLY in ft, "'속삭이듯'" in ft],
               [True, True, True, 1, True, True], "속마음: 입 다묾 · 속마음 목소리 · 소리 규칙 1번(inner 형) · 말투(한국어 따옴표)")
        bare = "SCENE: An office at night.\nDIALOGUE: 민지: \"안녕\"."
        prev = build(sp, base_k) + "\n" + gm.AUDIO_DIALOGUE_ONLY_COMPACT  # 앞선 대사 다시 만들기 결과를 다시 고칠 때의 기준
        expect("lr-k", [lr.audio_rule_count(build(sp, bare)), lr.audio_rule_count(build(sp, prev)),
                        sum(l.startswith("EDIT:") for l in build(sp, prev).split("\n")), lr.audio_rule_count(build(sp, base_k, en, 1500)),
                        "(meaning:" in build(sp, base_k, "Change the line.")],
               [1, 1, 1, 1, False], "기준에 규칙 없음 · 앞선 대사 고침 결과(EDIT·규칙 겹침) · 짧은 형 → 모두 1번 · 따옴표 없는 번역 → 뜻 없음")
        seven = rt._compose_prompt(base_k, tmpl_k, rng0, 6.0, request_ko="표정을 밝게", extra_ko="", change_en="Make her smile.",  # noqa: SLF001
                                   max_bytes=4000, result=True)
        seven_none = rt._compose_prompt(base_k, tmpl_k, rng0, 6.0, request_ko="표정을 밝게", extra_ko="",  # noqa: SLF001
                                        change_en="Make her smile.", max_bytes=4000, result=True, edit_lines=None)
        expect("lr-k", [seven == seven_none, seven[0].startswith(rt.COMPOSE_RESULT_EDIT_HEAD), "even where it changes framing" in seven[0]],
               [True, True, True], "⑦ AI로 고치기: edit_lines 없으면 지금 머리 그대로(구도가 바뀌어도 끝까지 적용)")
    except Exception as exc:  # noqa: BLE001
        crash("lr-k", exc)

    # ================= (lr-l) fix2: 형식마다(reference @Video1 r2v · elements Kling O3 edit) 같은 EDIT 틀 · 규칙 통과 =================
    try:
        sp_l = {"speaker": "민지", "textKo": "잘 가", "thought": False, "deliveryKo": ""}
        base_l = "SCENE: An office at night.\nDIALOGUE: 민지: \"안녕\"."
        rng_l = {"start": 0.5, "end": 3.5, "in": 0.5, "out": 3.5}
        fit = []
        for fmt, tmpl in (("reference", "@Video1 is the shot to edit. @Image1 is 민지 (face reference). " + gm.AUDIO_DIALOGUE_ONLY),
                          ("elements", "@Video1 is the shot to edit. @Element1 is 민지. " + gm.AUDIO_DIALOGUE_ONLY)):
            lines_l = lr.redo_edit_lines(sp_l, 'Change the line to "Bye."', 4000)
            p_l, meta_l = rt._compose_prompt(base_l, tmpl, rng_l, 6.0, request_ko=lr.redo_request(sp_l), extra_ko="",  # noqa: SLF001
                                             change_en='Change the line to "Bye."', max_bytes=4000, result=True, edit_lines=lines_l)
            f_l = lr._finish_prompt(p_l, "안녕", "잘 가", 4000)  # noqa: SLF001
            rules_l = {"maxBytes": 4000, "real": True, "refs": meta_l["refs"], "labels": meta_l["labels"], "against": "template"}
            fit.append([fmt in rt.V2V_FORMATS, rt._refs(" ".join(lines_l)), rt.check_prompt(f_l, base_l, rules_l),  # noqa: SLF001
                        f_l.startswith(lr.LINE_EDIT_HEAD), lr.audio_rule_count(f_l)])
        expect("lr-l", fit, [[True, {"@Video1"}, [], True, 1]] * 2,
               "형식마다 retake._compose_prompt 그대로: EDIT 틀은 @Video1 만 · 형식 참고 표시 그대로(규칙 문제 0) · 소리 규칙 1번")
    except Exception as exc:  # noqa: BLE001
        crash("lr-l", exc)

    # ================= (lr-m) fix3: 잇기는 줄의 샷 안만 · 컷 줄 · 샷 안에서 먼저 늘림 =================
    try:
        pick_m = lambda r: [r["start"], r["end"], r["padded"], r["cuts"], r["splice"], r["shot"]]  # noqa: E731
        prod = [{"t0": 0.0, "t1": 10.0, "sourceKey": "k", "in0": 0.0, "rate": 1.0, "cuts": [[0.0, 5.0, "a"], [5.0, 10.0, "b"]]}]
        expect("lr-m", [pick_m(lr.line_range(3.6, 5.17, prod, scenes=[5.2, 8.9])), pick_m(lr.line_range(3.6, 5.17, prod))],
               [[2.2, 5.2, True, [], [2.2, 5.2], [0.0, 5.2]], [2.0, 5.0, True, [], [2.0, 5.0], [0.0, 5.0]]],
               "prod(e3): 줄의 샷(5.2초까지) 안에서 늘림 — 2.886–5.886 처럼 컷을 넘지 않음 · 장면 바뀜(5.2)이 콘티 컷(5.0)을 다듬음 · "
               "콘티에서 먼 장면 바뀜(8.9)은 무시 · 검사 없으면 콘티만")
        short = [{"t0": 0.0, "t1": 10.0, "sourceKey": "k", "in0": 1.0, "rate": 1.0,
                  "cuts": [[0.0, 4.0, "a"], [4.0, 6.0, "b"], [6.0, 10.0, "c"]]}]
        r_s = lr.line_range(4.5, 5.9, short)
        sp_m = {"speaker": "민지", "textKo": "잘 가", "thought": False, "deliveryKo": ""}
        lines_m = lr.redo_edit_lines(sp_m, "", 4000, r_s["cutsAt"])
        hard = ("@Video1 has a hard cut at 2 s to a different shot; keep that cut and the second shot exactly as in @Video1.")
        p_m, _meta_m = rt._compose_prompt("SCENE: x.\nDIALOGUE: 민지: \"안녕\".", "@Video1 is the shot to edit. " + gm.AUDIO_DIALOGUE_ONLY,  # noqa: SLF001
                                          {"start": 3.0, "end": 6.0, "in": 3.0, "out": 6.0}, 10.0, request_ko=lr.redo_request(sp_m),
                                          extra_ko="", change_en="", max_bytes=4000, result=True, edit_lines=lines_m)
        f_m = lr._finish_prompt(p_m, "안녕", "잘 가", 4000)  # noqa: SLF001
        expect("lr-m", [pick_m(r_s), r_s["in"], r_s["cutsAt"], len(lines_m), lines_m[-1], hard in f_m, lr.audio_rule_count(f_m),
                        len(lr.redo_edit_lines(sp_m, "", 4000))],
               [[4.0, 7.0, True, [6.0], [4.0, 6.0], [4.0, 6.0]], 5.0, [2.0], 3, hard, True, 1, 2],
               "샷(2초)이 최소보다 짧음 → 샷 전체 + 이웃 샷 쪽으로 넘음 · 잇기는 컷(6.0)에서 멈춤 · @Video1 초의 컷 줄(EDIT 줄들 뒤) · 컷 없으면 줄 그대로")
        expect("lr-m", [lr._splice_in(2.886, 5.886, 3.6, 5.17, lr.shot_bounds(prod[0], [5.2]), 1.0)["splice"],  # noqa: SLF001
                        lr._splice_in(2.886, 5.886, 3.6, 4.5, lr.shot_bounds(prod[0], [5.2]), 1.0)["splice"],  # noqa: SLF001
                        lr._splice_in(2.886, 5.886, 3.6, 5.17, lr.shot_bounds(prod[0], [5.2]), 1.0)["cuts"]],  # noqa: SLF001
               [[2.886, 5.2], [2.886, 4.75], [5.2]], "저장된 구간 2.886–5.886: 잇기 = 컷(5.2)까지 · 컷이 멀면 줄 끝 + 0.25초 · 구간 안 컷")
        import array as _array  # noqa: PLC0415

        rec: dict = {"seg": [], "join": None, "audio": []}

        def f_seg(source, video, *, start_sec, frames, fps, width, height, **kw):
            rec["seg"].append([Path(source).stem, round(start_sec, 4), frames])
            Path(video).write_bytes(b"v")

        def f_audio(source, out, *, start_sec, samples, **kw):
            rec["audio"].append([Path(source).stem, samples])
            value = 1000 if Path(source).stem == "base" else -1000
            Path(out).write_bytes(_array.array("h", [value] * (samples * 2)).tobytes())

        def f_join(videos, out, *, audio_path=""):
            rec["join"] = _array.array("h", Path(audio_path).read_bytes())

        from src import stitcher as st  # noqa: PLC0415

        saved_st = {n: getattr(st, n) for n in ("edit_segment", "edit_audio", "edit_join")}
        st.edit_segment, st.edit_audio, st.edit_join = f_seg, f_audio, f_join
        try:
            with tempfile.TemporaryDirectory(prefix="chk212m-") as tmp:
                base_p, new_p = str(Path(tmp) / "base.mp4"), str(Path(tmp) / "new.mp4")
                Path(base_p).write_bytes(b"b")
                Path(new_p).write_bytes(b"n")
                lr._splice_file(base_p, new_p, str(Path(tmp) / "out.mp4"), start=2.886, end=5.886, duration=10.0, width=160,  # noqa: SLF001
                                height=120, fps=24, base_audio=True, new_audio=True, splice_start=2.886, splice_end=5.2)
                pcm = rec["join"]
                a_s, b_s = 69 * 2000, 125 * 2000  # 프레임 69 · 125(= 5.2초 반올림) × 48000/24
                at = lambda i: pcm[2 * i]  # noqa: E731
                expect("lr-m", [rec["seg"], sum(s[2] for s in rec["seg"]), rec["audio"], len(pcm) // 2,
                                [at(a_s - 1), at(a_s + 960), at(b_s - 481), at(b_s + 480), at(479999), abs(at(a_s + 480)) < 20,
                                 abs(at(b_s)) < 20, at(a_s + 200) > 0 > at(a_s + 700), at(b_s - 200) < 0 < at(b_s + 200)]],
                       [[["base", 0.0, 69], ["new", 0.0, 56], ["base", 5.2083, 115]], 240, [["base", 480000], ["new", 144000]], 480000,
                        [1000, -1000, -1000, 1000, 1000, True, True, True, True]],
                       "잇기 파일: 원본 앞 · 새 영상(구간 시작부터 컷까지) · 컷 뒤는 원본 프레임 · 프레임 수 그대로 · 소리 = 컷 뒤 원본 · "
                       "이음매마다 20ms 크로스페이드(구간 시작 = 새 영상 0초면 이음매 뒤로 민다)")
                rec.update(seg=[], audio=[])
                lr._splice_file(base_p, new_p, str(Path(tmp) / "out2.mp4"), start=2.886, end=5.886, duration=10.0, width=160,  # noqa: SLF001
                                height=120, fps=24, base_audio=True, new_audio=False, splice_start=2.886, splice_end=5.2)
                expect("lr-m", [rec["audio"], set(rec["join"])], [[["base", 480000]], {1000}], "새 영상에 소리가 없으면 원래 소리 그대로")
        finally:
            for n, fn in saved_st.items():
                setattr(st, n, fn)
    except Exception as exc:  # noqa: BLE001
        crash("lr-m", exc)

    # ================= (lr-c ~ lr-h) 가짜 저장소 =================
    store = _Store()
    calls: dict[str, list] = {"compose": [], "submit": [], "splice": [], "llm": [], "charge": [], "spawn": []}
    retake_state: dict = {"v": 1, "cells": {}}
    llm_plan: dict = {"fail": set()}

    def fake_compose(owner, pid, mid, payload, edit_lines=None):
        lines = edit_lines('Change the line to "Bye." and match lip-sync.', 3000) if edit_lines else ["EDIT: Edit @Video1. " + payload["requestKo"]]
        prompt = "\n".join(lines + ["@Video1 is the current result video of this shot (not a previz): keep it as the base.",
                                    rt.COMPOSE_CONTEXT_LINE, "CONTEXT: 민지 says \"안녕\". 준호 thinks \"설마…\"", gm.AUDIO_DIALOGUE_ONLY])
        calls["compose"].append({"mid": mid, **payload, "prompt": prompt})
        cell = "webtoon:c1" if mid == editmod.media_id(_key("c1-webtoon-1.mp4")) else "webtoon:c2"
        model = payload.get("model") or "fal:edit"
        usd, label = PRICES[model]
        return {"cellKey": cell, "draft": {"id": f"d{len(calls['compose'])}", "model": model, "modelLabel": label,
                                           "maxBytes": 3000, "plan": {"usd": usd, "sentSeconds": 3.0}, "prompt": prompt}}

    def fake_submit(owner, pid, mid, payload):
        calls["submit"].append({"mid": mid, **payload})
        usd = PRICES[payload.get("model") or "fal:edit"][0]
        if round(float(payload.get("confirmUsd") or 0), 2) != usd:
            raise VpocError("vnew.retake.submit", "CostMismatch", f"금액이 서버 계산과 다릅니다. 서버 {usd} USD.", 409)
        cell = "webtoon:c1" if mid == editmod.media_id(_key("c1-webtoon-1.mp4")) else "webtoon:c2"
        return {"cellKey": cell, "live": {"n": 2, "status": "queued", "attempt": 1}}

    def fake_splice(base_path, new_path, out_path, **kw):
        with open(base_path, "rb") as h1, open(new_path, "rb") as h2:
            calls["splice"].append({"base": h1.read(60), "new": h2.read(60), **kw})
        with open(out_path, "wb") as handle:
            handle.write(_fake(kw["duration"], w=kw["width"], h=kw["height"], fps=kw["fps"]))

    def fake_llm(*, system_text, blocks, model):
        calls["llm"].append({"model": model, "text": blocks[0]["text"]})
        if model in llm_plan["fail"]:
            raise RuntimeError("model down")
        return {"lines": ["\"이제 가.\"", "어서 가자.", "어서 가자.", "그만 가 봐.", "또 봐."]}, {"model": model, "inputTokens": 10, "outputTokens": 5}

    def fake_download(key, path):
        with open(path, "wb") as handle:
            handle.write(store.blobs[key])
        return len(store.blobs[key])

    def fake_upload(path, key):
        with open(path, "rb") as handle:
            store.blobs[key] = handle.read()

    def fake_spawn(target, args, name):
        calls["spawn"].append(name)
        target(*args)

    saved_attrs: list = []

    def setv(obj, name, value):
        saved_attrs.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    for mod in (editmod, planmod, gm):
        setv(mod, "STORE", store)
    setv(vpoc_set, "VIDEO_BUCKET", "lr-video")
    setv(vset, "VNEW_SOUND", True)
    from src.vnew import voices as voices_mod  # noqa: PLC0415

    setv(voices_mod, "enabled", lambda: False)
    setv(vset, "VNEW_EDIT_RETAKE", True)
    setv(vpoc_set, "FAL_KEY", "")
    setv(snd, "_download", fake_download)
    setv(snd, "_probe", _probe_bytes)
    setv(snd, "_detect_speech", lambda path, duration: [])
    setv(lr, "ENABLED", True)
    setv(lr, "_download", fake_download)
    setv(lr, "_upload", fake_upload)
    setv(lr, "_probe", _probe_bytes)
    setv(lr, "_splice_file", fake_splice)
    setv(lr, "_compose", fake_compose)
    setv(lr, "_submit", fake_submit)
    models_box: dict = {"rows": [], "calls": 0}

    def fake_models(owner, pid, mid, rng):
        models_box["calls"] += 1
        return copy.deepcopy(models_box["rows"])

    setv(lr, "_models", fake_models)
    setv(lr, "_retake_state", lambda owner, pid, gen_id: copy.deepcopy(retake_state))
    setv(lr, "_spawn", fake_spawn)
    setv(lr, "_llm", fake_llm)
    scene_box: dict = {"times": [], "keys": [], "files": 0}

    def fake_scene_changes(key, start, end):
        scene_box["keys"].append(key)
        return [t for t in scene_box["times"] if start <= t <= end]

    def fake_scenes(path, start, end):
        scene_box["files"] += 1
        return [t for t in scene_box["times"] if start <= t <= end]

    setv(lr, "_scene_changes", fake_scene_changes)
    setv(lr, "_scenes", fake_scenes)
    setv(ledger_mod, "charge_text", lambda key, **kw: calls["charge"].append(key))
    final_key = _key("final-webtoon.mp4")
    try:
        store.blobs.update({final_key: _fake(4.0), _key("c1-webtoon-1.mp4"): _fake(2.0), _key("c2-webtoon-1.mp4"): _fake(2.0)})
        planmod.save_plan(OWNER, PID, _sound_plan())
        plan_before = planmod.load_plan(OWNER, PID)[0]

        def frozen() -> str:
            plan = planmod.load_plan(OWNER, PID)[0]
            return json.dumps([plan.get("revision"), plan.get("script"), plan["previz"].get("approved")], ensure_ascii=False, sort_keys=True)

        frozen0 = json.dumps([plan_before.get("revision"), plan_before.get("script"), plan_before["previz"].get("approved")],
                             ensure_ascii=False, sort_keys=True)
        # ================= (lr-c) 저장 · 견적 · CostMismatch · 시작 =================
        try:
            v0 = snd.sound_view(OWNER, PID, "webtoon")
            expect("lr-c", [[(l["id"], l["start"], l["end"]) for l in v0["lines"]], v0["lineRedo"]["cast"], v0["lineRedo"]["lines"],
                            v0["lineRedo"]["revisions"], v0["source"]["kind"]],
                   [[("e1", 0.0, 2.0), ("e2", 2.0, 4.0)], ["민지", "준호"], {}, [], "final"], "처음 보기: 줄 구간 · 말하는 사람 목록 · 고침 없음")
            expect("lr-c", [err_of(lambda: lr.save_override(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "textKo": " … ",
                                                                         "speaker": "민지", "thought": False})),
                            err_of(lambda: lr.save_override(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "textKo": "잘 가",
                                                                         "speaker": "철수", "thought": False})),
                            err_of(lambda: lr.save_override(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "textKo": "잘 가",
                                                                         "speaker": "민지"})),
                            err_of(lambda: lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"}))],
                   [["BadLineText", 400], ["BadSpeaker", 400], ["BadVoice", 400], ["LineNoOverride", 409]],
                   "빈 대사 · 목록 밖 사람 · 목소리 없음 400 · 고침 없이 견적 409")
            v1 = lr.save_override(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "textKo": "잘 가", "speaker": "민지", "thought": False,
                                              "deliveryKo": "작게, 떨리는 목소리"})
            ov = v1["lineRedo"]["lines"]["e1"]["override"]
            expect("lr-c", [ov["textKo"], ov["original"], ov["scriptApplied"], frozen() == frozen0, v1["lines"][0]["lineKo"]],
                   ["잘 가", {"lineKo": "안녕", "speaker": "민지", "voice": "spoken"}, False, True, "안녕"],
                   "저장: 새 대사 · 원래 대사 지킴 · 대본은 그대로(조용히 바꾸지 않음)")
            q = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            c = calls["compose"][-1]
            expect("lr-c", [c["mid"] == editmod.media_id(_key("c1-webtoon-1.mp4")), c["in"], c["out"], c["scope"],
                            '"잘 가"' in c["requestKo"], q["quote"]["status"], q["quote"]["usd"], q["quote"]["modelLabel"],
                            q["quote"]["range"]["start"], q["quote"]["range"]["end"], "안녕" in q["prompt"], '"잘 가"' in q["prompt"]],
                   [True, 0.0, 2.0, "range", True, "quoted", 1.23, "Kling Edit", 0.0, 2.0, False, True],
                   "견적: c1 재료 · 칸 전체(최소 길이보다 짧은 조각) · compose 바디 · 금액 · 옛 대사 → 새 대사")
            got = err_of(lambda: lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.0}))
            job = lr.read_redo(OWNER, PID, "g1", "webtoon")["jobs"]["e1"]
            expect("lr-c", [got, job["status"], len(calls["submit"])], [["CostMismatch", 409], "quoted", 1],
                   "화면 금액이 다르면 409 CostMismatch · 작업은 견적 그대로")
            started = lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.23})
            s = calls["submit"][-1]
            expect("lr-c", [started["job"]["status"], s["prompt"] == q["prompt"], s["draftId"], s["scope"], s["in"], s["out"], s["model"],
                            err_of(lambda: lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.23}))],
                   ["running", True, "d1", "range", 0.0, 2.0, "fal:edit", ["LineRedoRunning", 409]],
                   "시작: 견적 초안 그대로 submit · running · 두 번 누르면 409")
        except Exception as exc:  # noqa: BLE001
            crash("lr-c", exc)

        # ================= (lr-d) 완료 → 새 판 =================
        try:
            def add_version(current):
                current["versions"] = [{"n": 1, "key": _key("sound/webtoon/v1.mp4"), "at": "x", "thoughtIds": ["e2"], "sourceKey": final_key}]
                return current

            editmod._mutate_json(snd.doc_key(OWNER, PID, "g1", "webtoon"), add_version)  # noqa: SLF001
            mid_view = snd.sound_view(OWNER, PID, "webtoon")
            expect("lr-d", [mid_view["lineRedo"]["busy"], mid_view["lineRedo"]["lines"]["e1"]["job"]["status"], calls["splice"]],
                   [True, "running", []], "retake 진행 중: running · 잇지 않음")
            out1 = _key("retake/c1-v2.mp4")
            store.blobs[out1] = _fake(2.0, audio=True)
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 2, "status": "completed", "outputKey": out1}], "draft": None}
            final_bytes = store.blobs[final_key]
            snd.sound_view(OWNER, PID, "webtoon")  # 폴링: 결과 → 잇기(가짜 스레드 = 바로)
            v2 = snd.sound_view(OWNER, PID, "webtoon")
            # 219: 판 키는 이제 고유 토큰이 붙는다(-lines-r1-xxxxxx.mp4) — 고정 키(revision_key(...,1)) 대신 판의 rev["key"] 를 본다.
            r1 = next((x["key"] for x in lr.read_redo(OWNER, PID, "g1", "webtoon")["revisions"] if x.get("r") == 1), "")
            sp = calls["splice"][-1] if calls["splice"] else {}
            info = v2["lineRedo"]
            expect("lr-d", [sp.get("start"), sp.get("end"), sp.get("duration"), sp.get("fps"), sp.get("width"), sp.get("new_audio"),
                            sp.get("base", b"")[:12], r1 in store.blobs, store.blobs.get(final_key) == final_bytes],
                   [0.0, 2.0, 4.0, 24, 160, True, b"FAKE:dur=4.0", True, True], "잇기: 구간 · 원본 길이·크기 · 새 판 키 · 원래 완성본 그대로")
            expect("lr-d", [v2["source"]["kind"], "대사 고침 1" in v2["source"]["label"], v2["source"]["videoUrl"].endswith(r1),
                            info["current"], [r["r"] for r in info["revisions"]], info["lines"]["e1"]["job"]["status"],
                            info["lines"]["e1"]["job"]["revision"], [(l["id"], l["start"], l["end"]) for l in v2["lines"]]],
                   ["redo", True, True, 1, [1], "done", 1, [("e1", 0.0, 2.0), ("e2", 2.0, 4.0)]],
                   "⑥ 원본 = 새 판 · 판 목록 · 작업 done · 줄 시간 그대로")
            expect("lr-d", [[v.get("outdated"), v.get("outdatedText"), v["currentSource"]] for v in v2["versions"]], [[True, lr.OUTDATED_TEXT, False]],
                   "목소리 반영 기록 outdated(“목소리 맞춤을 다시 반영해 주세요”) · 자동 반영 없음")
            rev1 = lr.read_redo(OWNER, PID, "g1", "webtoon")["revisions"][0]
            expect("lr-d", [[p["t0"], p["t1"], p["sourceKey"]] for p in rev1["pieces"]],
                   [[0.0, 2.0, out1], [2.0, 4.0, _key("c2-webtoon-1.mp4")]], "판 조각: c1 자리 = retake 결과")
            # 두 번째 줄(속마음)은 첫 판 위에.
            lr.save_override(OWNER, PID, {"variant": "webtoon", "lineId": "e2", "textKo": "설마 그럴 리가", "speaker": "준호", "thought": True})
            lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e2"})
            c2 = calls["compose"][-1]
            lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e2", "confirmUsd": 1.23})
            out2 = _key("retake/c2-v2.mp4")
            store.blobs[out2] = _fake(2.0, audio=False)
            retake_state["cells"]["webtoon:c2"] = {"versions": [{"n": 2, "status": "completed", "outputKey": out2}], "draft": None}
            snd.sound_view(OWNER, PID, "webtoon")
            v3 = snd.sound_view(OWNER, PID, "webtoon")
            redo = lr.read_redo(OWNER, PID, "g1", "webtoon")
            rev2 = redo["revisions"][-1]
            expect("lr-d", [c2["mid"] == editmod.media_id(_key("c2-webtoon-1.mp4")), "mouth stays closed" in redo["jobs"]["e2"]["prompt"],
                            calls["splice"][-1]["base"] == store.blobs[r1][:60], calls["splice"][-1]["new_audio"], rev2["fromR"],
                            [p["sourceKey"] for p in rev2["pieces"]], v3["lineRedo"]["current"],
                            v3["lineRedo"]["lines"]["e2"]["job"]["message"] == lr.NO_AUDIO_TEXT, r1 in store.blobs],
                   [True, True, True, False, 1, [out1, out2], 2, True, True],
                   "두 번째 줄: 첫 판 위에 잇기(첫 고침 유지) · 소리 없는 결과 → 원래 소리 + 안내 · 앞 판 파일 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("lr-d", exc)

        # ================= (lr-e) 되돌리기 =================
        try:
            back0 = lr.revert(OWNER, PID, {"variant": "webtoon", "r": 0})
            expect("lr-e", [back0["source"]["kind"], back0["lineRedo"]["current"], [v.get("outdated") for v in back0["versions"]],
                            [v["currentSource"] for v in back0["versions"]]],
                   ["final", 0, [None], [True]], "원래 영상으로: ⑤ 완성본 · outdated 뗌")
            back1 = lr.revert(OWNER, PID, {"variant": "webtoon", "r": 1})
            expect("lr-e", [back1["source"]["kind"], back1["lineRedo"]["current"], "대사 고침 1" in back1["source"]["label"],
                            [v.get("outdated") for v in back1["versions"]], err_of(lambda: lr.revert(OWNER, PID, {"variant": "webtoon", "r": 9})),
                            err_of(lambda: lr.revert(OWNER, PID, {"variant": "webtoon", "r": "1"})),
                            all(k in store.blobs for k in [final_key] + [x["key"] for x in  # 219: 판 키 = rev["key"](고유 토큰)
                                                                         lr.read_redo(OWNER, PID, "g1", "webtoon")["revisions"]
                                                                         if x.get("r") in (1, 2)])],
                   ["redo", 1, True, [True], ["BadRevision", 404], ["BadRevision", 400], True],
                   "앞 판으로 · 없는 판 404 · 틀린 값 400 · 파일은 지우지 않음")
        except Exception as exc:  # noqa: BLE001
            crash("lr-e", exc)

        # ================= (lr-g) AI로 다시 쓰기 =================
        try:
            calls["llm"].clear()
            calls["charge"].clear()
            got = lr.suggest(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            expect("lr-g", [got["lines"], got["model"], len(calls["llm"]), len(calls["charge"]), "▶ 민지: 안녕" in calls["llm"][0]["text"],
                            "준호 (속마음): 설마…" in calls["llm"][0]["text"]],
                   [["이제 가.", "어서 가자.", "그만 가 봐."], vset.BRIEF_MODEL, 1, 1, True, True],
                   "후보 3줄(따옴표·겹침 정리) · 지시서와 같은 싼 모델 · 원장 1건 · 앞뒤 대사 문맥")
            llm_plan["fail"] = {vset.BRIEF_MODEL}
            calls["llm"].clear()
            got2 = lr.suggest(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            expect("lr-g", [len(got2["lines"]), [c["model"] for c in calls["llm"]]],
                   [3, list(dict.fromkeys([vset.BRIEF_MODEL, vset.BRIEF_FALLBACK_MODEL]))], "첫 모델 실패 → 대체 모델")
            llm_plan["fail"] = set()
        except Exception as exc:  # noqa: BLE001
            crash("lr-g", exc)

        # ================= (lr-h) 라우트 =================
        try:
            from flask import Flask  # noqa: PLC0415

            from src import vnew  # noqa: PLC0415

            perm = {"write": True}
            app = Flask(__name__)
            app.register_blueprint(vnew.create_blueprint(vnew.VnewDependencies(
                require_auth=lambda handler: handler, current_owner=lambda: OWNER,
                get_project=lambda pid: {"id": pid, "owner": OWNER, "track": "video_new"},
                can_read_project=lambda proj: True, can_write_project=lambda proj: perm["write"], project_owner_prefix=lambda n: n)))
            client = app.test_client()
            base = f"/api/vnew/p/{PID}/sound/lines/"
            body = {"variant": "webtoon", "lineId": "e1"}
            routes = [("post", "suggest"), ("put", "override"), ("post", "quote"), ("post", "redo"), ("get", "status"), ("post", "revert")]

            def hit(method, tail):
                fn = getattr(client, method)
                resp = fn(base + tail + ("?variant=webtoon" if method == "get" else ""), **({} if method == "get" else {"json": body}))
                return resp.status_code

            perm["write"] = False
            expect("lr-h", [hit(m, t) for m, t in routes], [404] * 6, "남의 프로젝트(쓰기 권한 없음) → 6개 모두 404")
            perm["write"] = True
            lr.ENABLED = False
            expect("lr-h", [hit(m, t) for m, t in routes], [404] * 6, "VNEW_LINE_REDO 끔 → 404")
            lr.ENABLED = True
            resp = client.get(base + "status?variant=webtoon")
            expect("lr-h", [resp.status_code, "lineRedo" in (resp.get_json() or {})], [200, True], "소유자 GET status → 200 · lineRedo")
        except Exception as exc:  # noqa: BLE001
            crash("lr-h", exc)

        # ================= (lr-k) 실패한 작업 [다시 시도] → 견적부터 다시(새 틀) =================
        try:
            old_prompt = ("EDIT: Edit @Video1. Apply this change fully, even where it changes framing, camera or composition: Change "
                          "Minji's line to \"Bye.\" (Korean original: \"잘 가\").")

            def stale(status):
                def mutate(doc):
                    job = doc["jobs"]["e1"]
                    job.update(status=status, prompt=old_prompt, error="fal 잔액이 부족합니다.", draftId="dOld")
                    job.pop("promptV", None)
                    return doc
                lr._mutate_redo(OWNER, PID, "g1", "webtoon", mutate)  # noqa: SLF001

            n_submit = len(calls["submit"])
            stale("failed")
            failed_try = err_of(lambda: lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.23}))
            stale("quoted")
            quoted_try = err_of(lambda: lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.23}))
            n_compose = len(calls["compose"])
            q2 = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 1.23})
            sent = calls["submit"][-1]["prompt"] if len(calls["submit"]) > n_submit else ""
            expect("lr-k", [failed_try, quoted_try, len(calls["compose"]) - n_compose, len(calls["submit"]) - n_submit, sent == q2["prompt"],
                            sent.startswith(lr.LINE_EDIT_HEAD), "even where" in sent, lr.audio_rule_count(sent), calls["submit"][-1]["extraKo"]],
                   [["LineRedoNoQuote", 409], ["LineRedoNoQuote", 409], 1, 1, True, True, False, 1, ""],
                   "실패한 작업·옛 꼴 견적은 옛 프롬프트를 다시 보내지 않음(409) → 견적이 새 틀로 다시 만들고 그 프롬프트를 제출")
        except Exception as exc:  # noqa: BLE001
            crash("lr-k", exc)

        # ================= (lr-l) fix2: 가장 싼 모델 기본 · 고르기 · 거절 → 다음 모델(확인 뒤) =================
        try:
            dm = lr.default_model
            expect("lr-l", [dm(SAMPLE_ROWS), dm([_row(SEED, 0.82, original=True)]), dm([_row(SEED, 0.82, original=True), _row(WAN, 0.2, ok=False)]),
                            dm([_row(SEED, 0.10), _row(KLING, 0.42)]), dm([_row(WAN, 0.42), _row(KLING, 0.42, original=True)]),
                            dm([_row(WAN, 0.20, path=rt.KCUT_PATH), _row(KLING, 0.42)]), dm([_row(WAN, None), _row(KLING, 0.42)]), dm([]),
                            dm(SAMPLE_ROWS, [WAN]), dm(SAMPLE_ROWS, [WAN, KLING]), dm(SAMPLE_ROWS, [WAN, KLING, SEED])],
                   [WAN, SEED, SEED, KLING, KLING, KLING, KLING, "", KLING, SEED, WAN],
                   "기본 = 가장 싼 줄(Wan $0.20) · Seedance 는 유일할 때만 · 싸도 Seedance 는 뒤 · 같으면 원래 모델 · 시작 그림·구간 못 받는 줄·값 모름 뒤 · "
                   "거절 모델 빼고 다음으로 싼 줄")
            def cell_of():  # 이 원본(판 r1)에서 e1 구간 재료의 칸(가짜 compose 의 cellKey)
                return lr.read_redo(OWNER, PID, "g1", "webtoon")["jobs"]["e1"]["cellKey"]

            retake_state["cells"][cell_of()] = {"versions": [{"n": 2, "status": "failed", "error": "x"}], "draft": None}
            snd.sound_view(OWNER, PID, "webtoon")  # lr-k 에서 시작한 e1 을 끝낸다(보통 실패 — 거절 아님)
            models_box["rows"] = copy.deepcopy(SAMPLE_ROWS)
            q_def = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})["quote"]
            c_def = calls["compose"][-1]
            q_pick = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "model": KLING})["quote"]
            c_pick = calls["compose"][-1]
            expect("lr-l", [c_def.get("model"), q_def["model"], q_def["usd"], [m["id"] for m in q_def["models"]],
                            [m["usd"] for m in q_def["models"]], q_def["stepUp"], c_pick.get("model"), q_pick["model"], q_pick["usd"],
                            [m["usd"] for m in q_pick["models"] if m["id"] == KLING], [m["seedance"] for m in q_def["models"]]],
                   [WAN, WAN, 0.2, [WAN, KLING, SEED], [0.2, 0.42, 0.82], None, KLING, KLING, 0.42, [0.42], [False, False, True]],
                   "견적: compose 에 가장 싼 모델 · 모델 줄(싼 순서·금액) · 다른 모델을 고르면 그 모델로 견적 다시(새 금액)")
            lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 0.42})
            expect("lr-l", [calls["submit"][-1]["model"], calls["submit"][-1]["draftId"]], [KLING, f"d{len(calls['compose'])}"],
                   "고른 모델(Kling)로 시작 → submit 에 그 모델·그 초안")
            retake_state["cells"][cell_of()] = {"versions": [{"n": 2, "status": "failed", "error": "x"}], "draft": None}
            snd.sound_view(OWNER, PID, "webtoon")
            lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 0.2})
            sent_def = calls["submit"][-1]["model"]
            n_ok = len(calls["submit"])
            expect("lr-l", [sent_def, calls["submit"][-1]["confirmUsd"]], [WAN, 0.2], "시작: submit 에 기본 모델(Wan) · 그 금액")

            def flag(n=2):
                retake_state["cells"][cell_of()] = {"versions": [{"n": n, "status": "failed", "error": rt.CONTENT_FLAG_TEXT,
                                                                     "failureKind": rt.CONTENT_FLAG_KIND}], "draft": None}
                return snd.sound_view(OWNER, PID, "webtoon")["lineRedo"]["lines"]["e1"]["job"]

            failed = flag()
            q_up = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})["quote"]
            after_quote = len(calls["submit"])
            low = err_of(lambda: lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 0.2}))
            still = lr.read_redo(OWNER, PID, "g1", "webtoon")["jobs"]["e1"]["status"]
            ok_up = lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": 0.42})["job"]["status"]
            expect("lr-l", [failed["status"], failed["failureKind"], q_up["model"], q_up["usd"], q_up["stepUp"], after_quote == n_ok, low, still,
                            ok_up, calls["submit"][-1]["model"], calls["submit"][-1]["confirmUsd"]],
                   ["failed", rt.CONTENT_FLAG_KIND, KLING, 0.42, {"fromModel": WAN, "fromLabel": "Wan 3.0"}, True, ["CostMismatch", 409],
                    "quoted", "running", KLING, 0.42],
                   "거절 → 다음 견적은 다음으로 싼 Kling($0.42, stepUp) · 견적만으로는 내지 않음 · 옛 금액 확인은 409 · 새 금액 확인 뒤 Kling 제출")
            flag()
            q_seed = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})["quote"]
            expect("lr-l", [q_seed["model"], q_seed["stepUp"]["fromModel"], q_seed["usd"]], [SEED, KLING, 0.82],
                   "다시 거절 → 남은 Seedance(유일한 줄일 때만) · 새 금액")
            models_box["rows"] = []
        except Exception as exc:  # noqa: BLE001
            crash("lr-l", exc)

        # ================= (lr-m) fix3: 견적 → 잇기(구간 안 컷) · rebuild_revision =================
        try:
            c1_key, c2_key = _key("c1-webtoon-1.mp4"), _key("c2-webtoon-1.mp4")
            one = [{"t0": 0.0, "t1": 4.0, "sourceKey": c1_key, "in0": 0.0, "rate": 1.0, "cuts": [[0.0, 2.0, "c1"], [2.0, 4.0, "c2"]]}]
            real_pieces = lr._pieces  # noqa: SLF001
            lr._pieces = lambda *a, **k: copy.deepcopy(one)  # noqa: SLF001 — 두 컷이 든 한 칸(테이크) 원본
            try:
                q_m = lr.quote(OWNER, PID, {"variant": "webtoon", "lineId": "e1"})
            finally:
                lr._pieces = real_pieces  # noqa: SLF001
            hard_m = "@Video1 has a hard cut at 2 s to a different shot; keep that cut and the second shot exactly as in @Video1."
            rq = q_m["quote"]["range"]
            expect("lr-m", [rq["start"], rq["end"], rq["cuts"], rq["splice"], rq["scene"], hard_m in q_m["prompt"],
                            hard_m in calls["compose"][-1]["prompt"], calls["compose"][-1]["in"], calls["compose"][-1]["out"]],
                   [0.0, 3.0, [2.0], [0.0, 2.0], True, True, True, 0.0, 3.0],
                   "견적: 짧은 샷(c1 2초) → 3초로 늘려 컷을 넘음 · 모델에는 늘린 구간 · 프롬프트에 컷 줄 · 잇기 구간은 컷까지")
            lr.start(OWNER, PID, {"variant": "webtoon", "lineId": "e1", "confirmUsd": q_m["quote"]["usd"]})
            out_m = _key("retake/c1-v3.mp4")
            store.blobs[out_m] = _fake(3.0)
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 2, "status": "completed", "outputKey": out_m}], "draft": None}
            n_splice, n_compose, n_submit = len(calls["splice"]), len(calls["compose"]), len(calls["submit"])
            snd.sound_view(OWNER, PID, "webtoon")
            redo_m = lr.read_redo(OWNER, PID, "g1", "webtoon")
            rev_m = redo_m["revisions"][-1]
            sp_m = calls["splice"][-1] if len(calls["splice"]) > n_splice else {}
            expect("lr-m", [sp_m.get("start"), sp_m.get("end"), sp_m.get("splice_start"), sp_m.get("splice_end"), sp_m.get("duration"),
                            rev_m["range"]["splice"], [[p["t0"], p["t1"], p["sourceKey"], p["in0"]] for p in rev_m["pieces"]],
                            rev_m["clipKey"] == out_m, redo_m["jobs"]["e1"]["status"]],
                   [0.0, 3.0, 0.0, 2.0, 4.0, [0.0, 2.0], [[0.0, 2.0, out_m, 0.0], [2.0, 4.0, c2_key, 0.0]], True, "done"],
                   "잇기: 새 영상은 컷(2초)까지만 · 컷 뒤(c2 자리)는 원래 조각 · 길이 그대로")
            # 옛 규칙으로 만든 판(prod test3 r1 모양: 구간 전체를 덮음, clipKey·splice 없음) → rebuild_revision
            out9 = _key("retake/c1-v9.mp4")
            store.blobs[out9] = _fake(3.0)
            old_pieces = [{"t0": 0.0, "t1": 3.0, "sourceKey": out9, "in0": 0.0, "rate": 1.0, "cuts": []},
                          {"t0": 3.0, "t1": 4.0, "sourceKey": c2_key, "in0": 1.0, "rate": 1.0, "cuts": []}]

            def add_old(doc):
                doc["revisions"].append({"r": 9, "key": lr.revision_key(OWNER, PID, "g1", "webtoon", 9), "at": "x", "baseKey": final_key,
                                         "fromKey": final_key, "fromR": 0, "lineId": "e1", "range": {"start": 0.0, "end": 3.0},
                                         "pieces": copy.deepcopy(old_pieces), "retake": {"cellKey": "webtoon:c1", "n": 9},
                                         "model": WAN, "usd": 0.2})
                doc["current"] = 9
                return doc

            lr._mutate_redo(OWNER, PID, "g1", "webtoon", add_old)  # noqa: SLF001
            final_before = store.blobs[final_key]
            files_before = scene_box["files"]
            n_splice = len(calls["splice"])
            got_m = lr.rebuild_revision(OWNER, PID, "webtoon", 9)
            redo_r = lr.read_redo(OWNER, PID, "g1", "webtoon")
            new_rev = next(x for x in redo_r["revisions"] if x["r"] == got_m["r"])
            old_rev = next(x for x in redo_r["revisions"] if x["r"] == 9)
            sp_r = calls["splice"][-1] if len(calls["splice"]) > n_splice else {}
            expect("lr-m", [got_m["r"], got_m["splice"], got_m["seconds"], sp_r.get("start"), sp_r.get("end"), sp_r.get("splice_start"),
                            sp_r.get("splice_end"), sp_r.get("base", b"")[:12], sp_r.get("new", b"")[:12],
                            [[p["t0"], p["t1"], p["sourceKey"], p["in0"]] for p in new_rev["pieces"]], new_rev["usd"], new_rev["rebuiltFrom"],
                            redo_r["current"], old_rev["pieces"] == old_pieces, got_m["key"] in store.blobs,
                            store.blobs[final_key] == final_before, scene_box["files"] - files_before,
                            [len(calls["compose"]) - n_compose, len(calls["submit"]) - n_submit]],
                   [10, [0.0, 2.0], 4.0, 0.0, 3.0, 0.0, 2.0, b"FAKE:dur=4.0", b"FAKE:dur=3.0",
                    [[0.0, 2.0, out9, 0.0], [2.0, 4.0, c2_key, 0.0]], 0.0, 9, 10, True, True, True, 1, [0, 0]],
                   "rebuild_revision: 저장된 클립 · 같은 원본으로 다시 잇기 · 길이 그대로 · 샷 안(0–2초)만 바꿈 · 컷 뒤 원래 · 새 판(r10) · "
                   "옛 판·원본 그대로 · 장면 검사는 로컬 파일 · 모델 호출 0(견적·제출 0)")
            expect("lr-m", [err_of(lambda: lr.rebuild_revision(OWNER, PID, "webtoon", 77))], [["BadRevision", 404]], "없는 판 404")
        except Exception as exc:  # noqa: BLE001
            crash("lr-m", exc)
    finally:
        for obj, name, value in reversed(saved_attrs):
            setattr(obj, name, value)

    # ================= (lr-f) 대본에도 반영 → pvSig · 바뀐 테이크만 =================
    try:
        from src.vnew import previz as pm  # noqa: PLC0415
        from src.vnew import shot_brief as sb  # noqa: PLC0415

        box: dict = {"plan": None, "n": 0}

        def fake_brief(blocks, *, model):
            box["n"] += 1
            text = blocks[0]["text"]
            ids = re.findall(r"^CUT (\S+):$", text, re.M)
            return ({"sceneEn": f"Scene v{box['n']}.", "cuts": [{"cutId": cid, "shotEn": f"MS {cid} v{box['n']}", "actionEn": "looks",
                                                                 "dialogue": []} for cid in ids]}, {"model": "fake-brief"})

        def _mutate(owner, pid, mutator):
            data = copy.deepcopy(box["plan"])
            result = mutator(data)
            box["plan"] = data
            return data, result

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
            (planmod, "load_plan"): lambda owner, pid: (copy.deepcopy(box["plan"]), "etag"),
            (planmod, "mutate_plan"): _mutate,
            (pm, "_spawn"): lambda *a, **k: None,
        }
        saved = {key: getattr(*key) for key in patches}
        for (mod, name), fn in patches.items():
            setattr(mod, name, fn)
        try:
            def rerun(plan: dict) -> dict:
                data = copy.deepcopy(plan)
                brief = data["previz"].get("shotBrief") or sb._empty_brief()  # noqa: SLF001
                brief["status"] = "running"
                brief["task"] = {"status": "running", "startedAt": "2026-11-10T00:00:00Z", "id": f"t{box['n']}"}
                data["previz"]["shotBrief"] = brief
                box["plan"] = data
                sb.run_brief(OWNER, PID, brief["task"]["id"])
                return copy.deepcopy(box["plan"])

            p0 = rerun(_take_plan())
            gen = _done(gm._new_take_gen(p0))  # noqa: SLF001
            box["plan"] = copy.deepcopy(p0)
            changed = lr.apply_to_script(OWNER, PID, "e8", {"textKo": "문 열어.", "speaker": "민수", "thought": True})
            p1 = copy.deepcopy(box["plan"])
            e8 = next(e for e in p1["script"]["entries"] if e["id"] == "e8")
            expect("lr-f", [changed, e8["lineKo"], e8["voice"], p1["revision"], p1["previz"]["approved"],
                            next(e for e in p1["script"]["entries"] if e["id"] == "e2") == p0["script"]["entries"][1]],
                   [True, "문 열어.", "thought", p0["revision"] + 1, True, True], "그 대본 줄만 · revision + 1 · 프리비즈 승인 그대로")
            expect("lr-f", [sb.brief_outdated_cuts(p0, owner=OWNER), sb.brief_outdated_cuts(p1, owner=OWNER)], [[], ["c8"]],
                   "대본에 반영 → 그 컷(c8) 지시서 지문(pvSig)만 달라짐")
            p2 = rerun(p1)
            expect("lr-f", [p2["previz"]["shotBrief"]["cuts"]["c8"]["pvSig"] != p0["previz"]["shotBrief"]["cuts"]["c8"]["pvSig"],
                            gm.gen_stale_units(gen, p2), gm.only_changed_quote(p2, gen).get("takes")],
                   [True, ["take2"], ["take2"]], "지시서 따라잡기 뒤 → 바뀐 테이크만 [take2](⑤ 기존 흐름)")
            expect("lr-f", lr.apply_to_script(OWNER, PID, "e8", {"textKo": "문 열어.", "speaker": "민수", "thought": True}), False,
                   "같은 값으로 다시 반영 → 바뀌지 않음(revision 그대로)")
        finally:
            for (mod, name), fn in saved.items():
                setattr(mod, name, fn)
    except Exception as exc:  # noqa: BLE001
        crash("lr-f", exc)

    # ================= (lr-i) node =================
    node = shutil.which("node")
    try:
        vsrc = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        expect("lr-i", ["lrdRowTools(ctx, line, row, lineCell, listenCell)" in vsrc, "lrdRevisionBar(ctx)" in vsrc,
                        "이전 영상으로 되돌리기" in vsrc, "테이크 전체 다시 만들기" in vsrc, "costChangedText(shown.usd, fresh.usd)" in vsrc,
                        "innerHTML" in vsrc[vsrc.index("⑥ 사운드 — 대사 고치기"):vsrc.index("⑥ 사운드 — 인물 목소리(작업 203)")]],
               [True, True, True, True, True, False], "vnew.js: 줄 도구 · 판 막대 · 되돌리기 · 테이크 안내 · 금액 바뀜 · innerHTML 없음")
        if not node:
            print("[WARN] node 가 없어 작업 212 화면 검사(lr-i)를 건너뜁니다")
        else:
            proc = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True, encoding="utf-8", timeout=120)
            expect("lr-i", [proc.returncode, (proc.stderr or "")[-300:]], [0, ""], "node --check vnew.js")
            names = ("el", "clear", "castingUsdText", "lrdInfo", "lrdChanged", "lrdUsd", "lrdJobText", "lrdControls", "lrdPaintLine",
                     "lrdRowTools", "lrdEntry", "lrdPaintRow", "lrdEditor")
            js = "\n".join(_extract_js(vsrc, n) for n in names) + _UI_JS
            with tempfile.TemporaryDirectory(prefix="chk212-") as tmp:
                path = Path(tmp) / "ui.js"
                path.write_text(js, encoding="utf-8", newline="\n")
                proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            lines = (proc.stdout or "").strip().splitlines()
            if proc.returncode != 0 or not lines:
                expect("lr-i", (proc.stderr or "")[-300:], "", "화면 실행")
            else:
                o = json.loads(lines[-1])
                expect("lr-i", o["jobs"], ["", "금액 확인됨: $1.23 · Kling", "영상을 다시 만드는 중… ($1.23 · Kling)",
                                           {"text": "다시 만들지 못했습니다: 거절", "bad": True}], "상태 글: 금액 · 모델 · 실패")
                expect("lr-i", o["closed"], ["대사 고치기", False, "안녕"], "닫힘: [대사 고치기] · 편집 창 없음 · 대사 그대로")
                pick = lambda s: [s["open"], s["save"], s["redo"], s["take"], s["hint"]]  # noqa: E731
                expect("lr-i", [pick(o["opened"]), o["opened"]["text"], o["opened"]["speaker"], o["opened"]["voice"]],
                       [["닫기", False, True, "none", "먼저 새 대사를 저장해 주세요."], "안녕", "민지", "spoken"],
                       "열기: 지금 대사·사람·목소리 · 저장 가능 · 다시 만들기 꺼짐(저장 전)")
                expect("lr-i", [o["empty"]["save"], o["typed"]["save"], o["typed"]["redo"]], [True, False, True], "빈 대사 → 저장 꺼짐")
                expect("lr-i", [pick(o["saved"]), o["saved"]["label"], o["lineCell"]],
                       [["닫기", False, False, "none", ""], "이 대사 영상 다시 만들기 (금액 확인)", ["안녕", "잘 가"]],
                       "저장 뒤: 다시 만들기 켜짐 · 원래 대사(회색)와 새 대사")
                expect("lr-i", [o["quoted"]["label"], o["running"]["redo"], o["running"]["save"], o["status"]],
                       ["이 대사 영상 다시 만들기 ($1.23)", True, True, "영상을 다시 만드는 중… ($1.23 · Kling)"],
                       "견적 금액 표시 · 진행 중 단추 꺼짐 · 상태 글")
                expect("lr-i", [o["applied"]["take"], o["applied"]["redo"], o["changed"]], ["", False, [False, True]],
                       "대본에 반영 → [테이크 전체 다시 만들기] 보임 · 폴링 다시 그리기 판정")
            names_l = ("el", "clear", "castingUsdText", "lrdUsd", "lrdConfirmText", "lrdModelChoices", "lrdStepUpText", "lrdQuoteBody",
                       "lrdConfirmPaint", "lrdConfirmClose")
            js_l = "\n".join(_extract_js(vsrc, n) for n in names_l) + _UI_JS.split("var out = {};")[0] + _UI_JS_L
            with tempfile.TemporaryDirectory(prefix="chk212l-") as tmp:
                path = Path(tmp) / "ui_l.js"
                path.write_text(js_l, encoding="utf-8", newline="\n")
                proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            lines = (proc.stdout or "").strip().splitlines()
            if proc.returncode != 0 or not lines:
                expect("lr-l", (proc.stderr or "")[-300:], "", "화면 실행(모델 고르기)")
            else:
                o = json.loads(lines[-1])
                expect("lr-l", [o["radios"], o["picks"], o["starts"], o["go"], o["stepup"]],
                       [[["W", True, "vnew-lrd-model-e1", "Wan 3.0 · $0.20"], ["K", False, "vnew-lrd-model-e1", "Kling O3 Edit · $0.42"],
                         ["S", False, "vnew-lrd-model-e1", "Seedance 2.5 · $0.82 (원래 모델)"]], ["K"], 1, "이 금액으로 시작 ($0.20)", False],
                       "화면: 모델 줄(라벨·금액, 기본 선택) · 다른 모델 고르면 견적 다시(pick) · 같은 모델은 그대로 · 시작 단추 금액")
                expect("lr-l", [o["up"], o["busy"], o["closed"], o["body"]],
                       [["Wan 3.0 모델이 내용 검사에서 거절해 다음으로 싼 Kling O3 Edit($0.42)로 바꿨습니다. 금액을 확인한 뒤 시작해 주세요.",
                         "이 금액으로 시작 ($0.42)", 1], [True, True], [None, 0],
                        [{"variant": "real", "lineId": "e1"}, {"variant": "real", "lineId": "e1", "model": "K"}]],
                       "화면: 거절 뒤 다음 모델 안내 + 새 금액 확인 · 견적 중엔 고르기·시작 꺼짐 · 닫기 · 견적 바디(model)")
    except Exception as exc:  # noqa: BLE001
        crash("lr-i", exc)

    # ================= (lr-j) LF =================
    try:
        expect("lr-j", [p.name for p in FILES if b"\r\n" in p.read_bytes()], [], "새·고친 파일 LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("lr-j", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 212 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 212 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 212 ({group}) {title}")
    print('[hook] check_vpoc.main(): if not __import__("check_line_redo_212").check_line_redo_212(): '
          'fail("작업 212: ⑥ 대사 고치기 · 그 대사 구간 영상 다시 만들기 검사 실패")')
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_line_redo_212() else 1)
