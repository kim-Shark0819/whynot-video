# PRD · AI-POC → DEV-AI-POC-eks 복제 및 Terraform 전환 (Kiro 실행 지시서)

| 항목 | 내용 |
|---|---|
| 문서 목적 | Kiro(Claude Opus 5 Max)가 아래 작업을 단계적으로 수행하기 위한 요구사항·설계·실행 지시서 |
| 버전 / 일자 | v1.0 / 2026-09-08 |
| 실행 주체 | Kiro |
| 승인 주체 | 사용자 (각 Phase 게이트에서 승인) |
| 권장 저장 위치 | `AI-POC/docs/PRD_AI-POC_DEV-EKS_Terraform.md` |

---

## 0. Kiro 실행 지침 (가장 먼저 읽을 것)

1. 이 문서는 **PRD + 실행 지시서**다. 요구사항(§2·§3), 설계(§5·§7·§8), 작업(§6), 완료 기준(§9)이 모두 들어 있다.
2. Kiro Spec 워크플로우를 따른다. 작업 시작 전 이 문서를 기반으로 `requirements.md` / `design.md` / `tasks.md`를 생성하고 사용자 승인을 받은 뒤 tasks를 실행한다. (spec 파일 위치는 §11 D-04)
3. 6개 Phase(0~5)로 진행한다. **각 Phase 종료 시 보고서를 제출하고, 사용자 승인 전에는 다음 Phase로 넘어가지 않는다.**
4. 불확실한 것은 추측하지 말고 §10.3 형식으로 질문한다. §11의 결정 항목(D-01~D-13)은 해당 시점 전에 반드시 확인한다.
5. 다른 파일·문서와 충돌하면 이 문서가 우선한다. 단, 실제 AWS 상태(Phase 0 탐색 결과)가 이 문서의 가정과 다르면 **사실을 우선하고 즉시 보고**한다.
6. §3 절대 규칙은 예외가 없다. 규칙 때문에 작업이 막히면 멈추고 보고한다.

**한눈에 보기**

- 하는 일: ① `이매지너스/`의 추가·수정분을 `AI-POC/`에 덧씌워 병합본 완성 → ② 통합(Hub) 계정의 VPC `AI-POC`를 Terraform import → ③ 같은 VPC에 `DEV-AI-POC-eks`를 재사용 모듈로 생성 → ④ 병합본 앱 배포·동등성 검증 → ⑤ 고객사 배포용 모듈·템플릿·가이드 완성
- 절대 규칙 3줄: **쓰기는 `AI-POC/` 안에서만** · **삭제는 반영하지 않고 추가·수정만** · **GPU 관련은 AI-POC 원본 유지**
- 인프라 변경은 Terraform으로만, 모든 apply는 plan 제시 후 사용자 승인

---

## 1. 배경 및 현재 상태

### 1.1 계정·폴더 구조

- AWS 계정: **운영 계정**과 **이매지너스 계정**이 있고 각각 별도 EKS가 있다. 이매지너스 EKS는 운영 EKS를 복제한 뒤 기능 추가·버그 수정이 이루어졌다.
- 변경 이력: `이매지너스_history.md` — 기존 `AI-POC/` 대비 변경 내용을 기록. (제공된 tree 기준 워크스페이스 루트에 위치. `이매지너스/` 하위에도 있을 수 있으므로 둘 다 확인하고 둘 다 있으면 최신 수정본을 기준으로 삼고 보고)
- `AI-POC/` = **기본 상태(Base)** 앱·인프라. `이매지너스/` = **개선판(Overlay)**.
- Overlay는 GPU node 기능을 삭제했으나, **이번 작업에서는 GPU 관련 설정·코드를 Base(AI-POC) 그대로 유지**한다.
- 통합(Hub) 계정: 폴더 내 자격증명으로 로그인. 내부에 VPC `AI-POC`와 EKS 클러스터 `AI-POC-eks`가 있다. 다른 VPC도 존재하지만 작업 대상이 아니다.
- 향후: Terraform으로 관리하고, 고객사 계정마다 동일 구성을 Terraform으로 배포한다. 고객사 수가 미정이므로 **모듈화 + 고객사명 prefix 변수화**가 필요하다.

### 1.2 용어

| 용어 | 정의 |
|---|---|
| Hub 계정 | 통합 계정. VPC `AI-POC`, EKS `AI-POC-eks` 보유. 운영 계정과 동일 계정으로 추정 — Phase 0에서 계정 ID로 확인(D-01) |
| Base | `AI-POC/` 폴더 (기본 상태) |
| Overlay | `이매지너스/` 폴더 (개선판, 읽기 전용) |
| 병합본 | Overlay의 추가·수정만 Base에 반영한 결과. `AI-POC/`에 in-place로 생성 |
| Protected | GPU 관련 설정·코드·매니페스트. 항상 Base 우선 |
| DEV 클러스터 | `DEV-AI-POC-eks`. Hub 계정, VPC `AI-POC` 내부에 신규 생성 |
| 고객사 배포 모듈 | `AI-POC/terraform-modules/`의 재사용 Terraform 모듈 세트 |
| 게이트 | Phase 종료 시 보고서 제출 → 사용자 승인 절차 |

---

## 2. 목표와 범위

### 2.1 목표 (Goals)

| ID | 목표 | 성공 판단 |
|---|---|---|
| G1 | 병합본 완성 | Base 대비 삭제 0, Overlay 추가·수정 반영, Protected 무변경, 빌드·dry-run 통과 |
| G2 | Hub 계정 VPC `AI-POC` Terraform화 | import 후 `terraform plan` = No changes |
| G3 | `DEV-AI-POC-eks` 생성 (같은 VPC, 재사용 모듈 사용) | 클러스터 ACTIVE, 노드(GPU 포함) Ready, add-on ACTIVE, 기존 리소스 변경 0 |
| G4 | 병합본 앱을 DEV에 배포·검증 | 동등성 체크리스트(§9.2) 통과 |
| G5 | 고객사 배포용 모듈·템플릿·가이드 | dummy 고객사 plan에서 모든 리소스명이 `<customer_name>-AI-POC-*` 규칙 준수 |

### 2.2 범위 밖 (Non-goals) — 하지 말 것

- 기존 `AI-POC-eks` 및 그 노드·워크로드의 변경·재구성·삭제
- `AI-POC` 외 다른 VPC의 Terraform화 또는 참조
- 이매지너스 계정의 리소스 접근·변경 (자격증명이 폴더에 있어도 사용 금지)
- Overlay에서 삭제된 항목을 Base에서 삭제하는 것
- 기존 `AI-POC-eks`의 Terraform import (후속 과제, 별도 승인 필요)
- 실제 고객사 계정으로의 apply (템플릿·plan 검증까지만)

---

## 3. 절대 규칙 (Hard Constraints)

| ID | 규칙 |
|---|---|
| H1 | **쓰기 허용 경로는 `AI-POC/**` 뿐이다.** `이매지너스/**`, `이매지너스_history.md`, 워크스페이스 루트의 다른 파일은 읽기 전용. Kiro 메타데이터(`.kiro/**`)만 D-04 결정에 따라 예외 가능 |
| H2 | **병합은 추가·수정만 반영한다.** Overlay에서 삭제된 파일·코드·설정·리소스는 Base에 그대로 남긴다. Base에만 있는 것은 항상 유지 |
| H3 | **GPU 관련은 Base가 우선한다(Protected).** Overlay가 GPU 부분을 삭제·축소·변경했다면 무시하고 Base 원본 사용 |
| H4 | AWS 작업 범위는 **Hub 계정 + VPC `AI-POC` 내부**로 한정. 기존 `AI-POC-eks`에 대한 쓰기(kubectl apply/delete/patch/scale 포함) 금지. 다른 VPC·계정 접근 금지 |
| H5 | **인프라 변경은 Terraform으로만.** `terraform apply`, 상태를 바꾸는 AWS CLI·kubectl·helm·docker push는 plan/dry-run 결과 제시 후 사용자 명시 승인 뒤 실행. `terraform destroy`, `-auto-approve`, 기존 리소스의 destroy/replace가 포함된 plan의 apply는 절대 금지 |
| H6 | **비밀정보 보호.** 자격증명·토큰·secret 값·`*.tfstate`를 리포지토리 파일, 보고서, 채팅에 기록·출력 금지. `.gitignore` 필수 |
| H7 | **환경 종속 값 하드코딩 금지.** 계정 ID, ECR URL, 버킷명, ARN, 호스트명, 클러스터명은 변수화. Overlay에 들어 있는 이매지너스 계정 값을 그대로 복사하지 않는다 |
| H8 | **게이트 없이 다음 Phase 진행 금지.** 승인은 채팅에서 사용자가 명시적으로 준 것만 유효 |

---

## 4. 필독 자료 (읽는 순서)

1. 이 문서
2. `이매지너스_history.md` (루트, 없으면 `이매지너스/` 하위)
3. `AI-POC/docs/**`, `AI-POC/infra/**`, `AI-POC/deploy/**`, `AI-POC/k8s/**`
4. `이매지너스/docs/**`, `이매지너스/_inventory/**`, `이매지너스/terraform_infra/**` — 단 `.terraform/`, `.build/`, `*.tfstate*`는 열지 않음. **참조 전용**: 이매지너스 계정용 Terraform이므로 복사하지 않고 패턴·정책 내용만 참고
5. `이매지너스/app`, `이매지너스/frontend`, `이매지너스/k8s`, `이매지너스/deploy`, `이매지너스/tools`

---

## 5. 목표 폴더 구조 (모두 `AI-POC/` 내부)

```
AI-POC/
├─ app/  frontend/  k8s/  deploy/  infra/  docs/     # 기존 + 병합본
├─ .kiro/                                            # D-04 (기본: 여기 또는 루트)
├─ terraform/                                        # Hub 계정 전용 root 모음
│  ├─ bootstrap/                                     # tfstate 버킷 (1회, 로컬 state)
│  └─ hub/
│     ├─ 10-network/                                 # VPC AI-POC import + 기존 EKS data(읽기전용)
│     ├─ 20-eks/                                     # DEV-AI-POC-eks (모듈 호출, customer_name="DEV")
│     └─ 30-app/                                     # 병합본 앱 배포 (kubernetes/helm provider)
├─ terraform-modules/                                # 고객사 배포용 재사용 모듈 (별도 폴더)
│  ├─ modules/
│  │  ├─ network/      # VPC·서브넷·NAT (고객사용. Hub에서는 사용하지 않음)
│  │  ├─ eks/          # 클러스터·OIDC·access entry·로깅·암호화
│  │  ├─ nodegroup/    # CPU/GPU 노드그룹 (map + for_each)
│  │  ├─ irsa/         # IRSA 역할·정책 (Bedrock, S3 등)
│  │  ├─ addons/       # vpc-cni, coredns, kube-proxy, ebs-csi 등
│  │  ├─ registry/     # ECR 리포지토리
│  │  ├─ storage/      # 앱용 S3 버킷 등
│  │  └─ app/          # 클러스터 부트스트랩 컴포넌트 + 앱 배포
│  ├─ customers/
│  │  ├─ _template/    # 10-network / 20-eks / 30-app + tfvars·backend 예시
│  │  └─ README.md
│  └─ tests/           # dummy 고객사 plan-only 검증
└─ docs/
   ├─ PRD_AI-POC_DEV-EKS_Terraform.md   (이 문서)
   ├─ 00_hub_discovery.md
   ├─ 01_change_ledger.md · 01_merge_plan.md · 01_merge_report.md
   ├─ 02_terraform_import_report.md
   ├─ 03_dev_cluster_report.md
   ├─ 04_dev_app_verification.md
   ├─ 05_customer_deployment_guide.md
   ├─ AI-POC_history.md                 (이번 작업의 변경 이력. 이매지너스_history.md와 같은 형식)
   └─ run-log/                          (Phase별 실행 명령 로그)
```

---

## 6. Phase별 작업

각 Phase는 `목표 → 작업 → 산출물 → 게이트` 순으로 기술한다. 명령은 Windows PowerShell 기준.

### Phase 0 — 준비 및 Hub 계정 탐색 (읽기 전용)

**목표**: 로그인 확인, as-is 인벤토리, 변경 원장(ledger) 작성. 이 Phase에서는 AWS에 어떤 쓰기도 하지 않는다.

**작업**

0.1 Git 기준선
- `AI-POC/`에 git이 없으면 `git init`. `.gitignore` 생성(§8.6). 현재 상태를 `baseline` 태그로 커밋.
- Overlay 무결성 스냅샷: `이매지너스/` 전체 파일 해시를 `AI-POC/docs/run-log/overlay_hash_start.txt`에 저장 (작업 종료 시 재계산해 H1 준수 증명).

0.2 자격증명 탐색·프로필 구성
- 워크스페이스 루트와 `AI-POC/`에서 자격증명 후보 탐색: `*.csv`, `credentials`, `*.env`, `*.txt`, `*.json`, `README*` 중 `aws_access_key_id` / SSO start URL 포함 파일.
- **값을 화면·로그·파일에 출력하지 않는다.** AWS CLI named profile `ai-poc-hub`로 구성(또는 SSO 로그인). 이매지너스 계정 자격증명이 함께 있어도 프로필을 만들지 않는다(H4).
- `aws sts get-caller-identity --profile ai-poc-hub` → 계정 ID·ARN을 `00_hub_discovery.md`에 기록.

0.3 리전·대상 확인
- 후보 리전(`ap-northeast-2` 우선, 이어서 `us-west-2`, `us-east-1` 등)에서 `aws ec2 describe-vpcs --filters Name=tag:Name,Values=AI-POC` 및 `aws eks list-clusters` 실행 → VPC `AI-POC`와 클러스터 `AI-POC-eks`가 있는 리전 확정.
- 같은 계정의 다른 VPC 목록은 "제외 대상"으로만 기록.

0.4 인벤토리 수집 (모두 read-only)

| 계층 | 수집 대상 | 명령 예 |
|---|---|---|
| VPC | VPC, 서브넷(AZ·CIDR·태그), 라우트 테이블·연결, IGW, NAT·EIP, SG(소유자 태그 포함), VPC endpoint, NACL, flow log, DHCP options | `aws ec2 describe-*` (`--filters Name=vpc-id,...`) |
| EKS | 클러스터(버전, endpoint access, 로깅, 암호화, auth mode, access entries), 노드그룹별(instance types, ami type, capacity type, disk, scaling, labels, taints, launch template·user data, subnets), add-on·버전, OIDC provider | `aws eks describe-cluster`, `list-nodegroups`, `describe-nodegroup`, `list-addons`, `describe-addon`, `list-access-entries` |
| IAM | 클러스터 역할, 노드 역할, IRSA 역할(OIDC 신뢰 정책 기준), 연결 정책(특히 Bedrock·S3) | `aws iam list-roles`, `get-role`, `list-attached-role-policies`, `get-policy-version` |
| 컨테이너·스토리지 | ECR 리포지토리·최신 태그, 앱이 참조하는 S3 버킷, EBS/EFS, CloudWatch 로그 그룹 | `aws ecr describe-repositories`, `aws s3api list-buckets` |
| 서비스 쿼터 | GPU 인스턴스 쿼터 (Service Quotas → EC2 "Running On-Demand G and VT instances"), EKS·EIP·NAT 쿼터 | `aws service-quotas list-service-quotas --service-code ec2` |
| K8s | 네임스페이스, Deployment/StatefulSet/DaemonSet/CronJob, Service, Ingress, ConfigMap(키), Secret(**이름·키만**), PVC·StorageClass, HPA·PDB·NetworkPolicy, CRD, Helm release, 서비스어카운트 IRSA 어노테이션 | `kubectl get ... -A -o yaml`, `helm list -A` — **기존 클러스터 컨텍스트는 get/describe/logs만 사용** |

- 원본 JSON은 `AI-POC/infra/inventory/hub/`에 저장하되 계정 ID 외 민감 값은 마스킹. `이매지너스/_inventory/`에 인벤토리 도구·형식이 있으면 동일 형식을 재사용.

0.5 Protected 목록 도출
- Base에서 GPU 관련 항목을 전수 식별: 키워드 `gpu`, `nvidia`, `cuda`, `g6e`, `l40s`, `wan`, `device-plugin`, `nodeSelector`/`tolerations`의 GPU 조건, GPU 노드그룹·런치템플릿, GPU 워커 Deployment/Job, 관련 IAM·버킷.
- 파일 경로·리소스(kind/name)·함수 단위로 `00_hub_discovery.md`의 "Protected 목록" 절에 기록.

0.6 변경 원장(ledger) 작성
- `이매지너스_history.md`의 각 항목을 아래 표로 정리 → `01_change_ledger.md`

| ID | 항목 요약 | 분류 | 영향 경로(파일/리소스) | 병합 액션 | 근거(history 위치) |
|---|---|---|---|---|---|
| L-01 | … | ADD / FIX / REMOVE / UNKNOWN | … | APPLY / SKIP / NEEDS_DECISION | … |

- 분류 규칙: 기능·파일·설정 추가 = **ADD**, 버그·설정 오류 수정 = **FIX**, 기능·파일·설정 제거 = **REMOVE**, 판단 불가 = **UNKNOWN**. REMOVE와 Protected 관련 항목은 SKIP.
- `이매지너스/terraform_infra/`에만 존재하는 인프라 수준 추가분(예: Bedrock 접근 정책, Lambda, 추가 버킷)도 ledger에 포함 — 이는 Phase 3에서 **모듈 기능으로 재구현**한다(복사 금지).

**산출물**: `00_hub_discovery.md`, `infra/inventory/hub/*.json`, `01_change_ledger.md`, `run-log/phase0.md`

**게이트**: 계정 ID·리전(D-01, D-02), history 파일 위치(D-03), Protected 목록, ledger 분류 승인

---

### Phase 1 — 앱 오버레이 병합 (Base ← Overlay)

**목표**: `이매지너스/`의 추가·수정만 `AI-POC/`에 반영한 병합본 생성. 파일 시스템 변경만 있으며 AWS 접근 없음.

**작업**

1.1 파일 분류 — Overlay 전체를 순회하여 각 파일을 `NEW`(Overlay에만) / `MODIFIED` / `IDENTICAL` / `BASE_ONLY`(Base에만)로 분류. 비교는 `git diff --no-index --stat` 또는 `Compare-Object`.
- Overlay에서 무시할 경로: `**/__pycache__/**`, `**/.terraform/**`, `**/.build/**`, `**/*.tfstate*`, `**/node_modules/**`, `**/.env*`, `**/*.pem`, `**/*.key`, `terraform_infra/**`(참조 전용), `_inventory/**`(참조 전용)
- 개별 판단 후 결정: `tools/**`, `포스터들/**`, `frontend/public/assets/{brand,login,posters}/**` (D-09)

1.2 파일별 액션 (§7 병합 규칙 적용) → `01_merge_plan.md`에 표로 작성. **적용 전에 사용자 승인(게이트 1-A)**.

1.3 적용 — `AI-POC/`에 브랜치 `merge/imaginus-overlay`를 만들어 작업. Python은 함수·클래스 단위, K8s는 리소스(kind/name) 단위, frontend는 컴포넌트·라우트 단위로 병합(§7.3).

1.4 환경 종속 값 치환 — 병합된 파일에서 12자리 계정 ID, `*.dkr.ecr.*.amazonaws.com`, 버킷명, `arn:aws:*`, 호스트명, 클러스터명을 검출 → Base의 기존 설정 방식(env, ConfigMap, kustomize, helm values 등)에 맞춰 변수화. 치환 목록을 보고서에 기록.

1.5 정합성 검증
- Python: `python -m py_compile` 전 파일, `ruff`/`pytest`가 있으면 실행
- Frontend: `npm ci` → `npm run build`
- K8s: `kubectl apply --dry-run=client -f k8s/` (kustomize/helm이면 `kustomize build` / `helm template`)
- 삭제 0 검증: Base의 파일 집합·K8s 리소스 집합·Python 최상위 심볼 집합이 병합본에 모두 존재하는지 스크립트로 확인
- Protected 무변경 검증: Protected 목록 항목별 Base와 diff = 0

1.6 커밋 및 이력 — 병합 커밋, `AI-POC_history.md`에 항목별 반영 내역 기록.

**산출물**: `01_merge_plan.md`(승인 전), `01_merge_report.md`(파일별 액션·hunk 판정·치환 목록·NEEDS_DECISION 목록), `AI-POC_history.md`, `run-log/phase1.md`

**게이트**: 1-A merge_plan 승인 → 적용 → 1-B merge_report 승인

---

### Phase 2 — Hub 계정 VPC `AI-POC` Terraform import

**목표**: VPC `AI-POC`와 그에 속한 네트워크 리소스를 Terraform 관리 하에 두되, 실제 리소스는 한 개도 변경하지 않는다. 기존 `AI-POC-eks`는 **data source로 읽기만** 한다.

**작업**

2.1 도구 버전 고정 — Terraform ≥ 1.10 (import 블록, S3 native lock). Provider는 `이매지너스/terraform_infra/.terraform.lock.hcl`과 같은 major(aws 6.x, kubernetes 2.x, helm 3.x, random, tls)로 `versions.tf`에 고정.

2.2 `terraform/bootstrap/` — tfstate 버킷 생성 (로컬 state로 1회 apply, **승인 필요 D-05**)
- 버킷명 `ai-poc-tfstate-<account_id>-<region>`, 버저닝·SSE·Public Access Block. DynamoDB 없이 `use_lockfile = true` 사용.

2.3 `terraform/hub/10-network/`
- import 대상: `aws_vpc`, VPC 내 모든 `aws_subnet`, `aws_route_table` + association, `aws_internet_gateway`, `aws_nat_gateway` + `aws_eip`, VPC endpoint, custom NACL/DHCP options/flow log(있으면), **공용 SG**(ALB·bastion 등 클러스터가 소유하지 않은 것).
- import 제외: EKS가 생성·관리하는 SG(`eks-cluster-sg-*`, `aws:eks:cluster-name` 태그), 기존 클러스터 노드그룹 소유 리소스, default SG/NACL/RT. 소유자가 애매하면 NEEDS_DECISION.
- 절차: `imports.tf`에 import 블록 작성 → `terraform plan -generate-config-out=generated.tf` → 생성 코드 정리(ID 하드코딩 제거, 변수·locals·태그 정리, 모듈 스타일 통일) → `terraform plan`이 **No changes**가 될 때까지 반복 → `terraform apply`(import만 수행, 승인 D-05) → 재 plan으로 No changes 재확인.
- import된 모든 리소스에 `lifecycle { prevent_destroy = true }`.
- `existing.tf`: `data "aws_eks_cluster" "ai_poc"`, `data "aws_eks_node_group"` 등 읽기 전용 + `outputs.tf`(vpc_id, subnet id 목록(public/private), 기존 클러스터 버전 등)를 20-eks 입력으로 노출.

2.4 다른 VPC의 리소스 ID가 코드·state에 한 개도 포함되지 않았음을 `terraform state list`로 확인.

**산출물**: `terraform/bootstrap/`, `terraform/hub/10-network/`, `02_terraform_import_report.md`(import 목록, 제외 항목과 사유, plan 요약), `run-log/phase2.md`

**게이트**: 2-A bootstrap apply 승인 → 2-B import apply 승인 → 2-C No changes 확인 보고

---

### Phase 3 — 재사용 모듈 구현 + `DEV-AI-POC-eks` 생성

**목표**: `terraform-modules/modules/*`를 구현하고, 그 첫 소비자로 `terraform/hub/20-eks/`에서 `customer_name = "DEV"`로 DEV 클러스터를 생성한다. 모듈이 실제로 동작함을 DEV로 증명한다.

**작업**

3.1 모듈 구현 (§8 설계 규칙 준수). 순서: `eks` → `nodegroup` → `irsa` → `addons` → `registry` → `storage` → `network`(고객사용, Hub에서는 plan-only 테스트) → `app`(Phase 4).

3.2 DEV 스펙 = Phase 0 인벤토리의 `AI-POC-eks` 미러
- 동일: K8s 버전, endpoint access·CIDR, 로깅 타입, secrets 암호화(KMS 있으면 신규 키), auth mode·access entries(운영자 principal 포함), OIDC, **노드그룹 전부(GPU 노드그룹 포함, H3)** — instance types·ami type·capacity type·disk·labels·taints·scaling·user data, add-on과 버전, IRSA 역할·정책 내용, ledger에서 ADD로 분류된 인프라 추가분(Bedrock 정책 등).
- 다른 점: 이름 prefix `DEV-`(예: `DEV-AI-POC-eks`, `DEV-AI-POC-ng-gpu`), 태그 `Environment=dev`, ECR/S3는 `dev-ai-poc-*`(D-08), 클러스터 SG는 EKS가 새로 생성.
- 서브넷: 같은 VPC의 기존 서브넷 재사용(≥2 AZ). **가용 IP 수를 먼저 확인**하고 부족하면 NEEDS_DECISION. 서브넷에 DEV용 태그 추가가 필요하면 D-13.

3.3 `terraform/hub/20-eks/` — backend(`hub/20-eks.tfstate`), providers, `terraform_remote_state`로 10-network 출력 참조, `module "eks" { source = "../../../terraform-modules/modules/eks" ... customer_name = "DEV" }` 등 호출. 계정 ID 가드(§8.4) 포함.

3.4 `terraform plan` 검토 — 신규 생성(`+`)만 존재하고 기존 리소스의 변경(`~`)·삭제(`-`)·교체(`-/+`)가 0인지 확인. GPU 노드그룹 `desired_size`와 시간당 비용을 명시해 승인 요청(D-06). 승인 후 apply.

3.5 후속 확인
- kubeconfig를 **별도 파일**로 생성: `aws eks update-kubeconfig --name DEV-AI-POC-eks --profile ai-poc-hub --kubeconfig AI-POC/.kube/dev-ai-poc-eks.config` (gitignore). 이후 DEV 작업은 `$env:KUBECONFIG`로 이 파일만 지정.
- `kubectl get nodes -o wide`(GPU 노드 포함 Ready), add-on 상태 ACTIVE, `aws eks describe-cluster` 결과를 인벤토리와 대조.

**산출물**: `terraform-modules/modules/*` (README·variables·outputs 포함), `terraform/hub/20-eks/`, `03_dev_cluster_report.md`, `run-log/phase3.md`

**게이트**: 3-A 모듈 인터페이스(변수·출력) 승인 → 3-B plan 승인(비용 포함) → 3-C 생성 결과 보고 승인

---

### Phase 4 — 병합본 앱 배포 및 동등성 검증 (DEV만)

**목표**: `AI-POC-eks`에 있는 것과 같은 워크로드가 DEV에서 동작한다. 기존 클러스터는 읽기만.

**작업**

4.1 이미지 빌드·푸시 — 병합본으로 컴포넌트별(`app`, `frontend`, GPU 워커 등) `docker build` → ECR(D-08) 푸시. 태그는 git SHA. Windows에서 Docker 사용 가능 여부 확인(D-10).

4.2 클러스터 부트스트랩 컴포넌트 — 인벤토리에 있는 것만 동일하게: NVIDIA device plugin(Protected), metrics-server, ingress/ALB controller, autoscaler(cluster-autoscaler 또는 Karpenter), external-dns, cert-manager, StorageClass 등. `modules/app` 또는 Base의 기존 `deploy/` 방식 중 하나로 통일(D-11).

4.3 앱 배포 — `terraform/hub/30-app/`(kubernetes/helm provider, 클러스터 정보는 remote state·data source로) 또는 `deploy/` 스크립트(D-11). DEV용 값(이미지 URL, IRSA 역할 ARN, 버킷, 호스트명)만 치환. **Secret 값은 파일에 넣지 않는다** — 제공 방식은 D-07.

4.4 검증
- 병합본 `deploy/verify/` 스크립트 실행
- §9.2 동등성 체크리스트 전 항목 확인
- 기능 스모크: 헬스체크, 로그인, Bedrock 콘텐츠 생성 1건(Overlay 추가 기능), GPU 영상 파이프라인 짧은 job 1건(Protected 기능 — GPU 노드 스케줄링·`nvidia.com/gpu` 할당 확인, D-12)
- 실패 항목은 원인·조치·재시도 결과를 기록. 원인이 Base 자체의 문제이면 수정하지 말고 보고(범위 밖일 수 있음)

**산출물**: `terraform/hub/30-app/`(선택 시), `04_dev_app_verification.md`, `run-log/phase4.md`

**게이트**: 4-A 배포 계획(이미지·secret 방식·컴포넌트 목록) 승인 → 4-B 검증 보고 승인

---

### Phase 5 — 고객사 배포 패키징

**목표**: 새 고객사가 들어올 때 폴더 하나 복사 + tfvars 수정으로 배포 가능한 상태.

**작업**

5.1 `terraform-modules/customers/_template/` 완성 — `10-network/`, `20-eks/`, `30-app/` 각 root, `backend.hcl.example`, `terraform.tfvars.example`(모든 변수에 설명·기본값), `README.md`(온보딩 절차).
- 온보딩 절차(가이드에 기술): 고객사 AWS 프로필 생성 → Bedrock 모델 액세스 활성화(수동 전제조건) → GPU 쿼터 확인 → `bootstrap` apply → `_template` 복사 → `customer_name`·`region`·`account_id` 등 설정 → `10-network` → `20-eks` → `30-app` 순으로 init/plan/apply → 검증 스크립트 → 모델 가중치 등 데이터 반입.

5.2 `terraform-modules/tests/` — dummy 고객사(예: `customer_name = "ACME"`)로 `terraform init -backend=false` → `validate` → `plan`(plan-only, apply 금지). plan 출력에서 **모든 리소스명이 `ACME-AI-POC-*` / `acme-ai-poc-*`** 규칙을 따르는지, Hub 전용 값이 섞이지 않았는지 확인.

5.3 품질 — `terraform fmt -check`, `terraform validate`, `tflint`/`checkov`(있으면). 모듈별 README에 inputs/outputs 표(`terraform-docs` 사용 가능 시).

5.4 문서 — `05_customer_deployment_guide.md`, `AI-POC_history.md` 최종 갱신, Overlay 해시 재계산 후 `overlay_hash_start.txt`와 비교 결과 기록.

**산출물**: `customers/_template/`, `tests/`, `05_customer_deployment_guide.md`, 최종 보고

**게이트**: 최종 보고 승인

---

## 7. 병합 규칙 상세 (Phase 1)

### 7.1 파일 단위 액션

| 분류 | 조건 | 액션 |
|---|---|---|
| NEW | Overlay에만 존재 | **COPY** (환경 종속 값 치환 검사 후). 단 Protected 대체 목적의 파일이면 NEEDS_DECISION |
| MODIFIED | 양쪽에 존재, 내용 다름 | **MERGE** — hunk 단위 판정(§7.2). 바이너리는 §7.4 |
| IDENTICAL | 내용 같음 | 없음 |
| BASE_ONLY | Base에만 존재 (Overlay에서 삭제) | **KEEP** (H2) |
| 무시 경로 | §6 Phase 1.1 목록 | SKIP |

### 7.2 Hunk(변경 블록) 단위 판정

| hunk 성격 | 판정 |
|---|---|
| 추가만 (+) | **APPLY** |
| 치환 (−/+) | **APPLY** — FIX로 간주. 단 Protected 대상이면 SKIP |
| 삭제만 (−) | **SKIP** |
| ledger가 REMOVE로 분류한 항목에 속함 | **SKIP** — 추가 hunk라도 REMOVE를 완성하기 위한 부수 변경(예: GPU 옵션 제거 후 UI·라우팅 정리)이면 SKIP |
| 판단 불가 | **NEEDS_DECISION** — 보고서에 hunk 원문 위치와 두 선택지를 적고 사용자 결정까지 보류 |

### 7.3 파일 유형별 병합 방식

- **Python(`app/src`)**: 함수·클래스 단위. Overlay의 새 모듈(예: `roles/*`)은 추가. 수정된 함수는 Overlay 버전 사용(FIX). Base에서만 존재하는 함수·분기(GPU 경로 등)는 유지. import 문은 합집합. `requirements.txt`·`package.json`은 합집합, 버전 충돌 시 상위 버전 채택 후 NEEDS_DECISION으로 표시.
- **K8s 매니페스트(`k8s/`)**: 리소스(kind/name) 단위. 새 리소스 추가, 기존 리소스의 수정 필드 반영, Overlay에서 사라진 리소스(GPU 워커·device plugin 등)는 유지. 이미지·계정 값은 Base의 기존 방식으로 변수화.
- **Frontend**: 컴포넌트·라우트·상태 단위. GPU 영상 기능 UI는 유지하고 Overlay의 Bedrock 콘텐츠 기능 UI를 추가하여 **두 기능이 공존**하도록 내비게이션·출력 타입·설정을 통합. 구조적으로 공존이 불가능한 지점은 NEEDS_DECISION.
- **설정·스크립트(`deploy/`, `infra/`, `docs/`)**: 위와 같은 원칙. `docs/`는 Overlay 문서를 추가하되 Base 문서를 덮어쓰지 않음(동명 파일은 병합 또는 접미사 `_overlay`로 추가).

### 7.4 바이너리·자산

- NEW 이미지·폰트 → COPY. MODIFIED → Overlay 버전으로 교체(FIX), 단 브랜딩·고객사 콘텐츠는 아래 규칙.
- **고객사 고유 자산**(로고, 로그인 이미지, 포스터, 브랜드명 문자열): 병합본에는 자산을 로드하는 **구조·경로·로직**만 반영하고, 자산 자체는 Base 기본값 또는 placeholder 사용. 고객사별 자산은 설정·변수로 주입하도록 설계(D-09). 이매지너스 브랜딩을 기본값으로 굳히지 않는다.

### 7.5 기록 의무

`01_merge_report.md`에 파일별 액션, hunk별 판정과 근거(ledger ID), 치환한 환경 종속 값 목록, NEEDS_DECISION 목록, 검증 결과를 남긴다.

---

## 8. Terraform 설계 규칙

### 8.1 레이어와 상태 분리

| 레이어 | 역할 | Hub | 고객사 |
|---|---|---|---|
| `10-network` | VPC·서브넷·NAT·공용 SG | 기존 VPC import (`modules/network` 미사용) | `modules/network`로 신규 생성 |
| `20-eks` | 클러스터·노드그룹·IRSA·add-on·ECR·S3 | `customer_name="DEV"` | `customer_name="<고객사>"` |
| `30-app` | 부트스트랩 컴포넌트·앱 | DEV 배포 | 고객사 배포 |

- 레이어마다 별도 state(`<customer>/<layer>.tfstate`, Hub는 `hub/<layer>.tfstate`). 하위 레이어는 `terraform_remote_state`로 상위 출력 참조(명시적 변수 입력도 허용).
- kubernetes/helm provider는 `30-app`에서만 사용(plan 시점에 클러스터가 필요한 문제 회피).
- 모듈 간 직접 참조 금지 — 조합은 root에서만 한다.

### 8.2 변수와 네이밍

```hcl
variable "customer_name" {
  type        = string
  description = "고객사 식별자. 모든 리소스명 접두어 (예: DEV, ACME)"
  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9]{1,15}$", var.customer_name))
    error_message = "customer_name: 영문자로 시작, 영숫자 2~16자, 하이픈/밑줄/공백 불가."
  }
}

variable "project_name" {
  type    = string
  default = "AI-POC"
}

variable "environment" { type = string }   # dev | stg | prod
variable "region"      { type = string }
variable "account_id"  { type = string }
variable "aws_profile" { type = string }

locals {
  name_prefix    = "${var.customer_name}-${var.project_name}"   # DEV-AI-POC
  name_prefix_lc = lower(local.name_prefix)                     # dev-ai-poc (ECR/S3 등 소문자 전용)
  common_tags = {
    Customer    = var.customer_name
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Source      = "AI-POC"
  }
}
```

| 리소스 | 이름 규칙 | DEV 예 |
|---|---|---|
| EKS 클러스터 | `${name_prefix}-eks` | `DEV-AI-POC-eks` |
| VPC(고객사) | `${name_prefix}-vpc` | (Hub는 기존 `AI-POC` 사용) |
| 노드그룹 | `${name_prefix}-ng-<key>` | `DEV-AI-POC-ng-gpu` |
| IAM 역할 | `${name_prefix}-<purpose>-role` | `DEV-AI-POC-cluster-role` |
| ECR | `${name_prefix_lc}-<component>` | `dev-ai-poc-app` |
| S3 | `${name_prefix_lc}-<purpose>-${account_id}` | `dev-ai-poc-assets-123456789012` |
| K8s 네임스페이스 | Base와 동일 (고객사별로 클러스터가 다르므로 접두어 불필요) | |

노드그룹은 map 변수 + `for_each`로 정의하고, GPU 노드그룹은 `enable_gpu` 토글로 고객사에서 on/off 가능하게 한다. DEV는 on(H3, Base와 동일 스펙).

### 8.3 Provider와 태그

```hcl
provider "aws" {
  region  = var.region
  profile = var.aws_profile
  # 교차 계정 배포 시 assume_role { role_arn = var.deploy_role_arn } 선택 사용
  default_tags { tags = local.common_tags }
}
```

### 8.4 잘못된 계정 배포 차단 (모든 root에 포함)

```hcl
data "aws_caller_identity" "current" {}

resource "terraform_data" "account_guard" {
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "현재 자격증명 계정이 var.account_id와 다릅니다. 실행을 중단합니다."
    }
  }
}
```

### 8.5 Backend (partial config, `backend.hcl`)

```hcl
terraform {
  backend "s3" {
    bucket       = "ai-poc-tfstate-<ACCOUNT_ID>-<REGION>"
    key          = "hub/10-network.tfstate"      # 고객사: <customer>/<layer>.tfstate
    region       = "<REGION>"
    encrypt      = true
    use_lockfile = true
  }
}
```

고객사는 고객사 계정 안의 state 버킷을 사용한다(격리). 버킷은 `bootstrap/`으로 생성.

### 8.6 `.gitignore` 필수 항목

`.terraform/`, `*.tfstate`, `*.tfstate.*`, `*.tfvars`(`*.tfvars.example` 제외), `backend.hcl`, `.kube/`, `*.pem`, `*.key`, `.env*`, `__pycache__/`, `node_modules/`, `infra/inventory/**/raw/`

### 8.7 금지 옵션

`terraform destroy`, `-auto-approve`, 승인 없는 `state rm/mv/push`, 이유 없는 `-target`, `prevent_destroy` 제거.

---

## 9. 완료 기준 (Acceptance Criteria)

### 9.1 전체

| ID | 기준 | 증명 방법 |
|---|---|---|
| A1 | `AI-POC/` 외 경로 변경 0 | Overlay 해시 스냅샷 시작/종료 비교 일치 |
| A2 | 병합본에서 Base 파일·K8s 리소스·Python 최상위 심볼 삭제 0 | 집합 포함 검증 스크립트 결과 |
| A3 | Protected 항목 Base와 diff 0 | 항목별 diff 결과 |
| A4 | py_compile·frontend build·K8s dry-run 통과 | 로그 |
| A5 | `10-network` import 후 plan = No changes, 다른 VPC 리소스 ID 미포함 | plan 출력, `state list` |
| A6 | `20-eks` plan에 기존 리소스 변경·삭제 0; DEV 클러스터 ACTIVE, 노드(GPU 포함) Ready, add-on ACTIVE | plan 요약, `describe-cluster`, `get nodes` |
| A7 | §9.2 체크리스트 전 항목 통과 또는 사유 기록 | `04_dev_app_verification.md` |
| A8 | dummy 고객사 plan에서 리소스명 전부 prefix 규칙 준수, `fmt -check`·`validate` 통과 | plan 출력, 명령 결과 |
| A9 | 비밀정보 스캔 통과 (`gitleaks`/`trufflehog` 있으면 사용, 없으면 정규식: `AKIA[0-9A-Z]{16}`, `-----BEGIN`, 12자리 계정 ID 하드코딩) | 스캔 결과 |
| A10 | §5의 문서 산출물 전부 존재, `AI-POC_history.md` 최신 | 파일 목록 |

### 9.2 DEV ↔ AI-POC-eks 동등성 체크리스트

| 항목 | 확인 방법 |
|---|---|
| K8s 버전 | `kubectl version` |
| 노드그룹 수·instance type·AMI·labels·taints·scaling | `aws eks describe-nodegroup` |
| GPU 노드 `nvidia.com/gpu` allocatable > 0 | `kubectl describe node` |
| add-on 목록·버전 | `aws eks list-addons` / `describe-addon` |
| 네임스페이스 집합 | `kubectl get ns` |
| Deployment/StatefulSet/DaemonSet/CronJob 집합, replicas Ready | `kubectl get all -A` |
| Service/Ingress/LB, 외부 접근 | `curl` 헬스체크 |
| ConfigMap 키 집합, Secret 이름·키 집합(값 제외) | `kubectl get cm/secret -o json` (키만 추출) |
| PVC·StorageClass | `kubectl get pvc,sc -A` |
| 서비스어카운트 IRSA 어노테이션 → 역할 존재·정책 동일 | `kubectl get sa`, `aws iam get-role` |
| HPA·PDB·NetworkPolicy | `kubectl get hpa,pdb,netpol -A` |
| Helm release 목록 | `helm list -A` |
| 앱 기능 스모크(Bedrock 1건, GPU job 1건) | 로그·결과물 |

---

## 10. 운영 규칙

### 10.1 명령 분류

| 분류 | 예 | 규칙 |
|---|---|---|
| 읽기 (자유) | `aws describe-*/list-*/get-*`, `kubectl get/describe/logs`, `terraform init/validate/fmt/plan`, `git status/diff/log` | 사전 승인 불필요. run-log 기록 |
| 쓰기 — DEV·AI-POC 폴더 (승인) | `terraform apply`(plan 제시 후), DEV 컨텍스트 `kubectl apply/delete/patch/scale/rollout`, DEV `helm install/upgrade`, `docker push`, `git commit`(폴더 내) | plan/dry-run + 사용자 승인 후 실행 |
| 금지 | `terraform destroy`, `-auto-approve`, 기존 `AI-POC-eks` 컨텍스트에서의 쓰기 동사, `eksctl`, 다른 VPC·계정 대상 쓰기, 승인 없는 `aws ... delete-*`, `이매지너스/` 쓰기, 비밀 값 출력 | 어떤 요청에도 실행하지 않음 |

### 10.2 로그와 보고

- 실행한 모든 명령을 `docs/run-log/phase<N>.md`에 `시각 · 명령 · 목적 · 결과 요약`으로 기록(비밀 값 제외).
- Phase 보고서는 `수행 내용 / 결과 / 발견한 차이·이슈 / NEEDS_DECISION 목록 / 다음 Phase 계획` 5개 절로 작성.

### 10.3 질문 형식

```
[NEEDS_DECISION] D-xx: <제목>
상황: <사실 요약, 근거 파일·명령>
선택지: A) … / B) … / C) …
권장: <선택지와 이유>
보류 범위: <결정 전까지 멈추는 작업 / 계속 가능한 작업>
```

### 10.4 실행 환경 주의

- Windows PowerShell, 경로에 한글·공백 포함 → 항상 따옴표로 감싼다. 파일은 UTF-8(BOM 없음)로 저장.
- OneDrive 동기화 폴더에서 `.terraform/`·`node_modules/`가 잠금·지연을 일으킬 수 있음 → 문제가 생기면 `TF_DATA_DIR`을 동기화 밖 경로로 지정하고 보고.
- kubeconfig는 클러스터별 파일로 분리하고 `KUBECONFIG` 환경변수로 명시. 기존 클러스터 컨텍스트는 읽기 전용으로만 사용.

---

## 11. 사용자 결정 요청 목록 (해당 시점 전에 확인)

| ID | 시점 | 질문 | 기본값(미응답 시 제안) |
|---|---|---|---|
| D-01 | Phase 0 | 폴더 내 자격증명의 계정이 "운영 계정"과 동일한가? (계정 ID 제시 후 확인) | 확인 전 진행 금지 |
| D-02 | Phase 0 | 리전 확정 (탐색 결과 제시) | 탐색된 리전 |
| D-03 | Phase 0 | `이매지너스_history.md` 위치·최신본 | 루트 파일 |
| D-04 | 시작 전 | Kiro spec·steering 파일(`.kiro/`) 위치 — 워크스페이스 루트(H1의 유일한 예외) vs `AI-POC/.kiro/` | 루트 워크스페이스로 열고 `.kiro/`만 루트 예외 허용 |
| D-05 | Phase 2 | tfstate 버킷 생성 승인, import apply 승인 | 버킷 생성 후 S3 backend |
| D-06 | Phase 3 | GPU 노드그룹 `desired_size`·비용 승인 (Base 동일 스펙 기준) | Base 동일 |
| D-07 | Phase 4 | Secret 값 제공 방식 — AWS Secrets Manager + External Secrets / SSM Parameter Store / 운영자 수동 `kubectl create secret` | Secrets Manager + External Secrets(Base가 이미 쓰는 방식이 있으면 그것) |
| D-08 | Phase 3~4 | ECR·S3 — 신규 `dev-ai-poc-*` 생성(모듈 검증) vs 기존 리포·버킷 공유(태그·경로 분리). 모델 가중치 버킷은 DEV에서 읽기 전용 공유 가능 여부 | 신규 생성, 모델 가중치는 공유 읽기 |
| D-09 | Phase 1 | `포스터들/`, `tools/`, `frontend/public/assets/{brand,login,posters}` 처리 — 고객사 자산 분리 여부 | 구조만 반영, 자산은 설정 주입 |
| D-10 | Phase 4 | 이미지 빌드 환경 — 로컬 Docker Desktop / CodeBuild / 기존 CI | 로컬 Docker 가능하면 로컬 |
| D-11 | Phase 4 | 앱 배포 방식 — `30-app` Terraform(kubernetes/helm) vs Base의 기존 `deploy/` 스크립트 | Base 기존 방식 유지, 모듈 `app`은 부트스트랩 컴포넌트 담당 |
| D-12 | Phase 4 | GPU 스모크 job 실행 허용(비용 발생) | 최소 길이 1건 |
| D-13 | Phase 3 | 기존 서브넷에 DEV용 태그(`kubernetes.io/cluster/DEV-AI-POC-eks=shared` 등) 추가 필요 시 승인 | 필요할 때만, 태그 추가에 한정 |

---

## 12. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 서브넷 가용 IP 부족으로 DEV 노드·Pod 배치 실패 | Phase 3.2에서 사전 확인, 부족 시 NEEDS_DECISION(서브넷 추가는 별도 승인) |
| GPU 인스턴스 쿼터·가용 용량 부족 | Phase 0에서 쿼터 확인, 부족 시 증량 요청 안내 |
| import 생성 코드의 노이즈·drift | `-generate-config-out` 결과를 정리하고 No changes 확인 후에만 apply |
| Overlay의 이매지너스 계정 값 오염 | Phase 1.4 치환 + A9 스캔 |
| GPU 기능(Base)과 Bedrock 전용 UI(Overlay)의 구조 충돌 | 공존 통합을 기본으로, 불가 지점은 NEEDS_DECISION |
| add-on·AMI와 K8s 버전 호환성 | 인벤토리와 동일 버전 우선, 불가 시 호환 최신 버전 후 보고 |
| ECR·S3 소문자 규칙 위반 | `name_prefix_lc` 사용 |
| kubectl 컨텍스트 실수로 기존 클러스터 변경 | kubeconfig 파일 분리, 기존 컨텍스트 읽기 전용 원칙(§10.4) |
| 비용(g6e.24xlarge 등) | D-06·D-12 승인, 검증 후 `desired_size` 축소 여부 별도 제안 |
| 잘못된 계정에 apply | §8.4 계정 가드 |

---

## 부록 A. Kiro steering 파일 초안 (`.kiro/steering/guardrails.md`)

```markdown
---
inclusion: always
---
# AI-POC 작업 가드레일
- 쓰기 허용 경로: AI-POC/** 만. 이매지너스/**, 이매지너스_history.md, 루트 파일은 읽기 전용.
- 병합은 추가·수정만 반영, 삭제는 반영하지 않는다. GPU 관련은 AI-POC 원본 우선.
- AWS 범위: Hub 계정 + VPC AI-POC 내부. 기존 AI-POC-eks 쓰기 금지. 다른 VPC·계정 금지.
- 인프라 변경은 Terraform으로만. apply/파괴적 명령은 plan 제시 후 사용자 승인. destroy·-auto-approve 금지.
- 자격증명·secret 값·tfstate를 파일·보고서·채팅에 기록하지 않는다.
- 계정 ID·ECR URL·버킷·ARN·호스트명은 변수화. 이매지너스 계정 값 복사 금지.
- Phase 게이트 승인 없이 다음 Phase 진행 금지. 불확실하면 [NEEDS_DECISION] 형식으로 질문.
- 기준 문서: AI-POC/docs/PRD_AI-POC_DEV-EKS_Terraform.md
```

## 부록 B. Spec 생성 매핑

| Kiro spec 파일 | 이 문서의 출처 |
|---|---|
| `requirements.md` | §2 목표, §3 절대 규칙, §9 완료 기준 (각 항목을 사용자 스토리 + 수용 기준으로 변환) |
| `design.md` | §5 폴더 구조, §7 병합 규칙, §8 Terraform 설계, §12 리스크 |
| `tasks.md` | §6 Phase 0~5의 작업 번호(0.1, 1.1, …)를 그대로 체크리스트로, 각 게이트를 "사용자 승인 대기" 태스크로 삽입 |
