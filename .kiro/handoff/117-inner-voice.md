# 작업 117: 속마음 대사(꼬리 없는 둥근 말풍선) — 입은 움직이지 않고 목소리로만

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 10 위에서 한다.

## 예산 (가장 우선)
- 바뀐 분기만 검사한다. 변형 검사는 새 판정 1곳에만 1회 한다. 커버리지 측정은 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다.**
  - 스펙은 `.kiro/specs/vnew-inner-voice/tasks.md`를 새로 만든다. 짧게 쓴다.
  - WORKLOG는 3줄 이내.

## 배경 (사용자 지시, 2026-11-03)
> 속마음으로 표현하는 구간이 있는데 말풍선이 뾰족하지 않고 동글동글 한 것은 입모양이 움직이는 것이 아니다. 해당 캐릭터 내의 속마음을 표현하는 것이니 이건 캐릭터의 입을 움직이면 안되고 소리로만 표현이 되어야한다

- 웹툰에서 **꼬리(뾰족한 부분)가 없는 둥근·구름 모양 말풍선**은 속마음(생각)이다.
- 그 대사는 영상에서 **입이 움직이면 안 되고**(입 다문 채 표정만), **목소리로만**(속마음 내레이션 / voice-over) 나와야 한다.
- 지금은 모든 대사를 같은 말로 다룬다.
  - 프롬프트: `{이름} (Korean, lips move): "…"`(generate.py `_dialogue_line`), `{이름}: "…"` / `(off-screen voice)`(타임라인 컷 항목).
  - 프리비즈: `speaking`(머리 끄덕임 = 말하는 사람 표시).
  - 그래서 속마음에도 입이 움직인다.

## 요구사항

### 1. 대본 읽기 (`app/src/vnew/prompts.py`의 emit_script 스키마·규칙)
- entries 항목에 `voice` 필드를 더한다. 값은 `"spoken"` | `"thought"`, 기본은 spoken이다.
- 규칙 문구는 아래 뜻을 짧게 담는다.
  - 꼬리 없는 둥근·구름 모양 말풍선, 또는 점선 말풍선의 대사는 `thought`(인물의 속마음, 입은 다문 채)다.
  - 꼬리가 화자를 가리키는 보통 말풍선은 `spoken`이다.
  - 사각 해설 상자(내레이션)는 지금 처리 그대로 둔다(이번 범위 밖).
- 기존 저장 데이터에 `voice`가 없으면 spoken으로 읽는다.

### 2. 대본 편집 화면 (`frontend/public/vnew.js`, ② 대본·상황 줄)
- 대사 칸 옆에 **"속마음"** 체크(또는 토글)를 두어 사용자가 고칠 수 있게 한다.
- 저장 경로가 `voice`를 보존하는지 확인한다. 서버 정리 함수가 모르는 필드를 지우면 허용 목록에 더한다.

### 3. 콘티로 전달 (`script` → `conti` entries → 컷)
- 대사가 콘티 컷으로 옮겨지는 곳에서 `voice`(또는 `thought: true`)를 함께 옮긴다.
- 콘티 화면에 "속마음" 표시를 짧게 보인다(선택, 최소).

### 4. 영상 프롬프트 (`app/src/vnew/generate.py`)
- **`_dialogue_line`(DIALOGUE 줄)**: thought 대사는 아래처럼 쓴다.
  `{이름} (inner thought, voice-over only — mouth stays closed, lips do not move): "…"`
  - spoken 대사는 지금 그대로다.
- **타임라인 컷 항목(약 3024·3140줄)**: thought 대사는 아래처럼 쓴다.
  `{이름} (inner voice-over, lips closed): "…"`
  - 화면 밖 화자 규칙과 겹치면 inner가 우선이다.
- **RULES의 시선·립싱크 문구**에 짧게 더한다: 속마음 대사가 나오는 동안 그 인물의 입은 움직이지 않는다.
  - 이미 바이트 상한 줄이기 단계(cut_prompt steps 등)가 있으니, 줄일 때 이 표시가 빠지지 않게 보호한다.
- **실사·웹툰 두 경로, 통 테이크, 컷 단위 모두** 같은 판정을 쓴다. 판정 함수는 한 곳에 둔다(예: `_is_thought(entry)`).

### 5. 프리비즈 (`app/src/vnew/previz.py`)
- 속마음 대사만 있는 컷의 화자는 `speaking`(머리 끄덕임)을 켜지 않는다.
  - 마네킹이 말하는 것처럼 보이면 영상 모델이 입을 움직인다.
  - 같은 컷에 spoken 대사가 있으면 그 화자는 지금처럼 켠다.

### 6. AI로 고치기 (`retake.py`)
- 새 프롬프트 LLM 지침에 짧게 더한다: 원래 프롬프트의 inner thought 표시를 유지한다.
- 규칙 자르기(`_trim_to_limit`)는 "inner"·"voice-over"·"lips closed"가 든 조각을 지우지 않는다.

## 검사 (가짜 LLM)
- **스키마·규칙:** `voice` enum이 있고, 규칙 문구에 둥근 말풍선과 thought 설명이 있다.
- **대본 저장·읽기:** `voice: thought`가 보존된다. 없으면 spoken으로 읽는다.
- **프롬프트:**
  - thought 대사가 있는 컷의 DIALOGUE에 `inner thought` / `mouth stays closed` 표시가 있고, `lips move`는 없다.
  - spoken 대사는 지금 그대로다. 기존 기대값이 바뀌지 않는다.
  - 같은 컷에 spoken과 thought가 섞이면 각자 표시가 붙는다.
- **프리비즈:** thought만 있는 컷의 화자는 `speaking` False다. 섞이면 spoken 화자만 True다.
- **AI로 고치기 자르기:** inner 조각을 보호한다.
- **화면:** `node --check`. 가능하면 체크 상태 → `voice` 값 순수 함수를 검사한다.

## 범위
- 고칠 것:
  - `app/src/vnew/prompts.py`, `generate.py`, `previz.py`, `retake.py`, `plan.py`(정리 함수가 필요하면)
  - `frontend/public/vnew.js`
  - `tools/check_vpoc.py`
  - `docs/WORKLOG.md`(덧붙이기)
  - `.kiro/specs/vnew-inner-voice/tasks.md`(새로)
- 수정 금지: `vpoc/fal.py`, `k8s/`, `deploy/`, `.gitlab-ci.yml`, `whynot-20261029`

## 완료 조건
- [ ] `node --check frontend/public/vnew.js`
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] WORKLOG 삭제 줄 0, 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`.
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어)
- 바꾼 함수와 화면, 데이터 흐름(대본 → 콘티 → 프롬프트·프리비즈), 프롬프트 예시 1줄, 검사 결과, 남은 이슈
