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

"""Measure Bedrock prompt caching for the Strands Agent path.

Uses the same CacheConfig / cache_tools / cache_prompt helpers as
strands_agent.get_model(), then runs a 2-step tool loop and reports
per-cycle cache usage from AgentResult.metrics.

Usage:
  cd runtime_agent/strands
  python test_prompt_caching.py
"""

from __future__ import annotations

import argparse
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

BEDROCK_MAX_RETRY_ATTEMPTS = 8
BEDROCK_READ_TIMEOUT_SECONDS = 180
TEST_MAX_TOKENS = 256

import strands_agent as sa
from model_factory import _build_mantle_openai_model, _supports_gpt_explicit_caching


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5"
DEFAULT_GPT_MODEL_ID = "openai.gpt-5.6-sol"


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
    details = usage.get("input_token_details") or usage.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    return CacheStats(
        label=label,
        input_tokens=int(usage.get("inputTokens") or usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("outputTokens") or usage.get("output_tokens") or 0),
        cache_creation=int(
            usage.get("cacheWriteInputTokens")
            or usage.get("cache_write_tokens")
            or details.get("cache_write_tokens")
            or 0
        ),
        cache_read=int(
            usage.get("cacheReadInputTokens")
            or usage.get("cached_tokens")
            or details.get("cached_tokens")
            or 0
        ),
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


def _build_model(model_id: str, region: str, run_id: str):
    if _supports_gpt_explicit_caching("openai", model_id):
        profile = {
            "bedrock_region": region,
            "model_id": model_id,
            "mantle_api": "responses",
            "model_type": "openai",
        }
        boto_session = boto3.Session(region_name=region)
        return _build_mantle_openai_model(
            profile,
            boto_session,
            TEST_MAX_TOKENS,
            session_id=f"probe:{run_id}",
        )

    boto_session = boto3.Session(region_name=region)
    bedrock_config = Config(retries={"max_attempts": BEDROCK_MAX_RETRY_ATTEMPTS}, read_timeout=BEDROCK_READ_TIMEOUT_SECONDS)
    kwargs: dict[str, Any] = {
        "boto_session": boto_session,
        "boto_client_config": bedrock_config,
        "model_id": model_id,
        "max_tokens": TEST_MAX_TOKENS,
        **sa._prompt_cache_kwargs("claude"),
    }
    # Claude 5 / fable reject temperature (adaptive thinking path).
    mid = model_id.lower()
    if not (
        "claude-sonnet-5" in mid
        or "claude-5-sonnet" in mid
        or "claude-opus-5" in mid
        or "claude-5-opus" in mid
        or "fable" in mid
    ):
        kwargs["temperature"] = 0.1
    return BedrockModel(**kwargs)


# ---------------------------------------------------------------------------
# Unit tests for the pure metric helpers (no AWS / no model calls required).
# Run with: pytest test_prompt_caching.py
# ---------------------------------------------------------------------------

def test_cache_hit_ratio_and_footprint() -> None:
    stats = CacheStats(
        label="t",
        input_tokens=100,
        output_tokens=10,
        cache_creation=50,
        cache_read=150,
    )
    # footprint = uncached + cache write + cache read
    assert stats.billed_input_like == 300
    assert stats.cache_hit_ratio == 150 / 300


def test_cache_hit_ratio_zero_when_no_input() -> None:
    empty = CacheStats("t", 0, 0, 0, 0)
    assert empty.billed_input_like == 0
    assert empty.cache_hit_ratio == 0.0


def test_usage_to_stats_supports_both_key_styles() -> None:
    camel = _usage_to_stats(
        "c",
        {
            "inputTokens": 12,
            "outputTokens": 3,
            "cacheWriteInputTokens": 4,
            "cacheReadInputTokens": 5,
        },
    )
    assert (camel.input_tokens, camel.output_tokens) == (12, 3)
    assert (camel.cache_creation, camel.cache_read) == (4, 5)

    snake = _usage_to_stats("s", {"input_tokens": 7, "output_tokens": 1})
    assert snake.input_tokens == 7
    assert snake.output_tokens == 1

    empty = _usage_to_stats("e", None)
    assert empty.billed_input_like == 0


def test_summarize_token_savings_computes_reduction() -> None:
    stats_list = [
        CacheStats("call1", input_tokens=100, output_tokens=0, cache_creation=100, cache_read=0),
        CacheStats("call2", input_tokens=0, output_tokens=0, cache_creation=0, cache_read=200),
    ]
    summary = summarize_token_savings(stats_list)
    assert summary["calls"] == 2
    # total footprint = (100+100+0) + (0+0+200) = 400 ; cache_read = 200
    assert summary["total_input_tokens_without_reuse"] == 400
    assert summary["tokens_reused_via_cache_read"] == 200
    assert summary["input_token_reduction_pct"] == 50.0


def test_summarize_token_savings_empty_is_safe() -> None:
    summary = summarize_token_savings([])
    assert summary["calls"] == 0
    assert summary["input_token_reduction_pct"] == 0.0


def test_extract_cycle_stats_reads_per_cycle_usage() -> None:
    class _Cycle:
        def __init__(self, usage: dict[str, Any]) -> None:
            self.usage = usage

    class _Latest:
        cycles = [
            _Cycle({"inputTokens": 10, "cacheReadInputTokens": 5}),
            _Cycle({"inputTokens": 2, "cacheReadInputTokens": 8}),
        ]

    class _Metrics:
        latest_agent_invocation = _Latest()

    class _Result:
        metrics = _Metrics()

    stats = _extract_cycle_stats(_Result())
    assert len(stats) == 2
    assert stats[0].input_tokens == 10
    assert stats[1].cache_read == 8


def test_extract_cycle_stats_without_metrics_returns_empty() -> None:
    class _NoMetrics:
        metrics = None

    assert _extract_cycle_stats(_NoMetrics()) == []


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Strands prompt caching")
    parser.add_argument("--model-id", default=os.environ.get("PROMPT_CACHE_MODEL_ID", DEFAULT_MODEL_ID))
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    region = args.region or _load_region()
    model_id = args.model_id
    run_id = uuid.uuid4().hex[:8]
    system = _padded_system_prompt(run_id)

    model = _build_model(model_id, region, run_id)
    cache_mode = "gpt-explicit" if _supports_gpt_explicit_caching("openai", model_id) else "bedrock-converse"
    agent = Agent(
        model=model,
        system_prompt=system,
        tools=[echo_cache_probe],
    )

    print(f"model_id={model_id} region={region} run_id={run_id} cache_mode={cache_mode}")
    print(f"system_prompt_chars={len(system)}")
    print("tools=1 (echo_cache_probe)")
    if cache_mode == "bedrock-converse":
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
