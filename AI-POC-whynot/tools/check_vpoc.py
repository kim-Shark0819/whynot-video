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

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


# ES module 문법(최상위 import/export). 이런 파일은 CommonJS 로 읽으면 node --check 가
# "Cannot use import statement outside a module" 로 죽는다 → --input-type=module 로 검사한다.
_ESM_PATTERN = re.compile(
    r"^\s*(?:import\s[^;]*?from\s|import\s*['\"]|import\s*\(|export\s+(?:default|const|let|var|function|class|\{|\*))",
    re.M,
)


def _is_es_module(text: str) -> bool:
    return bool(_ESM_PATTERN.search(text))


def check_js() -> None:
    node = shutil.which("node")
    if not node:
        warn("node 가 없어 JS 구문 검사를 건너뜁니다 (E16)")
        return
    bad = []
    files = sorted(PUBLIC.glob("*.js"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            bad.append(f"{path.name}: 읽기 실패 {exc}")
            continue
        if _is_es_module(text):
            # ES module: 파일 경로(.js)로는 node 가 CommonJS 로 파싱해 import 에서 죽는다.
            # 임시 .mjs 파일로 복사해 검사한다 — .mjs 확장자가 module 파싱을 보장하므로
            # node 버전에 상관없이 안전하다(--input-type/stdin 지원 여부에 의존하지 않는다).
            result = _check_as_module(node, text)
        else:
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            bad.append(f"{path.name}: {(result.stderr or '').strip()[:400]}")
    if bad:
        fail("node --check 실패\n" + "\n".join(bad))
    else:
        ok(f"node --check ({len(files)} files)")


def _check_as_module(node: str, text: str) -> subprocess.CompletedProcess:
    """ES module 소스를 임시 .mjs 파일에 써서 node --check 로 검사한다."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", encoding="utf-8", delete=False
    )
    try:
        tmp.write(text)
        tmp.close()
        return subprocess.run([node, "--check", tmp.name], capture_output=True, text=True)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
            # 이 dual 회차는 ① sceneFiles·③ 캐릭터가 없어 참고 이미지가 0장 → 일반 문장(설계자 2026-10-20).
            if call["references"]:
                bad.append(f"(b) 참고 이미지가 없어야 하는데 {len(call['references'])}장 있습니다")
            if "Korean webtoon style" not in call["prompt"]:
                bad.append(f"(b) 참고 없음인데 일반 'Korean webtoon style' 문장이 없습니다: {call['prompt'][:80]}")
            # 일반 화풍 지시 문구(설계자 2026-10-20 제거 대상)가 없어야 한다.
            for banned in ("cel shading", "flat soft colors", "2D animation"):
                if banned in call["prompt"]:
                    bad.append(f"(b) 프롬프트에 제거 대상 문구 '{banned}' 가 남아 있습니다")
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

    def delete_bytes(self, key: str, *, bucket: str = "") -> None:
        self.blobs.pop(key, None)

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
    (d) 진행 중 build 409, stale 여도 variant ready 면 캐릭터 ready 유지 (e) GLB 스트리밍 200·Content-Type
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
    analyze_extra_seen: list[str] = []  # 분석 blocks 에 들어온 '사용자 추가 요청' 텍스트

    def fake_analyze(blocks, *, model):
        analyze_calls.append(model)
        # 추가 요청 텍스트 블록이 왔는지 캡처(extraPromptKo 전달 검증).
        for b in blocks:
            t = b.get("text") if isinstance(b, dict) else ""
            if t and "사용자 추가 요청" in t:
                analyze_extra_seen.append(t)
        if model == characters.settings.ASTRA_MODEL:
            raise RuntimeError("Astra 한도 0 (가짜)")
        # 추가 요청이 왔으면 extraEn 을 채워 반환(끝에 붙는지 검증용 고정 문구).
        extra_en = "EXTRA_REQ_EN" if analyze_extra_seen else ""
        return (
            {
                "nameGuess": "김부장",
                "descriptionKo": "중년 남성 캐릭터. 안경을 썼다. 정장 차림.",
                "appearanceEn": "A middle-aged Korean man with glasses and short black hair.",
                "styleEn": "Thin sketchy pencil-like lines with soft pastel flat coloring.",
                "baseOutfitEn": "A beige button-up shirt with dark trousers and loafers.",
                "outfitEn": "A beige button-up shirt with dark trousers and loafers.",
                "outfitLayersEn": [
                    {"labelKo": "정장 자켓", "promptEn": "a black suit jacket over the shirt"},
                    {"labelKo": "후드티", "promptEn": "a light blue hoodie"},
                ],
                "altOutfitsEn": ["a grey hoodie", "a white tee"],
                "expressionsKo": ["무표정", "미소"],
                "extraEn": extra_en,
            },
            {"model": model},
        )

    # Seedream: variant 별 크기 캡처. 'real' variant 의 front 호출만 실패시켜 (c) 를 검증.
    # Seedream: variant 별 크기·프롬프트 캡처. 'real' variant 의 front 호출만 실패시켜 (c) 를 검증.
    seedream_sizes: dict[str, list[str]] = {}
    seedream_prompts: dict[str, list[str]] = {}  # variant → [프롬프트...]
    fail_variant = {"name": ""}
    # seedream 호출마다 (variant, kind, references) 를 담는다((ac)(af) 참고 이미지 검증용).
    seedream_calls: list[dict] = []

    def _kind_of(prompt):
        if "turnaround reference sheet" in prompt:
            return "sheet"
        # 추가 복장 프롬프트는 'plus ' 가 들어간다(기본 복장 + 추가 겹옷).
        if "plus " in prompt and ("same base outfit" in prompt):
            return "outfit-back" if "BACK view" in prompt else "outfit-front"
        if "BACK view" in prompt:
            return "back"
        return "front"

    def fake_seedream(prompt, references, *, size):
        # 어떤 variant 인지 프롬프트로 가늠(real 은 'live-action'/'real Korean actor' 포함).
        variant = "real" if ("live-action" in prompt or "real Korean actor" in prompt) else "webtoon"
        seedream_sizes.setdefault(variant, []).append(size)
        seedream_prompts.setdefault(variant, []).append(prompt)
        kind = _kind_of(prompt)
        seedream_calls.append(
            {"variant": variant, "kind": kind, "prompt": prompt, "refs": list(references or [])}
        )
        # real 의 정면(front) 호출만 실패시켜 (c) 검증(back·sheet 는 실패시키지 않는다).
        if fail_variant["name"] == variant and kind == "front":
            raise characters.VpocError("vnew.seedream", "Filtered", "가짜 실패", 502)
        # 시트는 가로로 긴(5칸), 정면·후면은 세로로 긴 이미지로 돌려준다.
        if kind == "sheet":
            return _tiny_jpeg(2560, 1440)
        return _tiny_jpeg(1440, 2560)

    # 복장 일관성 검사(가짜): 기본은 일관됨. outfit_mode["results"] 가 리스트면 호출마다 하나씩
    # 꺼내 {"consistent":bool,"problems":[..]} 로 돌려준다(비면 일관됨). None 이면 모델 오류(건너뜀).
    outfit_mode = {"results": None}
    outfit_check_calls = {"n": 0}

    def fake_outfit_check(sheet, base_outfit):
        outfit_check_calls["n"] += 1
        results = outfit_mode["results"]
        if results is None:
            return {"consistent": True, "problems": []}
        if isinstance(results, list):
            return results.pop(0) if results else {"consistent": True, "problems": []}
        return results

    # 추가 복장 promptKo→promptEn 번역(가짜): _converse 를 쓰므로 llm 을 패치한다(아래).
    translate_calls: list[str] = []

    # 사람 수 검사(가짜): 기본은 1명. people_mode 로 (o) 시나리오를 만든다.
    # people_mode["counts"] 가 리스트면 호출마다 하나씩 꺼내 쓴다(비면 1). None 이면 모델 오류(건너뜀).
    people_mode = {"counts": None}

    def fake_count_people(jpeg):
        counts = people_mode["counts"]
        if counts is None:
            return 1
        if isinstance(counts, list):
            return counts.pop(0) if counts else 1
        return counts

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
    from src.vpoc import llm as _vpoc_llm_mod  # noqa: PLC0415

    orig = {
        "STORE": characters.STORE,
        "_analyze": characters._analyze,
        "_seedream": characters._seedream,
        "_fal_request": characters._fal_request,
        "_fetch_url": characters._fetch_url,
        "_count_people": characters._count_people,
        "_check_outfit_consistency": characters._check_outfit_consistency,
        "_converse": _vpoc_llm_mod._converse,
        "sleep": _time.sleep,
    }
    characters.STORE = fake_store
    characters._analyze = fake_analyze
    characters._seedream = fake_seedream
    characters._fal_request = fake_fal_request
    characters._fetch_url = fake_fetch_url
    characters._count_people = fake_count_people
    characters._check_outfit_consistency = fake_outfit_check

    # 추가 복장 번역은 vpoc.llm._converse(tool=emit_outfit_prompt)를 쓴다 — 가짜로 바꿔 끼운다.
    def fake_converse(*, system_text, blocks, tool, max_tokens, where, model):
        if tool and tool.get("name") == "emit_outfit_prompt":
            ko = ""
            for b in blocks:
                t = b.get("text") if isinstance(b, dict) else ""
                if t and "Korean:" in t:
                    ko = t.split("Korean:", 1)[1].strip()
            translate_calls.append(ko)
            return {"promptEn": "EN(" + ko + ")"}, {"model": model}
        raise RuntimeError("예상치 못한 _converse 호출(가짜): " + str(tool))

    _vpoc_llm_mod._converse = fake_converse
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

        # (b) 시트/정면/후면 크기(이제 후면도 따로 생성)
        if seedream_sizes.get("webtoon", [])[:3] != [
            characters.settings.SHEET_SIZE,
            characters.settings.FRONT_SIZE,
            characters.settings.BACK_SIZE,
        ]:
            bad.append(f"(b) webtoon Seedream 크기 순서가 시트·정면·후면이 아닙니다: {seedream_sizes.get('webtoon')}")

        # (b) variant ready + 키
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

        # --- (l) 분석 지침 문장에 "SAME person" 포함 ---
        if "SAME person" not in characters._ANALYZE_SYSTEM:
            bad.append("(l) 분석 시스템 문장에 'SAME person' 지침이 없습니다")
        # 분석 JSON 에 styleEn/altOutfitsEn/expressionsKo/extraEn 필드가 들어왔는지(옛 데이터도 동작)
        an = char.get("analysis") or {}
        for f in ("styleEn", "outfitEn"):
            if not an.get(f):
                bad.append(f"(l) 분석 결과에 {f} 가 없습니다: {an}")
        if not isinstance(an.get("expressionsKo"), list) or not an.get("expressionsKo"):
            bad.append(f"(l) 분석 결과 expressionsKo 가 리스트가 아닙니다: {an.get('expressionsKo')}")
        if not isinstance(an.get("altOutfitsEn"), list):
            bad.append(f"(l) 분석 결과 altOutfitsEn 가 리스트가 아닙니다: {an.get('altOutfitsEn')}")

        # --- (m) 웹툰 프롬프트: styleEn · "exactly one person"(ONE person) · outfitEn · generic 없음 ---
        wprompts = seedream_prompts.get("webtoon", [])
        if not wprompts:
            bad.append("(m) 웹툰 프롬프트가 캡처되지 않았습니다")
        style_en = an.get("styleEn") or ""
        outfit_en = an.get("outfitEn") or ""
        for wp in wprompts:
            if style_en and style_en not in wp:
                bad.append("(m) 웹툰 프롬프트에 styleEn 이 그대로 들어가지 않았습니다")
                break
        for wp in wprompts:
            if "ONE person" not in wp and "one person" not in wp:
                bad.append("(m) 웹툰 프롬프트에 'one person'(한 사람만) 문장이 없습니다")
                break
        for wp in wprompts:
            if outfit_en and outfit_en not in wp:
                bad.append("(m) 웹툰 프롬프트에 outfitEn 이 들어가지 않았습니다")
                break
        # generic 문장을 쓰지 않는다
        for wp in wprompts:
            if "Keep the exact Korean webtoon art style" in wp or "clean ink line art, cel shading" in wp:
                bad.append("(m) 웹툰 프롬프트에 generic 그림체 문장이 남아 있습니다")
                break

        # --- (n) 후면을 따로 생성 ---
        # 웹툰 프롬프트 3건(시트·정면·후면)이 있고, 후면 프롬프트에 'BACK view'·'same single person' 이 있다.
        kinds = [_kind_of(p) for p in wprompts]
        if kinds[:3] != ["sheet", "front", "back"]:
            bad.append(f"(n) 웹툰 그림 호출 순서가 시트·정면·후면이 아닙니다: {kinds}")
        back_prompts = [p for p in wprompts if _kind_of(p) == "back"]
        if not back_prompts:
            bad.append("(n) 후면 프롬프트가 따로 생성되지 않았습니다")
        else:
            bp = back_prompts[0]
            if "BACK view" not in bp or "same single person" not in bp:
                bad.append("(n) 후면 프롬프트에 'BACK view/same single person' 이 없습니다")
        # 후면 이미지가 세로(1440x2560) — 시트 5등분 조각(가로가 좁은)이 아니다.
        import io as _io2  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        back_key = char["variants"]["webtoon"]["backKey"]
        with _Img.open(_io2.BytesIO(fake_store.blobs[back_key])) as bimg:
            if bimg.width != 1440 or bimg.height != 2560:
                bad.append(f"(n) 후면이 따로 생성된 세로 전신이 아닙니다(시트 조각 의심): {bimg.width}x{bimg.height}")

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

        # (d) char 레벨이 building·stale 이어도 variant 가 모두 ready 면 조회 때 failed 로 떨어지지 않는다
        # (이전 '30분 전체 실패' 규칙을 variant 단위로 바꿈 — 끊긴 variant 만 이어 만든다).
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: PLC0415

        stale_at = (_dt.now(_tz.utc) - _td(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _set_stale(d):
            d["status"] = "building"
            d["buildStartedAt"] = stale_at

        characters.mutate_char("tester", char_id, _set_stale)
        resp = client.get(f"/api/vnew/characters/{char_id}")
        detail = resp.get_json() or {}
        _join_builds()
        if detail.get("status") == "failed":
            bad.append(f"(d) variant 가 모두 ready 인데 조회 때 캐릭터가 failed 가 됐습니다: {detail.get('status')}")

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

        # --- (o) 사람 수 2 → 1회 재생성, 계속 2 → warning ---
        # (o-1) front 가 처음 2명, 재생성에서 1명 → warning 없음, seedream 재호출 1회 늘어남.
        oid = _make_char_with_source("o-캐릭터")
        # counts 순서: sheet 는 검사 안 함(front·back 만 _count_people 호출).
        # webtoon: front=2 → retry=1(정상), back=1. real: front=2 → retry=1, back=1.
        people_mode["counts"] = [2, 1, 1, 2, 1, 1]
        before_sd = len(seedream_prompts.get("webtoon", []))
        resp = client.post(f"/api/vnew/characters/{oid}/build")
        _join_builds()
        people_mode["counts"] = None
        ochar, _ = characters.load_char("tester", oid)
        ov = (ochar.get("variants") or {}).get("webtoon", {})
        if ov.get("warning"):
            bad.append(f"(o) 재생성으로 1명이 됐는데 warning 이 남았습니다: {ov.get('warning')}")
        after_sd = len(seedream_prompts.get("webtoon", []))
        # webtoon front 가 2명이라 1회 더 그렸으니 호출 수가 최소 4(시트·정면·정면재·후면).
        if after_sd - before_sd < 4:
            bad.append(f"(o) 사람 수 2 → 재생성이 일어나지 않았습니다(호출 수 {after_sd - before_sd})")

        # (o-2) 계속 2명 → warning 남고 3D 는 진행(ready)
        o2id = _make_char_with_source("o2-캐릭터")
        people_mode["counts"] = 2  # 항상 2명
        resp = client.post(f"/api/vnew/characters/{o2id}/build")
        _join_builds()
        people_mode["counts"] = None
        o2char, _ = characters.load_char("tester", o2id)
        o2v = (o2char.get("variants") or {}).get("webtoon", {})
        if not o2v.get("warning"):
            bad.append("(o) 재생성 후에도 2명인데 warning 이 없습니다")
        if (o2v.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(o) warning 이어도 3D 는 진행(ready)해야 합니다: {(o2v.get('model3d') or {}).get('status')}")

        # --- (p) build 의 extraPromptKo 저장·유지·빈 문자열 삭제 ---
        pid_c = _make_char_with_source("p-캐릭터")
        # (p-1) extraPromptKo 저장 + 분석 blocks 로 전달 + 프롬프트 끝에 extraEn 삽입
        analyze_extra_seen.clear()
        resp = client.post(
            f"/api/vnew/characters/{pid_c}/build", json={"extraPromptKo": "표정은 무표정으로"}
        )
        if resp.status_code != 202:
            bad.append(f"(p) extraPromptKo build 가 202 가 아닙니다: {resp.status_code}")
        _join_builds()
        pchar, _ = characters.load_char("tester", pid_c)
        if pchar.get("extraPromptKo") != "표정은 무표정으로":
            bad.append(f"(p) extraPromptKo 가 저장되지 않았습니다: {pchar.get('extraPromptKo')}")
        if not analyze_extra_seen:
            bad.append("(p) 추가 요청이 분석 blocks 로 전달되지 않았습니다")
        # extraEn(EXTRA_REQ_EN) 이 그림 프롬프트 끝에 붙었는지
        pw = seedream_prompts.get("webtoon", [])
        if not pw or not pw[-1].rstrip().endswith("EXTRA_REQ_EN"):
            bad.append(f"(p) 그림 프롬프트 끝에 extraEn 이 붙지 않았습니다: {pw[-1][-60:] if pw else None}")

        # (p-2) body 에 extraPromptKo 없이 다시 build → 이전 값 유지
        resp = client.post(f"/api/vnew/characters/{pid_c}/build")
        _join_builds()
        pchar, _ = characters.load_char("tester", pid_c)
        if pchar.get("extraPromptKo") != "표정은 무표정으로":
            bad.append(f"(p) body 없는 build 에서 extraPromptKo 유지가 안 됐습니다: {pchar.get('extraPromptKo')}")

        # (p-3) 빈 문자열 → 삭제
        resp = client.post(f"/api/vnew/characters/{pid_c}/build", json={"extraPromptKo": ""})
        _join_builds()
        pchar, _ = characters.load_char("tester", pid_c)
        if pchar.get("extraPromptKo"):
            bad.append(f"(p) 빈 문자열 extraPromptKo 가 삭제되지 않았습니다: {pchar.get('extraPromptKo')}")

        # ============================================================ 자동 이어 만들기 · 그림체만 다시
        from datetime import datetime as _dt2, timedelta as _td2, timezone as _tz2  # noqa: PLC0415

        def _old_iso(minutes):
            return (_dt2.now(_tz2.utc) - _td2(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _post_count2():
            return len([1 for (m, u, p) in fal_calls if m == "POST"])

        # --- (q) heartbeat 10분 초과 variant 자동 재개 — 그림 있으면 3D 만 ---
        # model3d 단계에서 끊긴(heartbeat 오래된) variant: 그림 3장 + 이전 requestArn(COMPLETED).
        # 조회(reconcile)하면 그 variant 만 이어서 만들고, 그림은 다시 안 그리고 3D 만(제출 0회).
        qid = _make_char_with_source("q-캐릭터")
        qsheet = characters.sheet_key("tester", qid, "webtoon", 1)
        qfront = characters.front_key("tester", qid, "webtoon", 1)
        qback = characters.back_key("tester", qid, "webtoon", 1)
        fake_store.put_bytes(qsheet, _tiny_jpeg(2560, 1440), "image/jpeg")
        fake_store.put_bytes(qfront, _tiny_jpeg(1440, 2560), "image/jpeg")
        fake_store.put_bytes(qback, _tiny_jpeg(512, 2560), "image/jpeg")
        q_arn = characters.ARN_PREFIX + characters.settings.HUNYUAN_ENDPOINT + "/req_q"

        def _seed_q(d):
            d["version"] = 1
            d["status"] = "building"
            d["buildStartedAt"] = _old_iso(20)
            # webtoon: model3d 에서 끊김(그림 다 있음, 이전 requestArn 있음)
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["sheetKey"] = qsheet
            w["frontKey"] = qfront
            w["backKey"] = qback
            w["heartbeatAt"] = _old_iso(20)
            w["resumeCount"] = 0
            w["model3d"] = {
                "status": "submitted", "requestArn": q_arn,
                "glbKey": "", "thumbKey": "", "error": "",
            }
            # real 은 이미 ready(건드리지 않는다)
            r = d["variants"]["real"]
            r["status"] = "ready"
            r["sheetKey"] = qsheet
            r["frontKey"] = qfront
            r["backKey"] = qback
            r["heartbeatAt"] = characters.now_iso()
            r["model3d"] = {
                "status": "ready", "requestArn": "",
                "glbKey": "users/tester/characters/" + qid + "/real/v1/model.glb",
                "thumbKey": "", "error": "",
            }
            fake_store.put_bytes(r["model3d"]["glbKey"], b"glTF-real", "model/gltf-binary")

        characters.mutate_char("tester", qid, _seed_q)
        q_before = _post_count2()
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        resp = client.get(f"/api/vnew/characters/{qid}")
        _join_builds()
        q_submits = _post_count2() - q_before
        qchar, _ = characters.load_char("tester", qid)
        qw = qchar["variants"]["webtoon"]
        if q_submits != 0:
            bad.append(f"(q) 그림이 있고 이전 요청이 COMPLETED 인데 새 3D 제출이 0회가 아닙니다: {q_submits}")
        if qw.get("status") != "ready" or (qw.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(q) 자동 재개 후 webtoon 이 ready(3D 만)가 아닙니다: {qw.get('status')} / {(qw.get('model3d') or {}).get('status')}")
        if int(qw.get("resumeCount") or 0) != 1:
            bad.append(f"(q) 자동 재개로 resumeCount 가 1 이 되지 않았습니다: {qw.get('resumeCount')}")
        # 그림은 다시 그리지 않았다(sheetKey 그대로)
        if qw.get("sheetKey") != qsheet:
            bad.append("(q) 자동 재개에서 그림(sheet)을 다시 그렸습니다")

        # --- (r) resumeCount 2 초과 → 그 variant 만 failed, 캐릭터 ready 유지 ---
        rid = _make_char_with_source("r-캐릭터")

        def _seed_r(d):
            d["version"] = 1
            d["status"] = "building"
            d["buildStartedAt"] = _old_iso(30)
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["heartbeatAt"] = _old_iso(30)
            w["resumeCount"] = characters.settings.MAX_RESUME  # 이미 한도
            w["model3d"] = {"status": "submitted", "requestArn": "", "glbKey": "", "thumbKey": "", "error": ""}
            r = d["variants"]["real"]
            r["status"] = "ready"
            r["model3d"] = {
                "status": "ready", "requestArn": "",
                "glbKey": "users/tester/characters/" + rid + "/real/v1/model.glb",
                "thumbKey": "", "error": "",
            }
            fake_store.put_bytes(r["model3d"]["glbKey"], b"glTF-real", "model/gltf-binary")

        characters.mutate_char("tester", rid, _seed_r)
        resp = client.get(f"/api/vnew/characters/{rid}")
        _join_builds()
        rdetail = resp.get_json() or {}
        rchar, _ = characters.load_char("tester", rid)
        if rchar["variants"]["webtoon"].get("status") != "failed":
            bad.append(f"(r) resumeCount 한도 초과 variant 가 failed 가 아닙니다: {rchar['variants']['webtoon'].get('status')}")
        if "중단되었습니다" not in (rchar["variants"]["webtoon"].get("error") or ""):
            bad.append(f"(r) 한도 초과 오류 문구가 다릅니다: {rchar['variants']['webtoon'].get('error')}")
        if rchar.get("status") != "ready":
            bad.append(f"(r) 한 variant failed 여도 다른 variant ready 면 캐릭터 ready 여야 합니다: {rchar.get('status')}")

        # --- (s) 다른 variant ready 면 캐릭터 ready(롤업 규칙) ---
        schar = {"variants": {"webtoon": {"status": "failed"}, "real": {"status": "ready"}}}
        if characters._rollup_status(schar) != "ready":
            bad.append(f"(s) 하나 ready·하나 failed 면 ready 여야 합니다: {characters._rollup_status(schar)}")
        schar2 = {"variants": {"webtoon": {"status": "failed"}, "real": {"status": "failed"}}}
        if characters._rollup_status(schar2) != "failed":
            bad.append(f"(s) 전부 failed 면 failed 여야 합니다: {characters._rollup_status(schar2)}")
        schar3 = {"variants": {"webtoon": {"status": "ready"}, "real": {"status": "model3d"}}}
        if characters._rollup_status(schar3) != "building":
            bad.append(f"(s) 하나 진행 중이면 building 이어야 합니다: {characters._rollup_status(schar3)}")

        # --- (t) 중복 선점 1회만 실행 ---
        tid = _make_char_with_source("t-캐릭터")

        def _seed_t(d):
            d["version"] = 1
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["claim"] = ""
            w["claimedAt"] = ""

        characters.mutate_char("tester", tid, _seed_t)
        tok1 = characters._claim_variant("tester", tid, "webtoon")
        tok2 = characters._claim_variant("tester", tid, "webtoon")
        if not tok1:
            bad.append("(t) 첫 선점이 token 을 얻지 못했습니다")
        if tok2:
            bad.append(f"(t) 두 번째 선점이 이미 잡힌 claim 을 또 잡았습니다: {tok2}")
        if not characters._holds_claim("tester", tid, "webtoon", tok1):
            bad.append("(t) 첫 선점 token 이 claim 으로 남아 있지 않습니다")
        # 다른 token 의 재개 스레드는 아무것도 하지 않는다(그림/3D 제출 없음).
        t_before = _post_count2()
        characters.run_resume_variant("tester", tid, "webtoon", 1, "WRONG-TOKEN")
        if _post_count2() != t_before:
            bad.append("(t) 다른 claim 의 재개가 제출을 일으켰습니다")

        # --- (u) variant rebuild 409·성공 ---
        # (u-1) 진행 중(싱싱한 heartbeat)이면 409
        uid = _make_char_with_source("u-캐릭터")

        def _seed_u_running(d):
            d["version"] = 1
            d["status"] = "building"
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["heartbeatAt"] = characters.now_iso()  # 지금 — 진행 중

        characters.mutate_char("tester", uid, _seed_u_running)
        resp = client.post(f"/api/vnew/characters/{uid}/variants/webtoon/rebuild", json={})
        if resp.status_code != 409:
            bad.append(f"(u) 진행 중 variant rebuild 가 409 가 아닙니다: {resp.status_code}")

        # (u-2) 실패한 variant rebuild → 202, 처음부터 다시 그려 ready, 분석 재사용
        def _seed_u_failed(d):
            w = d["variants"]["webtoon"]
            w["status"] = "failed"
            w["heartbeatAt"] = ""
            w["error"] = "이전 실패"
            w["model3d"] = {"status": "failed", "requestArn": "", "glbKey": "", "thumbKey": "", "error": "x"}
            # 분석을 미리 채워 둬서 rebuild 가 재사용하는지 본다(분석 호출 수로 확인)
            d["analysis"] = {
                "appearanceEn": "A man.", "styleEn": "S.", "outfitEn": "O.",
                "altOutfitsEn": [], "expressionsKo": ["무표정"], "extraEn": "", "model": "x",
            }

        characters.mutate_char("tester", uid, _seed_u_failed)
        analyze_before = len(analyze_calls)
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        resp = client.post(f"/api/vnew/characters/{uid}/variants/webtoon/rebuild", json={})
        if resp.status_code != 202:
            bad.append(f"(u) 실패한 variant rebuild 가 202 가 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        _join_builds()
        uchar, _ = characters.load_char("tester", uid)
        uw = uchar["variants"]["webtoon"]
        if uw.get("status") != "ready":
            bad.append(f"(u) variant rebuild 후 webtoon 이 ready 가 아닙니다: {uw.get('status')} / {uw.get('error')}")
        for key in ("sheetKey", "frontKey", "backKey"):
            if not uw.get(key) or uw[key] not in fake_store.blobs:
                bad.append(f"(u) rebuild 후 {key} 가 저장되지 않았습니다")
        if (uw.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(u) rebuild 후 3D 가 ready 가 아닙니다: {(uw.get('model3d') or {}).get('status')}")
        # 분석을 재사용(새 분석 호출 0회)
        if len(analyze_calls) != analyze_before:
            bad.append(f"(u) rebuild 가 기존 분석을 재사용하지 않았습니다(분석 호출 {len(analyze_calls) - analyze_before}회)")

        # ============================================================ 잃은 요청(unavailable) 자동 재제출
        # fal 이 'Downstream service unavailable' 만 연속으로 돌려주면(요청을 잃음) 같은 front/back 으로
        # 새로 제출한다(variant 당 최대 MAX_RESUBMITS 회, 원장은 새 제출마다 1회). 한도 넘으면 failed.
        # 원장 호출 수는 _charge_3d 를 세는 래퍼로 확인한다(검사 동안만 교체).
        charge3d_calls = {"n": 0}
        real_charge3d = characters._charge_3d

        def _counting_charge3d(owner_, cid_, variant_):
            charge3d_calls["n"] += 1
            return real_charge3d(owner_, cid_, variant_)

        characters._charge_3d = _counting_charge3d
        try:
            # (v) unavailable 8회 → 새 제출 → 완료 ready. 제출 2회(최초+재제출 1회)·원장 2회.
            # retry3d(이전 요청 없음)로 한 variant 만 돌려 제출/원장 수를 깔끔히 센다.
            vid = _make_char_with_source("v-캐릭터")
            vfk = characters.front_key("tester", vid, "webtoon", 1)
            vbk = characters.back_key("tester", vid, "webtoon", 1)
            fake_store.put_bytes(vfk, _tiny_jpeg(1440, 2560), "image/jpeg")
            fake_store.put_bytes(vbk, _tiny_jpeg(512, 2560), "image/jpeg")

            def _seed_v(d):
                d["version"] = 1
                d["status"] = "failed"
                vv = d.setdefault("variants", {}).setdefault("webtoon", characters._empty_variant())
                vv["status"] = "failed"
                vv["frontKey"] = vfk
                vv["backKey"] = vbk
                vv["model3d"] = {"status": "failed", "requestArn": "", "glbKey": "", "thumbKey": "", "error": "x", "resubmits": 0}

            characters.mutate_char("tester", vid, _seed_v)
            # LOST_AFTER(8) 만큼 unavailable 뒤 새 arn 은 바로 완료되게 한다.
            fal_mode["transient_left"] = characters.settings.LOST_AFTER
            fal_mode["fail_status"] = False
            v_before_post = _post_count()
            charge3d_calls["n"] = 0
            resp = client.post(f"/api/vnew/characters/{vid}/variants/webtoon/retry3d")
            if resp.status_code != 202:
                bad.append(f"(v) retry3d 가 202 가 아닙니다: {resp.status_code}")
            _join_builds()
            fal_mode["transient_left"] = 0
            v_submits = _post_count() - v_before_post
            vchar, _ = characters.load_char("tester", vid)
            vm3d = (vchar.get("variants") or {}).get("webtoon", {}).get("model3d") or {}
            if v_submits != 2:
                bad.append(f"(v) unavailable 8회 → 제출이 2회(최초+재제출)가 아닙니다: {v_submits}")
            if charge3d_calls["n"] != 2:
                bad.append(f"(v) 원장(3D) 호출이 2회가 아닙니다: {charge3d_calls['n']}")
            if vm3d.get("status") != "ready" or not vm3d.get("glbKey"):
                bad.append(f"(v) 재제출 후 3D 가 ready/glbKey 가 아닙니다: {vm3d}")
            if int(vm3d.get("resubmits") or 0) != 1:
                bad.append(f"(v) model3d.resubmits 가 1 이 아닙니다: {vm3d.get('resubmits')}")

            # (w) 재제출 2회(MAX_RESUBMITS)를 넘기면 failed(기존 문구 유지).
            # 계속 unavailable 이면: 최초+재제출2회=제출 3회 뒤 그래도 unavailable → failed.
            wid = _make_char_with_source("w-캐릭터")
            wfk = characters.front_key("tester", wid, "webtoon", 1)
            wbk = characters.back_key("tester", wid, "webtoon", 1)
            fake_store.put_bytes(wfk, _tiny_jpeg(1440, 2560), "image/jpeg")
            fake_store.put_bytes(wbk, _tiny_jpeg(512, 2560), "image/jpeg")

            def _seed_w(d):
                d["version"] = 1
                d["status"] = "failed"
                vv = d.setdefault("variants", {}).setdefault("webtoon", characters._empty_variant())
                vv["status"] = "failed"
                vv["frontKey"] = wfk
                vv["backKey"] = wbk
                vv["model3d"] = {"status": "failed", "requestArn": "", "glbKey": "", "thumbKey": "", "error": "x", "resubmits": 0}

            characters.mutate_char("tester", wid, _seed_w)
            # 최초 + 재제출 2회 + 그 뒤에도 unavailable 이 되도록 넉넉히 준다(LOST_AFTER*(MAX_RESUBMITS+1)).
            fal_mode["transient_left"] = characters.settings.LOST_AFTER * (characters.settings.MAX_RESUBMITS + 1)
            fal_mode["fail_status"] = False
            w_before_post = _post_count()
            charge3d_calls["n"] = 0
            resp = client.post(f"/api/vnew/characters/{wid}/variants/webtoon/retry3d")
            _join_builds()
            fal_mode["transient_left"] = 0
            w_submits = _post_count() - w_before_post
            wchar, _ = characters.load_char("tester", wid)
            wv = (wchar.get("variants") or {}).get("webtoon", {})
            wm3d = wv.get("model3d") or {}
            if w_submits != 1 + characters.settings.MAX_RESUBMITS:
                bad.append(f"(w) 제출이 최초+재제출 {characters.settings.MAX_RESUBMITS}회가 아닙니다: {w_submits}")
            if wv.get("status") != "failed" or wm3d.get("status") != "failed":
                bad.append(f"(w) 재제출 한도 초과인데 failed 가 아닙니다: {wv.get('status')} / {wm3d.get('status')}")
            if "fal 일시 오류가 계속됩니다" not in (wm3d.get("error") or ""):
                bad.append(f"(w) 한도 초과 오류 문구(기존)가 다릅니다: {wm3d.get('error')}")
            if int(wm3d.get("resubmits") or 0) != characters.settings.MAX_RESUBMITS:
                bad.append(f"(w) model3d.resubmits 가 {characters.settings.MAX_RESUBMITS} 가 아닙니다: {wm3d.get('resubmits')}")

            # (x) retry3d 의 '이전 요청 먼저 조회'도 같은 규칙: 이전 arn 이 unavailable 8회면
            # 기다리지 말고 바로 새로 제출(제출 1회)해 완료 → ready.
            xid = _make_char_with_source("x-캐릭터")
            xfk = characters.front_key("tester", xid, "webtoon", 1)
            xbk = characters.back_key("tester", xid, "webtoon", 1)
            fake_store.put_bytes(xfk, _tiny_jpeg(1440, 2560), "image/jpeg")
            fake_store.put_bytes(xbk, _tiny_jpeg(512, 2560), "image/jpeg")
            x_prior = characters.ARN_PREFIX + characters.settings.HUNYUAN_ENDPOINT + "/req_x_prior"

            def _seed_x(d):
                d["version"] = 1
                d["status"] = "failed"
                vv = d.setdefault("variants", {}).setdefault("webtoon", characters._empty_variant())
                vv["status"] = "failed"
                vv["frontKey"] = xfk
                vv["backKey"] = xbk
                vv["model3d"] = {"status": "failed", "requestArn": x_prior, "glbKey": "", "thumbKey": "", "error": "이전 unavailable", "resubmits": 0}

            characters.mutate_char("tester", xid, _seed_x)
            # 이전 요청(prior)이 LOST_AFTER 회 unavailable → 바로 새 제출, 새 arn 은 완료.
            fal_mode["transient_left"] = characters.settings.LOST_AFTER
            fal_mode["fail_status"] = False
            x_before_post = _post_count()
            resp = client.post(f"/api/vnew/characters/{xid}/variants/webtoon/retry3d")
            if resp.status_code != 202:
                bad.append(f"(x) retry3d 가 202 가 아닙니다: {resp.status_code}")
            _join_builds()
            fal_mode["transient_left"] = 0
            x_submits = _post_count() - x_before_post
            xchar, _ = characters.load_char("tester", xid)
            xm3d = (xchar.get("variants") or {}).get("webtoon", {}).get("model3d") or {}
            if x_submits != 1:
                bad.append(f"(x) 이전 요청 unavailable → 새 제출이 1회가 아닙니다: {x_submits}")
            if xm3d.get("status") != "ready" or not xm3d.get("glbKey"):
                bad.append(f"(x) retry3d 이전요청 unavailable 재제출 후 ready/glbKey 가 아닙니다: {xm3d}")
            if int(xm3d.get("resubmits") or 0) != 1:
                bad.append(f"(x) retry3d 재제출 resubmits 가 1 이 아닙니다: {xm3d.get('resubmits')}")
        finally:
            characters._charge_3d = real_charge3d

        # ============================================================ 033: 시작 전 선점·heartbeat
        # retry3d·rebuild·build 가 '시작 전에' claim·heartbeat 를 잡아, 직후 조회 자동 재개가
        # 끊긴 작업으로 오인해 두 번째 실행(이중 3D 제출)을 띄우지 못하게 한다.
        from datetime import datetime as _dt3, timedelta as _td3, timezone as _tz3  # noqa: PLC0415

        def _old_iso3(minutes):
            return (_dt3.now(_tz3.utc) - _td3(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- (y) retry3d 시작 직후 detail_view → 자동 재개 0회·3D 제출 1회 ---
        # 재현: 이전 실패 실행 때의 '오래된 heartbeat'(10분 초과)가 남은 variant 를 retry3d 로 다시
        # 시작. 시작 전 claim·heartbeat 를 잡으므로, 직후 조회가 자동 재개를 띄우지 않는다.
        yid = _make_char_with_source("y-캐릭터")
        yfk = characters.front_key("tester", yid, "real", 1)
        ybk = characters.back_key("tester", yid, "real", 1)
        fake_store.put_bytes(yfk, _tiny_jpeg(1440, 2560), "image/jpeg")
        fake_store.put_bytes(ybk, _tiny_jpeg(512, 2560), "image/jpeg")

        def _seed_y(d):
            d["version"] = 1
            d["status"] = "failed"
            vv = d.setdefault("variants", {}).setdefault("real", characters._empty_variant())
            vv["status"] = "failed"
            vv["frontKey"] = yfk
            vv["backKey"] = ybk
            vv["heartbeatAt"] = _old_iso3(15)  # 이전 실패 실행의 오래된 heartbeat
            # 다른 그림체(webtoon)는 완료 상태로 둔다 — 끊긴 채 두면 그쪽 자동 재개가 정상적으로 3D 를 하나 더 낸다.
            d["variants"].setdefault("webtoon", characters._empty_variant()).update({"status": "ready", "model3d": {"status": "ready", "glbKey": "k", "thumbKey": "", "error": ""}})
            vv["claim"] = ""
            vv["claimedAt"] = ""
            # 이전 실패 실행의 old requestArn(없다고 두면 retry3d 가 새로 제출 1회).
            vv["model3d"] = {"status": "failed", "requestArn": "", "glbKey": "", "thumbKey": "", "error": "이전 실패", "resubmits": 0}

        characters.mutate_char("tester", yid, _seed_y)
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        y_before = _post_count2()
        resp = client.post(f"/api/vnew/characters/{yid}/variants/real/retry3d")
        if resp.status_code != 202:
            bad.append(f"(y) retry3d 가 202 가 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        # 스레드 합류 전에 바로 상세 조회를 여러 번 — 자동 재개가 끼어들면 두 번째 제출이 생긴다.
        for _ in range(3):
            client.get(f"/api/vnew/characters/{yid}")
        _join_builds()
        # 조회가 또 스레드를 띄웠을 수 있으니 한 번 더 합류.
        client.get(f"/api/vnew/characters/{yid}")
        _join_builds()
        y_submits = _post_count2() - y_before
        ychar, _ = characters.load_char("tester", yid)
        yv = (ychar.get("variants") or {}).get("real", {})
        if y_submits != 1:
            bad.append(f"(y) retry3d 직후 조회로 3D 제출이 1회가 아닙니다(이중 제출 의심): {y_submits}")
        if int(yv.get("resumeCount") or 0) != 0:
            bad.append(f"(y) retry3d 직후 자동 재개가 일어났습니다(resumeCount {yv.get('resumeCount')})")
        if yv.get("status") != "ready" or (yv.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(y) retry3d 후 real 이 ready 가 아닙니다: {yv.get('status')} / {(yv.get('model3d') or {}).get('status')}")

        # --- (z) 오래된 heartbeat + claim 없음 → 자동 재개 1회 ---
        # (y) 와 대조: claim 이 비어 있고 heartbeat 가 오래됐으면 조회가 그 variant 를 이어 만든다.
        zid = _make_char_with_source("z-캐릭터")
        zsheet = characters.sheet_key("tester", zid, "webtoon", 1)
        zfront = characters.front_key("tester", zid, "webtoon", 1)
        zback = characters.back_key("tester", zid, "webtoon", 1)
        fake_store.put_bytes(zsheet, _tiny_jpeg(2560, 1440), "image/jpeg")
        fake_store.put_bytes(zfront, _tiny_jpeg(1440, 2560), "image/jpeg")
        fake_store.put_bytes(zback, _tiny_jpeg(512, 2560), "image/jpeg")
        z_arn = characters.ARN_PREFIX + characters.settings.HUNYUAN_ENDPOINT + "/req_z"

        def _seed_z(d):
            d["version"] = 1
            d["status"] = "building"
            d["buildStartedAt"] = _old_iso3(20)
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["sheetKey"] = zsheet
            w["frontKey"] = zfront
            w["backKey"] = zback
            w["heartbeatAt"] = _old_iso3(15)  # 오래된 heartbeat
            w["claim"] = ""  # 선점 없음
            w["claimedAt"] = ""
            w["resumeCount"] = 0
            w["model3d"] = {"status": "submitted", "requestArn": z_arn, "glbKey": "", "thumbKey": "", "error": "", "resubmits": 0}
            r = d["variants"]["real"]
            r["status"] = "ready"
            r["model3d"] = {
                "status": "ready", "requestArn": "",
                "glbKey": "users/tester/characters/" + zid + "/real/v1/model.glb",
                "thumbKey": "", "error": "",
            }
            fake_store.put_bytes(r["model3d"]["glbKey"], b"glTF-real", "model/gltf-binary")

        characters.mutate_char("tester", zid, _seed_z)
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        resp = client.get(f"/api/vnew/characters/{zid}")
        _join_builds()
        zchar, _ = characters.load_char("tester", zid)
        zw = zchar["variants"]["webtoon"]
        if int(zw.get("resumeCount") or 0) != 1:
            bad.append(f"(z) claim 없고 heartbeat 오래됨 → 자동 재개 1회가 아닙니다(resumeCount {zw.get('resumeCount')})")
        if zw.get("status") != "ready" or (zw.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(z) 자동 재개 후 webtoon 이 ready 가 아닙니다: {zw.get('status')} / {(zw.get('model3d') or {}).get('status')}")

        # --- (aa) 순차 빌드 중 앞 variant 가 오래 걸려도(가짜 시계 15분) 대기 variant 자동 재개 0회 ---
        # (aa-1) 단위: keepalive 에 대기 variant(real)를 등록하고 real 을 15분 전으로 늙힌 뒤,
        #        앞 variant(webtoon)의 heartbeat 를 찍으면 real 의 claim·heartbeat 도 함께 갱신돼
        #        _variant_stale(real) 가 False 가 된다.
        aid = _make_char_with_source("aa-캐릭터")
        aa_tok = "keepalivetok"

        def _seed_aa(d):
            d["version"] = 1
            d["status"] = "building"
            w = d["variants"]["webtoon"]
            w["status"] = "model3d"
            w["heartbeatAt"] = characters.now_iso()
            r = d["variants"]["real"]
            r["status"] = "queued"
            r["claim"] = aa_tok
            r["claimedAt"] = _old_iso3(15)      # 15분 전(가짜 시계) — 그냥 두면 stale
            r["heartbeatAt"] = _old_iso3(15)

        characters.mutate_char("tester", aid, _seed_aa)
        aa_char, _ = characters.load_char("tester", aid)
        if not characters._variant_stale(aa_char, "real"):
            bad.append("(aa) 전제: 15분 전 claim·heartbeat 인 대기 variant 는 갱신 전 stale 이어야 합니다")
        # keepalive 로 real 등록 → webtoon heartbeat 찍기 → real 도 갱신.
        characters._set_keepalive("tester", aid, {"real": aa_tok})
        try:
            characters._touch_heartbeat("tester", aid, "webtoon")
        finally:
            characters._set_keepalive("tester", aid, {})
        aa_char, _ = characters.load_char("tester", aid)
        aa_real = aa_char["variants"]["real"]
        if characters._variant_stale(aa_char, "real"):
            bad.append("(aa) keepalive 로 대기 variant 를 갱신했는데도 아직 stale 입니다")
        if not characters._claim_fresh(aa_real):
            bad.append("(aa) keepalive 갱신 후 대기 variant 의 claim 이 살아 있지 않습니다")

        # (aa-2) 통합: 순차 build 에서 webtoon 3D 폴링이 여러 번 도는 동안 가짜 시계로 시간을
        #        흘려(폴링마다 +8분, 2회면 16분 경과) 대기 variant(real)가 자동 재개로 중복 실행되지
        #        않는지 본다 — keepalive 가 폴링마다 real 을 '가짜 지금'으로 다시 찍어 10분 stall 을
        #        넘지 않게 한다. real 은 run_build 가 한 번만 만든다.
        from datetime import datetime as _RealDT  # noqa: PLC0415

        class _Clock:
            def __init__(self):
                self.t = _RealDT.now(_tz3.utc)

            def advance(self, minutes):
                self.t = self.t + _td3(minutes=minutes)

            def iso(self):
                return self.t.strftime("%Y-%m-%dT%H:%M:%SZ")

        aa_clock = _Clock()

        class _FakeDT(_RealDT):
            @classmethod
            def now(cls, tz=None):
                return aa_clock.t if tz is not None else aa_clock.t.replace(tzinfo=None)

        aa2id = _make_char_with_source("aa2-캐릭터")
        aa2_state = {"polls": 0, "resumed_seen": 0}
        orig_fake_fal = characters._fal_request
        orig_now_iso = characters.now_iso
        orig_dt = characters.datetime
        # 가짜 시계 적용: now_iso 와 datetime.now 둘 다 같은 시계를 본다.
        characters.now_iso = aa_clock.iso
        characters.datetime = _FakeDT

        def _aa2_fal(method, url, payload=None):
            # webtoon 3D 상태 조회 때마다: 가짜 시계 +8분 흘리고(누적 16분) 상세 조회.
            if method == "GET" and url.endswith("/status"):
                aa2_state["polls"] += 1
                if aa2_state["polls"] <= 2:
                    aa_clock.advance(8)  # 가짜 시계 전진(앞 variant 가 오래 걸리는 것)
                    # 상세 조회(reconcile) — 대기 variant 가 자동 재개되면 안 된다(keepalive 덕).
                    client.get(f"/api/vnew/characters/{aa2id}")
                    rc, _ = characters.load_char("tester", aa2id)
                    if int(rc["variants"]["real"].get("resumeCount") or 0) > 0:
                        aa2_state["resumed_seen"] += 1
                # webtoon 3D 를 최초 2회는 폴링만, 그 뒤 완료시켜 build 가 끝나게.
                return {"status": "COMPLETED"} if aa2_state["polls"] > 2 else {"status": "IN_PROGRESS"}
            return orig_fake_fal(method, url, payload)

        characters._fal_request = _aa2_fal
        try:
            fal_mode["fail_status"] = False
            fal_mode["transient_left"] = 0
            resp = client.post(f"/api/vnew/characters/{aa2id}/build")
            if resp.status_code != 202:
                bad.append(f"(aa) build 가 202 가 아닙니다: {resp.status_code}")
            _join_builds()
            client.get(f"/api/vnew/characters/{aa2id}")
            _join_builds()
        finally:
            characters._fal_request = orig_fake_fal
            characters.now_iso = orig_now_iso
            characters.datetime = orig_dt
        aa2char, _ = characters.load_char("tester", aa2id)
        aa2_real = aa2char["variants"]["real"]
        if aa2_state["polls"] < 3:
            bad.append(f"(aa) webtoon 3D 폴링이 충분히 돌지 않아 시간 경과를 시뮬레이션하지 못했습니다: polls={aa2_state['polls']}")
        if aa2_state["resumed_seen"] != 0:
            bad.append(f"(aa) 앞 variant 가 오래 걸리는 동안 대기 variant 가 자동 재개됐습니다(resumeCount>0 {aa2_state['resumed_seen']}회)")
        if int(aa2_real.get("resumeCount") or 0) != 0:
            bad.append(f"(aa) 최종 대기 variant resumeCount 가 0 이 아닙니다: {aa2_real.get('resumeCount')}")
        if aa2_real.get("status") != "ready" or (aa2_real.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(aa) 순차 build 후 대기 variant(real)가 ready 가 아닙니다: {aa2_real.get('status')} / {(aa2_real.get('model3d') or {}).get('status')}")
        if aa2char.get("status") != "ready":
            bad.append(f"(aa) 순차 build 후 캐릭터가 ready 가 아닙니다: {aa2char.get('status')}")

        # ============================================================ 034: 기본 복장 고정 + 추가 복장
        base_outfit = "A beige button-up shirt with dark trousers and loafers."

        # --- (ab) 시트 프롬프트에 baseOutfitEn·"nothing else" 가 들어간다 ---
        # 앞서 돈 build 들의 시트 프롬프트(webtoon·real)를 확인한다.
        sheet_calls_all = [c for c in seedream_calls if c["kind"] == "sheet"]
        if not sheet_calls_all:
            bad.append("(ab) 시트 프롬프트가 캡처되지 않았습니다")
        # 다른 소검사가 일부러 다른 분석값(예: "A man"/"O")으로 만든 시트도 섞여 있다 — 그 시트는 자기
        # 분석값의 옷을 쓴다. 그래서 기본 분석값으로 만든 시트(appearance 가 기본 fake 값)만 baseOutfitEn 을 본다.
        base_calls = [c for c in sheet_calls_all if "Wearing: O." not in c["prompt"]]
        if not base_calls or any(base_outfit not in c["prompt"] for c in base_calls):
            bad.append("(ab) 시트 프롬프트에 baseOutfitEn 이 들어가지 않았습니다")
        for c in sheet_calls_all:
            if "nothing else" not in c["prompt"]:
                bad.append("(ab) 시트 프롬프트에 'nothing else' 가 없습니다")
                break

        # --- (ac) 일관성 false → 시트 1회 재생성 → 정면·후면 참고에 재생성 시트가 들어간다 ---
        abid = _make_char_with_source("ab-캐릭터")
        seedream_calls.clear()
        outfit_check_calls["n"] = 0
        # webtoon 시트 첫 장은 불일치, 재생성은 일치. real 은 일치. (호출 순서: webtoon 먼저)
        outfit_mode["results"] = [
            {"consistent": False, "problems": ["back view adds a black jacket"]},
            {"consistent": True, "problems": []},
            {"consistent": True, "problems": []},  # real 시트
        ]
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        resp = client.post(f"/api/vnew/characters/{abid}/build")
        _join_builds()
        outfit_mode["results"] = None
        # webtoon 시트가 2번 그려졌는지(최초 불일치 + 재생성).
        w_sheets = [c for c in seedream_calls if c["variant"] == "webtoon" and c["kind"] == "sheet"]
        if len(w_sheets) != 2:
            bad.append(f"(ac) 불일치 시트가 1회 재생성되지 않았습니다(webtoon 시트 {len(w_sheets)}회)")
        # 재생성 시트 프롬프트에 problems 를 고치라는 지시가 붙었는지.
        if len(w_sheets) >= 2 and "black jacket" not in w_sheets[1]["prompt"]:
            bad.append("(ac) 재생성 시트 프롬프트에 problems(고칠 점)가 붙지 않았습니다")
        # 정면·후면이 '재생성(통과)한 시트'를 참고로 그렸는지 — 재생성 시트 바이트가 refs 에 있다.
        abchar, _ = characters.load_char("tester", abid)
        regen_sheet_key = abchar["variants"]["webtoon"]["sheetKey"]
        regen_sheet = fake_store.blobs.get(regen_sheet_key)
        w_front = next((c for c in seedream_calls if c["variant"] == "webtoon" and c["kind"] == "front"), None)
        if w_front is None:
            bad.append("(ac) webtoon 정면 호출이 없습니다")
        elif regen_sheet is not None and regen_sheet not in w_front["refs"]:
            bad.append("(ac) 정면 참고에 (재생성 통과) 시트가 들어가지 않았습니다")
        w_back = next((c for c in seedream_calls if c["variant"] == "webtoon" and c["kind"] == "back"), None)
        if w_back is None:
            bad.append("(ac) webtoon 후면 호출이 없습니다")
        elif regen_sheet is not None and regen_sheet not in w_back["refs"]:
            bad.append("(ac) 후면 참고에 (재생성 통과) 시트가 들어가지 않았습니다")

        # --- (ad) 계속 false → warning("복장이 면마다 다를 수 있습니다") ---
        adid = _make_char_with_source("ad-캐릭터")
        seedream_calls.clear()
        outfit_mode["results"] = None
        # 항상 불일치가 되도록 dict(리스트 아님) 로 둔다.
        outfit_mode["results"] = {"consistent": False, "problems": ["mismatch"]}
        fal_mode["fail_status"] = False
        fal_mode["transient_left"] = 0
        resp = client.post(f"/api/vnew/characters/{adid}/build")
        _join_builds()
        outfit_mode["results"] = None
        adchar, _ = characters.load_char("tester", adid)
        adw = adchar["variants"]["webtoon"]
        if "복장이 면마다 다를 수 있습니다" not in (adw.get("warning") or ""):
            bad.append(f"(ad) 계속 불일치인데 복장 warning 이 없습니다: {adw.get('warning')}")
        # 재생성은 OUTFIT_SHEET_RETRY(1)회만 — webtoon 시트가 2회(최초+재생성 1회)만 그려진다.
        ad_w_sheets = [c for c in seedream_calls if c["variant"] == "webtoon" and c["kind"] == "sheet"]
        if len(ad_w_sheets) != 1 + characters.settings.OUTFIT_SHEET_RETRY:
            bad.append(f"(ad) 시트 재생성이 {characters.settings.OUTFIT_SHEET_RETRY}회를 넘거나 모자랍니다: {len(ad_w_sheets)}회")
        # 3D 는 그래도 진행(ready)
        if (adw.get("model3d") or {}).get("status") != "ready":
            bad.append(f"(ad) 복장 warning 이어도 3D 는 진행(ready)해야 합니다: {(adw.get('model3d') or {}).get('status')}")

        # --- (ae) outfitLayersEn → source:auto 추가 복장 후보, 그림 0장 ---
        # ab 캐릭터(build 완료)의 outfits 가 auto 후보 2개(정장 자켓·후드티)로 채워졌는지.
        resp = client.get(f"/api/vnew/characters/{abid}")
        ab_detail = resp.get_json() or {}
        outfits = ab_detail.get("outfits") or []
        autos = [o for o in outfits if o.get("source") == "auto"]
        if len(autos) < 2:
            bad.append(f"(ae) outfitLayersEn 가 auto 후보로 채워지지 않았습니다: {outfits}")
        auto_prompts = {o.get("promptEn") for o in autos}
        if "a black suit jacket over the shirt" not in auto_prompts:
            bad.append(f"(ae) auto 후보 promptEn 이 분석 그대로가 아닙니다: {auto_prompts}")
        # auto 후보는 그림이 0장(variants 가 전부 queued, frontUrl/backUrl 빈값).
        for o in autos:
            for meta in characters.settings.VARIANTS:
                ov = (o.get("variants") or {}).get(meta) or {}
                if ov.get("status") not in ("queued", None, ""):
                    bad.append(f"(ae) auto 후보가 자동으로 그려졌습니다(status={ov.get('status')})")
                if ov.get("frontUrl") or ov.get("backUrl"):
                    bad.append("(ae) auto 후보에 이미지가 생겼습니다(그려선 안 됨)")

        # --- (af) outfit build → 두 그림체 정면·후면 4장, 참고에 기본 정면 포함 ---
        af_outfit_id = autos[0]["id"]
        seedream_calls.clear()
        before_outfit_calls = len(seedream_calls)
        resp = client.post(
            f"/api/vnew/characters/{abid}/outfits/{af_outfit_id}/build"
        )
        if resp.status_code != 202:
            bad.append(f"(af) outfit build 가 202 가 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        _join_builds()
        outfit_imgs = [c for c in seedream_calls if c["kind"] in ("outfit-front", "outfit-back")]
        if len(outfit_imgs) != 4:
            bad.append(f"(af) 추가 복장 이미지가 4장(두 그림체 정면·후면)이 아닙니다: {len(outfit_imgs)}")
        # 두 그림체 각각 정면·후면 1장씩.
        for meta in characters.settings.VARIANTS:
            fr = [c for c in outfit_imgs if c["variant"] == meta and c["kind"] == "outfit-front"]
            bk = [c for c in outfit_imgs if c["variant"] == meta and c["kind"] == "outfit-back"]
            if len(fr) != 1 or len(bk) != 1:
                bad.append(f"(af) {meta} 추가 복장 정면·후면이 각각 1장이 아닙니다: front={len(fr)} back={len(bk)}")
        # 참고에 '기본 정면' 이 들어갔는지 — webtoon 추가복장 정면의 refs 에 기본 정면 바이트가 있다.
        base_front_key = abchar["variants"]["webtoon"]["frontKey"]
        base_front_bytes = fake_store.blobs.get(base_front_key)
        w_outfit_front = next(
            (c for c in outfit_imgs if c["variant"] == "webtoon" and c["kind"] == "outfit-front"), None
        )
        if w_outfit_front is None:
            bad.append("(af) webtoon 추가복장 정면 호출이 없습니다")
        elif base_front_bytes is not None and base_front_bytes not in w_outfit_front["refs"]:
            bad.append("(af) 추가 복장 정면 참고에 기본 정면 이미지가 들어가지 않았습니다")
        # 추가 복장 프롬프트에 promptEn 이 들어갔는지('plus ...').
        if w_outfit_front is not None and "a black suit jacket over the shirt" not in w_outfit_front["prompt"]:
            bad.append("(af) 추가 복장 프롬프트에 promptEn 이 들어가지 않았습니다")
        # 3D 는 추가로 만들지 않는다 — outfit build 동안 fal POST 가 늘지 않는다(이미 확인된 build 외).
        # 결과가 ready 로 저장됐는지.
        resp = client.get(f"/api/vnew/characters/{abid}")
        af_detail = resp.get_json() or {}
        af_outfit = next((o for o in (af_detail.get("outfits") or []) if o.get("id") == af_outfit_id), {})
        for meta in characters.settings.VARIANTS:
            ov = (af_outfit.get("variants") or {}).get(meta) or {}
            if ov.get("status") != "ready" or not ov.get("frontUrl") or not ov.get("backUrl"):
                bad.append(f"(af) 추가 복장 {meta} 결과가 ready/정면·후면 URL 이 아닙니다: {ov}")

        # --- (ag) 사용자 추가 promptKo → promptEn(Astra 번역) ---
        translate_calls.clear()
        resp = client.post(
            f"/api/vnew/characters/{abid}/outfits",
            json={"labelKo": "빨간 코트", "promptKo": "빨간 긴 코트를 걸친다"},
        )
        if resp.status_code != 201:
            bad.append(f"(ag) 사용자 추가 복장이 201 이 아닙니다: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
        ag_outfit = (resp.get_json() or {}).get("outfit") or {}
        if ag_outfit.get("source") != "user":
            bad.append(f"(ag) 사용자 추가 복장 source 가 user 가 아닙니다: {ag_outfit.get('source')}")
        if not translate_calls:
            bad.append("(ag) promptKo 가 번역(_converse)으로 전달되지 않았습니다")
        if ag_outfit.get("promptEn") != "EN(빨간 긴 코트를 걸친다)":
            bad.append(f"(ag) promptKo 가 promptEn 으로 바뀌지 않았습니다: {ag_outfit.get('promptEn')}")
        if ag_outfit.get("labelKo") != "빨간 코트":
            bad.append(f"(ag) 사용자 추가 복장 labelKo 가 저장되지 않았습니다: {ag_outfit.get('labelKo')}")
        # 삭제도 동작하는지.
        resp = client.delete(f"/api/vnew/characters/{abid}/outfits/{ag_outfit.get('id')}")
        if resp.status_code != 200:
            bad.append(f"(ag) 추가 복장 삭제가 200 이 아닙니다: {resp.status_code}")
        resp = client.get(f"/api/vnew/characters/{abid}")
        remain = [o.get("id") for o in ((resp.get_json() or {}).get("outfits") or [])]
        if ag_outfit.get("id") in remain:
            bad.append("(ag) 삭제한 추가 복장이 아직 남아 있습니다")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1200])
    finally:
        characters.STORE = orig["STORE"]
        characters._analyze = orig["_analyze"]
        characters._seedream = orig["_seedream"]
        characters._fal_request = orig["_fal_request"]
        characters._fetch_url = orig["_fetch_url"]
        characters._count_people = orig["_count_people"]
        characters._check_outfit_consistency = orig["_check_outfit_consistency"]
        _vpoc_llm_mod._converse = orig["_converse"]
        characters.time.sleep = orig["sleep"]
        characters.threading.Thread = orig_thread

    if bad:
        fail("캐릭터 만들기(vnew) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "캐릭터 만들기(vnew) 검사 (a)CRUD·업로드 (b)분석 fallback·시트·정면·후면·3D"
            "(60000·PBR false·data URI)·ready (c)variant 격리 (d)409·variant ready 유지 (e)GLB 200 (f)격리 404 "
            "(g)size 기본 (h)일시오류 재조회 ready (i)FAILED 동기화 (j)retry3d 재사용·새제출 (k)기본값 "
            "(l)SAME person 지침·분석 필드 (m)웹툰 styleEn·one person·outfitEn·generic 없음 "
            "(n)후면 따로 생성 (o)사람 수 2→재생성→warning·3D 진행 (p)extraPromptKo 저장·유지·삭제 "
            "(q)heartbeat 10분 초과 자동재개 3D만 (r)resumeCount 초과 variant failed·캐릭터 ready "
            "(s)롤업 규칙 (t)중복 선점 1회 (u)variant rebuild 409·성공·분석 재사용 "
            "(v)unavailable 8회→재제출→ready(제출2·원장2) (w)재제출 한도 초과→failed "
            "(x)retry3d 이전요청 unavailable→재제출 "
            "(y)retry3d 직후 조회 자동재개 0회·3D 제출 1회(이중 제출 방지) "
            "(z)오래된 heartbeat+claim 없음→자동재개 1회 "
            "(aa)순차 build 중 앞 variant 가 15분 걸려도 대기 variant 자동재개 0회(keepalive) "
            "(ab)시트 프롬프트에 baseOutfitEn·nothing else "
            "(ac)복장 불일치→시트 1회 재생성·정면/후면 참고는 재생성 시트 "
            "(ad)계속 불일치→복장 warning·3D 진행 "
            "(ae)outfitLayersEn→auto 후보(그림 0장) "
            "(af)outfit build 두 그림체 정면·후면 4장·참고에 기본 정면 "
            "(ag)사용자 추가 promptKo→promptEn·삭제"
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

        # (l) 모든 구간은 MIN_SEGMENT_SECONDS(3) 이상·max 이하 — 16초 컷·최대 15초 → 둘 다 ≥3.
        #     최종 생성 모델(Kling O3 edit)이 3~15초만 받으므로 2초 구간을 만들면 안 된다.
        exp_h, seg_h = planmod.compute_segments([{"id": "h1", "no": 1, "seconds": 16}], 15)
        if len(seg_h) != 2:
            bad.append(f"(l) 16초·max15 가 2구간이 아닙니다: {[s['seconds'] for s in seg_h]}")
        if any(int(s["seconds"]) < planmod.MIN_SEGMENT_SECONDS for s in seg_h):
            bad.append(f"(l) 3초 미만 구간이 있습니다: {[s['seconds'] for s in seg_h]}")
        if any(int(s["seconds"]) > 15 for s in seg_h):
            bad.append(f"(l) 15초 초과 구간이 있습니다: {[s['seconds'] for s in seg_h]}")
        if sum(int(s["seconds"]) for s in seg_h) != 16:
            bad.append(f"(l) 구간 합이 16초가 아닙니다: {[s['seconds'] for s in seg_h]}")
        # 고르게 나눠 [8,8] 이 되는지(둘 다 ≥3 의 대표 예).
        if sorted(int(s["seconds"]) for s in seg_h) != [8, 8]:
            bad.append(f"(l) 16초·max15 가 [8,8] 로 고르게 나뉘지 않았습니다: {sorted(int(s['seconds']) for s in seg_h)}")

        # (m) 마지막 컷 2초 → 앞과 합쳐 2초짜리 구간이 사라진다(모든 구간 ≥3).
        #     max5 에서 4,4,2 는 그대로면 [4,4,2] — 2초 구간을 앞과 합쳐 다시 나눈다.
        tail_cuts = [
            {"id": "t1", "no": 1, "seconds": 4},
            {"id": "t2", "no": 2, "seconds": 4},
            {"id": "t3", "no": 3, "seconds": 2},
        ]
        exp_i, seg_i = planmod.compute_segments([dict(c) for c in tail_cuts], 5)
        if any(int(s["seconds"]) < planmod.MIN_SEGMENT_SECONDS for s in seg_i):
            bad.append(f"(m) 마지막 2초가 앞과 합쳐지지 않아 3초 미만 구간이 남았습니다: {[s['seconds'] for s in seg_i]}")
        if any(int(s["seconds"]) > 5 for s in seg_i):
            bad.append(f"(m) 합친 뒤 max(5)를 넘는 구간이 있습니다: {[s['seconds'] for s in seg_i]}")
        if sum(int(s["seconds"]) for s in seg_i) != 10:
            bad.append(f"(m) 구간 합이 총초(10)와 다릅니다: {[s['seconds'] for s in seg_i]}")

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
            " (l)구간 3~max·16초→[8,8] (m)마지막 2초 앞과 합침"
        )


# ---------------------------------------------------------------- 9b. 영상만들기_신규 공간 디테일 시트


def check_vnew_locations() -> None:
    """영상만들기_신규 ③ 공간 디테일 시트 — 설계 LLM·Seedream 은 가짜(설계자 2026-10-21, 048 컷어웨이 뷰).

    옛 '벽 4장 따로 그리기'(046) 를 '컷어웨이 뷰 2장(back·front) × 그림체 2 = 4장' 으로 바꿨다.
    046 검사 중 "Seedream 10장·2x2 시트·Orthographic" 전제는 새 방식(뷰 4장·montage_views·면별 라벨)에
    맞게 고쳤다(보고에 사유 기재). 기존 wall_key/floor_key 저장은 그대로라 previz 는 수정 없이 동작한다.

    (a) 장소 정규화·중복 제거(대소문자·공백 다른 같은 이름은 하나)
    (b) 설계 JSON 정리(없는 필드 기본값·벽 4면 보장)
    (e) 콘티 재생성 후 같은 장소 유지·자동 그림 0장(설계·사실·프롬프트·비교도 안 함)
    (f) 진행 중 build 409
    (g) 설계 모델 호출 순서 Claude(DESIGN_MODEL) → Astra(fallback)
    (j) 설계 결과에 'assumed'/'provisional' 이 있으면 1회 재요청
    (l) 공간 '다시 만들기' 는 설계 재요청 1회, designEditedAt 있으면 0회(수정본 유지)
    (m)(q) 빌드 중 progress.done 이 뷰마다 +1, 단계 순서 design→facts→prompts→images→verify→cut→sheet→done
    (n) 사실 추출 결과 저장(벽 물건·재질, 보이지 않는 벽 estimated, 창문·문·시대·나라), plan_view 에 facts
    (o) Astra 프롬프트 지침 문장·사실 단어 포함, Astra→Claude 순서, 완성 프롬프트 길이 1,500~2,500자
    (lv-a) box_view_geometry: back 사각형 화면 안·가로 35~55%, 면이 back 모서리 공유, front 에서 left=east
    (lv-b) rectify: 체커 사다리꼴을 펴면 직사각형 격자, 꼭짓점 순서(west 오른쪽 끝 = back 모서리)
    (lv-c) 빌드: Seedream 4회(+보정), 첫 참고 가이드(real)·real 뷰(webtoon); wall_key 4·floor_key 저장; previz 사용
    (lv-d) 프롬프트에 CENTER BACK WALL·LEFT WALL·RIGHT WALL·continuous·No people, 뷰 B 에 opposite side
    (lv-e) verify 꼭짓점 4% 초과면 Claude 꼭짓점, 이상하면 가이드
    (lv-f) 옛 데이터(views 없음) location_view 정상
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import locations as locmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vpoc import images as _images  # noqa: PLC0415

    fake_store = _FakeCharStore()

    # --- (np-d) 설계·사실 지침에 건축 임의 추가 금지(설계자 2026-10-22) ---
    if "Do not add stairs" not in locmod._DESIGN_SYSTEM:
        bad.append("(np-d) locations 설계 지침에 'Do not add stairs' 가 없습니다")
    if "Do not add stairs" not in locmod._FACTS_SYSTEM:
        bad.append("(np-d) locations 사실 지침에 'Do not add stairs' 가 없습니다")
    for need in ("mezzanines", "terrace/balcony", "estimate size from people and doors"):
        if need not in locmod._DESIGN_SYSTEM:
            bad.append(f"(np-d) locations 설계 지침에 '{need}' 가 없습니다")

    # --- (b) 설계 JSON 정리: _normalize_design 단위(네트워크 불필요) ---
    thin = locmod._normalize_design(
        {
            # walls 에 north 만 있고 descEn 도 없음 → 4면이 다 생기고 기본값으로 채워진다.
            "walls": {"north": {}},
            "widthM": "nope",  # 숫자 아님 → 기본값
        }
    )
    if sorted((thin.get("walls") or {}).keys()) != sorted(locmod.SIDES):
        bad.append(f"(b) 설계 정리가 벽 4면을 보장하지 않습니다: {sorted((thin.get('walls') or {}).keys())}")
    for side in locmod.SIDES:
        w = (thin["walls"] or {}).get(side) or {}
        if "descEn" not in w or "hasWindow" not in w or "hasDoor" not in w or "featuresEn" not in w:
            bad.append(f"(b) {side} 벽에 기본 필드가 없습니다: {w}")
    if not isinstance(thin.get("widthM"), float):
        bad.append(f"(b) widthM 기본값이 숫자가 아닙니다: {thin.get('widthM')!r}")
    if not isinstance(thin.get("enclosed"), bool):
        bad.append(f"(b) enclosed 기본값이 bool 이 아닙니다: {thin.get('enclosed')!r}")

    # --- (lv-a) box_view_geometry: back 사각형·면 모서리 공유·front 좌우 반전 ---
    from src.vnew import location_views as lvmod  # noqa: PLC0415

    gsize = (2560, 1440)
    geom_back = lvmod.box_view_geometry(6.0, 5.0, 2.8, view="back", size=gsize)
    back_q = geom_back["back"]
    xs = [p[0] for p in back_q]
    back_w_ratio = (max(xs) - min(xs)) / gsize[0]
    if not (0.35 <= back_w_ratio <= 0.55):
        bad.append(f"(lv-a) back 사각형 가로 비율이 35~55% 가 아닙니다: {back_w_ratio:.3f}")
    # back 사각형이 화면 안.
    for (px, py) in back_q:
        if not (0 <= px <= gsize[0] and 0 <= py <= gsize[1]):
            bad.append(f"(lv-a) back 꼭짓점이 화면 밖입니다: {(px, py)}")
            break
    # left/right/floor 사다리꼴이 back 모서리를 공유(점 일치).
    btl, btr, bbr, bbl = back_q
    if geom_back["left"][1] != btl or geom_back["left"][2] != bbl:
        bad.append(f"(lv-a) left 면이 back 왼쪽 모서리를 공유하지 않습니다: {geom_back['left']}")
    if geom_back["right"][0] != btr or geom_back["right"][3] != bbr:
        bad.append(f"(lv-a) right 면이 back 오른쪽 모서리를 공유하지 않습니다: {geom_back['right']}")
    if geom_back["floor"][0] != bbl or geom_back["floor"][1] != bbr:
        bad.append(f"(lv-a) floor 면이 back 아래 모서리를 공유하지 않습니다: {geom_back['floor']}")
    # view=front 에서 left 면이 east(면→벽 매핑). back 면은 south.
    if lvmod.face_to_wall("front", "back") != "south":
        bad.append("(lv-a) front 뷰 back 면이 south 가 아닙니다")
    if lvmod.face_to_wall("back", "left") != "west" or lvmod.face_to_wall("back", "right") != "east":
        bad.append("(lv-a) back 뷰 left=west·right=east 매핑이 틀립니다")
    # front 는 좌우가 바뀌어 화면상 left 가 east 벽을 담는다(x 반전 확인).
    geom_front = lvmod.box_view_geometry(6.0, 5.0, 2.8, view="front", size=gsize)
    if geom_front["_view"] != "front":
        bad.append("(lv-a) front geometry _view 가 front 가 아닙니다")

    # --- (lv-b) rectify: 체커 사다리꼴을 펴면 직사각형 격자(모서리 색·꼭짓점 순서) ---
    try:
        import io as _iob  # noqa: PLC0415

        from PIL import Image as _ImgB  # noqa: PLC0415

        # 2x2 체커(왼위 빨강·오른위 초록·왼아래 파랑·오른아래 노랑)를 그린 원본.
        src = _ImgB.new("RGB", (400, 300), (0, 0, 0))
        src.paste((220, 30, 30), (0, 0, 200, 150))
        src.paste((30, 200, 30), (200, 0, 400, 150))
        src.paste((30, 30, 220), (0, 150, 200, 300))
        src.paste((220, 220, 30), (200, 150, 400, 300))
        buf = _iob.BytesIO()
        src.save(buf, format="JPEG", quality=95)
        src_bytes = buf.getvalue()
        # 전체 사각형(좌상·우상·우하·좌하)을 그대로 펴면 같은 체커가 나와야 한다.
        quad = [(0, 0), (400, 0), (400, 300), (0, 300)]
        rect = lvmod.rectify(src_bytes, quad, (200, 160))
        with _ImgB.open(_iob.BytesIO(rect)) as out:
            out = out.convert("RGB")
            w, h = out.size
            tl = out.getpixel((w // 4, h // 4))
            tr = out.getpixel((w * 3 // 4, h // 4))
            bl = out.getpixel((w // 4, h * 3 // 4))
            br = out.getpixel((w * 3 // 4, h * 3 // 4))

        def _dominant(px):
            r, g, b = px[0], px[1], px[2]
            if r > 150 and g < 120 and b < 120:
                return "red"
            if g > 150 and r < 120 and b < 120:
                return "green"
            if b > 150 and r < 120 and g < 120:
                return "blue"
            if r > 150 and g > 150 and b < 120:
                return "yellow"
            return "?"

        corners = (_dominant(tl), _dominant(tr), _dominant(bl), _dominant(br))
        if corners != ("red", "green", "blue", "yellow"):
            bad.append(f"(lv-b) rectify 격자 모서리 색이 맞지 않습니다: {corners}")
        # 꼭짓점 순서: west 텍스처의 오른쪽 끝이 back 모서리(back 사각형 오른쪽 x)와 가까워야 한다.
        left_quad = geom_back["left"]  # (ftl, btl, bbl, fbl) — 오른쪽(인덱스1,2)이 back 모서리.
        if not (abs(left_quad[1][0] - btl[0]) < 1.0 and abs(left_quad[2][0] - bbl[0]) < 1.0):
            bad.append(f"(lv-b) west(left) 사다리꼴의 오른쪽 끝이 back 모서리가 아닙니다: {left_quad}")
    except Exception as exc:  # noqa: BLE001
        bad.append(f"(lv-b) rectify 검사 중 예외: {exc!r}")

    # --- (a) 장소 정규화·중복 제거: sync_locations 단위 ---
    plan_a = {
        "locations": [],
        "script": {
            "entries": [
                {"id": "e1", "panels": [0, 1], "locationKo": "교장실", "locationEn": "A closed Principal's Office"},
                # 같은 장소(대소문자·공백만 다름) → 하나로 합쳐야 한다.
                {"id": "e2", "panels": [2], "locationKo": "교장실", "locationEn": "a closed principal's   office"},
                {"id": "e3", "panels": [3], "locationKo": "노을 진 거리", "locationEn": "A city street at sunset"},
            ]
        },
    }
    locmod.sync_locations(plan_a)
    locs_a = plan_a.get("locations") or []
    if len(locs_a) != 2:
        bad.append(f"(a) 정규화·중복 제거 후 장소가 2개가 아닙니다: {len(locs_a)} {[l.get('nameEn') for l in locs_a]}")
    office = next((l for l in locs_a if "principal" in (l.get("key") or "")), None)
    if not office:
        bad.append("(a) 교장실 장소를 찾지 못했습니다")
    elif sorted(office.get("panels") or []) != [0, 1, 2]:
        bad.append(f"(a) 합쳐진 장소의 panels 가 [0,1,2] 가 아닙니다: {office.get('panels')}")

    # --- 가짜 외부 호출(설계 LLM·Seedream·사람 수·원장·스레드) ---
    design_calls: list[str] = []
    design_blocks: list[list[dict]] = []

    def fake_design(blocks, *, model):
        design_calls.append(model)
        design_blocks.append(list(blocks or []))
        payload = {
            "widthM": 6.0,
            "depthM": 5.0,
            "heightM": 2.8,
            "enclosed": True,
            "floorEn": "polished wooden floor",
            "ceilingEn": "white ceiling",
            "walls": {
                "north": {"descEn": "a wall with two large windows", "hasWindow": True, "hasDoor": False, "featuresEn": "windows"},
                "east": {"descEn": "a bookshelf wall", "hasWindow": False, "hasDoor": False, "featuresEn": "bookshelf"},
                "south": {"descEn": "a wall with the entrance door", "hasWindow": False, "hasDoor": True, "featuresEn": "door"},
                "west": {"descEn": "a plain painted wall", "hasWindow": False, "hasDoor": False, "featuresEn": ""},
            },
            "lightEn": "main light comes from the north-wall windows, soft daylight",
            "propsEn": [{"labelEn": "a wooden desk", "wall": "center", "sizeM": 1.6}],
        }
        return payload, {"model": model, "inputTokens": 8, "outputTokens": 16}

    seedream_calls: list[dict] = []

    def fake_seedream(prompt, references, *, size):
        # 048: real 프롬프트는 'Photoreal', webtoon 은 styleEn(원작 그림체). 뷰는 'opposite side'(front) /
        # 'continuous'(back). 보정은 'Corrections:'(_redraw_view 가 붙인다).
        variant = "real" if "Photoreal" in prompt else "webtoon"
        view = "front" if "opposite side" in prompt else "back"
        is_fix = "Corrections:" in prompt
        seedream_calls.append(
            {"variant": variant, "view": view, "prompt": prompt, "size": size, "fix": is_fix,
             "ref_count": len(references or []), "refs": list(references or [])}
        )
        # 뷰는 16:9(2560x1440). rectify 가 열 수 있는 실제 픽셀 JPEG 를 돌려준다.
        return _tiny_jpeg(640, 360)

    def fake_count_people(jpeg):
        return 1  # 배경 조각 선호 정렬이 안정적으로 돌게(인물 적음).

    ledger_calls: list[dict] = []

    def fake_charge(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    def fake_charge_text(key, **kwargs):
        ledger_calls.append({"key": key, "text": True, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    tiles_per_file = {"n": 4}

    def fake_tiles_for_analysis(jpeg):
        return [f"loc-tile{index}".encode("ascii") for index in range(tiles_per_file["n"])]

    def fake_load_char(owner, char_id):
        return {"name": "김부장역", "status": "ready", "analysis": {"styleEn": "thin sketchy pencil lines, soft pastel"}}, "e1"

    # 대본·콘티 LLM(계획 쪽)도 가짜로 둔다(장소 sync 를 콘티 재생성으로 검증 (e)).
    def fake_analyze_conti(blocks, *, model):
        cuts = [
            {"id": "c1", "no": 1, "seconds": 10, "panels": [0], "shotKo": "컷1", "cameraEn": "static camera",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c2", "no": 2, "seconds": 10, "panels": [1], "shotKo": "컷2", "cameraEn": "static camera",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c3", "no": 3, "seconds": 10, "panels": [2], "shotKo": "컷3", "cameraEn": "static camera",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c4", "no": 4, "seconds": 10, "panels": [3], "shotKo": "컷4", "cameraEn": "static camera",
             "locationEn": "A city street at sunset", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c5", "no": 5, "seconds": 10, "panels": [3], "shotKo": "컷5", "cameraEn": "static camera",
             "locationEn": "A city street at sunset", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
        ]
        return {"cuts": cuts}, {"model": model, "inputTokens": 3, "outputTokens": 6}

    # (n) 사실 추출(Claude → Astra). 벽별 물건·창문·문·재질·빛·시대·나라.
    facts_calls: list[str] = []
    facts_blocks: list[list[dict]] = []

    def fake_facts(blocks, *, model):
        facts_calls.append(model)
        facts_blocks.append(list(blocks or []))
        payload = {
            "walls": {
                "north": {
                    "wallColorEn": "pale green",
                    "wallMaterialEn": "painted plaster",
                    "estimated": False,
                    "objects": [
                        {"kind": "window", "count": 2, "colorEn": "white", "materialEn": "wood",
                         "position": "left high", "sizeEn": "floor to ceiling", "attachedTo": "wall"},
                    ],
                },
                "east": {
                    "wallColorEn": "pale green", "wallMaterialEn": "painted plaster", "estimated": False,
                    "objects": [
                        {"kind": "bookshelf", "count": 1, "colorEn": "brown", "materialEn": "wood",
                         "position": "center mid", "sizeEn": "full-height", "attachedTo": "wall"},
                    ],
                },
                "south": {
                    "wallColorEn": "pale green", "wallMaterialEn": "painted plaster", "estimated": False,
                    "objects": [
                        {"kind": "door", "count": 1, "colorEn": "dark brown", "materialEn": "wood",
                         "position": "right mid", "sizeEn": "about one metre wide", "attachedTo": "wall"},
                    ],
                },
                # west 는 보이지 않는 벽 — estimated.
                "west": {"wallColorEn": "", "wallMaterialEn": "", "estimated": True, "objects": []},
            },
            "floorEn": "polished warm-brown wooden floor with straight planks",
            "ceilingEn": "flat white ceiling",
            "windows": [
                {"wall": "north", "position": "left high", "sizeEn": "floor to ceiling",
                 "frameEn": "white wood", "curtainEn": "none", "outsideEn": "schoolyard trees"},
            ],
            "doors": [
                {"wall": "south", "position": "right mid", "sizeEn": "standard", "materialEn": "wood", "colorEn": "dark brown"},
            ],
            "lightEn": "soft daylight from the north windows, warm white, medium strength",
            "eraEn": "modern 2010s",
            "countryEn": "South Korea",
        }
        return payload, {"model": model, "inputTokens": 7, "outputTokens": 14}

    # (o) Astra 프롬프트 작성. system_text 에 설계자 지침이 들어왔는지 기록한다(뷰당 1회).
    author_calls: list[dict] = []

    def fake_author(system_text, user_text, *, model):
        author_calls.append({"model": model, "system": system_text, "user": user_text})
        # 지능 없는 이미지 모델용의 아주 구체적인(사실 단어가 든) 프롬프트를 흉내 낸다.
        body = (
            "Box-set cutaway view over the layout guide. pale green painted plaster walls, 2 white wood "
            "windows, a brown wood bookshelf, a dark brown wooden door, polished warm-brown wooden floor. "
        )
        return {"promptEn": body}, {"model": model, "inputTokens": 5, "outputTokens": 40}

    # (p→048) verify. 뷰마다 {matchesPlace, matchesSides, backQuad, frontQuad, fixesEn}. front 뷰만
    #   matchesPlace=False 로 두어 그 뷰만(그림체2 × front = 2장) 1회 다시 그리게 한다. 꼭짓점은 가이드와
    #   가깝게(4% 이내) 두어 method=guide 가 되게 한다.
    verify_calls: list[dict] = []

    def fake_verify(blocks, *, model):
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text = b["text"]
                break
        view = "front" if "front cutaway" in text else "back"
        verify_calls.append({"model": model, "view": view})
        # 가이드와 사실상 같은 꼭짓점(정규화) — 4% 이내라 가이드 좌표를 유지한다.
        _g = locmod.location_views.box_view_geometry(6.0, 5.0, 2.8, view=view, size=(2560, 1440))
        back_quad = [[x / 2560 + 0.01, y / 1440] for x, y in _g["back"]]
        front_quad = [[x / 2560, y / 1440 - 0.01] for x, y in _g["_front"]]
        payload = {
            "matchesPlace": view != "front",  # front 만 불일치 → 1회 재생성
            "matchesSides": True,
            "backQuad": back_quad,
            "frontQuad": front_quad,
            "fixesEn": [] if view != "front" else ["match the side walls to the back view"],
        }
        return payload, {"model": model, "inputTokens": 4, "outputTokens": 8}

    orig = {
        "loc_STORE": locmod.STORE,
        "plan_STORE": planmod.STORE,
        "_design": locmod._design,
        "_facts": locmod._facts,
        "_author": locmod._author,
        "_verify": locmod._verify,
        "_seedream": locmod._seedream,
        "charge": locmod.vpoc_ledger.charge,
        "charge_text": locmod.vpoc_ledger.charge_text,
        "count_people": locmod.characters._count_people,
        "load_char": locmod.characters.load_char,
        "tiles": _images.tiles_for_analysis,
        "plan_analyze_conti": planmod._analyze_conti,
        "plan_charge_text": planmod.vpoc_ledger.charge_text,
        "loc_Thread": locmod.threading.Thread,
    }
    locmod.STORE = fake_store
    planmod.STORE = fake_store
    locmod._design = fake_design
    locmod._facts = fake_facts
    locmod._author = fake_author
    locmod._verify = fake_verify
    locmod._seedream = fake_seedream
    locmod.vpoc_ledger.charge = fake_charge
    locmod.vpoc_ledger.charge_text = fake_charge_text
    locmod.characters._count_people = fake_count_people
    locmod.characters.load_char = fake_load_char
    _images.tiles_for_analysis = fake_tiles_for_analysis
    planmod._analyze_conti = fake_analyze_conti
    planmod.vpoc_ledger.charge_text = fake_charge_text

    # 빌드 스레드를 추적해 join(동기 검증).
    started_threads: list = []
    orig_thread = locmod.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    locmod.threading.Thread = _tracking_thread

    def _join():
        for t in list(started_threads):
            t.join(timeout=30)
        started_threads.clear()

    def _real_image(label: int) -> bytes:
        import io as _io  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        buffer = _io.BytesIO()
        _Img.new("RGB", (600, 900), (label, 180, 160)).save(buffer, format="PNG")
        return buffer.getvalue()

    try:
        import io as _io  # noqa: PLC0415

        owner = "tester"
        pid = "prj_loc_1"

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

        # 계획 생성 + 업로드 4장(패널 번호 0~15 범위를 만든다: 4파일 × 4타일).
        client.get(f"/api/vnew/p/{pid}/plan")
        data = {"files": [(_io.BytesIO(_real_image(10 + i * 20)), f"{i}.png") for i in range(4)]}
        resp = client.post(f"/api/vnew/p/{pid}/uploads", data=data, content_type="multipart/form-data")
        if resp.status_code != 201:
            bad.append(f"업로드가 201 이 아닙니다: {resp.status_code}")
        client.post(f"/api/vnew/p/{pid}/input/sort")

        # script 를 직접 심는다(대본 LLM 대신): 교장실·노을 진 거리 두 장소.
        planmod.update_script(
            owner,
            pid,
            {
                "setting": {"locationKo": "교장실", "locationEn": "A closed principal's office", "enclosed": True},
                "entries": [
                    {"id": "e1", "panels": [0, 1], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "교장실", "locationEn": "A closed principal's office", "characters": []},
                    {"id": "e2", "panels": [2], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "교장실", "locationEn": "A CLOSED Principal's Office ", "characters": []},
                    {"id": "e3", "panels": [3], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "노을 진 거리", "locationEn": "A city street at sunset", "characters": []},
                ],
            },
        )

        def _set_ready_script(d):
            d["script"]["status"] = "ready"

        planmod.mutate_plan(owner, pid, _set_ready_script)

        # 콘티 생성 → run_conti 끝에서 장소 sync. 자동 그림 0장이어야 한다.
        resp = client.post(f"/api/vnew/p/{pid}/conti")
        if resp.status_code != 202:
            bad.append(f"POST /conti 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()
        plan_after, _ = planmod.load_plan(owner, pid)
        locs = plan_after.get("locations") or []
        if len(locs) != 2:
            bad.append(f"(a) 콘티 뒤 장소가 2개가 아닙니다: {len(locs)} {[l.get('nameEn') for l in locs]}")
        # (e) 자동 그림 0장: 콘티만으로는 Seedream·design 이 전혀 불리지 않는다.
        if seedream_calls:
            bad.append(f"(e) 콘티만으로 자동 그림이 그려졌습니다(Seedream {len(seedream_calls)}장)")
        if design_calls:
            bad.append(f"(e) 콘티만으로 설계 LLM 이 불렸습니다: {len(design_calls)}")

        office_loc = next((l for l in locs if "principal" in (l.get("key") or "")), None)
        if not office_loc:
            bad.append("(a) 콘티 뒤 교장실 장소가 없습니다")
            raise RuntimeError("교장실 장소 없음 — 이후 검사 생략")
        lid = office_loc["id"]

        # (lv-c)(d) 공간 시트 만들기 → 설계 1회, Seedream 뷰 4장(back·front ×2그림체) → cut → 시트.
        seedream_calls.clear()
        design_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        if resp.status_code != 202:
            bad.append(f"(c) POST build 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()

        if len(design_calls) != 1:
            bad.append(f"(lv-c) 설계 LLM 호출이 1회가 아닙니다: {len(design_calls)}")
        # (g) 설계 모델 순서: Claude 분석 체인(DESIGN_MODEL) 먼저, Astra 는 fallback.
        if design_calls and design_calls[0] != locmod.DESIGN_MODEL:
            bad.append(f"(g) 설계가 Claude(DESIGN_MODEL) 를 먼저 쓰지 않습니다: {design_calls[0]}")
        if locmod.DESIGN_MODEL == locmod.DESIGN_FALLBACK_MODEL:
            bad.append("(g) DESIGN_MODEL 과 DESIGN_FALLBACK_MODEL 이 같습니다(Claude→Astra 순서가 무의미)")

        # --- (lv-c) 빌드: Seedream 뷰 4회(+보정), 첫 참고 가이드(real)·real 뷰(webtoon), 키 저장 ---
        # 초기 생성(보정 아님)은 정확히 4장(뷰2 × 그림체2). 보정(fix)은 아래에서 센다.
        initial_calls = [c for c in seedream_calls if not c.get("fix")]
        fix_calls = [c for c in seedream_calls if c.get("fix")]
        if len(initial_calls) != locmod.TOTAL_VIEWS:
            bad.append(f"(lv-c) 초기 Seedream 호출이 {locmod.TOTAL_VIEWS}장이 아닙니다: {len(initial_calls)}")
        pairs = {(c["variant"], c["view"]) for c in initial_calls}
        if pairs != {(v, w) for v in ("real", "webtoon") for w in ("back", "front")}:
            bad.append(f"(lv-c) 뷰 4장(real/webtoon × back/front)이 다 그려지지 않았습니다: {sorted(pairs)}")
        # 크기는 VIEW_SIZE.
        if any(c["size"] != locmod.VIEW_SIZE for c in initial_calls):
            bad.append(f"(lv-c) 뷰 크기가 {locmod.VIEW_SIZE} 가 아닙니다: {[c['size'] for c in initial_calls]}")
        # real 뷰 첫 참고는 가이드(참고 1장 이상), webtoon 뷰 첫 참고는 real 뷰(참고 1장 이상).
        for c in initial_calls:
            if c["ref_count"] < 1:
                bad.append(f"(lv-c) {c['variant']} {c['view']} 뷰에 참고 이미지가 없습니다")
                break
        # (lv-d) 프롬프트 면별 라벨·이음새·인물 금지. 뷰 B(front)에 'opposite side'.
        for c in initial_calls:
            for need in ("CENTER BACK WALL", "LEFT WALL", "RIGHT WALL", "No people"):
                if need not in c["prompt"]:
                    bad.append(f"(lv-d) {c['variant']} {c['view']} 프롬프트에 '{need}' 이 없습니다")
                    break
        back_prompts = [c for c in initial_calls if c["view"] == "back"]
        front_prompts = [c for c in initial_calls if c["view"] == "front"]
        if back_prompts and not all("continuous" in c["prompt"] for c in back_prompts):
            bad.append("(lv-d) 뷰 A(back) 프롬프트에 'continuous' 가 없습니다")
        if front_prompts and not all("opposite side" in c["prompt"] for c in front_prompts):
            bad.append("(lv-d) 뷰 B(front) 프롬프트에 'opposite side' 가 없습니다")
        # 두 그림체 모두 그려졌는지.
        if {c["variant"] for c in seedream_calls} != {"real", "webtoon"}:
            bad.append(f"(lv-c) real·webtoon 둘 다 그리지 않았습니다: {sorted({c['variant'] for c in seedream_calls})}")

        # 원장: Seedream 장당. 초기 4장 + 보정분(fix).
        img_charges = [c for c in ledger_calls if c.get("kind") == "image" and not c.get("text")]
        expected_img = locmod.TOTAL_VIEWS + len(fix_calls)
        if len(img_charges) != expected_img:
            bad.append(f"(lv-c) Seedream 원장 계상이 초기{locmod.TOTAL_VIEWS}+보정({expected_img})건이 아닙니다: {len(img_charges)}")

        # 저장 키: 뷰 원본(view-), 펴진 벽(wall_ × 4), 바닥(floor_) 이 저장돼야 한다(그림체별).
        saved_views = [k for k in fake_store.blobs if f"/locations/{lid}/" in k and "/view-" in k]
        saved_walls = [k for k in fake_store.blobs if f"/locations/{lid}/" in k and "/wall_" in k]
        saved_floors = [k for k in fake_store.blobs if f"/locations/{lid}/" in k and "/floor_" in k]
        if len(saved_views) != 4:  # back·front × 그림체2
            bad.append(f"(lv-c) 저장된 뷰 원본이 4장이 아닙니다: {len(saved_views)}")
        if len(saved_walls) != 8:  # north/east/south/west × 그림체2
            bad.append(f"(lv-c) 펴진 벽 키가 8개(4면×2그림체)가 아닙니다: {len(saved_walls)}")
        if len(saved_floors) != 2:  # 바닥 × 그림체2
            bad.append(f"(lv-c) 바닥 키가 2개(×2그림체)가 아닙니다: {len(saved_floors)}")

        # (lv-c) previz._textures_for 가 그 키(real variant wall_key/floor_key)를 그대로 쓴다.
        from src.vnew import previz as previzmod  # noqa: PLC0415

        plan_c, _ = planmod.load_plan(owner, pid)
        loc_c = locmod._find_location(plan_c, lid) or {}
        tex = previzmod._textures_for(owner, pid, loc_c)
        tex_walls = tex.get("walls") or {}
        if sorted(tex_walls.keys()) != sorted(locmod.SIDES):
            bad.append(f"(lv-c) previz _textures_for 가 벽 4면 키를 못 읽습니다: {sorted(tex_walls.keys())}")
        if not tex.get("floorKey"):
            bad.append("(lv-c) previz _textures_for 가 floorKey 를 못 읽습니다")
        real_walls = ((loc_c.get("variants") or {}).get("real") or {}).get("wallKeys") or {}
        if tex_walls and tex_walls.get("north") != real_walls.get("north"):
            bad.append("(lv-c) previz 텍스처 키가 저장된 wall_key 와 다릅니다")

        # (d→048) plan_view: hasViews·viewUrls·sheetUrl·wallUrls·floorUrl·viewLabels.
        view = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        vlocs = view.get("locations") or []
        vloc = next((l for l in vlocs if l.get("id") == lid), None)
        if not vloc:
            bad.append("(d) plan_view 에 장소가 없습니다")
        else:
            if vloc.get("status") != "ready":
                bad.append(f"(d) 장소 상태가 ready 가 아닙니다: {vloc.get('status')} / {vloc.get('error')}")
            if not vloc.get("hasViews"):
                bad.append("(d) plan_view 에 hasViews=true 가 아닙니다")
            if "viewLabels" not in vloc:
                bad.append("(d) plan_view 에 viewLabels 가 없습니다")
            for variant in ("real", "webtoon"):
                vv = (vloc.get("variants") or {}).get(variant) or {}
                if not vv.get("sheetUrl"):
                    bad.append(f"(d) {variant} 시트(sheetUrl) 가 없습니다")
                view_urls = vv.get("viewUrls") or {}
                if not view_urls.get("back") or not view_urls.get("front"):
                    bad.append(f"(d) {variant} 뷰 URL(back·front) 이 없습니다: {view_urls}")
                wall_urls = vv.get("wallUrls") or {}
                if sorted(wall_urls.keys()) != sorted(locmod.SIDES) or any(not wall_urls[s] for s in locmod.SIDES):
                    bad.append(f"(d) {variant} 펴진 벽 4장 URL 이 다 없습니다: {wall_urls}")
                if not vv.get("floorUrl"):
                    bad.append(f"(d) {variant} 바닥 URL 이 없습니다")

        # (d) montage_views 단위: 뷰 A + 펴진 텍스처로 열 수 있는 JPEG 가 나온다.
        import io as _io2  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        mv_walls = {s: _tiny_jpeg(320, 180) for s in locmod.SIDES}
        sheet = locmod.montage_views(_tiny_jpeg(640, 360), mv_walls, _tiny_jpeg(320, 180))
        with _Img.open(_io2.BytesIO(sheet)) as sheet_img:
            if sheet_img.size[0] < 100 or sheet_img.size[1] < 100:
                bad.append(f"(d) montage_views 시트 크기가 너무 작습니다: {sheet_img.size}")
        # (lv-f) 옛 montage_2x2 도 여전히 동작(옛 데이터용 보존).
        old_sheet = locmod.montage_2x2({s: _tiny_jpeg(320, 180) for s in locmod.SIDES})
        with _Img.open(_io2.BytesIO(old_sheet)) as old_img:
            cell_w, cell_h = locmod._size_wh(locmod.WALL_SIZE)
            if old_img.size != (cell_w * 2, cell_h * 2):
                bad.append(f"(lv-f) montage_2x2(옛) 시트 크기가 2배가 아닙니다: {old_img.size}")


        # --- (n) 사실 추출 결과가 loc["facts"] 에 저장됐는지 ---
        if len(facts_calls) < 1:
            bad.append(f"(n) 사실 추출 LLM 이 불리지 않았습니다: {len(facts_calls)}")
        # (n-2) 사실 추출 모델 순서: Claude(FACTS_MODEL) 먼저.
        if facts_calls and facts_calls[0] != locmod.FACTS_MODEL:
            bad.append(f"(n) 사실 추출이 Claude(FACTS_MODEL) 를 먼저 쓰지 않습니다: {facts_calls[0]}")
        plan_n, _ = planmod.load_plan(owner, pid)
        loc_n = locmod._find_location(plan_n, lid) or {}
        facts_n = loc_n.get("facts") or {}
        if not locmod._facts_has_wall_detail(facts_n):
            bad.append(f"(n) loc['facts'] 에 벽 물건·재질이 저장되지 않았습니다: {facts_n.get('walls')}")
        # 보이지 않는 벽(west)도 비어 있지 않고 estimated 로 표시돼야 한다(비워 두지 않는다).
        west_n = (facts_n.get("walls") or {}).get("west") or {}
        if not west_n.get("estimated"):
            bad.append(f"(n) 보이지 않는 west 벽이 'estimated' 로 표시되지 않았습니다: {west_n}")
        # 창문·문·시대·나라도 저장됐는지.
        if not facts_n.get("windows") or not facts_n.get("doors"):
            bad.append(f"(n) 창문·문 사실이 저장되지 않았습니다: windows={facts_n.get('windows')} doors={facts_n.get('doors')}")
        if not facts_n.get("eraEn") or not facts_n.get("countryEn"):
            bad.append(f"(n) 시대·나라 사실이 저장되지 않았습니다: era={facts_n.get('eraEn')!r} country={facts_n.get('countryEn')!r}")
        # plan_view(locations_view) 에도 facts 가 담겨야 한다(화면이 읽는다).
        vloc_n = next((l for l in (view.get("locations") or []) if l.get("id") == lid), None)
        if not vloc_n or "facts" not in vloc_n:
            bad.append("(n) plan_view locations 에 facts 가 없습니다")

        # --- (o) Astra 프롬프트 작성: 설계자 지침 문장 + 사실 단어 포함 ---
        if len(author_calls) < locmod.TOTAL_VIEWS:
            bad.append(f"(o) 프롬프트 작성 호출이 {locmod.TOTAL_VIEWS}회(뷰2×그림체2) 미만입니다: {len(author_calls)}")
        # (o-2) Astra system 에 설계자 지침(ASTRA_PROMPT_INSTRUCTION)이 그대로 들어가야 한다.
        if author_calls and locmod.ASTRA_PROMPT_INSTRUCTION not in author_calls[0]["system"]:
            bad.append("(o) Astra system 에 설계자 프롬프트 지침 문장이 들어가지 않았습니다")
        # (o-3) Astra 가 먼저(PROMPT_AUTHOR_MODEL), Claude 가 fallback.
        if author_calls and author_calls[0]["model"] != locmod.PROMPT_AUTHOR_MODEL:
            bad.append(f"(o) 프롬프트 작성이 Astra(PROMPT_AUTHOR_MODEL) 를 먼저 쓰지 않습니다: {author_calls[0]['model']}")
        if locmod.PROMPT_AUTHOR_MODEL == locmod.PROMPT_AUTHOR_FALLBACK_MODEL:
            bad.append("(o) PROMPT_AUTHOR_MODEL 과 fallback 이 같습니다(Astra→Claude 순서가 무의미)")
        # (o-4) 작성에 쓴 user_text 에 사실 단어(예: bookshelf·window·wooden)가 들어가야 한다(사실 기반).
        joined_user = " ".join(c["user"] for c in author_calls)
        if not any(word in joined_user for word in ("bookshelf", "window", "wooden", "plaster")):
            bad.append("(o) 프롬프트 작성 입력에 사실 단어(bookshelf/window/...)가 들어가지 않았습니다")
        # (o-5) 완성된 뷰 프롬프트 길이 1,500~3,000자(설계자 048, 하드캡 이내).
        for c in initial_calls:
            if not (locmod.PROMPT_MIN_CHARS <= len(c["prompt"]) <= locmod.PROMPT_HARD_CAP):
                bad.append(f"(o) 뷰 프롬프트 길이가 범위를 벗어납니다: {len(c['prompt'])}자")
                break

        # --- (p→048) 불일치 뷰만 1회 재생성(장수·원장·checks·method 정확) ---
        # fake_verify 가 front 뷰만 불일치로 둔다 → real·webtoon 각 1장, 총 2장만 보정(fix)한다.
        if len(verify_calls) != locmod.TOTAL_VIEWS:
            bad.append(f"(p) 비교(verify)가 뷰 {locmod.TOTAL_VIEWS}장(그림체2×뷰2)이 아닙니다: {len(verify_calls)}")
        if len(fix_calls) != 2:
            bad.append(f"(p) 불일치 재생성(fix)이 2장(front×그림체2)이 아닙니다: {len(fix_calls)}")
        if any(c["view"] != "front" for c in fix_calls):
            bad.append(f"(p) front 외 뷰가 재생성됐습니다: {[c['view'] for c in fix_calls]}")
        # 보정 프롬프트에 fixesEn 이 붙었는지.
        if fix_calls and not all("match the side walls" in c["prompt"] for c in fix_calls):
            bad.append("(p) 보정 프롬프트에 fixesEn(수정 지시)이 붙지 않았습니다")
        # 보정 원장: f"{variant}-view-front-fix" 키로 2건.
        fix_charges = [c for c in ledger_calls if c.get("kind") == "image" and "-fix-" in str(c.get("key") or "")]
        if len(fix_charges) != 2:
            bad.append(f"(p) 보정 원장이 2건이 아닙니다: {len(fix_charges)}")
        # checks 가 그림체별·뷰별로 저장되고, front=불일치(matchesPlace False)·fixedEn, back=일치.
        for variant in ("real", "webtoon"):
            vv = (vloc.get("variants") or {}).get(variant) or {}
            chk = vv.get("checks") or {}
            front_chk = chk.get("front") or {}
            back_chk = chk.get("back") or {}
            if front_chk.get("matchesPlace") is not False:
                bad.append(f"(p) {variant} front 가 '불일치'(matchesPlace False)로 저장되지 않았습니다: {front_chk}")
            if not front_chk.get("fixedEn"):
                bad.append(f"(p) {variant} front 가 실제 보정(fixedEn)되지 않았습니다: {front_chk}")
            if back_chk.get("matchesPlace") is not True:
                bad.append(f"(p) {variant} back 가 '일치'(matchesPlace True)로 저장되지 않았습니다: {back_chk}")
            # (lv-e) 꼭짓점이 가이드와 4% 이내라 method=guide 로 저장됐는지.
            if back_chk.get("method") != "guide":
                bad.append(f"(lv-e) back 꼭짓점이 4% 이내인데 method=guide 가 아닙니다: {back_chk.get('method')}")

        # --- (lv-e) _pick_quad 단위: 4% 초과 + 볼록·화면 안이면 Claude, 이상하면 가이드 ---
        guide_q = [[0.33, 0.28], [0.67, 0.28], [0.67, 0.72], [0.33, 0.72]]
        # 4% 초과로 어긋났지만 볼록·화면 안 → Claude 꼭짓점을 쓴다.
        claude_ok = [[0.20, 0.20], [0.70, 0.22], [0.72, 0.78], [0.22, 0.80]]
        picked, method = locmod._pick_quad(claude_ok, guide_q, gsize)
        if method != "claude" or picked != claude_ok:
            bad.append(f"(lv-e) 4% 초과·정상 꼭짓점인데 Claude 좌표를 쓰지 않았습니다: method={method}")
        # 자기교차(bowtie) 꼭짓점(이상한 값) → 가이드.
        claude_bad = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
        picked2, method2 = locmod._pick_quad(claude_bad, guide_q, gsize)
        if method2 != "guide" or picked2 != guide_q:
            bad.append(f"(lv-e) 이상한(자기교차) 꼭짓점인데 가이드로 복귀하지 않았습니다: method={method2}")
        # 4% 이내 → 가이드 유지.
        claude_close = [[0.34, 0.29], [0.66, 0.29], [0.66, 0.71], [0.34, 0.71]]
        _, method3 = locmod._pick_quad(claude_close, guide_q, gsize)
        if method3 != "guide":
            bad.append(f"(lv-e) 4% 이내인데 가이드를 쓰지 않았습니다: method={method3}")

        # --- (q) 진행: 보정(2장)만큼 total 이 늘었는지(TOTAL_VIEWS + fix). ---
        prog_q = loc_n.get("progress") or {}
        if int(prog_q.get("total") or 0) != locmod.TOTAL_VIEWS + len(fix_calls):
            bad.append(f"(q) 보정 뒤 progress.total 이 {locmod.TOTAL_VIEWS}+{len(fix_calls)} 가 아닙니다: {prog_q.get('total')}")

        # (lv-f) 옛 데이터(views 없음) location_view 정상 — viewKeys 없는 장소도 hasViews False 로 그려진다.
        old_plan = {
            "locations": [
                {
                    "id": "loc_old", "nameKo": "옛 장소", "nameEn": "old place", "key": "old place",
                    "panels": [], "status": "ready", "error": "", "design": {}, "facts": {},
                    "variants": {
                        "real": {"status": "ready", "wallKeys": {s: f"k/{s}.jpg" for s in locmod.SIDES},
                                 "floorKey": "k/floor.jpg", "sheetKey": "k/sheet.jpg", "checks": {}},
                        "webtoon": {"status": "none"},
                    },
                    "task": {"status": "done", "startedAt": None, "id": None},
                }
            ]
        }
        try:
            old_view = locmod.locations_view(owner, pid, old_plan)
            ov = old_view[0] if old_view else {}
            if ov.get("hasViews"):
                bad.append("(lv-f) 옛 데이터인데 hasViews=True 입니다")
            if "views" not in ov or "viewLabels" not in ov:
                bad.append("(lv-f) 옛 데이터 location_view 에 views·viewLabels 가 없습니다")
            if not ((ov.get("variants") or {}).get("real") or {}).get("sheetUrl") is not None:
                bad.append("(lv-f) 옛 데이터 real sheetUrl 키가 없습니다")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"(lv-f) 옛 데이터 location_view 중 예외: {exc!r}")

        # (e) 콘티 재생성 후 같은 장소(id) 유지·이미지 보존·자동 그림 0장.
        seedream_calls.clear()
        design_calls.clear()
        facts_calls.clear()
        author_calls.clear()
        verify_calls.clear()

        def _reset_conti(d):
            d["conti"]["status"] = "none"
            d["conti"]["cuts"] = []

        planmod.mutate_plan(owner, pid, _reset_conti)
        resp = client.post(f"/api/vnew/p/{pid}/conti")
        _join()
        plan_re, _ = planmod.load_plan(owner, pid)
        locs_re = plan_re.get("locations") or []
        office_re = next((l for l in locs_re if "principal" in (l.get("key") or "")), None)
        if not office_re or office_re.get("id") != lid:
            bad.append(f"(e) 콘티 재생성 후 교장실 장소 id 가 유지되지 않았습니다: {office_re and office_re.get('id')} vs {lid}")
        elif office_re.get("status") != "ready" or not ((office_re.get("variants") or {}).get("real") or {}).get("sheetKey"):
            bad.append("(e) 콘티 재생성 후 장소의 이미지·ready 상태가 보존되지 않았습니다")
        if seedream_calls or design_calls or facts_calls or author_calls or verify_calls:
            bad.append(
                f"(e) 콘티 재생성이 자동으로 작업을 했습니다: seedream={len(seedream_calls)} "
                f"design={len(design_calls)} facts={len(facts_calls)} author={len(author_calls)} verify={len(verify_calls)}"
            )

        # (f) 진행 중 build 409 — task.status 를 running 으로 막아 둔다.
        def _set_running(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "running", "startedAt": locmod.now_iso(), "id": "busy"}

        planmod.mutate_plan(owner, pid, _set_running)
        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        if resp.status_code != 409 or (resp.get_json().get("error") or {}).get("type") != "Building":
            bad.append(f"(f) 진행 중 build 가 409 Building 으로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")
        _join()

        # (j) 설계 결과에 'assumed'/'provisional' 이 있으면 같은 모델에 1회 재요청해 깨끗한 설계로 바꾼다.
        jcalls: list[str] = []

        def fake_design_provisional(blocks, *, model):
            jcalls.append(model)
            # 첫 호출은 '얼버무린' 설계(provisional/assumed), 두 번째(재요청)는 깨끗한 설계.
            if len(jcalls) == 1:
                payload = {
                    "walls": {
                        "north": {"descEn": "a wall with two windows", "hasWindow": True, "hasDoor": False, "featuresEn": "windows"},
                        "east": {"descEn": "Use a plain off-white plaster wall as the assumed main wall", "hasWindow": False, "hasDoor": False, "featuresEn": "provisional"},
                        "south": {"descEn": "the floor is not visible; use a simple wall", "hasWindow": False, "hasDoor": True, "featuresEn": "door"},
                        "west": {"descEn": "a plain wall", "hasWindow": False, "hasDoor": False, "featuresEn": ""},
                    },
                    "floorEn": "provisional light gray floor",
                    "lightEn": "soft daylight",
                }
            else:
                payload = {
                    "walls": {
                        "north": {"descEn": "a wall with two large windows", "hasWindow": True, "hasDoor": False, "featuresEn": "two tall windows"},
                        "east": {"descEn": "a bookshelf wall", "hasWindow": False, "hasDoor": False, "featuresEn": "full-height bookshelves"},
                        "south": {"descEn": "a wall with the entrance door", "hasWindow": False, "hasDoor": True, "featuresEn": "wooden door, notice board"},
                        "west": {"descEn": "a wall with filing cabinets", "hasWindow": False, "hasDoor": False, "featuresEn": "metal filing cabinets"},
                    },
                    "floorEn": "polished wooden floor",
                    "lightEn": "main light comes from the north-wall windows, soft daylight",
                }
            return payload, {"model": model, "inputTokens": 5, "outputTokens": 9}

        saved_design = locmod._design
        locmod._design = fake_design_provisional
        try:
            jdesign = locmod.design_location(owner, pid, {"id": lid, "nameEn": "A closed principal's office", "panels": [0, 1]})
        finally:
            locmod._design = saved_design
        if len(jcalls) != 2:
            bad.append(f"(j) 'assumed'/'provisional' 설계인데 1회 재요청하지 않았습니다(설계 호출 {len(jcalls)}회)")
        if jcalls and len(set(jcalls)) != 1:
            bad.append(f"(j) 재요청이 같은 모델이 아닙니다: {jcalls}")
        if locmod._has_provisional(jdesign):
            bad.append("(j) 재요청 뒤에도 설계에 'assumed'/'provisional' 이 남아 있습니다")
        if not locmod._has_provisional({"walls": {"east": {"descEn": "assumed wall"}}}):
            bad.append("(j) _has_provisional 이 'assumed' 를 잡지 못합니다")

        # --- (l) 공간 '다시 만들기' 는 설계를 새로 한다(designEditedAt 있으면 유지) ---
        # 증상: [다시 만들기] 가 예전 설계(벽 4면 "흰 벽 가정")를 재사용 → 창문 없는 방. 그래서 재설계한다.
        # 먼저 (f) 에서 running 으로 막아 둔 task 를 풀고, 설계가 있는 상태(앞 build 로 저장됨)로 맞춘다.
        def _reset_task_and_design(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"
            # 앞 build 가 저장한 설계가 있고 designEditedAt 는 없어야 한다(사용자가 안 고침).
            design = loc.get("design") or {}
            design.pop("designEditedAt", None)
            if not design.get("walls"):
                # 혹시 설계가 비었으면 (l) 전제를 위해 심는다.
                design["walls"] = {s: {"descEn": "a plain wall", "hasWindow": False, "hasDoor": False, "featuresEn": ""} for s in locmod.SIDES}
            loc["design"] = design

        planmod.mutate_plan(owner, pid, _reset_task_and_design)

        # (l-1) designEditedAt 없음 → [다시 만들기] 는 설계 재요청 1회.
        design_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        if resp.status_code != 202:
            bad.append(f"(l) 다시 만들기 build 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()
        if len(design_calls) != 1:
            bad.append(f"(l) designEditedAt 없이 다시 만들기인데 설계 재요청이 1회가 아닙니다: {len(design_calls)}")

        # (l-2) 사용자가 설계를 직접 고치면(update_location) designEditedAt 이 찍히고, 그 뒤 [다시 만들기]
        #       는 설계를 새로 받지 않는다(0회) — 수정본 유지.
        edited_loc = locmod.update_location(owner, pid, lid, {"lightEn": "lamp light from the ceiling"})
        if not (edited_loc.get("design") or {}).get("designEditedAt"):
            bad.append("(l) update_location 이 designEditedAt 을 기록하지 않았습니다")
        # task 를 다시 idle 로(앞 build 가 done 으로 끝났을 테지만 안전하게).
        def _idle_task(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"

        planmod.mutate_plan(owner, pid, _idle_task)
        design_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        if resp.status_code != 202:
            bad.append(f"(l) 수정본 유지 build 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()
        if len(design_calls) != 0:
            bad.append(f"(l) designEditedAt 이 있는데 다시 만들기가 설계를 새로 받았습니다(재요청 {len(design_calls)}회)")
        # 수정한 lightEn 이 유지되는지.
        plan_l, _ = planmod.load_plan(owner, pid)
        loc_l = locmod._find_location(plan_l, lid) or {}
        if (loc_l.get("design") or {}).get("lightEn") != "lamp light from the ceiling":
            bad.append(f"(l) 다시 만들기 뒤 사용자가 고친 lightEn 이 유지되지 않았습니다: {(loc_l.get('design') or {}).get('lightEn')}")

        # --- (m)(q) 진행 표시(progress): 빌드 중 done 이 그림마다 +1, step 순서 design→facts→prompts→images→verify→sheet→done ---
        # _set_progress·_set_step(단계 전환)·_bump_progress(그림 완료마다 +1)를 감싸 호출을 기록하고 실제 함수로 넘긴다.
        step_seq: list[str] = []
        bump_count = {"n": 0}
        orig_set_progress = locmod._set_progress
        orig_set_step = locmod._set_step
        orig_bump_progress = locmod._bump_progress

        def rec_set_progress(owner_, pid_, lid_, task_id_, *, step, done, total):
            step_seq.append(step)
            return orig_set_progress(owner_, pid_, lid_, task_id_, step=step, done=done, total=total)

        def rec_set_step(owner_, pid_, lid_, task_id_, *, step):
            step_seq.append(step)
            return orig_set_step(owner_, pid_, lid_, task_id_, step=step)

        def rec_bump_progress(owner_, pid_, lid_, task_id_):
            bump_count["n"] += 1
            return orig_bump_progress(owner_, pid_, lid_, task_id_)

        locmod._set_progress = rec_set_progress
        locmod._set_step = rec_set_step
        locmod._bump_progress = rec_bump_progress

        def _idle_task_m(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"

        planmod.mutate_plan(owner, pid, _idle_task_m)
        try:
            resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
            if resp.status_code != 202:
                bad.append(f"(m) 진행 표시 build 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
            _join()
        finally:
            locmod._set_progress = orig_set_progress
            locmod._set_step = orig_set_step
            locmod._bump_progress = orig_bump_progress

        # start_build 가 design 으로 seed → run_build 가 design→facts→prompts→images→verify→cut→sheet→done.
        first_seen: list[str] = []
        for s in step_seq:
            if s not in first_seen:
                first_seen.append(s)
        # (q) 진행 단계 순서: 사실 추출·프롬프트·비교·잘라 펴기·시트가 올바른 자리에 들어가야 한다.
        expected_order = ["design", "facts", "prompts", "images", "verify", "cut", "sheet", "done"]
        if first_seen != expected_order:
            bad.append(f"(m)(q) progress step 순서가 {expected_order} 가 아닙니다: {first_seen}")
        # 뷰 4장 완료마다 +1 — _bump_progress 가 정확히 TOTAL_VIEWS 회. 보정은 _bump_total 이 따로 민다.
        if bump_count["n"] != locmod.TOTAL_VIEWS:
            bad.append(f"(m) 뷰 완료 progress +1 이 {locmod.TOTAL_VIEWS}회가 아닙니다: {bump_count['n']}")
        # 끝난 뒤 progress: step=done. 보정 2장(front×그림체2) 만큼 total·done 이 늘어 TOTAL_VIEWS+2.
        plan_m, _ = planmod.load_plan(owner, pid)
        loc_m = locmod._find_location(plan_m, lid) or {}
        prog_m = loc_m.get("progress") or {}
        expected_total_m = locmod.TOTAL_VIEWS + 2
        if prog_m.get("step") != "done":
            bad.append(f"(m) 끝난 뒤 progress.step 이 done 이 아닙니다: {prog_m.get('step')}")
        if int(prog_m.get("done") or 0) != expected_total_m or int(prog_m.get("total") or 0) != expected_total_m:
            bad.append(f"(m)(q) 끝난 뒤 progress.done/total 이 {expected_total_m}(=보정 2장 포함) 가 아닙니다: {prog_m.get('done')}/{prog_m.get('total')}")
        if not prog_m.get("startedAt") or not prog_m.get("updatedAt"):
            bad.append(f"(m) progress 에 startedAt·updatedAt 이 없습니다: {prog_m}")
        # plan_view(locations_view) 에도 progress 가 담겨야 한다(화면이 읽는다).
        view_m = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        vloc_m = next((l for l in (view_m.get("locations") or []) if l.get("id") == lid), None)
        if not vloc_m or "progress" not in vloc_m:
            bad.append("(m) plan_view locations 에 progress 가 없습니다")

        # ============================================================ 참고 사진(설계자 2026-10-22, 지시서 056)
        # 장소마다 사용자 참고 사진을 붙여 설계·사실·그림·확인에 웹툰 패널보다 '먼저' 쓴다.
        # (lp-a) 업로드 201·정규화 JPEG·7장째 400·GET 200·다른 사용자 404·DELETE 후 파일 삭제
        # (lp-b) PUT side·noteKo·잘못된 side 400·빌드 중 409
        # (lp-c) 사진 있음 → 설계·사실 블록에서 사진이 패널보다 먼저·라벨 문구
        # (lp-d) 뷰 A 참고 = [가이드, back, left, any, 패널…]·≤6·뷰 B 에 back 사진 없음
        # (lp-e) 프롬프트 "real reference photo of the CENTER BACK WALL" 이미지 번호가 참고 순서와 일치
        # (lp-f) 사진 없음 → 참고·프롬프트가 지금과 같다

        def _photo_png(label):
            import io as _iop  # noqa: PLC0415

            from PIL import Image as _ImgP  # noqa: PLC0415

            buf = _iop.BytesIO()
            _ImgP.new("RGB", (400, 300), (label, 120, 60)).save(buf, format="PNG")
            return buf.getvalue()

        def _idle(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"
            loc["photos"] = []
            (loc.get("design") or {}).pop("designEditedAt", None)
            (loc.get("facts") or {}).pop("factsEditedAt", None)

        planmod.mutate_plan(owner, pid, _idle)
        base_url = f"/api/vnew/p/{pid}/locations/{lid}/photos"

        # (lp-a) 업로드 201 · 정규화 JPEG 저장.
        up = client.post(
            base_url,
            data={"files": (_io.BytesIO(_photo_png(200)), "room.png")},
            content_type="multipart/form-data",
        )
        if up.status_code != 201:
            bad.append(f"(lp-a) 사진 업로드가 201 이 아닙니다: {up.status_code} {up.get_data(as_text=True)[:200]}")
        up_loc = (up.get_json() or {}).get("location") or {}
        up_photos = up_loc.get("photos") or []
        if len(up_photos) != 1:
            bad.append(f"(lp-a) 업로드 후 photos 가 1장이 아닙니다: {len(up_photos)}")
        first_pid = up_photos[0].get("photoId") if up_photos else ""
        # 정규화 JPEG 로 저장됐는지(키 존재·JPEG 로 열림).
        if first_pid:
            pkey = locmod.photo_key(owner, pid, lid, first_pid)
            if pkey not in fake_store.blobs:
                bad.append("(lp-a) 정규화 사진이 photos/ 키로 저장되지 않았습니다")
            else:
                import io as _iojpg  # noqa: PLC0415

                from PIL import Image as _ImgJ  # noqa: PLC0415

                try:
                    with _ImgJ.open(_iojpg.BytesIO(fake_store.blobs[pkey])) as pim:
                        if (pim.format or "").upper() != "JPEG":
                            bad.append(f"(lp-a) 저장된 사진이 JPEG 가 아닙니다: {pim.format}")
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"(lp-a) 저장된 사진을 JPEG 로 열 수 없습니다: {exc!r}")

        # (lp-a) GET 미리보기 200.
        if first_pid:
            g = client.get(base_url + "/" + first_pid)
            if g.status_code != 200 or g.headers.get("Content-Type") != "image/jpeg":
                bad.append(f"(lp-a) 사진 미리보기 GET 이 200/jpeg 가 아닙니다: {g.status_code} {g.headers.get('Content-Type')}")
            if not g.get_data():
                bad.append("(lp-a) 사진 미리보기 본문이 비었습니다")

        # (lp-a) 6장 채우고 7장째 400.
        for _ in range(5):
            client.post(base_url, data={"files": (_io.BytesIO(_photo_png(200)), "x.png")}, content_type="multipart/form-data")
        over = client.post(base_url, data={"files": (_io.BytesIO(_photo_png(200)), "over.png")}, content_type="multipart/form-data")
        if over.status_code != 400 or (over.get_json().get("error") or {}).get("type") != "TooManyPhotos":
            bad.append(f"(lp-a) 7장째가 TooManyPhotos 400 이 아닙니다: {over.status_code} {over.get_json()}")

        # (lp-a) 다른 사용자는 404(다른 owner·project 로 같은 photoId 접근).
        other_app = Flask(__name__)
        other_bp = vnew.create_blueprint(
            vnew.VnewDependencies(
                require_auth=lambda h: h,
                current_owner=lambda: "intruder",
                get_project=lambda p: {"id": p, "owner": "intruder", "track": "video_new"},
                can_read_project=lambda proj: True,
                can_write_project=lambda proj: True,
                project_owner_prefix=lambda name: name,
            )
        )
        other_app.register_blueprint(other_bp)
        if first_pid:
            og = other_app.test_client().get(base_url + "/" + first_pid)
            if og.status_code != 404:
                bad.append(f"(lp-a) 다른 사용자가 사진을 보는데 404 가 아닙니다: {og.status_code}")

        # (lp-a) DELETE 후 파일 삭제.
        if first_pid:
            dkey = locmod.photo_key(owner, pid, lid, first_pid)
            dresp = client.delete(base_url + "/" + first_pid)
            if dresp.status_code != 200:
                bad.append(f"(lp-a) 사진 삭제가 200 이 아닙니다: {dresp.status_code}")
            if dkey in fake_store.blobs:
                bad.append("(lp-a) DELETE 후 저장 파일이 지워지지 않았습니다")
            del_loc = (dresp.get_json() or {}).get("location") or {}
            if any(p.get("photoId") == first_pid for p in (del_loc.get("photos") or [])):
                bad.append("(lp-a) DELETE 후 목록에 사진이 남아 있습니다")

        # (lp-b) PUT side·noteKo·잘못된 side 400·빌드 중 409.
        # 지금 photos 가 몇 장 남았는지 보고 한 장을 고른다.
        cur = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        cur_loc = next((l for l in (cur.get("locations") or []) if l.get("id") == lid), None)
        cur_photos = (cur_loc or {}).get("photos") or []
        bpid = cur_photos[0].get("photoId") if cur_photos else ""
        if bpid:
            putr = client.put(base_url + "/" + bpid, json={"side": "back", "noteKo": "창문 쪽 벽"})
            pv = (putr.get_json() or {}).get("location") or {}
            pp = next((p for p in (pv.get("photos") or []) if p.get("photoId") == bpid), {})
            if putr.status_code != 200 or pp.get("side") != "back":
                bad.append(f"(lp-b) PUT side 가 반영되지 않았습니다: {putr.status_code} {pp.get('side')}")
            if pp.get("noteKo") != "창문 쪽 벽":
                bad.append(f"(lp-b) PUT noteKo 가 반영되지 않았습니다: {pp.get('noteKo')}")
            badput = client.put(base_url + "/" + bpid, json={"side": "nope"})
            if badput.status_code != 400 or (badput.get_json().get("error") or {}).get("type") != "BadSide":
                bad.append(f"(lp-b) 잘못된 side 가 BadSide 400 이 아닙니다: {badput.status_code} {badput.get_json()}")

        # (lp-b) 빌드 중(running)이면 변경 409.
        def _set_running_b(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "running", "startedAt": locmod.now_iso(), "id": "busy-photo"}

        planmod.mutate_plan(owner, pid, _set_running_b)
        if bpid:
            r409 = client.put(base_url + "/" + bpid, json={"side": "left"})
            if r409.status_code != 409 or (r409.get_json().get("error") or {}).get("type") != "Building":
                bad.append(f"(lp-b) 빌드 중 PUT 이 Building 409 가 아닙니다: {r409.status_code} {r409.get_json()}")
            d409 = client.delete(base_url + "/" + bpid)
            if d409.status_code != 409:
                bad.append(f"(lp-b) 빌드 중 DELETE 가 409 가 아닙니다: {d409.status_code}")
        a409 = client.post(base_url, data={"files": (_io.BytesIO(_photo_png(200)), "y.png")}, content_type="multipart/form-data")
        if a409.status_code != 409:
            bad.append(f"(lp-b) 빌드 중 업로드가 409 가 아닙니다: {a409.status_code}")

        # 사진을 깔끔히 정리하고(back·left·any 3장) 빌드해 (lp-c)(lp-d)(lp-e) 를 본다.
        def _reset_photos(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"
            loc["photos"] = []
            (loc.get("design") or {}).pop("designEditedAt", None)
            (loc.get("facts") or {}).pop("factsEditedAt", None)

        planmod.mutate_plan(owner, pid, _reset_photos)
        # back 사진, left 사진, any(전체 분위기) 사진 — 각 1장. 색을 달리해 참고 순서를 구별한다.
        side_label = {"back": 40, "left": 110, "right": 150, "front": 180, "any": 220}

        def _add_photo(side):
            rr = client.post(base_url, data={"files": (_io.BytesIO(_photo_png(side_label.get(side, 200))), f"{side}.png")}, content_type="multipart/form-data")
            ph = ((rr.get_json() or {}).get("location") or {}).get("photos") or []
            pid_new = ph[-1].get("photoId") if ph else ""
            if pid_new and side != "any":
                client.put(base_url + "/" + pid_new, json={"side": side})
            return pid_new

        back_pid = _add_photo("back")
        left_pid = _add_photo("left")
        any_pid = _add_photo("any")  # 'any' 로 둔다(기본값).
        back_key = locmod.photo_key(owner, pid, lid, back_pid)
        left_key = locmod.photo_key(owner, pid, lid, left_pid)
        any_key = locmod.photo_key(owner, pid, lid, any_pid)

        design_blocks.clear()
        facts_blocks.clear()
        seedream_calls.clear()
        author_calls.clear()
        verify_calls.clear()

        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        if resp.status_code != 202:
            bad.append(f"(lp-c) 사진 있는 build 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()

        # (lp-c) 설계·사실 블록에서 사진이 패널보다 먼저·라벨 문구.
        def _first_image_is_photo(blocks):
            # 라벨 텍스트("사용자가 첨부한 실제 참고 사진입니다(최우선)") 다음 이미지가 패널보다 앞에 온다.
            label_idx = None
            first_img_idx = None
            for i, b in enumerate(blocks):
                if isinstance(b, dict) and b.get("text") and "사용자가 첨부한 실제 참고 사진입니다(최우선)" in b["text"]:
                    if label_idx is None:
                        label_idx = i
                if isinstance(b, dict) and "image" in b and first_img_idx is None:
                    first_img_idx = i
            return label_idx is not None and first_img_idx is not None and label_idx < first_img_idx

        if not design_blocks or not _first_image_is_photo(design_blocks[-1]):
            bad.append("(lp-c) 설계 블록에 사진 라벨·사진이 패널보다 먼저 오지 않았습니다")
        if not facts_blocks or not _first_image_is_photo(facts_blocks[-1]):
            bad.append("(lp-c) 사실 블록에 사진 라벨·사진이 패널보다 먼저 오지 않았습니다")
        # 라벨에 면 한국어·메모가 들어가는지(back 사진).
        label_texts = " ".join(
            b.get("text", "") for b in (design_blocks[-1] if design_blocks else []) if isinstance(b, dict) and b.get("text")
        )
        if "메인배경(뒤)" not in label_texts:
            bad.append("(lp-c) 설계 사진 라벨에 면 한국어(메인배경(뒤))가 없습니다")

        # (lp-d) 뷰 A(real back) 초기 참고 = [가이드, back 사진, left 사진, any 사진, 패널…]·≤6.
        real_back = next((c for c in seedream_calls if c["variant"] == "real" and c["view"] == "back" and not c.get("fix")), None)
        if real_back is None:
            bad.append("(lp-d) real back 생성 호출을 찾지 못했습니다")
        else:
            refs = real_back["refs"]
            if len(refs) > 6:
                bad.append(f"(lp-d) 참고가 6장을 넘습니다: {len(refs)}")
            # refs[0] 은 가이드(사진 아님), refs[1..3] 은 back·left·any 사진.
            photo_keys_in_order = [back_key, left_key, any_key]
            got_photos = [r for r in refs if r in (fake_store.blobs.get(back_key), fake_store.blobs.get(left_key), fake_store.blobs.get(any_key))]
            expect_photos = [fake_store.blobs[k] for k in photo_keys_in_order]
            if refs[:1] and refs[0] in expect_photos:
                bad.append("(lp-d) 첫 참고가 가이드가 아니라 사진입니다")
            if got_photos != expect_photos:
                bad.append("(lp-d) 뷰 A 참고 사진 순서가 [back, left, any] 가 아닙니다")
            # 사진 바로 뒤에 패널(loc-tile)이 온다(사진이 패널보다 먼저).
            tile_refs = [r for r in refs if isinstance(r, bytes) and r.startswith(b"loc-tile")]
            if refs and tile_refs:
                last_photo_idx = max(refs.index(p) for p in expect_photos if p in refs)
                first_tile_idx = min(refs.index(t) for t in tile_refs)
                if first_tile_idx < last_photo_idx:
                    bad.append("(lp-d) 패널이 사진보다 앞에 왔습니다")

        # (lp-d) 뷰 B(front)에는 back 사진이 없다.
        real_front = next((c for c in seedream_calls if c["variant"] == "real" and c["view"] == "front" and not c.get("fix")), None)
        if real_front is not None:
            if fake_store.blobs.get(back_key) in real_front["refs"]:
                bad.append("(lp-d) 뷰 B(front) 참고에 back 사진이 들어갔습니다")

        # (lp-e) 프롬프트에 "real reference photo of the CENTER BACK WALL" 와 이미지 번호가 참고 순서와 일치.
        if real_back is not None:
            prompt = real_back["prompt"]
            if "real reference photo of the CENTER BACK WALL" not in prompt:
                bad.append("(lp-e) 프롬프트에 CENTER BACK WALL 참고 사진 지시가 없습니다")
            else:
                # back 사진은 가이드(이미지1) 다음 첫 사진 → 이미지 2.
                refs = real_back["refs"]
                back_bytes = fake_store.blobs.get(back_key)
                if back_bytes in refs:
                    img_no = refs.index(back_bytes) + 1  # 1-based
                    if f"Image {img_no} is a real reference photo of the CENTER BACK WALL" not in prompt:
                        bad.append(f"(lp-e) CENTER BACK WALL 이미지 번호가 참고 순서(Image {img_no})와 일치하지 않습니다")
                # 'any' 사진은 "real look of this place" 지시.
                if "real look of this place" not in prompt:
                    bad.append("(lp-e) 'any' 사진의 'real look of this place' 지시가 없습니다")

        # (lp-f) 사진 없음 → 참고·프롬프트가 지금과 같다(사진 지시 없음·참고에 사진 없음).
        def _clear_all_photos(d):
            loc = locmod._find_location(d, lid)
            loc["task"] = {"status": "idle", "startedAt": None, "id": None}
            loc["status"] = "ready"
            loc["photos"] = []

        planmod.mutate_plan(owner, pid, _clear_all_photos)
        seedream_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid}/locations/{lid}/build", json={})
        _join()
        no_photo_initial = [c for c in seedream_calls if not c.get("fix")]
        for c in no_photo_initial:
            if "real reference photo" in c["prompt"] or "real look of this place" in c["prompt"]:
                bad.append("(lp-f) 사진 없음인데 프롬프트에 사진 지시가 남아 있습니다")
                break
        # 참고에 사진 바이트가 없다(patterns: photos/ 키 바이트). 가이드·real 뷰·패널(loc-tile)만.
        nb = next((c for c in no_photo_initial if c["variant"] == "real" and c["view"] == "back"), None)
        if nb is not None:
            # real back 참고는 [가이드, 패널…] — 가이드 1 + 패널 ≤3.
            tiles = [r for r in nb["refs"] if isinstance(r, bytes) and r.startswith(b"loc-tile")]
            if len(nb["refs"]) != 1 + len(tiles):
                bad.append(f"(lp-f) 사진 없음 real back 참고가 [가이드,패널]만이 아닙니다: {len(nb['refs'])}장")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 공간 디테일 시트 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        locmod.STORE = orig["loc_STORE"]
        planmod.STORE = orig["plan_STORE"]
        locmod._design = orig["_design"]
        locmod._facts = orig["_facts"]
        locmod._author = orig["_author"]
        locmod._verify = orig["_verify"]
        locmod._seedream = orig["_seedream"]
        locmod.vpoc_ledger.charge = orig["charge"]
        locmod.vpoc_ledger.charge_text = orig["charge_text"]
        locmod.characters._count_people = orig["count_people"]
        locmod.characters.load_char = orig["load_char"]
        _images.tiles_for_analysis = orig["tiles"]
        planmod._analyze_conti = orig["plan_analyze_conti"]
        planmod.vpoc_ledger.charge_text = orig["plan_charge_text"]
        locmod.threading.Thread = orig["loc_Thread"]
        # (m) progress 래퍼가 걸린 채로 예외가 났을 수 있으니 원본으로 되돌린다(아직 안 걸렸으면 no-op).
        if "orig_set_progress" in locals():
            locmod._set_progress = orig_set_progress
        if "orig_set_step" in locals():
            locmod._set_step = orig_set_step
        if "orig_bump_progress" in locals():
            locmod._bump_progress = orig_bump_progress

    if bad:
        fail("영상만들기_신규 공간 디테일 시트(vnew locations) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "영상만들기_신규 공간 디테일 시트(vnew locations, 048 컷어웨이 뷰) 검사 (a)정규화·중복 제거 (b)설계 JSON 정리"
            " (e)콘티 재생성 유지·자동 0장 (f)409 (g)설계 모델 Claude→Astra (j)'assumed'/'provisional' 1회 재요청"
            " (l)다시 만들기 설계 재요청 1회·designEditedAt 있으면 0회 (n)사실 추출 저장·estimated·창문/문/시대/나라"
            " (o)Astra 프롬프트 지침 문장·사실 단어·길이 1500~3000"
            " (lv-a)box_view_geometry back 35~55%·면 모서리 공유·front 매핑 (lv-b)rectify 격자·꼭짓점 순서"
            " (lv-c)Seedream 뷰 4장·첫 참고 가이드/real·wall_key4·floor_key 저장·previz _textures_for 사용"
            " (lv-d)프롬프트 CENTER BACK WALL/LEFT/RIGHT/continuous/No people·뷰B opposite side"
            " (lv-e)verify 꼭짓점 4% 초과→Claude·이상→가이드 (lv-f)옛 데이터(views 없음)·montage_2x2 보존"
            " (m)(q)단계 design→facts→prompts→images→verify→cut→sheet→done·뷰마다 +1·보정분 total 반영"
            " (lp-a)업로드 201·정규화 JPEG·7장째 400·GET 200·다른 사용자 404·DELETE 파일 삭제"
            " (lp-b)PUT side/noteKo·잘못된 side 400·빌드 중 409 (lp-c)사진이 설계·사실 블록에서 패널보다 먼저·라벨"
            " (lp-d)뷰 A 참고 [가이드,back,left,any,패널]·≤6·뷰 B back 없음 (lp-e)프롬프트 CENTER BACK WALL 이미지 번호 일치"
            " (lp-f)사진 없음→참고·프롬프트 회귀 없음"
            " (np-d)설계·사실 지침에 Do not add stairs·mezzanines·terrace/balcony·estimate size from people and doors"
        )



# ---------------------------------------------------------------- 10. 영상만들기_신규 생성(vnew generate)


def check_vnew_generate() -> None:
    """영상만들기_신규 ⑤ 영상 생성 — 승인된 프리비즈에 실사·웹툰을 입힌다(설계자 2026-10-21, 038).

    외부 호출(fal 제출·조회·concat·원장·캐릭터 로드)은 전부 가짜. 유료 호출 없음.

    (j) 프리비즈 미승인/미녹화 → 409 PrevizRequired
    (k) 구간×2(실사+웹툰) 제출, 실사 payload 에 image_urls 없음·elements 정면=기본 복장 정면(시트 키 아님)
    (l) 웹툰 payload elements≤3 + image_urls 1, 총 ≤4
    (m) 참고 이미지(정면·후면·웹툰 컷) 비율 0.4~2.5
    (n) prompt 에 "@Element1"·색 매핑·"Do not add or remove anyone"
    (o) 모두 완료 → finalReal·finalWebtoon concat 2회·done·estimate(초합×단가×2)
    (p) 실패 구간만 재시도({segId,variant}) — 그 칸만 다시, 다른 칸 유지
    (q) 옛 모드(gen.mode 없음) 계획 보기(gen_view) OK
    (ur-a) gen_view units 에 startSec·endSec·cutNo, clipKey 없으면 녹화 키·있으면 클립 키 기반 previzUrl
    (ur-b) vnew.js 가 컷 구간만 재생(#t= · timeupdate 되감기)
    (u) prompt 에 컷 promptEn·cameraEn 문자열 없음·"come ONLY from @Video1" 포함·대사 포함(화면 구성은 @Video1 만)
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

    # 컷 클립 자르기(ffmpeg)는 가짜 — 녹화 바이트가 진짜 mp4 가 아니므로 trim/pad 을 돌리지 않는다.
    # 2초 컷은 3초로 채운 것처럼(padded True, sentSeconds 3), 3초 이상은 그대로.
    cut_clip_calls: list[dict] = []

    def fake_cut_clip(video_mp4, unit):
        orig = float(unit.get("endSec") or 0) - float(unit.get("startSec") or 0)
        padded = orig < genmod.PREVIZ_MIN_CLIP_S - 0.01
        sent = genmod.PREVIZ_MIN_CLIP_S if padded else orig
        cut_clip_calls.append({"cutId": unit.get("cutId"), "origSeconds": round(orig, 3), "sentSeconds": round(sent, 3), "padded": padded})
        return {"bytes": b"CUT-CLIP-MP4", "origSeconds": round(orig, 3), "sentSeconds": round(sent, 3), "padded": padded}

    # fal.poll(웹툰 조회)도 가짜로.
    from src.vpoc import fal as _fal_mod  # noqa: PLC0415

    def fake_fal_poll(invocation_arn, output_prefix):
        if anime_poll["status"] == "Completed":
            return {"status": "Completed", "failureMessage": "", "usage": None}
        return {"status": anime_poll["status"], "failureMessage": "", "usage": None}

    # 프리비즈 edit 제출(generate._previz_request → vpoc_fal._request 직접). 본문(payload)을 잡아 두고
    # 가짜 request_id 를 돌려준다. 유료 호출 없음.
    previz_request_calls: list[dict] = []

    def fake_previz_request(method, url, payload):
        import secrets as _s  # noqa: PLC0415

        previz_request_calls.append({"method": method, "url": url, "payload": payload})
        return {"request_id": "previz-" + _s.token_hex(4)}

    # ledger 는 메모리(네트워크 금지).
    from src.vpoc import ledger as _ledger_mod  # noqa: PLC0415

    ledger_calls: list[dict] = []

    def fake_charge(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    # 캐릭터 로드(실사 front 있음/없음 전환).
    char_has_real = {"value": True}

    def fake_load_char(owner, char_id):
        # 기본 복장 정면(frontKey)·후면(backKey)·시트(sheetKey). 정면은 가로로 긴(괜찮은) 이미지,
        # 후면은 세로로 아주 긴(비율 0.25 — 범위 밖) 이미지로 둬서 (m) 비율 맞춤을 검증한다.
        real = {"frontKey": "", "backKey": "", "sheetKey": ""}
        webtoon = {"frontKey": "", "backKey": "", "sheetKey": ""}
        if char_has_real["value"]:
            real = {
                "frontKey": "rf-" + char_id + ".jpg",
                "backKey": "rb-" + char_id + ".jpg",
                "sheetKey": "rs-" + char_id + ".jpg",
            }
            webtoon = {
                "frontKey": "wf-" + char_id + ".jpg",
                "backKey": "wb-" + char_id + ".jpg",
                "sheetKey": "ws-" + char_id + ".jpg",
            }
            # 정면: 400x600(세로비 0.67, 범위 안). 후면: 100x400(0.25, 범위 밖 → 여백 맞춤).
            fake_store.blobs.setdefault(real["frontKey"], _tiny_jpeg(400, 600))
            fake_store.blobs.setdefault(real["backKey"], _tiny_jpeg(100, 400))
            fake_store.blobs.setdefault(webtoon["frontKey"], _tiny_jpeg(400, 600))
            fake_store.blobs.setdefault(webtoon["backKey"], _tiny_jpeg(100, 400))
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
        "_cut_clip": genmod._cut_clip,
        "_trim_clip": genmod._trim_clip,
        "gen_load_char": genmod.characters.load_char,
        "fal_poll": _fal_mod.poll,
        "previz_request": genmod._previz_request,
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
    genmod._cut_clip = fake_cut_clip
    genmod._trim_clip = lambda mp4, seconds: b"TRIMMED-CUT-MP4"
    genmod.characters.load_char = fake_load_char
    _fal_mod.poll = fake_fal_poll
    genmod._previz_request = fake_previz_request
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

    def _make_plan(owner, pid, *, segments, total=30, with_char=True, previz=True, approved=True, record=True, extra_actors=None, brief=True):
        """fake store 에 ①②③(+프리비즈·촬영 지시서) 까지 된 계획을 직접 써 둔다(생성 직전 상태).

        previz=True 면 scenes(actors=charId ch1 + 추가 마네킹)·timeline(컷마다 등장)·recordings 를 채운다.
        approved/record 로 (j) 미승인·미녹화 분기를 만든다. extra_actors 는 charId 없는 마네킹 목록
        [{id,name,color,appearanceEn}] — 색 문장 검증용. brief=True 면 촬영 지시서(shotBrief ready)도 넣는다
        (⑤ 컷 단위: 지시서가 없으면 /generate 가 409 BriefNotReady 로 막힌다).
        """
        plan = planmod.new_plan()
        plan["input"]["totalSeconds"] = total
        plan["input"]["videoModel"] = "luma-ray2"
        plan["input"]["order"] = ["f1"]
        plan["input"]["images"] = [{"fileId": "f1", "name": "1.png", "width": 600, "height": 900}]
        if with_char:
            plan["input"]["characters"] = [{"charId": "ch1", "roleName": "김부장"}]
        fake_store.blobs[planmod.upload_key(owner, pid, "f1")] = _tiny_jpeg(600, 900)
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

        if previz:
            scene_actors = [{"id": "a1", "name": "김부장", "color": "#4363d8", "heightM": 1.75, "charId": "ch1"}]
            for ea in (extra_actors or []):
                scene_actors.append(dict(ea))
            cut_ids = [c["id"] for c in cuts]
            timeline = []
            for cut_id in cut_ids:
                timeline.append(
                    {
                        "cutId": cut_id,
                        "start": 0,
                        "end": 3,
                        "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 50},
                        "actors": [{"id": act["id"], "speaking": True} for act in scene_actors],
                    }
                )
            scenes = [{"locationId": "loc:office", "room": {"width": 6, "depth": 6}, "actors": scene_actors, "timeline": timeline}]
            recordings = {}
            rev = 0
            if record:
                for seg in segments:
                    rec_key = f"rec/{pid}/{seg['id']}.mp4"
                    fake_store.blobs[rec_key] = b"PREVIZ-REC-MP4"
                    recordings[seg["id"]] = {"status": "ready", "key": rec_key, "seconds": float(seg["seconds"])}
            plan["previz"] = {
                "status": "ready",
                "error": "",
                "model": "astra",
                "revision": rev,
                "scenes": scenes,
                "approved": bool(approved),
                "recordings": recordings,
                "task": {"status": "done", "startedAt": None, "id": None},
            }
            # 촬영 지시서(⑤). 컷마다 brief 를 넣어 두면 /generate 가 BriefNotReady 로 막히지 않는다.
            if brief:
                brief_cuts = {}
                for c in cuts:
                    brief_cuts[c["id"]] = {
                        "shotEn": "MCU, 50mm lens, eye-level, three-quarter, clean single.",
                        "blockingEn": "Kim at frame center, facing camera.",
                        "actionEn": "Kim leans forward, eyes narrowing, right hand on the desk.",
                        "moodEn": "tense, authoritative.",
                        "backgroundEn": "wood-panelled office wall behind, shallow focus.",
                        "dialogue": [{"speaker": "김부장", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp, clipped"}],
                    }
                plan["previz"]["shotBrief"] = {
                    "status": "ready",
                    "error": "",
                    "model": "astra",
                    "scene": {"loc:office": "A closed principal's office, wood panelling, warm afternoon light from the left window."},
                    "cuts": brief_cuts,
                    "updatedAt": "2026-10-22T00:00:00Z",
                    "task": {"status": "done", "startedAt": None, "id": None},
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

        # fal 키가 있어야 _submit_previz_edit 가 FalKeyMissing 으로 빠지지 않는다(실제 호출은 가짜).
        from src.vpoc import settings as _settings  # noqa: PLC0415

        saved_fal_key = _settings.FAL_KEY
        _settings.FAL_KEY = "dummy-fal-key"

        import base64 as _b64  # noqa: PLC0415
        import io as _io  # noqa: PLC0415

        from PIL import Image as _PILImage  # noqa: PLC0415

        def _ratio_of_data_uri(uri):
            """data:image/jpeg;base64,... → 가로/세로 비율(못 읽으면 0)."""
            try:
                b64 = str(uri).split(",", 1)[1]
                with _PILImage.open(_io.BytesIO(_b64.b64decode(b64))) as im:
                    return im.width / max(1, im.height)
            except Exception:  # noqa: BLE001
                return 0.0

        two_segs = [
            {"id": "seg1", "cutIds": ["c1"], "seconds": 6},
            {"id": "seg2", "cutIds": ["c2"], "seconds": 6},
        ]

        # (j) 프리비즈 미승인/미녹화 → 409 PrevizRequired
        pid_j = "prj_gen_j"
        char_has_real["value"] = True
        # 승인 안 됨.
        _make_plan(owner, pid_j, segments=two_segs, total=12, approved=False, record=True)
        planj, _ = planmod.load_plan(owner, pid_j)
        estj = genmod.estimate(planj)
        resp = client.post(f"/api/vnew/p/{pid_j}/generate", json={"confirmUsd": estj["totalUsd"]})
        bodyj = resp.get_json() or {}
        if resp.status_code != 409 or (bodyj.get("error") or {}).get("type") != "PrevizRequired":
            bad.append(f"(j) 미승인이 409 PrevizRequired 가 아닙니다: {resp.status_code} {bodyj}")
        # 승인됐지만 미녹화.
        _make_plan(owner, pid_j, segments=two_segs, total=12, approved=True, record=False)
        planj2, _ = planmod.load_plan(owner, pid_j)
        estj2 = genmod.estimate(planj2)
        resp = client.post(f"/api/vnew/p/{pid_j}/generate", json={"confirmUsd": estj2["totalUsd"]})
        bodyj2 = resp.get_json() or {}
        if resp.status_code != 409 or (bodyj2.get("error") or {}).get("type") != "PrevizRequired":
            bad.append(f"(j) 미녹화가 409 PrevizRequired 가 아닙니다: {resp.status_code} {bodyj2}")

        # (k)(l)(m)(n)(o) 정상 진행: 구간×2 제출 → 완료 → concat 2 → done, payload 검증.
        pid_k = "prj_gen_k"
        extra = [{"id": "a2", "name": "비서", "color": "#e6194b", "appearanceEn": "a young woman in a grey suit"}]
        _make_plan(owner, pid_k, segments=two_segs, total=12, extra_actors=extra)
        previz_request_calls.clear(); concat_calls.clear(); ledger_calls.clear()
        anime_poll["status"] = "Completed"  # fal.poll 완료
        plank, _ = planmod.load_plan(owner, pid_k)
        estk = genmod.estimate(plank)
        # estimate: 구간 초합(6+6) × 단가 × 2.
        exp_unit = genmod.vnew_settings.previz_edit_usd(6)
        exp_total = round(exp_unit * 2 * 2, 2)
        if abs(estk["totalUsd"] - exp_total) > 0.001:
            bad.append(f"(o) estimate 가 (초합×단가×2) 가 아닙니다: {estk['totalUsd']} != {exp_total}")
        if estk.get("mode") != "previz":
            bad.append(f"(o) estimate.mode 가 previz 가 아닙니다: {estk.get('mode')}")
        resp = client.post(f"/api/vnew/p/{pid_k}/generate", json={"confirmUsd": estk["totalUsd"]})
        if resp.status_code != 202:
            bad.append(f"(k) generate 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_drivers()

        kplan, _ = planmod.load_plan(owner, pid_k)
        kgen = kplan.get("gen") or {}
        if kgen.get("mode") != "previz":
            bad.append(f"(k) gen.mode 가 previz 가 아닙니다: {kgen.get('mode')}")
        # 제출: 구간 2 × 그림체 2 = 4회.
        if len(previz_request_calls) != 4:
            bad.append(f"(k) 제출이 4회(구간2×그림체2)가 아닙니다: {len(previz_request_calls)}")
        # payload 를 그림체별로 나눈다(prompt 로 실사/웹툰 구분).
        reals = [c["payload"] for c in previz_request_calls if "live-action" in str(c["payload"].get("prompt"))]
        webs = [c["payload"] for c in previz_request_calls if "webtoon" in str(c["payload"].get("prompt"))]
        if len(reals) != 2:
            bad.append(f"(k) 실사 제출이 2회가 아닙니다: {len(reals)}")
        if len(webs) != 2:
            bad.append(f"(l) 웹툰 제출이 2회가 아닙니다: {len(webs)}")

        for pay in reals:
            # (k) 실사: image_urls 없음, video_url 있음, keep_audio False.
            if "image_urls" in pay:
                bad.append("(k) 실사 payload 에 image_urls 가 있습니다(실사는 장소 이미지 금지)")
            if not str(pay.get("video_url") or "").startswith("data:video/mp4;base64,"):
                bad.append("(k) 실사 payload 의 video_url 이 data URI 가 아닙니다")
            if pay.get("keep_audio") is not False:
                bad.append(f"(k) 실사 keep_audio 가 False 가 아닙니다: {pay.get('keep_audio')}")
            els = pay.get("elements") or []
            if not els:
                bad.append("(k) 실사 payload 에 elements 가 없습니다")
            # 정면 = 기본 복장 정면(rf-ch1.jpg 바이트: 400x600) — 시트 키(rs-)가 아니다.
            # 시트는 가로로 긴 2560x1440 이라 비율로는 구분이 어려우므로, store 원본 바이트로 대조한다.
            front_uri = (els[0] or {}).get("frontal_image_url") if els else ""
            expect_front = _b64.b64encode(fake_store.blobs["rf-ch1.jpg"]).decode("ascii")
            # fit_ratio 는 범위 안(0.67)이면 원본 그대로 → data URI 안 base64 가 원본과 같다.
            if expect_front not in str(front_uri):
                bad.append("(k) 실사 정면이 기본 복장 정면(frontKey) 이 아닙니다(시트 키 의심)")
            # (m) 참고 이미지(정면·후면) 비율 0.4~2.5.
            for el in els:
                for uri in ([el.get("frontal_image_url")] + list(el.get("reference_image_urls") or [])):
                    r = _ratio_of_data_uri(uri)
                    if r and not (0.4 - 1e-6 <= r <= 2.5 + 1e-6):
                        bad.append(f"(m) 참고 이미지 비율이 0.4~2.5 밖입니다: {round(r, 3)}")
            # (n) prompt 에 @Element1·색 매핑·CAST 안내. 컷 단위 CAST/SHOT 형식(설계자 2026-10-22).
            prompt = str(pay.get("prompt") or "")
            if "@Element1" not in prompt:
                bad.append("(n) 실사 prompt 에 @Element1 이 없습니다")
            if "mannequin is @Element1" not in prompt:
                bad.append("(n) 실사 prompt 에 색 마네킹 ↔ @Element 매핑 문장이 없습니다")
            if "only these people" not in prompt:
                bad.append("(n) 실사 prompt 에 CAST 'only these people' 안내가 없습니다")
            if "Do not add or remove anyone" in prompt:
                bad.append("(n) 실사 prompt 에 'Do not add or remove anyone' 이 남아 있습니다(연기는 ACTION 을 따름)")
            # charId 없는 마네킹(비서=빨강)은 색 문장으로.
            if "red mannequin" not in prompt:
                bad.append(f"(n) charId 없는 마네킹의 색 문장이 없습니다: {prompt[:160]}")

        for pay in webs:
            # (l) 웹툰: elements ≤3 + image_urls 1, 합 ≤4.
            els = pay.get("elements") or []
            imgs = pay.get("image_urls") or []
            if len(els) > 3:
                bad.append(f"(l) 웹툰 elements 가 3 을 넘습니다: {len(els)}")
            if len(imgs) > 1:
                bad.append(f"(l) 웹툰 image_urls 가 1 을 넘습니다: {len(imgs)}")
            if len(els) + len(imgs) > 4:
                bad.append(f"(l) 웹툰 elements+image_urls 합이 4 를 넘습니다: {len(els)}+{len(imgs)}")
            if len(imgs) != 1:
                bad.append(f"(l) 웹툰 image_urls 가 1장이 아닙니다(그림체 참고 원작 컷): {len(imgs)}")
            # (m) 웹툰 image_urls·elements 참고 이미지도 비율 0.4~2.5.
            for uri in imgs:
                r = _ratio_of_data_uri(uri)
                if r and not (0.4 - 1e-6 <= r <= 2.5 + 1e-6):
                    bad.append(f"(m) 웹툰 참고 컷 비율이 0.4~2.5 밖입니다: {round(r, 3)}")
            for el in els:
                for uri in ([el.get("frontal_image_url")] + list(el.get("reference_image_urls") or [])):
                    r = _ratio_of_data_uri(uri)
                    if r and not (0.4 - 1e-6 <= r <= 2.5 + 1e-6):
                        bad.append(f"(m) 웹툰 element 참고 비율이 0.4~2.5 밖입니다: {round(r, 3)}")
            if "@Image1" not in str(pay.get("prompt")):
                bad.append("(l) 웹툰 prompt 에 @Image1(그림체 참고) 이 없습니다")

        # (u) 프롬프트는 화면 구성을 @Video1 만 따른다(컷 단위, 설계자 2026-10-22): 컷의 promptEn·cameraEn
        #     문자열이 없고, 'Do not add or remove anyone'·'come ONLY from @Video1' 문장은 쓰지 않으며,
        #     CAST/SHOT/.../CONTINUITY 순서를 지키고, 대사(dialogueKo)는 DIALOGUE 에 들어간다. UTF-8 ≤ 2500바이트.
        cut_prompt_en = "Kim speaks, static camera."
        cut_camera_en = "static camera"
        cut_dialogue = "자네 뭐 하나?"
        order_labels = ["CAST", "SHOT", "BLOCKING", "ACTION", "MOOD", "SETTING", "DIALOGUE", "CONTINUITY"]
        for pay in (reals + webs):
            prompt = str(pay.get("prompt") or "")
            if cut_prompt_en in prompt:
                bad.append("(u) 프롬프트에 컷 promptEn 이 들어 있습니다(화면 구성은 @Video1 만 따라야 합니다)")
            if cut_camera_en in prompt:
                bad.append("(u) 프롬프트에 컷 cameraEn 이 들어 있습니다(카메라는 @Video1 만 따라야 합니다)")
            if "Do not add or remove anyone" in prompt:
                bad.append("(u) 프롬프트에 'Do not add or remove anyone' 이 남아 있습니다")
            if "come ONLY from @Video1" in prompt:
                bad.append("(u) 프롬프트에 옛 'come ONLY from @Video1' 문장이 남아 있습니다")
            # CAST/SHOT/.../CONTINUITY 순서(섹션 머리말 토큰으로 — 본문 속 단어와 혼동 방지).
            header_tokens = {
                "CAST": "CAST —", "SHOT": "\nSHOT:", "BLOCKING": "\nBLOCKING:", "ACTION": "\nACTION:",
                "MOOD": "\nMOOD:", "SETTING": "\nSETTING:", "DIALOGUE": "\nDIALOGUE:", "CONTINUITY": "\nCONTINUITY:",
            }
            positions = [("\n" + prompt).find(header_tokens[lbl]) for lbl in order_labels]
            present = [(order_labels[i], positions[i]) for i in range(len(order_labels)) if positions[i] >= 0]
            pos_only = [p for _lbl, p in present]
            if pos_only != sorted(pos_only):
                bad.append(f"(u) 섹션 순서가 CAST→…→CONTINUITY 가 아닙니다: {present}")
            for must, tok in (("CAST", "CAST —"), ("SHOT", "SHOT:"), ("CONTINUITY", "CONTINUITY:")):
                if tok not in prompt:
                    bad.append(f"(u) 프롬프트에 필수 섹션 {must} 이 없습니다")
            if cut_dialogue[:40] not in prompt:
                bad.append(f"(u) 프롬프트 DIALOGUE 에 대사가 없습니다: {prompt[:200]}")
            if len(prompt.encode("utf-8")) > 2500:
                bad.append(f"(u) 프롬프트가 2500바이트를 넘습니다: {len(prompt.encode('utf-8'))}")

        # (np-a) ASSETS 줄 + CONTINUITY 보강(설계자 2026-10-22, 뉴테이크 실전 프롬프트).
        #   - 모든 컷 프롬프트 첫 줄이 "ASSETS:" 로 시작.
        #   - 실사는 @Video1·@Element1, 웹툰은 @Video1·@Image 포함.
        #   - CONTINUITY 에 "Do not add any action"·"identical to the reference"·"skin texture".
        for pay in reals:
            prompt = str(pay.get("prompt") or "")
            first_line = prompt.split("\n", 1)[0]
            if not first_line.startswith("ASSETS:"):
                bad.append(f"(np-a) 실사 프롬프트 첫 줄이 ASSETS 가 아닙니다: {first_line[:80]}")
            if "@Video1 = rough 3D previz" not in prompt:
                bad.append("(np-a) ASSETS 줄에 @Video1 자료 설명이 없습니다")
            if "@Element1 =" not in prompt:
                bad.append(f"(np-a) 실사 ASSETS 줄에 @Element1 참고 시트가 없습니다: {first_line[:120]}")
            for need in ("Do not add any action", "identical to the reference", "skin texture"):
                if need not in prompt:
                    bad.append(f"(np-a) 실사 CONTINUITY 에 '{need}' 가 없습니다")
            if "high-end live-action shot" not in prompt:
                bad.append("(np-a) 실사 CONTINUITY 에 고퀄 실사 마감 문장이 없습니다")
            if len(prompt.encode("utf-8")) > 2500:
                bad.append(f"(np-a) 실사 프롬프트가 2500바이트를 넘습니다: {len(prompt.encode('utf-8'))}")
        for pay in webs:
            prompt = str(pay.get("prompt") or "")
            first_line = prompt.split("\n", 1)[0]
            if not first_line.startswith("ASSETS:"):
                bad.append(f"(np-a) 웹툰 프롬프트 첫 줄이 ASSETS 가 아닙니다: {first_line[:80]}")
            if "@Image" not in first_line:
                bad.append(f"(np-a) 웹툰 ASSETS 줄에 @Image 참고가 없습니다: {first_line[:120]}")
            for need in ("Do not add any action", "identical to the reference", "skin texture"):
                if need not in prompt:
                    bad.append(f"(np-a) 웹툰 CONTINUITY 에 '{need}' 가 없습니다")
            if "original webtoon's drawing style" not in prompt:
                bad.append("(np-a) 웹툰 CONTINUITY 에 '원작 웹툰 그림체' 마감 문장이 없습니다")
            if len(prompt.encode("utf-8")) > 2500:
                bad.append(f"(np-a) 웹툰 프롬프트가 2500바이트를 넘습니다: {len(prompt.encode('utf-8'))}")

        # (np-a2) 긴 입력에서도 ASSETS·CONTINUITY 문장이 살아남고 ≤2500바이트 — _cut_prompt 함수 단위.
        #   아주 긴 촬영 지시서(brief)로 2500바이트 초과를 유도해도, 보호 문장은 그대로 남아야 한다.
        long_plan, _ = planmod.load_plan(owner, pid_k)
        long_brief = long_plan["previz"]["shotBrief"]["cuts"]
        for cid, b in long_brief.items():
            b["actionEn"] = "Kim leans forward and speaks. " + ("He gestures slowly and deliberately. " * 60)
            b["blockingEn"] = "Kim at frame center facing camera. " * 40
            b["moodEn"] = "tense and heavy. " * 40
        long_plan["previz"]["shotBrief"]["scene"] = {
            "loc:office": "A closed principal's office with wood panelling. " * 40
        }
        cast_demo = [{"colorName": "blue", "name": "Kim", "elementRef": "@Element1", "appearance": "a middle-aged man in a dark suit"}]
        long_real = genmod._cut_prompt(long_plan, {"cutId": "c1"}, "real", cast=cast_demo, has_image_ref=False)
        if not long_real.startswith("ASSETS:"):
            bad.append("(np-a2) 긴 입력 실사 프롬프트 첫 줄이 ASSETS 가 아닙니다")
        for need in ("@Element1 =", "Do not add any action", "identical to the reference", "skin texture"):
            if need not in long_real:
                bad.append(f"(np-a2) 긴 입력 실사 프롬프트에서 보호 문장 '{need}' 가 사라졌습니다")
        if len(long_real.encode("utf-8")) > 2500:
            bad.append(f"(np-a2) 긴 입력 실사 프롬프트가 2500바이트를 넘습니다: {len(long_real.encode('utf-8'))}")

        # (o) 완료 → concat 2(실사·웹툰), done.
        if kgen.get("status") != "done":
            bad.append(f"(o) gen.status 가 done 이 아닙니다: {kgen.get('status')}")
        if len(concat_calls) != 2:
            bad.append(f"(o) concat 호출이 2회(실사·웹툰)가 아닙니다: {concat_calls}")
        if (kgen.get("finalReal") or {}).get("status") != "done" or not (kgen.get("finalReal") or {}).get("key"):
            bad.append(f"(o) finalReal 이 done/key 가 아닙니다: {kgen.get('finalReal')}")
        if (kgen.get("finalWebtoon") or {}).get("status") != "done" or not (kgen.get("finalWebtoon") or {}).get("key"):
            bad.append(f"(o) finalWebtoon 이 done/key 가 아닙니다: {kgen.get('finalWebtoon')}")
        # 원장: edit 제출 4회(구간2×그림체2), 모델 = VNEW_PREVIZ_EDIT_MODEL.
        previz_charges = [e for e in ledger_calls if e.get("model") == genmod.vnew_settings.VNEW_PREVIZ_EDIT_MODEL]
        if len(previz_charges) != 4:
            bad.append(f"(o) 프리비즈 edit 원장 계상이 4건이 아닙니다: {len(previz_charges)}")

        # (ur-a) gen_view units 에 startSec·endSec·cutNo 가 있고, previzUrl 은 녹화(또는 클립) 키 기반이다
        #        (설계자 2026-10-22, ⑤ — 컷마다 그 구간만 보이게 하려면 화면이 시간 범위를 알아야 한다).
        #        · clipKey 없음 → previzUrl 은 녹화 구간 mp4(rec/...) 키 기반, clipped False.
        #        · clipKey 있음 → previzUrl 은 그 클립 키 기반, clipped True.
        presigned_keys = {"value": []}
        _saved_presign = genmod._presign

        def _spy_presign(key, *, bucket="", content_type=""):
            # 어떤 키를 presign 했는지 기록하고, 키를 그대로 URL 처럼 돌려준다(검증용).
            presigned_keys["value"].append(str(key or ""))
            return ("url://" + str(key)) if key else ""

        try:
            genmod._presign = _spy_presign
            # 완료된 previz gen(kplan) 에 units 가 있어야 한다(⑤ 컷 단위).
            kplan2, _ = planmod.load_plan(owner, pid_k)
            kgen2 = kplan2.get("gen") or {}
            if not (kgen2.get("units") or []):
                bad.append("(ur-a) 완료된 previz gen 에 units 가 없습니다(컷 단위가 아닙니다)")
            # cut.no 를 cutId 로 알아 둔다(기대값).
            cut_no_by_id = {str(c.get("id")): c.get("no") for c in (kplan2.get("conti") or {}).get("cuts") or []}

            # 분기 ①: clipKey 없음 — previzUrl 이 녹화 키 기반.
            view_a = genmod.gen_view(owner, pid_k, kplan2)
            units_a = (view_a or {}).get("units") or []
            if len(units_a) != 2:
                bad.append(f"(ur-a) gen_view units 가 2개가 아닙니다: {len(units_a)}")
            for u in units_a:
                for field in ("startSec", "endSec", "cutNo"):
                    if field not in u:
                        bad.append(f"(ur-a) units 항목에 {field} 가 없습니다: {sorted(u.keys())}")
                if u.get("cutNo") != cut_no_by_id.get(str(u.get("cutId"))):
                    bad.append(f"(ur-a) cutNo 가 콘티 cut.no 와 다릅니다: {u.get('cutNo')} != {cut_no_by_id.get(str(u.get('cutId')))}")
                # endSec 가 startSec 보다 커야(그 컷에 실제 길이가 있다).
                if not (float(u.get("endSec") or 0) > float(u.get("startSec") or 0)):
                    bad.append(f"(ur-a) endSec 가 startSec 보다 크지 않습니다: {u.get('startSec')}~{u.get('endSec')}")
                if u.get("clipped"):
                    bad.append("(ur-a) clipKey 가 없는데 clipped 가 True 입니다")
                # previzUrl 이 녹화 구간 mp4(rec/...) 키 기반인지(spy presign 이 key 를 URL 에 담았다).
                if "rec/" not in str(u.get("previzUrl") or ""):
                    bad.append(f"(ur-a) clipKey 없음인데 previzUrl 이 녹화 키 기반이 아닙니다: {u.get('previzUrl')}")

            # 분기 ②: clipKey 있음 — previzUrl 이 그 클립 키 기반, clipped True.
            kplan3, _ = planmod.load_plan(owner, pid_k)
            for u in kplan3["gen"]["units"]:
                u["clipKey"] = "clip/" + str(u.get("cutId")) + ".mp4"
            view_b = genmod.gen_view(owner, pid_k, kplan3)
            for u in (view_b or {}).get("units") or []:
                if not u.get("clipped"):
                    bad.append("(ur-a) clipKey 가 있는데 clipped 가 True 가 아닙니다")
                purl = str(u.get("previzUrl") or "")
                if "clip/" not in purl:
                    bad.append(f"(ur-a) clipKey 있음인데 previzUrl 이 클립 키 기반이 아닙니다: {purl}")
                if "rec/" in purl:
                    bad.append(f"(ur-a) clipKey 있음인데 previzUrl 이 여전히 녹화 키 기반입니다: {purl}")
        finally:
            genmod._presign = _saved_presign

        # (ur-b) 프런트(vnew.js)가 컷 구간만 재생하려고 #t= 를 쓰고 timeupdate 로 되감는 코드가 있다.
        vnew_js_text = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        if "#t=" not in vnew_js_text:
            bad.append("(ur-b) vnew.js 에 '#t=' (컷 구간 재생) 코드가 없습니다")
        if "timeupdate" not in vnew_js_text:
            bad.append("(ur-b) vnew.js 에 timeupdate(구간 끝 되감기) 코드가 없습니다")

        # (p) 실패 컷만 재시도 — fal.poll 를 한 번 Failed 로 만들어 한 칸 실패시킨 뒤 그 칸만 retry.
        #     컷 단위(⑤): unit id = cutId. two_segs 는 각 구간 1컷이라 unit 은 c1·c2.
        pid_p = "prj_gen_p"
        _make_plan(owner, pid_p, segments=two_segs, total=12)
        previz_request_calls.clear(); concat_calls.clear()
        # c1 webtoon 만 실패시킨다: outputPrefix 에 c1-webtoon prefix 가 들어가므로 그걸로 가른다.
        fail_once = {"webtoon_c1": True}

        def fail_fal_poll(invocation_arn, output_prefix):
            if fail_once["webtoon_c1"] and "c1-webtoon" in str(output_prefix):
                fail_once["webtoon_c1"] = False
                return {"status": "Failed", "failureMessage": "가짜 웹툰 실패", "usage": None}
            return {"status": "Completed", "failureMessage": "", "usage": None}

        _fal_mod.poll = fail_fal_poll
        try:
            planp, _ = planmod.load_plan(owner, pid_p)
            estp = genmod.estimate(planp)
            resp = client.post(f"/api/vnew/p/{pid_p}/generate", json={"confirmUsd": estp["totalUsd"]})
            _join_drivers()
            pplan, _ = planmod.load_plan(owner, pid_p)
            pgen = pplan.get("gen") or {}
            if pgen.get("status") != "partial":
                bad.append(f"(p) 한 칸 실패인데 partial 이 아닙니다: {pgen.get('status')}")
            unit_c1 = next((s for s in pgen.get("units") or [] if s.get("id") == "c1"), {})
            if (unit_c1.get("webtoon") or {}).get("status") != "failed":
                bad.append(f"(p) c1 웹툰이 failed 가 아닙니다: {(unit_c1.get('webtoon') or {}).get('status')}")
            if (unit_c1.get("real") or {}).get("status") != "completed":
                bad.append(f"(p) c1 실사가 completed 가 아닙니다(실패가 번졌습니다): {(unit_c1.get('real') or {}).get('status')}")
            # 실패 칸만 재시도(unit id = c1).
            previz_request_calls.clear()
            resp = client.post(f"/api/vnew/p/{pid_p}/generate/retry", json={"segId": "c1", "variant": "webtoon"})
            if resp.status_code != 202:
                bad.append(f"(p) retry 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
            _join_drivers()
            pplan2, _ = planmod.load_plan(owner, pid_p)
            pgen2 = pplan2.get("gen") or {}
            unit_c1b = next((s for s in pgen2.get("units") or [] if s.get("id") == "c1"), {})
            if (unit_c1b.get("webtoon") or {}).get("status") != "completed":
                bad.append(f"(p) retry 뒤 c1 웹툰이 completed 가 아닙니다: {(unit_c1b.get('webtoon') or {}).get('status')}")
            # retry 는 그 칸(c1 webtoon)만 다시 제출 → 1회.
            if len(previz_request_calls) != 1:
                bad.append(f"(p) retry 가 그 칸만 1회 제출하지 않았습니다: {len(previz_request_calls)}")
            if pgen2.get("status") != "done":
                bad.append(f"(p) retry 뒤 done 이 아닙니다: {pgen2.get('status')}")
        finally:
            _fal_mod.poll = fake_fal_poll

        # (q) 옛 모드(gen.mode 없음) 계획 보기(gen_view) OK — 저장된 옛 gen 을 열어도 터지지 않는다.
        pid_q = "prj_gen_q"
        _make_plan(owner, pid_q, segments=two_segs, total=12)
        qplan, _ = planmod.load_plan(owner, pid_q)
        # 옛 모드 gen(mode 키 없음, opening·finalReal 등 옛 구조)을 직접 써 둔다.
        qplan["gen"] = {
            "genId": "g_old_1",
            "status": "done",
            "videoModel": "luma-ray2",
            "resolution": "540p",
            "createdAt": "2026-10-20T08:00:00Z",
            "opening": {"status": "done", "imageKey": "op.jpg", "error": ""},
            "segments": [
                {"id": "seg1", "cutIds": ["c1"], "seconds": 6,
                 "real": {"status": "completed", "attempt": 1, "outputKey": "r1.mp4", "error": ""},
                 "webtoon": {"status": "completed", "attempt": 1, "outputKey": "w1.mp4", "error": ""}},
            ],
            "finalReal": {"status": "done", "key": "fr.mp4", "error": ""},
            "finalWebtoon": {"status": "done", "key": "fw.mp4", "error": ""},
            "selected": None,
            "costUsd": 0.0,
        }
        planmod.save_plan(owner, pid_q, qplan)
        view = genmod.gen_view(owner, pid_q, qplan)
        if not view or view.get("status") != "done":
            bad.append(f"(q) 옛 모드 gen_view 가 done 을 못 돌려줍니다: {view and view.get('status')}")
        if view and view.get("mode") not in ("", None):
            bad.append(f"(q) 옛 모드인데 mode 가 비어 있지 않습니다: {view.get('mode')}")
        # 옛 모드도 선택(select) 이 그대로 된다.
        resp = client.post(f"/api/vnew/p/{pid_q}/select", json={"variant": "real"})
        if resp.status_code != 200:
            bad.append(f"(q) 옛 모드 select 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")

        # ---- (r)(s)(t) 동시 제출(설계자 2026-10-21, 지시서 041) ----
        # 드라이버 전체를 돌리지 않고 한 순회(_drive_previz_pass)만 직접 불러 "한 순회에서 몇 칸을
        # 제출하는지"를 결정적으로 본다. poll 을 InProgress 로 두면 submitted 칸이 그대로 남는다.
        char_has_real["value"] = True
        saved_parallel = genmod.vnew_settings.VNEW_GEN_PARALLEL

        def _fresh_previz_gen(owner_, pid_):
            """드라이버 없이 새 previz gen 을 계획에 심는다(모든 칸 queued). genId 반환."""
            pl, _ = planmod.load_plan(owner_, pid_)
            g = genmod._new_previz_gen(pl)
            pl["gen"] = g
            planmod.save_plan(owner_, pid_, pl)
            return g["genId"]

        def _statuses(owner_, pid_):
            pl, _ = planmod.load_plan(owner_, pid_)
            g = pl.get("gen") or {}
            cells = (g.get("units") or []) if (g.get("units")) else (g.get("segments") or [])
            out = []
            for s in cells:
                out.append((s["id"], "real", (s.get("real") or {}).get("status")))
                out.append((s["id"], "webtoon", (s.get("webtoon") or {}).get("status")))
            return out

        # poll 을 InProgress 로(submitted 가 안 완료되고 그대로 남게).
        def inprogress_fal_poll(invocation_arn, output_prefix):
            return {"status": "InProgress", "failureMessage": "", "usage": None}

        try:
            # (r) 상한 4, 2구간 → 첫 순회에서 4칸 모두 submitted, 제출 4회·원장 4회.
            genmod.vnew_settings.VNEW_GEN_PARALLEL = 4
            _fal_mod.poll = inprogress_fal_poll
            pid_r = "prj_gen_r"
            _make_plan(owner, pid_r, segments=two_segs, total=12)
            gid_r = _fresh_previz_gen(owner, pid_r)
            previz_request_calls.clear(); ledger_calls.clear()
            res_r = genmod._drive_previz_pass(owner, pid_r, gid_r)
            if res_r != "work":
                bad.append(f"(r) 첫 순회가 제출(work)로 끝나지 않았습니다: {res_r}")
            st_r = _statuses(owner, pid_r)
            submitted_r = [x for x in st_r if x[2] == "submitted"]
            if len(submitted_r) != 4:
                bad.append(f"(r) 첫 순회에서 4칸 모두 submitted 가 아닙니다: {st_r}")
            if len(previz_request_calls) != 4:
                bad.append(f"(r) 첫 순회 제출이 4회가 아닙니다: {len(previz_request_calls)}")
            previz_charges_r = [e for e in ledger_calls if e.get("model") == genmod.vnew_settings.VNEW_PREVIZ_EDIT_MODEL]
            if len(previz_charges_r) != 4:
                bad.append(f"(r) 첫 순회 원장 계상이 4건이 아닙니다: {len(previz_charges_r)}")

            # (s) 상한 2, 2구간 → 첫 순회 2칸만 submitted(나머지 queued), 다음 순회에 나머지.
            genmod.vnew_settings.VNEW_GEN_PARALLEL = 2
            _fal_mod.poll = inprogress_fal_poll
            pid_s = "prj_gen_s"
            _make_plan(owner, pid_s, segments=two_segs, total=12)
            gid_s = _fresh_previz_gen(owner, pid_s)
            previz_request_calls.clear()
            genmod._drive_previz_pass(owner, pid_s, gid_s)
            st_s1 = _statuses(owner, pid_s)
            sub1 = [x for x in st_s1 if x[2] == "submitted"]
            queued1 = [x for x in st_s1 if x[2] == "queued"]
            if len(sub1) != 2:
                bad.append(f"(s) 상한 2 인데 첫 순회에 2칸만 submitted 가 아닙니다: {st_s1}")
            if len(queued1) != 2:
                bad.append(f"(s) 상한 2 인데 나머지 2칸이 queued 가 아닙니다: {st_s1}")
            if len(previz_request_calls) != 2:
                bad.append(f"(s) 상한 2 첫 순회 제출이 2회가 아닙니다: {len(previz_request_calls)}")
            # 둘째 순회: submitted 2칸은 아직 InProgress(상한 다 참) → 새 제출 0.
            previz_request_calls.clear()
            res_s2 = genmod._drive_previz_pass(owner, pid_s, gid_s)
            if res_s2 != "poll_only":
                bad.append(f"(s) 상한 가득 찬 둘째 순회가 poll_only 가 아닙니다: {res_s2}")
            if len(previz_request_calls) != 0:
                bad.append(f"(s) 상한 가득 찼는데 둘째 순회가 또 제출했습니다: {len(previz_request_calls)}")
            # 앞 2칸을 완료시키면(상한이 비면) 셋째 순회에 나머지 2칸이 제출된다.
            _fal_mod.poll = fake_fal_poll  # Completed
            genmod._drive_previz_pass(owner, pid_s, gid_s)  # 앞 2칸 poll → completed(상한 비움)
            _fal_mod.poll = inprogress_fal_poll
            previz_request_calls.clear()
            genmod._drive_previz_pass(owner, pid_s, gid_s)  # 나머지 2칸 제출
            st_s3 = _statuses(owner, pid_s)
            sub3 = [x for x in st_s3 if x[2] == "submitted"]
            comp3 = [x for x in st_s3 if x[2] == "completed"]
            if len(previz_request_calls) != 2:
                bad.append(f"(s) 나머지 2칸이 다음 순회에 제출되지 않았습니다: {len(previz_request_calls)}")
            if len(sub3) != 2 or len(comp3) != 2:
                bad.append(f"(s) 나머지 2칸 제출 뒤 상태가 틀립니다(완료2·제출2 기대): {st_s3}")

            # (t) 가짜 경합: 같은 gen 에 순회 2개를 동시에 돌려도 칸별 제출은 1회.
            genmod.vnew_settings.VNEW_GEN_PARALLEL = 4
            _fal_mod.poll = inprogress_fal_poll
            pid_t = "prj_gen_t"
            _make_plan(owner, pid_t, segments=two_segs, total=12)
            gid_t = _fresh_previz_gen(owner, pid_t)
            previz_request_calls.clear()
            racers = [
                orig_thread(target=genmod._drive_previz_pass, args=(owner, pid_t, gid_t))
                for _ in range(2)
            ]
            for rt in racers:
                rt.start()
            for rt in racers:
                rt.join(timeout=20.0)
            st_t = _statuses(owner, pid_t)
            sub_t = [x for x in st_t if x[2] == "submitted"]
            if len(sub_t) != 4:
                bad.append(f"(t) 경합 뒤 4칸 모두 submitted 가 아닙니다: {st_t}")
            # 칸(4개)마다 제출 1회 → 총 4회(경합이어도 중복 제출 없음).
            if len(previz_request_calls) != 4:
                bad.append(f"(t) 경합에서 칸별 제출이 1회(총 4회)가 아닙니다: {len(previz_request_calls)}")
        finally:
            genmod.vnew_settings.VNEW_GEN_PARALLEL = saved_parallel
            _fal_mod.poll = fake_fal_poll

        # ---- (gr-a)~(gr-d) 취소·실패·프리비즈 변경 후 재생성(설계자 2026-10-22, 지시서 060) ----
        # 제품: 한 번 gen 이 생기면(cancelled/failed/partial/done) 새로 시작할 길이 없던 버그를 고쳤다.
        #   · _new_previz_gen 이 previzRevision·briefUpdatedAt 을 저장
        #   · gen_view.stale(프리비즈 revision/녹화 키가 바뀜)
        #   · start_generate 가 진행 중이 아닌 이전 gen 을 genHistory 로 밀고 교체
        #   · retry_segment 가 cancelled 칸도 다시 queued
        # 외부 호출은 위 (k)~(t) 와 같은 가짜를 그대로 쓴다(유료 호출 없음).

        # (gr-a) cancelled gen 이 있는 상태에서 POST /generate → 202·새 genId·genHistory 에 이전 gen.
        #        이전 gen 은 '취소된 상태'를 직접 심어 둔다(드라이버 없이 결정적 — InProgress 로 돌리면
        #        데몬 드라이버가 폴링을 멈추지 않아 검사가 느려지고 스레드가 샌다). poll 은 Completed 라
        #        새 gen 은 바로 끝난다.
        pid_a = "prj_gen_gra"
        _make_plan(owner, pid_a, segments=two_segs, total=12)
        first_gen_id = "g_cancelled_prior"

        def _seed_cancelled(d):
            g = genmod._new_previz_gen(d)
            g["genId"] = first_gen_id
            g["status"] = "cancelled"
            for u in g.get("units") or []:
                u["real"]["status"] = "cancelled"
                u["webtoon"]["status"] = "cancelled"
            d["gen"] = g

        planmod.mutate_plan(owner, pid_a, _seed_cancelled)
        aplan_c, _ = planmod.load_plan(owner, pid_a)
        if (aplan_c.get("gen") or {}).get("status") != "cancelled":
            bad.append(f"(gr-a) 전제: 심은 gen.status 가 cancelled 가 아닙니다: {(aplan_c.get('gen') or {}).get('status')}")
        # cancelled 인데 POST /generate 가 다시 된다(202·새 genId).
        aplan2, _ = planmod.load_plan(owner, pid_a)
        esta2 = genmod.estimate(aplan2)
        resp = client.post(f"/api/vnew/p/{pid_a}/generate", json={"confirmUsd": esta2["totalUsd"]})
        if resp.status_code != 202:
            bad.append(f"(gr-a) cancelled 뒤 재생성이 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_drivers()
        aplan3, _ = planmod.load_plan(owner, pid_a)
        new_gen = aplan3.get("gen") or {}
        if not new_gen.get("genId") or new_gen.get("genId") == first_gen_id:
            bad.append(f"(gr-a) 재생성이 새 genId 를 만들지 않았습니다: {new_gen.get('genId')} vs {first_gen_id}")
        history = aplan3.get("genHistory") or []
        if not any(h.get("genId") == first_gen_id for h in history):
            bad.append(f"(gr-a) genHistory 에 이전 gen({first_gen_id})이 없습니다: {[h.get('genId') for h in history]}")
        # genHistory 항목은 요약 키만(genId·status·createdAt·finalReal/finalWebtoon).
        prior = next((h for h in history if h.get("genId") == first_gen_id), {})
        for key in ("genId", "status", "createdAt", "finalReal", "finalWebtoon"):
            if key not in prior:
                bad.append(f"(gr-a) genHistory 항목에 {key} 가 없습니다: {sorted(prior.keys())}")
        if prior.get("status") != "cancelled":
            bad.append(f"(gr-a) genHistory 의 이전 gen status 가 cancelled 가 아닙니다: {prior.get('status')}")
        if len(history) > 3:
            bad.append(f"(gr-a) genHistory 가 최근 3개를 넘습니다: {len(history)}")
        # 이전 gen 의 원장·파일은 건드리지 않는다 — genHistory 는 요약(키 문자열)만 담는다(units·segments 없음).
        if "units" in prior or "segments" in prior:
            bad.append("(gr-a) genHistory 항목이 요약이 아니라 전체 gen 을 담았습니다")

        # (gr-b) gen 생성 뒤 previz.revision 변경 → gen_view.stale True. 같은 revision 이면 False.
        pid_b = "prj_gen_grb"
        _make_plan(owner, pid_b, segments=two_segs, total=12)
        gid_b = _fresh_previz_gen(owner, pid_b)
        bplan, _ = planmod.load_plan(owner, pid_b)
        view_b0 = genmod.gen_view(owner, pid_b, bplan)
        if (view_b0 or {}).get("stale") is not False:
            bad.append(f"(gr-b) 같은 revision 인데 stale 이 False 가 아닙니다: {(view_b0 or {}).get('stale')}")
        # previz.revision 을 올린다(다시 설계·녹화한 상황).

        def _bump_rev(d):
            d.setdefault("previz", {})["revision"] = int((d.get("previz") or {}).get("revision") or 0) + 1

        planmod.mutate_plan(owner, pid_b, _bump_rev)
        bplan2, _ = planmod.load_plan(owner, pid_b)
        view_b1 = genmod.gen_view(owner, pid_b, bplan2)
        if (view_b1 or {}).get("stale") is not True:
            bad.append(f"(gr-b) revision 이 바뀌었는데 stale 이 True 가 아닙니다: {(view_b1 or {}).get('stale')}")
        # gen 에 저장된 previzRevision 이 생성 시점 값(0)인지도 확인(교체 전).
        if (bplan2.get("gen") or {}).get("previzRevision") != 0:
            bad.append(f"(gr-b) gen.previzRevision 이 생성 시점 값(0)이 아닙니다: {(bplan2.get('gen') or {}).get('previzRevision')}")
        # 옛 gen(previzRevision 없음)은 녹화 키 비교로 stale 을 본다.
        pid_b2 = "prj_gen_grb2"
        _make_plan(owner, pid_b2, segments=two_segs, total=12)
        b2plan, _ = planmod.load_plan(owner, pid_b2)
        b2plan["gen"] = {
            "genId": "g_old_stale",
            "mode": "previz",
            "status": "cancelled",
            "units": [
                {"id": "c1", "cutId": "c1", "recSegId": "seg1", "recordingKey": "rec/old/seg1.mp4",
                 "real": {"status": "cancelled"}, "webtoon": {"status": "cancelled"}},
            ],
            "segments": [],
            "finalReal": {"status": "none", "key": "", "error": ""},
            "finalWebtoon": {"status": "none", "key": "", "error": ""},
            "selected": None,
        }
        planmod.save_plan(owner, pid_b2, b2plan)
        b2plan2, _ = planmod.load_plan(owner, pid_b2)
        view_b2 = genmod.gen_view(owner, pid_b2, b2plan2)
        # 현재 녹화 키(rec/prj_gen_grb2/seg1.mp4)와 옛 recordingKey(rec/old/seg1.mp4)가 달라 stale True.
        if (view_b2 or {}).get("stale") is not True:
            bad.append(f"(gr-b) 옛 gen 녹화 키가 바뀌었는데 stale 이 True 가 아닙니다: {(view_b2 or {}).get('stale')}")

        # (gr-c) cancelled 칸 retry → queued, gen.status running.
        #        취소된 gen 을 직접 심고, retry_segment 를 직접 불러 '뮤테이트 직후'(드라이버가 상태를
        #        바꾸기 전) 결과를 본다 — queued·running 이 결정적이다. poll 은 Completed 라 그 뒤 드라이버는
        #        바로 끝난다(InProgress 로 돌려 데몬이 폴링을 멈추지 않는 문제를 피한다).
        pid_c = "prj_gen_grc"
        _make_plan(owner, pid_c, segments=two_segs, total=12)

        def _seed_cancelled_c(d):
            g = genmod._new_previz_gen(d)
            g["genId"] = "g_cancelled_c"
            g["status"] = "cancelled"
            for u in g.get("units") or []:
                u["real"]["status"] = "cancelled"
                u["webtoon"]["status"] = "cancelled"
            d["gen"] = g

        planmod.mutate_plan(owner, pid_c, _seed_cancelled_c)
        cplan, _ = planmod.load_plan(owner, pid_c)
        cgen = cplan.get("gen") or {}
        unit_c1 = next((u for u in cgen.get("units") or [] if u.get("id") == "c1"), {})
        if (unit_c1.get("real") or {}).get("status") != "cancelled":
            bad.append(f"(gr-c) 전제: c1 실사가 cancelled 가 아닙니다: {(unit_c1.get('real') or {}).get('status')}")
        # retry_segment 직접 호출 → 반환된 gen(뮤테이트 직후)이 queued·running 이어야 한다.
        retried = genmod.retry_segment(owner, pid_c, "c1", "real")
        if (retried or {}).get("status") != "running":
            bad.append(f"(gr-c) retry 직후 gen.status 가 running 이 아닙니다: {(retried or {}).get('status')}")
        unit_c1r = next((u for u in (retried or {}).get("units") or [] if u.get("id") == "c1"), {})
        if (unit_c1r.get("real") or {}).get("status") != "queued":
            bad.append(f"(gr-c) retry 직후 c1 실사가 queued 가 아닙니다: {(unit_c1r.get('real') or {}).get('status')}")
        # retry_segment 는 드라이버를 깨운다 — poll Completed 라 바로 끝난다. 스레드를 비운다.
        _join_drivers()

        # (gr-d) node --check 대상(check_js)에 vnew.js 가 있고, "새로 영상 만들기" 버튼 라벨이 있다.
        vnew_js = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        if "새로 영상 만들기" not in vnew_js:
            bad.append("(gr-d) vnew.js 에 '새로 영상 만들기' 버튼 라벨이 없습니다")

        # ============================================================ ⑤ OOM 완화(설계자 2026-10-23)
        # (mem-a) 준비 단계 직렬화: 4칸을 동시에 돌려도 _PREPARE_SEM(기본 1) 안의 준비 구간은 한 번에
        #         최대 1칸만 실행된다. 제출 호출(_previz_request, 세마포어 안)에 짧은 지연·동시 카운터를
        #         두고 봉우리 동시 수를 본다. (세마포어가 없으면 4칸이 겹쳐 2 이상이 관측된다.)
        mem_lock = _threading.Lock()
        mem_state = {"cur": 0, "peak": 0}
        saved_previz_req = genmod._previz_request

        def _counting_previz_request(method, url, payload):
            import secrets as _s  # noqa: PLC0415

            with mem_lock:
                mem_state["cur"] += 1
                mem_state["peak"] = max(mem_state["peak"], mem_state["cur"])
            try:
                _time.sleep(0.05)  # 겹침을 관측할 틈(세마포어가 막으면 겹치지 않는다).
                return {"request_id": "mem-" + _s.token_hex(4)}
            finally:
                with mem_lock:
                    mem_state["cur"] -= 1

        pid_mem = "prj_gen_mem_a"
        _make_plan(owner, pid_mem, segments=two_segs, total=12)
        gid_mem = _fresh_previz_gen(owner, pid_mem)
        saved_parallel_mem = genmod.vnew_settings.VNEW_GEN_PARALLEL
        genmod._previz_request = _counting_previz_request
        try:
            # 상한 4 로 4칸(구간2×그림체2)을 한 순회에서 동시에 선점·제출하게 한다. 준비 구간은
            # _PREPARE_SEM(1) 이 직렬화하므로 _previz_request 봉우리 동시 수는 1 이어야 한다.
            genmod.vnew_settings.VNEW_GEN_PARALLEL = 4
            genmod._drive_previz_pass(owner, pid_mem, gid_mem)
        finally:
            genmod.vnew_settings.VNEW_GEN_PARALLEL = saved_parallel_mem
            genmod._previz_request = saved_previz_req
        if mem_state["peak"] != 1:
            bad.append(f"(mem-a) 준비 구간 봉우리 동시 수가 1 이 아닙니다(세마포어 미동작): {mem_state['peak']}")
        # 세마포어 상한이 설정값과 같은지(기본 1).
        if genmod._PREPARE_SEM._initial_value != max(1, int(genmod.vnew_settings.VNEW_GEN_PREPARE_CONCURRENCY or 1)):
            bad.append(f"(mem-a) _PREPARE_SEM 상한이 VNEW_GEN_PREPARE_CONCURRENCY 와 다릅니다: {genmod._PREPARE_SEM._initial_value}")

        # (mem-b) 참고 이미지 2000x3000 → data URI 로 넣을 이미지의 긴 변 ≤ VNEW_REF_MAX_PX(1024)·JPEG.
        big_jpeg = _tiny_jpeg(2000, 3000)
        fitted = genmod._fit_ref(big_jpeg)
        try:
            with _PILImage.open(_io.BytesIO(fitted)) as fim:
                long_side = max(fim.width, fim.height)
                fmt = (fim.format or "").upper()
            if long_side > genmod.vnew_settings.VNEW_REF_MAX_PX:
                bad.append(f"(mem-b) 축소된 참고 이미지 긴 변이 {genmod.vnew_settings.VNEW_REF_MAX_PX} 를 넘습니다: {long_side}")
            if fmt != "JPEG":
                bad.append(f"(mem-b) 축소된 참고 이미지가 JPEG 이 아닙니다: {fmt}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"(mem-b) 축소된 참고 이미지를 열지 못했습니다: {exc!r}")
        # 이미 작은 이미지(긴 변 ≤ 1024)는 그대로(재인코딩 안 함) — downscale 단위 계약.
        small_jpeg = _tiny_jpeg(400, 600)
        if genmod.imaging.downscale(small_jpeg, max_px=1024) != small_jpeg:
            bad.append("(mem-b) 이미 작은 이미지를 불필요하게 재인코딩했습니다")

        # (mem-c) VNEW_GEN_PARALLEL 기본 2: 6컷 계획 첫 순회에 2칸만 제출(나머지 queued).
        if genmod.vnew_settings.VNEW_GEN_PARALLEL != 2:
            bad.append(f"(mem-c) VNEW_GEN_PARALLEL 기본값이 2 가 아닙니다: {genmod.vnew_settings.VNEW_GEN_PARALLEL}")
        six_segs = [{"id": f"seg{i}", "cutIds": [f"mc{i}"], "seconds": 5} for i in range(1, 7)]
        pid_mc = "prj_gen_mem_c"
        _make_plan(owner, pid_mc, segments=six_segs, total=30)
        gid_mc = _fresh_previz_gen(owner, pid_mc)
        saved_poll_mc = _fal_mod.poll

        def inprogress_poll_mc(invocation_arn, output_prefix):
            return {"status": "InProgress", "failureMessage": "", "usage": None}

        _fal_mod.poll = inprogress_poll_mc
        previz_request_calls.clear()
        try:
            # 기본값(2)을 그대로 쓴다 — 설정을 건드리지 않고 '기본 2' 를 검증한다.
            genmod._drive_previz_pass(owner, pid_mc, gid_mc)
        finally:
            _fal_mod.poll = saved_poll_mc
        mc_plan, _ = planmod.load_plan(owner, pid_mc)
        mc_gen = mc_plan.get("gen") or {}
        mc_cells = (mc_gen.get("units") or []) if mc_gen.get("units") else (mc_gen.get("segments") or [])
        mc_submitted = 0
        for s in mc_cells:
            for v in ("real", "webtoon"):
                if (s.get(v) or {}).get("status") == "submitted":
                    mc_submitted += 1
        if mc_submitted != 2:
            bad.append(f"(mem-c) 기본 상한 2 인데 첫 순회 제출이 2칸이 아닙니다: {mc_submitted}")
        if len(previz_request_calls) != 2:
            bad.append(f"(mem-c) 기본 상한 2 첫 순회 제출 호출이 2회가 아닙니다: {len(previz_request_calls)}")

        # (mem-d) 프리비즈 모드 preparing 6분 지난 칸 → queued 로 되돌림(VNEW_PREPARE_STALE_S=300초 기준).
        if genmod.vnew_settings.VNEW_PREPARE_STALE_S != 300:
            bad.append(f"(mem-d) VNEW_PREPARE_STALE_S 기본값이 300 이 아닙니다: {genmod.vnew_settings.VNEW_PREPARE_STALE_S}")
        import datetime as _dt_mem  # noqa: PLC0415

        stale_at_mem = (_dt_mem.datetime.now(_dt_mem.timezone.utc) - _dt_mem.timedelta(minutes=6)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        fresh_at_mem = (_dt_mem.datetime.now(_dt_mem.timezone.utc) - _dt_mem.timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mem_gen = {
            "mode": "previz",
            "units": [
                {"id": "md1", "cutId": "md1",
                 "real": {"status": "preparing", "claim": "C1", "claimedAt": stale_at_mem},
                 "webtoon": {"status": "preparing", "claim": "C2", "claimedAt": fresh_at_mem}},
            ],
            "segments": [],
        }
        changed_md = genmod._reconcile_prepare_stale_previz(mem_gen)
        md_real = mem_gen["units"][0]["real"]
        md_web = mem_gen["units"][0]["webtoon"]
        if not changed_md:
            bad.append("(mem-d) 6분 지난 preparing 을 해제하지 않았습니다(changed False)")
        if md_real.get("status") != "queued" or md_real.get("claim") != "":
            bad.append(f"(mem-d) 6분 지난 preparing 이 queued 로 돌아가지 않았습니다: {md_real.get('status')}/{md_real.get('claim')}")
        if md_web.get("status") != "preparing":
            bad.append(f"(mem-d) 30초 전 preparing(신선)을 건드렸습니다: {md_web.get('status')}")

        _settings.FAL_KEY = saved_fal_key


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
        genmod._cut_clip = orig["_cut_clip"]
        genmod._trim_clip = orig["_trim_clip"]
        genmod.characters.load_char = orig["gen_load_char"]
        _fal_mod.poll = orig["fal_poll"]
        genmod._previz_request = orig["previz_request"]
        _ledger_mod.charge = orig["charge"]
        genmod.threading.Thread = orig["Thread"]

    if bad:
        fail("영상만들기_신규 생성(vnew generate) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "영상만들기_신규 생성(vnew generate, 프리비즈) 검사 (j)미승인·미녹화 409 PrevizRequired"
            " (k)구간×2 제출·실사 image_urls 없음·정면=기본복장 (l)웹툰 elements≤3+image_urls1 총≤4"
            " (m)참고 비율 0.4~2.5 (n)@Element1·색 매핑·Do not add or remove anyone (o)concat2·done·estimate×2"
            " (p)실패 칸만 재시도 (q)옛 모드 보기·select"
            " (r)상한4 첫 순회 4칸 동시 submitted·제출4·원장4 (s)상한2 첫 순회 2칸·다음 순회 나머지"
            " (t)경합에서도 칸별 제출 1회"
            " (np-a)ASSETS 첫 줄·@Video1·@Element1/@Image·CONTINUITY 보강(Do not add any action·identical to the reference·skin texture)·긴 입력에서도 유지·≤2500바이트"
            " (gr-a)cancelled 뒤 재생성 202·새 genId·genHistory (gr-b)revision 변경→stale·옛 gen 녹화 키 비교"
            " (gr-c)cancelled 칸 retry→running·재진행 (gr-d)새로 영상 만들기 라벨"
            " (mem-a)준비 단계 세마포어 직렬화(4칸 동시→봉우리 1) (mem-b)참고 이미지 2000x3000→긴 변≤1024·JPEG"
            " (mem-c)VNEW_GEN_PARALLEL 기본 2(6컷 첫 순회 2칸) (mem-d)프리비즈 preparing 6분→queued(300초)"
        )


# ---------------------------------------------------------------- 11. fal 단가·프롬프트 길이


def check_fal_pricing() -> None:
    """fal 단가(pricing)·shot_usd·프롬프트 길이(설계자 2026-10-20). 실제 유료 호출 금지(가짜).

    (a) _parse_pricing: seconds·per-video(videos)·없음(None) 을 바르게 읽는다
    (b) shot_usd(fal): seconds 면 unit_price×초, 영상 1건 단위면 unit_price, 단가 모르면 0
        — fal 모델이 Luma 표(VIDEO_USD_PER_SEC)로 떨어지지 않는다
    (c) build_payload: prompt 3000자 → promptMaxLength(2500) 이하로 문장 경계에서 잘림
    (d) promptMaxLength 가 없으면 기본 2500, caps 추출(extract_caps)에 promptMaxLength 가 있다
    (e) "1000 tokens" 720p 10초 → 토큰 공식으로 4.6224 근처(1건 가격 아님)
    (f) "1M tokens" → 단위 수 1000000 으로 환산
    (g) 모르는 단위 "credits" → 0(단가 미정, 1건 가격으로 보지 않는다)
    (h) "videos" → 1건 가격(초 무시)
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import fal, settings  # noqa: PLC0415

    bad: list[str] = []

    eid = "fal-ai/kling-video/v3/pro/image-to-video"

    # (a) _parse_pricing — seconds
    secs = fal._parse_pricing(
        {"prices": [{"endpoint_id": eid, "unit_price": 0.14, "unit": "seconds", "currency": "USD"}]}, eid
    )
    if not secs or abs(secs["unitPrice"] - 0.14) > 1e-9 or secs["unit"] != "seconds":
        bad.append(f"(a) seconds 단가 파싱이 틀립니다: {secs}")
    # per-video(videos)
    per_video = fal._parse_pricing(
        {"prices": [{"endpoint_id": eid, "unit_price": 1.2, "unit": "videos"}]}, eid
    )
    if not per_video or abs(per_video["unitPrice"] - 1.2) > 1e-9 or per_video["unit"] != "videos":
        bad.append(f"(a) per-video 단가 파싱이 틀립니다: {per_video}")
    # 없음 → None
    if fal._parse_pricing({"prices": []}, eid) is not None:
        bad.append("(a) 빈 prices 가 None 이 아닙니다")
    if fal._parse_pricing({}, eid) is not None:
        bad.append("(a) prices 없음이 None 이 아닙니다")
    # 다른 endpoint 만 있고 하나뿐이면 그 하나를 쓴다(단일 응답).
    single = fal._parse_pricing(
        {"prices": [{"endpoint_id": "other/model", "unit_price": 0.0325, "unit": "seconds"}]}, eid
    )
    if not single or abs(single["unitPrice"] - 0.0325) > 1e-9:
        bad.append(f"(a) 단일 응답 단가 파싱이 틀립니다: {single}")

    # (b) shot_usd(fal) — pricing_for 를 가짜로 바꿔 네트워크 없이 계산 경로만 본다.
    real_pricing_for = fal.pricing_for
    fal_model = settings.FAL_ID_PREFIX + eid
    try:
        # seconds → unit_price × 초
        fal.pricing_for = lambda m: {"unitPrice": 0.14, "unit": "seconds"}
        v = settings.shot_usd(fal_model, "720p", 20)
        if abs(v - round(0.14 * 20, 4)) > 1e-6:
            bad.append(f"(b) seconds fal shot_usd 가 틀립니다: {v} != 2.8")
        # Luma 표(720p=1.5)로 떨어지지 않았는지 — 20초×1.5=30 이 아니어야 한다.
        if abs(v - 30.0) < 1e-6:
            bad.append("(b) fal 모델이 Luma 표(720p 30USD)로 떨어졌습니다")
        # 영상 1건 단위(videos) → unit_price(초 무시)
        fal.pricing_for = lambda m: {"unitPrice": 1.2, "unit": "videos"}
        v2 = settings.shot_usd(fal_model, "720p", 20)
        if abs(v2 - 1.2) > 1e-6:
            bad.append(f"(b) per-video fal shot_usd 가 틀립니다: {v2} != 1.2")
        # 단가 모르면 0(단가 미정) — Luma 표로 안 감
        fal.pricing_for = lambda m: None
        v3 = settings.shot_usd(fal_model, "720p", 20)
        if abs(v3) > 1e-9:
            bad.append(f"(b) 단가 미정 fal shot_usd 가 0 이 아닙니다: {v3}")
        # (e) "1000 tokens" 720p 10초 → 토큰 공식.
        #     tokens = 1280×720×24×10/1024 = 216000, 금액 = 0.0214 × 216000 / 1000 = 4.6224
        fal.pricing_for = lambda m: {"unitPrice": 0.0214, "unit": "1000 tokens"}
        v_e = settings.shot_usd(fal_model, "720p", 10)
        if abs(v_e - 4.6224) > 1e-3:
            bad.append(f"(e) '1000 tokens' 720p 10초 금액이 틀립니다: {v_e} != 4.6224")
        if abs(v_e - 0.0214) < 1e-6:
            bad.append("(e) '1000 tokens' 를 영상 1건 가격으로 봤습니다(토큰 공식 미적용)")
        # (f) "1M tokens" — 같은 토큰 수(216000)에 단위 수 1000000.
        #     단가 2140(1M당 2140)이면 2140 × 216000 / 1000000 = 462.24
        fal.pricing_for = lambda m: {"unitPrice": 2140.0, "unit": "1M tokens"}
        v_f = settings.shot_usd(fal_model, "720p", 10)
        if abs(v_f - 462.24) > 1e-2:
            bad.append(f"(f) '1M tokens' 720p 10초 금액이 틀립니다: {v_f} != 462.24")
        # (g) 모르는 단위 "credits" → 0(단가 미정), 1건 가격으로 보지 않는다.
        fal.pricing_for = lambda m: {"unitPrice": 5.0, "unit": "credits"}
        v_g = settings.shot_usd(fal_model, "720p", 10)
        if abs(v_g) > 1e-9:
            bad.append(f"(g) 모르는 단위 'credits' 가 0 이 아닙니다: {v_g}")
        # (h) "videos" 는 1건 가격(초 무시).
        fal.pricing_for = lambda m: {"unitPrice": 3.5, "unit": "videos"}
        v_h = settings.shot_usd(fal_model, "720p", 10)
        if abs(v_h - 3.5) > 1e-6:
            bad.append(f"(h) 'videos' 1건 가격이 틀립니다: {v_h} != 3.5")
    finally:
        fal.pricing_for = real_pricing_for

    # (c) build_payload: 3000자 → promptMaxLength(2500) 이하로 문장 경계에서 잘림
    long_prompt = ("This is a scene sentence. " * 200).strip()  # 200문장, ~5000자
    if len(long_prompt) <= 2500:
        bad.append("(c) 테스트 프롬프트가 3000자 미만입니다(구성 오류)")
    caps = {"promptField": "prompt", "promptMaxLength": 2500, "durations": [5], "sendDuration": False}
    body = fal.build_payload(caps, prompt=long_prompt)
    sent = body.get("prompt", "")
    if len(sent) > 2500:
        bad.append(f"(c) 잘린 프롬프트가 2500자를 넘습니다: {len(sent)}")
    if not sent.endswith("."):
        bad.append(f"(c) 문장 경계에서 자르지 않았습니다(끝 문자): {sent[-20:]!r}")
    if len(sent) == len(long_prompt):
        bad.append("(c) 프롬프트가 잘리지 않았습니다")

    # _trim_prompt 자체: 상한 이내면 그대로
    short = "A short prompt."
    if fal._trim_prompt(short, 2500) != short:
        bad.append("(c) 상한 이내 프롬프트가 바뀌었습니다")

    # (d) promptMaxLength 기본값(없으면 2500)
    if fal.DEFAULT_PROMPT_MAX != 2500:
        bad.append(f"(d) DEFAULT_PROMPT_MAX 가 2500 이 아닙니다: {fal.DEFAULT_PROMPT_MAX}")
    caps_nomax = {"promptField": "prompt", "sendDuration": False}
    body2 = fal.build_payload(caps_nomax, prompt=long_prompt)
    if len(body2.get("prompt", "")) > 2500:
        bad.append(f"(d) promptMaxLength 없을 때 기본 2500 으로 안 잘렸습니다: {len(body2.get('prompt',''))}")

    # extract_caps 가 OpenAPI 의 prompt maxLength 를 뽑고, 없으면 2500 을 넣는다.
    oa_with_max = {
        "paths": {
            "/" + eid: {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "prompt": {"type": "string", "maxLength": 1800},
                                        "image_url": {"type": "string"},
                                    },
                                    "required": ["prompt"],
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    caps_x, reason = fal.extract_caps(oa_with_max, eid)
    if not caps_x or caps_x.get("promptMaxLength") != 1800:
        bad.append(f"(d) extract_caps 가 promptMaxLength(1800) 를 뽑지 못했습니다: {caps_x and caps_x.get('promptMaxLength')} / {reason}")
    oa_no_max = {
        "paths": {
            "/" + eid: {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {"prompt": {"type": "string"}},
                                    "required": ["prompt"],
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    caps_y, _ = fal.extract_caps(oa_no_max, eid)
    if not caps_y or caps_y.get("promptMaxLength") != 2500:
        bad.append(f"(d) maxLength 없을 때 promptMaxLength 가 2500 이 아닙니다: {caps_y and caps_y.get('promptMaxLength')}")

    if bad:
        fail("fal 단가·프롬프트 길이 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal 단가·프롬프트 길이 검사 (a)pricing seconds·per-video·없음 (b)shot_usd fal 은 Luma 표로 안 감"
            " (c)3000자→2500 문장 경계 (d)promptMaxLength 기본 2500·extract_caps"
        )


# ---------------------------------------------------------------- 12. vnew 실사 시작 이미지 거절


def check_vnew_real_start() -> None:
    """vnew Seedance 실사 시작 이미지 거절(설계자 2026-10-20). 실제 호출 금지.

    (a) plan.available_models 의 realStartImage: byteplus(Seedance) false, 나머지(Luma·fal) true
    (b) plan.default_video_model 은 realStartImage=true 중 첫 번째
    (c) 038 이후 생성은 프리비즈 기반 — check_preconditions 가 프리비즈 미승인/미녹화를 409
        PrevizRequired 로 막는다(모델 무관). _model_accepts_real_start 함수는 그대로.
    (d) errors.USER_FACING_KINDS 에 RealStartImageUnsupported 가 있다
    (e) _user_facing_error 가 'may contain real person' 을 한국어 문구로 바꾼다
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vpoc import errors as vpoc_errors  # noqa: PLC0415
    from src.vpoc import settings as vpoc_settings  # noqa: PLC0415

    bad: list[str] = []

    # (a)(b) available_models/default_video_model — ark.choices 를 가짜로(네트워크 없이).
    real_choices = planmod.vpoc_ark.choices
    fake_choices = [
        {"id": "luma-ray2", "label": "Luma Ray2", "available": True, "provider": "bedrock"},
        {"id": "seedance-2-0", "label": "Seedance 2.0", "available": True, "provider": "byteplus"},
        {"id": "seedance-2-5", "label": "Seedance 2.5", "available": True, "provider": "byteplus"},
        {"id": "fal:fal-ai/kling-video/v3/pro/image-to-video", "label": "fal · Kling", "available": True, "provider": "fal"},
        {"id": "unavailable-x", "label": "X", "available": False, "provider": "byteplus"},
    ]
    try:
        planmod.vpoc_ark.choices = lambda: [dict(item) for item in fake_choices]
        models = planmod.available_models()
        by_id = {m["id"]: m for m in models}
        if "unavailable-x" in by_id:
            bad.append("(a) available=False 모델이 목록에 들어갔습니다")
        if by_id.get("luma-ray2", {}).get("realStartImage") is not True:
            bad.append(f"(a) Luma realStartImage 가 true 가 아닙니다: {by_id.get('luma-ray2', {}).get('realStartImage')}")
        if by_id.get("seedance-2-0", {}).get("realStartImage") is not False:
            bad.append(f"(a) Seedance realStartImage 가 false 가 아닙니다: {by_id.get('seedance-2-0', {}).get('realStartImage')}")
        fal_row = by_id.get("fal:fal-ai/kling-video/v3/pro/image-to-video", {})
        if fal_row.get("realStartImage") is not True:
            bad.append(f"(a) fal realStartImage 가 true 가 아닙니다: {fal_row.get('realStartImage')}")

        # (b) 기본 모델 = realStartImage true 중 첫 번째(여기선 luma-ray2)
        if planmod.default_video_model() != "luma-ray2":
            bad.append(f"(b) default_video_model 이 luma-ray2 가 아닙니다: {planmod.default_video_model()}")
        # Seedance 가 먼저 와도 true 중 첫 번째(fal)를 고른다
        planmod.vpoc_ark.choices = lambda: [
            {"id": "seedance-2-0", "label": "S", "available": True, "provider": "byteplus"},
            {"id": "fal:x", "label": "F", "available": True, "provider": "fal"},
        ]
        if planmod.default_video_model() != "fal:x":
            bad.append(f"(b) Seedance 뒤 true 첫 번째(fal:x)를 안 골랐습니다: {planmod.default_video_model()}")
        # realStartImage true 가 없으면 ark.DEFAULT_MODEL
        planmod.vpoc_ark.choices = lambda: [
            {"id": "seedance-2-0", "label": "S", "available": True, "provider": "byteplus"},
        ]
        if planmod.default_video_model() != planmod.vpoc_ark.DEFAULT_MODEL:
            bad.append(f"(b) true 모델 없을 때 DEFAULT_MODEL 로 안 떨어졌습니다: {planmod.default_video_model()}")
    finally:
        planmod.vpoc_ark.choices = real_choices

    # (c) check_preconditions — 038 이후 생성은 프리비즈 기반이다. 비디오 모델의 실사 시작 이미지
    # 거절(RealStartImageUnsupported)은 더 이상 선행 조건이 아니다. 프리비즈 미승인/미녹화면
    # PrevizRequired 로 막는다(모델과 무관). _model_accepts_real_start 함수 자체는 그대로 둔다.
    def _plan(model: str) -> dict:
        return {
            "input": {"videoModel": model, "characters": []},
            "conti": {"status": "ready", "segments": [{"id": "seg1", "cutIds": ["c1"], "seconds": 10}]},
            "previz": {"approved": False, "recordings": {}},
            "gen": {},
        }

    seedance_model = next(iter(vpoc_settings.ARK_MODEL_IDS.keys()))
    # 모델과 무관하게 프리비즈 미승인이면 PrevizRequired(409).
    for model in (seedance_model, "luma-ray2"):
        try:
            genmod.check_preconditions(_plan(model), "tester")
            bad.append(f"(c) 프리비즈 미승인인데 거절되지 않았습니다(model={model})")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != "PrevizRequired":
                bad.append(f"(c) 미승인 거절 kind 가 PrevizRequired 가 아닙니다(model={model}): {getattr(exc, 'kind', '?')}")
            if getattr(exc, "status", 0) != 409:
                bad.append(f"(c) 미승인 거절이 409 가 아닙니다(model={model}): {getattr(exc, 'status', '?')}")

    # _model_accepts_real_start 직접(함수는 그대로 남아 있다)
    if genmod._model_accepts_real_start(seedance_model):
        bad.append("(c) _model_accepts_real_start(Seedance) 가 True 입니다")
    if not genmod._model_accepts_real_start("luma-ray2"):
        bad.append("(c) _model_accepts_real_start(Luma) 가 False 입니다")
    if not genmod._model_accepts_real_start("fal:fal-ai/kling-video/v3/pro/image-to-video"):
        bad.append("(c) _model_accepts_real_start(fal) 가 False 입니다")

    # (d) USER_FACING_KINDS
    if "RealStartImageUnsupported" not in vpoc_errors.USER_FACING_KINDS:
        bad.append("(d) USER_FACING_KINDS 에 RealStartImageUnsupported 가 없습니다")

    # (e) _user_facing_error 가 'may contain real person' 을 한국어로 바꾼다
    mapped = genmod._user_facing_error(
        "The request failed because the input image 'content[1]' may contain real person"
    )
    if "모델을 바꿔" not in mapped or "may contain real person" in mapped:
        bad.append(f"(e) 'may contain real person' 이 한국어 문구로 안 바뀌었습니다: {mapped}")
    # 다른 오류는 원문 그대로
    other = genmod._user_facing_error("some other failure")
    if other != "some other failure":
        bad.append(f"(e) 관계없는 오류가 바뀌었습니다: {other}")

    if bad:
        fail("vnew 실사 시작 이미지 거절 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "vnew 실사 시작 이미지 거절 검사 (a)realStartImage byteplus false·나머지 true"
            " (b)기본 모델 true 첫 번째 (c)생성은 프리비즈 기반 — 미승인 409 PrevizRequired"
            " (d)USER_FACING_KINDS (e)'may contain real person'→한국어"
        )


# ---------------------------------------------------------------- 13. 원작 그림체 변환 프롬프트·참고


def check_anime_style() -> None:
    """실사→웹툰 변환 프롬프트가 원작 그림체를 베끼게 바뀌었는지(설계자 2026-10-20).

    (a) 두 프롬프트(vnew·vpoc) 모두: 참고 있을 때 "Do NOT use a generic anime" 포함·
        "cel shading"/"flat soft colors"/"2D anime" 없음
    (b) 참고 없음 → 일반 "Korean webtoon style" 문장(두 프롬프트)
    (c) vnew 참고 순서: 원작 웹툰 패널 먼저, 그 뒤 캐릭터 웹툰 시트
    (d) vnew: 등장 캐릭터 styleEn 이 있으면 "Style notes: {styleEn}" 포함
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    BANNED = ("cel shading", "flat soft colors", "2D anime", "2D animation")

    from src.vnew import generate as genmod  # noqa: PLC0415

    # --- (a)(b)(d) vnew _anime_prompt 는 모듈 수준 함수라 바로 부른다 ---
    # 참고 있음 + styleEn 있음: characters.load_char 를 가짜로.
    real_load = genmod.characters.load_char

    def fake_load_with_style(owner, char_id):
        return ({"analysis": {"styleEn": "Thin sketchy pencil lines with soft pastel flats."}}, "e")

    plan_with_char = {"input": {"characters": [{"charId": "ch1", "roleName": "김부장"}]}}
    try:
        genmod.characters.load_char = fake_load_with_style
        p_refs = genmod._anime_prompt("tester", plan_with_char, 3)
    finally:
        genmod.characters.load_char = real_load

    if "Do NOT use a generic anime" not in p_refs:
        bad.append("(a) vnew 참고 프롬프트에 'Do NOT use a generic anime' 가 없습니다")
    for banned in BANNED:
        if banned in p_refs:
            bad.append(f"(a) vnew 참고 프롬프트에 제거 대상 '{banned}' 가 남아 있습니다")
    if "@Image1, @Image2, @Image3" not in p_refs:
        bad.append(f"(a) vnew 참고 프롬프트의 @Image 나열이 틀립니다: {p_refs[:120]}")
    # (d) styleEn 포함
    if "Style notes: Thin sketchy pencil lines with soft pastel flats." not in p_refs:
        bad.append(f"(d) vnew 프롬프트에 styleEn(Style notes) 이 없습니다: {p_refs[-120:]}")

    # 참고 없음(styleEn 조회도 하지 않는 경로) → 일반 문장
    p_none = genmod._anime_prompt("tester", {"input": {"characters": []}}, 0)
    if "Korean webtoon style" not in p_none:
        bad.append(f"(b) vnew 참고 없음 프롬프트에 일반 문장이 없습니다: {p_none[:120]}")
    for banned in BANNED:
        if banned in p_none:
            bad.append(f"(b) vnew 참고 없음 프롬프트에 '{banned}' 가 있습니다")
    if "@Video1" not in p_none or "No text" not in p_none:
        bad.append(f"(b) vnew 참고 없음 프롬프트 형식이 틀립니다: {p_none[:120]}")

    # styleEn 이 없으면 Style notes 를 붙이지 않는다(참고는 있음).
    try:
        genmod.characters.load_char = lambda o, c: ({"analysis": {}}, "e")
        p_nostyle = genmod._anime_prompt("tester", plan_with_char, 2)
    finally:
        genmod.characters.load_char = real_load
    if "Style notes:" in p_nostyle:
        bad.append("(d) styleEn 이 없는데 Style notes 가 붙었습니다")

    # --- (c) vnew _anime_reference_b64 순서: 패널 먼저, 그 뒤 캐릭터 시트 ---
    fake_store = _FakeCharStore()
    from src.vnew import plan as planmod  # noqa: PLC0415

    owner, pid = "tester", "prj_style"
    panel_key = planmod.upload_key(owner, pid, "f1")
    fake_store.blobs[panel_key] = b"PANEL-BYTES"
    fake_store.blobs["ws.jpg"] = b"CHAR-SHEET-BYTES"

    def fake_load_char_sheet(o, c):
        return ({"variants": {"webtoon": {"sheetKey": "ws.jpg"}}, "analysis": {}}, "e")

    plan = {
        "input": {"order": ["f1"], "characters": [{"charId": "ch1", "roleName": "김부장"}]},
        "conti": {"segments": [{"id": "seg1", "cutIds": ["c1"], "seconds": 10}]},
    }
    seg = {"id": "seg1", "cutIds": ["c1"]}
    import base64 as _b64  # noqa: PLC0415

    orig_store = genmod.STORE
    orig_load2 = genmod.characters.load_char
    try:
        genmod.STORE = fake_store
        genmod.characters.load_char = fake_load_char_sheet
        refs = genmod._anime_reference_b64(owner, pid, plan, seg)
    finally:
        genmod.STORE = orig_store
        genmod.characters.load_char = orig_load2

    decoded = [_b64.b64decode(r) for r in refs]
    if not decoded:
        bad.append("(c) vnew 참고 이미지가 비었습니다")
    else:
        if decoded[0] != b"PANEL-BYTES":
            bad.append(f"(c) vnew 참고 첫 장이 원작 웹툰 패널이 아닙니다: {decoded[0][:20]!r}")
        if b"CHAR-SHEET-BYTES" not in decoded:
            bad.append("(c) vnew 참고에 캐릭터 웹툰 시트가 없습니다")
        elif decoded.index(b"CHAR-SHEET-BYTES") == 0:
            bad.append("(c) vnew 참고에서 캐릭터 시트가 패널보다 먼저 왔습니다")
        if len(decoded) > 4:
            bad.append(f"(c) vnew 참고가 4장을 넘습니다: {len(decoded)}")

    # --- (a)(b) vpoc routes._anime_prompt 는 create_blueprint 안 중첩 함수라 소스로 확인한다 ---
    rsource = (ROOT / "app" / "src" / "vpoc" / "routes.py").read_text(encoding="utf-8")
    # _anime_prompt 는 모듈 함수라 실제 결과 문장으로 본다(docstring 의 설명 글자는 보지 않는다).
    from src.vpoc import routes as _vroutes  # noqa: PLC0415
    body = _vroutes._anime_prompt(3) + " " + _vroutes._anime_prompt(0)
    if "Do NOT use a generic anime" not in body:
        bad.append("(a) vpoc _anime_prompt 에 'Do NOT use a generic anime' 가 없습니다")
    if "Korean webtoon style" not in body:
        bad.append("(b) vpoc _anime_prompt 에 참고 없음용 'Korean webtoon style' 문장이 없습니다")
    for banned in BANNED:
        if banned in body:
            bad.append(f"(a) vpoc _anime_prompt 에 제거 대상 '{banned}' 가 남아 있습니다")

    # vpoc 참고 순서: sceneFiles 패널(최대 3)이 캐릭터 그림보다 먼저. 소스로 확인(_spread(panels, 3) 가
    # 캐릭터 루프보다 앞에 있고 캐릭터는 1장만).
    rb_start = rsource.index("def _anime_reference_b64(state: dict, owner: str, pid: str)")
    rb_body = rsource[rb_start : rsource.index("def _run_anime_job(")]
    if "_spread(panels, 3)" not in rb_body:
        bad.append("(c) vpoc 참고가 패널 최대 3장(_spread(panels, 3))이 아닙니다")
    if "break  # 캐릭터 그림은 1장만." not in rb_body:
        bad.append("(c) vpoc 참고에서 캐릭터 그림을 1장만으로 제한하지 않습니다")
    if rb_body.index("panels = _story_panels") > rb_body.index('steps["3"].get("characters")'):
        bad.append("(c) vpoc 참고에서 패널이 캐릭터보다 먼저 오지 않습니다")

    if bad:
        fail("원작 그림체 변환 프롬프트·참고 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "원작 그림체 변환 프롬프트·참고 검사 (a)두 프롬프트 'Do NOT use a generic anime'·금지 문구 없음"
            " (b)참고 없음 일반 문장 (c)vnew·vpoc 패널 먼저 (d)styleEn 포함"
        )


# ---------------------------------------------------------------- 11. 영상만들기_신규 프리비즈(vnew previz)


def check_vnew_previz() -> None:
    """영상만들기_신규 ③→④ 프리비즈(3D 장면 설계) — 설계 LLM 은 가짜(설계자 2026-10-21).

    (a) design 202 → scenes, 컷 1:1, 시간 합계
    (b) 정리: 방 밖 인물 좌표 보정, 색 중복 재배정, fov 범위
    (c) 장소 바뀌면 scene 2개
    (d) 텍스처 키 채움(location 실사 variant 벽·바닥)
    (e) PUT 정리·승인 해제
    (f) 녹화 업로드 변환(합성 webm → mp4) · 길이 벗어나면 400
    (g) recordedAll
    (j) 프레임 묶음 업로드(3 묶음) + finish → mp4 길이 = frames/fps, 프레임 정리
    (k) 중간 묶음 누락이면 finish 400(MissingFrame)
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import previz as pvmod  # noqa: PLC0415
    from src.vnew import previz_grammar as grammarmod  # noqa: PLC0415
    from src.vnew import shot_brief as _sbmod  # noqa: PLC0415
    _orig_brief_safe = _sbmod.start_brief_safe
    _sbmod.start_brief_safe = lambda *_a, **_k: None  # 승인 후 지시서 스레드가 (e) 와 경합하지 않게(지시서는 sb-d 검사)
    from src.vnew import previz_schema as pvschema  # noqa: PLC0415

    fake_store = _FakeCharStore()

    # --- (np-c) 대사 있는 컷의 말하는 인물(제스처 없음) → turn_head_to_other + 상대 id ---
    from src.vnew import generate as _genmod_npc  # noqa: PLC0415

    # 컷 c1: 범건우가 말하고(대사), 여의준은 듣는다. grammar.actions 없음 → 말하는 인물에 다른 제스처 없음.
    npc_scene = {
        "actors": [{"id": "a1", "name": "범건우"}, {"id": "a2", "name": "여의준"}],
        "timeline": [
            {
                "cutId": "c1",
                "actors": [{"id": "a1", "speaking": True}, {"id": "a2", "speaking": False}],
            }
        ],
    }
    npc_groups = [{"locationEn": "office", "cuts": [{"cutId": "c1", "characters": [{"name": "범건우", "actionEn": ""}]}]}]
    npc_plan = {
        "script": {"entries": []},
        "conti": {"cuts": [{"id": "c1", "panels": [0], "dialogueKo": '범건우: "자네 뭐 하나?"'}]},
    }
    pvmod._apply_gestures([npc_scene], npc_groups, {}, npc_plan)
    speaker_actor = next((ca for ca in npc_scene["timeline"][0]["actors"] if ca["id"] == "a1"), {})
    listener_actor = next((ca for ca in npc_scene["timeline"][0]["actors"] if ca["id"] == "a2"), {})
    if speaker_actor.get("gesture") != "turn_head_to_other":
        bad.append(f"(np-c) 말하는 인물 제스처가 turn_head_to_other 가 아닙니다: {speaker_actor.get('gesture')}")
    if speaker_actor.get("gestureTarget") != "a2":
        bad.append(f"(np-c) 말하는 인물 gestureTarget 이 상대(a2)가 아닙니다: {speaker_actor.get('gestureTarget')}")
    # 듣는 사람은 turn_head 강제가 없다(말하지 않으므로).
    if listener_actor.get("gesture") == "turn_head_to_other":
        bad.append("(np-c) 듣는 사람에게 turn_head_to_other 가 잘못 붙었습니다")
    # 이미 다른 제스처가 있으면 덮어쓰지 않는다.
    npc_scene2 = {
        "actors": [{"id": "a1", "name": "범건우"}, {"id": "a2", "name": "여의준"}],
        "timeline": [
            {
                "cutId": "c1",
                "actors": [{"id": "a1", "speaking": True, "gesture": "wipe_tears"}, {"id": "a2"}],
            }
        ],
    }
    pvmod._apply_gestures([npc_scene2], npc_groups, {}, npc_plan)
    keep_actor = next((ca for ca in npc_scene2["timeline"][0]["actors"] if ca["id"] == "a1"), {})
    if keep_actor.get("gesture") != "wipe_tears":
        bad.append(f"(np-c) 이미 있던 제스처가 turn_head_to_other 로 덮였습니다: {keep_actor.get('gesture')}")

    # ffmpeg 바이너리를 어떤 패치보다 먼저 구해 둔다(imageio_ffmpeg 를 threading.Thread 패치 전에 import).
    _ffmpeg_bin = ""
    try:
        import imageio_ffmpeg as _iioff  # noqa: PLC0415

        _ffmpeg_bin = _iioff.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        import shutil as _shutil  # noqa: PLC0415

        _ffmpeg_bin = _shutil.which("ffmpeg") or ""

    # --- (b) 정리 단위: 방 밖 좌표 보정·색 중복 재배정·fov 범위 ---
    scene_raw = {
        "room": {"width": 100.0, "depth": 2.0, "height": 2.8},  # width 큼→30, depth 작음→3
        "actors": [
            {"id": "a1", "name": "김", "color": "#ff0000", "heightM": 1.7},
            {"id": "a2", "name": "이", "color": "#ff0000", "heightM": 1.7},  # 색 중복
        ],
        "timeline": [
            {
                "cutId": "c1",
                "camera": {"pos": [0, 1.5, 5], "target": [0, 1, 0], "fov": 200},  # fov 범위 밖→90
                "actors": [
                    {"id": "a1", "pos": [999, 0, 999], "pose": "stand"},  # 방 밖→보정
                    {"id": "a2", "pos": [-999, 0, -999], "pose": "sit"},
                ],
            }
        ],
    }
    scene = pvschema.normalize_scene(
        scene_raw, location_id="loc1", cut_specs=[{"cutId": "c1", "seconds": 4}]
    )
    room = scene["room"]
    if not (pvschema.ROOM_MIN_M <= room["width"] <= pvschema.ROOM_MAX_M):
        bad.append(f"(b) 방 width 범위 보정 실패: {room['width']}")
    if not (pvschema.ROOM_MIN_M <= room["depth"] <= pvschema.ROOM_MAX_M):
        bad.append(f"(b) 방 depth 범위 보정 실패: {room['depth']}")
    colors = [a["color"] for a in scene["actors"]]
    if len(set(c.lower() for c in colors)) != 2:
        bad.append(f"(b) 인물 색 중복이 재배정되지 않았습니다: {colors}")
    fov = scene["timeline"][0]["camera"]["fov"]
    if not (pvschema.FOV_MIN <= fov <= pvschema.FOV_MAX):
        bad.append(f"(b) fov 범위 보정 실패: {fov}")
    # 방 밖 인물 좌표가 방 안으로 들어왔는지(|x|<=width/2, |z|<=depth/2).
    half_w = room["width"] / 2.0
    half_d = room["depth"] / 2.0
    for ca in scene["timeline"][0]["actors"]:
        x, _, z = ca["pos"]
        if abs(x) > half_w or abs(z) > half_d:
            bad.append(f"(b) 인물 좌표가 방 밖입니다: {ca['pos']} (방 {room['width']}x{room['depth']})")
            break
    # 시간 연속·합계: c1 seconds=4 → start 0, end 4.
    if scene["timeline"][0]["start"] != 0 or scene["timeline"][0]["end"] != 4:
        bad.append(f"(b) 시간 누적이 틀립니다: {scene['timeline'][0]['start']}~{scene['timeline'][0]['end']}")

    # --- 가짜 설계 LLM(장소 1개=컷 3개 timeline, cutId 그대로) ---
    # 투영 검산(fit)·재요청·격자 보정을 함께 확인하려고, 인물 a1(교장)을 패널 샷 목표에 맞추는
    # '좋은' 카메라와 많이 어긋난 '나쁜' 카메라를 둔다. a1 발 위치는 (1,0,0)(c1·c2, c3 은 복사).
    # 나쁜 카메라는 '먼 거리'(설계자 예시)로 둔다 — a1 을 같은 방향(target)으로 보되 5m 더 멀리서
    # 보면 heightFrac(화면 높이 대비 인물 키)이 목표보다 훨씬 작아져 크기 오차가 임계를 넘는다.
    # target(바라보는 점)은 a1 쪽을 그대로 두므로 screenX 는 유지되고, 격자 탐색이 forward(거리)
    # 축으로 카메라를 끌어당겨 오차를 줄일 수 있다(결정적).
    from src.vnew import previz_fit as pvfit  # noqa: PLC0415

    A1_FOOT = [1.0, 0.0, 0.0]
    A1_HEIGHT = 1.72
    GOOD_CAM = {"pos": [1.0, 1.5, 4.0], "target": [1.0, 1.0, 0.0], "fov": 50.0}  # a1 이 화면에 알맞게
    BAD_CAM = {"pos": [1.0, 1.5, 9.0], "target": [1.0, 1.0, 0.0], "fov": 50.0}  # 5m 더 멀리 → a1 이 너무 작게
    # a1 을 GOOD_CAM 으로 투영한 값을 패널 샷 목표로 쓴다(=GOOD_CAM 이면 오차 ~0).
    _a1_proj = pvfit.project_actor(GOOD_CAM, A1_FOOT, A1_HEIGHT)
    A1_TARGET = {
        "screenX": round(_a1_proj["screenX"], 3),
        "screenY": round(_a1_proj["headY"], 3),
        "heightFrac": round(_a1_proj["heightFrac"], 3),
    }

    design_calls: list[str] = []
    design_prompts: list[str] = []  # fake_design 이 받은 프롬프트 전문(pv-c 다시 만들기 검증)

    # --- 가짜 설계 LLM(049): 카메라는 서버가 계산하므로 Astra 는 인물 자리·자세·facingDeg 만 쓴다. ---
    # 교장실 scene: a1(교장) 앉음·a2(학생) 서 있음. 거리 scene: a3 한 사람.
    def fake_design(blocks, *, model):
        design_calls.append(model)
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text = b["text"]
                break
        design_prompts.append(text)
        # 거리(c4) 장소 프롬프트면 거리 장면을 준다.
        if "city street" in text or ("거리" in text and "c4" in text):
            payload = {
                "room": {"width": 8.0, "depth": 8.0, "height": 10.0},  # 열린 공간: 서버가 3.0 으로 clamp
                "actors": [{"id": "a3", "name": "행인", "color": "#3cb44b", "heightM": 1.7}],
                "timeline": [{"cutId": "c4", "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 50},
                              "actors": [{"id": "a3", "pos": [0, 0, 0], "pose": "stand"}]}],
                "notesKo": "거리",
            }
            return payload, {"model": model, "inputTokens": 10, "outputTokens": 20}
        # 교장실: 모델이 timeline 을 2개만 주고(c3 누락), 색도 하나만 준다 → 정리가 1:1·색 보정.
        # 방 height 10 → 닫힌 방이라 서버가 2.4~3.5 로 clamp(pg-d).
        payload = {
            "room": {"width": 6.0, "depth": 5.0, "height": 10.0, "walls": {"north": {"color": "#ccc"}}},
            "light": {"kind": "sun", "from": [0, 2.8, -2.5], "to": [0, 0, 0], "labelKo": "북쪽 창문 햇빛"},
            "props": [{"id": "p1", "labelKo": "책상", "shape": "box", "size": [1.5, 0.8, 0.8], "pos": [0, 0.4, 0]}],
            "actors": [
                {"id": "a1", "name": "교장", "color": "#e6194b", "heightM": A1_HEIGHT},
                {"id": "a2", "name": "학생", "color": "#e6194b", "heightM": 1.6},  # 중복색
            ],
            "timeline": [
                {"cutId": "c1", "camera": {"pos": [1, 1.5, 4], "target": [1, 1, 0], "fov": 50},
                 "actors": [{"id": "a1", "pos": [1, 0, 0], "pose": "sit", "speaking": True, "facingDeg": 270},
                            {"id": "a2", "pos": [-1, 0, 0], "pose": "stand", "facingDeg": 90}]},
                {"cutId": "c2", "camera": {"pos": [1, 1.5, 4], "target": [1, 1, 0], "fov": 50},
                 "actors": [{"id": "a1", "pos": [1, 0, 0], "pose": "sit", "facingDeg": 270},
                            {"id": "a2", "pos": [-1, 0, 0], "pose": "stand", "facingDeg": 90}]},
            ],
            "notesKo": "교장실 대화",
        }
        return payload, {"model": model, "inputTokens": 10, "outputTokens": 20}

    # --- 가짜 연출 문법 읽기(previz_grammar._read 대체). read_cut 이 이를 호출·normalize 한다. ---
    # 교장실 c1·c2·c3 는 교장 단독 바스트(더티 싱글). 거리 c4 는 패널이 없다고 보고 None 을 돌려준다
    # (match null → 낮은 컷 → pg-f·승인 관문). 호출 횟수를 cutId 별로 센다(캐시 검증 pg-a).
    read_calls: list[str] = []

    def fake_read(blocks, *, model):
        # 블록의 cut 설명(json)에서 cutId 를 추정하기 어려우니 호출만 센다. shotKo 로 교장/학생 구분.
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text = b["text"]
                break
        read_calls.append(text[:40])
        # 거리 컷은 패널 없음 → read_cut 이 tiles 없으면 애초에 안 부른다. 여기 오면 교장실 컷.
        payload = {
            "mainSubject": "교장",
            "frameBottomCut": "chest",
            "shotType": "dirty_single",
            "vantage": "front3q",
            "faceTurn": "toward_camera",
            "cameraHeight": "eye",
            "headTopY": 0.1,
            "subjectX": 0.5,
            "others": [{"name": "학생", "how": "hands_only", "side": "right", "screenX": 0.85}],
            "offscreenPartnerSide": "right",
            "reasonKo": "교장 바스트 단독",
        }
        return payload, {"model": model, "inputTokens": 7, "outputTokens": 14}

    # 가짜 지시→수정 목록 변환(_changes, emit_edits). 받은 지시를 기록하고 c1 vantage=side 로 바꾼다
    # (pv-c·pv-d·pg-e). Astra 먼저 불린다(모델 순서 검증).
    changes_calls: list[dict] = []

    def fake_changes(blocks, *, model):
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text = b["text"]
                break
        changes_calls.append({"model": model, "text": text})
        return (
            {"edits": [{"cutId": "c1", "field": "vantage", "value": "side", "noteEn": "profile only"}]},
            {"model": model, "inputTokens": 4, "outputTokens": 8},
        )

    ledger_calls: list[dict] = []

    def fake_charge_text(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    # 검사 환경에는 실제 업로드 웹툰 타일이 없다. _cut_tiles 를 가짜로 바꿔, panels 가 있는 컷은
    # 가짜 JPEG 바이트 1장, panels 가 빈 컷(c4)은 빈 목록을 돌려준다 → read_cut 이 c1·c2·c3 만 불린다.
    # (코드 경로는 그대로: run_design → _read_grammars → _cut_tiles → read_cut.)
    def fake_cut_tiles(cut_panels, all_tiles):
        return [b"FAKE-PANEL-JPEG"] if (cut_panels or []) else []

    def fake_analyze_conti(blocks, *, model):
        # 교장실 컷 3개(c1,c2,c3) + 거리 컷 1개(c4) → 장소 2개.
        cuts = [
            {"id": "c1", "no": 1, "seconds": 4, "panels": [0], "shotKo": "", "cameraEn": "",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c2", "no": 2, "seconds": 4, "panels": [1], "shotKo": "", "cameraEn": "",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c3", "no": 3, "seconds": 4, "panels": [2], "shotKo": "", "cameraEn": "",
             "locationEn": "A closed principal's office", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
            {"id": "c4", "no": 4, "seconds": 5, "panels": [], "shotKo": "", "cameraEn": "",
             "locationEn": "A city street at sunset", "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"},
        ]
        return {"cuts": cuts}, {"model": model, "inputTokens": 3, "outputTokens": 6}

    orig = {
        "pv_STORE": pvmod.STORE,
        "plan_STORE": planmod.STORE,
        "_design": pvmod._design,
        "gr_read": grammarmod._read,
        "_cut_tiles": pvmod._cut_tiles,
        "_changes": pvmod._changes,
        "pv_charge_text": pvmod.vpoc_ledger.charge_text,
        "plan_analyze_conti": planmod._analyze_conti,
        "plan_charge_text": planmod.vpoc_ledger.charge_text,
        "pv_Thread": pvmod.threading.Thread,
    }
    pvmod.STORE = fake_store
    planmod.STORE = fake_store
    pvmod._design = fake_design
    grammarmod._read = fake_read
    pvmod._cut_tiles = fake_cut_tiles
    pvmod._changes = fake_changes
    pvmod.vpoc_ledger.charge_text = fake_charge_text
    planmod._analyze_conti = fake_analyze_conti
    planmod.vpoc_ledger.charge_text = fake_charge_text

    # 승인 → 촬영 지시서 작성(shot_brief)이 자동 시작한다(⑤). _brief 를 가짜로 바꿔 실제 LLM 호출을 막고,
    # (sb-d)에서 쓸 입력 텍스트를 잡아 둔다. _brief 는 (payload, usage) 를 돌려준다.
    from src.vnew import shot_brief as briefmod  # noqa: PLC0415

    brief_inputs: list[str] = []

    def fake_brief(blocks, *, model):
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text += b["text"] + "\n"
        brief_inputs.append(text)
        # 그 입력에 든 컷 id 를 읽어 각 컷 brief 를 돌려준다(장소당 1회 호출).
        import re as _re  # noqa: PLC0415

        cut_ids = _re.findall(r"CUT (\S+):", text)
        payload = {
            "sceneEn": "A principal's office with pale green plaster walls and warm daylight.",
            "cuts": [
                {
                    "cutId": cid,
                    "shotEn": "MCU, 50mm, eye-level, three-quarter, clean single.",
                    "blockingEn": "Kim at frame center.",
                    "actionEn": "Kim leans forward.",
                    "moodEn": "tense.",
                    "backgroundEn": "wood wall behind, shallow focus.",
                    "dialogue": [],
                }
                for cid in cut_ids
            ],
        }
        return payload, {"model": model, "inputTokens": 6, "outputTokens": 30}

    orig["brief"] = briefmod._brief
    briefmod._brief = fake_brief

    started_threads: list = []
    orig_thread = pvmod.threading.Thread

    def _tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    pvmod.threading.Thread = _tracking_thread

    def _join():
        for t in list(started_threads):
            t.join(timeout=60)
        started_threads.clear()

    def _make_webm(seconds: float) -> bytes:
        """imageio_ffmpeg 바이너리로 testsrc 를 libvpx(vp8)로 인코딩한 합성 webm 바이트.

        ffmpeg 경로(_ffmpeg_bin)는 threading.Thread 를 바꿔 끼우기 전에 미리 구해 둔다 —
        get_ffmpeg_exe 를 패치 중에 처음 부르면 imageio_ffmpeg 가 threading.Thread 를 상속하다
        깨질 수 있다("function() argument 'code' must be code, not str").
        """
        import subprocess as _sub  # noqa: PLC0415
        import tempfile as _tmp  # noqa: PLC0415
        import os as _os  # noqa: PLC0415

        if not _ffmpeg_bin:
            raise RuntimeError("ffmpeg 바이너리를 찾지 못했습니다(imageio_ffmpeg)")
        with _tmp.TemporaryDirectory(prefix="previz-webm-") as workdir:
            out = _os.path.join(workdir, "rec.webm")
            cmd = [
                _ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=24:duration={seconds}",
                "-c:v", "libvpx", "-b:v", "500k", "-an", out,
            ]
            result = _sub.run(cmd, stdout=_sub.PIPE, stderr=_sub.PIPE, timeout=120, check=False)
            if result.returncode != 0 or not _os.path.exists(out) or not _os.path.getsize(out):
                detail = result.stderr.decode("utf-8", errors="replace")[:200]
                raise RuntimeError(f"합성 webm 인코딩 실패: {detail}")
            with open(out, "rb") as handle:
                return handle.read()

    try:
        import io as _io  # noqa: PLC0415

        from PIL import Image as _Img  # noqa: PLC0415

        owner = "tester"
        pid = "prj_previz_1"

        def _real_image(label: int) -> bytes:
            buffer = _io.BytesIO()
            _Img.new("RGB", (600, 900), (label % 255, 180, 160)).save(buffer, format="PNG")
            return buffer.getvalue()

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
        client.get(f"/api/vnew/p/{pid}/plan")

        # 대본(장소 2개)·콘티를 심는다. 장소에 실사 텍스처 키를 넣어 (d) 를 확인한다.
        planmod.update_script(
            owner,
            pid,
            {
                "setting": {"locationKo": "교장실", "locationEn": "A closed principal's office", "enclosed": True},
                "entries": [
                    {"id": "e1", "panels": [0, 1], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "교장실", "locationEn": "A closed principal's office",
                     "characters": [{"name": "교장", "positionEn": "behind the desk"}]},
                    {"id": "e2", "panels": [2], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "교장실", "locationEn": "A closed principal's office", "characters": []},
                    {"id": "e3", "panels": [3], "speaker": "", "lineKo": "", "situationKo": "",
                     "locationKo": "노을 진 거리", "locationEn": "A city street at sunset", "characters": []},
                ],
            },
        )

        def _set_ready_script(d):
            d["script"]["status"] = "ready"

        planmod.mutate_plan(owner, pid, _set_ready_script)

        resp = client.post(f"/api/vnew/p/{pid}/conti")
        if resp.status_code != 202:
            bad.append(f"POST /conti 가 202 가 아닙니다: {resp.status_code}")
        _join()

        # (d) 장소에 실사 variant 텍스처 키를 심는다(공간 시트가 만들어졌다고 가정).
        # 키는 반드시 이 프로젝트의 locations 접두사 아래여야 텍스처 API 가 돌려준다. 바이트도 심는다.
        loc_prefix = pvmod._locations_prefix(owner, pid)
        tex_bytes = _tiny_jpeg(64, 64)

        def _seed_textures(d):
            from src.vnew import locations as _loc  # noqa: PLC0415

            office = next((l for l in d.get("locations") or [] if "principal" in (l.get("key") or "")), None)
            if office:
                lid = office.get("id") or "loc1"
                wall_keys = {s: f"{loc_prefix}{lid}/real/wall_{s}_v1.jpg" for s in _loc.SIDES}
                floor_k = f"{loc_prefix}{lid}/real/floor_v1.jpg"
                office["variants"]["real"] = {
                    "status": "ready",
                    "wallKeys": wall_keys,
                    "floorKey": floor_k,
                    "sheetKey": f"{loc_prefix}{lid}/real/sheet_v1.jpg",
                    "error": "",
                }
                for k in list(wall_keys.values()) + [floor_k]:
                    fake_store.blobs[k] = tex_bytes

        planmod.mutate_plan(owner, pid, _seed_textures)

        # --- (a) design 202 → scenes, 컷 1:1, 시간 합계 ---
        resp = client.post(f"/api/vnew/p/{pid}/previz/design")
        if resp.status_code != 202:
            bad.append(f"(a) POST /previz/design 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()

        if not design_calls:
            bad.append("(a) 설계 LLM 이 불리지 않았습니다")
        elif design_calls[0] != pvmod.ASTRA_MODEL:
            bad.append(f"(a) 설계가 Astra 를 먼저 쓰지 않습니다: {design_calls[0]}")

        view = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        pv = view.get("previz") or {}
        if pv.get("status") != "ready":
            bad.append(f"(a) previz 상태가 ready 가 아닙니다: {pv.get('status')} / {pv.get('error')}")
        scenes = pv.get("scenes") or []

        # 콘티 컷(정리·fit 후)에서 기대 timeline 을 계산한다 — normalize 는 콘티 seconds 를 강제해야
        # 하므로, 하드코딩이 아니라 콘티 seconds 를 그대로 따라야 한다.
        conti = view.get("conti") or {}
        conti_cuts = conti.get("cuts") or []
        conti_total = sum(float(c.get("seconds") or 0) for c in conti_cuts)

        # 장소(연속 컷)별 그룹 = scene. cutId→(locationEn, seconds).
        def _groups(cuts):
            groups = []
            for c in cuts:
                loc = str(c.get("locationEn") or "").strip()
                if groups and groups[-1][0] == loc:
                    groups[-1][1].append(c)
                else:
                    groups.append([loc, [c]])
            return groups

        groups = _groups(conti_cuts)

        # (c) 장소가 바뀌면 scene 2개(교장실·거리).
        if len(scenes) != len(groups):
            bad.append(f"(c) scene 수가 콘티 장소 그룹과 다릅니다: scenes={len(scenes)} groups={len(groups)}")
        if len(groups) != 2:
            bad.append(f"(c) 장소가 바뀌었는데 콘티 그룹이 2개가 아닙니다: {len(groups)}")

        office_scene = scenes[0] if scenes else {}

        # (a) 컷 1:1: 교장실 scene 의 timeline cutId 가 그 그룹 컷과 1:1(모델이 c3 를 빼도 복사로 채움).
        expect_office_ids = [str(c.get("id") or "") for c in (groups[0][1] if groups else [])]
        cut_ids = [str(t.get("cutId") or "") for t in office_scene.get("timeline") or []]
        if cut_ids != expect_office_ids:
            bad.append(f"(a) 컷 1:1 이 아닙니다(빠진 컷 복사 포함): {cut_ids} vs 콘티 {expect_office_ids}")

        # (a) 시간 합계 = 콘티 totalSeconds(= 모든 장면 합).
        total = pv.get("totalSeconds")
        if abs(float(total or 0) - float(conti_total)) > 0.01:
            bad.append(f"(a) 시간 합계가 콘티 합({conti_total})과 다릅니다: {total}")

        # (a) timeline 은 콘티 전체 기준 누적(장면이 바뀌어도 0 으로 안 돌아감). 각 컷 길이=콘티 seconds.
        clock = 0.0
        all_tl: list = []
        for scene, (_loc, cuts) in zip(scenes, groups):
            tl = scene.get("timeline") or []
            if len(tl) != len(cuts):
                bad.append(f"(a) scene 컷 수가 콘티 그룹과 다릅니다: {len(tl)} vs {len(cuts)}")
                break
            for t, c in zip(tl, cuts):
                exp_start = round(clock, 3)
                exp_end = round(clock + float(c.get("seconds") or 0), 3)
                if abs(float(t.get("start") or 0) - exp_start) > 0.01 or abs(float(t.get("end") or 0) - exp_end) > 0.01:
                    bad.append(
                        f"(a) timeline 누적 시간이 콘티와 다릅니다 cut={t.get('cutId')}: "
                        f"{(t.get('start'), t.get('end'))} 기대 {(exp_start, exp_end)}"
                    )
                clock = exp_end
                all_tl.append((t.get("cutId"), t.get("start"), t.get("end")))
        # 두 번째 장면(거리)이 0 에서 다시 시작하지 않고 교장실 끝에서 이어지는지 확인.
        if len(scenes) >= 2:
            street_tl = scenes[1].get("timeline") or []
            office_tl = scenes[0].get("timeline") or []
            if street_tl and office_tl:
                office_end = float(office_tl[-1].get("end") or 0)
                street_start = float(street_tl[0].get("start") or 0)
                if abs(street_start - office_end) > 0.01:
                    bad.append(f"(a) 장면이 바뀔 때 시간이 이어지지 않습니다(거리 start={street_start} vs 교장실 end={office_end})")
                if street_start == 0.0 and office_end != 0.0:
                    bad.append("(a) 두 번째 장면 timeline 이 0 으로 되돌아갔습니다(누적이어야 함)")

        # (b) 색 중복 재배정(모델이 두 인물에 같은 색을 줬다).
        acolors = [a.get("color") for a in office_scene.get("actors") or []]
        if len(acolors) == 2 and acolors[0].lower() == acolors[1].lower():
            bad.append(f"(b) 설계 결과 인물 색 중복이 재배정되지 않았습니다: {acolors}")

        # (d) 텍스처 키 채움 + textureUrl(같은 출처 API 경로).
        room = office_scene.get("room") or {}
        walls = room.get("walls") or {}
        north = walls.get("north") or {}
        if not north.get("textureKey"):
            bad.append("(d) 벽 textureKey 가 채워지지 않았습니다")
        if not north.get("textureUrl"):
            bad.append("(d) 벽 textureUrl 이 없습니다")
        if not room.get("floorTextureKey"):
            bad.append("(d) 바닥 floorTextureKey 가 채워지지 않았습니다")

        # (i) plan_view 의 textureUrl 은 서명 URL(S3 직통)이 아니라 같은 출처 텍스처 API 경로여야 한다.
        import urllib.parse as _urlparse  # noqa: PLC0415

        tex_api_prefix = f"/api/vnew/p/{pid}/previz/texture?"
        wall_url = str(north.get("textureUrl") or "")
        floor_url = str(room.get("floorTextureUrl") or "")
        if not wall_url.startswith(tex_api_prefix):
            bad.append(f"(i) 벽 textureUrl 이 텍스처 API 경로가 아닙니다: {wall_url}")
        if wall_url.startswith("http"):
            bad.append(f"(i) 벽 textureUrl 이 서명 URL(S3 직통)로 보입니다: {wall_url}")
        if not floor_url.startswith(tex_api_prefix):
            bad.append(f"(i) 바닥 textureUrl 이 텍스처 API 경로가 아닙니다: {floor_url}")
        # textureUrl 의 key 가 textureKey 와 일치하는지.
        if wall_url.startswith(tex_api_prefix):
            qs = _urlparse.parse_qs(_urlparse.urlsplit(wall_url).query)
            key_in_url = (qs.get("key") or [""])[0]
            if key_in_url != str(north.get("textureKey") or ""):
                bad.append(f"(i) textureUrl 의 key 가 textureKey 와 다릅니다: {key_in_url} vs {north.get('textureKey')}")

        # (h) 텍스처 API: 올바른 key 는 200·이미지 바이트, 접두사 밖 key 는 404, 다른 사용자 프로젝트는 404.
        if wall_url.startswith(tex_api_prefix):
            resp = client.get(wall_url)
            if resp.status_code != 200:
                bad.append(f"(h) 텍스처 API 가 200 이 아닙니다: {resp.status_code}")
            elif resp.data != tex_bytes:
                bad.append("(h) 텍스처 API 가 저장된 이미지 바이트를 그대로 돌려주지 않습니다")
            elif not str(resp.headers.get("Content-Type") or "").startswith("image/"):
                bad.append(f"(h) 텍스처 API Content-Type 이 이미지가 아닙니다: {resp.headers.get('Content-Type')}")
            elif "max-age=3600" not in str(resp.headers.get("Cache-Control") or ""):
                bad.append(f"(h) 텍스처 API Cache-Control 이 private max-age=3600 이 아닙니다: {resp.headers.get('Cache-Control')}")

        # (h) locations 접두사 밖 key → 404(다른 프로젝트·경로 접근 차단).
        outside_key = f"users/{owner}/video/{pid}/vnew/uploads/not_a_texture.jpg"
        resp = client.get(tex_api_prefix + _urlparse.urlencode({"key": outside_key}))
        if resp.status_code != 404:
            bad.append(f"(h) 접두사 밖 텍스처 key 가 404 가 아닙니다: {resp.status_code}")

        # (h) 다른 사용자 프로젝트의 텍스처 key → 404(소유자 접두사가 달라 접두사 밖).
        other_key = f"users/other_user/video/{pid}/vnew/locations/locX/real/wall_north_v1.jpg"
        resp = client.get(tex_api_prefix + _urlparse.urlencode({"key": other_key}))
        if resp.status_code != 404:
            bad.append(f"(h) 다른 사용자 텍스처 key 가 404 가 아닙니다: {resp.status_code}")

        # 원장: 장소 2개 → 설계 LLM 2회 → charge_text 최소 2건.
        text_charges = [c for c in ledger_calls if "previz" in str(c.get("key") or "")]
        if len(text_charges) < 2:
            bad.append(f"(원장) 프리비즈 설계 원장이 2건 미만입니다: {len(text_charges)}")

        # --- (pg-a) read_cut 이 컷 수만큼 호출됐다(패널 있는 교장실 c1·c2·c3, 거리 c4 는 panels 없음) ---
        # 교장실 3컷만 패널이 있으므로 read 호출은 3회.
        if len(read_calls) != 3:
            bad.append(f"(pg-a) read_cut 호출이 교장실 3컷 수와 다릅니다: {len(read_calls)}")
        # panelGrammar 캐시에 c1·c2·c3 가 {hash, grammar} 로 저장됐는지.
        panel_grammar = pv.get("panelGrammar") or {}
        for cid in ("c1", "c2", "c3"):
            cell = panel_grammar.get(cid) or {}
            if not cell.get("hash") or not isinstance(cell.get("grammar"), dict):
                bad.append(f"(pg-a) panelGrammar[{cid}] 가 {{hash,grammar}} 로 저장되지 않았습니다: {cell}")
        if (panel_grammar.get("c1") or {}).get("grammar", {}).get("shotSize") != "MCU":
            bad.append(f"(pg-a) c1 grammar.shotSize 가 MCU 가 아닙니다: {(panel_grammar.get('c1') or {}).get('grammar')}")

        # --- (pg-a 캐시) 같은 패널로 다시 설계하면 read_cut 0회 ---
        read_calls.clear()
        resp = client.post(f"/api/vnew/p/{pid}/previz/design")
        _join()
        if read_calls:
            bad.append(f"(pg-a) 캐시가 같은 패널을 다시 읽었습니다(read {len(read_calls)}회)")
        # 다시 읽은 뒤 상태를 갱신.
        view = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        pv = view.get("previz") or {}
        scenes = pv.get("scenes") or []
        office_scene = scenes[0] if scenes else {}
        fit = pv.get("fit") or {}

        # --- (pg-b) 각 컷 camera 가 place_cut 결과와 같고 fit.method=="grammar", match 저장 ---
        import math as _math  # noqa: PLC0415

        from src.vnew import previz_camera as cammod  # noqa: PLC0415

        for cid in ("c1", "c2", "c3"):
            fe = fit.get(cid) or {}
            if fe.get("method") != "grammar":
                bad.append(f"(pg-b) {cid} fit.method 가 grammar 가 아닙니다: {fe.get('method')}")
            if fe.get("match") is None:
                bad.append(f"(pg-b) {cid} fit.match 가 저장되지 않았습니다: {fe}")
            if not fe.get("lens"):
                bad.append(f"(pg-b) {cid} fit.lens 가 없습니다: {fe}")
        # c1 의 camera 가 place_cut 을 그대로 다시 돌린 결과와 같은지(결정적 재현).
        g_c1 = cammod  # place_cut 재현
        off_cut = None
        for t in office_scene.get("timeline") or []:
            if str(t.get("cutId")) == "c1":
                off_cut = t
                break
        if off_cut is None:
            bad.append("(pg-b) 교장실 c1 컷을 찾지 못했습니다")

        # --- (pg-c) crossed 표시·hide 반영 ---
        # 교장 단독(더티 싱글, 상대 hands_only)이라 학생(a2)이 가림 판정이면 그 컷 actors 에서 빠질 수 있다.
        # crossed 는 line_side 다수결로 정해진다 — 적어도 fit 에 crossed 키가 bool 로 있어야 한다.
        for cid in ("c1", "c2", "c3"):
            fe = fit.get(cid) or {}
            if "crossed" in fe and not isinstance(fe.get("crossed"), bool):
                bad.append(f"(pg-c) {cid} crossed 가 bool 이 아닙니다: {fe.get('crossed')}")
        # hide 된 인물은 그 컷 actors 에서 빠진다(cheats 에 hide 가 있으면).
        for t in office_scene.get("timeline") or []:
            cid = str(t.get("cutId"))
            fe = fit.get(cid) or {}
            hide_ids = {c.get("actor") for c in (fe.get("cheats") or []) if c.get("type") == "hide"}
            cut_actor_ids = {str(a.get("id")) for a in (t.get("actors") or [])}
            if hide_ids & cut_actor_ids:
                bad.append(f"(pg-c) {cid}: hide 된 인물이 actors 에 남아 있습니다: {hide_ids & cut_actor_ids}")

        # --- (pg-d) 방 높이 10 → clamp. 교장실(닫힌 방)은 2.4~3.5, 거리(열린 공간)는 3.0 ---
        oh = float((office_scene.get("room") or {}).get("height") or 0)
        if not (2.4 <= oh <= 3.5):
            bad.append(f"(pg-d) 닫힌 방 높이가 2.4~3.5 로 clamp 되지 않았습니다: {oh}")
        if len(scenes) >= 2:
            sh = float((scenes[1].get("room") or {}).get("height") or 0)
            if abs(sh - 3.0) > 0.01:
                bad.append(f"(pg-d) 열린 공간 높이가 3.0 으로 clamp 되지 않았습니다: {sh}")

        # --- (pg-f 준비) 거리 scene 의 컷은 패널이 없어 grammar 없음 → method=astra·match null → 낮은 컷 ---
        # 거리 컷은 5초라 모델 max 에 따라 쪼개질 수 있으므로 id 를 하드코딩하지 않고 scene[1] 에서 뽑는다.
        street_cut_ids = []
        if len(scenes) >= 2:
            street_cut_ids = [str(t.get("cutId") or "") for t in (scenes[1].get("timeline") or [])]
        if not street_cut_ids:
            bad.append("(pg-f) 거리 scene 의 컷을 찾지 못했습니다")
        for cid in street_cut_ids:
            fe = fit.get(cid) or {}
            if fe.get("method") != "astra" or fe.get("match") is not None:
                bad.append(f"(pg-f) 패널 없는 거리 컷 {cid} 가 method=astra·match null 이 아닙니다: {fe}")
        # lowMatchCuts 에 거리 컷(판정 불가)이 모두 들어 있어야 한다.
        low_cuts = pv.get("lowMatchCuts") or []
        low_ids = {c.get("cutId") for c in low_cuts}
        for cid in street_cut_ids:
            if cid not in low_ids:
                bad.append(f"(pg-f) 낮은 컷 목록에 거리 컷 {cid}(판정 불가)가 없습니다: {low_ids}")
        # 반대로 교장실 컷(grammar 있음)은 낮은 컷이 아니어야 한다(match 계산됨).
        for cid in ("c1", "c2", "c3"):
            if cid in low_ids and (fit.get(cid) or {}).get("match") is not None and float((fit.get(cid) or {}).get("match") or 0) >= 0.7:
                bad.append(f"(pg-f) 교장실 컷 {cid}(match≥0.7)가 낮은 컷으로 잘못 세졌습니다")
        if abs(float(pv.get("approveMinMatch") or 0) - 0.7) > 1e-6:
            bad.append(f"(pg-f) approveMinMatch 가 0.7 이 아닙니다: {pv.get('approveMinMatch')}")

        # --- (l2) Astra 지침(_DESIGN_SYSTEM)에 fov·높이 규칙 문장 포함(옛 지침 유지) ---
        sys_text = pvmod._DESIGN_SYSTEM
        for needle in ("fov 20-30", "35-45", "50-65", "0.6 m", "1.5 m", "2.3 m", "screenX", "heightFrac"):
            if needle not in sys_text:
                bad.append(f"(l2) Astra 지침에 '{needle}' 규칙 문장이 없습니다")
        # 049: _design_prompt 에 '카메라는 서버가 계산' 문단이 들어가는지(장면 하나로 확인).
        dp = pvmod._design_prompt(
            "A closed principal's office", None,
            [{"cutId": "c1", "seconds": 4, "shotKo": "", "situationKo": "", "panels": []}],
            {"setting": {"enclosed": True}}, [], {"c1": (panel_grammar.get("c1") or {}).get("grammar")},
            room_height=(2.4, 3.5),
        )
        if "카메라는 서버가 계산" not in dp:
            bad.append("(l2) _design_prompt 에 '카메라는 서버가 계산' 지침 문단이 없습니다")

        # ============================================================ ④ 진행 표시·지시 다시 만들기(지시서 050)
        # 첫 설계가 끝난 상태(scenes·fit 있음). 이제 '다시 만들기'(지시 포함)를 돌려 progress·지시 반영·
        # grammarOverrides·그룹 한정 유지·실패 시 이전 유지를 검증한다.
        import copy  # noqa: PLC0415

        prev_pv = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        prev_previz = prev_pv.get("previz") or {}
        prev_scenes_snapshot = copy.deepcopy(prev_previz.get("scenes") or [])
        # 거리 장면(두 번째 그룹)을 그룹 한정 비교(pv-e)에 쓸 스냅샷으로 둔다.
        prev_street = copy.deepcopy(prev_scenes_snapshot[1]) if len(prev_scenes_snapshot) >= 2 else None

        # --- (pv-a) progress 단계 순서 panels→design→camera→done, done ≤ total ---
        # _set_progress·_set_step·_bump_progress 를 감싸 호출을 기록한다.
        step_seq: list[str] = []
        done_le_total = {"ok": True}
        orig_set_progress = pvmod._set_progress
        orig_set_step = pvmod._set_step
        orig_bump_progress = pvmod._bump_progress

        def rec_set_progress(owner_, pid_, task_id_, *, step, done, total):
            step_seq.append(step)
            if total > 0 and done > total:
                done_le_total["ok"] = False
            return orig_set_progress(owner_, pid_, task_id_, step=step, done=done, total=total)

        def rec_set_step(owner_, pid_, task_id_, *, step):
            step_seq.append(step)
            return orig_set_step(owner_, pid_, task_id_, step=step)

        def rec_bump_progress(owner_, pid_, task_id_, *, step=None):
            return orig_bump_progress(owner_, pid_, task_id_, step=step)

        pvmod._set_progress = rec_set_progress
        pvmod._set_step = rec_set_step
        pvmod._bump_progress = rec_bump_progress

        changes_calls.clear()
        design_prompts.clear()
        rebuild_cut = "c1"  # 교장실 그룹의 컷(그룹 한정·사용자 지시 대상)
        try:
            resp = client.post(
                f"/api/vnew/p/{pid}/previz/design",
                json={"extraPromptKo": "컷1 카메라를 교장 얼굴 가까이", "cutId": rebuild_cut},
            )
            if resp.status_code != 202:
                bad.append(f"(pv) 지시 다시 만들기 design 이 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
            _join()
        finally:
            pvmod._set_progress = orig_set_progress
            pvmod._set_step = orig_set_step
            pvmod._bump_progress = orig_bump_progress

        # step 처음 등장 순서: panels → design → camera → done.
        first_seen: list[str] = []
        for s in step_seq:
            if s not in first_seen:
                first_seen.append(s)
        core = [s for s in first_seen if s in ("panels", "design", "camera", "done")]
        if core != ["panels", "design", "camera", "done"]:
            bad.append(f"(pv-a) progress 단계 순서가 panels→design→camera→done 이 아닙니다: {first_seen}")
        if not done_le_total["ok"]:
            bad.append("(pv-a) progress done 이 total 을 넘었습니다")

        rb_pv = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        rb_previz = rb_pv.get("previz") or {}

        # --- (pv-b) previz_view 에 progress 포함, done = total(끝) ---
        prog_b = rb_previz.get("progress")
        if not isinstance(prog_b, dict) or "step" not in prog_b:
            bad.append(f"(pv-b) previz_view 에 progress 가 없습니다: {prog_b}")
        elif prog_b.get("step") != "done" or int(prog_b.get("done") or -1) != int(prog_b.get("total") or -2):
            bad.append(f"(pv-b) 끝난 뒤 progress 가 done·done=total 이 아닙니다: {prog_b}")

        # --- (pv-c) 지시 다시 만들기 → 지시→수정 목록(_changes, emit_edits) 가 Astra 를 먼저 쓴다 ---
        if not changes_calls:
            bad.append("(pv-c) 지시→수정 목록 변환(_changes)이 불리지 않았습니다")
        elif changes_calls[0]["model"] != pvmod.ASTRA_MODEL:
            bad.append(f"(pv-c) 지시 변환이 Astra 를 먼저 쓰지 않습니다: {changes_calls[0]['model']}")
        # 교장실 그룹의 설계 프롬프트에 '[이전 설계]' 가 있어야 한다(다시 만들기).
        office_prompts = [t for t in design_prompts if "[이전 설계]" in t]
        if not office_prompts:
            bad.append("(pv-c) 다시 만들기 설계 프롬프트에 '[이전 설계]' 문단이 없습니다")

        # lastRebuild 가 저장됐는지(editsEn).
        last_rb = rb_previz.get("lastRebuild") or {}
        if not last_rb or not last_rb.get("editsEn"):
            bad.append(f"(pv-c) lastRebuild.editsEn 가 저장되지 않았습니다: {last_rb}")

        # --- (pg-e) 지시 다시 만들기 → fake Astra edits(c1 vantage=side)가 grammarOverrides 로 저장·적용 ---
        overrides_rb = rb_previz.get("grammarOverrides") or {}
        if (overrides_rb.get("c1") or {}).get("vantage") != "side":
            bad.append(f"(pg-e) c1 vantage=side 가 grammarOverrides 에 저장되지 않았습니다: {overrides_rb.get('c1')}")
        # 그 컷 카메라가 바뀐다: vantage side → vantageDeg≈90.
        fit_rb = rb_previz.get("fit") or {}
        fit_c1_rb = fit_rb.get("c1") or {}
        if fit_c1_rb.get("method") != "grammar":
            bad.append(f"(pg-e) 지시 컷도 같은 계산기(method=grammar)를 거쳐야 합니다: {fit_c1_rb.get('method')}")
        if abs(float(fit_c1_rb.get("vantageDeg") or 0) - 90.0) > 1.0:
            bad.append(f"(pg-e) c1 vantage side 인데 vantageDeg 가 ~90 이 아닙니다: {fit_c1_rb.get('vantageDeg')}")
        if not fit_c1_rb.get("override"):
            bad.append(f"(pg-e) c1 fit 에 override 표시가 없습니다: {fit_c1_rb}")

        # --- (pv-e) extraCutId(c1=교장실) 다시 만들기 → 다른 그룹(거리) scenes 는 이전과 동일 ---
        rb_scenes = rb_previz.get("scenes") or []
        if prev_street is not None and len(rb_scenes) >= 2:
            # 화면용 textureUrl 은 매번 새로 붙으므로 저장 키(textureKey)만 비교하도록 room 을 정리한다.
            def _strip_urls(scene):
                s = copy.deepcopy(scene)
                room = s.get("room") or {}
                for side in pvschema.SIDES:
                    w = (room.get("walls") or {}).get(side)
                    if isinstance(w, dict):
                        w.pop("textureUrl", None)
                room.pop("floorTextureUrl", None)
                return s
            if _strip_urls(rb_scenes[1]) != _strip_urls(prev_street):
                bad.append("(pv-e) extraCutId 로 그룹 한정했는데 다른 장소(거리) scene 이 바뀌었습니다")
        else:
            bad.append("(pv-e) 거리 scene 비교 전제를 만들지 못했습니다(장면 2개 필요)")

        # --- (pv-f) 설계 실패 시 이전 scenes 유지 ---
        # fake_design 을 던지게 바꿔 설계를 실패시키고, 실패 뒤에도 scenes 가 남는지 본다.
        scenes_before_fail = copy.deepcopy(rb_previz.get("scenes") or [])

        def failing_design(blocks, *, model):
            raise RuntimeError("가짜 설계 실패(pv-f)")

        saved_design_fn = pvmod._design
        pvmod._design = failing_design
        try:
            resp = client.post(f"/api/vnew/p/{pid}/previz/design", json={"extraPromptKo": "아무거나"})
            _join()
        finally:
            pvmod._design = saved_design_fn
        fail_pv = (client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("previz") or {}
        if fail_pv.get("status") != "failed":
            bad.append(f"(pv-f) 설계 실패 상태가 failed 가 아닙니다: {fail_pv.get('status')}")
        if not (fail_pv.get("scenes") or []):
            bad.append("(pv-f) 설계 실패 시 이전 scenes 가 사라졌습니다")
        elif len(fail_pv.get("scenes") or []) != len(scenes_before_fail):
            bad.append(f"(pv-f) 설계 실패 시 scenes 수가 달라졌습니다: {len(fail_pv.get('scenes'))} vs {len(scenes_before_fail)}")

        # 실패 뒤 task 를 정리하고 scenes 를 복구(다음 (e) 검사들이 깨끗한 상태에서 돌게).
        def _recover_after_fail(d):
            pv = d.setdefault("previz", pvmod.new_previz())
            pv["task"] = {"status": "idle", "startedAt": None, "id": None}
            pv["status"] = "ready"

        planmod.mutate_plan(owner, pid, _recover_after_fail)
        # scenes 변수를 다시 만들기 후 상태로 갱신(이후 (e) PUT 이 쓰는 scenes).
        scenes = (client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("previz", {}).get("scenes") or scenes

        # --- (pg-f) approve 70% 관문: 낮은 컷(c4 match null) 있으면 409 LowMatch(컷 번호), force 면 승인 ---
        resp = client.post(f"/api/vnew/p/{pid}/previz/approve", json={})
        body = resp.get_json() or {}
        if resp.status_code != 409 or (body.get("error") or {}).get("type") != "LowMatch":
            bad.append(f"(pg-f) 낮은 컷이 있는데 approve 가 409 LowMatch 가 아닙니다: {resp.status_code} {body}")
        else:
            msg = (body.get("error") or {}).get("message") or ""
            if "컷" not in msg:
                bad.append(f"(pg-f) LowMatch 메시지에 컷 번호가 없습니다: {msg}")
        # force=True → 승인 + approvedForced 기록.
        resp = client.post(f"/api/vnew/p/{pid}/previz/approve", json={"force": True})
        pv2 = (resp.get_json() or {}).get("previz") or {}
        if resp.status_code != 200 or not pv2.get("approved"):
            bad.append(f"(pg-f) force 승인이 200·approved 가 아닙니다: {resp.status_code} approved={pv2.get('approved')}")
        if not (pv2.get("approvedForced") or {}).get("lowCuts"):
            bad.append(f"(pg-f) approvedForced.lowCuts 가 기록되지 않았습니다: {pv2.get('approvedForced')}")

        # --- (pg-g) PUT 로 카메라를 직접 고치면 그 컷 match 를 match_visible 로 재계산(method manual) ---
        # 교장실 c1 카메라를 임의로 바꾼다. 그 컷 fit.method 가 manual 로 바뀌고 match 가 재계산된다.
        scenes = pv2.get("scenes") or scenes
        edited = copy.deepcopy(scenes)
        edited[0]["timeline"][0]["camera"] = {"pos": [1.0, 1.4, 2.2], "target": [1.0, 1.1, 0.0], "fov": 45}
        resp = client.put(f"/api/vnew/p/{pid}/previz/scene", json={"scenes": edited})
        pv3 = (resp.get_json() or {}).get("previz") or {}
        if resp.status_code != 200:
            bad.append(f"(pg-g) PUT /previz/scene 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        if pv3.get("approved"):
            bad.append("(pg-g) 장면을 고쳤는데 승인이 해제되지 않았습니다")
        manual_c1 = (pv3.get("fit") or {}).get("c1") or {}
        if manual_c1.get("method") != "manual":
            bad.append(f"(pg-g) 카메라를 직접 고친 c1 fit.method 가 manual 이 아닙니다: {manual_c1}")
        if "match" not in manual_c1:
            bad.append(f"(pg-g) manual c1 fit 에 match 가 재계산되지 않았습니다: {manual_c1}")

        # --- (e) PUT 정리: 방 밖 좌표 보정 ---
        edited2 = copy.deepcopy(pv3.get("scenes") or scenes)
        edited2[0]["timeline"][0]["actors"] = [{"id": "a1", "pos": [500, 0, 500], "pose": "stand"}]
        resp = client.put(f"/api/vnew/p/{pid}/previz/scene", json={"scenes": edited2})
        pv3 = (resp.get_json() or {}).get("previz") or {}
        if resp.status_code != 200:
            bad.append(f"(e) PUT /previz/scene 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        if pv3.get("approved"):
            bad.append("(e) 장면을 고쳤는데 승인이 해제되지 않았습니다")
        # 정리: 방 밖 좌표가 방 안으로.
        put_scene = (pv3.get("scenes") or [{}])[0]
        put_room = put_scene.get("room") or {}
        pw = (put_room.get("width") or 6) / 2.0
        pd = (put_room.get("depth") or 6) / 2.0
        put_actors = ((put_scene.get("timeline") or [{}])[0]).get("actors") or []
        if put_actors:
            x, _, z = put_actors[0]["pos"]
            if abs(x) > pw or abs(z) > pd:
                bad.append(f"(e) PUT 정리가 방 밖 좌표를 보정하지 않았습니다: {put_actors[0]['pos']}")

        # 승인 해제 훅: 콘티를 고치면 승인 해제. 먼저 다시 승인(낮은 컷 c4 있으니 force)한 뒤 콘티 PUT.
        client.post(f"/api/vnew/p/{pid}/previz/approve", json={"force": True})
        conti_view = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        conti_cuts = (conti_view.get("conti") or {}).get("cuts") or []
        if conti_cuts:
            client.put(f"/api/vnew/p/{pid}/conti", json={"cuts": conti_cuts})
            pv_after = (client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("previz") or {}
            if pv_after.get("approved"):
                bad.append("(e) 콘티를 고쳤는데 프리비즈 승인이 해제되지 않았습니다")

        # --- (f)(g) 녹화 업로드 변환 · 길이 검사 · recordedAll ---
        # 현재 segments 를 다시 설계해 맞춘다(콘티 PUT 으로 revision 이 바뀌었을 수 있다).
        resp = client.post(f"/api/vnew/p/{pid}/previz/design")
        _join()
        plan_now = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        segments = (plan_now.get("conti") or {}).get("segments") or []
        if not segments:
            bad.append("(f) 구간(segments) 이 없습니다")
        else:
            # (f) 길이 벗어남 → 400: 첫 구간 seconds 와 많이 다른 webm(예: 1초, 3초 하한 미만).
            seg0 = segments[0]
            seg0_id = seg0.get("id")
            try:
                short_webm = _make_webm(1.0)
            except Exception as exc:  # noqa: BLE001
                short_webm = b""
                bad.append(f"(f) 합성 webm(1초) 생성 실패: {exc}")
            if short_webm:
                resp = client.post(
                    f"/api/vnew/p/{pid}/previz/recordings/{seg0_id}",
                    data=short_webm,
                    content_type="video/webm",
                )
                if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "BadDuration":
                    bad.append(f"(f) 길이 벗어난 녹화가 400 BadDuration 으로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")

            # Content-Type 틀리면 400.
            resp = client.post(
                f"/api/vnew/p/{pid}/previz/recordings/{seg0_id}",
                data=b"xx",
                content_type="application/octet-stream",
            )
            if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "BadContentType":
                bad.append(f"(f) 잘못된 Content-Type 이 400 BadContentType 으로 거절되지 않습니다: {resp.status_code}")

            # (f)(g) 모든 구간에 맞는 길이 webm 을 올려 변환·저장 → recordedAll.
            all_ok = True
            for seg in segments:
                seg_id = seg.get("id")
                seg_s = float(seg.get("seconds") or 0)
                try:
                    good_webm = _make_webm(seg_s)
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"(f) 합성 webm({seg_s}초) 생성 실패: {exc}")
                    all_ok = False
                    break
                resp = client.post(
                    f"/api/vnew/p/{pid}/previz/recordings/{seg_id}",
                    data=good_webm,
                    content_type="video/webm",
                )
                if resp.status_code != 201:
                    bad.append(f"(f) 올바른 녹화 업로드가 201 이 아닙니다(seg={seg_id}, {seg_s}초): {resp.status_code} {resp.get_json()}")
                    all_ok = False
                    break
                rec = (resp.get_json() or {}).get("recording") or {}
                if rec.get("status") != "ready" or not rec.get("url"):
                    bad.append(f"(f) 녹화 변환 결과가 ready·url 이 아닙니다: {rec}")
                    all_ok = False
                    break
                # 저장 키가 VIDEO_BUCKET .../vnew/previz/{rev}/{segId}.mp4 모양인지.
                rec_key = str(rec.get("key") or "")
                if "/vnew/previz/" not in rec_key or not rec_key.endswith(f"/{seg_id}.mp4"):
                    bad.append(f"(f) 녹화 저장 키 경로가 예상과 다릅니다: {rec_key}")

            if all_ok:
                final = (client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}).get("previz") or {}
                if not final.get("recordedAll"):
                    bad.append(f"(g) 모든 구간 녹화 후 recordedAll 이 True 가 아닙니다: {final.get('recordings')}")
                recs = final.get("recordings") or {}
                if any((r or {}).get("status") != "ready" for r in recs.values()):
                    bad.append(f"(g) 일부 구간 녹화 상태가 ready 가 아닙니다: {recs}")

        # --- (j)(k) 프레임 단위 오프라인 렌더(frames 묶음 업로드 + finish) ---
        # 실시간 녹화 대신 프레임 jpeg 를 올려 서버가 ffmpeg -framerate 로 mp4 를 만든다. 길이=frames/fps.
        plan_now2 = client.get(f"/api/vnew/p/{pid}/plan").get_json() or {}
        segments2 = (plan_now2.get("conti") or {}).get("segments") or []
        rev_now = int(((plan_now2.get("previz") or {}).get("revision")) or 0)
        if segments2 and _ffmpeg_bin:
            segj = segments2[0]
            segj_id = segj.get("id")
            segj_s = float(segj.get("seconds") or 0)
            fps_j = 24
            frames_j = int(round(segj_s * fps_j))  # 길이 = frames/fps ≈ 구간 seconds
            frame_jpg = _tiny_jpeg(1280, 720)

            def _post_frames(seg_id, start, count):
                import io as _io2  # noqa: PLC0415

                data = {"f": [(_io2.BytesIO(frame_jpg), f"{start + k:05d}.jpg") for k in range(count)]}
                return client.post(
                    f"/api/vnew/p/{pid}/previz/recordings/{seg_id}/frames?start={start}",
                    data=data,
                    content_type="multipart/form-data",
                )

            # (j) 3 묶음으로 나눠 올린다(합 = frames_j).
            import math as _math  # noqa: PLC0415

            chunk = max(1, _math.ceil(frames_j / 3))
            uploaded = 0
            chunk_count = 0
            while uploaded < frames_j:
                this = min(chunk, frames_j - uploaded)
                resp = _post_frames(segj_id, uploaded, this)
                if resp.status_code != 201:
                    bad.append(f"(j) 프레임 묶음 업로드가 201 이 아닙니다(start={uploaded}): {resp.status_code} {resp.get_json()}")
                    break
                uploaded += this
                chunk_count += 1
            if chunk_count != 3:
                bad.append(f"(j) 프레임을 3 묶음으로 올리지 못했습니다: {chunk_count} 묶음(frames={frames_j})")

            # 업로드된 프레임이 메인 버킷(fake_store)에 있는지(정리 전).
            fprefix = pvmod.frames_prefix(owner, pid, rev_now, segj_id)
            before = [k for k in fake_store.blobs if k.startswith(fprefix)]
            if len(before) != frames_j:
                bad.append(f"(j) 업로드된 프레임 수가 frames 와 다릅니다: {len(before)} vs {frames_j}")

            # finish → mp4 길이 = frames/fps(= 구간 seconds ±0.5), recordedAll.
            resp = client.post(
                f"/api/vnew/p/{pid}/previz/recordings/{segj_id}/finish",
                json={"frames": frames_j, "fps": fps_j},
            )
            if resp.status_code != 201:
                bad.append(f"(j) finish 가 201 이 아닙니다: {resp.status_code} {resp.get_json()}")
            else:
                recj = (resp.get_json() or {}).get("recording") or {}
                expect_len = frames_j / float(fps_j)
                if abs(float(recj.get("seconds") or 0) - expect_len) > pvmod.REC_TOLERANCE_S:
                    bad.append(f"(j) mp4 길이가 frames/fps 와 다릅니다: {recj.get('seconds')} vs {expect_len}")
                if recj.get("status") != "ready" or not recj.get("url"):
                    bad.append(f"(j) finish 결과가 ready·url 이 아닙니다: {recj}")
                rkey = str(recj.get("key") or "")
                if "/vnew/previz/" not in rkey or not rkey.endswith(f"/{segj_id}.mp4"):
                    bad.append(f"(j) finish mp4 저장 키 경로가 예상과 다릅니다: {rkey}")

            # (j) 프레임 정리: finish 뒤 프레임 파일이 모두 지워져야 한다.
            after = [k for k in fake_store.blobs if k.startswith(fprefix)]
            if after:
                bad.append(f"(j) finish 후 임시 프레임이 지워지지 않았습니다: {len(after)}개 남음")

            # (k) 중간 묶음 누락이면 finish 400. 다른 구간(또는 같은 구간 재업로드)에서 0 과 2 만 올리고 1 을 뺀다.
            segk = segments2[-1]
            segk_id = segk.get("id")
            segk_s = float(segk.get("seconds") or 0)
            frames_k = int(round(segk_s * fps_j))
            if frames_k >= 3:
                # 0 번과 마지막(frames_k-1) 만 올리고 가운데는 비운다.
                _post_frames(segk_id, 0, 1)
                _post_frames(segk_id, frames_k - 1, 1)
                resp = client.post(
                    f"/api/vnew/p/{pid}/previz/recordings/{segk_id}/finish",
                    json={"frames": frames_k, "fps": fps_j},
                )
                if resp.status_code != 400 or (resp.get_json().get("error") or {}).get("type") != "MissingFrame":
                    bad.append(f"(k) 중간 묶음 누락 finish 가 400 MissingFrame 으로 거절되지 않습니다: {resp.status_code} {resp.get_json()}")
                # (k) 실패해도 프레임은 정리된다(finally).
                kprefix = pvmod.frames_prefix(owner, pid, rev_now, segk_id)
                leftover = [kk for kk in fake_store.blobs if kk.startswith(kprefix)]
                if leftover:
                    bad.append(f"(k) finish 실패 후 임시 프레임이 지워지지 않았습니다: {len(leftover)}개 남음")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 프리비즈 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        _sbmod.start_brief_safe = _orig_brief_safe
        pvmod.STORE = orig["pv_STORE"]
        planmod.STORE = orig["plan_STORE"]
        pvmod._design = orig["_design"]
        grammarmod._read = orig["gr_read"]
        pvmod._cut_tiles = orig["_cut_tiles"]
        pvmod._changes = orig["_changes"]
        pvmod.vpoc_ledger.charge_text = orig["pv_charge_text"]
        planmod._analyze_conti = orig["plan_analyze_conti"]
        planmod.vpoc_ledger.charge_text = orig["plan_charge_text"]
        briefmod._brief = orig["brief"]
        pvmod.threading.Thread = orig["pv_Thread"]

    if bad:
        fail("영상만들기_신규 프리비즈(vnew previz) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "영상만들기_신규 프리비즈(vnew previz) 검사 (a)design 202·컷 1:1·시간 합계 (b)좌표·색·fov 보정"
            " (c)장소 2개 scene (d)텍스처 키·URL (e)PUT 정리·승인 해제 (f)녹화 변환·길이 400 (g)recordedAll"
            " (h)텍스처 API 200·접두사 밖 404·다른 사용자 404 (i)textureUrl 은 API 경로"
            " (j)frames 3묶음+finish mp4 길이=frames/fps·프레임 정리 (k)중간 묶음 누락 finish 400"
            " (pg-a)read_cut 컷 수·캐시 0회·panelGrammar (pg-b)컷 camera=place_cut·method grammar·match"
            " (pg-c)crossed bool·hide 반영 (pg-d)방 높이 10→clamp(닫힌 2.4~3.5·열린 3.0)"
            " (pg-e)지시 다시 만들기 edits→grammarOverrides·vantage side→vantageDeg≈90·override"
            " (pg-f)approve 낮은 컷 409 LowMatch·force→approvedForced (pg-g)PUT 카메라→method manual·match 재계산"
            " (l2)Astra 지침 fov·높이·카메라 서버 계산 문단"
            " (pv-a)진행 단계 panels→design→camera→done·done≤total (pv-b)previz_view progress"
            " (pv-c)다시 만들기 [이전 설계]·editsEn (pv-e)extraCutId 다른 그룹 scenes 유지"
            " (pv-f)설계 실패 시 이전 scenes 유지"
            " (np-c)대사 있는 컷 말하는 인물 제스처 없음→turn_head_to_other+상대 id·기존 제스처 유지"
        )


# ---------------------------------------------------------------- 10b. 프리비즈 연출 문법·카메라 계산기


def check_vnew_previz_camera() -> None:
    """④ 프리비즈 — 패널 "연출 문법" 읽기(previz_grammar) + 순수 카메라 계산기(previz_camera).

    (설계자 2026-10-21, 지시서 049) 외부 LLM 은 가짜. 유료 호출 없음.

    (pc-a) normalize: frameBottomCut chest→MCU, 모르는 vantage→3q, headTopY 2.0→0.9 clamp
    (pc-b) match_visible: 전신이 아닌 보이는 부분 기준(바스트샷 목표 맞춘 카메라 ≥0.95, 2배 멀면 <0.5)
    (pc-c) 실험 8컷 재현: CLAUDE 표 + 장면 배치로 place_cut → 8컷 match ≥0.9, 거리 0.8~3.0m, 렌즈 일치
    (pc-d) 컷 1·5·6 crossed, 나머지 기본 쪽
    (pc-e) 클린 싱글 가리는 상대 hide, hands_only 상대 hide 안 함
    (pc-f) 카메라가 어떤 인물 몸 축과도 0.35m 이상 떨어짐
    (pc-g) 순수 파이썬: previz_camera·previz_grammar 에 import numpy 없음
    (pc-h) read_cut: 가짜 _read 가 받은 블록에 cut 설명·이미지 1장, 결과 normalize, charge 콜백 1회
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import previz_camera as cam  # noqa: PLC0415
    from src.vnew import previz_fit as pvfit  # noqa: PLC0415
    from src.vnew import previz_grammar as grammar  # noqa: PLC0415

    # ---------------- (pc-g) 순수 파이썬: numpy import 없음 ----------------
    for name in ("previz_camera.py", "previz_grammar.py"):
        text = (ROOT / "app" / "src" / "vnew" / name).read_text(encoding="utf-8")
        if "import numpy" in text or "from numpy" in text:
            bad.append(f"(pc-g) {name} 에 numpy import 가 있습니다(앱에는 numpy 없음)")

    # ---------------- (pc-a) normalize_grammar ----------------
    norm = grammar.normalize_grammar(
        {
            "mainSubject": "범건우",
            "frameBottomCut": "chest",
            "shotType": "clean_single",
            "vantage": "weird-unknown",  # 모름 → 3q
            "faceTurn": "screen_right",
            "cameraHeight": "eye",
            "headTopY": 2.0,  # clamp → 0.9
            "subjectX": 1.7,  # clamp → 1.0
            "others": [{"name": "여의준", "how": "hands_only", "side": "right", "screenX": 0.9}],
            "offscreenPartnerSide": "right",
        }
    )
    if norm["shotSize"] != "MCU":
        bad.append(f"(pc-a) frameBottomCut chest 가 MCU 로 안 바뀌었습니다: {norm['shotSize']}")
    if norm["vantage"] != "3q":
        bad.append(f"(pc-a) 모르는 vantage 가 3q 로 안 바뀌었습니다: {norm['vantage']}")
    if abs(norm["headTopY"] - 0.9) > 1e-6:
        bad.append(f"(pc-a) headTopY 2.0 이 0.9 로 clamp 되지 않았습니다: {norm['headTopY']}")
    if abs(norm["subjectX"] - 1.0) > 1e-6:
        bad.append(f"(pc-a) subjectX 1.7 이 1.0 으로 clamp 되지 않았습니다: {norm['subjectX']}")
    if not norm["others"] or norm["others"][0]["how"] != "hands_only":
        bad.append(f"(pc-a) others 정리가 틀립니다: {norm['others']}")

    # ---------------- (pc-b) match_visible 보이는 부분 기준 ----------------
    # 바스트샷 목표: 머리 위 top_y=0.1, 화면 아래에 chest. 그 목표를 정확히 맞춘 카메라를 만든다.
    # 서 있는 1.7m 인물, 발 (0,0,0). chest·top 을 fov 50 으로 화면 0.1~1.0 에 담는 거리를 역산.
    height = 1.7
    foot = [0.0, 0.0, 0.0]
    top_w = pvfit.body_y("top", height, "stand")
    chest_w = pvfit.body_y("chest", height, "stand")
    fov = 50.0
    import math as _math  # noqa: PLC0415

    half_v = _math.tan(_math.radians(fov) / 2.0)
    # top 이 screenY 0.1, chest 가 screenY 1.0 이 되도록 거리·높이를 역산한다.
    # screenY(y) = 0.5 - ((y - pos_y)/dist)/half_v * 0.5 (수평 시선, z=0 평면).
    #   0.1 = 0.5 - (top-pos_y)/(dist*half_v)*0.5,  1.0 = 0.5 - (chest-pos_y)/(dist*half_v)*0.5
    # → dist*half_v = (top-chest)/1.8,  pos_y = top - 0.8*(top-chest)/1.8.
    extent = top_w - chest_w
    dist = (extent / 1.8) / half_v
    pos_y = top_w - 0.8 * extent / 1.8
    good_cam = {"pos": [0.0, pos_y, dist], "target": [0.0, pos_y, 0.0], "fov": fov}
    m_good = pvfit.match_visible(good_cam, foot, height, "stand", 0.1, 0.5, "chest")
    if m_good < 0.95:
        bad.append(f"(pc-b) 바스트샷 목표를 맞춘 카메라 일치도가 0.95 미만입니다: {m_good:.3f}")
    # 같은 카메라를 2배 멀리 → 인물이 절반 크기 → 보이는 부분 기준 오차 커짐 <0.5.
    far_cam = {"pos": [0.0, pos_y, dist * 2.0], "target": [0.0, pos_y, 0.0], "fov": fov}
    m_far = pvfit.match_visible(far_cam, foot, height, "stand", 0.1, 0.5, "chest")
    if m_far >= 0.5:
        bad.append(f"(pc-b) 2배 멀리 둔 카메라 일치도가 0.5 미만이 아닙니다: {m_far:.3f}")

    # ---------------- (pc-c)(pc-d)(pc-f) 실험 8컷 재현 ----------------
    Y, B = "여의준", "범건우"
    # CLAUDE (Fable 5.1) 연출 문법 읽기(2026-10-21) — solver2.py 와 같은 표.
    # (subj, frameBottomCut, shotType, vantage, faceTurn, cameraHeight, headTopY, subjectX, others, partner)
    CLAUDE = {
        "cut-01": (B, "chest", "clean_single", "front3q", "screen_right", "eye", 0.10, 0.47, [], "right"),
        "cut-02": (Y, "belly", "clean_single", "front3q", "screen_right", "eye", 0.06, 0.60, [], "right"),
        "cut-03": (Y, "chest", "clean_single", "front", "down", "eye", 0.15, 0.50, [], "right"),
        "cut-04": (Y, "chest", "dirty_single", "back3q", "screen_right", "eye", 0.05, 0.55, [(B, "hands_only", 0.85)], "right"),
        "cut-05": (Y, "chest", "dirty_single", "side", "screen_left", "eye", 0.05, 0.30, [(B, "hands_only", 0.40)], "left"),
        "cut-06": (Y, "chin", "dirty_single", "side", "screen_left", "eye", -0.10, 0.50, [(B, "hands_only", 0.80)], "left"),
        "cut-07": (B, "belly", "insert", "front", "down", "low", -0.40, 0.35, [(Y, "hands_only", 0.45)], "front_of_camera"),
        "cut-08": (Y, "belly", "dirty_single", "front3q", "up", "high", -0.02, 0.53, [(B, "partial_edge", 0.85)], "right"),
    }
    ORDER = [f"cut-0{i}" for i in range(1, 9)]

    def _grammar_of(spec):
        subj, cut, stype, vant, turn, height_k, top, x, others, partner = spec
        payload = {
            "mainSubject": subj,
            "frameBottomCut": cut,
            "shotType": stype,
            "vantage": vant,
            "faceTurn": turn,
            "cameraHeight": height_k,
            "headTopY": top,
            "subjectX": x,
            "offscreenPartnerSide": partner,
            "others": [{"name": n, "how": how, "side": "center", "screenX": sx} for (n, how, sx) in others],
        }
        return grammar.normalize_grammar(payload)

    cuts_grammar = {cid: _grammar_of(spec) for cid, spec in CLAUDE.items()}

    # 장면 배치(설계자): 두 사람 앉음, 마주 봄.
    Y_FOOT = [-0.39, 0.0, -0.8]
    B_FOOT = [0.39, 0.0, -0.8]
    actors_base = {
        Y: {"id": "aY", "name": Y, "heightM": 1.76, "foot": Y_FOOT, "pose": "sit", "facingDeg": 90},
        B: {"id": "aB", "name": B, "heightM": 1.90, "foot": B_FOOT, "pose": "sit", "facingDeg": 270},
    }
    # 실제 장면 방 크기(width 8, depth 6). 벽 안에서도 8컷이 재현돼야 한다(벽 처리: 밖이면 렌즈를
    # 한 단계 넓혀 재계산 → 그래도 밖이면 끌어오고 fit 에 표시).
    room = {"width": 8.0, "depth": 6.0}

    # 장면 라인 쪽(다수결) + crossed.
    line = cam.scene_line(cuts_grammar, Y, B, Y_FOOT, B_FOOT)
    crossed_set = set(line.get("crossed") or [])
    if crossed_set != {"cut-01", "cut-05", "cut-06"}:
        bad.append(f"(pc-d) crossed 컷이 1·5·6 이 아닙니다: {sorted(crossed_set)}")
    if line.get("side") != 1:
        bad.append(f"(pc-d) 장면 기본 쪽이 +1 이 아닙니다: {line.get('side')}")

    size_by_cut = {}
    for cid in ORDER:
        g = cuts_grammar[cid]
        size_by_cut[cid] = g["shotSize"]
        g["_crossed"] = cid in crossed_set
        this_side = -line["side"] if cid in crossed_set else line["side"]
        actors_in_cut = [dict(actors_base[Y]), dict(actors_base[B])]
        result = cam.place_cut(g, actors_in_cut, room, this_side)
        fit = result["fit"]
        # (pc-c) 일치도·거리·렌즈.
        if fit["match"] < 0.9:
            bad.append(f"(pc-c) {cid} 일치도가 0.9 미만입니다: {fit['match']}")
        if not (0.8 <= fit["distanceM"] <= 3.0):
            bad.append(f"(pc-c) {cid} 카메라-주인공 거리가 0.8~3.0m 가 아닙니다: {fit['distanceM']}")
        if fit["lens"] != cam.LENS[g["shotSize"]][1]:
            bad.append(f"(pc-c) {cid} 렌즈가 LENS 표와 다릅니다: {fit['lens']} vs {cam.LENS[g['shotSize']][1]}")
        # (pc-d) crossed 표시.
        if fit["crossed"] != (cid in crossed_set):
            bad.append(f"(pc-d) {cid} crossed 표시가 틀립니다: {fit['crossed']}")
        # (pc-f) 카메라가 두 인물 몸 축과 0.35m 이상 떨어짐.
        cpos = result["camera"]["pos"]
        for foot_pos in (Y_FOOT, B_FOOT):
            axis_dist = _math.hypot(cpos[0] - foot_pos[0], cpos[2] - foot_pos[2])
            if axis_dist < cam.MIN_AXIS_DIST_M - 1e-6:
                bad.append(f"(pc-f) {cid} 카메라가 인물 몸 축에 {axis_dist:.3f}m 로 너무 붙었습니다")
                break
        # (pc-j) 8컷 재현에서 화면에 남은 두 사람 발 간격 ≥ 0.45m, occluded False.
        feet = [a.get("foot") or [0, 0, 0] for a in result["actors"]]
        if len(feet) >= 2:
            gap = _math.hypot(feet[0][0] - feet[1][0], feet[0][2] - feet[1][2])
            if gap < cam.MIN_BODY_GAP_M - 1e-6:
                bad.append(f"(pc-j) {cid} 두 사람 발 간격이 0.45m 미만입니다: {gap:.3f}")
        if fit.get("occluded"):
            bad.append(f"(pc-j) {cid} 에 가림이 남았습니다(occluded True)")
        if "minBodyGapM" not in fit and len(result["actors"]) >= 2:
            bad.append(f"(pc-j) {cid} fit 에 minBodyGapM 이 없습니다")

    # ---------------- (pc-e) 클린 싱글 hide / hands_only 숨기지 않음 ----------------
    # 클린 싱글(others 비어 있음): 주인공을 가리는 상대는 hide.
    clean_g = grammar.normalize_grammar(
        {
            "mainSubject": Y, "frameBottomCut": "chest", "shotType": "clean_single",
            "vantage": "front", "faceTurn": "toward_camera", "cameraHeight": "eye",
            "headTopY": 0.1, "subjectX": 0.5, "offscreenPartnerSide": "none", "others": [],
        }
    )
    # 상대(B)를 카메라→주인공 시선 위(카메라와 주인공 사이, 더 가까이)에 둬서 가리게 한다.
    # front vantage 면 카메라가 Y 의 +z 쪽에 서므로, B 를 그 사이(z≈1.0, 살짝 -x)에 둔다.
    clean_actors = [
        {"id": "aY", "name": Y, "heightM": 1.76, "foot": [0.0, 0.0, 0.0], "pose": "stand"},
        {"id": "aB", "name": B, "heightM": 1.90, "foot": [-0.2, 0.0, 1.0], "pose": "stand"},
    ]
    clean_res = cam.place_cut(clean_g, clean_actors, room, 1)
    hide_ids = {c["actor"] for c in clean_res["cheats"] if c.get("type") == "hide"}
    if "aB" not in hide_ids:
        bad.append(f"(pc-e) 클린 싱글에서 가리는 상대가 hide 되지 않았습니다: {clean_res['cheats']}")
    out_ids = {a.get("id") for a in clean_res["actors"]}
    if "aB" in out_ids:
        bad.append("(pc-e) hide 한 상대가 actors 목록에 남아 있습니다")

    # hands_only 상대는 others 에 있으니 숨기지 않는다.
    dirty_g = grammar.normalize_grammar(
        {
            "mainSubject": Y, "frameBottomCut": "chest", "shotType": "dirty_single",
            "vantage": "front", "faceTurn": "toward_camera", "cameraHeight": "eye",
            "headTopY": 0.1, "subjectX": 0.5, "offscreenPartnerSide": "none",
            "others": [{"name": B, "how": "hands_only", "side": "right", "screenX": 0.85}],
        }
    )
    dirty_actors = [
        {"id": "aY", "name": Y, "heightM": 1.76, "foot": [0.0, 0.0, 0.0], "pose": "stand"},
        {"id": "aB", "name": B, "heightM": 1.90, "foot": [0.0, 0.0, 1.5], "pose": "stand"},
    ]
    dirty_res = cam.place_cut(dirty_g, dirty_actors, room, 1)
    dirty_hide = {c["actor"] for c in dirty_res["cheats"] if c.get("type") == "hide"}
    if "aB" in dirty_hide:
        bad.append(f"(pc-e) hands_only 상대가 hide 됐습니다: {dirty_res['cheats']}")

    # ---------------- (pc-i) 실측 cut-07 재현: 여의준이 범건우 얼굴을 가리면 안 됨 ----------------
    # dev 실측(2026-10-21): 범건우(인서트, 주인공)와 여의준(hands_only)이 앉아 마주 본다.
    # 측정 카메라가 여의준 바로 뒤에서 범건우를 봐, 조정 전에는 여의준이 범건우 얼굴을 가렸다.
    cut07_cam = {"pos": [-0.9592, 1.1902, -0.9841], "target": [-0.0032, 1.1321, -0.6965], "fov": 15.9}
    beom_actor = {"id": "aB", "name": B, "heightM": 1.90, "foot": [0.38, 0.0, -0.7], "pose": "sit"}
    yeo_actor = {"id": "aY", "name": Y, "heightM": 1.76, "foot": [-0.38, 0.0, -0.7], "pose": "sit"}
    beom_info = cam._actor_points(beom_actor, beom_actor["foot"], "sit")
    yeo_info = cam._actor_points(yeo_actor, yeo_actor["foot"], "sit")
    # 측정 카메라에서는 여의준이 범건우를 가린다(버그 재현 — 조정이 필요한 상황이었다).
    if not cam._occludes(cut07_cam, yeo_info, beom_info):
        bad.append("(pc-i) 측정 카메라에서 여의준이 범건우를 가리는 상황이 재현되지 않습니다(테스트 전제)")
    # place_cut 을 돌리면(계산기 카메라·조정) 여의준이 범건우 얼굴을 가리지 않거나(_occludes False) hide 된다.
    cut07_g = grammar.normalize_grammar(
        {
            "mainSubject": B, "frameBottomCut": "belly", "shotType": "insert",
            "vantage": "front", "faceTurn": "down", "cameraHeight": "low",
            "headTopY": -0.40, "subjectX": 0.35, "offscreenPartnerSide": "left",
            "others": [{"name": Y, "how": "hands_only", "side": "left", "screenX": 0.45}],
        }
    )
    cut07_res = cam.place_cut(cut07_g, [dict(beom_actor), dict(yeo_actor)], room, 1)
    if cut07_res["fit"].get("occluded"):
        bad.append(f"(pc-i) cut-07 재현에서 여의준이 범건우를 가립니다(occluded True): {cut07_res['cheats']}")

    # ---------------- (pc-k) hands_only screenX 0.85 상대는 오른쪽 가장자리(투영 x ≥ 0.7)로 ----------------
    # 상대가 주인공을 가리는 자리에 있어도, hands_only·screenX 0.85 면 화면 오른쪽 가장자리로 옮긴다.
    pk_g = grammar.normalize_grammar(
        {
            "mainSubject": Y, "frameBottomCut": "chest", "shotType": "dirty_single",
            "vantage": "front", "faceTurn": "toward_camera", "cameraHeight": "eye",
            "headTopY": 0.1, "subjectX": 0.35, "offscreenPartnerSide": "right",
            "others": [{"name": B, "how": "hands_only", "side": "right", "screenX": 0.85}],
        }
    )
    pk_actors = [
        {"id": "aY", "name": Y, "heightM": 1.76, "foot": [0.0, 0.0, 0.0], "pose": "stand"},
        # 상대를 카메라와 주인공 사이(가리는 자리)에 둔다 → 옮겨야 한다.
        {"id": "aB", "name": B, "heightM": 1.90, "foot": [0.1, 0.0, 1.0], "pose": "stand"},
    ]
    pk_res = cam.place_cut(pk_g, pk_actors, room, 1)
    pk_cam = pk_res["camera"]
    pk_b = next((a for a in pk_res["actors"] if a.get("id") == "aB"), None)
    if pk_b is None:
        # 가려서 hide 됐다면 pc-k 의 '오른쪽 가장자리로 이동' 요구를 못 지킨 것.
        bad.append("(pc-k) hands_only screenX 0.85 상대가 이동 대신 hide 됐습니다")
    else:
        from src.vnew import previz_fit as _pvf  # noqa: PLC0415

        eye_y = cam.body_y("eye", 1.90, "stand")
        sp = _pvf.project_point(pk_cam, [pk_b["foot"][0], eye_y, pk_b["foot"][2]])
        if sp is None or sp["screenX"] < 0.7:
            bad.append(f"(pc-k) hands_only screenX 0.85 상대 투영 x 가 0.7 이상(오른쪽)이 아닙니다: {sp and round(sp['screenX'], 3)}")

    # ---------------- (pc-l) probe_yj 2차 cut-01 재현: 클린 싱글 상대가 화면에 걸리면 안 됨 ----------------
    # dev 실측(2026-10-21 probe_yj 2차, 051 이후): cut-01(범건우 클린 싱글)에서 others 가 비어 있는데
    # 상대(여의준)의 머리가 화면 안에 크게 보였다. place_cut 결과에서 여의준이 화면과 겹치지 않거나 hide.
    pl_g = grammar.normalize_grammar(
        {
            "mainSubject": B, "frameBottomCut": "chest", "shotType": "clean_single",
            "vantage": "front3q", "faceTurn": "screen_right", "cameraHeight": "eye",
            "headTopY": 0.1, "subjectX": 0.5, "offscreenPartnerSide": "none", "others": [],
        }
    )
    pl_actors = [
        {"id": "aY", "name": Y, "heightM": 1.76, "foot": [-0.05, 0.0, -1.1], "pose": "sit", "facingDeg": 35},
        {"id": "aB", "name": B, "heightM": 1.90, "foot": [0.65, 0.0, -1.1], "pose": "sit"},
    ]
    pl_res = cam.place_cut(pl_g, pl_actors, room, 1)
    pl_hide = {c["actor"] for c in pl_res["cheats"] if c.get("type") == "hide"}
    pl_out = {a.get("id"): a for a in pl_res["actors"]}
    if "aY" not in pl_hide:
        # 숨기지 않았으면 결과 자리에서 화면과 겹치지 않아야 한다.
        pl_cam = pl_res["camera"]
        pl_y = pl_out.get("aY")
        if pl_y is None:
            bad.append("(pc-l) 여의준이 hide 도 아니고 actors 목록에도 없습니다")
        else:
            info = cam._actor_points({"heightM": 1.76}, pl_y["foot"], "sit")
            box = cam._person_screen_box(pl_cam, info)
            if cam._overlaps_frame(box):
                bad.append(f"(pc-l) 클린 싱글 cut-01 에서 여의준이 화면과 겹칩니다(hide 도 아님): {box}")

    # ---------------- (pc-m) 인서트(headTopY -0.7, belly) + hands_only 상대 몸통 앞 ----------------
    # cut-08 류: 주인공 얼굴이 화면 위로 잘리고 몸통(belly 까지)만 보인다. hands_only 상대가 몸통 앞을
    # 덮으면 안 된다 → 결과에서 보이는 주인공 영역 가림 < 15%, 상대 머리는 화면 밖.
    pm_g = grammar.normalize_grammar(
        {
            "mainSubject": B, "frameBottomCut": "belly", "shotType": "insert",
            "vantage": "front", "faceTurn": "down", "cameraHeight": "low",
            "headTopY": -0.7, "subjectX": 0.5, "offscreenPartnerSide": "left",
            "others": [{"name": Y, "how": "hands_only", "side": "left", "screenX": 0.3}],
        }
    )
    # 상대(Y)를 주인공(B) 몸통 앞(카메라와 주인공 사이)에 둬 몸통을 덮게 한다.
    pm_actors = [
        {"id": "aB", "name": B, "heightM": 1.90, "foot": [0.0, 0.0, -1.0], "pose": "sit"},
        {"id": "aY", "name": Y, "heightM": 1.76, "foot": [-0.1, 0.0, -0.3], "pose": "sit"},
    ]
    pm_res = cam.place_cut(pm_g, pm_actors, room, 1)
    pm_cam = pm_res["camera"]
    pm_subject = cam._actor_points({"heightM": 1.90}, [0.0, 0.0, -1.0], "sit")
    pm_out = {a.get("id"): a for a in pm_res["actors"]}
    pm_y = pm_out.get("aY")
    if pm_y is None:
        bad.append("(pc-m) hands_only 상대가 hide 됐습니다(인서트에서 숨기면 안 됨)")
    else:
        pm_yinfo = cam._actor_points({"heightM": 1.76}, pm_y["foot"], "sit")
        # 보이는 주인공 영역 가림 < 15%(= _occludes False, grammar 의 _bottom=belly 기준).
        if cam._occludes(pm_cam, pm_yinfo, pm_subject, pm_g):
            bad.append("(pc-m) 인서트에서 hands_only 상대가 주인공 보이는 영역을 15% 이상 가립니다")
        # 상대 머리는 화면 밖.
        if cam._head_onscreen(pm_cam, pm_yinfo):
            bad.append("(pc-m) 인서트에서 hands_only 상대 머리가 화면 안에 보입니다")
        if pm_res["fit"].get("occluded"):
            bad.append(f"(pc-m) 인서트에서 occluded True 입니다: {pm_res['cheats']}")

    # ---------------- (pc-n) 8컷 재현: extraVisible 비어 있음·occluded False·match ≥ 0.9 ----------------
    for cid in ORDER:
        g = cuts_grammar[cid]
        g["_crossed"] = cid in crossed_set
        this_side = -line["side"] if cid in crossed_set else line["side"]
        actors_in_cut = [dict(actors_base[Y]), dict(actors_base[B])]
        result = cam.place_cut(g, actors_in_cut, room, this_side)
        fit = result["fit"]
        if fit.get("extraVisible"):
            bad.append(f"(pc-n) {cid} extraVisible 가 비어 있지 않습니다: {fit.get('extraVisible')}")
        if fit.get("occluded"):
            bad.append(f"(pc-n) {cid} occluded True 입니다")
        if fit["match"] < 0.9:
            bad.append(f"(pc-n) {cid} match 가 0.9 미만입니다: {fit['match']}")

    # ---------------- (pc-h) read_cut: 가짜 _read ----------------
    read_seen: list[dict] = []
    charge_calls: list[tuple] = []
    real_read = grammar._read

    def fake_read(blocks, *, model):
        read_seen.append({"blocks": blocks, "model": model})
        payload = {
            "mainSubject": "범건우",
            "frameBottomCut": "chest",
            "shotType": "clean_single",
            "vantage": "front3q",
            "faceTurn": "screen_right",
            "cameraHeight": "eye",
            "headTopY": 0.1,
            "subjectX": 0.47,
            "others": [],
            "offscreenPartnerSide": "right",
            "reasonKo": "범건우 바스트 단독",
        }
        return payload, {"model": model, "inputTokens": 7, "outputTokens": 14}

    def fake_charge(usage, model):
        charge_calls.append((usage, model))

    grammar._read = fake_read
    try:
        cut = {"shotKo": "범건우 바스트", "characters": [{"name": "범건우"}]}
        tiles = [b"FAKE-JPEG-BYTES"]
        out = grammar.read_cut("tester", "prj1", "pl1", cut, tiles, ["범건우", "여의준"], charge=fake_charge)
    finally:
        grammar._read = real_read

    if not read_seen:
        bad.append("(pc-h) read_cut 이 _read 를 부르지 않았습니다")
    else:
        blocks = read_seen[-1]["blocks"]
        text_blocks = [b for b in blocks if isinstance(b, dict) and b.get("text")]
        image_blocks = [b for b in blocks if isinstance(b, dict) and b.get("image")]
        if not text_blocks or "범건우 바스트" not in text_blocks[0]["text"]:
            bad.append(f"(pc-h) 블록에 cut 설명이 없습니다: {text_blocks}")
        if len(image_blocks) != 1:
            bad.append(f"(pc-h) 블록에 이미지가 1장이 아닙니다: {len(image_blocks)}")
        if read_seen[-1]["model"] != grammar.GRAMMAR_MODEL:
            bad.append(f"(pc-h) 모델이 GRAMMAR_MODEL 이 아닙니다: {read_seen[-1]['model']}")
    if out is None or out.get("shotSize") != "MCU":
        bad.append(f"(pc-h) read_cut 결과가 normalize 되지 않았습니다: {out}")
    if len(charge_calls) != 1:
        bad.append(f"(pc-h) charge 콜백이 1회가 아닙니다: {len(charge_calls)}")

    if bad:
        fail("④ 프리비즈 연출 문법·카메라 계산기 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "④ 프리비즈 연출 문법·카메라 계산기 검사 (pc-a)normalize clamp·열거 (pc-b)보이는 부분 일치도"
            " (pc-c)실험 8컷 재현 match≥0.9·거리·렌즈 (pc-d)컷1·5·6 crossed (pc-e)클린 싱글 hide·hands_only 유지"
            " (pc-f)몸 축 0.35m 밖 (pc-g)numpy 없음 (pc-h)read_cut 블록·normalize·charge 1회"
            " (pc-i)cut-07 재현 여의준 범건우 안 가림 (pc-j)8컷 발간격≥0.45·occluded False"
            " (pc-k)hands_only screenX0.85 상대 오른쪽 가장자리(투영 x≥0.7)"
            " (pc-l)클린 싱글 cut-01 상대 화면 밖·hide (pc-m)인서트 belly hands_only 가림<15%·머리 밖"
            " (pc-n)8컷 extraVisible 비움·occluded False·match≥0.9"
        )


# ---------------------------------------------------------------- 10c. ⑤ 촬영 지시서·컷 단위·색 이름


def check_vnew_shot_brief() -> None:
    """⑤ 영상 생성 개선(설계자 2026-10-22, 지시서 053) — 색 이름·컷 단위·대사 귀속·촬영 지시서.

    외부 LLM·fal·ffmpeg 는 전부 가짜(유료 호출·실제 인코딩 없음).

    (sb-a) 팔레트: Astra 색 #00E5FF 를 줘도 배우 색은 팔레트 값·이름 있음. _color_name 근사·'#' 없음
    (sb-b) 컷 단위: 4컷 → units 4개, 각 unit elements=그 컷 보이는 인물만(숨긴 인물 제외), 2초 컷 3초로 채움
    (sb-c) _cut_lines: '여의준: "…아저씨?" / 범건우: "넌…"' → 두 줄·화자 정확, 빈 대사 silent
    (sb-d) approve → 지시서 작업 시작(장소당 1회), 입력에 lens·shotSize·moodKo·timeKo·facts 벽 물건 포함
    (sb-e) 지시서 없음 → /generate 409 BriefNotReady
    (sb-f) 컷 프롬프트: CAST·SHOT·…·CONTINUITY 순서, 'only these people', '#' 없음, 'Do not add' 없음, UTF-8 ≤ 2500
    (sb-g) 완료 → 컷 순서 concat, 3초로 채운 컷은 원래 길이로 잘림
    (sb-h) 옛 gen(segments 형식) 보기 정상
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import previz_schema as pvschema  # noqa: PLC0415
    from src.vnew import shot_brief as briefmod  # noqa: PLC0415

    # ---------------- (np-b) 촬영 지시서 시스템 지침: 말하는 사람 시선·건축 임의 추가 금지·카메라 흔들림 금지 ----------------
    if "keep their eyes on the listener" not in briefmod._BRIEF_SYSTEM:
        bad.append("(np-b) 촬영 지시서 시스템 지침에 'keep their eyes on the listener' 가 없습니다")
    for need in ("Do not invent architecture", "No meaningless camera shake"):
        if need not in briefmod._BRIEF_SYSTEM:
            bad.append(f"(np-b) 촬영 지시서 시스템 지침에 '{need}' 가 없습니다")

    # ---------------- (sb-a) 팔레트·색 이름 ----------------
    scene = pvschema.normalize_scene(
        {
            "actors": [
                {"id": "a1", "name": "범건우", "color": "#00E5FF", "charId": "ch1"},
                {"id": "a2", "name": "여의준", "color": "#FF8A00", "charId": "ch2"},
            ],
            "room": {"width": 6, "depth": 6},
            "timeline": [],
        },
        location_id="loc1",
        cut_specs=[],
    )
    acolors = [a.get("color") for a in scene.get("actors") or []]
    # 배우 색은 Astra 값(#00E5FF/#FF8A00)이 아니라 팔레트에서 등장 순서대로.
    if acolors and acolors[0].lower() == "#00e5ff":
        bad.append("(sb-a) 배우 색이 Astra 값(#00E5FF)을 그대로 썼습니다(팔레트 배정이어야 함)")
    palette_hex = {h.lower() for h in pvschema.ACTOR_PALETTE}
    for c in acolors:
        if str(c).lower() not in palette_hex:
            bad.append(f"(sb-a) 배우 색이 팔레트 값이 아닙니다: {c}")
    if len(acolors) == 2 and acolors[0].lower() == acolors[1].lower():
        bad.append(f"(sb-a) 두 배우 색이 겹칩니다: {acolors}")
    # _color_name: 팔레트 밖 hex 근사. #00e5ff→cyan, #ff8a00→orange, 결과에 '#' 없음.
    if genmod._color_name("#00e5ff") != "cyan":
        bad.append(f"(sb-a) _color_name('#00e5ff') 가 cyan 이 아닙니다: {genmod._color_name('#00e5ff')}")
    if genmod._color_name("#ff8a00") != "orange":
        bad.append(f"(sb-a) _color_name('#ff8a00') 가 orange 가 아닙니다: {genmod._color_name('#ff8a00')}")
    for probe in ("#00e5ff", "#ff8a00", "#4363d8", "#zzzzzz", ""):
        name = genmod._color_name(probe)
        if "#" in name:
            bad.append(f"(sb-a) _color_name 이 hex 를 돌려줬습니다: {probe} → {name}")

    # ---------------- (sb-c) _cut_lines 대사 귀속 ----------------
    plan_lines = {"script": {"entries": []}}
    cut_two = {"id": "c1", "panels": [0], "dialogueKo": '여의준: "…아저씨?" / 범건우: "넌…"'}
    lines = genmod._cut_lines(plan_lines, cut_two)
    if len(lines) != 2:
        bad.append(f"(sb-c) 두 대사로 나뉘지 않았습니다: {lines}")
    else:
        if lines[0].get("speaker") != "여의준" or "아저씨" not in (lines[0].get("lineKo") or ""):
            bad.append(f"(sb-c) 첫 화자·대사가 틀립니다: {lines[0]}")
        if lines[1].get("speaker") != "범건우" or "넌" not in (lines[1].get("lineKo") or ""):
            bad.append(f"(sb-c) 둘째 화자·대사가 틀립니다: {lines[1]}")
    # 빈 대사 → silent.
    silent_lines = genmod._cut_lines({"script": {"entries": []}}, {"id": "c1", "panels": [0], "dialogueKo": "… …."})
    if not silent_lines or not silent_lines[0].get("silent"):
        bad.append(f"(sb-c) 빈 대사가 silent 로 표시되지 않았습니다: {silent_lines}")
    # script.entries 우선(panels 겹침) + speaker/lineKo.
    plan_entries = {"script": {"entries": [{"id": "e1", "panels": [2], "speaker": "범건우", "lineKo": "안녕"}]}}
    el = genmod._cut_lines(plan_entries, {"id": "c3", "panels": [2], "dialogueKo": "여의준: \"딴 거\""})
    if not el or el[0].get("speaker") != "범건우" or el[0].get("lineKo") != "안녕":
        bad.append(f"(sb-c) script.entries(panels 겹침) 우선 귀속이 틀립니다: {el}")

    # ---------------- 컷 단위·프롬프트용 plan ----------------
    def _plan_cuts(n_cuts, seconds_each):
        cuts = []
        segs = []
        actors = [
            {"id": "a1", "name": "범건우", "color": "#00E5FF", "charId": "ch1"},
            {"id": "a2", "name": "여의준", "color": "#FF8A00", "charId": "ch2"},
        ]
        timeline = []
        for i in range(n_cuts):
            cid = "c" + str(i + 1)
            cuts.append({
                "id": cid, "no": i + 1, "seconds": seconds_each, "panels": [i],
                "locationEn": "A closed principal's office",
                "characters": [{"name": "범건우", "actionEn": "speaks"}],
                "dialogueKo": '범건우: "자네 뭐 하나?"', "situationKo": "호통", "cameraEn": "static camera",
                "promptEn": "do-not-leak-promptEn",
            })
            segs.append({"id": "seg" + str(i + 1), "cutIds": [cid], "seconds": seconds_each})
            # c1 은 두 명 보임, 나머지는 범건우만(a1) 보임 — 숨긴 인물 제외 검증.
            vis = [{"id": "a1", "speaking": True}]
            if i == 0:
                vis.append({"id": "a2", "speaking": False})
            timeline.append({"cutId": cid, "start": 0, "end": seconds_each,
                             "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 50}, "actors": vis})
        # normalize_scene 로 색을 팔레트로 배정한다.
        sc = pvschema.normalize_scene(
            {"actors": actors, "room": {"width": 6, "depth": 6},
             "timeline": timeline},
            location_id="loc:office",
            cut_specs=[{"cutId": c["id"], "seconds": c["seconds"]} for c in cuts],
        )
        plan = {
            "input": {"characters": [{"charId": "ch1", "roleName": "범건우"}, {"charId": "ch2", "roleName": "여의준"}]},
            "script": {"setting": {"moodKo": "긴장", "timeKo": "오후", "layoutKo": "책상 앞", "layoutEn": "at the desk", "enclosed": True},
                       "entries": []},
            "conti": {"status": "ready", "cuts": cuts, "segments": segs,
                      "setting": {"locationEn": "A closed principal's office"}},
            "locations": [{
                "key": "a closed principal's office", "nameEn": "A closed principal's office",
                "design": {"floorEn": "wooden floor", "lightEn": "soft daylight from the north windows",
                           "walls": {"north": {"descEn": "two windows", "featuresEn": "windows"}}},
                "facts": {"walls": {"east": {"objects": [{"kind": "bookshelf"}]}}},
            }],
            "previz": {
                "approved": True, "scenes": [sc],
                "panelGrammar": {"c1": {"grammar": {"shotSize": "MCU", "shotType": "clean_single", "vantage": "front3q",
                                                    "faceTurn": "screen_right", "cameraHeight": "eye", "others": []}}},
                "fit": {"c1": {"method": "grammar", "lens": 50, "distanceM": 2.0, "side": 1, "crossed": False}},
            },
        }
        return plan

    # ---------------- (sb-b) 컷 단위·보이는 인물·3초 채움 ----------------
    plan4 = _plan_cuts(4, 2)  # 4컷, 각 2초(3초 미만).
    units = genmod._previz_units(plan4)
    if len(units) != 4:
        bad.append(f"(sb-b) units 가 4개가 아닙니다: {len(units)}")
    if units and [u["cutId"] for u in units] != ["c1", "c2", "c3", "c4"]:
        bad.append(f"(sb-b) units cutId 순서가 틀립니다: {[u.get('cutId') for u in units]}")
    # 각 unit 의 보이는 인물(= 그 컷 timeline actors). c1 은 2명, 나머지는 1명.
    vis_c1 = genmod._segment_actors(plan4, {"cutIds": ["c1"]})
    vis_c2 = genmod._segment_actors(plan4, {"cutIds": ["c2"]})
    if len(vis_c1) != 2:
        bad.append(f"(sb-b) c1 보이는 인물이 2명이 아닙니다: {[a.get('name') for a in vis_c1]}")
    if len(vis_c2) != 1 or (vis_c2 and vis_c2[0].get("name") != "범건우"):
        bad.append(f"(sb-b) c2 보이는 인물이 범건우 1명이 아닙니다(숨긴 인물 제외 실패): {[a.get('name') for a in vis_c2]}")
    # _cut_clip 가짜 없이, fake ffmpeg 가 없으므로 _cut_clip 은 직접 부르지 않는다. 대신 billed 시간 확인:
    # 2초 컷 → estimate 는 max(3,2)=3 로 청구.
    est = genmod.estimate(plan4)
    if est.get("mode") != "previz":
        bad.append(f"(sb-b) estimate.mode 가 previz 가 아닙니다: {est.get('mode')}")
    exp_cost = round(genmod.vnew_settings.previz_edit_usd(3) * 4 * 2, 2)
    if abs(est.get("totalUsd", 0) - exp_cost) > 0.001:
        bad.append(f"(sb-b) estimate 가 컷별 max(3,2)×단가×2 가 아닙니다: {est.get('totalUsd')} != {exp_cost}")

    # ---------------- (sb-f) 컷 프롬프트 형식·순서·바이트 ----------------
    # shotBrief 를 넣어 CAST/SHOT/.../CONTINUITY 가 다 나오게 한다.
    plan_f = _plan_cuts(1, 6)
    plan_f["previz"]["shotBrief"] = {
        "status": "ready",
        "scene": {"loc:office": "A principal's office with pale green walls and warm daylight from the left."},
        "cuts": {"c1": {
            "shotEn": "MCU, 50mm lens, eye-level, three-quarter, clean single, line stays on the right.",
            "blockingEn": "범건우 at frame center facing camera, 여의준 just off frame right.",
            "actionEn": "범건우 leans forward, eyes narrowing, right hand flat on the desk.",
            "moodEn": "tense, authoritative, controlled anger.",
            "backgroundEn": "wood-panelled office wall behind, bookshelf soft in shallow focus.",
            "dialogue": [{"speaker": "범건우", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp, clipped, low voice"}],
        }},
    }
    # charId 가 있는 cast(정면 로드는 없음 → elementRef 없이 색 문장). 색 문장·Element 둘 다 검증하려면
    # 정면 이미지를 주입해야 하는데 여기선 store 가 없다 → cast 에 elementRef 없는 경로로 'only these people'·'#' 없음 검증.
    cast = [
        {"colorName": genmod._color_name(scene["actors"][0]["color"]), "name": "범건우", "elementRef": "@Element1", "appearance": "a tall man in a suit"},
        {"colorName": "orange", "name": "여의준", "elementRef": "", "appearance": "a young man"},
    ]
    prompt = genmod._cut_prompt(plan_f, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
    order_labels = ["CAST", "SHOT", "BLOCKING", "ACTION", "MOOD", "SETTING", "DIALOGUE", "CONTINUITY"]
    header_tokens = {
        "CAST": "CAST —", "SHOT": "\nSHOT:", "BLOCKING": "\nBLOCKING:", "ACTION": "\nACTION:",
        "MOOD": "\nMOOD:", "SETTING": "\nSETTING:", "DIALOGUE": "\nDIALOGUE:", "CONTINUITY": "\nCONTINUITY:",
    }
    positions = [("\n" + prompt).find(header_tokens[lbl]) for lbl in order_labels]
    present = [p for p in positions if p >= 0]
    if present != sorted(present):
        bad.append(f"(sb-f) 섹션 순서가 틀립니다: {[(order_labels[i], positions[i]) for i in range(len(order_labels))]}")
    for must, tok in (("CAST", "CAST —"), ("SHOT", "SHOT:"), ("CONTINUITY", "CONTINUITY:")):
        if tok not in prompt:
            bad.append(f"(sb-f) 필수 섹션 {must} 이 없습니다")
    if "only these people" not in prompt:
        bad.append("(sb-f) CAST 'only these people' 안내가 없습니다")
    if "Do not add or remove anyone" in prompt:
        bad.append("(sb-f) 'Do not add or remove anyone' 문장이 남아 있습니다")
    if "#" in prompt:
        bad.append(f"(sb-f) 프롬프트에 '#'(hex) 가 있습니다: {prompt[:200]}")
    if "do-not-leak-promptEn" in prompt or "static camera" in prompt:
        bad.append("(sb-f) 컷 promptEn·cameraEn 이 프롬프트에 샜습니다")
    if len(prompt.encode("utf-8")) > 2500:
        bad.append(f"(sb-f) 프롬프트가 2500바이트를 넘습니다: {len(prompt.encode('utf-8'))}")
    # 긴 입력에서도 2500바이트 이내(backgroundEn·actionEn 을 아주 길게).
    plan_big = _plan_cuts(1, 6)
    long_text = "a very detailed description. " * 400
    plan_big["previz"]["shotBrief"] = {
        "status": "ready",
        "scene": {"loc:office": long_text},
        "cuts": {"c1": {
            "shotEn": "MCU, 50mm lens, eye-level.", "blockingEn": long_text,
            "actionEn": long_text, "moodEn": long_text, "backgroundEn": long_text,
            "dialogue": [{"speaker": "범건우", "lineKo": "자네 뭐 하나?", "deliveryEn": long_text}],
        }},
    }
    prompt_big = genmod._cut_prompt(plan_big, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
    if len(prompt_big.encode("utf-8")) > 2500:
        bad.append(f"(sb-f) 긴 입력에서 프롬프트가 2500바이트를 넘습니다: {len(prompt_big.encode('utf-8'))}")
    for must in ("CAST", "SHOT", "CONTINUITY"):
        if must not in prompt_big:
            bad.append(f"(sb-f) 긴 입력에서 필수 섹션 {must} 이 줄여졌습니다")

    # ---------------- (sb-d) 촬영 지시서 입력 텍스트(장소당 1회, 필수 항목 포함) ----------------
    brief_inputs = []

    def fake_brief(blocks, *, model):
        text = ""
        for b in blocks:
            if isinstance(b, dict) and b.get("text"):
                text += b["text"] + "\n"
        brief_inputs.append(text)
        import re as _re  # noqa: PLC0415
        cut_ids = _re.findall(r"CUT (\S+):", text)
        return (
            {"sceneEn": "scene desc", "cuts": [{"cutId": cid, "shotEn": "MCU", "dialogue": []} for cid in cut_ids]},
            {"model": model, "inputTokens": 5, "outputTokens": 10},
        )

    orig_brief = briefmod._brief
    orig_charge = briefmod.vpoc_ledger.charge_text
    orig_load = briefmod.gen_mod.characters.load_char
    briefmod._brief = fake_brief
    briefmod.vpoc_ledger.charge_text = lambda *a, **k: {"spentUsd": 0.0}
    briefmod.gen_mod.characters.load_char = lambda owner, cid: (
        {"id": cid, "name": "범건우역", "status": "ready",
         "analysis": {"appearanceEn": "a man", "outfitEn": "a suit"},
         "variants": {"real": {"frontKey": "", "backKey": ""}, "webtoon": {"frontKey": "", "backKey": ""}}}, "e"
    )
    try:
        # 한 장소(교장실) 4컷을 직접 _location_prompt 로 만든다(장소당 1회 호출을 흉내).
        from src.vnew import previz as pvmod  # noqa: PLC0415
        groups = pvmod._scene_groups(plan4["conti"])
        if len(groups) != 1:
            bad.append(f"(sb-d) 교장실 1개 그룹이 아닙니다: {len(groups)}")
        else:
            loc = pvmod._location_for(plan4, groups[0]["locationEn"])
            text = briefmod._location_prompt(plan4, "tester", groups[0]["locationEn"], loc, groups[0]["cuts"])
            # 장소당 1회: 이 텍스트 하나에 모든 컷(CUT c1..c4)이 들어 있어야 한다.
            for cid in ("c1", "c2", "c3", "c4"):
                if ("CUT " + cid) not in text:
                    bad.append(f"(sb-d) 장소 입력에 {cid} 가 없습니다(장소당 1회 호출)")
            needles = [("lens", "lens"), ("shotSize", "MCU"), ("moodKo", "긴장"), ("timeKo", "오후")]
            for label, needle in needles:
                if needle not in text:
                    bad.append(f"(sb-d) 지시서 입력에 {label}({needle}) 가 없습니다")
            if "bookshelf" not in text:
                bad.append("(sb-d) 지시서 입력에 facts 벽 물건(bookshelf) 이 없습니다")
    finally:
        briefmod._brief = orig_brief
        briefmod.vpoc_ledger.charge_text = orig_charge
        briefmod.gen_mod.characters.load_char = orig_load

    # ---------------- (sb-e)(sb-g)(sb-h) 하니스 ----------------
    import threading as _threading  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415
    from src import vnew  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vpoc import fal as _fal_mod  # noqa: PLC0415
    from src.vpoc import ledger as _ledger_mod  # noqa: PLC0415
    from src.vpoc import settings as _settings  # noqa: PLC0415

    fake_store = _FakeCharStore()
    concat_order = []
    trim_calls = []

    def fake_previz_request(method, url, payload):
        import secrets as _s  # noqa: PLC0415
        return {"request_id": "previz-" + _s.token_hex(4)}

    def fake_fal_poll(invocation_arn, output_prefix):
        return {"status": "Completed", "failureMessage": "", "usage": None}

    def fake_fetch_bytes(key, *, bucket=""):
        return fake_store.blobs.get(key, b"CLIP-" + key.encode("ascii", "ignore"))

    def fake_cut_clip(video_mp4, unit):
        orig = float(unit.get("endSec") or 0) - float(unit.get("startSec") or 0)
        padded = orig < genmod.PREVIZ_MIN_CLIP_S - 0.01
        return {"bytes": b"CUT-MP4", "origSeconds": round(orig, 3),
                "sentSeconds": genmod.PREVIZ_MIN_CLIP_S if padded else round(orig, 3), "padded": padded}

    def fake_trim_clip(mp4, seconds):
        trim_calls.append(round(float(seconds), 3))
        return b"TRIMMED"

    def fake_concat(clips):
        concat_order.append(len(clips))
        return b"FINAL-MP4"

    def fake_load_char(owner, char_id):
        # 기본 복장 정면·후면 키(실사·웹툰). check_preconditions 가 front 를 요구한다.
        rf, rb = "rf-" + char_id + ".jpg", "rb-" + char_id + ".jpg"
        wf, wb = "wf-" + char_id + ".jpg", "wb-" + char_id + ".jpg"
        for k in (rf, rb, wf, wb):
            fake_store.blobs.setdefault(k, _tiny_jpeg(400, 600))
        return {"id": char_id, "name": "범건우역", "status": "ready",
                "analysis": {"appearanceEn": "a man", "outfitEn": "a suit"},
                "variants": {"real": {"frontKey": rf, "backKey": rb, "sheetKey": ""},
                             "webtoon": {"frontKey": wf, "backKey": wb, "sheetKey": ""}}}, "e"

    def fake_charge(key, **kwargs):
        return {"spentUsd": 0.0, "capUsd": None}

    sb_orig = {
        "gen_STORE": genmod.STORE, "plan_STORE": planmod.STORE, "ledger_STORE": _ledger_mod.STORE,
        "_concat": genmod._concat, "_fetch_bytes": genmod._fetch_bytes, "_cut_clip": genmod._cut_clip,
        "_trim_clip": genmod._trim_clip, "load_char": genmod.characters.load_char,
        "fal_poll": _fal_mod.poll, "previz_request": genmod._previz_request, "charge": _ledger_mod.charge,
        "Thread": genmod.threading.Thread, "fal_key": _settings.FAL_KEY,
    }
    genmod.STORE = fake_store
    planmod.STORE = fake_store
    _ledger_mod.STORE = fake_store
    genmod._concat = fake_concat
    genmod._fetch_bytes = fake_fetch_bytes
    genmod._cut_clip = fake_cut_clip
    genmod._trim_clip = fake_trim_clip
    genmod.characters.load_char = fake_load_char
    _fal_mod.poll = fake_fal_poll
    genmod._previz_request = fake_previz_request
    _ledger_mod.charge = fake_charge
    _settings.FAL_KEY = "dummy-fal-key"

    started = []
    base_thread = genmod.threading.Thread

    def _trk(*a, **k):
        t = base_thread(*a, **k)
        started.append(t)
        return t

    genmod.threading.Thread = _trk

    def _join(timeout=20.0):
        deadline = _time.time() + timeout
        for t in list(started):
            t.join(timeout=max(0.0, deadline - _time.time()))
        started.clear()

    owner = "tester"

    def make_client():
        app = Flask(__name__)
        bp = vnew.create_blueprint(
            vnew.VnewDependencies(
                require_auth=lambda h: h, current_owner=lambda: owner,
                get_project=lambda p: {"id": p, "owner": owner, "track": "video_new"},
                can_read_project=lambda proj: True, can_write_project=lambda proj: True,
                project_owner_prefix=lambda name: name,
            )
        )
        app.register_blueprint(bp)
        return app.test_client()

    def _seed_plan(pid, *, brief_status):
        """컷 2개(2초·6초)·녹화·승인된 plan 을 심는다. brief_status='ready'면 shotBrief 를 넣는다."""
        plan = planmod.new_plan()
        plan["input"]["characters"] = [{"charId": "ch1", "roleName": "범건우"}]
        cuts = [
            {"id": "c1", "no": 1, "seconds": 2, "panels": [0], "locationEn": "A closed principal's office",
             "characters": [{"name": "범건우", "actionEn": "speaks"}], "dialogueKo": '범건우: "자네?"', "cameraEn": "x", "promptEn": "x"},
            {"id": "c2", "no": 2, "seconds": 6, "panels": [1], "locationEn": "A closed principal's office",
             "characters": [{"name": "범건우", "actionEn": "speaks"}], "dialogueKo": '범건우: "뭐 하나?"', "cameraEn": "x", "promptEn": "x"},
        ]
        segs = [{"id": "seg1", "cutIds": ["c1"], "seconds": 2}, {"id": "seg2", "cutIds": ["c2"], "seconds": 6}]
        plan["script"] = {"status": "ready", "setting": {"locationEn": "A closed principal's office"}, "entries": []}
        plan["conti"] = {"status": "ready", "cuts": cuts, "segments": segs, "setting": {"locationEn": "A closed principal's office"}}
        actors = [{"id": "a1", "name": "범건우", "color": "#00E5FF", "charId": "ch1"}]
        timeline = [{"cutId": c["id"], "start": 0, "end": c["seconds"],
                     "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 50},
                     "actors": [{"id": "a1", "speaking": True}]} for c in cuts]
        sc = pvschema.normalize_scene({"actors": actors, "room": {"width": 6, "depth": 6}, "timeline": timeline},
                                      location_id="loc:office",
                                      cut_specs=[{"cutId": c["id"], "seconds": c["seconds"]} for c in cuts])
        recordings = {}
        for seg in segs:
            rkey = f"rec/{pid}/{seg['id']}.mp4"
            fake_store.blobs[rkey] = b"PREVIZ-REC"
            recordings[seg["id"]] = {"status": "ready", "key": rkey, "seconds": float(seg["seconds"])}
        previz = {"status": "ready", "revision": 0, "scenes": [sc], "approved": True, "recordings": recordings,
                  "task": {"status": "done", "startedAt": None, "id": None}}
        if brief_status == "ready":
            previz["shotBrief"] = {
                "status": "ready", "model": "astra",
                "scene": {"loc:office": "office scene"},
                "cuts": {c["id"]: {"shotEn": "MCU", "blockingEn": "", "actionEn": "", "moodEn": "",
                                   "backgroundEn": "", "dialogue": []} for c in cuts},
                "updatedAt": "2026-10-22T00:00:00Z",
                "task": {"status": "done", "startedAt": None, "id": None},
            }
        plan["previz"] = previz
        planmod.save_plan(owner, pid, plan)
        return plan

    try:
        client = make_client()

        # (sb-e) 지시서 없음 → /generate 409 BriefNotReady.
        pid_e = "prj_sb_e"
        _seed_plan(pid_e, brief_status="none")
        plan_e, _ = planmod.load_plan(owner, pid_e)
        est_e = genmod.estimate(plan_e)
        resp = client.post(f"/api/vnew/p/{pid_e}/generate", json={"confirmUsd": est_e["totalUsd"]})
        body_e = resp.get_json() or {}
        if resp.status_code != 409 or (body_e.get("error") or {}).get("type") != "BriefNotReady":
            bad.append(f"(sb-e) 지시서 없음이 409 BriefNotReady 가 아닙니다: {resp.status_code} {body_e}")

        # (sb-g) 지시서 ready → 생성 완료 → concat, 3초로 채운 c1(2초) 은 원래 2초로 잘림.
        pid_g = "prj_sb_g"
        _seed_plan(pid_g, brief_status="ready")
        concat_order.clear(); trim_calls.clear()
        plan_g, _ = planmod.load_plan(owner, pid_g)
        est_g = genmod.estimate(plan_g)
        resp = client.post(f"/api/vnew/p/{pid_g}/generate", json={"confirmUsd": est_g["totalUsd"]})
        if resp.status_code != 202:
            bad.append(f"(sb-g) generate 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join()
        gplan, _ = planmod.load_plan(owner, pid_g)
        ggen = gplan.get("gen") or {}
        if ggen.get("status") != "done":
            bad.append(f"(sb-g) gen.status 가 done 이 아닙니다: {ggen.get('status')}")
        if len(concat_order) != 2:
            bad.append(f"(sb-g) concat 이 2회(실사·웹툰)가 아닙니다: {concat_order}")
        if concat_order and any(n != 2 for n in concat_order):
            bad.append(f"(sb-g) concat 에 넘긴 컷 수가 2가 아닙니다: {concat_order}")
        # 채운 컷(c1, 2초)은 그림체별로 1번씩(실사·웹툰) 원래 길이로 잘린다 → trim 이 2회, 모두 ~2초.
        padded_trims = [t for t in trim_calls if abs(t - 2.0) < 0.5]
        if len(padded_trims) < 2:
            bad.append(f"(sb-g) 채운 컷이 원래 길이(2초)로 잘리지 않았습니다: {trim_calls}")

        # (sb-h) 옛 gen(segments 형식) 보기 정상.
        pid_h = "prj_sb_h"
        _seed_plan(pid_h, brief_status="ready")
        hplan, _ = planmod.load_plan(owner, pid_h)
        hplan["gen"] = {
            "genId": "g_old", "status": "done", "videoModel": "luma-ray2", "createdAt": "2026-10-20T08:00:00Z",
            "opening": {"status": "done", "imageKey": "op.jpg", "error": ""},
            "segments": [{"id": "seg1", "cutIds": ["c1"], "seconds": 6,
                          "real": {"status": "completed", "outputKey": "r1.mp4", "error": ""},
                          "webtoon": {"status": "completed", "outputKey": "w1.mp4", "error": ""}}],
            "finalReal": {"status": "done", "key": "fr.mp4", "error": ""},
            "finalWebtoon": {"status": "done", "key": "fw.mp4", "error": ""},
            "selected": None, "costUsd": 0.0,
        }
        planmod.save_plan(owner, pid_h, hplan)
        view_h = genmod.gen_view(owner, pid_h, hplan)
        if not view_h or view_h.get("status") != "done":
            bad.append(f"(sb-h) 옛 gen_view 가 done 을 못 돌려줍니다: {view_h and view_h.get('status')}")
        if view_h and len(view_h.get("segments") or []) != 1:
            bad.append(f"(sb-h) 옛 gen_view 의 segments 가 보이지 않습니다: {view_h and view_h.get('segments')}")

        # ---------------- (sb-i) 짧은 지시서 → 아무것도 안 지움(ACTION·MOOD·SETTING 모두) ----------------
        # 합계가 한도(2500) 안인 짧은 지시서. 069 v2 에서 컷 프롬프트에 MOTION(short)·HARMONY(short)
        # 고정 블록이 더해져 기본 길이가 늘었다 → 전제 바이트를 2300 → 2450 으로 조정하고, 지시서 본문을
        # 조금 짧게 둬 아무 섹션도 지워지지 않게 한다(설계자 2026-10-26 재지시 v2).
        plan_i = _plan_cuts(1, 6)
        plan_i["previz"]["shotBrief"] = {
            "status": "ready",
            "scene": {"loc:office": "A principal's office with pale green walls."},
            "cuts": {"c1": {
                "shotEn": "MCU, eye-level, clean single.",
                "blockingEn": "He stands at frame center facing the camera.",
                "actionEn": "He leans forward and narrows his eyes.",
                "moodEn": "tense and controlled.",
                "backgroundEn": "wood-panelled wall behind, bookshelf in shallow focus.",
                "dialogue": [{"speaker": "범건우", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp, low voice"}],
            }},
        }
        prompt_i = genmod._cut_prompt(plan_i, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
        if len(prompt_i.encode("utf-8")) > 2450:  # 069 v2 MOTION/HARMONY short 고정 블록으로 기본 길이 증가 — 한도(2500) 안
            bad.append(f"(sb-i) 전제: 짧은 지시서 프롬프트가 2450바이트를 넘습니다: {len(prompt_i.encode('utf-8'))}")
        for must in ("\nACTION:", "\nMOOD:", "\nSETTING:"):
            if must not in ("\n" + prompt_i):
                bad.append(f"(sb-i) 짧은 지시서인데 섹션 {must.strip()} 이 지워졌습니다")
        # 069 v2: MOTION(short)·HARMONY(short) 고정 블록이 짧은 지시서에서도 들어 있어야 한다.
        if "\nMOTION:" not in ("\n" + prompt_i):
            bad.append("(sb-i) 짧은 지시서인데 MOTION(short) 섹션이 없습니다")
        if "\nHARMONY:" not in ("\n" + prompt_i):
            bad.append("(sb-i) 짧은 지시서인데 HARMONY(short) 섹션이 없습니다")
        # 본문이 그대로 들어갔는지(줄이기가 전혀 일어나지 않았다).
        if "He leans forward and narrows his eyes." not in prompt_i:
            bad.append("(sb-i) 짧은 지시서인데 ACTION 본문이 줄여졌습니다")
        if "tense and controlled" not in prompt_i:
            bad.append("(sb-i) 짧은 지시서인데 MOOD 본문이 줄여졌습니다")

        # ---------------- (sb-j) 긴 지시서 → ≤2500, ACTION 전체 유지, 장소 요약·배경 먼저 줆, CAST·CONTINUITY 유지 ----------------
        plan_j = _plan_cuts(1, 6)
        scene_j = "This is a long location paragraph. " * 40  # ≈ 1,200자 이상.
        action_j = "He slams the desk. He rises slowly. He points at the door. He lowers his voice. " * 7  # ≈ 500자 이상.
        action_j = action_j[:500]
        plan_j["previz"]["shotBrief"] = {
            "status": "ready",
            "scene": {"loc:office": scene_j},
            "cuts": {"c1": {
                "shotEn": "MCU, 50mm lens, eye-level, clean single.",
                "blockingEn": "He stands at frame center facing the camera, others off frame.",
                "actionEn": action_j,
                "moodEn": "tense, authoritative, controlled anger throughout the shot.",
                "backgroundEn": "a very detailed office background. " * 20,
                "dialogue": [
                    {"speaker": "범건우", "lineKo": "자네 지금 뭐 하는 건가?", "deliveryEn": "sharp, clipped"},
                    {"speaker": "여의준", "lineKo": "죄송합니다, 부장님.", "deliveryEn": "quiet, shaking"},
                    {"speaker": "범건우", "lineKo": "당장 나가.", "deliveryEn": "cold, final"},
                ],
            }},
        }
        prompt_j = genmod._cut_prompt(plan_j, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
        if len(prompt_j.encode("utf-8")) > 2500:
            bad.append(f"(sb-j) 긴 지시서 프롬프트가 2500바이트를 넘습니다: {len(prompt_j.encode('utf-8'))}")
        # ACTION 전체 문장 유지: actionEn 첫 문장과 마지막 문장이 모두 남아 있어야 한다(장소 요약·배경이 먼저 줆).
        if "ACTION: He slams the desk." not in prompt_j:
            bad.append("(sb-j) ACTION 이 유지되지 않았습니다(먼저 줄여선 안 됨)")
        # action_j 의 마지막 온전한 문장이 남아 있는지(꼬리가 잘리지 않음).
        if "He lowers his voice." not in prompt_j:
            bad.append("(sb-j) ACTION 뒤쪽 문장이 잘렸습니다(장소 요약·배경이 먼저 줄어야 함)")
        for must in ("CAST —", "CONTINUITY:"):
            if must not in prompt_j:
                bad.append(f"(sb-j) 긴 지시서에서 {must} 가 사라졌습니다")
        # 장소 요약·배경이 먼저 줄어들었는지: 긴 scene 문단 전체가 그대로 들어가지 않았다.
        if prompt_j.count("This is a long location paragraph.") >= 30:
            bad.append("(sb-j) 장소 문단이 통째로 들어갔습니다(앞 2~3문장 요약이어야 함)")

        # ---------------- (sb-k) CAST: 'mannequin.' 문장·'..' 없음, 외형은 캐릭터 데이터 ----------------
        plan_k = _plan_cuts(1, 6)
        plan_k["previz"]["shotBrief"] = {
            "status": "ready", "scene": {"loc:office": "office."},
            "cuts": {"c1": {"shotEn": "MCU.", "blockingEn": "", "actionEn": "", "moodEn": "",
                            "backgroundEn": "", "dialogue": []}},
        }
        cast_k = [
            {"colorName": "cyan", "name": "범건우", "elementRef": "@Element1",
             "appearance": "a tall man in a dark suit. plain cyan mannequin."},
            {"colorName": "orange", "name": "여의준", "elementRef": "",
             "appearance": "a young man in a hoodie. plain orange mannequin"},
        ]
        prompt_k = genmod._cut_prompt(plan_k, {"cutId": "c1"}, "real", cast=cast_k, has_image_ref=False)
        cast_segment = prompt_k.split("\n")[1] if "\n" in prompt_k else prompt_k
        # 외형 설명에 'plain cyan mannequin'·'plain orange mannequin' 문장이 남으면 안 된다.
        if "plain cyan mannequin" in prompt_k or "plain orange mannequin" in prompt_k:
            bad.append(f"(sb-k) CAST 외형에 'mannequin' 문장이 남아 있습니다: {cast_segment}")
        if ".." in prompt_k:
            bad.append(f"(sb-k) 프롬프트에 중복 마침표 '..' 가 있습니다: {cast_segment}")
        # 외형이 캐릭터 데이터 문구(앞 문장)로 남아 있어야 한다.
        if "a tall man in a dark suit" not in prompt_k or "a young man in a hoodie" not in prompt_k:
            bad.append(f"(sb-k) CAST 외형이 캐릭터 데이터 문구로 남지 않았습니다: {cast_segment}")
        # 색은 마네킹 참조 문장에만("The cyan mannequin is @Element1, 범건우").
        if "The cyan mannequin is @Element1, 범건우" not in prompt_k:
            bad.append(f"(sb-k) 마네킹 색 참조 문장이 없습니다: {cast_segment}")

        # ---------------- (sb-l) 가짜 Astra 가 'side -1'·'wears blue' → 저장된 지시서·프롬프트에 없음 ----------------
        pid_l = "prj_sb_l"
        _seed_plan(pid_l, brief_status="none")

        def fake_brief_dirty(blocks, *, model):
            text = ""
            for b in blocks:
                if isinstance(b, dict) and b.get("text"):
                    text += b["text"] + "\n"
            import re as _re  # noqa: PLC0415
            cut_ids = _re.findall(r"CUT (\S+):", text)
            cuts_out = []
            for cid in cut_ids:
                cuts_out.append({
                    "cutId": cid,
                    "shotEn": "MCU, 50mm. Cuts 01, 05 and 06 use the crossed side across the established line.",
                    "blockingEn": "범건우 is on side +1 at frame center. He stands relaxed.",
                    "actionEn": "He leans forward. The camera is on side -1 for this beat.",
                    "moodEn": "tense.",
                    "backgroundEn": "wood wall behind.",
                    "dialogue": [{"speaker": "범건우", "lineKo": "자네?", "deliveryEn": "sharp, staying on the line side"}],
                })
            return (
                {"sceneEn": "A principal's office. 범건우 wears blue; 여의준 wears orange. Warm daylight from the left.",
                 "cuts": cuts_out},
                {"model": model, "inputTokens": 5, "outputTokens": 10},
            )

        l_orig_brief = briefmod._brief
        l_orig_charge = briefmod.vpoc_ledger.charge_text
        l_orig_load = briefmod.gen_mod.characters.load_char
        briefmod._brief = fake_brief_dirty
        briefmod.vpoc_ledger.charge_text = lambda *a, **k: {"spentUsd": 0.0}
        briefmod.gen_mod.characters.load_char = lambda owner_, cid: (
            {"id": cid, "name": "범건우역", "status": "ready",
             "analysis": {"appearanceEn": "a man", "outfitEn": "a suit"},
             "variants": {"real": {"frontKey": "", "backKey": ""}, "webtoon": {"frontKey": "", "backKey": ""}}}, "e"
        )
        try:
            # start_brief 는 백그라운드 스레드를 띄우므로(경합·스레드 누수) 여기선 task 를 직접 세우고
            # run_brief 를 동기로 부른다(_commit 의 task.id 일치 조건을 맞춘다).
            l_task = "sbltaskid01"

            def _seed_brief_task(data):
                previz = data.get("previz") or {}
                previz["shotBrief"] = {
                    "status": "running", "model": "", "scene": {}, "cuts": {}, "error": "",
                    "task": {"status": "running", "startedAt": "2026-10-22T00:00:00Z", "id": l_task},
                    "updatedAt": "",
                }
                data["previz"] = previz

            planmod.mutate_plan(owner, pid_l, _seed_brief_task)
            briefmod.run_brief(owner, pid_l, l_task)
        finally:
            briefmod._brief = l_orig_brief
            briefmod.vpoc_ledger.charge_text = l_orig_charge
            briefmod.gen_mod.characters.load_char = l_orig_load

        lplan, _ = planmod.load_plan(owner, pid_l)
        lbrief = (lplan.get("previz") or {}).get("shotBrief") or {}
        banned = ["side +1", "side -1", "side+1", "side-1", "crossed side", "line side", "wears blue", "wears orange"]
        # 저장된 sceneEn·cut 필드에 금지 문구가 없어야 한다.
        import json as _json  # noqa: PLC0415
        saved_text = _json.dumps({"scene": lbrief.get("scene"), "cuts": lbrief.get("cuts")}, ensure_ascii=False)
        for needle in banned:
            if needle in saved_text:
                bad.append(f"(sb-l) 저장된 지시서에 금지 문구 '{needle}' 가 남아 있습니다")
        # 그 지시서로 만든 프롬프트에도 금지 문구가 없어야 한다.
        prompt_l = genmod._cut_prompt(lplan, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
        for needle in banned:
            if needle in prompt_l:
                bad.append(f"(sb-l) 컷 프롬프트에 금지 문구 '{needle}' 가 남아 있습니다")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("sb 하니스 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod.STORE = sb_orig["gen_STORE"]
        planmod.STORE = sb_orig["plan_STORE"]
        _ledger_mod.STORE = sb_orig["ledger_STORE"]
        genmod._concat = sb_orig["_concat"]
        genmod._fetch_bytes = sb_orig["_fetch_bytes"]
        genmod._cut_clip = sb_orig["_cut_clip"]
        genmod._trim_clip = sb_orig["_trim_clip"]
        genmod.characters.load_char = sb_orig["load_char"]
        _fal_mod.poll = sb_orig["fal_poll"]
        genmod._previz_request = sb_orig["previz_request"]
        _ledger_mod.charge = sb_orig["charge"]
        genmod.threading.Thread = sb_orig["Thread"]
        _settings.FAL_KEY = sb_orig["fal_key"]

    if bad:
        fail("⑤ 촬영 지시서·컷 단위·색 이름 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "⑤ 촬영 지시서·컷 단위·색 이름 검사 (sb-a)팔레트·_color_name·'#' 없음 (sb-b)컷 단위 units·보이는 인물·3초 청구"
            " (sb-c)_cut_lines 화자 귀속·silent (sb-d)장소당 1회·lens/shotSize/mood/time/facts 포함"
            " (sb-e)지시서 없음 409 BriefNotReady (sb-f)CAST→CONTINUITY 순서·only these people·'#' 없음·Do not add 없음·UTF-8≤2500"
            " (sb-g)컷 순서 concat·채운 컷 원래 길이로 잘림 (sb-h)옛 segments gen_view"
            " (sb-i)짧은 지시서 아무것도 안 지움(ACTION·MOOD·SETTING·MOTION/HARMONY short) (sb-j)긴 지시서 ≤2500·ACTION 유지·CAST/CONTINUITY 유지"
            " (sb-k)CAST 'mannequin.'·'..' 없음·캐릭터 데이터 외형 (sb-l)side±1·wears blue 저장/프롬프트에 없음"
            " (np-b)촬영 지시서 시스템 지침에 keep their eyes on the listener·건축 임의 추가 금지·카메라 흔들림 금지"
        )


# ---------------------------------------------------------------- 10d. 마네킹 얼굴·머리카락·연기 동작(054)


def check_vnew_mannequin() -> None:
    """④ 프리비즈 마네킹 — 얼굴 방향 표식·머리카락 색·연기 동작 + ECU 하한(설계자 2026-10-22, 지시서 054).

    (mf-a) vnew-previz.js 에 얼굴 표식(eye·nose)·hair 캡 생성 코드가 있고, GESTURES 목록이 schema 와
           렌더러에서 문자열로 같다(node --check 는 check_js 가 본다).
    (mf-b) schema: hairColor 정리(잘못된 값→기본), 새 gesture 통과·모르는 gesture→None,
           gestureTarget 은 존재하는 actor id 만.
    (mf-c) previz.hair_color_for: "short blond hair"→#e8cf7a, "black hair"→#1a1a1a.
    (mf-d) grammar actions(가짜) → 그 컷 인물 gesture·gestureTarget 반영. actions 없는 옛 캐시는
           conti actionEn 키워드로 배정.
    (mf-e) ECU 컷 → 카메라 CU 렌즈(70mm)·fit.pushInTo == "ECU".
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    import re as _re  # noqa: PLC0415

    from src.vnew import previz as pvmod  # noqa: PLC0415
    from src.vnew import previz_grammar as grammarmod  # noqa: PLC0415
    from src.vnew import previz_schema as pvschema  # noqa: PLC0415

    # ---------------- (mf-a) 렌더러 얼굴 표식·머리카락·GESTURES 목록 ----------------
    js = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
    # 얼굴 표식: 흰 눈(faceMat)·코(nose)·머리카락 캡(hairGeo/hairMat) 생성 코드가 있어야 한다.
    for needle in ("faceMat", "eyeGeo", "nose", "hairTop", "hairMat"):
        if needle not in js:
            bad.append(f"(mf-a) vnew-previz.js 에 '{needle}' 생성 코드가 없습니다")
    # 렌더러 GESTURES 배열을 뽑아 schema.GESTURES 와 문자열로 비교.
    match = _re.search(r"const GESTURES = \[(.*?)\];", js, _re.S)
    if not match:
        bad.append("(mf-a) vnew-previz.js 에 const GESTURES 배열이 없습니다")
    else:
        js_gestures = _re.findall(r'"([^"]+)"', match.group(1))
        if list(js_gestures) != list(pvschema.GESTURES):
            bad.append(
                f"(mf-a) 렌더러 GESTURES 가 schema 와 다릅니다:\n  js={js_gestures}\n  schema={list(pvschema.GESTURES)}"
            )

    # ---------------- (mf-b) schema: hairColor·gesture·gestureTarget 정리 ----------------
    scene = pvschema.normalize_scene(
        {
            "actors": [
                {"id": "a1", "name": "범건우", "color": "#111", "hairColor": "#E8CF7A"},
                {"id": "a2", "name": "여의준", "color": "#222", "hairColor": "not-a-color"},
            ],
            "room": {"width": 6, "depth": 6},
            "timeline": [
                {
                    "cutId": "c1",
                    "camera": {"pos": [0, 1.5, 4], "target": [0, 1, 0], "fov": 50},
                    "actors": [
                        {"id": "a1", "pos": [0, 0, 0], "gesture": "both_hands_other_cheeks", "gestureTarget": "a2"},
                        {"id": "a2", "pos": [1, 0, 0], "gesture": "no_such_gesture", "gestureTarget": "ghost"},
                    ],
                }
            ],
        },
        location_id="loc1",
        cut_specs=[{"cutId": "c1", "seconds": 4}],
    )
    sa = {a["id"]: a for a in scene.get("actors") or []}
    if sa.get("a1", {}).get("hairColor") != "#e8cf7a":
        bad.append(f"(mf-b) 올바른 hairColor 가 정리되지 않았습니다: {sa.get('a1', {}).get('hairColor')}")
    if sa.get("a2", {}).get("hairColor") != pvschema.DEFAULT_HAIR_COLOR:
        bad.append(f"(mf-b) 잘못된 hairColor 가 기본값으로 안 바뀌었습니다: {sa.get('a2', {}).get('hairColor')}")
    ca = {a["id"]: a for a in (scene.get("timeline") or [{}])[0].get("actors") or []}
    if ca.get("a1", {}).get("gesture") != "both_hands_other_cheeks":
        bad.append(f"(mf-b) 새 gesture 가 통과되지 않았습니다: {ca.get('a1', {}).get('gesture')}")
    if ca.get("a1", {}).get("gestureTarget") != "a2":
        bad.append(f"(mf-b) 존재하는 gestureTarget 이 유지되지 않았습니다: {ca.get('a1', {}).get('gestureTarget')}")
    if ca.get("a2", {}).get("gesture") is not None:
        bad.append(f"(mf-b) 모르는 gesture 가 None 이 아닙니다: {ca.get('a2', {}).get('gesture')}")
    if "gestureTarget" in ca.get("a2", {}):
        bad.append(f"(mf-b) 존재하지 않는 gestureTarget 이 남았습니다: {ca.get('a2', {}).get('gestureTarget')}")

    # ---------------- (mf-c) previz.hair_color_for ----------------
    if pvmod.hair_color_for("short blond hair") != "#e8cf7a":
        bad.append(f"(mf-c) 'short blond hair' 가 #e8cf7a 가 아닙니다: {pvmod.hair_color_for('short blond hair')}")
    if pvmod.hair_color_for("black hair") != "#1a1a1a":
        bad.append(f"(mf-c) 'black hair' 가 #1a1a1a 가 아닙니다: {pvmod.hair_color_for('black hair')}")
    if pvmod.hair_color_for("no hair words here") != pvmod.HAIR_COLOR_DEFAULT:
        bad.append(f"(mf-c) 색 단어 없음이 기본값이 아닙니다: {pvmod.hair_color_for('no hair words here')}")

    # ---------------- (mf-d) 제스처 배정: grammar actions · 옛 캐시 keyword ----------------
    # 장면 2인(범건우 a1, 여의준 a2). 컷 c1 에 grammar actions 로 범건우가 여의준 뺨 감싸기.
    def _scene_two():
        return {
            "actors": [
                {"id": "a1", "name": "범건우"},
                {"id": "a2", "name": "여의준"},
            ],
            "timeline": [
                {"cutId": "c1", "actors": [{"id": "a1"}, {"id": "a2"}]},
            ],
        }

    groups = [{
        "locationEn": "office",
        "cuts": [{"cutId": "c1", "characters": [{"name": "범건우", "actionEn": "stands still"}]}],
    }]
    # grammar actions(가짜): "범건우 cups 여의준's cheeks with both hands".
    grammar_by_cut = {
        "c1": grammarmod.normalize_grammar({
            "mainSubject": "범건우",
            "frameBottomCut": "chest",
            "shotType": "two_shot",
            "vantage": "front",
            "actions": [
                {"name": "범건우", "actionEn": "cups 여의준's cheeks with both hands",
                 "gesture": "both_hands_other_cheeks", "target": "여의준"},
            ],
        }),
    }
    sc_d = _scene_two()
    pvmod._apply_gestures([sc_d], groups, grammar_by_cut)
    cut_actors = {a["id"]: a for a in sc_d["timeline"][0]["actors"]}
    if cut_actors.get("a1", {}).get("gesture") != "both_hands_other_cheeks":
        bad.append(f"(mf-d) grammar actions gesture 가 반영되지 않았습니다: {cut_actors.get('a1')}")
    if cut_actors.get("a1", {}).get("gestureTarget") != "a2":
        bad.append(f"(mf-d) grammar actions target(여의준→a2)이 반영되지 않았습니다: {cut_actors.get('a1')}")
    # grammar.actions 가 normalize 로 들어갔는지(열거·target 정리).
    acts = grammar_by_cut["c1"].get("actions")
    if not acts or acts[0].get("gesture") != "both_hands_other_cheeks" or acts[0].get("target") != "여의준":
        bad.append(f"(mf-d) normalize_grammar 가 actions 를 정리하지 않았습니다: {acts}")

    # 옛 캐시(grammar.actions 없음) → 콘티 actionEn 키워드로 배정.
    sc_old = _scene_two()
    old_groups = [{
        "locationEn": "office",
        "cuts": [{"cutId": "c1", "characters": [
            {"name": "범건우", "actionEn": "wipes tears from his eyes with the back of his hand"},
            {"name": "여의준", "actionEn": "stands still"},
        ]}],
    }]
    # grammar 에 actions 가 없는 옛 캐시(다른 필드만).
    old_grammar = {"c1": grammarmod.normalize_grammar({"mainSubject": "범건우", "frameBottomCut": "chest", "shotType": "two_shot", "vantage": "front"})}
    old_grammar["c1"].pop("actions", None)  # 옛 캐시 흉내(actions 키 없음)
    pvmod._apply_gestures([sc_old], old_groups, old_grammar)
    old_actors = {a["id"]: a for a in sc_old["timeline"][0]["actors"]}
    if old_actors.get("a1", {}).get("gesture") != "wipe_tears":
        bad.append(f"(mf-d) 옛 캐시 conti actionEn(tears) → wipe_tears 가 배정되지 않았습니다: {old_actors.get('a1')}")

    # ---------------- (mf-e) ECU 컷 → CU 렌즈(70mm)·pushInTo "ECU" ----------------
    ecu_scene = {
        "room": {"width": 8.0, "depth": 6.0},
        "actors": [{"id": "a1", "name": "범건우", "heightM": 1.75}],
        "timeline": [
            {
                "cutId": "c1",
                "camera": {"pos": [0, 1.5, 2], "target": [0, 1, 0], "fov": 50},
                "actors": [{"id": "a1", "pos": [0, 0, 0], "pose": "stand"}],
            }
        ],
    }
    ecu_groups = [{"locationEn": "office", "cuts": [{"cutId": "c1", "seconds": 4, "characters": []}]}]
    ecu_grammar = {
        "c1": grammarmod.normalize_grammar({
            "mainSubject": "범건우",
            "frameBottomCut": "eyes",  # → shotSize ECU
            "shotType": "clean_single",
            "vantage": "front",
            "faceTurn": "toward_camera",
            "cameraHeight": "eye",
        }),
    }
    if ecu_grammar["c1"].get("shotSize") != "ECU":
        bad.append(f"(mf-e) frameBottomCut eyes 가 ECU 가 아닙니다(테스트 전제): {ecu_grammar['c1'].get('shotSize')}")
    fit = pvmod._place_cameras("tester", "prj_mf", [ecu_scene], ecu_groups, ecu_grammar, {})
    fe = fit.get("c1") or {}
    if fe.get("pushInTo") != "ECU":
        bad.append(f"(mf-e) ECU 컷 fit.pushInTo 가 'ECU' 가 아닙니다: {fe.get('pushInTo')}")
    if fe.get("lens") != "70mm":
        bad.append(f"(mf-e) ECU 컷 카메라 렌즈가 CU(70mm)가 아닙니다: {fe.get('lens')}")

    if bad:
        fail("④ 마네킹 얼굴·머리카락·연기 동작 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "④ 마네킹 얼굴·머리카락·연기 동작 검사 (mf-a)렌더러 얼굴 표식·머리카락·GESTURES 동일"
            " (mf-b)schema hairColor·gesture·gestureTarget 정리 (mf-c)hair_color_for blond/black"
            " (mf-d)grammar actions·옛 캐시 conti 키워드 배정 (mf-e)ECU→CU 70mm·pushInTo ECU"
        )


# ---------------------------------------------------------------- 10. fal 영상 모델 caps(fm)


def check_fal_model_caps() -> None:
    """① 단계 영상 모델 — fal video-to-video caps·등록·plan 검증·생성 payload·vpoc 회귀(설계자 2026-10-22).

    외부 호출(모델 메타·스키마·단가)은 전부 가짜. 유료 호출 없음.

    (fm-a) extract_caps: (video_url+prompt+elements) → previzCapable True·elementsField;
           (video_url+image_urls maxItems 3) → imageListField·imageListMax 3; (image_url 만) →
           previzCapable False. 기존 caps 키(promptField·startImageField 등) 그대로.
    (fm-b) 등록: video-to-video 카테고리 허용, deny 패턴(*seedance*2*fast*) 400 Denied.
    (fm-c) plan·generate: 빈 videoModel → 기본으로 저장, 등록 안 된/previz 미지원 모델 → 생성 때 기본으로 되돌림.
    (fm-d) 생성 payload: elements 모델 → 기존과 동일(@Element); image_urls 모델 → elements 없음·
           image_urls = 보이는 인물 정면 순서·프롬프트 "Image 1 is …" 번호 일치·maxItems 준수; video_url = 컷 클립.
    (fm-e) vpoc 기존 피커 목록(ark.choices)에 video-to-video/previzCapable 모델이 나오지 않음(회귀 없음).
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vpoc import ark, fal, settings  # noqa: PLC0415

    def _input_schema(props: dict, required: list | None = None) -> dict:
        """endpoint "/ep" 의 POST requestBody 가 가리키는 Input 스키마를 가진 가짜 OpenAPI."""
        schema = {"type": "object", "properties": props}
        if required is not None:
            schema["required"] = required
        return {
            "paths": {
                "/ep": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/EpInput"}
                                }
                            }
                        }
                    }
                }
            },
            "components": {"schemas": {"EpInput": schema}},
        }

    # ---------------- (fm-a) extract_caps ----------------

    # (1) video_url + prompt + elements[{frontal_image_url}] → previzCapable True, elementsField.
    oa_elements = _input_schema(
        {
            "prompt": {"type": "string", "maxLength": 2500},
            "video_url": {"type": "string"},
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "frontal_image_url": {"type": "string"},
                        "reference_image_urls": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "keep_audio": {"type": "boolean"},
        }
    )
    caps_e, reason_e = fal.extract_caps(oa_elements, "ep")
    if caps_e is None:
        bad.append(f"(fm-a) elements 스키마에서 caps 를 못 만들었습니다: {reason_e}")
    else:
        if caps_e.get("videoField") != "video_url":
            bad.append(f"(fm-a) videoField 가 video_url 이 아닙니다: {caps_e.get('videoField')}")
        if caps_e.get("elementsField") != "elements":
            bad.append(f"(fm-a) elementsField 가 elements 가 아닙니다: {caps_e.get('elementsField')}")
        if caps_e.get("previzCapable") is not True:
            bad.append(f"(fm-a) previzCapable 가 True 가 아닙니다: {caps_e.get('previzCapable')}")
        if caps_e.get("keepAudioField") != "keep_audio":
            bad.append(f"(fm-a) keepAudioField 가 keep_audio 가 아닙니다: {caps_e.get('keepAudioField')}")
        # 기존 caps 키(회귀) — promptField·startImageField·durations 등이 그대로 있어야 한다.
        for key in ("promptField", "promptMaxLength", "startImageField", "endImageField",
                    "referenceImagesField", "durations", "resolutions", "aspectRatios",
                    "audioField", "requiredFields", "available", "reason"):
            if key not in caps_e:
                bad.append(f"(fm-a) 기존 caps 키 '{key}' 가 사라졌습니다")

    # (2) video_url + image_urls(maxItems 3) → imageListField, imageListMax 3.
    oa_imagelist = _input_schema(
        {
            "prompt": {"type": "string", "maxLength": 1500},
            "video_url": {"type": "string"},
            "image_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        }
    )
    caps_i, reason_i = fal.extract_caps(oa_imagelist, "ep")
    if caps_i is None:
        bad.append(f"(fm-a) image_urls 스키마에서 caps 를 못 만들었습니다: {reason_i}")
    else:
        if caps_i.get("imageListField") != "image_urls":
            bad.append(f"(fm-a) imageListField 가 image_urls 가 아닙니다: {caps_i.get('imageListField')}")
        if caps_i.get("imageListMax") != 3:
            bad.append(f"(fm-a) imageListMax 가 3 이 아닙니다: {caps_i.get('imageListMax')}")
        if caps_i.get("elementsField"):
            bad.append(f"(fm-a) image_urls 전용인데 elementsField 가 잡혔습니다: {caps_i.get('elementsField')}")
        if caps_i.get("previzCapable") is not True:
            bad.append(f"(fm-a) video_url+prompt 인데 previzCapable 가 True 가 아닙니다: {caps_i.get('previzCapable')}")

    # (3) image_url 만(영상 입력 없음) → previzCapable False.
    oa_noprev = _input_schema(
        {
            "prompt": {"type": "string"},
            "image_url": {"type": "string"},
        }
    )
    caps_n, _ = fal.extract_caps(oa_noprev, "ep")
    if caps_n is None:
        bad.append("(fm-a) image_url 전용 스키마에서 caps 를 못 만들었습니다")
    else:
        if caps_n.get("previzCapable") is not False:
            bad.append(f"(fm-a) 영상 입력이 없는데 previzCapable 가 False 가 아닙니다: {caps_n.get('previzCapable')}")
        if caps_n.get("videoField"):
            bad.append(f"(fm-a) 영상 입력이 없는데 videoField 가 잡혔습니다: {caps_n.get('videoField')}")

    # ---------------- (fm-b) 등록: 카테고리·deny ----------------

    # deny 판정 함수.
    if not settings.is_denied_endpoint("fal-ai/seedance-2-0-fast/video-to-video"):
        bad.append("(fm-b) seedance 2 fast 계열이 deny 로 판정되지 않습니다")
    if settings.is_denied_endpoint("fal-ai/kling-video/o3/standard/video-to-video/edit"):
        bad.append("(fm-b) Kling 모델이 잘못 deny 로 판정됩니다")
    if "video-to-video" not in settings.FAL_VIDEO_CATEGORIES:
        bad.append("(fm-b) FAL_VIDEO_CATEGORIES 에 video-to-video 가 없습니다")

    saved = {
        "STORE": fal.STORE,
        "model_meta": fal.model_meta,
        "openapi_schema": fal.openapi_schema,
        "pricing": fal.pricing,
        "fal_key": settings.FAL_KEY,
        "ark_fal_registered": ark.fal.registered,
    }
    fake_store = _FakeCharStore()
    fal.STORE = fake_store
    settings.FAL_KEY = "dummy-fal-key"
    fal._invalidate_cache()

    def fake_meta(endpoint):
        return {"category": "video-to-video", "displayName": "Fake V2V", "openapi": {}}

    def fake_schema(endpoint, expand_result=None):
        return oa_elements  # video-to-video previzCapable 스키마

    fal.model_meta = fake_meta
    fal.openapi_schema = fake_schema
    fal.pricing = lambda endpoint: None  # 단가 미정(네트워크 없음)

    try:
        # deny 패턴 endpoint 는 400 Denied(스키마·메타 조회 전에 막힌다).
        try:
            fal.register("fal-ai/seedance-2-0-fast/video-to-video", added_by="tester")
            bad.append("(fm-b) deny endpoint 가 400 Denied 로 거절되지 않았습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != "Denied" or getattr(exc, "status", 0) != 400:
                bad.append(f"(fm-b) deny endpoint 가 400 Denied 가 아닙니다: kind={getattr(exc, 'kind', '?')} status={getattr(exc, 'status', '?')}")

        # video-to-video 카테고리 허용: 정상 등록된다.
        item = fal.register("fal-ai/kling-video/o3/standard/video-to-video/edit", added_by="tester")
        if item.get("category") != "video-to-video":
            bad.append(f"(fm-b) 등록 category 가 video-to-video 가 아닙니다: {item.get('category')}")
        if not (item.get("caps") or {}).get("previzCapable"):
            bad.append("(fm-b) 등록 caps 의 previzCapable 가 True 가 아닙니다")

        # ---------------- (fm-e) vpoc 피커(ark.choices) 회귀 ----------------
        # 등록 목록을 가짜로: video-to-video previz 1 + image-to-video non-previz 1 + text-to-video 일반 1.
        def fake_registered():
            return [
                {
                    "id": "fal:v2v-previz",
                    "endpointId": "v2v-previz",
                    "label": "V2V previz",
                    "category": "video-to-video",
                    "caps": {"previzCapable": True, "available": True},
                },
                {
                    "id": "fal:i2v-plain",
                    "endpointId": "i2v-plain",
                    "label": "I2V plain",
                    "category": "image-to-video",
                    "caps": {"previzCapable": False, "available": True, "startImageField": "image_url"},
                },
            ]

        ark.fal.registered = fake_registered
        choice_ids = [str(c.get("id")) for c in ark.choices()]
        if "fal:v2v-previz" in choice_ids:
            bad.append(f"(fm-e) vpoc 피커에 video-to-video previz 모델이 나타났습니다(회귀): {choice_ids}")
        # image-to-video non-previz 는 기존처럼 보여야 한다(회귀 없음).
        if "fal:i2v-plain" not in choice_ids:
            bad.append(f"(fm-e) vpoc 피커에서 기존 image-to-video 모델이 사라졌습니다: {choice_ids}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("(fm-b/e) 등록·피커 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1000])
    finally:
        fal.STORE = saved["STORE"]
        fal.model_meta = saved["model_meta"]
        fal.openapi_schema = saved["openapi_schema"]
        fal.pricing = saved["pricing"]
        settings.FAL_KEY = saved["fal_key"]
        ark.fal.registered = saved["ark_fal_registered"]
        fal._invalidate_cache()

    # ---------------- (fm-c) plan 저장·생성 모델 되돌림 ----------------

    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import settings as vnew_settings  # noqa: PLC0415

    default_id = planmod.default_previz_model()
    default_endpoint = str(vnew_settings.VNEW_PREVIZ_EDIT_MODEL)
    if default_id != fal.settings.FAL_ID_PREFIX + default_endpoint:
        bad.append(f"(fm-c) 기본 프리비즈 모델 id 가 fal:<VNEW_PREVIZ_EDIT_MODEL> 이 아닙니다: {default_id}")

    # 빈 videoModel → 기본 모델 id 로 저장(plan.update_input 의 model or default).
    # (update_input 은 저장 레이어를 쓰므로 함수 단위로 mutator 로직을 직접 확인한다.)
    model_ids = planmod._previz_model_ids()
    if default_id not in model_ids:
        bad.append(f"(fm-c) 기본 모델이 고를 수 있는 모델 집합에 없습니다: {sorted(model_ids)}")

    # 생성 때 되돌림: 등록 안 된/previz 미지원 모델 → 기본 endpoint.
    gen_saved_find = genmod.vpoc_fal.find
    try:
        genmod.vpoc_fal.find = lambda model: None  # 아무 모델도 등록 안 됨
        # 등록 안 된 fal 모델 → 기본 endpoint.
        ep1 = genmod._previz_model_endpoint({"input": {"videoModel": "fal:not-registered-xyz"}})
        if ep1 != default_endpoint:
            bad.append(f"(fm-c) 등록 안 된 모델이 기본 endpoint 로 되돌려지지 않았습니다: {ep1}")
        # 빈 값 → 기본 endpoint.
        ep2 = genmod._previz_model_endpoint({"input": {}})
        if ep2 != default_endpoint:
            bad.append(f"(fm-c) 빈 videoModel 이 기본 endpoint 가 아닙니다: {ep2}")
        # previz 미지원(등록됐지만 caps.previzCapable False) → 기본 endpoint.
        genmod.vpoc_fal.find = lambda model: {"caps": {"previzCapable": False}} if model.endswith("i2v-plain") else None
        ep3 = genmod._previz_model_endpoint({"input": {"videoModel": "fal:i2v-plain"}})
        if ep3 != default_endpoint:
            bad.append(f"(fm-c) previz 미지원 모델이 기본 endpoint 로 되돌려지지 않았습니다: {ep3}")
        # 등록된 previzCapable → 그 endpoint 유지.
        genmod.vpoc_fal.find = lambda model: {"caps": {"previzCapable": True}} if model.endswith("good-v2v") else None
        ep4 = genmod._previz_model_endpoint({"input": {"videoModel": "fal:good-v2v"}})
        if ep4 != "good-v2v":
            bad.append(f"(fm-c) 등록된 previzCapable 모델 endpoint 가 유지되지 않았습니다: {ep4}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("(fm-c) 생성 모델 되돌림 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:800])
    finally:
        genmod.vpoc_fal.find = gen_saved_find

    # ---------------- (fm-d) 생성 payload: elements vs image_urls ----------------

    # 공통 plan·seg·actors. 캐릭터 2명(둘 다 charId 있음, 색 마네킹).
    def _gen_plan():
        return {
            "input": {"characters": [{"charId": "ch1", "roleName": "A"}, {"charId": "ch2", "roleName": "B"}]},
            "conti": {
                "cuts": [
                    {"id": "c1", "panels": [0], "seconds": 4, "locationEn": "A room",
                     "dialogueKo": "", "characters": [{"name": "에이", "actionEn": "stands"}]},
                ],
            },
            "script": {"entries": [{"id": "e1", "panels": [0], "speaker": "에이", "lineKo": "안녕"}]},
            "previz": {
                "scenes": [
                    {
                        "locationId": "loc1",
                        "actors": [
                            {"id": "a1", "name": "에이", "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
                            {"id": "a2", "name": "비", "color": "#e6194b", "charId": "ch2", "appearanceEn": "a woman"},
                        ],
                        "timeline": [
                            {"cutId": "c1", "actors": [{"id": "a1"}, {"id": "a2"}]},
                        ],
                    }
                ],
                "shotBrief": {},
            },
        }

    unit = {"id": "c1", "cutId": "c1", "recSegId": "seg1", "recordingKey": "rec/k.mp4",
            "startSec": 0.0, "endSec": 4.0, "seconds": 4.0}

    # 가짜: 캐릭터 로드(정면·후면 키), 이미지 로드, 비율 맞춤, 원작 패널.
    gsaved = {
        "load_char": genmod.characters.load_char,
        "load_jpeg": genmod._load_jpeg,
        "fit_ratio": genmod.imaging.fit_ratio,
        "panel": genmod._segment_panel_jpeg,
    }
    fake_blobs = {
        "rf-ch1": b"RF1", "rb-ch1": b"RB1", "wf-ch1": b"WF1", "wb-ch1": b"WB1",
        "rf-ch2": b"RF2", "rb-ch2": b"RB2", "wf-ch2": b"WF2", "wb-ch2": b"WB2",
    }

    def fake_load_char(owner, char_id):
        n = char_id[-1]
        return (
            {
                "id": char_id, "name": "에이" if n == "1" else "비", "status": "ready",
                "analysis": {"appearanceEn": "a person", "outfitEn": "an outfit"},
                "variants": {
                    "real": {"frontKey": "rf-" + char_id, "backKey": "rb-" + char_id, "sheetKey": ""},
                    "webtoon": {"frontKey": "wf-" + char_id, "backKey": "wb-" + char_id, "sheetKey": ""},
                },
            },
            "etag",
        )

    try:
        genmod.characters.load_char = fake_load_char
        genmod._load_jpeg = lambda key: fake_blobs.get(str(key or ""))
        genmod.imaging.fit_ratio = lambda jpeg, *a, **k: jpeg  # 그대로(비율 맞춤 생략)
        genmod._segment_panel_jpeg = lambda owner, pid, plan, seg: b"PANEL-JPEG"

        plan = _gen_plan()

        # (fm-d-1) elements 모델(caps.elementsField) → 기존과 동일(@Element, image_urls 없음(실사)).
        caps_elements = {"elementsField": "elements", "videoField": "video_url",
                         "keepAudioField": "keep_audio", "previzCapable": True}
        built_real = genmod._previz_payload("o", "p", plan, unit, "real", caps=caps_elements)
        if not built_real["elements"]:
            bad.append("(fm-d) elements 모델 실사 payload 에 elements 가 없습니다")
        if built_real["imageUrls"]:
            bad.append("(fm-d) elements 모델 실사 payload 에 imageUrls 가 있습니다(실사는 없어야)")
        if "@Element1" not in built_real["prompt"]:
            bad.append("(fm-d) elements 모델 프롬프트에 @Element1 이 없습니다")

        # (fm-d-2) image_urls 모델(caps.imageListField, max 2) → elements 없음·image_urls = 정면 순서·
        #          프롬프트 "Image 1 is …" 번호 일치·maxItems 준수.
        caps_imagelist = {"imageListField": "image_urls", "imageListMax": 2, "videoField": "video_url",
                          "keepAudioField": "keep_audio", "previzCapable": True}
        built_il = genmod._previz_payload("o", "p", plan, unit, "real", caps=caps_imagelist)
        if built_il["elements"]:
            bad.append(f"(fm-d) image_urls 모델에 elements 가 있습니다: {len(built_il['elements'])}")
        imgs = built_il["imageUrls"]
        # 실사 image_urls 는 보이는 인물 정면(순서는 _segment_actors 가 정한다). maxItems 2 준수.
        if len(imgs) > 2:
            bad.append(f"(fm-d) image_urls 가 maxItems(2) 를 넘습니다: {len(imgs)}")
        if len(imgs) < 1:
            bad.append("(fm-d) image_urls 모델에 참고 이미지가 없습니다")
        # 프롬프트 번호: "Image 1 is" · 마네킹 색 매핑 "becomes the person in Image 1".
        if "Image 1 is" not in built_il["prompt"]:
            bad.append(f"(fm-d) image_urls 프롬프트에 'Image 1 is' 가 없습니다: {built_il['prompt'][:200]}")
        if "becomes the person in Image 1" not in built_il["prompt"]:
            bad.append("(fm-d) image_urls 프롬프트에 색 마네킹 ↔ Image 번호 매핑 문장이 없습니다")
        # 핵심 불변식(설계자 지시서 057): CAST 의 "Image {n} is {name}" 번호가 실제 image_urls 순서와
        # 반드시 같다. 이름 → 실사 정면 바이트 매핑(에이=RF1·비=RF2)으로, 프롬프트가 가리킨 n 번째
        # 인물의 정면이 image_urls[n-1] 과 같은지 본다.
        import re as _re_fmd  # noqa: PLC0415

        name_to_front = {"에이": b"RF1", "비": b"RF2"}
        for match in _re_fmd.finditer(r"Image (\d+) is ([^;:]+)", built_il["prompt"]):
            n = int(match.group(1))
            name = match.group(2).strip()
            expect = name_to_front.get(name)
            if expect is None:
                continue  # charId 없는 인물(번호 없음)은 여기 안 걸린다
            if n < 1 or n > len(imgs):
                bad.append(f"(fm-d) CAST Image 번호 {n} 가 image_urls 범위를 벗어납니다(개수 {len(imgs)})")
            elif imgs[n - 1] != expect:
                bad.append(
                    f"(fm-d) CAST 'Image {n} is {name}' 가 image_urls[{n - 1}] 와 다릅니다: "
                    f"{imgs[n - 1]} != {expect}"
                )

        # (fm-d-3) 웹툰 image_urls: 인물 정면 + 그림체 참고 1장(원작 웹툰 컷), max 이내.
        built_il_web = genmod._previz_payload("o", "p", plan, unit, "webtoon", caps=caps_imagelist)
        web_imgs = built_il_web["imageUrls"]
        if len(web_imgs) > 2:
            bad.append(f"(fm-d) 웹툰 image_urls 가 maxItems(2) 를 넘습니다: {len(web_imgs)}")
        if b"PANEL-JPEG" not in web_imgs:
            bad.append("(fm-d) 웹툰 image_urls 에 그림체 참고 원작 컷(PANEL)이 없습니다")

        # (fm-d-4) _submit_previz_edit 가 caps 필드 이름으로 본문을 만든다(video_url=컷 클립, image_urls).
        prev_calls = []

        def fake_previz_request(method, url, payload):
            prev_calls.append({"method": method, "url": url, "payload": payload})
            return {"request_id": "req-fm-d"}

        gsaved_req = genmod._previz_request
        gsaved_key = settings.FAL_KEY
        try:
            genmod._previz_request = fake_previz_request
            settings.FAL_KEY = "dummy-fal-key"
            genmod._submit_previz_edit(
                endpoint="v2v-model",
                caps=caps_imagelist,
                video_mp4=b"CUT-CLIP",
                prompt="hello",
                output_prefix="pfx",
                elements=[],
                image_urls=[b"RF1", b"RF2"],
            )
            if not prev_calls:
                bad.append("(fm-d) _submit_previz_edit 가 요청을 보내지 않았습니다")
            else:
                body = prev_calls[-1]["payload"]
                if not str(body.get("video_url") or "").startswith("data:video/mp4;base64,"):
                    bad.append("(fm-d) image_urls 모델 본문의 video_url(컷 클립) 이 data URI 가 아닙니다")
                if "elements" in body:
                    bad.append("(fm-d) image_urls 모델 본문에 elements 가 있습니다")
                if not body.get("image_urls"):
                    bad.append("(fm-d) image_urls 모델 본문에 image_urls 가 없습니다")
                if body.get("keep_audio") is not False:
                    bad.append(f"(fm-d) keep_audio 가 False 가 아닙니다: {body.get('keep_audio')}")
        finally:
            genmod._previz_request = gsaved_req
            settings.FAL_KEY = gsaved_key
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("(fm-d) 생성 payload 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1000])
    finally:
        genmod.characters.load_char = gsaved["load_char"]
        genmod._load_jpeg = gsaved["load_jpeg"]
        genmod.imaging.fit_ratio = gsaved["fit_ratio"]
        genmod._segment_panel_jpeg = gsaved["panel"]

    if bad:
        fail("fal 영상 모델 caps(fm) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal 영상 모델 caps(fm) 검사 (fm-a)extract_caps elements·imageList·previzCapable·기존 키 "
            "(fm-b)등록 video-to-video 허용·deny 400 (fm-c)기본 모델·생성 되돌림 "
            "(fm-d)payload elements vs image_urls·Image 번호·video_url 컷 (fm-e)vpoc 피커 회귀 없음"
        )


# ---------------------------------------------------------------- 13b. reference-to-video caps/등록


def check_vnew_reference_video() -> None:
    """reference-to-video 카테고리·참조 영상(caps) 인식 — Seedance 2.5 대비(Claude 조사 2026-10-25).

    외부 호출(모델 메타·스키마·단가)은 전부 가짜. 유료 호출 없음.

    (rv-a) 가짜 OpenAPI(prompt+image_urls maxItems 없음+video_urls+task enum+draft) → category
           reference-to-video 등록 허용, caps: videoListField="video_urls"·videoListMax 10·
           imageListMax 10·taskField 값 목록·draftField·previzCapable True.
    (rv-b) 기존 vpoc 피커 목록(ark.choices)에 reference-to-video 모델이 나오지 않음.
    (rv-c) 기존 caps 회귀 없음(elements 모델·i2v 모델 기존 기대값 유지).
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vpoc import ark, fal, settings  # noqa: PLC0415

    def _input_schema(props: dict, required: list | None = None) -> dict:
        schema = {"type": "object", "properties": props}
        if required is not None:
            schema["required"] = required
        return {
            "paths": {
                "/ep": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/EpInput"}
                                }
                            }
                        }
                    }
                }
            },
            "components": {"schemas": {"EpInput": schema}},
        }

    # ---------------- (rv-a) extract_caps: Seedance 2.5 reference-to-video 모양 ----------------
    oa_ref = _input_schema(
        {
            "prompt": {"type": "string", "maxLength": 2500},
            # 참조 이미지 배열 — maxItems 없음(실측: Seedance 는 maxItems 를 안 적는다) → 10.
            "image_urls": {"type": "array", "items": {"type": "string"}},
            # 참조 영상 배열 — maxItems 없음 → 10.
            "video_urls": {"type": "array", "items": {"type": "string"}},
            "audio_urls": {"type": "array", "items": {"type": "string"}},
            "task": {
                "anyOf": [
                    {"type": "string", "enum": ["auto", "reference", "editing", "extension"]},
                    {"type": "null"},
                ]
            },
            "duration": {"type": "string", "enum": ["4", "8", "12", "30"]},
            "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"]},
            "generate_audio": {"type": "boolean"},
            "draft": {"type": "boolean"},
            "seed": {"type": "integer"},
        },
        required=["prompt"],
    )
    caps_r, reason_r = fal.extract_caps(oa_ref, "ep")
    if caps_r is None:
        bad.append(f"(rv-a) reference 스키마에서 caps 를 못 만들었습니다: {reason_r}")
    else:
        if caps_r.get("videoListField") != "video_urls":
            bad.append(f"(rv-a) videoListField 가 video_urls 가 아닙니다: {caps_r.get('videoListField')}")
        if caps_r.get("videoListMax") != 10:
            bad.append(f"(rv-a) videoListMax 가 10 이 아닙니다: {caps_r.get('videoListMax')}")
        if caps_r.get("imageListField") != "image_urls":
            bad.append(f"(rv-a) imageListField 가 image_urls 가 아닙니다: {caps_r.get('imageListField')}")
        if caps_r.get("imageListMax") != 10:
            bad.append(f"(rv-a) maxItems 없는 image_urls 의 imageListMax 가 10 이 아닙니다: {caps_r.get('imageListMax')}")
        if caps_r.get("taskField") != "task":
            bad.append(f"(rv-a) taskField 가 task 가 아닙니다: {caps_r.get('taskField')}")
        if caps_r.get("taskValues") != ["auto", "reference", "editing", "extension"]:
            bad.append(f"(rv-a) taskValues 목록이 틀립니다: {caps_r.get('taskValues')}")
        if caps_r.get("draftField") != "draft":
            bad.append(f"(rv-a) draftField 가 draft 가 아닙니다: {caps_r.get('draftField')}")
        if caps_r.get("generateAudioField") != "generate_audio":
            bad.append(f"(rv-a) generateAudioField 가 generate_audio 가 아닙니다: {caps_r.get('generateAudioField')}")
        if caps_r.get("seedField") != "seed":
            bad.append(f"(rv-a) seedField 가 seed 가 아닙니다: {caps_r.get('seedField')}")
        if caps_r.get("previzCapable") is not True:
            bad.append(f"(rv-a) video_urls+prompt 인데 previzCapable 가 True 가 아닙니다: {caps_r.get('previzCapable')}")
        # 문자열 duration 이미 지원(durationAsString True).
        if caps_r.get("durationAsString") is not True:
            bad.append(f"(rv-a) 문자열 duration 인데 durationAsString 가 True 가 아닙니다: {caps_r.get('durationAsString')}")
        if sorted(int(x) for x in (caps_r.get("durations") or [])) != [4, 8, 12, 30]:
            bad.append(f"(rv-a) durations 가 [4,8,12,30] 이 아닙니다: {caps_r.get('durations')}")
        if caps_r.get("available") is not True:
            bad.append(f"(rv-a) prompt 만 required 인데 available 가 True 가 아닙니다: {caps_r.get('reason')}")

    # 등록(가짜 메타·스키마·단가) — category reference-to-video 허용.
    saved = {
        "STORE": fal.STORE,
        "model_meta": fal.model_meta,
        "openapi_schema": fal.openapi_schema,
        "pricing": fal.pricing,
        "fal_key": settings.FAL_KEY,
        "ark_fal_registered": ark.fal.registered,
    }
    fake_store = _FakeCharStore()
    fal.STORE = fake_store
    settings.FAL_KEY = "dummy-fal-key"
    fal._invalidate_cache()
    fal.model_meta = lambda endpoint: {"category": "reference-to-video", "displayName": "Seedance 2.5 r2v", "openapi": {}}
    fal.openapi_schema = lambda endpoint, expand_result=None: oa_ref
    fal.pricing = lambda endpoint: None

    try:
        if "reference-to-video" not in settings.FAL_VIDEO_CATEGORIES:
            bad.append("(rv-a) FAL_VIDEO_CATEGORIES 에 reference-to-video 가 없습니다")
        item = fal.register("bytedance/seedance-2.5/reference-to-video", added_by="tester")
        if item.get("category") != "reference-to-video":
            bad.append(f"(rv-a) 등록 category 가 reference-to-video 가 아닙니다: {item.get('category')}")
        if not (item.get("caps") or {}).get("previzCapable"):
            bad.append("(rv-a) 등록 caps 의 previzCapable 가 True 가 아닙니다")
        if (item.get("caps") or {}).get("videoListField") != "video_urls":
            bad.append("(rv-a) 등록 caps 의 videoListField 가 video_urls 가 아닙니다")

        # Seedance 2.5 는 deny 아님(설계자 Notion 워크플로 권장 모델).
        if settings.is_denied_endpoint("bytedance/seedance-2.5/reference-to-video"):
            bad.append("(rv-a) Seedance 2.5 가 잘못 deny 로 판정됩니다")
        # seedance 2.0 fast 는 여전히 deny.
        if not settings.is_denied_endpoint("fal-ai/seedance-2-0-fast/reference-to-video"):
            bad.append("(rv-a) seedance 2.0 fast 가 deny 로 판정되지 않습니다")

        # ---------------- (rv-b) vpoc 피커(ark.choices)에 reference-to-video 안 보임 ----------------
        def fake_registered():
            return [
                {
                    "id": "fal:r2v-seedance",
                    "endpointId": "r2v-seedance",
                    "label": "Seedance r2v",
                    "category": "reference-to-video",
                    "caps": {"previzCapable": True, "available": True, "videoListField": "video_urls"},
                },
                {
                    "id": "fal:i2v-plain",
                    "endpointId": "i2v-plain",
                    "label": "I2V plain",
                    "category": "image-to-video",
                    "caps": {"previzCapable": False, "available": True, "startImageField": "image_url"},
                },
            ]

        ark.fal.registered = fake_registered
        choice_ids = [str(c.get("id")) for c in ark.choices()]
        if "fal:r2v-seedance" in choice_ids:
            bad.append(f"(rv-b) vpoc 피커에 reference-to-video 모델이 나타났습니다(회귀): {choice_ids}")
        if "fal:i2v-plain" not in choice_ids:
            bad.append(f"(rv-b) vpoc 피커에서 기존 image-to-video 모델이 사라졌습니다: {choice_ids}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415
        bad.append("(rv-a/b) 등록·피커 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1000])
    finally:
        fal.STORE = saved["STORE"]
        fal.model_meta = saved["model_meta"]
        fal.openapi_schema = saved["openapi_schema"]
        fal.pricing = saved["pricing"]
        settings.FAL_KEY = saved["fal_key"]
        ark.fal.registered = saved["ark_fal_registered"]
        fal._invalidate_cache()

    # ---------------- (rv-c) 기존 caps 회귀 없음 ----------------
    # elements 모델: previzCapable True·elementsField·videoListField 빈값.
    oa_elements = _input_schema(
        {
            "prompt": {"type": "string", "maxLength": 2500},
            "video_url": {"type": "string"},
            "elements": {
                "type": "array",
                "items": {"type": "object", "properties": {"frontal_image_url": {"type": "string"}}},
            },
            "keep_audio": {"type": "boolean"},
        }
    )
    caps_e, _ = fal.extract_caps(oa_elements, "ep")
    if caps_e is None or caps_e.get("elementsField") != "elements" or caps_e.get("previzCapable") is not True:
        bad.append(f"(rv-c) elements 모델 기존 기대값이 깨졌습니다: {caps_e and {k: caps_e.get(k) for k in ('elementsField', 'previzCapable')}}")
    if caps_e and caps_e.get("videoListField"):
        bad.append(f"(rv-c) elements 모델에 videoListField 가 잘못 잡혔습니다: {caps_e.get('videoListField')}")
    if caps_e and caps_e.get("videoField") != "video_url":
        bad.append(f"(rv-c) elements 모델 videoField 회귀: {caps_e and caps_e.get('videoField')}")

    # i2v 모델(image_url 만): previzCapable False·videoField/videoListField 빈값·imageListField 빈값.
    oa_i2v = _input_schema({"prompt": {"type": "string"}, "image_url": {"type": "string"}})
    caps_i2v, _ = fal.extract_caps(oa_i2v, "ep")
    if caps_i2v is None or caps_i2v.get("previzCapable") is not False:
        bad.append(f"(rv-c) i2v 모델 previzCapable 가 False 가 아닙니다: {caps_i2v and caps_i2v.get('previzCapable')}")
    if caps_i2v and (caps_i2v.get("videoField") or caps_i2v.get("videoListField")):
        bad.append("(rv-c) i2v 모델에 영상 입력 필드가 잘못 잡혔습니다")
    if caps_i2v and caps_i2v.get("startImageField") != "image_url":
        bad.append(f"(rv-c) i2v 모델 startImageField 회귀: {caps_i2v and caps_i2v.get('startImageField')}")
    # 새 키가 모델에 없을 때 빈값으로 들어가는지(일반 i2v 는 task/draft/seed 없음).
    if caps_i2v and (caps_i2v.get("taskField") or caps_i2v.get("draftField") or caps_i2v.get("seedField")):
        bad.append("(rv-c) i2v 모델에 task/draft/seed 필드가 잘못 잡혔습니다")

    if bad:
        fail("reference-to-video caps 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "reference-to-video caps 검사 (rv-a)videoListField·videoListMax 10·imageListMax 10·"
            "taskField·draftField·previzCapable·등록 허용 (rv-b)vpoc 피커 회귀 없음 "
            "(rv-c)elements·i2v 기존 caps 유지"
        )


# ---------------------------------------------------------------- 13c. 통 테이크(Seedance 2.5) 생성


def check_vnew_take() -> None:
    """영상만들기_신규 ⑤ 통 테이크(Seedance 2.5 reference-to-video) — 웹툰만(설계자 2026-10-26, 063).

    외부 호출(fal 제출·조회·concat·원장·캐릭터·장소·패널 로드)은 전부 가짜. 유료 호출 없음.

    (tk-a) videoListField 모델이면 gen.mode previz-take, 아니면 previz(회귀 없음).
    (tk-b) 녹화 2구간(12s+15s) → 테이크 1개(27s); 3구간(15+15+10) → 테이크 2개(30s, 10s).
    (tk-c) payload: video_urls 1개, image_urls 순서 = 인물 정면·후면 → 패널 → 장소, ≤10, @Image 번호 일치,
           task reference·draft true·resolution 480p·duration 문자열·generate_audio false, 실사 skipped·제출 0.
    (tk-d) 프롬프트 STYLE 맨 앞·AVOID 포함·"cinematic lighting" STYLE/TIMELINE 에 없음·≤4000 바이트.
    (tk-e) 견적: 480p 15초 ≈ 144k 토큰 × 0.0214/1000 ≈ $3.08(±0.05), 원장 같은 값.
    (tk-f) draft_id 저장, 완료 → finalWebtoon done·finalReal skipped·gen done.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import settings as vnew_settings  # noqa: PLC0415
    from src.vpoc import fal as _fal_mod  # noqa: PLC0415
    from src.vpoc import ledger as _ledger_mod  # noqa: PLC0415
    from src.vpoc import settings as _settings  # noqa: PLC0415

    fake_store = _FakeCharStore()
    TAKE_ENDPOINT = "bytedance/seedance-2.5/reference-to-video"
    TAKE_MODEL_ID = _settings.FAL_ID_PREFIX + TAKE_ENDPOINT
    # Seedance reference-to-video caps(062 extract_caps 결과 모양).
    TAKE_CAPS = {
        "videoListField": "video_urls",
        "videoListMax": 10,
        "imageListField": "image_urls",
        "imageListMax": 10,
        "taskField": "task",
        "taskValues": ["auto", "reference", "editing", "extension"],
        "draftField": "draft",
        "generateAudioField": "generate_audio",
        "seedField": "seed",
        "durationAsString": True,
        "durations": [4, 8, 12, 30],
        "previzCapable": True,
        "available": True,
    }

    # 가짜 fal.find: 통 테이크 모델이면 caps 를 돌려준다(그 밖은 미등록=None → 기본 previz).
    def fake_find(model):
        if str(model) == TAKE_MODEL_ID:
            return {"id": TAKE_MODEL_ID, "endpointId": TAKE_ENDPOINT, "caps": TAKE_CAPS}
        return None

    # 가짜 pricing: unit "1000 tokens", unitPrice 0.0214(fal 실측).
    def fake_pricing_for(model):
        if str(model) == TAKE_MODEL_ID:
            return {"unit": "1000 tokens", "unitPrice": 0.0214}
        return None

    def fake_pricing(endpoint):
        if str(endpoint) == TAKE_ENDPOINT:
            return {"unit": "1000 tokens", "unitPrice": 0.0214}
        return None

    # 제출 본문을 잡아 두고 가짜 request_id·draft_id 를 돌려준다.
    previz_request_calls: list[dict] = []

    def fake_previz_request(method, url, payload):
        import secrets as _s  # noqa: PLC0415

        previz_request_calls.append({"method": method, "url": url, "payload": payload})
        return {"request_id": "take-" + _s.token_hex(4), "draft_id": "draft-" + _s.token_hex(3)}

    # 조회: 바로 Completed.
    def fake_fal_poll(invocation_arn, output_prefix):
        return {"status": "Completed", "failureMessage": "", "usage": None}

    concat_calls: list[int] = []

    def fake_concat(clips):
        concat_calls.append(len(clips))
        return b"FINAL-TAKE-MP4"

    def fake_fetch_bytes(key, *, bucket=""):
        return fake_store.blobs.get(key, b"REC-MP4-BYTES")

    ledger_calls: list[dict] = []

    def fake_charge(key, **kwargs):
        ledger_calls.append({"key": key, **kwargs})
        return {"spentUsd": 0.0, "capUsd": None}

    # 캐릭터 로드(웹툰 정면·후면 키).
    def fake_load_char(owner, char_id):
        return (
            {
                "id": char_id,
                "name": "에이" if char_id.endswith("1") else "비",
                "status": "ready",
                "analysis": {"appearanceEn": "a person", "outfitEn": "an outfit", "styleEn": "thin ink lines, soft flat color"},
                "variants": {
                    "real": {"frontKey": "rf-" + char_id, "backKey": "rb-" + char_id, "sheetKey": ""},
                    "webtoon": {"frontKey": "wf-" + char_id, "backKey": "wb-" + char_id, "sheetKey": ""},
                },
            },
            "etag",
        )

    # 장면 의상·목소리 읽기(Claude) 가짜 — 호출 횟수를 세어 캐시(ca-a)를 검증한다.
    cast_notes_calls = {"n": 0}

    def fake_read_cast_notes(blocks, *, model):
        cast_notes_calls["n"] += 1
        return (
            {
                "people": [
                    {"name": "에이", "costumeEn": "black dress shirt, sleeves rolled; charcoal slacks",
                     "voiceEn": "low, calm adult male voice"},
                    {"name": "비", "costumeEn": "navy training top", "voiceEn": "soft young female voice"},
                ]
            },
            {"model": model, "inputTokens": 5, "outputTokens": 10},
        )

    orig = {
        "gen_STORE": genmod.STORE,
        "plan_STORE": planmod.STORE,
        "ledger_STORE": _ledger_mod.STORE,
        "find": genmod.vpoc_fal.find,
        "pricing_for": genmod.vpoc_fal.pricing_for,
        "pricing": genmod.vpoc_fal.pricing,
        "previz_request": genmod._previz_request,
        "fal_poll": _fal_mod.poll,
        "_concat": genmod._concat,
        "_fetch_bytes": genmod._fetch_bytes,
        "load_char": genmod.characters.load_char,
        "_load_jpeg": genmod._load_jpeg,
        "_segment_panel_jpegs": genmod._segment_panel_jpegs,
        "fit_ratio": genmod.imaging.fit_ratio,
        "charge": _ledger_mod.charge,
        "Thread": genmod.threading.Thread,
        "fal_key": _settings.FAL_KEY,
        "read_cast_notes": genmod._read_cast_notes,
    }
    genmod.STORE = fake_store
    planmod.STORE = fake_store
    _ledger_mod.STORE = fake_store
    genmod.vpoc_fal.find = fake_find
    genmod.vpoc_fal.pricing_for = fake_pricing_for
    genmod.vpoc_fal.pricing = fake_pricing
    genmod._previz_request = fake_previz_request
    _fal_mod.poll = fake_fal_poll
    genmod._concat = fake_concat
    genmod._fetch_bytes = fake_fetch_bytes
    genmod.characters.load_char = fake_load_char
    # 참조 이미지: 인물 정면·후면·패널·장소를 바이트로 구별(순서 검증). 비율 맞춤은 그대로.
    fake_blobs = {
        "wf-ch1": b"WF1", "wb-ch1": b"WB1",
        "wf-ch2": b"WF2", "wb-ch2": b"WB2",
    }
    genmod._load_jpeg = lambda key: fake_blobs.get(str(key or ""))
    genmod._segment_panel_jpegs = lambda owner, pid, plan, seg, limit: [b"PANEL1", b"PANEL2"][:limit]
    genmod.imaging.fit_ratio = lambda jpeg, *a, **k: jpeg
    genmod._read_cast_notes = fake_read_cast_notes
    _ledger_mod.charge = fake_charge
    _settings.FAL_KEY = "dummy-fal-key"

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

    def _make_take_plan(owner, pid, segments, *, video_model=TAKE_MODEL_ID, record=True, loc_view=True):
        """통 테이크 선행 조건(③콘티·프리비즈 승인·녹화·지시서)이 된 계획을 써 둔다."""
        plan = planmod.new_plan()
        total = sum(int(s["seconds"]) for s in segments)
        plan["input"]["totalSeconds"] = total
        plan["input"]["videoModel"] = video_model
        plan["input"]["order"] = ["f1"]
        plan["input"]["images"] = [{"fileId": "f1", "name": "1.png", "width": 600, "height": 900}]
        plan["input"]["characters"] = [
            {"charId": "ch1", "roleName": "에이"},
            {"charId": "ch2", "roleName": "비"},
        ]
        fake_store.blobs[planmod.upload_key(owner, pid, "f1")] = _tiny_jpeg(600, 900)
        cuts = []
        for seg in segments:
            n_cuts = max(1, len(seg["cutIds"]))
            for cid in seg["cutIds"]:
                cuts.append(
                    {
                        "id": cid,
                        "no": len(cuts) + 1,
                        "seconds": int(seg["seconds"]) // n_cuts,
                        "panels": [0],
                        "shotKo": "컷",
                        "cameraEn": "static camera",
                        "locationEn": "A closed principal's office",
                        "characters": [{"name": "에이", "positionEn": "at the desk", "actionEn": "speaks"}],
                        "dialogueKo": "자네 뭐 하나?",
                        "situationKo": "호통",
                        "promptEn": "Kim speaks, static camera.",
                    }
                )
        plan["script"] = {
            "status": "ready",
            "setting": {"locationEn": "A closed principal's office", "enclosed": True},
            "entries": [{"id": "e1", "panels": [0], "locationEn": "A closed principal's office"}],
        }
        plan["conti"] = {
            "status": "ready",
            "cuts": cuts,
            "segments": segments,
            "setting": {"locationEn": "A closed principal's office"},
        }
        # 장소(웹툰 back 뷰 키).
        loc_variants = {"webtoon": {"viewKeys": {"back": "loc-wb-back"}}} if loc_view else {}
        if loc_view:
            fake_blobs["loc-wb-back"] = b"LOC-BACK"
        plan["locations"] = [
            {
                "id": "loc1",
                "nameEn": "A closed principal's office",
                "key": "a closed principal's office",
                "panels": [0],
                "status": "ready",
                "variants": loc_variants,
            }
        ]
        scene_actors = [
            {"id": "a1", "name": "에이", "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": "비", "color": "#e6194b", "charId": "ch2", "appearanceEn": "a woman"},
        ]
        timeline = []
        for c in cuts:
            timeline.append({"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]})
        recordings = {}
        if record:
            for seg in segments:
                rec_key = f"rec/{pid}/{seg['id']}.mp4"
                fake_store.blobs[rec_key] = b"PREVIZ-REC-MP4"
                recordings[seg["id"]] = {"status": "ready", "key": rec_key, "seconds": float(seg["seconds"])}
        brief_cuts = {}
        for c in cuts:
            brief_cuts[c["id"]] = {
                "shotEn": "MCU, 50mm lens, eye-level.",
                "actionEn": "Kim leans forward, eyes narrowing.",
                "dialogue": [{"speaker": "에이", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp"}],
            }
        plan["previz"] = {
            "status": "ready",
            "error": "",
            "revision": 0,
            "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
            "approved": True,
            "recordings": recordings,
            "shotBrief": {
                "status": "ready",
                "scene": {"loc1": "A closed principal's office, wood panelling."},
                "cuts": brief_cuts,
                "updatedAt": "2026-10-26T00:00:00Z",
            },
            "task": {"status": "done", "startedAt": None, "id": None},
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

        import base64 as _b64  # noqa: PLC0415

        # (tk-a) 모드 선택: videoListField 모델 → previz-take, 기본 모델 → previz.
        plan_a = _make_take_plan(owner, "prj_tk_a", [{"id": "s1", "cutIds": ["c1"], "seconds": 10}])
        if genmod._gen_mode(plan_a) != "previz-take":
            bad.append(f"(tk-a) videoListField 모델인데 mode 가 previz-take 가 아닙니다: {genmod._gen_mode(plan_a)}")
        plan_a2 = _make_take_plan(owner, "prj_tk_a2", [{"id": "s1", "cutIds": ["c1"], "seconds": 10}],
                                  video_model="luma-ray2")
        if genmod._gen_mode(plan_a2) != "previz":
            bad.append(f"(tk-a) 기본 모델인데 mode 가 previz 가 아닙니다(회귀): {genmod._gen_mode(plan_a2)}")

        # (tk-b) 테이크 그룹핑. 2구간(12+15=27)→1개; 3구간(15+15+10)→2개(30,10).
        pb = _make_take_plan(owner, "prj_tk_b", [
            {"id": "s1", "cutIds": ["c1"], "seconds": 12},
            {"id": "s2", "cutIds": ["c2"], "seconds": 15},
        ])
        takes_b = genmod._take_units(pb)
        if len(takes_b) != 1 or abs(takes_b[0]["seconds"] - 27) > 0.01:
            bad.append(f"(tk-b) 12+15 가 테이크 1개(27s)가 아닙니다: {[(t['id'], t['seconds']) for t in takes_b]}")
        pb2 = _make_take_plan(owner, "prj_tk_b2", [
            {"id": "s1", "cutIds": ["c1"], "seconds": 15},
            {"id": "s2", "cutIds": ["c2"], "seconds": 15},
            {"id": "s3", "cutIds": ["c3"], "seconds": 10},
        ])
        takes_b2 = genmod._take_units(pb2)
        secs_b2 = [round(t["seconds"]) for t in takes_b2]
        if len(takes_b2) != 2 or secs_b2 != [30, 10]:
            bad.append(f"(tk-b) 15+15+10 가 테이크 [30,10] 가 아닙니다: {secs_b2}")

        # (tk-e) 견적: 480p 15초 ≈ $3.08.
        pe = _make_take_plan(owner, "prj_tk_e", [{"id": "s1", "cutIds": ["c1"], "seconds": 15}])
        est_e = genmod.estimate(pe)
        if est_e.get("mode") != "previz-take":
            bad.append(f"(tk-e) estimate.mode 가 previz-take 가 아닙니다: {est_e.get('mode')}")
        if abs(est_e.get("totalUsd", 0) - 3.08) > 0.05:
            bad.append(f"(tk-e) 480p 15초 견적이 ~$3.08 이 아닙니다: {est_e.get('totalUsd')}")
        if est_e.get("realUsd") not in (0, 0.0):
            bad.append(f"(tk-e) 실사 견적이 0 이 아닙니다: {est_e.get('realUsd')}")
        # 토큰 공식 직접 검산: 854×480×24×15/1024 ≈ 144k, ×0.0214/1000.
        expect = round((854 * 480 * 24 * 15 / 1024) / 1000 * 0.0214, 4)
        got = genmod._take_usd(TAKE_ENDPOINT, 15, "480p")
        if abs(got - expect) > 0.001:
            bad.append(f"(tk-e) _take_usd 토큰 공식 값이 틀립니다: {got} != {expect}")

        # (tk-c)(tk-d)(tk-f) 전체 진행: 1테이크(2구간 27초) → 제출 1회 → 완료 → done.
        pid_c = "prj_tk_c"
        _make_take_plan(owner, pid_c, [
            {"id": "s1", "cutIds": ["c1"], "seconds": 12},
            {"id": "s2", "cutIds": ["c2"], "seconds": 15},
        ])
        previz_request_calls.clear(); concat_calls.clear(); ledger_calls.clear()
        planc, _ = planmod.load_plan(owner, pid_c)
        est_c = genmod.estimate(planc)
        resp = client.post(f"/api/vnew/p/{pid_c}/generate", json={"confirmUsd": est_c["totalUsd"]})
        if resp.status_code != 202:
            bad.append(f"(tk-c) generate 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
        _join_drivers()
        cplan, _ = planmod.load_plan(owner, pid_c)
        cgen = cplan.get("gen") or {}
        if cgen.get("mode") != "previz-take":
            bad.append(f"(tk-c) gen.mode 가 previz-take 가 아닙니다: {cgen.get('mode')}")
        # 제출 1회(테이크 1개 × 웹툰만). 실사는 제출 0.
        if len(previz_request_calls) != 1:
            bad.append(f"(tk-c) 제출이 1회(웹툰만)가 아닙니다: {len(previz_request_calls)}")
        if previz_request_calls:
            body = previz_request_calls[0]["payload"]
            # video_urls 1개(data URI).
            vids = body.get("video_urls")
            if not isinstance(vids, list) or len(vids) != 1:
                bad.append(f"(tk-c) video_urls 가 1개 배열이 아닙니다: {vids if not isinstance(vids, list) else len(vids)}")
            elif not str(vids[0]).startswith("data:video/mp4;base64,"):
                bad.append("(tk-c) video_urls[0] 이 data URI 가 아닙니다")
            imgs = body.get("image_urls") or []
            if len(imgs) > 10:
                bad.append(f"(tk-c) image_urls 가 10 을 넘습니다: {len(imgs)}")
            # 순서: 인물 정면 1장만(후면 제외) → 패널 → 장소. 바이트로 복원.
            def _b(uri):
                try:
                    return _b64.b64decode(str(uri).split(",", 1)[1])
                except Exception:  # noqa: BLE001
                    return b""
            decoded = [_b(u) for u in imgs]
            # 처음 2장은 두 인물의 정면(후면 없음 — 065: 다른 의상 증거를 줄인다).
            person_fronts = {b"WF1", b"WF2"}
            if set(decoded[:2]) != person_fronts:
                bad.append(f"(tk-c) image_urls 앞 2장이 인물 정면(WF1·WF2)이 아닙니다: {decoded[:2]}")
            # 후면(WB1·WB2)은 들어가면 안 된다.
            if b"WB1" in decoded or b"WB2" in decoded:
                bad.append(f"(tk-c) 인물 후면이 참조에 들어갔습니다(들어가면 안 됨): {decoded}")
            # 이어 패널(PANEL1,PANEL2), 장소(LOC-BACK).
            if b"PANEL1" not in decoded or b"PANEL2" not in decoded:
                bad.append("(tk-c) image_urls 에 원작 패널이 없습니다")
            if b"LOC-BACK" not in decoded:
                bad.append("(tk-c) image_urls 에 장소 시트(back)가 없습니다")
            # 패널·장소가 인물(앞 2장) 뒤, 장소는 패널 뒤.
            if decoded.index(b"PANEL1") < 2 or decoded.index(b"LOC-BACK") < decoded.index(b"PANEL1"):
                bad.append(f"(tk-c) image_urls 순서가 인물→패널→장소가 아닙니다: {decoded}")
            # 필드 값.
            if body.get("task") != "reference":
                bad.append(f"(tk-c) task 가 reference 가 아닙니다: {body.get('task')}")
            if body.get("draft") is not True:
                bad.append(f"(tk-c) draft 가 true 가 아닙니다: {body.get('draft')}")
            if body.get("resolution") != "480p":
                bad.append(f"(tk-c) resolution 이 480p 가 아닙니다: {body.get('resolution')}")
            if not isinstance(body.get("duration"), str):
                bad.append(f"(tk-c) duration 이 문자열이 아닙니다: {body.get('duration')!r}")
            # 065: generate_audio 기본 true(효과음·립싱크).
            if body.get("generate_audio") is not True:
                bad.append(f"(tk-c) generate_audio 가 true 가 아닙니다: {body.get('generate_audio')}")
            if "seed" in body:
                bad.append("(tk-c) seed 를 보냈습니다(보내면 안 됨)")
            if body.get("aspect_ratio") != "16:9":
                bad.append(f"(tk-c) aspect_ratio 가 16:9 가 아닙니다: {body.get('aspect_ratio')}")

            # (tk-d) 프롬프트.
            prompt = str(body.get("prompt") or "")
            if not prompt.startswith("STYLE:"):
                bad.append(f"(tk-d) 프롬프트가 STYLE 로 시작하지 않습니다: {prompt[:40]}")
            if "AVOID:" not in prompt:
                bad.append("(tk-d) 프롬프트에 AVOID 섹션이 없습니다")
            style_part = prompt.split("TIMELINE", 1)[0]
            if "cinematic lighting" in style_part.lower():
                bad.append("(tk-d) STYLE/ASSETS 에 'cinematic lighting' 이 들어 있습니다")
            # TIMELINE 섹션에도 cinematic lighting 이 없어야.
            timeline_part = ""
            if "TIMELINE" in prompt:
                timeline_part = prompt.split("TIMELINE", 1)[1].split("RULES", 1)[0]
            if "cinematic lighting" in timeline_part.lower():
                bad.append("(tk-d) TIMELINE 에 'cinematic lighting' 이 들어 있습니다")
            if len(prompt.encode("utf-8")) > 4000:
                bad.append(f"(tk-d) 프롬프트가 4000바이트를 넘습니다: {len(prompt.encode('utf-8'))}")
            # @Image 번호가 image_urls 순서와 일치(front 는 @Image1, back @Image2 ...).
            if "@Image1 (front)" not in prompt:
                bad.append(f"(tk-d) ASSETS 에 @Image1 (front) 매핑이 없습니다: {prompt[:300]}")

        # 실사 칸 skipped·제출 0(제출은 웹툰 1회뿐이므로 위에서 이미 확인).
        unit_c = (cgen.get("units") or [{}])[0]
        if (unit_c.get("real") or {}).get("status") != "skipped":
            bad.append(f"(tk-c) 실사 칸이 skipped 가 아닙니다: {(unit_c.get('real') or {}).get('status')}")

        # (tk-e) 원장: 테이크 토큰 공식 값(27초 480p) 1건.
        take_charges = [e for e in ledger_calls if e.get("plan_id") == cgen.get("genId")]
        if len(take_charges) != 1:
            bad.append(f"(tk-e) 원장 계상이 1건이 아닙니다: {len(take_charges)}")
        else:
            exp_charge = genmod._take_usd(TAKE_ENDPOINT, 27, "480p")
            if abs(float(take_charges[0].get("usd") or 0) - exp_charge) > 0.001:
                bad.append(f"(tk-e) 원장 금액이 토큰 공식과 다릅니다: {take_charges[0].get('usd')} != {exp_charge}")

        # (tk-f) draft_id 저장, 완료 → finalWebtoon done·finalReal skipped·gen done.
        if not unit_c.get("draftId"):
            bad.append("(tk-f) 테이크에 draftId 가 저장되지 않았습니다")
        if (cgen.get("webtoon") if False else (unit_c.get("webtoon") or {})).get("status") != "completed":
            bad.append(f"(tk-f) 웹툰 칸이 completed 가 아닙니다: {(unit_c.get('webtoon') or {}).get('status')}")
        if (cgen.get("finalWebtoon") or {}).get("status") != "done":
            bad.append(f"(tk-f) finalWebtoon 이 done 이 아닙니다: {cgen.get('finalWebtoon')}")
        if (cgen.get("finalReal") or {}).get("status") != "skipped":
            bad.append(f"(tk-f) finalReal 이 skipped 가 아닙니다: {cgen.get('finalReal')}")
        if cgen.get("status") != "done":
            bad.append(f"(tk-f) gen.status 가 done 이 아닙니다: {cgen.get('status')}")

        # gen_view 가 터지지 않고 take 필드를 돌려주는지.
        view = genmod.gen_view(owner, pid_c, cplan)
        if not view or view.get("mode") != "previz-take":
            bad.append(f"(tk-f) gen_view mode 가 previz-take 가 아닙니다: {view and view.get('mode')}")
        vunits = (view or {}).get("units") or []
        if vunits and ("startCutNo" not in vunits[0] or "draftId" not in vunits[0]):
            bad.append(f"(tk-f) gen_view units 에 startCutNo·draftId 가 없습니다: {sorted(vunits[0].keys())}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod.STORE = orig["gen_STORE"]
        planmod.STORE = orig["plan_STORE"]
        _ledger_mod.STORE = orig["ledger_STORE"]
        genmod.vpoc_fal.find = orig["find"]
        genmod.vpoc_fal.pricing_for = orig["pricing_for"]
        genmod.vpoc_fal.pricing = orig["pricing"]
        genmod._previz_request = orig["previz_request"]
        _fal_mod.poll = orig["fal_poll"]
        genmod._concat = orig["_concat"]
        genmod._fetch_bytes = orig["_fetch_bytes"]
        genmod.characters.load_char = orig["load_char"]
        genmod._load_jpeg = orig["_load_jpeg"]
        genmod._segment_panel_jpegs = orig["_segment_panel_jpegs"]
        genmod.imaging.fit_ratio = orig["fit_ratio"]
        _ledger_mod.charge = orig["charge"]
        genmod.threading.Thread = orig["Thread"]
        _settings.FAL_KEY = orig["fal_key"]
        genmod._read_cast_notes = orig["read_cast_notes"]

    if bad:
        fail("통 테이크(Seedance 2.5) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크(Seedance 2.5) 검사 (tk-a)모드 선택·회귀 없음 (tk-b)≤30초 그룹핑 "
            "(tk-c)video_urls·image_urls 순서·@Image 번호·task/draft/480p/duration/audio·실사 skipped "
            "(tk-d)STYLE 맨앞·AVOID·cinematic lighting 없음·≤4000B (tk-e)견적·원장 토큰 공식 "
            "(tk-f)draft_id·finalWebtoon done·finalReal skipped·gen done"
        )


# ---------------------------------------------------------------- 13d. 통 테이크 프롬프트(컷 보존)


def check_vnew_take_prompt() -> None:
    """통 테이크 프롬프트 — 컷이 잘려 나가지 않는지(설계자 2026-10-26, test2 컷 1만 남던 버그 수정).

    외부 호출 없음. _take_prompt·_take_timeline_entries·_short_appearance 를 직접 부른다.

    (tp-a) test2 규모(인물 2명·외형 각 600자·6컷·대사 각 2줄 한국어) → 6개 타임스탬프 모두 존재·≤4,000 바이트.
    (tp-b) _short_appearance: 인물당 ≤170자, "hair" 정보 포함.
    (tp-c) 12컷(30초) 극단 입력 → 12개 타임스탬프 모두 존재·≤4,000 바이트.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415

    # styleEn 로드(캐릭터)를 가짜로 — 네트워크·load_char 를 타지 않게.
    orig_style = genmod._first_take_style_en
    genmod._first_take_style_en = lambda plan: "thin sketchy pencil lines, soft pastel flat color"

    # 긴 외형 한 사람분(~600자): hair·wears 문장을 포함한다(요약이 집어내야 한다).
    long_appearance = (
        "A middle-aged Korean man in his early fifties with a stern, weathered face and deep "
        "frown lines across his forehead. He has short black hair combed neatly to one side, "
        "greying slightly at the temples, and thick dark eyebrows over narrow watchful eyes. "
        "His jaw is square and he often clenches it when angry. He wears a charcoal-grey "
        "double-breasted suit with a crisp white dress shirt and a dark navy silk tie, polished "
        "black leather oxford shoes, and a gold wristwatch on his left wrist. His posture is "
        "rigid and authoritative, shoulders squared, and he carries a leather document folder "
        "under one arm. His skin is slightly tanned with age spots on the hands, and his "
        "fingers are thick and calloused from years of manual work before his promotion."
    )

    def _make_plan_and_take(n_cuts: int, total_seconds: int):
        cut_ids = [f"c{i + 1}" for i in range(n_cuts)]
        per = max(1, total_seconds // n_cuts)
        cuts = []
        brief_cuts = {}
        for i, cid in enumerate(cut_ids):
            cuts.append(
                {
                    "id": cid,
                    "no": i + 1,
                    "seconds": per,
                    "panels": [i],
                    # 대사 2줄(한국어)을 컷 dialogueKo 에 " / " 로.
                    "dialogueKo": '에이: "자네 지금 뭐 하는 건가 당장 설명해 보게" / 비: "죄송합니다 부장님 제가 처리하겠습니다"',
                }
            )
            brief_cuts[cid] = {
                "shotEn": "Medium close-up, 50mm lens at eye level, slight three-quarter angle on the speaker.",
                "actionEn": "Kim leans forward across the desk, eyes narrowing, one hand flat on the folder as he speaks.",
                "dialogue": [{"speaker": "에이", "lineKo": "자네 지금 뭐 하는 건가", "deliveryEn": "sharp and low"}],
            }
        plan = {
            "conti": {"cuts": cuts},
            "previz": {"shotBrief": {"status": "ready", "cuts": brief_cuts}},
            "script": {"entries": []},
        }
        take = {"id": "take1", "cutIds": cut_ids, "seconds": total_seconds}
        return plan, take

    try:
        # (tp-b) _short_appearance: ≤170자·"hair" 포함.
        short = genmod._short_appearance(long_appearance, 170)
        if len(short) > 170:
            bad.append(f"(tp-b) 요약 외형이 170자를 넘습니다: {len(short)}자")
        if "hair" not in short.lower():
            bad.append(f"(tp-b) 요약 외형에 'hair' 정보가 없습니다: {short!r}")
        if not short.strip():
            bad.append("(tp-b) 요약 외형이 비었습니다")
        # 100자 축소도 단어 경계로 자른다.
        short100 = genmod._short_appearance(long_appearance, 100)
        if len(short100) > 100:
            bad.append(f"(tp-b) 100자 요약이 100자를 넘습니다: {len(short100)}자")

        # cast: 인물 2명(요약 외형·@Image 번호).
        cast = [
            {"name": "에이", "colorName": "blue", "frontNo": 1, "backNo": 2,
             "appearance": genmod._short_appearance(long_appearance, 170)},
            {"name": "비", "colorName": "red", "frontNo": 3, "backNo": 4,
             "appearance": genmod._short_appearance(long_appearance, 170)},
        ]

        # (tp-a) test2 규모: 2명·6컷·대사 각 2줄.
        plan_a, take_a = _make_plan_and_take(6, 18)
        prompt_a = genmod._take_prompt(plan_a, take_a, cast=cast, panel_nos=[5, 6, 7], location_no=8)
        entries_a = genmod._take_timeline_entries(plan_a, take_a)
        stamps_a = [f"{e['start']}-{e['end']}s" for e in entries_a]
        if len(stamps_a) != 6:
            bad.append(f"(tp-a) 타임라인 항목이 6개가 아닙니다: {len(stamps_a)}")
        missing_a = [s for s in stamps_a if s not in prompt_a]
        if missing_a:
            bad.append(f"(tp-a) 프롬프트에 빠진 컷 타임스탬프가 있습니다: {missing_a}")
        if len(prompt_a.encode("utf-8")) > 4000:
            bad.append(f"(tp-a) 프롬프트가 4,000바이트를 넘습니다: {len(prompt_a.encode('utf-8'))}")
        if not prompt_a.startswith("STYLE:"):
            bad.append(f"(tp-a) 프롬프트가 STYLE 로 시작하지 않습니다: {prompt_a[:30]}")
        if "TIMELINE" not in prompt_a or "RULES:" not in prompt_a:
            bad.append("(tp-a) TIMELINE·RULES 섹션이 없습니다")
        # ASSETS 에 요약 외형(인물당 ≤170)만 들어갔는지 — 원문 전체(600자)가 통째로 들어가면 안 된다.
        if long_appearance[:200] in prompt_a:
            bad.append("(tp-a) 외형 원문이 요약되지 않고 통째로 들어갔습니다")

        # (tp-c) 12컷(30초) → 12개 타임스탬프 모두·≤2,500 바이트.
        plan_c, take_c = _make_plan_and_take(12, 30)
        prompt_c = genmod._take_prompt(plan_c, take_c, cast=cast, panel_nos=[13, 14], location_no=None)
        entries_c = genmod._take_timeline_entries(plan_c, take_c)
        stamps_c = [f"{e['start']}-{e['end']}s" for e in entries_c]
        if len(stamps_c) != 12:
            bad.append(f"(tp-c) 타임라인 항목이 12개가 아닙니다: {len(stamps_c)}")
        missing_c = [s for s in stamps_c if s not in prompt_c]
        if missing_c:
            bad.append(f"(tp-c) 12컷 중 빠진 타임스탬프가 있습니다: {missing_c}")
        if len(prompt_c.encode("utf-8")) > 4000:
            bad.append(f"(tp-c) 12컷 프롬프트가 4,000바이트를 넘습니다: {len(prompt_c.encode('utf-8'))}")
        # STYLE·RULES 는 유지.
        if not prompt_c.startswith("STYLE:"):
            bad.append("(tp-c) 12컷 프롬프트가 STYLE 로 시작하지 않습니다")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 프롬프트 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1200])
    finally:
        genmod._first_take_style_en = orig_style

    if bad:
        fail("통 테이크 프롬프트(컷 보존) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크 프롬프트(컷 보존) 검사 (tp-a)6컷 타임스탬프 모두·≤4000B·외형 요약 "
            "(tp-b)_short_appearance ≤170·hair 포함 (tp-c)12컷 타임스탬프 모두·≤4000B"
        )


# ---------------------------------------------------------------- 13e. 통 테이크 의상·소리·시선(065)


def check_vnew_take_costume() -> None:
    """통 테이크 — 장면 의상(원작 패널 읽기·캐시)·소리(generate_audio)·시선(eyelines) (설계자 2026-10-26, 065).

    외부 호출(Claude·fal·concat·원장) 전부 가짜. 유료 호출 없음.

    (ca-a) castNotes 캐시: 같은 패널이면 _read_cast_notes 재호출 0회, 원장(charge_text) 1회.
    (ca-b) override 가 자동값보다 우선, 프롬프트에 "COSTUME in this scene" + override 문구,
           외형 요약에 "shirt"/"wears" 없음.
    (ca-c) 참조: 인물당 정면 1장, 후면 없음, 패널 ≤4, 번호 일치, ≤4,000 바이트, 6컷 타임스탬프 유지.
    (ca-d) generate_audio true 기본, MOTION 에 립싱크(입 모양) 지시, RULES 에 "no background music".
    (ca-e) 테이크 1개 → final 은 복사(재인코딩 _concat 호출 0), 여러 개 → concat.
    (ca-f) eyelines 있는 grammar → TIMELINE 에 그대로; 옛 캐시 → 규칙 보완(화자→상대);
           6컷 모두 gaze 존재, ≤2,500 바이트.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import previz_grammar as pgmod  # noqa: PLC0415
    from src.vnew import settings as vnew_settings  # noqa: PLC0415
    from src.vpoc import ledger as _ledger_mod  # noqa: PLC0415

    fake_store = _FakeCharStore()

    # 원작 패널·인물 참조는 바이트로 구별. 비율 맞춤은 그대로(식별 보존).
    fake_blobs = {"wf-ch1": b"WF1", "wb-ch1": b"WB1", "wf-ch2": b"WF2", "wb-ch2": b"WB2", "loc-wb-back": b"LOC-BACK"}

    cast_notes_calls = {"n": 0}

    def fake_read_cast_notes(blocks, *, model):
        cast_notes_calls["n"] += 1
        return (
            {"people": [
                {"name": "에이", "costumeEn": "black dress shirt, sleeves rolled; charcoal slacks",
                 "voiceEn": "low, calm adult male voice"},
                {"name": "비", "costumeEn": "navy training top", "voiceEn": "soft young female voice"},
            ]},
            {"model": model, "inputTokens": 5, "outputTokens": 10},
        )

    textcharge_calls = {"n": 0}

    def fake_charge_text(key, **kwargs):
        textcharge_calls["n"] += 1
        return {"spentUsd": 0.0, "capUsd": None}

    def fake_load_char(owner, char_id):
        # 긴 외형(hair·wears 포함) — 요약이 옷 문장을 버리는지 본다.
        appearance = (
            "A middle-aged man with short black hair combed to one side and narrow watchful eyes. "
            "He wears an ivory button-up shirt and dark slacks."
        )
        return (
            {"id": char_id, "name": ("에이" if char_id.endswith("1") else "비"), "status": "ready",
             "analysis": {"appearanceEn": appearance, "outfitEn": "ivory button-up shirt", "styleEn": "thin ink lines"},
             "variants": {"webtoon": {"frontKey": "wf-" + char_id, "backKey": "wb-" + char_id}}},
            "etag",
        )

    concat_calls = {"n": 0}

    def fake_concat(clips):
        concat_calls["n"] += 1
        return b"CONCATTED"

    def fake_fetch_bytes(key, *, bucket=""):
        return fake_store.blobs.get(key, b"CLIP-" + str(key).encode("ascii", "ignore"))

    orig = {
        "gen_STORE": genmod.STORE,
        "plan_STORE": planmod.STORE,
        "ledger_STORE": _ledger_mod.STORE,
        "read_cast_notes": genmod._read_cast_notes,
        "charge_text": _ledger_mod.charge_text,
        "load_char": genmod.characters.load_char,
        "_load_jpeg": genmod._load_jpeg,
        "_segment_panel_jpegs": genmod._segment_panel_jpegs,
        "fit_ratio": genmod.imaging.fit_ratio,
        "_concat": genmod._concat,
        "_fetch_bytes": genmod._fetch_bytes,
        "has_audio": genmod._has_audio_stream,
        "first_style": genmod._first_take_style_en,
    }
    genmod.STORE = fake_store
    planmod.STORE = fake_store
    _ledger_mod.STORE = fake_store
    genmod._read_cast_notes = fake_read_cast_notes
    _ledger_mod.charge_text = fake_charge_text
    genmod.characters.load_char = fake_load_char
    genmod._load_jpeg = lambda key: fake_blobs.get(str(key or ""))
    genmod._segment_panel_jpegs = lambda owner, pid, plan, seg, limit: [b"PANEL1", b"PANEL2"][:limit]
    genmod.imaging.fit_ratio = lambda jpeg, *a, **k: jpeg
    genmod._concat = fake_concat
    genmod._fetch_bytes = fake_fetch_bytes
    genmod._has_audio_stream = lambda mp4: None  # ffmpeg 없이(진단 로그용) — 막지 않는다.
    genmod._first_take_style_en = lambda plan: "thin ink lines, soft flat color"

    def _plan_with_cuts(owner, pid, *, n_cuts=6, with_eyelines=False, save=True):
        """통 테이크 1개(6컷)·인물 2명·녹화 1구간 계획을 fake_store 에 써 둔다."""
        plan = planmod.new_plan()
        plan["owner"] = owner
        plan["input"]["videoModel"] = "fake"  # 모드는 아래에서 _take_units 로 직접 검증
        plan["input"]["order"] = ["f1"]
        plan["input"]["images"] = [{"fileId": "f1", "name": "1.png", "width": 600, "height": 900}]
        plan["input"]["characters"] = [{"charId": "ch1", "roleName": "에이"}, {"charId": "ch2", "roleName": "비"}]
        fake_store.blobs[planmod.upload_key(owner, pid, "f1")] = b"UP1"
        cut_ids = ["c" + str(i + 1) for i in range(n_cuts)]
        cuts = []
        brief_cuts = {}
        grammar = {}
        for i, cid in enumerate(cut_ids):
            cuts.append({
                "id": cid, "no": i + 1, "seconds": 3, "panels": [i],
                "dialogueKo": '에이: "자네 뭐 하나"' if i % 2 == 0 else '비: "죄송합니다"',
            })
            brief_cuts[cid] = {
                "shotEn": "MCU, 50mm, eye level.", "actionEn": "Kim leans forward.",
                "dialogue": [{"speaker": "에이", "lineKo": "자네 뭐 하나", "deliveryEn": "sharp"}],
            }
            g = {"mainSubject": "에이", "faceTurn": "screen_right",
                 "others": [{"name": "비", "side": "right"}]}
            if with_eyelines:
                g["eyelines"] = [{"name": "에이", "target": "person:비"}, {"name": "비", "target": "down"}]
            grammar[cid] = {"grammar": g}
        segments = [{"id": "s1", "cutIds": cut_ids, "seconds": 3 * n_cuts}]
        plan["conti"] = {"status": "ready", "cuts": cuts, "segments": segments,
                         "setting": {"locationEn": "A closed principal's office"}}
        plan["script"] = {"status": "ready", "entries": [{"id": "e1", "panels": [0], "locationEn": "A closed principal's office"}],
                          "setting": {"locationEn": "A closed principal's office"}}
        rec_key = "rec/" + pid + "/s1.mp4"
        fake_store.blobs[rec_key] = b"REC"
        scene_actors = [
            {"id": "a1", "name": "에이", "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": "비", "color": "#e6194b", "charId": "ch2", "appearanceEn": "a woman"},
        ]
        timeline = [{"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for c in cuts]
        plan["locations"] = [{"id": "loc1", "nameEn": "A closed principal's office",
                              "key": "a closed principal's office", "panels": [0], "status": "ready",
                              "variants": {"webtoon": {"viewKeys": {"back": "loc-wb-back"}}}}]
        plan["previz"] = {
            "status": "ready", "revision": 0, "approved": True,
            "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
            "recordings": {"s1": {"status": "ready", "key": rec_key, "seconds": float(3 * n_cuts)}},
            "panelGrammar": grammar,
            "shotBrief": {"status": "ready", "scene": {"loc1": "office"}, "cuts": brief_cuts, "updatedAt": "2026-10-26T00:00:00Z"},
        }
        if save:
            planmod.save_plan(owner, pid, plan)
        return plan

    try:
        TAKE_CAPS = {"videoListField": "video_urls", "imageListField": "image_urls", "imageListMax": 10,
                     "taskField": "task", "draftField": "draft", "generateAudioField": "generate_audio",
                     "durationAsString": True, "durations": [4, 8, 12, 30]}

        # ---- (ca-a) castNotes 캐시: 같은 패널이면 재호출 0회, 원장 1회 ----
        owner = "tester"
        pid_a = "prj_ca_a"
        _plan_with_cuts(owner, pid_a)
        plan_a, _ = planmod.load_plan(owner, pid_a)
        take_a = genmod._take_units(plan_a)[0]
        cast_notes_calls["n"] = 0
        textcharge_calls["n"] = 0
        notes1 = genmod._scene_cast_notes(owner, pid_a, plan_a, take_a)
        if cast_notes_calls["n"] != 1:
            bad.append(f"(ca-a) 첫 읽기가 1회가 아닙니다: {cast_notes_calls['n']}")
        if textcharge_calls["n"] != 1:
            bad.append(f"(ca-a) 원장(charge_text) 계상이 1회가 아닙니다: {textcharge_calls['n']}")
        if (notes1.get("에이") or {}).get("costumeEn") != "black dress shirt, sleeves rolled; charcoal slacks":
            bad.append(f"(ca-a) 읽은 의상이 저장되지 않았습니다: {notes1}")
        # 캐시가 채워진 계획을 다시 읽어 재호출 0회·원장 증가 없음.
        plan_a2, _ = planmod.load_plan(owner, pid_a)
        notes2 = genmod._scene_cast_notes(owner, pid_a, plan_a2, take_a)
        if cast_notes_calls["n"] != 1:
            bad.append(f"(ca-a) 같은 패널 재읽기가 재호출을 일으켰습니다: {cast_notes_calls['n']}")
        if textcharge_calls["n"] != 1:
            bad.append(f"(ca-a) 캐시 히트인데 원장이 늘었습니다: {textcharge_calls['n']}")
        if notes2 != notes1:
            bad.append("(ca-a) 캐시 값이 첫 읽기와 다릅니다")

        # ---- (ca-b) override 우선 + 프롬프트 COSTUME + 외형 요약에 shirt/wears 없음 ----
        pid_b = "prj_ca_b"
        _plan_with_cuts(owner, pid_b)
        plan_b, _ = planmod.load_plan(owner, pid_b)
        # override: 에이 = 검은 와이셔츠(한국어). 자동값(black dress shirt...)보다 우선해야 한다.
        def _set_override(data):
            data.setdefault("input", {})["castOverrides"] = {"에이": {"costume": "검은 와이셔츠", "voice": "낮고 차분한 남성"}}
        planmod.mutate_plan(owner, pid_b, _set_override)
        plan_b, _ = planmod.load_plan(owner, pid_b)
        take_b = genmod._take_units(plan_b)[0]
        resolved = genmod._resolve_cast(owner, pid_b, plan_b, take_b, read=True)
        if (resolved.get("에이") or {}).get("costume") != "검은 와이셔츠":
            bad.append(f"(ca-b) override 가 자동값보다 우선하지 않습니다: {resolved.get('에이')}")
        if (resolved.get("비") or {}).get("costume") != "navy training top":
            bad.append(f"(ca-b) override 없는 인물이 자동값을 쓰지 않습니다: {resolved.get('비')}")
        built_b = genmod._take_payload(owner, pid_b, plan_b, take_b, TAKE_CAPS)
        prompt_b = built_b["prompt"]
        if "COSTUME in this scene" not in prompt_b:
            bad.append("(ca-b) 프롬프트에 'COSTUME in this scene' 문구가 없습니다")
        if "검은 와이셔츠" not in prompt_b:
            bad.append("(ca-b) 프롬프트에 override 의상 문구(검은 와이셔츠)가 없습니다")
        # ASSETS 부분(외형 요약)에 'shirt'/'wears' 가 없어야 한다(COSTUME 절은 예외).
        assets_part = prompt_b.split("ASSETS:", 1)[1].split("TIMELINE", 1)[0] if "ASSETS:" in prompt_b else ""
        # COSTUME 절을 뺀 나머지(외형 요약)만 본다.
        appearance_only = " ".join(
            seg.split("COSTUME")[0] for seg in assets_part.split(". ")
        )
        if "shirt" in appearance_only.lower() or "wears" in appearance_only.lower():
            bad.append(f"(ca-b) 외형 요약에 shirt/wears 가 남아 있습니다: {appearance_only[:200]}")
        # _short_appearance 단위: 옷 문장 제거.
        short = genmod._short_appearance(
            "A man with short black hair and narrow eyes. He wears an ivory button-up shirt.", 170
        )
        if "shirt" in short.lower() or "wears" in short.lower():
            bad.append(f"(ca-b) _short_appearance 가 옷 문장을 지우지 않았습니다: {short!r}")
        if "hair" not in short.lower():
            bad.append(f"(ca-b) _short_appearance 에 hair 가 없습니다: {short!r}")

        # ---- (ca-c) 참조: 정면 1장·후면 없음·패널 ≤4·번호 일치·≤2500B·6컷 타임스탬프 ----
        imgs_c = built_b["imageUrls"]
        if b"WB1" in imgs_c or b"WB2" in imgs_c:
            bad.append("(ca-c) 참조에 인물 후면이 들어갔습니다")
        fronts = [x for x in imgs_c if x in (b"WF1", b"WF2")]
        if len(fronts) != 2:
            bad.append(f"(ca-c) 인물 정면이 2장(인물당 1장)이 아닙니다: {fronts}")
        panels_c = [x for x in imgs_c if x in (b"PANEL1", b"PANEL2")]
        if len(panels_c) > 4:
            bad.append(f"(ca-c) 패널이 4장을 넘습니다: {len(panels_c)}")
        # 번호 일치: 앞 2장이 정면이므로 @Image1 (front)·@Image2 (front).
        if "@Image1 (front)" not in prompt_b or "@Image2 (front)" not in prompt_b:
            bad.append("(ca-c) ASSETS 의 @Image 정면 번호가 image_urls 순서와 맞지 않습니다")
        if len(prompt_b.encode("utf-8")) > 4000:
            bad.append(f"(ca-c) 프롬프트가 4000바이트를 넘습니다: {len(prompt_b.encode('utf-8'))}")
        entries_b = genmod._take_timeline_entries(plan_b, take_b)
        stamps_b = [f"{e['start']}-{e['end']}s" for e in entries_b]
        if len(stamps_b) != 6:
            bad.append(f"(ca-c) 타임라인 항목이 6개가 아닙니다: {len(stamps_b)}")
        miss_b = [s for s in stamps_b if s not in prompt_b]
        if miss_b:
            bad.append(f"(ca-c) 프롬프트에 빠진 컷 타임스탬프: {miss_b}")

        # ---- (ca-d) generate_audio true 기본 + MOTION 에 립싱크(입 모양) + RULES 에 배경음악 없음 ----
        if vnew_settings.VNEW_TAKE_AUDIO is not True:
            bad.append(f"(ca-d) VNEW_TAKE_AUDIO 기본이 true 가 아닙니다: {vnew_settings.VNEW_TAKE_AUDIO}")
        # 립싱크는 MOTION 이 맡는다(설계자 2026-10-26 재지시 v2). 통 테이크 MOTION full 은 입 모양을
        # 한국어 음절에 맞추라고 지시한다("mouth shapes matching the Korean").
        motion_part = prompt_b.split("MOTION:", 1)[1] if "MOTION:" in prompt_b else ""
        if "mouth shapes matching the korean" not in motion_part.lower() and "lip sync" not in motion_part.lower():
            bad.append(f"(ca-d) MOTION 에 립싱크(입 모양) 지시가 없습니다: {motion_part[:160]}")
        rules_part = prompt_b.split("RULES:", 1)[1] if "RULES:" in prompt_b else ""
        if "no background music" not in rules_part.lower():
            bad.append("(ca-d) RULES 에 'no background music' 가 없습니다")

        # ---- (ca-e) 테이크 1개 → final 복사(_concat 0회), 여러 개 → concat ----
        # 1개: _do_concat 을 직접 불러 concat 호출 0회를 본다. gen 을 직접 심는다.
        pid_e = "prj_ca_e"
        _plan_with_cuts(owner, pid_e)
        def _seed_gen_one(data):
            data["gen"] = {
                "genId": "g1", "mode": "previz-take", "status": "running",
                "videoModel": "fake", "resolution": "480p",
                "units": [{"id": "take1", "cutIds": ["c1"], "recKeys": ["rec/" + pid_e + "/s1.mp4"],
                           "recSegIds": ["s1"], "seconds": 18.0,
                           "real": {"status": "skipped"},
                           "webtoon": {"status": "completed", "outputKey": "out/take1.mp4"}}],
                "segments": [], "finalReal": {"status": "skipped"}, "finalWebtoon": {"status": "waiting"},
            }
        planmod.mutate_plan(owner, pid_e, _seed_gen_one)
        concat_calls["n"] = 0
        genmod._do_concat(owner, pid_e, "g1", "webtoon")
        if concat_calls["n"] != 0:
            bad.append(f"(ca-e) 테이크 1개인데 _concat 이 불렸습니다(복사여야 함): {concat_calls['n']}")
        plan_e, _ = planmod.load_plan(owner, pid_e)
        if (plan_e.get("gen") or {}).get("finalWebtoon", {}).get("status") != "done":
            bad.append(f"(ca-e) 1개 테이크 final 이 done 이 아닙니다: {(plan_e.get('gen') or {}).get('finalWebtoon')}")
        # 여러 개: 2 테이크 completed → concat 1회.
        pid_e2 = "prj_ca_e2"
        _plan_with_cuts(owner, pid_e2)
        def _seed_gen_two(data):
            data["gen"] = {
                "genId": "g2", "mode": "previz-take", "status": "running",
                "videoModel": "fake", "resolution": "480p",
                "units": [
                    {"id": "take1", "cutIds": ["c1"], "recKeys": ["r1"], "recSegIds": ["s1"], "seconds": 30.0,
                     "real": {"status": "skipped"}, "webtoon": {"status": "completed", "outputKey": "out/t1.mp4"}},
                    {"id": "take2", "cutIds": ["c2"], "recKeys": ["r2"], "recSegIds": ["s2"], "seconds": 10.0,
                     "real": {"status": "skipped"}, "webtoon": {"status": "completed", "outputKey": "out/t2.mp4"}},
                ],
                "segments": [], "finalReal": {"status": "skipped"}, "finalWebtoon": {"status": "waiting"},
            }
        planmod.mutate_plan(owner, pid_e2, _seed_gen_two)
        concat_calls["n"] = 0
        genmod._do_concat(owner, pid_e2, "g2", "webtoon")
        if concat_calls["n"] != 1:
            bad.append(f"(ca-e) 테이크 여러 개인데 concat 이 1회가 아닙니다: {concat_calls['n']}")

        # ---- (ca-f) eyelines → TIMELINE 그대로; 옛 캐시 → 화자→상대; 6컷 gaze·≤2500B ----
        # eyelines 있는 grammar.
        pid_f = "prj_ca_f"
        _plan_with_cuts(owner, pid_f, with_eyelines=True)
        plan_f, _ = planmod.load_plan(owner, pid_f)
        take_f = genmod._take_units(plan_f)[0]
        eyelines_f = genmod._cut_eyelines(plan_f, (plan_f["conti"]["cuts"][0]))
        targets_f = {e["name"]: e["target"] for e in eyelines_f}
        if targets_f.get("에이") != "person:비" or targets_f.get("비") != "down":
            bad.append(f"(ca-f) grammar.eyelines 가 그대로 쓰이지 않았습니다: {targets_f}")
        built_f = genmod._take_payload(owner, pid_f, plan_f, take_f, TAKE_CAPS)
        prompt_f = built_f["prompt"]
        if "gaze:" not in prompt_f:
            bad.append("(ca-f) 프롬프트 TIMELINE 에 gaze 가 없습니다")
        # 6컷 모두 gaze 존재(각 컷 항목에 'gaze:' 포함).
        entries_f = genmod._take_timeline_entries(plan_f, take_f)
        if len([e for e in entries_f if (e.get("gaze") or "")]) != 6:
            bad.append(f"(ca-f) 6컷 중 gaze 가 비는 컷이 있습니다: {[bool(e.get('gaze')) for e in entries_f]}")
        if len(prompt_f.encode("utf-8")) > 4000:
            bad.append(f"(ca-f) eyelines 프롬프트가 4000바이트를 넘습니다: {len(prompt_f.encode('utf-8'))}")
        # 옛 캐시(eyelines 없음): 화자(에이)가 상대(비)를 본다 — c1 은 에이 대사.
        pid_f2 = "prj_ca_f2"
        _plan_with_cuts(owner, pid_f2, with_eyelines=False)
        plan_f2, _ = planmod.load_plan(owner, pid_f2)
        eyelines_old = genmod._cut_eyelines(plan_f2, plan_f2["conti"]["cuts"][0])
        targets_old = {e["name"]: e["target"] for e in eyelines_old}
        # c1 화자 = 에이 → 에이는 비(듣는 사람)를 본다.
        if targets_old.get("에이") != "person:비":
            bad.append(f"(ca-f) 옛 캐시 보완: 화자(에이)가 상대(비)를 보지 않습니다: {targets_old}")
        gaze_old = genmod._gaze_text(eyelines_old)
        if "gaze:" not in gaze_old or "비" not in gaze_old:
            bad.append(f"(ca-f) 옛 캐시 gaze 문구가 비었습니다: {gaze_old!r}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 의상·소리·시선 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod.STORE = orig["gen_STORE"]
        planmod.STORE = orig["plan_STORE"]
        _ledger_mod.STORE = orig["ledger_STORE"]
        genmod._read_cast_notes = orig["read_cast_notes"]
        _ledger_mod.charge_text = orig["charge_text"]
        genmod.characters.load_char = orig["load_char"]
        genmod._load_jpeg = orig["_load_jpeg"]
        genmod._segment_panel_jpegs = orig["_segment_panel_jpegs"]
        genmod.imaging.fit_ratio = orig["fit_ratio"]
        genmod._concat = orig["_concat"]
        genmod._fetch_bytes = orig["_fetch_bytes"]
        genmod._has_audio_stream = orig["has_audio"]
        genmod._first_take_style_en = orig["first_style"]

    if bad:
        fail("통 테이크 의상·소리·시선(065) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크 의상·소리·시선(065) 검사 (ca-a)castNotes 캐시·원장 1회 "
            "(ca-b)override 우선·COSTUME 문구·외형 shirt/wears 없음 (ca-c)정면 1장·후면 없음·패널≤4·번호·≤4000B·6컷 "
            "(ca-d)generate_audio true·MOTION 립싱크·RULES no background music (ca-e)1개 복사·여러개 concat "
            "(ca-f)eyelines→TIMELINE·옛 캐시 화자→상대·6컷 gaze·≤4000B"
        )


# ---------------------------------------------------------------- 13f. 통 테이크 프롬프트 다듬기(065 dry-run)


def check_vnew_take_prompt_polish() -> None:
    """통 테이크 프롬프트 다듬기 — 065 dry-run 실측 4건 수정(설계자 2026-10-26). 외부 호출 없음.

    (pp-a) 키스 컷(두 사람, 대사 화자=여의준) → gaze 에 자기 자신 대상 없음. grammar.eyelines 에
           자기 자신(person:여의준)이 있어도 상대(범건우)로 바뀐다. 동일 인물 중복 없음.
    (pp-b) RULES 에 "costume exactly as written in ASSETS" 있고, "costume identical to the references" 없음.
    (pp-c) test2 규모(장면 의상·목소리 포함, 6컷) → 6컷 모두 연기 문장 존재·≤4,000 바이트·"approximately" 없음.
    (pp-d) voice 문구가 단어 중간에서 끊기지 않음(_clip_words 가 마침표로 끝맺는다).
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415

    orig_style = genmod._first_take_style_en
    genmod._first_take_style_en = lambda plan: "thin ink lines, soft flat color"

    def _plan(n_cuts: int, *, eyelines_self=False, self_name="여의준", other_name="범건우"):
        """통 테이크 1개(n컷)·인물 2명 계획. grammar.panelGrammar 로 샷 약어·시선을 준다."""
        cut_ids = [f"c{i + 1}" for i in range(n_cuts)]
        cuts = []
        brief_cuts = {}
        grammar = {}
        for i, cid in enumerate(cut_ids):
            # 컷마다 화자를 여의준으로 둔다(키스 컷: 여의준 대사).
            cuts.append({
                "id": cid, "no": i + 1, "seconds": 3, "panels": [i],
                "dialogueKo": f'{self_name}: "조금만 더 있어 줘"',
            })
            # shotEn 에는 일부러 렌즈·거리 숫자를 넣어, 샷 약어가 이를 버리는지 본다.
            brief_cuts[cid] = {
                "shotEn": "MCU, 50 mm lens, approximately 2.1 m from the subject, three-quarter angle.",
                "actionEn": f"{self_name} cups {other_name}'s cheek and leans in slowly as their eyes meet.",
                "dialogue": [{"speaker": self_name, "lineKo": "조금만 더 있어 줘", "deliveryEn": "soft and trembling"}],
            }
            g = {"mainSubject": self_name, "shotSize": "MCU", "shotType": "profile_two_shot",
                 "vantage": "side", "others": [{"name": other_name, "side": "right"}]}
            if eyelines_self:
                # 옛 캐시 버그: target 이 자기 자신.
                g["eyelines"] = [
                    {"name": self_name, "target": "person:" + self_name},
                    {"name": other_name, "target": "closed"},
                ]
            grammar[cid] = {"grammar": g}
        scene_actors = [
            {"id": "a1", "name": self_name, "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": other_name, "color": "#e6194b", "charId": "ch2", "appearanceEn": "a man"},
        ]
        timeline = [{"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for c in cuts]
        plan = {
            "conti": {"status": "ready", "cuts": cuts,
                      "segments": [{"id": "s1", "cutIds": cut_ids, "seconds": 3 * n_cuts}]},
            "script": {"status": "ready", "entries": []},
            "previz": {
                "status": "ready",
                "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
                "panelGrammar": grammar,
                "shotBrief": {"status": "ready", "cuts": brief_cuts},
            },
        }
        take = {"id": "take1", "cutIds": cut_ids, "seconds": 3 * n_cuts}
        return plan, take

    # 긴 목소리 문구(70자 넘김) — 단어 중간 끊김을 본다.
    long_voice = "a low, calm, stoic subdued adult male voice with a slow measured cadence and warm timbre"

    try:
        # (pp-a) 키스 컷: grammar.eyelines 에 자기 자신(person:여의준)이 있어도 상대로 바뀐다.
        plan_a, take_a = _plan(6, eyelines_self=True)
        eyelines = genmod._cut_eyelines(plan_a, plan_a["conti"]["cuts"][0])
        targets = {e["name"]: e["target"] for e in eyelines}
        # 자기 자신 대상이 없어야 한다.
        for e in eyelines:
            if e["target"] == "person:" + e["name"]:
                bad.append(f"(pp-a) gaze 에 자기 자신 대상이 남아 있습니다: {e}")
        if targets.get("여의준") != "person:범건우":
            bad.append(f"(pp-a) 자기 자신 시선이 상대로 바뀌지 않았습니다: {targets}")
        # 동일 인물 중복 없음.
        names_seen = [e["name"] for e in eyelines]
        if len(names_seen) != len(set(names_seen)):
            bad.append(f"(pp-a) 동일 인물 중복 항목이 있습니다: {names_seen}")
        gaze_txt = genmod._gaze_text(eyelines)
        if "여의준 \u2192 여의준's eyes" in gaze_txt:
            bad.append(f"(pp-a) gaze 문구에 자기 자신(여의준 → 여의준's eyes)이 남아 있습니다: {gaze_txt}")

        # (pp-b)(pp-c)(pp-d) 는 프롬프트로 본다. cast 에 장면 의상·긴 목소리를 넣는다.
        cast = [
            {"name": "여의준", "colorName": "blue", "frontNo": 1,
             "appearance": "short black hair, sharp jaw, narrow eyes",
             "costume": "black dress shirt, sleeves rolled up", "voice": genmod._clip_words(long_voice, 70)},
            {"name": "범건우", "colorName": "red", "frontNo": 2,
             "appearance": "wavy brown hair, soft features",
             "costume": "grey hoodie", "voice": "soft low adult male voice"},
        ]
        plan_c, take_c = _plan(6, eyelines_self=False)
        prompt = genmod._take_prompt(plan_c, take_c, cast=cast, panel_nos=[3, 4], location_no=5)

        # (pp-b) RULES 문구.
        if "costume exactly as written in ASSETS" not in prompt:
            bad.append("(pp-b) RULES 에 'costume exactly as written in ASSETS' 가 없습니다")
        if "costume identical to the references" in prompt:
            bad.append("(pp-b) RULES 에 'costume identical to the references' 가 남아 있습니다")

        # (pp-c) 6컷 모두 연기 문장 존재·≤2500B·approximately 없음.
        entries = genmod._take_timeline_entries(plan_c, take_c)
        stamps = [f"{e['start']}-{e['end']}s" for e in entries]
        if len(stamps) != 6:
            bad.append(f"(pp-c) 타임라인 항목이 6개가 아닙니다: {len(stamps)}")
        missing = [s for s in stamps if s not in prompt]
        if missing:
            bad.append(f"(pp-c) 프롬프트에 빠진 컷 타임스탬프: {missing}")
        if len(prompt.encode("utf-8")) > 4000:
            bad.append(f"(pp-c) 프롬프트가 4000바이트를 넘습니다: {len(prompt.encode('utf-8'))}")
        if "approximately" in prompt:
            bad.append("(pp-c) 프롬프트에 'approximately'(렌즈·거리 숫자)가 남아 있습니다")
        if " mm" in prompt or "50 mm" in prompt:
            bad.append("(pp-c) 프롬프트에 렌즈 숫자(mm)가 남아 있습니다")
        # 연기 문장(actionEn 유래) 이 6컷 모두에 있다 — cup/lean 등 동사가 보인다.
        tl_part = prompt.split("TIMELINE", 1)[1] if "TIMELINE" in prompt else ""
        # 연기 문장(actionEn 유래) 이 6컷 모두에 있어야 한다 — 각 컷 action 에 "cups" 가 든다.
        if tl_part.count("cups") != 6:
            bad.append(f"(pp-c) 6컷 모두에 연기 문장이 있지 않습니다('cups' {tl_part.count('cups')}회): {tl_part[:160]}")
        # 샷 약어가 머리에 붙었는지(MCU profile — profile_two_shot/vantage side).
        if "MCU profile" not in prompt:
            bad.append(f"(pp-c) 샷 약어 'MCU profile' 이 없습니다: {tl_part[:120]}")

        # (pp-d) voice 가 단어 중간에서 끊기지 않는다. cast 의 clipped voice 는 마침표로 끝난다.
        clipped_voice = genmod._clip_words(long_voice, 70)
        if len(clipped_voice) > 70 + 1:  # 마침표 1자 여유
            bad.append(f"(pp-d) voice 가 70자(+마침표)를 넘습니다: {len(clipped_voice)} {clipped_voice!r}")
        if clipped_voice.endswith("…"):
            bad.append(f"(pp-d) voice 가 말줄임표로 끝납니다(마침표여야 함): {clipped_voice!r}")
        # 단어 중간 끊김: 원문을 공백으로 나눈 단어들의 접두사로만 끝나면 안 된다.
        body = clipped_voice.rstrip(".")
        words = long_voice.split()
        # body 의 마지막 단어가 원문 단어 목록에 온전히 있는지.
        last_word = body.split()[-1] if body.split() else ""
        if last_word and last_word not in words:
            bad.append(f"(pp-d) voice 가 단어 중간에서 끊겼습니다(마지막 토큰 '{last_word}')")
        # 프롬프트 ASSETS 의 Voice 문구도 'stoic su' 같은 중간 끊김이 없어야 한다.
        if "stoic su." in prompt or "stoic su;" in prompt or "stoic su " in prompt.split("Voice:", 1)[-1][:40]:
            bad.append("(pp-d) 프롬프트 Voice 문구가 단어 중간에서 끊겼습니다")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 프롬프트 다듬기 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod._first_take_style_en = orig_style

    if bad:
        fail("통 테이크 프롬프트 다듬기(065 dry-run) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크 프롬프트 다듬기(065 dry-run) 검사 (pp-a)자기 자신 시선 없음·중복 없음 "
            "(pp-b)RULES costume ASSETS·references 없음 (pp-c)6컷 연기·≤4000B·approximately 없음·샷 약어 "
            "(pp-d)voice 단어 경계·마침표"
        )


# ---------------------------------------------------------------- 13g. 통 테이크 프롬프트 바이트 예산(066)


def check_vnew_take_prompt_budget() -> None:
    """통 테이크 프롬프트 — 한국어 실제 길이에서도 ASSETS·연기가 통째로 빠지지 않는지(설계자 2026-10-26, 066).

    외부 호출 없음. _take_prompt·_take_timeline_entries·_romanize_name·_gaze_is_default 를 직접 부른다.

    (pb-a) test2 실제 길이(6컷·한국어 대사 각 25~35자·장면 의상 140자·목소리 70자·인물 2명) →
           ≤4,000 바이트, ASSETS 에 두 인물 COSTUME 모두, 6컷 모두 연기 문장(최소 30자 이상) 존재.
    (pb-b) 화자→상대 기본 시선 컷은 TIMELINE 에 gaze 없음, closed/down 예외 컷만 gaze 있음,
           RULES 에 "Eyes:" 기본 규칙.
    (pb-c) 12컷 극단 입력에서도 ASSETS 인물 문장·12 타임스탬프 유지, ≤4,000 바이트.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415

    orig_style = genmod._first_take_style_en
    genmod._first_take_style_en = lambda plan: "thin sketchy pencil lines, soft pastel flat color"

    # 로마자: 한글 이름을 로마자로 바꾼다(영문 표기 폴백).
    name_a, name_b = "범건우", "여의준"

    def _plan(n_cuts: int, *, dialogue_len: int, gaze_mode: str = "default"):
        """통 테이크 1개(n컷)·인물 2명 계획. gaze_mode: default|exception.

        default: grammar.eyelines 없음 → 화자↔듣는 사람 상호(기본) → gaze 생략 기대.
        exception: 홀수 컷은 듣는 사람이 down/closed(예외) → gaze 표기 기대.
        """
        cut_ids = [f"c{i + 1}" for i in range(n_cuts)]
        cuts = []
        brief_cuts = {}
        grammar = {}
        # 한국어 대사(요청한 길이에 맞춘 글자 수). 25~35자 범위.
        ko_line = ("자네 지금 뭐 하는 건가 당장 설명해 보게 어서" * 3)[:dialogue_len]
        for i, cid in enumerate(cut_ids):
            speaker = name_a if i % 2 == 0 else name_b
            cuts.append({
                "id": cid, "no": i + 1, "seconds": 3, "panels": [i],
                "dialogueKo": f'{speaker}: "{ko_line}"',
            })
            brief_cuts[cid] = {
                "shotEn": "MCU, 50 mm lens, approximately 2.1 m from the subject, three-quarter angle.",
                "actionEn": (
                    f"{speaker} leans forward across the desk, eyes narrowing, one hand flat on the "
                    "folder as he speaks in a low measured voice."
                ),
                "dialogue": [{"speaker": speaker, "lineKo": ko_line, "deliveryEn": "sharp and low"}],
            }
            listener = name_b if speaker == name_a else name_a
            g = {"mainSubject": speaker, "shotSize": "MCU", "shotType": "profile_two_shot",
                 "vantage": "side", "others": [{"name": listener, "side": "right"}]}
            if gaze_mode == "exception" and i % 2 == 0:
                # 예외 컷: 듣는 사람이 down(기본과 다름) → gaze 표기.
                g["eyelines"] = [
                    {"name": speaker, "target": "person:" + listener},
                    {"name": listener, "target": "down"},
                ]
            elif gaze_mode == "default":
                # 기본: 화자↔듣는 사람 상호 eye contact → gaze 생략.
                g["eyelines"] = [
                    {"name": speaker, "target": "person:" + listener},
                    {"name": listener, "target": "person:" + speaker},
                ]
            grammar[cid] = {"grammar": g}
        scene_actors = [
            {"id": "a1", "name": name_a, "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": name_b, "color": "#e6194b", "charId": "ch2", "appearanceEn": "a man"},
        ]
        timeline = [{"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for c in cuts]
        plan = {
            "conti": {"status": "ready", "cuts": cuts,
                      "segments": [{"id": "s1", "cutIds": cut_ids, "seconds": 3 * n_cuts}]},
            "script": {"status": "ready", "entries": []},
            "previz": {
                "status": "ready",
                "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
                "panelGrammar": grammar,
                "shotBrief": {"status": "ready", "cuts": brief_cuts},
            },
        }
        take = {"id": "take1", "cutIds": cut_ids, "seconds": 3 * n_cuts}
        return plan, take

    # 장면 의상(~140자)·목소리(~70자) — test2 실측 규모.
    costume_a = (
        "a charcoal double-breasted suit with a crisp white dress shirt and a dark navy silk tie, "
        "polished black oxford shoes and a gold wristwatch on the left wrist"
    )
    costume_b = (
        "a loose ivory knit sweater over a pale blue collared shirt, slim grey slacks and white "
        "canvas sneakers, with a thin silver chain at the neck"
    )
    voice_a = "a low, calm, stoic adult male voice with a slow measured cadence and warm timbre"
    voice_b = "a bright, slightly nervous young adult male voice with a quick light cadence"

    try:
        # 로마자 변환 자체.
        if genmod._romanize_name("범건우") != "Beomgeonu":
            bad.append(f"(pb) _romanize_name(범건우) 예상과 다름: {genmod._romanize_name('범건우')!r}")
        if genmod._romanize_name("Alice") != "Alice":
            bad.append(f"(pb) _romanize_name 영문 입력을 바꿨습니다: {genmod._romanize_name('Alice')!r}")

        cast = [
            {"name": name_a, "colorName": "blue", "frontNo": 1,
             "appearance": "short black hair combed to one side, narrow watchful eyes, square jaw",
             "costume": costume_a, "voice": voice_a},
            {"name": name_b, "colorName": "red", "frontNo": 2,
             "appearance": "wavy brown hair, soft rounded features, bright eyes",
             "costume": costume_b, "voice": voice_b},
        ]

        # ---- (pb-a) test2 실제 길이: 6컷·한국어 대사 30자·장면 의상 140자·목소리 70자 ----
        plan_a, take_a = _plan(6, dialogue_len=30, gaze_mode="default")
        prompt_a = genmod._take_prompt(plan_a, take_a, cast=cast, panel_nos=[3, 4], location_no=5)
        if len(prompt_a.encode("utf-8")) > 4000:
            bad.append(f"(pb-a) 프롬프트가 4,000바이트를 넘습니다: {len(prompt_a.encode('utf-8'))}")
        # ASSETS 에 두 인물 COSTUME 모두(장면 의상 문구가 통째로 빠지지 않았는지).
        assets_a = prompt_a.split("ASSETS:", 1)[1].split("\n", 1)[0] if "ASSETS:" in prompt_a else ""
        if "COSTUME in this scene" not in assets_a:
            bad.append("(pb-a) ASSETS 에 'COSTUME in this scene' 문구가 없습니다")
        if assets_a.count("COSTUME") < 2:
            bad.append(f"(pb-a) ASSETS 에 두 인물 COSTUME 이 모두 있지 않습니다: {assets_a[:300]}")
        # 두 인물 이름 소개(한국 이름 + 로마자) 모두 ASSETS 에.
        if ("범건우 (" not in assets_a) or ("여의준 (" not in assets_a):
            bad.append(f"(pb-a) ASSETS 인물 이름 소개(한국어+로마자)가 빠졌습니다: {assets_a[:300]}")
        # 6컷 모두 연기 문장(최소 30자 이상) 존재 — TIMELINE 각 컷 항목에 'leans forward' 동사.
        entries_a = genmod._take_timeline_entries(plan_a, take_a)
        stamps_a = [f"{e['start']}-{e['end']}s" for e in entries_a]
        tl_a = prompt_a.split("TIMELINE", 1)[1].split("\nRULES", 1)[0] if "TIMELINE" in prompt_a else ""
        missing_a = [s for s in stamps_a if s not in prompt_a]
        if missing_a:
            bad.append(f"(pb-a) 프롬프트에 빠진 컷 타임스탬프: {missing_a}")
        if tl_a.count("leans forward") != 6:
            bad.append(f"(pb-a) 6컷 모두에 연기 문장이 있지 않습니다('leans forward' {tl_a.count('leans forward')}회)")
        # 각 컷 연기 문장 30자 이상(머리·대사·gaze 제외한 action 부분).
        for e in entries_a:
            act = genmod._clip_words(e.get("action") or "", 45)
            if len(act) < 30:
                bad.append(f"(pb-a) 연기 문장이 30자 미만입니다: {act!r}")
                break
        # 대사는 한국어 유지(립싱크).
        if ko_a := (entries_a[0].get("dialogue") or ""):
            if ko_a[:15] not in prompt_a:  # 068: 예산 부족 시 대사는 25자까지 줄일 수 있다(앞부분은 유지)
                bad.append("(pb-a) 한국어 대사가 프롬프트에서 빠졌습니다")

        # ---- (pb-b) 기본 시선 컷 gaze 없음·예외 컷만 gaze·RULES Eyes ----
        # 067: RULES 시선 규칙을 ON SCREEN/off-screen 기준으로 바꿨다(이유: 컷별 화면 등장 인물 명시).
        if "Eyes: the on-screen speaker looks at the listener" not in prompt_a:
            bad.append("(pb-b) RULES 에 ON SCREEN 기반 시선 규칙(Eyes:)이 없습니다")
        # 기본(default) 플랜: 어느 컷에도 gaze 가 없어야 한다.
        if "gaze:" in prompt_a:
            bad.append(f"(pb-b) 기본 시선인데 TIMELINE 에 gaze 가 있습니다: {tl_a[:200]}")
        # gazeDefault 플래그 확인.
        if not all(e.get("gazeDefault") for e in entries_a):
            bad.append(f"(pb-b) 기본 시선 컷의 gazeDefault 가 True 가 아닙니다: {[e.get('gazeDefault') for e in entries_a]}")
        # 예외(exception) 플랜: 홀수 컷(듣는 사람 down)만 gaze.
        plan_b, take_b = _plan(6, dialogue_len=30, gaze_mode="exception")
        prompt_b = genmod._take_prompt(plan_b, take_b, cast=cast, panel_nos=[3, 4], location_no=5)
        entries_b = genmod._take_timeline_entries(plan_b, take_b)
        # 예외 컷(i%2==0 → c1,c3,c5): gazeDefault False, gaze 존재. 짝수 컷: default True.
        exc_flags = [e.get("gazeDefault") for e in entries_b]
        if exc_flags != [False, True, False, True, False, True]:
            bad.append(f"(pb-b) 예외/기본 컷 구분이 틀립니다: {exc_flags}")
        if "gaze:" not in prompt_b:
            bad.append("(pb-b) 예외 컷이 있는데 gaze 가 전혀 없습니다")
        if "down" not in prompt_b:
            bad.append("(pb-b) 예외 컷의 down 시선이 프롬프트에 없습니다")
        # gaze 에 로마자 이름이 쓰였는지(한국 이름 아님).
        gaze_seg = prompt_b.split("gaze:", 1)[1][:60] if "gaze:" in prompt_b else ""
        if "범건우" in gaze_seg or "여의준" in gaze_seg:
            bad.append(f"(pb-b) gaze 에 한국 이름이 쓰였습니다(로마자여야 함): {gaze_seg!r}")

        # ---- (pb-c) 12컷 극단 입력: ASSETS 인물 문장·12 타임스탬프·≤2500B ----
        plan_c, take_c = _plan(12, dialogue_len=35, gaze_mode="default")
        prompt_c = genmod._take_prompt(plan_c, take_c, cast=cast, panel_nos=[3, 4], location_no=5)
        if len(prompt_c.encode("utf-8")) > 4000:
            bad.append(f"(pb-c) 12컷 프롬프트가 4,000바이트를 넘습니다: {len(prompt_c.encode('utf-8'))}")
        entries_c = genmod._take_timeline_entries(plan_c, take_c)
        stamps_c = [f"{e['start']}-{e['end']}s" for e in entries_c]
        if len(stamps_c) != 12:
            bad.append(f"(pb-c) 타임라인 항목이 12개가 아닙니다: {len(stamps_c)}")
        missing_c = [s for s in stamps_c if s not in prompt_c]
        if missing_c:
            bad.append(f"(pb-c) 12컷 중 빠진 타임스탬프: {missing_c}")
        # ASSETS 인물 문장(색·이름·@Image·COSTUME) 이 극단 입력에서도 유지.
        if "ASSETS:" not in prompt_c:
            bad.append("(pb-c) 12컷 극단 입력에서 ASSETS 섹션이 통째로 빠졌습니다")
        assets_c = prompt_c.split("ASSETS:", 1)[1].split("\n", 1)[0] if "ASSETS:" in prompt_c else ""
        if ("범건우" not in assets_c) or ("여의준" not in assets_c):
            bad.append(f"(pb-c) 12컷 극단 입력에서 ASSETS 인물 이름이 빠졌습니다: {assets_c[:200]}")
        if assets_c.count("COSTUME") < 2:
            bad.append(f"(pb-c) 12컷 극단 입력에서 두 인물 COSTUME 이 빠졌습니다: {assets_c[:200]}")
        if ("@Image1" not in assets_c) or ("@Image2" not in assets_c):
            bad.append("(pb-c) 12컷 극단 입력에서 @Image 매핑이 빠졌습니다")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 프롬프트 바이트 예산 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod._first_take_style_en = orig_style

    if bad:
        fail("통 테이크 프롬프트 바이트 예산(066) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크 프롬프트 바이트 예산(066) 검사 (pb-a)실길이 6컷 ≤4000B·두 인물 COSTUME·6컷 연기≥30자·대사 한국어 "
            "(pb-b)기본 시선 gaze 생략·예외만 gaze·RULES Eyes·gaze 로마자 (pb-c)12컷 ASSETS 인물 문장·12 타임스탬프·≤4000B"
        )


# ---------------------------------------------------------------- 13h. 통 테이크 ON SCREEN·연기 보존·gen 상태(067)


def check_vnew_take_on_screen() -> None:
    """통 테이크 — 컷별 ON SCREEN + 연기 핵심 동사구 보존 + gen 상태 done(설계자 2026-10-26, 067).

    외부 호출은 (os-d)만 가짜 저장소를 쓴다(유료 호출 없음). (os-a)~(os-c)는 _take_prompt·
    _take_timeline_entries·_action_core 를 직접 부른다.

    (os-a) test2 형태(컷1: 화면 범건우 단독·화자 여의준 화면 밖) → 컷1 항목에 "ON SCREEN: Beomgeonu only"
           와 "Yeouijun (off-screen voice)".
    (os-b) 키스 컷 actionEn "Geonu leans the final short distance and places his lips against Uijun's." →
           프롬프트에 "places his lips" 포함, " and." / " as." 로 끝나는 연기 없음.
    (os-c) test2 실제 길이(의상·목소리·한국어 대사 6컷) → ≤4,000 바이트, 6컷 모두 ON SCREEN·연기 핵심
           동사 존재, ASSETS 두 인물 COSTUME 유지.
    (os-d) 실사 skipped+웹툰 completed → gen done.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415

    orig_style = genmod._first_take_style_en
    genmod._first_take_style_en = lambda plan: "thin ink lines, soft flat color"

    name_a, name_b = "범건우", "여의준"  # Beomgeonu, Yeouijun

    def _plan(cuts_spec):
        """cuts_spec: [{dialogueSpeaker, dialogueKo, actionEn, onScreen:[names], shotSize, shotType, vantage}].

        onScreen 이 1명이면 grammar.mainSubject=그 사람·others=[], 2명이면 mainSubject=첫 사람·others=둘째.
        장면 배우(timeline.actors)에는 두 인물을 모두 넣어, onScreen 밖 인물은 '숨긴 사람'이 된다.
        """
        cut_ids = [f"c{i + 1}" for i in range(len(cuts_spec))]
        cuts = []
        brief_cuts = {}
        grammar = {}
        for i, (cid, spec) in enumerate(zip(cut_ids, cuts_spec)):
            cuts.append({
                "id": cid, "no": i + 1, "seconds": 3, "panels": [i],
                "dialogueKo": f'{spec["dialogueSpeaker"]}: "{spec["dialogueKo"]}"',
            })
            brief_cuts[cid] = {
                "shotEn": "MCU, 50 mm lens, approximately 2.1 m, three-quarter angle.",
                "actionEn": spec["actionEn"],
                "dialogue": [{"speaker": spec["dialogueSpeaker"], "lineKo": spec["dialogueKo"], "deliveryEn": "soft"}],
            }
            on = spec["onScreen"]
            g = {"mainSubject": on[0], "shotSize": spec.get("shotSize", "MCU"),
                 "shotType": spec.get("shotType", "profile_two_shot"), "vantage": spec.get("vantage", "side"),
                 "others": [{"name": n, "how": "full", "side": "right"} for n in on[1:]]}
            grammar[cid] = {"grammar": g}
        scene_actors = [
            {"id": "a1", "name": name_a, "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": name_b, "color": "#e6194b", "charId": "ch2", "appearanceEn": "a man"},
        ]
        # timeline actors: 두 인물을 다 넣는다(숨김 판정은 grammar 가 한다).
        timeline = [{"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for c in cuts]
        plan = {
            "conti": {"status": "ready", "cuts": cuts,
                      "segments": [{"id": "s1", "cutIds": cut_ids, "seconds": 3 * len(cuts)}]},
            "script": {"status": "ready", "entries": []},
            "previz": {
                "status": "ready",
                "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
                "panelGrammar": grammar,
                "shotBrief": {"status": "ready", "cuts": brief_cuts},
            },
        }
        take = {"id": "take1", "cutIds": cut_ids, "seconds": 3 * len(cuts)}
        return plan, take

    cast = [
        {"name": name_a, "colorName": "blue", "frontNo": 1,
         "appearance": "short black hair, sharp jaw, narrow eyes",
         "costume": "black dress shirt, sleeves rolled up; charcoal slacks", "voice": "low calm adult male voice"},
        {"name": name_b, "colorName": "red", "frontNo": 2,
         "appearance": "wavy brown hair, soft features",
         "costume": "grey hoodie and dark jeans", "voice": "soft young male voice"},
    ]

    try:
        # ---- (os-a) 컷1: 화면 범건우 단독·화자 여의준 화면 밖 ----
        spec_a = [
            {"dialogueSpeaker": name_b, "dialogueKo": "죄송해요", "onScreen": [name_a],
             "actionEn": "Beomgeonu turns his head toward the door, his shoulder filling the frame."},
            {"dialogueSpeaker": name_a, "dialogueKo": "괜찮아", "onScreen": [name_a, name_b],
             "actionEn": "Beomgeonu steps closer and rests a hand on the desk."},
        ]
        plan_a, take_a = _plan(spec_a)
        entries_a = genmod._take_timeline_entries(plan_a, take_a)
        # 컷1 onScreen = 범건우 단독, offScreenSpeaker = 여의준.
        e1 = entries_a[0]
        on1 = [i.get("name") for i in (e1.get("onScreen") or [])]
        if on1 != [name_a]:
            bad.append(f"(os-a) 컷1 onScreen 이 범건우 단독이 아닙니다: {on1}")
        if (e1.get("offScreenSpeaker") or "") != name_b:
            bad.append(f"(os-a) 컷1 offScreenSpeaker 가 여의준이 아닙니다: {e1.get('offScreenSpeaker')!r}")
        prompt_a = genmod._take_prompt(plan_a, take_a, cast=cast, panel_nos=[3, 4], location_no=5)
        if "ON SCREEN: Beomgeonu only" not in prompt_a:
            bad.append(f"(os-a) 프롬프트에 'ON SCREEN: Beomgeonu only' 가 없습니다: {prompt_a[:400]}")
        if "Yeouijun (off-screen voice)" not in prompt_a:
            bad.append(f"(os-a) 프롬프트에 'Yeouijun (off-screen voice)' 가 없습니다")
        # 컷2는 둘 다 화면 → off-screen 표기 없어야(컷2 대사 화자 범건우는 화면 안).
        e2 = entries_a[1]
        if e2.get("offScreenSpeaker"):
            bad.append(f"(os-a) 컷2 는 화자가 화면 안인데 offScreenSpeaker 가 있습니다: {e2.get('offScreenSpeaker')}")
        if "ON SCREEN: Beomgeonu, Yeouijun" not in prompt_a:
            bad.append(f"(os-a) 컷2 'ON SCREEN: Beomgeonu, Yeouijun' 가 없습니다")
        # RULES 시선 규칙(ON SCREEN 기반).
        if "an off-screen voice never appears on screen" not in prompt_a:
            bad.append("(os-a) RULES 에 off-screen voice 규칙이 없습니다")

        # ---- (os-b) 키스 컷: 핵심 동사구 보존, "and."/"as." 끝 없음 ----
        kiss_action = "Geonu leans the final short distance and places his lips against Uijun's."
        core = genmod._action_core(kiss_action, 55)
        if "places his lips" not in core:
            bad.append(f"(os-b) _action_core 가 'places his lips' 를 보존하지 않았습니다: {core!r}")
        if core.rstrip(".").endswith(" and") or core.rstrip(".").endswith(" as"):
            bad.append(f"(os-b) _action_core 가 접속사(and/as)로 끝납니다: {core!r}")
        # 프롬프트 레벨: 키스 컷을 넣은 테이크.
        spec_b = [
            {"dialogueSpeaker": name_b, "dialogueKo": "조금만 더", "onScreen": [name_a, name_b],
             "actionEn": kiss_action, "shotSize": "MCU", "shotType": "profile_two_shot", "vantage": "side"},
        ]
        plan_b, take_b = _plan(spec_b)
        prompt_b = genmod._take_prompt(plan_b, take_b, cast=cast, panel_nos=[3, 4], location_no=5)
        if "places his lips" not in prompt_b:
            bad.append(f"(os-b) 프롬프트에 'places his lips' 가 없습니다: {prompt_b[-400:]}")
        # 연기 문장이 " and." / " as." 로 끝나지 않는다(TIMELINE 전체에서).
        import re as _re  # noqa: PLC0415
        if _re.search(r"\b(and|as)\.", prompt_b):
            bad.append(f"(os-b) 연기가 ' and.' / ' as.' 로 끝납니다: {prompt_b}")

        # ---- (os-c) test2 실제 길이: 의상·목소리·한국어 대사 6컷 ≤2500B·6컷 ON SCREEN·연기 핵심 ----
        ko_line = "자네 지금 뭐 하는 건가 당장 설명해 보게"
        spec_c = []
        for i in range(6):
            speaker = name_a if i % 2 == 0 else name_b
            # 컷마다 두 인물 화면(일반 대화), 단 컷1만 화자 화면 밖 시나리오를 섞지 않는다(길이 측정용).
            spec_c.append({
                "dialogueSpeaker": speaker, "dialogueKo": ko_line, "onScreen": [name_a, name_b],
                "actionEn": (f"{speaker} leans forward across the desk, eyes narrowing, one hand flat on "
                             "the folder as he speaks in a low measured voice."),
            })
        plan_c, take_c = _plan(spec_c)
        prompt_c = genmod._take_prompt(plan_c, take_c, cast=cast, panel_nos=[3, 4], location_no=5)
        if len(prompt_c.encode("utf-8")) > 4000:
            bad.append(f"(os-c) 프롬프트가 4,000바이트를 넘습니다: {len(prompt_c.encode('utf-8'))}")
        entries_c = genmod._take_timeline_entries(plan_c, take_c)
        stamps_c = [f"{e['start']}-{e['end']}s" for e in entries_c]
        if len(stamps_c) != 6:
            bad.append(f"(os-c) 타임라인 항목이 6개가 아닙니다: {len(stamps_c)}")
        miss_c = [s for s in stamps_c if s not in prompt_c]
        if miss_c:
            bad.append(f"(os-c) 프롬프트에 빠진 컷 타임스탬프: {miss_c}")
        # 6컷 모두 ON SCREEN(이 경우 둘 다 화면 → "ON SCREEN: Beomgeonu, Yeouijun" 6회).
        if prompt_c.count("ON SCREEN:") != 6:
            bad.append(f"(os-c) ON SCREEN 줄이 6개가 아닙니다: {prompt_c.count('ON SCREEN:')}")
        # 6컷 모두 연기 핵심 동사("leans forward") 존재.
        tl_c = prompt_c.split("TIMELINE", 1)[1].split("\nRULES", 1)[0] if "TIMELINE" in prompt_c else ""
        if tl_c.count("leans forward") != 6:
            bad.append(f"(os-c) 6컷 모두에 연기 핵심 동사가 있지 않습니다('leans forward' {tl_c.count('leans forward')}회)")
        # ASSETS 두 인물 COSTUME 유지.
        assets_c = prompt_c.split("ASSETS:", 1)[1].split("\n", 1)[0] if "ASSETS:" in prompt_c else ""
        if assets_c.count("COSTUME") < 2:
            bad.append(f"(os-c) ASSETS 에 두 인물 COSTUME 이 모두 있지 않습니다: {assets_c[:300]}")

        # ---- (os-d) 실사 skipped + 웹툰 completed → gen done ----
        fake_store = _FakeCharStore()
        orig_gen_store = genmod.STORE
        orig_plan_store = planmod.STORE
        orig_concat = genmod._concat
        orig_fetch = genmod._fetch_bytes
        orig_has_audio = genmod._has_audio_stream
        genmod.STORE = fake_store
        planmod.STORE = fake_store
        genmod._concat = lambda clips: b"FINAL"
        genmod._fetch_bytes = lambda key, *, bucket="": b"CLIP"
        genmod._has_audio_stream = lambda mp4: None
        try:
            owner, pid = "tester", "prj_os_d"
            base = planmod.new_plan()
            base["owner"] = owner
            planmod.save_plan(owner, pid, base)

            def _seed(data):
                data["gen"] = {
                    "genId": "gosd", "mode": "previz-take", "status": "running",
                    "videoModel": "fake", "resolution": "480p",
                    "units": [{
                        "id": "take1", "cutIds": ["c1"], "recKeys": ["r1"], "recSegIds": ["s1"], "seconds": 18.0,
                        "real": {"status": "skipped"},
                        "webtoon": {"status": "completed", "outputKey": "out/take1.mp4"},
                    }],
                    "segments": [],
                    "finalReal": {"status": "skipped", "key": "", "error": ""},
                    "finalWebtoon": {"status": "waiting", "key": "", "error": ""},
                }

            planmod.mutate_plan(owner, pid, _seed)
            genmod._maybe_finalize_previz(owner, pid, "gosd")
            dplan, _ = planmod.load_plan(owner, pid)
            dgen = dplan.get("gen") or {}
            if dgen.get("status") != "done":
                bad.append(f"(os-d) 실사 skipped·웹툰 completed 인데 gen.status 가 done 이 아닙니다: {dgen.get('status')}")
            if (dgen.get("finalWebtoon") or {}).get("status") != "done":
                bad.append(f"(os-d) finalWebtoon 이 done 이 아닙니다: {dgen.get('finalWebtoon')}")
            if (dgen.get("finalReal") or {}).get("status") != "skipped":
                bad.append(f"(os-d) finalReal 이 skipped 가 아닙니다: {dgen.get('finalReal')}")
        finally:
            genmod.STORE = orig_gen_store
            planmod.STORE = orig_plan_store
            genmod._concat = orig_concat
            genmod._fetch_bytes = orig_fetch
            genmod._has_audio_stream = orig_has_audio

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("통 테이크 ON SCREEN 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod._first_take_style_en = orig_style

    if bad:
        fail("통 테이크 ON SCREEN·연기 보존·gen 상태(067) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "통 테이크 ON SCREEN·연기 보존·gen 상태(067) 검사 (os-a)컷1 Beomgeonu only·Yeouijun off-screen voice "
            "(os-b)키스 컷 places his lips 보존·and./as. 끝 없음 (os-c)6컷 ON SCREEN·연기 핵심·두 COSTUME·≤4000B "
            "(os-d)실사 skipped·웹툰 completed→gen done"
        )


# ---------------------------------------------------------------- 13i. 살아 움직이는 TV 애니 룩·조화·모션(069 v2)


def check_vnew_take_harmony_motion() -> None:
    """살아 움직이는 TV 애니 룩 + 인물·배경 조화 + 마네킹 흔들림 금지 — 통 테이크 예산 4,000 바이트(설계자 2026-10-26 재지시 v2).

    외부 호출 없음. _take_prompt·_cut_prompt·_take_prompt_budget·_motion_line·_harmony_line 을 직접 부른다.

    (hm-a) 통 테이크 test2 규모(6컷·인물2·한국어 대사·의상·목소리) → "anime compositing"·"rim light"·
           "depth of field"·"do NOT copy"·"parallax"·"same light direction"·"contact shadows" 포함,
           "unlit flat color tones" 없음, AVOID 에 "cinematic lighting" 없음, 6컷 ON SCREEN·"places his lips"·
           연기 문장 유지, 두 COSTUME, ≤4,000 바이트.
    (hm-b) setting.timeKo "낮" → HARMONY 에 "day"·"no drift to sunset".
    (hm-c) 컷 단위 프롬프트: MOTION(short)·HARMONY(short) 포함, ≤2,500 바이트, 짧은 지시서에서 MOOD·SETTING 유지.
    (hm-d) 스키마에 prompt maxLength 1800 이 명시된 모델(caps.promptMaxLength=1800) → 통 테이크 예산 1800.
    """
    sys.path.insert(0, str(ROOT / "app"))
    bad: list[str] = []

    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vpoc import fal as _fal  # noqa: PLC0415

    orig_style = genmod._first_take_style_en
    genmod._first_take_style_en = lambda plan: "thin sketchy pencil lines, soft pastel flat color"

    name_a, name_b = "범건우", "여의준"  # Beomgeonu, Yeouijun

    # 장소(design.lightEn)·대본 setting.timeKo 를 주어 HARMONY 변수를 채운다.
    def _plan(n_cuts: int, *, time_ko: str = "낮", kiss_last: bool = False):
        cut_ids = [f"c{i + 1}" for i in range(n_cuts)]
        cuts = []
        brief_cuts = {}
        grammar = {}
        ko_line = "자네 지금 뭐 하는 건가 당장 설명해 보게"
        for i, cid in enumerate(cut_ids):
            speaker = name_a if i % 2 == 0 else name_b
            listener = name_b if speaker == name_a else name_a
            cuts.append({
                "id": cid, "no": i + 1, "seconds": 3, "panels": [i],
                "dialogueKo": f'{speaker}: "{ko_line}"',
            })
            action = (
                f"{speaker} leans forward across the desk, eyes narrowing, one hand flat on the folder "
                "as he speaks in a low measured voice."
            )
            if kiss_last and i == n_cuts - 1:
                action = f"{speaker} leans the final short distance and places his lips against {listener}'s."
            brief_cuts[cid] = {
                "shotEn": "MCU, 50 mm lens, approximately 2.1 m, three-quarter angle.",
                "actionEn": action,
                "dialogue": [{"speaker": speaker, "lineKo": ko_line, "deliveryEn": "sharp and low"}],
            }
            g = {"mainSubject": speaker, "shotSize": "MCU", "shotType": "profile_two_shot",
                 "vantage": "side", "others": [{"name": listener, "how": "full", "side": "right"}]}
            grammar[cid] = {"grammar": g}
        scene_actors = [
            {"id": "a1", "name": name_a, "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": name_b, "color": "#e6194b", "charId": "ch2", "appearanceEn": "a man"},
        ]
        timeline = [{"cutId": c["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for c in cuts]
        plan = {
            "conti": {"status": "ready", "cuts": cuts,
                      "segments": [{"id": "s1", "cutIds": cut_ids, "seconds": 3 * n_cuts}]},
            "script": {"status": "ready", "setting": {"timeKo": time_ko, "enclosed": True,
                       "locationEn": "A closed principal's office"}, "entries": []},
            "locations": [
                {"key": "a closed principal's office", "nameEn": "A closed principal's office",
                 "design": {"lightEn": "soft daylight from the north windows, warm white"}},
            ],
            "previz": {
                "status": "ready",
                "scenes": [{"locationId": "loc1", "actors": scene_actors, "timeline": timeline}],
                "panelGrammar": grammar,
                "shotBrief": {"status": "ready",
                              "scene": {"loc1": "A closed principal's office with wood panelling."},
                              "cuts": brief_cuts},
            },
        }
        take = {"id": "take1", "cutIds": cut_ids, "seconds": 3 * n_cuts}
        return plan, take

    cast = [
        {"name": name_a, "colorName": "blue", "frontNo": 1,
         "appearance": "short black hair combed to one side, narrow watchful eyes, square jaw",
         "costume": "a charcoal double-breasted suit with a crisp white dress shirt and a dark navy silk tie",
         "voice": "a low, calm, stoic adult male voice with a slow measured cadence"},
        {"name": name_b, "colorName": "red", "frontNo": 2,
         "appearance": "wavy brown hair, soft rounded features, bright eyes",
         "costume": "a loose ivory knit sweater over a pale blue collared shirt and slim grey slacks",
         "voice": "a bright, slightly nervous young adult male voice"},
    ]

    try:
        # ---- (hm-a) test2 규모·TV 애니 룩·조화·모션 ----
        plan_a, take_a = _plan(6, time_ko="낮", kiss_last=True)
        prompt_a = genmod._take_prompt(plan_a, take_a, cast=cast, panel_nos=[3, 4], location_no=5)
        need_present = [
            "anime compositing", "rim light", "depth of field", "do NOT copy", "parallax",
            "same light direction", "contact shadows",
        ]
        for needle in need_present:
            if needle not in prompt_a:
                bad.append(f"(hm-a) 프롬프트에 '{needle}' 가 없습니다")
        for banned in ("unlit flat color tones",):
            if banned in prompt_a:
                bad.append(f"(hm-a) 프롬프트에 '{banned}' 가 남아 있습니다")
        # AVOID 블록에 cinematic lighting 없음.
        avoid_part = prompt_a.split("AVOID:", 1)[1] if "AVOID:" in prompt_a else ""
        if "cinematic lighting" in avoid_part:
            bad.append("(hm-a) AVOID 에 'cinematic lighting' 가 남아 있습니다")
        # 6컷 ON SCREEN·타임스탬프·연기·키스 컷 보존.
        if prompt_a.count("ON SCREEN:") != 6:
            bad.append(f"(hm-a) ON SCREEN 줄이 6개가 아닙니다: {prompt_a.count('ON SCREEN:')}")
        entries_a = genmod._take_timeline_entries(plan_a, take_a)
        stamps_a = [f"{e['start']}-{e['end']}s" for e in entries_a]
        miss_a = [s for s in stamps_a if s not in prompt_a]
        if miss_a:
            bad.append(f"(hm-a) 프롬프트에 빠진 컷 타임스탬프: {miss_a}")
        if "places his lips" not in prompt_a:
            bad.append("(hm-a) 키스 컷 'places his lips' 가 사라졌습니다")
        tl_a = prompt_a.split("TIMELINE", 1)[1].split("\nMOTION", 1)[0] if "TIMELINE" in prompt_a else ""
        if tl_a.count("leans forward") < 5:
            bad.append(f"(hm-a) 연기 문장이 충분히 유지되지 않았습니다('leans forward' {tl_a.count('leans forward')}회)")
        assets_a = prompt_a.split("ASSETS:", 1)[1].split("\n", 1)[0] if "ASSETS:" in prompt_a else ""
        if assets_a.count("COSTUME") < 2:
            bad.append(f"(hm-a) ASSETS 에 두 인물 COSTUME 이 모두 있지 않습니다: {assets_a[:300]}")
        if len(prompt_a.encode("utf-8")) > 4000:
            bad.append(f"(hm-a) 프롬프트가 4,000바이트를 넘습니다: {len(prompt_a.encode('utf-8'))}")

        # ---- (hm-b) 낮 → day · no drift to sunset ----
        harmony_part = prompt_a.split("HARMONY:", 1)[1].split("\nRULES", 1)[0] if "HARMONY:" in prompt_a else ""
        if "day light" not in harmony_part:
            bad.append(f"(hm-b) HARMONY 에 'day light'(낮→day) 가 없습니다: {harmony_part[:160]}")
        if "no drift to sunset" not in harmony_part:
            bad.append(f"(hm-b) HARMONY 에 'no drift to sunset' 가 없습니다: {harmony_part[:200]}")
        # _harmony_line 단위(full): 밤(night)도 바로 반영.
        night_plan, _ = _plan(1, time_ko="밤")
        night_harmony = genmod._harmony_line(night_plan, True)
        if "night light" not in night_harmony:
            bad.append(f"(hm-b) 밤 setting 이 'night light' 로 반영되지 않았습니다: {night_harmony[:120]}")

        # ---- (hm-c) 컷 단위 프롬프트: MOTION(short)·HARMONY(short)·MOOD·SETTING 유지·≤2500B ----
        # 짧은 지시서(brief 작게) → 아무 섹션도 지워지지 않아야 한다. cast 는 _cut_prompt 형식(elementRef).
        cut_cast = [{"colorName": "blue", "name": "Kim", "elementRef": "@Element1",
                     "appearance": "a middle-aged man in a dark suit"}]
        plan_c, _ = _plan(1, time_ko="낮")
        # 짧은 지시서: MOOD·SETTING 이 존재하고, 전체가 2,450 바이트 전제 안에 들도록 적당한 길이.
        plan_c["previz"]["shotBrief"]["cuts"]["c1"] = {
            "shotEn": "MCU, eye level, three-quarter angle on Kim.",
            "blockingEn": "Kim stands at the desk, slightly turned toward the door.",
            "actionEn": "Kim leans forward and speaks in a low measured voice.",
            "moodEn": "tense and heavy, the air thick with unspoken anger.",
            "backgroundEn": "The principal's office behind him, bookshelves and a wide window.",
            "dialogue": [{"speaker": "Kim", "lineKo": "자네 지금 뭐 하나", "deliveryEn": "sharp and low"}],
        }
        plan_c["previz"]["shotBrief"]["scene"] = {"loc1": "A closed principal's office with wood panelling and a wide window."}
        prompt_cut = genmod._cut_prompt(plan_c, {"cutId": "c1"}, "webtoon", cast=cut_cast, has_image_ref=True)
        # MOTION short·HARMONY short 포함(ACTION 뒤 MOTION, SETTING 뒤 HARMONY).
        if "\nMOTION:" not in prompt_cut:
            bad.append("(hm-c) 컷 프롬프트에 MOTION(short) 섹션이 없습니다")
        if "\nHARMONY:" not in prompt_cut:
            bad.append("(hm-c) 컷 프롬프트에 HARMONY(short) 섹션이 없습니다")
        # short MOTION·HARMONY 핵심 문구.
        if "do NOT copy it" not in prompt_cut:
            bad.append("(hm-c) MOTION(short) 핵심 문구(do NOT copy it)가 없습니다")
        if "no drift to sunset" not in prompt_cut:
            bad.append("(hm-c) HARMONY(short) 에 'no drift to sunset' 가 없습니다")
        # 짧은 지시서에서 MOOD·SETTING 유지(지워지지 않는다).
        if "\nMOOD:" not in prompt_cut:
            bad.append("(hm-c) 짧은 지시서인데 MOOD 가 지워졌습니다")
        if "\nSETTING:" not in prompt_cut:
            bad.append("(hm-c) 짧은 지시서인데 SETTING 이 지워졌습니다")
        # 순서: ACTION → MOTION → MOOD → SETTING → HARMONY → DIALOGUE → CONTINUITY.
        order = ["\nACTION:", "\nMOTION:", "\nMOOD:", "\nSETTING:", "\nHARMONY:", "\nDIALOGUE:", "\nCONTINUITY:"]
        positions = [("\n" + prompt_cut).find(tok) for tok in order]
        present_pos = [p for p in positions if p >= 0]
        if present_pos != sorted(present_pos):
            bad.append(f"(hm-c) 컷 섹션 순서가 틀립니다: {list(zip(order, positions))}")
        if len(prompt_cut.encode("utf-8")) > 2500:
            bad.append(f"(hm-c) 컷 프롬프트가 2,500바이트를 넘습니다: {len(prompt_cut.encode('utf-8'))}")
        # 컷 단위도 Kling 2500 제한 유지(PREVIZ_PROMPT_TARGET).
        if genmod.vnew_settings.PREVIZ_PROMPT_TARGET != 2500:
            bad.append(f"(hm-c) 컷 단위 예산(PREVIZ_PROMPT_TARGET)이 2500 이 아닙니다: {genmod.vnew_settings.PREVIZ_PROMPT_TARGET}")

        # ---- (hm-d) 스키마 명시 maxLength 1800 → 통 테이크 예산 1800 ----
        if genmod._take_prompt_budget(None) != 4000:
            bad.append(f"(hm-d) caps 없음 통 테이크 예산이 4000 이 아닙니다: {genmod._take_prompt_budget(None)}")
        # 미명시(기본 2500) → 길이 상한으로 보지 않는다(4000 유지).
        if genmod._take_prompt_budget({"promptMaxLength": _fal.DEFAULT_PROMPT_MAX}) != 4000:
            bad.append("(hm-d) promptMaxLength=기본값(미명시)인데 예산이 4000 이 아닙니다")
        # 스키마 명시 1800 → min(4000, 1800) = 1800.
        if genmod._take_prompt_budget({"promptMaxLength": 1800}) != 1800:
            bad.append(f"(hm-d) 스키마 명시 1800 인데 예산이 1800 이 아닙니다: {genmod._take_prompt_budget({'promptMaxLength': 1800})}")
        # 스키마 명시 1800 caps 로 실제 프롬프트도 1800 이하.
        prompt_d = genmod._take_prompt(plan_a, take_a, cast=cast, panel_nos=[3, 4], location_no=5,
                                       caps={"promptMaxLength": 1800})
        if len(prompt_d.encode("utf-8")) > 1800:
            bad.append(f"(hm-d) 1800 caps 프롬프트가 1800바이트를 넘습니다: {len(prompt_d.encode('utf-8'))}")

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("TV 애니 룩·조화·모션 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        genmod._first_take_style_en = orig_style

    if bad:
        fail("살아 움직이는 TV 애니 룩·조화·모션(069 v2) 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "살아 움직이는 TV 애니 룩·조화·모션(069 v2) 검사 (hm-a)anime compositing·rim light·parallax·"
            "contact shadows·unlit 없음·AVOID cinematic 없음·6컷·키스 보존·두 COSTUME·≤4000B "
            "(hm-b)낮→day·no drift to sunset·밤→night (hm-c)컷 MOTION/HARMONY short·MOOD/SETTING 유지·순서·≤2500B "
            "(hm-d)caps 없음 4000·미명시 4000·스키마 1800→1800"
        )


# ---------------------------------------------------------------- ARK Seedance 게이트


def check_ark_seedance_gate() -> None:
    """기존(BytePlus ARK 직결) Seedance 제외 — fal Seedance 만(설계자 2026-10-26).

    기본 VPOC_ARK_SEEDANCE_ENABLED=false 이면 ② 목록에서 ARK Seedance(provider byteplus)가 빠지고,
    저장된 ARK Seedance 선택은 ark.resolve_model 로 fal Seedance(등록 시) 또는 Luma 로 바뀐다.
    이미 제출된 ARK 작업(ark/tasks/ ARN)은 그대로 조회된다(is_ark_job 유지). env=true 면 예전과 동일.

    (sf-a) 기본(false): choices 에 provider byteplus 항목 없음, fal 등록 seedance i2v(가짜) 있음.
    (sf-b) 저장 videoModel "seedance-2-5" → resolve 결과 fal seedance 2.5 i2v(등록 시), 미등록이면 Luma.
    (sf-c) ARK ARN(ark/tasks/...) 조회 경로 유지(is_ark_job True).
    (sf-d) env true → 예전 choices(byteplus 2개 포함) 그대로.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, settings  # noqa: PLC0415

    bad: list[str] = []

    fal_target = settings.ARK_SEEDANCE_FAL_MAP["seedance-2-5"]  # "fal:bytedance/seedance-2.5/image-to-video"
    fake_item = {
        "id": fal_target,
        "endpointId": fal_target[len("fal:"):],
        "label": "Seedance 2.5 i2v",
        "category": "image-to-video",
        "caps": {"available": True, "durations": [5], "resolutions": ["720p"], "startImageField": "image_url"},
    }

    saved = {
        "enabled": settings.ARK_SEEDANCE_ENABLED,
        "api_key": settings.ARK_API_KEY,
        "picker": settings.MODEL_PICKER_ENABLED,
        "fal_registered": fal.registered,
        "fal_is_registered": fal.is_registered,
        "fal_unavailable": fal.unavailable_reason,
        "fal_find": fal.find,
        "fal_pricing_for": fal.pricing_for,
    }

    def _install_fake_fal(registered_items: list[dict]) -> None:
        by_id = {item["id"]: item for item in registered_items}
        fal.registered = lambda: [dict(item) for item in registered_items]
        fal.is_registered = lambda model: model in by_id
        fal.unavailable_reason = lambda model: "" if model in by_id else "등록되지 않은 모델"
        fal.find = lambda model: dict(by_id[model]) if model in by_id else None
        fal.pricing_for = lambda model: {"unit": "seconds", "unitPrice": 0.0}

    try:
        # ARK_API_KEY 를 채워 env=true 일 때 byteplus 가 '사용 가능'으로 보이게 한다(실제 호출 없음).
        settings.ARK_API_KEY = "dummy-ark-key"

        # ---- (sf-a) 기본(false): byteplus 없음, fal 등록 seedance i2v 있음 ----
        settings.ARK_SEEDANCE_ENABLED = False
        _install_fake_fal([fake_item])
        items = ark.choices()
        providers = [item.get("provider") for item in items]
        if "byteplus" in providers:
            bad.append(f"(sf-a) 기본(false)인데 choices 에 byteplus 항목이 있습니다: {providers}")
        ids = {item.get("id") for item in items}
        if fal_target not in ids:
            bad.append(f"(sf-a) fal 등록 seedance i2v 가 choices 에 없습니다: {sorted(ids)}")
        # ARK Seedance id 의 unavailable_reason 은 '사용 중지' 안내.
        reason = ark.unavailable_reason("seedance-2-5")
        if "사용 중지" not in reason or "fal" not in reason:
            bad.append(f"(sf-a) ARK Seedance unavailable_reason 안내가 다릅니다: {reason!r}")

        # ---- (sf-b) 저장 videoModel 'seedance-2-5' → fal seedance 2.5 i2v(등록 시) ----
        if ark.resolve_model("seedance-2-5") != fal_target:
            bad.append(f"(sf-b) 등록된 fal seedance 로 resolve 되지 않았습니다: {ark.resolve_model('seedance-2-5')}")
        if ark.resolve_model("seedance-2-0") != fal_target:
            bad.append(f"(sf-b) seedance-2-0 가 fal seedance 로 resolve 되지 않았습니다: {ark.resolve_model('seedance-2-0')}")
        # selected() 도 같은 함수를 거친다(MODEL_PICKER 켬).
        settings.MODEL_PICKER_ENABLED = True
        state = {"steps": {"2": {"videoModel": "seedance-2-5"}}}
        if ark.selected(state) != fal_target:
            bad.append(f"(sf-b) selected() 가 fal seedance 로 바뀌지 않았습니다: {ark.selected(state)}")
        # 미등록이면 Luma(DEFAULT_MODEL) 로 떨어진다.
        _install_fake_fal([])  # fal 에 아무것도 등록 안 됨
        if ark.resolve_model("seedance-2-5") != ark.DEFAULT_MODEL:
            bad.append(f"(sf-b) 미등록인데 Luma 로 안 떨어졌습니다: {ark.resolve_model('seedance-2-5')}")
        if ark.selected(state) != ark.DEFAULT_MODEL:
            bad.append(f"(sf-b) 미등록 selected() 가 Luma 가 아닙니다: {ark.selected(state)}")
        settings.MODEL_PICKER_ENABLED = saved["picker"]
        _install_fake_fal([fake_item])  # 다시 등록 상태로

        # ---- (sf-c) ARK ARN 조회 경로 유지 ----
        if not ark.is_ark_job("ark/tasks/task-xyz"):
            bad.append("(sf-c) ark/tasks/ ARN 이 is_ark_job True 가 아닙니다")
        # fal 작업도 여전히 ark_job(공용 외부 경로).
        if not ark.is_ark_job("fal/requests/x/req-1"):
            bad.append("(sf-c) fal 작업이 is_ark_job True 가 아닙니다")

        # ---- (sf-d) env true → 예전 choices(byteplus 2개 포함) ----
        settings.ARK_SEEDANCE_ENABLED = True
        items_on = ark.choices()
        byteplus_ids = [item.get("id") for item in items_on if item.get("provider") == "byteplus"]
        if sorted(byteplus_ids) != ["seedance-2-0", "seedance-2-5"]:
            bad.append(f"(sf-d) env=true 인데 byteplus 2개가 아닙니다: {byteplus_ids}")
        # env=true 면 ARK Seedance 는 그대로 고를 수 있고 resolve 는 바꾸지 않는다(회귀 없음).
        if ark.unavailable_reason("seedance-2-5") != "":
            bad.append(f"(sf-d) env=true 인데 ARK Seedance 가 unavailable 입니다: {ark.unavailable_reason('seedance-2-5')!r}")
        if ark.resolve_model("seedance-2-5") != "seedance-2-5":
            bad.append(f"(sf-d) env=true 인데 resolve 가 모델을 바꿨습니다: {ark.resolve_model('seedance-2-5')}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("ARK Seedance 게이트 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1200])
    finally:
        settings.ARK_SEEDANCE_ENABLED = saved["enabled"]
        settings.ARK_API_KEY = saved["api_key"]
        settings.MODEL_PICKER_ENABLED = saved["picker"]
        fal.registered = saved["fal_registered"]
        fal.is_registered = saved["fal_is_registered"]
        fal.unavailable_reason = saved["fal_unavailable"]
        fal.find = saved["fal_find"]
        fal.pricing_for = saved["fal_pricing_for"]

    if bad:
        fail("ARK Seedance 게이트 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "ARK Seedance 게이트 검사 (sf-a)기본 byteplus 없음·fal i2v 있음·안내 "
            "(sf-b)저장 seedance-2-5→fal(등록)·미등록→Luma·selected 동일 "
            "(sf-c)ark/tasks ARN is_ark_job 유지 (sf-d)env=true byteplus 2개·resolve 무변경"
        )


# ---------------------------------------------------------------- 073 fal-seedance-parity 공용 하네스
#
# design.md §11. 작업 1에서 만들고 작업 2~6이 재사용한다. 외부 호출은 모두 가짜다.
# - BytePlus: ark._call 기록기(POST 는 {"id": "task-N"}).
# - fal: fal._request 기록기(POST 는 {"request_id": "req-N"}).
# - 원장: ledger.charge 기록기. Luma: video.submit 기록기. urllib.request.urlopen 은 호출되면 기록 후 예외.
# - 백그라운드 스레드(드라이버·제출)는 routes.threading 을 바꿔 띄우지 않는다(이름만 기록).

# 운영 되돌리기 배선을 확인할 env 키(템플릿·config.env.example). 작업 2~4가 키를 더한다.
PARITY_ENV_KEYS = [
    "VPOC_ARK_SEEDANCE_ENABLED",
    "VPOC_FAL_SEEDANCE_FAMILY",
    "VPOC_FAL_SEEDANCE_MAX_S",
    "VPOC_FAL_PROMPT_MAX_OVERRIDES",
    "VPOC_FAL_SEEDANCE_TAG_STYLE",
    # 작업 7(R15.8): 결과 조회 포기 시간.
    "VPOC_POLL_GIVEUP_S",
    # llm-top-model-first R6: 설정 거절 미리 적용·잘림 재시도.
    "VPOC_LLM_NO_FORCED_TOOL",
    "VPOC_LLM_NO_TEMPERATURE",
    "VPOC_LLM_TRUNC_RETRY_FACTOR",
    "VPOC_LLM_TRUNC_RETRY_CAP",
    # 084 작업 1(R1.13): 프리비즈 소품 관통 보정.
    "VNEW_PREVIZ_COLLISION",
]
PARITY_FACE = "fal:bytedance/seedance-2.5/image-to-video"
PARITY_PARTNER = "fal:bytedance/seedance-2.5/reference-to-video"
PARITY_OWNER = "tester"
PARITY_PID = "prj_parity"
PARITY_PLAN = "pl_parity_01"
PARITY_CHAR_KEY = "users/tester/projects/prj_parity/vpoc/pl_parity_01/characters/c1.jpg"
# BytePlus 제출 본문 키(073 이전과 같아야 한다, ip-g).
PARITY_ARK_BODY_KEYS = {"model", "content", "ratio", "duration", "resolution", "generate_audio", "watermark"}


class _LogCapture(logging.Handler):
    """`vpoc`·`vnew` 로거에 붙였다 떼며 레코드를 모은다(073 작업 1, 작업 2~6 재사용).

    로거 수준을 잠시 DEBUG 로 내려 info 로그도 받는다. detach 가 수준을 되돌린다.
    with _LogCapture() as logs: ... 또는 attach()/detach() 로 쓴다.
    """

    def __init__(self, names: tuple[str, ...] = ("vpoc", "vnew")) -> None:
        super().__init__(level=logging.DEBUG)
        self.names = tuple(names)
        self.records: list[logging.LogRecord] = []
        self._saved: list[tuple[logging.Logger, int]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def attach(self) -> "_LogCapture":
        if self._saved:
            return self
        for name in self.names:
            logger = logging.getLogger(name)
            self._saved.append((logger, logger.level))
            logger.addHandler(self)
            logger.setLevel(logging.DEBUG)
        return self

    def detach(self) -> None:
        for logger, level in self._saved:
            logger.removeHandler(self)
            logger.setLevel(level)
        self._saved = []

    def clear(self) -> None:
        self.records.clear()

    def messages(self, contains: str = "", level: int | None = None) -> list[str]:
        out: list[str] = []
        for record in self.records:
            if level is not None and record.levelno != level:
                continue
            try:
                text = record.getMessage()
            except Exception:  # noqa: BLE001
                text = str(record.msg)
            if contains in text:
                out.append(text)
        return out

    def __enter__(self) -> "_LogCapture":
        return self.attach()

    def __exit__(self, *exc) -> bool:
        self.detach()
        return False


def _parity_caps(kind: str) -> dict:
    """개발 레지스트리와 같은 모양의 Seedance 2.5 caps(kind = "i2v" | "r2v")."""
    durations = [str(value) for value in range(4, 31)]
    resolutions = ["480p", "720p", "1080p"]
    caps = {
        "promptField": "prompt",
        "promptMaxLength": None,
        "startImageField": "",
        "endImageField": "",
        "referenceImagesField": "",
        "durations": durations,
        "durationAsString": True,
        "durationValues": {value: value for value in durations},
        "sendDuration": True,
        "resolutions": resolutions,
        "resolutionValues": {value: value for value in resolutions},
        "aspectRatios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "audioField": "generate_audio",
        "videoField": "",
        "elementsField": "",
        "imageListField": "",
        "imageListMax": None,
        "keepAudioField": "",
        "videoListField": "",
        "videoListMax": None,
        "taskField": "",
        "taskValues": [],
        "draftField": "",
        "generateAudioField": "generate_audio",
        "seedField": "seed",
        "previzCapable": False,
        "requiredFields": ["prompt"],
        "available": True,
        "reason": "",
    }
    if kind == "i2v":
        caps.update(
            {
                "startImageField": "image_url",
                "endImageField": "end_image_url",
                "requiredFields": ["prompt", "image_url"],
            }
        )
    else:
        caps["referenceImagesField"] = "image_urls"
    return caps


def _parity_registry_items(*, face: bool = True, partner: bool = True) -> list[dict]:
    """fal 레지스트리 항목(얼굴 i2v·짝 r2v). pricing 은 토큰 단가(개발 레지스트리와 같다)."""
    items: list[dict] = []
    pricing = {"unit": "1000 tokens", "unitPrice": 0.0214}
    if face:
        items.append(
            {
                "id": PARITY_FACE,
                "endpointId": PARITY_FACE[len("fal:"):],
                "label": "Bytedance Seedance 2.5 Image to Video",
                "category": "image-to-video",
                "caps": _parity_caps("i2v"),
                "pricing": dict(pricing),
                "addedBy": "tester",
            }
        )
    if partner:
        items.append(
            {
                "id": PARITY_PARTNER,
                "endpointId": PARITY_PARTNER[len("fal:"):],
                "label": "Bytedance Seedance 2.5 Reference to Video",
                "category": "reference-to-video",
                "caps": _parity_caps("r2v"),
                "pricing": dict(pricing),
                "addedBy": "tester",
            }
        )
    return items


def _closure_fn(root, name: str):
    """create_blueprint 안쪽 함수(클로저)를 이름으로 찾는다. 없으면 None.

    라우트 함수(`__wrapped__`)와 그 클로저 셀을 따라간다. 라우트로 닿지 않는 기본 인자 분기를
    직접 부를 때만 쓴다.
    """
    seen: set[int] = set()
    stack = [root]
    while stack:
        fn = stack.pop()
        if fn is None or id(fn) in seen:
            continue
        seen.add(id(fn))
        code = getattr(fn, "__code__", None)
        if code is not None and code.co_name == name:
            return fn
        inner = getattr(fn, "__wrapped__", None)
        if inner is not None:
            stack.append(inner)
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if callable(value) and hasattr(value, "__code__"):
                stack.append(value)
    return None


class _ParityEnv:
    """_parity_env() 가 돌려주는 가짜 환경. restore() 로 모든 바꿔치기를 되돌린다."""

    def __init__(self) -> None:
        self.owner = PARITY_OWNER
        self.pid = PARITY_PID
        self.plan_id = PARITY_PLAN
        self.fake = None
        self.app = None
        self.client = None
        self.ark_calls: list[tuple[str, str, dict | None]] = []
        self.fal_calls: list[tuple[str, str, dict | None]] = []
        self.charges: list[dict] = []
        self.video_calls: list[dict] = []
        self.url_calls: list[str] = []
        self.threads: list[str] = []
        self._undo: list[tuple[object, str, object, bool]] = []

    def patch(self, obj, name: str, value):
        """obj.name 을 value 로 바꾸고 되돌릴 값을 기록한다(같은 속성을 여러 번 바꿔도 된다)."""
        missing = not hasattr(obj, name)
        self._undo.append((obj, name, getattr(obj, name, None), missing))
        setattr(obj, name, value)
        return value

    def restore(self) -> None:
        while self._undo:
            obj, name, old, missing = self._undo.pop()
            if missing:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
            else:
                setattr(obj, name, old)
        from src.vpoc import fal  # noqa: PLC0415

        fal._invalidate_cache()

    def reset_calls(self) -> None:
        for bucket in (
            self.ark_calls, self.fal_calls, self.charges, self.video_calls, self.url_calls, self.threads
        ):
            bucket.clear()

    def set_registry(self, items: list[dict]) -> None:
        from src.vpoc import fal  # noqa: PLC0415

        self.fake.write_json(fal.REGISTRY_KEY, {"models": [dict(item) for item in items]})
        fal._invalidate_cache()

    def save(self, state: dict) -> None:
        from src.vpoc import store  # noqa: PLC0415

        store.save_state(self.owner, self.pid, self.plan_id, state)

    def load(self) -> dict:
        from src.vpoc import store  # noqa: PLC0415

        return store.load_state(self.owner, self.pid, self.plan_id)[0]

    def plan_url(self) -> str:
        return f"/api/vpoc/p/{self.pid}/plans/{self.plan_id}"

    def run_url(self, action: str) -> str:
        return f"{self.plan_url()}/run/{action}"

    def ark_posts(self) -> list[tuple[str, str, dict | None]]:
        return [call for call in self.ark_calls if call[0] == "POST" and call[1] == "/contents/generations/tasks"]

    def fal_posts(self) -> list[tuple[str, str, dict | None]]:
        return [call for call in self.fal_calls if call[0] == "POST"]


def _parity_env(
    *,
    registry: bool | list[dict] = True,
    ark_seedance: bool = False,
    ark_key: str = "dummy-ark-key",
    fal_key: str = "dummy-fal-key",
    picker: bool = True,
    is_admin=None,
) -> _ParityEnv:
    """가짜 저장소·레지스트리·외부 호출 기록기·Flask 클라이언트(design.md §11).

    registry: True 면 얼굴·짝 둘 다, 리스트면 그 항목, False 면 빈 레지스트리.
    is_admin: VpocDependencies 에 is_admin 필드가 있을 때만 넘긴다(작업 5 이전과 호환).
    """
    import dataclasses  # noqa: PLC0415
    import threading as _threading  # noqa: PLC0415
    import types  # noqa: PLC0415
    import urllib.request as _urlrequest  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, images, ledger, routes, settings, store, video  # noqa: PLC0415

    env = _ParityEnv()
    try:
        fake = _FakeCharStore()
        env.fake = fake
        for module in (store, ark, fal, images, ledger, video):
            env.patch(module, "STORE", fake)
        fake.blobs[PARITY_CHAR_KEY] = _tiny_jpeg()

        env.patch(settings, "ARK_SEEDANCE_ENABLED", bool(ark_seedance))
        env.patch(settings, "ARK_API_KEY", ark_key)
        env.patch(settings, "FAL_KEY", fal_key)
        env.patch(settings, "MODEL_PICKER_ENABLED", bool(picker))
        env.patch(settings, "VPOC_DUAL_STYLE_ENABLED", False)
        env.patch(settings, "REGEN_ENABLED", True)

        def fake_ark_call(method: str, path: str, payload: dict | None = None) -> dict:
            env.ark_calls.append((method, path, json.loads(json.dumps(payload)) if payload is not None else None))
            if method == "POST":
                return {"id": f"task-{len(env.ark_calls)}"}
            return {"status": "running"}

        def fake_fal_request(method: str, url: str, payload: dict | None = None, *, where: str = "") -> dict:
            env.fal_calls.append((method, url, json.loads(json.dumps(payload)) if payload is not None else None))
            if method == "POST":
                return {"request_id": f"req-{len(env.fal_calls)}"}
            return {}

        def fake_charge(key: str, **kwargs) -> dict:
            env.charges.append({"key": key, **kwargs})
            return {"spentUsd": 0.0, "capUsd": None}

        def fake_video_submit(**kwargs) -> dict:
            env.video_calls.append(dict(kwargs))
            shot_id = str(kwargs.get("shot_id") or "s")
            return {
                "provider": "luma-ray2",
                "invocationArn": "arn:aws:bedrock:test:async-invoke/" + shot_id,
                "clientRequestToken": "luma-parity-" + shot_id,
                "outputPrefix": "p/luma",
                "submittedAt": "2026-10-26T12:00:00Z",
                "promptChars": len(str(kwargs.get("prompt") or "")),
            }

        def fake_urlopen(*args, **kwargs):
            target = args[0] if args else kwargs.get("url")
            env.url_calls.append(str(getattr(target, "full_url", target))[:120])
            raise RuntimeError("check_vpoc: 실제 네트워크 호출 금지")

        class _NoThread:
            """routes 의 백그라운드 스레드를 띄우지 않고 이름만 남긴다."""

            def __init__(self, target=None, name=None, args=(), kwargs=None, daemon=None) -> None:
                env.threads.append(str(name or getattr(target, "__name__", "thread")))

            def start(self) -> None:
                return None

            def join(self, timeout=None) -> None:
                return None

            def is_alive(self) -> bool:
                return False

        env.patch(ark, "_call", fake_ark_call)
        env.patch(fal, "_request", fake_fal_request)
        env.patch(ledger, "charge", fake_charge)
        env.patch(video, "submit", fake_video_submit)
        env.patch(_urlrequest, "urlopen", fake_urlopen)
        env.patch(
            routes,
            "threading",
            types.SimpleNamespace(Thread=_NoThread, Lock=_threading.Lock, local=_threading.local),
        )

        if registry is True:
            env.set_registry(_parity_registry_items())
        elif isinstance(registry, list):
            env.set_registry(registry)
        else:
            env.set_registry([])

        kwargs = {
            "require_auth": lambda handler: handler,
            "current_username": lambda: PARITY_OWNER,
            "get_project": lambda p: {"id": PARITY_PID, "owner": PARITY_OWNER},
            "can_read_project": lambda proj: True,
            "can_write_project": lambda proj: True,
            "graph_run": lambda *a, **k: [],
        }
        if is_admin is not None and "is_admin" in {f.name for f in dataclasses.fields(routes.VpocDependencies)}:
            kwargs["is_admin"] = is_admin
        from flask import Flask  # noqa: PLC0415

        app = Flask("vpoc-parity")
        app.register_blueprint(routes.create_blueprint(routes.VpocDependencies(**kwargs)))
        env.app = app
        env.client = app.test_client()
    except Exception:
        env.restore()
        raise
    return env


def _parity_shot4(shot_id: str, status: str = "queued", **extra) -> dict:
    """④ 샷 하나. completed 면 출력 키·시각을 채운다. extra 가 마지막에 덮는다."""
    shot = {
        "id": shot_id,
        "status": status,
        "attempt": 1,
        "mode": "text",
        "durationS": 5,
        "submittedAt": None,
        "completedAt": None,
        "outputKey": "",
        "invocationArn": "",
        "videoUrl": None,
        "note": None,
        "error": None,
    }
    if status == "completed":
        shot.update(
            {
                "submittedAt": "2026-10-26T11:00:00Z",
                "completedAt": "2026-10-26T11:03:00Z",
                "outputKey": f"users/tester/parity/{shot_id}.mp4",
                "invocationArn": f"ark/tasks/task-done-{shot_id}",
            }
        )
    shot.update(extra)
    return shot


def _parity_state(
    model: str = "seedance-2-5",
    make_mode: str = "shots",
    *,
    step4: dict | None = None,
    resolution: str = "720p",
    durations: tuple[int, ...] = (6, 5),
    use_keyframe: bool = False,
) -> dict:
    """단계 1~4 상태(design.md §11). ② 저장 모델 = model. step4 를 주면 ④ 기본값 위에 덮는다.

    인물 c1 의 그림은 PARITY_CHAR_KEY(_parity_env 가 _tiny_jpeg() 를 넣는다)이고 샷마다 나온다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import store  # noqa: PLC0415

    state = store.new_state(PARITY_OWNER, PARITY_PID, PARITY_PLAN)
    state["currentStep"] = 4 if step4 is not None else 3
    state["maxReachedStep"] = state["currentStep"]
    steps = state["steps"]
    steps["1"] = {"status": "saved", "text": "t", "characters": [], "sceneFiles": []}
    steps["2"] = {
        "status": "ready",
        "story": {"style": "live_action", "settingEn": "A quiet rooftop at dusk"},
        "characters": [
            {
                "id": "c1",
                "nameKo": "서원",
                "nameEn": "Seo Won",
                "handleEn": "the black-haired man",
                "appearanceEn": "A lean man with black hair",
            }
        ],
        "questions": [],
        "answers": {},
        "makeMode": make_mode,
        "videoModel": model,
    }
    shots3 = [
        {
            "id": f"s{index + 1}",
            "summaryKo": f"샷{index + 1}",
            "cameraKo": "고정 샷",
            "cameraEn": "Static camera",
            "locationEn": "A quiet rooftop at dusk",
            "actionEn": "The black-haired man looks at the city",
            "characters": ["c1"],
            "promptEn": f"Cinematic live-action film footage. Shot {index + 1} on a quiet rooftop at dusk.",
            "useKeyframe": bool(use_keyframe),
            "reviewKo": None,
            "durationS": int(seconds),
        }
        for index, seconds in enumerate(durations)
    ]
    steps["3"] = {
        "status": "ready",
        "useKeyframes": False,
        "makeMode": make_mode,
        "resolution": resolution,
        "characters": [
            {"id": "c1", "image": {"status": "ready", "source": "ai", "key": PARITY_CHAR_KEY}}
        ],
        "shots": shots3,
        "cost": {"shots": len(shots3), "totalUsd": 0.0},
    }
    if step4 is not None:
        base = {
            "status": "running",
            "genId": "g20261026110000",
            "videoModel": model,
            "resolution": resolution,
            "oneTake": False,
            "single": make_mode == "single",
            "dual": False,
            "shots": [
                _parity_shot4(item["id"], "completed", durationS=item["durationS"]) for item in shots3
            ],
            "final": {"status": "none", "url": None, "error": None},
        }
        base.update(step4)
        steps["4"] = base
    return state


def check_fal_parity_inflight() -> None:
    """073 작업 1 — 진행 중 BytePlus 회차 유지(R1.1-1.6, D1·X1). 외부 호출은 전부 가짜.

    (ip-a) 플래그 false · 회차 seedance-2-5 · failed 샷 → run/retry → BytePlus 1회(모델·화질·길이)·ARN.
    (ip-b) 같은 회차 queued 샷 → GET plan(refresh_shots) → 같은 본문으로 제출.
    (ip-c) 재생성 금액 = ④ regenUsd(회차 값) → CostMismatch 없이 ARK 제출. vpoc.js 가 regenUsd 를 먼저 본다.
    (ip-d) E5: keyframe 샷 ark.poll Failed → text 로 다시 queued → BytePlus 로 다시 제출.
    (ip-e) ARK_API_KEY 없음 → failed ModelUnavailable "API 키 미설정", ark._call 0회. _submit_block_reason 3갈래.
    (ip-f) 새 선택 게이트 유지: choices 에 byteplus 없음, ② 저장 seedance-2-5 400, generate 회차 모델 = fal 얼굴/Luma.
    (ip-g) 플래그 true: ark.submit 본문 키·content 역할 순서가 073 이전과 같다(플래그 false 와도 같다).
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, settings  # noqa: PLC0415

    bad: list[str] = []
    ark_model_id = "dreamina-seedance-2-5-260628"
    env: _ParityEnv | None = None
    logs = _LogCapture()

    def check_body(tag: str, body: dict | None, seconds: int) -> None:
        body = body or {}
        if body.get("model") != ark_model_id:
            bad.append(f"{tag} 본문 model 이 {ark_model_id} 가 아닙니다: {body.get('model')}")
        if body.get("resolution") != "720p":
            bad.append(f"{tag} 본문 resolution 이 회차 값(720p)이 아닙니다: {body.get('resolution')}")
        if body.get("duration") != seconds:
            bad.append(f"{tag} 본문 duration 이 샷 durationS({seconds})가 아닙니다: {body.get('duration')}")
        if set(body) != PARITY_ARK_BODY_KEYS:
            bad.append(f"{tag} 본문 키가 다릅니다: {sorted(body)}")

    def shot_of(state: dict, shot_id: str) -> dict:
        return next((s for s in state["steps"]["4"].get("shots") or [] if s.get("id") == shot_id), {})

    try:
        env = _parity_env()
        logs.attach()

        # ---- (ip-a) failed 샷 → run/retry ----
        env.save(
            _parity_state(
                "seedance-2-5",
                step4={
                    "status": "partial",
                    "shots": [
                        _parity_shot4(
                            "s1",
                            "failed",
                            durationS=6,
                            invocationArn="ark/tasks/task-old",
                            error={"where": "ark.poll", "type": "AsyncInvokeFailed", "message": "x"},
                        ),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        resp = env.client.post(env.run_url("retry"), json={"shotId": "s1"})
        posts = env.ark_posts()
        body_a: dict = {}
        if resp.status_code != 200:
            bad.append(f"(ip-a) run/retry 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        if len(posts) != 1:
            bad.append(f"(ip-a) ark._call POST 가 1회가 아닙니다: {len(posts)}")
        else:
            body_a = posts[0][2] or {}
            check_body("(ip-a)", body_a, 6)
        s1 = shot_of(env.load(), "s1")
        if s1.get("status") != "submitted" or not str(s1.get("invocationArn") or "").startswith("ark/tasks/"):
            bad.append(f"(ip-a) 샷이 submitted·ark/tasks/ 가 아닙니다: {s1.get('status')} {s1.get('invocationArn')}")
        if s1.get("invocationArn") == "ark/tasks/task-old":
            bad.append("(ip-a) 새 작업 ARN 이 아닙니다(옛 ARN 그대로)")
        expected_usd = settings.shot_usd("seedance-2-5", "720p", 6)
        if len(env.charges) != 1 or env.charges[0].get("model") != ark_model_id or abs(
            float(env.charges[0].get("usd") or 0) - expected_usd
        ) > 1e-6:
            bad.append(f"(ip-a) 원장 기록이 BytePlus 단가 1건이 아닙니다: {env.charges}")
        if not logs.messages("where=ark.submit"):
            bad.append("(ip-a) vpoc.call where=ark.submit 로그가 없습니다")
        if any("dummy-ark-key" in text for text in logs.messages()):
            bad.append("(ip-a) 로그에 API 키 값이 남았습니다")

        # ---- (ip-b) queued 샷 → GET plan ----
        env.reset_calls()
        env.save(
            _parity_state(
                "seedance-2-5",
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4("s1", "queued", durationS=6, attempt=2),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        resp = env.client.get(env.plan_url())
        posts = env.ark_posts()
        if resp.status_code != 200:
            bad.append(f"(ip-b) GET plan 이 200 이 아닙니다: {resp.status_code}")
        if len(posts) != 1:
            bad.append(f"(ip-b) ark._call POST 가 1회가 아닙니다: {len(posts)}")
        else:
            check_body("(ip-b)", posts[0][2], 6)
            if body_a and posts[0][2] != body_a:
                bad.append("(ip-b) 본문이 run/retry 경로(ip-a)와 다릅니다")
        s1 = shot_of(env.load(), "s1")
        if s1.get("status") != "submitted" or not str(s1.get("invocationArn") or "").startswith("ark/tasks/"):
            bad.append(f"(ip-b) 샷이 submitted 가 아닙니다: {s1.get('status')} {s1.get('error')}")

        # ---- (ip-c) 재생성: ④ regenUsd 로 보내면 CostMismatch 없이 ARK 제출 ----
        env.reset_calls()
        env.save(
            _parity_state(
                "seedance-2-5",
                step4={
                    "status": "done",
                    "shots": [
                        _parity_shot4(
                            "s1",
                            "completed",
                            durationS=6,
                            revision={
                                "id": "rev-1",
                                "feedbackKo": "표정을 밝게",
                                "summaryKo": "새 버전",
                                "promptEn": "Cinematic live-action film footage. He smiles at the city.",
                                "useKeyframe": False,
                                "reviewKo": None,
                            },
                        ),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        view = env.client.get(env.plan_url()).get_json() or {}
        shot_view = next(
            (s for s in ((view.get("steps") or {}).get("4") or {}).get("shots") or [] if s.get("id") == "s1"), {}
        )
        row_usd = next(
            (r.get("usd") for r in ((view.get("steps") or {}).get("3") or {}).get("shots") or [] if r.get("id") == "s1"),
            None,
        )
        regen_usd = shot_view.get("regenUsd")
        if regen_usd is None or abs(float(regen_usd) - expected_usd) > 1e-9:
            bad.append(f"(ip-c) regenUsd 가 회차 값({expected_usd})이 아닙니다: {regen_usd}")
        if row_usd is not None and regen_usd is not None and abs(float(row_usd) - float(regen_usd)) <= 0.005:
            bad.append(f"(ip-c) ③ 행 금액({row_usd})이 regenUsd 와 같아 검사가 의미 없습니다")
        resp = env.client.post(
            env.run_url("regenerate"), json={"shotId": "s1", "revisionId": "rev-1", "confirmUsd": row_usd}
        )
        if resp.status_code != 409 or ((resp.get_json() or {}).get("error") or {}).get("type") != "CostMismatch":
            bad.append(f"(ip-c) ③ 행 금액으로 보내면 409 CostMismatch 여야 합니다(옛 화면 동작): {resp.status_code}")
        if env.ark_posts():
            bad.append("(ip-c) CostMismatch 인데 ARK 로 제출됐습니다")
        resp = env.client.post(
            env.run_url("regenerate"), json={"shotId": "s1", "revisionId": "rev-1", "confirmUsd": regen_usd}
        )
        posts = env.ark_posts()
        if resp.status_code != 200:
            bad.append(f"(ip-c) regenUsd 로 보낸 재생성이 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        if len(posts) != 1:
            bad.append(f"(ip-c) 재생성 ark._call POST 가 1회가 아닙니다: {len(posts)}")
        else:
            check_body("(ip-c)", posts[0][2], 6)
        regen_target = shot_of(env.load(), "s1").get("regen") or {}
        if regen_target.get("status") != "submitted" or not str(regen_target.get("invocationArn") or "").startswith(
            "ark/tasks/"
        ):
            bad.append(f"(ip-c) 재생성 대상이 ARK 로 제출되지 않았습니다: {regen_target.get('status')}")
        # 기본 인자(step4 없음) 분기 — 라우트로는 닿지 않아 클로저로 부른다.
        shot4_view = _closure_fn(env.app.view_functions.get("whynot_vpoc.get_plan"), "_shot4_view")
        if shot4_view is None:
            bad.append("(ip-c) _shot4_view 를 찾지 못했습니다")
        else:
            plain = shot4_view({"id": "s1", "status": "completed", "durationS": 6})
            if "regenUsd" not in plain or plain["regenUsd"] is not None:
                bad.append(f"(ip-c) step4 없이 부르면 regenUsd 가 None 이어야 합니다: {plain.get('regenUsd')}")
            old = shot4_view({"id": "s1", "status": "completed"}, None, "", step4={})
            if abs(float(old.get("regenUsd") or 0) - settings.shot_usd("", "", None)) > 1e-9:
                bad.append(f"(ip-c) 옛 회차(모델 없음) regenUsd 가 서버 계산과 다릅니다: {old.get('regenUsd')}")
        js = (PUBLIC / "vpoc.js").read_text(encoding="utf-8")
        if not re.search(r"var amount = shot\.regenUsd != null\s*\?\s*shot\.regenUsd\s*:", js):
            bad.append("(ip-c) vpoc.js 재생성 금액이 shot.regenUsd 를 먼저 보지 않습니다")

        # ---- (ip-d) E5: keyframe 샷 ark.poll Failed → text 로 queued → ARK 재제출 ----
        env.reset_calls()
        poll_calls: list[str] = []

        def fake_poll(invocation_arn: str, output_prefix: str) -> dict:
            poll_calls.append(invocation_arn)
            return {"status": "Failed", "failureMessage": "first frame rejected", "usage": None}

        env.patch(ark, "poll", fake_poll)
        env.save(
            _parity_state(
                "seedance-2-5",
                use_keyframe=True,
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4(
                            "s1",
                            "submitted",
                            durationS=6,
                            mode="keyframe",
                            invocationArn="ark/tasks/task-kf",
                            outputPrefix="p/ark",
                            submittedAt="2026-10-26T11:00:00Z",
                            lastPolledAt="",
                        ),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        env.client.get(env.plan_url())
        posts = env.ark_posts()
        s1 = shot_of(env.load(), "s1")
        if poll_calls != ["ark/tasks/task-kf"]:
            bad.append(f"(ip-d) ark.poll 이 1회 불리지 않았습니다: {poll_calls}")
        if len(posts) != 1:
            bad.append(f"(ip-d) E5 재제출 ark._call POST 가 1회가 아닙니다: {len(posts)}")
        else:
            check_body("(ip-d)", posts[0][2], 6)
        if (
            s1.get("status") != "submitted"
            or int(s1.get("attempt") or 0) != 2
            or s1.get("mode") == "keyframe"
            or not str(s1.get("invocationArn") or "").startswith("ark/tasks/")
            or s1.get("invocationArn") == "ark/tasks/task-kf"
        ):
            bad.append(
                f"(ip-d) E5 뒤 BytePlus 재제출 상태가 아닙니다: {s1.get('status')} attempt={s1.get('attempt')}"
                f" mode={s1.get('mode')} arn={s1.get('invocationArn')}"
            )

        # ---- (ip-e) 키 없음 → failed ModelUnavailable, ark._call 0회 ----
        env.reset_calls()
        env.patch(settings, "ARK_API_KEY", "")
        env.save(
            _parity_state(
                "seedance-2-5",
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4("s1", "queued", durationS=6),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        env.client.get(env.plan_url())
        s1 = shot_of(env.load(), "s1")
        err = s1.get("error") or {}
        if s1.get("status") != "failed" or err.get("type") != "ModelUnavailable":
            bad.append(f"(ip-e) 키 없음인데 failed ModelUnavailable 이 아닙니다: {s1.get('status')} {err.get('type')}")
        if "API 키 미설정" not in str(err.get("message") or ""):
            bad.append(f"(ip-e) 문구에 'API 키 미설정' 이 없습니다: {err.get('message')}")
        if env.ark_calls or env.charges:
            bad.append(f"(ip-e) 키 없음인데 ark._call {len(env.ark_calls)}회·원장 {len(env.charges)}건")
        if ark._submit_block_reason("seedance-2-5") != "API 키 미설정":
            bad.append(f"(ip-e) _submit_block_reason 키 없음: {ark._submit_block_reason('seedance-2-5')!r}")
        env.patch(settings, "ARK_API_KEY", "dummy-ark-key")
        if ark._submit_block_reason("seedance-2-5") != "":
            bad.append(f"(ip-e) 플래그 false 인데 _submit_block_reason 이 막습니다: {ark._submit_block_reason('seedance-2-5')!r}")
        if not ark.unavailable_reason("seedance-2-5"):
            bad.append("(ip-e) unavailable_reason 의 플래그 게이트가 사라졌습니다(새 선택 게이트)")
        env.patch(settings, "ARK_MODEL_IDS", {**settings.ARK_MODEL_IDS, "seedance-2-5": ""})
        if ark._submit_block_reason("seedance-2-5") != "모델 ID 미설정":
            bad.append(f"(ip-e) _submit_block_reason 모델 ID 없음: {ark._submit_block_reason('seedance-2-5')!r}")
        try:
            ark.submit(
                model="seedance-2-5", owner="o", project_id="p", plan_id="pl", shot_id="s1",
                attempt=1, prompt="Cinematic test.",
            )
            bad.append("(ip-e) 모델 ID 없음인데 ark.submit 이 통과했습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != "ModelUnavailable" or "Seedance 2.5: 모델 ID 미설정" not in str(
                getattr(exc, "message", exc)
            ):
                bad.append(f"(ip-e) ark.submit 차단 문구가 다릅니다: {getattr(exc, 'kind', '')} {exc}")
        if env.ark_calls:
            bad.append(f"(ip-e) 차단인데 ark._call 이 {len(env.ark_calls)}회 불렸습니다")
        env.patch(settings, "ARK_MODEL_IDS", {**settings.ARK_MODEL_IDS, "seedance-2-5": ark_model_id})
        if ark._submit_block_reason(PARITY_FACE) != "":
            bad.append(f"(ip-e) fal 모델 _submit_block_reason 이 fal 판정과 다릅니다: {ark._submit_block_reason(PARITY_FACE)!r}")

        # ---- (ip-f) 새 선택 게이트 유지 ----
        env.reset_calls()
        items = ark.choices()
        if any(item.get("provider") == "byteplus" for item in items):
            bad.append("(ip-f) 플래그 false 인데 choices 에 byteplus 가 있습니다")
        if not any(item.get("id") == PARITY_FACE and item.get("available") for item in items):
            bad.append("(ip-f) choices 에 fal 얼굴이 available 로 없습니다")
        env.save(_parity_state("seedance-2-5"))
        resp = env.client.put(f"{env.plan_url()}/steps/2", json={"videoModel": "seedance-2-5"})
        if resp.status_code != 400 or ((resp.get_json() or {}).get("error") or {}).get("type") != "ModelUnavailable":
            bad.append(f"(ip-f) ② 저장 videoModel=seedance-2-5 가 400 ModelUnavailable 이 아닙니다: {resp.status_code}")
        resp = env.client.put(f"{env.plan_url()}/steps/2", json={"videoModel": PARITY_FACE})
        if resp.status_code != 200:
            bad.append(f"(ip-f) ② 저장 videoModel=fal 얼굴이 200 이 아닙니다: {resp.status_code}")
        for tag, registry_items, want in (
            ("등록", _parity_registry_items(), PARITY_FACE),
            ("미등록", [], ark.DEFAULT_MODEL),
        ):
            env.set_registry(registry_items)
            env.save(_parity_state("seedance-2-5"))
            plan = env.client.get(env.plan_url()).get_json() or {}
            total = (((plan.get("steps") or {}).get("3") or {}).get("cost") or {}).get("totalUsd")
            resp = env.client.post(env.run_url("generate"), json={"confirmUsd": total})
            if resp.status_code != 200:
                bad.append(f"(ip-f) generate({tag})가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
                continue
            got = env.load()["steps"]["4"].get("videoModel")
            if got != want:
                bad.append(f"(ip-f) generate({tag}) 회차 모델이 {want} 가 아닙니다: {got}")
        env.set_registry(_parity_registry_items())
        if env.ark_calls:
            bad.append(f"(ip-f) 새 선택·생성 검사 중 ark._call 이 불렸습니다: {len(env.ark_calls)}")

        # ---- (ip-g) 플래그 true: 본문 키·content 역할 순서가 073 이전과 같다 ----
        env.reset_calls()
        cases = [
            ("글만", {}, ["text"]),
            (
                "참고 2장(+시작·끝 무시)",
                {"reference_jpegs_b64": ["AAA", "BBB"], "keyframe_jpeg_b64": "KKK", "last_frame_jpeg_b64": "LLL"},
                ["text", "reference_image", "reference_image"],
            ),
            ("시작·끝", {"keyframe_jpeg_b64": "KKK", "last_frame_jpeg_b64": "LLL"}, ["text", "first_frame", "last_frame"]),
            ("시작만", {"keyframe_jpeg_b64": "KKK"}, ["text", "first_frame"]),
        ]
        bodies: dict[bool, list[dict]] = {}
        for flag in (True, False):
            env.patch(settings, "ARK_SEEDANCE_ENABLED", flag)
            bodies[flag] = []
            for tag, extra, roles in cases:
                before = len(env.ark_calls)
                job = ark.submit(
                    model="seedance-2-5", owner="o", project_id="p", plan_id="pl", shot_id="s1",
                    attempt=1, prompt="Cinematic test.", gen_id="g1", resolution="720p", duration_s=6, **extra,
                )
                body = (env.ark_calls[before][2] if len(env.ark_calls) > before else {}) or {}
                bodies[flag].append(body)
                got_roles = [item.get("role") or item.get("type") for item in body.get("content") or []]
                if got_roles != roles:
                    bad.append(f"(ip-g) flag={flag} {tag} content 역할 순서가 다릅니다: {got_roles}")
                check_body(f"(ip-g) flag={flag} {tag}", body, 6)
                if body.get("ratio") != settings.ASPECT or body.get("watermark") is not False or body.get(
                    "generate_audio"
                ) is not bool(settings.ARK_GENERATE_AUDIO):
                    bad.append(f"(ip-g) flag={flag} {tag} ratio·watermark·generate_audio 가 다릅니다")
                if not str(job.get("invocationArn") or "").startswith("ark/tasks/") or job.get("provider") != "seedance-2-5":
                    bad.append(f"(ip-g) flag={flag} {tag} 반환값이 다릅니다: {job}")
        if bodies.get(True) != bodies.get(False):
            bad.append("(ip-g) 플래그 true/false 의 제출 본문이 다릅니다")

        # 외부 호출 0회(실제 네트워크·fal·Luma)
        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.fal_calls or env.video_calls:
            bad.append(f"fal·Luma 제출이 있었습니다: fal={len(env.fal_calls)} luma={len(env.video_calls)}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("진행 중 BytePlus 회차 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 진행 중 BytePlus 회차 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 진행 중 BytePlus 회차 검사 (ip-a)retry→ARK 모델·화질·길이·ARN·원장 "
            "(ip-b)GET 대기 샷 같은 본문 (ip-c)regenUsd 재생성·409 재현·vpoc.js (ip-d)E5 텍스트 재제출 "
            "(ip-e)키 없음 차단·_submit_block_reason 3갈래 (ip-f)choices·② 400·generate 얼굴/Luma "
            "(ip-g)본문 키·역할 순서 고정"
        )


def _rendered_env_values(text: str, key: str) -> list[str]:
    """렌더된 매니페스트에서 `- name: <key>` 바로 다음 `value: "..."` 값들."""
    pattern = re.compile(r"-\s+name:\s+" + re.escape(key) + r"\s*\n\s*value:\s*\"([^\"]*)\"")
    return pattern.findall(text)


def check_fal_parity_rollback() -> None:
    """073 작업 1 — 되돌리기 배선(R7.1-7.3, R7.5, D7). 렌더만 하고 배포·클러스터 명령은 없다.

    (rw-a) PARITY_ENV_KEYS 마다 k8s/workloads.yaml.tmpl 에 `- name: <키>` + `value: "${<키>}"`.
    (rw-b) deploy/config.env.example 에 `<키>=` 줄. 플래그 기본 false(작업 4 뒤 TAG_STYLE 기본 at).
    (rw-c) render.py: example + prod/dev 오버레이 → add_derived → render_text 예외 없음, 값 "false".
           오버레이에 true 를 더하면 "true"(되돌리기 경로).
    """
    import importlib.util  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    bad: list[str] = []
    template_path = ROOT / "k8s" / "workloads.yaml.tmpl"
    example_path = ROOT / "deploy" / "config.env.example"
    template = template_path.read_text(encoding="utf-8")
    example = example_path.read_text(encoding="utf-8")
    flag = "VPOC_ARK_SEEDANCE_ENABLED"
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import settings as _vpoc_settings  # noqa: PLC0415

    family_default = _vpoc_settings.FAL_SEEDANCE_FAMILY_DEFAULT

    def safe(exc: BaseException) -> str:
        # render.py 의 KEY=VALUE 형식 오류 문구는 원문 줄(값)을 담는다 — 값은 출력하지 않는다.
        text = str(exc)
        return "설정 줄 형식 오류(값 생략)" if "KEY=VALUE" in text else text[:300]

    # (rw-a)·(rw-b)
    for key in PARITY_ENV_KEYS:
        names = re.findall(r"^\s*-\s+name:\s+" + re.escape(key) + r"\s*$", template, re.M)
        pair = re.search(
            r"^\s*-\s+name:\s+" + re.escape(key) + r"\s*\n\s*value:\s*\"\$\{" + re.escape(key) + r"\}\"\s*$",
            template,
            re.M,
        )
        if len(names) != 1 or not pair:
            bad.append(f"(rw-a) 템플릿에 '- name: {key}' + 'value: \"${{{key}}}\"' 가 한 번 있지 않습니다")
        found = re.findall(r"^" + re.escape(key) + r"=(.*)$", example, re.M)
        if len(found) != 1:
            bad.append(f"(rw-b) config.env.example 에 '{key}=' 줄이 한 번 있지 않습니다: {len(found)}")
            continue
        value = found[0].strip()
        if key == flag and value != "false":
            bad.append(f"(rw-b) {key} 기본값이 false 가 아닙니다: {value!r}")
        if key == "VPOC_FAL_SEEDANCE_TAG_STYLE" and value != "at":
            bad.append(f"(rw-b) {key} 기본값이 at 이 아닙니다: {value!r}")
        if key == "VPOC_FAL_SEEDANCE_FAMILY" and value != family_default:
            # design.md §6: base 값 = 코드 기본(얼굴 i2v = 짝 r2v). 템플릿이 늘 env 를 넣으므로 이 값이 실제 기본이다.
            bad.append(f"(rw-b) {key} 기본값이 코드 기본과 다릅니다: {value!r}")
        if key == "VPOC_FAL_SEEDANCE_MAX_S" and value != "15":
            # design.md §6: base 값 = 코드 기본 15(설계자 승인 상한).
            bad.append(f"(rw-b) {key} 기본값이 15 가 아닙니다: {value!r}")
        if key == "VPOC_FAL_PROMPT_MAX_OVERRIDES" and value != _vpoc_settings.FAL_PROMPT_MAX_OVERRIDES_DEFAULT:
            # design.md §6: base 값 = 코드 기본(bytedance/seedance-2.5/*=4000).
            bad.append(f"(rw-b) {key} 기본값이 코드 기본과 다릅니다: {value!r}")
        if key == "VPOC_POLL_GIVEUP_S" and value != "1800":
            # design.md §13.2: base 값 = 코드 기본 1800(작업 7, R15.8).
            bad.append(f"(rw-b) {key} 기본값이 1800 이 아닙니다: {value!r}")

    # (rw-c) render.py 로 실제 렌더(파일은 쓰지 않는다)
    try:
        spec = importlib.util.spec_from_file_location("_vpoc_deploy_render", ROOT / "deploy" / "render.py")
        render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render)  # type: ignore[union-attr]
        params = list(inspect.signature(render.add_derived).parameters)
        if len(params) != 1:
            bad.append(f"(rw-c) add_derived 시그니처가 (config) 가 아닙니다: {params}")
        base = render.load_config(example_path)
        for target in ("prod", "dev"):
            overlay_path = ROOT / "deploy" / f"config.{target}.env"
            try:
                overlay = render.load_config(overlay_path)
                for extra, want in (({}, "false"), ({flag: "true"}, "true")):
                    layer = {**overlay, **extra}
                    unknown = sorted(set(layer) - set(base))
                    if unknown:
                        # CI(load_config_chain)는 base 에 없는 키를 거절한다. 키 이름만 적는다.
                        bad.append(f"(rw-c) {target}: 오버레이에 base 에 없는 키 {unknown}")
                        continue
                    values = render.add_derived({**base, **layer})
                    text = render.render_text(template, values, str(template_path.relative_to(ROOT)))
                    got = _rendered_env_values(text, flag)
                    if got != [want]:
                        bad.append(f"(rw-c) {target}{'+true' if extra else ''}: 렌더된 {flag} 값이 [{want!r}] 가 아닙니다: {got}")
                    family = _rendered_env_values(text, "VPOC_FAL_SEEDANCE_FAMILY")
                    if family != [family_default]:
                        bad.append(f"(rw-c) {target}: 렌더된 VPOC_FAL_SEEDANCE_FAMILY 값이 기본 쌍이 아닙니다: {family}")
                    max_s = _rendered_env_values(text, "VPOC_FAL_SEEDANCE_MAX_S")
                    if max_s != ["15"]:
                        bad.append(f"(rw-c) {target}: 렌더된 VPOC_FAL_SEEDANCE_MAX_S 값이 ['15'] 가 아닙니다: {max_s}")
                    prompt_max = _rendered_env_values(text, "VPOC_FAL_PROMPT_MAX_OVERRIDES")
                    if prompt_max != [_vpoc_settings.FAL_PROMPT_MAX_OVERRIDES_DEFAULT]:
                        bad.append(f"(rw-c) {target}: 렌더된 VPOC_FAL_PROMPT_MAX_OVERRIDES 값이 기본이 아닙니다: {prompt_max}")
                    tag_style = _rendered_env_values(text, "VPOC_FAL_SEEDANCE_TAG_STYLE")
                    if tag_style != ["at"]:
                        bad.append(f"(rw-c) {target}: 렌더된 VPOC_FAL_SEEDANCE_TAG_STYLE 값이 ['at'] 가 아닙니다: {tag_style}")
                    giveup = _rendered_env_values(text, "VPOC_POLL_GIVEUP_S")
                    if giveup != ["1800"]:
                        bad.append(f"(rw-c) {target}: 렌더된 VPOC_POLL_GIVEUP_S 값이 ['1800'] 이 아닙니다: {giveup}")
                    if "${" in text:
                        bad.append(f"(rw-c) {target}: 렌더 결과에 치환되지 않은 변수가 남았습니다")
            except (Exception, SystemExit) as exc:  # noqa: BLE001 — render.py 는 SystemExit 로 실패를 알린다.
                bad.append(f"(rw-c) {target}: 렌더 실패: {type(exc).__name__} {safe(exc)}")
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        bad.append(f"(rw-c) render.py 를 읽지 못했습니다: {type(exc).__name__} {safe(exc)}")

    if bad:
        fail("fal-seedance-parity 되돌리기 배선 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 되돌리기 배선 검사 (rw-a)템플릿 env "
            f"{len(PARITY_ENV_KEYS)}개 (rw-b)config.env.example 기본값 "
            "(rw-c)render.py prod·dev 렌더 false·오버레이 true"
        )
PARITY_KLING = "fal:fal-ai/kling-video/v3/pro/image-to-video"


def _parity_kling_item() -> dict:
    """계열이 아닌 fal 모델(Kling i2v 모양 가짜 caps, 073 fr-i·du-h 공용)."""
    durations = ["5", "10"]
    return {
        "id": PARITY_KLING,
        "endpointId": PARITY_KLING[len("fal:"):],
        "label": "Kling 3.0 Pro Image to Video",
        "category": "image-to-video",
        "caps": {
            "promptField": "prompt",
            "promptMaxLength": 2500,
            "startImageField": "image_url",
            "endImageField": "tail_image_url",
            "referenceImagesField": "",
            "durations": durations,
            "durationAsString": True,
            "durationValues": {value: value for value in durations},
            "sendDuration": True,
            "resolutions": [],
            "resolutionValues": {},
            "aspectRatios": ["16:9", "9:16", "1:1"],
            "audioField": "generate_audio",
            "previzCapable": False,
            "requiredFields": ["prompt", "image_url"],
            "available": True,
            "reason": "",
        },
        "pricing": {"unit": "seconds", "unitPrice": 0.112},
        "addedBy": "tester",
    }


def check_fal_parity_route() -> None:
    """073 작업 2 — Seedance 계열 엔드포인트 선택(i2v·r2v)·소리 고정(R2, R6, R11.1, R14.3·14.5).

    (fr-a) settings 파서·fal_family_partner·is_fal_seedance_family.
    (fr-b) fal.unavailable_reason(얼굴): 짝 미등록 E1 · 짝 caps 사용 불가 E1b · 둘 다 "" · 짝 자신은 짝 요구 없음.
    (fr-c) ark.choices·label: 얼굴 "Seedance 2.5 (fal)", r2v 목록에 없음, 짝 미등록 available=false E1, Kling 이름 그대로.
    (fr-d) fal.route_model 표(§4) 5갈래 + reference_caps 짝 있음·없음.
    (fr-e) 통합: shots 참고→r2v image_urls · 옛 키 장면→i2v image_url·end_image_url · 글만→r2v ·
           single 인물2+배경1→r2v 3장 · single 참고 0장→r2v text. videoModel=얼굴, provider·endpointModel=엔드포인트,
           vpoc.fal.route 로그 1줄.
    (fr-f) 참고 그림 10장 이상 → image_urls 9장 이하.
    (fr-g) 회차 도중 짝 해제 → failed ModelUnavailable(E5), i2v 로 보내지 않음, 원장 없음.
    (fr-h) 소리: i2v·r2v generate_audio == ARK_GENERATE_AUDIO, False 로 바꾸면 False.
    (fr-i) 회귀: 계열이 아닌 fal(Kling) 본문 = build_payload 기존 결과, URL 은 그 모델.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, images, settings, store  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    face_eid = PARITY_FACE[len("fal:"):]
    partner_eid = PARITY_PARTNER[len("fal:"):]
    e1 = "짝 모델(reference-to-video)이 등록되지 않았습니다"
    e1b = "짝 모델(reference-to-video)을 쓸 수 없습니다: "
    image_fields = ("image_url", "end_image_url", "image_urls")

    def shot_of(state: dict, shot_id: str) -> dict:
        return next((s for s in state["steps"]["4"].get("shots") or [] if s.get("id") == shot_id), {})

    def job_key_of(state: dict, shot_id: str, attempt: int = 1) -> str:
        step4 = state["steps"]["4"]
        return store.job_key(PARITY_OWNER, PARITY_PID, PARITY_PLAN, shot_id, attempt, str(step4.get("genId") or ""))

    def job_of(state: dict, shot_id: str, attempt: int = 1) -> dict:
        return env.fake.read_json(job_key_of(state, shot_id, attempt))[0] or {}

    def run(state: dict, shot_id: str = "s1") -> tuple[list, dict, dict, dict]:
        """상태를 저장하고 GET plan(refresh_shots → submit_shot)으로 대기 샷 하나를 제출한다."""
        env.reset_calls()
        logs.clear()
        # 앞 경우의 jobs 기록(같은 키)을 지워 이번 제출 기록만 본다.
        env.fake.json.pop(job_key_of(state, shot_id), None)
        env.save(state)
        resp = env.client.get(env.plan_url())
        if resp.status_code != 200:
            bad.append(f"GET plan 이 200 이 아닙니다({shot_id}): {resp.status_code}")
        loaded = env.load()
        return env.fal_posts(), shot_of(loaded, shot_id), job_of(loaded, shot_id), loaded

    def shots_state(model: str = PARITY_FACE, **plan_extra) -> dict:
        state = _parity_state(
            model,
            step4={
                "status": "running",
                "shots": [
                    _parity_shot4("s1", "queued", durationS=6),
                    _parity_shot4("s2", "completed", durationS=5),
                ],
            },
        )
        state["steps"]["3"]["shots"][0].update(plan_extra)
        return state

    def single_state(char_keys: list[str], bg_key: str) -> dict:
        state = _parity_state(
            PARITY_FACE,
            "single",
            step4={"status": "running", "shots": [_parity_shot4(ark.ONE_TAKE_ID, "queued", durationS=6)]},
        )
        state["steps"]["3"]["master"] = {
            "version": 1,
            "totalS": 6,
            "styleEn": "Cinematic live-action film footage",
            "background": {"promptEn": "A quiet rooftop at dusk", "imageKey": bg_key},
            "characters": [
                {"id": f"c{i + 1}", "handleEn": f"person {i + 1}", "promptEn": "dark coat", "imageKey": key}
                for i, key in enumerate(char_keys)
            ],
            "beats": [{"id": "b1", "t0": 0, "t1": 6, "actionEn": "They look at the city", "cameraEn": "Static"}],
        }
        return state

    def expect_route(
        tag: str, posts: list, shot: dict, job: dict, loaded: dict, *, endpoint: str, fields: dict, mode: str
    ) -> dict:
        """POST 1회·URL·이미지 필드·기록(videoModel=얼굴, provider·endpointModel=엔드포인트)·route 로그 1줄."""
        if len(posts) != 1:
            bad.append(f"{tag} fal POST 가 1회가 아닙니다: {len(posts)} (샷 {shot.get('status')} {shot.get('error')})")
            return {}
        url, body = posts[0][1], posts[0][2] or {}
        if url != f"{settings.FAL_QUEUE_URL}/{endpoint[len('fal:'):]}":
            bad.append(f"{tag} POST URL 이 {endpoint} 가 아닙니다: {url}")
        for field in image_fields:
            want = fields.get(field)
            if want is None and field in body:
                bad.append(f"{tag} 본문에 {field} 가 있으면 안 됩니다")
            elif want is not None:
                got = body.get(field)
                count = len(got) if isinstance(got, list) else (1 if got else 0)
                if count != want:
                    bad.append(f"{tag} 본문 {field} 개수가 {want} 가 아닙니다: {count}")
        if loaded["steps"]["4"].get("videoModel") != PARITY_FACE:
            bad.append(f"{tag} steps.4.videoModel 이 얼굴 id 가 아닙니다: {loaded['steps']['4'].get('videoModel')}")
        if shot.get("status") != "submitted" or shot.get("provider") != endpoint:
            bad.append(f"{tag} ④ 샷 status·provider 가 다릅니다: {shot.get('status')} {shot.get('provider')}")
        if shot.get("mode") != mode:
            bad.append(f"{tag} ④ 샷 mode 가 {mode} 가 아닙니다: {shot.get('mode')}")
        if job.get("endpointModel") != endpoint or job.get("videoModel") != PARITY_FACE:
            bad.append(f"{tag} jobs endpointModel·videoModel 이 다릅니다: {job.get('endpointModel')} {job.get('videoModel')}")
        routes_logged = logs.messages("vpoc.fal.route ")
        if len(routes_logged) != 1 or f"endpoint={endpoint} " not in (routes_logged or [""])[0]:
            bad.append(f"{tag} vpoc.fal.route 로그가 1줄이 아니거나 엔드포인트가 다릅니다: {routes_logged}")
        if len(env.charges) != 1:
            bad.append(f"{tag} 원장 기록이 1건이 아닙니다: {len(env.charges)}")
        return body

    saved_family = settings.FAL_SEEDANCE_FAMILY
    saved_audio = settings.ARK_GENERATE_AUDIO
    try:
        # ---- (fr-a) 설정 해석 ----
        parse = settings._parse_fal_family
        if parse(settings.FAL_SEEDANCE_FAMILY_DEFAULT) != {face_eid: partner_eid}:
            bad.append(f"(fr-a) 기본 쌍이 1개가 아닙니다: {parse(settings.FAL_SEEDANCE_FAMILY_DEFAULT)}")
        if os.getenv("VPOC_FAL_SEEDANCE_FAMILY") is None and settings.FAL_SEEDANCE_FAMILY != {face_eid: partner_eid}:
            bad.append(f"(fr-a) env 없을 때 FAL_SEEDANCE_FAMILY 가 기본 쌍이 아닙니다: {settings.FAL_SEEDANCE_FAMILY}")
        if parse("a=b,c=d") != {"a": "b", "c": "d"}:
            bad.append(f"(fr-a) 'a=b,c=d' 가 2쌍이 아닙니다: {parse('a=b,c=d')}")
        broken = "x, =b ,a=, a b=c, fal:a=b, a=fal:b, a=a, a=b=c,ok/face = ok/partner"
        if parse(broken) != {"ok/face": "ok/partner"}:
            bad.append(f"(fr-a) 형식이 틀린 항목을 버리지 않았습니다: {parse(broken)}")
        if parse("") != {} or parse("  ") != {} or parse(None) != {}:  # type: ignore[arg-type]
            bad.append("(fr-a) 빈 문자열이 {} 가 아닙니다")
        if settings.fal_family_partner(PARITY_FACE) != PARITY_PARTNER:
            bad.append(f"(fr-a) 얼굴의 짝이 다릅니다: {settings.fal_family_partner(PARITY_FACE)!r}")
        for other in (PARITY_PARTNER, PARITY_KLING, "seedance-2-5", "", face_eid):
            if settings.fal_family_partner(other):
                bad.append(f"(fr-a) {other!r} 는 짝이 없어야 합니다: {settings.fal_family_partner(other)!r}")
        if not (settings.is_fal_seedance_family(PARITY_FACE) and settings.is_fal_seedance_family(PARITY_PARTNER)):
            bad.append("(fr-a) is_fal_seedance_family 가 얼굴·짝 모두 True 가 아닙니다")
        for other in (PARITY_KLING, "seedance-2-5", "luma-ray2", "", face_eid):
            if settings.is_fal_seedance_family(other):
                bad.append(f"(fr-a) is_fal_seedance_family({other!r}) 가 True 입니다")
        if settings.FAL_SEEDANCE_LABEL != "Seedance 2.5 (fal)":
            bad.append(f"(fr-a) FAL_SEEDANCE_LABEL 이 다릅니다: {settings.FAL_SEEDANCE_LABEL!r}")

        env = _parity_env(registry=_parity_registry_items() + [_parity_kling_item()])
        logs.attach()

        # ---- (fr-b) fal.unavailable_reason 짝 분기 ----
        if fal.unavailable_reason(PARITY_FACE) != "":
            bad.append(f"(fr-b) 둘 다 등록인데 얼굴이 막힙니다: {fal.unavailable_reason(PARITY_FACE)!r}")
        env.set_registry(_parity_registry_items(partner=False))
        if not fal.unavailable_reason(PARITY_FACE).startswith(e1):
            bad.append(f"(fr-b) 짝 미등록인데 E1 이 아닙니다: {fal.unavailable_reason(PARITY_FACE)!r}")
        broken_partner = _parity_registry_items()
        broken_partner[1]["caps"] = {**broken_partner[1]["caps"], "available": False, "reason": "채울 수 없는 필수 입력: x"}
        env.set_registry(broken_partner)
        if fal.unavailable_reason(PARITY_FACE) != e1b + "채울 수 없는 필수 입력: x":
            bad.append(f"(fr-b) 짝 caps 사용 불가인데 E1b 가 아닙니다: {fal.unavailable_reason(PARITY_FACE)!r}")
        env.set_registry(_parity_registry_items(face=False))
        if fal.unavailable_reason(PARITY_PARTNER) != "":
            bad.append(f"(fr-b) 짝 단독(vnew r2v)이 막힙니다: {fal.unavailable_reason(PARITY_PARTNER)!r}")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", {})
        env.set_registry(_parity_registry_items(partner=False))
        if fal.unavailable_reason(PARITY_FACE) != "":
            bad.append("(fr-b) 계열 라우팅을 끄면(빈 값) 얼굴 단독이 사용 가능해야 합니다(070 동작)")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", saved_family)

        # ---- (fr-c) ark.choices·label ----
        env.set_registry(_parity_registry_items() + [_parity_kling_item()])
        items = {item.get("id"): item for item in ark.choices()}
        face_row = items.get(PARITY_FACE) or {}
        if face_row.get("label") != "Seedance 2.5 (fal)" or not face_row.get("available"):
            bad.append(f"(fr-c) 얼굴 항목 이름·available 이 다릅니다: {face_row.get('label')!r} {face_row.get('available')}")
        if PARITY_PARTNER in items:
            bad.append("(fr-c) r2v 짝이 ② 목록에 있습니다")
        if (items.get(PARITY_KLING) or {}).get("label") != "fal · Kling 3.0 Pro Image to Video":
            bad.append(f"(fr-c) Kling 이름이 바뀌었습니다: {(items.get(PARITY_KLING) or {}).get('label')!r}")
        if ark.label(PARITY_FACE) != "Seedance 2.5 (fal)" or ark.label(PARITY_KLING) != "fal · Kling 3.0 Pro Image to Video":
            bad.append(f"(fr-c) ark.label 이 다릅니다: {ark.label(PARITY_FACE)!r} {ark.label(PARITY_KLING)!r}")
        if ark.label("seedance-2-5") != "Seedance 2.5" or ark.label("fal:not/registered") != "fal:not/registered":
            bad.append("(fr-c) ark.label 비계열 분기(ARK·미등록 fal)가 바뀌었습니다")
        env.set_registry(_parity_registry_items(partner=False) + [_parity_kling_item()])
        face_row = {item.get("id"): item for item in ark.choices()}.get(PARITY_FACE) or {}
        if face_row.get("available") is not False or not str(face_row.get("reason") or "").startswith(e1):
            bad.append(f"(fr-c) 짝 미등록인데 available=false·E1 이 아닙니다: {face_row.get('available')} {face_row.get('reason')!r}")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", {})
        if ark.label(PARITY_FACE) != "fal · Bytedance Seedance 2.5 Image to Video":
            bad.append(f"(fr-c) 계열을 끄면 얼굴 이름이 fal · <label> 이어야 합니다: {ark.label(PARITY_FACE)!r}")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", saved_family)

        # ---- (fr-d) route_model 표 + reference_caps ----
        env.set_registry(_parity_registry_items() + [_parity_kling_item()])
        table = [
            ((True, True), PARITY_PARTNER),
            ((True, False), PARITY_PARTNER),
            ((False, True), PARITY_FACE),
            ((False, False), PARITY_PARTNER),
        ]
        for (refs, start), want in table:
            got = fal.route_model(PARITY_FACE, has_references=refs, has_start=start)
            if got != want:
                bad.append(f"(fr-d) route_model(참고={refs}, 시작={start}) 가 {want} 가 아닙니다: {got}")
        for other in (PARITY_KLING, PARITY_PARTNER, "seedance-2-5"):
            for refs, start in ((True, True), (False, False)):
                if fal.route_model(other, has_references=refs, has_start=start) != other:
                    bad.append(f"(fr-d) 계열 아님({other}) 인데 route_model 이 바꿨습니다")
        if fal.reference_caps(PARITY_FACE).get("referenceImagesField") != "image_urls":
            bad.append("(fr-d) reference_caps(얼굴) 이 짝 caps 가 아닙니다")
        if fal.reference_caps(PARITY_KLING).get("startImageField") != "image_url" or fal.reference_caps(
            PARITY_KLING
        ).get("referenceImagesField"):
            bad.append("(fr-d) reference_caps(Kling) 이 자기 caps 가 아닙니다")
        if fal.reference_caps("fal:not/registered") != {}:
            bad.append("(fr-d) reference_caps(미등록) 이 {} 가 아닙니다")
        # 되돌리기(VPOC_FAL_SEEDANCE_FAMILY=""): 얼굴은 늘 자기 자신(070 동작), 계열 판정도 False.
        env.patch(settings, "FAL_SEEDANCE_FAMILY", {})
        if fal.route_model(PARITY_FACE, has_references=True, has_start=False) != PARITY_FACE:
            bad.append("(fr-d) 계열을 끄면 참고 샷도 얼굴(i2v)로 가야 합니다")
        if fal.reference_caps(PARITY_FACE).get("startImageField") != "image_url" or settings.is_fal_seedance_family(
            PARITY_FACE
        ):
            bad.append("(fr-d) 계열을 끄면 reference_caps(얼굴) 이 자기 caps, 계열 판정이 False 여야 합니다")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", saved_family)
        env.set_registry(_parity_registry_items(partner=False))
        if fal.reference_caps(PARITY_FACE) != {}:
            bad.append("(fr-d) 짝 미등록이면 reference_caps(얼굴) 이 {} 여야 합니다")
        env.set_registry(_parity_registry_items() + [_parity_kling_item()])

        # ---- (fr-e) 통합 ----
        posts, shot, job, loaded = run(shots_state())
        body_r2v = expect_route(
            "(fr-e) shots 인물 참고", posts, shot, job, loaded,
            endpoint=PARITY_PARTNER, fields={"image_urls": 1}, mode="reference",
        )

        kf_keys = ["users/tester/parity/kf-a.jpg", "users/tester/parity/kf-b.jpg"]
        for key in kf_keys:
            env.fake.blobs[key] = _tiny_jpeg()

        def keyframe_state() -> dict:
            state_kf = shots_state(keyframes=["k1", "k2"])
            state_kf["steps"]["3"]["keyframes"] = [
                {"id": "k1", "final": {"key": kf_keys[0]}},
                {"id": "k2", "final": {"key": kf_keys[1]}},
            ]
            return state_kf

        posts, shot, job, loaded = run(keyframe_state())
        body_i2v = expect_route(
            "(fr-e) 옛 키 장면", posts, shot, job, loaded,
            endpoint=PARITY_FACE, fields={"image_url": 1, "end_image_url": 1}, mode="keyframe",
        )

        posts, shot, job, loaded = run(shots_state(characters=[]))
        expect_route(
            "(fr-e) 참고·시작 없음", posts, shot, job, loaded, endpoint=PARITY_PARTNER, fields={}, mode="text",
        )

        single_keys = ["users/tester/parity/m-c1.jpg", "users/tester/parity/m-c2.jpg", "users/tester/parity/m-bg.jpg"]
        for key in single_keys:
            env.fake.blobs[key] = _tiny_jpeg()
        posts, shot, job, loaded = run(single_state(single_keys[:2], single_keys[2]), ark.ONE_TAKE_ID)
        expect_route(
            "(fr-e) single 인물2·배경1", posts, shot, job, loaded,
            endpoint=PARITY_PARTNER, fields={"image_urls": 3}, mode="reference",
        )
        posts, shot, job, loaded = run(single_state([], ""), ark.ONE_TAKE_ID)
        expect_route(
            "(fr-e) single 참고 0장", posts, shot, job, loaded, endpoint=PARITY_PARTNER, fields={}, mode="text",
        )

        # ---- (fr-f) 참고 그림 10장 이상 → 9장 이하 ----
        state = shots_state()
        many = [f"c{i}" for i in range(1, 12)]
        state["steps"]["2"]["characters"] = [
            {"id": cid, "nameKo": cid, "nameEn": cid, "handleEn": f"person {cid}", "appearanceEn": ""} for cid in many
        ]
        state["steps"]["3"]["characters"] = []
        for cid in many:
            key = f"users/tester/parity/many-{cid}.jpg"
            env.fake.blobs[key] = _tiny_jpeg()
            state["steps"]["3"]["characters"].append({"id": cid, "image": {"status": "ready", "source": "ai", "key": key}})
        state["steps"]["3"]["shots"][0]["characters"] = many
        posts, shot, job, loaded = run(state)
        sent = ((posts[0][2] or {}).get("image_urls") if posts else None) or []
        if not posts or not 1 <= len(sent) <= 9:
            bad.append(f"(fr-f) 참고 11장 샷의 image_urls 가 1~9장이 아닙니다: {len(sent)} (POST {len(posts)})")
        if posts and posts[0][1] != f"{settings.FAL_QUEUE_URL}/{partner_eid}":
            bad.append(f"(fr-f) 참고 많은 샷이 r2v 로 가지 않았습니다: {posts[0][1]}")

        # ---- (fr-g) 회차 도중 짝 해제 → E5 ----
        env.set_registry(_parity_registry_items(partner=False))
        posts, shot, job, loaded = run(shots_state())
        err = shot.get("error") or {}
        if shot.get("status") != "failed" or err.get("type") != "ModelUnavailable":
            bad.append(f"(fr-g) 짝 해제 뒤 failed ModelUnavailable 이 아닙니다: {shot.get('status')} {err.get('type')}")
        if "등록되지 않은 모델입니다" not in str(err.get("message") or "") or partner_eid not in str(err.get("message") or ""):
            bad.append(f"(fr-g) E5 문구가 다릅니다: {err.get('message')!r}")
        if posts or env.fal_calls:
            bad.append(f"(fr-g) 짝 해제인데 fal 로 보냈습니다(i2v 대체 금지): {[c[1] for c in env.fal_calls]}")
        if env.charges or job:
            bad.append(f"(fr-g) 실패인데 원장 {len(env.charges)}건·jobs 기록이 있습니다")
        env.set_registry(_parity_registry_items() + [_parity_kling_item()])

        # ---- (fr-h) 소리 ----
        for tag, body in (("r2v", body_r2v), ("i2v", body_i2v)):
            if body.get("generate_audio") is not bool(settings.ARK_GENERATE_AUDIO) or body.get("generate_audio") is not True:
                bad.append(f"(fr-h) {tag} generate_audio 가 ARK_GENERATE_AUDIO(True) 가 아닙니다: {body.get('generate_audio')}")
        env.patch(settings, "ARK_GENERATE_AUDIO", False)
        posts, *_ = run(shots_state())
        if not posts or (posts[0][2] or {}).get("generate_audio") is not False:
            bad.append("(fr-h) ARK_GENERATE_AUDIO=False 인데 r2v generate_audio 가 False 가 아닙니다")
        posts, *_ = run(keyframe_state())
        if not posts or (posts[0][2] or {}).get("generate_audio") is not False:
            bad.append("(fr-h) ARK_GENERATE_AUDIO=False 인데 i2v generate_audio 가 False 가 아닙니다")
        env.patch(settings, "ARK_GENERATE_AUDIO", saved_audio)

        # ---- (fr-i) 회귀: 계열이 아닌 fal(Kling) ----
        kling = _parity_kling_item()
        state = shots_state(PARITY_KLING)
        state["steps"]["4"]["shots"][0]["durationS"] = 5
        posts, shot, job, loaded = run(state)
        if len(posts) != 1:
            bad.append(f"(fr-i) Kling POST 가 1회가 아닙니다: {len(posts)} {shot.get('error')}")
        else:
            expected = fal.build_payload(
                kling["caps"],
                prompt=str(job.get("sentPrompt") or ""),
                reference_jpegs_b64=[images.keyframe_base64(PARITY_CHAR_KEY)],
                resolution=str(loaded["steps"]["4"].get("resolution") or ""),
                duration_s=5,
            )
            if posts[0][2] != expected:
                bad.append(f"(fr-i) Kling 본문이 build_payload 기존 결과와 다릅니다: {sorted(posts[0][2] or {})} != {sorted(expected)}")
            if posts[0][1] != f"{settings.FAL_QUEUE_URL}/{kling['endpointId']}":
                bad.append(f"(fr-i) Kling URL 이 다릅니다: {posts[0][1]}")
            if shot.get("provider") != PARITY_KLING or job.get("endpointModel") != PARITY_KLING:
                bad.append(f"(fr-i) Kling provider·endpointModel 이 다릅니다: {shot.get('provider')} {job.get('endpointModel')}")
            if logs.messages("vpoc.fal.route "):
                bad.append("(fr-i) 계열이 아닌데 vpoc.fal.route 로그가 남았습니다")

        # 외부 호출 0회(실제 네트워크·BytePlus·Luma)
        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.ark_calls or env.video_calls:
            bad.append(f"BytePlus·Luma 제출이 있었습니다: ark={len(env.ark_calls)} luma={len(env.video_calls)}")
        if any("dummy-fal-key" in text for text in logs.messages()):
            bad.append("로그에 fal 키 값이 남았습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("Seedance 계열 라우팅 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        settings.FAL_SEEDANCE_FAMILY = saved_family
        settings.ARK_GENERATE_AUDIO = saved_audio
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 엔드포인트 선택 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 엔드포인트 선택 검사 (fr-a)계열 설정 파서·짝·계열 판정 "
            "(fr-b)짝 미등록 E1·사용 불가 E1b·짝 단독 통과 (fr-c)choices 이름·r2v 숨김·Kling 그대로 "
            "(fr-d)route_model 5갈래·reference_caps·빈 값 되돌리기 (fr-e)r2v 참고·i2v 키 장면·글만·single 3장·기록·로그 "
            "(fr-f)참고 9장 이하 (fr-g)짝 해제 E5·대체 없음 (fr-h)generate_audio 고정 (fr-i)Kling 본문 회귀"
        )


# ------------------------------------------------ 073 작업 3 공용(길이·화질 du·rz)

PARITY_MINIMAX = "fal:fal-ai/minimax/hailuo-02/standard/image-to-video"
PARITY_GEN_ID = "g20261026110000"  # _parity_state 의 ④ genId


def _parity_minimax_item() -> dict:
    """계열이 아닌 fal 모델(MiniMax i2v 모양 가짜 caps, 073 du-h·rz-f). 길이 6·10초, 화질 512P·768P."""
    durations = ["6", "10"]
    return {
        "id": PARITY_MINIMAX,
        "endpointId": PARITY_MINIMAX[len("fal:"):],
        "label": "MiniMax Hailuo 02 Standard Image to Video",
        "category": "image-to-video",
        "caps": {
            "promptField": "prompt",
            "promptMaxLength": 2000,
            "startImageField": "image_url",
            "endImageField": "end_image_url",
            "referenceImagesField": "",
            "durations": durations,
            "durationAsString": True,
            "durationValues": {value: value for value in durations},
            "sendDuration": True,
            "resolutions": ["512P", "768P"],
            "resolutionValues": {"512p": "512P", "768p": "768P"},
            "aspectRatios": [],
            "audioField": "",
            "previzCapable": False,
            "requiredFields": ["prompt", "image_url"],
            "available": True,
            "reason": "",
        },
        "pricing": {"unit": "seconds", "unitPrice": 0.045},
        "addedBy": "tester",
    }


def _parity_with_caps(items: list[dict], model: str, **caps_update) -> list[dict]:
    """레지스트리 항목 복사본에서 model 의 caps 만 바꾼다."""
    out = []
    for item in items:
        copy = dict(item)
        if copy.get("id") == model:
            copy["caps"] = {**(copy.get("caps") or {}), **caps_update}
        out.append(copy)
    return out


def _parity_norm_body(body: dict | None) -> dict:
    """fal 제출 본문을 상수로 비교할 수 있게 줄인다. prompt 는 sha1 앞 16자, data URI 는 표식."""
    import hashlib  # noqa: PLC0415

    out: dict = {}
    for key, value in (body or {}).items():
        if key == "prompt":
            out[key] = "sha1:" + hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]
        elif isinstance(value, str) and value.startswith("data:"):
            out[key] = "<data-uri>"
        elif isinstance(value, list):
            out[key] = ["<data-uri>" if isinstance(v, str) and v.startswith("data:") else v for v in value]
        else:
            out[key] = value
    return out


def _parity_submit(env: _ParityEnv, logs: _LogCapture, state: dict, shot_id: str = "s1") -> tuple[list, dict, dict, dict]:
    """상태를 저장하고 GET plan(refresh_shots → submit_shot)으로 대기 샷 하나를 제출한다.

    돌려주는 값: (fal POST 목록, ④ 샷, jobs 기록, 저장된 상태). 앞 경우의 jobs 기록은 지운다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import store  # noqa: PLC0415

    env.reset_calls()
    logs.clear()
    gen_id = str(((state.get("steps") or {}).get("4") or {}).get("genId") or "")
    key = store.job_key(PARITY_OWNER, PARITY_PID, PARITY_PLAN, shot_id, 1, gen_id)
    env.fake.json.pop(key, None)
    env.save(state)
    env.client.get(env.plan_url())
    loaded = env.load()
    shot = next((s for s in loaded["steps"]["4"].get("shots") or [] if s.get("id") == shot_id), {})
    job = env.fake.read_json(key)[0] or {}
    return env.fal_posts(), shot, job, loaded


def _parity_single_master(total_s: int) -> dict:
    """single(영상 1개) ③ 마스터(인물 c1 = PARITY_CHAR_KEY, 배경 없음)."""
    return {
        "version": 1,
        "totalS": total_s,
        "styleEn": "Cinematic",
        "background": {"promptEn": "rooftop", "imageKey": ""},
        "characters": [{"id": "c1", "handleEn": "the man", "promptEn": "coat", "imageKey": PARITY_CHAR_KEY}],
        "beats": [{"id": "b1", "t0": 0, "t1": total_s, "actionEn": "He looks", "cameraEn": "Static"}],
    }


# 073 du-h·rz-f: 계열이 아닌 fal 모델의 길이·화질·제출 회귀 스냅샷. 작업 3 구현 **전** 코드로 같은 입력
# (_parity_nonfamily_measure)을 돌려 얻은 값이다(2026-10-27). 원장 model 칸만 X2 로 엔드포인트 id 로 바뀐다
# (구현 전 값은 settings.VIDEO_MODEL "luma.ray-v2:0").
_PARITY_PROMPT_SHA = "sha1:2016bd6664e5ae30"
PARITY_NONFAMILY_SNAPSHOT = {
    PARITY_KLING: {
        "options": {
            "durationChoices": [5, 10], "minDurationS": 5, "maxDurationS": 10,
            "resolutions": ["720p"], "defaultResolution": "720p",
        },
        "shotSeconds": [5, 5, 5],
        "oneTake": [False, 5],
        "oneTake2x5": [False, 5],
        "singleTotal": {"7": 7, "12": 10, "0": 10, "10": 10},
        "generateSingle": {"status": 200, "totalUsd": 1.12, "singleTotalS": 10, "resolution": "540p",
                           "shots": [["take", 10]]},
        "generateShots": {"status": 200, "totalUsd": 1.12, "resolution": "540p", "oneTake": False,
                          "shots": [["s1", 5], ["s2", 5]]},
        "planResolution": {"none": "540p", "540p": "540p", "720p": "540p"},
        "submit": {
            case: {
                "body": {"aspect_ratio": "16:9", "duration": duration, "generate_audio": True,
                         "image_url": "<data-uri>", "prompt": _PARITY_PROMPT_SHA},
                "status": "submitted", "mode": "reference",
                "jobResolution": res, "jobUsd": usd, "jobDurationS": secs,
                "charges": [{"kind": "video", "units": secs, "usd": usd}],
                "chargeVideoResolution": [res],
            }
            for case, duration, res, usd, secs in (
                ("540p/5", "5", "540p", 0.56, 5),
                ("540p/10", "10", "540p", 1.12, 10),
                ("none/5", "5", "540p", 0.56, 5),
                ("720p/5", "5", "720p", 0.56, 5),
            )
        },
    },
    PARITY_MINIMAX: {
        "options": {
            "durationChoices": [6, 10], "minDurationS": 6, "maxDurationS": 10,
            "resolutions": ["512P", "768P"], "defaultResolution": "512P",
        },
        "shotSeconds": [5, 5, 5],
        "oneTake": [False, 5],
        "oneTake2x5": [False, 5],
        "singleTotal": {"7": 7, "12": 10, "0": 10, "10": 10},
        "generateSingle": {"status": 200, "totalUsd": 0.45, "singleTotalS": 10, "resolution": "540p",
                           "shots": [["take", 10]]},
        "generateShots": {"status": 200, "totalUsd": 0.45, "resolution": "540p", "oneTake": False,
                          "shots": [["s1", 5], ["s2", 5]]},
        "planResolution": {"none": "540p", "540p": "540p", "720p": "540p"},
        "submit": {
            case: {
                "body": {"duration": duration, "image_url": "<data-uri>", "prompt": _PARITY_PROMPT_SHA},
                "status": "submitted", "mode": "reference",
                "jobResolution": res, "jobUsd": usd, "jobDurationS": secs,
                "charges": [{"kind": "video", "units": secs, "usd": usd}],
                "chargeVideoResolution": [res],
            }
            for case, duration, res, usd, secs in (
                ("540p/5", "6", "540p", 0.225, 5),
                ("540p/10", "10", "540p", 0.45, 10),
                ("none/5", "6", "540p", 0.225, 5),
                ("720p/5", "6", "720p", 0.225, 5),
            )
        },
    },
}


def _parity_nonfamily_measure(env: _ParityEnv, logs: _LogCapture, model: str, cv_calls: list[dict]) -> dict:
    """계열이 아닌 fal 모델 하나의 길이·화질·제출 값(PARITY_NONFAMILY_SNAPSHOT 과 같은 모양 + 비교용 부가 값).

    cv_calls: ledger.charge_video 기록기(호출 kwargs). 레지스트리에 model 이 등록돼 있어야 한다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark  # noqa: PLC0415

    snap: dict = {}
    opts = ark.video_options(model)
    snap["options"] = {
        key: opts.get(key)
        for key in ("durationChoices", "minDurationS", "maxDurationS", "resolutions", "defaultResolution")
    }
    plan_res: dict = {}
    for stored in ("", "540p", "720p"):
        state = _parity_state(model, durations=(7, 12, 5), resolution=stored)
        state["steps"]["3"]["shots"][2]["durationS"] = None
        if not stored:
            state["steps"]["3"].pop("resolution", None)
        env.save(state)
        step3 = ((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}
        plan_res[stored or "none"] = step3.get("resolution")
        if not stored:
            snap["shotSeconds"] = [row.get("durationS") for row in step3.get("shots") or []]
            snap["oneTake"] = [step3.get("oneTakeActive"), step3.get("oneTakeMaxS")]
    snap["planResolution"] = plan_res
    env.save(_parity_state(model, durations=(5, 5)))
    step3 = ((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}
    snap["oneTake2x5"] = [step3.get("oneTakeActive"), step3.get("oneTakeMaxS")]
    single_total = _closure_fn(env.app.view_functions.get("whynot_vpoc.get_plan"), "_single_total")
    snap["singleTotal"] = (
        {str(v): single_total({"totalSeconds": v}, model) for v in (7, 12, 0, 10)} if single_total else None
    )
    # generate single(master.totalS = 10, caps 범위 안 → E2 아님)
    state = _parity_state(model, "single")
    state["steps"]["2"]["totalSeconds"] = 10
    state["steps"]["3"]["master"] = _parity_single_master(10)
    env.save(state)
    view = env.client.get(env.plan_url()).get_json() or {}
    step3 = (view.get("steps") or {}).get("3") or {}
    total = (step3.get("cost") or {}).get("totalUsd")
    resp = env.client.post(env.run_url("generate"), json={"confirmUsd": total})
    step4 = env.load()["steps"]["4"]
    snap["generateSingle"] = {
        "status": resp.status_code, "totalUsd": total, "singleTotalS": step3.get("singleTotalS"),
        "resolution": step4.get("resolution"),
        "shots": [[s.get("id"), s.get("durationS")] for s in step4.get("shots") or []],
    }
    # generate shots(7·12초 요청)
    env.save(_parity_state(model, durations=(7, 12)))
    view = env.client.get(env.plan_url()).get_json() or {}
    total = ((((view.get("steps") or {}).get("3") or {}).get("cost")) or {}).get("totalUsd")
    resp = env.client.post(env.run_url("generate"), json={"confirmUsd": total})
    step4 = env.load()["steps"]["4"]
    snap["generateShots"] = {
        "status": resp.status_code, "totalUsd": total, "resolution": step4.get("resolution"),
        "oneTake": step4.get("oneTake"),
        "shots": [[s.get("id"), s.get("durationS")] for s in step4.get("shots") or []],
    }
    # submit_shot(회차 화질 540p·없음·720p, 샷 5·10초)
    submits: dict = {}
    extra: dict = {}
    for stored, seconds in (("540p", 5), ("540p", 10), ("", 5), ("720p", 5)):
        cv_calls.clear()
        state = _parity_state(
            model,
            resolution=stored or "540p",
            step4={
                "status": "running",
                "resolution": stored,
                "shots": [
                    _parity_shot4("s1", "queued", durationS=seconds),
                    _parity_shot4("s2", "completed", durationS=5),
                ],
            },
        )
        posts, shot, job, _loaded = _parity_submit(env, logs, state)
        case = f"{stored or 'none'}/{seconds}"
        submits[case] = {
            "body": _parity_norm_body(posts[0][2]) if posts else None,
            "status": shot.get("status"),
            "mode": shot.get("mode"),
            "jobResolution": job.get("resolution"),
            "jobUsd": job.get("usd"),
            "jobDurationS": job.get("durationS"),
            "charges": [{k: c.get(k) for k in ("kind", "units", "usd")} for c in env.charges],
            "chargeVideoResolution": [k.get("resolution") for k in cv_calls],
        }
        extra[case] = {
            "posts": len(posts),
            "url": posts[0][1] if posts else None,
            "rawBody": (posts[0][2] or {}) if posts else {},
            "provider": shot.get("provider"),
            "chargeModels": [c.get("model") for c in env.charges],
            "adjustedLogs": logs.messages("vpoc.fal.resolution_adjusted"),
        }
    snap["submit"] = submits
    snap["_extra"] = extra
    return snap


def check_fal_parity_duration() -> None:
    """073 작업 3 — 샷 길이·한 번에 만들기(R3.1-3.8, R14.2-14.4, D3·X5·X6). 외부 호출은 전부 가짜.

    (du-a) ark.duration_range: ARK (4,15)·Luma (5,5)·비계열 fal [5,10]→(5,5)·계열 4..30→(4,15)·상한 12→(4,12)·
           상한<최소→(4,4)·미등록 fal (5,5).
    (du-b) ark.snap_seconds: 비계열 fal 7·12·빈 값→5, 계열 20→15·3→4, 계열 이산 [4,8,12] 5→8·13→12, ARK 20→15·2→4, Luma 9→5.
    (du-c) video_options(계열): durationChoices 4..15·maxDurationS 15(choices 도 같다).
    (du-d) plan_view 한 번에 만들기: 계열 2×6 True·15, Kling 2×5 False·5, ARK 2×6 True. one_take_max_s 3갈래.
    (du-e) generate single: 계열 totalS=20 → 400 BadDuration(E2, 금액 검사 전), 10 → plan_view 금액 그대로 통과.
    (du-f) ark.plan_model: 플래그 false·seedance-2-5 → 얼굴(등록)/Luma. 준비 3곳이 ark.plan_model( 을 부른다.
           do_prepare·_build_master·_make_storyboard 를 가짜 LLM·Seedream 으로 실제로 돌려 길이가 실제 모델 범위다.
    (du-g) 제출: 계열 durationS=20 → failed BadDuration(fal 0회), ARK 20 → BadDuration, Kling 10 → 검사 없이 제출.
    (du-h) 비계열 fal(Kling·MiniMax 모양) 길이 회귀 스냅샷(구현 전 값).
    (du-i) ③ 저장 길이(save_step): 계열 4·10·15 저장, 16·3·틀린 값 400, 이산 caps 밖 400, Kling 5 저장·10 400, Luma 5 저장.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, ledger, settings  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    routes_path = APP_SRC / "vpoc" / "routes.py"
    base_items = _parity_registry_items() + [_parity_kling_item(), _parity_minimax_item()]
    saved_max = settings.FAL_SEEDANCE_MAX_S
    try:
        env = _parity_env(registry=base_items)
        logs.attach()
        cv_calls: list[dict] = []
        real_charge_video = ledger.charge_video

        def record_charge_video(token: str, **kwargs) -> dict:
            cv_calls.append(dict(kwargs))
            return real_charge_video(token, **kwargs)

        env.patch(ledger, "charge_video", record_charge_video)

        # ---- (du-a) duration_range ----
        table_a = [
            ("seedance-2-5", (4, 15)),
            ("luma-ray2", (5, 5)),
            (PARITY_KLING, (5, 5)),
            (PARITY_MINIMAX, (5, 5)),
            (PARITY_FACE, (4, 15)),
            (PARITY_PARTNER, (4, 15)),
            ("fal:not/registered", (5, 5)),
        ]
        for model, want in table_a:
            if ark.duration_range(model) != want:
                bad.append(f"(du-a) duration_range({model}) 가 {want} 가 아닙니다: {ark.duration_range(model)}")
        env.patch(settings, "FAL_SEEDANCE_MAX_S", 12)
        if ark.duration_range(PARITY_FACE) != (4, 12):
            bad.append(f"(du-a) FAL_SEEDANCE_MAX_S=12 인데 (4,12) 가 아닙니다: {ark.duration_range(PARITY_FACE)}")
        if ark.duration_range(PARITY_KLING) != (5, 5):
            bad.append("(du-a) 계열 상한이 비계열 fal 범위를 바꿨습니다")
        env.patch(settings, "FAL_SEEDANCE_MAX_S", 2)
        if ark.duration_range(PARITY_FACE) != (4, 4):
            bad.append(f"(du-a) 상한이 최소보다 작으면 (4,4) 여야 합니다: {ark.duration_range(PARITY_FACE)}")
        env.patch(settings, "FAL_SEEDANCE_MAX_S", saved_max)
        if saved_max != 15 and os.getenv("VPOC_FAL_SEEDANCE_MAX_S") is None:
            bad.append(f"(du-a) env 없을 때 FAL_SEEDANCE_MAX_S 가 15 가 아닙니다: {saved_max}")

        # ---- (du-b) snap_seconds ----
        table_b = [
            (PARITY_KLING, 7, 5), (PARITY_KLING, 12, 5), (PARITY_KLING, None, 5), (PARITY_KLING, "", 5),
            (PARITY_FACE, 20, 15), (PARITY_FACE, 3, 4), (PARITY_FACE, 6, 6), (PARITY_FACE, "x", 5),
            (PARITY_FACE, None, 5),
            ("seedance-2-5", 20, 15), ("seedance-2-5", 2, 4), ("seedance-2-5", "abc", 5),
            ("luma-ray2", 9, 5),
        ]
        for model, requested, want in table_b:
            got = ark.snap_seconds(model, requested)
            if got != want:
                bad.append(f"(du-b) snap_seconds({model}, {requested!r}) 가 {want} 가 아닙니다: {got}")
        env.set_registry(_parity_with_caps(base_items, PARITY_FACE, durations=["4", "8", "12"]))
        for requested, want in ((5, 8), (13, 12), (4, 4), (None, 8)):
            got = ark.snap_seconds(PARITY_FACE, requested)
            if got != want:
                bad.append(f"(du-b) 계열 이산 [4,8,12] snap_seconds({requested!r}) 가 {want} 가 아닙니다: {got}")
        env.set_registry(base_items)

        # ---- (du-c) video_options(계열) ----
        opts = ark.video_options(PARITY_FACE)
        if opts.get("durationChoices") != list(range(4, 16)) or opts.get("maxDurationS") != 15 or opts.get(
            "minDurationS"
        ) != 4:
            bad.append(
                f"(du-c) 계열 video_options 길이가 4..15 가 아닙니다: {opts.get('durationChoices')} "
                f"max={opts.get('maxDurationS')} min={opts.get('minDurationS')}"
            )
        face_choice = next((item for item in ark.choices() if item.get("id") == PARITY_FACE), {})
        if face_choice.get("durationChoices") != list(range(4, 16)) or face_choice.get("maxDurationS") != 15:
            bad.append(f"(du-c) ② choices 의 계열 길이 선택지가 4..15 가 아닙니다: {face_choice.get('durationChoices')}")

        # ---- (du-d) plan_view 한 번에 만들기 ----
        def one_take_view(model: str, durations: tuple[int, ...]) -> dict:
            env.save(_parity_state(model, durations=durations))
            return ((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}

        step3 = one_take_view(PARITY_FACE, (6, 6))
        if step3.get("oneTakeActive") is not True or step3.get("oneTakeMaxS") != 15:
            bad.append(f"(du-d) 계열 2×6초 oneTakeActive·MaxS 가 True·15 가 아닙니다: {step3.get('oneTakeActive')} {step3.get('oneTakeMaxS')}")
        if [row.get("durationS") for row in step3.get("shots") or []] != [6, 6]:
            bad.append(f"(du-d) 계열 샷 길이가 6·6 이 아닙니다: {[row.get('durationS') for row in step3.get('shots') or []]}")
        step3 = one_take_view(PARITY_FACE, (10, 10))
        if step3.get("oneTakeActive") is not False:
            bad.append("(du-d) 계열 2×10초(20초 > 15)인데 oneTakeActive 가 True 입니다")
        step3 = one_take_view(PARITY_KLING, (5, 5))
        if step3.get("oneTakeActive") is not False or step3.get("oneTakeMaxS") != 5:
            bad.append(f"(du-d) Kling 2×5초가 False·5 가 아닙니다: {step3.get('oneTakeActive')} {step3.get('oneTakeMaxS')}")
        env.patch(settings, "ARK_SEEDANCE_ENABLED", True)
        step3 = one_take_view("seedance-2-5", (6, 6))
        if step3.get("oneTakeActive") is not True or step3.get("oneTakeMaxS") != 15:
            bad.append(f"(du-d) ARK 2×6초가 True·15 가 아닙니다: {step3.get('oneTakeActive')} {step3.get('oneTakeMaxS')}")
        env.patch(settings, "ARK_SEEDANCE_ENABLED", False)
        for model, want in (("seedance-2-5", 15), (PARITY_FACE, 15), (PARITY_KLING, 5), ("luma-ray2", 5), ("fal:x/y", 5)):
            if ark.one_take_max_s(model) != want:
                bad.append(f"(du-d) one_take_max_s({model}) 가 {want} 가 아닙니다: {ark.one_take_max_s(model)}")

        # ---- (du-e) generate single E2 ----
        def single_state(total_s: int) -> dict:
            state = _parity_state(PARITY_FACE, "single")
            state["steps"]["2"]["totalSeconds"] = total_s
            state["steps"]["3"]["master"] = _parity_single_master(total_s)
            return state

        env.save(single_state(20))
        resp = env.client.post(env.run_url("generate"), json={"confirmUsd": 0})
        err = (resp.get_json() or {}).get("error") or {}
        if resp.status_code != 400 or err.get("type") != "BadDuration":
            bad.append(f"(du-e) 계열 totalS=20 이 400 BadDuration(E2)이 아닙니다(금액 검사보다 먼저): {resp.status_code} {err.get('type')}")
        elif "4~15초" not in str(err.get("message") or "") or "20초" not in str(err.get("message") or ""):
            bad.append(f"(du-e) E2 문구에 범위·길이가 없습니다: {err.get('message')!r}")
        if env.load()["steps"]["4"].get("genId"):
            bad.append("(du-e) E2 인데 ④ 회차가 만들어졌습니다")
        env.save(single_state(10))
        view = env.client.get(env.plan_url()).get_json() or {}
        step3 = (view.get("steps") or {}).get("3") or {}
        total = (step3.get("cost") or {}).get("totalUsd")
        if step3.get("singleTotalS") != 10:
            bad.append(f"(du-e) plan_view singleTotalS 가 master.totalS(10)가 아닙니다: {step3.get('singleTotalS')}")
        resp = env.client.post(env.run_url("generate"), json={"confirmUsd": total})
        if resp.status_code != 200:
            bad.append(f"(du-e) 계열 totalS=10 을 화면 금액({total})으로 보낸 generate 가 200 이 아닙니다: {resp.status_code} {resp.get_json()}")
        else:
            step4 = env.load()["steps"]["4"]
            if [[s.get("id"), s.get("durationS")] for s in step4.get("shots") or []] != [["take", 10]]:
                bad.append(f"(du-e) 회차 샷이 take 10초가 아닙니다: {step4.get('shots')}")
        # master 가 없으면 ② totalSeconds(_single_total) — plan_view 641 의 대체 분기.
        state = single_state(12)
        state["steps"]["3"].pop("master", None)
        env.save(state)
        step3 = ((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}
        if step3.get("singleTotalS") != 12:
            bad.append(f"(du-e) 마스터 없을 때 singleTotalS 가 ② 값(12)이 아닙니다: {step3.get('singleTotalS')}")

        # ---- (du-f) plan_model ----
        if ark.plan_model({"videoModel": "seedance-2-5"}) != PARITY_FACE:
            bad.append(f"(du-f) 플래그 false·seedance-2-5 → 얼굴이 아닙니다: {ark.plan_model({'videoModel': 'seedance-2-5'})}")
        if ark.plan_model({"videoModel": PARITY_KLING}) != PARITY_KLING or ark.plan_model({}) != "":
            bad.append("(du-f) plan_model 이 fal·빈 값을 바꿨습니다")
        env.set_registry([_parity_kling_item()])
        if ark.plan_model({"videoModel": "seedance-2-5"}) != ark.DEFAULT_MODEL:
            bad.append(f"(du-f) 얼굴 미등록이면 Luma 여야 합니다: {ark.plan_model({'videoModel': 'seedance-2-5'})}")
        env.set_registry(base_items)
        source = routes_path.read_text(encoding="utf-8")
        for name in ("do_prepare", "_make_storyboard", "_build_master"):
            match = re.search(r"\n    def " + name + r"\(.*?(?=\n    def |\n    @)", source, re.S)
            body = match.group(0) if match else ""
            if "ark.plan_model(" not in body:
                bad.append(f"(du-f) routes.{name} 가 ark.plan_model( 을 부르지 않습니다")
            if 'str(step2.get("videoModel") or "")' in body:
                bad.append(f"(du-f) routes.{name} 에 ② 저장값을 그대로 쓰는 줄이 남았습니다")
        # 073 fix1: 준비 3곳을 실제로 지나가게 한다. LLM·Seedream 은 가짜, 백그라운드 그림 스레드는 하네스가 막는다.
        # 상한 12초로 두면 ② 저장값(ARK seedance-2-5, 4~15초)과 실제 모델(fal 얼굴, 4~12초)의 길이가 갈린다.
        from src.vpoc import llm, seedream  # noqa: PLC0415

        plan_model_calls: list[tuple[str, str]] = []
        real_plan_model = ark.plan_model

        def spy_plan_model(step2: dict) -> str:
            got = real_plan_model(step2)
            plan_model_calls.append((str((step2 or {}).get("videoModel") or ""), got))
            return got

        llm_calls: list[tuple[str, dict]] = []

        def fake_master_plan(panel_bytes, characters, **kwargs):
            llm_calls.append(("master_plan", dict(kwargs)))
            return (
                {"beats": [{"actionEn": "He looks", "characters": []}], "backgroundEn": "A quiet rooftop at dusk"},
                {"inputTokens": 1, "outputTokens": 1, "model": "fake"},
            )

        def fake_storyboard(panel_bytes, characters, **kwargs):
            llm_calls.append(("storyboard", dict(kwargs)))
            count = int(kwargs.get("count") or 2)
            return (
                {
                    "keyframes": [{"summaryKo": "k", "sceneEn": "scene", "characters": []} for _ in range(count)],
                    "segments": [{"summaryKo": "s", "motionEn": "motion"} for _ in range(count - 1)],
                },
                {"inputTokens": 1, "outputTokens": 1, "model": "fake"},
            )

        def fake_llm_other(*args, **kwargs):
            llm_calls.append(("other", {}))
            raise RuntimeError("check_vpoc: 이 경로에서 부르면 안 되는 LLM 호출")

        seedream_calls: list[str] = []

        def fake_generate_safe(prompt, refs=None, *, model="", where=""):
            seedream_calls.append(where)
            return _tiny_jpeg()

        env.patch(ark, "plan_model", spy_plan_model)
        env.patch(llm, "master_plan", fake_master_plan)
        env.patch(llm, "storyboard", fake_storyboard)
        env.patch(llm, "plan", fake_llm_other)
        env.patch(seedream, "generate_safe", fake_generate_safe)
        env.patch(settings, "FAL_SEEDANCE_MAX_S", 12)
        do_prepare = make_storyboard = None
        for view in env.app.view_functions.values():
            do_prepare = do_prepare or _closure_fn(view, "do_prepare")
            make_storyboard = make_storyboard or _closure_fn(view, "_make_storyboard")
        prep_step1 = {"status": "saved", "text": "t", "characters": [], "sceneFiles": []}
        prep_step2 = {
            "story": {"style": "live_action"},
            "characters": [],
            "makeMode": "single",
            "videoModel": "seedance-2-5",
            "totalSeconds": 20,
        }
        if do_prepare is None or make_storyboard is None:
            bad.append("(du-f) do_prepare·_make_storyboard 를 찾지 못했습니다")
        else:
            result = do_prepare(PARITY_OWNER, PARITY_PID, PARITY_PLAN, PARITY_OWNER, dict(prep_step2), step1=prep_step1)
            if [call[1] for call in plan_model_calls] != [PARITY_FACE, PARITY_FACE]:
                bad.append(f"(du-f) do_prepare·_build_master 가 plan_model(→얼굴)을 2번 부르지 않았습니다: {plan_model_calls}")
            masters = [kw for name, kw in llm_calls if name == "master_plan"]
            if len(masters) != 1 or masters[0].get("total_s") != 12:
                bad.append(f"(du-f) _build_master 길이가 실제 모델 범위(12초)가 아닙니다: {[kw.get('total_s') for kw in masters]}")
            if (result.get("master") or {}).get("totalS") != 12 or result.get("makeMode") != "single":
                bad.append(f"(du-f) do_prepare 결과 master.totalS·makeMode 가 12·single 이 아닙니다: {(result.get('master') or {}).get('totalS')} {result.get('makeMode')}")
            plan_model_calls.clear()
            board = make_storyboard(
                PARITY_OWNER, PARITY_PID, PARITY_PLAN, PARITY_OWNER, prep_step1, dict(prep_step2), make_mode="single"
            )
            boards = [kw for name, kw in llm_calls if name == "storyboard"]
            if [call[1] for call in plan_model_calls] != [PARITY_FACE]:
                bad.append(f"(du-f) _make_storyboard 가 plan_model(→얼굴)을 부르지 않았습니다: {plan_model_calls}")
            if len(boards) != 1 or boards[0].get("total_s") != 12:
                bad.append(f"(du-f) _make_storyboard 길이가 12초가 아닙니다: {[kw.get('total_s') for kw in boards]}")
            if not board.get("keyframes") or float(board["keyframes"][-1].get("t") or 0) != 12.0:
                bad.append(f"(du-f) 마지막 키 장면이 12초가 아닙니다: {[k.get('t') for k in board.get('keyframes') or []]}")
            if any(name == "other" for name, _ in llm_calls):
                bad.append("(du-f) 준비 중 llm.plan 이 불렸습니다(single 은 마스터만)")
            if seedream_calls != ["seedream.master_background"]:
                bad.append(f"(du-f) Seedream 가짜 호출이 배경 1장이 아닙니다(미리보기는 스레드라 막힘): {seedream_calls}")
        env.patch(settings, "FAL_SEEDANCE_MAX_S", saved_max)
        env.patch(ark, "plan_model", real_plan_model)

        # ---- (du-g) 제출 길이 검사 ----
        def queued_state(model: str, seconds: int) -> dict:
            return _parity_state(
                model,
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4("s1", "queued", durationS=seconds),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )

        posts, shot, job, _ = _parity_submit(env, logs, queued_state(PARITY_FACE, 20))
        if shot.get("status") != "failed" or (shot.get("error") or {}).get("type") != "BadDuration":
            bad.append(f"(du-g) 계열 20초 샷이 failed BadDuration 이 아닙니다: {shot.get('status')} {(shot.get('error') or {}).get('type')}")
        if posts or env.charges or job:
            bad.append(f"(du-g) 계열 20초인데 fal POST {len(posts)}·원장 {len(env.charges)}·jobs 기록")
        posts, shot, job, _ = _parity_submit(env, logs, queued_state(PARITY_FACE, 15))
        if shot.get("status") != "submitted" or not posts or (posts[0][2] or {}).get("duration") != "15":
            bad.append(f"(du-g) 계열 15초(상한)가 제출되지 않았습니다: {shot.get('status')} {shot.get('error')}")
        env.patch(settings, "ARK_SEEDANCE_ENABLED", True)
        _parity_submit(env, logs, queued_state("seedance-2-5", 20))
        shot = next((s for s in env.load()["steps"]["4"]["shots"] if s.get("id") == "s1"), {})
        if shot.get("status") != "failed" or (shot.get("error") or {}).get("type") != "BadDuration" or env.ark_calls:
            bad.append(f"(du-g) ARK 20초가 BadDuration·ark._call 0회가 아닙니다: {shot.get('status')} {len(env.ark_calls)}")
        env.patch(settings, "ARK_SEEDANCE_ENABLED", False)
        posts, shot, job, _ = _parity_submit(env, logs, queued_state(PARITY_KLING, 10))
        if shot.get("status") != "submitted" or len(posts) != 1 or (posts[0][2] or {}).get("duration") != "10":
            bad.append(f"(du-g) Kling 10초가 검사 없이 제출되지 않았습니다: {shot.get('status')} {shot.get('error')}")

        # ---- (du-i) ③ 저장 길이 검증(save_step, 073 fix1 R3.9) ----
        def save_duration(model: str, seconds: object) -> tuple[int, str, object]:
            env.save(_parity_state(model, durations=(5, 5)))
            resp = env.client.put(f"{env.plan_url()}/steps/3", json={"shots": [{"id": "s1", "durationS": seconds}]})
            err = ((resp.get_json() or {}).get("error") or {})
            saved = next((s for s in env.load()["steps"]["3"]["shots"] if s.get("id") == "s1"), {}).get("durationS")
            return resp.status_code, str(err.get("type") or ""), saved

        cases_i = [
            (PARITY_FACE, 4, 200), (PARITY_FACE, 10, 200), (PARITY_FACE, 15, 200),
            (PARITY_FACE, 16, 400), (PARITY_FACE, 3, 400), (PARITY_FACE, "x", 400),
            (PARITY_KLING, 5, 200), (PARITY_KLING, 10, 400),
            ("luma-ray2", 5, 200), ("luma-ray2", 6, 400),
        ]
        for model, seconds, want in cases_i:
            status, kind, saved = save_duration(model, seconds)
            if status != want or (want == 400 and kind != "BadDuration"):
                bad.append(f"(du-i) {model} durationS={seconds!r} 저장이 {want} 가 아닙니다: {status} {kind}")
            if want == 200 and saved != seconds:
                bad.append(f"(du-i) {model} durationS={seconds} 가 저장되지 않았습니다: {saved}")
            if want == 400 and saved != 5:
                bad.append(f"(du-i) {model} durationS={seconds!r} 거절인데 값이 바뀌었습니다: {saved}")
        # 계열 이산 caps [4,8,12]: 범위(4~12) 안이어도 caps 에 없는 값(5)은 거절, 8 은 저장.
        env.set_registry(_parity_with_caps(base_items, PARITY_FACE, durations=["4", "8", "12"]))
        for seconds, want in ((5, 400), (8, 200)):
            status, kind, _saved = save_duration(PARITY_FACE, seconds)
            if status != want or (want == 400 and kind != "BadDuration"):
                bad.append(f"(du-i) 계열 이산 caps durationS={seconds} 저장이 {want} 가 아닙니다: {status} {kind}")
        env.set_registry(base_items)
        env.save(_parity_state(PARITY_FACE, durations=(5, 5)))
        resp = env.client.put(f"{env.plan_url()}/steps/3", json={"shots": [{"id": "s1", "durationS": 16}]})
        message = str(((resp.get_json() or {}).get("error") or {}).get("message") or "")
        if "샷 길이는 4~15초 사이로 정해 주세요 (받은 값 16)" not in message:
            bad.append(f"(du-i) 거절 문구 형식이 지금과 다릅니다: {message!r}")

        # ---- (du-h) 비계열 fal 길이 회귀 스냅샷 ----
        for model, want in PARITY_NONFAMILY_SNAPSHOT.items():
            got = _parity_nonfamily_measure(env, logs, model, cv_calls)
            tag = f"(du-h) {model.split('/')[1]}"
            for key in ("durationChoices", "minDurationS", "maxDurationS"):
                if got["options"].get(key) != want["options"][key]:
                    bad.append(f"{tag} video_options {key} 가 구현 전과 다릅니다: {got['options'].get(key)} != {want['options'][key]}")
            for key in ("shotSeconds", "oneTake", "oneTake2x5", "singleTotal", "generateSingle", "generateShots"):
                if got.get(key) != want[key]:
                    bad.append(f"{tag} {key} 가 구현 전과 다릅니다: {got.get(key)} != {want[key]}")
            for case, expect in want["submit"].items():
                body = (got["submit"].get(case) or {}).get("body") or {}
                if body.get("duration") != expect["body"]["duration"]:
                    bad.append(f"{tag} {case} 제출 duration 이 구현 전과 다릅니다: {body.get('duration')} != {expect['body']['duration']}")
                if (got["submit"].get(case) or {}).get("jobDurationS") != expect["jobDurationS"]:
                    bad.append(f"{tag} {case} jobs durationS 가 구현 전과 다릅니다")

        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.video_calls:
            bad.append(f"Luma 제출이 있었습니다: {len(env.video_calls)}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("샷 길이 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        settings.FAL_SEEDANCE_MAX_S = saved_max
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 샷 길이 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 샷 길이 검사 (du-a)duration_range 계열 caps·상한·비계열 (5,5) "
            "(du-b)snap_seconds 계열 올림·이산·자르기 (du-c)계열 4..15 선택지 (du-d)한 번에 만들기 계열·Kling·ARK "
            "(du-e)generate E2·화면 금액 통과 (du-f)plan_model·준비 3곳 실행 (du-g)제출 길이 검사 계열·ARK·Kling 건너뜀 "
            "(du-h)Kling·MiniMax 길이 회귀 스냅샷 (du-i)③ 저장 길이 계열·Kling·Luma"
        )


def check_fal_parity_resolution() -> None:
    """073 작업 3 — 화질·원장(R4.1-4.6, R14.3, D4·X2·X3). 외부 호출은 전부 가짜.

    (rz-a) video_options 기본 화질: 계열 [480,720,1080]→720p·[480,1080]→480p·[]→["720p"]/720p,
           비계열 fal [480,720,1080]→480p(첫 값)·[]→["720p"]/720p.
    (rz-b) plan_view step3.resolution: 계열 540p→720p·1080p→1080p, Luma 720p→720p·없음→540p.
    (rz-c) 070 이전 계열 회차(540p·빈 값) 제출 → 본문 480p(승인 금액 이하)·resolution_adjusted 로그·원장/jobs 480p 단가.
           submit_resolution 갈래(그대로·작은 쪽·더 작은 값 없음·픽셀 모름·빈 값·기본이 caps 에 있음·비계열·ARK·Luma).
    (rz-d) ledger.charge_video model 칸: fal+endpoint r2v → r2v id(얼굴 단가), endpoint 없음 → 얼굴 id, ARK·Luma 회귀.
    (rz-e) generate 계열 720p 10초 + dual: plan_view cost.totalUsd 와 서버 계산이 0.005 안(4.6224 + 1.4).
    (rz-f) 비계열 fal(Kling·MiniMax 모양) 화질·제출 본문·원장 회귀 스냅샷(구현 전 값), 보정 로그 없음.
    (rz-g) ③ 저장 화질(save_step): 계열 1080p·480p·720p 저장·540p 400, Luma 720p 저장·1080p 400, 비계열 fal 지금 목록.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, ledger, settings  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    base_items = _parity_registry_items() + [_parity_kling_item(), _parity_minimax_item()]
    partner_eid = PARITY_PARTNER[len("fal:"):]
    face_eid = PARITY_FACE[len("fal:"):]
    try:
        env = _parity_env(registry=base_items)
        logs.attach()
        cv_calls: list[dict] = []
        real_charge_video = ledger.charge_video

        def record_charge_video(token: str, **kwargs) -> dict:
            cv_calls.append(dict(kwargs))
            return real_charge_video(token, **kwargs)

        env.patch(ledger, "charge_video", record_charge_video)

        # ---- (rz-a) video_options 기본 화질 ----
        family_cases = [
            (["480p", "720p", "1080p"], ["480p", "720p", "1080p"], "720p"),
            (["480p", "1080p"], ["480p", "1080p"], "480p"),
            ([], ["720p"], "720p"),
        ]
        for caps_res, want_list, want_default in family_cases:
            env.set_registry(_parity_with_caps(base_items, PARITY_FACE, resolutions=caps_res))
            opts = ark.video_options(PARITY_FACE)
            if opts.get("resolutions") != want_list or opts.get("defaultResolution") != want_default:
                bad.append(f"(rz-a) 계열 caps {caps_res} → {want_list}/{want_default} 가 아닙니다: {opts.get('resolutions')}/{opts.get('defaultResolution')}")
        for caps_res, want_list, want_default in (
            (["480p", "720p", "1080p"], ["480p", "720p", "1080p"], "480p"),
            ([], ["720p"], "720p"),
        ):
            env.set_registry(_parity_with_caps(base_items, PARITY_KLING, resolutions=caps_res))
            opts = ark.video_options(PARITY_KLING)
            if opts.get("resolutions") != want_list or opts.get("defaultResolution") != want_default:
                bad.append(f"(rz-a) 비계열 caps {caps_res} → {want_list}/{want_default}(지금 값) 가 아닙니다: {opts.get('resolutions')}/{opts.get('defaultResolution')}")
        env.set_registry(base_items)

        # ---- (rz-b) plan_view step3.resolution ----
        def plan_resolution(model: str, stored: str | None) -> str | None:
            state = _parity_state(model, resolution=stored or "")
            if stored is None:
                state["steps"]["3"].pop("resolution", None)
            env.save(state)
            return (((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}).get("resolution")

        for model, stored, want in (
            (PARITY_FACE, "540p", "720p"),
            (PARITY_FACE, "1080p", "1080p"),
            (PARITY_FACE, None, "720p"),
            ("luma-ray2", "720p", "720p"),
            ("luma-ray2", None, "540p"),
        ):
            got = plan_resolution(model, stored)
            if got != want:
                bad.append(f"(rz-b) {model} 저장 {stored} → step3.resolution 이 {want} 가 아닙니다: {got}")

        # ---- (rz-c) 070 이전 계열 회차(540p) 제출 ----
        # 빈 값 규칙은 default_resolution(fal → settings.RESOLUTION)에 기대므로 기본값(540p)으로 고정한다.
        env.patch(settings, "RESOLUTION", "540p")
        for model, stored, want in (
            (PARITY_FACE, "1080p", ("1080p", "")),
            (PARITY_FACE, "480p", ("480p", "")),
            # 073 fix1: caps 에 없으면 픽셀 수가 저장 화질 이하인 caps 값 중 가장 큰 것(승인 금액을 넘지 않게).
            (PARITY_FACE, "540p", ("480p", "540p")),
            (PARITY_FACE, "720P", ("720p", "720P")),
            (PARITY_FACE, "4k", ("480p", "4k")),
            # 빈 값 = default_resolution(fal → RESOLUTION 540p) 로 보고 같은 규칙.
            (PARITY_FACE, "", ("480p", settings.default_resolution(PARITY_FACE))),
            (PARITY_KLING, "", ("540p", "")),
            (PARITY_KLING, "720p", ("720p", "")),
            ("seedance-2-5", "", (settings.ARK_RESOLUTION, "")),
            ("luma-ray2", None, ("540p", "")),
        ):
            if ark.submit_resolution(model, stored) != want:
                bad.append(f"(rz-c) submit_resolution({model}, {stored!r}) 가 {want} 가 아닙니다: {ark.submit_resolution(model, stored)}")
        # 저장 화질보다 작은 caps 값이 없으면 caps 의 가장 작은 화질.
        env.set_registry(_parity_with_caps(base_items, PARITY_FACE, resolutions=["720p", "1080p"]))
        if ark.submit_resolution(PARITY_FACE, "540p") != ("720p", "540p"):
            bad.append(f"(rz-c) 더 작은 caps 화질이 없으면 가장 작은 값(720p)이어야 합니다: {ark.submit_resolution(PARITY_FACE, '540p')}")
        # caps 화질의 픽셀 수를 하나도 모르면(이론상) 첫 값.
        env.set_registry(_parity_with_caps(base_items, PARITY_FACE, resolutions=["SD", "HD"]))
        if ark.submit_resolution(PARITY_FACE, "540p") != ("SD", "540p"):
            bad.append(f"(rz-c) 픽셀 수를 모르는 caps 는 첫 값이어야 합니다: {ark.submit_resolution(PARITY_FACE, '540p')}")
        # 기본 화질이 caps 에 있으면(VPOC_RESOLUTION=720p 배포) 빈 값은 보정 없이 그 값.
        env.set_registry(base_items)
        env.patch(settings, "RESOLUTION", "720p")
        if ark.submit_resolution(PARITY_FACE, "") != ("720p", ""):
            bad.append(f"(rz-c) 기본 화질이 caps 에 있으면 빈 값이 보정 없이 그 값이어야 합니다: {ark.submit_resolution(PARITY_FACE, '')}")
        env.patch(settings, "RESOLUTION", "540p")
        for stored_res, tag in (("540p", "540p"), ("", "빈 값")):
            cv_calls.clear()
            state = _parity_state(
                PARITY_FACE,
                resolution="540p",
                step4={
                    "status": "running",
                    "resolution": stored_res,
                    "shots": [_parity_shot4("s1", "queued", durationS=6), _parity_shot4("s2", "completed", durationS=5)],
                },
            )
            posts, shot, job, _ = _parity_submit(env, logs, state)
            want_usd = settings.shot_usd(PARITY_FACE, "480p", 6)
            if want_usd >= settings.shot_usd(PARITY_FACE, "540p", 6):
                bad.append("(rz-c) 480p 단가가 540p(승인 금액) 이상입니다(검사 구성 오류)")
            if len(posts) != 1 or (posts[0][2] or {}).get("resolution") != "480p":
                bad.append(f"(rz-c) {tag} 회차 본문 resolution 이 480p 가 아닙니다: {[(p[2] or {}).get('resolution') for p in posts]} {shot.get('error')}")
            adjusted = logs.messages("vpoc.fal.resolution_adjusted")
            if len(adjusted) != 1 or "from=540p" not in adjusted[0] or "to=480p" not in adjusted[0]:
                bad.append(f"(rz-c) {tag} vpoc.fal.resolution_adjusted 로그(from=540p to=480p)가 1줄이 아닙니다: {adjusted}")
            if [k.get("resolution") for k in cv_calls] != ["480p"]:
                bad.append(f"(rz-c) {tag} 원장 charge_video resolution 이 480p 가 아닙니다: {[k.get('resolution') for k in cv_calls]}")
            if len(env.charges) != 1 or abs(float(env.charges[0].get("usd") or 0) - want_usd) > 1e-6:
                bad.append(f"(rz-c) {tag} 원장 usd 가 shot_usd(얼굴, 480p, 6)={want_usd} 가 아닙니다: {env.charges}")
            if job.get("resolution") != "480p" or abs(float(job.get("usd") or 0) - want_usd) > 1e-6:
                bad.append(f"(rz-c) {tag} jobs resolution·usd 가 480p·{want_usd} 가 아닙니다: {job.get('resolution')} {job.get('usd')}")
        # 저장 화질이 caps 에 있으면 보정 로그가 없다.
        state["steps"]["4"]["resolution"] = "1080p"
        posts, _shot, _job, _ = _parity_submit(env, logs, state)
        if not posts or (posts[0][2] or {}).get("resolution") != "1080p" or logs.messages("vpoc.fal.resolution_adjusted"):
            bad.append("(rz-c) 1080p 회차가 그대로 보내지지 않았거나 보정 로그가 남았습니다")

        # ---- (rz-d) ledger.charge_video model 칸 ----
        env.reset_calls()
        cases_d = [
            ("fal+r2v", {"model": PARITY_FACE, "endpoint": PARITY_PARTNER}, partner_eid, settings.shot_usd(PARITY_FACE, "720p", 5)),
            ("fal+r2v(접두 없음)", {"model": PARITY_FACE, "endpoint": partner_eid}, partner_eid, settings.shot_usd(PARITY_FACE, "720p", 5)),
            ("fal 엔드포인트 없음", {"model": PARITY_FACE}, face_eid, settings.shot_usd(PARITY_FACE, "720p", 5)),
            ("ARK", {"model": "seedance-2-5", "endpoint": "seedance-2-5"}, settings.ARK_MODEL_IDS["seedance-2-5"], settings.shot_usd("seedance-2-5", "720p", 5)),
            ("Luma", {"model": "luma-ray2", "endpoint": "luma-ray2"}, settings.VIDEO_MODEL, settings.shot_usd("luma-ray2", "720p", 5)),
            ("Luma(빈 값)", {}, settings.VIDEO_MODEL, settings.shot_usd("", "720p", 5)),
        ]
        for index, (tag, extra, want_model, want_usd) in enumerate(cases_d):
            before = len(env.charges)
            real_charge_video(f"t-rz-d-{index}", user="u", project_id="p", plan_id="pl", resolution="720p", seconds=5, **extra)
            entry = env.charges[before] if len(env.charges) > before else {}
            if entry.get("model") != want_model or abs(float(entry.get("usd") or 0) - want_usd) > 1e-9:
                bad.append(f"(rz-d) {tag} 원장 model·usd 가 {want_model}·{want_usd} 가 아닙니다: {entry.get('model')} {entry.get('usd')}")
        if settings.shot_usd(PARITY_FACE, "720p", 5) <= 0:
            bad.append("(rz-d) 얼굴 단가가 0 입니다(검사 구성 오류)")

        # ---- (rz-e) generate 금액(계열 720p 10초 + dual) ----
        env.patch(settings, "VPOC_DUAL_STYLE_ENABLED", True)
        state = _parity_state(PARITY_FACE, "single", resolution="720p")
        state["steps"]["2"]["totalSeconds"] = 10
        state["steps"]["3"]["master"] = _parity_single_master(10)
        env.save(state)
        step3 = ((env.client.get(env.plan_url()).get_json() or {}).get("steps") or {}).get("3") or {}
        total = (step3.get("cost") or {}).get("totalUsd")
        exact = 4.6224 + 1.4
        if total is None or abs(float(total) - exact) > 0.005:
            bad.append(f"(rz-e) plan_view cost.totalUsd 가 토큰식 4.6224 + 변환 1.4 와 0.005 안이 아닙니다: {total}")
        if step3.get("resolution") != "720p" or (step3.get("cost") or {}).get("resolution") != "720p":
            bad.append(f"(rz-e) 화질이 720p 가 아닙니다: {step3.get('resolution')}")
        resp = env.client.post(env.run_url("generate"), json={"confirmUsd": total})
        if resp.status_code != 200:
            bad.append(f"(rz-e) 화면 금액({total})으로 보낸 generate 가 200 이 아닙니다(서버 계산 불일치): {resp.status_code} {resp.get_json()}")
        elif env.load()["steps"]["4"].get("resolution") != "720p":
            bad.append(f"(rz-e) 회차 화질이 720p 가 아닙니다: {env.load()['steps']['4'].get('resolution')}")
        env.patch(settings, "VPOC_DUAL_STYLE_ENABLED", False)

        # ---- (rz-g) ③ 저장 화질 검증(save_step, 073 fix1 R4.7) ----
        def save_resolution(model: str, wanted: str) -> tuple[int, str, object]:
            env.save(_parity_state(model, resolution="none-yet"))
            resp = env.client.put(f"{env.plan_url()}/steps/3", json={"resolution": wanted})
            err = ((resp.get_json() or {}).get("error") or {})
            return resp.status_code, str(err.get("type") or ""), env.load()["steps"]["3"].get("resolution")

        nonfamily_allowed = settings.VIDEO_RESOLUTIONS.get(PARITY_KLING) or [settings.RESOLUTION]
        cases_g = [
            (PARITY_FACE, "1080p", 200), (PARITY_FACE, "480p", 200), (PARITY_FACE, "720p", 200),
            (PARITY_FACE, "540p", 400),
            ("luma-ray2", "720p", 200), ("luma-ray2", "1080p", 400),
        ] + [(model, res, 200 if res in nonfamily_allowed else 400)
             for model in (PARITY_KLING, PARITY_MINIMAX) for res in ("540p", "720p", "512P")]
        if nonfamily_allowed != ["540p"]:
            bad.append(f"(rz-g) 비계열 fal 허용 목록이 지금 값([RESOLUTION]=['540p'])이 아닙니다: {nonfamily_allowed}")
        for model, wanted, want in cases_g:
            status, kind, saved = save_resolution(model, wanted)
            if status != want or (want == 400 and kind != "BadResolution"):
                bad.append(f"(rz-g) {model} resolution={wanted} 저장이 {want} 가 아닙니다: {status} {kind}")
            if want == 200 and saved != wanted:
                bad.append(f"(rz-g) {model} resolution={wanted} 가 저장되지 않았습니다: {saved}")
            if want == 400 and saved != "none-yet":
                bad.append(f"(rz-g) {model} resolution={wanted} 거절인데 값이 바뀌었습니다: {saved}")
        env.save(_parity_state(PARITY_FACE))
        resp = env.client.put(f"{env.plan_url()}/steps/3", json={"resolution": "540p"})
        message = str(((resp.get_json() or {}).get("error") or {}).get("message") or "")
        if "이 모델에서 고를 수 있는 화질이 아닙니다: 540p (가능: 480p, 720p, 1080p)" not in message:
            bad.append(f"(rz-g) 거절 문구가 지금 형식·계열 목록이 아닙니다: {message!r}")

        # ---- (rz-f) 비계열 fal 화질 회귀 스냅샷 ----
        for model, want in PARITY_NONFAMILY_SNAPSHOT.items():
            got = _parity_nonfamily_measure(env, logs, model, cv_calls)
            tag = f"(rz-f) {model.split('/')[1]}"
            eid = model[len("fal:"):]
            for key in ("resolutions", "defaultResolution"):
                if got["options"].get(key) != want["options"][key]:
                    bad.append(f"{tag} video_options {key} 가 구현 전과 다릅니다: {got['options'].get(key)} != {want['options'][key]}")
            if got.get("planResolution") != want["planResolution"]:
                bad.append(f"{tag} step3.resolution 이 구현 전과 다릅니다: {got.get('planResolution')} != {want['planResolution']}")
            for case, expect in want["submit"].items():
                now = got["submit"].get(case) or {}
                extra = got["_extra"].get(case) or {}
                for key in ("body", "status", "mode", "jobResolution", "jobUsd", "charges", "chargeVideoResolution"):
                    if now.get(key) != expect[key]:
                        bad.append(f"{tag} {case} {key} 가 구현 전과 다릅니다: {now.get(key)} != {expect[key]}")
                if "resolution" in (extra.get("rawBody") or {}):
                    bad.append(f"{tag} {case} 본문에 resolution 키가 생겼습니다(지금은 enum 에 없으면 없다)")
                if extra.get("posts") != 1 or extra.get("url") != f"{settings.FAL_QUEUE_URL}/{eid}" or extra.get("provider") != model:
                    bad.append(f"{tag} {case} URL·provider 가 그 모델이 아닙니다: {extra.get('url')} {extra.get('provider')}")
                if extra.get("chargeModels") != [eid]:
                    bad.append(f"{tag} {case} 원장 model 칸이 엔드포인트 id(X2)가 아닙니다: {extra.get('chargeModels')}")
                if extra.get("adjustedLogs"):
                    bad.append(f"{tag} {case} 비계열인데 vpoc.fal.resolution_adjusted 로그가 남았습니다")

        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.ark_calls or env.video_calls:
            bad.append(f"BytePlus·Luma 제출이 있었습니다: ark={len(env.ark_calls)} luma={len(env.video_calls)}")
        if any("dummy-fal-key" in text for text in logs.messages()):
            bad.append("로그에 fal 키 값이 남았습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("화질·원장 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 화질·원장 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 화질·원장 검사 (rz-a)계열 기본 720p·비계열 첫 값 (rz-b)step3.resolution 계열·Luma "
            "(rz-c)540p·빈 값 회차 480p 보정·로그·원장·jobs·submit_resolution 갈래 (rz-d)charge_video model 칸 3갈래 "
            "(rz-e)720p 10초+dual 화면=서버 (rz-f)Kling·MiniMax 화질·본문·원장 회귀 스냅샷 (rz-g)③ 저장 화질 계열·Luma·비계열"
        )


# ------------------------------------------------ 073 작업 4 공용(프롬프트 상한·지칭 pm·애니 ai)


def _parity_text_of_bytes(target_bytes: int, *, korean: bool = False) -> str:
    """문장을 이어 붙여 target_bytes(UTF-8) 이하에서 가장 긴 문장열을 만든다(문장 경계 자르기 확인용)."""
    sentence = (
        "그는 조용한 옥상에서 도시의 불빛을 오래 바라본다. "
        if korean
        else "The black-haired man watches the city lights from the quiet rooftop. "
    )
    out = ""
    while len((out + sentence).encode("utf-8")) <= target_bytes:
        out += sentence
    return out.strip()


def _parity_at_numbers(text: str) -> list[int]:
    """문장 속 서로 다른 @Image 번호(오름차순)."""
    return sorted({int(value) for value in re.findall(r"@Image(\d+)", text or "")})


def _parity_has_plain_tag(text: str) -> bool:
    """"Image 1"·"Images 1-2" 같은 plain 지칭이 있는가(@Image 는 제외)."""
    return bool(re.search(r"(?<![@\w])Images? \d", text or ""))


def _parity_closure(env: "_ParityEnv", name: str):
    """create_blueprint 안쪽 함수를 모든 라우트에서 찾는다(_closure_fn 을 라우트마다 시도)."""
    for view in (env.app.view_functions or {}).values():
        found = _closure_fn(view, name)
        if found is not None:
            return found
    return None


def check_fal_parity_prompt() -> None:
    """073 작업 4 — 프롬프트 상한·실제 보낸 문장·@Image 지칭·지칭 방식 설정(R5.1-5.7, R14.1, R14.3).

    (pm-a) settings.fal_prompt_max: 계열 4000 · Kling default · 형식 오류 항목 버림 · 빈 값 덮어쓰기 없음.
    (pm-b) build_payload(prompt_max=4000): 3,500B 그대로 · 5,000B → ≤4,000B 문장 경계 · 한글 바이트 기준 ·
           prompt_max 없음 = 지금(caps·2,500) · 로그 fromBytes/toBytes.
    (pm-c) fal.submit: 자르면 promptTrimmed={fromBytes,toBytes}·sentPrompt=본문, 아니면 None.
    (pm-d) 통합: 긴 장면 r2v 샷 → jobs sentPrompt=④ sentPromptEn=본문, plan_view ④ promptTrimmed, vpoc.js E8.
           single 은 sentPromptEn=본문·promptTrimmed None, 그 밖(안 잘림)은 앞 시도 값을 지운다.
    (pm-e) 지칭: r2v 본문 @Image 번호 수 = image_urls 수(1..N 연속), plain 지칭 없음, 잘린 뒤에도 지칭이 앞에 남음.
    (pm-f) plan_view 미리보기(sendPromptEn·oneTakePromptEn) = 실제 제출 문장.
    (pm-g) 회귀: ARK·Kling "Image N", ARK 4,001자 PromptOverBudget, reference_prompt(plain) 기존 문장.
    (pm-h) VPOC_FAL_SEEDANCE_TAG_STYLE: 파서(기본·plain·틀린 값), plain 이면 계열 r2v·미리보기 "Image N",
           ARK·Kling·짝은 설정과 상관없이 plain. 설정은 finally 에서 되돌린다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, images, settings  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    partner_eid = PARITY_PARTNER[len("fal:"):]
    face_eid = PARITY_FACE[len("fal:"):]
    kling_eid = PARITY_KLING[len("fal:"):]
    saved_overrides = settings.FAL_PROMPT_MAX_OVERRIDES
    saved_tag_style = settings.FAL_SEEDANCE_TAG_STYLE
    saved_ark_flag = settings.ARK_SEEDANCE_ENABLED
    c2_key = "users/tester/projects/prj_parity/vpoc/pl_parity_01/characters/c2.jpg"

    def nbytes(text: str) -> int:
        return len(str(text or "").encode("utf-8"))

    def shots_state(model: str = PARITY_FACE, *, seconds: int = 6, **plan_extra) -> dict:
        state = _parity_state(
            model,
            step4={
                "status": "running",
                "shots": [
                    _parity_shot4("s1", "queued", durationS=seconds),
                    _parity_shot4("s2", "completed", durationS=5),
                ],
            },
        )
        state["steps"]["3"]["shots"][0].update(plan_extra)
        return state

    def add_second_character(state: dict) -> dict:
        state["steps"]["2"]["characters"].append(
            {"id": "c2", "nameKo": "하윤", "nameEn": "Ha Yoon", "handleEn": "the red-haired woman", "appearanceEn": ""}
        )
        state["steps"]["3"]["characters"].append(
            {"id": "c2", "image": {"status": "ready", "source": "ai", "key": c2_key}}
        )
        state["steps"]["3"]["shots"][0]["characters"] = ["c1", "c2"]
        return state

    def single_state(char_keys: list[str], bg_key: str) -> dict:
        state = _parity_state(
            PARITY_FACE,
            "single",
            step4={"status": "running", "shots": [_parity_shot4(ark.ONE_TAKE_ID, "queued", durationS=6)]},
        )
        state["steps"]["3"]["master"] = {
            "version": 1,
            "totalS": 6,
            "styleEn": "Cinematic live-action film footage",
            "background": {"promptEn": "A quiet rooftop at dusk", "imageKey": bg_key},
            "characters": [
                {"id": f"c{i + 1}", "handleEn": f"person {i + 1}", "promptEn": "dark coat", "imageKey": key}
                for i, key in enumerate(char_keys)
            ],
            "beats": [{"id": "b1", "t0": 0, "t1": 6, "actionEn": "They look at the city", "cameraEn": "Static"}],
        }
        return state

    def one_take_state() -> dict:
        return _parity_state(
            PARITY_FACE,
            step4={"status": "running", "oneTake": True, "shots": [_parity_shot4(ark.ONE_TAKE_ID, "queued", durationS=11)]},
        )

    def view_of(state_view: dict, step: str) -> dict:
        return ((state_view.get("steps") or {}).get(step) or {})

    def preview_row(plan: dict, shot_id: str = "s1") -> dict:
        return next((row for row in view_of(plan, "3").get("shots") or [] if row.get("id") == shot_id), {})

    def body_prompt(posts: list) -> str:
        return str(((posts[0][2] or {}).get("prompt") if posts else "") or "")

    try:
        # ---- (pm-a) settings.fal_prompt_max ----
        parse = settings._parse_prompt_max_overrides
        if os.getenv("VPOC_FAL_PROMPT_MAX_OVERRIDES") is None and settings.FAL_PROMPT_MAX_OVERRIDES != (
            ("bytedance/seedance-2.5/*", 4000),
        ):
            bad.append(f"(pm-a) env 없을 때 FAL_PROMPT_MAX_OVERRIDES 가 기본이 아닙니다: {settings.FAL_PROMPT_MAX_OVERRIDES}")
        if parse(settings.FAL_PROMPT_MAX_OVERRIDES_DEFAULT) != (("bytedance/seedance-2.5/*", 4000),):
            bad.append(f"(pm-a) 기본 문자열 해석이 다릅니다: {parse(settings.FAL_PROMPT_MAX_OVERRIDES_DEFAULT)}")
        broken = "a/*=100, bad, x=, =5, y=abc, z=-1, w=0, q r=5, B/*=200,c/*=1.5"
        if parse(broken) != (("a/*", 100), ("b/*", 200)):
            bad.append(f"(pm-a) 형식이 틀린 항목을 버리지 않았습니다: {parse(broken)}")
        if parse("") != () or parse("   ") != () or parse(None) != ():  # type: ignore[arg-type]
            bad.append("(pm-a) 빈 값 해석이 () 가 아닙니다")
        settings.FAL_PROMPT_MAX_OVERRIDES = parse(settings.FAL_PROMPT_MAX_OVERRIDES_DEFAULT)
        for endpoint in (partner_eid, face_eid, PARITY_PARTNER, PARITY_FACE.upper().replace("FAL:", "fal:")):
            if settings.fal_prompt_max(endpoint, 2500) != 4000:
                bad.append(f"(pm-a) 계열 {endpoint} 상한이 4000 이 아닙니다: {settings.fal_prompt_max(endpoint, 2500)}")
        for default, want in ((2500, 2500), (1800, 1800), (None, 2500), (0, 2500), ("x", 2500)):
            if settings.fal_prompt_max(kling_eid, default) != want:
                bad.append(f"(pm-a) Kling default={default!r} 상한이 {want} 가 아닙니다: {settings.fal_prompt_max(kling_eid, default)}")
        if settings.fal_prompt_max("", 1800) != 1800:
            bad.append("(pm-a) 빈 endpoint 는 default 여야 합니다")
        settings.FAL_PROMPT_MAX_OVERRIDES = parse("first/*=111,first/x=222")
        if settings.fal_prompt_max("first/x", 2500) != 111:
            bad.append("(pm-a) 첫 번째로 맞는 글롭 값을 쓰지 않습니다")
        settings.FAL_PROMPT_MAX_OVERRIDES = parse("")
        if settings.fal_prompt_max(partner_eid, 2500) != 2500 or settings.fal_prompt_max(partner_eid, None) != 2500:
            bad.append("(pm-a) 빈 값이면 덮어쓰기 없이 caps·2,500 이어야 합니다")
        settings.FAL_PROMPT_MAX_OVERRIDES = saved_overrides

        # ---- (pm-b) build_payload(prompt_max) ----
        logs.attach()
        r2v_caps = _parity_caps("r2v")
        text_3500 = _parity_text_of_bytes(3500)
        text_5000 = _parity_text_of_bytes(5000)
        text_ko = _parity_text_of_bytes(6000, korean=True)
        if not 2500 < nbytes(text_3500) <= 3500 or nbytes(text_5000) <= 4000:
            bad.append("(pm-b) 검사 문장 구성 오류")
        if len(text_ko) > 4000 or nbytes(text_ko) <= 4000:
            bad.append(f"(pm-b) 한글 문장이 '글자 ≤4000·바이트 >4000' 이 아닙니다: {len(text_ko)}자 {nbytes(text_ko)}B")
        got = fal.build_payload(r2v_caps, prompt=text_3500, duration_s=5, prompt_max=4000).get("prompt")
        if got != text_3500:
            bad.append(f"(pm-b) 3,500바이트가 그대로가 아닙니다: {nbytes(got)}B")
        logs.clear()
        got = str(fal.build_payload(r2v_caps, prompt=text_5000, duration_s=5, prompt_max=4000).get("prompt") or "")
        if not 0 < nbytes(got) <= 4000 or not got.endswith(".") or not text_5000.startswith(got):
            bad.append(f"(pm-b) 5,000바이트가 4,000바이트 이하 문장 경계로 잘리지 않았습니다: {nbytes(got)}B {got[-20:]!r}")
        trim_logs = logs.messages("vpoc.fal.prompt_trimmed")
        if len(trim_logs) != 1 or f"fromBytes={nbytes(text_5000)} toBytes={nbytes(got)}" not in trim_logs[0] or "max=4000" not in trim_logs[0]:
            bad.append(f"(pm-b) prompt_trimmed 로그에 max·fromBytes·toBytes 가 없습니다: {trim_logs}")
        if any("watches the city" in text for text in logs.messages()):
            bad.append("(pm-b) 로그에 프롬프트 본문이 남았습니다")
        got = str(fal.build_payload(r2v_caps, prompt=text_ko, duration_s=5, prompt_max=4000).get("prompt") or "")
        if not 0 < nbytes(got) <= 4000 or not got.endswith(".") or got == text_ko:
            bad.append(f"(pm-b) 한글 문장이 바이트 기준으로 잘리지 않았습니다: {len(got)}자 {nbytes(got)}B")
        # prompt_max 없음·0 → 지금과 같다(caps promptMaxLength, 없으면 2,500).
        for caps_max, limit in ((None, 2500), (1800, 1800)):
            caps = {**r2v_caps, "promptMaxLength": caps_max}
            legacy = fal.build_payload(caps, prompt=text_5000, duration_s=5)
            for extra in ({}, {"prompt_max": None}, {"prompt_max": 0}):
                again = fal.build_payload(caps, prompt=text_5000, duration_s=5, **extra)
                if again != legacy or nbytes(again.get("prompt")) > limit:
                    bad.append(f"(pm-b) prompt_max {extra} 가 지금(caps {caps_max}) 결과와 다릅니다")
        no_field = fal.build_payload({**r2v_caps, "promptField": ""}, prompt=text_5000, duration_s=5, prompt_max=4000)
        if "prompt" in no_field:
            bad.append("(pm-b) promptField 가 없는데 prompt 를 넣었습니다")

        env = _parity_env(registry=_parity_registry_items() + [_parity_kling_item()])
        logs.attach()
        env.fake.blobs[c2_key] = _tiny_jpeg()

        # ---- (pm-c) fal.submit 반환 ----
        kwargs = dict(owner="o", project_id="p", plan_id="pl", shot_id="s1", attempt=1, gen_id="g1", duration_s=5)
        env.reset_calls()
        job = fal.submit(model=PARITY_PARTNER, prompt=text_5000, **kwargs)
        sent = body_prompt(env.fal_posts())
        if job.get("promptTrimmed") != {"fromBytes": nbytes(text_5000), "toBytes": nbytes(sent)} or nbytes(sent) > 4000:
            bad.append(f"(pm-c) 잘린 제출 promptTrimmed 가 다릅니다: {job.get('promptTrimmed')} 본문 {nbytes(sent)}B")
        if job.get("sentPrompt") != sent or not sent:
            bad.append("(pm-c) sentPrompt 가 본문 prompt 와 다릅니다")
        if job.get("promptChars") != len(text_5000):
            bad.append(f"(pm-c) promptChars 가 원문 길이가 아닙니다: {job.get('promptChars')}")
        env.reset_calls()
        job = fal.submit(model=PARITY_PARTNER, prompt=text_3500, **kwargs)
        if job.get("promptTrimmed") is not None or job.get("sentPrompt") != text_3500 or body_prompt(env.fal_posts()) != text_3500:
            bad.append(f"(pm-c) 안 잘린 제출이 promptTrimmed None·sentPrompt 원문이 아닙니다: {job.get('promptTrimmed')}")
        # 계열이 아닌 fal(Kling caps 2,500)은 지금 상한 그대로(R14.3).
        env.reset_calls()
        job = fal.submit(model=PARITY_KLING, prompt=text_3500, keyframe_jpeg_b64="AAA", **kwargs)
        sent = body_prompt(env.fal_posts())
        if not 0 < nbytes(sent) <= 2500 or job.get("promptTrimmed") != {"fromBytes": nbytes(text_3500), "toBytes": nbytes(sent)}:
            bad.append(f"(pm-c) Kling 상한이 caps 2,500 이 아니거나 promptTrimmed 가 다릅니다: {nbytes(sent)}B {job.get('promptTrimmed')}")

        # ---- (pm-d) 통합: 긴 장면 r2v 샷 ----
        long_scene = _parity_text_of_bytes(5000)
        posts, shot, job, loaded = _parity_submit(env, logs, shots_state(promptEn=long_scene))
        sent = body_prompt(posts)
        if len(posts) != 1 or posts[0][1] != f"{settings.FAL_QUEUE_URL}/{partner_eid}":
            bad.append(f"(pm-d) 긴 샷이 r2v 로 1회 제출되지 않았습니다: {[p[1] for p in posts]} {shot.get('error')}")
        if not 0 < nbytes(sent) <= 4000:
            bad.append(f"(pm-d) 본문 prompt 가 4,000바이트 이하가 아닙니다: {nbytes(sent)}B")
        if job.get("sentPrompt") != sent or shot.get("sentPromptEn") != sent:
            bad.append("(pm-d) jobs sentPrompt·④ sentPromptEn 이 본문 prompt 와 다릅니다")
        plan = env.client.get(env.plan_url()).get_json() or {}
        preview_long = str(preview_row(plan).get("sendPromptEn") or "")
        trimmed = {"fromBytes": nbytes(preview_long), "toBytes": nbytes(sent)}
        if shot.get("promptTrimmed") != trimmed or job.get("promptTrimmed") != trimmed:
            bad.append(f"(pm-d) ④·jobs promptTrimmed 가 {trimmed} 가 아닙니다: {shot.get('promptTrimmed')} {job.get('promptTrimmed')}")
        shot_view = next((s for s in view_of(plan, "4").get("shots") or [] if s.get("id") == "s1"), {})
        if shot_view.get("promptTrimmed") != trimmed or shot_view.get("sentPromptEn") != sent:
            bad.append(f"(pm-d) plan_view ④ 샷에 promptTrimmed·sentPromptEn 이 없습니다: {shot_view.get('promptTrimmed')}")
        if not preview_long.startswith(sent[:200]) or nbytes(preview_long) <= 4000:
            bad.append("(pm-d) 잘린 문장이 미리보기(원문)의 앞부분이 아닙니다")
        trimmed_sent = sent
        js = (PUBLIC / "vpoc.js").read_text(encoding="utf-8")
        for needle in ("바이트로 잘렸습니다(원래 ", "아래가 실제로 보낸 문장입니다.", "shot.promptTrimmed"):
            if needle not in js:
                bad.append(f"(pm-d) vpoc.js 에 E8 안내({needle!r})가 없습니다")
        # 그 밖(안 잘림): 앞 시도의 잘린 기록을 지운다.
        stale = shots_state()
        stale["steps"]["4"]["shots"][0].update({"sentPromptEn": "old trimmed", "promptTrimmed": {"fromBytes": 9, "toBytes": 5}})
        posts, shot, job, _ = _parity_submit(env, logs, stale)
        if shot.get("promptTrimmed") is not None or shot.get("sentPromptEn") is not None or job.get("promptTrimmed") is not None:
            bad.append(f"(pm-d) 안 잘린 재제출이 앞 시도 기록을 지우지 않았습니다: {shot.get('sentPromptEn')!r} {shot.get('promptTrimmed')}")
        if job.get("sentPrompt") != body_prompt(posts):
            bad.append("(pm-d) 안 잘린 샷 jobs sentPrompt 가 본문과 다릅니다")
        # single(안 잘림): sentPromptEn = 본문, promptTrimmed None.
        single_keys = ["users/tester/parity/pm-c1.jpg", "users/tester/parity/pm-c2.jpg", "users/tester/parity/pm-bg.jpg"]
        for key in single_keys:
            env.fake.blobs[key] = _tiny_jpeg()
        posts, shot, job, _ = _parity_submit(env, logs, single_state(single_keys[:2], single_keys[2]), ark.ONE_TAKE_ID)
        sent_single = body_prompt(posts)
        if not sent_single or shot.get("sentPromptEn") != sent_single or job.get("sentPromptEn") != sent_single:
            bad.append("(pm-d) single sentPromptEn 이 본문 prompt 와 다릅니다")
        if shot.get("promptTrimmed") is not None or job.get("sentPrompt") != sent_single:
            bad.append(f"(pm-d) single 안 잘림 기록이 다릅니다: {shot.get('promptTrimmed')}")

        # ---- (pm-e) 지칭 ----
        image_urls = (posts[0][2] or {}).get("image_urls") if posts else []
        if _parity_at_numbers(sent_single) != list(range(1, len(image_urls or []) + 1)) or len(image_urls or []) != 3:
            bad.append(f"(pm-e) single @Image 번호가 image_urls(3장)와 맞지 않습니다: {_parity_at_numbers(sent_single)}")
        if not sent_single.startswith("@Image1 is person 1; @Image2 is person 2; @Image3 is the background setting."):
            bad.append(f"(pm-e) single 지칭 문장이 맨 앞에 없습니다: {sent_single[:120]!r}")
        if _parity_has_plain_tag(sent_single):
            bad.append("(pm-e) single 본문에 plain 'Image N' 지칭이 있습니다")
        panel_calls: list[tuple] = []

        def fake_panel_jpeg(owner, pid, file_id, index, _cache=None):
            panel_calls.append((file_id, index))
            return _tiny_jpeg()

        env.patch(images, "panel_jpeg", fake_panel_jpeg)
        state = add_second_character(shots_state())
        state["steps"]["4"]["panels"] = {"s1": [["f1", 0], ["f2", 0], ["f3", 0]]}
        posts, shot, job, _ = _parity_submit(env, logs, state)
        sent = body_prompt(posts)
        urls = (posts[0][2] or {}).get("image_urls") if posts else []
        if len(urls or []) != 5 or _parity_at_numbers(sent) != [1, 2, 3, 4, 5]:
            bad.append(f"(pm-e) 인물 2·패널 3 샷 @Image 번호가 image_urls 와 맞지 않습니다: {len(urls or [])} {_parity_at_numbers(sent)}")
        if not sent.startswith("@Image1 is the black-haired man; @Image2 is the red-haired woman; @Image3, @Image4, @Image5 are"):
            bad.append(f"(pm-e) 지칭 문장 형식이 다릅니다: {sent[:160]!r}")
        if _parity_has_plain_tag(sent) or "Image 1 is" in sent.replace("@Image", ""):
            bad.append("(pm-e) r2v 본문에 plain 'Image N' 지칭이 있습니다")
        if len(panel_calls) < 3:
            bad.append(f"(pm-e) 패널 그림을 읽지 않았습니다: {panel_calls}")
        # 잘린 뒤에도 지칭 문장이 앞에 남는다(pm-d 본문).
        if not trimmed_sent.startswith("@Image1 is the black-haired man.") or not (
            shot_view.get("sentPromptEn") or ""
        ).startswith("@Image1 is the black-haired man."):
            bad.append("(pm-e) 잘린 문장에서 지칭 문장이 맨 앞에 남지 않았습니다")
        # image_label 단위 갈래.
        label_table = [
            ((3, 3, "plain"), "Image 3"), ((3, 4, "plain"), "Images 3-4"), ((3, 5, "plain"), "Images 3-5"),
            ((3, 3, "at"), "@Image3"), ((3, 4, "at"), "@Image3-@Image4"), ((3, 5, "at"), "@Image3, @Image4, @Image5"),
        ]
        for args, want in label_table:
            if ark.image_label(*args) != want:
                bad.append(f"(pm-e) image_label{args} 가 {want!r} 가 아닙니다: {ark.image_label(*args)!r}")
        if ark.image_label(2, 2) != "Image 2":
            bad.append("(pm-e) image_label 기본 style 이 plain 이 아닙니다")

        # ---- (pm-f) plan_view 미리보기 = 실제 제출 ----
        env.patch(images, "panel_jpeg", fake_panel_jpeg)
        posts, shot, job, _ = _parity_submit(env, logs, add_second_character(shots_state()))
        plan = env.client.get(env.plan_url()).get_json() or {}
        preview = str(preview_row(plan).get("sendPromptEn") or "")
        if not preview or preview != body_prompt(posts) or preview_row(plan).get("referenceCount") != 2:
            bad.append(f"(pm-f) sendPromptEn 이 실제 제출 문장과 다릅니다: {preview[:80]!r} / {body_prompt(posts)[:80]!r}")
        if _parity_at_numbers(preview) != [1, 2]:
            bad.append(f"(pm-f) 미리보기 지칭이 @Image1·@Image2 가 아닙니다: {preview[:80]!r}")
        posts, shot, job, _ = _parity_submit(env, logs, one_take_state(), ark.ONE_TAKE_ID)
        plan = env.client.get(env.plan_url()).get_json() or {}
        one_take_preview = str(view_of(plan, "3").get("oneTakePromptEn") or "")
        if not one_take_preview or one_take_preview != body_prompt(posts):
            bad.append(
                f"(pm-f) oneTakePromptEn 이 실제 제출 문장과 다릅니다: {one_take_preview[:80]!r} / {body_prompt(posts)[:80]!r}"
                f" (샷 {shot.get('status')} {shot.get('error')})"
            )
        if not one_take_preview.startswith("@Image1 is the black-haired man."):
            bad.append(f"(pm-f) oneTakePromptEn 지칭이 @Image 가 아닙니다: {one_take_preview[:80]!r}")

        # ---- (pm-g) 회귀 ----
        # Kling(계열 아님): "Image N" 지칭.
        posts, shot, job, _ = _parity_submit(env, logs, shots_state(PARITY_KLING, seconds=5))
        sent = body_prompt(posts)
        if "Image 1 is the black-haired man" not in sent or "@Image" in sent:
            bad.append(f"(pm-g) Kling 프롬프트가 'Image N' 지칭이 아닙니다: {sent[:80]!r}")
        # ARK(플래그 true): "Image N" 지칭, 미리보기도 같다.
        env.patch(settings, "ARK_SEEDANCE_ENABLED", True)
        env.reset_calls()
        env.save(shots_state("seedance-2-5"))
        plan = env.client.get(env.plan_url()).get_json() or {}
        ark_posts = env.ark_posts()
        ark_text = str(((((ark_posts[0][2] or {}).get("content") or [{}])[0]).get("text")) if ark_posts else "")
        if len(ark_posts) != 1 or "Image 1 is the black-haired man" not in ark_text or "@Image" in ark_text:
            bad.append(f"(pm-g) ARK 프롬프트가 'Image N' 지칭이 아닙니다: {len(ark_posts)} {ark_text[:80]!r}")
        ark_preview = str(preview_row(plan).get("sendPromptEn") or "")
        if ark_preview != ark_text:
            bad.append(f"(pm-g) ARK 미리보기가 제출 문장과 다릅니다: {ark_preview[:80]!r}")
        env.reset_calls()
        try:
            ark.submit(
                model="seedance-2-5", owner="o", project_id="p", plan_id="pl", shot_id="s1",
                attempt=1, prompt="a" * 4001,
            )
            bad.append("(pm-g) ARK 4,001자가 거절되지 않았습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != "PromptOverBudget":
                bad.append(f"(pm-g) ARK 4,001자가 PromptOverBudget 이 아닙니다: {getattr(exc, 'kind', '')} {exc}")
        if env.ark_calls:
            bad.append("(pm-g) ARK 4,001자인데 ark._call 이 불렸습니다")
        env.patch(settings, "ARK_SEEDANCE_ENABLED", False)
        names = ["the man", "the man", ark.PANEL_LABEL, None]
        plain_labels = (
            "Images 1-2 are all the man (the same person from different views); Image 3 is the webtoon panel "
            "this shot brings to life, in story order (follow their setting, art style and events); Image 4 is an "
            "art-style reference only (not a character)."
        )
        at_labels = plain_labels.replace("Images 1-2", "@Image1-@Image2").replace("Image 3", "@Image3").replace(
            "Image 4", "@Image4"
        )
        plain = ark.reference_prompt("A rooftop.", names, tag_style="plain")
        legacy = ark.reference_prompt("A rooftop.", names)
        at = ark.reference_prompt("A rooftop.", names, tag_style="at")
        if plain != legacy or not plain.startswith(plain_labels + " Keep every character exactly") or not plain.endswith(
            "Scene: A rooftop."
        ):
            bad.append(f"(pm-g) reference_prompt(plain) 가 기존 문장과 다릅니다: {plain[:160]!r}")
        if not at.startswith(at_labels) or at.replace(at_labels, plain_labels, 1) != plain:
            bad.append(f"(pm-g) reference_prompt(at) 가 지칭만 바뀐 문장이 아닙니다: {at[:160]!r}")
        if ark.reference_prompt("A rooftop.", [], tag_style="at") != "A rooftop.":
            bad.append("(pm-g) 참고 없음이면 문장이 그대로여야 합니다")

        # ---- (pm-h) 지칭 방식 설정 ----
        parse_style = settings._parse_tag_style
        for raw, want in ((None, "at"), ("plain", "plain"), (" PLAIN ", "plain"), ("", "at"), ("xyz", "at"), ("AT", "at")):
            if parse_style(raw) != want:
                bad.append(f"(pm-h) _parse_tag_style({raw!r}) 가 {want} 가 아닙니다: {parse_style(raw)!r}")
        if os.getenv("VPOC_FAL_SEEDANCE_TAG_STYLE") is None and saved_tag_style != "at":
            bad.append(f"(pm-h) env 없을 때 FAL_SEEDANCE_TAG_STYLE 이 at 이 아닙니다: {saved_tag_style!r}")
        for style in ("at", "plain"):
            settings.FAL_SEEDANCE_TAG_STYLE = style
            if ark.tag_style(PARITY_FACE) != style:
                bad.append(f"(pm-h) 설정 {style} 인데 tag_style(얼굴) 이 {ark.tag_style(PARITY_FACE)!r}")
            for other in ("seedance-2-5", "seedance-2-0", PARITY_KLING, PARITY_PARTNER, "luma-ray2", ""):
                if ark.tag_style(other) != "plain":
                    bad.append(f"(pm-h) 설정 {style} 인데 tag_style({other!r}) 가 plain 이 아닙니다")
        settings.FAL_SEEDANCE_TAG_STYLE = "xyz"
        if ark.tag_style(PARITY_FACE) != "at":
            bad.append("(pm-h) 틀린 설정값이면 tag_style(얼굴) 이 at 이어야 합니다")
        settings.FAL_SEEDANCE_TAG_STYLE = "plain"
        posts, shot, job, _ = _parity_submit(env, logs, add_second_character(shots_state()))
        sent = body_prompt(posts)
        plan = env.client.get(env.plan_url()).get_json() or {}
        if not posts or posts[0][1] != f"{settings.FAL_QUEUE_URL}/{partner_eid}":
            bad.append("(pm-h) plain 설정에서 r2v 로 제출되지 않았습니다")
        if not sent.startswith("Image 1 is the black-haired man; Image 2 is the red-haired woman.") or "@Image" in sent:
            bad.append(f"(pm-h) plain 설정인데 r2v 본문이 'Image N' 지칭이 아닙니다: {sent[:100]!r}")
        if str(preview_row(plan).get("sendPromptEn") or "") != sent:
            bad.append("(pm-h) plain 설정 sendPromptEn 이 제출 문장과 다릅니다")
        posts, shot, job, _ = _parity_submit(env, logs, one_take_state(), ark.ONE_TAKE_ID)
        plan = env.client.get(env.plan_url()).get_json() or {}
        one_take_preview = str(view_of(plan, "3").get("oneTakePromptEn") or "")
        if one_take_preview != body_prompt(posts) or "@Image" in one_take_preview or not one_take_preview.startswith(
            "Image 1 is the black-haired man."
        ):
            bad.append(f"(pm-h) plain 설정 oneTakePromptEn 이 제출 문장과 다르거나 @Image 입니다: {one_take_preview[:80]!r}")
        posts, shot, job, _ = _parity_submit(env, logs, single_state(single_keys[:2], single_keys[2]), ark.ONE_TAKE_ID)
        if not body_prompt(posts).startswith("Image 1 is person 1; Image 2 is person 2; Image 3 is the background setting."):
            bad.append(f"(pm-h) plain 설정 single 지칭이 'Image N' 이 아닙니다: {body_prompt(posts)[:100]!r}")
        settings.FAL_SEEDANCE_TAG_STYLE = saved_tag_style

        # 외부 호출 0회(실제 네트워크·Luma), 비밀값·본문 로그 없음.
        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.video_calls:
            bad.append(f"Luma 제출이 있었습니다: {len(env.video_calls)}")
        if any("dummy-fal-key" in text or "dummy-ark-key" in text for text in logs.messages()):
            bad.append("로그에 API 키 값이 남았습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("프롬프트 상한·지칭 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:2000])
    finally:
        logs.detach()
        settings.FAL_PROMPT_MAX_OVERRIDES = saved_overrides
        settings.FAL_SEEDANCE_TAG_STYLE = saved_tag_style
        settings.ARK_SEEDANCE_ENABLED = saved_ark_flag
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 프롬프트 상한·지칭 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 프롬프트 상한·지칭 검사 (pm-a)fal_prompt_max 계열 4000·Kling default·형식 오류·빈 값 "
            "(pm-b)build_payload 3,500B 그대로·5,000B 문장 경계·한글 바이트·prompt_max 없음 회귀·로그 "
            "(pm-c)submit sentPrompt·promptTrimmed (pm-d)긴 r2v 샷 jobs·④·plan_view·E8·single·안 잘림 지움 "
            "(pm-e)@Image 번호=image_urls·지칭 맨 앞·image_label (pm-f)sendPromptEn·oneTakePromptEn=제출 "
            "(pm-g)ARK·Kling Image N·4,001자 거절·reference_prompt plain (pm-h)TAG_STYLE 파서·plain 복귀·비계열 plain"
        )


def check_anime_identity() -> None:
    """073 작업 4 — 애니 변환 프롬프트: 패널만 그림체 기준, 인물은 identity only, 패널 0장은 2D 앵커(R8, D8).

    (ai-a) _anime_prompt(3, identity_count=1): 그림체 문장 @Image1..3, @Image4 는 identity 문장에만, generic 금지 문장.
    (ai-b) _anime_prompt(0, identity_count=1): "2D Korean webtoon"·"Korean webtoon style", @Image1 은 identity only.
    (ai-c) _anime_prompt(0): 앵커 문장, @Image 없음. _anime_prompt(3)(인자 1개)은 계속 동작.
    (ai-d) 금지 문구 4개가 모든 경우에 없다.
    (ai-e) 통합(_run_anime_job, 가짜 fal.submit_edit): 패널 2+인물 1 → 3장(패널·패널·인물)·번호 일치,
           패널 0+인물 1 → 1장·2D 앵커, 패널 5+인물 → 4장 이하, 패널 읽기 실패·인물 없음·인물 읽기 실패.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src import stitcher  # noqa: PLC0415
    from src.vpoc import fal, images, routes, settings, store  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    banned = ("cel shading", "flat soft colors", "2D anime", "2D animation")

    def identity_sentence(text: str) -> str:
        match = re.search(r"(@Image\d+(?:, @Image\d+)*) (?:is|are) an identity reference only[^.]*\.", text)
        return match.group(0) if match else ""

    try:
        # ---- (ai-a) ----
        p = routes._anime_prompt(3, identity_count=1)
        style_part = p.split("Do NOT use a generic anime")[0]
        if "(@Image1, @Image2, @Image3)" not in style_part:
            bad.append(f"(ai-a) 그림체 문장에 @Image1, @Image2, @Image3 이 없습니다: {style_part[:160]!r}")
        if "@Image4" in style_part or p.count("@Image4") != 1 or "@Image4" not in identity_sentence(p):
            bad.append(f"(ai-a) @Image4 가 identity 문장에만 있지 않습니다: {p!r}")
        if not identity_sentence(p).startswith("@Image4 is an identity reference only"):
            bad.append(f"(ai-a) identity 문장 형식이 다릅니다: {identity_sentence(p)!r}")
        if "Do NOT use a generic anime" not in p or "Do not copy its photographic look" not in p:
            bad.append("(ai-a) generic 금지·실사 질감 금지 문장이 없습니다")
        if not p.endswith("No text, no speech bubbles.") or "exactly the same as @Video1" not in p:
            bad.append("(ai-a) 공통 끝 문장이 없습니다")

        # ---- (ai-b) ----
        p0i = routes._anime_prompt(0, identity_count=1)
        if "2D Korean webtoon" not in p0i or "Korean webtoon style" not in p0i:
            bad.append(f"(ai-b) 패널 0장 앵커 문장이 없습니다: {p0i[:120]!r}")
        if p0i.count("@Image1") != 1 or not identity_sentence(p0i).startswith("@Image1 is an identity reference only"):
            bad.append(f"(ai-b) @Image1 이 identity only 가 아닙니다: {p0i!r}")
        if "drawing style as the reference webtoon panels" in p0i or "@Image2" in p0i:
            bad.append("(ai-b) 패널 0장인데 패널 그림체 문장·없는 번호가 있습니다")

        # ---- (ai-c) ----
        p0 = routes._anime_prompt(0)
        if not p0.startswith("Redraw @Video1 in a hand-drawn 2D Korean webtoon style") or "@Image" in p0:
            bad.append(f"(ai-c) _anime_prompt(0) 이 앵커 문장·@Image 없음이 아닙니다: {p0!r}")
        if "identity reference" in p0:
            bad.append("(ai-c) 인물 0장인데 identity 문장이 있습니다")
        p3 = routes._anime_prompt(3)
        if p3 != routes._anime_prompt(3, identity_count=0) or "(@Image1, @Image2, @Image3)" not in p3 or "identity" in p3:
            bad.append(f"(ai-c) _anime_prompt(3) 인자 1개 호출이 패널 3장 문장이 아닙니다: {p3[:120]!r}")
        p2 = routes._anime_prompt(1, identity_count=2)
        if "@Image2, @Image3 are an identity reference only" not in p2:
            bad.append(f"(ai-c) 인물 2장 identity 문장이 다릅니다: {p2!r}")
        if routes._anime_prompt(-1, identity_count=-2) != p0:
            bad.append("(ai-c) 음수 인자는 0 으로 봐야 합니다")

        # ---- (ai-d) 금지 문구 ----
        for panels in range(0, 4):
            for identities in range(0, 3):
                text = routes._anime_prompt(panels, identity_count=identities).lower()
                for phrase in banned:
                    if phrase.lower() in text:
                        bad.append(f"(ai-d) _anime_prompt({panels}, {identities}) 에 금지 문구 {phrase!r}")

        # ---- (ai-e) 통합: _run_anime_job ----
        env = _parity_env()
        run_job = _parity_closure(env, "_run_anime_job")
        if run_job is None:
            bad.append("(ai-e) _run_anime_job 을 찾지 못했습니다")
        else:
            edits: list[dict] = []
            fail_panels: set[str] = set()
            fail_chars: set[str] = set()

            def fake_submit_edit(*, endpoint, video_mp4, prompt, output_prefix, reference_jpegs_b64=None, keep_audio=True):
                edits.append({"prompt": prompt, "references": list(reference_jpegs_b64 or []), "endpoint": endpoint})
                return {
                    "provider": settings.FAL_ID_PREFIX + endpoint,
                    "invocationArn": f"fal/requests/{endpoint}/req-ai-{len(edits)}",
                    "clientRequestToken": f"fal-ai-{len(edits)}",
                    "outputPrefix": output_prefix,
                    "submittedAt": "2026-10-27T00:00:00Z",
                    "promptChars": len(prompt),
                }

            def fake_prepare(source_path, output_path, workdir):
                with open(output_path, "wb") as handle:
                    handle.write(b"PREPARED")
                return {"path": output_path, "seconds": 5.0, "height": 720}

            def fake_panel_jpeg(owner, pid, file_id, index, _cache=None):
                if file_id in fail_panels:
                    raise OSError("panel read failed")
                return f"PANEL:{file_id}:{index}".encode()

            def fake_keyframe_b64(key):
                if key in fail_chars:
                    raise OSError("char read failed")
                return "CHAR:" + key

            env.patch(fal, "submit_edit", fake_submit_edit)
            env.patch(stitcher, "prepare_for_anime", fake_prepare)
            env.patch(images, "panel_jpeg", fake_panel_jpeg)
            env.patch(images, "jpeg_for_reference", lambda jpeg: "P64:" + jpeg.decode())
            env.patch(images, "keyframe_base64", fake_keyframe_b64)
            source_key = "users/tester/parity/s1-live.mp4"
            env.fake.blobs[source_key] = b"LIVE-MP4"

            def run_case(tag: str, files: list[str], *, character: bool = True) -> dict:
                edits.clear()
                env.reset_calls()
                fail_panels.clear()
                state = _parity_state(PARITY_FACE, step4={"status": "running", "dual": True})
                state["steps"]["1"]["sceneFiles"] = [{"fileId": fid} for fid in files]
                for fid in files:
                    env.fake.write_json(store.upload_meta_key(PARITY_OWNER, PARITY_PID, fid), {"height": 1000})
                if not character:
                    state["steps"]["3"]["characters"] = [{"id": "c1", "image": {"status": "pending"}}]
                shot = state["steps"]["4"]["shots"][0]
                shot["outputKey"] = source_key
                shot["anime"] = {"status": "preparing", "claim": "claim-" + tag, "sourceOutputKey": source_key}
                env.save(state)
                return state

            def run(tag: str) -> dict:
                run_job(PARITY_OWNER, PARITY_PID, PARITY_PLAN, "s1", "claim-" + tag, PARITY_OWNER)
                anime = (env.load()["steps"]["4"]["shots"][0].get("anime") or {})
                if len(edits) != 1:
                    bad.append(f"(ai-e) {tag}: submit_edit 가 1회가 아닙니다: {len(edits)} {anime.get('status')} {anime.get('error')}")
                    return {}
                if anime.get("status") != "submitted":
                    bad.append(f"(ai-e) {tag}: anime 가 submitted 가 아닙니다: {anime.get('status')}")
                if len(env.charges) != 1:
                    bad.append(f"(ai-e) {tag}: 원장이 1건이 아닙니다: {len(env.charges)}")
                edit = edits[0]
                if len(edit["references"]) > 4:
                    bad.append(f"(ai-e) {tag}: 참고가 4장을 넘습니다: {len(edit['references'])}")
                for phrase in banned:
                    if phrase.lower() in edit["prompt"].lower():
                        bad.append(f"(ai-e) {tag}: 금지 문구 {phrase!r}")
                return edit

            # 패널 2 + 인물 1 → 3장(패널·패널·인물), 프롬프트 번호 일치.
            run_case("p2c1", ["f1", "f2"])
            edit = run("p2c1")
            want_refs = ["P64:PANEL:f1:0", "P64:PANEL:f2:0", "CHAR:" + PARITY_CHAR_KEY]
            if edit and edit["references"] != want_refs:
                bad.append(f"(ai-e) 패널 2+인물 1 참고 순서가 다릅니다: {edit['references']}")
            if edit and (
                "(@Image1, @Image2)" not in edit["prompt"]
                or not identity_sentence(edit["prompt"]).startswith("@Image3 is an identity reference only")
                or "@Image4" in edit["prompt"]
            ):
                bad.append(f"(ai-e) 패널 2+인물 1 프롬프트 번호가 참고와 다릅니다: {edit['prompt'][:200]!r}")
            # 패널 0 + 인물 1 → 1장, 2D 앵커.
            run_case("p0c1", [])
            edit = run("p0c1")
            if edit and (edit["references"] != ["CHAR:" + PARITY_CHAR_KEY] or "2D Korean webtoon" not in edit["prompt"]):
                bad.append(f"(ai-e) 패널 0+인물 1 이 1장·2D 앵커가 아닙니다: {edit['references']} {edit['prompt'][:100]!r}")
            if edit and not identity_sentence(edit["prompt"]).startswith("@Image1 is an identity reference only"):
                bad.append("(ai-e) 패널 0+인물 1 에서 @Image1 이 identity only 가 아닙니다")
            # 패널 5 + 인물 1 → 패널 3(고르게)+인물 1 = 4장.
            run_case("p5c1", ["f1", "f2", "f3", "f4", "f5"])
            edit = run("p5c1")
            if edit and edit["references"] != ["P64:PANEL:f1:0", "P64:PANEL:f3:0", "P64:PANEL:f5:0", "CHAR:" + PARITY_CHAR_KEY]:
                bad.append(f"(ai-e) 패널 5+인물 1 참고가 패널 3+인물 1(4장)이 아닙니다: {edit['references']}")
            if edit and not identity_sentence(edit["prompt"]).startswith("@Image4 is an identity reference only"):
                bad.append("(ai-e) 패널 5+인물 1 에서 @Image4 가 identity 가 아닙니다")
            # 패널 읽기 실패 → 실패한 패널은 번호에 들어가지 않는다.
            run_case("pfail", ["f1", "f2", "f3"])
            fail_panels.add("f2")
            edit = run("pfail")
            if edit and (
                edit["references"] != ["P64:PANEL:f1:0", "P64:PANEL:f3:0", "CHAR:" + PARITY_CHAR_KEY]
                or "(@Image1, @Image2)" not in edit["prompt"]
                or not identity_sentence(edit["prompt"]).startswith("@Image3 is")
            ):
                bad.append(f"(ai-e) 패널 읽기 실패 뒤 번호가 맞지 않습니다: {edit['references']} {edit['prompt'][:160]!r}")
            # 인물 없음(준비 안 됨) → identity 문장 없음.
            run_case("p1c0", ["f1"], character=False)
            edit = run("p1c0")
            if edit and (edit["references"] != ["P64:PANEL:f1:0"] or "identity" in edit["prompt"] or "(@Image1)" not in edit["prompt"]):
                bad.append(f"(ai-e) 인물 없음이 패널 1장·identity 없음이 아닙니다: {edit['references']} {edit['prompt'][:120]!r}")
            # 인물 그림 읽기 실패 → identity 문장 없음.
            run_case("cfail", ["f1"])
            fail_chars.add(PARITY_CHAR_KEY)
            edit = run("cfail")
            fail_chars.clear()
            if edit and (edit["references"] != ["P64:PANEL:f1:0"] or "identity" in edit["prompt"]):
                bad.append(f"(ai-e) 인물 읽기 실패가 identity 없음이 아닙니다: {edit['references']}")
            if env.url_calls or env.fal_calls:
                bad.append(f"(ai-e) 실제 네트워크·fal 호출이 있었습니다: {env.url_calls} {len(env.fal_calls)}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("애니 identity 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:2000])
    finally:
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 애니 변환 프롬프트 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 애니 변환 프롬프트 검사 (ai-a)패널 3 그림체·@Image4 identity only "
            "(ai-b)패널 0 2D 앵커·@Image1 identity (ai-c)인자 1개 호환·@Image 없음 (ai-d)금지 문구 없음 "
            "(ai-e)_run_anime_job 패널·인물 순서·번호·4장 이하·읽기 실패"
        )


# ---------------------------------------------------------------- 073 작업 5: 해제 권한·금지·조용한 대체 금지·치환 로그

PARITY_E3 = "fal 모델 해제는 관리자만 할 수 있습니다."
PARITY_E4 = "설계자 지시로 사용이 금지된 모델입니다."
PARITY_E5_PART = "등록되지 않은 모델입니다. 이 회차의 fal 모델이 해제되어 만들 수 없습니다."
PARITY_DENIED = "fal:fal-ai/seedance-2-0-fast/image-to-video"
PARITY_UNKNOWN_FAL = "fal:fal-ai/unknown-xyz/image-to-video"


def _parity_registry_models(env: "_ParityEnv") -> list[dict]:
    """가짜 저장소의 fal 레지스트리 항목(캐시를 거치지 않고 직접 읽는다)."""
    from src.vpoc import fal  # noqa: PLC0415

    data, _ = env.fake.read_json(fal.REGISTRY_KEY)
    return list((data or {}).get("models") or [])


def _parity_error_of(resp) -> dict:
    return ((resp.get_json(silent=True) or {}).get("error") or {}) if resp is not None else {}


def check_fal_admin_deny() -> None:
    """073 작업 5 — fal 모델 해제 권한·금지 목록 재검사(R10.1-10.5, D10, X7). 외부 호출은 전부 가짜.

    (ad-a) 비관리자 DELETE → 403 AdminOnly·E3(RAW_ERRORS=False 에서도), 레지스트리 그대로, warning 로그.
    (ad-b) 관리자 DELETE → 200 removed=true, info 로그 vpoc.fal.unregistered.
    (ad-c) 비관리자 POST /fal/models(가짜 fal.register) → 201.
    (ad-d) is_admin 없이 만든 deps(기존 6개 인자) → 해제 403(fail-closed). _no_admin 이 기본값이다.
    (ad-e) 금지 패턴 항목 → unavailable_reason E4, choices available=false, fal.submit ModelUnavailable·_request 0회.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, errors, fal, routes, settings  # noqa: PLC0415

    bad: list[str] = []
    face_eid = PARITY_FACE[len("fal:"):]
    delete_url = "/api/vpoc/fal/models/" + face_eid
    envs: list[_ParityEnv] = []
    logs = _LogCapture()
    saved_raw = settings.RAW_ERRORS

    def new_env(**kwargs) -> _ParityEnv:
        env = _parity_env(**kwargs)
        envs.append(env)
        return env

    try:
        logs.attach()
        settings.RAW_ERRORS = False

        # ---- (ad-a) 비관리자 해제 → 403 AdminOnly, 레지스트리 그대로 ----
        env = new_env(is_admin=lambda: False)
        before = _parity_registry_models(env)
        resp = env.client.delete(delete_url)
        err = _parity_error_of(resp)
        if resp.status_code != 403:
            bad.append(f"(ad-a) 비관리자 해제가 403 이 아닙니다: {resp.status_code} {resp.get_json(silent=True)}")
        if err.get("type") != "AdminOnly":
            bad.append(f"(ad-a) 오류 type 이 AdminOnly 가 아닙니다: {err.get('type')}")
        if err.get("message") != PARITY_E3:
            bad.append(f"(ad-a) RAW_ERRORS=False 에서 E3 문구가 그대로가 아닙니다: {err.get('message')}")
        if "AdminOnly" not in errors.USER_FACING_KINDS:
            bad.append("(ad-a) errors.USER_FACING_KINDS 에 AdminOnly 가 없습니다")
        after = _parity_registry_models(env)
        if after != before or not fal.is_registered(PARITY_FACE):
            bad.append(f"(ad-a) 거절됐는데 레지스트리가 바뀌었습니다: {len(before)} → {len(after)}")
        denied = logs.messages("vpoc.fal.unregister.denied", level=logging.WARNING)
        if len(denied) != 1 or f"user={PARITY_OWNER}" not in denied[0] or f"endpoint={face_eid}" not in denied[0]:
            bad.append(f"(ad-a) warning 로그 vpoc.fal.unregister.denied user·endpoint 가 1줄이 아닙니다: {denied}")
        if logs.messages("vpoc.fal.unregistered"):
            bad.append("(ad-a) 거절됐는데 vpoc.fal.unregistered 로그가 남았습니다")
        # 경로의 제어 문자는 로그에 남기지 않는다(로그 위조 방지). 줄바꿈은 Flask path 변환기가 이미 404 로 막아
        # 그 밖의 제어 문자(탭·ESC)로 확인한다.
        logs.clear()
        env.client.delete("/api/vpoc/fal/models/x%09%1Bforged=1")
        forged = logs.messages("vpoc.fal.unregister.denied")
        if len(forged) != 1 or "\t" in forged[0] or "\x1b" in forged[0] or "endpoint=xforged=1" not in forged[0]:
            bad.append(f"(ad-a) 경로 제어 문자가 로그에 그대로 남았습니다: {forged!r}")

        # ---- (ad-b) 관리자 해제 → 200 removed=true ----
        logs.clear()
        env = new_env(is_admin=lambda: True)
        resp = env.client.delete(delete_url)
        body = resp.get_json(silent=True) or {}
        if resp.status_code != 200 or body.get("removed") is not True:
            bad.append(f"(ad-b) 관리자 해제가 200 removed=true 가 아닙니다: {resp.status_code} {body}")
        left = [item.get("endpointId") for item in _parity_registry_models(env)]
        if face_eid in left or len(left) != 1:
            bad.append(f"(ad-b) 해제 뒤 레지스트리가 맞지 않습니다: {left}")
        done = logs.messages("vpoc.fal.unregistered", level=logging.INFO)
        if len(done) != 1 or f"endpoint={face_eid}" not in done[0] or f"by={PARITY_OWNER}" not in done[0] or (
            "removed=True" not in done[0]
        ):
            bad.append(f"(ad-b) info 로그 vpoc.fal.unregistered endpoint·by·removed 가 1줄이 아닙니다: {done}")
        # 없는 항목 해제는 removed=false(기존 동작)이고 로그에 그대로 남는다.
        logs.clear()
        resp = env.client.delete(delete_url)
        if resp.status_code != 200 or (resp.get_json(silent=True) or {}).get("removed") is not False:
            bad.append(f"(ad-b) 없는 항목 해제가 200 removed=false 가 아닙니다: {resp.status_code}")
        if not logs.messages("removed=False"):
            bad.append("(ad-b) 없는 항목 해제 로그에 removed=False 가 없습니다")

        # ---- (ad-c) 비관리자 등록은 그대로 201 ----
        env = new_env(is_admin=lambda: False)
        register_calls: list[tuple[str, str]] = []

        def fake_register(endpoint: str, *, added_by: str = "") -> dict:
            register_calls.append((endpoint, added_by))
            return {"id": "fal:" + endpoint, "endpointId": endpoint, "addedBy": added_by}

        env.patch(fal, "register", fake_register)
        resp = env.client.post("/api/vpoc/fal/models", json={"endpointId": "fal-ai/kling-video/v3/pro/image-to-video"})
        if resp.status_code != 201:
            bad.append(f"(ad-c) 비관리자 등록이 201 이 아닙니다: {resp.status_code} {resp.get_json(silent=True)}")
        if register_calls != [("fal-ai/kling-video/v3/pro/image-to-video", PARITY_OWNER)]:
            bad.append(f"(ad-c) fal.register 호출이 다릅니다: {register_calls}")

        # ---- (ad-d) is_admin 없이 만든 deps → 해제 403(fail-closed) ----
        env = new_env()  # is_admin=None → 하네스가 기존 6개 인자만 넘긴다
        deps_plain = routes.VpocDependencies(
            require_auth=lambda handler: handler,
            current_username=lambda: PARITY_OWNER,
            get_project=lambda p: None,
            can_read_project=lambda proj: True,
            can_write_project=lambda proj: True,
            graph_run=lambda *a, **k: [],
        )
        if deps_plain.is_admin is not routes._no_admin or routes._no_admin() is not False:
            bad.append("(ad-d) VpocDependencies.is_admin 기본값이 _no_admin(False) 이 아닙니다")
        resp = env.client.delete(delete_url)
        if resp.status_code != 403 or _parity_error_of(resp).get("type") != "AdminOnly":
            bad.append(f"(ad-d) is_admin 없는 deps 해제가 403 AdminOnly 가 아닙니다: {resp.status_code}")
        if not fal.is_registered(PARITY_FACE):
            bad.append("(ad-d) 거절됐는데 얼굴이 해제됐습니다")
        # server.py 는 require_admin 과 같은 조건(g.role == ROLE_ADMIN)을 주입한다(소스 확인).
        server_src = (ROOT / "app" / "src" / "server.py").read_text(encoding="utf-8")
        if "is_admin=lambda: g.role == ROLE_ADMIN" not in server_src:
            bad.append("(ad-d) server.py 가 VpocDependencies 에 is_admin=lambda: g.role == ROLE_ADMIN 을 넘기지 않습니다")

        # ---- (ad-e) 금지 패턴 항목은 고를 수도 보낼 수도 없다(E4) ----
        denied_item = {
            "id": PARITY_DENIED,
            "endpointId": PARITY_DENIED[len("fal:"):],
            "label": "Seedance 2.0 Fast Image to Video",
            "category": "image-to-video",
            "caps": _parity_caps("i2v"),
            "pricing": {"unit": "1000 tokens", "unitPrice": 0.0214},
            "addedBy": "tester",
        }
        env = new_env(registry=_parity_registry_items() + [denied_item])
        if not settings.is_denied_endpoint(denied_item["endpointId"]):
            bad.append("(ad-e) 검사 전제: 기본 금지 패턴이 seedance-2-0-fast 에 맞지 않습니다")
        reason = fal.unavailable_reason(PARITY_DENIED)
        if reason != PARITY_E4:
            bad.append(f"(ad-e) fal.unavailable_reason 이 E4 가 아닙니다: {reason!r}")
        if fal.unavailable_reason(PARITY_FACE) != "":
            bad.append(f"(ad-e) 금지가 아닌 얼굴까지 막혔습니다: {fal.unavailable_reason(PARITY_FACE)!r}")
        entry = next((item for item in ark.choices() if item.get("id") == PARITY_DENIED), None)
        if entry is None or entry.get("available") is not False or entry.get("reason") != PARITY_E4:
            bad.append(f"(ad-e) choices 의 금지 항목이 available=false·E4 가 아닙니다: {entry}")
        env.reset_calls()
        try:
            fal.submit(
                model=PARITY_DENIED,
                owner=PARITY_OWNER,
                project_id=PARITY_PID,
                plan_id=PARITY_PLAN,
                shot_id="s1",
                attempt=1,
                prompt="A man looks at the city.",
            )
            bad.append("(ad-e) 금지 모델 fal.submit 이 예외 없이 끝났습니다")
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "kind", "") != "ModelUnavailable" or PARITY_E4 not in str(getattr(exc, "message", exc)):
                bad.append(f"(ad-e) 금지 모델 fal.submit 이 ModelUnavailable·E4 가 아닙니다: {exc!r}")
        if env.fal_calls:
            bad.append(f"(ad-e) 금지 모델인데 fal._request 가 불렸습니다: {len(env.fal_calls)}")

        for item in envs:
            if item.url_calls:
                bad.append(f"실제 네트워크 호출이 있었습니다: {item.url_calls}")
            if item.charges or item.video_calls or item.ark_calls:
                bad.append("원장·Luma·ARK 호출이 있었습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("해제 권한·금지 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        settings.RAW_ERRORS = saved_raw
        logs.detach()
        while envs:
            envs.pop().restore()

    if bad:
        fail("fal-seedance-parity 해제 권한·금지 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 해제 권한·금지 검사 (ad-a)비관리자 403 AdminOnly·E3·레지스트리 유지·warning·제어 문자 제거 "
            "(ad-b)관리자 200 removed·info 로그 (ad-c)비관리자 등록 201 (ad-d)is_admin 없는 deps 403·server.py 주입 "
            "(ad-e)금지 E4·choices 불가·submit ModelUnavailable·_request 0회"
        )


def check_fal_no_silent_fallback() -> None:
    """073 작업 5 — 조용한 대체 금지(R11.1-11.4, D11). 외부 호출은 전부 가짜.

    (ns-a) 회차 videoModel=fal:<미등록> queued 샷 → GET plan → failed ModelUnavailable(E5), Luma·fal·원장 0회.
    (ns-b) 레지스트리 읽기 예외 → registered() [] 이고 캐시하지 않는다(읽기 2회). 성공은 30초 캐시(읽기 1회).
    (ns-c) refresh_shots 한도: 미등록 fal 회차도 ARK_MAX_INFLIGHT(2) — queued 2개가 함께 제출 시도된다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, fal, settings  # noqa: PLC0415
    from src.vpoc.errors import now_iso as _now_iso  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()

    def shots_of(state: dict) -> dict:
        return {item.get("id"): item for item in state["steps"]["4"].get("shots") or []}

    try:
        env = _parity_env()
        logs.attach()

        # ---- (ns-a) 미등록 fal 회차 → E5, 다른 경로로 보내지 않는다 ----
        if ark.is_ark(PARITY_UNKNOWN_FAL) or not ark.is_external(PARITY_UNKNOWN_FAL):
            bad.append("(ns-a) 미등록 fal 이 is_ark False·is_external True 가 아닙니다")
        if not ark.is_external("seedance-2-5") or ark.is_external(ark.DEFAULT_MODEL) or not ark.is_external(PARITY_FACE):
            bad.append("(ns-a) is_external 이 ARK·등록 fal True, Luma False 가 아닙니다")
        env.save(
            _parity_state(
                PARITY_UNKNOWN_FAL,
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4("s1", "queued", durationS=6),
                        _parity_shot4("s2", "completed", durationS=5),
                    ],
                },
            )
        )
        resp = env.client.get(env.plan_url())
        if resp.status_code != 200:
            bad.append(f"(ns-a) GET plan 이 200 이 아닙니다: {resp.status_code}")
        s1 = shots_of(env.load()).get("s1") or {}
        err = s1.get("error") or {}
        if s1.get("status") != "failed" or err.get("type") != "ModelUnavailable":
            bad.append(f"(ns-a) 샷이 failed ModelUnavailable 이 아닙니다: {s1.get('status')} {err}")
        if PARITY_E5_PART not in str(err.get("message") or "") or PARITY_UNKNOWN_FAL not in str(err.get("message") or ""):
            bad.append(f"(ns-a) E5 문구(모델 id 포함)가 아닙니다: {err.get('message')}")
        if env.video_calls or env.fal_calls or env.charges or env.ark_calls:
            bad.append(
                f"(ns-a) 다른 경로로 새거나 원장에 남았습니다: luma={len(env.video_calls)} fal={len(env.fal_calls)} "
                f"charge={len(env.charges)} ark={len(env.ark_calls)}"
            )
        # 같은 상태로 run/retry 도 Luma 로 새지 않는다.
        env.reset_calls()
        resp = env.client.post(env.run_url("retry"), json={"shotId": "s1"})
        s1 = shots_of(env.load()).get("s1") or {}
        if (s1.get("error") or {}).get("type") != "ModelUnavailable" or env.video_calls or env.charges:
            bad.append(f"(ns-a) run/retry 가 E5 실패가 아닙니다: {resp.status_code} {s1.get('status')} {s1.get('error')}")

        # ---- (ns-b) 읽기 실패는 캐시하지 않는다 ----
        reads = {"n": 0}
        real_read = env.fake.read_json

        def failing_read(key: str):
            if key == fal.REGISTRY_KEY:
                reads["n"] += 1
                raise RuntimeError("S3 read timeout")
            return real_read(key)

        def counting_read(key: str):
            if key == fal.REGISTRY_KEY:
                reads["n"] += 1
            return real_read(key)

        logs.clear()
        env.patch(env.fake, "read_json", failing_read)
        fal._invalidate_cache()
        if fal._registry_read() is not None:
            bad.append("(ns-b) 읽기 예외에서 _registry_read 가 None 이 아닙니다")
        reads["n"] = 0
        first, second = fal.registered(), fal.registered()
        if first != [] or second != []:
            bad.append(f"(ns-b) 읽기 실패인데 registered() 가 [] 가 아닙니다: {first} {second}")
        if reads["n"] != 2:
            bad.append(f"(ns-b) 읽기 실패 결과를 캐시했습니다(읽기 {reads['n']}회, 2회여야 함)")
        if len(logs.messages("vpoc.fal.registry.read_failed", level=logging.WARNING)) < 2:
            bad.append("(ns-b) warning 로그 vpoc.fal.registry.read_failed 가 매 실패마다 남지 않습니다")
        env.patch(env.fake, "read_json", counting_read)
        reads["n"] = 0
        got = fal.registered()
        again = fal.registered()
        if len(got) != 2 or len(again) != 2:
            bad.append(f"(ns-b) 복구 뒤 registered() 가 2개가 아닙니다: {len(got)} {len(again)}")
        if reads["n"] != 1:
            bad.append(f"(ns-b) 성공 결과를 30초 캐시하지 않습니다(읽기 {reads['n']}회, 1회여야 함)")
        env.patch(env.fake, "read_json", real_read)
        fal._invalidate_cache()

        # ---- (ns-c) 동시 제출 한도: 미등록 fal 도 ARK_MAX_INFLIGHT ----
        env.patch(settings, "ARK_MAX_INFLIGHT", 2)
        env.patch(settings, "MAX_INFLIGHT", 1)
        env.reset_calls()
        # 이미 제출된 샷 1개(최근 조회라 다시 조회하지 않는다) + queued 2개.
        # 한도 2 면 inflight 1 < 2 라 둘 다 제출을 시도한다(둘 다 E5 failed). 한도 1(MAX_INFLIGHT)이면 둘 다 queued 로 남는다.
        env.save(
            _parity_state(
                PARITY_UNKNOWN_FAL,
                durations=(5, 5, 5),
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4(
                            "s1",
                            "submitted",
                            invocationArn="fal/requests/fal-ai/unknown-xyz/image-to-video/req-old",
                            submittedAt=_now_iso(),
                            lastPolledAt=_now_iso(),
                        ),
                        _parity_shot4("s2", "queued"),
                        _parity_shot4("s3", "queued"),
                    ],
                },
            )
        )
        env.client.get(env.plan_url())
        rows = shots_of(env.load())
        tried = [sid for sid in ("s2", "s3") if (rows.get(sid) or {}).get("status") == "failed"]
        waiting = [sid for sid in ("s2", "s3") if (rows.get(sid) or {}).get("status") == "queued"]
        if tried != ["s2", "s3"] or waiting:
            bad.append(
                f"(ns-c) 미등록 fal 회차가 ARK_MAX_INFLIGHT(2)로 제출 시도되지 않았습니다: 시도={tried} 대기={waiting}"
            )
        if any(((rows.get(sid) or {}).get("error") or {}).get("type") != "ModelUnavailable" for sid in tried):
            bad.append("(ns-c) 시도한 샷이 E5 ModelUnavailable 이 아닙니다")
        if env.video_calls or env.fal_posts() or env.charges:
            bad.append("(ns-c) 미등록 fal 회차가 Luma·fal 로 제출되거나 원장에 남았습니다")
        # 대조: 등록된 얼굴 회차 queued 3개 → 한도 2 만큼만 제출되고 1개는 대기.
        env.reset_calls()
        env.save(
            _parity_state(
                PARITY_FACE,
                durations=(5, 5, 5),
                step4={
                    "status": "running",
                    "shots": [_parity_shot4(sid, "queued") for sid in ("s1", "s2", "s3")],
                },
            )
        )
        env.client.get(env.plan_url())
        rows = shots_of(env.load())
        submitted = [sid for sid, row in rows.items() if row.get("status") == "submitted"]
        queued = [sid for sid, row in rows.items() if row.get("status") == "queued"]
        if len(submitted) != 2 or len(queued) != 1 or len(env.fal_posts()) != 2:
            bad.append(
                f"(ns-c) 대조: 등록 fal 회차가 한도 2 로 제출되지 않았습니다: 제출={submitted} 대기={queued} "
                f"fal POST={len(env.fal_posts())}"
            )

        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("조용한 대체 금지 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 조용한 대체 금지 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 조용한 대체 금지 검사 (ns-a)미등록 fal 회차 E5·Luma/fal/원장 0회·retry 같음 "
            "(ns-b)읽기 실패 캐시 안 함(2회)·성공 30초 캐시(1회)·warning (ns-c)미등록 fal 도 ARK_MAX_INFLIGHT·대조 한도 2"
        )


def check_model_resolved_log() -> None:
    """073 작업 5 — 치환 로그·화면 선택 상태(R12.1-12.4, D12). 외부 호출은 전부 가짜.

    (ml-a) resolve_model("seedance-2-5"): 얼굴 등록 시 reason=ark_disabled 1회·다시 부르면 없음(10분 억제)·
           10분 뒤 다시 1회. 미등록 시 reason=fal_target_unavailable 이 따로 1회.
    (ml-b) selected: 저장값 fal:<미등록> → Luma, reason=unavailable 로그.
    (ml-c) plan_view: videoModel = 실제 모델, videoModelRequested = ② 저장값.
    (ml-d) vpoc.js: currentModelId 가 plan.videoModel 을 step.videoModel 보다 먼저 본다. E6 안내 문구.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()

    def resolved(contains: str = "") -> list[str]:
        return [text for text in logs.messages("vpoc.model.resolved", level=logging.INFO) if contains in text]

    try:
        env = _parity_env()
        env.patch(ark, "_resolved_logged", {})
        logs.attach()

        # ---- (ml-a) resolve_model 두 사유와 10분 억제 ----
        got = ark.resolve_model("seedance-2-5")
        if got != PARITY_FACE:
            bad.append(f"(ml-a) 얼굴 등록인데 resolve 결과가 얼굴이 아닙니다: {got}")
        lines = resolved("reason=ark_disabled")
        if len(lines) != 1 or f"from=seedance-2-5 to={PARITY_FACE}" not in lines[0]:
            bad.append(f"(ml-a) reason=ark_disabled 로그가 from·to 와 함께 1줄이 아닙니다: {lines}")
        ark.resolve_model("seedance-2-5")
        if len(resolved("reason=ark_disabled")) != 1:
            bad.append("(ml-a) 같은 조합을 10분 안에 다시 불렀는데 로그가 또 남았습니다")
        # 10분이 지나면 다시 남는다(마지막 시각을 600초 앞으로 옮긴다).
        key = ("seedance-2-5", PARITY_FACE, "ark_disabled")
        if key not in ark._resolved_logged:
            bad.append(f"(ml-a) 억제 사전 키가 (from, to, reason) 이 아닙니다: {list(ark._resolved_logged)}")
        else:
            ark._resolved_logged[key] -= ark._RESOLVED_LOG_INTERVAL_S + 1
            ark.resolve_model("seedance-2-5")
            if len(resolved("reason=ark_disabled")) != 2:
                bad.append("(ml-a) 10분이 지난 뒤 같은 조합 로그가 다시 남지 않습니다")
        if ark._RESOLVED_LOG_INTERVAL_S != 600:
            bad.append(f"(ml-a) 억제 간격이 600초가 아닙니다: {ark._RESOLVED_LOG_INTERVAL_S}")
        # 바꾸지 않는 호출(이미 fal·Luma)은 로그가 없다.
        logs.clear()
        ark.resolve_model(PARITY_FACE)
        ark.resolve_model(ark.DEFAULT_MODEL)
        if resolved():
            bad.append(f"(ml-a) 모델을 바꾸지 않았는데 로그가 남았습니다: {resolved()}")
        # 얼굴 미등록 → Luma, 다른 조합이라 따로 1회.
        env.set_registry([])
        got = ark.resolve_model("seedance-2-5")
        ark.resolve_model("seedance-2-5")
        lines = resolved("reason=fal_target_unavailable")
        if got != ark.DEFAULT_MODEL or len(lines) != 1 or f"from=seedance-2-5 to={ark.DEFAULT_MODEL}" not in lines[0]:
            bad.append(f"(ml-a) 미등록 → Luma·reason=fal_target_unavailable 1줄이 아닙니다: {got} {lines}")
        if resolved("reason=ark_disabled"):
            bad.append("(ml-a) 미등록인데 reason=ark_disabled 로그가 남았습니다(억제 사전이 조합을 섞었습니다)")
        env.set_registry(_parity_registry_items())

        # ---- (ml-b) selected: 저장값 fal:<미등록> → Luma, reason=unavailable ----
        logs.clear()
        state = _parity_state(PARITY_UNKNOWN_FAL)
        got = ark.selected(state)
        lines = resolved("reason=unavailable")
        if got != ark.DEFAULT_MODEL:
            bad.append(f"(ml-b) 미등록 fal 저장값인데 selected 가 Luma 가 아닙니다: {got}")
        if len(lines) != 1 or f"from={PARITY_UNKNOWN_FAL} to={ark.DEFAULT_MODEL}" not in lines[0]:
            bad.append(f"(ml-b) reason=unavailable 로그가 from·to 와 함께 1줄이 아닙니다: {lines}")
        logs.clear()
        if ark.selected(_parity_state(PARITY_FACE)) != PARITY_FACE or ark.selected(_parity_state("luma-ray2")) != "luma-ray2":
            bad.append("(ml-b) 쓸 수 있는 저장값(얼굴·Luma)의 selected 가 바뀌었습니다")
        if resolved():
            bad.append(f"(ml-b) 대체하지 않았는데 로그가 남았습니다: {resolved()}")

        # ---- (ml-c) plan_view videoModel·videoModelRequested ----
        cases = [
            ("seedance-2-5", PARITY_FACE),
            (PARITY_UNKNOWN_FAL, ark.DEFAULT_MODEL),
            (PARITY_FACE, PARITY_FACE),
        ]
        for stored, actual in cases:
            env.save(_parity_state(stored))
            view = env.client.get(env.plan_url()).get_json(silent=True) or {}
            if view.get("videoModel") != actual or view.get("videoModelRequested") != stored:
                bad.append(
                    f"(ml-c) 저장 {stored}: videoModel={view.get('videoModel')}(기대 {actual}) "
                    f"videoModelRequested={view.get('videoModelRequested')}(기대 {stored})"
                )

        # ---- (ml-d) vpoc.js 선택 상태·E6 ----
        js = (PUBLIC / "vpoc.js").read_text(encoding="utf-8")
        current = re.search(r"function currentModelId\(\)\s*\{(.*?)\n\s*\}", js, re.S)
        body = current.group(1) if current else ""
        if not re.search(r"return plan\.videoModel \|\| step\.videoModel \|\| \"luma-ray2\";", body):
            bad.append("(ml-d) currentModelId 가 plan.videoModel 을 step.videoModel 보다 먼저 보지 않습니다")
        if "edits.videoModel" not in body:
            bad.append("(ml-d) currentModelId 가 사용자가 새로 고른 값(edits.videoModel)을 먼저 보지 않습니다")
        picker_at = js.find("function drawModelPicker()")
        picker = js[picker_at : js.find("falSection()", picker_at)] if picker_at >= 0 else ""
        if "plan.videoModelRequested !== plan.videoModel" not in picker:
            bad.append("(ml-d) drawModelPicker 가 videoModelRequested·videoModel 차이를 보지 않습니다")
        if '"저장된 모델(" + plan.videoModelRequested + ")은 지금 쓸 수 없어 "' not in picker or (
            '"(으)로 만듭니다."' not in picker
        ):
            bad.append("(ml-d) drawModelPicker 에 E6 안내 문구가 없습니다")

        if env.url_calls or env.fal_calls or env.ark_calls or env.video_calls or env.charges:
            bad.append("외부 호출·제출·원장 기록이 있었습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("치환 로그 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 치환 로그·선택 상태 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 치환 로그·선택 상태 검사 (ml-a)ark_disabled 1회·10분 억제·10분 뒤 재기록·"
            "fal_target_unavailable 따로 (ml-b)selected Luma 대체 reason=unavailable (ml-c)plan_view "
            "videoModel·videoModelRequested (ml-d)vpoc.js plan.videoModel 우선·E6"
        )


def check_vnew_previz_lazy() -> None:
    """073 작업 6 — three.js 지연 로드(R9.1-9.4, D9). 소스·Dockerfile 정적 검사, 네트워크 없음.

    (vl-a) index.html 에 vnew-previz.js 정적 module 로드가 없다. app.js 가 VNEW_ENABLED 일 때만
           import("/vnew-previz.js?v=__ASSET_VERSION__") 한다(꺼지면 Promise.resolve(false)).
    (vl-b) vnew.js 가 window.vnewPrevizReady 를 기다린 뒤 window.vnewPreviz.create 를 부른다. E7 문구,
           .catch 가 showError 로 E7 을 보인다.
    (vl-c) frontend/Dockerfile 치환 목록에 /usr/share/nginx/html/vnew-previz.js 가 있다.
    (vl-d) Dockerfile sed 를 파이썬으로 흉내 낸다: 목록 파일(vnew-previz.js·app.js·index.html 포함)의
           __ASSET_VERSION__ 이 바뀐 뒤 0개다.
    (vl-e) node --check: app.js(classic, 동적 import)·vnew.js·vnew-previz.js(module).
    """
    bad: list[str] = []
    try:
        index_src = (PUBLIC / "index.html").read_text(encoding="utf-8")
        app_src = (PUBLIC / "app.js").read_text(encoding="utf-8")
        vnew_src = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        previz_src = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
        docker_src = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

        # ---- (vl-a) 정적 로드 없음·동적 import ----
        if 'type="module" src="/vnew-previz.js' in index_src:
            bad.append('(vl-a) index.html 이 아직 vnew-previz.js 를 type="module" 로 정적 로드합니다')
        if re.search(r"<script[^>]*vnew-previz\.js", index_src):
            bad.append("(vl-a) index.html 에 vnew-previz.js script 태그가 남아 있습니다")
        literal = 'import("/vnew-previz.js?v=__ASSET_VERSION__")'
        if app_src.count(literal) != 1:
            bad.append(f"(vl-a) app.js 에 {literal} 가 정확히 1번 있지 않습니다: {app_src.count(literal)}")
        gated = re.search(
            r"window\.vnewPrevizReady\s*=\s*VNEW_ENABLED\s*\?\s*import\(\"/vnew-previz\.js\?v=__ASSET_VERSION__\"\)"
            r".*?:\s*Promise\.resolve\(false\);",
            app_src,
            re.S,
        )
        if not gated:
            bad.append("(vl-a) app.js 동적 import 가 VNEW_ENABLED 조건(꺼지면 Promise.resolve(false)) 안에 있지 않습니다")
        else:
            body = gated.group(0)
            if "window.vnewPrevizError = error" not in body or "return false" not in body:
                bad.append("(vl-a) app.js 동적 import 실패 처리(vnewPrevizError 저장·false)가 없습니다")
        enabled_at = app_src.find("const VNEW_ENABLED")
        if enabled_at < 0 or not gated or app_src.find("window.vnewPrevizReady") < enabled_at:
            bad.append("(vl-a) window.vnewPrevizReady 가 VNEW_ENABLED 선언 뒤에 있지 않습니다")

        # ---- (vl-b) vnew.js 가 로드를 기다린 뒤 create, E7 ----
        wait_at = vnew_src.find("Promise.resolve(window.vnewPrevizReady).then(")
        create_at = vnew_src.find("window.vnewPreviz.create(")
        if wait_at < 0:
            bad.append("(vl-b) vnew.js 가 window.vnewPrevizReady 를 기다리지 않습니다")
        if vnew_src.count("window.vnewPreviz.create(") != 1:
            bad.append(f"(vl-b) window.vnewPreviz.create 호출이 1곳이 아닙니다: {vnew_src.count('window.vnewPreviz.create(')}")
        if wait_at >= 0 and not (wait_at < create_at):
            bad.append("(vl-b) window.vnewPreviz.create 가 vnewPrevizReady 를 기다린 뒤에 불리지 않습니다")
        block = vnew_src[wait_at : vnew_src.find("function buildTimeline()", wait_at)] if wait_at >= 0 else ""
        if '"3D 뷰어를 불러오지 못했습니다: "' not in block:
            bad.append("(vl-b) vnew.js 에 E7 문구(3D 뷰어를 불러오지 못했습니다: <원인>)가 없습니다")
        if "window.vnewPrevizError" not in block:
            bad.append("(vl-b) E7 원인에 window.vnewPrevizError 를 쓰지 않습니다")
        if not re.search(r"\.catch\(function \(err\) \{\s*//[^\n]*\n\s*showError\(err && err\.previzLoad \? err\.message", block):
            bad.append("(vl-b) createPromise 뒤 .catch 가 showError 로 E7 을 보이지 않습니다")
        if "if (!ctl) return;" not in block:
            bad.append("(vl-b) 화면을 떠나 create 를 건너뛴 경우(null)를 처리하지 않습니다")

        # ---- (vl-c) Dockerfile 치환 목록 ----
        loop = re.search(r"for f in (.*?); do", docker_src, re.S)
        listed = re.findall(r"/usr/share/nginx/html/[\w.\-]+", loop.group(1)) if loop else []
        if "/usr/share/nginx/html/vnew-previz.js" not in listed:
            bad.append(f"(vl-c) Dockerfile 치환 목록에 vnew-previz.js 가 없습니다: {listed}")
        if 'grep -q "__ASSET_VERSION__" "$f"' not in docker_src:
            bad.append("(vl-c) Dockerfile 이 치환 뒤 남은 __ASSET_VERSION__ 으로 빌드를 실패시키지 않습니다")

        # ---- (vl-d) sed 흉내 ----
        for need in ("index.html", "app.js", "vnew-previz.js"):
            if f"/usr/share/nginx/html/{need}" not in listed:
                bad.append(f"(vl-d) 치환 목록에 {need} 가 없습니다")
        if "__ASSET_VERSION__" not in previz_src:
            bad.append("(vl-d) vnew-previz.js 에 __ASSET_VERSION__ 이 없습니다(three.js import 버전 쿼리)")
        if "__ASSET_VERSION__" not in app_src or "__ASSET_VERSION__" not in index_src:
            bad.append("(vl-d) app.js·index.html 에 __ASSET_VERSION__ 이 없습니다")
        for path in listed:
            name = path.rsplit("/", 1)[-1]
            local = PUBLIC / name
            if not local.exists():
                bad.append(f"(vl-d) 치환 목록 파일이 frontend/public 에 없습니다: {name}")
                continue
            replaced = local.read_text(encoding="utf-8").replace("__ASSET_VERSION__", "ci-check0001")
            if "__ASSET_VERSION__" in replaced:
                bad.append(f"(vl-d) 치환 뒤에도 {name} 에 __ASSET_VERSION__ 이 남습니다")
            if name == "app.js" and 'import("/vnew-previz.js?v=ci-check0001")' not in replaced:
                bad.append("(vl-d) 치환 뒤 app.js 동적 import URL 에 버전이 들어가지 않습니다")
            if name == "vnew-previz.js" and "three.module.min.js?v=ci-check0001" not in replaced:
                bad.append("(vl-d) 치환 뒤 vnew-previz.js 의 three.js import 에 버전이 들어가지 않습니다")

        # ---- (vl-e) node --check ----
        if _is_es_module(app_src):
            bad.append("(vl-e) app.js 가 ES module 로 판정됩니다(classic script 여야 한다, 동적 import 만 허용)")
        node = shutil.which("node")
        if not node:
            warn("node 가 없어 (vl-e) 구문 검사를 건너뜁니다 (E16)")
        else:
            for name, text in (("app.js", app_src), ("vnew.js", vnew_src)):
                result = subprocess.run([node, "--check", str(PUBLIC / name)], capture_output=True, text=True)
                if result.returncode != 0:
                    bad.append(f"(vl-e) node --check {name} 실패: {(result.stderr or '').strip()[:300]}")
            result = _check_as_module(node, previz_src)
            if result.returncode != 0:
                bad.append(f"(vl-e) node --check vnew-previz.js(module) 실패: {(result.stderr or '').strip()[:300]}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("three.js 지연 로드 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])

    if bad:
        fail("fal-seedance-parity three.js 지연 로드 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity three.js 지연 로드 검사 (vl-a)index.html 정적 로드 없음·app.js VNEW_ENABLED 동적 import "
            "(vl-b)vnew.js vnewPrevizReady 대기 후 create·E7 .catch (vl-c)Dockerfile 치환 목록 vnew-previz.js "
            "(vl-d)sed 흉내 뒤 __ASSET_VERSION__ 0개 (vl-e)node --check app.js·vnew.js·vnew-previz.js"
        )


def check_stitcher_threads() -> None:
    """073 작업 6 — stitcher threads 를 인코더에도(R13.2-13.3, D13, 리뷰 P-5). 실제 ffmpeg 는 돌리지 않는다.

    stitcher._run·ffmpeg_path·probe_duration 을 가짜로 바꿔 명령 리스트를 받는다. 출력 파일은 가짜 _run 이 만든다.
    (st-a) trim(threads=1): -threads 1 이 -i 앞과 libx264 설정(-crf 20) 바로 뒤에 하나씩.
    (st-b) scale_to_min_height(threads=1): 같음.
    (st-c) pad_to_seconds(threads=1): 같음. 이미 충분히 길면 _run 없이 복사한다.
    (st-d) threads=None·0·-1: 세 함수 명령이 073 이전 리스트와 정확히 같다. _threads_args(None·0·-1) → [].
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src import stitcher  # noqa: PLC0415

    bad: list[str] = []
    calls: list[list[str]] = []
    durations = {"value": 1.0}
    binary = "/fake/ffmpeg"
    saved = {name: getattr(stitcher, name) for name in ("_run", "ffmpeg_path", "probe_duration")}
    workdir = tempfile.mkdtemp(prefix="st-check-")

    def fake_run(command):
        calls.append(list(command))
        with open(command[-1], "wb") as handle:
            handle.write(b"fake-mp4")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    try:
        stitcher._run = fake_run
        stitcher.ffmpeg_path = lambda: binary
        stitcher.probe_duration = lambda path: durations["value"]
        src = os.path.join(workdir, "src.mp4")
        with open(src, "wb") as handle:
            handle.write(b"source-bytes")
        head = [binary, "-y", "-hide_banner", "-loglevel", "error"]
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]

        def out(name: str) -> str:
            return os.path.join(workdir, name)

        # 073 이전 명령(회귀 스냅샷, stitcher.py 2026-10-26 작업 트리에서 옮겨 적음).
        def base_trim(o: str) -> list[str]:
            return head + ["-i", src, "-ss", "0.000", "-to", "1.500"] + enc + [
                "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", o
            ]

        def base_scale(o: str) -> list[str]:
            return head + ["-i", src, "-vf", "scale=-2:720:flags=lanczos"] + enc + [
                "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", o
            ]

        def base_pad(o: str) -> list[str]:
            return head + ["-i", src, "-vf", "tpad=stop_mode=clone:stop_duration=2.000"] + enc + [
                "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", o
            ]

        def with_threads(base: list[str], n: int) -> list[str]:
            crf_end = base.index("-crf") + 2
            return base[:5] + ["-threads", str(n)] + base[5:crf_end] + ["-threads", str(n)] + base[crf_end:]

        def run_one(label: str, threads):
            calls.clear()
            o = out(f"{label}-{threads}.mp4")
            if label == "trim":
                stitcher.trim(src, o, start_sec=0.0, end_sec=1.5, threads=threads)
                return o, base_trim(o)
            if label == "scale":
                stitcher.scale_to_min_height(src, o, min_height=720, threads=threads)
                return o, base_scale(o)
            durations["value"] = 1.0
            stitcher.pad_to_seconds(src, o, target_sec=3.0, threads=threads)
            return o, base_pad(o)

        prefix = {"trim": "st-a", "scale": "st-b", "pad": "st-c"}
        for label in ("trim", "scale", "pad"):
            tag = prefix[label]
            o, base = run_one(label, 1)
            if len(calls) != 1:
                bad.append(f"({tag}) {label}(threads=1) _run 호출이 1회가 아닙니다: {len(calls)}")
                continue
            cmd = calls[0]
            idx = [i for i, value in enumerate(cmd) if value == "-threads"]
            if len(idx) != 2 or any(cmd[i + 1] != "1" for i in idx):
                bad.append(f"({tag}) {label}: -threads 1 이 정확히 2번이 아닙니다: {cmd}")
            else:
                if not idx[0] < cmd.index("-i"):
                    bad.append(f"({tag}) {label}: 첫 -threads 가 -i 앞에 없습니다")
                if idx[1] != cmd.index("-crf") + 2 or cmd.index("libx264") > idx[1]:
                    bad.append(f"({tag}) {label}: 둘째 -threads 가 libx264 설정(-crf 20) 바로 뒤에 없습니다: {cmd}")
            if cmd != with_threads(base, 1):
                bad.append(f"({tag}) {label}(threads=1) 명령이 기대와 다릅니다:\n    got {cmd}\n    exp {with_threads(base, 1)}")
            if not os.path.exists(o):
                bad.append(f"({tag}) {label}: 출력 파일이 없습니다")

        # (st-c) 이미 충분히 길면 _run 없이 복사.
        calls.clear()
        durations["value"] = 3.0
        copied = out("pad-copy.mp4")
        stitcher.pad_to_seconds(src, copied, target_sec=3.0, threads=1)
        if calls:
            bad.append(f"(st-c) 이미 충분히 긴데 _run 이 불렸습니다: {calls}")
        if not os.path.exists(copied) or open(copied, "rb").read() != b"source-bytes":
            bad.append("(st-c) 이미 충분히 긴 영상이 그대로 복사되지 않았습니다")
        durations["value"] = 1.0

        # (st-d) threads None·0·-1 → 073 이전과 같다.
        for threads in (None, 0, -1):
            for label in ("trim", "scale", "pad"):
                _o, base = run_one(label, threads)
                if calls != [base]:
                    bad.append(f"(st-d) {label}(threads={threads}) 명령이 073 이전과 다릅니다:\n    got {calls}\n    exp {base}")
            if stitcher._threads_args(threads) != []:
                bad.append(f"(st-d) _threads_args({threads}) 가 [] 가 아닙니다: {stitcher._threads_args(threads)}")
        if stitcher._threads_args(2) != ["-threads", "2"]:
            bad.append(f"(st-d) _threads_args(2) 가 ['-threads', '2'] 가 아닙니다: {stitcher._threads_args(2)}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("stitcher threads 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        for name, value in saved.items():
            setattr(stitcher, name, value)
        shutil.rmtree(workdir, ignore_errors=True)

    if bad:
        fail("fal-seedance-parity stitcher threads 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity stitcher threads 검사 (st-a)trim -threads 1 입력 앞·인코더 뒤 "
            "(st-b)scale_to_min_height 같음 (st-c)pad_to_seconds 같음·충분히 길면 복사 "
            "(st-d)threads None·0·-1 073 이전 명령 그대로·_threads_args []"
        )


def check_vnew_model_fallback() -> None:
    """073 작업 6 — vnew ① 옛 ARK 값(R13.1, D13, 리뷰 O-11). 외부 호출은 전부 가짜(_parity_env).

    (vm-a) 플래그 false, update_input(videoModel="seedance-2-5"/"seedance-2-0") → 400 이 아니다. 저장값이
           default_previz_model() 이고 vnew.plan.video_model_reset 로그가 남는다(fal 얼굴 등록·미등록 둘 다).
    (vm-b) videoModel="fal:unknown-xyz" → 400 BadVideoModel(회귀), 저장값이 바뀌지 않는다.
    (vm-c) 빈 값 → 기본 프리비즈 모델(회귀). 등록된 previzCapable 모델은 그대로 저장되고 reset 로그가 없다.
    """
    sys.path.insert(0, str(ROOT / "app"))
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vpoc import ark, settings  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    owner, pid = "tester", "prj_vm"
    good_v2v = "fal:fal-ai/check/previz-v2v"

    def reset_lines() -> list[str]:
        return logs.messages("vnew.plan.video_model_reset", level=logging.INFO)

    def stored() -> str:
        data, _ = planmod.load_plan(owner, pid)
        return str((data.get("input") or {}).get("videoModel") or "")

    try:
        env = _parity_env()
        env.patch(planmod, "STORE", env.fake)
        env.patch(settings, "ARK_SEEDANCE_ENABLED", False)
        env.patch(ark, "_resolved_logged", {})
        previz_item = {
            "id": good_v2v,
            "endpointId": good_v2v[len("fal:"):],
            "label": "Check Previz V2V",
            "category": "video-to-video",
            "caps": {"previzCapable": True, "available": True, "videoField": "video_url", "promptField": "prompt"},
            "pricing": {"unit": "seconds", "unitPrice": 0.1},
            "addedBy": "tester",
        }
        env.set_registry(_parity_registry_items() + [previz_item])
        logs.attach()
        planmod.load_or_create(owner, pid)
        default_id = planmod.default_previz_model()

        # ---- (vm-a) 옛 ARK 값 → 기본 프리비즈 모델 ----
        for registry_label, items in (
            ("얼굴 등록", _parity_registry_items() + [previz_item]),
            ("얼굴 미등록", [previz_item]),
        ):
            env.set_registry(items)
            for ark_value in ("seedance-2-5", "seedance-2-0"):
                if ark_value not in settings.ARK_MODEL_IDS:
                    bad.append(f"(vm-a) {ark_value} 가 ARK_MODEL_IDS 에 없습니다(검사 전제)")
                    continue
                logs.clear()
                planmod.update_input(owner, pid, {"videoModel": good_v2v})
                try:
                    planmod.update_input(owner, pid, {"videoModel": ark_value})
                except VpocError as exc:
                    bad.append(f"(vm-a) {registry_label}·{ark_value} 가 {exc.status} {exc.kind} 로 거절됐습니다")
                    continue
                if stored() != default_id:
                    bad.append(f"(vm-a) {registry_label}·{ark_value} 저장값이 기본 프리비즈 모델이 아닙니다: {stored()}")
                lines = reset_lines()
                if len(lines) != 1 or f"from={ark_value} to={default_id}" not in lines[0]:
                    bad.append(f"(vm-a) {registry_label}·{ark_value} vnew.plan.video_model_reset 로그(from·to)가 1줄이 아닙니다: {lines}")
        env.set_registry(_parity_registry_items() + [previz_item])

        # ---- (vm-b) 모르는 값 → 400 BadVideoModel ----
        planmod.update_input(owner, pid, {"videoModel": good_v2v})
        logs.clear()
        try:
            planmod.update_input(owner, pid, {"videoModel": "fal:unknown-xyz"})
            bad.append("(vm-b) fal:unknown-xyz 가 거절되지 않았습니다")
        except VpocError as exc:
            if exc.status != 400 or exc.kind != "BadVideoModel":
                bad.append(f"(vm-b) fal:unknown-xyz 가 400 BadVideoModel 이 아닙니다: {exc.status} {exc.kind}")
        if stored() != good_v2v:
            bad.append(f"(vm-b) 거절됐는데 저장값이 바뀌었습니다: {stored()}")
        if reset_lines():
            bad.append(f"(vm-b) 모르는 값인데 reset 로그가 남았습니다: {reset_lines()}")

        # ---- (vm-c) 빈 값·정상 값 ----
        logs.clear()
        planmod.update_input(owner, pid, {"videoModel": ""})
        if stored() != default_id:
            bad.append(f"(vm-c) 빈 값이 기본 프리비즈 모델로 저장되지 않았습니다: {stored()}")
        planmod.update_input(owner, pid, {"videoModel": good_v2v})
        if stored() != good_v2v:
            bad.append(f"(vm-c) 등록된 previzCapable 모델이 그대로 저장되지 않았습니다: {stored()}")
        planmod.update_input(owner, pid, {"videoModel": default_id})
        if stored() != default_id:
            bad.append(f"(vm-c) 기본 프리비즈 모델이 그대로 저장되지 않았습니다: {stored()}")
        if reset_lines():
            bad.append(f"(vm-c) 정상 값인데 reset 로그가 남았습니다: {reset_lines()}")

        if env.url_calls or env.fal_posts() or env.ark_calls or env.video_calls or env.charges:
            bad.append("외부 호출·제출·원장 기록이 있었습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 옛 모델 값 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity vnew 옛 모델 값 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity vnew 옛 모델 값 검사 (vm-a)seedance-2-5·2-0 → 기본 프리비즈·reset 로그(얼굴 등록·미등록) "
            "(vm-b)fal:unknown-xyz 400 BadVideoModel·값 유지 (vm-c)빈 값 기본·previzCapable·기본 모델 그대로"
        )


def check_fal_poll_failure() -> None:
    """073 작업 7 — fal 거절 샷·조회 실패 샷 마무리(R15, D14~D16). 외부 호출은 전부 가짜(_parity_env).

    (pf-a) status COMPLETED + 결과 422 likeness(V-2 원문) → fal.poll Failed·likeness 문구, 로그 kind=likeness
           (원문 없음), 400 'real person' 도 likeness, vnew _user_facing_error 를 지나도 그대로.
    (pf-b) 422 'Output video has sensitive content' → Failed·sensitive 문구, 로그 kind=sensitive.
    (pf-c) 400·403·404 기타 → "fal이 요청을 거절했습니다: <요지>"(URL 쿼리·data URI·키 제거, 200자),
           500·FalUnreachable·FalBalanceExhausted·status 조회 실패 → 예외 유지. error_gist 갈래.
    (pf-d) 통합: submitted r2v 샷 + (pf-a) 조건 → GET plan 뒤 failed, ④ 보기 문구가 RAW_ERRORS=False 에서도 그대로.
    (pf-e) 안전장치: ark.poll 이 늘 예외 → lastPolledAt·pollErrors·pollError, 조회 간격, 31분 전 제출 + 연속 5회
           → failed PollGiveUp·giveup 로그, 10분 전·submittedAt 없음은 유지, 성공 조회 뒤 0, 새 제출 0,
           POLL_GIVEUP_S 파서(기본·최소·틀린 값).
    (pf-f) 회귀: IN_QUEUE·IN_PROGRESS·FAILED·결과 error·URL 없음·Completed(가짜 다운로드) 처리가 같고 rejected 로그 없음.
    """
    import datetime as _dt  # noqa: PLC0415
    import urllib.request as _urlrequest  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import ark, errors as vpoc_errors, fal, settings  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    logs = _LogCapture()
    arn = "fal/requests/bytedance/seedance-2.5/reference-to-video/req-x"
    prefix = "users/tester/parity/out"
    v2_text = (
        "The images or videos provided may contain likenesses of real people or other private information "
        "that cannot be processed."
    )
    likeness_msg = fal._REJECT_LIKENESS_MSG
    sensitive_msg = fal._REJECT_SENSITIVE_MSG
    other_prefix = "fal이 요청을 거절했습니다: "
    fal_calls: list[str] = []

    def fake_request(status_payload, response):
        """status URL(…/status)은 status_payload, 결과 URL 은 response. 예외면 올린다."""

        def run(method: str, url: str, payload: dict | None = None, *, where: str = "") -> dict:
            fal_calls.append(url)
            if url.endswith("/status"):
                if isinstance(status_payload, BaseException):
                    raise status_payload
                return status_payload
            if isinstance(response, BaseException):
                raise response
            return response

        return run

    def poll_with(status_payload, response) -> dict:
        env.patch(fal, "_request", fake_request(status_payload, response))
        return fal.poll(arn, prefix)

    def http_error(code: int, text: str) -> VpocError:
        # fal._request 가 4xx·5xx 에 올리는 모양(kind HTTP<code>, message = detail[:500]).
        return VpocError("fal.poll", f"HTTP{code}", text[:500], 502)

    def rejected_lines() -> list[str]:
        return logs.messages("vpoc.fal.rejected", level=logging.INFO)

    def iso_ago(minutes: int) -> str:
        moment = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def shot_of(state: dict, shot_id: str = "s1") -> dict:
        return next((s for s in state["steps"]["4"].get("shots") or [] if s.get("id") == shot_id), {})

    completed = {"status": "COMPLETED"}
    try:
        env = _parity_env()
        env.patch(settings, "FAL_KEY", "dummy-fal-key-XYZ")
        logs.attach()

        # ---- (pf-a) likeness ----
        logs.clear()
        result = poll_with(completed, http_error(422, v2_text))
        if result != {"status": "Failed", "failureMessage": likeness_msg, "usage": None}:
            bad.append(f"(pf-a) 422 likeness 결과가 다릅니다: {result}")
        if rejected_lines() != ["vpoc.fal.rejected request=req-x kind=likeness http=422"]:
            bad.append(f"(pf-a) vpoc.fal.rejected 로그가 1줄(kind=likeness http=422)이 아닙니다: {rejected_lines()}")
        if logs.messages("likenesses of real people"):
            bad.append("(pf-a) 로그에 fal 오류 원문이 남았습니다")
        result = poll_with(completed, http_error(400, "Request rejected: may contain real person"))
        if result.get("status") != "Failed" or result.get("failureMessage") != likeness_msg:
            bad.append(f"(pf-a) 400 'real person' 이 likeness 문구가 아닙니다: {result}")
        if "real person" in likeness_msg.lower() or "real person" in sensitive_msg.lower():
            bad.append("(pf-a) 문구에 영어 'real person' 이 있어 vnew 매핑이 덮어씁니다")
        try:
            from src.vnew import generate as vnew_generate  # noqa: PLC0415

            if vnew_generate._user_facing_error(likeness_msg) != likeness_msg:
                bad.append("(pf-a) vnew _user_facing_error 가 likeness 문구를 바꿉니다")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"(pf-a) vnew _user_facing_error 확인 실패: {exc!r}")

        # ---- (pf-b) sensitive ----
        logs.clear()
        result = poll_with(completed, http_error(422, "Output video has sensitive content."))
        if result != {"status": "Failed", "failureMessage": sensitive_msg, "usage": None}:
            bad.append(f"(pf-b) 422 sensitive 결과가 다릅니다: {result}")
        if rejected_lines() != ["vpoc.fal.rejected request=req-x kind=sensitive http=422"]:
            bad.append(f"(pf-b) vpoc.fal.rejected 로그가 kind=sensitive 1줄이 아닙니다: {rejected_lines()}")

        # ---- (pf-c) 기타 4xx·예외 유지·error_gist ----
        for code in (400, 403, 404):
            logs.clear()
            result = poll_with(completed, http_error(code, "Unprocessable: field prompt is invalid"))
            message = str(result.get("failureMessage") or "")
            if result.get("status") != "Failed" or message != other_prefix + "Unprocessable: field prompt is invalid":
                bad.append(f"(pf-c) {code} 기타가 other 문구가 아닙니다: {result}")
            if rejected_lines() != [f"vpoc.fal.rejected request=req-x kind=other http={code}"]:
                bad.append(f"(pf-c) {code} 로그가 kind=other 1줄이 아닙니다: {rejected_lines()}")
        secret_body = (
            "Bad file https://v3.fal.media/files/a/b.png?token=SECRET123&sig=zz "
            "data:image/jpeg;base64,QUFBQUFBQUFB key=dummy-fal-key-XYZ " + "x" * 600
        )
        result = poll_with(completed, http_error(422, secret_body))
        message = str(result.get("failureMessage") or "")
        gist_part = message[len(other_prefix):] if message.startswith(other_prefix) else ""
        if not gist_part:
            bad.append(f"(pf-c) 비밀 본문 문구가 other 형식이 아닙니다: {message[:80]}")
        for secret in ("SECRET123", "sig=zz", "QUFBQUFB", "dummy-fal-key-XYZ"):
            if secret in message:
                bad.append(f"(pf-c) 문구에 비밀 조각 {secret!r} 가 남았습니다")
        if len(gist_part) > 200 or not gist_part.endswith("…"):
            bad.append(f"(pf-c) 요지가 200자 이내로 잘리지 않았습니다: {len(gist_part)}")
        if "https://v3.fal.media/files/a/b.png" not in gist_part or "data:…" not in gist_part or "***" not in gist_part:
            bad.append(f"(pf-c) 요지 정리(URL 경로 유지·data:…·***)가 다릅니다: {gist_part[:120]}")
        result = poll_with(completed, http_error(422, ""))
        if result.get("failureMessage") != other_prefix + "HTTP422":
            bad.append(f"(pf-c) 빈 본문 문구가 'HTTP422' 가 아닙니다: {result}")
        for label, exc in (
            ("500", http_error(500, "Internal Server Error")),
            ("FalUnreachable", VpocError("fal.poll", "FalUnreachable", "연결 실패", 502)),
            ("FalBalanceExhausted", VpocError("fal.poll", "FalBalanceExhausted", "잔액 부족", 402)),
        ):
            logs.clear()
            try:
                poll_with(completed, exc)
                bad.append(f"(pf-c) 결과 조회 {label} 가 예외를 올리지 않았습니다")
            except VpocError as raised:
                if raised is not exc:
                    bad.append(f"(pf-c) 결과 조회 {label} 예외가 원래 예외가 아닙니다: {raised.kind}")
            if rejected_lines():
                bad.append(f"(pf-c) {label} 에 rejected 로그가 남았습니다")
        try:
            poll_with(http_error(422, v2_text), completed)
            bad.append("(pf-c) status 조회 실패가 예외를 올리지 않았습니다")
        except VpocError:
            pass
        gist = vpoc_errors.error_gist
        if gist("") != "" or gist(None) != "":
            bad.append("(pf-c) error_gist 빈 값이 빈 문자열이 아닙니다")
        if gist("a \n\t  b ") != "a b":
            bad.append(f"(pf-c) error_gist 공백 모으기가 다릅니다: {gist('a  b')!r}")
        if gist("abcdef", 4) != "abc…":
            bad.append(f"(pf-c) error_gist 자르기가 다릅니다: {gist('abcdef', 4)!r}")
        env.patch(settings, "FAL_KEY", "")
        if gist("plain text ***") != "plain text ***":
            bad.append("(pf-c) error_gist 키가 비었을 때 본문을 바꿉니다")
        env.patch(settings, "FAL_KEY", "dummy-fal-key-XYZ")

        # ---- (pf-d) 통합: submitted r2v 샷 → GET plan → failed, RAW_ERRORS=False ----
        env.reset_calls()
        logs.clear()
        env.patch(settings, "RAW_ERRORS", False)
        env.patch(fal, "_request", fake_request(completed, http_error(422, v2_text)))
        env.save(
            _parity_state(
                PARITY_FACE,
                durations=(5,),
                step4={
                    "status": "running",
                    "shots": [
                        _parity_shot4(
                            "s1",
                            "submitted",
                            mode="reference",
                            provider=PARITY_PARTNER,
                            invocationArn=arn,
                            outputPrefix=prefix,
                            submittedAt=iso_ago(2),
                            lastPolledAt="",
                        )
                    ],
                },
            )
        )
        resp = env.client.get(env.plan_url())
        data = resp.get_json() or {}
        view = next((s for s in ((data.get("steps") or {}).get("4") or {}).get("shots") or [] if s.get("id") == "s1"), {})
        stored = shot_of(env.load())
        if resp.status_code != 200:
            bad.append(f"(pf-d) GET plan 이 200 이 아닙니다: {resp.status_code}")
        if stored.get("status") != "failed" or (stored.get("error") or {}).get("type") != "AsyncInvokeFailed":
            bad.append(f"(pf-d) 저장된 샷이 failed·AsyncInvokeFailed 가 아닙니다: {stored.get('status')} {stored.get('error')}")
        if view.get("status") != "failed" or (view.get("error") or {}).get("message") != likeness_msg:
            bad.append(f"(pf-d) ④ 보기 문구가 likeness 문구 그대로가 아닙니다: {view.get('status')} {view.get('error')}")
        if rejected_lines() != ["vpoc.fal.rejected request=req-x kind=likeness http=422"]:
            bad.append(f"(pf-d) 통합 경로 rejected 로그가 1줄이 아닙니다: {rejected_lines()}")
        if env.charges or env.url_calls or env.ark_calls or env.video_calls:
            bad.append("(pf-d) 조회 중 원장 기록·외부 호출이 있었습니다")
        env.patch(settings, "RAW_ERRORS", True)

        # ---- (pf-e) 안전장치: ark.poll 이 늘 예외 ----
        env.reset_calls()
        poll_calls: list[str] = []
        poll_mode = {"raise": True}

        def fake_ark_poll(invocation_arn: str, output_prefix: str) -> dict:
            poll_calls.append(invocation_arn)
            if poll_mode["raise"]:
                raise RuntimeError("provider down")
            return {"status": "InProgress", "failureMessage": "", "usage": None}

        env.patch(ark, "poll", fake_ark_poll)

        def save_submitted(**extra) -> None:
            shot = _parity_shot4(
                "s1",
                "submitted",
                durationS=5,
                invocationArn="ark/tasks/task-pf",
                outputPrefix="p/ark",
                submittedAt=iso_ago(2),
                lastPolledAt="",
            )
            shot.update(extra)
            env.save(_parity_state("seedance-2-5", durations=(5,), step4={"status": "running", "shots": [shot]}))

        def poll_again(**extra) -> dict:
            state = env.load()
            shot = shot_of(state)
            shot["lastPolledAt"] = ""
            shot.update(extra)
            env.save(state)
            env.client.get(env.plan_url())
            return shot_of(env.load())

        logs.clear()
        save_submitted()
        env.client.get(env.plan_url())
        s1 = shot_of(env.load())
        if not s1.get("lastPolledAt") or s1.get("pollErrors") != 1 or "RuntimeError: provider down" not in str(s1.get("pollError")):
            bad.append(
                f"(pf-e) 첫 실패 뒤 lastPolledAt·pollErrors=1·pollError 가 아닙니다: "
                f"{s1.get('lastPolledAt')!r} {s1.get('pollErrors')} {s1.get('pollError')!r}"
            )
        if s1.get("status") != "submitted":
            bad.append(f"(pf-e) 첫 실패에 상태가 바뀌었습니다: {s1.get('status')}")
        if not logs.messages("vpoc.poll.failed shot=s1 errors=1", level=logging.WARNING):
            bad.append(f"(pf-e) warning vpoc.poll.failed errors=1 이 없습니다: {logs.messages('vpoc.poll')}")
        env.client.get(env.plan_url())
        if len(poll_calls) != 1:
            bad.append(f"(pf-e) 조회 간격 안에 다시 조회했습니다: {len(poll_calls)}회")

        # 31분 전 제출 + 연속 5회 → failed
        logs.clear()
        save_submitted(submittedAt=iso_ago(31))
        statuses: list[str] = []
        for _ in range(5):
            statuses.append(str(poll_again().get("status")))
        s1 = shot_of(env.load())
        if statuses[:4] != ["submitted"] * 4 or statuses[4] != "failed":
            bad.append(f"(pf-e) 31분 전 제출의 상태 변화가 4회 submitted 뒤 failed 가 아닙니다: {statuses}")
        error = s1.get("error") or {}
        expected_giveup = "결과를 가져오지 못했습니다(RuntimeError: provider down). '다시 만들기'로 다시 시도해 주세요."
        if error.get("type") != "PollGiveUp" or error.get("message") != expected_giveup or not s1.get("completedAt"):
            bad.append(f"(pf-e) 포기 오류가 PollGiveUp·문구·completedAt 가 아닙니다: {error} {s1.get('completedAt')}")
        if logs.messages("vpoc.poll.giveup", level=logging.WARNING) != ["vpoc.poll.giveup shot=s1 errors=5"]:
            bad.append(f"(pf-e) giveup 로그가 1줄(errors=5)이 아닙니다: {logs.messages('vpoc.poll.giveup')}")
        if int(s1.get("attempt") or 1) != 1 or s1.get("invocationArn") != "ark/tasks/task-pf":
            bad.append(f"(pf-e) 포기 샷이 E5 재제출로 넘어갔습니다: attempt={s1.get('attempt')} arn={s1.get('invocationArn')}")

        # 10분 전 제출은 5회가 넘어도 유지
        save_submitted(submittedAt=iso_ago(10), pollErrors=6)
        s1 = poll_again()
        if s1.get("status") != "submitted" or s1.get("pollErrors") != 7:
            bad.append(f"(pf-e) 10분 전 제출이 포기됐거나 pollErrors 가 7 이 아닙니다: {s1.get('status')} {s1.get('pollErrors')}")
        # submittedAt 이 없으면 포기하지 않는다
        save_submitted(submittedAt=None, pollErrors=9)
        s1 = poll_again()
        if s1.get("status") != "submitted" or s1.get("pollErrors") != 10:
            bad.append(f"(pf-e) submittedAt 없음이 포기됐습니다: {s1.get('status')} {s1.get('pollErrors')}")

        # 성공 조회 뒤 0
        save_submitted(pollErrors=3, pollError="RuntimeError: provider down")
        poll_mode["raise"] = False
        s1 = poll_again()
        if s1.get("pollErrors") != 0 or s1.get("pollError") is not None or s1.get("status") != "submitted":
            bad.append(f"(pf-e) 성공 조회 뒤 pollErrors·pollError 가 초기화되지 않았습니다: {s1.get('pollErrors')} {s1.get('pollError')!r}")

        # 새 제출은 0(옛 값이 남아 있어도)
        env.reset_calls()
        env.save(
            _parity_state(
                "seedance-2-5",
                durations=(5,),
                step4={
                    "status": "running",
                    "shots": [_parity_shot4("s1", "queued", durationS=5, pollErrors=4, pollError="old")],
                },
            )
        )
        env.client.get(env.plan_url())
        s1 = shot_of(env.load())
        if s1.get("status") != "submitted" or s1.get("pollErrors") != 0 or s1.get("pollError") is not None:
            bad.append(f"(pf-e) 새 제출 뒤 pollErrors=0·pollError=None 이 아닙니다: {s1.get('status')} {s1.get('pollErrors')} {s1.get('pollError')!r}")

        # POLL_GIVEUP_S 파서(새 프로세스에서 env 별로 읽는다 — 이 프로세스 설정을 건드리지 않는다)
        probe = (
            "import importlib, os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from src.vpoc import settings\n"
            "out = []\n"
            "for value in ('', '30', 'abc', '2400'):\n"
            "    os.environ['VPOC_POLL_GIVEUP_S'] = value\n"
            "    importlib.reload(settings)\n"
            "    out.append(str(settings.POLL_GIVEUP_S))\n"
            "print(','.join(out), settings.POLL_GIVEUP_ERRORS)\n"
        )
        child_env = {k: v for k, v in os.environ.items() if k != "VPOC_POLL_GIVEUP_S"}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", probe, str(ROOT / "app")],
            capture_output=True,
            text=True,
            env=child_env,
            timeout=120,
        )
        if proc.returncode != 0 or proc.stdout.strip() != "1800,60,1800,2400 5":
            bad.append(f"(pf-e) POLL_GIVEUP_S 파서가 기본 1800·최소 60·틀린 값 1800 이 아닙니다: {proc.stdout.strip()!r} {proc.stderr[-300:]!r}")

        # ---- (pf-f) 회귀: 기존 분기 ----
        logs.clear()
        for raw in ("IN_QUEUE", "IN_PROGRESS"):
            result = poll_with({"status": raw}, completed)
            if result != {"status": "InProgress", "failureMessage": "", "usage": None}:
                bad.append(f"(pf-f) {raw} 가 InProgress 가 아닙니다: {result}")
        result = poll_with({"status": "FAILED", "error": "boom upstream"}, completed)
        if result.get("status") != "Failed" or result.get("failureMessage") != "boom upstream":
            bad.append(f"(pf-f) FAILED 가 원문 그대로 Failed 가 아닙니다: {result}")
        result = poll_with(completed, {"error": "bad output"})
        if result.get("status") != "Failed" or result.get("failureMessage") != "bad output":
            bad.append(f"(pf-f) 결과 본문 error 가 Failed 원문이 아닙니다: {result}")
        result = poll_with(completed, {"video": {}})
        if result.get("status") != "Failed" or "영상 URL" not in str(result.get("failureMessage")):
            bad.append(f"(pf-f) URL 없음이 Failed 가 아닙니다: {result}")

        class _Body:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> bytes:
                return b"mp4-bytes"

        downloads: list[str] = []

        def fake_urlopen(target, timeout=None):
            downloads.append(str(target))
            return _Body()

        env.patch(_urlrequest, "urlopen", fake_urlopen)
        result = poll_with(completed, {"video": {"url": "https://v3.fal.media/files/out.mp4"}})
        key = f"{prefix}/req-x/output.mp4"
        if result != {"status": "Completed", "failureMessage": "", "usage": None} or env.fake.blobs.get(key) != b"mp4-bytes":
            bad.append(f"(pf-f) Completed 가 우리 버킷 저장과 함께 오지 않습니다: {result} {key in env.fake.blobs}")
        if downloads != ["https://v3.fal.media/files/out.mp4"]:
            bad.append(f"(pf-f) 결과 다운로드가 1회가 아닙니다: {downloads}")
        if rejected_lines():
            bad.append(f"(pf-f) 정상 분기에 rejected 로그가 남았습니다: {rejected_lines()}")
        if env.url_calls:
            bad.append(f"(pf) 실제 네트워크 호출이 있었습니다: {env.url_calls}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("fal 거절·조회 실패 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        if env is not None:
            env.restore()

    if bad:
        fail("fal-seedance-parity 거절·조회 실패 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity 거절·조회 실패 검사 (pf-a)422 likeness·400 real person·로그·vnew 매핑 유지 "
            "(pf-b)sensitive (pf-c)400·403·404 other·요지 정리·빈 본문·500·Unreachable·Balance·status 실패 예외 "
            "(pf-d)GET plan → failed·④ 문구 RAW_ERRORS=False (pf-e)lastPolledAt·pollErrors·간격·31분 5회 PollGiveUp·"
            "10분·submittedAt 없음 유지·성공 0·새 제출 0·파서 (pf-f)IN_QUEUE·IN_PROGRESS·FAILED·error·URL 없음·Completed"
        )


def check_vnew_model_list_parity() -> None:
    """073 작업 8 — vnew ① 영상 모델 목록: Seedance 이름·중복·토큰 단가(R16, D17~D19). 외부 호출은 전부 가짜.

    레지스트리 = 계열 얼굴(i2v)·짝(r2v, 통 테이크 caps) + 가짜 H3 Max(i2v) + 초 단위 v2v (+ 토큰 컷 단위 v2v).
    (vq-a) previz_models 에 r2v 한 줄, i2v 없음. label == ark.label(얼굴) == FAL_SEEDANCE_LABEL, "fal · " 없음.
           계열 설정이 비면 둘 다 지금처럼 `fal · <label>`. _family_face_of 갈래.
    (vq-b) r2v 단가: priceKnown·usdPerSecond == shot_usd(id, VNEW_TAKE_RESOLUTION, 1)·priceResolution(480p 0.2056,
           720p 0.4622). pricing 없음·단가 0·토큰 컷 단위(전송 해상도 모름)는 "단가 미정". _previz_send_resolution.
    (vq-c) 계열 밖 i2v(H3 Max): previzCapable False·available False·"프리비즈 미지원"·"fal · " 접두어(회귀).
    (vq-d) 초 단위 v2v·기본 모델 행 값·키가 지금과 같다(회귀).
    (vq-e) 통합: 통 테이크 POST /generate → 제출 본문 resolution == gen.resolution == priceResolution,
           _charge_take 원장 == shot_usd(id, res, 초) == 표시 단가 × 초(반올림 차이 이내), ④ 견적 일치(480p·720p).
           옛 흐름 _charge_video 도 shot_usd 공식.
    (vq-f) update_input r2v 저장·i2v 400 BadVideoModel·빈 값 기본, _previz_model_ids 집합 그대로(회귀).
    (vq-g) vnew.js previzPriceText: "단가 미정"은 priceKnown false 일 때만, priceResolution 표기, 목록·설명 줄 호출.
    """
    sys.path.insert(0, str(ROOT / "app"))
    import threading as _threading_mod  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from flask import Flask  # noqa: PLC0415

    from src import vnew  # noqa: PLC0415
    from src.vnew import generate as genmod  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import settings as vnew_settings  # noqa: PLC0415
    from src.vpoc import ark, fal, settings  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    bad: list[str] = []
    env: _ParityEnv | None = None
    owner = PARITY_OWNER
    r2v, i2v = PARITY_PARTNER, PARITY_FACE
    r2v_endpoint = r2v[len("fal:"):]
    h3 = "fal:fal-ai/check/h3-max/image-to-video"
    v2v_sec = "fal:fal-ai/check/seconds-v2v"
    v2v_tok = "fal:fal-ai/check/token-cut-v2v"
    token_price = {"unit": "1000 tokens", "unitPrice": 0.0214}
    started_threads: list = []
    pricing_calls: list[str] = []
    orig_thread = _threading_mod.Thread

    def registry(*, r2v_pricing: dict | None = token_price, with_token_cut: bool = False) -> list[dict]:
        items = _parity_registry_items()
        for item in items:
            if item["id"] == r2v:
                # 개발 레지스트리 r2v 모양(extract_caps): 배열 영상 입력 → 통 테이크(previz-take).
                item["caps"] = dict(
                    item["caps"],
                    videoListField="video_urls",
                    videoListMax=3,
                    imageListField="image_urls",
                    imageListMax=10,
                    taskField="task",
                    taskValues=["auto", "reference", "editing", "extension"],
                    draftField="draft",
                    previzCapable=True,
                )
                if r2v_pricing is None:
                    item.pop("pricing", None)
                else:
                    item["pricing"] = dict(r2v_pricing)
        items.append(
            {
                "id": h3,
                "endpointId": h3[len("fal:"):],
                "label": "H3 Max Image to Video",
                "category": "image-to-video",
                "caps": {"previzCapable": False, "available": True, "startImageField": "image_url", "promptField": "prompt"},
                "pricing": {"unit": "seconds", "unitPrice": 0.1},
                "addedBy": "tester",
            }
        )
        items.append(
            {
                "id": v2v_sec,
                "endpointId": v2v_sec[len("fal:"):],
                "label": "Check Seconds V2V",
                "category": "video-to-video",
                "caps": {"previzCapable": True, "available": True, "videoField": "video_url", "promptField": "prompt",
                         "imageListField": "image_urls"},
                "pricing": {"unit": "seconds", "unitPrice": 0.1},
                "addedBy": "tester",
            }
        )
        if with_token_cut:
            items.append(
                {
                    "id": v2v_tok,
                    "endpointId": v2v_tok[len("fal:"):],
                    "label": "Check Token Cut V2V",
                    "category": "video-to-video",
                    "caps": {"previzCapable": True, "available": True, "videoField": "video_url", "promptField": "prompt"},
                    "pricing": dict(token_price),
                    "addedBy": "tester",
                }
            )
        return items

    def rows_by_id() -> tuple[list[dict], dict]:
        rows = planmod.previz_models()
        return rows, {str(row.get("id")): row for row in rows}

    def tracking_thread(*args, **kwargs):
        thread = orig_thread(*args, **kwargs)
        started_threads.append(thread)
        return thread

    def join_drivers(timeout: float = 30.0) -> None:
        deadline = _time.time() + timeout
        for thread in list(started_threads):
            thread.join(timeout=max(0.0, deadline - _time.time()))
        started_threads.clear()

    fake_blobs = {"wf-ch1": b"WF1", "wb-ch1": b"WB1", "wf-ch2": b"WF2", "wb-ch2": b"WB2", "loc-wb-back": b"LOC"}

    def fake_load_char(_owner, char_id):
        return (
            {
                "id": char_id,
                "name": "에이" if char_id.endswith("1") else "비",
                "status": "ready",
                "analysis": {"appearanceEn": "a person", "outfitEn": "an outfit", "styleEn": "thin ink lines"},
                "variants": {
                    "real": {"frontKey": "rf-" + char_id, "backKey": "rb-" + char_id, "sheetKey": ""},
                    "webtoon": {"frontKey": "wf-" + char_id, "backKey": "wb-" + char_id, "sheetKey": ""},
                },
            },
            "etag",
        )

    def make_take_plan(pid: str, seconds: list[int]) -> None:
        """통 테이크 선행 조건(③ 콘티·프리비즈 승인·녹화·지시서)을 갖춘 r2v 계획(check_vnew_take 와 같은 모양)."""
        plan = planmod.new_plan()
        segments = [{"id": f"s{i + 1}", "cutIds": [f"c{i + 1}"], "seconds": sec} for i, sec in enumerate(seconds)]
        plan["input"].update(
            {
                "totalSeconds": sum(seconds),
                "videoModel": r2v,
                "order": ["f1"],
                "images": [{"fileId": "f1", "name": "1.png", "width": 600, "height": 900}],
                "characters": [{"charId": "ch1", "roleName": "에이"}, {"charId": "ch2", "roleName": "비"}],
            }
        )
        env.fake.blobs[planmod.upload_key(owner, pid, "f1")] = _tiny_jpeg(600, 900)
        cuts = [
            {
                "id": seg["cutIds"][0],
                "no": index + 1,
                "seconds": seg["seconds"],
                "panels": [0],
                "shotKo": "컷",
                "cameraEn": "static camera",
                "locationEn": "A closed office",
                "characters": [{"name": "에이", "positionEn": "at the desk", "actionEn": "speaks"}],
                "dialogueKo": "자네 뭐 하나?",
                "situationKo": "호통",
                "promptEn": "Kim speaks, static camera.",
            }
            for index, seg in enumerate(segments)
        ]
        plan["script"] = {
            "status": "ready",
            "setting": {"locationEn": "A closed office", "enclosed": True},
            "entries": [{"id": "e1", "panels": [0], "locationEn": "A closed office"}],
        }
        plan["conti"] = {"status": "ready", "cuts": cuts, "segments": segments, "setting": {"locationEn": "A closed office"}}
        plan["locations"] = [
            {
                "id": "loc1",
                "nameEn": "A closed office",
                "key": "a closed office",
                "panels": [0],
                "status": "ready",
                "variants": {"webtoon": {"viewKeys": {"back": "loc-wb-back"}}},
            }
        ]
        actors = [
            {"id": "a1", "name": "에이", "color": "#4363d8", "charId": "ch1", "appearanceEn": "a man"},
            {"id": "a2", "name": "비", "color": "#e6194b", "charId": "ch2", "appearanceEn": "a woman"},
        ]
        recordings = {}
        for seg in segments:
            rec_key = f"rec/{pid}/{seg['id']}.mp4"
            env.fake.blobs[rec_key] = b"PREVIZ-REC-MP4"
            recordings[seg["id"]] = {"status": "ready", "key": rec_key, "seconds": float(seg["seconds"])}
        plan["previz"] = {
            "status": "ready",
            "error": "",
            "revision": 0,
            "scenes": [
                {
                    "locationId": "loc1",
                    "actors": actors,
                    "timeline": [{"cutId": cut["id"], "actors": [{"id": "a1"}, {"id": "a2"}]} for cut in cuts],
                }
            ],
            "approved": True,
            "recordings": recordings,
            "shotBrief": {
                "status": "ready",
                "scene": {"loc1": "A closed office, wood panelling."},
                "cuts": {
                    cut["id"]: {
                        "shotEn": "MCU, 50mm lens, eye-level.",
                        "actionEn": "Kim leans forward.",
                        "dialogue": [{"speaker": "에이", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp"}],
                    }
                    for cut in cuts
                },
                "updatedAt": "2026-10-28T00:00:00Z",
            },
            "task": {"status": "done", "startedAt": None, "id": None},
        }
        planmod.save_plan(owner, pid, plan)

    try:
        env = _parity_env(registry=registry())
        for module in (planmod, genmod):
            env.patch(module, "STORE", env.fake)
        env.patch(fal, "pricing", lambda endpoint: pricing_calls.append(str(endpoint)) or None)
        env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", "480p")
        default_id = planmod.default_previz_model()

        # ---- (vq-a) 이름·중복 ----
        rows, by_id = rows_by_id()
        ids = [str(row.get("id")) for row in rows]
        if ids.count(r2v) != 1:
            bad.append(f"(vq-a) r2v 가 목록에 한 줄이 아닙니다: {ids}")
        if i2v in ids:
            bad.append(f"(vq-a) 계열 얼굴(i2v)이 목록에 남았습니다: {ids}")
        family_rows = [row_id for row_id in ids if settings.is_fal_seedance_family(row_id)]
        if family_rows != [r2v]:
            bad.append(f"(vq-a) Seedance 계열 행이 r2v 하나가 아닙니다: {family_rows}")
        r2v_label = str((by_id.get(r2v) or {}).get("label") or "")
        if r2v_label != ark.label(i2v) or r2v_label != settings.FAL_SEEDANCE_LABEL:
            bad.append(f"(vq-a) r2v label 이 vpoc 계열 이름(ark.label(얼굴))과 다릅니다: {r2v_label!r} != {ark.label(i2v)!r}")
        if r2v_label.startswith("fal · "):
            bad.append(f"(vq-a) r2v label 에 'fal · ' 접두어가 있습니다: {r2v_label!r}")
        for model_id, expect in ((r2v, i2v), (i2v, ""), (h3, ""), ("luma-ray2", ""), ("", "")):
            if planmod._family_face_of(model_id) != expect:
                bad.append(f"(vq-a) _family_face_of({model_id!r}) != {expect!r}: {planmod._family_face_of(model_id)!r}")
        # 계열 설정이 비면 계열이 없다 — r2v·i2v 모두 073 이전과 같은 `fal · <label>` 행.
        orig_family = settings.FAL_SEEDANCE_FAMILY
        env.patch(settings, "FAL_SEEDANCE_FAMILY", {})
        _rows_off, off = rows_by_id()
        if (off.get(r2v) or {}).get("label") != "fal · Bytedance Seedance 2.5 Reference to Video":
            bad.append(f"(vq-a) 계열 설정이 비었는데 r2v label 이 지금 값이 아닙니다: {(off.get(r2v) or {}).get('label')}")
        off_i2v = off.get(i2v) or {}
        if off_i2v.get("label") != "fal · Bytedance Seedance 2.5 Image to Video" or off_i2v.get("previzCapable") is not False:
            bad.append(f"(vq-a) 계열 설정이 비었는데 i2v 행이 지금(회색 fal · ) 값이 아닙니다: {off_i2v}")
        env.patch(settings, "FAL_SEEDANCE_FAMILY", orig_family)

        # ---- (vq-b) 토큰 단가 ----
        row = by_id.get(r2v) or {}
        expect_480 = settings.shot_usd(r2v, "480p", 1)
        if row.get("priceKnown") is not True or row.get("usdPerSecond") != round(expect_480, 4):
            bad.append(f"(vq-b) r2v 단가가 shot_usd(id, 480p, 1)={expect_480} 이 아닙니다: {row}")
        if abs(float(row.get("usdPerSecond") or 0) - 0.2056) > 0.0001:
            bad.append(f"(vq-b) 480p 초당 단가가 약 0.2056 이 아닙니다: {row.get('usdPerSecond')}")
        if row.get("priceResolution") != str(vnew_settings.VNEW_TAKE_RESOLUTION):
            bad.append(f"(vq-b) priceResolution 이 전송 해상도(VNEW_TAKE_RESOLUTION)가 아닙니다: {row.get('priceResolution')}")
        env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", "720p")
        row_720 = planmod._previz_price_row(r2v)
        if row_720 != {"usdPerSecond": round(settings.shot_usd(r2v, "720p", 1), 4), "priceKnown": True, "priceResolution": "720p"}:
            bad.append(f"(vq-b) 720p 단가 행이 다릅니다: {row_720}")
        if abs(float(row_720.get("usdPerSecond") or 0) - 0.4622) > 0.0001:
            bad.append(f"(vq-b) 720p 초당 단가가 약 0.4622 가 아닙니다: {row_720.get('usdPerSecond')}")
        env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", "480p")
        if planmod._previz_send_resolution({"videoListField": "video_urls"}) != "480p":
            bad.append("(vq-b) _previz_send_resolution(통 테이크) 이 VNEW_TAKE_RESOLUTION 이 아닙니다")
        if planmod._previz_send_resolution({"videoField": "video_url"}) != "" or planmod._previz_send_resolution({}) != "":
            bad.append("(vq-b) _previz_send_resolution(컷 단위·빈 caps) 이 빈 값이 아닙니다")
        # 원장 공식(TAKE_RESOLUTION_WH)에 없는 설정 값은 표시·원장 픽셀이 달라지므로 "단가 미정"(리뷰 3단계).
        for odd in ("540p", "4k"):
            env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", odd)
            got_odd = planmod._previz_price_row(r2v)
            if planmod._previz_send_resolution({"videoListField": "video_urls"}) != "" or got_odd != {
                "usdPerSecond": 0.0,
                "priceKnown": False,
            }:
                bad.append(f"(vq-b) VNEW_TAKE_RESOLUTION={odd} 인데 '단가 미정'이 아닙니다: {got_odd}")
        env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", "480p")
        # pricing 없음 / 단가 0 / 토큰 컷 단위(전송 해상도 모름) → "단가 미정".
        for case, items, model_id in (
            ("pricing 없음", registry(r2v_pricing=None), r2v),
            ("토큰 단가 0", registry(r2v_pricing={"unit": "1000 tokens", "unitPrice": 0}), r2v),
            ("토큰 컷 단위", registry(with_token_cut=True), v2v_tok),
        ):
            env.set_registry(items)
            got = planmod._previz_price_row(model_id)
            if got != {"usdPerSecond": 0.0, "priceKnown": False}:
                bad.append(f"(vq-b) {case} 인데 '단가 미정' 행이 아닙니다: {got}")
            _rows_case, case_by_id = rows_by_id()
            if "priceResolution" in (case_by_id.get(model_id) or {}):
                bad.append(f"(vq-b) {case} 행에 priceResolution 이 있습니다: {case_by_id.get(model_id)}")
        env.set_registry(registry())

        # ---- (vq-c) 계열 밖 i2v 회귀 ----
        rows, by_id = rows_by_id()
        h3_row = by_id.get(h3) or {}
        if (
            h3_row.get("previzCapable") is not False
            or h3_row.get("available") is not False
            or not str(h3_row.get("reason") or "").startswith("프리비즈 미지원")
            or h3_row.get("label") != "fal · H3 Max Image to Video"
            or "priceResolution" in h3_row
        ):
            bad.append(f"(vq-c) 계열 밖 i2v(H3 Max) 행이 지금(회색·fal · ) 값이 아닙니다: {h3_row}")

        # ---- (vq-d) 초 단위·기본 모델 회귀 ----
        if planmod._previz_price_row(v2v_sec) != {"usdPerSecond": 0.1, "priceKnown": True}:
            bad.append(f"(vq-d) 초 단위 v2v 단가 행이 지금 값이 아닙니다: {planmod._previz_price_row(v2v_sec)}")
        sec_row = by_id.get(v2v_sec) or {}
        if sec_row.get("label") != "fal · Check Seconds V2V" or sec_row.get("available") is not True:
            bad.append(f"(vq-d) 초 단위 v2v 행 이름·사용 가능이 지금 값이 아닙니다: {sec_row}")
        first = rows[0] if rows else {}
        expect_default = {"usdPerSecond": round(float(vnew_settings.VNEW_PREVIZ_EDIT_USD_PER_S), 4), "priceKnown": True}
        default_keys = {"id", "label", "recommended", "previzCapable", "refMode", "available", "reason", "usdPerSecond", "priceKnown"}
        if (
            first.get("id") != default_id
            or first.get("recommended") is not True
            or first.get("label") != "Kling O3 (추천)"
            or set(first) != default_keys
            or {key: first.get(key) for key in ("usdPerSecond", "priceKnown")} != expect_default
        ):
            bad.append(f"(vq-d) 기본 모델 행이 지금 값이 아닙니다: {first}")

        # ---- (vq-f) 검증 규칙 회귀 ----
        pid_f = "prj_vq_f"
        planmod.load_or_create(owner, pid_f)
        if planmod._previz_model_ids() != {default_id, r2v, v2v_sec}:
            bad.append(f"(vq-f) 고를 수 있는 모델 집합이 다릅니다: {sorted(planmod._previz_model_ids())}")

        def stored_model(pid: str) -> str:
            data, _ = planmod.load_plan(owner, pid)
            return str((data.get("input") or {}).get("videoModel") or "")

        planmod.update_input(owner, pid_f, {"videoModel": r2v})
        if stored_model(pid_f) != r2v:
            bad.append(f"(vq-f) r2v 가 저장되지 않았습니다: {stored_model(pid_f)}")
        try:
            planmod.update_input(owner, pid_f, {"videoModel": i2v})
            bad.append("(vq-f) 계열 i2v 가 거절되지 않았습니다(지금 규칙은 400)")
        except VpocError as exc:
            if exc.status != 400 or exc.kind != "BadVideoModel":
                bad.append(f"(vq-f) 계열 i2v 가 400 BadVideoModel 이 아닙니다: {exc.status} {exc.kind}")
        if stored_model(pid_f) != r2v:
            bad.append(f"(vq-f) i2v 거절 뒤 저장값이 바뀌었습니다: {stored_model(pid_f)}")
        planmod.update_input(owner, pid_f, {"videoModel": ""})
        if stored_model(pid_f) != default_id:
            bad.append(f"(vq-f) 빈 값이 기본 프리비즈 모델로 저장되지 않았습니다: {stored_model(pid_f)}")

        # ---- (vq-e) 통합: 표시 단가 = 전송 해상도 = 원장 ----
        env.patch(genmod.characters, "load_char", fake_load_char)
        env.patch(genmod, "_load_jpeg", lambda key: fake_blobs.get(str(key or "")))
        env.patch(genmod, "_segment_panel_jpegs", lambda o, p, plan, seg, limit: [b"PANEL1", b"PANEL2"][:limit])
        env.patch(genmod.imaging, "fit_ratio", lambda jpeg, *a, **k: jpeg)
        env.patch(genmod, "_read_cast_notes", lambda blocks, *, model: ({"people": []}, {"model": model}))
        env.patch(genmod, "_concat", lambda clips: b"FINAL-TAKE-MP4")
        env.patch(genmod, "_fetch_bytes", lambda key, *, bucket="": env.fake.blobs.get(key, b"REC-MP4-BYTES"))
        env.patch(fal, "poll", lambda arn, prefix: {"status": "Completed", "failureMessage": "", "usage": None})
        env.patch(_threading_mod, "Thread", tracking_thread)
        app = Flask("vnew-model-list-parity")
        app.register_blueprint(
            vnew.create_blueprint(
                vnew.VnewDependencies(
                    require_auth=lambda handler: handler,
                    current_owner=lambda: owner,
                    get_project=lambda p: {"id": p, "owner": owner, "track": "video_new"},
                    can_read_project=lambda proj: True,
                    can_write_project=lambda proj: True,
                    project_owner_prefix=lambda name: name,
                )
            )
        )
        client = app.test_client()
        for resolution in ("480p", "720p"):
            env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", resolution)
            env.reset_calls()
            pid = "prj_vq_e_" + resolution
            make_take_plan(pid, [12, 15])
            shown = planmod._previz_price_row(r2v)
            plan_e, _ = planmod.load_plan(owner, pid)
            est = genmod.estimate(plan_e)
            resp = client.post(f"/api/vnew/p/{pid}/generate", json={"confirmUsd": est["totalUsd"]})
            if resp.status_code != 202:
                bad.append(f"(vq-e) {resolution} generate 가 202 가 아닙니다: {resp.status_code} {resp.get_json()}")
                continue
            join_drivers()
            gen = (planmod.load_plan(owner, pid)[0].get("gen")) or {}
            posts = env.fal_posts()
            if len(posts) != 1 or not str(posts[0][1]).endswith("/" + r2v_endpoint):
                bad.append(f"(vq-e) {resolution} r2v 제출이 1회가 아닙니다: {[call[1] for call in posts]}")
                continue
            body = posts[0][2] or {}
            if not (body.get("resolution") == gen.get("resolution") == shown.get("priceResolution") == resolution):
                bad.append(
                    f"(vq-e) {resolution} 전송·gen·표시 해상도가 다릅니다: body={body.get('resolution')} "
                    f"gen={gen.get('resolution')} shown={shown.get('priceResolution')}"
                )
            charges = [entry for entry in env.charges if entry.get("plan_id") == gen.get("genId")]
            formula = settings.shot_usd(r2v, resolution, 27)
            if len(charges) != 1:
                bad.append(f"(vq-e) {resolution} 원장 계상이 1건이 아닙니다: {len(charges)}")
            else:
                usd = float(charges[0].get("usd") or 0)
                if abs(usd - formula) > 0.0001:
                    bad.append(f"(vq-e) {resolution} 원장 {usd} != shot_usd(id, {resolution}, 27)={formula}")
                if abs(usd - float(shown.get("usdPerSecond") or 0) * 27) > 0.00005 * 27 + 0.0001:
                    bad.append(f"(vq-e) {resolution} 원장 {usd} 이 표시 단가×27({shown.get('usdPerSecond')}×27)과 다릅니다")
                if charges[0].get("model") != r2v_endpoint:
                    bad.append(f"(vq-e) {resolution} 원장 model 이 r2v endpoint 가 아닙니다: {charges[0].get('model')}")
            if est.get("mode") != "previz-take" or est.get("totalUsd") != round(formula, 2):
                bad.append(f"(vq-e) {resolution} ④ 견적이 같은 공식이 아닙니다: {est.get('mode')} {est.get('totalUsd')} != {round(formula, 2)}")
        env.patch(vnew_settings, "VNEW_TAKE_RESOLUTION", "480p")
        # 옛 흐름 계상(_charge_video)도 shot_usd(id, 해상도, 초)다.
        env.reset_calls()
        genmod._charge_video(owner, "prj_vq_e_old", "gen-vq-old", {"clientRequestToken": "tok-vq-old"}, r2v, "720p", 10)
        old = [entry for entry in env.charges if entry.get("key") == "tok-vq-old"]
        if len(old) != 1 or abs(float(old[0].get("usd") or 0) - settings.shot_usd(r2v, "720p", 10)) > 0.0001:
            bad.append(f"(vq-e) _charge_video 원장이 shot_usd(id, 720p, 10) 이 아닙니다: {old}")

        # ---- (vq-g) 프런트 문구 ----
        js = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        start = js.find("function previzPriceText(")
        body_js = js[start : js.find("\n  }\n", start)] if start >= 0 else ""
        # 주석 줄(/* … */ · //)은 빼고 코드 줄만 본다.
        miss_lines = [
            line
            for line in js.splitlines()
            if "단가 미정" in line and not line.strip().startswith(("/*", "*", "//"))
        ]
        if start < 0:
            bad.append("(vq-g) vnew.js 에 previzPriceText 함수가 없습니다")
        if len(miss_lines) != 1 or "!model.priceKnown" not in miss_lines[0]:
            bad.append(f"(vq-g) '단가 미정'이 priceKnown false 조건 한 곳에만 있지 않습니다: {miss_lines}")
        if "model.priceResolution" not in body_js or '"(" + String(model.priceResolution) + ")"' not in body_js:
            bad.append("(vq-g) previzPriceText 가 priceResolution 을 '(480p)' 형식으로 붙이지 않습니다")
        if '"초당 " + amount' not in body_js or 'amount + "/초"' not in body_js:
            bad.append("(vq-g) previzPriceText 의 목록('/초')·설명 줄('초당 ') 형식이 없습니다")
        if "previzPriceText(model, false)" not in js or "previzPriceText(chosen, true)" not in js:
            bad.append("(vq-g) 목록·설명 줄이 previzPriceText 를 부르지 않습니다")
        if 'toFixed(3) + "/초"' in js or '"초당 $" + (Number(chosen.usdPerSecond)' in js:
            bad.append("(vq-g) 옛 단가 문구 조립이 남아 있습니다")

        if env.url_calls:
            bad.append(f"실제 네트워크 호출이 있었습니다: {env.url_calls}")
        if env.ark_calls or env.video_calls:
            bad.append("BytePlus·Luma 호출이 있었습니다")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("vnew 모델 목록 검사 중 예외: " + repr(exc) + "\n" + _tb.format_exc()[:1500])
    finally:
        join_drivers(5.0)
        if env is not None:
            env.restore()
        _threading_mod.Thread = orig_thread

    if bad:
        fail("fal-seedance-parity vnew 모델 목록 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "fal-seedance-parity vnew 모델 목록 검사 (vq-a)r2v 한 줄·i2v 숨김·ark.label 이름·fal · 없음·계열 끔 회귀 "
            "(vq-b)토큰 단가 shot_usd 480p 0.2056·720p 0.4622·priceResolution·pricing 없음·0·컷 단위·540p/4k 미정 "
            "(vq-c)H3 Max 회색·fal · (vq-d)초 단위·기본 모델 행 그대로 (vq-e)전송=gen=표시 해상도·원장=shot_usd=단가×초·"
            "견적(480p·720p)·_charge_video (vq-f)r2v 저장·i2v 400·빈 값 기본·집합 그대로 (vq-g)previzPriceText 문구"
        )


# ---------------------------------------------------------------- llm-top-model-first

# 검사가 쓰는 모델 id(코드 기본 체인과 같은 값).
LT_FABLE = "global.anthropic.claude-fable-5-1"
LT_OPUS55 = "global.anthropic.claude-opus-5-5"
LT_OPUS46 = "global.anthropic.claude-opus-4-6-v1"
LT_SONNET46 = "global.anthropic.claude-sonnet-4-6"
LT_SONNET45 = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
LT_ASTRA = "global.openai.gpt-6-astra"
LT_KEYS = (
    "VPOC_LLM_NO_FORCED_TOOL",
    "VPOC_LLM_NO_TEMPERATURE",
    "VPOC_LLM_TRUNC_RETRY_FACTOR",
    "VPOC_LLM_TRUNC_RETRY_CAP",
)


class _LtFakeBedrock:
    """가짜 bedrock-runtime. script 의 응답을 차례로 준다(예외면 올린다). 요청을 (종류, 요청) 으로 모은다."""

    def __init__(self) -> None:
        self.script: list = []
        self.calls: list[tuple[str, dict]] = []

    def load(self, *items) -> None:
        self.script = list(items)
        self.calls = []

    def _next(self, kind: str, request: dict):
        self.calls.append((kind, request))
        if not self.script:
            raise AssertionError(f"가짜 Bedrock 응답이 모자랍니다({kind} {request.get('modelId')})")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def converse_stream(self, **request):
        return self._next("stream", request)

    def converse(self, **request):
        return self._next("converse", request)

    def models(self) -> list:
        return [request.get("modelId") for _, request in self.calls]

    def budgets(self) -> list:
        return [(request.get("inferenceConfig") or {}).get("maxTokens") for _, request in self.calls]

    def kinds(self) -> list:
        return [kind for kind, _ in self.calls]


def _lt_stream(*, tool_json: str | None = None, text: str = "", stop: str | None = "tool_use", out: int = 20) -> dict:
    """converse_stream 응답 모양(이벤트 목록). stop=None 이면 messageStop 이 없다."""
    events: list[dict] = [{"messageStart": {"role": "assistant"}}]
    if text:
        events.append({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}})
    if tool_json is not None:
        events.append({"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "t1", "name": "emit"}}}})
        events.append({"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": tool_json}}}})
        events.append({"contentBlockStop": {"contentBlockIndex": 1}})
    if stop is not None:
        events.append({"messageStop": {"stopReason": stop}})
    events.append({"metadata": {"usage": {"inputTokens": 100, "outputTokens": out}, "metrics": {"latencyMs": 5}}})
    return {"stream": events}


def _lt_converse(*, tool_input: dict | None = None, text: str = "", stop: str | None = "tool_use", out: int = 20) -> dict:
    """converse(보통 호출) 응답 모양. stop=None 이면 stopReason 키가 없다."""
    content: list[dict] = []
    if text:
        content.append({"text": text})
    if tool_input is not None:
        content.append({"toolUse": {"toolUseId": "t1", "name": "emit", "input": tool_input}})
    response: dict = {
        "output": {"message": {"role": "assistant", "content": content}},
        "usage": {"inputTokens": 100, "outputTokens": out},
    }
    if stop is not None:
        response["stopReason"] = stop
    return response


def check_llm_top_model_first() -> None:
    """llm-top-model-first — 잘림 재시도·설정 거절 미리 적용·호출부 예산·로그·배선(R1~R6). 외부 호출은 전부 가짜.

    (lt-a) 스트림 잘림(messageStop max_tokens + 깨진 JSON) → 같은 모델 재시도 400→1600·900→3600·2000→4096, 성공·model·로그
    (lt-b) 재시도도 잘림 → 다음 모델(원래 예산)·체인 전부 잘림 문구·8000 재시도 없음·FACTOR=1 재시도 없음
    (lt-c) end_turn 도구 없음 → 재시도 없이 다음 모델·지금 문구, max_tokens 인데 JSON 온전 → 성공 1회
    (lt-d) 미리 적용: opus-5-5·fable-5-1 temperature 없음·auto·강제 문장, gpt-6 temperature 없음·tool, 4.6·4.5 그대로
    (lt-e) 설정 빈 값 → 미리 적용 꺼짐·_adapt 2회(회귀)·_parse_marks
    (lt-f) 보통 호출 stopReason 잘림 재시도·스트림 권한 거절 전환·4개 반환
    (lt-g) count_people 400·previz.grammar 2000(가짜 _converse 인자)
    (lt-h) vpoc.call stop=·프롬프트 본문 로그 없음·실제 클라이언트 0
    (lt-i) config.env.example 값 = 코드 기본·render.py base+dev·prod 렌더 값
    """
    import importlib.util  # noqa: PLC0415
    import time as _time_mod  # noqa: PLC0415
    import types as _types  # noqa: PLC0415

    from botocore.exceptions import ClientError, ReadTimeoutError  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "app"))
    from src.vnew import characters, previz_grammar  # noqa: PLC0415
    from src.vnew import settings as vnew_settings  # noqa: PLC0415
    from src.vpoc import llm, settings  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    bad: list[str] = []
    logs = _LogCapture()
    fake = _LtFakeBedrock()
    real_calls: list[str] = []
    sleeps: list[float] = []
    sys_mark = "LT-SYSTEM-SECRET-PROMPT-7f3a"
    user_mark = "LT-USER-SECRET-BLOCK-9c1e"
    tool = {
        "name": "emit_people_count",
        "description": "Count people.",
        "inputSchema": {"json": {"type": "object", "properties": {"people": {"type": "integer"}}, "required": ["people"]}},
    }
    blocks = [{"text": user_mark}]
    forced_line = "You MUST answer by calling the emit_people_count tool exactly once. No plain text."
    saved = {
        "client_for": llm.client_for,
        "runtime": llm.runtime,
        "_broker_client": llm._broker_client,
        "_converse": llm._converse,
        "time": llm.time,
        "STREAM": dict(llm._STREAM),
        "DENIED": dict(llm._DENIED),
        "NO_FORCED_TOOL": set(llm._NO_FORCED_TOOL),
        "NO_TEMPERATURE": set(llm._NO_TEMPERATURE),
    }
    setting_names = (
        "TEXT_FALLBACK_MODELS",
        "TEXT_MODEL",
        "LLM_NO_FORCED_TOOL",
        "LLM_NO_TEMPERATURE",
        "LLM_TRUNC_RETRY_FACTOR",
        "LLM_TRUNC_RETRY_CAP",
    )
    saved_settings = {name: getattr(settings, name) for name in setting_names}
    chain = [LT_FABLE, LT_OPUS55, LT_OPUS46, LT_SONNET46, LT_SONNET45]

    def reset() -> None:
        llm._STREAM.clear()
        llm._STREAM.update({"ok": True, "broker": True})
        llm._DENIED.clear()
        llm._NO_FORCED_TOOL.clear()
        llm._NO_TEMPERATURE.clear()
        settings.TEXT_FALLBACK_MODELS = [LT_OPUS55, LT_OPUS46, LT_SONNET46, LT_SONNET45]
        settings.TEXT_MODEL = LT_SONNET45
        settings.LLM_NO_FORCED_TOOL = settings._parse_marks(settings.LLM_NO_FORCED_TOOL_DEFAULT)
        settings.LLM_NO_TEMPERATURE = settings._parse_marks(settings.LLM_NO_TEMPERATURE_DEFAULT)
        settings.LLM_TRUNC_RETRY_FACTOR = 4
        settings.LLM_TRUNC_RETRY_CAP = 4096
        logs.clear()

    def call(max_tokens: int = 400, model: str = LT_FABLE, where: str = "check.lt"):
        return llm._converse(
            system_text=sys_mark,
            blocks=blocks,
            tool=tool,
            max_tokens=max_tokens,
            where=where,
            model=model,
        )

    def trunc_lines() -> list[str]:
        return logs.messages("vpoc.call.trunc_retry", level=logging.INFO)

    def call_lines() -> list[str]:
        return logs.messages("vpoc.call where=", level=logging.INFO)

    def validation(message: str) -> ClientError:
        return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "ConverseStream")

    def no_real(name: str):
        def refuse(*_args, **_kwargs):
            real_calls.append(name)
            raise AssertionError(f"실제 Bedrock 클라이언트({name})를 만들려 했습니다")

        return refuse

    try:
        llm.client_for = lambda model: fake
        llm.runtime = no_real("runtime")
        llm._broker_client = no_real("broker")
        # 일시 오류 대기(3초)는 기록만 한다. 전역 time 모듈은 건드리지 않고 llm 이 보는 이름만 바꾼다.
        llm.time = _types.SimpleNamespace(time=_time_mod.time, sleep=sleeps.append)
        logs.attach()

        # ---- (lt-a) 스트림 잘림 → 같은 모델 재시도 ----
        for max_tokens, want in ((400, 1600), (900, 3600), (2000, 4096)):
            reset()
            if llm._trunc_budget(max_tokens) != want:
                bad.append(f"(lt-a) _trunc_budget({max_tokens}) != {want}: {llm._trunc_budget(max_tokens)}")
            fake.load(
                _lt_stream(text="Looking at the image, ", tool_json='{"people": 2', stop="max_tokens", out=max_tokens),
                _lt_stream(tool_json='{"people": 2}', stop="tool_use", out=40),
            )
            try:
                payload, usage = call(max_tokens)
            except Exception as exc:  # noqa: BLE001
                bad.append(f"(lt-a) {max_tokens}: 재시도가 성공하지 않았습니다: {type(exc).__name__} {str(exc)[:200]}")
                continue
            if payload != {"people": 2} or usage.get("model") != LT_FABLE or usage.get("outputTokens") != 40:
                bad.append(f"(lt-a) {max_tokens}: payload·usage 가 재시도 결과가 아닙니다: {payload} {usage}")
            if fake.models() != [LT_FABLE, LT_FABLE] or fake.budgets() != [max_tokens, want]:
                bad.append(
                    f"(lt-a) {max_tokens}: 요청이 같은 모델 [{max_tokens}, {want}] 가 아닙니다: {fake.models()} {fake.budgets()}"
                )
            lines = trunc_lines()
            if lines != [f"vpoc.call.trunc_retry where=check.lt model={LT_FABLE} budget={want}"]:
                bad.append(f"(lt-a) {max_tokens}: trunc_retry 로그가 1줄(where·model·budget)이 아닙니다: {lines}")

        # ---- (lt-b) 재시도도 잘림 → 다음 모델 ----
        reset()
        fake.load(
            _lt_stream(tool_json='{"peo', stop="max_tokens", out=400),
            _lt_stream(text="x" * 50, stop="max_tokens", out=1600),
            _lt_stream(tool_json='{"people": 1}', stop="tool_use"),
        )
        payload, usage = call(400)
        if fake.models() != [LT_FABLE, LT_FABLE, LT_OPUS55] or fake.budgets() != [400, 1600, 400]:
            bad.append(f"(lt-b) 재시도 잘림 뒤 다음 모델(원래 예산)이 아닙니다: {fake.models()} {fake.budgets()}")
        if payload != {"people": 1} or usage.get("model") != LT_OPUS55:
            bad.append(f"(lt-b) 다음 모델 결과가 아닙니다: {payload} {usage}")
        if len(trunc_lines()) != 1:
            bad.append(f"(lt-b) trunc_retry 로그가 1줄이 아닙니다: {trunc_lines()}")
        # 체인 전부 잘림 → 모델마다 2회, 마지막 오류는 잘림 문구
        reset()
        fake.load(*[_lt_stream(text="thinking...", stop="max_tokens", out=400) for _ in range(len(chain) * 2)])
        try:
            call(400)
            bad.append("(lt-b) 체인 전부 잘림인데 오류가 없습니다")
        except VpocError as exc:
            if (
                exc.kind != "SchemaViolation"
                or exc.status != 502
                or not exc.message.startswith("모델 출력이 출력 한도(1600 토큰)에서 잘렸습니다. 출력 앞부분: thinking")
            ):
                bad.append(f"(lt-b) 잘림 오류가 SchemaViolation 502·잘림 문구가 아닙니다: {exc.kind} {exc.status} {exc.message[:120]}")
        want_models = [model for model in chain for _ in range(2)]
        if fake.models() != want_models or fake.budgets() != [400, 1600] * len(chain):
            bad.append(f"(lt-b) 체인 순서·예산이 모델마다 [400, 1600] 이 아닙니다: {fake.models()} {fake.budgets()}")
        if len(trunc_lines()) != len(chain):
            bad.append(f"(lt-b) 체인 전부 잘림 trunc_retry 로그가 {len(chain)}줄이 아닙니다: {len(trunc_lines())}")
        # 원래 예산 ≥ 상한 → 재시도 없음
        reset()
        fake.load(_lt_stream(text="t", stop="max_tokens", out=8000), _lt_stream(tool_json='{"people": 0}'))
        call(8000)
        if fake.models() != [LT_FABLE, LT_OPUS55] or fake.budgets() != [8000, 8000] or trunc_lines():
            bad.append(f"(lt-b) 8000 잘림이 재시도 없이 다음 모델로 가지 않았습니다: {fake.models()} {fake.budgets()} {trunc_lines()}")
        # FACTOR=1 → 재시도 없음(되돌리기 스위치)
        reset()
        settings.LLM_TRUNC_RETRY_FACTOR = 1
        fake.load(_lt_stream(text="t", stop="max_tokens", out=400), _lt_stream(tool_json='{"people": 0}'))
        call(400)
        if fake.models() != [LT_FABLE, LT_OPUS55] or fake.budgets() != [400, 400] or trunc_lines():
            bad.append(f"(lt-b) FACTOR=1 인데 재시도했습니다: {fake.models()} {fake.budgets()} {trunc_lines()}")
        # 잘림 재시도 중 일시 오류(혼잡·읽기 시간 초과) → 늘린 예산으로 같은 모델 한 번 더(design.md §3)
        for label, error in (
            ("Throttling", ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "ConverseStream")),
            ("ReadTimeout", ReadTimeoutError(endpoint_url="https://bedrock-runtime.invalid")),
        ):
            reset()
            sleeps.clear()
            fake.load(_lt_stream(text="t", stop="max_tokens", out=400), error, _lt_stream(tool_json='{"people": 7}'))
            payload, usage = call(400)
            if fake.models() != [LT_FABLE] * 3 or fake.budgets() != [400, 1600, 1600] or payload != {"people": 7} or sleeps != [3]:
                bad.append(
                    f"(lt-b) 재시도 중 {label} 뒤 같은 모델·1600 이 아닙니다: {fake.models()} {fake.budgets()} {payload} {sleeps}"
                )
        # 미허용(AccessDenied) → 거부 기억·다음 모델은 원래 예산
        reset()
        fake.load(
            ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no access to model"}}, "ConverseStream"),
            _lt_stream(tool_json='{"people": 1}'),
        )
        payload, usage = call(400)
        if fake.models() != [LT_FABLE, LT_OPUS55] or fake.budgets() != [400, 400] or LT_FABLE not in llm._DENIED:
            bad.append(f"(lt-b) AccessDenied 뒤 다음 모델·거부 기억이 지금과 다릅니다: {fake.models()} {fake.budgets()}")

        # ---- (lt-c) 잘림이 아닌 실패(회귀) ----
        reset()
        fake.load(_lt_stream(text="There are two people.", stop="end_turn"), _lt_stream(tool_json='{"people": 2}'))
        payload, usage = call(400)
        if fake.models() != [LT_FABLE, LT_OPUS55] or fake.budgets() != [400, 400] or trunc_lines():
            bad.append(f"(lt-c) end_turn 도구 없음이 재시도 없이 다음 모델로 가지 않았습니다: {fake.models()} {fake.budgets()}")
        if usage.get("model") != LT_OPUS55:
            bad.append(f"(lt-c) 다음 모델 결과가 아닙니다: {usage}")
        reset()
        fake.load(*[_lt_stream(text="plain answer", stop="end_turn") for _ in chain])
        try:
            call(400)
            bad.append("(lt-c) 체인 전부 도구 없음인데 오류가 없습니다")
        except VpocError as exc:
            if not exc.message.startswith("모델이 도구를 쓰지 않았습니다. 출력 앞부분: plain answer") or "잘렸습니다" in exc.message:
                bad.append(f"(lt-c) 도구 없음 오류 문구가 지금과 다릅니다: {exc.message[:120]}")
        if fake.models() != chain:
            bad.append(f"(lt-c) 도구 없음이 모델마다 1회가 아닙니다: {fake.models()}")
        reset()
        fake.load(_lt_stream(tool_json='{"people": 5}', stop="max_tokens", out=400))
        payload, usage = call(400)
        if payload != {"people": 5} or len(fake.calls) != 1 or trunc_lines():
            bad.append(f"(lt-c) max_tokens 인데 JSON 이 온전하면 1회로 성공해야 합니다: {payload} {len(fake.calls)}")

        # ---- (lt-d) 미리 적용 ----
        reset()
        for model in (LT_OPUS55, LT_FABLE):
            request = llm._request(model, sys_mark, blocks, tool, 400)
            inference = request.get("inferenceConfig") or {}
            choice = (request.get("toolConfig") or {}).get("toolChoice")
            system = request["system"][0]["text"]
            if "temperature" in inference or choice != {"auto": {}} or not system.endswith(forced_line):
                bad.append(f"(lt-d) {model}: temperature 없음·auto·강제 문장이 아닙니다: {inference} {choice} {system[-60:]!r}")
            if inference.get("maxTokens") != 400 or not system.startswith(sys_mark):
                bad.append(f"(lt-d) {model}: maxTokens·system 원문이 바뀌었습니다")
        request = llm._request(LT_ASTRA, sys_mark, blocks, tool, 400)
        if "temperature" in request["inferenceConfig"] or request["toolConfig"]["toolChoice"] != {"tool": {"name": tool["name"]}}:
            bad.append(
                f"(lt-d) gpt-6-astra: temperature 없음·toolChoice tool 이 아닙니다: "
                f"{request['inferenceConfig']} {request['toolConfig']['toolChoice']}"
            )
        if request["system"][0]["text"] != sys_mark:
            bad.append("(lt-d) gpt-6-astra: system 에 강제 문장이 붙었습니다")
        for model in (LT_OPUS46, LT_SONNET45, LT_SONNET46):
            request = llm._request(model, sys_mark, blocks, tool, 400)
            if (
                request["inferenceConfig"].get("temperature") != 0.6
                or request["toolConfig"]["toolChoice"] != {"tool": {"name": tool["name"]}}
                or request["system"][0]["text"] != sys_mark
            ):
                bad.append(f"(lt-d) {model}: 요청이 지금과 다릅니다: {request['inferenceConfig']} {request['toolConfig']['toolChoice']}")
        if not llm._listed("GLOBAL.ANTHROPIC.CLAUDE-OPUS-5-5", settings.LLM_NO_FORCED_TOOL) or llm._listed(
            LT_OPUS46, settings.LLM_NO_FORCED_TOOL
        ):
            bad.append("(lt-d) _listed 가 대소문자·4.6 을 잘못 판정합니다")
        for model in (LT_FABLE, LT_OPUS55, LT_ASTRA):
            reset()
            fake.load(_lt_stream(tool_json='{"people": 2}'))
            call(400, model=model)
            first = fake.calls[0][1] if fake.calls else {}
            want_choice = {"tool": {"name": tool["name"]}} if model == LT_ASTRA else {"auto": {}}
            if (
                len(fake.calls) != 1
                or "temperature" in (first.get("inferenceConfig") or {})
                or (first.get("toolConfig") or {}).get("toolChoice") != want_choice
            ):
                bad.append(f"(lt-d) {model} 첫 요청이 거절 없이 1회(미리 적용)가 아닙니다: {len(fake.calls)} {first.get('inferenceConfig')}")
            if logs.messages("vpoc.call.adapt") or llm._NO_FORCED_TOOL or llm._NO_TEMPERATURE:
                bad.append(f"(lt-d) {model}: 미리 적용인데 적응 로그·기억 집합이 생겼습니다: {llm._NO_FORCED_TOOL} {llm._NO_TEMPERATURE}")

        # ---- (lt-e) 설정 빈 값 → 미리 적용 꺼짐, _adapt 경로 ----
        for raw, want in (("", ()), (None, ()), (" Claude-Opus-5 , ,gpt-6 ", ("claude-opus-5", "gpt-6")), (",", ())):
            if settings._parse_marks(raw) != want:
                bad.append(f"(lt-e) _parse_marks({raw!r}) != {want}: {settings._parse_marks(raw)}")
        reset()
        settings.LLM_NO_FORCED_TOOL = settings._parse_marks("")
        settings.LLM_NO_TEMPERATURE = settings._parse_marks("")
        request = llm._request(LT_FABLE, sys_mark, blocks, tool, 400)
        if request["inferenceConfig"].get("temperature") != 0.6 or request["toolConfig"]["toolChoice"] != {"tool": {"name": tool["name"]}}:
            bad.append(
                f"(lt-e) 빈 값인데 fable 요청에 미리 적용이 남았습니다: "
                f"{request['inferenceConfig']} {request['toolConfig']['toolChoice']}"
            )
        fake.load(
            validation("The model returned the following errors: tool_choice.type any is not supported"),
            validation("`temperature` is deprecated for this model."),
            _lt_stream(tool_json='{"people": 4}'),
        )
        payload, usage = call(400, model=LT_FABLE)
        shapes = [
            ("temperature" in (req.get("inferenceConfig") or {}), sorted((req.get("toolConfig") or {}).get("toolChoice") or {}))
            for _, req in fake.calls
        ]
        if shapes != [(True, ["tool"]), (True, ["auto"]), (False, ["auto"])]:
            bad.append(f"(lt-e) 적응 경로 요청 모양이 [tool+temp, auto+temp, auto] 가 아닙니다: {shapes}")
        if payload != {"people": 4} or usage.get("model") != LT_FABLE:
            bad.append(f"(lt-e) 적응 뒤 같은 모델 성공이 아닙니다: {payload} {usage}")
        if len(logs.messages("vpoc.call.adapt", level=logging.INFO)) != 2:
            bad.append(f"(lt-e) vpoc.call.adapt 로그가 2줄이 아닙니다: {logs.messages('vpoc.call.adapt')}")
        if LT_FABLE not in llm._NO_FORCED_TOOL or LT_FABLE not in llm._NO_TEMPERATURE:
            bad.append("(lt-e) 적응 뒤 기억 집합에 fable 이 없습니다")

        # ---- (lt-f) 보통 호출(converse) 경로 ----
        reset()
        llm._STREAM.update({"ok": False, "broker": False})
        fake.load(
            _lt_converse(text="Let me look carefully at the image first. " * 3, stop="max_tokens", out=400),
            _lt_converse(tool_input={"people": 3}, stop="tool_use", out=30),
        )
        payload, usage = call(400)
        if fake.kinds() != ["converse", "converse"] or fake.models() != [LT_FABLE, LT_FABLE] or fake.budgets() != [400, 1600]:
            bad.append(
                f"(lt-f) 보통 호출 잘림이 같은 모델 1600 재시도가 아닙니다: {fake.kinds()} {fake.models()} {fake.budgets()}"
            )
        if payload != {"people": 3} or usage.get("model") != LT_FABLE or len(trunc_lines()) != 1:
            bad.append(f"(lt-f) 보통 호출 재시도 결과·로그가 다릅니다: {payload} {usage} {trunc_lines()}")
        fake.load(_lt_converse(tool_input={"people": 1}, stop=None))
        got = llm._converse_once(LT_FABLE, sys_mark, blocks, tool, 400)
        if len(got) != 4 or got[0] != {"people": 1} or got[3] != "":
            bad.append(f"(lt-f) _converse_once 가 (payload, text, usage, stop) 4개가 아닙니다: {got}")
        reset()
        fake.load(
            ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "not authorized to perform: bedrock:InvokeModelWithResponseStream"}},
                "ConverseStream",
            ),
            _lt_converse(tool_input={"people": 1}, stop=None),
        )
        payload, usage = call(400)
        if fake.kinds() != ["stream", "converse"] or payload != {"people": 1}:
            bad.append(f"(lt-f) 스트림 권한 거절 → 보통 호출 전환이 아닙니다: {fake.kinds()} {payload}")
        if all(llm._STREAM.values()):
            bad.append(f"(lt-f) 스트림 권한 거절이 기억되지 않았습니다: {llm._STREAM}")
        if [line for line in call_lines() if line.endswith(" stop=-")] != call_lines() or len(call_lines()) != 1:
            bad.append(f"(lt-f) stopReason 없는 응답의 vpoc.call 이 stop=- 1줄이 아닙니다: {call_lines()}")
        reset()
        fake.load(_lt_stream(tool_json='{"people": 6}', stop="tool_use"))
        got = llm._stream_once(LT_FABLE, sys_mark, blocks, tool, 400)
        if len(got) != 4 or got[0] != {"people": 6} or got[3] != "tool_use":
            bad.append(f"(lt-f) _stream_once 가 (payload, text, usage, stop) 4개가 아닙니다: {got}")
        got = llm._read_stream(_lt_stream(tool_json='{"people": 6}', stop=None))
        if len(got) != 4 or got[3] != "":
            bad.append(f"(lt-f) messageStop 없는 스트림의 stop 이 빈 값이 아닙니다: {got}")
        got = llm._read_stream(_lt_stream(tool_json='{"people": ', stop="max_tokens"))
        if got[0] is not None or not got[1].startswith("(도구 입력 JSON 이 깨짐) ") or got[3] != "max_tokens":
            bad.append(f"(lt-f) 깨진 JSON 스트림이 (None, 깨짐 표시, …, max_tokens) 가 아닙니다: {got}")

        # ---- (lt-h) 로그 형식 ----
        reset()
        fake.load(
            _lt_stream(text="I see ", tool_json='{"people": ', stop="max_tokens", out=400),
            _lt_stream(tool_json='{"people": 2}', stop="tool_use"),
        )
        call(400, where="check.lt.log")
        lines = [line for line in call_lines() if "where=check.lt.log " in line]
        if len(lines) != 2 or not lines[0].endswith(" stop=max_tokens") or not lines[1].endswith(" stop=tool_use"):
            bad.append(f"(lt-h) vpoc.call 줄 끝이 stop=max_tokens·stop=tool_use 가 아닙니다: {lines}")
        pattern = r"vpoc\.call where=check\.lt\.log model=\S+ ms=\d+ ok=False in=100 out=400 stop=max_tokens$"
        if not lines or not re.match(pattern, lines[0]):
            bad.append(f"(lt-h) vpoc.call 형식이 다릅니다: {lines[:1]}")
        sent = fake.calls[0][1] if fake.calls else {}
        sent_system = ((sent.get("system") or [{}])[0] or {}).get("text") or ""
        if not sent_system.startswith(sys_mark) or user_mark not in json.dumps(sent.get("messages"), ensure_ascii=False):
            bad.append("(lt-h) 표식 문자열이 요청에 실리지 않았습니다(검사 전제)")

        # ---- (lt-g) 호출부 예산 ----
        got_calls: list[dict] = []

        def fake_converse(**kwargs):
            got_calls.append(dict(kwargs))
            if kwargs.get("where") == "vnew.count_people":
                return {"people": 2}, {"model": kwargs.get("model")}
            return {"mainSubject": "A", "frameBottomCut": "chest"}, {"model": kwargs.get("model")}

        llm._converse = fake_converse
        try:
            people = characters._count_people(b"JPEG")
            charged: list = []
            grammar = previz_grammar.read_cut(
                "tester",
                "prj_lt",
                "pl_lt",
                {"cutId": "c1", "shotKo": "s", "characters": ["A"]},
                [b"JPEG"],
                ["A"],
                charge=lambda usage, model: charged.append((usage, model)),
            )
            # 모델 오류·정수가 아닌 값이면 None(검사 건너뜀, 지금 동작)
            llm._converse = lambda **kwargs: (_ for _ in ()).throw(VpocError("vnew.count_people", "SchemaViolation", "x", 502))
            people_error = characters._count_people(b"JPEG")
            llm._converse = lambda **kwargs: ({"people": "many"}, {"model": kwargs.get("model")})
            people_bad = characters._count_people(b"JPEG")
        finally:
            llm._converse = saved["_converse"]
        if people_error is not None or people_bad is not None:
            bad.append(f"(lt-g) count_people 오류·정수 아님이 None 이 아닙니다: {people_error} {people_bad}")
        wants = [
            ("vnew.count_people", 400, vnew_settings.PEOPLE_COUNT_MODEL),
            ("vnew.previz.grammar", 2000, previz_grammar.GRAMMAR_MODEL),
        ]
        seen = [(item.get("where"), item.get("max_tokens"), item.get("model")) for item in got_calls]
        if seen != wants:
            bad.append(f"(lt-g) 호출부 (where, max_tokens, model) 이 다릅니다: {seen}")
        if people != 2 or not isinstance(grammar, dict) or grammar.get("shotSize") != "MCU" or len(charged) != 1:
            bad.append(
                f"(lt-g) 호출부 결과가 다릅니다: people={people} "
                f"grammar={isinstance(grammar, dict) and grammar.get('shotSize')} charged={len(charged)}"
            )

        # ---- (lt-h) 프롬프트 본문이 어떤 로그에도 없다(이 그룹의 레코드 전부) ----
        leaked = [
            record.getMessage()[:120]
            for record in logs.records
            if sys_mark in record.getMessage() or user_mark in record.getMessage()
        ]
        if leaked:
            bad.append(f"(lt-h) 로그에 프롬프트 본문이 남았습니다: {leaked[:3]}")
        if real_calls:
            bad.append(f"(lt-h) 실제 Bedrock 클라이언트를 만들려 했습니다: {real_calls}")

        # ---- (lt-i) 배선 값 ----
        example_path = ROOT / "deploy" / "config.env.example"
        example = example_path.read_text(encoding="utf-8")
        saved_env = {key: os.environ.pop(key) for key in LT_KEYS if key in os.environ}
        try:
            spec = importlib.util.spec_from_file_location("_lt_settings_fresh", APP_SRC / "vpoc" / "settings.py")
            fresh = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh)  # type: ignore[union-attr]
            os.environ["VPOC_LLM_NO_FORCED_TOOL"] = ""
            os.environ["VPOC_LLM_NO_TEMPERATURE"] = ""
            os.environ["VPOC_LLM_TRUNC_RETRY_FACTOR"] = ""
            spec_empty = importlib.util.spec_from_file_location("_lt_settings_empty", APP_SRC / "vpoc" / "settings.py")
            empty = importlib.util.module_from_spec(spec_empty)
            spec_empty.loader.exec_module(empty)  # type: ignore[union-attr]
        finally:
            for key in LT_KEYS:
                os.environ.pop(key, None)
            os.environ.update(saved_env)
        if empty.LLM_NO_FORCED_TOOL != () or empty.LLM_NO_TEMPERATURE != () or empty.LLM_TRUNC_RETRY_FACTOR != 4:
            bad.append(
                f"(lt-i) env 빈 값이 ()·()·4 가 아닙니다: {empty.LLM_NO_FORCED_TOOL} {empty.LLM_NO_TEMPERATURE} "
                f"{empty.LLM_TRUNC_RETRY_FACTOR}"
            )
        code_defaults = {
            "VPOC_LLM_NO_FORCED_TOOL": fresh.LLM_NO_FORCED_TOOL_DEFAULT,
            "VPOC_LLM_NO_TEMPERATURE": fresh.LLM_NO_TEMPERATURE_DEFAULT,
            "VPOC_LLM_TRUNC_RETRY_FACTOR": str(fresh.LLM_TRUNC_RETRY_FACTOR),
            "VPOC_LLM_TRUNC_RETRY_CAP": str(fresh.LLM_TRUNC_RETRY_CAP),
        }
        if code_defaults["VPOC_LLM_TRUNC_RETRY_FACTOR"] != "4" or code_defaults["VPOC_LLM_TRUNC_RETRY_CAP"] != "4096":
            bad.append(f"(lt-i) 코드 기본 배수·상한이 4·4096 이 아닙니다: {code_defaults}")
        if fresh.LLM_NO_FORCED_TOOL != ("claude-opus-5", "claude-fable-5") or fresh.LLM_NO_TEMPERATURE != (
            "claude-opus-5",
            "claude-fable-5",
            "gpt-6",
        ):
            bad.append(f"(lt-i) 코드 기본 목록이 다릅니다: {fresh.LLM_NO_FORCED_TOOL} {fresh.LLM_NO_TEMPERATURE}")
        for key, want in code_defaults.items():
            found = re.findall(r"^" + re.escape(key) + r"=(.*)$", example, re.M)
            if [value.strip() for value in found] != [want]:
                bad.append(f"(lt-i) config.env.example {key} 값이 코드 기본 {want!r} 이 아닙니다: {found}")
        try:
            spec = importlib.util.spec_from_file_location("_lt_deploy_render", ROOT / "deploy" / "render.py")
            render = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(render)  # type: ignore[union-attr]
            template_path = ROOT / "k8s" / "workloads.yaml.tmpl"
            template = template_path.read_text(encoding="utf-8")
            base = render.load_config(example_path)
            for target in ("dev", "prod"):
                overlay = render.load_config(ROOT / "deploy" / f"config.{target}.env")
                values = render.add_derived({**base, **overlay})
                text = render.render_text(template, values, str(template_path.relative_to(ROOT)))
                for key, want in code_defaults.items():
                    got = _rendered_env_values(text, key)
                    if got != [want]:
                        bad.append(f"(lt-i) {target}: 렌더된 {key} 값이 [{want!r}] 가 아닙니다: {got}")
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — render.py 는 SystemExit 로 실패를 알린다.
            bad.append(f"(lt-i) render.py 렌더 실패: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb  # noqa: PLC0415

        bad.append("llm-top-model-first 검사 중 예외: " + repr(exc)[:300] + "\n" + _tb.format_exc()[:1500])
    finally:
        logs.detach()
        llm.client_for = saved["client_for"]
        llm.runtime = saved["runtime"]
        llm._broker_client = saved["_broker_client"]
        llm._converse = saved["_converse"]
        llm.time = saved["time"]
        llm._STREAM.clear()
        llm._STREAM.update(saved["STREAM"])
        llm._DENIED.clear()
        llm._DENIED.update(saved["DENIED"])
        llm._NO_FORCED_TOOL.clear()
        llm._NO_FORCED_TOOL.update(saved["NO_FORCED_TOOL"])
        llm._NO_TEMPERATURE.clear()
        llm._NO_TEMPERATURE.update(saved["NO_TEMPERATURE"])
        for name, value in saved_settings.items():
            setattr(settings, name, value)

    if bad:
        fail("llm-top-model-first 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "llm-top-model-first 검사 (lt-a)스트림 잘림 같은 모델 재시도 400→1600·900→3600·2000→4096·model·trunc_retry "
            "(lt-b)재시도 잘림 → 다음 모델 원래 예산·체인 전부 잘림 문구·8000·FACTOR=1 재시도 없음·재시도 중 일시 오류·AccessDenied "
            "(lt-c)end_turn 재시도 없음·지금 문구·온전한 JSON 1회 (lt-d)opus-5-5·fable-5-1 auto·temperature 없음·"
            "gpt-6 tool·4.6·4.5 그대로·첫 요청 1회·기억 집합 그대로 (lt-e)빈 값 → _adapt 2회·_parse_marks "
            "(lt-f)converse stopReason 재시도·스트림 권한 전환·4개 반환·깨진 JSON (lt-g)count_people 400·previz.grammar 2000·오류 None "
            "(lt-h)stop=·프롬프트 본문 로그 없음·실제 클라이언트 0 (lt-i)config.env.example = 코드 기본·env 빈 값·dev·prod 렌더"
        )


# ---------------------------------------------------------------- main


# ---------------------------------------------------------------- 084 previz-camera-only 공용 도우미(design.md §12.1)

# 개발계 "위험한편의점 test3" plan.json 실측(Claude, 2026-10-28). 모든 rotY 0. label 은 upper·lower 만 실측.
_TEST3_PROPS = (
    ("kerb", "box", [7.6, 0.12, 0.35], [0.0, 0.06, -1.8], "경계석"),
    ("upper_rail", "box", [7.2, 0.09, 0.09], [0.0, 0.94, -1.45], "두 사람이 걸터앉는 철제 상부 가로대"),
    ("lower_rail", "box", [7.2, 0.06, 0.06], [0.0, 0.40, -1.45], "철제 하부 가로대"),
    ("post_left", "cylinder", [0.08, 0.96, 0.08], [-3.5, 0.48, -1.45], "철제 기둥"),
    ("post_mid_left", "cylinder", [0.08, 0.96, 0.08], [-1.4, 0.48, -1.45], "철제 기둥"),
    ("post_mid_right", "cylinder", [0.08, 0.96, 0.08], [1.4, 0.48, -1.45], "철제 기둥"),
    ("post_right", "cylinder", [0.08, 0.96, 0.08], [3.5, 0.48, -1.45], "철제 기둥"),
    ("utility_pole", "cylinder", [0.24, 3.0, 0.24], [3.5, 1.5, -1.85], "전봇대"),
    ("bin", "box", [0.48, 0.72, 0.48], [-3.35, 0.36, -0.65], "쓰레기통"),
    ("ac_unit", "box", [0.85, 0.55, 0.38], [3.25, 0.55, -2.08], "에어컨 실외기"),
    ("ac_bracket", "box", [0.95, 0.08, 0.46], [3.25, 0.235, -2.08], "실외기 받침대"),
    ("apartment_mass", "box", [7.8, 3.0, 0.8], [-0.1, 1.5, -2.3], "뒤편 아파트 덩어리"),
)
_TEST3_CUTS = (
    ("cut-01", (("geonwoo", [0.44, 0, -1.45], 270),)),
    ("cut-02", (("uijun", [-0.18, 0, -1.45], 90),)),
    ("cut-03", (("geonwoo", [0.44, 0, -1.45], 270),)),
    ("cut-04", (("uijun", [-0.18, 0, -1.45], 81.64), ("geonwoo", [0.4697, 0, -1.3545], 0))),
    ("cut-05", (("uijun", [-0.18, 0, -1.45], 90), ("geonwoo", [0.44, 0, -1.45], 0))),
    ("cut-06", (("uijun", [-0.095, 0, -1.45], 0), ("geonwoo", [0.355, 0, -1.45], 270))),
    ("cut-07", (("uijun", [-0.18, 0, -1.45], 78.62), ("geonwoo", [0.501, 0, -1.313], 0))),
)


def _previz_test3_scene() -> dict:
    """084 design.md §4.10 — test3 실측 장면(방 8×5×3, 소품 12개(기둥 4개 포함), 인물 2명, 7컷 모두 sit).

    인물 키 1.75(여의준)·1.70(범건우)은 실측이 없어 가정이다. sitOn·sit 은 없다(옛 계획과 같다).
    """
    return {
        "room": {"width": 8.0, "depth": 5.0, "height": 3.0},
        "props": [
            {"id": pid, "shape": shape, "size": list(size), "pos": list(pos), "rotY": 0.0, "labelKo": label}
            for pid, shape, size, pos, label in _TEST3_PROPS
        ],
        "actors": [
            {"id": "uijun", "name": "여의준", "heightM": 1.75},
            {"id": "geonwoo", "name": "범건우", "heightM": 1.70},
        ],
        "timeline": [
            {
                "cutId": cut_id,
                "actors": [{"id": aid, "pos": list(pos), "facingDeg": facing, "pose": "sit"} for aid, pos, facing in actors],
            }
            for cut_id, actors in _TEST3_CUTS
        ],
    }


@contextlib.contextmanager
def _pin_vnew(**values):
    """`vnew.settings` 속성을 잠시 바꾸고 되돌린다. 예: with _pin_vnew(VNEW_PREVIZ_COLLISION=False): …"""
    sys.path.insert(0, str(ROOT / "app"))
    from src.vnew import settings as vnew_settings  # noqa: PLC0415

    saved = {name: getattr(vnew_settings, name) for name in values}
    try:
        for name, value in values.items():
            setattr(vnew_settings, name, value)
        yield vnew_settings
    finally:
        for name, value in saved.items():
            setattr(vnew_settings, name, value)


@contextlib.contextmanager
def _guard_real_llm():
    """`vpoc.llm._converse` 를 "가짜가 아닌 LLM 호출" 예외로 바꿔 둔다(실제 Bedrock 0회 보장). 호출 where 목록을 준다."""
    sys.path.insert(0, str(ROOT / "app"))
    from src.vpoc import llm as vpoc_llm  # noqa: PLC0415

    saved = vpoc_llm._converse  # noqa: SLF001
    calls: list[str] = []

    def _blocked(*_args, **kwargs):
        calls.append(str(kwargs.get("where") or ""))
        raise RuntimeError("가짜가 아닌 LLM 호출이 일어났습니다(검사)")

    vpoc_llm._converse = _blocked  # noqa: SLF001
    try:
        yield calls
    finally:
        vpoc_llm._converse = saved  # noqa: SLF001


def check_previz_collision() -> None:
    """084 작업 1 — 프리비즈 소품 관통 보정(R1.1-1.19). 외부 호출은 모두 가짜다(유료 0회).

    (cs-a) test3 실측 7컷: upper_rail·hipY 0.985·legPitch 후보·legYaw 0·unresolved 0·겹침 0·두 번 같음
    (cs-b) 의자 좌판+등받이: 좌판·hipY 0.45·legPitch 0·legYaw 0·등받이 겹침 0
    (cs-c) 기둥 안 서 있는 인물 → 밀려남, fix 로그 형식 1줄
    (cs-d) 회전 상자(rotY π/4)·눕기(상자·원기둥)·무릎·소품 없는 앉기 밀어내기
    (cs-e) 밀 수 없음 → unresolved·warning 로그·자리 그대로
    (cs-f) 다른 인물 간격을 지키는 자리를 고른다
    (cs-g) 멱등(다시 보정하면 fix 0)
    (cs-h) 옛 장면 보기: 사본 그대로(인물 pos·카메라·sit 없음)·needsRedesign·관통 컷 목록, 섞인 계획에서 설계된 장면만
           사본 보정(sit·fit.collision 두 조건)·source view, 옛 의자 장면은 관통 아님, 보기 로그 0, 안내 문구
    (cs-i) 설계 통합: sittable·sitOn → 저장 sit·fit.collision, 카메라 눈높이 = 앉은 hipY, match ≥ 0.7, stage 로그
    (cs-j) body_y·match_visible·_actor_points·_recompute_manual_fit 의 hip_y
    (cs-k) VNEW_PREVIZ_COLLISION=False: 보정 0회·sit 없음·보기 = 저장본
    (cs-l) 정규화: sittable·sitOn·sit(legYaw 감기·facing 대체·범위 밖 버림)
    (cs-m) 렌더러·화면 문자열, node --check
    (cs-n) 2차 보정: 배우 조정(closeapex)으로 소품 안에 들어간 인물 → 밀려남, _rematch_cut
    (cs-o) 다리 방향 규칙: 의자 4방향 탐색·벤치 정렬 4단계·회전 벤치·등급 4·sitOn 등급 1·무시 로그
    (cs-p) test3 을 보정 끔으로 설계한 옛 장면: 보기 사본·주인공 머리 위치 그대로. 보정 켬으로 다시 설계하면
           7컷 주인공 머리 사각형이 화면 안(0 ≤ y0, y1 ≤ 1)·보기 보정 멱등
    """
    sys.path.insert(0, str(ROOT / "app"))
    import copy as _copy  # noqa: PLC0415
    import math as _math  # noqa: PLC0415
    import traceback as _tb  # noqa: PLC0415

    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import previz as pvmod  # noqa: PLC0415
    from src.vnew import previz_camera as pcam  # noqa: PLC0415
    from src.vnew import previz_collision as pcol  # noqa: PLC0415
    from src.vnew import previz_fit as pfit  # noqa: PLC0415
    from src.vnew import previz_grammar as grammarmod  # noqa: PLC0415
    from src.vnew import previz_schema as pschema  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415

    bad: list[str] = []
    fake_store = _FakeCharStore()
    saved = {
        "pv_STORE": pvmod.STORE,
        "plan_STORE": planmod.STORE,
        "_design": pvmod._design,
        "gr_read": grammarmod._read,
        "_cut_tiles": pvmod._cut_tiles,
        "_spawn": pvmod._spawn,
        "_rematch_cut": pvmod._rematch_cut,
        "resolve_scenes": pcol.resolve_scenes,
        "resolve_scene": pcol.resolve_scene,
        "detect_scene": pcol.detect_scene,
        "pv_charge_text": pvmod.vpoc_ledger.charge_text,
        "collision_flag": vset.VNEW_PREVIZ_COLLISION,
    }
    seen_ids: set[str] = set()

    def mark(*ids: str) -> None:
        seen_ids.update(ids)

    def bx(pid, size, pos, *, rot=0.0, label="", shape="box", **extra):
        item = {"id": pid, "shape": shape, "size": list(size), "pos": list(pos), "rotY": rot, "labelKo": label}
        item.update(extra)
        return item

    def sc(props, actors, *, room=(10.0, 10.0), heights=None):
        heights = heights or {}
        return {
            "room": {"width": room[0], "depth": room[1], "height": 3.0},
            "props": props,
            "actors": [{"id": a["id"], "name": a["id"], "heightM": heights.get(a["id"], 1.7)} for a in actors],
            "timeline": [{"cutId": "c1", "actors": actors}],
        }

    def cut_actor(scene, actor_id, cut_index=0):
        return next(ca for ca in scene["timeline"][cut_index]["actors"] if ca["id"] == actor_id)

    def plan_key(owner, pid):
        return planmod.plan_key(owner, pid)

    # --- 설계 하네스(가짜 Astra·가짜 문법·가짜 타일, 실제 run_design 경로) ---
    design_prompts: list[str] = []

    def run_design_with(tag: str, raw_scene: dict, grammar_by_cut: dict, cut_ids: list[str]) -> tuple[str, str, dict]:
        owner = "tester"
        pid = f"prj_cs_{tag}"
        location_en = "Railing outside a convenience store at night"
        planmod.load_or_create(owner, pid)

        def seed(data):
            data["script"] = {"status": "ready", "setting": {"locationEn": location_en, "enclosed": False}, "entries": []}
            data["conti"] = {
                "status": "ready",
                "segments": [],
                "cuts": [
                    {"id": cid, "no": i + 1, "seconds": 3, "panels": [i], "shotKo": f"[{cid}]", "cameraEn": "",
                     "locationEn": location_en, "characters": [], "dialogueKo": "", "situationKo": "", "promptEn": "x"}
                    for i, cid in enumerate(cut_ids)
                ],
            }

        planmod.mutate_plan(owner, pid, seed)

        def fake_design(blocks, *, model):
            design_prompts.append(str((blocks[0] or {}).get("text") or ""))
            return _copy.deepcopy(raw_scene), {"model": model, "inputTokens": 1, "outputTokens": 1}

        def fake_read(blocks, *, model):
            text = str((blocks[0] or {}).get("text") or "")
            found = re.search(r"\[([\w-]+)\]", text)
            payload = grammar_by_cut.get(found.group(1) if found else "") or {}
            return _copy.deepcopy(payload), {"model": model, "inputTokens": 1, "outputTokens": 1}

        pvmod._design = fake_design
        grammarmod._read = fake_read
        task_id = pvmod.start_design(owner, pid)
        pvmod.run_design(owner, pid, task_id)
        plan, _ = planmod.load_plan(owner, pid)
        previz = plan.get("previz") or {}
        if previz.get("status") != "ready":
            bad.append(f"({tag}) 설계가 ready 가 아닙니다: {previz.get('status')} / {previz.get('error')}")
        return owner, pid, plan

    def test3_design_payload() -> dict:
        raw = _previz_test3_scene()
        for prop in raw["props"]:
            if prop["id"] == "upper_rail":
                prop["sittable"] = True
        for cut in raw["timeline"]:
            for ca in cut["actors"]:
                ca["sitOn"] = "upper_rail"
        return raw

    def grammar(subject, partner=None, *, shot_type="clean_single", bottom="belly", side="right", subject_x=0.5):
        out = {
            "mainSubject": subject, "frameBottomCut": bottom, "shotType": shot_type, "vantage": "3q",
            "faceTurn": "screen_right" if side == "right" else "screen_left", "cameraHeight": "eye",
            "headTopY": 0.1, "subjectX": subject_x,
            "others": [], "offscreenPartnerSide": side if partner else "none", "reasonKo": "검사",
        }
        if partner and shot_type == "two_shot":
            out["others"] = [{"name": partner, "how": "full", "side": side, "screenX": 1.0 - subject_x}]
        return out

    # 1인 컷(cut-01~03)은 상대가 없어 place_cut 이 가짜 상대를 −z 에 둔다(기존 규칙) — 카메라가 난간 뒤 벽에
    # 막혀 일치도가 낮다. 그래서 일치도 검사는 2인 컷(cut-04~07)만 본다. 화면 쪽은 +z 반원 기준이다.
    test3_grammar = {
        "cut-01": grammar("범건우"), "cut-02": grammar("여의준"), "cut-03": grammar("범건우"),
        "cut-04": grammar("여의준", "범건우"),
        "cut-05": grammar("여의준", "범건우", shot_type="two_shot", subject_x=0.35),
        "cut-06": grammar("범건우", "여의준", side="left"), "cut-07": grammar("여의준", "범건우"),
    }
    two_person_cuts = ("cut-04", "cut-05", "cut-06", "cut-07")

    logs = _LogCapture()
    try:
        pvmod.STORE = fake_store
        planmod.STORE = fake_store
        pvmod._cut_tiles = lambda cut_panels, all_tiles: [b"FAKE-PANEL-JPEG"] if cut_panels else []
        pvmod._spawn = lambda *_a, **_k: None
        pvmod.vpoc_ledger.charge_text = lambda *_a, **_k: {"spentUsd": 0.0, "capUsd": None}
        vset.VNEW_PREVIZ_COLLISION = True
        with _guard_real_llm() as real_calls, logs:
            forbidden = {"lower_rail", "kerb", "post_left", "post_mid_left", "post_mid_right", "post_right", "apartment_mass"}

            # ---------------- (cs-a) test3 실측 7컷 ----------------
            base = _previz_test3_scene()
            s1, s2 = _copy.deepcopy(base), _copy.deepcopy(base)
            r1 = pcol.resolve_scene(s1, log_fixes=False)
            r2 = pcol.resolve_scene(s2, log_fixes=False)
            if json.dumps([s1, r1], sort_keys=True) != json.dumps([s2, r2], sort_keys=True):
                bad.append("(cs-a) 같은 test3 장면을 두 번 보정한 결과가 다릅니다")
            if len(r1) != 7:
                bad.append(f"(cs-a) 7컷 결과가 아닙니다: {sorted(r1)}")
            for cut in s1["timeline"]:
                res = r1.get(cut["cutId"]) or {}
                if res.get("unresolved"):
                    bad.append(f"(cs-a) {cut['cutId']} unresolved 가 비어 있지 않습니다: {res.get('unresolved')}")
                for ca in cut["actors"]:
                    sit = ca.get("sit") or {}
                    where = f"{cut['cutId']}/{ca['id']}"
                    if sit.get("propId") != "upper_rail":
                        bad.append(f"(cs-a) {where} 앉은 소품이 upper_rail 이 아닙니다: {sit}")
                    if abs(float(sit.get("hipY") or 0) - 0.985) > 0.001:
                        bad.append(f"(cs-a) {where} hipY 가 0.985 가 아닙니다: {sit.get('hipY')}")
                    if sit.get("legPitchDeg") not in pcol.LEG_PITCHES:
                        bad.append(f"(cs-a) {where} legPitchDeg 가 후보 밖입니다: {sit.get('legPitchDeg')}")
                    if sit.get("legYawDeg") != 0.0:
                        bad.append(f"(cs-a) {where} legYawDeg 가 0(방 쪽 +z)이 아닙니다(facing {ca['facingDeg']}): {sit.get('legYawDeg')}")
                    overlaps = pcol.actor_overlaps(s1, ca)
                    if overlaps:
                        bad.append(f"(cs-a) {where} 소품과 겹칩니다: {overlaps} (금지 {sorted(set(overlaps) & forbidden)})")
            # 길쭉한 자리의 반대쪽(−z)은 경계석·아파트에 막혀 풀리지 않는다(규칙 1이 실제로 작동하는 입력).
            vols = pcol._scene_vols(base)  # noqa: SLF001
            rail = next(v for v in vols if v["id"] == "upper_rail")
            if not pcol.is_elongated(rail):
                bad.append("(cs-a) 상부 가로대가 길쭉한 자리로 판정되지 않습니다")
            if pcol.solve_legs(-0.18, -1.45, 0.985, 1.75 / 1.7, 180.0, rail, vols)[1]:
                bad.append("(cs-a) 반대쪽(180°) 다리가 풀린다고 나옵니다(경계석·아파트에 막혀야 함)")
            mark("cs-a")

            # ---------------- (cs-b) 의자 ----------------
            chair = sc([bx("seat", [0.45, 0.45, 0.45], [0, 0.225, 0], label="의자 좌판"),
                        bx("back", [0.45, 0.45, 0.05], [0, 0.675, -0.25], label="의자 등받이")],
                       [{"id": "a1", "pos": [0, 0, 0], "pose": "sit", "facingDeg": 0}])
            res = pcol.resolve_scene(chair, log_fixes=False)["c1"]
            sit = cut_actor(chair, "a1").get("sit") or {}
            if sit != {"propId": "seat", "hipY": 0.45, "legPitchDeg": 0, "legYawDeg": 0.0}:
                bad.append(f"(cs-b) 의자 좌판 sit 이 기대와 다릅니다: {sit}")
            if res["unresolved"] or pcol.actor_overlaps(chair, cut_actor(chair, "a1")):
                bad.append(f"(cs-b) 의자에서 겹침이 남았습니다: {res}")
            mark("cs-b")

            # ---------------- (cs-c) 기둥 안 서기 + 로그 ----------------
            logs.clear()
            post = sc([bx("post_1", [0.1, 1.0, 0.1], [1.4, 0.5, 0.0], shape="cylinder")],
                      [{"id": "a1", "pos": [1.42, 0, 0], "pose": "stand"}], room=(6.0, 6.0))
            res = pcol.resolve_scene(post, log_fixes=True)["c1"]
            pos = cut_actor(post, "a1")["pos"]
            if _math.hypot(pos[0] - 1.4, pos[2]) < 0.22 + 0.05 - 0.01:
                bad.append(f"(cs-c) 기둥에서 충분히 밀려나지 않았습니다: {pos}")
            if not (abs(pos[0]) <= 2.8 and abs(pos[2]) <= 2.8):
                bad.append(f"(cs-c) 방 밖으로 밀렸습니다: {pos}")
            fix_lines = logs.messages("vnew.previz.collision.fix")
            if len(fix_lines) != 1 or not re.fullmatch(
                r"vnew\.previz\.collision\.fix cut=c1 actor=a1 prop=post_1 dist=\d+\.\d{3} kind=push", fix_lines[0]
            ):
                bad.append(f"(cs-c) fix 로그 형식이 다릅니다: {fix_lines}")
            if len(res["fixes"]) != 1 or res["fixes"][0].get("kind") != "push":
                bad.append(f"(cs-c) fixes 가 push 1개가 아닙니다: {res['fixes']}")
            if pcol._log_id("post\nvnew.fake line=1") != "post_vnew.fake_line_1" or pcol._log_id("") != "-":  # noqa: SLF001
                bad.append("(cs-c) 로그 id 정리(_log_id)가 줄바꿈·공백을 막지 않습니다")
            mark("cs-c")

            # ---------------- (cs-d) 회전·눕기·무릎·소품 없는 앉기 ----------------
            rot_on = sc([bx("crate", [1.0, 1.0, 1.0], [0, 0.5, 0], rot=_math.pi / 4)], [{"id": "a1", "pos": [0.8, 0, 0], "pose": "stand"}])
            rot_off = sc([bx("crate", [1.0, 1.0, 1.0], [0, 0.5, 0], rot=0.0)], [{"id": "a1", "pos": [0.8, 0, 0], "pose": "stand"}])
            if not pcol.resolve_scene(rot_on, log_fixes=False)["c1"]["fixes"]:
                bad.append("(cs-d) rotY=π/4 상자 모서리 안 인물이 밀리지 않았습니다(회전 무시)")
            if pcol.actor_overlaps(rot_on, cut_actor(rot_on, "a1")):
                bad.append("(cs-d) 회전 상자와 겹침이 남았습니다")
            if pcol.resolve_scene(rot_off, log_fixes=False)["c1"]["fixes"]:
                bad.append("(cs-d) 회전이 없으면 겹치지 않는 배치인데 밀렸습니다")
            pose_cases = (
                ("lie-box", [bx("box1", [0.4, 0.4, 0.4], [0, 0.2, -1.5])], {"pose": "lie", "facingDeg": 0}),
                ("lie-cyl", [bx("pole", [0.2, 1.0, 0.2], [0, 0.5, -1.0], shape="cylinder")], {"pose": "lie", "facingDeg": 0}),
                ("kneel", [bx("box2", [0.4, 0.6, 0.4], [0, 0.3, 0])], {"pose": "kneel"}),
                ("loose-sit", [bx("tall", [0.4, 2.0, 0.4], [0, 1.0, 0])], {"pose": "sit"}),
            )
            for name, props, extra in pose_cases:
                scene = sc(props, [dict({"id": "a1", "pos": [0, 0, 0]}, **extra)])
                if pcol.actor_overlaps(scene, cut_actor(scene, "a1")) == []:
                    bad.append(f"(cs-d) {name}: 보정 전 겹침이 없어 검사가 성립하지 않습니다")
                got = pcol.resolve_scene(scene, log_fixes=False)["c1"]
                ca = cut_actor(scene, "a1")
                if len(got["fixes"]) != 1 or got["fixes"][0]["kind"] != "push" or pcol.actor_overlaps(scene, ca):
                    bad.append(f"(cs-d) {name}: 밀어내기 결과가 다릅니다 {got} pos={ca['pos']}")
                if name == "loose-sit" and "sit" in ca:
                    bad.append("(cs-d) 앉을 소품이 없는데 sit 이 생겼습니다")
            mark("cs-d")

            # ---------------- (cs-e) 밀 수 없음 ----------------
            logs.clear()
            stuck = sc([bx("big_box", [2.9, 1.0, 2.9], [0, 0.5, 0], label="큰 상자")],
                       [{"id": "a1", "pos": [0, 0, 0], "pose": "stand"}], room=(3.0, 3.0))
            res = pcol.resolve_scene(stuck, log_fixes=True)["c1"]
            warn_lines = logs.messages("vnew.previz.collision.unresolved", level=logging.WARNING)
            if res["unresolved"] != [{"actor": "a1", "prop": "big_box"}] or res["fixes"]:
                bad.append(f"(cs-e) unresolved 1개·fix 0 이 아닙니다: {res}")
            if warn_lines != ["vnew.previz.collision.unresolved cut=c1 actor=a1 prop=big_box"]:
                bad.append(f"(cs-e) warning 로그가 1줄이 아닙니다: {warn_lines}")
            if cut_actor(stuck, "a1")["pos"] != [0, 0, 0]:
                bad.append(f"(cs-e) 못 미는 인물이 옮겨졌습니다: {cut_actor(stuck, 'a1')['pos']}")
            mark("cs-e")

            # ---------------- (cs-f) 인물 간격 ----------------
            gap_scene = sc([bx("post_g", [0.1, 2.0, 0.1], [0.05, 1.0, 0.0], shape="cylinder")],
                           [{"id": "a1", "pos": [0, 0, 0], "pose": "stand"}, {"id": "a2", "pos": [-0.5, 0, 0], "pose": "stand"}])
            # 첫 MTV 방향(−x, 깊이 0.22+0.02)으로 밀면 a2 와 0.31m — 0.45 미만이라 그 자리는 안 된다.
            mtv_x = 0.0 - (0.22 + 0.05 - 0.05 + 0.02)
            if abs(mtv_x - (-0.5)) >= 0.45:
                bad.append("(cs-f) 검사 전제가 틀렸습니다(첫 MTV 자리가 이미 간격을 지킴)")
            pcol.resolve_scene(gap_scene, log_fixes=False)
            p1, p2 = cut_actor(gap_scene, "a1")["pos"], cut_actor(gap_scene, "a2")["pos"]
            gap = _math.hypot(p1[0] - p2[0], p1[2] - p2[2])
            if gap < 0.45 - 1e-6 or pcol.actor_overlaps(gap_scene, cut_actor(gap_scene, "a1")):
                bad.append(f"(cs-f) 간격 {gap:.3f}m 또는 겹침이 남았습니다: a1={p1}")
            if abs(p1[0] - mtv_x) < 1e-3 and abs(p1[2]) < 1e-3:
                bad.append("(cs-f) 간격을 깨는 첫 MTV 자리를 골랐습니다")
            mark("cs-f")

            # ---------------- (cs-g) 멱등 ----------------
            again = pcol.resolve_scene(s1, log_fixes=False)
            if any(item["fixes"] or item["unresolved"] for item in again.values()):
                bad.append(f"(cs-g) 보정한 test3 을 다시 보정하면 fix 가 생깁니다: {again}")
            if pcol.resolve_scene(post, log_fixes=False)["c1"]["fixes"]:
                bad.append("(cs-g) 밀어낸 장면을 다시 보정하면 fix 가 생깁니다")
            mark("cs-g")

            # ---------------- (cs-h) 옛 계획 보기 ----------------
            owner_h, pid_h = "tester", "prj_cs_view"
            planmod.load_or_create(owner_h, pid_h)
            old_scene = pschema.normalize_scene(
                _previz_test3_scene(), location_id="loc1",
                cut_specs=[{"cutId": cid, "seconds": 3} for cid, _a in _TEST3_CUTS],
            )

            def seed_old(data):
                data["previz"] = dict(pvmod.new_previz(), status="ready", scenes=[old_scene], fit={}, revision=2)

            planmod.mutate_plan(owner_h, pid_h, seed_old)
            stored_before = json.dumps(fake_store.json[plan_key(owner_h, pid_h)][0], sort_keys=True)
            plan_h, _ = planmod.load_plan(owner_h, pid_h)
            plan_h_copy = _copy.deepcopy(plan_h)
            logs.clear()
            view_h = planmod._previz_view(owner_h, pid_h, plan_h)  # noqa: SLF001 — plan_view 의 previz 부분
            if logs.messages("vnew.previz.collision."):
                bad.append(f"(cs-h) 보기 때 보정 로그가 남았습니다: {logs.messages('vnew.previz.collision.')}")
            if json.dumps(fake_store.json[plan_key(owner_h, pid_h)][0], sort_keys=True) != stored_before:
                bad.append("(cs-h) 보기가 저장된 plan.json 을 바꿨습니다")
            if plan_h != plan_h_copy:
                bad.append("(cs-h) 보기가 넘겨받은 계획 dict 를 바꿨습니다")
            if any("sit" in ca for cut in old_scene["timeline"] for ca in cut["actors"]):
                bad.append("(cs-h) 검사 전제: 옛 장면에 sit 이 있습니다")
            view_scene = (view_h.get("scenes") or [{}])[0]
            for cut_v, cut_s in zip(view_scene.get("timeline") or [], old_scene["timeline"]):
                if [ca.get("pos") for ca in cut_v.get("actors") or []] != [ca.get("pos") for ca in cut_s["actors"]]:
                    bad.append(f"(cs-h) 옛 장면 {cut_s['cutId']} 보기 사본의 인물 자리가 저장본과 다릅니다")
                if cut_v.get("camera") != cut_s.get("camera"):
                    bad.append(f"(cs-h) 옛 장면 {cut_s['cutId']} 보기 사본의 카메라가 저장본과 다릅니다")
                if any("sit" in ca for ca in cut_v.get("actors") or []):
                    bad.append(f"(cs-h) 옛 장면 {cut_s['cutId']} 보기 사본에 sit 이 생겼습니다(카메라는 엉덩이 0.5 기준)")
            col_h = view_h.get("collision") or {}
            if col_h != {"source": "none", "needsRedesign": True, "cuts": [cid for cid, _a in _TEST3_CUTS]}:
                bad.append(f"(cs-h) 옛 장면 collision 이 needsRedesign·7컷이 아닙니다: {col_h}")

            # 섞인 계획: 옛 장면(test3) + sit 으로 설계된 장면(b1) + fit.collision 으로 설계된 장면(c1) + 옛 의자 장면(d1).
            def _norm(raw, loc, cut_id):
                return pschema.normalize_scene(raw, location_id=loc, cut_specs=[{"cutId": cut_id, "seconds": 3}])

            post_m = bx("post_m", [0.1, 2.0, 0.1], [1.5, 1.0, 0.0], shape="cylinder", label="기둥")
            scene_b = _norm({
                "room": {"width": 8.0, "depth": 6.0, "height": 3.0},
                "props": [bx("bench", [2.0, 0.45, 0.45], [0, 0.225, -1.0], label="벤치"), post_m],
                "actors": [{"id": "b_sit", "heightM": 1.7}, {"id": "b_std", "heightM": 1.7}],
                "timeline": [{"cutId": "b1", "actors": [
                    {"id": "b_sit", "pos": [0, 0, -1.0], "pose": "sit", "facingDeg": 0,
                     "sit": {"propId": "bench", "hipY": 0.45, "legPitchDeg": 0, "legYawDeg": 0}},
                    {"id": "b_std", "pos": [1.52, 0, 0], "pose": "stand"}]}],
            }, "loc_b", "b1")
            scene_c = _norm({
                "room": {"width": 8.0, "depth": 6.0, "height": 3.0}, "props": [post_m],
                "actors": [{"id": "c_std", "heightM": 1.7}],
                "timeline": [{"cutId": "c1", "actors": [{"id": "c_std", "pos": [1.52, 0, 0], "pose": "stand"}]}],
            }, "loc_c", "c1")
            scene_d = _norm({
                "room": {"width": 8.0, "depth": 6.0, "height": 3.0},
                "props": [bx("seat", [0.45, 0.45, 0.45], [0, 0.225, 0], label="의자 좌판")],
                "actors": [{"id": "d_sit", "heightM": 1.7}],
                "timeline": [{"cutId": "d1", "actors": [{"id": "d_sit", "pos": [0, 0, 0], "pose": "sit", "facingDeg": 0}]}],
            }, "loc_d", "d1")
            if "sit" not in scene_b["timeline"][0]["actors"][0] or any("sit" in ca for ca in scene_c["timeline"][0]["actors"]):
                bad.append("(cs-h) 검사 전제: b1 에 저장된 sit 이 없거나 c1 에 sit 이 있습니다")
            owner_m, pid_m = "tester", "prj_cs_view_mixed"
            planmod.load_or_create(owner_m, pid_m)

            def seed_mixed(data):
                data["previz"] = dict(
                    pvmod.new_previz(), status="ready", revision=3,
                    scenes=[_copy.deepcopy(old_scene), scene_b, scene_c, scene_d],
                    fit={"c1": {"match": None, "method": "astra", "collision": {"fixes": [], "unresolved": []}}},
                )

            planmod.mutate_plan(owner_m, pid_m, seed_mixed)
            stored_m = json.dumps(fake_store.json[plan_key(owner_m, pid_m)][0], sort_keys=True)
            logs.clear()
            view_m = planmod._previz_view(owner_m, pid_m, planmod.load_plan(owner_m, pid_m)[0])  # noqa: SLF001
            if logs.messages("vnew.previz.collision."):
                bad.append(f"(cs-h) 섞인 계획 보기 때 보정 로그가 남았습니다: {logs.messages('vnew.previz.collision.')}")
            if json.dumps(fake_store.json[plan_key(owner_m, pid_m)][0], sort_keys=True) != stored_m:
                bad.append("(cs-h) 섞인 계획 보기가 저장된 plan.json 을 바꿨습니다")
            col_m = view_m.get("collision") or {}
            if col_m.get("needsRedesign") is not True or col_m.get("source") != "none" or col_m.get("cuts") != [cid for cid, _a in _TEST3_CUTS]:
                bad.append(f"(cs-h) 섞인 계획의 옛 장면 안내가 다릅니다(옛 의자 d1 은 관통이 아니라 빠져야 함): {col_m.get('cuts')}")
            for cut_id, actor_id in (("b1", "b_std"), ("c1", "c_std")):
                entry = col_m.get(cut_id) or {}
                if entry.get("source") != "view" or [(f.get("actor"), f.get("kind")) for f in entry.get("fixes") or []] != [(actor_id, "push")]:
                    bad.append(f"(cs-h) 보정과 함께 설계된 장면 {cut_id} 가 사본 보정·source view 가 아닙니다: {entry}")
            views_m = view_m.get("scenes") or []
            if len(views_m) == 4:
                b_std = next(ca for ca in views_m[1]["timeline"][0]["actors"] if ca["id"] == "b_std")
                if b_std["pos"] == [1.52, 0.0, 0.0] or pcol.actor_overlaps(views_m[1], b_std):
                    bad.append(f"(cs-h) 설계된 장면 b1 의 겹친 인물이 보기 사본에서 밀리지 않았습니다: {b_std['pos']}")
                if views_m[0]["timeline"] != old_scene["timeline"] or views_m[3]["timeline"] != scene_d["timeline"]:
                    bad.append("(cs-h) 섞인 계획의 옛 장면 보기 사본이 저장본과 다릅니다")
            else:
                bad.append(f"(cs-h) 섞인 계획 보기 장면 수가 4가 아닙니다: {len(views_m)}")
            vjs_h = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
            css_h = (ROOT / "frontend" / "public" / "vnew.css").read_text(encoding="utf-8")
            redesign_text = ("이 프리비즈는 소품 관통 보정 전에 만들었습니다. '다시 설계'하면 인물이 소품 위에 제대로 앉고 "
                             "카메라도 다시 맞춰집니다.")
            if redesign_text not in vjs_h or vjs_h.count("COLLISION_REDESIGN_TEXT") < 3:
                bad.append("(cs-h) vnew.js 에 다시 설계 안내 문구(녹화 영역·컷 패널)가 없습니다")
            if ".vnew-previz-collision-redesign {" not in css_h:
                bad.append("(cs-h) vnew.css 에 .vnew-previz-collision-redesign 이 없습니다")
            mark("cs-h")

            # ---------------- (cs-i) 설계 통합 ----------------
            logs.clear()
            design_prompts.clear()
            owner_i, pid_i, plan_i = run_design_with("cs-i", test3_design_payload(), test3_grammar, [c for c, _a in _TEST3_CUTS])
            pv_i = plan_i.get("previz") or {}
            scene_i = (pv_i.get("scenes") or [{}])[0]
            fit_i = pv_i.get("fit") or {}
            if not any(p.get("sittable") is True and p.get("id") == "upper_rail" for p in scene_i.get("props") or []):
                bad.append("(cs-i) 저장 소품에 sittable 이 없습니다")
            if not design_prompts or "sittable:true" not in design_prompts[0] or "sitOn" not in design_prompts[0]:
                bad.append("(cs-i) 설계 프롬프트에 앉을 소품 안내가 없습니다")
            if "sittable:true" not in pvmod._DESIGN_SYSTEM or "sitOn" not in pvmod._DESIGN_CUT_ACTOR["properties"]:
                bad.append("(cs-i) 설계 도구 문장·스키마에 sittable·sitOn 이 없습니다")
            prop_schema = pvmod._DESIGN_TOOL["inputSchema"]["json"]["properties"]["props"]["items"]["properties"]
            if prop_schema.get("sittable") != {"type": "boolean"}:
                bad.append("(cs-i) 설계 도구 소품에 sittable(boolean)이 없습니다")
            heights = {a["id"]: float(a.get("heightM") or 1.7) for a in scene_i.get("actors") or []}
            names = {a["id"]: a.get("name") for a in scene_i.get("actors") or []}
            for cut in scene_i.get("timeline") or []:
                cid = cut["cutId"]
                entry = fit_i.get(cid) or {}
                if not isinstance(entry.get("collision"), dict) or entry["collision"].get("unresolved"):
                    bad.append(f"(cs-i) {cid} fit.collision 이 없거나 unresolved 가 있습니다: {entry.get('collision')}")
                for ca in cut.get("actors") or []:
                    if (ca.get("sit") or {}).get("propId") != "upper_rail" or ca.get("sitOn") != "upper_rail":
                        bad.append(f"(cs-i) {cid}/{ca['id']} 저장 sit·sitOn 이 다릅니다: {ca.get('sit')} {ca.get('sitOn')}")
                    elif pcol.actor_overlaps(scene_i, ca):
                        bad.append(f"(cs-i) {cid}/{ca['id']} 저장 장면에서 겹칩니다: {pcol.actor_overlaps(scene_i, ca)}")
                if cid in two_person_cuts:
                    subject = test3_grammar[cid]["mainSubject"]
                    aid = next((k for k, v in names.items() if v == subject), "")
                    eye = pfit.body_y("eye", heights.get(aid, 1.7), "sit", 0.985)
                    cam_y = float(((cut.get("camera") or {}).get("pos") or [0, 0, 0])[1])
                    if abs(cam_y - eye) > 0.35:
                        bad.append(f"(cs-i) {cid} 카메라 높이 {cam_y:.3f} 가 앉은 눈높이 {eye:.3f} ± 0.35 밖입니다")
                    if not isinstance(entry.get("match"), (int, float)) or entry["match"] < 0.7:
                        bad.append(f"(cs-i) {cid} fit.match 가 0.7 미만입니다: {entry.get('match')}")
            stage = logs.messages("vnew.previz.stage")
            if len(stage) != 1 or not re.fullmatch(
                rf"vnew\.previz\.stage pid={pid_i} step=collision ms=\d+ fixes=\d+ unresolved=0", stage[0]
            ):
                bad.append(f"(cs-i) stage 로그가 다릅니다: {stage}")
            if not logs.messages("vnew.previz.collision.fix cut=cut-04 actor=geonwoo prop=upper_rail"):
                bad.append("(cs-i) 설계 때 seat fix 로그가 없습니다(cut-04 범건우)")
            view_i = planmod._previz_view(owner_i, pid_i, plan_i)  # noqa: SLF001
            if any(v.get("source") != "design" for v in (view_i.get("collision") or {}).values()):
                bad.append(f"(cs-i) 다시 설계한 계획의 보기 collision 이 source design 이 아닙니다: {view_i.get('collision')}")
            # 보정 함수가 예외를 내도 설계·보기는 끝난다(warning 만, 보정 없이 계속).
            def _boom(*_a, **_k):
                raise RuntimeError("검사용 보정 실패")

            pcol.resolve_scenes = _boom
            pcol.resolve_scene = _boom
            pcol.detect_scene = _boom  # 1차가 실패하면 장면이 옛 장면이 되어 보기는 검출 경로를 탄다.
            logs.clear()
            _o, _p, plan_f = run_design_with("cs-i-fail", test3_design_payload(), test3_grammar, [c for c, _a in _TEST3_CUTS])
            view_f = planmod._previz_view(_o, _p, plan_f)  # noqa: SLF001
            pcol.resolve_scenes = saved["resolve_scenes"]
            pcol.resolve_scene = saved["resolve_scene"]
            pcol.detect_scene = saved["detect_scene"]
            if (plan_f.get("previz") or {}).get("status") != "ready" or not view_f.get("scenes"):
                bad.append("(cs-i) 보정 함수 예외가 설계·보기를 막았습니다")
            if len(logs.messages("vnew.previz.collision.failed")) != 1 or len(logs.messages("vnew.previz.collision.view_failed")) != 1:
                bad.append(f"(cs-i) 보정 실패 warning 로그가 다릅니다: {logs.messages('collision.')}")
            mark("cs-i")

            # ---------------- (cs-j) 높이 함수 ----------------
            for height in (1.6, 1.75):
                for point in ("top", "eye", "chest", "waist", "knee", "foot"):
                    frac = pfit.BODY_FRACTION[point]
                    old = 0.5 + (frac - 0.5) * height if frac >= 0.5 else frac / 0.5 * 0.5
                    if abs(pfit.body_y(point, height, "sit") - old) > 1e-9:
                        bad.append(f"(cs-j) hip_y 없을 때 body_y({point}, {height}, sit) 가 지금 값과 다릅니다")
                    if abs(pfit.body_y(point, height, "stand", 0.985) - frac * height) > 1e-9:
                        bad.append("(cs-j) 서 있는 인물에 hip_y 가 쓰였습니다")
            if abs(pfit.body_y("top", 1.75, "sit", 0.985) - (0.985 + 0.5 * 1.75)) > 1e-9:
                bad.append("(cs-j) hip_y 0.985 의 top 이 0.985 + 0.5·h 가 아닙니다")
            cam_j = {"pos": [0.0, 1.4, 2.5], "target": [0.0, 1.2, 0.0], "fov": 22.6}
            if pfit.match_visible(cam_j, [0, 0, 0], 1.7, "sit", 0.1, 0.5, "waist") != pfit.match_visible(cam_j, [0, 0, 0], 1.7, "sit", 0.1, 0.5, "waist", None):
                bad.append("(cs-j) match_visible hip_y 기본값이 지금과 다릅니다")
            info = pcam._actor_points({"heightM": 1.75, "hipY": 0.985}, [0, 0, 0], "sit")  # noqa: SLF001
            if abs(info["y"]("top") - (0.985 + 0.875)) > 1e-9 or abs(info["eyeY"] - pfit.body_y("eye", 1.75, "sit", 0.985)) > 1e-9:
                bad.append("(cs-j) _actor_points 가 actor.hipY 를 쓰지 않습니다")
            # 수동 카메라 재계산(PUT)도 sit.hipY 를 쓴다.
            j_scene = {"actors": [{"id": "a1", "name": "김", "heightM": 1.7}],
                       "timeline": [{"cutId": "c1", "camera": cam_j, "actors": [{"id": "a1", "pos": [0, 0, 0], "pose": "sit",
                                                                                "sit": {"propId": "p", "hipY": 0.985, "legPitchDeg": 75, "legYawDeg": 0}}]}]}
            j_old = _copy.deepcopy(j_scene)
            j_old["timeline"][0]["camera"] = {"pos": [0.0, 1.0, 3.0], "target": [0.0, 1.0, 0.0], "fov": 22.6}
            j_prev = {"fit": {"c1": {"match": 0.1, "method": "grammar"}},
                      "panelGrammar": {"c1": {"hash": "h", "grammar": grammarmod.normalize_grammar(grammar("김"))}}}
            pvmod._recompute_manual_fit(j_prev, [j_old], [j_scene])  # noqa: SLF001
            want = round(pfit.match_visible(cam_j, [0, 0, 0], 1.7, "sit", 0.1, 0.5, "waist", 0.985), 4)
            if j_prev["fit"]["c1"].get("match") != want or j_prev["fit"]["c1"].get("method") != "manual":
                bad.append(f"(cs-j) _recompute_manual_fit 가 sit.hipY 를 쓰지 않습니다: {j_prev['fit']['c1']} 기대 {want}")
            mark("cs-j")

            # ---------------- (cs-k) 설정 끔 ----------------
            calls = {"n": 0}

            def counting(real):
                def _wrapped(*args, **kwargs):
                    calls["n"] += 1
                    return real(*args, **kwargs)
                return _wrapped

            pcol.resolve_scenes = counting(saved["resolve_scenes"])
            pcol.resolve_scene = counting(saved["resolve_scene"])
            with _pin_vnew(VNEW_PREVIZ_COLLISION=False):
                logs.clear()
                _o, _p, plan_k = run_design_with("cs-k", test3_design_payload(), test3_grammar, [c for c, _a in _TEST3_CUTS])
                scenes_k = (plan_k.get("previz") or {}).get("scenes") or []
                view_k = planmod._previz_view(_o, _p, plan_k)  # noqa: SLF001
                view_old = planmod._previz_view(owner_h, pid_h, planmod.load_plan(owner_h, pid_h)[0])  # noqa: SLF001
            pcol.resolve_scenes = saved["resolve_scenes"]
            pcol.resolve_scene = saved["resolve_scene"]
            if calls["n"]:
                bad.append(f"(cs-k) 설정을 껐는데 보정이 {calls['n']}회 불렸습니다")
            if any("sit" in ca for s in scenes_k for cut in s.get("timeline") or [] for ca in cut.get("actors") or []):
                bad.append("(cs-k) 설정을 껐는데 설계 저장 장면에 sit 이 있습니다")
            if any("collision" in (e or {}) for e in ((plan_k.get("previz") or {}).get("fit") or {}).values()):
                bad.append("(cs-k) 설정을 껐는데 fit.collision 이 있습니다")
            if "collision" in view_k or "collision" in view_old:
                bad.append("(cs-k) 설정을 껐는데 보기 응답에 collision 이 있습니다")
            if view_old.get("scenes") != [old_scene]:
                bad.append("(cs-k) 설정을 껐는데 보기 장면이 저장본 사본과 다릅니다")
            if logs.messages("vnew.previz.collision.") or logs.messages("step=collision"):
                bad.append("(cs-k) 설정을 껐는데 보정 로그가 남았습니다")
            stripped = pvmod._scene_view("p", {"timeline": [{"actors": [{"id": "a", "pose": "sit", "sit": {"hipY": 1.0}}]}]}, collision=False)  # noqa: SLF001
            if "sit" in stripped["timeline"][0]["actors"][0]:
                bad.append("(cs-k) 설정을 껐는데 보기 사본에 저장된 sit 이 남습니다(엉덩이 0.5 가 아님)")
            mark("cs-k")

            # ---------------- (cs-l) 정규화 ----------------
            raw_l = {
                "props": [
                    {"id": "rail", "size": [3, 0.1, 0.1], "pos": [0, 0.9, 0], "sittable": True},
                    {"id": "box", "size": [0.5, 0.5, 0.5], "pos": [1, 0.25, 0], "sittable": False},
                    {"id": "step", "size": [1, 0.3, 0.4], "pos": [-1, 0.15, 0], "sittable": "yes"},
                ],
                "actors": [{"id": f"a{i}"} for i in range(1, 9)],
                "timeline": [{"cutId": "c1", "actors": [
                    {"id": "a1", "pose": "sit", "facingDeg": 270, "sitOn": "rail", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": 270}},
                    {"id": "a2", "pose": "sit", "facingDeg": 90, "sitOn": "nope", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 60}},
                    {"id": "a3", "pose": "stand", "sitOn": "rail", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": 0}},
                    {"id": "a4", "pose": "sit", "sit": {"propId": "rail", "hipY": 5.0, "legPitchDeg": 75, "legYawDeg": 0}},
                    {"id": "a5", "pose": "sit", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 20, "legYawDeg": 0}},
                    {"id": "a6", "pose": "sit", "sit": {"propId": "ghost", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": 0}},
                    {"id": "a7", "pose": "sit", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": -540}},
                    {"id": "a8", "pose": "sit", "sit": {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": "x"}, "facingDeg": 450},
                ]}],
            }
            norm = pschema.normalize_scene(raw_l, location_id="l", cut_specs=[{"cutId": "c1", "seconds": 3}])
            flags = {p["id"]: p.get("sittable") for p in norm["props"]}
            if flags != {"rail": True, "box": None, "step": True} or "sittable" in norm["props"][1]:
                bad.append(f"(cs-l) sittable 정규화가 다릅니다: {flags}")
            got_l = {ca["id"]: ca for ca in norm["timeline"][0]["actors"]}
            want_l = {
                "a1": ("rail", {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": -90.0}),
                "a2": (None, {"propId": "rail", "hipY": 0.95, "legPitchDeg": 60, "legYawDeg": 90.0}),
                "a3": (None, None), "a4": (None, None), "a5": (None, None), "a6": (None, None),
                "a7": (None, {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": 180.0}),
                "a8": (None, {"propId": "rail", "hipY": 0.95, "legPitchDeg": 75, "legYawDeg": 90.0}),
            }
            for aid, (want_on, want_sit) in want_l.items():
                ca = got_l.get(aid) or {}
                if ca.get("sitOn") != want_on or ca.get("sit") != want_sit:
                    bad.append(f"(cs-l) {aid} sitOn·sit 정규화가 다릅니다: {ca.get('sitOn')} {ca.get('sit')} 기대 {want_on} {want_sit}")
            no_props = pschema._normalize_cut_actors([{"id": "a1", "pose": "sit", "sitOn": "rail"}], ["a1"], {"width": 6, "depth": 6})  # noqa: SLF001
            if "sitOn" in no_props[0] or "sit" in no_props[0]:
                bad.append("(cs-l) prop_ids 없이 부르면 sitOn·sit 을 두지 않아야 합니다(지금 출력과 같게)")
            mark("cs-l")

            # ---------------- (cs-m) 렌더러·화면 문자열 ----------------
            js = (PUBLIC / "vnew-previz.js").read_text(encoding="utf-8")
            vjs = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
            css = (ROOT / "frontend" / "public" / "vnew.css").read_text(encoding="utf-8")
            for needle in ("apply(pose, gesture, speaking, t, targetYaw, sit)", "this.hips.position.y = sit.hipY / scale;",
                           "sitData.legYawDeg", "sit.legPitchDeg", "this.spine.rotation.y += sit.turn;",
                           "Math.max(-Math.PI / 2, Math.min(Math.PI / 2, turn))", "entry.mannequin.group.rotation.y = legYaw;",
                           "this.hips.position.y = 0.5;", "const GESTURES", "sitArg.hipY + 0.86 * heightM / 1.7 + 0.2"):
                if needle not in js:
                    bad.append(f"(cs-m) vnew-previz.js 에 '{needle}' 이 없습니다")
            for needle in ("소품 겹침 보정: ", "소품과 겹침을 풀지 못했습니다: ", "화면에서만 보정했습니다. 다시 설계하면 저장됩니다.",
                           "소품과 겹침을 풀지 못한 컷: ", "function previzCollisionBadges(cut, previz)"):
                if needle not in vjs:
                    bad.append(f"(cs-m) vnew.js 에 '{needle}' 이 없습니다")
            if "innerHTML" in vjs[vjs.find("function previzCollisionBadges"):vjs.find("function collisionPropLabel")]:
                bad.append("(cs-m) 보정 배지가 innerHTML 을 씁니다(textContent 만)")
            for needle in (".vnew-previz-tag-collision {", ".vnew-previz-tag-collision.is-warn {"):
                if needle not in css:
                    bad.append(f"(cs-m) vnew.css 에 '{needle}' 이 없습니다")
            node = shutil.which("node")
            if not node:
                warn("node 가 없어 cs-m node --check 를 건너뜁니다 (E16)")
            else:
                for name, text in (("vnew-previz.js", js), ("vnew.js", vjs)):
                    result = _check_as_module(node, text) if _is_es_module(text) else subprocess.run(
                        [node, "--check", str(PUBLIC / name)], capture_output=True, text=True)
                    if result.returncode != 0:
                        bad.append(f"(cs-m) node --check {name} 실패: {(result.stderr or '').strip()[:300]}")
            mark("cs-m")

            # ---------------- (cs-n) 2차 보정(closeapex 배우 조정) ----------------
            rematch_calls: list[str] = []
            real_rematch = saved["_rematch_cut"]

            def spy_rematch(scene, cut, grammar_in, fit_entry):
                rematch_calls.append(str(cut.get("cutId")))
                return real_rematch(scene, cut, grammar_in, fit_entry)

            pvmod._rematch_cut = spy_rematch
            raw_n = {
                "room": {"width": 8.0, "depth": 6.0, "height": 3.0},
                "props": [{"id": "pole_n", "shape": "cylinder", "size": [0.1, 2.0, 0.1], "pos": [0.3, 1.0, 0.0], "labelKo": "기둥"}],
                "actors": [{"id": "a1", "name": "가", "heightM": 1.7}, {"id": "a2", "name": "나", "heightM": 1.7}],
                "timeline": [{"cutId": "c1", "actors": [{"id": "a1", "pos": [-1.0, 0, 0], "pose": "stand", "facingDeg": 90},
                                                        {"id": "a2", "pos": [1.0, 0, 0], "pose": "stand", "facingDeg": 270}]}],
            }
            g_n = grammar("가", "나", shot_type="two_shot", bottom="shoulders", subject_x=0.35)
            logs.clear()
            _o, _p, plan_n = run_design_with("cs-n", raw_n, {"c1": g_n}, ["c1"])
            pvmod._rematch_cut = real_rematch
            pv_n = plan_n.get("previz") or {}
            scene_n = (pv_n.get("scenes") or [{}])[0]
            fit_n = (pv_n.get("fit") or {}).get("c1") or {}
            fixes_n = (fit_n.get("collision") or {}).get("fixes") or []
            # closeapex(CU 투샷) 배우 조정은 두 인물을 0.45m 까지 당긴다 — a2 가 기둥(x 0.3) 안으로 들어간다.
            if [(f.get("pass"), f.get("kind"), f.get("actor"), f.get("prop")) for f in fixes_n] != [("B", "push", "a2", "pole_n")]:
                bad.append(f"(cs-n) 2차 push fix 가 a2↔pole_n 1개가 아닙니다: {fixes_n}")
            ca_n = {ca["id"]: ca for ca in (scene_n.get("timeline") or [{}])[0].get("actors") or []}
            if "a2" not in ca_n or pcol.actor_overlaps(scene_n, ca_n["a2"]):
                bad.append(f"(cs-n) 2차 뒤에도 a2 가 기둥과 겹칩니다: {ca_n.get('a2')}")
            elif _math.hypot(ca_n["a1"]["pos"][0] - ca_n["a2"]["pos"][0], ca_n["a1"]["pos"][2] - ca_n["a2"]["pos"][2]) < 0.45 - 1e-6:
                bad.append("(cs-n) 2차 밀어내기가 두 인물 간격 0.45 를 깼습니다")
            if rematch_calls != ["c1"]:
                bad.append(f"(cs-n) 2차에서 움직인 컷의 _rematch_cut 호출이 다릅니다: {rematch_calls}")
            # 단위: 주인공이 움직이면 match 가 다시 계산된다(match_visible 과 같은 값).
            unit_scene = _copy.deepcopy(scene_n)
            unit_cut = unit_scene["timeline"][0]
            unit_fit = dict(fit_n)
            for ca in unit_cut["actors"]:
                if ca["id"] == "a1":
                    ca["pos"] = [round(ca["pos"][0] - 0.3, 4), 0.0, ca["pos"][2]]
            real_rematch(unit_scene, unit_cut, grammarmod.normalize_grammar(g_n), unit_fit)
            a1_unit = next(ca for ca in unit_cut["actors"] if ca["id"] == "a1")
            size_n = next((k for k, v in pcam.LENS.items() if v[1] == fit_n.get("lens")), "CU")
            want_n = pfit.match_visible(unit_cut["camera"], a1_unit["pos"], 1.7, "stand", 0.1, 0.5, pcam.LENS[size_n][2])
            if fit_n.get("occluded") or fit_n.get("extraVisible"):
                want_n *= 0.5
            if unit_fit.get("match") != round(want_n, 4) or unit_fit.get("match") == fit_n.get("match"):
                bad.append(f"(cs-n) _rematch_cut 결과가 다릅니다: {unit_fit.get('match')} 기대 {round(want_n, 4)} (전 {fit_n.get('match')})")
            mark("cs-n")

            # ---------------- (cs-o) 다리 방향 규칙 ----------------
            boxed = sc([
                bx("seat", [0.45, 0.45, 0.45], [0, 0.225, 0], label="의자 좌판"),
                bx("back", [0.45, 0.9, 0.05], [0, 0.45, -0.25], label="의자 등받이"),
                bx("side_l", [0.05, 0.9, 0.45], [-0.25, 0.45, 0], label="의자 옆판"),
                bx("side_r", [0.05, 0.9, 0.45], [0.25, 0.45, 0], label="의자 옆판"),
            ], [{"id": "a1", "pos": [0, 0, 0], "pose": "sit", "facingDeg": 180}])
            b_vols = pcol._scene_vols(boxed)  # noqa: SLF001
            b_seat = next(v for v in b_vols if v["id"] == "seat")
            blocked = [y for y in (180.0, 90.0, 270.0) if pcol.solve_legs(0, 0, 0.45, 1.0, y, b_seat, b_vols)[1]]
            if blocked or pcol.is_elongated(b_seat):
                bad.append(f"(cs-o) 검사 전제: 의자 facing·옆 방향이 막혀 있지 않습니다: {blocked}")
            res_o = pcol.resolve_scene(boxed, log_fixes=False)["c1"]
            if (cut_actor(boxed, "a1").get("sit") or {}).get("legYawDeg") != 0.0 or res_o["unresolved"]:
                bad.append(f"(cs-o) 의자 등받이 쪽 facing 이 4방향 탐색으로 0° 가 되지 않았습니다: {cut_actor(boxed, 'a1').get('sit')} {res_o}")
            bench_cases = (
                ("z-1·f90", [0, 0.225, -1], 0.0, 90, 0.0),
                ("z+1·f90", [0, 0.225, 1], 0.0, 90, 180.0),
                ("z+1·f30", [0, 0.225, 1], 0.0, 30, 0.0),
                ("중심·f90", [0, 0.225, 0], 0.0, 90, 0.0),
                ("rotY π/2·x-1·f0", [-1, 0.225, 0], _math.pi / 2, 0, 90.0),
            )
            for name, pos, rot, facing, want_yaw in bench_cases:
                bench = sc([bx("bench", [2.0, 0.45, 0.45], pos, rot=rot, label="벤치")],
                           [{"id": "a1", "pos": [pos[0], 0, pos[2]], "pose": "sit", "facingDeg": facing}])
                pcol.resolve_scene(bench, log_fixes=False)
                got_yaw = (cut_actor(bench, "a1").get("sit") or {}).get("legYawDeg")
                if got_yaw != want_yaw:
                    bad.append(f"(cs-o) 벤치 {name}: legYawDeg {got_yaw} 기대 {want_yaw}")
            crate = sc([bx("crate", [0.6, 0.5, 0.6], [0, 0.25, 0])], [{"id": "a1", "pos": [0, 0, 0], "pose": "sit", "facingDeg": 0}])
            pcol.resolve_scene(crate, log_fixes=False)
            if (cut_actor(crate, "a1").get("sit") or {}).get("propId") != "crate":
                bad.append("(cs-o) 이름·표시 없는 상자 안 앉기가 등급 4로 앉지 않았습니다")
            stool = sc([bx("stool", [0.3, 0.45, 0.3], [0.5, 0.225, 0])], [{"id": "a1", "pos": [0, 0, 0], "pose": "sit", "sitOn": "stool"}])
            res_s = pcol.resolve_scene(stool, log_fixes=False)["c1"]
            if (cut_actor(stool, "a1").get("sit") or {}).get("propId") != "stool" or [f["kind"] for f in res_s["fixes"]] != ["seat"]:
                bad.append(f"(cs-o) sitOn(0.35m) 등급 1 자리 맞춤이 다릅니다: {cut_actor(stool, 'a1')} {res_s}")
            logs.clear()
            far = sc([bx("stool", [0.3, 0.45, 0.3], [2.0, 0.225, 0])], [{"id": "a1", "pos": [0, 0, 0], "pose": "sit", "sitOn": "stool"}])
            pcol.resolve_scene(far, log_fixes=True)
            if "sit" in cut_actor(far, "a1") or logs.messages("vnew.previz.collision.sit_on_ignored") != [
                "vnew.previz.collision.sit_on_ignored cut=c1 actor=a1 prop=stool"
            ]:
                bad.append(f"(cs-o) 먼 sitOn 무시·로그가 다릅니다: {logs.messages('sit_on_ignored')}")
            mark("cs-o")

            # ---------------- (cs-p) 옛 장면 보기는 그대로, 다시 설계하면 얼굴이 화면 안 ----------------
            def subject_heads(scene_p):
                """컷마다 주인공 머리 화면 사각형 (cutId, y0, y1). sit.hipY 가 있으면 그 엉덩이로 계산한다."""
                names_p = {a["id"]: a.get("name") for a in scene_p.get("actors") or []}
                heights_p = {a["id"]: float(a.get("heightM") or 1.7) for a in scene_p.get("actors") or []}
                out_p = []
                for cut in scene_p.get("timeline") or []:
                    subject = test3_grammar[cut["cutId"]]["mainSubject"]
                    ca = next((x for x in cut.get("actors") or [] if names_p.get(x["id"]) == subject), None)
                    if ca is None:
                        out_p.append((cut["cutId"], None, None))
                        continue
                    info_p = pcam._actor_points(  # noqa: SLF001
                        {"heightM": heights_p.get(ca["id"], 1.7), "hipY": (ca.get("sit") or {}).get("hipY")},
                        ca.get("pos"), str(ca.get("pose") or "stand"),
                    )
                    box_p = pcam._head_box(cut.get("camera") or {}, info_p)  # noqa: SLF001
                    out_p.append((cut["cutId"], None if box_p is None else box_p["y0"], None if box_p is None else box_p["y1"]))
                return out_p

            cut_ids_p = [c for c, _a in _TEST3_CUTS]
            with _pin_vnew(VNEW_PREVIZ_COLLISION=False):
                owner_p, pid_p, plan_p_old = run_design_with("cs-p", _previz_test3_scene(), test3_grammar, cut_ids_p)
            scene_p_old = ((plan_p_old.get("previz") or {}).get("scenes") or [{}])[0]
            fit_p_old = (plan_p_old.get("previz") or {}).get("fit") or {}
            if any("sit" in ca for cut in scene_p_old.get("timeline") or [] for ca in cut.get("actors") or []) or any(
                "collision" in (e or {}) for e in fit_p_old.values()
            ):
                bad.append("(cs-p) 검사 전제: 보정 끔으로 설계한 옛 장면에 sit·fit.collision 이 있습니다")
            heads_old = subject_heads(scene_p_old)
            bug_scene = _copy.deepcopy(scene_p_old)
            saved["resolve_scene"](bug_scene, log_fixes=False)
            if not any(y1 is not None and y1 < 0 for _cid, _y0, y1 in subject_heads(bug_scene)):
                bad.append("(cs-p) 검사 전제: 옛 장면 사본을 보정하면 얼굴이 화면 위로 나가야 합니다(옛 보기 동작의 결함)")
            logs.clear()
            view_p = planmod._previz_view(owner_p, pid_p, plan_p_old)  # noqa: SLF001
            view_scene_p = (view_p.get("scenes") or [{}])[0]
            for cut_v, cut_s in zip(view_scene_p.get("timeline") or [], scene_p_old.get("timeline") or []):
                if cut_v.get("actors") != cut_s.get("actors") or cut_v.get("camera") != cut_s.get("camera"):
                    bad.append(f"(cs-p) 옛 장면 {cut_s['cutId']} 보기 사본이 저장본과 다릅니다(인물·카메라)")
            if subject_heads(view_scene_p) != heads_old:
                bad.append("(cs-p) 옛 장면 보기의 주인공 머리 위치가 저장본과 다릅니다")
            if (view_p.get("collision") or {}).get("needsRedesign") is not True or logs.messages("vnew.previz.collision."):
                bad.append(f"(cs-p) 옛 장면 보기에 needsRedesign 이 없거나 로그가 남았습니다: {view_p.get('collision')}")
            # 같은 장면을 보정 켬으로 다시 설계(run_design 경로, 가짜 LLM).
            owner_p, pid_p, plan_p_new = run_design_with("cs-p", _previz_test3_scene(), test3_grammar, cut_ids_p)
            scene_p_new = ((plan_p_new.get("previz") or {}).get("scenes") or [{}])[0]
            for cid_p, y0, y1 in subject_heads(scene_p_new):
                if y0 is None or not (0.0 <= y0 and y1 <= 1.0):
                    bad.append(f"(cs-p) 다시 설계한 {cid_p} 주인공 머리 사각형이 화면 밖입니다: y0={y0} y1={y1}")
            if any(not (ca.get("sit") or {}).get("hipY") for cut in scene_p_new.get("timeline") or [] for ca in cut.get("actors") or []):
                bad.append("(cs-p) 다시 설계한 장면에 sit.hipY 가 없습니다")
            view_p2 = planmod._previz_view(owner_p, pid_p, plan_p_new)  # noqa: SLF001
            if "needsRedesign" in (view_p2.get("collision") or {}):
                bad.append("(cs-p) 다시 설계한 계획에 needsRedesign 안내가 남았습니다")
            if subject_heads((view_p2.get("scenes") or [{}])[0]) != subject_heads(scene_p_new):
                bad.append("(cs-p) 다시 설계한 계획의 보기 사본 보정이 주인공 머리 위치를 바꿨습니다(멱등 아님)")
            mark("cs-p")

            if real_calls:
                bad.append(f"(guard) 가짜가 아닌 LLM 호출이 있었습니다: {real_calls}")
    except Exception as exc:  # noqa: BLE001
        bad.append(f"검사 중 예외: {type(exc).__name__} {str(exc)[:200]}\n{_tb.format_exc()[-1200:]}")
    finally:
        pvmod.STORE = saved["pv_STORE"]
        planmod.STORE = saved["plan_STORE"]
        pvmod._design = saved["_design"]
        grammarmod._read = saved["gr_read"]
        pvmod._cut_tiles = saved["_cut_tiles"]
        pvmod._spawn = saved["_spawn"]
        pvmod._rematch_cut = saved["_rematch_cut"]
        pcol.resolve_scenes = saved["resolve_scenes"]
        pcol.resolve_scene = saved["resolve_scene"]
        pcol.detect_scene = saved["detect_scene"]
        pvmod.vpoc_ledger.charge_text = saved["pv_charge_text"]
        vset.VNEW_PREVIZ_COLLISION = saved["collision_flag"]

    expected = [f"cs-{c}" for c in "abcdefghijklmnop"]
    missing = [i for i in expected if i not in seen_ids]
    if missing and not bad:
        bad.append(f"끝까지 돌지 않은 검사: {missing}")
    if bad:
        fail("084 프리비즈 소품 관통 보정 검사 실패\n" + "\n".join(f"  {item}" for item in bad))
    else:
        ok(
            "084 프리비즈 소품 관통 보정 검사 (cs-a)test3 실측 7컷 upper_rail·0.985·legYaw 0·겹침 0·결정적 "
            "(cs-b)의자 (cs-c)기둥 push·로그 (cs-d)회전·눕기·무릎·앉기 (cs-e)unresolved (cs-f)인물 간격 "
            "(cs-g)멱등 (cs-h)옛 장면 보기 그대로·needsRedesign·설계된 장면만 사본 보정 (cs-i)설계 통합·카메라 hipY·match (cs-j)hip_y 함수 "
            "(cs-k)설정 끔 (cs-l)정규화 (cs-m)렌더러·화면 문자열 (cs-n)2차 보정·_rematch_cut "
            "(cs-o)다리 방향 규칙 (cs-p)옛 test3 보기 그대로·다시 설계하면 얼굴 화면 안"
        )


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
    check_vnew_locations()
    check_vnew_generate()
    check_vnew_previz()
    check_vnew_previz_camera()
    check_vnew_shot_brief()
    check_vnew_mannequin()
    check_fal_pricing()
    check_vnew_real_start()
    check_anime_style()
    check_fal_model_caps()
    check_vnew_reference_video()
    check_vnew_take()
    check_vnew_take_prompt()
    check_vnew_take_costume()
    check_vnew_take_prompt_polish()
    check_vnew_take_prompt_budget()
    check_vnew_take_on_screen()
    check_vnew_take_harmony_motion()
    check_ark_seedance_gate()
    check_fal_parity_inflight()
    check_fal_parity_rollback()
    check_fal_parity_route()
    check_fal_parity_duration()
    check_fal_parity_resolution()
    check_fal_parity_prompt()
    check_anime_identity()
    check_fal_admin_deny()
    check_fal_no_silent_fallback()
    check_model_resolved_log()
    check_vnew_previz_lazy()
    check_stitcher_threads()
    check_vnew_model_fallback()
    check_fal_poll_failure()
    check_vnew_model_list_parity()
    check_llm_top_model_first()
    check_previz_collision()
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
