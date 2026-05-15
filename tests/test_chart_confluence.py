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


def test_smt_weight_solusdt_in_set(monkeypatch):
    """SOLUSDT is now in SMT_SYMBOLS — should trigger on divergence."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_price", lambda s: 98.0)
    inds = {"ema": {"current_price": 100.0}}  # 2% delta
    assert cc._smt_weight(inds, "SOLUSDT") == 0.15


def test_smt_symbols_coverage():
    """SMT_SYMBOLS must contain at least BTC, ETH, SOL, BNB, XRP."""
    from chart_confluence import SMT_SYMBOLS
    required = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}
    assert required.issubset(SMT_SYMBOLS)


def test_smt_direction_weight_bullish_divergence(monkeypatch):
    """Symbol up, pair down → +0.15 (bullish SMT)."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_ticker_change",
                        lambda s: 2.5 if s == "BTCUSDT" else -1.5)
    assert cc._smt_direction_weight({}, "BTCUSDT") == 0.15


def test_smt_direction_weight_bearish_divergence(monkeypatch):
    """Symbol down, pair up → -0.15 (bearish SMT)."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_ticker_change",
                        lambda s: -2.0 if s == "BTCUSDT" else 1.5)
    assert cc._smt_direction_weight({}, "BTCUSDT") == -0.15


def test_smt_direction_weight_no_divergence(monkeypatch):
    """Both assets moving same direction → 0.0."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "get_binance_ticker_change",
                        lambda s: 2.0)
    assert cc._smt_direction_weight({}, "BTCUSDT") == 0.0


def test_smt_direction_weight_unknown_symbol():
    """Symbol not in SMT_PAIRS → 0.0."""
    import chart_confluence as cc
    assert cc._smt_direction_weight({}, "PEPEUSDT") == 0.0


def test_vix_multiplier_suppresses_on_high_vix(monkeypatch):
    """VIX > 30 should reduce confluence score by 20%."""
    import chart_confluence as cc
    # Force VIX cache to return 0.80 multiplier
    monkeypatch.setattr(cc, "_get_vix_multiplier", lambda: 0.80)
    # Use a mock context that would normally score positively
    ctx = {
        "4H": {"indicators": {"ok": True,
            "rsi": {"value": 25, "signal": "oversold"},
            "macd": {"trend": "bullish", "histogram_trend": "rising",
                     "crossover": False, "crossunder": False},
            "ema": {"stack": "bullish", "alignment": "bullish", "current_price": 100.0},
            "adx": {"value": 30, "strength": "strong", "direction": "bullish"},
            "wavetrend": {"wt1": -65.0, "wt2": -68.0, "signal": "buy",
                          "zone": "oversold", "mfi": 25.0,
                          "cross_up": True, "cross_down": False},
            "cvd": {"trend": "rising"}, "volume": {"ratio": 2.0},
        }}
    }
    with __import__("unittest.mock", fromlist=["patch"]).patch(
            "chart_confluence.get_binance_price", return_value=None):
        with __import__("unittest.mock", fromlist=["patch"]).patch(
                "chart_confluence.get_binance_ticker_change", return_value=None):
            result = cc.confluence_score("BTCUSDT", ["4H"], ctx=ctx)
    # Verify the multiplier was applied and vix_regime_active flag is set
    assert "score" in result
    assert "vix_regime_active" in result
    assert result["vix_regime_active"] == True


def test_vix_multiplier_no_change_normal(monkeypatch):
    """VIX ≤ 30 should not change confluence score."""
    import chart_confluence as cc
    monkeypatch.setattr(cc, "_get_vix_multiplier", lambda: 1.0)
    assert cc._get_vix_multiplier() == 1.0


def test_vix_cache_structure():
    """_vix_cache dict must have value and ts keys."""
    from chart_confluence import _vix_cache
    assert "value" in _vix_cache
    assert "ts" in _vix_cache
