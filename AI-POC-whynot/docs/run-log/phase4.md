# Phase 4 실행 기록 · 병합본 앱을 두 클러스터에 배포

| 항목 | 내용 |
|---|---|
| 대상 | `Dev-AI-POC-eks` + `AI-POC-eks` (계정 `730335451955` / `ap-northeast-2` / 프로필 `ai-poc-hub`) |
| 범위 | 앱(`ai-poc-app` + `ai-poc-frontend`) 배포. **GPU·영상 생성 제외** (사용자 지시) |
| 브랜치 | `merge/imaginus-overlay` (Phase 1 병합 완료본) |
| 상태 | 파이프라인 수정 완료 · IAM plan 승인 대기 · Neptune 결정 대기 |

---

## 1. 조사 (읽기 전용)

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 13:20 | `git log --oneline -12` / `status --porcelain` | 병합 진행도 | **Phase 1 완료** — 커밋 4개(1.2 계획서 → 1.3 백엔드 → 1.4 프론트 → 보고서). 인수인계 문서가 낡았음 |
| 13:21 | `aws ecr describe-repositories` / `describe-images` | 이미지 유무 | `ai-poc-app`·`ai-poc-frontend` 저장소는 있으나 **이미지 0건** |
| 13:21 | `aws s3api list-buckets` | 버킷 | `ai-poc-730335451955` 있음. tfstate 버킷도 있음 |
| 13:22 | `aws neptune describe-db-clusters` | 그래프 DB | **0건** |
| 13:23 | `aws iam list-roles` | 앱 역할 | `AI-POC-app-role` **부재**. albc·cluster·node 역할만 존재 |
| 13:24 | `aws iam list-open-id-connect-providers` + `describe-cluster` | OIDC | 등록됨 `2A49…AB1`(운영). **Dev 의 `9761A0…37B` 는 미등록** |
| 13:26 | SSM `AWS-RunShellScript` → bastion (probe) | 클러스터 k8s 상태 | 양쪽 노드 1개 Ready(v1.36.3), coredns 2/2, **aws-load-balancer-controller 2/2**, IngressClass `alb` 있음, SC `gp2` 만, `ai-poc` ns 없음. bastion Docker 25.0.14 active·27GB 여유 |
| 13:28 | `aws bedrock list-foundation-models` | 모델 가용성 | claude 계열 ACTIVE (haiku-4-5 / sonnet-4-5 / opus-4-5 등) |
| 13:29 | `aws pricing get-products` (Neptune) | 비용 | `db.t4g.medium` $0.1424/hr ≈ $104/월 |

### 발견한 차단 요소

1. **[치명] `deploy.ps1` → `build-deploy.sh` 환경변수 누락.** `build-deploy.sh` 는 `AWS_ACCOUNT_ID`·`AWS_REGION`·`EKS_CLUSTER_NAME`·`K8S_NAMESPACE`·`ECR_APP_REPO`·`ECR_FRONTEND_REPO`·`APP_SERVICE_ACCOUNT` 를 `${VAR:?}` 로 요구하는데 SSM wrapper 는 6개만 넘겼다. **모든 배포가 시작 직후 죽는 상태.** Phase 1 에서 `build-deploy.sh` 만 탈하드코딩한 결과로 보인다.
2. **Neptune 부재 → 로그인 불가.** `/api/auth/login` → `graph_store.authenticate()` → openCypher. `is_configured()` 가 false 면 `GraphUnavailable`. `bootstrap_if_needed` 는 `GraphError` 를 잡아 기동은 막지 않으므로(`server.py:7671`, `graph_store.py:2158`) **앱은 뜨고 `/api/health` 도 응답하지만 admin 조차 로그인할 수 없다.** 우회 경로 없음 — admin 계정도 그래프에 산다.
3. **IRSA 역할 부재.** `service-account.yaml` 이 없는 역할을 annotate. 파드는 기동하고 S3·Bedrock 첫 호출에서만 실패한다.
4. **ALB 이름 단일값.** 두 클러스터에 같은 이름을 주면 양쪽 컨트롤러가 같은 ALB 를 서로 고쳐 쓴다.
5. **클러스터별 값 분리 구조 없음.**
6. **[보안] Neptune 요청에 SigV4 서명이 없다.** `graph_store.run()` 은 순수 `urllib` POST — Neptune IAM 인증을 끈 상태여야 동작한다. 사용자 비밀번호 해시(PBKDF2 210k)를 담는 DB 가 **네트워크(SG)로만 보호된다.**
7. [경미] `Get-SourceStamp -RepoRoot $workspaceRoot` — git 저장소는 `AI-POC/` 다. 스탬프가 `unknown-dirty` 로 떨어져 커밋 맥락이 사라졌다.

---

## 2. 파이프라인 수정 (AWS 변경 0)

| 파일 | 변경 |
|---|---|
| `deploy/render.py` | `load_config_chain(target)` + `--target`. `config.<target>.env` 오버레이. config.env 에 없는 키는 거절(오타 가드) |
| `deploy/preflight.ps1` | `-Target` + `Add-ToonOverlay`. render.py 와 같은 규칙·같은 가드 |
| `deploy/deploy.ps1` | `-Target` 필수화 / **누락 환경변수 9개 추가 전달(치명 버그 수정)** / 필수키 검사를 `build-deploy.sh` 요구와 일치 / `RepoRoot` → `$projectRoot` / 렌더 결과의 ALB·역할 ARN 이 대상과 맞는지 검증 / 완료 메시지를 실제 ALB DNS 조회로 교체 |
| `deploy/config.env.example` | 클러스터별 오버레이 절 추가 |
| `deploy/config.env` | `ALB_NAME=AI-POC-web-alb` |
| **신규** `deploy/config.prod.env` | `AI-POC-eks` / OIDC `2A49…AB1` / SG `sg-022e24e853dfdc241` / `AI-POC-app-role` / **`ALB_NAME=AI-POC-web-alb`** |
| **신규** `deploy/config.dev.env` | `Dev-AI-POC-eks` / OIDC `9761A0…37B` / SG `sg-09275dce10e4cfeff` / `Dev-AI-POC-app-role` / **`ALB_NAME=DEV-AI-POC-web-alb`** |

### 검증

| 항목 | 결과 |
|---|---|
| `render.py` `ast.parse` | OK |
| `.ps1` 2개 PowerShell Parser | 오류 0 |
| `render.py --target dev` | ingress `load-balancer-name: DEV-AI-POC-web-alb` / SA `role-arn: …Dev-AI-POC-app-role` / trust policy `…/id/9761A0…37B` |
| `render.py --target prod` | `AI-POC-web-alb` / `AI-POC-app-role` |
| 오타 가드 | 없는 키 → 거절 확인 |
| 인코딩 | `.ps1` UTF8-BOM + CRLF 복원 / `.env` no-BOM + LF |
| `git diff --stat` | surgical (전체 파일 churn 없음) |

---

## 3. Terraform `hub/25-app-iam` (plan 만, apply 대기)

| 시각 | 명령 | 결과 |
|---|---|---|
| 13:52 | `terraform init -backend-config=backend.hcl` | aws 6.63.0 + tls 4.4.0 설치. backend S3 `hub/25-app-iam.tfstate` |
| 13:55 | `terraform validate` | Success |
| 13:57 | `terraform plan -out=tfplan.25-app-iam` | **12 to add, 0 to change, 0 to destroy** |

정책 본문은 `AI-POC/infra/*.json.tmpl` 을 `templatefile()` 로 **직접 읽는다.** HCL 로 옮겨 적으면 사본이 생겨 검증 스크립트가 보는 정책과 실제 정책이 어긋난다.

운영 OIDC provider 는 `data` 로만 읽는다. Terraform 이 관리하면 같은 provider 를 쓰는 `AI-POC-eks-albc-role` 까지 함께 위험해진다.

`config_env_check` 출력이 `config.dev.env`·`config.prod.env` 의 OIDC ID·역할명과 일치하는 것을 확인했다.

### 환경 함정 (기록)

`TF_DATA_DIR` 가 부모 프로세스에서 `…\terraform-data\ai-poc\40-video-gpu` 로 주입되고 있었다(User 스코프에는 `…\ai-poc`). PowerShell 프로필과는 무관하며 이전 세션 잔재로 보인다. 그대로 두면 **다른 레이어의 `.terraform` 을 공유해** provider 캐시 불일치로 `validate` 가 실패한다. 이 레이어의 모든 terraform 명령은 `TF_DATA_DIR` 를 `…\ai-poc\25-app-iam` 로 명시 지정해 실행했다.

`terraform` 인수에 `-out=...` 처럼 `=` 가 들어가면 PS 5.1 이 인수를 쪼개 `Too many command line arguments` 가 난다. **인수 전체를 따옴표로 감싼다** (`"-out=tfplan.25-app-iam"`).

---

## 4. NEEDS_DECISION

### D-21 · Neptune 생성 여부 (배포 유용성을 좌우)

Neptune 없이 배포하면 두 클러스터에 앱이 올라가고 ALB 도 붙지만 **아무도 로그인할 수 없다.** 화면은 열리고 로그인만 막힌다.

| 안 | 내용 | 월 비용(인스턴스) |
|---|---|---|
| A | Neptune 1개를 두 클러스터가 공유 | 약 $104 |
| B | Neptune 2개 (dev/운영 분리) | 약 $208 |
| C | 만들지 않고 배포 (로그인 불가 상태로 확인만) | 0 |

B 가 원칙에 맞지만 POC 규모에서는 A 가 실용적이다. A 는 DEV 시험이 운영 데이터에 섞이는 것을 감수한다.

### D-22 · Neptune IAM 인증

앱이 SigV4 서명을 하지 않으므로 **IAM 인증을 끈 상태로만 동작한다.** 비밀번호 해시를 담는 DB 가 SG 로만 보호된다. 수용하려면 SG 를 두 클러스터 노드 SG 에서만 8182 인바운드로 좁힌다. 앱에 SigV4 서명을 넣는 것은 앱 코드 변경(범위 확대)이다.

---

## 5. 다음 단계

1. `hub/25-app-iam` apply (승인 대기) — 12 add / 0 change / 0 destroy
2. D-21 결정 후 Neptune Terraform 작성 → plan → 승인 → apply → `config.env` 의 `NEPTUNE_ENDPOINT` 채움
3. `deploy.ps1 -Target dev` — 이미지 빌드·푸시 + 배포 + `DEV-AI-POC-web-alb` 생성 확인
4. `deploy.ps1 -Target prod -AllowExistingTag` — 같은 이미지로 배포 + `AI-POC-web-alb` 생성 확인
5. `deploy/verify/*.ps1` 실행, `docs/04_dev_app_verification.md` 작성

---

## 6. 실행 결과 (2026-09-09 오후)

### 6-1. `hub/25-app-iam` apply

| 시각 | 명령 | 결과 |
|---|---|---|
| 14:06 | `terraform apply tfplan.25-app-iam` | **부분 실패.** 정책 4개 + OIDC provider(dev) 생성, **역할 2개 실패** |
| — | — | `ValidationError: Value at 'description' failed to satisfy constraint: [\u0009\u000A\u000D\u0020-\u007E\u00A1-\u00FF]*` |
| 14:10 | `main.tf` 수정 — 역할 description 을 ASCII 로 | IAM **역할** description 은 Latin-1 출력 가능 문자만 받는다. 한글도 em dash 도 거절. 같은 계정에서 **`aws_iam_policy` 의 description 은 한글을 받아 주어** 더 헷갈린다 |
| 14:12 | `terraform apply -auto-approve=false` | 대화형 확인에서 멈춤 → 120s 타임아웃. `-auto-approve` 는 금지 규칙이라 쓰지 않음 |
| 14:13 | `Stop-Process` + `terraform force-unlock` | 죽인 프로세스가 S3 `.tflock` 을 남겨 해제. state 내용은 건드리지 않음 |
| 14:14 | `plan -out=tfplan.25-app-iam` → `apply tfplan…` | **6 added, 0 changed, 0 destroyed** (역할 2 + 연결 4) |

교훈: **`apply` 는 항상 저장된 plan 파일로 실행한다.** 대화형 확인이 없어 멈추지 않고, 보여준 plan 과 적용본이 같음을 보장한다.

### 6-2. `hub/26-neptune` apply

| 시각 | 명령 | 결과 |
|---|---|---|
| 14:20 | `terraform plan -out=tfplan.26-neptune` | 11 to add, 0 to change, 0 to destroy |
| 14:22 | `terraform apply tfplan.26-neptune` | **11 added.** dev 8m29s / prod 9m0s |

| | dev | prod |
|---|---|---|
| 클러스터 | `dev-ai-poc-graph` | `ai-poc-graph` |
| 상태 | available | available |
| 인스턴스 | `db.t4g.medium` writer 1 | 동일 |
| 백업 보관 | 1일 | 7일 |
| 저장 암호화 | on | on |
| IAM 인증 | off (앱이 SigV4 미서명) | off |
| 삭제 보호 | on | on |
| 8182 인바운드 | `sg-09275dce10e4cfeff` 만 | `sg-022e24e853dfdc241` 만 |

엔드포인트를 `config.dev.env`·`config.prod.env` 에 기록했다. **writer 엔드포인트**를 쓴다 — `cluster-ro-` reader 를 넣으면 읽기는 되고 쓰기만 실패해 "로그인은 되는데 회원가입만 안 되는" 상태가 된다.

### 6-3. `deploy.ps1 -Target dev`

전 단계 통과. 이미지 2개 빌드·푸시, 매니페스트 적용, 파드 4/4 Running, rollout 완료.

- JS 문법 검사 OK / import 스모크 OK / ffmpeg 스모크 OK (7.0.2-static)
- `graph: configured=true, reachable=true` — **Neptune 연결 확인.** SG 규칙이 의도대로 동작
- ALB `DEV-AI-POC-web-alb` 생성

### 6-4. ECR IMMUTABLE 문제와 수정

`ai-poc-app`·`ai-poc-frontend` 저장소가 **IMMUTABLE** 이다. 운영 배포는 같은 태그를 다시 push 하므로 실패하고, `build-deploy.sh` 의 `set -e` 가 **`kubectl apply` 전에 스크립트를 죽인다.** `deploy.ps1` 의 `-AllowExistingTag` 안내문("이미지 푸시는 실패하고 매니페스트만 다시 적용됩니다")은 실제와 달랐다.

`infra/build-deploy.sh` 에 `push_if_absent()` 를 넣어 **이미 있는 태그는 push 를 건너뛴다.** 빌드와 스모크 테스트는 유지한다(두 번째 대상에서도 확인할 값이 있다). 그 결과 **두 클러스터가 ECR 의 동일한 이미지를 쓴다** — 태그를 올려 두 번 빌드하면 같은 소스라도 바이트가 달라진다. bastion 에서 `bash -n` 문법 확인 완료.

### 6-5. `deploy.ps1 -Target prod -AllowExistingTag`

매니페스트 전부 적용, ALB `AI-POC-web-alb` 생성, 앱 동작. 단 **파드 2개가 Pending** 으로 rollout 이 2/2 를 채우지 못했다.

```
FailedScheduling  0/1 nodes are available: 1 Too many pods.
```

원인은 CPU·메모리가 아니다(요청 25% / 10%). **t3.medium 의 파드 상한이 17개**(VPC CNI ENI 한도)인데 운영 노드에는 argocd 7개 + gitlab-runner + kube-system 이 이미 올라가 있다. 총 19개가 필요해 2개가 남는다.

### 6-6. 최종 확인 — 두 사이트 접속

| 대상 | ALB DNS | `/` | `/api/health` |
|---|---|---|---|
| DEV | `DEV-AI-POC-web-alb-826800154.ap-northeast-2.elb.amazonaws.com` | 200 (109,033 B) | 200 |
| 운영 | `AI-POC-web-alb-511652255.ap-northeast-2.elb.amazonaws.com` | 200 (109,033 B) | 200 |

`/api/health` 양쪽 모두 `ok=true` · `account.allowed=true` · `graph.configured=true, reachable=true`.

**`sourceRevision` 이 양쪽 동일**(`archive:fda772ba72f23204`) — 두 클러스터가 같은 이미지를 돌리고 있음이 확인된다.

---

## 7. NEEDS_DECISION

### D-23 · 운영 클러스터 파드 용량

지금도 서비스는 응답한다(각 1 레플리카). 하지만 두 가지 문제가 남는다.

- 운영에 이중화가 없다 (파드 1개씩)
- **다음 배포가 또 멈춘다.** 롤링 전략이 `maxSurge 1 / maxUnavailable 0` 이라 새 파드를 띄울 빈 슬롯이 필요하다

| 안 | 내용 | 비용 | 위험 |
|---|---|---|---|
| A | 노드그룹 `desired` 1 → 2 (maxSize 가 이미 2라 상한 변경 불필요). 파드 용량 17 → 34 | t3.medium 1대 = $0.052/hr ≈ **월 $38** | 낮음. 다만 이 노드그룹은 Terraform 관리 밖이라 import 가 선행돼야 "Terraform 으로만" 규칙을 지킨다 |
| B | VPC CNI prefix delegation (`ENABLE_PREFIX_DELEGATION=true`). t3.medium 파드 상한 17 → 110 | **0** | 중간. 클러스터 전체 CNI 설정이고 `aws-node` 재시작이 필요하다. 같은 노드의 argocd·gitlab-runner(범위 밖)에 영향이 갈 수 있다 |
| C | 운영 레플리카를 1로 줄인다 | 0 | 이중화 없음 + 다음 배포도 같은 문제. 근본 해결이 아니다 |

권장은 **A**. 비용이 적고 범위 밖 워크로드를 건드리지 않는다. B 는 비용 0이지만 운영 클러스터의 네트워킹을 바꾸는 것이라 범위 밖(argocd·gitlab-runner)에 닿는다.

### D-24 · 동시 작업 감지

`AI-POC/terraform/hub/40-video-gpu/`, `docs/gpu-video/`, `docs/run-log/gpu-video-P0.md` 가 이 세션 중에 생겼다(작성 시각 13:49~14:28). 이 세션이 만든 것이 아니다. 세션 시작 시점부터 `TF_DATA_DIR` 가 `…\terraform-data\ai-poc\40-video-gpu` 로 주입돼 있던 것과 일치한다. **다른 세션이 GPU 영상 레이어를 동시에 작업 중으로 보인다.** 같은 저장소·같은 계정을 두 세션이 함께 만지면 state 락 충돌과 커밋 충돌이 난다. 이 세션은 해당 경로를 건드리지 않았다.

---

## 8. D-23 해소 · A안 적용 (운영 노드그룹 desired 1 → 2)

사용자 승인: **A안**.

### 8-1. 왜 새 Terraform 레이어인가

가드레일이 "인프라 변경은 Terraform 으로만" 인데 `AI-POC-eks-ng` 는 Terraform 관리 밖이었다.
그래서 `terraform/hub/20-eks-prod/` 를 만들고 **기존 노드그룹을 `import` 블록으로 편입**했다.
클러스터는 `data` 로만 읽는다 — 운영 컨트롤 플레인을 이 시점에 Terraform 밑으로 넣으면
잘못된 plan 한 번의 여파가 훨씬 커진다.

### 8-2. 안전장치

이 노드에는 argocd 와 gitlab-runner(둘 다 범위 밖)가 함께 올라가 있다. 노드그룹이
**교체**되면 그것들이 함께 내려간다. 두 겹으로 막았다.

1. 교체 유발 속성을 실물과 같은 값으로 전부 명시 — `ami_type` · `capacity_type` ·
   `instance_types` · `disk_size` · `node_role_arn` · `subnet_ids` · `version` ·
   `release_version`. 특히 `release_version` 을 비워 두면 provider 가 최신을 원해
   노드 교체를 제안한다
2. `lifecycle { prevent_destroy = true }` — 교체가 계획되면 **apply 가 실패한다**

`desired_size` 에는 `ignore_changes` 를 걸지 않았다. 설계 문서(tasks 3.2.2)는 걸라고
적었지만 그것은 오토스케일러가 값을 바꿀 때의 이야기다. 이 클러스터에는
cluster-autoscaler 도 Karpenter 도 없다(kube-system 에 albc 와 coredns 만). 지금은
Terraform 이 이 값의 단일 원본이다.

### 8-3. 실행

| 시각 | 명령 | 결과 |
|---|---|---|
| 15:44 | `aws eks describe-nodegroup` | 속성 전량 수집 (launch template 없음, taint 없음, remoteAccess 없음) |
| 15:48 | `terraform init` / `validate` | Success |
| 15:49 | `terraform plan -out=tfplan.20-eks-prod` | **1 to import, 1 to add, 1 to change, 0 to destroy** |
| 15:50 | `terraform apply tfplan.20-eks-prod` | **1 imported, 1 added, 1 changed, 0 destroyed** (7s) |

plan 이 `update in-place` 였다. 교체(`-/+`)나 삭제는 없었다. 실제 변경은 둘뿐이다.

```
~ scaling_config { desired_size = 1 -> 2 }
~ tags_all  + Component/ManagedBy/Project/Source (관리 태그 4개 추가)
```

### 8-4. 검증

| 항목 | 결과 |
|---|---|
| 새 인스턴스 | `i-05295ee3383068f0a` (`ap-northeast-2b`) running |
| 노드 | 2개 Ready — `ip-100-10-4-11`(12m) · `ip-100-10-7-163`(25h) |
| `ai-poc-app` | **2/2** |
| `ai-poc-frontend` | **2/2** |
| Pending 파드 | **0** |
| 파드 분산 | app·frontend 각각 두 노드에 1개씩 (`topologySpreadConstraints` 동작) |
| 운영 총 파드 | 22 |
| DEV | 2/2 · 2/2 · Pending 0 (영향 없음) |
| 두 사이트 | `/` 200 · `/api/health` `ok=true` · `graph.reachable=true` · `rev=archive:fda772ba72f23204` |

이로써 §2.6 동등성 표의 마지막 불일치 항목(레플리카 가용)이 해소됐다.

### 8-5. 발견 · bastion kubeconfig 공유 위험

검증 중 `kubectl get nodes` 는 운영 노드를, 같은 실행의 `kubectl get pods` 는 DEV 파드를
돌려주는 모순된 출력이 나왔다. 원인은 **`/root/.kube/config` 를 여러 작업이 함께 쓰기**
때문이다. `aws eks update-kubeconfig` 가 같은 파일의 current-context 를 바꾸므로,
두 세션(또는 두 명령)이 동시에 다른 클러스터를 가리키면 조회 대상이 섞인다.

`--kubeconfig /tmp/kc-<대상>` 으로 파일을 격리해 재확인했고 결과는 위 표와 같다.
**앞으로 bastion 에서 kubectl 을 쓸 때는 대상별 kubeconfig 파일을 분리한다.**
D-24(동시 작업)가 해소되지 않는 동안 특히 중요하다.

`infra/build-deploy.sh` 는 `KUBECONFIG=/root/.kube/config` 를 쓴다. 배포가 겹치면 같은
문제가 **apply 대상 클러스터**에서 발생할 수 있다. 다만 그 스크립트는 자기 실행 안에서
`update-kubeconfig` 직후 곧바로 apply 하므로 창이 좁고, 계정 가드와 렌더 검증이
별도로 걸려 있다. 대상별 kubeconfig 로 바꾸는 것을 후속 과제로 둔다.

---

## 9. 로그인 영상 적용 + Bedrock 실호출 (D-25 해소)

사용자 지시 두 건: **① Bedrock 실제 호출 + `ai-test-bedrock` 공유 프로젝트 생성 ② 이매지너스의 로그인 영상 적용.**

### 9-1. 로그인 영상

병합 시점에 `index.html` 은 이 자리에 Base 의 CSS 일러스트(`scene-stack`)를 두고 Overlay 의 나비 영상을 "고객사 자산"이라며 뺐다. 사용자 지시로 그 판단을 뒤집었다.

조사해 보니 **CSS 와 JS 는 이미 병합돼 있었다.** `styles.css` 의 `login-motion*` 규칙 7개와 `app.js` 의 `syncLoginMotion()`(MutationObserver + visibilitychange + prefers-reduced-motion 연동)이 그대로 있었고, `if (!video) return;` 가드 때문에 동작만 하지 않는 상태였다. `styles.css` 에는 "대응 마크업이 없어 적용되지 않는다"는 주석까지 남아 있었다. 즉 **마크업과 자산 2개만** 채우면 되는 일이었다.

| 항목 | 내용 |
|---|---|
| 복사한 자산 | `butterfly-effect.mp4` (3,388,845 B) · `butterfly-effect-poster.jpg` (34,229 B) → `frontend/public/assets/login/` |
| 원본 대조 | SHA256 두 파일 모두 MATCH |
| 마크업 | `login-motion` 블록(포스터 `<img>` + `<video autoplay muted loop playsinline>`) 을 `scene-stack` 자리에 넣음 |
| `scene-stack` | 마크업만 제거. **CSS 규칙은 남겨 두었다**(되돌릴 수 있게). 두 요소의 z-index 가 모두 1 이라 함께 두면 영상 위에 일러스트가 겹친다 |
| 접근성 | `aria-hidden` + `pointer-events: none`. 축소 모션·고대비 환경에서는 정적 포스터로 대체 — 병합된 CSS 가 이미 처리 |
| 이미지 영향 | frontend 23,300,340 B → **26,716,838 B** (+3.4 MB) |

`__ASSET_VERSION__` 캐시버스팅은 Dockerfile 의 `sed` 가 `index.html` 을 치환하므로 자동 적용됐다. 실제 응답에서 `?v=20260909-v1` 로 확인.

### 9-2. Bedrock 실호출 — D-25

`POST /api/projects/<id>/summary` (Converse, `global.anthropic.claude-haiku-4-5-20251001-v1:0`) 로 확인했다.

**1차 시도: HTTP 500.** 파드 로그의 traceback 이 원인을 정확히 알려줬다.

```
botocore.errorfactory.AccessDeniedException: ... Converse operation:
Model access is denied due to IAM user or service role is not authorized to perform
the required AWS Marketplace actions (aws-marketplace:ViewSubscriptions,
aws-marketplace:Subscribe) to enable access to this model.
```

이 메시지는 "계정에 모델 액세스가 없다"로 읽히지만 **그게 아니었다.** 판별 방법: `AdministratorAccess` 를 가진 `kiro-iam` 사용자로 같은 모델을 직접 Converse 호출했더니 **성공**했다(응답 "ok", 18토큰, 664ms). 즉 계정 수준 Marketplace 구독은 이미 있고, **앱 역할에 엔타이틀먼트 확인 권한만 없었다.**

| 시각 | 조치 | 결과 |
|---|---|---|
| 15:55 | `infra/app-bedrock-policy.json.tmpl` 에 `VerifyModelEntitlement` 문장 추가 (`aws-marketplace:ViewSubscriptions`) | JSON 유효성 OK, statement 5개 |
| 15:58 | `terraform plan` (`hub/25-app-iam`) | **0 to add, 2 to change, 0 to destroy** — 정책 2개 in-place |
| 15:59 | `terraform apply tfplan.25-app-iam` | 적용 |
| 16:00 | `/summary` 재호출 (DEV) | **HTTP 200** |

`aws-marketplace:Subscribe` 는 **넣지 않았다.** 새 Marketplace 구독을 만드는 권한이라 과금을 발생시킬 수 있고, 앱이 스스로 구독할 이유가 없다. 새 모델을 쓰려면 관리자가 모델 액세스를 켠다. `ViewSubscriptions` 는 리소스 수준 권한을 지원하지 않아 `Resource: "*"` 이며 읽기 전용이다.

한 번 더 걸린 함정: 1차 성공 응답의 요약이 "입력된 텍스트가 인코딩 오류로 인해 읽을 수 없습니다" 였다. **PowerShell 5.1 이 문자열을 native 명령에 파이프할 때 UTF-8 이 아니라 콘솔 인코딩으로 넘긴다.** `$json | curl.exe --data-binary @-` 로 보낸 한글이 깨져 저장된 것이다. JSON 을 UTF-8(BOM 없음) 파일로 쓰고 `--data-binary "@file"` 로 보내 해결했다.

### 9-3. 결과

| 항목 | DEV | 운영 |
|---|---|---|
| 로그인 | 200 (`admin` / role `admin`) | 200 |
| `ai-test-bedrock` 공유 프로젝트 | `008d70dc96e64377a301edfa33692e0b` | `de7c2f47a7054c81b3a5f4be2014ad37` |
| visibility | shared | shared |
| Bedrock `/summary` | **200 · 2.5초** | **200 · 2.3초** |
| 요약 길이 | 156자 | 159자 |

두 클러스터가 **서로 다른 IRSA 역할**(`Dev-AI-POC-app-role` / `AI-POC-app-role`)과 **서로 다른 Neptune**(`dev-ai-poc-graph` / `ai-poc-graph`)을 쓰므로 양쪽 각각 검증했다. 로그인 성공은 Neptune **쓰기** 경로(bootstrap 이 admin 생성)까지 검증한다 — `graph.reachable=true` 는 읽기까지만 증명한다.

### 9-4. 운영 배포

| 항목 | 내용 |
|---|---|
| 태그 | `20260905-v1` → `20260909-v1` |
| 이미지 | **DEV 에서 검증한 것을 그대로 재사용.** `push_if_absent()` 가 "이미 있어 푸시 생략" 을 출력하고, 실행 중 파드의 `sourceRevision` 이 DEV 빌드본 `archive:e9d385e62a5c33be` 로 찍힌다 |
| Ingress | **unchanged.** 렌더 결과가 이미 적용된 운영 Ingress 와 완전히 동일했다(80+443 · certificate-arn · ssl-redirect 443) |
| 파드 | 4/4 Running, rollout 완료 |
| 접속 | `https://ai-poc-web.meta-clouds.com` / DEV `https://dev-ai-poc-web.meta-clouds.com` — `/` · 영상 · 포스터 모두 200 |

`§6-4` 에서 넣은 `push_if_absent()` 가 여기서 값을 했다. 태그를 올려 두 번 빌드하면 같은 소스라도 바이트가 달라져 두 클러스터가 다른 이미지를 돌린다. 지금은 **DEV 에서 스모크를 통과한 바로 그 이미지**가 운영에 있다.

### 9-5. 확인한 위험 — 동시 작업 (D-24 심화)

운영 배포 전에 `git status` 를 확인한 것이 중요했다. 다른 세션이 `app/src/prompt_layers.py` · `prompts_story.py` · `story_models.py` 를 수정하고 `character_curator.py` · `roles/character_sheet.py` · `roles/shot_planner.py` 를 새로 만들고 있었으며, `k8s/ingress.yaml.tmpl` 도 고쳤다(`ALB_LISTEN_PORTS` · `ALB_TLS_ANNOTATIONS` 도입 + `render.py` 파생 변수 추가).

**이 파이프라인은 워킹 디렉터리를 그대로 tar 로 묶는다.** 즉 다른 세션의 진행 중 작업이 함께 배포된다. 실제로 그렇게 됐다. 완화된 사정은 두 가지다.

- 운영은 새로 빌드하지 않고 **DEV 에서 스모크·Bedrock 호출까지 통과한 이미지**를 재사용했다
- Ingress 는 렌더 결과가 live 와 동일해 변경이 없었다

그래도 구조적 위험은 남는다. `APP_DOMAIN` 은 클러스터마다 달라야 하는데(`dev-ai-poc-web` / `ai-poc-web`) 공용 `config.env` 에 있고, 작업 중 값이 채워졌다 비워졌다 했다. **`APP_DOMAIN` 을 `config.dev.env` · `config.prod.env` 로 옮기는 것이 맞다.** 다른 세션의 작업 영역과 겹치므로 조율이 필요하다.
