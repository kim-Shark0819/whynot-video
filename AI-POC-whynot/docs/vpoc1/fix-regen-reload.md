# 되돌아가 다시 만든 영상에 이전 영상이 다시 붙는 문제

조사 2026-09-23 · 브랜치 `whynot/vpoc-fix` (기준 `9e6d63c`) · 읽기 전용 조사(`--profile ai-poc-hub`)

증상(사용자 보고): 영상까지 만든 프로젝트(화풍 애니)에서 ④ → 이전 단계로 되돌아가 화풍을
실사로 바꿔 다시 만들었다. ③ 계획까지는 실사로 나오지만 [승인하고 영상 만들기]를 누르면
**새 영상이 아니라 기존 애니 영상이 그대로 다시 로드된다.**

---

## 1. 대상 계획

`pl_20260922_0042` · owner `admin` · pid `231b1c4500d25abda814691f3c781e88`
현재 `steps["2"].story.style = live_action`, ③ ready(샷 3개), ④ done(샷 3개), `finalizedAt` 있음.
`revisions = {"1": 1, "2": 2, "3": 1}` — ②를 두 번 다시 실행했다.

## 2. 실제로 일어난 일

### 1차 생성 (애니)

| 샷 | 토큰 | invocationArn | 제출 | 출력 키 | 영상 생성 시각 |
|---|---|---|---|---|---|
| shot_01 | `pl-20260922-0042-shot-01-1` | `…/dqkdv3uey8u5` | 09-22 23:33:13 | `…/shots/shot_01-1/dqkdv3uey8u5/output.mp4` | 23:34:16 |
| shot_04 | `pl-20260922-0042-shot-04-1` | `…/hnkhmfuh7mu8` | 23:40:47 | `…/shots/shot_04-1/hnkhmfuh7mu8/output.mp4` | 23:41:50 |
| shot_02 | `pl-20260922-0042-shot-02-1` | `…/f1cx70yj100i` | 23:47:00 | `…/shots/shot_02-1/f1cx70yj100i/output.mp4` | 23:48:33 |
| shot_03 | `pl-20260922-0042-shot-03-1` | `…/e7paedyryi5k` | 23:56:02 | `…/shots/shot_03-1/e7paedyryi5k/output.mp4` | 23:57:04 |

23:57:14 `final.json` 기록(**style=anime**), 23:59:34 합본 `final.mp4`,
`project.json`(**style=anime**, updatedAt 23:57:14).

### 되돌아가 ②·③ 다시 실행 (실사)

원장에 00:25:33 캐릭터 이미지 1장, 00:25:37 `plan` 호출이 있다. ③이 실사 프롬프트로 새로 나왔다 —
현재 ③의 `promptEn` 세 개 모두 `Cinematic live-action film footage, photorealistic, …` 로 시작한다.

### 2차 생성 (실사) — **실제로는 아무 일도 일어나지 않았다**

| 증거 | 값 |
|---|---|
| `jobs/*.json` 파일 수정 시각 | shot_04 **00:35:58**, shot_01 **00:36:26**, shot_02 **00:36:28**, shot_03 **00:36:34** — 2차 생성 때 덮어써졌다 |
| 그 jobs 파일의 `invocationArn` | `dqkdv3uey8u5` · `hnkhmfuh7mu8` · `f1cx70yj100i` · `e7paedyryi5k` — **1차와 똑같다** |
| 그 jobs 파일의 `submittedAt` | 00:36:26 등 — 2차 시각인데 arn 은 1차 것이다 |
| 그 jobs 파일의 `prompt` | `Cinematic live-action film footage…` — **실사 프롬프트다.** 프롬프트는 새로 보냈다 |
| `ListAsyncInvokes` (us-west-2) | 가장 최근 Luma 작업이 **23:56:01**. 00:35~00:36 에 새 작업이 **하나도 없다** |
| 원장 영상 항목 | 마지막 영상 과금이 **23:56:02**. 2차 생성의 새 과금이 **없다** |
| 영상 버킷 출력 | `output.mp4` 4개 전부 09-22 23:34~23:57 생성. **새 mp4 가 없다** |
| `state.json` ④ | 샷 3개가 `completed`, `attempt=1`, `invocationArn`·`outputKey` 가 전부 1차 것 |
| `final.json` | `completedAt` 23:57:14, `style=anime` — **1차 기록 그대로** |
| `project.json` | `style=anime`, updatedAt 23:57:14 — **1차 그대로** |

**결론**: 2차 생성은 `StartAsyncInvoke` 를 실사 프롬프트로 불렀지만, `clientRequestToken` 이
1차와 글자까지 같아서 **Bedrock 이 멱등 처리로 1차 invocation 을 그대로 돌려줬다.** 새 작업도,
새 과금도, 새 영상도 없다. 그 다음 조회가 1차 출력 접두사에서 1차 mp4 를 찾아 붙였다.

즉 **중복 과금을 막으려고 넣은 멱등 키가, 회차 개념이 없어서 "다시 만들기"를 막아 버렸다.**

## 3. 가설 판정

### H1 — 새 생성의 shotId·attempt 가 이전과 같아 토큰·jobs 키·출력 접두사가 겹친다 → **참 (근본 원인)**

| 근거 | 위치 |
|---|---|
| 토큰이 회차를 모른다 | `app/src/vpoc/video.py:86` `client_token(plan_id, shot_id, attempt)` → `f"{plan_id}-{shot_id}-{attempt}"` |
| generate 가 attempt 를 **항상 1로 초기화**한다 | `app/src/vpoc/routes.py:1391` generate 분기 — 샷마다 `"attempt": 1` |
| jobs 키가 회차를 모른다 | `app/src/vpoc/store.py:74` `job_key(...)` → `jobs/{shotId}-{attempt}.json` |
| 출력 접두사가 회차를 모른다 | `app/src/vpoc/store.py:106` `video_output_prefix(...)` → `shots/{shotId}-{attempt}` |
| mp4 찾기가 접두사 아래를 통째로 나열한다 | `app/src/vpoc/video.py:222` `find_output_mp4(prefix)` — 1차 invocation 폴더의 mp4 를 집는다 |
| 데이터 | 2차 jobs 파일의 arn 이 1차와 같고, `ListAsyncInvokes` 에 새 작업이 없다 |

### H2 — ③ 이전을 다시 실행했을 때 ④가 stale 이 되면서 shots·jobs 참조·videoUrl 이 비워지지 않는다 → **참 (증상을 키운다)**

| 근거 | 위치 |
|---|---|
| `mark_stale` 이 `status` 만 바꾼다 | `app/src/vpoc/store.py:340`~`357` — `step["status"] = "stale"` 뿐이고 `shots`·`final`·`genId` 를 건드리지 않는다 |
| 그래서 승인 전에도 ④ 탭에 1차 영상이 그대로 보인다 | 같은 곳 |

generate 가 `steps["4"]` 를 통째로 새로 만들기는 한다(`routes.py:1388`). 그래서 이것만으로
영상이 되붙는 것은 아니지만, **되돌아간 직후 화면이 옛 영상을 보여 주는 것**과 `finalizedAt`·
`final.json`·`project.json` 이 1차에 묶여 있는 것이 사용자가 본 "그대로 다시 로드"에 겹쳐 있다.

### H3 — 완료 기록이 첫 기록만 계속 보여 준다 → **참 (독립된 두 번째 경로)**

| 근거 | 위치 |
|---|---|
| `final.json` 을 `create_only=True`(If-None-Match: \*)로만 쓴다 | `app/src/vpoc/routes.py:833` |
| 이미 있으면 **아무것도 갱신하지 않고** `finalizedAt` 만 세우고 끝난다 | `routes.py:836`~`842` `except VpocError … return True` |
| 그래서 `project.json`·Neptune 갱신 코드에 도달하지 않는다 | `routes.py:844` 이후가 실행되지 않는다 |
| 프로젝트 보기가 `finals/` 최신 → 없으면 `final.json` 을 읽는다 | `routes.py:997` `regen.latest_final(...)` · `regen.py` `latest_final` |
| 데이터 | `finals/` 가 비어 있고 `final.json` 은 23:57:14 **anime**, `project.json` 도 anime |

즉 2차 생성이 성공했더라도 **프로젝트 보기는 계속 1차(애니) 영상을 보여 줬을 것이다.**

### H4 — 프론트가 이전 PlanView·영상 URL 을 다시 그린다 → **거짓 (근본 원인 아님)**

`runAction` 이 서버 응답 뒤 `loadPlan()` 으로 PlanView 를 새로 받아 `state.plan` 을 통째로
교체한다(`frontend/public/vpoc.js:1175`·`1223`). 화면에 남는 캐시는 없다. 사용자가 본 옛 영상은
**서버가 준 값**이다.

다만 방어적으로 둘을 고친다: generate 뒤 ④ 화면 상태(`regenUi` 등 샷별 UI 기억)를 버리고,
`selectedAttempt`/`versions` 가 이전 회차 값으로 남지 않게 한다.

## 4. 원장 정합성

2차 생성에 대한 새 과금이 없다. 멱등 키가 같아 Bedrock 도 우리 원장도 중복 계상하지 않았다 —
**과금은 정확하다.** 사용자가 잃은 것은 돈이 아니라 "다시 만들기" 자체다.
현재 누계 38.52 / 상한 60 USD.

---

## 5. 수정 방향 (지시받은 방식만)

| # | 내용 |
|---|---|
| 1 | **생성 회차 `genId`** — generate 가 실행될 때마다 `"g" + UTC yyyymmddHHMMSS` 를 만들어 `steps["4"].genId` 에 둔다. 토큰 `{planId}-{genId}-{shotId}-{attempt}`, jobs 키 `jobs/{genId}/{shotId}-{attempt}.json`, 출력 접두사 `shots/{genId}/{shotId}-{attempt}/` |
| 2 | 재생성(regen)·retry·키프레임 실패 자동 대체는 **현재 genId 아래에서 attempt 만** 올린다. `versions` 는 현재 genId 의 job 만 보여 준다 |
| 3 | `genId` 가 없는 기존 데이터는 **옛 키 규칙으로 읽기만** 한다(마이그레이션 쓰기 없음) |
| 4 | ③ 이전을 다시 실행하거나 ③ 저장 내용이 바뀌면 ④를 stale 로 만들고 `shots`·`regen`·`revision`·`final` 참조와 `genId` 를 비운다. **S3 의 과거 jobs·영상 파일은 지우지 않는다** |
| 5 | mp4 찾기는 **그 job 의 invocation 폴더 아래에서만** 한다(상위 접두사 나열 금지) |
| 6 | 완료 기록: 회차마다 `finals/{UTC}.json` 을 새로 쓴다(genId 포함). `final.json` 은 옛 데이터 호환용으로 없을 때만 만들고 읽기 전용으로 취급한다. 완료 때 `project.json` 확정본과 Neptune `vpocFinalKey`·`vpocUpdatedAt` 을 **항상** 갱신한다 |
| 7 | 프론트: generate 응답 뒤 ④를 서버 PlanView 로 전부 다시 그리고 이전 회차의 화면 상태를 버린다 |
| 8 | 기존 액션과 PlanView 기존 키는 유지한다. `steps["4"].genId` 만 새 키로 더한다 |

사용자의 기존 계획 데이터는 고치지 않는다. 수정 뒤 사용자가 ③에서 다시 승인하면 새 `genId` 로
새 토큰·새 출력 경로가 생겨 실제로 새로 만들어진다.
