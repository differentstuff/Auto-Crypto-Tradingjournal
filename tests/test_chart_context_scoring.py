"""Tests for _mfi_weight and its integration into confluence_score."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_mfi_weight_bullish():
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": 30.0}) == 0.3


def test_mfi_weight_bearish():
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": -25.0}) == -0.3


def test_mfi_weight_deadband_positive():
    """Values between -10 and +10 return 0."""
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": 5.0}) == 0.0


def test_mfi_weight_deadband_negative():
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": -8.0}) == 0.0


def test_mfi_weight_empty_dict():
    from chart_confluence import _mfi_weight
    assert _mfi_weight({}) == 0.0


def test_mfi_weight_none():
    from chart_confluence import _mfi_weight
    assert _mfi_weight(None) == 0.0


def test_mfi_weight_boundary_positive_10():
    """Exactly 10 is inside the dead-band → 0."""
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": 10.0}) == 0.0


def test_mfi_weight_boundary_above_10():
    """11 is outside dead-band → bullish."""
    from chart_confluence import _mfi_weight
    assert _mfi_weight({"mfi": 11.0}) == 0.3


def test_confluence_score_max_val_updated():
    """max_val in confluence_score must equal len(tfs) * 6.35 when SMT included."""
    from chart_context import confluence_score
    import unittest.mock as mock

    mock_ctx = {
        "4H": {"indicators": {"ok": True, "rsi": {"value": 50}, "macd": {}, "ema": {},
                               "adx": {}, "wavetrend": {"mfi": 0.0}, "cvd": {}, "volume": {}}},
        "1D": {"indicators": {"ok": True, "rsi": {"value": 50}, "macd": {}, "ema": {},
                               "adx": {}, "wavetrend": {"mfi": 0.0}, "cvd": {}, "volume": {}}},
    }
    with mock.patch("chart_context.get_chart_context", return_value=mock_ctx):
        result = confluence_score("BTCUSDT", ["4H", "1D"], ctx=mock_ctx)
    assert result["max"] == pytest.approx(2 * 6.35, rel=1e-3), \
        f"Expected max=12.7, got {result['max']}"


def test_confluence_score_mfi_raises_bullish_score():
    """Strong bullish MFI increases the score compared to neutral MFI."""
    from chart_context import confluence_score
    import unittest.mock as mock

    def _make_ctx(mfi_val):
        return {
            "4H": {"indicators": {"ok": True,
                "rsi": {"value": 65}, "macd": {"trend": "bullish", "histogram_trend": "growing"},
                "ema": {"alignment": "fully bullish", "stack": "bullish"},
                "adx": {"direction": "bullish", "value": 30},
                "wavetrend": {"signal": "buy", "wt1": 20.0, "mfi": mfi_val},
                "cvd": {"trend": "rising"}, "volume": {"ratio": 1.8}}},
        }

    score_with_mfi = confluence_score("BTCUSDT", ["4H"], ctx=_make_ctx(50.0))
    score_neutral   = confluence_score("BTCUSDT", ["4H"], ctx=_make_ctx(0.0))

    assert score_with_mfi["score"] > score_neutral["score"], \
        "Bullish MFI should increase confluence score"


# --- SMT Divergence tests ---

def test_smt_weight_btc_prices_in_sync():
    """Bitget and Binance prices within 0.5% -> 0.0 (agreement = no divergence signal)."""
    from unittest.mock import patch
    import chart_confluence

    inds = {"ema": {"current_price": 60000.0}}
    with patch("chart_confluence.get_binance_price", return_value=60200.0):
        result = chart_confluence._smt_weight(inds, "BTCUSDT")
    assert result == 0.0


def test_smt_weight_divergence_neutral():
    """Bitget and Binance prices differ > 0.5% -> +0.15 (SMT divergence detected)."""
    from unittest.mock import patch
    import chart_confluence

    inds = {"ema": {"current_price": 60000.0}}
    with patch("chart_confluence.get_binance_price", return_value=62000.0):
        result = chart_confluence._smt_weight(inds, "BTCUSDT")
    assert result == 0.15


def test_smt_weight_non_smt_symbol():
    """Non-SMT symbols (e.g. AAVEUSDT) always return 0.0."""
    import chart_confluence

    inds = {"ema": {"current_price": 100.0}}
    result = chart_confluence._smt_weight(inds, "AAVEUSDT")
    assert result == 0.0
