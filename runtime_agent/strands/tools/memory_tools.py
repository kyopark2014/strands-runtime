import json
import logging
from pathlib import Path

from strands import tool

from tools.workspace import WORKING_DIR

logger = logging.getLogger("strands-agent")


@tool
def memory_search(query: str, max_results: int = 5, min_score: float = 0.0) -> str:
    """Search across memory files (MEMORY.md and memory/*.md) for relevant information.

    Performs keyword-based search over all memory files and returns matching snippets
    ranked by relevance score.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default: 5).
        min_score: Minimum relevance score threshold 0.0-1.0 (default: 0.0).

    Returns:
        JSON array of matching snippets with text, path, from (line), lines, and score.
    """
    import re as _re
    logger.info(f"###### memory_search: {query} ######")

    memory_root = Path(WORKING_DIR)
    memory_dir = memory_root / "memory"

    target_files = []
    memory_md = memory_root / "MEMORY.md"
    if memory_md.exists():
        target_files.append(memory_md)
    if memory_dir.exists():
        target_files.extend(sorted(memory_dir.glob("*.md"), reverse=True))

    if not target_files:
        return json.dumps([], ensure_ascii=False)

    query_lower = query.lower()
    query_tokens = [token for token in _re.split(r'\s+', query_lower) if len(token) >= 2]

    results = []
    for fpath in target_files:
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception:
            continue

        lines = content.split("\n")
        content_lower = content.lower()

        if not any(token in content_lower for token in query_tokens):
            continue

        window_size = 5
        for i in range(0, len(lines), window_size):
            chunk_lines = lines[i:i + window_size]
            chunk_text = "\n".join(chunk_lines)
            chunk_lower = chunk_text.lower()

            matched_tokens = sum(1 for token in query_tokens if token in chunk_lower)
            if matched_tokens == 0:
                continue

            score = matched_tokens / len(query_tokens) if query_tokens else 0.0

            if score >= min_score:
                rel_path = str(fpath.relative_to(memory_root))
                results.append({
                    "text": chunk_text.strip(),
                    "path": rel_path,
                    "from": i + 1,
                    "lines": len(chunk_lines),
                    "score": round(score, 3),
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:max_results]

    return json.dumps(results, indent=2, ensure_ascii=False)


@tool
def memory_get(path: str, from_line: int = 0, lines: int = 0) -> str:
    """Read a specific memory file (MEMORY.md or memory/*.md).

    Use after memory_search to get full context, or when you know the exact file path.

    Args:
        path: Workspace-relative path (e.g. "MEMORY.md", "memory/2026-03-02.md").
        from_line: Starting line number, 1-indexed (0 = read from beginning).
        lines: Number of lines to read (0 = read entire file).

    Returns:
        JSON with 'text' (file content) and 'path'. Returns empty text if file doesn't exist.
    """
    logger.info(f"###### memory_get: {path} ######")

    full_path = Path(WORKING_DIR) / path

    if not full_path.exists():
        return json.dumps({"text": "", "path": path}, ensure_ascii=False)

    try:
        content = full_path.read_text(encoding="utf-8")

        if from_line > 0 or lines > 0:
            all_lines = content.split("\n")
            start = max(0, from_line - 1)
            if lines > 0:
                end = start + lines
                content = "\n".join(all_lines[start:end])
            else:
                content = "\n".join(all_lines[start:])

        return json.dumps({"text": content, "path": path}, ensure_ascii=False)

    except Exception as exc:
        logger.error("Error reading file %s: %s", path, type(exc).__name__, exc_info=True)
        return json.dumps(
            {"text": f"Error reading file: {type(exc).__name__}", "path": path},
            ensure_ascii=False,
        )
