"""작업 211 검사 — 생성 영상 소리 = 인물 대사만(배경음악 · 앰비언트 · 효과음 · 내레이션 없음). 유료 · 네트워크 0.

  (d-a) 규칙 글: AUDIO_DIALOGUE_ONLY(원형) · 짧은 형 · 침묵 형, 옛 글(앰비언트)은 상수에 없다
  (d-b) 빌더마다 규칙: 옛 통 테이크(애니 · 실사) · vibe 통 테이크(애니 · 실사) · 컷 단위 · 실사 Kling 컷(옛 짜임 · 177 짜임) · 옛 구간
  (d-c) 156 글자 다시 만들기(no_text) · 153 순화(soften) · 168 대사 순화(lines_fix map)도 규칙 그대로
  (d-d) 앰비언트 · 음악을 시키는 글 없음(부정 구절 · 규칙 글을 뺀 나머지에 music · ambient · soundtrack · sound effect 낱말 없음)
  (d-e) 예산: 넘친 통 테이크(옛 · vibe) · 컷 단위 · Kling 컷에 짧은 형이 남는다 · vibe 는 157 짧은 형 순서 그대로
  (d-f) 부정 구절: 실사 Kling 컷 NEGATIVE 줄에 음악 · 앰비언트 · 효과음 · fal 영상 caps 에 부정 프롬프트 필드 없음(WARN 아님 — 알림)
  (d-g) 168 audioDropped(lines_fix drop) · 말 없는 컷 · 195 순화 다시 내기 = 침묵 규칙(대사 규칙 없음) · 소리 켬/끔 매개변수 그대로
  (d-h) ⑦ AI로 고치기: 옛 소리 문장 → 새 규칙(상한이 모자라면 짧은 형) · compose(LLM 0) · 자르기가 규칙 조각을 지우지 않는다
  (d-i) LF 줄 끝(바꾼 파일)

실행: python -X utf8 -B tools/check_dialogue_audio_211.py
"""

from __future__ import annotations

import inspect
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
    "d-a": "규칙 글(원형 · 짧은 형 · 침묵 형) · 옛 앰비언트 글 없음",
    "d-b": "빌더마다 규칙(옛 · vibe 통 테이크 애니/실사 · 컷 단위 · 실사 Kling 컷 · 옛 구간)",
    "d-c": "156 no_text · 153 soften · 168 대사 순화도 규칙 그대로",
    "d-d": "앰비언트 · 음악을 시키는 글 없음",
    "d-e": "예산 초과 → 짧은 형이 남는다 · 157 짧은 형 순서 그대로",
    "d-f": "부정 구절에 음악 · 앰비언트 · 효과음",
    "d-g": "168 audioDropped · 말 없는 컷 · 195 순화 = 침묵 규칙 · 소리 켬/끔 그대로",
    "d-h": "⑦ AI로 고치기: 옛 문장 → 새 규칙 · compose · 자르기 보호",
    "d-i": "LF 줄 끝",
    "d-j": "211 fix1 바이트 중립: 옛 문장 자리 ≤ 옛 글 · 옛 통 테이크 짧은 형 단계 = 211 전",
}
CHANGED = ["app/src/vnew/generate.py", "app/src/vnew/retake.py", "tools/check_vpoc.py", "tools/check_dialogue_audio_211.py"]
HOOK = ('    if not __import__("check_dialogue_audio_211").check_dialogue_audio_211(): '
        'fail("작업 211: 영상 소리 = 대사만(음악 · 앰비언트 · 효과음 없음) 프롬프트 검사 실패")')
OLD_FULL = ("Generate synchronized audio: characters speak the Korean lines in their voices; natural ambient sound of the "
            "location; no background music, no narration.")
OLD_COMPACT = "Korean dialogue and ambient sound, no music."


def _plan(spoken: bool = True) -> dict:
    """컷 c1 · c2(3초씩, 사무실). c1 민수 대사(spoken 이면), c2 지아 속마음. 프리비즈 장면 1개."""
    timeline = []
    for i in (1, 2):
        timeline.append({"cutId": f"c{i}", "start": 3.0 * (i - 1), "end": 3.0 * i,
                         "camera": {"pos": [2.5, 1.5, 1.0], "target": [0.0, 1.2, 1.0], "fov": 54.4, "lensMm": 35},
                         "actors": [{"id": "a1", "pos": [0.0, 0.0, 0.0], "facingDeg": 0.0, "pose": "stand"},
                                    {"id": "a2", "pos": [0.0, 0.0, 2.0], "facingDeg": 180.0, "pose": "stand"}]})
    scene = {"locationId": "loc-office", "room": {"width": 6.0, "depth": 6.0, "height": 3.0},
             "actors": [{"id": "a1", "name": "민수", "charId": "ch1", "heightM": 1.7},
                        {"id": "a2", "name": "지아", "charId": "ch2", "heightM": 1.7}],
             "props": [], "timeline": timeline}
    lines = {1: "민수: 괜찮아?", 2: "지아: (속마음) 괜찮을 리가 없잖아."} if spoken else {}
    cuts = [{"id": f"c{i}", "no": i, "seconds": 3.0, "panels": [i], "locationEn": "A quiet office",
             "characters": [{"name": "민수", "actionEn": "looks at Jia"}, {"name": "지아", "actionEn": "smiles"}],
             "situationKo": f"상황 {i}", "dialogueKo": lines.get(i, "")} for i in (1, 2)]
    return {"planId": "pl211", "conti": {"status": "ready", "cuts": cuts,
                                         "segments": [{"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}]},
            "previz": {"scenes": [scene]}, "script": {"entries": []}, "input": {}}


CAST = [{"name": "민수", "colorName": "blue", "costume": "navy suit"}, {"name": "지아", "colorName": "red", "costume": "white blouse"}]


def _residue(text: str, gm) -> str:
    """규칙 글 · 부정 구절을 뺀 나머지(여기에 음악 · 앰비언트 낱말이 있으면 그것을 시키는 글이다)."""
    out = str(text or "")
    for rule in (gm.AUDIO_DIALOGUE_ONLY, gm.AUDIO_DIALOGUE_ONLY_SPOKEN, gm.AUDIO_DIALOGUE_ONLY_COMPACT, gm.AUDIO_SILENCE_ONLY,
                 gm.AUDIO_SILENCE_ONLY_COMPACT, gm.AUDIO_NEGATIVE):  # 211 fix2: 속마음 없는 형도
        out = out.replace(rule, "")
    return re.sub(r"(?m)^NEGATIVE:.*$", "", out)


def check_dialogue_audio_211() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    notes: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-900:])

    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415
    from src.vpoc import fal as vfal  # noqa: PLC0415

    saved_char = gm._char_by_id  # noqa: SLF001
    gm._char_by_id = lambda owner, plan: {}  # noqa: SLF001 — 캐릭터 저장소 없이
    full, compact = gm.AUDIO_DIALOGUE_ONLY, gm.AUDIO_DIALOGUE_ONLY_COMPACT
    # 211 fix2: 속마음 줄이 없는 통 테이크 원형은 "inner" 낱말 없는 형(이 계획의 "(속마음)" 글은 속마음 판정이 아니다 — spoken 만)
    full_sp = gm.AUDIO_DIALOGUE_ONLY_SPOKEN
    silent, silent_c = gm.AUDIO_SILENCE_ONLY, gm.AUDIO_SILENCE_ONLY_COMPACT
    rules = (full, compact, silent, silent_c)
    rules_all = rules + (full_sp,)  # 211 fix2

    def has_full(v: str) -> bool:
        return full in v or full_sp in v
    seg = {"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}
    built: dict[str, str] = {}
    variants: dict[str, str] = {}

    def take(plan=None, **kw) -> str:
        return gm._take_prompt(plan or _plan(), seg, cast=CAST, panel_nos=[], location_no=None, quiet=True, **kw)  # noqa: SLF001

    def vibe(plan=None, **kw) -> str:
        return gm._vibe_take_prompt(plan or _plan(), seg, cast=CAST, quiet=True, **kw)  # noqa: SLF001

    def cut(plan=None, cid: str = "c1") -> str:
        return gm._cut_prompt(plan or _plan(), {"id": "x" + cid, "cutIds": [cid]}, "webtoon", cast=CAST,  # noqa: SLF001
                              has_image_ref=False)

    try:
        # ================= (d-a) 규칙 글 =================
        try:
            expect("d-a", [full.startswith("Audio: only the characters speaking their Korean lines"),
                           all(w in full for w in ("no music", "no ambient sound", "no sound effects", "no narration",
                                                   "inner voice-overs")),
                           compact, "silence" in silent and "Silence" in silent_c,
                           [gm.audio_rule(True), gm.audio_rule(True, compact=True), gm.audio_rule(False),
                            gm.audio_rule(False, compact=True)] == list(rules),
                           any(OLD_COMPACT in str(getattr(gm, n)) or "natural ambient" in str(getattr(gm, n))
                               for n in ("TAKE_RULES_COMPACT", "AUDIO_DIALOGUE_ONLY", "AUDIO_DIALOGUE_ONLY_COMPACT"))],
                   [True, True, "Korean dialogue only; no music/ambient/SFX.", True, True, False],  # 211 fix1
                   "원형 · 짧은 형 · 침묵 형 글 · 옛 앰비언트 글 없음")
        except Exception as exc:  # noqa: BLE001
            crash("d-a", exc)

        # ================= (d-b) 빌더마다 규칙 =================
        try:
            built["take"] = take()
            built["take_real"] = take(variant="real")
            built["vibe"] = vibe(real=False)
            built["vibe_real"] = vibe(real=True)
            built["cut"] = cut()
            built["kling_cut"] = gm._segment_prompt(_plan(), {"cutIds": ["c1"]}, first_segment=True, real=True)  # noqa: SLF001
            built["segment"] = gm._segment_prompt(_plan(), {"cutIds": ["c1"]}, first_segment=True)  # noqa: SLF001
            layout = {"framing": "FRAMING (shot lock): medium two-shot", "costume": "", "action": "looks at Jia", "refs": []}
            built["kling_177"] = gm._kling_cut_layout(_plan(), _plan()["conti"]["cuts"][0], layout,  # noqa: SLF001
                                                      action="looks at Jia", said=lambda limit: 'DIALOGUE (Korean, lip-sync): Minsu: "괜찮아?"')
            got = {k: [has_full(v), compact in v] for k, v in built.items()}  # 211 fix2: 원형 = 속마음 형 · 없는 형
            # 211 fix1: 옛 문장 자리(옛 통 테이크 RULES) = 원형, 옛 문장이 없던 자리(vibe · 컷 · Kling · 옛 구간) = 짧은 형
            want = {"take": [True, False], "take_real": [True, False], "vibe": [False, True], "vibe_real": [False, True],
                    "cut": [False, True], "kling_cut": [False, True], "segment": [False, True], "kling_177": [False, True]}
            expect("d-b", got, want, "빌더마다 규칙(옛 통 테이크 = 원형, vibe · 2,500 컷 · Kling · 옛 구간 = 짧은 형)")
            rules_line = next((ln for ln in built["take"].split("\n") if ln.startswith("RULES:")), "")
            vibe_last = built["vibe"].split("\n")[-1]
            cut_dlg = next((ln for ln in built["cut"].split("\n") if ln.startswith("DIALOGUE:")), "")
            # 211 fix2: 컷 단위 규칙은 DIALOGUE 줄에 붙이지 않고 맨 끝 자기 줄(DIALOGUE 줄 형식 그대로)
            expect("d-b", [rules_line.endswith(" " + full) or rules_line.endswith(" " + full_sp),
                           rules_line.count("Audio:"), vibe_last,
                           built["cut"].split("\n")[-1] == compact and compact not in cut_dlg],
                   [True, 1, compact, True], "옛 테이크 RULES 끝 · vibe 맨 끝 줄 · 컷 맨 끝 줄(DIALOGUE 줄 밖, 규칙은 한 번)")
        except Exception as exc:  # noqa: BLE001
            crash("d-b", exc)

        # ================= (d-c) 156 · 153 · 168 =================
        try:
            mapped = {"map": {"괜찮아?": "괜찮니?"}}
            variants.update({"take no_text": take(no_text=True), "vibe no_text": vibe(real=False, no_text=True),
                        "take soften": take(soften=True), "vibe soften": vibe(real=True, soften=True),
                        "take lines map": take(lines_fix=mapped), "vibe lines map": vibe(real=False, lines_fix=mapped)})
            expect("d-c", {k: [has_full(v) if k.startswith("take") else compact in v, sum(v.count(r) for r in rules_all)]
                           for k, v in variants.items()},  # 211 fix2: 원형 = 속마음 형 · 없는 형
                   {k: [True, 1] for k in variants}, "다시 만들기 길도 같은 규칙 1번(옛 테이크 원형 · vibe 짧은 형)")
            expect("d-c", [gm.TEXT_RETRY_RULE[1:] in variants["vibe no_text"], gm.TEXT_RETRY_RULE in variants["take no_text"]],
                   [True, True], "156 글자 금지 문장도 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("d-c", exc)

        # ================= (d-d) 앰비언트 · 음악을 시키는 글 없음 =================
        try:
            leftovers = {}
            for name, text in {**built, **variants}.items():
                rest = _residue(text, gm).lower()
                words = [w for w in ("ambient", "music", "soundtrack", "sound effect", "room tone", "narration", "score")
                         if w in rest]
                if words or "ambient sound" in text.replace(full, "").replace(full_sp, "").replace(silent, ""):  # 211 fix2
                    leftovers[name] = words
            expect("d-d", leftovers, {}, "규칙 · 부정 구절 밖에 음악 · 앰비언트 · 효과음 낱말 없음(옛 'natural ambient sound' 없음)")
        except Exception as exc:  # noqa: BLE001
            crash("d-d", exc)

        # ================= (d-e) 예산 =================
        try:
            audit: dict = {}
            small = gm._take_prompt(_plan(), seg, cast=CAST, panel_nos=[], location_no=None, quiet=True,  # noqa: SLF001
                                    caps={"promptMaxLength": 600}, audit=audit)
            rules_line = next((ln for ln in small.split("\n") if ln.startswith("RULES:")), "")
            expect("d-e", ["rules" in (audit.get("compact") or []), rules_line.endswith(compact), has_full(small),
                           list(audit.get("compact") or [])[:6] == list(gm.TAKE_COMPACT_ORDER[:6])],
                   [True, True, False, True], "옛 통 테이크 600자: RULES 짧은 형 끝에 짧은 규칙 · 157 순서 그대로")
            v_audit: dict = {}
            v_small = gm._vibe_take_prompt(_plan(), seg, real=False, cast=CAST, caps={"promptMaxLength": 600},  # noqa: SLF001
                                           audit=v_audit, quiet=True)
            order = list(gm.VIBE_COMPACT_ORDER)
            got_c = list(v_audit.get("compact") or [])
            expect("d-e", [v_small.split("\n")[-1], has_full(v_small), got_c == order[:len(got_c)], bool(got_c)],
                   [compact, False, True, True], "vibe 600자: 157 짧은 형을 다 쓴 뒤 맨 끝 줄 = 짧은 규칙")
            fit = len(vibe(real=False)) + 5
            v_fit = gm._vibe_take_prompt(_plan(), seg, real=False, cast=CAST, caps={"promptMaxLength": fit},  # noqa: SLF001
                                         quiet=True)
            expect("d-e", [v_fit.split("\n")[-1]], [compact], "vibe 상한 안: 짧은 형 그대로(211 fix1 — 옛 문장이 없던 자리)")
            k_cap = len(built["kling_cut"].encode()) - 30  # 가운데(샷 · 연기)를 줄여야 하는 상한
            k_small = gm._segment_prompt(_plan(), {"cutIds": ["c1"]}, first_segment=True, real=True, prompt_max=k_cap)  # noqa: SLF001
            k_177 = gm._kling_cut_layout(_plan(), _plan()["conti"]["cuts"][0], layout, action="looks at Jia " * 60,  # noqa: SLF001
                                         said=lambda limit: 'DIALOGUE (Korean, lip-sync): Minsu: "괜찮아?"', prompt_max=900)
            expect("d-e", [compact in k_small, len(k_small.encode()) <= k_cap, compact in k_177, len(k_177.encode()) <= 900],
                   [True, True, True, True], "실사 Kling 컷(옛 짜임 −30 B · 177 짜임 900 B): 꼬리 짧은 규칙 남음 · 상한 안")
        except Exception as exc:  # noqa: BLE001
            crash("d-e", exc)

        # ================= (d-f) 부정 구절 =================
        try:
            neg = [ln for ln in built["kling_cut"].split("\n") if ln.startswith("NEGATIVE:")]
            neg177 = [ln for ln in built["kling_177"].split("\n") if ln.startswith("NEGATIVE:")]
            words = ("music", "ambient noise", "SFX")  # 211 fix1: 가장 짧은 형
            expect("d-f", [bool(neg) and all(w in neg[0] for w in words), gm.REAL_CUT_NEGATIVE in (neg or [""])[0],
                           bool(neg177) and all(w in neg177[0] for w in words)],
                   [True, True, True], "실사 Kling 컷 NEGATIVE 줄 = 132 구절 + 음악 · 앰비언트 · 효과음")
            supported = "negative" in (inspect.getsource(vfal.extract_caps) + inspect.getsource(vfal.build_payload)).lower()
            seen.add("d-f")
            notes.append("fal 영상 caps 에 부정 프롬프트 필드가 " + ("있다" if supported else "없다 — 부정어는 프롬프트 안 NEGATIVE 줄만"))
        except Exception as exc:  # noqa: BLE001
            crash("d-f", exc)

        # ================= (d-g) 대사 없는 길 = 침묵 =================
        try:
            drop = {"drop": True}
            silent_paths = {"take audioDropped": take(lines_fix=drop), "take_real audioDropped": take(variant="real", lines_fix=drop),
                            "vibe audioDropped": vibe(real=False, lines_fix=drop), "vibe_real audioDropped": vibe(real=True, lines_fix=drop),
                            "take no lines": take(_plan(spoken=False)), "cut no lines": cut(_plan(spoken=False)),
                            "kling no lines": gm._segment_prompt(_plan(spoken=False), {"cutIds": ["c1"]},  # noqa: SLF001
                                                                 first_segment=True, real=True)}
            expect("d-g", {k: [has_full(v) or compact in v, silent in v or silent_c in v] for k, v in silent_paths.items()},
                   {k: [False, True] for k in silent_paths}, "대사 없는 길: 대사 규칙 없음 · 침묵 규칙")
            cut_none = next((ln for ln in silent_paths["cut no lines"].split("\n") if ln.startswith("DIALOGUE:")), "")
            # 211 fix2: 침묵 규칙도 DIALOGUE 줄 밖 맨 끝 자기 줄
            expect("d-g", [cut_none, silent_paths["cut no lines"].split("\n")[-1]],
                   ["DIALOGUE: No one speaks; keep mouths closed.", silent_c], "말 없는 컷 DIALOGUE 줄 · 맨 끝 침묵 규칙 줄")
            soft, _info = rt._soft_prompt(built["cut"])  # noqa: SLF001 — 195: DIALOGUE 줄(규칙 포함)을 뺀다
            soft_take, _i2 = rt._soft_prompt(built["take"])  # noqa: SLF001
            expect("d-g", [compact in soft or has_full(soft), silent_c in soft, has_full(soft_take), silent in soft_take,
                           len(soft.encode()) <= len(built["cut"].encode())],
                   [False, True, False, True, True], "195 순화 다시 내기: 대사를 빼면 침묵 규칙(바이트는 처음 이하)")
            params_on = gm._take_params({"generateAudioField": "generate_audio"}, 6.0, "720p", True)  # noqa: SLF001
            params_off = gm._take_params({"generateAudioField": "generate_audio"}, 6.0, "720p", False)  # noqa: SLF001
            expect("d-g", [params_on.get("audio"), params_off.get("audio")], [True, False], "소리 켬/끔 매개변수는 그 길의 지금 결정 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("d-g", exc)

        # ================= (d-h) ⑦ AI로 고치기 =================
        try:
            take_full = full if full in built["take"] else full_sp  # 211 fix2: 이 계획은 spoken 만 → 속마음 없는 형
            old_take = built["take"].replace(" " + take_full, " " + OLD_FULL)
            old_tiny = "RULES: Reproduce @Video1. No added people, no text. " + OLD_COMPACT
            new_take = gm.dialogue_audio_rewrite(old_take)
            expect("d-h", [new_take == built["take"], gm.dialogue_audio_rewrite(old_tiny).endswith("no text. " + compact),
                           take_full in gm.dialogue_audio_rewrite(old_take, len(old_take.encode())),  # 211 fix1: 원형 ≤ 옛 글
                           gm.dialogue_audio_rewrite(built["vibe"]) == built["vibe"]],
                   [True, True, True, True], "옛 문장 → 원형(옛 글 바이트 이하라 옛 상한 안) · 옛 문장 없으면 그대로")
            rng = {"start": 0.0, "end": 3.0, "in": 0.0, "out": 3.0, "seconds": 3.0, "cutIds": ["c1"]}
            out, _meta = rt._compose_prompt(old_take, built["take"], rng, 6.0, request_ko="표정을 더 밝게",  # noqa: SLF001
                                            extra_ko="", change_en="Make the expression brighter.", max_bytes=12000,
                                            result=True)
            old_vibe = built["vibe"].rsplit("\n", 1)[0]  # 211 전 vibe 글(소리 줄 없음)
            out_v, _mv = rt._compose_prompt(old_vibe, built["vibe"], rng, 6.0, request_ko="표정을 더 밝게",  # noqa: SLF001
                                            extra_ko="", change_en="", max_bytes=12000, result=True)
            expect("d-h", ["natural ambient" in out, has_full(out) or compact in out, "natural ambient" in out_v,
                           compact in out_v, out_v.split("\n").index(compact) < out_v.split("\n").index(rt.COMPOSE_CONTEXT_LINE)],
                   [False, True, False, True, True], "compose: 옛 기준 문장을 바꾸고, 규칙 없는 기준이면 머리에 짧은 규칙 1줄")
            line = "RULES: Keep the room, the light, the chairs, the desk. " + full
            trimmed, cut_n = rt._trim_to_limit(line, len(line.encode()) - 20, [], ["RULES"])  # noqa: SLF001
            expect("d-h", [full in trimmed, cut_n > 0], [True, True], "자르기(_trim_to_limit)는 규칙 조각을 지우지 않는다")
        except Exception as exc:  # noqa: BLE001
            crash("d-h", exc)

        # ================= (d-j) 211 fix1 바이트 중립 =================
        try:
            nb = lambda s: len(s.encode("utf-8"))  # noqa: E731
            expect("d-j", [nb(full) <= nb(OLD_FULL), nb(silent) <= nb(OLD_FULL), nb(compact) <= nb(OLD_COMPACT),
                           nb(silent_c) <= nb(OLD_COMPACT)], [True] * 4, "원형 · 침묵 원형 ≤ 옛 원형 153 B, 짧은 형 ≤ 옛 짧은 형 44 B")
            saved_rule = gm._take_audio_rule  # noqa: SLF001
            rows = {}
            for cap in (None, 600, 1200, 2000):
                for real in (False, True):
                    def _build(old: bool) -> tuple[str, list]:
                        a: dict = {}
                        if old:
                            gm._take_audio_rule = lambda *_a, compact=False, **_k: OLD_COMPACT if compact else OLD_FULL  # noqa: SLF001
                        try:
                            txt = take(variant="real" if real else "webtoon", audit=a,
                                       **({"caps": {"promptMaxLength": cap}} if cap else {}))
                        finally:
                            gm._take_audio_rule = saved_rule  # noqa: SLF001
                        return txt, list(a.get("compact") or [])
                    (new_t, new_c), (old_t, old_c) = _build(False), _build(True)
                    rows[f"{'real' if real else 'anime'} {cap or 'default'}"] = [new_c == old_c, nb(new_t) <= nb(old_t)]
            expect("d-j", rows, {k: [True, True] for k in rows}, "옛 통 테이크: 211 글 = 옛 글과 짧은 형 단계 같음 · 바이트 이하")
        except Exception as exc:  # noqa: BLE001
            crash("d-j", exc)

        # ================= (d-i) LF =================
        try:
            crlf = [f for f in CHANGED if b"\r\n" in (ROOT / f).read_bytes()]
            expect("d-i", crlf, [], "바꾼 파일은 LF 줄 끝")
        except Exception as exc:  # noqa: BLE001
            crash("d-i", exc)
    finally:
        gm._char_by_id = saved_char  # noqa: SLF001

    ok = True
    for group, title in GROUPS.items():
        if group not in seen:
            print(f"[WARN] 작업 211 ({group}) {title} — 돌지 않음")
            continue
        if bad[group]:
            ok = False
            print(f"[FAIL] 작업 211 ({group}) {title}")
            for line in bad[group][:3]:
                print("    " + line[:300])
        else:
            print(f"[ok  ] 작업 211 ({group}) {title}")
    for note in notes:
        print("    " + note)
    return ok


if __name__ == "__main__":
    result = check_dialogue_audio_211()
    print("check_vpoc.py 에 붙일 줄(main, check_pose_controls_209a 다음):")
    print(HOOK)
    sys.exit(0 if result else 1)
