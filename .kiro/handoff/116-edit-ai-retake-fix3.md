# 작업(116 보완 3): AI로 고치기 초안 — 바이트 상한을 넘으면 LLM에 1번 줄여 달라고 다시 묻기

`.kiro/steering/quality-week.md`를 따르되, 아래 **예산** 절이 우선한다. 116 보완 2까지 반영된 코드 위에서 한다.

## 예산 (2026-11-03, 가장 우선)
- 크레딧을 아낀다.
- 스펙은 `.kiro/specs/vnew-edit-retake/tasks.md`에 몇 줄만 더한다.
- 새 검사는 바뀐 함수만 덮는다. 변형(mutation) 검사는 이번에 바뀐 서버 분기에만 1회 한다.
- 커버리지 측정과 전체 검사 반복은 하지 않는다. `check_vpoc.py` 전체는 마지막에 **1번만** 돌린다(Claude가 다시 돌린다).
- 리뷰와 WORKLOG는 각 3~5줄로 쓴다.

## 배경
dev 유료 시험(2026-11-02)에서 Kling O3 형식(보완 1의 `_run_draft_model`, 상한 2500B)으로 초안을 만들었다. 결과 프롬프트가 3415B여서 `check_prompt`가 problems=1(바이트 초과)을 냈다.
지금은 사용자가 직접 줄여야 [만들기]를 할 수 있다. 상한이 작은 모델을 고르면 자주 생긴다.

## 요구사항
1. 초안 두 경로 모두에 적용한다.
   - 기본 경로: `run_draft`
   - 모델·구간 경로: `_run_draft_model`
   - LLM 결과의 `check_prompt` problems 중 **바이트 초과**가 있으면, 같은 모델에 **딱 1번** 줄이기를 다시 요청한다.
   - 줄이기 요청에 넣을 것: 지금 초안, 상한, "참고 표시(@Video·@Image·@Element)·항목 이름과 순서·바뀐 내용은 지키고 군더더기만 줄여라".
   - 프레임 이미지는 다시 보내지 않는다(비용).
   - 기존 `_rewrite`를 재사용하고 출력 스키마는 같게 한다.
2. 다시 받은 결과로 `check_prompt`를 다시 한다.
   - 더 나아졌으면(바이트 초과가 사라졌거나, problems가 줄었고 바이트도 줄었으면) 그 결과를 쓴다.
   - 아니면 처음 결과와 problems를 그대로 남긴다.
3. 두 번째 호출도 `_charge_llm`으로 원장에 기록한다(같은 draft_id, 합산 또는 별도 줄 — 기존 원장 방식에 맞게).
4. 초안 상태에 `shrunk: true/false`(다시 줄였는지)를 남긴다.
   - 화면 초안 영역에 "길이 상한에 맞게 한 번 줄였습니다" 같은 짧은 안내를 보인다.
   - 화면은 `vnew.js`에서 최소 변경만 한다.
5. 바이트 초과가 아닌 다른 problems만 있으면 다시 묻지 않는다.
6. 로그(`vnew.retake.draft_done`)에 `shrunk=` 필드를 더한다. 프롬프트 원문은 로그에 쓰지 않는다.

## 검사 (check_vpoc rtk 그룹에 이어서, LLM은 가짜)
- 첫 결과가 상한 초과 → 두 번째 호출 1회 → 통과 결과를 쓰고 `shrunk=true`, 원장 2번
- 두 번째도 초과 → 처음 결과 유지(또는 더 짧은 쪽), problems 남음, 호출은 정확히 2회
- 첫 결과가 상한 이내 → 호출 1회, `shrunk=false`
- 바이트 외 problems만 있음 → 다시 묻지 않음
- 두 번째 호출이 예외 → 처음 결과로 done(실패로 바꾸지 않음)
- 기존 검사 기대값은 바꾸지 않는다.

## 범위
- 고칠 것
  - `app/src/vnew/retake.py`
  - `frontend/public/vnew.js`, 필요하면 `vnew.css`
  - `tools/check_vpoc.py`
  - `docs/WORKLOG.md`, 스펙 `tasks.md`
- 수정 금지: `casting.py`, `term_search.py`, `generate.py`(필요 없으면), `.gitlab-ci.yml`, `deploy/`, `k8s/`, `whynot-20261029`

## 완료 조건
- [ ] `node --check frontend/public/vnew.js`
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] 보안 → 성능 → 운영성 리뷰(짧게)
- [ ] 줄 끝 LF, 공백만 바뀐 줄 0, 부산물 0(pyc 포함)
- [ ] WORKLOG 116-fix3

## 제약
- 허용 명령: `python`, `node`. git·aws·kubectl·네트워크·유료 API 금지. 커밋하지 않는다. 비밀값을 쓰지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (마지막에 반드시, 한국어)
- 바꾼 파일과 함수
- 검사 결과
- 남은 이슈
