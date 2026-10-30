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

### previz-support-reasoning 필수 수정 A — 실측 검증 → seen 추가: 안 보이는 받침은 적용하지 않는다 (Kiro, claude-opus-5.5)
- **발견(Claude 검증, 개발계 `ci-bca31ec1`, test3 실제 패널을 Fable 5.1로 읽기만)**: 몸·받침이 화면 밖인데도 모델이 컷 설명·맥락으로 받침을 짐작해 썼다. cut-01은 "앉은 자리가 안 그려졌지만 컷 설명이 난간이라" rail·hip·perch, cut-02·07은 "다리·발이 화면 밖이지만 서 있는 것 같다"고 stand·floor였다. 그대로면 서버가 cut-02·07을 sit → stand로 바꿔 이어지는 컷에서 인물이 앉았다 섰다 한다. 같은 cut-02의 옛 읽기 `actions`는 "Sits hunched forward"여서 짐작이 매번 다르다. SYSTEM의 "그려진 것만" 지시로는 막히지 않았다.
- **결정**: `supports[]`에 `seen`(visible·partial·hidden, `posture` 바로 앞)을 더했다. 받침과 닿는 몸(엉덩이·허벅지·발)이 화면 밖이거나 맥락으로 짐작했으면 hidden이다. 정규화는 모르는 값·빈 값·없음을 hidden으로 보고, hidden이면 kind·contact·feet를 none, heightCue를 "", 높이를 null로 비운다(문자열·자세는 그대로 남겨 근거를 볼 수 있다). 서버는 visible·partial만 자세·앉을 소품·높이 맞춤에 쓰고, hidden은 컷 인물에 `support`(seen 포함)만 붙여 `status:"hidden"`으로 둔다. 프롬프트 줄·화면 줄은 없다. stage 로그에 `hidden=`을 더했다(개발계에서 무시된 읽기 수를 본다).
  - 대안: 지시 문장만 더 세게 쓰기(기각 — 실측에서 지시가 짐작을 막지 못했다), 자세 맞춤을 없애기(기각 — 그림에 보이는 자세 차이까지 버린다).
- **확인**: `check_vpoc` 전부 통과(52개 ok 줄). sr-a·b·e·f·h·i·k 갱신, sr-m 신규. sr-m은 test3 실측 읽기 3종을 seen 없음·hidden으로 넣으면 pose·sitOn·소품이 그대로이고, visible·partial이면 지금처럼 적용되는지(cut-02·07 sit → stand) 본다. run_design 경로에서는 7컷 모두 앉은 채이고 가로대는 그대로, `hidden=10`이다. 기존 시나리오는 고정값에 `seen:"visible"`을 넣어 기대값을 지켰다. 변형 검사(메모리): 없는 seen을 visible로 보면 sr-a·k·m, seen을 무시하면 sr-a·e·f·k·m이 실패한다. 이번 수정의 바뀐 실행 줄 18/18, 086 전체 339/339(coverage 7.17).
- **남은 이슈**: 이 수정 전 코드로 이미 v2 캐시가 저장된 계획은 `supports`에 seen이 없어 모두 hidden으로 본다(받침 맞춤이 꺼진 것과 같다). 그 계획에서 받침을 다시 쓰려면 그 컷을 다시 읽기 지시하거나 `GRAMMAR_VERSION`을 3으로 올려 1회 다시 읽게 해야 한다(이번에는 올리지 않았다). 실제 모델이 seen을 지시대로 쓰는지는 개발계 V-1에서 본다.



## 2026-10-30

### vnew-live-fx 스펙 — 기호·분위기 · 실사 캐스팅 · 통 테이크 실사 버전 (Kiro, claude-opus-5.5, 스펙만)
- **배경**: 설계자는 외부 저장소 `ottlseo/video-vibecode`(참고 구현)의 실사 구성(실사 캐스팅·기호·캐릭터 묘사)과 vnew의 캐릭터 설정·프리비즈·영상화를 합쳐 보려 한다. 비교는 `.kiro/handoff/logs/087.log` 끝 보고서다. 참고 구현의 장점은 컷 간 일관성, 단계별로 보이는 진행, 부드러운 실사화와 미리 뽑는 캐스팅, 기호, 캐릭터 묘사다. 단점은 인체 뒤틀림과 소리 없음이다. vnew는 3D 덕에 뒤틀림이 적고 소리가 잘 되지만, 컷을 나누면 일관성이 깨지고 통 테이크 실사가 없다.
- **이번 실행**: 스펙 `.kiro/specs/vnew-live-fx/`(requirements·design·tasks)와 `docs/ANALYSIS.md` §3.6.1(A·B·C가 닿는 지금 코드 경로)만 썼다. 앱 코드는 바꾸지 않았다.
- **PM 결정(Claude, 설계자 부재)과 근거**
  - 결정 1 — 세 묶음으로 나누고 A → B → C 순서로 따로 구현·검증·개발 배포한다.
    - 근거: 묶음마다 비용과 위험이 다르다. A는 텍스트 비용뿐이고 기존 웹툰 통 테이크 품질에 바로 닿는다. B는 사진 장당 $0.04다. C는 회차마다 영상 비용(480p 15초 ≈ $3.1)이 들고 B의 사진을 쓴다. 따로 배포하면 문제가 생긴 묶음만 끌 수 있다.
    - 대안: 한 번에 구현(기각 — 검토·되돌리기 단위가 너무 크다). C를 먼저(기각 — 캐스팅 사진 없이 실사 정면만으로는 참고 구현의 "부드러운 실사화" 이점이 줄어든다).
  - 결정 2 — A: 컷마다 `fx`(적용 0~2)·`fxSuggest`(추천 0~2)·`moodKo`(≤60자)를 두고, 기호 열거값·문장은 참고 구현(`vpoc/compose.py:329-336`)과 같게, 추천 규칙은 d9a48ad(`vpoc/llm.py:1217-1222`)와 같게 둔다. 콘티를 만들 때 함께 채우고, 이미 있는 계획에는 ③ "기호·분위기 채우기"(빈 컷만, 상위 모델 1회, 패널 이미지 포함)를 둔다. ③에서 고치고 ④에서 읽기 전용으로 보이고, TIMELINE·RULES와 컷 단위 프롬프트에 넣는다. 묘사 규칙(손 두 개·머리색·무늬 위치)과 장소 일관성 줄을 넣는다. 되돌리기 `VNEW_FX`.
    - 근거: 설계자가 기호를 "장면 분위기와 표정을 어떻게 연출할지 알 수 있다"고 평가했다. 모양을 못 박은 문장은 참고 구현 exp2(땀방울을 핏대로 그린 사고)에서 나온 것이라 그대로 쓴다. 손·머리색·무늬 규칙도 참고 구현 실험(여의준 줄무늬·금발)에서 나왔다.
    - 설계 결정: 기호는 콘티 도구에 섞지 않고 따로 부른다(`emit_fx`). 지금 콘티 호출은 글만 보내고, 기호에는 그림이 필요하다. 콘티 출력 예산(8,000토큰)을 나누지 않고, 버튼과 같은 함수를 쓴다. 영상 프롬프트에 쓸 영어 분위기 `moodEn`(≤80)을 함께 저장한다(한국어를 영상 모델에 넣지 않으려고). 그려진 기호는 3컷 한도에서 뺀다(원작 충실 우선). 기호·분위기만 고친 저장은 프리비즈 승인을 풀지 않는다(기호는 마네킹·녹화에 영향이 없다).
    - 대안: 콘티 도구에 필드 추가 + 콘티 호출에 패널 이미지 추가(기각 — 콘티 결과 자체가 바뀌어 회귀 위험), 패널 읽기(`previz_grammar`) 단계에서 읽기(기각 — ③에서 보여야 하는데 그 단계는 ④다).
  - 결정 3 — B: 캐릭터마다 `casting`(preset idol·real, lookEn, candidates, history ≤12, chosen)을 두고 Seedream 4.5 text-to-image로 글만으로 2장을 만든다. 금액은 장당 설정값으로 보이고 확인 뒤 만든다. 성별·대명사는 분석으로 정한다(참고 구현 `CAST_BASE` 남성 고정 수정). 고른 사진은 실사 영상 인물 참고로 쓰고, 없으면 실사 정면이다. 기존 실사 시트·3D는 바꾸지 않는다. 되돌리기 `VNEW_CASTING`.
    - 근거: 참고 구현이 그림 편집 방식에서 웹툰 선·회색 눈·그림 포즈가 남는 것을 확인하고 글로 만드는 방식으로 바꿨다(`vpoc/live.py:27-32`). 여성 캐릭터에 "male … He is"가 들어가는 버그는 그대로 옮기면 안 된다.
    - 설계 결정: 분석 도구에 `genderEn`(선택 필드)을 더하고, 사용자가 화면에서 고칠 수 있게 한다. 참고 구현의 동기 `fal.run` 2회 대신 fal 큐로 제출·조회해 파드가 재시작돼도 이어 받는다. 이어할 때 아직 제출하지 않은 칸은 다시 제출하지 않는다(뜻밖의 과금 방지).
  - 결정 4 — C: 웹툰 통 테이크가 끝난 gen에 "실사 버전 만들기"(금액 확인)를 둔다. 같은 녹화(@Video1) + 캐스팅 사진(없으면 실사 정면) + 실사 장소 뷰를 쓰고, 웹툰 문구 없이 실사 화풍과 기호의 실제 표현(`REAL_FX`)을 쓰며, "webtoon"이 남으면 제출하지 않는다. 결과는 `unit.real` → `finalReal`, 다시 만들면 `realHistory`(≤3). 고르기는 지금 `selected`, 웹툰·실사 나란히 보기. 소리 검사 거절은 한국어 안내와 "소리 없이 다시 만들기"(웹툰 테이크 포함). 되돌리기 `VNEW_TAKE_REAL`.
    - 근거: 지금 통 테이크는 실사를 건너뛴다(`_empty_take_unit`). 같은 녹화를 쓰면 vnew의 장점(구도·인체·타이밍 고정)을 실사에서도 지킨다. 참고 구현은 웹툰을 실사로 바꾸지 않고 처음부터 실사로 다시 만든다. 우리도 같은 방식이다(웹툰 결과를 입력으로 쓰지 않는다).
    - 설계 결정: 실사 회차마다 파일 이름을 바꾼다(`final-real-r{n}.mp4`, 지금 키는 고정이라 덮어쓴다). 조사 중 찾은 기존 결함 두 가지를 함께 고친다. 하나는 previz-take에서 실사 실패가 gen을 바로 partial로 바꾸는 `_fail_variant`, 다른 하나는 `createdAt`만 보는 `_gen_stale`이다. fal 거절 판정에 소리 종류(`failureKind: "audio"`)를 더한다. 지금은 소리 거절이 영상 내용 거절과 같은 문구가 돼 화면이 구분할 수 없다. 소리 거절 처리와 결함 수정은 `VNEW_TAKE_REAL`과 상관없이 적용한다(결함 수정이라서).
  - 결정 5 — 보안 원칙을 바꾸지 않는다. 이미지·영상은 data URI로만 보내고, 서명 URL을 외부로 주지 않는다. `_defaults` 복사와 다른 계정 역할 빌림은 가져오지 않는다.
    - 근거: 087 보고서 4장. 참고 구현은 8MB 넘는 클립에 서명 URL을 fal에 보낸다(`vpoc/shotfix_service.py:202-204`). `_defaults`는 우리 IRSA(`users/*`만)에서 접근도 안 된다. 캐스팅 요청은 글만 보내고, 결과 사진은 우리 버킷 `users/{owner}/characters/{id}/casting/`에 둔다.
  - 결정 6 — 프로덕션 LLM은 지금 최상위 설정을 쓴다. 기호 채우기는 `ANALYZE_MODEL`(콘티와 같다, Fable 5.1), 캐릭터 분석은 지금 체인(Astra → Fable)이다.
    - 근거: llm-top-model-first(2026-10-28) 원칙. 기호 판단은 그림을 읽어야 해서 상위 모델이 맞다. max_tokens는 8,000이다(40컷 추정 출력 ≈ 6K).
  - 결정 7 — 유료 호출은 검사에서 모두 가짜다(그룹 `fx-`·`cast-`·`lr-`).
    - 근거: 이번 주 작업 원칙(`.kiro/steering/quality-week.md` 4). 실제 비용 확인은 묶음마다 "배포 뒤 확인"(VA·VB·VC)으로 설계자 승인 뒤에만 한다.
- **작업 나누기**(tasks.md): A 4개(작음 1·중간 3), B 3개(작음 1·중간 2), C 3개(작음 1·중간 1·큼 1). 묶음 끝 작업이 리뷰·커버리지·문서를 맡는다.
- **확인**: 스펙 3개 파일을 만들었다(requirements 221줄·design 606줄·tasks 102줄). 앱 코드(`app/`·`frontend/`·`tools/`·`k8s/`·`deploy/`)와 참고 구현 폴더는 읽기만 했고 실행하지 않았다.
- **Claude 결정(2026-10-30 검토, 열린 질문 9개)**: 스펙 3개 파일에 반영했다(requirements.md 「검토 결정」 표, design·tasks 본문의 "(결정 N)").
  1. 금액 표시 — 새 기능(캐스팅 뽑기·실사 버전·기호 채우기)의 확인창에는 금액 숫자를 보인다. 기존 생성 확인창은 그대로 둔다(설계자 2026-10-20 규칙). 기호 채우기는 토큰 추정 금액(`fxFill.estimateUsd`)을 "약 $X"로 보인다.
  2. 새 필드 `fxDrawn`·`moodEn`·`analysis.genderEn`을 둔다.
  3. 그려진 기호(`fxDrawn`)는 "같은 기호 3컷 넘게 추천 금지" 한도에서 뺀다(원작 충실).
  4. 캐스팅 사진은 통 테이크 실사(묶음 C)에만 쓴다. 컷 단위 Kling 실사 payload(`_previz_payload_elements`, `_previz_payload_image_list`)는 바꾸지 않는다. tasks B3의 해당 항목을 지우고 cast-k를 "캐스팅을 골라도 컷 단위 실사 payload가 지금과 같다"로 바꿨다.
     - 근거: 가슴 위 캐스팅 사진과 전신 실사 후면(시트 얼굴)이 Kling에서 다른 사람처럼 보일 위험(design §7).
  5. 다시 뽑아도 이미 고른 사진(`chosen`)은 유지한다.
  6. 사용자가 고친 분위기는 [기호·분위기 채우기]에서 영어로 옮긴다. 옮기기 전 컷은 `moodEn`이 비고, 영상 프롬프트에 분위기를 넣지 않는다(한국어는 넣지 않는다). 화면 분위기 칸 옆에 "영어로 옮기려면 [기호·분위기 채우기]" 안내를 둔다(A4).
  7. 실사 STYLE 문구 두 곳을 바꾼다(묶음 C): "warm pastel color grading"을 빼고, "romance K-drama cinematography"를 "K-drama cinematography"로 바꾼다.
  8. vpoc ② 소리 거절 문구가 새 문구로 바뀌는 것을 받아들인다.
  9. 새 기본값(켬) 때문에 깨지는 기존 검사는 함수 본문만 `_pin_vnew(VNEW_FX=False)`로 고정한다. 기대값은 바꾸지 않고 보고한다.
- **남은 이슈**: `_gen_previz_stale`이 previz-take를 늘 false로 본다(ANALYSIS §3.6.1 문제 5, 범위 밖). Seedance가 실사 사진 참고를 likeness로 거절할 가능성이 있어 C의 첫 개발계 확인(VC-1)에서 본다.

### vnew-live-fx 묶음 A — A1·A2 기호 모듈·저장 규칙·채우기 호출·작업·라우트 (Kiro, claude-opus-5.5)
- **범위**: tasks.md A1·A2만 했다. 영상 프롬프트(A3)·화면·ANALYSIS 갱신(A4), 묶음 B·C는 하지 않았다. 첫 단계로 Claude 결정 1~9를 스펙 3개와 위 2026-10-30 항목에 반영했다.
- **바꾼 것**: `vnew/fx.py`(신규, 순수 함수), `vnew/settings.py`(`VNEW_FX` 기본 true, `VNEW_FX_MAX_PANELS` 20), `vnew/prompts.py`(`EMIT_FX`·`FX_SYSTEM` design 원문), `vnew/plan.py`(`update_conti`, `run_conti`, `_analyze_fx`, `_fx_blocks`, `fill_fx`, `start_fx_fill`, `run_fx_fill`, `_commit_step(extra=None)`, `cancel_task`·`reconcile_stale` 단계 목록, `plan_view` `features`·`fxFill`), `vnew/routes.py`(`POST /p/<pid>/conti/fx`), `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`(`VNEW_PREVIZ_SUPPORT` 바로 뒤), `tools/check_vpoc.py`(`check_vnew_fx`, `PARITY_ENV_KEYS`).
- **결정**
  - D1 — 켬 상태에서 아무것도 바뀌지 않은 콘티 저장도 프리비즈 승인을 풀지 않는다(`only_fx_changed`가 같은 목록을 True로 본다).
    - 이유: A4의 [기호·분위기 채우기]는 지금 수정본을 먼저 `PUT /conti`로 저장한다. 같은 목록 저장이 승인을 풀면 RA2.2("승인을 풀지 않는다")가 깨진다.
    - 대안: 기호 필드에 차이가 있을 때만 유지(기각 — 버튼이 승인을 푼다). 영향: `check_vnew_previz` (e)가 같은 컷 PUT으로 해제를 기대한다 → 결정 9대로 끔 고정.
  - D2 — 채우기 확인창 금액(결정 1)은 서버 추정값 `plan_view.fxFill.estimateUsd`(`fx.estimate_usd`, 원장과 같은 단가표)다. 토큰 추정이라 서버는 확인 금액을 받지 않는다.
    - 대안: 화면에서 계산(기각 — 단가표가 서버 설정이다), `confirmUsd` 검사(기각 — 실제 금액과 달라 의미가 없다).
  - D3 — 채우기 실패 문구는 `VpocError`면 그 문구(한 줄, 300자), 그 밖은 "기호·분위기를 채우지 못했습니다(종류)."다. 내부 예외 원문을 plan에 저장하지 않는다(기존 `run_conti`의 `str(exc)` 저장은 바꾸지 않았다).
  - D4 — 채우기 요청 글에서 컷 id는 한 줄로 정리해 보이고, 대상 판단·합치기는 저장된 id 그대로 한다. TRANSLATE `moodKo`는 JSON 문자열로 감싼다(따옴표·줄바꿈으로 가짜 줄을 만들 수 없다).
  - D5 — 끔이면 라우트가 프로젝트를 보기 전에 404 `FxDisabled`다(`start_fx_fill`도 한 번 더 막는다).
  - D6 — `merge_fill` 통계에 `filledIds`(한도 계산용), `apply_cap`은 뺀 추천 수(로그 `capped=`)를 돌려준다. design §A.1 표에 적었다.
  - D7 — `features`는 지금 `{fx}`만 둔다. B1·C1이 `casting`·`takeReal`을 더한다(design §2에 적었다).
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과(53개 ok 줄, FAIL·WARN 0). 새 그룹 `check_vnew_fx`(fx-a~g·fx-m·fx-n 저장 쪽). rw-a는 템플릿 env 13개다.
  - 끔 비교: 같은 시나리오(run_conti → 기호만 바꾼 PUT → 같은 PUT)를 변경 전 트리와 새 코드(`VNEW_FX=false`)로 돌려 저장본·LLM 호출·저장 횟수·원장이 모두 같다(임시 폴더의 사본에서만 실행).
  - 변형 검사 28개(메모리에서만 소스를 바꿔 다시 실행): 모두 `check_vnew_fx`가 실패로 잡았다.
  - 바뀐 줄 커버리지(`coverage run --source=app\src tools/check_vpoc.py`, coverage 7.17, 원본 사본과 difflib): 실행 대상 439줄 중 436줄(99.3%). 안 탄 3줄은 id 없는 컷 건너뛰기 1줄과 `_fx_fill_view` 방어 except 2줄이다.
  - 유료 호출 0회: 최종 전체 실행 출력에 `vpoc.call` 줄이 없다. LLM 층의 boto3·urlopen을 막고 기록하는 실행에서도 boto3 시도는 0이다.
- **기존 검사 끔 고정(결정 9, 기대값 그대로)**: `check_vnew_plan`·`check_vnew_locations`(POST /conti → 채우기가 가짜가 아닌 LLM을 부른다), `check_vnew_previz`((e) 같은 컷 PUT의 승인 해제 기대, D1).
- **개발 중 사고**: 끔 고정 전 전체 검사 1회에서 `check_vnew_locations`·`check_vnew_previz`의 POST /conti가 실제 `_converse`로 채우기를 불러 Bedrock 호출을 시도했다. IAM이 AssumeRole·InvokeModel을 거절해(AccessDenied) 모델은 실행되지 않았고 비용은 없다. 그 뒤 두 검사를 끔으로 고정했다.
- **3단계 리뷰**
  - 보안: 저장·프롬프트에 들어가는 글(분위기·대사·상황·이름·id)은 `clean_text`(제어·서식 문자·줄바꿈 → 한 칸, 길이 자름)를 지나고, 기호는 열거값만 받는다. 채우기 요청 글은 컷마다 한 줄이다(fx-n). 이미지는 Bedrock 요청 바이트로만 보내고 서명 URL을 만들지 않는다. 라우트는 쓰기 권한(`_project_for(write=True)`)을 본다. 로그에는 개수·ms만 남긴다. 다른 계정 역할·`_defaults`는 없다.
  - 성능: 채우기 1회 = LLM 1회(max_tokens 8000). 보낼 패널은 ≤ 20장이고 블록에는 고른 타일만 남는다. `ordered_tiles`는 대본 단계와 같은 메모리 봉우리(업로드 원본 + 타일 ≤ 40장)이고 계획당 작업은 하나다. 콘티 만들기는 채우기 호출만큼 길어진다(추정 30~60초, 실측은 VA-1). 번역만이면 타일을 읽지 않는다. `plan_view` 추가 계산은 컷 수에 비례한다.
  - 운영성: 로그 `vnew.conti.fx pid= fill= translate= panels= filled= translated= missed= capped= ms=`, `vnew.conti.fx.failed pid= err=`, `vnew.fxFill.discarded pid= task=`. 되돌리기는 `config.<target>.env`에 `VNEW_FX=false` 한 줄이다(끔 비교로 지금과 같음 확인). 진행 중 채우기는 끝까지 간다. 파드가 죽으면 fxFill 작업은 30분 뒤 멈춤으로 정리되고 다시 누를 수 있다. 그동안 대본·콘티 작업도 막힌다(지금 대본·콘티와 같은 한계).
- **남은 이슈**
  - 운영(`VPOC_RAW_ERRORS=false`)에서 `FxDisabled`·`ContiNotReady`·`NothingToFill`의 문구는 "처리 중 오류가 발생했습니다 (코드: …)"로 감춰진다(`vpoc/errors.USER_FACING_KINDS`, 범위 밖). A4 화면이 대상 0일 때 버튼을 막으면 대부분 보이지 않는다.
  - 기존 검사 `check_vnew_generate`·`check_vnew_shot_brief`(와 그 스레드)가 fal 가격 조회 GET을 실제로 61회 보낸다(생성 아님, 유료 아님). 변경 전 트리에서도 같은 61회다.
  - 실제 모델(Fable 5.1)이 drawnFx·3컷 한도·감정 없음 → 빈 추천을 지시대로 쓰는지, 금액 추정이 맞는지는 VA-1에서 본다.
  - `docs/ANALYSIS.md` 갱신은 tasks대로 A4에서 한다.


### vnew-live-fx 묶음 A — A3·A4 영상 프롬프트·분석 규칙·화면·문서·리뷰 (Kiro, claude-opus-5.5)
- **범위**: tasks.md A3·A4와 Claude 검토 피드백 1·2만 했다. 묶음 B·C는 하지 않았다. 커밋·push 하지 않았다.
- **검토 피드백 처리**
  - 피드백 1 — A2가 감싼 `check_vnew_plan`·`check_vnew_locations`·`check_vnew_previz` 본문의 들여쓰기를 되돌렸다(세 함수 모두 A 이전 사본과 글자까지 같음). 고정은 `main()`의 부르는 줄에서 `_run_fx_off(check)`(= `with _pin_vnew(VNEW_FX=False): check()`)로 한다. 원본 사본과 difflib으로 잰 `check_vpoc.py`의 공백만 바뀐 줄: 5,658 → 0. tasks 공통 규칙·결정 9 문구도 "부르는 줄에서 고정"으로 고쳤다(B·C에서 다시 들여쓰지 않게).
  - 피드백 2 — `vpoc/errors.USER_FACING_KINDS`에 `FxDisabled`·`ContiNotReady`·`NothingToFill`을 더했다. 안전 근거: 이 kind를 던지는 곳 5곳(`plan.py` 3, `routes.py` 1, `generate.check_preconditions` 1)이 모두 고정 한국어 문구이고 외부 원문을 싣지 않는다. 다른 kind는 건드리지 않았다(fx-l이 `TaskRunning`은 여전히 가려지는지도 본다). 부수 효과: ⑤ 선행 조건의 `ContiNotReady`("먼저 콘티를 완성해 주세요.")도 운영에서 보인다.
- **바꾼 것**: `vnew/generate.py`(`RULE_BODY`·`CUT_RULE_BODY`, `_take_timeline_entries` fx·moodEn, `_take_prompt` mood·FX 조각·RULES·줄이기 단계·최소형, `_cut_prompt` FX·EXPRESSION·MOOD·CONTINUITY·⑥-1, `_cast_notes_system`·`_read_cast_notes`), `vnew/characters.py`(`_analyze_system`·`_analyze`), `vpoc/errors.py`(피드백 2), `frontend/public/vnew.js`(기호 구역 `FX_KEYS`~`previzFxLine`, `cutCard`·`renderContiStep`·`decidePlanStep`·`previzPanelMatchCard` 배선), `vnew.css`(`vnew-fx-*`·`vnew-mood-input`·`vnew-previz-fx-line`), `tools/check_vpoc.py`(`check_vnew_fx_prompt` fx-h~k·n, `check_vnew_fx_screen` fx-l, 고정 입력·sha 상수, `_run_fx_off`), `docs/ANALYSIS.md`(§3.6·§3.6.1 A 구현 결과·§7.1·§10.1), 스펙 3개(고정 방식 문구, A3·A4 체크).
- **결정**
  - D1 — 변경 전 sha256(fx-i)은 코드를 바꾸기 전에 잰 5종이다: 통 테이크 6컷(tp-a)·12컷(tp-c), 컷 단위 웹툰(hm-c)·실사(hm-c)·실사 긴 입력(sb-f 문장). 입력은 기존 take 검사 고정값을 모듈 수준 도우미(`_fx_take_fixture`·`_fx_cut_fixture`)로 옮겨 쓴다. 기존 검사 본문은 바꾸지 않았다. 끔 상태는 컷에 기호·분위기 필드가 있어도 같은 해시다.
  - D2 — 분위기의 한국 이름 → 로마자 치환과 한글 검사는 컷마다 한 번만 하고(`moodText`), 뺀 컷마다 로그 `vnew.gen.take_mood_dropped take= at= reason=hangul` 1줄이다. 새 줄이기 단계를 넣을지(`has_content`)는 이 결과로 판단한다(한글로 빠진 분위기만 있으면 단계를 넣지 않는다).
  - D3 — 컷 단위 MOOD는 "{컷 moodEn}. {지시서 moodEn}"이다. 기존 ③ MOOD 줄이기가 뒤 문장부터 떼므로 지시서 분위기가 먼저 빠진다. 실사 EXPRESSION에는 짧은 형이 없어 ⑥-1을 건너뛴다.
  - D4 — [기호·분위기 채우기]는 저장(PUT) 뒤 서버가 다시 센 대상 수·금액이 확인한 값과 다르면 새 숫자로 한 번 더 묻고, 대상이 0이면 시작하지 않는다("저장했습니다. 채울 컷이 없습니다."). 리뷰(과금)에서 찾았다: 고친 분위기는 저장해야 번역 대상이 되므로 처음 확인한 숫자가 틀릴 수 있다. 대안: 화면에서 대상을 다시 세기(기각 — 금액 단가표가 서버 설정이다).
  - D5 — `decidePlanStep`에 `fxFill`을 더해 채우기 중에는 ③을 보인다. design 함수 목록 밖이지만 이 한 줄이 없으면 진행 막대(RA7.2) 대신 ②가 열린다.
  - D6 — 그린 기호는 title("패널에 그려진 기호")에 더해 칩 안에 "그림" 표를 보인다(지시 "그린 기호 표시"). 분위기 칸 안내는 저장된 moodEn이 없을 때와, 입력을 바꿔 저장하면 moodEn이 비워질 때 보인다.
  - D7 — 화면 기호 이름은 design·`fx.py`의 `FX_LABEL_KO`(눈물 줄기·땀 한 방울·볼 홍조·분홍 반짝이 배경·속도선)를 그대로 썼다. 지시서 예시("땀방울", "볼 홍조 빗금")와 두 개가 다르다(남은 이슈 3).
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과(55개 ok 줄, FAIL·WARN 0). `[ok  ]` 줄에 fx-a~n이 모두 있다(fx-a~g·m·n 저장 쪽 / fx-h~k·n 프롬프트 쪽 / fx-l).
  - `python -m py_compile`(generate·characters·errors·check_vpoc), `node --check frontend/public/vnew.js` 통과.
  - 끔 상태 프롬프트 sha256 5종이 변경 전과 같다(fx-i). 켬 상태 통 테이크 ≤ 4,000바이트, 컷 단위 ≤ 2,500바이트, 모든 타임스탬프가 남는다(fx-h·fx-j).
  - 프롬프트 바이트(fx-h 출력, 변경 전 = 끔):

    | 입력 | 끔 | 켬 기호 없음 | 켬 보통(기호 1·분위기 ≈45자) | 켬 최악(기호 2·분위기 80자) |
    |---|---|---|---|---|
    | 통 테이크 6컷(tp-a) | 3,945B styleTrim · MOTION·HARMONY 있음 | 3,043B 최소형 | 3,226B 최소형 | 3,427B 최소형 |
    | 통 테이크 12컷(tp-c) | 3,922B 최소형 | 3,641B 최소형 | — | 2,469B 최소형(연기 없는 후보) |
    | 컷 단위 웹툰(hm-c) | 2,362B | 2,455B(장소 요약 뗌) | 2,463B(기호 2+분위기, 장소 요약·배경 뗌) | 긴 입력 2,490B(FX 짧은 형) |

  - 바뀐 줄 커버리지(묶음 A 전체 .py, `coverage run --source=app\src tools/check_vpoc.py`, 원본 사본과 difflib): 실행 대상 495줄 중 492줄(99.4%). 안 탄 3줄은 A2의 id 없는 컷 `continue` 1줄과 `_fx_fill_view` 방어 except 2줄이다. JS는 coverage.py 밖이고, fx-l이 기호 구역의 순수 함수 전부와 DOM 조립(`fxRow`·`fxToolbar`·`previzFxLine`)을 node 가짜 DOM으로 실행한다.
  - 변형 검사 26개(메모리·임시 사본에서만, Python 18 · JS 8): 모두 해당 검사가 실패로 잡았다.
  - 유료 호출 0회: LLM 층 boto3와 urlopen을 막고 기록하는 실행에서 boto3 시도 0. urlopen은 `api.fal.ai/v1/models/pricing` 가격 조회 GET 61회(`check_vnew_generate`·`check_vnew_shot_brief`와 그 스레드, 생성 아님·무료)이고, 막으면 그 두 검사가 실패한다. A 이전 트리 사본에서도 61회·같은 두 실패라 이번 변경과 무관하다.
  - 공백만 바뀐 줄 0, CRLF 0(바뀐 18개 파일 전부). 기존 함수 정의는 하나도 지우지 않았다(ast·정규식 비교).
- **기존 검사 끔 고정(결정 9, 부르는 줄에서, 기대값 그대로)**: A2의 `check_vnew_plan`·`check_vnew_locations`·`check_vnew_previz`에 더해 A3에서 `check_vnew_take_harmony_motion` 1개. 이유: 켬이면 hm-a 6컷 입력이 묘사 규칙(+229B) 때문에 최소형으로 떨어져 MOTION·HARMONY가 빠진다(hm-a 'do NOT copy'·'parallax'·'contact shadows', hm-b 'day light'). 나머지 take 검사(`check_vnew_take`·`_prompt`·`_costume`·`_prompt_polish`·`_prompt_budget`·`_on_screen`)는 켬 그대로 통과한다.
- **3단계 리뷰(묶음 A 전체)**
  - 보안
    - 프롬프트로 가는 사용자·모델 글(moodEn)은 저장 때와 프롬프트를 만들 때 두 번 `clean_text`(제어·서식 문자·줄바꿈 → 한 칸, 80자)를 지나고, 기호는 열거값만 문장으로 바뀐다. 줄바꿈·"RULES:"·모르는 기호를 넣어도 새 줄·새 머리글이 생기지 않는다(fx-n 두 쪽). moodKo(한국어)는 어느 영상 프롬프트에도 들어가지 않는다(결정 6).
    - 분석·의상 읽기 SYSTEM 추가 문장은 고정 문자열이다. 새 외부 전송·서명 URL·비밀값은 없다. 로그에는 take id·시간 범위·바이트·단계 이름만 남긴다.
    - 화면은 `el()`(textContent)과 속성(`title`·`placeholder`·`aria-pressed`)만 쓰고 fx-l이 가짜 DOM에서 innerHTML 0을 확인한다. 경로의 프로젝트 id는 `encodeURIComponent`. 라우트 권한은 A2 그대로(`_project_for(write=True)`).
    - 발견: 운영에서 채우기 거절 문구 3종이 가려졌다(피드백 2) → 조치함. 사용자가 쓴 영어 분위기는 그대로 영상 프롬프트에 들어간다. 이미 사용자가 고칠 수 있는 promptEn·의상 덮어쓰기와 같은 신뢰 수준이라 그대로 둔다.
  - 성능
    - A3·A4는 외부 호출을 늘리지 않는다(프롬프트 문자열만). 분석 SYSTEM +282자, 의상 읽기 SYSTEM +110자(호출당 입력 ≈ +70·+30토큰). 화면은 버튼 1번에 PUT 1 + POST 1, 진행 중 폴링은 기존 5초 간격이다. 칩은 칩 줄만 다시 그린다.
    - 메모리: 프롬프트 조립에 컷당 문자열 몇 개(수 KB)만 더한다. 채우기의 타일 메모리는 A2 리뷰 그대로(대본 단계와 같은 봉우리, 계획당 작업 1개).
    - 발견(조치 안 함, 남은 이슈 1·2): 켬이면 통 테이크 RULES가 229B 길어져 test2 규모 6컷이 최소형으로 떨어지고 MOTION·HARMONY가 빠진다. 컷 단위는 CONTINUITY가 160B 길어져 SETTING이 더 자주 줄어든다.
  - 운영성
    - 되돌리기: `VNEW_FX=false` 한 줄이면 프롬프트 5종·분석 SYSTEM이 구현 전과 글자까지 같고(fx-i·fx-k) 화면 요소가 숨는다(fx-l). 저장된 기호·분위기 필드는 남는다(R0.3). 프롬프트는 제출 때 설정을 읽으므로 진행 중 gen의 남은 칸만 바뀐 설정을 따른다.
    - 진행 중 작업: 배포 시점에 이미 제출된 칸은 영향이 없고, queued 칸·다시 시도는 새 프롬프트(묘사 규칙 포함)로 나간다. 채우기 중에는 ③에 진행 막대·[취소](`POST /cancel` → `cancel_task` fxFill)가 보인다. 파드가 죽으면 30분 뒤 멈춤 정리(A2) → 실패 문구 + "[기호·분위기 채우기]로 다시 시도할 수 있습니다."
    - 로그: `vnew.gen.take_prompt bytes= steps=`에 mood40·moodDrop·fxShort가 보인다. 새 로그 `vnew.gen.take_mood_dropped`.
    - 발견: 채우기 중 화면이 ②로 넘어갔다(`decidePlanStep`) → 조치함(D5). 저장 뒤 대상 수·금액이 확인창과 달라질 수 있었다 → 조치함(D4).
- **남은 이슈**
  1. **켬 상태 통 테이크에서 MOTION·HARMONY가 빠진다**(가장 중요). test2 규모 6컷(tp-a): 끔 3,945B(styleTrim만) → 켬 줄이기 9단계를 다 써도 4,019B라 최소형 3,043B로 떨어진다. hm-a 입력(의상·목소리 있음)도 끔 3,954B → 켬 3,230B 최소형이다. 기호가 하나도 없어도 같다(RULE_BODY는 켬이면 늘 붙는다). 줄이기 단계 중 avoidShort(긴 형과 같은 문자열)·action55(70자 하한 때문에 이 입력에서 0B)는 줄이는 양이 0이다. design대로 구현하고 기존 검사는 끔 고정했다. 개발 배포(VA-2) 전에 결정이 필요하다. 대안:
     - (가) 묘사 규칙 짧은 형 단계(예 ≈120B)를 action55 다음·최소형 전에 둔다(문구는 Claude가 정한다).
     - (나) 최소형 전에 묘사 규칙을 빼는 단계를 둔다(손 두 개 규칙을 잃는다).
     - (다) 최소형 후보에도 MOTION·HARMONY 짧은 형을 넣는다(069 v2 규칙 "지우지 않는 등급"을 최소형까지 넓힘).
     - (라) 그대로 두고 VA-2에서 눈으로 본다.
  2. 컷 단위(Kling) CONTINUITY 문장 +160B: hm-c 짧은 지시서도 2,362 → 2,522B가 돼 기호가 없어도 SETTING의 장소 요약을 떼고 2,455B가 된다. 기호 2개+분위기면 SETTING(장소 요약·배경)이 모두 빠진다. 컷 단위는 줄이기 단계 로그가 없어(기존) 개발계에서 FX 짧은 형이 쓰였는지 로그로 볼 수 없다.
  3. 화면 기호 이름: 지시서 예시는 "땀방울"·"볼 홍조 빗금"인데 design·`fx.py`·화면은 "땀 한 방울"·"볼 홍조"다. 스펙을 따랐다. 바꾸려면 `fx.FX_LABEL_KO`·`vnew.js FX_LABEL_KO`·fx-a 기대값을 함께 바꾼다.
  4. 운영에서 `TaskRunning`(채우기·대본·콘티 진행 중 다시 누름)은 여전히 가려진다(이번 범위 밖). 진행 중에는 버튼이 없어 거의 보이지 않는다.
  5. VA-1(채우기 1회 ≈ $0.15)·VA-2(웹툰 통 테이크 1회 ≈ $3.1)는 설계자 승인 뒤 개발계에서 한다. 실제 모델이 분위기를 80자 영어로 쓰는지, 확인창 금액이 원장과 맞는지는 그때 본다.


### vnew-live-fx 묶음 A — 090 필수 수정 2건: 통 테이크 MOTION·HARMONY 보존, 컷 단위 SETTING 보존 (Kiro, claude-opus-5.5)
- **범위**: Claude 피드백(090 남은 이슈 1·2)만 고쳤다. 기호 이름·`TaskRunning` 문구는 그대로다. 커밋하지 않았다.
- **바꾼 것**: `vnew/generate.py`(`RULE_BODY_SHORT`·`MOTION_MIN`·`HARMONY_MIN`·`_harmony_min` 신규, `CUT_RULE_BODY` 짧은 형, `_take_prompt` RULES `state.body`·`bodyShort` 단계·최소형 MOTION·HARMONY, `_cut_prompt` `_continuity`·`bodyDrop`·단계 로그), `tools/check_vpoc.py`(fx-h 합격 기준 `_FX_TAKE_MUST`·짧은 형 상수, fx-j 기본 입력 SETTING·로그, fx-n 로그 컷 id, `check_vnew_take_harmony_motion` 끔 고정 해제), 스펙(design §A.4·§A.5·§8, RA4.2·RA4.3·RA5.2·RA5.3, tasks A3 메모), `docs/ANALYSIS.md` §3.6.1·§10.1.
- **고친 줄이기 순서**
  - 통 테이크: `styleTrim → avoidShort → voice40 → voiceDrop → [mood40] → appearance100 → costume110 → dialogue25 → audioShort → [moodDrop → fxShort] → {bodyShort} → action55 → 최소형`. `{bodyShort}`는 켬이면 늘 들어간다(묘사 규칙이 늘 붙으므로). 최소형 후보는 연기 있는 4개 × MOTION·HARMONY 원문 → 같은 4개 × 짧은 형 → 연기 없는 3개 × 원문 → × 짧은 형 → 의상을 줄인 ASSETS × 짧은 형 → 지금 후보(없음) 순이다. 단계 로그 끝에 `motionHarmonyShort`·`motionHarmonyDrop`.
  - 컷 단위: `⓪ bodyDrop → ① 장소 요약 → ② 배경 → ③ MOOD → ④ BLOCKING → ⑤ 대사 연기 → ⑥ SHOT → ⑥-1 FX 짧은 형 → ⑦ ACTION → 바이트 자르기`. 로그 `vnew.gen.cut_prompt cut= variant= bytes= steps=`.
- **결정**
  - D1 — `RULE_BODY_SHORT` 문구: Claude 제시 문구는 116B라 "(including where its pattern is)" → "(and where its pattern is)"만 바꿔 110B로 맞췄다(뜻 같음). 대안: "incl."(112B, 여전히 초과), 한도 우선 대신 문구 우선(기각 — 한도가 합격 기준).
  - D2 — `CUT_RULE_BODY`: Claude 예시("hair and outfit colors")는 무늬 위치가 빠져 RA5.2와 어긋나서 "hair color and outfit patterns as placed in the references"(89B)로 썼다.
  - D3 — HARMONY 짧은 형은 장소 빛 문장(`lightEn`, 최대 80자)을 빼고 시간대만 남긴다. 시간대를 모르면 `@Video1's light`(원문의 "the time of day shown in @Video1"는 160B를 넘긴다). 늦은 시간대(sunset·evening·dusk·night·midnight)에서는 "no drift to sunset or night"가 모순이라 "no drift in time of day"로 쓴다.
  - D4 — 최소형 후보 순서는 069 등급을 따른다. MOTION·HARMONY는 연기 핵심과 같은 등급, 외형·의상보다 위다. 그래서 "외형을 뺀 원문"을 "외형을 둔 짧은 형"보다 먼저 보고, 연기 있는 짧은 형을 연기 없는 원문보다 먼저 본다.
  - D5 — 컷 단계 로그는 설정과 상관없이 남긴다(프롬프트 글자는 그대로라 fx-i가 같다). 프롬프트를 실제로 바꾼 단계만 적는다. 컷 id는 `clean_text` 40자라 줄바꿈으로 가짜 로그 줄을 만들 수 없다(fx-n).
- **바이트(fx-h·fx-j 출력, 같은 고정 입력)**

  | 입력 | 끔 | 켬(수정 전, 090) | 켬(수정 후) |
  |---|---|---|---|
  | 통 테이크 6컷 기호없음 | 3,945B · M·H 원문 | 3,043B 최소형 · M·H 없음 | 3,901B · M·H 원문 · 묘사 짧은 형(최소형 아님) |
  | 통 테이크 6컷 보통 | 3,945B | 3,226B · 없음 | 3,874B 최소형 · M·H 원문 · 연기·기호 짧은 형 남음 |
  | 통 테이크 6컷 최악 | 3,945B | 3,427B · 없음 | 3,875B 최소형 · M·H 원문 · 연기·기호 짧은 형 남음(외형 뺌) |
  | 통 테이크 12컷 기호없음 | 3,922B · 없음 | 3,641B · 없음 | 3,857B 최소형 · M·H 짧은 형 · 연기 남음 |
  | 통 테이크 12컷 최악 | 3,922B · 없음 | 2,469B · 없음 | 3,627B 최소형 · M·H 원문 · 연기 없음 |
  | hm-a 입력(의상·목소리) | 3,954B · 원문 | 3,230B · 없음 | 3,855B 최소형 · M·H 원문 |
  | 컷 단위 웹툰 기본 | 2,362B | 2,455B · 장소 요약 뗌 | 2,452B · steps none · SETTING = 끔 |
  | 컷 단위 실사 기본 | 2,193B | 2,353B | 2,283B · steps none · SETTING = 끔 |
  | 컷 단위 웹툰 기호2+분위기 | — | 2,463B · 장소 요약·배경 뗌 | 2,497B · bodyDrop만 · SETTING = 기본 |

- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과(55개 ok 줄, FAIL·WARN 0). `check_vnew_take_harmony_motion`은 끔 고정을 풀고 켬 상태에서 기대값 그대로 통과한다(hm-a 6컷 입력이 MOTION·HARMONY 원문을 남긴다).
  - fx-i 끔 sha256 5종이 변경 전과 같다. 컷 단위 로그는 프롬프트에 영향이 없다.
  - 합격 기준 `_FX_TAKE_MUST`(검사 상수): 6컷 기호없음·보통·최악 = MOTION·HARMONY 원문 + 묘사 규칙 짧은 형, 12컷 기호없음 = 짧은 형, 12컷 최악 = 원문, 모두 ≤ 4,000B·모든 타임스탬프. 짧은 형 바이트: `RULE_BODY_SHORT` 110, `CUT_RULE_BODY` 89, `MOTION_MIN` 146, `HARMONY_MIN` 149~157(시간대 표 전부).
  - 1800 caps: 연기 있는·없는 후보가 모두 넘쳐 의상을 줄인 ASSETS + MOTION·HARMONY 짧은 형(1,728B). 1500 caps: 그것도 넘쳐 빠지고 로그 `motionHarmonyDrop`(1,423B).
  - 변형 검사 15개(메모리에서만): 모두 fx-h·fx-j·fx-n이 잡았다.
  - 바뀐 줄 커버리지(묶음 A 전체 .py): 571줄 중 561줄(98.2%). `generate.py` 127줄 중 120줄. 안 탄 7줄은 `_cut_prompt`의 단계별 중간 return 5줄과 바이트 자르기 2줄이다(기존 경로, 고정 입력이 그 지점에서 끝나지 않는다).
  - 유료 호출 0회(boto3 시도 0, urlopen은 기존 가격 조회 GET 61회 그대로). 공백만 바뀐 줄 0(`check_vpoc.py`·`generate.py` 짝 비교), CRLF 0.
- **3단계 리뷰(이번 변경분)**
  - 보안: 새 문장은 모두 고정 문자열이다. 사용자 입력이 새로 들어가는 곳은 컷 단계 로그의 컷 id 하나이고 `clean_text`(줄바꿈·제어 문자 → 한 칸, 40자)를 지난다.
  - 성능: 최소형 후보가 켬이면 7개 → 14개(원문·짧은 형) + 의상 줄인 1개 + 지금 7개로 는다. 문자열 합치기뿐이고 모델 호출·메모리 봉우리는 그대로다. 컷 단위는 단계마다 `_current()`를 한 번 더 만든다(≤ 10회, 수 KB).
  - 운영성: 켬·끔 되돌리기는 그대로다(`VNEW_FX=false` → fx-i). 개발계에서는 `vnew.gen.take_prompt … steps=…,motionHarmonyShort`와 `vnew.gen.cut_prompt … steps=bodyDrop,…`로 어느 컷이 줄었는지 볼 수 있다.
- **남은 이슈**
  1. 12컷 최악(컷마다 기호 2개)은 연기 있는 최소형이 모두 넘쳐 연기와 기호가 빠지고 MOTION·HARMONY 원문이 남는다(수정 전에도 연기는 빠졌다). 대안: 최소형에 "연기는 남기고 기호를 뺀" 후보를 연기 없는 후보보다 먼저 둔다. 이번 범위 밖이라 하지 않았다.
  2. 통 테이크 `avoidShort`는 긴 형과 같은 문자열이라 줄이는 양이 0이다(069부터, 범위 밖).
  3. 컷 단위 로그는 끈 상태에서도 나온다(새 로그 줄 1개/컷 제출). 프롬프트·저장은 그대로다.


### vnew-live-fx 묶음 B — B1·B2 캐스팅 문장·성별, 뽑기·이어하기·라우트 (Kiro, claude-opus-5.5)
- **범위**: tasks.md B1·B2만 했다. B3(영상에 쓰기·화면·ANALYSIS 갱신)와 묶음 C는 하지 않았다. 커밋하지 않았다.
- **바꾼 것**: `vnew/casting.py`(신규 — 문장: `base_text`·`PRESETS`·`preset_of`·`normalize_gender`·`auto_gender`·`resolve_gender`·`age_phrase`·`align_gender`·`look_sentence`·`pattern_warning`·`prompt_look`·`cast_prompt`, 뽑기: `update_settings`·`start_draw`·`_spawn`·`run_draw`·`reconcile`·`choose`·`view`·`chosen_key`·`auto_look`·`current_look`·`total_usd`·`parse_confirm_usd`, 래퍼 `_fal_request`·`_fetch_url`, `_CAST_RUNNING`), `vnew/characters.py`(`casting_key`, `_analyze_tool`·`_with_gender_en`·`_ANALYZE_GENDER_RULE`, `_analyze_system` 성별 문장, `_analyze` 도구, `analyze_sources` genderEn, `detail_view`→`_casting_detail`), `vnew/routes.py`(PUT `/characters/<id>/casting`, POST `…/casting/draw`·`…/casting/choose`), `vnew/settings.py`(`VNEW_CASTING`·`_MODEL`·`_USD`·`_COUNT`·`_SIZE`·`_STALL_S`·`_POLL_MAX_S`), `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`(`VNEW_FX` 바로 뒤), `tools/check_vpoc.py`(`check_vnew_casting` cast-a~j·cast-m, `PARITY_ENV_KEYS`, `_run_casting_off`).
- **결정**
  - D1 — 참고 구현의 `_HAIR`·`_SUBJECT`·`_AGE`·`_PATTERN`·`_POSITION`과 무늬 경고 문구는 design §B.2에 원문이 없다(참고 구현 폴더는 읽지 않는다). 같은 뜻으로 새로 썼다. 프리셋 두 문장(`_IDOL`·`_REAL`)만 design 원문이고, male 결과는 그 원문과 글자까지 같다(cast-a). 대안: 원문을 받아 옮기기(Claude가 붙여 주면 상수만 바꾸면 된다).
  - D2 — 성별은 RB2.1 순서(사용자 → `analysis.genderEn` → 외모 문장 주어 man·boy·woman·girl → unknown)다. 옛 캐릭터는 다시 분석하지 않고 genderEn 없음 = unknown으로 보고 다음 단계로 간다. 보기에 `gender`(사용자 값, ""=자동)·`genderAuto`를 둔다.
  - D3 — 나이가 10대·학생이면 "early 20s"로 올린다. 프리셋이 "adult … in {pos} 20s"라 미성년 사진을 만들지 않는다.
  - D4 — 후보 id = fal 요청 id의 sha256 앞 10자. 저장 → 원장 → 칸 ready 순서다. 파드가 그 사이 죽어 같은 요청을 다시 받아도 같은 S3 키·같은 원장 키라 두 번 계상하지 않는다. 대안: 임의 id(기각 — 재처리 때 이중 계상).
  - D5 — 고른 사진이 history 12개 밖으로 밀리면 마지막 자리에 남긴다(결정 5 보호). 대안: 그대로 버림(기각 — 6회 뒤 chosen이 없는 id를 가리킨다).
  - D6 — 제출 실패는 5xx라도 그 칸을 실패로 두고 다시 제출하지 않는다(요청이 만들어졌을 수 있다 → 이중 과금 방지). 조회·받기의 일시 오류(5xx·429·연결)는 다음 순회에 다시 한다.
  - D7 — 뽑기 스레드의 뜻밖의 예외는 상태를 그대로 두고 끝낸다. 하트비트가 멈추면 이어하기가 제출한 칸만 다시 조회하고, `VNEW_CASTING_POLL_MAX_S`가 끝을 보장한다. 대안: 바로 failed(기각 — 제출해 과금될 결과를 버린다).
  - D8 — `casting_key`는 design §B.1·§9대로 `characters.py`에 둔다(tasks B2 줄은 casting.py라고 적었다). 키 규칙이 한 파일에 모인다.
  - D9 — design 표에 없는 입력 오류 2개: 400 `BadLook`(외모 문장이 글이 아님), 400 `NoCandidateId`(choose 본문에 candidateId 키 없음). 고르기 풀기는 `""`를 명시해야 한다(키가 빠진 요청이 고른 사진을 지우지 않게).
  - D10 — 결과 사진 받기: https만, 120초, 25MB 상한, 풀기 전 2,000만 픽셀 상한, 프로세스 전체 한 장씩(`_PHOTO_SEM`). 저장은 RGB JPEG 긴 변 1536·q90.
  - D11 — 후보의 느낌(preset)은 그 회차 문장에서 읽는다(뽑는 중 느낌을 바꿔도 후보 표시는 회차 값).
- **확인**
  - `python -m py_compile` 통과, `python -X utf8 tools/check_vpoc.py` 전부 통과(56개 ok 줄, FAIL·WARN 0). `[ok  ]` 줄에 cast-a~j·cast-m.
  - cast-a: male `base_text`(idol·real)가 design 원문 상수와 같다. 3성별 × 2프리셋 × 14외모 = 84문장에서 female·unknown에 낱말 male·"He is"·his가 없고, 모두 webtoon이 없다(주입 문장·webtoonish 포함).
  - cast-d: `VNEW_CASTING=False`면 분석 도구 JSON·SYSTEM sha256이 변경 전과 같다. cast-j: 끔이면 라우트 3개 404 `CastingDisabled`, 상세 `casting` = `{enabled:false}`.
  - cast-f·cast-m: 가짜 fal 제출 13건 본문 키가 모두 정확히 `{prompt, num_images, image_size}`이고 http·data:·base64·서명 URL·`users/`가 없다. 원장 = 만든 사진 수(성공 2·한 칸 실패 1·두 칸 실패 0·이어하기 1·원장 쓰기 실패 0). cast-h: 이어하기에서 queued 칸 제출 0.
  - 변형 검사 21개(실제 소스를 한 개씩 바꾸고 되돌림): 모두 `check_vnew_casting`이 잡았다.
  - 바뀐 줄 커버리지(coverage 7.17, `--source=app\src`, 원본 사본과 difflib): 699줄 중 692줄(99.0%).
  - 실제 fal·Bedrock 호출 0회: 캐스팅 검사는 `casting._fal_request`·`_fetch_url`을 가짜로 두고 `vpoc.fal._request`·`characters._fal_request`를 막아 호출 0을 확인한다. boto3·urlopen을 막는 실행에서 boto3 시도 0, 캐스팅 검사 urlopen 0(기존 가격 조회 GET 61회는 그대로).
  - LF: 바뀐 파일 CRLF 0, 공백만 바뀐 줄 0, 지운 함수 정의 0.
- **기존 검사 끔 고정(결정 9, 기대값 그대로)**: `check_vnew_fx_prompt` — (fx-k)가 분석 SYSTEM을 "상수 + 묘사 규칙"과 글자째 비교한다. 켬이면 성별 문장이 더 붙는다. `main()`의 부르는 줄에서 `_run_casting_off(check_vnew_fx_prompt)`로 고정했다.
- **3단계 리뷰**
  - 보안: fal에는 글만 보낸다(본문 3키, cast-m). 결과 주소는 https만 받고 요청 id는 `[A-Za-z0-9._-]{1,128}`만 받는다(주소·S3 키 끼워 넣기 방지). 외모 문장은 `clean_text`(줄바꿈·제어 문자 → 한 칸, 300자)를 지난다. 칸 오류 문구는 `error_gist`(URL 쿼리·data URI·키 제거)를 지난다. 저장 경로는 `users/{owner}/characters/{id}/casting/`이고 다른 사용자는 404다. 화면에는 presigned 주소만 준다. 로그에 문장·주소·키를 남기지 않는다.
  - 성능(512Mi): 사진은 프로세스 전체에서 한 장씩 받고 줄인다(봉우리 ≈ 받은 파일 ≤25MB + RGB ≈15MB). 리뷰 중 고침: 픽셀이 많은 작은 PNG가 풀 때 수백 MB가 될 수 있어 풀기 전 픽셀 상한을 넣었다. 외부 호출은 칸마다 제출 1 + 5초마다 상태 조회 + 결과 1 + 받기 1이고, 뽑기 1회는 최대 900초다. S3는 순회마다 읽기 3·쓰기 1(하트비트)이다.
  - 운영성: 로그 `vnew.casting.draw_start`·`submitted`·`slot_failed kind=`·`transient`·`saved`·`done status= ready= failed= timeout=`·`resume queued_failed=`·`claim_lost`·`run_failed`·`run_end ms=`·`ledger_failed`·`reconcile_failed`. 되돌리기는 `config.<target>.env`에 `VNEW_CASTING=false` 한 줄이다. 진행 중 뽑기는 끝까지 가고, 저장된 casting은 지우지 않는다. 재시작: 하트비트가 600초 넘게 멈추면 조회 때 이어받는다(queued → 실패, submitted → 조회).
- **남은 이슈**
  1. D1의 참고 구현 원문(머리색·주어·나이 표, 무늬 낱말·위치 낱말, 경고 문구)을 확인하지 못했다. 같은 동작인지는 VB-1과 원문 대조로 본다.
  2. 운영(`VPOC_RAW_ERRORS=false`)에서는 B 오류 코드(`CastingDisabled`·`CastingRunning`·`CostMismatch`·`NoLook`·`CandidateNotFound`·`BadPreset`·`BadGender`·`BadLook`·`NoConfirmUsd`·`NoCandidateId`)의 문구가 "처리 중 오류가 발생했습니다 (코드: …)"로 감춰진다(`vpoc/errors.USER_FACING_KINDS`, B 파일 목록 밖). B3 화면이 코드별 문구를 두거나 errors.py에 더해야 한다.
  3. 4xx 거절 문구는 `vpoc.fal.rejection`의 영상용 문구("이 샷을 거절했습니다")가 그대로 나온다(design대로).
  4. 제출 직후 "submitted" 기록 전에 파드가 죽으면 그 요청 1건은 결과를 못 받는다(fal 과금 가능, 원장 없음, design §7 한계).
  5. 재시작 뒤 이어받기까지 최대 10분 동안 화면은 "만드는 중"이다.
  6. `analysis.genderEn`이 unknown이어도 외모 문장에 man·woman이 있으면 그 낱말로 성별을 정한다(RB2.1 순서). "unknown이면 성 중립"을 genderEn만으로 읽으면 결과가 다르다.
  7. boto3·urlopen을 전역으로 막는 실행에서 `check_vnew_generate`·`check_vnew_shot_brief`가 실패한다. 묶음 A 때의 같은 실행에서도 같은 두 검사가 실패했다(가격 조회 GET 61회, 이번 변경과 무관).
  8. `docs/ANALYSIS.md` 갱신은 tasks대로 B3에서 한다.


### vnew-live-fx 묶음 B — B3 영상에 쓰기·캐스팅 화면·091 남은 이슈·문서·리뷰 (Kiro, claude-opus-5.5)
- **범위**: tasks.md B3와 091 남은 이슈 2·3·5(PM 결정)만 했다. 묶음 C는 하지 않았다. 컷 단위 Kling 실사 payload(`_previz_payload_elements`·`_previz_payload_image_list`)와 `vpoc/fal.py`는 바꾸지 않았다. 커밋하지 않았다.
- **바꾼 것**
  - `vnew/generate.py`: `_char_entries`(켬이면 항목에 `castingKey`), `_casting_key_of`·`_real_front_key`(신규), `casting` import.
  - `vnew/casting.py`: `_MSG_REJECTED`·`_REJECT_GIST_MAX`(신규 상수), `_slot_error`의 4xx 거절 문구.
  - `vpoc/errors.py`: `USER_FACING_KINDS`에 9종.
  - `frontend/public/vnew.js`: 캐스팅 구역(`CASTING_*` 상수, 순수 함수 `castingCount`·`castingUsdText`·`castingGenderLabel`·`castingGenderOptions`·`castingDrawLabel`·`castingConfirmText`·`castingCostChangedText`·`castingProgress`·`castingUsingText`·`castingPhotoCaption`·`castingLockText`, DOM `castingSection`), `renderDetail` 배선, `schedulePoll` 조건. `vnew.css`: `vnew-casting-*`.
  - `tools/check_vpoc.py`: `check_vnew_casting`에 cast-k·cast-l과 cast-g 거절 문구 경우, 모듈 상수 `_CAST_REJECT_MSG`·`_CAST_ENTRY_KEYS_BEFORE`·`_CAST_USER_FACING`·`_CAST_SCREEN_NODE_HARNESS`·`_CAST_SCREEN_NODE_CASES`, 도우미 `_cast_screen_problems`.
  - 문서: `docs/ANALYSIS.md`(§3.6 모듈 표, §3.6.1 「B 구현 결과」·캐스팅 화면·결정 4·남은 위험, §7.1 `VNEW_CASTING`, §10.1), tasks.md B3 체크.
- **결정**
  - D1 — `castingKey`는 dict 리터럴을 고치지 않고 `out[-1]`에 켬일 때만 더한다. 끔 경로의 항목은 B 이전과 키·값까지 같다(cast-k가 `set(entry) == _CAST_ENTRY_KEYS_BEFORE`와 값 전체를 본다). `casting.chosen_key`가 예외를 내면 ""와 경고 로그 `vnew.gen.casting_key_unreadable char= err=`(예외 종류만)로 넘긴다. 대안: 예외를 그대로 올림(기각 — 컷 단위는 이 키를 쓰지 않는데, 깨진 캐스팅 값 하나가 ⑤ 선행 조건과 모든 생성을 막는다).
  - D2 — `_real_front_key`는 정의와 검사만 있고 부르는 곳이 없다(묶음 C `_take_payload(variant="real")`·`start_real`이 부른다). 결정 4대로 컷 단위 payload 두 함수는 손대지 않았다. cast-k가 캐스팅을 고르기 전·후·끔 세 경우의 payload 4종(elements·imageList × 실사·웹툰)을 deep-equal과 sha256으로 비교하고, 캐스팅 사진 바이트가 들어가지 않는지, 두 함수 소스에 `castingKey`·`_real_front_key`가 없는지 본다.
  - D3 — 캐스팅 4xx 거절 문구(091 남은 이슈 5): "fal이 이 캐스팅 문장을 거절했습니다. 외모 문장을 바꿔 다시 뽑아 주세요." 뒤에 ` (fal: {요지})`를 붙인다. 요지는 `error_gist`(URL 쿼리·data URI·fal 키 제거) 120자이고, 비면 문구만 쓴다. 400·403·404·422 모두 같은 문구다. 영상용 `vpoc.fal.rejection`(likeness·sensitive 구분, "이 샷을")은 쓰지 않는다. 대안: `rejection`의 종류만 빌려 캐스팅 문구를 셋으로 나눔(기각 — `fal.py` 문구 표에 묶이고, 글만 보내는 t2i라 likeness 안내 "인물 이미지를 바꿔"가 맞지 않는다).
  - D4 — `USER_FACING_KINDS`(091 남은 이슈 2): `CastingDisabled`·`CastingRunning`·`NoLook`·`CandidateNotFound`·`BadPreset`·`BadGender`·`BadLook`·`NoConfirmUsd`·`NoCandidateId` 9종을 더했다. 모두 처음 넣는 이름이다(중복 없음). 던지는 곳(`casting.py` 10곳, `routes.py` 2곳)이 모두 고정 문자열이다. cast-l이 `app/src` 전체를 ast로 훑어 이 9종의 `VpocError` 문구 인자가 모두 상수인지 본다. `NoConfirmUsd`는 ⑤ 생성 시작(`routes.py`)의 같은 고정 문구에도 적용된다. **`CostMismatch`는 뺐다**: vnew(`casting.py`·`generate.py`) 문구에 화면이 보낸 금액이 들어가고, vpoc(`regen.py`·`routes.py`)는 `confirmUsd` 입력 원문을 그대로 싣는다. 운영에서도 봉투의 `type`은 남으므로 화면은 문구가 아니라 `err.type === "CostMismatch"`로 판단한다.
  - D5 — 성별(091 남은 이슈 3): 서버 규칙은 그대로다(genderEn unknown이어도 외모 문장의 man·woman으로 정함). select의 "자동" 옆에 서버가 정한 `genderAuto`를 "자동(분석: 남/여/모름)"으로 보인다.
  - D6 — 409 `CostMismatch`: 상세를 다시 읽어 버튼 글을 새 금액으로 바꾸고 "금액이 바뀌었습니다. 새 금액은 $X입니다. 다시 누르면 새 금액으로 확인합니다."를 보인다. 이때 보내지 않는다. 사용자가 다시 누르면 새 금액이 든 확인창을 보이고, 확인해야 새 `confirmUsd`로 보낸다. 다시 읽는 사이 기능이 꺼졌으면 상세를 다시 그린다. 대안: 다시 읽은 직후 바로 새 확인창을 띄움(기각 — 버튼 글과 안내가 바뀌는 것을 먼저 보이는 편이 덜 놀랍고, 검사로 "보내지 않음"을 분명히 볼 수 있다).
  - D7 — 뽑는 중이거나 캐릭터를 만드는 중에는 느낌·성별·외모 칸과 [저장]·[되돌리기]·[뽑기]를 막고 이유를 한 줄 보인다. 그동안 상세가 5초마다 다시 그려져 쓰던 글이 사라지기 때문이다. 다시 만드는 중에는 분석 문장도 바뀐다. 서버는 뽑는 중 PUT을 받지만 화면에서만 막는다. 대안: 다시 그릴 때 입력 중인 글을 상태에 보관(기각 — 상태가 늘고, 뽑기는 최대 900초다).
  - D8 — [저장]과 뽑기는 textarea가 처음 값에서 바뀌었을 때만 `lookEn`을 보낸다. 느낌·성별만 바꿔 저장해도 자동 문장이 "고친 문장"(`lookEdited`)으로 굳지 않는다. [분석 문장으로 되돌리기]는 `lookEn: ""`를 보내고, 고친 문장이 없으면 누를 수 없다. 외모 문장이 비면 뽑기를 막고 title로 "먼저 캐릭터를 만들어 분석해 주세요."를 보인다.
  - D9 — 사진 `src`는 상세 응답의 presigned `url`만 쓴다. url이 비면(presign 실패) img 대신 "사진을 불러오지 못했습니다."를 보인다. id 없는 항목은 그리지도 세지도 않는다. 고른 사진이 이전 사진에 있으면 "이전 사진 (n)" 접기를 펼쳐 배지를 보인다. 사진 아래에는 "느낌 · n회차"를 보인다.
  - D10 — cast-l은 fx-l과 같은 방식이다. `vnew.js`에서 `el`·`schedulePoll`·캐스팅 구역을 잘라 가짜 DOM·`api`·`window.confirm`과 함께 node로 돌린다. 기대값은 design §B.6 문구다.
- **기존 검사 기대값 변경(보고)**: cast-g의 거절 문구 기대값 2줄을 새 캐스팅 문구로 바꿨다(PM이 정한 동작 변경, 091 남은 이슈 5). 다른 검사의 기대값은 바꾸지 않았다. 새 끔 고정도 없다(`_run_casting_off(check_vnew_fx_prompt)` 1곳은 B1 그대로).
- **확인**
  - `python -m py_compile`(generate·casting·errors·check_vpoc), `node --check frontend/public/vnew.js`(rc 0) 통과.
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 56줄, FAIL·WARN 0. `[ok  ]` 캐스팅 줄에 cast-a~m이 모두 있다. `check_vnew_real_start`·`check_fal_model_caps`(컷 단위 payload)·take 검사는 기대값 그대로 통과한다.
  - 바뀐 줄 커버리지(묶음 B 전체 .py, `coverage run --source=app\src tools/check_vpoc.py`, B 이전 사본과 difflib): 714줄 중 707줄(99.0%).
    - `casting.py` 621/626. 안 탄 줄: `_sleep` 본문, `_spawn` 스레드 시작 실패 3줄, `_poll_slot` `except _ClaimLost: raise`.
    - `characters.py` 29/29, `routes.py` 38/40(B2 그대로: 기존 `delete_outfit` except 2줄을 difflib이 바뀐 줄로 셈), `settings.py` 7/7, `generate.py` 12/12.
    - `errors.py`는 집합 리터럴이 문장 1개(바뀌지 않은 첫 줄)로 잡혀 0/0이다. 동작은 cast-l이 운영 봉투로 본다.
  - 변형 검사 20개(실제 파일을 한 군데씩 바꾸고 `check_vnew_casting` 실행 → 원래 바이트로 되돌림, sha256 같음 확인). Python 10 · JS 10, 모두 잡혔다. 예: 끔에서도 castingKey를 더함, `_real_front_key` 순서 뒤집기, Kling elements·imageList에 `_real_front_key` 사용, 영상용 거절 문구, error_gist 생략, `CostMismatch` 추가, 폴링 조건 빠짐, 확인창 금액 빠짐, innerHTML, presigned가 아닌 src, CostMismatch 뒤 바로 다시 보냄.
  - 유료 호출 0회: LLM boto3와 urlopen을 막고 기록하는 실행에서 boto3 0회, `check_vnew_casting`의 urlopen 0회다. urlopen은 `api.fal.ai/v1/models/pricing` 가격 조회 GET 61회뿐이다(`check_vnew_generate`·`check_vnew_shot_brief`, 무료). 그 두 검사가 막힌 실행에서 실패하는 것은 A·B2와 같다.
  - 바뀐 9개 파일 모두 CRLF 0이고, 공백만 바뀐 줄 0, 지운 함수 정의 0이다(B3 이전·B 이전 사본과 비교).
- **3단계 리뷰(B3 변경분 + 묶음 B 전체 재확인)**
  - 보안
    - `castingKey`는 서버 안 항목에만 있다. 화면 응답(`plan_view`·`gen_view`·상세)에는 나가지 않고, 컷 단위 fal 본문에도 들어가지 않는다(cast-k). 상세는 지금처럼 presigned URL만 준다.
    - 화면 글은 모두 `el()`(textContent)과 속성(`title`·`placeholder`·`value`·`name`)으로 넣는다. cast-l이 외모 문장·경고·오류에 `<img onerror>`를 넣어 글자로 남는지, 가짜 DOM에서 innerHTML이 0회인지 본다. 경로의 캐릭터 id는 `encodeURIComponent`.
    - 확인 금액은 서버가 다시 계산한다(`start_draw`). 화면 값은 확인창 표시용이다.
    - 거절 상세는 `error_gist`를 지난다(cast-g: 서명 쿼리·data URI·fal 키가 남지 않음).
    - 운영 문구 9종은 고정 문구만 연다. `CostMismatch`는 입력 원문이 섞여 계속 가린다.
    - 발견: 없음.
  - 성능(512Mi)
    - `_char_entries`는 이미 읽은 char.json에서 키를 찾는다(S3 호출 추가 없음, 후보 ≤ 14개 복사). 컷 단위 payload는 바뀌지 않아 이미지 수·바이트·메모리가 같다.
    - 화면은 뽑는 중 5초마다 상세 GET 1회다(뽑기 최대 900초 → 최대 약 180회). 상세 1회당 presign은 사진 수(≤ 14 + 1)만큼이고, 서명은 로컬에서 계산한다.
    - 버튼 1번에 POST 1회, `CostMismatch`면 GET 1회가 더 든다. 다시 그리기는 상세 전체 렌더(기존과 같다).
    - 발견: 없음.
  - 운영성
    - 되돌리기는 `VNEW_CASTING=false` 한 줄이다. 끄면 라우트 404, 상세 `{enabled:false}`, 화면 칸·폴링 없음, `_char_entries`가 B 이전과 같다(cast-j·cast-k·cast-l). 저장된 `char.casting`은 남는다.
    - 진행 중 작업: 컷 단위·웹툰 통 테이크 생성은 `castingKey`를 읽지 않아 배포·되돌리기와 상관없다. 뽑는 중 설정을 끄면 뽑기는 끝까지 가지만 화면에서는 다시 켤 때까지 보이지 않는다(R0.3·R0.5).
    - 재시작: 상세를 보는 동안 5초 폴링이 `reconcile`을 부르므로 600초 뒤 이어받는다.
    - 로그: 새 로그 `vnew.gen.casting_key_unreadable char= err=`(예외 종류만). 칸 실패 로그 `vnew.casting.slot_failed kind=HTTP422`는 그대로다.
    - 발견·조치: 뽑는 중 5초 다시 그리기가 쓰던 외모 문장을 지운다 → 입력을 막았다(D7). 자동 문장이 [저장]으로 "고친 문장"이 되는 문제 → 바뀌었을 때만 보낸다(D8). 이 항목을 쓰는 중 `WORKLOG.md` 전체가 CRLF로 바뀌었다(편집 도구) → LF로 되돌리고 9개 파일을 다시 쟀다(CRLF 0).
- **남은 이슈**
  1. `_real_front_key`는 아직 부르는 곳이 없다. 묶음 C(`_take_payload(variant="real")`·`start_real` 인물 확인)에서 쓴다.
  2. 운영에서 `CostMismatch` 문구는 여전히 "처리 중 오류가 발생했습니다 (코드: CostMismatch)"다. 캐스팅 화면은 `type`으로 새 금액을 다시 읽어 안내하므로 문제없다. ⑤ 생성·vpoc 확인창의 같은 코드는 이전과 같다.
  3. 뽑는 중·캐릭터를 만드는 중에는 캐스팅 입력을 막는다(D7). 제품 판단이 필요하면 알려 주세요.
  4. 거절 요지는 fal 원문(영어)이라 한국어 문구 뒤에 영어가 붙는다(예: "(fal: Image has sensitive content.)").
  5. 091 남은 이슈 1(참고 구현 원문 없음)·4(제출 직후 파드가 죽으면 1건 과금 가능, 재시작 뒤 최대 10분 "만드는 중")는 그대로다.
  6. VB-1(남·여 캐스팅 2장씩, $0.16)·VB-2(뽑는 중 재시작)는 개발 배포 뒤 설계자 승인으로 한다.

### vnew-live-fx 묶음 C — C1·C2 소리 거절·기존 결함, 실사 회차 시작·제출·병합 (Kiro, claude-opus-5.5)
- **범위**: tasks.md C1·C2만 했다. C3(화면 `vnew.js`·`vnew.css`, lr-n·lr-o, `docs/ANALYSIS.md`)는 하지 않았다. 커밋·push 하지 않았다. 사용자 데이터·참고 구현 폴더는 읽지 않았다.
- **바꾼 것**
  - `vpoc/fal.py`: `_REJECT_AUDIO_MSG`(design 원문), `_is_audio_rejection`(신규, audio+sensitive 두 낱말), `rejection`(likeness → audio → sensitive 순서), `poll`(거절 4xx·status 오류·결과 error 세 경로에서 소리 거절이면 `failureKind:"audio"`·안내 문구, 그 밖의 dict 는 그대로).
  - `vnew/generate.py`
    - C1: `_fail_variant`(previz-take 실사 실패가 gen 을 partial 로 바꾸지 않음, `kind` 인자 → `failureKind` 저장·지움), `_audio_kind`(신규), `_gen_stale`(`activeSince` 우선), `_do_poll_previz`(Failed → `kind`), `retry_segment`(`audio` 인자, previz-take 는 `realRun` 이 있을 때 실사, `failureKind`·error 비움, `activeSince`), `_submit_take`(`audio` 인자).
    - C2: `final_real_run_key`, `REAL_HISTORY_MAX`, `_require_take_real`, `real_estimate`, `_real_ready_error`, `_real_missing_names`, `_real_run_summary`, `_empty_real_state`, `start_real`, `_real_block_text`, `REAL_TAKE_STYLE`·`REAL_TAKE_RULE_REPLACE`·`REAL_TAKE_AVOID`, `_real_action`, `_with_real_expressions`, `_assert_no_webtoon`, `_take_prompt(variant)`, `_real_look`, `_real_appearance`, `_take_payload(variant)`, `_take_location_jpeg(variant)`, `_do_submit_take`(실사 경로·검사 단계 `check`·실사 draftId 는 칸에), `_do_concat`(회차 키·n), `select_variant`(previz-take 조건), `gen_view`(`realRun`·`realHistory`·`realEstimate`·`canStartReal`·`realBlock`·칸 `failureKind`·`audio`·`finalReal.n`).
  - `vnew/routes.py`: `POST /p/<pid>/generate/real`(신규), retry 본문 `audio`(bool 만).
  - `vnew/settings.py`: `VNEW_TAKE_REAL`(기본 true). `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`: `VNEW_CASTING` 바로 뒤, 되돌리기 주석.
  - `vpoc/errors.py`: `USER_FACING_KINDS` 에 `TakeRealDisabled`·`RealNotReady`·`RealRunning`·`RealPromptWebtoon`.
  - `tools/check_vpoc.py`: `_LR_TAKE_SHA_ON`(lr-e 기준값, 코드 변경 전에 잼), `check_vnew_take_real`(lr-a~m)과 하네스 `_LrEnv`·`_LrStore`·`_LrSem`·`_lr_char`, 상수 `_LR_*`, `PARITY_ENV_KEYS` 에 `VNEW_TAKE_REAL`, `main()` 끝에서 호출.
- **결정**
  - D1 — 소리 거절은 원문에 audio 와 sensitive 가 둘 다 있을 때만 본다(`_is_audio_rejection`). likeness 판정이 먼저다. 로그는 거절 4xx 경로 `vpoc.fal.rejected request= kind=audio http=422`, status·결과 error 경로 `http=-`(원문 없음). 대안: 정확한 문구 일치(기각 — fal 문구가 조금만 바뀌어도 버튼이 사라진다).
  - D2 — `retry_segment` 의 `activeSince` 는 옛 순차 모드 다시 시도에도 둔다(design C.10 은 모드를 가리지 않는다. 30분 지난 옛 gen 도 같은 결함이 있다). previz-take 실사 다시 시도는 `finalReal` 을 `waiting` + 지금 회차 `n` 으로 둔다.
  - D3 — **실사 외형(design 과 다름, 확인 필요)**: design 은 `casting.lookEn`(없으면 `look_sentence`)을 `_short_appearance(…, 170)` 하라고 했지만, `look_sentence` 는 옷까지 쉼표로 이은 한 문장이라 `_short_appearance`(문장 단위로 옷 낱말 문장을 버림)를 지나면 늘 빈 값이 된다. `_real_appearance` 를 두어 쉼표 조각마다 `_short_appearance` 의 옷 판정을 빌려 옷 조각만 빼고, `strip_drawing_words` 뒤 단어 경계 170자로 자른다. 예: "Korean man in his late 20s, short black anime-style hair, sharp eyes, wearing a black dress shirt" → "Korean man in his late 20s, short black style hair, sharp eyes". 문장은 `characters.load_char` 로 다시 읽어 `casting.current_look`(캐스팅 켬)·`auto_look`(끔)으로 만든다(실사 제출 1회에 인물당 char.json 1회 더 읽음). 대안: design 그대로(기각 — 외형이 늘 비어 사진만 남는다), `_char_entries` 에 문장 추가(기각 — B 함수·결정 4 범위를 넓힌다).
  - D4 — `start_real` 의 인물 확인은 charId 가 있는 배우만 본다. charId 없는 엑스트라는 웹툰 테이크도 사진 없이 보내므로 막지 않는다. 사진 없는 인물의 ASSETS 문장에는 "(photo)"·COSTUME 대체 문장을 넣지 않는다.
  - D5 — 실사 회차(`realRun`)가 없는데 실사 칸이 선점되면(옛 데이터·수동 수정) 제출하지 않고 "실사 회차가 없어 제출하지 않았습니다."로 실패시킨다. design("있을 때만 진행")대로 그냥 돌아가면 칸이 preparing 으로 남아 300초마다 다시 선점된다.
  - D6 — 실사 `draft_id` 는 칸(`unit.real.draftId`)에 둔다. 웹툰 테이크의 `unit.draftId` 를 덮어쓰지 않는다(구현 중 발견).
  - D7 — 실사 프롬프트: FX 조각이 없으므로 `fxShort` 단계를 넣지 않는다. 줄이기 단계를 더할지는 실사 표현(배경 기호만이면 빈 값)·분위기로 판단한다. 최소형에서 연기를 남기는 후보는 실사 표현을 함께 남긴다. 그림 낱말 제거는 외형·의상·목소리·연기·분위기·HARMONY 에 한다(목소리는 design 목록 밖이지만 같은 이유로 더했다). 웹툰 경로는 호출·로그·글자가 지금과 같다(lr-e sha256 4종).
  - D8 — 로그: 웹툰 로그 형식은 그대로 두고 실사만 꼬리표를 붙인다. `vnew.gen.take_prompt variant=real bytes= steps=`, `vnew.gen.take_submit … ms= variant=real`, `vnew.gen.take_submit_failed … stage=check err=… variant=real`, `vnew.gen.real_start pid= n= takes= usd= audio=on|off`. 실사 `final_copy`·`final_concat` 은 기존 `variant=real` 로그를 쓴다.
  - D9 — 라우트는 `VNEW_TAKE_REAL` 을 프로젝트를 보기 전에 본다(fx·캐스팅과 같다). `confirmUsd` 는 숫자·숫자 문자열만 받고 bool·None·NaN·무한은 400 `NoConfirmUsd`(기존 ⑤ 생성 라우트는 `float("abc")` 가 500 이다 — 바꾸지 않았다). 금액은 원장 기록용이고 생성을 막지 않는다(결정, 기존과 같다).
  - D10 — `USER_FACING_KINDS` 에 4종을 더했다. 던지는 곳이 모두 고정 문구다(lr-a 가 ast 로 확인). `CharacterRealMissing`·`FalKeyMissing`·`NoConfirmUsd` 는 이미 있다. `CostMismatch` 는 B 와 같은 이유로 넣지 않았다(화면이 보낸 금액이 문구에 들어간다).
  - D11 — `realHistory` 상태: finalReal done → done, 취소된 칸 → cancelled, 병합 실패·모든 칸 실패 → failed, 그 밖 → partial. `finalKey` 는 done 일 때만. 오래된 것부터 버린다(최대 3, S3 파일은 지우지 않는다).
  - D12 — `gen_view.realBlock` 은 시작 때와 같은 순서로 이유를 고른다(설정 끔 → 회차 상태 → fal 키 → 인물 사진). 인물 확인은 take gen 이 끝났고 진행 중이 아닐 때만 캐릭터를 읽는다(GET /plan 1회에 char.json 인물 수만큼 더, `plan_view` 가 이미 같은 수를 읽는다). 확인이 예외를 내면 "" + 경고 로그(보기는 깨지지 않고 시작 때 다시 본다).
- **기존 검사 고정·기대값**: 없음. 기존 검사 기대값을 바꾸지 않았고 새 끔 고정도 없다. pf-a~f·fx-i(sha256 5종)·tk-a~f·cast-a~m 이 그대로 통과한다.
- **확인**
  - `python -m py_compile`(fal·errors·generate·routes·settings·check_vpoc) 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 57줄(이전 56 + lr 1), FAIL·WARN 0. `[ok  ]` 줄에 lr-a~m 이 모두 있다. rw-a 는 템플릿 env 15개다(이전 14 + VNEW_TAKE_REAL).
  - lr-e: 웹툰 take 프롬프트 sha256 4종(6컷 기호없음 3,901B·보통 3,874B·최악 3,875B·12컷 최악 3,627B)을 `variant` 인자를 더하기 전에 재서 상수로 두었고, 기본값·`variant="webtoon"` 둘 다 같다.
  - 실사 프롬프트(lr-d, 6컷·12컷은 fx-h 와 같은 고정 입력): 1테이크 27초 3,203B 단계 없음 · 6컷 보통 3,959B · 6컷 최악 3,941B(둘 다 `…audioShort,moodDrop,bodyShort`) · 12컷 최악 3,569B(`…bodyShort,action55,minimalTimeline`) · 12컷 기호없음 3,928B(`…minimalTimeline,motionHarmonyShort`). 모두 "webtoon" 0·≤4,000B·모든 타임스탬프.
  - lr-c: 실사 본문 이미지·영상이 모두 `data:` 이고, 본문에서 data URI 를 지우면 http·`users/`·`rec/`·서명 URL 이 없다. 실사 @Video1 이 웹툰 테이크 본문의 것과 바이트까지 같다.
  - 변형 검사 28개(실제 파일을 한 군데씩 바꾸고 `check_vnew_take_real` 실행 → 원래 바이트로 되돌림, sha256 확인): 27개를 잡았다. 놓친 1개는 retry 라우트의 bool 거르기를 뺀 것인데, `retry_segment` 가 같은 거르기를 한 번 더 해서 동작이 같다(동등 변형).
  - 바뀐 줄 커버리지(`coverage run --source=app\src tools/check_vpoc.py`, coverage 7.17, C 이전 사본과 difflib): 실행 대상 298줄 중 298줄(100%). `fal.py` 14/14, `generate.py` 258/258, `routes.py` 25/25, `settings.py` 1/1, `errors.py` 0/0(집합 리터럴은 바뀌지 않은 첫 줄이 문장이다).
  - 실제 fal·Bedrock 호출 0회: lr 그룹은 fal 제출·조회·취소·urlopen·LLM 을 모두 가짜·막음으로 두고 urlopen 0·LLM 0 을 스스로 확인한다(제출 30건·조회 58건 모두 가짜). boto3 API·urlopen 을 막고 기록하는 전체 실행: 새 트리·C 이전 트리 모두 s3.GetObject 시도 394·가격 조회 GET 61·같은 2개 검사 실패(`check_vnew_generate`·`check_vnew_shot_brief`, A·B 때와 같음)이고 bedrock 시도는 0이다. lr 그룹만 막고 돌리면 0·0이다.
  - 공백만 바뀐 줄 0(`check_vpoc.py`·`generate.py` 포함 바뀐 .py 전부), 지운 함수·클래스 정의 0, 바뀐 9개 파일 CRLF 0.
- **3단계 리뷰**
  - 보안
    - 외부 전송: 실사 본문의 인물 사진·장소 뷰는 `_fit_ref`(1024px) 바이트를 `_submit_take` 가 data URI 로 바꾸고, 녹화는 `_video_data_uri` 다. presigned·S3 URL 을 fal 에 보내는 경로가 없다(lr-c). `realHistory[].finalUrl` 은 브라우저용 presigned 이고 모델로 가지 않는다.
    - 입력 검증: 라우트는 `_project_for(write=True)`(쓰기 권한·트랙). `confirmUsd` 는 유한한 숫자만, `audio` 는 bool 만 받는다. 금액은 서버가 다시 계산한다.
    - 프롬프트 주입: 새로 프롬프트에 들어가는 사용자 글은 캐스팅 외모 문장(저장 때 `clean_text` 300자, 줄바꿈 없음)이고, `strip_drawing_words`·쉼표 조각 처리는 줄을 만들지 않는다. 남은 "webtoon" 은 `_assert_no_webtoon` 이 제출 전에 막는다(칸 failed·제출 0·원장 0, lr-d).
    - 로그: 프롬프트·URL·키를 남기지 않는다(바이트·단계·회차 번호·금액·예외 종류만). 소리 거절 원문도 남기지 않는다.
    - 발견: 없음(문구 4종은 고정 문자열이라 운영에서도 보이게 했다, D10).
  - 성능(512Mi)
    - 실사 준비(녹화 받기·이어붙이기·참고 이미지·base64·본문)는 웹툰과 같은 `_PREPARE_SEM`(1) 안에서 한 칸씩 한다. 같은 순회의 실사+웹툰 제출 합은 `VNEW_GEN_PARALLEL`(2)을 넘지 않는다(lr-m: 대기 3칸 → 첫 순회 2건, 모든 제출이 세마포어를 잡은 채, 최대 동시 1). 실사 참고 이미지는 인물 수 + 장소 1장이라 웹툰(+패널 3장)보다 적다.
    - 외부 호출: 실사 회차 1번 = 테이크 수만큼 제출 + 5초마다 조회. 의상 읽기(LLM)는 웹툰 때 캐시라 0회다(lr-f). 실사 제출 1회에 인물당 char.json 1회, take gen 이 끝난 상태의 GET /plan 1회에 char.json 인물 수만큼 S3 읽기가 더 든다(D3·D12).
    - 발견: 없음. 실사 프롬프트 6컷 보통은 3,959B 로 분위기가 빠진다(moodDrop, 남은 이슈 4).
  - 운영성
    - 되돌리기: `config.<target>.env` 에 `VNEW_TAKE_REAL=false` 한 줄. 시작만 404 이고, 진행 중 회차는 끝까지 간다(lr-j 에서 끈 채 드라이버가 done 까지 감). 소리 거절 안내·소리 없이 다시·결함 수정은 끈 상태에서도 그대로다(lr-g 를 끈 채 실행).
    - 재시작: gen 이 running 이면 `GET /plan` 의 `ensure_driver` 가 드라이버를 살린다. preparing 300초 → queued → 다시 제출 1회, submitted 는 조회를 잇는다(lr-j). 취소는 실사 칸만 cancelled 이고 웹툰은 그대로이며, 다음 회차 기록에 cancelled 로 남는다.
    - 진행 중 작업 회귀: 배포 때 진행 중인 웹툰 take gen 의 실사 칸은 skipped 그대로다. `activeSince` 가 없는 옛 gen 은 createdAt 으로 본다. `gen_view` 는 키만 늘었다.
    - 발견·조치: 회차 없는 실사 칸의 무한 재선점(D5), 실사 draft_id 가 웹툰 draft_id 를 덮어씀(D6) → 고쳤다. 코드 이미지를 C 이전으로 되돌리면 진행 중 실사 칸은 옛 `_do_submit_take` 가 그냥 돌아가 preparing·queued 를 30분(GEN_STALL_S)까지 오간다 → 이미지 되돌리기 전에 진행 중 실사 회차를 취소한다(운영 계획, 남은 이슈 3).
- **남은 이슈**
  1. **`plan_view.features.takeReal` 을 더하지 않았다.** design §2 는 C1 이 더한다고 했지만 C 파일 목록(design §9·tasks C1·C3)에 `plan.py` 가 없고, 기존 fx-g 가 `features == {"fx": True}` 를 글자째 비교한다(B 도 `casting` 을 더하지 않았다). C3 화면은 (가) `gen.canStartReal`·`gen.realBlock`("실사 버전 기능이 꺼져 있습니다.")·`gen.realRun` 으로 판단하거나 (나) C3 파일 목록에 `plan.py` 를 넣고 켬일 때만 키를 두며 fx-g 를 부르는 줄에서 `VNEW_TAKE_REAL=False` 로 고정한다. Claude 결정이 필요하다.
  2. 실사 외형 처리(D3)가 design 문장과 다르다. 확인이 필요하다.
  3. 코드 이미지 되돌리기 때 진행 중 실사 회차는 먼저 취소해야 한다(운영성 리뷰).
  4. 실사 6컷 보통(3,959B)은 분위기가 빠지고 묘사 규칙이 짧은 형이 된다. 12컷은 최소형이다(웹툰과 같은 경향). 개발계 `vnew.gen.take_prompt variant=real … steps=` 로 본다.
  5. `strip_drawing_words` 가 낱말만 지워 "black dress shirt drawn in manga style" → "black dress shirt in style" 처럼 어색한 조각이 남을 수 있다(A1 함수, 범위 밖).
  6. 실사 사진 참고가 Seedance likeness 거절에 걸릴 수 있다(design §7). VC-1 에서 본다. 금액 예: 480p 15초 $3.08, 30초 $6.17(`VNEW_TAKE_TOKEN_UNIT_PRICE` 0.0214).
  7. 기존 ⑤ 생성 라우트의 `confirmUsd` 가 숫자가 아니면 500 이다(범위 밖, 바꾸지 않았다).
  8. C3(화면·lr-n·lr-o·ANALYSIS.md)는 다음 실행이다.

### vnew-live-fx 묶음 B 피드백 — 자동 외모 문장(`look_sentence`)이 실제 분석 문장 꼴에서 깨지는 5가지 (Kiro, claude-opus-5.5)
- **범위**: `vnew/casting.py`의 `look_sentence`와 보조 함수·정규식, `tools/check_vpoc.py` cast-b, 이 기록만 바꿨다. `base_text`·프리셋 문구(cast-a 원문 고정), 뽑기·이어하기·라우트·화면, 묶음 C 코드는 바꾸지 않았다. 사용자 데이터·참고 구현 폴더는 읽지 않았다. 커밋하지 않았다.
- **원인**: 기존 `look_sentence`는 외모와 기본 복장을 `", wearing "`으로 먼저 이은 뒤 한 덩어리로 다뤘다.
  1. 주어 찾기가 "He is a middle-aged man"의 man 앞 "He is"를 형용사로 넣었다("a He is Korean man").
  2. 글 속 나이는 "in his 20s" 꼴만 봤다("in his twenties"가 남아 나이가 두 번).
  3. 기본 복장의 "He wears …"·"while his shoes are not visible"을 그대로 붙였다.
  4. 눈 색 정규식은 "색 + eyes"가 바로 붙은 꼴만 봤다("light gray-blue almond-shaped eyes"·"with tiny near-black irises"가 남음).
  5. 300자를 문장 끝에서 단어 경계로 잘라 뒤쪽의 머리·옷이 반쪽으로 잘리거나 빠졌다.
- **바꾼 것**(`casting.py`, 지운 함수 없음)
  - `look_sentence` 본문: 외모(`_look_text`)·기본 복장(`_outfit_text`)을 따로 정리 → 나이 → 머리말(`_look_head`) → 성별 낱말·dyed → 300자가 넘으면 `_fit_look`.
  - 새 함수: `_strip_lead_verbs`·`_drop_pieces`·`_strip_eye_colors`·`_look_text`·`_outfit_text`·`_text_age`·`_look_head`·`_piece_rank`·`_look_pieces`·`_join_pieces`·`_clip_tail`·`_fit_look`.
  - 정규식: `_EYE_COLOR`를 `_COLOR`·`_TONE`·`_COLOR_WORD`·`_EYE_NOUN`·`_EYE_STOP`·`_EYES_OF_COLOR`·`_COLOR_EYED`·`_IRIS_PIECE`로 바꿨다. `_TEXT_AGE`에 낱말 나이(twenties …)를, `_PREFIX_AGE`에 "N-year-old"를 더했다. 새로 `_TEXT_AGE_WORDS`·`_HEAD_NOUN`·`_LEAD_SUBJECT`·`_LEAD_WEAR`·`_OUTFIT_VERB`·`_HAS`·`_NOT_SHOWN`·`_PIECE_SEP`·`_LOOK_SPLIT`·`_OUTFIT_SPLIT`·`_CLAUSE_HEAD`·`_ARTICLE_HEAD`·`_ADJ_TAIL`·`_PIECE_RANKS`·`_DANGLING`을 두었다.
  - `check_vpoc.py`: 모듈 상수 `_CAST_LOOK_FEEDBACK`(합성 5개: 남 3·여 1·모름 1)·`_CAST_LOOK_RULES`(규칙별 28경우), cast-b 조건 검사, `check_vnew_casting` 설명·통과 문구.
- **결정**
  - D1 — 외모와 기본 복장을 따로 정리한 뒤 잇는다. 대안: 이은 한 문장에서 처리(기각 — 복장 앞 동사와 외모 속 "wearing"을 구분할 수 없고, 조각 나누기에서 외모·옷 경계를 잃는다).
  - D2 — 문장 첫머리 "He is / She is / They are / He has / She has / They have / He's / This man is"를 뺀다. has·have는 with로 바꾼다(주어 뒤 "… in his twenties has fair skin" → "… with fair skin"). 외모 문장 속 "He wears …"는 "wearing …", 기본 복장 앞의 "He wears / She is wearing / They wear / is dressed in"은 지운다.
  - D3 — 나이: descriptionKo 나이가 있으면 글 속 나이 낱말("in his twenties"·"in her late 30s"·middle-aged·young adult·"25-year-old")을 모두 뺀다. **descriptionKo에 나이가 없으면** 글 속 "in his twenties"를 "in his 20s"로 바꿔 머리말에 한 번만 쓴다(10대는 D3 원칙대로 "early 20s"). 요구 2의 "나이 표현이 두 번 나오면 안 된다"를 나이 없는 캐릭터에도 지키려는 것이다. 피드백 요구 1의 예시 "a young adult Korean man in his 20s with …"는 요구 2(young adult도 뺀다)와 부딪혀 요구 2를 따랐다(결과 "a Korean man in his 20s with …").
  - D4 — 머리말 명사에 person·guy·lady·gentleman을 더했다(`_HEAD_NOUN`). "They are a slim person …"이 "a Korean person …, a slim person …"으로 겹치지 않는다. 성별 정하기(`_SUBJECT`·`auto_gender`)는 그대로다.
  - D5 — 눈 색: 눈 명사 앞 같은 명사구(쉼표·with·and·관사·소유격·-ing 낱말에서 멈춤, 최대 5낱말)에서 색 낱말과 그 앞 톤 낱말(light·pale·near …)만 지운다. "dark eyes"의 dark는 색이 뒤따를 때만 뺀다. "blue-eyed"·"eyes of deep blue"도 뺀다. irises·pupils는 with·and 조각이나 혼자 선 조각을 통째로 뺀다. 대안: 기존 정규식에 낱말 조합 추가(기각 — 색과 눈 사이에 끼는 모양 낱말 조합을 다 적을 수 없다).
  - D6 — 기본 복장의 꼬리: "not visible/shown/seen/pictured", "out of frame/view", "no visible", "cropped out"이 든 조각만 뺀다("while/but …" 앞에서도 나눈다). "no tie"는 옷 정보라 남긴다. 목록 끝 "and …" 조각이 빠지면 남은 마지막 항목에 and를 다시 붙인다.
  - D7 — 300자: 300자 이하이면 다시 잇지 않는다(기존 cast-b 기대값 13개가 글자까지 그대로). 넘으면 쉼표·세미콜론·", and"·관사 앞 " and "로 조각을 나누고 옷은 " with " 앞에서도 나눈다. 빼는 순서는 몸 세부 → 피부 → 옷 두 번째 이후 → 코·턱·눈썹(분류 안 된 조각도 여기) → 체격·키 → 안경·수염 → 얼굴형이고, 같은 순위는 뒤 조각부터 뺀다. 머리말·머리·눈 모양·옷 첫 항목은 남긴다. "facial hair"는 머리가 아니라 수염으로 본다. "a broad-shouldered, solid build" 같은 등위 형용사는 한 조각이다. 다시 이을 때 원래 목록에 and가 있었으면 남은 마지막 항목 앞에 and를 둔다.
  - D8 — 남기는 조각만으로도 넘치면 가장 긴 조각을 넘친 만큼(한 번에 최대 절반) 단어 경계에서 줄인다. 조각마다 앞부분이 남고 끝의 옷 첫 항목이 통째로 잘려 나가지 않는다. 그래도 넘치면 전체를 단어 경계에서 자른다. 끝에 매달린 with·without·or·and·of·관사·소유격·쉼표는 지운다. 대안: 문장 끝에서 자르기(기각 — 옷 첫 항목이 사라진다, 이번 피드백 #2·#3).
- **기존 검사**: cast-a~m 기대값을 하나도 바꾸지 않았다. 동작 변경 1건은 기존 기대값이 없는 경우다. "A man." + 복장은 전에 "a Korean man, wearing a suit"였고 지금은 "a Korean man wearing a suit"다(외모 나머지가 비면 쉼표를 넣지 않는다).
- **확인**
  - `python -m py_compile`(casting·check_vpoc) 통과. `python -X utf8 -m coverage run --source=app\src tools/check_vpoc.py` 전부 통과: ok 57줄, FAIL·WARN 0.
  - 합성 5개 결과 글자 수: #1 259 · #2 289 · #3 293 · 여 285 · 모름 296. 모두 He is·She is·They are·wearing He/She/They·" has " 0회, 나이 표현 1회, 머리 조각 통째, 눈 색·irises·pupils 0회, 옷 첫 항목 있음, 끝 매달린 낱말 없음. `auto_look`·`current_look`(보기 때 계산)도 같은 문장이다. 사용자가 고친 문장은 그대로다.
  - 새 cast-b를 원본 사본 `casting.py`로 돌리면 52줄이 실패한다(합성 5개 전부와 규칙 경우). 새 코드에서는 0줄이다.
  - 변형 검사 17개(메모리에서 `casting.py` 소스를 한 군데씩 바꿔 `check_vnew_casting` 실행, 파일은 그대로): 17개 모두 cast-b가 잡았다. 예: has → with 빼기, 주어+동사 빼기, 옷 동사 빼기, 보이지 않음 꼬리, 주어 뒤 나이 낱말, 글 속 나이, 눈동자 조각, 섞인 눈 색, 같은 순위 순서, 순위 무시, 옷 첫 항목도 뺌, 매달린 낱말, 마지막 and, 등위 형용사, facial hair, 남기는 조각 줄이기, 옛 단어 경계 자르기.
  - 바뀐 줄 커버리지(coverage 7.17, 원본 사본과 difflib): `casting.py` 실행 대상 219줄 중 219줄(100%). 파일 전체에서 안 탄 5줄은 이번 변경 밖(B 때와 같은 `_sleep`·`_spawn` 예외·`_poll_slot` `except _ClaimLost`)이다.
  - 실제 fal·Bedrock 호출 0회: urlopen·socket 연결·boto3 client를 막고 기록하는 실행에서 `check_vnew_casting`의 시도가 urlopen 0·socket 0·boto3 0이다. 바뀐 함수는 순수 함수다.
  - 바뀐 3개 파일 CR 0, 공백만 바뀐 줄 0, 지운 함수·클래스 정의 0(원본 사본과 비교).
- **3단계 리뷰**
  - 보안: 입력은 분석 결과 글이고 `clean_text`(제어 문자·줄바꿈 → 한 칸, 600·400자)를 그대로 지난다. 새 외부 호출·로그·저장이 없다. 정규식 역추적은 악성 입력으로 쟀다("with " 120번, ", a" 200번, 합성 색 25번 이은 낱말, 톤 낱말 110개, "-" 600자, "while not visible" 25번). 한 번 호출 최대 25ms다. fal에 가는 글은 지금처럼 cast_prompt ≤1500자 한 줄이다. 발견: 없음.
  - 성능(512Mi): 순수 문자열 처리다. 실제 꼴 #2 한 번에 약 1ms다(1000번 977ms). 조각 빼기 루프는 조각 수만큼 다시 잇는다(최악 200조각 25ms). 보기·실사 제출 때 캐릭터마다 한 번이고 메모리 증가는 없다. 발견: 없음.
  - 운영성: 설정 플래그는 없다. 고치지 않은 자동 문장은 저장되지 않고 보기 때 계산되므로 배포하면 기존 캐릭터도 바로 바뀐다. 되돌리기는 이미지 되돌리기다. 진행 중 뽑기는 시작 때 저장한 `casting.prompt`를 쓰므로 영향이 없다. 묶음 C 실사 테이크(`_real_appearance`)는 쉼표 조각을 그대로 받으므로 문장 모양이 같고 lr-a~m이 그대로 통과한다. 발견·조치: 남기는 조각만으로 넘칠 때 처음 구현(전체를 단어 경계에서 자름)은 옷 첫 항목을 잘라냈다 → 가장 긴 조각부터 줄이도록 고쳤다(D8).
- **남은 이슈**
  1. 머리색 "dyed" 판정은 그대로다. "chestnut-brown"·"ash-gray"(나이 30대)도 dyed가 붙는다(합성 여·모름). 짙은 갈색·검정·나이 든 회색만 자연색으로 본다.
  2. 외모 문장 속 옷("…, wearing round glasses")과 기본 복장이 함께 있으면 "wearing"이 두 번 나온다(전과 같다).
  3. "This man is tall with short hair" → "a Korean man, tall with short hair"처럼 주어 뒤 is 문장은 쉼표 조각으로 남는다(요구 범위 밖).
  4. 조각 분류는 낱말 목록으로 한다. 목록에 없는 조각(흉터·문신 외의 새 낱말)은 코·턱·눈썹 순위로 빠진다.
  5. 개발 데이터 3개 캐릭터에서 화면의 자동 문장이 바뀌었는지는 배포 뒤 읽기 전용으로 확인해야 한다(이번에는 사용자 데이터를 읽지 않았다).

### vnew-live-fx 묶음 C — C3 실사 버전 화면·나란히 보기·소리 없이 다시·문서·리뷰 (Kiro, claude-opus-5.5)
- **범위**: tasks.md C3만 했다. `plan.py`·fx 검사 기대값·`vpoc/fal.py`·캐스팅 서버 코드는 바꾸지 않았다. `generate.py`는 `gen_view`의 `realEnabled` 한 줄만 더했다. 커밋하지 않았다. 사용자 데이터·참고 구현 폴더는 읽지 않았다.
- **PM 결정(093 남은 이슈) 반영**
  - 이슈 1 → (가)를 고쳐서 썼다: `gen_view.realEnabled = bool(VNEW_TAKE_REAL)`. 화면은 previz-take이면서 `realEnabled` 또는 `realRun`이 있을 때 실사 열을 그린다. 시작 버튼은 `canStartReal`일 때만, 아니면 `realBlock` 문구. 끔 + 회차 없음은 지금 화면("건너뜀")이다.
  - 이슈 2 → 093 D3(실사 외형 = 옷 조각을 뺀 외모 조각)을 받아들였다. 코드는 그대로다.
  - 이슈 5(`strip_drawing_words` 어색한 조각)는 범위 밖이라 남은 이슈로 둔다.
- **바꾼 것**
  - `frontend/public/vnew.js`
    - 새 구역 "통 테이크 실사 버전"(`finalResults` 바로 뒤): 상수 `TAKE_REAL_LEAD`·`TAKE_REAL_DONE_TEXT`·`TAKE_REAL_HISTORY_STATUS`, 순수 함수 `takeRealShown`·`takeRealUsdText`·`takeRealSecondsText`·`takeRealStartLabel`·`takeRealConfirmText`·`takeRealCostChangedText`·`takeRealCounts`·`takeRealState`·`takeRealProgress`·`takeRealFailedText`·`takeRealHistoryLabel`·`takeStatusText`·`takeCompareSoundLabel`·`takeCompareSound`·`takeCompareFollow`·`takeComparePlay`·`takeComparePause`, DOM `takePickButton`·`takeRealColumn`·`takeRealHistory`·`openRealConfirm`·`takeCompare`.
    - 기존 함수: `finalResults`(take 분기: 실사 열·웹툰 [이걸로 고르기]·나란히 보기), `takeTimeline`·`takeRow`(선택 인자 `gen`, 실사 회차가 있으면 실사 칸 = `cutCell`), `cutCell`([소리 없이 다시 만들기]), `renderGenStep`(상태 띠 `takeStatusText`, `takeTimeline`에 gen).
  - `frontend/public/vnew.css`: `vnew-real-*`(lead·block·error·start·progress·progress-fill·history·history-item·history-label·confirm-text·audio), `vnew-take-compare*`(전체 줄·row·item·tools, 좁은 화면 1열).
  - `app/src/vnew/generate.py`: `gen_view` 반환에 `"realEnabled": bool(vnew_settings.VNEW_TAKE_REAL)` 한 줄.
  - `tools/check_vpoc.py`: `check_vnew_take_real_screen`(lr-n·lr-o, `main()` 끝), 상수 `_LR_SCREEN_SLICES`·`_LR_SCREEN_BLOCK`·`_LR_SCREEN_CSS`·`_LR_SCREEN_WIRING`·`_LR_TAKE_DONE_TEXT`·`_LR_SKIP_NOTE`·`_LR_SCREEN_NODE_HARNESS`·`_LR_SCREEN_NODE_CASES`·`_LR_SCREEN_NODE_SERVER_CASES`, 도우미 `_lr_screen_code`·`_lr_run_node`·`_lr_screen_problems`. 기존 검사 본문은 바꾸지 않았다.
  - 문서: `docs/ANALYSIS.md`(§3.6.1 머리·「C 구현 결과」·실사 버전 화면·"문제" 목록 상태·사용자가 써 보는 순서, §4.4 vnew 라우트 50개, §7.1 `VNEW_TAKE_REAL`, §10.1 58개·실사 버전 행), 이 기록, tasks.md C3 체크.
- **결정**
  - D1 — 웹툰 열 [이걸로 고르기]는 설정과 상관없이 보인다(previz-take에서 웹툰 final이 done이고 gen이 진행 중이 아닐 때). ANALYSIS §3.6.1 문제 4(take gen에서 사운드로 갈 버튼이 없다)를 고치는 것이라 R0.3 예외(결함 수정)와 같이 다뤘다. 끔 + 회차 없음에서 바뀌는 것은 이 버튼 하나이고 실사 열·테이크 행·상태 띠는 지금 그대로다. 대안: 끔이면 버튼도 숨김(기각 — take gen이 ⑥으로 갈 길이 계속 없다).
  - D2 — [이걸로 고르기]는 `POST /select` 응답의 plan으로 ⑥을 바로 그린다(`renderPlan`). `loadPlan`을 거치면 `decidePlanStep`이 partial gen을 ⑤로 되돌려, 실사가 실패한 gen에서 웹툰을 고르고도 ⑤에 남는다(RC5.1). 기존 컷 단위 [이 영상으로 사운드 편집]은 그대로 둔다.
  - D3 — design에 없는 [실사 버전 다시 만들기 ($X)]를 done·failed·cancelled에서 `canStartReal`일 때 둔다. VC-2(같은 gen 실사 다시 만들기)를 화면에서 하려면 필요하다. 이때 확인창에 "지금 실사 결과는 '이전 실사 보기'로 남습니다."를 더한다. 금액·문구 원문은 design 그대로다.
  - D4 — 409 `CostMismatch`: 확인창 안에서 `GET /plan`으로 새 금액을 읽어 확인창 글을 바꾸고 "금액이 바뀌었습니다. 새 금액은 $X.XX입니다. 다시 누르면 새 금액으로 확인합니다."를 보인다. 보내지 않고, 사용자가 [만들기 시작]을 다시 눌러야 새 금액으로 보낸다(캐스팅 D6과 같은 방식). 다시 읽은 gen이 시작할 수 없으면(설정 끔·진행 중) 창을 닫고 화면을 다시 그려 `realBlock`이 보이게 한다. 응답이 오기 전에 다른 프로젝트로 옮겼으면 창만 닫는다.
  - D5 — 실사 열 상태(`takeRealState`): finalReal done → done, finalReal failed → failed, gen running이고 진행할 칸이 있거나 모든 칸이 끝났으면(병합 대기) → running, 취소 칸만 있으면 cancelled, 그 밖 failed. 리뷰 중 고침: 실패 칸이 0인데 끝난 회차(30분 넘게 진행이 없어 보기에서 partial로 본 경우)는 "실사 테이크 0개를 만들지 못했습니다"가 되어 "실사 버전 만들기가 멈췄습니다. 다시 만들어 주세요."로 바꿨다.
  - D6 — 나란히 보기 동기화는 `seeked`만 따른다. 한쪽이 옮겨지면 다른 쪽을 같은 시간으로 두고, 맞추려고 옮긴 쪽의 `seeked`는 한 번 건너뛴다. 0.05초 안이면 옮기지 않는다. 재생 중에는 맞추는 동안 원래 쪽이 앞으로 가 있어, 건너뛰지 않으면 되부름이 원래 쪽을 되감는다(변형 검사가 처음에 이 경우를 놓쳐 검사에 재생 중 어긋남을 넣었다). [같이 재생]은 둘 다 0초로 되감고 `play()`, 거절(자동 재생 정책)은 삼킨다. 대안: `timeupdate`마다 맞춤(기각 — 재생 중 계속 seek 해 끊긴다).
  - D7 — 영상 `preload`: 나란히 보기는 `metadata`, 이전 실사는 `none`(누를 때까지 받지 않음). 결과 열은 지금 값(지정 없음) 그대로다.
  - D8 — 상태 띠 새 문구는 실사 열을 그릴 때(`takeRealShown`)만 쓴다. 끔 + 회차 없음은 지금 문구다.
  - D9 — 테이크 행 실사 칸은 `takeRow` 안에서 회차가 있을 때 먼저 돌아가는 분기로 넣어 기존 "건너뜀" 블록의 들여쓰기를 바꾸지 않았다. `takeTimeline`·`takeRow`의 `gen`은 선택 인자라 없으면 지금과 같다.
  - D10 — [소리 없이 다시 만들기]는 칸이 failed이고 `failureKind === "audio"`일 때만 [다시] 옆에 둔다. 누르면 [다시]도 막아 두 번 제출되지 않게 한다. 웹툰·실사·설정 끔 모두 같다.
- **기존 검사 고정·기대값**: 없음. 기존 검사 기대값을 바꾸지 않았고 새 끔 고정도 없다. cast-a~m·fx-a~n·lr-a~m이 그대로 통과한다.
- **확인**
  - `node --check frontend/public/vnew.js` rc 0, `python -m py_compile`(generate·check_vpoc) 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 58줄(이전 57 + lr 화면 1), FAIL·WARN 0.
  - lr-n: 문구 함수 10종 결과와 DOM(회차 없음·시작 못 함·끔·진행 2종·done·이전 실사·실패·취소·병합 실패·영상 주소 없음·진행 중 done, 확인창 9경우, 나란히 보기 동작 6단계, 고르기 3경우, 테이크 행 7경우, 폴링 5경우)을 node 가짜 DOM에서 실행해 textContent·클래스·요청 본문을 design 문구와 비교한다. `<img onerror>` 문구가 글자 그대로 남고 innerHTML 0회, 영상 src 4종이 모두 응답 presigned URL이다.
  - lr-o: `_LrEnv`(가짜 fal·S3·원장)에서 실제 `gen_view` 4종(끔·끔 소리 거절·켬 27초·회차 중 끔)을 만들어 같은 화면 함수에 넣고, 화면이 만든 요청 2건(`/generate/retry {audio:false}`, `/generate/real {confirmUsd, audio:true}`)을 Flask 라우트에 그대로 보냈다. 끔 → 시작 404 `TakeRealDisabled`·`realEnabled` false·회차 없음·화면은 지금 그대로, 소리 거절 다시 만들기 본문 `generate_audio:false`·done, take 웹툰 고르기 200·`_gen_stale` activeSince·`_fail_variant` running 유지, 켬 → 화면 금액($5.55)으로 202, 회차 중 끔 → done까지 가고 실사 결과·나란히 보기가 보인다. urlopen 0·Bedrock 0.
  - 변형 검사 26개(실제 파일을 한 군데씩 바꾸고 `check_vnew_take_real_screen` 실행 → 원래 바이트로 되돌림, sha256 확인): 26개 모두 잡았다. 예: 확인창 금액 빠짐, 소리 기본 끔, audio 안 보냄, CostMismatch 뒤 바로 보냄·다시 읽기 없음, realEnabled·realRun 무시, 되부름 막기 없음, 되감기 없음, play 거절 안 삼킴, 소리 둘 다, innerHTML, presigned가 아닌 src, 소리 없이 audio·조건 빠짐, takeRow에 gen 안 넘김, 상태 띠, 웹툰 고르기 없음, 고르기 뒤 loadPlan, 진행 중 고르기, realBlock 숨김, 이전 실사 순서, 진행 문구, 나란히 보기 조건, CSS, `realEnabled` 상수.
  - 바뀐 줄 커버리지(C1~C3, `coverage run --data-file=%TEMP%\kiro_c3\cov.data --source=app\src tools/check_vpoc.py`, coverage 7.17, C 이전 사본 `C:\dev\ai\_bak_vnew_c12`와 difflib): 실행 대상 298줄 중 298줄(100%). `fal.py` 14/14, `generate.py` 258/258, `routes.py` 25/25, `settings.py` 1/1, `errors.py` 0/0. C3의 `realEnabled` 줄은 `return {…}` 문장의 일부라 coverage가 따로 세지 않는다(그 문장은 실행됨, lr-o가 켬·끔 값을 본다). JS는 coverage.py 밖이고 lr-n·lr-o가 새 함수 전부를 node로 실행한다.
  - 위생(C3 이전 사본 대비): `check_vpoc.py`·`vnew.js`·`vnew.css`·`generate.py`·문서 공백만 바뀐 줄 0, 지운 Python·JS 정의 0, CRLF 0. `check_vpoc.py`는 C 이전 사본 대비도 공백만 0·지운 정의 0. 저장소 안 `.coverage` 부산물 없음(데이터는 `%TEMP%\kiro_c3`).
- **3단계 리뷰**
  - 보안
    - 화면 글은 모두 `el()`(textContent)로 넣고 속성은 고정 문구(`title`)뿐이다. 서버 문구(`realBlock`·실패 문구·오류)는 글자로만 들어간다(lr-n `<img onerror>`). 영상 `src`·내려받기 `href`는 응답의 presigned URL만 쓴다. 경로의 프로젝트 id는 `encodeURIComponent`.
    - 요청 본문은 `confirmUsd`(서버 `realEstimate.usd`)·`audio`(bool)·`segId`·`variant`뿐이다. 서버가 숫자·bool만 받고 금액을 다시 계산한다(C2, lr-a). `realEnabled`는 설정 bool이라 새로 드러나는 값이 없다.
    - 발견: 없음.
  - 성능(512Mi, 두 영상 동시 재생)
    - 서버: 상수 한 줄이다. 새 S3·LLM·fal 호출이 없다. 실사 회차 중 `GET /plan`은 `_real_block_text`가 진행 중 판정에서 먼저 돌아가 캐릭터를 읽지 않는다.
    - 영상은 브라우저가 S3 presigned URL에서 바로 받는다. 앱 파드를 지나지 않으므로 두 영상을 같이 재생해도 512Mi와 상관없다. 브라우저 쪽은 결과 열 2개 + 나란히 보기 2개(`metadata`) + 이전 실사 ≤3개(`none`)다. 동기화는 `seeked` 때만 일해 프레임마다 도는 일이 없다.
    - 발견(조치 안 함, 남은 이슈 2): 실사 회차 중에는 기존 gen 폴링(3·5초)이 ⑤ 화면 전체를 다시 그린다. 그때 웹툰 열 영상도 새 presigned URL로 다시 만들어져, 회차 중 웹툰을 보던 재생이 끊기고 폴링마다 영상 앞부분 요청이 한 번 더 간다. 컷 단위 진행 중 화면과 같은 기존 방식이다.
  - 운영성
    - 재시작: 화면 상태는 모두 `GET /plan`에서 온다. 진행 중 폴링이 `ensure_driver`를 부르므로 파드가 재시작돼도 이어 간다(C2 lr-j). 화면은 진행 막대(완료/전체)로 그 상태를 보인다.
    - 되돌리기: `VNEW_TAKE_REAL=false` 한 줄이면 시작 404, `realEnabled` false, 회차 없는 gen은 C 이전 화면이다(lr-o). 진행 중 회차는 끝까지 가고 결과는 보인다. 프런트 이미지만 C3 이전으로 되돌려도 서버 필드가 늘었을 뿐이라 깨지지 않는다(실사 칸은 "건너뜀"으로 보이고, 두 final이 done이면 옛 [이 영상으로 사운드 편집]이 보인다). 서버만 C 이전으로 되돌리면 `realEnabled`·`realRun`이 없어 새 화면은 지금 화면 + 웹툰 [이걸로 고르기]가 되고, 옛 `select_variant`도 done gen에서 웹툰을 고른다.
    - 진행 중 작업: 배포 때 웹툰만 끝난 take gen은 회차 없음 → [실사 버전 만들기 ($X)]가 보인다. 캐시는 CI 태그(`ASSET_VERSION`)로 새 `vnew.js`를 받는다.
    - 로그: C3은 서버 로그를 더하지 않는다. 시작·제출·거절 로그는 C1·C2 그대로다(`vnew.gen.real_start`, `vpoc.fal.rejected … kind=audio`).
    - 발견·조치: 실패 칸 없이 멈춘 회차의 문구(D5), 고른 뒤 ⑤에 남는 문제(D2) → 고쳤다.
- **남은 이슈**
  1. ⑤ 통 테이크 안내 배너(`renderGenStep`·`renderGenStart`)와 생성 확인창·테이크 행 안내는 여전히 "실사는 건너뜁니다 — 기본 모델로 따로 만들 수 있습니다."다. C3 수정 범위(상태 띠 문구) 밖이라 두었다. 실사 버전과 어긋나므로 문구 결정이 필요하다(예: "실사 버전은 웹툰이 끝난 뒤 따로 만들 수 있습니다.").
  2. 실사 회차 중 폴링이 화면 전체를 다시 그려 웹툰 재생이 끊기고 영상 요청이 폴링마다 한 번 더 간다(성능 리뷰). 대안: 결과 칸이 그대로면 다시 그리지 않기, 또는 진행 중에는 결과 영상을 `preload="none"`.
  3. 실사가 실패한(partial) gen에서 웹툰을 고른 뒤 페이지를 새로 열면 `decidePlanStep`이 partial이라 ⑤를 연다(기존 규칙, 단계 막대로 ⑥에 갈 수 있다).
  4. `strip_drawing_words`의 어색한 조각(093 이슈 5), `_gen_previz_stale`의 previz-take 판정(문제 5)은 범위 밖이다.
  5. 확인창 "480p 초안"은 `realEstimate.resolution`과 "초안"을 쓴다. `VNEW_TAKE_DRAFT`를 끄면 "초안"이 맞지 않는다(웹툰 확인창과 같은 한계).
  6. 컷 단위(Kling) 칸도 `failureKind:"audio"`가 오면 [소리 없이 다시 만들기]가 보인다(스펙대로). Kling edit 본문에는 소리 필드가 없어 같은 설정으로 다시 만든다. 지금까지 실측된 적은 없다.
  7. presigned URL은 1시간이라 나란히 보기 화면을 1시간 넘게 열어 두면 영상이 열리지 않는다(기존과 같다, 새로 열면 된다).
  8. 배포 뒤 확인 VC-1(실사 1회 ≈ $3.08)·VC-2(다시 만들기)·VC-3(소리 거절 시)은 설계자 승인 뒤 개발계에서 한다. 코드 이미지를 C 이전으로 되돌릴 때는 진행 중인 실사 회차를 먼저 취소한다(093 운영성).

### vnew-live-fx 묶음 C 피드백(095) — C3 남은 이슈 1·2: 통 테이크 안내의 실사 문구, 실사 회차 중 폴링이 ⑤를 다시 그리는 문제 (Kiro, claude-opus-5.5)

- **범위**: `frontend/public/vnew.js`, `tools/check_vpoc.py`(lr-n·lr-o 보강), 이 기록만 바꿨다. 서버 코드·다른 검사 기대값·`vnew.css`·`ANALYSIS.md`는 그대로다. 사용자 데이터를 읽지 않았고 커밋하지 않았다. C3 남은 이슈 3~8은 그대로 둔다.
- **바꾼 것**(`vnew.js`, 지운 함수 없음)
  - 새 함수: `takeRealEnabledOf(gen, plan)`·`takePlanHintText(where, amount, enabled)`(상수 `TAKE_PLAN_SKIP_TEXT`·`TAKE_PLAN_LATER_TEXT`, 실사 버전 구역 `takeStatusText` 뒤), `genViewSignature(gen)`·`genPollUnchanged()`(`genBusyFast` 뒤).
  - 고친 함수: `openGenerateConfirm`·`renderGenStart`·`renderGenStep`(안내 한 줄씩 `takePlanHintText`로), `loadPlan(first, fromPoll)`(폴링이면 건너뛰기, 처음 열기·불러오기 실패 때 서명 비움), `schedulePlanPoll`(타이머가 `loadPlan(false, true)`), `renderPlan`(⑤를 그릴 때 서명을 둔다). `state.genViewSig` 필드를 더했다.
- **결정**
  - D1 — 문구 판단 값: `gen.realEnabled`(boolean) → `plan.features.takeReal`(boolean, design §2에 적힌 키지만 서버는 아직 `fx`만 보낸다) → 둘 다 없으면 모름. 견적(`estimate`)에는 설정 값이 없다. 지금 서버에서 값이 있는 곳은 gen이 있는 화면(결과 화면·[새로 영상 만들기] 확인창·stale gen 시작 화면)이다. gen이 없는 첫 시작 화면·첫 확인창은 모름이고, 이때는 지금 문구에서 "실사는 건너뜁니다"만 "실사는 웹툰이 끝난 뒤 따로 만듭니다"로 바꾼다(095 지시의 "끝내 없으면"). 대안: 모름을 끔(지금 문구)으로 — 기각(지시 첫 문단 괄호와 둘째 문단이 겹치는데, 값이 없을 때의 규칙은 둘째 문단이 더 구체적이다). 모름을 켬(설정 기본값 true)으로 — 기각(화면이 확인한 값이 아니다). 서버에 `features.takeReal` 더하기 — 기각(서버 수정 금지, 남은 이슈 1).
  - D2 — 끔 문구는 C3 글 그대로다. 세 곳이 서로 다르다: 확인창·결과 화면 배너에는 "— 기본 모델로 따로 만들 수 있습니다." 꼬리가 있고, 시작 화면 배너는 "실사는 건너뜁니다."로 끝난다. 모름 문구도 이 꼬리를 그대로 둔다(지시대로 그 부분만 바꿈). "실사는 건너뜁니다" 글은 상수 한 곳에만 둔다(lr-n이 1번인지 본다).
  - D3 — 서명 필드: `gen.status`, 칸별 `[webtoon.status, webtoon.failureKind, real.status, real.failureKind]`, `finalWebtoon`·`finalReal`의 `[status, key]`, `realRun`(있으면 `{n}`, 없으면 null), `realHistory` 길이, `selected`. 칸은 `units`, 비면 `segments`(옛 gen, `genBusyFast`와 같은 규칙)다. 서버가 final의 key를 보내지 않아 `videoUrl`에서 `?`·`#` 뒤를 뺀 경로(S3 객체 키)를 key로 쓴다. presigned 서명·`previzUrl`·`costUsd`·`attempt`·`promptEn`·`error`·`realRun.startedAt`은 넣지 않는다. `realRun`은 처음에 n 값만 넣었는데, 리뷰에서 "회차 있음(n 없음)"과 "회차 없음"이 같은 서명이 되는 것을 찾아 `{n}`으로 바꿨다.
  - D4 — gen이 없거나(none) stale이면 서명이 ""이고 지금처럼 늘 다시 그린다. 시작 화면의 촬영 지시서 진행 카드는 폴링으로 바뀌어야 한다.
  - D5 — 비교 기준은 직전 응답이 아니라 **마지막으로 그린 ⑤의 서명**(`state.genViewSig`)이다. `renderPlan`이 ⑤일 때만 두고(⑤가 아니면 ""), `loadPlan(true)`의 "불러오는 중…"과 불러오기 실패 화면에서 비운다. 대안: 직전 `state.plan`과 비교 — 기각(버튼 응답의 `payload.plan`이나 이번 건너뛰기처럼 그리지 않고 `state.plan`만 바뀌는 길이 있어 화면과 데이터가 어긋날 수 있다).
  - D6 — 건너뛰기는 폴링 타이머(`schedulePlanPoll` → `loadPlan(false, true)`)에서만 하고, `decidePlanStep` 뒤에도 ⑤이며 그린 ⑤ 서명이 있을 때만 한다. 버튼 뒤 `loadPlan(false)`와 처음 열기는 늘 다시 그린다. 건너뛸 때도 `state.plan`은 새 값이고, `renderPlan` 대신 `schedulePlanPoll()`을 불러 폴링 간격(3·5초) 규칙이 그대로다.
- **기존 검사 고정·기대값**: 없음. lr-a~o·cast-a~m·fx-a~n 기대값을 바꾸지 않았다. lr-n 하네스의 가짜 `loadPlan(first)`는 첫 인자만 기록하므로 폴링 기대값 `["false"]`가 그대로다.
- **검사 보강**(`check_vpoc.py`: 새 상수·함수를 더하고, `check_vnew_take_real_screen`에는 호출·준비 줄만 끼워 넣었다)
  - lr-n: 배선 6곳(`_LR_095_WIRING`)과 "실사는 건너뜁니다" 상수 1곳을 본다. node 실행 ①: `takeRealEnabledOf` 7경우, `takePlanHintText` 켬·끔·모름 × 세 곳(글자째). 실제 `renderGenStep`·`renderGenStart`·`openGenerateConfirm`을 가짜 DOM에서 그려 시작 화면 5경우(gen 없음 모름·features 켬·끔·stale gen 켬·끔)와 결과 화면 2경우(realEnabled 켬, realEnabled 끔이 features 켬보다 먼저)의 배너·확인창 글. `genViewSignature`: URL 서명만 바뀐 두 plan은 같음, 필드 13개 각각 다름, failureKind 2쌍·realRun 있음/없음 다름, 넣지 않은 필드 3개는 같음, 시작 화면 4경우 "", 옛 segments. `genPollUnchanged` 6경우. node 실행 ②: 실제 `loadPlan`·`decidePlanStep`·`schedulePlanPoll`·`renderPlan`을 가짜 타이머·api로 돌린 10경우(처음 열기, URL만 바뀐 폴링 0번, 상태가 바뀐 폴링 1번, 버튼 뒤 1번, ④ 뒤 폴링, 같은 상태 폴링 2번 0번, 실패 뒤 폴링, 시작 화면 폴링, 회차 끝 폴링 멈춤, 다시 열기 뒤 늦은 응답)의 다시 그린 횟수·화면 노드·`state.plan`·폴링 타이머.
  - lr-o: 실제 GET /plan 3종(gen 없는 새 계획 → 모름, 끔 → 지금 문구, 켬 → 새 문구)을 같은 화면 함수에 넣어 배너·확인창 글을 본다. gen 없는 GET /plan에 `realEnabled`·`features.takeReal`이 없다는 전제도 본다. 조회마다 서명이 바뀌는 가짜 presign(`?X-Amz-Signature=sN`)으로 실제 `gen_view`를 웹툰 끝·실사 회차 중(POST /generate/real 202)·회차 끝(드라이버)에서 두 번씩 본다. 두 번 본 보기가 URL 서명만 다르다는 것을 확인한 뒤, 서명이 같고 단계가 바뀌면 다르다는 것을 본다.
- **확인**
  - `node --check frontend/public/vnew.js` rc 0. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 58줄(C3와 같은 수 — 보강은 lr 화면 묶음 안), FAIL·WARN 0, lr-a~o·cast-a~m·fx-a~n 모두 ok.
  - 변형 검사 23개(`vnew.js` 사본을 `%TEMP%`에 두고 모듈의 `PUBLIC`만 바꿔 `check_vnew_take_real_screen` 실행, 저장소 파일은 그대로): 23개 모두 잡았다. 배선 바늘로 먼저 잡힌 6개(폴링 표시 무시·fromPoll 안 넘김·⑤ 아니어도 서명·확인창/시작 배너가 gen 안 봄·결과 배너 꼴)는 배선 줄을 빼고 다시 세어 동작 검사만으로도 6개 모두 잡혔다. 예: 서명에 전체 URL, key·failureKind·realRun·realHistory·selected 빠짐, realRun 있음·없음 같게, 시작 화면도 서명, 실패·처음 열기 때 서명 안 비움, 건너뛸 때 폴링 안 다시 걸기, 단계 무시, features 무시, boolean 아닌 값, 모름 → 지금 문구, 켬 문구, segments 무시.
  - 커버리지(coverage 7.17, 데이터 `%TEMP%\c3fix_bak\cov95.data`, 시작 전 사본과 difflib): `check_vpoc.py` 새 줄 중 실행 대상 131줄 중 114줄(87.0%). 안 탄 17줄은 모두 실패 때만 타는 `problems.append`·`bad.append`·`return`이다. JS는 coverage.py 밖이고, 새·바뀐 JS 함수 7개와 고친 화면 함수 3개를 lr-n·lr-o가 node로 실행한다.
  - 위생(시작 전 사본 대비): 세 파일 공백만 바뀐 줄 0, 지운 JS·Python 정의 0, CR 0. 저장소 안 `.coverage`·새 `__pycache__` 없음(검사는 바이트코드를 쓰지 않게 돌렸고 데이터는 `%TEMP%`).
- **3단계 리뷰**
  - 보안: 새 글은 상수와 숫자(테이크 수·초)뿐이고 `el()`(textContent)로 넣는다. 서명은 비교에만 쓰고 DOM·요청에 넣지 않는다. 새 요청·저장·로그가 없다. 발견: 없음.
  - 성능: 서버 변경이 없고 `GET /plan` 횟수·간격이 그대로다. 서명은 칸 수만큼의 짧은 배열을 `JSON.stringify` 한 번(그릴 때 한 번 더) 한다. 같은 상태 폴링에서는 ⑤ DOM과 `<video>`를 다시 만들지 않는다. 그래서 회차 중 웹툰 재생이 끊기지 않고, 폴링마다 가던 영상 앞부분 요청이 없어진다(C3 성능 리뷰의 발견). presigned URL(1시간): 폴링 중에는 gen이 30분(`GEN_STALL_S`) 안에 상태가 바뀌어 다시 그리므로 화면의 URL이 1시간을 넘기지 않는다. 폴링이 멈춘 뒤는 C3 남은 이슈 7과 같다. 발견: 없음.
  - 운영성: 설정 플래그는 없다. 되돌리기는 프런트 이미지 되돌리기다(서버 변경 없음). 진행 중 회차·재시작: 서버 상태는 그대로이고, 화면을 새로 열면(`loadPlan(true)`) 서명을 비우고 다시 그린다. 바뀐 동작: ⑤ 오류 줄의 버튼 실패 문구가 다음 폴링에 지워지지 않고 상태가 바뀔 때까지 남는다. 발견·조치: `realRun` 있음/없음이 같은 서명이 될 수 있었다 → `{n}`으로 고쳤다(D3). 코멘트에 "실사는 건너뜁니다"를 그대로 적어 상수 1곳 검사가 2번을 셌다 → 코멘트를 상수 이름으로 바꿨다.
- **남은 이슈**
  1. gen이 없는 첫 시작 화면·첫 확인창은 설정 값을 모른다(모름 문구). `plan_view.features`에 `takeReal`(design §2)을 더하면 화면은 이미 그 값을 쓴다(서버 한 줄, 이번 범위 밖).
  2. 서명에 없는 값은 상태가 바뀔 때까지 화면에 늦게 보인다: `realEnabled`(폴링 중 설정을 바꿔 다시 배포한 경우), 같은 status 안에서 바뀐 `error`·`promptEn`, `canStartReal`·`realBlock`. 지금 흐름에서는 status가 바뀔 때 함께 바뀐다.
  3. 그린 뒤 버튼이 붙잡은 값(`openRealConfirm`의 `realEstimate`, 칸 `unit.id`)은 그릴 때 값이다. `state.plan`을 읽는 버튼(`openGenerateConfirm` 등)은 새 값을 쓴다. 금액이 달라지면 기존 409 `CostMismatch` 흐름이 새 금액을 읽는다.
  4. 지시의 "테이크 행 안내"는 줄 번호(6112)와 문구로 보아 ⑤ 결과 화면 배너로 읽었다. 실제 테이크 행(`takeRow`) 실사 칸의 "건너뜀" + "통 테이크(베타)는 웹툰만 만듭니다 — 실사는 기본 모델로 따로 만들 수 있습니다."는 그대로다. 실사 버전이 켜져 있고 회차가 없을 때 이 글이 보인다.
  5. `ANALYSIS.md` §3.6 「실사 버전 화면」에 이번 폴링 규칙·안내 문구를 적지 않았다(수정 범위 밖). 다음 문서 갱신 때 더한다.
