import io
import json
import logging
import os
import sys
import subprocess as _subprocess
import pathlib as _pathlib
import shutil as _shutil
import tempfile as _tempfile
import glob as _glob
import datetime as _datetime
import math as _math
import re as _re
import requests as _requests

from strands import tool

import tools.workspace as workspace
from tools.workspace import WORKING_DIR, REPO_ROOT, ARTIFACTS_REL

logger = logging.getLogger("strands-agent")

_ARTIFACT_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
})

_mpl_runtime_ready = False
_MAX_CODE_BYTES = 512_000


def _validate_executable_code(code: str) -> str | None:
    """Return an error message if code is unsafe to execute, else None."""
    if not isinstance(code, str):
        return "Error: code must be a string."
    if "\x00" in code:
        return "Error: code contains null bytes."
    if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
        return f"Error: code exceeds maximum allowed size ({_MAX_CODE_BYTES} bytes)."
    return None


def _artifact_files_mtime_snapshot() -> dict:
    """Relative path from workspace.ARTIFACTS_DIR -> mtime."""
    snap = {}
    artifacts_dir = workspace.ARTIFACTS_DIR
    if not os.path.isdir(artifacts_dir):
        return snap
    for dirpath, _, filenames in os.walk(artifacts_dir):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                rel = os.path.relpath(full, artifacts_dir)
                snap[rel] = os.path.getmtime(full)
            except OSError:
                pass
    return snap


def _touched_artifact_paths(before: dict, after: dict) -> list:
    """Only files created or modified between pre/post execution snapshots."""
    touched = []
    for rel, mt in after.items():
        if rel not in before or before[rel] != mt:
            touched.append(rel)
    return sorted(touched)


def _paths_for_ui(relative_paths: list) -> list:
    """Absolute path for Streamlit st.image."""
    return [os.path.abspath(os.path.join(workspace.ARTIFACTS_DIR, rel)) for rel in relative_paths]


def _ensure_matplotlib_runtime():
    """Use non-interactive Agg backend, prefer CJK-capable fonts, silence headless/show noise."""
    global _mpl_runtime_ready
    if _mpl_runtime_ready:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")

        import warnings

        warnings.filterwarnings(
            "ignore",
            message=r"Glyph .* missing from font",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r"FigureCanvasAgg is non-interactive.*",
            category=UserWarning,
        )

        import matplotlib.font_manager as fm  # noqa: F401
        import matplotlib as mpl

        mpl.rcParams["axes.unicode_minus"] = False
        cjk_candidates = (
            "AppleGothic",
            "Apple SD Gothic Neo",
            "Malgun Gothic",
            "NanumGothic",
            "NanumBarunGothic",
            "Noto Sans CJK KR",
            "Noto Sans KR",
        )
        mpl.rcParams["font.family"] = "sans-serif"
        mpl.rcParams["font.sans-serif"] = list(cjk_candidates) + ["DejaVu Sans", "sans-serif"]

        _mpl_runtime_ready = True
    except Exception as e:
        logger.info(f"matplotlib runtime setup skipped: {e}")
        _mpl_runtime_ready = True


_exec_globals = {
    "__builtins__": __builtins__,
    "subprocess": _subprocess,
    "json": json,
    "os": os,
    "sys": sys,
    "io": io,
    "pathlib": _pathlib,
    "shutil": _shutil,
    "tempfile": _tempfile,
    "glob": _glob,
    "datetime": _datetime,
    "math": _math,
    "re": _re,
    "requests": _requests,
    "WORKING_DIR": WORKING_DIR,
    "REPO_ROOT": REPO_ROOT,
    "ARTIFACTS_DIR": workspace.ARTIFACTS_DIR,
    "ARTIFACTS_REL": ARTIFACTS_REL,
}


@tool
def execute_code(code: str) -> str:
    """Execute Python code and return stdout/stderr output.

    Use this tool to run Python code for tasks such as processing data,
    processing data, or performing computations. The execution environment
    has access to common libraries: pandas, numpy, matplotlib, seaborn, etc.
    json, csv, os, requests, etc.

    Variables and imports from previous calls persist across invocations.
    Working directory is artifacts/ (ARTIFACTS_DIR). Save generated files by filename only
    (e.g. report.docx), not application/artifacts/report.docx.

    Path variables (pre-defined, do NOT redefine):
    - REPO_ROOT: absolute path to repository root
    - WORKING_DIR: absolute path to the strands/application runtime root
    - ARTIFACTS_DIR: absolute path to artifacts/
    - ARTIFACTS_REL: workspace-relative path "artifacts"

    Args:
        code: Python code to execute.

    Returns:
        Captured stdout output, or a sanitized error message if execution failed.
        If there is a result file, return the path of the file.            
    """
    logger.info(f"###### execute_code ######")
    validation_error = _validate_executable_code(code)
    if validation_error:
        return validation_error

    os.makedirs(workspace.ARTIFACTS_DIR, exist_ok=True)
    _exec_globals["ARTIFACTS_DIR"] = workspace.ARTIFACTS_DIR
    before_files = _artifact_files_mtime_snapshot()

    old_cwd = os.getcwd()
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    try:
        os.chdir(workspace.ARTIFACTS_DIR)
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_capture, stderr_capture

        _ensure_matplotlib_runtime()
        # Intentional sandboxed Python tool: cwd pinned to workspace.ARTIFACTS_DIR, I/O
        # captured, size/null validated above. Not for untrusted multi-tenant use.
        exec(code, _exec_globals)  # nosec B102  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected

        sys.stdout, sys.stderr = old_stdout, old_stderr
        os.chdir(old_cwd)

        output = stdout_capture.getvalue()
        errors = stderr_capture.getvalue()

        result = ""
        if output:
            result += output
        if errors:
            result += f"\n[stderr]\n{errors}"
        if not result.strip():
            result = "Code executed successfully (no output)."

        after_files = _artifact_files_mtime_snapshot()
        touched = _touched_artifact_paths(before_files, after_files)
        artifact_rels = [
            rel_path
            for rel_path in touched
            if os.path.splitext(rel_path)[1].lower() in _ARTIFACT_EXT
        ]
        other_rels = [rel_path for rel_path in touched if rel_path not in artifact_rels]
        if other_rels:
            lines = "\n".join(
                os.path.abspath(os.path.join(workspace.ARTIFACTS_DIR, rel_path))
                for rel_path in other_rels
            )
            result += f"\n[artifacts]\n{lines}"

        if artifact_rels:
            payload = {"output": result.strip()}
            payload["path"] = _paths_for_ui(artifact_rels)
            return json.dumps(payload, ensure_ascii=False)

        return result

    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        os.chdir(old_cwd)
        logger.error("Code execution error", exc_info=True)
        return f"Error executing code: {type(e).__name__}"
