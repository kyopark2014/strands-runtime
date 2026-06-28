# Session Management

Strands Agent에서 `conversation_manager`와 `session_manager`가 어떻게 동작하는지, 이 프로젝트(`runtime_agent/strands/strands_agent.py`) 구현을 기준으로 정리합니다.

## 한 줄 요약

| 매니저 | 역할 | 저장 위치 |
|---|---|---|
| `conversation_manager` | 모델에 보낼 메시지 **개수/크기 제한** (슬라이딩 윈도우) | 프로세스 메모리 (상태는 session에도 동기화) |
| `session_manager` | 전체 대화·agent state **디스크 저장/복원** | `/mnt/workspace/session_<id>/...` |

`conversation_manager`가 모델 컨텍스트를 관리하고, `session_manager`는 디스크 영속화와 재시작 시 복원을 담당합니다. `session_manager`는 `conversation_manager`가 비었을 때만 쓰이는 fallback이 아니라, **항상 병행**됩니다.

---

## 현재 코드 구조

### conversation_manager — 모듈 레벨 싱글톤

```python
# runtime_agent/strands/strands_agent.py
conversation_manager = SlidingWindowConversationManager(
    window_size=50,
)
```

- import 시 **한 번** 생성되며, 여러 Agent 인스턴스가 **공유**합니다.
- in-memory에서 슬라이딩 윈도우로 `agent.messages`를 trim합니다.

### session_manager — Agent 생성마다 새로 생성

`create_agent()`에서 Strands SDK의 `FileSessionManager`를 사용해 세션을 디스크에 영속화합니다.

```python
# runtime_agent/strands/strands_agent.py
from strands.session.file_session_manager import FileSessionManager
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
```

`create_agent()` 내부:

```python
# runtime_agent/strands/strands_agent.py (create_agent, L1117–1129)
session_manager = FileSessionManager(
    session_id=get_runtime_session_id(),
    storage_dir="/mnt/workspace",
)

agent = Agent(
    model=model,
    system_prompt=BASE_SYSTEM_PROMPT,
    tools=tools,
    plugins=[skills_plugin] if skills_plugin else [],
    conversation_manager=conversation_manager,
    session_manager=session_manager,
)
```

| 파라미터 | 값 | 설명 |
|---|---|---|
| `session_id` | `get_runtime_session_id()` | Bedrock AgentCore 요청 컨텍스트의 `runtimeSessionId` |
| `storage_dir` | `"/mnt/workspace"` | AgentCore Session Storage 마운트 경로 (S3 Files 등) |

- `create_agent()` 호출마다 `session_id`에 맞는 `FileSessionManager`를 새로 만듭니다.
- `plugins`(AgentSkills)는 세션 저장과 무관하며, `session_manager`만 대화·agent state를 디스크에 기록합니다.
- `session_id`는 Bedrock AgentCore 요청 컨텍스트의 `runtimeSessionId`에서 가져옵니다.

```python
def get_runtime_session_id() -> str:
    runtime_session_id = BedrockAgentCoreContext.get_session_id()
    if not runtime_session_id:
        logger.warning("runtimeSessionId not found in request context; using 'default-session'")
        runtime_session_id = "default-session"
    return runtime_session_id
```

---

## conversation_manager — 모델 컨텍스트 관리

**"지금 LLM에게 무엇을 보여줄까?"** 를 담당합니다.

### 동작 방식

1. **실제 컨텍스트 소스는 `agent.messages`**
   - 모델 호출 시 `agent.messages`가 전달됩니다.
   - `conversation_manager`는 이 배열을 **in-place로 trim**합니다.

2. **`window_size=50` — 메시지 개수 기준**
   - `window_size`는 **사용자 턴(turn) 수가 아니라** `agent.messages` 배열의 **메시지 개수**입니다 (`len(agent.messages)`).
   - 매 invocation 종료 후 `apply_management()`가 호출됩니다.
   - 메시지가 50개를 넘으면 오래된 메시지를 **메모리에서 제거**합니다.
   - 디스크에는 전체 대화가 남고, 모델에는 **최근 50개 메시지**만 전달됩니다.

   **한 번의 사용자 요청이 쌓는 메시지 수** (Strands event loop 기준):

   | 흐름 | `agent.messages`에 추가되는 메시지 | 합계 |
   |---|---|---|
   | `request → response` (tool 없음) | `user`(request) + `assistant`(response) | **2** |
   | `request → toolUse → toolResult → response` | `user` + `assistant`(toolUse) + `user`(toolResult) + `assistant`(response) | **4** |

   따라서 `window_size=50`이면 tool 없는 대화 약 **25회**, tool 1회씩 포함 대화 약 **12~13회** 수준까지 메모리에 남을 수 있습니다 (실제 tool 호출 횟수에 따라 달라짐).

   - trim 시 `toolUse` / `toolResult` **쌍이 깨지지 않도록** SDK가 유효한 경계에서만 잘라냅니다.
   - 컨텍스트 overflow 시에는 메시지 제거 전에 큰 **tool result** 텍스트를 부분 truncate할 수 있습니다 (`should_truncate_results=True`, 기본값).

3. **제거된 메시지 추적**
   - `removed_message_count`로 슬라이딩 윈도우로 잘려 나간 메시지 수를 기록합니다.
   - 이 값은 `session_manager.sync_agent()` 시 디스크에 함께 저장됩니다.

### 주요 SDK 호출 시점

- `apply_management()` — invocation 종료 후 컨텍스트 정리
- `reduce_context()` — 컨텍스트 윈도우 overflow 시 복구
- `get_state()` / `restore_from_session()` — `removed_message_count` 등 상태 저장/복원

---

## session_manager — 언제 쓰이나?

Strands SDK가 **Agent lifecycle hook**으로 자동 호출합니다.

```
Agent 생성
  └─ AgentInitializedEvent → session_manager.initialize(agent)   ← 복원

메시지 추가
  └─ MessageAddedEvent → append_message() + sync_agent()         ← 저장

invocation 종료
  └─ AfterInvocationEvent → sync_agent()                         ← 상태 동기화

컨텍스트 overflow
  └─ reduce_context() 후 → sync_agent()                           ← trim 상태 반영
```

### 1. Agent 초기화 시 — 세션 복원 (재시작 시 핵심)

`RepositorySessionManager.initialize()` 흐름:

1. `/mnt/workspace/session_<session_id>/` 에 세션이 있는지 확인
2. **있으면 (기존 세션)**:
   - `agent.state` 복원
   - `conversation_manager.restore_from_session(conversation_manager_state)` — `removed_message_count` 등 복원
   - 디스크 메시지 로드 (`offset=removed_message_count`로 이미 trim된 부분은 건너뜀)
   - `agent.messages = prepend_messages + [디스크 메시지들]`
3. **없으면 (신규 세션)**: 빈 agent 생성 후 디스크에 새 세션 기록

### 2. 대화 중 — 실시간 저장

- 사용자/assistant 메시지가 추가될 때마다 `message_<id>.json`으로 저장
- `sync_agent()`로 agent state, `conversation_manager_state`, interrupt state 등을 `agent.json`에 갱신

### 3. Guardrail redaction

- 모델이 사용자 메시지 redact를 요청하면 `redact_latest_message()`로 디스크의 최신 메시지도 수정

---

## 재시작 시 동작

> runtime이 재시작해서 conversation_manager가 없을 때 자동으로 session_manager를 읽어오는 건가?

**거의 맞지만, 정확히는 다음과 같습니다.**

- `conversation_manager`는 코드에서 **항상 전달**됩니다 (없어지지 않음).
- 재시작 시 **사라지는 것은 in-memory 상태** (`agent.messages`, `removed_message_count` 등)입니다.
- **복원은 `session_manager.initialize()`가 담당**하며, 그 안에서 `conversation_manager.restore_from_session()`도 함께 호출됩니다.

즉 fallback이 아니라 **협력 구조**입니다:

```
[디스크: 전체 대화 + conversation_manager_state]
        ↓  session_manager.initialize() (Agent 생성 시 1회)
[메모리: agent.messages 복원 + conversation_manager 상태 복원]
        ↓  conversation_manager.apply_management() (매 호출 후)
[모델에 전달: 최근 window_size=50개]
        ↓
      LLM 호출
        ↓  MessageAddedEvent / AfterInvocationEvent
[디스크: 새 메시지 + 상태 다시 저장]
```

---

## 이 프로젝트에서의 실제 타이밍

`run_strands_agent()` 및 `agent.py` entrypoint 모두 Agent를 **조건부로 재생성**합니다.

```python
if (
    selected_strands_tools != strands_tools
    or selected_mcp_servers != mcp_servers
    or selected_skill_list != skill_list
    or selected_session_id != current_session_id
    or agent is None
):
    agent = create_agent(strands_tools, mcp_servers, skill_list)
```

| 상황 | 동작 |
|---|---|
| **같은 프로세스, 같은 session_id, 설정 동일** | Agent 재사용 → `initialize()` 재호출 없음 → 메모리의 `agent.messages` 유지 |
| **session_id 변경 또는 설정 변경** | `create_agent()` → 새 Agent + `initialize()` → 해당 session_id 디스크 데이터 복원 |
| **Runtime 프로세스 재시작** | 메모리 초기화 → 첫 요청에서 `create_agent()` → `initialize()` → 디스크에서 복원 |

---

## 디스크 저장 구조

`FileSessionManager`가 `/mnt/workspace`에 만드는 구조:

```
/mnt/workspace/
└── session_<runtimeSessionId>/
    ├── session.json
    └── agents/
        └── agent_<agent_id>/
            ├── agent.json          # state, conversation_manager_state 등
            └── messages/
                ├── message_0.json
                ├── message_1.json
                └── ...
```

---

## 정리

1. **`conversation_manager`**: 런타임 중 `agent.messages`를 **모델 컨텍스트 한도 내**로 유지 (슬라이딩 윈도우 50 **messages**).
2. **`session_manager`**: 대화 전체와 manager 상태를 **디스크에 영속화**, Agent 생성 시 **자동 복원**.
3. **재시작 복원**: `conversation_manager`가 session_manager를 "대신 읽는" 것이 아니라, `session_manager.initialize()`가 **메시지 + conversation_manager 상태를 함께** 복원합니다.
4. **모델에 보이는 것**: 디스크의 전체 대화가 아니라, 복원된 `agent.messages` 중 `window_size=50`으로 trim된 최근 대화입니다.

---

## 주의사항

- `session_id`는 사용자/요청별로 고유해야 합니다. `default-session` fallback을 쓰면 서로 다른 요청이 같은 세션을 공유할 수 있습니다.
- `window_size=50`이면 디스크에는 전체 대화가 저장되지만, 모델에는 최근 **50개 메시지**만 전달됩니다.
- `/mnt/workspace`는 AgentCore session storage 마운트가 있어야 `FileSessionManager`가 정상 동작합니다.
- `conversation_manager`는 모듈 레벨 싱글톤이므로, **다른 session_id로 Agent를 재생성할 때** `initialize()`가 해당 세션의 `conversation_manager_state`로 덮어씁니다.
