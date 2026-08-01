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
import re
import sys
import time
import uuid
from collections.abc import Callable
from typing import TypeVar

from bedrock_agentcore.memory import MemoryClient
from botocore.exceptions import BotoCoreError, ClientError

from memory_service import MemoryService
from memory_config import (
    MEMORY_EXTRACTION_MODEL_ID,
    SEMANTIC_CONSOLIDATION_PROMPT,
    SEMANTIC_PROMPT,
    SUMMARY_PROMPT,
    USER_PREFERENCE_PROMPT,
    _existing_strategy_names,
    shared_memory_strategies,
)
from retry_utils import retry_call
from utils import load_config

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("agentcore_memory")

config = load_config()

T = TypeVar("T")
MEMORY_MAX_ATTEMPTS = 3
MEMORY_RETRY_BASE_DELAY_SECONDS = 1.0
# add_strategy triggers an asynchronous UpdateMemory; the Memory stays in
# UPDATING briefly and rejects a follow-up strategy write until it settles.
# There is no describe-and-wait primitive, so pause before the next call.
STRATEGY_SETTLE_DELAY_SECONDS = 5


def _retry_call(
    operation: str,
    fn: Callable[[], T],
    *,
    max_attempts: int = MEMORY_MAX_ATTEMPTS,
    base_delay: float = MEMORY_RETRY_BASE_DELAY_SECONDS,
) -> T:
    """Retry an idempotent Memory client call with exponential backoff."""
    return retry_call(
        operation,
        fn,
        max_attempts=max_attempts,
        base_delay=base_delay,
        log=logger,
    )

bedrock_region = config.get('region')
projectName = config.get('projectName')
agentcore_memory_role = config.get('agentcore_memory_role') or os.environ.get(
    "AGENTCORE_MEMORY_ROLE", ""
)
# Prefer env from CDK Runtime / ECS when config.json is stale in the image.
if os.environ.get("MEMORY_ID"):
    config["memory_id"] = os.environ["MEMORY_ID"]
if os.environ.get("AGENTCORE_MEMORY_ROLE"):
    config["agentcore_memory_role"] = os.environ["AGENTCORE_MEMORY_ROLE"]
    agentcore_memory_role = os.environ["AGENTCORE_MEMORY_ROLE"]

memory_client = MemoryClient(region_name=bedrock_region)
_memory_service = MemoryService(memory_client.create_event)

# Cap list_memories pagination pages (fail-fast if exceeded; do not return partial lists).
MAX_LIST_MEMORY_PAGES = 100
# AgentCore Memory actor_id max length (API constraint).
MAX_ACTOR_ID_LENGTH = 128
# Page size for list_memories API calls.
LIST_MEMORIES_PAGE_SIZE = 100

# AgentCore Memory namespace pattern:
# [a-zA-Z0-9/*][a-zA-Z0-9-_/*]*(?::[a-zA-Z0-9-_/*]+)*[a-zA-Z0-9-_/*]*
# Emails (@, .) are invalid and cause ValidationException on retrieve.
_INVALID_ACTOR_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_memory_actor_id(user_id: str) -> str:
    """Make a user id safe for AgentCore Memory actor_id / namespace / strategy name."""
    raw = (user_id or "").strip() or "default"
    cleaned = _INVALID_ACTOR_CHARS.sub("_", raw).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "default"
    if not re.match(r"^[a-zA-Z0-9]", cleaned):
        cleaned = f"u_{cleaned}"
    return cleaned[:MAX_ACTOR_ID_LENGTH]


def resolve_memory_actor_id(user_id: str) -> str:
    """Map application user_id → API-safe AgentCore Memory actor_id."""
    actor_id = sanitize_memory_actor_id(user_id)
    if actor_id != (user_id or "").strip():
        logger.info(f"memory actor_id sanitized: {user_id!r} -> {actor_id!r}")
    return actor_id


def load_memory_variables(user_id: str):
    """
    Resolve AgentCore memory identifiers for a user.

    memory_id comes from config.json (installer) or is retrieved/created.
    actor_id / namespace are derived from sanitized user_id.
    session_id is ephemeral — no per-user JSON cache file.
    """
    actor_id = resolve_memory_actor_id(user_id)
    session_id = uuid.uuid4().hex
    namespace = f"/users/{actor_id}/preferences"

    memory_id = config.get("memory_id")
    if memory_id:
        logger.info(f"memory_id from config.json: {memory_id}")
    else:
        logger.info("memory_id is None, attempting to retrieve existing memory...")
        memory_id = retrieve_memory_id()
        if memory_id is None:
            logger.info("No existing memory found, creating new memory...")
            memory_id = create_memory()

    logger.info(
        f"memory_id: {memory_id}, actor_id: {actor_id}, "
        f"session_id: {session_id}, namespace: {namespace}"
    )
    return memory_id, actor_id, session_id, namespace

def _normalize_memory_summary(memory: dict) -> dict:
    normalized = memory.copy()
    if "id" in memory and "memoryId" not in normalized:
        normalized["memoryId"] = memory["id"]
    elif "memoryId" in memory and "id" not in normalized:
        normalized["id"] = memory["memoryId"]
    return normalized


def _list_all_memories() -> list:
    """
    List all AgentCore memories, following nextToken until exhausted.

    MemoryClient.list_memories(max_results=N) paginates but stops at N.
    This helper consumes pages until nextToken is gone (with a page safety cap).
    """
    memories: list = []
    next_token = None
    for page in range(MAX_LIST_MEMORY_PAGES):
        kwargs: dict = {"maxResults": LIST_MEMORIES_PAGE_SIZE}
        if next_token:
            kwargs["nextToken"] = next_token
        response = memory_client.gmcp_client.list_memories(**kwargs)
        page_items = response.get("memories") or []
        memories.extend(_normalize_memory_summary(m) for m in page_items)
        next_token = response.get("nextToken")
        if not next_token:
            return memories
    raise RuntimeError(
        f"list_memories exceeded {MAX_LIST_MEMORY_PAGES} pages; "
        "refusing incomplete results for retrieve_memory_id"
    )


def retrieve_memory_id():
    memory_id = None
    memory_name = projectName.replace("-", "_")  # use projectName as memory name

    try:
        memories = _list_all_memories()
    except (ClientError, BotoCoreError, RuntimeError) as e:
        logger.error(
            "Failed to list memories while resolving memory_id for %s: %s",
            memory_name,
            type(e).__name__,
        )
        return None
    logger.info(f"memories: {memories}")
    for memory in memories:            
        logger.info(f"Memory ID: {memory.get('id')}")
        if memory.get('id').split("-")[0] == memory_name:
            logger.info(f"The memory of {memory_name} was found")
            memory_id = memory.get('id')
            logger.info(f"Memory Arn: {memory.get('arn')}")
            break

    return memory_id

def load_memory_strategy(memory_id: str):
    try:
        strategies = memory_client.get_memory_strategies(memory_id)
        logger.info(f"strategies: {strategies}")
        return strategies
    except Exception as e:
        logger.error(
            f"Failed to get memory strategies for memory_id={memory_id!r}: {e}"
        )
        raise

def add_strategy(memory_id: str, strategy: dict):
    name = (strategy.get("customMemoryStrategy") or {}).get("name")
    namespaces = (strategy.get("customMemoryStrategy") or {}).get("namespaces")
    _retry_call(
        "add_strategy",
        lambda: memory_client.add_strategy(memory_id, strategy),
    )
    logger.info(
        f"Added shared strategy {name!r} namespaces={namespaces!r} to memory_id={memory_id}"
    )
    time.sleep(STRATEGY_SETTLE_DELAY_SECONDS)


def create_strategy_if_not_exists(memory_id: str):
    """
    Ensure this memory_id has the shared UserPreference / Summary / Semantic strategies.

    Do NOT create a strategy per actor_id — that hits the 6-strategy quota.
    """
    try:
        strategies = load_memory_strategy(memory_id)
        for strategy in strategies or []:
            logger.info(f"strategy: {strategy}")
        existing = _existing_strategy_names(strategies)
        for strategy_def in shared_memory_strategies():
            name = strategy_def["customMemoryStrategy"]["name"]
            if name in existing:
                logger.info(f"Shared strategy already present: {name}")
                continue
            logger.info(f"{name} strategy not found, adding...")
            try:
                add_strategy(memory_id, strategy_def)
                existing.add(name)
                logger.info(f"{name} strategy was added...")
            except Exception as add_err:
                logger.error(f"Failed to add strategy {name!r}: {add_err}")
    except Exception as e:
        # Do not block CreateEvent short-term save if strategy UpdateMemory fails
        logger.error(f"Failed to ensure memory strategy (continuing without update): {e}")


def create_memory():
    """Create project Memory with shared UserPreference + Summary + Semantic strategies."""
    try:
        result = _retry_call(
            "create_memory_and_wait",
            lambda: memory_client.create_memory_and_wait(
                name=projectName.replace("-", "_"),
                description=f"Memory for {projectName}",
                event_expiry_days=365,  # 7 - 365 days
                strategies=shared_memory_strategies(),
                memory_execution_role_arn=agentcore_memory_role,
            ),
        )
    except Exception:
        logger.exception("Failed to create AgentCore memory")
        raise
    logger.info(f"result of memory creation: {result}")
    memory_id = result.get("id")
    logger.info(f"created memory_id: {memory_id}")
    return memory_id
    
def save_conversation_to_memory(memory_id, actor_id, session_id, query, result):
    logger.info("###### save_conversation_to_memory ######")
    try:
        return _memory_service.save_conversation(
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            query=query,
            result=result,
        )
    except Exception as e:
        logger.error(f"Error saving conversation to memory: {e}")
        raise

def get_memory_record(user_id: str):
    logger.info(f"###### get_memory_record ######")    

    memory_id, actor_id, session_id, namespace = load_memory_variables(user_id)
    logger.info(f"memory_id: {memory_id}, user_id: {user_id}, actor_id: {actor_id}, session_id: {session_id}, namespace: {namespace}")
    
    try:
        conversations = _retry_call(
            "list_events",
            lambda: memory_client.list_events(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                max_results=5,
            ),
        )
    except Exception:
        logger.exception("Failed to list memory events for user_id=%s", user_id)
        raise
    logger.info(f"conversations: {conversations}")

    return conversations

# Re-export strategy constants for backward compatibility.
__all__ = [
    "MEMORY_EXTRACTION_MODEL_ID",
    "USER_PREFERENCE_PROMPT",
    "SUMMARY_PROMPT",
    "SEMANTIC_PROMPT",
    "SEMANTIC_CONSOLIDATION_PROMPT",
    "shared_memory_strategies",
    "sanitize_memory_actor_id",
    "resolve_memory_actor_id",
    "load_memory_variables",
    "retrieve_memory_id",
    "load_memory_strategy",
    "add_strategy",
    "create_strategy_if_not_exists",
    "create_memory",
    "save_conversation_to_memory",
    "get_memory_record",
]
