"""file_write wrapper that forces all writes into workspace.ARTIFACTS_DIR."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools.file_write import TOOL_SPEC as _BASE_TOOL_SPEC
from strands_tools.file_write import file_write as _strands_file_write

import tools.workspace as workspace
from tools.workspace import force_artifacts_path

logger = logging.getLogger("strands-agent")

# Re-export for Agent module discovery (strands_tools style).
TOOL_SPEC = {
    **_BASE_TOOL_SPEC,
    "description": (
        "Write content to a file under the artifacts directory. "
        "Prefer a bare filename (e.g. report.docx). Paths like "
        "application/artifacts/... or artifacts/... are remapped onto the "
        f"artifacts cwd ({workspace.ARTIFACTS_DIR})."
    ),
}


def file_write(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Write a file, remapping path onto workspace.ARTIFACTS_DIR before delegating."""
    tool_input = tool.get("input") or {}
    original = tool_input.get("path", "")
    try:
        remapped = force_artifacts_path(original)
    except ValueError as e:
        return {
            "toolUseId": tool.get("toolUseId", ""),
            "status": "error",
            "content": [{"text": f"Error: invalid path ({e})"}],
        }

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
