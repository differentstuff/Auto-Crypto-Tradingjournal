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
# Lazy import to avoid RuntimeWarning when running `python -m scripts.time_travel`
# (Python finds __main__ in sys.modules before executing it as __main__).
def __getattr__(name):
    if name in ("time_travel", "_write_trade"):
        from scripts.time_travel.__main__ import time_travel, _write_trade
        return time_travel if name == "time_travel" else _write_trade
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")