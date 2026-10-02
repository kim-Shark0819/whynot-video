# 01 · 병합 보고서 (Phase 1, 게이트 1-B)

| 항목 | 내용 |
|---|---|
| 계획서 | `docs/01_merge_plan.md` |
| **병합 원본** | **`이매지너스` @ 커밋 `298717a`** ("배포 태그 20260907-v16 시점", 142파일) |
| 대상 | `AI-POC/` 브랜치 `merge/imaginus-overlay` |
| 기준선 | `baseline` = `7ebee61` |
| 커밋 | `fa87a62` (백엔드) · `a76d25c` (프론트엔드) |
| 일시 | 2026-09-08 |

---

## 1. 수행 내용

사용자가 지정한 순서대로 3개 기능을 병합하고 GPU는 손대지 않았다.

| 순서 | 기능 | 반영 내용 |
|---|---|---|
| ① | **로그인** | `POST /api/auth/password` · `graph_store.change_password()` · `_config_password_rotated()`(config.env 비밀번호 로테이션 감지) · 비밀번호 변경 UI |
| ② | **LLM 추가** | 신규 모듈 10개(`story_router` `story_context` `story_models` `prompts_story` + `roles/*` 6) · `model_registry.py`(+184) 역할→모델 매핑 · `prompt_layers.py`(+123) · `gateway.py`(+10) · `GET /api/admin/story/models` · 2단 검수 · 비전 분석 S3 캐시 |
| ③ | **프로젝트 내용** | story/bible/outputs/script-jobs/PDF 라우트 20여 개 · `graph_store.py`(+822) project 5필드·정점 3종 · `pdf_export.py` · 프로젝트 상세 스토리·결과물 구획 |
| ④ | **GPU** | **무변경.** Protected 5개 파일 diff 0. flag `gpuVideo`·`longForm`·`legacyInputType` 모두 `true` |

### 1.1 파일 단위 결과

| 분류 | 계획 | 실제 |
|---|---|---|
| NEW → COPY | 16 | 16 (코드 12 + Overlay spec 문서 4 → `docs/story-dev-beta_overlay/`) |
| NEW → SKIP (자산) | 15 | 15 (고객사 자산 9 + 루트 자산 5 + 로고 생성 도구 1) |
| MODIFIED → 적용 | 19 | 18 (`verify-image-brief.ps1`은 baseline 복원 — §3.4) |
| BASE_ONLY → KEEP | 1 | 1 (`deploy/config.env`) |
| 신규 생성 | — | `assets/brand/logo.svg`, `deploy/verify/verify_merge_nodelete.py` |

### 1.2 브랜드 로고 (사용자 결정)

> 브랜드 로고는 빈칸으로 두고 언제든지 바꿀 수 있도록 공백 이미지로 두어 줘.
> 로그인 화면과 글귀는 그대로 둬도 돼.

- `frontend/public/assets/brand/logo.svg` — **의도적으로 비어 있는 SVG**(viewBox 339×64). 파일 하나만 교체하면 **로그인 · 모바일 · 사이드바 · 상단 경로 4곳**이 함께 바뀐다. 규격과 교체 방법을 파일 주석에 적었다
- Overlay의 `<img class="brand-logo">` 구조는 채택하고 자산만 이 저장소 것으로 바꿨다
- **텍스트 라벨(`TOON MOTION` / `AI STUDIO`)을 함께 남겼다** — 로고가 비어도 제품을 식별할 수 있다
- 로그인 비주얼은 Base의 `scene-stack` CSS 일러스트를 유지했다. Overlay의 나비 영상(`butterfly-effect.mp4`)과 이매지너스 로고는 고객사 자산이라 반영하지 않았다 (D-09)
- 글귀는 Overlay도 바꾸지 않아 그대로다

---

## 2. 결과 (검증)

전용 도구를 만들어 커밋했다: `deploy/verify/verify_merge_nodelete.py`

| 항목 | 기준선 | 병합본 | 판정 |
|---|---|---|---|
| 파일 집합 | 79 | 155 | **PASS** 삭제 0 |
| Python 최상위 심볼 | 644 | 904 | **PASS** 삭제 0 (이동 2건은 §3.1) |
| Flask 라우트 | 53 | **76** | **PASS** 삭제 0, 신규 23 |
| K8s 리소스 | 8 | 8 | **PASS** |
| Protected (GPU) 5파일 | — | — | **PASS** 전부 diff 0 |

| 검사 | 결과 |
|---|---|
| `python -m compileall app/src` | exit 0 |
| `python deploy/render.py --check` | 템플릿 13개, **미해결 변수 0** |
| 렌더 YAML 파싱 | 5파일 8문서 정상 (`Ingress/ai-poc` `ConfigMap/ai-poc-models` `Namespace/ai-poc` `ServiceAccount/ai-poc-app` `Deployment`·`Service` × 2) |
| 자산 참조 무결성 | `/assets/brand/logo.svg` 존재 |
| app.js DOM id 대조 | 병합에서 손댄 15개 전부 존재. `#loginMotionVideo`는 널 가드 확인 |
| 비밀정보 (AWS 키 · PEM) | **0건** |
| 이매지너스 계정값 · `story-ai` 접두 | **0건** |

`kubectl apply --dry-run=client`는 쓸 수 없었다 — kubectl이 `--dry-run=client`에서도 API group 발견을 위해 서버에 접속하고, 이 클러스터는 프라이빗 전용이다. YAML 파싱으로 대체했다.

### 2.1 되돌린 것 (H2 · SKIP 13건)

| 대상 | 이유 |
|---|---|
| `handoffPanel`(제작 넘기기 카드 3개) + `app.js`의 표시 코드 | Base 기능. Overlay가 지운 자리에 스토리 구획을 넣었으나 삭제는 반영하지 않는다 |
| 진입점 4종 선택 그리드(`inputTypeOptions`) | `legacyInputType: true`. Overlay 주석도 "inputType은 데이터 모델에 남아 있다"고 밝혔다 |
| `projectStory` `maxlength` 8000 (Overlay는 300) | 줄이면 기존 사용자의 긴 원문이 잘린다 |
| 롱테이크 안내 문장 | `longForm: true` |
| `scene-stack` 마크업 + CSS 19규칙 | 로그인 비주얼 Base 유지 |
| `verify-image-brief.ps1` 픽스처 인물명 4건 | Protected 파일. diff 0 유지(A3) |

CSS 복원은 Base 원문과 **바이트 단위로 대조**했다. 콘솔 출력이 잘린 값을 옮겨 적어 `.scene-character` 등 5개가 틀렸던 것을 검사가 잡아 고쳤다.

---

## 3. 발견한 차이 · 이슈

### 3.1 `server.py`의 심볼 2개 이동 (검증이 잡음)

`COVER_IMAGE_MODEL` · `COVER_IMAGE_REGION`이 사라지려 해 병합을 중단하고 조사했다. 삭제가 아니라 **중복 정의 제거**였고 Overlay 주석이 Base의 버그를 기록하고 있었다.

> "예전에는 이 파일에도 같은 환경변수의 기본값이 적혀 있었고, 두 값이 어긋난 상태에서 카탈로그가 비어 관리자가 지정한 적 없는 모델이 호출됐다."

같은 환경변수를 `model_registry.py`가 읽는 것(`BUILTIN_IMAGE_MODEL_ID` / `IMAGE_REGION`)을 확인하고 통과시켰다.

**동반되는 동작 변화**: 표지·콘티 이미지 모델 기본값이 `stability.stable-image-core-v1:1` → `stability.sd3-5-large-v1:0`. 사용자 승인으로 수락했다. 되돌리려면 `COVER_IMAGE_MODEL` 환경변수로 고정한다.

### 3.2 `nginx.conf` CSP가 양쪽 다 틀렸다

| | `img-src` 호스트 |
|---|---|
| Base | 원본 계정의 버킷 |
| Overlay | 이매지너스 계정의 버킷 |
| **병합본** | **이 계정의 버킷** (`config.env`의 `S3_BUCKET`과 동일) |

CSP가 `Report-Only`라 차단되지는 않았지만 강제 모드로 바꾸면 이미지가 전부 막히는 상태였다.

**남은 문제**: 버킷명이 하드코딩되어 있다(H7은 변수화를 요구). 이 파일은 `frontend/Dockerfile`이 직접 `COPY`하므로 `deploy/render.py`의 `.tmpl` 경로 밖이고, 템플릿화하려면 빌드 컨텍스트를 함께 바꿔야 해 Phase 1 범위를 넘긴다. 파일에 TODO로 표시했다.
us-west-2 영상 버킷은 아직 없어(`S3_VIDEO_BUCKET` 빈 값) 두 번째 호스트는 넣지 않았다.

### 3.3 `start-all.ps1`의 Overlay 가드가 사실과 달랐다

Overlay가 넣은 실행 차단 가드는 유용하지만 내용이 이 계정과 맞지 않았다.

| Overlay 주장 | 이 계정 실측 |
|---|---|
| 대체 스크립트는 `terraform_infra/power.ps1` | **그 폴더를 병합하지 않았다** — 존재하지 않는 경로 |
| "G/VT 온디맨드 vCPU 쿼터가 0이라 GPU가 뜨지 않는다" | **쿼터 768** (Phase 0 실측) |
| "이 계정의 스케줄은 `story-ai-*`" | **이 계정에는 스케줄이 하나도 없다** |

차단은 유지하고 안내를 이 계정 사실로 다시 썼다.

### 3.4 Base에 이미 있던 문제 (수정하지 않고 보고 — R6.9)

`baseline`과 대조해 병합이 들여온 것과 Base 원래 것을 갈랐다.

| 위치 | 내용 | 영향 |
|---|---|---|
| `app/src/model_registry.py` · `prompt_layers.py` | `S3_BUCKET` 기본값이 **원본 계정 버킷** | `config.env`가 항상 설정하므로 배포 시 무해. 환경변수가 없으면 다른 계정 버킷을 향한다 |
| `README.md` · `docs/구조분석.md` | 원본 계정 ID 참조 | 이관 출처를 적은 정당한 문서 |
| `infra/build-deploy.sh` · `k8s/model-catalog.yaml.tmpl` | 원본 계정 ID | 확인 필요 |
| `deploy/verify/verify-models-usage.ps1` | 음성 테스트 픽스처의 버킷명 | 400을 기대하는 테스트라 무해 |
| `deploy/schedules.ps1` · `stop-all.ps1` | 원본 계정 스케줄 이름 (`quick-test-eks-*`) | 이 계정에 스케줄이 없어 무동작 |

### 3.5 스토리 개발 Beta는 병합하지 않았다

`이매지너스/`는 다른 세션이 작업 중인 활성 워킹 트리다. 병합 중 실측된 변화: `graph_store.py` +52KB, `server.py` 2회 변경, 신규 모듈 7개와 git 저장소·venv 생성.

`이매지너스/.kiro/specs/story-dev-beta/진행상황.md`가 그 세션의 상태를 밝힌다 — 87개 태스크 중 데이터 계층까지 완료, **"아직 HTTP로 노출된 것이 없다"**, Neptune 쿼리와 S3 접근은 문법만 확인.

그래서 병합 원본을 불변 커밋 `298717a`로 고정해 미완성 WIP 11개를 제외했다. 설계 문서 4종(123KB + 64KB)은 `docs/story-dev-beta_overlay/`로 가져와 2차 병합의 근거로 남겼다.

### 3.6 스케줄 실측 (사용자 요청: 있으면 비활성화)

| 스케줄 | 상태 | 대상 | 조치 |
|---|---|---|---|
| `rule-lambda-startscheduler` (EventBridge 규칙) | **DISABLED** | `lambda-startscheduler` | 이미 비활성 — 조치 없음 |
| `rule-lambda-stopscheduler` | **DISABLED** | `lambda-stopscheduler` | 이미 비활성 — 조치 없음 |
| `grafana-event` (EventBridge Scheduler) | ENABLED · `cron(*/5 * * * ? *)` Asia/Seoul | Lambda **`securityhub-test`** | **범위 밖 — 손대지 않음** |
| Auto Scaling 예약 작업 | 0건 | — | — |

**AI-POC 관련 스케줄은 하나도 없다.** `grafana-event`는 Security Hub 점검 함수를 5분마다 호출하는 보안 모니터링이라 임의로 끄면 감시가 멈춘다. 명시적 지시가 있을 때만 조치한다.

향후 방침: Terraform 모듈의 `enable_schedules` 기본값을 **`false`**로 둔다 (Overlay는 `true`였다).

---

## 4. NEEDS_DECISION

| ID | 항목 | 상태 |
|---|---|---|
| M-1 | 병합 원본을 `298717a`로 고정 | **승인** |
| M-4 | 고객사 자산 구조만 반영 | **승인** — 로고는 공백 자리표시자로 |
| M-5 | `projectStory` `maxlength` 8000 유지 | **적용** |
| M-6 | `verify-image-brief.ps1` 픽스처 SKIP | **적용** |
| 신규 | 표지 이미지 모델 기본값 `sd3-5-large-v1:0` 수락 | **승인** |
| **M-2** | story-dev-beta 2차 병합 시점 | 그 세션 완성·커밋 후 |
| **잔여** | `nginx.conf` 버킷명 템플릿화 | 빌드 컨텍스트 변경 필요 — 별도 과제 |
| **잔여** | Base의 `S3_BUCKET` 기본값 교정 | Base 문제. 수정 여부 판단 필요 |
| **잔여** | L-57 Bedrock 모델 접근 확인 | Phase 4 전 `bedrock list-foundation-models` |

---

## 5. 다음 Phase 계획

**Phase 2 — VPC Terraform import.** Phase 0 인벤토리로 대상이 확정되어 있다.

| 단계 | 내용 |
|---|---|
| 2.1 | Terraform 1.14.3 확인, provider major 고정 (`이매지너스/terraform_infra/.terraform.lock.hcl` 참조) |
| 2.2 | `terraform/bootstrap/` — state 버킷 `ai-poc-tfstate-730335451955-ap-northeast-2` → **게이트 2-A (D-05)** |
| 2.3 | `terraform/hub/10-network/` — VPC 1 · 서브넷 9 · 라우트테이블 7 · IGW 1 · NAT 1 + EIP · 공용 SG 1(`AI-POC-bastion-sg`) import |
| 2.3 제외 | `eks-cluster-sg-AI-POC-eks-*`(EKS 소유) · `default` SG/NACL/RT · main RT `rtb-0df670383c3edfc74` · `Metanet-GitLab-SG` · **`AI-poc-ALB-SG`(GitLab 프론트 — F13)** · 고아 RT `rtb-0d34269ef6e001717`(F14) |
| 2.4 | `plan` = No changes 도달 후 **게이트 2-B** → apply → 재 plan 확인 → **게이트 2-C** |

`default_tags`는 `10-network`에서 쓰지 않는다 — import된 기존 리소스의 태그를 덮어써 drift를 만든다.
