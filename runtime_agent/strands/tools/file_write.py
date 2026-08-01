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

"""file_write wrapper that forces all writes into ARTIFACTS_DIR."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools.file_write import TOOL_SPEC as _BASE_TOOL_SPEC
from strands_tools.file_write import file_write as _strands_file_write

from tools.workspace import ARTIFACTS_DIR, force_artifacts_path

logger = logging.getLogger("strands-agent")

# Re-export for Agent module discovery (strands_tools style).
TOOL_SPEC = {
    **_BASE_TOOL_SPEC,
    "description": (
        "Write content to a file under the artifacts directory. "
        "Prefer a bare filename (e.g. report.docx). Paths like "
        "application/artifacts/... or artifacts/... are remapped onto the "
        f"artifacts cwd ({ARTIFACTS_DIR})."
    ),
}


def file_write(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Write a file, remapping path onto ARTIFACTS_DIR before delegating."""
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

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    if remapped != original:
        logger.info("file_write path remapped: %r -> %r", original, remapped)

    # Bypass interactive confirmation in AgentCore / non-TTY runtimes.
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

    patched = {
        **tool,
        "input": {**tool_input, "path": remapped},
    }
    return _strands_file_write(patched, **kwargs)
