"""Integration tests for chart_context.confluence_score() and _smt_weight()."""
import sys
import os
import types
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy deps so chart_context can be imported without Bitget/Binance ──
import pandas as pd
import numpy as np

if "bitget_client" not in sys.modules:
    _bc = types.ModuleType("bitget_client")
    _bc.get_ohlcv = MagicMock(return_value=pd.DataFrame())
    sys.modules["bitget_client"] = _bc

if "ccxt_client" not in sys.modules:
    _ccxt = types.ModuleType("ccxt_client")
    _ccxt.get_binance_price = MagicMock(return_value=None)
    _ccxt.get_binance_futures_symbols = MagicMock(return_value=[])
    sys.modules["ccxt_client"] = _ccxt

# chart_indicators and chart_sr already stubbed by conftest, but guard anyway
if "chart_indicators" not in sys.modules:
    _ci = types.ModuleType("chart_indicators")
    _ci.compute_all_indicators = MagicMock(return_value={})
    _ci.compute_wavetrend = MagicMock(return_value={})
    sys.modules["chart_indicators"] = _ci

if "chart_sr" not in sys.modules:
    _csr = types.ModuleType("chart_sr")
    _csr.detect_support_resistance = MagicMock(return_value=[])
    sys.modules["chart_sr"] = _csr


def _mock_ctx(
    rsi=45, macd_trend="bullish", ema_stack="20>50",
    adx=22, wt1=-10, mfi=5, cvd_trend=True,
):
    """Build a minimal chart context dict that confluence_score() can consume."""
    return {
        "4H": {
            "indicators": {
                "ok": True,
                "rsi": {"value": rsi, "signal": "neutral"},
                "macd": {
                    "trend": macd_trend,
                    "histogram_trend": "rising",
                    "crossover": False,
                    "crossunder": False,
                },
                "ema": {
                    "stack": ema_stack,
                    "alignment": "bullish",
                    "current_price": 60000.0,
                },
                "adx": {"value": adx, "strength": "moderate", "direction": "bullish"},
                "wavetrend": {
                    "wt1": wt1,
                    "wt2": wt1 - 2,
                    "signal": None,
                    "zone": "neutral",
                    "mfi": mfi,
                    "cross_up": False,
                    "cross_down": False,
                },
                "cvd": {
                    "trend": "bullish" if cvd_trend else "bearish",
                    "rising": cvd_trend,
                },
                "volume": {"ratio": 1.0, "signal": "normal"},
            }
        }
    }


def test_confluence_score_returns_dict():
    """confluence_score must return a dict with score, max, and label."""
    from chart_context import confluence_score
    with patch("chart_context.get_chart_context", return_value=_mock_ctx()):
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"])
    assert isinstance(result, dict)
    assert "score" in result
    assert "max" in result
    assert "label" in result


def test_confluence_score_has_label(self=None):
    """Label field must be a non-empty string."""
    from chart_context import confluence_score
    with patch("chart_context.get_chart_context", return_value=_mock_ctx()):
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"])
    assert isinstance(result["label"], str)
    assert len(result["label"]) > 0


def test_confluence_score_max_positive():
    """max field must be a positive number."""
    from chart_context import confluence_score
    with patch("chart_context.get_chart_context", return_value=_mock_ctx()):
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"])
    assert result["max"] > 0


def test_confluence_score_bullish_signals_raise_score():
    """Oversold RSI + bullish MACD + strong ADX → positive score."""
    from chart_context import confluence_score
    with patch("chart_context.get_chart_context",
               return_value=_mock_ctx(rsi=25, macd_trend="bullish", adx=30)):
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"])
    assert result["score"] > 0


def test_confluence_score_neutral_indicators():
    """Neutral indicators → score close to 0 (label 'Neutral' or similar)."""
    from chart_context import confluence_score
    neutral_ctx = {
        "4H": {
            "indicators": {
                "ok": True,
                "rsi": {"value": 50, "signal": "neutral"},
                "macd": {"trend": "neutral", "histogram_trend": "flat",
                         "crossover": False, "crossunder": False},
                "ema": {"stack": "flat", "alignment": "neutral", "current_price": 60000.0},
                "adx": {"value": 15, "strength": "weak", "direction": "neutral"},
                "wavetrend": {"wt1": 0, "wt2": -2, "signal": None, "zone": "neutral",
                              "mfi": 0, "cross_up": False, "cross_down": False},
                "cvd": {"trend": "neutral", "rising": False},
                "volume": {"ratio": 1.0, "signal": "normal"},
            }
        }
    }
    with patch("chart_context.get_chart_context", return_value=neutral_ctx):
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"])
    # Score should be near zero for neutral indicators
    assert abs(result["score"]) < result["max"]


def test_confluence_score_non_smt_symbol_zero_smt():
    """Non-SMT symbol (AAVEUSDT) gets 0 SMT weight regardless of price."""
    from chart_context import _smt_weight
    inds = {"ema": {"current_price": 100.0}}
    weight = _smt_weight(inds, "AAVEUSDT")
    assert weight == 0.0


def test_smt_weight_btcusdt_no_price():
    """_smt_weight returns 0.0 when Binance price is None (even for BTCUSDT)."""
    from chart_context import _smt_weight
    inds = {"ema": {"current_price": 60000.0}}
    with patch("chart_confluence.get_binance_price", return_value=None):
        weight = _smt_weight(inds, "BTCUSDT")
    assert weight == 0.0


def test_smt_weight_btcusdt_prices_converge():
    """_smt_weight returns +0.15 when prices are within 0.5%."""
    from chart_context import _smt_weight
    inds = {"ema": {"current_price": 60000.0}}
    # 60000 vs 60100 → delta = 0.167% < 0.5%
    with patch("chart_confluence.get_binance_price", return_value=60100.0):
        weight = _smt_weight(inds, "BTCUSDT")
    assert weight == 0.15


def test_smt_weight_btcusdt_prices_diverge():
    """_smt_weight returns 0.0 when prices diverge > 0.5%."""
    from chart_context import _smt_weight
    inds = {"ema": {"current_price": 60000.0}}
    # 60000 vs 60400 → delta = 0.667% > 0.5%
    with patch("chart_confluence.get_binance_price", return_value=60400.0):
        weight = _smt_weight(inds, "BTCUSDT")
    assert weight == 0.0


def test_confluence_score_uses_ctx_param():
    """Passing ctx= directly should not call get_chart_context."""
    from chart_context import confluence_score
    ctx = _mock_ctx()
    with patch("chart_context.get_chart_context") as mock_fetch:
        with patch("chart_confluence.get_binance_price", return_value=None):
            result = confluence_score("BTCUSDT", timeframes=["4H"], ctx=ctx)
    mock_fetch.assert_not_called()
    assert "score" in result
