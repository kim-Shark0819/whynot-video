# 00 · Hub 계정 탐색 결과 (Phase 0)

| 항목 | 내용 |
|---|---|
| 근거 문서 | `docs/PRD_AI-POC_DEV-EKS_Terraform.md` §6 Phase 0 |
| spec | `.kiro/specs/ai-poc-dev-eks-terraform/` (requirements R2, tasks 0.x) |
| 수집 일시 | 2026-09-08 12:40 ~ 13:20 KST |
| 수집 방식 | **읽기 전용만.** `describe-*` / `list-*` / `get-*` / `kubectl get` — AWS 쓰기 0건 |
| 원본 데이터 | `AI-POC/infra/inventory/hub/` (JSON 53개 + K8s 텍스트 2개) |
| 실행 로그 | `docs/run-log/phase0.md` |

---

## 1. 계정 · 자격증명 (D-01)

| 항목 | 값 |
|---|---|
| 계정 ID | **730335451955** |
| 호출자 ARN | `arn:aws:iam::730335451955:user/kiro-iam` |
| UserId | `AIDA2UC3CTMZ3BRUHJA74` |
| AWS CLI 프로필 | `ai-poc-hub` (region `ap-northeast-2`, output json) |
| 자격증명 출처 | 워크스페이스 루트 `통합계정.json` → `accounts_list[]` 중 `access_name = kiro-iam-mtx` |

사용자가 사전에 제시한 계정 ID와 일치한다.

### 1.1 "운영 계정"과의 관계 (D-01 보충)

PRD는 Hub 계정이 "운영 계정과 동일한 것으로 추정"한다고 적었으나, `AI-POC/README.md`에 따르면 원본은 **다른 계정**이다.

| 역할 | 계정 | 접두사 / 클러스터 |
|---|---|---|
| 원본 (이관 출발점) | `061051247564` | `toon-change-ai-pj` / `quick-test-eks` |
| **Hub (이번 작업 대상)** | **`730335451955`** | `ai-poc` / `AI-POC-eks` |
| Overlay (범위 밖, H4) | `726990466389` | `story-ai` / `story-ai-eks` |

즉 Hub는 "운영 계정"이 아니라 **원본에서 이관받은 대상 계정**이다.

### 1.2 자격증명 후보 파일 (값은 기록하지 않음)

| 경로 | 내용 | 처리 |
|---|---|---|
| `통합계정.json` | Hub 계정 액세스 키 1건 (`kiro-iam-mtx`) | **사용** |
| `이매지너스/이매지너스.json` | Overlay 계정 자격증명 | **사용 금지** (H4). 열지 않았다 |
| `이매지너스/terraform_infra/terraform.tfstate(.backup)` | 관리자 비밀번호·`token-secret` 평문 포함 (history §10 자체 경고) | **열지 않았다** (PRD §4) |

문자열 `access_key` / `secret_key` 를 포함한 나머지 파일(`*/deploy/preflight.ps1`, `PRD*.md`, `terraform_infra/*.ps1`, `kubernetes.tf`)은 **코드 내 키 이름**일 뿐 자격증명 저장소가 아니다.

**부수 관찰**: 워크스페이스 루트에 `와이낫미디어/`, `와이랩/`, `퍼스트맨 스튜디오/` 형제 폴더가 있고 각각 `deploy/preflight.ps1`을 갖고 있다. 다른 고객사 배포본으로 보인다. 범위 밖이라 열지 않았으나 Phase 5(고객사 패키징)에서 참고할 선례가 될 수 있다.

---

## 2. 리전 · 대상 확정 (D-02, D-18)

| 항목 | 값 |
|---|---|
| 리전 | **ap-northeast-2** (대상 VPC·클러스터가 이 리전에만 존재) |
| 대상 VPC | **`vpc-0de9ff1693bd56f38`** |
| 대상 VPC Name 태그 | **`AI-poc-vpc`** |
| 대상 클러스터 | `AI-POC-eks` |

> **PRD 가정과의 차이 (F1)**: PRD는 VPC 이름을 `AI-POC`로 적었으나 실제 Name 태그는 `AI-poc-vpc`다. `describe-vpcs --filters Name=tag:Name,Values=AI-POC`는 0건을 돌려준다. **태그를 변경하지 않고** 실제 값을 정식 표기로 사용할 것을 권고한다 (D-18).

### 2.1 제외 대상 (작업 범위 밖 · 참조·변경 금지)

| 종류 | 대상 | 사유 |
|---|---|---|
| VPC | `vpc-06a99f0ed6f4ba832` (`Grafana-vpc`, `10.1.0.0/16`) | 대상 VPC 아님 |
| EKS | `eks-metaclouds` (v1.34, `Grafana-vpc` 소재) | 다른 VPC |
| EC2 | `Metanet-GitLab` (t3.large, 대상 VPC 내) | 범위 밖 워크로드 |
| ALB | `Main-ALB`, `k8s-default-grafanaa-4005cdfda3` (`Grafana-vpc`) | 다른 VPC |
| ALB | `AI-poc-ALB` (대상 VPC 내) | GitLab 프론트 — §3.4 참조 |
| ECR | `metaclouds-backend`, `metaclouds-frontend`, `metaclouds-collector` | 다른 프로젝트 |
| S3 | `aws-cloudtrail-logs-*`, `aws-quicksetup-*`(2), `castai-logo-public`, `config-bucket-*`, `grafana-data-mtp-01`, `s3-goodrich-scheduler`, `s3-goodrich-scheduler-test` | 다른 프로젝트 |
| ACM | `*.meta-clouds.com` (`certificate/9fd7fdac-…`) | metaclouds용 |
| Scheduler | `grafana-event` (default 그룹) | 다른 프로젝트 |
| IAM OIDC | `…/id/B5648DE04DEC92B289495A0310392530` | `eks-metaclouds`용 |

---

## 3. 인벤토리

### 3.1 VPC

| 항목 | 값 |
|---|---|
| VPC | `vpc-0de9ff1693bd56f38` / `AI-poc-vpc` / `100.10.0.0/20` / tenancy default |
| 서브넷 | 9개 (3 AZ) — 전부 `MapPublicIpOnLaunch=false` |
| IGW | `igw-0f123941983c92a33` |
| NAT | `nat-0720e9291b442e603` (public1 소재, 단일 AZ) |
| NAT EIP | `eipalloc-0d00ce1428b52c74a` / `13.125.117.242` / Name `AI-POC-eip-ap-northeast-2a` |
| VPC endpoint | **0개** |
| Flow log | **0개** |
| NACL | 1개 (`acl-0655dbb96428fa9dc`, default, 9개 서브넷 연결) |
| DHCP options | 계정 기본 |

#### 서브넷

| 논리 이름 | Subnet ID | AZ | CIDR | 가용 IP |
|---|---|---|---|---|
| `AI-POC-subnet-public1-ap-northeast-2a` | `subnet-0b8b4832085705b56` | 2a | 100.10.0.0/24 | 248 |
| `AI-POC-subnet-public2-ap-northeast-2b` | `subnet-07db8faf09bcfcdbb` | 2b | 100.10.3.0/24 | 249 |
| `AI-POC-subnet-public3-ap-northeast-2c` | `subnet-053f2f798359b197a` | 2c | 100.10.6.0/24 | 250 |
| `AI-POC-subnet-private1-ap-northeast-2a` | `subnet-01e0e91f3c43b57f2` | 2a | 100.10.1.0/24 | 250 |
| `AI-POC-subnet-private2-ap-northeast-2b` | `subnet-071250e072cc06c64` | 2b | 100.10.4.0/24 | 251 |
| `AI-POC-subnet-private3-ap-northeast-2c` | `subnet-0ac1a0b3cf1fb3c1f` | 2c | 100.10.7.0/24 | 251 |
| `AI-POC-subnet-private4-ap-northeast-2a` | `subnet-0d290d58f65b8af9b` | 2a | 100.10.2.0/24 | 250 |
| `AI-POC-subnet-private5-ap-northeast-2b` | `subnet-088ee9cce28b857ec` | 2b | 100.10.5.0/24 | 251 |
| `AI-POC-subnet-private6-ap-northeast-2c` | `subnet-093122a3f02f9f381` | 2c | 100.10.8.0/24 | 251 |

`config.env`가 참조하는 것은 public1/2/3과 private1/2/3이다. private4/5/6은 미사용 여유분이다.

**서브넷 태그**: `kubernetes.io/role/elb`, `kubernetes.io/role/internal-elb`, `kubernetes.io/cluster/*` 태그가 **9개 모두에 없다**. AWS Load Balancer Controller의 서브넷 자동 발견에 필요하므로 D-13 대상이다.

**IP 여유**: 서브넷당 248~251개. PRD §12의 "서브넷 가용 IP 부족" 리스크는 해소됐다.

#### 라우트 테이블 (import 판정 포함)

| Route Table | Name | main | 연결 | 기본 경로 | import 판정 |
|---|---|---|---|---|---|
| `rtb-027480f278fb838bc` | `AI-POC-rtb-public` | - | 3 (public1/2/3) | `0.0.0.0/0` → IGW | **대상** |
| `rtb-04292908122085c0f` | `AI-POC-rtb-private1-ap-northeast-2a` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-09672c332a962e1c1` | `AI-POC-rtb-private2-ap-northeast-2b` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-07993e001273ff781` | `AI-POC-rtb-private3-ap-northeast-2c` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-0c51abfaf38f5bac3` | `AI-POC-rtb-private4-ap-northeast-2a` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-029cd928ba0b9b4fd` | `AI-POC-rtb-private5-ap-northeast-2b` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-0b9b2914e2972acd9` | `AI-POC-rtb-private6-ap-northeast-2c` | - | 1 | `0.0.0.0/0` → NAT | **대상** |
| `rtb-0df670383c3edfc74` | (없음) | **main** | 1 | local만 | **제외** (main RT) |
| `rtb-0d34269ef6e001717` | `public-AI-poc-rtb` | - | **0** | local만 | **NEEDS_DECISION** (고아) |

#### 보안 그룹 (소유자 판정)

| SG | 이름 | 용도 실측 | import 판정 |
|---|---|---|---|
| `sg-022e24e853dfdc241` | `eks-cluster-sg-AI-POC-eks-1555708737` | EKS가 생성·관리 | **제외** |
| `sg-095d1391509f6e4dc` | `default` | VPC 기본 | **제외** |
| `sg-00fcb3e3dac49da71` | `Metanet-GitLab-SG` | GitLab 서버 (HTTP from AI-poc-ALB only) | **제외** (범위 밖 워크로드) |
| `sg-0a3e4ede5310ed2ed` | `AI-poc-ALB-SG` | `AI-poc-ALB`에 부착 → 타깃 그룹이 `AI-poc-GitLab-TG` | **NEEDS_DECISION** — §3.4 |
| `sg-0b0fc86fc94232df3` | `AI-POC-bastion-sg` | bastion (SSM only, no inbound) | **대상** |

#### EC2

| Instance | Name | Type | AZ | 비고 |
|---|---|---|---|---|
| `i-0ca57a18e69b73a1f` | `AI-POC-bastion` | t3.medium | 2a | SSM Online, `AI-POC-bastion-role` |
| `i-0f80645166caaff36` | `Metanet-GitLab` | t3.large | 2a | 범위 밖 |

EKS 워커 노드는 **0대**다 (`eks:nodegroup-name` / `aws:eks:cluster-name` 태그를 가진 인스턴스 없음).

### 3.2 EKS `AI-POC-eks`

| 항목 | 값 |
|---|---|
| 상태 / 생성 | ACTIVE / 2026-09-05 18:53 KST |
| 버전 | **1.36** (서버 실측 `v1.36.4-eks-4cc7921`), upgrade policy `EXTENDED` |
| 엔드포인트 | **프라이빗 전용** (`endpointPublicAccess=false`, `publicAccessCidrs=[]`) |
| 인증 모드 | `API_AND_CONFIG_MAP` — 단 `aws-auth` ConfigMap은 **존재하지 않음** (access entry만 사용) |
| 컨트롤플레인 로깅 | **전 타입 비활성** (api/audit/authenticator/controllerManager/scheduler) |
| Secret 암호화 | **없음** (`encryptionConfig=null`) |
| Service CIDR / IP family | `10.100.0.0/16` / ipv4 |
| 클러스터 역할 | `AI-POC-eks-cluster-role` (`AmazonEKSClusterPolicy` 1건, 인라인 0건) |
| 클러스터 SG | `sg-022e24e853dfdc241` (EKS 생성) / 추가 SG 없음 |
| OIDC issuer | `https://oidc.eks.ap-northeast-2.amazonaws.com/id/2A49105728911261AD4F240BFD9A8AB1` |
| OIDC IAM provider | 등록됨 (동일 ID) |
| 서브넷 | public1/2/3 + private1/2/3 (6개) |
| **노드그룹** | **0개** |
| **Fargate profile** | **0개** |
| **관리형 애드온** | **0개** |

access entries 3건:

| Principal | 비고 |
|---|---|
| `arn:aws:iam::730335451955:user/kiro-iam` | 운영자(현재 자격증명) |
| `arn:aws:iam::730335451955:role/AI-POC-bastion-role` | bastion |
| `…/AWSServiceRoleForAmazonEKS` | 서비스 링크 역할 |

> **PRD 가정과의 차이 (F2)**: PRD §3.2는 DEV 클러스터가 기존 `AI-POC-eks`의 노드그룹·애드온을 미러하도록 지시하지만 **미러할 원본이 없다**. 컨트롤 플레인만 존재하는 빈 클러스터다.

### 3.3 K8s 실측 (bastion 경유, 읽기 전용)

엔드포인트가 프라이빗 전용이라 `AI-POC-bastion`에서 `kubectl`을 실행했다. 원본: `infra/inventory/hub/k8s-existing-cluster.txt`, `…-part2.txt`.

| 항목 | 실측 |
|---|---|
| 노드 | **0** (`No resources found`) |
| 네임스페이스 | `default`, `kube-node-lease`, `kube-public`, `kube-system` — **`ai-poc` 없음** |
| Deployment | `kube-system/coredns` **0/2 (2개 파드 2d18h Pending)** |
| DaemonSet | `kube-system/aws-node` 0/0, `kube-system/kube-proxy` 0/0 |
| Service | `default/kubernetes`, `kube-system/kube-dns`, `kube-system/eks-extension-metrics-api` |
| Ingress | 없음 |
| **Secret** | **0건** — `ai-poc-auth` 없음 |
| ServiceAccount | 45개 전부 시스템. **IRSA 어노테이션 0건** |
| ConfigMap | 시스템 + `kube-system/amazon-vpc-cni` |
| PVC | 0 |
| StorageClass | `gp2` (`kubernetes.io/aws-ebs` — **in-tree 프로비저너**) |
| HPA / NetworkPolicy | 0 / 0 |
| PDB | `kube-system/coredns` 1건 |
| CRD | 7개 (전부 vpc-cni 계열: `*.networking.k8s.aws`, `*.vpcresources.k8s.aws`, `eniconfigs.crd.k8s.amazonaws.com`) |
| Helm release | **0건** |

#### 자체 관리 애드온 실측 버전

| 컴포넌트 | 이미지 태그 | 1.36 관리형 default | 일치 |
|---|---|---|---|
| aws-node (vpc-cni) | `amazon-k8s-cni:v1.22.4-eksbuild.3` | `v1.22.4-eksbuild.3` | **일치** |
| aws-eks-nodeagent | `aws-network-policy-agent:v1.4.0-eksbuild.1` | (vpc-cni 부속) | — |
| kube-proxy | `eks/kube-proxy:v1.36.0-eksbuild.17` | `v1.36.0-eksbuild.17` | **일치** |
| coredns | `eks/coredns:v1.14.3-eksbuild.14` | `v1.14.3-eksbuild.14` | **일치** |
| metrics-server | **미설치** | `v0.9.0-eksbuild.9` | — |

→ 관리형 애드온으로 등록해도 **버전 변화는 없다**. README 1번 항목은 관리 주체 전환일 뿐 업그레이드가 아니다.

**주의**: `gp2` StorageClass가 in-tree `kubernetes.io/aws-ebs` 프로비저너를 쓴다. k8s 1.36에는 in-tree AWS EBS 플러그인이 없으므로 이 SC로는 PV가 프로비저닝되지 않는다. PVC가 필요해지면 `aws-ebs-csi-driver` 애드온(1.36 default `v1.65.0-eksbuild.1`)이 선행되어야 한다.

### 3.4 `AI-poc-ALB` 판정 (신규 발견)

| 항목 | 값 |
|---|---|
| 이름 / 유형 | `AI-poc-ALB` / application, internet-facing |
| ARN | `…:loadbalancer/app/AI-poc-ALB/6af48b65095d9276` |
| 생성 | 2026-09-07 10:05 KST |
| SG | `sg-0a3e4ede5310ed2ed` (`AI-poc-ALB-SG`) |
| 서브넷 | public1/2/3 |
| **타깃 그룹** | **`AI-poc-GitLab-TG`** (target-type `instance`, port 80) |
| service-managed EIP | 3개 (100.10.0.216 / 100.10.3.84 / 100.10.6.219) |

이름이 `AI-poc-*`이지만 실제로 트래픽을 보내는 대상은 **GitLab**이다. `Metanet-GitLab-SG`의 설명("HTTP from AI-poc-ALB only")과 일치한다. k8s가 만든 ALB가 아니다(k8s ALB는 `k8s-<ns>-<name>-<hash>` 규칙).

**따라서 `AI-poc-ALB` / `AI-poc-ALB-SG`는 AI-POC 앱 인프라가 아니다.** import 대상에서 제외하는 것을 권고하나, 이름 때문에 오판 위험이 있어 NEEDS_DECISION으로 올린다.

**부수 리스크**: `config.env`의 `ALB_NAME=AI-POC`다. 앱 Ingress가 뜰 때 AWS Load Balancer Controller가 만들 ALB 이름이 기존 `AI-poc-ALB`와 충돌하지 않는지 Phase 4에서 확인해야 한다 (ELB 이름은 리전 내 유일).

### 3.5 IAM

| 역할 | 연결 정책 | 인라인 정책 |
|---|---|---|
| `AI-POC-eks-cluster-role` | `AmazonEKSClusterPolicy` | 없음 |
| `AI-POC-bastion-role` | `AmazonSSMManagedInstanceCore` | `bastion-eks-ecr-access` |

`AI-POC` / `ai-poc` 접두 역할은 이 둘뿐이다. **IRSA 역할(`AI-POC-app-role`)은 존재하지 않는다.**

### 3.6 컨테이너 · 스토리지

| 항목 | 값 |
|---|---|
| ECR | `ai-poc-app`, `ai-poc-frontend` — 태그 `IMMUTABLE`, scan on push, 수명주기 정책 적용됨 |
| ECR 이미지 | **양쪽 모두 0개** |
| ECR 수명주기 | rule1 untagged 1일 만료 / rule2 `20*` 태그 10개 초과분 만료 |
| S3 | `ai-poc-730335451955` (ap-northeast-2) |
| us-west-2 영상 출력 버킷 | **없음** |
| Neptune | **없음** (`describe-db-clusters` 0건) |
| EventBridge Scheduler | AI-POC 관련 **0건** |
| CloudWatch 로그 그룹 | AI-POC 앱 관련 없음 |

### 3.7 서비스 쿼터

| 쿼터 | 코드 | 값 | 판정 |
|---|---|---|---|
| Running On-Demand G and VT instances | `L-DB2E81BA` | **768** | GPU 여유 충분 |
| Running On-Demand Standard (A,C,D,H,I,M,R,T,Z) | `L-1216C47A` | 512 | 충분 |
| EKS Clusters | `L-1194D53C` | 100 | 충분 |
| EC2-VPC Elastic IPs | `L-0263D0A3` | **5** | 현재 비-서비스관리 EIP 3개 사용 → 여유 2 |
| NAT gateways per AZ | `L-FE5A380F` | 5 | 충분 |

> Overlay 계정은 GPU 쿼터가 0이어서 GPU를 켤 수 없었다(history §6, §13). **Hub 계정은 768로 제약이 아니다.**

---

## 4. Protected 목록 (H3)

### 4.1 결론 먼저

**Base(`AI-POC/`)에는 GPU 인프라 자산이 존재하지 않는다.** `README.md` "옮기지 않은 것 → GPU / Wan 2.2 (사용자 지시로 제외)"가 근거이고, 실측으로 부재를 확인했다.

| README가 미이관이라고 적은 자산 | 실측 |
|---|---|
| `k8s/gpu-device-plugin.yaml` | 부재 확인 |
| `k8s/gpu-storage.yaml` | 부재 확인 |
| `k8s/video-gen.yaml.tmpl` | 부재 확인 |
| `k8s/wan22-download-job.yaml` | 부재 확인 |
| `video-gen/` (8개) | 부재 확인 |
| `infra/gpu-*.sh` (9개) | 부재 확인 (0건) |
| `infra/wan22-*.sh` (17개) | 부재 확인 (0건) |
| `infra/video-gen-*.sh`, `build-video-gen.sh` | 부재 확인 (0건) |
| `deploy/deploy-video-gen.ps1` | 부재 확인 |
| `deploy/bench/` (3개) | 부재 확인 |

따라서 H3의 Protected 범위는 **인프라가 아니라 앱 코드 경로**로 한정된다.

### 4.2 Protected — Base의 살아있는 GPU 코드 경로

Phase 1에서 이 항목들은 `git diff baseline`이 **빈 출력**이어야 한다 (A3).

| 파일 | 보호 단위 | 근거 |
|---|---|---|
| `app/src/wan_client.py` | **파일 전체** (최상위 심볼 32개: `BASE_URL` `WanError` `submit` `status` `result_bytes` `health` `describe` `effective_limits` `frames_for` `latent_tokens` `max_seconds_for` `budget_from` `too_long_message` `_request` `_parallel_ranks` `_estimate_minutes` `_limit_note` `RESOLUTIONS` `SIZE_FOR` `FPS` `FRAME_FLOOR` `FRAME_CEILING` `DURATION_CHOICES` `MAX_LATENT_TOKENS` `LATENT_DIVISOR` `DEFAULT_RESOLUTION` `ASPECT_NOTE` `ESTIMATE_REFERENCE_SECONDS` 등) | `server.py` L36이 모듈 최상단에서 import. 제거하면 앱이 기동 불가(ImportError) |
| `app/src/server.py` | Wan 함수 14개: `wan_prompt_caller`(L4365) `wanjob_payload`(L4385) `reap_stale_wan_job`(L4414) `adopt_wan_job`(L4476) `run_wan_job`(L4535) `wan_profile_json`(L4712) `watch_wan_job`(L4752) `wan_analyze`(L4864) `wan_options`(L4877) `wan_convert`(L4935) `wan_generate`(L5142) `wan_jobs`(L5360) `wan_job_detail`(L5371) + `/api/v2/video/*` 라우트 + `wan_client` 호출 12곳 | GPU 영상 경로 진입점 |
| `app/src/graph_store.py` | `wanjobs` 정점 함수 7개: `_wanjob_view`(L2011) `create_wanjob`(L2062) `update_wanjob`(L2146) `touch_wanjob`(L2175) `get_wanjob`(L2186) `list_wanjobs`(L2191) `delete_wanjob`(L2209) + `QUOTA_VIDEO` | Wan 작업 기록. 기존 Bedrock 영상과 정점을 분리해 둔 구조 |
| `app/src/video_prompt.py` | L504~ Wan 2.2 I2V-A14B 전용 프롬프트 블록 (`_WAN_INSTRUCTION`, `_WAN_STYLE_GUARD`) | Wan은 자유 문장 한 단락을 받아 Luma 경로와 조립 방식이 다르다 |
| `app/src/prompt_layers.py` | `SELF_HOSTED_WAN` 모델 라벨(L106-107), `video:wan-prompt` 역할 계층(L185-192) | 자체 GPU는 Bedrock 카탈로그에 없어 별도 라벨 |
| `app/src/stitcher.py` | 파일 전체 (7개 심볼: `StitchError` `ffmpeg_path` `_run` `concat` `probe_duration` `STITCH_TIMEOUT_SECONDS`) | 롱테이크 컷 이어붙이기 |
| `k8s/workloads.yaml.tmpl` | `VIDEO_GEN_URL` env 항목 + L178~187 주석 블록 | 값을 비우면 "연결 실패"가 아니라 "설정 오류"가 된다 |
| `deploy/render.py` | L61~L76 `VIDEO_GEN_IMAGE` 렌더 분기 (`ECR_VIDEO_GEN_REPO`/`VIDEO_GEN_TAG`가 비면 `VIDEO_GEN_NOT_CONFIGURED`) | 빈 값을 정상 경로로 처리 |
| `deploy/config.env(.example)` | `ECR_VIDEO_GEN_REPO=`, `VIDEO_GEN_TAG=` (**빈 값 유지**) | 의도된 빈 값 |
| `frontend/public/index.html` | `videoNewView` 화면과 `wan*` 요소 62개 (`wanText` `wanDropzone` `wanGenerateButton` `wanJobList` `wanServerChip` `wanStyleChips` `wanConvert*` `wanBrief*` 등) | GPU 영상 UI |
| `frontend/public/app.js` | `wan*` 식별자군(`wanAnalyze` `wanConvert` `wanEstimateFor` 등) + 이벤트 배선 | markup만 지우면 null 참조로 **이후 모든 리스너가 끊긴다** (Overlay history §5의 경고) |
| `frontend/public/styles.css` | Wan 화면 스타일 | — |
| `deploy/verify/verify-video-generation.ps1` | Wan 경로 검증 | — |

### 4.3 Protected 아님 — 옛 계정 잔존물 (정리 대상, 이번 범위 밖)

GPU 키워드를 포함하지만 **원본 계정(`061051247564`)의 리소스 이름**을 가리켜 Hub에서 동작하지 않는다. README "알려진 제약"이 이미 지적한 항목이다.

| 파일 | 잔존 내용 |
|---|---|
| `deploy/schedules.ps1` | `quick-test-eks-gpu-start`, `-GpuNodegroup quick-test-eks-gpu-g6e24` |
| `deploy/start-all.ps1` | `-NoGpu` 스위치, `g6e.24xlarge $18.5/hr` 언급 |
| `deploy/stop-all.ps1` | `-GpuOnly`, `quick-test-eks-gpu` / `quick-test-eks-nodegroup` |
| `infra/build-deploy.sh` | L100 `gpu-device-plugin.yaml` 미이관 주석 |

이 파일들은 Hub에 스케줄이 0건이라 현재 무동작이다. **수정하지 않고 보고만 한다** (원인이 Base 자체 문제이므로 R6.9 원칙).

### 4.4 A2 검증 기준선 (삭제 0 증명용)

| 집합 | 기준선 값 |
|---|---|
| git 추적 파일 | **79개** (`baseline` 태그, 커밋 `7ebee61`) |
| Python 파일 | 16개 |
| Python 최상위 심볼 | **666개** (server.py 218 / graph_store.py 151 / documents.py 61 / model_registry.py 33 / wan_client.py 32 / prompt_layers.py 31 / video_prompt.py 30 / image_brief.py 20 / styles.py 20 / toon_convert.py 16 / gateway.py 13 / prompt_boost.py 13 / providers.py 12 / account_guard.py 9 / stitcher.py 7 / `__init__.py` 0) |
| K8s 템플릿 | 5개 (`namespace` `service-account` `workloads` `ingress` `model-catalog`) |

---

## 5. 도구 · 사전 조건

| 도구 | 상태 |
|---|---|
| aws-cli | 2.28.21 (로컬) / 2.36.40 (bastion) |
| Terraform | 1.14.3 — PRD 요건 ≥1.10 충족 |
| git | 2.52.0 (로컬) / 2.50.1 (bastion) |
| kubectl | **v1.36.4 신규 설치** (`%USERPROFILE%\.local\bin`, User PATH 등록). 클러스터 1.36과 일치 |
| helm | 미설치 (로컬) / v3.21.4 (bastion) |
| **Docker** | **로컬 부재** / bastion Docker 25.0.14 → **D-10은 bastion 빌드로 확정** |
| python | 3.11.9 |
| npm | 11.17.0 |
| TF_DATA_DIR | `%LOCALAPPDATA%\terraform-data\ai-poc` (User scope, OneDrive 동기화 밖) |
| bastion 디스크 | 30GB 중 28GB 여유 (9% 사용) |

---

## 6. Phase 0에서 발견한 차이 · 이슈

`.kiro/specs/…/requirements.md` §2의 F1~F12에 더해 이번 수집에서 새로 확인된 것.

| ID | 내용 | 영향 |
|---|---|---|
| F13 | `AI-poc-ALB` / `AI-poc-ALB-SG`가 GitLab 인프라 (타깃 그룹 `AI-poc-GitLab-TG`) | import 범위. NEEDS_DECISION |
| F14 | `rtb-0d34269ef6e001717` (`public-AI-poc-rtb`)가 연결 0개 고아 route table | import 범위. NEEDS_DECISION |
| F15 | `aws-auth` ConfigMap 부재 (authMode는 `API_AND_CONFIG_MAP`이지만 access entry만 사용) | DEV 미러 시 `API` 모드로 좁힐 여지 |
| F16 | 자체관리 애드온 3종의 버전이 이미 1.36 관리형 default와 동일 | 관리형 등록은 버전 변화 없는 관리 주체 전환 |
| F17 | `gp2` SC가 in-tree `kubernetes.io/aws-ebs` 프로비저너 → 1.36에서 비동작 | PVC 필요 시 `aws-ebs-csi-driver` 선행 |
| F18 | EC2-VPC EIP 쿼터 5, 비-서비스관리 3개 사용 중 | DEV 전용 NAT를 추가하면 여유 1 |
| F19 | `config.env`의 `CREDENTIALS_FILE=test.json`이 루트에 없고 `CREDENTIALS_ACCESS_NAME=kiro`가 실제 `kiro-iam-mtx`와 불일치 → **`preflight.ps1`이 exit 1로 즉시 중단** | Phase 4 배포 경로 선행 차단. 2줄 수정으로 해소 |
| F20 | `deploy.ps1`의 `Get-SourceStamp -RepoRoot $workspaceRoot`가 git 저장소를 **워크스페이스 루트**로 가정. Phase 0.1에서 git을 `AI-POC/`에 초기화함 | Phase 4에서 source-stamp 동작 확인 필요. H1이 루트 git init을 금지하므로 스크립트 쪽 조정이 맞다 |
| F21 | `coredns` 파드 2개가 2d18h 동안 Pending | 노드그룹 생성 시 자동 해소 |

---

## 7. 게이트 0 확인 요청 항목

| ID | 상태 |
|---|---|
| D-01 계정 | 확정 `730335451955`. 단 "운영 계정"이 아니라 이관 대상 계정임을 §1.1로 정정 |
| D-02 리전 | 확정 `ap-northeast-2` |
| D-03 history 위치 | 확정 `이매지너스/이매지너스_history.md` (루트에 없음) |
| D-04 `.kiro` 위치 | 적용 = 워크스페이스 루트 `.kiro/` |
| D-10 빌드 환경 | **확정 = bastion 빌드** (로컬 Docker 부재) |
| D-13 서브넷 태그 | **필요 확정** — `role/elb`·`role/internal-elb`·`cluster/*` 태그가 9개 서브넷 전부에 없음 |
| D-14 GPU 방침 | **미결** — Base에 GPU 인프라 자산 부재(§4.1) 확인. 결정 없이 Phase 3 착수 불가 |
| D-15 엔드포인트 | **미결** |
| D-16 동등성 기준선 | **미결** — 기존 클러스터가 빈 상태로 확정(§3.3) |
| D-18 VPC 명칭 | **미결** — Name 태그 `AI-poc-vpc` |
| 신규 | F13(ALB 소유권) · F14(고아 RT) · F19(config.env 수정 승인) · F20(source-stamp) |
