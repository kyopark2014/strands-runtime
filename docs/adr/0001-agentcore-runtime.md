# ADR 0001: AgentCore Runtime으로 에이전트 호스팅

## Status

Accepted

## Context

Web UI와 에이전트 런타임을 분리해 배포해야 한다. 장시간 SSE 스트리밍, 세션 격리, Bedrock 연동, VPC private subnet 배치가 필요하며, 범용 컨테이너 오케스트레이션만으로는 에이전트 전용 운영(세션·observability·권한 모델)을 직접 조립해야 한다.

## Decision

Amazon Bedrock **AgentCore Runtime**에 Strands 에이전트 Docker 이미지를 배포한다. Web UI(ECS)는 `invoke_agent_runtime`으로 SSE를 수신한다.

## Alternatives considered

| 대안 | 기각 사유 |
|------|-----------|
| ECS/Fargate에 에이전트도 함께 배포 | 세션 격리·에이전트 전용 메트릭·버전 롤아웃을 직접 구축해야 함 |
| Lambda + API Gateway | SSE/장시간 추론에 적합하지 않은 동기 한도 |
| 로컬 프로세스만 | 다중 사용자·프로덕션 경로 부재 |

## Consequences

- Runtime은 private subnet + VPC endpoint/NAT로 AWS·외부 도구에 접근한다.
- 이미지 크기·아키텍처(arm64 등)는 AgentCore 제약에 맞춰야 한다.
- Web UI와 에이전트 수명 주기가 분리되어 독립 배포가 가능하다.
