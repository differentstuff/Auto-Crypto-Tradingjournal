"""Tests for Finnhub economic calendar and CoinGecko market data."""
import pytest
from unittest.mock import patch


def test_finnhub_get_upcoming_events_structure():
    """get_upcoming_events returns dict with required keys."""
    with patch("finnhub_client._get", return_value={"economicCalendar": []}):
        from finnhub_client import get_upcoming_events
        result = get_upcoming_events()
    assert "events" in result
    assert "macro_risk" in result
    assert isinstance(result["macro_risk"], bool)


def test_finnhub_macro_risk_detected():
    """macro_risk is True when FOMC event is in next 24h."""
    fake_events = {"economicCalendar": [
        {"time": "2026-05-16T14:00:00Z", "event": "FOMC Meeting", "country": "US", "impact": "high"}
    ]}
    with patch("finnhub_client._get", return_value=fake_events):
        from finnhub_client import get_upcoming_events
        result = get_upcoming_events(hours_ahead=48)
    assert result["macro_risk"] is True
    assert result["next_event"] is not None


def test_coingecko_global_market_structure():
    """get_global_market returns dict with btc_dominance_pct."""
    fake_global = {"data": {
        "market_cap_percentage": {"btc": 52.3},
        "total_market_cap": {"usd": 2_100_000_000_000},
        "total_volume": {"usd": 80_000_000_000},
        "active_cryptocurrencies": 12000,
    }}
    with patch("coingecko_client._get", return_value=fake_global):
        from coingecko_client import get_global_market
        result = get_global_market()
    assert result["btc_dominance_pct"] == 52.3
    assert result["market_regime"] == "mixed"


def test_coingecko_unknown_symbol():
    """get_coin_market_data returns {} for unmapped symbols."""
    from coingecko_client import get_coin_market_data
    assert get_coin_market_data("FAKEXXX") == {}


def test_coingecko_cap_tier_logic():
    """Market cap rank 8 → large_cap."""
    fake_markets = [{"market_cap_rank": 8, "market_cap": 500e9,
                     "total_volume": 20e9, "price_change_percentage_24h": 1.5}]
    with patch("coingecko_client._get", return_value=fake_markets):
        from coingecko_client import get_coin_market_data
        result = get_coin_market_data("BTCUSDT")
    assert result["cap_tier"] == "large_cap"
    assert result["market_cap_rank"] == 8


def test_fetch_economic_events_adapter_degrades():
    """fetch_economic_events returns safe default on error."""
    with patch("finnhub_client.get_upcoming_events", side_effect=Exception("network")):
        from data_sources import fetch_economic_events
        result = fetch_economic_events()
    assert result["macro_risk"] is False


def test_collector_result_has_new_fields():
    """CollectorResult must include economic_events, global_market, coin_market_data."""
    from agent_types import CollectorResult
    for field in ("economic_events", "global_market", "coin_market_data"):
        assert field in CollectorResult.__annotations__, f"Missing: {field}"


def test_get_trending_coins_returns_list():
    """get_trending_coins returns a list of symbol strings."""
    fake = {"coins": [
        {"item": {"symbol": "PEPE", "market_cap_rank": 50}},
        {"item": {"symbol": "WIF",  "market_cap_rank": 60}},
    ]}
    with patch("coingecko_client._get", return_value=fake):
        from coingecko_client import get_trending_coins
        result = get_trending_coins()
    assert "PEPE" in result
    assert "WIF"  in result


def test_get_trending_coins_degrades():
    """get_trending_coins returns [] on error."""
    with patch("coingecko_client._get", return_value=None):
        from coingecko_client import get_trending_coins
        assert get_trending_coins() == []


def test_collector_result_has_trending_coins():
    """CollectorResult must include trending_coins field."""
    from agent_types import CollectorResult
    assert "trending_coins" in CollectorResult.__annotations__


def test_fetch_trending_adapter():
    """fetch_trending_coins adapter returns list."""
    with patch("coingecko_client.get_trending_coins", return_value=["BTC", "ETH"]):
        from data_sources import fetch_trending_coins
        result = fetch_trending_coins()
    assert "BTC" in result
