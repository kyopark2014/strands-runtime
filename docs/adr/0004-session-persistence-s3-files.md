# ADR 0004: FileSessionManager + S3 Files 세션 영속화

## Status

Accepted

## Context

태스크별 대화 이력·에이전트 상태가 Runtime 버전 업데이트 후에도 유지되어야 한다. AgentCore managed session storage만 쓰면 런타임 버전 교체 시 세션이 초기화될 수 있다. ECS Web UI의 task DB도 동일하게 영속이 필요하다.

## Decision

Strands **FileSessionManager**를 `/mnt/workspace`에 두고, 해당 경로를 **S3 Files**로 마운트한다. ECS의 `tasks.db`도 S3 Files 기반 영속을 사용한다.

## Alternatives considered

| 대안 | 기각 사유 |
|------|-----------|
| AgentCore managed session only | 버전 교체 시 세션 유실 위험 |
| EFS | 가능하나 S3 Files가 이 워크로드의 객체/세션 prefix 모델과 더 단순하게 맞음 |
| DynamoDB에 메시지 저장 | 파일·아티팩트·SQLite task store와 이중 모델이 됨 |

## Consequences

- 스토리지 GB-month·마운트 처리 비용이 발생한다.
- Runtime/ECS 모두 동일 영속 계층을 공유하는 설계가 가능하다.
- 미사용 세션 prefix cleanup·retention 정책이 운영 과제가 된다.
