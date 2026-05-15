"""
Integration tests for agent_orchestrator.run_call_analysis().
All external calls (AI, exchange, chart) are mocked.
Tests the 5-stage pipeline contract and verifies no circular imports.
"""
from unittest.mock import patch, MagicMock


def _mock_collected():
    """CollectorResult shape — matches agent_types.CollectorResult."""
    return {
        "symbol": "BTCUSDT",
        "candles": {},
        "funding_rate": {"rate": 0.01, "rate_pct": 0.01, "direction": "longs_paying",
                         "high": False, "ok": True},
        "open_interest": {"oi_coins": 100000, "oi_usd_m": 6000, "change_24h_pct": 0.5,
                          "trend": "rising", "ok": True},
        "long_short": {"long_pct": 52.0, "short_pct": 48.0, "bias": "long", "ok": True},
        "fear_greed": {"value": 55, "classification": "Neutral", "ok": True},
        "fred_macro": {"fed_rate": 5.25, "cpi": 3.1, "m2_b": 21000, "t10y": 4.5,
                       "ok": True},
        "nansen": {},
        "grok": {},
        "fetched_at": 1700000000.0,
    }


def _mock_interpreter():
    """InterpreterResult shape — matches agent_types.InterpreterResult."""
    return {
        "symbol": "BTCUSDT",
        "by_timeframe": {},
        "sr_levels": [{"price": 59000.0, "type": "support", "strength": 0.8,
                       "touches": 3, "recency_score": 0.7}],
        "confluence_score": {"score": 4.2, "max": 6.35, "bullish": 4.2,
                             "bearish": 0, "label": "Bullish", "details": []},
        "trend_direction": "bullish",
        "momentum_bias": "moderate",
        "prompt_text": "RSI 45, EMA bullish stack, ADX 22",
    }


def _mock_sentiment():
    """SentimentResult shape — matches agent_types.SentimentResult."""
    return {
        "macro_bias": "bullish",
        "sentiment_score": 6.5,
        "funding_bias": "longs_paying",
        "crowd_position": "balanced",
        "contra_signal": False,
        "key_factors": ["F&G 55 — Neutral"],
        "grok_summary": "",
        "prompt_text": "Macro: risk-on",
    }


def _mock_reviewer():
    """ReviewerResult shape — matches agent_types.ReviewerResult."""
    return {
        "signal_quality": 7.5,
        "warnings": [],
        "backtest_context": "5 trades, 60% WR, PF 1.4",
        "kpis": {"win_rate_pct": 60.0, "avg_win": 200.0, "avg_loss": -100.0,
                 "profit_factor": 1.4, "streak": 2},
        "symbol_history": {},
        "rubric": "Breakout rubric",
    }


def _mock_trade_prep():
    """TradePrepResult shape — matches agent_types.TradePrepResult."""
    return {
        "setup_score": 7,
        "direction": "Long",
        "entry_price": 60000.0,
        "sl_price": 57000.0,
        "tp1_price": 63000.0,
        "tp2_price": 66000.0,
        "rr_ratio": 2.0,
        "key_conditions": ["EMA stack bullish", "RSI neutral"],
        "pattern_warnings": [],
        "sizing_hint": "Standard 1% risk",
        "cot_reasoning": "Bullish setup with EMA stack and confluence",
        "gemini_score": 7,
        "consensus": {"consensus_score": 7.0, "claude_score": 7, "gemini_score": 7,
                      "delta": 0, "confidence": "high", "flag": "✓ Confirmed",
                      "prompt_line": "CONSENSUS SCORE: 7.0/10 [✓ Confirmed]"},
        "raw_json": {"score": 7},
        "chart_png_b64": "",
        "_model": "claude-sonnet-4-6",
        "_cached_tokens": 0,
    }


def _mock_risk():
    """RiskResult shape — matches agent_types.RiskResult."""
    return {
        "approved": True,
        "position_size_usdt": 600.0,
        "margin_usdt": 60.0,
        "risk_pct": 1.0,
        "atr_sl_valid": True,
        "correlation_warning": "",
        "max_risk_hit": False,
        "kelly_fraction": 0.15,
        "warnings": [],
        "sizing_breakdown": {"entry_price": 60000.0, "leverage": 10,
                             "margin_needed_usdt": 60.0},
    }


def _make_mock_conn():
    """Minimal mock for a sqlite3 connection object."""
    conn = MagicMock()
    conn.execute.return_value = MagicMock(fetchall=lambda: [], fetchone=lambda: None)
    return conn


def test_run_call_analysis_returns_complete_result():
    """Full 5-stage pipeline returns a result with required fields."""
    mock_conn = _make_mock_conn()

    with patch("agent_data_collector.run", return_value=_mock_collected()), \
         patch("agent_data_interpreter.run", return_value=_mock_interpreter()), \
         patch("agent_market_sentiment.run", return_value=_mock_sentiment()), \
         patch("agent_data_reviewer.run", return_value=_mock_reviewer()), \
         patch("agent_trade_prep.run", return_value=_mock_trade_prep()), \
         patch("agent_risk_mgmt.run", return_value=_mock_risk()):

        from agent_orchestrator import run_call_analysis
        result = run_call_analysis(
            call_text="$BTC Long — Entry 60000",
            symbol="BTCUSDT",
            direction="Long",
            account_equity=1000.0,
            setup_type="breakout",
            open_positions=[],
            conn=mock_conn,
        )

    assert result is not None
    # Core TradePrepResult fields must be present
    for field in ("direction", "entry_price", "sl_price", "tp1_price", "tp2_price",
                  "rr_ratio", "setup_score"):
        assert field in result, f"Missing required field: {field}"
    # Core RiskResult fields
    for field in ("risk_approved", "position_size_usdt", "margin_usdt", "kelly_fraction"):
        assert field in result, f"Missing required risk field: {field}"
    # Pipeline metadata
    assert result["error"] == ""
    assert result["degraded"] is False


def test_no_circular_import_between_orchestrator_and_trade_prep():
    """agent_trade_prep must not import agent_orchestrator (circular dep removed)."""
    import sys
    import ast
    import inspect

    # Force fresh import of agent_trade_prep
    for mod in list(sys.modules):
        if mod in ("agent_trade_prep", "agent_orchestrator", "consensus"):
            del sys.modules[mod]

    import agent_trade_prep

    src = inspect.getsource(agent_trade_prep)
    tree = ast.parse(src)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    assert "agent_orchestrator" not in imports, (
        "agent_trade_prep still imports agent_orchestrator — circular dep not fixed"
    )


def test_compute_consensus_importable_from_consensus_module():
    """compute_consensus must live in consensus.py, not agent_orchestrator."""
    from consensus import compute_consensus

    result = compute_consensus(7, 7)
    assert result["flag"] == "✓ Confirmed"
    assert result["consensus_score"] == 7.0
