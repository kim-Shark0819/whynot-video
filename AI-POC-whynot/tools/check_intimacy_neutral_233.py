"""작업 233 — 콘텐츠 검사 다시 내기의 입맞춤 · 입술 · 신음 글 → 중립 연출(generate._intimacy_neutral = vnew/neutral_wording.py).

(in-a) 거절된 c16 샷 줄 그대로 → kiss · lips · mouths · moan 낱말 0, "faces close together" · cheek · blush · "(0-3s)" · @표시 남음,
       통과한 글과 같은 뜻(낱말 단위 검사)
(in-b) 표(영어 · 한국어 15개 이상: 부정 · forehead-to-forehead · @Image 괄호 · 하읏 대사 · 바뀔 말 없는 줄 = 바이트 그대로) → 기대 글
(in-c) 두 번 써도 같다: f(f(x)) == f(x) (모든 고정 글)
(in-d) 지키는 줄(MANDATORY · 화풍 · look · Setting · @Video1)은 그대로
(in-e) ⑦ 다시 만들기: 195 순화 다시 내기 프롬프트에 입맞춤 낱말 0, 첫 시도 프롬프트는 그대로(sha). 232 셋째(패널 없이)도 0
(in-f) 본 생성 콘텐츠 사다리: 센 순화(3단계 이상) 프롬프트에 입맞춤 낱말 0, 앞 단계(순화 없음 · 153 순화)는 그대로(sha)
(in-g) 돌연변이 "부정한 키스 절을 남김" · "첫 시도를 고침" 을 검사가 잡는다(검사 안에서만 바꾼다)
(in-h) 바꾼 파일 LF

가짜만 쓴다(232 다시 만들기 하네스 · check_vpoc _LrEnv 가짜 계획 · 가짜 LLM). 네트워크 · AWS · 유료 API 없음.
실행: python -X utf8 -B tools/check_intimacy_neutral_233.py   (고치기 전 sha: --baseline)
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_intimacy_neutral_233").check_intimacy_neutral_233(): '
        'fail("작업 233: 콘텐츠 검사 다시 내기 입맞춤 글 중립 연출 검사 실패")')
GROUPS = {
    "in-a": "거절된 c16 샷 줄 → 입맞춤 낱말 0 · 통과한 글과 같은 뜻",
    "in-b": "표(영어 · 한국어 · 부정 · @Image 괄호 · 하읏 · 그대로인 줄)",
    "in-c": "두 번 써도 같다",
    "in-d": "지키는 줄(MANDATORY · 화풍 · look · Setting · @Video1) 그대로",
    "in-e": "⑦ 195 순화 다시 내기 · 232 셋째 = 입맞춤 낱말 0, 첫 시도 그대로(sha)",
    "in-f": "본 생성 사다리 센 순화 = 입맞춤 낱말 0, 앞 단계 그대로(sha)",
    "in-g": "돌연변이(부정 절 남김 · 첫 시도 고침) → 잡힘",
    "in-h": "LF",
}
CHANGED = ("app/src/vnew/neutral_wording.py", "app/src/vnew/generate.py", "app/src/vnew/retake.py",
           "tools/check_intimacy_neutral_233.py", "tools/check_retake_droprefs_232.py")
# 고치기 전(작업 233 전) 코드에서 --baseline 으로 뽑은 sha(첫 시도 · 사다리 앞 단계). 바꾸면 안 된다.
OLD_SHA: dict[str, str] = {
    "retake/first": "d70687d5ec86f976",
    "retake/real-first": "ddcb8994408648b4",
    "take/vibe/webtoon/none": "ce8f3c465a32da67",
    "take/vibe/webtoon/soften": "ce8f3c465a32da67",
    "take/vibe/real/none": "69e0de394cbd02a2",
    "take/vibe/real/soften": "69e0de394cbd02a2",
    "take/old/webtoon/none": "9353c8e3cd72ddc6",
    "take/old/webtoon/soften": "9353c8e3cd72ddc6",
    "take/old/real/none": "b63a7a30caf4f57f",
    "take/old/real/soften": "b63a7a30caf4f57f",
}
# 고치기 전 센 순화 프롬프트에는 입맞춤 낱말이 5개씩 남아 있었다(vibe/old × 애니 · 실사, --baseline).

# 입맞춤 낱말(검사 쪽 — 모듈과 따로 쓴다). 말하기 입 모양 글(lip sync · lips closed · lips do not move)은 먼저 지운다.
BANNED = re.compile(r"\bkiss\w*|\blips?\b|\bmouths\b|\bmoan\w*|\bmak(?:e|es|ing)\s+out\b|\bgasp\w*|uneven breaths|catch\w* his breath"
                    r"|입맞|키스|입술|뽀뽀|하읏|흐읏", re.IGNORECASE)
SPEECH = re.compile(r"lip[- ]?sync\w*|\blip\s+movements?|\b(?:lips|mouths?)\s+(?:stay\s+|stays\s+|are\s+)?closed|\blips\s+(?:do\s+not\s+)?mov\w*"
                    r"|\(Korean, lips move\)", re.IGNORECASE)


def banned(text: str) -> list[str]:
    return BANNED.findall(SPEECH.sub("", str(text or "")))


def sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


C16_REFUSED = (
    "Shot 16 (0-3s), close-up: Beomgeonu (@Image1) screen left, Yeouijun (@Image2) screen right. From 0s their lips are already "
    "apart, faces a few centimetres apart, the kiss just ended. Yeouijun blushes bright pink (soft webtoon blush on cheeks and ears), "
    "lips slightly parted and still as he catches his breath in small uneven breaths, eyes dazed, looking at Beomgeonu the whole "
    "shot. Beomgeonu gently lifts Yeouijun's chin with his fingertips, thumb lightly on the chin, away from both mouths; nothing "
    "between their lips. Static camera. Soft frontal daylight. Yeouijun, spoken (audio only, never written on screen): "
    "\"\ud558\uc74f\u2026\"")
C16_PASSED = ("Beomgeonu screen left, Yeouijun screen right, faces close together. Beomgeonu looks calmly at Yeouijun. Yeouijun "
              "blushes bright pink (soft webtoon blush on cheeks and ears), mouth slightly open, eyes dazed, looking at Beomgeonu "
              "the whole shot. Beomgeonu's right hand rests gently on Yeouijun's cheek and jaw line; left hand rests. Static camera. "
              "Soft frontal daylight.")
SHEET_LINE = ("@Image1 is Beomgeonu's turnaround sheet (front, three-quarter, side, back): for angles the webtoon images do not "
              "show (e.g. a face turned toward camera in a kiss), take face, hair and build from it.")
MOAN = "\ud558\uc74f\u2026"  # 하읏…
TABLE: list[tuple[str, str]] = [
    ("No kiss: lips never touch.", ""),
    ("Forehead-to-forehead, eyes closed; no kiss, lips do not meet, a small gap between mouths.", "Forehead-to-forehead, eyes closed."),
    ("Their faces are a finger's width apart, a finger's width between mouths.", "Their faces are a finger's width apart."),
    ("Kim gently lifts Lee's chin with his fingertips, thumb resting lightly on the chin, away from both mouths; nothing between "
     "their lips.", "Kim gently lifts Lee's chin with his fingertips, thumb resting lightly on the chin, resting on the cheek."),
    (SHEET_LINE, SHEET_LINE.replace("(e.g. a face turned toward camera in a kiss)", "(e.g. a face turned away)")),
    ("Mina kisses him softly.", "Mina leans in close, faces close together."),
    ("Jun presses his lips to Mina's forehead.", "Jun leans in close, faces close together."),
    ("Their lips meet.", "They lean in close, faces close together."),
    ("The kiss just ended; Mina looks down.", "Faces close together; Mina looks down."),
    ("Just before the kiss, Jun leans toward Mina.", "Faces close together, Jun leans toward Mina."),
    ("After the kiss, Mina's lips slightly parted, eyes dazed.", "Faces close together, Mina's mouth slightly open, eyes dazed."),
    ("Lee catches his breath in small uneven breaths.", "Lee breathes softly."),
    ("Mina gasps softly. She moans.", "Mina breathes softly. She breathes softly."),
    ("They make out on the sofa.", "They lean in close, faces close together on the sofa."),
    ("Mina leans toward Jun, their mouths close.", "Mina leans toward Jun, faces close together."),
    # 앞 순화(generate._soften_text 센 순화)가 남긴 글
    ("Mina looks up, faces close just ended.", "Mina looks up, faces close together."),
    ("Mina leans toward Jun, just before faces close.", "Mina leans toward Jun, faces close together."),
    ("No faces close: lips never touch.", ""),
    ("0-5s: MCU: Mina leans toward Jun, just before the kiss, their mouths close; Mina: \"\uac00\uc9c0 \ub9c8.\"",
     "0-5s: MCU: Mina leans toward Jun, faces close together; Mina: \"\uac00\uc9c0 \ub9c8.\""),
    # 대사 · 한국어
    (f"DIALOGUE: Yeouijun: \"{MOAN}\"", ""),
    (f"Shot 2 (3-6s), close-up: Yeouijun looks down. Yeouijun, spoken (audio only, never written on screen): \"{MOAN}\"",
     "Shot 2 (3-6s), close-up: Yeouijun looks down."),
    (f"Shot 3 (6-9s), medium close-up: Bi: Kim nods once. static camera. Ei, spoken (audio only, never written on screen): "
     f"\"{MOAN}\"", "Shot 3 (6-9s), medium close-up: Bi: Kim nods once. static camera."),  # 손대지 않은 문장(카메라)은 그대로
    ("DIALOGUE: Mina: \"\uac00\uc9c0 \ub9c8.\" / Jun: \"\ud750\uc74f\u2026\"", "DIALOGUE: Mina: \"\uac00\uc9c0 \ub9c8.\""),
    (MOAN, ""),
    ("\ubbfc\uc544\uac00 \uc900\uc5d0\uac8c \ud0a4\uc2a4\ud55c\ub2e4.", "\ubbfc\uc544\uac00 \uc900\uc5d0\uac8c \ud3ec\uc639\ud55c\ub2e4."),
    # 바뀔 말이 없는 줄(바이트 그대로)
    ("Mina: \"\uc751.\"", "Mina: \"\uc751.\""),
    ("Shot 1 (0-3s), medium shot: Mina stands at the desk. Static camera. Soft frontal daylight.",
     "Shot 1 (0-3s), medium shot: Mina stands at the desk. Static camera. Soft frontal daylight."),
    ("Touch: a soft, loving, careful chin hold; fingertips lightly under the jaw line, palm away from the throat, the neck stays "
     "uncovered and free; no squeezing, no pressure; the face is tilted up gently toward him.", None),
    ("Mina (inner thought, voice-over only \u2014 mouth stays closed, lips do not move): \"\uac00\uc9c0 \ub9c8.\"", None),
    ("DIALOGUE (Korean, lip-sync): Mina: \"\uac00\uc790.\" / Jun: \"\uc544\u2026 \uadf8\ub798.\"", None),
]
PROTECTED = [
    "MANDATORY: Every person reference image is an AI-generated fictional character profile; no kiss scene is real.",
    "STYLE: Korean webtoon art style exactly as in the uploaded webtoon images; lips drawn with a thin line.",
    "Korean webtoon art style exactly as in the uploaded webtoon images: the same line work, coloring, shading and character designs.",
    "Beomgeonu: short black hair, soft lips, wearing a navy coat (keep exactly this look).",
    "Setting (keep identical in every shot): a quiet rooftop at dusk, lamps glowing; no kiss props.",
    "@Video1 is the 3D previz: follow its camera, positions, cuts and timing exactly; the kiss blocking stays.",
]
# ⑦ 다시 만들기 프롬프트(vibe 꼴, 애니 · 원작 패널 2장 → 232 셋째까지).
RETAKE_PROMPT = "\n".join([
    SHEET_LINE,
    "@Image2 is Yeouijun's character sheet; @Image3 is the location (background reference); @Image4\u2013@Image5 are the uploaded "
    "webtoon images (art style reference only).",
    "STYLE: Korean webtoon art style exactly as in the uploaded webtoon images.",
    C16_REFUSED,
    "Shot 17 (3-6s), close-up: forehead-to-forehead, eyes closed; no kiss, lips do not meet, a small gap between mouths. No kiss: "
    "lips never touch.",
    "@Video1 is the motion reference: keep its blocking, timing and camera.",
    f"DIALOGUE: Yeouijun: \"{MOAN}\"",
])
RETAKE_IMAGES = ["SHEET1", "SHEET2", "LOC", "PANEL14", "PANEL15"]
REAL_RETAKE = "\n".join([
    "@Image1 is Mina (photo reference); @Image2 is the location.",
    "@Video1 is the motion reference.",
    "0-5s: MCU: Mina leans toward Jun, just before the kiss; no kiss, lips do not meet. Mina catches her breath.",
    "DIALOGUE: Mina: \"Don't go.\"",
])
# 본 생성(가짜 계획): c2 = 키스 끝난 컷 · 신음 대사.
TAKE_ACTION = ("From 0s their lips are already apart, faces a few centimetres apart, the kiss just ended. Lee keeps his lips "
               "slightly parted and still as he catches his breath in small uneven breaths, eyes dazed. No kiss: lips never touch.")


def _nw():
    from src.vnew import neutral_wording as nw  # noqa: PLC0415

    return nw


def _f(text: str) -> str:
    from src.vnew import generate as gm  # noqa: PLC0415

    fn = getattr(gm, "_intimacy_neutral", None)
    return fn(text) if fn else _nw().intimacy_neutral(text)


# ---------------------------------------------------------------- (in-e) ⑦ 다시 만들기
def retake_case(prompt: str, *, variant: str, images: list[str], panels: list[int], polls: list[str], mutant: str = "") -> dict:
    import check_retake_droprefs_232 as dr  # noqa: PLC0415

    gm, rt, _E = dr._modules()  # noqa: SLF001
    saved = []
    if mutant == "first":  # 돌연변이: 첫 시도 프롬프트도 고친다
        real_resubmit = rt._resubmit  # noqa: SLF001

        def resubmit(owner, pid, gen_id, variant_, cell_id, n, version):
            if int(version.get("attempt") or 1) == 1:
                version = {**version, "prompt": gm._intimacy_neutral(str(version.get("prompt") or ""))}  # noqa: SLF001
            return real_resubmit(owner, pid, gen_id, variant_, cell_id, n, version)

        saved.append((rt, "_resubmit", real_resubmit))
        rt._resubmit = resubmit  # noqa: SLF001
    try:
        return dr.run_case(variant=variant, prompt=prompt, images=images, panel_nos=panels, polls=polls)
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


# ---------------------------------------------------------------- (in-f) 본 생성 사다리
@contextlib.contextmanager
def take_env(vibe: bool):
    import check_vpoc as cv  # noqa: PLC0415
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415

    owner = "tester"
    with cv._guard_real_llm(), cv._LrEnv(owner) as env, cv._pin_vnew(  # noqa: SLF001
            VNEW_FX=False, VNEW_TAKE_MAX_SECONDS=30, VNEW_VIBE_PROMPT=vibe):
        env.set(gm, "_read_soften", lambda blocks, *, model: ({"items": []}, {"model": model}))

        def echo(blocks, *, model):
            import json  # noqa: PLC0415

            body = json.loads(blocks[0]["text"])
            return {"items": [{"id": it["id"], "text": it["text"]} for it in body["items"]]}, {"model": model}

        env.set(gm, "_read_line_rewrite", echo)
        gm._SOFTEN_CACHE.clear()  # noqa: SLF001
        pid = "prj_in233"
        env.make_plan(pid, [{"id": "s1", "cutIds": ["c1", "c2"], "seconds": 10}])

        def mut(data: dict) -> None:
            for cut in data["conti"]["cuts"]:
                brief = data["previz"]["shotBrief"]["cuts"][cut["id"]]
                brief["actionEn"] = TAKE_ACTION if cut["id"] == "c2" else "Kim nods once."
                cut["dialogueKo"] = f"\uc5d0\uc774: \"{MOAN}\"" if cut["id"] == "c2" else "\uc5d0\uc774: \"\uac00\uc790.\""

        planmod.mutate_plan(owner, pid, mut)
        plan = env.plan(pid)
        take = gm._take_units(plan)[0]  # noqa: SLF001
        yield gm, plan, take, owner, pid, dict(cv._RV152_CAPS)  # noqa: SLF001


LEVELS = (("none", False), ("soften", True), ("strong", "SOFTEN_STRONG"), ("dropRefs", "SOFTEN_DROP_REFS"),
          ("rewrite", "SOFTEN_REWRITE"))


def take_prompts(mutant: str = "") -> dict[str, str]:
    """{vibe/variant/level: 프롬프트}. 실사만 4 · 5단계(dropRefs · rewrite)."""
    out: dict[str, str] = {}
    for vibe in (True, False):
        with take_env(vibe) as (gm, plan, take, owner, pid, caps):
            real_strong = gm._strong  # noqa: SLF001
            if mutant == "first":
                gm._strong = lambda soften: True  # noqa: SLF001 — 돌연변이: 첫 시도 · 153 순화에도 233 을 쓴다
            try:
                for variant in ("webtoon", "real"):
                    for name, level in LEVELS:
                        if variant == "webtoon" and name in ("dropRefs", "rewrite"):
                            continue
                        soften = getattr(gm, level) if isinstance(level, str) else level
                        built = gm._take_payload(owner, pid, plan, take, caps, variant=variant, soften=soften)  # noqa: SLF001
                        out[f"{'vibe' if vibe else 'old'}/{variant}/{name}"] = str(built["prompt"])
            finally:
                gm._strong = real_strong  # noqa: SLF001
    return out


def _baseline() -> None:
    rec = retake_case(RETAKE_PROMPT, variant="webtoon", images=RETAKE_IMAGES, panels=[4, 5], polls=["flag", "flag", "done"])
    print("retake/first", sha(rec["submits"][0]["prompt"]))
    rec = retake_case(REAL_RETAKE, variant="real", images=["RSHEET1", "RLOC"], panels=[], polls=["flag", "flag"])
    print("retake/real-first", sha(rec["submits"][0]["prompt"]))
    for key, prompt in take_prompts().items():
        print(key, sha(prompt), len(banned(prompt)))


def check_intimacy_neutral_233() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    warn: list[str] = []
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {got!r}\n      기대 {want!r}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append(f"예외 {type(exc).__name__}: {exc}\n" + traceback.format_exc()[-1200:])

    def problems_c16(out: str) -> list[str]:
        miss: list[str] = []
        if banned(out):
            miss.append(f"입맞춤 낱말 {banned(out)}")
        for word in ("faces close together", "cheek", "blush", "(0-3s)", "@Image1", "@Image2", "mouth slightly open", "eyes dazed",
                     "Static camera", "Soft frontal daylight", "screen left", "screen right", "Shot 16"):
            if word not in out:
                miss.append(f"{word!r} 없음")
        for word in ("faces close together", "mouth slightly open", "eyes dazed", "blushes bright pink", "Static camera",
                     "Soft frontal daylight", "screen left", "screen right"):
            if word not in C16_PASSED:
                miss.append(f"통과한 글에 {word!r} 없음(검사 고정 글 오류)")
        if "  " in out or ". ." in out or ",," in out or " ," in out or " ." in out or out.endswith((",", ";", ":")):
            miss.append("문장부호 · 빈칸이 깨끗하지 않다")
        return miss

    # (in-a)
    try:
        out = _f(C16_REFUSED)
        expect("in-a", problems_c16(out), [], "c16 거절 줄 → 중립")
    except Exception as exc:  # noqa: BLE001
        crash("in-a", exc)

    # (in-b) · (in-c)
    fixtures = [C16_REFUSED, RETAKE_PROMPT, REAL_RETAKE, *PROTECTED]
    try:
        expect("in-b", len(TABLE) >= 15, True, "표 15개 이상")
        for src, want in TABLE:
            got = _f(src)
            expect("in-b", got, src if want is None else want, f"표: {src[:70]!r}")
            if want is None or want == src:
                expect("in-b", got.encode("utf-8") == src.encode("utf-8"), True, f"바이트 그대로: {src[:60]!r}")
            fixtures.append(src)
    except Exception as exc:  # noqa: BLE001
        crash("in-b", exc)
    try:
        for src in fixtures:
            once = _f(src)
            expect("in-c", _f(once), once, f"f(f(x)) == f(x): {src[:60]!r}")
    except Exception as exc:  # noqa: BLE001
        crash("in-c", exc)

    # (in-d)
    try:
        for line in PROTECTED:
            expect("in-d", _f(line), line, f"지키는 줄: {line[:50]!r}")
        joined = "\n".join(PROTECTED + [C16_REFUSED])
        expect("in-d", _f(joined).split("\n")[:len(PROTECTED)], PROTECTED, "여러 줄 글 안의 지키는 줄")
    except Exception as exc:  # noqa: BLE001
        crash("in-d", exc)

    # (in-e) ⑦ 다시 만들기
    rec_e: dict = {}
    try:
        rec = retake_case(RETAKE_PROMPT, variant="webtoon", images=RETAKE_IMAGES, panels=[4, 5], polls=["flag", "flag", "done"])
        rec_e = rec
        subs = rec["submits"]
        expect("in-e", len(subs), 3, "제출 3번(처음 · 195 순화 · 232 패널 없이)")
        if len(subs) == 3:
            s1, s2, s3 = (s["prompt"] for s in subs)
            expect("in-e", [s1 == RETAKE_PROMPT, sha(s1)], [True, OLD_SHA.get("retake/first")], "첫 시도 = 쓴 글 그대로(sha)")
            expect("in-e", [banned(s2), banned(s3)], [[], []], "195 순화 · 232 셋째 프롬프트 입맞춤 낱말 0")
            expect("in-e", ["(e.g. a face turned away)" in s2, "faces close together" in s2, "@Image1" in s2, "(0-3s)" in s2,
                            "@Video1" in s2, len(s2.encode()) <= len(s1.encode())], [True] * 6,
                   "순화 다시 내기: 괄호 · faces close together · @표시 · 시간 · @Video1 · 바이트 ≤ 처음")
            expect("in-e", rec["final"].get("status"), "completed", "셋째 완성")
        rec = retake_case(REAL_RETAKE, variant="real", images=["RSHEET1", "RLOC"], panels=[], polls=["flag", "flag"])
        subs = rec["submits"]
        expect("in-e", [len(subs), sha(subs[0]["prompt"]) if subs else "", banned(subs[1]["prompt"]) if len(subs) > 1 else ["?"]],
               [2, OLD_SHA.get("retake/real-first"), []], "실사: 첫 시도 그대로(sha) · 순화 다시 내기 입맞춤 낱말 0")
    except Exception as exc:  # noqa: BLE001
        crash("in-e", exc)

    # (in-f) 본 생성 사다리
    try:
        prompts = take_prompts()
        for key, prompt in prompts.items():
            level = key.rsplit("/", 1)[-1]
            if level in ("none", "soften"):
                expect("in-f", sha(prompt), OLD_SHA.get(f"take/{key}"), f"{key}: 앞 단계 그대로(sha)")
            else:
                expect("in-f", banned(prompt), [], f"{key}: 센 순화 입맞춤 낱말 0")
                expect("in-f", [_f(prompt) == prompt, "@Video1" in prompt, "Shot 1" in prompt or "0-5s" in prompt or "Shot" in prompt],
                       [True, True, True], f"{key}: 233 을 지난 글 · @Video1 · 샷 줄")
        expect("in-f", len(prompts), 16, "프롬프트 16개(vibe/old × 애니 3 · 실사 5)")
    except Exception as exc:  # noqa: BLE001
        crash("in-f", exc)

    # (in-g) 돌연변이
    try:
        nw = _nw()
        real_neg = nw._NEG  # noqa: SLF001
        nw._NEG = re.compile(r"(?!x)x")  # noqa: SLF001 — 돌연변이: 부정 절을 빼지 않는다
        try:
            caught = [src for src, want in TABLE if want is not None and _f(src) != want] or problems_c16(_f(C16_REFUSED))
        finally:
            nw._NEG = real_neg  # noqa: SLF001
        expect("in-g", bool(caught), True, "돌연변이(부정한 키스 절을 남김)를 in-a · in-b 가 잡는다")
        rec = retake_case(RETAKE_PROMPT, variant="webtoon", images=RETAKE_IMAGES, panels=[4, 5], polls=["flag", "flag", "done"],
                          mutant="first")
        first = rec["submits"][0]["prompt"] if rec["submits"] else ""
        expect("in-g", sha(first) != OLD_SHA.get("retake/first"), True, "돌연변이(⑦ 첫 시도를 고침)를 in-e sha 가 잡는다")
        mutated = take_prompts(mutant="first")
        caught_f = [k for k, p in mutated.items() if k.endswith(("/none", "/soften")) and sha(p) != OLD_SHA.get(f"take/{k}")]
        expect("in-g", bool(caught_f), True, "돌연변이(본 생성 첫 시도 · 153 순화에도 233)를 in-f sha 가 잡는다")
    except Exception as exc:  # noqa: BLE001
        crash("in-g", exc)

    # (in-h) LF
    try:
        crlf = [p for p in CHANGED if b"\r\n" in (ROOT / p).read_bytes()]
        expect("in-h", crlf, [], "CRLF 0")
    except Exception as exc:  # noqa: BLE001
        crash("in-h", exc)

    if not rec_e:
        warn.append("in-e 하네스 결과가 없어 셋째 보기를 보지 못했습니다")
    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 233 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 233 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 233 ({group}) {title}")
    for item in warn:
        print(f"[WARN] 작업 233 {item}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    _baseline()
    sys.exit(0)

if __name__ == "__main__":
    ok = check_intimacy_neutral_233()
    print("hook (check_vpoc.py main, 작업 232 다음 줄): " + HOOK.strip())
    sys.exit(0 if ok else 1)
