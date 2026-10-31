# 설계 — bl-look

> 요구사항 `requirements.md`(R1~R8). 경로는 `AI-POC-whynot/` 아래다. 2026-10-31 103 피드백(Claude 결정)으로 D3·D11 을 바꾸고
> D13~D17 을 더했다(분류를 형태·인상으로 나눔, 미인수 피부 조각, 운영 문구 `BadBlType`).

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
