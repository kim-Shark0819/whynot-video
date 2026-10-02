# AI-POC — 변경 이력

인수받은 상태(`baseline` 태그)와 현재의 차이를 기록한다. 큰 기능 단위만 적는다.
형식은 `이매지너스/이매지너스_history.md`를 따랐다.

- 계정: `730335451955` / `ap-northeast-2`
- 프로젝트 접두사: `ai-poc`
- 기준선: `baseline` = `7ebee61` (79파일, Python 최상위 심볼 644개)
- 최종 갱신: 2026-09-08

---

## 1. 계정 · 출처 정리

`AI-POC/`는 원본 계정에서 이관받은 것이다. 세 계정이 얽혀 있어 먼저 정리한다.

| 역할 | 접두사 / 클러스터 | 비고 |
|---|---|---|
| 원본 (이관 출발점) | `toon-change-ai-pj` / `quick-test-eks` | GPU·Wan 2.2 자산이 여기에만 있다 |
| **이 계정 (작업 대상)** | **`ai-poc` / `AI-POC-eks`** | 계정 `730335451955` |
| 이매지너스 (병합 원본) | `story-ai` / `story-ai-eks` | 읽기 전용. 리소스 접근 금지 |

PRD는 이 계정을 "운영 계정"으로 추정했으나 실제로는 **원본에서 이관받은 대상 계정**이다.

**주의**: 로컬 기본(`default`) AWS 프로필은 **원본 계정**을 가리킨다. 모든 명령에
`--profile ai-poc-hub`를 붙여야 한다. 빼면 엉뚱한 계정에 붙는다.

---

## 2. Phase 0 — 탐색 (AWS 쓰기 0)

git 저장소를 만들고(`baseline` 태그) as-is 인벤토리를 수집했다. 산출물은
`00_hub_discovery.md` · `01_change_ledger.md`(L-01~L-65) · `infra/inventory/hub/`(JSON 53 + K8s 2).

### 2.1 PRD 가정과 달랐던 것

| | PRD | 실측 |
|---|---|---|
| VPC 이름 | `AI-POC` | **`AI-poc-vpc`** (`vpc-0de9ff1693bd56f38`). PRD의 필터로는 0건 |
| `AI-POC-eks` | 노드그룹·애드온을 DEV가 미러 | **노드 0 / 애드온 0 / Fargate 0 / 워커 0** — 컨트롤 플레인만 있는 빈 클러스터 |
| GPU 자산 | Base가 보유, Overlay가 삭제 | **반대.** Base에 GPU 인프라가 없고(README "옮기지 않은 것"), GPU 노드그룹 코드는 **Overlay에만** 있다 |
| Wan 2.2 추론 서버 | — | **워크스페이스 5개 폴더 어디에도 없다.** 원본 계정에만 존재 |
| GPU 쿼터 | 부족 위험 | **768** (Overlay 계정은 0이었다) |
| `이매지너스_history.md` | 루트 | `이매지너스/` 하위에만 |

### 2.2 그 외 발견

- `AI-poc-ALB`의 타깃 그룹이 `AI-poc-GitLab-TG`다 — 이름이 `AI-poc-*`이지만 **GitLab 프론트**다. `AI-poc-ALB-SG`도 함께 범위 밖
- 서브넷 9개 전부에 `kubernetes.io/role/elb`·`internal-elb`·`cluster/*` 태그가 없다
- `aws-auth` ConfigMap이 없다 (인증 모드는 `API_AND_CONFIG_MAP`이지만 access entry만 사용)
- 자체관리 애드온 3종의 버전이 이미 1.36 관리형 default와 동일 — 관리형 등록은 업그레이드가 아니라 관리 주체 전환이다. `metrics-server`만 실제 신규
- `gp2` StorageClass가 in-tree `kubernetes.io/aws-ebs` 프로비저너다 — k8s 1.36에서 동작하지 않는다. PVC가 필요하면 `aws-ebs-csi-driver`가 선행
- `config.env`의 `CREDENTIALS_FILE`이 없는 파일을 가리켜 `preflight.ps1`이 즉시 중단된다 (미해결)

---

## 3. Phase 1 — 이매지너스 병합

병합 원본을 **커밋 `298717a`**("배포 태그 20260907-v16 시점")로 고정했다. `이매지너스/`는
다른 세션이 작업 중인 활성 워킹 트리라 병합 중에도 파일이 바뀐다(실측: `graph_store.py` +52KB,
`server.py` 2회 변경). 불변 커밋을 쓰면 대상이 고정되고 미완성 WIP가 자동으로 빠진다.

추가·수정만 반영하고 삭제는 반영하지 않았다.

### 3.1 로그인

- `POST /api/auth/password` — 비밀번호 변경
- `graph_store.change_password(username, current, new)`
- `graph_store._config_password_rotated()` — `config.env`의 비밀번호가 바뀌면 기동 시 반영
- 비밀번호 변경 UI

### 3.2 LLM 추가 (스토리 → 로그라인 / 콘티 / 대본)

바이블(작품 설정)을 중간 표현으로 두고 결과물 3종을 파생한다. 분배자는 **코드**(`story_router.py`)이고
LLM이 다른 LLM을 부르지 않는다. 역할끼리 대화 히스토리를 공유하지 않고 JSON 컨텍스트 팩만 주고받는다.

| 역할 | 모듈 |
|---|---|
| 라우터 (LLM 없음) | `story_router.py` |
| 비전 분석가 | `roles/vision.py` |
| 바이블 작성자 | `roles/bible.py` |
| 결과물 작성자 | `roles/writer.py` |
| 검수자 | `roles/validator.py` |

- 신규 모듈: `story_models.py` `prompts_story.py` `story_context.py` `story_router.py` `roles/{vision,bible,writer,validator,_json,__init__}.py`
- 비용 억제: 이미지가 없으면 비전 분석가를 부르지 않는다. 분석 결과는 S3 `_cache/story-analysis/`에 캐시하고 같은 이미지 재첨부 시 실호출 0. 바이블은 `source_hash`가 바뀔 때만 재생성
- **캐시가 조용히 죽던 버그 수정**: IRSA 정책이 `_cache/` 읽기를 거부했고 저장소 코드가 그것을 "캐시 없음"으로 처리해 비용이 두 배였다. 정책에 `_cache/*`를 추가하고 `AccessDenied`를 오류 로그로 드러낸다
- 2단 검수(코드 → Haiku). 검수 거짓 양성 2건 수정 — 요청에 목표 분량이 없으면 분량 검사 제외, 엔딩 훅은 코드가 존재 여부로만 판정
- `GET /api/admin/story/models` — 역할→모델 표

**모델 매핑은 다른 계정 실측이다.** Bedrock 접근은 계정 단위로 갈리므로 배포 전
`bedrock list-foundation-models`로 대조해야 한다(변경 원장 L-57).

### 3.3 프로젝트 내용

- 라우트 20여 개: story · story-text · summary · bible · assets(analyze/PATCH) · outputs(logline/storyboard/script/options/목록) · `/api/outputs/<id>`(+pdf) · script-jobs(목록/상세/approve/resume/episodes/regenerate/pdf)
- `project`에 5필드 추가: `summaryStory`(≤300자 인라인) `storyTextKey` `storyTextCharCount`(본문은 S3) `storyType` `storyAssetsJson`
- 신규 정점 3종: `story_bible` `story_output` `script_job`
- 마이그레이션 쿼리 없음 — openCypher가 없는 속성에 null을 주고 `_project_view`가 기본값으로 메운다. `storyType`만 기본값 없음(null = 타입 미선택)
- `pdf_export.py` — PDF 내보내기
- 대본은 아웃라인을 먼저 보여주고 **승인 전에는 회차 생성이 시작되지 않는다**
- 콘티 컷은 기존 `storyboard` 정점에 `SB_MODE_MANUAL`로 넘긴다 (`auto`면 컷을 다시 나눠 버린다)

### 3.4 GPU — 그대로 유지

| | |
|---|---|
| Protected 5파일 | `wan_client.py` · `video_prompt.py` · `stitcher.py` · `verify-video-generation.ps1` · `verify-image-brief.ps1` — **전부 diff 0** |
| feature flag | `gpuVideo` · `longForm` · `legacyInputType` **모두 `true`** (Overlay는 전부 `false`) |
| `VIDEO_GEN_URL` | 값 유지. 비우면 "연결 실패"가 아니라 "설정 오류"가 된다 |
| `ECR_VIDEO_GEN_REPO` · `VIDEO_GEN_TAG` | 빈 값 유지 |

**추론 서버는 없다.** 워크스페이스 어디에도 `video-gen/` 자산이 없어 화면은 "서버 연결 안 됨"을
표시한다 — Base가 이미 정상 경로로 다루는 동작이다. GPU 노드그룹·device plugin은 Phase 3에서
모듈로 구현하되 `desired_size = 0`으로 두어 상시 비용이 없다.

### 3.5 반영하지 않은 것

| 대상 | 이유 |
|---|---|
| `handoffPanel` 삭제 | Base 기능. 스토리 구획과 공존시킨다 |
| 진입점 4종 그리드 삭제 | `legacyInputType: true`. Overlay도 데이터 모델은 남겼다 |
| `projectStory` `maxlength` 300 | Base의 8000 유지. 줄이면 기존 긴 원문이 잘린다 |
| 롱테이크 안내 문장 삭제 | `longForm: true` |
| 로그인 나비 영상 + 이매지너스 로고 | 고객사 자산 (D-09) |
| 443 리스너 + 자체 서명 ACM | Base는 의도적으로 80만 열어 둔다 |
| 스토리 개발 Beta 구현 (모듈 7개) | **미완성 WIP.** 라우트 0개, Neptune 쿼리 미검증. 설계 문서만 `docs/story-dev-beta_overlay/`로 가져왔다 |

### 3.6 브랜딩

브랜드 로고를 **교체 가능한 공백 이미지**로 뒀다.

- `frontend/public/assets/brand/logo.svg` — 의도적으로 빈 SVG(viewBox 339×64)
- 이 파일 하나만 바꾸면 **로그인 · 모바일 · 사이드바 · 상단 경로 4곳**이 함께 바뀐다
- 텍스트 라벨(`TOON MOTION` / `AI STUDIO`)을 함께 남겨 로고가 비어도 제품을 식별할 수 있다
- 이매지너스 브랜드 문자열은 기본값으로 굳히지 않았다 (H7)

### 3.7 고친 것

| 대상 | 내용 |
|---|---|
| `server.py` → `model_registry.py` | `COVER_IMAGE_MODEL`·`COVER_IMAGE_REGION` 중복 정의 제거. 두 값이 어긋나면 관리자가 지정한 적 없는 모델이 호출됐다. **부수 효과**: 표지 이미지 모델 기본값이 `stable-image-core-v1:1` → `sd3-5-large-v1:0` (승인) |
| `frontend/nginx.conf` | CSP `img-src`가 Base는 원본 계정, Overlay는 이매지너스 계정 버킷을 가리켰다. 이 계정 버킷으로 교정. **버킷명이 하드코딩으로 남았다** — Dockerfile이 직접 COPY해 `.tmpl` 경로 밖이다 |
| `deploy/start-all.ps1` | Overlay의 실행 차단 가드는 유지하고 안내를 이 계정 사실로 다시 썼다. 없는 `terraform_infra/power.ps1`을 가리키고 GPU 쿼터를 0이라 적었으나 실제는 768이다 |
| `app/src/story_models.py` | 모델 실측 표의 계정 번호를 지우고 "이 계정에서 재확인 필요"를 명시 |

### 3.8 신규 도구

`deploy/verify/verify_merge_nodelete.py` — 집합 포함 관계로 삭제 0을 증명한다.
`git diff`는 대규모 재구성에서 **이동·모듈 추출을 삭제로 보이게** 하므로 쓸 수 없다
(실제로 인증 가드 6개가 삭제처럼 보였으나 다른 모듈로 옮겨진 것이었다).

파일 집합 · Python 최상위 심볼 · Flask 라우트 · K8s 리소스 네 집합을 `baseline`과 대조하고,
Protected 5파일의 diff가 0인지 확인한다. 이동이 확인된 심볼만 근거와 함께 예외로 둔다.

### 3.9 검증 결과

| 항목 | 기준선 → 병합본 |
|---|---|
| 파일 집합 | 79 → 155 (삭제 0) |
| Python 최상위 심볼 | 644 → 904 (삭제 0) |
| Flask 라우트 | 53 → **76** (삭제 0) |
| K8s 리소스 | 8 → 8 |
| Protected 5파일 | diff 0 |

`compileall` exit 0 · `render.py --check` 미해결 변수 0 · 렌더 YAML 8문서 파싱 정상 ·
AWS 키·PEM·이매지너스 계정값·`story-ai` 접두 **각 0건**.

---

## 4. 아직 남은 일

- **`config.env`의 `CREDENTIALS_FILE`** 이 없는 파일을 가리켜 `preflight.ps1`이 exit 1. 이것이 풀리기 전에는 어떤 배포도 시작되지 않는다
- **Neptune** 없음 → 로그인·프로젝트가 동작하지 않는다
- **노드그룹** 없음 → 파드가 뜨지 않는다
- **IRSA `AI-POC-app-role`** 없음 → S3·Bedrock 호출 실패
- **AWS Load Balancer Controller** 없음 → Ingress가 ALB를 만들지 못한다
- **ECR 이미지** 0개
- **서브넷 태그** (`role/elb`·`internal-elb`) 없음
- **us-west-2 영상 출력 버킷** 없음 → Bedrock 영상 생성 불가
- **Bedrock 모델 접근** 이 계정에서 미확인 (L-57)
- `nginx.conf` 버킷명 템플릿화 (빌드 컨텍스트 변경 필요)
- Base의 `S3_BUCKET` 기본값이 원본 계정 버킷 (`model_registry.py`·`prompt_layers.py`)
- 스토리 개발 Beta 2차 병합 (그 세션 완성 후)
- 기동/중지 스케줄 자동화 — 현재 이 계정에 AI-POC 스케줄은 **0건**. Terraform 모듈의
  `enable_schedules` 기본값은 `false`로 둔다
