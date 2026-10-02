# vpoc-regen 조사 (K-1)

작업 트리 `C:\dev\ai\.worktrees\vpoc-regen` · 브랜치 `whynot/vpoc-regen` · 기준 `origin/main` = `98fadf0`
정본: `docs/vpoc1/regen-plan.md` + `docs/vpoc1/PRD.md`

줄 번호는 이 커밋 기준이다. `routes.py` 의 함수는 대부분 `create_blueprint()` 안의 클로저다 —
`deps` 와 `g` 에 의존하는 것을 주입으로 받기 때문이다. 그래서 새 모듈에서 부를 때는 **호출
가능한 것을 인자로 받아야 한다.**

---

## 3-a. `run/<action>` 분기와 재사용할 제출 함수

| 대상 | 위치 | 비고 |
|---|---|---|
| 라우트 | `routes.py:1297` `@bp.post("/p/<pid>/plans/<plan_id>/run/<action>")` → `def run()` `:1300` | 액션 분기는 `if action == "…"` 연쇄. `analyze` 1308 · `prepare` 1330 · `redraw` 1346 · `generate` 1360 · `retry` 1422 · `stitch` 1455 · 알 수 없는 액션 1458 |
| 선행 검사 공통 | `run()` 첫머리 — `project_for(pid, write=True)` · `owner_of(project)` · `deps.current_username()` · `store.load_state(...)` · `require_idle(state)` | `require_idle` `:107` 은 `task.status == "running"` 이면 409 `TaskRunning` |
| 샷 제출 | **`submit_shot(state, owner, pid, plan_id, user, shot)`** `routes.py:573` | 클로저. `state["steps"]["3"]` 에서 같은 id 의 plan_shot 을 찾아 `plan_shot["promptEn"]` 을 제출한다. 제출 결과를 **`shot` 딕셔너리에 직접 쓴다**(`status` `mode` `invocationArn` `outputPrefix` `submittedAt` `promptChars` `note` `error` `lastPolledAt`). 원장 검사·기록과 jobs 파일 쓰기까지 포함 |
| 키프레임 판정 | `submit_shot` 안 `:592`~`:610` | `shot["mode"] == "keyframe"` **그리고** `plan_shot["useKeyframe"]` 일 때만. 인물 1명 + `step3.characters[].image.source == "ai"` + `image.key` 존재. `images.keyframe_base64(key)` `images.py` |
| 제출 실패 | `:622` `video.Throttled` → `status="queued"`(E4, 과금 없음) / `:628` 그 외 → `status="failed"` + 오류 봉투 | |
| retry 구현 | `routes.py:1422`~`1454` | `mutator` 안에서 샷을 찾아 `status="queued"` · `attempt += 1` · `mode="text"` · `invocationArn=""` · `outputKey=""` 로 초기화하고 `state.pop("finalizedAt")` 뒤 `refresh_shots(...)` 를 부른다 |

**적용**: `submit_shot` 에 **키워드 전용 인자 3개를 더한다** — `target`(제출 결과를 쓸 딕셔너리,
기본값 `shot`) · `prompt_en`(프롬프트 덮어쓰기) · `want_keyframe`(키프레임 여부 덮어쓰기).
기존 호출부(`:745`)는 인자를 안 주므로 동작이 그대로다(G1).

## 3-b. 샷 상태 필드와 PlanView 직렬화

| 대상 | 위치 | 실제 필드 |
|---|---|---|
| ④ 샷 직렬화 | **`_shot4_view(shot)`** `routes.py:135` | 응답 키: `id` `status` `attempt` `mode` `submittedAt` `completedAt` `elapsedS` `videoUrl` `note` `error`. `videoUrl` 은 `shot["outputKey"]` 로 **조회 때마다 새로 서명**한다(`video.video_url`) |
| state 내부 전용 | `submit_shot` · `refresh_shots` | `invocationArn` `outputPrefix` `outputKey` `outputBytes` `lastPolledAt` `provider` `promptChars` — 응답에 나가지 않는다 |
| ④ 전체 | `plan_view()` `routes.py:160` 안 `step4` 조립 `:206` | `status` `shots` `final{status,url,error}`. `final.key` 는 응답에서 제거한다 |
| 계획 샷(③) | `plan_view()` `:181` | `id` `durationS` `summaryKo` `cameraKo` `characters` `promptEn` `promptChars` `useKeyframe` `reviewKo`. state 에는 `cameraEn` `locationEn` `actionEn` 도 있다(응답에는 없다) |

**적용**: `_shot4_view` 에 `selectedAttempt` `versions` `revision` `regen` 네 키만 더한다.
기존 10개 키의 이름·의미는 건드리지 않는다(G2).

## 3-c. attempt 번호 · jobs 키 · clientRequestToken

| 대상 | 위치 |
|---|---|
| 최초 attempt | `run()` generate 분기 `:1391` — 샷마다 `"attempt": 1` |
| 증가 | retry `:1441` 와 E5 자동 재제출 `:727` 에서 `+1` |
| jobs 파일 | `store.job_key(owner, pid, plan_id, shot_id, attempt)` `store.py:74` → `…/plans/{planId}/jobs/{shotId}-{attempt}.json`. 쓰는 곳은 `submit_shot` `:652` |
| 출력 접두사 | `store.video_output_prefix(...)` `store.py:106` → `…/plans/{planId}/shots/{shotId}-{attempt}` |
| 멱등 키 | **`video.client_token(plan_id, shot_id, attempt)`** `video.py:79` — `{planId}-{shotId}-{attempt}` 를 만든 뒤 영숫자 아닌 것을 `-` 로 바꾼다(2026-09-23 결함 수정). `video.submit` 이 부른다 |

**적용**: regen 의 새 attempt = 그 샷의 **기존 최대 attempt + 1**(`shot.attempt` 와 `versions[].attempt`
중 최대). 키 규칙을 그대로 쓰므로 jobs·출력·멱등 키가 자동으로 분리된다.

## 3-d. 조회에서 GetAsyncInvoke 를 부르는 곳

| 대상 | 위치 |
|---|---|
| 조회 본체 | **`refresh_shots(state, owner, pid, plan_id, user)`** `routes.py:671` |
| 10초 제한 | `:681`~`:683` — `shot["lastPolledAt"]` 로부터 `settings.POLL_MIN_INTERVAL_S`(10초) 안이면 건너뛴다 |
| 완료 처리 | `:692` `video.find_output_mp4(outputPrefix)` → 없으면 `OutputMissing` |
| 실패 처리 | `:712` `AsyncInvokeFailed` 봉투 |
| E5 자동 재제출 | `:722`~`:731` — `shot["mode"] == "keyframe"` 이면 `queued` + `mode="text"` + `attempt+1` + `note="키프레임이 막혀 텍스트로 다시 만듦"` |
| 대기 제출 | `:735`~`:745` — `inflight` 를 세고 `settings.MAX_INFLIGHT`(실측 1) 미만일 때만 `submit_shot` |
| ④ 상태 집계 | `:747`~`:753` — `shot["status"]` 만 본다 |
| 완료 처리 호출 | `:756` `finish_plan(...)` |
| 호출 지점 | `get_plan()` `routes.py:1080` 안 `:1089` (편집 권한 + 샷이 있을 때만) |

**적용**: 폴링 본문을 `poll_target(target, shot_id)` 로 **추출**하고, 샷마다
`[shot] + ([shot["regen"]] if regen 있으면)` 을 돌린다. 대기 제출 루프도 같은 대상 목록을 쓴다.
④ 상태 집계는 `shot["status"]` 만 보므로 손대지 않는다 → regen 진행이 ④ 상태를 바꾸지 않는다(G2).

## 3-e. 완료 처리

| 대상 | 위치 | 내용 |
|---|---|---|
| `finish_plan(state, owner, pid, plan_id)` | `routes.py:759` | `state["finalizedAt"]` 로 한 번만. `store.final_key(...)` 에 `create_only=True`(If-None-Match) 로 `final.json` → `store.project_key(...)` 에 `project.json` → `update_graph(pid, "done", plan_id, "", thumb_key)` |
| `final.json` 내용 | `:768`~`:829` | `planId` `projectId` `owner` `completedAt` `model` `provider` `params` `style` `settingEn` `costUsd` `shots[]` `characters` `characterImages` `input` |
| Neptune | `update_graph(...)` `routes.py:875` | `vpocStatus` `vpocPlanId` `vpocFinalKey` `vpocThumbKey` `vpocUpdatedAt` 다섯 속성만 SET |

**적용**: `final.json` 은 손대지 않는다(G4). `select` 가 `plans/{planId}/finals/{UTC}.json` 을 새로
쓰고 `update_graph` 로 `vpocFinalKey`·`vpocUpdatedAt` 을 갱신한다. `store.py` 에 키 함수 2개를 더한다.

## 3-f. 프로젝트 보기의 영상 목록 출처

`project_view()` `routes.py:901`, 영상 목록은 `:911`~`:945`.

계획 목록을 `store.plans_prefix` 로 나열하고, 계획마다 **`store.final_key(...)` 의 `final.json` 을
읽어** `record["shots"]` 중 `status == "completed"` 인 것의 `outputKey` 로 URL 을 만든다.
`state.json` 을 쓰지 않는다.

> **불일치.** 계획 문서 5.3 은 "`finals/` 최신 → 없으면 `final.json`" 이다. 현재는 `final.json` 만
> 본다. **적용할 방법**: `project_view` 의 기록 로딩을 `regen.latest_final(owner, pid, plan_id)` 로
> 바꾼다 — `finals/` 를 나열해 최신을 고르고 없으면 `final.json` 을 돌려준다. 호출 한 줄 교체이고
> 응답 키는 그대로다.

## 3-g. 합본(stitch)

| 대상 | 위치 | 내용 |
|---|---|---|
| `stitch(owner, pid, plan_id)` | `routes.py:1460` | **있다**(R10 미적용). `state["steps"]["4"].shots` 에서 `status == "completed"` 이고 `outputKey` 가 있는 것을 순서대로 임시 디렉터리에 내려받아 `stitcher.concat` 으로 잇고 `store.final_mp4_key` 에 올린다. 결과를 `steps["4"].final = {"status":"ready","key":…}` 에 쓴다 |
| 액션 | `run()` `:1455` → `stitch(...)` | |

**적용**: `stitch` 는 `shot["outputKey"]` 를 쓰고 `select` 가 그 값을 선택 버전으로 갱신하므로
**합본 코드는 고치지 않아도 선택 버전을 쓴다.** `select` 가 `final.status` 를 `"stale"` 로 바꾸고
화면이 [다시 이어 붙이기]를 보이면 된다.

## 3-h. 키프레임 제출 기능과 E5

**있다**(R11 미적용). `submit_shot` `:592`~`:610` + `images.keyframe_base64` + E5 `refresh_shots:722`.
다만 현재 ③ 계획은 `useKeyframes` 를 항상 false 로 만들고(`do_prepare` `:517`) 화면에도 켜는 곳이
없다. 즉 **경로는 구현돼 있고 쓰이지 않는 상태**다. regen 의 체크박스가 이 경로의 첫 실사용이 된다.

키프레임 가능 조건(화면이 체크박스를 보일 조건): 샷 인물 정확히 1명 · 그 캐릭터의
`steps["3"].characters[].image.source == "ai"` · 이미지가 있다.

## 3-i. 재사용할 나머지 부품

| 용도 | 함수 | 위치 |
|---|---|---|
| 원장 상한 검사 | `ledger.ensure_room(usd)` | `ledger.py:44` |
| 원장 기록(영상) | `ledger.charge_video(token, *, user, project_id, plan_id)` | `ledger.py:111` |
| 원장 기록(텍스트) | `ledger.charge_text(key, *, input_tokens, output_tokens, user, project_id, plan_id)` | `ledger.py:141` |
| 원장 요약 | `ledger.summary()` | `ledger.py:39` |
| 샷당 금액 | `settings.shot_usd()` | `settings.py` 맨 끝 |
| 프롬프트 조립 | `compose.build_prompt(shot, characters, style)` | `compose.py:68` |
| 샷 검사(길이·한글·이름·부정어·빈 조각) | `compose.check_shot(shot, characters, style)` | `compose.py:104` |
| 화풍 정규화 | `compose.style_of(value)` | `compose.py:45` |
| 상한 상수 | `compose.MAX_PROMPT_CHARS`(1200) | `compose.py:38` |
| Claude 도구 강제 호출 | **`llm._converse(*, system_text, blocks, tool, max_tokens, where)`** → `(payload, usage)` | `llm.py:212` |
| 위반 재요청 블록 | `llm._retry_block(problems)` | `llm.py:259` |
| 제출 | `video.submit(...)` · `video.poll(arn)` · `video.find_output_mp4(prefix)` · `video.video_url(key)` | `video.py` |
| 상태 읽기·쓰기 | `store.load_state` · `store.save_state` · `store.mutate_state(owner,pid,plan_id,mutator)` | `store.py:312`·`319`·`324` |
| 작업 스레드 | **`run_task(name, owner, pid, plan_id, worker)`** + `apply_result(state, name, result)` | `routes.py:254`·`310` |
| 오류 봉투 | `envelope(where, exc)` · `VpocError(where, kind, message, status)` | `errors.py` |
| 지연 판정 | `stall_check(state)` | `routes.py:344` |

`llm._converse` 와 `llm._retry_block` 은 밑줄로 시작한다. 같은 패키지 안에서 부르므로 그대로 쓰고,
`regen.py` 에 그 의도를 주석으로 적는다(공개 API 로 바꾸는 것은 기존 파일 변경을 늘린다).

## 3-j. 프론트엔드 ④ 화면

| 대상 | 위치 |
|---|---|
| ④ 렌더 | **`renderStep4(host)`** `vpoc.js:877` — 합본/순차 재생 → 샷 카드 목록 → 완료 안내 |
| 샷 카드 | `renderStep4` 안 `:919`~`:973` — `vpoc-shot` > `vpoc-shot-no` + `vpoc-shot-body`(요약·상태 칩·note·키프레임 배지·영상·오류·다시 시도) |
| 영상 태그 | `videoTag(url, small)` `:978` |
| 순차 재생 | `sequencePlayer(shots)` `:987` — `videoUrl` 이 있는 완료 샷만 |
| 오류 상자 | `errorBox(error, retry)` `:220` — 원문 + [복사] [다시 시도] |
| 확인 창 | `confirmModal(title, lines, okLabel, onOk)` `:254` |
| 액션 호출 | `runAction(action, body)` `:1175` — `run/<action>` POST 뒤 `loadPlan()` |
| 조회 주기 | `schedule()` `:1307` — `task.status=="running"` 2초 / `steps["4"].status=="running"` 5초 / 그 외 멈춤 |
| 렌더 진입 | `refresh()` `:1268` |
| 픽스처 | `MOCK_PLAN` `:1423` |

> **불일치.** `schedule()` 은 `steps["4"].status` 만 본다. regen 은 ④ 상태를 바꾸지 않으므로(G2)
> 재생성 중에 조회가 멈춘다. **적용할 방법**: `schedule()` 의 조건에 "어떤 샷의 `regen.status` 가
> `queued`/`submitted`" 를 OR 로 더한다(한 줄). 기존 두 조건은 그대로 둔다.

## 3-k. 원장 현재 상태

`users/_vpoc1/ledger.json` 을 읽기만 했다.

| 항목 | 값 |
|---|---|
| 상한 | 60 USD |
| 누계 | **38.41 USD** |
| 여유 | 21.59 USD — 검증용 3.75 USD **충분하다** |

내역(항목 26개, 요약):

| 종류 | 건수 | 금액 |
|---|---|---|
| 사전 점검 2건 (`gate0-preflight-20260923` · `tokencheck-20260923`) | 2 | 7.58 |
| 영상 제출 (`pl-…-shot-…-1`) | 8 | 30.00 |
| 캐릭터 이미지 (SD3.5) | 8 | 0.64 |
| Claude (analyze · plan) | 8 | 0.19 |

영상 8건이 `pl-20260922-0042-shot-01-1` 같은 **하이픈 토큰**으로 계상돼 있다 —
2026-09-23 의 `clientRequestToken` 수정이 실제로 동작한 증거다. 계획 두 개(`0042` 4샷,
`a2f2` 4샷)가 생성까지 갔다. 이 계획들이 regen 검증 대상이 된다.

---

## 4. 계획 문서와 실제 코드의 불일치 요약

| # | 불일치 | 적용할 방법 (G1~G7 안에서) |
|---|---|---|
| D1 | 계획 문서는 `versions[]` 를 "조회 때 jobs 파일에서 만들어 보여 준다"고 했다. jobs 파일을 매 조회마다 나열하면 S3 호출이 샷 수만큼 늘고, 지난 버전의 의견·요약이 jobs 파일에 없다 | `versions` 를 **state 에 보관**한다. 첫 regen 때 현재(재생성 전) 버전을 `versions` 에 심고 새 버전을 덧붙인다. `generate`·`retry` 는 손대지 않는다(G1) — `versions` 가 비어 있으면 조회 때 현재 샷 필드로 1개를 만들어 보여 준다(R9) |
| D2 | 프로젝트 보기가 `final.json` 만 읽는다 (3-f) | `regen.latest_final()` 로 `finals/` 최신 → `final.json` 순으로 읽는다. 호출 한 줄 교체 |
| D3 | 화면 조회 주기가 ④ 상태만 본다 (3-j) | `schedule()` 조건에 regen 진행 중을 OR 로 더한다 |
| D4 | 계획 문서 5.1 은 `purpose: vpoc.revise` 를 적었다. vpoc 는 `prompt_layers` purpose 를 등록하지 않고 boto3 로 직접 부른다(PRD E12) | purpose 를 등록하지 않는다. `llm._converse(where="claude.revise")` 로 부르고 로그·원장에 그 이름을 쓴다 |
| D5 | 계획 문서는 `revise` 선행 조건에 "계획에 다른 작업 없음"을 넣었다. 기존 `require_idle` 이 이미 그 검사다 | `require_idle(state)` 를 그대로 쓴다 |
| D6 | `task` 에 `shotId` 를 넣으라고 했다. 기존 `task` 는 `name` `status` `startedAt` `finishedAt` `error` 다섯 키다 | `task.shotId` 를 더한다. PlanView 의 `task` 직렬화(`plan_view` `:190`)에 키 하나를 추가하는 것이라 기존 키는 그대로다 |
| D7 | R11(키프레임 없음)·R10(합본 없음)은 해당 없다 | 둘 다 구현돼 있다(3-g·3-h) |

## 5. 고치는 파일

| 파일 | 변경 |
|---|---|
| `app/src/vpoc/regen.py` | **신규** — 도구 스키마·시스템 프롬프트·revise 작업·regenerate·select·버전 목록·finals 기록·폴링 대상 |
| `app/src/vpoc/routes.py` | 액션 분기 3개 추가 · `submit_shot` 에 키워드 인자 3개 · 폴링 본문을 `poll_target` 으로 추출하고 대상에 regen 포함 · `_shot4_view` 에 키 4개 · `task` 에 `shotId` · `project_view` 의 기록 로딩 한 줄 · `apply_result` 에 revise 분기 |
| `app/src/vpoc/store.py` | `finals_prefix` · `finals_key` 두 함수 추가 |
| `app/src/vpoc/settings.py` | `REGEN_ENABLED` 추가 |
| `deploy/config.env.example` · `config.prod.env` · `k8s/workloads.yaml.tmpl` | `VPOC_REGEN_ENABLED` |
| `frontend/public/vpoc.js` · `vpoc.css` | 샷 카드에 요소 추가(`vpocRegen*` 함수, `vpoc-regen-*` 클래스) |
| `tools/check_vpoc.py` | 자체 검사 4건 추가 |
| `docs/vpoc1/api-check.md` | 새 필드를 "추가" 절에 적는다 |

플랫폼 파일(`gateway.py` `graph_store.py` `prompt_layers.py` `model_registry.py` `stitcher.py`)과
다른 고객 디렉터리는 건드리지 않는다.
