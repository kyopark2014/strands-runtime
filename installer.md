# AWS Infrastructure Installer

boto3를 사용하여 AWS 인프라 리소스를 생성하는 Python 스크립트입니다.  
CDK 스택과 동등한 AWS 인프라를 프로그래밍 방식으로 배포합니다.

## 목차

1. [개요](#개요)
2. [설정값](#설정값)
3. [생성되는 리소스](#생성되는-리소스)
4. [주요 함수](#주요-함수)
5. [실행 방법](#실행-방법)
6. [배포 순서](#배포-순서)

---

## 개요

이 스크립트는 AI 기반 채팅 애플리케이션을 위한 전체 AWS 인프라를 자동으로 생성합니다.

- **Streamlit UI** (`application/`) → ECS Fargate
- **Strands Agent Runtime** (`runtime_agent/strands/`) → AgentCore Runtime (배포 중 `install_agent_runtime()` 호출)

### 주요 특징
- **완전 자동화**: 단일 스크립트로 전체 인프라 배포
- **멱등성**: 이미 존재하는 리소스는 재사용
- **에러 핸들링**: 각 단계별 예외 처리 및 부분 배포 정보 저장
- **로깅**: 상세한 배포 진행 상황 출력
- **S3 Vectors 기반 RAG**: Bedrock Knowledge Base가 OpenSearch Serverless 대신 S3 Vectors를 벡터 스토어로 사용
- **ECS Fargate 배포**: Dockerfile 기반 이미지를 ECR에 push한 뒤 ECS Fargate 서비스로 실행
- **AgentCore 연동**: Web Search Gateway 생성 및 Strands Agent Runtime 자동 배포
- **S3 Files 세션 스토리지**: AgentCore Runtime용 `/mnt/workspace` 영속 마운트 (Version 업데이트 후에도 세션 유지)

### 사전 요구사항
- **Docker CLI**: 로컬에서 컨테이너 이미지 빌드 및 ECR push
- **AWS CLI**: ECR 로그인 (`aws ecr get-login-password`)
- **boto3** 및 스크립트 실행에 필요한 AWS 자격 증명
- **IAM 권한**: EC2/로컬에서 installer를 실행하는 주체는 아래 작업 권한이 필요합니다.
  - S3, IAM, VPC, ECS, ECR, CloudFront, Bedrock Agent, S3 Vectors, AgentCore Control, **S3 Files** (`s3files`)
  - Knowledge Base 생성 시 `iam:PassRole` (Knowledge Base 서비스 역할에 대해)
  - AgentCore Runtime 배포 시 `runtime_agent/strands/installer.py` 추가 권한

---

## 설정값

```python
# 기본 설정
project_name = "strands-runtime"   # 프로젝트 이름 (최소 3자)
region = "us-west-2"               # AWS 리전
git_name = "strands-runtime"       # Git 저장소 이름 (레거시 EC2 SSM 배포용)

# AgentCore Web Search Gateway
AGENTCORE_GATEWAY_REGION = "us-east-1"
AGENTCORE_WEBSEARCH_GATEWAY_NAME = "gateway-websearch"
AGENTCORE_WEBSEARCH_TARGET_NAME = "websearch"

# 자동 생성되는 변수
account_id = sts_client.get_caller_identity()["Account"]
bucket_name = f"storage-for-{project_name}-{account_id}-{region}"
vector_bucket_name = f"{project_name}-{account_id}"
vector_index_name = project_name

# 벡터 인덱스 설정
embedding_dimensions = 1024
embedding_data_type = "float32"
distance_metric = "cosine"

# Bedrock Knowledge Base 필수 메타데이터 (S3 Vectors non-filterable)
BEDROCK_NON_FILTERABLE_METADATA_KEYS = [
    "AMAZON_BEDROCK_TEXT",
    "AMAZON_BEDROCK_METADATA",
]

# S3 Files (AgentCore session storage)
S3_FILES_SESSION_PREFIX = "agentcore-sessions/"

# 커스텀 헤더 (CloudFront-ALB 통신용)
custom_header_name = "X-Custom-Header"
custom_header_value = f"{project_name}_12dab15e4s31"
```

---

## 생성되는 리소스

### 1. S3 버킷
- **이름**: `storage-for-{project_name}-{account_id}-{region}`
- **설정**:
  - CORS 활성화 (GET, POST, PUT)
  - 퍼블릭 액세스 차단
  - 버전 관리 **Enabled** (S3 Files file system 생성 필수; 기존 bucket은 `create_s3_files_session_storage` 시 자동 활성화)
  - `docs/` 폴더 자동 생성

### 2. IAM 역할

| 역할 | 설명 |
|------|------|
| `role-knowledge-base-for-{project_name}-{region}` | Bedrock Knowledge Base용 역할 (S3, S3 Vectors, Bedrock 모델 접근) |
| `role-agent-for-{project_name}-{region}` | Bedrock Agent용 역할 |
| `role-ecs-task-for-{project_name}-{region}` | ECS 태스크용 역할 (Bedrock, S3, Secrets Manager, PassRole 등) |
| `role-ecs-execution-for-{project_name}-{region}` | ECS 태스크 실행 역할 (ECR pull, CloudWatch Logs) |
| `role-agentcore-gateway-websearch-for-{project_name}` | AgentCore Web Search Gateway 서비스 역할 |
| `role-s3files-sync-for-{project_name}` | S3 Files ↔ S3 bucket 동기화 역할 (`elasticfilesystem.amazonaws.com` trust) |

> AgentCore Runtime 실행 역할(`AmazonBedrockAgentCoreRuntimeRoleFor{project_name}`)은 `runtime_agent/strands/installer.py`에서 생성하며, S3 Files 사용 시 `s3files:ClientMount` 등 권한이 조건부로 추가됩니다.

> `create_lambda_role()`, `create_agentcore_memory_role()` 함수는 코드에 남아 있으나, 현재 `main()` 배포 흐름에서는 호출되지 않습니다.

#### Knowledge Base 역할 Trust Policy

Bedrock 서비스가 역할을 assume할 수 있도록 AWS 권장 형식을 사용합니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "{account_id}" },
      "ArnLike": {
        "aws:SourceArn": "arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"
      }
    }
  }]
}
```

역할 생성 후 IAM 전파를 위해 15초 대기하고, inline policy 부착 여부를 확인합니다 (`wait_for_iam_role_propagation()`).

### 3. S3 Vectors (벡터 스토어)
- **벡터 버킷**: `{project_name}-{account_id}`
- **인덱스**: `{project_name}` (1024차원, cosine, float32)
- **메타데이터**: Bedrock 필수 키(`AMAZON_BEDROCK_TEXT`, `AMAZON_BEDROCK_METADATA`)를 non-filterable로 설정

### 4. Bedrock Knowledge Base
- **스토리지**: S3 Vectors (`S3_VECTORS` 타입)
- **임베딩 모델**: Amazon Titan Embed Text v2 (1024차원, FLOAT32)
- **파싱**: 기본 파서 (default parser)
- **청킹**: Fixed Size (300 토큰, 20% 오버랩)
- **데이터 소스**: S3 `docs/` 프리픽스
- **재시도**: 역할 assume 실패(`ValidationException`) 시 최대 6회 재시도

> `create_opensearch_collection()` 함수는 이전 버전 호환을 위해 코드에 남아 있으나, 현재 배포 흐름에서는 사용하지 않습니다.

### 5. AgentCore Web Search Gateway
- **리전**: `us-east-1` (`AGENTCORE_GATEWAY_REGION`)
- **게이트웨이 이름**: `gateway-websearch`
- **역할**: `role-agentcore-gateway-websearch-for-{project_name}`
- MCP `websearch` 도구에서 사용하는 AgentCore Gateway URL이 `application/config.json`에 기록됩니다.

### 6. VPC 네트워킹

```
VPC (10.20.0.0/16)
├── Public Subnets (2개 AZ)
│   ├── Internet Gateway 연결
│   └── NAT Gateway 호스팅
├── Private Subnets (2개 AZ)
│   └── NAT Gateway를 통한 아웃바운드 (ECR pull, Bedrock API 등)
├── Security Groups
│   ├── ALB SG (포트 80)
│   ├── ECS SG (포트 8501, 443)
│   ├── agent-runtime-sg-for-{project_name} (AgentCore microVM)
│   └── s3files-mount-sg-for-{project_name} (NFS 2049)
└── VPC Endpoints
    └── Bedrock Runtime 엔드포인트 (agent runtime SG 추가 연결)
```

### 6.5. S3 Files (AgentCore Session Storage)

VPC 생성 직후 `create_s3_files_session_storage()`가 아래를 **멱등**으로 프로비저닝합니다.

| 리소스 | 설명 |
|--------|------|
| Sync IAM role | `role-s3files-sync-for-{project_name}` — S3 bucket ↔ NFS 동기화 |
| File system | bucket `storage-for-...`, prefix `agentcore-sessions/` |
| Mount targets | private subnet마다 1개 (Runtime과 AZ 정렬) |
| Access point | `/mnt/workspace` 마운트용 (`posix uid/gid: 0/0`) |

`apply_s3_files_config()`가 `application/config.json`에 `s3_files_*`, `agent_runtime_vpc_*` 키를 기록합니다.  
`runtime_agent/strands/installer.py`는 access point ARN이 있으면 **`s3FilesAccessPoint` + VPC 모드**, 없으면 managed **`sessionStorage` + PUBLIC** 으로 Runtime을 생성합니다.

### 7. Application Load Balancer
- **타입**: Internet-facing Application Load Balancer
- **리스너**: HTTP 포트 80
- **타겟 그룹**: ECS Fargate 태스크 (IP 타겟, 포트 8501)
- **헬스체크**: `/_stcore/health`

### 8. CloudFront 배포
- **오리진**:
  - 기본: ALB (동적 컨텐츠)
  - `/images/*`, `/docs/*`: S3 (정적 컨텐츠)
- **캐시 정책**: Managed-CachingDisabled
- **프로토콜**: HTTP → HTTPS 리다이렉트

### 9. ECR (Elastic Container Registry)
- **리포지토리**: `ecr-for-{project_name}`
- **이미지 태그**: 빌드 시 타임스탬프 기반 태그 + `latest`
- **플랫폼**: `linux/amd64`
- **빌드 소스**: 프로젝트 루트의 `Dockerfile` (Streamlit UI)

### 10. ECS Fargate
- **클러스터**: `cluster-for-{project_name}`
- **서비스**: `service-for-{project_name}`
- **태스크 정의**: `task-for-{project_name}`
- **컨테이너**: `app` (포트 8501)
- **CPU / Memory**: 1024 / 2048
- **배포 위치**: Private Subnet (퍼블릭 IP 없음)
- **로그**: CloudWatch Logs `/ecs/app-for-{project_name}`

### 11. AgentCore Runtime (Strands)
- VPC·S3 Files 프로비저닝 **후**, CloudFront 생성 직후 `install_agent_runtime("strands")` → `runtime_agent/strands/installer.py` subprocess 실행
- ECR에 **arm64** Runtime 이미지 push, AgentCore Runtime 생성/갱신
- **Session storage**: S3 Files `s3FilesAccessPoint` @ `/mnt/workspace` + `networkMode: VPC` (기본)
- Runtime 내 `FileSessionManager`가 `/mnt/workspace/session_<id>/`에 대화·agent state 저장
- 결과(`agent_runtime_arn`, `agent_runtime_role` 등)는 `runtime_agent/strands/config.json` → `application/config.json`으로 병합

---

## 주요 함수

### 인프라 생성 함수

#### `create_s3_bucket()`
S3 버킷 생성, CORS·퍼블릭 액세스 차단, **versioning Enabled** (S3 Files 요구사항)

#### `create_iam_role()` / `attach_inline_policy()`
IAM 역할 생성, Trust Policy 갱신, 인라인 정책 연결

#### `create_knowledge_base_role()`
Knowledge Base 서비스 역할 생성

- Trust Policy: `_bedrock_knowledge_base_trust_policy()` (Bedrock + SourceAccount + SourceArn)
- Inline: Bedrock 모델 호출, S3, S3 Vectors(버킷·인덱스 ARN), Bedrock Agent 정책
- 생성 후 `wait_for_iam_role_propagation()` 호출

#### `create_agent_role()` / `create_ecs_roles()`
Bedrock Agent 역할 및 ECS Task/Execution 역할 생성

`create_ecs_roles()`는 아래 두 역할을 반환합니다.

```python
{
    "task_role_arn": "...",
    "execution_role_arn": "...",
}
```

ECS Task 역할에는 Knowledge Base 역할에 대한 `iam:PassRole` 권한이 포함됩니다.

#### `create_agentcore_websearch_gateway_role()` / `get_or_create_agentcore_websearch_gateway()`
AgentCore Web Search Gateway IAM 역할 및 Gateway/Target 생성

#### `create_s3_vectors_store()`
S3 Vectors 벡터 버킷 및 인덱스 생성

```python
def create_s3_vectors_store() -> Dict[str, str]:
    return {
        "vectorBucketName": vector_bucket_name,
        "vectorBucketArn": vector_bucket_arn,
        "indexName": vector_index_name,
        "indexArn": index_arn,
    }
```

#### `create_knowledge_base_with_s3_vectors()`
S3 Vectors를 스토리지로 사용하는 Bedrock Knowledge Base 생성

```python
def create_knowledge_base_with_s3_vectors(
    s3_vectors_info: Dict[str, str],
    knowledge_base_role_arn: str,
    s3_bucket_name: str,
) -> Tuple[str, str]:
    # 기존 KB가 다른 스토리지를 사용하면 삭제 후 재생성
    # Knowledge Base 역할 Trust Policy 검증
    # Knowledge Base 생성 (Titan Embed v2), assume 실패 시 재시도
    # S3 데이터 소스 생성 (docs/ 프리픽스)
    return knowledge_base_id, data_source_id
```

#### `create_vpc()` / `create_alb()` / `create_cloudfront_distribution()`
VPC, ALB, CloudFront 배포

#### `create_s3_files_session_storage(vpc_info, s3_bucket_name)`
AgentCore Runtime용 S3 Files 세션 스토리지 프로비저닝 (멱등).

```python
def create_s3_files_session_storage(
    vpc_info: Dict[str, str],
    s3_bucket_name: str,
) -> Dict[str, object]:
    # 1. _get_or_create_s3files_sync_role()
    # 2. _ensure_s3_bucket_versioning_enabled()
    # 3. _get_or_create_s3files_file_system()  # prefix: agentcore-sessions/
    # 4. agent-runtime-sg + s3files-mount-sg (NFS 2049)
    # 5. _ensure_s3files_mount_targets() per private subnet
    # 6. _get_or_create_s3files_access_point()
    # 7. _add_security_group_to_vpc_endpoint()  # Bedrock endpoint
    return {
        "file_system_id": "...",
        "access_point_arn": "...",
        "subnets": [...],
        "security_groups": [...],
    }
```

#### `apply_s3_files_config(app_config, s3_files_info)`
S3 Files·VPC 키를 `application/config.json` 페이로드에 병합.

#### `install_agent_runtime(runtime_type="strands")`
`runtime_agent/strands/installer.py`를 subprocess로 실행하여 AgentCore Runtime 배포 (S3 Files + VPC 모드 반영)

#### `create_ecr_repository()` / `build_and_push_docker_image()`
ECR 리포지토리 생성 및 Streamlit UI Docker 이미지 빌드·push

#### `deploy_ecs_service()`
ECS Fargate 서비스 배포 (태스크 정의, ALB 연동, `APP_CONFIG_JSON` 환경변수 포함)

#### `build_app_environment()` / `write_application_config()` / `sync_application_capability_lists()`
- `build_app_environment()`: 컨테이너·로컬 개발용 `application/config.json` 내용 생성
- `apply_s3_files_config()`: S3 Files 키를 app config에 병합 (`main()`에서 호출)
- `sync_application_capability_lists()`: `runtime_agent/strands/mcp.list`, `skills.list` → `application/` 복사
- `write_application_config()`: `application/config.json` 저장 (기존 값과 병합)

### 헬퍼 함수

| 함수 | 설명 |
|------|------|
| `s3_vectors_bucket_arn()` / `s3_vectors_index_arn()` | S3 Vectors ARN 생성 |
| `_bedrock_knowledge_base_trust_policy()` | Knowledge Base 역할 Trust Policy 생성 |
| `_principal_allows_service()` | Trust Policy Principal 검증 |
| `wait_for_iam_role_propagation()` | IAM 역할·정책 전파 대기 |
| `ensure_data_source()` | Knowledge Base S3 데이터 소스 생성/조회 |
| `delete_knowledge_base()` | Knowledge Base 및 데이터 소스 삭제 |
| `_merge_runtime_agent_settings()` | `runtime_agent/strands/config.json` → application config 병합 |
| `_apply_websearch_gateway_config()` | Web Search Gateway 설정을 config에 추가 |
| `build_config_from_deployment_state()` | 부분 배포 상태로 config.json 생성 |
| `check_application_ready()` | CloudFront URL 애플리케이션 준비 상태 확인 |
| `create_security_group()` / `create_vpc_endpoint()` | VPC 보안 그룹·엔드포인트 |
| `_ensure_s3_bucket_versioning_enabled()` | S3 bucket versioning Enabled (S3 Files 필수) |
| `_get_or_create_s3files_sync_role()` | S3 Files sync IAM role |
| `_get_or_create_s3files_file_system()` | S3 Files file system (prefix `agentcore-sessions/`) |
| `_ensure_s3files_mount_targets()` | private subnet별 mount target |
| `_get_or_create_s3files_access_point()` | S3 Files access point |
| `_add_security_group_to_vpc_endpoint()` | Bedrock VPC endpoint에 runtime SG 추가 |
| `_wait_for_s3files_status()` | S3 Files 리소스 available 폴링 |
| `create_ecs_log_group()` / `create_ecs_cluster()` | ECS 로그·클러스터 |
| `create_alb_target_group_for_ecs()` / `create_alb_listener_with_target_group()` | ALB 타겟 그룹·리스너 |

### 레거시 함수 (main()에서 미사용)

| 함수 | 설명 |
|------|------|
| `create_opensearch_collection()` | OpenSearch Serverless 컬렉션 (구버전 벡터 스토어) |
| `create_lambda_role()` | Lambda 실행 역할 |
| `create_agentcore_memory_role()` | AgentCore Memory 역할 |
| `get_setup_script()` / `run_setup_script_via_ssm()` | EC2 SSM 설정 |
| `create_ec2_instance()` / `verify_ec2_subnet_deployment()` | EC2 배포 (레거시) |

---

## 실행 방법

### 기본 실행 (전체 인프라 배포)

```bash
python installer.py
```

로컬 Docker로 Streamlit UI 이미지를 빌드하고 ECR에 push한 뒤 ECS Fargate 서비스를 생성합니다.  
배포 중 AgentCore Strands Runtime도 자동 설치됩니다.

### Docker 빌드 생략 (기존 ECR 이미지 재사용)

```bash
python installer.py --skip-docker-build
```

ECR의 `{repository_uri}:latest` 이미지를 그대로 사용합니다.

### Agent Runtime만 별도 설치

```bash
python installer.py --install-agent-runtime strands
```

인프라 배포 없이 `runtime_agent/strands/installer.py`만 실행합니다.

### 레거시: EC2 SSM 설정 / 서브넷 검증

```bash
python installer.py --run-setup
python installer.py --run-setup i-1234567890abcdef0
python installer.py --verify-deployment
```

---

## 배포 순서

```
[1/10] S3 버킷 생성
       ↓
[2/10] IAM 역할 생성
       • Knowledge Base 역할 (+ IAM 전파 대기)
       • Agent 역할
       • ECS Task / Execution 역할
       • AgentCore Web Search Gateway 역할 + Gateway 생성
       ↓
[3/10] S3 Vectors 스토어 생성
       • 벡터 버킷 + 인덱스
       ↓
[4.5/10] Bedrock Knowledge Base 생성
       • S3 Vectors 연결
       • S3 데이터 소스 (docs/) 연결
       • assume 실패 시 재시도
       ↓
[5/10] VPC 네트워킹 리소스 생성
       ↓
[5.5/10] S3 Files 세션 스토리지 생성
       • sync role, file system, mount targets, access point
       • agent-runtime-sg / s3files-mount-sg (NFS 2049)
       • application/config.json에 S3 Files·VPC 키 기록
       ↓
[6/10] Application Load Balancer 생성
       ↓
[7/10] CloudFront 배포 생성
       ↓
[8/10] 애플리케이션 설정 및 Agent Runtime 배포
       • mcp.list / skills.list 동기화
       • application/config.json 생성 (S3 Files 키 + sharing_url 포함)
       • runtime_agent/strands/installer.py 실행
         (s3FilesAccessPoint + VPC 또는 sessionStorage fallback)
       ↓
[9/10] ECR + Docker 빌드 + ECS Fargate 서비스 배포
       • ecr-for-{project_name} 생성
       • linux/amd64 Streamlit UI 이미지 push
       • Private Subnet Fargate 서비스 생성
       ↓
[10/10] 애플리케이션 준비 상태 확인
       ↓
완료 - application/config.json 업데이트
```

---

## 배포 완료 후

배포가 완료되면 다음 정보가 출력됩니다:

```
================================================================
Infrastructure Deployment Completed Successfully!
================================================================
Summary:
  S3 Bucket: storage-for-strands-runtime-{account_id}-us-west-2
  VPC ID: vpc-xxxxxxxxx
  Public Subnets: subnet-xxx, subnet-yyy
  Private Subnets: subnet-aaa, subnet-bbb
  ALB DNS: http://alb-for-strands-runtime-xxxxxx.us-west-2.elb.amazonaws.com/
  CloudFront Domain: https://xxxxxxxxx.cloudfront.net
  ECS Service: service-for-strands-runtime (Fargate in private subnet)
  ECR Image: {account_id}.dkr.ecr.us-west-2.amazonaws.com/ecr-for-strands-runtime:...
  S3 Vector Bucket: strands-runtime-{account_id}
  S3 Vector Index ARN: arn:aws:s3vectors:...
  Knowledge Base ID: XXXXXXXXXX
  Knowledge Base Role: arn:aws:iam::...
  AgentCore Web Search Gateway: gateway-websearch (...)
  S3 Files Access Point: arn:aws:s3files:...
  Agent Runtime Subnets: subnet-aaa, subnet-bbb

Total deployment time: XX.XX minutes
================================================================
```

### application/config.json

배포 성공/실패와 관계없이 `finally` 블록에서 `application/config.json`이 갱신됩니다.

| 필드 | 설명 |
|------|------|
| `projectName`, `accountId`, `region` | 프로젝트 기본 정보 |
| `knowledge_base_id`, `data_source_id` | Bedrock Knowledge Base |
| `knowledge_base_role` | Knowledge Base IAM 역할 ARN |
| `vector_bucket_name`, `vector_bucket_arn` | S3 Vectors 버킷 |
| `vector_index_name`, `vector_index_arn` | S3 Vectors 인덱스 |
| `s3_bucket`, `s3_arn` | 문서 저장 S3 버킷 |
| `s3_files_file_system_id` | S3 Files file system ID (AgentCore session storage) |
| `s3_files_access_point_arn` | S3 Files access point ARN |
| `agent_runtime_vpc_subnets` | AgentCore Runtime VPC subnet ID 목록 |
| `agent_runtime_security_groups` | AgentCore Runtime security group ID 목록 |
| `sharing_url` | CloudFront URL |
| `agent_runtime_arn`, `agent_runtime_role` | AgentCore Strands Runtime (`runtime_agent/strands/config.json`에서 병합) |
| `agentcore_websearch_gateway_*` | Web Search Gateway ID, URL, 역할 ARN |
| `latest_image_tag`, `build_number` | ECR 이미지 빌드 태그 |
| `collectionArn`, `opensearch_url` | 레거시 호환용 빈 값 |

ECS 컨테이너에는 `APP_CONFIG_JSON` 환경변수로 동일한 설정이 주입되며, `docker-entrypoint.sh`가 시작 시 `application/config.json`으로 기록합니다.

### Docker Container 구성

ECS에 배포되는 컨테이너는 **Streamlit UI**(`application/app.py`)만 포함합니다. Agent 추론은 AgentCore Runtime(`runtime_agent/strands/`)에서 수행됩니다.

```text
FROM --platform=linux/amd64 python:3.13-slim
WORKDIR /app
# Node.js (npx), curl (헬스체크)
COPY . .
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "-m", "streamlit", "run", "application/app.py", ...]
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health
```

### 주의사항
- CloudFront 배포는 완전히 활성화되기까지 15-20분이 소요될 수 있습니다
- ECS Fargate 서비스가 안정화되고 ALB 헬스체크가 통과하기까지 수 분이 걸릴 수 있습니다
- Knowledge Base 생성 직후 IAM 전파 지연으로 assume 오류가 날 수 있으며, 스크립트가 자동 재시도합니다
- installer 실행 주체(EC2 인스턴스 역할 등)에 Knowledge Base 역할에 대한 `iam:PassRole` 권한이 필요합니다
- Knowledge Base가 기존 OpenSearch Serverless를 사용 중이면 S3 Vectors로 마이그레이션 시 자동 삭제 후 재생성됩니다
- Private Subnet의 Fargate 태스크는 NAT Gateway를 통해 ECR에서 이미지를 pull합니다
- AgentCore Runtime 이미지는 **arm64**이며, Streamlit UI 이미지는 **amd64**입니다
- S3 Files 사용 시 AgentCore Runtime은 **VPC 모드**이며, mount target AZ·SG(2049)가 맞아야 invoke가 성공합니다
- S3 bucket **versioning Enabled**가 없으면 file system 생성이 실패합니다 (`ValidationException`)
- Managed `sessionStorage`만 사용할 경우 Runtime **Version 업데이트 시** `/mnt/workspace` 세션이 초기화됩니다 (S3 Files 권장)

---

## 에러 처리

| 상황 | 처리 방법 |
|------|----------|
| 리소스 이미 존재 | 기존 리소스 재사용 |
| IAM 역할 이미 존재 | Trust Policy 및 inline policy 갱신 |
| KB 역할 assume 실패 | IAM 전파 대기 후 최대 6회 재시도 |
| KB 스토리지 불일치 | Knowledge Base 삭제 후 S3 Vectors로 재생성 |
| ECS 서비스 이미 존재 | 새 태스크 정의로 서비스 업데이트 (`forceNewDeployment`) |
| CIDR 충돌 | 대체 CIDR 블록 자동 선택 |
| S3 Files file system 생성 실패 | bucket versioning 미활성 → `_ensure_s3_bucket_versioning_enabled()` 자동 처리 |
| 배포 실패 | 가능한 배포 정보를 `application/config.json`에 저장 |

배포 실패 시 상세한 에러 메시지와 스택 트레이스가 출력됩니다.

### Knowledge Base assume 역할 오류

```
Bedrock Knowledge Base was unable to assume the given role.
```

확인 사항:
1. `role-knowledge-base-for-{project_name}-{region}` Trust Policy에 `bedrock.amazonaws.com` 포함
2. installer 실행 주체에 `iam:PassRole` 권한
3. IAM 전파 완료 후 재실행 (스크립트 자동 재시도 포함)

### S3 Files file system 생성 오류

```
Your bucket must have versioning enabled to create a file system.
```

- `create_s3_bucket()`은 신규 bucket에 versioning **Enabled** 설정
- 기존 bucket은 `create_s3_files_session_storage()` 내 `_ensure_s3_bucket_versioning_enabled()`가 자동 활성화
- sync role(`role-s3files-sync-for-{project_name}`) 및 S3/EventBridge inline policy 확인

---

## 인프라 삭제

```bash
python uninstaller.py
```

삭제 순서(요약): CloudFront 비활성화 → **AgentCore Runtime** (`runtime_agent/strands/uninstaller.py` 위임) → ECS → ALB → EC2(레거시) → NAT → **S3 Files** (access point / mount target / file system / sync role) → VPC → Knowledge Base / S3 Vectors → Gateway / IAM / S3 bucket → CloudFront 완전 삭제 → **`application/config.json`**, `runtime_agent/strands/config.json` 정리

단독으로 Runtime만 제거할 때는 `runtime_agent/strands/uninstaller.py`를 사용할 수 있습니다.
