"""
ai_call_analyzer.py — Backward-compatible re-export shim (v2.1).

Logic has been split into:
  ai_call.py  → analyze_call()
  ai_limit.py → analyze_pending_limit()

All callers (routes/calls.py, routes/limits.py) continue to work unchanged.
"""
from ai_call  import analyze_call           # noqa: F401
from ai_limit import analyze_pending_limit  # noqa: F401
