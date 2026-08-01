"""Shared helpers for per-skill office validator shim modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_SKILLS_ROOT = Path(__file__).resolve().parents[2]
_OFFICE_VALIDATORS = _SKILLS_ROOT / "office" / "validators"


def _ensure_skills_root() -> None:
    root = str(_SKILLS_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def import_office_validator(module: str, attr: str) -> Any:
    """Import ``attr`` from ``skills/office/validators/{module}.py``."""
    _ensure_skills_root()
    imported = __import__(f"office.validators.{module}", fromlist=[attr])
    return getattr(imported, attr)


def load_office_validator_module(module: str, attr: str) -> Any:
    """Load ``attr`` from the shared validator module file by path."""
    target = _OFFICE_VALIDATORS / f"{module}.py"
    label = f"_shared_office_validators_{module}"
    spec = importlib.util.spec_from_file_location(label, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load shared validator module from {target}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return getattr(loaded, attr)
