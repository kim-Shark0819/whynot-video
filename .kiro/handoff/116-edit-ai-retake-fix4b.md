# 작업(116 보완 4b): fal "Generation timeout"(504)을 끝난 실패로 처리

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 4 위에서 한다.

## 예산 (가장 우선)
- 새 검사는 바뀐 분기만 덮는다. 변형 검사는 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다. WORKLOG는 3줄로 쓴다.

## 배경 (dev 실측)
보완 4의 되살리기는 dev v3(Kling O3)를 1번 조회했다.
- 로그: `vnew.retake.recover_failed ... err=VpocError`
- pod 안에서 직접 확인했다.
  - fal status는 `COMPLETED`다.
  - 결과 본문 GET(`fal._response_url`)은 `VpocError kind=HTTP504 "Generation timeout"`이다.
- 즉 fal 쪽 생성이 시간 초과로 끝나 **결과 영상이 없다**. 되살릴 것이 없다.
- 그런데 지금은 예외로 취급한다. 그래서 24시간 동안 60초마다 다시 묻고, 화면은 계속 "다시 확인하는 중입니다"를 보인다.
- `_poll_until_done`도 같은 504를 일시 오류로 보고 5번 재시도한 뒤 예외로 끝낸다.

## 요구사항
1. `retake.py`에 작은 판정 함수를 둔다(예: `_terminal_fal_error(exc)`).
   - `VpocError`이고 kind가 `HTTP504`이며 메시지에 "Generation timeout"(대소문자 무시)가 들어가면 끝난 실패다.
   - fal.py는 고치지 않는다.
2. `run_recover`: 끝난 실패면 아래처럼 처리한다.
   - `timedOut`을 지운다(더 묻지 않음).
   - 오류 문구는 "모델 쪽에서 영상 생성 시간이 초과되어 결과가 없습니다. 다시 시도해 주세요."로 한다.
   - 기록(log_result)도 failed로 맞춘다.
   - 로그 `vnew.retake.recover_gone`을 남긴다.
3. `_poll_until_done`: 조회 중 끝난 실패가 나오면 재시도 없이 바로 같은 문구로 failed가 된다(`timedOut`은 넣지 않음). 로그 `vnew.retake.job_failed kind=gen_timeout`.
4. 화면 변경은 없다(서버 문구만 바뀜).

## 검사 (rtk-x 이어서)
- 되살리기: 가짜 poll이 HTTP504 Generation timeout을 내면 → failed, timedOut 없음, 새 문구. 다음 보기에서 조회 0.
- 폴링: 같은 예외 → 즉시 failed. poll 호출 1번, 새 제출 0.
- 다른 VpocError(예: HTTP502)는 지금 동작 그대로.
- 기존 기대값은 바꾸지 않는다.

## 범위
- 고칠 것: `app/src/vnew/retake.py`, `tools/check_vpoc.py`, `docs/WORKLOG.md`, 스펙 `tasks.md`
- 그 밖은 수정 금지

## 완료 조건
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`. git·aws·kubectl·네트워크·유료 API 금지. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 짧게)
- 바꾼 함수, 검사 결과, 남은 이슈
