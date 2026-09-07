"""Per-task cancel flags for cooperative agent interruption.

Stop in the UI calls POST /api/tasks/{id}/cancel, which sets a flag checked by
the LangGraph astream loop, should_continue, call_model, and the tool node.
The same thread_id checkpoint is kept so the next user turn continues history.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# task_id / runtime_session_id -> cancelled_at monotonic time
_cancelled: dict[str, float] = {}
_CANCEL_TTL_SECONDS = 3600

# Final texts the runtime may emit when the client disconnects mid-stream.
# After Stop these must not be persisted as assistant answers.
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
    """Return True if cancelled, clearing the flag (one-shot for late-persist skip)."""
    if not task_id:
        return False
    with _lock:
        ts = _cancelled.pop(task_id, None)
        if ts is None:
            return False
        return time.monotonic() - ts <= _CANCEL_TTL_SECONDS


def is_cancel_noise(text: str | None) -> bool:
    """True when text is a generic disconnect/failure message after Stop."""
    t = (text or "").strip()
    if not t:
        return False
    if t in _CANCEL_NOISE_MESSAGES:
        return True
    if t.startswith("Error: ") and t[7:].strip() in _CANCEL_NOISE_MESSAGES:
        return True
    return False
