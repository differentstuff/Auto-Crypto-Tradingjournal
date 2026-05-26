"""
tests/test_phase_b.py -- Validation tests for Phase B: Data Pipeline.

Tests that:
1. Indicator modules produce correct output shapes
2. Registry resolves indicator names to functions
3. Enzymes activate/deactivate correctly based on substrate state
4. Enzyme pipeline transforms substrate correctly (unit test, no API calls)
5. Substrate reset_cycle clears market + analysis for re-sensing
6. Enzyme registry contains all Phase B enzymes
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from core.substrate import Substrate
from core.enzyme import list_enzymes, create_enzyme, EnzymeClass

# Trigger @register_enzyme decorators for all Phase B enzymes.
# Must be at module level so the registry is populated for ALL test classes.
import enzymes  # noqa: F401


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_ohlcv():
    """Generate a synthetic OHLCV DataFrame for indicator tests."""
    np.random.seed(42)
    n = 200
    timestamps = pd.date_range("2024-01-01", periods=n, freq="4h")
    base_price = 50000.0
    returns = np.random.normal(0.001, 0.02, n)
    close = base_price * np.cumprod(1 + returns)
    high = close * (1 + np.abs(np.random.normal(0, 0.01, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.01, n)))
    open_ = close * (1 + np.random.normal(0, 0.005, n))
    volume = np.random.uniform(100, 10000, n)

    return pd.DataFrame({
        "timestamp": timestamps.astype(int) // 10**6,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def substrate():
    """Create a substrate with test config."""
    config = {
        "strategy": {
            "name": "momentum_rising",
            "timeframe": "4H",
            "confirmation_tf": "1H",
        },
        "symbols": {
            "always_watch": ["BTCUSDT", "ETHUSDT"],
        },
        "indicators": [
            {"name": "rsi", "weight": 1.0, "params": {"period": 14}},
            {"name": "macd", "weight": 1.0},
            {"name": "ema_stack", "weight": 1.0},
            {"name": "adx", "weight": 0.5},
            {"name": "atr", "weight": 0},
            {"name": "sr_levels", "weight": 0},
        ],
        "scoring": {
            "entry_threshold": 6.5,
            "confluence_min_signals": 3,
            "rr_minimum": 2.0,
        },
        "exit_rules": {
            "hard_stop": {"width_atr_multiplier": 1.5},
        },
        "modules": {
            "macro_context": True,
        },
    }
    return Substrate(config=config)


# ── 1. Indicator modules ─────────────────────────────────────────────────────

class TestMomentumIndicators:
    def test_rsi_returns_dict(self, sample_ohlcv):
        from indicators.momentum import compute_rsi
        result = compute_rsi(sample_ohlcv)
        assert isinstance(result, dict)
        assert "value" in result
        assert "level" in result
        assert 0 <= result["value"] <= 100
        assert result["level"] in ("overbought", "oversold", "neutral")

    def test_macd_returns_dict(self, sample_ohlcv):
        from indicators.momentum import compute_macd
        result = compute_macd(sample_ohlcv)
        assert isinstance(result, dict)
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result
        assert "bias" in result
        assert result["bias"] in ("bullish", "bearish")

    def test_stochrsi_returns_dict(self, sample_ohlcv):
        from indicators.momentum import compute_stochrsi
        result = compute_stochrsi(sample_ohlcv)
        assert result is not None
        assert "k" in result
        assert "d" in result
        assert 0 <= result["k"] <= 100

    def test_wavetrend_returns_dataframe(self, sample_ohlcv):
        from indicators.momentum import compute_wavetrend
        result = compute_wavetrend(sample_ohlcv)
        assert isinstance(result, pd.DataFrame)
        assert "wt1" in result.columns
        assert "wt2" in result.columns
        assert len(result) == len(sample_ohlcv)

    def test_cvd_returns_dict(self, sample_ohlcv):
        from indicators.momentum import compute_cvd
        result = compute_cvd(sample_ohlcv)
        assert result is not None
        assert "trend" in result
        assert result["trend"] in ("rising", "falling", "flat")

    def test_order_flow_returns_dict(self, sample_ohlcv):
        from indicators.momentum import compute_order_flow_delta
        result = compute_order_flow_delta(sample_ohlcv)
        assert result is not None
        assert "delta" in result
        assert "signal" in result

    def test_short_df_returns_defaults(self):
        from indicators.momentum import compute_rsi
        short_df = pd.DataFrame({
            "close": [100, 101, 102],
            "high": [103, 104, 105],
            "low": [99, 100, 101],
            "open": [100, 101, 102],
            "volume": [1000, 1100, 1200],
        })
        result = compute_rsi(short_df)
        assert result["value"] == 50.0  # Default for insufficient data


class TestTrendIndicators:
    def test_ema_alignment(self, sample_ohlcv):
        from indicators.trend import compute_ema_alignment
        result = compute_ema_alignment(sample_ohlcv)
        assert isinstance(result, dict)
        assert "alignment" in result
        assert "stack" in result
        assert result["alignment"] in (
            "bullish", "bearish", "neutral", "mixed-bullish", "mixed-bearish"
        )

    def test_adx(self, sample_ohlcv):
        from indicators.trend import compute_adx
        result = compute_adx(sample_ohlcv)
        assert isinstance(result, dict)
        assert "value" in result
        assert "trend_strength" in result
        assert result["trend_strength"] in ("strong", "trending", "weak")

    def test_recent_candles(self, sample_ohlcv):
        from indicators.trend import compute_recent_candles
        result = compute_recent_candles(sample_ohlcv)
        assert result is not None
        assert len(result) == 3


class TestVolatilityIndicators:
    def test_atr(self, sample_ohlcv):
        from indicators.volatility import compute_atr
        result = compute_atr(sample_ohlcv)
        assert result is not None
        assert "value" in result
        assert "pct" in result
        assert result["value"] > 0
        assert result["pct"] > 0

    def test_bollinger(self, sample_ohlcv):
        from indicators.volatility import compute_bollinger
        result = compute_bollinger(sample_ohlcv)
        assert result is not None
        assert "upper" in result
        assert "lower" in result
        assert result["upper"] > result["lower"]


class TestVolumeIndicators:
    def test_volume(self, sample_ohlcv):
        from indicators.volume import compute_volume
        result = compute_volume(sample_ohlcv)
        assert result is not None
        assert "ratio" in result
        assert "signal" in result


class TestStructureIndicators:
    def test_sr_levels(self, sample_ohlcv):
        from indicators.structure import detect_sr_levels
        result = detect_sr_levels(sample_ohlcv)
        assert isinstance(result, list)
        if result:
            assert "price" in result[0]
            assert "type" in result[0]
            assert result[0]["type"] in ("support", "resistance")

    def test_fibonacci(self, sample_ohlcv):
        from indicators.structure import detect_fibonacci
        result = detect_fibonacci(sample_ohlcv)
        assert result is not None
        assert "swing_high" in result
        assert "swing_low" in result
        assert "direction" in result
        assert "levels" in result
        assert len(result["levels"]) > 0

    def test_trendlines(self, sample_ohlcv):
        from indicators.structure import detect_trendlines
        result = detect_trendlines(sample_ohlcv)
        assert isinstance(result, list)

    def test_nearest_levels(self):
        from indicators.structure import nearest_levels
        levels = [
            {"price": 49000, "type": "support"},
            {"price": 51000, "type": "resistance"},
            {"price": 48000, "type": "support"},
            {"price": 52000, "type": "resistance"},
        ]
        result = nearest_levels(50000, levels)
        assert result["support"] == 49000
        assert result["resistance"] == 51000
        assert result["support_dist_pct"] is not None


# ── 2. Registry ──────────────────────────────────────────────────────────────

class TestRegistry:
    def test_list_available(self):
        from indicators.registry import list_available
        names = list_available()
        assert "rsi" in names
        assert "macd" in names
        assert "ema_stack" in names
        assert "adx" in names
        assert "atr" in names
        assert "bollinger" in names
        assert "volume" in names
        assert "sr_levels" in names
        assert "trendlines" in names
        assert "fibonacci" in names

    def test_compute_indicator(self, sample_ohlcv):
        from indicators.registry import compute_indicator
        result = compute_indicator("rsi", sample_ohlcv, period=14)
        assert isinstance(result, dict)
        assert "value" in result

    def test_unknown_indicator_raises(self, sample_ohlcv):
        from indicators.registry import compute_indicator
        with pytest.raises(ValueError, match="Unknown indicator"):
            compute_indicator("nonexistent", sample_ohlcv)

    def test_is_registered(self):
        from indicators.registry import is_registered
        assert is_registered("rsi")
        assert not is_registered("nonexistent")


# ── 3. Enzyme activation ─────────────────────────────────────────────────────

class TestEnzymeActivation:
    def test_collect_ohlcv_activates_when_empty(self, substrate):
        enz = create_enzyme("CollectOHLCV")
        assert enz is not None
        # Fresh substrate has empty indicators → should activate (cold start)
        assert enz.can_activate(substrate)

    def test_collect_ohlcv_does_not_activate_when_full(self, substrate):
        enz = create_enzyme("CollectOHLCV")
        # Simulate filled indicators WITH current candle close timestamps
        # P7: CollectOHLCV only activates when a new candle has closed
        substrate.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True}}}
        substrate.market["last_scan_at"] = "2024-01-01T00:00:00"
        # Set last_candle_close_ts to current candle floor (no new candle)
        # Must cover ALL symbols in symbols_watched AND both timeframes
        from enzymes.collect_ohlcv import candle_floor
        now = datetime.now(timezone.utc)
        substrate.market["last_candle_close_ts"] = {
            "BTCUSDT_4H": candle_floor(now, "4H").isoformat(),
            "BTCUSDT_1H": candle_floor(now, "1H").isoformat(),
            "ETHUSDT_4H": candle_floor(now, "4H").isoformat(),
            "ETHUSDT_1H": candle_floor(now, "1H").isoformat(),
        }
        assert not enz.can_activate(substrate)

    def test_score_confluence_requires_indicators(self, substrate):
        enz = create_enzyme("ScoreConfluence")
        # Empty indicators → cannot activate
        assert not enz.can_activate(substrate)
        # With indicators → can activate
        substrate.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True}}}
        assert enz.can_activate(substrate)

    def test_score_confluence_prohibits_existing_candidates(self, substrate):
        enz = create_enzyme("ScoreConfluence")
        substrate.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True}}}
        substrate.analysis["candidates"] = [{"symbol": "BTCUSDT"}]
        assert not enz.can_activate(substrate)

    def test_detect_noise_requires_indicators(self, substrate):
        enz = create_enzyme("DetectNoise")
        # DetectNoise requires indicators (not candidates) and noise_evaluated=False
        assert not enz.can_activate(substrate)
        substrate.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True}}}
        assert enz.can_activate(substrate)

    def test_validate_entry_zone_requires_candidates(self, substrate):
        enz = create_enzyme("ValidateEntryZone")
        assert not enz.can_activate(substrate)
        substrate.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 5}]
        assert enz.can_activate(substrate)

    def test_validate_entry_zone_prohibits_already_evaluated(self, substrate):
        enz = create_enzyme("ValidateEntryZone")
        substrate.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 5}]
        substrate.analysis["entry_zones_evaluated"] = True
        assert not enz.can_activate(substrate)

    def test_collect_macro_context_respects_module_flag(self, substrate):
        enz = create_enzyme("CollectMacroContext")
        # macro_context enabled in fixture config
        assert enz.can_activate(substrate)

    def test_collect_macro_context_disabled(self, substrate):
        enz = create_enzyme("CollectMacroContext")
        substrate._config["modules"]["macro_context"] = False
        assert not enz.can_activate(substrate)


# ── 4. Enzyme pipeline (unit test) ───────────────────────────────────────────

class TestEnzymePipeline:
    def test_score_confluence_transform(self, substrate, sample_ohlcv):
        """Test ScoreConfluence with pre-populated indicator data."""
        from indicators.registry import compute_indicator

        # Compute indicators for the sample data
        tf_inds = {"ok": True, "candles_used": 200}
        for name in ["rsi", "macd", "ema_stack", "adx", "atr", "sr_levels"]:
            try:
                result = compute_indicator(name, sample_ohlcv)
                if result is not None:
                    tf_inds[name] = result
            except Exception:
                pass

        substrate.market["indicators"] = {
            "BTCUSDT": {"4H": tf_inds}
        }

        enz = create_enzyme("ScoreConfluence")
        result = enz.transform(substrate)

        assert "candidates" in result.analysis
        assert isinstance(result.analysis["candidates"], list)
        assert "signal_states" in result.analysis

    def test_detect_noise_transform(self, substrate):
        """Test DetectNoise with pre-populated candidates."""
        substrate.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 5.0, "pct": 0.4, "label": "Bullish"}
        ]
        substrate.market["indicators"] = {
            "BTCUSDT": {
                "4H": {
                    "ok": True,
                    "adx": {"value": 25, "trend_strength": "trending", "direction": "bullish"},
                    "rsi": {"value": 60, "level": "neutral"},
                    "macd": {"bias": "bullish", "histogram_growing": True},
                    "volume": {"ratio": 1.2, "signal": "average volume"},
                    "atr": {"pct": 2.5},
                }
            }
        }

        enz = create_enzyme("DetectNoise")
        result = enz.transform(substrate)

        assert "noise_flag" in result.analysis
        assert isinstance(result.analysis["noise_flag"], bool)
        assert "noise_reason" in result.analysis

    def test_validate_entry_zone_transform(self, substrate):
        """Test ValidateEntryZone with pre-populated candidates and indicators."""
        substrate.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 5.0, "pct": 0.4, "label": "Bullish"}
        ]
        substrate.market["indicators"] = {
            "BTCUSDT": {
                "4H": {
                    "ok": True,
                    "ema_stack": {
                        "current_price": 50000.0,
                        "alignment": "bullish",
                        "stack": "bullish",
                    },
                    "atr": {"value": 1000.0, "pct": 2.0},
                    "sr_levels": [
                        {"price": 48000, "type": "support"},
                        {"price": 52000, "type": "resistance"},
                    ],
                }
            }
        }

        enz = create_enzyme("ValidateEntryZone")
        result = enz.transform(substrate)

        assert "entry_zones" in result.analysis
        if "BTCUSDT" in result.analysis["entry_zones"]:
            zone = result.analysis["entry_zones"]["BTCUSDT"]
            assert zone["direction"] == "Long"
            assert zone["entry_price"] == 50000.0
            assert zone["sl_price"] > 0
            assert zone["tp1"] > zone["entry_price"]  # TP above entry for long


# ── 5. Substrate reset_cycle ─────────────────────────────────────────────────

class TestSubstrateReset:
    def test_reset_clears_market(self, substrate):
        substrate.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True}}}
        substrate.market["last_scan_at"] = "2024-01-01"
        substrate.market["macro"] = {"regime": "risk-on"}
        substrate.market["pre_trade_context"] = {"BTCUSDT": {}}

        substrate.reset_cycle()

        # P7: indicators persist across reset_cycle (managed by CollectOHLCV)
        assert substrate.market["indicators"] != {}
        assert "BTCUSDT" in substrate.market["indicators"]
        # Transient fields are still cleared
        assert substrate.market["last_scan_at"] == ""
        assert substrate.market["macro"] == {}
        assert substrate.market["pre_trade_context"] == {}

    def test_reset_clears_analysis(self, substrate):
        substrate.analysis["candidates"] = [{"symbol": "BTCUSDT"}]
        substrate.analysis["entry_zones"] = {"BTCUSDT": {}}
        substrate.analysis["noise_flag"] = True

        substrate.reset_cycle()

        assert substrate.analysis["candidates"] == []
        assert substrate.analysis["entry_zones"] == {}
        assert substrate.analysis["noise_flag"] is False

    def test_reset_preserves_strategy(self, substrate):
        substrate.strategy["name"] = "test_strategy"
        substrate.reset_cycle()
        assert substrate.strategy["name"] == "test_strategy"

    def test_reset_preserves_learning(self, substrate):
        substrate.learning["idle_cycles"] = 42
        substrate.learning["total_trades_recorded"] = 10
        substrate.reset_cycle()
        assert substrate.learning["idle_cycles"] == 42
        assert substrate.learning["total_trades_recorded"] == 10


# ── 6. Enzyme registry ───────────────────────────────────────────────────────

class TestEnzymeRegistry:
    def test_all_phase_b_enzymes_registered(self):
        """All Phase B enzymes should be in the registry."""
        import enzymes  # noqa: F401 — trigger registration
        registered = list_enzymes()
        expected = [
            "Wait",
            "CollectOHLCV",
            "ScoreConfluence",
            "DetectNoise",
            "ValidateEntryZone",
            "CollectPreTradeContext",
            "CollectMacroContext",
        ]
        for name in expected:
            assert name in registered, f"Enzyme {name} not found in registry"

    def test_create_enzyme_returns_correct_class(self):
        import enzymes  # noqa: F401
        enz = create_enzyme("CollectOHLCV")
        assert enz is not None
        assert enz.name == "CollectOHLCV"
        assert enz.enzyme_class == EnzymeClass.SENSOR

    def test_create_enzyme_returns_none_for_unknown(self):
        enz = create_enzyme("NonExistentEnzyme")
        assert enz is None


# ── 7. Exchange module ───────────────────────────────────────────────────────

class TestExchange:
    def test_symbol_conversion(self):
        from core.exchange import Exchange
        assert Exchange.to_ccxt_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert Exchange.to_ccxt_symbol("ETHUSDT") == "ETH/USDT:USDT"
        assert Exchange.to_journal_symbol("BTC/USDT:USDT") == "BTCUSDT"
        assert Exchange.to_journal_symbol("ETH/USDT:USDT") == "ETHUSDT"

    def test_roundtrip_symbol(self):
        from core.exchange import Exchange
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            assert Exchange.to_journal_symbol(Exchange.to_ccxt_symbol(sym)) == sym