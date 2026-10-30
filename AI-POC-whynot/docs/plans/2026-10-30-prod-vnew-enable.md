# 운영 배포 계획: 영상만들기_신규(vnew) 켜기 + Seedance 두 경로 (2026-10-30)

> 상태: **사용자 승인(2026-10-30), 실행 전.** 실행은 Claude가 한다(이번 주 원칙 7번 — Kiro는 계획만 쓰고 배포·클라우드 명령을 쓰지 않는다).
> 사용자 원문(2026-10-30):
> - "앞으로 추가로 운영에도 같이 배포를 진행해라"
> - 운영 vnew: "켠다"
> - Seedance: "seedance도 사용할 수 있고 fal seedance도 사용할 수 있게 해라"
>
> 관련: `docs/plans/2026-10-26-prod-deploy-fal-seedance.md`(단계 1·2 승인, 단계 3 안 함), `docs/ANALYSIS.md` §7.1·§8.5·R-21, `docs/WORKLOG.md` 2026-10-30 char-fidelity T3.

## 1. 무엇이 바뀌나

설정은 `deploy/config.prod.env` 두 줄이다(char-fidelity T3 커밋). 개발(`config.dev.env`)은 바꾸지 않는다.

```
VNEW_ENABLED=true                 # false → true
VPOC_ARK_SEEDANCE_ENABLED=true    # 새 줄(바로 위에 날짜·결정 주석 1줄)
```

| 구분 | 지금 운영(`ci-ae21dc13`) | 배포 뒤 |
|---|---|---|
| 이미지 | `ci-ae21dc13` | 이번 main 커밋의 `ci-<sha>` |
| 만들기·트랙 고르기 창 | "영상", "스토리" | "영상만들기_기존", "영상만들기_신규". **"스토리" 버튼은 숨는다**(`app.js` `applyVnewToggles`). 기존 스토리 프로젝트는 목록·열기 그대로다 |
| 메뉴 | — | "캐릭터 만들기"가 생긴다 |
| API | `/api/vnew` 없음 | `/api/vnew` 블루프린트 등록(`server.py`의 `if VNEW_ENABLED:`) |
| 첫 화면 다운로드 | three.js 없음 | 앱을 처음 읽을 때 `vnew-previz.js`(약 48KB)·`three.module.min.js`(약 690KB)를 비동기로 받는다. 첫 화면 그리기는 기다리지 않는다. 배포 태그가 같으면 브라우저 캐시(30일 immutable)를 쓴다 |
| ② 영상 모델 목록 | Luma + BytePlus Seedance 2.0/2.5 + 등록된 fal | 같고, 등록 뒤 "Seedance 2.5 (fal)"이 함께 보인다(BytePlus 그대로) |
| 캐릭터 3D | — | 기본 Tripo H3.1(`VNEW_3D_PROVIDER=tripo`), 그 밖의 `VNEW_*`는 코드·base 기본값(개발과 같다) |

함께 나가는 코드: `ci-ae21dc13`(2026-10-20) 이후 main 변경 전부다. 영상만들기_기존(vpoc)에 닿는 것은 2026-10-26 계획 §2(073 fal Seedance 동등성)와 WORKLOG 2026-10-28 llm-top-model-first(`vpoc.llm` 잘림 재시도·설정 거절 미리 적용)다. 운영 기본 텍스트 모델은 base 값 그대로다.

## 2. 사전 확인

Claude 읽기 전용 확인(2026-10-30):
- [x] 운영 앱 `ci-ae21dc13`에 fal 키·ARK 키·Bedrock 중개 역할 설정이 있다.
- [x] vnew import가 정상이다.
- [x] S3 권한이 `users/*`이고, vnew 저장 경로(캐릭터·계획·장소·프리비즈·⑤ 산출물)가 모두 `users/` 아래다.
- [x] 운영 앱 메모리 한도는 8Gi다(개발 512Mi에서 검사·리뷰한 값보다 여유가 크다).

Kiro 로컬 확인(2026-10-30, 유료·네트워크 없음):
- [x] `python -X utf8 tools/check_vpoc.py` 전부 통과(ok 61줄).
- [x] 렌더 값(`config.env.example` + `config.prod.env`, `render.py`를 메모리에서만): `VNEW_ENABLED="true"`, runtime-config `vnewEnabled: true`, `VPOC_ARK_SEEDANCE_ENABLED="true"`, `VPOC_MODEL_PICKER_ENABLED="true"`, `VPOC_RAW_ERRORS="false"`, `VPOC_POLL_GIVEUP_S="1800"`, `VNEW_CHAR_FIDELITY`·`VNEW_3D_SIDE_VIEWS`·`VNEW_FX`·`VNEW_CASTING`·`VNEW_TAKE_REAL`=`"true"`, `VNEW_3D_PROVIDER="tripo"`, `STORY_DEV_ENABLED="true"`. 개발 렌더 `VPOC_ARK_SEEDANCE_ENABLED="false"`. 검사 rw-c가 같은 값을 본다.

실행 직전(Claude):
- [ ] MR 파이프라인 `check:whynot` 성공.
- [ ] 같은 커밋의 개발 자동 배포 뒤 개발 무료 확인(4절과 같은 목록)을 통과했다.
- [ ] 운영 ECR `whynot-app`·`whynot-frontend`에 이번 `ci-<sha>`가 있다. 롤백용 `prod-ae21dc13`도 있다.
- [ ] 실행 직전 30분 동안 운영 사용자 작업 요청이 없다(운영 로그의 POST·`/api/vpoc`). 파드 교체 중 진행 중인 vpoc 작업은 이어하기 드라이버가 다시 잡는다.

## 3. 단계

1. MR 병합(char-fidelity T3 커밋 포함). main 병합으로 개발이 자동 배포된다.
2. 그 파이프라인의 수동 job `deploy:whynot:prod`를 실행한다. job이 이미지에 `prod-<sha>` 보호 태그를 달고 `awstech/ai-deploy` `whynot/prod/*`에 커밋한다.
3. 클러스터 안 ArgoCD 앱 `whynot-prod`의 동기화와 롤아웃 완료를 기다린다.
4. `https://whynot.meta-clouds.com/api/health`의 `build.imageTag`가 이번 main 커밋의 `ci-<sha>`인지 본다.
5. fal 모델 3개 등록(무료, 스키마·단가 조회만): `bytedance/seedance-2.5/image-to-video`, `bytedance/seedance-2.5/reference-to-video`, `bytedance/seedance-2.5/draft/complete`. 방법·확인 항목은 2026-10-26 계획 단계 2다.
6. 무료 확인(4절).

## 4. 무료 확인(유료 호출 없음)

- [ ] 첫 화면 메뉴가 "영상만들기_기존"·"영상만들기_신규"다.
- [ ] 캐릭터 목록이 열린다(메뉴 "캐릭터 만들기" → `GET /api/vnew/characters` 200).
- [ ] ② 목록에 BytePlus Seedance와 "Seedance 2.5 (fal)"이 함께 있다.
- [ ] 로그 `vpoc.error`가 늘지 않는다(10분).
- 함께 볼 것:
  - 브라우저 네트워크 탭에서 `vnew-previz.js`·`three.module.min.js` 요청이 `app.js` 뒤에 비동기로 나가고, 첫 화면이 그 전에 그려진다.
  - 기존 스토리 프로젝트가 목록에서 열린다("스토리" 만들기 버튼만 숨는다).
  - 캐릭터 빌드·⑤ 영상 생성 같은 유료 확인은 하지 않는다. 운영 유료 확인은 따로 승인받는다(7절).

## 5. 되돌리기

| 범위 | 방법 | 효과 | 주의 |
|---|---|---|---|
| vnew 전체 | `config.prod.env`의 `VNEW_ENABLED=false` 한 줄 + 운영 job | 화면(메뉴·버튼·three.js)과 `/api/vnew`가 함께 꺼진다. 저장된 vnew 데이터(`users/{owner}/characters/…`, `…/vnew/…`)는 남는다(무해) | 끄기 전에 진행 중인 캐릭터 빌드·⑤ 생성이 없는지 본다. 끄면 그 작업의 조회·이어 만들기가 멈추고, 이미 낸 fal·ARK 요청의 비용은 든다 |
| 캐릭터 3D 공급자 | `VNEW_3D_PROVIDER=hunyuan` 한 줄 + 운영 job | 다음 새 3D 요청부터 Hunyuan3D v3(T2 본문·$0.675). 진행 중 요청은 arn의 공급자로 끝난다 | — |
| BytePlus Seedance 빼기 | `VPOC_ARK_SEEDANCE_ENABLED=true` 줄 삭제 + 운영 job | 2026-10-26 계획 단계 3과 같다 | **승인되지 않음.** 다시 지시가 있을 때만 |
| 전체 | `awstech/ai-deploy` `whynot/prod` 이번 커밋 revert(이미지 `prod-ae21dc13`) | 10/20 운영 상태. ArgoCD 자동 동기화 | 레지스트리 항목은 남는다(무해) |

## 6. 영향과 위험

- 메뉴: 운영 사용자 전체의 메뉴 이름이 바뀐다("영상" → "영상만들기_기존", "영상만들기_신규"·"캐릭터 만들기" 추가, "스토리" 만들기 버튼 숨김). 사용자 안내가 필요하면 배포 전에 한다.
- 비용: 새 유료 기능이 로그인한 운영 사용자 누구에게나 열린다. 캐릭터 1명 약 $1.2(Seedream 그림 약 10장 × $0.04 + 검사 LLM 소액 + Tripo 3D $0.60 × 2 포함, 다시 그리면 더). ⑤ 영상은 고른 모델 단가대로다(①·② 화면에 보인다). 비용 상한은 없고 원장에 기록만 남는다(2026-09-23 사용자 결정).
- 권한: 새 권한이 필요 없다(S3 `users/*`, Bedrock 중개 역할, fal·ARK 키 — 2절 사전 확인).
- 메모리: 운영 앱 한도 8Gi. 캐릭터 빌드는 빌드 하나에 수십 MB 안쪽, Tripo GLB 옮기기는 약 24MB 순간 최고점이다(개발 512Mi 기준 리뷰, WORKLOG T2·T2b).
- 첫 화면: 2026-10-26 계획이 지키던 "운영 첫 화면은 three.js를 받지 않는다"가 바뀐다. 받는 것은 비동기라 화면을 막지 않지만, 느린 회선에서는 처음 접속 때 약 735KB(압축 전)가 더 내려간다.
- 진행 중 작업: 운영은 지금까지 `/api/vnew`가 등록되지 않았으므로 배포 때 이어 갈 vnew 작업이 없다(코드 경로 기준 추정). vpoc 진행 중 작업은 지금처럼 이어하기 드라이버가 잡는다.
- 설정 주석: `config.prod.env`의 VNEW 주석 3줄("운영은 꺼진 채로 둔다")은 이번에 바꾸지 않아 값(`true`)과 맞지 않는다. 커밋 때 Claude가 고칠지 정한다.

## 7. 승인

- [x] 운영 배포 진행: 사용자 2026-10-30, 원문 "앞으로 추가로 운영에도 같이 배포를 진행해라".
- [x] 운영 vnew 켜기: 사용자 2026-10-30, 원문 "켠다".
- [x] BytePlus·fal Seedance 둘 다: 사용자 2026-10-30, 원문 "seedance도 사용할 수 있고 fal seedance도 사용할 수 있게 해라".
- [ ] 운영 유료 확인(캐릭터 1명 빌드 약 $1.2 등): 승인되지 않음. 따로 승인받는다.
