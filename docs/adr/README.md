# Architectural Decision Records (ADRs)

이 디렉터리는 프로토타입에 구현된 주요 아키텍처 선택의 **맥락·결정·대안·결과**를 기록합니다.

| ID | 결정 | 상태 |
|----|------|------|
| [0001](./0001-agentcore-runtime.md) | AgentCore Runtime으로 에이전트 호스팅 | Accepted |
| [0002](./0002-strands-sdk.md) | Strands Agents SDK를 에이전트 프레임워크로 채택 | Accepted |
| [0003](./0003-embedded-mcp.md) | MCP 서버를 Runtime 이미지에 임베드 | Accepted |
| [0004](./0004-session-persistence-s3-files.md) | FileSessionManager + S3 Files로 세션 영속화 | Accepted |
| [0005](./0005-cognito-hmac-auth.md) | Cognito + HMAC 세션 쿠키 인증 | Accepted |

비용 관점의 참고는 루트 [README 비용 추정 기준](../../README.md#비용-추정-기준)을 참고하세요.
