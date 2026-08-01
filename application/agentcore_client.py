# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import boto3
from botocore.config import Config
import json
import logging
import sys

try:
    from application.agentcore_runtime import (
        bedrock_region,
        load_agentcore_config,
        projectName,
        runtime_session_id_for,
    )
    from application.agentcore_sse import (
        _finalize_agent_result,
        _process_strands_sse_event,
        add_notification,
        normalize_bedrock_message_content,
        tool_info_list,
        tool_name_list,
        tool_result_list,
        tool_slot_update,
        update_streaming_result,
    )
    from application.tool_result_parsers import get_tool_info
except ImportError:
    from agentcore_runtime import (
        bedrock_region,
        load_agentcore_config,
        projectName,
        runtime_session_id_for,
    )
    from agentcore_sse import (
        _finalize_agent_result,
        _process_strands_sse_event,
        add_notification,
        normalize_bedrock_message_content,
        tool_info_list,
        tool_name_list,
        tool_result_list,
        tool_slot_update,
        update_streaming_result,
    )
    from tool_result_parsers import get_tool_info

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("agentcore_client")

# Re-export public helpers used by other modules / callers.
__all__ = [
    "AgentCoreService",
    "run_agent",
    "load_agentcore_config",
    "runtime_session_id_for",
    "get_tool_info",
    "add_notification",
    "update_streaming_result",
    "tool_slot_update",
    "normalize_bedrock_message_content",
]


# AgentCore SSE streams can run for several minutes (tool use / long answers).
AGENTCORE_READ_TIMEOUT_SECONDS = 300  # 5 minutes
# TCP connect to the AgentCore control plane should fail fast if the endpoint is unreachable.
AGENTCORE_CONNECT_TIMEOUT_SECONDS = 60
# Standard-mode retries for transient AgentCore API errors (throttling / brief outages).
AGENTCORE_MAX_ATTEMPTS = 3


class AgentCoreService:
    """Orchestrates AgentCore runtime resolution, invoke, and stream processing."""

    def __init__(self, region: str | None = None):
        self.region = region or bedrock_region

    def create_runtime_client(self):
        boto_config = Config(
            read_timeout=AGENTCORE_READ_TIMEOUT_SECONDS,
            connect_timeout=AGENTCORE_CONNECT_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": AGENTCORE_MAX_ATTEMPTS},
        )
        return boto3.client(
            "bedrock-agentcore",
            region_name=self.region,
            config=boto_config,
        )

    def resolve_runtime_arn(self, agent_type: str = "strands") -> str | None:
        runtime_name = projectName.replace("-", "_") + "_" + agent_type
        return load_agentcore_config(runtime_name, agent_type=agent_type)

    def build_payload(
        self,
        *,
        prompt,
        user_id,
        mcp_servers,
        model_name,
        runtime_session_id,
        skill_list=None,
        strands_tools=None,
        guardrail_enabled=None,
        memory_enabled=None,
        files=None,
    ) -> str:
        return json.dumps(
            {
                "prompt": prompt,
                "mcp_servers": mcp_servers,
                "model_name": model_name,
                "user_id": user_id,
                "runtime_session_id": runtime_session_id,
                "skill_list": skill_list or [],
                "strands_tools": strands_tools or [],
                "guardrail_enabled": (
                    bool(guardrail_enabled) if guardrail_enabled is not None else True
                ),
                "memory_enabled": (
                    bool(memory_enabled) if memory_enabled is not None else True
                ),
                "files": files or [],
            }
        )

    def process_event_stream(
        self,
        response,
        notification_queue,
        stream_state: dict,
    ) -> None:
        processed_data = set()
        if "text/event-stream" not in response.get("contentType", ""):
            return

        for line in response["response"].iter_lines(chunk_size=10):
            line = line.decode("utf-8")
            if line:
                print(f"-> {line}")

            if not line.startswith("data: "):
                continue

            data = line[6:].strip()
            if not data or data in processed_data:
                continue
            processed_data.add(data)

            try:
                data_json = json.loads(data)
                _process_strands_sse_event(data_json, notification_queue, stream_state)
            except json.JSONDecodeError:
                logger.info(f"Not JSON: {data}")
            except Exception as e:
                logger.error(f"Error processing data: {e}")
                break

    def run(
        self,
        prompt,
        user_id,
        mcp_servers,
        model_name,
        runtime_session_id,
        notification_queue=None,
        skill_list=None,
        strands_tools=None,
        guardrail_enabled=None,
        memory_enabled=None,
        files=None,
    ):
        tool_info_list.clear()
        tool_result_list.clear()
        tool_name_list.clear()
        if notification_queue is not None:
            notification_queue.reset()

        references = []
        image_url = []

        logger.info(f"user_id: {user_id}")

        payload = self.build_payload(
            prompt=prompt,
            user_id=user_id,
            mcp_servers=mcp_servers,
            model_name=model_name,
            runtime_session_id=runtime_session_id,
            skill_list=skill_list,
            strands_tools=strands_tools,
            guardrail_enabled=guardrail_enabled,
            memory_enabled=memory_enabled,
            files=files,
        )

        agent_runtime_arn = self.resolve_runtime_arn("strands")
        print(f"agent_runtime_arn: {agent_runtime_arn}")
        logger.info(f"agent_runtime_arn: {agent_runtime_arn}")
        logger.info(f"Payload: {payload}")

        if agent_runtime_arn is None:
            logger.error("agent_runtime_arn is not found")
            return "Error: agent_runtime_arn is not found", []

        try:
            agent_core_client = self.create_runtime_client()
            logger.info(f"runtime_session_id: {runtime_session_id}")
            response = agent_core_client.invoke_agent_runtime(
                agentRuntimeArn=agent_runtime_arn,
                runtimeSessionId=runtime_session_id,
                payload=payload,
                qualifier="DEFAULT",  # DEFAULT or LATEST
            )

            stream_state = {
                "result": "",
                "current": "",
                "image_url": image_url,
                "references": references,
            }
            self.process_event_stream(response, notification_queue, stream_state)

            result = _finalize_agent_result(
                stream_state["result"],
                stream_state["current"],
                stream_state["references"],
                notification_queue,
            )
            image_url = stream_state["image_url"]

            logger.info(f"result: {result}")
            return result, image_url

        except Exception:
            logger.exception("Unexpected error while running agent")
            return "An error occurred processing your request", []


def run_agent(
    prompt,
    user_id,
    mcp_servers,
    model_name,
    runtime_session_id,
    notification_queue=None,
    skill_list=None,
    strands_tools=None,
    guardrail_enabled=None,
    memory_enabled=None,
    files=None,
):
    return AgentCoreService().run(
        prompt,
        user_id,
        mcp_servers,
        model_name,
        runtime_session_id,
        notification_queue=notification_queue,
        skill_list=skill_list,
        strands_tools=strands_tools,
        guardrail_enabled=guardrail_enabled,
        memory_enabled=memory_enabled,
        files=files,
    )
