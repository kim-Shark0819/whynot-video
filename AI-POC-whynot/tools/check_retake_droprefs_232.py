"""작업 232 — ⑦ AI로 고치기: 콘텐츠 검사 순화 다시 내기(195)도 거절이면 원작 웹툰 패널 그림을 빼고 1번 더.

(dr-a) 애니 다시 만들기 → contentFlag → 순화 다시 contentFlag → 셋째 제출: 패널 그림 0 · @Image 번호가 보낸 그림과 맞음 ·
       인물 시트 · 장소 · @Video1 그대로 · 순화한 프롬프트 그대로(참고 줄만 뺌) → 완성, 버전 contentDropRefs. 패널이 장소 앞(옛 꼴)이면
       장소 번호를 당긴다. 제출 자체 거절(원장 0) 두 번 뒤에도 셋째는 패널 없이.
(dr-b) 셋째도 거절 → CONTENT_FLAG_TEXT + " 원작 패널 없이도 거절됐습니다." · 제출 정확히 3번 · 원장 = 받아 준 제출 수 · 견적(usd) 그대로
(dr-c) 실사 다시 만들기 → 지금 2번 동작(고치기 전 호출 순서 sha)
(dr-d) 패널 참고가 없는 애니 다시 만들기 → 지금 동작(고치기 전 sha)
(dr-e) 셋째 시도 중 보기 contentNote = 안내 문구, 끝나면 "" · 실패 문구 · 화면(vnew.js)이 contentNote · error 를 그대로 쓴다
(dr-f) 돌연변이 "첫 다시 내기에서 패널을 뺀다" → dr-a 검사가 잡는다
(dr-g) 고친 파일 LF

가짜만 쓴다(가짜 fal 제출 · 조회, 메모리 retakes 상태, 가짜 plan · payload). 네트워크 · AWS · 유료 API 없음.
실행: python -X utf8 -B tools/check_retake_droprefs_232.py   (고치기 전 sha: --baseline)
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
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
    "dr-a": "애니: 순화도 거절 → 패널 없이 셋째 제출(번호 · 시트 · 장소 · @Video1) → 완성 · contentDropRefs",
    "dr-b": "셋째도 거절 → 고정 문구 + 한 문장 · 제출 3번 · 원장 = 받아 준 제출",
    "dr-c": "실사 다시 만들기 = 지금 2번 동작(sha)",
    "dr-d": "패널 참고 없는 애니 = 지금 동작(sha)",
    "dr-e": "보기 안내 문구 · 실패 문구 · 화면 글",
    "dr-f": "돌연변이(첫 다시 내기에서 패널 빼기) → 잡힘",
    "dr-g": "LF",
}
OWNER, PID, GEN = "tester", "prj_dr232", "gen1"
MODEL = "alibaba/wan-3.0/reference-to-video"
FLAG_MSG = "The content could not be processed because it contained material flagged by a content checker."
NOTE_TEXT = "콘텐츠 검사에서 다시 거절돼, 원작 패널 그림을 빼고 한 번 더 시도하는 중입니다."
FAIL_SENTENCE = " 원작 패널 없이도 거절됐습니다."
# 고치기 전(작업 232 전) 코드에서 --baseline 으로 뽑은 sha. 바꾸면 안 된다.
# 233: 두 고정 글의 "just before the kiss" 가 195 순화 다시 내기에서 "faces close together" 로 바뀐다(233 을 끄면 옛 sha
# a66bd96a5bc13e87 / ed47f5a953200984 그대로 — 바뀐 것은 둘째 제출 프롬프트 글뿐).
OLD_SHA = {
    "dr-c": "f764175aaff7ac1b",  # 233
    "dr-d": "613d51950112446b",  # 233
}
CHANGED = ("app/src/vnew/retake.py", "app/src/vnew/generate.py", "tools/check_retake_droprefs_232.py")

# 그림(바이트 → 이름). 신원 시트 · 장소 · 원작 패널.
IMG = {name: name.encode() for name in ("SHEET1", "SHEET2", "LOC", "PANEL14", "PANEL15", "PANEL16", "RSHEET1", "RLOC")}
NAME_OF = {v: k for k, v in IMG.items()}

# 새 꼴(vibe): 시트 → 장소 → 패널(맨 끝).
VIBE_PROMPT = "\n".join([
    "@Image1 is Mina's character sheet; @Image2 is Jun's character sheet; @Image3 is the location (background reference); "
    "@Image4\u2013@Image6 are the uploaded webtoon images (art style reference only).",
    "STYLE: Korean webtoon art style exactly as in the uploaded webtoon images.",
    "@Video1 is the motion reference: keep its blocking, timing and camera.",
    "0-5s: MCU: Mina leans toward Jun, just before the kiss, their mouths close; Mina: \"\uac00\uc9c0 \ub9c8.\"",
    "DIALOGUE: Mina: \"\uac00\uc9c0 \ub9c8.\"",
])
VIBE_IMAGES = ["SHEET1", "SHEET2", "LOC", "PANEL14", "PANEL15", "PANEL16"]
# 옛 꼴(_take_prompt): 정면 → 패널 → 장소. 패널을 빼면 장소 번호가 당겨진다.
OLD_PROMPT = "\n".join([
    "STYLE: Modern TV-anime adaptation of the original webtoon @Image2\u2013@Image3: keep the webtoon's faces and line art.",
    "ASSETS: @Image1 is Mina (front view); @Image2\u2013@Image3 are the uploaded webtoon images (art style reference only); "
    "@Image4 is the location.",
    "@Video1 is the motion reference.",
    "0-5s: CU: Mina catches her breath, lips apart, after the kiss.",
])
OLD_IMAGES = ["SHEET1", "PANEL15", "PANEL16", "LOC"]
NOPANEL_PROMPT = "\n".join([
    "@Image1 is Mina's character sheet; @Image2 is the location (background reference).",
    "@Video1 is the motion reference.",
    "0-5s: MCU: Mina leans toward Jun, just before the kiss.",
    "DIALOGUE: Mina: \"\uac00\uc9c0 \ub9c8.\"",
])
NOPANEL_IMAGES = ["SHEET1", "LOC"]
REAL_PROMPT = "\n".join([
    "@Image1 is Mina (photo reference); @Image2 is the location.",
    "@Video1 is the motion reference.",
    "0-5s: MCU: Mina leans toward Jun, just before the kiss.",
    "DIALOGUE: Mina: \"Don't go.\"",
])
REAL_IMAGES = ["RSHEET1", "RLOC"]


class _Patch:
    def __init__(self) -> None:
        self.saved: list = []

    def set(self, obj, name: str, value) -> None:
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        while self.saved:
            obj, name, value = self.saved.pop()
            setattr(obj, name, value)


def _modules():
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    return gm, rt, VpocError


def run_case(*, variant: str, prompt: str, images: list[str], panel_nos: list[int], polls: list[str],
             submits: list[str] | None = None, mutant: str = "") -> dict:
    """가짜 하네스에서 run_retake 1번. polls = 받아 준 제출마다 조회 결과("flag"|"done"), submits = 제출마다("ok"|"reject")."""
    gm, rt, VpocError = _modules()
    patch = _Patch()
    rec: dict = {"submits": [], "polls": [], "ledger": [], "notes": [], "results": [], "logs": [], "views": []}
    key = f"{variant}:take1"
    claim = "claim-dr232"
    version0 = {"n": 2, "status": "queued", "attempt": 1, "claim": claim, "format": "template", "path": "take", "model": MODEL,
                "scope": "full", "prompt": prompt, "promptBytes": len(prompt.encode()), "formatRefs": sorted(rt._refs(prompt)),  # noqa: SLF001
                "usd": 0.5, "maxBytes": 4000, "requestKo": "\ud654\ud48d \ubc14\uafb8\uae30", "fromN": 1,
                "createdAt": "2026-11-12T00:00:00Z"}
    box = {"state": {"v": 1, "cells": {key: {"versions": [version0], "draft": None}}}}
    poll_plan = list(polls)
    submit_plan = list(submits or [])

    def mutate_state(_o, _p, _g, fn):
        res = fn(rt._normalized(copy.deepcopy(box["state"])))  # noqa: SLF001
        if res is not None:
            box["state"] = copy.deepcopy(res)
        return rt._normalized(copy.deepcopy(box["state"]))  # noqa: SLF001

    def view_now() -> dict:
        found = rt._find_version(box["state"], key, 2) or {}  # noqa: SLF001
        return rt._version_out({"baseLabel": "c15"}, found)  # noqa: SLF001

    def fake_payload(_o, _p, _plan, _like, *, path, caps, variant):  # noqa: ARG001
        return {"imageUrls": [IMG[n] for n in images], "elements": [], "prompt": prompt, "panelNos": list(panel_nos),
                "locationNo": None, "cast": []}

    def fake_submit_take(*, endpoint, caps, video_mp4, prompt, output_prefix, image_urls, seconds, resolution, audio):  # noqa: ARG001
        i = len(rec["submits"])
        rec["submits"].append({"endpoint": endpoint, "prompt": prompt, "images": [NAME_OF.get(bytes(b), "?") for b in image_urls],
                               "video": video_mp4.decode(), "prefix": output_prefix, "seconds": seconds})
        rec["views"].append(view_now())
        if (submit_plan[i] if i < len(submit_plan) else "ok") == "reject":
            raise VpocError("fal.submit", "HTTP422", "fal HTTP422: " + FLAG_MSG, 422)
        return {"invocationArn": f"arn-{i + 1}", "outputPrefix": output_prefix, "provider": "fal", "clientRequestToken": f"tok-{i + 1}"}

    def fake_poll(arn, prefix):  # noqa: ARG001
        rec["polls"].append(arn)
        outcome = poll_plan.pop(0) if poll_plan else "done"
        if outcome == "flag":
            return {"status": "Failed", "failureMessage": FLAG_MSG}
        return {"status": "Completed"}

    def fake_charge(_o, _p, _g, result, model, seconds, usd, price_unknown=False):
        rec["ledger"].append([str(result.get("clientRequestToken") or ""), model, seconds, usd, price_unknown])

    def fake_log_attempt(_o, _p, _g, **kw):
        rec["logs"].append({"attempt": kw.get("attempt"), "refs": len(kw.get("refs") or []), "version": kw.get("version")})
        return f"log-{len(rec['logs'])}"

    cell = {"seconds": 5.0, "id": "take1"}
    try:
        patch.set(rt, "_mutate_state", mutate_state)
        patch.set(rt, "read_state", lambda _o, _p, _g: rt._normalized(copy.deepcopy(box["state"])))  # noqa: SLF001
        patch.set(rt, "_note_version", lambda _o, _p, _g, _k, v, m, *, kind="", action="": rec["notes"].append([kind, action]))
        patch.set(rt, "log_result", lambda _o, _p, _g, _k, arn, status, **kw: rec["results"].append(
            [arn, status, str(kw.get("error") or ""), str(kw.get("output_key") or "")]))
        patch.set(rt, "_poll", fake_poll)
        patch.set(rt, "_sleep", lambda _s: None)
        patch.set(gm.plan_mod, "load_plan", lambda _o, _p: ({"gen": {"genId": GEN, "videoModel": MODEL}}, "e1"))
        patch.set(gm, "_retake_kcut_cell", lambda *a, **k: None)
        patch.set(gm, "_retake_cell", lambda *a, **k: (0, cell, "previz-take"))
        patch.set(gm, "retake_path", lambda *a, **k: "take")
        patch.set(gm, "retake_plan", lambda *a, **k: {"ok": True, "sentSeconds": 5, "resolutionValue": "480p", "trimTo": None,
                                                      "billedSeconds": 5, "usd": 0.5})
        patch.set(gm, "_previz_caps", lambda _m: {"imageListMax": 9})
        patch.set(gm, "_retake_like", lambda *a, **k: {"id": "take1"})
        patch.set(gm, "_seg_output_prefix", lambda _o, _p, _g, tag, attempt: f"out/{tag}/a{attempt}")
        patch.set(gm, "_retake_video", lambda *a, **k: (b"VIDEO1", {"sentSeconds": 5}, ["rec/take1.mp4"]))
        patch.set(gm, "_retake_payload", fake_payload)
        patch.set(gm, "_submit_take", fake_submit_take)
        patch.set(gm, "_take_params", lambda *a, **k: {})
        patch.set(gm, "_retake_charge_usd", lambda info: (float(info["usd"]), False))
        patch.set(gm, "_charge_retake", fake_charge)
        patch.set(gm, "_log_attempt", fake_log_attempt)
        patch.set(gm, "_find_output_key", lambda prefix, arn: f"{prefix}/out.mp4")
        if mutant == "drop-first":  # 돌연변이: 첫 거절에서 이미 순화한 척(패널 빼기가 첫 다시 내기에 온다)
            real_flag = rt._content_flag  # noqa: SLF001

            def flag_mutant(o, p, g, k, n, c, version, message=""):
                if not version.get("contentRetry"):
                    version = {**version, "contentRetry": {"fromAttempt": 0}}
                return real_flag(o, p, g, k, n, c, version, message=message)

            patch.set(rt, "_content_flag", flag_mutant)
        logging.disable(logging.CRITICAL)
        rt.run_retake(OWNER, PID, GEN, key, 2, claim)
    finally:
        logging.disable(logging.NOTSET)
        patch.undo()
    final = dict(rt._find_version(box["state"], key, 2) or {})  # noqa: SLF001
    rec["final"] = final
    rec["finalView"] = rt._version_out({"baseLabel": "c15"}, final)  # noqa: SLF001
    return rec


def call_sha(rec: dict) -> str:
    """호출 순서(제출 · 조회 · 원장 · 노트 · 기록)와 끝 버전(시각 뺌)의 sha."""
    final = rec["final"]
    retry = {k: v for k, v in (final.get("contentRetry") or {}).items() if k != "at"}
    body = {"submits": rec["submits"], "polls": rec["polls"], "ledger": rec["ledger"], "notes": rec["notes"],
            "results": rec["results"], "logs": rec["logs"],
            "final": {k: final.get(k) for k in ("status", "attempt", "error", "failureKind", "prompt", "usd", "outputKey")},
            "retry": retry, "keys": sorted(k for k in final if k not in ("heartbeatAt", "finishedAt", "submittedAt")),
            "views": [{k: v for k, v in view.items() if k not in ("finishedAt", "submittedAt")} for view in rec["views"]]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def case_c() -> dict:
    return run_case(variant="real", prompt=REAL_PROMPT, images=REAL_IMAGES, panel_nos=[], polls=["flag", "flag"])


def case_d() -> dict:
    return run_case(variant="webtoon", prompt=NOPANEL_PROMPT, images=NOPANEL_IMAGES, panel_nos=[], polls=["flag", "flag"])


_TAG_RE = re.compile(r"@Image(\d+)(?:\u2013@Image(\d+))?")


def _image_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for m in _TAG_RE.finditer(text):
        a = int(m.group(1))
        b = int(m.group(2) or a)
        out.update(range(a, b + 1))
    return out


def _location_no(text: str) -> int:
    m = re.search(r"@Image(\d+) is the location", text)
    return int(m.group(1)) if m else 0


def problems_a(rec: dict, *, panels: list[str], kept: list[str]) -> list[str]:
    """dr-a 기대(패널 그림이 든 애니 · 순화도 거절 → 셋째 = 패널 없이 → 완성)를 어긴 것들."""
    bad: list[str] = []
    subs = rec["submits"]
    if len(subs) != 3:
        return [f"제출 {len(subs)}번(기대 3)"]
    s1, s2, s3 = subs
    if not all(p in s2["images"] for p in panels):
        bad.append(f"둘째(순화) 제출에 패널이 없다: {s2['images']}")
    if s2["prompt"] == s1["prompt"]:
        bad.append("둘째 제출 프롬프트가 순화되지 않았다")
    if any(p in s3["images"] for p in panels) or "PANEL" in "".join(s3["images"]):
        bad.append(f"셋째 제출에 패널: {s3['images']}")
    if s3["images"] != kept:
        bad.append(f"셋째 그림 {s3['images']} (기대 {kept})")
    nums = _image_numbers(s3["prompt"])
    if nums != set(range(1, len(s3["images"]) + 1)):
        bad.append(f"셋째 @Image 번호 {sorted(nums)} ≠ 그림 {len(s3['images'])}장")
    loc = _location_no(s3["prompt"])
    if not loc or loc > len(s3["images"]) or s3["images"][loc - 1] != "LOC":
        bad.append(f"셋째 장소 번호 {loc} 가 장소 그림이 아니다")
    if "@Video1" not in s3["prompt"] or s3["video"] != "VIDEO1":
        bad.append("셋째에 @Video1 이 없다")
    if "uploaded webtoon images (art style" in s3["prompt"]:
        bad.append("셋째에 패널 이름표 줄이 남았다")
    strip = re.compile(r"@Image\d+(?:\u2013@Image\d+)?")
    rest2 = [strip.sub("", ln) for ln in s2["prompt"].split("\n") if "uploaded webtoon images (art" not in ln]
    rest3 = [strip.sub("", ln) for ln in s3["prompt"].split("\n") if "uploaded webtoon images (art" not in ln]
    if [ln for ln in rest2 if "@Image" not in ln and ln not in rest3 and not ln.startswith(("STYLE", "ASSETS"))]:
        bad.append("셋째 프롬프트가 순화한 둘째 프롬프트와 참고 줄 밖에서 다르다")
    if len(s3["prompt"].encode()) > len(s2["prompt"].encode()):
        bad.append("셋째 프롬프트가 둘째보다 길다")
    final = rec["final"]
    if (final.get("status"), final.get("attempt"), final.get("contentDropRefs"), final.get("failureKind"), final.get("error")) != \
            ("completed", 3, True, "", ""):
        bad.append(f"끝 버전 {[final.get(k) for k in ('status', 'attempt', 'contentDropRefs', 'failureKind', 'error')]}")
    if final.get("prompt") != s3["prompt"]:
        bad.append("버전 프롬프트 ≠ 셋째로 보낸 프롬프트")
    if [s["prefix"].rsplit("/", 1)[-1] for s in subs] != ["a1", "a2", "a3"]:
        bad.append(f"시도 번호 {[s['prefix'] for s in subs]}")
    if rec["logs"] and rec["logs"][-1]["refs"] != len(kept):
        bad.append(f"기록 refs {rec['logs'][-1]['refs']} (기대 {len(kept)})")
    return bad


def _baseline() -> None:
    for group, fn in (("dr-c", case_c), ("dr-d", case_d)):
        rec = fn()
        print(group, call_sha(rec), len(rec["submits"]), rec["final"].get("status"), rec["final"].get("attempt"))


def check_retake_droprefs_232() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {got!r}\n      기대 {want!r}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append(f"예외 {type(exc).__name__}: {exc}\n" + traceback.format_exc()[-1200:])

    try:
        _gm, rt, _E = _modules()
        flag_text = rt.CONTENT_FLAG_TEXT
    except Exception as exc:  # noqa: BLE001
        crash("dr-a", exc)
        flag_text = ""

    # (dr-a) 새 꼴 · 옛 꼴 · 제출 자체 거절 두 번
    try:
        kept_vibe = ["SHEET1", "SHEET2", "LOC"]
        rec = run_case(variant="webtoon", prompt=VIBE_PROMPT, images=VIBE_IMAGES, panel_nos=[4, 5, 6], polls=["flag", "flag", "done"])
        expect("dr-a", problems_a(rec, panels=["PANEL14", "PANEL15", "PANEL16"], kept=kept_vibe), [], "새 꼴(패널 맨 끝)")
        expect("dr-a", [rec["notes"], len(rec["ledger"]), [r[1] for r in rec["results"]]],
               [[["contentFlag", "softRetry"], ["contentFlag", "dropRefs"]], 3, ["failed", "failed", "completed"]],
               "노트(순화 → 패널 빼기) · 원장 = 받아 준 제출 3 · 기록")
        rec_vibe = rec
        rec = run_case(variant="webtoon", prompt=OLD_PROMPT, images=OLD_IMAGES, panel_nos=[2, 3], polls=["flag", "flag", "done"])
        expect("dr-a", problems_a(rec, panels=["PANEL15", "PANEL16"], kept=["SHEET1", "LOC"]), [], "옛 꼴(패널이 장소 앞 — 번호 당김)")
        s3 = rec["submits"][2]["prompt"] if len(rec["submits"]) == 3 else ""
        expect("dr-a", ["STYLE: Modern TV-anime adaptation of the original webtoon: keep the webtoon's faces and line art." in s3,
                        "@Image2 is the location" in s3], [True, True], "옛 꼴: STYLE 의 패널 표시만 빠짐 · 장소 @Image2")
        rec = run_case(variant="webtoon", prompt=VIBE_PROMPT, images=VIBE_IMAGES, panel_nos=[4, 5, 6], polls=["done"],
                       submits=["reject", "reject", "ok"])
        subs = rec["submits"]
        expect("dr-a", [len(subs), [s["images"] for s in subs][2:], rec["final"].get("status"), rec["final"].get("contentDropRefs"),
                        len(rec["ledger"])],
               [3, [kept_vibe], "completed", True, 1], "제출 자체 거절 2번(원장 0) → 셋째 패널 없이 → 완성 · 원장 1")
    except Exception as exc:  # noqa: BLE001
        crash("dr-a", exc)
        rec_vibe = {}

    # (dr-b) 셋째도 거절
    try:
        rec = run_case(variant="webtoon", prompt=VIBE_PROMPT, images=VIBE_IMAGES, panel_nos=[4, 5, 6], polls=["flag", "flag", "flag"])
        final = rec["final"]
        expect("dr-b", [len(rec["submits"]), len(rec["polls"]), final.get("status"), final.get("attempt"), final.get("failureKind"),
                        final.get("error"), final.get("contentDropRefs"), final.get("usd"), len(rec["ledger"]), rec["notes"]],
               [3, 3, "failed", 3, "contentFlag", flag_text + FAIL_SENTENCE, True, 0.5, 3,
                [["contentFlag", "softRetry"], ["contentFlag", "dropRefs"], ["contentFlag", "gaveUp"]]],
               "셋째도 거절 → failed · 고정 문구 + 한 문장 · 제출 3 · 견적 그대로 · 원장 3(받아 준 제출마다)")
        expect("dr-b", [FAIL_SENTENCE.strip() in flag_text, "flagged" in str(final.get("error"))], [False, False],
               "한 문장은 셋째 거절에만 · 영어 원문 없음")
        rec = run_case(variant="webtoon", prompt=VIBE_PROMPT, images=VIBE_IMAGES, panel_nos=[4, 5, 6], polls=["flag", "flag"],
                       submits=["ok", "ok", "reject"])
        final = rec["final"]
        expect("dr-b", [len(rec["submits"]), final.get("status"), final.get("error"), len(rec["ledger"])],
               [3, "failed", flag_text + FAIL_SENTENCE, 2], "셋째 제출 자체 거절 → 같은 실패 · 원장 2(거절된 제출 0)")
    except Exception as exc:  # noqa: BLE001
        crash("dr-b", exc)

    # (dr-c) · (dr-d) 지금 동작(sha)
    for group, fn in (("dr-c", case_c), ("dr-d", case_d)):
        try:
            rec = fn()
            final = rec["final"]
            expect(group, [call_sha(rec), len(rec["submits"]), final.get("status"), final.get("attempt"), final.get("error"),
                           "contentDropRefs" in final],
                   [OLD_SHA[group], 2, "failed", 2, flag_text, False], "고치기 전과 같은 호출 순서 · 2번 · 지금 실패 문구")
        except Exception as exc:  # noqa: BLE001
            crash(group, exc)

    # (dr-e) 보기 · 화면 글
    try:
        views = rec_vibe.get("views") or []
        notes = [v.get("contentNote") for v in views]
        expect("dr-e", [notes, (rec_vibe.get("finalView") or {}).get("contentNote")],
               [["", rt.CONTENT_RETRY_TEXT, NOTE_TEXT], ""], "제출 때 보기 안내: 처음 없음 → 순화 중 → 패널 빼고 다시 내는 중, 완성 뒤 \"\"")
        expect("dr-e", [getattr(rt, "CONTENT_DROP_TEXT", None), getattr(rt, "CONTENT_DROP_FAILED_TEXT", None)],
               [NOTE_TEXT, flag_text + FAIL_SENTENCE], "문구 상수")
        js = (ROOT / "frontend" / "public" / "vnew.js").read_text(encoding="utf-8")
        body = js[js.find("function editFixStatusText("):][:900]
        expect("dr-e", ["v.contentNote" in body, "v.error" in body], [True, True], "화면 진행 줄이 contentNote · error 를 그대로 쓴다")
    except Exception as exc:  # noqa: BLE001
        crash("dr-e", exc)

    # (dr-f) 돌연변이
    try:
        rec = run_case(variant="webtoon", prompt=VIBE_PROMPT, images=VIBE_IMAGES, panel_nos=[4, 5, 6], polls=["flag", "done"],
                       mutant="drop-first")
        caught = problems_a(rec, panels=["PANEL14", "PANEL15", "PANEL16"], kept=["SHEET1", "SHEET2", "LOC"])
        expect("dr-f", bool(caught), True, "돌연변이(첫 다시 내기에서 패널 빼기)를 dr-a 검사가 잡는다")
    except Exception as exc:  # noqa: BLE001
        crash("dr-f", exc)

    # (dr-g) LF
    try:
        crlf = [p for p in CHANGED if b"\r\n" in (ROOT / p).read_bytes()]
        expect("dr-g", crlf, [], "CRLF 0")
    except Exception as exc:  # noqa: BLE001
        crash("dr-g", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 232 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 232 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 232 ({group}) {title}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    _baseline()
    sys.exit(0)

if __name__ == "__main__":
    ok = check_retake_droprefs_232()
    print('hook (check_vpoc.py main, 작업 231 다음 줄): if not __import__("check_retake_droprefs_232").check_retake_droprefs_232(): '
          'fail("작업 232: ⑦ 콘텐츠 검사 순화도 거절 → 원작 패널 없이 1번 더 검사 실패")')
    sys.exit(0 if ok else 1)
