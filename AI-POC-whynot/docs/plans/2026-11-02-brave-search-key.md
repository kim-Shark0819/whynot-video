# 운영 계획: Brave Search API 키 등록과 웹 검색 용어 풀이 켜기 (2026-11-02, 지시서 113)

> 상태: **계획만(실행 전).** 이번 주 원칙 7번에 따라 Kiro는 클라우드·클러스터 명령을 실행하지 않는다. 실행은 사람(운영자·Claude)이 한다.
> 관련: 스펙 `.kiro/specs/cast-term-search/`, `docs/ANALYSIS.md` §3.6.1 B 「인터넷 외모 용어 웹 검색 풀이」·§6.4·§6.6, `docs/WORKLOG.md` 113.
> 명령 예시에는 키 값 대신 `<KEY>`를 쓴다. 키 값은 이 저장소·config 파일·채팅·티켓·로그에 남기지 않는다.

## 1. 무엇이 바뀌나

- 코드는 기본 꺼짐이다(`VNEW_TERM_SEARCH=false`, base `config.env.example`). 키 Secret이 없으면 켜도 검색하지 않는다(`term_search.active`).
- 켜면 실사 캐스팅 뽑기 스레드가 첫 제출 전에 캐릭터 설명의 모르는 외모 인터넷 용어(최대 3개)를 Bedrock LLM으로 뽑고, 용어마다 Brave Web Search
  1회(질의 `"<용어>" 뜻` 하나)와 해석 LLM 1회로 영어 외모 조각을 만들어 캐스팅 문장에 붙인다. 화면 캐스팅 칸에 "설명의 '<용어>'를 '<조각>'으로
  해석했어요"가 작게 보인다.
- 앱 파드가 새로 나가는 곳: `https://api.search.brave.com/res/v1/web/search`(HTTPS GET). 클러스터에 NetworkPolicy가 없어 fal.ai와 같은 경로로 나간다
  (템플릿 기준 — 실제 egress 제한은 미확인).

## 2. 키 발급(사람)

1. Brave Search API 대시보드(`https://api-dashboard.search.brave.com/`)에서 계정을 만든다.
2. 요금제를 고른다. **요금·초당 한도·월 한도는 확인 필요**(이 문서는 요금표를 조사하지 않았다). 앱은 검색 시작 사이를 1.1초 두므로 초당 1건 요금제에서도
   동작한다. 예상 사용량: 캐릭터 설명이 바뀐 뒤 첫 뽑기에서만 용어당 1회(최대 3회), 같은 사용자의 같은 용어는 30일 동안 다시 검색하지 않는다.
3. 웹 검색(Web Search)용 API 키를 발급한다. 키는 비밀번호 관리자 등 안전한 곳에만 둔다.

## 3. Secret 등록(개발 먼저)

Secret 이름은 base의 `BRAVE_SECRET_NAME`(기본 `brave-search-api-key`), Secret 안의 키 이름은 `brave-search-api-key`, 네임스페이스는 `K8S_NAMESPACE`
(base `whynot`)다. 템플릿이 `optional: true`로 참조하므로 Secret이 생기기 전에도 파드는 뜬다.

```bash
# 대상 클러스터 컨텍스트인지 먼저 본다(개발 → 운영 순서).
kubectl config current-context

# Secret 만들기(키 값은 <KEY> 자리에만). 셸 기록에 남지 않게 하려면 아래 파일 방식을 쓴다.
kubectl -n whynot create secret generic brave-search-api-key --from-literal=brave-search-api-key='<KEY>'

# (권장) 파일 방식: 권한 600 임시 파일에 키 한 줄을 쓰고 등록 뒤 지운다.
#   printf '%s' '<KEY>' > /tmp/brave.key && chmod 600 /tmp/brave.key
kubectl -n whynot create secret generic brave-search-api-key --from-file=brave-search-api-key=/tmp/brave.key
#   rm -f /tmp/brave.key

# 값이 아니라 키 이름만 확인한다.
kubectl -n whynot get secret brave-search-api-key -o jsonpath='{.data}' | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'

# env 는 파드가 뜰 때 읽으므로 앱을 다시 띄운다(Deployment 이름 = APP_SERVICE_ACCOUNT, base whynot-app).
kubectl -n whynot rollout restart deployment/whynot-app
kubectl -n whynot rollout status deployment/whynot-app
```

- ArgoCD는 이 Secret을 관리하지 않는다(ai-deploy 매니페스트에 없다). 수동으로 만든 Secret은 동기화·prune 대상이 아니다(fal-api-key와 같다 — 추정, 운영자 확인).
- 키를 바꿀 때: `kubectl -n whynot create secret generic brave-search-api-key --from-literal=brave-search-api-key='<KEY>' --dry-run=client -o yaml | kubectl apply -f -`
  → `rollout restart`.

## 4. 기능 켜기(코드 변경 — 커밋·배포 필요)

1. `deploy/config.dev.env`에 한 줄을 더한다(날짜·이유 주석 한 줄과 함께): `VNEW_TERM_SEARCH=true`. base에 키가 있어 오버레이 규칙(`render.py`)을 지난다.
2. MR → main 병합 → CI → 개발 ArgoCD 동기화.
3. 개발 확인(아래 5절) 뒤 운영은 `deploy/config.prod.env`에 같은 줄을 더하고 수동 job `deploy:whynot:prod`.

## 5. 개발 확인 순서

1. 캐릭터 설명(추가 요청)에 "꽃미남에 두부상" 같은 용어를 쓴 캐릭터로 실사 캐스팅 뽑기 1회(현실 배우풍 Seedream 2장 $0.08).
2. 앱 로그: `vnew.cast.term_extract found=2`, `vnew.cast.term term=꽃미남 source=search pieces=…`, `vnew.cast.term_prompt cid=… terms=… pieces=… chars=…`,
   `vpoc.call where=vnew.cast_term_look …`. `vnew.cast.term_search_failed`가 있으면 `reason=`을 본다(`http401`·`http403` = 키·요금제, `http429` = 한도,
   `http422` = 매개변수, `timeout`·`unreachable` = 네트워크).
3. 화면: 캐스팅 칸에 "설명의 '꽃미남'을 '…'으로 해석했어요" 줄. 해석이 이상하면 그 조각을 Claude에 알린다(남은 이슈: 고치는 기능 없음).
4. 같은 캐릭터로 다시 뽑기: `vnew.cast.term` 로그가 없어야 한다(풀이 안 함). 다른 캐릭터에 같은 용어: `source=cache`.
5. 로그·화면 어디에도 키 값이 없는지 본다.

## 6. 끄기·되돌리기

- 빠른 끄기: Secret을 지우고(`kubectl -n whynot delete secret brave-search-api-key`) `rollout restart` — 키가 없으면 검색하지 않는다(지금 동작).
- 설정으로: `config.<target>.env`의 `VNEW_TERM_SEARCH=true` 줄을 지우거나 `false`로 바꾸고 배포.
- 저장된 캐릭터 기록(`casting.termSearch`)·사용자 캐시(`users/<owner>/characters/_term-cache/`)는 지우지 않는다. 끄면 읽지 않는다.
