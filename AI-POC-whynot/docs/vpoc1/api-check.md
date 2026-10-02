# vpoc1 API 계약 대조

정본: `PRD.md` 7.1~7.5. 아래는 **코드에서 확인한 것**과 **dev 에서 확인해야 하는 것**을 나눈다.

## 1. 라우트 등록 (로컬 임포트 스모크)

`check_vpoc.py` 의 임포트 스모크가 실제 Flask `url_map` 을 읽어 확인한다.

```
GET    /api/vpoc/p/<pid>
POST   /api/vpoc/p/<pid>/plans
GET    /api/vpoc/p/<pid>/plans/<plan_id>
POST   /api/vpoc/p/<pid>/uploads
PUT    /api/vpoc/p/<pid>/plans/<plan_id>/steps/<int:number>
POST   /api/vpoc/p/<pid>/plans/<plan_id>/run/<action>
POST   /api/vpoc/p/<pid>/plans/<plan_id>/back
POST   /api/vpoc/smoke
GET    /api/vpoc/smoke/<smoke_id>
```

PRD 7.1 의 8행과 일치한다(8번 행이 두 라우트다). 전체 라우트 수 71개.

`VIDEO_GEN_ENABLED=false` 이거나 `VPOC_ENABLED=false` 면 블루프린트를 등록하지 않는다 —
`server.py` 의 `if VIDEO_GEN_ENABLED and vpoc.settings.VPOC_ENABLED:`. 그 배포에서는 404 다.

## 2. 응답 키 — 코드 대조

PlanView 는 `routes.plan_view()` 가, ProjectView 는 `project_view()` 가 만든다.
PRD 7.3 · 7.4 예시와 키 이름을 글자까지 맞췄다.

| 봉투 | 키 |
|---|---|
| PlanView 최상위 | `ok` `planId` `projectId` `canEdit` `currentStep` `maxReachedStep` `budget` `task` `steps` |
| `budget` | `spentUsd` `capUsd`(항상 null — 상한 없음, 2026-09-23) |
| `task` | `name` `status` `startedAt` `finishedAt` `error` |
| `steps["1"]` | `status` `text` `characters[{name,description,files[{fileId,kind,name,url,thumbUrl}]}]` `sceneFiles[...]` |
| `steps["2"]` | `status` `story{titleKo,loglineKo,genre,settingKo,settingEn,style,styleSuggestion,notesKo}` `characters[...]` `questions[{id,questionKo,optionsKo,defaultIndex,whyKo}]` `answers{qid:{index,text}}` `extraKo` |
| `steps["2"].characters[]` | `id` `nameKo` `nameEn` `source` `roleKo` `blPosition` `appearanceKo` `appearanceEn` `handleEn` `bodyKo` `bodyEn` `personalityKo` `traitsKo` `likesKo` `aiFilled` |
| `steps["3"]` | `status` `useKeyframes` `characters[{id,image{status,source,imageId,url,seed,error}}]` `shots[...]` `cost{shots,durationS,resolution,perShotUsd,totalUsd}` |
| `steps["3"].shots[]` | `id` `durationS` `summaryKo` `cameraKo` `characters` `promptEn` `promptChars` `useKeyframe` `reviewKo` |
| `steps["4"]` | `status` `shots[...]` `final{status,url,error}` |
| `steps["4"].shots[]` | `id` `status` `attempt` `mode` `submittedAt` `completedAt` `elapsedS` `videoUrl` `note` `error` |
| 오류 봉투 | `where` `type` `message` `requestId` `httpStatus` `at` |
| ProjectView | `ok` `projectId` `title` `visibility` `canEdit` `activePlanId` `videos` `characters{status,items}` `inputs` `prompts` |

### 추가 (vpoc-regen, 2026-09-23)

"이 샷만 의견 반영해 다시 만들기"가 **기존 키를 바꾸지 않고** 아래를 더했다.
`check_vpoc.py` 의 `check_planview_keys` 가 기존 10개 키 유지와 새 4개 키 존재를 CI 에서 강제한다.

| 봉투 | 추가 키 |
|---|---|
| PlanView 최상위 | `regenEnabled`(bool) — 화면이 버튼을 보일지 판단한다 |
| `task` | `shotId` — `revise` 가 어느 샷 작업인지. 다른 작업에서는 null |
| `steps["4"]` | `genId` — 생성 회차. ③을 고쳐 다시 승인하면 새 값이 된다 |
| `steps["4"].shots[]` | `selectedAttempt`(int) · `versions[]` · `revision` · `regen` |
| `versions[]` | `attempt` `genId` `origin`(plan\|regen) `feedbackKo` `summaryKo` `cameraKo` `promptEn` `mode` `status` `videoUrl` `createdAt` |
| `revision` | `id` `feedbackKo` `summaryKo` `cameraKo` `characters` `promptEn` `promptChars` `useKeyframe` `notesKo` `reviewKo` `createdAt` — 없으면 null |
| `regen` | `attempt` `status` `mode` `feedbackKo` `summaryKo` `cameraKo` `submittedAt` `completedAt` `videoUrl` `note` `error` — 없으면 null |

새 액션 3개는 기존 `POST …/plans/<planId>/run/<action>` 에 붙는다.

| 액션 | body | 응답 |
|---|---|---|
| `revise` | `{shotId, feedbackKo, useKeyframe}` | 202 `{"ok":true,"task":"revise"}` |
| `regenerate` | `{shotId, revisionId, promptEn?, confirmUsd}` | 200 `{"ok":true,"attempt":2,"status":"submitted"}` |
| `select` | `{shotId, attempt}` | PlanView |

추가 오류 코드: 409 `RegenRunning` · `RevisionMismatch`, 400 `FeedbackEmpty` · `FeedbackTooLong` ·
`PromptRejected`. `VPOC_REGEN_ENABLED=false` 면 세 액션 모두 404 `NotFound`.

`steps["4"].shots[].status`·`videoUrl`·`attempt` 와 `steps["4"].status` 의 **의미는 그대로다** —
항상 지금 쓰는(선택된) 버전을 뜻하고, 재생성 진행 상태는 `regen` 에만 있다.

내부 전용 키는 응답에서 제거한다 — `steps["3"].characters[].image.key`,
`steps["4"].final.key`, 샷의 `invocationArn`·`outputPrefix`·`outputKey`·`lastPolledAt` 은
`state.json` 과 `jobs/*.json` 에만 있고 화면에는 나가지 않는다. URL 은 조회 때마다 새로 서명한다.

## 3. 상태 코드 — 코드 대조

| 코드 | 내는 곳 |
|---|---|
| 400 | `BadKind` `MissingFile` `BadStep` `BadAction` `BadRequest` `EmptyPrompt` `PromptOverBudget` `UnreadableImage` |
| 401 | `require_auth`(플랫폼) |
| 403 | `Forbidden` — 가시성 없음 / 소유자 아님(D-L12) |
| 404 | `NotFound` — 프로젝트·계획·샷·캐릭터 없음, 테스트 경로 꺼짐 |
| 409 | `TaskRunning` `PreconditionFailed` `CostMismatch` |
| 413 | `FileTooLarge` |
| 502 | `ImageFiltered` `SchemaViolation` `AsyncInvokeFailed` `PlanMissing` 및 AWS 오류 |

`errors.status_for()` 가 봉투의 `httpStatus` 를 응답 코드로 옮긴다. AWS 가 200 을 주고도
논리적으로 실패한 경우(`AsyncInvokeFailed`)는 502 로 나간다.

## 4. dev 에서 확인해야 하는 것

로컬에서는 Neptune·S3·Bedrock 에 붙지 않으므로 아래는 **배포 후** 확인한다.

| # | 확인 | 방법 |
|---|---|---|
| 1 | `/api/vpoc/smoke` 가 영상 1건을 만들고 재생된다 | `vpoc-smoke.html` (Gate 1) |
| 2 | `/api/vpoc/smoke` 에 `{"errorTest": true}` 를 보내면 AWS 원문 오류가 그대로 온다(과금 없음) | 같은 페이지 두 번째 버튼 |
| 3 | 계획 생성 → ① 저장 → analyze → ② 저장 → prepare → ③ ready | `vpoc_e2e.py` 또는 브라우저 |
| 4 | generate → 4샷 completed → presigned GET 200/206, Content-Type `video/mp4` | `vpoc_e2e.py` (Gate 2) |
| 5 | 프로젝트 보기 3구역 순서 | 브라우저 (부록 E 8) |
| 6 | 원장 합계 = 제출 건수 × 3.75 + 이미지 + 텍스트 | `run-log.md` 대조 |
| 7 | `vpoc.js`·`vpoc.css` 200 이고 `?v=` 가 `ci-<sha>` 로 치환됐다 | `curl -sI https://dev-whynot.meta-clouds.com/vpoc.js` · index.html 확인 |
| 8 | `?vpocMock=1` 없이 픽스처가 꺼져 있다 | 화면이 서버 데이터를 보이는지 |

## 생성 회차 (genId, 2026-09-23)

`steps["4"].genId` 가 생성 회차를 구분한다. `generate` 가 실행될 때마다 `"g" + UTC yyyymmddHHMMSS`
로 새로 만든다. 이 값이 아래 세 곳을 갈라 놓는다.

| 대상 | genId 있을 때 | 옛 데이터(genId 없음) |
|---|---|---|
| `clientRequestToken` | `{planId}-{genId}-{shotId}-{attempt}` | `{planId}-{shotId}-{attempt}` |
| jobs 키 | `plans/{planId}/jobs/{genId}/{shotId}-{attempt}.json` | `plans/{planId}/jobs/{shotId}-{attempt}.json` |
| 출력 접두사 | `plans/{planId}/shots/{genId}/{shotId}-{attempt}/` | `plans/{planId}/shots/{shotId}-{attempt}/` |

재생성(`regenerate`)·`retry`·키프레임 실패 자동 대체는 **현재 genId 아래에서 `attempt` 만** 올린다.
`versions` 는 현재 genId 의 job 만 보여 준다. 옛 데이터는 옛 키 규칙으로 **읽기만** 한다
(마이그레이션 쓰기 없음).

완료 기록은 회차마다 `plans/{planId}/finals/{UTC}.json` 을 새로 쓴다. `final.json` 은 옛 데이터
호환용으로 없을 때만 만들고, 정본은 `finals/` 최신이다. 완료 때 `project.json` 확정본(화풍·캐릭터·
배경)과 Neptune `vpocFinalKey`·`vpocUpdatedAt` 을 **항상** 갱신한다.

근거와 사고 기록: `docs/vpoc1/fix-regen-reload.md`.
