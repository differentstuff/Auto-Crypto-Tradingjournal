# tests/test_order_flow.py
import sys
import os
import pytest
import pandas as pd
import numpy as np

# Force the real chart_indicators (not the conftest stub) by evicting the stub
# and importing from the project root before any stub re-registration.
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

# Remove stub module if conftest already registered it
sys.modules.pop("chart_indicators", None)

# Now import the real module (pandas_ta stub is already in place from conftest)
import chart_indicators as _real_ci
compute_order_flow_delta = _real_ci.compute_order_flow_delta


def _make_df(closes, opens=None):
    n  = len(closes)
    o  = opens if opens is not None else [c * 0.999 for c in closes]
    return pd.DataFrame({
        "open":   o,
        "high":   [c * 1.001 for c in closes],
        "low":    [c * 0.999 for c in closes],
        "close":  closes,
        "volume": [100.0] * n,
    })


def test_order_flow_bullish():
    df = _make_df([100, 101, 102, 103, 104, 105],
                  opens=[99, 100, 101, 102, 103, 104])
    result = compute_order_flow_delta(df)
    assert result is not None
    assert result["signal"] == "buying_pressure"
    assert result["delta"] > 0


def test_order_flow_bearish():
    df = _make_df([104, 103, 102, 101, 100, 99],
                  opens=[105, 104, 103, 102, 101, 100])
    result = compute_order_flow_delta(df)
    assert result is not None
    assert result["signal"] == "selling_pressure"
    assert result["delta"] < 0


def test_order_flow_divergence_key_present():
    df = _make_df([100, 101, 102, 103, 104, 106],
                  opens=[99, 100, 101, 102, 103, 107])
    result = compute_order_flow_delta(df)
    assert result is not None
    assert "divergence" in result


def test_order_flow_none_on_short_df():
    df = _make_df([100, 101])
    assert compute_order_flow_delta(df) is None
