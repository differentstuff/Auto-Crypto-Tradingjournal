import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture(autouse=True)
def _real_flask():
    """Evict the Flask stub so routes.live (which imports Blueprint) can load."""
    for k in list(sys.modules):
        if k == "flask" or k.startswith("flask.") or k == "routes.live" or k == "routes":
            del sys.modules[k]
    yield
    for k in list(sys.modules):
        if k == "flask" or k.startswith("flask.") or k == "routes.live" or k == "routes":
            del sys.modules[k]


def test_classify_sector():
    from routes.live import _classify_sector
    assert _classify_sector("SOLUSDT") == "L1"
    assert _classify_sector("UNIUSDT") == "DeFi"
    assert _classify_sector("PEPEUSDT") == "Meme"
    assert _classify_sector("ARBUSDT") == "L2"
    assert _classify_sector("UNKNOWN123USDT") == "Other"


def test_empty_positions():
    from routes.live import _compute_portfolio_risk
    r = _compute_portfolio_risk([], equity=1000.0)
    assert r["total_long_usd"] == 0
    assert r["margin_used_pct"] == 0


def test_long_short_split():
    from routes.live import _compute_portfolio_risk
    positions = [
        {"symbol": "BTCUSDT", "direction": "Long",  "size_usdt": 500, "margin_usdt": 50},
        {"symbol": "SOLUSDT", "direction": "Short", "size_usdt": 200, "margin_usdt": 20},
    ]
    r = _compute_portfolio_risk(positions, equity=1000.0)
    assert r["total_long_usd"] == 500
    assert r["total_short_usd"] == 200
    assert r["margin_used_pct"] == pytest.approx(7.0, abs=0.1)


def test_sector_grouping():
    from routes.live import _compute_portfolio_risk
    positions = [
        {"symbol": "BTCUSDT", "direction": "Long", "size_usdt": 500, "margin_usdt": 50},
        {"symbol": "UNIUSDT", "direction": "Long", "size_usdt": 200, "margin_usdt": 20},
    ]
    r = _compute_portfolio_risk(positions, equity=2000.0)
    sectors = {s["sector"]: s["usd"] for s in r["by_sector"]}
    assert sectors.get("BTC", 0) == 500
    assert sectors.get("DeFi", 0) == 200
