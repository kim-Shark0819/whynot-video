# 작업(116 보완 10): AI로 고치기 — 소리 없는 다시 만든 버전은 원본 소리를 쓴다

`.kiro/steering/quality-week.md`를 따르되 **예산**이 우선한다. 116 보완 9 위에서 한다.

## 예산 (가장 우선)
- 바뀐 분기만 검사한다. 변형 검사는 새 판정 함수에만 1회 한다. 커버리지 측정은 하지 않는다.
- `check_vpoc.py` 전체는 마지막에 1번만 돌린다.
- **`docs/WORKLOG.md`와 스펙 `tasks.md`는 맨 끝에 덧붙이기만 한다.** WORKLOG는 3줄 이내.

## 배경 (운영 실측, 2026-11-03)
- 운영 "위험한 편의점 test1" 테이크 1(웹툰 30초, Seedance, 소리 있음)에서 컷 2(4.0–8.0초)를 Kling O3 "이 구간만"으로 다시 만들었다. v2 결과는 4.04초, 1280×720이다.
- `_ensure_meta` 결과 v2는 **`hasAudio: False`** 이다.
  - Kling O3 video-to-video edit은 프리비즈 녹화(소리 없음)를 @Video1로 받는다. 그래서 결과에 소리가 없다.
- ⑦ 타임라인에서 [바꾸기]로 컷 2 자리를 v2로 바꾸면 그 4초의 소리 줄이 빈다(`is-silent`). 내보내기에서도 그 4초가 무음이 되고 **대사가 빠진다.**

## 요구사항
1. **원본 소리 대체 규칙(서버, `app/src/vnew/edit.py`, 필요하면 `retake.py`)**
   - 재료가 아래 조건을 모두 만족하면, 소리를 그 칸 **원본 결과(v1, 지금 결과)** 의 같은 녹화 시각 구간에서 가져온다.
     - AI로 고치기 버전(material group `"retake"`)이다.
     - 파일에 소리가 없다(`hasAudio is False`).
     - 원본 결과에는 소리가 있다.
   - 시각 대응은 재료가 이미 가진 정보로 계산한다(`range.start/end`, `trimTo`, 칸 안 시각 등). 계산을 새로 만들지 말고 기존 `_time_map`·`_range_of` 계열을 재사용한다.
   - 구간 버전(`scope: range`): 원본의 `range.start`부터 클립 `in`만큼 더한 위치에서 시작한다.
   - 전체 버전(`scope: full`): 같은 칸 시각이다. 길이가 다르면(template 형식 trimTo) 짧은 쪽에 맞추고, 남는 부분은 무음이다.
   - 재료 보기(edit 재료 목록 응답)에 `audioFrom: {sourceKey, offset}`(또는 같은 뜻의 필드)를 더한다. 화면과 내보내기가 같은 판정을 쓰게 한다.
2. **내보내기(`_export_segment` 호출부)**
   - 위 규칙이 적용되는 클립은 영상은 v2에서, 소리는 원본에서 같은 길이·속도(`_speed_of`, atempo)로 가져온다.
   - `muted` 클립은 지금처럼 무음이다.
   - `with_audio` 판정에도 이 대체 소리를 포함한다.
   - 원본을 받는 것(`_download`)은 지금 로컬 캐시(`local`)를 재사용한다.
   - `vpoc/stitcher.py`(또는 실제 위치)는 필요할 때만 최소로 고친다. 고치면 이유를 보고한다.
3. **타임라인 화면(`frontend/public/vnew.js`, 최소)**
   - `audioFrom`이 있는 클립은 소리 줄을 `is-silent`가 아니라 원본 소리 파형으로 보인다.
   - 파형을 그리기 어렵다면 소리 있음 표시 + 제목 "원본 소리 사용"만 한다.
   - 미리보기 재생에서도 그 구간에 원본 소리가 들리게 할 수 있으면 한다. 어렵다면 하지 않고, 이유를 보고한다(내보내기가 우선이다).
4. **바꾸지 않는 것**
   - 소리가 있는 버전.
   - fal 호출(새 과금 없음).
   - Kling 제출 본문.
   - 저장된 기존 데이터. 판정은 읽을 때 계산하고, 기존 편집 문서를 고쳐 쓰지 않는다.

## 검사 (편집·rtk 그룹에 이어서, 가짜 meta)
- **구간 v2(hasAudio False), 원본 hasAudio True**
  - 재료 보기에 `audioFrom = 원본 키`, `offset = range.start + in`이 있다.
  - 내보내기에서 그 클립의 소리 원천이 원본이고 offset이 맞다(가짜 `_export_segment` 인자로 확인).
  - `with_audio`가 True다.
- **v2에 소리가 있으면** `audioFrom`이 없고 지금 동작 그대로다.
- **원본도 소리가 없으면** `audioFrom`이 없고 무음이다.
- **muted 클립**은 대체하지 않는다.
- **속도 1.5×**: 소리도 같은 속도 인자로 넘어간다.
- **vnew.js** 문법 확인(`node --check`)과, 가능하면 소리 줄 클래스 판정 순수 함수 검사를 한다.

## 범위
- 고칠 것:
  - `app/src/vnew/edit.py`
  - `app/src/vnew/retake.py`(필요 시)
  - `frontend/public/vnew.js`(최소)
  - 소리 자르기 함수 파일(필요 시, 최소)
  - `tools/check_vpoc.py`
  - `docs/WORKLOG.md`(덧붙이기)
  - `.kiro/specs/vnew-edit-retake/tasks.md`(덧붙이기)
- 수정 금지: `vpoc/fal.py`, `generate.py`의 제출 경로, `k8s/`, `deploy/`, `.gitlab-ci.yml`

## 완료 조건
- [ ] `node --check frontend/public/vnew.js`
- [ ] `python -X utf8 -B tools/check_vpoc.py` → "전부 통과"
- [ ] WORKLOG·tasks.md 삭제 줄 0, 줄 끝 LF, 부산물 0

## 제약
- 허용 명령: `python`, `node`, (로컬 ffmpeg가 이미 검사에 쓰이면 그대로).
- git·aws·kubectl·네트워크·유료 API는 쓰지 않는다. 커밋하지 않는다.
- 비대화형 실행이다. 결과 보고까지 한 번에 끝낸다. **할 일 목록 도구는 쓰지 않는다.**

## 결과 보고 (한국어, 짧게)
- 바꾼 함수, 대체 규칙(시각 계산식), 화면 처리 범위, 검사 결과, 남은 이슈
