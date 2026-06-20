# Strands Agent의 AgentCore 배포 및 활용

여기에서는 AgentCore Runtime을 이용해서 Strands Agent을 배포하는 방법과 2) agent에 필요한 데이터를 수집하고 사용자의 의도에 따른 동작을 수행하는 방법을 설명합니다. AgentCore는 Agent와 MCP/SKILL를 위한 서버리스 production 환경으로서 Agent와 MCP 서버를 편리하게 배포하고 안전하고 효과적으로 운용할 수 있습니다.

## 주요 구현 

### 전체 Architecture

전체적인 Architecture는 아래와 같습니다. 여기서는 MCP/SKILL를 지원하는 Strands agent를 [AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)를 이용해 배포하고 streamlit 애플리케이션을 이용해 사용합니다. 개발자는 각 agent에 맞는 [Dockerfile](./runtime/strands/Dockerfile)을 이용하여, docker image를 생성하고 ECR에 업로드 합니다. 이후 [bedrock-agentcore-control](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/Welcome.html)의 [installer.py](./runtime/strands/installer.py)을 이용해서 [AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)의 runtime으로 배포합니다. 이 작업이 끝나면 EC2와 같은 compute에 있는 streamlit에서 Strands와 Strands agent를 활용할 수 있습니다. 애플리케이션에서 AgentCore의 runtime을 호출할 때에는 [bedrock-agentcore](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore.html)의 [invoke_agent_runtime](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html)을 이용합니다. 이때에 각 agent를 생성할 때에 확인할 수 있는 [agentRuntimeArn](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_Agent.html)을 이용합니다. Agent는 [MCP](https://modelcontextprotocol.io/introduction)을 이용해 RAG, AWS Document, Tavily와 같은 검색 서비스를 활용할 수 있습니다. 여기에서는 RAG를 위하여 Lambda를 이용합니다. 데이터 저장소의 관리는 Knowledge base를 사용하고, 벡터 스토어로는 OpenSearch를 이용합니다. Agent에 필요한 S3, CloudFront, OpenSearch, Lambda등의 배포를 위해서는 AWS CDK를 이용합니다.

<img width="1000" alt="image" src="https://github.com/user-attachments/assets/297edccf-de23-40bf-8f94-99b4a3cbbca1" />


AgentCore의 runtime은 배포를 위해 Docker를 이용합니다. 현재(2025.7) 기준으로 arm64와 1GB 이하의 docker image를 지원합니다.

### Operation Architecture

Streamlit UI(`application/app.py`)에서 대화 모드·Skills·MCP·Strands Tools·모델을 선택하면 `application/agentcore_client.py`가 AgentCore Runtime(`invoke_agent_runtime`)으로 SSE 요청을 보냅니다. 로컬 개발 시에는 `run_agent_in_docker`로 `localhost:8080`의 Docker 컨테이너를 호출할 수 있습니다. Runtime은 `runtime_agent/strands/agent.py`의 `agent_strands` 엔트리포인트에서 Strands Agent, Agent Skills, 임베디드 MCP 서버를 연결한 뒤 Amazon Bedrock으로 추론합니다.

```mermaid
flowchart TB
  subgraph UI["Streamlit (application/app.py)"]
    MODE["Agent / Agent (Chat)"]
    SEL["Skills · MCP · Strands Tools · 모델"]
  end

  subgraph Client["application/agentcore_client.py"]
    RA["run_agent · invoke_agent_runtime"]
    RD["run_agent_in_docker · localhost:8080"]
    TEST["test_runtime_remote.py"]
  end

  subgraph AgentCore["Amazon Bedrock AgentCore"]
    AC["AgentCore Runtime (SSE)"]
  end

  subgraph Runtime["runtime_agent/strands"]
    ENTRY["agent.py · agent_strands"]
    SA["strands_agent.py"]
    SK["skill.py"]
    MCP["mcp_config.py · mcp.list"]
    CHAT["chat.py · get_tool_info"]
    INFO["info.py · model profiles"]
    UTILS["utils.py · config.json"]
  end

  subgraph Skills["Agent Skills"]
    SRC["skills/*/SKILL.md + references/"]
  end

  subgraph AgentStack["Strands Agents SDK"]
    A["Agent + BedrockModel"]
    BT["Built-in: execute_code, bash, upload_file_to_s3"]
    GSI["get_skill_instructions"]
    ST["strands_tools: current_time, file_read, file_write"]
    MCM["MCPClientManager"]
  end

  subgraph MCPServers["Embedded MCP (mcp_server_*.py)"]
    MCPsrv["tavily · knowledge base · aws documentation · trade info · web_fetch · image generation · 사용자 설정"]
  end

  subgraph LLM["Amazon Bedrock"]
    BR[Bedrock Runtime]
  end

  subgraph Storage["Artifacts / S3 / CloudFront"]
    ART["artifacts/"]
    S3[(S3)]
    CF["sharing_url"]
  end

  MODE --> RA
  SEL --> RA
  MODE -.-> RD
  SEL -.-> RD
  TEST --> AC

  RA --> AC
  RD -.-> ENTRY
  AC --> ENTRY

  ENTRY --> SA
  ENTRY --> CHAT
  CHAT --> INFO
  SA --> INFO
  SA --> UTILS
  MCP --> UTILS
  SA --> A
  SA --> SK
  SK -->|build_skill_prompt| A
  A --> GSI
  GSI --> SK
  SK --> SRC
  A --> BT
  A --> ST
  A --> MCM
  A --> BR
  MCM -->|load_config| MCP
  MCM --> MCPsrv
  BT --> ART
  BT --> S3
  BT --> CF
```

| 모드 | 모듈 | 설명 |
|------|------|------|
| **Agent** | `application/app.py` → `agentcore_client.run_agent` | 단일 턴 Agent. `history_mode=Disable`로 매 요청을 독립 처리 |
| **Agent (Chat)** | `application/app.py` → `agentcore_client.run_agent` | 대화 이력 유지. `history_mode=Enable`로 세션 기반 interactive 대화 |
| Strands Runtime | `runtime_agent/strands/agent.py` | Strands SDK `Agent` + `MCPClientManager` + strands_tools |
| 임베디드 MCP | `runtime_agent/strands/mcp_server_*.py` | Tavily/Knowledge Base/AWS Docs/Trade Info/Web Fetch/Image 생성/사용자 설정 MCP 제공 |
| Skill/MCP 선택 목록 | `application/skills.list`, `application/mcp.list` | UI에서 스킬·MCP 체크박스 옵션 제공 |

플랫폼은 **AgentCore**(서버리스 Runtime)와 **Docker**(로컬 `localhost:8080`)를 지원하며, 현재 애플리케이션은 `agent_type = "strands"` 고정으로 Strands Runtime을 사용합니다. MCP는 UI에서 `tavily`, `knowledge base`, `aws documentation`, `trade info`, `web_fetch`, `image generation`, `사용자 설정`을 체크박스로 선택합니다.

### AgentCore 소개

- AgentCore Runtime: AI agent와 tool을 배포하고 트래픽에 따라 자동으로 확장(Scaling)이 가능한 serverless runtime입니다. Strands, CrewAI, Strands Agents를 포함한 다양한 오픈소스 프레임워크을 지원합니다. 빠른 cold start, 세션 격리, 내장된 신원 확인(built-in identity), multimodal payload를 지원합니다. 이를 통해 안전하고 빠른 출시가 가능합니다.
- AgentCore Memory: Agent가 편리하게 short term, long term 메모리를 관리할 수 있습니다.
- AgentCore Code Interpreter: 분리된 sandbox 환경에서 안전하게 코드를 실행할 수 있습니다.
- AgentCore Broswer: 브라우저를 이용해 빠르고 안전하게 웹크롤링과 같은 작업을 수행할 수 있습니다.
- AgentCore Gateway: API, Lambda를 비롯한 서비스들을 쉽게 Tool로 활용할 수 있습니다.
- AgentCore Observability: 상용 환경에서 개발자가 agent의 동작을 trace, debug, monitor 할 수 있습니다.



## Agent 구현

AgentCore는 SSE 방식의 stream을 제공합니다. 

### Strands

[Strands - agent.py](./strands_stream/agent.py)와 같이 stream으로 처리합니다. 아래와 같이 AgentCore를 endpoint로 지정할 때에 agent_stream의 값을 yeild로 전달하면 streamlit 같은 client에서 동적으로 응답을 받을 수 있습니다.

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
async def agentcore_strands(payload):
    # initiate agent
    await initiate_agent(
        system_prompt=None, 
        strands_tools=strands_tools, 
        mcp_servers=mcp_servers, 
        historyMode='Disable'
    )

    # run agent
    with mcp_manager.get_active_clients(mcp_servers) as _:
        agent_stream = agent.stream_async(query)

        async for event in agent_stream:
            text = ""            
            if "data" in event:
                text = event["data"]
                stream = {'data': text}
            elif "result" in event:
                final = event["result"]                
                message = final.message
                if message:
                    content = message.get("content", [])
                    result = content[0].get("text", "")
                    stream = {'result': result}
            elif "current_tool_use" in event:
                current_tool_use = event["current_tool_use"]
                name = current_tool_use.get("name", "")
                input = current_tool_use.get("input", "")
                toolUseId = current_tool_use.get("toolUseId", "")
                text = f"name: {name}, input: {input}"
                stream = {'tool': name, 'input': input, 'toolUseId': toolUseId}            
            elif "message" in event:
                message = event["message"]
                if "content" in message:
                    content = message["content"]
                    if "toolResult" in content[0]:
                        toolResult = content[0]["toolResult"]
                        toolUseId = toolResult["toolUseId"]
                        toolContent = toolResult["content"]
                        toolResult = toolContent[0].get("text", "")
                        stream = {'toolResult': toolResult, 'toolUseId': toolUseId}

            yield (stream)
```

#### Client

AgentCore로 agent_runtime_arn을 이용해 request에 대한 응답을 얻습니다. 이때 content-type이 "text/event-stream"인 경우에 prefix인 "data:"를 제거한 후에 json parser를 이용해 얻어진 값을 목적에 맞게 활용합니다.

```python
agent_core_client = boto3.client('bedrock-agentcore', region_name=bedrock_region)
response = agent_core_client.invoke_agent_runtime(
    agentRuntimeArn=agent_runtime_arn,
    runtimeSessionId=runtime_session_id,
    payload=payload,
    qualifier="DEFAULT" # DEFAULT or LATEST
)

result = current = ""
processed_data = set()  # Prevent duplicate data

# stream response
if "text/event-stream" in response.get("contentType", ""):
    for line in response["response"].iter_lines(chunk_size=10):
        line = line.decode("utf-8")        
        if line.startswith('data: '):
            data = line[6:].strip()  # Remove "data:" prefix and whitespace
            if data:  # Only process non-empty data
                # Check for duplicate data
                if data in processed_data:
                    continue
                processed_data.add(data)
                
                data_json = json.loads(data)
                if 'data' in data_json:
                    text = data_json['data']
                    logger.info(f"[data] {text}")
                    current += text
                    containers['result'].markdown(current)
                elif 'result' in data_json:
                    result = data_json['result']
                elif 'tool' in data_json:
                    tool = data_json['tool']
                    input = data_json['input']
                    toolUseId = data_json['toolUseId']
                    if toolUseId not in tool_info_list: # new tool info
                        tool_info_list[toolUseId] = index                                        
                        add_notification(containers, f"Tool: {tool}, Input: {input}")
                    else: # overwrite tool info
                        containers['notification'][tool_info_list[toolUseId]].info(f"Tool: {tool}, Input: {input}")                    
                elif 'toolResult' in data_json:
                    toolResult = data_json['toolResult']
                    toolUseId = data_json['toolUseId']
                    if toolUseId not in tool_result_list:  # new tool result
                        tool_result_list[toolUseId] = index
                        add_notification(containers, f"Tool Result: {toolResult}")
                    else: # overwrite tool result
                        containers['notification'][tool_result_list[toolUseId]].info(f"Tool Result: {toolResult}")
```




## Runtime Agent

### IAM 인증

Agent가 MCP server에 요청을 보낼때 IAM 인증을 수행합니다. [create_agent_runtime](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore-control/client/create_agent_runtime.html)에서 authorizerConfiguration을 포함하지 않은 경우에 IAM으로 인증하게 됩니다. Runtime 생성시 client는 bedrock-agentcore-control을 사용하고 MCP server에 대한 ECR 경로를 가지고 있어야 합니다. 상세한 코드는 [installer.py](https://github.com/kyopark2014/agent-runtime/blob/main/runtime_mcp/iam_auth/kb-retriever/installer.py)을 참조합니다.

```python
client = boto3.client('bedrock-agentcore-control', region_name=aws_region)

response = client.create_agent_runtime(
    agentRuntimeName=runtime_name,
    agentRuntimeArtifact={
        'containerConfiguration': {
            'containerUri': f"{account_id}.dkr.ecr.{aws_region}.amazonaws.com/{repository_name}:{image_tag}"
        }
    },
	filesystemConfigurations=[
		{
			"sessionStorage": {
				"mountPath": "/mnt/workspace"
			}
		}
	],
    networkConfiguration={"networkMode": "PUBLIC"}, 
    roleArn=agent_runtime_role,
    protocolConfiguration={"serverProtocol": "MCP"}
)

print(f"✓ Agent runtime created: {response['agentRuntimeArn']}")
```

Agent에서 MCP server로 요청을 보낼때에는 아래와 같이 IAM 인증을 수행하기 위하여 request에 X-Amz-Security-Token을 포함합니다. 이를 위해 httpx의 event hook을 이용해 아래와 같이 구현할 수 있습니다. 상세코드는 [agent.py](https://github.com/kyopark2014/agent-runtime/blob/main/runtime_agent/Strands/agent.py)을 참조합니다.

```python
def _patched_httpx_async_init(self, *args, **kwargs):
    async def sign_request(request: httpx.Request) -> None:
        url_str = str(request.url)
        if "bedrock-agentcore" not in url_str:
            return
        if ".gateway.bedrock-agentcore." in url_str:
            return
        if request.headers.get("Authorization"):
            return

        boto_session = boto3.Session()
        credentials = boto_session.get_credentials().get_frozen_credentials()

        parsed_url = urlparse(url_str)
        host = parsed_url.netloc
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        body = None
        if request.content:
            if isinstance(request.content, bytes):
                body = request.content
            else:
                try:
                    body = await request.aread()
                    if hasattr(request, "_content"):
                        request._content = body
                except Exception:
                    pass

        aws_headers = {
            "host": host,
            "x-amz-date": timestamp,
            "Content-Type": request.headers.get("Content-Type", "application/json"),
            "Accept": request.headers.get("Accept", "application/json, text/event-stream"),
        }
        if body:
            aws_headers["Content-Length"] = str(len(body))

        aws_request = AWSRequest(
            method=request.method,
            url=url_str,
            headers=aws_headers,
            data=body,
        )

        region = _sigv4_region_for_bedrock_agentcore_url(url_str)
        auth = BotocoreSigV4Auth(credentials, "bedrock-agentcore", region)
        auth.add_auth(aws_request)

        request.headers["X-Amz-Date"] = timestamp
        request.headers["Authorization"] = aws_request.headers["Authorization"]
        if credentials.token:
            request.headers["X-Amz-Security-Token"] = credentials.token

    if "event_hooks" not in kwargs:
        kwargs["event_hooks"] = {"request": [], "response": []}
    elif not isinstance(kwargs["event_hooks"], dict):
        kwargs["event_hooks"] = {"request": [], "response": []}
    if "request" not in kwargs["event_hooks"]:
        kwargs["event_hooks"]["request"] = []
    kwargs["event_hooks"]["request"].append(sign_request)

    _original_httpx_async_init(self, *args, **kwargs)
```

이후 아래와 같이 auth_type이 iam이면, httpx.AsyncClient을 업데이트 합니다.

```python
import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
async def agent_strands(payload):
    """Invoke the Strands agent with a payload."""
    
    query = payload.get("prompt")
    mcp_servers = payload.get("mcp_servers", [])
    skill_list = payload.get("skill_list", [])
    strands_tools = payload.get("strands_tools", strands_agent.strands_tools or [])
    model_name = payload.get("model_name")
    user_id = payload.get("user_id")

    if auth_type == "iam":
        httpx.AsyncClient.__init__ = _patched_httpx_async_init

    strands_agent.mcp_manager.start_agent_clients(mcp_servers)

    with strands_agent.mcp_manager.get_active_clients(mcp_servers) as _:
        agent_stream = strands_agent.agent.stream_async(query)

        async for event in agent_stream:
            if "data" in event:
                text = event["data"]
                streamed_text += text
                logger.info(f"[data] {text}")
                yield {"data": text}

            elif "result" in event:
                final = event["result"]
                message = final.message
                if message:
                    content = message.get("content", [])
                    text = content[0].get("text", "") if content else ""
                    logger.info(f"[result] {text}")
                    final_output = {"messages": text, "image_url": image_urls}

        result_text = final_output.get("messages") or streamed_text

        final_output = {
            "messages": result_text if result_text else "답변을 찾지 못하였습니다.",
            "image_url": image_urls,
        }

    yield {"result": final_output}
```


#### Session Strogage

AgentCore Runtime에서 context를 관리하기 위해 Session Strage를 활용할 수 있습니다.

```python
client = boto3.client('bedrock-agentcore-control', region_name=aws_region)

response = client.create_agent_runtime(
    agentRuntimeName=runtime_name,
    agentRuntimeArtifact={
        'containerConfiguration': {
            'containerUri': f"{account_id}.dkr.ecr.{aws_region}.amazonaws.com/{repository_name}:{image_tag}"
        }
    },
    filesystemConfigurations=[
        {
            "sessionStorage": {
                "mountPath": "/mnt/workspace"  # /mnt/ 하위 경로 필수
            }
        }
    ]
    networkConfiguration={"networkMode": "PUBLIC"}, 
    roleArn=agent_runtime_role
)
```

filesystemConfigurations에서 설정한 Session Storage는 runtime에서 아래처럼 Session Manager를 이용해 활용할 수 있습니다.

```python
from strands import Agent
from strands.session.file_session_manager import FileSessionManager
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

# Create a session manager with a unique session ID 
session_manager = FileSessionManager(
	session_id=BedrockAgentCoreContext.get_session_id(),
	storage_dir="/mnt/workspace"
)

# Create an agent with the session manager
agent = Agent(session_manager=session_manager)

agent("Hello!") # This conversation is persisted
```

### Conversation Manager와 File Session Manager의 차이

Strands Agent는 `conversation_manager`와 `session_manager`를 **독립적으로** 받을 수 있으며, **함께 사용하는 것이 정상적인 패턴**입니다. 두 매니저는 역할이 다릅니다.

| | `conversation_manager` | `session_manager` |
|---|---|---|
| **역할** | 모델에 보낼 **대화 컨텍스트 관리** | 대화·상태 **영속 저장** |
| **관심사** | 메모리, 토큰 한도, 컨텍스트 길이 | 프로세스 재시작 후에도 세션 유지 |
| **동작 시점** | 매 호출/턴에서 in-memory로 동작 | 메시지·상태 변경 시 파일에 저장 |
| **현재 구현** | `SlidingWindowConversationManager(window_size=10)` | `FileSessionManager(session_id=..., storage_dir="/mnt/workspace")` |

**`conversation_manager`** — "지금 모델에게 뭘 보여줄까?"

- 대화 히스토리 크기 제어 (슬라이딩 윈도우, 요약, truncation)
- 컨텍스트 윈도우 초과 시 `reduce_context()`로 복구
- 큰 tool result 잘라내기
- **런타임 중** in-memory에서 동작

**`session_manager`** — "다음에 다시 켜도 기억할까?"

- 메시지, agent state, `conversation_manager_state`를 **디스크에 저장**
- AgentCore Runtime의 session storage(`/mnt/workspace`)와 연동
- 재시작 후 세션 복원

둘을 같이 쓰면 역할이 다음과 같이 나뉩니다.

```
[전체 대화 히스토리]  ← session_manager가 디스크에 저장
        ↓
[슬라이딩 윈도우 10턴] ← conversation_manager가 모델에 전달할 부분만 선택
        ↓
      LLM 호출
```

[`strands_agent.py`](./runtime_agent/strands/strands_agent.py)에서는 두 매니저를 함께 사용합니다.

```python
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.session.file_session_manager import FileSessionManager

conversation_manager = SlidingWindowConversationManager(
    window_size=10,
)

session_manager = FileSessionManager(
    session_id="test-session",
    storage_dir="/mnt/workspace"
)

agent = Agent(
    model=model,
    system_prompt=system_prompt,
    tools=tools,
    conversation_manager=conversation_manager,  # 컨텍스트 관리
    session_manager=session_manager,            # 영속 저장
)
```

SDK 내부에서도 함께 동작하도록 설계되어 있습니다.

- `conversation_manager.apply_management()` — 호출 후 컨텍스트 정리
- `conversation_manager.reduce_context()` 후 `session_manager.sync_agent()` — 압축 결과를 세션에 반영
- 세션 복원 시 `conversation_manager.restore_from_session()` — 이전 윈도우/요약 상태 복원

**주의사항**

- `session_id`는 사용자/요청별로 고유하게 설정해야 합니다. 고정값(`"test-session"`)을 쓰면 모든 사용자가 같은 세션을 공유합니다.
- `window_size=10`이면 디스크에는 전체 대화가 저장되지만, 모델에는 최근 10턴만 전달됩니다.
- `/mnt/workspace`는 AgentCore session storage 마운트가 있어야 `FileSessionManager`가 정상 동작합니다.











## 배포하기

아래와 같이 EC2를 이용해 배포 환경을 구성합니다.

1. AWS Console의 EC2에 접속해서 [Launch instance]를 선택합니다.
2. EC2 생성시 Architecture로 Arm64을 선택하고 나머지는 기본값으로 생성합니다.
3. [EC2 Instance Connect]로 접속해서 아래와 같이 python, pip, git, boto3를 설치합니다.

```text
sudo yum install python3 python3-pip git 
pip install boto3 
```

4. 아래 명령어로 docker를 설치합니다.

```bash
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
newgrp docker
docker info
```

5. 아래와 같이 git source를 가져옵니다.

```python
git clone https://github.com/kyopark2014/strands-runtime
```

6. 아래와 같이 [installer.py](./installer.py)를 이용해 설치를 시작합니다.

```text
python3 strands-runtime/installer.py
```


설치가 완료되면 CloudFront로 접속하여 동작을 확인합니다. 

접속한 후 아래와 같이 Agent를 선택한 후에 적절한 MCP tool을 선택하여 원하는 작업을 수행합니다.

인프라가 더이상 필요없을 때에는 루트 [uninstaller.py](./uninstaller.py)를 이용해 제거합니다.

```text
python uninstaller.py
```



### Knowledge Base 문서 동기화 하기 

Knowledge Base에서 문서를 활용하기 위해서는 S3에 문서 등록 및 동기화기 필요합니다. [S3 Console](https://us-west-2.console.aws.amazon.com/s3/home?region=us-west-2)에 접속하여 "storage-for-agentcore-xxxxxxxxxxxx-us-west-2"를 선택하고, 아래와 같이 docs폴더를 생성한 후에 파일을 업로드 합니다. 

<img width="400" alt="image" src="https://github.com/user-attachments/assets/482f635e-a38d-4525-b9a3-fb1c2a9089c8" />

이후 [Knowledge Bases Console](https://us-west-2.console.aws.amazon.com/bedrock/home?region=us-west-2#/knowledge-bases)에 접속하여, "agentcore"라는 Knowledge Base를 선택합니다. 이후 아래와 같이 [Sync]를 선택합니다.

<img width="1533" height="287" alt="noname" src="https://github.com/user-attachments/assets/2edd3b6b-dbce-4784-b640-139fa84cc223" />




### Local에서 실행하기

AWS 환경을 잘 활용하기 위해서는 [AWS CLI를 설치](https://docs.aws.amazon.com/ko_kr/cli/v1/userguide/cli-chap-install.html)하여야 합니다. EC2에서 배포하는 경우에는 별도로 설치가 필요하지 않습니다. Local에 설치시는 아래 명령어를 참조합니다.

```text
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" 
unzip awscliv2.zip
sudo ./aws/install
```

AWS credential을 아래와 같이 AWS CLI를 이용해 등록합니다.

```text
aws configure
```

설치하다가 발생하는 각종 문제는 [Kiro-cli](https://aws.amazon.com/ko/blogs/korea/kiro-general-availability/)를 이용해 빠르게 수정합니다. 아래와 같이 설치할 수 있지만, Windows에서는 [Kiro 설치](https://kiro.dev/downloads/)에서 다운로드 설치합니다. 실행시는 셀에서 "kiro-cli"라고 입력합니다. 

```python
curl -fsSL https://cli.kiro.dev/install | bash
```

venv로 환경을 구성하면 편리하게 패키지를 관리합니다. 아래와 같이 환경을 설정합니다.

```text
python -m venv .venv
source .venv/bin/activate
```

이후 다운로드 받은 github 폴더로 이동한 후에 아래와 같이 필요한 패키지를 추가로 설치 합니다.

```text
pip install -r requirements.txt
```

이후 아래와 같은 명령어로 streamlit을 실행합니다. 

```text
streamlit run application/app.py
```



### 비동기 실행

에이전트가 즉시 응답하고 백그라운드에서 계속 처리할 수 있습니다. 클라이언트는 동기/비동기 구분 없이 동일한 API 사용가능하고, 세션을 재사용하여 컨텍스트 유지합니다.

```python
import threading
import time
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@tool
def start_background_task(duration: int = 5) -> str:
    """백그라운드에서 지정된 시간 동안 실행되는 태스크 시작"""

    # 비동기 태스크 등록
    task_id = app.add_async_task("background_processing", {"duration": duration})

    # 별도 스레드에서 백그라운드 작업 실행
    def background_work():
        time.sleep(duration)  # 실제 작업 수행
        app.complete_async_task(task_id)  

    threading.Thread(target=background_work, daemon=True).start()

    return f"백그라운드 태스크 시작됨 (ID: {task_id}), {duration}초 후 완료 예정"

agent = Agent(tools=[start_background_task])

@app.entrypoint
def main(payload):
    user_message = payload.get("prompt", "3초짜리 태스크를 시작해줘")
    return {"message": agent(user_message).message}

if __name__ == "__main__":
    app.run()
```


## 실행 결과

"https://github.com/kyopark2014/strands-runtime/blob/main/README.md 을 정리해줘."와 같이 입력하면 웹의 정보를 편리하게 활용할 수 있습니다.

<img width="728" height="729" alt="image" src="https://github.com/user-attachments/assets/c3a18138-ba1c-4956-90b4-d55a0737da33" />

이때의 결과는 아래와 같습니다.

<img width="663" height="780" alt="image" src="https://github.com/user-attachments/assets/6b4ed348-c923-46d7-838b-da8f54e123f8" />


"aws document로 agent evalutation 에 대해 조사해줘."로 하면 필요한 정보를 조회하여 정리합니다.

<img width="720" height="706" alt="image" src="https://github.com/user-attachments/assets/fb5eb40e-720e-420f-ad3b-8aafceab236e" />



## Reference 

[Invoke streaming agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html)

[Get started with the Amazon Bedrock AgentCore Runtime starter toolkit](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-getting-started-toolkit.html)

[Amazon Bedrock AgentCore - Developer Guide](https://docs.aws.amazon.com/pdfs/bedrock-agentcore/latest/devguide/bedrock-agentcore-dg.pdf)

[BedrockAgentCoreControlPlaneFrontingLayer](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore-control.html)

[get_agent_runtime](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore-control/client/get_agent_runtime.html)

[Amazon Bedrock AgentCore Samples](https://github.com/awslabs/amazon-bedrock-agentcore-samples)

[Amazon Bedrock AgentCore](https://buttoned-gull-5fa.notion.site/Amazon-Bedrock-AgentCore-23708996fdd380c2a6e1ffaa2e08c000)

[Amazon Bedrock AgentCore RuntCode Interpreter](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/01-tutorials/05-AgentCore-tools/01-Agent-Core-code-interpreter)

[Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

[Hosting Strands Agents with Amazon Bedrock models in Amazon Bedrock AgentCore Runtime](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/main/01-tutorials%2F06-AgentCore-observability%2F01-Agentcore-runtime-hosted%2Fruntime_with_strands_and_bedrock_models.ipynb)

[Agentic AI 펀드 매니저](https://github.com/ksgsslee/investment_advisor_strands)

[AWS re:Invent 2025 - Architecting scalable and secure agentic AI with Bedrock AgentCore (AIM431)](https://www.youtube.com/watch?v=wqmeZOT6mmc)


[Deploy Production-Ready Agents in 22 Minutes with AgentCore Runtime](https://www.youtube.com/watch?v=Q-tYIAuv9WI)

[AgentCore Workshop](https://atomoh.gitbook.io/aiops)
