# ADR 0005: Cognito + HMAC 세션 쿠키 인증

## Status

Accepted

## Context

Web UI는 브라우저 클라이언트를 가지므로 IdP 기반 로그인과, ALB/CloudFront 뒤에서의 세션 유지가 필요하다. Agent 호출 시 사용자 식별자(`agent_user_id`)를 안정적으로 전달해야 하며, S3 아티팩트는 CloudFront Signed Cookies로 보호한다.

## Decision

**Amazon Cognito User Pool**(USER_PASSWORD_AUTH)로 로그인하고, 서버가 **HMAC 서명 세션 쿠키**를 발급한다. CloudFront Signed Cookies로 `/artifacts` 등 정적 경로를 보호한다.

## Alternatives considered

| 대안 | 기각 사유 |
|------|-----------|
| Cognito Hosted UI / OIDC만 | SPA+커스텀 로그인 UX·Signed Cookie 발급 흐름과 맞춤 비용 |
| IAM Identity Center | 외부/데모 사용자 온보딩이 무거움 |
| API Key only | 사용자별 세션·감사·로그아웃 UX 부족 |

## Consequences

- Cognito 사용자 풀·앱 클라이언트·시크릿(HMAC key) 운영이 필요하다.
- CloudFront→ALB 구간 proto 헤더를 고려한 secure cookie 판별 로직이 필요하다.
- 프론트는 AWS SDK로 Bedrock 등을 직접 호출하지 않고 백엔드 API만 호출한다.
