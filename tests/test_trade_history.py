def test_get_recent_trades_returns_list(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions)
    assert isinstance(trades, list)
    assert all("realized_pnl" in t for t in trades)

def test_get_recent_trades_filters_by_symbol(sample_positions):
    from trade_history import get_recent_trades
    btc = get_recent_trades("BTCUSDT", sample_positions)
    eth = get_recent_trades("ETHUSDT", sample_positions)
    assert all(t["symbol"] == "BTCUSDT" for t in btc)
    assert all(t["symbol"] == "ETHUSDT" for t in eth)

def test_get_recent_trades_limit_respected(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions, limit=2)
    assert len(trades) <= 2

def test_get_recent_trades_before_date(sample_positions):
    from trade_history import get_recent_trades
    trades = get_recent_trades("BTCUSDT", sample_positions, before_iso="2026-01-04")
    # Only trades with open_time < 2026-01-04: first one (2026-01-01)
    assert all(t["open_time"] < "2026-01-04" for t in trades)

def test_get_trade_stats_correct_win_rate(sample_positions):
    from trade_history import get_recent_trades, get_trade_stats
    trades = get_recent_trades("BTCUSDT", sample_positions)
    stats = get_trade_stats(trades)
    # BTCUSDT: 100 (win), -50 (loss), 80 (win), -20 (loss) = 2W 2L
    assert stats["trades"] == 4
    assert stats["wins"] == 2
    assert stats["win_rate_pct"] == 50.0

def test_get_trade_stats_empty():
    from trade_history import get_trade_stats
    stats = get_trade_stats([])
    assert stats["trades"] == 0
    assert stats["win_rate_pct"] == 0

def test_get_symbol_summary_combines_both(sample_positions):
    from trade_history import get_symbol_summary
    summary = get_symbol_summary("BTCUSDT", sample_positions)
    assert "trades" in summary
    assert "win_rate_pct" in summary
    assert "recent_trades" in summary
    assert isinstance(summary["recent_trades"], list)
