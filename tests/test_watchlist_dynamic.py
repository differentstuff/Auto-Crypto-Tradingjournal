import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_cache_is_used_on_second_call(monkeypatch):
    """Second call within TTL must not refetch Binance."""
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    call_count = {"n": 0}
    def fake_fetch(*a, **kw):
        call_count["n"] += 1
        return ["BTCUSDT", "ETHUSDT"]

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols", fake_fetch)
    monkeypatch.setattr("ccxt_client.get_binance_oi_map", lambda syms: {})
    wl._get_dynamic_watchlist()
    wl._get_dynamic_watchlist()
    assert call_count["n"] == 1


def test_cache_expires_after_ttl(monkeypatch):
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = ["BTCUSDT"]
    wl._dynamic_cache["ts"] = time.time() - wl._DYNAMIC_TTL - 1  # expired

    fetched = {"done": False}
    def fake_fetch(*a, **kw):
        fetched["done"] = True
        return ["BTCUSDT", "ETHUSDT"]

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols", fake_fetch)
    monkeypatch.setattr("ccxt_client.get_binance_oi_map", lambda syms: {})
    result = wl._get_dynamic_watchlist()
    assert fetched["done"]
    assert "BTCUSDT" in result


def test_oi_filter_excludes_low_oi(monkeypatch):
    """Symbols below OI threshold must not appear when OI data is available."""
    import scanner_watchlist as wl
    # Reset cache
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    # Only NEWCOIN is in the volume list (not in static Bitget list)
    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols",
                        lambda **kw: ["NEWCOINUSDT"])
    monkeypatch.setattr("ccxt_client.get_binance_oi_map",
                        lambda syms: {"NEWCOINUSDT": 100_000})  # below threshold

    result = wl._get_dynamic_watchlist(min_oi_usd=1_000_000)
    assert "NEWCOINUSDT" not in result


def test_high_oi_symbol_included(monkeypatch):
    """Symbols above OI threshold must appear in dynamic list."""
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols",
                        lambda **kw: ["NEWCOINUSDT"])
    monkeypatch.setattr("ccxt_client.get_binance_oi_map",
                        lambda syms: {"NEWCOINUSDT": 50_000_000})  # above threshold

    result = wl._get_dynamic_watchlist(min_oi_usd=1_000_000)
    assert "NEWCOINUSDT" in result


def test_fallback_on_api_error(monkeypatch):
    """If Binance fetch fails, falls back to _get_extended_watchlist."""
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("timeout")))

    result = wl._get_dynamic_watchlist()
    # Should return something (fallback), not raise
    assert isinstance(result, list)
    assert len(result) > 0
