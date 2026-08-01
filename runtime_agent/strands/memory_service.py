"""Conversation memory helpers for AgentCore Memory CreateEvent."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Callable

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("memory_service")

# AWS Bedrock CreateEvent message content limit
BEDROCK_MAX_TEXT_LENGTH = 9000
_TRUNCATE_SUFFIX = "... [truncated]"


def validate_memory_input(query, result) -> bool:
    """Return True when query and result are non-empty strings suitable for memory."""
    if not query or not isinstance(query, str) or len(query.strip()) == 0:
        logger.warning("Query is empty or invalid, skipping memory save")
        return False
    if not result or not isinstance(result, str) or len(result.strip()) == 0:
        logger.warning("Result is empty or invalid, skipping memory save")
        return False
    return True


def truncate_text_for_bedrock(text, max_length=BEDROCK_MAX_TEXT_LENGTH):
    """Strip and truncate text to the Bedrock message length limit."""
    trimmed = text.strip()
    if len(trimmed) <= max_length:
        return trimmed

    logger.warning(f"Text exceeds {max_length} characters, truncating")
    max_content_length = max_length - len(_TRUNCATE_SUFFIX)
    truncated = trimmed[:max_content_length] + _TRUNCATE_SUFFIX
    if len(truncated) > max_length:
        truncated = truncated[:max_length]
    return truncated


def format_conversation_event(query, result, timestamp=None):
    """Build (messages, event_timestamp) for memory_client.create_event."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    conversation = [
        (query, "USER"),
        (result, "ASSISTANT"),
    ]
    return conversation, timestamp


class MemoryService:
    """
    Orchestrates validation, formatting, and CreateEvent persistence.

    Pass a create_event callable (e.g. memory_client.create_event) so this
    module stays free of AWS client construction.
    """

    def __init__(self, create_event: Callable[..., Any]):
        self._create_event = create_event

    def save_conversation(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        query: str,
        result: str,
    ) -> Any | None:
        if not validate_memory_input(query, result):
            return None

        conversation, event_timestamp = format_conversation_event(
            truncate_text_for_bedrock(query),
            truncate_text_for_bedrock(result),
        )

        try:
            memory_result = self._create_event(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                event_timestamp=event_timestamp,
                messages=conversation,
            )
        except Exception:
            logger.exception("CreateEvent failed for memory_id=%s", memory_id)
            raise
        logger.info("result of save conversation to memory: %s", memory_result)
        return memory_result
