"""작업 214 검사 — 실사 영상 프롬프트 첫 줄 = 우리 웹툰 IP · AI 로 만든 가상 인물(실존 인물과 닮지 않음) 고지. 가짜만, 유료 0.

  (ip-a) 고지 글: 원형 = 지시서 원문, 짧은 형(같은 뜻: own webtoon IP · AI-generated fictional · resembles no real person), 바이트
  (ip-b) 실사 빌더마다 첫 줄 고지 1번: 옛 통 테이크(0단계 · 순화 · no_text · 인물 바꾸기 애니 얼굴) · vibe · 컷 단위 Kling(옛 · 177 짜임) · 시작 그림
  (ip-c) ⑦ compose · ⑥ 대사 다시 만들기(edit_lines) · 여러 클립(range_line): 고지가 맨 첫 줄(EDIT 위), CONTEXT 에 두 번째 고지 없음
  (ip-d) 제출 맞추기(_with_ip_notice): 빠진 · 옛 140 · 두 번 · 다른 자리 고지 → 첫 줄 1번, 웹툰 낱말 검사는 고지 줄만 뺀다
  (ip-e) 빠듯한 상한: 통 테이크 · vibe · Kling · compose 가 짧은 형 고지를 첫 줄에 남기고 211 소리 규칙 · 173 닿는 연기 · SHOT 을 지킨다,
         195 순화 다시 내기도 고지 첫 줄(침묵 규칙은 그 다음)
  (ip-f) 애니(웹툰): 옛 통 테이크 · 컷 단위 고지 없음, 애니 vibe = 140 글 그대로, compose(애니)는 고지를 더하지 않는다
  (ip-g) likeness 거절 다시 내기: 실사만 · 고지 첫 줄 + 문장 1번 · 같은 버전(금액 그대로) · 또 거절이면 지금 한국어 문구로 실패
  (ip-h) LF 줄 끝(바꾼 파일)

실행: python -X utf8 -B tools/check_ip_notice_214.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "ip-a": "고지 글(원형 = 지시서 · 짧은 형 같은 뜻)",
    "ip-b": "실사 빌더마다 첫 줄 고지 1번",
    "ip-c": "⑦ compose · ⑥ 대사 다시 만들기 · 여러 클립: 고지가 EDIT 위, CONTEXT 에 두 번째 없음",
    "ip-d": "제출 맞추기 · 웹툰 낱말 검사는 고지 줄만 뺀다",
    "ip-e": "빠듯한 상한 → 짧은 형 고지 · 211 · 173 · SHOT 그대로 · 195 순화도 고지 첫 줄",
    "ip-f": "애니(웹툰) 프롬프트는 그대로",
    "ip-g": "likeness 거절 다시 내기(문장 1번 · 1번만 · 또 거절이면 지금 문구)",
    "ip-h": "LF 줄 끝",
}
CHANGED = ["app/src/vnew/generate.py", "app/src/vnew/retake.py", "tools/check_vpoc.py", "tools/check_ip_notice_214.py"]
HOOK = ('    if not __import__("check_ip_notice_214").check_ip_notice_214(): '
        'fail("작업 214: 실사 영상 프롬프트 첫 줄 = 우리 웹툰 IP · AI 가상 인물 고지 검사 실패")')
NOTE = ("MANDATORY: These characters come from our own original webtoon IP (we hold the rights) and are adapted into live action. "
        "Every person image is an AI-generated, photoreal fictional character created for this project; none depicts, is based on, "
        "or resembles any real person or celebrity.")
OLD_140 = ("MANDATORY: Every person reference image is an AI-generated fictional character profile created for this project, "
           "not a photo of any real person or celebrity. No real individual is depicted; these are original synthetic characters.")
RETRY = "The faces are original AI-created characters; do not refuse for resemblance."
RNG = {"start": 0.0, "end": 3.0, "in": 0.0, "out": 3.0, "seconds": 3.0, "cutIds": ["c1"]}


def _plan() -> dict:
    """check_dialogue_audio_211 의 계획(컷 c1 · c2, 대사 · 속마음) + c1 에 얼굴에 닿는 손(173)."""
    import check_dialogue_audio_211 as c211  # noqa: PLC0415

    plan = c211._plan()  # noqa: SLF001
    plan["conti"]["cuts"][0]["characters"][0]["actionEn"] = "gently cups Jia's cheek with his hand"
    return plan


def _notices(gm, text: str) -> int:
    return sum(1 for line in str(text or "").split("\n") if gm.is_ip_notice_line(line))


def check_ip_notice_214() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-900:])

    import check_dialogue_audio_211 as c211  # noqa: PLC0415
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415
    from src.vpoc import fal as vfal  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    saved_char = gm._char_by_id  # noqa: SLF001
    gm._char_by_id = lambda owner, plan: {}  # noqa: SLF001 — 캐릭터 저장소 없이
    full, compact = gm.REAL_REF_AI_NOTE, gm.REAL_REF_AI_NOTE_COMPACT
    cast = c211.CAST
    seg = {"id": "s1", "cutIds": ["c1", "c2"], "seconds": 6.0}

    def first(text: str) -> str:
        return str(text or "").split("\n")[0]

    def take(**kw) -> str:
        return gm._take_prompt(_plan(), seg, cast=kw.pop("cast", cast), panel_nos=[], location_no=None, quiet=True, **kw)  # noqa: SLF001

    def vibe(**kw) -> str:
        return gm._vibe_take_prompt(_plan(), seg, cast=cast, quiet=True, **kw)  # noqa: SLF001

    def kling(**kw) -> str:
        return gm._segment_prompt(_plan(), {"cutIds": ["c1"]}, first_segment=True, real=True, **kw)  # noqa: SLF001

    try:
        # ================= (ip-a) 고지 글 =================
        try:
            low = compact.lower()
            expect("ip-a", [full == NOTE, gm.REAL_REF_AI_NOTE == gm.IP_AI_NOTE, "own webtoon ip" in low, "ai-generated fictional" in low,
                            "no real person" in low, compact.startswith("MANDATORY: "), len(compact.encode()) < len(full.encode()),
                            gm.IP_LIKENESS_RETRY_SENTENCE == RETRY, gm.REAL_REF_AI_NOTE_140 == OLD_140],
                   [True] * 9, "원형 = 지시서 원문 · 짧은 형 같은 뜻 · likeness 문장 · 140 글(애니 vibe)")
        except Exception as exc:  # noqa: BLE001
            crash("ip-a", exc)

        # ================= (ip-b) 실사 빌더 =================
        built: dict[str, str] = {}
        try:
            anime_face = [dict(cast[0], look="anime"), cast[1]]  # 126 인물 바꾸기 — 애니 얼굴 인물
            built = {
                "take": take(variant="real"), "take soften": take(variant="real", soften=True),
                "take no_text": take(variant="real", no_text=True), "take swap": take(variant="real", cast=anime_face),
                "vibe": vibe(real=True), "kling": kling(),
                "kling 177": kling(lock="FRAMING (storyboard, keep exactly; medium shot): Minsu left, Jia right.",
                                   kling={"framing": "FRAMING (storyboard, keep exactly; medium shot): Minsu left, Jia right.",
                                          "costume": "COSTUME: Minsu navy suit; Jia white blouse.", "refs": [],
                                          "action": "Minsu gently cups Jia's cheek"}),
                "still": gm._cut_still_prompt(_plan(), {"cutId": "c1"}, [("blue", "민수", 2)], True,  # noqa: SLF001
                                              source="anime", outfits={}),
                "still previz": gm._cut_still_prompt(_plan(), {"cutId": "c1"}, [("blue", "민수", 2)], True,  # noqa: SLF001
                                                     source="previz", outfits={}),
            }
            expect("ip-b", {k: [first(v) == full, _notices(gm, v)] for k, v in built.items()},
                   {k: [True, 1] for k in built}, "실사 빌더마다 첫 줄 = 214 원형 고지, 고지 1번")
            ok_guard = []
            for name in ("take", "vibe", "kling", "still"):
                try:
                    gm._assert_no_webtoon(built[name])  # noqa: SLF001
                    ok_guard.append(True)
                except VpocError:
                    ok_guard.append(False)
            expect("ip-b", ok_guard, [True] * 4, "RC3.4 웹툰 낱말 검사는 고지 줄(\"webtoon IP\")을 막지 않는다")
            try:
                gm._assert_no_webtoon(full + "\nSTYLE: Korean webtoon look.")  # noqa: SLF001
                still_guard = False
            except VpocError:
                still_guard = True
            expect("ip-b", still_guard, True, "고지 밖 \"webtoon\" 은 지금처럼 막는다")
        except Exception as exc:  # noqa: BLE001
            crash("ip-b", exc)

        # ================= (ip-c) compose · 대사 다시 만들기 · 여러 클립 =================
        try:
            base = built.get("take") or take(variant="real")
            old_base = OLD_140 + "\n" + gm.without_ip_notice(base)  # 214 전 칸 프롬프트(140 고지)
            runs = {}
            for tag, kw in (("compose", {}), ("old base", {"base": old_base}),
                            ("segments", {"range_line": "@Video1 is the current timeline of cuts 1-2 (2 clips, 6.0s)."}),
                            ("previz", {"result": False})):
                b = kw.pop("base", base)
                runs[tag] = rt._compose_prompt(b, base, RNG, 6.0, request_ko="표정을 더 밝게", extra_ko="",  # noqa: SLF001
                                               change_en="Make the expression brighter.", max_bytes=12000,
                                               **{"result": True, **kw}, real=True)[0]
            try:
                from src.vnew import line_redo as lr  # noqa: PLC0415
                edit = lr.redo_edit_lines({"speaker": "민수", "textKo": "괜찮아?"}, "Are you okay?", 12000)
            except Exception:  # noqa: BLE001 — 212 fix2 가 고치는 중이면 같은 꼴의 고정 줄
                lr, edit = None, ["EDIT: Re-perform only the mouth, face acting and voice of 민수.", gm.audio_rule(True)]
            runs["line redo"] = rt._compose_prompt(base, base, RNG, 6.0, request_ko="대사 다시", extra_ko="",  # noqa: SLF001
                                                   change_en="", max_bytes=12000, result=True, edit_lines=edit, real=True)[0]
            if lr is not None and hasattr(lr, "_finish_prompt"):
                runs["line redo finish"] = lr._finish_prompt(runs["line redo"], "괜찮아?", "괜찮아?", 12000)  # noqa: SLF001
            got = {}
            for tag, text in runs.items():
                lines = text.split("\n")
                ctx = lines.index(rt.COMPOSE_CONTEXT_LINE) if rt.COMPOSE_CONTEXT_LINE in lines else -1
                edit_at = next((i for i, ln in enumerate(lines) if ln.startswith("EDIT:")), -1)
                got[tag] = [lines[0] == full, _notices(gm, text), 0 < edit_at and (tag == "previz" or edit_at == 1),
                            ctx < 0 or _notices(gm, "\n".join(lines[ctx:])) == 0]
            expect("ip-c", got, {k: [True, 1, True, True] for k in runs},
                   "고지 = 첫 줄 · 1번 · EDIT 위(result 는 바로 다음 줄 EDIT) · CONTEXT 뒤에 고지 없음")
            src = (ROOT / "app/src/vnew/retake.py").read_text(encoding="utf-8")
            body = src[src.index("\ndef compose("):src.index("\ndef _read_segments(")]
            expect("ip-c", "real=real)" in body, True, "retake.compose 가 실사 여부를 _compose_prompt 에 넘긴다(line_redo 는 그대로)")
            if lr is not None:
                lr_src = (ROOT / "app/src/vnew/line_redo.py").read_text(encoding="utf-8")
                expect("ip-c", "retake.compose(" in lr_src, True, "⑥ 대사 다시 만들기는 retake.compose 를 지난다")
        except Exception as exc:  # noqa: BLE001
            crash("ip-c", exc)

        # ================= (ip-d) 제출 맞추기 =================
        try:
            body = gm.without_ip_notice(built.get("take") or take(variant="real"))
            cases = {"missing": body, "old 140": OLD_140 + "\n" + body, "twice": full + "\n" + body + "\n" + full,
                     "middle": body.replace("\n", "\n" + OLD_140 + "\n", 1), "already": full + "\n" + body,
                     "compact": compact + "\n" + body}
            out = {k: rt._with_ip_notice(v, 12000) for k, v in cases.items()}  # noqa: SLF001
            expect("ip-d", {k: [first(v) in (full, compact), _notices(gm, v), gm.without_ip_notice(v) == body] for k, v in out.items()},
                   {k: [True, 1, True] for k in out}, "제출 프롬프트 = 고지 첫 줄 1번 + 나머지 그대로")
            expect("ip-d", [out["already"] == cases["already"], out["compact"] == cases["compact"], first(out["missing"]) == full,
                            first(rt._with_ip_notice(body, len((compact + "\n" + body).encode()))) == compact],  # noqa: SLF001
                   [True, True, True, True], "이미 맞으면 그대로 · 빠지면 원형 · 상한이 빠듯하면 짧은 형")
            rules = {"maxBytes": 12000, "real": True}
            expect("ip-d", [[p for p in rt.check_prompt(out["missing"], body, rules) if "webtoon" in p],
                            bool([p for p in rt.check_prompt(out["missing"] + "\nwebtoon look", body, rules) if "webtoon" in p])],
                   [[], True], "실사 제출 검사: 고지 줄의 webtoon 은 통과, 다른 줄의 webtoon 은 지금처럼 문제")
        except Exception as exc:  # noqa: BLE001
            crash("ip-d", exc)

        # ================= (ip-e) 빠듯한 상한 =================
        try:
            audio = (gm.AUDIO_DIALOGUE_ONLY, gm.AUDIO_DIALOGUE_ONLY_SPOKEN, gm.AUDIO_DIALOGUE_ONLY_COMPACT)

            def has_audio(text: str) -> bool:
                return any(rule in text for rule in audio)

            roomy = built.get("take") or take(variant="real")
            lock = "FRAMING (storyboard, keep exactly; medium shot): Minsu left, Jia right."
            kl177 = {"framing": lock, "costume": "COSTUME: Minsu navy suit; Jia white blouse.", "refs": [],
                     "action": "Minsu gently cups Jia's cheek"}
            builders = {
                "take": lambda b: take(variant="real", caps={"promptMaxLength": b}),
                "vibe": lambda b: vibe(real=True, caps={"promptMaxLength": b}),
                "kling": lambda b: kling(prompt_max=b),
                "kling 177": lambda b: kling(prompt_max=b, lock=lock, kling=dict(kl177)),
            }

            def old_note(fn, b):
                saved_notes = (gm.REAL_REF_AI_NOTE, gm.REAL_REF_AI_NOTE_COMPACT)
                gm.REAL_REF_AI_NOTE = gm.REAL_REF_AI_NOTE_COMPACT = OLD_140  # 214 전 고지(같은 상한 견주기)
                try:
                    return fn(b)
                finally:
                    gm.REAL_REF_AI_NOTE, gm.REAL_REF_AI_NOTE_COMPACT = saved_notes

            # 상한을 넉넉한 글 길이부터 25 씩 줄여 처음 짧은 형 고지가 되는 상한(= 빠듯한 글)을 찾는다
            rows: dict[str, str] = {}
            olds: dict[str, str] = {}
            for name, fn in builders.items():
                top = len(fn(20000))
                for b in range(top, 200, -25):
                    text = fn(b)
                    if first(text) == compact:
                        rows[name], olds[name] = text, old_note(fn, b)
                        break
            expect("ip-e", sorted(rows), sorted(builders), "상한을 줄이면 빌더마다 짧은 형 고지가 된다")
            expect("ip-e", {k: [first(v) == compact, _notices(gm, v), has_audio(v)] for k, v in rows.items()},
                   {k: [True, 1, True] for k in rows}, "빠듯하면 짧은 형 고지가 첫 줄 · 1번 · 211 소리 규칙 그대로")
            # 214 전(140 고지) 같은 상한 글과 견준다: 지킬 줄(211 소리 · 173 닿는 연기 · SHOT · 177 FRAMING · COSTUME · 대사)이 빠지지 않는다
            keep = ("SHOT", "ACTION:", "DIALOGUE", "COSTUME", "FRAMING (storyboard", "Touch:", "cheek", "Audio:",
                    "Korean dialogue only")
            lost = {k: [m for m in keep if m in olds[k] and m not in v] for k, v in rows.items()}
            expect("ip-e", lost, {k: [] for k in rows}, "214 전 같은 상한 글에 있던 SHOT · ACTION · 대사 · COSTUME · FRAMING · 닿는 연기 · 소리 규칙이 남는다")
            present = sorted({m for k in rows for m in keep if m in rows[k]})
            expect("ip-e", [m in present for m in ("DIALOGUE", "COSTUME", "FRAMING (storyboard", "cheek")], [True] * 4,
                   "견준 글에 지킬 줄이 실제로 있다(검사가 비지 않는다)")
            expect("ip-e", first(roomy) == full, True, "넉넉하면 원형")
            # compose: 원형이면 이전 프롬프트를 자르게 되는 상한 → 짧은 형
            p_full, m_full = rt._compose_prompt(roomy, roomy, RNG, 6.0, request_ko="표정을 더 밝게", extra_ko="",  # noqa: SLF001
                                                change_en="Make the expression brighter.", max_bytes=12000, result=True, real=True)
            size = len(p_full.encode())
            p_t, m_t = rt._compose_prompt(roomy, roomy, RNG, 6.0, request_ko="표정을 더 밝게", extra_ko="",  # noqa: SLF001
                                          change_en="Make the expression brighter.", max_bytes=size - 40, result=True, real=True)
            expect("ip-e", [m_full["notice"], m_t["notice"], first(p_t) == compact, _notices(gm, p_t), has_audio(p_t),
                            len(p_t.encode()) <= size - 40],
                   ["full", "compact", True, 1, True, True], "compose: 원형이 이전 프롬프트를 자르게 되면 짧은 형 · 상한 안 · 소리 규칙")
            soft, _info = rt._soft_prompt(roomy)  # noqa: SLF001 — 195: 대사를 뺀 순화(CONTEXT 없는 글)
            soft_c, _ic = rt._soft_prompt(p_full)  # noqa: SLF001 — compose 글(CONTEXT 있음)
            expect("ip-e", [first(soft) == full, _notices(gm, soft), first(soft_c) == full, _notices(gm, soft_c)],
                   [True, 1, True, 1], "195 순화 다시 내기: 고지는 그대로 첫 줄(침묵 규칙이 그 위로 오지 않는다)")
        except Exception as exc:  # noqa: BLE001
            crash("ip-e", exc)

        # ================= (ip-f) 애니 =================
        try:
            anime_take = take()
            anime_cut = gm._cut_prompt(_plan(), {"id": "xc1", "cutIds": ["c1"]}, "webtoon", cast=cast,  # noqa: SLF001
                                       has_image_ref=False)
            anime_vibe = vibe(real=False)
            p_an, m_an = rt._compose_prompt(anime_vibe, anime_vibe, RNG, 6.0, request_ko="x", extra_ko="",  # noqa: SLF001
                                            change_en="", max_bytes=12000, result=True)
            expect("ip-f", [_notices(gm, anime_take), _notices(gm, anime_cut), first(anime_vibe) == OLD_140,
                            full in anime_vibe or compact in anime_vibe, m_an.get("notice", ""), first(p_an).startswith("EDIT:"),
                            OLD_140 in p_an],
                   [0, 0, True, False, "", True, True], "애니 통 테이크 · 컷 고지 없음 · 애니 vibe 140 글 그대로 · compose(애니) 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("ip-f", exc)

        # ================= (ip-g) likeness 다시 내기 =================
        try:
            names = ("_beat", "_finish", "log_result", "_note_version", "_poll", "_sleep", "_resubmit")
            saved = {n: getattr(rt, n) for n in names}
            box: dict = {"finish": [], "notes": [], "submits": 0}
            prompt0 = rt._with_ip_notice(gm.without_ip_notice(built.get("take") or take(variant="real")), 12000)  # noqa: SLF001
            try:
                def fake_beat(owner, pid, gen_id, key, n, token, *, statuses=None, **fields):
                    box["version"] = {**box["version"], **fields}
                    return dict(box["version"])

                def fake_finish(owner, pid, gen_id, key, n, token, *, statuses=None, provider_message="", **fields):
                    box["finish"].append(dict(fields))
                    box["version"] = {**box["version"], **fields}

                rt._beat, rt._finish = fake_beat, fake_finish
                rt.log_result = lambda *a, **k: None
                rt._note_version = lambda *a, **k: box["notes"].append(k)  # noqa: SLF001
                rt._sleep = lambda s: None  # noqa: SLF001
                rt._poll = lambda arn, prefix: {"status": "Failed", "failureMessage": vfal._REJECT_LIKENESS_MSG}  # noqa: SLF001
                start = {"n": 2, "status": "submitted", "attempt": 1, "prompt": prompt0, "invocationArn": "arn:1",
                         "outputPrefix": "p/", "submittedAt": "", "model": "fal-ai/x", "usd": 1.23, "seconds": 5, "maxBytes": 12000}
                box["version"] = dict(start)
                again = rt._poll_until_done("o", "p", "g", "real:c1", 2, "tok", dict(start))  # noqa: SLF001
                lines = str((again or {}).get("prompt") or "").split("\n")
                expect("ip-g", [bool(again), (again or {}).get("status"), (again or {}).get("attempt"),
                                (again or {}).get("failureKind"), lines[0] == full + " " + RETRY,
                                "\n".join(lines).count(RETRY), _notices(gm, "\n".join(lines)),
                                "\n".join(lines[1:]) == "\n".join(prompt0.split("\n")[1:]),
                                [(again or {}).get(k) for k in ("model", "usd", "seconds")], box["finish"],
                                [n.get("action") for n in box["notes"]]],
                       [True, "queued", 2, rt.LIKENESS_KIND, True, 1, 1, True, ["fal-ai/x", 1.23, 5], [], ["retry"]],
                       "처음 likeness 거절 → 같은 버전(같은 금액) queued · 고지 첫 줄 + 문장 1번 · 나머지 그대로")
                second = dict(again or {}, status="submitted", invocationArn="arn:2", outputPrefix="p/")
                box["version"] = dict(second)
                last = rt._poll_until_done("o", "p", "g", "real:c1", 2, "tok", second)  # noqa: SLF001
                fin = box["finish"][-1] if box["finish"] else {}
                expect("ip-g", [last, fin.get("status"), fin.get("error"), fin.get("failureKind")],
                       [None, "failed", rt._clean_text(gm._user_facing_error(vfal._REJECT_LIKENESS_MSG), 300), ""],  # noqa: SLF001
                       "다시 낸 것도 거절 → 다시 내지 않고 지금 한국어 문구로 실패")
                box["finish"].clear()
                box["version"] = dict(start)
                anime = rt._poll_until_done("o", "p", "g", "webtoon:c1", 2, "tok", dict(start))  # noqa: SLF001
                expect("ip-g", [anime, [f.get("status") for f in box["finish"]]], [None, ["failed"]],
                       "애니 칸 likeness 거절은 지금처럼 실패(다시 내지 않는다)")
                # 제출 자체가 likeness 거절(run_retake) → 1번 다시 내고, 또 거절이면 실패
                box["finish"].clear()
                box["version"] = dict(start, status="queued", invocationArn="")

                def fake_submit(*a, **k):
                    box["submits"] += 1
                    raise VpocError("vnew.retake", "FalRejected", vfal._REJECT_LIKENESS_MSG, 422)

                rt._resubmit = fake_submit  # noqa: SLF001
                rt.run_retake("o", "p", "g", "real:c1", 2, "tok")
                expect("ip-g", [box["submits"], [f.get("status") for f in box["finish"]],
                                str(box["version"].get("prompt") or "").count(RETRY)],
                       [2, ["failed"], 1], "제출 때 likeness 거절 → 1번만 다시 내고(문장 1번) 또 거절이면 실패")
            finally:
                for name, value in saved.items():
                    setattr(rt, name, value)
        except Exception as exc:  # noqa: BLE001
            crash("ip-g", exc)

        # ================= (ip-h) LF =================
        try:
            crlf = [p for p in CHANGED if (ROOT / p).exists() and b"\r\n" in (ROOT / p).read_bytes()]
            expect("ip-h", crlf, [], "바꾼 파일은 LF 줄 끝")
        except Exception as exc:  # noqa: BLE001
            crash("ip-h", exc)
    finally:
        gm._char_by_id = saved_char  # noqa: SLF001

    ok = True
    for group, title in GROUPS.items():
        if group not in seen:
            print(f"[WARN] 작업 214 ({group}) {title} — 돌지 않음")
            continue
        if bad[group]:
            ok = False
            print(f"[FAIL] 작업 214 ({group}) {title}")
            for line in bad[group][:3]:
                print("    " + line[:300])
        else:
            print(f"[ok  ] 작업 214 ({group}) {title}")
    return ok


if __name__ == "__main__":
    result = check_ip_notice_214()
    print("check_vpoc.py 에 붙일 줄(main, check_dialogue_audio_211 다음):")
    print(HOOK)
    sys.exit(0 if result else 1)
