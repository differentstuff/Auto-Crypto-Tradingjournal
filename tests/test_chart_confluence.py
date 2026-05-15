"""Tests for chart_confluence weight functions."""
import pytest


def test_smt_weight_zero_on_price_agreement(monkeypatch):
    """_smt_weight returns 0.0 when prices agree (no divergence = no signal)."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_price", lambda s: 100.0)
    inds = {"ema": {"current_price": 100.2}}  # 0.2% delta — below 0.5% threshold
    assert cc._smt_weight(inds, "BTCUSDT") == 0.0


def test_smt_weight_signal_on_divergence(monkeypatch):
    """_smt_weight returns 0.15 when prices diverge >= 0.5%."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_price", lambda s: 98.0)
    inds = {"ema": {"current_price": 100.0}}  # 2% delta — above threshold
    assert cc._smt_weight(inds, "BTCUSDT") == 0.15


def test_smt_weight_unknown_symbol(monkeypatch):
    """_smt_weight returns 0.0 for symbols not in SMT_SYMBOLS."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_price", lambda s: 1.0)
    inds = {"ema": {"current_price": 1.0}}
    assert cc._smt_weight(inds, "DOGEUSDT") == 0.0


def test_smt_weight_missing_price(monkeypatch):
    """_smt_weight returns 0.0 when Bitget price unavailable."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_price", lambda s: 100.0)
    assert cc._smt_weight({}, "BTCUSDT") == 0.0
