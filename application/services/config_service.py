"""Assemble application UI config (models, skills, MCP, strands tools)."""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    from application import utils
except ImportError:
    import utils

logger = logging.getLogger("config_service")

_APPLICATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS = [
    "Claude 5.0 Sonnet",
    "Claude 5.0 Opus",
    "Claude 4.6 Sonnet",
    "Claude Fable 5",
    "Claude 4.8 Opus",
    "Claude 4.7 Opus",
    "Claude 4.6 Opus",
    "Claude 4.5 Opus",
    "Claude 4.5 Sonnet",
    "Claude 4.5 Haiku",
    "OpenAI GPT 5.4",
    "OpenAI GPT 5.5",
    "OpenAI GPT 5.6 Sol",
    "OpenAI GPT 5.6 Terra",
    "OpenAI GPT 5.6 Luna",
    "OpenAI OSS 120B",
    "OpenAI OSS 20B",
]

DEFAULT_MODEL = "Claude 4.6 Sonnet"
STRANDS_TOOLS = ["current_time", "file_read", "file_write", "http_request"]
DEFAULT_STRANDS_TOOLS = ["current_time", "file_read", "file_write"]


def load_capability_list(filename: str) -> list[str]:
    path = os.path.join(_APPLICATION_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        logger.warning("Capability list not found: %s", path)
        return []


def get_application_config() -> dict[str, Any]:
    skill_options = load_capability_list("skills.list")
    mcp_options = load_capability_list("mcp.list")
    default_skills, default_mcp = utils.get_initial_tool_defaults()
    config = utils.load_config()
    default_strands_tools = (
        config.get("default_strands_tool_selections") or DEFAULT_STRANDS_TOOLS
    )
    default_skills = [s for s in default_skills if s in skill_options]
    default_mcp = [m for m in default_mcp if m in mcp_options]
    default_strands_tools = [t for t in default_strands_tools if t in STRANDS_TOOLS]
    if not default_skills and "skill-creator" in skill_options:
        default_skills = ["skill-creator"]
    if not default_mcp:
        logger.info("No initial MCP defaults matched current capability list")
    if not default_strands_tools:
        default_strands_tools = DEFAULT_STRANDS_TOOLS
    return {
        "projectName": config.get("projectName", "agent"),
        "skills": skill_options,
        "mcp_servers": mcp_options,
        "strands_tools": STRANDS_TOOLS,
        "models": MODELS,
        "default_model": DEFAULT_MODEL,
        "default_skills": default_skills,
        "default_mcp_servers": default_mcp,
        "default_strands_tools": default_strands_tools,
    }
