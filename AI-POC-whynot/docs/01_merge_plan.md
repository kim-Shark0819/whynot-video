# 01 · 병합 계획서 (Phase 1, 게이트 1-A 승인 대상)

| 항목 | 내용 |
|---|---|
| 근거 | PRD §6 Phase 1 · §7 병합 규칙 / `01_change_ledger.md` / `.kiro/specs/ai-poc-dev-eks-terraform/decisions.md` (개정 R1) |
| **병합 원본** | **`이매지너스` @ 커밋 `298717a`** — 워킹 트리가 아니다 (§1) |
| 대상 | `AI-POC/` (브랜치 `merge/imaginus-overlay`, 아직 생성 안 함) |
| 기준선 | `baseline` 태그 = `7ebee61` (79파일 / Python 최상위 심볼 666개) |
| 작성 | 2026-09-08 |
| 상태 | **게이트 1-A 승인 대기 — 승인 전 파일 수정 없음** |

---

## 1. 병합 원본을 커밋으로 고정한 이유 (중요)

`이매지너스/`는 **다른 세션이 지금 작업 중인 활성 워킹 트리다.** 이번 세션 안에서 실측된 변화:

| 파일 | 12:55 | 15:5x | 변화 |
|---|---|---|---|
| `app/src/graph_store.py` | 123,658 B | 176,192 B | **+52KB** |
| `app/src/server.py` | 362,502 B | 356,283 B | −6KB (두 번 변경됨) |
| `docs/구조분석.md` | 30,684 B | 46,132 B | +15KB |
| `terraform_infra/{iam_app,locals,s3}.tf`, `terraform.tfvars` | — | — | 4건 변경 |
| 신규 | — | — | `http_guards.py` `story_dev.py` `story_dev_models.py` `story_dev_router.py` `story_artifacts.py` `story_stale.py` `length_budget.py` `tools/check_*.py` `story_dev.tf` + `.git/` + `.venv-check/` |

`이매지너스/.kiro/specs/story-dev-beta/진행상황.md`가 그 세션의 인수인계 문서다. 요지:

- **스토리 개발 Beta**라는 **별개의 더 큰 기능**(R-01~R-26, 87 태스크)을 구현 중이다
- Phase 1 ① 데이터 계층까지 끝났고 **task 1.6(`story_dev.py` 블루프린트)이 다음 작업**이었다
- **"아직 HTTP 로 노출된 것이 없다"** — 만든 모듈이 화면에서 쓰이지 않는다
- 검증 상태: `graph_store` 신규 쿼리 6종과 `story_artifacts` S3 접근은 **문법만 확인됨**
- 그 세션은 커밋을 하지 않았다 (`298717a` 1건뿐)

따라서 워킹 트리를 병합 원본으로 쓰면 **미완성·미검증 코드를 가져오고, 원본이 작업 중에 바뀐다.**

### 고정 기준

```
이매지너스 @ 298717a   "chore: 초기 커밋 — 배포 태그 20260907-v16 시점 워크스페이스"
                      2026-09-08 14:51:10 +0900 / 142 파일
```

이 커밋은 **실제 배포된 상태**(`20260907-v16`)이고 불변이다. 사용자가 요청한 3개 기능이 전부 이 안에 있다.

| 요청 항목 | `298717a`에 있는가 | 근거 |
|---|---|---|
| ① 로그인 | **있음** | `POST /api/auth/password` (server.py L929) |
| ② LLM 추가 | **있음** | `story_router.py` `story_context.py` `story_models.py` `prompts_story.py` `roles/*` 6개, `GET /api/admin/story/models` |
| ③ 프로젝트 내용 | **있음** | story/bible/outputs/script-jobs/PDF 라우트 16개, `pdf_export.py` |
| ④ GPU 그대로 | **해당** | `wan_client.py` `video_prompt.py` `stitcher.py` 모두 Base와 IDENTICAL |

그리고 `298717a`의 `server.py`는 **`http_guards`·`story_dev`를 import하지 않는다.** 즉 ledger L-62(가드 추출 도입 여부)가 **쟁점에서 사라진다.**

### 자동으로 제외되는 것 (미커밋 WIP 11개)

`http_guards.py` · `story_dev.py` · `story_dev_models.py` · `story_dev_router.py` · `story_artifacts.py` · `story_stale.py` · `length_budget.py` · `tools/check_constants.py` · `tools/check_imports.py` · `tools/check_length_budget.py` · `story-dev-beta/진행상황.md`

`graph_store.py`·`server.py`의 story-dev-beta 변경분도 함께 빠진다.

> **스토리 개발 Beta는 별도 과제로 둔다.** 그 세션이 완성해 커밋하면 그때 두 번째 병합으로 가져오는 것이 안전하다. 지금 가져오면 라우트 0개인 미완성 모듈 7개(~150KB)와 미검증 Neptune 쿼리가 들어온다.

---

## 2. 파일 분류 (`298717a` 기준, 필터 후 108파일)

| 분류 | 건수 | 기본 액션 |
|---|---|---|
| **NEW** | **31** | COPY (자산은 §5) |
| **MODIFIED** | **19** | MERGE — hunk 판정 §4 |
| IDENTICAL | 58 | 없음 |
| **BASE_ONLY** | **1** | KEEP |

무시 경로: `__pycache__` · `.terraform` · `.build` · `node_modules` · `*.tfstate*` · `*.pyc` · `terraform_infra/**`(참조 전용) · `_inventory/**` · `이매지너스_history.md` · `이매지너스.json`

**BASE_ONLY 1건 = `deploy/config.env`.** Overlay의 `.gitignore`가 비밀값 파일로 제외한 것이고 Base에는 실물이 있다. 삭제 쟁점이 아니다 — 오히려 비밀값이 커밋되지 않았다는 증거다. **KEEP.**

### 2.1 NEW 31건 액션

| 묶음 | 파일 | 액션 | 근거 |
|---|---|---|---|
| **항목② LLM** | `app/src/story_router.py` `story_context.py` `story_models.py` `prompts_story.py` | **COPY** | L-01·L-02 |
| **항목② 역할** | `app/src/roles/{__init__,_json,bible,validator,vision,writer}.py` (6) | **COPY** | L-01·L-03·L-06 |
| **항목③ PDF** | `app/src/pdf_export.py` | **COPY** | L-19 |
| 문서 | `.kiro/specs/story-dev-beta/{requirements,design,tasks}.md` · `스토리개발_Beta_계획.md` | **COPY → `AI-POC/docs/story-dev-beta_overlay/`** | L-64. 문서라 위험 없음. 향후 Beta 병합의 설계 원본 |
| 빌드 | `frontend/.dockerignore` | **COPY** | — |
| **고객사 자산** | `assets/brand/imaginus-logo.svg` · `assets/login/butterfly-effect.{mp4,jpg}` · `assets/posters/poster-*.webp`(6) | **SKIP (자산)** / **구조·경로만 반영** | L-51 · D-09 |
| 루트 자산 | `콘티_러프_1 (1).pdf` · `포스터들/*.webp`(4) | **SKIP** | L-53. 제3자 저작물 |
| 도구 | `tools/render_butterfly_effect.py` | **SKIP** | L-52. 이매지너스 로그인 영상 생성용 |

고객사 자산을 SKIP하되 **로드 구조는 반영한다** — `BRAND_NAME`·`BRAND_SUBTITLE`·`APP_TITLE`을 `config.env` 키로 뽑고 `render.py`가 주입한다. 자산 경로 규약(`assets/brand/`·`assets/login/`·`assets/posters/`)은 남기고 실물은 Base 기본값/placeholder를 쓴다.

---

## 3. GPU 무변경 증명 계획 (항목④ · A3)

Protected 항목은 `git diff baseline -- <경로>`가 **빈 출력**이어야 한다.

| 파일 | Overlay와의 관계 |
|---|---|
| `app/src/wan_client.py` | **IDENTICAL** — 병합 대상 아님 |
| `app/src/video_prompt.py` | **IDENTICAL** |
| `app/src/stitcher.py` | **IDENTICAL** |
| `app/src/server.py` Wan 함수 14개 | 아래 hunk 판정에서 해당 영역 무변경 확인 |
| `app/src/graph_store.py` wanjob 함수 7개 | 동일 |
| `k8s/workloads.yaml.tmpl` `VIDEO_GEN_URL` | Overlay도 IDENTICAL |
| `deploy/render.py` L61~76 | Overlay도 IDENTICAL |
| `config.env` `ECR_VIDEO_GEN_REPO`/`VIDEO_GEN_TAG` | 빈 값 유지 |
| `index.html` `wan*` 요소 62개 | hunk [7] `id="navVideoNew"` 추가만 (§4.3) |
| `app.js` `wan*` 식별자군 | 변경 없음 |
| `deploy/verify/verify-video-generation.ps1` | Overlay도 IDENTICAL |

**Protected 파일 3개가 이미 IDENTICAL이라는 것이 핵심이다.** Overlay는 GPU 코드를 건드리지 않았다.

---

## 4. Hunk 판정 (삭제를 포함한 59건 전수)

삭제를 포함하지 않는 hunk는 전부 APPLY이므로 여기서는 **삭제 hunk만** 다룬다. H2 위험이 그곳에만 있다.

| 판정 | 건수 |
|---|---|
| APPLY (FIX) | 38 |
| APPLY + 환경값 치환 | 8 |
| **SKIP (H2 · 순수 삭제)** | **3** |
| **개별 검토 → 판정 확정** | **10** |
| 합계 | **59** (PURE-DELETE 3 / REPLACE 56) |

파일별: `index.html` 16 · `model_registry.py` 9 · `server.py` 8 · `styles.css` 7 · `graph_store.py` 4 · `verify-image-brief.ps1` 4 · `verify-models-usage.ps1` 3 · `prompt_layers.py` 2 · `app.js` 3 · `gateway.py` 1 · `image_brief.py` 1 · `nginx.conf` 1

### 4.1 PURE-DELETE 3건 → 전부 SKIP

| 파일 | 삭제 내용 | 판정 |
|---|---|---|
| `app.js` [1] −2 | `$("#handoffPanel").classList.remove("is-hidden")` + 주석 | **SKIP** — Base의 "이 프로젝트로 만들기" 패널을 살린다 |
| `index.html` [3] −10 | 로그인 화면 `scene-stack` 장식(ORIGINAL CUT / AI MOTION) | **SKIP** — 이매지너스 나비 영상으로 교체하는 변경. D-09에 따라 Base 자산 유지 |
| `styles.css` [5] −1 | `.scene-stack { opacity: .8; }` | **SKIP** — 위와 한 쌍 |

### 4.2 개별 검토 10건

| # | 파일 | hunk | 내용 | 판정 |
|---|---|---|---|---|
| 1 | `verify-image-brief.ps1` | [2] −1/+1 | 픽스처 인물명 `지훈`→`김남주` | **SKIP** — Protected 파일이고 이득 없음 (A3 유지) |
| 2 | `verify-image-brief.ps1` | [3] −1/+1 | 검사 문자열 `지훈`→`김남주` | **SKIP** — 위와 한 쌍 |
| 3 | `index.html` | [6] −2/+2 | Bedrock 메뉴 `새 영상 만들기`+`BEDROCK` → `영상 만들기`+`BETA`, `id="navStudio"` 추가 | **APPLY** — L-29. GPU 메뉴가 아니다 |
| 4 | `index.html` | [7] −1/+1 | GPU 메뉴 버튼에 `id="navVideoNew"` 추가 (텍스트 `자체 GPU Wan 2.2` 그대로) | **APPLY** — id만 추가. `gpuVideo:true`이므로 계속 보인다 |
| 5 | `index.html` | [10] −14/+112 | `handoffPanel`(숏폼·콘티·롱테이크 카드 3개) 제거 + 신규 `storyPanel` 112줄 추가 | **분할: +112 APPLY / −14 SKIP** — 두 패널 공존 |
| 6 | `index.html` | [11] −4/+13 | 스튜디오 헤더 `BEDROCK · LUMA RAY2`→`BEDROCK · BETA` + 베타 안내 상자. 롱테이크 언급 문장 삭제 | **분할: 베타 배너 APPLY / 롱테이크 문장 삭제 SKIP** — `longForm:true` |
| 7 | `index.html` | [13] −9/+14 | 롱테이크 묶음을 `<div id="longTakeBlock">`으로 감쌈 (마크업 동일, 재들여쓰기) | **APPLY** — 삭제가 아니라 래퍼 추가. Overlay 주석도 "마크업과 배선은 남긴다"고 명시 |
| 8 | `index.html` | [15] −1/+1 | placeholder 인물명 `무현`→`여주` | **APPLY** — 무해한 문구 |
| 9 | `index.html` | [16] −26/+7 | 진입점 4종 선택 그리드 제거 + `projectStory` `maxlength` **8000→300**, 라벨 `스토리`→`요약 스토리` | **전체 SKIP** — §4.4 |
| 10 | `server.py` | [8] −9/+2156 | 신규 스토리 라우트 2156줄 추가. 삭제 9줄은 `account_guard.verify()` · `bootstrap_if_needed()` · Wan adopt 주석 · `if __name__ == "__main__"` | **APPLY (+2156)**. −9는 **삭제가 아니라 모듈 말미가 뒤로 밀린 것**이므로 병합본에도 말미로 유지 |

### 4.3 hunk 10번(`server.py` [8]) 주의

`git diff --no-index`가 삭제로 표시하지만 심볼 존재 확인 결과 `account_guard.verify()` · `graph_store.bootstrap_if_needed` · `if __name__ ==` · `WAN_ADOPT_AFTER_SECONDS` 가 Overlay에도 **전부 존재한다.** 함수 단위 병합(§7.3)으로 처리하면 자연히 해결되고, 패치 적용 방식으로 하면 모듈 말미가 사라지므로 **패치를 쓰지 않는다.**

### 4.4 hunk 9번(`index.html` [16]) 판정 근거

Overlay는 세 가지를 함께 바꿨다.

1. 진입점 4종(웹툰 원작 / 소재·로그라인 / 트리트먼트 / 논의된 대본) 선택 그리드 **삭제** → L-17. REMOVE이므로 **SKIP** (`legacyInputType: true`)
2. `projectStory` `maxlength` **8000 → 300**, `rows` 5→4 → **SKIP.** Base는 8000자를 받는다. 300으로 줄이면 기존 사용자의 긴 원문이 잘린다
3. 라벨 `스토리` → `요약 스토리 <선택>` + 새 힌트 → **SKIP.** Base 라벨 유지

Overlay 주석이 남긴 사실: "**inputType 은 데이터 모델에 남아 있고 createProject 가 webtoon 으로 채운다.**" 즉 Overlay도 데이터 모델은 지우지 않았다. UI만 숨긴 것이므로 Base UI를 살려도 데이터 정합성 문제가 없다.

**공존이 성립하는 이유**: 요약/실제 스토리 입력은 hunk [10]의 신규 `storyPanel`(프로젝트 상세)에서 하고, 모달의 8000자 칸은 Base 그대로 남는다. Overlay의 `create_project`가 같은 글을 `story`와 `summaryStory` 양쪽에 넣으므로 두 경로가 충돌하지 않는다.

---

## 5. Feature flag 처리 (D-14 확정 반영)

`app.js` 말미의 `FEATURES` 상수와 `applyFeatureFlags()` **구조는 APPLY**한다. 모든 이벤트 배선이 끝난 뒤 실행되므로 요소가 사라져 배선이 끊기는 사고를 막는 안전장치다.

**기본값은 Overlay와 다르게 둔다.**

| 플래그 | Overlay | **병합본** | 근거 |
|---|---|---|---|
| `gpuVideo` | `false` | **`true`** | D-14 GPU 유지. Base에 `/api/v2/video/*`와 `wan_client`가 살아 있다 |
| `longForm` | `false` | **`true`** | Base `stitcher.py` 롱테이크 경로 유지 (H2) |
| `legacyInputType` | `false` | **`true`** | 진입점 4종 유지 (§4.4) |

값이 빌드 타임 상수라 이미지 재빌드가 필요하다. `config.env`로 주입하도록 바꾸는 것은 범위 확대이므로 하지 않는다.

**패널 순서**: `applyFeatureFlags()`가 신규 구획(스토리·결과물)을 옛 패널(원본 자료·기획 방향·AI 작업) **위로** 올리고 옛 패널에 "이전 방식" 안내 줄을 붙인다(L-16). 이 동작은 그대로 APPLY한다 — 두 방식이 한 화면에 공존한다.

---

## 6. 환경 종속 값 치환 (H7 · A9)

`APPLY + 치환` 8건에 포함된 값들이다.

| 검출 대상 | 위치 | 치환 방식 |
|---|---|---|
| `726990466389` | 병합 파일 전수 | `config.env`의 `AWS_ACCOUNT_ID` |
| `story-ai-*` (버킷·역할·클러스터) | 동일 | `config.env` 해당 키 |
| `story-ai-*.elb.amazonaws.com` | `nginx.conf` CSP `img-src` | Hub 버킷 호스트로 치환 (L-21) |
| `arn:aws:*` 하드코딩 | 동일 | 변수화 |
| 브랜드 문자열 `이매지너스` / `AI POC 프로젝트` | `index.html` `app.js` | **신규 키** `BRAND_NAME` `BRAND_SUBTITLE` `APP_TITLE` → `render.py` 주입 |
| 자산 경로 | `index.html` `styles.css` | 경로 규약만 유지, 실물은 Base 기본값 |

**값을 바꾸지 않는 것**: 백엔드 `SERVICE_NAME = "toon-change-ai-pj-app"` (L-50). `/api/health`의 `service`, 로거 이름, S3 오브젝트 메타데이터에 함께 쓰여 바꾸면 기존 로그 검색과 메타데이터가 갈린다. **변수화만 하고 값은 유지.**

---

## 7. 적용 순서 (게이트 1-A 승인 후)

```
7.1  git checkout -b merge/imaginus-overlay
7.2  항목② LLM      — roles/* 6 + story_router/story_context/story_models/prompts_story COPY
                       model_registry.py(+184) · prompt_layers.py(+123) · gateway.py(+10) MERGE
7.3  항목③ 프로젝트  — pdf_export.py COPY · graph_store.py(+822) MERGE
                       server.py 신규 라우트 20여 개 MERGE (함수 단위)
7.4  항목① 로그인    — server.py POST /api/auth/password
                       graph_store.py change_password() / _config_password_rotated()
7.5  프론트엔드      — app.js(+1877/-6) · index.html · styles.css · nginx.conf
                       §4.2 판정과 §5 flag 기본값 적용
7.6  부속            — requirements.txt 합집합 · verify/*.ps1 · docs(_overlay 접미사)
7.7  환경값 치환      — §6
7.8  검증            — §8
7.9  커밋 + AI-POC_history.md
```

각 단계마다 `python -m compileall -q app/src`로 문법을 확인하며 진행한다.

---

## 8. 검증 계획 (Phase 1.5)

| # | 항목 | 명령 | 통과 기준 |
|---|---|---|---|
| 1 | Python 컴파일 | `python -m compileall -q AI-POC/app/src` | exit 0 |
| 2 | 렌더 완전성 | `python AI-POC/deploy/render.py --check` | 미해결 변수 0 |
| 3 | K8s dry-run | `kubectl apply --dry-run=client -f AI-POC/deploy/rendered/` | 오류 0 |
| 4 | **삭제 0 (A2)** | `deploy/verify/verify-merge-nodelete.ps1` (신규 작성) | 파일 79 ⊆ / Python 심볼 666 ⊆ / K8s 리소스 ⊆ |
| 5 | **Protected 무변경 (A3)** | `git diff baseline -- <§3 경로>` | 빈 출력 |
| 6 | 라우트 보존 | Base 라우트 55개가 병합본에 전부 존재 | 55/55 |
| 7 | 비밀정보 (A9) | 정규식 `AKIA[0-9A-Z]{16}` · `-----BEGIN` · 하드코딩 12자리 계정 ID | 0건 |
| 8 | Frontend | `npm ci && npm run build` (package.json 있으면) | exit 0 |

Overlay의 `tools/check_imports.py`(정의되지 않은 전역 참조 검출)는 미커밋 WIP라 가져오지 않지만, **같은 검사를 4번 항목에 넣는다** — 함수를 옮기는 작업에서 호출부 누락을 잡는 유일한 방법이다.

---

## 9. NEEDS_DECISION

| ID | 질문 | 권장 |
|---|---|---|
| **M-1** | 병합 원본을 `298717a`로 고정하는 것에 동의하는가. 워킹 트리의 story-dev-beta WIP(모듈 7개·~150KB, 라우트 0개, Neptune 쿼리 미검증)를 제외한다 | **고정** |
| **M-2** | story-dev-beta를 나중에 별도 병합할 것인가 | 그 세션이 완성·커밋한 뒤 2차 병합 |
| **M-3** | `.kiro/specs/story-dev-beta/` 3종 + 계획서를 `AI-POC/docs/story-dev-beta_overlay/`로 가져올까 | **가져온다** (문서라 위험 없고 2차 병합의 설계 원본) |
| **M-4** | D-09 확정 — 고객사 자산 12개를 복사하지 않고 구조만 반영 | **구조만** |
| **M-5** | `projectStory` `maxlength` 8000 유지 (§4.4) | **유지** |
| **M-6** | `verify-image-brief.ps1`의 픽스처 인물명 변경 SKIP (A3 유지) | **SKIP** |

L-62(`http_guards.py` 도입)는 `298717a`에 그 파일이 없으므로 **쟁점 해소**. 세션 인수인계 문서의 §7.6은 더 이상 결정 대상이 아니다.

---

## 10. 요약

- 병합 원본을 **불변 커밋 `298717a`**로 고정해 움직이는 대상 문제와 미완성 WIP를 동시에 제거했다
- 파일: NEW 31 COPY(자산 12는 구조만) / MODIFIED 19 MERGE / BASE_ONLY 1 KEEP
- 삭제 hunk 59건 전수 판정: APPLY 46 / SKIP 13
- **Base 파일 삭제 0 · Base 라우트 삭제 0 · Protected 파일 3개는 이미 IDENTICAL**
- flag는 `gpuVideo`·`longForm`·`legacyInputType` 전부 **`true`** — Base 기능을 하나도 끄지 않는다
