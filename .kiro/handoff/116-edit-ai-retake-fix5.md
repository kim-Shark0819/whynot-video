# 작업(116 보완 5): AI로 고치기 — 자동 줄이기가 끝나도 바이트 상한을 넘는 문제

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 4b 위에서 한다.

## 예산 (가장 우선)
- 새 검사는 바뀐 분기만 덮는다. 변형 검사는 새 자르기 함수에만 1회 한다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다. 커버리지 측정과 전체 검사 반복은 하지 않는다.
- WORKLOG는 3~5줄로 쓴다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다. 파일을 통째로 다시 쓰거나 비우지 않는다.** 지난번 실행에서 두 파일이 빈 파일이 됐다.

## 배경 (dev 실측, 2026-11-03)
Kling O3 "이 구간만"(4초) 초안에서 줄이기 2번(`SHRINK_TRIES`, 목표 0.9 → 0.75)을 다 썼다.
- 결과는 2,619바이트였다. 상한은 2,500이다.
- 그래서 문제("…2,500바이트 안으로 줄여 주세요") 1개가 남았다.
- 사용자가 직접 줄이지 않으면 제출할 수 없다.

원인은 LLM이 바이트 수를 정확히 세지 못하는 데 있다. 목표를 낮춰도 상한을 조금 넘는 결과가 나온다. 그 결과를 사람이 손으로 줄였다.
- 지운 것은 영어 수식 구절 5곳, 약 137바이트다.
  - "loose strands on forehead, "
  - "; parallax"
  - " (noon–afternoon, not sunset)"
  - "soft contact shadows; consistent scale; "
  - "hair and clothes react softly; "
- 이렇게 줄인 2,480바이트는 `check_prompt` 문제 0개였다. 제출하니 영상도 정상으로 나왔다.

## 요구사항 (`app/src/vnew/retake.py`만)

### 1. 남은 초과 바이트를 숫자로 다시 지시한다
2번째부터 줄이기를 시도할 때, 직전 결과가 상한을 넘었으면 지시 문구를 바꾼다.
- 지금 몇 바이트이고 최소 몇 바이트를 더 줄여야 하는지 숫자로 준다(지금 `rule1`과 같은 방식).
- 목표는 `상한 × target − 여유`로 정한다. 여유는 직전 초과분 이상이다.
- 상수와 시도 횟수(`SHRINK_TRIES` 2번)는 바꾸지 않는다. LLM 호출을 늘리지 않는다.

### 2. 마지막에 규칙대로 자르기(LLM 없이)
LLM 줄이기를 다 했는데도 상한을 넘을 때만 새 함수(예: `_trim_to_limit(prompt, max_bytes, refs, labels)`)를 쓴다.
- 지울 수 있는 것과 지우면 안 되는 것:

  | 지우면 안 됨 | 지울 수 있음 |
  |---|---|
  | 참고 표시(`@Video1`, `@Element1` 등)가 든 조각 | 항목 줄에서 위 조건에 걸리지 않는 영어 조각 |
  | 한국어(한글)가 든 조각 | |
  | 따옴표 안 대사 | |
  | 시간 표기(`0–1.0s` 등) | |
  | 줄 앞 항목 이름(`SHOT:`, `MOTION:` …) | |

  - 조각 = `;` 또는 `,` 로 나뉜 구절이나 괄호 `( … )` 묶음.
- 지우는 순서:
  - 줄은 MOTION, HARMONY, CONTINUITY처럼 꾸밈이 많은 줄부터다. 순서는 상수 하나로 둔다.
  - 한 줄 안에서는 뒤 조각부터, 괄호 묶음을 먼저 지운다.
  - 상한 안에 들어오면 바로 멈춘다.
  - 줄마다 최소 1조각은 남긴다. 항목 이름만 남는 줄을 만들지 않는다.
- 조각을 지운 자리에 구두점이 겹치거나(`; ;`, `, .`) 공백이 겹치지 않게 정리한다.
- 결과는 `check_prompt`로 다시 본다.
  - `_shrink_better` 판정(다른 문제가 늘지 않음)을 통과할 때만 쓴다.
  - 상한 안에 못 들어가도 줄었으면 쓴다.
  - 쓰면 `shrunk=True`로 둔다(화면 안내는 지금 그대로 보인다).
- 로그는 `vnew.retake.shrink_trim from=<바이트> to=<바이트> cut=<지운 조각 수> problems=<문제 수> kept=<True|False>` 한 줄이다. 원문은 남기지 않는다.
- LLM 호출이 실패해 줄이기가 멈춘 경우에도 이 단계는 한다. 돈이 들지 않는다.

### 3. 바꾸지 않는 것
- 상한 안 초안: 지금과 같다(자르기 0).
- `check_prompt` 규칙, 화면, 제출 경로, `fal.py`.

## 검사 (rtk 그룹에 이어서, LLM 가짜)
- **자르기 함수만**
  - 위 배경의 실제 프롬프트 모양(아래 예시 줄들)이 2,600바이트대 → 상한 2,500 안.
  - 참고 표시, 한글, 따옴표 대사, 시간 표기, 항목 이름이 모두 그대로다.
  - `check_prompt` 문제 0.
- **보호 조각만 있어서 못 줄이는 경우**: 원문 그대로 반환하고 kept=False.
- **줄이기 흐름**
  - 가짜 LLM이 2번 다 상한을 넘게 답하면 → 자르기 단계 후 상한 안, `shrunk=True`.
  - LLM 호출 수는 2.
- **상한 안 초안**: 자르기를 부르지 않는다(호출 0).
- **2번째 시도 문구**: 직전 초과분을 숫자로 담는다.
- 기존 기대값은 바꾸지 않는다.

예시 줄(검사용 모양, 길이는 반복으로 맞춘다):
```
MOTION: Don't copy @Video1's head bobbing (speaker marker only). Restrained acting: breathing, blinks, small eye darts, smooth expression changes, accurate lip sync; no snaps, eased starts/stops; hair and clothes react softly; move only per @Video1's blocking; parallax; no jitter or warping.
HARMONY: bright, slightly warm daytime sun (noon–afternoon, not sunset); brighter exposure on faces and rooftop; same light direction and color temperature in both cuts; soft contact shadows; consistent scale; one palette.
DIALOGUE: 0–1.0s 여의준 (off-screen, never shown): "죄송해요…. 역시 불편하셨겠죠….", then 범건우 (Korean, lips move): "…아냐. 재밌네. 계속 해 봐.".
```

## 범위
- 고칠 것: `app/src/vnew/retake.py`, `tools/check_vpoc.py`, `docs/WORKLOG.md`(덧붙이기), `.kiro/specs/vnew-edit-retake/tasks.md`(덧붙이기)
- 그 밖은 수정 금지

## 완료 조건
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] `git diff --stat`에서 WORKLOG·tasks.md는 줄이 늘기만 함(삭제 0)
- [ ] 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`.
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 짧게)
- 바꾼 함수, 자르기 규칙 요약, 검사 결과, 남은 이슈
