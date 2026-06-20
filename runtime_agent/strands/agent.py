import logging
import sys

import chat
import httpx
import boto3
import utils
import strands_agent

from datetime import datetime, timezone
from urllib.parse import urlparse
from botocore.auth import SigV4Auth as BotocoreSigV4Auth
from botocore.awsrequest import AWSRequest
from bedrock_agentcore.runtime import BedrockAgentCoreApp

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("agent")

_original_httpx_async_init = httpx.AsyncClient.__init__

def _sigv4_region_for_bedrock_agentcore_url(url: str) -> str:
    host = urlparse(url).netloc
    parts = host.split(".")
    try:
        idx = parts.index("bedrock-agentcore")
        if idx + 1 < len(parts) and parts[idx + 1] != "amazonaws":
            return parts[idx + 1]
    except ValueError:
        pass
    return utils.load_config().get("region", "us-west-2")

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


auth_type = "iam"
app = BedrockAgentCoreApp()


@app.entrypoint
async def agent_strands(payload):
    """Invoke the Strands agent with a payload."""
    logger.info(f"payload: {payload}")

    query = payload.get("prompt")
    mcp_servers = payload.get("mcp_servers", [])
    skill_list = payload.get("skill_list", [])
    strands_tools = payload.get("strands_tools", strands_agent.strands_tools or [])
    model_name = payload.get("model_name")
    user_id = payload.get("user_id")

    logger.info(f"query: {query}")
    logger.info(f"mcp_servers: {mcp_servers}")
    logger.info(f"skill_list: {skill_list}")
    logger.info(f"strands_tools: {strands_tools}")

    skill_mode = payload.get("skill_mode")
    if skill_mode is None:
        skill_mode = "Enable" if skill_list else "Disable"

    if auth_type == "iam":
        httpx.AsyncClient.__init__ = _patched_httpx_async_init
        logger.info("Applied SigV4 monkey patch for Bedrock AgentCore MCP")

    chat.update(
        userId=user_id if user_id else chat.user_id,
        modelName=model_name if model_name else chat.model_name,
        debugMode=payload.get("debug_mode", chat.debug_mode),
        reasoningMode=payload.get("reasoning_mode", chat.reasoning_mode),
        skillMode=skill_mode,
    )

    needs_agent = (
        strands_agent.selected_strands_tools != strands_tools
        or strands_agent.selected_mcp_servers != mcp_servers
        or strands_agent.selected_skill_list != skill_list
        or strands_agent.selected_session_id != strands_agent.get_runtime_session_id()
        or strands_agent.agent is None
    )
    if needs_agent:
        strands_agent.selected_strands_tools = list(strands_tools)
        strands_agent.selected_mcp_servers = list(mcp_servers)
        strands_agent.selected_skill_list = list(skill_list)
        strands_agent.selected_session_id = strands_agent.get_runtime_session_id()

        strands_agent.mcp_manager.stop_agent_clients()
        strands_agent.agent = strands_agent.create_agent(
            strands_tools, mcp_servers, skill_list
        )
        strands_agent.mcp_manager.start_agent_clients(mcp_servers)

    strands_agent.mcp_manager.start_agent_clients(mcp_servers)

    final_output: dict = {"messages": "", "image_url": []}
    streamed_text = ""
    image_urls: list = []
    tool_names: dict[str, str] = {}

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

            elif "current_tool_use" in event:
                current_tool_use = event["current_tool_use"]
                name = current_tool_use.get("name", "")
                input_val = current_tool_use.get("input", "")
                tool_use_id = current_tool_use.get("toolUseId", "")
                logger.info(f"[current_tool_use] name={name}, input={input_val}")

                if tool_use_id:
                    tool_names[tool_use_id] = name
                yield {"tool": name, "input": input_val, "toolUseId": tool_use_id}

            elif "message" in event:
                message = event["message"]
                logger.info(f"[message] {message}")

                msg_content = message.get("content", [])
                for item in msg_content:
                    if "toolResult" not in item:
                        continue
                    tool_result = item["toolResult"]
                    tool_use_id = tool_result["toolUseId"]
                    tool_content = tool_result["content"]
                    tool_result_text = tool_content[0].get("text", "") if tool_content else ""
                    tool_name = tool_names.get(tool_use_id, "")
                    logger.info(f"[toolResult] {tool_result_text}, [toolUseId] {tool_use_id}")

                    yield {"toolResult": tool_result_text, "toolUseId": tool_use_id}

                    _, urls, _ = chat.get_tool_info(tool_name, tool_result_text)
                    if urls:
                        for url in urls:
                            if url not in image_urls:
                                image_urls.append(url)

            elif "contentBlockDelta" or "contentBlockStop" or "messageStop" or "metadata" in event:
                pass
            else:
                logger.info(f"event: {event}")

        result_text = final_output.get("messages") or streamed_text

        if not (result_text or "").strip() and streamed_text.strip():
            result_text = streamed_text

        final_output = {
            "messages": result_text if result_text else "답변을 찾지 못하였습니다.",
            "image_url": image_urls,
        }

    yield {"result": final_output}


if __name__ == "__main__":
    app.run()
