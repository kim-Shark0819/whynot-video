# Phase 4 보고서 · 병합본 앱 배포 및 동등성 검증

| 항목 | 내용 |
|---|---|
| 작성 | 2026-09-09 |
| 대상 | `Dev-AI-POC-eks` + `AI-POC-eks` (계정 `730335451955` / `ap-northeast-2` / 프로필 `ai-poc-hub`) |
| 소스 | 브랜치 `merge/imaginus-overlay`, 커밋 `1427cc1` + 미커밋 41건 |
| 이미지 | `ai-poc-app:20260905-v1` · `ai-poc-frontend:20260905-v1` |
| 소스 스탬프 | `archive:fda772ba72f23204` |
| 범위 | 앱 배포. **GPU·영상 생성 제외** (사용자 지시) |
| 실행 기록 | `docs/run-log/phase4.md` |

---

## 1. 수행 내용

### 1.1 배포 파이프라인 수정 (AWS 변경 0)

배포를 시도하기 전에 파이프라인이 동작하지 않는 상태였다. 세 가지를 고쳤다.

**환경변수 전달 누락 (치명).** `infra/build-deploy.sh` 는 `AWS_ACCOUNT_ID`·`AWS_REGION`·`EKS_CLUSTER_NAME`·`K8S_NAMESPACE`·`ECR_APP_REPO`·`ECR_FRONTEND_REPO`·`APP_SERVICE_ACCOUNT` 를 `${VAR:?}` 로 요구한다. 그런데 `deploy.ps1` 의 SSM wrapper 는 `IMAGE_TAG`·`SOURCE_*`·`ADMIN_*` 6개만 넘겼다. **모든 배포가 시작 직후 죽는 상태였다.** Phase 1 에서 `build-deploy.sh` 만 탈하드코딩하고 호출부를 맞추지 않은 결과다. `env \` 로 9개를 추가 전달하고, `deploy.ps1` 의 필수키 검사 목록을 `build-deploy.sh` 의 요구와 같게 맞췄다.

**클러스터별 값 분리.** 배포 대상이 둘인데 `config.env` 는 단일값이었다. `config.env`(공통) + `config.dev.env`·`config.prod.env`(차이만) 구조를 만들었다. `config.env` 를 두 벌 복사하지 않은 이유는 위 버그와 같다 — 공통 값이 두 곳에 생기면 한쪽만 고쳐서 조용히 어긋난다. `render.py` 의 `load_config_chain()` 과 `preflight.ps1` 의 `Add-ToonOverlay` 가 같은 규칙을 쓰고, 오버레이에 `config.env` 에 없는 키가 있으면 **거절한다**(오타로 새 키를 만들면 아무 효과 없이 통과한다).

대상마다 다른 값은 6개다. `EKS_CLUSTER_NAME` · `EKS_OIDC_ID` · `EKS_CLUSTER_SECURITY_GROUP` · `IAM_APP_ROLE_NAME` · `IAM_APP_POLICY_NAME` · `ALB_NAME`. 이후 Neptune 값 3개가 추가됐다.

**ECR IMMUTABLE.** 두 저장소가 IMMUTABLE 이라 두 번째 대상 배포에서 같은 태그 push 가 실패하고, `set -e` 가 **`kubectl apply` 전에** 스크립트를 죽인다. `deploy.ps1` 의 `-AllowExistingTag` 안내문("매니페스트만 다시 적용됩니다")은 실제와 달랐다. `push_if_absent()` 를 넣어 이미 있는 태그는 push 만 건너뛰게 했다. 빌드와 스모크 테스트는 유지한다. **결과적으로 두 클러스터가 ECR 의 동일한 이미지를 쓴다** — 태그를 올려 두 번 빌드하면 같은 소스라도 바이트가 달라진다.

부수 수정 둘. `Get-SourceStamp -RepoRoot` 가 워크스페이스 루트를 받고 있었으나 git 저장소는 `AI-POC/` 다(루트에 `.git` 없음) — 스탬프가 `unknown-dirty` 로 떨어져 커밋 맥락이 사라졌다. 완료 메시지는 빈 `APP_DOMAIN` 으로 `https://` 만 찍고 있었으므로 실제 ALB DNS 조회로 바꿨다.

또 하나. 렌더 출력 경로는 대상과 무관하게 하나이므로 `-SkipRender` 로 다른 대상의 매니페스트를 올릴 수 있었다. 렌더 결과의 ALB 이름과 IRSA 역할 ARN 이 대상과 맞는지 검증하는 단계를 추가했다.

### 1.2 선행 인프라 (Terraform)

두 개 레이어를 신설했다. 정책 본문은 `infra/*.json.tmpl` 을 `templatefile()` 로 직접 읽는다. HCL 로 옮겨 적으면 사본이 생겨 검증 스크립트가 보는 정책과 실제 정책이 어긋난다.

`terraform/hub/25-app-iam` — 12개 생성. 렌더된 `service-account.yaml` 이 가리키는 `AI-POC-app-role` 이 **계정에 없었다.** 이 상태로 배포하면 파드는 정상 기동하고 `/api/health` 도 200 을 주며, S3·Bedrock 첫 호출에서만 실패한다. 추가로 **Dev 클러스터의 OIDC provider 가 IAM 에 등록되지 않아** Dev 는 IRSA 가 아예 동작하지 않았다. 운영 provider 는 `data` 로만 읽는다 — Terraform 이 관리하면 같은 provider 를 쓰는 `AI-POC-eks-albc-role` 까지 위험해진다.

`terraform/hub/26-neptune` — 11개 생성. `NEPTUNE_ENDPOINT` 가 비면 로그인이 전면 불가하다(§3 참고). 사용자 지시대로 DEV/운영 각각 하나씩 만들었다.

### 1.3 배포

`deploy.ps1 -Target dev` → `deploy.ps1 -Target prod -AllowExistingTag` 순서로 실행했다. DEV 가 통과한 뒤 운영으로 넘어갔다.

---

## 2. 결과

### 2.1 접속 확인

| 대상 | 접속 주소 | `/` | `/api/health` | 로그인 영상 |
|---|---|---|---|---|
| DEV | `https://dev-ai-poc-web.meta-clouds.com` | 200 | 200 | 영상·포스터 200 |
| 운영 | `https://ai-poc-web.meta-clouds.com` | 200 | 200 | 영상·포스터 200 |

ALB DNS 로도 접속되지만 지금은 80 이 443 으로 301 리다이렉트된다. `dev-ai-poc-web-alb-826800154...` / `ai-poc-web-alb-511652255...` 로 접속하면 인증서 호스트명이 맞지 않으므로 **도메인을 쓴다.**

ALB 는 별도로 만들지 않았다. 두 클러스터에 이미 `aws-load-balancer-controller` 가 2/2 로 돌고 있어 Ingress `ai-poc` 의 `load-balancer-name` 주석을 보고 컨트롤러가 생성했다. `internet-facing` / 퍼블릭 서브넷 3개 / `target-type: ip` / **리스너 80만**(ACM 인증서·도메인 없음) / healthcheck `/health` / idle timeout 300s.

### 2.2 `/api/health` 응답

| 항목 | DEV | 운영 |
|---|---|---|
| `ok` | true | true |
| `account.allowed` | true (`730335451955`) | true |
| `graph.configured` | true | true |
| `graph.reachable` | **true** | **true** |
| `manifestFailures.count` | 0 | 0 |
| `build.imageTag` | `20260909-v1` | `20260909-v1` |
| `build.sourceRevision` | `archive:e9d385e62a5c33be` | `archive:e9d385e62a5c33be` |

`sourceRevision` 이 양쪽 동일한 것이 핵심이다. 운영은 새로 빌드하지 않고 DEV 에서 스모크를 통과한 이미지를 그대로 재사용했다(`push_if_absent()`). 초기 배포는 `20260905-v1` / `archive:fda772ba72f23204` 였고, 로그인 영상 적용으로 `20260909-v1` 로 올렸다.

`graph.reachable=true` 는 Neptune 보안그룹 규칙이 의도대로 동작함을 뜻한다. 인바운드를 해당 클러스터 보안그룹에서 8182 로만 열었다.

### 2.3 파드 상태

| 대상 | `ai-poc-app` | `ai-poc-frontend` | 노드 |
|---|---|---|---|
| DEV | **2/2** | **2/2** | 1 |
| 운영 | **2/2** | **2/2** | 2 |

Pending 파드 0. 운영은 처음에 1/2 였고 D-23 A안(노드그룹 `desired` 1→2)으로 해소했다 — §3.1 과 `run-log/phase4.md` §8 참고. app·frontend 가 각각 두 노드에 1개씩 분산됐다(`topologySpreadConstraints` 동작).

### 2.4 빌드 단계 검증 (bastion)

| 항목 | 결과 |
|---|---|
| `app.js` 문법 검사 (`node --check`) | OK |
| import 스모크 (`from src import server, graph_store, providers`) | OK. 어댑터 6개 인식 |
| ffmpeg 스모크 (읽기 전용 루트 + tmpfs) | OK, 7.0.2-static (휠 내장) |
| 계정 가드 (`sts get-caller-identity` 대조) | 통과 |

### 2.5 생성한 AWS 리소스

**`hub/25-app-iam` (12)**

| 종류 | 이름 |
|---|---|
| OIDC provider | Dev 클러스터용 1개 (운영은 기존 것을 `data` 로 읽음) |
| IAM 역할 | `AI-POC-app-role` · `Dev-AI-POC-app-role` |
| IAM 정책 | `{AI-POC,Dev-AI-POC}-s3-access` · `{AI-POC,Dev-AI-POC}-bedrock-access` |
| 연결 | 4 |

**`hub/26-neptune` (11)**

| 항목 | DEV | 운영 |
|---|---|---|
| 클러스터 | `dev-ai-poc-graph` | `ai-poc-graph` |
| 상태 | available | available |
| 인스턴스 | `db.t4g.medium` writer 1 | 동일 |
| 백업 보관 | 1일 | 7일 |
| 저장 암호화 | on | on |
| 퍼블릭 접근 | off | off |
| 삭제 보호 | on | on |
| 최종 스냅샷 | 삭제 시 생성 | 동일 |
| 엔진 | `1.4.8.0` 고정, 마이너 자동 업그레이드 off | 동일 |
| IAM 인증 | **off** (§3.2) | **off** |

**ALB 2개** (컨트롤러 생성) · **ECR 이미지 2개** (`app` 103MB, `frontend` 23MB)

### 2.6 DEV ↔ 운영 동등성 (PRD §9.2)

두 클러스터에 같은 앱을 배포했으므로 직접 대조가 성립한다(D-16).

| 항목 | 판정 |
|---|---|
| 이미지 태그·소스 스탬프 | **일치** — `archive:fda772ba72f23204` |
| K8s 버전 | 일치 (`v1.36.3-eks`) |
| 네임스페이스·리소스 집합 | 일치 (ns `ai-poc`, Deployment 2 / Service 2 / ConfigMap 1 / Secret 1 / SA 1 / Ingress 1) |
| 노드 instance type | 일치 (`t3.medium` ×1) |
| ALB 구성 | 이름만 다름. scheme·target-type·리스너·healthcheck 동일 |
| IRSA | 구조 동일, 역할·OIDC 만 클러스터별 |
| Neptune | 구조 동일, 백업 보관일만 다름 (1일 / 7일 — 의도) |
| 애드온 | 일치 (`coredns`·`kube-proxy`·`vpc-cni`·`eks-pod-identity-agent`) |
| StorageClass | 일치 (`gp2` 만). 앱은 PVC 를 쓰지 않아 무관 |
| 레플리카 가용 | **일치** — 양쪽 2/2 (D-23 해소 후) |
| 노드 수 | 다름 — DEV 1 / 운영 2. 운영에 argocd·gitlab-runner(범위 밖)가 함께 있어 파드 슬롯이 더 필요하다. **의도된 차이** |

동등성 체크리스트 전 항목 통과. 남은 차이는 의도된 것(노드 수, Neptune 백업 보관일, 이름)뿐이다.

---

## 3. 발견한 차이·이슈

### 3.1 운영 클러스터 파드 용량 부족 (**해소 완료** — D-23 A안)

```
FailedScheduling  0/1 nodes are available: 1 Too many pods.
```

CPU·메모리 문제가 아니다. 요청은 CPU 25% / 메모리 10% 로 여유가 많다. **`t3.medium` 의 파드 상한이 17개**(VPC CNI ENI 한도)인데 운영 노드에는 argocd 7개 + gitlab-runner + kube-system 이 이미 올라가 있다. 앱 4개를 더하면 19개가 필요해 2개가 남는다.

DEV 노드는 kube-system 만 있어 4개가 모두 들어갔다. 즉 이 차이는 앱이 아니라 **운영 클러스터에 범위 밖 워크로드(argocd·gitlab-runner)가 함께 있기 때문**이다.

그대로 두면 두 가지가 문제였다. 운영에 이중화가 없고, 롤링 전략이 `maxSurge 1 / maxUnavailable 0` 이라 **다음 배포도 같은 지점에서 멈춘다**(새 파드를 띄울 빈 슬롯이 없다).

**조치 (A안).** `terraform/hub/20-eks-prod/` 를 만들어 기존 노드그룹을 `import` 하고 `desired_size` 를 1 → 2 로 올렸다. `max_size` 가 이미 2라 상한은 건드리지 않았다. plan 은 `update in-place` 였고 `0 to destroy` 였다 — 실제 변경은 `desired_size` 와 관리 태그 4개 추가뿐이다.

교체를 막는 장치를 두 겹으로 두었다. 이 노드에 argocd·gitlab-runner(범위 밖)가 함께 있어 노드그룹이 교체되면 함께 내려간다.

1. 교체 유발 속성을 실물 값으로 전부 명시 (`ami_type`·`capacity_type`·`instance_types`·`disk_size`·`node_role_arn`·`subnet_ids`·`version`·`release_version`). 특히 `release_version` 을 비워 두면 provider 가 최신을 원해 노드 교체를 제안한다
2. `prevent_destroy = true` — 교체가 계획되면 apply 가 실패한다

결과: 노드 2개 Ready, `ai-poc-app` 2/2, `ai-poc-frontend` 2/2, Pending 0, 파드가 두 노드에 분산. DEV 는 영향 없음. 상세는 `run-log/phase4.md` §8.

`desired_size` 에 `ignore_changes` 를 걸지 않은 이유: 설계 문서(tasks 3.2.2)의 지침은 오토스케일러가 값을 바꾸는 상황을 전제한다. 이 클러스터에는 cluster-autoscaler 도 Karpenter 도 없어 지금은 Terraform 이 단일 원본이다. 오토스케일러를 도입하면 그때 붙인다.

### 3.2 Neptune 접근 통제가 네트워크 한 겹뿐

`app/src/graph_store.py` 의 `run()` 은 순수 `urllib` POST 로 openCypher 를 호출하고 **SigV4 서명을 하지 않는다.** 따라서 Neptune IAM 인증을 켜면 모든 질의가 실패한다. `iam_database_authentication_enabled = false` 로 두었다.

이 DB 에는 사용자 비밀번호 해시(PBKDF2 210k)가 들어간다. 완화 조치로 인바운드를 해당 클러스터 보안그룹에서 8182 로만 열고, 두 Neptune 이 서로 다른 SG 를 가져 DEV 파드가 운영 그래프에 닿지 못하게 했으며, 저장 시 암호화를 켰다. 근본 해결은 앱에 SigV4 서명을 넣는 것이고 그것은 앱 코드 변경이라 이번 범위를 넘는다.

### 3.3 IAM 역할 description 제약

IAM **역할** description 은 `[\u0009\u000A\u000D\u0020-\u007E\u00A1-\u00FF]` 만 받는다. 한글도 em dash 도 거절되고 실패는 `CreateRole` 시점의 `ValidationError` 로 난다. 같은 계정에서 **`aws_iam_policy` 의 description 은 한글을 받아 주어** 더 헷갈린다. 역할 description 만 ASCII 로 바꿨다.

### 3.4 인수인계 문서가 낡아 있었다

`SESSION_HANDOFF.md` 는 "Phase 1.1 진행 중 중단"이라고 적혀 있으나 실제로는 **Phase 1 이 완료**되어 있었다(커밋 4개: 계획서 → 백엔드 → 프론트 → 보고서). 작업 전 `git log` 확인이 없으면 끝난 일을 다시 한다.

### 3.5 `TF_DATA_DIR` 오주입

부모 프로세스에서 `TF_DATA_DIR` 가 `…\terraform-data\ai-poc\40-video-gpu` 로 주입되고 있었다(PowerShell 프로필과 무관). 그대로 두면 다른 레이어의 `.terraform` 을 공유해 provider 캐시 불일치로 `validate` 가 실패한다. 레이어마다 명시 지정해 실행했다.

### 3.6 운영 절차 두 가지

`terraform apply` 는 **저장된 plan 파일로만** 실행한다(`apply tfplan.<layer>`). `-auto-approve` 는 금지 규칙이고, 없으면 대화형 확인에서 멈춰 타임아웃 난다. 한 번 그렇게 멈춘 프로세스를 종료해 S3 `.tflock` 이 남아 `force-unlock` 이 필요했다(state 내용은 건드리지 않음).

PowerShell 5.1 은 `-out=x` 처럼 `=` 가 든 인수를 쪼개 `Too many command line arguments` 를 낸다. 인수 전체를 따옴표로 감싼다.

### 3.7 범위 밖으로 남긴 것

| 항목 | 상태 |
|---|---|
| GPU 노드그룹 · Wan 2.2 추론 서버 | 사용자 지시로 제외. `VIDEO_GEN_URL` 은 값을 남겨 두어 화면이 "서버 연결 안 됨"을 정상 경로로 표시 |
| `S3_VIDEO_BUCKET` (us-west-2) | 비어 있음. Bedrock 영상 생성 미동작 |
| 도메인 · ACM · HTTPS | `APP_DOMAIN` 비어 있음 → 리스너 80만 |
| argocd · gitlab-runner | 운영 클러스터에 존재. 건드리지 않음 |
| Bedrock 실제 호출 | 모델 ACTIVE 는 확인. **호출 스모크는 미실시** |

---

## 4. NEEDS_DECISION

### ~~D-23 · 운영 클러스터 파드 용량~~ → **해소 (A안 적용)**

사용자가 A안을 승인해 적용했다. 노드그룹 `desired` 1 → 2, 파드 용량 17 → 34. 월 약 $38 증가. 상세는 §3.1.

### D-26 · bastion kubeconfig 공유 (신규)

검증 중 같은 실행 안에서 `kubectl get nodes` 는 운영을, `kubectl get pods` 는 DEV 를 돌려주는 모순이 나왔다. 원인은 **`/root/.kube/config` 를 여러 작업이 함께 쓰기** 때문이다. `aws eks update-kubeconfig` 가 같은 파일의 current-context 를 바꾸므로 두 작업이 동시에 다른 클러스터를 가리키면 조회 대상이 섞인다. 조회는 `--kubeconfig /tmp/kc-<대상>` 으로 격리해 해결했다.

문제는 `infra/build-deploy.sh` 도 `KUBECONFIG=/root/.kube/config` 를 쓴다는 것이다. 배포가 겹치면 **apply 대상 클러스터가 섞일 수 있다.** 창은 좁고(스크립트가 `update-kubeconfig` 직후 곧바로 apply) 계정 가드와 렌더 검증이 별도로 걸려 있지만, D-24(동시 작업)가 남아 있는 동안은 실질적 위험이다. 대상별 kubeconfig 로 바꿀지 결정이 필요하다.

### ~~D-25 · Bedrock 호출 스모크~~ → **해소 (양쪽 200)**

실제 호출로 확인했다. `POST /api/projects/<id>/summary`(Converse, `global.anthropic.claude-haiku-4-5-20251001-v1:0`)가 DEV 2.5초 · 운영 2.3초로 **HTTP 200**, 한국어 요약을 정상 생성했다.

1차 시도는 500 이었고 원인은 **앱 역할의 Marketplace 엔타이틀먼트 확인 권한 부재**였다. 오류 메시지가 "모델 액세스가 없다"로 읽히지만 그게 아니었다 — `AdministratorAccess` 사용자로 같은 모델을 직접 호출하면 성공했으므로 계정 구독은 이미 있었다. `infra/app-bedrock-policy.json.tmpl` 에 `aws-marketplace:ViewSubscriptions`(읽기 전용) 한 줄을 더해 해결했다. `Subscribe` 는 과금을 발생시킬 수 있어 넣지 않았다. 상세는 `run-log/phase4.md` §9-2.

### D-22 · Neptune IAM 인증 (§3.2)

현 상태(네트워크 한 겹)를 수용할지, 앱에 SigV4 서명을 넣어 IAM 인증을 켤지. 후자는 앱 코드 변경으로 범위 확대다.

### D-27 · `APP_DOMAIN` 이 공용 config 에 있다 (신규)

두 클러스터의 도메인이 다르다(`dev-ai-poc-web.meta-clouds.com` / `ai-poc-web.meta-clouds.com`). 그런데 `APP_DOMAIN` 은 공용 `config.env` 에 있어 두 대상이 같은 값을 본다. 작업 중 이 값이 채워졌다 비워졌다 하는 것도 관측됐다(다른 세션이 편집 중).

`EKS_CLUSTER_NAME`·`ALB_NAME` 등과 같은 성질이므로 `config.dev.env`·`config.prod.env` 로 옮기는 것이 맞다. 다만 도메인·TLS 는 다른 세션의 작업 영역(`k8s/ingress.yaml.tmpl` 의 `ALB_LISTEN_PORTS`·`ALB_TLS_ANNOTATIONS` 도입)이라 조율이 필요하다. 지금은 운영 Ingress 가 이미 의도한 상태(80+443·cert·redirect)라 급하지 않다.

### D-24 · 동시 작업 감지

`terraform/hub/40-video-gpu/`, `docs/gpu-video/`, `docs/run-log/gpu-video-P0.md` 가 이 세션 중에 생겼다(13:49~14:28). 이 세션이 만든 것이 아니며, 세션 시작 시점부터 `TF_DATA_DIR` 가 `…\ai-poc\40-video-gpu` 를 가리키고 있던 것과 일치한다. **다른 세션이 GPU 영상 레이어를 동시에 작업 중으로 보인다.** 같은 저장소·같은 계정을 두 세션이 함께 만지면 state 락 충돌과 커밋 충돌이 난다. 이 세션은 해당 경로를 건드리지 않았다. 진행 방식 정리가 필요하다.

---

## 5. 다음 Phase 계획

### 5.1 즉시

1. ~~운영 파드 용량 확보~~ — **완료.** 양쪽 2/2, Pending 0
2. ~~기능 스모크 — 로그인~~ — **완료.** 양쪽 200. Neptune **쓰기** 경로까지 검증됨(bootstrap 이 admin 생성)
3. ~~Bedrock 콘텐츠 생성 1건~~ — **완료.** 양쪽 200 (D-25)
4. ~~로그인 영상 적용~~ — **완료.** 양쪽 영상·포스터 200
5. `deploy/verify/*.ps1` 실행. 아직 개별 검증 스크립트는 돌리지 않았다
6. **`APP_DOMAIN` 을 클러스터별 오버레이로 이동** — 지금 공용 `config.env` 에 있어 두 클러스터가 같은 값을 본다. 다른 세션 작업 영역과 겹치므로 조율 필요 (§4 D-27)
7. D-26 — `build-deploy.sh` 의 kubeconfig 를 대상별 파일로 분리할지 결정

### 5.2 커밋 정리

지금 워킹 디렉터리에 미커밋 41건이 있고 그 상태로 배포됐다(`sourceRevision` 이 아카이브 해시라 소스 자체는 특정된다). 이번 변경을 주제별로 커밋해야 한다.

- 파이프라인 수정 (`deploy/` 4개 + `infra/build-deploy.sh`)
- 클러스터별 config (`config.dev.env`·`config.prod.env`)
- Terraform 레이어 2개 (`hub/25-app-iam`·`hub/26-neptune`)
- 문서 (`run-log/phase2.md`·`phase4.md`·이 보고서)

`terraform.tfvars`·`backend.hcl`·`config.env` 는 `.gitignore` 대상이다. `*.example` 만 추적된다.

### 5.3 남은 Phase

| Phase | 내용 | 상태 |
|---|---|---|
| 2 | VPC Terraform import (`hub/10-network`) | **미착수.** 이번에 건너뛰고 배포를 먼저 했다 |
| 3 | 재사용 모듈 (`modules/{eks,nodegroup,irsa,addons}`) | 미착수. D-23 A안을 Terraform 으로 하려면 노드그룹 import 가 여기에 걸린다 |
| 5 | 고객사 배포 패키징 | 미착수 |

이번에 만든 `hub/25-app-iam`·`hub/26-neptune` 은 Phase 3·5 에서 모듈로 뽑아낼 후보다. 특히 IRSA 신뢰 정책과 Neptune SG 규칙은 고객사마다 클러스터가 달라 그대로 재사용된다.

### 5.4 SESSION_HANDOFF 갱신

`§지금 위치` 가 "Phase 1.1 진행 중"으로 남아 있다. 실제 위치(Phase 1 완료, Phase 4 앱 배포 완료·D-23 대기, Phase 2·3 미착수)로 고쳐야 다음 세션이 끝난 일을 다시 하지 않는다.
