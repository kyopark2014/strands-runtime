# ADR 0003: MCP 서버를 Runtime 이미지에 임베드

## Status

Accepted

## Context

에이전트는 web search, docs, 도메인 도구 등 MCP 도구가 필요하다. MCP를 별도 원격 서비스로 두면 인증·네트워킹·배포 단위가 늘고, VPC private Runtime에서 도달성 설계가 복잡해진다.

## Decision

선택한 MCP 서버를 **AgentCore Runtime Docker 이미지에 임베드**하고, `mcp.list` / config로 활성 서버를 고른다. Runtime 프로세스 로컬에서 MCPClientManager가 연결한다.

## Alternatives considered

| 대안 | 기각 사유 |
|------|-----------|
| 원격 MCP(HTTP) 전용 | 인증·TLS·egress·가용성 부담; 데모/파일럿에서 운영 복잡 |
| Tool을 전부 커스텀 Python 함수로 작성 | MCP 생태계·표준 프로토콜 재사용 포기 |
| ECS sidecar로 MCP 분리 | 배포·스케일 동기화 비용 증가 |

## Consequences

- 이미지에 도구 의존성이 포함되어 빌드 시간이 길어진다.
- MCP 추가/제거는 이미지 재빌드 또는 config 선택으로 반영한다.
- private subnet에서도 로컬 stdio/MCP로 도구를 붙일 수 있다(외부 HTTP가 필요한 도구는 NAT/endpoint 필요).
