"""
tests_new/test_exchange_access.py -- Integration test for exchange data access.

Tests that the system can:
  1. Connect to Binance public API and fetch OHLCV data
  2. Compute indicators on real data via the registry
  3. Run a full paper-mode daemon cycle (with mocked exchange for reliability)

These tests require network access (Binance public API).
Marked with pytest.mark.integration so they can be skipped in offline mode:
    pytest tests_new/ -m "not integration"     # skip network tests
    pytest tests_new/ -m "integration"          # run only network tests
    pytest tests_new/                            # run all (including network)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config_loader():
    """Create a ConfigLoader pointing at the real project config/."""
    from core.config_loader import ConfigLoader
    return ConfigLoader(strategy_name="momentum_rising")


# ---------------------------------------------------------------------------
# TestExchange: public data access via core.exchange
# ---------------------------------------------------------------------------

class TestExchangePublicData:
    """Test that Exchange can fetch public market data from Binance."""

    def test_fetch_ohlcv_btcusdt_4h(self):
        """Fetch BTCUSDT 4h candles from Binance public API."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)

        df = exchange.fetch_ohlcv("BTCUSDT", "4h", limit=50)

        assert df is not None, "fetch_ohlcv returned None — check network"
        assert len(df) >= 30, f"Expected >= 30 candles, got {len(df)}"
        assert "close" in df.columns
        assert "volume" in df.columns
        # BTC price should be > 1000
        assert df["close"].iloc[-1] > 1000, f"BTC price seems wrong: {df['close'].iloc[-1]}"

    def test_fetch_ohlcv_ethusdt_1h(self):
        """Fetch ETHUSDT 1H candles from Binance public API."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)

        df = exchange.fetch_ohlcv("ETHUSDT", "1h", limit=50)

        assert df is not None, "fetch_ohlcv returned None for ETH"
        assert len(df) >= 30

    def test_fetch_ohlcv_invalid_symbol(self):
        """Fetching an invalid symbol should return None, not raise."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)

        df = exchange.fetch_ohlcv("INVALIDPAIR", "4h", limit=50)

        # Should return None or very short DataFrame, not crash
        assert df is None or len(df) < 30

    def test_paper_mode_no_exchange_calls(self):
        """In paper mode, balance/position calls return empty data without errors."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)

        assert exchange.paper_mode is True

        balance = exchange.fetch_balance()
        assert balance == {}

        positions = exchange.fetch_positions()
        assert positions == []

    def test_test_connection(self):
        """test_connection() should report data_ok=True for Binance public API."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)

        result = exchange.test_connection()

        assert result["data_ok"] is True, f"Data exchange failed: {result}"
        assert result["paper_mode"] is True


# ---------------------------------------------------------------------------
# TestIndicatorOnRealData: compute indicators on real OHLCV
# ---------------------------------------------------------------------------

class TestIndicatorOnRealData:
    """Test that indicators can be computed on real exchange data."""

    @pytest.fixture(autouse=True)
    def _fetch_data(self):
        """Fetch BTCUSDT 4h data once for all tests in this class."""
        from core.exchange import Exchange
        config = _make_config_loader()
        exchange = Exchange(config)
        self.df = exchange.fetch_ohlcv("BTCUSDT", "4h", limit=200)
        if self.df is None or len(self.df) < 50:
            pytest.skip("Could not fetch sufficient BTCUSDT data")

    def test_rsi_on_real_data(self):
        """RSI computation on real BTC data produces a sensible value."""
        from indicators.registry import compute_indicator

        result = compute_indicator("rsi", self.df, period=14)

        assert result is not None
        assert isinstance(result, dict)
        assert "value" in result
        # RSI should be between 0 and 100
        assert 0 <= result["value"] <= 100, f"RSI out of range: {result['value']}"

    def test_macd_on_real_data(self):
        """MACD computation on real BTC data produces directional bias."""
        from indicators.registry import compute_indicator

        result = compute_indicator("macd", self.df, fast=12, slow=26, signal=9)

        assert result is not None
        assert isinstance(result, dict)
        assert "bias" in result
        assert result["bias"] in ("bullish", "bearish", "neutral", "")

    def test_ema_stack_on_real_data(self):
        """EMA stack computation on real BTC data produces alignment info."""
        from indicators.registry import compute_indicator

        result = compute_indicator("ema_stack", self.df, periods=[21, 55, 200])

        assert result is not None
        assert isinstance(result, dict)
        assert "alignment" in result or "current_price" in result

    def test_adx_on_real_data(self):
        """ADX computation on real BTC data produces a trend strength value."""
        from indicators.registry import compute_indicator

        result = compute_indicator("adx", self.df, period=14)

        assert result is not None
        assert isinstance(result, dict)
        assert "value" in result
        # ADX should be non-negative
        assert result["value"] >= 0

    def test_atr_on_real_data(self):
        """ATR computation on real BTC data produces a volatility value."""
        from indicators.registry import compute_indicator

        result = compute_indicator("atr", self.df, period=14)

        assert result is not None
        assert isinstance(result, dict)
        assert "value" in result
        assert result["value"] > 0, "ATR should be positive for BTC"

    def test_sr_levels_on_real_data(self):
        """S/R level detection on real BTC data returns a list."""
        from indicators.registry import compute_indicator

        result = compute_indicator("sr_levels", self.df, tolerance=0.004, min_touches=3)

        assert result is not None
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestDaemonPaperCycle: full daemon cycle in paper mode
# ---------------------------------------------------------------------------

class TestDaemonPaperCycle:
    """Test that the daemon can complete a full paper-mode cycle."""

    def test_daemon_initialize_paper_mode(self):
        """Daemon initializes successfully in paper mode."""
        from core.daemon import Daemon

        daemon = Daemon(strategy_name="momentum_rising", paper_mode=True)
        daemon.initialize()

        assert daemon.substrate is not None
        assert daemon.scheduler is not None
        assert daemon.config is not None
        assert daemon.paper_mode is True

    def test_daemon_single_cycle_paper_mode(self, temp_db):
        """Daemon completes a single cycle in paper mode without errors."""
        from core.daemon import Daemon

        daemon = Daemon(strategy_name="momentum_rising", paper_mode=True)
        daemon.initialize()

        result = daemon.run_cycle()

        assert result is not None
        assert "cycle" in result
        assert "action" in result
        assert "enzymes_fired" in result
        assert "duration_ms" in result

    def test_enzyme_registry_complete(self):
        """All expected enzymes from Phase B and C are registered."""
        from core.enzyme import list_enzymes

        # Import enzymes package to trigger registration
        import enzymes  # noqa: F401

        registered = list_enzymes()
        expected = [
            # Phase B
            "CollectOHLCV",
            "ScoreConfluence",
            "DetectNoise",
            "ValidateEntryZone",
            "CollectPreTradeContext",
            "CollectMacroContext",
            # Phase C
            "ApproveTrade",
            "ApproveExit",
            "RequestExit",
            "ExecuteTrade",
            "ExecuteExit",
            "SyncPositions",
            "SendTelegramLog",
            "Wait",
        ]

        missing = [e for e in expected if e not in registered]
        assert not missing, f"Missing enzymes: {missing}. Available: {registered}"

    def test_exchange_module_imports(self):
        """core.exchange module imports without errors."""
        from core.exchange import Exchange
        assert Exchange is not None