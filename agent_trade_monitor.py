"""
agent_trade_monitor.py — TradeMonitor agent.

Called by the background monitor thread for each open position that passes
the polling filter. Runs a lightweight chain:
  InterpreterResult + SentimentResult → Haiku verdict.

Returns MonitorResult with action recommendation. Does NOT execute trades.
On risk_rating >= 7 or action != "Hold", callers fire Telegram + set
monitor_alert=1 in analyzed_calls.
"""
import json
import time

from constants import FAST_MODEL
from ai_client import send as ai_send
from helpers import strip_fence
from agent_types import MonitorInput, MonitorResult


def run(inp: MonitorInput) -> MonitorResult:
    return _call_haiku(inp)


def _call_haiku(inp: MonitorInput) -> MonitorResult:
    position    = inp["position"]
    orig_prep   = inp.get("original_prep") or {}
    interpreted = inp["interpreted"]
    sentiment   = inp["sentiment"]
    symbol      = position.get("symbol", "")

    prompt = _build_prompt(position, orig_prep, interpreted, sentiment)

    raw_text, _ = ai_send(
        "live_trade", FAST_MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=768,
    )
    raw = strip_fence(raw_text.strip())

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    # Handle both {value: N} and plain int for risk_rating
    risk_raw = data.get("risk_rating", {})
    if isinstance(risk_raw, dict):
        risk_rating = int(risk_raw.get("value", 5))
    else:
        risk_rating = int(risk_raw or 5)

    alert_level = "critical" if risk_rating >= 8 else "warning" if risk_rating >= 6 else "info"

    return MonitorResult(
        action            = data.get("action", "Hold"),
        action_reason     = data.get("action_reason", ""),
        risk_rating       = risk_rating,
        alert_level       = alert_level,
        tp_recommendation = data.get("tp_recommendation", {}),
        sl_recommendation = data.get("sl_recommendation", {}),
        key_risks         = data.get("key_risks", []),
        summary           = data.get("summary", ""),
        _symbol           = symbol,
        _checked_at       = time.time(),
    )


def _build_prompt(position: dict, orig_prep: dict,
                  interpreted: dict, sentiment: dict) -> str:
    symbol   = position.get("symbol", "")
    unrl_pct = position.get("unrealized_pct", 0)
    unrl_pl  = position.get("unrealizedPL", "?")
    entry    = position.get("openPrice", "?")
    mark     = position.get("markPrice", "?")
    dur      = position.get("duration_minutes", 0)
    side     = position.get("side", "long").title()
    lev      = position.get("leverage", "10")
    sl       = position.get("stop_loss", "") or orig_prep.get("sl_price", "not set")
    tp       = position.get("take_profit", "") or orig_prep.get("tp1_price", "not set")

    sent_txt   = sentiment.get("prompt_text", "")
    interp_txt = interpreted.get("prompt_text", "")

    is_short = side.lower() == "short"
    direction_ctx = (
        f"DIRECTION CONTEXT — {side.upper()}:\n"
        + (
            "- Bearish momentum (falling RSI, price below EMAs, bearish MACD) = FAVORABLE — price moving toward TP\n"
            "- Bullish momentum = UNFAVORABLE — price moving toward SL\n"
            f"- SL MUST be ABOVE entry ({entry}) — price rising above SL triggers the stop\n"
            f"- TP is BELOW entry ({entry}) — position profits as price falls\n"
            "- 'Price below all EMAs' = price moving in the profitable direction for this Short"
            if is_short else
            "- Bullish momentum (rising RSI, price above EMAs, bullish MACD) = FAVORABLE — price moving toward TP\n"
            "- Bearish momentum = UNFAVORABLE — price moving toward SL\n"
            f"- SL MUST be BELOW entry ({entry}) — price falling below SL triggers the stop\n"
            f"- TP is ABOVE entry ({entry}) — position profits as price rises"
        )
    )

    return f"""You are a crypto futures risk manager. Assess this OPEN position and give a specific, actionable verdict.

POSITION: {symbol} {side} {lev}x
Entry: {entry} | Mark: {mark} | Unrealized: {unrl_pct:.1f}% (${unrl_pl})
Duration: {dur:.0f} min | SL: {sl} | TP: {tp}

{direction_ctx}

CURRENT TECHNICALS:
{interp_txt}

MARKET SENTIMENT:
{sent_txt}

Respond with ONLY valid JSON (no markdown):
{{"risk_rating":{{"value":1,"label":"Low|Medium|High|Critical"}},"action":"Hold|Adjust SL|Partial Close|Close Now","action_reason":"one sentence WHY","tp_recommendation":{{"price":"0","rationale":"one sentence"}},"sl_recommendation":{{"price":"0","rationale":"one sentence"}},"key_risks":["risk 1","risk 2"],"summary":"2 sentence assessment"}}

Rules:
- unrealized_pct < -30% → seriously consider Close Now or Partial Close
- SL is "not set" → recommend setting one with correct placement (above entry for Short, below for Long)
- Contra signal (crowd against position) → raise risk_rating by 1
- Reference actual numbers in your reasoning
- For Short: recommend SL price ABOVE entry, TP price BELOW entry — never swap these"""
