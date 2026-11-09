"""vnew ⑤ 촬영 지시서(shot brief) — 프리비즈 → 영상 제작으로 넘어가는 단계(설계자 2026-10-22).

프리비즈 승인 직후, 영상 모델에게 **학습한 촬영 전문 언어**로 각 컷을 지시하는 "촬영 지시서"를 만든다.
영상 모델은 똑똑하지 않으므로, 샷 크기(ECU/CU/MCU…)·렌즈 mm·앵글·정면성·샷 종류·라인 쪽처럼 표준
영화 용어로, 그리고 누가 화면 어디에서 무엇을 하는지(손·머리·눈·표정·타이밍)·보이는 배경·빛을 구체적
으로 쓴다. `.kiro/steering/camera-grammar.md` 의 문법을 그대로 쓴다.

흐름:
- previz.approve 성공 시 start_brief 를 자동으로 부른다(사용자가 [촬영 지시서 다시 만들기]로도 호출).
- run_brief(백그라운드): 장소(장면)마다 1회 Astra(실패 시 Claude)를 불러 그 장소 컷들을 한꺼번에 쓴다.
- 결과: previz["shotBrief"] = {status, model, scene:{locationId: sceneEn}, cuts:{cutId: brief}, updatedAt}.
  brief = {shotEn, blockingEn, actionEn, moodEn, backgroundEn, dialogue:[{speaker, lineKo, deliveryEn}], pvSig}.
- 작업 206: 다시 만들 때 pvSig(컷 입력 지문)가 같은 컷 항목·장소 문장은 그대로 두고 바뀐 컷만 새로 쓴다(changedCuts).

재사용(수정 금지): vpoc.llm(_converse) · vpoc.ledger · vnew.previz(_scene_groups·_location_for·_spawn·
task/progress helpers) · vnew.generate(_cut_lines·_color_name) · vnew.previz_schema. 외부 LLM 호출은
모듈 함수 _brief(blocks, model=...) 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc.errors import VpocError, now_iso

from . import generate as gen_mod
from . import previz as previz_mod
from . import settings as vnew_settings

log = logging.getLogger("vpoc")

BRIEF_MODEL = vnew_settings.BRIEF_MODEL
BRIEF_FALLBACK_MODEL = vnew_settings.BRIEF_FALLBACK_MODEL
BRIEF_STALL_S = vnew_settings.BRIEF_STALL_S
# 작업 206 fix 1: 지시서 작성이 끝났는데 그새 바뀐 컷(brief_outdated_cuts)이 있으면 곧바로 1번 더 돈다(따라잡기).
# 승인(또는 [다시 만들기]·영상 시작) 1번당 이어 도는 따라잡기는 최대 이만큼. 지시서 문서 catchUp 에 센다.
BRIEF_CATCH_UP_MAX = 2


# ================================================================ 시스템·도구(Astra/Claude)

_BRIEF_SYSTEM = (
    "You are a cinematographer and first AD writing shot briefs for an image/video model that is NOT "
    "intelligent. Use standard film terminology (shot size: ECU/CU/MCU/MS/MLS/FS/LS, lens mm, "
    "eye-level/high/low angle, front/three-quarter/profile, clean/dirty single, over-the-shoulder, "
    "two-shot, line of action, static/dolly/push-in). Be literal and visual: name who is where in the "
    "frame (frame left/right/center, foreground/background), what each hand, head and eye does, facial "
    "expression, and timing within the shot. Describe the background that is actually visible in this "
    "framing (materials, colors, objects, depth, focus/bokeh) and the light (source, direction, quality, "
    "color temperature, time of day, weather). No metaphors, no story recap, no names of people who are "
    "not in the shot. "
    "Mannequin colors (blue, orange, …) are only identification placeholders in the previz; never "
    "describe clothing, skin or hair by mannequin color — use the character descriptions given. "
    "Never use internal labels such as 'side +1', 'side -1', 'crossed side', 'line side'; instead state "
    "screen positions explicitly (e.g. 'Ui-jun is frame right facing screen left'). sceneEn must "
    "describe only the place, weather, time and light — no people, no clothing, no camera rules. "
    "When a person speaks to someone, keep their eyes on the listener's face for the whole line unless "
    "the panel shows otherwise. Do not invent architecture (stairs, mezzanines, walls, ceilings) that "
    "is not in the location references. No meaningless camera shake or excessive rotation; keep each "
    "cut's camera as the previz cut. "
    "For a dialogue line marked 속마음 (inner thought), write its deliveryEn as facial and eye acting only with the "
    "mouth closed and lips not moving \u2014 never as speaking, shouting or whispering. "
    "Answer ONLY by calling emit_brief exactly once, writing every cut given to you."
)

_BRIEF_TOOL = {
    "name": "emit_brief",
    "description": "Write a shot brief for one location and all of its cuts, in film terminology.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                # 장소 공통(400~700자): 공간·재질·색·소품·날씨·시간·빛 방향과 질·전체 색감·공기감.
                "sceneEn": {"type": "string"},
                "cuts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cutId": {"type": "string"},
                            "shotEn": {"type": "string"},        # 카메라(샷 크기·렌즈·높이·앵글·정면성·샷 종류·라인 쪽·움직임)
                            "blockingEn": {"type": "string"},    # 누가 화면 어디·몸 방향·시선
                            "actionEn": {"type": "string"},      # 손·머리·눈·표정·타이밍
                            "moodEn": {"type": "string"},        # 감정·분위기·연기 톤
                            "backgroundEn": {"type": "string"},  # 이 프레이밍에서 보이는 배경 부분·초점
                            "dialogue": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "speaker": {"type": "string"},
                                        "lineKo": {"type": "string"},
                                        "deliveryEn": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "required": ["cutId"],
                    },
                },
            },
            "required": ["sceneEn", "cuts"],
        }
    },
}


def _brief(blocks: list[dict], *, model: str):
    """llm._converse 로 촬영 지시서 JSON 을 받는다. (payload, usage{..model}). 검사가 가짜로 바꾼다."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_BRIEF_SYSTEM,
        blocks=blocks,
        tool=_BRIEF_TOOL,
        max_tokens=6000,
        where="vnew.shot_brief",
        model=model,
    )


def _brief_location(owner: str, pid: str, plan_id: str, blocks: list[dict]) -> tuple[dict, str]:
    """Astra 먼저, 실패하면 Claude. 사용량을 원장에 남긴다. (payload, model)."""
    for model in (BRIEF_MODEL, BRIEF_FALLBACK_MODEL):
        try:
            payload, usage = _brief(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.shot_brief.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        used = str(usage.get("model") or model)
        _charge(owner, pid, plan_id, usage, used)
        return (payload if isinstance(payload, dict) else {}), used
    raise VpocError("vnew.shot_brief", "BriefFailed", "촬영 지시서 작성에 실패했습니다.", 502)


def _charge(owner: str, pid: str, plan_id: str, usage: dict, model: str) -> None:
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        vpoc_ledger.charge_text(
            "vnew-brief-" + secrets.token_hex(6),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=plan_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.shot_brief.charge_failed err=%s", str(exc)[:200])


# ================================================================ task 상태


def _empty_brief() -> dict:
    return {
        "status": "none",  # none|running|ready|failed
        "model": "",
        "scene": {},       # locationId → sceneEn
        "cuts": {},        # cutId → brief
        "error": "",
        "task": {"status": "idle", "startedAt": None, "id": None},
        "updatedAt": "",
    }


def _task_stale(task: dict) -> bool:
    if (task or {}).get("status") != "running":
        return False
    started = previz_mod._parse_iso((task or {}).get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > BRIEF_STALL_S


# ================================================================ 시작 · 백그라운드


def start_brief(owner: str, pid: str, *, catch_up: bool = False) -> str:
    """촬영 지시서 작성을 시작한다(진행 중이면 409). 프리비즈가 승인돼 있어야 한다. task.id 반환.

    previz.approve 가 성공한 직후 자동으로 부르고, [촬영 지시서 다시 만들기](POST /previz/brief)로도
    부른다. 이전 결과는 지우지 않는다(새 작성이 끝날 때 교체, 실패하면 이전 결과 유지).
    작업 206 fix 1: 보통 시작은 따라잡기 횟수(catchUp)를 0 으로 되돌린다 — 진행 중이라 409 여도 되돌려 둔다(그 작업이
    끝나면 새 승인 몫으로 따라잡는다). catch_up=True 는 _commit 뒤 따라잡기: catchUp 이 BRIEF_CATCH_UP_MAX 면 409.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    new_id = secrets.token_hex(8)
    busy = {"reset": False}

    def mutator(data: dict) -> None:
        previz = data.get("previz") or {}
        if not previz.get("approved"):
            raise VpocError("vnew.shot_brief", "NotApproved", "먼저 프리비즈를 승인해 주세요.", 409)
        if not (previz.get("scenes")):
            raise VpocError("vnew.shot_brief", "NoScenes", "먼저 장면을 설계해 주세요.", 409)
        brief = previz.get("shotBrief") or _empty_brief()
        task = brief.get("task") or {}
        running = task.get("status") == "running" and not _task_stale(task)
        count = int(brief.get("catchUp") or 0)
        if running and not catch_up and count:
            brief["catchUp"] = 0  # 진행 중이어도 이번 승인 몫으로 따라잡을 수 있게 되돌려 둔다(저장 뒤 409).
            busy["reset"] = True
            previz["shotBrief"] = brief
            data["previz"] = previz
            return
        if running:
            raise VpocError("vnew.shot_brief", "Briefing", "이미 촬영 지시서를 만들고 있습니다.", 409)
        if catch_up and count >= BRIEF_CATCH_UP_MAX:
            raise VpocError("vnew.shot_brief", "CatchUpLimit", "촬영 지시서 따라잡기 횟수를 넘었습니다.", 409)
        brief["catchUp"] = count + 1 if catch_up else 0
        brief["status"] = "running"
        brief["error"] = ""
        brief["task"] = {"status": "running", "startedAt": now_iso(), "id": new_id}
        previz["shotBrief"] = brief
        data["previz"] = previz

    plan_mod.mutate_plan(owner, pid, mutator)
    if busy["reset"]:
        raise VpocError("vnew.shot_brief", "Briefing", "이미 촬영 지시서를 만들고 있습니다.", 409)
    previz_mod._spawn(run_brief, owner, pid, new_id, name=f"vnew-brief-{pid}")
    return new_id


def start_brief_safe(owner: str, pid: str) -> None:
    """approve 훅 등에서 부르는 안전 버전(진행 중 409 등은 조용히 무시)."""
    try:
        start_brief(owner, pid)
    except VpocError as exc:
        log.info("vnew.shot_brief.start_skipped pid=%s kind=%s", pid, exc.kind)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.shot_brief.start_failed pid=%s err=%s", pid, str(exc)[:200])


def brief_outdated_cuts(plan: dict, *, owner: str = "", char_by_id: dict | None = None) -> list[str]:
    """작업 206 fix 1: 지시서 컷 항목의 pvSig 가 지금 지문과 다른 컷 id(콘티 순서). pvSig 없는 항목(옛 지시서)·항목 없는
    컷은 세지 않는다. 지문은 run_brief 와 같게 캐릭터 데이터를 쓴다 — owner(또는 char_by_id)를 주어야 한다."""
    previz = plan.get("previz") or {}
    brief = previz.get("shotBrief") or {}
    cuts = brief.get("cuts") if isinstance(brief.get("cuts"), dict) else {}
    if not any(isinstance(item, dict) and item.get("pvSig") for item in cuts.values()):
        return []
    if char_by_id is None:
        char_by_id = gen_mod._char_by_id(owner, plan) if owner else {}
    out: list[str] = []
    for group in previz_mod._scene_groups(plan.get("conti") or {}):
        name_en = str(group.get("locationEn") or "")
        loc_sig = _location_sig(plan, name_en, previz_mod._location_for(plan, name_en))
        for c in group["cuts"]:
            cut_id = str(c.get("cutId") or "")
            entry = cuts.get(cut_id)
            if not isinstance(entry, dict) or not entry.get("pvSig"):
                continue
            if entry["pvSig"] != cut_pv_sig(plan, cut_id, loc_sig, char_by_id):
                out.append(cut_id)
    return out


def _catch_up(owner: str, pid: str) -> None:
    """작업 206 fix 1: 지시서를 다 쓴 뒤 계획을 다시 읽어, 그새 바뀐 컷이 있으면 1번 더 돈다(최대 BRIEF_CATCH_UP_MAX)."""
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        outdated = brief_outdated_cuts(plan, owner=owner)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.shot_brief.catch_up_check_failed pid=%s err=%s", pid, str(exc)[:200])
        return
    if not outdated:
        return
    try:
        task_id = start_brief(owner, pid, catch_up=True)
        log.info("vnew.shot_brief.catch_up pid=%s task=%s cuts=%s", pid, task_id, ",".join(outdated)[:300])
    except VpocError as exc:
        level = logging.WARNING if exc.kind == "CatchUpLimit" else logging.INFO
        log.log(level, "vnew.shot_brief.catch_up_skipped pid=%s kind=%s cuts=%s", pid, exc.kind,
                ",".join(outdated)[:300])
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.shot_brief.catch_up_failed pid=%s err=%s", pid, str(exc)[:200])


def run_brief(owner: str, pid: str, task_id: str) -> None:
    """촬영 지시서 작성(백그라운드). 장소마다 1회 호출해 그 장소 컷들을 한꺼번에 쓴다."""
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        conti = plan.get("conti") or {}
        groups = previz_mod._scene_groups(conti)
        if not groups:
            _fail(owner, pid, task_id, "콘티 컷이 없습니다.")
            return
        plan_id = str(plan.get("planId") or "")

        # 작업 206: 이미 지시서가 있으면(컷 항목에 pvSig) 프리비즈가 바뀐 컷·항목 없는 컷만 새로 쓴다.
        # 나머지 컷 항목과 장소 문장은 그대로 둔다(바이트 그대로). pvSig 가 하나도 없는 옛 지시서는 지금처럼 전부 새로.
        old_brief = (plan.get("previz") or {}).get("shotBrief") or {}
        old_cuts = old_brief.get("cuts") if isinstance(old_brief.get("cuts"), dict) else {}
        old_scene = old_brief.get("scene") if isinstance(old_brief.get("scene"), dict) else {}
        keep = any(isinstance(item, dict) and item.get("pvSig") for item in old_cuts.values())
        scene_map: dict[str, str] = dict(old_scene) if keep else {}
        cuts_map: dict[str, dict] = dict(old_cuts) if keep else {}
        used_model = str(old_brief.get("model") or "") if keep else ""
        char_by_id = gen_mod._char_by_id(owner, plan)
        changed_all: list[str] = []
        for group in groups:
            name_en = str(group.get("locationEn") or "")
            loc = previz_mod._location_for(plan, name_en)
            location_id = previz_mod._location_id_for(loc, name_en)
            loc_sig = _location_sig(plan, name_en, loc)
            sigs = {str(c.get("cutId") or ""): cut_pv_sig(plan, str(c.get("cutId") or ""), loc_sig, char_by_id)
                    for c in group["cuts"]}
            changed = [cid for cid, sig in sigs.items()
                       if not keep or not isinstance(old_cuts.get(cid), dict) or old_cuts[cid].get("pvSig") != sig]
            if not changed:
                continue
            changed_all.extend(changed)
            blocks = [{"text": _location_prompt(plan, owner, name_en, loc, group["cuts"])}]
            payload, model = _brief_location(owner, pid, plan_id, blocks)
            used_model = model or used_model
            scene_en = str(payload.get("sceneEn") or "").strip()
            # 장소 문장: 그 장소 컷이 전부 바뀌었거나 아직 없을 때만 바꾼다.
            if scene_en and (not keep or len(changed) == len(sigs) or not scene_map.get(location_id)):
                # 저장 전 정리: sceneEn 은 장소만 — 인물 이름·"wears" 문장과 내부 라벨 문장을 지운다.
                scene_en = _strip_internal_labels(scene_en, where=f"scene {location_id}")
                scene_en = _strip_scene_people(scene_en, _person_names(plan), where=f"scene {location_id}")
                if scene_en:
                    scene_map[location_id] = scene_en
            for item in payload.get("cuts") or []:
                if not isinstance(item, dict):
                    continue
                cut_id = str(item.get("cutId") or "").strip()
                if not cut_id or cut_id not in changed:
                    continue  # 안 바뀐 컷은 문맥으로만 보냈다 — 결과는 버린다.
                entry = _sanitize_cut_brief(_normalize_cut_brief(item), cut_id=cut_id)
                entry["pvSig"] = sigs[cut_id]
                cuts_map[cut_id] = entry

        log.info("vnew.shot_brief.run pid=%s keep=%s changed=%s", pid, "on" if keep else "off",
                 ",".join(changed_all)[:300] or "-")
        _commit(owner, pid, task_id, scene_map, cuts_map, used_model, changed_all)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.shot_brief.run_failed pid=%s err=%s", pid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail(owner, pid, task_id, message)


def _normalize_cut_brief(item: dict) -> dict:
    dialogue = []
    for d in item.get("dialogue") or []:
        if not isinstance(d, dict):
            continue
        dialogue.append(
            {
                "speaker": str(d.get("speaker") or "").strip(),
                "lineKo": str(d.get("lineKo") or "").strip(),
                "deliveryEn": str(d.get("deliveryEn") or "").strip(),
            }
        )
    return {
        "shotEn": str(item.get("shotEn") or "").strip(),
        "blockingEn": str(item.get("blockingEn") or "").strip(),
        "actionEn": str(item.get("actionEn") or "").strip(),
        "moodEn": str(item.get("moodEn") or "").strip(),
        "backgroundEn": str(item.get("backgroundEn") or "").strip(),
        "dialogue": dialogue,
    }


# 저장 전 정리(sanitize) — 내부 용어·장소 문단 규칙 위반을 지운다(설계자 2026-10-22).
# 내부 라벨("side +1"/"side -1"/"crossed side"/"line side")이 든 문장은 어느 필드든 지운다.
# sceneEn 은 장소만 묘사해야 하므로 인물 이름·"wears" 가 든 문장도 지운다.
_INTERNAL_LABEL_RE_SRC = r"side\s*[+-]?1|crossed side|line side"


def _split_sentences_sb(text: str) -> list[str]:
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return []
    parts = _re.split(r"(?<=[.!?])\s+", body)
    return [p.strip() for p in parts if p.strip()]


def _strip_internal_labels(text: str, *, where: str = "") -> str:
    """내부 라벨이 든 문장을 지운다. 지운 게 있으면 로그로 남긴다."""
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return ""
    pattern = _re.compile(_INTERNAL_LABEL_RE_SRC, _re.IGNORECASE)
    kept, dropped = [], []
    for sentence in _split_sentences_sb(body):
        if pattern.search(sentence):
            dropped.append(sentence)
        else:
            kept.append(sentence)
    if dropped:
        log.info("vnew.shot_brief.sanitize_label where=%s dropped=%s", where, " | ".join(dropped)[:300])
    return " ".join(kept).strip()


def _strip_scene_people(text: str, names: list[str], *, where: str = "scene") -> str:
    """sceneEn 에서 인물 이름·'wears' 가 든 문장을 지운다(장소만 묘사). 로그로 남긴다."""
    import re as _re  # noqa: PLC0415

    body = str(text or "").strip()
    if not body:
        return ""
    name_patterns = []
    for name in names:
        n = str(name or "").strip()
        if n:
            name_patterns.append(_re.compile(r"\b" + _re.escape(n) + r"\b", _re.IGNORECASE))
    wears = _re.compile(r"\bwears?\b", _re.IGNORECASE)
    kept, dropped = [], []
    for sentence in _split_sentences_sb(body):
        has_name = any(p.search(sentence) for p in name_patterns)
        if has_name or wears.search(sentence):
            dropped.append(sentence)
        else:
            kept.append(sentence)
    if dropped:
        log.info("vnew.shot_brief.sanitize_scene where=%s dropped=%s", where, " | ".join(dropped)[:300])
    return " ".join(kept).strip()


def _sanitize_cut_brief(brief: dict, *, cut_id: str = "") -> dict:
    """컷 brief 의 각 텍스트 필드에서 내부 라벨 문장을 지운다(dialogue.deliveryEn 포함)."""
    out = dict(brief)
    for field in ("shotEn", "blockingEn", "actionEn", "moodEn", "backgroundEn"):
        out[field] = _strip_internal_labels(out.get(field) or "", where=f"cut {cut_id} {field}")
    dialogue = []
    for d in out.get("dialogue") or []:
        if not isinstance(d, dict):
            continue
        dd = dict(d)
        dd["deliveryEn"] = _strip_internal_labels(dd.get("deliveryEn") or "", where=f"cut {cut_id} deliveryEn")
        dialogue.append(dd)
    out["dialogue"] = dialogue
    return out


def _person_names(plan: dict) -> list[str]:
    """프리비즈 scene.actors 의 인물 이름 목록(sceneEn 정리용)."""
    names: set[str] = set()
    for scene in (plan.get("previz") or {}).get("scenes") or []:
        for actor in scene.get("actors") or []:
            n = str(actor.get("name") or "").strip()
            if n:
                names.add(n)
    return sorted(names, key=len, reverse=True)


def _commit(owner: str, pid: str, task_id: str, scene_map: dict, cuts_map: dict, model: str,
            changed_cuts: list | None = None) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.get("previz") or {}
        brief = previz.get("shotBrief") or _empty_brief()
        if (brief.get("task") or {}).get("id") != task_id:
            raise previz_mod._Discard()
        brief["status"] = "ready"
        brief["error"] = ""
        brief["model"] = model
        brief["scene"] = scene_map
        brief["cuts"] = cuts_map
        if changed_cuts is not None:
            brief["changedCuts"] = [str(c) for c in changed_cuts]  # 작업 206: 이번에 새로 쓴 컷(화면·로그용)
        brief["updatedAt"] = now_iso()
        brief["task"] = {"status": "done", "startedAt": (brief.get("task") or {}).get("startedAt"), "id": None}
        previz["shotBrief"] = brief
        data["previz"] = previz

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except previz_mod._Discard:
        log.info("vnew.shot_brief.discarded pid=%s task=%s", pid, task_id)
        return
    _catch_up(owner, pid)  # 작업 206 fix 1: 작성 중 바뀐 컷이 있으면 곧바로 1번 더


def _fail(owner: str, pid: str, task_id: str, message: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.get("previz") or {}
        brief = previz.get("shotBrief") or _empty_brief()
        if (brief.get("task") or {}).get("id") != task_id:
            raise previz_mod._Discard()
        brief["status"] = "failed"
        brief["error"] = message
        brief["task"] = {"status": "error", "startedAt": (brief.get("task") or {}).get("startedAt"), "id": None}
        previz["shotBrief"] = brief
        data["previz"] = previz

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except previz_mod._Discard:
        log.info("vnew.shot_brief.discarded_fail pid=%s task=%s", pid, task_id)


# ================================================================ 입력 프롬프트


def _location_prompt(plan: dict, owner: str, name_en: str, loc: dict | None, cuts: list[dict]) -> str:
    """한 장소의 촬영 지시서 입력 텍스트(장소 설계·facts·대본 setting + 컷마다 연출 문법·fit·인물·대사)."""
    previz = plan.get("previz") or {}
    grammar_by_cut = {cid: (cell or {}).get("grammar") for cid, cell in (previz.get("panelGrammar") or {}).items()}
    fit_by_cut = previz.get("fit") or {}
    scene_actors = _scene_actors_by_id(plan)
    char_by_id = gen_mod._char_by_id(owner, plan)
    script_setting = (plan.get("script") or {}).get("setting") or {}
    cuts_by_id = {str(c.get("id")): c for c in (plan.get("conti") or {}).get("cuts") or []}

    lines: list[str] = []
    lines.append(f"LOCATION: {name_en or 'the place'}")

    # 장소 설계(design: walls·floor·light·props).
    design = (loc or {}).get("design") or {}
    if design:
        lines.append("Set design:")
        if design.get("floorEn"):
            lines.append(f"  floor: {design.get('floorEn')}")
        if design.get("ceilingEn"):
            lines.append(f"  ceiling: {design.get('ceilingEn')}")
        if design.get("lightEn"):
            lines.append(f"  light: {design.get('lightEn')}")
        walls = design.get("walls") or {}
        for side in ("north", "east", "south", "west"):
            wall = walls.get(side) or {}
            desc = str(wall.get("descEn") or "").strip()
            feats = str(wall.get("featuresEn") or "").strip()
            if desc or feats:
                lines.append(f"  {side} wall: {desc} {('(' + feats + ')') if feats else ''}".rstrip())
        props = design.get("propsEn") or []
        if props:
            prop_names = ", ".join(str((p or {}).get("labelEn") or "") for p in props if (p or {}).get("labelEn"))
            if prop_names:
                lines.append(f"  props: {prop_names}")

    # facts(벽별 물건·재질·색).
    facts = (loc or {}).get("facts") or {}
    if facts:
        try:
            facts_text = json.dumps(facts, ensure_ascii=False)[:1200]
        except Exception:  # noqa: BLE001
            facts_text = str(facts)[:1200]
        lines.append(f"Location facts (walls, materials, colours, objects): {facts_text}")

    # 대본 setting(moodKo·timeKo·layoutKo/En·enclosed).
    lines.append(
        "Scene setting — "
        f"mood(ko): {script_setting.get('moodKo') or ''}; "
        f"time(ko): {script_setting.get('timeKo') or ''}; "
        f"layout(ko): {script_setting.get('layoutKo') or ''}; "
        f"layout(en): {script_setting.get('layoutEn') or ''}; "
        f"enclosed: {bool(script_setting.get('enclosed'))}."
    )

    lines.append("")
    lines.append(
        "Write sceneEn (this location, shared across its cuts) and one brief per cut below. "
        "Use the film grammar, lens and fit given for each cut; name only the people listed as visible."
    )

    for cut in cuts:
        cut_id = str(cut.get("cutId") or "")
        lines.append("")
        lines.append(f"CUT {cut_id}:")
        grammar = grammar_by_cut.get(cut_id) or {}
        if grammar:
            others = ", ".join(
                str((o or {}).get("name") or "") for o in (grammar.get("others") or []) if (o or {}).get("name")
            )
            lines.append(
                "  grammar — "
                f"shotSize: {grammar.get('shotSize') or ''}; shotType: {grammar.get('shotType') or ''}; "
                f"vantage: {grammar.get('vantage') or ''}; faceTurn: {grammar.get('faceTurn') or ''}; "
                f"cameraHeight: {grammar.get('cameraHeight') or ''}; others: {others}."
            )
        fit = fit_by_cut.get(cut_id) or {}
        if fit:
            # 내부 라벨(side +1/-1, crossed)은 영상 모델에게 의미가 없다 → 화면 위치로 풀어서 준다.
            lens = fit.get("lens") or ""
            distance = fit.get("distanceM") or ""
            crossed = bool(fit.get("crossed"))
            if crossed:
                screen_note = (
                    "the camera is on the opposite side of the line for this cut, so the people appear on "
                    "the opposite side of the frame from the other cuts"
                )
            else:
                screen_note = "the camera stays on the usual side of the line"
            lines.append(f"  camera fit — lens: {lens}mm; distanceM: {distance}; {screen_note}.")
        # 보이는 인물(그 컷 timeline actors) → 이름 + 캐릭터 데이터(외형·옷·그림체). 마네킹 색·Astra
        # 배우 외형이 아니라 characters analysis 의 appearanceEn·outfitEn·styleEn 을 쓴다.
        visible = gen_mod._segment_actors(plan, {"cutIds": [cut_id]})
        who = []
        for actor in visible:
            name = actor.get("name") or ""
            char = char_by_id.get(actor.get("charId") or "")
            appearance = ""
            outfit = ""
            style = ""
            if char:
                appearance = str(char.get("appearanceEn") or "")
                outfit = str(char.get("outfitEn") or "")
                style = str(char.get("styleEn") or "")
            desc_parts = [p for p in (appearance, outfit) if p]
            desc = " ".join(desc_parts).strip()
            entry = f"{name}: {desc}".strip().rstrip(":").strip() if desc else name
            if style:
                entry += f" (drawing style: {style})"
            who.append(entry.strip())
        if who:
            lines.append("  visible people (describe them from this character data, not by mannequin color): " + "; ".join(who))
        # conti cut characters 의 행동·상황·샷·카메라·감정.
        conti_cut = cuts_by_id.get(cut_id) or {}
        char_notes = []
        for ch in conti_cut.get("characters") or []:
            nm = str(ch.get("name") or "")
            act = str(ch.get("actionEn") or ch.get("actionKo") or "")
            emo = str(ch.get("emotionKo") or "")
            if nm or act:
                char_notes.append(f"{nm}: {act}{(' [' + emo + ']') if emo else ''}".strip())
        if char_notes:
            lines.append("  character actions: " + "; ".join(char_notes))
        if conti_cut.get("situationKo"):
            lines.append(f"  situation(ko): {conti_cut.get('situationKo')}")
        if conti_cut.get("shotKo"):
            lines.append(f"  shot note(ko): {conti_cut.get('shotKo')}")
        if conti_cut.get("cameraEn"):
            lines.append(f"  camera note: {conti_cut.get('cameraEn')}")
        # 대사(_cut_lines).
        dlg = gen_mod._cut_lines(plan, conti_cut)
        for line in dlg:
            if line.get("silent"):
                lines.append(f"  dialogue: {line.get('speaker') or 'someone'} — (silent)")
            else:
                # 지시서 118a: 속마음 대사는 입 다문 채 목소리만(deliveryEn 은 표정·눈 연기로만 쓰게).
                mark = " (속마음 — 입 다문 채 목소리만)" if gen_mod._is_thought(line) else ""
                lines.append(f"  dialogue: {line.get('speaker') or 'someone'}: \"{line.get('lineKo')}\"{mark}")

    return "\n".join(lines)


def _scene_actors_by_id(plan: dict) -> dict:
    """actorId → scene.actors 항목(색·이름·charId)."""
    out: dict[str, dict] = {}
    for scene in (plan.get("previz") or {}).get("scenes") or []:
        for a in scene.get("actors") or []:
            aid = str(a.get("id") or "")
            if aid:
                out[aid] = a
    return out


# ================================================================ 작업 206: 컷 지문(pvSig)
#
# 지시서 컷 항목마다 그 컷을 쓸 때의 입력 지문(pvSig)을 둔다. 다시 만들 때 지문이 같은 컷은 그대로 두고, 다른 컷·지문
# 없는 컷만 새로 쓴다. 지문 = _location_prompt 가 그 컷에 쓰는 값: 프리비즈 카메라(pos·target·fov·lensMm·roll·moveTo)·
# 컷 길이·배우 배치(pos·facingDeg·pose·moveTo …, 'manual' 표시 제외)·연출 문법·fit·보이는 인물과 캐릭터 데이터·콘티 컷
# 글(characters·situationKo·shotKo·cameraEn·dialogueKo)·대사(_cut_lines) + 장소 지문(장소 설계·facts·대본 setting).
# 콘티 전체 시각(start/end)은 넣지 않는다 — 앞 컷 길이만 바뀌어도 뒤 컷이 모두 바뀐 것으로 보이기 때문이다.

PV_SIG_LEN = 16


def _sig(value) -> str:
    import hashlib  # noqa: PLC0415

    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:PV_SIG_LEN]


def _location_sig(plan: dict, name_en: str, loc: dict | None) -> str:
    """장소 공통 입력(장소 이름·설계·facts·대본 setting)의 지문. 바뀌면 그 장소 컷이 전부 바뀐 것이 된다."""
    return _sig({
        "nameEn": str(name_en or ""),
        "design": (loc or {}).get("design") or {},
        "facts": (loc or {}).get("facts") or {},
        "setting": (plan.get("script") or {}).get("setting") or {},
    })


def _without_manual(item) -> dict:
    out = dict(item) if isinstance(item, dict) else {}
    out.pop("manual", None)
    return out


def cut_pv_sig(plan: dict, cut_id: str, loc_sig: str = "", char_by_id: dict | None = None) -> str:
    """이 컷의 지시서 입력 지문(pvSig, 16자 hex). 같은 입력이면 같은 값."""
    cut_id = str(cut_id or "")
    previz = plan.get("previz") or {}
    entry: dict = {}
    for scene in previz.get("scenes") or []:
        for item in (scene or {}).get("timeline") or []:
            if isinstance(item, dict) and str(item.get("cutId") or "") == cut_id:
                entry = item
                break
        if entry:
            break
    try:
        duration = round(float(entry.get("end") or 0) - float(entry.get("start") or 0), 3)
    except (TypeError, ValueError):
        duration = 0.0
    grammar = ((previz.get("panelGrammar") or {}).get(cut_id) or {}).get("grammar") or {}
    fit = (previz.get("fit") or {}).get(cut_id) or {}
    conti_cut = next((c for c in (plan.get("conti") or {}).get("cuts") or []
                      if isinstance(c, dict) and str(c.get("id")) == cut_id), {})
    char_by_id = char_by_id or {}
    people = []
    for actor in gen_mod._segment_actors(plan, {"cutIds": [cut_id]}):
        char = char_by_id.get(actor.get("charId") or "") or {}
        people.append([actor.get("name") or "", actor.get("charId") or "", char.get("appearanceEn") or "",
                       char.get("outfitEn") or "", char.get("styleEn") or ""])
    lines = []
    for line in gen_mod._cut_lines(plan, conti_cut) if conti_cut else []:
        lines.append([line.get("speaker") or "", line.get("lineKo") or "", bool(line.get("silent")),
                      bool(gen_mod._is_thought(line))])
    return _sig({
        "loc": loc_sig,
        "camera": _without_manual(entry.get("camera")),
        "actors": [_without_manual(a) for a in entry.get("actors") or []],
        "seconds": duration,
        "grammar": {k: grammar.get(k) for k in ("shotSize", "shotType", "vantage", "faceTurn", "cameraHeight")}
        | {"others": [str((o or {}).get("name") or "") for o in grammar.get("others") or []]},
        "fit": {k: fit.get(k) for k in ("lens", "distanceM", "crossed")},
        "people": people,
        "cut": {k: conti_cut.get(k) for k in ("characters", "situationKo", "shotKo", "cameraEn", "dialogueKo")},
        "lines": lines,
    })


# ================================================================ 보기(view)


def brief_view(plan: dict) -> dict:
    """plan_view 의 shotBrief 보기 — status·model·scene·cuts·task·updatedAt."""
    previz = plan.get("previz") or {}
    brief = previz.get("shotBrief") or _empty_brief()
    task = brief.get("task") or {}
    if _task_stale(task):
        brief = dict(brief)
        brief["status"] = "failed"
        brief["error"] = "촬영 지시서 작성이 중단되었습니다. 다시 시도해 주세요."
        brief["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
    return {
        "status": brief.get("status") or "none",
        "error": brief.get("error") or "",
        "model": brief.get("model") or "",
        "scene": brief.get("scene") or {},
        "cuts": brief.get("cuts") or {},
        "updatedAt": brief.get("updatedAt") or "",
        "changedCuts": list(brief.get("changedCuts") or []),
        "task": brief.get("task") or {"status": "idle", "startedAt": None, "id": None},
    }
