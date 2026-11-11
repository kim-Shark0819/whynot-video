"""작업 228 — 애니(웹툰) 테이크가 likeness(실존 인물 닮음)로 거절되면 Wan 3.0 으로 1번 자동 대체. 가짜 fal·저장소·원장, 유료 0.

  (al-a) Seedance 애니 테이크 조각 1 이 likeness 거절 → 테이크 전체를 Wan 3.0 으로 조각 1부터 다시 내고 성공 · animeFallback{from,to,at} ·
         대체 원장 = Wan 모델 · Wan 단가 · 화면 한 줄(gen 보기 animeFallback.text) · 모델 노트(거절 + 대체 action) · 프롬프트 기록
         (Seedance failed + Wan completed) · gen.videoModel · input.videoModel · 견적 그대로. 20초 테이크는 Wan 상한(15초)으로 조각 2개
  (al-b) 대체도 거절 → 지금 문구로 실패 · 대체 1번(Wan 요청 1) · 노트에 포기 1줄
  (al-c) 실사 테이크 likeness 거절 → 실사 사다리 그대로(호출 순서 sha 를 고치기 전에 고정) · 사다리 끔도 그대로 · Wan 0
  (al-d) 설정이 비었으면 · 틀렸으면(등록표 없음 · 테이크 모델 아님) 지금처럼 실패 · 틀린 값은 경고 1줄
  (al-e) 테이크가 대체 모델 상한(최대 길이 · 나누기 끔의 참고 영상)을 넘으면 대체하지 않음 · 이유 로그
  (al-f) 이미 Wan 인 애니 테이크가 거절 → 대체 없음
  (al-g) 변이 "실사에도 대체" · "횟수 제한 없음" 을 검사가 잡는다(검사 안에서만 바꾼다 — 파일은 그대로)
  (al-h) 바꾼 파일 LF 줄 끝 · node --check vnew.js(node 가 없으면 WARN)

실행: python -X utf8 -B tools/check_anime_likeness_228.py   (고치기 전 sha: --baseline)
check_vpoc 훅 줄(직접 넣지 않는다 — 아래 HOOK 을 출력한다).
"""

from __future__ import annotations

import __future__ as _future
import contextlib
import hashlib
import inspect
import json
import logging
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

HOOK = ('    if not __import__("check_anime_likeness_228").check_anime_likeness_228(): '
        'fail("작업 228: 애니 테이크 likeness 거절 → Wan 3.0 1번 대체 검사 실패")')
GROUPS = {
    "al-a": "Seedance 애니 likeness 거절 → Wan 3.0 으로 조각 1부터 다시 · 기록 · 원장 · 화면 한 줄 · 노트",
    "al-b": "대체도 거절 → 지금 문구로 실패 · 대체 1번",
    "al-c": "실사 likeness 사다리 그대로(호출 순서 sha 고정) · 대체 없음",
    "al-d": "설정 빔 · 틀림 → 지금처럼 실패(틀림은 경고)",
    "al-e": "대체 모델 상한을 넘는 테이크 → 대체 없음 · 이유 로그",
    "al-f": "이미 Wan 인 애니 테이크 → 대체 없음",
    "al-g": "변이(실사에도 대체 · 횟수 제한 없음)를 검사가 잡음",
    "al-h": "LF 줄 끝 · node --check vnew.js",
}
CHANGED = ("app/src/vnew/generate.py", "app/src/vnew/settings.py", "frontend/public/vnew.js",
           "tools/check_anime_likeness_228.py")
WAN = "alibaba/wan-3.0/reference-to-video"
WAN_PRICE = 0.0105  # 가짜 Wan "1000 tokens" 단가(Seedance 가짜 0.0214 와 다르다)
WAN_CAPS = {
    "promptField": "prompt", "videoListField": "video_urls", "videoListMax": 3, "imageListField": "image_urls",
    "imageListMax": 10, "durations": [5, 10, 15, 20, 25, 30], "durationAsString": False, "sendDuration": True,
    "resolutions": ["480p", "720p"], "previzCapable": True, "available": True,
}
KLING_I2V = "fal-ai/kling-video/v3/pro/image-to-video"
LIKENESS_RAW = "The images or videos provided may contain likenesses of real people or other private information."
# 고치기 전(작업 228 전) 코드에서 --baseline 으로 뽑은 실사 사다리 호출 순서 sha(al-c). 바꾸면 안 된다.
REAL_LADDER_SHA = {
    "ladder": "20e6a5cf2c6fd289da4aee3409a0b7b698edbb5d15eea8cb858e6d00e743ea8f",
    "ladder-off": "d23ee56bba094e2a7a7ac119760f138d400a4ee7961afba15afea67d5725ada5",
}
REAL_STYLE_MARK = "Cinematic live-action film footage"
NO_NODE = False


class _Logs(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg))


@contextlib.contextmanager
def _scenario(owner: str = "u228", *, fallback=None, wan_caps: dict | None = None, **pins):
    """_LrEnv(check_vpoc) + 가짜 Wan 등록 · 단가 · 조각 자르기 · 잇기 + likeness 거절 정책. 나오면 모두 되돌린다."""
    import check_vpoc as cv  # noqa: PLC0415

    values = dict(VNEW_FX=True, VNEW_CASTING=True, VNEW_TAKE_REAL=True, VNEW_GEN_PARALLEL=2, POLL_INTERVAL_S=0,
                  VNEW_TAKE_AUDIO=True, VNEW_TAKE_RESOLUTION="480p", VNEW_PREPARE_STALE_S=300,
                  VNEW_TAKE_REAL_LIKENESS_LADDER=True, VNEW_TAKE_REAL_KLING_MODEL="", VNEW_REAL_CUT_QA=False,
                  VNEW_TAKE_MAX_SECONDS=30, VNEW_TAKE_REF_SPLIT=True, VNEW_PART_CONTINUITY=True, VNEW_MODEL_NOTES=True,
                  VNEW_EDIT_RETAKE=True, VNEW_TEXT_CHECK=False)
    values.update(pins)
    from src.vnew import settings as vset  # noqa: PLC0415

    if hasattr(vset, "VNEW_ANIME_LIKENESS_FALLBACK_MODEL"):
        values["VNEW_ANIME_LIKENESS_FALLBACK_MODEL"] = WAN if fallback is None else fallback
    logs = _Logs()
    vlog = logging.getLogger("vpoc")
    old_level = vlog.level
    vlog.addHandler(logs)
    vlog.setLevel(logging.INFO)
    try:
        with cv._guard_real_llm(), cv._LrEnv(owner) as env, cv._pin_vnew(**values):  # noqa: SLF001
            genmod = env.genmod
            pfx = genmod.vpoc_settings.FAL_ID_PREFIX
            caps = dict(WAN_CAPS if wan_caps is None else wan_caps)
            seed_find, seed_price, seed_price_e = genmod.vpoc_fal.find, genmod.vpoc_fal.pricing_for, genmod.vpoc_fal.pricing
            kling_caps = {"promptField": "prompt", "startImageField": "start_image_url", "durations": [5, 10],
                          "available": True}

            def find(model):
                if str(model) == pfx + WAN:
                    return {"id": pfx + WAN, "endpointId": WAN, "category": "reference-to-video", "caps": dict(caps)}
                if str(model) == pfx + KLING_I2V:
                    return {"id": pfx + KLING_I2V, "endpointId": KLING_I2V, "caps": dict(kling_caps)}
                return seed_find(model)

            wan_price = {"unit": "1000 tokens", "unitPrice": WAN_PRICE}
            env.set(genmod.vpoc_fal, "find", find)
            env.set(genmod.vpoc_fal, "pricing_for", lambda m: dict(wan_price) if str(m) == pfx + WAN else seed_price(m))
            env.set(genmod.vpoc_fal, "pricing", lambda e: dict(wan_price) if str(e) == WAN else seed_price_e(e))
            env.set(genmod, "_cut_clip", lambda mp4, unit: {
                "bytes": b"CLIP:%.3f-%.3f:" % (float(unit["startSec"]), float(unit["endSec"])) + bytes(mp4)[:20],
                "origSeconds": round(float(unit["endSec"]) - float(unit["startSec"]), 3),
                "sentSeconds": round(float(unit["endSec"]) - float(unit["startSec"]), 3), "padded": False})
            env.set(genmod, "_mp4_seconds", lambda mp4: 0.0)
            env.set(genmod, "_conform_concat", lambda pieces, **_kw: b"PARTS:%d" % len(pieces))
            policy = {"reject": lambda req: False, "rejected": []}
            base_fal = env._fal  # noqa: SLF001

            def fal_request(method, url, payload=None, *, where=""):
                rid = url.split("/requests/", 1)[-1].split("/", 1)[0]
                req = next((r for r in env.requests if r["rid"] == rid), None)
                if method == "GET" and not url.endswith("/status") and req and policy["reject"](req):
                    policy["rejected"].append(rid)
                    raise env.VpocError("fal.poll", "HTTP422", LIKENESS_RAW, 502)
                return base_fal(method, url, payload, where=where)

            env.set(env.falmod, "_request", fal_request)
            yield env, env.client(), genmod, policy, logs
    finally:
        vlog.removeHandler(logs)
        vlog.setLevel(old_level)


def _is(req: dict, endpoint: str) -> bool:
    return str(req.get("url") or "").endswith("/" + endpoint)


def _real_req(req: dict) -> bool:
    """실사 테이크 요청인가(실사 STYLE 문장). _LrEnv 의 kind 는 "STYLE:" 머리를 보는데 지금 실사 프롬프트는 그 머리 없이 시작한다."""
    return REAL_STYLE_MARK in str((req.get("payload") or {}).get("prompt") or "")


def _seed_anime(req: dict) -> bool:
    import check_vpoc as cv  # noqa: PLC0415

    return _is(req, cv._LR_ENDPOINT) and not _real_req(req)  # noqa: SLF001


def _seed_real(req: dict) -> bool:
    import check_vpoc as cv  # noqa: PLC0415

    return _is(req, cv._LR_ENDPOINT) and _real_req(req)  # noqa: SLF001


def _segs(total: int = 12) -> list[dict]:
    if total == 20:
        return [{"id": "s1", "cutIds": ["c1"], "seconds": 8}, {"id": "s2", "cutIds": ["c2"], "seconds": 12}]
    return [{"id": "s1", "cutIds": ["c1"], "seconds": 4}, {"id": "s2", "cutIds": ["c2"], "seconds": 8}]


def _start(env, client, genmod, pid: str, *, total: int = 12, model: str = "") -> dict:
    env.make_plan(pid, _segs(total))
    if model:
        def mutator(data: dict) -> None:
            data["input"]["videoModel"] = genmod.vpoc_settings.FAL_ID_PREFIX + model

        env.planmod.mutate_plan(env.owner, pid, mutator)
    usd = genmod.estimate(env.plan(pid))["totalUsd"]
    resp = client.post(f"/api/vnew/p/{pid}/generate", json={"confirmUsd": usd})
    if resp.status_code != 202:
        raise AssertionError(f"{pid}: 생성 시작 {resp.status_code} {str(resp.get_json())[:300]}")
    return env.drive(pid)


def _web(g: dict) -> dict:
    return ((g.get("units") or [{}])[0]).get("webtoon") or {}


def _notes(env, genmod, pid: str) -> list[dict]:
    return genmod.vnew_model_notes.read_notes(env.owner, pid, str(env.gen(pid).get("genId") or ""))


def _video_rows(env, since: int) -> list[tuple]:
    return [(c.get("model"), round(float(c.get("usd") or 0), 4), round(float(c.get("units") or 0), 3))
            for c in env.ledger[since:] if c.get("kind") == "video"]


def _fallback_text(genmod) -> str:
    return str(getattr(genmod, "ANIME_FALLBACK_TEXT", ""))


# ---------------------------------------------------------------- (al-a)
def group_a() -> list[str]:
    bad: list[str] = []
    want_text = "Seedance가 인물 그림을 실존 인물로 오인해 거절해서, 이 테이크는 Wan 3.0으로 다시 만들었습니다."
    with _scenario() as (env, client, genmod, policy, logs):
        import check_vpoc as cv  # noqa: PLC0415

        seed = cv._LR_ENDPOINT  # noqa: SLF001
        if _fallback_text(genmod) != want_text:
            bad.append(f"ANIME_FALLBACK_TEXT 가 다릅니다: {_fallback_text(genmod)!r}")
        for total, pid in ((12, "prj_al_a"), (20, "prj_al_a20")):
            policy["reject"] = _seed_anime
            env.make_plan(pid, _segs(total))
            est0 = genmod.estimate(env.plan(pid))
            usd = est0["totalUsd"]
            led0, since = len(env.ledger), len(env.requests)
            resp = client.post(f"/api/vnew/p/{pid}/generate", json={"confirmUsd": usd})
            g = env.drive(pid)
            news = env.requests[since:]
            seeds = [r for r in news if _seed_anime(r)]
            wans = [r for r in news if _is(r, WAN)]
            web = _web(g)
            fb = web.get("animeFallback") or {}
            tag = f"({total}초)"
            if resp.status_code != 202 or len(seeds) != 1 or len(news) != len(seeds) + len(wans):
                bad.append(f"{tag} Seedance 애니 제출 1번이 아닙니다: {resp.status_code} {len(seeds)} {len(news)}")
            want_wan = 1 if total == 12 else 2
            if len(wans) != want_wan or any(_real_req(r) for r in wans):
                bad.append(f"{tag} Wan 제출이 {want_wan}번(애니)이 아닙니다: {[(r['url'][-40:], r['kind']) for r in wans]}")
            if web.get("status") != "completed" or not web.get("outputKey"):
                bad.append(f"{tag} 대체 테이크가 끝나지 않았습니다: {web.get('status')} {web.get('error')!r}")
            if fb.get("from") != seed or fb.get("to") != WAN or not str(fb.get("at") or ""):
                bad.append(f"{tag} animeFallback 이 다릅니다: {fb}")
            parts = web.get("parts") or []
            if total == 20:
                if [p.get("status") for p in parts] != ["completed", "completed"] \
                        or [round(float(p.get("seconds") or 0)) for p in parts] != [8, 12] \
                        or any(not str(p.get("invocationArn") or "").count(WAN) for p in parts):
                    bad.append(f"{tag} Wan 조각(8·12초, Wan 작업 id)이 아닙니다: "
                               f"{[(p.get('status'), p.get('seconds'), str(p.get('invocationArn'))[-50:]) for p in parts]}")
            elif parts:
                bad.append(f"{tag} 통 테이크인데 조각 기록이 있습니다: {len(parts)}")
            # 원장: 거절된 Seedance 제출은 지금처럼(제출 때 1줄), 대체 제출은 Wan 모델 · Wan 단가.
            rows = _video_rows(env, led0)
            secs = [12.0] if total == 12 else [8.0, 12.0]
            want_rows = [(seed, genmod._take_usd(seed, float(total), "480p"), float(total))] + [  # noqa: SLF001
                (WAN, genmod._take_usd(WAN, s, "480p"), s) for s in secs]  # noqa: SLF001
            if sorted(rows) != sorted((m, round(u, 4), round(s, 3)) for m, u, s in want_rows):
                bad.append(f"{tag} 원장이 다릅니다: {rows} want {want_rows}")
            if any(abs(genmod._take_usd(WAN, s, "480p") - genmod._take_usd(seed, s, "480p")) < 1e-6 for s in secs):  # noqa: SLF001
                bad.append(f"{tag} 가짜 Wan 단가가 Seedance 와 같아 원장 검사가 무의미합니다")
            plan = env.plan(pid)
            if g.get("videoModel") != seed or plan["input"].get("videoModel") != env.model_id:
                bad.append(f"{tag} 프로젝트 모델이 바뀌었습니다: {g.get('videoModel')} {plan['input'].get('videoModel')}")
            if genmod.estimate(plan)["totalUsd"] != est0["totalUsd"]:
                bad.append(f"{tag} 견적이 바뀌었습니다: {genmod.estimate(plan)['totalUsd']} != {est0['totalUsd']}")
            if (g.get("finalWebtoon") or {}).get("status") != "done":
                bad.append(f"{tag} 웹툰 최종 병합이 끝나지 않았습니다: {g.get('finalWebtoon')} {g.get('status')}")
            gv = ((client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("plan") or {}).get("gen") \
                or ((client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("gen")) or {}
            view_fb = ((gv.get("units") or [{}])[0].get("webtoon") or {}).get("animeFallback") or {}
            if view_fb.get("text") != want_text or view_fb.get("to") != WAN or view_fb.get("from") != seed:
                bad.append(f"{tag} gen 보기 animeFallback 이 다릅니다: {view_fb}")
            notes = [n for n in _notes(env, genmod, pid) if n.get("variant") == "webtoon"]
            refusal = [n for n in notes if n.get("model") == seed and n.get("action") == "fallbackModel:" + WAN]
            if len(refusal) != 1 or "실존 인물" not in str(refusal[0].get("reasonKo")) \
                    or refusal[0].get("providerMessage") != genmod.vpoc_fal._REJECT_LIKENESS_MSG \
                    or any(n.get("action") == "gaveUp" for n in notes):
                bad.append(f"{tag} 모델 노트(거절 + 대체)가 다릅니다: "
                           f"{[(n.get('model'), n.get('kind'), n.get('action')) for n in notes]}")
            log_cells = genmod_retake_log(env, pid)
            entries = [e for k, v in log_cells.items() if k.startswith("webtoon:") for e in v]
            got = [(str(e.get("model")), e.get("status")) for e in entries]
            want_log = [(seed, "failed")] + [(WAN, "completed")] * want_wan
            if got != want_log:
                bad.append(f"{tag} 프롬프트 기록(Seedance failed → Wan completed)이 다릅니다: {got}")
            if not any("vnew.gen.anime_likeness_fallback" in line and f"to={WAN}" in line for line in logs.lines):
                bad.append(f"{tag} 대체 로그 줄이 없습니다")
        vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        if vjs.count("v.animeFallback && v.animeFallback.text") != 1:
            bad.append("vnew.js 에 animeFallback 한 줄이 한 번 있지 않습니다")
    return bad


def genmod_retake_log(env, pid: str) -> dict:
    from src.vnew import retake  # noqa: PLC0415

    return retake.read_log(env.owner, pid, str(env.gen(pid).get("genId") or ""))


# ---------------------------------------------------------------- (al-b)
def _group_b(env, client, genmod, policy) -> list[str]:
    bad: list[str] = []
    counter = {"wan": 0}

    def reject(req):
        if _seed_anime(req):
            return True
        if _is(req, WAN) and req["rid"] not in policy["rejected"]:
            counter["wan"] += 1
            return counter["wan"] <= 3  # 변이(횟수 제한 없음)도 끝나게: Wan 은 3번까지만 거절
        return _is(req, WAN)

    policy["reject"] = reject
    since = len(env.requests)
    g = _start(env, client, genmod, "prj_al_b")
    news = env.requests[since:]
    web = _web(g)
    wans = [r for r in news if _is(r, WAN)]
    if len(wans) != 1 or len([r for r in news if _seed_anime(r)]) != 1:
        bad.append(f"대체가 1번이 아닙니다(Wan {len(wans)}번, 전체 {len(news)}번)")
    if web.get("status") != "failed" or web.get("error") != genmod.vpoc_fal._REJECT_LIKENESS_MSG \
            or web.get("failureKind"):
        bad.append(f"지금 문구로 실패하지 않았습니다: {web.get('status')} {web.get('error')!r} {web.get('failureKind')!r}")
    if (web.get("animeFallback") or {}).get("to") != WAN:
        bad.append(f"animeFallback 기록이 없습니다: {web.get('animeFallback')}")
    notes = [n for n in _notes(env, genmod, "prj_al_b") if n.get("variant") == "webtoon"]
    acts = [(n.get("model"), n.get("action")) for n in notes]
    if acts != [(acts[0][0] if acts else "", "fallbackModel:" + WAN), (WAN, "gaveUp")]:
        bad.append(f"노트가 (거절 → 대체, Wan 포기)가 아닙니다: {acts}")
    return bad


def group_b() -> list[str]:
    with _scenario() as (env, client, genmod, policy, _logs):
        return _group_b(env, client, genmod, policy)


# ---------------------------------------------------------------- (al-c)
def _real_blob(env, client, genmod, policy, pid: str) -> tuple[str, dict]:
    """웹툰 끝 → 실사 회차 시작 → 실사 Seedance likeness 거절. 호출 순서·원장·칸 상태를 한 덩어리로."""
    policy["reject"] = lambda req: False
    g0 = _start(env, client, genmod, pid)
    if (g0.get("finalWebtoon") or {}).get("status") != "done":
        raise AssertionError(f"{pid}: 웹툰 통 테이크가 끝나지 않았습니다 {g0.get('status')}")
    policy["reject"] = _seed_real
    since, led0 = len(env.requests), len(env.ledger)
    usd = genmod.real_estimate(g0)["usd"]
    resp = client.post(f"/api/vnew/p/{pid}/generate/real", json={"confirmUsd": usd})
    g = env.drive(pid)
    calls = [{"endpoint": str(r["url"]).rsplit("/", 3)[-3:], "real": _real_req(r), "keys": sorted(r["payload"] or {}),
              "images": len((r["payload"] or {}).get("image_urls") or []),
              "prompt": hashlib.sha256(str((r["payload"] or {}).get("prompt") or "").encode("utf-8")).hexdigest()[:16]}
             for r in env.requests[since:]]
    real = (g.get("units") or [{}])[0].get("real") or {}
    blob = {"status": resp.status_code, "calls": calls,
            "ledger": [(c.get("kind"), c.get("model"), round(float(c.get("usd") or 0), 4)) for c in env.ledger[led0:]],
            "cell": {k: real.get(k) for k in ("status", "error", "failureKind", "fallback")},
            "web": sorted(_web(g)), "final": (g.get("finalReal") or {}).get("status")}
    return hashlib.sha256(json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(), blob


def _real_blobs() -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    with _scenario() as (env, client, genmod, policy, _logs):
        out["ladder"] = _real_blob(env, client, genmod, policy, "prj_al_c")
    with _scenario(VNEW_TAKE_REAL_LIKENESS_LADDER=False) as (env, client, genmod, policy, _logs):
        out["ladder-off"] = _real_blob(env, client, genmod, policy, "prj_al_c2")
    return out


def group_c() -> list[str]:
    bad: list[str] = []
    for name, (sha, blob) in _real_blobs().items():
        if sha != REAL_LADDER_SHA[name]:
            bad.append(f"실사 사다리({name}) 호출 순서 sha 가 바뀌었습니다: {sha} != {REAL_LADDER_SHA[name]} "
                       f"{json.dumps(blob, ensure_ascii=False, default=str)[:400]}")
        if any(WAN in "/".join(c["endpoint"]) for c in blob["calls"]) or "animeFallback" in blob["web"]:
            bad.append(f"실사({name})에서 Wan 대체가 쓰였습니다")
        if (blob["cell"] or {}).get("status") != "failed":
            bad.append(f"실사({name}) 칸이 실패로 끝나지 않았습니다: {blob['cell']}")
    return bad


# ---------------------------------------------------------------- (al-d)
def group_d() -> list[str]:
    bad: list[str] = []
    for label, value, warn in (("빈 값", "", False), ("등록표 없음", "fal-ai/none/reference-to-video", True),
                               ("테이크 모델 아님", KLING_I2V, True)):
        with _scenario(fallback=value) as (env, client, genmod, policy, logs):
            policy["reject"] = _seed_anime
            since = len(env.requests)
            g = _start(env, client, genmod, "prj_al_d")
            web = _web(g)
            news = env.requests[since:]
            if web.get("status") != "failed" or web.get("error") != genmod.vpoc_fal._REJECT_LIKENESS_MSG \
                    or "animeFallback" in web or len(news) != 1:
                bad.append(f"{label}: 지금처럼 실패하지 않았습니다: {web.get('status')} {web.get('error')!r} {len(news)}")
            warned = [line for line in logs.lines if "anime_likeness_fallback_invalid" in line]
            if bool(warned) != warn:
                bad.append(f"{label}: 경고 줄이 {'없습니다' if warn else '있습니다'}: {warned[:2]}")
    return bad


# ---------------------------------------------------------------- (al-e)
def group_e() -> list[str]:
    bad: list[str] = []
    short = {**WAN_CAPS, "durations": [5, 10]}
    for label, total, caps, pins, word in (("최대 길이 10초 < 12초", 12, short, {}, "duration"),
                                           ("나누기 끔 · 참고 영상 15초 < 20초", 20, None, {"VNEW_TAKE_REF_SPLIT": False}, "refVideo")):
        with _scenario(wan_caps=caps, **pins) as (env, client, genmod, policy, logs):
            policy["reject"] = _seed_anime
            since = len(env.requests)
            g = _start(env, client, genmod, "prj_al_e", total=total)
            web = _web(g)
            news = env.requests[since:]
            if web.get("status") != "failed" or "animeFallback" in web or any(_is(r, WAN) for r in news):
                bad.append(f"{label}: 대체했거나 실패가 아닙니다: {web.get('status')} {web.get('animeFallback')} {len(news)}")
            if web.get("error") != genmod.vpoc_fal._REJECT_LIKENESS_MSG:
                bad.append(f"{label}: 지금 문구가 아닙니다: {web.get('error')!r}")
            why = [line for line in logs.lines if "anime_likeness_fallback_skip" in line]
            if len(why) != 1 or word not in why[0]:
                bad.append(f"{label}: 대체하지 않은 이유 로그가 없습니다: {why[:2]}")
    return bad


# ---------------------------------------------------------------- (al-f)
def group_f() -> list[str]:
    bad: list[str] = []
    with _scenario() as (env, client, genmod, policy, _logs):
        policy["reject"] = lambda req: _is(req, WAN)
        since = len(env.requests)
        g = _start(env, client, genmod, "prj_al_f", model=WAN)
        web = _web(g)
        news = env.requests[since:]
        if g.get("videoModel") != WAN or len(news) != 1 or not _is(news[0], WAN):
            bad.append(f"Wan 프로젝트 준비가 다릅니다: {g.get('videoModel')} {[r['url'][-40:] for r in news]}")
        if web.get("status") != "failed" or "animeFallback" in web \
                or web.get("error") != genmod.vpoc_fal._REJECT_LIKENESS_MSG:
            bad.append(f"이미 Wan 인 테이크가 대체됐거나 지금처럼 실패하지 않았습니다: {web.get('status')} {web.get('animeFallback')}")
    return bad


# ---------------------------------------------------------------- (al-g) 변이
MUTANTS = {
    "real-too": ("실사에도 대체", "al-c", ['    if variant != "webtoon" or gen.get("mode") != "previz-take":\n',
                                         '    if gen.get("mode") != "previz-take":\n']),
    "unlimited": ("횟수 제한 없음", "al-b", ["    if _anime_fallback_of(state):  # 한 테이크에 1번\n        return \"\"\n", ""]),
}


@contextlib.contextmanager
def _mutant(genmod, old: str, new: str):
    fn = genmod._anime_fallback_target  # noqa: SLF001
    src = inspect.getsource(fn)
    if src.count(old) != 1:
        raise AssertionError(f"변이 자리를 찾지 못했습니다: {old!r}")
    code = compile(src.replace(old, new), genmod.__file__, "exec", flags=_future.annotations.compiler_flag, dont_inherit=True)
    exec(code, genmod.__dict__)  # noqa: S102
    try:
        yield
    finally:
        genmod._anime_fallback_target = fn  # noqa: SLF001


def group_g(caught: list[str]) -> list[str]:
    bad: list[str] = []
    from src.vnew import generate as genmod  # noqa: PLC0415

    for name, (title, group, (old, new)) in MUTANTS.items():
        with _mutant(genmod, old, new):
            try:
                found = (group_c() if group == "al-c" else group_b())
            except Exception as exc:  # noqa: BLE001
                found = [f"예외 {type(exc).__name__}"]
        if found:
            caught.append(name)
        else:
            bad.append(f"변이 {name}({title})를 {group} 가 잡지 못했습니다")
    return bad


# ---------------------------------------------------------------- (al-h)
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


def check_anime_likeness_228() -> bool:
    results: dict[str, list[str]] = {}
    caught: list[str] = []
    warns: list[str] = []
    for group, fn in (("al-a", group_a), ("al-b", group_b), ("al-c", group_c), ("al-d", group_d), ("al-e", group_e),
                      ("al-f", group_f), ("al-g", lambda: group_g(caught)), ("al-h", lambda: group_h(warns))):
        try:
            results[group] = fn()
        except Exception:  # noqa: BLE001
            results[group] = ["예외:\n" + traceback.format_exc()[-1200:]]
    passed = True
    for group, title in GROUPS.items():
        items = results.get(group) or []
        if items:
            passed = False
            print(f"[FAIL] 작업 228 ({group}) {title}")
            for item in items[:4]:
                print("    " + item[:600].replace("\n", "\n    "))
        else:
            extra = f" ({' '.join(caught)})" if group == "al-g" else ""
            print(f"[ok  ] 작업 228 ({group}) {title}{extra}")
    for warn in warns:
        print(f"[WARN] 작업 228 (al-h) {warn}")
    return passed


if __name__ == "__main__" and "--baseline" in sys.argv:
    for _name, (_sha, _blob) in _real_blobs().items():
        print(f"REAL_LADDER_SHA[{_name}]", _sha, f"calls={len(_blob['calls'])} cell={_blob['cell'].get('status')}")
    sys.exit(0)

if __name__ == "__main__":
    NO_NODE = "--no-node" in sys.argv
    _ok = check_anime_likeness_228()
    print("check_vpoc 훅 줄(직접 넣지 않는다):")
    print(HOOK)
    sys.exit(0 if _ok else 1)
