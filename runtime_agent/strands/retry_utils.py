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

"""Shared retry helpers for idempotent SDK/API calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0

logger = logging.getLogger("retry_utils")


def retry_call(
    operation: str,
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    log: logging.Logger | None = None,
) -> T:
    """Retry an idempotent call with exponential backoff."""
    log = log or logger
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            log.warning(
                "%s failed (attempt %s/%s): %s",
                operation,
                attempt,
                max_attempts,
                type(exc).__name__,
                exc_info=True,
            )
            if attempt < max_attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error
