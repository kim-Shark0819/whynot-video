# 작업(116 보완 6): AI로 고치기 — 초안 만들기가 느린 문제(약 4.8분 → 목표 1.5분 안)

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 5 위에서 한다.

## 예산 (가장 우선)
- 새 검사는 바뀐 분기만 덮는다. 변형 검사는 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다. 커버리지 측정과 전체 검사 반복은 하지 않는다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다.** 통째로 다시 쓰거나 비우지 않는다.

## 배경 (dev 실측, 2026-11-03 06:32, Kling O3 구간 초안, 전체 287초)
```
06:32:21 draft_start
06:33:13 rewrite(초안)  fable  48s ok  in=5518 out=2828 stop=tool_use      → 3,255B(상한 2,500 초과)
06:33:47 rewrite(줄이기1) fable 34s FAIL out=3000 stop=max_tokens
06:34:35   trunc_retry    fable 47s FAIL out=4096 stop=max_tokens
06:34:59   다음 모델       opus  24s FAIL out=3000 stop=max_tokens
06:35:27   trunc_retry    opus  27s ok   out=3392                         → 2,649B
06:36:02 rewrite(줄이기2) fable 35s FAIL out=3000 stop=max_tokens
06:36:49   trunc_retry    fable 47s FAIL out=4096 stop=max_tokens
06:37:08   다음 모델       opus  19s ok   out=2376                         → 1,944B, 문제 0
```
- **낭비:** 287초 중 약 187초(65%)가 출력 한도에 잘려 버린 호출이다.
- **출력 크기:** 줄이기 입력은 약 2,100토큰인데 출력은 3,000~4,096토큰을 넘는다. 결과 프롬프트 자체는 2.6KB(약 900토큰) 정도다.
  - 즉 도구 입력 밖의 무언가(평문 설명, 생각 과정 등)가 출력 예산을 먹는 것으로 보인다.
  - Fable은 `_skip_forced_tool` 때문에 toolChoice가 auto일 수 있다. **원인을 코드와 요청 모양으로 먼저 확인하고** 결과 보고에 적는다.
- **설정:** `LLM_MAX_TOKENS = 3000`(retake.py). 잘림 재시도는 `vpoc/llm.py`의 `_trunc_budget`이다.

## 진행 방법 (이 순서로)
1. **원인 확인(코드 읽기만):**
   - `vpoc/llm.py`의 `_request`, `_skip_forced_tool`, `_model_chain`, `_converse`를 읽는다.
   - 줄이기 호출에서 출력 예산을 무엇이 먹는지 판단한다.
   - 보고에 1~3줄로 적는다.
2. **줄이기 호출 전용 설정(`retake.py`만):**
   - 줄이기 전용 도구 스키마를 둔다. `promptEn` 하나만 받는다(resultKo·changesKo 없음). 출력이 짧아진다.
   - 줄이기 전용 출력 예산을 상수로 둔다(예: `SHRINK_MAX_TOKENS = 8000`). 첫 호출에서 잘리지 않을 만큼이다.
   - 시스템 문구에 "도구 호출 밖에 글을 쓰지 말 것"을 짧게 넣는다.
   - 1번 원인이 다른 것이면 그 원인에 맞게 고친다. `vpoc/llm.py`를 고쳐야 하면 줄이기 호출에만 영향이 가게 하고 이유를 적는다. 다른 호출자 동작은 바꾸지 않는다.
3. **초안 호출에서 처음부터 상한 안에 들게:**
   - 초안 시스템 문구에 상한 바이트와 목표(상한 × 0.85)를 숫자로 준다.
   - "한국어 1글자 = 3바이트"도 짧게 알린다.
   - 이미 비슷한 문구가 있으면 숫자만 보강한다.
4. **작은 초과는 LLM 없이 처리:**
   - 줄이기를 하기 전이나 1번째 줄이기 뒤에, 초과분이 상한의 10% 이하이면 다음 LLM 줄이기를 하지 않는다.
   - 대신 바로 보완 5의 `_trim_to_limit` 단계로 간다(비용 0, 즉시).
   - 그 판정 결과도 기존 `_shrink_better`를 통과해야 쓴다. 자르기로 상한 안에 못 들면 기존처럼 다음 LLM 줄이기를 한다.
   - 비율은 상수로 둔다(예: `SHRINK_TRIM_FIRST = 0.10`).
5. **단계별 시간 로그:**
   - `shrink_try`와 `shrink_trim` 로그에 `ms=`를 더한다.
   - `draft_done`에는 이미 `ms`가 있다.

## 바꾸지 않는 것
- 초안 모델 순서(최상위 모델 먼저), `check_prompt` 규칙, 제출 경로, 화면, fal.py, 원장 key 규칙.

## 검사 (rtk 그룹에 이어서, LLM 가짜)
- **줄이기 요청 모양**
  - 도구 스키마가 promptEn 하나만 받는다.
  - maxTokens가 `SHRINK_MAX_TOKENS`다.
  - 초안 호출의 도구·예산은 그대로다.
- **초안 시스템 문구**에 상한 바이트와 목표(0.85) 숫자가 있다.
- **작은 초과(예: 상한 +5%)**
  - LLM 줄이기 호출 0, 자르기로 상한 안, `shrunk=True`.
- **큰 초과(예: +30%)**
  - LLM 줄이기 1번 뒤 남은 초과가 작으면, 2번째 LLM 없이 자르기로 끝난다(LLM 호출 1).
- **로그**에 `ms=`가 있다.
- 기존 기대값은 바꾸지 않는다. 바꿔야 하면 이유를 보고에 적는다.

## 범위
- 고칠 것: `app/src/vnew/retake.py`, `tools/check_vpoc.py`, `docs/WORKLOG.md`(덧붙이기), `.kiro/specs/vnew-edit-retake/tasks.md`(덧붙이기)
- 필요할 때만: `app/src/vpoc/llm.py`(줄이기 호출에만 영향, 이유 보고)
- 그 밖은 수정 금지

## 완료 조건
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] WORKLOG·tasks.md 삭제 줄 0
- [ ] 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`.
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 짧게)
- 원인(1번), 바꾼 함수·상수, 검사 결과, 예상 시간, 남은 이슈
