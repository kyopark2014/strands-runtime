"""Agent bash tool — runs commands under ARTIFACTS_DIR.

Threat model / shell=True rationale:
  This tool is intentionally a general shell for the agent (pipes, redirects,
  &&/||, globs). shell=False + shlex.split cannot express that surface.
  Mitigations: cwd fixed to ARTIFACTS_DIR, timeout, captured stdout/stderr,
  structured OSError handling. Do not expose this tool to untrusted end-users
  without an allowlist or sandbox.
"""

import logging
import os
import subprocess

from strands import tool

from tools.workspace import (
    WORKING_DIR,
    REPO_ROOT,
    ARTIFACTS_DIR,
    ARTIFACTS_REL,
)

logger = logging.getLogger("strands-agent")

BASH_TIMEOUT_SECONDS = 300


def _ensure_cli_scripts_on_path() -> None:
    """Prepend pip user script dir so CLIs (e.g. browser-use) resolve in subprocess."""
    import site
    import sysconfig

    extra: list[str] = []
    user_base = getattr(site, "USER_BASE", None)
    if user_base:
        user_bin = os.path.join(user_base, "bin")
        if os.path.isdir(user_bin):
            extra.append(user_bin)
    try:
        scripts = sysconfig.get_path("scripts")
        if scripts and os.path.isdir(scripts):
            extra.append(scripts)
    except Exception:
        pass
    path = os.environ.get("PATH", "")
    parts = [p for p in path.split(os.pathsep) if p]
    for d in reversed(extra):
        if d and d not in parts:
            parts.insert(0, d)
    os.environ["PATH"] = os.pathsep.join(parts)


@tool
def bash(command: str) -> str:
    """Execute a bash command from artifacts/ and return the result.

    Working directory is ARTIFACTS_DIR. Save outputs by filename only
    (e.g. node create_skills_doc.js, output.docx). Skill scripts must use
    $WORKING_DIR/skills/... (not a relative skills/ path).
    """
    logger.info(f"###### bash: {command} ######")
    if not isinstance(command, str) or not command.strip():
        return "Error: empty command"

    _ensure_cli_scripts_on_path()
    try:
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    except OSError as e:
        logger.warning("bash: failed to create artifacts dir: %s", e)
        return f"Error: could not create artifacts directory ({type(e).__name__})"
    env = {
        **os.environ,
        "REPO_ROOT": REPO_ROOT,
        "WORKING_DIR": WORKING_DIR,
        "ARTIFACTS_DIR": ARTIFACTS_DIR,
        "ARTIFACTS_REL": ARTIFACTS_REL,
    }
    try:
        # Intentional agent shell tool; shell=True required for pipes/redirects/&&/globs.
        # cwd pinned to ARTIFACTS_DIR, 300s timeout, captured I/O. See module docstring
        # threat model. Inline suppressions are kept on the exact flagged lines so the
        # scanner honors them (adjacent-line placement is unreliable across tools).
        result = subprocess.run(  # nosec B602  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            command,
            shell=True,  # nosec B602  # nosemgrep: python.lang.security.audit.subprocess-shell-true
            capture_output=True,
            text=True,
            cwd=ARTIFACTS_DIR,
            timeout=BASH_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("bash timed out after %ss: %s", BASH_TIMEOUT_SECONDS, command[:200])
        return f"Error: command timed out after {BASH_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        logger.warning("bash: shell or command not found")
        return "Error: shell or command not found"
    except PermissionError:
        logger.warning("bash: permission denied for command")
        return "Error: permission denied"
    except OSError as e:
        logger.warning("bash OSError: %s", e)
        return f"Error: OS error ({type(e).__name__})"

    parts = []
    if result.stdout:
        parts.append(f"STDOUT:\n{result.stdout}")
    if result.stderr:
        parts.append(f"STDERR:\n{result.stderr}")
    if result.returncode != 0:
        parts.append(f"Return code: {result.returncode}")
    return "\n".join(parts) if parts else "(no output)"
