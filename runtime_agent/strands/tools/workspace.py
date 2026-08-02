"""Shared workspace paths for builtin tools (runtime PYTHONPATH=/app → strands/)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("strands-agent")

# tools/ → strands/ (WORKING_DIR matches strands_agent.py location)
WORKING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(WORKING_DIR)
SKILLS_DIR = os.path.join(WORKING_DIR, "skills")

SESSION_STORAGE_DIR = os.environ.get(
    "SESSION_STORAGE_DIR",
    "/mnt/workspace"
    if os.path.isdir("/mnt/workspace")
    else os.path.join(WORKING_DIR, ".session_storage"),
)


def sanitize_user_path_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user workspace folders, or None."""
    if not user_id:
        return None
    segment = (
        str(user_id)
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
    return segment or None


def get_user_artifacts_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/artifacts (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "artifacts")


def ensure_user_artifacts_dir(user_id: str | None) -> str:
    """Create {SESSION_STORAGE_DIR}/{user_id}/artifacts if needed and return it."""
    artifacts_dir = get_user_artifacts_dir(user_id)
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def get_user_skills_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/skills (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "skills")


def ensure_user_skills_dir(user_id: str | None) -> str:
    """Create {SESSION_STORAGE_DIR}/{user_id}/skills if needed and return it."""
    skills_dir = get_user_skills_dir(user_id)
    os.makedirs(skills_dir, exist_ok=True)
    logger.info("user skills dir ready: %s", skills_dir)
    return skills_dir


def get_user_skills_list_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/skills.list (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "skills.list")


def list_skill_dir_names(skills_dir: str) -> list[str]:
    """Return subdirectory names that contain SKILL.md under skills_dir."""
    if not os.path.isdir(skills_dir):
        return []
    names: list[str] = []
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as e:
        logger.warning("Failed to list skills directory %s: %s", skills_dir, e)
        return []
    for entry in entries:
        skill_md = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_md):
            names.append(entry)
    return names


def load_skills_list_file(path: str) -> list[str]:
    """Load skill names from a skills.list file (ignore blanks/comments)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("Failed to read skills.list %s: %s", path, e)
        return []


def builtin_skill_names() -> list[str]:
    """Builtin skill names discovered by scanning SKILLS_DIR for SKILL.md."""
    return list_skill_dir_names(SKILLS_DIR)


def _merged_skill_names(user_id: str | None) -> list[str]:
    """Builtin skills/ dirs + per-user skill-creator skills (deduped, stable order)."""
    merged: list[str] = []
    seen: set[str] = set()
    for name in builtin_skill_names() + list_skill_dir_names(get_user_skills_dir(user_id)):
        if name not in seen:
            merged.append(name)
            seen.add(name)
    return merged


def write_user_skills_list(user_id: str | None, names: list[str] | None = None) -> str:
    """Write {SESSION_STORAGE_DIR}/{user_id}/skills.list and return its path."""
    ensure_user_skills_dir(user_id)
    path = get_user_skills_list_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = names if names is not None else _merged_skill_names(user_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged) + ("\n" if merged else ""))
    logger.info("wrote user skills.list (%d skills) -> %s", len(merged), path)
    return path


def ensure_user_skills_list(user_id: str | None) -> str:
    """Use {SESSION_STORAGE_DIR}/{user_id}/skills.list; create it if missing.

    Runtime does not read ``strands/skills.list``. The active list always lives
    under the per-user workspace mount. When creating, seed from ``skills/``
    directories plus ``{user-id}/skills/``. If the file already exists, only
    newly discovered skill-creator dirs under ``{user-id}/skills/`` are appended.
    """
    ensure_user_skills_dir(user_id)
    path = get_user_skills_list_path(user_id)
    if not os.path.isfile(path):
        return write_user_skills_list(user_id)

    existing = load_skills_list_file(path)
    seen = set(existing)
    appended = [
        name
        for name in list_skill_dir_names(get_user_skills_dir(user_id))
        if name not in seen
    ]
    if appended:
        return write_user_skills_list(user_id, existing + appended)
    return path


def update_user_skills_list(user_id: str | None) -> str:
    """Refresh {SESSION_STORAGE_DIR}/{user_id}/skills.list from skills dirs.

    Rebuilds the list from builtin ``skills/`` and skill-creator skills under
    ``{user-id}/skills/``. Prefer ``ensure_user_skills_list`` when only
    create-if-missing is needed.
    """
    return write_user_skills_list(user_id)


def set_user_artifacts(user_id: str | None) -> str:
    """Point ARTIFACTS_DIR at {SESSION_STORAGE_DIR}/{user_id}/artifacts."""
    global ARTIFACTS_DIR
    artifacts_dir = ensure_user_artifacts_dir(user_id)
    ARTIFACTS_DIR = artifacts_dir
    return artifacts_dir


def set_user_skills(user_id: str | None) -> str:
    """Point USER_SKILLS_DIR and ensure per-user skills.list exists."""
    global USER_SKILLS_DIR
    skills_dir = ensure_user_skills_dir(user_id)
    USER_SKILLS_DIR = skills_dir
    ensure_user_skills_list(user_id)
    return skills_dir


def set_user_workspace(user_id: str | None) -> tuple[str, str]:
    """Configure per-user artifacts + skills dirs; create skills.list if missing."""
    artifacts_dir = set_user_artifacts(user_id)
    skills_dir = set_user_skills(user_id)
    return artifacts_dir, skills_dir


def path_is_under(path: str, root: str) -> bool:
    """True when path resolves under root (normpath containment check)."""
    if not path or not root:
        return False
    try:
        norm_path = os.path.normpath(path)
        norm_root = os.path.normpath(root)
        return os.path.commonpath([norm_path, norm_root]) == norm_root
    except ValueError:
        return False


ARTIFACTS_DIR = get_user_artifacts_dir("default")
USER_SKILLS_DIR = get_user_skills_dir("default")
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


def _expand_user_skills_placeholders(raw: str) -> str:
    """Expand $USER_SKILLS_DIR / ${USER_SKILLS_DIR} using the current workspace path."""
    if not USER_SKILLS_DIR or "$" not in raw:
        return raw
    expanded = raw
    for token in ("${USER_SKILLS_DIR}", "$USER_SKILLS_DIR", "${user_skills_dir}", "$user_skills_dir"):
        expanded = expanded.replace(token, USER_SKILLS_DIR)
    return expanded


def _normalize_under_root(raw: str, root: str) -> str | None:
    """If raw targets root (absolute or skills/ relative), return a path under root."""
    if not root:
        return None
    norm_root = os.path.normpath(root)
    raw = _expand_user_skills_placeholders(raw)
    candidate = None

    if os.path.isabs(raw) and path_is_under(os.path.normpath(raw), norm_root):
        candidate = os.path.normpath(raw)
    else:
        lower = raw.lower()
        skills_prefixes = (
            "skills/",
            "user-skills/",
        )
        for prefix in skills_prefixes:
            if lower == prefix.rstrip("/"):
                return norm_root
            if lower.startswith(prefix):
                suffix = raw[len(prefix) :].lstrip("/")
                candidate = os.path.normpath(os.path.join(norm_root, suffix))
                break

    if candidate is None:
        return None
    if ".." in os.path.relpath(candidate, norm_root).split(os.sep):
        return None
    if not path_is_under(candidate, norm_root):
        return None
    return candidate


def resolve_agent_file_path(path: str) -> str:
    """Allow writes/reads under USER_SKILLS_DIR; otherwise remap onto ARTIFACTS_DIR."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("empty path")

    raw = os.path.expanduser(path.strip()).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = _expand_user_skills_placeholders(raw)

    under_skills = _normalize_under_root(raw, USER_SKILLS_DIR)
    if under_skills is not None:
        return under_skills
    return force_artifacts_path(path)


def resolve_agent_file_paths(path_field: str) -> str:
    """Remap comma-separated paths via resolve_agent_file_path."""
    if not isinstance(path_field, str) or not path_field.strip():
        return path_field
    parts = [p.strip() for p in path_field.split(",")]
    return ",".join(resolve_agent_file_path(p) for p in parts if p)
