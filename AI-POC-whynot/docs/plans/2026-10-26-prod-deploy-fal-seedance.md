# 운영 배포 계획: Seedance fal 경유 전환 (073 fal-seedance-parity 반영 main → 운영)

> 상태: **계획 — 단계마다 설계자 승인 후 실행**. 이 문서는 계획이며, 승인 전에는 아무 단계도 실행하지 않는다(이번 주 원칙 7번).
> 개정 2026-10-27: 072 리뷰(O-1·O-2·O-3·O-10)와 스펙 `.kiro/specs/fal-seedance-parity/` 구현(작업 1~6)에 맞춰 design.md §9의 두 단계 순서(안전 모드 배포 → 등록 → 전환)로 고쳤다.
> 이전 판(main `0271fdb0` 기준 한 번에 배포·배포 후 등록)은 쓰지 않는다. 진행 중 BytePlus 회차 실패(O-1), 플래그 되돌리기 불가(O-3), 등록 공백 동안 Luma 대체(O-2) 때문이다.
> 관련: WORKLOG 2026-10-26·2026-10-27, 리뷰 보고서 `docs/reviews/2026-10-26-prod-release-review.md`, design.md §8·§9, tasks.md "설계자 승인 후 유료 검증".

## 1. 목적
설계자 지시: "운영과 개발에 fal.ai의 seedance를 사용하도록 활성하고 기존 seedance는 제외".
- 개발: 073 main 병합 시 자동 배포된다(단계 0).
- 운영: 코드 배포(안전 모드) → fal 모델 2개 등록 → 플래그 전환, 세 번으로 나눠 켠다. 각 단계는 따로 승인받는다.

## 2. 무엇이 바뀌나
| 구분 | 지금 운영(`prod-ae21dc13`, 2026-10-20) | 단계 1 뒤(안전 모드) | 단계 3 뒤(전환) |
|---|---|---|---|
| 이미지 | `ci-ae21dc13` | `ci-<073 main sha>` | 같음 |
| ② 모델 목록 | Luma + BytePlus Seedance 2.0/2.5 + 등록된 fal | 지금과 같다(`VPOC_ARK_SEEDANCE_ENABLED=true`) | Luma + 등록된 fal. "Seedance 2.5 (fal)" 하나(i2v·r2v 짝). BytePlus 없음 |
| 옛 Seedance 저장 계획 | BytePlus로 생성 | BytePlus로 생성 | ②에서 "Seedance 2.5 (fal)"로 보이고 E6 안내. 로그 `vpoc.model.resolved reason=ark_disabled` |
| 진행 중 BytePlus 회차 | — | BytePlus로 끝난다 | BytePlus로 끝난다(R1, 재생성 포함, 회차 금액 `regenUsd`) |
| fal Seedance 계열 샷 | — | (목록에 BytePlus와 함께 보일 수 있음, 단계 2 뒤) | 참고 그림·글만은 reference-to-video, 시작·끝 장면은 image-to-video. 길이 4~15초, 화질 480/720/1080p(기본 720p), 프롬프트 4,000바이트, `@Image` 지칭 |

함께 나가는 변경 중 운영 기존 기능에 닿는 것:
- `vpoc/ark.py`·`fal.py`·`routes.py`·`ledger.py`·`settings.py`: 위 표의 동작. 계열이 아닌 fal 모델(Kling·MiniMax)의 길이·화질·지칭·프롬프트 상한은 지금과 같다(R14.3). 원장 model 칸만 엔드포인트 id로 바뀐다.
- fal 모델 해제(`DELETE /api/vpoc/fal/models/<id>`)는 관리자 전용이다(비관리자 403 `AdminOnly`, E3). 등록은 그대로 누구나.
- 금지 목록(`*seedance*2*fast*`)을 고를 때·제출 때도 다시 본다. 미등록 fal 회차는 Luma로 몰래 만들지 않고 failed(E5).
- 웹툰 변환(dual) 프롬프트: 패널만 그림체 기준, 실사 인물 그림은 identity only, 패널 0장이면 2D 웹툰 앵커.
- 프런트: `index.html`이 `vnew-previz.js`(three.js)를 더는 싣지 않는다. `app.js`가 `vnewEnabled`일 때만 동적 import한다. 운영은 `VNEW_ENABLED=false`라 three.js를 내려받지 않는다. Dockerfile이 `vnew-previz.js` 버전 쿼리도 치환한다.
- `stitcher.py`의 `threads`(vnew 전용, 운영에서 쓰이지 않음)는 인코더에도 걸린다. 기본(None)은 명령이 그대로다.
- `vnew/*`: 운영 `VNEW_ENABLED=false`라 꺼진 상태 그대로다.

## 3. 사전 확인 (단계 1 전에 전부 충족)
- [ ] 073 main(`<sha>`)에서 check_vpoc 전부 통과(CI `check:whynot` success, 로컬 `python -X utf8 tools/check_vpoc.py` 재실행 통과). 새 그룹 ip·rw·fr·du·rz·pm·ai·ad·ns·ml·vl·st·vm 포함.
- [ ] 바뀐 코드 커버리지 80% 이상(Claude `coverage run tools/check_vpoc.py`).
- [ ] ECR `whynot-app`·`whynot-frontend`에 `ci-<sha>`가 있다. 롤백용 `prod-ae21dc13`도 있다.
- [ ] 렌더 값(`config.env.example` + `config.prod.env`, 검사 rw-c와 같은 방식):
  - `VNEW_ENABLED=false`, `VPOC_MODEL_PICKER_ENABLED=true`, `VPOC_RAW_ERRORS=false`.
  - 단계 1 커밋에서 `VPOC_ARK_SEEDANCE_ENABLED="true"`.
  - 나머지 새 키는 base 값: `VPOC_FAL_SEEDANCE_FAMILY=bytedance/seedance-2.5/image-to-video=bytedance/seedance-2.5/reference-to-video`, `VPOC_FAL_SEEDANCE_MAX_S=15`, `VPOC_FAL_PROMPT_MAX_OVERRIDES=bytedance/seedance-2.5/*=4000`, `VPOC_FAL_SEEDANCE_TAG_STYLE=at`.
- [ ] 개발(단계 0): 개발 레지스트리에 i2v·r2v 둘 다 있고, 무료 확인 목록(6절)을 통과했다.
- [ ] 072 리뷰 "운영 배포 전 필수"가 0건이다: O-1(작업 1 `_submit_block_reason`), O-3(작업 1~4 템플릿·base 배선), O-2(이 계획의 두 단계 순서)로 해소했는지 확인한다.
- [ ] 실행 직전 30분 동안 운영 사용자 작업 요청이 없다(운영 로그의 POST·`/api/vpoc`).
- 단계 3의 선행 조건은 따로 있다(4절 단계 3).

## 4. 실행 단계 (단계마다 설계자 승인 후, 실행은 Claude)

### 단계 1. 운영 배포(안전 모드) — 승인 필요
1. `deploy/config.prod.env`에 `VPOC_ARK_SEEDANCE_ENABLED=true` 한 줄을 넣은 커밋을 main에 병합한다(MR).
2. 그 파이프라인의 수동 job `deploy:whynot:prod`를 실행한다.
   - job이 이미지에 `prod-<sha>` 보호 태그를 달고 `awstech/ai-deploy` `whynot/prod/*`에 커밋한다.
3. 클러스터 안 ArgoCD 앱 `whynot-prod`의 자동 동기화와 롤아웃 완료를 기다린다.
4. 확인(읽기 전용, 유료 호출 없음):
   - `https://whynot.meta-clouds.com/api/health`의 `build.imageTag`가 `ci-<sha>`다.
   - ② 목록이 지금 운영과 같다(BytePlus 2개 + 등록된 fal).
   - 진행 중 BytePlus 회차가 있으면 계속 제출·조회된다.
   - 프런트 첫 화면이 `vnew-previz.js`·`three.module.min.js`를 요청하지 않는다(브라우저 네트워크 탭).
   - 비관리자 계정으로 fal 모델 해제를 시도하면 403과 E3이 보인다.
   - 앱 로그에 `vpoc.error`가 늘지 않는다(10분).

### 단계 2. fal 모델 등록 — 승인 필요(단계 1 확인 뒤)
- 등록할 것: `bytedance/seedance-2.5/image-to-video`, `bytedance/seedance-2.5/reference-to-video` 둘 다(얼굴은 짝까지 등록돼야 고를 수 있다, R2.2).
- 방법은 둘 중 하나다.
  - (가) 설계자 승인 뒤 Claude가 운영 앱 파드 안에서 `fal.register(endpoint)`를 두 번 실행한다. fal 스키마·단가 조회만 하며 과금이 없다.
  - (나) 설계자가 관리자 계정으로 운영 화면 "fal에서 모델 가져오기"에서 등록한다. Claude는 운영 계정 비밀번호를 입력하지 않는다.
- 확인(`GET /api/vpoc/fal/models`):
  - 둘 다 `available`.
  - caps: `durations` 4..30 · `resolutions` 480p/720p/1080p · i2v `endImageField=end_image_url` · r2v `referenceImagesField=image_urls` · 둘 다 `audioField=generate_audio` · `pricing` 채워짐(`1000 tokens`, 0.0214).
  - 금지 패턴(`*seedance*2*fast*`) 항목이 있으면 목록에 사용 불가로 보인다.
- 참고: 단계 2~3 사이에는 플래그가 true라 ②에 BytePlus와 "Seedance 2.5 (fal)"이 함께 보인다. 옛 저장값 치환은 일어나지 않는다.

### 단계 3. 전환 — 승인 필요(선행 조건 충족 뒤)
- 선행 조건: 개발계 유료 검증 **V-2·V-3·V-6**(약 $4.37, 설계자 승인 2026-10-26)이 끝나 결과가 기록돼 있다. 실패 항목은 tasks.md "실패하면" 칸의 조치를 먼저 반영한다.
  - V-2 실패 → `VPOC_FAL_SEEDANCE_TAG_STYLE=plain`을 단계 3 커밋에 함께 넣는다(config만).
  - V-3 실패 → 키 장면 방식을 r2v 참고로 바꾸는 안을 검토한다(코드 변경, 이 계획 보류).
  - V-6 실패 → X3(원장 단가)을 다시 정한다(코드 변경, 이 계획 보류).
  - 나머지(V-1·V-4·V-5·V-7)는 별도 예산 승인 뒤에 한다. 단계 3의 선행 조건이 아니다.
1. `config.prod.env`에서 `VPOC_ARK_SEEDANCE_ENABLED=true` 줄을 지운 커밋을 병합하고 운영 job을 다시 실행한다.
2. 확인:
   - ② 목록에 byteplus가 없고 "Seedance 2.5 (fal)"이 available이다.
   - 로그 `vpoc.model.resolved … reason=ark_disabled`(같은 조합 10분에 1회).
   - 진행 중 BytePlus 회차가 있으면 계속 진행된다(R1).

### 단계 4. 검증
- 무료 확인 목록(6절) + 10분 로그 관찰(`vpoc.error`, `vpoc.fal.route`, `vpoc.fal.resolution_adjusted`).
- 유료 1건(운영)은 설계자가 따로 승인할 때만 한다.

## 5. 되돌리기 (design.md §8, 최신 코드 기준)
오버레이는 base(`config.env.example`)에 있는 키만 덮는다. 아래 키는 모두 base·템플릿에 배선돼 있어 `config.prod.env` 한 줄 + 운영 job으로 바뀐다. ai-deploy를 직접 고치면 다음 배포가 덮어쓰므로 쓰지 않는다.

| 범위 | 방법 | 효과 | 주의 |
|---|---|---|---|
| BytePlus Seedance 복귀(기능 전체) | `VPOC_ARK_SEEDANCE_ENABLED=true` → 운영 job | ARK가 목록에 돌아오고 치환이 멈춘다. 이미 만든 fal 회차(`steps.4.videoModel=fal:…`)는 그대로 진행된다 | Secret `seedance-api-key`를 지우지 않는다(진행 중 회차·Seedream이 같은 키) |
| r2v 라우팅만 끄기 | `VPOC_FAL_SEEDANCE_FAMILY=`(빈 값) → 운영 job | 070 동작(i2v 하나, 인물 그림이 시작 프레임). 짝 등록 요구도 사라진다 | 품질이 070 수준으로 떨어진다 |
| 길이 상한 | `VPOC_FAL_SEEDANCE_MAX_S=<n>` | ③ 선택지·검사 상한이 바뀐다 | 이미 만든 ③ 계획은 generate에서 E2가 날 수 있다 |
| 프롬프트 상한 | `VPOC_FAL_PROMPT_MAX_OVERRIDES=`(빈 값) 또는 다른 값 | caps·2,500으로 돌아간다 | — |
| 지칭 방식 | `VPOC_FAL_SEEDANCE_TAG_STYLE=plain` | 계열 r2v 프롬프트·③ 미리보기가 "Image N" 지칭 | 다음 제출부터 적용 |
| three.js 지연 로드 | 플래그 없음. 커밋 revert | — | 프런트 이미지 재빌드 |
| 전체 | `awstech/ai-deploy` `whynot/prod` 이번 커밋 revert(이미지 `prod-ae21dc13`) | 10/20 운영 상태. ArgoCD 자동 동기화 | 레지스트리 항목은 남는다(무해). 필요하면 관리자가 해제 |

## 6. 무료 확인 목록(운영·개발 공통, 유료 호출 없음)
- 옛 Seedance 저장 계획의 ②에서 "Seedance 2.5 (fal)"이 선택되어 있고 E6 안내가 보인다(단계 3 뒤).
- ③ 화질 기본 720p, 선택지 480/720/1080p. 샷 길이 선택지 4~15초. ③ 저장도 같은 범위로 받는다.
- ③ 미리보기 문장에 `@Image1…`이 있고, 번호 수가 참고 이미지 수와 같다.
- `plan_view` 금액이 토큰식(720p 10초 ≈ $4.62)과 맞는다.
- 비관리자 계정으로 해제하면 403과 E3이 보인다.
- 첫 화면이 three.js를 내려받지 않는다(운영). 개발(vnew 켜짐)은 ④ 프리비즈가 열린다.
- 앱 로그에 `vpoc.error`가 늘지 않는다.

## 7. 영향과 위험
- 배포 중 파드가 교체된다. 진행 중 백그라운드 작업은 이어하기 드라이버가 다시 잡는다. 그래도 사용자 작업이 없는 시간에 실행한다.
- 키 장면(시작·끝 장면) 방식은 단계 3 뒤 fal i2v(`end_image_url`)로 간다. V-3에서 끝 프레임 반영을 확인한다.
- 범위 밖: 계열이 아닌 토큰 단가 fal 모델의 540p 과소 계상(O-5, WORKLOG 2026-10-27).
- 비용: 배포·등록·무료 확인에는 유료 생성 호출이 없다. 유료 검증 V-2·V-3·V-6은 개발계에서 약 $4.37.

## 8. 승인
- [ ] 단계 1(안전 모드 배포) 설계자 승인: ____ (날짜·방법)
- [ ] 단계 2(등록, 방법 가/나) 설계자 승인: ____
- [ ] 단계 3(전환) 설계자 승인: ____ (V-2·V-3·V-6 결과 확인 후)
