"""
scripts/time_travel -- Fast-forward daemon package.

Replays the daemon's scoring logic on historical OHLCV data, simulates
entries at multiple thresholds, walks forward to find exits, and computes
dollar-math profitability metrics.

Modules:
    scoring     -- Signal extractors + confluence scoring (mirrors live daemon)
    simulation  -- Exit simulation, position sizing, per-trade dollar math
    data        -- OHLCV fetching, indicator pre-computation, helpers
    reporting   -- Aggregate metrics, table/JSON output formatting
    export_trades -- CSV export for TradingView analysis

Usage:
    python -m scripts.time_travel --start 2025-01-01 --symbols BTCUSDT
    python scripts/time_travel/__main__.py --start 2025-01-01
"""

# Re-export public API for backward compatibility.
# Existing tests import from `scripts.time_travel` directly.
from scripts.time_travel.__main__ import (  # noqa: F401
    time_travel,
    _write_trade,
)