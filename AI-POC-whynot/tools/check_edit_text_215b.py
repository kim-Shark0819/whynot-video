"""작업 215b 검사: ⑦ 자막 · 타이틀 · 블랙/화이트 전환 · 시작/끝 페이드 · 클립 색 보정 · 프리미어 버튼 · ⑦ 안 대사 고치기.
가짜 저장소, 유료·네트워크 0. ffmpeg(imageio-ffmpeg 또는 PATH)가 없으면 tx-d 는 WARN, node 가 없으면 tx-g 는 WARN.

  (tx-a) 옛 문서 그대로: 옛 클립 정리 · 저장 문서 키 · _doc_hash/extend_hash 같음 · 새 키(색 · 블랙 전환 · 자막) 저장 · 틀린 값 400 · 길이 맞춤
  (tx-b) 자막 자동 만들기: ⑥ 줄 시간 → ⑥ 원본 조각 → 재료 초 → 지금 클립(순서 바꿈 · 자름) · 대사 고침이 대본보다 먼저 · 속마음 기울임
  (tx-c) SRT 글 · 굽기 ASS(테두리/상자 · 위/아래 · 기울임) · 타이틀(9칸 \\an · \\fad · 상자) · -vf 글
  (tx-d) ffmpeg 작은 클립: 블랙/화이트 경계 장 · 시작/끝 페이드 · 전체 장 수 그대로 · 색온도/색조 방향 · 자막 굽기(글꼴 있으면) ·
         run_export 끝까지(start_export 사본 textFx · 해시 · 마무리 패스)
  (tx-e) 색 보정 ffmpeg 값이 필터 범위 안(±100 끝 · 범위 밖 값은 버림)
  (tx-f) 라우트: /edit/captions/srt · /edit/captions/auto(남의 프로젝트 404 · VNEW_EDIT 끔 404) · routes.py 한 줄 · 프리미어 버튼 → /edit/premiere
  (tx-g) 화면(node): 자막 끌기 · 다듬기 · 자르기 · 지우기 · 실행 취소/다시 · 글 고치기 → ⑥ 대사 고침 PUT · 전환 메뉴 블랙/화이트 ·
         색 슬라이더 · 원래대로 · 같은 테이크 · 프리미어 POST → GET → 내려받기 · ⑦ 대사 창 = ⑥ 함수 · 새 영상으로 바꾸기(편집 그대로)
  (tx-h) LF 줄 끝 · import

실행: python -X utf8 -B tools/check_edit_text_215b.py
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
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "tx-a": "옛 문서 그대로 · 새 키 저장 · 틀린 값 400",
    "tx-b": "자막 자동 만들기(⑥ 줄 시간 · 대사 고침 먼저)",
    "tx-c": "SRT · 굽기 ASS · 타이틀 · -vf 글",
    "tx-d": "ffmpeg: 블랙/화이트 · 페이드 · 장 수 · 색 · 굽기 · run_export",
    "tx-e": "색 보정 값 범위",
    "tx-f": "라우트 · routes.py 한 줄 · 프리미어 버튼",
    "tx-g": "화면(node) 조작",
    "tx-h": "LF · import",
}
HOOK = "from . import edit_text  # noqa: PLC0415\n    edit_text.register_routes(bp, deps, _project_for, _fail)"
HOOK_215D = "edit_xml.register_routes(bp, deps, _project_for, _fail)"
OWNER, PID = "tester", "prj_tx"
BASE = f"users/{OWNER}/video/{PID}/"
K1, K2, EXP = BASE + "vnew/gen/g1/a.mp4", BASE + "vnew/gen/g1/b.mp4", BASE + "vnew/gen/g1/final-edit-1.mp4"
META = {K1: {"sourceKey": K1, "duration": 6.0, "width": 64, "height": 36, "fps": 24, "hasAudio": False},
        K2: {"sourceKey": K2, "duration": 6.0, "width": 64, "height": 36, "fps": 24, "hasAudio": False}}
OLD_CLIPS = [
    {"id": "c1", "sourceKey": K1, "in": 0.0, "out": 2.0, "muted": False, "transition": {"type": "dissolve", "seconds": 0.5}},
    {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0, "muted": True, "speed": 0.5, "audioTail": 0.5, "label": "둘"},
    {"id": "c3", "sourceKey": K1, "in": 3.0, "out": 5.0, "muted": False},
]
CHANGED = [m for m in ("edit.py", "routes.py")]
NEW_FILES = [ROOT / "app" / "src" / "vnew" / "edit_text.py", Path(__file__).resolve()]


class _Store:
    """메모리 저장소(조건부 쓰기 흉내 — 작업 215d 검사와 같다)."""

    def __init__(self) -> None:
        self.json: dict = {}
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


def _ffmpeg() -> str:
    try:
        from src import stitcher  # noqa: PLC0415

        return stitcher.ffmpeg_path()
    except Exception:  # noqa: BLE001
        return ""


def _make_clip(ff: str, path: str, color: str, seconds: float) -> None:
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=24:d={seconds}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", path], check=True, capture_output=True, timeout=60)


def _join(ff: str, out: str, colors: list[str]) -> None:
    args = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    for color in colors:
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=24:d=1"]
    graph = "".join(f"[{i}:v]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1:a=0[v]"
    subprocess.run(args + ["-filter_complex", graph, "-map", "[v]", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                           out], check=True, capture_output=True, timeout=60)


def _frames(ff: str, path: str, w: int = 64, h: int = 36) -> list[tuple[int, int, int]]:
    """장마다 평균 (R, G, B)."""
    proc = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True, timeout=60)
    data, size, out = proc.stdout, w * h * 3, []
    for i in range(len(data) // size):
        frame = data[i * size:(i + 1) * size]
        out.append(tuple(round(sum(frame[c::3]) / (w * h)) for c in range(3)))
    return out


def _region(ff: str, path: str, index: int, w: int = 64, h: int = 36) -> list[int]:
    """index 번째 장의 아래 1/3 밝기 합(자막 자리)."""
    proc = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True, timeout=60)
    frame = proc.stdout[index * w * h:(index + 1) * w * h]
    return list(frame[(h * 2 // 3) * w:])


# 화면 검사(node): vnew.js 에서 아래 구역을 잘라 가짜 DOM 위에서 돌린다(검사가 api · 타이머를 쥔다).
_JS_SLICES = (
    ("  function el(tag, className, text)", "  function token()"),
    ("  /* ================================================================ ⑦ 편집(영상 편집 타임라인, 지시서 111)",
     "  /* ---------------------------------------------------------------- 전역 */"),
    ("  /* ================================================================ ⑥ 사운드 — 대사별 실제 말/속마음(작업 122)",
     "  /* ================================================================ ⑥ 사운드 — 인물 목소리(작업 203)"),
)
_JS_FUNCS = ("errMessage", "downloadLink", "planError", "castingUsdText", "isCostMismatch", "costChangedText")


def _js_function(source: str, name: str) -> str:
    m = re.search(r"\n  function\s+" + re.escape(name) + r"\s*\(", source)
    if not m:
        raise AssertionError(f"vnew.js 에 function {name} 이 없습니다")
    i, depth = source.index("{", m.end()), 0
    for j in range(i, len(source)):
        depth += {"{": 1, "}": -1}.get(source[j], 0)
        if depth == 0:
            return source[m.start() + 1: j + 1] + "\n"
    raise AssertionError(f"function {name} 의 끝을 찾지 못했습니다")


def _js_code(vjs: str) -> str:
    parts = []
    for start, end in _JS_SLICES:
        a = vjs.index(start)
        parts.append(vjs[a:vjs.index(end, a)])
    return "".join(parts) + "".join(_js_function(vjs, n) for n in _JS_FUNCS)


_UI_HARNESS = r"""
function N(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.style = {}; this.attrs = {}; this.listeners = {};
  this.parentNode = null; this.className = ""; this._text = ""; this.disabled = false; this.title = ""; this.value = ""; this.type = "";
  this.checked = false; this.clientWidth = 0; this.clientHeight = 0; this.scrollLeft = 0; var self = this;
  this.classList = { add: function (c) { var l = self.className.split(/\s+/).filter(Boolean); if (l.indexOf(c) < 0) l.push(c); self.className = l.join(" "); },
    remove: function (c) { self.className = self.className.split(/\s+/).filter(function (x) { return x && x !== c; }).join(" "); },
    contains: function (c) { return self.className.split(/\s+/).indexOf(c) >= 0; } }; }
Object.defineProperty(N.prototype, "textContent", {
  get: function () { return this._text + this.children.map(function (c) { return c.textContent; }).join(""); },
  set: function (v) { this._text = String(v); this.children = []; } });
Object.defineProperty(N.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
N.prototype.appendChild = function (c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; };
N.prototype.removeChild = function (c) { var i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; };
N.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
N.prototype.getAttribute = function (k) { return this.attrs[k] === undefined ? null : this.attrs[k]; };
N.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
N.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
N.prototype.removeEventListener = function (t, f) { this.listeners[t] = (this.listeners[t] || []).filter(function (g) { return g !== f; }); };
N.prototype.fire = function (t, ev) { var e = ev || {}; if (!e.target) e.target = this; e.preventDefault = e.preventDefault || function () {};
  e.stopPropagation = e.stopPropagation || function () {}; (this.listeners[t] || []).slice().forEach(function (f) { f(e); }); return e; };
N.prototype.click = function () { if (!this.disabled) this.fire("click", {}); };
N.prototype.focus = function () {}; N.prototype.play = function () { return Promise.resolve(); }; N.prototype.pause = function () {};
N.prototype.load = function () {}; N.prototype.setPointerCapture = function () {}; N.prototype.releasePointerCapture = function () {};
N.prototype.getBoundingClientRect = function () { return { left: 0, right: 1000, top: 0, bottom: 40 }; };
var docL = {};
var document = { createElement: function (t) { return new N(t); }, createElementNS: function (ns, t) { return new N(t); }, body: new N("body"),
  activeElement: null, addEventListener: function (t, f) { (docL[t] = docL[t] || []).push(f); },
  removeEventListener: function (t, f) { docL[t] = (docL[t] || []).filter(function (g) { return g !== f; }); } };
var timers = [], seq = 0;
var window = { setTimeout: function (fn, ms) { seq += 1; timers.push({ id: seq, fn: fn, ms: ms, alive: true }); return seq; },
  clearTimeout: function (id) { timers.forEach(function (t) { if (t.id === id) t.alive = false; }); },
  requestAnimationFrame: function () { return 0; }, cancelAnimationFrame: function () {}, innerWidth: 1200, innerHeight: 800,
  confirm: function () { return true; }, localStorage: { getItem: function () { return "1"; }, setItem: function () {} },
  addEventListener: function () {}, removeEventListener: function () {} };
function fireTimers(ms) { var n = 0; timers.slice().forEach(function (t) { if (t.alive && t.ms === ms) { t.alive = false; n += 1; t.fn(); } }); return n; }
function walk(n, out) { out.push(n); n.children.forEach(function (c) { walk(c, out); }); return out; }
function byClass(root, c) { return walk(root, []).filter(function (n) { return (" " + n.className + " ").indexOf(" " + c + " ") >= 0; }); }
function byText(root, t) { return walk(root, []).filter(function (n) { return n.tagName === "BUTTON" && n.textContent.indexOf(t) >= 0; }); }
function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
async function settle() { for (var i = 0; i < 12; i++) await tick(); }
var calls = [], answer = function () { return undefined; };
function api(path, init) {
  var method = (init && init.method) || "GET";
  var body = init && init.body ? JSON.parse(init.body) : null;
  if (String(path).indexOf("/model-notes") >= 0) return Promise.resolve({ notes: [], counts: { byModel: {}, byKind: {} } });
  calls.push([method, path, body]);
  var res = answer(method, path, body);
  if (res === undefined) return Promise.reject({ message: "가짜 응답 없음 " + method + " " + path });
  return res.err ? Promise.reject(res.err) : Promise.resolve(res.ok);
}
function token() { return ""; }
var state = { mounted: true, planStep: 7, projectId: "p1", plan: {} };
"""

_UI_DRIVER = r"""
(async function () {
  var out = {};
  try {
    // 작업 219b: 프리미어 묶음 칸은 EDIT_PREMIERE_UI(화면 기본 false)일 때만 만든다 — 이 검사는 켠 길을 본다(끈 길은 check_review_fixes_219b)
    EDIT_PREMIERE_UI = true;
    var ctx = editNewCtx();
    editCtx = ctx;
    var mats = [{ mid: "m1", sourceKey: "K1", label: "테이크 1", duration: 6, fps: 24, kind: "take", group: "current", retake: { can: true } },
                { mid: "m2", sourceKey: "K2", label: "테이크 2", duration: 6, fps: 24, kind: "take", group: "current" }];
    var view = { variant: "webtoon", variants: [], etag: "e0", saved: true, savedVariants: { webtoon: true }, defaultClips: [], limits: {},
      doc: { clips: [{ id: "c1", sourceKey: "K1", in: 0, out: 3, muted: false }, { id: "c2", sourceKey: "K2", in: 0, out: 3, muted: false },
                     { id: "c3", sourceKey: "K1", in: 3, out: 6, muted: false }],
             captions: [{ id: "s1", start: 0.5, end: 1.5, text: "안녕", lineId: "e1", style: {} }] },
      materials: mats, export: { status: "none" }, exportHistory: [], retake: { enabled: true },
      textFx: { burnIn: { available: true, font: "Noto Sans KR", reason: "" } } };
    editApplyView(ctx, view, false);
    editBuild(ctx);
    var root = ctx.root;
    function caps() { return ctx.tx.captions.map(function (c) { return [c.id, c.start, c.end, c.text]; }); }
    function item(i) { return byClass(ctx.dom.txCapLane, "vnew-edit-txitem")[i || 0]; }
    out.built = [byClass(root, "vnew-edit-txlane").length, byClass(ctx.dom.txCapLane, "vnew-edit-txitem").length, ctx.pps,
      byText(root, "자막 자동 만들기").length, byText(root, "SRT 내려받기").length, byText(root, "프리미어 프로젝트로 내보내기 (XML + EDL + 소스)").length];
    // 끌어 옮기기 +50px = +0.5초 · 끝 끌기는 클립 경계(3초)에 붙는다
    var node = item(0);
    node.fire("pointerdown", { clientX: 100, pointerId: 1, button: 0, pointerType: "mouse" });
    node.fire("pointermove", { clientX: 150, pointerId: 1 });
    node.fire("pointerup", { clientX: 150, pointerId: 1 });
    out.moved = caps();
    var edge = byClass(item(0), "vnew-edit-txedge")[1];
    edge.fire("pointerdown", { clientX: 300, pointerId: 2, button: 0, pointerType: "mouse" });
    item(0).fire("pointermove", { clientX: 396, pointerId: 2 }); // 붙잡은 요소 = 자막 상자(setPointerCapture)
    item(0).fire("pointerup", { clientX: 396, pointerId: 2 });
    out.trimmed = caps();
    editDo(ctx, "undo");
    out.undo = caps();
    editDo(ctx, "redo");
    out.redo = caps();
    editTxSelect(ctx, "caption", "s1");
    ctx.t = 2;
    editDo(ctx, "split");
    out.split = caps().map(function (c) { return [c[1], c[2], c[3]]; });
    editTxSelect(ctx, "caption", ctx.tx.captions[1].id);
    editDo(ctx, "delete");
    out.deleted = [caps().length, ctx.clips.length];
    // 글 고치기 → [이 대사도 고치기](기본 켬) → ⑥ 대사 고침 PUT(같은 말하는 사람 · 목소리 · 말투)
    var soundView = { lines: [{ id: "e1", speaker: "민지", lineKo: "안녕", voice: "spoken", start: 0.5, end: 1.5 }],
      lineRedo: { enabled: true, cast: ["민지", "준호"], limits: {}, lines: { e1: { override: { lineId: "e1", textKo: "안녕?", speaker: "민지", thought: false,
        deliveryKo: "작게", original: { lineKo: "안녕" } } } } } };
    answer = function (m, p, b) {
      if (m === "GET" && p.indexOf("/sound?variant=") >= 0) return { ok: soundView };
      if (m === "PUT" && p.indexOf("/sound/lines/override") >= 0) return { ok: soundView };
      if (m === "PUT" && /\/edit$/.test(p)) return { ok: { etag: "e" + calls.length } };
      return undefined;
    };
    item(0).fire("dblclick", {});
    var ed = byClass(root, "vnew-edit-txeditor")[0];
    var checkbox = byClass(ed, "vnew-edit-txeditor-line")[0];
    byClass(ed, "vnew-edit-txeditor-text")[0].value = "새 글";
    out.editor = [!!checkbox && checkbox.checked, byClass(ed, "vnew-edit-txeditor-check")[0].textContent];
    byClass(ed, "vnew-edit-txeditor-ok")[0].click();
    await settle();
    out.textEdit = [caps()[0][3], calls.filter(function (c) { return c[1].indexOf("/sound") >= 0; }).map(function (c) { return [c[0], c[2]]; })];
    // 전환 메뉴: 블랙 0.5초 → 컷, 오른쪽 누르기로도 열린다
    editTrOpen(ctx, 0, ctx.trEls[0].btn);
    byClass(ctx.trMenu.el, "vnew-edit-txdip-item").filter(function (b) { return b.attrs["aria-label"] === "블랙 전환 0.5초"; })[0].click();
    out.dip = [JSON.stringify(ctx.clips[0].transition), ctx.trEls[0].btn.classList.contains("is-dip-black"), editStarts(ctx.clips).total];
    editTrOpen(ctx, 0, ctx.trEls[0].btn);
    byText(ctx.trMenu.el, "컷")[0].click();
    out.cut = [ctx.clips[0].transition === undefined];
    ctx.trEls[1].btn.fire("contextmenu", {});
    out.cut.push(!!ctx.trMenu && ctx.trMenu.index === 1);
    editTrClose(ctx);
    // 색 보정: 슬라이더(끄는 동안 미리보기 → 놓으면 한 단계) · 같은 테이크 · 원래대로 · 실행 취소
    editSelect(ctx, 0);
    var slider = byClass(root, "vnew-edit-txslider is-brightness")[0];
    slider.value = "40";
    slider.fire("input", {});
    out.live = [ctx.vids[ctx.cur].style.filter, ctx.clips[0].color === undefined];
    slider.fire("change", {});
    byText(root, "같은 테이크 모두에 적용")[0].click();
    out.color = ctx.clips.map(function (c) { return c.color ? c.color.brightness : 0; });
    editSelect(ctx, 0);
    byClass(root, "vnew-edit-txbtn is-reset")[0].click();
    out.reset = ctx.clips.map(function (c) { return c.color ? c.color.brightness : 0; });
    editDo(ctx, "undo");
    out.reset.push(ctx.clips[0].color ? ctx.clips[0].color.brightness : 0);
    // 저장 바디: 자막 · 색(옛 키는 그대로)
    var body = editTxBody(ctx, editSaveBody(ctx.etag, ctx.variant, ctx.clips));
    out.body = [Object.keys(body).sort(), body.captions.length, body.clips[0].color, "fadeIn" in body];
    // 프리미어: 저장 → POST /edit/premiere → GET 조회 → 내려받기 링크
    answer = function (m, p, b) {
      if (m === "PUT" && /\/edit$/.test(p)) return { ok: { etag: "e" + calls.length } };
      if (m === "POST" && /\/edit\/premiere$/.test(p)) return { ok: { ok: true, started: true, premiere: { status: "queued" } } };
      if (m === "GET" && p.indexOf("/edit/premiere?variant=webtoon") >= 0) return { ok: { premiere: { status: "done", url: "https://x/p.zip", fileName: "p1-webtoon-1.zip" } } };
      return undefined;
    };
    fireTimers(800);
    await settle();
    byText(root, "프리미어 프로젝트로 내보내기")[0].click();
    await settle();
    var queued = byClass(ctx.dom.txPrem, "vnew-edit-txprem-status")[0].textContent;
    fireTimers(3000);
    await settle();
    var link = byClass(ctx.dom.txPrem, "vnew-download")[0];
    out.premiere = [calls.filter(function (c) { return c[1].indexOf("/premiere") >= 0; }).map(function (c) { return c[0] + " " + c[1] + " " + JSON.stringify(c[2]); }),
      queued, link ? [link.href, link.textContent] : null];
    answer = function (m, p) { return m === "POST" ? { err: { status: 404, type: "NotFound", message: "없음" } } : undefined; };
    ctx.txPrem.state = null;
    byText(root, "프리미어 프로젝트로 내보내기")[0].click();
    await settle();
    out.premiere404 = byClass(ctx.dom.txPrem, "vnew-edit-txprem-status")[0].textContent;
    // ⑦ 안 대사 고치기: ⑥ 함수(lrdRowTools · lrdEditor · lrdSave)가 ⑦ 창 안에서 · 저장하면 ⑦ 자막도 새 대사 · 끝난 다시 만들기 → 새 영상으로 바꾸기
    var saved = JSON.parse(JSON.stringify(soundView));
    saved.lineRedo.lines.e1 = { override: { lineId: "e1", textKo: "고친 대사", speaker: "민지", thought: true, deliveryKo: "", original: { lineKo: "안녕" } },
      job: { status: "done", usd: 0.2 } };
    answer = function (m, p) {
      if (m === "GET" && p.indexOf("/sound?variant=") >= 0) return { ok: soundView };
      if (m === "PUT" && p.indexOf("/sound/lines/override") >= 0) return { ok: saved };
      if (m === "PUT" && /\/edit$/.test(p)) return { ok: { etag: "e" + calls.length } };
      if (m === "GET" && p.indexOf("/edit?variant=webtoon") >= 0) return { ok: { materials: mats.concat([{ mid: "m3", sourceKey: "K1v2", label: "테이크 1 v2",
        duration: 6, fps: 24, kind: "take", group: "retake" }]) } };
      if (m === "GET" && p.indexOf("/edit/retake/m1") >= 0) return { ok: { kind: "take", versions: [
        { n: 1, original: true, status: "completed", sourceKey: "K1", label: "테이크 1" },
        { n: 2, original: false, status: "completed", sourceKey: "K1v2", label: "테이크 1 v2" }] } };
      return undefined;
    };
    editTxLineOpen(ctx, "e1", ["c1"]);
    await settle();
    var panel = ctx.txLine.panel;
    var lrdEd = byClass(panel, "vnew-lrd-editor")[0];
    out.lineOpen = [!!lrdEd, byClass(panel, "vnew-lrd-text")[0].value, byClass(panel, "vnew-lrd-speaker")[0].value,
      byClass(panel, "vnew-lrd-voice")[0].value, byClass(panel, "vnew-lrd-delivery")[0].value, byText(panel, "AI로 다시 쓰기").length,
      byText(panel, "이 대사 영상 다시 만들기").length, soundAlive(ctx.txLine)];
    byClass(panel, "vnew-lrd-text")[0].value = "고친 대사";
    byClass(panel, "vnew-lrd-text")[0].fire("input", {});
    byClass(panel, "vnew-lrd-save")[0].click();
    await settle();
    out.lineSaved = [caps()[0][3], ctx.tx.captions[0].style.italic, byText(ctx.txLine.panel, "새 영상으로 바꾸기").length];
    var c1 = editCloneClip(ctx.clips[0]);
    c1.color = { saturation: -30 };
    c1.transition = { type: "dipWhite", seconds: 1 };
    c1.in = 0.5; c1.out = 2.5; c1.speed = 1.5; c1.muted = true;
    editCommit(ctx, [c1].concat(ctx.clips.slice(1)), {});
    byText(ctx.txLine.panel, "새 영상으로 바꾸기")[0].click();
    await settle();
    out.swap = [ctx.clips.map(function (c) { return [c.id, c.sourceKey, c.in, c.out, c.speed || 1, c.muted, JSON.stringify(c.color || null),
      JSON.stringify(c.transition || null)]; }), ctx.txLine.swapNote.textContent];
  } catch (e) {
    out.error = String(e && e.stack || e).slice(0, 1500);
  }
  console.log(JSON.stringify(out));
})();
"""


def check_edit_text_215b() -> bool:  # noqa: C901, PLR0915 — 한 검사 함수(다른 작업 검사와 같은 모양)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1500:])

    from src.vnew import edit as E  # noqa: PLC0415
    from src.vnew import edit_text as T  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def kind_of(fn) -> list:
        try:
            fn()
            return ["통과"]
        except VpocError as exc:
            return [exc.kind, exc.status]

    durations = {K1: 6.0, K2: 6.0}
    saved = {name: getattr(E, name) for name in ("STORE", "_load_gen", "_saved_docs", "_all_items", "_retake_state", "_ensure_meta",
                                                 "_spawn_export")}
    saved_flags = (vset.VNEW_EDIT, getattr(vset, "VNEW_SOUND", None))
    saved_burn = T._cache.pop("burn", None)  # noqa: SLF001

    def fakes(store: _Store) -> None:
        E.STORE = store
        E._load_gen = lambda owner, pid: ({}, {}, "g1")  # noqa: SLF001
        E._all_items = lambda gen, variant, retakes: [{"sourceKey": K1}, {"sourceKey": K2}]  # noqa: SLF001
        E._retake_state = lambda *a: None  # noqa: SLF001
        E._ensure_meta = lambda owner, pid, gen_id, keys, budget_s=None: ({k: META[k] for k in keys if k in META}, True)  # noqa: SLF001

    try:
        # ================= (tx-a) 옛 문서 그대로 · 새 키 =================
        try:
            old = E._clean_clips(copy.deepcopy(OLD_CLIPS), {K1, K2}, durations, "t")  # noqa: SLF001
            expect("tx-a", old, [
                {"id": "c1", "sourceKey": K1, "in": 0.0, "out": 2.0, "muted": False, "transition": {"type": "dissolve", "seconds": 0.5}},
                {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0, "muted": True, "speed": 0.5, "audioTail": 0.5, "label": "둘"},
                {"id": "c3", "sourceKey": K1, "in": 3.0, "out": 5.0, "muted": False}], "옛 클립 정리 결과 그대로(새 키 없음)")
            plain = E._doc_hash("webtoon", old)  # noqa: SLF001
            expect("tx-a", [T.extend_hash(plain, {"clips": old}, old), T.export_extras({"clips": old}), T.text_fx({"captionBurnIn": True})],
                   [plain, {}, {}], "효과 없는 문서: 해시 · 내보내기 사본 · textFx 그대로")
            fx = copy.deepcopy(OLD_CLIPS)
            fx[1]["transition"] = {"type": "dipBlack", "seconds": 0.5}
            fx[2]["color"] = {"brightness": 20, "tint": 0, "temperature": -30}
            cleaned = E._clean_clips(fx, {K1, K2}, durations, "t")  # noqa: SLF001
            expect("tx-a", [cleaned[1].get("transition"), cleaned[2].get("color")],
                   [{"type": "dipBlack", "seconds": 0.5}, {"brightness": 20, "temperature": -30}], "블랙 전환 · 색 보정(0 은 뺀다) 저장")
            expect("tx-a", T.extend_hash(E._doc_hash("webtoon", cleaned), {}, cleaned) != E._doc_hash("webtoon", cleaned), True,  # noqa: SLF001
                   "클립 효과가 있으면 해시가 바뀐다(옛 내보내기를 다시 쓰지 않는다)")
            bad_dip = copy.deepcopy(OLD_CLIPS)
            bad_dip[1]["transition"] = {"type": "dipBlack", "seconds": 0.3}
            bad_color = copy.deepcopy(OLD_CLIPS)
            bad_color[0]["color"] = {"contrast": 140}
            last_dip = copy.deepcopy(OLD_CLIPS)
            last_dip[2]["transition"] = {"type": "dipWhite", "seconds": 0.5}
            expect("tx-a", [kind_of(lambda: E._clean_clips(bad_dip, {K1, K2}, durations, "t")),  # noqa: SLF001
                            kind_of(lambda: E._clean_clips(bad_color, {K1, K2}, durations, "t")),  # noqa: SLF001
                            kind_of(lambda: E._clean_clips(last_dip, {K1, K2}, durations, "t"))],  # noqa: SLF001
                   [["BadTransition", 400], ["BadClipColor", 400], ["BadTransition", 400]], "틀린 전환 길이 · 색 범위 · 마지막 클립 전환 400")
            short = [{"id": "a", "sourceKey": K1, "in": 0.0, "out": 0.6, "muted": False, "transition": {"type": "dipWhite", "seconds": 2.0}},
                     {"id": "b", "sourceKey": K2, "in": 0.0, "out": 3.0, "muted": False}]
            tiny = [dict(short[0], out=0.2, transition={"type": "dipWhite", "seconds": 0.25}), short[1]]
            expect("tx-a", [E._clean_clips(short, {K1, K2}, durations, "t")[0].get("transition"),  # noqa: SLF001
                            E._clean_clips(tiny, {K1, K2}, durations, "t")[0].get("transition")],  # noqa: SLF001
                   [{"type": "dipWhite", "seconds": 0.5}, None], "짧은 클립: 맞는 가장 긴 길이(0.6초 × 0.9 → 0.5) · 없으면 컷")
            store = _Store()
            fakes(store)
            res = E.save_doc(OWNER, PID, {"version": 0, "variant": "webtoon", "clips": copy.deepcopy(OLD_CLIPS)})
            expect("tx-a", sorted(res["doc"]), ["audio", "clips", "updatedAt", "variant", "version"], "옛 바디 저장 = 215b 전 문서 키만")
            caps = [{"id": "s1", "start": 0.5, "end": 1.5, "text": " 안녕\r\n  ", "style": {"italic": True}, "lineId": "e1"}]
            titles = [{"id": "t1", "start": 0, "end": 2, "text": "1화", "pos": 2, "size": "xl", "fadeIn": 0.33, "box": True}]
            res = E.save_doc(OWNER, PID, {"etag": res["etag"], "variant": "webtoon", "clips": copy.deepcopy(OLD_CLIPS), "captions": caps,
                                          "titles": titles, "fadeIn": 1, "fadeOut": 0, "captionBurnIn": False})
            doc = res["doc"]
            expect("tx-a", [doc.get("captions"), doc.get("titles"), doc.get("fadeIn"), "fadeOut" in doc, doc.get("captionBurnIn")],
                   [[{"id": "s1", "start": 0.5, "end": 1.5, "text": "안녕", "lineId": "e1",
                      "style": {"size": "m", "color": "#ffffff", "back": "outline", "pos": "bottom", "italic": True}}],
                    [{"id": "t1", "start": 0.0, "end": 2.0, "text": "1화", "size": "xl", "pos": 2, "fadeIn": 0.35, "fadeOut": 0.5,
                      "box": True, "color": "#ffffff"}], 1.0, False, False], "새 키 저장(글 정리 · 기본 모양 · 페이드 0.05 단위 · 0 은 저장 안 함)")
            bad_bodies = [{"captions": [{"id": "x", "start": 1, "end": 1.05, "text": "a"}]},
                          {"captions": [{"id": "x", "start": 0, "end": 1, "text": "a", "style": {"color": "red"}}]},
                          {"titles": [{"id": "t", "start": 0, "end": 1, "text": "a", "pos": 10}]}, {"fadeIn": 0.7}, {"captionBurnIn": "no"}]
            expect("tx-a", [kind_of(lambda b=b: T.clean_doc(b, "t"))[0] for b in bad_bodies],
                   ["BadCaption", "BadCaption", "BadTitle", "BadFade", "BadCaption"], "틀린 자막 · 타이틀 · 페이드 · 굽기 값 400")
        except Exception as exc:  # noqa: BLE001
            crash("tx-a", exc)

        # ================= (tx-b) 자막 자동 만들기 =================
        try:
            export_clips = [{"id": "c1", "sourceKey": K1, "in": 0.0, "out": 2.0}, {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0}]
            lines = [{"id": "e1", "speaker": "민지", "lineKo": "원래 대사", "voice": "spoken", "start": 0.5, "end": 1.5},
                     {"id": "e2", "speaker": "준호", "lineKo": "속으로 말", "voice": "thought", "start": 2.5, "end": 3.5},
                     {"id": "e3", "speaker": "민지", "lineKo": "경계", "voice": "spoken", "start": 1.8, "end": 2.2},
                     {"id": "e4", "speaker": "민지", "lineKo": "편집에 없음", "voice": "spoken", "start": 1.3, "end": 1.45}]
            overrides = {"e1": {"lineId": "e1", "textKo": "새 대사", "thought": True}, "e2": {"lineId": "e2", "textKo": "고친 속마음", "thought": False}}
            now = [{"sourceKey": K2, "in": 1.0, "out": 3.0}, {"sourceKey": K1, "in": 0.0, "out": 1.2}]
            from src.vnew import line_redo  # noqa: PLC0415
            from src.vnew import sound  # noqa: PLC0415

            pieces = line_redo._pieces(OWNER, PID, "g1", {}, {}, "webtoon", {"kind": "export", "key": EXP, "clips": export_clips}, 4.0)  # noqa: SLF001
            caps, skipped = T.build_captions(lines, overrides, pieces, now)
            expect("tx-b", [[c["lineId"], c["start"], c["end"], c["text"], c["style"]["italic"]] for c in caps] + [skipped],
                   [["e3", 0.0, 0.2, "경계", False], ["e2", 0.5, 1.5, "고친 속마음", False], ["e1", 2.5, 3.2, "새 대사", True], 1],
                   "⑥ 시간 → 순서 바꾸고 자른 ⑦ 클립 · 대사 고침 글/속마음이 먼저 · 경계 줄은 남은 쪽 · 편집에 없는 줄은 뺀다")
            expect("tx-b", T.map_span(0.5, 1.5, pieces, [{"sourceKey": K1, "in": 0.0, "out": 2.0, "speed": 0.5}]), (1.0, 3.0),
                   "속도 0.5 클립 = 타임라인 두 배")
            saved_sound = {(sound, "_ensure_doc"): sound._ensure_doc, (sound, "_base_source"): sound._base_source,  # noqa: SLF001
                           (line_redo, "read_overrides"): line_redo.read_overrides}
            try:
                vset.VNEW_SOUND = True
                E._load_gen = lambda owner, pid: ({}, {}, "g1")  # noqa: SLF001
                sound._ensure_doc = lambda *a: ({"source": {"kind": "export", "key": EXP, "duration": 4.0}, "lines": lines}, False)  # noqa: SLF001
                sound._base_source = lambda *a: {"kind": "export", "key": EXP, "n": 1, "clips": export_clips}  # noqa: SLF001
                line_redo.read_overrides = lambda owner, pid, gen_id: overrides
                got = T.auto_captions(OWNER, PID, {"variant": "webtoon", "clips": now + [{"bad": 1}]})
                expect("tx-b", [[c["id"], c["text"]] for c in got["captions"]] + [got["skipped"], got["lines"], got["source"]],
                       [["cap-e3", "경계"], ["cap-e2", "고친 속마음"], ["cap-e1", "새 대사"], 1, 4, "export"], "auto_captions(가짜 ⑥ 문서 · 고침)")
                expect("tx-b", [kind_of(lambda: T.auto_captions(OWNER, PID, {"variant": "x", "clips": now})),
                                kind_of(lambda: T.auto_captions(OWNER, PID, {"variant": "real", "clips": []}))],
                       [["BadVariant", 400], ["EmptyTimeline", 409]], "그림체 · 빈 타임라인")
            finally:
                for (mod, name), fn in saved_sound.items():
                    setattr(mod, name, fn)
        except Exception as exc:  # noqa: BLE001
            crash("tx-b", exc)

        # ================= (tx-c) SRT · ASS · -vf =================
        try:
            caps = [{"id": "a", "start": 0.5, "end": 1.5, "text": "안녕", "style": {}},
                    {"id": "b", "start": 61.25, "end": 62, "text": "속마음\n둘째 줄", "style": {"italic": True}}]
            expect("tx-c", T.srt_text(caps), "1\n00:00:00,500 --> 00:00:01,500\n안녕\n\n2\n00:01:01,250 --> 00:01:02,000\n<i>속마음</i>\n<i>둘째 줄</i>\n",
                   "SRT(번호 · 쉼표 밀리초 · 기울임 <i>)")
            ccaps = T.clean_captions(caps + [{"id": "c", "start": 3, "end": 4, "text": "{위}\\", "style": {"back": "box", "pos": "top"}}], "t")
            ctitles = T.clean_titles([{"id": "t", "start": 0, "end": 3, "text": "1화", "pos": 3, "size": "m", "fadeIn": 0.5, "fadeOut": 0.25,
                                       "box": True, "color": "#ffe14d"}], "t")
            ass = T.ass_text(ccaps, ctitles, 1280, 720, "Noto Sans KR")
            expect("tx-c", ["PlayResX: 1280" in ass, "PlayResY: 720" in ass,
                            "Style: S0,Noto Sans KR,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,2,64,64,43,1" in ass,
                            ",0,-1,0,0,100,100,0,0,1,4,0,2,64,64,43,1" in ass,
                            "&H60000000,&H60000000,0,0,0,0,100,100,0,0,3,6,0,8,64,64,43,1" in ass,
                            "Dialogue: 0,0:01:01.25,0:01:02.00,S2,,0,0,0,,속마음\\N둘째 줄" in ass,
                            "｛위｝＼" in ass,
                            "Dialogue: 1,0:00:00.00,0:00:03.00,S3,,0,0,0,,{\\fad(500,250)}1화" in ass,
                            "Style: S3,Noto Sans KR,54,&H004DE1FF,&H004DE1FF,&H60000000,&H60000000,0,0,0,0,100,100,0,0,3,6,0,9,64,64,36,1" in ass],
                   [True] * 9, "ASS: 화면 크기 · 테두리 아래 · 기울임 · 상자 위(BorderStyle 3 · \\an8) · 줄바꿈 \\N · 중괄호 전각 · 타이틀 9칸 3 = \\an9 · \\fad · 색")
            plan = T.finish_plan([{"in": 0, "out": 1, "color": {"brightness": 50}}, {"in": 0, "out": 1, "transition": {"type": "dipWhite", "seconds": 0.5}},
                                  {"in": 0, "out": 1}], [24, 24, 24], None, 24,
                                 {"captions": ccaps, "titles": ctitles, "fadeIn": 0.5, "fadeOut": 1.0})
            vf = T.video_filter(plan)
            expect("tx-c", vf, "eq=brightness=0.1250:contrast=1.0000:saturation=1.0000:enable='between(n,0,23)',"
                               "fade=t=out:s=42:n=6:c=white:enable='between(n,42,47)',fade=t=in:s=48:n=6:c=white:enable='between(n,48,53)',"
                               "fade=t=in:s=0:n=12:c=black,fade=t=out:s=47:n=24:c=black,ass=filename=subs.ass:fontsdir=fonts",
                   "-vf: 색(그 클립 장) → 화이트 전환(경계 48) → 시작/끝 페이드 → ass")
            expect("tx-c", [T.finish_plan([{"in": 0, "out": 1}], [24], None, 24, {}), T.finish_plan([{"in": 0, "out": 1}], [24], None, 24, None)],
                   [None, None], "효과가 없으면 마무리 패스 없음")
            saved_find = T._find_font  # noqa: SLF001
            try:
                T._find_font = lambda: ""  # noqa: SLF001 — 앱 이미지(python:3.13-slim)처럼 한글 글꼴이 없을 때
                T._cache.pop("burn", None)  # noqa: SLF001
                doc_caps = {"captions": [{"id": "a", "start": 0, "end": 1, "text": "가"}]}
                expect("tx-c", [kind_of(lambda: T.check_export(doc_caps, "t")), kind_of(lambda: T.check_export({**doc_caps, "captionBurnIn": False}, "t")),
                                kind_of(lambda: T.check_export({"titles": [{"id": "t", "start": 0, "end": 1, "text": "1화"}]}, "t")),
                                T.view_info()["burnIn"]["available"], T.view_info()["burnIn"]["reason"] == T.FONT_MISSING_TEXT],
                       [["CaptionFontMissing", 409], ["통과"], ["CaptionFontMissing", 409], False, True],
                       "글꼴이 없으면: 굽기 켬 + 자막 409 · 굽기 끔이면 내보내기 · 타이틀 409 · 화면에 이유")
            finally:
                T._find_font = saved_find  # noqa: SLF001
                T._cache.pop("burn", None)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            crash("tx-c", exc)

        # ================= (tx-e) 색 보정 값 범위 =================
        try:
            vals = []
            for sign in (1, -1):
                for item in T.color_filters({k: 100 * sign for k in T.COLOR_KEYS}):
                    vals += [(m.group(1), float(m.group(2))) for m in re.finditer(r"(\w+)=(-?[\d.]+)", item)]
            ranges = {"brightness": (-1, 1), "contrast": (-1000, 1000), "saturation": (0, 3), "temperature": (1000, 40000),
                      "gs": (-1, 1), "gm": (-1, 1), "gh": (-1, 1)}
            expect("tx-e", [name for name, v in vals if not ranges[name][0] <= v <= ranges[name][1]], [], "±100 끝 값이 필터 범위 안")
            expect("tx-e", [T.color_filters({"temperature": 100}), T.color_filters({"temperature": -100}), T.color_filters({"tint": 100}),
                            T.color_of({"color": {"brightness": 300, "saturation": "x"}}), T.color_of({"color": {"saturation": -40.4}})],
                   [["colortemperature=temperature=3500"], ["colortemperature=temperature=9500"],
                    ["colorbalance=gs=-0.1500:gm=-0.1500:gh=-0.1500"], {}, {"saturation": -40}],
                   "따뜻함 = 낮은 K · 차가움 = 높은 K · 자홍 = 초록 빼기 · 범위 밖/글자는 버림 · 정수 반올림")
        except Exception as exc:  # noqa: BLE001
            crash("tx-e", exc)

        # ================= (tx-d) ffmpeg 작은 클립 =================
        ff = _ffmpeg()
        if not ff:
            warns.append("(tx-d) ffmpeg 가 없어 작은 클립 검사를 건너뜁니다")
        else:
            try:
                with tempfile.TemporaryDirectory(prefix="chk215b-") as tmp:
                    one = {"in": 0.0, "out": 1.0}

                    def run_finish(colors, clips, textfx, name):
                        work = os.path.join(tmp, name)
                        os.makedirs(work)
                        out = os.path.join(work, "edit.mp4")
                        _join(ff, out, colors)
                        done = T.finish({"textFx": textfx} if textfx else {}, clips, [24] * len(colors), None, 24, 64, 36, work, out)
                        return done, _frames(ff, out), out

                    done, fr, _ = run_finish(["red", "green"], [dict(one, transition={"type": "dipBlack", "seconds": 0.5}), dict(one)], {}, "dip")
                    expect("tx-d", [done, len(fr), max(fr[24]) <= 20, fr[17][0] > 150, 20 < fr[21][0] < 150, fr[30][1] > 80, fr[0][0] > 150],
                           [True, 48, True, True, True, True, True], "블랙 0.5초: 경계 장(24) 검다 · 앞 3장 어두워짐 · 길이 48장 그대로")
                    _, fr, _ = run_finish(["red", "green"], [dict(one, transition={"type": "dipWhite", "seconds": 1.0}), dict(one)], {}, "white")
                    expect("tx-d", [len(fr), min(fr[24]) >= 230, fr[11][0] > 150 and fr[11][1] < 60], [48, True, True],
                           "화이트 1초: 경계 장 희다 · 반 길이(12장) 앞은 그대로")
                    _, fr, _ = run_finish(["white", "white"], [dict(one), dict(one)], {"fadeIn": 0.5, "fadeOut": 0.5}, "edge")
                    expect("tx-d", [len(fr), max(fr[0]) <= 20, max(fr[47]) <= 20, min(fr[24]) >= 230], [48, True, True, True],
                           "시작 페이드 첫 장 검다 · 끝 페이드 마지막 장 검다 · 가운데 그대로")
                    _, fr, _ = run_finish(["gray", "gray"], [dict(one, color={"temperature": 100}), dict(one, color={"tint": 100})], {}, "color")
                    expect("tx-d", [fr[5][0] > fr[5][2] + 20, fr[30][1] < fr[30][0] - 10, abs(fr[5][0] - fr[30][0]) < 60],
                           [True, True, True], "색온도 +100 = 따뜻함(R > B) · 색조 +100 = 자홍(G 낮음) · 클립 구간마다 따로")
                    info = T.burn_info()
                    if info["available"]:
                        caps = T.clean_captions([{"id": "s", "start": 0.0, "end": 1.5, "text": "가나다", "style": {"size": "l"}}], "t")
                        _, fr, out = run_finish(["black", "black"], [dict(one), dict(one)], {"captions": caps}, "burn")
                        lit = [sum(1 for v in _region(ff, out, k) if v > 50) for k in (12, 44)]
                        expect("tx-d", [len(fr), lit[0] > 10, lit[1]], [48, True, 0], f"자막 굽기({info['font']}): 0~1.5초 아래쪽 글자 · 뒤는 없음")
                    else:
                        warns.append(f"(tx-d) 한글 글꼴·ass 필터가 없어 굽기 화면 검사를 건너뜁니다: {info['reason']}")
                    # run_export 끝까지(진짜 stitcher): start_export 사본 textFx · 해시 · 마무리 패스
                    store = _Store()
                    fakes(store)
                    for key, color in ((K1, "red"), (K2, "green")):
                        path = os.path.join(tmp, color + ".mp4")
                        _make_clip(ff, path, color, 2)
                        store.blobs[key] = Path(path).read_bytes()
                    clips = [{"id": "c1", "sourceKey": K1, "in": 0.0, "out": 1.0, "muted": False, "transition": {"type": "dipBlack", "seconds": 0.5}},
                             {"id": "c2", "sourceKey": K2, "in": 0.0, "out": 1.0, "muted": False, "color": {"brightness": -100}}]
                    doc = {"version": 3, "variant": "webtoon", "updatedAt": "", "clips": clips, "audio": [], "fadeOut": 0.5}
                    E._saved_docs = lambda owner, pid, gen_id: {  # noqa: SLF001
                        "real": {"doc": None, "etag": "", "source": "", "version": 0},
                        "webtoon": {"doc": copy.deepcopy(doc), "etag": "e1", "source": "own", "version": 3}}
                    E._spawn_export = lambda owner, pid, gen_id, n, claim: E.run_export(owner, pid, gen_id, n, claim)  # noqa: SLF001
                    res = E.start_export(OWNER, PID, "e1", "webtoon")
                    cur = store.json[E.export_state_key(OWNER, PID, "g1")][0]["current"]
                    plain = E._doc_hash("webtoon", E._clean_clips(copy.deepcopy(clips), {K1, K2}, durations, "t"))  # noqa: SLF001
                    expect("tx-d", [res["started"], cur["status"], cur.get("error"), cur.get("textFx"), cur["docHash"] != plain,
                                    cur["clips"][1].get("color")],
                           [True, "done", "", {"fadeOut": 0.5}, True, {"brightness": -100}], "start_export 사본 textFx · 효과 해시 · done")
                    out = os.path.join(tmp, "export.mp4")
                    Path(out).write_bytes(store.blobs[E.export_key(OWNER, PID, "g1", 1)])
                    fr = _frames(ff, out)
                    expect("tx-d", [len(fr), max(fr[24]) <= 20, max(fr[47]) <= 20, fr[33][1] < 110, fr[10][0] > 150],
                           [48, True, True, True, True], "내보낸 mp4: 48장 · 블랙 경계 · 끝 페이드 · 둘째 클립만 어둡게")
            except Exception as exc:  # noqa: BLE001
                crash("tx-d", exc)

        # ================= (tx-f) 라우트 · 프리미어 버튼 =================
        try:
            from flask import Blueprint, Flask, jsonify  # noqa: PLC0415

            from src.vpoc.errors import envelope, status_for  # noqa: PLC0415

            writes: list = []

            class _Deps:
                require_auth = staticmethod(lambda fn: fn)

            def project_for(pid: str, *, write: bool) -> str:
                writes.append(write)
                if pid != PID:
                    raise VpocError("vnew.plan", "NotFound", "프로젝트를 찾을 수 없습니다.", 404)
                return OWNER

            def fail(exc):
                body = envelope("vnew", exc)
                return jsonify({"error": body}), status_for(body)

            bp = Blueprint("vnew_tx", __name__, url_prefix="/api/vnew")
            T.register_routes(bp, _Deps, project_for, fail)
            app = Flask("tx")
            app.register_blueprint(bp)
            client = app.test_client()
            vset.VNEW_EDIT = True
            srt = client.post(f"/api/vnew/p/{PID}/edit/captions/srt", json={"variant": "real",
                                                                           "captions": [{"id": "a", "start": 1, "end": 2, "text": "하나"}]})
            expect("tx-f", [srt.status_code, srt.get_json()], [200, {"fileName": "captions-real.srt", "count": 1,
                                                                     "text": "1\n00:00:01,000 --> 00:00:02,000\n하나\n"}], "POST /edit/captions/srt")
            expect("tx-f", [client.post("/api/vnew/p/other/edit/captions/auto", json={}).status_code,
                            client.post("/api/vnew/p/other/edit/captions/srt", json={}).status_code,
                            client.post(f"/api/vnew/p/{PID}/edit/captions/srt", json={"captions": "x"}).status_code], [404, 404, 400],
                   "남의 프로젝트 404 · 틀린 자막 400")
            expect("tx-f", writes[:2], [False, True], "SRT = 읽기 권한 · 자동 자막 = 쓰기 권한(⑥ 문서를 만들 수 있다)")
            vset.VNEW_EDIT = False
            expect("tx-f", [client.post(f"/api/vnew/p/{PID}/edit/captions/auto", json={}).status_code,
                            client.post(f"/api/vnew/p/{PID}/edit/captions/srt", json={}).status_code], [404, 404], "VNEW_EDIT 끔 404")
            vset.VNEW_EDIT = saved_flags[0]
            routes_src = (ROOT / "app" / "src" / "vnew" / "routes.py").read_text(encoding="utf-8")
            vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
            block = vjs[vjs.index("/* ==== 작업 215b 시작 ==== */"):vjs.index("/* ==== 작업 215b 끝 ==== */")]
            from src.vnew import edit_xml  # noqa: PLC0415

            expect("tx-f", [routes_src.count(HOOK), 'api(editPath(ctx) + "/premiere", { method: "POST"' in block,
                            'api(editPath(ctx) + "/premiere?variant=" + encodeURIComponent(variant))' in block,
                            "EDIT_TX_PREM_LABEL" in block and "프리미어 프로젝트로 내보내기 (XML + EDL + 소스)" in block,
                            callable(edit_xml.register_routes)], [1, True, True, True, True],
                   "routes.py 한 줄 · 프리미어 버튼 = 작업 215d POST /edit/premiere → GET 조회")
            if HOOK_215D not in routes_src:
                warns.append("(tx-f) 작업 215d edit_xml.register_routes 가 routes.py 에 아직 연결되지 않았습니다 — 버튼은 /edit/premiere 를 부르고, "
                             "연결 전에는 404 안내를 보입니다")
        except Exception as exc:  # noqa: BLE001
            crash("tx-f", exc)

        # ================= (tx-g) 화면(node) =================
        node = shutil.which("node")
        try:
            vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
            block = vjs[vjs.index("/* ==== 작업 215b 시작 ==== */"):vjs.index("/* ==== 작업 215b 끝 ==== */")]
            hooks = ["if (ctx && ctx.inEdit) return editTxLrdAlive(ctx);", "if (ctx.inEdit) return editTxLrdReload(ctx, view);",
                     "if (ctx.inEdit) return editTxLrdPoll(ctx);"]
            expect("tx-g", [re.findall(r"function (lrd\w+|sound\w+)", block), "lrdRowTools(lctx, line, row, lineCell, cell);" in block,
                            [vjs.count(h) for h in hooks], "editFixSwap(ctx, fix, ver" in block, "innerHTML" in block],
                   [[], True, [1, 1, 1], True, False],
                   "⑦ 대사 창 = ⑥ 함수 그대로(복사 없음 · ⑥ 첫 줄 고리 3곳) · 바꾸기 = ⑦ editFixSwap · innerHTML 없음")
            if not node:
                warns.append("(tx-g) node 가 없어 화면 조작 검사를 건너뜁니다")
            else:
                proc = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True, encoding="utf-8", timeout=120)
                expect("tx-g", [proc.returncode, (proc.stderr or "")[-300:]], [0, ""], "node --check vnew.js")
                with tempfile.TemporaryDirectory(prefix="chk215b-ui-") as tmp:
                    path = Path(tmp) / "ui.js"
                    path.write_text(_UI_HARNESS + _js_code(vjs) + _UI_DRIVER, encoding="utf-8", newline="\n")
                    proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
                lines = (proc.stdout or "").strip().splitlines()
                got = json.loads(lines[-1]) if proc.returncode == 0 and lines else {"error": (proc.stderr or "")[-800:]}
                if os.getenv("CHECK_215B_DUMP"):
                    print(json.dumps(got, ensure_ascii=False, indent=1))
                expect("tx-g", got.get("error"), None, "화면 실행")
                if not got.get("error"):
                    expect("tx-g", got["built"], [2, 1, 100, 1, 1, 1], "자막 줄 · V2 줄 · 자막 상자 · [자막 자동 만들기] · [SRT] · [프리미어] 버튼")
                    expect("tx-g", [got["moved"], got["trimmed"], got["undo"], got["redo"]],
                           [[["s1", 1, 2, "안녕"]], [["s1", 1, 3, "안녕"]], [["s1", 1, 2, "안녕"]], [["s1", 1, 3, "안녕"]]],
                           "끌어 옮기기(+0.5초) · 끝 끌기(클립 경계 3초에 붙음) · 실행 취소 · 다시 실행")
                    expect("tx-g", [got["split"], got["deleted"]], [[[1, 2, "안녕"], [2, 3, "안녕"]], [1, 3]],
                           "S = 재생헤드에서 자막 자르기 · Delete = 고른 자막만 지우기(클립 그대로)")
                    expect("tx-g", [got["editor"], got["textEdit"]],
                           [[True, "이 대사도 고치기"], ["새 글", [["GET", None], ["PUT", {"variant": "webtoon", "lineId": "e1", "textKo": "새 글",
                                                                                          "speaker": "민지", "thought": False, "deliveryKo": "작게"}]]]],
                           "글 고치기 → [이 대사도 고치기] 기본 켬 → ⑥ 대사 고침 PUT(같은 말하는 사람 · 목소리 · 말투)")
                    expect("tx-g", [got["dip"], got["cut"]], [['{"type":"dipBlack","seconds":0.5}', True, 9], [True, True]],
                           "전환 메뉴 블랙 0.5초(길이 그대로 9초 · 손잡이 표시) → 컷 · 오른쪽 누르기로 메뉴")
                    expect("tx-g", [got["live"], got["color"], got["reset"]], [["brightness(1.160)", True], [40, 0, 40], [0, 0, 40, 40]],
                           "색 슬라이더: 끄는 동안 CSS 미리보기만 → 놓으면 저장 · 같은 테이크(K1) 모두 · 원래대로 · 실행 취소")
                    expect("tx-g", got["body"], [["audio", "captions", "clips", "etag", "variant"], 1, {"brightness": 40}, False],
                           "PUT 바디: 자막 · 클립 색(기본값 키는 넣지 않는다)")
                    expect("tx-g", [got["premiere"], got["premiere404"]],
                           [[['POST /api/vnew/p/p1/edit/premiere {"variant":"webtoon"}', "GET /api/vnew/p/p1/edit/premiere?variant=webtoon null"],
                             "프리미어 프로젝트: 대기 중…", ["https://x/p.zip", "프리미어 프로젝트 내려받기 (p1-webtoon-1.zip)"]],
                            "서버에 프리미어 내보내기 경로(/edit/premiere)가 아직 연결되지 않았습니다."],
                           "프리미어 버튼: 저장 → POST /edit/premiere(작업 215d) → 진행 → GET → 내려받기 링크 · 라우트 없음(404) 안내")
                    expect("tx-g", [got["lineOpen"], got["lineSaved"]], [[True, "안녕?", "민지", "spoken", "작게", 1, 1, True], ["고친 대사", True, 1]],
                           "⑦ 대사 창 = ⑥ 편집 창(새 대사 · 말하는 사람 · 목소리 · 말투 · AI로 다시 쓰기 · 다시 만들기) · 저장하면 ⑦ 자막도 · 끝난 다시 만들기 → [새 영상으로 바꾸기]")
                    expect("tx-g", got["swap"], [[["c1", "K1v2", 0.5, 2.5, 1.5, True, '{"saturation":-30}', '{"type":"dipWhite","seconds":1}'],
                                                  ["c2", "K2", 0, 3, 1, False, "null", "null"], ["c3", "K1", 3, 6, 1, False, '{"brightness":40}', "null"]],
                                                 "타임라인 클립 1개를 v2로 바꿨습니다. 다른 버전의 [바꾸기]로 되돌릴 수 있습니다."],
                           "새 영상으로 바꾸기: 그 클립만 새 버전 · in/out · 속도 · 소리 끔 · 색 · 전환 그대로(⑦ editFixSwap)")
        except Exception as exc:  # noqa: BLE001
            crash("tx-g", exc)

        # ================= (tx-h) LF · import =================
        try:
            files = NEW_FILES + [ROOT / "app" / "src" / "vnew" / n for n in CHANGED] + [PUBLIC / "vnew.js", PUBLIC / "vnew.css"]
            expect("tx-h", [p.name for p in files if b"\r\n" in p.read_bytes()], [], "새 · 고친 파일 LF 줄 끝")
            vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
            css = (PUBLIC / "vnew.css").read_text(encoding="utf-8")
            expect("tx-h", [vjs.count("/* ==== 작업 215b 시작 ==== */"), vjs.count("/* ==== 작업 215b 끝 ==== */"),
                            css.count("/* ==== 작업 215b 시작 ==== */"), css.count("/* ==== 작업 215b 끝 ==== "), css.endswith("}\n")],
                   [1, 1, 1, 1, True], "vnew.js · vnew.css 구역 표시 한 번씩")
            import src.vnew.routes as routes_mod  # noqa: PLC0415

            expect("tx-h", callable(routes_mod.create_blueprint), True, "src.vnew.routes import")
        except Exception as exc:  # noqa: BLE001
            crash("tx-h", exc)
    finally:
        for name, value in saved.items():
            setattr(E, name, value)
        vset.VNEW_EDIT = saved_flags[0]
        if saved_flags[1] is not None:
            vset.VNEW_SOUND = saved_flags[1]
        if saved_burn is not None:
            T._cache["burn"] = saved_burn  # noqa: SLF001

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 215b ({group}) {item}"[:1800])
        elif group not in seen:
            print(f"[WARN] 작업 215b ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 215b ({group}) {title}")
    for item in warns:
        print(f"[WARN] 작업 215b {item}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_edit_text_215b() else 1)
