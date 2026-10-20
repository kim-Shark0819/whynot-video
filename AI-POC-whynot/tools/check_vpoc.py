"""vpoc1 CI 검사기 — `check:whynot` 이 부르는 유일한 검사기다.

구 검사기 13종은 검사 대상 코드와 함께 지웠다. 정적 검사 통과는 완료 증거가 아니다(R3).
이 검사기가 보는 것은 네 가지뿐이다.

  1. app/src 전체 compileall
  2. 앱 임포트 스모크 (필요한 환경변수는 더미)
  3. node 가 있으면 frontend/public 의 모든 .js 에 node --check (없으면 경고 후 건너뜀, E16)
  4. 잔존 검사 — 구 구현 식별자(PRD 부록 G)의 import·호출이 0건인지
  5. 조립기 자체 검사 5건 (길이 · 한글 · 이름 단어 경계 · 부정어 · 빈 조각) — 모델 호출 없음

실행: python -X utf8 AI-POC-whynot/tools/check_vpoc.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # AI-POC-whynot
REPO = ROOT.parent                               # 저장소 루트
APP_SRC = ROOT / "app" / "src"
PUBLIC = ROOT / "frontend" / "public"

failures: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"[FAIL] {message}")


def warn(message: str) -> None:
    warnings.append(message)
    print(f"[WARN] {message}")


def ok(message: str) -> None:
    print(f"[ok  ] {message}")


# ---------------------------------------------------------------- 1. compileall


def check_compileall() -> None:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "compileall", "-q", str(APP_SRC)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail("compileall 실패\n" + (result.stdout or "") + (result.stderr or ""))
    else:
        ok("compileall app/src")


# ---------------------------------------------------------------- 2. 임포트 스모크


def check_import() -> None:
    env = dict(os.environ)
    env.update(
        {
            "S3_BUCKET": "dummy-bucket",
            "S3_VIDEO_BUCKET": "dummy-video-bucket",
            "VPOC_VIDEO_BUCKET": "dummy-video-bucket",
            "AUTH_JWT_SECRET": "dummy-secret",
            "AWS_REGION": "ap-northeast-2",
            "AWS_DEFAULT_REGION": "ap-northeast-2",
            "AWS_ACCESS_KEY_ID": "dummy",
            "AWS_SECRET_ACCESS_KEY": "dummy",
            "NEPTUNE_ENDPOINT": "",
            "VIDEO_GEN_ENABLED": "true",
            "VPOC_ENABLED": "true",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import src.server as s;"
        "rules=[str(r) for r in s.app.url_map.iter_rules()];"
        "need=['/api/vpoc/p/<pid>','/api/vpoc/smoke'];"
        "missing=[n for n in need if not any(r.startswith(n) for r in rules)];"
        "assert not missing, 'vpoc 라우트 누락: %%s' %% missing;"
        "print('routes', len(rules))" % str(ROOT / "app")
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        fail("앱 임포트 스모크 실패\n" + (result.stdout or "") + (result.stderr or ""))
    else:
        ok("앱 임포트 스모크 — " + (result.stdout or "").strip().splitlines()[-1])


# ---------------------------------------------------------------- 3. node --check


def check_js() -> None:
    node = shutil.which("node")
    if not node:
        warn("node 가 없어 JS 구문 검사를 건너뜁니다 (E16)")
        return
    bad = []
    for path in sorted(PUBLIC.glob("*.js")):
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            bad.append(f"{path.name}: {(result.stderr or '').strip()[:400]}")
    if bad:
        fail("node --check 실패\n" + "\n".join(bad))
    else:
        ok(f"node --check ({len(list(PUBLIC.glob('*.js')))} files)")


# ---------------------------------------------------------------- 4. 잔존 검사

# PRD 부록 G. 작업 트리에 import·호출이 0건이어야 하는 구 구현 식별자.
RESIDUE_EVERYWHERE = [
    # 모듈
    "story_generate", "story_i2v", "story_clips", "story_people", "story_steps",
    "video_api", "video_purposes", "story_video", "scene_video", "bedrock_reel",
    "video_callers", "video_cuts", "video_graph", "video_jobs", "video_records",
    "video_spec", "video_store", "video_types", "wan_client", "story_assemble",
    "story_characters", "story_failures", "story_host", "story_results",
    "story_state", "story_text_input", "story_understand", "character_curator",
    # purpose
    "video.story_understanding", "video.keyframe_ref", "video.i2v_prompt", "video.qa",
    # 모델 · 설정
    "amazon.nova-reel", "REQUIRED_IMAGE_SIZE", "IMAGE_MODEL_BY_PROVIDER",
    # 함수 · 상태
    "build_first_frame", "keyframe_reuse_plan", "keyframe_cache_id", "display_orders",
    "PromptTooLong", "PROMPT_REVIEW_REQUIRED", "WAITING_FOR_SOURCE", "SUBMISSION_UNKNOWN",
    # 저장 · 경로
    "project-state.json", "/story-session",
]

# 새 코드에서만 검사한다(구 데이터·플랫폼 파일에는 남아 있어도 된다).
RESIDUE_NEW_CODE_ONLY = [
    "video_scene", "video_job", "video_rating",
    "textInputPreview", "textInputGeneration",
]

# 지워졌어야 하는 파일
MUST_NOT_EXIST = [
    "app/src/video_api.py", "app/src/video_jobs.py", "app/src/video_store.py",
    "app/src/video_graph.py", "app/src/video_records.py", "app/src/video_types.py",
    "app/src/video_callers.py", "app/src/video_cuts.py", "app/src/video_purposes.py",
    "app/src/video_spec.py", "app/src/wan_client.py", "app/src/story_generate.py",
    "app/src/story_i2v.py", "app/src/story_clips.py", "app/src/story_people.py",
    "app/src/story_steps.py", "app/src/story_understand.py", "app/src/story_video",
    "app/src/scene_video", "app/src/roles/video",
    "frontend/public/story-app.js", "frontend/public/scene.js",
    "frontend/public/scene-cuts.js", "frontend/public/production.js",
    "frontend/public/video-folder.js", "frontend/public/story-characters.js",
    "frontend/public/story-text-input.js",
    "docs/prd.md", "docs/prd2.md", "docs/text-input-extension",
    "docs/run-log/story-video-rebuild",
    "tools/check_story_e2e.py", "tools/check_story_i2v.py", "tools/check_story_clips.py",
    "tools/check_video_prd_frontend.py", "tools/check_story_contracts.py",
]

# 검사에서 제외한다. docs/vpoc1 은 PRD 본문이라 식별자가 당연히 나온다.
# review.md 2-b 의 잔존 항목(플랫폼 파일)도 여기 적는다 — 사유는 review.md 에 있다.
EXCLUDE_PARTS = {
    "docs/vpoc1",
    "tools/check_vpoc.py",
    ".kiro/steering/whynot-vpoc1.md",
    "CLAUDE.md",
    # 잔존: 플랫폼 파일. E20 에 따라 import 한 줄 이상의 리팩터링은 하지 않았다.
    "app/src/video_prompt.py",
    "app/src/documents.py",
    "app/src/prompt_layers.py",
    "app/src/graph_store.py",
    "frontend/public/app.js",
    "frontend/public/index.html",
    "frontend/public/styles.css",
    # 잔존: 영상 전용 인프라. 삭제는 Terraform·ArgoCD 절차가 따로 필요하다.
    "k8s-gpu",
    "video-worker",
    "terraform",
    "deploy/verify",
    "deploy/config.env.example",
    "deploy/config.prod.env",
    "docs/구조분석.md",
    "docs/AI-POC_history.md",
    "docs/run-log",
}

SCAN_SUFFIXES = {".py", ".js", ".html", ".css", ".yml", ".yaml", ".tmpl", ".json", ".sh", ".ps1"}


def _iter_files(base: Path):
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        rel = path.relative_to(REPO).as_posix()
        if "/__pycache__/" in rel or rel.endswith(".pyc"):
            continue
        if any(part in rel for part in EXCLUDE_PARTS):
            continue
        yield path, rel


def check_residue() -> None:
    for relative in MUST_NOT_EXIST:
        if (ROOT / relative).exists():
            fail(f"지워야 할 것이 남아 있습니다: AI-POC-whynot/{relative}")

    hits: list[str] = []
    for path, rel in _iter_files(ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in RESIDUE_EVERYWHERE:
            if needle in text:
                line_no = text[: text.index(needle)].count("\n") + 1
                hits.append(f"{rel}:{line_no} — 구 식별자 '{needle}'")

    new_code = [APP_SRC / "vpoc", PUBLIC / "vpoc.js", PUBLIC / "vpoc-smoke.js"]
    for base in new_code:
        paths = [base] if base.is_file() else list(base.rglob("*")) if base.exists() else []
        for path in paths:
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in RESIDUE_NEW_CODE_ONLY:
                if needle in text:
                    hits.append(f"{path.relative_to(REPO).as_posix()} — 새 코드에 '{needle}'")

    if hits:
        fail("잔존 검사 실패\n" + "\n".join(f"  {item}" for item in sorted(set(hits))))
    else:
        ok(f"잔존 검사 — 구 식별자 {len(RESIDUE_EVERYWHERE)}종 0건")


# ---------------------------------------------------------------- 5. 조립기 자체 검사


def check_compose() -> None:
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import compose  # noqa: PLC0415

    characters = [
        {
            "id": "c1",
            "nameKo": "서원",
            "nameEn": "Seo Won",
            "appearanceEn": "A lean man with black hair and East Asian features, wearing a white-and-blue racing suit",
            "handleEn": "the black-haired racer",
        }
    ]
    base = {
        "id": "s1",
        "summaryKo": "서원이 걸어온다",
        "characters": ["c1"],
        "locationEn": "A floodlit racing pit lane at night",
        "actionEn": "The black-haired racer walks toward the camera and stops",
        "cameraEn": "Tracking shot at eye level",
        "cameraKo": "인물을 따라가기(트래킹)",
    }
    cases: list[tuple[str, dict, bool, str]] = []

    # (1) 정상 통과 + 조립 결과가 상한 안
    cases.append(("정상 샷", dict(base), True, ""))

    # (2) 길이 상한 — actionEn 260자 초과
    too_long = dict(base)
    too_long["actionEn"] = "The black-haired racer " + ("walks forward " * 30)
    cases.append(("길이 상한", too_long, False, "260자를 넘습니다"))

    # (3) 한글
    hangul = dict(base)
    hangul["actionEn"] = "The racer 걸어온다 toward the camera"
    cases.append(("한글", hangul, False, "한글이 있습니다"))

    # (4) 이름 단어 경계 — "Seo Won" 은 잡고 "won the race" 는 통과
    named = dict(base)
    named["actionEn"] = "Seo Won walks toward the camera and stops"
    cases.append(("이름 단어 경계(잡아야 함)", named, False, "캐릭터 이름"))
    won = dict(base)
    won["actionEn"] = "The black-haired racer who won the race walks toward the camera"
    cases.append(("이름 단어 경계(통과해야 함)", won, True, ""))

    # (5) 부정어와 빈 조각
    negated = dict(base)
    negated["actionEn"] = "The black-haired racer does not look at the camera"
    cases.append(("부정어", negated, False, "부정어가 있습니다"))
    empty = dict(base)
    empty["locationEn"] = ""
    cases.append(("빈 조각", empty, False, "비어 있습니다"))

    bad: list[str] = []
    for label, shot, should_pass, marker in cases:
        problems = compose.check_shot(shot, characters, "live_action")
        passed = not problems
        if passed != should_pass:
            bad.append(f"{label}: 기대 {'통과' if should_pass else '위반'} / 실제 {problems}")
        elif marker and not any(marker in item for item in problems):
            bad.append(f"{label}: 위반 사유에 '{marker}' 가 없습니다 → {problems}")

    prompt = compose.build_prompt(base, characters, "live_action")
    if not prompt.startswith(compose.STYLE["live_action"]):
        bad.append("조립 결과가 화풍 접두어로 시작하지 않습니다")
    if len(prompt) > compose.MAX_PROMPT_CHARS:
        bad.append(f"정상 샷 조립 결과가 상한을 넘습니다 ({len(prompt)}자)")

    if bad:
        fail("조립기 자체 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(f"조립기 자체 검사 {len(cases)}건")


# ---------------------------------------------------------------- 6. 멱등 키


def check_client_token() -> None:
    """clientRequestToken 이 AWS 패턴을 만족하는지 본다.

    2026-09-23 dev 에서 4샷 전부 ValidationException 으로 죽었다. planId(`pl_20260922_de38`)와
    shotId(`shot_01`)의 밑줄을 그대로 넣어서다. AWS 패턴은 밑줄을 받지 않는다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import video  # noqa: PLC0415

    cases = [
        ("pl_20260922_de38", "shot_01", 1),
        ("pl_20260923_a1b2", "s1", 2),
        ("sm_deadbeef", "s1", 1),
        ("pl.x/y", "shot 3", 10),
        ("__pl__", "__s__", 1),
    ]
    bad: list[str] = []
    seen: dict[str, tuple] = {}
    for plan_id, shot_id, attempt in cases:
        try:
            token = video.client_token(plan_id, shot_id, attempt)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{plan_id}/{shot_id}/{attempt}: 예외 {exc}")
            continue
        if not video.TOKEN_PATTERN.match(token):
            bad.append(f"{plan_id}/{shot_id}/{attempt} -> {token!r} 이 AWS 패턴을 어깁니다")
        if "_" in token:
            bad.append(f"{plan_id}/{shot_id}/{attempt} -> {token!r} 에 밑줄이 있습니다")
        if not 1 <= len(token) <= 256:
            bad.append(f"{plan_id}/{shot_id}/{attempt} -> 길이 {len(token)}")
        # 같은 입력은 항상 같은 값, 다른 입력은 다른 값이어야 한다(멱등성과 충돌 방지).
        if token in seen and seen[token] != (plan_id, shot_id, attempt):
            bad.append(f"{token!r} 가 {seen[token]} 와 충돌합니다")
        seen[token] = (plan_id, shot_id, attempt)

    first = video.client_token("pl_20260922_de38", "shot_01", 1)
    if first != video.client_token("pl_20260922_de38", "shot_01", 1):
        bad.append("같은 입력이 다른 키를 냅니다 (멱등성 깨짐)")
    if first == video.client_token("pl_20260922_de38", "shot_01", 2):
        bad.append("attempt 가 달라도 같은 키가 나옵니다")

    if bad:
        fail("멱등 키 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(f"멱등 키 검사 {len(cases)}건 (예: {first})")


# ---------------------------------------------------------------- 7. 재생성(regen)


def _fake_state() -> dict:
    """새 필드가 전혀 없는 옛 계획 상태. R9 경로를 태운다."""
    return {
        "planId": "pl_20260923_test",
        "projectId": "prj_test",
        "owner": "tester",
        "currentStep": 4,
        "maxReachedStep": 4,
        "task": {"name": None, "status": "idle", "startedAt": None, "finishedAt": None, "error": None},
        "revisions": {"1": 0, "2": 0, "3": 0},
        "finalizedAt": "2026-09-23T05:20:11Z",
        "steps": {
            "1": {"status": "saved", "text": "t", "characters": [], "sceneFiles": []},
            "2": {
                "status": "ready",
                "story": {"style": "live_action", "settingEn": "A pit lane at night"},
                "characters": [
                    {
                        "id": "c1",
                        "nameKo": "서원",
                        "nameEn": "Seo Won",
                        "handleEn": "the black-haired racer",
                        "appearanceEn": "A lean man with black hair",
                    }
                ],
                "questions": [],
                "answers": {},
            },
            "3": {
                "status": "ready",
                "useKeyframes": False,
                "characters": [
                    {"id": "c1", "image": {"status": "ready", "source": "ai", "key": "k.jpg"}}
                ],
                "shots": [
                    {
                        "id": "s1",
                        "summaryKo": "첫 샷",
                        "cameraKo": "고정 샷",
                        "cameraEn": "Static camera",
                        "locationEn": "A pit lane at night",
                        "actionEn": "The black-haired racer stands still",
                        "characters": ["c1"],
                        "promptEn": "Cinematic live-action film footage. A pit lane at night.",
                        "useKeyframe": False,
                        "reviewKo": None,
                    },
                    {
                        "id": "s2",
                        "summaryKo": "둘째 샷",
                        "cameraKo": "트래킹",
                        "cameraEn": "Tracking shot",
                        "locationEn": "A pit lane at night",
                        "actionEn": "The black-haired racer walks forward",
                        "characters": ["c1"],
                        "promptEn": "Cinematic live-action film footage. Tracking shot.",
                        "useKeyframe": False,
                        "reviewKo": None,
                    },
                ],
                "cost": {"shots": 2, "totalUsd": 7.5},
            },
            "4": {
                "status": "done",
                "shots": [
                    {
                        "id": "s1",
                        "status": "completed",
                        "attempt": 1,
                        "mode": "text",
                        "submittedAt": "2026-09-23T05:10:02Z",
                        "completedAt": "2026-09-23T05:13:40Z",
                        "outputKey": "users/tester/a/s1.mp4",
                        "note": None,
                        "error": None,
                    },
                    {
                        "id": "s2",
                        "status": "completed",
                        "attempt": 1,
                        "mode": "text",
                        "submittedAt": "2026-09-23T05:10:03Z",
                        "completedAt": "2026-09-23T05:14:10Z",
                        "outputKey": "users/tester/a/s2.mp4",
                        "note": None,
                        "error": None,
                    },
                ],
                "final": {"status": "ready", "key": "users/tester/a/final.mp4", "error": None},
            },
        },
    }


# PlanView 의 ④ 샷이 반드시 들고 있어야 하는 기존 키(api-check.md).
LEGACY_SHOT4_KEYS = {
    "id", "status", "attempt", "mode", "submittedAt", "completedAt",
    "elapsedS", "videoUrl", "note", "error",
}
REGEN_SHOT4_KEYS = {"selectedAttempt", "versions", "revision", "regen"}

# ④ 자체에 더한 새 키. 생성 회차를 구분한다.
STEP4_KEYS = {"genId"}


def check_regen() -> None:
    import copy  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import regen, settings  # noqa: PLC0415

    bad: list[str] = []

    # (a) 새 필드가 없는 옛 state 를 읽어도 기존 키가 그대로고 새 키가 규칙대로 채워진다 (R9)
    state = _fake_state()
    shot = state["steps"]["4"]["shots"][0]
    plan_shot = state["steps"]["3"]["shots"][0]
    versions = regen.versions_view(shot, plan_shot)
    if len(versions) != 1 or versions[0]["attempt"] != 1 or versions[0]["origin"] != "plan":
        bad.append(f"(a) 옛 state 의 versions 가 1개(plan)여야 합니다 → {versions}")
    if regen.revision_view(shot) is not None or regen.regen_view(shot) is not None:
        bad.append("(a) 옛 state 는 revision·regen 이 null 이어야 합니다")
    if regen.max_attempt(shot) != 1:
        bad.append(f"(a) max_attempt 가 1이어야 합니다 → {regen.max_attempt(shot)}")
    if "versions" in state["steps"]["4"]["shots"][0]:
        bad.append("(a) 읽기만 했는데 state 에 versions 가 쓰였습니다 (마이그레이션 금지)")

    # (b) regen 진행 중에도 샷 본체의 status·videoUrl·attempt 는 선택 버전 값이다 (G2)
    state = _fake_state()
    shot = state["steps"]["4"]["shots"][0]
    regen.seed_versions(shot, plan_shot)
    shot["regen"] = {
        "attempt": 2,
        "origin": "regen",
        "status": "submitted",
        "mode": "text",
        "promptEn": "Cinematic live-action film footage. New take.",
        "feedbackKo": "표정을 바꿔 주세요",
        "summaryKo": "새 버전",
        "submittedAt": "2026-09-23T06:00:00Z",
        "outputKey": "",
        "note": None,
        "error": None,
    }
    if shot["status"] != "completed" or shot["outputKey"] != "users/tester/a/s1.mp4":
        bad.append("(b) regen 이 샷 본체를 바꿨습니다")
    if int(shot.get("attempt") or 0) != 1:
        bad.append("(b) regen 이 샷 attempt 를 바꿨습니다")
    if regen.max_attempt(shot) != 2:
        bad.append("(b) max_attempt 가 regen attempt 를 세지 않습니다")
    targets = regen.poll_targets(shot)
    if len(targets) != 2 or targets[1] is not shot["regen"]:
        bad.append("(b) poll_targets 가 regen 을 대상에 넣지 않습니다")
    view = regen.regen_view(shot)
    if not view or view["status"] != "submitted" or view["attempt"] != 2:
        bad.append(f"(b) regen_view 가 잘못됐습니다 → {view}")

    # (c) select 뒤 다른 샷 데이터가 변하지 않는다
    state = _fake_state()
    other_before = _json.dumps(state["steps"]["4"]["shots"][1], sort_keys=True, ensure_ascii=False)
    step3_before = _json.dumps(state["steps"]["3"], sort_keys=True, ensure_ascii=False)
    shot = state["steps"]["4"]["shots"][0]
    regen.seed_versions(shot, state["steps"]["3"]["shots"][0])
    shot["versions"].append(
        {
            "attempt": 2,
            "origin": "regen",
            "feedbackKo": "표정을 바꿔 주세요",
            "summaryKo": "새 버전",
            "promptEn": "Cinematic live-action film footage. New take.",
            "mode": "text",
            "status": "completed",
            "outputKey": "users/tester/a/s1-v2.mp4",
            "submittedAt": "2026-09-23T06:00:00Z",
            "completedAt": "2026-09-23T06:03:24Z",
            "createdAt": "2026-09-23T06:00:00Z",
        }
    )
    shot["regen"] = None
    result = regen.apply_select(state, shot_id="s1", attempt=2)
    if result["attempt"] != 2 or result["previousAttempt"] != 1:
        bad.append(f"(c) apply_select 결과가 잘못됐습니다 → {result}")
    if shot["outputKey"] != "users/tester/a/s1-v2.mp4" or shot["selectedAttempt"] != 2:
        bad.append("(c) select 이 선택 버전을 샷에 반영하지 않았습니다")
    if not result["stitchStale"] or state["steps"]["4"]["final"]["status"] != "stale":
        bad.append("(c) 버전을 바꿨는데 합본이 stale 이 되지 않았습니다")
    other_after = _json.dumps(state["steps"]["4"]["shots"][1], sort_keys=True, ensure_ascii=False)
    step3_after = _json.dumps(state["steps"]["3"], sort_keys=True, ensure_ascii=False)
    if other_before != other_after:
        bad.append("(c) 다른 샷 데이터가 바뀌었습니다")
    if step3_before != step3_after:
        bad.append("(c) ③ 계획이 바뀌었습니다")

    # 되돌리기: 이전 버전 유지 = 같은 attempt 로 select
    state2 = copy.deepcopy(state)
    back = regen.apply_select(state2, shot_id="s1", attempt=1)
    if back["attempt"] != 1 or state2["steps"]["4"]["shots"][0]["selectedAttempt"] != 1:
        bad.append("(c) 이전 버전으로 되돌릴 수 없습니다")

    # (d) 스위치가 꺼지면 404
    original = settings.REGEN_ENABLED
    try:
        settings.REGEN_ENABLED = False
        if regen.enabled():
            bad.append("(d) 스위치를 껐는데 enabled() 가 True 입니다")
        try:
            regen.require_enabled("test")
            bad.append("(d) 스위치가 꺼졌는데 require_enabled 가 통과했습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) != 404:
                bad.append(f"(d) 404 가 아니라 {getattr(exc, 'status', '?')} 입니다")
    finally:
        settings.REGEN_ENABLED = original

    # 선행 조건: 진행 중이면 409, 의견이 비면 400, 너무 길면 400
    state3 = _fake_state()
    for feedback, want in (("", "FeedbackEmpty"), ("가" * 501, "FeedbackTooLong")):
        try:
            regen.check_revise_preconditions(state3, "s1", feedback)
            bad.append(f"선행 조건 검사가 {want} 를 잡지 못했습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != want:
                bad.append(f"{want} 대신 {getattr(exc, 'kind', '?')} 가 났습니다")
    state3["steps"]["4"]["shots"][0]["regen"] = {"status": "submitted", "attempt": 2}
    try:
        regen.check_revise_preconditions(state3, "s1", "표정을 바꿔 주세요")
        bad.append("진행 중인데 RegenRunning 이 나지 않았습니다")
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "kind", "") != "RegenRunning":
            bad.append(f"RegenRunning 대신 {getattr(exc, 'kind', '?')} 가 났습니다")

    if bad:
        fail("재생성 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok("재생성 검사 (a)옛 state (b)G2 격리 (c)select 영향 범위 (d)스위치 + 선행 조건")


def check_gen_id() -> None:
    """생성 회차(genId) 분리 — 2026-09-23 "옛 영상이 다시 붙는" 결함의 재발 방지.

    docs/vpoc1/fix-regen-reload.md 참고. ③을 고쳐 다시 승인했을 때 1차와 같은
    clientRequestToken 이 나와 Bedrock 이 1차 invocation 을 돌려줬다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import regen, store, video  # noqa: PLC0415

    bad: list[str] = []
    owner, pid, plan = "admin", "prj1", "pl_20260922_0042"
    shot, attempt = "shot_01", 1
    g1, g2 = "g20260922233000", "g20260923003500"

    # (a) genId 가 다르면 토큰·jobs 키·출력 접두사가 모두 다르다
    t_old = video.client_token(plan, shot, attempt)
    t1 = video.client_token(plan, shot, attempt, g1)
    t2 = video.client_token(plan, shot, attempt, g2)
    if not (t_old != t1 != t2 and t1 != t2):
        bad.append(f"(a) 토큰이 회차별로 갈라지지 않습니다: {t_old} / {t1} / {t2}")
    for token in (t_old, t1, t2):
        if not video.TOKEN_PATTERN.match(token):
            bad.append(f"(a) 토큰 {token!r} 이 AWS 패턴을 어깁니다")
    if video.client_token(plan, shot, attempt, g1) != t1:
        bad.append("(a) 같은 회차인데 토큰이 달라집니다 (멱등성 깨짐)")

    j1 = store.job_key(owner, pid, plan, shot, attempt, g1)
    j2 = store.job_key(owner, pid, plan, shot, attempt, g2)
    j_old = store.job_key(owner, pid, plan, shot, attempt)
    if len({j1, j2, j_old}) != 3:
        bad.append(f"(a) jobs 키가 겹칩니다: {j_old} / {j1} / {j2}")
    if f"/jobs/{g1}/" not in j1 or f"/jobs/{g2}/" not in j2:
        bad.append("(a) jobs 키에 genId 가 들어가지 않습니다")
    if "/jobs/" not in j_old or j_old.count("/jobs/") != 1 or g1 in j_old:
        bad.append(f"(a) 옛 jobs 키 규칙이 바뀌었습니다: {j_old}")

    p1 = store.video_output_prefix(owner, pid, plan, shot, attempt, g1)
    p2 = store.video_output_prefix(owner, pid, plan, shot, attempt, g2)
    p_old = store.video_output_prefix(owner, pid, plan, shot, attempt)
    if len({p1, p2, p_old}) != 3:
        bad.append(f"(a) 출력 접두사가 겹칩니다: {p_old} / {p1} / {p2}")
    if not p1.endswith(f"/shots/{g1}/{shot}-{attempt}"):
        bad.append(f"(a) 출력 접두사에 genId 가 없습니다: {p1}")
    if not p_old.endswith(f"/shots/{shot}-{attempt}"):
        bad.append(f"(a) 옛 출력 접두사 규칙이 바뀌었습니다: {p_old}")

    if not store.new_gen_id().startswith("g") or len(store.new_gen_id()) != 15:
        bad.append(f"(a) new_gen_id 형식이 다릅니다: {store.new_gen_id()}")

    # mp4 찾기가 그 invocation 폴더만 보는지 (상위 접두사 나열 금지)
    source = (ROOT / "app" / "src" / "vpoc" / "video.py").read_text(encoding="utf-8")
    body = source[source.index("def find_output_mp4(") :]
    body = body[: body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    if "invocation_arn" not in body or "invocation_id(" not in body:
        bad.append("(a) find_output_mp4 가 invocation 폴더로 한정하지 않습니다")

    # (b) ③ 재실행 뒤 ④가 stale 이고 이전 영상 참조가 남지 않는다.
    #     사용자가 실제로 한 것: ④에서 ②로 되돌아가 화풍을 바꿨다 → mark_stale(2) → ③·④ stale.
    state = _fake_state()
    state["steps"]["4"]["genId"] = g1
    dropped = store.mark_stale(state, 2)
    step4 = state["steps"]["4"]
    if step4.get("status") != "stale":
        bad.append(f"(b) ④ 가 stale 이 아닙니다: {step4.get('status')}")
    if step4.get("shots"):
        bad.append(f"(b) ④ 에 이전 샷이 남아 있습니다: {len(step4['shots'])}개")
    if step4.get("genId"):
        bad.append(f"(b) genId 가 비워지지 않았습니다: {step4.get('genId')}")
    if (step4.get("final") or {}).get("key") or (step4.get("final") or {}).get("status") != "none":
        bad.append(f"(b) 합본 참조가 남아 있습니다: {step4.get('final')}")
    if state.get("finalizedAt"):
        bad.append("(b) finalizedAt 이 남아 있습니다 — 완료 처리가 이전 회차에 묶입니다")
    if dropped.get("videos") != 2:
        bad.append(f"(b) 버려질 영상 수가 틀립니다: {dropped}")
    # ③은 stale 이 되지만 내용은 남는다(사용자가 다시 실행해야 한다)
    if state["steps"]["3"].get("status") != "stale" or not state["steps"]["3"].get("shots"):
        bad.append("(b) ③ 내용이 지워졌습니다 — status 만 stale 이어야 합니다")

    # ③ 저장 내용이 바뀐 경우(mark_stale(3))도 ④만 비워진다
    only4 = _fake_state()
    only4["steps"]["4"]["genId"] = g1
    store.mark_stale(only4, 3)
    if only4["steps"]["4"].get("shots") or only4["steps"]["4"].get("genId"):
        bad.append("(b) mark_stale(3) 이 ④를 비우지 않았습니다")
    if only4["steps"]["3"].get("status") != "ready":
        bad.append("(b) mark_stale(3) 이 ③ 자신을 건드렸습니다")
    if only4.get("finalizedAt"):
        bad.append("(b) mark_stale(3) 뒤 finalizedAt 이 남아 있습니다")

    # (c) 두 번째 완료가 새 finals 기록을 만들고 project.json·Neptune 을 갱신한다
    k1 = store.finals_key(owner, pid, plan, "2026-09-22T23:57:14Z")
    k2 = store.finals_key(owner, pid, plan, "2026-09-23T00:40:02Z")
    if k1 == k2 or not k1.startswith(store.finals_prefix(owner, pid, plan)):
        bad.append(f"(c) finals 키가 회차별로 갈라지지 않습니다: {k1} / {k2}")
    rsource = (ROOT / "app" / "src" / "vpoc" / "routes.py").read_text(encoding="utf-8")
    fin = rsource[rsource.index("def finish_plan(") : rsource.index("def update_graph(")]
    if "finals_key" not in fin:
        bad.append("(c) finish_plan 이 finals 기록을 쓰지 않습니다")
    if '"genId": step4.get("genId")' not in fin:
        bad.append("(c) 완료 기록에 genId 가 없습니다")
    # final.json 이 이미 있을 때 조기 반환하면 project.json·Neptune 갱신에 도달하지 못한다
    tail = fin[fin.index("store.final_key(owner, pid, plan_id), record, create_only=True") :]
    guard = tail[: tail.index("store.project_key")]
    if "return True" in guard:
        bad.append("(c) final.json 이 이미 있으면 조기 반환합니다 — 확정본 갱신에 도달하지 못합니다")
    if "update_graph(pid, \"done\", plan_id, finals_key" not in fin:
        bad.append("(c) Neptune vpocFinalKey 가 finals 기록을 가리키지 않습니다")

    # (d) genId 없는 옛 state 를 읽어도 기존 키와 영상이 그대로다
    old = _fake_state()
    old_shot = old["steps"]["4"]["shots"][0]
    old_plan_shot = old["steps"]["3"]["shots"][0]
    versions = regen.versions_view(old_shot, old_plan_shot, "")
    if len(versions) != 1 or versions[0]["attempt"] != 1:
        bad.append(f"(d) 옛 state 의 versions 가 1개여야 합니다: {versions}")
    if old_shot.get("outputKey") != "users/tester/a/s1.mp4":
        bad.append("(d) 옛 state 의 outputKey 가 바뀌었습니다")
    if "versions" in old["steps"]["4"]["shots"][0]:
        bad.append("(d) 읽기만 했는데 state 에 versions 가 쓰였습니다")

    # (e) 재생성 versions 에 다른 genId 의 job 이 섞이지 않는다
    mixed = _fake_state()
    mixed["steps"]["4"]["genId"] = g2
    shot_m = mixed["steps"]["4"]["shots"][0]
    shot_m["versions"] = [
        {"attempt": 1, "genId": g1, "origin": "plan", "status": "completed",
         "outputKey": "old.mp4", "summaryKo": "1차"},
        {"attempt": 2, "genId": g1, "origin": "regen", "status": "completed",
         "outputKey": "old2.mp4", "summaryKo": "1차 재생성"},
        {"attempt": 1, "genId": g2, "origin": "plan", "status": "completed",
         "outputKey": "new.mp4", "summaryKo": "2차"},
    ]
    view = regen.versions_view(shot_m, mixed["steps"]["3"]["shots"][0], g2)
    if len(view) != 1 or view[0].get("genId") != g2:
        bad.append(f"(e) 다른 회차 버전이 섞였습니다: {[v.get('genId') for v in view]}")
    kept = regen.seed_versions(shot_m, mixed["steps"]["3"]["shots"][0], g2)
    if len(kept) != 1 or kept[0].get("outputKey") != "new.mp4":
        bad.append(f"(e) seed_versions 가 이전 회차를 버리지 않습니다: {kept}")

    if bad:
        fail("생성 회차 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok("생성 회차 검사 (a)키 분리 (b)③재실행 (c)finals·확정본 (d)옛 데이터 (e)회차 격리")


def check_no_budget_cap() -> None:
    """비용 상한 없음 — 2026-09-23 사용자 결정. 기록은 남기고 제출을 막지 않는다.

    (a) 누계가 매우 커도 제출이 거부되지 않는다
    (b) 제출하면 원장에 정확히 한 번 기록된다 (같은 키는 중복 계상하지 않는다)
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ledger, settings  # noqa: PLC0415

    bad: list[str] = []

    # 상한 검사 코드가 남아 있으면 실패시킨다 — 되살아나면 제출이 다시 막힌다.
    if hasattr(ledger, "ensure_room"):
        bad.append("ledger.ensure_room 이 남아 있습니다")
    if hasattr(settings, "BUDGET_USD"):
        bad.append("settings.BUDGET_USD 가 남아 있습니다")
    for name in ("routes.py", "regen.py", "ledger.py"):
        text = (ROOT / "app" / "src" / "vpoc" / name).read_text(encoding="utf-8")
        for needle in ("ensure_room(", "BudgetExceeded", "BUDGET_USD"):
            # ledger.py 의 모듈 docstring 에는 "제거했다" 설명이 남아 있어도 된다.
            if needle in text and not (name == "ledger.py" and needle in text.split('"""')[1]):
                bad.append(f"{name} 에 {needle} 이 남아 있습니다")

    # (a) 누계가 100,000 USD 여도 어떤 제출 경로도 거부되지 않는다
    fake = {"capUsd": None, "spentUsd": 100000.0, "entries": [{"key": "old", "usd": 100000.0}]}
    real_read = ledger.read
    real_write = ledger.STORE.write_json
    charged: list[dict] = []

    def fake_read():
        return json.loads(json.dumps(fake)), "etag"

    def fake_write(key, data, **kwargs):
        fake["entries"] = data["entries"]
        fake["spentUsd"] = data["spentUsd"]
        charged.append(data["entries"][-1])
        return "etag2"

    try:
        ledger.read = fake_read
        ledger.STORE.write_json = fake_write
        summary = ledger.summary()
        if summary.get("capUsd") is not None:
            bad.append(f"(a) summary 의 capUsd 가 null 이 아닙니다: {summary}")
        if abs(float(summary.get("spentUsd") or 0) - 100000.0) > 0.01:
            bad.append(f"(a) 누계가 그대로 보이지 않습니다: {summary}")

        # (b) 제출 1건이 정확히 한 번 기록된다
        token = "pl-x-g20260923-shot-01-1"
        out = ledger.charge_video(token, user="u", project_id="p", plan_id="pl")
        if len(charged) != 1:
            bad.append(f"(b) 기록이 1건이 아닙니다: {len(charged)}")
        elif abs(float(charged[0]["usd"]) - settings.shot_usd()) > 0.001:
            bad.append(f"(b) 기록 금액이 틀립니다: {charged[0]['usd']}")
        elif charged[0]["key"] != token:
            bad.append(f"(b) 기록 키가 틀립니다: {charged[0]['key']}")
        if out.get("capUsd") is not None:
            bad.append("(b) charge 응답의 capUsd 가 null 이 아닙니다")

        # 같은 키를 다시 계상하지 않는다
        before = len(charged)
        ledger.charge_video(token, user="u", project_id="p", plan_id="pl")
        if len(charged) != before:
            bad.append("(b) 같은 키가 두 번 계상됐습니다")
    finally:
        ledger.read = real_read
        ledger.STORE.write_json = real_write

    # 화면이 상한을 그리지 않는다
    js = (ROOT / "frontend" / "public" / "vpoc.js").read_text(encoding="utf-8")
    # 값을 읽어 쓰는 곳이 없어야 한다. 주석과 픽스처의 `capUsd: null` 은 괜찮다.
    if re.search(r"\.capUsd\b", js):
        bad.append("vpoc.js 가 budget.capUsd 값을 읽어 씁니다 (상한을 표시하게 됩니다)")
    if "상한 " in js:
        bad.append("vpoc.js 에 '상한' 문구가 남아 있습니다")
    # 설계자 2026-10-07: 영상 만들기 화면에는 금액을 보이지 않는다(admin 모델 관리에서만 본다).
    # 예전에는 "누적 비용"·"현재 누적" 문구가 있어야 했다 — 그 요구를 반대로 바꿨다.
    for name in ("vpoc.js", "vpoc2.js"):
        text = (ROOT / "frontend" / "public" / name).read_text(encoding="utf-8")
        # 주석은 빼고 문자열 리터럴 안의 금액 표시만 본다.
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
        for needle in (" USD", "누적 비용", "현재 누적"):
            if re.search(r"[\"'][^\"'\n]*" + re.escape(needle), code):
                bad.append(f"{name} 가 영상 화면에 금액을 보입니다: {needle.strip()!r}")

    if bad:
        fail("비용 상한 없음 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok("비용 상한 없음 검사 (a)누계 100,000 에도 제출 가능 (b)원장 1건 정확히 기록")


# ---------------------------------------------------------------- 8. 진행 중 작업 취소


class _FakeStore:
    """메모리 S3. 취소 검사만 쓰는 최소 인터페이스(실제 외부 호출 금지, 설계자 2026-10-20)."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.etags: dict[str, int] = {}

    def read_json(self, key: str):
        if key not in self.data:
            return None, ""
        return json.loads(json.dumps(self.data[key])), str(self.etags.get(key, 0))

    def write_json(self, key: str, data: dict, *, etag: str = "", create_only: bool = False) -> str:
        self.etags[key] = self.etags.get(key, 0) + 1
        self.data[key] = json.loads(json.dumps(data))
        return str(self.etags[key])

    def presign(self, key: str, **kwargs) -> str:
        return "https://example/" + key

    def exists(self, key: str, **kwargs) -> bool:
        return key in self.data


def check_cancel() -> None:
    """진행 중 작업 취소 — 서버 API(설계자 2026-10-20). 실제 외부 호출 금지(공급자 취소는 monkeypatch).

    (a) running task 취소 → cancelled, 늦게 끝난 스레드 결과가 버려진다
    (b) ④ queued/submitted 샷 취소 → cancelled, 공급자 취소 결과 ok/rejected/unsupported
    (c) 취소할 것이 없으면 409 NothingToCancel
    (d) 취소 후 generate 가 다시 가능하다
    """
    import time as _time  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, ledger, routes, store, video  # noqa: PLC0415

    bad: list[str] = []

    owner, pid, plan_id = "tester", "prj_c", "pl_cancel_01"

    # --- 메모리 저장소로 바꾼다(실제 S3 금지) ---
    fake = _FakeStore()
    saved = {
        "store_STORE": store.STORE,
        "ledger_STORE": ledger.STORE,
        "video_STORE": video.STORE,
        "fal_STORE": fal.STORE,
        "ark_cancel": ark.cancel,
    }
    store.STORE = fake
    ledger.STORE = fake
    video.STORE = fake
    fal.STORE = fake

    # 공급자 취소는 가짜다. ark 작업(ark/tasks/)은 성공 → ok, fal 작업(fal/requests/)은 거절 → rejected.
    # Luma(Bedrock async)는 _cancel_video 가 ark.cancel 을 호출하지 않는다 → unsupported.
    cancel_calls: list[str] = []

    def fake_ark_cancel(arn: str) -> None:
        cancel_calls.append(arn)
        if fal.is_fal_job(arn):
            raise RuntimeError("fal 이 거절했습니다(이미 생성 중)")
        # ark/tasks/ 는 성공(조용히 반환)

    ark.cancel = fake_ark_cancel

    def _state_with(task=None, step4=None) -> dict:
        state = store.new_state(owner, pid, plan_id)
        state["currentStep"] = 4
        state["maxReachedStep"] = 4
        state["steps"]["3"] = {
            "status": "ready",
            "useKeyframes": False,
            "characters": [],
            "shots": [
                {
                    "id": "s1",
                    "summaryKo": "샷1",
                    "cameraKo": "",
                    "cameraEn": "",
                    "locationEn": "A pit lane",
                    "actionEn": "stands",
                    "characters": [],
                    "promptEn": "Cinematic. A pit lane.",
                    "useKeyframe": False,
                    "reviewKo": None,
                    "durationS": 5,
                }
            ],
            "cost": {"shots": 1, "totalUsd": 3.75},
        }
        if task is not None:
            state["task"] = task
        if step4 is not None:
            state["steps"]["4"] = step4
        return state

    # --- Flask 블루프린트를 가짜 deps 로 만든다 ---
    def _passthrough(handler):
        return handler

    deps = routes.VpocDependencies(
        require_auth=_passthrough,
        current_username=lambda: owner,
        get_project=lambda p: {"id": pid, "owner": owner},
        can_read_project=lambda proj: True,
        can_write_project=lambda proj: True,
        graph_run=lambda *a, **k: [],
    )

    try:
        from flask import Flask  # noqa: PLC0415

        bp = routes.create_blueprint(deps)
        app = Flask(__name__)
        app.register_blueprint(bp)
        client = app.test_client()
        url = f"/api/vpoc/p/{pid}/plans/{plan_id}/cancel"

        # (c) 취소할 것이 없으면 409 NothingToCancel
        fake.data.clear()
        fake.etags.clear()
        idle = _state_with()
        store.save_state(owner, pid, plan_id, idle)
        resp = client.post(url)
        body = resp.get_json()
        if resp.status_code != 409:
            bad.append(f"(c) 취소할 것 없음이 409 가 아닙니다: {resp.status_code}")
        elif (body.get("error") or {}).get("type") != "NothingToCancel":
            bad.append(f"(c) kind 가 NothingToCancel 이 아닙니다: {body}")

        # (a) running task 취소 → cancelled, 늦게 끝난 스레드 결과가 버려진다
        fake.data.clear()
        fake.etags.clear()
        task_id = "abc123def456"
        running = _state_with(
            task={
                "name": "prepare",
                "status": "running",
                "startedAt": "2026-10-20T04:00:00Z",
                "finishedAt": None,
                "error": None,
                "shotId": None,
                "id": task_id,
            }
        )
        store.save_state(owner, pid, plan_id, running)
        resp = client.post(url)
        body = resp.get_json()
        if resp.status_code != 200:
            bad.append(f"(a) 취소가 200 이 아닙니다: {resp.status_code} {body}")
        task_view = (body or {}).get("task") or {}
        if task_view.get("status") != "cancelled":
            bad.append(f"(a) task 상태가 cancelled 가 아닙니다: {task_view}")
        if task_view.get("id") is not None:
            bad.append(f"(a) 취소 뒤 task.id 가 None 이 아닙니다: {task_view.get('id')}")

        # require_idle 은 running 만 막으므로 취소 직후 바로 다시 실행할 수 있어야 한다.
        state_now, _ = store.load_state(owner, pid, plan_id)
        try:
            # require_idle 은 클로저라 직접 못 부른다 — cancelled 는 running 이 아니므로 통과해야 한다.
            if (state_now.get("task") or {}).get("status") == "running":
                bad.append("(a) 취소 뒤에도 running 이라 재실행이 막힙니다")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"(a) 상태 확인 실패: {exc}")

        # 늦게 끝난 스레드: _finish 가 현재 task.id(None) != 자기 id(task_id) 이므로 결과를 버린다.
        # run_task 를 직접 흉내 내기보다, 취소가 task.id 를 바꿨는지로 '버려짐'을 확인한다
        # (_finish 의 discard 조건: current_id is not None and current_id != task_id, 또는 current_id != task_id).
        # 여기서는 취소가 남긴 상태가 그대로 유지되는지(스레드가 덮어쓰지 못하게 id 를 바꿨는지)를 본다.
        rsource = (ROOT / "app" / "src" / "vpoc" / "routes.py").read_text(encoding="utf-8")
        rt = rsource[rsource.index("def run_task("):]
        rt = rt[: rt.index("def apply_result(")]
        if "current_id != task_id" not in rt:
            bad.append("(a) _finish 가 task id 불일치로 결과를 버리지 않습니다")
        if "store.revision_of(state, watch_step) != revision" not in rt:
            bad.append("(a) 기존 revision 비교(되돌리기)가 사라졌습니다")
        if 'task_id = secrets.token_hex' not in rt:
            bad.append("(a) run_task 가 task 에 고유 id 를 넣지 않습니다")

        # (b) ④ queued/submitted 샷 취소 → cancelled + 공급자 취소 ok/rejected/unsupported
        fake.data.clear()
        fake.etags.clear()
        cancel_calls.clear()
        step4 = {
            "status": "running",
            "genId": "g20261020040000",
            "videoModel": "luma-ray2",
            "resolution": "540p",
            "oneTake": False,
            "single": False,
            "final": {"status": "none", "url": None, "error": None},
            "shots": [
                # 큐 대기 — 공급자 호출 없이 cancelled
                {"id": "s_q", "status": "queued", "attempt": 1, "mode": "text",
                 "submittedAt": None, "completedAt": None, "videoUrl": None, "note": None, "error": None},
                # 제출됨 — ark 작업(성공 → ok)
                {"id": "s_ark", "status": "submitted", "attempt": 1, "mode": "text",
                 "invocationArn": "ark/tasks/task-xyz", "outputPrefix": "p/ark",
                 "submittedAt": "2026-10-20T04:00:01Z", "completedAt": None, "videoUrl": None,
                 "note": None, "error": None},
                # 제출됨 — fal 작업(거절 → rejected)
                {"id": "s_fal", "status": "submitted", "attempt": 1, "mode": "text",
                 "invocationArn": "fal/requests/fal-ai/kling-video/req-123", "outputPrefix": "p/fal",
                 "submittedAt": "2026-10-20T04:00:02Z", "completedAt": None, "videoUrl": None,
                 "note": None, "error": None},
                # 제출됨 — Luma(Bedrock async, 취소 API 없음 → unsupported)
                {"id": "s_luma", "status": "submitted", "attempt": 1, "mode": "text",
                 "invocationArn": "arn:aws:bedrock:ap-northeast-2:1:async-invoke/abcd",
                 "outputPrefix": "p/luma",
                 "submittedAt": "2026-10-20T04:00:03Z", "completedAt": None, "videoUrl": None,
                 "note": None, "error": None},
                # 이미 완료된 샷 — 취소가 건드리지 않는다
                {"id": "s_done", "status": "completed", "attempt": 1, "mode": "text",
                 "invocationArn": "ark/tasks/task-done", "outputKey": "users/tester/done.mp4",
                 "submittedAt": "2026-10-20T03:50:00Z", "completedAt": "2026-10-20T03:55:00Z",
                 "videoUrl": None, "note": None, "error": None},
            ],
        }
        gen = _state_with(step4=step4)
        store.save_state(owner, pid, plan_id, gen)
        resp = client.post(url)
        body = resp.get_json()
        if resp.status_code != 200:
            bad.append(f"(b) 영상 취소가 200 이 아닙니다: {resp.status_code} {body}")
        after, _ = store.load_state(owner, pid, plan_id)
        s4 = after["steps"]["4"]
        if s4.get("status") != "cancelled":
            bad.append(f"(b) ④ 상태가 cancelled 가 아닙니다: {s4.get('status')}")
        by_id = {item["id"]: item for item in s4.get("shots") or []}
        expect = {
            "s_q": (None, "cancelled"),
            "s_ark": ("ok", "cancelled"),
            "s_fal": ("rejected", "cancelled"),
            "s_luma": ("unsupported", "cancelled"),
        }
        for sid, (want_remote, want_status) in expect.items():
            shot = by_id.get(sid) or {}
            if shot.get("status") != want_status:
                bad.append(f"(b) {sid} 상태가 {want_status} 가 아닙니다: {shot.get('status')}")
            if shot.get("cancelRemote") != want_remote:
                bad.append(f"(b) {sid} cancelRemote 가 {want_remote} 가 아닙니다: {shot.get('cancelRemote')}")
            if want_status == "cancelled" and shot.get("note") != "사용자가 취소했습니다":
                bad.append(f"(b) {sid} note 가 다릅니다: {shot.get('note')}")
            if sid != "s_q" and not shot.get("cancelledAt"):
                bad.append(f"(b) {sid} cancelledAt 이 없습니다")
        # 완료 샷은 그대로
        if (by_id.get("s_done") or {}).get("status") != "completed":
            bad.append("(b) 완료 샷이 취소됐습니다")
        # 공급자 취소는 제출된 ark · fal 두 건에만 시도됐다(큐·luma·완료는 호출 안 함).
        if sorted(cancel_calls) != sorted(["ark/tasks/task-xyz", "fal/requests/fal-ai/kling-video/req-123"]):
            bad.append(f"(b) 공급자 취소 호출 대상이 틀립니다: {cancel_calls}")

        # 취소 뒤 다시 조회(GET)해도 cancelled 가 done 으로 덮이지 않는다(refresh_shots 처리 확인).
        getresp = client.get(f"/api/vpoc/p/{pid}/plans/{plan_id}")
        gbody = getresp.get_json()
        if (gbody.get("steps") or {}).get("4", {}).get("status") != "cancelled":
            bad.append(f"(b) 재조회 때 ④ 상태가 cancelled 로 유지되지 않습니다: {(gbody.get('steps') or {}).get('4', {}).get('status')}")

        # (d) 취소 후 generate 가 다시 가능 — generate 는 새 genId 로 ④ 를 새로 만든다.
        # 실제 제출(모델 호출)은 응답 뒤 스레드라 외부 호출이 없다. 여기서는 generate 가 선행 조건을
        # 통과하고 ④ 가 새 회차로 running 이 되는지만 본다(MODEL_PICKER 가 꺼져 Luma 기본).
        after2, _ = store.load_state(owner, pid, plan_id)
        # ③ 은 그대로 ready 여야 한다(취소가 ③ 을 건드리지 않았다).
        if after2["steps"]["3"].get("status") != "ready":
            bad.append(f"(d) 취소 뒤 ③ 상태가 ready 가 아닙니다: {after2['steps']['3'].get('status')}")
        # generate 호출. confirmUsd 는 서버 계산값과 맞춰야 한다(Luma 5초 1샷).
        from src.vpoc import settings as _settings  # noqa: PLC0415
        expected_usd = round(_settings.shot_usd("luma-ray2", "540p", 5), 2)
        # 제출 스레드가 외부(Bedrock)를 부르지 않도록 video.submit 을 가짜 작업으로 바꾼다
        # (실제 유료 호출 금지). 가짜는 모든 저장을 메모리 fake store 로 하므로 네트워크가 없다.
        saved_video_submit = video.submit

        def fake_video_submit(**k):
            return {
                "provider": "luma-ray2",
                "invocationArn": "arn:aws:bedrock:test:async-invoke/" + (k.get("shot_id") or "s"),
                "clientRequestToken": "luma-test-" + (k.get("shot_id") or "s"),
                "outputPrefix": "p/test",
                "submittedAt": "2026-10-20T04:10:00Z",
                "promptChars": len(str(k.get("prompt") or "")),
            }

        video.submit = fake_video_submit
        try:
            gresp = client.post(
                f"/api/vpoc/p/{pid}/plans/{plan_id}/run/generate",
                json={"confirmUsd": expected_usd},
            )
            _time.sleep(0.2)  # 응답 뒤 제출 스레드가 메모리 안에서 돌 틈(외부 호출 없음)
        finally:
            video.submit = saved_video_submit
        if gresp.status_code != 200:
            bad.append(f"(d) 취소 후 generate 가 200 이 아닙니다: {gresp.status_code} {gresp.get_json()}")
        after3, _ = store.load_state(owner, pid, plan_id)
        new4 = after3["steps"]["4"]
        if new4.get("genId") in ("", None) or new4.get("genId") == "g20261020040000":
            bad.append(f"(d) generate 가 새 genId 로 ④ 를 만들지 않았습니다: {new4.get('genId')}")
        # 제출 스레드가 video.submit 을 막혀 실패시킬 수 있어 running/partial 둘 다 허용한다
        # (핵심은 '새 회차로 다시 시작됐다' — genId · 새 샷 · 선행 조건 통과).
        if new4.get("status") not in ("running", "partial"):
            bad.append(f"(d) generate 뒤 ④ 가 새로 시작되지 않았습니다: {new4.get('status')}")
        # 새 ④ 에는 취소된 옛 샷이 남지 않는다.
        new_ids = {item.get("id") for item in new4.get("shots") or []}
        if {"s_q", "s_ark", "s_fal", "s_luma"} & new_ids:
            bad.append(f"(d) 새 ④ 에 취소된 옛 샷이 남아 있습니다: {new_ids}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("취소 검사 실행 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        store.STORE = saved["store_STORE"]
        ledger.STORE = saved["ledger_STORE"]
        video.STORE = saved["video_STORE"]
        fal.STORE = saved["fal_STORE"]
        ark.cancel = saved["ark_cancel"]

    if bad:
        fail("진행 중 작업 취소 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok("진행 중 작업 취소 검사 (a)task (b)④ ok/rejected/unsupported (c)409 (d)재생성")


def check_dual() -> None:
    """dual-style(실사+애니) — 서버 API(설계자 2026-10-20). 실제 외부 호출 금지(전부 monkeypatch).

    (a) dual 꺼짐 → 기존과 동일(anime 없음, 금액 animeUsd 0)
    (b) dual 켜짐 → 샷 완료 시 anime queued → 제출 payload 에 720 이상 영상·@Video1·keep_audio,
        완료 시 outputKey·원장 1회
    (c) 3초 미만 거절, 15초 초과 자르기(stitcher.prepare_for_anime 함수 단위)
    (d) 금액에 animeUsd 포함, CostMismatch 없음(generate confirmUsd)
    (e) 취소 시 anime 도 cancelled
    (f) 옛 state(dual 없음) 열기 OK
    """
    import time as _time  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, ledger, routes, settings, store, video  # noqa: PLC0415

    bad: list[str] = []
    owner, pid, plan_id = "tester", "prj_d", "pl_dual_01"

    fake = _FakeStore()
    saved = {
        "store_STORE": store.STORE,
        "ledger_STORE": ledger.STORE,
        "video_STORE": video.STORE,
        "fal_STORE": fal.STORE,
        "fal_submit_edit": fal.submit_edit,
        "fal_poll": fal.poll,
        "video_find": video.find_output_mp4,
        "dual_flag": settings.VPOC_DUAL_STYLE_ENABLED,
        "fal_key": settings.FAL_KEY,
    }
    store.STORE = fake
    ledger.STORE = fake
    video.STORE = fake
    fal.STORE = fake
    settings.FAL_KEY = "dummy-fal-key"  # 변환 경로가 "키 없음"으로 빠지지 않게(실제 호출은 monkeypatch)

    # 원장은 메모리 fake 를 쓰므로 charge 가 실제 네트워크 없이 기록된다.
    ledger_entries: list[dict] = []
    real_charge = ledger.charge

    def fake_charge(key, **kwargs):
        ledger_entries.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    # fal 외부 호출 가짜: submit_edit 는 인자를 잡아 두고 가짜 작업을 돌려준다.
    submit_edit_calls: list[dict] = []

    def fake_submit_edit(*, endpoint, video_mp4, prompt, output_prefix, reference_jpegs_b64=None, keep_audio=True):
        submit_edit_calls.append(
            {
                "endpoint": endpoint,
                "video_bytes": len(video_mp4 or b""),
                "prompt": prompt,
                "output_prefix": output_prefix,
                "references": list(reference_jpegs_b64 or []),
                "keep_audio": keep_audio,
            }
        )
        return {
            "provider": settings.FAL_ID_PREFIX + endpoint,
            "invocationArn": f"fal/requests/{endpoint}/req-anime-1",
            "clientRequestToken": "fal-anime-req-anime-1",
            "outputPrefix": output_prefix,
            "submittedAt": "2026-10-20T08:00:00Z",
            "promptChars": len(prompt),
        }

    # anime 조회: 완료로 돌려주되 영상 복사는 하지 않는다(fake store). 출력은 find_output_mp4 가 준다.
    anime_poll_status = {"value": "Completed"}

    def fake_fal_poll(invocation_arn, output_prefix):
        if "req-anime-1" in invocation_arn:
            return {"status": anime_poll_status["value"], "failureMessage": "", "usage": None}
        return {"status": "Completed", "failureMessage": "", "usage": None}

    def fake_find(prefix, invocation_arn=""):
        if "req-anime-1" in invocation_arn:
            return {"key": prefix.rstrip("/") + "/req-anime-1/output.mp4", "size": 2048}
        return {"key": prefix.rstrip("/") + "/output.mp4", "size": 1024}

    ledger.charge = fake_charge
    fal.submit_edit = fake_submit_edit
    fal.poll = fake_fal_poll
    video.find_output_mp4 = fake_find

    # prepare_for_anime 는 실제 ffmpeg 를 돌리지 않게 함수 단위로 가짜 처리(짧은/긴 입력 흉내).
    from src import stitcher  # noqa: PLC0415

    real_prepare = stitcher.prepare_for_anime
    prepare_mode = {"value": "ok"}  # ok | short | long

    def fake_prepare(source_path, output_path, workdir):
        import os as _os  # noqa: PLC0415

        if prepare_mode["value"] == "short":
            raise stitcher.AnimeTooShort("3초보다 짧아 변환할 수 없습니다")
        # 720 으로 키운 결과 파일을 쓴다(실제 바이트는 더미). 15초 초과면 15초로 잘렸다고 본다.
        with open(output_path, "wb") as handle:
            handle.write(b"PREPARED-MP4-BYTES")
        seconds = 15.0 if prepare_mode["value"] == "long" else 5.0
        return {"path": output_path, "seconds": seconds, "height": 720}

    stitcher.prepare_for_anime = fake_prepare

    def _dual_state(dual: bool, shot_status: str = "completed") -> dict:
        state = store.new_state(owner, pid, plan_id)
        state["currentStep"] = 4
        state["maxReachedStep"] = 4
        state["steps"]["1"] = {"status": "saved", "text": "t", "characters": [], "sceneFiles": []}
        state["steps"]["2"] = {
            "status": "ready",
            "story": {"style": "live_action", "settingEn": "A pit lane"},
            "characters": [],
            "questions": [],
            "answers": {},
        }
        state["steps"]["3"] = {
            "status": "ready",
            "useKeyframes": False,
            "characters": [],
            "shots": [
                {
                    "id": "s1", "summaryKo": "샷1", "cameraKo": "", "cameraEn": "",
                    "locationEn": "A pit lane", "actionEn": "stands", "characters": [],
                    "promptEn": "Cinematic. A pit lane.", "useKeyframe": False,
                    "reviewKo": None, "durationS": 5,
                }
            ],
            "cost": {"shots": 1, "totalUsd": 3.75},
        }
        shot = {
            "id": "s1", "status": shot_status, "attempt": 1, "mode": "text",
            "submittedAt": "2026-10-20T07:50:00Z",
            "completedAt": "2026-10-20T07:55:00Z" if shot_status == "completed" else None,
            "outputKey": "users/tester/a/s1.mp4" if shot_status == "completed" else "",
            "videoUrl": None, "note": None, "error": None,
        }
        state["steps"]["4"] = {
            "status": "done" if shot_status == "completed" else "running",
            "genId": "g20261020070000",
            "videoModel": "luma-ray2",
            "resolution": "540p",
            "oneTake": False,
            "single": False,
            "dual": dual,
            "shots": [shot],
            "final": {"status": "none", "url": None, "error": None},
        }
        # 완료 샷의 실사 mp4 를 fake store 에 둔다(변환이 읽는다).
        if shot_status == "completed":
            fake.data[shot["outputKey"]] = {"_mp4": True}  # read_json 은 안 쓰고 get_bytes 를 쓴다
        return state

    # get_bytes 는 _FakeStore 에 없다 — dual 검사용으로 더미 바이트를 돌려주게 덧붙인다.
    def fake_get_bytes(key, **kwargs):
        return b"SOURCE-MP4-BYTES"

    fake.get_bytes = fake_get_bytes  # type: ignore[attr-defined]
    fake.put_bytes = lambda key, body, content_type, **kwargs: fake.data.__setitem__(key, {"_bytes": len(body)})  # type: ignore[attr-defined]

    deps = routes.VpocDependencies(
        require_auth=lambda h: h,
        current_username=lambda: owner,
        get_project=lambda p: {"id": pid, "owner": owner},
        can_read_project=lambda proj: True,
        can_write_project=lambda proj: True,
        graph_run=lambda *a, **k: [],
    )

    try:
        from flask import Flask  # noqa: PLC0415

        bp = routes.create_blueprint(deps)
        app = Flask(__name__)
        app.register_blueprint(bp)
        client = app.test_client()
        get_url = f"/api/vpoc/p/{pid}/plans/{plan_id}"

        import threading as _threading  # noqa: PLC0415

        def _join_anime_threads(timeout: float = 3.0) -> None:
            """이 검사가 띄운 제출 스레드(vpoc-anime*)를 모두 기다린다(016 수정 2026-10-20).

            제출은 mutate_state 성공 뒤 백그라운드 데몬 스레드에서 돈다. 검사끼리 스레드가 새지 않도록
            각 소검사 끝에서 이 스레드들을 join 해 제출·원장 카운터가 그 소검사 안에서 확정되게 한다.
            """
            deadline = _time.time() + timeout
            while _time.time() < deadline:
                live = [
                    t for t in _threading.enumerate()
                    if (t.name or "").startswith("vpoc-anime") and t.is_alive()
                ]
                if not live:
                    return
                for t in live:
                    t.join(timeout=max(0.0, deadline - _time.time()))

        def _wait_anime(statuses, timeout: float = 3.0) -> dict:
            """016 수정 2026-10-20: 제출은 mutate_state 성공 뒤 백그라운드 스레드에서 돈다.

            그 스레드가 anime.status 를 원하는 값들 중 하나로 바꿀 때까지(또는 timeout) 기다렸다가
            그 anime 딕셔너리를 돌려준다. preparing 은 '제출 전' 상태라 대기 조건으로 쓰지 않는다
            (스레드가 아직 안 끝난 상태에서 일찍 돌아오면 카운터가 다음 소검사로 샌다).
            """
            want = set(statuses if isinstance(statuses, (list, tuple, set)) else [statuses])
            deadline = _time.time() + timeout
            anime: dict = {}
            while _time.time() < deadline:
                st, _ = store.load_state(owner, pid, plan_id)
                anime = (st["steps"]["4"]["shots"][0].get("anime") or {})
                if anime.get("status") in want:
                    return anime
                _time.sleep(0.02)
            return anime

        # (f) 옛 state(dual 없음) 열기 OK — step4.dual 키가 아예 없는 회차.
        fake.data.clear(); fake.etags.clear()
        old = _dual_state(dual=False)
        old["steps"]["4"].pop("dual", None)
        store.save_state(owner, pid, plan_id, old)
        resp = client.get(get_url)
        gbody = resp.get_json()
        if resp.status_code != 200:
            bad.append(f"(f) 옛 state 열기가 200 이 아닙니다: {resp.status_code} {gbody}")
        else:
            shot_view = (((gbody.get("steps") or {}).get("4") or {}).get("shots") or [{}])[0]
            if shot_view.get("anime") is not None:
                bad.append(f"(f) 옛 회차인데 anime 가 null 이 아닙니다: {shot_view.get('anime')}")
            if (gbody.get("steps") or {}).get("4", {}).get("finalAnime", {}).get("status") != "none":
                bad.append("(f) 옛 회차 finalAnime 가 none 이 아닙니다")

        # (a) dual 꺼짐 → anime 없음, 금액 animeUsd 0
        settings.VPOC_DUAL_STYLE_ENABLED = False
        fake.data.clear(); fake.etags.clear()
        off = _dual_state(dual=False)
        store.save_state(owner, pid, plan_id, off)
        resp = client.get(get_url)
        gbody = resp.get_json()
        if gbody.get("dualStyle") is not False:
            bad.append(f"(a) dualStyle 가 false 가 아닙니다: {gbody.get('dualStyle')}")
        cost = (((gbody.get("steps") or {}).get("3") or {}).get("cost") or {})
        if cost.get("animeUsd") not in (0, 0.0):
            bad.append(f"(a) dual 꺼짐인데 animeUsd 가 0 이 아닙니다: {cost.get('animeUsd')}")
        shot_view = (((gbody.get("steps") or {}).get("4") or {}).get("shots") or [{}])[0]
        if shot_view.get("anime") is not None:
            bad.append("(a) dual 꺼짐인데 anime 가 생겼습니다")

        # (b) dual 켜짐 → 샷 완료 → anime queued → submitted(payload 검증) → 완료(outputKey·원장 1회)
        settings.VPOC_DUAL_STYLE_ENABLED = True
        fake.data.clear(); fake.etags.clear()
        ledger_entries.clear(); submit_edit_calls.clear()
        prepare_mode["value"] = "ok"
        anime_poll_status["value"] = "InProgress"  # 첫 조회에서는 아직 진행 중
        on = _dual_state(dual=True)
        store.save_state(owner, pid, plan_id, on)
        resp = client.get(get_url)  # refresh_shots → anime queued 준비 후 즉시 제출
        gbody = resp.get_json()
        if gbody.get("dualStyle") is not True:
            bad.append(f"(b) dualStyle 가 true 가 아닙니다: {gbody.get('dualStyle')}")
        # 016 수정: 제출은 저장 성공 뒤 백그라운드 스레드에서 돈다 — submitted 가 될 때까지 기다린다.
        anime = _wait_anime("submitted")
        _join_anime_threads()  # 제출 스레드가 끝났는지 확정(카운터가 다음 소검사로 새지 않게).
        if anime.get("status") != "submitted":
            bad.append(f"(b) anime 가 submitted 가 아닙니다: {anime.get('status')}")
        if not submit_edit_calls:
            bad.append("(b) fal.submit_edit 가 호출되지 않았습니다")
        else:
            call = submit_edit_calls[-1]
            if call["video_bytes"] <= 0:
                bad.append("(b) 변환 영상 바이트가 비었습니다")
            if "@Video1" not in call["prompt"]:
                bad.append(f"(b) 프롬프트에 @Video1 이 없습니다: {call['prompt'][:60]}")
            if call["keep_audio"] is not True:
                bad.append("(b) keep_audio 가 True 가 아닙니다")
            if call["endpoint"] != settings.VPOC_ANIME_MODEL:
                bad.append(f"(b) endpoint 가 VPOC_ANIME_MODEL 이 아닙니다: {call['endpoint']}")
        # 제출 1건 원장 계상(kind=video, usd = 5초×0.14)
        anime_charges = [e for e in ledger_entries if e.get("model") == settings.VPOC_ANIME_MODEL]
        if len(anime_charges) != 1:
            bad.append(f"(b) anime 원장 계상이 1건이 아닙니다: {len(anime_charges)}")
        elif abs(float(anime_charges[0].get("usd") or 0) - settings.anime_usd(5)) > 0.001:
            bad.append(f"(b) anime 원장 금액이 틀립니다: {anime_charges[0].get('usd')}")

        # 다시 조회 → 완료 처리(outputKey 채워짐). 조회 간격(5초) 때문에 lastPolledAt 을 비운다.
        def _clear_poll(inner):
            inner["steps"]["4"]["shots"][0]["anime"]["lastPolledAt"] = ""
        store.mutate_state(owner, pid, plan_id, _clear_poll)
        anime_poll_status["value"] = "Completed"
        resp = client.get(get_url)
        after2, _ = store.load_state(owner, pid, plan_id)
        anime2 = (after2["steps"]["4"]["shots"][0].get("anime") or {})
        if anime2.get("status") != "completed":
            bad.append(f"(b) anime 가 completed 가 아닙니다: {anime2.get('status')}")
        if not anime2.get("outputKey"):
            bad.append("(b) anime 완료인데 outputKey 가 없습니다")
        # 완료 뒤 재조회해도 원장이 추가 계상되지 않는다(제출 때 1회만).
        anime_charges2 = [e for e in ledger_entries if e.get("model") == settings.VPOC_ANIME_MODEL]
        if len(anime_charges2) != 1:
            bad.append(f"(b) 완료 후 anime 원장이 늘었습니다: {len(anime_charges2)}")

        # (c) 3초 미만 거절, 15초 초과 자르기 — stitcher.prepare_for_anime 함수 단위.
        import os as _os  # noqa: PLC0415
        import tempfile as _tempfile  # noqa: PLC0415

        prepare_mode["value"] = "short"
        with _tempfile.TemporaryDirectory() as wd:
            src = _os.path.join(wd, "s.mp4")
            with open(src, "wb") as handle:
                handle.write(b"x")
            try:
                stitcher.prepare_for_anime(src, _os.path.join(wd, "o.mp4"), wd)
                bad.append("(c) 3초 미만인데 AnimeTooShort 가 나지 않았습니다")
            except stitcher.AnimeTooShort:
                pass
            except Exception as exc:  # noqa: BLE001
                bad.append(f"(c) 3초 미만에서 다른 예외: {exc}")
        # 15초 초과 → 15초로 잘렸다고 가짜가 돌려준다(함수 계약 검증).
        prepare_mode["value"] = "long"
        with _tempfile.TemporaryDirectory() as wd:
            src = _os.path.join(wd, "s.mp4")
            with open(src, "wb") as handle:
                handle.write(b"x")
            prepared = stitcher.prepare_for_anime(src, _os.path.join(wd, "o.mp4"), wd)
            if float(prepared.get("seconds") or 0) > stitcher.ANIME_MAX_SECONDS:
                bad.append(f"(c) 15초 초과가 잘리지 않았습니다: {prepared.get('seconds')}")
            if int(prepared.get("height") or 0) < stitcher.ANIME_MIN_HEIGHT:
                bad.append(f"(c) 높이가 720 미만입니다: {prepared.get('height')}")
        # 실제 상수가 설계값과 같은지.
        if stitcher.ANIME_MIN_SECONDS != 3.0 or stitcher.ANIME_MAX_SECONDS != 15.0 or stitcher.ANIME_MIN_HEIGHT != 720:
            bad.append("(c) 변환 길이·높이 상수가 설계값(3·15·720)과 다릅니다")
        prepare_mode["value"] = "ok"

        # (d) 금액에 animeUsd 포함, CostMismatch 없음 — generate(confirmUsd = 실사+애니).
        settings.VPOC_DUAL_STYLE_ENABLED = True
        fake.data.clear(); fake.etags.clear()
        gen_state = _dual_state(dual=True, shot_status="queued")  # ③ ready, ④ 아직 생성 전처럼
        gen_state["steps"]["4"] = store.new_state(owner, pid, plan_id)["steps"]["4"]
        store.save_state(owner, pid, plan_id, gen_state)
        from src.vpoc import settings as _settings  # noqa: PLC0415
        live_usd = _settings.shot_usd("luma-ray2", "540p", 5)
        anime_usd = _settings.anime_usd(5)
        expected = round(live_usd + anime_usd, 2)
        # 제출 스레드가 외부를 부르지 않도록 video.submit 을 가짜로.
        saved_vsubmit = video.submit

        def fake_video_submit(**k):
            return {
                "provider": "luma-ray2",
                "invocationArn": "arn:aws:bedrock:test:async-invoke/" + (k.get("shot_id") or "s"),
                "clientRequestToken": "luma-test-" + (k.get("shot_id") or "s"),
                "outputPrefix": "p/test", "submittedAt": "2026-10-20T08:10:00Z",
                "promptChars": len(str(k.get("prompt") or "")),
            }

        video.submit = fake_video_submit
        try:
            # 틀린 금액(실사만) → CostMismatch 409
            wrong = client.post(f"{get_url}/run/generate", json={"confirmUsd": round(live_usd, 2)})
            if wrong.status_code != 409 or (wrong.get_json().get("error") or {}).get("type") != "CostMismatch":
                bad.append(f"(d) 실사만 금액이 CostMismatch 가 아닙니다: {wrong.status_code} {wrong.get_json()}")
            # 맞는 금액(실사+애니) → 200
            right = client.post(f"{get_url}/run/generate", json={"confirmUsd": expected})
            _time.sleep(0.15)
            if right.status_code != 200:
                bad.append(f"(d) 실사+애니 금액이 200 이 아닙니다: {right.status_code} {right.get_json()}")
            after3, _ = store.load_state(owner, pid, plan_id)
            if after3["steps"]["4"].get("dual") is not True:
                bad.append(f"(d) generate 가 dual 을 세우지 않았습니다: {after3['steps']['4'].get('dual')}")
        finally:
            video.submit = saved_vsubmit

        # (e) 취소 시 anime 도 cancelled
        saved_ark_cancel = ark.cancel
        ark.cancel = lambda arn: None  # fal.cancel 까지 가지 않게(조용히 성공)
        try:
            fake.data.clear(); fake.etags.clear()
            cancel_state = _dual_state(dual=True)
            cancel_state["steps"]["4"]["status"] = "running"
            cancel_state["steps"]["4"]["shots"][0]["anime"] = {
                "status": "submitted", "mode": "anime",
                "invocationArn": f"fal/requests/{settings.VPOC_ANIME_MODEL}/req-anime-1",
                "outputPrefix": "p/anime", "submittedAt": "2026-10-20T08:00:00Z",
                "sourceOutputKey": "users/tester/a/s1.mp4", "note": None, "error": None,
                "lastPolledAt": "", "outputKey": "",
            }
            store.save_state(owner, pid, plan_id, cancel_state)
            cresp = client.post(f"{get_url}/cancel")
            if cresp.status_code != 200:
                bad.append(f"(e) 취소가 200 이 아닙니다: {cresp.status_code} {cresp.get_json()}")
            cafter, _ = store.load_state(owner, pid, plan_id)
            canime = (cafter["steps"]["4"]["shots"][0].get("anime") or {})
            if canime.get("status") != "cancelled":
                bad.append(f"(e) anime 가 cancelled 가 아닙니다: {canime.get('status')}")
            if canime.get("note") != "사용자가 취소했습니다":
                bad.append(f"(e) anime note 가 다릅니다: {canime.get('note')}")
        finally:
            ark.cancel = saved_ark_cancel

        # (g) 016 수정: 가짜 경합(저장 PreconditionFailed)에도 fal.submit_edit 1회·원장 1회.
        #     화면 조회(get_plan)는 refresh_shots 로 선점한 뒤 save_state 로 저장한다. 첫 저장이 경합으로
        #     실패하면 get_plan 은 선점 목록을 버리고(reload) 스레드를 띄우지 않는다. 다음 조회가 다시
        #     선점(새 claim)·저장 성공해 그 claim 하나만 백그라운드로 제출한다 → 제출·원장 각각 1회.
        #     (동시 조회+드라이버가 같은 queued 를 선점하는 실제 경합을 흉내: 저장은 한쪽만 이긴다.)
        settings.VPOC_DUAL_STYLE_ENABLED = True
        fake.data.clear(); fake.etags.clear()
        ledger_entries.clear(); submit_edit_calls.clear()
        prepare_mode["value"] = "ok"
        anime_poll_status["value"] = "InProgress"
        gstate = _dual_state(dual=True)
        store.save_state(owner, pid, plan_id, gstate)
        real_save_state = store.save_state
        race = {"left": 1}  # 첫 저장만 한 번 가짜 경합(PreconditionFailed)

        def flaky_save(o, p, pl, st, etag=""):
            if race["left"] > 0:
                race["left"] -= 1
                raise store.VpocError("store.save_state", "PreconditionFailed", "가짜 경합", 409)
            return real_save_state(o, p, pl, st, etag)

        # get_plan 의 save_state 는 store.save_state 를 직접 부른다 — 그 참조를 가짜로.
        store.save_state = flaky_save
        try:
            resp = client.get(get_url)  # 첫 저장 경합 → get_plan 이 선점 버리고 reload(스레드 안 뜸)
            # get_plan 은 경합 시 refresh 를 재실행하지 않으므로, 한 번 더 조회해 확정 저장·제출시킨다.
            store.save_state = real_save_state
            resp = client.get(get_url)  # 재선점(새 claim) → 저장 성공 → 제출 스레드 1개
        finally:
            store.save_state = real_save_state
        # 제출 스레드가 끝날 때까지 기다린 뒤(터미널 상태 + join) 카운터를 확정한다.
        ganime = _wait_anime(("submitted", "failed"))
        _join_anime_threads()
        g_submits = len(submit_edit_calls)
        if g_submits != 1:
            bad.append(f"(g) 가짜 경합인데 fal.submit_edit 가 1회가 아닙니다: {g_submits}")
        g_charges = [e for e in ledger_entries if e.get("model") == settings.VPOC_ANIME_MODEL]
        if len(g_charges) != 1:
            bad.append(f"(g) 가짜 경합인데 anime 원장이 1회가 아닙니다: {len(g_charges)}")
        if ganime.get("status") != "submitted":
            bad.append(f"(g) 가짜 경합 뒤 anime 가 submitted 가 아닙니다: {ganime.get('status')}")

        # (g') 코드 단위: 선점 목록 thread-local 이 '매 시도 시작에서 비워져' 마지막 시도만 남는지.
        #     refresh_shots 가 mutate_state 재시도 때 _claims_reset() 로 이전 시도 선점을 버리는 계약.
        routes._claims_reset()
        routes._claims_add({"claim": "attempt-1"})  # 첫 시도(경합으로 버려질 선점)
        routes._claims_reset()                        # 재시도 시작 → 이전 선점 버림
        routes._claims_add({"claim": "attempt-2"})  # 성공한 마지막 시도 선점
        taken = routes._claims_take()
        if [c.get("claim") for c in taken] != ["attempt-2"]:
            bad.append(f"(g') _claims_reset 가 이전 시도 선점을 버리지 않습니다: {[c.get('claim') for c in taken]}")
        if routes._claims_take() != []:
            bad.append("(g') _claims_take 가 두 번째 호출에서 비우지 않습니다")

        # (h) 016 수정: 다른 claim 의 스레드는 제출하지 않는다. 제출 스레드가 state 를 다시 읽어
        #     그 샷의 anime.claim 이 자기 것이 아니면(경합으로 버려진 선점) fal.submit_edit 를 하지 않는다.
        #     job 스레드(이름 vpoc-anime*)의 첫 load_state 를 gate 로 잡고, 그 사이 저장된 claim 을
        #     다른 값으로 바꿔치기한다 → gate 해제 뒤 스레드는 claim 불일치로 즉시 그만둔다.
        fake.data.clear(); fake.etags.clear()
        ledger_entries.clear(); submit_edit_calls.clear()
        prepare_mode["value"] = "ok"
        anime_poll_status["value"] = "InProgress"
        hstate = _dual_state(dual=True)
        store.save_state(owner, pid, plan_id, hstate)

        gate = _threading.Event()
        released = {"done": False}
        real_load_state = store.load_state

        def gated_load(o, p, pl):
            # job 스레드의 첫 로드만 막는다. 메인 스레드·mutate_state 는 그대로.
            name = _threading.current_thread().name or ""
            if name.startswith("vpoc-anime") and not released["done"]:
                released["done"] = True
                gate.wait(timeout=3.0)
            return real_load_state(o, p, pl)

        store.load_state = gated_load
        try:
            resp = client.get(get_url)  # 선점 → claim C1, job 스레드 뜸(첫 load 에서 gate 대기)
            # 저장된 claim 을 C2 로 바꿔치기(다른 pod 가 15분 stale 후 다시 선점한 상황 흉내).
            def _swap_claim(inner):
                a = inner["steps"]["4"]["shots"][0].get("anime") or {}
                a["claim"] = "DIFFERENT-CLAIM"
                a["status"] = "preparing"
            store.save_state = real_save_state  # mutate_state 가 쓰도록 복구
            store.mutate_state(owner, pid, plan_id, _swap_claim)
            gate.set()  # job 스레드 진행 → claim 불일치 확인 → 제출하지 않음
            _join_anime_threads()  # 스레드가 완전히 끝날 때까지 기다린 뒤 카운터 확정
        finally:
            gate.set()  # 어떤 경로로든 스레드가 영원히 막히지 않게(방어적)
            store.load_state = real_load_state
            store.save_state = real_save_state
        _join_anime_threads()  # finally 이후에도 남은 스레드가 없도록 한 번 더
        if submit_edit_calls:
            bad.append(f"(h) 다른 claim 인데 fal.submit_edit 가 호출됐습니다: {len(submit_edit_calls)}")
        h_charges = [e for e in ledger_entries if e.get("model") == settings.VPOC_ANIME_MODEL]
        if h_charges:
            bad.append(f"(h) 다른 claim 인데 원장이 계상됐습니다: {len(h_charges)}")

        # (i) 016 수정: preparing 15분 초과 → 다시 queued(선점 해제). _sync_anime_targets 규칙.
        #     조회(refresh_shots) 때 preparing·claimedAt 이 15분보다 오래면 queued 로 되돌린다.
        fake.data.clear(); fake.etags.clear()
        submit_edit_calls.clear()
        anime_poll_status["value"] = "InProgress"
        istate = _dual_state(dual=True)
        # 16분 전에 선점(claimedAt)된 preparing. 15분(_ANIME_PREPARE_STALE_S)을 넘겼다.
        old_claimed = "2026-10-20T07:39:00Z"  # 완료 샷 completedAt(07:55)보다 전이지만 테스트는 now 기준
        import datetime as _dt  # noqa: PLC0415

        stale_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=16)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        istate["steps"]["4"]["shots"][0]["anime"] = {
            "status": "preparing", "claim": "STALE-CLAIM", "claimedAt": stale_at,
            "sourceOutputKey": "users/tester/a/s1.mp4", "note": None, "error": None,
        }
        store.save_state(owner, pid, plan_id, istate)
        # FAL_KEY 를 잠시 비워 15분 초과로 queued 가 된 뒤 즉시 재선점·제출되지 않게 한다
        # (queued 로 되돌아갔는지만 확인). refresh_shots 는 queued→(키 없음)→failed 로 두지 않도록
        # 먼저 _sync_anime_targets 가 preparing→queued 로 바꾸는지를 state 로 본다.
        saved_fal_key2 = settings.FAL_KEY
        settings.FAL_KEY = ""
        try:
            resp = client.get(get_url)
            iafter, _ = store.load_state(owner, pid, plan_id)
            ianime = (iafter["steps"]["4"]["shots"][0].get("anime") or {})
            # FAL_KEY 없음이면 preemption 전에 queued 로 되돌린 뒤 같은 refresh 에서 키 없음 실패로
            # 바뀔 수 있다 — 핵심은 'STALE-CLAIM preparing 이 유지되지 않았다'.
            if ianime.get("status") == "preparing" or ianime.get("claim") == "STALE-CLAIM":
                bad.append(f"(i) 15분 초과 preparing 이 해제되지 않았습니다: {ianime.get('status')}/{ianime.get('claim')}")
        finally:
            settings.FAL_KEY = saved_fal_key2

        _join_anime_threads()  # 검사가 띄운 제출 스레드가 남지 않도록 마지막으로 비운다.

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("dual 검사 실행 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        store.STORE = saved["store_STORE"]
        ledger.STORE = saved["ledger_STORE"]
        video.STORE = saved["video_STORE"]
        fal.STORE = saved["fal_STORE"]
        fal.submit_edit = saved["fal_submit_edit"]
        fal.poll = saved["fal_poll"]
        video.find_output_mp4 = saved["video_find"]
        ledger.charge = real_charge
        stitcher.prepare_for_anime = real_prepare
        settings.VPOC_DUAL_STYLE_ENABLED = saved["dual_flag"]
        settings.FAL_KEY = saved["fal_key"]

    if bad:
        fail("dual-style 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "dual-style 검사 (a)꺼짐 (b)켜짐 제출·완료·원장 (c)3초/15초 (d)animeUsd (e)취소 (f)옛 state"
            " (g)가짜경합 1회 (h)다른 claim 제출안함 (i)preparing 15분→queued"
        )


def check_vnew() -> None:
    """영상만들기_신규(vnew, 설계자 2026-10-20) — 틀만 확인한다.

    (a) VNEW 꺼짐 → video_new 생성 거절(잘못된 트랙), 블루프린트 미등록(server.py 가 플래그로 가둠)
    (b) VNEW 켜짐 → video_new 생성 허용, /api/vnew/health 200 {ok:true}
    (c) node --check 대상에 vnew.js 가 있다(check_js 가 public/*.js 전부를 보므로 존재만 확인)
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src import project_tracks  # noqa: PLC0415
    from src import vnew  # noqa: PLC0415

    bad: list[str] = []

    # --- 공통: 가짜 deps. 트랙 검증 분기만 쓰므로 나머지는 더미다. ---
    class _Err(Exception):
        pass

    def make_deps(vnew_on: bool):
        def response_error(message, status, code, **kwargs):
            return (code, status)

        return project_tracks.ProjectTrackDependencies(
            require_auth=lambda h: h,
            response_error=response_error,
            current_username=lambda: "tester",
            list_projects=lambda actor: {"shared": [], "private": []},
            project_payload=lambda p: p,
            can_read_project=lambda p: True,
            can_write_project=lambda p: True,
            command_store=object(),
            repository=object(),
            story_enabled=True,
            video_enabled=True,
            vnew_enabled=vnew_on,
        )

    deps_off = make_deps(False)
    deps_on = make_deps(True)

    # (a) 꺼짐: video_new 는 받는 트랙 집합에 없고, track_enabled 도 False,
    #     _availability_error 도 거절한다.
    if "video_new" in project_tracks._accepted_tracks(deps_off):
        bad.append("(a) VNEW 꺼짐인데 video_new 가 받는 트랙에 있습니다")
    if project_tracks.track_enabled(
        "video_new", story_enabled=True, video_enabled=True, vnew_enabled=False
    ):
        bad.append("(a) VNEW 꺼짐인데 track_enabled(video_new) 가 True 입니다")
    off_err = project_tracks._availability_error(deps_off, "video_new")
    if not off_err or off_err[1] != 403:
        bad.append(f"(a) VNEW 꺼짐인데 video_new 가용성 검사가 거절하지 않습니다: {off_err}")

    # 블루프린트 미등록: server.py 가 'if VNEW_ENABLED:' 안에서만 등록한다.
    server_src = (ROOT / "app" / "src" / "server.py").read_text(encoding="utf-8")
    if "if VNEW_ENABLED:" not in server_src:
        bad.append("(a) server.py 가 VNEW_ENABLED 플래그로 vnew 등록을 가두지 않습니다")
    reg_at = server_src.find("vnew.create_blueprint")
    gate_at = server_src.rfind("if VNEW_ENABLED:", 0, reg_at)
    if reg_at < 0 or gate_at < 0:
        bad.append("(a) vnew.create_blueprint 등록이 VNEW_ENABLED 블록 뒤에 있지 않습니다")
    if "VNEW_ENABLED = os.getenv(\"VNEW_ENABLED\"" not in server_src:
        bad.append("(a) server.py 가 VNEW_ENABLED env 를 읽지 않습니다")

    # (b) 켜짐: video_new 가 받는 트랙에 있고, track_enabled True, 가용성 검사 통과(None).
    if "video_new" not in project_tracks._accepted_tracks(deps_on):
        bad.append("(b) VNEW 켜짐인데 video_new 가 받는 트랙에 없습니다")
    if not project_tracks.track_enabled(
        "video_new", story_enabled=True, video_enabled=True, vnew_enabled=True
    ):
        bad.append("(b) VNEW 켜짐인데 track_enabled(video_new) 가 False 입니다")
    on_err = project_tracks._availability_error(deps_on, "video_new")
    if on_err is not None:
        bad.append(f"(b) VNEW 켜짐인데 video_new 가용성 검사가 거절합니다: {on_err}")
    # 트랙 상수·라벨
    if project_tracks.TRACK_VIDEO_NEW != "video_new" or "video_new" not in project_tracks.TRACKS:
        bad.append("(b) TRACK_VIDEO_NEW/TRACKS 에 video_new 가 없습니다")
    if "video_new" not in project_tracks.TRACK_LABELS:
        bad.append("(b) TRACK_LABELS 에 video_new 라벨이 없습니다")

    # (b) /api/vnew/health 200 {ok:true} — 인증은 주입된 require_auth 로 통과시킨다.
    try:
        from flask import Flask  # noqa: PLC0415

        bp = vnew.create_blueprint(vnew.VnewDependencies(require_auth=lambda h: h))
        app = Flask(__name__)
        app.register_blueprint(bp)
        client = app.test_client()
        resp = client.get("/api/vnew/health")
        body = resp.get_json()
        if resp.status_code != 200:
            bad.append(f"(b) /api/vnew/health 가 200 이 아닙니다: {resp.status_code}")
        if not body or body.get("ok") is not True:
            bad.append(f"(b) /api/vnew/health 응답이 {{ok:true}} 가 아닙니다: {body}")
        rules = [str(r) for r in app.url_map.iter_rules()]
        if not any(r.startswith("/api/vnew/health") for r in rules):
            bad.append("(b) /api/vnew/health 라우트가 없습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("(b) health 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:800])

    # (c) node --check 대상에 vnew.js 가 있다(check_js 가 public/*.js 전부를 본다).
    if not (PUBLIC / "vnew.js").exists():
        bad.append("(c) frontend/public/vnew.js 가 없습니다")
    if not (PUBLIC / "vnew.css").exists():
        bad.append("(c) frontend/public/vnew.css 가 없습니다")
    # index.html 이 vnew.js/vnew.css 를 defer·asset version 으로 싣는지.
    index_src = (PUBLIC / "index.html").read_text(encoding="utf-8")
    if "/vnew.js?v=__ASSET_VERSION__" not in index_src or "defer" not in index_src[
        index_src.find("/vnew.js"): index_src.find("/vnew.js") + 60
    ]:
        bad.append("(c) index.html 이 vnew.js 를 defer·asset version 으로 싣지 않습니다")
    if "/vnew.css?v=__ASSET_VERSION__" not in index_src:
        bad.append("(c) index.html 이 vnew.css 를 asset version 으로 싣지 않습니다")

    if bad:
        fail("영상만들기_신규(vnew) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok("영상만들기_신규(vnew) 검사 (a)꺼짐 거절·미등록 (b)켜짐 허용·health 200 (c)vnew.js/css·index 로드")


def check_planview_keys() -> None:
    """PlanView ④ 샷의 기존 키가 그대로고 새 키만 더해졌는지 본다."""
    source = (ROOT / "app" / "src" / "vpoc" / "routes.py").read_text(encoding="utf-8")
    start = source.index("def _shot4_view(")
    body = source[start : source.index("def plan_view(", start)]
    missing = [key for key in LEGACY_SHOT4_KEYS if f'"{key}":' not in body]
    added = [key for key in REGEN_SHOT4_KEYS if f'"{key}":' not in body]
    gen = source[source.index("def mutator(inner: dict):", source.index('if action == "generate"')) :]
    gen = gen[: gen.index("store.mutate_state")]
    step4_missing = [key for key in STEP4_KEYS if f'"{key}":' not in gen]
    if missing:
        fail(f"PlanView ④ 샷에서 기존 키가 사라졌습니다: {missing}")
    elif added:
        fail(f"PlanView ④ 샷에 새 키가 없습니다: {added}")
    elif step4_missing:
        fail(f"generate 가 ④ 에 새 키를 세우지 않습니다: {step4_missing}")
    else:
        ok(
            f"PlanView ④ 샷 키 — 기존 {len(LEGACY_SHOT4_KEYS)}개 유지"
            f" + 새 {len(REGEN_SHOT4_KEYS)}개, ④ genId 확인"
        )


# ---------------------------------------------------------------- 8. 캐릭터 만들기(vnew)


class _FakeCharStore:
    """vpoc.store.STORE 를 대신하는 메모리 저장소(캐릭터 만들기 검사 전용). 외부 S3 없이 돈다.

    기존 _FakeStore(취소·dual 검사용)와 이름이 겹치지 않게 별도 클래스로 둔다.
    read_json/write_json 은 etag 조건부 쓰기를 흉내 내되, 단일 스레드 검사라 경합은 없으므로
    write 는 항상 성공하고 새 etag 를 돌려준다. put_bytes/get_bytes/presign 만 더 쓴다.
    """

    def __init__(self) -> None:
        self.json: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self._seq = 0

    def _etag(self) -> str:
        self._seq += 1
        return f"etag{self._seq}"

    def read_json(self, key: str):
        if key not in self.json:
            return None, ""
        data, etag = self.json[key]
        import copy as _copy  # noqa: PLC0415

        return _copy.deepcopy(data), etag

    def write_json(self, key: str, data: dict, *, etag: str = "", create_only: bool = False) -> str:
        import copy as _copy  # noqa: PLC0415

        new_etag = self._etag()
        self.json[key] = (_copy.deepcopy(data), new_etag)
        return new_etag

    def put_bytes(self, key: str, body: bytes, content_type: str, *, bucket: str = "") -> None:
        self.blobs[key] = body

    def get_bytes(self, key: str, *, bucket: str = "") -> bytes:
        return self.blobs[key]

    def exists(self, key: str, *, bucket: str = "") -> bool:
        return key in self.blobs

    def presign(self, key: str, *, bucket: str = "", content_type: str = "", expires: int = 3600) -> str:
        return f"https://signed.example/{key}?e={expires}"


def _tiny_jpeg(width: int = 100, height: int = 100) -> bytes:
    """검사용 작은 JPEG 바이트(Pillow)."""
    import io as _io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    buffer = _io.BytesIO()
    Image.new("RGB", (width, height), (200, 180, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


def check_vnew_characters() -> None:
    """캐릭터 만들기 서버 — 외부 호출을 전부 가짜로 바꿔 파이프라인·API 를 돈다.

    (a) 생성·업로드·목록·상세 (b) build → 분석(Astra 실패 → Claude)·시트·정면·후면·3D 제출
    (face_count 60000·enable_pbr false·data URI)·복사·ready (c) 한 variant 실패해도 다른 variant ready
    (d) 진행 중 build 409, 30분 초과 failed (e) GLB 스트리밍 200·Content-Type
    (f) 다른 사용자 접근 403/404 (g) seedream.generate 기본 size 가 그대로 1920x1080
    (h) 조회 중 503 두 번 뒤 완료 → ready (i) FAILED → variant·model3d 둘 다 failed
    (j) retry3d: 이전 요청 COMPLETED 면 제출 0회·복사 1회, 없으면 제출 1회 (k) 기본 60000·PBR false.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    import threading as _threading  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import characters  # noqa: PLC0415
    from src.vpoc import seedream as _seedream_mod  # noqa: PLC0415

    # --- (g) seedream.generate 기본 size 가 그대로 1920x1080 ---
    # size 를 안 주면 기본값이 SIZE(1920x1080) 여야 기존 호출이 영향받지 않는다.
    import inspect as _inspect  # noqa: PLC0415

    sig = _inspect.signature(_seedream_mod.generate)
    default_size = sig.parameters["size"].default if "size" in sig.parameters else None
    if default_size != "1920x1080" or _seedream_mod.SIZE != "1920x1080":
        bad.append(f"(g) seedream.generate 기본 size 가 1920x1080 이 아닙니다: {default_size} / {_seedream_mod.SIZE}")

    # --- 가짜 외부 호출 ---
    fake_store = _FakeCharStore()

    # 분석: Astra(첫 모델)는 항상 실패, Claude(fallback)는 성공 → fallback 경로 검증.
    analyze_calls: list[str] = []

    def fake_analyze(blocks, *, model):
        analyze_calls.append(model)
        if model == characters.settings.ASTRA_MODEL:
            raise RuntimeError("Astra 한도 0 (가짜)")
        return (
            {
                "nameGuess": "김부장",
                "descriptionKo": "중년 남성 캐릭터. 안경을 썼다. 정장 차림.",
                "appearanceEn": "A middle-aged Korean man with glasses and short black hair.",
                "outfitEn": "A dark business suit with dress shoes.",
            },
            {"model": model},
        )

    # Seedream: variant 별 크기 캡처. 'real' variant 의 front 호출만 실패시켜 (c) 를 검증.
    seedream_sizes: dict[str, list[str]] = {}
    fail_variant = {"name": ""}

    def fake_seedream(prompt, references, *, size):
        # 어떤 variant 인지 프롬프트로 가늠(real 은 'live-action'/'real Korean actor' 포함).
        variant = "real" if ("live-action" in prompt or "real Korean actor" in prompt) else "webtoon"
        seedream_sizes.setdefault(variant, []).append(size)
        if fail_variant["name"] == variant and "Single full-body" in prompt:
            raise characters.VpocError("vnew.seedream", "Filtered", "가짜 실패", 502)
        # 시트는 가로로 긴(5칸), 정면은 세로로 긴 이미지로 돌려준다.
        if "turnaround reference sheet" in prompt:
            return _tiny_jpeg(2560, 1440)
        return _tiny_jpeg(1440, 2560)

    # fal: 제출 body 를 캡처(face_count·data URI 확인). 기본은 바로 COMPLETED 이지만,
    # 아래 fal_mode 로 상태 조회의 반응을 (h)(i) 시나리오별로 바꾼다.
    fal_calls: list[tuple[str, str, dict]] = []
    # transient_left: 상태 조회가 앞으로 몇 번 503(일시 오류)을 낼지. fail_status: True 면 FAILED.
    fal_mode = {"transient_left": 0, "fail_status": False}

    def fake_fal_request(method, url, payload=None):
        fal_calls.append((method, url, payload or {}))
        if method == "POST":
            return {"request_id": "req_" + str(len(fal_calls))}
        if url.endswith("/status"):
            if fal_mode["transient_left"] > 0:
                fal_mode["transient_left"] -= 1
                raise characters.VpocError("vnew.fal", "HTTP503", "Downstream service unavailable", 502)
            if fal_mode["fail_status"]:
                return {"status": "FAILED", "error": "가짜 fal 실패"}
            return {"status": "COMPLETED"}
        # response
        return {
            "model_glb": {"url": "https://fal.media/files/x/model.glb"},
            "thumbnail": {"url": "https://fal.media/files/x/thumb.png"},
        }

    def fake_fetch_url(url):
        if url.endswith(".glb"):
            return b"glTF\x02\x00\x00\x00fake-binary-glb"
        return _tiny_jpeg(256, 256)

    # 패치 적용
    orig = {
        "STORE": characters.STORE,
        "_analyze": characters._analyze,
        "_seedream": characters._seedream,
        "_fal_request": characters._fal_request,
        "_fetch_url": characters._fetch_url,
        "sleep": _time.sleep,
    }
    characters.STORE = fake_store
    characters._analyze = fake_analyze
    characters._seedream = fake_seedream
    characters._fal_request = fake_fal_request
    characters._fetch_url = fake_fetch_url
    characters.time.sleep = lambda *_a, **_k: None  # 폴링 대기 없이

    # build 가 백그라운드 스레드를 join 할 수 있도록 start_build 를 감싼다(검사 전용).
    started_threads: list = []
    orig_thread = characters.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    characters.threading.Thread = _tracking_thread

    def _join_builds():
        for t in started_threads:
            t.join(timeout=30)
        started_threads.clear()

    try:
        # 앱 두 개: tester 와 other(다른 사용자). owner 주입으로 격리.
        owner_box = {"name": "tester"}

        def make_app():
            app = Flask(__name__)
            bp = vnew.create_blueprint(
                vnew.VnewDependencies(
                    require_auth=lambda h: h,
                    current_owner=lambda: owner_box["name"],
                )
            )
            app.register_blueprint(bp)
            return app.test_client()

        client = make_app()

        # (a) 생성
        resp = client.post("/api/vnew/characters", json={"name": "김부장"})
        if resp.status_code != 201:
            bad.append(f"(a) 생성이 201 이 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        char_id = (resp.get_json() or {}).get("id", "")
        if not char_id:
            bad.append("(a) 생성 응답에 id 가 없습니다")

        # (a) 업로드 — multipart 2장
        import io as _io  # noqa: PLC0415

        data = {
            "files": [
                (_io.BytesIO(_tiny_jpeg()), "cut1.jpg"),
                (_io.BytesIO(_tiny_jpeg()), "cut2.jpg"),
            ]
        }
        resp = client.post(
            f"/api/vnew/characters/{char_id}/uploads",
            data=data,
            content_type="multipart/form-data",
        )
        if resp.status_code != 201:
            bad.append(f"(a) 업로드가 201 이 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        sources = (resp.get_json() or {}).get("sources", [])
        if len(sources) != 2:
            bad.append(f"(a) 업로드 결과 sources 가 2개가 아닙니다: {len(sources)}")

        # (a) 목록
        resp = client.get("/api/vnew/characters")
        rows = (resp.get_json() or {}).get("characters", [])
        if resp.status_code != 200 or not any(r.get("id") == char_id for r in rows):
            bad.append(f"(a) 목록에 생성한 캐릭터가 없습니다: {resp.status_code} {rows}")

        # (a) 상세
        resp = client.get(f"/api/vnew/characters/{char_id}")
        detail = resp.get_json() or {}
        if resp.status_code != 200 or detail.get("id") != char_id:
            bad.append(f"(a) 상세 조회 실패: {resp.status_code}")
        if len(detail.get("sources") or []) != 2:
            bad.append("(a) 상세의 sources 가 2개가 아닙니다")

        # (b) build → ready
        resp = client.post(f"/api/vnew/characters/{char_id}/build")
        if resp.status_code != 202:
            bad.append(f"(b) build 가 202 가 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        _join_builds()

        char, _ = characters.load_char("tester", char_id)
        if char.get("status") != "ready":
            bad.append(f"(b) build 후 status 가 ready 가 아닙니다: {char.get('status')} / {char.get('buildError')}")
        # 분석 fallback: Astra 먼저 호출되고 Claude 로 넘어감
        if not analyze_calls or analyze_calls[0] != characters.settings.ASTRA_MODEL:
            bad.append(f"(b) 분석이 Astra 를 먼저 부르지 않았습니다: {analyze_calls}")
        if characters.settings.ANALYZE_FALLBACK_MODEL not in analyze_calls:
            bad.append(f"(b) 분석이 Claude fallback 로 넘어가지 않았습니다: {analyze_calls}")
        if char.get("analysis", {}).get("model") != characters.settings.ANALYZE_FALLBACK_MODEL:
            bad.append(f"(b) analysis.model 이 실제 쓴 모델(Claude)이 아닙니다: {char.get('analysis', {}).get('model')}")

        # (b) 시트/정면 크기
        if seedream_sizes.get("webtoon", [])[:2] != [characters.settings.SHEET_SIZE, characters.settings.FRONT_SIZE]:
            bad.append(f"(b) webtoon Seedream 크기 순서가 시트·정면이 아닙니다: {seedream_sizes.get('webtoon')}")

        # (b) variant ready + 키·후면 자르기
        for variant in characters.settings.VARIANTS:
            v = char["variants"][variant]
            if v.get("status") != "ready":
                bad.append(f"(b) {variant} variant 가 ready 가 아닙니다: {v.get('status')} {v.get('error')}")
            for key in ("sheetKey", "frontKey", "backKey"):
                if not v.get(key) or v[key] not in fake_store.blobs:
                    bad.append(f"(b) {variant}.{key} 가 저장되지 않았습니다")
            m3d = v.get("model3d") or {}
            if m3d.get("status") != "ready" or not m3d.get("glbKey"):
                bad.append(f"(b) {variant} 3D 가 ready/glbKey 가 아닙니다: {m3d}")

        # (b) 3D 제출 body: face_count 60000 · enable_pbr false · data URI
        posts = [p for (m, u, p) in fal_calls if m == "POST"]
        if not posts:
            bad.append("(b) 3D 제출(POST)이 없습니다")
        else:
            for body in posts:
                if body.get("face_count") != 60000:
                    bad.append(f"(b) 3D face_count 가 60000 이 아닙니다: {body.get('face_count')}")
                if not str(body.get("input_image_url", "")).startswith("data:image/"):
                    bad.append("(b) 3D input_image_url 이 data URI 가 아닙니다")
                if not str(body.get("back_image_url", "")).startswith("data:image/"):
                    bad.append("(b) 3D back_image_url 이 data URI 가 아닙니다")
                if body.get("enable_pbr") is not False:
                    bad.append("(b) 3D enable_pbr 가 False 가 아닙니다")

        # (b) 후면 = 시트 5번째 칸(가로 5등분) — 저장된 back 이미지 가로가 시트의 1/5 근처
        import io as _io2  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        back_key = char["variants"]["webtoon"]["backKey"]
        sheet_key = char["variants"]["webtoon"]["sheetKey"]
        with _Img.open(_io2.BytesIO(fake_store.blobs[back_key])) as bimg, _Img.open(
            _io2.BytesIO(fake_store.blobs[sheet_key])
        ) as simg:
            expected = simg.width - int(round(simg.width / 5.0 * 4))
            if abs(bimg.width - expected) > 2:
                bad.append(f"(b) 후면 자르기 폭이 시트 5번째 칸이 아닙니다: back={bimg.width} expected~{expected}")

        # (e) GLB 스트리밍 200·Content-Type
        resp = client.get(f"/api/vnew/characters/{char_id}/model/webtoon.glb")
        if resp.status_code != 200:
            bad.append(f"(e) GLB 스트리밍이 200 이 아닙니다: {resp.status_code}")
        if resp.headers.get("Content-Type") != "model/gltf-binary":
            bad.append(f"(e) GLB Content-Type 이 model/gltf-binary 가 아닙니다: {resp.headers.get('Content-Type')}")
        if not resp.get_data():
            bad.append("(e) GLB 응답 본문이 비었습니다")

        # (c) 한 variant 실패해도 다른 variant ready — real 의 front 를 실패시켜 다시 build
        fail_variant["name"] = "real"
        resp = client.post(f"/api/vnew/characters/{char_id}/build")
        if resp.status_code != 202:
            bad.append(f"(c) 재-build 가 202 가 아닙니다: {resp.status_code}")
        _join_builds()
        char, _ = characters.load_char("tester", char_id)
        if char["variants"]["real"]["status"] != "failed":
            bad.append(f"(c) real variant 가 failed 가 아닙니다: {char['variants']['real']['status']}")
        if char["variants"]["webtoon"]["status"] != "ready":
            bad.append(f"(c) webtoon variant 가 ready 가 아닙니다: {char['variants']['webtoon']['status']}")
        if char.get("status") != "ready":
            bad.append(f"(c) 하나라도 ready 면 전체 ready 여야 합니다: {char.get('status')}")
        fail_variant["name"] = ""

        # (d) 진행 중 build 409 — status=building 으로 직접 세우고 호출
        def _set_building(d):
            d["status"] = "building"
            d["buildStartedAt"] = characters.now_iso()

        characters.mutate_char("tester", char_id, _set_building)
        resp = client.post(f"/api/vnew/characters/{char_id}/build")
        if resp.status_code != 409:
            bad.append(f"(d) 진행 중 build 가 409 가 아닙니다: {resp.status_code}")

        # (d) 30분 초과 → 조회 때 failed
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: PLC0415

        stale_at = (_dt.now(_tz.utc) - _td(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _set_stale(d):
            d["status"] = "building"
            d["buildStartedAt"] = stale_at

        characters.mutate_char("tester", char_id, _set_stale)
        resp = client.get(f"/api/vnew/characters/{char_id}")
        detail = resp.get_json() or {}
        if detail.get("status") != "failed":
            bad.append(f"(d) 30분 초과 building 이 조회 때 failed 가 되지 않습니다: {detail.get('status')}")

        # (f) 다른 사용자 접근 — owner 를 other 로 바꾸면 같은 char_id 는 안 보인다(404).
        owner_box["name"] = "other"
        resp = client.get(f"/api/vnew/characters/{char_id}")
        if resp.status_code != 404:
            bad.append(f"(f) 다른 사용자가 상세를 보는데 404 가 아닙니다: {resp.status_code}")
        resp = client.get(f"/api/vnew/characters/{char_id}/model/webtoon.glb")
        if resp.status_code != 404:
            bad.append(f"(f) 다른 사용자가 GLB 를 받는데 404 가 아닙니다: {resp.status_code}")
        resp = client.post(f"/api/vnew/characters/{char_id}/build")
        if resp.status_code not in (404, 409):
            bad.append(f"(f) 다른 사용자가 build 하는데 404 가 아닙니다: {resp.status_code}")
        owner_box["name"] = "tester"

        # (k) 기본값: face_count 60000 · enable_pbr false
        if characters.settings.HUNYUAN_FACE_COUNT != 60000:
            bad.append(f"(k) HUNYUAN_FACE_COUNT 기본이 60000 이 아닙니다: {characters.settings.HUNYUAN_FACE_COUNT}")
        if characters.settings.HUNYUAN_ENABLE_PBR is not False:
            bad.append(f"(k) HUNYUAN_ENABLE_PBR 기본이 False 가 아닙니다: {characters.settings.HUNYUAN_ENABLE_PBR}")

        # --- (h)(i) 용 새 캐릭터(업로드 1장) ---
        def _make_char_with_source(name):
            r = client.post("/api/vnew/characters", json={"name": name})
            cid = (r.get_json() or {}).get("id", "")
            d = {"files": [(_io.BytesIO(_tiny_jpeg()), "c.jpg")]}
            client.post(
                f"/api/vnew/characters/{cid}/uploads", data=d, content_type="multipart/form-data"
            )
            return cid

        # (h) 조회 중 503 두 번 뒤 완료 → ready (일시 오류는 실패로 보지 않고 다시 조회)
        hid = _make_char_with_source("h-캐릭터")
        fal_mode["transient_left"] = 2
        fal_mode["fail_status"] = False
        resp = client.post(f"/api/vnew/characters/{hid}/build")
        _join_builds()
        fal_mode["transient_left"] = 0
        hchar, _ = characters.load_char("tester", hid)
        if hchar.get("status") != "ready":
            bad.append(f"(h) 503 두 번 뒤 완료인데 ready 가 아닙니다: {hchar.get('status')}")
        for variant in characters.settings.VARIANTS:
            m3d = (hchar.get("variants") or {}).get(variant, {}).get("model3d") or {}
            if m3d.get("status") != "ready":
                bad.append(f"(h) {variant} 3D 가 일시오류 후 ready 가 아닙니다: {m3d.get('status')}")

        # (i) FAILED → variant·model3d 둘 다 failed
        iid = _make_char_with_source("i-캐릭터")
        fal_mode["fail_status"] = True
        resp = client.post(f"/api/vnew/characters/{iid}/build")
        _join_builds()
        fal_mode["fail_status"] = False
        ichar, _ = characters.load_char("tester", iid)
        for variant in characters.settings.VARIANTS:
            v = (ichar.get("variants") or {}).get(variant, {})
            if v.get("status") != "failed":
                bad.append(f"(i) {variant} variant 가 failed 가 아닙니다: {v.get('status')}")
            if (v.get("model3d") or {}).get("status") != "failed":
                bad.append(f"(i) {variant} model3d 가 failed 가 아닙니다(submitted 로 남음): {(v.get('model3d') or {}).get('status')}")

        # (j) retry3d: 이전 요청이 COMPLETED 면 제출 0회·복사 1회, 없으면 제출 1회
        def _post_count():
            return len([1 for (m, u, p) in fal_calls if m == "POST"])

        # (j-1) 이전 requestArn 이 있고 그 조회가 COMPLETED → 새 제출 0회
        jid = _make_char_with_source("j-캐릭터")
        # front/back 이미지와 실패한 3D·이전 requestArn 을 직접 세운다.
        fk = characters.front_key("tester", jid, "webtoon", 1)
        bk = characters.back_key("tester", jid, "webtoon", 1)
        fake_store.put_bytes(fk, _tiny_jpeg(1440, 2560), "image/jpeg")
        fake_store.put_bytes(bk, _tiny_jpeg(512, 2560), "image/jpeg")
        prior_arn = characters.ARN_PREFIX + characters.settings.HUNYUAN_ENDPOINT + "/req_prior"

        def _seed_failed_3d(d):
            d["version"] = 1
            d["status"] = "failed"
            v = d.setdefault("variants", {}).setdefault("webtoon", characters._empty_variant())
            v["status"] = "failed"
            v["frontKey"] = fk
            v["backKey"] = bk
            v["model3d"] = {
                "status": "failed",
                "requestArn": prior_arn,
                "glbKey": "",
                "thumbKey": "",
                "error": "이전 실패",
            }

        characters.mutate_char("tester", jid, _seed_failed_3d)
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        before = _post_count()
        resp = client.post(f"/api/vnew/characters/{jid}/variants/webtoon/retry3d")
        if resp.status_code != 202:
            bad.append(f"(j) retry3d 가 202 가 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        _join_builds()
        submits_1 = _post_count() - before
        jchar, _ = characters.load_char("tester", jid)
        jm3d = (jchar.get("variants") or {}).get("webtoon", {}).get("model3d") or {}
        if submits_1 != 0:
            bad.append(f"(j) 이전 요청이 COMPLETED 인데 새 제출이 0회가 아닙니다: {submits_1}")
        if jm3d.get("status") != "ready" or not jm3d.get("glbKey"):
            bad.append(f"(j) retry3d(재사용) 후 3D 가 ready/glbKey 가 아닙니다: {jm3d}")
        if jm3d.get("glbKey") not in fake_store.blobs:
            bad.append("(j) retry3d(재사용) GLB 복사본이 저장되지 않았습니다")

        # (j-2) 이전 requestArn 이 없으면 새 제출 1회
        jid2 = _make_char_with_source("j2-캐릭터")
        fk2 = characters.front_key("tester", jid2, "webtoon", 1)
        bk2 = characters.back_key("tester", jid2, "webtoon", 1)
        fake_store.put_bytes(fk2, _tiny_jpeg(1440, 2560), "image/jpeg")
        fake_store.put_bytes(bk2, _tiny_jpeg(512, 2560), "image/jpeg")

        def _seed_failed_3d_noarn(d):
            d["version"] = 1
            d["status"] = "failed"
            v = d.setdefault("variants", {}).setdefault("webtoon", characters._empty_variant())
            v["status"] = "failed"
            v["frontKey"] = fk2
            v["backKey"] = bk2
            v["model3d"] = {"status": "failed", "requestArn": "", "glbKey": "", "thumbKey": "", "error": "이전 실패"}

        characters.mutate_char("tester", jid2, _seed_failed_3d_noarn)
        before = _post_count()
        resp = client.post(f"/api/vnew/characters/{jid2}/variants/webtoon/retry3d")
        if resp.status_code != 202:
            bad.append(f"(j) retry3d(새 제출) 가 202 가 아닙니다: {resp.status_code}")
        _join_builds()
        submits_2 = _post_count() - before
        if submits_2 != 1:
            bad.append(f"(j) 이전 요청이 없으면 새 제출이 1회여야 합니다: {submits_2}")
        j2char, _ = characters.load_char("tester", jid2)
        j2m3d = (j2char.get("variants") or {}).get("webtoon", {}).get("model3d") or {}
        if j2m3d.get("status") != "ready" or not j2m3d.get("glbKey"):
            bad.append(f"(j) retry3d(새 제출) 후 3D 가 ready/glbKey 가 아닙니다: {j2m3d}")

        # (j-3) front/back 없으면 400
        jid3 = _make_char_with_source("j3-캐릭터")
        resp = client.post(f"/api/vnew/characters/{jid3}/variants/webtoon/retry3d")
        if resp.status_code != 400:
            bad.append(f"(j) front/back 없는 retry3d 가 400 이 아닙니다: {resp.status_code}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1200])
    finally:
        characters.STORE = orig["STORE"]
        characters._analyze = orig["_analyze"]
        characters._seedream = orig["_seedream"]
        characters._fal_request = orig["_fal_request"]
        characters._fetch_url = orig["_fetch_url"]
        characters.time.sleep = orig["sleep"]
        characters.threading.Thread = orig_thread

    if bad:
        fail("캐릭터 만들기(vnew) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "캐릭터 만들기(vnew) 검사 (a)CRUD·업로드 (b)분석 fallback·시트·정면·후면·3D"
            "(60000·PBR false·data URI)·ready (c)variant 격리 (d)409·stale failed (e)GLB 200 (f)격리 404 "
            "(g)size 기본 (h)일시오류 재조회 ready (i)FAILED 동기화 (j)retry3d 재사용·새제출 (k)기본값"
        )


# ---------------------------------------------------------------- 9. 영상만들기_신규 계획(vnew plan)


def check_vnew_plan() -> None:
    """영상만들기_신규 ①입력 ②대본 ③콘티 서버 — LLM 은 가짜(설계자 2026-10-20).

    (a) 계획 생성·업로드·자연 정렬(1,2,10,a 순)
    (b) input 검증(61초 거절·모르는 모델 거절·잘못된 order 거절)
    (c) script 202 → ready, 이미지 순서 유지·타일 상한(40)
    (d) conti 최소 5컷 보정(모델이 3컷 → 결과 ≥5), seconds 합 = totalSeconds,
        segments: 60초·최대 15초 → 4구간 이상·구간 ≤15초
    (e) 컷 하나가 최대 길이보다 길면 나눔(compute_segments 함수 단위)
    (f) 취소 시 늦게 온 결과가 버려짐
    (g) 다른 사용자 403/404, video 트랙 프로젝트 거절
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    import io as _io  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vpoc import images as _images  # noqa: PLC0415

    fake_store = _FakeCharStore()

    # --- 가짜 LLM: 대본·콘티. 블록(타일)을 캡처해 순서·상한을 검증한다. ---
    script_blocks: list[list[dict]] = []
    conti_calls = {"n": 0}

    def fake_analyze_script(blocks, *, model):
        # 모델에 보낸 타일 블록을 기록(이미지 블록만).
        script_blocks.append([b for b in blocks if "image" in b])
        payload = {
            "setting": {
                "locationKo": "교장실",
                "locationEn": "A closed principal's office",
                "enclosed": True,
                "timeKo": "낮",
                "moodKo": "긴장",
                "layoutKo": "김부장이 책상 앞에 앉아 있다",
                "layoutEn": "Kim stays seated at the desk",
            },
            # 웹툰이 중간에 장소를 바꾼다: 앞 항목(panels 0,1)은 교장실, 뒤 항목(panels 2,3)은 노을 진 거리.
            "entries": [
                {
                    "id": "e1",
                    "panels": [0, 1],
                    "speaker": "김부장",
                    "lineKo": "자네 지금 뭐 하는 건가?",
                    "situationKo": "김부장이 호통친다",
                    "emotionKo": "분노",
                    "locationKo": "교장실",
                    "locationEn": "A closed principal's office",
                    "characters": [
                        {"name": "김부장", "positionKo": "같은 자리", "positionEn": "at the desk", "actionKo": "호통친다"}
                    ],
                },
                {
                    "id": "e2",
                    "panels": [2, 3],
                    "speaker": "김부장",
                    "lineKo": "오늘은 그만하지.",
                    "situationKo": "김부장이 밖으로 나와 걷는다",
                    "emotionKo": "피곤",
                    "locationKo": "노을 진 거리",
                    "locationEn": "A city street at sunset",
                    "characters": [
                        {"name": "김부장", "positionKo": "길 가운데", "positionEn": "in the middle of the street", "actionKo": "걷는다"}
                    ],
                },
            ],
        }
        return payload, {"model": model, "inputTokens": 10, "outputTokens": 20}

    def fake_analyze_conti(blocks, *, model):
        # 첫 호출은 3컷(부족), 재요청(두 번째)에도 3컷으로 둬 서버의 '쪼개 채우기'를 검증한다.
        # cut1·cut2 는 교장실 항목(panels 0·1)을 맡지만 모델이 WRONG place 를 줬다 → 웹툰 장소로 보정.
        # cut3 은 노을 진 거리 항목(panels 3)을 맡고 모델이 올바른 장소를 줬다 → 그대로 유지.
        conti_calls["n"] += 1
        specs = [
            (0, "WRONG place that server must override"),
            (1, "WRONG place that server must override"),
            (3, "A city street at sunset"),
        ]
        cuts = []
        for index, (panel, location) in enumerate(specs):
            cuts.append(
                {
                    "id": f"c{index + 1}",
                    "no": index + 1,
                    "seconds": 20,  # 합 60, 각 컷이 최대 15초보다 길다(쪼개기도 검증).
                    "panels": [panel],
                    "shotKo": f"컷{index + 1}",
                    "cameraEn": "static camera",
                    "locationEn": location,
                    "characters": [{"name": "김부장", "positionEn": "at the desk", "actionEn": "speaks"}],
                    "dialogueKo": "자네 지금 뭐 하는 건가?",
                    "situationKo": "호통",
                    "promptEn": "Kim in the scene, static camera.",
                }
            )
        return {"cuts": cuts}, {"model": model, "inputTokens": 5, "outputTokens": 10}

    # --- 가짜 타일 자르기: 파일마다 여러 타일을 돌려줘 순서·상한을 검증한다. ---
    # 업로드는 진짜 이미지라 normalize_upload 가 다시 JPEG 로 인코딩한다. 파일을 구별하려고
    # 각 업로드를 '고유한 빨강값(label)'의 단색으로 만들고, 여기서 그 픽셀을 읽어 라벨을 복원한다.
    tiles_per_file = {"n": 3}

    def _file_label(jpeg: bytes) -> int:
        # JPEG 재인코딩으로 빨강값이 몇 단위 흔들릴 수 있다. 라벨 간격(20)의 격자에 맞춰 스냅한다
        # (라벨 = 10 + 20*k). 가장 가까운 격자값으로 되돌려 파일을 안정적으로 구별한다.
        import io as _io2  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        with _Img.open(_io2.BytesIO(jpeg)) as image:
            r, _g, _b = image.convert("RGB").getpixel((0, 0))
        k = round((int(r) - 10) / 20)
        return 10 + 20 * max(0, k)

    def fake_tiles_for_analysis(jpeg):
        # 파일 라벨(빨강값)을 타일 바이트에 심어 둔다(순서·상한 검증용).
        label = _file_label(jpeg)
        return [f"label{label}-tile{index}".encode("ascii") for index in range(tiles_per_file["n"])]

    # --- 가짜 캐릭터 로드(roster 용). 없으면 조용히 건너뛰므로 패치 불필요하지만 안전하게. ---
    def fake_load_char(owner, char_id):
        return {"name": "김부장역", "status": "ready", "analysis": {"appearanceEn": "a middle-aged man"}, "variants": {}}, "e1"

    orig = {
        "STORE": planmod.STORE,
        "_analyze_script": planmod._analyze_script,
        "_analyze_conti": planmod._analyze_conti,
        "tiles": _images.tiles_for_analysis,
        "load_char": planmod.characters.load_char,
        "Thread": planmod.threading.Thread,
        "charge_text": planmod.vpoc_ledger.charge_text,
    }
    planmod.STORE = fake_store
    planmod._analyze_script = fake_analyze_script
    planmod._analyze_conti = fake_analyze_conti
    _images.tiles_for_analysis = fake_tiles_for_analysis
    planmod.characters.load_char = fake_load_char

    # 원장은 가짜(네트워크 금지). 호출을 기록만 한다.
    ledger_calls: list[dict] = []

    def fake_charge_text(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    planmod.vpoc_ledger.charge_text = fake_charge_text

    started_threads: list = []
    orig_thread = planmod.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    planmod.threading.Thread = _tracking_thread

    def _join_tasks():
        for t in list(started_threads):
            t.join(timeout=30)
        started_threads.clear()

    def _real_image(label: int, *, height: int = 900) -> bytes:
        # 진짜 세로 긴 PNG(빨강값=label 로 파일 구별). normalize_upload 가 열 수 있어야 한다.
        import io as _io3  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        buffer = _io3.BytesIO()
        _Img.new("RGB", (600, height), (label, 180, 160)).save(buffer, format="PNG")
        return buffer.getvalue()

    try:
        owner_box = {"name": "tester"}
        project_box = {"track": "video_new", "owner": "tester"}

        def make_client():
            app = Flask(__name__)
            bp = vnew.create_blueprint(
                vnew.VnewDependencies(
                    require_auth=lambda h: h,
                    current_owner=lambda: owner_box["name"],
                    get_project=lambda pid: {"id": pid, "owner": project_box["owner"], "track": project_box["track"]},
                    can_read_project=lambda proj: True,
                    can_write_project=lambda proj: True,
                    project_owner_prefix=lambda name: name,  # 항등(검사용)
                )
            )
            app.register_blueprint(bp)
            return app.test_client()

        client = make_client()
        pid = "prj_vnew_1"

        # (a) 계획 생성(없으면 빈 계획)
        resp = client.get(f"/api/vnew/p/{pid}/plan")
        if resp.status_code != 200:
            bad.append(f"(a) GET /plan 이 200 이 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        body = resp.get_json() or {}
        if not body.get("planId"):
            bad.append("(a) 빈 계획에 planId 가 없습니다")

        # (a) 업로드 — 이름이 뒤섞인 5장(자연 정렬 검증용): 10.png,2.png,a.png,1.png,ab.png
        # 각 파일은 빨강값이 다른 진짜 세로 PNG(라벨 = 10 + i*20).
        names = ["10.png", "2.png", "a.png", "1.png", "ab.png"]
        labels = [10 + i * 20 for i in range(len(names))]
        data = {"files": [(_io.BytesIO(_real_image(labels[i])), name) for i, name in enumerate(names)]}
        resp = client.post(f"/api/vnew/p/{pid}/uploads", data=data, content_type="multipart/form-data")
        if resp.status_code != 201:
            bad.append(f"(a) 업로드가 201 이 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        added = (resp.get_json() or {}).get("added") or []
        if len(added) != 5:
            bad.append(f"(a) 업로드 결과가 5장이 아닙니다: {len(added)}")

        # (a) 자연 정렬: 1.png, 2.png, 10.png, a.png, ab.png 순 → fileId 를 그 순서로.
        resp = client.post(f"/api/vnew/p/{pid}/input/sort")
        order = (resp.get_json() or {}).get("order") or []
        # 올린 순서(names) 와 fileId 를 짝지어 기대 순서를 만든다.
        by_name = {item["name"]: item["fileId"] for item in added}
        expect_order = [by_name[n] for n in ["1.png", "2.png", "10.png", "a.png", "ab.png"]]
        if order != expect_order:
            # 실제 이름→fileId 로 순서 라벨을 풀어 보기 쉽게.
            got_names = {v: k for k, v in by_name.items()}
            bad.append(f"(a) 자연 정렬 순서가 1,2,10,a,ab 가 아닙니다: {[got_names.get(x) for x in order]}")

        # (b) input 검증: 61초 거절
        resp = client.put(f"/api/vnew/p/{pid}/input", json={"totalSeconds": 61})
        if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "BadTotalSeconds":
            bad.append(f"(b) 61초가 BadTotalSeconds 로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")
        # 0초도 거절
        resp = client.put(f"/api/vnew/p/{pid}/input", json={"totalSeconds": 0})
        if resp.status_code != 400:
            bad.append(f"(b) 0초가 거절되지 않습니다: {resp.status_code}")

        # (b) 모르는 모델 거절
        resp = client.put(f"/api/vnew/p/{pid}/input", json={"videoModel": "no-such-model-xyz"})
        if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "BadVideoModel":
            bad.append(f"(b) 모르는 모델이 BadVideoModel 로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")

        # (b) 잘못된 order 거절(모르는 fileId)
        resp = client.put(f"/api/vnew/p/{pid}/input", json={"order": ["nope1", "nope2"]})
        if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "BadOrder":
            bad.append(f"(b) 잘못된 order 가 BadOrder 로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")

        # (b) 올바른 input(60초·기본 모델) 저장 → 콘티 세그먼트 검증에 쓴다.
        resp = client.put(f"/api/vnew/p/{pid}/input", json={"totalSeconds": 60, "characters": [{"charId": "ch1", "roleName": "김부장"}]})
        if resp.status_code != 200:
            bad.append(f"(b) 올바른 input 저장이 200 이 아닙니다: {resp.status_code} {resp.get_json()}")

        # (c) script 202 → ready, 타일 순서·상한
        # 먼저 상한 검증용으로 파일당 타일을 많게(5장×... = 25) 두고, order 를 그대로 둔다.
        tiles_per_file["n"] = 3  # 5파일 × 3 = 15 타일(상한 40 이하) — 순서 유지 검증
        script_blocks.clear()
        resp = client.post(f"/api/vnew/p/{pid}/script")
        if resp.status_code != 202:
            bad.append(f"(c) POST /script 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_tasks()
        plan_after, _ = planmod.load_plan("tester", pid)
        if plan_after["script"]["status"] != "ready":
            bad.append(f"(c) script 가 ready 가 아닙니다: {plan_after['script'].get('status')} / {plan_after['script'].get('error')}")
        if plan_after["script"]["setting"].get("enclosed") is not True:
            bad.append("(c) script setting.enclosed 가 유지되지 않았습니다(배경 보존)")
        # 타일 순서: order 순으로 모든 타일이 그 순서대로 들어갔는지(첫 블록 라벨이 order[0] 라벨).
        if not script_blocks:
            bad.append("(c) script 분석에 블록이 전달되지 않았습니다")
        else:
            sent = script_blocks[-1]
            total_expected = 5 * tiles_per_file["n"]
            if len(sent) != total_expected:
                bad.append(f"(c) 보낸 타일 수가 {total_expected} 가 아닙니다: {len(sent)}")
            # order 는 sort 로 1,2,10,a,ab 순. 그 첫 fileId 의 라벨(빨강값)이 보낸 첫 타일에 있어야 한다.
            order_now = plan_after["input"]["order"]
            first_fid = order_now[0]
            idx = [a["fileId"] for a in added].index(first_fid)
            first_label = f"label{labels[idx]}-".encode("ascii")
            if sent and not sent[0]["image"]["source"]["bytes"].startswith(first_label):
                bad.append("(c) 타일 순서가 order 를 따르지 않습니다(첫 타일 라벨 불일치)")

        # (c) 타일 상한: 파일당 타일을 크게 해 전체가 40 을 넘게 → 균등 선택·순서 유지로 <=40
        tiles_per_file["n"] = 20  # 5 × 20 = 100 → 40 으로 균등 선택
        tiles, total, picked = planmod.ordered_tiles("tester", pid, plan_after["input"]["order"])
        if total != 100:
            bad.append(f"(c) 전체 타일 수가 100 이 아닙니다: {total}")
        if picked > planmod.MAX_SCRIPT_TILES or len(tiles) > planmod.MAX_SCRIPT_TILES:
            bad.append(f"(c) 타일 상한(40)을 넘겼습니다: picked={picked} len={len(tiles)}")
        if picked != planmod.MAX_SCRIPT_TILES:
            bad.append(f"(c) 상한 초과 시 정확히 40 장을 고르지 않았습니다: {picked}")
        tiles_per_file["n"] = 3

        # (d) conti 202 → ready, ≥5컷·초 합=60·세그먼트
        conti_calls["n"] = 0
        resp = client.post(f"/api/vnew/p/{pid}/conti")
        if resp.status_code != 202:
            bad.append(f"(d) POST /conti 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_tasks()
        plan_after, _ = planmod.load_plan("tester", pid)
        conti = plan_after["conti"]
        if conti["status"] != "ready":
            bad.append(f"(d) conti 가 ready 가 아닙니다: {conti.get('status')} / {conti.get('error')}")
        cuts = conti.get("cuts") or []
        if len(cuts) < planmod.MIN_CUTS:
            bad.append(f"(d) 컷이 5개 미만입니다: {len(cuts)}")
        summed = sum(int(c.get("seconds") or 0) for c in cuts)
        if summed != 60:
            bad.append(f"(d) 컷 seconds 합이 60 이 아닙니다: {summed}")
        # (d1) 모델이 지어낸 WRONG place 는 그 컷 panels 의 대본 장소로 보정된다.
        #      panels 0 또는 1(교장실 항목)을 맡은 컷은 교장실이어야 한다.
        office = "A closed principal's office"
        sunset = "A city street at sunset"
        office_cuts = [c for c in cuts if set(c.get("panels") or []) & {0, 1}]
        if not office_cuts:
            bad.append("(d1) 교장실 panels 를 맡은 컷이 없습니다(테스트 구성 오류)")
        if any(c.get("locationEn") != office for c in office_cuts):
            bad.append(f"(d1) WRONG place 가 교장실로 보정되지 않았습니다: {[c.get('locationEn') for c in office_cuts]}")
        if any(c.get("locationEn") == "WRONG place that server must override" for c in cuts):
            bad.append("(d1) 지어낸 장소(WRONG place)가 그대로 남았습니다")
        # (d2) 웹툰이 바뀐 장소(노을 진 거리, panels 3)를 맡은 컷은 그 장소를 유지한다.
        sunset_cuts = [c for c in cuts if 3 in set(c.get("panels") or [])]
        if not sunset_cuts:
            bad.append("(d2) 노을 진 거리 panels 를 맡은 컷이 없습니다(테스트 구성 오류)")
        if any(c.get("locationEn") != sunset for c in sunset_cuts):
            bad.append(f"(d2) 바뀐 장소(노을 진 거리)가 유지되지 않았습니다: {[c.get('locationEn') for c in sunset_cuts]}")
        segments = conti.get("segments") or []
        if len(segments) < 4:
            bad.append(f"(d) 60초·최대 15초인데 구간이 4개 미만입니다: {len(segments)}")
        if any(int(s.get("seconds") or 0) > 15 for s in segments):
            bad.append(f"(d) 15초를 넘는 구간이 있습니다: {[s.get('seconds') for s in segments]}")
        # 세그먼트 cutIds 합집합이 cuts 전체를 덮는지.
        seg_ids = [cid for s in segments for cid in (s.get("cutIds") or [])]
        if sorted(seg_ids) != sorted(c["id"] for c in cuts):
            bad.append("(d) 세그먼트가 모든 컷을 덮지 않습니다")

        # (e) 컷 하나가 최대 길이보다 길면 나눔 — compute_segments 함수 단위.
        long_cuts = [
            {"id": "x1", "no": 1, "seconds": 20},  # 최대 15 보다 길다 → 15+5 로 나뉜다
            {"id": "x2", "no": 2, "seconds": 10},
        ]
        expanded, segs = planmod.compute_segments([dict(c) for c in long_cuts], 15)
        if not any(c["id"].startswith("x1-") for c in expanded):
            bad.append(f"(e) 20초 컷이 쪼개지지 않았습니다: {[c['id'] for c in expanded]}")
        if any(int(c["seconds"]) > 15 for c in expanded):
            bad.append(f"(e) 쪼갠 뒤에도 15초 초과 컷이 있습니다: {[(c['id'], c['seconds']) for c in expanded]}")
        if any(int(s["seconds"]) > 15 for s in segs):
            bad.append(f"(e) 쪼갠 뒤 구간이 15초를 넘습니다: {[s['seconds'] for s in segs]}")

        # (f) 취소 시 늦게 온 결과가 버려진다 — _commit_step 이 task.id 불일치면 저장하지 않는다.
        # script 를 다시 시작해 task_id 를 얻고, 취소(task.id→None)한 뒤 그 task_id 로 _commit_step 을 호출.
        task_id = planmod.start_task("tester", pid, "script")
        planmod.cancel_task("tester", pid)  # task.id 를 None 으로
        before, _ = planmod.load_plan("tester", pid)
        before_entries = len((before.get("script") or {}).get("entries") or [])
        planmod._commit_step("tester", pid, "script", task_id, {"status": "ready", "entries": [{"id": "ZZZ"}], "setting": {}})
        after, _ = planmod.load_plan("tester", pid)
        after_entries = len((after.get("script") or {}).get("entries") or [])
        if after_entries != before_entries:
            bad.append("(f) 취소된 뒤 늦게 온 결과가 버려지지 않고 저장됐습니다")
        if (after.get("task") or {}).get("status") == "done":
            bad.append("(f) 취소된 task 가 늦은 결과로 done 이 됐습니다")

        # (g) 다른 사용자: owner 는 주입된 current_owner 가 아니라 프로젝트 owner 를 쓴다.
        #     프로젝트 owner 를 other 로 바꾸면 저장 경로가 달라 tester 의 계획이 안 보인다 →
        #     하지만 권한은 can_read/can_write 가 결정한다. 권한 거부를 흉내 내려면 can_read False.
        deny_app = Flask(__name__)
        deny_bp = vnew.create_blueprint(
            vnew.VnewDependencies(
                require_auth=lambda h: h,
                current_owner=lambda: "tester",
                get_project=lambda p: {"id": p, "owner": "tester", "track": "video_new"},
                can_read_project=lambda proj: False,  # 읽기 거부
                can_write_project=lambda proj: False,
                project_owner_prefix=lambda name: name,
            )
        )
        deny_app.register_blueprint(deny_bp)
        deny_client = deny_app.test_client()
        resp = deny_client.get(f"/api/vnew/p/{pid}/plan")
        if resp.status_code != 403:
            bad.append(f"(g) 권한 없는 사용자가 403 을 받지 않습니다: {resp.status_code}")

        # (g) 없는 프로젝트 → 404
        nf_app = Flask(__name__)
        nf_bp = vnew.create_blueprint(
            vnew.VnewDependencies(
                require_auth=lambda h: h,
                current_owner=lambda: "tester",
                get_project=lambda p: None,  # 없음
                can_read_project=lambda proj: True,
                can_write_project=lambda proj: True,
                project_owner_prefix=lambda name: name,
            )
        )
        nf_app.register_blueprint(nf_bp)
        resp = nf_app.test_client().get(f"/api/vnew/p/{pid}/plan")
        if resp.status_code != 404:
            bad.append(f"(g) 없는 프로젝트가 404 가 아닙니다: {resp.status_code}")

        # (g) video 트랙 프로젝트 거절 → TrackMismatch 403
        vt_app = Flask(__name__)
        vt_bp = vnew.create_blueprint(
            vnew.VnewDependencies(
                require_auth=lambda h: h,
                current_owner=lambda: "tester",
                get_project=lambda p: {"id": p, "owner": "tester", "track": "video"},  # 다른 트랙
                can_read_project=lambda proj: True,
                can_write_project=lambda proj: True,
                project_owner_prefix=lambda name: name,
            )
        )
        vt_app.register_blueprint(vt_bp)
        resp = vt_app.test_client().get(f"/api/vnew/p/{pid}/plan")
        if resp.status_code != 403 or (resp.get_json().get("error") or {}).get("type") != "TrackMismatch":
            bad.append(f"(g) video 트랙 프로젝트가 TrackMismatch 403 으로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")

        # (h) setting 이 JSON 문자열(Fable 계열) → 풀어서 채운다. enclosed 는 "true" 문자열도 True.
        import json as _json  # noqa: PLC0415

        setting_obj = {
            "locationKo": "교장실",
            "locationEn": "A closed principal's office",
            "enclosed": "true",
            "timeKo": "낮",
            "moodKo": "긴장",
            "layoutKo": "책상 앞",
            "layoutEn": "at the desk",
        }
        h_script = planmod._normalize_script(
            {
                "setting": _json.dumps(setting_obj, ensure_ascii=False),
                "entries": [{"id": "e1", "panels": [0], "locationEn": "A city street"}],
            }
        )
        h_set = h_script.get("setting") or {}
        if h_set.get("locationKo") != "교장실" or h_set.get("locationEn") != "A closed principal's office":
            bad.append(f"(h) JSON 문자열 setting 이 풀려 채워지지 않았습니다: {h_set.get('locationKo')!r} {h_set.get('locationEn')!r}")
        if h_set.get("enclosed") is not True:
            bad.append(f"(h) enclosed 문자열 'true' 가 bool True 로 바뀌지 않았습니다: {h_set.get('enclosed')!r}")
        if h_set.get("timeKo") != "낮" or h_set.get("moodKo") != "긴장":
            bad.append(f"(h) JSON 문자열 setting 의 시간·분위기가 유지되지 않았습니다: {h_set!r}")

        # (i) setting 이 없고 필드가 최상위(별칭 포함) → 최상위에서 끌어온다.
        i_script = planmod._normalize_script(
            {
                "location": "교장실",  # 한글 → locationKo
                "locationEn": "A closed principal's office",
                "time": "밤",
                "mood": "불안",
                "layout": "창가",
                "enclosed": "yes",
                "entries": [{"id": "e1", "panels": [0], "locationEn": "A city street"}],
            }
        )
        i_set = i_script.get("setting") or {}
        if i_set.get("locationKo") != "교장실":
            bad.append(f"(i) 최상위 별칭 location(한글) 이 locationKo 로 안 들어갔습니다: {i_set.get('locationKo')!r}")
        if i_set.get("locationEn") != "A closed principal's office":
            bad.append(f"(i) 최상위 locationEn 이 안 들어갔습니다: {i_set.get('locationEn')!r}")
        if i_set.get("timeKo") != "밤" or i_set.get("moodKo") != "불안" or i_set.get("layoutKo") != "창가":
            bad.append(f"(i) 최상위 별칭(time/mood/layout) 이 채워지지 않았습니다: {i_set!r}")
        if i_set.get("enclosed") is not True:
            bad.append(f"(i) enclosed 'yes' 가 True 로 안 바뀌었습니다: {i_set.get('enclosed')!r}")

        # (j) setting 이 아예 없고 최상위에도 장소가 없음 → 첫 entry 의 장소로 채운다.
        j_script = planmod._normalize_script(
            {
                "entries": [
                    {"id": "e1", "panels": [0], "locationKo": "노을 진 거리", "locationEn": "A city street at sunset"},
                    {"id": "e2", "panels": [1], "locationKo": "딴 곳", "locationEn": "elsewhere"},
                ],
            }
        )
        j_set = j_script.get("setting") or {}
        if j_set.get("locationKo") != "노을 진 거리" or j_set.get("locationEn") != "A city street at sunset":
            bad.append(f"(j) setting 없음일 때 첫 entry 장소로 안 채워졌습니다: {j_set.get('locationKo')!r} {j_set.get('locationEn')!r}")

        # (k) entries 가 문자열(JSON) 이고, 그 안의 characters 도 문자열 → 둘 다 풀린다.
        k_entries = [
            {
                "id": "e1",
                "panels": [0],
                "speaker": "김부장",
                "lineKo": "자네 지금 뭐 하나?",
                "locationEn": "A closed principal's office",
                "characters": _json.dumps(
                    [{"name": "김부장", "positionKo": "책상 앞", "actionKo": "호통친다"}], ensure_ascii=False
                ),
            }
        ]
        k_script = planmod._normalize_script(
            {
                "setting": {"locationKo": "교장실", "locationEn": "A closed principal's office"},
                "entries": _json.dumps(k_entries, ensure_ascii=False),
            }
        )
        k_out = k_script.get("entries") or []
        if len(k_out) != 1:
            bad.append(f"(k) 문자열 entries 가 풀려 1개로 안 나왔습니다: {len(k_out)}")
        elif not k_out[0].get("characters") or k_out[0]["characters"][0].get("name") != "김부장":
            bad.append(f"(k) 문자열 characters 가 풀리지 않았습니다: {k_out[0].get('characters')!r}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 계획 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        planmod.STORE = orig["STORE"]
        planmod._analyze_script = orig["_analyze_script"]
        planmod._analyze_conti = orig["_analyze_conti"]
        _images.tiles_for_analysis = orig["tiles"]
        planmod.characters.load_char = orig["load_char"]
        planmod.threading.Thread = orig_thread
        planmod.vpoc_ledger.charge_text = orig["charge_text"]

    if bad:
        fail("영상만들기_신규 계획(vnew plan) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "영상만들기_신규 계획(vnew plan) 검사 (a)생성·업로드·자연정렬 (b)input 검증"
            " (c)script 순서·상한 (d)conti ≥5컷·초합·세그먼트 (e)긴 컷 분할 (f)취소 버림 (g)권한·트랙"
            " (h)setting JSON 문자열 (i)setting 최상위·별칭 (j)setting 없음→첫 entry 장소 (k)entries 문자열"
        )


# ---------------------------------------------------------------- 10. 영상만들기_신규 생성(vnew generate)


def check_vnew_generate() -> None:
    """영상만들기_신규 ④ 영상 생성 — 외부 호출 전부 가짜(설계자 2026-10-20).

    (a) estimate·CostMismatch
    (b) 캐릭터 실사 front 없음 409
    (c) 순서: opening → seg1 제출(첫 프레임=opening) → 완료 → seg2 첫 프레임=seg1 마지막 프레임
    (d) 실사 완료마다 webtoon 변환 제출, 참고 이미지 ≤4
    (e) 모두 완료 → finalReal·finalWebtoon concat 호출·done
    (f) 가짜 경합에도 구간 제출 1회
    (g) 실사 실패 → partial, retry 로 그 구간부터
    (h) 취소
    (i) select
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    import threading as _threading  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415

    fake_store = _FakeCharStore()

    # --- 가짜 외부 호출(모듈 래퍼를 전부 바꿔 끼운다) ---
    seedream_calls: list[dict] = []
    submit_video_calls: list[dict] = []
    submit_anime_calls: list[dict] = []
    concat_calls: list[int] = []
    cancel_calls: list[str] = []

    # 제출된 영상의 상태를 조회 때 돌려줄 값. 기본은 바로 Completed.
    video_poll = {"status": "Completed"}
    anime_poll = {"status": "Completed"}
    # 실사 특정 구간 실패시키기(seg_id).
    fail_real_seg = {"id": ""}

    def fake_seedream(prompt, references, *, size):
        seedream_calls.append({"prompt": prompt, "refs": len(references or []), "size": size})
        return b"OPENING-JPEG-BYTES"

    def fake_submit_video(**kwargs):
        # 첫 프레임(b64)·모델·구간 id 를 기록. clientRequestToken 으로 원장 1회.
        submit_video_calls.append(
            {
                "shot_id": kwargs.get("shot_id"),
                "keyframe_b64": kwargs.get("keyframe_jpeg_b64", ""),
                "attempt": kwargs.get("attempt"),
                "model": kwargs.get("model"),
            }
        )
        sid = kwargs.get("shot_id") or "s"
        return {
            "provider": kwargs.get("model") or "luma-ray2",
            "invocationArn": "ark/tasks/vid-" + sid + "-" + str(kwargs.get("attempt")),
            "clientRequestToken": "ark-vid-" + sid + "-" + str(kwargs.get("attempt")) + "-" + secrets_token(),
            "outputPrefix": kwargs.get("gen_id", "") + "/" + sid,
            "submittedAt": "2026-10-20T08:00:00Z",
            "promptChars": len(str(kwargs.get("prompt") or "")),
        }

    def fake_poll_video(invocation_arn, output_prefix):
        status = video_poll["status"]
        # seg 실패 지정: ARN 에 그 seg_id 가 들어 있으면 Failed.
        if fail_real_seg["id"] and ("vid-" + fail_real_seg["id"] + "-") in invocation_arn:
            return {"status": "Failed", "failureMessage": "가짜 실사 실패"}
        if status == "Completed":
            key = output_prefix.rstrip("/") + "/output.mp4"
            # 완료된 구간 mp4 를 fake store(video bucket)에 둔다.
            fake_store.put_bytes(key, b"REAL-SEG-MP4", "video/mp4", bucket="dummy-video-bucket")
            return {"status": "Completed", "failureMessage": "", "outputKey": key}
        return {"status": status, "failureMessage": ""}

    def fake_cancel_video(invocation_arn):
        cancel_calls.append(invocation_arn)
        return "ok"

    def fake_submit_anime(*, video_mp4, prompt, output_prefix, reference_jpegs_b64):
        submit_anime_calls.append(
            {"bytes": len(video_mp4 or b""), "prompt": prompt, "refs": len(reference_jpegs_b64 or [])}
        )
        return {
            "provider": "fal:anime",
            "invocationArn": "fal/requests/fal-ai/anime/req-" + secrets_token(),
            "clientRequestToken": "fal-anime-" + secrets_token(),
            "outputPrefix": output_prefix,
            "submittedAt": "2026-10-20T08:05:00Z",
            "promptChars": len(prompt),
        }

    def fake_last_frame(mp4):
        return b"LAST-FRAME-JPEG"

    def fake_prepare_anime(mp4):
        return {"bytes": b"PREPARED-ANIME-MP4", "seconds": 5.0}

    def fake_concat(clips):
        concat_calls.append(len(clips))
        return b"FINAL-MERGED-MP4"

    def fake_fetch_bytes(key, *, bucket=""):
        return fake_store.blobs.get(key, b"SOME-BYTES")

    # fal.poll(웹툰 조회)도 가짜로.
    from src.vpoc import fal as _fal_mod  # noqa: PLC0415

    def fake_fal_poll(invocation_arn, output_prefix):
        if anime_poll["status"] == "Completed":
            return {"status": "Completed", "failureMessage": "", "usage": None}
        return {"status": anime_poll["status"], "failureMessage": "", "usage": None}

    # ledger 는 메모리(네트워크 금지).
    from src.vpoc import ledger as _ledger_mod  # noqa: PLC0415

    ledger_calls: list[dict] = []

    def fake_charge(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    # 캐릭터 로드(실사 front 있음/없음 전환).
    char_has_real = {"value": True}

    def fake_load_char(owner, char_id):
        real = {"frontKey": "", "sheetKey": ""}
        webtoon = {"frontKey": "wf.jpg", "sheetKey": "ws.jpg"}
        if char_has_real["value"]:
            real = {"frontKey": "rf-" + char_id + ".jpg", "sheetKey": "rs-" + char_id + ".jpg"}
            fake_store.blobs.setdefault(real["frontKey"], b"REAL-FRONT")
            fake_store.blobs.setdefault(webtoon["sheetKey"], b"WEBTOON-SHEET")
        char = {
            "id": char_id,
            "name": "김부장역",
            "status": "ready",
            "analysis": {"appearanceEn": "a middle-aged man", "outfitEn": "a dark suit"},
            "variants": {"real": real, "webtoon": webtoon},
        }
        return char, "etag"

    def secrets_token():
        import secrets as _s  # noqa: PLC0415

        return _s.token_hex(3)

    # --- 패치 ---
    orig = {
        "gen_STORE": genmod.STORE,
        "plan_STORE": planmod.STORE,
        "ledger_STORE": _ledger_mod.STORE,
        "_seedream": genmod._seedream,
        "_submit_video": genmod._submit_video,
        "_poll_video": genmod._poll_video,
        "_cancel_video": genmod._cancel_video,
        "_submit_anime": genmod._submit_anime,
        "_last_frame": genmod._last_frame,
        "_prepare_anime": genmod._prepare_anime,
        "_concat": genmod._concat,
        "_fetch_bytes": genmod._fetch_bytes,
        "gen_load_char": genmod.characters.load_char,
        "fal_poll": _fal_mod.poll,
        "charge": _ledger_mod.charge,
        "Thread": genmod.threading.Thread,
    }
    genmod.STORE = fake_store
    planmod.STORE = fake_store
    _ledger_mod.STORE = fake_store
    genmod._seedream = fake_seedream
    genmod._submit_video = fake_submit_video
    genmod._poll_video = fake_poll_video
    genmod._cancel_video = fake_cancel_video
    genmod._submit_anime = fake_submit_anime
    genmod._last_frame = fake_last_frame
    genmod._prepare_anime = fake_prepare_anime
    genmod._concat = fake_concat
    genmod._fetch_bytes = fake_fetch_bytes
    genmod.characters.load_char = fake_load_char
    _fal_mod.poll = fake_fal_poll
    _ledger_mod.charge = fake_charge

    started_threads: list = []
    orig_thread = genmod.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    genmod.threading.Thread = _tracking_thread

    def _join_drivers(timeout: float = 20.0):
        deadline = _time.time() + timeout
        for t in list(started_threads):
            t.join(timeout=max(0.0, deadline - _time.time()))
        started_threads.clear()

    def _make_plan(owner, pid, *, segments, total=30, with_char=True):
        """fake store 에 ①②③ 까지 된 계획을 직접 써 둔다(생성 직전 상태)."""
        plan = planmod.new_plan()
        plan["input"]["totalSeconds"] = total
        plan["input"]["videoModel"] = "luma-ray2"
        plan["input"]["order"] = ["f1"]
        plan["input"]["images"] = [{"fileId": "f1", "name": "1.png", "width": 600, "height": 900}]
        if with_char:
            plan["input"]["characters"] = [{"charId": "ch1", "roleName": "김부장"}]
        fake_store.blobs[planmod.upload_key(owner, pid, "f1")] = b"WEBTOON-PANEL"
        # 컷: segments 의 cutIds 에 맞춰 만든다.
        cuts = []
        for seg in segments:
            for cid in seg["cutIds"]:
                cuts.append(
                    {
                        "id": cid,
                        "no": len(cuts) + 1,
                        "seconds": seg["seconds"] // max(1, len(seg["cutIds"])),
                        "panels": [0],
                        "shotKo": "컷",
                        "cameraEn": "static camera",
                        "locationEn": "A closed principal's office",
                        "characters": [{"name": "김부장", "positionEn": "at the desk", "actionEn": "speaks"}],
                        "dialogueKo": "자네 뭐 하나?",
                        "situationKo": "호통",
                        "promptEn": "Kim speaks, static camera.",
                    }
                )
        plan["script"] = {
            "status": "ready",
            "setting": {"locationEn": "A closed principal's office", "layoutEn": "at the desk"},
            "entries": [{"id": "e1", "panels": [0], "locationEn": "A closed principal's office"}],
        }
        plan["conti"] = {
            "status": "ready",
            "cuts": cuts,
            "segments": segments,
            "setting": {"locationEn": "A closed principal's office", "layoutEn": "at the desk"},
        }
        planmod.save_plan(owner, pid, plan)
        return plan

    try:
        owner = "tester"

        def make_client():
            app = Flask(__name__)
            bp = vnew.create_blueprint(
                vnew.VnewDependencies(
                    require_auth=lambda h: h,
                    current_owner=lambda: owner,
                    get_project=lambda p: {"id": p, "owner": owner, "track": "video_new"},
                    can_read_project=lambda proj: True,
                    can_write_project=lambda proj: True,
                    project_owner_prefix=lambda name: name,
                )
            )
            app.register_blueprint(bp)
            return app.test_client()

        client = make_client()

        two_segs = [
            {"id": "seg1", "cutIds": ["c1"], "seconds": 15},
            {"id": "seg2", "cutIds": ["c2"], "seconds": 15},
        ]

        # (a) estimate·CostMismatch
        pid = "prj_gen_a"
        char_has_real["value"] = True
        _make_plan(owner, pid, segments=two_segs, total=30)
        plan, _ = planmod.load_plan(owner, pid)
        est = genmod.estimate(plan)
        # 실사 = 2구간 shot_usd(luma,540p,15) 합, opening = SEEDREAM_USD, 웹툰 = anime_usd(15)*2
        from src.vpoc import settings as _settings  # noqa: PLC0415

        exp_real = round(_settings.shot_usd("luma-ray2", est["resolution"], 15) * 2, 4)
        exp_open = round(float(genmod.SEEDREAM_USD), 4)
        exp_web = round(_settings.anime_usd(15) * 2, 4)
        exp_total = round(exp_real + exp_open + exp_web, 2)
        if abs(est["totalUsd"] - exp_total) > 0.001:
            bad.append(f"(a) estimate 합이 틀립니다: {est['totalUsd']} != {exp_total}")
        if abs(est["realUsd"] - exp_real) > 0.001 or abs(est["webtoonUsd"] - exp_web) > 0.001:
            bad.append(f"(a) estimate 실사/웹툰 합이 틀립니다: {est['realUsd']}/{est['webtoonUsd']}")
        # 틀린 금액 → CostMismatch 409
        resp = client.post(f"/api/vnew/p/{pid}/generate", json={"confirmUsd": round(exp_total + 1.0, 2)})
        if resp.status_code != 409 or (resp.get_json().get("error") or {}).get("type") != "CostMismatch":
            bad.append(f"(a) 틀린 금액이 CostMismatch 409 가 아닙니다: {resp.status_code} {resp.get_json()}")

        # (b) 캐릭터 실사 front 없음 → 409 CharacterRealMissing(메시지에 이름)
        pid_b = "prj_gen_b"
        char_has_real["value"] = False
        _make_plan(owner, pid_b, segments=two_segs, total=30)
        planb, _ = planmod.load_plan(owner, pid_b)
        estb = genmod.estimate(planb)
        resp = client.post(f"/api/vnew/p/{pid_b}/generate", json={"confirmUsd": estb["totalUsd"]})
        body = resp.get_json() or {}
        if resp.status_code != 409 or (body.get("error") or {}).get("type") != "CharacterRealMissing":
            bad.append(f"(b) 실사 front 없음이 409 CharacterRealMissing 이 아닙니다: {resp.status_code} {body}")
        elif "김부장" not in str((body.get("error") or {}).get("message") or ""):
            bad.append(f"(b) 거절 메시지에 캐릭터 이름이 없습니다: {body}")
        char_has_real["value"] = True

        # (c)(d)(e) 정상 진행: opening → seg1(첫프레임=opening) → 완료 → seg2(첫프레임=seg1 last) →
        #           실사 완료마다 webtoon 제출(참고 ≤4) → 모두 완료 → finalReal·finalWebtoon concat·done
        pid_c = "prj_gen_c"
        _make_plan(owner, pid_c, segments=two_segs, total=30)
        seedream_calls.clear(); submit_video_calls.clear(); submit_anime_calls.clear()
        concat_calls.clear(); ledger_calls.clear()
        video_poll["status"] = "Completed"
        anime_poll["status"] = "Completed"
        fail_real_seg["id"] = ""
        planc, _ = planmod.load_plan(owner, pid_c)
        estc = genmod.estimate(planc)
        resp = client.post(f"/api/vnew/p/{pid_c}/generate", json={"confirmUsd": estc["totalUsd"]})
        if resp.status_code != 202:
            bad.append(f"(c) generate 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_drivers()

        final_plan, _ = planmod.load_plan(owner, pid_c)
        gen = final_plan.get("gen") or {}
        if gen.get("status") != "done":
            bad.append(f"(e) gen.status 가 done 이 아닙니다: {gen.get('status')} opening={gen.get('opening')}")
        # opening 1회(Seedream)
        if len(seedream_calls) != 1:
            bad.append(f"(c) opening Seedream 호출이 1회가 아닙니다: {len(seedream_calls)}")
        # 실사 제출 2회(seg1,seg2)
        real_submits = [c for c in submit_video_calls]
        if len(real_submits) != 2:
            bad.append(f"(c) 실사 제출이 2회가 아닙니다: {len(real_submits)}")
        else:
            # seg1 첫 프레임 = opening 바이트(b64), seg2 첫 프레임 = seg1 last frame
            import base64 as _b64  # noqa: PLC0415

            s1 = next((c for c in real_submits if c["shot_id"] == "seg1"), None)
            s2 = next((c for c in real_submits if c["shot_id"] == "seg2"), None)
            if not s1 or _b64.b64decode(s1["keyframe_b64"]) != b"OPENING-JPEG-BYTES":
                bad.append("(c) seg1 첫 프레임이 opening 이미지가 아닙니다")
            if not s2 or _b64.b64decode(s2["keyframe_b64"]) != b"LAST-FRAME-JPEG":
                bad.append("(c) seg2 첫 프레임이 seg1 마지막 프레임이 아닙니다")
        # (d) 웹툰 변환 2회(실사 완료마다), 참고 이미지 ≤4
        if len(submit_anime_calls) != 2:
            bad.append(f"(d) 웹툰 변환 제출이 2회가 아닙니다: {len(submit_anime_calls)}")
        if any(c["refs"] > 4 for c in submit_anime_calls):
            bad.append(f"(d) 웹툰 변환 참고 이미지가 4장을 넘습니다: {[c['refs'] for c in submit_anime_calls]}")
        # (e) 병합: 실사·웹툰 각각 concat 호출(각 2클립), finalReal·finalWebtoon done
        if len(concat_calls) != 2:
            bad.append(f"(e) concat 호출이 2회(실사·웹툰)가 아닙니다: {concat_calls}")
        if (gen.get("finalReal") or {}).get("status") != "done" or not (gen.get("finalReal") or {}).get("key"):
            bad.append(f"(e) finalReal 이 done/key 가 아닙니다: {gen.get('finalReal')}")
        if (gen.get("finalWebtoon") or {}).get("status") != "done" or not (gen.get("finalWebtoon") or {}).get("key"):
            bad.append(f"(e) finalWebtoon 이 done/key 가 아닙니다: {gen.get('finalWebtoon')}")
        # 원장: 영상 제출 2 + 웹툰 2 + opening 1 = 토큰 중복 없이 계상
        video_charges = [e for e in ledger_calls if e.get("kind") == "video"]
        if len(video_charges) < 4:
            bad.append(f"(e) 영상·웹툰 원장 계상이 4건 미만입니다: {len(video_charges)}")

        # (f) 가짜 경합에도 구간 제출 1회 — save_plan 첫 저장을 한 번 경합시켜도 seg1 제출이 1회.
        pid_f = "prj_gen_f"
        _make_plan(owner, pid_f, segments=[{"id": "seg1", "cutIds": ["c1"], "seconds": 15}], total=15)
        submit_video_calls.clear(); ledger_calls.clear()
        video_poll["status"] = "Completed"; anime_poll["status"] = "Completed"; fail_real_seg["id"] = ""
        real_save = planmod.save_plan
        race = {"left": 1}

        def flaky_save(o, p, data, etag=""):
            if race["left"] > 0:
                race["left"] -= 1
                raise planmod.VpocError("plan.save", "PreconditionFailed", "가짜 경합", 409)
            return real_save(o, p, data, etag)

        planmod.save_plan = flaky_save
        try:
            planf, _ = planmod.load_plan(owner, pid_f)
            estf = genmod.estimate(planf)
            resp = client.post(f"/api/vnew/p/{pid_f}/generate", json={"confirmUsd": estf["totalUsd"]})
            _join_drivers()
        finally:
            planmod.save_plan = real_save
        seg1_submits = [c for c in submit_video_calls if c["shot_id"] == "seg1"]
        if len(seg1_submits) != 1:
            bad.append(f"(f) 가짜 경합인데 seg1 제출이 1회가 아닙니다: {len(seg1_submits)}")
        f_video_charges = [e for e in ledger_calls if e.get("kind") == "video" and "seg1" in str(e.get("key"))]
        if len(f_video_charges) != 1:
            bad.append(f"(f) 가짜 경합인데 seg1 영상 원장이 1회가 아닙니다: {len(f_video_charges)}")

        # (g) 실사 실패 → partial, retry 로 그 구간부터
        pid_g = "prj_gen_g"
        _make_plan(owner, pid_g, segments=two_segs, total=30)
        submit_video_calls.clear()
        video_poll["status"] = "Completed"; anime_poll["status"] = "Completed"
        fail_real_seg["id"] = "seg1"  # seg1 실사 실패
        plang, _ = planmod.load_plan(owner, pid_g)
        estg = genmod.estimate(plang)
        resp = client.post(f"/api/vnew/p/{pid_g}/generate", json={"confirmUsd": estg["totalUsd"]})
        _join_drivers()
        gplan, _ = planmod.load_plan(owner, pid_g)
        ggen = gplan.get("gen") or {}
        if ggen.get("status") != "partial":
            bad.append(f"(g) 실사 실패인데 partial 이 아닙니다: {ggen.get('status')}")
        seg1_state = (ggen.get("segments") or [{}])[0].get("real") or {}
        if seg1_state.get("status") != "failed":
            bad.append(f"(g) seg1 실사가 failed 가 아닙니다: {seg1_state.get('status')}")
        seg2_state = (ggen.get("segments") or [{}, {}])[1].get("real") or {}
        if seg2_state.get("status") not in ("waiting", "queued"):
            bad.append(f"(g) 실패 뒤 seg2 가 진행됐습니다(순서 보장 깨짐): {seg2_state.get('status')}")
        # retry: 이제 실패를 풀고 seg1 부터 다시.
        fail_real_seg["id"] = ""
        submit_video_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid_g}/generate/retry", json={"segId": "seg1"})
        if resp.status_code != 202:
            bad.append(f"(g) retry 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_drivers()
        gplan2, _ = planmod.load_plan(owner, pid_g)
        ggen2 = gplan2.get("gen") or {}
        if ggen2.get("status") != "done":
            bad.append(f"(g) retry 뒤 done 이 아닙니다: {ggen2.get('status')}")
        seg1_after = (ggen2.get("segments") or [{}])[0].get("real") or {}
        if seg1_after.get("status") != "completed":
            bad.append(f"(g) retry 뒤 seg1 이 completed 가 아닙니다: {seg1_after.get('status')}")

        # (h) 취소 — 제출됐지만 아직 완료 안 된 상태에서 취소.
        pid_h = "prj_gen_h"
        _make_plan(owner, pid_h, segments=two_segs, total=30)
        cancel_calls.clear(); submit_video_calls.clear()
        video_poll["status"] = "InProgress"  # 제출 뒤 완료되지 않게(취소 대상 만들기)
        anime_poll["status"] = "InProgress"
        fail_real_seg["id"] = ""
        planh, _ = planmod.load_plan(owner, pid_h)
        esth = genmod.estimate(planh)
        resp = client.post(f"/api/vnew/p/{pid_h}/generate", json={"confirmUsd": esth["totalUsd"]})
        # 드라이버가 opening·seg1 제출까지 하고 seg1 poll 에서 InProgress 로 쉰다. 잠깐 기다린다.
        _time.sleep(0.5)
        resp = client.post(f"/api/vnew/p/{pid_h}/cancel")
        if resp.status_code != 200:
            bad.append(f"(h) 취소가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        video_poll["status"] = "Completed"; anime_poll["status"] = "Completed"
        _join_drivers()
        hplan, _ = planmod.load_plan(owner, pid_h)
        hgen = hplan.get("gen") or {}
        if hgen.get("status") != "cancelled":
            bad.append(f"(h) 취소 뒤 gen.status 가 cancelled 가 아닙니다: {hgen.get('status')}")
        # 제출됐던 seg1 실사는 cancelled, 공급자 취소가 시도됐다(ok).
        hseg1 = (hgen.get("segments") or [{}])[0].get("real") or {}
        if hseg1.get("status") != "cancelled":
            bad.append(f"(h) 취소 뒤 seg1 실사가 cancelled 가 아닙니다: {hseg1.get('status')}")
        if hseg1.get("invocationArn") and not cancel_calls:
            bad.append("(h) 제출된 구간인데 공급자 취소가 시도되지 않았습니다")

        # (i) select — done 일 때만, 고른 그림체가 selected 에 남는다.
        pid_i = "prj_gen_i"
        _make_plan(owner, pid_i, segments=two_segs, total=30)
        video_poll["status"] = "Completed"; anime_poll["status"] = "Completed"; fail_real_seg["id"] = ""
        plani, _ = planmod.load_plan(owner, pid_i)
        esti = genmod.estimate(plani)
        resp = client.post(f"/api/vnew/p/{pid_i}/generate", json={"confirmUsd": esti["totalUsd"]})
        _join_drivers()
        # done 전 select 는 거절돼야 하지만 여기선 이미 done. 먼저 done 확인.
        iplan, _ = planmod.load_plan(owner, pid_i)
        if (iplan.get("gen") or {}).get("status") != "done":
            bad.append(f"(i) select 전 gen 이 done 이 아닙니다: {(iplan.get('gen') or {}).get('status')}")
        resp = client.post(f"/api/vnew/p/{pid_i}/select", json={"variant": "webtoon"})
        if resp.status_code != 200:
            bad.append(f"(i) select 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        iplan2, _ = planmod.load_plan(owner, pid_i)
        if (iplan2.get("gen") or {}).get("selected") != "webtoon":
            bad.append(f"(i) selected 가 webtoon 이 아닙니다: {(iplan2.get('gen') or {}).get('selected')}")
        # 잘못된 variant 거절
        resp = client.post(f"/api/vnew/p/{pid_i}/select", json={"variant": "nope"})
        if resp.status_code != 400:
            bad.append(f"(i) 잘못된 variant select 가 400 이 아닙니다: {resp.status_code}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 생성 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1800])
    finally:
        genmod.STORE = orig["gen_STORE"]
        planmod.STORE = orig["plan_STORE"]
        _ledger_mod.STORE = orig["ledger_STORE"]
        genmod._seedream = orig["_seedream"]
        genmod._submit_video = orig["_submit_video"]
        genmod._poll_video = orig["_poll_video"]
        genmod._cancel_video = orig["_cancel_video"]
        genmod._submit_anime = orig["_submit_anime"]
        genmod._last_frame = orig["_last_frame"]
        genmod._prepare_anime = orig["_prepare_anime"]
        genmod._concat = orig["_concat"]
        genmod._fetch_bytes = orig["_fetch_bytes"]
        genmod.characters.load_char = orig["gen_load_char"]
        _fal_mod.poll = orig["fal_poll"]
        _ledger_mod.charge = orig["charge"]
        genmod.threading.Thread = orig["Thread"]

    if bad:
        fail("영상만들기_신규 생성(vnew generate) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "영상만들기_신규 생성(vnew generate) 검사 (a)estimate·CostMismatch (b)실사 front 없음 409"
            " (c)순서 opening→seg1(opening)→seg2(last frame) (d)webtoon 변환·참고≤4 (e)concat·done"
            " (f)가짜 경합 1회 (g)실사 실패 partial·retry (h)취소 (i)select"
        )


# ---------------------------------------------------------------- main


def main() -> int:
    print("== vpoc1 check ==")
    check_compileall()
    check_import()
    check_js()
    check_residue()
    check_compose()
    check_client_token()
    check_regen()
    check_gen_id()
    check_no_budget_cap()
    check_planview_keys()
    check_cancel()
    check_dual()
    check_vnew()
    check_vnew_characters()
    check_vnew_plan()
    check_vnew_generate()
    print()
    if warnings:
        print(f"경고 {len(warnings)}건")
    if failures:
        print(f"실패 {len(failures)}건")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
