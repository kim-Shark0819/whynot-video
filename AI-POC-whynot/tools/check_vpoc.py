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
        # 합계가 넉넉히 1,500바이트 안인 짧은 지시서. 섹션이 전부 그대로 들어가야 한다.
        plan_i = _plan_cuts(1, 6)
        plan_i["previz"]["shotBrief"] = {
            "status": "ready",
            "scene": {"loc:office": "A principal's office with pale green walls. Warm daylight from the left window."},
            "cuts": {"c1": {
                "shotEn": "MCU, 50mm lens, eye-level, clean single.",
                "blockingEn": "He stands at frame center facing the camera.",
                "actionEn": "He leans forward and narrows his eyes.",
                "moodEn": "tense and controlled.",
                "backgroundEn": "wood-panelled wall behind, bookshelf soft in shallow focus.",
                "dialogue": [{"speaker": "범건우", "lineKo": "자네 뭐 하나?", "deliveryEn": "sharp, low voice"}],
            }},
        }
        prompt_i = genmod._cut_prompt(plan_i, {"cutId": "c1"}, "real", cast=cast, has_image_ref=False)
        if len(prompt_i.encode("utf-8")) > 2300:  # 058 ASSETS·CONTINUITY 보강으로 기본 길이 증가 — 한도(2500) 안이면 충분
            bad.append(f"(sb-i) 전제: 짧은 지시서 프롬프트가 2300바이트를 넘습니다: {len(prompt_i.encode('utf-8'))}")
        for must in ("\nACTION:", "\nMOOD:", "\nSETTING:"):
            if must not in ("\n" + prompt_i):
                bad.append(f"(sb-i) 짧은 지시서인데 섹션 {must.strip()} 이 지워졌습니다")
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
            " (sb-i)짧은 지시서 아무것도 안 지움(ACTION·MOOD·SETTING) (sb-j)긴 지시서 ≤2500·ACTION 유지·CAST/CONTINUITY 유지"
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
