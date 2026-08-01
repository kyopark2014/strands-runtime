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

"""Unit tests for retry_utils.retry_call."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from retry_utils import retry_call


class RetryCallTests(unittest.TestCase):
    def test_returns_on_first_success(self) -> None:
        self.assertEqual(retry_call("ok", lambda: 42, max_attempts=3), 42)

    def test_retries_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return "done"

        with patch("retry_utils.time.sleep") as sleep_mock:
            result = retry_call("flaky", flaky, max_attempts=3, base_delay=0.01)
        self.assertEqual(result, "done")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_raises_after_exhausted_attempts(self) -> None:
        with patch("retry_utils.time.sleep"):
            with self.assertRaises(ValueError):
                retry_call(
                    "always-fail",
                    lambda: (_ for _ in ()).throw(ValueError("boom")),
                    max_attempts=2,
                    base_delay=0.01,
                )


if __name__ == "__main__":
    unittest.main()
