# 작업 — bl-look

> 요구사항 `requirements.md`(R1~R7), 설계 `design.md`. 경로는 `AI-POC-whynot/` 아래다.

## 공통 규칙

- 수정 범위: design.md §4~§7과 지시서 「범위」 파일만. 기존 함수 정의를 지우지 않는다. 줄 끝은 LF다.
- 검사: `tools/check_vpoc.py`에 그룹 `check_vnew_bl_look`(접두어 `bl-`)을 더하고 `main()` 마지막에서 부른다. 외부 호출은 모두 가짜(fal·Bedrock·S3 0회).
- 허용 명령: `python`, `node`. git·aws·kubectl·네트워크·유료 API 금지. 커밋하지 않는다.
- 완료 조건(공통)
  1. `node --check frontend/public/vnew.js` 통과.
  2. `python -X utf8 tools/check_vpoc.py`가 "전부 통과"(새 그룹·기존 전 그룹).
  3. 새 그룹 `[ok  ]` 줄에 bl-a~bl-i가 모두 나온다(103 피드백 뒤 bl-j 까지).
  4. 줄 끝 LF, 공백만 바뀐 줄 0, 지운 정의 0, 저장소 안 부산물 0.

---

- [x] 1. 지식 모듈 (R1)
  - **수정 파일**: `app/src/vnew/genre_bl.py`(새)
  - **검사**: bl-a, bl-b
  - **완료 조건**: 공통 1~4. 12종·조각 350자 이하·금지 낱말 0.

- [x] 2. 캐스팅 설정·문장·보기·설정 플래그 (R2~R5)
  - **수정 파일**: `app/src/vnew/casting.py`, `app/src/vnew/routes.py`, `app/src/vnew/settings.py`, `deploy/config.env.example`, `k8s/workloads.yaml.tmpl`, `tools/check_vpoc.py`(`PARITY_ENV_KEYS`)
  - **검사**: bl-c~bl-h, 기존 cast-a~m(켬 그대로, cast-j 키 집합만 BL 5개 추가), rw-a~c
  - **완료 조건**: 공통 1~4. 유형 없음 결과가 지금과 같다.

- [x] 3. 화면 (R6)
  - **수정 파일**: `frontend/public/vnew.js`, `frontend/public/vnew.css`
  - **검사**: bl-i, 기존 cast-l(기대값 그대로)
  - **완료 조건**: 공통 1~4.

- [x] 4. 지침·문서 (R7)
  - **수정 파일**: `.kiro/steering/genre-bl.md`(새, 저장소 루트), `docs/WORKLOG.md`, `docs/ANALYSIS.md`
  - **완료 조건**: 세 파일에 design.md의 항목이 있고 줄 끝이 LF다.

- [x] 5. 리뷰·커버리지
  - 보안 → 성능 → 운영성 리뷰, `coverage run`(저장소 밖 data-file)로 바뀐 서버 줄 80% 이상.

---

## 103 피드백(2026-10-31, Claude) — 남은 이슈 1·3·4

- [x] 6. 분류를 형태·인상으로 나눔 (R1.6·R1.8·R1.9, design D3·D13~D16)
  - **수정 파일**: `app/src/vnew/genre_bl.py`(`FORM_CATEGORIES`·`IMPRESSION_CATEGORIES`·`CATEGORIES`, `TYPES` 이름표·나눈 조각·떡대공 인상 2조각, 형태 판정 낱말 표 `_CATEGORY_RE`·`_JOIN_STOP`·`_PHRASE_STOP`·`_before`, `look_categories`·`hint_pieces`)
  - **검사**: bl-a·bl-b(기대값 새 규칙), bl-d(떡대공 전문)·bl-e·bl-h·bl-i(참고 문장 없는 상세)
  - **완료 조건**: 공통 1~4. 실제 분석 꼴 6개 떡대공 평균 조각 ≥ 4.

- [x] 7. 미인수 피부 조각 (R1.8, design D17)
  - **수정 파일**: `app/src/vnew/genre_bl.py`
  - **검사**: bl-a

- [x] 8. 운영 문구 `BadBlType` (R8, design D11·D17)
  - **수정 파일**: `app/src/vpoc/errors.py`(`USER_FACING_KINDS` 한 줄), `app/src/vnew/casting.py`(`_MSG_BAD_BL`)
  - **검사**: bl-f(문구), bl-j(표·던지는 곳·운영 봉투·라우트), 기존 cast-l·lr-a·ad-a·(d) 그대로
  - **완료 조건**: 공통 1~4.

- [x] 9. 문서·리뷰 — `docs/WORKLOG.md` 103 항목에 이어 쓰기, `docs/ANALYSIS.md`, `.kiro/steering/genre-bl.md`, 이 스펙. 3단계 리뷰·커버리지.

---

## 지시서 104(2026-10-31) — BL 유형은 사용자가 쓴 캐릭터 설명에 적었을 때만

> 요구사항 R9~R12, 설계 D18~D27·§9. 공통 규칙·완료 조건은 위와 같다(새 그룹 줄에 bl-a~l).

- [x] 10. 찾기·합치기 (R9, design D19~D21·§9.1)
  - **수정 파일**: `app/src/vnew/genre_bl.py`(`NEGATIONS`·`NEGATION_WINDOW`·`DETECT_MAX`·`_NAME_TO_ID`·`_NAME_RE`, `detect_types`·`known_types`·`_piece_categories`·`merged_pieces`, `hint_pieces`·`look_hints` 유형 목록)
  - **검사**: bl-k, bl-l, bl-a·bl-b 그대로
  - **완료 조건**: 공통 1~4. 유형 하나의 결과가 103과 같다(bl-b·bl-d 기대값 그대로).

- [x] 11. 캐스팅·라우트 (R10, design D18·D22~D24·D26·§9.2·§9.3)
  - **수정 파일**: `app/src/vnew/casting.py`(`_bl_types_now`, `cast_prompt`·`_cast_text`, `_apply_settings`·`update_settings`·`start_draw`, `view`, docstring·주석), `app/src/vnew/routes.py`
  - **검사**: bl-c·bl-e·bl-f·bl-g·bl-h·bl-j, cast-j(키 집합만 `blDetected`·`blHintsEn`), cast-a~m·cf-*·rw-* 그대로
  - **완료 조건**: 공통 1~4. 설명에 유형이 없으면 문장이 103 이전과 같다.

- [x] 12. 화면 (R11, design D25·§9.4)
  - **수정 파일**: `frontend/public/vnew.js`(`castingBlLine`·상수 2개, `castingSection`, 주석), `frontend/public/vnew.css`
  - **검사**: bl-i, cast-l·cf-s 그대로
  - **완료 조건**: 공통 1~4.

- [x] 13. 지침·문서 (R12)
  - **수정 파일**: `.kiro/steering/genre-bl.md`, `docs/WORKLOG.md`(104 항목), `docs/ANALYSIS.md`, 이 스펙
  - **완료 조건**: 줄 끝 LF.

- [x] 14. 리뷰·커버리지·변형 검사
  - 보안 → 성능 → 운영성 리뷰, `coverage run`(저장소 밖 data-file)로 바뀐 서버 줄 80% 이상, 변형 검사(저장소 밖 스크립트) 갱신.

---

## 104 피드백(2026-10-31, Claude 결정) — 남은 이슈 1·2·3

> 요구사항 R9.8~R9.10·R13·R14, 설계 D28~D32·§10. 공통 규칙·완료 조건은 위와 같다. 지운 정의 = §10.2 표뿐이어야 한다(공통 완료 조건 4의 예외, Claude 승인).

- [x] 15. 앞 경계·뒤쪽 제외 (R9.8~R9.10, design D28~D30·§10.1)
  - **수정 파일**: `app/src/vnew/genre_bl.py`
  - **검사**: bl-k(예 추가·기대값 "떡대공원" → 없음), bl-f·bl-h·bl-i 그대로
- [x] 16. 103 정의 지우기 (R13, design D31·§10.2)
  - **수정 파일**: `app/src/vnew/casting.py`, `frontend/public/vnew.js`, `frontend/public/vnew.css`, `tools/check_vpoc.py`(bl-f·bl-i·bl-j)
- [x] 17. `VNEW_BL_LOOK` 주석 (R14, design D32)
  - **수정 파일**: `app/src/vnew/settings.py`, `deploy/config.env.example`, `k8s/workloads.yaml.tmpl`(주석만), `tools/check_vpoc.py`(bl-g)
- [x] 18. 문서·리뷰 — WORKLOG 104 항목에 이어 쓰기, ANALYSIS, 이 스펙. 3단계 리뷰·커버리지·변형 검사.
