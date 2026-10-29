# 작업 로그 (WORKLOG)

결정과 근거를 시간순으로 남긴다. 항목 형식: **날짜 · 결정** / 이유 / 검토한 대안 / 관련 파일·MR.
코드 변경 상세는 커밋·MR에, 큰 구조는 `docs/ANALYSIS.md`에 둔다.

---

## 2026-10-26

### 이번 주 작업 원칙 채택 (설계자 지시)
- **결정**: 이번 주에는 비용보다 품질을 우선한다.
  - Kiro는 `claude-opus-5.5`로 고정한다(저장소 workspace 설정 `chat.defaultModel` + 실행마다 `--model`).
  - 구현 전에 스펙(requirements → design → tasks)을 쓴다.
  - `docs/ANALYSIS.md`와 이 로그를 유지한다.
  - 바뀐 코드의 테스트 커버리지 목표는 80% 이상이다.
  - 구현 후 보안 → 성능 → 운영성 순서로 리뷰한다.
  - 운영 변경은 계획서만 쓰고, 실행은 승인 후에 한다.
- **이유**: 설계자 지시(2026-10-26).
- **적용**:
  - `.kiro/steering/quality-week.md`(Kiro가 매 실행 읽음).
  - 커버리지 측정 도구 `coverage` 7.17을 로컬에 설치했다. 검증용 도구라 프로젝트 의존성에는 넣지 않았다.
- **기준치**: `tools/check_vpoc.py` 전체 실행 기준.
  - `vpoc`+`vnew`+`stitcher.py` 구문 커버리지 62%(15,647문 중 9,728문).
  - 모듈별: `vnew` 66~90%, `vpoc/routes.py` 27%, `vpoc/ark.py` 41%, `vpoc/fal.py` 60%, `stitcher.py` 22%.
  - 낮은 모듈은 이후 변경 때마다 바뀐 부분부터 끌어올린다.

### 069 — 통 테이크 프롬프트 예산 4,000바이트 (MR 41, 개발 배포)
- **결정**: fal Seedance 2.5 reference-to-video 통 테이크 프롬프트 예산을 `VNEW_TAKE_PROMPT_MAX=4000`으로 늘린다.
  - 모델 스키마가 더 작은 maxLength를 밝히면 그 값을 따른다.
  - 컷 단위(Kling O3)는 실측 한도 2,500바이트를 유지한다.
- **이유**:
  - 2,500바이트 안에 TV 애니 룩·MOTION·HARMONY 섹션을 넣으려던 시도가 3번 실패했다. 키스 연기 같은 핵심 문장이 밀려났다.
  - fal API 페이지와 OpenAPI 스키마에 Seedance prompt 최대 길이가 없다. 2,500은 우리 기본값이었다.
- **대안**: 섹션을 더 줄이기(핵심 손실로 기각), 통 테이크 대신 컷 단위만 쓰기(움직임 연속성 손실로 기각).

### 070 — Seedance는 fal 경유만, BytePlus 직결 Seedance 제외 (MR 42, 개발 배포 ci-0271fdb0)
- **결정**:
  - 플래그 `VPOC_ARK_SEEDANCE_ENABLED`(기본 false)로 BytePlus 직결 Seedance 2.0/2.5를 모델 목록에서 뺀다.
  - 저장된 선택(`seedance-2-0`/`2-5`)은 `ark.resolve_model`이 `fal:bytedance/seedance-2.5/image-to-video`로 바꾼다. 그 모델이 등록·사용 가능할 때만 바꾸고, 아니면 Luma로 바꾼다.
  - 이미 BytePlus에 제출된 작업(`ark/tasks/` ARN)은 끝까지 조회한다.
- **이유**: 설계자 지시 "운영과 개발에 fal.ai의 seedance를 사용하도록 활성하고 기존 seedance는 제외".
- **대안**:
  - BytePlus 코드 삭제: 되돌리기 어렵고 진행 중 작업이 끊길 수 있어 기각.
  - API 키만 제거: 목록에 "사용 불가"로 남아 혼란스러워 기각.
- **확인**:
  - check_vpoc 전부 통과(sf-a~d 포함).
  - 개발 레지스트리에 fal Seedance 2.5 i2v를 등록했다(스키마 조회만, 과금 없음).
  - 개발 목록 = Luma Ray2, MiniMax H3 Max, Kling v3 Pro, fal Seedance 2.5. byteplus 항목은 없다.
- **참고**:
  - 운영 매니페스트에 남은 `SEEDANCE_MODE`·`ARK_MODEL_ID`·`SCENE_VIDEO_PROVIDER` 값은 현재 코드가 읽지 않는다(grep 확인). 070 범위 밖이며, 나중에 정리할 후보다.
  - 키 장면(keyframe) 제작 방식은 BytePlus Seedance 전용이다. 새 계획에서는 쓰이지 않게 된다.

### 운영 배포 보류 → 계획서 작성
- **결정**:
  - 운영 배포(`deploy:whynot:prod`)는 계획서 `docs/plans/2026-10-26-prod-deploy-fal-seedance.md`를 쓴 뒤 설계자 승인을 받고 실행한다.
  - 배포 전에 운영에 닿는 변경을 3단계 리뷰한다(Kiro 072, 보고서 `docs/reviews/2026-10-26-prod-release-review.md`).
- **이유**:
  - 실행 시도가 권한 분류기에서 "Production Deploy"로 차단됐다.
  - 이번 주 원칙 7번(운영 변경은 계획만).
  - 운영이 10/20 `ci-ae21dc13` 그대로라, 커밋 64개가 함께 나간다.
- **확인한 사실**:
  - 운영 렌더링 = `deploy/config.env.example` + `config.prod.env` 오버레이(`render.py --target prod`).
    - `VPOC_MODEL_PICKER_ENABLED=true`·`VNEW_ENABLED=false`는 유지된다.
    - `config.env.example`만 보고 "모델 선택이 꺼진다"고 본 것은 오탐이었다.
  - 롤백 이미지 `prod-ae21dc13`(app·frontend)이 ECR에 있다. `prod-*` 태그는 수명 주기 규칙상 영구 보관된다.
  - 운영 바뀐 줄 커버리지 84%(146문 중 122문).
    - `stitcher.py` 바뀐 줄은 18%다. vnew 전용이라 운영에서는 실행되지 않는다. 테스트 보강 대상이다.

### 072 리뷰 결과 → 운영 배포 보류, fal Seedance 동등성 스펙(073) 착수
- **리뷰 요약**(`docs/reviews/2026-10-26-prod-release-review.md`):
  - 보안: 중간 1건, 낮음 3건.
  - 성능: 낮음 5건.
  - 운영성: 중간 6건, 낮음 6건.
  - Claude가 코드로 재확인한 것:
    - O-1 `submit_shot`이 resolve를 거치지 않음(`routes.py:2137`).
    - O-3 플래그가 템플릿에 없음.
    - O-5 fal 기본 화질 540p가 enum 밖이라 전송되지 않음.
    - O-8 dual 실사 인물 그림을 그림체 참고로 넘김.
    - P-1 `index.html:37`이 three.js(687KB)를 모든 페이지에서 로드.
- **결정**: 현재 main 그대로는 운영에 내지 않는다.
  - fal Seedance가 BytePlus Seedance를 기능 그대로 대체하도록 스펙 `fal-seedance-parity`를 쓰고 구현한다.
  - 결정 D1~D13은 지시서 `.kiro/handoff/073-*.md`에 있다.
- **주요 결정과 근거**:
  - D1 진행 중 회차는 BytePlus로 끝낸다. 승인 금액·길이 그대로 끝내야 이중 과금·회차 내 모델 혼합이 없다. 대안인 제출 시점 fal 전환은 금액 불일치로 기각했다.
  - D2 화면 목록은 "Seedance 2.5 (fal)" 하나이고, 엔드포인트는 방식별로 고른다.
    - 키 장면·시작 이미지는 image-to-video, 인물 참고·글만은 reference-to-video.
    - 이유: fal은 Seedance 2.5를 방식별 엔드포인트로 나눠 제공한다. i2v 하나로 대체하면 인물 그림이 시작 프레임이 되는 등 의미가 바뀐다(O-7).
  - D10 fal 모델 해제는 관리자만 한다. 등록은 설계자 요청(1단계 활성화)에 따라 열어 둔다.
- **확인된 caps**(개발 레지스트리):
  - i2v·r2v 모두 4~30초, 480p·720p·1080p, `generate_audio`.
  - r2v `image_urls` 최대 10, 필수 필드 없음.
  - 프롬프트 maxLength는 둘 다 스키마에 없다.
- **ANALYSIS 보완**: 운영 ECR 실제 수명 주기 정책에 `prod-*` 영구 보존 규칙이 있음을 확인했다(R-6 해소, 저장소 파일만 다름).

### 073 스펙 작성·검토 (Kiro spec 모드 V3, claude-opus-5.5)
- **결과**: `.kiro/specs/fal-seedance-parity/`
  - `requirements.md`: R1~R14, EARS 형식.
  - `design.md`: 함수 단위 변경, 엔드포인트 표, 되돌리기, 운영 두 단계 전환.
  - `tasks.md`: 작업 6개, 검사 그룹 13개, 유료 검증 7건 약 $9.79.
- **Claude 검토**: `review-claude.md`. 조건부 승인.
  - 필수 A: 길이·화질 변경을 fal Seedance 계열로 한정하고, Kling·MiniMax 등 다른 fal 모델의 운영 동작은 바꾸지 않는다.
  - 필수 B: 유료 검증은 예산 재승인이 필요하다.
  - 권장 C~E.
- **설계자 확인 대기**:
  - D1 진행 중 BytePlus 회차를 BytePlus로 끝낼지(재생성 포함).
  - D3 15초 상한.
  - D10 해제 관리자 전용.
  - 유료 검증 예산.
  - 운영 두 단계 전환.
- **범위 밖 과제**: 비계열 토큰 단가 fal 모델의 540p 과소 계상(리뷰 O-5). fal 기본 화질 조사가 필요하다.
- **도구 메모**: `kiro-cli chat --v3 --mode spec --trust-workspace --no-interactive`가 비대화형에서도 세 파일을 한 번에 만든다(약 9분).


## 2026-10-27

### 073 fal-seedance-parity 구현 (작업 1~6, Kiro, claude-opus-5.5)
- **결과**: 스펙 `.kiro/specs/fal-seedance-parity/` 작업 1~6을 구현했다. 검사 그룹 13개(ip·rw·fr·du·rz·pm·ai·ad·ns·ml·vl·st·vm)를 `tools/check_vpoc.py`에 더했다. 외부 호출은 전부 가짜이고 유료 호출은 0회다.
- **주요 결정과 근거**(design.md §10 D1~D13·X1~X8의 구현 시 결정만 적는다):
  - 작업 1(D1·D7): 진행 중 BytePlus 회차는 제출 검사를 `_submit_block_reason`(플래그 게이트 없음)으로 바꿔 BytePlus로 끝낸다. 새 선택·새 생성 게이트(`unavailable_reason`)는 그대로다. 재생성 금액은 ④ `regenUsd`(회차 값)를 쓴다(X1, 409 방지). 새 env 키는 템플릿·base에 함께 배선했다.
    - 대안: 제출 때 fal로 바꾸기. 승인 금액·길이가 어긋나 기각.
  - 작업 3 fix1 — ③ 저장 검증: `save_step`(③)의 샷 길이·화질 허용 범위를 화면 선택지와 같은 출처(`ark.duration_range`·`ark.video_options`)로 맞췄다(R3.9·R4.7).
    - 이유: 화면은 계열 4~15초·480/720/1080p를 보이는데 저장 검증이 옛 범위(5초·540p)라 저장이 400이 됐다.
    - 계열이 아닌 fal(Kling·MiniMax)은 지금 허용 목록 그대로다(R14.3, 회귀 스냅샷 du-h·rz-f).
    - 대안: 화면 선택지를 옛 범위로 되돌리기. BytePlus 동등성 목표와 어긋나 기각.
  - 작업 3 fix1 — 승인 금액을 넘지 않는 화질 보정: 070 이전 계열 회차의 저장 화질(540p)이 caps에 없으면, caps 중 픽셀 수가 저장 화질 이하인 가장 큰 값(540p→480p)으로 보낸다. 그런 값이 없으면 caps의 가장 작은 값이다. 로그 `vpoc.fal.resolution_adjusted`.
    - 이유: fix1 전 규칙은 caps에 없는 저장 화질을 더 높은 화질로 보낼 수 있어, 사용자가 승인한 540p 금액보다 비싸게 청구될 수 있었다. 원장·jobs도 보낸 화질 기준이다.
    - 대안: 720p로 올리기(화질은 좋지만 승인 금액 초과로 기각), 제출 거절(진행 중 회차가 멈춰 기각).
  - 작업 4 — `@Image` 개별 번호 지칭: at 방식에서 3장 이상 이어지는 지칭을 범위 표기(`@Image3-@Image5`) 대신 번호를 모두 적는다(`@Image3, @Image4, @Image5`, `ark.image_label`).
    - 이유: R5.3은 프롬프트의 `@Image<n>` 번호 수가 실제 보낸 `image_urls` 수와 같고 1..N 연속이어야 한다. 범위 표기는 가운데 번호가 문장에 없어 모델이 그 이미지를 지칭으로 못 볼 수 있다(검사 pm-e). 2장은 `@Image3-@Image4`도 두 번호가 다 있어 그대로 둔다.
    - 대안: plain 방식(`Images 3-5`) 유지. vnew r2v 경로가 `@Image{n}`을 쓰므로 기각. 되돌릴 때는 `VPOC_FAL_SEEDANCE_TAG_STYLE=plain`(config만).
  - 작업 5(D10·D11): fal 모델 해제는 관리자만(deps `is_admin`, 주입하지 않으면 거부). 등록은 로그인 사용자 누구나. 미등록 fal 회차는 Luma로 몰래 제출하지 않고 failed(E5). 레지스트리 읽기 실패는 캐시하지 않는다.
  - 작업 6(D9) — three.js 지연 로드: `index.html`의 `vnew-previz.js` 정적 module 로드를 지우고 `app.js`가 `VNEW_ENABLED`일 때만 `import("/vnew-previz.js?v=__ASSET_VERSION__")`한다. `vnew.js` ④가 `window.vnewPrevizReady`를 기다린 뒤 `create`를 부른다. 로드 실패는 기존 `.catch`의 `showError`로 E7을 보인다(`.catch`가 이미 있어 새로 더하지 않았다). Dockerfile 치환 목록에 `vnew-previz.js`를 더했다.
    - 이유: 운영(vnew 꺼짐)에서도 모든 사용자가 three.js(약 690KB)를 받았다(P-1). `vnew-previz.js`의 버전 쿼리가 치환되지 않아 30일 캐시에서 옛 파일이 나올 수 있었다(P-2).
    - 대안: import map(여전히 정적 로드라 기각), `vnew.js`에서 import(classic script라 `app.js`와 같은 방식이고, 플래그를 이미 아는 곳이 `app.js`라 그쪽에 둠).
    - 되돌리기: 플래그가 없다. 커밋 revert + 프런트 이미지 재빌드.
  - 작업 6(D13) — vnew 옛 값: `update_input`이 옛 ARK Seedance 값(`seedance-2-0`/`2-5`)을 받았고 매핑 결과가 프리비즈 모델이 아니면 400 대신 기본 프리비즈 모델로 저장하고 `vnew.plan.video_model_reset from= to=` info 로그를 남긴다. 그 밖의 모르는 값은 지금처럼 400(O-11).
  - 작업 6(D13) — stitcher threads: `trim`·`scale_to_min_height`·`pad_to_seconds`가 `threads`를 받으면 `-threads N`을 입력 앞(디코더)과 `libx264` 설정 뒤(인코더)에 모두 둔다. `threads`가 None·0 이하면 명령이 073 이전과 같다(P-5, 검사 st-d 스냅샷).
- **확인**: `python -X utf8 tools/check_vpoc.py` 전부 통과(작업 6 기준). 커버리지는 Claude가 `coverage run`으로 잰다.
- **범위 밖 과제(남김)**:
  - O-5: 계열이 아닌 토큰 단가 fal 모델의 540p 과소 계상. 계열이 아닌 fal은 ③ 화질이 지금처럼 540p로 저장되고, enum에 없으면 전송되지 않아 fal 기본 화질로 만들어진다. 토큰 단가 모델이면 원장이 실제보다 낮게 계상될 수 있다. fal 기본 화질을 caps에서 알 수 없어 별도 조사가 필요하다(design.md §12, R14.3으로 이번에는 동작을 바꾸지 않음).
  - X8(제안만): 레지스트리 읽기 실패 때 지난 성공 목록 재사용.
- **운영 배포**: 계획서 `docs/plans/2026-10-26-prod-deploy-fal-seedance.md`를 design.md §9 두 단계 순서(안전 모드 배포 → 등록 → 전환)로 고쳤다. 실행은 단계마다 설계자 승인 뒤 Claude가 한다.


### 073 작업 7 — fal 거절 샷이 "생성 중"에 멈추는 결함 수정 (R15, Kiro, claude-opus-5.5)
- **유료 검증 결과**(2026-10-26, 개발계 `ci-0426c61d`, 설계자 승인 V-2·V-3·V-6):
  - V-3 성공($1.03): i2v 시작·끝 프레임, 480p 5초, 854×480, 소리 있음. 마지막 프레임이 끝 이미지와 일치한다(`end_image_url` 반영).
  - V-6 성공($2.31): r2v 인물 2장, 720p 5초, 1280×720, 소리 있음. `@Image1`/`@Image2` 지칭대로 인물·행동이 나뉘었고 얼굴·의상이 참고 그대로다. V-2의 목적(지칭 인식)은 이것으로 확인했다. `VPOC_FAL_SEEDANCE_TAG_STYLE=at`을 유지한다.
  - V-2 실패: r2v 같은 인물 2장, 480p. fal이 "The images or videos provided may contain likenesses of real people or other private information that cannot be processed."로 거절했다. 같은 이미지로 V-6은 통과했다 — fal 필터 판정이 매번 같지 않다. **청구 여부 미확인(최대 $1.03)**.
  - 합계 $3.34 확정 + V-2 최대 $1.03. V-6의 fal 청구액과 원장 비교·오디오 가산 여부(X3)는 fal 대시보드에서 따로 확인한다.
- **결함**: fal은 거절한 요청의 status를 `COMPLETED`로 주고, 결과 조회가 4xx다. `fal.poll`이 예외를 올리고 `routes.poll_target`이 경고 로그만 남긴 채 `return False`했다(`lastPolledAt`도 그대로). 샷이 `submitted`에 영원히 멈췄고 조회마다 같은 실패를 되풀이했다. `stall_check`는 준비 작업용이라 샷에 걸리지 않는다. 070 이전부터 운영의 모든 fal 모델에 있던 결함이다.
- **결정**:
  - D14: 결과 조회 400·403·404·422는 fal 거절로 보고 `Failed` + 한국어 문구(likeness·sensitive·other)를 돌려준다. 5xx·402(잔액)·네트워크·status 조회 실패는 지금처럼 예외(다음 조회에서 재시도). 로그 `vpoc.fal.rejected request= kind= http=`(원문·프롬프트·키 없음).
    - 이유: 4xx는 다시 조회해도 같다. 5xx는 일시 장애일 수 있다.
    - 대안: 모든 예외를 실패로(일시 장애에도 실패해 기각). vnew `_user_facing_error`(`'real person'`→"모델을 바꿔") 재사용 — BytePlus 실사 시작 이미지용 안내라 뜻이 다르고(fal은 다시 시도하면 통과할 때가 있다), vpoc→vnew import가 순환을 만들고, vnew 파일은 범위 밖이라 기각. 새 문구에는 영어 `real person`이 없어 vnew 경로를 지나도 바뀌지 않는다(검사 pf-a).
  - D15: 모든 공급자의 조회 예외에 `lastPolledAt`·`pollErrors`(연속 실패 수)·`pollError`(요지 200자)를 남기고, 제출 뒤 `VPOC_POLL_GIVEUP_S`(기본 1800초) 경과 + 연속 5회면 `failed`(`PollGiveUp`, "결과를 가져오지 못했습니다(…). '다시 만들기'로 다시 시도해 주세요."). 성공 조회·새 제출이면 0으로 되돌린다. 로그 `vpoc.poll.giveup shot= errors=`.
    - 이유: 4xx 외의 원인(알 수 없는 응답·코드 결함)으로도 멈출 수 있다. 30분은 Seedance 생성(수 분)보다 충분히 길고, 연속 5회는 짧은 장애를 견딘다.
    - 대안: 시간만 보기(일시 오류 한 번에 실패), 횟수만 보기(조회가 잦으면 곧 실패), 성공 조회에도 시간 제한(대기열이 길면 정상 샷 실패) — 기각.
  - D16: 포기 시간만 설정(최소 60초), 횟수는 상수 5. 템플릿·`config.env.example`에 배선(오버레이는 base 키만 덮는다).
  - 오류 요지(`errors.error_gist`)는 URL 쿼리·data URI·fal 키 값을 지우고 200자로 자른다.
  - 원장: 제출 때 계상하는 지금 동작을 바꾸지 않는다. **fal이 거절 건을 청구하는지는 미확인**이다(V-2 청구 내역으로 확인 예정). 청구하지 않는 것으로 확인되면 거절 건 환급(원장 차감)을 별도 과제로 검토한다.
  - 포기한 샷은 공급자 쪽 작업을 취소하지 않는다(조회도 못 하는 상태라 취소도 실패할 가능성이 크다). 키 장면 샷이어도 E5 자동 재제출을 하지 않는다.
- **되돌리기**: 거절 처리는 플래그가 없다(커밋 revert). 포기 동작은 `VPOC_POLL_GIVEUP_S`를 크게(예: 86400) 두면 사실상 끈다.
- **확인**: `python -X utf8 tools/check_vpoc.py` 전부 통과(새 그룹 `check_fal_poll_failure` pf-a~f, rw-a~c 새 키 포함). 유료 호출 0회.
- **관련 파일**: `app/src/vpoc/{errors,fal,routes,settings}.py`, `k8s/workloads.yaml.tmpl`, `deploy/config.env.example`, `tools/check_vpoc.py`, 스펙 R15·design.md §13·tasks.md 작업 7.



## 2026-10-28

### 073 작업 8 — vnew ① 영상 모델 목록: Seedance 이름·중복·토큰 단가 (R16, Kiro, claude-opus-5.5)
- **배경**: 설계자 지시 "신규화면도 개발에 적용해". 개발계(`ci-41ab5f0e`) vnew ① 목록에 `fal · bytedance/seedance-2.5/reference-to-video · 단가 미정`과 회색 `… image-to-video · 프리비즈 미지원`이 함께 보였다. vpoc ②는 073에서 "Seedance 2.5 (fal)" 한 줄로 정리했다.
- **결정**:
  - D17 — 이름·중복: vnew 목록에서 계열 얼굴(i2v)을 숨기고, 짝(r2v) 한 줄의 이름을 `ark.label(<얼굴 id>)`(= `settings.FAL_SEEDANCE_LABEL`)로 만든다. 문자열을 따로 적지 않았다. 계열 밖 fal 모델(H3 Max·Kling v3 Pro 등 i2v)은 지금처럼 `fal · <label>` 회색 "프리비즈 미지원"이다(설계자 2026-10-22 결정 유지). 계열 설정 `VPOC_FAL_SEEDANCE_FAMILY`를 비우면 073 이전 목록으로 돌아간다.
    - 이유: vnew는 영상 입력이 필요해 짝만 쓸 수 있고, 얼굴은 늘 회색이라 혼란만 준다. 고를 수 있는 집합(`_previz_model_ids`)은 얼굴이 원래 빠져 있어 바뀌지 않는다(i2v 저장은 지금처럼 400).
    - 대안: `FAL_SEEDANCE_LABEL` 직접 사용(vpoc 이름 규칙과 갈라질 수 있어 기각), `ark.label`이 짝에도 계열 이름을 주게 vpoc 수정(범위 밖, vpoc 원장·로그 표시 변화로 기각), 얼굴 회색 유지(지시와 어긋나 기각).
  - D18 — 해상도 근거: vnew가 r2v로 실제 보내는 해상도는 통 테이크의 `VNEW_TAKE_RESOLUTION`(기본 `480p`)이다. 073 계열 규칙(`ark.submit_resolution`, 기본 720p)은 vnew 경로에 쓰이지 않는다. 그래서 목록 단가 = `settings.shot_usd(r2v, VNEW_TAKE_RESOLUTION, 1)`(480p ≈ $0.2056/초)이고 `priceResolution="480p"`를 함께 준다. `generate.py`는 바꾸지 않았다.
    - 근거(코드 위치): 모드 `_gen_mode`(caps `videoListField` → `previz-take`, `vnew/generate.py:481-486`) → gen `resolution = VNEW_TAKE_RESOLUTION`(`generate.py:567`; `vnew/settings.py:167`, k8s·deploy 배선 없음) → 제출 `resolution = gen.resolution`(`generate.py:3002`)을 본문에 늘 명시(`_submit_take`, `generate.py:2918`. fal 스키마 기본값을 쓰지 않는다) → 원장 `_charge_take`(`generate.py:2945-2965`, 호출 3047) → `_take_usd`(`generate.py:581-592`, 같은 토큰 공식·같은 픽셀 표). ④ 견적(`estimate`, 727-733)도 같은 값이다.
    - 원장 일치: pricing unit이 "1000 tokens"이면 `_take_usd(e, s, r) == shot_usd(id, r, s)`(4자리 반올림 차이 이내). 검사 vq-e가 480p·720p 통합 경로(제출 본문·gen·표시 해상도, 원장, 견적)로 고정한다. 지시서의 `_charge_video`(`generate.py:5373`)는 옛 흐름(`_new_gen`, 호출 없음) 계상 함수로, 이미 `shot_usd(model, resolution, seconds)`다(vq-e에서 같이 확인).
    - 대안: 720p(073 계열 기본) 표시 — 실제 전송(480p)보다 2.25배 높아 기각. 전송을 바꾸기 — 품질·비용 변화라 기각.
  - D19 — 토큰 단가 표시 범위: 전송 해상도를 아는 통 테이크 모델에만 토큰 단가를 보인다. 컷 단위 프리비즈 제출(`_submit_previz_edit`, `generate.py:4198-4260`)은 `resolution`을 보내지 않아 해상도를 모르므로, 토큰 단가 컷 단위 모델은 지금처럼 "단가 미정"이다. 초 단위 모델·기본 모델(Kling O3) 행은 값·키가 그대로다.
    - 리뷰(3단계 운영성)에서 더함: `VNEW_TAKE_RESOLUTION`이 원장 픽셀 표(`TAKE_RESOLUTION_WH`, 480p·720p·1080p)에 없는 값(예: 540p)이면 표시(`shot_usd`, 960×540)와 원장(`_take_usd`, 표에 없어 480p로 계산)이 갈라진다. 그래서 그 경우는 "단가 미정"으로 둔다(검사 vq-b 540p·4k). 원장 쪽 대체 동작은 범위 밖이라 바꾸지 않았다.
  - 화면(`vnew.js` `previzPriceText`): 목록 `$0.206/초(480p)`, 설명 줄 `초당 $0.206(480p)`. `priceResolution`이 없으면 숫자만, "단가 미정"은 `priceKnown`이 false일 때만이다.
- **미확인(O-6)**: fal이 Seedance 2.5 r2v에서 입력 영상(`video_urls`)의 길이·해상도를 토큰에 더해 청구하는지 확인하지 않았다. 이 작업은 출력 영상 기준(가로×세로×24×초/1024)으로만 계산한다. fal 청구 내역과 원장을 비교해 확인한다(V-6 X3 확인 때 같이 보면 된다).
- **그 밖의 미확인**:
  - `VNEW_TAKE_DRAFT=true`(기본)에서 `VNEW_TAKE_RESOLUTION`을 720p로 바꾸면 fal이 몇 p로 만들고 얼마를 청구하는지 모른다. 기본 480p에서는 표시·원장이 같다.
  - pricing이 없거나 unit이 "1000 tokens"가 아닌 토큰 단위일 때, 목록은 `shot_usd` 기준("단가 미정" 또는 그 단위 환산)이고 견적·원장(`_take_unit_price`)은 대체 단가 `VNEW_TAKE_TOKEN_UNIT_PRICE`(0.0214)다. 실측 unit은 "1000 tokens"라 지금은 같다.
- **되돌리기**: 플래그가 없다. 이름·숨김만 되돌리려면 `VPOC_FAL_SEEDANCE_FAMILY=`(빈 값, vpoc 계열 라우팅도 함께 꺼짐 — design.md §8). 전체는 커밋 revert.
- **확인**: `python -X utf8 tools/check_vpoc.py` 전부 통과(새 그룹 `check_vnew_model_list_parity` vq-a~g 포함). `node --check frontend/public/vnew.js` 통과. 유료 호출 0회.
- **줄 끝**: 파일 끝 덧붙이기(append)를 한 세 파일(`requirements.md`·`design.md`·`WORKLOG.md`)만 편집 뒤 전체 CRLF였다. `WORKLOG.md`는 덧붙이기 전 LF였음을 확인했다. 스펙 두 파일의 편집 전 상태는 확인하지 못했다(같은 원인으로 추정 — 덧붙이기 없이 고친 `tasks.md`·`ANALYSIS.md`는 LF 그대로였다). 세 파일 모두 LF로 맞췄다. 커밋 전 `git diff --stat`에서 스펙 두 파일이 전 줄 변경으로 보이면 원래 CRLF였던 것이다.
- **관련 파일**: `app/src/vnew/plan.py`, `frontend/public/vnew.js`, `tools/check_vpoc.py`, 스펙 R16·design.md §14·tasks.md 작업 8.

### llm-top-model-first — Bedrock 상위 모델이 첫 시도에 끝내게: 잘림 재시도·설정 거절 미리 적용·예산 (Kiro, claude-opus-5.5)
- **배경**: 설계자 지시(2026-10-28) "제작을 진행하는 것은 sonnet이나 opus, gpt 최대한 상위 모델을 이용해라". 체인(`vpoc/llm._model_chain`)은 이미 상위 모델부터 시작한다(Fable 5.1·GPT-6 Astra → Opus 5.5 → Opus 4.6 → Sonnet 4.6 → Sonnet 4.5). 그런데 상위 모델이 자주 실패해 Opus 4.6이 일을 했다. 프리비즈 설계 한 번이 5분 넘게 걸렸다(02:38:26→02:43:45 UTC).
- **근거 표**(개발계 로그, 2026-10-28 최근 16시간 `vpoc.call` 160건, 설계자 집계):

  | where | 모델 | 성공 | 실패(out=max_tokens) |
  |---|---|---|---|
  | vnew.count_people | fable-5-1 | 56 | 27 (out=120) |
  | vnew.count_people | opus-5-5 | 4 | 23 (out=120) |
  | vnew.count_people | opus-4-6 | 23 | 0 |
  | vnew.previz.grammar | fable-5-1 | 2 | 5 (out=900) |
  | vnew.previz.grammar | opus-5-5 | 3 | 2 (out=900) |
  | vnew.previz.grammar | opus-4-6 | 2 | 0 |

  - 실패 57건 전부 출력 토큰 = 그 호출의 `max_tokens`다. 출력이 잘려 도구 입력(JSON)이 없거나 깨졌다(`ok=False`). count_people 83건 중 23건(28%)은 사실상 Opus 4.6이 처리했다.
  - 5.x 모델은 적응 뒤 toolChoice가 auto라 도구 호출 앞에 글을 쓰기도 하고, 작은 예산에서 잘린다.
  - 프로세스마다 첫 호출이 설정 거절(Opus 5.5·Fable 5.1: toolChoice tool/any·temperature, Astra: temperature)을 겪은 뒤 적응했다(`vpoc.call.adapt`).
- **결정**(스펙 `.kiro/specs/llm-top-model-first/`, design.md §9):
  - D1 — 잘림이면 같은 모델로 1회 더: stopReason(stream `messageStop.stopReason`, converse `response["stopReason"]`)이 `max_tokens`이고 도구 입력이 없으면, 다음 모델로 가기 전에 같은 모델로 `min(max_tokens × 4, 4096)` 예산으로 다시 부른다. 재시도도 잘리면 지금처럼 다음 모델(원래 예산으로 시작).
    - 이유: 원인이 예산이라 같은 상위 모델로 고칠 수 있다. 하위 모델로 내려가지 않는다.
    - 대안: 지금처럼 바로 다음 모델(하위 모델이 일함, 기각), 무한 확대(시간·비용 상한 없음, 기각), 전 호출부 예산 일괄 상향(근거 없는 곳까지 바뀜, 기각).
  - D2 — 재시도 예산이 원래 이하면 재시도하지 않는다(8000·6000 호출부, FACTOR=1). 같은 예산 반복은 같은 결과라서다.
  - D3 — 알려진 거절은 첫 요청부터 뺀다: `VPOC_LLM_NO_FORCED_TOOL`(기본 `claude-opus-5,claude-fable-5`), `VPOC_LLM_NO_TEMPERATURE`(기본 `claude-opus-5,claude-fable-5,gpt-6`). 모델 id 소문자 부분 문자열 비교다. `_adapt`와 프로세스 기억은 그대로 두어 목록에 없는 새 모델은 지금처럼 적응한다.
    - 대안: 정확한 모델 id(버전마다 설정 수정, 기각), 기억 집합을 미리 채우기(`_adapt`와 섞여 끄기 어려움, 기각).
  - D4 — stopReason은 `_converse_once`·`_stream_once`·`_read_stream`의 4번째 반환값이다. 세 함수의 호출자는 `llm.py` 안뿐이다(저장소 검색). usage에 넣으면 호출부·원장으로 새 키가 퍼져 기각했다.
  - D5 — 호출부 예산: `vnew.count_people` 120→400, `vnew.previz.grammar` 900→2000. 로그에서 잘림이 확인된 두 곳만 바꿨다.
    - count_people 출력은 `{"people": n}` 하나다. 400은 앞글 몇 문장까지 들어간다. grammar는 15개 필드(배열 3개·한국어 `reasonKo`)라 2000으로 둔다.
    - 출력 토큰은 쓴 만큼만 과금된다. 최악(예산을 다 씀) 추가는 Opus 단가(25 USD/1M) 어림으로 count_people 약 $0.007/회, grammar 약 $0.028/회다. Fable 5.1 실제 단가는 미확인이다.
  - D6 — 로그: `vpoc.call` 줄 끝에 `stop=<stopReason>`(없으면 `-`), 재시도 때 `vpoc.call.trunc_retry where= model= budget=`(info). 마지막 실패가 잘림이면 오류 문구가 "모델 출력이 출력 한도(N 토큰)에서 잘렸습니다. 출력 앞부분: …"이다(종류 `SchemaViolation`·502 그대로). 프롬프트 본문은 로그에 남기지 않는다.
  - 모델 체인 순서와 기본 모델 id는 바꾸지 않았다.
- **O-7(원장, 이번에 바꾸지 않음)**: 번호는 073 작업 8의 미확인 O-6 다음이다(리뷰 072의 O-7과는 다른 항목). 호출부는 `_converse`가 돌려준 성공 호출의 usage만 계상한다. 실패한(잘린) 시도와 거절된 시도의 사용량은 원장에 빠진다. 그 토큰은 `vpoc.call` 로그(`in=`·`out=`)에만 남는다. D1·D3·D5로 잘린 시도 대부분이 없어질 것으로 본다. 원장 의미를 바꾸려면(실패 시도 계상) 따로 결정한다. 이와 별개로 `previz_grammar.read_cut`은 실제로 답한 모델이 아니라 `GRAMMAR_MODEL`로 계상한다(`charge(usage, GRAMMAR_MODEL)`, 이번 범위 밖).
- **되돌리기**(`config.<target>.env`에서 덮는다. 오버레이는 base에 있는 키만 덮을 수 있다):
  - 미리 적용 끄기: `VPOC_LLM_NO_FORCED_TOOL=`·`VPOC_LLM_NO_TEMPERATURE=`(빈 값) → 거절 뒤 적응(예전 동작).
  - 잘림 재시도 끄기: `VPOC_LLM_TRUNC_RETRY_FACTOR=1`.
  - 호출부 예산·`stop=` 로그·잘림 문구: 플래그 없음(커밋 revert).
- **확인**:
  - `python -X utf8 tools/check_vpoc.py` 전부 통과. 새 그룹 `check_llm_top_model_first` lt-a~i, 기존 rw-a~c(템플릿 env 10개)를 포함한다. 가짜 Bedrock만 썼고 유료 호출은 0회다.
  - 변형 검사(메모리에서만): 잘림 재시도를 끄면 lt-a·b·f, 미리 적용(temperature·강제 도구)을 끄면 lt-d, stream stopReason을 버리면 lt-a·b·f·h, converse stopReason을 버리면 lt-f가 실패한다.
  - `coverage run tools/check_vpoc.py`: 바뀐 함수 143문 중 140문(97.9%). 빠진 곳은 `_converse` 읽기 시간 초과 2회째 break(505)·`NoModel`(535)·`read_cut`의 bytes가 아닌 조각(329)으로, 이번 변경 전부터 있던 분기다.
- **미확인**:
  - Bedrock converse(비스트림)가 도구 입력 도중 잘렸을 때 toolUse 블록 모양. 지금은 payload가 없을 때만 잘림으로 본다.
  - 거절(ValidationException)된 요청의 과금 여부. 미리 적용의 확실한 효과는 지연 제거다.
  - 성공 호출의 `out` 분포. 배포 뒤 `vpoc.call ... stop=`와 `vpoc.call.trunc_retry` 수, 후보별 `ok=True` 비율로 효과를 잰다.
- **남은 이슈**:
  - `claude.plan`(③ 샷 계획)·`claude.safe_image_prompt`·`claude.revise`는 호출부가 model을 넘기지 않아 `VPOC_TEXT_MODEL`(Sonnet 4.5)이 1순위다. 설계자 지시(상위 모델 우선)와 어긋나지만 기본 모델 id 수정 금지 범위라 바꾸지 않았다.
  - Fable 5.1은 `VPOC_TEXT_FALLBACK_MODELS`에 없다. 그래서 Astra가 1순위인 호출은 Astra → Opus 5.5 → … → Sonnet 4.5를 다 실패해야 Fable 5.1로 간다(`docs/ANALYSIS.md` §6.1.1).
  - `max_tokens ≤ 300` 도구 호출부: `vnew.outfit_check` 300(Fable 5.1), `vnew.outfit_translate` 200(Astra → Fable 5.1). 이번에는 바꾸지 않았다(로그 근거 없음). 잘림 재시도가 1200·800으로 받쳐 준다.
- **관련 파일**: `app/src/vpoc/{llm,settings}.py`, `app/src/vnew/{characters,previz_grammar}.py`, `k8s/workloads.yaml.tmpl`, `deploy/config.env.example`, `tools/check_vpoc.py`, `docs/ANALYSIS.md` §6.1.1·§7.3, 스펙 `.kiro/specs/llm-top-model-first/`.

### 개발계 기본 텍스트 모델을 Fable 5.1로 (설계자 지시, Claude 직접 — 설정 1줄)
- **지시**: "그래 개발에만 상위 모델로 변경해서 배포해라"(2026-10-28). 위 남은 이슈의 Sonnet 4.5 1순위 단계가 대상이다.
- **결정**
  - `deploy/config.dev.env`에 `VPOC_TEXT_MODEL=global.anthropic.claude-fable-5-1`을 더한다.
  - 운영(`config.prod.env`)과 base(`config.env.example`)는 그대로 Sonnet 4.5다.
  - 개발 체인은 Fable 5.1 → Opus 5.5 → Opus 4.6 → Sonnet 4.6 → Sonnet 4.5다.
  - Fable 5.1은 첫 요청부터 강제 도구·temperature를 빼고 보낸다(위 미리 적용).
- **확인**: `render.load_config_chain`으로 dev는 Fable 5.1, prod는 Sonnet 4.5인 것을 봤다. check_vpoc 전부 통과.
- **되돌리기**: 이 줄을 지우고 개발 재배포.


### 084 작업 1 — 프리비즈 소품 관통 수정: 앉을 자리·다리 방향·겹침 보정 (Kiro, claude-opus-5.5)
- **배경**: 개발계 "위험한편의점 test3"에서 두 인물이 난간을 관통했다. sit 엉덩이가 0.5로 고정(`vnew-previz.js`)이라 몸통이 상부 가로대(0.94)를, 다리가 하부 가로대(0.40)를 지났다. 인물↔소품 겹침 처리는 어디에도 없었다. 설계자 지시: "관통 수정 끝나면 바로 개발에 배포".
- **스펙 수정(Claude 검토 필수 수정 A·B)**: `.kiro/specs/previz-camera-only/`
  - A: design.md §4.10 고정 입력을 test3 plan.json 실측값(방 8×5×3, 소품 12개·rotY 0, 7컷 자리·facing)으로 바꿨다. 인물 키 1.75·1.70만 가정이다. requirements R1 근거도 실측값으로 고쳤다.
  - B: 길쭉한 앉을 소품의 다리 방향. requirements R1.15-1.19 신규, R1.2·R1.5 `legYawDeg`. design §4.2(다리 기준 = legYaw)·§4.3·§4.4(전면 재작성)·§4.7(렌더러 몸 돌림)·§12.2(cs-a·b·m 수정, cs-o 신규). tasks.md 작업 1(요구사항 R1.1-1.19, cs-a~o).
- **결정**
  - D1 — 앉을 소품은 서버가 결정적 규칙(지정 sitOn → sittable → 이름 → 발이 바닥면 안, 같은 등급은 거리·높은 윗면·id)으로 고른다. 엉덩이 = 소품 윗면. test3은 상부 가로대("걸터앉는", 등급 3)·hipY 0.985다.
    - 대안: LLM에게 높이를 받기(기각 — 숫자는 서버가 정한다는 1-5 교훈), 렌더러만 엉덩이를 올리기(기각 — 카메라 계산이 0.5로 남아 구도가 어긋난다).
  - D2 — 다리 방향 `sit.legYawDeg`. 바닥면 긴 변 ≥ 짧은 변 × 2인 box는 "길쭉한 자리"로 보고 짧은 축 두 법선만 후보로 둔다. 순서는 풀리는 쪽 → facing에 가까운 쪽 → 방 중심 쪽 → + 쪽이다. 그 밖(의자·상자·원기둥)은 facing 먼저, 막히면 축 4방향이다.
    - 이유: facing 90/270(난간 방향으로 마주 봄)이면 facing 방향 다리가 난간 평면에 놓인다. 변형 검사에서 facing 다리로 바꾸면 cut-04 여의준(81.64)이 `lower_rail`과 겹치고 cs-a·g·i·o가 실패했다.
    - 대안: facing을 바꾸기(기각 — 카메라·시선 연출이 바뀐다), 각도만 바꾸기(기각 — 모든 각도가 같은 평면).
  - D3 — 렌더러는 엉덩이·다리를 legYaw로, 몸통·머리를 facing 쪽으로(다리 방향 ±90° 안) 돌린다. 카메라 계산은 facingDeg 그대로다.
  - D4 — 정강이는 앉은 소품과도 겹침을 본다(좌판 밑으로 다리를 넣지 않게). 허벅지는 앉은 소품을 뺀다.
  - D5 — 보기 때 보정은 사본에만 하고 로그를 남기지 않는다. 저장본(plan.json)은 바꾸지 않는다. 설정을 끄면 보기 사본에서 `sit`을 빼 엉덩이 0.5로 그린다. (아래 "필수 수정 C"로 범위를 줄였다: 보정과 함께 설계된 장면만 사본 보정.)
  - D6 — 2차 보정(카메라 계산 뒤)에서 인물이 움직인 컷은 `_rematch_cut`으로 `fit.match`를 다시 잰다. closeapex(CU 투샷) 배우 조정이 인물을 소품 안으로 당기는 경우다(cs-n).
- **test3 결과**(실측 장면, `resolve_scene`): 7컷 11명 모두 `upper_rail`·hipY 0.985·legPitch 75·legYaw 0, unresolved 0. 자리 맞춤 fix는 cut-04 범건우 0.081m·cut-07 범건우 0.122m(z −1.3545·−1.313 → −1.435). 처리 시간 약 3ms.
- **되돌리기**: `config.dev.env`에 `VNEW_PREVIZ_COLLISION=false` → 개발 배포. 보정·sit 없음, 엉덩이 0.5(지금 동작). 저장된 `sit`·`sitOn`·`fit.collision`은 남지만 보기·렌더러가 쓰지 않는다(`sitOn`은 다시 켜면 힌트로 쓴다).
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과(51개 ok 줄). 새 그룹 `check_previz_collision` cs-a~o. 기존 rw-a~c는 템플릿 env 11개로 통과. 기존 그룹 기대값은 바꾸지 않았고 끔 고정도 필요 없었다.
  - 유료 호출 0회(`_guard_real_llm`가 `vpoc.llm._converse`를 막는다).
  - 변형 검사(메모리에서만): `choose_leg_yaw`를 facing 방향으로 바꾸면 cs-a·g·i·o가 실패한다.
- **3단계 리뷰**
  - 보안: 로그에 넣는 actor·prop·cut id는 LLM·사용자 값이라 `_log_id`로 영숫자·`-_.:`만 64자 이하로 남긴다(줄 위조 방지, cs-c). 화면 배지는 `textContent`·`title` 속성만 쓴다(cs-m). `sit` 정규화는 propId가 장면 소품일 때만, hipY 0.2~2.0, legPitch 후보만, legYaw는 유한수만 받는다(NaN·inf는 facing, cs-l). 새 API·외부 전송·비밀값 없음.
  - 성능: 보기 때 보정은 조회마다 돈다. 측정(로컬): test3 3ms, 30컷×6명×소품 40개 무작위 72ms, 인물이 모두 소품 안 125ms, 방을 덮는 상자에 180명 전부 풀 수 없음(고리 탐색 320후보 전부 실패) 612ms. 마지막은 비정상 배치에서만 생긴다(남은 이슈). 메모리는 장면 사본(이미 하던 deepcopy) 외에 늘지 않는다. 외부 호출 0.
  - 운영성: 보정 함수가 예외를 내도 설계는 끝나고(warning `vnew.previz.collision.failed pid= pass=A|B err=`, 1차 실패면 2차도 건너뛴다), 보기는 저장본 그대로 보인다(warning `vnew.previz.collision.view_failed`) — cs-i에서 확인. 설정은 `run_design` 시작 때 한 번 읽는다. 새 설계만 `sit`·`fit.collision`을 저장하고 옛 계획은 보기 때만 보정한다.
- **미확인·남은 이슈**
  - 1인 컷(test3 cut-01~03): 상대가 없으면 `place_cut`이 가짜 상대를 −z에 둔다(기존 규칙). 카메라가 난간 뒤 벽에 막혀 가짜 문법 검사에서 일치도가 0.0이다(보정을 끄면 0.30~0.33, 어느 쪽이든 승인 기준 0.7 미만). 이번 범위 밖이다(카메라 자리 규칙). 실제 연출 문법으로 다시 설계했을 때의 값은 개발계에서 본다.
  - 인물 키 1.75·1.70은 가정이다. 실제 키가 다르면 legPitch만 달라질 수 있다(hipY·legYaw는 같다).
  - 옛 계획 test3는 보기 때 관통 안내만 하고(필수 수정 C), 다시 설계해야 `sit`·`sitOn`이 저장되고 관통이 사라진다.
  - 비정상 배치(방을 덮는 소품 안에 인물이 많음)에서 보기 때 보정이 수백 ms 걸린다(180명 612ms). 필요하면 고리 탐색 상한이나 보기 결과 캐시(revision 기준)를 둔다.
  - 커버리지는 이 실행에서 재지 않았다(Claude가 `coverage run tools/check_vpoc.py`로 잰다).
- **관련 파일**: `app/src/vnew/{previz_collision(신규),previz_schema,previz_fit,previz_camera,previz,settings}.py`, `frontend/public/{vnew-previz.js,vnew.js,vnew.css}`, `k8s/workloads.yaml.tmpl`, `deploy/config.env.example`, `tools/check_vpoc.py`, `docs/ANALYSIS.md` §3.6·§7.1·§10.1, 스펙 `.kiro/specs/previz-camera-only/`.


### 084 작업 1 필수 수정 C — 옛 계획 보기 때 보정이 얼굴을 화면 밖으로 밀어냄 (Kiro, claude-opus-5.5)
- **발견(Claude 검증, 개발계 test3 plan.json 사본)**: 옛 계획을 볼 때 `_scene_view(collision=True)`가 엉덩이를 0.985로 올리는데 카메라는 엉덩이 0.5 기준 그대로라, 거의 모든 컷에서 머리가 화면 위로 나갔다(cut-01 여의준 머리 y 0.21~0.73 → −0.86~−0.29, cut-04 범건우 0.04~0.56 → −0.93~−0.38). 배포 불가 판정. 다시 설계 경로(1차 → `_place_cameras` → 2차)는 맞았다.
- **결정**: 보기 때 보정은 "보정과 함께 설계된 장면"(어떤 컷 인물이든 `sit`, 또는 그 장면 컷의 `fit.collision`)에만 한다. 옛 장면은 보기 사본을 바꾸지 않고 `previz_collision.detect_scene`(신규, 읽기만)으로 관통 컷을 찾아 `previz.collision = {source:"none", needsRedesign:true, cuts}`를 준다. 화면은 녹화 영역과 관통 컷의 컷 패널에 다시 설계 안내 한 줄(`textContent`). 승인·녹화는 막지 않는다.
  - 검출 모양 = 지금 화면에 그려지는 모양(엉덩이 0.5, 허벅지 수평, 다리 = facing). 앉을 소품 윗면이 0.6(0.5 + `OLD_SEAT_TOL` 0.1) 이하일 때만 그 소품 접촉을 허용한다. 그래서 옛 의자 장면(좌판 0.45)은 관통으로 잡지 않고, test3(상부 가로대 0.985)는 7컷 모두 잡는다.
  - 대안: 보기 때 카메라도 다시 계산(기각 — 보기마다 `place_cut`·연출 문법이 필요하고 저장된 fit과 달라진다), 엉덩이만 올리고 카메라를 머리 차이만큼 올리기(기각 — 구도·일치도가 근거 없이 바뀐다).
- **스펙**: requirements R1.11 전면 수정. design §4.1(실행 위치·검출 규칙·`collision` 모양)·§4.9(안내 문구)·§12.2(cs-h 수정, cs-p 신규). 모순을 없애려고 §1 요약 한 줄, §3.2 `previz.collision` 행, §3.3 옛 데이터 두 행, §11 작업 1 행(`detect_cut`·`detect_scene`, 화면 함수·class), §14 R-b도 고쳤다. tasks.md 작업 1 구현 메모·검사 범위(cs-a~p)·완료 조건, V-0 행(열기는 안내 확인, 관통 해소는 다시 설계 1회 뒤).
- **확인**: `python -X utf8 tools/check_vpoc.py` 전부 통과. cs-a~p. 변형 검사(메모리): `previz_view`가 모든 장면을 보정하게 되돌리면 cs-h·cs-p가 실패한다.
  - 로컬 측정(가짜 문법, test3을 보정 끔으로 설계한 옛 장면): 주인공 머리 y는 옛 카메라 그대로 0.02~0.40. 옛 동작(사본 보정)이면 −0.58~−0.18로 화면 밖. 보정 켬으로 다시 설계하면 0.01~0.40(7컷 모두 화면 안).
- **남은 이슈**
  - 되돌리기 방향의 같은 문제: 보정 켬으로 다시 설계한 계획을 `VNEW_PREVIZ_COLLISION=false`로 보면 보기 사본이 `sit`을 빼 엉덩이 0.5로 그린다. 카메라는 hipY 기준이라 머리가 아래로 내려간다(로컬 측정 2인 컷 머리 y 0.72~1.03, 화면 아래 끝에 걸림). 되돌린 뒤에는 다시 설계해야 한다. cs-k 기대값(끔이면 `sit` 뺌)을 바꾸지 않으려고 이번에는 고치지 않았다. 고친다면 끔에서도 저장된 `sit`을 그대로 그리는 쪽이다.
  - 섞인 계획(다시 만들기에서 일부 장면만 다시 설계)은 설계된 장면만 사본 보정되고 옛 장면은 안내가 남는다.


## 2026-10-29

### previz-support-reasoning — 프리비즈 받침 판단: 무엇에·어떻게 앉았는지 그림에서 읽기 (Kiro, claude-opus-5.5)
- **배경**: 설계자 요청 "캐릭터가 앉아 있다 → 어디에? 주차봉 → 주차봉은 무엇이고 어떻게 앉았나?(주차를 막는 기둥, 보통 무릎 위 높이. 그 위에 걸터앉아 있다)". 그림을 보는 유일한 단계(`previz_grammar.read_cut`)는 카메라 문법·동작만 읽고, 장면 설계(Astra → Fable)는 글만 받아 받침 종류·높이를 추측했다. 그래서 받침이 바뀌거나(낮은 기둥 → 1m 난간) 높이가 틀리면 084 보정이 맞아도 그림과 다른 프리비즈가 됐다.
- **스펙**: `.kiro/specs/previz-support-reasoning/`(requirements 107줄·design 175줄·tasks 56줄). 084 작업 2~7은 하지 않았다.
- **결정**
  - D1 — 패널 읽기에 `supports[]`(인물마다 1개)를 더했다. 필드 순서가 생각 순서다: 자세 → 받침 물체(`supportEn`) → 정체·보통 크기(`supportWhatEn`) → 종류 → 서 있는 사람 몸 기준 윗면 높이(`heightCue`) → 추정 m → 앉는 방식(`contact`) → 발 → 근거. `required`는 늘리지 않았다(옛 출력과 같은 필수 키, 거절 위험 없음).
    - 대안: 모델 m 숫자만 받기(기각 — 숫자는 서버가 정한다는 1-5 교훈. `supportHeightM`은 `heightCue`가 없을 때 대비책).
  - D2 — 높이는 서버가 `heightCue` × 인물 키로 바꾼다. knee·waist·chest·shoulder 비율은 카메라 몸 모델(`previz_fit.BODY_FRACTION`)과 같고, above_knee 0.33(1.7m에서 0.56m)은 주차봉 0.5~0.6m에 맞췄다.
  - D3 — 서버 받침 맞춤은 소품 높이만 바꾸고 모양은 바꾸지 않는다. 앉을 소품 후보는 084 `pick_seat` 등급 1과 같은 조건(윗면 0.25~1.30m, 발 → 바닥면 0.6m 이하)이다. 그 안에서 `sitOn` → 받침 낱말 라벨 → `sittable` → 가장 가까운 소품 순이고, 고른 소품을 `sitOn`으로 적어 보정이 같은 소품에 앉힌다.
    - 이유: 지시서는 "`sitOn`이 있으면 그대로"였지만, 보정이 실제로 앉히지 않는 먼 `sitOn` 소품의 높이를 바꾸면 엉뚱한 소품만 줄어든다. 창이 없으면 3m 벽 같은 소품도 0.56m로 줄일 수 있었다(sr-f noProp 검사).
    - 대안: 서버가 받침 소품을 새로 만들기(기각 — 범위를 넘고 장면 일관성이 깨진다).
  - D4 — 목표 윗면 = 같은 소품에 앉은 읽기들의 중앙값(0.25~1.30으로 자름), 차이가 0.15m를 넘을 때만 바꾼다. 바닥 소품은 높이 = 목표, 떠 있는 소품(가로대)은 두께를 두고 위아래로만 옮긴다.
  - D5 — 옛 캐시(`v` 없음)는 컷마다 한 번 다시 읽되 `supports`만 새 값으로 채운다. 카메라 문법은 옛 값이다(사용자가 확인한 카메라가 바뀌지 않게). 실패하면 `supports=[]`로 설계하고 캐시는 그대로 둔다(다음에 다시 시도).
    - 대안: 새 읽기 전체 쓰기(기각 — test3 카메라가 바뀐다).
  - D6 — 프롬프트 지시 단락은 받침 줄이 하나라도 있을 때 한 번만 넣는다(컷마다 되풀이하면 7컷에서 약 2KB가 더 는다). `_DESIGN_SYSTEM` 예 목록에 bollard·post를 더했다. 이 한 문장은 상수라 설정과 상관없이 바뀐다.
  - D7 — `feet: dangling`이면 `solve_legs` 목표 각도를 0°(허벅지 수평)로 둔다. `support`가 없거나 floor·on_prop이면 지금 계산과 같다.
- **test3 결과**(sr-h, 가짜 LLM): 그림 읽기 "parking bollard·above_knee·perch·발 바닥" + Astra 1m 가로대(윗면 1.0) → 가로대 윗면 0.561m(11개 읽기 중앙값), 두께 0.09 그대로, 하부 가로대 그대로. 11명 모두 hipY = 0.561, 관통 0, unresolved 0, 7컷 주인공 머리가 화면 안이다. 옛 캐시 7컷은 한 번씩 다시 읽었고 카메라 문법(vantage)은 옛 값이다.
- **되돌리기**: `config.dev.env`에 `VNEW_PREVIZ_SUPPORT=false` → 개발 배포. 다음 설계부터 프롬프트 받침 줄·받침 맞춤·`fit.support`가 없다(지금 동작). 이미 저장된 장면의 소품 높이와 `fit.support` 화면 줄은 그대로다(다시 설계하면 사라진다). 패널 읽기의 `supports`는 끈 상태에서도 읽어 캐시에 둔다(sr-j).
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과(52개 ok 줄, FAIL·WARN 0). 새 그룹 `check_previz_support` sr-a~l. rw-a는 템플릿 env 12개다. 기존 그룹 기대값은 바꾸지 않았다(emit_shot 스키마·SYSTEM 문자열을 그대로 비교하는 기존 검사는 없었다).
  - 설정 끔 프롬프트는 변경 전 sha256과 같다(sr-e, 첫 설계·다시 만들기 두 입력).
  - 변형 검사(메모리에서만): feet 무시 → sr-g, 정규화가 support를 버림 → sr-i, 허용오차 10m → sr-f·sr-h, 소품 순서 무시 → sr-f, 지시 단락 바꿈 → sr-e·sr-h·sr-j가 실패한다.
  - 검사가 찾은 결함 1건: 문자열을 자른 뒤 끝 공백이 남아 정리가 멱등이 아니었다(sr-a). `_text`가 자른 뒤 `rstrip` 하도록 고쳤다.
  - 커버리지(`coverage run --source=app\src\vnew tools/check_vpoc.py`, coverage 7.17, 원본 사본과 difflib으로 바뀐 줄): 실행 대상 330줄 전부 실행, 100%. 처음 측정은 326/330(98.8%)이었다. 안 탄 4줄(`previz_support` 한국어 라벨 낱말·원소가 모자란 소품·dict가 아닌 장면 인물)에 sr-f 사례를 더하고 다시 쟀다. `previz_support.py` 파일 전체도 100%다.
  - 유료 호출 0회(`_guard_real_llm`).
- **3단계 리뷰**
  - 보안: 모델이 쓴 받침 문자열은 제어 문자·줄바꿈을 한 칸으로 바꾸고 `supportEn` 80자·`supportWhatEn`·`reasonEn` 200자·이름 40자로 자른 뒤에만 프롬프트에 넣는다. 받침 이름에 줄바꿈으로 "[사용자 배치 지시]" 같은 가짜 줄을 넣어도 새 줄이 생기지 않는다(sr-e). 열거값은 정해진 값만 받는다.
  - 보안(이어서): 화면은 `el()`(textContent)과 `title` 속성만 쓴다(sr-k가 node로 `<b>`가 글자 그대로인지 확인). 로그에는 id·숫자만 넣고 id는 `_log_id`로 거른다. 새 API·외부 전송·비밀값은 없다.
  - 성능: 읽기 출력은 받침 항목당 보통 약 110·상한 약 230토큰 는다(sr-b 추정: 2인 보통 컷 전체 501, 4인·모든 문자열 상한 1,609 — `max_tokens` 2000 안). 넘치면 llm-top-model-first 잘림 재시도가 같은 모델로 4,096에 한 번 더 부른다. 그래서 2000을 유지했다(lt-g 기대값 그대로).
  - 성능(이어서): `apply_supports`는 test3 0.44ms, 30컷·6명·소품 40개 8.5ms(로컬, 최솟값)다. 메모리는 장면 사본 1개다. test3 설계 프롬프트는 4,606 → 7,821바이트(+3.2KB)다.
  - 운영: 끄기는 `VNEW_PREVIZ_SUPPORT=false` 한 줄이다. 설정은 `run_design` 시작 때 한 번 읽는다. 받침 맞춤이 예외를 내면 사본을 버리고 warning `vnew.previz.support.failed pid= err=`만 남긴 채 설계를 끝낸다(sr-h). 로그는 `vnew.previz.stage step=support ms= entries= heightFixes= noProp=`, `vnew.previz.support.height loc= prop= from= to= n=`, `vnew.previz.grammar.upgrade cut= ok=`다.
  - 운영(이어서): 옛 캐시는 다음 설계에서 컷마다 연출 문법 읽기 1회(Fable 5.1, 이미지 1장 + 출력 ≈ 0.6~1K토큰)가 더 든다. test3는 7회다. 그다음 설계부터는 0회다(V-2에서 upgrade 로그 0줄로 확인). 다시 읽기가 실패한 컷은 다음 설계에서 다시 시도한다.
- **미확인·남은 이슈**
  - 실제 모델(Fable 5.1)이 `supports`를 지시대로 쓰는지, 특히 앉은 인물의 `heightCue`를 "서 있는 사람 기준"으로 답하는지는 개발계에서 본다(V-1). 토큰 수는 추정이다.
  - 지시서와 다른 점: `sitOn`은 보정이 앉히는 창(0.6m·윗면 0.25~1.30m) 안일 때만 그대로 쓴다(D3). 화면 예문 "발 바닥"은 라벨 표 "바닥에 닿음"을 써서 "발 바닥에 닿음"이다. `step=support` 로그는 받침 항목이 1개 이상일 때만 남긴다(빈 줄을 남기지 않고, 084 cs-i의 stage 줄 수 기대값을 지키려고).
  - 받침 맞춤 뒤 설정을 끄고 보면, 저장된 소품 높이는 그대로이고 `fit.support` 줄도 보인다. 되돌린 결과는 다시 설계해야 보인다.
  - 1인 컷의 가짜 상대 카메라 문제(084 남은 이슈)는 그대로다.
- **관련 파일**: `app/src/vnew/{previz_support(신규),previz_grammar,previz,previz_schema,previz_collision,settings}.py`, `frontend/public/{vnew.js,vnew.css}`, `k8s/workloads.yaml.tmpl`, `deploy/config.env.example`, `tools/check_vpoc.py`, `docs/ANALYSIS.md` §3.6·§7.1·§10.1, 스펙 `.kiro/specs/previz-support-reasoning/`.
