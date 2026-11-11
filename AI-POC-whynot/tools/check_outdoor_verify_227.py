"""작업 227: 실외 장소(design.enclosed false) verify · 다시 그리기가 열린 바깥을 방으로 되돌리지 않는다.

네트워크 0 · LLM 0 · 유료 0(locations._verify · _seedream · _seedream_edit · STORE · 원장을 가짜로 바꾼다).

  (ov-a) 실외 고정물: verify system · user · tool 글에 상자 · 방 · 벽 · 천장 낱말 없음(OUTDOOR_OPEN_AIR 부정 문장만),
         open-air · sky · MAIN BACKGROUND · 물건(bollard · apartment) 있음. 실내 verify 글은 227 전과 바이트 그대로(sha256)
  (ov-b) 낮 실외 + fixesEn 이 방 · 가구 · 밤뿐 → 전부 버림 → 다시 그리기 0 · 원장 0 · 첫 그림 유지 · 로그 1줄.
         섞인 fixesEn(볼라드 + 천장) → 볼라드 문장만 붙는다
  (ov-c) 웹툰 back 을 다시 그리면 실사 back 은 새 웹툰 바이트로 edit 1회(원장 1건) 다시 만들고 1회 검사(더 안 그린다).
         다시 안 그린 쪽 실사는 첫 웹툰 바이트(지금과 같다)
  (ov-d) 실내 고정물 _generate_views + _verify_views 호출 순서가 227 전과 같다(sha256, REAL_FROM_WEBTOON 켬 · 끔)
  (ov-e) 변이 "실외도 _VERIFY_SYSTEM" 을 (ov-a) 가, 변이 "실사 다시 그리기가 첫 웹툰 바탕" 을 (ov-c) 가 잡는다
  (ov-f) 바꾼 파일 LF 줄 끝
실행: python -X utf8 -B tools/check_outdoor_verify_227.py
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from check_outdoor_location_226 import DAY_LIGHT, NIGHT_LIGHT, _loc  # noqa: E402

HOOK = ('    if not __import__("check_outdoor_verify_227").check_outdoor_verify_227(): '
        'fail("작업 227: 실외 장소 verify · 다시 그리기 검사 실패")')
GROUPS = {
    "ov-a": "실외 verify 글에 방/상자/벽/천장 낱말 없음 · open-air 물건 · 실내 verify 글 그대로(sha256)",
    "ov-b": "실외 fixesEn 방 · 가구 · 밤 문장 버림 → 남는 것 없으면 다시 그리기 0 · 원장 0 · 섞이면 실외 문장만",
    "ov-c": "웹툰 다시 그림 → 실사는 새 웹툰 바이트로 edit 1회 · 원장 1건 · 다시 안 그린 쪽은 첫 웹툰 바탕",
    "ov-d": "실내 _generate_views + _verify_views 호출 순서 227 전과 같음(sha256 켬 · 끔)",
    "ov-e": "변이(실외도 _VERIFY_SYSTEM · 실사 다시 그리기 첫 웹툰 바탕)를 (ov-a) · (ov-c) 가 잡는다",
    "ov-f": "바꾼 파일 LF",
}
CHANGED = ["app/src/vnew/locations.py", "tools/check_outdoor_verify_227.py"]

# 227 전 코드(2026-11-12)로 잰 값(--baseline).
INDOOR_VERIFY_SHA256 = "e626083cbf811200381426fbb79a1dbc787f04dfafe64ea44f737fba02fbee7b"
INDOOR_FLOW_SHA256 = {"on": "78201657db2995c0819022e2890d61236e734e62466de6de1f51051c743eac38",
                      "off": "0649b5103ee02317a803c520bee1b8ac440568cd523e2594efafd2105b40452f"}

# 구조물 낱말. 꼭 넣는 부정 문장 OUTDOOR_OPEN_AIR("no walls, no ceiling, no room, no box")는 빼고 센다.
FORBIDDEN_RE = re.compile(r"\b(?:rooms?|box(?:es)?|box-set|ceilings?|indoors?|interiors?|walls?|cutaway)\b", re.IGNORECASE)
BAD_FIXES = ["Show the room's back wall with a window", "add a desk and chair", "make it night"]
BOLLARD_FIX = "add the yellow bands on the bollard legs"
MIXED_FIXES = [BOLLARD_FIX, "add a ceiling"]


def _modules():
    from src.vnew import locations as lm  # noqa: PLC0415
    return lm


def _h(data) -> str:
    raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FakeStore:
    def __init__(self, run: "_Run"):
        self.run = run

    def put_bytes(self, key, data, content_type=None):
        who = self.run.key_of.get(key, key)
        self.run.owner_of[bytes(data)] = who
        self.run.calls.append(("put", who, _h(data)))


class _Run:
    """가짜 그림 · 검사 · 원장. LOCATION_WORKERS=1 이라 호출 순서가 결정적이다."""

    def __init__(self, lm, loc: dict, verify_fn, *, real_from_webtoon: bool = True):
        self.lm, self.loc, self.verify_fn, self.flag = lm, loc, verify_fn, real_from_webtoon
        self.calls: list[tuple] = []
        self.prompts: list[dict] = []
        self.verifies: list[dict] = []
        self.owner_of: dict[bytes, str] = {}
        self.n = 0
        lid = loc["id"]
        self.key_of = {lm.view_key("o", "p", lid, v, w, 1): f"{v}/{w}" for v in lm.VARIANTS for w in lm.VIEWS}

    def seedream(self, prompt, references, *, size):
        self.n += 1
        image = f"T2I#{self.n}|{_h(prompt)}".encode()
        self.calls.append(("t2i", _h(prompt), len(references or []), size))
        self.prompts.append({"kind": "t2i", "prompt": prompt, "base": None, "out": image})
        return image

    def seedream_edit(self, prompt, base, *, size):
        self.n += 1
        image = f"EDIT#{self.n}|{_h(prompt)}|{_h(base)}".encode()
        self.calls.append(("edit", _h(prompt), self.owner_of.get(bytes(base), "?"), _h(base), size))
        self.prompts.append({"kind": "edit", "prompt": prompt, "base": bytes(base), "out": image})
        return image

    def verify(self, blocks, *, model, **kw):
        lm = self.lm
        text = blocks[0]["text"]
        image = bytes(blocks[-1]["image"]["source"]["bytes"])
        who = self.owner_of.get(image, "?")
        system = kw.get("system_text", lm._VERIFY_SYSTEM)  # noqa: SLF001
        tool = kw.get("tool", lm._VERIFY_TOOL)  # noqa: SLF001
        self.calls.append(("verify", who, _h(system), _h(json.dumps(tool, sort_keys=True)), _h(text), _h(image)))
        self.verifies.append({"who": who, "system": system, "tool": tool, "text": text, "image": image})
        return dict(self.verify_fn(who)), {"model": model, "inputTokens": 1, "outputTokens": 1}

    def charge(self, owner, pid, lid, kind, *, model=None):
        self.calls.append(("charge", kind, model))

    @contextmanager
    def patched(self):
        lm = self.lm
        names = ("STORE", "_seedream", "_seedream_edit", "_verify", "_charge_seedream", "_charge_text",
                 "_bump_progress", "_bump_total", "_real_from_webtoon", "LOCATION_WORKERS")
        saved = {n: getattr(lm, n) for n in names}
        lm.STORE = _FakeStore(self)
        lm._seedream = self.seedream  # noqa: SLF001
        lm._seedream_edit = self.seedream_edit  # noqa: SLF001
        lm._verify = self.verify  # noqa: SLF001
        lm._charge_seedream = self.charge  # noqa: SLF001
        lm._charge_text = lambda *a, **k: None  # noqa: SLF001
        lm._bump_progress = lambda *a, **k: self.calls.append(("done",))  # noqa: SLF001
        lm._bump_total = lambda *a, **k: self.calls.append(("total",))  # noqa: SLF001
        lm._real_from_webtoon = lambda: self.flag  # noqa: SLF001
        lm.LOCATION_WORKERS = 1
        try:
            yield self
        finally:
            for n, v in saved.items():
                setattr(lm, n, v)

    def flow(self):
        """_generate_views + _verify_views. 반환: (첫 뷰 사본, 최종 뷰, checks)."""
        lm, loc = self.lm, self.loc
        geometries = lm._build_geometries(loc)  # noqa: SLF001
        guides = {view: f"GUIDE-{view}".encode() for view in lm.VIEWS}
        with self.patched():
            views = lm._generate_views("o", "p", loc["id"], 1, loc, guides, [b"REF1"], "t")  # noqa: SLF001
            first = dict(views)
            checks = lm._verify_views("o", "p", loc["id"], 1, loc, guides, [b"REF1"], views, geometries, "t")  # noqa: SLF001
        return first, views, checks


@contextmanager
def _capture_log():
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("vpoc")
    handler, level, prop = _H(), logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = prop


def _ok_reply(_who):
    return {"matchesPlace": True, "matchesSides": True}


def _verify_texts(lm, loc: dict) -> list[dict]:
    """(variant, view) 마다 _verify_view 가 보내는 system · tool · user 글."""
    run = _Run(lm, loc, _ok_reply)
    with run.patched():
        for variant in lm.VARIANTS:
            for view in lm.VIEWS:
                image = f"IMG-{variant}-{view}".encode()
                run.owner_of[image] = f"{variant}/{view}"
                lm._verify_view("o", "p", loc["id"], copy.deepcopy(loc), variant, view, image,  # noqa: SLF001
                                f"GUIDE-{view}".encode(), [b"REF1"])
    return run.verifies


def _indoor_verify_blob(lm) -> str:
    parts: list[str] = []
    for loc in (_loc(True), _loc(False, drop_enclosed=True)):
        for item in _verify_texts(lm, loc):
            parts += [item["who"], item["system"], json.dumps(item["tool"], sort_keys=True), item["text"]]
    return "\n".join(parts)


def _indoor_reply(who):
    return {
        "webtoon/back": {"matchesPlace": False, "fixesEn": ["Add a window to the back wall"]},
        "real/back": {"matchesPlace": False, "fixesEn": ["make the desk wooden"]},
        "real/front": {"matchesPlace": True, "matchesSides": False, "fixesEn": []},
    }.get(who, {"matchesPlace": True, "matchesSides": True})


def _indoor_flow(lm, flag: bool) -> tuple[str, _Run, dict]:
    run = _Run(lm, _loc(True), _indoor_reply, real_from_webtoon=flag)
    first, views, checks = run.flow()
    blob = "\n".join(repr(c) for c in run.calls) + "\n" + json.dumps(checks, sort_keys=True)
    return blob, run, first


def _forbidden(lm, text: str) -> list[str]:
    body = text.replace(lm.OUTDOOR_OPEN_AIR, " ")
    return sorted({m.group(0).lower() for m in FORBIDDEN_RE.finditer(body)})


def group_a() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    for item in _verify_texts(lm, _loc(False)):
        who = item["who"]
        for name, text in (("system", item["system"]), ("tool", json.dumps(item["tool"])), ("user", item["text"])):
            words = _forbidden(lm, text)
            if words:
                bad.append(f"{who}/{name}: 구조물 낱말 {words}")
        if "open-air" not in item["system"].lower() or "MAIN BACKGROUND" not in item["system"]:
            bad.append(f"{who}/system: open-air · MAIN BACKGROUND 없음")
        missing = [w for w in ("bollard", "apartment", "open-air", "sky", "MAIN BACKGROUND") if w.lower() not in item["text"].lower()]
        if missing:
            bad.append(f"{who}/user: 없음 {missing}")
    got = _sha(_indoor_verify_blob(lm))
    if got != INDOOR_VERIFY_SHA256:
        bad.append(f"실내 verify 글 sha256 {got} != {INDOOR_VERIFY_SHA256}")
    return bad


def group_b() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    day = _loc(False)
    if lm._outdoor_fixes(BAD_FIXES, day):  # noqa: SLF001
        bad.append(f"방 · 가구 · 밤 fixes 가 남았다: {lm._outdoor_fixes(BAD_FIXES, day)}")  # noqa: SLF001
    if lm._outdoor_fixes(MIXED_FIXES, day) != [BOLLARD_FIX]:  # noqa: SLF001
        bad.append(f"섞인 fixes → {lm._outdoor_fixes(MIXED_FIXES, day)}")  # noqa: SLF001
    night = _loc(False, light=NIGHT_LIGHT)
    if lm._outdoor_fixes(["make the street lamps glow at night"], night) != ["make the street lamps glow at night"]:  # noqa: SLF001
        bad.append("진짜 밤 실외에서 night 문장을 버렸다")
    # 흐름: 4장 모두 불일치 + 버릴 문장뿐 → 다시 그리기 0.
    run = _Run(lm, day, lambda who: {"matchesPlace": False, "matchesSides": False, "fixesEn": list(BAD_FIXES)})
    with _capture_log() as records:
        first, views, checks = run.flow()
    draws = [c for c in run.calls if c[0] in ("t2i", "edit")]
    charges = [c for c in run.calls if c[0] == "charge"]
    if len(draws) != 4 or len(charges) != 4:
        bad.append(f"버릴 문장뿐인데 그림 {len(draws)}장 · 원장 {len(charges)}건(4 · 4 여야)")
    if views != first:
        bad.append("첫 그림이 바뀌었다")
    if any(checks[v][w]["fixedEn"] for v in lm.VARIANTS for w in lm.VIEWS):
        bad.append("fixedEn 이 참")
    skipped = [r for r in records if "redraw.skipped" in r]
    if len(skipped) != 4:
        bad.append(f"건너뜀 로그 {len(skipped)}줄(4 여야)")
    # 흐름: 섞인 문장 → 볼라드 문장만 붙는다.
    run = _Run(lm, day, lambda who: {"matchesPlace": False, "fixesEn": list(MIXED_FIXES)} if who == "webtoon/back"
               else {"matchesPlace": True, "matchesSides": True})
    run.flow()
    fixes = [p["prompt"] for p in run.prompts if "Corrections:" in p["prompt"]]
    if len(fixes) != 1 or not fixes[0].endswith(" Corrections: " + BOLLARD_FIX):
        bad.append(f"섞인 문장 다시 그리기 {len(fixes)}장: {[f[-90:] for f in fixes]}")
    return bad


def _outdoor_c_reply(who):
    return {
        "webtoon/back": {"matchesPlace": False, "fixesEn": list(MIXED_FIXES)},
        "real/back": {"matchesPlace": False, "fixesEn": [BOLLARD_FIX]},
        "real/front": {"matchesPlace": False, "fixesEn": ["add the green street lamp on the left side"]},
    }.get(who, {"matchesPlace": True, "matchesSides": True})


def group_c() -> list[str]:
    lm = _modules()
    bad: list[str] = []
    run = _Run(lm, _loc(False), _outdoor_c_reply)
    first, views, checks = run.flow()
    new_w_back = views[("webtoon", "back")][0]
    if new_w_back == first[("webtoon", "back")][0]:
        return ["웹툰 back 이 다시 그려지지 않았다(고정물)"]
    regen = [p for p in run.prompts if p["kind"] == "edit" and p["base"] == new_w_back]
    if len(regen) != 1:
        bad.append(f"새 웹툰 back 바탕 edit {len(regen)}회(1 이어야)")
    stale = [p for p in run.prompts if p["kind"] == "edit" and p["base"] == first[("webtoon", "back")][0]]
    if len(stale) != 1:  # 첫 생성 1회만 첫 웹툰 바탕
        bad.append(f"첫 웹툰 back 바탕 edit {len(stale)}회(첫 생성 1회여야)")
    if regen and views[("real", "back")][0] != regen[0]["out"]:
        bad.append("최종 실사 back 이 새 웹툰 바탕 edit 결과가 아니다")
    back_charges = [c for c in run.calls if c[0] == "charge" and c[1].startswith("real-view-back")]
    if [c[1] for c in back_charges] != ["real-view-back", "real-view-back-fix"]:
        bad.append(f"실사 back 원장 {back_charges}(첫 1 + 교체 1 이어야)")
    if back_charges and back_charges[-1][2] != lm.LOCATION_EDIT_MODEL:
        bad.append(f"교체 원장 모델 {back_charges[-1][2]}")
    rb = [v for v in run.verifies if v["who"] == "real/back"]
    if len(rb) != 1 or (regen and rb[0]["image"] != regen[0]["out"]):
        bad.append(f"실사 back 검사 {len(rb)}회 · 새 그림 검사 아님")
    if not checks["real"]["back"]["fixedEn"]:
        bad.append("실사 back fixedEn 거짓")
    rf = [p for p in run.prompts if p["kind"] == "edit" and "Corrections:" in p["prompt"]]
    if len(rf) != 1 or rf[0]["base"] != first[("webtoon", "front")][0]:
        bad.append(f"실사 front 다시 그리기 바탕이 첫(=최종) 웹툰 front 가 아님({len(rf)}회)")
    if views[("webtoon", "front")] != first[("webtoon", "front")]:
        bad.append("웹툰 front 가 바뀌었다")
    order = [c[1] for c in run.calls if c[0] == "verify"]
    if order[:2] != ["webtoon/back", "webtoon/front"] or sorted(order[2:]) != ["real/back", "real/front"]:
        bad.append(f"검사 순서 {order}(웹툰 먼저)")
    return bad


def group_d(warns: list[str]) -> list[str]:
    lm = _modules()
    bad: list[str] = []
    for flag, name in ((True, "on"), (False, "off")):
        blob, run, first = _indoor_flow(lm, flag)
        got = _sha(blob)
        if got != INDOOR_FLOW_SHA256[name]:
            bad.append(f"실내 흐름({name}) sha256 {got} != {INDOOR_FLOW_SHA256[name]}")
        if flag:
            fixes = [p for p in run.prompts if p["kind"] == "edit" and "Corrections:" in p["prompt"]]
            if fixes and fixes[0]["base"] == first[("webtoon", "back")][0]:
                warns.append("실내도 실사 다시 그리기가 첫 웹툰 바탕(다시 그린 웹툰이 아님) — 227 은 실내를 바꾸지 않는다")
    return bad


def group_e(caught: list[str]) -> list[str]:
    lm = _modules()
    bad: list[str] = []
    saved = lm._VERIFY_SYSTEM_OUTDOOR  # noqa: SLF001
    lm._VERIFY_SYSTEM_OUTDOOR = lm._VERIFY_SYSTEM  # noqa: SLF001 — 변이: 실외도 실내 verify 글
    try:
        got = group_a()
    finally:
        lm._VERIFY_SYSTEM_OUTDOOR = saved  # noqa: SLF001
    (caught.append("indoor-verify→ov-a") if got else bad.append("변이(실외도 _VERIFY_SYSTEM)를 (ov-a) 가 잡지 못했습니다"))

    first_pass: dict[str, bytes] = {}
    orig_gen, orig_redraw = lm._generate_views, lm._redraw_view  # noqa: SLF001

    def gen(*a, **k):
        out = orig_gen(*a, **k)
        first_pass.update({view: out[("webtoon", view)][0] for view in lm.VIEWS})
        return out

    def redraw(*a, base=None, **k):  # 변이: 실사 다시 그리기 · 교체가 첫 웹툰 바탕
        if base is not None and a[5] == "real":
            base = first_pass.get(a[6], base)
        return orig_redraw(*a, base=base, **k)

    lm._generate_views, lm._redraw_view = gen, redraw  # noqa: SLF001
    try:
        got = group_c()
    finally:
        lm._generate_views, lm._redraw_view = orig_gen, orig_redraw  # noqa: SLF001
    (caught.append("stale-base→ov-c") if got else bad.append("변이(실사 다시 그리기 첫 웹툰 바탕)를 (ov-c) 가 잡지 못했습니다"))
    return bad


def check_outdoor_verify_227() -> bool:
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    warns: list[str] = []
    for group, fn in (("ov-a", group_a), ("ov-b", group_b), ("ov-c", group_c), ("ov-d", lambda: group_d(warns)),
                      ("ov-e", lambda: group_e(caught))):
        try:
            results[group] = fn()
        except Exception:  # noqa: BLE001
            results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    results["ov-f"] = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 227 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "ov-e" else ""
            print(f"[ok  ] 작업 227 ({group}) {title}{extra}")
    for warn in warns:
        print(f"[WARN] 작업 227 (ov-d) {warn}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    _lm = _modules()
    print("INDOOR_VERIFY_SHA256", _sha(_indoor_verify_blob(_lm)))
    for _flag, _name in ((True, "on"), (False, "off")):
        _blob, _run, _first = _indoor_flow(_lm, _flag)
        print(f"INDOOR_FLOW_SHA256[{_name}]", _sha(_blob), f"calls={len(_run.calls)}")
    sys.exit(0)

if __name__ == "__main__":
    _ok = check_outdoor_verify_227()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
