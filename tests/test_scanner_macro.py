"""Tests for scanner macro context layer."""


def test_get_scan_macro_context_returns_dict():
    """_get_scan_macro_context returns dict with required keys."""
    from scanner_stages import _get_scan_macro_context
    # With mocked dependencies
    from unittest.mock import patch
    with patch("market_context.get_macro_regime", return_value={"vix": 22.0, "regime": "neutral"}):
        with patch("market_context.get_fear_greed", return_value={"value": 45}):
            with patch("finnhub_client.get_upcoming_events", return_value={"macro_risk": False}):
                with patch("coingecko_client.get_global_market", return_value={"btc_dominance_pct": 58.3}):
                    result = _get_scan_macro_context()
    assert "vix" in result
    assert "regime" in result
    assert "macro_risk" in result


def test_macro_cap_vix_above_35():
    """VIX > 35 caps score at 6."""
    from scanner_stages import _apply_macro_cap
    score, warnings = _apply_macro_cap(8.5, {"vix": 38.0, "regime": "risk_off"})
    assert score == 6.0
    assert any("VIX" in w for w in warnings)


def test_macro_cap_no_suppression_normal_vix():
    """Normal VIX (< 25) does not cap score."""
    from scanner_stages import _apply_macro_cap
    score, warnings = _apply_macro_cap(8.5, {"vix": 18.0, "regime": "risk_on"})
    assert score == 8.5
    assert warnings == []


def test_macro_cap_fomc_caps_at_7():
    """Macro risk event caps score at 7."""
    from scanner_stages import _apply_macro_cap
    score, warnings = _apply_macro_cap(8.5, {"vix": 20.0, "macro_risk": True,
                                              "next_event": "FOMC", "hours_until": 8.0})
    assert score <= 7.0
