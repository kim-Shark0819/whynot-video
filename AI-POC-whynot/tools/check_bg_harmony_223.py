"""작업 223 · 223 fix1 검사 — 배경이 인물과 같은 빛 · 같은 그림체(video-vibecode 배경 규칙 이식). 네트워크 0 · LLM 0 · 유료 0.

  (b-a) 배경 줄 = "Setting (keep identical in every shot): <장소>, <시간대>, <주 광원 + 방향 + 색온도>, <소품 2-4>, <팔레트>; no people in
        the background plate." — fixture 값이 다 들어감, prod 꼴(장소 데이터 없음) = 채움 글 없이 장소 · 시간대 · 주 광원, ≤ 300자
  (b-b) fix1 크기: 같은 줄이기 단계마다 223 글 ≤ 223 전 글(글자 · 바이트), 줄이기 단계 · fits · 조각 수가 같다(vibe · 옛 테이크 여러 상한,
        컷 단위 · 실사 컷 · Kling), 상한 안, 바닥(장소 + 시간대 + 주 광원)은 가장 좁은 단계에도 남는다(Kling 시작 그림 컷만 223 전처럼 뺀다)
  (b-c) 샷 빛 = 주 광원 기준 글: prod 빛(왼쪽 → 뒤 → 정면) → 모두 창 왼쪽, 역광 샷은 같은 광원의 림, ≤ 80자, 설계 방향이 샷 다수보다 먼저,
        실내 광원은 밝혀 둠, Kling 샷 고정 재료도 같은 글
  (b-d) 조화 문장(≤ 200 B, 애니 = line weight, 실사 = 없음, 짧은 형 ≤ 197 짧은 형)이 빌더마다 1번(197 조화 문장 없음)
  (b-e) 웹툰 장소 뷰 지시문 = 화풍 줄 + 인물과 같은 선 · 칠 · 팔레트 문장(상한 자르기 뒤에도), 실사 편집 글 그대로
  (b-f) 장소 그림 이름표 = 참고 구현 reference_lead 글(줄인 단계 · Kling 컷 1개 = 앞부분)
  (b-g) 222 줄 그대로 + 역광 눈 보호 = 그 샷에 나가는 빛 글로 판정(vibe · 옛 테이크 · 컷 단위)
  (b-h) 자막 금지 문장은 잘리지 않는다: 컷 단위 byteTrim(실사 "No text …" · 애니 222 문장) · Kling 빠듯한 상한
  (b-i) 저장된 프롬프트 그대로: 212 _finish_prompt 는 머리에 223 배경 줄이 없으면 옛 글을 건드리지 않고, 있으면 옛 배경 줄만 뺀다
  (b-j) 옛 동작 변이(223 끔 · 크기 맞춤 없음 · 저장된 빛으로 역광 판정 · byteTrim 이 자막 금지를 자름)를 검사가 잡는다
  (b-k) 바꾼 파일 LF 줄 끝
실행: python -X utf8 -B tools/check_bg_harmony_223.py
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
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

HOOK = ('    if not __import__("check_bg_harmony_223").check_bg_harmony_223(): '
        'fail("작업 223: 배경 줄 · 샷 빛 맞춤 · 조화 문장 · 장소 뷰 화풍 · 크기 검사 실패")')
GROUPS = {
    "b-a": "배경 줄 = 장소 · 시간대 · 주 광원 · 소품 · 팔레트(fixture 값) · 채움 글 없음 · ≤ 300자",
    "b-b": "fix1 크기: 같은 단계마다 223 전 글 이하 · 단계 · fits · 조각 수 그대로 · 상한 안 · 바닥 남음",
    "b-c": "샷 빛이 주 광원과 맞음(prod 빛 왼쪽 · 뒤 · 정면 → 창 왼쪽, 림, ≤ 80자)",
    "b-d": "조화 문장 ≤ 200 B — 애니 · 실사 빌더마다 1번(197 조화 없음)",
    "b-e": "웹툰 장소 뷰 지시문 = 화풍 줄 + 팔레트(상한 자르기 뒤에도)",
    "b-f": "장소 그림 이름표 = 참고 구현 reference_lead 글(줄인 단계 · Kling = 앞부분)",
    "b-g": "222 줄 그대로 · 역광 눈 보호 = 나가는 빛 글로 판정",
    "b-h": "자막 금지 문장은 잘리지 않는다(byteTrim · Kling)",
    "b-i": "저장된 프롬프트 그대로 · 212 배경 줄 한 번만",
    "b-l": "223 fix2: setting-line props stay whole (no clipped fragments such as \"Black U-shaped\")",
    "b-j": "옛 동작 변이를 검사가 잡는다",
    "b-k": "바꾼 파일 LF",
}
CHANGED = ["app/src/vnew/generate.py", "app/src/vnew/locations.py", "app/src/vnew/line_redo.py",
           "tools/check_bg_harmony_223.py", "tools/check_vpoc.py"]
PROD_LOC = "Indoor space beside a bright sunlit window"
PROD_LIGHTS = ("soft light from the left", "bright window light from behind", "soft frontal window light")
OFFICE = "A quiet office"
STYLE_EN = "Clean thin line art with soft cel shading, a muted pastel palette and gentle gradients."
SEG3 = {"id": "s1", "cutIds": ["c1", "c2", "c3"], "seconds": 9.0}


@contextlib.contextmanager
def bg_223_off():
    """작업 223 을 끈다(검사용 — 223 전 글). 223 전 글을 기대값으로 두는 옛 검사 · 이 검사의 '같은 단계 223 전 글' 비교가 쓴다."""
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import locations as lm  # noqa: PLC0415

    saved = (gm.BG_HARMONY_223, lm.VIEW_STYLE_223)
    gm.BG_HARMONY_223 = lm.VIEW_STYLE_223 = False
    try:
        yield
    finally:
        gm.BG_HARMONY_223, lm.VIEW_STYLE_223 = saved


def run_223_off(fn) -> None:
    """223 전 글을 기대값으로 두는 검사를 223 을 끈 채로 돌린다(기대값은 바꾸지 않는다)."""
    with bg_223_off():
        fn()


def _plan(a2, location: str, lights, *, rich: bool = False, design_light: str = "") -> dict:
    """컷 c1-c3(3초씩, 대사 없음), 같은 장소 · 샷 빛 lights. rich = 장소 설계 · 사실 · 대본 시간대 · 콘티 배경 글.
    design_light = 장소 설계 lightEn 만 둔 장소(역광 주 광원 fixture)."""
    from src.vnew import locations as lm  # noqa: PLC0415

    plan = a2._plan()  # noqa: SLF001 — 222 fixture(프리비즈 장면 · 인물 2명)
    base = plan["conti"]["cuts"][0]
    plan["conti"]["cuts"] = [{**base, "id": f"c{i}", "no": i, "locationEn": location, "lightEn": light, "dialogueKo": ""}
                             for i, light in enumerate(lights, 1)]
    plan["conti"]["segments"] = [dict(SEG3)]
    plan["script"] = {"entries": [], "setting": {"locationEn": location, "enclosed": True}}
    if rich:
        plan["script"]["setting"]["timeKo"] = "오후"
        plan["conti"]["background"] = {"promptEn": "A quiet office in the afternoon, sunlight through the left windows, "
                                                   "a wooden desk, a potted plant and a whiteboard.", "descKo": ""}
        plan["locations"] = [{
            "key": lm.normalize_name(location), "nameEn": location,
            "design": {"enclosed": True, "floorEn": "light oak wooden floor",
                       "lightEn": "Main light comes from the west-wall windows, soft afternoon daylight.",
                       "propsEn": [{"labelEn": "wooden desk", "wall": "center"}, {"labelEn": "potted plant", "wall": "west"},
                                   {"labelEn": "whiteboard", "wall": "north"}]},
            "facts": {"walls": {"north": {"wallColorEn": "cream", "wallMaterialEn": "painted plaster", "objects": []},
                                "west": {"wallColorEn": "pale grey", "wallMaterialEn": "plaster", "objects": []}},
                      "floorEn": "light oak floor"},
        }]
    elif design_light:
        plan["locations"] = [{"key": lm.normalize_name(location), "nameEn": location,
                              "design": {"enclosed": True, "lightEn": design_light}}]
    return plan


def _cut_cast(a2) -> list[dict]:
    return [{"name": a["name"], "colorName": a["colorName"], "elementRef": "", "appearance": a2.CHARS[a["charId"]]["appearanceEn"]}
            for a in a2.ACTORS]


def _shot_lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.startswith("Shot ") and "(backlit)" not in ln.split(":", 1)[0]]


def _setting_line_of(gm, text: str) -> str:
    """프롬프트의 223 배경 줄(머리부터 꼬리까지 — 조화 문장 앞)."""
    for line in text.split("\n"):
        at = line.find(gm.SETTING_LINE_HEAD + ": ")
        if at >= 0:
            rest = line[at:]
            for tail in (gm.SETTING_LINE_TAIL, gm.SETTING_LINE_TAIL_SHORT):
                if tail in rest:
                    return rest[:rest.index(tail) + len(tail)]
            return rest
    return ""


def _vibe(gm, a2, plan, *, real=False, caps=None, audit=None, location_no=None, cut_ids=None) -> str:
    seg = dict(SEG3, cutIds=list(cut_ids or SEG3["cutIds"]), seconds=3.0 * len(cut_ids or SEG3["cutIds"]))
    return gm._vibe_take_prompt(plan, seg, real=real, cast=a2._cast(gm, real=real), quiet=True, caps=caps,  # noqa: SLF001
                                audit=audit, location_no=location_no)


def _take(gm, a2, plan, *, real=False, caps=None, audit=None, location_no=None) -> str:
    return gm._take_prompt(plan, SEG3, cast=a2._cast(gm, real=real), panel_nos=[], location_no=location_no, quiet=True,  # noqa: SLF001
                           caps=caps, audit=audit, variant="real" if real else "webtoon")


def _group_setting(gm, a2) -> list[str]:
    bad: list[str] = []
    rich_plan = _plan(a2, OFFICE, PROD_LIGHTS, rich=True)
    rich = gm.setting_line(rich_plan, OFFICE)  # 가장 긴 형(상한 여유가 있을 때 나가는 글)
    want = [OFFICE + " (indoor)", "afternoon", "main light: soft window light from the left", "color temperature",
            "wooden desk", "potted plant", "whiteboard", "palette: cream / pale grey walls, light oak floor"]
    missing = [w for w in want if w not in rich]
    if missing or not rich.startswith(gm.SETTING_LINE_HEAD + ": ") or not rich.endswith(gm.SETTING_LINE_TAIL) or len(rich) > 300:
        bad.append(f"설계 fixture 배경 줄({len(rich)}자): 없는 조각 {missing} · {rich!r}")
    in_vibe = _setting_line_of(gm, _vibe(gm, a2, rich_plan, location_no=4))
    if not all(w in in_vibe for w in (OFFICE, "afternoon", "main light: soft window light from the left")):
        bad.append(f"vibe 에 나간 배경 줄에 장소 · 시간대 · 주 광원이 없습니다: {in_vibe!r}")
    prod = _setting_line_of(gm, _vibe(gm, a2, _plan(a2, PROD_LOC, PROD_LIGHTS)))
    want = ["Indoor space", "daytime", "window light from the left"]
    if [w for w in want if w not in prod] or any(f in prod for f in gm.SETTING_FILLER) or "palette:" in prod or len(prod) > 300:
        bad.append(f"prod 꼴 배경 줄({len(prod)}자): 장소 · 시간대 · 주 광원만(채움 글 없음)이어야 합니다 {prod!r}")
    prod_full = gm.setting_line(_plan(a2, PROD_LOC, PROD_LIGHTS), PROD_LOC)
    if "main light: bright window light from the left, neutral daylight color temperature" not in prod_full \
            or any(f in prod_full for f in gm.SETTING_FILLER) or "palette:" in prod_full:
        bad.append(f"prod 꼴 가장 긴 형: {prod_full!r}")
    long_plan = _plan(a2, OFFICE, PROD_LIGHTS, rich=True)
    long_plan["locations"][0]["nameEn"] = "A very long and richly described old municipal office hall " * 3
    long_plan["locations"][0]["design"]["propsEn"] = [{"labelEn": f"tall carved wooden cabinet number {i}"} for i in range(6)]
    long_line = gm.setting_line(long_plan, OFFICE)
    # 223 fix2: whole props are dropped before the place is shortened, so the overlong place may leave no props (3 commas, not 4)
    if len(long_line) > 300 or long_line.count(", ") < 3 or "afternoon" not in long_line or "from the left" not in long_line \
            or not long_line.endswith(gm.SETTING_LINE_TAIL):
        bad.append(f"긴 데이터 배경 줄({len(long_line)}자): 다섯 조각 · 300자 · 꼬리 {long_line!r}")
    real = _setting_line_of(gm, _vibe(gm, a2, _plan(a2, OFFICE, PROD_LIGHTS, rich=True), real=True))
    if not real or "webtoon" in real.lower() or "panels" in real or len(real) > 300:
        bad.append(f"실사 배경 줄: {real!r}")
    forms = gm.setting_forms(_plan(a2, OFFICE, PROD_LIGHTS, rich=True), OFFICE)
    floor = forms[-1]
    if any(len(b) >= len(a) for a, b in zip(forms, forms[1:])) or not all(w in floor for w in (OFFICE, "afternoon", "window light")):
        bad.append(f"배경 줄 형이 짧아지는 순서가 아니거나 바닥에 장소 · 시간대 · 주 광원이 없습니다: {floor!r}")
    return bad


def _sizes(text: str) -> tuple[int, int]:
    return len(text), len(text.encode("utf-8"))


def _group_budget(gm, a2) -> list[str]:
    """fix1: 같은 입력 · 같은 상한에서 줄이기 단계 · fits · 조각 수가 223 전과 같고, 줄이기가 없으면 223 글 ≤ 223 전 글(글자 · 바이트).
    가장 좁은 단계에서만 바닥(장소 + 시간대 + 주 광원)과 주 광원 기준 샷 빛만큼 길 수 있다(배경 줄은 그 밑으로 줄이지 않는다)."""
    bad: list[str] = []
    plans = {"prod": _plan(a2, PROD_LOC, PROD_LIGHTS), "rich": _plan(a2, OFFICE, PROD_LIGHTS, rich=True)}
    for pname, plan in plans.items():
        for builder in ("vibe", "take"):
            for real in (False, True):
                for cap in (None, 1500, 900, 600):
                    caps = {"promptMaxLength": cap} if cap else None
                    build = _vibe if builder == "vibe" else _take
                    on_a, off_a = {}, {}
                    on = build(gm, a2, plan, real=real, caps=caps, audit=on_a, location_no=4)
                    with bg_223_off():
                        off = build(gm, a2, plan, real=real, caps=caps, audit=off_a, location_no=4)
                    tag = f"{pname} {builder} {'real' if real else 'anime'} cap={cap}"
                    longer = any(n > o for n, o in zip(_sizes(on), _sizes(off)))
                    if (cap is None and longer) or on_a.get("compact") != off_a.get("compact") or on_a.get("fits") != off_a.get("fits"):
                        bad.append(f"{tag}: 223 {_sizes(on)} · 223 전 {_sizes(off)} · 단계 {on_a.get('compact')} / "
                                   f"{off_a.get('compact')} · fits {on_a.get('fits')} / {off_a.get('fits')}")
                    line = _setting_line_of(gm, on)
                    if not line or " light " not in line:
                        bad.append(f"{tag}: 바닥 배경 줄(장소 + 주 광원)이 없습니다: {line!r}")
    # 조각 수(157 나누기) — 1,500자 상한
    plan = plans["prod"]

    def probe(sub: dict) -> dict:
        audit: dict = {}
        gm._vibe_take_prompt(plan, sub, real=False, cast=a2._cast(gm), caps={"promptMaxLength": 700}, audit=audit,  # noqa: SLF001
                             quiet=True, location_no=4)
        return audit

    parts_on = gm._prompt_split_parts(plan, SEG3, [], {"audit": probe(SEG3), "probe": probe})  # noqa: SLF001
    with bg_223_off():
        parts_off = gm._prompt_split_parts(plan, SEG3, [], {"audit": probe(SEG3), "probe": probe})  # noqa: SLF001
    if len(parts_on) > len(parts_off):
        bad.append(f"조각 수가 늘었습니다: {len(parts_off)} → {len(parts_on)}")
    # 컷 단위 · 실사 컷 · Kling(시작 그림 · 하드 컷)
    long_plan = _plan(a2, PROD_LOC, PROD_LIGHTS)
    long_plan["conti"]["cuts"][0]["characters"][0]["actionEn"] = "turns slowly toward the window and breathes. " * 120
    for p in (plans["prod"], long_plan):
        for variant in ("webtoon", "real"):
            on = gm._cut_prompt(p, {"id": "xc1", "cutIds": ["c1"]}, variant, cast=_cut_cast(a2), has_image_ref=False)  # noqa: SLF001
            with bg_223_off():
                off = gm._cut_prompt(p, {"id": "xc1", "cutIds": ["c1"]}, variant, cast=_cut_cast(a2), has_image_ref=False)  # noqa: SLF001
            target = int(gm.vnew_settings.PREVIZ_PROMPT_TARGET)
            short_off = len(off.encode()) < target
            if len(on.encode()) > target or (short_off and len(on.encode()) > len(off.encode()) + 8) \
                    or ("\nSETTING:" in off) != ("\nSETTING:" in on) or not _setting_line_of(gm, on):
                bad.append(f"컷 단위 {variant}: {len(off.encode())} B → {len(on.encode())} B(≤ {target}, SETTING 줄 그대로, 배경 줄 있음)")
    kl_cut = long_plan["conti"]["cuts"][0]
    action = "Beomgeonu turns to the window and breathes. " * 40
    for no_start in (False, True):
        kling = {"framing": "FRAMING: Medium close-up of Beomgeonu.", "refs": ["@Element1 is Beomgeonu."], "noStart": no_start}
        on = gm._kling_cut_layout(long_plan, kl_cut, kling, action=action, said=lambda _n: "", prompt_max=1400)  # noqa: SLF001
        with bg_223_off():
            off = gm._kling_cut_layout(long_plan, kl_cut, kling, action=action, said=lambda _n: "", prompt_max=1400)  # noqa: SLF001
        act_on = next((ln for ln in on.split("\n") if ln.startswith("ACTION: ")), "")
        act_off = next((ln for ln in off.split("\n") if ln.startswith("ACTION: ")), "")
        floor = gm.setting_block(long_plan, PROD_LOC, real=True, room=0, harmony=False, time=not gm._real_time_sentence(long_plan))  # noqa: SLF001
        floor_ok = bool(_setting_line_of(gm, on)) if no_start else True
        # 시작 그림 없는 하드 컷만 바닥 배경 줄이 남는다 — ACTION 이 줄어드는 몫 ≤ 바닥 줄 + 문장 1개(문장 경계에서 자른다)
        if len(on.encode()) > 1400 or len(act_on) < len(act_off) - ((len(floor) + 60) if no_start else 0) or not floor_ok:
            bad.append(f"Kling 1,400B(noStart={no_start}): {len(on.encode())} B · ACTION {len(act_off)} → {len(act_on)} · "
                       f"바닥 배경 줄 {floor_ok}")
    real_cut = gm._segment_prompt(long_plan, {"cutIds": ["c1"]}, first_segment=True, real=True, prompt_max=1600)  # noqa: SLF001
    with bg_223_off():
        real_off = gm._segment_prompt(long_plan, {"cutIds": ["c1"]}, first_segment=True, real=True, prompt_max=1600)  # noqa: SLF001
    if len(real_cut.encode()) > 1600 or not _setting_line_of(gm, real_cut) or len(real_cut.encode()) > len(real_off.encode()) + 8:
        bad.append(f"실사 컷 1,600B: {len(real_off.encode())} → {len(real_cut.encode())} B · 배경 줄 {bool(_setting_line_of(gm, real_cut))}")
    return bad


def _group_lights(gm, a2) -> list[str]:
    bad: list[str] = []
    plan = _plan(a2, PROD_LOC, PROD_LIGHTS)
    framing: dict = {}
    vibe = gm._vibe_take_prompt(plan, SEG3, real=False, cast=a2._cast(gm), quiet=True, framing=framing)  # noqa: SLF001
    want = ("soft light from the left", "bright key light from the window on the left, rim light on the hair",
            "soft key light from the window on the left")  # 223 fix1: 주 광원과 맞는 c1 은 저장된 글 그대로
    shots = _shot_lines(vibe)
    for i, (line, light) in enumerate(zip(shots, want), 1):
        if not line.rstrip(".").endswith(light):
            bad.append(f"Shot {i} 빛이 '{light}' 가 아닙니다: {line[-120:]!r}")
    if len(shots) != 3 or any(old in vibe for old in PROD_LIGHTS[1:]):
        bad.append(f"옛 샷 빛이 남았습니다(샷 {len(shots)}개)")
    main = gm.main_light(plan, PROD_LOC)
    if main.get("dir") != "left" or any(gm.light_direction(w) != "left" for w in want) or any(len(w) > 80 for w in want):
        bad.append(f"주 광원 {main} · 샷 빛 방향이 주 방향과 다르거나 80자를 넘습니다")
    action = ((framing.get("shots") or {}).get("c2") or {}).get("action") or ""
    if want[1] not in action:
        bad.append(f"Kling 샷 고정 재료(c2 action)에 맞춘 빛이 없습니다: {action[-120:]!r}")
    rich = _plan(a2, OFFICE, ("bright window light from behind", "soft frontal light", "warm glow from a desk lamp on the right"),
                 rich=True)
    main = gm.main_light(rich, OFFICE)
    lamp = gm.reconcile_light("warm glow from a desk lamp on the right", main)
    if main.get("dir") != "left" or lamp != "soft key light from the window on the left, plus a soft glow from the desk lamp":
        bad.append(f"설계 방향(west 창 = 왼쪽)이 샷 다수(뒤)보다 먼저가 아니거나 실내 광원을 밝히지 않았습니다: {main} · {lamp!r}")
    tie = gm.main_light(_plan(a2, OFFICE, ("light from behind", "soft overhead light")), OFFICE)
    if tie.get("dir") != "above" or gm.reconcile_light("soft light", {"dir": ""}) != "soft light":
        bad.append(f"샷 방향이 같은 수면 역광이 아닌 쪽 · 주 방향 모름 = 그대로가 아닙니다: {tie}")
    return bad


def _group_harmony(gm, a2, lr, rt) -> list[str]:
    bad: list[str] = []
    web, real = gm.CHAR_HARMONY["webtoon"], gm.CHAR_HARMONY["real"]
    short = gm.CHAR_HARMONY_SHORT
    if len(web.encode()) > 200 or len(real.encode()) > 200 or "line weight" not in web or "line weight" in real \
            or any(len(s.encode()) > len(gm.BG_HARMONY_SHORT.encode()) for s in short.values()):
        bad.append(f"조화 문장 바이트 {len(web.encode())} · {len(real.encode())}(≤ 200), 애니 line weight · 실사 없음, 짧은 형 ≤ 197 짧은 형")
    plan = _plan(a2, PROD_LOC, PROD_LIGHTS)

    def builds() -> dict:
        return {
            ("vibe", "webtoon"): _vibe(gm, a2, plan), ("vibe real", "real"): _vibe(gm, a2, plan, real=True),
            ("take", "webtoon"): _take(gm, a2, plan), ("take real", "real"): _take(gm, a2, plan, real=True),
            ("cut", "webtoon"): gm._cut_prompt(plan, {"id": "xc1", "cutIds": ["c1"]}, "webtoon", cast=_cut_cast(a2),  # noqa: SLF001
                                               has_image_ref=False),
            ("cut real", "real"): gm._cut_prompt(plan, {"id": "xc1", "cutIds": ["c1"]}, "real", cast=_cut_cast(a2),  # noqa: SLF001
                                                 has_image_ref=False),
            ("kling", "real"): gm._kling_cut_layout(plan, plan["conti"]["cuts"][0],  # noqa: SLF001
                                                    {"framing": "FRAMING: Close-up.", "refs": []},
                                                    action="Beomgeonu turns.", said=lambda _n: ""),
            ("real cut", "real"): gm._segment_prompt(plan, {"cutIds": ["c1"]}, first_segment=True, real=True),  # noqa: SLF001
        }

    built = builds()
    with bg_223_off():
        before = builds()
    setting = gm.setting_block(plan, PROD_LOC)
    lines = lr.redo_edit_lines({"speaker": "범건우", "textKo": "왜 이제야 왔어?"}, "", 12000, (), anime=True, setting=setting)
    built[("212 redo", "webtoon")] = lr._finish_prompt("\n".join(lines + [rt.COMPOSE_CONTEXT_LINE, built[("vibe", "webtoon")]]),  # noqa: SLF001
                                                        "왜 이제 왔어?", "왜 이제야 왔어?", 0)
    before[("212 redo", "webtoon")] = gm._bg_harmony(False)  # 212 머리 줄은 늘 1번(223 새 줄)  # noqa: SLF001
    old_197 = (gm._bg_harmony(False), gm._bg_harmony(True), gm.BG_HARMONY_SHORT)  # noqa: SLF001
    for (name, variant), text in built.items():
        other = "real" if variant == "webtoon" else "webtoon"
        mine = text.count(gm.CHAR_HARMONY[variant]) + text.count(short[variant])
        was = sum(before[(name, variant)].count(h) for h in old_197)  # 197 조화 문장이 있던 자리 = 223 조화 문장 자리
        if mine != was or gm.CHAR_HARMONY[other] in text or text.count(gm.SETTING_LINE_HEAD) != 1 or any(h in text for h in old_197):
            bad.append(f"{name}: 조화 문장(긴 · 짧은) {mine}번(197 은 {was}번) · 다른 형 {gm.CHAR_HARMONY[other] in text} · 배경 줄 "
                       f"{text.count(gm.SETTING_LINE_HEAD)}번 · 197 조화 {any(h in text for h in old_197)}")
    if sum(t.count(gm.CHAR_HARMONY[v]) + t.count(short[v]) for (_n, v), t in built.items()) < len(built) - 2:
        bad.append("조화 문장이 거의 모든 빌더에서 빠졌습니다")
    small = lr.redo_edit_lines({"speaker": "범건우", "textKo": "왜?"}, "", 1500, (), anime=True,
                               setting=(setting, gm.setting_block(plan, PROD_LOC, room=0)))
    if setting not in lines or lines[1] != gm.audio_rule(True, inner=False, anime=True) or small[-1] != gm.setting_block(plan, PROD_LOC,
                                                                                                                        room=0):
        bad.append("212 머리 줄: 소리 규칙 줄 뒤 배경 줄(좁은 상한 = 바닥 형)이 없습니다")
    return bad


def _group_view(lm) -> list[str]:
    bad: list[str] = []
    loc = {"id": "loc1", "nameEn": OFFICE, "styleEn": STYLE_EN, "extraEn": "", "photos": [],
           "design": {"widthM": 6.0, "depthM": 5.0, "heightM": 2.7, "enclosed": True, "lightEn": "west-wall windows",
                      "walls": {s: {"descEn": f"{s} wall"} for s in lm.SIDES}},
           "facts": {"walls": {"north": {"wallColorEn": "cream", "objects": []}, "west": {"wallColorEn": "pale grey", "objects": []}},
                     "floorEn": "light oak floor"}}
    style_line = STYLE_EN.rstrip(".")
    match = lm.VIEW_MATCH_CHARACTERS.format(palette="a muted pastel palette and gentle gradients; cream and pale grey walls")
    for view in lm.VIEWS:
        text = lm._finalize_prompt(lm._view_prompt("webtoon", view, loc), "webtoon", view, loc)  # noqa: SLF001
        if text.count(style_line) != 1 or text.count(match) != 1:
            bad.append(f"웹툰 {view} 뷰: 화풍 줄 {text.count(style_line)}번 · 팔레트 문장 {text.count(match)}번(기대 1 · 1)")
        long = lm._finalize_prompt("x " * 2000, "webtoon", view, loc)  # noqa: SLF001 — 상한(3,000자) 자르기
        if match not in long or len(long) > lm.PROMPT_HARD_CAP:
            bad.append(f"웹툰 {view} 뷰 상한 자르기 뒤 팔레트 문장이 없습니다({len(long)}자)")
        real = lm._finalize_prompt(lm._view_prompt("real", view, loc), "real", view, loc)  # noqa: SLF001
        if "line weight" in real or "pastel" in real:
            bad.append(f"실사 {view} 뷰에 웹툰 화풍 문장이 들어갔습니다")
    plain = lm._style_clause("webtoon", {})  # noqa: SLF001
    if lm.WEBTOON_STYLE_DEFAULT not in plain or "soft webtoon colors" not in plain:
        bad.append(f"styleEn 이 없을 때 기본 화풍 + 기본 팔레트가 아닙니다: {plain!r}")
    if "{place}" not in lm.PLACE_EDIT or lm._place_edit_prompt("A room").count("line weight"):  # noqa: SLF001
        bad.append("실사 장소 편집 글(PLACE_EDIT)이 바뀌었습니다")
    return bad


def _group_lead(gm, a2) -> list[str]:
    bad: list[str] = []
    plan = _plan(a2, PROD_LOC, PROD_LIGHTS)
    full = "@Image9 is the location (no people in it); keep this place, its background and light in every shot"
    short = "@Image9 is the location (no people in it)"
    for name, build in (("vibe", _vibe), ("take", _take)):
        roomy = build(gm, a2, plan, location_no=9)
        tight_a: dict = {}
        tight = build(gm, a2, plan, location_no=9, caps={"promptMaxLength": 900}, audit=tight_a)
        if roomy.count(full) != 1 or "location reference only" in roomy + tight or short not in tight \
                or (tight_a.get("compact") and full in tight):
            bad.append(f"{name}: reference_lead 장소 이름표(긴 형 {roomy.count(full)}번 · 줄인 단계 짧은 형 {short in tight})")
    saved = gm._cut_location_view  # noqa: SLF001
    gm._cut_location_view = lambda *_a, found=None, **_k: (found.update(key="loc-k") if found is not None else None) or (b"LOC",)  # noqa: SLF001
    try:
        _imgs, _max, layout = gm._kling_hard_cut_images(plan, {"caps": {"imageListMax": 4}, "promptMax": 2200}, "c1", 1,  # noqa: SLF001
                                                       {"refs": []}, b"LOOK", "look-k", None)
    finally:
        gm._cut_location_view = saved  # noqa: SLF001
    if short.replace("@Image9", "@Image1") + "." not in (layout.get("refs") or []):
        bad.append(f"Kling 하드 컷 장소 줄이 reference_lead 앞부분이 아닙니다: {layout.get('refs')}")
    return bad


def _group_222(gm, a2) -> list[str]:
    """222 문장 · 눈 줄 그대로, 역광 눈 보호 = 그 샷에 나가는 빛 글(shot_light)로 판정."""
    bad: list[str] = []
    g = gm.ANIME_BACKLIGHT_EYES
    vibe = _vibe(gm, a2, _plan(a2, PROD_LOC, PROD_LIGHTS))
    if vibe.count(gm.ANIME_NO_SUBTITLES) != 1 or a2.EYE_LINE not in vibe or "Shot 2 (backlit): " + g not in vibe:
        bad.append("prod vibe: 222 자막 금지 · 눈 줄 · Shot 2(나가는 빛 = 림) 역광 눈 보호가 아닙니다")
    # 장소 주 광원이 뒤(역광)면 저장된 빛이 옆 · 정면이어도 나가는 빛은 역광 — 눈 보호도 그 샷들에
    back = _plan(a2, OFFICE, ("soft light from the left", "soft frontal light", "soft light from the right"),
                 design_light="Main light comes from the north-wall windows behind the characters, soft daylight.")
    v = _vibe(gm, a2, back)
    emitted = [gm.shot_light(back, c) for c in back["conti"]["cuts"]]
    t = _take(gm, a2, back)
    cut = gm._cut_prompt(back, {"id": "xc1", "cutIds": ["c1"]}, "webtoon", cast=_cut_cast(a2), has_image_ref=False)  # noqa: SLF001
    if not all(gm._backlit(e) for e in emitted) or "Shots 1, 2 and 3 (backlit): " + g not in v \
            or "Shots 0-3s, 3-6s and 6-9s (backlit): " + g not in t or cut.count(g) != 1:  # noqa: SLF001
        bad.append(f"역광 주 광원: 나가는 빛 {emitted} · vibe/take/컷 단위 눈 보호가 나가는 빛을 따르지 않습니다")
    if any(e not in v for e in emitted):
        bad.append("역광 주 광원: vibe 샷 줄에 판정한 빛 글이 없습니다(판정 글 = 나가는 글)")
    return bad


def _group_no_text(gm, a2) -> list[str]:
    bad: list[str] = []
    plan = _plan(a2, PROD_LOC, PROD_LIGHTS)
    plan["conti"]["cuts"][0]["characters"][0]["actionEn"] = "turns slowly toward the window and breathes. " * 120
    long_cast = [dict(m, appearance=m["appearance"] * 12) for m in _cut_cast(a2)]
    for variant, sentence in (("real", gm.NO_TEXT_SENTENCE), ("webtoon", gm.ANIME_NO_SUBTITLES)):
        text = gm._cut_prompt(plan, {"id": "xc1", "cutIds": ["c1"]}, variant, cast=long_cast, has_image_ref=False)  # noqa: SLF001
        if sentence not in text or len(text.encode()) > int(gm.vnew_settings.PREVIZ_PROMPT_TARGET):
            bad.append(f"컷 단위 {variant} byteTrim({len(text.encode())} B): 자막 금지 문장이 잘렸습니다")
    kl = gm._kling_cut_layout(plan, plan["conti"]["cuts"][0], {"framing": "FRAMING: Close-up.", "refs": []},  # noqa: SLF001
                              action="Beomgeonu turns to the window. " * 80, said=lambda _n: "", prompt_max=900)
    if "No text, no subtitles, no speech bubbles." not in kl or len(kl.encode()) > 900:
        bad.append(f"Kling 900B: 자막 금지 문장이 없습니다({len(kl.encode())} B)")
    return bad


def _group_stored(gm, a2, lr, rt) -> list[str]:
    bad: list[str] = []
    old_setting = "Setting (the same place in every shot): A quiet office (indoor). Render background and characters together in one style."
    stored = "\n".join(["Korean webtoon art style.", old_setting, "Shot 1 (0-3s), medium: Beomgeonu nods. soft light from the left."])
    head = lr.redo_edit_lines({"speaker": "범건우", "textKo": "왜?"}, "", 12000, (), anime=True)
    plain = lr._finish_prompt("\n".join(head + [rt.COMPOSE_CONTEXT_LINE, stored]), "", "", 0)  # noqa: SLF001
    if not plain.endswith(stored):
        bad.append("212: 머리에 223 배경 줄이 없는데 저장된 이전 프롬프트(⑦ 문맥)가 바뀌었습니다")
    setting = gm.setting_block(_plan(a2, OFFICE, PROD_LIGHTS), OFFICE)
    head = lr.redo_edit_lines({"speaker": "범건우", "textKo": "왜?"}, "", 12000, (), anime=True, setting=setting)
    once = lr._finish_prompt("\n".join(head + [rt.COMPOSE_CONTEXT_LINE, stored]), "", "", 0)  # noqa: SLF001
    if old_setting in once or once.count("Setting (") != 1 or "soft light from the left" not in once:
        bad.append("212: 머리에 223 배경 줄이 있으면 옛 배경 줄만 빠지고 나머지는 그대로여야 합니다")
    if gm.dialogue_audio_rewrite(stored) != stored:
        bad.append("저장된 프롬프트(⑦ 기준)를 다시 썼습니다")
    return bad


# 223 fix2: fixture shaped like the prod dry run (outdoor railing by a convenience store, long main light, long props).
FIX2_LOC = "Outdoors by a low metal railing"
FIX2_LIGHT = "dim convenience store glass front light from behind the characters, cool color temperature"


def _fix2_plan(a2) -> dict:
    from src.vnew import locations as lm  # noqa: PLC0415

    plan = _plan(a2, FIX2_LOC, (FIX2_LIGHT,) * 3)
    plan["script"]["setting"].update({"enclosed": False, "timeKo": "낮"})
    plan["locations"] = [{
        "key": lm.normalize_name(FIX2_LOC), "nameEn": FIX2_LOC,
        "design": {"enclosed": False, "floorEn": "light grey asphalt with painted parking lines",
                   "lightEn": "Main light is the dim cool glow of the convenience store glass front behind the characters.",
                   "propsEn": [{"labelEn": "Black U-shaped steel bollard with yellow reflective band (seat for two)"},
                               {"labelEn": "Black U-shaped steel bollard with yellow band"}],
                   "walls": {"north": {"featuresEn": "two parked white compact cars on the asphalt; a row of potted shrubs"},
                             "east": {"featuresEn": "a tall green street lamp, a blue recycling bin"}}},
        "facts": {"walls": {"north": {"wallColorEn": "white and blue; a full-width glass storefront with posters",
                                      "objects": [{"kind": "compact cars", "colorEn": "White"}]},
                            "east": {"wallColorEn": "light grey concrete", "objects": []}},
                  "floorEn": "light grey asphalt"},
    }]
    return plan


def _group_fix2(gm, a2) -> list[str]:
    """223 fix2: every comma piece between the main light and "palette:" is a whole prop, never a clipped fragment."""
    bad: list[str] = []
    plan = _fix2_plan(a2)
    parts = gm.setting_parts(plan, FIX2_LOC)
    props = list(parts["props"])
    if len(props) < 2:
        bad.append(f"fixture props too few: {props!r}")
    short = gm._setting_light(parts["main"], short=True)  # noqa: SLF001
    lights = [x for x in (parts["light"], short, short[len("main light: "):]) if x]
    for real in (False, True):
        for form in gm.setting_forms(plan, FIX2_LOC, real=real):
            body = form[len(gm.SETTING_LINE_HEAD) + 2:]
            for tail in (gm.SETTING_LINE_TAIL, gm.SETTING_LINE_TAIL_SHORT):
                body = body[:-len(tail)] if body.endswith(tail) else body
            light = next((x for x in lights if x in body), "")
            if not light or len(form) > gm.SETTING_LINE_MAX:
                bad.append(f"form without main light or over {gm.SETTING_LINE_MAX}: {form!r}")
                continue
            rest = body[body.index(light) + len(light):].split("palette:")[0]
            want = gm.setting_parts(plan, FIX2_LOC, real=real)["props"]
            for piece in (p.strip() for p in rest.split(",")):
                if not piece:
                    continue
                if piece not in want or any(p != piece and p.startswith(piece) for p in want):
                    bad.append(f"clipped prop fragment {piece!r} (props {want!r}): {form!r}")
    return bad


def _run(fn, *args) -> list[str]:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return ["예외: " + traceback.format_exc()[-900:]]


def check_bg_harmony_223() -> bool:
    import check_anime_prompt_222 as a2  # noqa: PLC0415 — 222 fixture(장면 · 인물 · 눈 줄)

    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import line_redo as lr  # noqa: PLC0415
    from src.vnew import locations as lm  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415

    vlog = logging.getLogger("vpoc")
    saved_disabled, vlog.disabled = vlog.disabled, True
    saved_chars = gm._char_by_id  # noqa: SLF001
    gm._char_by_id = lambda owner, plan: {}  # noqa: SLF001 — 캐릭터 저장소 없이
    groups = {"b-a": lambda: _group_setting(gm, a2), "b-b": lambda: _group_budget(gm, a2), "b-c": lambda: _group_lights(gm, a2),
              "b-d": lambda: _group_harmony(gm, a2, lr, rt), "b-e": lambda: _group_view(lm), "b-f": lambda: _group_lead(gm, a2),
              "b-g": lambda: _group_222(gm, a2), "b-h": lambda: _group_no_text(gm, a2), "b-i": lambda: _group_stored(gm, a2, lr, rt),
              "b-l": lambda: _group_fix2(gm, a2)}
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    try:
        for name, fn in groups.items():
            results[name] = _run(fn)
        problems: list[str] = []
        with bg_223_off():  # 옛 코드(223 끔): 배경 줄 · 빛 · 조화 · 장소 뷰 · 이름표 · 역광 판정 검사가 실패해야 한다
            for name in ("b-a", "b-c", "b-d", "b-e", "b-f", "b-g"):
                (caught.append(f"223끔→{name}") if _run(groups[name]) else problems.append(f"223 끔을 ({name}) 가 잡지 못했습니다"))
        mutants = {  # 이름: (잡아야 할 묶음, 바꿀 속성)
            "noFit": ("b-b", {"setting_room": lambda *_a, **_k: 10 ** 6}),
            "storedLight": ("b-g", {"shot_light": lambda plan, cut, main=None: str((cut or {}).get("lightEn") or "")}),
            "trimNoText": ("b-h", {"NO_TEXT_SENTENCE": "\u0000"}),
            # 223 fix2: per-prop clipping (the old fit step) must be caught
            "clipProps": ("b-l", {"_setting_text": lambda parts, props, *rest, _o=gm._setting_text: _o(parts, [" ".join(p.split()[:2]) for p in props], *rest)}),
        }
        for mname, (group, attrs) in mutants.items():
            saved = {k: getattr(gm, k) for k in attrs}
            try:
                for k, v in attrs.items():
                    setattr(gm, k, v)
                got = _run(groups[group])
            finally:
                for k, v in saved.items():
                    setattr(gm, k, v)
            (caught.append(f"{mname}→{group}") if got else problems.append(f"옛 동작 변이 {mname} 를 ({group}) 가 잡지 못했습니다"))
        results["b-j"] = problems
    finally:
        gm._char_by_id = saved_chars  # noqa: SLF001
        vlog.disabled = saved_disabled
    results["b-k"] = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]

    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 223 ({group}) {title}")
            for item in items[:3]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "b-j" else ""
            print(f"[ok  ] 작업 223 ({group}) {title}{extra}")
    return passed


if __name__ == "__main__":
    _ok = check_bg_harmony_223()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
