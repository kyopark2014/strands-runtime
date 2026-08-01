"""Model catalog — thin re-export of runtime_agent/strands/info.py (single source of truth).

Canonical module: runtime_agent/strands/info.py
Web UI image includes the full repo, so this shim loads the runtime catalog via importlib
instead of maintaining a second copy. AgentCore Runtime image uses info.py directly.
Consolidation is intentional — do not duplicate model lists here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RUNTIME_INFO = (
    Path(__file__).resolve().parents[1] / "runtime_agent" / "strands" / "info.py"
)

_spec = importlib.util.spec_from_file_location("_cde_shared_model_info", _RUNTIME_INFO)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load shared model info from {_RUNTIME_INFO}")
_module = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_cde_shared_model_info", _module)
_spec.loader.exec_module(_module)

# Re-export public API used by application.chat and callers.
get_model_info = _module.get_model_info
get_stop_sequence = _module.get_stop_sequence

# Re-export model list constants for any direct attribute access.
for _name in dir(_module):
    if _name.startswith("_"):
        continue
    globals()[_name] = getattr(_module, _name)

del _name, _module, _spec, _RUNTIME_INFO
