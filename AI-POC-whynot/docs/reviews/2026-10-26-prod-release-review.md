# 운영 배포 전 3단계 리뷰 — main `0271fdb0` → 운영 (2026-10-26, Kiro 072)

- 대상: `.kiro/handoff/072-prod-diff.patch`(ark·fal·routes·settings·stitcher·index.html), vnew 차단 여부, 070 동작(`resolve_model`·`choices`·`selected`·`unavailable_reason`·`vnew/plan.py`·`is_ark_job`).
- 방법: 코드 읽기만 했다. 명령·테스트·커버리지는 실행하지 않았다. 줄 번호는 현재 작업 트리 기준이다.
- 운영 설정 전제(`deploy/config.prod.env`): `VNEW_ENABLED=false`(149), `VPOC_MODEL_PICKER_ENABLED=true`(136), `VPOC_DUAL_STYLE_ENABLED=true`(144), `VPOC_RAW_ERRORS=false`(123), `VPOC_ARK_SEEDANCE_ENABLED` 미설정(=false).
- 전제 정정: 운영 앱 파드 메모리 한도는 512Mi가 아니라 **8Gi**다(`config.prod.env:112 APP_MEM_LIMIT=8Gi`). 512Mi는 기본값·개발계 기준이다.

## 요약

### 운영 배포 전 필수 수정

코드 수정 필수 항목은 **없음**. 배포 절차에서 반드시 지킬 것 2건:

1. **fal Seedance 2.5 i2v 등록 시점을 앞당긴다(O-2).**
   - 배포 전, 즉 현 운영 코드에서 등록한다. 현 운영 코드도 `image-to-video` 등록을 허용한다(패치 전 `FAL_VIDEO_CATEGORIES`).
   - 그게 어려우면 롤아웃 직후, 사용자가 접근하기 전에 등록한다.
   - 등록 항목의 `caps.durations`(4~15초를 덮는지)·`resolutions`·`endImageField`·`promptMaxLength`를 확인한다.
   - 미등록 상태에서는 옛 Seedance 계획이 전부 Luma로 바뀐다. 그러면 single 계획 생성이 409 CostMismatch로 막히고, 키 장면 계획은 Luma 텍스트 전용으로 과금된다.
2. **진행 중인 BytePlus 회차가 0건일 때 배포한다(O-1).**
   - 대상: `steps.4.videoModel`이 `seedance-2-0`/`seedance-2-5`이고, 샷·regen이 `queued`(또는 다음 샷 대기)인 계획.
   - 이런 계획은 배포 뒤 남은 샷 제출이 `ModelUnavailable`로 실패한다. "다시 만들기"로도 복구되지 않는다.
   - 0건을 보장할 수 없으면 `submit_shot`에 `resolve_model`을 적용하는 코드 수정이 먼저다.

### 단계별 발견 사항 수

| 단계 | 높음 | 중간 | 낮음 |
|---|---|---|---|
| 1 보안 | 0 | 1 | 3 |
| 2 성능 | 0 | 0 | 5 |
| 3 운영성 | 0 | 6 | 6 |

---

## 1단계 — 보안

### S-1 (중간) fal 모델 등록·해제를 로그인 사용자 누구나 할 수 있다
- 근거:
  - `app/src/vpoc/routes.py:4622-4643`: `fal_register`·`fal_unregister`에는 `require_auth`와 `_require_picker`(4551, 플래그 검사)만 있고 관리자 검사가 없다.
  - `vpoc/fal.py:299-313`(`unregister`)은 레지스트리 전체(전 사용자 공용)를 바꾼다.
- 실패 시나리오:
  - 일반 사용자가 `DELETE /api/vpoc/fal/models/bytedance/seedance-2.5/image-to-video`를 보낸다. 30초 캐시(`fal.py:41`)가 지나면 모든 파드에서 `ark.resolve_model`이 Luma를 돌려준다(`ark.py:177-179`).
  - 옛 Seedance 계획은 전부 Luma로 생성된다.
  - 진행 중인 fal 회차의 대기 샷은 Luma로 제출되고, 원장에는 0달러로 남는다(O-6).
  - 등록 API는 새로 `reference-to-video`·`video-to-video`도 받는다(`settings.py:162-167`).
- 배경: 이번 배포 전부터 있던 문제다. 070으로 운영 Seedance 전체가 fal 레지스트리 항목 하나에 기대게 되어 영향이 커졌다.
- 권장 조치: 등록·해제를 관리자 역할로 제한한다. 그 전까지는 운영 사용자 범위를 확인하고, 레지스트리 변경 로그(`addedBy`)를 점검한다.
- 배포 전 필수: 아니오(권장, 다음 변경 1순위).

### S-2 (낮음) 금지 목록(Seedance 2.0 Fast)은 등록 순간에만 검사한다
- 근거:
  - 등록 시점 검사: `vpoc/fal.py:245-251`, `settings.py:180-195`.
  - `unavailable_reason`(`fal.py:948-959`)·`submit`(`fal.py:1069-1075`)·`ark.choices`는 다시 검사하지 않는다.
- 실패 시나리오: 금지 지시(10/22) 이전에 운영 레지스트리에 `…seedance…2…fast…`가 등록돼 있었다면 ② 목록에 그대로 나오고 제출도 된다.
- 권장 조치:
  - `fal.unavailable_reason`에 `settings.is_denied_endpoint` 검사를 더한다.
  - 당장은 등록 작업(필수 1) 때 운영 레지스트리 목록을 함께 확인한다.
- 배포 전 필수: 아니오(레지스트리 점검으로 대체).

### S-3 (낮음) 옛 Seedance 계획의 외부 전송처가 사용자 재선택 없이 BytePlus에서 fal로 바뀐다
- 근거:
  - `vpoc/ark.py:164-179`(`resolve_model`), `182-195`(`selected`), `routes.py:4069`(generate가 `selected` 사용).
  - 전송 방식은 그대로 data URI다: `fal.py:1022-1030`(이미지 `_data_uri`), `fal.py:1118-1125`·`1128-1180`(애니 변환 영상 `data:video/mp4`).
  - 결과는 fal 응답 URL에서 받아 우리 버킷으로 바로 복사한다(`fal.py:1275-1281`).
- 실패 시나리오: 사용자가 BytePlus Seedance로 고른 계획의 인물 사진·키 장면·프롬프트가 다음 생성부터 fal로 전송된다.
- 권장 조치: 설계자 지시 범위 안이다. 고객 데이터 처리 고지가 공급자별로 되어 있다면 fal을 포함하는지만 확인한다. Seedream(이미지)은 계속 BytePlus로 간다(`seedream.py:53-68`).
- 배포 전 필수: 아니오.

### S-4 (낮음, vnew 전용·운영 꺼짐) 프리비즈 오버레이의 style 속성 값 이스케이프가 약하다
- 근거: `frontend/public/vnew-previz.js:891`, `903`. `escapeAttr`가 `"<>`만 지우므로 `item.color`에 `red;background:url(...)` 같은 CSS를 넣을 수 있다.
- 실패 시나리오: vnew가 켜진 배포에서 인물 색 값을 조작하면 CSS가 주입된다. 운영은 `VNEW_ENABLED=false`라 이 코드가 실행되지 않는다.
- 권장 조치: 색 값을 `^#[0-9a-fA-F]{3,8}$`로 검증한다.
- 배포 전 필수: 아니오.

### 문제없음으로 확인한 항목
- 비밀값:
  - fal 키는 `_auth_header`(`fal.py:129-133`) 헤더에만, ARK 키는 `_call` 헤더(`ark.py:381`)에만 쓴다.
  - 새 로그 `vpoc.fal.prompt_trimmed`(`fal.py:1008`)는 길이만 남긴다. 금지 모델 오류 문구는 고정 문자열이다.
  - 응답·프롬프트에 키가 들어가는 경로가 없다.
- 오류 노출:
  - 운영 `RAW_ERRORS=false`라 외부 원문(`HTTP<code>`)은 감춰진다(`errors.py:76-83`).
  - 새 문구 "기존 Seedance 는 사용 중지 — fal Seedance 를 쓰세요"(`ark.py:77`)는 `ModelUnavailable`(사용자 노출 kind, `errors.py:39-49`)로 보인다. 내부 ID는 없다.
- 입력 검증:
  - ② `videoModel`은 `choices()`에서 available인 것만 받는다(`routes.py:3651-3658`). `totalSeconds`는 받아들인 모델 범위로 검사한다(3663-3690).
  - 카탈로그 category는 화이트리스트로 거른다(`routes.py:4570-4577`). vnew 입력은 `resolve_model` 뒤 화이트리스트로 검사한다(`vnew/plan.py:459-471`).
- 경로·명령 주입:
  - ffmpeg는 리스트 인자에 shell 없이 timeout을 걸어 실행한다(`stitcher.py:51-57`). `_threads_args`는 `int()`로 변환한다(`stitcher.py:61-70`).
  - fal 호출 호스트는 `FAL_QUEUE_URL`·`FAL_API_URL`로 고정이고, 조회 인자는 `urlencode`를 거친다(`fal.py:316-327`, `364-375`). endpoint 문자열로 다른 호스트에 키를 보낼 수 없다.
- 프런트 XSS:
  - `vpoc.js`에는 `innerHTML`이 없다(검색 0건).
  - `index.html:37` 추가분은 같은 출처 module 스크립트다. CSP는 기존과 같이 Report-Only다(`frontend/nginx.conf`, 이번 변경 아님).
  - vnew-previz의 라벨 텍스트는 `escapeHtml`을 거친다(`vnew-previz.js:893-901`).
- vnew 차단(라우트·프런트):
  - `/api/vnew`는 `VNEW_ENABLED`일 때만 등록된다(`server.py:341`, `6996-7011`). video_new 트랙은 꺼져 있으면 거절된다(`project_tracks.py:81-85`, `405`).
  - 프런트는 `window.__WHYNOT_RUNTIME__.vnewEnabled`(`k8s/workloads.yaml.tmpl:12`)로 판단한다. 꺼져 있으면 vnew 화면으로 이동하지 않고(`app.js:6353`), 버튼은 `is-hidden`으로 시작하며 토글하지 않는다(`index.html:1768`, `1821`, `app.js:7094-7095`).
  - `vnew-previz.js`는 로드돼도 `window.vnewPreviz`(1204)를 정의만 하고, 네트워크 호출이나 DOM 조작을 하지 않는다.

---

## 2단계 — 성능

### P-1 (낮음) vnew가 꺼진 운영에서도 모든 페이지가 three.js를 받는다
- 근거:
  - `frontend/public/index.html:37`: `<script type="module" src="/vnew-previz.js">`를 조건 없이 로드한다.
  - `vnew-previz.js:27`: 첫 줄에서 `import * as THREE from "/vendor/three/three.module.min.js"`를 정적으로 import한다.
- 실패 시나리오: 운영 사용자 모두 첫 방문 때 three.js를 내려받고 파싱한다. 화면 기능은 그대로다.
- 문서 불일치: 배포 계획서 2절의 "three.js import map 4줄 … import 전에는 로드하지 않는다"는 실제 코드와 다르다(O-10).
- 권장 조치: `app.js`에서 `VNEW_ENABLED`일 때만 `import("/vnew-previz.js")`를 동적으로 부른다.
- 배포 전 필수: 아니오.

### P-2 (낮음) three.js import URL의 버전 쿼리가 치환되지 않는다
- 근거:
  - `vnew-previz.js:27`의 `?v=__ASSET_VERSION__`.
  - `frontend/Dockerfile:20-27`의 치환 대상은 `index.html`·`app.js`·`vpoc-smoke.html`뿐이다.
  - `nginx.conf` 정적 자산은 `max-age=2592000, immutable`이다.
- 실패 시나리오: three.js를 교체해도 브라우저가 최대 30일 동안 옛 파일을 쓴다. 지금 버전은 고정이라 당장 영향은 없다.
- 권장 조치: 치환 대상에 `vnew-previz.js`를 더하거나, 파일명에 버전을 넣는다.
- 배포 전 필수: 아니오.

### P-3 (낮음) 레지스트리 읽기 실패를 빈 목록으로 30초 캐시한다
- 근거: `vpoc/fal.py:191-214`. `_registry_read`가 예외를 삼키고 `[]`를 돌려주며, `registered`가 그 값을 30초 캐시한다.
- 실패 시나리오:
  - S3 일시 오류가 나면 30초 동안 `resolve_model`이 Luma를 돌려준다(`ark.py:177-179`).
  - 그 사이 generate가 들어오면 회차 모델이 `luma-ray2`로 확정된다(`routes.py:4069`, `4128` 근처 `videoModel` 저장).
  - single 계획이면 CostMismatch가 나고, 그 밖에는 조용히 Luma로 생성된다.
- 권장 조치: 실패는 캐시하지 않는다. 매핑 대상이 사라져 Luma로 떨어질 때 generate가 409로 알리는 방안을 검토한다.
- 배포 전 필수: 아니오.

### P-4 (낮음) 단가(pricing) 조회 실패는 캐시되지 않는다
- 근거:
  - `vpoc/fal.py:458-483`: `VpocError`면 캐시 없이 None을 돌려준다. `pricing_for`(485-499)는 레지스트리에 단가가 없으면 매번 다시 부른다.
  - 호출 타임아웃은 60초다(`fal.py:53`).
  - `settings.shot_usd`가 `plan_view`(④ 조회 폴링)마다 이 경로를 탄다.
- 실패 시나리오: 등록 때 단가 조회에 실패한 항목(`fal.py:273`)이 있으면, fal 단가 API 장애 동안 계획 조회 1회에 최대 60초가 걸린다. gunicorn timeout도 60초다.
- 권장 조치: 실패도 몇 분 동안 캐시한다(음성 캐시).
- 배포 전 필수: 아니오(등록 직후 레지스트리에 `pricing`이 채워졌는지 확인하면 피할 수 있다).

### P-5 (낮음, vnew 전용) `-threads 1`이 디코더에만 걸린다
- 근거: `stitcher.py:204`, `305`, `pad_to_seconds`. `-threads`를 `-i` 앞에 두면 입력(디코드) 옵션이 되어 libx264 인코더 스레드는 줄지 않는다.
- 실패 시나리오: vnew 컷 준비의 OOM 완화 효과가 기대보다 작다. 운영은 vnew가 꺼져 있어 호출되지 않는다(`vnew/generate.py:1386-1402`에서만 `threads=1`).
- 권장 조치: 출력 쪽(`-c:v libx264` 뒤)에도 `-threads 1`을 넣는다.
- 배포 전 필수: 아니오.

### 문제없음으로 확인한 항목
- 메모리(운영 8Gi):
  - 애니 변환 참고 이미지는 이전과 같이 최대 4장이다. 구성만 패널 2장+인물 최대 2장에서 패널 3장+인물 1장으로 바뀌었다(`routes.py:2357-2385`).
  - stitcher 새 인자는 기본값 None이라 vpoc 쪽 ffmpeg 명령이 그대로다(`stitcher.py:61-70`). vpoc는 `prepare_for_anime`만 부른다(`routes.py:2465`).
  - `pad_to_seconds`는 vnew만 호출한다(`vnew/generate.py:1394`).
- 스레드:
  - vpoc diff에는 새 스레드가 없다.
  - vnew 스레드는 모두 함수 안에서만 시작한다(`characters.py:1587`·`1645`·`1808`, `generate.py:1006`·`1318-1335`, `locations.py:2846`). import 때 실행되는 시작 코드가 없다(모듈 최상위 검색).
  - 블루프린트가 등록되지 않으므로 운영에서는 시작 경로가 없다. vpoc 모듈은 vnew를 import하지 않는다(검색 0건).
- 외부 호출:
  - `resolve_model`·`choices`·`selected`는 30초 캐시 레지스트리만 본다(`fal.py:204-236`). 계획 조회마다 네트워크 호출이 늘지 않는다.
  - BytePlus 조회 타임아웃(60초·다운로드 300초, `ark.py:385`·`529`)과 fal(60·300초, `fal.py:53-54`)은 그대로다.
- 프롬프트 바이트 자르기(`fal.py:645-666`)는 길이에 비례하는 처리라 부담이 없다.

---

## 3단계 — 운영성

### O-1 (중간, 필수 절차 2) 진행 중 BytePlus 회차의 남은 제출이 배포 뒤 전부 실패한다
- 근거:
  - `routes.py:2137-2138`: `submit_shot`이 `resolve_model`을 거치지 않고, 회차에 저장된 `steps.4.videoModel`을 그대로 쓴다.
  - `ark.py:76-77`: 이 값이 `seedance-2-x`면 `is_ark`는 True이고 `unavailable_reason`은 "사용 중지"를 돌려준다.
  - `ark.py:436-438`: `ark.submit`이 400 `ModelUnavailable`을 올린다.
  - 같은 값을 쓰는 경로: 대기 샷 제출(`refresh_shots` 2590~), "다시 만들기" `retry`(`routes.py:4186~`), regen(`regen.py:588-597`), 키프레임 실패 자동 재제출 E5(`routes.py:2674-2681`).
- 실패 시나리오:
  - 배포 시점에 샷별 BytePlus 회차가 진행 중이다(`ARK_MAX_INFLIGHT=1`이라 남은 샷은 `queued`).
  - 롤아웃 뒤 드라이버나 화면 조회가 대기 샷을 제출하면 그 샷은 `failed`가 되고 "Seedance 2.5: 기존 Seedance 는 사용 중지 — fal Seedance 를 쓰세요"가 보인다.
  - "다시 만들기"도 같은 오류로 실패한다. ③에서 다시 승인해 새 회차를 만들어야 하므로, 이미 끝난 샷까지 다시 과금된다.
  - 이미 `submitted`인 BytePlus 작업은 정상 완료된다(아래 확인 항목).
- 권장 조치:
  - 배포 직전에 운영 계획 중 해당 회차가 0건인지 확인한다. 아니면 끝날 때까지 기다린다.
  - 코드로 막으려면 `submit_shot`에서 `video_model in ARK_MODEL_IDS and not ARK_SEEDANCE_ENABLED`일 때 `resolve_model`을 적용하고 `steps.4.videoModel`을 갱신한다. regen 금액 계산(`regen.py:588`)도 같은 함수로 맞춘다.
- 배포 전 필수: **예(절차)**. 0건을 확인하지 못하면 코드 수정이 필수다.

### O-2 (중간, 필수 절차 1) fal 모델 미등록 상태로 배포하면 Luma로 바뀌고, 부작용 세 가지가 생긴다
- 동작 확인: 미등록·키 없음·사용 불가면 `resolve_model`이 `luma-ray2`를 돌려준다(`ark.py:176-179`). Luma로 대체되는 것 자체는 의도대로다.
- 부작용 1. single 계획 생성이 409로 막힌다.
  - 근거: 화면 금액은 `_single_total(step2, 현재 모델)`로 낸다(`routes.py:641`, `650-661`). 서버는 `master.totalS`로 다시 계산한다(`routes.py:4085`, `4103-4108`).
  - `master.totalS`는 ③ 준비 때 옛 모델(Seedance 4~15초) 기준으로 정해졌다(`routes.py:1822-1823`). 그때는 저장값 그대로를 썼고 resolve하지 않았다.
  - 시나리오: Seedance로 10초짜리 ③을 만든 계획은 Luma(5초 고정, `settings.py VIDEO_DURATION_RANGE`)로 바뀌면 화면 금액 5초분과 서버 금액 10초분이 어긋난다(dual 변환 금액 포함). "새로고침해 주세요" 409가 반복되고, ③을 다시 만들기 전까지 생성할 수 없다.
  - fal 모델로 바뀌어도 `caps.durations`가 `master.totalS`를 덮지 못하면 같은 일이 생긴다(`_single_total` 273-284의 clamp).
- 부작용 2. 키 장면(keyframes) 계획은 Luma 텍스트 전용이 된다.
  - 근거: 키 장면 계획의 샷은 `useKeyframe=False` → `mode="text"`(`routes.py:4153`). Luma 경로는 시작 이미지 없이 구간 문장만 보낸다(`routes.py:2116`, `2257-2267`).
  - 시나리오: 이미 키 장면을 완성한 30초 계획(구간 6개)을 생성하면, 키 장면 그림과 인물이 반영되지 않은 Luma 5초 영상 6개가 만들어지고 과금된다. 720p면 구간당 $7.5다.
- 부작용 3. 옛 샷별 계획은 샷마다 Luma 단가로 과금된다(금액은 화면과 같다).
- 권장 조치: fal Seedance 2.5 i2v를 배포 전에 등록한다. 현 운영 코드도 i2v 등록을 허용하고, 개발에서 같은 endpoint 등록에 성공했다(WORKLOG 070). 등록 뒤 caps의 durations가 4~15초를 덮는지 확인한다.
- 배포 전 필수: **예(절차)**. 계획서 4단계 4(배포 후 등록)를 "배포 전 등록"으로 바꾼다.

### O-3 (중간) 플래그로 되돌리기가 배선돼 있지 않다
- 근거:
  - `VPOC_ARK_SEEDANCE_ENABLED`는 `vpoc/settings.py:134`에만 있다.
  - `k8s/workloads.yaml.tmpl`은 env를 하나씩 나열한다(예: 283-294). 이 값은 목록에 없고, `deploy/config.env.example`에도 없다(검색 결과 코드·테스트 2곳뿐).
- 실패 시나리오: 문제가 생겨 `config.prod.env`에 `VPOC_ARK_SEEDANCE_ENABLED=true`만 넣으면 파드에 전달되지 않아 아무 변화가 없다. 템플릿 수정이 필요하므로 MR·CI·운영 job을 다 거쳐야 한다.
- 권장 조치:
  - 이번 배포 전에 템플릿에 `- name: VPOC_ARK_SEEDANCE_ENABLED / value: "${VPOC_ARK_SEEDANCE_ENABLED}"`를 더하고, `config.env.example`에 `VPOC_ARK_SEEDANCE_ENABLED=false`를 더한다.
  - 그러면 되돌리기가 config 한 줄과 운영 job으로 끝난다.
  - 되돌려도 BytePlus 키 Secret(`workloads.yaml.tmpl:337-342`)이 남아 있어야 한다.
- 배포 전 필수: 아니오. 전체 롤백(ai-deploy 커밋 revert, 이미지 `prod-ae21dc13`)이 있다. 다만 강력히 권장한다.

### O-4 (중간) 옛 Seedance 계획 프롬프트가 2,500바이트에서 조용히 잘린다
- 근거:
  - fal 기본 상한은 `fal.py:50 DEFAULT_PROMPT_MAX=2500`이다. 스키마에 maxLength가 없으면 이 값을 쓴다.
  - `_trim_prompt`(645-666)가 바이트 기준으로 문장 경계에서 자른다(`build_payload` 997-1009).
  - BytePlus 경로는 4,000자까지 받고 넘으면 "자르지 않고 멈춘다"(`ark.py:31`, `440-447`). 마스터 프롬프트도 4,000자 예산으로 만든다(`routes.py:2084`, `compose.master_video_prompt(max_chars=ark.MAX_PROMPT_CHARS)`).
  - WORKLOG 069에 따르면 fal Seedance OpenAPI에는 prompt 최대 길이가 없다.
- 실패 시나리오:
  - 인물 고정 문장과 비트별 지시가 붙어 3,000자가 된 single·한 번에 만들기 프롬프트는 뒤쪽 비트가 잘린 채 제출된다.
  - ④ "보낸 문장"(`routes.py:2316 sentPromptEn`)과 jobs 기록에는 자르기 전 전체 문장이 남아, 실제로 보낸 문장과 다르다.
- 권장 조치:
  - 등록 항목의 `caps.promptMaxLength`를 확인한다.
  - Seedance 계열은 4,000으로 두거나(vnew `VNEW_TAKE_PROMPT_MAX`와 같은 근거), 넘으면 ARK처럼 거절한다.
  - 실제로 보낸 문장을 기록한다.
- 배포 전 필수: 아니오(품질). 등록 직후 caps 확인을 권장한다.

### O-5 (중간) fal 모델은 화질 선택이 무시되고 금액이 540p로 계산된다
- 근거:
  - `routes.py:230-236 _plan_resolution`은 `settings.VIDEO_RESOLUTIONS`만 본다. fal 모델은 그 표에 없어 늘 `RESOLUTION="540p"`가 된다.
  - `fal.py:1039-1043`: 540p가 스키마 enum에 없으면 resolution을 보내지 않는다. 그러면 fal 기본 화질로 생성된다.
  - `settings.shot_usd`의 토큰식은 540p 픽셀로 계산하고, 원장도 같은 값을 쓴다(`routes.py:2289`, `ledger.py:130`).
- 실패 시나리오: 720p로 고른 옛 Seedance 계획이 fal로 바뀌면, ③에는 540p 금액이 보이고 원장에도 540p로 기록된다. 실제 청구는 fal 기본 화질(확인 못 함) 기준이라 과소 계상될 수 있다.
- 배경: 이번 배포 전부터 모든 fal 모델에 있던 문제다. 070으로 운영 Seedance 사용량 전체로 노출이 넓어진다.
- 권장 조치: fal이면 `caps.resolutions`(`ark.video_options`가 이미 계산함)로 허용 목록을 만든다.
- 배포 전 필수: 아니오.

### O-6 (낮음) 회차 도중 fal 모델이 해제되면 Luma로 조용히 제출되고 원장은 0달러가 된다
- 근거:
  - `routes.py:2137-2138`: 미등록 fal은 `is_ark`가 False라 `use_ark=False`가 된다. 그러면 `routes.py:2257`의 `video.submit`(Luma)으로 간다.
  - 원장(`routes.py:2289`)은 `model=fal:…`로 `shot_usd`를 계산하는데, 미등록이면 0이다(`settings.py shot_usd`의 `pricing_for` None).
- 실패 시나리오: S-1처럼 누군가 해제한 직후, 대기 중인 fal 샷이 Luma로 만들어진다. 비용은 기록되지 않는다.
- 권장 조치: `submit_shot`에서 회차 모델이 fal인데 미등록이면 실패로 처리한다(조용한 대체 금지).
- 배포 전 필수: 아니오.

### O-7 (낮음) 옛 shots·한 번에 만들기 계획을 fal i2v로 바꾸면 입력의 의미가 달라진다
- 근거:
  - `routes.py:463`: fal은 `VIDEO_DURATION_RANGE`에 없어 최대 5초로 취급된다. 그래서 한 번에 만들기가 꺼진다.
  - `routes.py:239-246`: 샷이 5초로 고정된다.
  - `fal.py:1028-1030`: 참고 이미지 필드가 없는 모델은 첫 참고 이미지(인물 그림)를 시작 프레임으로 쓴다.
  - 그런데 프롬프트는 여전히 "Image 1..N"을 설명한다(`routes.py:398-440`, `2199-2221`).
- 실패 시나리오: 20초 한 번에 만들기로 승인했던 옛 계획이 5초 샷 4개로 바뀐다. 각 샷은 인물 정면 그림에서 시작하고, 프롬프트에는 보내지 않은 이미지 설명이 남는다.
- 권장 조치: fal i2v(시작 이미지 전용)이면 `reference_prompt` 접두를 빼고, single처럼 `start_only`로 처리한다. 옛 계획은 화면에서 ③ 재작성을 안내한다.
- 배포 전 필수: 아니오.

### O-8 (중간) 애니 변환 프롬프트가 인물 그림까지 "웹툰 그림체 참고"로 부른다(운영 dual=true 전부 해당)
- 근거:
  - `routes.py:116-137`: `_anime_prompt`가 참고 이미지 수만 받아 `@Image1..N` 전부를 "reference webtoon images"로 지칭하고, 2D·cel 앵커 문구를 없앴다.
  - `routes.py:2357-2385`: 참고 이미지는 패널 최대 3장과 인물 그림 1장이다.
  - dual은 생성 화풍을 실사로 강제한다(`routes.py:99-108`). 그래서 AI 인물 그림은 실사다.
- 실패 시나리오:
  - ①에 웹툰 장면 없이 인물 사진만 올린 계획: 참고 이미지가 실사 1장뿐이다. 이걸 "이 그림체 그대로"로 지시하면 변환 결과가 실사에 가깝게 남을 위험이 있다. 옛 문구에는 "2D Korean webtoon / anime" 앵커가 있었다.
  - 패널 3장+인물 1장인 일반 경우에도 실사 1장이 그림체 기준에 섞인다.
- 권장 조치:
  - `@Image` 목록을 패널 수로만 만들고, 인물 그림은 "identity reference only"로 따로 지칭한다.
  - 패널이 0장이면 2D 앵커가 있는 문장을 쓴다.
- 배포 전 필수: 아니오(품질). 운영 전체에 해당하므로 배포 전 수정을 권장한다.

### O-9 (낮음) 모델 치환이 로그와 화면에 드러나지 않는다
- 근거:
  - `ark.py:164-179`: `resolve_model`에 로그가 없다.
  - `frontend/public/vpoc.js:607-609`: ② 라디오는 저장값(`step.videoModel = "seedance-2-5"`)을 우선하는데, 그 값이 목록에 없어 아무것도 선택되지 않는다.
- 실패 시나리오: "왜 Luma로 나왔나"를 로그로 추적할 수 없다. 사용자는 모델이 바뀐 것을 모른다.
- 권장 조치:
  - 치환할 때 `vpoc.model.resolved from=… to=… reason=…` info 로그를 남긴다.
  - `plan_view`에 `videoModelRequested`를 함께 주고, 화면은 `plan.videoModel`을 우선한다.
- 배포 전 필수: 아니오.

### O-10 (낮음) 배포 계획서와 실제 코드가 다르다
- 근거:
  - `docs/plans/2026-10-26-prod-deploy-fal-seedance.md` 2절의 index.html 설명("import map 4줄, import 전에는 로드하지 않는다")은 실제 코드(`index.html:37`, P-1)와 다르다.
  - 5절은 플래그 되돌리기에 템플릿 수정이 필요하다고 적었지만, 그 자체가 코드 변경이라는 점이 빠져 있다(O-3).
  - 4단계 4의 등록 시점(배포 후)은 O-2와 충돌한다.
- 권장 조치: 계획서를 고친다.
- 배포 전 필수: 아니오. 다만 필수 절차 1과 함께 갱신한다.

### O-11 (낮음, vnew 전용) 저장된 ARK Seedance 값이 vnew에서 400이 된다
- 근거: `vnew/plan.py:463-471`. `resolve_model`이 Luma를 돌려주면 그 값이 프리비즈 모델 목록에 없어 400 `BadVideoModel`이 된다(빈 값일 때처럼 기본값으로 바꾸지 않는다).
- 실패 시나리오: 개발계 vnew에서 옛 값을 보내면 저장이 거절된다. 운영은 vnew가 꺼져 있어 영향이 없다.
- 권장 조치: 매핑 결과가 프리비즈 모델이 아니면 기본 프리비즈 모델로 바꾼다.
- 배포 전 필수: 아니오.

### O-12 (낮음) 새 caps 인식 때문에 "사용 가능"인데 제출이 실패하는 모델이 생길 수 있다
- 근거:
  - `fal.py:878-897`: `fillable`에 `elements`·`video_url(s)`·`image_urls`·`task`·`seed` 등이 들어갔다.
  - `build_payload`(982-1050)는 vpoc 경로에서 이 필드들을 채우지 않는다.
- 실패 시나리오: 앞으로 등록하는 t2v·i2v 모델 중 `elements`나 `task`가 필수인 모델이 available로 ② 목록에 나온다. 그 모델은 제출 때 fal 422로 실패한다. 이미 등록된 항목은 caps를 다시 계산하지 않으므로 영향이 없다.
- 권장 조치: vpoc 피커용 available을 따로 계산한다(`build_payload`가 채우는 필드 기준).
- 배포 전 필수: 아니오.

### 문제없음으로 확인한 항목
- 진행 중 BytePlus 작업(`ark/tasks/`):
  - `is_ark_job`(`ark.py:54-56`)은 플래그와 무관하다. 조회(`routes.py:2617-2619` → `ark.poll` 511-539)와 취소(`routes.py:3865-3871` → `ark.cancel` 542-553)는 ARK 키만 있으면 그대로 돈다. 토큰 원장 기록도 그대로다(`routes.py:2628-2640`).
  - ARK 키는 Secret optional 마운트로 유지된다(`workloads.yaml.tmpl:337-342`). Seedream도 같은 키를 쓰므로(`seedream.py:53-68`) 키를 지우면 안 된다.
- 플래그 `true`일 때 예전과 같은지:
  - `choices`(`ark.py:119-121`)는 건너뛰지 않는다. `unavailable_reason`(76)은 해당하지 않는다. `resolve_model`(172-173)은 원래 값을 그대로 돌려준다.
  - fal 필터(143-146)는 원래 숨겨야 할 v2v·r2v에만 걸린다. 패치 전에는 이런 모델을 등록할 수 없었다.
  - false 기간에 fal로 만든 회차는 `steps.4.videoModel=fal:…`로 저장돼 있어, true로 되돌린 뒤에도 그대로 진행된다. 데이터 이전이 없어 되돌릴 수 있다.
- 키 장면 경로(fal 등록 상태, 키 장면을 이미 만든 계획):
  - generate는 `selected` → fal로 정한다. `is_ark`가 True라 briefing과 `_prepare_briefs`를 거친다(`routes.py:4161-4173`).
  - keyframes 분기에서 `_finalize_keyframes`를 부른다(3196). 러프가 같으면 이미 그린 완성본을 재사용하므로 Seedream 추가 과금이 없다(1479).
  - `submit_shot`에서 fal은 길이 검사를 건너뛴다(2143). `_keyframe_inputs`(1492~)의 시작·끝 장면을 `build_payload`가 start·end 필드에 넣는다(`fal.py:1021-1024`).
  - 화면 금액과 서버 금액은 둘 다 `_shot_seconds(fal)=5초`로 계산해 일치한다.
  - 단, Seedance 2.5 i2v에 `endImageField`가 없으면 끝 장면이 조용히 빠진다(caps 확인 필요, 필수 1에 포함).
- 재시작 후 이어하기:
  - 드라이버는 파드 메모리에만 있다. 계획을 조회하면 다시 붙는다(`routes.py:3438-3440`).
  - 멈춘 작업은 `stall_check`(982~)가, 애니 선점은 15분 회수(`routes.py:36`, `2557~`)가 처리한다. 이번 변경으로 바뀌지 않았다.
  - vnew는 꺼져 있어 이어하기 경로가 돌지 않는다.
- 오류 메시지: 사용 중지 모델 제출은 사용자 노출 kind로 원인을 그대로 보여 준다(`ark.py:436-438`, `errors.py:39-49`). 운영에서도 "처리 중 오류"로 가려지지 않는다.
- 금지 목록 기본값: `*seedance*2*fast*`(`settings.py:180-185`)는 `bytedance/seedance-2.5/image-to-video`에 걸리지 않는다(문자열에 fast가 없다).

---

## 확인하지 못한 부분
- `check_vpoc.py`·커버리지를 실행하지 않았다(이번 작업은 명령 실행 금지). sf-a~d 검사(`tools/check_vpoc.py:11684~`)가 있다는 것만 확인했다.
- fal `bytedance/seedance-2.5/image-to-video`의 실제 caps(durations·resolutions 표기·`end_image_url` 유무·prompt maxLength·기본 해상도·단가 unit). 개발 레지스트리를 읽지 않았다.
- 운영에 진행 중인 BytePlus 회차가 있는지, 운영 레지스트리에 금지 모델이 있는지, 운영에 ARK·fal Secret이 있는지.
- `three.module.min.js` 크기, 그리고 실제 브라우저 로드 시간.
- 패치 밖의 64개 커밋 중 운영 경로에 닿는 다른 변경. `deploy/render.py`가 정의되지 않은 템플릿 변수를 어떻게 처리하는지(O-3 조치 때 확인 필요).
- vnew 내부 로직은 차단 여부만 봤고 품질은 리뷰하지 않았다.
