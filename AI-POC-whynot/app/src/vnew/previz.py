"""vnew 프리비즈 — Astra 3D 장면 설계(JSON) 서버(설계자 2026-10-21, 지시서 037).

영상 흐름: 웹툰 → 캐릭터 → 콘티(배경·대사) → [여기] 3D 모델링(프리비즈) → 모델링 영상 확인·승인 →
캐릭터 그림체·실사를 각각 입혀 영상 생성. 설계자 결정: Astra 가 장면 설계 JSON 을 쓰고, 브라우저
three.js 가 그려 재생·녹화한다. 이렇게 만든 마네킹 영상을 Kling O3 편집으로 실사화하면 배치·카메라·
인원이 정확히 유지된다(Claude 실측).

프리비즈 규칙(설계자): 배경은 하나의 덩어리로 땅/벽을 알아보고 빛 방향을 표기, 등장 인물은 고유의
색, 그 외 사물은 디테일 없이 인식만. 화질 낮아도 됨.

- 장소별(연속 컷 묶음)로 Astra 에 콘티 컷·대본 위치·location 설계·인물 목록을 주고 장면 JSON 을
  받는다(도구 스키마). previz_schema.normalize_scenes 로 정리(범위·색·시간·텍스처 키·컷 1:1).
- 녹화 업로드는 서버가 imageio-ffmpeg 로 mp4 h264 1280x720 24fps 무음으로 변환해 VIDEO_BUCKET 에
  저장하고 길이를 확인한다.

재사용(수정 금지): vpoc.llm(_converse) · vpoc.store(S3) · vpoc.ledger(charge_text) · vpoc.settings
(VIDEO_BUCKET) · stitcher(ffmpeg_path · probe_duration) · vnew.locations(wall_key/floor_key) ·
vnew.previz_schema(정리). plan.py 가 plan_view 에 previz_view 를 담고 승인 해제 훅을 부른다.

외부 호출(LLM)은 아래 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _design. STORE 는 vpoc.store.STORE.
"""

from __future__ import annotations

import copy
import logging
import os
import secrets
import subprocess
import tempfile
import threading
from datetime import datetime, timezone

from .. import stitcher
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import settings as vpoc_settings
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE

from . import previz_schema
from . import settings as vnew_settings

log = logging.getLogger("vpoc")

# ================================================================ 상수

# 설계 LLM 체인: Astra 먼저, 실패하면 Claude 분석 체인(locations 와 같은 규칙).
ASTRA_MODEL = vnew_settings.ASTRA_MODEL
DESIGN_FALLBACK_MODEL = vnew_settings.ANALYZE_FALLBACK_MODEL
# 텍스처로 쓸 location 그림체(실사 기본).
TEXTURE_VARIANT = "real"
# 녹화 변환 결과 규격(설계자): mp4 h264 1280x720 24fps 무음.
REC_WIDTH = 1280
REC_HEIGHT = 720
REC_FPS = 24
# 녹화 길이 허용 오차·절대 범위(초). 구간 seconds ±0.5초, 그리고 3~15초.
REC_TOLERANCE_S = 0.5
REC_MIN_S = 3.0
REC_MAX_S = 15.0
# 업로드 바이트 상한(200MB).
MAX_RECORDING_BYTES = 200 * 1024 * 1024
# design task 가 이 시간을 넘게 running 이면 멈춘 것으로 본다(조회 때 failed).
TASK_STALL_S = 1800

# 받을 수 있는 녹화 Content-Type.
RECORDING_CONTENT_TYPES = ("video/webm", "video/mp4")


# ================================================================ 키 규칙


def _root(owner: str, pid: str) -> str:
    from . import plan as plan_mod  # noqa: PLC0415

    return plan_mod._root(owner, pid)  # noqa: SLF001


def recording_key(owner: str, pid: str, rev: int, seg_id: str) -> str:
    """녹화 mp4 는 VIDEO_BUCKET 아래 .../vnew/previz/{rev}/{segId}.mp4."""
    return f"{_root(owner, pid)}/previz/{int(rev)}/{seg_id}.mp4"


# ================================================================ 저장 틀


def new_previz() -> dict:
    """빈 프리비즈. plan["previz"] 에 둔다."""
    return {
        "status": "none",  # none|designing|ready|failed
        "error": "",
        "model": "",
        "revision": 0,  # 콘티·장면이 바뀔 때마다 올린다(녹화 키 경로·승인 해제 기준).
        "scenes": [],  # previz_schema.normalize_scenes 결과
        "approved": False,
        "recordings": {},  # segId -> {status, key, seconds}
        "task": _empty_task(),
    }


def _empty_task() -> dict:
    return {"status": "idle", "startedAt": None, "id": None}


# ================================================================ 외부 호출 래퍼(검사가 가짜로)


def _design(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 장면 설계 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_DESIGN_SYSTEM,
        blocks=blocks,
        tool=_DESIGN_TOOL,
        max_tokens=8000,
        where="vnew.previz.design",
        model=model,
    )


# ================================================================ 도구 스키마(Astra)

_DESIGN_SYSTEM = (
    "You are a 3D previsualization layout artist. You design a simple blocky 3D scene for ONE place so "
    "a browser (three.js) can render and record a low-fidelity 'mannequin' video. The video keeps the "
    "exact camera, placement, and number of people so it can later be turned photoreal. "
    "Rules: the background is one solid mass you can read as floor or wall, and you mark where light "
    "comes from; each character is a plain figure in a UNIQUE bright colour so they are easy to tell "
    "apart; other objects are simple blocks/cylinders recognisable only as what they are (no detail). "
    "Answer ONLY by calling emit_scene exactly once. Units are metres and seconds. Coordinate system: "
    "x right, y up, z towards the camera (+); the north wall is at z = -depth/2. "
    "room.walls has north/east/south/west. light.from/to are 3D points, light.kind is 'sun' or 'lamp', "
    "and labelKo names it in Korean (e.g. '북쪽 창문 햇빛'). props are {id, labelKo, labelEn, shape "
    "('box'|'cylinder'), size:[w,h,d], pos:[x,y,z], rotY, color}. actors are {id, name (the role name), "
    "color (a unique bright colour, clearly different from every other actor), heightM, charId?}. "
    "timeline has one entry per conti cut (keep the given cutId values): {cutId, start, end, camera:{pos, "
    "target, fov, moveTo?}, actors:[{id, pos:[x,0,z], facingDeg, pose ('stand'|'sit'|'kneel'|'walk'|'lie'), "
    "moveTo?, gesture?, speaking}]}. Within a cut the camera may move linearly via moveTo. When a cut "
    "changes, ONLY the camera changes — the room, props, and actor positions carry over (actors move only "
    "via moveTo/walk). Place actors to match the webtoon panels and the script positions. "
    "Write Korean for notesKo. Do not add text outside the tool."
)

_DESIGN_OPENING = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
    },
}

_DESIGN_WALL = {
    "type": "object",
    "properties": {
        "color": {"type": "string"},
        "openings": {"type": "array", "items": _DESIGN_OPENING},
    },
}

_DESIGN_CUT_ACTOR = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "pos": {"type": "array", "items": {"type": "number"}},
        "facingDeg": {"type": "number"},
        "pose": {"type": "string"},
        "moveTo": {"type": "array", "items": {"type": "number"}},
        "gesture": {"type": "string"},
        "speaking": {"type": "boolean"},
    },
}

_DESIGN_CAMERA = {
    "type": "object",
    "properties": {
        "pos": {"type": "array", "items": {"type": "number"}},
        "target": {"type": "array", "items": {"type": "number"}},
        "fov": {"type": "number"},
        "moveTo": {
            "type": "object",
            "properties": {
                "pos": {"type": "array", "items": {"type": "number"}},
                "target": {"type": "array", "items": {"type": "number"}},
            },
        },
    },
}

_DESIGN_TOOL = {
    "name": "emit_scene",
    "description": "Emit the 3D previz scene (room, light, props, actors, timeline) for one place.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "depth": {"type": "number"},
                        "height": {"type": "number"},
                        "floorColor": {"type": "string"},
                        "walls": {
                            "type": "object",
                            "properties": {s: _DESIGN_WALL for s in previz_schema.SIDES},
                        },
                    },
                },
                "light": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "from": {"type": "array", "items": {"type": "number"}},
                        "to": {"type": "array", "items": {"type": "number"}},
                        "color": {"type": "string"},
                        "intensity": {"type": "number"},
                        "labelKo": {"type": "string"},
                    },
                },
                "props": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "labelKo": {"type": "string"},
                            "labelEn": {"type": "string"},
                            "shape": {"type": "string"},
                            "size": {"type": "array", "items": {"type": "number"}},
                            "pos": {"type": "array", "items": {"type": "number"}},
                            "rotY": {"type": "number"},
                            "color": {"type": "string"},
                        },
                    },
                },
                "actors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "color": {"type": "string"},
                            "heightM": {"type": "number"},
                            "charId": {"type": "string"},
                        },
                    },
                },
                "timeline": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cutId": {"type": "string"},
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "camera": _DESIGN_CAMERA,
                            "actors": {"type": "array", "items": _DESIGN_CUT_ACTOR},
                        },
                    },
                },
                "notesKo": {"type": "string"},
            },
            "required": ["room", "actors", "timeline"],
        }
    },
}


# ================================================================ 장소별 컷 묶기(연속)


def _scene_groups(conti: dict) -> list[dict]:
    """콘티 컷을 '연속 같은 장소'로 묶어 장면 그룹을 만든다.

    설계자: 장소가 바뀌는 컷(예: 노을 진 거리)은 새 장면. 같은 장소가 이어지면 한 장면.
    반환: [{locationEn, cuts:[{cutId, seconds, panels, ...}]}] (콘티 순서).
    """
    cuts = conti.get("cuts") or []
    groups: list[dict] = []
    for cut in cuts:
        loc = str(cut.get("locationEn") or "").strip()
        spec = {
            "cutId": str(cut.get("id") or ""),
            "seconds": float(cut.get("seconds") or 0),
            "panels": list(cut.get("panels") or []),
            "shotKo": str(cut.get("shotKo") or ""),
            "cameraEn": str(cut.get("cameraEn") or ""),
            "situationKo": str(cut.get("situationKo") or ""),
            "dialogueKo": str(cut.get("dialogueKo") or ""),
            "characters": cut.get("characters") or [],
            "locationEn": loc,
        }
        if groups and groups[-1]["locationEn"] == loc:
            groups[-1]["cuts"].append(spec)
        else:
            groups.append({"locationEn": loc, "cuts": [spec]})
    return groups


def _location_for(plan: dict, name_en: str) -> dict | None:
    """콘티 컷의 locationEn 에 맞는 plan["locations"] 항목(key 정규화 매칭)."""
    from . import locations as loc_mod  # noqa: PLC0415

    key = loc_mod.normalize_name(name_en)
    for loc in plan.get("locations") or []:
        if str(loc.get("key") or loc_mod.normalize_name(loc.get("nameEn") or "")) == key:
            return loc
    return None


def _textures_for(owner: str, pid: str, loc: dict | None) -> dict:
    """location 의 실사(real) variant 벽·바닥 이미지 키를 textures dict 로. 없으면 빈 dict."""
    if not loc:
        return {}
    variant = (loc.get("variants") or {}).get(TEXTURE_VARIANT) or {}
    wall_keys = variant.get("wallKeys") or {}
    walls = {side: str(wall_keys.get(side) or "") for side in previz_schema.SIDES if wall_keys.get(side)}
    textures: dict = {}
    if walls:
        textures["walls"] = walls
    floor_key = str(variant.get("floorKey") or "")
    if floor_key:
        textures["floorKey"] = floor_key
    return textures


def _location_id_for(loc: dict | None, name_en: str) -> str:
    return str((loc or {}).get("id") or "") or ("loc:" + (name_en or ""))


# ================================================================ design(백그라운드)


def start_design(owner: str, pid: str) -> str:
    """프리비즈 설계 시작. 진행 중이면 409. 콘티가 준비돼 있어야 한다. 새 task.id 를 돌려준다."""
    from . import plan as plan_mod  # noqa: PLC0415

    new_id = secrets.token_hex(8)

    def mutator(data: dict) -> str:
        conti = data.get("conti") or {}
        if conti.get("status") != "ready" or not (conti.get("cuts")):
            raise VpocError("vnew.previz.design", "NoConti", "먼저 콘티를 만들어 주세요.", 409)
        previz = data.setdefault("previz", new_previz())
        task = previz.get("task") or _empty_task()
        if task.get("status") == "running" and not _task_stale(task):
            raise VpocError("vnew.previz.design", "Designing", "이미 장면을 설계하고 있습니다.", 409)
        previz["status"] = "designing"
        previz["error"] = ""
        previz["task"] = {"status": "running", "startedAt": now_iso(), "id": new_id}

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn(run_design, owner, pid, new_id, name=f"vnew-previz-{pid}")
    return new_id


def run_design(owner: str, pid: str, task_id: str) -> None:
    """프리비즈 설계(백그라운드). 장소별(연속 컷)로 Astra 에 장면 JSON 을 받아 정리·저장한다."""
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        plan, _ = plan_mod.load_plan(owner, pid)
        conti = plan.get("conti") or {}
        groups = _scene_groups(conti)
        if not groups:
            _fail(owner, pid, task_id, "콘티 컷이 없습니다.")
            return

        plan_id = str(plan.get("planId") or "")
        script = plan.get("script") or {}
        chars = (plan.get("input") or {}).get("characters") or []
        raw_scenes: list[dict] = []
        scene_specs: list[dict] = []
        used_model = ""

        for group in groups:
            name_en = group["locationEn"]
            loc = _location_for(plan, name_en)
            location_id = _location_id_for(loc, name_en)
            textures = _textures_for(owner, pid, loc)
            cut_specs = [{"cutId": c["cutId"], "seconds": c["seconds"]} for c in group["cuts"]]
            scene_specs.append({"locationId": location_id, "cuts": cut_specs, "textures": textures})

            blocks = [{"text": _design_prompt(name_en, loc, group["cuts"], script, chars)}]
            payload, model = _design_scene(owner, pid, plan_id, blocks)
            used_model = model or used_model
            payload = payload if isinstance(payload, dict) else {}
            payload["locationId"] = location_id
            raw_scenes.append(payload)

        scenes = previz_schema.normalize_scenes(raw_scenes, scene_specs=scene_specs)
        _commit_scenes(owner, pid, task_id, scenes, used_model)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.run_design.failed pid=%s err=%s", pid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _fail(owner, pid, task_id, message)


def _design_scene(owner: str, pid: str, plan_id: str, blocks: list[dict]) -> tuple[dict, str]:
    """Astra 먼저, 실패하면 Claude. 사용량을 원장에 남긴다. (payload, model)."""
    for model in (ASTRA_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _design(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.previz.design.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        used = str(usage.get("model") or model)
        _charge(owner, pid, plan_id, usage, used)
        return payload, used
    raise VpocError("vnew.previz.design", "DesignFailed", "장면 설계에 실패했습니다.", 502)


def _design_prompt(name_en: str, loc: dict | None, cuts: list[dict], script: dict, chars: list[dict]) -> str:
    """한 장소(연속 컷)의 설계 요청 문단. 콘티 컷·대본 위치·location 설계·인물 목록을 담는다."""
    setting = script.get("setting") or {}
    entries = script.get("entries") or []
    lines = [
        f"[장소] {name_en or '(이름 없음)'}",
        "이 장소에서 이어지는 콘티 컷들을 하나의 3D 장면으로 설계하세요. timeline 은 아래 컷과 1:1 로,"
        " 주어진 cutId 를 그대로 쓰세요. 컷이 바뀌어도 방·소품·인물 자리는 이어지고 카메라만 바뀝니다.",
        "",
        "[공간 배치(대본)]",
        f"장소 분위기: {setting.get('moodKo', '')} / 시간: {setting.get('timeKo', '')}",
        f"공간: {'닫힌 방' if setting.get('enclosed') else '열린 공간'}",
        f"배치: {setting.get('layoutKo', '')} / {setting.get('layoutEn', '')}",
    ]
    if loc:
        design = loc.get("design") or {}
        walls = design.get("walls") or {}
        lines += [
            "",
            "[장소 설계(공간 시트)]",
            f"크기(m): width≈{design.get('widthM', '?')} depth≈{design.get('depthM', '?')} height≈{design.get('heightM', '?')}",
            f"바닥: {design.get('floorEn', '')} / 천장: {design.get('ceilingEn', '')}",
            f"빛: {design.get('lightEn', '')}",
        ]
        for side in previz_schema.SIDES:
            w = walls.get(side) or {}
            lines.append(
                f"- {side} 벽: {w.get('descEn', '')}"
                + (" [창문]" if w.get("hasWindow") else "")
                + (" [문]" if w.get("hasDoor") else "")
            )
        props = design.get("propsEn") or []
        if props:
            lines.append("소품: " + ", ".join(f"{p.get('labelEn', '')}({p.get('wall', '')})" for p in props))
    lines += ["", "[등장 인물]"]
    if chars:
        for item in chars:
            lines.append(f"- {item.get('roleName', '')} (charId={item.get('charId', '')})")
    else:
        lines.append("(연결된 캐릭터 없음 — 대본 화자 이름을 쓰세요)")
    lines += ["", "[콘티 컷(순서대로 — 각 컷을 timeline 한 항목으로)]"]
    for index, cut in enumerate(cuts, start=1):
        char_pos = _entry_positions(cut.get("panels") or [], entries)
        lines.append(
            f"{index}. cutId={cut['cutId']} seconds={cut['seconds']}"
            f" / 샷={cut.get('shotKo', '')} / 카메라={cut.get('cameraEn', '')}"
            f" / 상황={cut.get('situationKo', '')}"
            + (f" / 인물위치={char_pos}" if char_pos else "")
        )
    lines += [
        "",
        "인물은 고유의 밝은 색(서로 확실히 다르게)으로, 배경은 하나의 덩어리(회색조), 빛은 어디서 오는지"
        " light 로 표기하세요. 컷 안에서 카메라가 움직이면 camera.moveTo, 인물이 걸으면 actor.moveTo 로.",
    ]
    return "\n".join(lines)


def _entry_positions(cut_panels: list[int], entries: list[dict]) -> str:
    """이 컷 panels 와 겹치는 대본 entries 의 인물 positionEn 요약."""
    cut_set = set(cut_panels or [])
    parts: list[str] = []
    for entry in entries:
        if cut_set & set(entry.get("panels") or []):
            for char in entry.get("characters") or []:
                name = str(char.get("name") or "")
                pos = str(char.get("positionEn") or "")
                if name or pos:
                    parts.append(f"{name}:{pos}")
    return "; ".join(parts)


# ================================================================ 저장(task.id 확인)


def _commit_scenes(owner: str, pid: str, task_id: str, scenes: list[dict], model: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.setdefault("previz", new_previz())
        if (previz.get("task") or {}).get("id") != task_id:
            raise _Discard()
        previz["scenes"] = scenes
        previz["status"] = "ready"
        previz["error"] = ""
        previz["model"] = model
        previz["approved"] = False  # 새로 설계하면 승인 해제.
        previz["revision"] = int(previz.get("revision") or 0) + 1
        previz["recordings"] = {}  # revision 이 바뀌면 녹화 키 경로가 달라진다.
        previz["task"] = {"status": "done", "startedAt": (previz.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.previz.design.discarded pid=%s task=%s", pid, task_id)


def _fail(owner: str, pid: str, task_id: str, message: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        previz = data.setdefault("previz", new_previz())
        if (previz.get("task") or {}).get("id") != task_id:
            raise _Discard()
        previz["status"] = "failed"
        previz["error"] = message
        previz["task"] = {"status": "error", "startedAt": (previz.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.previz.design.discarded_fail pid=%s task=%s", pid, task_id)


class _Discard(Exception):
    """늦게 온 결과를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


# ================================================================ 사용자 수정(PUT scene) · 승인


def update_scenes(owner: str, pid: str, patch: dict) -> dict:
    """사용자가 고친 scenes 를 저장한다(같은 정리 함수 통과). 저장하면 승인 해제.

    patch = {"scenes": [...]} — 각 scene 은 locationId·timeline 을 가진다. 정리할 때 cut_specs 는
    현재 저장된 scene 의 timeline(cutId·길이)을 기준으로 쓴다(컷 수·시간을 임의로 바꾸지 못하게).
    """
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        previz = data.setdefault("previz", new_previz())
        raw_scenes = patch.get("scenes")
        if not isinstance(raw_scenes, list):
            raise VpocError("vnew.previz.scene", "BadScenes", "scenes 배열이 필요합니다.", 400)
        # 현재 저장본에서 scene_specs(각 scene 의 cutId·seconds·textures)를 뽑는다.
        current = previz.get("scenes") or []
        scene_specs = _specs_from_scenes(current)
        if not scene_specs:
            # 아직 설계 전이면 콘티에서 그룹을 만든다(저장본이 없어도 PUT 을 허용).
            scene_specs = _specs_from_conti(owner, pid, data)
        scenes = previz_schema.normalize_scenes(raw_scenes, scene_specs=scene_specs)
        previz["scenes"] = scenes
        if previz.get("status") in ("none", "failed"):
            previz["status"] = "ready"
        previz["approved"] = False  # 장면을 고치면 승인 해제.
        data["revision"] = int(data.get("revision") or 0) + 1
        return previz

    _, previz = plan_mod.mutate_plan(owner, pid, mutator)
    return previz


def _specs_from_scenes(scenes: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for scene in scenes or []:
        timeline = scene.get("timeline") or []
        cuts = [{"cutId": str(t.get("cutId") or ""), "seconds": max(0.0, float(t.get("end") or 0) - float(t.get("start") or 0))} for t in timeline]
        textures = _textures_from_scene(scene)
        specs.append({"locationId": str(scene.get("locationId") or ""), "cuts": cuts, "textures": textures})
    return specs


def _textures_from_scene(scene: dict) -> dict:
    """저장된 scene.room 에서 textureKey 를 뽑아 textures 모양으로(PUT 때 유지)."""
    room = scene.get("room") or {}
    walls = room.get("walls") or {}
    tex_walls = {side: str((walls.get(side) or {}).get("textureKey") or "") for side in previz_schema.SIDES if (walls.get(side) or {}).get("textureKey")}
    textures: dict = {}
    if tex_walls:
        textures["walls"] = tex_walls
    if room.get("floorTextureKey"):
        textures["floorKey"] = str(room.get("floorTextureKey"))
    return textures


def _specs_from_conti(owner: str, pid: str, data: dict) -> list[dict]:
    groups = _scene_groups(data.get("conti") or {})
    specs: list[dict] = []
    for group in groups:
        loc = _location_for(data, group["locationEn"])
        location_id = _location_id_for(loc, group["locationEn"])
        cut_specs = [{"cutId": c["cutId"], "seconds": c["seconds"]} for c in group["cuts"]]
        specs.append({"locationId": location_id, "cuts": cut_specs, "textures": _textures_for(owner, pid, loc)})
    return specs


def approve(owner: str, pid: str) -> dict:
    """프리비즈 승인. scenes 가 있어야 한다."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        previz = data.setdefault("previz", new_previz())
        if not (previz.get("scenes")):
            raise VpocError("vnew.previz.approve", "NoScenes", "먼저 장면을 설계해 주세요.", 409)
        if previz.get("status") != "ready":
            raise VpocError("vnew.previz.approve", "NotReady", "장면이 준비되지 않았습니다.", 409)
        previz["approved"] = True
        return previz

    _, previz = plan_mod.mutate_plan(owner, pid, mutator)
    return previz


def unapprove(plan: dict) -> None:
    """콘티·장면을 고치면 승인 해제(plan.py 훅이 부른다). plan 을 제자리에서 고친다."""
    previz = plan.get("previz")
    if isinstance(previz, dict) and previz.get("approved"):
        previz["approved"] = False


# ================================================================ 녹화 업로드(변환·길이 확인)


def save_recording(owner: str, pid: str, seg_id: str, body: bytes, content_type: str) -> dict:
    """녹화(webm/mp4) 바이트를 받아 mp4 h264 1280x720 24fps 무음으로 변환해 VIDEO_BUCKET 에 저장.

    - segId 는 conti.segments 의 id 여야 한다(그 구간 seconds 로 길이를 검사).
    - 길이: 구간 seconds ±0.5초, 그리고 3~15초(벗어나면 400).
    - 최대 200MB. Content-Type 은 video/webm|video/mp4.
    반환: {status, key, seconds, url}.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in RECORDING_CONTENT_TYPES:
        raise VpocError("vnew.previz.recording", "BadContentType", "녹화는 webm 또는 mp4 여야 합니다.", 400)
    if not body:
        raise VpocError("vnew.previz.recording", "Empty", "녹화 데이터가 비어 있습니다.", 400)
    if len(body) > MAX_RECORDING_BYTES:
        raise VpocError("vnew.previz.recording", "TooLarge", "녹화 파일이 너무 큽니다(최대 200MB).", 400)

    plan, _ = plan_mod.load_plan(owner, pid)
    previz = plan.get("previz") or {}
    rev = int(previz.get("revision") or 0)
    segment = _find_segment(plan, seg_id)
    if segment is None:
        raise VpocError("vnew.previz.recording", "NoSegment", "구간을 찾을 수 없습니다.", 404)
    target_seconds = float(segment.get("seconds") or 0)

    raw_seconds = _probe_bytes_seconds(body, ctype)
    if target_seconds > 0 and raw_seconds and raw_seconds < target_seconds - REC_TOLERANCE_S:
        raise VpocError(
            "vnew.previz.recording", "BadDuration",
            f"녹화가 구간 길이({target_seconds:.1f}초)보다 너무 짧습니다(측정 {raw_seconds:.2f}초).", 400,
        )
    mp4, seconds = _convert_recording(body, ctype, target_seconds)

    # 길이 확인: 구간 seconds ±0.5초, 절대 3~15초.
    if seconds < REC_MIN_S or seconds > REC_MAX_S:
        raise VpocError(
            "vnew.previz.recording",
            "BadDuration",
            f"녹화 길이가 {REC_MIN_S:.0f}~{REC_MAX_S:.0f}초 범위를 벗어났습니다(측정 {seconds:.2f}초).",
            400,
        )
    if target_seconds > 0 and abs(seconds - target_seconds) > REC_TOLERANCE_S:
        raise VpocError(
            "vnew.previz.recording",
            "BadDuration",
            f"녹화 길이가 구간 {target_seconds:.2f}초와 ±{REC_TOLERANCE_S}초 넘게 차이납니다(측정 {seconds:.2f}초).",
            400,
        )

    key = recording_key(owner, pid, rev, seg_id)
    STORE.put_bytes(key, mp4, "video/mp4", bucket=vpoc_settings.VIDEO_BUCKET)

    def mutator(data: dict) -> None:
        pv = data.setdefault("previz", new_previz())
        pv.setdefault("recordings", {})[seg_id] = {"status": "ready", "key": key, "seconds": round(seconds, 3)}

    plan_mod.mutate_plan(owner, pid, mutator)
    return {"status": "ready", "key": key, "seconds": round(seconds, 3), "url": _presign_video(key)}


def _find_segment(plan: dict, seg_id: str) -> dict | None:
    for seg in (plan.get("conti") or {}).get("segments") or []:
        if str(seg.get("id") or "") == seg_id:
            return seg
    return None


def _convert_recording(body: bytes, ctype: str, target_seconds: float = 0.0) -> tuple[bytes, float]:
    """webm/mp4 바이트를 mp4 h264 1280x720 24fps 무음으로 변환하고 (mp4 bytes, 길이초)를 돌려준다.

    imageio-ffmpeg 정적 바이너리(stitcher.ffmpeg_path)를 쓴다. 변환 실패면 502.
    """
    binary = stitcher.ffmpeg_path()
    suffix = ".webm" if ctype == "video/webm" else ".mp4"
    with tempfile.TemporaryDirectory(prefix="previz-rec-") as workdir:
        src = os.path.join(workdir, "src" + suffix)
        out = os.path.join(workdir, "out.mp4")
        with open(src, "wb") as handle:
            handle.write(body)
        command = [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-i", src,
            # 1280x720 안에 맞추고(비율 유지) 24fps 로. 홀수 폭을 피하려 pad 로 정확히 채운다.
            "-vf", f"scale={REC_WIDTH}:{REC_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={REC_WIDTH}:{REC_HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={REC_FPS}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out,
        ]
        if target_seconds > 0:
            # 녹화는 실시간이라 계획보다 수십 ms 짧을 수 있다(예: 2.96초). 마지막 프레임을 늘리고 계획 길이로
            # 정확히 자른다 — Kling 편집은 3~15초만 받는다. 0.5초 넘게 짧은 녹화는 아래 길이 검사가 거절한다.
            command[command.index("-vf") + 1] += f",tpad=stop_mode=clone:stop_duration={REC_TOLERANCE_S}"
            command[-1:-1] = ["-t", f"{target_seconds:.3f}"]
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=stitcher.STITCH_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode != 0 or not os.path.exists(out) or not os.path.getsize(out):
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise VpocError("vnew.previz.recording", "ConvertFailed", f"녹화 변환에 실패했습니다: {detail}", 502)
        seconds = stitcher.probe_duration(out)
        with open(out, "rb") as handle:
            mp4 = handle.read()
    return mp4, float(seconds or 0.0)


def _probe_bytes_seconds(body: bytes, ctype: str) -> float:
    """원본 녹화 길이(초). 못 재면 0."""
    suffix = ".webm" if ctype == "video/webm" else ".mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="previz-probe-") as workdir:
            src = os.path.join(workdir, "src" + suffix)
            with open(src, "wb") as handle:
                handle.write(body)
            return float(stitcher.probe_duration(src) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


# ================================================================ 보기(previz_view)


def previz_view(owner: str, pid: str, plan: dict) -> dict:
    """plan_view 의 previz — status, scenes(JSON), 텍스처 presigned URL, 승인·녹화 상태."""
    previz = plan.get("previz") or new_previz()
    previz = _reconcile_stale(previz)
    rev = int(previz.get("revision") or 0)

    scenes_out = []
    for scene in previz.get("scenes") or []:
        scenes_out.append(_scene_view(scene))

    segments = (plan.get("conti") or {}).get("segments") or []
    recordings_in = previz.get("recordings") or {}
    recordings_out: dict[str, dict] = {}
    recorded_all = bool(segments)
    for seg in segments:
        seg_id = str(seg.get("id") or "")
        rec = recordings_in.get(seg_id)
        if rec and rec.get("key"):
            recordings_out[seg_id] = {
                "status": "ready",
                "url": _presign_video(str(rec.get("key") or "")),
                "seconds": rec.get("seconds"),
            }
        else:
            recordings_out[seg_id] = {"status": "none", "url": "", "seconds": None}
            recorded_all = False

    return {
        "status": previz.get("status") or "none",
        "error": previz.get("error") or "",
        "model": previz.get("model") or "",
        "revision": rev,
        "approved": bool(previz.get("approved")),
        "scenes": scenes_out,
        "totalSeconds": previz_schema.scenes_total_seconds(previz.get("scenes") or []),
        "segments": segments,
        "recordings": recordings_out,
        "recordedAll": recorded_all,
        "task": previz.get("task") or _empty_task(),
    }


def _scene_view(scene: dict) -> dict:
    """scene JSON 에 텍스처 presigned URL 을 더해 돌려준다(키는 그대로 남긴다)."""
    out = copy.deepcopy(scene)
    room = out.get("room") or {}
    walls = room.get("walls") or {}
    for side in previz_schema.SIDES:
        wall = walls.get(side)
        if isinstance(wall, dict) and wall.get("textureKey"):
            wall["textureUrl"] = _presign_image(str(wall.get("textureKey")))
    if room.get("floorTextureKey"):
        room["floorTextureUrl"] = _presign_image(str(room.get("floorTextureKey")))
    return out


def _reconcile_stale(previz: dict) -> dict:
    task = previz.get("task") or {}
    if _task_stale(task):
        previz = dict(previz)
        previz["status"] = "failed"
        previz["error"] = "장면 설계가 중단되었습니다. 다시 시도해 주세요."
        previz["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
    return previz


# ================================================================ 원장 · presign


def _charge(owner: str, pid: str, plan_id: str, usage: dict, model: str) -> None:
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        key = f"vnew-previz-{pid}-{plan_id}-{secrets.token_hex(3)}"
        vpoc_ledger.charge_text(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=plan_id,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.charge.failed pid=%s err=%s", pid, str(exc)[:200])


def _presign_image(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, content_type="image/jpeg", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.presign_image.failed key=%s err=%s", key, str(exc)[:150])
        return ""


def _presign_video(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, bucket=vpoc_settings.VIDEO_BUCKET, content_type="video/mp4", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.previz.presign_video.failed key=%s err=%s", key, str(exc)[:150])
        return ""


# ================================================================ 작은 도우미


def _spawn(func, owner: str, pid: str, task_id: str, *, name: str) -> None:
    thread = threading.Thread(target=func, args=(owner, pid, task_id), daemon=True, name=name)
    thread.start()


def _task_stale(task: dict) -> bool:
    if (task or {}).get("status") != "running":
        return False
    started = _parse_iso((task or {}).get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > TASK_STALL_S


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
