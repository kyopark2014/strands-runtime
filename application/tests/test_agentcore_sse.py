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

"""Unit tests for agentcore_sse helpers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from application.agentcore_sse import (  # noqa: E402
    ToolResultParseError,
    _collect_tool_result_artifacts,
    _finalize_agent_result,
    _process_strands_sse_event,
    normalize_bedrock_message_content,
)


class NormalizeContentTests(unittest.TestCase):
    def test_string_passthrough(self) -> None:
        self.assertEqual(normalize_bedrock_message_content("hi"), "hi")

    def test_none_empty(self) -> None:
        self.assertEqual(normalize_bedrock_message_content(None), "")

    def test_text_blocks_joined(self) -> None:
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "id": "1"},
            {"type": "text", "text": "world"},
        ]
        self.assertEqual(normalize_bedrock_message_content(content), "Hello world")

    def test_dict_text_block(self) -> None:
        self.assertEqual(
            normalize_bedrock_message_content({"type": "text", "text": "x"}),
            "x",
        )

    def test_empty_list_and_unknown_type(self) -> None:
        self.assertEqual(normalize_bedrock_message_content([]), "")
        self.assertEqual(normalize_bedrock_message_content(123), "123")


class CollectArtifactsTests(unittest.TestCase):
    @patch("application.agentcore_sse.get_tool_info")
    def test_parse_error_is_swallowed(self, mock_get_tool_info) -> None:
        mock_get_tool_info.side_effect = ToolResultParseError("bad payload")
        refs: list = []
        images: list = []
        _collect_tool_result_artifacts("kb", {"x": 1}, refs, images)
        self.assertEqual(refs, [])
        self.assertEqual(images, [])


class FinalizeResultTests(unittest.TestCase):
    def test_empty_result_falls_back_to_current(self) -> None:
        queue = MagicMock()
        out = _finalize_agent_result("", "streamed", [], queue)
        self.assertEqual(out, "streamed")
        queue.result.assert_called_with("streamed")

    def test_none_result_without_current_stays_empty(self) -> None:
        queue = MagicMock()
        out = _finalize_agent_result(None, "", [], queue)
        self.assertIsNone(out)


class ProcessSseEventTests(unittest.TestCase):
    def test_data_event_appends_stream(self) -> None:
        queue = MagicMock()
        state = {"current": "", "result": "", "image_url": []}
        _process_strands_sse_event({"data": "abc"}, queue, state)
        self.assertEqual(state["current"], "abc")
        queue.stream.assert_called_with("abc")

    def test_result_event_sets_messages(self) -> None:
        queue = MagicMock()
        state = {"current": "partial", "result": "", "image_url": []}
        _process_strands_sse_event(
            {"result": {"messages": "final", "image_url": ["u1"]}},
            queue,
            state,
        )
        self.assertEqual(state["result"], "final")
        self.assertEqual(state["image_url"], ["u1"])

    @patch("application.agentcore_sse.get_tool_info")
    def test_tool_result_collects_references(self, mock_get_tool_info) -> None:
        from application import agentcore_sse as sse

        sse.tool_name_list.clear()
        sse.tool_info_list.clear()
        sse.tool_result_list.clear()

        mock_get_tool_info.return_value = (
            "body",
            ["https://example.com/a"],
            [{"url": "https://example.com/a", "title": "a"}],
        )
        queue = MagicMock()
        state = {
            "current": "",
            "result": "",
            "image_url": [],
            "references": [],
        }
        sse.tool_name_list["tid-1"] = "tavily_search"
        _process_strands_sse_event(
            {
                "toolResult": {"ok": True},
                "toolUseId": "tid-1",
            },
            queue,
            state,
        )
        mock_get_tool_info.assert_called_once()
        self.assertEqual(state["image_url"], ["https://example.com/a"])
        self.assertEqual(len(state["references"]), 1)
        queue.tool_update.assert_called()


if __name__ == "__main__":
    unittest.main()
