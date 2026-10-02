# Run Log · Phase 1 (앱 오버레이 병합)

| 항목 | 내용 |
|---|---|
| 일시 | 2026-09-08 15:50 ~ 18:40 KST |
| 브랜치 | `merge/imaginus-overlay` (기준 `8626db9`) |
| 병합 원본 | `이매지너스` @ `298717a` (불변 커밋) |
| **AWS 쓰기** | **0건.** 스케줄 확인만 read-only (`list-*` / `get-*` / `describe-*`) |
| 비밀 값 | 기록·출력하지 않음 |

---

## 1.1 파일 분류

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 15:52 | `이매지너스/.kiro/specs/story-dev-beta/*` 절 구조 조회 | 항목 ②③ 범위 확정 | R-01~R-26, `story_dev` Blueprint, 87 태스크 — **별개의 더 큰 기능** |
| 15:55 | `story_dev_router.py` 라우트 조회 | HTTP 노출 여부 | **라우트 0개** — 순수 분배자 |
| 15:57 | Overlay `app/src` import 그래프 조회 | 도달 가능성 | `story_dev_router` 를 아무도 import 하지 않음 |
| 16:00 | `server.py` 상대 import 39개 해석 검사 | ImportError 가능성 | 초기엔 `story_dev` 부재로 보였으나 **재열거에서 존재 확인**(31,715 B) |
| 16:03 | `app/src` 파일시스템 직접 재열거 | 열거 신뢰성 | **`server.py` 가 355,607 → 356,283 B 로 변함** — Overlay 가 작업 중 |
| 16:06 | `overlay_hash_start.txt` 와 전량 대조 | 무결성 | **7파일 변경 · 신규 3,245**(git·venv 포함). Overlay 는 움직이는 대상 |
| 16:08 | `이매지너스/.kiro/specs/story-dev-beta/진행상황.md` 읽기 | 그 세션 상태 | "아직 HTTP 로 노출된 것이 없다", Neptune 쿼리 문법만 확인 |
| 16:10 | `git -C 이매지너스 log/status` (읽기 전용) | 고정 기준 탐색 | 커밋 1건 `298717a` + 미커밋 20건 |
| 16:14 | `git show 298717a:app/src/server.py` 라우트 조회 | 요청 3기능 존재 확인 | `POST /api/auth/password` + story/outputs 16개 **모두 있음** |
| 16:18 | `classify_vs_commit.py` (Python) | `298717a` 기준 분류 | NEW 31 / MODIFIED 19 / IDENTICAL 58 / BASE_ONLY 1 |

**주의 1** — `git archive | tar` 는 두 번 실패했다. PowerShell 파이프가 바이너리를 텍스트로
취급해 스트림이 깨지고(`Unrecognized archive format`), Windows `bsdtar` 는 git 의 UTF-8
한글 경로를 ANSI 로 디코딩해 `Invalid empty pathname` 을 낸다. **Python 으로 blob 을 직접
읽어야 한다**(`git cat-file blob <pin>:<path>`).

**주의 2** — `.ps1` 에 한글 경로 리터럴을 넣어 또 깨졌다. 가드레일에 적어 둔 함정을 스스로
어긴 것이다. 경로는 **파라미터로 넘긴다**.

---

## 1.2 hunk 판정

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 16:24 | `pin_diffs.py` — 고정 커밋 기준 diff 재생성 | 워킹트리 기준 63건 무효화 | **59건** (PURE-DELETE 3 / REPLACE 56). `server.py` 12→8 (가드 추출 hunk 소멸) |
| 16:28 | `tag_hunks.py` — Protected / REMOVE / OVERLAY-ENV 태깅 | 자동 1차 분류 | APPLY 38 / APPLY+치환 8 / SKIP 3 / 개별검토 10 |
| 16:32 | 개별검토 10건 원문 확인 | 판정 확정 | §01_merge_report.md 4.2 |
| 16:36 | `check_asset_hunks.py` | 자산 의존 hunk 식별 | `index.html` 4건 · `styles.css` 7건이 미복사 자산을 참조 |
| 16:40 | `styles.css` 선택자 집합 대조 | 로그인 비주얼 충돌 확인 | Base 선택자 **14개 삭제**, Overlay 신규 3개(`login-motion*`) — 배타적 교체 |

---

## 1.3 적용 (백엔드)

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 17:05 | `git checkout -b merge/imaginus-overlay` | 작업 브랜치 | 완료 |
| 17:06 | `extract_pin.py` | 고정 트리 구현체화 | 142파일 → `%TEMP%\ov-pin` |
| 17:08 | `merge_step1_new.py` | NEW 복사 | 16개 COPY / 15개 SKIP(자산) |
| 17:12 | `merge_step2_groupA.py` (심볼 검증 내장) | SKIP hunk 없는 11파일 적용 | 10개 적용, **`server.py` 는 ABORT** |
| 17:14 | `COVER_IMAGE_MODEL`/`REGION` 추적 | ABORT 원인 규명 | 삭제가 아니라 `model_registry.py` 로 **이동**. Overlay 주석이 Base 버그를 기록 |
| 17:18 | `merge_step3_server.py` (이동 허용목록 + 라우트·wan\*·모듈말미 검증) | server.py 적용 | 라우트 53→76(삭제 0) · 심볼 218→278 · **wan\* 18/18 보존** |
| 17:22 | `compileall` · `verify_routes.py` | 백엔드 검증 | exit 0 / PASS |
| 17:25 | `git commit` | 체크포인트 | **`fa87a62`** |

---

## 1.4 적용 (프론트엔드)

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 17:40 | `assets/brand/logo.svg` 생성 | 교체 가능한 공백 로고 | 빈 SVG + 교체 방법 주석 |
| 17:42 | `merge_step4_frontend.py` | 5파일 Overlay 버전 적용 + Base 원본 보관 | 완료 |
| 17:46 | `index.html` 로그인 브랜드 치환 | 로고 → `logo.svg`, 텍스트 라벨 복원, 나비 영상 제거, `scene-stack` 복원 | 완료 |
| 17:50 | 모바일·사이드바·상단경로 로고 3곳 치환 | 동일 | 완료 (들여쓰기 불일치로 2회 재시도) |
| 17:54 | `title`·`meta description` Base 값 복원 | 브랜드 문자열 | 완료 |
| 17:57 | `handoffPanel` 복원 (`documentsPanel` 다음) | Base 패널 순서 유지 | 완료 |
| 18:00 | `FEATURES` 3개 플래그 `true` | D-14 반영 | 완료 |
| 18:02 | `pageTitle` 기본값 `AI Studio` 복원 · `handoffPanel` 표시 코드 복원 | app.js | 완료 |
| 18:06 | `css_restore.py` → 21규칙 중 19개 복원 | Base 로그인 CSS | `brand-mark` 2규칙은 그 마크업을 쓰지 않아 제외 |
| 18:10 | `fix_css.py` — Base 원문과 바이트 대조 | 복원 정확성 | **5규칙 수정** (콘솔 잘림으로 잘못 옮긴 `.scene-character` 등). 중괄호 균형 0 |
| 18:14 | 롱테이크 문장 복원 | `longForm: true` | 완료 |
| 18:17 | 진입점 4종 그리드 + `projectStory` 8000 복원 | `legacyInputType: true` | 완료 |
| 18:20 | `nginx.conf` CSP 를 이 계정 버킷으로 교정 | H7 | Base·Overlay 둘 다 다른 계정을 가리키고 있었다 |
| 18:22 | `verify-image-brief.ps1` baseline 복원 | A3 유지 | 조사 오류(`지훈가`) 발생 → `git checkout baseline --` 로 정정. diff 0 |

---

## 1.5 검증

| 시각 | 명령 | 결과 |
|---|---|---|
| 18:26 | `deploy/verify/verify_merge_nodelete.py` (신규 작성) | **PASS** — 파일 79→155 · 심볼 644→904 · 라우트 53→76 · K8s 8→8, 전부 삭제 0. Protected 5파일 diff 0 |
| 18:28 | `python -m compileall -q app/src` | exit 0 |
| 18:29 | `python deploy/render.py --check` | 템플릿 13개, 미해결 변수 0 |
| 18:30 | `kubectl apply --dry-run=client -f deploy/rendered/k8s/` | **실패** — kubectl 이 client dry-run 에서도 API group 발견을 위해 서버에 접속. 클러스터가 프라이빗 전용이라 불가 |
| 18:32 | `final_verify.py` — YAML 파싱으로 대체 + 비밀정보 스캔 | 5파일 8문서 정상. AWS 키·PEM 0건. **이매지너스 계정값 2건 · story-ai 1건 검출** |
| 18:34 | `classify_env_hits.py` — baseline 대조 | 병합이 들여온 2건과 Base 원래 6건을 갈랐다 |
| 18:36 | `story_models.py` · `start-all.ps1` · `nginx.conf` 주석 교정 | 이매지너스 계정값·`story-ai` **0건** 달성 |
| 18:38 | `check_html_ids.py` | 병합에서 손댄 id 15개 전부 존재. `#loginMotionVideo` 널 가드 확인. `#modelRetry`·`#scriptJobPanel` 은 **JS 가 동적 생성**(Base·Overlay 동일, 오탐) |

---

## 1.6 스케줄 확인 (사용자 요청)

| 시각 | 명령 | 결과 |
|---|---|---|
| 18:44 | `scheduler list-schedules --profile ai-poc-hub` | `grafana-event` 1건 (ENABLED, default 그룹) |
| 18:45 | `scheduler get-schedule --name grafana-event` | `cron(*/5 * * * ? *)` Asia/Seoul → Lambda `securityhub-test` |
| 18:46 | `events list-rules` (ScheduleExpression 필터) | `rule-lambda-startscheduler` · `rule-lambda-stopscheduler` **둘 다 DISABLED** |
| 18:47 | `events list-targets-by-rule` × 2 | 각각 `lambda-startscheduler` / `lambda-stopscheduler` |
| 18:48 | `autoscaling describe-scheduled-actions` | **0건** |

**조치 없음.** AI-POC 관련 스케줄은 0건이고, 끌 수 있는 2건은 이미 DISABLED다.
`grafana-event`는 Security Hub 점검 함수를 호출하는 **범위 밖 보안 모니터링**이라
명시적 지시 없이 끄지 않았다.

---

## 1.7 산출물

| 파일 | 내용 |
|---|---|
| `docs/01_merge_report.md` | 게이트 1-B 보고서 (5개 절) |
| `docs/AI-POC_history.md` | 변경 이력 |
| `docs/run-log/phase1.md` | 이 파일 |
| `docs/story-dev-beta_overlay/` | Overlay 자체 spec 4종 (2차 병합 근거) |
| `deploy/verify/verify_merge_nodelete.py` | 삭제 0 검증 도구 (신규) |
| `frontend/public/assets/brand/logo.svg` | 교체 가능한 공백 로고 (신규) |

커밋: `fa87a62`(백엔드) · `a76d25c`(프론트엔드) · 보고서 커밋

---

## 임시 파일 (저장소 밖, `%TEMP%`)

`ov-pin/`(고정 트리 142파일) · `ov-pin-files/` · `base-frontend-before/`(Base 원본 5파일) ·
`aipoc-pindiff/`(diff 16) · `aipoc-pin-deletions.txt` ·
`classify_vs_commit.py` `extract_pin.py` `pin_diffs.py` `tag_hunks.py` `show_flagged.py`
`check_asset_hunks.py` `check_appjs.py` `css_restore.py` `fix_css.py` `check_html_ids.py`
`check_two_ids.py` `final_verify.py` `classify_env_hits.py`
`merge_step1_new.py` `merge_step2_groupA.py` `merge_step3_server.py` `merge_step4_frontend.py`

`verify_merge_nodelete.py`만 저장소에 정식 편입했다. 나머지는 일회성 분석 도구다.
