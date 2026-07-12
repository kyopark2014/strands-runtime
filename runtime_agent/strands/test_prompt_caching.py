"""Measure Bedrock prompt caching for the Strands Agent path.

Uses the same CacheConfig / cache_tools / cache_prompt helpers as
strands_agent.get_model(), then runs a 2-step tool loop and reports
per-cycle cache usage from AgentResult.metrics.

Usage:
  cd runtime_agent/strands
  python test_prompt_caching.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import warnings
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from strands import Agent, tool
from strands.models import BedrockModel

import strands_agent as sa


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5"


@dataclass
class CacheStats:
    label: str
    input_tokens: int
    output_tokens: int
    cache_creation: int
    cache_read: int

    @property
    def billed_input_like(self) -> int:
        """Approximate total input footprint (uncached + cache write + cache read)."""
        return self.input_tokens + self.cache_creation + self.cache_read

    @property
    def cache_hit_ratio(self) -> float:
        total = self.billed_input_like
        if total <= 0:
            return 0.0
        return self.cache_read / total


def summarize_token_savings(stats_list: list[CacheStats]) -> dict[str, Any]:
    """Compare total input tokens with vs without prompt caching.

    reduction_% = total_cache_read / total_input_footprint
    """
    total_input = sum(s.billed_input_like for s in stats_list)
    total_cache_read = sum(s.cache_read for s in stats_list)
    total_cache_creation = sum(s.cache_creation for s in stats_list)
    total_uncached = sum(s.input_tokens for s in stats_list)
    tokens_without_reuse = total_uncached + total_cache_creation
    reduction_ratio = (total_cache_read / total_input) if total_input else 0.0
    return {
        "calls": len(stats_list),
        "total_input_tokens_without_reuse": total_input,
        "tokens_processed_or_written": tokens_without_reuse,
        "tokens_reused_via_cache_read": total_cache_read,
        "input_token_reduction_pct": round(reduction_ratio * 100, 1),
        "formula": (
            "reduction_% = sum(cache_read) / sum(input + cache_creation + cache_read)"
        ),
    }


def _load_region() -> str:
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    if not os.path.isfile(config_path):
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(SCRIPT_DIR)),
            "application",
            "config.json",
        )
    with open(config_path, encoding="utf-8") as f:
        return json.load(f).get("region", "us-west-2")


def _usage_to_stats(label: str, usage: dict[str, Any] | None) -> CacheStats:
    usage = usage or {}
    return CacheStats(
        label=label,
        input_tokens=int(usage.get("inputTokens") or usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("outputTokens") or usage.get("output_tokens") or 0),
        cache_creation=int(usage.get("cacheWriteInputTokens") or 0),
        cache_read=int(usage.get("cacheReadInputTokens") or 0),
    )


def _extract_cycle_stats(result: Any) -> list[CacheStats]:
    """Prefer per-cycle usage so a single tool-loop maps to Call 1 / Call 2."""
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return []

    latest = getattr(metrics, "latest_agent_invocation", None)
    cycles = getattr(latest, "cycles", None) if latest is not None else None
    if cycles:
        labels = [
            "Call 1 (tool 요청)",
            "Call 2 (tool 결과 반영)",
        ]
        stats: list[CacheStats] = []
        for i, cycle in enumerate(cycles):
            label = labels[i] if i < len(labels) else f"Call {i + 1}"
            stats.append(_usage_to_stats(label, getattr(cycle, "usage", None)))
        return stats

    # Fallback: whole-invocation accumulated usage
    usage = None
    if latest is not None and isinstance(getattr(latest, "usage", None), dict):
        usage = latest.usage
    elif isinstance(getattr(metrics, "accumulated_usage", None), dict):
        usage = metrics.accumulated_usage
    return [_usage_to_stats("Call 1 (accumulated)", usage)]


def _padded_system_prompt(run_id: str) -> str:
    """Build a system prompt large enough to exceed Claude cache thresholds (~1K+ tokens)."""
    base = sa.BASE_SYSTEM_PROMPT
    filler = (
        "You are evaluating Bedrock prompt caching for a Strands Agent tool loop. "
        "Keep answers short. Prefer calling echo_cache_probe when asked to probe. "
    ) * 80
    return f"{base}\n\n## Cache Probe Context\nrun_id={run_id}\n{filler}"


@tool
def echo_cache_probe(text: str) -> str:
    """Echo text. Used only by the prompt-caching probe."""
    return text


def _build_model(model_id: str, region: str) -> BedrockModel:
    boto_session = boto3.Session(region_name=region)
    bedrock_config = Config(retries={"max_attempts": 8}, read_timeout=180)
    kwargs: dict[str, Any] = {
        "boto_session": boto_session,
        "boto_client_config": bedrock_config,
        "model_id": model_id,
        "max_tokens": 256,
        **sa._prompt_cache_kwargs("claude"),
    }
    # claude-sonnet-5 / fable reject temperature (adaptive thinking path).
    if not (
        "claude-sonnet-5" in model_id
        or "fable" in model_id.lower()
    ):
        kwargs["temperature"] = 0.1
    return BedrockModel(**kwargs)


def main() -> int:
    region = _load_region()
    model_id = os.environ.get("PROMPT_CACHE_MODEL_ID", DEFAULT_MODEL_ID)
    run_id = uuid.uuid4().hex[:8]
    system = _padded_system_prompt(run_id)

    model = _build_model(model_id, region)
    agent = Agent(
        model=model,
        system_prompt=system,
        tools=[echo_cache_probe],
    )

    print(f"model_id={model_id} region={region} run_id={run_id}")
    print(f"system_prompt_chars={len(system)}")
    print("tools=1 (echo_cache_probe)")
    print("cache kwargs:", sa._prompt_cache_kwargs("claude"))
    print("---")

    # One agent turn that forces a 2-cycle tool loop (request → tool → final).
    query = (
        f"[cache-probe {run_id}] Call echo_cache_probe exactly once with text "
        f"'cache-probe-{run_id}', then briefly confirm the echoed value. "
        "Do not call any other tools."
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="cache_prompt is deprecated.*",
            category=UserWarning,
        )
        result = agent(query)

    sa._log_prompt_cache_usage(result)
    stats_list = _extract_cycle_stats(result)
    if not stats_list:
        print("ERROR: no usage metrics on AgentResult", file=sys.stderr)
        return 1

    for stats in stats_list:
        print(
            f"{stats.label}: input={stats.input_tokens} "
            f"cache_creation={stats.cache_creation} cache_read={stats.cache_read} "
            f"output={stats.output_tokens} hit={stats.cache_hit_ratio:.1%}"
        )

    summary = summarize_token_savings(stats_list)
    print("---")
    print(json.dumps(summary, indent=2))
    print(f"input token reduction: {summary['input_token_reduction_pct']}%")

    if all(s.cache_creation <= 0 and s.cache_read <= 0 for s in stats_list):
        print(
            "WARNING: no cache_creation/cache_read observed. "
            "Check model support, min token threshold, and AWS region.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
