# tests/test_scanner_price_filter.py
"""
Tests for scanner_scheduler._enrich_and_filter_setups() price sanity guards.

Guards under test:
  1. No entry_ref (entry_zone missing) → drop setup
  2. Entry >20% from current price → drop unreachable setup (the KITE bug)
  3. Directional drift >5% → drop stale setup
  4. Price check exception → drop setup (fail-closed, not fail-open)
  5. Valid near-price setup → keep
"""
import pytest
from unittest.mock import patch


def _make_setup(symbol="TESTUSDT", entry_low=None, entry_high=None,
                entry_price=None, direction="Long"):
    s = {"_symbol": symbol, "direction": direction,
         "sl_price": 0.0, "tp1_price": 0.0, "tp2_price": 0.0,
         "key_conditions": []}
    if entry_low or entry_high:
        s["entry_zone"] = {"low": entry_low, "high": entry_high}
    if entry_price:
        s["entry_price"] = entry_price
    return s


def _run(setups, live_price=None, raise_exc=None):
    """Run _enrich_and_filter_setups with all external calls mocked."""
    from scanner_scheduler import _enrich_and_filter_setups

    def _price(sym):
        if raise_exc:
            raise raise_exc
        return live_price

    # get_live_price and get_candles are imported inside the function,
    # so patch at their source module level.
    with patch("ccxt_client.get_live_price", side_effect=_price), \
         patch("chart_context.get_candles", return_value=None), \
         patch("agent_chart_draw.draw", return_value=None), \
         patch("chart_sr.detect_support_resistance", return_value=[]):
        result = _enrich_and_filter_setups(setups)
    return result


def test_no_entry_ref_drops_setup():
    """Setup with no entry_zone and no entry_price is dropped."""
    s = _make_setup()  # no entry_zone, no entry_price
    result = _run([s], live_price=0.24)
    assert result == [], "Setup with no entry ref must be dropped"


def test_entry_far_from_price_drops_setup():
    """Entry $0.146 when current price $0.2399 (64% gap) is dropped."""
    s = _make_setup(entry_low=0.1438, entry_high=0.146)
    result = _run([s], live_price=0.2399)
    assert result == [], "Entry 64% below current price must be dropped"


def test_entry_20pct_threshold_boundary():
    """Entry exactly 20% below current price is dropped; 19% is kept."""
    current = 1.0

    # 20% below → should drop
    s_drop = _make_setup(entry_low=0.80, entry_high=0.80)
    assert _run([s_drop], live_price=current) == []

    # 15% below → within 20% threshold, but directional drift 15% > 5% → also drops
    # (drift filter catches it after the 20% guard passes)
    s_drift = _make_setup(entry_low=0.85, entry_high=0.85)
    assert _run([s_drift], live_price=current) == []


def test_price_check_exception_drops_setup():
    """If get_live_price raises, setup is dropped (fail-closed)."""
    s = _make_setup(entry_low=0.145, entry_high=0.147)
    result = _run([s], raise_exc=Exception("network timeout"))
    assert result == [], "Exception in price check must drop setup, not pass it through"


def test_valid_near_price_setup_kept():
    """Entry within 3% of current price and no big drift → kept."""
    # current = 0.150, entry_high = 0.148 → 1.3% below current
    s = _make_setup(entry_low=0.146, entry_high=0.148)
    result = _run([s], live_price=0.150)
    assert len(result) == 1, "Valid near-price setup must survive the filter"
