import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from application.task_store_persistence import (
    flush_persist,
    schedule_persist,
    working_db_path,
)

logger = logging.getLogger(__name__)

_APPLICATION_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_APPLICATION_DIR, "data")
_DB_PATH = working_db_path()

DEFAULT_MODEL = "Claude 4.6 Sonnet"
DEFAULT_TASK_TITLE = "New task"
AUTO_TITLE_MAX_LEN = 50
# Upper bound on messages returned for a single task so history that grows over
# time cannot force an unbounded SELECT/fetchall into memory. Callers that need
# older messages can page with the `before`/`limit` parameters.
MAX_MESSAGES_PER_TASK = 2000


class TaskStoreError(Exception):
    """Raised when the task store cannot complete a database operation."""


# Allowlisted ALTER TABLE statements only — never interpolate identifiers into SQL.
_COLUMN_MIGRATIONS: dict[tuple[str, str], str] = {
    ("tasks", "pinned"): "ALTER TABLE tasks ADD COLUMN pinned INTEGER DEFAULT 0",
    ("tasks", "strands_tools_json"): (
        "ALTER TABLE tasks ADD COLUMN strands_tools_json TEXT"
    ),
    ("tasks", "memory_enabled"): (
        "ALTER TABLE tasks ADD COLUMN memory_enabled INTEGER DEFAULT 1"
    ),
}


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, column_def: str
) -> None:
    """Add a column if missing; re-raise unexpected schema errors."""
    sql = _COLUMN_MIGRATIONS.get((table, column))
    if sql is None or f"ADD COLUMN {column} {column_def}" not in sql:
        raise ValueError(f"Unsupported schema migration: {table}.{column}")
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "duplicate column" in message or "already exists" in message:
            return
        logger.error(
            "Failed to add column %s.%s: %s", table, column, type(exc).__name__
        )
        raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as e:
        logger.error("Failed to connect to task store DB: %s", e, exc_info=True)
        raise TaskStoreError("Unable to connect to task store") from e


def init_db() -> None:
    global _DB_PATH
    _DB_PATH = working_db_path()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              title TEXT,
              runtime_session_id TEXT NOT NULL UNIQUE,
              model_name TEXT,
              skills_json TEXT,
              mcp_servers_json TEXT,
              guardrail_enabled INTEGER DEFAULT 0,
              memory_enabled INTEGER DEFAULT 1,
              created_at TEXT,
              updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT,
              images_json TEXT,
              tool_events_json TEXT,
              created_at TEXT,
              FOREIGN KEY (task_id) REFERENCES tasks(id)
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_user_updated
              ON tasks(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_task_created
              ON messages(task_id, created_at ASC);
            """
        )
        _ensure_column(conn, "tasks", "pinned", "INTEGER DEFAULT 0")
        _ensure_column(conn, "tasks", "strands_tools_json", "TEXT")
        _ensure_column(conn, "tasks", "memory_enabled", "INTEGER DEFAULT 1")


def _after_write() -> None:
    schedule_persist()


def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"] or "New task",
        "runtime_session_id": row["runtime_session_id"],
        "model_name": row["model_name"] or DEFAULT_MODEL,
        "skills": json.loads(row["skills_json"] or "[]"),
        "mcp_servers": json.loads(row["mcp_servers_json"] or "[]"),
        "guardrail_enabled": bool(row["guardrail_enabled"]),
        "memory_enabled": bool(row["memory_enabled"]) if "memory_enabled" in row.keys() else True,
        "strands_tools": json.loads(row["strands_tools_json"] or "[]") if "strands_tools_json" in row.keys() else [],
        "pinned": bool(row["pinned"]) if "pinned" in row.keys() else False,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "role": row["role"],
        "content": row["content"] or "",
        "images": json.loads(row["images_json"] or "[]"),
        "tool_events": json.loads(row["tool_events_json"] or "[]"),
        "created_at": row["created_at"],
    }


def list_tasks(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE user_id = ?
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_task(task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def get_task_refreshing(task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Return a task, reloading from S3 Files once if missing on this instance."""
    task = get_task(task_id, user_id)
    if task:
        return task
    try:
        from application.task_store_persistence import persistence_enabled, restore_tasks_db

        if not persistence_enabled():
            return None
        restore_tasks_db()
        init_db()
    except Exception:
        return None
    return get_task(task_id, user_id)


def create_task(
    user_id: str,
    *,
    model_name: str | None = None,
    skills: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    strands_tools: list[str] | None = None,
    guardrail_enabled: bool = False,
    memory_enabled: bool = True,
    title: str = "New task",
) -> dict[str, Any]:
    task_id = str(uuid.uuid4())
    runtime_session_id = str(uuid.uuid4())
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
              id, user_id, title, runtime_session_id, model_name,
              skills_json, mcp_servers_json, strands_tools_json, guardrail_enabled,
              memory_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                user_id,
                title,
                runtime_session_id,
                model_name or DEFAULT_MODEL,
                json.dumps(skills or [], ensure_ascii=False),
                json.dumps(mcp_servers or [], ensure_ascii=False),
                json.dumps(strands_tools or [], ensure_ascii=False),
                1 if guardrail_enabled else 0,
                1 if memory_enabled else 0,
                now,
                now,
            ),
        )
    # Flush immediately so sibling ECS tasks / replacements can see the row
    # (debounced persist alone loses creates during rolling deploys).
    flush_persist()
    return get_task(task_id)  # type: ignore[return-value]


def _build_update_task_statements(
    task_id: str,
    user_id: str,
    fields: dict[str, Any],
    now: str,
) -> list[tuple[str, tuple[Any, ...]]]:
    """Build parameterized UPDATE statements from validated field values."""
    statements: list[tuple[str, tuple[Any, ...]]] = []

    if "title" in fields and fields["title"] is not None:
        statements.append(
            (
                "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (fields["title"], now, task_id, user_id),
            )
        )
    if "model_name" in fields and fields["model_name"] is not None:
        statements.append(
            (
                "UPDATE tasks SET model_name = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (fields["model_name"], now, task_id, user_id),
            )
        )
    if "guardrail_enabled" in fields and fields["guardrail_enabled"] is not None:
        statements.append(
            (
                "UPDATE tasks SET guardrail_enabled = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (1 if fields["guardrail_enabled"] else 0, now, task_id, user_id),
            )
        )
    if "memory_enabled" in fields and fields["memory_enabled"] is not None:
        statements.append(
            (
                "UPDATE tasks SET memory_enabled = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (1 if fields["memory_enabled"] else 0, now, task_id, user_id),
            )
        )
    if "pinned" in fields and fields["pinned"] is not None:
        statements.append(
            (
                "UPDATE tasks SET pinned = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (1 if fields["pinned"] else 0, now, task_id, user_id),
            )
        )
    if "skills" in fields and fields["skills"] is not None:
        statements.append(
            (
                "UPDATE tasks SET skills_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (json.dumps(fields["skills"], ensure_ascii=False), now, task_id, user_id),
            )
        )
    if "mcp_servers" in fields and fields["mcp_servers"] is not None:
        statements.append(
            (
                "UPDATE tasks SET mcp_servers_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (
                    json.dumps(fields["mcp_servers"], ensure_ascii=False),
                    now,
                    task_id,
                    user_id,
                ),
            )
        )
    if "strands_tools" in fields and fields["strands_tools"] is not None:
        statements.append(
            (
                "UPDATE tasks SET strands_tools_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (
                    json.dumps(fields["strands_tools"], ensure_ascii=False),
                    now,
                    task_id,
                    user_id,
                ),
            )
        )
    return statements


def update_task(task_id: str, user_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update task fields using static parameterized SQL only (no dynamic SET clauses)."""
    statements = _build_update_task_statements(task_id, user_id, fields, _now_iso())

    if not statements:
        return get_task(task_id, user_id)

    with _connect() as conn:
        for sql, params in statements:
            conn.execute(sql, params)
    _after_write()
    return get_task(task_id, user_id)


def delete_task(task_id: str, user_id: str) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE task_id = ?", (task_id,))
        cur = conn.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
    if cur.rowcount > 0:
        _after_write()
    return cur.rowcount > 0


def list_messages(
    task_id: str,
    limit: int = MAX_MESSAGES_PER_TASK,
) -> list[dict[str, Any]]:
    """Return messages for a task in chronological order, capped at ``limit``.

    The query is bounded so a long-running task with a large message history
    cannot trigger an unbounded scan. When more than ``limit`` messages exist,
    the most recent ``limit`` are returned (still oldest-first).
    """
    capped = max(1, min(int(limit), MAX_MESSAGES_PER_TASK))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (task_id, capped),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def derive_task_title_from_message(
    content: str,
    current_title: str | None,
) -> str | None:
    """
    Business rule: auto-generate a title from the first user message
    only while the task still has the default/empty title.
    """
    title = current_title or DEFAULT_TASK_TITLE
    if title not in (DEFAULT_TASK_TITLE, ""):
        return None
    return content.strip()[:AUTO_TITLE_MAX_LEN] or DEFAULT_TASK_TITLE


def _insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    task_id: str,
    role: str,
    content: str,
    images: list[str],
    tool_events: list[dict[str, Any]],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO messages (
          id, task_id, role, content, images_json, tool_events_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            task_id,
            role,
            content,
            json.dumps(images, ensure_ascii=False),
            json.dumps(tool_events, ensure_ascii=False),
            created_at,
        ),
    )


def _fetch_task_title(conn: sqlite3.Connection, task_id: str) -> str | None:
    row = conn.execute(
        "SELECT title FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return row["title"] if row else None


def _touch_task_after_message(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    updated_at: str,
    title_update: str | None,
) -> None:
    if title_update:
        conn.execute(
            "UPDATE tasks SET updated_at = ?, title = ? WHERE id = ?",
            (updated_at, title_update, task_id),
        )
    else:
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (updated_at, task_id),
        )


def _fetch_message(conn: sqlite3.Connection, message_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM messages WHERE id = ?", (message_id,)
    ).fetchone()
    return _row_to_message(row) if row else {}


class MessageService:
    """Coordinates message persistence and title-generation business rules."""

    def add_message(
        self,
        task_id: str,
        role: str,
        content: str,
        *,
        images: list[str] | None = None,
        tool_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        message_id = str(uuid.uuid4())
        now = _now_iso()
        image_list = images or []
        tool_event_list = tool_events or []

        with _connect() as conn:
            _insert_message(
                conn,
                message_id=message_id,
                task_id=task_id,
                role=role,
                content=content,
                images=image_list,
                tool_events=tool_event_list,
                created_at=now,
            )

            title_update = None
            if role == "user":
                title_update = derive_task_title_from_message(
                    content,
                    _fetch_task_title(conn, task_id),
                )

            _touch_task_after_message(
                conn,
                task_id=task_id,
                updated_at=now,
                title_update=title_update,
            )

        _after_write()
        with _connect() as conn:
            return _fetch_message(conn, message_id)


def add_message(
    task_id: str,
    role: str,
    content: str,
    *,
    images: list[str] | None = None,
    tool_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return MessageService().add_message(
        task_id,
        role,
        content,
        images=images,
        tool_events=tool_events,
    )