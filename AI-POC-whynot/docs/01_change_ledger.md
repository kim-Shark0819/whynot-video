# 01 · 변경 원장 (Change Ledger)

| 항목 | 내용 |
|---|---|
| 근거 | `이매지너스/이매지너스_history.md` (28,912 B / 2026-09-07 14:05 수정 / **루트에는 없음 — D-03 확정**) |
| 보조 근거 | `이매지너스/terraform_infra/` 파일 목록, `AI-POC/README.md`, 파일 해시 비교 |
| 작성 | Phase 0.6 |
| 분류 규칙 | 기능·파일·설정 추가 = **ADD** / 버그·설정 오류 수정 = **FIX** / 기능·파일·설정 제거 = **REMOVE** / 판단 불가 = **UNKNOWN** |
| 액션 규칙 | **APPLY**(병합) / **SKIP**(반영 안 함) / **NEEDS_DECISION**(사용자 결정까지 보류) / **REIMPLEMENT**(복사 금지, Phase 3에서 모듈 기능으로 재구현) |

Overlay 계정: `726990466389` / 접두사 `story-ai` / 리전 `ap-northeast-2`.

---

## 0. 파일 분류 실측 (원장의 영향 경로 근거)

해시 비교 기준. 제외: `terraform_infra/**`·`_inventory/**`(참조 전용), `__pycache__`·`.terraform`·`.build`·`node_modules`·`*.tfstate*`·`*.pyc`, `이매지너스_history.md`, `이매지너스.json`.

| 분류 | 건수 |
|---|---|
| NEW (Overlay에만) | **42** |
| MODIFIED (양쪽, 내용 다름) | **20** |
| IDENTICAL | 58 |
| BASE_ONLY (Base에만) | **0** |

> **정정 (2026-09-08 재집계)**: 최초 집계는 `Get-ChildItem`에 `-Force`를 빼서 OneDrive 동기화 폴더의 파일 **13개를 누락**했다(NEW 29 → 42, MODIFIED 19 → 20). 파일 집합을 다룰 때는 항상 `-Force`를 붙인다.
>
> **누락됐던 NEW 13개**
>
> | 파일 | 크기 | 성격 |
> |---|---|---|
> | `app/src/http_guards.py` | 15.3KB | 인증·한도 가드 — `server.py`에서 **추출**된 모듈 (§1 L-62) |
> | `app/src/length_budget.py` | 17.4KB | 분량 예산 |
> | `app/src/story_artifacts.py` | 34.2KB | 스토리 산출물 |
> | `app/src/story_dev_models.py` | 17.0KB | 스토리 개발 Beta 모델 |
> | `app/src/story_dev_router.py` | 16.4KB | 스토리 개발 Beta 라우터 |
> | `app/src/story_stale.py` | 9.4KB | 오래된 작업 처리 |
> | `tools/check_constants.py` | — | 같은 값을 두 곳에 적은 상수의 일치 검사 |
> | `tools/check_imports.py` | — | 문법·미사용 import·미정의 전역 참조 검사 |
> | `tools/check_length_budget.py` | — | 분량 예산이 수용 기준을 만족하는지 확인 |
> | `.kiro/specs/story-dev-beta/requirements.md` | 30.5KB | **Overlay 자체 Kiro spec** |
> | `.kiro/specs/story-dev-beta/design.md` | 64.4KB | **Overlay 자체 Kiro spec** |
> | `.kiro/specs/story-dev-beta/tasks.md` | 28.6KB | **Overlay 자체 Kiro spec** |
> | `스토리개발_Beta_계획.md` | 64.4KB | 위 spec의 원본 계획서 |
>
> 결과적으로 `app/src`의 NEW는 11개가 아니라 **17개**다. 스토리 기능이 history §12의 서술보다 크다.

> **핵심**: Overlay가 삭제한 Base 파일은 **한 건도 없다.** H2의 "삭제 반영 금지"는 파일 단위가 아니라 **hunk 단위**에서만 쟁점이 된다.
>
> **GPU 코드 경로는 양쪽이 IDENTICAL이다** — `wan_client.py`, `video_prompt.py`, `stitcher.py`, `toon_convert.py`, `styles.py`, `providers.py`, `prompt_boost.py`, `documents.py`, `account_guard.py`. Overlay는 GPU 코드를 지우지 않았고 `app.js`의 feature flag로 화면만 숨겼다.

**MODIFIED 20건**: `.gitignore`, `app/requirements.txt`, `app/src/{gateway,graph_store,image_brief,model_registry,prompt_layers,server}.py`, `deploy/config.env`, `deploy/start-all.ps1`, `deploy/verify/{common,verify-image-brief,verify-models-usage}.ps1`, `docs/구조분석.md`, `frontend/nginx.conf`, `frontend/public/{app.js,index.html,styles.css}`, `k8s/ingress.yaml.tmpl`, `README.md`

**NEW 42건**

| 묶음 | 파일 |
|---|---|
| `app/src` Python 11 | `http_guards.py` `length_budget.py` `pdf_export.py` `prompts_story.py` `story_artifacts.py` `story_context.py` `story_dev_models.py` `story_dev_router.py` `story_models.py` `story_router.py` `story_stale.py` |
| `app/src/roles` 6 | `__init__.py` `_json.py` `bible.py` `validator.py` `vision.py` `writer.py` |
| 검사·생성 도구 4 | `tools/check_constants.py` `tools/check_imports.py` `tools/check_length_budget.py` `tools/render_butterfly_effect.py` |
| Overlay 자체 spec 4 | `.kiro/specs/story-dev-beta/{requirements,design,tasks}.md` · `스토리개발_Beta_계획.md` |
| 고객사 자산 9 | `frontend/public/assets/brand/imaginus-logo.svg` · `assets/login/{butterfly-effect.mp4,butterfly-effect-poster.jpg}` · `assets/posters/poster-{busemi,first-love,palace,polaris,queen,tangeum}.webp` |
| 기타 2 | `deploy/config.env.bak-preterraform` · `frontend/.dockerignore` |
| 루트 자산 6 | `나비 영상.mp4` · `콘티_러프_1 (1).pdf` · `포스터들/{씽,아생,이통되,태상}.webp` |

---

## 1. 앱 · 프론트엔드 (Phase 1 병합 대상)

| ID | 항목 요약 | 분류 | 영향 경로 | 액션 | 근거 |
|---|---|---|---|---|---|
| L-01 | **스토리 기반 결과물 생성 개편.** 진입점 4종(웹툰 원작/소재·로그라인/트리트먼트/논의된 대본) 구조를 버리고 **바이블(작품 설정)을 중간 표현**으로 두어 로그라인·콘티·대본 3종을 파생. 진입점×산출물 조합마다 프롬프트가 따로 있어 관리 불가였던 문제를 해결 | ADD | NEW: `story_models.py` `prompts_story.py` `story_context.py` `story_router.py` `roles/{vision,bible,writer,validator,_json,__init__}.py` / MOD: `server.py` `graph_store.py` `prompt_layers.py` | **APPLY** | §12 |
| L-02 | **역할 분배는 코드가 한다.** 분배자 `story_router.py`는 LLM 없음. 역할끼리 대화 히스토리를 공유하지 않고 JSON 컨텍스트 팩만 S3로 주고받음 | ADD | `story_router.py`, `story_context.py` | **APPLY** | §12 |
| L-03 | **비전 분석 캐시.** `s3://<bucket>/_cache/story-analysis/{sha256+model}.json`. 같은 이미지 재첨부 시 실호출 0 (`trace.cache_hit`). 키에 사용자 정보를 넣지 않아 사용자 간 공유 | ADD | `roles/vision.py`, `story_context.py`, **IAM 정책에 `_cache/*` 필요** | **APPLY** (IAM 부분은 L-24) | §12 |
| L-04 | **캐시가 조용히 죽던 버그 수정.** IRSA 정책이 `users/*`·`_config/*`만 허용해 `_cache/` 읽기가 `AccessDenied`였고 저장소 코드가 이를 "캐시 없음"으로 처리(로그도 없음) → 비용 2배. 정책에 `_cache/*` 추가 + `AccessDenied`를 오류 로그로 노출 | FIX | `roles/vision.py` 또는 캐시 저장소 코드 + IAM | **APPLY** | §12 |
| L-05 | **바이블 재생성 억제.** `source_hash`(원문+타입+분석 해시)가 바뀔 때만 재생성 | ADD | `roles/bible.py`, `story_models.py` | **APPLY** | §12 |
| L-06 | **2단 검수.** 1차 코드(무료): 스키마·컷 수·회차 번호·엔딩 훅 존재·바이블 외 인물명·image_prompt 한국어. 2차 Haiku: 설정 불일치·고유명사 변형·분량. 실패 시 `notes` 붙여 **1회만** 재생성, 그래도 실패면 `needs_review` | ADD | `roles/validator.py` | **APPLY** | §12 |
| L-07 | **검수 거짓 양성 2건 수정.** (a) 요청에 목표 분량이 없으면 분량 검사 제외(`_requested_conditions()`) (b) 엔딩 훅은 코드가 존재 여부로만 판정하고 2차의 `ending_hook`·`schema` 지적은 버림(`validator._MODEL_BLOCKED_KINDS`) | FIX | `roles/validator.py` | **APPLY** | §12 |
| L-08 | **역할→모델 매핑.** 비전·바이블·로그라인·콘티 = `global.anthropic.claude-sonnet-4-6` / 대본 고품질 = `claude-opus-4-6-v1` / 검수 = `claude-haiku-4-5-20251001-v1:0`. `purpose`는 `story.vision` `story.bible` `story.writer.*` `story.validator` | ADD | `prompt_layers.py`, `model_registry.py`, `gateway.py` | **NEEDS_DECISION** | §12 · L-31 참조 |
| L-09 | **출력 토큰 상한 실측 반영.** sonnet-4-6 65536 통과 / 131072 거부, haiku-4-5 32768 통과 / 65536 거부. 회차 분량 상한을 이 값에 맞춤 | FIX | `prompts_story.py`, `story_models.py` | **APPLY** | §12 |
| L-10 | **데이터 모델 확장.** `project`에 `summaryStory`(≤300자 인라인) `storyTextKey` `storyTextCharCount`(본문은 S3, 키만 저장) `storyType` `storyAssetsJson` 5필드. 신규 정점 3종 `story_bible` `story_output` `script_job` | ADD | `graph_store.py`, `story_models.py` | **APPLY** | §12 |
| L-11 | **마이그레이션 없이 하위 호환.** openCypher가 없는 속성에 null을 주고 `_project_view`가 기본값으로 메움. `storyType`만 기본값 없음(null = 타입 미선택 → 화면이 선택 요구) | ADD | `graph_store.py` | **APPLY** | §12 |
| L-12 | **콘티 → 이미지 연결.** 만든 컷 배열을 기존 `storyboard` 정점에 `SB_MODE_MANUAL`로 전달. `auto`로 넘기면 `run_storyboard`가 컷을 다시 나눠 버림. 요약 스토리를 `prompt`로 함께 전달 | ADD | `server.py`, `graph_store.py` | **APPLY** | §12 |
| L-13 | **한도 차감은 기존 방식 유지.** 결과물 1건 = 1차감, 실패 시도는 차감 안 함. 지시서의 "gateway 안에서만"을 따르지 않음 — gateway는 모델 호출 단위라 콘티 한 장(내부 10회+)이 10건으로 세어짐. `graph_store.spend_credit` 주석 근거 | FIX | `graph_store.py` | **APPLY** | §12 |
| L-14 | **신규 엔드포인트 18개.** `/api/projects/{id}/story` `story-text` `summary` `assets/analyze` `assets/{key}` `bible` `outputs/logline` `outputs/storyboard` `outputs/script` `outputs/script/options` `outputs`, `/api/outputs/{id}`, `/api/projects/{id}/script-jobs`, `/api/script-jobs/{id}` `approve` `episodes/{ep}` `episodes/{ep}/regenerate`, `/api/admin/story/models` | ADD | `server.py` | **APPLY** | §12 |
| L-15 | **프로젝트 상세 화면 개편.** 스토리 구획 + 결과물 만들기 구획 추가. 요약 스토리(300자, 자동 요약 버튼), 실제 스토리(길이 무제한, 이미지 ≤10장 첨부 → 즉시 분석 → 편집 가능 카드), 스토리 타입 버튼 3종(필수) | ADD | `app.js` `index.html` `styles.css` | **APPLY** | §12 |
| L-16 | **패널 순서 재배치.** `applyFeatureFlags()`가 신규 두 구획을 옛 패널(원본 자료·기획 방향·AI 작업) **위로** 올림. 두 곳 다 원문 붙여넣는 큰 칸이 있어 순서가 뒤바뀌면 사용자가 옛 칸에 적는다. 옛 패널 위에 "이전 방식" 안내 줄 | FIX | `app.js` `index.html` | **APPLY** | §12 |
| L-17 | **새 프로젝트 모달에서 진입점 선택 구간 삭제** (플래그가 아니라 실제 삭제). 남은 세 참조가 모두 `?.` 또는 `$$`라 배선 안 끊김. 입력칸 "스토리" → "요약 스토리"(300자), `create_project`가 같은 글을 `story`(기존 표지 생성이 읽음)와 `summaryStory` 양쪽에 저장 | REMOVE + FIX | `app.js` `index.html` `server.py` | **NEEDS_DECISION** | §12 · 아래 주석 참조 |
| L-18 | **대본 승인 게이트.** 모달에서 시작화~끝화·회차 분량·형식·고품질 선택 + 예상 토큰·비용 표시. 아웃라인을 먼저 보여주고 **승인 전에는 회차 생성 시작 안 함** | ADD | `app.js` `server.py` `story_router.py` | **APPLY** | §12 |
| L-19 | **PDF 내보내기** | ADD | NEW `app/src/pdf_export.py`, `app/requirements.txt` | **APPLY** | 파일 실측 |
| L-20 | **`app/requirements.txt` 의존성 변경** | ADD/FIX | `app/requirements.txt` | **APPLY** (합집합, 버전 충돌 시 상위 + 표기) | 파일 실측 |
| L-21 | **`nginx.conf` CSP `img-src` 호스트 교정.** 구 계정 버킷 `mtx-test-ai-change-toon*`이 박혀 있었음. 여전히 `Content-Security-Policy-Report-Only` 모드 | FIX | `frontend/nginx.conf` | **APPLY** (호스트는 Hub 값으로 치환) | §13 |
| L-22 | **검증 스크립트 갱신** | FIX | `deploy/verify/{common,verify-image-brief,verify-models-usage}.ps1` | **APPLY** | 파일 실측 |
| L-23 | **`docs/구조분석.md` 갱신** | FIX | `docs/구조분석.md` | **APPLY** (Base 문서를 덮지 않고 병합 또는 `_overlay` 접미사) | 파일 실측 |

### 1-b. 재집계에서 추가된 항목 (2026-09-08)

| ID | 항목 요약 | 분류 | 영향 경로 | 액션 | 근거 |
|---|---|---|---|---|---|
| L-62 | **인증·한도 가드를 모듈로 추출.** `issue_token` `verify_token` `require_auth` `require_admin` `quota_block` `require_quota` 6개를 `server.py`에서 `http_guards.py`로 옮겼다. 새 라우트를 블루프린트(`story_dev.py`)로 분리하려면 그 모듈이 `require_auth`를 import할 수 있어야 하기 때문(`http_guards.py` L9 주석). **diff에서는 `server.py` −155/+3 삭제로 보이지만 삭제가 아니다** | ADD (리팩터링) | NEW `app/src/http_guards.py` + MOD `app/src/server.py` | **NEEDS_DECISION** — Base `server.py` 구조를 바꾸는 리팩터링이라 "기능 추가만 수용"과는 별개 판단이 필요하다. 도입하지 않으면 Overlay 신규 라우트의 `require_auth` import 경로를 조정해야 한다 | 심볼 존재 확인 |
| L-63 | **정적 검사 도구 3종.** `check_constants.py`(두 곳에 적힌 상수 불일치), `check_imports.py`(문법·미사용 import·미정의 전역 참조), `check_length_budget.py`(분량 예산이 수용 기준을 만족하는지) | ADD | NEW `tools/check_*.py` | **APPLY 후보** — Phase 1.5 정합성 검증에 그대로 쓸 수 있다 | 파일 실측 |
| L-64 | **Overlay 자체 Kiro spec + 계획서** — `.kiro/specs/story-dev-beta/{requirements,design,tasks}.md`(123KB) + `스토리개발_Beta_계획.md`(64KB). R-ID 기반 수용 기준과 Given/When/Then 포함. 사용자 요청 항목 2·3의 **설계 원본** | ADD (문서) | NEW | **필독 후 반영 방식 결정** — `AI-POC/docs/`에 `_overlay` 접미사로 가져오거나 참조만 할지. **Phase 1 계획 전 반드시 읽는다** | 파일 실측 |
| L-65 | **스토리 기능 추가 모듈 5종** — `length_budget.py`(분량 예산), `story_artifacts.py`(산출물), `story_dev_models.py`·`story_dev_router.py`(스토리 개발 Beta), `story_stale.py`(오래된 작업 처리) | ADD | NEW `app/src/*.py` | **APPLY** (항목 ②③ 범위) | 파일 실측 |

**L-17 주석**: 진입점 선택 삭제는 REMOVE 성격이라 H2에 따라 기본은 SKIP이다. 그런데 이 삭제는 L-01(바이블 중간표현 구조)의 **전제**다 — 진입점별 프롬프트 분기를 남긴 채 신규 구조를 얹으면 두 구조가 같은 화면에서 충돌한다. Base 기능 보존(H2)과 신규 기능 정합성이 상충하므로 결정이 필요하다. Overlay는 `legacyInputType` 플래그로 프로젝트 상세의 진입점 표시는 숨기고 모달에서는 실제 삭제하는 절충을 택했다.

---

## 2. GPU · 영상 기능 (D-14 종속)

| ID | 항목 요약 | 분류 | 영향 경로 | 액션 | 근거 |
|---|---|---|---|---|---|
| L-25 | **GPU 화면을 삭제하지 않고 feature flag로 숨김.** `app.js` 맨 끝 `FEATURES = { gpuVideo:false, longForm:false, legacyInputType:false }` + `applyFeatureFlags()`. **모든 이벤트 배선이 끝난 뒤** 실행되므로 요소가 사라져 배선이 끊기는 사고가 없다. 되살릴 때는 값만 `true` | ADD (구조) | `app.js` | **구조는 APPLY** | §5 |
| L-26 | `gpuVideo:false` — 사이드바 `NEW_영상 제작`(`#navVideoNew`), 스튜디오의 GPU 안내 문단을 숨김 | REMOVE (효과) | `app.js` | **NEEDS_DECISION (D-14)** | §5 |
| L-27 | `longForm:false` — 스튜디오 롱테이크 묶음(`#longTakeBlock`: 목표 길이·예상·시작 버튼)을 숨김 | REMOVE (효과) | `app.js` | **SKIP 권고 → `true`** (Base에 `stitcher.py` 롱테이크 경로가 살아 있음, H2) | §5 |
| L-28 | `legacyInputType:false` — 프로젝트 상세의 진입점 선택(`#detailInputTypeOptions`)과 배지를 숨김 | REMOVE (효과) | `app.js` | **NEEDS_DECISION** (L-17과 연동) | §5 |
| L-29 | **Bedrock 영상을 "베타"로 성격 변경.** 메뉴 배지 `BEDROCK`→`BETA`, 사이드바 `새 영상 만들기`→`영상 만들기`, 화면 상단 안내 상자("AWS Bedrock의 영상 생성 모델을 직접 써 보실 수 있도록 제공하는 베타 기능입니다"). 제품 기능이 아니라 Bedrock 체험 기능으로 재정의 | ADD/FIX | `app.js` `index.html` | **APPLY** | §5 |
| L-30 | **GPU 노드그룹 신규 생성.** `story-ai-gpu` / `g4dn.xlarge` / `AL2023_x86_64_NVIDIA` / **min 0 · desired 0 · max 1** / `taint nvidia.com/gpu=true:NoSchedule` + NVIDIA device plugin DaemonSet (AL2023 NVIDIA AMI는 드라이버만 포함, plugin 없음). Overlay 계정은 GPU 쿼터가 0이어서 **동작 미확인** | ADD | `terraform_infra/eks_nodegroups.tf`, `kubernetes_gpu.tf` | **REIMPLEMENT** — **D-14 종속** | §6, §13 |

> **H2 ↔ H3 충돌 (D-14).** L-30은 Overlay의 ADD이므로 H2대로면 APPLY다. 그런데 H3는 "GPU는 Base 우선"이고 **Base에는 GPU 인프라 자산이 없다**(`00_hub_discovery.md` §4.1) — Base 우선을 문자대로 적용하면 GPU는 존재하지 않는다. 동시에 G3·A6은 "GPU 노드 Ready"를 완료 기준으로 요구한다. 세 규칙이 동시에 성립할 수 없다.
> 참고 사실: **Hub 계정의 GPU 쿼터는 768**(Overlay는 0)이므로 기술적 제약은 없다. `desired_size = 0`이면 상시 비용도 없다.

---

## 3. 인프라 — Phase 3에서 모듈 기능으로 재구현 (복사 금지)

`이매지너스/terraform_infra/`는 **이매지너스 계정 전용**이다. 코드를 복사하지 않고 패턴·정책 내용만 참고한다 (PRD §4).

| ID | 항목 요약 | 분류 | Overlay 파일 | 액션 → 대상 모듈 | 근거 |
|---|---|---|---|---|---|
| L-31 | **IaC 도입.** 인수 시점 IaC 없음(사람이 CLI로 생성, 저장소에는 리소스 ID만). 기존 리소스 33개를 `import`로 인수 | ADD | `imports.tf` | **REIMPLEMENT** → `terraform/hub/10-network/` (Phase 2) | §3 |
| L-32 | **`allowed_account_ids` 가드.** 과거 계정을 옮기다 원래 계정에 리소스를 만든 사고 기록(`infra/build-deploy.sh` 주석)이 근거 | ADD | `providers.tf` | **REIMPLEMENT** → 모든 root (설계 §5.3, 이중 방어) | §3 |
| L-33 | **`store` state 보호.** `.gitignore`가 `*.tfstate`·`terraform.tfvars` 차단 | ADD | `.gitignore` | **REIMPLEMENT** → `AI-POC/.gitignore` (Phase 0.1에서 완료) | §10 |
| L-34 | **노드그룹 `ignore_changes = [scaling_config[0].desired_size]`.** 없으면 18시에 0으로 내려간 노드를 다음 `apply`가 다시 올려 밤새 과금 | FIX | `eks_nodegroups.tf` | **REIMPLEMENT** → `modules/nodegroup` | §7 |
| L-35 | **vpc-cni 전용 IRSA.** 노드 역할에 `AmazonEKS_CNI_Policy`를 **붙이지 않음** — 붙이면 그 노드의 모든 파드가 ENI를 조작할 수 있다 | ADD | `iam_app.tf` | **REIMPLEMENT** → `modules/irsa` + `modules/nodegroup` | §9 |
| L-36 | **앱 IRSA `story-ai-app-role` + S3·Bedrock 정책.** S3는 프리픽스 단위로 좁힘: `users/*`, `_config/models.json`, `_config/prompts.json`, `_cache/*`. **버킷 전체 권한 안 줌** | ADD | `iam_app.tf` | **REIMPLEMENT** → `modules/irsa` (Base `infra/app-{s3,bedrock}-policy.json.tmpl`과 대조) | §9, §12 |
| L-37 | **AWS Load Balancer Controller (Helm) + 전용 IRSA.** EKS 관리형 애드온에 없어 Helm 설치 | ADD | `alb_controller.tf` | **REIMPLEMENT** → `modules/app` + `modules/irsa` | §9 |
| L-38 | **ECR 수명주기 정책.** untagged 1일, `20*` 태그 10개 유지. 패턴을 `20*`로 좁힌 이유는 `backup-*` 태그가 개수 제한에 걸려 지워지지 않게 | ADD | `ecr.tf` | **REIMPLEMENT** → `modules/registry`. **Hub ECR에는 이미 동일 정책 적용됨** | §9 |
| L-39 | **Neptune `story-ai-graph`** (`db.t4g.medium`, 암호화, 포트 8182). 인수 시점 `NEPTUNE_ENDPOINT`가 빈 값이었음 | ADD | `neptune.tf` | **REIMPLEMENT** → `modules/graph` — **D-17 종속** | §9 |
| L-40 | **us-west-2 S3 버킷** `story-ai-726990466389-us-west-2`. Bedrock 비동기 영상 출력 버킷은 **모델과 같은 리전**이어야 함 (`luma.ray-v2:0`이 us-west-2 전용). 없으면 영상 생성 동작 안 함 | ADD | `s3.tf` | **REIMPLEMENT** → `modules/storage` (별칭 provider) — **D-17 종속** | §9 |
| L-41 | **프라이빗 서브넷 `kubernetes.io/role/internal-elb=1` 태그 추가.** 실측 서브넷에 없었음 | FIX | `vpc.tf` | **REIMPLEMENT** → `10-network` 태그 추가 — **D-13**. Hub 서브넷 9개에도 없음(실측) | §9 |
| L-42 | **EventBridge Scheduler 8개 + 전용 그룹.** 기동 `cron(0 9 ? * MON-FRI *)` 평일만 / 중지 `cron(0 18 ? * * *)` 매일(안전망). 대상 4종(앱 노드그룹, GPU 노드그룹, bastion, Neptune). 주말은 `power.ps1 start` 수동 | ADD | `scheduler.tf`, `power.ps1` | **NEEDS_DECISION** (비용 절감 장치. Hub에는 스케줄 0건) | §7 |
| L-43 | **ACM 자체 서명 인증서 import + 443 리스너.** 도메인이 없어 퍼블릭 인증서를 발급받을 수 없음. 80·443 둘 다 열고 **같은 내용 반환**, `ssl-redirect` 미설정(켜면 http가 신뢰되지 않는 인증서 경고로 끌려감) | ADD | `alb_tls.tf`, `kubernetes.tf` | **NEEDS_DECISION** — Base는 의도적으로 **80만** 열어 둠(README "이관하면서 고친 것" 3) | §4 |
| L-44 | **Access Entry 전환** (`authentication_mode = API`) + **엔드포인트 퍼블릭+프라이빗**. 퍼블릭으로 연 이유는 Terraform이 로컬에서 k8s/Helm 리소스를 직접 관리해야 하기 때문. `aws-auth` ConfigMap은 잘못 편집하면 전원이 잠기고 복구가 어려워 회피 | ADD/FIX | `eks.tf` | **NEEDS_DECISION (D-15)** — Hub 실측은 프라이빗 전용 + `API_AND_CONFIG_MAP`(단 `aws-auth` CM 부재) | §8 |
| L-45 | **Secret을 Terraform이 관리.** `kubernetes_secret` + `random_password`. `token-secret`은 값이 바뀌면 모든 세션이 즉시 무효라 state에 남겨 `apply` 간 유지. **부작용: state에 관리자 비밀번호·`token-secret` 평문이 들어감** | ADD/FIX | `kubernetes.tf` | **NEEDS_DECISION (D-07)** — H6상 SSM Parameter Store 권고 | §10 |
| L-46 | **bastion 빌드 파이프라인.** 로컬에 Docker가 없어 배스천에서 빌드. tar.gz → `s3://<bucket>/_build/` → presigned URL → SSM → 빌드·검증·push → `rollout restart`. 빌드 중 검증: `app.js` 문법, Python import smoke, 읽기 전용 루트에서 ffmpeg 실행. ECR IMMUTABLE이라 빌드 **전에** `describe-images`로 태그 중복 확인 | ADD | `build-images.ps1`, `_build-push.sh` | **참고만** — Base `deploy/deploy.ps1`이 **이미 동일 구조를 갖고 있다**(실측) | §11 |
| L-47 | **`config.env`를 Terraform 출력으로 생성** (`terraform output -raw config_env`). 기존 파일은 `config.env.bak-preterraform`으로 보존 | ADD | `outputs.tf` | **NEEDS_DECISION** — Base는 `config.env`를 수동 단일 원본으로 씀 | §2 |
| L-48 | **SSM 출력 base64 래핑.** AWS CLI v2가 `get-command-invocation` 결과의 한글을 cp949로 쓰다 죽어서 결과를 못 받고 타임아웃까지 InProgress만 찍는 문제 | FIX | `ssm-run.ps1` | **참고 권고** — Kiro도 Phase 0에서 동일 계열 인코딩 문제를 겪음(run-log 참조) | §12 |

---

## 4. 브랜딩 · 고객사 자산 (D-09)

| ID | 항목 요약 | 분류 | 영향 경로 | 액션 | 근거 |
|---|---|---|---|---|---|
| L-49 | **브랜드 문자열 교체.** 탭 제목 `Toon Motion AI`→`이매지너스 \| AI POC 프로젝트`, 사이드바 `TOON MOTION`+`AI STUDIO`→`이매지너스`+`AI POC 프로젝트`, 로그인 화면(넓은/좁은) `TOON MOTION`→`이매지너스`, 상단 경로 `AI STUDIO`→`AI POC · 이매지너스`, meta description, `app.js` 화면 제목 기본값 `AI Studio`→`이매지너스`. 이미지에 구워지므로 `image_tag`를 `20260905-v1`→`20260906-v2`로 올려 반영 | ADD/FIX | `index.html` `app.js` | **구조만 APPLY** — 값은 Base 기본값/placeholder. `BRAND_NAME`·`BRAND_SUBTITLE`·`APP_TITLE` 키로 분리해 `render.py`가 주입 | §1 |
| L-50 | **백엔드 `SERVICE_NAME = "toon-change-ai-pj-app"` 유지.** `/api/health`의 `service` 값, 로거 이름(`toon-change-ai-pj-app.graph` 등), S3 오브젝트 메타데이터에 함께 쓰여 바꾸면 기존 로그 검색·S3 메타데이터가 갈린다. 화면에 노출되지 않아 그대로 둠 | (변경 없음) | `server.py`, `graph_store.py`, `wan_client.py`, `stitcher.py` | **변수화만, 값 변경 금지** | §1, §13 |
| L-51 | **고객사 자산 파일 12개.** `assets/brand/imaginus-logo.svg`, `assets/login/butterfly-effect.{mp4,jpg}`, `assets/posters/poster-*.webp`(6), `포스터들/*.webp`(4) | ADD | NEW 파일 | **구조·경로만 APPLY, 자산 미복사** (D-09) | 파일 실측 |
| L-52 | **`tools/render_butterfly_effect.py`** — 로그인 영상 생성 도구 | ADD | NEW `tools/` | **NEEDS_DECISION (D-09)** | 파일 실측 |
| L-53 | **`나비 영상.mp4`(24MB급), `콘티_러프_1 (1).pdf`(24MB)** | ADD | NEW 루트 파일 | **SKIP** — 제3자 저작물·산출물. 무시 경로 | 파일 실측 |
| L-54 | **`deploy/config.env.bak-preterraform`** | ADD | NEW | **SKIP** — 이매지너스 계정 값 백업 (H7) | §2 |

---

## 5. Overlay가 "아직 남은 일"로 적은 항목 (§13)

Base에도 그대로 적용되는지 확인이 필요한 것들.

| ID | 항목 | Hub 실측 | 판정 |
|---|---|---|---|
| L-55 | GPU 쿼터 상향 (`L-DB2E81BA`, Overlay는 0) | **Hub는 768** | Hub에서는 제약 아님 |
| L-56 | Bedrock Guardrails + Rekognition 사전 스크리닝 (업로드 시점 차단 없음, 비전 분석가의 `safety_flags`만) | 미구현 | **범위 밖** — 별도 과제로 기록 |
| L-57 | Claude Sonnet 5 / Opus 5 접근 거부 (계정 단위) | **Hub 미확인** | Phase 4 전에 `bedrock list-foundation-models`로 확인 필요 (L-08 종속) |
| L-58 | 도메인 + ACM 인증서 없음 | Hub도 없음 (`*.meta-clouds.com`만, 무관) | `APP_DOMAIN` 빈 값 유지, 80 리스너만 |
| L-59 | CSP를 강제 모드로 전환 (`Content-Security-Policy-Report-Only` → `Content-Security-Policy`) | Base도 Report-Only 추정 | **APPLY 후보** — 위반 0건 실측 뒤 헤더 이름만 변경 |
| L-60 | `infra/status-check.sh`, `apply-ingress.sh.tmpl`, `verify-all.sh.tmpl`에 구 계정 이름 하드코딩 (`quick-test-eks`, `toon-change-ai-pj`) | **Base도 동일 파일 보유** | **FIX 대상** — Phase 1.4 치환 범위에 포함 |
| L-61 | `deploy/schedules.ps1` / `start-all.ps1` / `stop-all.ps1`의 `$known` 목록이 구 계정 스케줄 이름. Overlay는 `power.ps1`로 대체 | **Base도 동일** (`quick-test-eks-gpu` 등, `00_hub_discovery.md` §4.3) | **보고만** — 수정은 범위 밖(R6.9). `power.ps1` 도입은 L-42 종속 |

---

## 6. 요약

| 액션 | 건수 | 항목 |
|---|---|---|
| **APPLY** | 20 | L-01~07, L-09~16, L-18~23, L-25(구조), L-29 |
| **REIMPLEMENT** (모듈로) | 8 | L-31, L-32, L-34, L-35, L-36, L-37, L-38, L-41 |
| **SKIP** | 4 | L-27(→`true`), L-53, L-54, L-61(보고만) |
| **NEEDS_DECISION** | 12 | L-08(모델), L-17·L-28(진입점), L-26(D-14 GPU UI), L-30(D-14 GPU 노드그룹), L-39(D-17 Neptune), L-40(D-17 영상버킷), L-42(스케줄러), L-43(443/ACM), L-44(D-15 엔드포인트), L-45(D-07 Secret), L-47(config.env 생성), L-52(tools) |
| **참고만** | 3 | L-46(Base가 이미 보유), L-48(인코딩 교훈), L-56(범위 밖) |
| **값 변경 금지** | 1 | L-50(`SERVICE_NAME`) |
