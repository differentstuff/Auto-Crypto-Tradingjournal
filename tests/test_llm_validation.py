"""
tests/test_llm_validation.py -- Tests for the LLM validation pipeline.

Tests that:
1. _parse_llm_verdict: structured and fallback parsing of LLM responses
2. ValidateEntryZone: LLM enable/disable switch, relax_factor, verdict storage
3. ApproveTrade: LLM override gate (proceed enables sub-threshold trades)
4. RecordTradeOutcome: LLM fields written to trade_learning DB
5. ScoreConfluence: borderline candidates included for LLM review
6. Database migration 48: llm_verdict, llm_reason, llm_model, llm_enabled, llm_override

Design principle: LLM NEVER blocks a trade. It only ENABLES trades that
numeric rules would reject (via "proceed" verdict + relax_factor).

All tests are pure unit tests — no real network calls, no real LLM calls.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import make_full_config
from core.substrate import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(overrides: dict | None = None) -> dict:
    """Return a complete config with LLM defaults."""
    cfg = make_full_config(
        strategy={"name": "test_strategy", "uid": "test-uid"},
        scoring={"entry_threshold": 5.0, "confluence_min_signals": 2,
                 "rr_minimum": 1.5, "min_candidate_pct": 0.20,
                 "rsi_signal_high": 55, "rsi_signal_low": 45,
                 "momentum_cap": 1.5, "momentum_dampening": 0.5,
                 "modifier_weights": {"volume": 0.15, "cvd": 0.1, "order_flow": 0.1},
                 "label_thresholds": {"strong": 0.60, "weak": 0.33},
                 "formula": {
                     "rsi_midpoint": 50, "rsi_scale": 30.0,
                     "macd_aligned_growing": 1.0, "macd_aligned_fading": 0.5,
                     "ema_full_alignment": 1.0, "ema_partial_alignment": 0.5,
                     "adx_scale": 50.0,
                     "wavetrend_gold_signal": 1.0, "wavetrend_signal": 0.85,
                     "wavetrend_wt1_scale": 60.0, "wavetrend_no_signal_cap": 0.5,
                     "volume_confirm": 0.5, "volume_weaken": -0.25,
                     "cvd_trend": 0.4, "order_flow_pressure": 0.15,
                     "mfi_threshold": 10, "mfi_contribution": 0.3,
                 }},
        llm={"enabled": True, "relax_factor": 0.8,
              "routing": {
                  "analysis": {
                      "provider": "openrouter",
                      "model": "z-ai/glm-5.1",
                  }
              }},
        portfolio={"risk_per_trade_pct": 1.0, "leverage": 5, "max_positions": 3,
                   "max_total_risk_pct": 3.0, "fallback_equity_usdt": 1000.0,
                   "correlation_check": False, "max_same_direction": 3,
                   "atr_cap_equity_pct": 2.0},
        risk={"kelly_min": 0.05, "kelly_max": 0.25, "kelly_win_rate_base": 0.35,
              "kelly_win_rate_range": 0.40, "kelly_avg_win_r": 2.0,
              "max_size_pct_of_equity": 25.0, "min_size_pct_of_equity": 5.0},
        exit_rules={"hard_stop": {"width_atr_multiplier": 1.5, "always_active": True},
                    "trailing_stop": {"enabled": True, "activation_profit_pct": 0.5,
                                      "trail_atr_multiplier": 1.0, "breakeven_at_activation": True,
                                      "distance_pct": 1.0, "move_to_breakeven_at_pct": 1.5},
                    "tp2_rr_ratio": 2.5,
                    "soft_exit": {"requires_indicators_reversed": 2, "urgency": "soft"},
                    "soft_reversal_profit_threshold": 0.5},
    )
    if overrides:
        from conftest import _deep_update
        _deep_update(cfg, overrides)
    return cfg


def _make_substrate(config_overrides: dict | None = None) -> Substrate:
    return Substrate(config=_make_config(config_overrides))


def _make_entry_zone(symbol="BTCUSDT", direction="Long", entry_price=50000.0,
                     sl_price=49000.0, tp1=52000.0, tp2=53500.0,
                     atr_value=800.0, atr_pct=1.6, score=7.5, sl_type="atr") -> dict:
    return {
        "direction": direction, "entry_price": entry_price,
        "sl_price": sl_price, "tp1": tp1, "tp2": tp2,
        "rr_ratio": 2.0, "atr_value": atr_value, "atr_pct": atr_pct,
        "score": score, "label": "Strong Bullish", "timeframe": "1h",
        "sl_type": sl_type,
    }


# ---------------------------------------------------------------------------
# 1. _parse_llm_verdict tests
# ---------------------------------------------------------------------------

class TestParseLLMVerdict:
    """Test LLM response parsing into structured (verdict, reason) tuple."""

    def test_structured_proceed(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("VERDICT: proceed\nREASON: EMA alignment is bullish despite RSI neutral.")
        assert verdict == "proceed"
        assert "EMA" in reason

    def test_structured_confirm(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("VERDICT: confirm\nREASON: Strong confluence across all indicators.")
        assert verdict == "confirm"
        assert "confluence" in reason

    def test_structured_concern(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("VERDICT: concern\nREASON: Low volume suggests no real trend.")
        assert verdict == "concern"

    def test_structured_adjust(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("VERDICT: adjust\nREASON: SL too tight for current ATR.")
        assert verdict == "adjust"

    def test_fallback_proceed_from_text(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("I would proceed with this trade as the pattern looks valid.")
        assert verdict == "proceed"

    def test_fallback_concern_from_text(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("I flag concern about this entry due to low volume.")
        assert verdict == "concern"

    def test_fallback_adjust_from_text(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("I suggest adjustment to the stop loss placement.")
        assert verdict == "adjust"

    def test_default_confirm_on_garbage(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("asdfghjkl 12345")
        assert verdict == "confirm"  # Safe default — don't override on failure

    def test_default_confirm_on_empty(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("")
        assert verdict == "confirm"

    def test_default_confirm_on_none(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict(None)
        assert verdict == "confirm"

    def test_case_insensitive_verdict_line(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        verdict, reason = _parse_llm_verdict("verdict: PROCEED\nreason: looks good")
        assert verdict == "proceed"

    def test_reason_truncated_when_no_reason_line(self):
        from enzymes.validate_entry_zone import _parse_llm_verdict
        long_response = "A" * 500
        verdict, reason = _parse_llm_verdict(long_response)
        assert len(reason) <= 200


# ---------------------------------------------------------------------------
# 2. ValidateEntryZone LLM switch tests
# ---------------------------------------------------------------------------

class TestValidateEntryZoneLLMSwitch:
    """Test LLM enable/disable switch and relax_factor in ValidateEntryZone."""

    def test_no_llm_call_when_enabled_false(self):
        """llm.enabled=false → no call_llm invocation at all."""
        from enzymes.validate_entry_zone import ValidateEntryZone
        sub = _make_substrate({"llm": {"enabled": False, "relax_factor": 0.8}})
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.5, "pct": 0.5, "label": "Strong",
             "indicators_aligned": 4, "details": [], "confirmation_tf_misaligned": False},
        ]
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True, "ema_stack": {"current_price": 50000.0, "alignment": "bullish", "stack": "bullish"},
                "atr": {"value": 800.0, "pct": 1.6}, "sr_levels": [],
            }}
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone(config=_make_config({"llm": {"enabled": False, "relax_factor": 0.8}}))

        with patch("llm.router.call_llm") as mock_llm:
            result = enzyme.transform(sub)

        mock_llm.assert_not_called()
        # Entry zones should still exist (LLM is optional)
        zones = result.analysis.get("entry_zones", {})
        assert len(zones) > 0

    def test_llm_call_when_enabled_true(self):
        """llm.enabled=true → call_llm is invoked for above-threshold candidates."""
        from enzymes.validate_entry_zone import ValidateEntryZone
        sub = _make_substrate()
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.5, "pct": 0.5, "label": "Strong",
             "indicators_aligned": 4, "details": [], "confirmation_tf_misaligned": False},
        ]
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True, "ema_stack": {"current_price": 50000.0, "alignment": "bullish", "stack": "bullish"},
                "atr": {"value": 800.0, "pct": 1.6}, "sr_levels": [],
            }}
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone(config=_make_config())

        with patch("llm.router.call_llm", return_value="VERDICT: confirm\nREASON: Looks good.") as mock_llm:
            result = enzyme.transform(sub)

        mock_llm.assert_called()
        zone = result.analysis.get("entry_zones", {}).get("BTCUSDT", {})
        assert zone.get("llm_verdict") == "confirm"

    def test_llm_verdict_stored_on_entry_zone(self):
        """LLM verdict, reason, model are stored on the entry zone dict."""
        from enzymes.validate_entry_zone import ValidateEntryZone
        sub = _make_substrate()
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.5, "pct": 0.5, "label": "Strong",
             "indicators_aligned": 4, "details": [], "confirmation_tf_misaligned": False},
        ]
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True, "ema_stack": {"current_price": 50000.0, "alignment": "bullish", "stack": "bullish"},
                "atr": {"value": 800.0, "pct": 1.6}, "sr_levels": [],
            }}
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone(config=_make_config())

        with patch("llm.router.call_llm", return_value="VERDICT: proceed\nREASON: Pattern valid despite weak RSI."):
            result = enzyme.transform(sub)

        zone = result.analysis.get("entry_zones", {}).get("BTCUSDT", {})
        assert zone.get("llm_verdict") == "proceed"
        assert "Pattern" in zone.get("llm_reason", "")
        assert zone.get("llm_model") is not None
        assert zone.get("llm_enabled") is True

    def test_llm_override_flag_set_for_sub_threshold_proceed(self):
        """When LLM says 'proceed' for a sub-threshold candidate, llm_override=True."""
        from enzymes.validate_entry_zone import ValidateEntryZone
        sub = _make_substrate()
        # Candidate with score 4.2 (below threshold 5.0 but above relaxed 4.0)
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 4.2, "pct": 0.4, "label": "Bullish",
             "indicators_aligned": 3, "details": [], "confirmation_tf_misaligned": False,
             "llm_borderline": True},
        ]
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True, "ema_stack": {"current_price": 50000.0, "alignment": "bullish", "stack": "bullish"},
                "atr": {"value": 800.0, "pct": 1.6}, "sr_levels": [],
            }}
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone(config=_make_config())

        with patch("llm.router.call_llm", return_value="VERDICT: proceed\nREASON: Valid pattern."):
            result = enzyme.transform(sub)

        zone = result.analysis.get("entry_zones", {}).get("BTCUSDT", {})
        assert zone.get("llm_override") is True

    def test_llm_graceful_on_call_failure(self):
        """If call_llm returns None, entry zone still has llm_verdict=None."""
        from enzymes.validate_entry_zone import ValidateEntryZone
        sub = _make_substrate()
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.5, "pct": 0.5, "label": "Strong",
             "indicators_aligned": 4, "details": [], "confirmation_tf_misaligned": False},
        ]
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True, "ema_stack": {"current_price": 50000.0, "alignment": "bullish", "stack": "bullish"},
                "atr": {"value": 800.0, "pct": 1.6}, "sr_levels": [],
            }}
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone(config=_make_config())

        with patch("llm.router.call_llm", return_value=None):
            result = enzyme.transform(sub)

        zone = result.analysis.get("entry_zones", {}).get("BTCUSDT", {})
        assert zone.get("llm_verdict") is None
        assert zone.get("llm_enabled") is True
        assert zone.get("llm_override") is False


# ---------------------------------------------------------------------------
# 3. ApproveTrade LLM override tests
# ---------------------------------------------------------------------------

class TestApproveTradeLLMOverride:
    """Test LLM override gate in ApproveTrade."""

    def _get_enzyme(self, config_overrides=None):
        from enzymes.approve_trade import ApproveTrade
        return ApproveTrade(config=_make_config(config_overrides))

    def test_proceed_overrides_sub_threshold(self):
        """Score below threshold + LLM 'proceed' → trade allowed."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=4.2),  # Below threshold 5.0
        }
        # Set LLM override fields
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_verdict"] = "proceed"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_override"] = True
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_reason"] = "Valid pattern"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_model"] = "z-ai/glm-5.1"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_enabled"] = True

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert approved["symbol"] == "BTCUSDT"
        assert approved.get("llm_override") is True

    def test_sub_threshold_without_proceed_is_skipped(self):
        """Score below threshold + no LLM proceed → trade skipped."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=4.2),
        }
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_verdict"] = "concern"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_override"] = False
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_enabled"] = True

        result = enzyme.transform(sub)
        assert result.decisions.get("trade_approved") is None

    def test_above_threshold_passes_regardless_of_llm(self):
        """Score above threshold → trade allowed regardless of LLM verdict."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=7.5),
        }
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_verdict"] = "concern"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_override"] = False
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_enabled"] = True

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert approved.get("llm_verdict") == "concern"

    def test_llm_fields_copied_to_trade_approved(self):
        """LLM verdict, reason, model, enabled, override copied to trade_approved."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=7.5),
        }
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_verdict"] = "confirm"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_reason"] = "Strong confluence"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_model"] = "z-ai/glm-5.1"
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_enabled"] = True
        sub.analysis["entry_zones"]["BTCUSDT"]["llm_override"] = False

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert approved["llm_verdict"] == "confirm"
        assert approved["llm_reason"] == "Strong confluence"
        assert approved["llm_model"] == "z-ai/glm-5.1"
        assert approved["llm_enabled"] is True
        assert approved["llm_override"] is False

    def test_no_llm_fields_when_llm_disabled(self):
        """llm.enabled=false → no LLM fields on entry zones, trade approved normally."""
        enzyme = self._get_enzyme({"llm": {"enabled": False, "relax_factor": 0.8}})
        sub = _make_substrate({"llm": {"enabled": False, "relax_factor": 0.8}})
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=7.5),
        }

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        assert approved is not None
        # llm_verdict should be None (no LLM call made)
        assert approved.get("llm_verdict") is None
        assert approved.get("llm_enabled") is False


# ---------------------------------------------------------------------------
# 4. ScoreConfluence borderline tests
# ---------------------------------------------------------------------------

class TestScoreConfluenceBorderline:
    """Test that borderline candidates (relax_factor) are included."""

    def test_borderline_candidate_included(self):
        """Candidate scoring 4.2 (above 4.0 relaxed, below 5.0 threshold) is included."""
        from enzymes.score_confluence import ScoreConfluence
        sub = _make_substrate()
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True,
                "rsi": {"value": 58, "level": "neutral"},
                "macd": {"bias": "bullish", "histogram_growing": True, "crossover": False, "crossunder": False},
                "ema_stack": {"alignment": "bullish", "stack": "bullish", "current_price": 50000.0},
                "adx": {"value": 25, "trend_strength": "trending", "direction": "bullish"},
                "volume": {"ratio": 1.2, "current": 1000, "avg_20": 800, "signal": "normal"},
            }}
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        # With relax_factor=0.8, threshold=5.0, relaxed=4.0
        # Candidate should be included if score >= 4.0
        assert len(candidates) >= 1
        # Check borderline flag
        borderline = [c for c in candidates if c.get("llm_borderline")]
        # If score is between 4.0 and 5.0, llm_borderline should be True
        for c in candidates:
            if abs(c["score"]) < 5.0:
                assert c.get("llm_borderline") is True

    def test_below_relaxed_threshold_excluded(self):
        """Candidate scoring 3.0 (below 4.0 relaxed threshold) is excluded."""
        from enzymes.score_confluence import ScoreConfluence
        # Use a high threshold so the candidate falls below relaxed
        sub = _make_substrate({"scoring": {"entry_threshold": 10.0},
                               "llm": {"enabled": True, "relax_factor": 0.8}})
        sub.market["indicators"] = {
            "BTCUSDT": {"1h": {
                "ok": True,
                "rsi": {"value": 52, "level": "neutral"},
                "macd": {"bias": "neutral", "histogram_growing": False, "crossover": False, "crossunder": False},
                "ema_stack": {"alignment": "neutral", "stack": "neutral", "current_price": 50000.0},
                "adx": {"value": 15, "trend_strength": "weak", "direction": "neutral"},
                "volume": {"ratio": 0.8, "current": 600, "avg_20": 800, "signal": "low"},
            }}
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        # Score should be low enough to be excluded even with relax_factor
        btc_candidates = [c for c in candidates if c["symbol"] == "BTCUSDT"]
        assert len(btc_candidates) == 0


# ---------------------------------------------------------------------------
# 5. RecordTradeOutcome LLM tracking tests
# ---------------------------------------------------------------------------

class TestRecordTradeOutcomeLLMTracking:
    """Test that LLM fields are written to trade_learning DB."""

    def test_llm_fields_recorded_in_db(self, temp_db):
        """trade_approved with LLM fields → llm_verdict, llm_reason, etc. in DB."""
        from enzymes.record_trade_outcome import _record_trade_entry
        from core.database import db_conn

        trade_approved = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "score": 4.2,
            "timeframe": "1h",
            "llm_verdict": "proceed",
            "llm_reason": "Valid pattern despite weak RSI",
            "llm_model": "z-ai/glm-5.1",
            "llm_enabled": True,
            "llm_override": True,
        }

        _record_trade_entry(
            trade_approved, "test_strategy", "test-uid",
            signal_states={}, trajectory_data={}, indicator_data={},
            indicator_configs=[],
        )

        with db_conn() as conn:
            row = conn.execute(
                "SELECT llm_verdict, llm_reason, llm_model, llm_enabled, llm_override "
                "FROM trade_learning WHERE symbol='BTCUSDT' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert row is not None
        assert row["llm_verdict"] == "proceed"
        assert row["llm_reason"] == "Valid pattern despite weak RSI"
        assert row["llm_model"] == "z-ai/glm-5.1"
        assert row["llm_enabled"] == 1
        assert row["llm_override"] == 1

    def test_llm_fields_null_when_disabled(self, temp_db):
        """trade_approved without LLM → llm_verdict=NULL, llm_enabled=0."""
        from enzymes.record_trade_outcome import _record_trade_entry
        from core.database import db_conn

        trade_approved = {
            "symbol": "ETHUSDT",
            "direction": "Long",
            "score": 7.5,
            "timeframe": "1h",
            "llm_verdict": None,
            "llm_reason": None,
            "llm_model": None,
            "llm_enabled": False,
            "llm_override": False,
        }

        _record_trade_entry(
            trade_approved, "test_strategy", "test-uid",
            signal_states={}, trajectory_data={}, indicator_data={},
            indicator_configs=[],
        )

        with db_conn() as conn:
            row = conn.execute(
                "SELECT llm_verdict, llm_enabled, llm_override "
                "FROM trade_learning WHERE symbol='ETHUSDT' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert row is not None
        assert row["llm_verdict"] is None
        assert row["llm_enabled"] == 0
        assert row["llm_override"] == 0


# ---------------------------------------------------------------------------
# 6. Database migration 48
# ---------------------------------------------------------------------------

class TestMigration48:
    """Test that migration 48 adds LLM columns to trade_learning."""

    def test_migration_adds_llm_columns(self, temp_db):
        """After init_db(), trade_learning has llm_verdict, llm_reason, llm_model, llm_enabled, llm_override."""
        from core.database import db_conn

        with db_conn() as conn:
            # Get column names from trade_learning
            cursor = conn.execute("PRAGMA table_info(trade_learning)")
            columns = {row["name"] for row in cursor.fetchall()}

        assert "llm_verdict" in columns
        assert "llm_reason" in columns
        assert "llm_model" in columns
        assert "llm_enabled" in columns
        assert "llm_override" in columns
