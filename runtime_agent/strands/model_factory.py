"""Bedrock / Mantle model factory and prompt-cache helpers."""

from __future__ import annotations

import logging
import os

import boto3
import bedrock_data_retention
import chat
import info
from botocore.config import Config
from strands.models import BedrockModel, CacheConfig, CacheToolsConfig
from strands.models.openai import OpenAIModel
from strands.models.openai_responses import OpenAIResponsesModel

logger = logging.getLogger("strands-agent")

REASONING_BUFFER_TOKENS = 1000

# bedrock-mantle is the Amazon Bedrock OpenAI-compatible endpoint (not a separate AWS
# service): https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html
_MANTLE_BASE_URL = "https://bedrock-mantle.{region}.api.aws/openai/v1"
_mantle_url_patch_applied = False


def _ensure_mantle_base_url_patch() -> None:
    """Work around missing /openai path in SDK until harness-sdk#2706 lands."""
    global _mantle_url_patch_applied
    if _mantle_url_patch_applied:
        return
    import strands.models._openai_bedrock as openai_bedrock

    openai_bedrock._MANTLE_BASE_URL_TEMPLATE = _MANTLE_BASE_URL
    _mantle_url_patch_applied = True


def _build_mantle_openai_model(profile: dict, boto_session, max_output_tokens: int):
    """Route OpenAI-compatible Bedrock models through Bedrock Mantle."""
    _ensure_mantle_base_url_patch()

    bedrock_region = profile["bedrock_region"]
    model_id = profile["model_id"]
    mantle_api = profile.get("mantle_api", "chat")
    mantle_config = {"region": bedrock_region, "boto_session": boto_session}

    if mantle_api == "responses":
        return OpenAIResponsesModel(
            model_id=model_id,
            bedrock_mantle_config=mantle_config,
            params={
                "max_output_tokens": max_output_tokens,
            },
        )

    return OpenAIModel(
        model_id=model_id,
        bedrock_mantle_config=mantle_config,
        params={
            "max_tokens": max_output_tokens,
        },
    )


# Bedrock Anthropic/Nova prompt caching (ephemeral, 5m TTL).
PROMPT_CACHE_TTL = "5m"


def _supports_prompt_caching(model_type: str | None) -> bool:
    return model_type in ("claude", "nova")


def _prompt_cache_kwargs(model_type: str) -> dict:
    """Strands BedrockModel kwargs for prompt caching (Claude/Nova only).

    - cache_prompt: system cachePoint appended at request format time, after
      AgentSkills injects skill XML into the system prompt.
    - cache_tools: tool schema cachePoint.
    - cache_config: last-user-message cachePoint for tool-loop / multi-turn reuse.
    Nova is not detected by strategy="auto", so use "anthropic" (Converse cachePoint).
    """
    if not _supports_prompt_caching(model_type):
        return {}
    strategy = "auto" if model_type == "claude" else "anthropic"
    return {
        "cache_prompt": "default",
        "cache_tools": CacheToolsConfig(type="default", ttl=PROMPT_CACHE_TTL),
        "cache_config": CacheConfig(strategy=strategy, ttl=PROMPT_CACHE_TTL),
    }


def _log_prompt_cache_usage(result) -> None:
    """Log cacheRead / cacheWrite from AgentResult metrics when present."""
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return
    usage = None
    latest = getattr(metrics, "latest_agent_invocation", None)
    if latest is not None:
        latest_usage = getattr(latest, "usage", None)
        if isinstance(latest_usage, dict) and latest_usage:
            usage = latest_usage
    if usage is None:
        accumulated = getattr(metrics, "accumulated_usage", None)
        if isinstance(accumulated, dict):
            usage = accumulated
    if not isinstance(usage, dict):
        return
    cache_read = usage.get("cacheReadInputTokens") or 0
    cache_creation = usage.get("cacheWriteInputTokens") or 0
    if cache_read or cache_creation:
        logger.info(
            "prompt cache usage: cache_read=%s cache_creation=%s",
            cache_read,
            cache_creation,
        )


def get_model():
    model_profiles = info.get_model_info(chat.model_name)
    if not model_profiles:
        raise RuntimeError(f"No Bedrock profile for model_name={chat.model_name!r}")
    profile = model_profiles[0]
    bedrock_region = profile["bedrock_region"]
    model_id = profile["model_id"]
    model_type = profile["model_type"]

    if model_type == "nova":
        STOP_SEQUENCE = '"\n\n<thinking>", "\n<thinking>", " <thinking>"'
    elif model_type == "claude":
        STOP_SEQUENCE = "\n\nHuman:"
    elif model_type == "openai":
        STOP_SEQUENCE = ""

    if model_type == "claude":
        maxOutputTokens = chat.get_max_output_tokens(model_id)
    else:
        maxOutputTokens = 5120

    maxReasoningOutputTokens = 64000
    thinking_budget = min(maxOutputTokens, maxReasoningOutputTokens - REASONING_BUFFER_TOKENS)

    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_session_token = os.environ.get("AWS_SESSION_TOKEN")

    bedrock_config = Config(
        retries={"max_attempts": 30},
        read_timeout=300,
    )

    if aws_access_key and aws_secret_key:
        boto_session = boto3.Session(
            region_name=bedrock_region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            aws_session_token=aws_session_token,
        )
    else:
        boto_session = boto3.Session(region_name=bedrock_region)

    if "fable" in model_id.lower():
        bedrock_data_retention.ensure_fable_data_retention(
            model_id,
            bedrock_region=bedrock_region,
        )

    adaptive_thinking = chat.uses_adaptive_thinking(model_id)
    guardrail_kwargs = chat.get_bedrock_model_guardrail_kwargs(model_type)
    prompt_cache_kwargs = _prompt_cache_kwargs(model_type)

    if chat.reasoning_mode == "Enable" and model_type != "openai" and not adaptive_thinking:
        model = BedrockModel(
            boto_session=boto_session,
            boto_client_config=bedrock_config,
            model_id=model_id,
            max_tokens=64000,
            stop_sequences=[STOP_SEQUENCE],
            temperature=1,
            additional_request_fields={
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
            },
            **prompt_cache_kwargs,
            **guardrail_kwargs,
        )
    elif chat.reasoning_mode == "Disable" and model_type != "openai" and not adaptive_thinking:
        model = BedrockModel(
            boto_session=boto_session,
            boto_client_config=bedrock_config,
            model_id=model_id,
            max_tokens=maxOutputTokens,
            stop_sequences=[STOP_SEQUENCE],
            temperature=0.1,
            additional_request_fields={
                "thinking": {
                    "type": "disabled"
                }
            },
            **prompt_cache_kwargs,
            **guardrail_kwargs,
        )
    elif model_type != "openai" and adaptive_thinking:
        model = BedrockModel(
            boto_session=boto_session,
            boto_client_config=bedrock_config,
            model_id=model_id,
            max_tokens=maxOutputTokens,
            stop_sequences=[STOP_SEQUENCE],
            **prompt_cache_kwargs,
            **guardrail_kwargs,
        )
    elif model_type == "openai":
        model = _build_mantle_openai_model(profile, boto_session, maxOutputTokens)

    return model
