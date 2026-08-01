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
