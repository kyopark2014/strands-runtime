"""file_read wrapper that allows artifacts and per-user skills directories."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools.file_read import TOOL_SPEC as _BASE_TOOL_SPEC
from strands_tools.file_read import file_read as _strands_file_read

import tools.workspace as workspace
from tools.workspace import resolve_agent_file_paths

logger = logging.getLogger("strands-agent")

TOOL_SPEC = {
    **_BASE_TOOL_SPEC,
    "description": (
        "Read files under the artifacts directory or the per-user skills "
        "directory ($USER_SKILLS_DIR). Prefer a bare filename for artifacts "
        f"(e.g. report.docx). User skills live under {workspace.USER_SKILLS_DIR}."
    ),
}


def file_read(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Read a file under artifacts or USER_SKILLS_DIR before delegating."""
    tool_input = tool.get("input") or {}
    original = tool_input.get("path", "")
    remapped = resolve_agent_file_paths(original) if original else original
    if remapped != original:
        logger.info("file_read path remapped: %r -> %r", original, remapped)

    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

    patched = {
        **tool,
        "input": {**tool_input, "path": remapped},
    }
    return _strands_file_read(patched, **kwargs)
