"""
tests/test_confirmation_tf.py -- Tests for Fix 3: confirmation_tf disabled via config.

Verify that when confirmation_tf is null, the kill-switch does NOT fire
(and candidates keep their scores). When confirmation_tf is set (e.g. "1d"),
the kill-switch still works as before — code path is preserved.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_full_config
from core.substrate import Substrate
from enzymes.score_confluence import ScoreConfluence


def _make_substrate(**overrides) -> Substrate:
    cfg = make_full_config(**overrides)
    return Substrate(config=cfg)


def _make_bullish_indicator_data(symbol="BTCUSDT", timeframe="4H") -> dict:
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
                    "ema20": 49800.0,
                    "ema50": 50200.0,
                    "ema200": 52000.0,
                    "current_price": 49500.0,
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
                    "mfi": -20.0,
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


class TestConfirmationTFDisabledViaNull:

    def test_null_confirmation_tf_does_not_zero_score(self):
        sub = _make_substrate(strategy={"confirmation_tf": None})
        sub.market["indicators"] = _make_bullish_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        btc_candidate = next((c for c in candidates if c["symbol"] == "BTCUSDT"), None)
        if btc_candidate:
            assert btc_candidate["score"] > 0, \
                f"With confirmation_tf=null, bullish candidate should have positive score, got {btc_candidate['score']}"
            assert btc_candidate.get("confirmation_tf_misaligned") is False, \
                "With confirmation_tf=null, misaligned should be False"

    def test_null_confirmation_tf_allows_bearish_candidates(self):
        sub = _make_substrate(strategy={"confirmation_tf": None})
        sub.market["indicators"] = _make_bearish_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        btc_candidate = next((c for c in candidates if c["symbol"] == "BTCUSDT"), None)
        if btc_candidate:
            assert btc_candidate["score"] < 0, \
                f"With confirmation_tf=null, bearish candidate should have negative score, got {btc_candidate['score']}"
            assert btc_candidate.get("confirmation_tf_misaligned") is False, \
                "With confirmation_tf=null, misaligned should be False"

    def test_empty_string_confirmation_tf_same_as_null(self):
        sub = _make_substrate(strategy={"confirmation_tf": ""})
        sub.market["indicators"] = _make_bullish_indicator_data()
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        btc_candidate = next((c for c in candidates if c["symbol"] == "BTCUSDT"), None)
        if btc_candidate:
            assert btc_candidate.get("confirmation_tf_misaligned") is False, \
                "Empty string confirmation_tf should not trigger kill-switch"


class TestConfirmationTFKillSwitchStillWorksWhenSet:

    def test_set_confirmation_tf_still_neutralizes_on_disagreement(self):
        sub = _make_substrate(strategy={"confirmation_tf": "1d"})
        data = _make_bullish_indicator_data("BTCUSDT", "4H")
        data["BTCUSDT"]["1d"] = {
            "ok": True,
            "rsi": {"value": 30.0, "level": "oversold"},
            "macd": {
                "macd": -100.5, "signal": -95.2, "histogram": -5.3,
                "bias": "bearish", "histogram_growing": True,
                "crossover": False, "crossunder": False,
            },
            "ema_stack": {
                "ema20": 49800.0, "ema50": 50200.0, "ema200": 52000.0,
                "current_price": 49500.0, "alignment": "bearish", "stack": "bearish",
            },
            "adx": {"value": 28.5, "trend_strength": "trending", "direction": "bearish"},
        }
        sub.market["indicators"] = data
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        all_scores = result.analysis.get("all_scores", {})
        btc_score = all_scores.get("BTCUSDT", {})
        assert btc_score.get("confirmation_tf_misaligned") is True, \
            "When primary (4H bullish) and confirmation (1d bearish) disagree, misaligned should be True"

    def test_set_confirmation_tf_same_direction_not_neutralized(self):
        sub = _make_substrate(strategy={"confirmation_tf": "1d"})
        data = _make_bullish_indicator_data("BTCUSDT", "4H")
        data["BTCUSDT"]["1d"] = {
            "ok": True,
            "rsi": {"value": 65.3, "level": "neutral"},
            "macd": {
                "macd": 100.5, "signal": 95.2, "histogram": 5.3,
                "bias": "bullish", "histogram_growing": True,
                "crossover": False, "crossunder": False,
            },
            "ema_stack": {
                "ema20": 50200.0, "ema50": 49800.0, "ema200": 48000.0,
                "current_price": 50500.0, "alignment": "bullish", "stack": "bullish",
            },
            "adx": {"value": 28.5, "trend_strength": "trending", "direction": "bullish"},
        }
        sub.market["indicators"] = data
        sub.analysis["candidates"] = []
        sub.analysis["confluence_scored"] = False

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        all_scores = result.analysis.get("all_scores", {})
        btc_score = all_scores.get("BTCUSDT", {})
        assert btc_score.get("confirmation_tf_misaligned") is False, \
            "When primary and confirmation agree (both bullish), misaligned should be False"
