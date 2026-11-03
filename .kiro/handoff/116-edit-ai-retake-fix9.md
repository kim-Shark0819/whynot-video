# 작업(116 보완 9): AI로 고치기 — 줄이기 호출은 Opus 먼저

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 8 위에서 하는 작은 작업이다.

## 예산 (가장 우선)
- 바뀐 분기만 검사한다. 변형 검사, 커버리지 측정, 전체 검사 반복은 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다.** WORKLOG는 2줄이다.

## 배경 (dev 실측)
| 시각 | 모델 | 시간 | 출력 토큰 | 결과 |
|---|---|---|---|---|
| 07:47 | Fable 5.1 | 52초 | 4,307 | 성공 |
| 07:48 | Fable 5.1 | 62초 | 5,406 | 성공 |
| 08:46 | Fable 5.1 | 90초 | 8,000 | **잘림**(`stop=max_tokens`) |
| 08:46 (바로 뒤) | Opus 5.5 | 8.6초 | 1,086 | 성공, 같은 일 |

- 줄이기는 "길이만 줄이는" 기계적인 일이다. 내용 판단은 초안 호출(Fable 먼저, 그대로)이 한다.
- 줄이기는 Opus를 먼저 쓰는 편이 빠르고 안정적이다. 초안 호출의 모델 순서는 바꾸지 않는다.

## 요구사항
1. `app/src/vnew/settings.py`에 `VNEW_RETAKE_SHRINK_MODEL`을 추가한다.
   - 코드 기본값은 `global.anthropic.claude-opus-5-5`다. 환경변수로 바꿀 수 있고, 빈 값이면 기본값을 쓴다.
   - `VNEW_RETAKE_MODEL` 옆에 주석 1~2줄(실측 근거)과 함께 둔다.
   - 템플릿과 배포 설정(`k8s/`, `deploy/`)에는 넣지 않는다. 코드 기본값이다.
2. `retake.py`의 `_shrink_draft`가 LLM 줄이기에서 이 모델을 쓰게 한다.
   - 다음 모델로 넘어가는 순서는 `vpoc.llm._model_chain` 그대로다(Opus 5.5 → 나머지 대체 모델).
   - 초안 호출(`run_draft`·`_run_draft_model`)은 `VNEW_RETAKE_MODEL` 그대로다.
3. 원장(`_charge_llm`)은 실제 쓴 모델의 usage를 그대로 계상한다. 이미 그렇게 되어 있으면 손대지 않는다.

## 검사 (rtk-w 이어서, LLM 가짜)
- 줄이기 호출의 model 인자가 `VNEW_RETAKE_SHRINK_MODEL`(기본 opus-5-5)이다.
- 초안 호출의 model 인자는 `VNEW_RETAKE_MODEL` 그대로다.
- 하위 프로세스로 환경변수 읽기를 확인한다.
  - 없음 → opus-5-5
  - 빈 값 → opus-5-5
  - 다른 값 → 그 값
- 기존 검사가 줄이기 호출의 model을 `VNEW_RETAKE_MODEL`로 기대하면, 새 설정으로 바꾸고 보고에 적는다.

## 범위
- 고칠 것:
  - `app/src/vnew/settings.py`
  - `app/src/vnew/retake.py`
  - `tools/check_vpoc.py`
  - `docs/WORKLOG.md`(덧붙이기)
  - `.kiro/specs/vnew-edit-retake/tasks.md`(덧붙이기)
- 그 밖은 수정 금지(`vpoc/llm.py`, `k8s/`, `deploy/` 포함)

## 완료 조건
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] WORKLOG·tasks.md 삭제 줄 0, 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`.
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 3~5줄)
- 바꾼 설정과 함수, 검사 결과, 바꾼 기존 기대값
