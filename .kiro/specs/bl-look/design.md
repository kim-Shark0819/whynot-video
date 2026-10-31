# 설계 — bl-look

> 요구사항 `requirements.md`(R1~R8). 경로는 `AI-POC-whynot/` 아래다. 2026-10-31 103 피드백(Claude 결정)으로 D3·D11 을 바꾸고
> D13~D17 을 더했다(분류를 형태·인상으로 나눔, 미인수 피부 조각, 운영 문구 `BadBlType`).
> 2026-10-31 지시서 104(R9~R12): 근거를 사용자가 쓴 캐릭터 설명(`char.extraPromptKo`)으로 바꿨다. D18~D27 과 §9 를 더했다.
> §4·§5·§7 의 103 표는 기록으로 남기고, 104 에서 바뀐 것은 §9 가 정본이다.
> 2026-10-31 104 피드백(Claude 결정, R9.8~R9.10·R13·R14): D28~D32 와 §10 을 더했다. D22 의 "103 정의를 남긴다"는 D31 로 바뀌었다.

## 1. 지금 흐름(2026-10-31 코드 확인)

| 위치 | 동작 |
|---|---|
| `casting.py:716` `look_sentence` → `:956` `auto_look` → `:967` `current_look` | 분석으로 만든 외모 문장(≤300자) 또는 사용자가 고친 문장 |
| `casting.py:778` `prompt_look` | 외모 문장 한 줄 정리(그림 낱말·성별 낱말·dyed 뒤 strands) |
| `casting.py:787` `cast_prompt(look, preset, gender)` | `{base_text} {He is|She is|They are} {look}, with dark brown eyes.`, ≤1500자 |
| `casting.py:934` `_apply_settings` | preset·gender·lookEn 정리. 400 `BadPreset`·`BadGender`·`BadLook` |
| `casting.py:1012` `update_settings`, `:1024` `start_draw` | PUT /casting, POST /casting/draw. 뽑기 문장은 시작 때 한 번 만들어 `cast.prompt`에 둔다 |
| `casting.py:1110` `view` | 상세 `casting` 보기 |
| `routes.py:242`·`:259` | payload → `update_settings`·`start_draw` |
| `vnew.js:1154` `castingSection` | 느낌·성별·외모 문장·저장·뽑기 |
| `generate.py:3256` | 영상 쪽은 `current_look`만 읽는다(이번에 바꾸지 않는다) |

길이 여유: 프리셋 문구 ≤527자 + 주어 + 외모 ≤300자 + 눈 색 ≈ 860자. 참고 문장(가장 긴 것 215자, 103 피드백 뒤)을 더해도 1,052자로
1500자 아래다.

## 2. 결정

| 번호 | 결정 | 이유 · 검토한 대안 |
|---|---|---|
| D1 | 참고 문장은 `cast_prompt`에서만 더한다. 외모 문장(300자)·`lookEn`·영상 쪽은 그대로 | 사용자 단서 "참고만". 300자 칸에 넣으면 그림 사실이 줄이기에서 밀려난다(대안: 외모 문장에 섞기 — 거절) |
| D2 | 분류 판정은 외모 문장(`prompt_look` 결과) 하나만 본다. 프리셋 문구는 보지 않는다 | 프리셋에 늘 "face"·"expression"이 있어 얼굴·표정 조각이 언제나 빠진다 |
| D3 | 형태 조각의 분류 = 단 분류 ∪ (조각 글이 스스로 담은 형태 분류). 인상 조각은 판정하지 않는다(늘 더한다, D13) | "a lean, tall build"(build)가 그림의 "short stature"와 부딪히지 않게. 103 피드백 전에는 체격·키만 보았다. 인상 조각은 형태 낱말이 없도록 쓰고(bl-a), 그래서 "a calm, unreadable face" 가 그림의 얼굴 때문에 빠지지 않는다 |
| D4 | 체격·키 낱말 바로 뒤(쉼표 전, 세 낱말 안)에 얼굴·머리·피부·옷 명사가 오면 그 낱말은 체격·키가 아니다 | "thin lips"·"a slender angular face"·"warm-toned skin"·"tall boots"·"thin black-rimmed round glasses"가 체격·키로 잡혀 조각이 잘못 빠지지 않게. "short"는 키 명사(man·stature·build …)가 뒤따르거나 "is short"일 때만 키("hair is short"·"very short crew cut"은 아님). face 의 "chin-length", eyes 의 "eye-catching"도 뺀다 |
| D5 | 문장 꼴 "He has A, B, and C."(조각이 하나면 "He has A.", 둘이면 "A and B") | 조각마다 관사를 넣어 두어 "has" 뒤에 바로 읽힌다. 지시서 예시의 "tall frame"·"firm square jawline"은 "a tall frame"·"a firm square jawline"으로 적었다 |
| D6 | 유형이 있고 성별이 unknown이면 male: 프리셋·주어 male, 외모 문장의 person→man·people→men·their→his·them→him·themselves→himself·theirs→his | steering "두 인물 모두 man". `align_gender(male)`은 중립 낱말을 바꾸지 않아 "He is a Korean person in their 30s"가 된다 |
| D7 | 1500자를 넘으면 조각을 끝에서부터 뺀다. 조각 순서는 표 순서(체격·키 → 얼굴·눈 → 표정·자세·몸가짐) | 몸 크기 정보가 사진 인상에 가장 크다. 실제로는 넘지 않는다(§1 길이) |
| D8 | 화면 미리보기 줄은 서버 `blHintsEn`만 보인다. 저장된 고른 값과 화면에서 바꾼 값이 다르면 숨긴다 | 분류 판정을 화면에 다시 쓰지 않는다(같은 규칙 두 벌 방지). 바꾼 값은 [저장]·뽑기 뒤 서버가 다시 계산한다 |
| D9 | 역할만 바꾸면 맞지 않는 저장 유형은 비운다(400 아님). 유형만 보내면 저장된 역할과 비교한다. 역할 `""` + 유형 → 400 | 화면은 늘 둘을 함께 보낸다. API만 쓰는 경우에 덜 놀라운 쪽 |
| D10 | 끔이면 `_apply_settings`가 BL 값을 보지 않는다(400 없음·저장 없음) | 끔 = 지금 동작(모르는 키 무시) |
| D11 | 400은 한 kind `BadBlType`. 문구는 고정 문자열 둘: 모르는 역할·유형 "알 수 없는 BL 역할·유형입니다.", 짝 불일치(지금 문구) | 103 피드백(D17): 운영 문구 표에 `BadBlType`을 더해 운영에서도 그대로 보인다. 103 처음에는 역할·유형 문구가 따로였다 |
| D13 | 분류를 형태 4(`build`·`height`·`faceShape`·`eyeShape`, 겹치면 뺀다)와 인상 5(`faceImpression`·`gaze`·`expression`·`posture`·`grooming`, 늘 더한다)로 나눈다 | 103 피드백(Claude 결정): 목적이 "정보를 많이 주기"다. 분석 외모 문장은 거의 늘 얼굴·눈을 말해 103 처음 규칙으로는 실제 분석 꼴 6개에서 떡대공 조각이 평균 1.83개만 남았다. 인상은 그림 사실과 부딪치지 않는다(`look_sentence`가 표정 조각을 지운다). 대안: 분류마다 겹침 강도를 두기(규칙이 커진다), 외모 문장을 LLM으로 다시 쓰기(기각) |
| D14 | 섞인 조각은 둘로 나눈다. 떡대공 "steady, deep-set eyes" → "deep-set eyes"(eyeShape) + "a steady gaze"(gaze). 미인수 "long lashes and clear eyes"는 eyeShape 하나(지시대로) | 형태만 빠지고 인상은 남게. 나눈 조각: 광공 "sharp features" + "striking good looks", 츤데레공 "well-defined features" + "a cool, reserved look", "sharp eyes" + "a slightly averted gaze", 무심공 "sharply defined features" + "a cool, detached air", "half-lidded eyes" + "an indifferent gaze", 꽃수 "large eyes" + "a clear gaze", 떡대수 "strong features" + "a good-natured, masculine look", 능력수 "sharp features" + "an intelligent, masculine look". 모양 낱말이 없는 얼굴·눈 조각은 이름표만 바꿨다(인상·눈빛) |
| D15 | 떡대공에 인상 조각 둘을 더했다: "a rugged, masculine look"(faceImpression), "a calm, confident expression"(expression) | 나누기만 하면 떡대공 인상 조각이 2개(눈빛·자세)라 분석 꼴 6개 평균이 2.83개로 목표(평균 4개 이상)에 못 미친다. 하나만 더하면 3.83개(bl 변형 N11). 둘을 더해 4.83개 |
| D16 | 형태 판정 낱말 표: 얼굴 모양 = 턱·턱선·광대·"…-faced"·얼굴형 낱말이 같은 명사구에서 face·features 앞, 눈 모양 = 눈꺼풀·속눈썹·"…-eyed"·눈 모양 낱말이 같은 명사구에서 eyes 앞. 같은 명사구 = 세 낱말 안, 관사·전치사·접속사·다른 명사를 넘지 않음 | "long hair, a calm face"의 long, "a slim build and narrow eyes"의 slim 이 모양으로 잡히지 않게. 코·입술·눈썹은 유형 조각이 말하지 않는 부위라 보지 않는다. "piercing eyes"·"calm, gentle eyes"는 눈빛이라 보지 않는다. "hooded"는 옷에도 쓰여 eyes 앞에서만, "chin-length"는 머리 길이 |
| D17 | 미인수 피부 조각 "clear, smooth skin" → "a clean, clear complexion"(faceImpression), `vpoc/errors.py` `USER_FACING_KINDS`에 `BadBlType` | 103 남은 이슈 4·1. 현실 배우풍 프리셋 "natural skin texture with pores and slight imperfections"와 부딪치지 않는 낱말. errors.py는 이번만 허용받았다 |
| D12 | 기존 검사: `check_vnew_casting`은 켬(기본) 그대로 돌리고 cast-j 보기 키 집합 기대값에 BL 5개를 더한다. `check_vnew_char_fidelity`는 부르는 줄에서 `VNEW_BL_LOOK` 끔으로 고정한다 | cast 전 그룹이 켬 + 유형 없음에서 지금 기대값 그대로 지나가는 것이 "유형 없음 = 지금과 같다"의 증거다. cf-r·cf-b의 상세 JSON sha256(`detailOld`)은 구현 전에 잰 값이라 끔 고정이 맞다(Claude 결정 9와 같은 방식) |
| D18 | (104) 근거는 `char.extraPromptKo` 하나다. `analysis.descriptionKo`·`appearanceEn`·외모 문장·저장된 `blRole`·`blType`은 보지 않는다 | 사용자 지시(2026-10-31) "설명에 떡대공이다 이런 식으로 넣는다면 그때 반영". `descriptionKo`는 LLM이 쓴 글이라 사용자가 적지 않은 유형이 들어갈 수 있다. 대안: 화면 선택 유지 + 설명 추가(두 근거가 부딪친다 — 거절), LLM으로 설명을 분류(비결정·비용 — 거절) |
| D19 | (104) 찾기: 이름 13개(label을 "/"로 나눔)를 붙은 꼴 그대로 부분 문자열로 찾는다. 앞 경계는 보지 않고 뒤에는 무엇이 붙어도 된다. 부정은 이름 끝 바로 뒤 0~5번째 글자(6자, 공백 포함)에서 "아님"·"아니"·"말고"가 **시작**되면이다. 나온 자리 순서, 중복 없이 최대 2개. 같은 유형이 한 자리에서 부정돼도 다른 자리에서 부정 없이 나오면 찾는다 | 지시 그대로. "6자 안에 오면"을 "시작하면"으로 읽어 "떡대공은 절대 아님"(아님이 5번째에서 시작)도 부정이다. 앞 경계를 요구하면 "형광공장"·"광공업"의 광공은 막지만 "집착광공"(흔한 말)을 놓친다 — 요구하지 않는다(남은 이슈에 오탐 예). NFC 정규화·띄어 쓴 꼴("떡대 공")은 보지 않는다 |
| D20 | (104) 합치기 `merged_pieces(types)`: 앞 유형의 조각을 표 순서대로 모두 넣고, 뒤 유형 조각은 그 분류가 앞 유형에 이미 있으면 뺀다. 형태 조각의 분류는 D3처럼 글이 담은 형태 분류까지 본다("a lean, tall build"는 체격·키). 판정은 표 기준(그림으로 빼기 전)이다. 그 뒤 그림 우선(`hint_pieces`)을 한 번 적용한다 | "같은 분류는 앞의 유형이 이긴다". 앞 유형 조각이 그림 때문에 빠져도 뒤 유형이 그 자리를 채우지 않는다 — 사용자가 앞에 쓴 유형의 뜻을 지킨다(무심공 + 떡대공에서 그림이 "slim"이면 떡대공 체격이 들어오지 않는다). 순서는 앞 유형 → 뒤 유형이라 1500자 줄이기(D7)는 뒤 유형 조각부터 뺀다. 유형이 하나면 103과 같다 |
| D21 | (104) `cast_prompt`·`_cast_text`·`hint_pieces`·`look_hints`의 유형 인자는 id 하나(글) 또는 id 목록이다(`known_types`가 아는 id만 순서대로 중복 없이 고른다). 넷째 인자 기본값은 그대로 `""` | 103 호출(글 하나)과 검사(bl-c·bl-d)가 그대로 돈다. 인자 이름은 `bl_types`로 바꿨다(키워드로 부르는 곳 없음) |
| D22 | (104) 저장된 `blRole`·`blType`은 읽지도 지우지도 않는다. `_empty_casting`의 기본값 `""`도 그대로 둔다. 103 함수 `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`, 화면 함수 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields`, CSS `.vnew-casting-row`·`-bl-role`·`-bl-type`·`-bl-hint`는 지우지 않는다(부르는 곳 없음) | quality-week 9 "기존 함수 정의를 지우지 않는다"(103은 커밋 89d5a34로 배포됐다). 저장 모양을 바꾸지 않아 103 파드와 섞여도 안전하다. 지울지는 따로 승인받는다(남은 이슈) |
| D23 | (104) 라우트는 `blRole`·`blType`을 넘기지 않고, `update_settings`·`start_draw`·`_apply_settings`에서 BL 키워드를 뺐다. 옛 화면이 보내도 모르는 키라 무시된다(400 없음) | 지시 "더 받지 않는다. 와도 무시". 함수에 키워드를 남겨 두고 버리는 대안은 읽는 사람에게 받는 것처럼 보여 뺐다 |
| D24 | (104) 보기 켬: 끝에 `blDetected`(`[{id, label}]`, label은 표 label 그대로 — 광공은 "광공 / 집착공")와 `blHintsEn` 두 키. 끔이면 BL 키 없음(103 끔과 같다) | 화면이 판정을 다시 하지 않게(D8) 서버가 찾은 결과와 참고 문장을 준다. 이름만 주는 대안은 화면이 id→이름 표를 따로 가져야 한다 |
| D25 | (104) 화면 한 줄: `blDetected`가 배열이면 성별 칸 바로 뒤(103 BL 줄 자리). 찾음 `p.vnew-casting-bl-found`(13px), 못 찾음 `p.vnew-hint.vnew-casting-bl-guide`(12px). 읽기 전용이라 잠금이 없다. 서버 응답이 103 꼴이어도 선택을 그리지 않는다 | 지시 문구 그대로("—"는 지시 예시의 글자). 롤링 배포 중 옛 파드 응답이 와도 선택이 다시 생기지 않게 화면은 `blDetected`만 본다 |
| D26 | (104) 로그 `bl=` 끝말은 찾은 id를 `+`로 이은 값, 없으면 `-` | 지시 그대로. 이 줄을 읽는 코드·문서는 없다(103 저장소 검색) |
| D27 | (104) 검사 기대값: cast-j 보기 키 집합 = 지금 19개 + `blDetected`·`blHintsEn`. `_run_bl_look_off(check_vnew_char_fidelity)`는 그대로(켬이면 보기에 2개 키가 붙어 cf-b·cf-r 지문이 달라진다). bl-f·bl-g·bl-h·bl-i·bl-j를 다시 쓰고 bl-k(찾기)·bl-l(합치기)을 더한다 | 103 기대값 중 화면 선택·400은 지시로 없어진 동작이다 |
| D28 | (104 피드백) 앞 경계: 이름 바로 앞 한 글자가 한글 음절(U+AC00~U+D7A3)이면 찾지 않는다. 그 밖의 글자(공백·문장부호·숫자·영문, 그리고 자모 "ㅋ"·한자·이모지)는 경계로 본다. 앞 글이 `_PREFIX_OK`("집착")로 끝나면 경계로 친다(그 앞 글자는 보지 않는다 — "초집착광공"도 찾는다) | Claude 결정 "이름 바로 앞 글자가 한글 음절이면 찾지 않는다". 허용 목록(공백·문장부호·숫자·영문만)으로 읽는 대안은 "ㅋㅋ떡대공이다"를 놓친다. 규칙 문장이 음절만 말하므로 음절만 막는다. 접두어 앞까지 다시 보는 대안은 규칙이 커지고 "초집착광공"을 놓친다 |
| D29 | (104 피드백) 뒤쪽 제외: 모든 이름은 바로 뒤 첫 글자가 "원"·"업"이면 뺀다(창 없음 — "떡대공인데 원래"는 찾는다). 꽃수만 이름 끝 바로 뒤 0~5번째 글자에서 "놓"·"자수"·"무늬"·"장식"·"프린트"·"패턴"·"그림"이 시작되면 뺀다(부정과 같은 창, D19). 뺀 자리는 부정처럼 건너뛰고, 같은 이름이 다른 자리에서 다시 나오면 찾는다 | Claude 결정. "원"·"업"을 창 전체로 보면 "떡대공인데 원래"·"떡대공이고 업무"를 놓친다. 꽃수 낱말을 모든 이름에 쓰면 "떡대공이고 줄무늬 셔츠"를 놓친다 |
| D30 | (104 피드백) 뒤에 한글이 더 붙어도 찾는다("무심수아"·"무심수인"·"무심수다"). 이름과 조사를 구별하지 않는다 | Claude 결정 "찾는 쪽으로 둔다". 조사 표로 구별하는 대안은 서술어 활용("무심수였다", "무심수라서")까지 표가 커진다 |
| D31 | (104 피드백, Claude 승인) 부르는 곳이 없는 103 정의를 지운다: 서버 `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`, 화면 `castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields`, CSS `.vnew-casting-row`·`.vnew-casting-bl-role`·`.vnew-casting-bl-type`·`.vnew-casting-bl-hint`. 이 정의만 쓰던 상수 `_MSG_BAD_BL`·`_MSG_BL_MISMATCH`·`CASTING_BL_NO_TYPE`도 지운다. `vpoc/errors.py`의 `BadBlType`(지시서 104 "표에서 지우지 않는다"), `genre_bl`의 `roles`·`fits`·`list_types`(승인 목록 밖, bl-a가 본다), 저장값·`_empty_casting` 기본값은 남긴다 | 상수를 남기면 쓰는 곳 없는 문구가 남는다. 상수는 함수 정의가 아니지만 그 함수만 쓰던 값이라 함께 지운다(결과 보고에 적는다) |
| D32 | (104 피드백) 세 파일의 `VNEW_BL_LOOK` 주석만 고친다. 줄 수·자리·값·base 되돌리기 문구는 그대로다. bl-g가 세 주석이 "캐릭터 설명"을 말하고 "고르" 낱말이 없는지 본다 | 배선 검사(bl-g·rw-a~c·ob-h)는 자리·값만 보므로 기대값이 바뀌지 않는다 |

## 3. 모듈 `genre_bl.py`

```python
ROLE_NONE, ROLE_TOP, ROLE_BOTTOM, ROLE_REVERSIBLE = "", "top", "bottom", "reversible"
ROLES = (ROLE_NONE, ROLE_TOP, ROLE_BOTTOM, ROLE_REVERSIBLE)
FORM_CATEGORIES = ("build", "height", "faceShape", "eyeShape")             # 겹치면 뺀다
IMPRESSION_CATEGORIES = ("faceImpression", "gaze", "expression", "posture", "grooming")  # 늘 더한다
CATEGORIES = FORM_CATEGORIES + IMPRESSION_CATEGORIES
TYPES: dict[str, dict]   # id -> {"role", "label", "desc", "lookEn": ((분류, 영어), ...)}
def roles() -> list[dict]                 # [{key, label}] 4개
def is_type(bl_type) -> bool
def fits(bl_type, role) -> bool           # "" 는 늘 True, 리버서블은 모든 유형
def list_types() -> list[dict]            # [{id, role, label, desc, lookEn(이은 글)}]
def look_categories(look_en) -> set[str]  # 외모 문장에 이미 있는 형태 분류(FORM_CATEGORIES 중)
def hint_pieces(bl_type, look_en) -> list[str]  # 인상 조각은 늘, 형태 조각은 겹치지 않을 때만
def look_hints(bl_type, look_en) -> str   # ", ".join(hint_pieces)
```

형태 분류 낱말 표(`_CATEGORY_RE`, 대소문자 무시, 낱말 경계, 103 피드백 뒤):

| 분류 | 낱말 |
|---|---|
| build | slim slender lean broad muscular stocky petite burly athletic chubby thin sturdy skinny plump wiry lanky brawny bulky heavyset toned build built physique figure well-built broad-shouldered, "(broad·narrow·wide·sloping·square·strong·small·big·large·rounded) shoulders" |
| height | tall taller tallest towering petite lanky height stature statuesque, "short(er) (…두 낱말) man·woman·person·guy·stature·build·frame·figure·body·height", "is·are·looks·quite·rather·very·fairly·slightly short" |
| faceShape | jaw(s) jawline(s) jawed chin(s) cheekbone(s)("chin-length" 아님), "(모양)-faced·-featured", "face·facial·bone shape·structure", 같은 명사구에서 face·faces·features·visage 앞의 angular oval round rounded roundish square squarish squared long narrow slender thin slim wide broad small heart-shaped v-line v-shaped diamond-shaped oblong chiseled chiselled sculpted defined well-defined sharp sharply strong bony gaunt chubby plump |
| eyeShape | eyelid(s) eyelashes lashes lids lidded monolid(s) monolidded, "(wide·doe·almond·round·big·large·narrow·cat·fox·hooded·slant·deep·sharp·sleepy)-eyed", 같은 명사구에서 eye·eyes 앞의 narrow almond almond-shaped round rounded large big small wide wide-set close-set deep-set hooded monolid monolidded single-lidded double-lidded upturned downturned droopy drooping slanted slanting sharp elongated doe cat-like catlike fox-like foxlike crescent phoenix sleepy half-lidded heavy-lidded |

인상 분류(faceImpression·gaze·expression·posture·grooming)는 판정하지 않는다. 103 처음의 face·eyes·expression·posture·grooming 낱말 표는
지웠다(face 의 skin·complexion·handsome, eyes 의 gaze·stare 등은 이제 아무것도 빼지 않는다).

D4 예외 명사(체격·키 낱말 뒤): 얼굴(face nose lips mouth eyes brows jaw chin cheeks forehead ears teeth smile …), 피부(skin complexion),
머리(hair bangs fringe ponytail braids beard …), 손(fingers hands nails), 옷·소품(glasses rims shirt jacket coat shoes boots collar sleeves
tie dress suit watch …). "frame" 은 넣지 않는다("a tall frame"·"a slim frame" 은 몸이다). 103 피드백에서 이 창도 관사·전치사·접속사
(`_JOIN_STOP`)를 넘지 않게 고쳤다. 전에는 "a slim build and narrow eyes"의 slim·build 가 뒤의 eyes 때문에 체격이 아니라고 읽혔다.

형태 조각은 자기 형태 분류로만 읽혀야 하고(체격·키 묶음 "a lean, tall build"만 둘), 인상 조각은 형태 분류로 읽히면 안 된다(bl-a).

유형 표(관사 포함 영어, 분류)는 `genre_bl.py` `TYPES`가 정본이다(WORKLOG 103에 전체 표).

## 4. `casting.py`

| 함수 | 바뀜 |
|---|---|
| `_empty_casting` | `blRole: ""`, `blType: ""` |
| 새 `_bl_state(cast)` | 저장값 정리(글·아는 값·짝) → `(role, type)` |
| 새 `_bl_type_now(cast)` | 끔이면 `""`, 켬이면 `_bl_state(cast)[1]` |
| 새 `_bl_checked(role, type, where)` | 형식 확인(글·아는 값, 둘 다 오면 짝). 400 `BadBlType` |
| 새 `_apply_bl(cast, role, type, where)` | 끔이면 아무것도 안 함. 역할 → (`""`면 유형 비움, 맞지 않는 저장 유형 비움) → 유형(저장 역할과 짝) |
| `_apply_settings(cast, preset, gender, look_en, where, bl_role=None, bl_type=None)` | 끝에서 `_apply_bl` |
| 새 `_NEUTRAL_TO_MALE`(+ 기존 `_sub_words`) | person·people·their·them·themselves·theirs → male 낱말(D6). `_cast_text` 가 유형 + unknown 일 때만 쓴다 |
| 새 `_hint_sentence(pieces, gender)` | "He has A, B, and C." |
| 새 `_cast_text(look, preset, gender, bl_type)` | `(문장, 참고 문장)`. 유형 없음이면 지금 `cast_prompt` 본문과 같은 식 |
| `cast_prompt(look, preset, gender, bl_type="")` | `_cast_text(...)[0]` |
| `update_settings`·`start_draw` | `bl_role=None, bl_type=None` 키워드. 뽑기 전 `_bl_checked`로 형식만 먼저 본다(끔이면 건너뜀). 문장은 `_bl_type_now(cast)` |
| `view` | 켬이면 BL 5개 키를 끝에 더한다. `blHintsEn = _cast_text(current_look, preset, resolve_gender, type)[1]` |
| 시작 로그 `vnew.casting.draw_start` | 끝에 ` bl=<유형 또는 ->` |

## 5. `routes.py`

PUT·draw 두 곳에서 `bl_role=payload.get("blRole"), bl_type=payload.get("blType")`만 더한다.

## 6. 설정·배선

- `vnew/settings.py`: `VNEW_BL_LOOK = _bool("VNEW_BL_LOOK", True)`(VNEW_CASTING 묶음 뒤, 주석 2줄).
- `deploy/config.env.example`: `VNEW_OUTFIT_STALL_S=1200` 바로 뒤에 주석 + `VNEW_BL_LOOK=true`.
- `k8s/workloads.yaml.tmpl`: 앱 env `VNEW_OUTFIT_STALL_S` 쌍 바로 뒤에 주석 + `- name: VNEW_BL_LOOK` / `value: "${VNEW_BL_LOOK}"`.
  (VNEW_CASTING 바로 뒤에 두면 lr-o·cf-a·cast-m이 보는 자리 순서가 바뀐다. ob-h는 VNEW_3D_SIDE_SWAP 바로 뒤 = VNEW_OUTFIT_STALL_S를 본다.)
- `tools/check_vpoc.py` `PARITY_ENV_KEYS` 끝에 `VNEW_BL_LOOK`(rw-a~c가 템플릿·base·렌더를 본다).

## 7. 화면 `vnew.js`

캐스팅 구역 안(cast-l이 자르는 `/* ---- 실사 캐스팅` ~ `/* ---- 추가 복장 섹션` 사이)에 순수 함수와 DOM 함수를 둔다.

| 함수 | 동작 |
|---|---|
| `castingBlOn(cast)` | `blRoles`·`blTypes`가 배열이면 true |
| `castingBlTypeOptions(cast, role)` | `[{value:"", label:"유형 없음", title:""}]` + 역할에 맞는 유형(리버서블 전부, 없음은 유형 없음만), title = desc |
| `castingBlTypeFits(cast, role, type)` | 그 역할 목록에 있는가("" 는 true) |
| `castingBlHintText(cast)` | `"참고로 더해지는 외모 설명(영어): " + blHintsEn`, 비면 "" |
| `castingBlFields(cast, form, lock, genderWrap)` | `div.vnew-casting-row`(성별 + `label.vnew-casting-bl-role` + `label.vnew-casting-bl-type`)와 `p.vnew-hint.vnew-casting-bl-hint`를 돌려준다. 역할을 바꾸면 유형 목록을 다시 채우고 맞지 않는 유형을 비운다. 미리보기 줄은 저장값과 같을 때만 보인다(D8) |
| `castingSection` | 켬이면 `castingBlFields`의 두 노드를, 끔이면 지금처럼 성별 칸을 붙인다. 저장·뽑기 본문에 켬일 때만 `blRole`·`blType` |

끔(필드 없음) 화면 DOM·요청 본문은 지금과 같다(cast-l 기대값 그대로).

CSS: `.vnew-casting-row`(flex·wrap), `.vnew-casting-bl-role`·`.vnew-casting-bl-type`(max-width 240px), `.vnew-casting-bl-hint`.

## 8. 검사(`check_vnew_bl_look`, 접두어 `bl-`)

| 검사 | 덮는 것 |
|---|---|
| bl-a | 유형 표: 12종·옛 10종 id·label·desc = 옛 TAGS·새 2종, 역할 4개, 분류 9종(형태 4·인상 5)만, 형태 조각은 자기 형태 분류로만·인상 조각은 형태 분류 없음, 나눈 조각·미인수 complexion, 조각 350자 이하, 금지 낱말(은어·성적·폭력·미성년·머리·옷·나이·색) 0, 수 유형 masculine/man 낱말, `list_types`·`roles` 모양, 모듈에 저장·외부 호출 없음, 모르는 유형 `""` |
| bl-b | `look_categories`(형태만)·`hint_pieces`: 형태 분류마다 빠짐(그림 우선), 인상 낱말은 아무것도 빼지 않음, 같은 명사구 창, 실제 분석 꼴 6개 떡대공·무심공·미인수 조각과 떡대공 평균 ≥ 4, "slim" → 떡대공 build 빠짐, D3(short stature → 무심공 "lean, tall build" 빠짐), D4 오탐 막기(thin lips·slender face·short black hair·tall boots·frown lines·thin-framed glasses), 실제 분석 꼴 외모 문장 |
| bl-c | 유형 없음 = 지금: 모든 프리셋·성별·외모(cast-a 입력 + 실제 분석 꼴)에서 `cast_prompt(…)` = `cast_prompt(…, "")` = `cast_prompt(…, "모르는 값")` = 옛 식, `_CAST_IDOL_MALE` 원문 |
| bl-d | 유형 있음: 떡대공·무심공·미인수 × 프리셋 × 성별 — 참고 문장 붙음·은어 0·"man"·≤1500·전문 고정값 1개, unknown → male(프리셋·He·person→man), female 지정 → female 유지 |
| bl-e | 1500자 상한: `PROMPT_MAX`를 줄여 조각이 끝에서 빠짐, 다 빠지면 참고 문장 없음 |
| bl-f | 설정: `_apply_settings`·`update_settings`·`start_draw`·Flask 라우트 PUT·draw — 모르는 역할·유형·글 아님 400, 짝 불일치 400, 리버서블 허용, 역할 비우면 유형 빔, 역할만 바꿈, 유형만 보냄, 뽑기 문장에 참고 문장·로그 `bl=` |
| bl-g | 끔: 보기 키 = 지금, 저장값이 있어도 문장 그대로, BL 요청 무시(400 없음·저장 없음), 설정 읽기·배선·렌더 |
| bl-h | 보기: 5개 키·깨진 저장값·`blHintsEn` = 뽑기 문장 끝, 사용자가 고친 외모 문장 그대로, `auto_look`·`current_look`·`generate` 무관 |
| bl-i | 화면(node 가짜 DOM, 실제 `view` 응답): 역할 바꾸면 유형 목록·title, 유형 칸 숨김, 미리보기 줄 보임·숨김·바꾸면 숨김, 저장·뽑기 payload에 blRole·blType, 끔이면 칸 없음, 잠금, innerHTML 0, CSS, `node --check` |
| bl-j | 운영 문구(103 피드백): `USER_FACING_KINDS`에 `BadBlType` 한 줄, 던지는 곳 4곳이 모두 casting.py 고정 문구 상수 둘, `RAW_ERRORS=false` 봉투·실제 라우트가 고정 문구 그대로 |

## 9. 지시서 104 — 캐릭터 설명에서 찾기(정본)

### 9.1 `genre_bl.py`(더함)

```python
NEGATIONS = ("아님", "아니", "말고")
NEGATION_WINDOW = 6          # 이름 끝 바로 뒤 0~5번째 글자에서 부정 낱말이 시작되면 그 자리는 찾지 않음(D19)
DETECT_MAX = 2
_NAME_TO_ID: dict[str, str]  # label 을 "/" 로 나눈 이름 13개 → id
def detect_types(text_ko) -> list[str]              # 나온 순서·중복 없이·최대 2개. 글이 아니면 []
def known_types(bl_types) -> list[str]              # 글 하나 또는 목록 → 아는 id 만(순서·중복 없이)
def merged_pieces(bl_types) -> list[tuple[str, str]]  # 같은 분류는 앞 유형이 이김(D20). 유형 하나면 표 그대로
def hint_pieces(bl_types, look_en) -> list[str]     # merged_pieces 에 그림 우선(103 규칙)
def look_hints(bl_types, look_en) -> str
```

### 9.2 `casting.py`

| 함수 | 바뀜 |
|---|---|
| 새 `_bl_types_now(char)` | 끔이면 `[]`, 켬이면 `genre_bl.detect_types(char.extraPromptKo)`(글이 아니면 `[]`) |
| `cast_prompt(look, preset, gender, bl_types="")`·`_cast_text` | 유형 인자가 글 하나 또는 목록(`known_types`). 유형이 있고 unknown 이면 male(D6 그대로) |
| `_apply_settings(cast, preset, gender, look_en, where)` | BL 키워드·`_apply_bl` 호출을 뺐다 |
| `update_settings`·`start_draw` | BL 키워드를 뺐다. 뽑기 전 BL 형식 확인(`_bl_checked`)을 뺐다. 회차 문장은 `_bl_types_now(data)`, 로그 `bl=` 은 `"+".join(...) or "-"` |
| `view` | 켬이면 끝에 `blDetected`·`blHintsEn` |
| `_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`·`_MSG_BAD_BL`·`_MSG_BL_MISMATCH`·`_empty_casting` | 그대로(D22, 부르는 곳 없음) |

### 9.3 `routes.py`

PUT `/casting`·POST `/casting/draw`에서 `bl_role=…, bl_type=…` 줄을 빼고 주석 한 줄(옛 화면이 보내도 무시).

### 9.4 화면 `vnew.js`·`vnew.css`

| 함수 | 동작 |
|---|---|
| 새 `castingBlLine(cast)` | `blDetected`가 배열이 아니면 `null`. 이름(label, 없으면 id)이 하나도 없으면 `{found:false, text:안내}`. 있으면 `{found:true, text:"캐릭터 설명에서 찾은 BL 유형: " + 이름들(", ") + (" — " + castingBlHintText)}` |
| `castingSection` | `blOn`·`form.blRole`·`form.blType`·`castingBlFields` 호출과 저장·뽑기 본문의 BL 두 곳을 뺐다. 성별 칸 뒤에 `castingBlLine` 한 줄 |
| `castingBlHintText` | 그대로(새 줄이 부른다) |

CSS: 새 `.vnew-casting-bl-found`(13px, 글자색), `.vnew-casting-bl-guide`(12px). 103 선택자는 그대로(주석만 "104부터 쓰지 않음").

### 9.5 검사(`check_vnew_bl_look`, 104)

| 검사 | 덮는 것 |
|---|---|
| bl-c | 유형 값에 목록 4종(`[]`·`["zz"]`·`("",)`·`[None]`)을 더해 유형 없음 = 옛 식 |
| bl-e | 두 유형 합친 문장도 끝에서부터 뺀다(뒤 유형 조각이 먼저 빠짐) |
| bl-f | 함수 인자에 BL 없음·라우트 소스에 blRole·blType 없음, 103 저장값이 있는 캐릭터에 PUT 6건(BL 값 여러 꼴) → 200·저장값 그대로, draw(BL 값 + 틀린 금액 → 409 CostMismatch, BL 값 + 맞는 금액 → 202·회차 문장 = 옛 식·저장값 그대로·로그 `bl=-`), 설명에 떡대공 → 회차 문장 끝 참고 문장·보기 `blDetected`·로그 `bl=ttokdae_gong`, 두 유형 → `bl=musim_gong+ttokdae_gong`, 정적 검사(저장값을 읽는 함수는 `_empty_casting`과 103 함수뿐) |
| bl-g | 끔: 보기 키 19개, 설명에 유형·저장값이 있어도 문장 그대로·로그 `bl=-`, BL 요청 무시, 다시 켜면 설명에서 찾음. 설정·배선·렌더는 103 그대로 |
| bl-h | 켬 보기 = 끔 보기 + `blDetected`·`blHintsEn`(끝), 103 키 없음, 근거는 extraPromptKo 뿐(descriptionKo·appearanceEn·lookEn·저장 blType 에 이름이 있어도 `[]`), 깨진 extraPromptKo, 두 유형 보기, 보기 성별 = 뽑기 성별, 고친 외모 문장 그림 우선, `generate.py`가 BL을 읽지 않음 |
| bl-i | node 가짜 DOM: `castingBlLine` 순수 결과, 실제 `view` 응답(찾음·두 유형·못 찾음(103 저장값 있음)·뽑는 중·만드는 중·끔)과 103 꼴 응답에서 선택 0·한 줄 글·class, 저장·뽑기·되돌리기 본문 키(BL 없음), 끔 화면 = 103 끔, 위험한 글, innerHTML 0, CSS(가이드 12px), `node --check`. 다시 만들기: 진짜 `buildSection`이 보낸 본문 = 파이썬이 실제 라우트 POST `/build`(스레드 대역)로 보낸 본문, 진짜 `refreshDetail`이 실제 GET 상세로 다시 그리면 줄이 안내 → 떡대공 → (떡대공 아님) 안내 |
| bl-j | 표에 `BadBlType` 한 줄 그대로, 던지는 곳은 103 함수 4곳(고정 문구 상수 둘), `RAW_ERRORS=false` 봉투 문구, 실제 라우트에 모르는 BL 값 → 200(이제 400 없음) |
| bl-k | `detect_types`: 이름 표 13개, 지시 예 6개(떡대공이다·무심공이고 떡대공·떡대공 아님·떡대 공원·3개·빈 글/None), 이름마다 "…이다", 부정 3낱말·창 경계(5번째 시작 = 부정, 6번째 = 찾음), 다시 나온 자리, 역할 낱말만, 집착광공, 중복, 글 아닌 값 |
| bl-l | `merged_pieces`·`hint_pieces`·`look_hints` 목록: 무심공 + 떡대공·떡대공 + 무심공·미인수 + 다정공 전문, 유형 하나 = 표, 모르는 id 건너뜀, 그림 우선 뒤 앞 유형 자리를 뒤 유형이 채우지 않음, 두 유형 `cast_prompt` 전문 |

## 10. 104 피드백 — 앞 경계·뒤쪽 제외·103 정의 정리(정본)

### 10.1 `genre_bl.py`

```python
_HANGUL_SYLLABLE = re.compile(r"[\uac00-\ud7a3]")
_PREFIX_OK = ("집착",)                    # 이름 바로 앞이 이 낱말로 끝나면 앞 경계로 친다(D28)
_NEXT_BLOCK = ("원", "업")                # 모든 이름: 바로 뒤 첫 글자(공원·수원·공업·수업, D29)
_AFTER_BLOCK = {"kkotsu": ("놓", "자수", "무늬", "장식", "프린트", "패턴", "그림")}  # 이름 끝 뒤 창 안에서 시작(D29)
def _starts_within(text, end, words) -> bool  # 이름 끝 바로 뒤 0~NEGATION_WINDOW-1 번째 글자에서 words 중 하나가 시작하는가
def _negated(text, end) -> bool               # _starts_within(text, end, NEGATIONS)
def _front_ok(text, start) -> bool            # 글 처음·한글 음절 아님·_PREFIX_OK 로 끝남
def _excluded_after(text, end, type_id) -> bool  # _NEXT_BLOCK 첫 글자 · 부정 · _AFTER_BLOCK[type_id]
```

`detect_types`는 자리마다 `_front_ok`와 `_excluded_after`를 보고 하나라도 걸리면 건너뛴다. `_NEGATION_SPAN`은 `_starts_within`이
낱말 길이로 창 끝을 정하므로 지웠다(104 새 상수, 커밋 전).

### 10.2 지우는 정의(D31)

| 파일 | 지움 | 남김 |
|---|---|---|
| `casting.py` | `_MSG_BAD_BL`·`_MSG_BL_MISMATCH`·`_bl_checked`·`_apply_bl`·`_bl_state`·`_bl_type_now`, 그 구역 주석 | `_bl_types_now`(구역 머리를 104 기준으로), `_empty_casting` 기본값 |
| `vnew.js` | `CASTING_BL_NO_TYPE`·`castingBlOn`·`castingBlTypeOptions`·`castingBlTypeFits`·`castingBlFields` | `CASTING_BL_HINT_LEAD`·`castingBlHintText`·`castingBlLine` |
| `vnew.css` | `.vnew-casting-row`·`.vnew-casting-bl-role`·`.vnew-casting-bl-type`·`.vnew-casting-bl-hint` | `.vnew-casting-bl-found`·`.vnew-casting-bl-guide` |

### 10.3 검사

| 검사 | 바뀜 |
|---|---|
| bl-f | 103 함수 동작 12경우를 뺐다(지운 정의를 직접 불렀다). AST: 103 함수·상수가 casting.py 에 없고, "blRole"·"blType" 글자를 쓰는 함수는 `_empty_casting` 뿐 |
| bl-g | 세 파일 `VNEW_BL_LOOK` 주석이 "캐릭터 설명"을 말하고 "고르" 낱말이 없음, 주석 줄 수 그대로(base 3줄·settings 2줄·템플릿 1줄) |
| bl-i | 남긴 103 화면 함수 실행을 뺐다. vnew.js 에 지운 함수·상수 이름이 없고 vnew.css 에 지운 선택자가 없음 |
| bl-j | `BadBlType`은 표에 한 줄 그대로, `app/src` 에 던지는 곳 0(AST), `RAW_ERRORS=false` 봉투가 고정 문구를 그대로 보임, 라우트는 BL 값에 200 |
| bl-k | 앞 경계·뒤쪽 제외 예(지시 9개 + 형광공장·ㅋㅋ·영문·숫자 뒤·떡대수원·"떡대공인데 원래"·"떡대공이고 줄무늬"·조사 꼴), 꽃수 제외 낱말 7개 × 꼴, 꽃수 창 경계 8경우, 모든 이름 × "원"·"업"·한글 앞 글자·"집착" 앞 |
