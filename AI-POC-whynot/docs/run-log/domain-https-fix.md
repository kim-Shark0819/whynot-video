# 도메인 HTTPS 접속 불가 해결

증상: `https://ai-poc-web.meta-clouds.com/` · `https://dev-ai-poc-web.meta-clouds.com/`
접속 시 브라우저가 "사이트에 연결할 수 없음".

대상 계정 `730335451955` · 리전 `ap-northeast-2` · 프로필 `ai-poc-hub`

---

## 1. 진단 — 무엇이 정상이었나

`https` 가 안 된다고 해서 DNS 나 ALB 를 먼저 의심하기 쉽다. 전부 정상이었다.

| 검사 | 결과 |
|---|---|
| DNS | **정상.** Route53 alias 가 각 ALB 를 정확히 가리킴. 도메인 IP 3개 = ALB IP 3개 |
| ALB | **정상.** `AI-POC-web-alb` · `DEV-AI-POC-web-alb` 둘 다 `active`, internet-facing, 3-AZ |
| 타깃 헬스 | **전부 `healthy`.** app 8000, frontend 8080 |
| **HTTP 접속** | **200 OK, 109KB HTML** |
| ACM 인증서 | `*.meta-clouds.com` **ISSUED** — 두 도메인 모두 커버 |
| **HTTPS 접속** | **불가** |

## 2. 원인

**ALB 에 `HTTP:80` 리스너만 있고 `HTTPS:443` 리스너가 없었다.**

443 에 아무것도 듣고 있지 않으면 TLS 핸드셰이크 이전에 TCP 연결 자체가 실패한다.
브라우저는 그것을 "사이트에 연결할 수 없음" 으로 표시한다. 앱 오류나 인증서 오류가
아니라 **포트가 닫혀 있던 것**이다.

보안 그룹도 함께 막고 있었다 — `k8s-aipoc-aipoc-*` SG 인바운드가 `tcp 80` 하나뿐이었다.

```
리스너 1개
    HTTP:80  cert=[]
      기본동작 고정응답 404
      규칙 1: path-pattern=['/api', '/api/*']
      규칙 2: path-pattern=['/*']
SG k8s-aipoc-aipoc-72cef98ce8 인바운드:
    tcp 80-80 cidr=['0.0.0.0/0']
```

## 3. 왜 그 상태였나 — 의도된 것이었다

ALB 는 `aws-load-balancer-controller` 가 Ingress 로 만든 것이고(SG 이름이 `k8s-*`),
그 Ingress 는 이 저장소의 `k8s/ingress.yaml.tmpl` 이 만든다. 템플릿에 이렇게 적혀 있었다.

> **80 포트만 연다.** 도메인과 ACM 인증서를 아직 만들지 않았다.
> 예전 매니페스트는 여기에 `[{"HTTP":80},{"HTTPS":443}]` 와 certificate-arn,
> ssl-redirect 가 함께 있었다. 인증서가 없는 계정에서 그대로 쓰면 컨트롤러가
> 리스너를 만들지 못하고, ssl-redirect 때문에 **80 도 함께 죽는다.**

즉 인증서가 없던 시점의 올바른 판단이었다. 그 뒤 도메인과 인증서가 준비됐는데
`config.env` 의 `APP_DOMAIN` / `ACM_CERTIFICATE_ARN` / `ROUTE53_HOSTED_ZONE_ID` 가
비어 있어 템플릿이 계속 80만 열고 있었다.

## 4. 조치

ALB 를 직접 고치지 않았다. 컨트롤러가 되돌린다. **Ingress 를 고쳤다.**

### 4.1 렌더러에 조건 분기를 파생값으로 넣었다

`render.py` 는 단순 변수 치환만 하고 조건문이 없다. 원저자가 만든 "값이 비면 80만 연다"
안전장치를 없애지 않으려고, `add_derived` 에 파생값 두 개를 더했다
(`S3_VIDEO_BUCKET_ARN` · `VIDEO_GEN_IMAGE` 가 쓰는 것과 같은 방식이다).

| `ACM_CERTIFICATE_ARN` | `APP_DOMAIN` | 결과 |
|---|---|---|
| 비어 있음 | — | `[{"HTTP":80}]` + 안내 주석 (**종전 동작 그대로**) |
| 있음 | 비어 있음 | `+{"HTTPS":443}` + `certificate-arn` |
| 있음 | 있음 | `+ ssl-redirect: "443"` |

`ssl-redirect` 를 도메인 유무에 걸어 둔 이유: ALB DNS 이름(`*.elb.amazonaws.com`)은 이
인증서가 덮지 않는다. 도메인 없이 리다이렉트를 켜면 ALB DNS 로 하던 테스트가
인증서 불일치로 막힌다.

### 4.2 설정값을 채웠다

| 파일 | 키 | 값 |
|---|---|---|
| `config.env` (공통) | `ACM_CERTIFICATE_ARN` | `*.meta-clouds.com` 인증서 ARN |
| `config.env` (공통) | `ROUTE53_HOSTED_ZONE_ID` | `meta-clouds.com` 영역 ID |
| `config.dev.env` | `APP_DOMAIN` | `dev-ai-poc-web.meta-clouds.com` |
| `config.prod.env` | `APP_DOMAIN` | `ai-poc-web.meta-clouds.com` |

인증서는 와일드카드라 두 환경이 공유하므로 공통 파일에 둔다. 도메인만 환경별로 다르다.

`config.env` 편집은 PowerShell 로 하지 않고 Python 으로 줄 단위 교체했다 —
PS 5.1 이 한글 주석 뒤 줄바꿈을 삼켜 다음 `KEY=VALUE` 가 주석에 합쳐진 사고가 있었다.
개행도 LF 로 고정했다. CRLF 가 섞이면 ARN 끝에 `\r` 이 붙어 조용히 무효가 된다.

### 4.3 `host` 규칙은 넣지 않았다

환경마다 전용 ALB 가 있어 호스트 분기가 필요 없다. 넣지 않으면 ALB DNS 이름으로도
접속해 확인할 수 있다. Ingress `HOSTS` 가 `*` 인 상태가 정상이다.

## 5. 검증

렌더 결과(dev):

```
alb.ingress.kubernetes.io/load-balancer-name: DEV-AI-POC-web-alb
alb.ingress.kubernetes.io/listen-ports: '[{"HTTP":80},{"HTTPS":443}]'
alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:...:certificate/9fd7fdac-...
alb.ingress.kubernetes.io/ssl-redirect: "443"
```

적용은 SSM 으로 배스천을 경유했다(클러스터가 프라이빗 전용). 매니페스트에 한글 주석과
따옴표가 섞여 있어 base64 로 실어 보내고, 보내기 전에 디코드해서 세 항목이 살아 있는지
확인했다. `--dry-run=server` 를 먼저 통과시킨 뒤 적용했다.

적용 후 두 환경 모두:

```
리스너 ['HTTP:80', 'HTTPS:443']   SG 443열림=True
HTTP 301 -> https://<도메인>:443/
HTTPS 200  109033바이트  text/html; charset=utf-8
```

컨트롤러가 443 리스너와 SG 인바운드 규칙을 **자동으로** 추가했다. 수동 조작은 없다.

### 5.1 로그인 → 내부 기능 E2E

두 도메인 모두 **실패 0건**.

| 단계 | dev | prod |
|---|---|---|
| 로그인 화면 `GET /` (title·app.js·styles.css 존재) | 200 | 200 |
| `GET /api/health` | 200 | 200 |
| 토큰 없이 `GET /api/projects` → 401 이어야 정상 | 401 | 401 |
| `POST /api/auth/login` | 200, 토큰 수신 | 200, 토큰 수신 |
| `GET /api/auth/me` | 200 | 200 |
| `GET /api/projects` (Neptune 읽기) | 200 | 200 |
| `GET /api/models` | 200 | 200 |
| `GET /api/styles` | 200 | 200 |
| `GET /api/usage/me` (Neptune 읽기) | 200 | 200 |
| `POST /api/projects` (**Neptune 쓰기**) | 201 | 201 |
| 생성분 조회 → 삭제 정리 | 200 / 200 | 200 / 200 |

비밀번호와 토큰은 출력하지 않았다. 점검용 프로젝트는 생성 후 삭제해 정리했다.

**Neptune 쓰기까지 확인됐다** — writer 엔드포인트가 올바르게 설정되어 있다.
reader 를 넣으면 로그인은 되고 프로젝트 생성만 실패하는데, 그 증상이 아니다.

## 6. 함정으로 기록할 것

| 함정 | 내용 |
|---|---|
| `.tmpl` 주석에 `${...}` 를 문자 그대로 쓰지 않는다 | 렌더러가 치환 대상으로 잡아 "config.env에 없는 변수 ['VAR']" 로 실패한다. 실제로 당했다 |
| `Invoke-WebRequest` 에 `-UseBasicParsing` 을 붙인다 | 없으면 PS 5.1 이 "스크립트 실행 위험" 확인 프롬프트를 띄우고 명령이 멈춘다 |
| SSM 명령은 PowerShell 이 중단돼도 원격에서는 계속 실행된다 | 운영 적용이 첫 호출에서 이미 끝나 두 번째가 `unchanged` 로 나왔다. 재시도 전에 실제 상태를 확인할 것 |
| ALB 리스너를 직접 만들지 않는다 | 컨트롤러 소유라 다음 조정에서 되돌아간다. Ingress 애노테이션이 유일한 진입점이다 |

## 7. 남은 것

- `verify/common.ps1` 과 `start-all.ps1` 이 `https://$APP_DOMAIN` 을 쓴다.
  `APP_DOMAIN` 이 채워졌으므로 이제 그 스크립트들이 동작한다. 다만
  `config.env` 만으로 렌더하면 `APP_DOMAIN` 이 비어 있으니 **`--target` 을 반드시 준다.**
- Ingress `HOSTS` 가 `*` 다. 한 ALB 에 도메인을 더 붙일 때가 되면 `host` 규칙을 넣는다.
