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

"""Unit tests for config_loader.load_json_config."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from config_loader import load_json_config


class LoadJsonConfigTests(unittest.TestCase):
    def test_defaults_when_file_missing(self) -> None:
        cfg = load_json_config("/nonexistent/config.json", defaults={"a": 1})
        self.assertEqual(cfg, {"a": 1})

    def test_file_values_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"region": "us-west-2", "projectName": "demo"}, handle)
            cfg = load_json_config(path)
            self.assertEqual(cfg["region"], "us-west-2")
            self.assertEqual(cfg["projectName"], "demo")

    def test_env_json_overrides_file_on_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"region": "us-west-2", "keep": "file"}, handle)
            env_key = "TEST_APP_CONFIG_JSON"
            os.environ[env_key] = json.dumps({"region": "us-east-1"})
            try:
                cfg = load_json_config(path, env_json_key=env_key)
            finally:
                os.environ.pop(env_key, None)
            self.assertEqual(cfg["region"], "us-east-1")
            self.assertEqual(cfg["keep"], "file")

    def test_invalid_env_json_does_not_raise(self) -> None:
        env_key = "TEST_APP_CONFIG_JSON_BAD"
        os.environ[env_key] = "{not-json"
        try:
            cfg = load_json_config(
                "/nonexistent/config.json",
                defaults={"ok": True},
                env_json_key=env_key,
            )
        finally:
            os.environ.pop(env_key, None)
        self.assertEqual(cfg, {"ok": True})


if __name__ == "__main__":
    unittest.main()
