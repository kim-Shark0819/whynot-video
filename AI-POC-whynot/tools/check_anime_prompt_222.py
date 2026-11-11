"""작업 222 검사 — 애니(웹툰) 영상 프롬프트: 자막을 그리지 않는다 · 인물마다 눈 색/모양 줄 · 역광 샷 눈 보호(운영 결함). 네트워크 0.

  (p-a) 자막 금지 고정 문장(≤ 100 B)이 모든 애니 영상 빌더에: vibe 통 테이크 · 옛 통 테이크 · 컷 단위 · 옛 실사→웹툰 변환 ·
        156 글자 다시(no_text) · 168 대사 빼기(침묵) · ⑦ compose · ⑥ 대사 다시 만들기(212) 머리 줄 · 한 번만(_finish_prompt)
  (p-b) 상한에서 줄여도 남는다: vibe · 옛 테이크 600자, 컷 단위 byteTrim, 글자 상한 딱 맞춤
  (p-c) 눈 줄 = 캐릭터 데이터(appearanceEn): "Beomgeonu's eyes: narrow, near-black irises …" · 170자 요약 · 줄이기 뒤에도 남음 ·
        눈 데이터 없는 사람은 줄 없음 · 컷 단위 cast 도
  (p-d) 역광 문장은 역광 샷에만(vibe Shot 번호 · 옛 테이크 시각 · 컷 단위 그 컷만), ≤ 200 B
  (p-e) 실사 프롬프트 = 222 전 글 그대로(눈 줄 · 역광 · 222 문장 없음, 기존 글자 금지 줄 그대로)
  (p-f) 옛 동작 변이(222 문장 없음 · 눈 줄 없음 · 역광 늘 · 역광 없음 · vibe 소리 줄 애니 표시 무시 · 222a 전 _drop_context)를 검사가 잡는다
  (p-g) 바꾼 파일 LF 줄 끝
  (p-h) 작업 222a: ⑦ result(고친 결과 다시 고치기) 빠듯한 상한 — _drop_context 가 줄을 빼도 소리 규칙 줄 · 자막 금지 문장 남음
        (옛 _drop_context 변이는 p-f 가 잡는다)
실행: python -X utf8 -B tools/check_anime_prompt_222.py
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_anime_prompt_222").check_anime_prompt_222(): fail("작업 222: 애니 영상 프롬프트 자막 금지 · 인물 눈 줄 · 역광 눈 보호 검사 실패")
"""

from __future__ import annotations

import contextlib
import logging
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_anime_prompt_222").check_anime_prompt_222(): '
        'fail("작업 222: 애니 영상 프롬프트 자막 금지 · 인물 눈 줄 · 역광 눈 보호 검사 실패")')
GROUPS = {
    "p-a": "자막 금지 문장 = 모든 애니 영상 빌더(vibe · 옛 테이크 · 컷 · 변환 · 156 · 168 · ⑦ · 212), 한 번만",
    "p-b": "상한에서 줄여도 자막 금지 문장 남음(600자 · byteTrim · 딱 맞춤)",
    "p-c": "눈 줄 = 캐릭터 데이터 · 줄이기 뒤에도 남음 · 눈 데이터 없으면 없음",
    "p-d": "역광 문장은 역광 샷에만",
    "p-e": "실사 프롬프트 = 222 전 글 그대로 · 눈 줄/역광 없음",
    "p-f": "옛 동작 변이를 검사가 잡는다",
    "p-g": "바꾼 파일 LF",
    "p-h": "⑦ result 빠듯한 상한: 줄을 빼도 소리 규칙 줄 · 자막 금지 문장 남음(222a)",
}
CHANGED = ["app/src/vnew/generate.py", "app/src/vnew/line_redo.py", "app/src/vnew/retake.py", "tools/check_anime_prompt_222.py",
           "tools/check_dialogue_audio_211.py", "tools/check_vpoc.py"]
BEOM_LOOK = ("Short black hair swept back, narrow near-black eyes under straight brows and a sharp jaw. "
             "He wears a charcoal suit.")
YEO_LOOK = "Soft brown hair with long bangs and a gentle smile. He wears a cream knit sweater."  # 눈 데이터 없음
EYE_LINE = "Beomgeonu's eyes: narrow, near-black irises with visible dark pupils in every shot and every light."
BACKLIT, PLAIN = "bright window light from behind", "soft warm overhead light"


@contextlib.contextmanager
def anime_222_off():
    """작업 222 를 끈다(검사용 — 222 전 글). 자막 금지 문장 · 역광 문장 = 빈 글, 눈 줄 글 = 없음. 222 전 기대값을 두는 검사가 쓴다."""
    from src.vnew import generate as gm  # noqa: PLC0415

    names = ("ANIME_NO_SUBTITLES", "ANIME_BACKLIGHT_EYES", "anime_eye_phrase")
    saved = {n: getattr(gm, n) for n in names}
    gm.ANIME_NO_SUBTITLES = ""
    gm.ANIME_BACKLIGHT_EYES = ""
    gm.anime_eye_phrase = lambda *_a, **_k: ""
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(gm, name, value)


def _plan(lights=(BACKLIT, PLAIN), spoken: bool = True) -> dict:
    """컷 c1 · c2(3초씩). c1 범건우 대사, c2 여의준 대사. lightEn = lights."""
    timeline = [{"cutId": f"c{i}", "start": 3.0 * (i - 1), "end": 3.0 * i,
                 "camera": {"pos": [2.5, 1.5, 1.0], "target": [0.0, 1.2, 1.0], "fov": 54.4, "lensMm": 35},
                 "actors": [{"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0, "pose": "stand"},
                            {"id": "a2", "pos": [0.0, 0.0, 2.0], "facingDeg": 180.0, "pose": "stand"}]} for i in (1, 2)]
    scene = {"locationId": "loc-office", "room": {"width": 6.0, "depth": 6.0, "height": 3.0},
             "actors": [{"id": "a1", "name": "범건우", "charId": "ch1", "heightM": 1.8},
                        {"id": "a2", "name": "여의준", "charId": "ch2", "heightM": 1.75}],
             "props": [], "timeline": timeline}
    lines = {1: "범건우: 왜 이제 왔어?", 2: "여의준: 미안해."} if spoken else {}
    cuts = [{"id": f"c{i}", "no": i, "seconds": 3.0, "panels": [i], "locationEn": "A quiet office", "lightEn": lights[i - 1],
             "characters": [{"name": "범건우", "actionEn": "turns to Yeouijun"}, {"name": "여의준", "actionEn": "looks down"}],
             "situationKo": f"상황 {i}", "dialogueKo": lines.get(i, "")} for i in (1, 2)]
    return {"planId": "pl222", "conti": {"status": "ready", "cuts": cuts,
                                         "segments": [{"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}]},
            "previz": {"scenes": [scene]}, "script": {"entries": []}, "input": {}}


SEG = {"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}
CHARS = {"ch1": {"id": "ch1", "appearanceEn": BEOM_LOOK}, "ch2": {"id": "ch2", "appearanceEn": YEO_LOOK}}
ACTORS = [{"name": "범건우", "charId": "ch1", "colorName": "blue"}, {"name": "여의준", "charId": "ch2", "colorName": "red"}]


def _cast(gm, real: bool = False) -> list[dict]:
    """페이로드와 같은 cast(애니 = _vibe_anime_cast — 참고 그림 없이 캐릭터 외형만, 실사 = 외형만)."""
    if real:
        return [{"name": a["name"], "nameEn": gm._romanize_name(a["name"]), "colorName": a["colorName"],  # noqa: SLF001
                 "appearance": CHARS[a["charId"]]["appearanceEn"], "costume": "dark suit"} for a in ACTORS]
    _images, cast = gm._vibe_anime_cast(ACTORS, CHARS, {"범건우": {"costume": "charcoal suit"},  # noqa: SLF001
                                                      "여의준": {"costume": "cream knit sweater"}}, 4)
    for member in cast:
        member["nameEn"] = gm._romanize_name(member["name"])  # noqa: SLF001
    return cast


def _builds(gm, plan=None, **kw) -> dict:
    plan = plan or _plan()
    cast = _cast(gm)
    cut_cast = [{"name": a["name"], "colorName": a["colorName"], "elementRef": "",
                 "appearance": CHARS[a["charId"]]["appearanceEn"], **gm._cut_eyes(CHARS[a["charId"]], False)}  # noqa: SLF001
                for a in ACTORS]
    out = {
        "vibe": gm._vibe_take_prompt(plan, SEG, real=False, cast=cast, quiet=True, **kw),  # noqa: SLF001
        "take": gm._take_prompt(plan, SEG, cast=cast, panel_nos=[], location_no=None, quiet=True, **kw),  # noqa: SLF001
    }
    for cid in ("c1", "c2"):
        out["cut " + cid] = gm._cut_prompt(plan, {"id": "x" + cid, "cutIds": [cid]}, "webtoon", cast=cut_cast,  # noqa: SLF001
                                          has_image_ref=False)
    return out


def _group_subs(gm, lr, rt) -> list[str]:
    bad: list[str] = []
    s = gm.ANIME_NO_SUBTITLES
    if not s or len(s.encode()) > 100 or "subtitles" not in s.lower():
        bad.append(f"자막 금지 문장이 없거나 100 B 를 넘습니다: {s!r}")
    built = _builds(gm)
    built["vibe no_text(156)"] = gm._vibe_take_prompt(_plan(), SEG, real=False, cast=_cast(gm), quiet=True, no_text=True)  # noqa: SLF001
    built["take no_text(156)"] = gm._take_prompt(_plan(), SEG, cast=_cast(gm), panel_nos=[], location_no=None,  # noqa: SLF001
                                                 quiet=True, no_text=True)
    built["vibe audioDropped(168)"] = gm._vibe_take_prompt(_plan(), SEG, real=False, cast=_cast(gm), quiet=True,  # noqa: SLF001
                                                           lines_fix={"drop": True})
    built["anime convert"] = gm._anime_prompt("u", _plan(), 0)  # noqa: SLF001
    built["anime convert refs"] = gm._anime_prompt("u", _plan(), 2)  # noqa: SLF001
    for name, text in built.items():
        if text.count(s) != 1:
            bad.append(f"{name}: 자막 금지 문장 {text.count(s)}번(기대 1)")
    vibe_last = built["vibe"].split("\n")[-1]
    if not vibe_last.endswith(s) or not gm.has_audio_rule(vibe_last):
        bad.append(f"vibe 맨 끝 줄 = 소리 규칙 + 자막 금지가 아닙니다: {vibe_last[:160]!r}")
    if gm._OLD_NO_TEXT.strip() in built["take"] or gm._OLD_NO_TEXT.strip() in built["cut c1"]:  # noqa: SLF001
        bad.append("옛 테이크 · 컷에 222 전 'No text …' 문장이 222 문장과 함께 두 번 있습니다")
    # ⑦ compose(LLM 0): 기준 = 222 vibe 프롬프트 → 결과에 그대로. 칸 전체 고치기는 상한이 빠듯해도(_trim_to_limit 는 이름표 없는
    # 소리 규칙 줄을 자르지 않는다). result(고친 결과 다시 고치기)의 빠듯한 상한은 (p-h) — 222a 부터 retake._drop_context 는
    # 소리 규칙 줄을 빼지 않는다.
    rng = {"start": 0.0, "end": 3.0, "in": 0.0, "out": 3.0, "seconds": 3.0, "cutIds": ["c1"]}
    for cap, result in ((12000, True), (12000, False), (len(built["vibe"].encode()) + 200, False)):
        out, _meta = rt._compose_prompt(built["vibe"], built["vibe"], rng, 6.0, request_ko="표정을 더 밝게", extra_ko="",  # noqa: SLF001
                                        change_en="Make the expression brighter.", max_bytes=cap, result=result)
        if out.count(s) != 1:
            bad.append(f"⑦ compose(상한 {cap} B, result={result}) 결과에 자막 금지 문장 {out.count(s)}번(기대 1)")
    # ⑥ 대사 다시 만들기(212): 애니 머리 줄에 · 실사 없음 · 이전 프롬프트의 같은 문장은 지워 한 번만
    override = {"speaker": "범건우", "textKo": "왜 이제야 왔어?", "thought": False}
    anime_lines = lr.redo_edit_lines(override, "", 12000, (), anime=True)
    real_lines = lr.redo_edit_lines(override, "", 12000, ())
    if s not in anime_lines[1] or s in "\n".join(real_lines):
        bad.append(f"212 머리 줄: 애니 {s in anime_lines[1]} · 실사 {s in chr(10).join(real_lines)}(기대 True · False)")
    draft = "\n".join(anime_lines + [rt.COMPOSE_CONTEXT_LINE, built["vibe"]])
    finished = lr._finish_prompt(draft, "왜 이제 왔어?", "왜 이제야 왔어?", 0)  # noqa: SLF001
    if finished.count(s) != 1:
        bad.append(f"212 _finish_prompt 뒤 자막 금지 문장 {finished.count(s)}번(기대 1)")
    return bad


def _group_budget(gm) -> list[str]:
    bad: list[str] = []
    s = gm.ANIME_NO_SUBTITLES
    audit: dict = {}
    v600 = gm._vibe_take_prompt(_plan(), SEG, real=False, cast=_cast(gm), caps={"promptMaxLength": 600}, audit=audit,  # noqa: SLF001
                                quiet=True)
    if not audit.get("compact") or not v600.split("\n")[-1].endswith(s):
        bad.append(f"vibe 600자: 줄이기 {audit.get('compact')} 뒤 맨 끝 줄에 자막 금지가 없습니다")
    t_audit: dict = {}
    t600 = gm._take_prompt(_plan(), SEG, cast=_cast(gm), panel_nos=[], location_no=None, quiet=True,  # noqa: SLF001
                           caps={"promptMaxLength": 600}, audit=t_audit)
    rules = next((ln for ln in t600.split("\n") if ln.startswith("RULES:")), "")
    if "rules" not in (t_audit.get("compact") or []) or s not in rules:
        bad.append("옛 테이크 600자: RULES 짧은 형에 자막 금지가 없습니다")
    fit = len(gm._vibe_take_prompt(_plan(), SEG, real=False, cast=_cast(gm), quiet=True))  # noqa: SLF001
    v_fit = gm._vibe_take_prompt(_plan(), SEG, real=False, cast=_cast(gm), caps={"promptMaxLength": fit}, quiet=True)  # noqa: SLF001
    if s not in v_fit or len(v_fit) > fit:
        bad.append("vibe 글자 상한 딱 맞춤: 자막 금지 문장이 없거나 상한을 넘습니다")
    plan = _plan()
    plan["conti"]["cuts"][0]["characters"][0]["actionEn"] = "turns slowly toward the window and breathes. " * 120
    cut = gm._cut_prompt(plan, {"id": "xc1", "cutIds": ["c1"]}, "webtoon", cast=[  # noqa: SLF001
        {"name": "범건우", "colorName": "blue", "elementRef": "", "appearance": BEOM_LOOK * 12,
         **gm._cut_eyes(CHARS["ch1"], False)}], has_image_ref=False)  # noqa: SLF001
    tail = cut.split("\n")[-3:]
    if len(cut.encode()) > int(gm.vnew_settings.PREVIZ_PROMPT_TARGET) or not tail[-1].endswith(s) \
            or not any(EYE_LINE.replace("Beomgeonu", "범건우") == t for t in tail):
        bad.append(f"컷 단위 byteTrim: {len(cut.encode())} B · 맨 끝 묶음 {[t[:50] for t in tail]}(눈 줄 · 자막 금지 남아야 함)")
    return bad


def _group_eyes(gm) -> list[str]:
    bad: list[str] = []
    cast = _cast(gm)
    by = {m["name"]: m for m in cast}
    if by["범건우"].get("eyes") != "narrow, near-black irises with visible dark pupils" or "eyes" in by["여의준"]:
        bad.append(f"_vibe_anime_cast eyes: {[(m['name'], m.get('eyes')) for m in cast]}")
    # 170자 요약이 눈 문장을 자르는 외형이어도 전체 외형에서 뽑는다
    long_look = ("Thick glossy black hair swept back from the forehead with a few loose strands falling over the temples and a "
                 "neat undercut. His face is long and pale with high cheekbones and a calm expression. He has narrow near-black "
                 "eyes.")
    _i, cast_long = gm._vibe_anime_cast(ACTORS[:1], {"ch1": {"appearanceEn": long_look}}, {}, 4)  # noqa: SLF001
    if "near-black" in cast_long[0]["appearance"] or cast_long[0].get("eyes") != by["범건우"].get("eyes"):
        bad.append("170자 요약 밖 눈 문장을 전체 외형에서 뽑지 않았습니다")
    built = _builds(gm)
    for name in ("vibe", "take"):
        text = built[name]
        if text.count(EYE_LINE) != 1 or "Yeouijun's eyes" in text:
            bad.append(f"{name}: 눈 줄 {text.count(EYE_LINE)}번 · 여의준 줄 {'Yeouijun' + chr(39) + 's eyes' in text}")
    v600 = gm._vibe_take_prompt(_plan(), SEG, real=False, cast=cast, caps={"promptMaxLength": 600}, quiet=True)  # noqa: SLF001
    t600 = gm._take_prompt(_plan(), SEG, cast=cast, panel_nos=[], location_no=None, quiet=True,  # noqa: SLF001
                           caps={"promptMaxLength": 600})
    if EYE_LINE not in v600 or EYE_LINE not in t600 or "straight brows" in v600:
        bad.append("600자 줄이기 뒤 눈 줄이 없습니다(또는 외형 줄이기가 돌지 않았습니다)")
    if EYE_LINE.replace("Beomgeonu", "범건우") not in built["cut c1"]:
        bad.append("컷 단위: 범건우 눈 줄이 없습니다")
    phrases = {t: gm.anime_eye_phrase(t) for t in ("dark brown almond-shaped eyes", "dark-eyed, slim", "calm gentle eyes", "")}
    if phrases != {"dark brown almond-shaped eyes": "almond-shaped, dark brown irises with visible dark pupils",
                   "dark-eyed, slim": "dark irises with visible dark pupils", "calm gentle eyes": "", "": ""}:
        bad.append(f"anime_eye_phrase: {phrases}")
    return bad


def _group_backlight(gm) -> list[str]:
    bad: list[str] = []
    g = gm.ANIME_BACKLIGHT_EYES
    if not g or len(g.encode()) > 200:
        bad.append(f"역광 문장이 없거나 200 B 를 넘습니다: {g!r}")
    built = _builds(gm)
    want = {"vibe": "Shot 1 (backlit): " + g, "take": "Shot 0-3s (backlit): " + g}
    for name, line in want.items():
        if built[name].count(g) != 1 or line not in built[name]:
            bad.append(f"{name}: 역광 문장 {built[name].count(g)}번 · 이름표 {line[:24]!r} {line in built[name]}")
    if built["cut c1"].count(g) != 1 or g in built["cut c2"]:
        bad.append(f"컷 단위: 역광 c1 {built['cut c1'].count(g)}번 · c2 {g in built['cut c2']}(기대 1 · False)")
    plain = _builds(gm, _plan((PLAIN, PLAIN)))
    both = _builds(gm, _plan(("bright diffuse window backlight", BACKLIT)))
    if any(g in t for t in plain.values()):
        bad.append("역광 샷이 없는데 역광 문장이 있습니다")
    if "Shots 1 and 2 (backlit): " + g not in both["vibe"]:
        bad.append("두 샷 역광: vibe 'Shots 1 and 2 (backlit)' 줄이 없습니다")
    return bad


def _group_real(gm) -> list[str]:
    bad: list[str] = []
    cast = _cast(gm, real=True)
    plan = _plan()

    def real_builds() -> dict:
        return {"vibe": gm._vibe_take_prompt(plan, SEG, real=True, cast=cast, quiet=True),  # noqa: SLF001
                "take": gm._take_prompt(plan, SEG, cast=cast, panel_nos=[], location_no=None, quiet=True,  # noqa: SLF001
                                        variant="real"),
                "cut": gm._cut_prompt(plan, {"id": "xc1", "cutIds": ["c1"]}, "real", cast=[  # noqa: SLF001
                    {"name": m["name"], "colorName": m["colorName"], "elementRef": "", "appearance": m["appearance"]}
                    for m in cast], has_image_ref=False),
                "kling": gm._segment_prompt(plan, {"cutIds": ["c1"]}, first_segment=True, real=True)}  # noqa: SLF001

    now = real_builds()
    with anime_222_off():
        old = real_builds()
    for name, text in now.items():
        if text != old[name]:
            bad.append(f"실사 {name}: 222 전 글과 다릅니다")
        if "'s eyes:" in text or gm.ANIME_BACKLIGHT_EYES in text or gm.ANIME_NO_SUBTITLES in text:
            bad.append(f"실사 {name}: 눈 줄 · 역광 · 222 문장이 들어갔습니다")
    keeps = {"take": "No text, no subtitles, no speech bubbles.", "cut": "No text, no subtitles, no speech bubbles.",
             "kling": "No text"}
    for name, needle in keeps.items():
        if needle not in now[name]:
            bad.append(f"실사 {name}: 기존 글자 금지 줄({needle})이 없습니다")
    if gm._cut_eyes(CHARS["ch1"], True):  # noqa: SLF001
        bad.append("실사 컷 cast 에 eyes 가 붙었습니다")
    stored = "RULES: Reproduce @Video1.\nKorean dialogue only; no music/ambient/SFX."
    if gm.dialogue_audio_rewrite(stored) != stored:
        bad.append("저장된 옛 프롬프트(⑦ 기준)가 바뀌었습니다")
    return bad


def _group_retake_tight(gm, rt) -> list[str]:
    """작업 222a: ⑦ result(고친 결과 다시 고치기) 빠듯한 상한 — retake._drop_context 가 줄을 빼도 소리 규칙 줄(+ 자막 금지)은 남는다."""
    bad: list[str] = []
    s = gm.ANIME_NO_SUBTITLES
    vibe = _builds(gm)["vibe"]
    audio = vibe.split("\n")[-1]
    rng = {"start": 0.0, "end": 3.0, "in": 0.0, "out": 3.0, "seconds": 3.0, "cutIds": ["c1"]}
    kw = {"request_ko": "표정을 더 밝게", "extra_ko": "", "change_en": "Make the expression brighter.", "result": True}
    roomy, _m = rt._compose_prompt(vibe, vibe, rng, 6.0, max_bytes=12000, **kw)  # noqa: SLF001
    size = len(roomy.encode())
    if audio not in roomy.split("\n"):
        bad.append(f"넉넉한 상한: 결과에 소리 규칙 줄이 없습니다: {audio[:120]!r}")
    for cap in (size - 120, size // 2):
        out, meta = rt._compose_prompt(vibe, vibe, rng, 6.0, max_bytes=cap, **kw)  # noqa: SLF001
        got = len(out.encode())
        if not meta.get("cut") or got > cap:
            bad.append(f"상한 {cap} B: 뺀 줄 {meta.get('cut')} · {got} B(줄을 빼고 상한 안이어야 함)")
        if out.count(s) != 1 or audio not in out.split("\n") or not gm.has_audio_rule(out):
            bad.append(f"상한 {cap} B: 자막 금지 {out.count(s)}번(기대 1) · 소리 규칙 줄 {audio in out.split(chr(10))}")
    return bad


def _old_drop_context(rt):
    """222a 전 retake._drop_context(소리 규칙 줄도 뒤부터 뺀다) — 변이 검사용."""
    def drop(lines: list[str], fixed: int, max_bytes: int) -> tuple[list[str], int]:
        dropped, at = 0, len(lines) - 1
        while rt._bytes_of("\n".join(lines)) > int(max_bytes) and at >= fixed:  # noqa: SLF001
            line = lines[at]
            if line.strip() and not rt._REF_RE.search(line) and not rt._is_def(line):  # noqa: SLF001
                del lines[at]
                dropped += 1
            at -= 1
        return lines, dropped
    return drop


def _run(fn, *args) -> list[str]:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return ["예외: " + traceback.format_exc()[-900:]]


def check_anime_prompt_222() -> bool:
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import line_redo as lr  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415

    vlog = logging.getLogger("vpoc")
    saved_disabled, vlog.disabled = vlog.disabled, True
    groups = {"p-a": lambda: _group_subs(gm, lr, rt), "p-b": lambda: _group_budget(gm), "p-c": lambda: _group_eyes(gm),
              "p-d": lambda: _group_backlight(gm), "p-e": lambda: _group_real(gm), "p-h": lambda: _group_retake_tight(gm, rt)}
    saved_chars = gm._char_by_id  # noqa: SLF001
    gm._char_by_id = lambda owner, plan: {}  # noqa: SLF001 — 캐릭터 저장소 없이
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    try:
        for name, fn in groups.items():
            results[name] = _run(fn)
        mutants = {  # 이름: (설명, 잡아야 할 묶음, 바꿀 속성)
            "noSubs": ("222 문장 없음", "p-a", {"ANIME_NO_SUBTITLES": ""}),
            "noEyes": ("눈 줄 없음", "p-c", {"anime_eye_phrase": lambda *_a, **_k: ""}),
            "alwaysBacklit": ("역광 늘", "p-d", {"_backlit": lambda *_a, **_k: True}),
            "neverBacklit": ("역광 없음", "p-d", {"_backlit": lambda *_a, **_k: False}),
            "vibeAudioPlain": ("vibe 소리 줄 애니 표시 무시", "p-b",
                               {"_vibe_audio_line": lambda has, **_k: gm.AUDIO_DIALOGUE_ONLY_COMPACT if has else
                                gm.AUDIO_SILENCE_ONLY_COMPACT}),
            "dropAudio": ("222a 전 _drop_context(소리 규칙 줄도 뺌)", "p-h", {"_drop_context": _old_drop_context(rt)}, rt),
        }
        problems: list[str] = []
        for mname, (desc, group, attrs, *target) in mutants.items():
            mod = target[0] if target else gm
            saved = {k: getattr(mod, k) for k in attrs}
            try:
                for k, v in attrs.items():
                    setattr(mod, k, v)
                got = _run(groups[group])
            finally:
                for k, v in saved.items():
                    setattr(mod, k, v)
            (caught.append(f"{mname}→{group}") if got else problems.append(f"옛 동작 변이 {mname}({desc})를 ({group}) 가 잡지 못했습니다"))
        results["p-f"] = problems
    finally:
        gm._char_by_id = saved_chars  # noqa: SLF001
        vlog.disabled = saved_disabled
    results["p-g"] = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 222 ({group}) {title}")
            for item in items[:3]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "p-f" else ""
            print(f"[ok  ] 작업 222 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__":
    _ok = check_anime_prompt_222()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
