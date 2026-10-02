# Phase 2 실행 로그 — Terraform 기반 구성

대상 계정 `${ACCOUNT_ID}` (= `730335451955`) · 리전 `ap-northeast-2` · 프로필 `ai-poc-hub`
표기: 시각 · 명령 · 목적 · 결과 요약

---

## 2-A. bootstrap (원격 state 저장소)

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 19:0x | `terraform init` | provider 확보 | 성공. `hashicorp/aws v6.63.0`, `hashicorp/terraform` 내장. `TF_DATA_DIR`을 root별로 분리(`%LOCALAPPDATA%\terraform-data\ai-poc\bootstrap`) — 전역 단일 경로는 root 간 충돌 발생 |
| 19:1x | `terraform plan -out=tfplan.bootstrap` | 생성 예정 항목 확인 | `8 to add, 0 to change, 0 to destroy` |
| 19:45 | `python assert_plan_safe.py tfplan.bootstrap` | **apply 전 안전 증명** — 사용자 조건 "ArgoCD·GitLab 이 없어지지 않고 설정이 변경되지 않을 것" 을 기계적으로 검사 | 3개 검사 전부 PASS (아래 상세) |
| 19:5x | `python snap_argocd.py argocd_before.json` | apply 전 ArgoCD·GitLab 상태 스냅샷 | 2개 클러스터 · access entry 10 · Pod Identity 5 · IAM 역할 1 수집 |
| 19:5x | `terraform apply tfplan.bootstrap` | state 버킷 생성 | **`Apply complete! 8 added, 0 changed, 0 destroyed`** |
| 19:5x | `aws s3api get-bucket-{versioning,encryption}` / `get-public-access-block` | 버킷 설정 실측 | versioning `Enabled` · SSE `AES256` + BucketKey · public access 4항목 전부 `true` |
| 19:52 | `python snap_argocd.py argocd_after.json` → `diff_argocd.py` | apply 전후 비교 | **차이 0건** |

### apply 전 안전 증명 결과

`terraform show -json tfplan.bootstrap` 을 파싱해 세 가지를 확인했다.

```
리소스 변경 항목 : 8
액션 집계
  create               8
리소스 타입
  aws_s3_bucket                                        1
  aws_s3_bucket_lifecycle_configuration                1
  aws_s3_bucket_ownership_controls                     1
  aws_s3_bucket_policy                                 1
  aws_s3_bucket_public_access_block                    1
  aws_s3_bucket_server_side_encryption_configuration   1
  aws_s3_bucket_versioning                             1
  terraform_data                                       1

[PASS] 모든 액션이 create — update/delete/replace 0건
[PASS] EKS·IAM·EC2·ELB·VPC 리소스 0건 (S3 와 terraform_data 뿐)
[PASS] plan 어디에도 argocd/gitlab/dev-ai-poc/ai-poc-eks/podidentity 없음
```

### apply 후 무변경 증명 결과

비교 항목: 두 클러스터의 `accessConfig`(authenticationMode 포함) · 엔드포인트 공개/비공개 플래그 ·
status · version · access entry 목록과 **각 entry 의 연결 정책** · Pod Identity 연결(namespace/serviceAccount) ·
애드온 목록 · 노드그룹 목록, 그리고 `ArgoCD-Dev-Cluster-Access` IAM 역할의 신뢰정책·연결정책.

```
[PASS] 차이 0건 — 모든 항목이 apply 전과 동일

[AI-POC-eks] status=ACTIVE  podIdentity=4건
    argocd/argocd-application-controller  <-- ArgoCD/GitLab
    argocd/argocd-server                  <-- ArgoCD/GitLab
    gitlab-runner/gitlab-runner           <-- ArgoCD/GitLab
    kube-system/aws-load-balancer-controller
[Dev-AI-POC-eks] status=ACTIVE  podIdentity=1건
    kube-system/aws-load-balancer-controller
```

### 산출물

- state 버킷 `ai-poc-tfstate-${ACCOUNT_ID}-${REGION}` — versioning · SSE-S3(BucketKey) · public access 전면 차단 · 버킷 정책(비-TLS 거부) · lifecycle(구버전 정리)
- `backend_hcl` output — 이후 레이어가 `key = "hub/<layer>.tfstate"` 로 참조. 잠금은 `use_lockfile = true`(S3 조건부 쓰기, DynamoDB 테이블 불필요)
- bootstrap 자체 state 는 **로컬 유지**(순환 의존 회피). `.gitignore` 로 추적 제외

### 부수 조치

`.gitignore` 의 Terraform 절에 `tfplan` / `tfplan.*` 추가. 기존 패턴 `*.tfplan` 은
`tfplan.bootstrap` 같은 `-out=tfplan.<layer>` 형태를 잡지 못했다. 저장된 plan 은 리소스 속성 전체를 담는다.

---

## 발견 — 운영 클러스터에 팀원 작업물이 올라와 있다

Phase 0 시점(12:22) 조회에서 `AI-POC-eks` 는 노드 0 · 워크로드 0 이었다. 지금 실측은 다르다.

| 항목 | Phase 0 기록 | 현재 실측 |
|---|---|---|
| 노드그룹 | 0 | **1** |
| access entry | 4 | **5** |
| Pod Identity 연결 | 4 | 4 (변동 없음) |

`argocd/argocd-server` · `argocd/argocd-application-controller` · `gitlab-runner/gitlab-runner` 연결이
존재한다. 즉 **ArgoCD 본체와 GitLab Runner 는 운영 클러스터에서 동작하고**,
`Dev-AI-POC-eks` 쪽 `ArgoCD-Dev-Cluster-Access` 역할(access entry + `AmazonEKSClusterAdminPolicy`)은
그 ArgoCD 가 개발 클러스터로 배포하기 위한 **크로스 클러스터 접근 경로**다.

이 사실은 개정 R1(H4 완화, "운영 클러스터 재생성 허용")의 전제와 어긋난다.
사용자가 R1 을 승인한 근거는 "지금은 아무도 사용하지 않는 완전 초기 구축 단계" 였다.

→ `[NEEDS_DECISION] D-20` 으로 올린다. 이 결정 전까지 `AI-POC-eks` 에 대한 쓰기 작업은 보류한다.

또한 Phase 0 보고서의 다음 항목을 정정해야 한다.

- 클러스터 수: 2개 → **3개** (`AI-POC-eks` · `Dev-AI-POC-eks` · `eks-metaclouds`(범위 밖))
- `AI-POC-eks` 는 빈 클러스터가 아니다 (위 표)
- F12/D-13 무효 — 서브넷의 `kubernetes.io/role/*` 태그는 이미 존재한다. 없는 것은 `kubernetes.io/cluster/*` 뿐

---

## 다음 (2-B)

`terraform/hub/10-network/` — VPC 1 · 서브넷 9 · RT 7 + assoc 9 · IGW 1 · NAT 1 · EIP 1 ·
DHCP 1 · SG 1(`AI-POC-bastion-sg`) import.

원칙:
- `import` 블록으로 선언 → `plan` 이 **`No changes`** 가 될 때까지 코드를 실물에 맞춘다
- `default_tags` 미사용 (기존 태그에 drift 유발)
- 전 리소스 `lifecycle { prevent_destroy = true }`
- EKS 클러스터·SG 는 `data` source 로만 참조 (`existing.tf`), 관리하지 않는다
- `plan` 에 `destroy` 나 `-/+` 가 하나라도 있으면 **apply 하지 않고 보고**

---

## 부록 · GPU 노드 생존 확인 (읽기 전용, 2026-09-09)

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 13:02 | `aws eks list-clusters` | 클러스터 목록 | 3개 (`AI-POC-eks` · `Dev-AI-POC-eks` · `eks-metaclouds`(범위 밖)) |
| 13:02 | `aws ec2 describe-instances --filters vpc-id=<AI-poc-vpc>` | VPC 내 인스턴스 전수 | 4대 전부 running, 전부 CPU: bastion t3.medium · Metanet-GitLab t3.large(범위 밖) · `Dev-AI-POC-eks` t3.medium · `AI-POC-eks` t3.medium |
| 13:02 | `aws eks list-nodegroups` (양쪽) | 노드그룹 수 | 각 1개 (`AI-POC-eks-ng`, `Dev-AI-POC-eks-ng`). **GPU 노드그룹 없음** |
| 13:02 | `aws eks describe-nodegroup` (양쪽) | 타입·AMI·taint | 양쪽 `t3.medium` / `AL2023_x86_64_STANDARD` / taint 없음 / min1·desired1·max2 |
| 13:02 | `aws ec2 describe-instances --filters instance-type=g4dn.*,g5.*,g6.*,p3.*,p4d.*,p5.*,inf*` | 리전 전체 GPU 인스턴스 | **0건** (stopped 포함 0) |
| 13:02 | `aws autoscaling describe-auto-scaling-groups` | ASG 확인 | 3개 모두 desired 1 / 인스턴스 1. GPU ASG 없음 |
| 13:02 | `aws service-quotas get-service-quota ec2 L-DB2E81BA` | G·VT On-Demand 쿼터 | **768 vCPU** — Hub 계정은 GPU 쿼터가 열려 있다 |

### 결론

**GPU 노드는 살아있지 않다. 애초에 생성된 적이 없다.** D-14/L-30의 `modules/nodegroup` GPU 구현이 아직 적용되지 않은 상태이고, 결정대로라면 적용 후에도 `desired_size = 0`이라 노드는 뜨지 않는다.

### 원장 정정 후보

`01_change_ledger.md` L-30은 "Overlay 계정 GPU 쿼터 0으로 동작 미확인"이라고 적혀 있다. **Hub 계정(`730335451955`)은 G·VT On-Demand 쿼터가 768 vCPU**이므로 `g4dn.xlarge`(4 vCPU) 기동에 제약이 없다. 즉 GPU 미검증의 원인이 Hub에서는 쿼터가 아니라 **추론 서버 자산 부재**(decisions §C-3)뿐이다. 향후 D-12 승인 시 `desired`를 1로 올리는 검증은 쿼터 측면에서 실행 가능하다.

---

## 2-A 부록. 원본 계정 조사 (읽기 전용) 와 GPU 방침 개정

사용자 안내: "`default` 로그인 계정이 기존이고 `통합계정.json`이 현재다.
필요한 내용과 리소스는 `default` 계정에서 가져오면 된다."

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 20:0x | `aws sts get-caller-identity` × 2 프로필 | 신원 확정 | `default` → `${ORIGIN_ACCOUNT_ID}` `user/kiro-tester` · `ai-poc-hub` → `${ACCOUNT_ID}` `user/kiro-iam` |
| 20:0x | `inspect_creds.py 통합계정.json` | 자격증명 구조 파악 (**값 미출력**) | `accounts_list` 1건, `access_name = kiro-iam-mtx`. 원본 계정 키는 없음 |
| 20:1x | `survey_origin.py` (EKS·EC2·ECR·S3·Neptune·SageMaker × 2리전) | 이관 대상 파악 | 인벤토리 JSON 17개 → `infra/inventory/origin/` |
| 20:1x | `service-quotas get-service-quota L-DB2E81BA` × 2 계정 | GPU 쿼터 | 양쪽 **768 vCPU** — 쿼터는 제약이 아니다 |
| 20:2x | `ssm_run2.py default <원본bastion> survey_origin_k8s.sh` | 원본 배포 구성 | namespace `toon-change-ai-pj`, 워크로드 3 + Job 1 |
| 20:2x | `ssm_run2.py ... dump_origin_specs.sh` | 상세 스펙 | Deployment·Service·Job·PVC·SC·ConfigMap·DaemonSet 확보 |
| 20:3x | `pricing get-products` (서울, g6e/g7e) | 비용 근거 | `g6e.4xlarge` $3.694/h · `g6e.12xlarge` $12.900/h · `g6e.24xlarge` $18.522/h · `g7e.4xlarge` $4.916/h |

원본 계정에는 `describe`/`list`/`get`만 사용했다. **쓰기 0건.**

### 핵심 발견

**원본 서버도 꺼져 있다.** `quick-test-eks`의 노드그룹 5개 전부 `desired=0`이고
파드 13개가 4일 19시간째 `Pending`이다. 실행 중 EC2는 bastion 1대뿐.
따라서 "서버를 기동시켜"를 원본에서 해결할 수는 없다.

**GPU는 원본 계정에만 있었다.** 계정 `${ACCOUNT_ID}`에는 GPU 인스턴스가 0개이고
두 노드그룹 모두 `t3.medium`/`AL2023_x86_64_STANDARD`다. 사용자가 인식한
"GPU가 있는 AI-POC"는 앱 **코드**(`wan_client.py` 등)를 말한 것이고, GPU **하드웨어**는
원본 계정 `quick-test-eks`의 노드그룹 4개였다.

### 개정 R3 — GPU 제외, 신규 설계

사용자 결정: "지금까지 사용했던 GPU nodegroup은 그냥 제외하고 아예 '신규' 기능으로
아예 추가해야겠어. 아직 안 만들었다면 그대로 둬."

**조치 0건.** GPU 자산을 만든 적이 없어 그대로 두었다. 확인 결과:

| 확인 | 결과 |
|---|---|
| `AI-POC/terraform/` | `bootstrap/` 8파일뿐. `gpu`·`g6e`·`g7e`·`nvidia`·`node_group` 문자열 **0건** |
| `${ACCOUNT_ID}` GPU 인스턴스 | **0개** |
| `AI-POC-eks-ng` / `Dev-AI-POC-eks-ng` | 양쪽 `t3.medium` / `AL2023_x86_64_STANDARD` |

원본 GPU 노드그룹 4개(`g6e.4xlarge`·`g6e.12xlarge`·`g6e.24xlarge`·`g7e.4xlarge`)와
그에 딸린 `video-gen-wan22-i2v`·`nvidia-device-plugin`·200Gi PVC·모델 다운로드 Job은
**복제하지 않는다.** 스펙은 `decisions.md` §H-2에 기록만 남겼다.

이매지너스 Terraform의 `g4dn.xlarge` 스펙 재구현(기존 §C-3) 역시 무효다.

**Phase 2-B·2-C·Phase 3(CPU 노드그룹·애드온)은 GPU와 무관하므로 영향 없다.**
GPU는 별도 트랙으로 분리한다.

### 추가 조회 · 기존 `Dev-AI-POC-eks` 실태 (13:10, 읽기 전용)

| 항목 | 실측값 | 확정 설계와 비교 |
|---|---|---|
| 생성 시각 | 2026-09-08 12:44 (KST) | — |
| 태그 | `Environment=dev`, `Name=Dev-AI-POC-eks` 뿐 | **Terraform 관리 태그 없음 → 수동 생성** |
| K8s 버전 | 1.36 | 일치 |
| 엔드포인트 | **퍼블릭 false / 프라이빗 true** | **D-15 불일치.** D-15는 DEV를 퍼블릭+프라이빗으로 확정 |
| 인증 모드 | `API_AND_CONFIG_MAP` | Access Entry 사용 가능 |
| 클러스터 서브넷 | 6개 | — |
| 노드 역할 | `Dev-AI-POC-eks-node-role` | 설계 명명 규칙(`DEV-AI-POC-node-role`)과 다름 |
| 노드그룹 | `Dev-AI-POC-eks-ng` (t3.medium ×1) | GPU 노드그룹 없음 |

**시사점.** 기존 `Dev-AI-POC-eks`는 Terraform 밖에서 수동 생성되었고 엔드포인트 정책이 D-15와 어긋난다. 여기에 GPU 노드그룹만 붙이면 관리 주체가 갈리는 하이브리드 상태가 된다. Phase 3의 `terraform/hub/20-eks/`로 신규 생성하는 편이 설계와 일치한다. 단 명명 충돌(`DEV-AI-POC-eks` ↔ 기존 `Dev-AI-POC-eks`, 대소문자만 다름)과 기존 클러스터 처리 방침이 선결 과제다.
