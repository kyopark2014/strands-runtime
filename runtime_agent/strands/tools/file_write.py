"""file_write wrapper that allows artifacts and per-user skills directories."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools.file_write import TOOL_SPEC as _BASE_TOOL_SPEC
from strands_tools.file_write import file_write as _strands_file_write

import tools.workspace as workspace
from tools.workspace import path_is_under, resolve_agent_file_path

logger = logging.getLogger("strands-agent")

# Re-export for Agent module discovery (strands_tools style).
TOOL_SPEC = {
    **_BASE_TOOL_SPEC,
    "description": (
        "Write content to a file under the artifacts directory, or under the "
        "per-user skills directory ($USER_SKILLS_DIR). Prefer a bare filename "
        "for artifacts (e.g. report.docx). For skill-creator output use "
        f"$USER_SKILLS_DIR/<skill-name>/SKILL.md ({workspace.USER_SKILLS_DIR}). "
        "Paths like application/artifacts/... are remapped onto artifacts."
    ),
}


def file_write(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Write a file under artifacts or USER_SKILLS_DIR before delegating."""
    tool_input = tool.get("input") or {}
    original = tool_input.get("path", "")
    try:
        remapped = resolve_agent_file_path(original)
    except ValueError as e:
        return {
            "toolUseId": tool.get("toolUseId", ""),
            "status": "error",
            "content": [{"text": f"Error: invalid path ({e})"}],
        }

    parent = os.path.dirname(remapped) or remapped
    if path_is_under(remapped, workspace.USER_SKILLS_DIR):
        os.makedirs(parent, exist_ok=True)
    else:
        os.makedirs(workspace.ARTIFACTS_DIR, exist_ok=True)

    if remapped != original:
        logger.info("file_write path remapped: %r -> %r", original, remapped)

    # Bypass interactive confirmation in AgentCore / non-TTY runtimes.
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

    patched = {
        **tool,
        "input": {**tool_input, "path": remapped},
    }
    return _strands_file_write(patched, **kwargs)
