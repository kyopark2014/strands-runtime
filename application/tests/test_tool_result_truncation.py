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

"""Tests for tool-result truncation and aws docs search_results parsing."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from application.agentcore_sse import (  # noqa: E402
    _NOTIFY_TRUNCATE_CHARS,
    _process_strands_sse_event,
    _truncate_text,
)
from application.tool_result_parsers import _parse_aws_docs_search_result  # noqa: E402


class TruncateTextTests(unittest.TestCase):
    def test_short_passthrough(self) -> None:
        self.assertEqual(_truncate_text("hello", 100), "hello")

    def test_truncates_with_suffix(self) -> None:
        big = "x" * 50_000
        out = _truncate_text(big, 200)
        self.assertLessEqual(len(out), 200)
        self.assertIn("truncated", out)
        self.assertTrue(out.startswith("x"))


class ToolResultSseTruncateTests(unittest.TestCase):
    @patch("application.agentcore_sse.get_tool_info")
    def test_huge_tool_result_is_truncated_in_notification(self, mock_get_tool_info) -> None:
        mock_get_tool_info.return_value = ("", [], [])
        queue = MagicMock()
        stream_state = {
            "current": "",
            "result": "",
            "references": [],
            "image_url": [],
        }
        huge = "<!doctype html>" + ("a" * 100_000)
        _process_strands_sse_event(
            {
                "toolResult": huge,
                "toolUseId": "tooluse_test",
                "tool": "get_raw_text",
            },
            queue,
            stream_state,
        )
        queue.tool_update.assert_called()
        message = queue.tool_update.call_args.args[1]
        self.assertTrue(message.startswith("Tool Result:"))
        self.assertLessEqual(len(message), _NOTIFY_TRUNCATE_CHARS + len("Tool Result: "))
        self.assertIn("truncated", message)


class AwsDocsSearchResultsTests(unittest.TestCase):
    def test_unwraps_search_results_root_object(self) -> None:
        payload = {
            "search_results": [
                {
                    "url": "https://docs.aws.amazon.com/a",
                    "title": "Title A",
                    "context": "Context A about pricing",
                },
                {
                    "url": "https://docs.aws.amazon.com/b",
                    "title": "Title B",
                    "context": "Context B",
                },
            ]
        }
        content, _urls, refs = _parse_aws_docs_search_result(json.dumps(payload))
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["url"], "https://docs.aws.amazon.com/a")
        self.assertIn("Context A", content)


if __name__ == "__main__":
    unittest.main()
