"""Shared workspace paths for builtin tools (runtime PYTHONPATH=/app → strands/)."""

import os

# tools/ → strands/ (WORKING_DIR matches strands_agent.py location)
WORKING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(WORKING_DIR)
SKILLS_DIR = os.path.join(WORKING_DIR, "skills")
ARTIFACTS_DIR = os.path.join(WORKING_DIR, "artifacts")
# Logical alias agents often use in prompts; always resolve to ARTIFACTS_DIR.
ARTIFACTS_REL = "artifacts"

_ARTIFACT_PREFIXES = (
    "application/artifacts/",
    "artifacts/",
)


def force_artifacts_path(path: str) -> str:
    """Map any agent-supplied path onto ARTIFACTS_DIR.

    file_write/file_read otherwise write relative to process cwd (/app), so
    ``application/artifacts/foo.js`` becomes ``/app/application/artifacts/foo.js``
    while bash cwd is ``/app/artifacts``. This helper collapses those aliases
    (and absolute paths under a nested ``.../artifacts/``) onto ARTIFACTS_DIR.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("empty path")

    raw = os.path.expanduser(path.strip()).replace("\\", "/")
    # Drop leading ./ 
    while raw.startswith("./"):
        raw = raw[2:]

    lower = raw.lower()
    suffix = None

    for prefix in _ARTIFACT_PREFIXES:
        if lower == prefix.rstrip("/") or lower == prefix.rstrip("/"):
            return ARTIFACTS_DIR
        if lower.startswith(prefix):
            suffix = raw[len(prefix) :]
            break

    if suffix is None and "/artifacts/" in lower:
        # Absolute or nested: /app/application/artifacts/foo → foo
        suffix = raw.split("/artifacts/", 1)[1]
    elif suffix is None and lower.endswith("/artifacts"):
        return ARTIFACTS_DIR

    if suffix is None:
        # Bare filename or unrelated relative/absolute → basename only under artifacts
        if os.path.isabs(raw):
            suffix = os.path.basename(raw.rstrip("/")) or "file.bin"
        else:
            # Keep simple relative names (incl. wildcards like *.py); strip parent dirs
            # so "../x" cannot escape ARTIFACTS_DIR.
            suffix = raw.lstrip("/")
            if ".." in suffix.split("/"):
                suffix = os.path.basename(suffix) or "file.bin"

    suffix = suffix.lstrip("/")
    if not suffix:
        return ARTIFACTS_DIR

    # Prevent escape via .. after join
    joined = os.path.normpath(os.path.join(ARTIFACTS_DIR, suffix))
    artifacts_real = os.path.realpath(ARTIFACTS_DIR)
    # normpath of non-existing paths is fine; realpath for containment check
    joined_real = os.path.realpath(joined) if os.path.exists(joined) else joined
    # If target does not exist yet, compare normpath prefix manually
    if os.path.exists(joined):
        try:
            if os.path.commonpath([joined_real, artifacts_real]) != artifacts_real:
                joined = os.path.join(ARTIFACTS_DIR, os.path.basename(suffix) or "file.bin")
        except ValueError:
            joined = os.path.join(ARTIFACTS_DIR, os.path.basename(suffix) or "file.bin")
    else:
        # Non-existent: ensure normpath does not walk above ARTIFACTS_DIR
        try:
            if os.path.commonpath([os.path.normpath(joined), os.path.normpath(ARTIFACTS_DIR)]) != os.path.normpath(
                ARTIFACTS_DIR
            ):
                joined = os.path.join(ARTIFACTS_DIR, os.path.basename(suffix) or "file.bin")
        except ValueError:
            joined = os.path.join(ARTIFACTS_DIR, os.path.basename(suffix) or "file.bin")

    return joined


def force_artifacts_paths(path_field: str) -> str:
    """Remap comma-separated path fields (file_read multi-path) onto ARTIFACTS_DIR."""
    if not isinstance(path_field, str) or not path_field.strip():
        return path_field
    parts = [p.strip() for p in path_field.split(",")]
    return ",".join(force_artifacts_path(p) for p in parts if p)
