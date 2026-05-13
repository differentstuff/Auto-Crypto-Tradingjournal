# tests/test_agent_risk_mgmt.py
import pytest
from agent_types import TradePrepResult, RiskInput, RiskResult
import agent_risk_mgmt as rm


def _prep(setup_score=7, entry=1.50, sl=1.42, tp1=1.65, direction="Long") -> TradePrepResult:
    return TradePrepResult(
        setup_score=setup_score, direction=direction,
        entry_price=entry, sl_price=sl, tp1_price=tp1, tp2_price=tp1 * 1.05,
        rr_ratio=round(abs(tp1 - entry) / abs(entry - sl), 2),
        key_conditions=[], pattern_warnings=[], sizing_hint="",
        cot_reasoning="", gemini_score=7, consensus={}, raw_json={},
        chart_png_b64="", _model="", _cached_tokens=0,
    )


def test_basic_sizing():
    result = rm.run({"trade_prep": _prep(), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert isinstance(result["position_size_usdt"], float)
    assert result["position_size_usdt"] > 0
    assert isinstance(result["margin_usdt"], float)
    assert 0 < result["risk_pct"] <= 2.0


def test_blocks_on_sl_wrong_side():
    # Long with SL above entry — invalid
    result = rm.run({"trade_prep": _prep(entry=1.50, sl=1.55), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert result["approved"] is False
    assert any("stop loss" in w.lower() for w in result["warnings"])


def test_kelly_is_capped_at_025():
    result = rm.run({"trade_prep": _prep(), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert 0.05 <= result["kelly_fraction"] <= 0.25


def test_correlation_warning_on_concentration():
    positions = [{"side": "long"} for _ in range(4)]
    result = rm.run({"trade_prep": _prep(direction="Long"), "account_equity": 500.0,
                     "open_positions": positions}, conn=None)
    assert result["correlation_warning"] != ""
