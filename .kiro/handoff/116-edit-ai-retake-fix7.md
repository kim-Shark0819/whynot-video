# 작업(116 보완 7): AI로 고치기 — 초안 호출 출력 한도 올리기

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 6 위에서 하는 아주 작은 작업이다.

## 예산 (가장 우선)
- 상수와 검사 몇 줄만 바꾼다. 변형 검사, 커버리지 측정, 전체 검사 반복은 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다.** 통째로 다시 쓰거나 비우지 않는다. WORKLOG는 2줄로 쓴다.

## 배경 (dev 실측)
- 초안 호출(`_rewrite`, where `vnew.retake.rewrite`, 도구 `emit_retake_prompt`)은 `LLM_MAX_TOKENS = 3000`이다.
- 실측 출력은 2,828토큰, 2,687토큰으로 한도에 가깝다.
- 보완 6에서 확인한 원인은 이렇다. toolChoice가 auto라서 도구 밖 출력도 같은 한도를 쓴다. 잘림 재시도 예산은 4096뿐이다.
- 그래서 초안 호출도 잘리면 버리는 호출과 대기가 생긴다. 줄이기 호출은 보완 6에서 8000으로 고쳤다.

## 요구사항 (`app/src/vnew/retake.py`만)
1. 초안 호출 전용 출력 한도를 **8000**으로 둔다.
   - 이름 예: `DRAFT_MAX_TOKENS = 8000`. 주석에 실측 근거를 1줄로 적는다.
   - `_rewrite`의 초안 경로가 이 값을 쓰게 한다.
2. `LLM_MAX_TOKENS`를 다른 곳에서도 쓰면 그 동작은 바꾸지 않는다. 쓰는 곳이 없어지면 지우고 보고에 적는다.
3. 줄이기 경로(`SHRINK_MAX_TOKENS`), 도구, 문구, 모델 순서는 그대로 둔다.

## 검사 (rtk-w 이어서)
- 실제 `_request` 결과로 확인한다.
  - 초안 호출: maxTokens 8000, 도구 `emit_retake_prompt`.
  - 줄이기 호출: 8000, `emit_short_prompt` 그대로.
- 기존 검사에서 초안 호출의 maxTokens를 3000으로 기대하는 곳이 있으면 8000으로 바꾸고 보고에 적는다.

## 범위
- 고칠 것: `app/src/vnew/retake.py`, `tools/check_vpoc.py`, `docs/WORKLOG.md`(덧붙이기), `.kiro/specs/vnew-edit-retake/tasks.md`(덧붙이기)
- 그 밖은 수정 금지

## 완료 조건
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] WORKLOG·tasks.md 삭제 줄 0, 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`.
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 3~5줄)
- 바꾼 상수와 함수, 검사 결과, 바꾼 기존 기대값
