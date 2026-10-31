# 요구사항 — bl-look (실사 캐스팅 외모 문장에 BL 역할·유형 참고 설명)

> 작성 2026-10-31 · Kiro(claude-opus-5.5) · 근거: 지시서 `.kiro/handoff/103-bl-look-hints.md`, 사용자 요청(2026-10-31)
> "참고만 하는 것이다. 외모 프롬프트를 고도화하고 정보를 많이 주기 위함이다."
> 경로의 `app/`·`frontend/`·`k8s/`·`deploy/`·`tools/`·`docs/`는 모두 `AI-POC-whynot/` 아래다. 줄 번호는 2026-10-31 작업 트리 기준이다.
> 갱신 2026-10-31(지시서 `.kiro/handoff/104-bl-from-description.md`): BL 유형의 근거를 화면 선택에서 **사용자가 쓴 캐릭터 설명
> (`char.extraPromptKo`)** 으로 바꿨다. 사용자 지시: "이런 BL 요소는 사용자가 해당 캐릭터에 대한 설명에 떡대공이다 이런 식으로
> 넣는다면 그때 반영을 하도록 해라." R2·R4·R6은 R10·R11로 바뀌었다(각 절의 104 표시). R1·R3·R5·R7·R8은 그대로이고 R9~R12를 더했다.
> 갱신 2026-10-31(104 피드백, Claude 결정): 찾기에 앞 경계·뒤쪽 제외(R9.8·R9.9), 부르는 곳이 없는 103 정의 지우기(R13, Claude 승인),
> `VNEW_BL_LOOK` 주석 고치기(R14). R2·R6의 103 기록 중 남긴 함수에 대한 말(R10.3 "읽지도 지우지도 않는다"는 저장값 이야기라 그대로)은 R13이 정본이다.

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
| 캐릭터 설명 | 사용자가 캐릭터 만들기의 추가 요청 칸에 쓴 한국어 글(`char.extraPromptKo`, ≤500자). 104부터 BL 유형의 유일한 근거다. LLM이 쓴 `analysis.descriptionKo`는 아니다 |
| 유형 이름 | 표 label을 "/"로 나눈 한국어 이름 13개: 광공, 집착공, 츤데레공, 다정공, 떡대공, 무심공, 미인수, 꽃수, 떡대수, 무심수, 능력수, 굴림수, 연하수 |

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

> 104에서 바뀜(R10.3·R10.4): 요청의 `blRole`·`blType`은 받지 않고, 저장된 값은 읽지도 지우지도 않는다. 아래 1~6은 103 기록이다.

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

> 104에서 바뀜(R10.5): `blRole`·`blType`·`blRoles`·`blTypes`를 빼고 `blDetected`·`blHintsEn`을 둔다. 아래 1~2는 103 기록이다.

1. 켬이면 `blRole`·`blType`·`blRoles`(`[{key, label}]`)·`blTypes`(`list_types()`)·`blHintsEn`(지금 외모 문장·느낌·성별로
   실제 뽑기 문장에 더해질 참고 문장, 없으면 `""`)을 더한다.
2. 저장값이 깨졌거나(글이 아님·모르는 값·역할과 맞지 않는 유형) 하면 `""`로 보인다.

## R5. 설정 `VNEW_BL_LOOK`

1. 코드 기본 true(`vnew/settings.py` `_bool`), base `deploy/config.env.example`, 앱 env `k8s/workloads.yaml.tmpl`.
2. false면 보기에 BL 필드가 없고, 저장값이 있어도 문장에 더하지 않으며, 요청의 `blRole`·`blType`은 무시한다(400 없음, 저장 없음).
   저장된 값은 지우지 않는다.
3. 되돌리기는 `config.<target>.env`에 `VNEW_BL_LOOK=false` 한 줄.

## R6. 화면(`frontend/public/vnew.js`, `vnew.css`)

> 104에서 바뀜(R11): 역할·유형 선택을 없애고 읽기 전용 한 줄을 둔다. 저장·뽑기 본문에 BL 키가 없다. 아래 1~5는 103 기록이다.

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

---

## R9. 캐릭터 설명에서 유형 찾기(지시서 104, `genre_bl.py`)

1. THE SYSTEM SHALL `detect_types(text_ko) -> list[str]`를 둔다. 글에 유형 이름(용어 표 13개)이 있으면 그 유형 id를 낸다.
   이름 목록은 표의 label을 "/"로 나눠 만든다(광공 / 집착공 → 광공·집착공, 둘 다 `gwanggong`).
2. 이름은 붙어 있는 꼴 그대로 찾는다. "떡대공이다"·"떡대공, 무심공"은 찾고, "떡대 공원"은 찾지 않는다. 뒤에 조사·서술어가 붙어도 된다.
3. IF 이름 바로 뒤 6자 안(공백 포함)에서 "아님"·"아니"·"말고"가 시작되면, THEN THE SYSTEM SHALL 그 자리의 이름을 찾지 않은 것으로 본다.
4. 나온 자리 순서대로, 중복 없이, 최대 2개를 낸다. 3개 이상이면 앞의 2개다.
5. 글이 아니거나(None·숫자·목록) 비면 `[]`. 역할 낱말(공·수)만 있고 유형 이름이 없으면 `[]`.
6. 여러 유형이면 `hint_pieces`·`look_hints`가 조각을 합친다. 같은 분류는 앞의 유형이 이긴다. 형태·인상 규칙(R1.6)과 1500자 줄이기(R3.3)는
   103 그대로다. 유형이 하나면 결과가 103과 같다.
7. 이 모듈은 여전히 저장·외부 호출이 없고 import는 `re`뿐이다.
8. (104 피드백) 앞 경계: IF 이름 바로 앞 글자가 한글 음절(가~힣)이면, THEN THE SYSTEM SHALL 그 자리를 찾지 않는다. 글 처음·공백·문장부호·
   숫자·영문(그 밖의 한글 음절이 아닌 글자) 뒤에서만 찾는다. 예외 접두어 `_PREFIX_OK = ("집착",)`: 이름 바로 앞이 "집착"으로 끝나면 찾는다
   ("집착광공" → 광공).
9. (104 피드백) 뒤쪽 제외: IF 다음 중 하나면, THEN THE SYSTEM SHALL 그 자리를 찾지 않는다.
   1. 모든 이름: 이름 바로 뒤 첫 글자가 "원"(공원·수원) 또는 "업"(공업·수업).
   2. 꽃수만: 이름 끝 바로 뒤 6자 안(공백 포함)에서 "놓"·"자수"·"무늬"·"장식"·"프린트"·"패턴"·"그림"이 시작("꽃수를 놓은 셔츠", "꽃수 무늬 넥타이").
   3. 뒤에 조사·서술어가 붙는 꼴("무심수다", "무심수인")은 찾는다. 이름("무심수아")과 조사는 구별하지 않는다(찾는 쪽).
10. (104 피드백) 예: 찾지 않음 — "꽃수를 놓은 셔츠", "꽃수 무늬 넥타이", "광공업", "떡대공원", "형광공장", "수원 출신"(유형 이름 없음).
    찾음 — "집착광공이다", "그는 꽃수다", "(떡대공)", "떡대공,무심공", "수원 출신 무심수아"(무심수, R9.9.3).

## R10. 캐스팅(지시서 104, `casting.py`, `routes.py`)

1. 참고 문장의 근거는 `detect_types(char.extraPromptKo)`뿐이다. `analysis.descriptionKo`·`appearanceEn`·외모 문장(`lookEn`)·저장된
   `blRole`·`blType`은 근거가 아니다.
2. WHEN 설명에서 유형을 찾지 못하면, THE SYSTEM SHALL 캐스팅 문장을 103 이전과 한 글자도 같게 만든다(저장된 `blType`이 있어도).
3. 캐스팅에 저장된 `blRole`·`blType`(103 선택값)은 읽지 않고 지우지도 않는다.
4. WHEN `PUT /casting` 또는 `POST /casting/draw`에 `blRole`·`blType`이 오면, THE SYSTEM SHALL 무시한다(400 없음, 저장 없음, 각각 200·202).
   라우트는 BL 값을 넘기지 않는다.
5. `view`(켬): `blRole`·`blType`·`blRoles`·`blTypes`를 빼고 끝에 `blDetected`(`[{id, label}]`, label은 표 label)와 `blHintsEn`(찾은 유형·
   지금 외모 문장·느낌·`resolve_gender`로 뽑기 문장에 더해질 참고 문장, 없으면 `""`)을 둔다. `VNEW_BL_LOOK` 끔이면 BL 필드가 없다(103과 같다).
6. 뽑기 시작 로그 `vnew.casting.draw_start … bl=` 끝말은 찾은 유형 id를 `+`로 이은 값, 없으면 `-`.
7. `BadBlType` 운영 문구는 `vpoc/errors.py` 표에 남긴다.
8. `VNEW_BL_LOOK=false`면 설명에 유형이 있어도 문장에 더하지 않는다(R5.2와 같은 뜻).

## R11. 화면(지시서 104, `vnew.js`, `vnew.css`)

1. 캐스팅 패널의 BL 역할·유형 선택을 없앤다. 서버 응답이 103 꼴(`blRoles`·`blTypes`)이어도 선택을 그리지 않는다.
2. 보기에 `blDetected`가 있으면 성별 칸 바로 뒤에 읽기 전용 한 줄을 둔다.
   - 찾았으면: "캐릭터 설명에서 찾은 BL 유형: 떡대공 — 참고로 더해지는 외모 설명(영어): …"(참고 문장이 비면 앞부분만, 유형이 둘이면 ", "로 잇는다).
   - 못 찾았으면: 작은 글씨 안내 "캐릭터 설명에 '떡대공'처럼 BL 유형을 적으면 실사 사진에 참고로 반영됩니다."
   - `blDetected`가 없으면(끔) 줄이 없다.
3. 저장·뽑기·되돌리기 본문에 `blRole`·`blType`을 보내지 않는다.
4. WHEN 사용자가 추가 요청을 고쳐 다시 만들면, THE SYSTEM SHALL 상세를 다시 받아 그리고 그 줄은 서버가 새 설명으로 다시 계산한 값을 보인다.
5. 글은 textContent로만 넣는다.

## R12. 지침·문서(지시서 104)

1. `.kiro/steering/genre-bl.md`에 "BL 유형은 사용자가 쓴 캐릭터 설명(extraPromptKo)에 적혀 있을 때만 반영" 한 줄.
2. `docs/WORKLOG.md` 104 항목, `docs/ANALYSIS.md` 캐스팅 보기 필드 표·BL 표·설정 표·검사 표.

## R13. 부르는 곳이 없는 103 정의 지우기(104 피드백, Claude 승인)

1. THE SYSTEM SHALL `casting.py`의 `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`를 지운다. 이 함수만 쓰던 문구 상수
   `_MSG_BAD_BL`·`_MSG_BL_MISMATCH`도 함께 지운다.
2. THE SYSTEM SHALL `vnew.js`의 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields`와 이 함수만 쓰던
   `CASTING_BL_NO_TYPE`을 지운다. `castingBlHintText`·`CASTING_BL_HINT_LEAD`는 `castingBlLine`이 써서 남긴다.
3. THE SYSTEM SHALL `vnew.css`의 `.vnew-casting-row`·`.vnew-casting-bl-role`·`.vnew-casting-bl-type`·`.vnew-casting-bl-hint`를 지운다.
4. 저장된 `blRole`·`blType`과 `_empty_casting`의 기본값, `vpoc/errors.py`의 `BadBlType`, `genre_bl`의 `roles`·`fits`·`list_types`는
   이번 승인 범위가 아니라 그대로 둔다.
5. 검사가 지운 정의를 직접 부르던 부분만 함께 정리한다.

## R14. `VNEW_BL_LOOK` 주석(104 피드백)

1. `app/src/vnew/settings.py`, `deploy/config.env.example`, `k8s/workloads.yaml.tmpl`의 `VNEW_BL_LOOK` 주석을 "캐릭터 설명(추가 요청)에 적힌
   BL 유형을 참고 문장으로 더한다"는 뜻으로 고친다. 값·자리·줄 수는 그대로, base의 되돌리기 문구("되돌리기: config.<target>.env 에
   VNEW_BL_LOOK=false")도 그대로 둔다.

## 범위 밖

- 수정 금지: `deploy/config.dev.env`, `deploy/config.prod.env`, `.gitlab-ci.yml`, `app/src/vnew/generate.py`, `whynot-20261029`,
  `C:\dev\ai\AI-POC-whynot`(읽기만).
- `vpoc/errors.py`는 103 피드백에서만 `BadBlType` 한 줄을 허용받았다(R8).
- 영상(통 테이크·컷 단위) 프롬프트, 관계 단계·포지션 행동 문장(옛 모듈의 나머지)은 옮기지 않는다.
- (104) 수정 금지: `app/src/vnew/characters.py`(Astra 분석 경로), `generate.py`, `deploy/config.*.env`, `.gitlab-ci.yml`, `whynot-20261029`.
  `settings.py`·`deploy/config.env.example`·`k8s/workloads.yaml.tmpl`은 104 범위 밖이라 그 안의 103 주석("고르면")은 그대로 둔다.
- (104 피드백) 위 세 파일은 `VNEW_BL_LOOK` 주석만 고친다(R14). `characters.py`·`generate.py`·`deploy/config.*.env`·`.gitlab-ci.yml`·
  `vpoc/errors.py`는 그대로다.
