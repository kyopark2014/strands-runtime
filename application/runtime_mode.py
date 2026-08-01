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

import logging
import os

logger = logging.getLogger("runtime_mode")

BACKEND_MODE = "agentcore"


def backend_mode_label() -> str:
    return BACKEND_MODE


def use_agentcore_runtime() -> bool:
    """Agent inference always goes through AgentCore invoke_agent_runtime."""
    return True


def ensure_agentcore_backend() -> None:
    """Reject docker/local agent backend overrides at startup."""
    forced = os.environ.get("AGENT_BACKEND", "").strip().lower()
    if forced in {"docker", "local", "run_agent_in_docker"}:
        logger.warning(
            "AGENT_BACKEND=%s is ignored; backend always uses AgentCore runtime",
            forced,
        )

    use_docker = os.environ.get("USE_DOCKER_AGENT", "").strip().lower()
    if use_docker in {"1", "true", "yes"}:
        logger.warning(
            "USE_DOCKER_AGENT is ignored; backend always uses AgentCore runtime",
        )


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
    """Dispatch agent calls to AgentCore runtime only."""
    from application import agentcore_client

    if not use_agentcore_runtime():
        raise RuntimeError("AgentCore runtime is required for agent execution")
    return agentcore_client.run_agent(
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
