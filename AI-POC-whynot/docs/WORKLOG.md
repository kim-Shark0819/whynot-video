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


### vnew-live-fx 묶음 C 피드백 — 실사 외모 문장 2건: 캐스팅 "with" 빠짐, 실사 테이크 외형 중간 잘림 (Kiro, claude-opus-5.5)

- **범위**: `app/src/vnew/casting.py`(조각 잇기·줄이기, 나이 꼴 정규식 2개), `app/src/vnew/generate.py`의 `_real_appearance`, `tools/check_vpoc.py`(cast-b·lr-d), 이 기록만 바꿨다. 웹툰 경로(`_short_appearance`·웹툰 take 프롬프트)·프리셋 문구·화면은 그대로다. 사용자 데이터를 읽지 않았고 git·네트워크·유료 API를 쓰지 않았다. 커밋하지 않았다.
- **원인**
  1. 캐스팅(재현 1): `_strip_lead_verbs`가 "… in his thirties has pale …"의 has를 with로 바꾼다. 이 with는 `_fit_look`에서 첫 조각의 앞 이음(`look_lead`)이 된다. 그런데 "appearing"이 나이(`_TEXT_AGE`)와 함께 빠지지 않아 머리말 뒤가 " appearing with pale warm-toned skin"이 되었다. 그래서 앞 이음이 빈칸(" ")이 되고 with는 첫 조각 안에 들어갔다. 300자 줄이기에서 첫 조각(피부 순위)이 빠지자 with와 appearing이 함께 사라지고, 다음 명사구가 빈칸으로 머리말에 붙었다("a Korean man in his 30s an angular …").
  2. 실사 외형(재현 2): `_real_appearance`가 쉼표 조각 중 옷 조각만 빼고 `generate._clip_words`(단어 경계 + 마침표)로 170자를 잘랐다. 그래서 끝의 머리 조각이 "… over his."로 잘렸다. 같은 이유로 "hair tied …"(옷 낱말 tie를 품음) 머리 조각과 그 앞 머리말이 통째로 빠지기도 했다(합성 여 f1: 전 결과 "large doe eyes, …, without glasses").
- **바꾼 것**(지운 정의 없음)
  - `casting.py`
    - `_join_pieces`: 앞 이음이 빈칸 하나이고 첫 항목이 명사구이면 " with "로 잇는다. 명사구 조건은 원래 첫 조각이 아니거나(앞 조각이 빠짐) 관사·소유격으로 시작하는 항목이다.
    - 새 함수 `fit_appearance(text, limit, is_outfit=None)`·`_split_head`·`_is_hair_piece`(`_fit_look` 바로 뒤).
    - 정규식: `_TEXT_AGE`에 나이 앞 appearing·looking·seeming·"who appears/looks/seems"(+ "to be")를 더했다. `_PREFIX_AGE`는 "-looking" 꼬리까지 뺀다. 새로 `_HAIR_WORDS`·`_HAIR_PIECE`·`_NOUN_LEAD`·`_HEAD_AGE`를 두었다. `_PIECE_RANKS` 남기는 줄은 `_HAIR_WORDS`로 다시 썼다(같은 정규식).
  - `generate.py` `_real_appearance`: `vnew_casting.fit_appearance(strip_drawing_words(look), limit, is_outfit=<_short_appearance 옷 판정>)` 한 줄로 바꿨다.
  - `check_vpoc.py`
    - 상수: `_LOOK_FIX_*`(재현 1 입력·결과·옛 결과), `_CAST_LOOK_FEEDBACK["m4"]`, `_CAST_LOOK_RULES` 7경우, `_LR_APPEARANCE_REAL1`·`_LR_APPEARANCE_CASES`(7경우), `_LOOK_NO_WITH`·`_LR_OUTFIT_WORDS`·`_LR_APPEARANCE_HEAD`.
    - 도우미 `_lr_appearance_problems`, cast-b·lr-d 검사 블록, `check_vnew_casting`·`check_vnew_take_real` 설명과 통과 문구.
- **결정**
  - D1 — with 규칙은 `_join_pieces`에 둔다. 그래서 캐스팅(`_fit_look`)과 실사 외형(`fit_appearance`)이 같은 규칙을 쓴다. 원래 첫 조각이 남아 있고 관사로 시작하지 않으면 원문 이음을 그대로 둔다("a Korean man of average height with …"·"named Jun with …"는 명사구가 아니다). 대안: 나이 정규식만 고침(기각 — "A man named Jun with fair skin, …"처럼 빈칸 이음 뒤 첫 조각이 빠지는 다른 꼴이 그대로 깨진다, cast-b namedDrop·poseDrop). 대안: 머리말 뒤 첫 항목에 늘 with(기각 — 위 비명사구가 "with of average height"가 된다).
  - D2 — 나이 꼴: appearing·looking·seeming과 "who appears/looks/seems"(+ "to be")는 나이와 함께 지운다. 붙임표 뒤 looking("youthful-looking")은 지우지 않는다. 점검 중 "A young-looking man in his thirties" → "a -looking Korean man"(기존 `_PREFIX_AGE`가 young만 지움)을 찾아 "-looking" 꼬리까지 빼도록 고쳤다. 피드백의 "나이 꼴도 확인한다"에 들어가는 같은 종류라 범위 안으로 보았다. 영향은 나이가 있고 머리말 앞에 "{나이 낱말}-looking"이 있는 문장뿐이다.
  - D3 — 실사 외형 줄이기는 `casting.fit_appearance`(공개 함수)에 두고, 옷 판정은 generate가 넘긴다(`_short_appearance` 판정 그대로, casting이 generate를 가져오지 않음). 대안: `_fit_look`에 limit 인자(기각 — `_fit_look`은 옷 첫 항목을 남기고 눈을 머리와 같은 순위로 둔다. 실사 외형은 옷을 빼고 머리를 눈보다 오래 남겨야 한다. cast-b 기대값이 걸린 함수라 건드리지 않았다).
  - D4 — 옷 조각은 셋이다. "wearing/dressed …" 조각, 옷 판정이 참인 조각, 옷 조각 뒤에 오는 조각 중 코·턱·눈썹 이상 순위 낱말(코·체격·안경·얼굴·머리·눈)이 없는 조각이다. 마지막 규칙은 낱말 목록에 없는 "a white pocket square"·"black ankle socks"·"a shoulder bag"을 잡는다. 머리·눈 조각은 옷 판정으로 빼지 않는다("hair tied …"). and는 남은 외모 항목만으로 다시 센다(옷 목록의 ", and a tie"가 외모 마지막 항목에 and를 붙이지 않게).
  - D5 — 빼는 순서는 `_PIECE_RANKS` 순서(같은 순위는 뒤 조각부터) 다음에 눈 조각(뒤부터)이다. 머리말·머리 조각은 끝까지 남긴다. 그래도 넘치면 `_clip_tail`(단어 경계, 매달린 낱말 지움, 마침표 없음)로 자른다. 재현 2 첫 입력은 순위대로 안경 → 얼굴형이 빠졌다. 캐스팅 순위가 눈 모양을 얼굴형보다 오래 남기기 때문이다.
  - D6 — 늘 조각을 다시 잇는다. 문장 사이 마침표는 쉼표로 본다("Short black hair. He wears a suit." → 옷 문장이 조각으로 빠진다). 그림 낱말은 줄이기 전에 지운다(전에는 이은 뒤 지워 170자를 그림 낱말이 든 글로 셌다). 저장된 옛 문장("… 30s an angular …")도 실사 외형에서는 with가 들어간다.
- **기존 검사 고정·기대값**: 바꾸지 않았다. cast-a~m·fx-a~n·lr-a~o 기대값과 lr-e(웹툰 take sha256 4종)·fx-i(끔 sha256 5종)가 그대로 통과한다. cast-b의 기존 13·5·28경우 결과도 글자까지 같다.
- **새 결과**
  - 재현 1(241자): `a Korean man in his 30s with an angular clean-shaven face, narrow dark eyes beneath sharply slanted brows, and short charcoal hair swept back with loose strands over his forehead, without glasses, wearing an open-collar ivory button-up shirt`. 나이 꼴 "who appears to be"·"looking to be", descriptionKo 나이 있음·없음 6가지가 모두 같은 문장이다. 기본 복장의 "rolled cuffs," 뒤는 피드백에서 "…"로 생략돼 검사 상수에 임의 값(charcoal tailored trousers, black leather loafers)을 넣었다. 결과에는 첫 항목만 남아 이 값에 기대지 않는다.
  - 재현 2 입력 1(147자): `a Korean man in his 30s with narrow dark eyes beneath sharply slanted brows and short charcoal hair swept back with loose strands over his forehead`
  - 재현 2 입력 2(154자): `a Korean man in his 20s with tousled short dyed pale-blond hair with long parted bangs and flyaway strands, almond-shaped eyes, and a slender angular face`
- **확인**
  - `python -B -X utf8 tools/check_vpoc.py` 전부 통과: ok 58줄, FAIL·WARN 0.
  - 옛 코드(백업 `casting.py`·`_real_appearance`를 메모리에서 넣고 cast-b·lr 검사 실행): 43줄 실패. m4 2줄, 규칙 6경우, with 빠진 이음·appearing 13줄(나이 꼴 결과 줄 포함), fit_appearance 2경우, lr-d 실사 외형 5경우(19줄)와 통합 1이다. 새 코드는 0줄이다.
  - 변형 검사 14개(메모리에서 한 군데씩 바꿈, 파일은 그대로): 14개 모두 잡았다. 바꾼 곳은 with 규칙·appearing·관사 첫 조각·-looking·눈 먼저 빼기·옷 뒤 목록·머리 조각 옷 판정 제외·and 다시 세기·문장 마침표·머리말 없을 때 이음·머리말 나이·순위·generate의 옷 판정 넘기기·그림 낱말 지우기다.
  - 검사가 덮는 것
    - cast-b m4·규칙 7경우: `look_sentence` → `_TEXT_AGE`(appearing·who appears to be·looking to be)·`_PREFIX_AGE`(-looking)·`_fit_look` → `_join_pieces` with 분기(앞 조각이 빠짐).
    - fit_appearance 14경우: `_split_head`(머리말 있음·없음·나이), 옷 판정 3갈래, `_is_hair_piece`, 순위·눈 순서, `_NOUN_LEAD` 분기, `_clip_tail` 마지막 자르기, 빈 입력.
    - lr-d 7경우 + 통합 1: `generate._real_appearance` → `strip_drawing_words` → `fit_appearance` → `_short_appearance` 옷 판정. 통합은 가짜 저장소 캐릭터 → `_real_look` → `current_look`/`auto_look` → `look_sentence` → `_real_appearance`다.
  - 바뀐 줄 커버리지(`coverage run --data-file=%TEMP%\kiro_look_fix\cov.data --source=app/src,tools`, 시작 전 사본과 difflib): `casting.py` 실행 대상 41/41(100%), `generate.py` 1/1(100%), `check_vpoc.py` 62/68(91.2%). 안 탄 6줄은 실패 때만 타는 `bad.append`다.
  - 위생(시작 전 사본 대비): 네 파일 CR 0, 공백만 바뀐 줄 0, 지운 함수·클래스 정의 0. 저장소 안 `.coverage` 없음(데이터는 `%TEMP%\kiro_look_fix`).
- **3단계 리뷰**
  - 보안: 입력은 분석 결과 글이나 사용자가 고친 외모 문장이다. 새 저장·로그·외부 호출이 없다. fal에 가는 글은 지금처럼 cast_prompt ≤1500자와 실사 프롬프트 ≤4000B다. 정규식 역추적은 악성 입력으로 쟀다("appearing " 200번, "who appears to be " 100번, ", a" 200번, " and the nose" 150번, "wearing a coat, " 100번, 2000자 한 조각). `look_sentence` 최대 9.7ms, `_real_appearance` 최대 10.9ms다. 발견: 없음.
  - 성능(512Mi): 순수 문자열 처리다. 실제 꼴 입력 2로 `_real_appearance` 1000번에 223ms다. 실사 테이크 제출 때 인물마다 한 번이다. 외부 호출 횟수는 그대로다. 발견: 없음.
  - 운영성: 설정 플래그는 없다. 되돌리기는 이미지 되돌리기다. 고치지 않은 자동 문장은 보기 때 계산되어 배포하면 바로 바뀐다. 진행 중 캐스팅 뽑기는 시작 때 저장한 `casting.prompt`를 쓴다. 이미 제출된 실사 테이크는 영향이 없고, 배포 뒤 제출하거나 다시 시도하는 테이크부터 새 외형을 쓴다. 실사 외형 끝 마침표는 없어졌지만 ASSETS가 원래 `rstrip('.')`을 해 프롬프트 모양은 같다. 발견·조치: f1의 "hair tied …"가 옷 판정에 걸려 머리말·머리 조각이 통째로 빠지는 것을 찾았다 → 머리·눈 조각은 옷 판정에서 뺐다(D4). "young-looking"의 반쪽 낱말 → D2.
- **남은 이슈**
  1. 프롬프트가 예산을 넘을 때의 ASSETS 줄이기(`_take_prompt`의 `appearance_limit` 170 → 100, `_clip_words`)는 여전히 단어 경계 + 마침표로 자른다. 웹툰과 같이 쓰는 단계라 이번 범위(`_real_appearance`만) 밖이다. 이번 개발 점검의 실사 프롬프트는 3,888B로, 100자 단계까지 갔는지는 단계 로그(`steps=`)로 확인해야 한다.
  2. 머리말 + 머리 조각만으로 limit를 넘으면 `_clip_tail`이 머리 조각을 단어 경계에서 자른다. 매달린 낱말은 없지만 반쪽 조각이 남는다. 실제 꼴의 머리 조각은 90자 안쪽이다.
  3. 관사 없는 " and "("narrow eyes … and short black hair")는 나누지 않아 눈과 머리가 한 조각이다. 이 조각은 머리 조각으로 남고 눈만 따로 뺄 수 없다.
  4. 옷 판정은 낱말 목록이다. `_short_appearance` 판정은 부분 문자열이라 "a button nose"가 옷으로 빠진다. 옷 뒤 "a silver nose ring"은 코 낱말 때문에 남는다.
  5. 같은 꼴의 기존 문제: 나이가 있을 때 머리말 앞 "old-fashioned"는 old만 빠진다(`_PREFIX_AGE`, 실제 분석 문장에서 본 적 없음).
  6. 배포 뒤 개발 데이터에서 그 캐릭터의 자동 문장과 실사 테이크 ASSETS 외형을 읽기 전용으로 다시 확인해야 한다.

### char-fidelity 스펙 — 회전 시트·한쪽 소품 좌우·사진 그대로의 기본 복장 (Kiro, claude-opus-5.5, 스펙만)
- **배경**: 설계자가 개발계 캐릭터 "여의준"(`admin/char_20261030_a7b69d`) 결과에서 세 가지를 고쳐 달라고 했다(지시서 `.kiro/handoff/097-char-fidelity-spec.md`). Claude가 개발 데이터를 읽기 전용으로 본 원인은 다음과 같다.
  1. 시트 4번째가 3/4 후면이 아니라 후면이었다.
  2. 분석이 화면 왼쪽 손목의 시계를 "left wrist"로 썼고, 3D는 반대 손목에도 줄을 만들었다.
  3. 기본 복장 규칙("겹옷을 모두 뺀 기본 한 벌")과 덧입힘 추가 복장 때문에 사진 속 옷과 다른 조합이 나왔다.
- **이번 실행**: `.kiro/specs/char-fidelity/`(requirements·design·tasks), `docs/ANALYSIS.md` §3.6.2(캐릭터 메이커 지금 흐름과 변경 지점), 이 기록만 썼다. 앱 코드·검사 코드·매니페스트는 바꾸지 않았다. git·네트워크·유료 API를 쓰지 않았고 사용자 데이터를 읽지 않았다.
- **PM 결정(Claude, 사용자 답 반영)**: D0 설정 2개(`VNEW_CHAR_FIDELITY`·`VNEW_3D_SIDE_VIEWS`, 둘 다 끄면 바이트 동일), D1 기준 사진 한 장의 옷 그대로, D2 소품 좌우는 화면 기준으로 받고 코드가 계산·그림마다 문장·검사·1회 재생성·반전·옆모습 제외, D3 45°씩 도는 시트·회전 검사, D4 Hunyuan3D에 옆모습 두 장, D5 화면, D6 마이그레이션 없음. 사용자 답(그대로): "좀 더 명확하게 나타낼 수 있는 의상으로 정한다. 정하는 의상은 첨부되는 사진 속 의상을 기반으로 하면 된다."
- **설계에서 구체화·변경한 점**(requirements 「설계에서 PM 결정을 구체화하거나 바꾼 점」 S1~S12)
  - S1 `sidedItemsEn`을 소품마다 `sightings` 목록으로 묶고 `labelKo`·`partEn`(부위 열거값)을 더했다.
    - 근거: 글자 비교로 같은 소품을 묶으면 놓친다. 부위가 있어야 "RIGHT wrist" 문장과 "오른쪽 손목" 화면 줄을 만든다.
    - 대안: PM의 납작한 목록 + itemEn 소문자 비교(기각 — 모델이 그림마다 이름을 다르게 쓰면 다수결이 갈린다).
  - S2 "첫 참고 그림" 대신 기준 사진의 실제 참고 번호(시트 1·정면 2·후면 3·옆 4)를 쓴다.
    - 근거: 정면·후면 참고 맨 앞은 시트·정면이다(`_build_variant`).
    - 대안: 정면·후면 참고 순서를 바꿔 기준 사진을 맨 앞에(기각 — 시트·정면을 첫 참고로 둔 것은 인물 일관성 때문이고, 바꾸면 끔 상태와 다른 동작이 늘어난다).
  - S3 번호와 함께 `baseSourceFileId`·`sourceFileId`·`baseWhereEn`을 저장한다(업로드 삭제·여러 인물 그림 대비).
  - S4 R1·R2 프롬프트 조각은 충실 분석(`fidelity: 1`)에만 넣는다. 회전 문장·옆모습은 설정 켬이면 옛 분석에도 넣는다.
    - 근거: 옛 `baseOutfitEn`은 겹옷을 뺀 옷이라 "기준 사진 그대로"와 부딪힌다.
  - S5 검사가 모델 오류로 실패한 옆모습도 3D에서 뺀다. S6 그림에 글자가 있으면 반전하지 않는다. S7 옆모습 먼 쪽·후면 눈/볼은 "안 보임"도 맞다고 본다.
  - S8 Seedream 3,000자 자르기 때문에 켬 프롬프트를 2,900자 안으로 맞춘다. 줄이는 순서는 styleEn → appearanceEn → 기본 복장 본문이다. 좌우·고칠 문장·회전·추가 요청은 줄이지 않는다.
  - S9 분석 `max_tokens` 1,200 → 2,000(켬).
  - S10 옆모습은 차례로 그린다.
    - 근거: 줄어드는 시간이 variant당 약 1.5분뿐이다. 선점·하트비트·원장 흐름이 단순하다. ARK 동시 호출을 늘리지 않는다.
    - 대안: 스레드 2개 병렬(기각), webtoon·real variant 병렬(범위 밖, 더 크게 줄일 수 있다).
  - S11 추가 복장 안내 문장도 켬일 때 바꾼다.
  - S12 버린 소품(동률·unclear)의 좌우 낱말은 지우지 않는다.
  - 순수 함수는 새 파일 `vnew/char_fidelity.py`에 둔다(`fx.py`와 같은 방식). `characters.py`가 이미 약 2,450줄이고, 표 경우가 많아 따로 검사하기 쉽다.
- **작업 나누기**(tasks.md): T1 분석·좌우·참고 순서·프롬프트·사진 속 옷(큼), T2 그림·시트 검사·옆모습·3D·재제출·이어 만들기(큼), T3 상세 응답·화면·문서·리뷰(중간). 검사 그룹은 `cf-a`~`cf-s`다. 새 기본값 때문에 `check_vnew_characters`·`check_vnew_fx_prompt`·`check_vnew_casting`을 부르는 줄에서 끔으로 고정한다. 개발 배포는 T3 뒤 한 번이다(VD-0~3).
- **예상 비용 변화**: variant당 Seedream 보통 +2장($0.08), 최대 +4장($0.16). 검사 LLM 보통 +2회, 최대 +4회. 캐릭터(두 그림체) 빌드 보통 13분 → 19분.
- **확인**: 스펙 3개 파일과 이 문서 두 곳만 썼다. 앱 코드(`app/`·`frontend/`·`tools/`·`k8s/`·`deploy/`)는 읽기만 했다. design의 함수·필드 이름은 `characters.py`·`settings.py`·`casting.py`·`vpoc/seedream.py`·`vpoc/llm.py`·`vnew.js`·`check_vpoc.py`를 읽고 맞췄다.
- **정하지 못한 점(Claude 결정 필요, design §16)**
  1. 다시 그린 그림의 원장 기록(지금도 안 남는다. 재생성이 늘면 최대 variant당 $0.16이 원장에 없다).
  2. 최악 빌드 38분 > `BUILD_STALL_S` 30분(선점 확인이 이중 과금은 막는다).
  3. Hunyuan3D v3 단가가 옆모습 입력으로 바뀌는지(네트워크 금지로 확인 못 함, VD-0).
- **남은 이슈**: 검사 모델이 화면 좌우를 잘못 읽을 위험(VD-1에서 확인). `outfitEn`이 길어져 컷 단위 Kling 프롬프트 줄이기가 잦아질 수 있음. "on the left"처럼 부위 낱말 없는 좌우 표현은 지우지 못함. 시트·사진 속 옷 그림은 소품 좌우를 검사하지 않음.

### char-fidelity 묶음 T1 — 분석·좌우 계산·참고 순서·프롬프트·사진 속 옷 (Kiro, claude-opus-5.5)
- **범위**: tasks.md T1만 했다(지시서 `.kiro/handoff/098-char-fidelity-t1.md`). T2(그림·시트 검사, 옆모습, 3D, 재제출, 이어 만들기 옆모습)와 T3(상세 응답·화면·`docs/ANALYSIS.md`)는 하지 않았다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터는 읽지 않았다.
- **Claude 결정(design §16)**: 1 다시 그린 그림 원장 (나), 2 `BUILD_STALL_S` (가) 그대로, 3 Hunyuan3D 단가 $0.675. 셋 다 T2 할 일이라 이번에는 코드에 넣지 않았다.
- **먼저 한 일**: 코드를 바꾸기 전에 `_cf_off_fingerprints()`로 두 설정 끔 지문 32개를 재어 `_CF_OFF_SHA`에 적었다(두 번 재어 같았다). 대상은 분석 SYSTEM·도구(기본·캐스팅 끔), `_analyze` 인자(max_tokens 포함), 분석 블록·결과(추가 요청 없음·있음), 사람 수·복장 검사 호출 인자, 고정 바이트 `submit_3d` 본문, 시트·정면·후면 프롬프트 12개, 추가 복장 프롬프트 4개, 옛 캐릭터 `detail_view` JSON, 가짜 빌드 + 추가 복장 그리기의 외부 호출 순서·인자(`buildTrace`)와 저장 키 집합(`buildKeys`)이다.
- **바꾼 것**
  - `vnew/char_fidelity.py`(신규, import 는 `re`뿐): `PARTS`·`SIDES`·`VIEWS`·`FACINGS`·`BACK_MAY_HIDE`·`MAX_ITEMS`·`MAX_SIGHTINGS`·`SHEET_VIEWS`·`SHEET_FACE_LEFT`, `clean_line`·`_one_line`·`clip_words`·`pick_index`·`strip_item_side_words`·`character_side`·`resolve_sided_items`·`strip_side_words`·`apply_sides`·`photo_layers`·`reference_order`·`pronoun`·`side_sentence`·`side_sentences`.
  - `vnew/settings.py`: `VNEW_CHAR_FIDELITY`·`VNEW_3D_SIDE_VIEWS`(둘 다 기본 true, `VNEW_TAKE_REAL` 바로 뒤). `VNEW_3D_SIDE_VIEWS`는 T1 코드에서 아직 읽지 않는다(T2).
  - `vnew/characters.py`
    - §4: `_ANALYZE_SYSTEM_FIDELITY`·`_PART_ENUM`·`_ANALYZE_FIDELITY_FIELDS`·`_ANALYZE_MAX_TOKENS_FIDELITY`(2000), `_analyze`(max_tokens), `_analyze_system`, `_analyze_tool`, `analyze_sources`(켬이면 그림마다 `Image N:` 글), `_with_fidelity`(신규).
    - §6: `_SHEET_ROTATION`·`_ONE_PERSON_F`·`_PROMPT_MAX`(2900), `_is_fidelity_analysis`·`_valid_sided_item`·`_sided_items`·`_pronoun_of`·`_figure_ref`·`_source_clause`·`_join`·`_clip_outfit_body`·`_budget_prompt`·`_fidelity_prompt`·`_side_prompt`·`_photo_outfit_prompt`(신규), `_sheet_prompt`·`_front_prompt`·`_back_prompt`(켬이면 `_fidelity_prompt`), `_outfit_prompt`(켬 + 충실 분석 소품이면 덧입힘 뒤 좌우 문장).
    - §8: `_reference_jpegs`(신규). `run_build`·`run_resume_variant`·`run_rebuild_variant`의 참고 반복문 자리를 이 도우미로 바꾸고 `base_first`를 넘긴다.
    - `_build_variant`: `base_first` 인자, 켬이면 시트·정면·후면 프롬프트를 `_fidelity_prompt(source_no=1·2·3)`로 만든다. 검사 함수(`_gen_checked`·`_gen_sheet_checked`·`_count_people`·`_check_outfit_consistency`)는 지금 그대로다.
    - §9: `sync_auto_outfits`(켬 + 충실 분석이면 `kind:"photo"`·`sourceFileId`·`whereEn`·`sidedItems`), `_photo_outfit_fields`(신규), `run_build_outfit`(photo 경로), `_photo_outfit_source`(신규).
  - `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`: env 2개(design §2 원문, `VNEW_TAKE_REAL` 바로 뒤, 되돌리기 주석).
  - `tools/check_vpoc.py`: `PARITY_ENV_KEYS`에 두 키, 하네스 `_CfFakes`·`_CfSyncThread`·`_cf_seed_char`·`_cf_old_char`·`_cf_llm_view`·`_cf_sha`·`_cf_jpeg`, `_cf_off_fingerprints`·`_CF_OFF_SHA`, design 원문 상수 `_CF_*`, `check_vnew_char_fidelity`(cf-a·b·c·d·f·g·h·i·l), `_run_char_fidelity_off`, `main()` 고정 3줄과 새 그룹 호출.
- **결정(design 과 다르거나 design 이 정하지 않은 점)**
  - D1 — `_gen_sheet_checked`에 선택 인자 `prompt`를 더했다(design §18 목록 밖). `_build_variant`가 기준 사진 번호(Image 1)를 넣은 시트 프롬프트를 지금 검사 함수에 넘기려면 필요하다. 없으면 지금처럼 `_sheet_prompt`라 끔 바이트가 같다(cf-b). 대안: 시트만 SOURCE 없이 두고 T2 `_gen_sheet_fidelity`로 넘기기(기각 — T1 할 일 "시트·정면·후면 프롬프트를 `_fidelity_prompt`로" 와 R1.6을 못 지킨다).
  - D2 — **보안 리뷰 조치**: `apply_sides`·`strip_side_words`·`strip_item_side_words`가 정규식 전에 글을 한 줄로 줄인다(`_one_line`). 실측: 공백 2,000칸에 28초, 4,000칸 274초, 8,000칸 2,362초(되짚기 폭발). 고친 뒤 200,000자 0.4초 이내. 그래서 켬 `baseOutfitEn`은 소품이 없어도 줄바꿈·연속 공백이 한 칸이 된다(design 은 꼬리만 뗀다고 했다).
  - D3 — 꼬리 정규식은 `[^.]*\.` 대신 글 끝까지(`.*\Z`) 뗀다. 소품 이름에 마침표가 있어도(예: "St. Laurent watch") 두 번 적용한 결과가 같다.
  - D4 — 가르마 규칙 4·5를 규칙 1~3보다 먼저 적용한다. 규칙 2가 "left parting"을 "parting"으로 먼저 바꾸면 "side parting"이 되지 않는다.
  - D5 — 사진 속 옷 원본은 S3 읽기 실패뿐 아니라 업로드 목록에 없는 그림도 쓰지 않는다. `remove_source`는 S3 파일을 지우지 않아 "지운 그림"이 읽기로는 잡히지 않는다(변형 검사로 찾은 빈칸, cf-l). 로그는 `vnew.build_outfit.source_missing char= outfit= err=not_in_sources|{예외 종류}`(메시지·키 없음).
  - D6 — `photo_layers`에 `limit` 키워드 인자를 더했다(`char_fidelity`는 settings 를 import 하지 않는다. `_with_fidelity`가 `MAX_OUTFITS`를 넘긴다). 사진 속 옷 `labelKo`는 한 줄 60자(사용자 복장 이름 상한과 같다), 비면 promptEn 앞 60자.
  - D7 — 로그 `dropped`는 "모델이 준 소품 수 − 정한 소품 수"다(design 은 "버린 수"만 정했다).
  - D8 — `SHEET_FACE_LEFT`·`FACINGS` 상수도 이번에 두었다(design §5.1 목록. T2 함수가 쓴다).
  - D9 — 켬 길이 예산 실측(시트, 단계 1~4는 고칠 문장 171자 포함): 여의준 수준(외모 200·스타일 300·복장 500자, 고칠 문장 없음) 2,678자 단계 0 · 단계 1 2,319자 · 단계 2 2,464자 · 단계 3 2,700자 · 단계 4 2,900자(잘림 로그). design 추정(약 2,700자)과 맞다.
- **기존 검사 고정(부르는 줄에서만, 본문·기대값·들여쓰기 그대로)**: `check_vnew_characters`, `check_vnew_fx_prompt`(기존 캐스팅 끔 고정 안쪽), `check_vnew_casting`. 셋 다 고정 없이 돌리면 실패하는 것을 확인했다((af) 추가 복장 이미지 0장, (fx-k) SYSTEM, (cast-d) SYSTEM). 그 밖에 깨진 검사는 없다.
- **확인**
  - `python -m py_compile`(characters·char_fidelity·settings·check_vpoc) 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 59줄(이전 58 + cf 1), FAIL·WARN 0. rw-a 는 템플릿 env 17개다(이전 15 + 2).
  - cf-b: 두 설정 끔 지문 32개가 구현 전 sha256 과 같다. `VNEW_CHAR_FIDELITY`만 끔(옆모습 켬)에서도 빌드·상세를 뺀 29개가 같다.
  - 여의준 입력: `sidedItems[0].side == "right"`, `baseOutfitEn`이 "… on the RIGHT wrist only."로 끝난다(cf-c·cf-d·cf-g).
  - 구현 상수(충실 SYSTEM·ROTATION·ONE_F·도구 필드·부위 enum)를 design.md 원문과 글자째 비교해 같았다(임시 스크립트, 저장소 밖).
  - 변형 검사 17개(소스 한 곳을 바꿔 새 프로세스에서 cf 그룹 실행): 17개 모두 잡았다. 처음 돌렸을 때 "목록에서 지운 원본" 1개를 놓쳐 검사를 더했다(D5).
  - 바뀐 줄 커버리지(`coverage run --source=app/src tools/check_vpoc.py`, coverage 7.17, 시작 전 사본과 difflib): 실행문 396줄 중 396줄(100%). `characters.py` 218/218, `char_fidelity.py` 176/176, `settings.py` 2/2. coverage 데이터는 저장소 밖에 두고 지웠다.
  - 실제 외부 호출 0회: cf 그룹은 LLM(`_guard_real_llm`)·Seedream·fal·urlopen 원래 함수를 막고 0회를 스스로 확인한다. botocore `_make_api_call`·urlopen 을 막고 기록한 cf 그룹 실행에서 boto3 0회·urlopen 0회였다.
  - 공백만 바뀐 줄 0, 줄 끝 공백 0, 지운 함수·클래스 정의 0, 바뀐 7개 파일 CRLF 0, 저장소 안 `.coverage` 0.
- **3단계 리뷰**
  - 보안
    - 비밀값: 새 비밀값·키 없음. 로그는 번호·개수·종류·길이만(`vnew.char.analysis_fidelity base= sided= dropped= layers=`, `vnew.char_prompt.clipped kind= variant= len=`, `source_missing … err=예외 종류`). cf-g가 로그에 모델 글이 없음을 본다.
    - 입력 검증: 모델 글은 한 줄로 줄이고 자른다(itemEn 80·labelKo 12·whereEn/baseWhereEn 60·사진 속 옷 promptEn 400·이름 60). 열거값은 목록 밖을 버리고 번호는 bool·음수·범위 밖·소수를 버린다(cf-c).
    - 경로 주입: `sourceFileId`·`baseSourceFileId`는 모델 글이 아니라 서버의 업로드 목록에서 번호로 고른다. 원본을 읽기 전에 지금 업로드 목록에 있는지도 본다(D5).
    - 외부 전송: 새 외부 호출이 없다. 사진 속 옷 원본은 지금 참고 그림처럼 Seedream 본문 base64 로만 간다.
    - 발견·조치: 좌우 정규식 되짚기 폭발(D2) → 고침, cf-d 회귀 검사(5만 칸 2초 안).
  - 성능(512Mi)
    - 메모리: 참고 그림 수는 그대로(최대 4장). 사진 속 옷은 원본 1장을 한 번 읽어 Seedream 요청마다 1장 더 보낸다(정면 2장·후면 3장).
    - 외부 호출: T1은 Seedream·LLM 호출을 늘리지 않는다(사진 속 옷 Seedream 4·LLM 0, R7). 분석 max_tokens 1,200 → 2,000(켬)으로 응답이 몇 초 길어질 수 있다. 타임아웃은 바꾸지 않았다.
    - CPU: 예산 맞추기는 프롬프트를 최대 4번 만든다(문자열 연산). 정규식은 D2 뒤 선형이다.
    - 발견: 켬 시트 프롬프트(약 2,700자)에 지금 `_gen_sheet_checked` 재생성 문장(problems + 기본 복장 전문)이 붙으면 3,000자를 넘어 끝이 잘릴 수 있다(남은 이슈 1).
  - 운영성
    - 되돌리기: `config.<target>.env`에 `VNEW_CHAR_FIDELITY=false` 한 줄 + 재배포. 끄면 지금과 바이트 단위로 같다(cf-b). 저장된 새 필드는 지우지 않는다.
    - 진행 중 작업: 배포 전에 분석한 캐릭터를 이어 만들면(옛 분석) 참고 순서는 업로드 순, SOURCE·좌우 문장 없음, 회전 문장·ONE_F 는 들어간다(결정 S4, cf-h·cf-i). 새 코드가 만든 분석을 옛 코드 이미지가 읽어도 오류는 없다(`outfitLayersEn`의 새 키는 `_parse_outfit_layers`가 무시하고, `baseOutfitEn` 꼬리 문장은 그대로 프롬프트에 들어간다. photo 후보는 덧입힘으로 그린다).
    - 재시작 뒤 이어하기: T1은 새 진행 상태를 만들지 않는다. 지금 이어 만들기 규칙 그대로다.
    - 발견: 없음.
- **남은 이슈**
  1. 켬 시트 재생성 프롬프트 길이: 지금 `_gen_sheet_checked`가 붙이는 고칠 문장(모델 problems 를 정리 없이 잇고 기본 복장 전문을 다시 붙인다)이 예산 밖이라 3,000자를 넘을 수 있다. T2 `_gen_sheet_fidelity`(FIX 자리, problems 120자 × 5)로 바뀌면 풀린다.
  2. `VNEW_3D_SIDE_VIEWS`는 아직 효과가 없다(T2). 템플릿·base 주석은 T2 뒤 동작을 적었다. 개발 배포는 T3 뒤 한 번이다.
  3. 정하지 못한 소품(동률·unclear)의 좌우 낱말은 기본 복장 본문에 남는다(결정 S12). "on the left"처럼 부위 낱말 없는 좌우 표현도 지우지 못한다.
  4. 켬 기본 복장 본문의 소품 언급(예: "and a round wristwatch with a brown strap")은 남고, 꼬리 문장이 쪽을 정한다. 개발계 VD-1 그림에서 확인한다.
  5. `docs/ANALYSIS.md` 갱신(§3.6.2 구현 결과·§7.1 설정 표·§10.1 검사 목록)은 T3 이다.

### char-fidelity 묶음 T2 — 그림 검사·시트 검사·옆모습·3D·재제출·이어 만들기 (Kiro, claude-opus-5.5)
- **범위**: tasks.md T2만 했다(지시서 `.kiro/handoff/099-char-fidelity-t2.md`). T3(상세 응답·화면·`docs/ANALYSIS.md`)는 하지 않았다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터는 읽지 않았다. 3D 공급자는 Hunyuan3D v3 그대로다.
- **Claude 결정 반영(099)**
  1. 다시 그린 그림 원장 (나): `_gen_view_checked`·`_gen_sheet_fidelity`가 돌려준 `draws`만큼 `_charge_draws`로 남긴다. 첫 장은 지금 종류, 다시 그린 장은 `{kind}-retry`(sheet·front·back·left·right). 끔·`_gen_checked` 경로는 1건(지금 원장, cf-b `buildTrace` 같음).
  2. `BUILD_STALL_S` 그대로.
  3. `MODEL3D_USD` 기본값 0.375 → 0.675, 주석에 fal 가격표 근거(Normal $0.375 + multi-view +$0.15 + custom face count +$0.15). 이 값으로 깨진 기존 검사는 없었다(전체 검사 통과). 그래서 env 0.375 고정을 넣은 검사도 없다.
  4. 켬 시트 재생성: `_gen_sheet_fidelity`가 고칠 문장을 `_fidelity_prompt(fix=…)`로 넣어 `_budget_prompt`(2,900자) 안에 둔다. cf-k가 여의준 수준 입력(외모 200·스타일 300·복장 500자 + 추가 요청)에서 4번째 인물 + 복장 불일치로 다시 그린 프롬프트를 잰다: 2,876자, 잘림 로그 0, FIX·그림체·추가 요청·회전 문장 남음. T1 방식(예산 뒤에 붙임)이면 3,631자였다.
- **먼저 한 일**: 코드를 바꾸기 전에 `_cf_3d_old_fingerprints()`로 옛 variant(옆모습 키 없음)의 3D 제출·재제출 본문, `_build_3d_from_images` 본문, 이어 만들기 호출 순서(그림 3개·3D 요청 없음 / 3D 요청 있음)를 재어 `_CF_3D_OLD_SHA` 6개로 적었다(두 번 재어 같았다). 시작 전 사본은 저장소 밖에 복사했다(HEAD 3475a7f = 5f8d891 + 지시서 커밋).
- **바꾼 것**
  - `vnew/char_fidelity.py`(import 는 그대로 `re`뿐): `SEEN_FLAT`·`SEEN_SIDE`·`VIEW_FACINGS`·`SHEET_VIEW_VALUES`·`SHEET_FACE_SIDES`·`SIDE_KO`·`MAX_PROBLEMS`·`SHEET_DESC`·`WARN_*`, `seen_values`·`expected_facing`·`expected_seen`·`normalize_view_obs`·`view_mismatches`·`flip_observation`·`can_flip`·`view_fix_text`·`normalize_sheet_obs`·`sheet_mismatches`·`sheet_fix_text`·`sheet_figures_text`·`view_warnings`·`side_warnings`·`merge_warnings`.
  - `vnew/characters.py`
    - §7: `_VIEW_CHECK_MAX_TOKENS`(500)·`_SHEET_CHECK_MAX_TOKENS`(600)·`_VIEW_SYSTEM_HEAD`·`_VIEW_ITEMS_FLAT`·`_VIEW_ITEMS_SIDE`·`_VIEW_SYSTEM_TAIL`·`_SHEET_TOOL`·`_SHEET_SYSTEM`(design 원문), `_view_tool`·`_view_system`·`_inspect_view`·`_inspect_sheet`·`_mirror_jpeg`·`_gen_view_checked`·`_sheet_outfit_fix`·`_gen_sheet_fidelity`(신규). PIL import 에 `ImageOps`.
    - §10: `left_key`·`right_key`·`_HUNYUAN_SIDE_FIELDS`·`_charge_draws`·`_set_variant_checked`·`_set_view_check`·`_draw_side_views`·`_stored_side_view`·`_needs_side_views`·`_resume_side_views`(신규), `submit_3d`(`left_square`·`right_square`)·`_make_3d_resubmit`(`left_sq`·`right_sq`)·`_build_3d_from_images`(`left`·`right`)·`_build_variant`(검사·옆모습 흐름)·`run_retry3d`(저장된 옆모습)·`run_resume_variant`(옆모습만 이어 그리기).
  - `vnew/settings.py`: `MODEL3D_USD` 기본값·주석(결정 3)만.
  - `tools/check_vpoc.py`: `_CF_3D_OLD_SHA`·`_cf_3d_old_fingerprints`, 하네스 `_CfFakesT2`(그림·시트 검사 가짜, 왼쪽·오른쪽 반 색이 다른 그림, Seedream hook)·`_cf_halves`·`_cf_mirror_ok`·`_cf_view_answer`·`_cf_sheet_answer`·`_CfEnv`·`_sheet_old_fix`, design 원문 상수, `_cf_t2_bodies`(cf-e·cf-f2·cf-j·cf-k·cf-m·cf-n·cf-o·cf-p·cf-q). `check_vnew_char_fidelity`는 T1 본문 뒤에 T2 본문을 같은 틀로 돌린다(docstring·실행 목록·끝까지 돈 검사 목록·ok 문구만 바꿨다).
- **결정(design 과 다르거나 design 이 정하지 않은 점)**
  - E1 — 시트 복장 고칠 문장의 끝 "…nothing else: {기본 복장}." 은 첫 프롬프트에 남은 자리(2,900 − 첫 프롬프트 길이 − 1)에 들어갈 때만 기본 복장 글을 붙인다. 넘치면 기본 복장 글 반복만 뺀 "…nothing else." 를 쓴다. 기본 복장은 이미 WHO 에 있고, 반복 때문에 예산이 넘치면 끝의 그림체·추가 요청이 잘린다(T1 ONE_F 가 옷 글 반복을 뺀 것과 같은 이유). 짧은 분석은 지금 문장 그대로다(cf-k). 끝 마침표는 한 번 떼고 붙인다("only.." 방지).
  - E2 — `_gen_sheet_fidelity`는 첫 시트를 그린 뒤에도 하트비트를 찍는다(design 은 재생성 뒤만). 그림마다 하트비트(R4.8)와 맞춘다.
  - E3 — `viewCheck.front`·`back`은 그림 검사(`_gen_view_checked`, 정한 소품 있음)로 검사했을 때만 쓴다. 소품이 없어 지금 사람 수 검사(`_gen_checked`)를 쓰면 ok·fixed·unchecked 를 가릴 수 없어 쓰지 않는다. `viewCheck.sheet`는 켬이면 늘 쓴다.
  - E4 — `_resume_side_views`의 warning 은 저장된 warning + 옆모습 결과 전체(재시작 전에 끝낸 쪽 포함)로 다시 만든다. 옆모습 warning 은 마지막 `model3d` patch 에서만 저장되므로, 왼쪽을 뺀 뒤 끊기면 그 문구가 사라지기 때문이다(cf-p).
  - E5 — 저장된 옆모습을 못 읽으면 `vnew.{retry3d|resume}.side_missing char= variant= view= err=예외 종류`를 남기고 그 쪽만 뺀다(design 표에는 retry3d 만 있다).
  - E6 — design §18 목록 밖 도우미: `_view_system`·`_charge_draws`·`_sheet_outfit_fix`·`_set_variant_checked`·`_stored_side_view`, `_build_variant` 안 `plain_prompt`·`checked_view`, char_fidelity 의 `seen_values`·`_count`·`_item_fix`·`_drawn_as`·`sheet_figures_text`·`side_warnings`·`merge_warnings`. `plain_prompt`는 지금 정면·후면 줄을 들여쓰기만 바꿔 옮기지 않으려고(공백만 바뀐 줄 0) 두었다.
  - E7 — `normalize_sheet_obs`는 dict 가 아닌 인물 항목을 other·hidden 으로 두어 자리를 지킨다(빼면 인물 수가 줄어 엉뚱한 "다섯 인물" 고칠 문장이 된다). `consistent`는 True 또는 "true" 만 참이다. 기본 복장이 빌 때 consistent=true 는 `_inspect_sheet`에서 정한다.
  - E8 — 시트 고칠 문장 `{got}`의 관사는 모음이면 "an"("an other view"), 얼굴 쪽은 left·right 일 때만 붙인다(center 는 "facing the center" 가 어색하다).
  - E9 — `_inspect_view`·`_inspect_sheet`는 응답이 dict 가 아니면 `failed err=bad_payload`를 남기고 None 이다.
- **기존 검사 고정**: 새로 고정한 검사는 없다. T1 에서 고정한 `check_vnew_characters`·`check_vnew_fx_prompt`·`check_vnew_casting` 세 줄은 그대로 두 설정 끔이다. 결정 3(0.675) 때문에 고친 검사도 없다.
- **확인**
  - `python -m py_compile`(characters·char_fidelity·settings·check_vpoc) 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 59줄, FAIL·WARN 0(약 83초).
  - cf-b(T1 끔 지문 32개) 그대로. cf-o: 옛 variant 3D 본문·재제출 본문·`_build_3d_from_images` 본문이 켬·끔·옆모습만 켬에서 구현 전 sha256 과 같다. cf-p: 끔 이어 만들기 호출과 3D 요청이 있는 이어 만들기 호출(켬·끔)이 구현 전 sha256 과 같다.
  - cf-j: 소품 없음 → `_count_people` 4회(그림체 2 × 정면·후면), 인자가 끔과 같고 정면·후면 `emit_view_check` 0회.
  - cf-m: variant당 Seedream/검사 LLM 보통 5/5, 최악 10/10, 빌드 1회 최악 20/20(R7 상한).
  - cf-n·cf-j: 반전 그림은 픽셀 비교로 좌우가 뒤집혔다(왼쪽 반·오른쪽 반 색이 다른 가짜 그림). 반전으로도 안 맞는 옆모습은 키 ""·저장 없음·3D 본문에 없다.
  - cf-q: 선점을 뺏으면(시트 재생성 직전·왼쪽 재생성 직전·오른쪽 직전·3D 제출 직전) 다음 Seedream·3D 제출 0회, 상태 그대로, `vnew.build_variant_claim_lost` 1줄. 켬 빌드에서 Seedream 12회·3D 제출 2회 모두 직전에 선점 확인, 그림마다 바로 뒤 하트비트. 가짜 시계 20분 동안 대기 variant 가 끊긴 것으로 보이지 않았다.
  - 변형 검사 10개(char_fidelity·characters 함수를 바꿔 끼워 cf 그룹 실행): 10개 모두 잡았다.
  - 바뀐 줄 커버리지(`coverage run --source=app/src tools/check_vpoc.py`, coverage 7.17, 시작 전 사본과 difflib): 실행문 496줄 중 496줄(100%). `characters.py` 308/308, `char_fidelity.py` 187/187, `settings.py` 1/1. coverage 데이터는 저장소 밖(`%TEMP%`)에 두었다.
  - 실제 외부 호출 0회: cf 그룹이 LLM·Seedream·fal·urlopen 원래 함수를 막고 스스로 센다. botocore `_make_api_call`·urlopen·socket 연결을 막고 기록한 cf 그룹 실행에서도 0회였다.
  - 공백만 바뀐 줄 0, 줄 끝 공백 0, 지운 함수·클래스 정의 0, 바뀐 6개 파일(tasks.md 포함) CR 0, 저장소 안 `.coverage` 0.
- **3단계 리뷰**
  - 보안
    - 비밀값: 새 비밀값·키 없음. 새 로그(`vnew.char.view_check`·`vnew.char.sheet_check`·`*.failed`·`*.side_missing`)는 상태·개수·열거값·예외 종류만 남긴다. 소품 글·프롬프트·S3 키·URL 없음.
    - 입력 검증: 검사 응답은 열거값 밖을 버리고(facing → other, view → other, faceSide → hidden), 번호는 범위·bool 을 검사하고, 사람 수는 정수·숫자 글자만, lettering 은 True 만 참이다(cf-e). 모델 `problems`는 한 줄 120자 × 5개로 줄여 Seedream 고칠 문장에 넣는다. 지금 끔 경로(`_gen_sheet_checked`)는 정리 없이 넣는다(바꾸지 않았다).
    - 외부 전송: 옆모습은 fal 본문 data URI(정사각 패딩)로만, 검사는 Bedrock 바이트 블록으로만 보낸다. 서명 URL 을 보내지 않는다. 결과는 `users/{owner}/characters/{id}/{variant}/v{n}/left.jpg`·`right.jpg`(IRSA `users/*` 안).
    - 경로: 옆모습 키는 서버가 `_variant_dir`로 만든다. 읽을 때도 서버가 쓴 `leftKey`·`rightKey`만 읽는다(지금 `frontKey`와 같은 신뢰 수준).
    - 반전: Seedream 결과(1440x2560 이하)만 디코드한다. 사용자 업로드는 뒤집지 않는다.
    - 발견·조치: 조치할 것 없음.
  - 성능(512Mi)
    - 메모리: variant 빌드가 더 쥐는 것은 옆모습 JPEG 2장(장당 약 0.5~1MB), 정사각 2장(장당 약 0.1~0.3MB), 3D 본문 data URI 2개(정사각의 1.37배)와 그 JSON 직렬화 사본이다. 3D 본문은 data URI 4개(약 0.6~1.6MB)와 직렬화 사본을 합쳐 제출 순간 몇 MB 다. 재제출 콜백이 정사각 4장(약 1MB 안)을 폴링 동안(최대 15분) 쥔다. 반전은 그림 1장을 디코드해 RGB 2벌(약 11MB씩)을 잠깐 쓴다. 빌드 하나에 수십 MB 안쪽이고, variant 는 차례로 돈다.
    - 발견·조치: `_resume_side_views`가 `_reference_jpegs`로 읽은 업로드 4장을 3D 폴링 내내 쥐고 있었다 → 기준 사진 1장만 남기고 바로 놓는다(`del refs`). 업로드 3장 읽기(S3 GET)는 이어 만들기 경로에서만 남는다(남은 이슈 6).
    - 외부 호출: R7 상한(cf-m). 정면·후면 그림 검사의 첫 그림은 바깥 선점 확인 + 안쪽 선점 확인으로 char.json 읽기가 1번 더 있다(그림 1장 60~100초에 비해 작다). 옆모습마다 쓰기 3번(하트비트·viewCheck·하트비트). 타임아웃은 바꾸지 않았다.
    - 시간: variant당 보통 약 +3분, 최악 빌드 약 38분(결정 2로 `BUILD_STALL_S` 그대로).
  - 운영성
    - 되돌리기: `VNEW_3D_SIDE_VIEWS=false`면 옆모습을 그리지 않고, 저장된 옆모습도 retry3d·이어 만들기에서 보내지 않는다(cf-o). `VNEW_CHAR_FIDELITY=false`면 시트·정면·후면 검사가 지금 함수다. 둘 다 끄면 호출·원장·본문이 지금과 같다(cf-b·cf-o·cf-p). 단가는 `VNEW_3D_USD=0.375` 한 줄로 되돌린다.
    - 진행 중 작업: T1 이미지로 만들던 variant 를 T2 가 이어 만들면, 그림 3개가 있고 3D 요청이 없을 때 옆모습만 그리고 3D 로 간다(R4.7, 옛 분석이면 SOURCE·좌우 문장 없음). 3D 요청이 있으면 조회만 한다. 옛 코드 이미지로 되돌리면 `leftKey`·`viewCheck`를 읽지 않을 뿐 오류가 없다.
    - 재시작 뒤 이어하기: 옆모습 하나를 끝낼 때마다 `viewCheck`·키를 저장해 남은 쪽만 그린다. 그리는 동안 variant 는 `images`라 끊기면 자동 이어 만들기 대상이다(cf-p).
    - 발견·조치: 왼쪽을 3D 에서 뺀 뒤 끊기면 이어 만들기에서 그 warning 이 사라지는 문제를 설계 중 찾아 결과 전체로 다시 만들게 했다(E4).
- **남은 이슈**
  1. 원장은 `_gen_*`이 돌려준 뒤 남긴다. 선점 뺏김·재생성 Seedream 실패·파드 종료로 도중에 끊기면 이미 그린 그림이 원장에 없다(지금 `_gen_checked`와 같은 방식). 소품 없는 정면·후면(`_gen_checked`)의 재생성도 결정 1 범위 밖이라 1건만 남는다.
  2. 시트 재생성 고칠 문장이 길면(틀린 인물 둘 이상 + 복장 problems 여러 개) 여의준 수준 입력에서 2,900자를 넘어 단계 4(끝 자름·로그)로 갈 수 있다. 실측 여유는 인물 하나 + problem 하나에서 24자다.
  3. 시트 4번째(3/4 후면) 인물은 얼굴 쪽이 left 여야 맞다고 본다(design). 검사 모델이 hidden 으로 답하면 맞는 시트도 다시 그린다. VD-1 로그 `vnew.char.sheet_check figures=`로 확인한다.
  4. 옆모습 Seedream 이 실패하면(필터 등) variant 전체가 failed 다(design §12). 정면·후면은 남아 있어 3D 만 다시(옆모습 키가 있으면 함께)로 복구한다.
  5. `deploy/config.env.example`의 단가 주석이 아직 `VNEW_3D_USD=0.375`다(T2 범위 밖). `docs/ANALYSIS.md` 갱신과 함께 T3 에서 고친다.
  6. `_resume_side_views`는 design 대로 `_reference_jpegs`를 불러 업로드를 최대 4장 읽고 1장만 쓴다(이어 만들기 경로의 S3 GET 3번).
  7. Hunyuan3D `left_image_url`·`right_image_url` 좌우 규칙은 개발계 VD-1 GLB 렌더로 확인한다(반대면 `_HUNYUAN_SIDE_FIELDS` 한 줄).

### char-fidelity 묶음 T2b — 캐릭터 3D 공급자 Tripo H3.1: 얼굴 일그러짐 개선 (Kiro, claude-opus-5.5)
- **범위**: 지시서 `.kiro/handoff/100-char-3d-tripo.md`. 스펙 보강(requirements R8·design §19·tasks T2b)과 구현을 함께 했다. T3(화면 이름표·`docs/ANALYSIS.md`)는 하지 않았다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터는 읽지 않았다.
- **사용자 지시(2026-10-30, 원문)**: "캐릭터 만들기에서 3D 모델링 할 때 얼굴이 일그러져서 제대로 보이지 않는 상태로 보이고 있다. 이 부분도 개선을 할 수 있도록 진행해라."
- **원인(Claude 확인, 개발계 여의준 `admin/char_20261030_a7b69d` 정면·후면 그대로)**: 입력이 1024 정사각이라 얼굴이 약 150px, `face_count` 60000이라 머리에 약 8천 면. 웹툰은 눈 위 앞머리가 덩어리로 굳어 한쪽 눈을 덮고, 실사는 얼굴 질감이 부어 보이고 이음선이 있다.
- **모델 비교(Claude, 같은 입력)**

  | 모델 | 결과 |
  |---|---|
  | Hunyuan3D v3, 2048 입력 + 50만 면 | 굴곡은 매끄러워졌지만 앞머리 덩어리와 부은 얼굴은 그대로 |
  | Hunyuan 3.1 Pro | 조금 나음 |
  | Rodin Gen-2 | 얼굴이 일그러짐 |
  | Meshy 6 | 얼굴은 괜찮지만 3만 면에 2K 텍스처 |
  | **Tripo H3.1**(`texture_quality`·`geometry_quality` detailed) | **두 그림체 모두 사진과 가장 가깝다.** 웹툰은 두 눈과 그림체가 살고, 실사도 얼굴이 사진과 비슷하다 |

  - Tripo 추가 확인: `face_limit` 300000이면 GLB 약 12MB·얼굴 품질 그대로(제한 없음 190만 면·58MB). fal은 `image_urls`의 빈 문자열 자리를 "Failed to download the file"로 거절한다. 정면이 +X(`orientation: align_image`도 같음), 화면 model-viewer는 +Z가 정면. 시계는 입력 그림과 같은 쪽 손목에만 있었고(옛 입력은 왼손목) 반대쪽에 없는 줄이 생기지 않았다. −90° 회전 뒤 +Z 정면·좌우 반전 없음을 Claude가 렌더로 확인했다.
  - 가격(fal 표 2026-10-30): HD 텍스처 $0.40 + detailed 형상 $0.20 = 1건 $0.60(지금 Hunyuan $0.675보다 싸다).
- **결정(Claude 결정 1~8)과 반영**
  1. 설정 11개(`VNEW_3D_PROVIDER` 기본 `tripo` 외): `vnew/settings.py`의 새 읽기 함수 `_checked_choice`·`_checked_int`·`_checked_float`·`_checked_bool`·`_checked_tripo_endpoint`(틀린 값 → 기본값 + 경고 `vnew.settings.invalid` 1줄), 템플릿·base(`VNEW_3D_SIDE_VIEWS` 바로 뒤), `PARITY_ENV_KEYS`.
  2. `submit_3d(provider=…)`: hunyuan이면 T2 본문 그대로(시작 전 sha256 16개로 고정, tr-k). 요청 꼬리는 `_post_3d`로 옮겼다(동작 같음).
  3. Tripo 본문 `_tripo_request`: 공통 값 6개, 다시점 [front, left, back(, right)]·SIDE_SWAP·단일, 빈 자리·None 0, data URI만, `enable_safety_checker` 없음. 정사각 `_3d_squares`(Tripo 2048, Hunyuan `MODEL_INPUT_SQUARE`). arn = 실제 endpoint, 조회 주소 `tripo3d/h3.1`.
  4. 재제출·이어 만들기: `_make_3d_resubmit(provider, counter)`·`_build_3d_from_images`·`run_retry3d`가 같은 분기 함수를 쓴다. 잃은 요청 재제출은 처음 제출과 같은 공급자·정사각. `run_retry3d`가 조회하는 이전 요청의 공급자가 설정과 다르면 그 요청의 재제출은 이전 공급자로 하고, 한도는 함께 센다.
  5. `_copy_3d_result(request_arn=)`: Tripo arn·YAW≠0이면 `_orient_glb`(순수 함수: 기본 scene 루트 노드에 Y축 회전을 앞에서 곱하고, JSON 공백·BIN 0 으로 4바이트 정렬, BIN 바이트 그대로). 틀린 GLB → 경고 `vnew.3d.orient_failed` + 받은 GLB 저장.
  6. 썸네일: `thumbnail.url` → `rendered_image.url`(새 줄) → 첫 `.png`.
  7. 원장: Tripo `VNEW_3D_TRIPO_USD`·model = 실제 endpoint(`_charge_tripo_3d`), Hunyuan은 지금 `_charge_3d` 그대로. 재제출도 1건씩.
  8. `model3d.endpoint`: Tripo 요청에만 저장(아래 E1).
- **먼저 한 일**: 코드를 바꾸기 전에 `_tr_hunyuan_fingerprints()`로 Hunyuan 경로 16개(옆모습 있는 본문 4종·조회·`_build_3d_from_images` 잃은 요청 재제출의 호출·원장 인자·`model3d`·옮긴 파일, `run_retry3d` 같은 4종, `_copy_3d_result` 3종)를 두 번 재어 같은 값을 `_TR_HUNYUAN_SHA`로 적었다. 시작 전 사본은 저장소 밖(`%TEMP%`)에 두었다.
- **바꾼 것**
  - `vnew/settings.py`: import `logging`·`math`·`re`, `log`, `_invalid`·`_checked_*`·`_TRIPO_ENDPOINT_RE`·`_TRIPO_QUALITIES`, 설정 11개(근거 주석 포함). 기존 `_int`·`_float`·`_bool`·설정은 그대로.
  - `vnew/characters.py`: import `math`·`struct`. 새 `_TRIPO_PREFIX`·`_3d_provider`·`_is_tripo_endpoint`·`_provider_of`·`_3d_squares`·`_tripo_request`·`_post_3d`·`_charge_tripo_3d`·`_charge_3d_request`·`_note_3d_endpoint`·`_set_variant_3d_request`·`_GLB_MAGIC`·`_GLB_CHUNK_JSON`·`_snap`·`_glb_numbers`·`_yaw_node`·`_orient_glb`. 바꾼 `submit_3d`·`poll_3d`·`_make_3d_resubmit`·`_build_3d_from_images`·`_copy_3d_result`·`run_retry3d`.
  - `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`: env 11개, 되돌리기 주석.
  - `tools/check_vpoc.py`: `check_vnew_3d_tripo`(tr-a~l), 하네스 `_TrFakes`(Tripo 응답 모양·진짜 GLB·원장 model/usd·잃은 요청), `_tr_*` 도우미·`_TR_*` 상수, `_tr_hunyuan_fingerprints`·`_TR_HUNYUAN_SHA`, `_run_3d_hunyuan`, `PARITY_ENV_KEYS` 11개, `main()`.
  - `.kiro/specs/char-fidelity/`: requirements R8(+ 결정 반영 위치 표 한 줄), design §19, tasks T2b(+ VD-4).
- **결정(지시서와 다르거나 지시서가 정하지 않은 점)**
  - E1 — `model3d.endpoint`는 Tripo 요청에만 저장하고, Hunyuan 요청을 저장할 때는 남은 값을 지운다. cf-b `buildKeys`(T1 고정)가 hunyuan 빌드의 `model3d` 키 집합을 보므로 Hunyuan에도 키를 더하면 T1 고정이 깨진다. T3 이름표에서 Hunyuan 표시가 필요하면 `requestArn`에서 endpoint를 뽑을 수 있다.
  - E2 — `_orient_glb`는 로그 없는 순수 함수로 두고 틀린 GLB면 `ValueError`(18종, 너무 깊은 JSON의 `RecursionError` 포함)를 올린다. 원본 저장·경고는 `_copy_3d_result`가 한다.
  - E3 — 루트 노드의 `translation`도 R·t로 돌린다. 노드 변환 전체를 앞에서 곱한 것과 같게 하려면 필요하다(지시서는 rotation·matrix만 적었다).
  - E4 — `_charge_3d`는 인자까지 그대로 두고 `_charge_3d_request`가 나눈다. 기존 검사 (v)가 `_charge_3d`를 인자 3개 래퍼로 바꿔 끼워 원장 수를 센다(검사 본문 수정 금지).
  - E5 — `VNEW_3D_SIDE_SWAP`은 Tripo 본문에만 쓴다. Hunyuan 좌우는 T2 `_HUNYUAN_SIDE_FIELDS` 한 줄 그대로다(hunyuan 바이트 같음 조건).
  - E6 — 틀린 값의 기준: FACE_LIMIT 10000~1000000(GLB 약 30MB 이하), INPUT_SQUARE 512~4096, YAW −360~360, USD 0~10, endpoint `^tripo3d(/[a-z0-9][a-z0-9._-]*){2,}$`. 경고 줄의 값은 40자까지만 쓴다.
  - E7 — `run_retry3d`의 교차 공급자 재제출 콜백과 `counter` 공유(결정 4 "같은 공급자·그림"의 구체화). 한 번의 retry3d 제출은 T2처럼 최대 1 + `MAX_RESUBMITS`건.
  - E8 — Tripo 단일이어도 `_3d_squares`가 후면 정사각을 만든다(분기를 `_tripo_request` 하나에 둔다). 2048 정사각 1장 CPU·0.1~0.5MB.
- **기존 검사 고정**(`main()` 호출 줄만, 본문·기대값 그대로): `check_vnew_characters` → `_run_3d_hunyuan(lambda: _run_char_fidelity_off(check_vnew_characters))`, `check_vnew_char_fidelity` → `_run_3d_hunyuan(check_vnew_char_fidelity)`. 고정 전 tripo 기본값에서 (b)(v)·cf-b(`submit3d`·`buildTrace`·`buildKeys`)·cf-m·cf-n·cf-o·cf-p가 깨졌다. 그 밖의 기존 검사는 그대로 통과했다.
- **확인**
  - `python -m py_compile`(characters·settings·check_vpoc) 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 60줄(T2 59줄 + tr 1줄), FAIL·WARN 0(약 90~110초). cf-b 32개·cf-o·cf-p(T1·T2 sha256) 그대로, tr-k Hunyuan 지문 16개 = 시작 전.
  - 바뀐 줄 커버리지(`coverage run --data-file=%TEMP%\… --source=app/src tools/check_vpoc.py`, coverage 7.17, 시작 전 사본과 difflib): 실행문 241줄 중 241줄(100%). `characters.py` 172/172, `settings.py` 69/69.
  - 변형 검사 18개(`characters.py`를 메모리에서만 바꿔 tr 그룹 실행: SIDE_SWAP 무시·순서·translation·Hunyuan 회전·원장·교차 공급자·endpoint 지우기·rendered_image·정사각·쿼터니언 곱 순서·BIN 0 채움·재제출 provider·빈 바이트·한도·matrix 곱 순서·JSON 채움·orientation·단일 endpoint): 18개 모두 잡았다.
  - 실제 외부 호출 0회: tr 그룹이 LLM(`_guard_real_llm`)·Seedream·fal·urlopen 원래 함수를 막고 센다.
  - 줄 끝 LF(바뀐 9개 파일 CR 0), 공백만 바뀐 줄 0, 줄 끝 공백 0, 지운 함수·클래스 정의 0, 저장소 안 `.coverage` 0.
- **3단계 리뷰**
  - 보안
    - 외부 전송: Tripo 본문의 그림은 모두 `_data_uri`다. tr-c가 11경우 모두 `image_url(s)`가 `data:image/jpeg;base64,`로 시작하고 본문 JSON에 `http`·`X-Amz`가 없음을 본다. 외부 저장소 업로드·presigned URL 없음.
    - 입력 검증: endpoint 설정은 정규식으로 `tripo3d/…` 경로 글자만 받는다(`..`·`?`·대문자 거절, tr-a). fal이 준 GLB는 길이·청크·scene·노드 번호·숫자 배열을 검사하고, 틀리면 저장만 원본으로 한다(파일 경로·명령 없음).
    - 비밀값·로그: 새 비밀값 없음. `vnew.settings.invalid`는 비밀이 아닌 3D 설정 키와 값 40자(`%r`), `vnew.3d.orient_failed`는 우리 예외 문구만(바이트·URL 없음, tr-g).
    - 권한: 새 라우트 없음. 저장 위치는 지금과 같은 `users/{owner}/characters/…/model.glb`.
    - 발견·조치: 너무 깊은 JSON이 `RecursionError`로 `_orient_glb` 계약(`ValueError`) 밖으로 나가던 것을 찾아 `ValueError`로 감쌌다(호출한 쪽은 원래도 `Exception`으로 받아 원본을 저장했다). tr-f에 "깊은 JSON" 경우를 더했다.
  - 성능(512Mi)
    - GLB: 받은 12MB + 돌린 12MB가 잠깐 함께 있다. `_orient_glb`는 BIN을 `memoryview`로 넘겨 결과 한 벌만 만든다. tr-l 12MB GLB 메모리 최고점 12.0MB(입력 12.0MB), 3~10ms. FACE_LIMIT 상한 100만 면(약 30MB)에서도 60MB 안이다.
    - 정사각: 2048² RGB 캔버스 약 12.6MB + 원본 디코드 약 11MB를 한 장씩 차례로 쓴다. 합성 그림(1440×2560)으로 잰 data URI는 장당 0.16~0.69MB, 4장 본문 0.63~2.76MB(1024일 때 0.24~0.60MB). 재제출 콜백이 폴링 동안 정사각 4장(최대 약 2MB)을 쥔다. `run_retry3d`는 저장된 옆모습 원본을 정사각을 만든 뒤 놓는다(`del stored`).
    - 외부 호출 수·타임아웃은 그대로다(3D 제출 1 + 재제출 ≤ 2, 조회 5초·최대 15분, 다운로드 300초). 교차 공급자에서도 제출 합계는 3건(tr-i).
    - 발견·조치: 조치할 것 없음(본문 크기 한도는 남은 이슈 2).
  - 운영성
    - 되돌리기: `config.<target>.env`에 `VNEW_3D_PROVIDER=hunyuan` 한 줄 → 렌더 `"hunyuan"`(tr-b) → T2 본문·원장·저장 바이트 그대로(tr-k). 회전만 끄기 `VNEW_3D_TRIPO_YAW_DEG=0`, 다시점만 끄기 `VNEW_3D_TRIPO_MULTIVIEW=false`, 좌우 반대 `VNEW_3D_SIDE_SWAP=true`.
    - 진행 중 작업: 배포 전에 낸 Hunyuan 요청은 공급자 설정이 아니라 arn endpoint로 판단해 Hunyuan 주소로 조회·완료하고 돌리지 않는다(tr-j). 잃은 요청이면 Hunyuan 본문으로 다시 낸다(tr-i). 반대로 hunyuan으로 되돌린 뒤 남은 Tripo 요청도 Tripo로 이어 가고 돌린다(tr-i).
    - 재시작 뒤 이어하기: 3D 요청이 있으면 `run_resume_variant` → `run_retry3d`가 arn으로 이어 간다(지금과 같음). 옆모습만 이어 그리는 경로도 Tripo 다시점으로 간다(tr-j).
    - 로그: `vnew.3d.oriented`(info)·`vnew.3d.orient_failed`(경고)·`vnew.settings.invalid`(경고). 원장 실패는 지금처럼 `vnew.ledger.3d_failed`(tr-h).
    - 코드 이미지 되돌리기: 옛 코드도 `_queue_app`으로 Tripo 요청을 조회할 수 있지만 GLB를 돌리지 않고(정면이 옆으로 보임) Hunyuan 단가로 원장을 남긴다. 이미지를 되돌릴 때는 `VNEW_3D_PROVIDER=hunyuan`으로 먼저 바꾸고 진행 중 Tripo 요청이 끝난 뒤 되돌린다.
    - 발견·조치: 공급자를 되돌린 뒤 새 Hunyuan 요청에 옛 Tripo `endpoint`가 남는 문제를 설계 중 찾아 새 요청을 저장할 때 지우게 했다(`_note_3d_endpoint`, tr-i).
- **남은 이슈**
  1. Tripo 정면 방향(−90°)·좌우 자리는 Claude가 말한 사실(+X 정면, left = 인물 자신의 왼쪽)과 수학으로만 확인했다. 개발계 VD-4(tasks)로 확인한다.
  2. fal 요청 본문 크기 한도를 확인하지 못했다(네트워크 금지). 2048 정사각 4장이면 합성 그림 기준 최대 약 2.8MB다. 거절되면 `VNEW_3D_TRIPO_INPUT_SQUARE=1536` 한 줄.
  3. Tripo 처리 시간을 확인하지 못했다. 조회 상한 15분(`VNEW_3D_POLL_MAX_S`)은 그대로다.
  4. 왼쪽 옆모습이 3D에서 빠지면 Tripo 순서 규칙상 [front, back]을 보낼 수 없어 단일(정면만)이다. 후면 정보가 빠진다.
  5. T3 이름표: Hunyuan 요청에는 `model3d.endpoint`가 없다(E1). `requestArn`으로 정할지 T3에서 정한다.
  6. 썸네일은 지금처럼 `image/png`로 저장한다. `rendered_image`가 png가 아니면 content-type이 틀린다(Claude 실측은 png).
  7. 루트 노드에 애니메이션이 걸린 GLB면 회전이 덮일 수 있다(Tripo 결과는 정적 메시로 본다).
  8. `docs/ANALYSIS.md`(Tripo 전환·설정·되돌리기)와 `config.env.example` 주석 `VNEW_3D_USD=0.375`는 T3에서 고친다.


### char-fidelity 묶음 T3 — 상세 응답·화면·3D 이름표·문서, 운영 설정(vnew 켬·Seedance 두 경로) (Kiro, claude-opus-5.5)
- **범위**: 지시서 `.kiro/handoff/101-char-fidelity-t3.md`. tasks.md T3 전부와 운영 설정·계획 문서를 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터를 읽지 않았다. 배포는 계획만 썼다.
- **사용자 결정(2026-10-30, 원문)**: "앞으로 추가로 운영에도 같이 배포를 진행해라", 운영 vnew "켠다", "seedance도 사용할 수 있고 fal seedance도 사용할 수 있게 해라".
- **먼저 한 일**: 바꿀 파일 전부의 시작 전 사본을 저장소 밖(`%TEMP%`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 60줄, 88초). 화면 기준값 `_CF_SCREEN_OLD_SHA`는 시작 전 `vnew.js` 사본으로 옛 캐릭터 + 두 설정 끔 상세를 `renderDetail`에 넣어 두 번 재어 같은 값을 적었다. 상세 응답 기준값은 T1 시작 전에 잰 `_CF_OFF_SHA["detailOld"]`를 그대로 쓴다.
- **바꾼 것**
  - `vnew/characters.py`: `detail_view`(variant `leftUrl`·`rightUrl`은 키가 있을 때만, `model3d.endpoint`는 저장된 문자열일 때만, 맨 위 `features{charFidelity, sideViews}`는 두 설정 중 하나라도 켜져 있을 때만), `_outfits_view`(저장된 `kind`가 있을 때만). 다른 함수는 바꾸지 않았다.
  - `frontend/public/vnew.js`
    - 새 순수 구역(`renderDetail` 바로 앞): `CHAR_PART_KO`·`CHAR_SIDE_KO`·`CHAR_3D_ENDPOINT_LABEL`·`CHAR_VIEW_PAIRS`·`OUTFITS_HINT`·`OUTFITS_HINT_FIDELITY`, `charTableValue`·`charFidelityOn`·`analysisFidelityLines`·`variantViewPairs`·`outfitBadgeText`·`outfitsHintText`·`model3dEndpointLabel`.
    - DOM: `renderDetail`(분석 카드 "기본 복장" 줄 다음에 새 줄), `variantColumn`(작은 그림 = `variantViewPairs`, 3D 보기 바로 아래 이름표 `p.vnew-3d-endpoint`), `outfitsSection`(안내 = `outfitsHintText`), `outfitCard`(배지 = `outfitBadgeText`).
  - `frontend/public/vnew.css`: `.vnew-sheet-smalls`에 `flex-wrap: wrap;`, 새 `.vnew-3d-endpoint`(12px, 흐린 글씨).
  - `deploy/config.prod.env`: `VNEW_ENABLED=false` → `true`, `FAL_SECRET_NAME` 줄 다음에 주석 1줄 + `VPOC_ARK_SEEDANCE_ENABLED=true`. BOM·LF 그대로, 다른 줄은 그대로. `config.dev.env`는 바꾸지 않았다.
  - `tools/check_vpoc.py`
    - `check_vnew_char_fidelity`에 cf-r(실행 목록·끝까지 돈 검사 목록·docstring·ok 문구도). 도우미 `_cf_new_char`·`_cf_detail_of`.
    - 새 `check_vnew_char_fidelity_screen`(cf-s)와 `_CF_SCREEN_SLICES`·`_CF_SCREEN_NODE_STUBS`·`_CF_SCREEN_NODE_OLD_CASES`·`_CF_SCREEN_NODE_CASES`·`_CF_SCREEN_OLD_SHA`·`_CF_HINT_*`·`_cf_screen_details`·`_cf_screen_script`·`_cf_run_node`. `main()` 끝에서 부른다.
    - `check_fal_parity_rollback`(rw-c) 기대값(아래).
  - 문서: `docs/ANALYSIS.md`(머리 갱신 줄, §1, §2.2·§2.3 표, §3.6.2 「구현 결과」 표·「3D 공급자 Tripo H3.1 전환」, §5.4, §6.2, §6.3, §7.1 표·설명, §8.5, §9.1, §10.1, R-21, §12), `docs/plans/2026-10-26-prod-deploy-fal-seedance.md`(상태·§1·§2·§3·단계 1·2·3·§6·§8), 새 `docs/plans/2026-10-30-prod-vnew-enable.md`, `.kiro/specs/char-fidelity/tasks.md` T3 체크.
- **바꾼 기존 검사 기대값(사용자 결정 때문)**: `check_fal_parity_rollback`(rw-c). 렌더 `VPOC_ARK_SEEDANCE_ENABLED`가 prod `"true"`, dev `"false"`다(전에는 둘 다 `"false"`). "+true" 되돌리기 경로는 그대로다. 운영 렌더 `VNEW_ENABLED`가 env·runtime-config 모두 `"true"`인지도 본다. 코드보다 먼저 고친 검사가 운영 오버레이를 바꾸기 전에 실패하는 것을 확인했다. 다른 기존 검사의 기대값은 바꾸지 않았다.
- **기존 검사 고정**: T3에서 새로 고정한 검사는 없다. T1(`_run_char_fidelity_off` 3줄)·T2b(`_run_3d_hunyuan` 2줄) 고정은 그대로다. 운영 오버레이를 바꾼 뒤에도 다른 렌더 검사(lt-i·sr-l·fx-m·cast-m·tr-b)는 그대로 통과했다.
- **결정(design·지시서와 다르거나 정하지 않은 점)**
  - E1 — 3D 이름표는 `model3d.endpoint`만 쓴다. Hunyuan 요청은 endpoint를 저장하지 않으므로(T2b E1) 이름표가 없다. `requestArn`에서 뽑으면 옛 데이터의 상세 JSON이 바뀌어 cf-r 고정과 "옛 데이터는 지금 화면 그대로"가 깨진다. 고정 표에는 `fal-ai/hunyuan3d-v3/image-to-3d` → "Hunyuan3D v3"도 넣어 두었다(endpoint가 저장될 때를 위해).
  - E2 — 이름표 표·부위·좌우 표는 `charTableValue`(표 자신의 키만)로 읽는다. `constructor`·`__proto__` 같은 이름이 함수 글을 화면에 내지 않게 한다.
  - E3 — `analysisFidelityLines`는 이름(labelKo·itemEn)이 비면 그 소품을 빼고, 부위가 비면 끝 공백을 지운다. 표에 없는 부위는 원문 그대로다(design `CHAR_PART_KO[partEn] || partEn`).
  - E4 — 이름표·`leftUrl`·`rightUrl`은 설정이 아니라 저장값으로 보인다(R4.9, design §14 "이미 저장한 옆모습은 화면에만 보인다"). `features`가 바꾸는 것은 분석 카드 새 줄·"사진 속 옷" 배지·안내 문장뿐이다.
  - E5 — 이름표는 `viewer3d`가 아니라 `variantColumn`이 3D 보기 바로 뒤에 단다(`viewer3d`를 바꾸지 않는다).
  - E6 — cf-s는 design보다 넓다. 실제 `detail_view` 응답(옛·새 캐릭터 × 켬·끔)을 화면에 넣고, 옛 캐릭터 + 끔 화면 DOM sha256을 구현 전 값과 비교하고, DOM 자리가 순수 함수를 부르는지 함수를 바꿔 끼워 센다.
  - E7 — cf-r에 라우트 통합을 더했다. 켬 + Tripo 가짜로 실제 빌드한 뒤 `GET /api/vnew/characters/<id>`(Flask 블루프린트)가 두 그림체의 `leftUrl`·`rightUrl`·`endpoint`·`features`를 주고, 다른 사용자는 404다.
  - E8 — design §18은 `deploy/config.{dev,prod}.env`를 바꾸지 않는다고 적었다. 2026-10-30 사용자 결정(지시서 101 B)에 따라 prod만 두 줄 바꿨다.
- **확인**
  - `node --check frontend/public/vnew.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 61줄(T2b 60줄 + cf-s 1줄), FAIL·WARN 0(약 92초).
  - cf-r: 옛 캐릭터 + 두 설정 끔 상세 JSON sha256 = 구현 전(`d4f1a977…`). 켬 3조합은 맨 뒤에 `features`만 더해지고 나머지 sha256이 같다.
  - cf-s: 옛 캐릭터 + 끔 화면 DOM sha256 = 구현 전(`a8ec4eff…`). 켬은 추가 복장 안내 문장만 다르다.
  - 바뀐 줄 커버리지(`coverage run --data-file=%TEMP%\… --source=app/src tools/check_vpoc.py`, coverage 7.17, 시작 전 사본과 difflib)

    | 기준 | 파일 | 바뀐 실행문 | 덮음 |
    |---|---|---|---|
    | T1~T3·T2b 누적(T1 시작 전 사본) | `characters.py` | 696 | 696 |
    | | `settings.py` | 72 | 72 |
    | | `char_fidelity.py`(T1 신규, 전부) | 363 | 363 |
    | | 합계 | 1,131 | 1,131 (100%) |
    | T3 만(T3 시작 전 사본) | `characters.py` | 11 | 11 (100%) |

  - JS는 cf-s가 덮는 함수 목록으로 갈음한다: T3에서 새로 만들거나 바꾼 JS 17개(상수 6 `CHAR_PART_KO`·`CHAR_SIDE_KO`·`CHAR_3D_ENDPOINT_LABEL`·`CHAR_VIEW_PAIRS`·`OUTFITS_HINT`·`OUTFITS_HINT_FIDELITY`, 순수 함수 7 `charTableValue`·`charFidelityOn`·`analysisFidelityLines`·`variantViewPairs`·`outfitBadgeText`·`outfitsHintText`·`model3dEndpointLabel`, DOM 함수 4 `renderDetail`·`variantColumn`·`outfitsSection`·`outfitCard`)를 cf-s가 node로 모두 실행한다.
  - 변형 검사 17개(`vnew.js`·`vnew.css`는 임시 폴더 사본, `characters.py`는 메모리에서만 바꿔 cf-r·cf-s 실행): 좌우 순서·배지 설정 무시·기준 번호 0부터·표 밖 이름 허용·안내 고정·이름표 늘 붙임·이름표 innerHTML·features 없이 줄·지운 그림 기준·그림 클릭 없음·flex-wrap 없음·이름표 클래스 없음·features 늘 둠·빈 키 leftUrl·endpoint 문자열 검사 없음·kind 안 줌·features 값 뒤바뀜. 17개 모두 잡았다.
  - 렌더 값(로컬 `render.py`, 메모리에서만): prod `VNEW_ENABLED="true"`·runtime-config `vnewEnabled: true`·`VPOC_ARK_SEEDANCE_ENABLED="true"`·`VPOC_RAW_ERRORS="false"`·`VPOC_POLL_GIVEUP_S="1800"`·`STORY_DEV_ENABLED="true"`·`APP_MEM_LIMIT=8Gi`, dev `VPOC_ARK_SEEDANCE_ENABLED="false"`.
  - 실제 외부 호출 0회: cf-r은 기존 cf 그룹의 막기(LLM·Seedream·fal·urlopen) 안에서 돌고, cf-s는 상세 응답을 `_guard_real_llm` 안 메모리 저장소로 만든다(LLM 0회 확인).
  - 줄 끝 LF(바뀐 10개 파일 CR 0), `config.prod.env` BOM 그대로, 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 함수·클래스·JS 함수·CSS 선택자 0, 저장소 안 `.coverage` 등 부산물 0. 수정 금지 파일(`vnew/generate.py`·`plan.py`·`casting.py`·`routes.py`·`vpoc/*`·`config.dev.env`·`.gitlab-ci.yml`·`k8s/*`)은 바뀌지 않았다.
- **3단계 리뷰**
  - 보안
    - 화면: 새 글은 모두 `el()`(`textContent`)로 넣는다. 그림 `src`는 응답의 presigned URL뿐이다. 모델이 쓴 소품 이름(서버에서 `clean_line` 12·80자)도 글자 그대로 보인다(cf-s 위험한 글 경우). 표는 자신의 키만 읽는다(E2).
    - 응답: `leftUrl`·`rightUrl`은 서버가 쓴 키의 1시간 presigned URL이다. `endpoint`는 정규식으로 검사한 설정에서 나온 저장값이고 문자열일 때만 준다. 사진 속 옷의 `sourceFileId`·`whereEn`·`sidedItems`는 응답에 넣지 않는다(cf-r). 소유자 규칙은 그대로다(다른 사용자 404, cf-r).
    - 운영 첫 켬: `/api/vnew` 라우트 50개가 모두 `@deps.require_auth` 뒤에 있다(routes.py를 읽어 확인). 새 IAM·Secret이 필요 없다(Claude 사전 확인: S3 `users/*`, fal·ARK 키, Bedrock 중개 역할). 새 문서·설정 줄에 계정 ID·비밀값이 없다(12자리 숫자 검사 0건).
    - 발견·조치: 표 조회가 `constructor`로 함수 글을 낼 수 있는 점을 설계 중 찾아 `charTableValue`로 막았다(변형 검사 "표 밖 이름 허용"이 잡는다).
  - 성능
    - 512Mi(개발): 상세 조회마다 presign이 variant당 최대 2번 늘고(boto3 로컬 서명, 네트워크 없음), 응답이 URL 두 개만큼(약 1KB) 커진다. 화면 함수는 상세 한 번에 목록 몇 개를 돈다. 메모리·외부 호출 수·타임아웃 변화는 없다.
    - 운영 첫 켬: 운영 앱 한도는 8Gi다. 첫 화면이 `vnew-previz.js`·three.js(약 735KB, 압축 전)를 비동기로 받는다. `app.js`의 동적 import라 첫 화면 그리기를 막지 않고, 배포 태그가 같으면 30일 immutable 캐시를 쓴다.
    - 발견·조치: 조치할 것 없음(three.js 다운로드는 계획서 6절·R-21에 영향으로 적었다).
  - 운영성
    - 설정 되돌리기: `VNEW_CHAR_FIDELITY=false`·`VNEW_3D_SIDE_VIEWS=false`면 `features`가 빠지고 화면은 지금 문장·배지다. 저장된 옆모습·이름표는 저장값이라 계속 보인다(E4). `VNEW_3D_PROVIDER=hunyuan`이면 다음 새 요청부터 endpoint를 지우므로 이름표가 없다. 운영 vnew는 `VNEW_ENABLED=false` 한 줄 + 운영 job으로 끈다(저장 데이터는 남는다).
    - 진행 중 빌드: 옆모습을 하나 끝낼 때마다 키가 저장되므로 폴링(5초) 중에 작은 그림이 하나씩 늘어난다. 3D 제출 때 endpoint가 저장되므로 "3D 모델을 준비하고 있습니다…" 아래에 이름표가 먼저 보인다. 상세 조회의 `reconcile_stale`(이어 만들기 시작)은 바꾸지 않았다.
    - 재시작 뒤 이어 만들기: T3는 상세 응답·화면만 바꿔 이어 만들기 경로가 같다(cf-p·tr-j 그대로 통과).
    - 섞인 버전(롤링 배포 중): 새 화면 + 옛 API는 `features` 등이 없어 지금 화면이고, 옛 화면 + 새 API는 새 키를 읽지 않는다. 오류가 없다.
    - 운영 첫 켬: 운영 사용자 전체의 메뉴가 바뀐다("영상" → "영상만들기_기존", "영상만들기_신규"·"캐릭터 만들기" 추가). **"스토리" 만들기 버튼이 숨는다**(`applyVnewToggles`, 운영 `STORY_DEV_ENABLED="true"` 렌더 확인). 기존 스토리 프로젝트는 그대로 열린다. 새 유료 기능(캐릭터 1명 약 $1.2, Tripo 3D $0.60 × 2 포함)이 비용 상한 없이 열린다. 운영은 지금까지 `/api/vnew`가 없었으므로 이어 갈 vnew 작업이 없다(추정).
    - 발견·조치: `config.prod.env`의 VNEW 주석 3줄("운영은 꺼진 채로 둔다")이 새 값과 맞지 않는다. 지시("다른 줄은 건드리지 않는다")대로 두고 남은 이슈 1과 계획서 6절에 적었다. "스토리" 버튼 숨김은 지시서에 없던 영향이라 계획서·R-21에 적었다.
- **남은 이슈**
  1. `config.prod.env`의 VNEW 주석 3줄이 `VNEW_ENABLED=true`와 맞지 않는다. 커밋 때 고칠지 Claude가 정한다.
  2. 운영에서 vnew를 켜면 "스토리" 만들기 버튼이 숨는다(기존 vnew 토글 동작). 운영 사용자가 스토리 새 프로젝트를 만들 길이 없어진다. 의도와 다르면 배포 전에 정해야 한다.
  3. Hunyuan 이름표("3D: Hunyuan3D v3")는 지금 데이터로는 보이지 않는다(E1). 필요하면 (가) Hunyuan 요청에도 endpoint 저장(T1 cf-b `buildKeys` 고정 갱신 필요), (나) 상세에서 `requestArn`으로 뽑기(cf-r 고정·옛 화면 변경) 중 정한다.
  4. `VNEW_3D_TRIPO_ENDPOINT`를 표에 없는 Tripo endpoint로 바꾸면 이름표 표에 한 줄을 더하기 전까지 이름표가 없다.
  5. `bytedance/seedance-2.5/draft/complete`는 코드에 이름이 없다. 등록 대상은 Claude 확인(통 테이크 초안 경로·개발 레지스트리에 있음)을 따랐다.
  6. `deploy/config.env.example`의 단가 주석 `VNEW_3D_USD=0.375`(T2·T2b 남은 이슈)는 이번 범위(허용 파일) 밖이라 그대로다.
  7. design §18의 "바꾸지 않는 파일: `deploy/config.{dev,prod}.env`" 문장은 고치지 않았다(이번 허용 범위는 tasks.md T3 체크뿐).
  8. 개발 유료 확인 VD-1~VD-4(tasks 「배포 뒤 확인」)와 운영 유료 확인은 승인 뒤 Claude가 한다.


### 지시서 102 — 추가 복장 [이미지 만들기]가 늘 꺼지는 결함, 운영 "스토리" 버튼 유지 설정 `VNEW_HIDE_STORY` (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/102-outfit-button-story-flag.md`. 작은 작업이라 스펙 폴더 없이 지시서와 이 항목을 스펙으로 삼았다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터를 읽지 않았다.
- **먼저 한 일**: 바꿀 파일 8개의 시작 전 사본을 저장소 밖(`%TEMP%`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 61줄, 78초).
- **1. 추가 복장 버튼 결함**
  - 사용자 보고(2026-10-30, 개발계 `char_20261030_a7b69d`): 추가 복장 후보 카드의 [이미지 만들기]가 꺼져 있다.
  - 원인(Claude 확인, 코드로 다시 확인): 아직 만들지 않은 복장 variant는 `characters._empty_outfit_variant()`가 `status: "queued"`로 저장하고, `_outfits_view`도 빈 상태를 `"queued"`로 준다. `vnew.js` `outfitCard`는 `images`이거나 `queued`이면 `anyBuilding = true`로 보고 버튼을 막았다. 서버 `start_build_outfit`은 `images`일 때만 409다. 그래서 한 번도 만들지 않은 복장은 늘 막혔다(049a78b, 지시서 034부터). 같은 판단을 쓰는 [삭제]도 함께 막혀 있었다.
  - 결정: 카드의 "만드는 중" 판단을 서버와 같게 `images`일 때만으로 바꿨다(`anyBuilding`, 결과 칸의 "만드는 중…" 분기 두 줄). 서버 데이터 모양은 바꾸지 않았다.
  - 검토한 대안
    - (가) 서버가 빈 복장을 `queued`가 아닌 값(예: `draft`)으로 저장: 이미 저장된 복장(개발 6벌)이 `queued`라 옮기기가 필요하고, 서버는 이번 수정 금지 범위다.
    - (나) 화면이 `queued`에 진행 흔적(시각 등)을 더해 판단: 복장 variant에는 그런 필드가 없다.
    - (다) 채택: 서버의 409 기준(`images`)을 화면에 그대로 쓴다.
- **2. 운영 "스토리" 버튼 유지**
  - 배경: `applyVnewToggles`는 vnew가 켜지면 만들기·트랙 고르기 창의 story 버튼을 숨긴다(설계자 2026-10-20). 운영에서 vnew를 켜면(2026-10-30 사용자 결정) 새 스토리 프로젝트를 만들 길이 없어진다. 사용자에게 물었지만 답이 없어, Claude가 기존 기능을 잃지 않는 쪽으로 정했다. 개발은 지금처럼 숨긴다.
  - 결정: 화면 전용 설정 `VNEW_HIDE_STORY`(base 기본 `true` = 지금 동작). 운영 오버레이만 `false`. 앱 env에는 넣지 않았다.
  - 서버 확인: `project_tracks.track_enabled`는 story를 `STORY_DEV_ENABLED`로만 판단한다(vnew와 무관). 운영 렌더 `STORY_DEV_ENABLED="true"`라 버튼을 보이면 바로 만들 수 있다.
  - E1 — runtime-config에서 `vnewHideStory`를 `vnewEnabled` **앞**에 두었다. 기존 rw-c가 운영 렌더에서 `"vnewEnabled: true }"`(맨 끝)를 찾기 때문이다. 뒤에 두면 rw-c 기대값을 바꿔야 한다(지시서: 다른 기존 검사의 기대값은 바꾸지 않는다). `runtime-track-flags` 주석은 끝에 `;vnewHideStory=…`를 붙였다. runtime-config.js는 subPath로 붙어 ConfigMap만 바뀌면 파드가 새 값을 읽지 못하므로, 이 주석 값이 바뀌어 프런트 파드를 다시 띄운다.
  - E2 — app.js는 지시서 그대로 `vnewHideStory !== false`로 읽는다. 값이 없거나(옛 runtime-config, 이미지 안의 기본 `frontend/public/runtime-config.js`) 글자 `"false"`이면 숨긴다(지금 동작). 템플릿은 따옴표 없는 `false`로 렌더한다(ob-d·ob-e).
  - E3 — story 숨김 한 줄을 `if (VNEW_HIDE_STORY) …;` 한 줄로 바꿨다(같은 함수의 `if (strong) …;`와 같은 꼴). 블록으로 감싸면 원래 줄이 들여쓰기만 바뀐다.
  - 검토한 대안
    - (가) 운영도 숨김 유지: 운영 사용자가 새 스토리 프로젝트를 만들 수 없다.
    - (나) story 숨김을 지운다: 개발 동작(설계자 결정)이 바뀐다.
    - (다) `STORY_DEV_ENABLED`와 묶는다: 그 값은 서버 트랙 허용과 영상 쪽 판단도 함께 바꾼다.
    - (라) 채택: 화면 전용 설정 하나.
- **바꾼 것**
  - `frontend/public/vnew.js` `outfitCard`: `anyBuilding` 판단·결과 칸 "만드는 중…" 분기(`queued` 제외), 설명 주석 1줄.
  - `frontend/public/app.js`: 상수 `VNEW_HIDE_STORY`(설명 2줄), `applyVnewToggles`의 story 숨김을 `VNEW_HIDE_STORY`일 때만. 위 설명 주석에 한 구절.
  - `k8s/workloads.yaml.tmpl`: runtime-config `vnewHideStory: ${VNEW_HIDE_STORY}`, `runtime-track-flags` 끝 `;vnewHideStory=${VNEW_HIDE_STORY}`.
  - `deploy/config.env.example`: `VNEW_ENABLED` 뒤에 주석 2줄 + `VNEW_HIDE_STORY=true`.
  - `deploy/config.prod.env`: 끝에 주석 1줄 + `VNEW_HIDE_STORY=false`. BOM·LF 그대로, 다른 줄 그대로.
  - `tools/check_vpoc.py`: 새 `check_vnew_outfit_button`(ob-a~ob-e)과 `_OB_TITLE_NO_BASE`·`_OB_FIXED_LINES`·`_OB_CARD_NODE_CASES`·`_OB_TOGGLE_NODE`·`_ObIdleThread`·`_ob_card_script`·`_ob_server_details`. `main()` 끝에서 부른다. cf-s 하네스(`_CAST_SCREEN_NODE_HARNESS`·`_CF_SCREEN_NODE_STUBS`·`_CF_SCREEN_SLICES`·`_cf_run_node`)와 cf 메모리 저장소(`_CfFakes`·`_cf_old_char`)를 그대로 쓴다.
  - `docs/plans/2026-10-30-prod-vnew-enable.md`: §1(설정 한 줄·표), §2(렌더 확인), §4(무료 확인에 "스토리" 버튼), §5(되돌리기 줄), §6(메뉴 영향).
- **검사(새 그룹 `check_vnew_outfit_button`)**

  | 검사 | 덮는 것 |
  |---|---|
  | ob-a | `outfitCard`를 node 가짜 DOM으로 10상태 실행: queued 켬 "이미지 만들기", images 끔, ready+images 끔·그 칸 "만드는 중…", ready "다시 만들기" 켬, ready+queued 켬·그 칸 비어 있음, version 0 끔·title 안내(queued·ready 둘 다), failed 켬, ready+failed 오류 문구, variants 없음 켬. [삭제]도 같은 판단. 누르기: 확인 → `POST /api/vnew/characters/ch%201/outfits/o%201/build` → 다시 읽기, 취소 → 호출 0, 거절 → 버튼 다시 켬·오류 문구. innerHTML 0 |
  | ob-b | 서버 → 화면: `sync_auto_outfits`가 만든 후보(두 그림체 queued, `_empty_outfit_variant` 모양) → `GET` 상세(Flask 블루프린트, 실제 `detail_view`) → 카드 켬 → `POST` 202 → 저장값 images → 카드 끔·`POST` 409 → `_set_outfit_variant`로 ready+failed → "다시 만들기" 켬·`POST` 202, 다른 queued 후보 202. 네 경우 모두 "카드 끔 = 서버 409". 그림·3D·원장·LLM 0회 |
  | ob-c | `outfitCard`에 `"queued"` 비교 0, 고친 줄 한 번씩. 변형: 옛 판단(`images || queued`)으로 되돌리면 queued 카드가 막히고 "만드는 중…"이 생긴다(검사가 결함을 잡는다). `node --check` vnew.js·app.js |
  | ob-d | `render.py`로 렌더만(파일 쓰지 않음): base `VNEW_HIDE_STORY=true` 한 줄, 템플릿 자리 2개(runtime-config 1·주석 1), 앱 env 없음, prod runtime-config `vnewHideStory: false, vnewEnabled: true }`·주석 `vnewHideStory=false`, dev `true`, prod+`true` 되돌리기 `true`, dev 오버레이에 키 없음, `config.prod.env` BOM·CR 0·끝 두 줄 |
  | ob-e | app.js `VNEW_ENABLED`·`VNEW_HIDE_STORY` 선언과 `applyVnewToggles`를 node vm으로 실행(경우마다 새 문맥): 렌더된 prod runtime-config → story 보임, 렌더된 dev → 숨김, 플래그 `true`·없음·글자 `"false"` → 숨김, `false` → 보임. 켬 경우 모두 vnew 버튼 보임·video 제목 "영상만들기_기존"·메뉴 보임이 같다. vnew 끔·runtime 없음 → 아무것도 바꾸지 않음. 모르는 선택자 0 |

- **확인**
  - `node --check frontend/public/vnew.js`·`app.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 62줄(시작 전 61줄 + ob 1줄), FAIL·WARN 0(약 74초).
  - 바뀐 기존 기대값 없음. cf-s의 옛 화면 DOM sha256은 구현 전 값(`a8ec4eff…`) 그대로다. 그 옛 캐릭터의 추가 복장은 ready뿐이라 이번 판단 변경이 닿지 않는다. rw-c도 기대값 그대로 통과한다(E1).
  - 변형 검사 15개(저장소 밖 임시 사본으로 `check_vnew_outfit_button` 실행): 옛 vnew.js·옛 app.js·옛 템플릿·옛 base·옛 prod 오버레이, story 늘 숨김, `=== true`로 읽기, 결과 칸만 옛 판단, 버튼만 옛 판단, images도 안 막음, 주석 자리 빠짐, 앱 env에도 넣음, prod `true`, dev 오버레이에 키, base `false`. 15개 모두 잡았고 지금 코드는 통과한다.
  - JS 실행 범위(node `NODE_V8_COVERAGE` 블록 커버리지, ob-a·ob-e 스크립트): `applyVnewToggles` 실행 안 된 블록 0. `outfitCard`는 바뀐 두 조건의 참·거짓이 모두 실행되고, 실행 안 된 블록 8개는 모두 이번에 바꾸지 않은 곳이다(이름·오류 문구 기본값, 빈 URL 건너뛰기, 그림 크게 보기, confirm 예외, 삭제 누르기).
  - Python 쪽 바뀐 앱 코드는 없다(`app/src` 변경 0). 커버리지 대상은 검사 코드뿐이다.
  - 줄 끝 LF(바뀐 8개 파일 CR 0), `config.prod.env` BOM 그대로, 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 정의 0, 저장소 안 부산물 0. 수정 금지 파일(`app/src/*`·`config.dev.env`·`.gitlab-ci.yml`·`whynot-20261029`)은 바뀌지 않았다.
- **3단계 리뷰**
  - 보안: 새 입력·라우트·비밀값 없음. 버튼이 켜져도 서버가 소유자(`users/{owner}/…`)·기본 그림 유무(400)·진행 중(409)을 그대로 본다(ob-b). 설정값은 템플릿이 따옴표 없는 JS 글자로 넣는다. 오버레이 값은 운영자가 쓰는 파일이고 `true`/`false` 밖의 값을 넣으면 runtime-config가 깨지는 위험은 기존 `vnewEnabled`와 같다. 새 줄에 계정 ID·비밀값 없음. 발견·조치: 없음.
  - 성능: 화면 판단 두 줄과 상수 하나. 메모리·외부 호출·타임아웃 변화 없음. 버튼이 켜져 복장 만들기 요청(Seedream 4장, 장당 $0.04)이 실제로 가능해진다. 원래 의도된 기능이고 확인 창에 비용 문구가 있다. 발견·조치: 없음.
  - 운영성
    - 되돌리기: 화면 수정은 이미지 되돌리기로만 되돌린다(설정 없음). 서버가 원래 받던 요청이라 따로 둘 필요가 없다고 봤다. 스토리 버튼은 `config.prod.env`의 `VNEW_HIDE_STORY=false`를 `true`로(또는 줄 삭제) + 운영 job.
    - 롤아웃: 이 커밋 배포 때 `runtime-track-flags` 값이 바뀌어 개발·운영 프런트 파드가 한 번 다시 뜬다(이미지 태그도 바뀌므로 어차피 다시 뜬다). 앱 파드 env는 그대로다.
    - 섞인 버전: 새 app.js + 옛 runtime-config(값 없음)는 숨김(지금 동작). 옛 app.js + 새 runtime-config는 새 키를 읽지 않는다. 오류 없음.
    - 진행 중 작업: 서버 데이터·상태 전이는 그대로다. 만들던 복장(images)은 지금처럼 막혀 있다.
    - 발견·조치: 복장 진행 표시·폴링·멈춘 복장(남은 이슈 1·2)은 이번 범위 밖이라 고치지 않고 적었다.
- **남은 이슈**
  1. 상세 폴링(`schedulePoll`)은 `detail.status === "building"`(기본 variant 기준)과 캐스팅 뽑기만 본다. 추가 복장을 누르면 한 번 다시 읽고 끝나므로, 다 그려진 뒤에도 다시 들어오기 전까지 카드가 꺼진 채다. 두 그림체가 모두 images면 결과 칸이 없어 "만드는 중…"도 보이지 않는다(버튼만 꺼진다). 고치려면 `schedulePoll`과 `outfitCard` 표시를 바꿔야 한다.
  2. 복장 variant에는 heartbeat·이어 만들기가 없다. 앱이 그리는 도중 재시작되면 그 복장은 images로 남아 서버가 계속 409를 내고 화면도 계속 막힌다(지금도 같다). 서버 범위다.
  3. 다시 만들기를 누르면 서버가 두 그림체를 모두 images로 바꿔 이전 그림이 카드에서 사라지고 버튼 글자가 "이미지 만들기"로 돌아간다(지금 동작).
  4. 이제 queued 후보의 [삭제]도 켜진다(같은 판단). 지운 auto 후보는 캐릭터를 다시 만들 때 `sync_auto_outfits`가 다시 넣는다(지금 동작).
  5. `docs/ANALYSIS.md`의 runtime-config 키 목록(§5.2)과 설정 표에 `VNEW_HIDE_STORY`가 없다. 이번 허용 파일 밖이라 그대로 두었다.
  6. 이미지 안의 기본 `frontend/public/runtime-config.js`에는 `vnewHideStory`가 없다(값 없음 = 숨김). 배포는 ConfigMap이 덮으므로 영향이 없다.
- **피드백(같은 날, Claude): 남은 이슈 1·2를 이번에 고친다.** 사용자가 버튼이 켜지면 바로 복장을 만들어 볼 것이라 진행 표시와 멈춘 복장을 함께 고쳤다. 같은 작업 트리에서 이어서 했고, 시작 전 사본(8개 파일)을 `%TEMP%`에 두었다.
  - 원인
    - 이슈 1: `schedulePoll`은 기본 variant(`detail.status === "building"`)와 캐스팅 뽑기만 본다. 복장을 누르면 한 번 다시 읽고 끝나, 다 그려도 다시 들어오기 전까지 카드가 꺼진 채였다. 결과 칸은 ready가 있을 때만 그려서 두 그림체가 모두 images면 "만드는 중…"도 없었고, 두 그림체가 모두 failed면 오류 문구도 없었다.
    - 이슈 2: 복장 variant에는 시작 시각·heartbeat가 없다. 그리는 도중 앱이 재시작되면 images가 남아 서버가 계속 409를 냈다.
  - 결정
    - 화면(`vnew.js`): `schedulePoll`이 새 `outfitsBuilding(detail)`(어느 복장이든 images)도 보고 계속 다시 읽는다. 간격은 지금과 같은 `POLL_MS`(5초)다. 기본 building·캐스팅 drawing 판단은 그대로다. `outfitCard`는 결과 칸을 ready **또는 failed**가 있을 때 그리고(실패한 그림체는 오류 문구), 결과 칸이 없고 images면 버튼 줄 바로 위에 `p.vnew-hint.vnew-outfit-building` "만드는 중…" 한 줄을 둔다.
    - 서버(`characters.py`): `start_build_outfit`이 images로 바꿀 때 두 그림체에 같은 `startedAt`(now_iso 한 번)을 적는다. 새 `_outfit_variant_stalled(char, ov)`가 images이면서 `startedAt`이 `OUTFIT_STALL_S`보다 오래됐는지 본다. `startedAt`이 없거나 읽을 수 없으면 char `updatedAt`을 대신 보고, 그것도 없으면 멈춤이 아니다(409 유지). 멈춘 것만 409 없이 다시 시작하고 경고 1줄 `vnew.outfit.stale_restart char=… outfit=… variants=webtoon,real`을 남긴다(mutator가 멈춘 그림체 목록을 돌려주고, 저장이 끝난 뒤 한 번 찍는다). `_outfits_view`는 멈춘 images를 `failed` + "만들기가 중단됐습니다. 다시 만들어 주세요."로 보인다(저장값은 그대로, 읽기 전용).
    - 설정: `VNEW_OUTFIT_STALL_S`(기본 1200초). `settings.OUTFIT_STALL_S = max(60, _int(...))`, base·템플릿 앱 env(`VNEW_3D_SIDE_SWAP` 바로 뒤). 0·음수면 모든 그리기가 곧바로 멈춘 것으로 보여 이중 빌드(이중 과금)가 되므로 60초 아래로 내리지 않는다.
  - 결정(지시와 다르거나 정하지 않은 점)
    - F1 — `outfitsBuilding`은 `schedulePoll` 바로 뒤(cast-l이 자르는 "schedulePoll ~ backRow" 구역 안)에 두었다. `outfitCard`는 같은 기준(`=== "images"`)을 자기 줄로 계속 쓴다. 한 함수를 두 곳이 부르면 cf-s(추가 복장 구역)나 cast-l(폴링 구역) 중 한쪽 하네스가 그 함수를 갖지 못해, 기존 검사 본문을 고쳐야 한다. 두 기준이 같은지는 ob-f 흐름(실제 응답 → 카드 끔 ↔ 폴링 계속)이 본다.
    - F2 — 결과 칸 조건을 `anyReady`에서 `anyReady || anyFailed`로 넓혔다. "끝나면 결과 또는 오류와 켜진 버튼"(피드백)과 멈춘 복장 문구를 보이려면 두 그림체가 모두 failed일 때도 칸이 있어야 한다.
    - F3 — 로그에 `variants=`를 더했다(한쪽만 멈춘 경우를 구분).
    - F4 — `startedAt`은 응답에 내지 않는다(복장 variant 응답 키 그대로, ob-b).
  - 검토한 대안
    - (가) 복장 그리기에 heartbeat(그림마다 갱신)를 두고 그것으로 멈춤을 판단: 더 정확하지만 `run_build_outfit`의 쓰기가 늘고 지시 범위 밖이다(남은 이슈 1).
    - (나) 상세 조회 때 멈춘 복장을 저장값까지 failed로 바꾸기: 지시가 "저장 데이터는 바꾸지 않는다"였다. 읽기 전용 보기 + 다시 만들기 때만 푸는 쪽이 GET에서 쓰기 경합이 없다.
    - (다) 폴링을 복장만 다시 그리는 부분 갱신으로: 지금 상세 폴링(전체 다시 그리기)과 다른 길이 생긴다. 지시가 "지금 상세 폴링과 같다"였다(남은 이슈 2).
  - 바꾼 것(이번)
    - `frontend/public/vnew.js`: `schedulePoll`(판단 한 줄 + 주석), 새 `outfitsBuilding`, `outfitCard`(`anyFailed`, 결과 칸 조건, "만드는 중…" 한 줄, 주석).
    - `app/src/vnew/characters.py`: 새 `_OUTFIT_STALLED_ERROR`·`_outfit_variant_stalled`, `start_build_outfit`(docstring·mutator·로그), `_outfits_view`(status·error 두 줄). 다른 함수는 바꾸지 않았다.
    - `app/src/vnew/settings.py`: `OUTFIT_STALL_S`(주석 3줄).
    - `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`: `VNEW_OUTFIT_STALL_S` 한 쌍·한 줄(주석 포함).
    - `tools/check_vpoc.py`: ob 그룹에 ob-f·ob-g·ob-h와 ob-a 경우 4개(`imagesQueued`·`imagesFailed`·`stalled`·`failedNoText`)·카드 자식 순서, `_OB_STALLED`·`_OB_STALL_S`·`_OB_POLL_NODE_CASES`·`_OB_POLL_SLICE`·`_ob_poll_script`, `_ob_server_details`(ob-g 경우·흐름), `_CF_OFF_SHA["buildKeys"]`(아래).
    - `docs/ANALYSIS.md`: 머리 갱신 줄, §5.2 runtime-config 키 목록(`vnewHideStory`), §7.1 표(`VNEW_HIDE_STORY`·`VNEW_OUTFIT_STALL_S`)와 설명 한 단락(102 남은 이슈 5도 함께 끝냈다).
  - **바꾼 기존 검사 기대값**: `_CF_OFF_SHA["buildKeys"]`(cf-b) 하나. 가짜 빌드 뒤 저장 키 집합 지문인데, 지시대로 `start_build_outfit`이 복장 variant에 `startedAt`을 적어 키가 하나 늘었다. 저장소 밖 스크립트로 같은 지문을 `startedAt`만 빼고 다시 재면 구현 전 값 `d43c5c32…`와 같았고, 나머지 지문 31개도 그대로였다. 새 값 `619815f5…`로 바꾸고 옛 값을 주석에 남겼다. cf-s 옛 화면 DOM sha256(`a8ec4eff…`)·cast-l·rw-c·tr-b 등 다른 기존 검사는 기대값 그대로 통과한다. 102에서 새로 만든 ob-a의 `images`·`failed` 기대값은 이번 화면 변경(한 줄·오류 칸)에 맞춰 고쳤다(커밋 전 같은 작업).
  - 검사(이번에 더한 것)

    | 검사 | 덮는 것 |
    |---|---|
    | ob-a | 102의 10경우에 더해 images·images+queued → 결과 칸 없이 버튼 줄 바로 위 "만드는 중…"(카드 자식 순서), images+failed → 오류 칸 + "만드는 중…"·버튼 끔, 멈춤(두 그림체 failed + 문구) → 오류 칸·버튼 켬, 오류 문구 없는 failed → "만들기에 실패했습니다." |
    | ob-f | `schedulePoll` 구역을 node로 실행: 복장 images(한 벌·여러 벌 중 하나) → 5000ms 예약, ready+failed·queued·failed·이상한 모양(null·글자·`variants: null`·배열 아님·모르는 그림체) → 예약 없음, 기본 building·캐스팅 drawing 그대로. 실제 `detail_view` 응답 흐름 4개(진행 → 완료, 진행 → 멈춤, 진행 → 한쪽 멈춤, 옛 데이터 진행 → 멈춤)로 타이머를 쏴 그리는 동안 5초마다 다시 읽고 끝나면 멈춘다. 마지막 카드는 결과·오류와 켜진 버튼. 예약 뒤 화면을 떠나면 다시 읽지 않는다 |
    | ob-g | 서버 11경우(`OUTFIT_STALL_S` 1200 고정, 메모리 저장소·Flask 라우트): 최근 409, 경계 안(1170초) 409, 지남(1260초) 202 + 로그, startedAt 없음 + updatedAt 오래됨 202 + 로그, startedAt 없음 + 최근 409, 못 읽는 startedAt + 최근 409·+ 오래됨 202, startedAt 최근이면 updatedAt이 오래돼도 409, 미래 시각 409, 시각 없음 409, 한쪽만 멈춤 202(로그 `variants=real`). 상세는 멈춤만 failed + 문구, GET은 저장을 바꾸지 않음(11경우 모두), 409는 저장 불변, 202는 두 그림체 images + 새 startedAt |
    | ob-b(보강) | POST 뒤 두 그림체 같은 startedAt(지금), 다시 만들기·다른 복장 POST도 새로 적음, 상세 응답 복장 키 그대로(startedAt 없음), 서버 응답 카드의 "만드는 중…" |
    | ob-h | `VNEW_OUTFIT_STALL_S` 읽기(하위 프로세스, 없음 1200·900·0 → 60·-5 → 60·59 → 60·61·글자 → 1200·공백 1800), base 한 줄, 템플릿 앱 env 한 쌍(`VNEW_3D_SIDE_SWAP` 바로 뒤), prod·dev 렌더 `"1200"`(오버레이 없음) |

  - 확인
    - `node --check` vnew.js·app.js 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 62줄(ob는 1줄 그대로), FAIL·WARN 0(약 77초).
    - 바뀐 서버 줄 커버리지(`coverage run --data-file=%TEMP%\… --source=app/src tools/check_vpoc.py`, coverage 7.17, 이번 시작 전 사본과 difflib): 실행문 22줄 중 21줄(95.5%). `characters.py` 20/21, `settings.py` 1/1. 안 덮은 1줄은 `start_build_outfit` mutator의 `return []`(읽은 뒤·쓰기 전에 복장이 지워진 경합 경로, 이전에도 `return`으로 안 덮였다).
    - JS 실행 범위(node `NODE_V8_COVERAGE`, ob-a·ob-f 스크립트): `schedulePoll`·`outfitsBuilding` 실행 안 된 블록 0. `outfitCard`는 실행 안 된 블록 5개가 모두 이번에 바꾸지 않은 곳이다(이름·제목 기본값, 빈 URL 건너뛰기, 그림 크게 보기·삭제 누르기 함수).
    - 변형 검사 19개(저장소 밖 사본 트리, 하위 프로세스에서 `check_vnew_outfit_button` 실행): 폴링이 복장을 안 봄·queued도 만드는 중·늘 false, "만드는 중…" 한 줄 없음, 결과 칸 ready만, startedAt 안 적음, updatedAt만 봄, 시각 없음 = 멈춤, startedAt 없으면 늘 멈춤, 못 읽는 startedAt 되짚기 없음, 상세가 멈춤을 안 보임, 로그 없음, 409 없음, 멈춤이어도 409, STALL 무시, 설정 하한 없음, 기본 600, 템플릿 env 없음, base 600. 19개 모두 잡았고 지금 코드는 통과한다.
    - 실제 외부 호출 0회: ob-g·ob-b는 `_CfFakes`(LLM·Seedream·fal·원장 가짜) + `_guard_real_llm` 안에서 돌고, 스레드는 시작하지 않는다(`_ObIdleThread`). LLM·Seedream·fal·원장 0회를 본다.
    - 줄 끝 LF(이번에 바뀐 8개 파일 CR 0), 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 정의 0, 저장소 안 부산물(`.coverage` 등) 0. 수정 금지 파일은 바뀌지 않았다.
  - 3단계 리뷰
    - 보안: `startedAt`은 서버가 적고 응답에 내지 않는다. 클라이언트가 멈춤을 만들 수 없다(STALL 안에서는 409). 멈춤 문구는 상수다. 로그는 캐릭터·복장 id와 그림체 이름뿐이다. 라우트·권한(소유자 접두사, `require_auth`)은 그대로다. 설정 하한 60초로 0·음수 설정이 이중 빌드(이중 과금)를 부르지 않게 했다. 발견·조치: 하한(위).
    - 성능
      - 서버(512Mi): 판단은 images 그림체마다 시각 두 개 읽기다. 메모리·외부 호출 변화 없음. 폴링으로 상세 GET이 그리는 동안 5초마다 1번 늘어난다(지시가 말한 정상 상한 10분이면 약 120번, 멈춤이면 20분 동안 약 240번 뒤 멈춘다). GET 한 번은 지금처럼 char.json 읽기 + presign(로컬 서명)이다. GET은 저장하지 않는다(ob-g).
      - 화면: 폴링마다 상세 전체를 다시 그린다(기본 빌드·캐스팅 폴링과 같다). 3D 보기는 캐시한 blob URL로 다시 만들어 GLB를 다시 받지 않는다.
      - 발견·조치: 다시 그리기가 3D 카메라 각도와 열어 둔 [추가 복장 넣기] 입력을 지우는 문제를 찾았다. 기존 폴링과 같은 동작이고 지시가 "지금 상세 폴링과 같다"여서 고치지 않고 남은 이슈 2에 적었다.
    - 운영성
      - 로그: 다시 시작할 때 경고 `vnew.outfit.stale_restart`. 409는 지금처럼 `vpoc.error … AlreadyBuilding`.
      - 되돌리기: `config.<target>.env`에 `VNEW_OUTFIT_STALL_S=<초>` 한 줄. 멈춤 판단을 사실상 끄려면 큰 값(예: 86400). 화면·서버 코드는 이미지 되돌리기로 되돌린다.
      - 진행 중 작업·배포: 배포 전에 시작한 복장 그리기는 startedAt이 없다. 파드 교체로 스레드가 죽으면 마지막 char.json 쓰기(updatedAt)부터 20분 뒤 상세에 "중단"으로 보이고 다시 만들 수 있다. 지금까지는 영원히 막혔다.
      - 섞인 버전(롤링 중): 옛 파드는 startedAt을 무시하고 images면 409(지금 동작), 새 파드는 위 규칙. 옛 화면은 새 응답을 지금처럼 그린다(폴링만 없다). 오류 없음.
      - 재시작 뒤 이어하기: 복장은 자동으로 이어 그리지 않는다. 사용자가 [이미지 만들기]를 다시 누른다(지시 범위).
      - 발견·조치: 로그를 mutator 안에서 찍으면 조건부 쓰기 재시도·뒤 그림체 409 때 거짓 로그가 생겨, 저장이 끝난 뒤 한 번 찍게 했다.
  - 남은 이슈(이번)
    1. 멈춤 판단이 시작 시각 기준이라, 실제로 20분 넘게 걸리는 정상 빌드(Seedream 응답 지연·재시도: 한 장 `urlopen` 300초 × 최대 몇 번)에 다시 만들기를 누르면 두 번째 스레드가 돈다. 비용이 두 번 들고, 늦게 끝난 첫 스레드가 상태를 덮을 수 있다. 막으려면 그림마다 heartbeat를 갱신하거나 실행 토큰(claim)을 두어야 한다(서버 `run_build_outfit` 변경).
    2. 폴링이 상세 전체를 다시 그려 3D 카메라 각도가 5초마다 처음으로 돌아가고, 열어 둔 [추가 복장 넣기] 입력이 지워진다(기본 빌드·캐스팅 폴링과 같은 기존 동작). 고치려면 입력을 `state`에 보존하거나, 상태 서명이 같으면 다시 그리지 않게 해야 한다(⑤ `genViewSig`와 같은 방식).
    3. 102 남은 이슈 3·4(다시 만들기 때 이전 그림이 사라짐, 지운 auto 후보가 다시 생김)는 그대로다.
    4. 멈춘 복장은 자동으로 이어 그리지 않는다. 사용자가 다시 눌러야 한다.
    5. 이미지 안의 기본 `frontend/public/runtime-config.js`에는 `vnewHideStory`가 없다(102 남은 이슈 6, 영향 없음).



## 2026-10-31

### 지시서 103 — 실사 캐스팅 외모 문장에 BL 역할·유형 참고 설명 (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/103-bl-look-hints.md`. 스펙 `.kiro/specs/bl-look/`(requirements·design·tasks)을 먼저 쓰고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터를 읽지 않았다. 옛 저장소(`C:\dev\ai\AI-POC-whynot`, BL 모듈·steering)는 읽기만 했다.
- **먼저 한 일**: 바꿀 파일과 수정 금지 파일 13개의 시작 전 사본을 저장소 밖(`%TEMP%`)에 두었다. 앞 실행(`logs/103.log`)은 읽기 도중 사용 한도로 멈췄고, 그때 사본과 지금 파일이 바이트까지 같아 아무것도 바뀌지 않았음을 확인했다. 시작 전 전체 검사는 전부 통과(ok 62줄, FAIL·WARN 0, 75초).
- **원인·배경**: 사용자 요청(2026-10-31)은 실사 캐릭터 외모 문장에 예전에 학습시킨 BL 장르 지식(공/수, 떡대공·무심공·미인수 같은 유형)을 적용하는 것이다. 단서는 "참고만 하는 것이다. 외모 프롬프트를 고도화하고 정보를 많이 주기 위함이다."다. 지금 캐스팅 문장(`cast_prompt`)은 그림 분석으로 만든 외모 문장(≤300자)만 쓰고, 1500자 상한까지 약 640자가 빈다. 그림이 말해 주지 않는 체격·키 느낌·얼굴 인상·눈빛·자세·몸가짐이 비어 있다.
- **결정**(design.md D1~D12)
  - D1 — 참고 문장은 캐스팅 문장 끝에만 더한다: `… with dark brown eyes. He has A, B, and C.` 300자 외모 문장(`look_sentence`·`lookEn`·사용자가 고친 문장)과 영상 쪽(`_real_appearance`)은 바꾸지 않는다.
  - D2·D3·D4 — 그림 우선: 외모 문장(`prompt_look` 결과)에 같은 분류가 있으면 그 조각을 뺀다. 분류는 낱말 표로 판정한다. 체격·키 조각은 글이 스스로 담은 체격·키도 본다("a lean, tall build"는 그림의 "short stature"와 부딪힌다). 체격·키 낱말 뒤 세 낱말 안에 얼굴·머리·피부·옷 명사가 오면 체격·키가 아니다("thin lips", "a slender angular face", "warm-toned skin", "tall boots", "thin black-rimmed round glasses"). "short"는 키 명사가 뒤따르거나 "is short"일 때만 키다("short black hair"는 아님). 프리셋 문구는 판정에 넣지 않는다(늘 face·expression을 담는다).
  - D5 — 조각마다 관사를 넣어 "He has" 뒤에 바로 읽히게 했다. 지시서 예시의 "tall frame"·"firm square jawline"은 "a tall frame"·"a firm square jawline"이다. 미인수 조각 순서는 체격을 맨 앞에 두었다(D7).
  - D6 — 유형이 있고 성별이 unknown이면 male: 프리셋·주어가 male이고, 외모 문장의 person→man·people→men·their→his·them→him·themselves→himself·theirs→his. `align_gender(male)`은 중립 낱말을 바꾸지 않아 "He is a Korean person in their 30s"가 되기 때문이다. female 지정은 그대로 두고 참고 문장만 "She has …"로 더한다.
  - D7 — 1500자를 넘으면 조각을 끝에서부터 뺀다(표 순서: 체격·키 → 얼굴·눈 → 표정·자세·몸가짐). 지금 길이로는 넘지 않는다(외모 300자 + 가장 긴 참고 문장 199자에서 1,036자).
  - D8 — 화면 미리보기 줄은 서버 `blHintsEn`만 보이고, 화면에서 고른 값이 저장값과 다르면 숨긴다. 분류 판정을 화면에 다시 쓰지 않는다.
  - D9 — 역할만 바꾸면 맞지 않는 저장 유형은 비운다(400 아님). 유형만 보내면 저장된 역할과 비교한다. 역할 `""` + 유형은 400. 뽑기는 BL 형식을 금액 확인보다 먼저 본다.
  - D10 — `VNEW_BL_LOOK` 끔이면 요청의 BL 값을 보지도 저장하지도 않는다(400 없음). 저장값은 지우지 않는다.
  - D11 — 400은 kind 하나 `BadBlType`, 문구 3종(역할·유형·짝)은 고정 문자열이다.
  - D12 — 기존 검사: `check_vnew_casting`은 켬(기본) 그대로 돌린다. cast-a~m 전 그룹이 켬 + 유형 없음에서 지금 기대값 그대로 지나가는 것이 "유형 없음 = 지금과 같다"의 증거다. cast-j의 보기 키 집합에만 BL 5개를 더했다. `check_vnew_char_fidelity`는 부르는 줄에서 `_run_bl_look_off`로 끔 고정했다(cf-b·cf-r의 상세 JSON sha256은 구현 전에 잰 값이다. Claude 결정 9와 같은 방식).
  - 옛 desc는 글자 그대로 옮겼다. 광공 desc의 "폭력적 면모"는 화면 title(한국어 설명)에만 나오고 영어 조각에는 "imposing presence"까지만 쓴다.
- **검토한 대안**
  - (가) 300자 외모 문장에 유형 낱말을 섞기: 300자 줄이기에서 그림 사실(머리·옷)이 먼저 밀려나고 사용자가 고친 문장과 섞인다. 거절.
  - (나) LLM으로 유형과 그림 분석을 합쳐 외모를 다시 쓰기: 비용·지연이 생기고 결정적이지 않으며 그림 사실을 바꿀 수 있다. 거절(규칙은 서버가 결정론적으로).
  - (다) 영상 프롬프트(통 테이크·실사 테이크 외형)에도 더하기: 지시 범위 밖이고 통 테이크 4,000바이트 예산에 닿는다. 하지 않았다.
  - (라) 분류 판정을 명사구 파서로: 오탐은 줄지만 규칙이 커진다. 낱말 표 + 짧은 예외(D4)로 실제 분석 꼴 6개와 오탐 사례 13개를 검사로 고정했다.
  - (마) 채택: 캐스팅 문장 끝 참고 문장 한 개, 그림이 말한 분류는 뺀다.
- **유형 표**(`genre_bl.TYPES`, 영어 조각 전체)

  | 유형(id) | 역할 | desc(화면 title) | 영어 조각(분류) | 이은 길이 |
  |---|---|---|---|---|
  | 광공 / 집착공 (`gwanggong`) | 공 | 집착 강함, 거칠고 폭력적 면모, 큰 신장·체격 | 키 "a tall frame" · 체격 "a broad-shouldered, powerful build" · 얼굴 "sharp, striking features" · 눈 "an intense, unwavering gaze" · 자세 "an upright stance with an imposing presence" | 148자 |
  | 츤데레공 (`tsundere_gong`) | 공 | 겉은 차갑고 속은 다정 | 얼굴 "cool, well-defined features" · 눈 "sharp eyes with a slightly averted gaze" · 표정 "an aloof expression with a hint of softness" · 자세 "a relaxed yet guarded posture" | 144자 |
  | 다정공 (`dajeong_gong`) | 공 | 따뜻하고 배려 | 얼굴 "warm, kind features" · 눈 "soft, attentive eyes" · 표정 "a gentle, warm expression" · 자세 "a relaxed, open posture" · 몸가짐 "neat, clean grooming" | 115자 |
  | 떡대공 (`ttokdae_gong`, 새) | 공 | 큰 키와 넓은 어깨, 건장한 체격의 공 | 체격 "broad shoulders and a solid, muscular build" · 키 "a tall frame" · 얼굴 "a firm square jawline" · 눈 "steady, deep-set eyes" · 자세 "an upright, imposing posture" | 133자 |
  | 무심공 (`musim_gong`, 새) | 공 | 감정 표현이 적고 무심한 태도의 공 | 체격 "a lean, tall build" · 얼굴 "cool, sharply defined features" · 눈 "a half-lidded, indifferent gaze" · 표정 "a calm, unreadable face" · 몸가짐 "neat, minimal grooming" | 132자 |
  | 미인수 (`minyu_su`) | 수 | 아름다운 외모의 수 | 체격 "a slim yet clearly masculine adult build" · 얼굴 "strikingly handsome, delicate and refined features" · 얼굴 "clear, smooth skin" · 눈 "long lashes and clear eyes" · 표정 "a gentle, composed expression" | 171자 |
  | 꽃수 (`kkotsu`) | 수 | 여성적으로 예쁘고 작은 체격 | 체격 "a slender, lightly built adult man's frame" · 키 "a modest height" · 얼굴 "soft, delicate features on a clearly masculine face" · 눈 "large, clear eyes" · 표정 "a shy, gentle expression" · 자세 "a slightly reserved posture" | 186자 |
  | 떡대수 (`ttokdaesu`) | 수 | 큰 체격의 수 | 체격 "broad shoulders and a sturdy, muscular adult man's build" · 키 "a tall frame" · 얼굴 "strong, masculine features" · 눈 "calm, gentle eyes" · 표정 "a soft, slightly shy expression" · 자세 "a relaxed, unassuming posture" | 181자 |
  | 무심수 (`musim_su`) | 수 | 감정 표현이 적음 | 얼굴 "calm, clean-cut masculine features" · 눈 "a quiet, indifferent gaze" · 표정 "a calm, unreadable expression" · 자세 "a still, composed posture" · 몸가짐 "neat, understated grooming" | 147자 |
  | 능력수 (`neungryeok_su`) | 수 | 능력이 뛰어나 스스로 해결 | 얼굴 "sharp, intelligent masculine features" · 눈 "focused, confident eyes" · 표정 "a composed, self-assured expression" · 자세 "an upright, confident posture" · 몸가짐 "a polished, well-groomed appearance" | 167자 |
  | 굴림수 (`gullim_su`) | 수 | 환경이나 공 때문에 어려운 상황 | 체격 "a lean adult man's build" · 얼굴 "a tired yet handsome masculine face" · 눈 "weary, guarded eyes" · 표정 "a subdued, patient expression" · 자세 "a slightly withdrawn posture" | 143자 |
  | 연하수 (`yeonha_su`) | 수 | 공보다 연하 | 얼굴 "fresh, bright features on a clearly adult male face" · 눈 "bright, lively eyes" · 표정 "an earnest, open expression" · 자세 "an energetic, open posture" · 몸가짐 "clean, natural grooming" | 154자 |

  예: 분석 외모 "a Korean man in his 30s with short black hair" + 떡대공 → 캐스팅 문장 끝 "He has broad shoulders and a solid, muscular build, a tall frame, a firm square jawline, steady, deep-set eyes, and an upright, imposing posture." 실제 분석 꼴 m3("a tall, muscular … angular clean-shaven face … narrow half-lidded eyes …") + 떡대공 → "He has an upright, imposing posture."만 남는다(그림이 체격·키·얼굴·눈을 말함).
- **바꾼 것**
  - `app/src/vnew/genre_bl.py`(새): `ROLES`·`CATEGORIES`·`LOOK_EN_MAX`·`TYPES`, `roles`·`is_type`·`fits`·`list_types`·`look_categories`·`hint_pieces`·`look_hints`, 낱말 표 `_CATEGORY_RE`·`_NOT_BODY`. import는 `re`뿐.
  - `app/src/vnew/casting.py`: `cast_prompt`(넷째 인자 `bl_type=""`, 본문은 `_cast_text`로), 새 `_cast_text`·`_hint_sentence`·`_NEUTRAL_TO_MALE`·`_HINT_SUBJECT`, `_empty_casting`(`blRole`·`blType`), `_apply_settings`(키워드 `bl_role`·`bl_type`, 끝에서 `_apply_bl`), 새 `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`·문구 3개, `update_settings`·`start_draw`(키워드, 뽑기 전 형식 확인, 회차 문장, 로그 ` bl=`), `view`(켬이면 BL 5개 키), 모듈 docstring 3줄.
  - `app/src/vnew/routes.py`: PUT·draw에서 `blRole`·`blType` 전달만.
  - `app/src/vnew/settings.py`: `VNEW_BL_LOOK = _bool("VNEW_BL_LOOK", True)`(주석 2줄).
  - `deploy/config.env.example`: `VNEW_OUTFIT_STALL_S=1200` 뒤 주석 3줄 + `VNEW_BL_LOOK=true`. `k8s/workloads.yaml.tmpl`: 앱 env `VNEW_OUTFIT_STALL_S` 쌍 뒤 주석 1줄 + 한 쌍(`VNEW_CASTING` 바로 뒤에 두면 lr-o·cf-a·cast-m이 보는 자리 순서가 바뀐다).
  - `frontend/public/vnew.js` 캐스팅 구역: 새 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlHintText`·`castingBlFields`, 상수 2개. `castingSection`은 `form.blRole`·`blType`, 성별 칸 붙이기(켬이면 BL 줄과 참고 줄), 저장·뽑기 본문 두 곳. `vnew.css`: `.vnew-casting-row`·`.vnew-casting-bl-role`·`.vnew-casting-bl-type`·`.vnew-casting-bl-hint`.
  - `tools/check_vpoc.py`: 새 `check_vnew_bl_look`(bl-a~i)·`_bl_screen_problems`·`_BL_*` 상수·`_BL_SCREEN_NODE_CASES`(cast-l 하네스 `_CAST_SCREEN_NODE_HARNESS` 그대로 씀), `_run_bl_look_off`, `PARITY_ENV_KEYS`에 `VNEW_BL_LOOK`, cast-j 키 집합, `main()` 두 줄.
  - `.kiro/steering/genre-bl.md`(새, 저장소 루트): 옛 규칙 + "외모는 참고만, 그림 사실 우선" 한 줄, 유형 목록에 떡대공·무심공. fileMatch `**/app/src/vnew/genre_bl.py`·`**/app/src/vnew/casting.py`.
  - `docs/ANALYSIS.md`: 머리 갱신 줄, §3.6 모듈 표(`genre_bl.py`·`casting.py`), §3.6.1 B 보기 행 + 「BL 역할·유형 참고 외모」 표, §7.1 `VNEW_BL_LOOK` 행·단락, §10.1 검사 수·고정 표·기대값 변경·새 그룹 행.
- **검사(새 그룹 `check_vnew_bl_look`)**

  | 검사 | 덮는 것 |
  |---|---|
  | bl-a | `TYPES` 12종·순서, 옛 TAGS 10종 label·desc 글자 그대로, 공 5·수 7 묶음, `roles`·`list_types`·`fits`(11경우)·`is_type`, 조각 61개 모두: 분류 7종 안·한 유형 ≤350자·은어(gong·su·seme·uke·top·bottom·BL·boys love…)·성적·폭력·미성년 낱말·머리·옷·나이·피부색·색 낱말 0·한글 0, 조각이 자기 분류로 읽힘(외모 문장에 같은 글이 있으면 겹치지 않음), 수 유형 masculine/man 낱말·미인수·꽃수 masculine, 모듈 import `re`뿐·저장·외부 호출·옛 식별자 0, 모르는 유형 `""` |
  | bl-b | `look_categories`·`hint_pieces`·`look_hints`: "slim" → 떡대공 build 빠짐, 분류 7종 × 유형 12종(그 분류 조각만 빠짐), 구체 13경우(D3 short stature·"a short, slim", D4 오탐 막기 7종, "He is short", 모든 분류, 빈 문장), 실제 분석 꼴 6개(cast-b 결과 문장)의 분류·떡대공·미인수 조각 |
  | bl-c | 유형 없음 = 지금: 프리셋 3 × 성별 6 × 외모 문장 26개 × 유형 값 7종(인자 없음·None·""·"zz"·0·7·대문자) = 3,276문장이 옛 식(프리셋 + 주어 + 외모 + 눈 색, ≤1500)과 같음, 참고 문장 `""`, `_CAST_IDOL_MALE` 원문 + He is …, 넷째 인자 기본값 `""` |
  | bl-d | 떡대공·무심공·미인수 × 프리셋 2 × 성별 4(male·unknown·""·female) × 외모 문장 9개 = 216문장: 문장 = 옛 식 + " " + 참고 문장, 참고 문장 = `_hint_sentence(조각)`, 은어 0·금지 낱말 0·≤1500·줄바꿈 0, unknown·"" → male(프리셋·He is·person→man·their→his, "man" 있음), female → female·"She has", 떡대공 전문 고정값(720자), `_hint_sentence` 1·2·3조각·They |
  | bl-e | `PROMPT_MAX`를 줄여 조각 하나 빠짐·하나만 남음·다 빠짐(참고 문장 없이 옛 식)·옛 식보다 짧음(옛 식 그대로 자름), 끝나면 1500 |
  | bl-f | `_apply_settings` 20경우(정리·모르는 역할·글 아닌 역할·모르는 유형·글 아닌 유형·짝 불일치·리버서블 2·역할 비움·역할 비움 + 유형·역할만 바꿈 2·유형만 3·유형 ""·None·깨진 저장값 2)와 문구, Flask PUT 11건(400 5·200 6, 저장값), POST draw 7건(형식 400이 CostMismatch보다 먼저·유형만 짝 400·저장 불변·스레드 0, 202 → 회차 문장 끝 참고 문장·로그 `bl=musim_gong`, 유형 없음 → 옛 식·`bl=-`) |
  | bl-g | 끔: 보기 키 = 지금 19개(PUT 응답도), BL 요청 무시(PUT 200·draw 202, 저장값 그대로), 회차 문장 = 옛 식·로그 `bl=-`, 다시 켜면 저장값 사용. settings 한 줄, 하위 프로세스 읽기(없음·false·0·true·no·글자 → T·F·F·T·F·F), 템플릿 한 쌍(`VNEW_OUTFIT_STALL_S` 바로 뒤), base true·되돌리기 주석, `PARITY_ENV_KEYS`, dev·prod 오버레이 키 없음, 렌더 dev·prod `"true"`·+false `"false"` |
  | bl-h | 켬 보기 = 끔 보기 + BL 5개(끝, 나머지 값 같음), `blRoles`·`blTypes` 값, `blHintsEn` = 회차 문장 끝, 깨진 저장값 7종 `""`, 보기 성별 = 뽑기 성별(분석 female → "She has", 사용자 male → "He has"), 고친 외모 문장·lookEdited·lookAuto 그대로·그림 우선("slim" → build 빠짐), 끔에서도 외모 문장 같음, `generate.py`가 BL을 읽지 않음 |
  | bl-i | node 가짜 DOM(cast-l 하네스), 화면 입력은 실제 `casting.view` 응답 6종(켬·저장값·수만·참고 없음·뽑는 중·끔): 순수 함수 5개(목록·title·이상한 모양·짝), 폼 자식 순서·한 줄(성별·역할·유형), 역할 바꾸기 공 → 떡대공 → 리버서블(유지) → 수(비움) → 꽃수 → 저장 본문, 뽑기 본문 2종, 없음이면 유형 칸 숨김, 미리보기 줄 보임 → 바꾸면 숨김 → 되돌리면 보임, 되돌리기 본문, 잠금 2종(뽑는 중·만드는 중), 끔 화면·본문 지금 그대로, 위험한 글 글자 그대로·innerHTML 0, CSS 4개, `node --check`. 화면 저장·뽑기 본문을 실제 Flask 라우트에 그대로 보내 저장·보기·회차 문장까지 확인 |

- **확인**
  - `node --check frontend/public/vnew.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 63줄(시작 전 62 + bl 1), FAIL·WARN 0(약 81초).
  - 바뀐 서버 줄 커버리지(`python -m coverage run --data-file=%TEMP%\… --source=app/src tools/check_vpoc.py`, coverage 7.17, 시작 전 사본과 difflib): 실행문 128줄 중 128줄(100%). `casting.py` 82/82, `genre_bl.py` 45/45, `settings.py` 1/1. `routes.py`는 바뀐 줄이 여러 줄 호출의 인자 줄이라 실행문 0줄이고, 그 호출문(251·268)은 실행됐다.
  - JS 실행 범위(node `NODE_V8_COVERAGE`, bl-i 스크립트): `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlHintText` 실행 안 된 블록 0, `castingBlFields`는 1개(역할 라벨이 빠진 응답의 `|| ""`). `castingSection`의 BL 줄은 모두 실행됐고, 실행 안 된 블록 28개는 이번에 바꾸지 않은 곳이다(사진·오류·금액 다시 읽기 등, cast-l이 덮는다).
  - 변형 검사 39개(저장소 밖 스크립트, 파이썬은 모듈을 바꾼 소스로 다시 실행, 화면·배선은 임시 사본 트리): 유형 없음에 참고 문장, male 강제 없음, 중립 낱말 그대로, 상한 무시, 역할 비워도 유형 남음, 유형 짝 안 봄, 끔인데 BL 처리, 끔인데 저장 유형 사용, 끔인데 보기 필드, 회차 문장에 유형 없음, 뽑기 형식 먼저 안 봄, 보기 짝 안 봄, "and" 없는 나열, 보기 성별 unknown 고정, D3·D4 없음, short 늘 키, 조각에 은어, 수 유형 masculine 없음, 옛 desc 바뀜, fits 역할 무시, 350자 넘음, 라우트 PUT·draw 전달 없음, 화면 미리보기 늘 보임·본문 BL 없음 2·유형 비우기 없음·유형 거르기 없음·늘 켬·잠금 없음·title 없음·유형 칸 늘 보임·innerHTML, CSS 없음, 템플릿 env 없음, base false, 되돌리기 주석 없음, 코드 기본 false. 39개 모두 잡았고 지금 코드는 통과한다. 처음 돌렸을 때 놓친 2개(보기 성별, `fits`의 군더더기 조건)는 검사를 더하고(bl-h 성별) `fits`를 한 줄로 줄였다.
  - 성능 측정(저장소 밖): `hint_pieces` 최악 입력(300자 반복 낱말 11종 × 유형 12) 1회 약 0.21ms, `_cast_text` 1회 약 0.16ms, 보기 BL 필드 JSON 약 3.4KB.
  - 실제 외부 호출 0회: `casting._fal_request`·`_fetch_url`·`characters._fal_request`·`vpoc.fal._request`를 막고(불리면 실패) `_guard_real_llm`으로 Bedrock 0회, 뽑기 스레드는 띄우지 않는다(`_spawn` 기록기).
  - 줄 끝 LF(바뀐·새 파일 15개 CR 0, BOM 없음. 이 WORKLOG는 편집 도구가 덧붙일 때 파일 전체를 CRLF로 써서 LF로 되돌렸고, 되돌린 뒤 diff는 추가 106줄뿐이다), 공백만 바뀐 줄 0(처음엔 `formBox.appendChild(genderWrap);`이 else 블록으로 들어가 들여쓰기만 바뀌어 고쳤다), 새 줄 끝 공백 0, 지운 정의 0(파이썬 AST·JS 함수·CSS 선택자 비교), 저장소 안 부산물 0(coverage data는 `%TEMP%`). 수정 금지 파일(`config.dev.env`·`config.prod.env`·`generate.py`·`.gitlab-ci.yml`·`whynot-20261029`·옛 저장소)은 바뀌지 않았다.
- **바꾼 기존 검사 기대값**
  - cast-j `want_keys`(상세 casting 보기 키 집합)에 `blRole`·`blType`·`blRoles`·`blTypes`·`blHintsEn`을 더했다. 이유: `VNEW_BL_LOOK` 기본 켬에서 보기 끝에 5개 키가 붙는다(R4). 끔이면 원래 19개 그대로인지는 bl-g가 본다. 나머지 cast-a~m 기대값(문장·보기 값·화면 DOM·요청 본문)은 그대로 켬 상태에서 통과한다.
  - 기대값은 그대로 두고 부르는 줄에서 고정: `_run_3d_hunyuan(lambda: _run_bl_look_off(check_vnew_char_fidelity))`. cf-b(`detailOld`)·cf-r이 옛 캐릭터 상세 JSON 전체(casting 보기 포함) sha256을 구현 전 값과 비교한다. 켬이면 casting 보기에 5개 키가 붙어 지문이 달라진다.
  - cf-s 옛 화면 DOM sha256(`a8ec4eff…`)·cast-l(캐스팅 화면 DOM·요청 본문)·rw-a~c·ob-h는 기대값 그대로 통과한다. cf-s는 `castingSection`을 대역으로 바꿔 BL 칸이 닿지 않고, cast-l은 BL 필드가 없는 상세라 끔 화면과 같다.
- **3단계 리뷰**
  - 보안
    - 입력 검증: `blRole`·`blType`은 고정 표의 값만 받는다(글 아님·모르는 값 400). 저장값도 읽을 때 다시 표로 거른다(`_bl_state`). 그래서 사용자가 BL 칸으로 넣을 수 있는 글은 없고, 모델에 가는 영어는 표의 고정 조각뿐이다(프롬프트 끼워 넣기 경로 없음).
    - 오류 문구는 고정 문자열이고 입력을 되풀이하지 않는다. 권한·저장 경로(`require_auth`, `users/{owner}/characters/…`)는 그대로다.
    - 화면은 `el`(textContent)과 속성(`option.value`·`title`)만 쓴다. 위험한 글(`<img onerror>`)을 라벨·desc·참고 문장에 넣어도 글자 그대로다(bl-i).
    - 외부 전송: fal 본문은 그대로 `{prompt, num_images, image_size}`이고 prompt에 고정 영어 한 문장이 더해질 뿐이다. 콘텐츠 등급: 조각에 성적·노출·폭력·미성년 낱말 0(bl-a). 비밀값·계정 정보 없음.
    - 발견·조치: 역할만 바꿀 때 저장 유형이 글이 아닌 깨진 값이면 비우지 않던 경로를 검사로 찾아 고쳤다(`_apply_bl` isinstance). `BadBlType`이 운영 문구 표(`vpoc/errors.py` `USER_FACING_KINDS`)에 없어 운영에서는 "처리 중 오류가 발생했습니다 (코드: BadBlType)"로 보인다. 화면은 맞는 값만 보내 사용자가 볼 일은 없다. `errors.py`가 허용 파일 밖이라 남은 이슈 1에 적었다.
  - 성능(앱 파드 512Mi)
    - 표는 조각 61개(수 KB)로 메모리 변화가 사실상 없다. 새 스레드·락·외부 호출이 없다. 정규식은 반복 상한이 있는 lookahead({0,3}·{0,2})라 되돌아가기 폭주가 없고 입력은 300자로 잘린다(최악 입력 0.21ms).
    - 상세 GET(뽑는 중 5초 폴링)마다 보기에 약 3.4KB가 더해지고 계산이 약 0.2ms 는다. fal 요청 수·금액은 같다(Seedream 장당 금액, 문장 길이와 무관).
    - 발견·조치: 없음.
  - 운영성
    - 로그: `vnew.casting.draw_start … gender=… bl=<유형 또는 ->`(끝에 필드 하나 더함. 이 줄을 읽는 다른 코드·문서는 없다 — 저장소 검색). 400은 `vpoc.error … BadBlType`.
    - 되돌리기: `config.<target>.env`에 `VNEW_BL_LOOK=false` 한 줄 + 배포 job. 끄면 보기·화면·문장이 이 기능 전과 같고 저장값은 남는다. 코드 되돌리기는 이미지 되돌리기.
    - 진행 중 작업·재시작: 회차 문장은 시작 때 한 번 만들어 저장하므로 진행 중 뽑기·이어하기(`reconcile`·`run_draw`)는 설정·배포와 상관없이 그 문장을 쓴다.
    - 섞인 버전(롤링): 옛 파드는 `blRole`·`blType`을 넘기지 않아 무시한다(PUT 200·저장 안 됨). 옛 파드가 casting을 다시 써도 `_casting_of`가 모르는 키를 그대로 두어 BL 저장값이 지워지지 않는다. 옛 화면은 BL을 보내지 않고 새 보기의 추가 키를 무시한다. 오류 없음.
    - 발견·조치: 없음.
- **남은 이슈**
  1. `BadBlType`이 `vpoc/errors.py` `USER_FACING_KINDS`에 없다(허용 파일 밖). 운영에서 API를 직접 부르면 고정 문구 대신 코드만 보인다. 한 줄 추가로 끝난다.
  2. 미리보기 줄은 저장된 외모 문장 기준이다. 외모 문장 칸을 고치고 저장하지 않으면 줄이 그대로 남는다(뽑기는 고친 문장으로 서버가 다시 계산한다). 역할·유형을 바꾼 경우만 숨긴다(D8).
  3. 분석 외모 문장은 거의 늘 얼굴(face)·눈(eyes)을 말해서, 실제로는 얼굴·눈 조각이 자주 빠지고 체격·키·표정·자세·몸가짐이 주로 남는다(실제 분석 꼴 6개에 떡대공을 고르면 m2·m3·f1은 자세 한 조각, u1은 눈·자세 두 조각, m1·m4는 세 조각). 그림 우선 규칙대로지만, 정보를 더 주려면 분류를 "얼굴 모양"과 "얼굴 인상"으로 나누는 결정이 필요하다.
  4. 미인수 "clear, smooth skin"은 현실 배우풍 프리셋의 "natural skin texture with pores and slight imperfections"와 결이 다르다. 프리셋 문구는 판정에 넣지 않는다(D2).
  5. 유형 + 사용자가 정한 female은 지시대로 그대로 두어 "She has … masculine …" 문장이 나온다. BL 유형을 고르고 성별을 여로 둔 경우 화면 경고가 없다.
  6. 유형 + 성별 male을 사용자가 직접 정했고 고친 외모 문장에 person·their가 있으면 지금처럼 그대로 둔다(D6은 unknown → male만 바꾼다).
  7. 실제 Seedream 사진에서 참고 문장이 체격·인상에 얼마나 반영되는지는 유료 확인(개발계 뽑기 1회 $0.08)이 필요하다. Claude가 승인 뒤 한다.
- **피드백(같은 날, Claude): 103 남은 이슈 1·3·4를 고친다.** 103 결과는 확인됐고 커밋 전이다. 같은 작업 트리에서 이어서 했고, 103 직후 사본(15개 파일 + 스펙·steering 4개)을 `%TEMP%`에 두었다. 시작 전 트리는 103 마지막 전체 검사(전부 통과) 때와 같았다(파일 시각 기준, 그 뒤 생긴 것은 지시서 C-bl-look-hints-fix1.md\와 로그뿐).
  - 원인(남은 이슈 3): 103 규칙은 외모 문장에 얼굴·눈 낱말만 있어도 그 분류 조각을 모두 뺐다. 분석 외모 문장은 거의 늘 얼굴·눈을 말해서 실제 분석 꼴 6개에 떡대공을 고르면 남는 조각이 [3, 1, 1, 1, 2, 3](평균 1.83개, 무심공 1.83·미인수 1.50)이었다. 목적("정보를 많이 주기")과 맞지 않았다.
  - 결정(Claude): 그림 우선은 그림이 실제로 보여 주는 **형태** 사실과 부딪칠 때만 뺀다. 형태 4분류(`build`·`height`·`faceShape`·`eyeShape`)는 겹치면 빼고, 인상 5분류(`faceImpression`·`gaze`·`expression`·`posture`·`grooming`)는 늘 더한다.
  - 결정(Kiro, design D13~D17)
    - F1 — 섞인 조각을 나눴다(D14). 떡대공 "steady, deep-set eyes" → "deep-set eyes" + "a steady gaze"(지시 예시). 같은 방식으로 광공 "sharp, striking features", 츤데레공 "cool, well-defined features"·"sharp eyes with a slightly averted gaze", 무심공 "cool, sharply defined features"·"a half-lidded, indifferent gaze", 꽃수 "large, clear eyes", 떡대수 "strong, masculine features", 능력수 "sharp, intelligent masculine features"를 형태 + 인상 둘로 나눴다. 모양 낱말이 없는 얼굴·눈 조각("warm, kind features", "soft, attentive eyes" 등)은 글은 그대로 두고 분류 이름만 인상·눈빛으로 바꿨다. 미인수 "long lashes and clear eyes"는 지시대로 eyeShape 하나다.
    - F2 — 떡대공에 인상 조각 둘을 더했다: "a rugged, masculine look"(faceImpression), "a calm, confident expression"(expression)(D15). 나누기만 하면 떡대공 인상 조각이 눈빛·자세 2개라 평균이 2.83개로 목표(4개 이상)에 못 미쳤다. 하나만 더하면 3.83개(변형 N11이 확인), 둘이면 4.83개다. 지시 범위(나누기)를 넘는 글 추가라 여기에 적는다.
    - F3 — 형태 판정 낱말 표(D16): 얼굴 모양은 턱·턱선·광대(`chin-length`는 머리 길이라 뺌)·"…-faced"·"face/bone structure", 그리고 얼굴형 낱말(angular·oval·round·square·narrow·slender·sharp·strong·defined …)이 **같은 명사구**에서 face·features 앞에 올 때다. 눈 모양은 눈꺼풀·속눈썹·"…-eyed", 그리고 눈 모양 낱말(narrow·almond·large·deep-set·hooded·half-lidded·sharp·doe …)이 같은 명사구에서 eyes 앞에 올 때다. 같은 명사구 = 세 낱말 안, 관사·전치사·접속사·다른 명사(`_PHRASE_STOP`)를 넘지 않음("long hair, a calm face"의 long은 얼굴형이 아니다). 코·입술·눈썹은 유형 조각이 말하지 않는 부위라 보지 않는다. "piercing eyes"·"calm, gentle eyes"는 눈빛이라 보지 않는다. "hooded"는 옷(hooded jacket)에도 쓰여 eyes 앞에서만 본다. 103의 face·eyes·expression·posture·grooming 낱말 표는 지웠다(이 모듈은 103에서 새로 만든 커밋 전 파일이다).
    - F4 — 103 결함 하나를 함께 고쳤다: 체격·키 낱말 뒤 창(`_NOT_BODY`)이 "and"를 넘어 "a slim build and narrow eyes"의 slim·build를 체격이 아니라고 읽었다. 창이 관사·전치사·접속사(`_JOIN_STOP`)를 넘지 않게 했다(변형 N1이 확인).
    - F5 — 인상 조각은 늘 더하므로 모양 사실이 없어야 한다. bl-a가 인상 조각이 형태 분류로 읽히지 않는지, 형태 조각이 자기 형태 분류로만 읽히는지(체격·키 묶음 "a lean, tall build"만 둘) 본다.
    - F6 — 미인수 "clear, smooth skin" → "a clean, clear complexion"(faceImpression, 남은 이슈 4). 피부 결 낱말이 없어 현실 배우풍 프리셋 "natural skin texture with pores and slight imperfections"와 부딪치지 않는다.
    - F7 — 운영 문구(남은 이슈 1): `vpoc/errors.py` `USER_FACING_KINDS`에 `BadBlType` 한 줄(이번만 허용). 모르는 역할·유형 문구는 둘이었는데 지시 문구 "알 수 없는 BL 역할·유형입니다." 하나(`casting._MSG_BAD_BL`)로 합쳤다. 짝 불일치 문구는 그대로다. 둘 다 고정 문자열이고 입력을 되풀이하지 않는다. 기존 errors 관련 검사((d)·ad-a·cast-l·lr-a)는 kind가 표에 있는지만 보고 표 전체를 고정하지 않아 기대값을 바꾸지 않았다.
  - 검토한 대안
    - (가) 인상도 겹치면 빼되 낱말을 좁히기: 표정은 `look_sentence`가 지우고 시선·인상 낱말은 그림 사실이 아니다. 규칙만 커진다.
    - (나) 떡대공 평균을 올리려고 형태 판정을 느슨하게(예: 얼굴형 낱말만 보고 턱 낱말은 안 봄): 그림 사실과 부딪치는 조각이 남는다(m3 "a strong jaw" + "a firm square jawline").
    - (다) 채택: 형태·인상 분류 + 섞인 조각 나누기 + 떡대공 인상 조각 2개.
  - 새 유형 표(형태 = 겹치면 뺀다, 인상 = 늘 더한다)

  | 유형(id) | 역할 | 형태 조각(겹치면 뺀다) | 인상 조각(늘 더한다) | 이은 길이 |
  |---|---|---|---|---|
  | 광공 / 집착공 (`gwanggong`) | 공 | 키 "a tall frame" · 체격 "a broad-shouldered, powerful build" · 얼굴 모양 "sharp features" | 얼굴 인상 "striking good looks" · 눈빛 "an intense, unwavering gaze" · 자세 "an upright stance with an imposing presence" | 159자 |
  | 츤데레공 (`tsundere_gong`) | 공 | 얼굴 모양 "well-defined features" · 눈 모양 "sharp eyes" | 얼굴 인상 "a cool, reserved look" · 눈빛 "a slightly averted gaze" · 표정 "an aloof expression with a hint of softness" · 자세 "a relaxed yet guarded posture" | 157자 |
  | 다정공 (`dajeong_gong`) | 공 | — | 얼굴 인상 "warm, kind features" · 눈빛 "soft, attentive eyes" · 표정 "a gentle, warm expression" · 자세 "a relaxed, open posture" · 몸가짐 "neat, clean grooming" | 115자 |
  | 떡대공 (`ttokdae_gong`) | 공 | 체격 "broad shoulders and a solid, muscular build" · 키 "a tall frame" · 얼굴 모양 "a firm square jawline" · 눈 모양 "deep-set eyes" | 얼굴 인상 "a rugged, masculine look" · 눈빛 "a steady gaze" · 표정 "a calm, confident expression" · 자세 "an upright, imposing posture" | 196자 |
  | 무심공 (`musim_gong`) | 공 | 체격 "a lean, tall build" · 얼굴 모양 "sharply defined features" · 눈 모양 "half-lidded eyes" | 얼굴 인상 "a cool, detached air" · 눈빛 "an indifferent gaze" · 표정 "a calm, unreadable face" · 몸가짐 "neat, minimal grooming" | 154자 |
  | 미인수 (`minyu_su`) | 수 | 체격 "a slim yet clearly masculine adult build" · 눈 모양 "long lashes and clear eyes" | 얼굴 인상 "strikingly handsome, delicate and refined masculine features" · 얼굴 인상 "a clean, clear complexion" · 표정 "a gentle, composed expression" | 178자 |
  | 꽃수 (`kkotsu`) | 수 | 체격 "a slender, lightly built adult man's frame" · 키 "a modest height" · 눈 모양 "large eyes" | 얼굴 인상 "soft, delicate features on a clearly masculine face" · 눈빛 "a clear gaze" · 표정 "a shy, gentle expression" · 자세 "a slightly reserved posture" | 193자 |
  | 떡대수 (`ttokdaesu`) | 수 | 체격 "broad shoulders and a sturdy, muscular adult man's build" · 키 "a tall frame" · 얼굴 모양 "strong features" | 얼굴 인상 "a good-natured, masculine look" · 눈빛 "calm, gentle eyes" · 표정 "a soft, slightly shy expression" · 자세 "a relaxed, unassuming posture" | 202자 |
  | 무심수 (`musim_su`) | 수 | — | 얼굴 인상 "calm, clean-cut masculine features" · 눈빛 "a quiet, indifferent gaze" · 표정 "a calm, unreadable expression" · 자세 "a still, composed posture" · 몸가짐 "neat, understated grooming" | 147자 |
  | 능력수 (`neungryeok_su`) | 수 | 얼굴 모양 "sharp features" | 얼굴 인상 "an intelligent, masculine look" · 눈빛 "focused, confident eyes" · 표정 "a composed, self-assured expression" · 자세 "an upright, confident posture" · 몸가짐 "a polished, well-groomed appearance" | 176자 |
  | 굴림수 (`gullim_su`) | 수 | 체격 "a lean adult man's build" | 얼굴 인상 "a tired yet handsome masculine face" · 눈빛 "weary, guarded eyes" · 표정 "a subdued, patient expression" · 자세 "a slightly withdrawn posture" | 143자 |
  | 연하수 (`yeonha_su`) | 수 | — | 얼굴 인상 "fresh, bright features on a clearly adult male face" · 눈빛 "bright, lively eyes" · 표정 "an earnest, open expression" · 자세 "an energetic, open posture" · 몸가짐 "clean, natural grooming" | 154자 |

  - 실제 분석 꼴 6개(cast-b `_CAST_LOOK_FEEDBACK` 결과 문장, 캐스팅 문장에 들어가는 `prompt_look` 그대로)에서 남는 조각(남음/전체)

  | 분석 꼴 | 외모 문장의 형태 분류 | 떡대공 | 무심공 | 미인수 |
  |---|---|---|---|---|
  | m1 | 체격·얼굴 모양 | 6/8 | 5/7 | 4/5 |
  | m2 | 체격·키·얼굴 모양·눈 모양 | 4/8 | 4/7 | 3/5 |
  | m3 | 체격·키·얼굴 모양·눈 모양 | 4/8 | 4/7 | 3/5 |
  | f1 | 체격·키·얼굴 모양·눈 모양 | 4/8 | 4/7 | 3/5 |
  | u1 | 체격·키·얼굴 모양 | 5/8 | 5/7 | 4/5 |
  | m4 | 얼굴 모양·눈 모양 | 6/8 | 5/7 | 4/5 |
  | 평균 | | 4.83 | 4.50 | 3.50 |

    103 처음 규칙의 평균은 떡대공 1.83·무심공 1.83·미인수 1.50이었다. 미인수는 인상 조각이 3개라 형태가 모두 겹치면 3개다.
  - 바꾼 것
    - `app/src/vnew/genre_bl.py`: `FORM_CATEGORIES`·`IMPRESSION_CATEGORIES`·`CATEGORIES`, `TYPES`(이름표·나눈 조각·떡대공 2조각·미인수 피부), 형태 판정 `_CATEGORY_RE`(build·height·faceShape·eyeShape)·`_JOIN_STOP`·`_PHRASE_STOP`·`_before`·`_FACE_SHAPE_WORDS`·`_EYE_SHAPE_WORDS`, `_NOT_BODY`(접속사를 넘지 않음), `look_categories`(형태만)·`hint_pieces`(인상은 늘), 모듈 docstring. `_BODY_CATEGORIES`는 쓰는 곳이 없어 지웠다(103 새 파일의 커밋 전 상수).
    - `app/src/vnew/casting.py`: `_MSG_BAD_BL_ROLE`·`_MSG_BAD_BL_TYPE` → `_MSG_BAD_BL`(103 새 상수, 커밋 전), `_bl_checked` 두 줄, 주석 2줄.
    - `app/src/vpoc/errors.py`: `USER_FACING_KINDS`에 `"BadBlType"`과 주석 2줄.
    - `tools/check_vpoc.py`: bl-a(분류 9종·형태·인상 읽힘·나눈 조각·complexion), bl-b(새 규칙 기대값 전체·인상 낱말 8경우·구체 19경우·분석 꼴 6개 × 3유형·떡대공 평균 ≥ 4), bl-d·bl-e(떡대공 8조각), bl-f(문구 둘), bl-h(고친 외모 문장 기대값), bl-i(참고 문장 없는 상세 = 유형 없음), 새 bl-j, `_BL_FORM`·`_BL_IMPRESSION`·`_BL_TTOKDAE_MIN_AVG`·`_BL_TTOKDAE_SENTENCE`.
    - `.kiro/steering/genre-bl.md`(외모 참고 한 줄을 형태·인상 규칙으로), `.kiro/specs/bl-look/`(R1.6·R1.8·R1.9·R8, design D3·D11·D13~D17·§3 표·§8, tasks 6~9), `docs/ANALYSIS.md`(머리 줄, §3.6 모듈 표, §3.6.1 그림 우선·설정 저장 행·운영 문구 단락, §10.1).
  - 바꾼 기대값: 모두 103에서 새로 만든(커밋 전) bl 검사다. bl-b 전체(분류 표·그림 우선 경우·실제 분석 꼴), bl-d `_BL_TTOKDAE_SENTENCE`(떡대공 8조각, 783자 문장), bl-e "하나 뺀" 문장, bl-f 문구, bl-h 고친 외모 문장의 참고 문장, bl-i noHint 상세(유형이 있으면 인상 조각이 늘 남아 참고 문장이 비지 않으므로, 비는 경우를 "역할만 있고 유형 없음"으로 바꿨다). 그 밖의 기존 검사 기대값은 바꾸지 않았다(cast-a~m·cf-*·rw-*·ob-*·lr-* 그대로).
  - 검사(바뀐 것)

    | 검사 | 덮는 것 |
    |---|---|
    | bl-a | 분류 표 = 형태 4·인상 5, 조각 72개 모두 9분류 안·한 유형 ≤350자(최장 202자)·금지 낱말 0, 형태 조각은 자기 형태 분류로만 읽힘, 인상 조각은 형태 분류 없음, 떡대공 눈 조각이 둘로 나뉨·미인수 eyeShape 하나, 미인수 complexion·"smooth skin" 0 |
    | bl-b | 형태 4종 × 유형 12종(그 분류 형태 조각만 빠짐), 인상 낱말 8경우(표정·자세·몸가짐·눈빛·얼굴 인상·피부·면도 — 아무것도 안 빠짐), 구체 19경우(D3·D4·같은 명사구 창·hooded jacket·chin-length·piercing eyes·a sharp nose·high cheekbones·almond-shaped·square-jawed), 실제 분석 꼴 6개 × 떡대공·무심공·미인수 조각 전부, 떡대공 평균 ≥ 4 |
    | bl-j | `USER_FACING_KINDS`에 `BadBlType` 한 줄, `BadBlType`을 던지는 곳 4곳(AST, `app/src` 전체)이 casting.py 고정 문구 상수 둘, `RAW_ERRORS=false` 봉투와 실제 Flask PUT 3건(모르는 역할·유형 → 지시 문구, 짝 불일치 → 짝 문구)이 고정 문구 그대로·type `BadBlType` |

  - 확인
    - `node --check frontend/public/vnew.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 63줄(bl 그룹 한 줄에 bl-a~j), FAIL·WARN 0(약 86초).
    - 바뀐 서버 줄 커버리지(coverage 7.17, `--data-file=%TEMP%\…`, 103 직후 사본과 difflib): 실행문 15줄 중 15줄(100%). `genre_bl.py` 12/12, `casting.py` 3/3, `errors.py`는 바뀐 줄이 집합 글자라 실행문 0줄. `genre_bl.py` 전체도 51/51.
    - 변형 검사 53개(103의 39개 + 이번 14개): 이번 것은 접속사를 넘는 창(N1), 명사구 막음 없음(N2), chin-length(N3), hooded 어디서나(N4), eyes면 늘 눈 모양(N5), face면 늘 얼굴 모양(N6), 인상도 뺌(N7), 옛 피부 조각(N8), 떡대공 눈 안 나눔(N9), 인상 조각에 모양 사실(N10), 떡대공 추가 조각 하나 없음(N11 — 평균 3.83), errors 표에 없음(E1), 옛 문구(E2), 문구에 입력 되풀이(E3). 53개 모두 잡았고 지금 코드는 통과한다. 103의 D3 변형(G1)은 새 식에 맞춰 다시 썼다.
    - 줄 끝 LF(바뀐 파일 CR 0 — 이번에는 WORKLOG를 파이썬으로 이어 써서 CRLF가 생기지 않았다), 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 저장소 안 부산물 0. 103 이전(커밋된) 정의는 지운 것이 없다(103 이전 사본과 AST 비교). 지운 \_BODY_CATEGORIES\·\_MSG_BAD_BL_ROLE\·\_MSG_BAD_BL_TYPE\는 103에서 새로 만든 커밋 전 상수다. 수정 금지 파일과 화면·설정 파일(vnew.js·vnew.css·routes.py·settings.py·템플릿·base)은 바뀌지 않았다.
  - 3단계 리뷰
    - 보안: 모델에 가는 영어는 여전히 고정 표 조각뿐이다(사용자 입력 없음). 인상 조각은 늘 더해지므로 bl-a가 인상 조각에 모양 사실·은어·성적·폭력·미성년 낱말이 없음을 본다. `BadBlType` 문구는 고정 문자열이고 운영에서 보여도 입력·키·URL이 섞이지 않는다(bl-j AST). 발견·조치: 없음.
    - 성능: 판정 정규식은 반복 상한이 있는 창(세 낱말)이고 입력은 300자다. 최악 입력(반복 낱말 300자) `look_categories` 1회 약 0.28ms, `hint_pieces` 1회 약 0.28ms. 참고 문장이 길어져 캐스팅 문장 최장 1,052자(1500자 상한 안), 보기 BL 필드 약 3.6KB. 외부 호출·금액 변화 없음. 발견·조치: 없음.
    - 운영성: 되돌리기는 그대로 `VNEW_BL_LOOK=false` 한 줄이다. 진행 중 뽑기는 시작 때 만든 문장을 쓴다(이 변경 전 문장). 운영에서 API로 모르는 BL 값을 보내면 이제 "알 수 없는 BL 역할·유형입니다."가 보인다(로그 `vpoc.error … BadBlType`은 그대로). 발견·조치: F4(접속사를 넘는 창)를 고쳤다.
  - 남은 이슈(이번)
    1. 몸가짐(grooming)도 지시대로 늘 더한다. 그림이 "messy undercut"(u1)처럼 흐트러진 머리를 말해도 무심공 "neat, minimal grooming"이 붙는다. 머리 모양은 그림이 이기지만(외모 문장에 남는다) 문장 안에서 결이 엇갈릴 수 있다.
    2. 수 유형의 masculine 낱말이 형태 조각에만 있는 유형이 있다(미인수 build). 형태가 모두 겹치면(m2·m3·f1) 참고 문장에 masculine이 없다. 주어 "He is a Korean man …"은 늘 있다. 인상 조각에 넣을지는 글 결정이 필요하다.
    3. 프리셋 문구와 인상 조각이 엇갈릴 수 있다: 아이돌풍 "slim V-line face"·"calm expression"과 떡대공 "a firm square jawline"·츤데레공 "an aloof expression". 프리셋은 판정에 넣지 않는다(D2, 103과 같음).
    4. 103 남은 이슈 2·5·6·7(미리보기 줄 기준, female + 유형, male 지정 + 중립 낱말, 유료 확인)은 그대로다.
- 2026-10-31 Claude 리뷰: 떡대공 인상 조각 2개 추가 승인. 미인수 남은 이슈 2는 인상 조각을 "strikingly handsome, delicate and refined masculine features"로 바꿔 해결(형태가 모두 겹쳐도 masculine 유지).

### 지시서 104 — BL 유형은 사용자가 캐릭터 설명에 적었을 때만 반영 (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/104-bl-from-description.md`. 스펙 `.kiro/specs/bl-look/`에 R9~R12·D18~D27·§9·tasks 10~14를 먼저 더하고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았고 사용자 데이터를 읽지 않았다.
- **먼저 한 일**: 바꿀 파일 12개의 시작 전 사본과 저장소 파일 목록(시각 포함)을 저장소 밖(`%TEMP%\kiro104_baseline`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 63줄, FAIL·WARN 0, 89초).
- **배경**: 103(커밋 89d5a34, 개발·운영 배포)은 캐스팅 패널의 "BL 역할 / 유형" 선택으로 참고 문장을 더했다. 사용자 지시(2026-10-31): "이런 BL 요소는 사용자가 해당 캐릭터에 대한 설명에 떡대공이다 이런 식으로 넣는다면 그때 반영을 하도록 해라." 근거를 사용자가 쓴 캐릭터 설명(`char.extraPromptKo`, 캐릭터 만들기의 추가 요청 칸)으로 바꾼다.
- **결정**(design D18~D27)
  - D18 — 근거는 `char.extraPromptKo` 하나다. `analysis.descriptionKo`(LLM 글)·`appearanceEn`·외모 문장·저장된 `blRole`·`blType`은 보지 않는다.
  - D19 — 찾기: 표 label을 "/"로 나눈 이름 13개를 붙은 꼴 그대로 부분 문자열로 찾는다(앞 경계 없음, 뒤에는 조사·서술어 허용). 부정은 이름 끝 바로 뒤 0~5번째 글자(6자, 공백 포함)에서 "아님"·"아니"·"말고"가 **시작**되면이다. "6자 안에 오면"을 "시작하면"으로 읽어 "떡대공은 절대 아님"(아님이 5번째에서 시작)도 부정으로 본다. 나온 순서·중복 없이 최대 2개. 부정된 자리 뒤에 같은 이름이 부정 없이 다시 나오면 찾는다.
  - D20 — 합치기: 앞 유형 조각을 표 순서대로 모두 넣고, 뒤 유형 조각은 그 분류를 앞 유형이 이미 가졌으면 뺀다(형태 조각은 글이 담은 형태 분류까지 — 무심공 "a lean, tall build"는 체격·키). 판정은 표 기준이고 그 뒤 그림 우선을 한 번 적용한다. 그래서 앞 유형 조각이 그림 때문에 빠져도 뒤 유형이 그 자리를 채우지 않는다. 1500자 줄이기는 끝(뒤 유형 조각)부터다.
  - D21 — `cast_prompt`·`_cast_text`·`hint_pieces`·`look_hints`의 유형 인자는 id 하나 또는 id 목록이다(`known_types`). 넷째 인자 기본값 `""`는 그대로라 103 호출·검사가 그대로 돈다.
  - D22 — 103 저장값은 읽지도 지우지도 않는다. `_empty_casting` 기본값도 그대로다. 103 함수(`_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`, 화면 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields`)와 CSS 선택자는 quality-week 9에 따라 남겼다. 부르는 곳은 없다.
  - D23 — 라우트는 `blRole`·`blType`을 넘기지 않고 `update_settings`·`start_draw`·`_apply_settings`에서 BL 키워드를 뺐다. 옛 화면이 보내도 무시된다(PUT 200·draw 202).
  - D24 — 보기(켬) 끝에 `blDetected`(`[{id, label}]`, label은 표 그대로)·`blHintsEn` 두 키. 끔이면 BL 키가 없다(103 끔과 같다).
  - D25 — 화면: 선택을 없애고 성별 칸 바로 뒤에 읽기 전용 한 줄을 둔다. 찾음 `p.vnew-casting-bl-found`(13px) "캐릭터 설명에서 찾은 BL 유형: 떡대공 — 참고로 더해지는 외모 설명(영어): …", 못 찾음 `p.vnew-hint.vnew-casting-bl-guide`(12px) "캐릭터 설명에 '떡대공'처럼 BL 유형을 적으면 실사 사진에 참고로 반영됩니다." 화면은 `blDetected`만 보므로 롤링 배포 중 103 꼴 응답이 와도 선택이 다시 생기지 않는다.
  - D26 — 로그 `bl=` 끝말은 찾은 id를 `+`로 이은 값(예 `bl=musim_gong+ttokdae_gong`), 없으면 `-`.
  - D27 — 검사 기대값: cast-j 키 집합은 19개 + `blDetected`·`blHintsEn`. `_run_bl_look_off(check_vnew_char_fidelity)`는 그대로 둔다.
- **검토한 대안**
  - (가) 화면 선택을 남기고 설명도 근거로 더하기: 두 근거가 부딪친다. 지시와 다르다. 거절.
  - (나) LLM으로 설명에서 유형 분류: 비결정적이고 비용·지연이 생긴다. 사용자가 쓴 낱말 그대로라는 지시와도 다르다. 거절.
  - (다) 이름 앞 경계(앞 글자가 한글이 아님)를 요구: "형광공장"·"광공업"의 광공 오탐은 막지만 흔한 말 "집착광공"을 놓친다. 하지 않았다(남은 이슈 1).
  - (라) 합칠 때 그림으로 빠진 앞 유형 자리를 뒤 유형이 채우기: 정보는 늘지만 사용자가 앞에 쓴 유형의 뜻과 어긋난다(무심공 + 떡대공에서 그림이 "slim"이면 떡대공 "broad shoulders…"가 들어온다). 거절.
  - (마) 103 함수·화면 함수 지우기: quality-week 9 위반. 남기고 남은 이슈 2에 적었다.
- **바꾼 것**
  - `app/src/vnew/genre_bl.py`: 새 `NEGATIONS`·`NEGATION_WINDOW`·`DETECT_MAX`·`_NAME_TO_ID`·`_NAME_RE`·`_NEGATION_SPAN`, 새 `_negated`·`detect_types`·`known_types`·`_piece_categories`·`merged_pieces`, `hint_pieces`·`look_hints`(유형 목록, 인자 이름 `bl_types`), 모듈 docstring 한 단락. import는 그대로 `re`뿐.
  - `app/src/vnew/casting.py`: 새 `_bl_types_now`, `cast_prompt`·`_cast_text`(인자 `bl_types`, `known_types`), `_apply_settings`·`update_settings`·`start_draw`(BL 키워드·`_apply_bl` 호출·뽑기 전 `_bl_checked`를 뺌, 회차 문장 `_bl_types_now(data)`, 로그 `"+".join(...) or "-"`), `view`(BL 필드 2개), 103 함수 구역 주석, 모듈 docstring 3줄.
  - `app/src/vnew/routes.py`: PUT·draw에서 BL 전달 두 줄을 빼고 주석 한 줄씩.
  - `frontend/public/vnew.js`: 새 `castingBlLine`·상수 2개(`CASTING_BL_FOUND_LEAD`·`CASTING_BL_GUIDE`), `castingSection`(`blOn`·`form.blRole`·`blType`·`castingBlFields` 호출과 저장·뽑기 본문의 BL 두 곳을 빼고 성별 칸 뒤 한 줄), BL 주석. `vnew.css`: 새 `.vnew-casting-bl-found`·`.vnew-casting-bl-guide`, 103 선택자 주석.
  - `tools/check_vpoc.py`: bl 그룹 docstring·`seed_char(extra=)`·bl-c(목록 4종)·bl-e(두 유형)·bl-f·bl-g(끔 부분)·bl-h·bl-i·bl-j(라우트 부분) 다시 씀, 새 bl-k·bl-l, `_BL_VIEW_KEYS`(2개)·`_BL_OLD_VIEW_KEYS`·`_BL_DESC_TTOKDAE`·`_BL_GUIDE`·`_BL_FOUND_LEAD`·`_BL_HINT_LEAD`·`_BL_NAMES`·`_BL_DETECT_CASES`, `_BL_SCREEN_NODE_CASES`·`_bl_screen_problems` 다시 씀, cast-j 키 집합, `_run_bl_look_off` 주석.
  - `.kiro/steering/genre-bl.md`(한 줄), `.kiro/specs/bl-look/`(R9~R12·D18~D27·§9·tasks 10~14), `docs/ANALYSIS.md`(머리 줄, §3.6 모듈 표 2행, §3.6.1 보기 행·운영 문구 단락·BL 표(근거·합치기 행 추가, 문장·설정 저장·뽑기 문장·보기·화면 행), §7.1 행·단락, §10.1 고정 표·기대값 단락·검사 행).
- **검사(바뀐 bl 그룹, 어떤 검사가 어떤 함수·분기를 덮는지)**

  | 검사 | 덮는 것 |
  |---|---|
  | bl-c | `known_types`의 글·목록·그 밖의 값 분기(`[]`·`["zz"]`·`("",)`·`[None, 대문자]`), `_cast_text` 유형 없음 경로 = 옛 식 5,148문장 |
  | bl-e | `_cast_text` 줄이기 루프의 두 유형 경로(뒤 유형 조각부터 뺌) |
  | bl-f | `update_settings`·`start_draw`·`_apply_settings` 서명(BL 인자 없음), routes.py 소스(전달 없음), casting.py AST(103 저장값을 읽는 함수 = `_empty_casting` + 103 함수뿐), 남긴 103 함수 동작(`_apply_bl` 12경우·`_bl_state`·`_bl_type_now`), 실제 Flask PUT 6건(BL 값 여러 꼴 → 200·저장값 그대로·보기에 103 키 없음), draw(틀린 금액 → 409 CostMismatch·저장 불변, 맞는 금액 → 202·회차 문장 = 103 이전 식·로그 `bl=-`), 설명 "떡대공이다. 후드티 말고 베이지 셔츠로" → 회차 문장 끝 참고 문장·`blDetected`·로그 `bl=ttokdae_gong`, 두 유형 → `bl=musim_gong+ttokdae_gong` |
  | bl-g | `_bl_types_now`의 끔 분기, 끔 보기 키 19개·PUT·draw·회차 문장 옛 식·`bl=-`, 다시 켜면 설명에서 찾음·103 저장값 그대로. 설정·배선·렌더 검사는 103 그대로 |
  | bl-h | `view` 켬 분기(키 = 끔 + 2개·끝 순서·103 키 없음), 근거가 아닌 곳 9경우(descriptionKo·appearanceEn·고친 외모 문장·103 저장값·None·숫자·목록·dict·역할 낱말·부정), `_bl_types_now(None·글)`, 두 유형 label·참고 문장, 보기 성별 = 뽑기 성별, 고친 외모 문장 그림 우선·PUT의 BL 값 저장 안 됨, generate.py가 BL을 읽지 않음 |
  | bl-i | node 가짜 DOM. `castingBlLine` 순수 결과 8경우(null·빈 것·배열 아님·빈 배열·찾음·참고 문장 없음·이상한 항목·이름 없음), 실제 `view` 응답(찾음·두 유형·못 찾음(103 저장값)·뽑는 중·만드는 중)의 폼 자식 순서·선택 1개(성별)·103 칸 0·줄 글·class, 103 꼴 응답과 끔: 선택·줄 0·본문 그대로, 저장·뽑기·되돌리기 본문 키(BL 없음), 위험한 글, innerHTML 0, CSS(안내 12px·찾음 13px), `node --check`, 남긴 103 화면 함수 동작. 다시 만들기: 진짜 `buildSection`(다시 만들기 → 추가 요청 → 확인)이 보낸 POST `/build` 본문 = 파이썬이 실제 라우트로 보낸 본문(빌드 스레드는 기록기), 진짜 `refreshDetail`이 실제 GET 상세로 다시 그려 줄이 안내 → "떡대공 — 참고 …" → ("떡대공 아님"으로 다시 만들면) 안내. 화면 본문을 실제 PUT·draw에 보내 보기·회차 문장 |
  | bl-j | `USER_FACING_KINDS`의 `BadBlType` 한 줄(남김), 던지는 곳 4곳 = 103 함수의 고정 문구 상수 둘(AST), `RAW_ERRORS=false` 봉투 문구, 실제 PUT에 모르는 BL 값 3건 → 200 |
  | bl-k | `detect_types` 전 분기·`_negated`: 이름 표 13개·상수, 33경우(지시 예 7·붙은 꼴·"떡대공원"·띄어 쓴 꼴·집착광공·중복·부정 7·줄바꿈·역할 낱말만 4·글 아닌 값 4), 이름마다 "…이다"·"…, " 2꼴, 이름 × 부정 낱말 39경우, 창 경계 8경우(0~5번째 = 부정, 6·7번째 = 찾음), 결과가 새 목록 |
  | bl-l | `merged_pieces`·`_piece_categories`·`known_types`·`hint_pieces`·`look_hints` 목록: 무심공 + 떡대공(자세만 더함)·떡대공 + 무심공(몸가짐만)·미인수 + 다정공(같은 유형 안 얼굴 인상 2조각 유지), 모르는 id·중복, 유형 하나 = 표 12종, 그림 "slim" 뒤 앞 유형 자리를 뒤 유형이 채우지 않음, 두 유형 `cast_prompt` 전문(771자), `["ttokdae_gong"]` = 103 떡대공 전문 |

  bl-a·bl-b·bl-d는 기대값을 바꾸지 않고 그대로 통과한다(유형 하나의 결과가 103과 같다는 증거).
- **확인**
  - `node --check frontend/public/vnew.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 63줄(bl 그룹 한 줄에 bl-a~l), FAIL·WARN 0(약 90초).
  - 바뀐 서버 줄 커버리지(coverage 7.17, `--data-file=%TEMP%\kiro104_baseline\cov104.data`, 시작 전 사본과 difflib): 실행문 70줄 중 70줄(100%). `genre_bl.py` 54/54(파일 전체 100%), `casting.py` 16/16(파일 전체 99.4%), `routes.py`는 바뀐 줄이 주석·인자 줄이라 실행문 0줄(그 호출문은 실행됨).
  - 변형 검사 56개(저장소 밖 `kiro104_mut.py`, 파이썬은 모듈을 바꾼 소스로 다시 실행, 화면·배선은 임시 사본 트리): 찾기 9(부정 없음·창 안에 통째로만·창 8자·최대 3·중복·표 순서·label 안 나눔·띄어 쓴 꼴 허용·글 아닌 값 str 변환), 합치기 6(뒤 유형 우선·제외 없음·단 분류만·known 중복·글을 글자로·유형 없음에 조각), 103 규칙 회귀 5, 캐스팅 16(descriptionKo 읽음·저장 blType 읽음·끔 무시·보기 103 키·로그 쉼표·로그 첫 유형만·회차 문장 유형 없음·label = id·보기 성별 unknown·male 강제 없음·중립 낱말·상한 무시·"and" 없음·유형 하나만·끔에도 보기 필드·PUT이 blType 저장), 라우트 2(PUT·draw 전달 되살림), 화면 11(늘 안내·저장 본문 BL·뽑기 본문 BL·103 선택 되살림·참고 문장 없음·안내 큰 글씨·끔에 줄·innerHTML·label 대신 id·" / " 잇기·줄 자리 옮김), CSS 2, 배선 3, 운영 문구 2. 56개 모두 검사 단언으로 잡았고(예외로 잡힌 것 0) 변형 없는 실행은 통과한다. 처음 돌렸을 때 K8(띄어 쓴 꼴)은 변형이 덜 만들어져 예외로만 잡혀 두 자리 변형으로 다시 썼다.
  - 성능 측정(저장소 밖): `detect_types` 최악 입력(부정 반복 500자) 1회 0.055ms, 5,000자 0.014ms. `merged_pieces` 두 유형 0.081ms, `_cast_text` 두 유형 0.29ms. 보기 BL 필드 JSON 255바이트(103의 `blTypes` 목록이 빠져 약 3.6KB에서 줄었다). 가장 긴 두 유형 참고 문장 309자 → 캐스팅 문장 최장 약 1,170자(1500자 안).
  - 실제 외부 호출 0회: `casting._fal_request`·`_fetch_url`·`characters._fal_request`·`vpoc.fal._request`를 막고 `_guard_real_llm`으로 Bedrock 0회, 뽑기 스레드·빌드 스레드는 기록기로 바꿨다.
  - 줄 끝 LF(바뀐 파일 12개 CR 0, BOM 없음), 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 정의 0(파이썬 AST·JS 함수·CSS 선택자 비교, 검사 파일의 도우미 `apply`·`type_opts`도 남김), 저장소 안 새 파일 0. 시각만 바뀐 파일은 `.gitignore` 대상 `__pycache__` 3개와 실행 로그 `.kiro/handoff/logs/104.log`뿐이다. 수정 금지·범위 밖 파일(`characters.py`·`generate.py`·`config.dev.env`·`config.prod.env`·`.gitlab-ci.yml`·`settings.py`·`config.env.example`·`workloads.yaml.tmpl`·`vpoc/errors.py`)은 바뀌지 않았다(`whynot-20261029`는 이 작업 트리에 없다).
- **바꾼 기존 검사 기대값**
  - cast-j `want_keys`: BL 5개(`blRole`·`blType`·`blRoles`·`blTypes`·`blHintsEn`) → 2개(`blDetected`·`blHintsEn`). 이유: 지시서 104가 보기에서 103 키를 빼고 `blDetected`를 두라고 했다.
  - bl-f·bl-h·bl-i·bl-j의 103 기대값(PUT·draw의 BL 400, 저장된 역할·유형이 회차 문장에 쓰임, 보기 5개 키, 화면 선택·미리보기 숨김·본문의 BL 키, 라우트의 `BadBlType` 문구): 지시로 없어진 동작이라 104 기대값으로 바꿨다. 103 함수 자체의 동작(20경우 중 12경우)은 남긴 정의를 직접 불러 계속 본다.
  - 그 밖의 기존 검사 기대값은 바꾸지 않았다(cast-a~m·cf-*·rw-*·ob-*·lr-* 그대로. cast-l·cf-s는 BL 필드가 없는 상세라 줄이 없다).
- **3단계 리뷰**
  - 보안
    - 사용자 글은 판정에만 쓴다. 모델에 가는 영어는 여전히 표의 고정 조각뿐이라 설명 글이 캐스팅 문장에 섞이지 않는다(프롬프트 끼워 넣기 경로 없음). `detect_types`는 글이 아니면 `[]`이고 정규식은 이름 13개의 이스케이프 리터럴 묶음이다.
    - 화면 글은 `el`(textContent)로만 넣는다. 위험한 글을 label·참고 문장에 넣어도 글자 그대로다(bl-i). 권한·저장 경로(`require_auth`, `users/{owner}/…`)는 그대로다.
    - 발견·조치: 없음. `BadBlType` 문구는 표에 남았고 지금은 던지는 경로가 없다(bl-j).
  - 성능(앱 파드 512Mi)
    - 상세 GET(뽑는 중·만드는 중 5초 폴링)마다 `detect_types` 1회(0.06ms 이하)와 두 유형 합치기가 더해진다. 보기 BL 필드는 3.6KB → 255바이트로 줄었다. 새 스레드·락·외부 호출이 없고 fal 요청 수·금액은 같다.
    - 발견·조치: 없음.
  - 운영성
    - 로그: `vnew.casting.draw_start … bl=<id+id 또는 ->`. 400 `BadBlType`은 더 나오지 않는다.
    - 되돌리기: `config.<target>.env`에 `VNEW_BL_LOOK=false` 한 줄(보기·문장이 103 끔과 같다). 화면 선택으로 되돌리기는 이미지 되돌리기(103 저장값이 남아 있어 그대로 쓰인다).
    - 진행 중 작업·재시작: 회차 문장은 시작 때 한 번 만들어 저장한다. 진행 중 뽑기·이어하기는 그 문장을 쓴다.
    - 섞인 버전(롤링): 옛 파드(103)는 저장된 `blType`으로 문장을 만들고 103 키를 준다. 새 화면은 `blDetected`가 없으면 줄을 그리지 않는다(bl-i 103 꼴 응답). 옛 화면이 새 파드에 BL 값을 보내도 무시된다(bl-f). 오류 없음.
    - 발견·조치: 103 남은 이슈 2(미리보기 줄이 저장된 외모 문장 기준)는 그대로다(남은 이슈 4).
- **남은 이슈**
  1. 앞 경계를 보지 않아 유형 이름이 다른 낱말 안에 있으면 찾는다: "꽃수를 놓은 셔츠"(꽃 자수)의 꽃수, "광공업"·"형광공장"의 광공, "떡대공원"의 떡대공. 추가 요청 칸은 그림 요청("후드티 말고 …")을 적는 곳이라 "꽃수"(자수)가 들어올 수 있다. 막으려면 이름별 예외 낱말("꽃수를 놓"·"꽃수가 놓") 또는 앞 경계 규칙이 필요하다(앞 경계는 "집착광공"을 놓친다). 결정이 필요하다.
  2. 103 함수 4개(`_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`)·화면 함수 4개·CSS 선택자 4개를 quality-week 9에 따라 남겼다. 부르는 곳이 없다. 지우려면 승인이 필요하다. 저장된 `blRole`·`blType`도 그대로 남아 있다.
  3. `vnew/settings.py`·`deploy/config.env.example`·`k8s/workloads.yaml.tmpl`의 103 주석("BL 역할·유형을 고르면")이 이제 맞지 않는다. 이번 범위 밖이라 두었다.
  4. 103 남은 이슈 2·5·6·7(미리보기 줄은 저장된 외모 문장 기준, female + 유형, male 지정 + 중립 낱말, 유료 확인)은 그대로다. female 지정 + 설명의 유형이면 "She has … masculine …"이 된다.
  5. 같은 캐릭터에 공 유형과 수 유형을 함께 적으면("떡대공 미인수") 둘 다 합친다. 역할이 다른 두 유형의 짝은 보지 않는다(지시에 규칙이 없다).
  6. 부정 판정은 이름 바로 뒤 6자만 본다. "떡대공 같은 느낌은 전혀 아님"처럼 먼 부정은 찾은 것으로 본다(지시 규칙 그대로).
- **피드백(같은 날, Claude): 104 남은 이슈 1·2·3을 고친다.** 104 결과는 확인됐고 커밋 전이다. 같은 작업 트리에서 이어서 했고, 104 직후 사본(바꿀 파일 16개)과 파일 목록을 `%TEMP%\kiro104f_baseline`에 두었다. 스펙에 R9.8~R9.10·R13·R14, design D28~D32·§10, tasks 15~18을 먼저 더했다.
  - 결정(Claude): 앞 경계(이름 바로 앞 글자가 한글 음절이면 찾지 않음, 예외 접두어 "집착"), 뒤쪽 제외(모든 이름은 바로 뒤 첫 글자 "원"·"업", 꽃수는 뒤 6자 안의 "놓"·"자수"·"무늬"·"장식"·"프린트"·"패턴"·"그림"), 부르는 곳이 없는 103 정의 지우기(승인), `VNEW_BL_LOOK` 주석 고치기.
  - 결정(Kiro)
    - G1 — 앞 경계는 한글 음절(U+AC00~U+D7A3)만 막는다. 자모("ㅋㅋ떡대공")·한자·이모지 뒤는 공백·문장부호·숫자·영문과 같이 찾는다. 지시의 규칙 문장이 "한글 음절이면 찾지 않는다"이고, 허용 목록으로 읽으면 "ㅋㅋ떡대공이다"를 놓친다(D28).
    - G2 — 바로 앞이 "집착"으로 끝나면 그 앞 글자는 보지 않는다. "초집착광공"도 찾는다(D28, 변형 F5가 이 판단을 고정).
    - G3 — "원"·"업"은 바로 뒤 첫 글자만 본다(지시 "바로 뒤 첫 글자로 붙는 경우"). 그래서 "떡대공인데 원래"·"떡대공 업무"는 찾고 "떡대공업무"는 뺀다(D29).
    - G4 — "수원 출신 무심수아"는 무심수를 찾는다. "수원"은 유형 이름이 아니라 아무것도 내지 않고, "무심수" 뒤의 "아"는 이름과 조사를 구별하지 않는다는 지시대로 찾는 쪽에 두었다. 지시는 이 예를 "찾지 않음"에 넣었지만 바로 아래 줄이 "찾는 쪽으로 두고 남은 이슈에 적는다"라서 그 줄을 따랐다(bl-k에 이 판단을 고정, 남은 이슈 1).
    - G5 — 지운 103 함수만 쓰던 문구 상수 `_MSG_BAD_BL`·`_MSG_BL_MISMATCH`(casting.py)와 `CASTING_BL_NO_TYPE`(vnew.js)도 함께 지웠다. 함수 정의가 아니라 승인 목록에 이름이 없지만, 남기면 쓰는 곳 없는 문구가 된다(D31). `vpoc/errors.py`의 `BadBlType`(지시서 104 "표에서 지우지 않는다"), `genre_bl`의 `roles`·`fits`·`list_types`(승인 목록 밖, bl-a가 본다), 저장된 `blRole`·`blType`과 `_empty_casting` 기본값은 남겼다.
    - G6 — 세 파일 주석은 줄 수·값·자리·base의 되돌리기 문구를 그대로 두고 뜻만 바꿨다(D32). 배선 검사(bl-g·rw-a~c·ob-h)의 기대값은 그대로다.
  - 검토한 대안
    - (가) 앞 경계를 허용 목록(공백·문장부호·숫자·영문만)으로: 자모·한자 뒤를 놓친다. 하지 않았다(G1).
    - (나) "원"·"업"도 6자 창으로: "떡대공인데 원래 착하다"·"떡대공이고 업무"를 놓친다. 지시 문장("바로 뒤 첫 글자")과도 다르다.
    - (다) 조사 표로 "무심수아"(이름)와 "무심수인"(조사)을 구별: 서술어 활용까지 표가 커진다. 지시대로 하지 않았다.
    - (라) 지운 함수의 상수를 남기기: 쓰는 곳 없는 문구가 남는다. 지웠다(G5).
  - 지운 정의(104 직후 사본과 AST·함수·선택자 비교)
    - `app/src/vnew/casting.py`: 함수 `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`, 상수 `_MSG_BAD_BL`·`_MSG_BL_MISMATCH`, 그 구역 머리 주석(104 기준으로 다시 씀).
    - `frontend/public/vnew.js`: 함수 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields`(안의 `fillTypes`·`showHint` 포함), 상수 `CASTING_BL_NO_TYPE`.
    - `frontend/public/vnew.css`: `.vnew-casting-row`, `.vnew-casting-bl-role`·`.vnew-casting-bl-type`, `.vnew-casting-bl-hint`.
    - `tools/check_vpoc.py`(지운 정의를 직접 부르던 부분만): bl-f의 103 함수 동작 12경우와 도우미 `apply`, bl-i의 남긴 103 화면 함수 실행(`kept103`)과 `_bl_screen_problems`의 도우미 `type_opts`. bl-j의 던지는 곳 기대값(103 함수 4곳 → 0곳).
    - 104에서 새로 만든(커밋 전) `genre_bl._NEGATION_SPAN`도 지웠다. `_starts_within`이 낱말 길이로 창 끝을 정해 필요 없다.
    - 그 밖에 지운 정의는 없다(hygiene: 승인 목록과 정확히 같음).
  - 바꾼 것
    - `app/src/vnew/genre_bl.py`: 새 `_HANGUL_SYLLABLE`·`_PREFIX_OK`·`_NEXT_BLOCK`·`_AFTER_BLOCK`, 새 `_starts_within`·`_front_ok`·`_excluded_after`, `_negated`(`_starts_within`로), `detect_types`(자리마다 앞 경계·뒤쪽 제외), 찾기 구역 주석.
    - `app/src/vnew/casting.py`: 위 지운 정의, BL 구역 머리 주석.
    - `frontend/public/vnew.js`·`vnew.css`: 위 지운 정의, BL 주석 한 줄.
    - `app/src/vnew/settings.py`(주석 2줄)·`deploy/config.env.example`(주석 3줄)·`k8s/workloads.yaml.tmpl`(주석 1줄): "캐릭터 설명(추가 요청)에 적힌 BL 유형을 참고 문장으로 더한다". 값·자리·줄 수 그대로.
    - `tools/check_vpoc.py`: bl-f(AST: 103 함수·상수가 없고 "blRole"·"blType"을 쓰는 함수는 `_empty_casting`뿐), bl-g(세 주석), bl-i(지운 화면 함수·상수·CSS 선택자가 없음), bl-j(던지는 곳 0, 봉투는 고정 문구 글자로), bl-k(`_BL_DETECT_CASES` 24경우 추가·"떡대공원" 기대값 변경, 표 3개, 이름 13 × 31꼴, 꽃수 창 경계 8경우), docstring·보고 줄.
    - `.kiro/specs/bl-look/`(R9.8~R9.10·R13·R14, D28~D32·§10, tasks 15~18), `docs/ANALYSIS.md`(머리 줄, 운영 문구 단락, BL 표 근거·합치기·설정 저장·뽑기 문장·보기·화면 행 줄 번호와 내용, §7.1 단락, §10.1 검사 행).
  - 검사(바뀐 것, 어떤 검사가 어떤 함수·분기를 덮는지)

    | 검사 | 덮는 것 |
    |---|---|
    | bl-k | `_front_ok` 세 분기(글 처음·한글 음절 아님·`_PREFIX_OK`)와 거절, `_excluded_after` 세 분기(`_NEXT_BLOCK` 첫 글자·`_negated`·`_AFTER_BLOCK`), `_starts_within` 창 경계. 지시 예 9개(꽃수를 놓은 셔츠·꽃수 무늬 넥타이·광공업·떡대공원 → 없음, 수원 출신 무심수아 → 무심수(G4), 집착광공이다·그는 꽃수다·(떡대공)·떡대공,무심공 → 찾음)와 형광공장·개떡대공·떡대공무심공·초집착광공·ㅋㅋ떡대공이다·BL떡대공·1.떡대공·수원 출신·떡대수원·떡대공업무·떡대공 업무·떡대공인데 원래·떡대공이고 줄무늬·꽃수인데 … 그림·무심수다·무심수인. 이름 13개 × 31꼴(한글 음절 앞 2·"원"·"업" 4 → 없음, 집착·공백·괄호·숫자·영문·자모·가운뎃점 앞·" 원래"·"이고 업무"·조사 2 → 찾음, 꽃수 낱말 7개 × 2꼴 → 꽃수만 없음), 꽃수 창 경계(0~5번째 = 뺌, 6·7번째 = 찾음), 표 3개 값 |
    | bl-f | casting.py AST: 103 함수·상수 6개가 정의·사용 모두 없고 모듈 속성도 없음, "blRole"·"blType" 글자를 쓰는 함수는 `_empty_casting`뿐. PUT·draw·설명 근거는 104 그대로 |
    | bl-g | 세 파일 `VNEW_BL_LOOK` 주석 줄(settings 2·base 3·템플릿 1)이 모두 주석이고 "캐릭터 설명"·"BL 유형"·"참고 문장"이 있고 "고르"·"역할·유형"이 없음. 배선·렌더는 그대로 |
    | bl-i | vnew.js에 지운 함수 4개·상수 1개 이름이 없고 vnew.css에 지운 선택자 4개가 없음. 화면 동작은 104 그대로(103 꼴 응답에서도 선택 0) |
    | bl-j | `app/src` 전체 AST에서 "BadBlType"을 인자로 받는 호출 0, 표 한 줄, `RAW_ERRORS=false` 봉투 문구, 라우트 200 |

  - 확인
    - `node --check frontend/public/vnew.js` 통과. `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 63줄(bl 그룹 한 줄에 bl-a~l), FAIL·WARN 0(87초).
    - 바뀐 서버 줄 커버리지(coverage 7.17, `--data-file=%TEMP%\kiro104f_baseline\covf.data`, 104 직후 사본과 difflib): 실행문 15줄 중 15줄(100%). `genre_bl.py` 15/15(파일 전체 100%), `casting.py`·`settings.py`는 바뀐 줄이 주석이라 실행문 0줄(casting.py 파일 전체 99.4%, 못 간 5줄은 이번에 바꾸지 않은 `_sleep`·`_spawn`·`_poll_slot`).
    - 변형 검사 54개(저장소 밖 `kiro104f_mut.py`. 파이썬 변형은 모듈을 다시 실행하고 바뀐 소스를 임시 트리에도 써서 AST 검사도 보게 했다): 찾기 9, 앞 경계 6(경계 없음·접두어 없음·자모를 음절로·영문 등 모든 글자를 막음·접두어 앞까지 다시 봄·두 글자 앞), 뒤쪽 제외 7(원·업 없음·원·업을 창 전체로·업 없음·꽃수 낱말 없음·꽃수 낱말을 모든 이름에·낱말 하나 빠짐·바로 뒤만), 합치기·그림 우선 회귀 7, 캐스팅 회귀 6, 지운 정의 되살림 8(서버 함수·상수·던지는 곳·화면 선택·화면 함수·화면 상수·CSS 2), 라우트 1, 주석 4(settings·base·템플릿 옛 문구·base 줄 합침), 배선 3, 운영 문구 표 1, 화면 2. 54개 모두 검사 단언으로 잡았고(예외로 잡힌 것 0) 변형 없는 실행은 통과한다.
    - 성능(저장소 밖): `detect_types` 최악 입력(꽃수 제외 반복 500자) 1회 0.23ms, 부정 반복 0.094ms, 한글 앞 이름 반복 0.12ms, 5,000자 0.015ms.
    - 줄 끝 LF(바뀐 파일 CR 0, BOM 없음), 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 정의 = 위 목록과 정확히 같음, 저장소 안 새 파일 0. 시각만 바뀐 파일은 `.gitignore` 대상 `__pycache__` 3개와 실행 로그 `.kiro/handoff/logs/104b.log`뿐이다. 수정 금지 파일(`characters.py`·`generate.py`·`config.dev.env`·`config.prod.env`·`.gitlab-ci.yml`·`vpoc/errors.py`)과 `routes.py`는 바뀌지 않았다.
  - 바꾼 기존 검사 기대값
    - bl-k "떡대공원": `["ttokdae_gong"]` → `[]`. 이유: Claude 결정 뒤쪽 제외("원"이 바로 뒤 첫 글자).
    - bl-j 던지는 곳: casting.py 고정 문구 상수 둘 × 4곳 → 0곳. 이유: 던지던 103 함수를 지웠다(승인).
    - bl-f의 103 함수 동작 12경우와 bl-i의 103 화면 함수 실행은 지운 정의를 직접 불러 뺐다. 그 밖의 기존 검사 기대값(cast-a~m·cf-*·rw-*·ob-*·lr-*·bl-a~e·bl-h·bl-l)은 그대로다.
  - 3단계 리뷰
    - 보안: 찾기는 여전히 판정에만 쓰고 모델에 가는 영어는 고정 표 조각뿐이다. 새 정규식은 한 글자 범위 하나이고, 앞 경계·뒤쪽 제외는 `str.find`·`endswith`의 고정 낱말 비교다. 운영 문구 표에 남은 `BadBlType`은 던지는 곳이 없어 사용자에게 보일 일이 없다. 발견·조치: 없음.
    - 성능: 자리마다 비교가 몇 번 늘었고 최악 입력 0.23ms(설명은 500자 상한). 지운 코드만큼 vnew.js가 3,680바이트 줄었다. 외부 호출·메모리 변화 없음. 발견·조치: 없음.
    - 운영성: 같은 설명이라도 이번 판정으로 결과가 바뀔 수 있는 꼴은 "한글 음절 + 이름"(예 "개떡대공", "떡대공무심공"의 무심공)과 이름 바로 뒤 "원"·"업", 꽃수 + 자수 낱말뿐이다. 진행 중 뽑기는 시작 때 만든 문장을 쓰므로 영향이 없다. 되돌리기는 그대로 `VNEW_BL_LOOK=false` 한 줄이고, 103 화면 선택으로 되돌리려면 이제 103 이미지로 되돌려야 한다(지운 함수가 없어 설정으로는 안 된다). 발견·조치: 없음.
  - 남은 이슈(이번)
    1. 뒤에 한글이 붙으면 이름과 조사를 구별하지 않아 찾는다(G4): "무심수아"(사람 이름)·"꽃수진"도 찾는다. 반대로 "꽃수인데 그림체는 …"처럼 꽃수 뒤 6자 안에 "그림"·"패턴"이 오면 꽃수가 빠진다.
    2. 앞 경계 때문에 붙여 쓴 두 유형("떡대공무심공")은 앞 유형만 찾는다. "개떡대공"처럼 꾸밈말을 붙여 쓴 꼴도 뺀다.
    3. 운영 문구 표의 `BadBlType`과 `genre_bl`의 `roles`·`fits`·`list_types`는 이제 앱 코드에서 쓰는 곳이 없다(bl-a만 본다). 지울지는 승인이 필요하다. bl 그룹 안의 103 검사 도우미 `err_of`도 쓰는 곳이 없지만 지운 정의를 부르지 않아 두었다.
    4. 저장된 `blRole`·`blType`과 `_empty_casting` 기본값은 그대로다(D22).
    5. 104 남은 이슈 4~6(female 지정 + 유형, 공·수 유형 함께, 먼 부정)은 그대로다.

### 지시서 105 — 운영 503: frontend·app 파드를 ALB가 쓰는 AZ에만 띄운다 `POD_ZONES` (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/105-pod-zone-pin.md`. 스펙 `.kiro/specs/pod-zone-pin/`(requirements R1~R6·design D1~D9·tasks 1~5)을 먼저 쓰고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았다. 클러스터 상태는 지시서가 준 사실만 썼다.
- **먼저 한 일**: 바꿀 파일과 수정 금지 파일 10개의 시작 전 사본, 변경 전 렌더 결과(dev·prod 템플릿 18개씩, 메모리 렌더)를 저장소 밖(`%TEMP%\pz105`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 63줄, FAIL·WARN 0, 94초).
- **원인**: 2026-10-31 운영 배포(ci-4d408e98) 직후 `https://whynot.meta-clouds.com/`이 약 7분 503이었다. 운영 ALB `whynot-prod-alb`는 2a·2c 서브넷만 쓴다(2b는 10/02 EIP 한도로 뺐다). Ingress가 `target-type: ip`라 ALB는 자기 AZ의 파드만 대상으로 쓴다. 운영 노드는 2a·2b 두 대이고 2c 노드는 없는데, 두 Deployment에는 노드 제약이 없어(hostname 분산 `ScheduleAnyway`만) 새 frontend 파드 2개가 모두 2b 노드에 놓였다. 그 파드는 `Target.NotInUse`라 `/` 대상이 0이 됐다. `/api/health`는 2a의 app 파드가 받아 200이라 놓치기 쉬웠다. `config.prod.env`의 "Safe because both target groups have all their healthy targets in 2c" 주석은 지금 사실과 달랐다.
- **결정**(design D1~D9)
  - D1 — 렌더러(`deploy/render.py`)를 고치지 않는다. 템플릿은 흐름 목록 `values: [${POD_ZONES}]`, 설정값은 따옴표·공백 없는 쉼표 목록이다. 렌더 결과 `values: [ap-northeast-2a,ap-northeast-2c]`는 YAML 문자열 2개 목록이다(PyYAML로 확인). 이유: `render.py`는 `PLATFORM_FILES.txt`의 플랫폼 파일이라 고객 디렉터리에서 직접 고치지 않는 규칙이고(반입 뒤 한 번도 안 바뀜), 새 파생값이 없으니 다른 템플릿 결과가 그대로다. 렌더러가 값 형식을 보지 않는 빈틈(빈 값이면 `values: []` → ArgoCD 동기화 실패, 끝 쉼표는 YAML이 조용히 받음)은 CI `check:whynot`의 새 검사 pz-b가 build·deploy 앞에서 막는다(`needs` 사슬).
  - D2 — 필수(`requiredDuringScheduling…`) 제약. 맞는 노드가 없으면 Pending이고 `maxUnavailable: 0`이라 롤링이 멈출 뿐 옛 파드가 계속 받는다.
  - D3 — 두 Deployment 파드 spec의 `topologySpreadConstraints` 바로 앞에 주석 + affinity 8줄. 분산 블록은 글자 그대로 둔다.
  - D4 — `POD_ZONES`는 두 파일 모두 `PUBLIC_SUBNET_IDS` 바로 다음 키(둘이 짝이다). base `ap-northeast-2a,ap-northeast-2b,ap-northeast-2c`(개발 ALB 2a·2b·2c), prod `ap-northeast-2a,ap-northeast-2c`. `config.dev.env`는 그대로다.
  - D5 — prod 서브넷 주석은 영어 블록이라 영어로 고쳤다(target-type ip·`Target.NotInUse`·2c 노드 없음·10/31 503·2b를 다시 넣으면 같은 변경에서 `POD_ZONES`에 2b). EIP 경고 줄과 문서 참조 줄은 그대로. BOM·LF·끝 두 줄(ob-d 기대값) 유지.
  - D8 — prod `PUBLIC_SUBNET_IDS` 개수 = `POD_ZONES` 개수(ALB는 AZ마다 서브넷 1개)를 검사한다. 2b 서브넷만 되살리면 CI가 실패해 같은 변경에서 `POD_ZONES`를 고치게 한다.
  - D9 — `docs/dev-migration/eip-quota-analysis.md`는 이 저장소에 없어 만들지 않았다. 같은 안내는 `config.prod.env` 주석과 pz-a가 맡는다.
- **검토한 대안**
  - (가) 렌더러 파생값 `POD_ZONES_FLOW`(항목마다 따옴표 + 렌더할 때 형식 검사): 실패가 렌더 단계에서 더 일찍 나지만 플랫폼 파일을 고친다. 거절.
  - (나) 렌더러가 여러 줄 블록 목록을 만든다: 렌더러가 템플릿 들여쓰기를 알아야 한다. 거절.
  - (다) 설정값을 `"ap-northeast-2a", "ap-northeast-2c"`로: config의 "따옴표 없이" 규칙과 어긋난다. 거절.
  - (라) `preferred…`(점수만): 2a가 꽉 차거나 죽으면 2b로 가서 다시 NotInUse. 거절.
  - (마) Ingress `target-type: instance`(kube-proxy가 다른 AZ 파드로 넘김)·ALB에 2b 다시 넣기·2c 노드 추가: Ingress 수정 금지·AWS 자원 불변(사용자 결정). 남은 이슈에 근본 대안으로 적었다.
- **바꾼 것**
  - `k8s/workloads.yaml.tmpl`: frontend(주석 2줄)·app(주석 1줄) 파드 spec에 affinity 8줄씩. 추가 19줄, 지운 줄 0.
  - `deploy/config.env.example`: `PUBLIC_SUBNET_IDS` 뒤 주석 5줄 + `POD_ZONES=…2a,…2b,…2c`.
  - `deploy/config.prod.env`: 서브넷 주석 6줄 → 12줄(사실대로), `PUBLIC_SUBNET_IDS` 뒤 주석 3줄 + `POD_ZONES=…2a,…2c`.
  - `tools/check_vpoc.py`: 새 상수 `_PZ_PROD_ZONES`·`_PZ_BASE_ZONES`·`_PZ_ZONE_RE`·`_PZ_AFFINITY_BLOCK`·`_PZ_CI_GREP`·`_PZ_BAD_VALUES`·`_PZ_GOOD_VALUES`, 새 함수 `_pz_zone_problems`·`_pz_spread_block`·`_pz_parent_path`·`_pz_flow_items`·`_pz_ci_hits`·`_pz_affinity_obj`·`_pz_has_key`·`check_pod_zones`, `main()` 끝 호출 한 줄.
  - `.kiro/specs/pod-zone-pin/`(새 3파일), `docs/ANALYSIS.md`(머리 줄, §7.4 행, §8.3 항목, 새 §8.7, §10.1, §11 R-22), 이 항목.
- **렌더 결과**(prod, frontend·app 같은 블록. dev는 `values: [ap-northeast-2a,ap-northeast-2b,ap-northeast-2c]`)

  ```yaml
        affinity:
          nodeAffinity:
            requiredDuringSchedulingIgnoredDuringExecution:
              nodeSelectorTerms:
                - matchExpressions:
                    - key: topology.kubernetes.io/zone
                      operator: In
                      values: [ap-northeast-2a,ap-northeast-2c]
        topologySpreadConstraints:
  ```

- **검사(새 그룹 `check_pod_zones`, 어떤 검사가 무엇을 덮는지)**

  | 검사 | 덮는 것 |
  |---|---|
  | pz-a | base·prod `POD_ZONES=` 줄(값·`render.load_config` 값·`PUBLIC_SUBNET_IDS` 다음 키·주석의 `PUBLIC_SUBNET_IDS`·`503`·BOM·CR), prod 서브넷 주석(옛 "Safe because" 없음·`POD_ZONES`·`Target.NotInUse`·no 2c node·`ap-northeast-2b`·EIP 경고 줄), prod 서브넷 수 = 영역 수, dev 오버레이에 키 없음. 함수 `comment_above` |
  | pz-b | `_pz_zone_problems` 전 분기(빈 값·빈 항목·공백·꼴·중복·통과): base·prod 값 문제 0, 나쁜 값 20개의 첫 분류, 좋은 값 3개 |
  | pz-c | 템플릿 `${POD_ZONES}`·`values: [${POD_ZONES}]`·8줄 블록 각 2번, 블록 자리(들여쓰기·바로 뒤 분산·바로 앞 주석), 문서 kind 순서·affinity는 두 Deployment에만, 분산 블록 2개 글자 그대로(`_pz_spread_block`) |
  | pz-d | `render.add_derived`·`render_text`로 prod·dev의 k8s 템플릿 5개를 렌더(base에 없는 오버레이 키 거절 분기 포함). Deployment마다 affinity 1개·부모 경로 `spec/template/spec`(`_pz_parent_path`)·블록 글자·`values` 목록(`_pz_flow_items`)·바로 뒤 분산, 다른 문서·다른 k8s 결과에 affinity 없음. PyYAML이 있으면 `safe_load_all`로 affinity·분산 dict와 다른 문서의 affinity 키(`_pz_affinity_obj`·`_pz_has_key`) |
  | pz-e | 저장소 밖 임시 트리에서 CI와 같게(`IMAGE_TAG`만 바꾼 `config.env`) `render.py --target prod|dev` CLI: 종료 0·파일 prod 4·dev 3·CI 미치환 판정 0줄(`_pz_ci_hits`, 판정 자체도 6줄 표본으로)·태그·CLI 결과 = 메모리 렌더(바이트). 음성: base에서 `POD_ZONES`를 빼면 prod는 오버레이 키 검사, dev는 없는 변수로 실패(문구까지). `.gitlab-ci.yml` grep 3줄이 두 벌 그대로. 저장소의 `deploy/rendered`·`deploy/config.env` 존재 여부 전후 같음 |

- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 64줄(기존 63줄은 글자까지 그대로 + pz 한 줄), FAIL·WARN 0, 96초. pz 그룹 단독 1.2~1.5초.
  - PyYAML을 숨긴 실행(CI와 같은 조건)도 통과("PyYAML 없음: 글자 블록·자리·값").
  - `python deploy/render.py --target dev`·`--target prod`(저장소 안, CI처럼 `config.env` = example + `IMAGE_TAG=ci-pz105local`): 둘 다 종료 0, 템플릿 18개, CI 미치환 판정 dev 3파일·prod 4파일 0줄, 태그 있음. 끝나고 `deploy/config.env`·`deploy/rendered/`를 지웠다(시작 전에도 없었다).
  - 변경 전·후 렌더 비교(dev·prod 각 18개): `workloads.yaml` 밖 17개는 바이트 같음. `workloads.yaml`은 추가 2묶음 19줄, 지운 줄 0.
  - 커버리지(coverage 7.17, `%TEMP%\pz105\cov.data`, 시작 전 사본과 difflib): `check_vpoc.py`의 바뀐 실행문 232개 중 통과 실행 215개(92.7%). 못 간 17개는 실패 분기(예: `bad.append`, 렌더 실패 `except`, `fail`)이고 변형 검사 실행과 합치면 232개(100%). `render.py`는 바꾸지 않았다(파일 전체 54%, CLI `main`은 하위 프로세스라 측정 밖).
  - 변형 검사 50개(저장소 밖 `%TEMP%\pz105\mutate.py`, 변형마다 새 임시 트리, 판정 함수는 모듈 속성 바꿔치기): 템플릿 16(블록 삭제 2·NotIn·region 키·preferred·값 고정·대괄호 없음·securityContext 아래·분산 DoNotSchedule·분산 삭제·Service에 affinity·주석 삭제·분산 뒤로·조건 추가·따옴표 한 덩어리·분산 이름), 설정 20(prod 2b 추가·빈 값·공백·중복·끝 쉼표·줄 삭제, base 줄 삭제·기본값 2a,2c·대문자·CRLF, dev 오버레이에 키, 2b 서브넷만 되살림, 옛 문구 되살림, BOM 삭제, CRLF, 줄 자리, 2e 오타, 2b 안내 삭제, base 주석 삭제, 2c 노드 문구 삭제), CI 기준 1, 렌더러 4(값 비움·CRLF 출력·없는 변수 허용·오버레이 키 검사 끔), 판정 함수 6(중복·공백·[a-z]·빈 항목·빈 값·search), CI 판정 1, 실패 경로 2(prod 설정 줄 형식 오류 → "읽지 못했습니다"·값 생략, `.gitlab-ci.yml` 없음 → 그 묶음만 예외·나머지 계속·"모든 묶음" 단언). 48개는 검사 단언으로, 실패 경로 2개는 의도한 경로로 잡혔다. 놓친 것 0. PyYAML을 숨겨도 48개 모두 잡힌다. 처음 돌렸을 때 3개가 샜다: base 키 삭제에서 메모리 렌더의 `SystemExit`가 검사 밖으로 나갔고(묶음 루프·메모리 렌더가 `SystemExit`도 받게 고침), CRLF 출력은 `read_text`가 줄 끝을 바꿔 못 봤고(바이트로 읽게 고침), 없는 변수 허용은 prod 음성이 오버레이 키 검사에서 먼저 멈춰 못 봤다(dev 음성 추가).
  - 줄 끝: 바뀐 파일 모두 CR 0, `config.prod.env`만 BOM(원래대로). 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 함수 정의 0(AST 비교). 시작 뒤 바뀌거나 생긴 파일(mtime 기준)은 위 6개, 스펙 3개, 이 실행 로그 `.kiro/handoff/logs/105.log`뿐이고 `__pycache__` 변화도 없다. 수정 금지 파일(`render.py`·`config.dev.env`·`ingress.yaml.tmpl`·`.gitlab-ci.yml`)은 시작 전 사본과 바이트가 같고 `app/`·`frontend/`에는 바뀐 파일이 없다(`whynot-20261029`는 이 작업 트리에 없다).
- **바꾼 기존 검사 기대값**: 없다. 기존 렌더 검사(rw-c·lt-i·ob-d·ob-h·tr·sr·fx·cast·bl-g)는 base를 그대로 읽어 `POD_ZONES`가 함께 들어가고 기대값은 그대로 통과한다.
- **3단계 리뷰**
  - 보안
    - 새 값은 AZ 이름뿐이고 비밀값·새 계정 ID가 없다(서브넷 ID는 원래 주석에 있던 것). 설정 파일은 이미 이미지·IAM 역할을 정하는 신뢰 경계 안이다. 렌더러가 값을 그대로 넣지만 값은 한 줄이라(`load_config`) 줄바꿈으로 다른 키를 만들 수 없고 흐름 목록 안 내용만 바뀐다. pz-b가 그 내용을 `ap-northeast-2[a-d]` 항목으로만 허용한다.
    - 검사의 하위 프로세스는 `sys.executable` + 고정 인자 목록(shell 없음), 저장소 밖 임시 폴더, 네트워크 없음. 렌더러 오류 문구에 설정 원문 줄이 섞이면 `safe`가 값을 빼고 출력한다(변형 E01로 확인).
    - RBAC·IAM·securityContext는 그대로다. 발견·조치: 없음.
  - 성능
    - 운영은 frontend·app 파드가 모두 2a 노드(m6i.2xlarge, 8 vCPU·32GiB) 한 대에 놓인다. 요청 합계는 두 Deployment가 함께 롤링할 때 최대 1.8 vCPU·3.4GiB(app 500m/1Gi × 3 + frontend 100m/128Mi × 3)다. 지시서 기준 기존 요청 CPU 11%·메모리 5%에 더해도 여유가 크다. 메모리 limit 합계는 평상시 16.5GiB, 롤링 중 최대 24.75GiB로 노드 메모리 안이다. 다만 app 두 개가 같은 노드에서 동시에 이어 붙이기(ffmpeg)를 하면 CPU limit(4 × 2)이 노드 8 vCPU를 나눠 쓴다(요청은 보장).
    - 앱 코드·외부 호출 변화 없음. 검사기 시간 +1.5초(94 → 96초). 발견·조치: 없음.
  - 운영성
    - 맞는 노드가 없으면 파드 이벤트에 "didn't match Pod's node affinity/selector"가 남고 롤링은 멈춘다(옛 파드가 계속 받는다). 검사 실패 문구는 결과·기대를 함께 보인다.
    - 되돌리기: 이전 커밋으로 운영 job, 또는 `config.prod.env`의 `POD_ZONES`에 AZ를 더하는 커밋. 진행 중 작업은 매 배포의 롤링과 같다(이번 변경이 더하는 재시작은 없다).
    - **2a 노드 1대가 죽으면**: frontend·app이 모두 Pending이 되어 `/`와 `/api`가 함께 503이다. 2b 노드로는 옮기지 않는다(옮겨도 NotInUse라 변경 전과 결과가 같다). 노드 교체 때 새 노드가 2b에 생겨도 같다. 대안: 노드 그룹에 2c 서브넷을 넣어 2c 노드를 두거나, EIP 한도를 늘려 ALB에 2b를 다시 넣고 `POD_ZONES`에 2b를 더한다.
    - 운영 배포 후에는 `/api/health`만이 아니라 `/`(화면)도 확인한다. 발견·조치: ANALYSIS §8.7에 확인 절차를 적었다.
- **남은 이슈**
  1. 운영 ALB 뒤 파드가 2a 노드 한 대에 모두 놓인다(단일 장애점, 변경 전부터의 위험). 2c 노드 또는 ALB 2b 복귀가 필요하다(AWS 자원 변경이라 결정 필요).
  2. ALB 대상 상태를 파드 준비에 묶는 pod readiness gate(네임스페이스 라벨 `elbv2.k8s.aws/pod-readiness-gate-inject`)가 없다. 있었다면 이번 롤링은 503 대신 멈췄을 것이다. 네임스페이스는 ArgoCD가 만들어 운영 변경이다.
  3. `config.prod.env`가 가리키는 `docs/dev-migration/eip-quota-analysis.md`가 이 저장소에 없다(참조만 남아 있다).
  4. 개발 노드의 AZ는 확인하지 않았다. 개발 노드가 2a·2b·2c 밖(2d)에 있으면 그 노드에는 더 놓이지 않는다(개발 ALB도 2d를 쓰지 않으므로 그곳 파드는 원래 대상으로 쓰이지 않는다).
  5. CI 검사 이미지에 PyYAML이 없어 pz-d의 YAML 파싱 부분은 로컬에서만 돈다. CI는 글자 블록·자리·값으로 본다.
  6. 렌더러는 값 형식을 보지 않는다. CI 밖에서 손으로 렌더하면 나쁜 값도 렌더된다(배포 경로는 CI뿐이라 `check:whynot`이 막는다).

### 지시서 106 — frontend·app 파드를 영역(AZ)마다 고르게 나눈다: 영역 분산 `topologySpreadConstraints` (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/106-zone-spread.md`. 스펙 `.kiro/specs/pod-zone-pin/`에 106 절(requirements R7~R11·design D10~D19·tasks 6~10)을 더하고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았다. 클러스터 상태는 지시서가 준 사실만 썼다.
- **먼저 한 일**: 바꿀 파일 9개와 수정 금지 파일 4개의 시작 전 사본, 변경 전 렌더(dev·prod 템플릿 18개씩, 메모리), 저장소 파일 mtime을 저장소 밖(`%TEMP%\pz106`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 64줄, FAIL·WARN 0).
- **사실(지시서)**: 2026-10-31 사용자 지시로 운영·개발 노드그룹에 2c 노드를 더해 노드는 운영 2a·2b·2c, 개발 2a·2b·2c 각 1대다. 그 뒤 운영 rollout restart에서 새 파드 4개가 모두 2c 노드에 놓였다. 분산 규칙이 hostname·`ScheduleAnyway`(점수만)뿐이고 이름 라벨만 보아 롤링 중 옛 파드까지 셌기 때문이다. Claude가 파드를 손으로 지워 2a·2c 하나씩으로 맞췄다.
- **결정**(design D10~D17)
  - D10 — 두 Deployment `topologySpreadConstraints`에서 hostname 항목 바로 뒤에 영역 항목(`topology.kubernetes.io/zone`, `maxSkew: 1`, `DoNotSchedule`, 이름 라벨 + `matchLabelKeys: [pod-template-hash]`)을 더했다. hostname 7줄 블록은 글자 그대로라 105 검사의 글자 비교가 그대로 맞는다. 스케줄러는 목록 순서와 무관하다.
  - D11 — `matchLabelKeys: [pod-template-hash]`로 같은 리비전 파드만 센다. 롤링 중 옛 ReplicaSet 파드를 세지 않는다.
  - D12 — `nodeAffinityPolicy`·`nodeTaintsPolicy`·`minDomains`를 적지 않는다(기본 Honor·Ignore·1). Honor라 운영 2b 노드(`POD_ZONES` 밖)는 영역 계산에서 빠진다. Ignore였다면 2a·2c 중 하나를 못 쓸 때 2b가 "0개" 도메인으로 남아 두 번째 파드가 Pending이었을 것이다. 템플릿 주석(frontend 5줄, app 1줄)에 이 점을 적었다.
  - D13 — 롤링(`replicas 2`·`maxSurge 1`·`maxUnavailable 0`)은 막히지 않는다: 첫 새 파드는 어디든, 두 번째 새 파드는 다른 영역으로 간다. 옛 파드를 세지 않으므로 옛 배치가 막지 않는다. 다른 영역 노드가 자원 부족·cordon·NotReady면 두 번째 파드가 Pending이고 롤링이 멈춘다(옛 1 + 새 1이 받는다, 600초 뒤 ProgressDeadlineExceeded).
  - D14 — 영역 하나가 없어질 때: 노드 객체가 없어지면 그 영역은 도메인에서 빠져 남은 영역에 2개가 뜬다(문서 "Known limitations", 확신 높음). 노드 객체가 NotReady·cordon으로 남으면 `nodeTaintsPolicy` 기본 Ignore라 그 영역을 계속 세어, 쫓겨난 파드를 대신할 파드는 Pending이고 남은 1개가 받는다(필드 의미 확신 높음, "종료 중 파드는 세지 않는다"는 스케줄러 구현 기억이라 중간). 지시서 문장("남은 영역에 2개")은 앞 경우에만 맞다. EKS에서 노드 객체가 지워지기까지의 시간은 미확인.
  - D16 — `config.prod.env` 서브넷 주석(영어)과 `POD_ZONES` 주석(한국어)의 "2c 노드 없음" 문장을 지금 사실로 고쳤다. 값·키 순서·BOM·LF·끝 두 줄은 그대로다(`render.load_config` 결과가 시작 전과 같음). base `POD_ZONES` 주석에 영역 분산 한 줄을 더했다.
- **검토한 대안**(D17)
  - (가) 영역 규칙 `ScheduleAnyway`: 점수만이라 쏠림을 못 막는다. 거절.
  - (나) 필수 `podAntiAffinity`(영역마다 1개): `replicas`를 늘리면 바로 Pending이고, 리비전을 구분하지 않으면 surge 파드가 Pending이다. 거절.
  - (다) `nodeTaintsPolicy: Honor`: NotReady·cordon 노드를 빼 D14의 Pending을 없애지만 노드 교체·drain 중 한 영역으로 몰리고 지시서 값 밖이다. 남은 이슈로 사용자 결정에 둔다.
  - (라) `minDomains: 2`: 영역 하나만 쓸 수 있을 때 Pending. 목표와 반대. 거절.
  - (마) hostname 규칙에도 `matchLabelKeys`, (바) 규칙을 끄는 설정 플래그: 지시서 밖. 하지 않았다.
- **바꾼 것**
  - `k8s/workloads.yaml.tmpl`: frontend·app 분산 목록에 주석 + 영역 항목 8줄씩. 추가 22줄, 지운 줄 0.
  - `deploy/config.prod.env`(주석만): 서브넷 주석 6줄 → 8줄, `POD_ZONES` 주석 3줄 → 3줄. `deploy/config.env.example`(주석만): 1줄 추가.
  - `tools/check_vpoc.py`: 새 상수 `_PZ_ROLLING`·`_PZ_ZONE_NOTE_WORDS`·`_PZ_ZONE_NOTE_FRONT`, 새 함수 `_pz_zone_spread_block`·`_pz_host_spread_obj`·`_pz_zone_spread_obj`·`_pz_zone_rule_place`·`_pz_flat_note`, `check_pod_zones` 안 `pz_f`·`pz_g`·`pz_h`와 묶음 루프·"모든 묶음" 목록·docstring·ok 줄, pz-a·pz-d 기대값(아래). difflib 기준 추가 173줄·지운 줄 10(지운 줄은 모두 같은 자리에서 고쳐 쓴 줄).
  - `.kiro/specs/pod-zone-pin/` 세 파일 끝에 106 절. `docs/ANALYSIS.md`(머리 줄, §7.4 행·근거, §8.7 원인 줄 "사고 당시"·남은 위험, 새 §8.8, §10.1 줄 수·pz 행, R-22), 이 항목.
- **렌더 결과**(prod frontend 분산 블록. app은 이름 `whynot-app`·주석 1줄, dev도 같은 블록)

  ```yaml
        topologySpreadConstraints:
          - maxSkew: 1
            topologyKey: kubernetes.io/hostname
            whenUnsatisfiable: ScheduleAnyway
            labelSelector:
              matchLabels:
                app.kubernetes.io/name: whynot-frontend
          # 영역 분산(지시서 106, 2026-10-31): … (주석 5줄)
          - maxSkew: 1
            topologyKey: topology.kubernetes.io/zone
            whenUnsatisfiable: DoNotSchedule
            labelSelector:
              matchLabels:
                app.kubernetes.io/name: whynot-frontend
            matchLabelKeys:
              - pod-template-hash
  ```

- **검사(어떤 검사가 무엇을 덮는지)**

  | 검사 | 덮는 것 |
  |---|---|
  | pz-f | 템플릿 두 Deployment 문서마다 `_pz_zone_rule_place`(hostname 블록 1개·영역 블록 1개·순서·사이 줄이 8칸 주석 1줄 이상·바로 뒤 `containers`), 주석 낱말(`_PZ_ZONE_NOTE_WORDS`·frontend `_PZ_ZONE_NOTE_FRONT`, `_pz_flat_note`), 파일 전체 zone·DoNotSchedule·ScheduleAnyway·matchLabelKeys 각 2번, 정책 키 0, 롤링 전제 `_PZ_ROLLING` 2곳. `_pz_zone_rule_place`의 모든 분기를 표본 6개(맞음·주석 없음·주석 아닌 줄·순서 뒤집힘·규칙 없음·두 번)로 |
  | pz-g | prod·dev 메모리 렌더(`add_derived`·`render_text`, 렌더 실패 분기 포함): Deployment마다 영역 규칙 자리·영역 항목 부모 경로 `spec/template/spec/topologySpreadConstraints`(`_pz_parent_path`)·분산 목록 1개·롤링 전제, `POD_ZONES` 서로 다른 영역 2개 이상. PyYAML이 있으면 분산 목록 = `[_pz_host_spread_obj, _pz_zone_spread_obj]`·selector·파드 라벨 이름·`replicas`·`strategy` dict |
  | pz-h | prod 서브넷 주석(옛 "only pods on the 2a node can serve" 없음, "one each in 2a, 2b and 2c"·"zone spread rule"·"different zones" 있음), prod `POD_ZONES` 주석("2c 노드가 없" 없음, "2a·2b·2c 한 대씩"·"영역 분산"), base `POD_ZONES` 주석("영역 분산") |
  | pz-a(바뀜) | 서브넷 주석의 "no 2c node"를 없음으로 기대 |
  | pz-d(바뀜) | YAML 분산 목록 기대값 `[hostname]` → `[hostname, zone]` |

- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 64줄, FAIL·WARN 0, 97초. 기존 63줄은 시작 전 트리(저장소 밖 사본)에서 돌린 시작 전 검사기의 출력과 글자까지 같고, pz 줄만 길어졌다. pz 그룹 단독 1.4~1.6초. PyYAML을 숨긴 실행(CI 조건)도 통과.
  - `python deploy/render.py --target dev`·`--target prod`(저장소 안, CI처럼 `config.env` = example + `IMAGE_TAG=ci-pz106local`): 둘 다 종료 0(템플릿 미해결 변수 0), 템플릿 18개, CI 미치환 판정 dev 3파일·prod 4파일 0줄, 태그 있음, 영역 규칙 2곳. 렌더 트리 전체에 남은 `${VAR}`는 그대로 복사되는 `infra/build-deploy.sh` 주석 1줄뿐이다(템플릿 아님, CI가 쓰지 않음, 원래부터). 끝나고 `deploy/config.env`·`deploy/rendered/`를 지웠다(시작 전에도 없었다).
  - 변경 전·후 렌더 비교(dev·prod 각 18개): `workloads.yaml` 밖 17개는 바이트 같음. `workloads.yaml`은 추가 22줄, 지운 줄 0.
  - 커버리지(coverage 7.17, 전체 검사기 `coverage run`, 시작 전 사본과 difflib): `check_vpoc.py`의 바뀐 실행문 87개 중 84개(96.6%). 못 간 3개는 pz-g 렌더 실패 `except` 분기이고 변형 검사 실행과 합치면 87개(100%).
  - 변형 검사 87개(저장소 밖 `%TEMP%\pz106\mutate106.py`, 변형마다 새 임시 트리, 판정·도우미 함수는 모듈 속성 바꿔치기): 105 변형 49개(서브넷 주석이 바뀐 C18은 새 글자로 맞추고, "(there is no 2c node)" 삭제였던 C20은 H01 되살림으로 바꿨다) + 106 새 변형 38개 — 영역 규칙 23(삭제 2·ScheduleAnyway·maxSkew 2·region·hostname 키·matchLabelKeys 삭제·다른 키·이름 바꿈·nodeAffinityPolicy Ignore·nodeTaintsPolicy Honor·minDomains·hostname 앞으로·주석 삭제·Honor 설명 삭제·hostname DoNotSchedule·hostname 삭제·두 번·키 겹침(API 거절 꼴)·들여쓰기·선택자 좁힘·2b 설명 삭제·NotReady 설명 삭제), 롤링 전제 3(maxSurge 2·maxUnavailable 1·replicas 3), 주석 7(옛 문구 되살림 3·새 사실 삭제 4), 설정 2(prod·base 한 영역), 도우미 3(`_pz_zone_rule_place` 주석 판정 끔·순서 안 봄, `_pz_zone_spread_block` 느슨하게). 모두 검사 단언으로 잡혔다. PyYAML이 있을 때 T10·T13(105)·Z20은 YAML이 깨져 pz-d 파싱 예외도 함께 나고(단언은 pz-c·pz-f·pz-g가 냄), E02는 의도한 예외 경로다. PyYAML을 숨기면 86개가 단언만으로, E02가 예외 경로로 잡힌다. 놓친 것 0.
  - 줄 끝: 바뀐 파일 모두 CR 0, `config.prod.env`만 BOM(원래대로). 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 함수 정의 0(AST 비교). 스펙 세 파일은 `insert` 도구가 CR을 넣어 LF로 되돌렸고, 원래 부분은 시작 전 사본과 바이트가 같다. 시작 뒤 바뀐 파일(mtime·크기)은 위 9개와 실행 로그 `.kiro/handoff/logs/106.log`뿐이고 새 파일 0이다. 수정 금지 파일(`render.py`·`config.dev.env`·`ingress.yaml.tmpl`·`.gitlab-ci.yml`)은 바이트가 같고 `app/`·`frontend/`에는 바뀐 파일이 없다(`whynot-20261029`는 이 작업 트리에 없다).
- **바꾼 기존 검사 기대값**: pz-a "no 2c node" 있음 → 없음(지시서 요구 4로 문장을 고쳤다), pz-d YAML 분산 목록 `[hostname]` → `[hostname, zone]`(요구 1로 항목을 더했다). 다른 기대값은 그대로다.
- **3단계 리뷰**
  - 보안: 바뀐 것은 스케줄링 규칙·주석·검사뿐이다. 새 설정 입력이 없고 템플릿의 새 줄은 상수와 기존 이름 변수뿐이다. 비밀값·새 계정 ID 없음. RBAC·IAM·securityContext 그대로. 검사기 새 코드는 파일 읽기와 메모리 렌더뿐(하위 프로세스·네트워크 추가 없음). 발견·조치: 없음.
  - 성능: 스케줄러 계산은 노드 3대 × 분산 항목 2개라 무시할 만하다. 앱 코드·메모리(512Mi)·외부 호출 변화 없음. 평상시 운영 노드 2a·2c에 app 1·frontend 1씩, 롤링 중 노드당 요청 최대 1.2 vCPU·2.25GiB(m6i.2xlarge). 검사기 pz 그룹 1.4~1.6초(105: 1.2~1.5초), 전체 97초. 발견·조치: 없음.
  - 운영성
    - 발견 1: 지시서의 "노드가 없어지면 남은 영역에 2개"는 노드 객체가 없어질 때만 맞다. 노드가 NotReady·cordon으로 남으면 두 번째 파드는 Pending이다(그동안 1개가 받는다). 조치: design D14·ANALYSIS §8.8·템플릿 주석에 적고, `nodeTaintsPolicy: Honor`를 남은 이슈로 두었다.
    - 발견 2: `config.prod.env`의 한국어 `POD_ZONES` 주석도 "2c 노드가 없어 … 모두 2a 노드에 뜬다"로 옛 사실이었다. 조치: 같이 고치고 pz-h로 고정했다.
    - 막힐 때 신호: Pending 파드 이벤트(분산 제약 불일치), 롤링 멈춤(옛 파드가 받는다), 600초 뒤 ProgressDeadlineExceeded. 검사 실패 문구는 결과·기대를 함께 보인다.
    - 되돌리기: 템플릿 커밋을 되돌린 파이프라인으로 운영 job. 설정 플래그는 두지 않았다. 진행 중 작업은 매 배포의 롤링과 같다(이 변경이 더하는 재시작은 없다).
- **남은 이슈**
  1. 영역 노드가 NotReady·cordon으로 남는 동안 쫓겨난 파드를 대신할 파드가 Pending이라 용량이 절반이다. `nodeTaintsPolicy: Honor`를 더할지는 사용자 결정이다(D17 (다)).
  2. 영역이 돌아와도 파드를 다시 나누지 않는다(descheduler 없음). 다음 배포까지 한 영역에 2개일 수 있다.
  3. EKS에서 인스턴스가 사라진 뒤 노드 객체가 지워지기까지의 시간, 영역 장애 때 노드그룹이 인스턴스를 바꾸는지는 확인하지 않았다(D14 경우 A·B의 갈림).
  4. 105 남은 이슈 2(pod readiness gate 없음)·3(`docs/dev-migration/eip-quota-analysis.md` 없음)·5(CI에 PyYAML 없음 — pz-d·pz-g의 YAML 부분은 로컬에서만 돈다)는 그대로다. 105 남은 이슈 1(2a 노드 한 대)은 2c 노드 추가와 이 변경으로 풀렸다.
  5. `docs/구조분석.md:204`(2026-09-03 문서)의 "앱·프론트는 topologySpreadConstraints로 2a/2c 노드에 분산한다"는 그때 기준 글이라 고치지 않았다(범위 밖).
  6. 운영 반영 확인(Deployment마다 파드 영역, Pending 0, `/`·`/api/health` 200)은 배포 뒤 운영자가 한다(design D18, 계획만).


## 2026-11-01

### 지시서 107 — 실사 캐스팅에서 그림체(웹툰·일러스트) 결과 막기: 사진 고정 문장·그림체 낱말·결과 검사와 1회 다시 뽑기 `VNEW_CAST_PHOTO_CHECK` (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/107-cast-photo-guard.md`. 스펙 `.kiro/specs/cast-photo-guard/`(requirements R1~R10·design D1~D11·tasks 1~7)를 쓰고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았다. 사용자 데이터는 읽지 않았다(지시서에 적힌 문장 조각만 썼다).
- **먼저 한 일**: 바꿀 파일과 수정 금지 파일(`generate.py`·`genre_bl.py`·`config.*.env`·`.gitlab-ci.yml` 등 15개)의 시작 전 사본과 저장소 파일 목록(크기·mtime)을 저장소 밖(`%TEMP%\cpg107`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 64줄, FAIL·WARN 0, 96초).
- **사실(지시서)**: 2026-11-01 운영 실사 캐스팅(범건우, 아이돌풍, 떡대공) 1회에서 후보 2장 중 1장이 웹툰 일러스트처럼 나왔다. 캐스팅은 Seedream 4.5 t2i에 글만 보낸다(본문 3키, RB3.1). 사진 단서는 문장 앞(프리셋)에만 있고 뒤에 웹툰 분석에서 온 긴 외모·BL 문장이 온다.
- **결정**(design)
  - D1 사진 고정 문장: `A real photograph of a real {noun} taken with a camera; not a drawing, illustration, anime, cartoon, painting or 3D render.` `{noun}`은 성별 낱말 표의 명사(man·woman·person, BL 유형 + unknown은 male로 바꾼 뒤). 두 프리셋 모두 맨 끝(BL 참고 문장 뒤), 설정과 상관없이 늘. 지시서 예문과 다른 점 셋: "webtoon"을 뺐다(B1 규칙 "캐스팅 문장에 webtoon 없음"·cast-a, 막으려는 그림체 이름을 끝에 두면 그쪽으로 끌 수 있다), "painting"을 더했다(프리셋 "not a painting"과 같은 낱말, 반실사 디지털 페인팅이 흔한 실패 꼴), 명사를 성별에 맞췄다(예문의 "person"이면 남성 문장 끝이 성 중립이 된다 — steering genre-bl, 구현 중 bl-d가 잡았다).
  - D2 외모 문장 그림체 낱말(`_no_drawing` 앞 단계, `fx.strip_drawing_words`는 그대로): manhwa·manhua·comic·comics·comic-book·chibi(각 "-style(d)" 꼴 포함)·art style·drawing style·cel-shaded/cel shading·line art/line-art/lineart·digital painting/art/artwork/illustration·stylized/stylised·semi-realistic·2D·flat-shaded/flat shading/flat colors/flat-colored·sketch/sketched/sketchy. 명사구가 구의 끝이면 앞 전치사구째 지우고("drawn in a clean manhwa style" → 없음, 낱말만 지우면 "in a clean"이 남았다), 지운 자리 "a, …"는 "a …"로. 더한 낱말이 없는 글은 107 전과 같다.
  - D3 1500자: 예산 = 상한 − 고정 문장 몫. 넘치면 BL 참고 문장 조각(끝에서부터) → 외모 단어 경계(`_fit_cast_head`) 순. 프리셋·주어·눈 색·고정 문장은 남는다. 운영 최대 1,270자라 실제로는 줄지 않는다.
  - D4~D6 검사: `casting._photo_check`(`characters.py`는 바꾸지 않았다). `vpoc.llm._converse` + 도구 `emit_photo_check {photo: bool, style: enum 8개}`, where `vnew.cast_photo_check`, max_tokens 400, 모델 `VNEW_CAST_PHOTO_CHECK_MODEL`(기본 = `VNEW_PEOPLE_COUNT_MODEL` 값, 지금 Fable 5.1, 코드 기본). 저장 JPEG(1536)를 1024로 줄여 보낸다(`_PHOTO_SEM` 안에서 디코드, LLM은 밖). photo는 bool·글 "true"/"false"만, style은 열거값만(로그에 그대로 남으므로). None이면 사진을 쓰고 경고(fail-open).
  - D7 칸 흐름: 그림체 + `redraws` 0 → 원장 1건 → 칸을 queued(`requestArn ""`, `redraws` 1)로 되돌려 다음 순회의 기존 `_submit_slot`이 같은 회차 문장으로 제출(본문 3키 그대로). 그림체 + `redraws` 1 → 원장 1건 → 그 칸만 failed "실사 사진이 아니라 그림체로 나와 이 칸을 버렸습니다. 다시 뽑아 주세요."(kind NotPhoto). 버린 사진은 메모리에서만 쓰고 S3에 두지 않는다. 다시 뽑지 않은 칸에는 `redraws` 키가 없다(106 칸 키 그대로). `reconcile`·`_stale`·`_expired`는 그대로이고 다시 뽑기 대기 칸에도 같게 적용한다.
  - D8 원장: 키 규칙 그대로 `vnew-cast-{cid}-{후보 id}`(요청 id sha256). 버린 사진도 같은 규칙으로 1건 → 원장 건수 = 결과 사진을 받은 fal 요청 수. 같은 요청은 재처리해도 1건. 상태를 바꾸기 전에 남긴다(claim을 잃어도 이어받은 실행이 같은 키로 남겨 이중 계상 없음). 10/30 char-fidelity "다시 그린 장도 원장"과 같은 뜻이고, `-retry` 접미사는 쓰지 않았다(같은 요청이 두 키로 남을 수 있다). 검사 LLM 비용은 기존 그림 검사처럼 원장에 없다.
  - D9 안내 한 줄: 둔다. 확인받은 금액(`confirmUsd`) 밖에서 칸당 $0.04가 더해질 수 있어서다. 켬이면 `view()` 끝에 `photoCheckNote`, 화면은 뽑기 버튼 바로 아래 12px 한 줄. `totalUsd`·확인창 문구는 그대로.
  - D11 설정: `VNEW_CAST_PHOTO_CHECK`(기본 true)를 settings·base(`VNEW_BL_LOOK` 바로 뒤, 되돌리기 주석)·템플릿 앱 env(같은 자리)·`PARITY_ENV_KEYS`에. 모델 키는 기존 캐스팅·검사 모델 키처럼 코드 기본이라 템플릿에 넣지 않고 주석에 적었다(템플릿에 넣으면 base에 모델 id를 글자로 적어야 해서 "그림 검사 모델을 따라간다"가 끊긴다). 진행 중 뽑기는 시작 때 읽은 값.
- **검토한 대안**: fal 본문에 negative_prompt 등 더하기(RB3.1·cast-m을 깨고 스키마 확인 불가, 기각), 예문 그대로 "webtoon" 넣기(D1, 기각), 프리셋 앞에도 사진 문장(프리셋 바뀜, 기각), 검사를 `characters.py` 공용 함수로(범위가 커짐, 기각), 늘 2장 뽑아 고르기(비용 2배, 기각), `_poll_slot` 안에서 바로 다시 제출(제출 경로 둘, 기각), fail-closed(지시서와 반대, 기각), 원장 키 접미사(D8, 기각), 안내 없음·확인 금액 2배(D9, 기각), 1536 그대로 검사(토큰 약 2배, 기각).
- **바꾼 것**
  - `app/src/vnew/casting.py`: 상수 `PHOTO_ANCHOR`·`_STYLE_HEAD`·`_STYLE_PHRASE`·`_STYLE_WORDS`·`_ARTICLE_COMMA`·`_MSG_NOT_PHOTO`·`_MSG_PHOTO_NOTE`·`_REDRAW_MAX`·`_PHOTO_CHECK_MAX_TOKENS`·`_PHOTO_CHECK_MAX_PX`·`_PHOTO_STYLES`·`_PHOTO_TOOL`·`_PHOTO_SYSTEM`·`_PHOTO_PROMPT`, 새 함수 `photo_anchor`·`_fit_cast_head`·`_check_jpeg`·`_photo_check`·`_photo_passes`, 고친 함수 `_no_drawing`(앞 단계)·`_cast_text`(예산·고정 문장·외모 줄이기)·`cast_prompt`(docstring)·`view`(끝 `photoCheckNote`)·`_poll_slot`(키워드 `photo_check`)·`run_draw`(설정 1회 읽기), `vpoc.llm` import. 추가 199줄·지운 줄 14(모두 같은 자리에서 고쳐 쓴 줄).
  - `app/src/vnew/settings.py`: `VNEW_CAST_PHOTO_CHECK`·`VNEW_CAST_PHOTO_CHECK_MODEL`(+7줄). `deploy/config.env.example`(+5줄), `k8s/workloads.yaml.tmpl`(+4줄). `frontend/public/vnew.js`(+2줄, `castingSection`), `vnew.css`(+6줄).
  - `tools/check_vpoc.py`: 새 그룹 `check_vnew_cast_photo_guard`(cpg-a~h)와 상수 `_CPG_*`·`_cpg_anchor`·`_CPG_SCREEN_NODE_CASES`, `_run_cast_photo_off`, `PARITY_ENV_KEYS` 1개, `main()` 배선, 기존 기대값(아래). 추가 890줄·지운 줄 19(고쳐 쓴 줄), 지운 함수 정의 0.
  - `.kiro/specs/cast-photo-guard/` 세 파일(새로), `docs/ANALYSIS.md`(머리 줄·§3.6 모듈 표·§3.6.1 B 보기 행·새 「실사 사진 검사·그림체 다시 뽑기」 절·§6.1.1·§7.1·§10.1), 이 항목.
- **검사(어떤 검사가 무엇을 덮는지)**

  | 검사 | 덮는 것 |
  |---|---|
  | cpg-a | `PHOTO_ANCHOR`·`photo_anchor`(성별 6값), `_cast_text`·`cast_prompt`: 두 프리셋 × 성별 3 × BL(없음·하나·둘) × 외모 6 = 108문장이 107 전 식(+ 참고 문장) + " " + 고정 문장(한 번)·≤1500·설정 켬/끔 같음, 운영 최대 1,270자(유형 144쌍 × 프리셋 2 × 성별 3, 참고 문장 줄지 않음), 긴 외모(`LOOK_MAX` 3000 → 1,495자: 참고 문장 빠짐·외모 단어 경계·눈 색·고정 문장 남음), `PROMPT_MAX` 줄이기 5경우(조각 하나·참고 문장 없음·외모 단어 경계·앞에서 자르기·고정 문장보다 작은 상한) — `_fit_cast_head` 세 분기 |
  | cpg-b | `_no_drawing` 더한 낱말 19경우(결과 고정, `_STYLE_PHRASE`·`_STYLE_WORDS`·`_ARTICLE_COMMA` 분기), 남길 낱말 12개, 더한 낱말이 없는 표본 197개 = 107 전 구현(테스트 안 기준 식), `look_sentence`·`prompt_look` 경로 |
  | cpg-c | settings 줄·주석·자리, 하위 프로세스 설정 읽기(없음·false·0·true·no·글자 / 모델 기본·덮어쓰기·빈 값 → `VNEW_PEOPLE_COUNT_MODEL`), 템플릿 한 쌍(`VNEW_BL_LOOK` 바로 뒤·주석 2줄·모델 키 없음), base(true·되돌리기 주석·모델 키 줄 없음), `PARITY_ENV_KEYS` 자리, `config.<target>.env` 그대로, render dev·prod true·+false |
  | cpg-d | `_photo_check`·`_check_jpeg`: 질문 글·사진 768×1024·도구 이름·스키마·required·모델 설정·where·max_tokens, 1024 이하 사진은 같은 바이트, 응답 10꼴(bool·"false"·" TRUE "·모르는 style → other·style 없음·photo 없음·"maybe"·숫자·list·None), 모델 오류(비밀 없는 경고)·깨진 사진 → None |
  | cpg-e | `run_draw`→`_poll_slot`→`_photo_passes`(가짜 fal·가짜 `_converse`가 받은 사진 색으로 판정·메모리 S3·가짜 원장): 실사(후보 1·원장 1·제출 1·칸 키 106), 그림체 → 실사(후보 1·원장 2·제출 2 같은 본문·`redraws` 1·쉼 5초 1번·S3 1장), 그림체 → 그림체(NotPhoto 문구·원장 2·S3 0·failed·오류 요약), 검사 오류·모양 틀림·그림체 → 오류(fail-open·경고 `photo=none`·`photo_check_failed`), 글 "false"(anime), 두 칸(다른 칸 그대로), 저장소 전체에 그림체 사진 0, 원장 필드, 로그 꼴·수준 |
  | cpg-f | 재개: submitted·`redraws` 1을 `detail_view`→`reconcile`로 이어받음 → 제출 0(세 번째 제출 없음)·NotPhoto, queued·`redraws` 1 → 중단 실패·제출 0, 900초 → 시간 초과·조회 0·검사 0, 검사 중 claim을 잃음 → 원장 1건·칸 그대로·`claim_lost` → 이어받은 실행이 같은 요청 → 같은 키 원장·다시 뽑기 |
  | cpg-g | 끔: 검사 호출 0·그림체 사진도 후보·원장 1·칸 키 = 106·`vnew.cast.` 로그 0·보기에 `photoCheckNote` 없음, 두 칸 |
  | cpg-h | `view` 켬 = 끔 + `photoCheckNote`(끝)·`totalUsd` 같음·캐스팅 끔 `{enabled:false}`, Flask 상세(켬·끔)·뽑기 202·회차 문장 끝, `vnew.js` `castingSection` node 가짜 DOM(뽑기 버튼 바로 뒤·class·끔/빈 값 없음·`<img onerror>` 글자 그대로·뽑는 중·innerHTML 0), CSS 12px, `node --check` |

- **바꾼 기존 검사 기대값(사진 고정 문장이 늘 붙어서, 요구 1)**: cast-a(male 전문, 84문장 끝 = 눈 색 + 고정 문장), cast-b(실제 분석 꼴 문장 끝), bl 기준 식 `old_prompt`를 `old_head` + `anchored`로 나눔, bl-c(male 전문), bl-d(조립 식·떡대공 전문), bl-e("다 빼도 넘침": 앞에서 자르기 → `… He is a Korean man, with dark brown eyes. <고정 문장>`), bl-f(회차 문장 2곳), bl-h·bl-i(문장 끝 = 참고 문장 + 고정 문장), bl-l(두 유형 전문). 모두 12곳이고 다른 기대값은 그대로다.
- **끔 고정한 기존 검사(새 기본값 켬에서 기대값이 달라짐, 본문·기대값 그대로)**: `check_vnew_casting`(cast-f~h `run_draw`가 검사 LLM을 부름·cast-j 보기 키), `check_vnew_bl_look`(bl-g·bl-h 보기 키), `check_vnew_char_fidelity`(cf-b·cf-r 옛 상세 sha256)를 `_run_cast_photo_off`로 감쌌다. 이 세 검사가 끔 = 106을 본다.
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 65줄, FAIL·WARN 0, 100초. 기존 63줄은 시작 전 출력과 글자까지 같고, rw 줄만 "템플릿 env 29개 → 30개"로 바뀌었다. cpg 단독 3.4~4.4초.
  - `node --check frontend/public/vnew.js` 통과(cpg-h·cast-l·`check_js`).
  - 커버리지(coverage 7.17, 전체 검사기 `coverage run --source=app/src`, 시작 전 사본과 difflib): 바뀐 서버 실행문 89개 중 89개(100%) — `casting.py` 87/87, `settings.py` 2/2.
  - 변형 검사 42개(저장소 밖 임시 트리, 변형마다 원본으로 되돌리고 한 군데만 바꿔 cpg·관련 cast·bl·rw 실행): 고정 문장 9(없음·앞에·예산·참고 문장 먼저·앞에서 자름·눈 색·명사·BL 성별·webtoon), 그림체 낱말 4(단계 없음·명사구·관사 쉼표·stylish), 검사·다시 뽑기·원장 18(원장 없음·2번·0번·redraws 안 남김·fail-closed·S3 저장·설정 늘 켬/끔·참거짓·style·크기·모델·오류 → 실사·원장 키·같은 요청 다시 조회·실패 문구·로그 redraw·kind), 재개 1(reconcile가 대기 칸을 둠), 보기 2, 설정·배선 4, 화면 4(버튼 앞·innerHTML·늘 보임·CSS). 모두 잡혔다(놓친 것 0).
  - 유료 호출 0: cpg는 fal 래퍼·`_fetch_url`·`vpoc.llm._converse`·원장을 가짜로, `characters._fal_request`·`vpoc.fal._request`를 막고 호출 0을 본다. 기존 검사의 Bedrock guard(`_guard_real_llm`)도 그대로 통과한다. 기존 `check_vnew_generate`·`check_vnew_shot_brief`의 무료 가격 조회 GET은 이번에 바꾸지 않았다.
  - 줄 끝: 바뀐 파일·새 스펙 세 파일 모두 CR 0, BOM 없음(`config.prod.env`만 BOM, 바꾸지 않음). 이 항목을 붙일 때 편집 도구(insert)가 `WORKLOG.md` 전체를 CRLF로 바꿔 LF로 되돌렸다(원래 부분은 시작 전 사본과 바이트가 같고 끝에 71줄만 더해졌다). 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 함수 정의 0(AST). 시작 뒤 바뀐 저장소 파일은 위 파일과 실행 로그 `.kiro/handoff/logs/107.log`, 검사기의 `compileall`이 다시 쓴 `app/src/vnew/__pycache__/casting·settings.cpython-311.pyc`(원래 있던 파일, `.gitignore`)뿐이다. 새 파일은 스펙 세 개뿐이다. 수정 금지 파일은 바이트가 같다.
- **3단계 리뷰**
  - 보안
    - 검사에 보내는 것은 고정 질문 글과 우리가 만든 결과 사진뿐이다(사용자 외모 문장·캐릭터 설명은 보내지 않는다). 사진은 기존 그림 검사와 같은 Bedrock 경로(Fable 5.1 broker)로 간다. 나쁜 그림이 검사를 속여도 결과는 107 전과 같다(fail-open).
    - fal 본문은 다시 제출도 기존 `_submit_slot`이라 정확히 3키다(cpg-e가 두 본문이 같은지 본다, cast-m 그대로).
    - 발견 1: 모델이 준 style 글을 로그에 그대로 쓰면 줄바꿈·임의 글이 들어갈 수 있다 → 열거값 8개로 묶고 모르는 값은 "other"(조치, cpg-d). 발견 2: 모델 예외 문구에 URL·키가 섞일 수 있다 → `error_gist`(URL 쿼리·data URI·fal 키 제거, 200자)(조치, cpg-d가 서명 쿼리·키가 없는지 본다).
    - 버린 사진은 S3에 쓰지 않는다(cpg-e: put 0, 저장소 전체 그림체 사진 0). 화면 안내는 `textContent`(cpg-h).
  - 성능(512Mi)
    - 칸당 검사 지연: Bedrock 비전 1회 약 2~6초(추정, 저장소에 그림 검사 실측 없음 — 운영 `vpoc.call where=vnew.cast_photo_check ms=`로 잰다), 줄이기 수십 ms. 칸을 차례로 조회하므로 2장 뽑기가 약 4~12초 늘어난다. 다시 뽑기가 생기면 fal 생성 1회 + 5초 쉼 + 검사 1회가 더 든다.
    - 비용 추정: 검사 1회 입력 약 1.3K토큰(이미지 768×1024 ÷ 750 ≈ 1,050 + SYSTEM·질문·도구 ≈ 250) + 출력 20~200토큰 → 텍스트 기본 단가($3/M·$15/M)로 약 $0.004~0.007, 2장 뽑기 1회 약 $0.01(사진 $0.08의 약 12~18%). 1024로 줄이지 않으면 이미지 토큰이 약 2,360이라 약 2배다. 다시 뽑기는 칸당 최대 $0.04 1회.
    - 메모리: 검사용 디코드(1152×1536 RGB ≈ 5.3MB + 768×1024 ≈ 2.4MB)를 받기와 같은 `_PHOTO_SEM` 안에서 한다. 발견: 처음에는 세마포어 밖에서 디코드했다 → 프로세스 전체 한 장씩 규칙(봉우리 ≈ 25MB + 15MB)이 깨질 수 있어 안으로 옮겼다(조치). LLM 호출은 세마포어 밖이라 다른 캐릭터의 받기를 막지 않는다.
    - 외부 호출: 칸마다 LLM 1회(다시 뽑으면 2회), fal 제출은 칸마다 최대 2회. S3 쓰기는 다시 뽑기 때 1번 더(칸을 queued로).
    - 발견: 검사 호출이 재시도로 길어져 하트비트 600초를 넘기면 다른 파드가 이어받는다 → 이 실행은 다음 쓰기에서 접고 원장은 같은 키라 이중 계상이 없다(cpg-f claim 잃음 경우). 조치 없음(설계로 안전).
  - 운영성
    - 로그: `vnew.cast.photo_check slot= photo=true|false|none style= redraw=0|1 cid=`(실사 INFO, 그 밖 WARNING), `vnew.cast.photo_check_failed err=`, `vnew.cast.redraw cid= slot= redraws=1`, 실패 칸 `vnew.casting.slot_failed … kind=NotPhoto`. 사진·문장·비밀값 없음.
    - 되돌리기: `config.<target>.env`에 `VNEW_CAST_PHOTO_CHECK=false` 한 줄(검사·다시 뽑기·안내 없음, 106). 사진 고정 문장·그림체 낱말은 지시서대로 설정과 무관해 코드 되돌리기가 필요하다.
    - 진행 중 작업: 배포 전에 시작한 회차는 저장된 문장(고정 문장 없음)을 그대로 쓰고, 재시작 뒤 이어받으면 그 실행의 설정으로 검사한다. 롤링 중 옛 파드가 이어받으면 `redraws`를 모르지만 queued 칸은 실패로 두므로 추가 과금이 없다.
    - 재시작: `redraws`가 칸에 남아 두 번 이상 다시 뽑지 않는다(cpg-f).
    - 발견 1: 예문의 "person"이 남성·BL 문장 끝을 성 중립으로 만들었다(bl-d가 남성 문장 뒤쪽 person을 잡음) → 성별 명사로(조치, D1). 발견 2: 그림 방식 명사구를 낱말만 지우면 "in a clean with thin and soft" 같은 조각이 남았다 → 전치사구째·관사째 지우기, "a, …" 정리(조치, D2·cpg-b).
- **남은 이슈**
  1. 효과·오판정률은 실측하지 않았다(유료 금지). 개발 배포 뒤 설계자 승인으로 같은 캐릭터를 몇 회 뽑아 그림체 비율·`photo=false` 빈도·검사 지연을 본다(1회 2장 $0.08 + 검사 약 $0.01, 다시 뽑기 때 칸당 $0.04).
  2. Seedream 4.5가 고정 문장의 부정문(not a drawing …)을 어떻게 읽는지 확인하지 않았다.
  3. 검사 LLM 비용은 원장에 남지 않는다(기존 그림 검사와 같다). 남기려면 `charge_text` 배선이 필요하다.
  4. `VNEW_CAST_PHOTO_CHECK_MODEL`은 템플릿에 없어 운영에서 바꾸려면 템플릿 env 추가가 필요하다(기존 검사 모델 키와 같다).
  5. 그림체 낱말을 낱말만 지우는 꼴은 "comic lines" → "lines"처럼 어색한 조각이 남을 수 있다(그림 낱말은 남지 않는다).
  6. 자동 외모 문장을 쓰는 실사 테이크 외형(`generate._real_appearance` → `current_look`)에도 더한 낱말 제거가 적용된다. 실사 영상에서 그림 낱말을 빼는 목적과 같은 방향이고 `generate.py`는 바꾸지 않았다.
  7. 화면 안내는 늘 보인다(켬일 때). 실제로 다시 뽑았는지는 칸 상태로만 알 수 있고 화면에 "다시 뽑는 중" 표시는 없다(지시서 범위 밖).

### 지시서 108 — 실사 캐스팅 그림체 다시 뽑기를 칸당 최대 2회로 (Kiro, claude-opus-5.5)
- **범위**: `.kiro/handoff/108-cast-redraw-two.md`. 스펙 `.kiro/specs/cast-photo-guard/`에 108 변경(requirements R11과 R4.2·R4.3·R5.1·R7.1·R8.1·R9.2의 "108" 표시, design §8 D12~D15, tasks 8~10)을 더하고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았다. 사용자 데이터는 읽지 않았다.
- **먼저 한 일**: 바꿀 파일과 수정 금지 파일(`settings.py`·`generate.py`·`genre_bl.py`·`config.dev/prod.env`·`vnew.js`·`vnew.css`·`.gitlab-ci.yml`)의 시작 전 사본과 저장소 파일 목록(크기·mtime)을 저장소 밖(`%TEMP%\cpg108`)에 두었다. 시작 전 전체 검사는 전부 통과(ok 65줄, FAIL·WARN 0, 83초).
- **사실(지시서)**: 107(커밋 83a03b0, ci-cecf5366, 개발·운영 반영)은 그림체 칸을 1회 다시 뽑고(`_REDRAW_MAX = 1`), 두 번 다 그림체면 NotPhoto다. 개발 실험(범건우, 아이돌풍)에서 아이돌풍 문장은 약 50%가 그림체였다. 1회로는 칸 실패가 약 0.5² = 25%다.
- **결정**(사용자 2026-11-01, design §8)
  - `_REDRAW_MAX` 1 → 2(주석 "지시서 108: 두 번까지"). 칸 흐름: 제출 1 → 그림체 → 다시 제출(`redraws` 1) → 그림체 → 다시 제출(`redraws` 2) → 그림체 → NotPhoto. 어느 제출이든 실사(또는 검사 None)면 그 즉시 후보다. 칸 실패는 약 0.5³ ≈ 12.5%로 본다(추정).
  - 안내 문구 `_MSG_PHOTO_NOTE` = "그림체로 나온 칸은 최대 두 번 다시 뽑으며 그 비용이 더해질 수 있습니다."
  - 로그 `vnew.cast.photo_check … redraw=` 값은 `min(redraws, 1)` → `redraws`(실제 횟수 0·1·2, 실사·그림체·검사 None 세 줄 모두).
  - D12 원장: 키 규칙 그대로(`vnew-cast-{cid}-{후보 id}`, 후보 id = sha256(fal 요청 id) 앞 10자). 다시 제출은 기존 `_submit_slot`이 fal에 새 POST를 보내 새 요청 id를 받고, 다시 뽑을 때 칸의 `requestArn`·`candidateId`를 비운다. 그래서 제출 3회 = 후보 id 3개 = 원장 키 3개이고 겹치지 않는다. 최악이면 칸 하나에 원장 3건·제출 3회·S3 저장 0. 같은 키는 같은 요청을 다시 처리할 때만 나오고 원장이 한 번만 계상한다.
  - D13 재시작: `reconcile` 그대로. submitted·`redraws` 2 → 조회 → 그림체면 NotPhoto(네 번째 제출 없음). submitted·`redraws` 1 → 그림체면 세 번째 제출 1회. queued(`redraws` 무엇이든) → "중단돼 제출하지 않았습니다."
  - D14 시간 상한(확인, 동작 그대로): `_expired`(900초)는 뽑기 시작 `startedAt` 기준이고 다시 뽑을 때 새로 잡지 않는다. `_stale`(600초)은 순회마다 갱신하는 하트비트 기준이라 다시 뽑기와 상관없다. 칸 하나 최악 ≈ fal 생성 3회 + 검사 3회 + 약 25초(다시 제출 대기 2 × 5초 + 조회 간격 올림). 900초에 걸리려면 fal 한 장이 약 285초(1칸)·279초(기본 2칸)·267초(4칸)를 넘어야 한다. fal 생성 시간은 저장소에 실측이 없어 보통 수십 초로 보면(추정) 정상 흐름은 걸리지 않는다. 걸리면 지금처럼 남은 칸만 "시간 안에 사진을 받지 못했습니다."로 끝나고 다른 칸 후보는 남는다.
  - 그대로 둔 것: `_MSG_NOT_PHOTO`, `VNEW_CAST_PHOTO_CHECK`, 검사 모델, 캐스팅 문장, `reconcile`·`_stale`·`_expired`·`VNEW_CASTING_POLL_MAX_S`, `totalUsd`·`confirmUsd`, 보기 키.
- **검토한 대안**: 다시 뽑기 횟수 설정 키(지시서가 플래그를 바꾸지 말라고 함·배선이 늘어남, 기각), 다시 뽑을 때 `startedAt` 새로 잡기(지시서 `_expired` 그대로·회차 끝 보장이 약해짐, 기각), 마지막 시도에서 문장 바꾸기(프롬프트 그대로, 기각), 로그 redraw를 그대로 두고 새 키(지시서, 기각).
- **바꾼 것**
  - `app/src/vnew/casting.py`(+6 −6, 모두 같은 자리에서 고친 줄): `_MSG_PHOTO_NOTE`(:953), `_REDRAW_MAX`·주석(:954-955), `_photo_passes` 로그 두 줄(:1560·:1563), 모듈 docstring(:19). 함수 정의·흐름 코드는 그대로다.
  - `deploy/config.env.example`(:310)·`k8s/workloads.yaml.tmpl`(:333): 주석 "한 번 다시 뽑는다" → "두 번까지 다시 뽑는다"(주석만, 값·자리 그대로. cpg-c·rw·pz 그대로 통과).
  - `tools/check_vpoc.py`(+113 −26, 지운 함수 정의 0): `_CPG_NOTE`, cpg-e·cpg-f 경우, docstring·ok 줄, cpg 시드 캐릭터 variant ready·`characters.run_resume_variant` 막기(아래 발견 1).
  - `.kiro/specs/cast-photo-guard/` 세 파일, `docs/ANALYSIS.md`(머리 줄·§3.6 모듈 표·§3.6.1 B 「실사 사진 검사」 판정·재개·원장·보기·로그 행과 비용 문단·§10.1 줄 수·기대값 문단·cpg 행), 이 항목.
- **검사(어떤 검사가 무엇을 덮는지, 108에서 바꾼 것만)**

  | 검사 | 덮는 것 |
  |---|---|
  | cpg-e 그림체 → 그림체 → 실사(새) | `_photo_passes`의 `redraws < _REDRAW_MAX` 분기 두 번과 실사 분기: ready·후보 1(세 번째 요청)·원장 3(키 서로 다름)·요청 id 3개·후보 id 3개·제출 3(같은 본문 3키)·S3 1장·`redraws` 2·쉼 5초 2번·로그 redraw 0(false)·1(false)·2(true INFO)·`vnew.cast.redraw` redraws 1·2 각 1번·`slot_failed` 0 |
  | cpg-e 그림체 ×3(새) | `redraws >= _REDRAW_MAX` 분기: failed·NotPhoto 문구·오류 요약·원장 3(서로 다름)·S3 0·제출 3·검사 3·`redraws` 2·로그 redraw 0·1·2(WARNING)·`slot_failed kind=NotPhoto` 1번·시작 시각(300초 전으로 당겨 둠) 그대로 |
  | cpg-e 검사 None | 오류·모양 틀림·그림체 → 오류에 그림체 ×2 → 오류(새, `photo=none … redraw=2`)를 더함. 로그 기대값을 실제 횟수(`len - 1`)로 |
  | cpg-e 두 칸 | 칸 0 그림체 ×3 → failed·`redraws` 2·네 번째 요청, 칸 1 실사 그대로·원장 4·제출 4 |
  | cpg-f 재개 | submitted·`redraws` 2 → `reconcile` → 제출 0·NotPhoto·`redraw=2`·`vnew.cast.redraw` 0, submitted·`redraws` 1(새) → 세 번째 제출 1·ready·`redraws` 2, queued·`redraws` 1·2(2는 새) → 중단 실패·제출 0 |
  | cpg-h | `photoCheckNote`·`_MSG_PHOTO_NOTE` = 새 문구(보기·Flask 상세·뽑기 응답·node 가짜 DOM) |

- **기대값을 바꾼 검사와 이유**(모두 cpg 안, 다른 검사 기대값은 그대로): (1) cpg-e "그림체 → 그림체 → NotPhoto·원장 2" — 108에서 두 번째 그림체 뒤에 한 번 더 다시 뽑으므로 성립하지 않는다. 그림체 ×2 → 실사와 그림체 ×3으로 바꿨다. (2) cpg-e 두 칸 — 칸 0이 실패하려면 그림체가 세 번 나와야 해서 판정 하나를 더했다(칸 1 기대값 그대로). (3) cpg-e 검사 None `redraw_flag` — 로그가 실제 횟수라 `1 if len > 1 else 0` → `len - 1`. (4) cpg-f "redraws 1 → 세 번째 제출 0" — 108에서는 세 번째 제출이 정상이라 redraws 2 → 네 번째 제출 0으로 바꾸고 redraws 1 경우를 따로 더했다. (5) cpg-h `_CPG_NOTE` — 새 안내 문구. 실사 1회·그림체 → 실사·글 "false"·S3 put 0·claim 잃음·900초·끔(cpg-g)·cpg-a~d는 그대로다.
- **확인**
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 65줄, FAIL·WARN 0, 79초(문서까지 고친 뒤 마지막 실행). 시작 전 출력과 비교해 cpg 두 줄(ok 줄 설명·cpg-e 보고)만 바뀌고 다른 64줄은 글자까지 같다.
  - 커버리지(coverage 7.17, 전체 검사기 `coverage run --source=app/src`, 데이터는 저장소 밖, 시작 전 사본과 difflib): 바뀐 서버 실행문 4개 중 4개(100%) — `:953`·`:955`·`:1560`·`:1562`. 둘레 함수 `_photo_passes` 19/19, `run_draw` 32/32, `reconcile` 25/25.
  - 변형 검사 15개(저장소 밖 사본 트리, 변형마다 한 군데만 바꿔 cpg 실행): `_REDRAW_MAX` 1·3, 비교 `>`(= 상한 3), 실사·그림체 로그 `min(redraws, 1)`·`redraws + 1`·0, 검사 None 로그 `min(redraws, 1)`·0, 안내 문구 107, `redraws + 2`, 읽은 `redraws`를 1로 자름, 버린 사진 원장 없음, 다시 뽑을 때 `startedAt` 새로, 107 `casting.py` 그대로, 검사기 시드 고침 뺌. 모두 잡혔다(놓친 것 0). `_REDRAW_MAX` 3은 cpg-e(그림체 ×3 → ready)와 cpg-f(네 번째 제출) 둘 다 잡는다.
  - 유료 호출 0: cpg는 fal·`_fetch_url`·`vpoc.llm._converse`·원장을 가짜로, `characters._fal_request`·`vpoc.fal._request`·`characters.run_resume_variant`를 막고 호출 0을 본다.
  - 줄 끝·부산물: 바뀐 9개 파일 CR 0(시작 전도 0)·BOM 없음·끝 줄바꿈 있음, 공백만 바뀐 줄 0(같은 묶음·파일 전체 둘 다), 새 줄 끝 공백 0, 지운 함수 정의 0(AST). 수정 금지 8개 파일은 바이트가 같다. 새 파일 0. 시작 뒤 바뀐 저장소 파일은 위 9개와 실행 로그 `.kiro/handoff/logs/108.log`, 검사기의 `compileall`이 다시 쓴 `app/src/vnew/__pycache__/casting.cpython-311.pyc`(원래 있던 파일, `.gitignore`)뿐이다.
- **3단계 리뷰**
  - 보안: 외부로 보내는 것은 그대로다(fal 본문 3키, 검사에는 고정 질문과 결과 사진만). 다시 제출도 기존 `_submit_slot`이라 claim 확인 뒤 같은 회차 문장만 보낸다(cpg-e가 세 본문이 같은지 본다). 로그 `redraw`는 서버가 센 정수라 주입 여지가 없다. 발견 없음.
  - 성능(512Mi): 메모리·동시성은 그대로다(검사용 디코드는 `_PHOTO_SEM` 안에서 한 장씩, 다시 뽑기는 순회 수만 늘린다). 칸당 최악 지연: fal 생성 3회 + 검사 3회(약 2~6초씩, 추정) + 약 25초. 107보다 1차례(fal 생성 + 검사 + 약 5~10초) 늘어난다. 칸당 최악 비용: 사진 $0.04 × 3 = $0.12 + 검사 약 $0.012~0.021. 2장 뽑기 최악 사진 $0.24(확인 금액 $0.08의 3배), 4장 $0.48. 그림체 50%면 칸당 기대 제출 1.75회(약 $0.07). 외부 호출: 칸마다 fal 제출 최대 3회·검사 LLM 최대 3회·S3 칸 쓰기 다시 뽑기마다 1번. 발견: 시작 900초 상한은 다시 뽑기에서 새로 잡지 않아 fal이 한 장에 약 4분 30초 넘게 밀리면 마지막 칸이 시간 초과로 끝날 수 있다 → 지시서대로 동작은 그대로 두고 design D14에 조건을 적었다.
  - 운영성: 로그 `redraw=0|1|2`로 몇 번째 사진이 그림체였는지 바로 보인다. `vnew.cast.redraw … redraws=2`와 `kind=NotPhoto` 빈도로 효과를 잰다. 되돌리기는 `_REDRAW_MAX` 한 줄(코드)이고, 검사 전체 끄기는 지금처럼 `VNEW_CAST_PHOTO_CHECK=false` 한 줄이다. 진행 중 작업: 배포 전에 시작한 회차를 108 파드가 이어받으면 두 번까지 다시 뽑는다(그 회차 화면은 "한 번" 안내). 107 파드가 108 칸(`redraws` 1)을 이어받으면 더 다시 뽑지 않는다(덜 과금하는 쪽). 재시작 뒤 이어하기는 칸의 `redraws`로 상한을 지킨다(cpg-f).
  - 발견 1(검사기, 조치함): coverage로 전체 검사를 돌릴 때 cpg-f 재개 경우가 한 번 실패했다(칸 submitted 그대로·제출 0, 고치기 전 coverage 전체 실행 2번 중 1번). cpg 시드 캐릭터의 variant가 queued·하트비트 없음이라 `detail_view` → `reconcile_stale`가 진짜 `run_resume_variant` 스레드를 띄웠고(스레드 이름·로그로 확인, 107부터), 그 스레드가 같은 캐릭터를 고쳤다. 메모리 저장소는 etag 조건부 쓰기를 흉내 내지 않아 늦은 쓰기가 reconcile의 새 claim을 덮은 것으로 본다. 운영 S3는 조건부 쓰기라 서버 결함은 아니다. 스레드는 ARK 키가 없어 Seedream 호출 전에 실패했지만 키가 있는 환경이면 유료 호출이 될 수 있었다. 조치: 시드 캐릭터 variant를 ready로 두고, cpg 동안 `run_resume_variant`를 막아 다시 생기면 "쓰면 안 되는 호출"로 잡는다. 고친 뒤 coverage 전체 실행 2번·일반 전체 실행 2번 모두 통과.
- **남은 이슈**
  1. `app/src/vnew/settings.py:269` 주석에 "같은 칸을 같은 문장으로 한 번만 다시 뽑는다"가 남는다. 이번 고칠 목록에 없어 바꾸지 않았다(다음 변경 때 "두 번까지"로).
  2. 효과(그림체 비율·칸 실패 약 12%)는 실측하지 않았다(유료 금지). 개발 배포 뒤 `redraw=2`·`kind=NotPhoto` 빈도와 칸 시간(`vnew.casting.run_end ms=`)을 본다.
  3. 시작 900초 상한에 걸린 submitted 칸은 fal이 사진을 만들어도 원장에 남지 않는다(106부터 있는 동작). 다시 뽑기가 늘어 칸이 상한 가까이 갈 가능성이 조금 커진다.
  4. fal Seedream 4.5 한 장 생성 시간 실측이 저장소에 없다. D14의 "걸리지 않는다"는 추정이다.
  5. 전체 검사 중 진짜 `vnew-resume-*` 스레드를 띄우는 그룹은 이제 `check_vnew_characters`(2개)뿐이다(스레드 시작을 감싸 센 결과). 이어 만들기 자체를 보는 그룹이라 이번에 건드리지 않았다.

### 지시서 109 — 아이돌풍 실사 캐스팅만 FLUX.2 [pro]로 `VNEW_CASTING_IDOL_MODEL` (Kiro, claude-opus-5.5)

- **범위**: `.kiro/handoff/109-cast-idol-flux.md`. 새 스펙 `.kiro/specs/cast-idol-model/`(requirements R1~R9, design D1~D12·§3 비용·§4 검사, tasks 1~8)을 쓰고 같은 실행에서 구현·검사까지 했다. 커밋·push 하지 않았다. git·aws·kubectl·네트워크·유료 API를 쓰지 않았다. 사용자 데이터는 읽지 않았다.
- **먼저 한 일**: 바꿀 9개 파일의 시작 전 사본과 저장소 파일 목록(경로·mtime)을 저장소 밖(`%TEMP%\cim-orig`)에 두었다. 시작 전 전체 검사는 전부 통과.
- **사실(지시서)**: 107·108 뒤에도 아이돌풍은 Seedream 4.5에서 그림체가 많다(운영 1회 4장 중 3장 그림체·한 칸 실패, 개발 비교 6장 중 1장 실사). 현실 배우풍은 Seedream 10장 중 10장 실사. Claude 모델 비교(같은 범건우 아이돌풍 문장, 모델당 6장, 비전 검사): Seedream 1/6, `fal-ai/flux-2-pro`·`flux-2-max`·`flux-pro/v1.1-ultra`(raw)·`nano-banana-pro`·`kling-image/v3/text-to-image`·`z-image/turbo` 모두 6/6. FLUX.2 [pro]를 고른 이유는 실사 품질·떡대 체격·아이돌 얼굴이고, 다른 모델은 체격이 마르거나(nano-banana-pro·ultra·z-image) 해상도가 낮거나(kling 880×1168) 비싸다(max). FLUX.2 [pro]는 같은 3키 본문으로 동작하고 결과가 `images[0].url`이다. 1920×2560 요청 → 1760×2352. 가격은 첫 1MP $0.03 + 1MP마다 $0.015(올림) → 1760×2352(3.95MP → 4MP) $0.075.
- **결정**(사용자 2026-11-01 방안 나, design §2)
  - D1·D2 설정: `VNEW_CASTING_IDOL_MODEL`(기본 `fal-ai/flux-2-pro`, env가 없을 때만 기본, 빈 값 = 되돌리기 — 아이돌풍도 `VNEW_CASTING_MODEL`·`_USD`·`_SIZE`), `VNEW_CASTING_IDOL_USD`(0.075, `_checked_float` 0~10, 틀리면 기본 + 경고), `VNEW_CASTING_IDOL_SIZE`(1760x2352, `_image_size`와 같은 규칙). 모델 키만 앱 env·base(값 = 코드 기본)·`PARITY_ENV_KEYS`에 배선했다. 되돌리기 한 줄이 파드 env까지 가려면 템플릿 `${…}`가 있어야 하고, 빈 값이면 금액·크기도 함께 돌아가므로 두 키는 다른 `VNEW_CASTING_*`처럼 코드 기본이다.
  - D3 `preset_choice(preset)` → (모델, 장당, 크기). `total_usd(preset=None)`(없으면 지금처럼 `VNEW_CASTING_USD`).
  - D4 회차 기록: `start_draw`가 회차 문장·칸과 같은 조건부 쓰기로 `casting.model`·`unitUsd`·`imageSize`를 남긴다. `run_draw`는 순회마다 `_round_choice(cast)`로 꺼내 제출·다시 제출·원장에 쓴다(시작 때 설정에서 읽던 세 줄을 뺐다). 조회 주소는 지금처럼 `requestArn` endpoint. 기록이 없는 옛 회차·꼴 틀린 기록은 Seedream 값(D6).
  - D5 금액 확인: `_check_cost`를 두 자리에서 부른다. 느낌을 보낸 요청(화면)은 캐릭터를 읽기 전에(지금 순서), 느낌이 없는 요청은 mutate 안에서 저장된 느낌으로(`CastingRunning`보다 먼저). 409 문구·꼴은 그대로.
  - D7 보기: `unitUsd`·`totalUsd`는 지금 느낌 기준. 모델 키가 켜져 있으면 `presets[]`에 `unitUsd`·`totalUsd`를 더하고, 되돌리면 108과 같은 `{key, label}`만 둬 보기 전체가 108과 같다.
  - D10 화면: `castingPresetPrice`(서버가 `presets[].totalUsd`를 주면 그 값, 아니면 `totalUsd`), 라디오 change·409 다시 읽기에서 `price`를 다시 구한다.
  - 그대로 둔 것: 캐스팅 문장·사진 고정 문장·BL 규칙(genre-bl)·사진 검사(두 모델 모두 적용)·다시 뽑기 2회·원장 키 규칙·`reconcile`·`_stale`·`_expired`·`draw_start` 로그 꼴·`vnew.css`.
- **검토한 대안**(design D11): 다른 비교 모델(기각, 지시서 이유), 두 프리셋 모두 FLUX.2 [pro](현실 배우풍 10/10·$0.04, 기각), 금액·크기 키도 배선(되돌리기에 필요 없음·B 규칙, 기각), 크기에서 MP 공식으로 금액 계산(가격표가 바뀌면 공식도, 기각), 회차 기록 없이 회차 문장의 프리셋으로 지금 설정 읽기(설정이 바뀌면 섞임, 기각), 모델만 기록(되돌린 파드가 이어받으면 원장 $0.04·크기 1920×2560, 기각), `_empty_casting`에 기록 키(모든 캐릭터 저장값이 바뀜, 기각), 보기 `presets` 금액 늘 붙이기(되돌린 보기가 108과 달라지고 sha256 검사 기대값 변경, 기각), 금액 확인 한 자리(화면 경로 거절 순서 바뀜, 기각), 총액 센트 올림(B 결정 1, 기각 → 남은 이슈), 두 모델 id가 같으면 되돌리기로 보기(규칙이 하나 더, 기각).
- **바꾼 것**
  - `app/src/vnew/casting.py`(+91 −20, 지운 정의 0): 새 `_parse_size`·`preset_choice`·`_round_choice`·`_preset_items`·`_check_cost`, `total_usd(preset=None)`, `_image_size`(→ `_parse_size`), `start_draw`(금액 확인 2곳·회차 기록), `view`(`presets`·`unitUsd`·`totalUsd`·docstring), `_submit_slot` 로그 `model=`, `run_draw`(`_round_choice`), 모듈 docstring·과금 주석.
  - `app/src/vnew/settings.py`(+14 −2): 키 3개·주석, `_checked_*` 쓰임 주석 2곳.
  - `frontend/public/vnew.js`(+21 −3): `castingPresetPrice`, `castingSection`의 `priceSource`·`price`·라디오 change·`rereadPrice`. `vnew.css`는 그대로.
  - `deploy/config.env.example`(+9 −3): `VNEW_CASTING_IDOL_MODEL=fal-ai/flux-2-pro`(`VNEW_CAST_PHOTO_CHECK=true` 바로 뒤, 되돌리기 주석), `VNEW_CASTING`·`VNEW_CAST_PHOTO_CHECK` 주석의 장당 금액. `k8s/workloads.yaml.tmpl`(+4): 앱 env 한 쌍·주석 2줄.
  - `tools/check_vpoc.py`(+748 −6, 지운 정의 0): `PARITY_ENV_KEYS`, cast-m 정규식 2곳, `_run_cast_idol_off`, `_CIM_*`, `check_vnew_cast_idol_model`(cim-a~h), `main()`.
  - `.kiro/specs/cast-idol-model/` 세 파일, `docs/ANALYSIS.md`(머리 줄·§3.6 모듈 표·§3.6.1 B 뽑기·보기 행·캐스팅 화면·「아이돌풍 모델」 절·§6.2·§7.1 표·문단·§10.1), 이 항목.
- **검사(어떤 검사가 무엇을 덮는지)**

  | 검사 | 덮는 것 |
  |---|---|
  | cim-a | `settings` 세 키(하위 프로세스: 없음·빈 값·공백·덮어쓰기, 금액 0.1·" 0.2 "·빈 값·abc·nan·inf·-1·11 → 경고 5줄, 크기), settings.py 자리·주석, `preset_choice` 아이돌풍·대소문자·None·현실·되돌리기(""·"  ")·덮어쓰기 분기, `_parse_size` 6꼴(범위 밖·꼴 틀림·빈 값), `total_usd` preset 없음·있음 × 장 수 2·1·3·4 |
  | cim-b | 템플릿 env 한 쌍·자리·주석·금액·크기 키 없음, base 값 = 코드 기본·되돌리기 주석·자리, `PARITY_ENV_KEYS` 자리, `config.<target>.env` 그대로, render dev·prod `fal-ai/flux-2-pro`·빈 값 오버레이 `""`, 빈 값 파드 하위 프로세스(`preset_choice`·`total_usd`·`_preset_items` 되돌리기 분기) |
  | cim-c | `start_draw`의 `_check_cost` 두 자리(느낌 보냄 4경우·느낌 없음 + 저장 느낌 4경우·느낌이 저장값을 이김 2경우·되돌리기 2경우), 409 문구·409면 저장·스레드·제출 0, 회차 기록 3키·`usd` 7종, Flask `POST /casting/draw` 409 `CostMismatch`·202·응답 `totalUsd` |
  | cim-d | `run_draw` → `_submit_slot`(주소·본문 정확히 3키·1760×2352·URL·data URI 0·로그 `model=`·비밀 없음)·`_poll_slot`(조회 주소 `fal-ai/flux-2-pro/requests/…`)·`_charge`(키·모델·0.075)·`_photo_check` 2회, 현실 배우풍 Seedream·1920×2560·0.04, 되돌리기 아이돌풍 Seedream·0.04·기록 |
  | cim-e | `_photo_passes` 다시 뽑기·NotPhoto 분기의 원장 금액·모델(아이돌풍 2 × 0.075·3 × 0.075, 현실 2 × 0.04), 다시 제출 본문 같음, S3 저장 수 |
  | cim-f | `_round_choice` 기록 분기(회차 도중 설정 6개 바꿈 → 제출 3·원장 3 모두 FLUX.2 [pro]·0.075·1760×2352), `reconcile` → `run_draw` 재시작(되돌린 설정에서 조회·다시 제출·원장이 기록 값), 옛 회차 분기(기록 없음 → Seedream·0.04·1920×2560, `redraws` 1 → 2), 꼴 틀린 기록 11꼴(빈 모델·공백·숫자 모델·bool·글·nan·inf·음수·크기만 틀림) |
  | cim-g | `view`·`_preset_items` 켬·끔 분기(아이돌풍·현실·장 수 4), 되돌린 보기 = 108 식, 키 순서·나머지 값, Flask 상세 |
  | cim-h | `castingPresetPrice` 9꼴(글 금액·Infinity·배열 아님·null), `castingSection` 느낌 라디오·확인창·`confirmUsd`·되돌린 보기·409 다시 읽기 뒤 라디오(node 가짜 DOM, 서버 `view` 4개 그대로), 화면이 보낸 본문 4개 → Flask 뽑기 202·기록 모델, `innerHTML` 0, `node --check` |

- **기대값을 바꾼 검사와 이유**: cast-m 하나다. "템플릿·base에 `VNEW_CASTING_` 키가 없다" 정규식에서 `VNEW_CASTING_IDOL_MODEL`만 뺐다(`VNEW_CASTING_(?!IDOL_MODEL\b)`, base는 `^VNEW_CASTING_(?!IDOL_MODEL=)`). 되돌리기 한 줄을 위해 배선해야 하는 키이고(PARITY), 나머지 세부 키는 여전히 없어야 한다(템플릿 주석에도 그 이름을 쓰지 않았다). 새 기본값에서 기대값이 달라지는 기존 검사 4개(`check_vnew_casting`·`check_vnew_char_fidelity`·`check_vnew_bl_look`·`check_vnew_cast_photo_guard`)는 기대값을 바꾸지 않고 `main()`의 부르는 줄에서 `_run_cast_idol_off`(되돌리기 = 108 동작)로 감쌌다(103·107과 같은 규칙, 이유는 design D9 표).
- **확인**
  - `node --check frontend/public/vnew.js` 통과.
  - `python -X utf8 tools/check_vpoc.py` 전부 통과: ok 66줄(시작 전 65 + cim), FAIL·WARN 0, 81~83초(문서까지 고친 뒤 마지막 실행 81초).
  - 커버리지(coverage 7.17, 전체 검사기 `coverage run --source=app/src/vnew`, 데이터는 저장소 밖, 시작 전 사본과 difflib): 바뀐 서버 실행문 43개 중 43개(100%) — `casting.py` 40/40, `settings.py` 3/3. 둘레 함수 `start_draw` 36/36, `run_draw` 32/32, `view` 16/16, `_submit_slot` 14/14, `reconcile` 25/25, `_photo_passes` 19/19, 새 함수 5개 모두 100%.
  - 변형 검사 41개(저장소 파일을 한 곳씩 틀리게 고쳐 `check_vnew_cast_idol_model`만 돌리고 바이트째 되돌림): `casting.py` 27(아이돌풍·현실 바꿈, 빈 값 되돌리기 무시, 아이돌풍 크기·금액·크기 기본, `total_usd` 프리셋 무시, 금액 확인 두 자리 각각 없앰·기본 느낌으로, 회차 모델·금액·크기·usd 기록, `run_draw`가 설정을 읽음, 옛 회차가 지금 프리셋, bool·inf·음수 금액, 기록 크기 무시, `presets` 금액 늘·안 붙임, 보기 `unitUsd`·`totalUsd`, 로그 `model=` 없음, 본문 키 하나 더, 크기 범위, 금액 비교 `>`), `settings.py` 6(공백 안 지움, 빈 값 = 기본, `_float`, 금액·크기·모델 기본), `vnew.js` 6(라디오가 금액 안 바꿈, `presets` 안 봄, 글·무한대 금액 받음, 다시 읽은 보기 안 씀, 다른 느낌 금액), 배선 2(템플릿 env 없음, base 빈 값). 모두 잡혔다(놓친 것 0). 같은 뜻이 되는 변형(처음 `price`를 `totalUsd`로: 서버 `totalUsd`가 고른 느낌 금액이라 처음 화면은 같다)은 세지 않았다.
  - 유료 호출 0: cim은 fal(`casting._fal_request`·`_fetch_url`)·`vpoc.llm._converse`·원장을 가짜로, `characters._fal_request`·`vpoc.fal._request`·`characters.run_resume_variant`를 막고 호출 0을 본다.
  - 줄 끝·부산물: 바뀐 코드·설정 6개(`casting.py`·`settings.py`·`vnew.js`·`check_vpoc.py`·`config.env.example`·`workloads.yaml.tmpl`)와 문서 2개, 새 스펙 3개 모두 CR 0(시작 전도 0)·BOM 없음·끝 줄바꿈 있음, 공백만 바뀐 줄 0, 새 줄 끝 공백 0, 지운 함수 정의 0(AST·JS). 새 파일은 스펙 3개뿐. 시작 뒤 바뀐 저장소 파일은 위 파일과 실행 로그 `.kiro/handoff/logs/109.log`, 검사기 `compileall`이 다시 쓴 `app/src/vnew/__pycache__/casting·settings.cpython-311.pyc`(원래 있던 파일, `.gitignore`)뿐이다. 수정 금지 파일(`generate.py`·`genre_bl.py`·`config.*.env`·`.gitlab-ci.yml`·`app/src/vpoc/`)은 mtime이 그대로다.
- **3단계 리뷰**
  - 보안: 비밀값 추가 없음(fal 키는 지금처럼 `vpoc.fal._request`만 다룬다. 로그 `model=`은 설정값이고 cim-d가 로그에 키가 없는지 본다). 사용자는 모델·금액·크기를 정할 수 없다(라우트는 preset·gender·lookEn만 넘기고 preset은 `_apply_settings`가 400으로 거른다, `confirmUsd`는 `parse_confirm_usd`가 유한한 수만). 회차 기록은 서버만 쓰고 `_round_choice`가 꼴을 본다. 모델 id는 `{FAL_QUEUE_URL}/{model}` 경로에 들어가지만 `VNEW_CASTING_MODEL`과 같은 운영자 설정이고 호스트는 고정이다. 외부 전송: FLUX.2 [pro]에도 Seedream과 같은 3키(글만, 이미지·URL 없음)를 fal 큐로 보낸다. 글에는 지금처럼 사용자가 쓴 외모 문장이 들어간다. fal이 FLUX.2 [pro] 요청을 안에서 다른 회사(Black Forest Labs)로 넘기는지는 확인하지 않았다(미확인, 남은 이슈). 발견: 금액 키를 `_float`로 읽으면 nan·inf·음수를 받아 확인 금액이 늘 어긋난다 → `_checked_float`(0~10, 경고)로 막았다(cim-a, 변형 S3).
  - 성능(512Mi): 결과 4.1MP(1760×2352) 디코드는 Seedream 4.9MP보다 작고 `_PHOTO_SEM`(한 장씩) 그대로라 메모리 봉우리가 늘지 않는다. 동시성·시간 제한(`_FETCH_TIMEOUT_S` 120초, 900초·600초 상한)은 그대로다. 외부 호출은 제출마다 같고(fal POST 1·조회·결과·내려받기·검사 LLM 1), 그림체가 줄면 아이돌풍 제출 수가 준다. 비용: 아이돌풍 1회(2장) 사진 **$0.15**(그림체 없을 때) + 검사 약 $0.01, 최악 $0.45. 108 Seedream 아이돌풍 평균은 칸당 제출 1 + p + p²라 p = 0.5(108 가정) 2장 $0.14·칸 실패 12.5%, 운영 관측 p = 0.75 $0.185·42%, 개발 비교 p = 0.83 $0.20·58%. 성공 후보 1장당 FLUX.2 [pro] $0.075, Seedream $0.08~0.24. 발견: 장 수가 홀수면 아이돌풍 표시 총액이 반 센트 작다(1장 $0.07) → 기본 2장은 정확해 그대로 두고 남은 이슈로.
  - 운영성: 로그 `vnew.casting.submitted … model=`로 어느 모델로 냈는지 칸마다 보인다(`draw_start` 꼴은 그대로, bl-f). 되돌리기는 `config.<target>.env`에 `VNEW_CASTING_IDOL_MODEL=` 한 줄이고 보기·화면이 108과 같아진다(cim-b·cim-g). 진행 중 회차는 회차 기록으로 끝까지 같은 모델이고, 재시작 뒤 이어하기도 같다(cim-f). 배포 전에 시작한 회차(기록 없음)는 Seedream으로 끝난다(cim-f). 발견: 코드를 108로 되돌리는 중에 진행 중 FLUX.2 [pro] 회차를 108 파드가 이어받으면 다시 제출이 Seedream, 원장이 $0.04가 된다 → 절차로 막는다(먼저 설정 되돌리기, 진행 중 회차가 끝난 뒤 최대 900초 기다려 코드 되돌리기). design D12·ANALYSIS에 적었다.
- **운영 반영 계획(실행하지 않음)**: 커밋 뒤 개발 main 병합 → CI → 개발 ArgoCD. 개발에서 범건우 아이돌풍 1회(2장, $0.15)로 화면 금액($0.15)·확인창·원장 2 × 0.075·로그 `model=fal-ai/flux-2-pro`·결과 크기를 보고, 현실 배우풍 1회로 $0.08·Seedream을 본다. 운영은 수동 job `deploy:whynot:prod`. 되돌리기는 위 설정 한 줄.
- **남은 이슈**
  1. 1760×2352를 직접 요청한 결과 크기와 금액은 실측하지 않았다(실측은 1920×2560 요청 → 1760×2352). 개발 1회에서 확인한다.
  2. FLUX.2 [pro]의 그림체 비율은 6장 표본(0/6)뿐이다. 단측 95% 상한은 약 39%(추정)라 다시 뽑기 비용·NotPhoto 빈도를 로그로 본다.
  3. FLUX.2 [pro] 한 장 생성 시간 실측이 없다(108 D14의 900초 판단은 추정).
  4. `VNEW_CASTING_COUNT`가 홀수면 아이돌풍 표시 총액이 반 센트 작다(1장 $0.07·3장 $0.22, 원장은 장당 $0.075). 기본 2장은 정확하다.
  5. fal이 FLUX.2 [pro] 요청을 안에서 다른 회사로 넘기는지(데이터 처리 범위) 확인하지 않았다.
  6. 코드 되돌리기(108 이미지) 때는 운영 절차(설정 먼저·진행 중 회차 끝난 뒤)가 필요하다(위 운영성).
  7. 409 `CostMismatch` 안내 문구가 보인 뒤 느낌을 바꾸면 버튼 금액은 바뀌지만 안내 문구는 그대로 남는다(확인창 금액은 맞다).
