# DEV 클러스터 폐지 · 운영 단일 클러스터로 통합

| 항목 | 내용 |
|---|---|
| 시각 | 2026-09-10 |
| 계기 | 사용자 결정 — `Dev-AI-POC-eks` 를 삭제했고 앞으로 앱 배포·개발은 `AI-POC-eks` 만 쓴다 |
| 제약 | **Terraform 은 사용·수정하지 않는다.** 사용자가 나중에 직접 정리한다 |

---

## 1. 실측한 상태

| 항목 | 결과 |
|---|---|
| `Dev-AI-POC-eks` | **DELETING**. 노드그룹 0개 |
| GPU 인스턴스 `i-0fe7abd96bfb1287c` (g6e.xlarge) | **terminated** — GPU 비용($2.288/h) 정지 확인 |
| `AI-POC-eks` | ACTIVE / k8s 1.36 / 노드그룹 `AI-POC-eks-ng` / 엔드포인트 프라이빗 전용 |
| `AI-POC-eks` 애드온 | `coredns` · `eks-pod-identity-agent` · `kube-proxy` · `vpc-cni`. **EBS CSI 없음** |
| 운영 앱 | `/api/health` 200 · `graph.reachable=true` · 태그 `20260909-v1` |

### 클러스터 삭제로 생긴 고아 리소스

| 리소스 | 크기 | 월 비용 | 처리 |
|---|---|---|---|
| Neptune `dev-ai-poc-graph` | db.t4g.medium | 약 $104 | **삭제 (승인)** |
| `vol-07e7c990f74fc7e6a` (2a) | 200GB gp3 | 약 $18 | **보존** — Wan 2.2 가중치 118GB 가 들어 있다 |
| `vol-0b43171c3e7f5a02f` (2b) | 200GB gp3 | 약 $18 | 미결정 — 첫 다운로드본, 2a 와 중복 |

2a 볼륨을 남긴 이유: GPU 작업을 `AI-POC-eks` 로 옮길 때 **정적 PV 로 그대로 붙이면 118GB 재다운로드를 건너뛴다**(실측 5분 15초 소요분). 단 EBS 는 AZ 에 묶이므로 새 GPU 노드그룹도 `ap-northeast-2a` 단일 AZ 여야 한다. g6e 는 2a·2b 에만 있다.

---

## 2. Neptune `dev-ai-poc-graph` 삭제

| 시각 | 명령 | 결과 |
|---|---|---|
| — | `describe-db-clusters` | `DeletionProtection=true`, 멤버 `dev-ai-poc-graph-1` |
| — | `modify-db-cluster --no-deletion-protection --apply-immediately` | `DelProt=false` |
| — | `delete-db-instance dev-ai-poc-graph-1` | `deleting` → 완료 |
| — | `delete-db-cluster --final-db-snapshot-identifier dev-ai-poc-graph-final` | `backing-up` → 삭제 진행 |

인스턴스를 클러스터보다 먼저 지워야 한다. 순서를 바꾸면 `InvalidDBClusterStateFault` 로 거절된다.

최종 스냅샷 `dev-ai-poc-graph-final` 을 남겼다. 거의 빈 DB(할당 1GB)라 비용은 무시할 수준이고 복구 여지를 남긴다. Terraform 의 `final_snapshot_identifier` 와 같은 이름을 써서 나중에 코드와 대조하기 쉽게 했다.

**남긴 것**: SG `sg-0b55cdf39be0b1861` 과 서브넷 그룹 `dev-ai-poc-neptune-subnets`. 둘 다 비용이 0 이고 Terraform 이 관리하므로 CLI 로 지우면 상태 불일치만 늘어난다.

---

## 3. 앱을 운영 단일 클러스터로 통합

앱은 이미 `AI-POC-eks` 에서 돌고 있었다. 그래서 이 작업은 **죽은 클러스터로 배포할 경로를 없애는 것**이다.

| 파일 | 변경 |
|---|---|
| `deploy/config.dev.env` | **삭제.** 모든 값이 사라진 리소스를 가리킨다 (클러스터명·OIDC·SG·IAM 역할·ALB·Neptune·GPU 노드그룹·큐) |
| `deploy/deploy.ps1` | `-Target` 을 `ValidateSet('prod')` + 기본값 `'prod'` 로. 인수를 빼도 운영으로 간다 |
| `deploy/preflight.ps1` | `ValidateSet('', 'prod')` |
| `deploy/config.env.example` | 오버레이 절을 단일 대상 기준으로 갱신 |

### 오버레이 구조는 남긴다

대상이 하나가 됐지만 `config.env` + `config.<target>.env` 구조는 유지했다. 두 가지 이유다.

- 값을 클러스터별로 분리하지 않은 것이 이 파이프라인의 치명적 버그를 만든 원인이었다.
  `deploy.ps1` 이 `build-deploy.sh` 에 환경변수를 넘기지 않아 모든 배포가 시작 직후 죽었고,
  그 원인은 공통 값과 대상 값이 한 파일에 섞여 있던 것이다
- Phase 5 의 고객사별 배포에서 같은 구조가 다시 필요하다

### 삭제한 `config.dev.env` 가 담고 있던 값 (기록)

나중에 필요할 수 있어 남긴다. 전부 삭제된 리소스다.

```
EKS_CLUSTER_NAME=Dev-AI-POC-eks
EKS_OIDC_ID=9761A0FCEF28DCFBC85E9C998F5FA37B
EKS_CLUSTER_SECURITY_GROUP=sg-09275dce10e4cfeff
IAM_APP_ROLE_NAME=Dev-AI-POC-app-role
IAM_APP_POLICY_NAME=Dev-AI-POC-s3-access
ALB_NAME=DEV-AI-POC-web-alb
NEPTUNE_ENDPOINT=dev-ai-poc-graph.cluster-cd0q2kk4wlbo...
NEPTUNE_CLUSTER_ID=dev-ai-poc-graph
NEPTUNE_SECURITY_GROUP=sg-0b55cdf39be0b1861
APP_DOMAIN=dev-ai-poc-web.meta-clouds.com
GPU_NODEGROUP_NAME=dev-ai-poc-video-gpu
VIDEO_WORKER_REPO=dev-ai-poc-video-worker
VIDEO_WORKER_TAG=wan22-v4
VIDEO_CLIP_QUEUE_URL=.../dev-ai-poc-video-clip
```

`config.env` 에 이 키들의 빈 기본값이 있어 `--target prod` 렌더는 깨지지 않는다. 확인: 템플릿 17개 렌더링 완료.

---

## 4. Terraform 상태 불일치 (손대지 않음 — 기록만)

사용자 지시로 Terraform 을 건드리지 않았다. 나중에 정리할 때 필요한 목록이다.

| 레이어 | 사라진 실물을 가리키는 것 |
|---|---|
| `hub/25-app-iam` | `Dev-AI-POC-app-role` · `Dev-AI-POC-s3-access` · `Dev-AI-POC-bedrock-access` · Dev OIDC provider(`9761A0FCEF28DCFBC85E9C998F5FA37B`) |
| `hub/26-neptune` | `dev-ai-poc-graph` 클러스터·인스턴스 (방금 삭제) · SG `sg-0b55cdf39be0b1861` · 서브넷그룹 `dev-ai-poc-neptune-subnets` |
| `hub/40-video-gpu` (다른 세션) | GPU 노드그룹 `dev-ai-poc-video-gpu` · 워커 IAM·Pod Identity 연결 · PVC. 클러스터 삭제와 함께 사라진 것과 남은 것이 섞여 있다 |
| `hub/20-eks-prod` | 영향 없음. 운영 노드그룹만 관리한다 |

정리 시 `terraform plan` 이 Dev 리소스에 대해 재생성을 제안할 것이다. 재생성이 아니라 **구성에서 제거**가 맞다.

---

## 5. GPU 영상 기능 — 이관 시 필요한 것

B안 결정으로 뒤로 미뤘다. `AI-POC-eks` 에서 다시 세우려면 다음이 필요하다.

| 항목 | 현재 |
|---|---|
| **EBS CSI 드라이버 애드온** | **없음.** 가중치 PVC 에 필수 |
| GPU 노드그룹 (g6e.xlarge, **2a 단일 AZ**, taint `nvidia.com/gpu=true:NoSchedule`, label `workload=video-gpu`) | 없음 |
| 노드 IAM 역할 | 없음 (Dev 것은 사라짐) |
| NVIDIA device plugin DaemonSet | 없음 |
| StorageClass + 가중치 PV/PVC | 없음. **2a 볼륨 `vol-07e7c990f74fc7e6a` 를 정적 PV 로 재사용** |
| SQS 큐 · 워커 IAM(Pod Identity) · 워커 Deployment | 없음 |
| 워커 이미지 | ECR `dev-ai-poc-video-worker:wan22-v6` **사용 가능** (§6) |

**단일 AZ 로 만들어야 한다.** Dev 에서 겪은 실패가 정확히 이것이다. 노드그룹을 2a·2b 두 AZ 에 두었더니 가중치 볼륨(2b)과 GPU 노드(2a)가 어긋나 워커가 영구 Pending 이 됐다. RWO EBS 는 AZ 를 넘지 못한다.

---

## 6. 이관 전에 해결해 둔 워커 이미지 문제 (자산으로 남음)

Dev 에서 GPU 워커가 `CrashLoopBackOff` 였고 원인을 끝까지 좁혀 `wan22-v6` 을 만들어 두었다. 클러스터가 사라져도 **이미지는 ECR 에 남아 재사용된다.**

### 증상

`wan22-v4` → `No module named 'einops'` / `wan22-v5` → `No module named 'decord'`. 모듈이 하나씩 나오는 패턴이었다(v3 → v4 → v5 가 그렇게 소모됐다).

### 근본 원인 둘

1. **업스트림 `requirements.txt` 가 실제 임포트와 어긋난다.** Wan2.2 저장소 쪽 선언 누락이다. 실측으로 확인한 누락은 네 개: `einops` · `decord` · `librosa` · `peft`
2. **`ARG WAN_REF=main` 이었다.** 주석은 "커밋을 박는다"고 적혀 있었지만 실제로는 브랜치를 따라가서 빌드 시점마다 의존성 목록이 달라졌다

### GPU 없는 곳에서는 이 누락이 보이지 않는다

배스천에서 같은 임포트를 돌리면 **통과한 것처럼 보인다.** `wan.configs` 가 임포트 도중 CUDA 를 건드려서, GPU 가 없으면 `decord` 임포트에 도달하기 전에 `Found no NVIDIA driver` 로 죽는다. 실제로 이 오판을 한 번 했다. **반드시 GPU 노드에서 확인한다.**

전수 열거는 GPU 파드 안에서 `import wan; from wan.configs import WAN_CONFIGS` 를 돌리며 빠진 모듈을 설치하는 루프로 한 번에 했다. 결과:

```
MISSING decord / librosa / peft
IMPORT_OK ['animate-14B', 'i2v-A14B', 's2v-14B', 't2v-A14B', 'ti2v-5B']
```

### 수정 (`video-worker/Dockerfile`)

- `einops decord librosa peft` 를 한 번에 설치
- `WAN_REF` 를 커밋 `42bf4cfaa384bc21833865abc2f9e6c0e67233dc` 로 고정
- `git clone --depth 1 --branch <ref>` 는 **커밋 SHA 를 거부한다.** `git init` + `fetch --depth 1 <sha>` + `checkout FETCH_HEAD` 로 바꿔 얕은 클론을 유지하면서 커밋을 고정. `.wan-ref` 파일에 SHA 를 남겨 이미지에서 되읽을 수 있게 했다

빌드 검증: `deps OK: 0.6.0 0.11.0 0.20.0 0.8.2` / `.wan-ref = 42bf4cfa…`. ECR 에 `wan22-v6` 푸시 완료.

---

## 7. AZ 교착 해결 절차 (재사용 가능한 기록)

Dev 에서 실제로 통했던 절차다. `AI-POC-eks` 로 이관할 때 같은 상황이면 그대로 쓴다.

`ai-poc-gp3` StorageClass 는 `reclaimPolicy: Retain` + `volumeBindingMode: WaitForFirstConsumer` 다. 그래서 **PVC 를 지워도 볼륨은 남고**, 같은 이름으로 다시 만들면 첫 소비자 파드가 뜨는 AZ 에 새 볼륨이 생긴다. 워커 Deployment 는 같은 `claimName` 을 보므로 수정할 필요가 없다.

1. 워커 `replicas=0` (PVC 참조 해제)
2. PVC 삭제 → PV 는 `Released` 로 보존
3. 같은 이름으로 PVC 재생성 → `Pending` (WaitForFirstConsumer)
4. 다운로드 Job 재실행. **GPU 노드셀렉터를 붙여** GPU 노드가 있는 AZ 에 볼륨이 생기게 한다
5. 워커 `replicas=1`

결과: 새 PVC 가 `ap-northeast-2a` 에 바인딩, 다운로드 118GB / 5분 15초, 샤드 검증 통과(high·low 각 6개 + T5 + VAE), 워커가 GPU 노드에 스케줄되어 **`GPU NVIDIA L40S, VRAM 44.4GB` 인식까지 확인**.

즉 인프라 경로는 전부 검증됐다. 남은 것은 워커 이미지 의존성이었고 그것이 §6 이다.

---

## 8. 다음

1. `vol-0b43171c3e7f5a02f` (2b, 중복) 삭제 여부 결정 — 월 약 $18
2. GPU 영상 기능을 `AI-POC-eks` 로 이관 (§5 목록). **EBS CSI 애드온이 선행**
3. Terraform 정리 — 사용자가 시점을 지정한다 (§4 목록)
4. 미커밋 정리. 다른 세션 작업이 섞여 있다

---

## 9. 리소스 정리 내역

사용자 결정 원칙: **쓸데없는 리소스는 지우고, 지금 실제 앱을 배포하고 사용할 때 필요한 것만 남긴다.** 만일을 대비해 산출물을 보관하지 않는다 — 필요해지면 그때 다시 만든다.

### 9.1 지운 것

| 리소스 | 근거 | 월 절감 |
|---|---|---|
| Neptune `dev-ai-poc-graph` (인스턴스 + 클러스터) | 붙을 클러스터가 사라졌다 | **약 $104** |
| `vol-07e7c990f74fc7e6a` 200GB gp3 (2a, Wan 가중치 118GB) | Dev 클러스터 삭제와 함께 정리됐다 | 약 $18 |
| `vol-0b43171c3e7f5a02f` 200GB gp3 (2b, 첫 다운로드본) | 위와 중복 | 약 $18 |
| ALB `DEV-AI-POC-web-alb` | Ingress 삭제 시 ALB 컨트롤러가 함께 정리 | 약 $17 |
| ECR `dev-ai-poc-video-worker:wan22-v3` `v4` `v5` | 전부 의존성 누락으로 기동 불가. `v6` 이 대체 | 약 $1.1 |
| ECR `dev-ai-poc-video-worker:wan22-v6` | §9.3 | 약 $0.4 |
| Neptune 스냅샷 `dev-ai-poc-graph-final` | 삭제된 클러스터의 것. 0GB | 0 |

합계 **월 약 $158**. 정리 후 미사용 EBS 볼륨 0개 · 미할당 EIP 0개.

### 9.2 남긴 것 — 앱 운영에 실제로 필요

| 리소스 | 왜 필요한가 |
|---|---|
| `AI-POC-eks` + 노드 **2대** | 파드 22개인데 `t3.medium` 상한이 노드당 17개다. argocd 7 + kube-system 10 + gitlab-runner 1 + 앱 4. 1대로 줄이면 앱 파드가 다시 Pending 된다 |
| Neptune `ai-poc-graph` + 자동 백업 1개 | 로그인·프로젝트가 전부 이 그래프를 거친다. 없으면 admin 조차 로그인할 수 없다 |
| ALB `AI-POC-web-alb` + 타깃그룹 2개 | 유일한 접속 경로 |
| ECR `ai-poc-app` · `ai-poc-frontend` 각 2태그 | `20260909-v1` 실행 중, `20260905-v1` 은 **롤백 대상**. 합쳐 245MB |
| bastion `i-0ca57a18e69b73a1f` | EKS 엔드포인트가 프라이빗 전용이라 빌드·배포·kubectl 이 전부 경유한다 |
| S3 `ai-poc-730335451955` | 사용자 콘텐츠 · 모델 카탈로그 · 빌드 아카이브 |

### 9.3 Wan 2.2 판정 — 필요하지만 산출물은 지운다

먼저 확인했다. **Wan 2.2 는 새 GPU 영상 기획의 핵심이다.**

- `.kiro/specs/gpu-video-generation/design.md` — "[3] 영상 생성 · GPU 워커 · Wan 2.2 I2V / FLF2V · 서울"
- 같은 spec `requirements.md` — Wan 2.2 Apache 2.0 확인 완료로 등재
- `tasks.md` 2.10 · 2.11 — 가중치 시딩, L40S **44GB** bf16 적재 검증
- `video-worker/src/inference.py` 가 `wan.WanI2V` 를 직접 호출하고 `WAN_TASK=i2v-A14B`
- g6e(L40S 44GB) 노드 선정 근거 자체가 Wan 2.2 14B bf16 이다

그래도 **산출물 두 개는 지웠다.** 기획에 필요한 것은 *레시피*이고, 산출물은 레시피에서 다시 만들어진다.

| 산출물 | 재생성 비용 | 레시피 위치 |
|---|---|---|
| 가중치 118GB | **5분 15초** (실측) | `k8s-gpu/model-download.yaml.tmpl` + `WAN_MODEL_REPO` |
| 워커 이미지 `wan22-v6` 3.7GB | 약 7분 (빌드 + push) | `video-worker/Dockerfile` + 이 문서 §6 |

즉 12분이면 둘 다 복구된다. 그것을 위해 월 $18.4 를 계속 낼 이유가 없다.

### 9.4 지우지 않은 고아 — 비용 0 + Terraform 관리

전부 **비용이 0** 이고 Terraform 이 관리한다. CLI 로 지우면 상태 불일치만 늘고 절감은 없다. 사용자가 예정한 Terraform 정리 때 함께 제거하는 것이 맞다.

| 리소스 | 관리 레이어 |
|---|---|
| SQS 큐 8개 (`dev-ai-poc-video-{clip,edit,keyframe,lora}` + 각 DLQ) | `hub/40-video-gpu` (다른 세션) |
| ECR 저장소 `dev-ai-poc-video-worker` (이미지 0개) | `hub/40-video-gpu` |
| IAM `Dev-AI-POC-app-role` · `Dev-AI-POC-s3-access` · `Dev-AI-POC-bedrock-access` | `hub/25-app-iam` |
| OIDC provider `9761A0FCEF28DCFBC85E9C998F5FA37B` | `hub/25-app-iam` |
| SG `sg-0b55cdf39be0b1861` · 서브넷그룹 `dev-ai-poc-neptune-subnets` | `hub/26-neptune` |
| GPU 노드 IAM 역할 · 워커 IAM · Pod Identity 연결 | `hub/40-video-gpu` |

### 9.5 건드리지 않은 범위 밖 리소스

가드레일대로 손대지 않았다. 정리 대상으로 오인하지 않도록 남긴다.

`Metanet-GitLab`(`i-0f80645166caaff36`) · `AI-poc-ALB` · `AI-poc-GitLab-TG` · `AI-poc-ArgoCD-TG` · `eks-metaclouds` 및 그 노드 · `Grafana-vpc`

### 9.6 확인해야 할 남은 위험

**`video-worker/` 가 아직 미커밋이다.** §6 의 Dockerfile 수정(누락 의존성 4개 + `WAN_REF` 커밋 고정 + clone 방식 변경)이 거기에 들어 있고, 그 디렉터리는 다른 세션 작업 영역이라 이 세션이 커밋하지 않았다.

이미지를 지웠으므로 **레시피가 유일한 복구 수단이다.** 다행히 재생성에 필요한 정보는 이 문서 §6 에 전부 적혀 있다(패키지 4개 이름, 커밋 SHA `42bf4cfaa384bc21833865abc2f9e6c0e67233dc`, `git init` + `fetch --depth 1 <sha>` + `checkout FETCH_HEAD` 방식, GPU 없는 곳에서는 누락이 보이지 않는다는 함정). 그래도 `video-worker/` 를 커밋해 두는 것이 안전하다.

### 9.7 정리 후 검증

| 항목 | 결과 |
|---|---|
| 클러스터 | `AI-POC-eks` · `eks-metaclouds`(범위 밖). `Dev-AI-POC-eks` 소멸 확인 |
| VPC 내 running 인스턴스 | bastion · Metanet-GitLab(범위 밖) · 운영 노드 2대 |
| 미사용 EBS · 미할당 EIP | 0 · 0 |
| Neptune | `ai-poc-graph` 만 available |
| 운영 앱 | `/api/health` **200** · `ok=true` · `graph.reachable=true` · `tag=20260909-v1` |
| 접속 | `https://ai-poc-web.meta-clouds.com` |
