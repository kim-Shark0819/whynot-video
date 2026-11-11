"""작업 229 검사 — 애니(웹툰) 테이크 조각을 모든 테이크 모델이 거절하면 그 조각의 컷만 Kling O3 로 컷 단위로 만든다. 가짜만(네트워크 0).

  (ak-a) Wan 애니 테이크(4조각 · 컷 8개 × 2초): 조각 3 이 Wan 콘텐츠 거절 → 순화 Wan 거절 → 콘텐츠 2단계 Seedance likeness 거절 →
         그 조각의 컷(c5 · c6)만 Kling(컷마다 요청 1 · 웹툰 정면/시트 elements · 웹툰 장소 · 애니 STYLE 글 · 실사 글 없음) →
         조각 4 는 Wan, 테이크 완료 · 잇기(조각 1 · 2 · Kling 2 · 조각 4). 조각 1 · 2 다시 제출 0
  (ak-b) Kling 도 거절 → 지금 문구 + "Kling도 거절했습니다" 로 실패, 그 뒤 제출 0(되돌아가지 않음)
  (ak-c) Seedance 애니 테이크 likeness 거절 → 228 Wan 대체 먼저(그대로). Wan 이 조각 3 을 콘텐츠 거절 → 사다리 2단계 Seedance 가
         likeness → Kling 컷 단위
  (ak-d) 실사 조각 likeness 사다리 → 컷 단위 Kling: 호출 순서 sha 가 고치기 전과 같다(사다리 켬 · 끔)
  (ak-e) 설정 끔(VNEW_ANIME_KLING_FALLBACK False) · Kling 모델 없음 → 지금처럼 실패(Kling 0)
  (ak-f) 원장 = Kling 초당 단가 × 보낸 길이 · 조각 animeKling{cutIds, at} · 모델 노트 fallbackModel:<Kling> · 화면 한 줄
  (ak-g) 변이 "모든 조각 Kling" · "애니 Kling 프롬프트에 실사 글" 을 검사가 잡는다(검사 안에서만 바꾼다 — 파일은 그대로)
  (ak-h) 바꾼 파일 LF 줄 끝 · node --check vnew.js(node 가 없으면 WARN)

실행: python -X utf8 -B tools/check_anime_kling_229.py   (고치기 전 sha: --baseline)
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import __future__ as _future
import base64
import contextlib
import hashlib
import inspect
import json
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PUBLIC = ROOT / "frontend" / "public"
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOOK = ('    if not __import__("check_anime_kling_229").check_anime_kling_229(): '
        'fail("작업 229: 애니 테이크 조각 모든 모델 거절 → Kling O3 컷 단위 검사 실패")')
GROUPS = {
    "ak-a": "Wan 콘텐츠 거절 → Seedance likeness → 그 조각 컷만 Kling(웹툰 재료 · 애니 글) → 완료 · 잇기",
    "ak-b": "Kling 도 거절 → 지금 문구 + Kling도 거절했습니다 · 되돌아가지 않음",
    "ak-c": "Seedance likeness → 228 Wan 먼저 → Wan 콘텐츠 → 사다리 likeness → Kling 컷 단위",
    "ak-d": "실사 likeness 사다리 호출 순서 sha 그대로(켬 · 끔)",
    "ak-e": "설정 끔 · Kling 없음 → 지금처럼 실패",
    "ak-f": "원장 Kling 단가 · animeKling 기록 · 모델 노트 · 화면 한 줄",
    "ak-g": "변이(모든 조각 Kling · 애니 Kling 에 실사 글)를 검사가 잡음",
    "ak-h": "LF 줄 끝 · node --check vnew.js",
}
CHANGED = ("app/src/vnew/generate.py", "app/src/vnew/settings.py", "frontend/public/vnew.js", "tools/check_anime_kling_229.py")
WAN = "alibaba/wan-3.0/reference-to-video"
KLING = "fal-ai/kling-video/o3/pro/reference-to-video"
KLING_PRICE = 0.168  # 가짜 Kling 초당 단가(USD)
KLING_CAPS = {"promptField": "prompt", "promptMaxLength": 2500, "startImageField": "start_image_url",
              "elementsField": "elements", "imageListField": "image_urls", "imageListMax": 4, "durations": [5, 10],
              "durationAsString": True, "durationValues": {"5": "5", "10": "10"}, "sendDuration": True,
              "aspectRatios": ["16:9"], "available": True}
LIKENESS_RAW = "The images or videos provided may contain likenesses of real people or other private information."
CONTENT_RAW = "Output video was flagged by the content checker."
KLING_TEXT = "Wan·Seedance가 거절한 컷(c5, c6)은 Kling O3로 컷 단위로 만들었습니다."
KLING_FAIL_TAIL = "Kling도 거절했습니다"
# 고치기 전(작업 229 전) 코드에서 --baseline 으로 뽑은 실사 조각 사다리 호출 순서 sha(ak-d). 바꾸면 안 된다.
REAL_LADDER_SHA = {
    "ladder": "33a2a1e37ffa3155d4ee591cb50b4a4ff7706bda45279eb550d4dc154b4554f4",
    "ladder-off": "33a2a1e37ffa3155d4ee591cb50b4a4ff7706bda45279eb550d4dc154b4554f4",
    "kling-off": "084a65c0707438f9d731a2778faa1b45e106268d87e2e03abf09bdaa426b2f1e",
}
REAL_STYLE_MARK = "Cinematic live-action film footage"
PHOTOREAL_WORDS = ("photoreal", "live-action", "Cinematic live-action", "35mm", "natural skin", "real photograph")
NO_NODE = False
_CLIP_RE = re.compile(rb"^CLIP:(\d+\.\d+)-(\d+\.\d+):")


def _decode(uri) -> bytes:
    try:
        return base64.b64decode(str(uri).split(",", 1)[1])
    except Exception:  # noqa: BLE001
        return b""


def _start_of(req: dict) -> float:
    match = _CLIP_RE.match(_decode(((req.get("payload") or {}).get("video_urls") or [""])[0]))
    return float(match.group(1)) if match else -1.0


def _is(req: dict, endpoint: str) -> bool:
    return str(req.get("url") or "").endswith("/" + endpoint)


def _prompt(req: dict) -> str:
    return str((req.get("payload") or {}).get("prompt") or "")


def _real_req(req: dict) -> bool:
    return REAL_STYLE_MARK in _prompt(req)


def _seed() -> str:
    import check_vpoc as cv  # noqa: PLC0415

    return cv._LR_ENDPOINT  # noqa: SLF001


def LIKENESS_MSG() -> str:  # noqa: N802
    from src.vpoc import fal  # noqa: PLC0415

    return fal._REJECT_LIKENESS_MSG  # noqa: SLF001


def _names(reqs: list[dict]) -> list[str]:
    return ["wan" if _is(q, WAN) else "seedance" if _is(q, _seed()) else "kling" if _is(q, KLING) else "?" for q in reqs]


@contextlib.contextmanager
def _env(owner: str = "u229", **pins):
    """_LrEnv(check_vpoc) + 가짜 Wan · Kling O3 등록 · 단가 · 조각 자르기 · 잇기 + 거절 정책(policy["rule"](req) → 원문|None)."""
    import check_vpoc as cv  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415

    values = dict(VNEW_FX=False, VNEW_CASTING=True, VNEW_TAKE_REAL=True, VNEW_GEN_PARALLEL=1, POLL_INTERVAL_S=0,
                  VNEW_TAKE_AUDIO=True, VNEW_TAKE_RESOLUTION="480p", VNEW_TEXT_CHECK=False, VNEW_REAL_TAKE_MODEL="",
                  VNEW_VIBE_PROMPT=True, VNEW_TAKE_PROMPT_MAX_CHARS=8000, VNEW_TAKE_MAX_SECONDS=30, VNEW_TAKE_REF_SPLIT=True,
                  VNEW_PART_CONTINUITY=True, VNEW_CONTENT_FALLBACK_MODEL=_seed(), VNEW_TAKE_REAL_LIKENESS_LADDER=True,
                  VNEW_TAKE_REAL_KLING_MODEL=KLING, VNEW_REAL_CUT_QA=False, VNEW_REAL_COSTUME_REF=False,
                  VNEW_CONTI_CAST=False, VNEW_REAL_STILL_CHECK=False, VNEW_MODEL_NOTES=True, VNEW_EDIT_RETAKE=True,
                  VNEW_PREPARE_STALE_S=300)
    if hasattr(vset, "VNEW_ANIME_LIKENESS_FALLBACK_MODEL"):
        values["VNEW_ANIME_LIKENESS_FALLBACK_MODEL"] = WAN
    values.update({k: v for k, v in pins.items() if hasattr(vset, k)})
    with cv._guard_real_llm(), cv._LrEnv(owner) as env, cv._pin_vnew(**values):  # noqa: SLF001
        genmod = env.genmod
        pfx = genmod.vpoc_settings.FAL_ID_PREFIX
        for cid in ("ch1", "ch2"):
            env.chars[cid]["variants"]["real"]["sheetKey"] = f"rs-{cid}"
            env.chars[cid]["variants"]["webtoon"]["sheetKey"] = f"ws-{cid}"
        env.blobs.update({"rs-ch1": b"RS1", "rs-ch2": b"RS2", "ws-ch1": b"WS1", "ws-ch2": b"WS2"})
        seed_find, seed_price, seed_price_e = genmod.vpoc_fal.find, genmod.vpoc_fal.pricing_for, genmod.vpoc_fal.pricing
        wan_caps = dict(cv._RV152_CAPS, seedField="seed")  # noqa: SLF001

        def find(model):
            if str(model) == pfx + WAN:
                return {"id": pfx + WAN, "endpointId": WAN, "category": "reference-to-video", "caps": dict(wan_caps)}
            if str(model) == pfx + KLING:
                return {"id": pfx + KLING, "endpointId": KLING, "caps": dict(KLING_CAPS)}
            return seed_find(model)

        def price(endpoint: str):
            if endpoint == WAN:
                return {"unit": "1000 tokens", "unitPrice": 0.0105}
            if endpoint == KLING:
                return {"unit": "seconds", "unitPrice": KLING_PRICE}
            return None

        env.set(genmod.vpoc_fal, "find", find)
        env.set(genmod.vpoc_fal, "pricing_for", lambda m: price(str(m)[len(pfx):]) or seed_price(m))
        env.set(genmod.vpoc_fal, "pricing", lambda e: price(str(e)) or seed_price_e(e))
        env.set(genmod.vpoc_settings, "FAL_REF_VIDEO_MAX_S", 4)
        env.set(genmod, "_cut_clip", lambda mp4, unit: {
            "bytes": b"CLIP:%.3f-%.3f:" % (float(unit["startSec"]), float(unit["endSec"])) + bytes(mp4)[:20],
            "origSeconds": round(float(unit["endSec"]) - float(unit["startSec"]), 3),
            "sentSeconds": round(float(unit["endSec"]) - float(unit["startSec"]), 3), "padded": False})
        env.set(genmod, "_mp4_seconds", lambda mp4: 0.0)
        merged: list[int] = []
        env.set(genmod, "_conform_concat", lambda pieces, **_kw: merged.append(len(pieces)) or b"PARTS:%d" % len(pieces))
        env.set(genmod, "_cut_first_frame", lambda mp4, start: b"FRAME@%.2f" % float(start))
        env.set(genmod, "_video_last_frame", lambda key, seconds: b"LAST:" + str(key).encode()[-24:])
        env.set(genmod, "_seedream", lambda prompt, refs, *, size: b"STILL")
        policy = {"rule": lambda req: None, "rejected": [], "merged": merged}
        base_fal = env._fal  # noqa: SLF001

        def fal_request(method, url, payload=None, *, where=""):
            rid = url.split("/requests/", 1)[-1].split("/", 1)[0]
            req = next((r for r in env.requests if r["rid"] == rid), None)
            if method == "GET" and not url.endswith("/status") and req is not None:
                raw = policy["rule"](req)
                if raw:
                    policy["rejected"].append(rid)
                    raise env.VpocError("fal.poll", "HTTP422", raw, 502)
            return base_fal(method, url, payload, where=where)

        env.set(env.falmod, "_request", fal_request)
        yield env, env.client(), genmod, policy


def _make(env, genmod, pid: str, model: str = WAN) -> None:
    """컷 8개 × 2초 한 테이크(16초). 조각 상한 4초 → 4조각(c1·c2 | c3·c4 | c5·c6 | c7·c8)."""
    env.make_plan(pid, [{"id": "s1", "cutIds": [f"c{n}" for n in range(1, 9)], "seconds": 16}])

    def mutator(data: dict) -> None:
        data["input"]["videoModel"] = genmod.vpoc_settings.FAL_ID_PREFIX + model
        for cut in data["conti"]["cuts"]:
            cut["dialogueKo"] = f'에이: "대사{cut["no"]}번."'
            brief = data["previz"]["shotBrief"]["cuts"][cut["id"]]
            brief["dialogue"] = [{"speaker": "에이", "lineKo": f"대사{cut['no']}번.", "deliveryEn": "calm"}]
            brief["actionEn"] = "Kim nods once."

    env.planmod.mutate_plan(env.owner, pid, mutator)


def _go(env, client, genmod, pid: str) -> dict:
    resp = client.post(f"/api/vnew/p/{pid}/generate", json={"confirmUsd": genmod.estimate(env.plan(pid))["totalUsd"]})
    if resp.status_code != 202:
        raise AssertionError(f"{pid}: 생성 시작 {resp.status_code} {str(resp.get_json())[:300]}")
    return env.drive(pid)


def _web(genmod, g: dict) -> dict:
    return genmod._previz_cells(g)[0].get("webtoon") or {}  # noqa: SLF001


# ---------------------------------------------------------------- (ak-d) 실사 사다리 sha
def _real_blob(env, client, genmod, policy, pid: str) -> tuple[str, dict]:
    """웹툰(Wan) 끝 → 실사 회차 → 실사 조각 2(4초~) Seedance/Wan 단계 모두 likeness 거절. 호출 순서·원장·칸 상태 한 덩어리."""
    policy["rule"] = lambda req: None
    _make(env, genmod, pid)
    g0 = _go(env, client, genmod, pid)
    if _web(genmod, g0).get("status") != "completed":
        raise AssertionError(f"{pid}: 웹툰 테이크가 끝나지 않았습니다 {_web(genmod, g0).get('status')}")
    policy["rule"] = lambda req: LIKENESS_RAW if _real_req(req) and _start_of(req) == 4.0 else None
    since, led0 = len(env.requests), len(env.ledger)
    resp = client.post(f"/api/vnew/p/{pid}/generate/real", json={"confirmUsd": genmod.real_estimate(g0)["usd"]})
    g = env.drive(pid)
    policy["rule"] = lambda req: None
    calls = []
    for r in env.requests[since:]:
        body = r["payload"] or {}
        calls.append({"endpoint": str(r["url"]).rsplit("/", 3)[-3:], "real": _real_req(r), "start": _start_of(r),
                      "keys": sorted(k for k in body if k != "seed"), "images": len(body.get("image_urls") or []),
                      "elements": hashlib.sha256(json.dumps(body.get("elements") or [], sort_keys=True).encode()).hexdigest()[:16],
                      "startImage": hashlib.sha256(str(body.get("start_image_url") or "").encode()).hexdigest()[:16],
                      "prompt": hashlib.sha256(_prompt(r).encode("utf-8")).hexdigest()[:16]})
    real = genmod._previz_cells(g)[0].get("real") or {}  # noqa: SLF001
    blob = {"status": resp.status_code, "calls": calls,
            "ledger": [(c.get("kind"), c.get("model"), round(float(c.get("usd") or 0), 4)) for c in env.ledger[led0:]],
            "cell": {k: real.get(k) for k in ("status", "error", "failureKind", "attempt")},
            "parts": [(p.get("status"), p.get("kind"), p.get("cutIds"), p.get("likenessStep"), p.get("replacedBy"))
                      for p in real.get("parts") or []],
            "web": sorted(_web(genmod, g)), "final": (g.get("finalReal") or {}).get("status")}
    return hashlib.sha256(json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(), blob


def _real_blobs() -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    with _env() as (env, client, genmod, policy):
        out["ladder"] = _real_blob(env, client, genmod, policy, "prj_ak_d")
    with _env(VNEW_TAKE_REAL_LIKENESS_LADDER=False) as (env, client, genmod, policy):
        out["ladder-off"] = _real_blob(env, client, genmod, policy, "prj_ak_d2")
    with _env(VNEW_TAKE_REAL_KLING_MODEL="") as (env, client, genmod, policy):
        out["kling-off"] = _real_blob(env, client, genmod, policy, "prj_ak_d3")
    return out


def _baseline() -> None:
    for name, (sha, blob) in _real_blobs().items():
        kling = sum(1 for c in blob["calls"] if "kling-video" in "/".join(c["endpoint"]))
        print(f"{name}: {sha} calls={len(blob['calls'])} kling={kling} cell={blob['cell']} parts={blob['parts']}"[:900])


if __name__ == "__main__" and "--baseline" in sys.argv:
    _baseline()
    sys.exit(0)


# ---------------------------------------------------------------- 실행 묶음
def _run(env, client, genmod, policy, pid: str, rule, *, model: str = WAN) -> dict:
    policy["rule"] = rule
    _make(env, genmod, pid, model)
    led0, since, merged0 = len(env.ledger), len(env.requests), len(policy["merged"])
    g = _go(env, client, genmod, pid)
    news = env.requests[since:]
    out = {"g": g, "web": _web(genmod, g), "reqs": news, "ledger": env.ledger[led0:], "merged": policy["merged"][merged0:],
           "by_start": {s: [r for r in news if _start_of(r) == s] for s in (0.0, 4.0, 8.0, 12.0)},
           "kling": [r for r in news if _is(r, KLING)], "pid": pid}
    out["parts"] = out["web"].get("parts") or []
    return out


def _rule_a(req: dict):
    """Wan 조각 3(8초~) 콘텐츠 거절 · Seedance 조각 3 likeness 거절."""
    if _is(req, WAN) and _start_of(req) == 8.0:
        return CONTENT_RAW
    if _is(req, _seed()) and _start_of(req) == 8.0:
        return LIKENESS_RAW
    return None


def _shape(parts: list[dict]) -> list[tuple]:
    return [(p.get("status"), p.get("kind"), list(p.get("cutIds") or []), p.get("replacedBy")) for p in parts]


WANT_SHAPE = [("completed", None, ["c1", "c2"], None), ("completed", None, ["c3", "c4"], None),
              ("superseded", None, ["c5", "c6"], "kling-cuts"), ("completed", "kling", ["c5"], None),
              ("completed", "kling", ["c6"], None), ("completed", None, ["c7", "c8"], None)]


def _kling_bad(genmod, r: dict, tag: str) -> list[str]:
    """Kling 요청: 컷마다 1번(c5 · c6) · 웹툰 정면/시트 elements · 웹툰 장소 · 애니 STYLE · 실사 글 없음 · 프리비즈 줄 없음."""
    bad: list[str] = []
    if len(r["kling"]) != 2:
        bad.append(f"{tag} Kling 요청이 2번(c5 · c6)이 아닙니다: {len(r['kling'])}")
    style = genmod.VIBE_STYLE["webtoon"]
    for req in r["kling"]:
        body = req["payload"] or {}
        prompt = _prompt(req)
        fronts = sorted(_decode(e.get("frontal_image_url")) for e in body.get("elements") or [])
        sheets = sorted(_decode(u) for e in body.get("elements") or [] for u in e.get("reference_image_urls") or [])
        locs = [_decode(u) for u in body.get("image_urls") or []]
        if fronts != [b"WF1", b"WF2"] or sheets != [b"WS1", b"WS2"]:
            bad.append(f"{tag} elements 가 웹툰 정면 · 시트가 아닙니다: {fronts} {sheets}")
        if locs != [b"LOC-WB"]:
            bad.append(f"{tag} 장소가 웹툰 장소 뷰가 아닙니다: {locs}")
        if style not in prompt or genmod.ANIME_NO_SUBTITLES not in prompt or "@Element2" not in prompt:
            bad.append(f"{tag} 애니 STYLE · 자막 금지 · @Element 줄이 없습니다: {prompt[:300]!r}")
        hits = [w for w in PHOTOREAL_WORDS if w.lower() in prompt.lower()]
        if hits or "@Video1" in prompt or body.get("start_image_url") or "video_urls" in body:
            bad.append(f"{tag} 실사 글 · 프리비즈 줄 · 시작 그림 · 영상이 있습니다: {hits} {sorted(body)}")
    return bad


_CACHE: dict = {}


def _run_a() -> dict:
    if "a" not in _CACHE:
        with _env() as (env, client, genmod, policy):
            r = _run(env, client, genmod, policy, "prj_ak_a", _rule_a)
            r["notes"] = [n for n in genmod.vnew_model_notes.read_notes(env.owner, r["pid"], str(r["g"].get("genId") or ""))
                          if n.get("variant") == "webtoon"]
            body = client.get(f"/api/vnew/p/{r['pid']}/plan").get_json() or {}
            gv = (body.get("plan") or {}).get("gen") or body.get("gen") or {}
            r["view"] = ((gv.get("units") or [{}])[0].get("webtoon") or {})
            r["unit_price"] = genmod._previz_model_unit_price(KLING)  # noqa: SLF001
            _CACHE["a"] = r
    return _CACHE["a"]


# ---------------------------------------------------------------- (ak-a)
def group_a() -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    r = _run_a()
    bad: list[str] = []
    web = r["web"]
    if web.get("status") != "completed" or not web.get("outputKey"):
        bad.append(f"테이크가 끝나지 않았습니다: {web.get('status')} {str(web.get('error'))[:200]!r}")
    counts = {s: _names(r["by_start"][s]) for s in r["by_start"]}
    want = {0.0: ["wan"], 4.0: ["wan"], 8.0: ["wan", "wan", "seedance"], 12.0: ["wan"]}
    if counts != want:
        bad.append(f"조각별 요청(조각 1 · 2 · 4 는 그대로, 조각 3 = Wan · 순화 Wan · Seedance)이 다릅니다: {counts}")
    if _shape(r["parts"]) != WANT_SHAPE:
        bad.append(f"조각 기록이 다릅니다: {_shape(r['parts'])}")
    if r["merged"] != [5]:
        bad.append(f"잇기(조각 1 · 2 · Kling 2 · 조각 4 = 5)가 아닙니다: {r['merged']}")
    if len(r["reqs"]) != 8:
        bad.append(f"전체 요청이 8번이 아닙니다: {len(r['reqs'])}")
    return bad + _kling_bad(genmod, r, "")


# ---------------------------------------------------------------- (ak-b)
def group_b() -> list[str]:
    bad: list[str] = []
    with _env() as (env, client, genmod, policy):
        r = _run(env, client, genmod, policy, "prj_ak_b", lambda q: LIKENESS_RAW if _is(q, KLING) else _rule_a(q))
        web = r["web"]
        err = str(web.get("error") or "")
        if web.get("status") != "failed" or not err.startswith("테이크 조각 3/4 이 실패했습니다: ") \
                or not err.endswith(" " + KLING_FAIL_TAIL + ".") or genmod.vpoc_fal._REJECT_LIKENESS_MSG not in err:  # noqa: SLF001
            bad.append(f"지금 문구 + Kling 문구로 실패하지 않았습니다: {web.get('status')} {err!r}")
        if web.get("failureKind") != "likeness" or len(r["kling"]) != 1:
            bad.append(f"Kling 1번 · likeness 가 아닙니다: {web.get('failureKind')!r} kling={len(r['kling'])}")
        since = len(env.requests)
        env.drive(r["pid"])
        if len(env.requests) != since:
            bad.append(f"실패 뒤에도 제출했습니다: {len(env.requests) - since}")
        if any(p.get("status") == "pending" for p in _web(genmod, env.gen(r["pid"])).get("parts") or []):
            bad.append("pending 조각이 남았습니다")
    return bad


# ---------------------------------------------------------------- (ak-c)
def group_c() -> list[str]:
    bad: list[str] = []
    seed = _seed()
    with _env() as (env, client, genmod, policy):
        rule = lambda q: LIKENESS_RAW if _is(q, seed) else (CONTENT_RAW if _is(q, WAN) and _start_of(q) == 8.0 else None)  # noqa: E731
        r = _run(env, client, genmod, policy, "prj_ak_c", rule, model=seed)
        web = r["web"]
        seeds = [q for q in r["reqs"] if _is(q, seed)]
        wans = [q for q in r["reqs"] if _is(q, WAN)]
        if not r["reqs"] or not _is(r["reqs"][0], seed) or (web.get("animeFallback") or {}).get("to") != WAN:
            bad.append(f"228 Wan 대체가 먼저가 아닙니다: {web.get('animeFallback')} {[q['url'][-30:] for q in r['reqs'][:2]]}")
        if len(seeds) != 2 or [_start_of(q) for q in seeds][1:] != [8.0] or len(wans) != 5:
            bad.append(f"요청(Seedance 2 · Wan 5)이 다릅니다: seed={[_start_of(q) for q in seeds]} wan={[_start_of(q) for q in wans]}")
        if web.get("status") != "completed" or _shape(r["parts"]) != WANT_SHAPE:
            bad.append(f"Kling 컷 단위로 끝나지 않았습니다: {web.get('status')} {str(web.get('error'))[:160]!r} {_shape(r['parts'])}")
        bad += _kling_bad(genmod, r, "(228 뒤)")
    # (c) 콘텐츠 단계 끝(대체 모델 없음: 순화 → 센 순화 → 끝) → Kling
    with _env(VNEW_CONTENT_FALLBACK_MODEL="") as (env, client, genmod, policy):
        r = _run(env, client, genmod, policy, "prj_ak_c2", lambda q: CONTENT_RAW if _is(q, WAN) and _start_of(q) == 8.0 else None)
        web = r["web"]
        origin = next((p for p in r["parts"] if p.get("replacedBy") == "kling-cuts"), {})
        if web.get("status") != "completed" or _shape(r["parts"]) != WANT_SHAPE or len(r["by_start"][8.0]) != 3 \
                or (origin.get("animeKling") or {}).get("reason") != "contentFlag":
            bad.append(f"(사다리 끝) Kling 으로 끝나지 않았습니다: {web.get('status')} {_shape(r['parts'])} "
                       f"{len(r['by_start'][8.0])} {origin.get('animeKling')}")
    return bad


# ---------------------------------------------------------------- (ak-d)
def group_d() -> list[str]:
    bad: list[str] = []
    for name, (sha, blob) in _real_blobs().items():
        if sha != REAL_LADDER_SHA[name]:
            bad.append(f"실사 사다리({name}) 호출 순서 sha 가 바뀌었습니다: {sha} != {REAL_LADDER_SHA[name]} "
                       f"{json.dumps(blob, ensure_ascii=False, default=str)[:300]}")
    return bad


# ---------------------------------------------------------------- (ak-e)
def group_e() -> list[str]:
    bad: list[str] = []
    want = None
    for label, pins in (("설정 끔", {"VNEW_ANIME_KLING_FALLBACK": False}), ("Kling 모델 없음", {"VNEW_TAKE_REAL_KLING_MODEL": ""})):
        with _env(**pins) as (env, client, genmod, policy):
            want = "테이크 조각 3/4 이 실패했습니다: " + genmod.vpoc_fal._REJECT_LIKENESS_MSG  # noqa: SLF001
            r = _run(env, client, genmod, policy, "prj_ak_e", _rule_a)
            web = r["web"]
            if web.get("status") != "failed" or web.get("error") != want or r["kling"] \
                    or any(p.get("animeKling") for p in r["parts"]) or "animeKling" in web:
                bad.append(f"{label}: 지금처럼 실패하지 않았습니다: {web.get('status')} {str(web.get('error'))[:200]!r} "
                           f"kling={len(r['kling'])}")
        with _env(VNEW_CONTENT_FALLBACK_MODEL="", **pins) as (env, client, genmod, policy):
            r = _run(env, client, genmod, policy, "prj_ak_e2", lambda q: CONTENT_RAW if _is(q, WAN) and _start_of(q) == 8.0 else None)
            text = genmod.CONTENT_FLAG_FAIL_TEXT.format(cuts="5…6")
            if r["web"].get("status") != "failed" or r["web"].get("error") != text or r["kling"]:
                bad.append(f"{label}(사다리 끝): 지금처럼 실패하지 않았습니다: {r['web'].get('status')} "
                           f"{str(r['web'].get('error'))[:200]!r}")
    return bad


# ---------------------------------------------------------------- (ak-f)
def group_f() -> list[str]:
    r = _run_a()
    bad: list[str] = []
    rows = sorted((c.get("model"), round(float(c.get("usd") or 0), 4), round(float(c.get("units") or 0), 3))
                  for c in r["ledger"] if c.get("kind") == "video" and c.get("model") == KLING)
    if abs(r["unit_price"] - KLING_PRICE) > 1e-9 or rows != [(KLING, round(KLING_PRICE * 5, 4), 5.0)] * 2:
        bad.append(f"Kling 원장(초당 {KLING_PRICE} × 5초 × 2)이 다릅니다: {rows} 단가={r['unit_price']}")
    origin = next((p for p in r["parts"] if p.get("replacedBy") == "kling-cuts"), {})
    ak = origin.get("animeKling") or {}
    if ak.get("cutIds") != ["c5", "c6"] or not str(ak.get("at") or "") or ak.get("model") != KLING:
        bad.append(f"animeKling 기록이 다릅니다: {ak}")
    acts = [(n.get("model"), n.get("action")) for n in r["notes"]]
    refusal = [n for n in r["notes"] if n.get("model") == _seed() and n.get("action") == "fallbackModel:" + KLING]
    if len(refusal) != 1 or refusal[0].get("providerMessage") != LIKENESS_MSG() \
            or any(a[1] == "gaveUp" for a in acts):
        bad.append(f"모델 노트(Seedance likeness → fallbackModel:Kling)가 다릅니다: {acts}")
    view = r["view"].get("animeKling") or {}
    if view.get("text") != KLING_TEXT:
        bad.append(f"화면 한 줄이 다릅니다: {view}")
    vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
    if vjs.count("v.animeKling && v.animeKling.text") != 1:
        bad.append("vnew.js 에 animeKling 한 줄이 한 번 있지 않습니다")
    return bad


# ---------------------------------------------------------------- (ak-g) 변이
MUTANTS = {
    "every-part": ("모든 조각 Kling", "_submit_take_parts",
                   "            if not real and old and _anime_kling_of(old):\n",
                   "            if not real and old is not None:\n"),
    "photoreal": ("애니 Kling 에 실사 글", "_anime_kling_prompt", "real=False", "real=True"),
}


@contextlib.contextmanager
def _mutant(genmod, name: str, old: str, new: str):
    fn = getattr(genmod, name)
    src = inspect.getsource(fn)
    if src.count(old) != 1:
        raise AssertionError(f"변이 자리를 찾지 못했습니다: {name} {old!r}")
    code = compile(src.replace(old, new), genmod.__file__, "exec", flags=_future.annotations.compiler_flag, dont_inherit=True)
    exec(code, genmod.__dict__)  # noqa: S102
    try:
        yield
    finally:
        setattr(genmod, name, fn)


def group_g(caught: list[str]) -> list[str]:
    from src.vnew import generate as genmod  # noqa: PLC0415

    bad: list[str] = []
    for key, (title, name, old, new) in MUTANTS.items():
        _CACHE.pop("a", None)
        with _mutant(genmod, name, old, new):
            try:
                found = group_a()
            except Exception as exc:  # noqa: BLE001
                found = [f"예외 {type(exc).__name__}"]
        _CACHE.pop("a", None)
        if found:
            caught.append(key)
        else:
            bad.append(f"변이 {key}({title})를 ak-a 가 잡지 못했습니다")
    return bad


# ---------------------------------------------------------------- (ak-h)
def group_h(warns: list[str]) -> list[str]:
    bad = [f"{rel} 에 CR 이 있습니다" for rel in CHANGED if (ROOT / rel).exists() and b"\r" in (ROOT / rel).read_bytes()]
    node = None if NO_NODE else shutil.which("node")
    if not node:
        warns.append("node 가 없어 node --check vnew.js 를 건너뜁니다")
        return bad
    out = subprocess.run([node, "--check", str(PUBLIC / "vnew.js")], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        bad.append("node --check vnew.js 실패: " + (out.stderr or out.stdout)[-400:])
    return bad


def check_anime_kling_229() -> bool:
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    warns: list[str] = []
    for group, fn in (("ak-a", group_a), ("ak-b", group_b), ("ak-c", group_c), ("ak-d", group_d), ("ak-e", group_e),
                      ("ak-f", group_f), ("ak-g", lambda: group_g(caught)), ("ak-h", lambda: group_h(warns))):
        try:
            results[group] = fn()
        except Exception:  # noqa: BLE001
            results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    _CACHE.clear()
    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 229 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "ak-g" else ""
            print(f"[ok  ] 작업 229 ({group}) {title}{extra}")
    for warn in warns:
        print(f"[WARN] 작업 229 (ak-h) {warn}")
    return passed


if __name__ == "__main__":
    NO_NODE = "--no-node" in sys.argv
    _ok = check_anime_kling_229()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
