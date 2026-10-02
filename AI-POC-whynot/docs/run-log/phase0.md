# Run Log · Phase 0 (준비 및 Hub 계정 탐색)

| 항목 | 내용 |
|---|---|
| 일시 | 2026-09-08 12:30 ~ 13:35 KST |
| 실행 환경 | Windows 11 / Windows PowerShell 5.1 / 워크스페이스 `…\A.AI_미디어프로젝트` |
| AWS 프로필 | `ai-poc-hub` (계정 730335451955, ap-northeast-2) |
| **AWS 쓰기** | **0건.** 전부 `describe-*` / `list-*` / `get-*` / `kubectl get` / `sts get-caller-identity` |
| 비밀 값 | 기록·출력하지 않았다. 자격증명은 `access_name`으로만 지칭 |

명령은 요약해 적는다. 반복 호출(인벤토리 53건)은 스크립트 단위로 묶었다.

---

## 0.0 도구 · 사전 조건

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 12:31 | `aws --version` · `terraform version` · `git --version` · `kubectl version` | 도구 확인 | aws 2.28.21 / **Terraform 1.14.3** / git 2.52.0 / **kubectl 미설치** |
| 12:33 | `Get-Command docker` | D-10 판단 | **부재** → bastion 빌드 확정 |
| 12:33 | `Get-Command winget` · `python --version` · `npm --version` · `Get-Command tar` | 보조 도구 | winget 있음 / python 3.11.9 / npm 11.17.0 / tar 있음 |
| 12:35 | `Invoke-WebRequest https://dl.k8s.io/release/stable-1.36.txt` → `kubectl.exe` 다운로드 | 클러스터(1.36)와 맞는 kubectl 설치 | **v1.36.4** → `%USERPROFILE%\.local\bin\kubectl.exe` |
| 12:36 | `[Environment]::SetEnvironmentVariable('PATH', …, 'User')` | kubectl PATH 등록 | 완료 |
| 12:36 | `[Environment]::SetEnvironmentVariable('TF_DATA_DIR', …, 'User')` | OneDrive 잠금·지연 회피 (PRD §10.4) | `%LOCALAPPDATA%\terraform-data\ai-poc` |
| 12:37 | `config.env` 키별 채움 여부 확인 (값 미출력) | 배포 선행 조건 | 존재. 빈 키 7개 · **`CREDENTIALS_FILE=test.json` 파일 부재 → F19** |

**주의 1** — `Invoke-WebRequest`는 `$ProgressPreference='SilentlyContinue'` 없이 실행하면 진행률 표시가 출력을 수 MB 규모로 오염시킨다. 이후 모든 명령에 설정했다.

---

## 0.1 Git 기준선 · Overlay 스냅샷

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 12:42 | `git config --global user.name/user.email` (조회만) | 커밋 가능 여부 | `Gripeace` / `ksb469344@gmail.com` — **git config 수정 안 함** |
| 12:42 | `git -C .. rev-parse --show-toplevel` | 상위 저장소 존재 확인 | 없음 (루트에 git 없음) |
| 12:43 | `AI-POC/.gitignore` 생성 | 설계 §5.6 + `deploy/config.env` | 완료 |
| 12:44 | `git init -b main` (in `AI-POC/`) | 기준선 저장소 | 완료 |
| 12:45 | `git add -A` → `git check-ignore -v deploy/config.env` | 비밀 파일 제외 확인 | `.gitignore:19`에서 차단 확인 |
| 12:46 | `AI-POC/.gitattributes` 생성 | `*.sh`/`*.yaml` `eol=lf` 고정 | CRLF 체크아웃 시 bastion `bash`가 `$'\r'`로 죽는 것을 예방 |
| 12:47 | `git commit` + `git tag -a baseline` | A2 기준선 확정 | 커밋 **`7ebee61`**, 태그 `baseline`, 추적 **79개**, working tree clean |
| 12:50 | `Get-ChildItem 이매지너스 -Recurse -File` 규모 측정 | 해시 범위 판단 | **195개 / 1131.3 MB** (노이즈 44개 = 1092.5 MB) |
| 12:52 | 최대 파일 속성 확인 | 다운로드 위험 판단 | `ReparsePoint` = **OneDrive 클라우드 전용**. 해시 시 1.1GB 내려받음 |
| 12:55 | `Get-FileHash -Algorithm SHA256` (151개) + 나머지 44개는 크기·수정시각 기록 | H1 준수 증명용 스냅샷 | `docs/run-log/overlay_hash_start.txt` — SHA256 151 / SKIPPED 44 / 총 195 |
| 12:58 | 헤더 카운트 재작성 | 버그 수정 | `"$hashed개"`를 PowerShell이 **한글 포함 변수명** `$hashed개`로 파싱해 빈 값이 됨 → `-f` 포맷으로 교체 |

**결정 기록** — Overlay 해시에서 `.terraform`·`.build`·`node_modules`·`__pycache__`(44개, 1092.5 MB)는 SHA256을 계산하지 않고 크기+수정시각으로 대체했다. 재생성 가능한 캐시이고 OneDrive 클라우드 전용 상태라 해시하면 1.1GB를 내려받는다. Phase 5.4.3 비교 시 같은 기준을 쓴다.

---

## 0.2 자격증명 탐색 · 프로필

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 12:38 | 루트·`AI-POC/` 재귀 `Select-String` (`aws_access_key_id`/`access_key`/`secret_key`/`AKIA…`/`sso_start_url`) — **경로만 출력** | 자격증명 후보 식별 | 16개 파일 히트. 실제 저장소는 `통합계정.json`, `이매지너스/이매지너스.json` 2개 |
| 12:20 | `aws configure set` × 4 (`ai-poc-hub`) | 프로필 구성 | 완료 (`access_name = kiro-iam-mtx`, 값 미출력) |
| 12:21 | `aws sts get-caller-identity --profile ai-poc-hub` | 계정 확인 (D-01) | 계정 `730335451955`, `arn:…:user/kiro-iam` |

이매지너스 계정 프로필은 만들지 않았다 (H4). `이매지너스/이매지너스.json`과 `terraform_infra/terraform.tfstate(.backup)`는 열지 않았다.

---

## 0.3 리전 · 대상 확정

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 12:22 | `ec2 describe-vpcs --filters Name=tag:Name,Values=AI-POC --region ap-northeast-2` | PRD 가정 검증 | **0건** → F1 |
| 12:23 | `ec2 describe-vpcs --region ap-northeast-2` | 실제 VPC 목록 | `AI-poc-vpc`(`vpc-0de9ff…`, 100.10.0.0/20), `Grafana-vpc`(`vpc-06a99f…`, 10.1.0.0/16) |
| 12:23 | `eks list-clusters --region ap-northeast-2` | 클러스터 목록 | `AI-POC-eks`, `eks-metaclouds` |
| 12:24 | `eks describe-cluster --name eks-metaclouds` | 범위 판정 | `Grafana-vpc` 소재 → 제외 확정 |

---

## 0.4 인벤토리 수집

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 13:02 | 인라인 함수로 배치 수집 시도 | — | **실패.** 함수 파라미터명을 `$args`로 써서 PowerShell 예약 자동변수와 충돌 |
| 13:05 | `.ps1` 파일로 전환 후 실행 | — | **실패.** AWS 호출은 전부 성공(exit 0)했으나 파일 쓰기가 전부 실패 — Windows PowerShell 5.1이 BOM 없는 `.ps1`을 **ANSI로 읽어** 스크립트 안의 한글 경로가 깨짐 |
| 13:08 | 스크립트를 **ASCII 전용**으로 재작성 + 출력 경로를 `$PWD`에서 파생 (`cwd`는 도구가 정상 전달) | 위 문제 회피 | 39개 저장 성공 |
| 13:12 | 저장 결과 파싱 검증 | 무결성 확인 | **`eip-addresses.json` 파싱 실패** — 한글 EIP Name 태그가 `?`로 깨져 JSON 문자열이 무효화됨 |
| 13:15 | `[Console]::OutputEncoding = UTF8` 추가 + 파일별 `ConvertFrom-Json` 검증 내장 + 수집 항목 확장 후 재실행 | 인코딩 교정 및 확대 | **53개 전부 `JSON-OK` / exit=0.** `eip-addresses.json` 5520→5522 bytes (한글 복원) |
| 13:20 | `ssm describe-instance-information` | bastion 가용성 | `i-0ca57a18e69b73a1f` **Online**, agent 3.3.4624.0, Amazon Linux |
| 13:24 | SSM `AWS-RunShellScript` → bastion에서 읽기 전용 `kubectl`/`helm` (base64 래핑) | K8s 인벤토리 (0.4.6) | Success. **출력이 24KB에서 잘림** — SA용 go-template이 nil 어노테이션에서 오류 블롭을 뿜어 예산 소진 |
| 13:30 | SA 조회를 `-o custom-columns`로 교체하고 잘린 섹션만 재수집 | 누락 보완 | Success. pvc·sc·hpa·pdb·netpol·crd·helm·이미지 버전·bastion 도구 확보 |

수집 산출물: `AI-POC/infra/inventory/hub/` — JSON 53개 + `k8s-existing-cluster.txt` + `k8s-existing-cluster-part2.txt`.

**주의 2** — AWS CLI는 stdout에 UTF-8을 쓴다. `[Console]::OutputEncoding`을 UTF-8로 설정하지 않으면 셸이 ANSI 코드페이지로 디코딩해 한글 태그 값이 `?`가 되고 **JSON이 무효가 된다.** Overlay도 같은 계열 문제를 겪어 SSM 출력을 base64로 감쌌다(history §12, `ssm-run.ps1`). `config.env.example`의 "PowerShell `Get-Content`/`Set-Content`로 편집하지 말라"는 경고도 같은 뿌리다.

**주의 3** — bastion에서 `aws eks update-kubeconfig`가 `/root/.kube/config`를 쓴다. 이는 bastion 파일시스템 쓰기이고 **클러스터 변경이 아니다**. 기존 도구(`deploy/bastion.ps1`)와 같은 경로다. 클러스터에 대해서는 `get`/`version`만 실행했다.

---

## 0.5 Protected 목록

| 시각 | 명령 | 목적 | 결과 |
|---|---|---|---|
| 13:33 | 전체 재귀 `Select-String` — `gpu|nvidia|cuda|g6e|g4dn|l40s|device-plugin|video[-_]gen|wan22|wan_client|VIDEO_GEN` | GPU 관련 전수 식별 | 19개 파일 히트 (server.py 72, app.js 54, README 33, wan_client.py 31, graph_store.py 28 …) |
| 13:34 | 파일별 심볼·라우트·id 추출 | 보호 단위 특정 | server.py Wan 함수 14개 / graph_store.py wanjob 함수 7개 / wan_client.py 최상위 32개 / index.html `wan*` id 62개 |
| 13:35 | README "옮기지 않은 것" 목록의 `Test-Path` 검증 | Base의 GPU 자산 부재 확인 | **10개 항목 전부 부재 확인** (`gpu-*.sh`·`wan22-*.sh`·`video-gen-*` 0건) |
| 13:35 | `python -c "ast…"` 최상위 심볼 집계 | A2 기준선 | 16 파일 / **666 심볼** |

---

## 0.6 변경 원장

| 시각 | 명령 / 작업 | 목적 | 결과 |
|---|---|---|---|
| 12:15 | `이매지너스/이매지너스_history.md` 읽기 (1~558행 전체) | 원장 근거 | 13개 절. 루트에는 파일 없음 → D-03 확정 |
| 12:16 | `이매지너스/terraform_infra/` 파일 목록 | 인프라 추가분 식별 | 45개. `eks_nodegroups.tf` `kubernetes_gpu.tf` `neptune.tf` `scheduler.tf` `alb_tls.tf` `imports.tf` 등. `_inventory/`는 **빈 디렉터리**(F7) |
| 13:37 | Overlay↔Base 전 파일 SHA256 비교 | 영향 경로 확정 | NEW 29 / MODIFIED 19 / IDENTICAL 58 / **BASE_ONLY 0** |
| 13:38 | `app/src` 단위 비교 | GPU 코드 변경 여부 | `wan_client.py` `video_prompt.py` `stitcher.py` **IDENTICAL** — Overlay는 GPU 코드를 지우지 않았다 |

산출물: `docs/01_change_ledger.md` (L-01 ~ L-61).

---

## 0.7 산출물

| 파일 | 내용 |
|---|---|
| `docs/00_hub_discovery.md` | 계정·리전·인벤토리·Protected 목록·제외 대상·F13~F21 |
| `docs/01_change_ledger.md` | L-01~L-61, 액션 분류, 파일 분류 실측 |
| `docs/run-log/phase0.md` | 이 파일 |
| `docs/run-log/overlay_hash_start.txt` | Overlay 무결성 스냅샷 (195 파일) |
| `infra/inventory/hub/*.json` | AWS 인벤토리 53건 |
| `infra/inventory/hub/k8s-existing-cluster*.txt` | K8s 실측 2건 |
| `.gitignore` · `.gitattributes` | 신규 |
| git | 커밋 `7ebee61` + 태그 `baseline` |

---

## 실행 중 만든 임시 파일 (저장소 밖)

| 경로 | 용도 |
|---|---|
| `%TEMP%\ai-poc-inventory.ps1` | AWS 인벤토리 수집 |
| `%TEMP%\ai-poc-k8s-inventory.ps1` | K8s 인벤토리 (1차) |
| `%TEMP%\ai-poc-k8s-inventory2.ps1` | K8s 인벤토리 (잘린 섹션) |

저장소를 오염시키지 않기 위해 `%TEMP%`에 두었다. 반복 사용이 필요하면 Phase 5에서 `infra/` 아래 정식 스크립트로 옮기는 것을 검토한다.

---

## 미해결 · 다음 Phase로 넘기는 항목

1. **게이트 0 승인 대기** — D-14(GPU) · D-15(엔드포인트) · D-16(동등성 기준선) · D-18(VPC 명칭) 미결. D-14 없이 Phase 3 착수 불가.
2. **F19** — `config.env`의 `CREDENTIALS_FILE` / `CREDENTIALS_ACCESS_NAME` 불일치로 `preflight.ps1`이 exit 1. 2줄 수정으로 해소되나 Phase 0 범위가 아니라 손대지 않았다.
3. **F20** — `deploy.ps1`의 `Get-SourceStamp -RepoRoot $workspaceRoot`가 git 저장소를 워크스페이스 루트로 가정. git을 `AI-POC/`에 초기화했으므로 Phase 4에서 확인 필요.
4. **F13 / F14** — `AI-poc-ALB`·`AI-poc-ALB-SG`(GitLab 소유), 고아 route table `rtb-0d34269ef6e001717`의 import 포함 여부.
5. **L-57** — Claude Sonnet 5 / Opus 5 접근 가능 여부를 Hub에서 미확인. Phase 4 전 `bedrock list-foundation-models` 필요.
6. 사용자가 제안한 **범위 변경**(DEV 클러스터 신설 대신 기존 `AI-POC-eks`에 배포, 이매지너스 병합 보류)이 미확정. Phase 1~3의 대상이 이 결정에 따라 달라진다.
