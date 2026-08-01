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

"""Shared JSON config loader for runtime_agent/strands modules."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def load_json_config(
    path: str,
    *,
    defaults: dict[str, Any] | None = None,
    env_json_key: str | None = None,
) -> dict[str, Any]:
    """Load config from optional env JSON and/or a JSON file.

    When both are present, env values win on conflicts (non-empty env values only).
    """
    config: dict[str, Any] = dict(defaults or {})

    if env_json_key:
        raw = os.environ.get(env_json_key)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    config.update(parsed)
                else:
                    logger.warning("%s must contain a JSON object", env_json_key)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", env_json_key, exc)

    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                file_cfg = json.load(handle)
            if not isinstance(file_cfg, dict):
                logger.warning("%s must contain a JSON object", path)
            else:
                merged = dict(file_cfg)
                merged.update(
                    {k: v for k, v in config.items() if v not in (None, "")}
                )
                config = merged
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)

    return config
