# 설계 — vnew-edit-retake (⑦ 편집: 테이크·컷을 AI로 고쳐 다시 만들기)

> 작성 2026-11-02 · Kiro(claude-opus-5.5) · `requirements.md` R1~R10 을 구현하는 방법. 줄 번호는 구현 전 작업 트리 기준이다.

## 0. 지금 상태 조사(지시서 "조사 필요")

| 항목 | 지금 저장되는가 | 근거 |
|---|---|---|
| 최종 프롬프트 | **마지막 시도만** 칸 상태 `units[i].<variant>.promptEn`·`promptBytes`에 남는다. 다시 시도하면 덮어쓴다 | `generate._do_submit_take`(mutator `s["promptEn"] = prompt`), `_do_submit_previz` 같음 |
| 모델 | gen 단위 `gen.videoModel`뿐(칸·시도별 없음). 원장에는 endpoint 가 남는다 | `_new_take_gen`·`_new_previz_gen`, `_charge_take`·`_charge_previz` |
| 매개변수 | 남지 않는다. 해상도는 `gen.resolution`(통 테이크), 오디오는 칸 `state.audio`(실사 회차·소리 없이 다시)만, duration·draft·task·keep_audio 는 제출 본문에만 | `_submit_take` 본문, `_submit_previz_edit` 본문 |
| 참고 이미지 | **남지 않는다**. payload 함수가 S3 키로 읽은 바이트를 data URI 로 보낼 뿐 | `_take_payload`·`_previz_payload_elements`·`_previz_payload_image_list` |
| 이전 시도 | 남지 않는다. `retry_segment`가 `outputKey`·`invocationArn`을 비우고, `start_real`이 실사 칸을 새로 만든다(지난 회차는 `realHistory`에 키·상태만) | `retry_segment`, `start_real`·`_real_run_summary` |
| 옛 순차 모드(segments) | 프롬프트도 남지 않는다 | `_do_submit_real`·`_do_submit_webtoon` |

→ 이번에 칸 시도마다 기록 한 줄을 별도 S3 JSON 에 남긴다(D1). 칸 상태는 바꾸지 않는다(기존 검사·화면이 칸 모양을 본다).

## 1. 바꾸는 파일

| 파일 | 바꾸는 것 |
|---|---|
| `app/src/vnew/retake.py`(새) | 프롬프트 기록(`log_submit`·`log_result`), AI 고치기 상태(`retakes.json`), 보기·미리보기·제출·스레드·이어받기, 규칙 검사(`check_prompt`), 프레임(`_result_frames`·`_grab_frame`) |
| `app/src/vnew/generate.py` | 파일 끝 새 구역(`_RefTrace`·`_ref_note_load`·`_ref_note_fit`·`_take_params`·`_cut_params`·`_log_attempt`·`_log_result`·`resubmit_cell`). 기존 함수는 관찰 줄만: `_load_jpeg`·`_segment_panel_jpegs`(읽은 키), `_fit_ref`(맞춘 바이트), `_do_submit_take`·`_do_submit_previz`(payload 를 `_RefTrace` 안에서·제출 뒤 기록·제출 실패 기록), `_do_poll_previz`(결과 기록). 제출 본문·칸 상태·원장은 그대로 |
| `app/src/vnew/edit.py` | 재료에 버전 더하기(`_retake_state`·`_all_items`), `_describe`(버전 이름 ` v{n}`), `edit_view`(재료·보기 `retake`·이어받기), `save_doc`·`start_export`·`media_view`(허용 목록), 머리 주석 |
| `app/src/vnew/routes.py` | 새 라우트 3개(파일 끝 ⑦ 구역 뒤)만 |
| `app/src/vnew/settings.py` | `VNEW_EDIT_RETAKE`(템플릿 배선), `VNEW_RETAKE_MODEL`(코드 기본) |
| `app/src/vpoc/errors.py` | `USER_FACING_KINDS` 새 kind 10줄 |
| `k8s/workloads.yaml.tmpl` | 앱 env 한 쌍(Brave Secret ref 뒤, 주석 1줄) |
| `deploy/config.env.example` | `VNEW_EDIT_RETAKE=true`(되돌리기 주석, `BRAVE_SECRET_NAME` 뒤) |
| `deploy/config.prod.env` | `VNEW_EDIT_RETAKE=false` 한 줄 + 주석(`VNEW_EDIT=true` 뒤, 끝 두 줄·BOM·LF 그대로) |
| `frontend/public/vnew.js` | ⑦ 구역: 재료 줄·도구 연결, 패널(파일 ⑦ 구역 끝 「AI로 고치기」 소구역), 순수 함수 |
| `frontend/public/vnew.css` | 파일 끝 116 구역 |
| `tools/check_vpoc.py` | `check_vnew_edit_retake`(rtk-a~m), `PARITY_ENV_KEYS` 끝에 한 키, `main()` |
| 문서 | 이 스펙 3개, `docs/WORKLOG.md` 116, `docs/ANALYSIS.md` |

수정 금지(`casting.py`·`term_search.py`·`.gitlab-ci.yml`·`whynot-20261029`)와 `stitcher.py`·`plan.py`·`vpoc/fal.py`는 건드리지 않는다.

## 2. 결정

### D1. 기록 형식과 위치(`prompts.json`)

`{gen 루트}/prompts.json`(메인 버킷, `generate._gen_root`) = `{"v": 1, "cells": {"<variant>:<cellId>": [기록, …]}}`.

```json
{"id": "p3f9a1c2d4e5b", "at": "2026-11-02T06:00:00Z", "source": "gen", "variant": "webtoon", "cellId": "take1", "cellIndex": 0,
 "mode": "previz-take", "attempt": 1, "run": null, "version": null, "provider": "fal",
 "model": "bytedance/seedance-2.5/reference-to-video",
 "params": {"seconds": 15.0, "duration": "12", "resolution": "480p", "aspectRatio": "16:9", "task": "reference", "audio": true, "draft": true},
 "refs": [{"slot": "image1", "key": "users/o/video/p/vpoc1/characters/…/front.jpg"}, …],
 "videoKeys": ["users/o/video/p/vnew/previz/s1.mp4"],
 "prompt": "STYLE: …", "promptBytes": 2810, "truncated": false,
 "invocationArn": "fal/requests/bytedance/seedance-2.5/reference-to-video/<id>", "outputPrefix": "users/…/take1-webtoon-1",
 "status": "completed", "outputKey": "users/…/take1-webtoon-1/<id>/output.mp4", "error": "", "finishedAt": "…"}
```

- `source`: gen(⑤ 제출) | retake(⑦ 다시 만들기). `run` = 통 테이크 실사 회차 n, `version` = ⑦ 버전 n.
- 칸당 최근 `PROMPT_LOG_MAX`(20)개, 프롬프트 `PROMPT_LOG_MAX_BYTES`(8,192, UTF-8 글자 경계)에서 자르고 `truncated`. 참고 24개·녹화 10개까지.
- 글자 정리 `_clean_text`: 제어 문자·`https?://…`·`data:…`·fal 키를 지운다(오류 요지·사용자 글). S3 키가 주소 꼴이면 빈 값. invocationArn 은 `fal/requests/…`(주소 아님)라 남긴다.
- 쓰기는 `edit._mutate_json`(조건부 쓰기, 경합이면 다시 읽기 5번) + gen 별 프로세스 잠금(동시 제출 2칸).
- 대안: plan.json 칸 상태에 목록(문서가 커지고 모든 조회·mutate 가 무거워짐 — 지시서 금지), 칸마다 파일(목록 조회·쓰기 수 증가), 원장에 프롬프트(원장은 금액용·공용 파일) — 기각.

### D2. 참고 이미지 S3 키 추적(`_RefTrace`)

payload 함수 3개는 키로 읽은 바이트를 `_fit_ref`로 맞춰 목록에 넣는다(키를 돌려주지 않는다). 함수를 고치면 기존 검사(`cast-k` 소스 검사·payload 비교)와 부딪히므로,
**같은 스레드 안에서만** 관찰한다.

- `_ref_note_load(key, data)`: `_load_jpeg`·`_segment_panel_jpegs`가 읽은 바이트의 `id → 키`.
- `_ref_note_fit(src, out)`: `_fit_ref`가 돌려준 바이트의 `id → (src 의 키)`.
- `with _RefTrace() as trace: built = _take_payload(…)` 뒤 `trace.refs(built)`가 실제 `elements`(정면·뒤)·`imageUrls` 순서대로 키를 찾는다(자리 이름 `element{n}.front`·
  `element{n}.ref{k}`·`image{n}`). 결과 목록의 바이트는 살아 있으므로 id 가 겹치지 않는다. 바이트를 더 붙잡지 않는다(정수 → 키만).
- 장면 의상 읽기(`_scene_cast_notes`)가 LLM 에 보내는 패널도 `_fit_ref`를 지나지만 결과 목록에 없어 기록되지 않는다. 키를 모르는 바이트(검사 가짜)는 빈 키.
- 대안: payload 함수가 키 목록도 돌려주기(소스 검사·가짜와 부딪힘), 키를 따로 계산(읽기 실패·자르기와 어긋남) — 기각.

### D3. 기록 시점

- `_do_submit_take`·`_do_submit_previz`: 제출 성공·원장 계상 뒤(세마포어 밖) `_log_attempt(status=submitted)`. claim 을 잃어도(다른 실행이 이어받음) 제출은 일어났으므로 기록한다.
  `stage == "submit"`에서 예외면 `status=failed`·오류 요지(본문 보내기 실패). 그 앞 단계 실패는 기록하지 않는다(제출이 아니다).
- `_do_poll_previz`: 완료 commit 뒤 `_log_result(completed, outputKey)`, 실패면 `_fail_variant` 앞에서 `_log_result(failed)`. 같은 invocationArn 의 마지막 submitted 기록을 고친다.
- `_log_attempt`·`_log_result`는 예외를 밖으로 내지 않는다(경고 `vnew.gen.prompt_log_failed` 1줄, 본문·키 없음).

### D4. 옛 순차 모드는 기록하지 않는다

`start_generate`는 `previz`·`previz-take` gen 만 만든다. 옛 순차(segments) 경로(`_do_submit_real` Luma/Seedance 첫 프레임, `_do_submit_webtoon` 애니 변환)는
2026-10-21 전 gen 의 ⑤ 재시도에서만 돈다. 참고 이미지 경로가 달라 키를 같은 방법으로 못 따라가고, AI 고치기 대상도 아니다(R3.2). 남은 이슈로 적는다.

### D5. 플래그 꺼짐 = 기록 쓰기 없음

`VNEW_EDIT_RETAKE=false`면 `_log_attempt`·`_log_result`가 바로 돌아간다(S3 읽기·쓰기 0, 115 전과 같은 동작). 대안(기록은 늘 쓰기)은 운영에서 승인 전 새 S3 쓰기가 생기고
되돌리기 경로가 둘로 갈려 기각. 운영을 켜기 전의 gen 은 재구성(D6)으로 보인다.

### D6. 재구성

칸 `<variant>.promptEn`은 그 칸의 지금 결과(v1)를 만든 마지막 제출 프롬프트다(제출 때 쓰고, 완료 때 `outputKey`). 기록에 같은 invocationArn 의 gen 기록이 없으면
`source: reconstructed`(모델 `gen.videoModel`, 매개변수: 통 테이크 해상도·길이·오디오 / 컷 길이·보낸 길이·채움, 참고 `null` = 기록 없음)로 시도 목록 끝에 더한다.
`promptEn`이 없으면 기록 없음 → AI 고치기 불가. 프롬프트 빌더로 다시 만드는 방법은 계획(콘티·지시서·캐릭터)이 그 뒤 바뀌었을 수 있어 그때 보낸 글과 다를 수 있으므로 기각.

### D7. 대상·버전·이름

- 대상 = `edit._material_items`의 `cut`·`take` 중 묶음 `current`(지금 결과, v1)와 `retake`(완성 버전 v2…). final·segment·`r{n}`은 409 `RetakeNotTarget`(고정 문구 3개).
- 버전 상태 `{gen 루트}/retakes.json` = `{"v": 1, "cells": {"<칸 키>": {"versions": [버전…], "draft": 초안|null}}}`.
  버전: `n`(2부터)·`status`(queued|preparing|submitted|completed|failed)·`attempt`·`claim`·`claimedAt`·`heartbeatAt`·`createdAt`·`submittedAt`·`finishedAt`·`prompt`·
  `promptBytes`·`requestKo`·`extraKo`·`fromN`·`fromKey`·`draftId`·`usd`·`model`·`seconds`·`resolution`·`invocationArn`·`outputPrefix`·`outputKey`·`error`·`failureKind`·`logId`.
- 결과 접두사 `generate._seg_output_prefix(…, "{cellId}-{variant}-rt{n}", attempt)` → `…/shots/{gen}/take1-webtoon-rt2-1/{요청 id}/output.mp4`. 원래 결과(`take1-webtoon-1`)와 겹치지 않는다.
  칸 상태(`gen.units`)는 바꾸지 않는다.
- 재료 이름 = 원래 이름 + ` v{n}`(예 `테이크 1 · 컷 1–3 v2`, `_describe`). 재료 순서는 그 칸 지금 결과 바로 뒤(`_all_items`) — 기본 문서(첫 묶음 `current`)는 그대로.
- 마지막 버전이 failed 면 다시 제출은 같은 n(attempt+1, 접두사가 attempt 로 달라진다). 칸당 20개(`VERSIONS_MAX`).

### D8. 새 프롬프트 만들기(초안, 202 + 조회)

- `POST …/retake/<mid>/preview` → 그 칸 `draft = {status: running, claim, …}` 저장 → 스레드 `run_draft` → 202(보기). 화면은 2초마다 GET 으로 본다.
  ALB 유휴 60초·gunicorn 60초라 동기 요청으로 하지 않는다(프레임 받기 + 이미지 LLM 은 수십 초).
- `run_draft`: 대상·기준 프롬프트 다시 찾기 → 프레임(`_result_frames`) → LLM 1회(`_rewrite` → `vpoc.llm._converse`, 도구 `emit_retake_prompt` {resultKo, promptEn, changesKo})
  → 원장 text(`vnew-retake-<draftId>`) → 정리(`_normalize_prompt`) → 규칙 검사(D9) → `draft = {status: done, prompt, promptBytes, problems, resultKo, changesKo, framesUsed}`.
  실패면 `failed` + 고정 문구(LLM 원문은 화면·로그에 넣지 않는다 — 원문에 프롬프트가 섞일 수 있다).
- 프레임: `edit._MEDIA_SEM`(프로세스당 재료 하나) 안에서 결과 영상을 임시 폴더에 받아(`edit._download` 스트리밍) 길이를 재고(`edit._probe_file`)
  1~3장(15%·50%·85% 근처, 짧으면 1~2장)을 ffmpeg 로 **stdout 파이프**에 JPEG 로 뽑는다(`-frames:v 1 -vf scale=-2:'min(480,ih)' -f image2pipe`). 프레임은 파일로 쓰지 않고,
  받은 영상은 임시 폴더째 지운다. S3 쓰기 0.
- LLM 모델 `VNEW_RETAKE_MODEL`(코드 기본 = 분석 모델 `ANALYZE_FALLBACK_MODEL`, 이미지를 읽는 상위 모델). max_tokens 3,000.
- 입력 블록: `<previous_prompt>`(이전 프롬프트 그대로) · `<storyboard>`(콘티: 컷 번호·길이·샷·상황·대사, 1,500자) · 프레임 이미지 + 시각 · `<user_request>`(수정 사항·추가 프롬프트).
  사용자 글·콘티의 `<`·`>`는 `‹`·`›`로 바꾸고 주소는 지운다(구분 표시를 흉내 낼 수 없다).
- 같은 칸에 running 초안이 있으면 409 `RetakeDrafting`. 끊긴 초안(하트비트 300초 넘음·이 프로세스에 없음)은 이어받지 않고 failed(D11).

### D9. 규칙 검사(`check_prompt`, 프롬프트 주입 방어)

결정적 검사로 모델 규칙을 지킨다(LLM 이 따르지 않아도 서버가 막는다).

| 규칙 | 검사 |
|---|---|
| 길이 | UTF-8 바이트 ≤ 통 테이크 `generate._take_prompt_budget(caps)` / 컷 `PREVIZ_PROMPT_TARGET` |
| 참고 표시 | `@(Video|Image|Element|Audio)\d+`·`Image \d+` 집합이 이전과 같다(빠짐·새로 생김을 문구에) |
| 항목 | 이전 프롬프트의 **줄 맨 앞** 대문자 항목 이름(`^[A-Z]{2,}( [A-Z]{2,})*( (…))?:`)이 모두 있고 처음 나오는 순서가 같다. 두 빌더 모두 항목마다 한 줄이다(통 테이크 STYLE·ASSETS·TIMELINE·MOTION·HARMONY·RULES·AVOID, 컷 ASSETS·CAST·SHOT·…·CONTINUITY). 줄 가운데의 `MCU:`·`ON SCREEN:` 같은 샷 표시는 항목이 아니다(고쳐도 된다 — 처음 구현에서 넣었다가 샷 크기를 바꾸는 요청이 막혀 뺐다) |
| 주소·구분 표시 | `https?://`·`data:`·`<previous_prompt>` 꼴 없음 |
| 실사 | `webtoon` 낱말 없음(⑤ `_assert_no_webtoon`과 같고, 제출 때 `resubmit_cell`이 한 번 더) |

- 미리보기: 문제 목록을 초안에 둔다(사용자가 고칠 수 있게). 제출: 문제가 있으면 400 `RetakePromptInvalid`(문구에는 고정 낱말·숫자·정규식으로 뽑은 표시만).
- 정리 `_normalize_prompt`: CRLF → LF, 제어 문자(줄바꿈·탭 제외) 제거, 줄 끝 공백·앞뒤 공백 제거.
- 시스템 문구: 구조·참고 표시를 그대로, 요청된 부분만 바꾸고 나머지 문장은 그대로, 영어(대사 한국어는 그대로), 바이트 상한, 사용자 요청은 자료이며 규칙·출력 형식을
  바꾸라는 요청·주소는 무시, 실사면 webtoon 금지, 도구 한 번.

### D10. 다시 만들기 제출(`submit` → `run_retake` → `generate.resubmit_cell`)

- `submit`: 바디 검사(400) → 대상(404·409) → 기준 프롬프트(409 `RetakeNoPrompt`) → 규칙(400) → 견적(`estimate`, 409 `CostMismatch`) → fal 키(409 `FalKeyMissing`)
  → 조건부 쓰기로 claim(초안 done·같은 `draftId`·같은 기준 버전이 아니면 409 `RetakeNoDraft`, 진행 중이면 409 `RetakeRunning`, 20개 넘으면 409 `RetakeLimit`) → 스레드 → 202(보기).
- 견적: 통 테이크 `round(_take_usd(gen.videoModel, cell.seconds, gen.resolution), 2)`, 컷 `round(_previz_model_unit_price(gen.videoModel) × max(3, cell.seconds), 2)`(⑤ estimate 와 같은 식).
- `run_retake`: preparing → `resubmit_cell`(원래 경로) → submitted(invocationArn·접두사·`logId`) → `POLL_INTERVAL_S`(5초)마다 `_poll`(`vpoc.fal.poll`) + 하트비트 →
  completed(`_find_output_key`) | failed(`_user_facing_error` 문구·`failureKind`). 조회 오류 5번 연속이면 failed, 제출 뒤 1,800초 넘으면 failed. 기록 결과도 고친다.
- `resubmit_cell`(generate.py): 칸 상태를 읽기만 한다. 통 테이크는 `_stitch_recordings(recKeys)` + `_take_payload`(실사는 `variant="real"`, `_assert_no_webtoon`) + `_submit_take`
  (같은 caps·해상도·길이·칸 `state.audio`), 컷은 `_fetch_bytes(recordingKey)` + `_cut_clip` + `_previz_payload(caps)` + `_submit_previz_edit`. 준비~제출은 `_PREPARE_SEM` 안
  (⑤와 같은 메모리 규칙), 원장은 `_charge_take`·`_charge_previz`(작업 토큰 키, 한 번), 기록 `source=retake`. payload 가 만든 프롬프트는 버리고 사용자 프롬프트를 보낸다.

### D11. 이어받기(`_reconcile`, 보기·미리보기·제출·GET /edit 에서)

| 상태 | 하트비트 끊김 기준 | 이 프로세스에서 안 돌면 |
|---|---|---|
| submitted | 180초 | 새 claim 으로 조회만 이어간다(비용 0) |
| queued·preparing | 900초(녹화 받기·본문이 길 수 있다) | failed "다시 만들기를 제출하는 중 멈췄습니다. 다시 시도해 주세요."(두 번 제출 방지) |
| 초안 running | 300초 | failed "새 프롬프트 만들기가 중단됐습니다. 다시 눌러 주세요." |

조건부 쓰기라 파드 둘이 함께 봐도 한쪽만 이어받는다. 스레드 표(`_threads`)로 이 프로세스에서 도는지 본다(edit 내보내기와 같은 방식).

### D12. 편집 재료 통합(edit.py)

- `_retake_state(owner, pid, gen_id)`: 켬이면 `retakes.json`(못 읽으면 `{}` + 경고), 끔이면 None. `_all_items(gen, variant, state)` = `_material_items` + 완성 버전(그 칸 current 바로 뒤).
  None 이면 `_material_items` 그대로(끔 = 115 전 바이트까지 같은 보기).
- `edit_view`·`save_doc`·`start_export`·`media_view`가 `_all_items`를 쓴다. `summary`(gen_view)는 그대로(S3 를 읽지 않는다).
- 켬이면 보기에 `retake: {enabled: true}`, 재료마다 `retake: {can, n, cellKey, running, reason}`(`retake.material_flag`). 프롬프트는 넣지 않는다(읽기 권한 보기).
- 켬이면 `edit_view`가 이어받기(`retake.reconcile_state`)를 한 번 부른다(보기는 실패하지 않는다).

### D13. 화면(`vnew.js` ⑦ 구역 끝 「AI로 고치기」, `vnew.css` 116 구역)

- 재료 줄: 켬(`view.retake.enabled`)이고 재료에 `retake`가 있으면 재료 버튼 옆에 [AI로 고치기](`vnew-edit-fix-open`, 못 하면 흐림 + title 이유). 재료 버튼은 그대로
  (`vnew-edit-bin-item` 글·순서·동작 같음, 둘을 `vnew-edit-bin-cell`로 묶는다). 머리 줄에 [고른 클립 AI로 고치기](`vnew-edit-fix-sel`, 고른 클립 원본이 고칠 수 있을 때만,
  `editRenderTools`가 disabled·title 만 바꾼다 — 노드를 만들지 않아 타임라인 다시 그리기 비용 그대로).
- 패널 = ⑦ 루트 안의 고정 위치 대화상자(`vnew-edit-fix`, `role=dialog`). 처음 열 때 뼈대를 한 번 만들고(영상·입력칸은 다시 만들지 않는다) 조회 때 바뀌는 부분만 다시 그린다.
  - 머리(이름·버전·닫기 ×) · 안내 줄 · 지금 결과 `<video controls>` · 버전 목록(v1 원본·v2… 상태, 완성 버전마다 [바꾸기]·[재료에 추가]) ·
    이전 프롬프트(출처 기록/재구성/버전/기록 없음 + 본문 `<pre>`, `<details>` 이전 시도 n개) · 입력(수정 사항·추가 프롬프트, 글자 수 제한) · [새 프롬프트 만들기] ·
    초안(결과 설명·바뀐 점·새 프롬프트 `<textarea>`·바이트 계기·문제 목록·예상 비용·[다시 만들기 ($x)]) · 진행 줄(대기·준비·만드는 중·완성·실패 문구 + [다시 시도]).
  - 조회: 초안 running 이면 2초, 버전 진행 중이면 5초마다 GET(패널이 열려 있고 ⑦ 이 살아 있을 때만). 완성되면 GET /edit?variant= 로 재료만 다시 받는다(문서·실행 취소 그대로).
  - 제출: 확인창("v{n} 을 같은 모델로 다시 만듭니다. 예상 비용 $x …") → POST {prompt(고친 값), requestKo, extraKo, confirmUsd: 서버 견적, draftId}.
  - [바꾸기] `editSwapSource`(순수): 대상 = 패널을 고른 클립에서 열었으면 그 클립, 아니면 그 칸의 모든 버전 원본을 쓰는 클립. in/out 은 새 길이 안으로 줄이고(속도 반영 최소
    길이), 속도·소리 끔·id 는 그대로, 이름은 원래 재료 이름일 때만 새 이름. 실행 취소 한 단계. 길이를 모르면 `/media/<mid>`로 먼저 잰다. v1 [바꾸기] = 되돌리기.
  - 패널이 열린 동안 `editOnKey`는 Esc(닫기) 말고 무시한다. `editLeave`·`editBuild`는 패널 타이머를 멈춘다.
- 글은 모두 `textContent`·`value`(innerHTML 0). 큰 글(`<pre>`)은 `white-space: pre-wrap`·세로 스크롤만(가로 스크롤은 타임라인 하나 — edu-k).

### D14. 권한·노출 범위

- 새 라우트 3개는 `_project_for(pid, write=True)`(프로젝트 소유자)만. 프롬프트·초안·요청 글·버전 비용은 이 라우트에서만 보인다. GET /edit(읽기 권한)에는 `retake` 표식만.
- 재료는 `mid`로만 고른다(S3 키를 받지 않는다). 영상 재생은 presigned 1시간(⑦과 같다). 기록에는 presigned URL 을 적지 않는다.

### D15. 비용(1회)

- 웹툰 통 테이크 15초(480p, Seedance 2.5 r2v): 토큰 854×480×24×15/1024 = 144,112.5 → $0.0214/1000토큰 → **$3.08**. 실사 테이크(같은 모델·해상도·길이) **$3.08**.
  720p 면 15초 324,000토큰 $6.93. 컷 단위(Kling O3 standard $0.14/초): 컷 2초 → 3초 과금 $0.42, 7.5초 $1.05.
- 새 프롬프트 만들기 LLM 1회: 입력 약 3천~5천 토큰(480p 프레임 3장 ≈ 1,600 + 이전 프롬프트 ≈ 1,000 + 시스템·콘티 ≈ 700) · 출력 약 1천~1.5천 토큰 → 약 $0.03~0.04(원장 text 단가 3/15 USD per Mtok). 원장 text.

### D16. 로그(본문·키·URL 없음)

`vnew.retake.prompt_logged pid gen cell source bytes refs` · `vnew.retake.draft_start|draft_done|draft_failed` · `vnew.retake.submit|submitted|done|failed|resume|claim_lost` ·
`vnew.retake.frames_failed mid` · `vnew.gen.resubmit` · `vnew.gen.prompt_log_failed`. 오류는 예외 이름·kind 만.

### D17. 검토한 대안

- 동기 미리보기(60초 넘을 수 있음, 기각) · 프레임을 S3 에 저장해 LLM 에 URL(지시서 금지, 기각) · LLM 이 수정 사항만 영어로 바꾸고 서버가 끝에 붙이기(요청된 부분만 바꾸기가 안 됨,
  보류) · 규칙 위반 시 자동 고치기(문장 지우기 — 뜻이 바뀜, 기각) · ⑤ 칸 자체를 새 결과로 바꾸기(원래 결과 덮어씀·⑤ 화면과 충돌, 기각) · 지난 실사 회차도 대상(회차마다
  기준이 달라 보류) · 버전마다 새 gen(편집 문서가 gen 마다라 기각) · 재료 줄 대신 타임라인 클립 위 버튼(다시 그리기 비용·작은 클립, 기각).

## 3. API

### 3.1 `GET /api/vnew/p/<pid>/edit/retake/<mid>` → 200 (소유자)

```json
{"enabled": true, "genId": "g1", "mid": "m…", "cellKey": "webtoon:take1", "variant": "webtoon", "cellId": "take1", "kind": "take",
 "label": "테이크 1 · 컷 1–3", "n": 1, "videoUrl": "https://…(presigned)",
 "base": {"source": "log|reconstructed|retake", "prompt": "…", "promptBytes": 2810, "n": 1, "attemptId": "p…"},
 "canRetake": true, "block": "",
 "attempts": [{"id": "p…", "source": "gen|retake|reconstructed", "at": "…", "attempt": 1, "run": null, "version": null, "model": "…", "provider": "fal",
               "params": {…}, "refCount": 4, "prompt": "…", "promptBytes": 2810, "truncated": false, "status": "completed", "error": ""}],
 "versions": [{"n": 1, "original": true, "status": "completed", "label": "…", "mid": "m…", "sourceKey": "…"},
              {"n": 2, "status": "submitted", "attempt": 1, "label": "… v2", "mid": "", "sourceKey": "", "error": "", "requestKo": "…", "usd": 3.08, "createdAt": "…"}],
 "live": {"n": 2, "status": "submitted", "attempt": 1} ,
 "draft": {"id": "d…", "status": "running|done|failed", "baseN": 1, "requestKo": "…", "extraKo": "", "prompt": "…", "promptBytes": 0, "problems": [],
           "resultKo": "", "changesKo": [], "framesUsed": 0, "error": ""},
 "estimate": {"usd": 3.08, "seconds": 15.0, "billedSeconds": 15.0, "model": "…", "resolution": "480p"},
 "limits": {"requestMax": 500, "extraMax": 500, "promptMaxBytes": 4000}}
```

### 3.2 `POST …/edit/retake/<mid>/preview` {requestKo, extraKo?} → 202 (3.1 보기)
### 3.3 `POST …/edit/retake/<mid>` {prompt, requestKo, extraKo?, confirmUsd, draftId} → 202 (3.1 보기)

바디는 `_edit_body`(256KB, 넘으면 413). 오류: 404 `RetakeDisabled`·`EditDisabled`·`MediaNotFound`·`NoGen`, 403(소유자 아님), 400 `RetakeBadRequest`·`NoConfirmUsd`·
`RetakePromptInvalid`, 409 `RetakeNotTarget`·`RetakeNoPrompt`·`RetakeDrafting`·`RetakeNoDraft`·`RetakeRunning`·`RetakeLimit`·`RetakeGenChanged`·`CostMismatch`·`FalKeyMissing`.

### 3.4 GET /edit 에 더해지는 것(켬일 때만)

`retake: {"enabled": true}`, 재료마다 `retake: {"can": bool, "n": 1|v, "cellKey": "…", "running": bool, "reason": "…"}`, 버전 재료(`group: "retake"`, `version: n`).

## 4. 검사(`check_vnew_edit_retake`, rtk-a~m)

| 그룹 | 보는 것 |
|---|---|
| rtk-a | 설정·배선: `_bool` 기본 true·하위 프로세스 7값·settings 줄, 템플릿 한 쌍(Brave ref 뒤)·base true·되돌리기 주석·운영 overlay false(BOM·LF·끝 두 줄 그대로)·dev 없음·`PARITY_ENV_KEYS`·렌더 dev true·prod false·prod+true |
| rtk-b | 기록: 통 테이크 웹툰 제출·완료, ⑤ 재시도(attempt 2), 실사 회차(run 1), 컷 단위 실사·웹툰(elements), 이미지 목록 모델 — 기록 한 줄씩·프롬프트 = 보낸 본문·모델·매개변수 = 본문·참고 키 순서 = 실제 읽은 키·녹화 키·완료 outputKey·실패 기록, URL·data URI·fal 키·presigned 없음, 칸당 20·8KB 자르기, 쓰기 실패 → 경고·제출 그대로, 끔 → 쓰기 0, 로그에 본문 없음 |
| rtk-c | 재구성: 기록 없는 gen 의 promptEn → reconstructed, promptEn 없음 → 기록 없음·409 `RetakeNoPrompt` |
| rtk-d | 미리보기: 202·초안 running → done, LLM 입력(이전 프롬프트·콘티·대사·프레임 3장·요청 꺾쇠 바꿈·주소 지움), 원장 text, 프레임 저장 0(put·write 는 retakes.json·원장뿐)·임시 폴더 0, 프레임 실패 → 프레임 없이, LLM 실패 → failed 고정 문구, 진행 중 409 `RetakeDrafting`, 바디 400 |
| rtk-e | 제출: 400 `NoConfirmUsd`·409 `CostMismatch`·409 `RetakeNoDraft`·400 `RetakePromptInvalid`·409 `FalKeyMissing`, 성공 → 같은 모델·caps·녹화·참고·매개변수, 프롬프트 = 고친 값, 새 접두사 rt2, 원장 1건, 원래 칸 상태·원래 결과 그대로, 중복 409 `RetakeRunning`, 실패 → 같은 번호 attempt 2, 20개 409 `RetakeLimit` |
| rtk-f | 규칙: `check_prompt` 경우(길이·참고·항목·순서·주소·구분 표시·실사 webtoon), 모델별(통 테이크 4000·컷 elements 2500·이미지 목록 `Image N`) 가짜 LLM 응답 |
| rtk-g | 이어받기: submitted 끊김 → 조회 이어감, preparing 끊김 → failed, 초안 끊김 → failed, 이 프로세스에서 돌면 그대로, 조회 실패·시간 상한 |
| rtk-h | 라우트·권한·끔: 끔 404(프로젝트 보기 전), `VNEW_EDIT` 끔 404, 읽기 전용 403, mid 꼴·없음 404, final·지난 회차 409, 413 |
| rtk-i | 편집 통합: 재료 순서(v2 가 원래 바로 뒤)·이름·기본 문서 그대로·v2 저장·내보내기·필름, 끔이면 v2 notAllowed·보기 키 없음 |
| rtk-j | 화면(node 가짜 DOM, 실제 GET /edit·retake 응답): 재료 버튼·고른 클립 버튼·끔이면 없음, 패널 열기·출처·시도·영상, 미리보기·조회·초안·바이트 계기·문제, 제출 확인창·바디, 진행·완성 → 재료 다시 받기·[바꾸기](자르기·속도 유지·실행 취소)·v1 되돌리기·[재료에 추가], 실패 → [다시 시도], 단축키 막음·Esc, innerHTML 0, `editSwapSource` 순수 경우 |
| rtk-k | CSS 116 구역: 쓰는 클래스 정의·가로 스크롤 없음·고정 너비 960px 이하·LF·끝 줄바꿈 |
| rtk-l | `USER_FACING_KINDS` 새 kind 10개 한 줄씩·운영 문구 그대로·`CostMismatch` 가림 그대로·던지는 곳 문구(AST: 글자 또는 상수만 넣은 f-string) |
| rtk-m | 로컬 ffmpeg: 실제 mp4 에서 `_grab_frame` JPEG(파이프)·없는 파일·시간 초과 None·`_result_frames` 3장·임시 파일 남지 않음(없으면 경고) |

## 5. 운영 반영(실행하지 않음)

Claude 검토·커밋 → 개발 main 병합 → CI → ECR → ai-deploy `whynot/dev` → ArgoCD(dev 는 base `VNEW_EDIT_RETAKE=true`). 운영을 켤 때는 `config.prod.env`의
`VNEW_EDIT_RETAKE=false`를 `true`로 바꾸고 수동 job `deploy:whynot:prod`. 되돌리기는 다시 `false`(라우트 404·버튼 없음·기록 쓰기 없음, 저장된 것은 그대로).
