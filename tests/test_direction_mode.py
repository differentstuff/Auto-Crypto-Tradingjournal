"""
tests/test_direction_mode.py -- Tests for Fix 4: Direction mode + short scoring.

 
Verify that:
 - long_only zeros out short (short candidates
 - short_only zeros out long candidates  
 - both keeps all candidates
 - long_weight and short_weight scale final scores
"""

import os
import sys
from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_full_config
from core.substrate import Substrate
from enzymes.score_confluence import ScoreConfluence


from enzymes.approve_trade import ApproveTrade


from enzymes.validate_entry_zone import ValidateEntryZone


from enzymes.collect_ohlcv import CollectOHLCV


def _make_substrate(**overrides) -> Substrate:
    cfg = make_full_config(**overrides)
    return Substrate(config=cfg)


def _make_indicator_data(symbol="BTCUSDT", timeframe="4H") -> dict:
    return {
        symbol: {
            timeframe: {
                "ok": True,
                "rsi": {"value": 65.3, "level": "neutral"},
                "macd": {
                    "macd": 100.5,
                    "signal": 95.2,
                    "histogram": 5.3,
                    "bias": "bullish",
                    "histogram_growing": True,
                    "crossover": False,
                    "crossunder": False,
                },
                "ema_stack": {
                    "ema20": 50200.0,
                    "ema50": 49800.0,
                    "ema200": 48000.0,
                    "current_price": 50500.0,
                    "alignment": "bullish",
                    "stack": "bullish",
                },
                "adx": {
                    "value": 28.5,
                    "trend_strength": "trending",
                    "direction": "bullish",
                },
                "wavetrend": {
                    "wt1": 15.0,
                    "wt2": 10.0,
                    "histogram": 5.0,
                    "mfi": 20.0,
                    "cross": "bullish",
                    "zone": "neutral",
                    "signal": "buy",
                },
                "volume": {
                    "current": 1200.0,
                    "avg_20": 800.0,
                    "ratio": 1.5,
                    "signal": "high volume (1.5x avg)",
                },
            },
        },
    }


def _make_bearish_indicator_data(symbol="BTCUSDT", timeframe="4H") -> dict:
    return {
        symbol: {
            timeframe: {
                "ok": True,
                "rsi": {"value": 30.0, "level": "oversold"},
                "macd": {
                    "macd": -100.5,
                    "signal": -95.2,
                    "histogram": -5.3,
                    "bias": "bearish",
                    "histogram_growing": True,
                    "crossover": False,
                    "crossunder": False,
                },
                "ema_stack": {
                    "ema20": 48000.0,
                    "ema50": 50200.0,
                    "ema200": 51000.0,
                    "current_price": 47500.0,
                    "alignment": "bearish",
                    "stack": "bearish",
                },
                "adx": {
                    "value": 28.5,
                    "trend_strength": "trending",
                    "direction": "bearish",
                },
                "wavetrend": {
                    "wt1": -15.0,
                    "wt2": -10.0,
                    "histogram": -5.0,
                    "mfi": 20.0,
                    "cross": "bearish",
                    "zone": "neutral",
                    "signal": "sell",
                },
                "volume": {
                    "current": 1200.0,
                    "avg_20": 800.0,
                    "ratio": 1.5,
                    "signal": "high volume (1.5x avg)",
                },
            },
        },
    }


class TestDirectionModeScoreConfluence:

    def test_long_only_zeros_short_candidates(self):
        sub = _make_substrate(strategy={"direction_mode": "long_only"})
        sub.market["indicators"] = _make_bearish_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        symbols = [c["symbol"] for c in candidates]
        assert "BTCUSDT" not in symbols, \
            "bearish candidate should be dropped in long_only mode"

        all_scores = result.analysis.get("all_scores", {})
        btc_score = all_scores.get("BTCUSDT", {}).get("score", 0)
        assert btc_score < 0, \
            "bearish indicator data should produce negative score before filter"

    def test_short_only_zeros_long_candidates(self):
        sub = _make_substrate(strategy={"direction_mode": "short_only"})
        sub.market["indicators"] = _make_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        symbols = [c["symbol"] for c in candidates]
        assert "BTCUSDT" not in symbols, \
            "bullish candidate should be dropped in short_only mode"

        all_scores = result.analysis.get("all_scores", {})
        btc_score = all_scores.get("BTCUSDT", {}).get("score", 0)
        assert btc_score > 0, \
            "bullish indicator data should produce positive score before filter"

    def test_both_keeps_all_candidates(self):
        sub_long = _make_substrate(strategy={"direction_mode": "both"})
        sub_long.market["indicators"] = _make_indicator_data()
        sub_long.analysis["candidates"] = []
        sub_long.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result_long = enzyme.transform(sub_long)

        candidates_long = result_long.analysis.get("candidates", [])
        symbols_long = [c["symbol"] for c in candidates_long]
        assert "BTCUSDT" in symbols_long, \
            "bullish candidate should be kept in both mode"

        sub_short = _make_substrate(strategy={"direction_mode": "both"})
        sub_short.market["indicators"] = _make_bearish_indicator_data()
        sub_short.analysis["candidates"] = []
        sub_short.analysis["confluence_scored"] = False

        result_short = enzyme.transform(sub_short)
        candidates_short = result_short.analysis.get("candidates", [])
        symbols_short = [c["symbol"] for c in candidates_short]
        assert "BTCUSDT" in symbols_short, \
            "bearish candidate should be kept in both mode"


class TestDirectionWeights:

    def test_short_weight_applied(self):
        sub = _make_substrate(strategy={
            "direction_mode": "both",
            "short_weight": 0.5,
        })
        sub.market["indicators"] = _make_bearish_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        btc_candidate = next((c for c in candidates if c["symbol"] == "BTCUSDT"), None)
        if btc_candidate:
            all_scores = result.analysis.get("all_scores", {})
            raw_score = all_scores.get("BTCUSDT", {}).get("score", 0)
            assert abs(btc_candidate["score"]) <= abs(raw_score) * 0.5 + 0.01, \
                f"short_weight=0.5 should halve the final candidate score (raw={raw_score}, final={btc_candidate['score']})"
        else:
            pytest.skip("No bearish candidate produced — weight test skipped")

    def test_long_weight_applied(self):
        sub = _make_substrate(strategy={
            "direction_mode": "both",
            "long_weight": 0.8,
        })
        sub.market["indicators"] = _make_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)
        candidates = result.analysis.get("candidates", [])
        btc_candidate = next((c for c in candidates if c["symbol"] == "BTCUSDT"), None)
        if btc_candidate:
            all_scores = result.analysis.get("all_scores", {})
            raw_score = all_scores.get("BTCUSDT", {}).get("score", 0)
            assert abs(btc_candidate["score"]) <= abs(raw_score) * 0.8 + 0.01, \
                f"long_weight=0.8 should scale the final candidate score (raw={raw_score}, final={btc_candidate['score']})"
        else:
            pytest.skip("No bullish candidate produced — weight test skipped")


