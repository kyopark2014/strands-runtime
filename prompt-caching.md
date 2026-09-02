# Prompt Caching

Strands 에이전트는 tool loop마다 동일한 **system prompt + tool schema**를 Bedrock에 다시 보냅니다. [Amazon Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)과 Strands SDK cache 옵션으로 이 정적 prefix를 재사용합니다. 구현은 [`runtime_agent/strands/model_factory.py`](./runtime_agent/strands/model_factory.py)의 `get_model()`에 있습니다.

## 대상 모델

| 경로 | model_type | 모델 예 | 캐싱 방식 |
|------|------------|---------|-----------|
| **Claude / Nova** | `claude`, `nova` | `us.anthropic.claude-sonnet-5` | Strands `CacheConfig` / `cache_tools` / `cache_prompt` |
| **GPT 5.6+ (Mantle)** | `openai` | `openai.gpt-5.6-sol`, `-terra`, `-luna` | Explicit (`prompt_cache_breakpoint` + `prompt_cache_options`) |
| **GPT 5.5 이하 (Mantle)** | `openai` | `openai.gpt-5.5`, `openai.gpt-5.4` | Implicit (AWS 자동, 코드 미적용) |

GPT 5.6+는 Mantle Responses API(`mantle_api: "responses"`)에서 `MantleGPTResponsesModel`로 explicit caching을 사용합니다.

---

## Claude / Nova (Strands `BedrockModel`)

### 적용 방식

1. **`cache_prompt="default"`** — system 끝에 cachePoint (AgentSkills 주입 후)
2. **`cache_tools=CacheToolsConfig(...)`** — tool schema cachePoint
3. **`cache_config=CacheConfig(strategy=..., ttl="5m")`** — 마지막 user message cachePoint
4. **관측** — `AgentResult.metrics`의 `cacheReadInputTokens` / `cacheWriteInputTokens`

```python
# runtime_agent/strands/model_factory.py
def _prompt_cache_kwargs(model_type: str) -> dict:
    strategy = "auto" if model_type == "claude" else "anthropic"
    return {
        "cache_prompt": "default",
        "cache_tools": CacheToolsConfig(type="default", ttl=PROMPT_CACHE_TTL),
        "cache_config": CacheConfig(strategy=strategy, ttl=PROMPT_CACHE_TTL),
    }
```

### 특성

- TTL: **5분**
- tool loop **2번째 LLM 호출부터** `cacheReadInputTokens` 발생이 일반적

---

## GPT 5.6+ (Mantle Responses API)

### 적용 방식

Strands `OpenAIResponsesModel`은 기본적으로 system을 `instructions` 필드로 보냅니다. GPT 5.6 explicit caching은 `input` content block의 `prompt_cache_breakpoint`가 필요하므로, **`MantleGPTResponsesModel`** 이 system을 developer message + breakpoint로 `input` 앞에 삽입합니다.

1. **`prompt_cache_breakpoint`** — system prefix 끝에 explicit breakpoint
2. **`prompt_cache_key` / `prompt_cache_options`** — `params`로 Responses API에 전달
3. **관측** — Strands metrics + CloudWatch (`cached_tokens` fallback 포함)

```python
GPT_PROMPT_CACHE_OPTIONS = {"mode": "explicit", "ttl": "30m"}

# get_model(session_id=runtime_session_id) →
# prompt_cache_key = "{projectName}:{session_id}:strands"
```

### 특성

| 항목 | 값 |
|------|-----|
| API | Mantle Responses |
| TTL | **30분** |
| 최소 prefix | **1,024 tokens** |
| cache read 할인 | **90%** |
| cache write 비용 | uncached input **1.25×** |

---

## 측정 (`test_prompt_caching.py`)

```bash
cd runtime_agent/strands

# Claude (기본)
python test_prompt_caching.py

# GPT 5.6 Mantle
python test_prompt_caching.py --model-id openai.gpt-5.6-sol --region us-east-2
```

## 확인 방법

1. probe 스크립트 실행 또는 tool 2회 이상 사용하는 질의
2. 로그의 `cacheReadInputTokens` / `cacheWriteInputTokens` (Claude) 또는 GPT cache fields
3. CloudWatch: [`cloudwatch_metrics.py`](./runtime_agent/strands/cloudwatch_metrics.py)

## 참고

- [Prompt caching (AWS)](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- [GPT-5.6 explicit caching (AWS Blog)](https://aws.amazon.com/blogs/machine-learning/introducing-explicit-prompt-caching-for-openai-gpt-5-6-models-on-amazon-bedrock/)
- [Strands Amazon Bedrock provider](https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/)
