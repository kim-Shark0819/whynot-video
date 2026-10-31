# 요구사항 — bl-look (실사 캐스팅 외모 문장에 BL 역할·유형 참고 설명)

> 작성 2026-10-31 · Kiro(claude-opus-5.5) · 근거: 지시서 `.kiro/handoff/103-bl-look-hints.md`, 사용자 요청(2026-10-31)
> "참고만 하는 것이다. 외모 프롬프트를 고도화하고 정보를 많이 주기 위함이다."
> 경로의 `app/`·`frontend/`·`k8s/`·`deploy/`·`tools/`·`docs/`는 모두 `AI-POC-whynot/` 아래다. 줄 번호는 2026-10-31 작업 트리 기준이다.

## 목적

실사 캐스팅 사진 문장(`casting.cast_prompt`, 영어)에 BL 장르 지식(공/수 역할, 떡대공·무심공·미인수 같은 유형)을
**참고 설명 한 문장**으로 더한다. 그림·사진에서 읽은 사실(머리 모양·색, 옷, 나이, 성별)이 항상 우선이고, 유형은 그림이
말해 주지 않는 빈칸(체격, 키 느낌, 얼굴 인상, 눈빛, 표정, 자세, 몸가짐)만 채운다.

## 용어

| 용어 | 뜻 |
|---|---|
| 역할 | `top`(공), `bottom`(수), `reversible`(리버서블), `""`(없음) |
| 유형 | 광공/집착공·떡대공·미인수 등 12종. 역할 하나에 속한다(`role`) |
| 조각 | 유형의 영어 외모 참고 한 덩어리(예: "a firm square jawline"). 분류 하나를 단다 |
| 분류 | 형태 `build`·`height`·`faceShape`·`eyeShape`(겹치면 뺀다), 인상 `faceImpression`·`gaze`·`expression`·`posture`·`grooming`(늘 더한다). 103 피드백(2026-10-31)에서 `face`·`eyes`를 모양과 인상으로 나눴다 |
| 외모 문장 | 캐스팅 문장에 들어가는 외모(`current_look` → `prompt_look`, ≤300자) |
| 참고 문장 | 외모 뒤에 붙는 "He has …." 한 문장(`blHintsEn`) |

---

## R1. 지식 모듈 `app/src/vnew/genre_bl.py`(새 파일)

1. THE SYSTEM SHALL 역할 4개(`""`·`top`·`bottom`·`reversible`)와 한국어 라벨(없음·공·수·리버서블)을 둔다.
2. THE SYSTEM SHALL 유형 12종을 둔다. 옛 `C:\dev\ai\AI-POC-whynot\app\src\scene_video\genre_bl.py` TAGS 10종(id·label·desc 그대로)에
   떡대공(`ttokdae_gong`)·무심공(`musim_gong`)을 더한다. 공: 광공/집착공·츤데레공·다정공·떡대공·무심공. 수: 미인수·꽃수·떡대수·
   무심수·능력수·굴림수·연하수.
3. 각 유형은 `role`·`label`(한국어)·`desc`(한국어 한 줄)·`lookEn`(분류를 단 영어 조각 목록)을 갖는다.
4. 영어 조각 규칙
   1. 은어(gong, su, seme, uke, top, bottom, BL, boys love)를 쓰지 않는다. 관찰할 수 있는 외모 말로만 쓴다.
   2. 머리카락·옷·나이·피부색 조각을 두지 않는다(그림이 정한다).
   3. 성인 남성 기준. 수 유형은 "masculine"·"adult man" 계열 낱말을 한 조각 이상 넣는다.
   4. 성적·노출 표현, 폭력적 묘사를 쓰지 않는다(광공은 "imposing presence" 정도까지). 미성년으로 읽히는 낱말도 쓰지 않는다.
   5. 한 유형의 조각을 모두 이으면 350자 이하.
5. `list_types()`는 화면용 목록 `{id, role, label, desc, lookEn}`(lookEn = 조각을 이은 미리보기 글)을 준다.
6. `look_hints(bl_type, look_en)`는 그 유형 조각을 쉼표로 이은 영어를 준다. 인상 조각은 늘 넣고, 형태 조각은 외모 문장에 같은 형태
   분류가 있으면 뺀다(103 피드백: 그림이 실제로 보여 주는 형태 사실과 부딪칠 때만 뺀다). 형태 분류는 낱말 표로 판정한다. 모르는 유형이면 `""`.
7. 이 모듈은 아무것도 저장하지 않고 외부 호출이 없다.
8. (103 피드백) 형태 조각은 모양 사실만, 인상 조각은 모양 사실 없이 쓴다. 한 조각에 모양과 인상이 섞이면 둘로 나눈다
   (떡대공 "steady, deep-set eyes" → "deep-set eyes" + "a steady gaze"). 미인수 피부 조각은 "a clean, clear complexion"(현실 배우풍
   프리셋의 피부 결 문구와 부딪치지 않게).
9. (103 피드백) 실제 분석 꼴 6개(cast-b `_CAST_LOOK_FEEDBACK` 결과 문장)에 떡대공을 골랐을 때 남는 조각이 평균 4개 이상이다.

## R2. 캐스팅 설정(`app/src/vnew/casting.py`, `routes.py`)

1. THE SYSTEM SHALL casting에 `blRole`(기본 `""`)·`blType`(기본 `""`)을 둔다.
2. WHEN `PUT /casting` 또는 `POST /casting/draw`가 `blRole`·`blType`을 보내면, THE SYSTEM SHALL `_apply_settings`로 받는다.
3. IF 역할·유형이 모르는 값이거나 글이 아니면, THEN THE SYSTEM SHALL 400 `BadBlType`.
4. IF 요청의 유형이 역할(요청 역할, 없으면 저장된 역할)과 맞지 않으면, THEN THE SYSTEM SHALL 400 `BadBlType`. 리버서블은 모든 유형을 받는다.
5. WHEN 역할을 `""`로 바꾸면, THE SYSTEM SHALL 유형도 `""`로 둔다.
6. WHEN 역할만 바꾸고 저장된 유형이 새 역할과 맞지 않으면, THE SYSTEM SHALL 유형을 `""`로 둔다.

## R3. 캐스팅 문장

1. `cast_prompt(look, preset, gender, bl_type="")`. 유형이 없거나 모르는 값이면 결과가 지금과 한 글자도 같다.
2. 유형이 있으면 외모 뒤에 참고 문장 한 개를 더한다: `"… with dark brown eyes. He has A, B, and C."`. 은어 없음.
3. 1500자(`PROMPT_MAX`)를 넘으면 참고 조각을 끝에서부터 뺀다. 다 빼도 넘으면 참고 문장이 없다(지금 동작).
4. 유형이 있고 성별이 `unknown`이면 male 낱말을 쓴다(프리셋·주어·외모 문장의 person·their 등). 사용자가 female을 지정했으면
   성별은 그대로 두고 참고 문장만 더한다.
5. 300자 외모 문장(`look_sentence`·`auto_look`·`current_look`·사용자가 고친 문장)과 영상 쪽 `_real_appearance`는 바꾸지 않는다.

## R4. 보기(`casting.view`)

1. 켬이면 `blRole`·`blType`·`blRoles`(`[{key, label}]`)·`blTypes`(`list_types()`)·`blHintsEn`(지금 외모 문장·느낌·성별로
   실제 뽑기 문장에 더해질 참고 문장, 없으면 `""`)을 더한다.
2. 저장값이 깨졌거나(글이 아님·모르는 값·역할과 맞지 않는 유형) 하면 `""`로 보인다.

## R5. 설정 `VNEW_BL_LOOK`

1. 코드 기본 true(`vnew/settings.py` `_bool`), base `deploy/config.env.example`, 앱 env `k8s/workloads.yaml.tmpl`.
2. false면 보기에 BL 필드가 없고, 저장값이 있어도 문장에 더하지 않으며, 요청의 `blRole`·`blType`은 무시한다(400 없음, 저장 없음).
   저장된 값은 지우지 않는다.
3. 되돌리기는 `config.<target>.env`에 `VNEW_BL_LOOK=false` 한 줄.

## R6. 화면(`frontend/public/vnew.js`, `vnew.css`)

1. 보기에 `blRoles`·`blTypes`가 있으면 성별 칸 옆에 "BL 역할"(없음·공·수·리버서블)과 "유형" 선택을 둔다. 없으면 지금 화면과 같다.
2. 유형 목록은 역할에 맞는 것만(리버서블은 전부). 역할이 없음이면 유형 칸을 숨긴다. 각 유형 option의 title은 `desc`.
3. 그 아래 한 줄 "참고로 더해지는 외모 설명(영어): …"(`blHintsEn`). 비었으면 숨긴다.
4. 저장·뽑기 때 `blRole`·`blType`을 보낸다. lookEn 전송 규칙(D8: 고친 경우만)은 그대로.
5. 뽑는 중·캐릭터를 만드는 중이면 두 선택을 막는다. 글은 textContent·속성으로만 넣는다.

## R7. 지침·문서

1. 고객 저장소 `.kiro/steering/genre-bl.md`(옛 규칙 + "외모는 참고만, 그림 사실 우선"). fileMatch는
   `**/app/src/vnew/genre_bl.py`, `**/app/src/vnew/casting.py`.
2. `docs/WORKLOG.md` 103 항목, `docs/ANALYSIS.md` 캐스팅 보기 필드·설정 표.

## R8. 운영 문구 `BadBlType`(103 피드백, 2026-10-31)

1. THE SYSTEM SHALL `app/src/vpoc/errors.py` `USER_FACING_KINDS`에 `BadBlType`을 둔다(운영 `RAW_ERRORS=false`에서도 문구를 그대로 보인다).
2. 모르는 역할·유형 문구는 "알 수 없는 BL 역할·유형입니다." 하나다. 역할과 유형의 짝이 틀린 경우는 지금 고정 문구를 그대로 쓴다.
3. 문구는 고정 문자열이고 사용자 입력을 되풀이하지 않는다.

## 범위 밖

- 수정 금지: `deploy/config.dev.env`, `deploy/config.prod.env`, `.gitlab-ci.yml`, `app/src/vnew/generate.py`, `whynot-20261029`,
  `C:\dev\ai\AI-POC-whynot`(읽기만).
- `vpoc/errors.py`는 103 피드백에서만 `BadBlType` 한 줄을 허용받았다(R8).
- 영상(통 테이크·컷 단위) 프롬프트, 관계 단계·포지션 행동 문장(옛 모듈의 나머지)은 옮기지 않는다.
