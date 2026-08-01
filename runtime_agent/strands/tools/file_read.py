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

"""file_read wrapper that forces paths into ARTIFACTS_DIR (matches file_write)."""

from __future__ import annotations

import logging
import os
from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools.file_read import TOOL_SPEC as _BASE_TOOL_SPEC
from strands_tools.file_read import file_read as _strands_file_read

from tools.workspace import ARTIFACTS_DIR, force_artifacts_paths

logger = logging.getLogger("strands-agent")

TOOL_SPEC = {
    **_BASE_TOOL_SPEC,
    "description": (
        "Read files under the artifacts directory. Prefer a bare filename "
        "(e.g. report.docx). Paths like application/artifacts/... are remapped "
        f"onto the artifacts cwd ({ARTIFACTS_DIR})."
    ),
}


def file_read(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Read a file, remapping path(s) onto ARTIFACTS_DIR before delegating."""
    tool_input = tool.get("input") or {}
    original = tool_input.get("path", "")
    remapped = force_artifacts_paths(original) if original else original
    if remapped != original:
        logger.info("file_read path remapped: %r -> %r", original, remapped)

    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

    patched = {
        **tool,
        "input": {**tool_input, "path": remapped},
    }
    return _strands_file_read(patched, **kwargs)
