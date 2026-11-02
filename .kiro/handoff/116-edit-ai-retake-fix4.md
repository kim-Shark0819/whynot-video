# 작업(116 보완 4): AI로 고치기 — 오래 걸리는 모델 결과를 놓치지 않기(시간 초과 → 되살리기)

`.kiro/steering/quality-week.md`를 따르되 **예산** 절이 우선한다. 116 보완 3b 위에서 한다.

## 예산 (가장 우선)
- 새 검사는 바뀐 분기만 덮는다. 변형 검사는 바뀐 판정 분기에만 1회 한다.
- 커버리지 측정과 전체 검사 반복은 하지 않는다. `check_vpoc.py` 전체는 마지막에 1번만 돌린다.
- WORKLOG와 리뷰는 각 3~5줄로 쓴다.

## 배경 (dev 실측, 2026-11-03)
Kling O3(`fal-ai/kling-video/o3/standard/video-to-video/edit`)로 v3를 제출했다(16:43:20Z, 유료 $2.10).
- `_poll_until_done`은 `POLL_MAX_S=1800`(30분)이 지나자 failed로 끝냈다. 로그는 `vnew.retake.timeout`, 문구는 "결과를 30분 넘게 받지 못했습니다".
- 그 뒤 fal 상태를 직접 조회하니 **COMPLETED**였다. 돈을 내고 만든 결과를 버린 셈이다.

## 요구사항
1. 모델별 최대 대기 시간
   - fal Kling 계열(endpoint에 `kling` 포함)은 **75분**, 그 밖은 지금처럼 30분.
   - 상수나 작은 함수로 둔다(예: `_poll_max_s(endpoint)`).
   - 실패 문구의 분 수도 그 값으로 만든다.
2. 시간 초과 버전 되살리기
   - 시간 초과로 failed가 된 버전은 상태에 `timedOut: true`를 남긴다. 그리고 `invocationArn`, `outputPrefix`를 보존한다.
   - 패널 보기(GET `/edit/retake/<mid>`)를 만들 때 그 칸에 `timedOut` 버전이 있으면 fal 조회를 **딱 1번** 한다.
     - 버전마다 최소 60초 간격이다. 마지막 조회 시각을 상태에 둔다.
     - 제출 뒤 24시간이 지났으면 더 묻지 않는다.
   - 조회 결과에 따라:
     - Completed: 기존 완료 경로(`generate._find_output_key`, `_finish(status="completed")`, `log_result`)로 completed로 바꾼다. `timedOut`을 지우고 로그 `vnew.retake.recovered`를 남긴다.
     - InProgress: 그대로 둔다.
     - Failed나 예외: 그대로 둔다(예외 이름만 로그).
   - 보기 응답이 느려지지 않게 하는 방법은 기존 코드 관례를 따라 고른다(백그라운드 스레드 `_spawn` 또는 짧은 타임아웃 동기 호출). 결과 보고에 이유를 적는다.
   - 화면에는 timedOut 버전에 "결과를 다시 확인하는 중입니다 — 잠시 뒤 [다시 불러오기]" 같은 짧은 안내를 보인다. `vnew.js` 최소 변경.
3. 이번 dev v3도 이 경로로 되살아나야 한다(배포 뒤 패널을 열면 completed).
   - 기존 상태에는 `timedOut` 표시가 없다. 그러니 "status=failed이고 error가 시간 초과 문구로 시작하고 invocationArn이 있음"도 timedOut으로 본다.
4. 이중 과금 없음: 되살리기는 조회만 한다. 새 제출은 절대 하지 않는다.

## 검사 (rtk 그룹에 이어서, fal 가짜)
- Kling endpoint는 75분, Seedance는 30분.
- timedOut 버전 + 가짜 poll Completed → 보기 뒤 completed, outputKey가 있음, 제출 호출 0.
- InProgress, Failed, 예외 → 상태 그대로, 60초 안에 다시 보면 조회 0.
- 24시간이 지나면 조회 0.
- 옛 상태(timedOut 표시 없음, 시간 초과 문구) → 되살리기 대상이 된다.
- 기존 검사 기대값은 바꾸지 않는다. 바꿔야 하면 이유를 보고에 적는다.

## 범위
- 고칠 것: `app/src/vnew/retake.py`, `frontend/public/vnew.js`(최소), `tools/check_vpoc.py`, `docs/WORKLOG.md`, 스펙 `tasks.md`
- 수정 금지: `vpoc/fal.py`, `generate.py`(필요 없으면), `casting.py`, `term_search.py`, `.gitlab-ci.yml`, `deploy/`, `k8s/`, `whynot-20261029`

## 완료 조건
- [ ] `node --check frontend/public/vnew.js`
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] 보안 → 성능 → 운영성 리뷰(짧게)
- [ ] 줄 끝 LF, 부산물 0
- [ ] WORKLOG 116-fix4

## 제약
- 허용 명령: `python`, `node`. git·aws·kubectl·네트워크·유료 API 금지. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어)
- 바꾼 함수, 되살리기 흐름(동기·비동기와 그 이유)
- 검사 결과
- 남은 이슈
