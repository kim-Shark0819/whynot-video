# 작업 — vnew-live-fx (기호·분위기 · 실사 캐스팅 · 통 테이크 실사 버전)

> 요구사항 `requirements.md`, 설계 `design.md`. 경로의 `app/`·`frontend/`·`k8s/`·`deploy/`·`tools/`·`docs/`는 `AI-POC-whynot/` 아래다.
> 묶음은 A → B → C 순서다. **작업 하나 = Kiro 실행 1회**다. 묶음 끝 작업 뒤에 Claude가 검토·커밋·개발 배포·"배포 뒤 확인"을 한 다음 다음 묶음을 시작한다.
> 2026-10-30 Claude 검토 결정 1~9를 반영했다(requirements.md 「검토 결정」 표). A1·A2는 같은 실행(089)에서 했다.

## 공통 규칙

- design.md §9에 적힌 파일과 그 작업에 적힌 함수만 고친다. 기존 함수 정의를 지우지 않는다. 줄 끝은 LF다.
- 허용 명령은 `python`·`node`뿐이다. git·aws·kubectl·네트워크·유료 API를 쓰지 않는다. 커밋·push 하지 않는다.
- 검사는 가짜 LLM·가짜 fal·메모리 저장소만 쓴다(`_guard_real_llm`, `_FakeCharStore`, `_pin_vnew`). 사용자 데이터(plan.json)를 읽지 않는다. 참고 구현 폴더는 읽지도 실행하지도 않는다(필요한 원문은 design.md에 있다).
- 기존 검사 기대값을 바꾸지 않는다. 새 기본값(켬) 때문에 기존 검사가 실패하면 **`main()`에서 그 검사를 부르는 줄에서** 끔으로 고정하고(`_run_fx_off(check_x)` 같은 도우미, `with _pin_vnew(VNEW_FX=False):` 또는 해당 키) 결과 보고에 함수 이름을 적는다. 검사 함수 본문·들여쓰기는 건드리지 않는다(공백만 바뀐 diff 금지, 2026-10-30 Claude 검토 피드백). 숫자·문자열 기대값은 고치지 않는다(Claude 결정 9).
- 새 검사 그룹은 `main()` 끝에 부른다. 설정 키는 `PARITY_ENV_KEYS`에 더한다(rw-a~c가 템플릿·base 배선을 본다).
- 완료 조건을 통과해야 체크하고 다음 작업으로 간다. 같은 작업에서 3회 실패하면 멈추고 요약한다.
- 묶음 끝 작업은 세 번 따로 리뷰(보안 → 성능 → 운영성)하고, 커버리지(`coverage run --source=app\src tools/check_vpoc.py`, 원본 사본과 difflib으로 구한 바뀐 줄 80% 이상)와 "검사 → 덮는 함수·분기" 표를 결과 보고에 적는다. `docs/ANALYSIS.md`(§3.6·§7.1·§10.1)와 `docs/WORKLOG.md`(날짜·결정·대안·확인·리뷰·남은 이슈)를 갱신한다.

크기: 작음 = 순수 함수·프롬프트(바뀐 줄 ≈ 200 이하), 중간 = 백엔드 흐름 또는 화면 한 덩어리(≈ 200~450), 큼 = 백엔드 + 화면 + 검사(≈ 450 이상). 줄 수는 검사 포함 추정이다.

---

## 묶음 A — 기호·분위기·묘사 규칙 (`VNEW_FX`)

- [x] A1. 기호 모듈·설정·저장 규칙 (작음)
  - 파일: `vnew/fx.py`(신규, design §A.1 전부), `vnew/settings.py`(`VNEW_FX`, `VNEW_FX_MAX_PANELS`), `vnew/plan.py`(`update_conti` §A.2 1~3, `plan_view` `features`·`fxFill`(targets·translate·estimateUsd — 결정 1)), `k8s/workloads.yaml.tmpl`·`deploy/config.env.example`(`VNEW_PREVIZ_SUPPORT` 바로 뒤), `tools/check_vpoc.py`(`check_vnew_fx` 뼈대 + `PARITY_ENV_KEYS`).
  - 검사: fx-a, fx-b, fx-c, fx-d(순수 함수 부분), fx-e, fx-m.
  - 완료 조건: `python -m py_compile` 통과, `fx.py`에 네트워크·S3 import 없음, `python -X utf8 tools/check_vpoc.py` "전부 통과"이고 `[ok  ]` 줄에 fx-a~e·fx-m, 끔 상태 `update_conti`가 지금과 같은 저장본을 만든다(fx-e).

- [x] A2. 채우기 호출·작업·라우트 (중간)
  - 파일: `vnew/prompts.py`(`EMIT_FX`, `FX_SYSTEM` — design §A.3 원문 그대로), `vnew/plan.py`(`_analyze_fx`, `_fx_blocks`, `fill_fx`, `start_fx_fill`, `run_fx_fill`, `run_conti` 통합, `_commit_step(extra=None)`, `cancel_task`·`reconcile_stale` 단계 목록), `vnew/routes.py`(`POST /p/<pid>/conti/fx`), `tools/check_vpoc.py`.
  - 검사: fx-d(합치기·한도 통합), fx-f, fx-g, fx-n(저장 쪽).
  - 완료 조건: 검사 전부 통과, 가짜 모델 호출 `vnew.conti.fx`가 콘티 1회당 정확히 1번, 보낸 이미지 ≤ 20장이고 번호가 대본 타일 번호와 같다(fx-f), 채우기 실패에도 콘티가 저장된다, 실제 Bedrock 호출 0회(`_guard_real_llm` 목록 비어 있음).

- [x] A3. 영상 프롬프트·분석 규칙 (중간)
  - 파일: `vnew/generate.py`(`_take_timeline_entries`, `_take_prompt` 조각·`RULE_BODY`·줄이기 단계·최소형, `_cut_prompt` FX·EXPRESSION·MOOD·CONTINUITY·⑥-1 단계, `_cast_notes_system`), `vnew/characters.py`(`_analyze_system`), `tools/check_vpoc.py`.
  - 먼저: 변경 전 프롬프트 sha256을 검사 상수로 적는다(통 테이크 6컷 보통·12컷 극단, 컷 단위 웹툰·실사 — 기존 take 검사 고정값을 재사용). 그다음 코드를 바꾼다.
  - 검사: fx-h, fx-i, fx-j, fx-k, fx-n(프롬프트 쪽).
  - 완료 조건: 검사 전부 통과, `VNEW_FX=False` sha256이 변경 전과 같다(fx-i), 켬 상태 통 테이크 ≤ 4,000바이트·컷 단위 ≤ 2,500바이트이고 모든 타임스탬프가 남는다, 기존 take 검사(`check_vnew_take*`)는 기대값 변경 없이 통과한다(필요하면 공통 규칙대로 끔 고정, 보고에 적음), fx-h가 보통·최악 입력의 바이트와 적용 단계를 출력한다.
  - 090 필수 수정(2026-10-30 Claude, 091 실행): ① 통 테이크 `RULE_BODY_SHORT`·`bodyShort` 단계·최소형 MOTION·HARMONY(원문 → 짧은 형), `check_vnew_take_harmony_motion` 끔 고정 해제, 합격 기준 `_FX_TAKE_MUST` ② 컷 단위 `CUT_RULE_BODY` ≤ 90B·`bodyDrop`이 장소 요약보다 먼저·로그 `vnew.gen.cut_prompt steps=`. design §A.4·§A.5·§8, RA4.2·RA4.3·RA5.2·RA5.3.

- [x] A4. 화면·문서·리뷰 (중간)
  - 파일: `frontend/public/vnew.js`(`FX_LABEL_KO`, `fxRow`(분위기 칸 옆 "영어로 옮기려면 [기호·분위기 채우기]" 안내 — 결정 6), `fxToolbar`(확인창에 대상 수·`fxFill.estimateUsd` 금액 숫자 — 결정 1), `renderContiStep` 진행·오류, `previzFxText`·`previzFxLine`), `frontend/public/vnew.css`(`vnew-fx-*`, `vnew-mood-input`, `vnew-previz-fx-line`), `tools/check_vpoc.py`(fx-l), `docs/ANALYSIS.md`, `docs/WORKLOG.md`.
  - 검사: fx-l, 기존 `check_js`.
  - 완료 조건: `node --check frontend/public/vnew.js` 통과, fx-l(node 실행)과 전체 검사 통과, A 바뀐 줄 커버리지 80% 이상과 표, 3단계 리뷰 결과, 문서 갱신.

### A 배포 뒤 확인(설계자 승인 뒤, 유료)

| # | 무엇 | 비용(추정) | 합격 기준 |
|---|---|---|---|
| VA-1 | 개발계 test3 계획에서 ③ [기호·분위기 채우기] 1회 | Fable 5.1 1회 ≈ $0.15 | 빈 컷만 채워짐, 감정 없는 컷은 비어 있음, 같은 기호 추천 ≤ 3컷(그린 기호 제외), 프리비즈 승인 유지, 로그 `vnew.conti.fx` 1줄 |
| VA-2 | 같은 계획 웹툰 통 테이크 1회(480p 15초) | ≈ $3.1 | 프롬프트 ≤ 4,000바이트, `FX:` 조각이 기호 문장 원문, 로그 `steps=`, 설계자가 기호·분위기 반영을 눈으로 확인 |

---

## 묶음 B — 실사 캐스팅 (`VNEW_CASTING`)

- [x] B1. 캐스팅 문장·성별 (작음)
  - 파일: `vnew/casting.py`(신규 — §B.2 순수 함수: `base_text`, `PRESETS`, `preset_of`, `age_phrase`, `look_sentence`, `pattern_warning`, `cast_prompt`, `resolve_gender`), `vnew/characters.py`(`_ANALYZE_TOOL` 사본에 `genderEn`, `_analyze_system` 성별 문장, `analyze_sources` 정리), `vnew/settings.py`(`VNEW_CASTING*`), 템플릿·base, `tools/check_vpoc.py`(`check_vnew_casting` 뼈대 + `PARITY_ENV_KEYS`).
  - 검사: cast-a, cast-b, cast-c, cast-d, cast-m(설정 부분).
  - 완료 조건: 전체 검사 통과, male 문장이 참고 구현 원문과 글자까지 같다(cast-a, 원문은 design §B.2에서 옮긴 상수), 모든 결과에 "webtoon"이 없다, 설정 끔이면 분석 도구 스키마·SYSTEM이 지금과 같다.

- [x] B2. 뽑기·이어하기·라우트 (중간)
  - 파일: `vnew/casting.py`(`casting_key`, `update_settings`, `start_draw`, `run_draw`, `reconcile`, `choose`, `view`, 래퍼 `_fal_request`·`_fetch_url`, `_CAST_RUNNING`), `vnew/characters.py`(`detail_view`가 `casting.reconcile`·`casting.view` 호출), `vnew/routes.py`(캐스팅 3개), `tools/check_vpoc.py`.
  - 검사: cast-e, cast-f, cast-g, cast-h, cast-i, cast-j, cast-m(본문 검사).
  - 완료 조건: 전체 검사 통과, 가짜 fal 요청 본문 키가 정확히 `{prompt, num_images, image_size}`이고 어디에도 URL·data URI가 없다, 원장이 만든 사진 수만큼, 이어하기가 queued 칸을 제출하지 않는다(cast-h), 캐릭터 `status`·variant 상태가 바뀌지 않는다.

- [x] B3. 영상에 쓰기·화면·문서·리뷰 (중간)
  - 파일: `vnew/generate.py`(`_char_entries` `castingKey`, `_real_front_key` — 컷 단위 Kling 실사 payload `_previz_payload_elements`·`_previz_payload_image_list`는 바꾸지 않는다, 결정 4), `frontend/public/vnew.js`(`castingSection`, `schedulePoll` 조건), `vnew.css`(`vnew-casting-*`), `tools/check_vpoc.py`(cast-k, cast-l), `docs/ANALYSIS.md`, `docs/WORKLOG.md`.
  - 검사: cast-k, cast-l, 기존 `check_vnew_real_start`·컷 단위 검사.
  - 완료 조건: `node --check` 통과, 전체 검사 통과, 캐스팅을 골라도 컷 단위 실사 payload가 지금과 같다(cast-k), B 바뀐 줄 커버리지 80% 이상과 표, 3단계 리뷰 결과, 문서 갱신.

### B 배포 뒤 확인

| # | 무엇 | 비용 | 합격 기준 |
|---|---|---|---|
| VB-1 | 남·여 캐릭터 각 1명 캐스팅 뽑기(2장씩) | 4장 × $0.04 = $0.16 | 여성 캐릭터 문장에 "female"·"her"·"She is", 남성은 참고 구현과 같은 문장, 사진이 실사(그림 아님), 머리색 dyed 반영, 무늬 경고가 맞게 뜸 |
| VB-2 | 뽑는 중 앱 파드 재시작 1회(설계자 판단) | 0(이어 조회만) | 제출된 칸은 결과를 받고, 제출 안 된 칸은 실패로 끝나며 다시 과금되지 않는다 |

---

## 묶음 C — 통 테이크 실사 버전 (`VNEW_TAKE_REAL`)

- [x] C1. 소리 거절·기존 결함 (작음)
  - 파일: `vpoc/fal.py`(`_REJECT_AUDIO_MSG`, `rejection` audio, `poll` `failureKind`), `vnew/generate.py`(`_fail_variant` mode 조건·`kind` 인자, `_gen_stale` `activeSince`, `_do_poll_previz` `failureKind`, `retry_segment` `audio`·previz-take 실사·`activeSince`, `_submit_take` `audio` 인자), `vnew/routes.py`(retry 본문 `audio`), `vnew/settings.py`(`VNEW_TAKE_REAL`), 템플릿·base, `tools/check_vpoc.py`(`check_vnew_take_real` 뼈대 + `PARITY_ENV_KEYS`).
  - 검사: lr-g, lr-h, lr-i, 기존 `check_fal_poll_failure`(pf-a~) 그대로.
  - 완료 조건: 전체 검사 통과, 소리 거절이 아닌 `fal.poll` 결과 dict가 지금과 같다(pf-b 기대값 그대로), 웹툰 테이크 소리 거절 → `failureKind:"audio"` → 다시 시도 본문 `generate_audio:false`(lr-g).

- [x] C2. 실사 회차 시작·제출·병합 (큼)
  - 파일: `vnew/generate.py`(`real_estimate`, `start_real`, `final_real_run_key`, `_do_submit_take` real 경로, `_take_payload(variant)`, `_take_location_jpeg(variant)`, `_take_prompt(variant)` 실사 조각·`_assert_no_webtoon`, `_do_concat` 회차 키, `select_variant` previz-take 조건, `gen_view` 실사 필드), `vnew/fx.py`(`strip_drawing_words` — A1에서 이미 있으면 그대로), `vnew/routes.py`(`POST /p/<pid>/generate/real`), `tools/check_vpoc.py`.
  - 먼저: 웹툰 take 프롬프트 sha256(켬, A 적용 후)을 검사 상수로 적는다(lr-e).
  - 검사: lr-a, lr-b, lr-c, lr-d, lr-e, lr-f, lr-j, lr-k, lr-l, lr-m.
  - 완료 조건: 전체 검사 통과, 실사 프롬프트에 "webtoon"이 없고 검사가 걸리면 제출·원장 0(lr-d), 웹툰 take 프롬프트·결과가 그대로(lr-e·lr-f), 실사 제출이 세마포어 안에서 이루어지고 동시 상한을 지킨다(lr-m), 실제 fal·Bedrock 호출 0회.

- [x] C3. 화면·문서·리뷰 (중간)
  - 파일: `frontend/public/vnew.js`(`finalResults` take 분기·`takeRealColumn`·`openRealConfirm`·`takeCompare`·`takeRow` 실사 칸·`cutCell` 소리 없이 다시·상태 띠 문구), `vnew.css`(`vnew-take-compare*`, `vnew-real-*`), `tools/check_vpoc.py`(lr-n, lr-o), `docs/ANALYSIS.md`(§3.6.1 "문제" 목록 상태 갱신 포함), `docs/WORKLOG.md`.
  - 검사: lr-n, lr-o, 기존 `check_js`.
  - 완료 조건: `node --check` 통과, 전체 검사 통과, C 바뀐 줄 커버리지 80% 이상과 표, 3단계 리뷰 결과(특히 512Mi 메모리·재시작·되돌리기), 문서 갱신.

### C 배포 뒤 확인

| # | 무엇 | 비용(추정) | 합격 기준 |
|---|---|---|---|
| VC-1 | 웹툰 take가 끝난 gen에서 실사 버전 1회(480p 15초) | ≈ $3.1 | 실사 결과(그림체 아님), 같은 구도·컷·타이밍, 캐스팅 사진 인물, 로그에 `RealPromptWebtoon` 없음, 나란히 보기 동시 재생 |
| VC-2 | 같은 gen 실사 다시 만들기 1회 | ≈ $3.1 | `realHistory` 1개, 이전 실사 영상 재생 가능, 웹툰 그대로, 파일 이름 `final-real-r2.mp4` |
| VC-3 | (소리 거절이 실제로 나오면) 소리 없이 다시 만들기 | 테이크 1개 | 한국어 안내, 버튼, 결과가 무음이고 다른 테이크와 이어 붙음 |

### 지시서 120 — ③ 콘티 그림 보기 (2026-11-04)

- [x] 120. 콘티 요청에 웹툰 타일(Panel N of M:)·CONTI_SYSTEM 샷 규칙·EMIT_CONTI 선택 필드 6개·저장 정리(merge_fill·pply_cap)·채우기 건너뛰기·글만 다시 시도·③ 카드 머리 글
  - 파일: pp/src/vnew/plan.py, prompts.py, settings.py(VNEW_CONTI_IMAGES), deploy/config.env.example, rontend/public/vnew.js, new.css, 	ools/check_vpoc.py(ci-a~f).
  - 남은 것: VNEW_CONTI_IMAGES는 k8s/workloads.yaml.tmpl 배선이 없어 지금은 코드 기본(true)으로만 동작한다.

### 작업 123 — ⑤ 실사·애니(웹툰) 처음부터 함께 (2026-11-04)

- [x] 123. POST /generate `styles`(통 테이크 + VNEW_TAKE_REAL만 "real", 그 밖 400)·합계 409·gen.autoReal·웹툰 완료 순회에서 start_real 1번 자동 시작·실패/취소 autoReal.error·보기 estimate.realUsd·realBlock·autoReal, 확인창 "만들 영상" 체크 2개·합계 문구, 실사 열 자동 시작 안내, ⑤⑥⑦ "애니(웹툰)" 이름
  - 파일: app/src/vnew/generate.py, routes.py, frontend/public/vnew.js, vnew.css, tools/check_vpoc.py(tb-a~f, lr-n·edu 기대 이름 갱신).
  - 남은 것: 웹툰이 끝난 뒤 자동 시작 전에 파드가 죽으면 자동 시작은 다시 오지 않는다(화면의 [실사 버전 만들기] 버튼으로 만든다). 400 BadStyles·RealNotTake는 USER_FACING_KINDS(vpoc/errors.py, 범위 밖)에 없어 운영에서는 코드만 보인다.
