"""Per-task cancel flags for cooperative agent interruption (runtime process)."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_cancelled: dict[str, float] = {}
_CANCEL_TTL_SECONDS = 3600

_CANCEL_NOISE_MESSAGES = frozenset(
    {
        "에이전트 응답 처리 중 오류가 발생했습니다.",
        "An error occurred processing your request",
        "An error occurred while processing your request. Please try again.",
        "Agent processing failed",
    }
)


def request_cancel(task_id: str) -> None:
    if not task_id:
        return
    with _lock:
        _cancelled[task_id] = time.monotonic()


def is_cancelled(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _lock:
        ts = _cancelled.get(task_id)
        if ts is None:
            return False
        if time.monotonic() - ts > _CANCEL_TTL_SECONDS:
            _cancelled.pop(task_id, None)
            return False
        return True


def clear(task_id: str | None) -> None:
    if not task_id:
        return
    with _lock:
        _cancelled.pop(task_id, None)


def consume_cancelled(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _lock:
        ts = _cancelled.pop(task_id, None)
        if ts is None:
            return False
        return time.monotonic() - ts <= _CANCEL_TTL_SECONDS


def is_cancel_noise(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t in _CANCEL_NOISE_MESSAGES:
        return True
    if t.startswith("Error: ") and t[7:].strip() in _CANCEL_NOISE_MESSAGES:
        return True
    return False
