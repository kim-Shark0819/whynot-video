"""vnew 영상만들기_신규 — 공간 디테일 시트(설계자 2026-10-21).

콘티 단계에서 쓰이는 배경(장소)마다 '4면이 다 보이는' 고정 공간 이미지를 만든다. 다음 단계
프리비즈(three.js)의 벽·바닥에 입힐 텍스처로 쓰려고, 각 벽을 정면에서 본 평평한 입면도(원근 없음)
로 그린다. 그림체별(real·webtoon)로 벽 4장 + 바닥 1장을 Seedream 으로 그리고, 벽 4장을 서버에서
2x2 로 붙여 '한 장 시트'를 만든다.

장소 목록은 plan["locations"] 에 둔다(콘티를 다시 만들어도 같은 이름이면 유지). script.entries 의
locationEn 을 정규화(소문자·공백 정리)해 고유 장소를 뽑는다.

재사용(수정 금지): vpoc.seedream(generate_safe · size) · vpoc.llm(_converse) · vpoc.images
(panel_jpeg · jpeg_for_reference) · vpoc.store(S3) · vpoc.ledger(원장) · vnew.characters(styleEn).
plan.py 가 sync_locations 를 콘티 끝에서 부르고, plan_view 가 locations_view 를 담는다. routes.py
는 build · build-all · update 를 HTTP 로 감싼다.

외부 호출(LLM · Seedream)은 아래 두 래퍼로 감싼다(검사가 가짜로 바꿔 끼운다): _design · _seedream.
STORE 는 vpoc.store.STORE 를 쓴다(검사가 모듈 변수로 바꿔 끼운다).
"""

from __future__ import annotations

import io
import logging
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from ..vpoc import images as vpoc_images
from ..vpoc import ledger as vpoc_ledger
from ..vpoc import llm as vpoc_llm
from ..vpoc import seedream as vpoc_seedream
from ..vpoc.errors import VpocError, now_iso
from ..vpoc.store import STORE

from . import characters
from . import settings

log = logging.getLogger("vpoc")

# ================================================================ 상수

# 설계에 보여 줄 웹툰 패널 최대 장수(그 장소가 나오는 패널). 배경이 큰 조각 우선.
MAX_DESIGN_PANELS = 6
# Seedream 참고 이미지 최대 장수(그 장소 패널).
MAX_REF_PANELS = 4
# 벽 입면도 크기(16:9). 4벽 모두 같은 크기.
WALL_SIZE = "2048x1152"
# 바닥 텍스처 크기(정사각, top-down).
FLOOR_SIZE = "1536x1536"
# 벽 방향(north=웹툰에서 가장 많이 보이는 정면 벽).
SIDES = ("north", "east", "south", "west")
# 그림체.
VARIANTS = ("real", "webtoon")
# 한 장소의 그림(벽4+바닥1 × 그림체 2 = 10장)을 동시에 그리는 최대 스레드 수.
IMAGE_WORKERS = 4
# 실패한 그림을 다시 그리는 횟수(1회).
IMAGE_RETRY = 1
# 추가 요청(한국어) 최대 길이.
MAX_EXTRA_PROMPT_LEN = 500
# build task 가 이 시간을 넘게 running 이면 멈춘 것으로 본다(조회 때 failed).
BUILD_STALL_S = 1800

# 단가(원장). Seedream 장당.
SEEDREAM_USD = settings.SEEDREAM_USD
SEEDREAM_MODEL = settings.SEEDREAM_MODEL
# 설계 LLM 체인: Claude 분석 체인(Fable 5.1) 먼저, 실패하면 Astra.
# (실측 2026-10-21: Astra 가 패널 그림을 거의 못 읽어 빈 벽·'assumed'/'provisional' 설계가 나왔다.
#  Claude 가 이미지 읽기가 강해 먼저 쓴다. Astra 는 지시서 036 장면 설계에 그대로 쓴다.)
DESIGN_MODEL = settings.ANALYZE_FALLBACK_MODEL  # = vpoc.settings.ANALYZE_MODEL (Claude Fable 5.1)
DESIGN_FALLBACK_MODEL = settings.ASTRA_MODEL


# ================================================================ 파드 안 실행 중 표시
#
# (owner,pid,lid) 가 이 파드에서 빌드 스레드로 돌고 있으면 _RUNNING 에 담는다. 같은 location 을
# 두 번 띄우지 않게 한다(409 와 함께 이중 안전장치).

_RUNNING: set[tuple[str, str, str]] = set()
_RUNNING_LOCK = threading.Lock()


def _mark_running(owner: str, pid: str, lid: str) -> bool:
    key = (owner, pid, lid)
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)
        return True


def _clear_running(owner: str, pid: str, lid: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING.discard((owner, pid, lid))


# ================================================================ 정규화 · 목록


_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """장소 이름 정규화 키(대소문자·공백 무시). 'A closed  Principal's Office ' → 'a closed principal's office'."""
    text = str(name or "").strip().lower()
    return _WS_RE.sub(" ", text)


def _loc_id() -> str:
    return f"loc_{secrets.token_hex(4)}"


def _empty_task() -> dict:
    return {"status": "idle", "startedAt": None, "id": None}


def _new_location(name_ko: str, name_en: str, panels: list[int]) -> dict:
    """빈 장소. design·variants(real·webtoon)·task 를 틀로 둔다."""
    return {
        "id": _loc_id(),
        "nameKo": str(name_ko or "").strip(),
        "nameEn": str(name_en or "").strip(),
        "key": normalize_name(name_en),
        "panels": sorted({int(p) for p in panels if _is_int(p)}),
        "status": "none",  # none|building|ready|failed
        "error": "",
        "design": {},  # 설계 JSON(아래 _normalize_design)
        "variants": {v: _empty_variant() for v in VARIANTS},
        "task": _empty_task(),
    }


def _empty_variant() -> dict:
    return {"status": "none", "wallKeys": {}, "floorKey": "", "sheetKey": "", "error": ""}


def sync_locations(plan: dict) -> dict:
    """plan["locations"] 를 script.entries 의 locationEn 으로 갱신한다(자동으로 그리지 않는다).

    - entries 를 돌며 locationEn 을 정규화해 고유 장소를 뽑는다(같은 key 는 하나로 합친다).
    - 이미 있는 장소(같은 key)는 유지하되 panels·이름만 갱신한다(설계·이미지는 보존).
    - 더 이상 어느 entry 에도 없는 장소는 뺀다(콘티가 바뀌어 사라진 배경).
    plan 을 제자리에서 고치고 그대로 돌려준다(저장은 호출자가 한다).
    """
    entries = ((plan.get("script") or {}).get("entries")) or []
    # key → {nameKo, nameEn, panels:set}
    found: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        name_en = str((entry or {}).get("locationEn") or "").strip()
        if not name_en:
            continue
        key = normalize_name(name_en)
        if not key:
            continue
        name_ko = str((entry or {}).get("locationKo") or "").strip()
        panels = [int(p) for p in ((entry or {}).get("panels") or []) if _is_int(p)]
        slot = found.get(key)
        if slot is None:
            slot = {"nameKo": name_ko, "nameEn": name_en, "panels": set()}
            found[key] = slot
            order.append(key)
        else:
            # 첫 이름을 유지하되, 비어 있던 한글 이름은 채운다.
            if not slot["nameKo"] and name_ko:
                slot["nameKo"] = name_ko
        slot["panels"].update(panels)

    existing = {str(loc.get("key") or normalize_name(loc.get("nameEn") or "")): loc for loc in (plan.get("locations") or [])}

    result: list[dict] = []
    for key in order:
        slot = found[key]
        panels = sorted(slot["panels"])
        prior = existing.get(key)
        if prior is not None:
            # 유지: panels·이름만 갱신, 설계·이미지·상태는 보존.
            prior = dict(prior)
            prior["key"] = key
            prior["panels"] = panels
            prior["nameEn"] = slot["nameEn"] or prior.get("nameEn") or ""
            if slot["nameKo"]:
                prior["nameKo"] = slot["nameKo"]
            prior.setdefault("variants", {v: _empty_variant() for v in VARIANTS})
            prior.setdefault("design", {})
            prior.setdefault("task", _empty_task())
            prior.setdefault("status", "none")
            result.append(prior)
        else:
            result.append(_new_location(slot["nameKo"], slot["nameEn"], panels))

    plan["locations"] = result
    return plan


def _find_location(plan: dict, lid: str) -> dict | None:
    for loc in plan.get("locations") or []:
        if str(loc.get("id") or "") == lid:
            return loc
    return None


# ================================================================ 외부 호출 래퍼
#
# 검사가 이 두 함수를 가짜로 바꿔 끼운다(실제 유료 호출 금지).


def _design(blocks: list[dict], *, model: str) -> tuple[dict, dict]:
    """llm._converse 로 공간 설계 JSON 을 받는다. (payload, usage{..model})."""
    return vpoc_llm._converse(  # noqa: SLF001 — 공개 래퍼가 없어 내부 함수를 쓴다
        system_text=_DESIGN_SYSTEM,
        blocks=blocks,
        tool=_DESIGN_TOOL,
        max_tokens=1600,
        where="vnew.location.design",
        model=model,
    )


def _seedream(prompt: str, references: list[bytes], *, size: str) -> bytes:
    """Seedream Pro 로 그림 1장. 안전 필터 오탐은 generate_safe 가 처리."""
    return vpoc_seedream.generate_safe(
        prompt,
        references,
        model=SEEDREAM_MODEL,
        where="vnew.location.seedream",
        size=size,
    )


# ================================================================ 설계(Claude → Astra)

_DESIGN_SYSTEM = (
    "You are a set designer. You are given a few webtoon panels that all show the SAME place. "
    "Describe that place as a simple rectangular room so an artist can draw each of its four walls as "
    "a flat frontal elevation (orthographic, no perspective). Answer ONLY by calling the emit_location "
    "tool exactly once. "
    "Choose 'north' as the wall the webtoon camera faces most often (the main front wall seen in the "
    "panels); east/south/west follow clockwise. "
    "Describe ONLY what is actually drawn in the panels. For every wall, list concrete visible features: "
    "windows and their rough size and position, bookshelves, cabinets, desks standing against the wall, "
    "notice boards, blackboards, framed pictures, doors. Put these in featuresEn (a short English list) "
    "and summarise the wall in one English sentence in descEn. "
    "If a wall is never shown in any panel, infer a plausible wall that is consistent with the walls you "
    "DID see — same materials, same era, same country — and describe it with concrete features too. "
    "Never leave a wall blank, and never write the words 'assumed', 'provisional', 'not visible', or "
    "'unknown' anywhere in your answer. "
    "hasWindow / hasDoor are booleans. widthM/depthM/heightM are rough room dimensions in metres. "
    "enclosed is true for a closed room. floorEn and ceilingEn describe the floor and ceiling surfaces "
    "with concrete materials and colour. lightEn is one English sentence about where the main light comes "
    "from (e.g. 'main light comes from the north-wall windows, soft daylight'). propsEn lists free-standing "
    "props as objects {labelEn, wall, sizeM} where wall is one of north/east/south/west/center. "
    "Ignore people and speech bubbles — describe only the room itself. "
    "Write plain English for every *En field. Do not add text outside the tool."
)

_DESIGN_WALL = {
    "type": "object",
    "properties": {
        "descEn": {"type": "string"},
        "hasWindow": {"type": "boolean"},
        "hasDoor": {"type": "boolean"},
        "featuresEn": {"type": "string"},
    },
}

_DESIGN_TOOL = {
    "name": "emit_location",
    "description": "Describe the single place seen in the reference panels as a rectangular room.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "widthM": {"type": "number"},
                "depthM": {"type": "number"},
                "heightM": {"type": "number"},
                "enclosed": {"type": "boolean"},
                "floorEn": {"type": "string"},
                "ceilingEn": {"type": "string"},
                "walls": {
                    "type": "object",
                    "properties": {
                        "north": _DESIGN_WALL,
                        "east": _DESIGN_WALL,
                        "south": _DESIGN_WALL,
                        "west": _DESIGN_WALL,
                    },
                },
                "lightEn": {"type": "string"},
                "propsEn": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "labelEn": {"type": "string"},
                            "wall": {"type": "string"},
                            "sizeM": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["walls"],
        }
    },
}


def design_location(owner: str, pid: str, loc: dict) -> dict:
    """그 장소 패널(최대 6장)을 Claude 분석 체인에 먼저, 실패하면 Astra 로 보내 설계 JSON 을 받는다.

    세로 긴 패널은 tiles_for_analysis 로 잘려 있으므로, 인물이 적고 배경이 큰 조각을 먼저 고른다.
    setting 의 locationKo·layoutEn·enclosed 와 패널 설명을 함께 준다. 결과에 'assumed'/'provisional'
    같은 말이 있으면(벽을 못 읽었다는 뜻) 1회만 더 강하게 재요청한다.
    반환: 정리된 설계 JSON(아래 _normalize_design) + model.
    """
    panels = _design_panels(owner, pid, loc)
    name_en = str(loc.get("nameEn") or "").strip() or "the place"
    setting = _setting_for(owner, pid)
    location_ko = str(setting.get("locationKo") or "").strip()
    layout_en = str(setting.get("layoutEn") or "").strip()
    enclosed = _as_bool(setting.get("enclosed"))

    intro_lines = [
        f"다음 웹툰 패널들은 모두 같은 장소('{name_en}')를 보여 줍니다. 이 장소를 사각형 방으로 보고 "
        "네 벽을 각각 정면 입면도로 그릴 수 있게 설계해 주세요. 카메라가 가장 자주 보는 정면 벽을 "
        "north 로 두세요. 인물·말풍선은 무시하고 방 자체만 묘사하세요.",
    ]
    if location_ko:
        intro_lines.append(f"장소(한국어): {location_ko}")
    if layout_en:
        intro_lines.append(f"Scene layout: {layout_en}")
    intro_lines.append(
        "This is a Korean setting (closed room)." if enclosed else "This is a Korean setting."
    )
    intro_lines.append(
        "Each panel below shows this place from a different angle — read the drawn walls, windows, "
        "shelves, desks and doors in the panels and base every wall on them."
    )
    blocks: list[dict] = [{"text": "\n".join(intro_lines)}]
    for jpeg in panels:
        blocks.append({"image": {"format": "jpeg", "source": {"bytes": jpeg}}})

    for model in (DESIGN_MODEL, DESIGN_FALLBACK_MODEL):
        try:
            payload, usage = _design(blocks, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.design.model_failed model=%s err=%s", model, str(exc)[:200])
            continue
        design = _normalize_design(payload)
        # 벽을 못 읽어 'assumed'/'provisional' 로 얼버무렸으면 같은 모델에 1회 더 강하게 요청한다.
        if _has_provisional(design):
            retry_blocks = blocks + [
                {
                    "text": (
                        "Your previous answer used words like 'assumed' or 'provisional' or left a wall "
                        "blank. Look again at the panels and give concrete visible features for every "
                        "wall (windows with size/position, bookshelves, cabinets, desks, notice boards, "
                        "doors). For any wall not shown, infer a plausible wall consistent with the ones "
                        "you can see. Never write 'assumed' or 'provisional'."
                    )
                }
            ]
            try:
                payload2, usage2 = _design(retry_blocks, model=model)
                design2 = _normalize_design(payload2)
                if not _has_provisional(design2):
                    payload, usage, design = payload2, usage2, design2
            except Exception as exc:  # noqa: BLE001
                log.warning("vnew.location.design.retry_failed model=%s err=%s", model, str(exc)[:200])
        design["model"] = str(usage.get("model") or model)
        _charge_text(owner, pid, loc.get("id") or "", usage, design["model"])
        return design
    raise VpocError("vnew.location.design", "DesignFailed", "공간 설계에 실패했습니다.", 502)


_PROVISIONAL_RE = re.compile(r"\b(assumed|provisional|not visible|unknown)\b", re.IGNORECASE)


def _has_provisional(design: dict) -> bool:
    """설계 텍스트에 '못 읽었다'는 얼버무림(assumed/provisional 등)이 있으면 True."""
    parts: list[str] = [
        str((design or {}).get("floorEn") or ""),
        str((design or {}).get("ceilingEn") or ""),
        str((design or {}).get("lightEn") or ""),
    ]
    for side in SIDES:
        wall = ((design or {}).get("walls") or {}).get(side) or {}
        parts.append(str(wall.get("descEn") or ""))
        parts.append(str(wall.get("featuresEn") or ""))
    return bool(_PROVISIONAL_RE.search(" ".join(parts)))


def _setting_for(owner: str, pid: str) -> dict:
    """plan.script.setting(장소 한국어·배치·밀폐 여부). 못 읽으면 빈 dict."""
    from . import plan as plan_mod  # noqa: PLC0415

    try:
        data, _ = plan_mod.load_plan(owner, pid)
    except Exception:  # noqa: BLE001
        return {}
    setting = ((data.get("script") or {}).get("setting")) or {}
    return setting if isinstance(setting, dict) else {}


def _design_panels(owner: str, pid: str, loc: dict) -> list[bytes]:
    """그 장소가 나오는 패널 조각들(최대 MAX_DESIGN_PANELS 장). 인물이 적고 배경이 큰 조각 우선.

    panels 는 '분석 타일 인덱스'가 아니라 script.panels 와 같은 '타일 번호'다. order 를 통째로
    타일로 잘라, 그 번호의 조각을 꺼낸다. 인물 수가 적은 조각을 먼저 고른다(배경이 크게 보이는 쪽).
    """
    from . import plan as plan_mod  # noqa: PLC0415 — 순환 import 피하기

    order = list(((plan_mod.load_plan(owner, pid)[0]).get("input") or {}).get("order") or [])
    if not order:
        return []
    tiles = _all_tiles(owner, pid, order)
    wanted = [int(p) for p in (loc.get("panels") or []) if _is_int(p)]
    picked: list[bytes] = []
    for index in wanted:
        if 0 <= index < len(tiles):
            picked.append(tiles[index])
    if not picked:
        # 번호가 범위를 벗어나면(옛 데이터) 앞에서부터 채운다.
        picked = tiles[:MAX_DESIGN_PANELS]
    # 인물이 적은 조각 우선(배경이 큰 조각). 사람 수 검사가 없으면 순서 유지.
    picked = _prefer_background(picked)
    return picked[:MAX_DESIGN_PANELS]


def _all_tiles(owner: str, pid: str, order: list[str]) -> list[bytes]:
    from . import plan as plan_mod  # noqa: PLC0415

    tiles: list[bytes] = []
    for file_id in order:
        try:
            jpeg = STORE.get_bytes(plan_mod.upload_key(owner, pid, file_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("vnew.location.tiles.missing file=%s err=%s", file_id, str(exc)[:150])
            continue
        tiles.extend(vpoc_images.tiles_for_analysis(jpeg))
    return tiles


def _prefer_background(tiles: list[bytes]) -> list[bytes]:
    """인물이 적은 조각을 앞으로(배경이 크게 보이는 조각 우선). characters._count_people 를 쓴다.

    검사가 _count_people 를 건너뛰거나(None) 오류면 원래 순서를 그대로 둔다(안정 정렬).
    """
    scored: list[tuple[int, int, bytes]] = []
    for index, jpeg in enumerate(tiles):
        try:
            count = characters._count_people(jpeg)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            count = None
        rank = 99 if count is None else int(count)
        scored.append((rank, index, jpeg))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]


def _normalize_design(payload: dict) -> dict:
    """설계 출력을 공간 모양으로 정리한다(없는 필드 기본값 · 벽 4면 보장 · props 정리)."""
    payload = payload if isinstance(payload, dict) else {}
    walls_in = payload.get("walls")
    walls_in = walls_in if isinstance(walls_in, dict) else {}
    walls: dict[str, dict] = {}
    for side in SIDES:
        raw = walls_in.get(side)
        raw = raw if isinstance(raw, dict) else {}
        walls[side] = {
            "descEn": str(raw.get("descEn") or "").strip(),
            "hasWindow": _as_bool(raw.get("hasWindow")),
            "hasDoor": _as_bool(raw.get("hasDoor")),
            "featuresEn": str(raw.get("featuresEn") or "").strip(),
        }
    props: list[dict] = []
    raw_props = payload.get("propsEn")
    if isinstance(raw_props, list):
        for item in raw_props:
            if not isinstance(item, dict):
                continue
            label = str(item.get("labelEn") or "").strip()
            if not label:
                continue
            props.append(
                {
                    "labelEn": label,
                    "wall": str(item.get("wall") or "center").strip().lower() or "center",
                    "sizeM": _as_float(item.get("sizeM"), 0.0),
                }
            )
    return {
        "widthM": _as_float(payload.get("widthM"), 4.0),
        "depthM": _as_float(payload.get("depthM"), 4.0),
        "heightM": _as_float(payload.get("heightM"), 2.7),
        "enclosed": _as_bool(payload.get("enclosed")),
        "floorEn": str(payload.get("floorEn") or "").strip(),
        "ceilingEn": str(payload.get("ceilingEn") or "").strip(),
        "walls": walls,
        "lightEn": str(payload.get("lightEn") or "").strip(),
        "propsEn": props,
    }


# ================================================================ 프롬프트

# 프롬프트 안에 반드시 들어가야 하는 지시(검사가 확인): 원근 없음 · 인물 없음.
_ORTHO = (
    "Orthographic straight-on view, the wall fills the frame edge to edge, floor line at the bottom, "
    "ceiling line at the top, no perspective, no people, no furniture in front of the wall except items "
    "attached to it."
)

# 벽·바닥 프롬프트 끝에 붙이는 지시(검사가 확인): 글자·표지판 금지 + 한국 배경.
_NO_TEXT = "No text, letters, signs or labels anywhere. Korean setting."


def _style_clause(variant: str, loc: dict) -> str:
    """그림체 지시. real=실사, webtoon=프로젝트 원작 그림체(캐릭터 analysis.styleEn 있으면 그것)."""
    if variant == "real":
        return "Photoreal, natural light."
    style = str(loc.get("styleEn") or "").strip()
    if style:
        return style + " Match this drawing style exactly."
    return "same drawing style as the reference panels."


def _wall_prompt(variant: str, side: str, loc: dict) -> str:
    name_en = str(loc.get("nameEn") or "").strip() or "the room"
    wall = ((loc.get("design") or {}).get("walls") or {}).get(side) or {}
    desc = str(wall.get("descEn") or "").strip() or f"a plain wall of {name_en}"
    features = str(wall.get("featuresEn") or "").strip()
    light = str((loc.get("design") or {}).get("lightEn") or "").strip()
    prompt = f"Flat frontal elevation of the {side} wall of {name_en}: {desc}."
    if features:
        prompt += f" Concrete features on this wall: {features}."
    prompt += " " + _ORTHO + " " + _style_clause(variant, loc)
    if light:
        prompt += " " + light
    prompt += " " + _NO_TEXT
    return _with_extra(prompt, loc)


def _floor_prompt(variant: str, loc: dict) -> str:
    name_en = str(loc.get("nameEn") or "").strip() or "the room"
    floor = str((loc.get("design") or {}).get("floorEn") or "").strip() or "a plain floor"
    prompt = (
        f"Top-down orthographic floor texture of {name_en}: {floor}. "
        "Seamless flat floor seen straight from above, no perspective, no people, no furniture. "
        + _style_clause(variant, loc)
        + " "
        + _NO_TEXT
    )
    return _with_extra(prompt, loc)


def _with_extra(prompt: str, loc: dict) -> str:
    extra = str(loc.get("extraEn") or "").strip()
    if extra:
        return prompt + " " + extra
    return prompt


# ================================================================ 2x2 시트 붙이기


def montage_2x2(walls: dict[str, bytes]) -> bytes:
    """벽 4장(N/E/S/W)을 2x2 로 붙인 한 장 시트(JPEG). 각 칸에 작은 라벨을 얹는다.

    배치: 좌상 N, 우상 E, 좌하 S, 우하 W. 각 칸은 같은 크기(WALL_SIZE)로 맞춘다.
    """
    cell_w, cell_h = _size_wh(WALL_SIZE)
    sheet = Image.new("RGB", (cell_w * 2, cell_h * 2), (24, 24, 24))
    positions = {
        "north": (0, 0),
        "east": (cell_w, 0),
        "south": (0, cell_h),
        "west": (cell_w, cell_h),
    }
    draw = ImageDraw.Draw(sheet)
    for side in SIDES:
        jpeg = walls.get(side)
        x, y = positions[side]
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as tile:
                tile = tile.convert("RGB").resize((cell_w, cell_h))
                sheet.paste(tile, (x, y))
        label = side[0].upper()  # N/E/S/W
        # 반투명 느낌의 어두운 배지 + 흰 글자(작게).
        draw.rectangle([x + 8, y + 8, x + 44, y + 40], fill=(0, 0, 0))
        draw.text((x + 18, y + 14), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# ================================================================ 키 규칙


def _var_dir(owner: str, pid: str, lid: str, variant: str) -> str:
    from . import plan as plan_mod  # noqa: PLC0415

    return f"{plan_mod._root(owner, pid)}/locations/{lid}/{variant}"  # noqa: SLF001


def wall_key(owner: str, pid: str, lid: str, variant: str, side: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/wall_{side}_v{version}.jpg"


def floor_key(owner: str, pid: str, lid: str, variant: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/floor_v{version}.jpg"


def sheet_key(owner: str, pid: str, lid: str, variant: str, version: int) -> str:
    return f"{_var_dir(owner, pid, lid, variant)}/sheet_v{version}.jpg"


# ================================================================ 빌드


def start_build(owner: str, pid: str, lid: str, extra_prompt_ko: str | None = None) -> str:
    """공간 시트 만들기 시작. 진행 중이면 409. 새 task.id 를 돌려준다(백그라운드가 이 id 로 확인)."""
    from . import plan as plan_mod  # noqa: PLC0415

    new_id = secrets.token_hex(8)

    def mutator(data: dict) -> str:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.build", "NotFound", "장소를 찾을 수 없습니다.", 404)
        task = loc.get("task") or _empty_task()
        if task.get("status") == "running" and not _task_stale(task):
            raise VpocError("vnew.location.build", "Building", "이미 공간 시트를 만들고 있습니다.", 409)
        if extra_prompt_ko is not None:
            cleaned = str(extra_prompt_ko or "").strip()[:MAX_EXTRA_PROMPT_LEN]
            if cleaned:
                loc["extraPromptKo"] = cleaned
            else:
                loc.pop("extraPromptKo", None)
        loc["version"] = int(loc.get("version") or 0) + 1
        loc["status"] = "building"
        loc["error"] = ""
        loc["task"] = {"status": "running", "startedAt": now_iso(), "id": new_id}
        return new_id

    plan_mod.mutate_plan(owner, pid, mutator)
    _spawn(run_build, owner, pid, lid, new_id, name=f"vnew-loc-{lid}")
    return new_id


def start_build_all(owner: str, pid: str) -> list[str]:
    """아직 진행 중이 아닌 모든 장소의 공간 시트 만들기 시작. 시작한 lid 목록을 돌려준다."""
    from . import plan as plan_mod  # noqa: PLC0415

    data, _ = plan_mod.load_plan(owner, pid)
    started: list[str] = []
    for loc in data.get("locations") or []:
        lid = str(loc.get("id") or "")
        task = loc.get("task") or {}
        if not lid or (task.get("status") == "running" and not _task_stale(task)):
            continue
        try:
            start_build(owner, pid, lid, None)
            started.append(lid)
        except VpocError as exc:
            if exc.kind == "Building":
                continue
            raise
    return started


def run_build(owner: str, pid: str, lid: str, task_id: str) -> None:
    """공간 시트 만들기(백그라운드 스레드). 설계 → 그림체별 벽 4장 + 바닥 1장 → 2x2 시트.

    task_id 가 현재 task.id 와 다르면(취소·재실행) 결과를 버린다.
    """
    from . import plan as plan_mod  # noqa: PLC0415

    if not _mark_running(owner, pid, lid):
        return
    try:
        data, _ = plan_mod.load_plan(owner, pid)
        loc = _find_location(data, lid)
        if loc is None:
            return
        version = int(loc.get("version") or 1)

        # 그림체(webtoon)용 원작 그림체: 연결된 캐릭터 analysis.styleEn 이 있으면 쓴다.
        loc = dict(loc)
        loc["styleEn"] = _project_style(owner, data)
        loc["extraEn"] = _extra_en(loc)

        # a. 설계(Claude → Astra). 이미 설계가 있으면(수정본) 그대로 쓰고, 없으면 새로 받는다.
        design = loc.get("design") or {}
        if not (design.get("walls")):
            design = design_location(owner, pid, loc)
            _store_design(owner, pid, lid, design)
            loc["design"] = design

        # b~d. 그림체별 벽 4장 + 바닥 1장을 병렬로 그리고(최대 IMAGE_WORKERS), 2x2 시트.
        refs = _design_panels(owner, pid, loc)[:MAX_REF_PANELS]
        images = _generate_images(owner, pid, lid, version, loc, refs)
        for variant in VARIANTS:
            _assemble_variant(owner, pid, lid, variant, version, images, task_id)

        _commit(owner, pid, lid, task_id, status="ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.build.failed pid=%s lid=%s err=%s", pid, lid, str(exc)[:300])
        message = exc.message if isinstance(exc, VpocError) else str(exc)[:300]
        _commit(owner, pid, lid, task_id, status="failed", error=message)
    finally:
        _clear_running(owner, pid, lid)


def _image_jobs(loc: dict) -> list[dict]:
    """그릴 그림 목록(벽4+바닥1 × 그림체 2 = 10). 각 job 은 variant·kind·side·prompt·size."""
    jobs: list[dict] = []
    for variant in VARIANTS:
        for side in SIDES:
            jobs.append(
                {
                    "variant": variant,
                    "kind": "wall",
                    "side": side,
                    "prompt": _wall_prompt(variant, side, loc),
                    "size": WALL_SIZE,
                }
            )
        jobs.append(
            {
                "variant": variant,
                "kind": "floor",
                "side": "",
                "prompt": _floor_prompt(variant, loc),
                "size": FLOOR_SIZE,
            }
        )
    return jobs


def _generate_images(
    owner: str, pid: str, lid: str, version: int, loc: dict, refs: list[bytes]
) -> dict[tuple[str, str], tuple[bytes, str]]:
    """벽4+바닥1 × 그림체 2 = 10장을 ThreadPoolExecutor 로 동시에 그린다(최대 IMAGE_WORKERS).

    실패한 장만 1회 재시도한다. 그래도 실패하면 예외를 올려(전체 실패) run_build 가 failed 로 적는다.
    반환: {(variant, side_or_'floor'): (jpeg_bytes, key)}. 저장(put_bytes)·원장은 여기서 한다(그림당 1회).
    """
    jobs = _image_jobs(loc)
    results: dict[tuple[str, str], tuple[bytes, str]] = {}

    def _slot(job: dict) -> tuple[str, str]:
        return (job["variant"], job["side"] if job["kind"] == "wall" else "floor")

    def _one(job: dict) -> None:
        variant = job["variant"]
        kind = job["kind"]
        side = job["side"]
        image = _seedream(job["prompt"], refs, size=job["size"])
        if kind == "wall":
            key = wall_key(owner, pid, lid, variant, side, version)
            charge_kind = f"{variant}-wall-{side}"
        else:
            key = floor_key(owner, pid, lid, variant, version)
            charge_kind = f"{variant}-floor"
        STORE.put_bytes(key, image, "image/jpeg")
        _charge_seedream(owner, pid, lid, charge_kind)
        results[_slot(job)] = (image, key)

    pending = list(jobs)
    last_error: Exception | None = None
    for attempt in range(IMAGE_RETRY + 1):
        if not pending:
            break
        failed: list[dict] = []
        workers = max(1, min(IMAGE_WORKERS, len(pending)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"vnew-loc-img-{lid}") as pool:
            future_to_job = {pool.submit(_one, job): job for job in pending}
            for future in future_to_job:
                job = future_to_job[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    failed.append(job)
        if failed:
            log.warning(
                "vnew.location.image.retry pid=%s lid=%s attempt=%s failed=%s",
                pid,
                lid,
                attempt,
                [(j["variant"], j["kind"], j["side"]) for j in failed],
            )
        pending = failed
    if pending:
        raise last_error or VpocError("vnew.location.seedream", "SeedreamFailed", "공간 그림 생성에 실패했습니다.", 502)
    return results


def _assemble_variant(
    owner: str,
    pid: str,
    lid: str,
    variant: str,
    version: int,
    images: dict[tuple[str, str], tuple[bytes, str]],
    task_id: str,
) -> None:
    """이미 그려 둔(그리고 저장한) 벽 4장 + 바닥 1장으로 2x2 시트를 붙여 그 variant 만 저장한다."""
    wall_keys: dict[str, str] = {}
    walls_bytes: dict[str, bytes] = {}
    for side in SIDES:
        image, key = images[(variant, side)]
        wall_keys[side] = key
        walls_bytes[side] = image
    _, fkey = images[(variant, "floor")]

    sheet = montage_2x2(walls_bytes)
    skey = sheet_key(owner, pid, lid, variant, version)
    STORE.put_bytes(skey, sheet, "image/jpeg")

    _store_variant(
        owner,
        pid,
        lid,
        variant,
        {"status": "ready", "wallKeys": wall_keys, "floorKey": fkey, "sheetKey": skey, "error": ""},
        task_id,
    )


# ================================================================ 저장(task.id 확인)


def _store_design(owner: str, pid: str, lid: str, design: dict) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is not None:
            loc["design"] = design

    plan_mod.mutate_plan(owner, pid, mutator)


def _store_variant(owner: str, pid: str, lid: str, variant: str, result: dict, task_id: str) -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        if (data.get("task") or {}) and _task_mismatch(data, lid, task_id):
            raise _Discard()
        loc = _find_location(data, lid)
        if loc is None:
            raise _Discard()
        loc.setdefault("variants", {})[variant] = result

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.location.variant.discarded pid=%s lid=%s variant=%s", pid, lid, variant)


def _commit(owner: str, pid: str, lid: str, task_id: str, *, status: str, error: str = "") -> None:
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> None:
        loc = _find_location(data, lid)
        if loc is None:
            raise _Discard()
        if (loc.get("task") or {}).get("id") != task_id:
            raise _Discard()
        loc["status"] = status
        loc["error"] = error
        loc["task"] = {"status": "done" if status == "ready" else "error", "startedAt": (loc.get("task") or {}).get("startedAt"), "id": None}

    try:
        plan_mod.mutate_plan(owner, pid, mutator)
    except _Discard:
        log.info("vnew.location.commit.discarded pid=%s lid=%s", pid, lid)


def _task_mismatch(data: dict, lid: str, task_id: str) -> bool:
    loc = _find_location(data, lid)
    if loc is None:
        return True
    return (loc.get("task") or {}).get("id") != task_id


class _Discard(Exception):
    """늦게 온 결과를 버릴 때 mutate_plan 밖으로 빠져나오는 신호."""


# ================================================================ 설계 수정(PUT)


def update_location(owner: str, pid: str, lid: str, patch: dict) -> dict:
    """설계 JSON 수정 — 이름·벽 설명·빛. 이미지는 그대로 두고 다음 '다시 만들기' 때 반영된다."""
    from . import plan as plan_mod  # noqa: PLC0415

    def mutator(data: dict) -> dict:
        loc = _find_location(data, lid)
        if loc is None:
            raise VpocError("vnew.location.update", "NotFound", "장소를 찾을 수 없습니다.", 404)
        if "nameKo" in patch:
            loc["nameKo"] = str(patch.get("nameKo") or "").strip()
        if "nameEn" in patch:
            name_en = str(patch.get("nameEn") or "").strip()
            loc["nameEn"] = name_en
            loc["key"] = normalize_name(name_en)
        design = loc.setdefault("design", {})
        if not design.get("walls"):
            design.update(_normalize_design(design))
        if "lightEn" in patch:
            design["lightEn"] = str(patch.get("lightEn") or "").strip()
        if isinstance(patch.get("walls"), dict):
            walls = design.setdefault("walls", {})
            for side in SIDES:
                wall_patch = patch["walls"].get(side)
                if not isinstance(wall_patch, dict):
                    continue
                wall = walls.setdefault(side, {})
                if "descEn" in wall_patch:
                    wall["descEn"] = str(wall_patch.get("descEn") or "").strip()
                if "featuresEn" in wall_patch:
                    wall["featuresEn"] = str(wall_patch.get("featuresEn") or "").strip()
                if "hasWindow" in wall_patch:
                    wall["hasWindow"] = _as_bool(wall_patch.get("hasWindow"))
                if "hasDoor" in wall_patch:
                    wall["hasDoor"] = _as_bool(wall_patch.get("hasDoor"))
        if "floorEn" in patch:
            design["floorEn"] = str(patch.get("floorEn") or "").strip()
        if "ceilingEn" in patch:
            design["ceilingEn"] = str(patch.get("ceilingEn") or "").strip()
        data["revision"] = int(data.get("revision") or 0) + 1
        return loc

    _, loc = plan_mod.mutate_plan(owner, pid, mutator)
    return loc


# ================================================================ 보기(presigned)


def locations_view(owner: str, pid: str, plan: dict) -> list[dict]:
    """plan_view 의 locations[] — 각 장소의 설계·상태·그림체별 이미지 presigned URL."""
    out: list[dict] = []
    for loc in plan.get("locations") or []:
        loc = _reconcile_stale(loc)
        variants_out = {}
        for variant in VARIANTS:
            v = (loc.get("variants") or {}).get(variant) or {}
            wall_urls = {side: _presign(v.get("wallKeys", {}).get(side) or "") for side in SIDES}
            variants_out[variant] = {
                "status": v.get("status") or "none",
                "error": v.get("error") or "",
                "wallUrls": wall_urls,
                "floorUrl": _presign(v.get("floorKey") or ""),
                "sheetUrl": _presign(v.get("sheetKey") or ""),
            }
        out.append(
            {
                "id": loc.get("id"),
                "nameKo": loc.get("nameKo") or "",
                "nameEn": loc.get("nameEn") or "",
                "panels": loc.get("panels") or [],
                "status": loc.get("status") or "none",
                "error": loc.get("error") or "",
                "design": loc.get("design") or {},
                "variants": variants_out,
                "task": loc.get("task") or _empty_task(),
            }
        )
    return out


def _reconcile_stale(loc: dict) -> dict:
    """조회 때: building 이 오래 멈췄으면 failed 로 본다(저장하지 않고 뷰만)."""
    task = loc.get("task") or {}
    if _task_stale(task):
        loc = dict(loc)
        loc["status"] = "failed"
        loc["error"] = "공간 시트 만들기가 중단되었습니다. 다시 시도해 주세요."
        loc["task"] = {"status": "error", "startedAt": task.get("startedAt"), "id": None}
    return loc


# ================================================================ 원장


def _charge_seedream(owner: str, pid: str, lid: str, kind: str) -> None:
    key = f"vnew-loc-{pid}-{lid}-{kind}-{secrets.token_hex(3)}"
    try:
        vpoc_ledger.charge(
            key,
            kind="image",
            model=SEEDREAM_MODEL,
            units=1,
            usd=SEEDREAM_USD,
            user=owner,
            project_id=pid,
            plan_id=lid,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.ledger.seedream_failed pid=%s err=%s", pid, str(exc)[:200])


def _charge_text(owner: str, pid: str, lid: str, usage: dict, model: str) -> None:
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if not input_tokens and not output_tokens:
            return
        key = f"vnew-loc-{pid}-{lid}-design-{secrets.token_hex(3)}"
        vpoc_ledger.charge_text(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user=owner,
            project_id=pid,
            plan_id=lid,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.ledger.text_failed pid=%s err=%s", pid, str(exc)[:200])


def _presign(key: str) -> str:
    if not key:
        return ""
    try:
        return STORE.presign(key, content_type="image/jpeg", expires=3600)
    except Exception as exc:  # noqa: BLE001
        log.warning("vnew.location.presign.failed key=%s err=%s", key, str(exc)[:150])
        return ""


# ================================================================ 작은 도우미


def _project_style(owner: str, plan: dict) -> str:
    """webtoon 그림체용 원작 그림체. 연결된 캐릭터 중 analysis.styleEn 이 있으면 첫 번째를 쓴다."""
    for item in ((plan.get("input") or {}).get("characters")) or []:
        char_id = str((item or {}).get("charId") or "")
        if not char_id:
            continue
        try:
            char, _ = characters.load_char(owner, char_id)
            style = str((char.get("analysis") or {}).get("styleEn") or "").strip()
            if style:
                return style
        except Exception:  # noqa: BLE001
            continue
    return ""


def _extra_en(loc: dict) -> str:
    """사용자 추가 요청(한국어)을 그대로 영어 지시로 쓸 수는 없으므로, 있으면 짧게 덧붙인다.

    간단히 원문을 'Additional request:' 로 감싸 Seedream 프롬프트 끝에 붙인다(설계 LLM 이 없을 때도
    동작하도록 번역 LLM 을 따로 호출하지 않는다 — 비용·복잡도).
    """
    extra_ko = str(loc.get("extraPromptKo") or "").strip()
    if not extra_ko:
        return ""
    return "Additional request: " + extra_ko


def _spawn(func, owner: str, pid: str, lid: str, task_id: str, *, name: str) -> None:
    thread = threading.Thread(target=func, args=(owner, pid, lid, task_id), daemon=True, name=name)
    thread.start()


def _size_wh(size: str) -> tuple[int, int]:
    try:
        width, height = str(size).lower().split("x")
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 2048, 1152


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return False


def _as_float(value, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _task_stale(task: dict) -> bool:
    if (task or {}).get("status") != "running":
        return False
    started = _parse_iso((task or {}).get("startedAt") or "")
    if not started:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() > BUILD_STALL_S


def _parse_iso(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
