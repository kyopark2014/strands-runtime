# ADR 0002: Strands Agents SDK 채택

## Status

Accepted

## Context

에이전트는 Bedrock 모델 호출, tool/MCP/Skill 조합, 대화 이력·세션 관리가 필요하다. 프레임워크 없이 직접 조립하면 tool loop·스트리밍·세션 직렬화를 모두 자체 구현해야 한다.

## Decision

**Strands Agents SDK**(`Agent`, `BedrockModel`, `FileSessionManager`, strands_tools)를 에이전트 런타임 프레임워크로 사용한다.

## Alternatives considered

| 대안 | 기각 사유 |
|------|-----------|
| LangChain / LangGraph | 가능하나 AgentCore·Skill/MCP 패턴과의 정합·이미지 경량화 측면에서 Strands가 더 직접적 |
| 순수 boto3 Converse loop | tool/MCP/Skill/세션 관리 비용이 큼 |
| Bedrock Agents(managed) | 커스텀 Skill·임베디드 MCP·로컬 파일시스템 워크스페이스 제어가 제한적 |

## Consequences

- `runtime_agent/strands/`가 SDK 관용 구조(agent entry, skills, mcp)를 따른다.
- Prompt caching·세션 매니저 등 SDK 기능을 활용할 수 있다.
- SDK API 변경 시 Runtime 이미지 재빌드가 필요하다.
