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

"""Builtin @tool implementations for the Strands runtime agent."""

from tools.execute_code import execute_code
from tools.bash import bash
import tools.file_read as file_read
import tools.file_write as file_write
from tools.s3_upload import upload_file_to_s3, resolve_workspace_path, s3_uri_to_console_url
from tools.memory_tools import memory_search, memory_get
from tools.workspace import (
    WORKING_DIR,
    REPO_ROOT,
    SKILLS_DIR,
    ARTIFACTS_DIR,
    ARTIFACTS_REL,
    force_artifacts_path,
)

__all__ = [
    "execute_code",
    "bash",
    "file_read",
    "file_write",
    "upload_file_to_s3",
    "memory_search",
    "memory_get",
    "get_builtin_tools",
    "resolve_workspace_path",
    "s3_uri_to_console_url",
    "force_artifacts_path",
    "WORKING_DIR",
    "REPO_ROOT",
    "SKILLS_DIR",
    "ARTIFACTS_DIR",
    "ARTIFACTS_REL",
]


def get_builtin_tools() -> list:
    """Built-in tools paired with AgentSkills (skills tool is registered by the plugin)."""
    return [execute_code, bash, upload_file_to_s3]
