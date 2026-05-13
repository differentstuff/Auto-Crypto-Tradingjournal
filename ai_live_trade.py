"""
ai_live_trade.py — Per-trade AI analysis for open positions.

Takes a single open position + the trader's historical stats on that symbol
and asks Claude for a specific, actionable assessment:
  - Risk rating (1–10, how urgent the situation is)
  - Action: Hold / Adjust SL / Partial Close / Close Now
  - TP/SL recommendations
  - Key risks and what to watch
  - Historical context ("your past 12 AIXBT trades had 58% win rate")

Response is structured JSON, rendered as a card in the Live Trades module.
"""

import json
import os
from constants import MODEL, FAST_MODEL
from ai_client import send as ai_send
from database import db_conn
from trade_history import get_symbol_summary
from helpers import strip_fence
import market_context
import prompt_builder




def _build_prompt(position: dict, history: dict, context_str: str = "") -> str:
    pos_slim  = {k: v for k, v in position.items() if k in _POS_FIELDS and v not in (None, "")}
    pos_json  = json.dumps(pos_slim)
    hist_json = json.dumps(history)
    ctx_block = f"\n{context_str}\n" if context_str else ""

    return f"""You are a professional crypto futures trading advisor. Analyze this OPEN position and give a specific, honest, actionable recommendation. The trader needs clear guidance — not generic advice.

OPEN POSITION:
{pos_json}

TRADER'S HISTORY ON {position['symbol']} (last {history.get('trades', 0)} closed trades):
{hist_json}
{ctx_block}
Key context:
- unrealized_pct is the current unrealized P/L as % of margin used
- take_profit / stop_loss: empty string means NO order is set
- duration_minutes: how long this trade has been open
- liquidation_price: the price where the position gets forcibly closed
- Use market context (funding rate, long/short ratio, Fear & Greed) to assess whether the crowd is against this position
- Use technical indicators (RSI, MACD, EMAs, Bollinger Bands) to judge momentum and trend alignment with the position

Respond with ONLY valid JSON (no markdown, no code fences):

{{"risk_rating":{{"value":1,"label":"Low|Medium|High|Critical"}},"action":"Hold|Adjust SL|Partial Close|Close Now","action_reason":"One sentence WHY this action is recommended","tp_recommendation":{{"price":"0.0","rationale":"one sentence"}},"sl_recommendation":{{"price":"0.0","rationale":"one sentence"}},"key_risks":["risk 1","risk 2","risk 3"],"historical_context":"One sentence about their past trades on this symbol","time_urgency":"Immediate|Today|No rush","summary":"2-3 sentence overall assessment referencing the actual numbers"}}

Rules:
- If unrealized_pct < -30%, seriously consider Close Now or Partial Close
- If stop_loss is empty AND unrealized_pct < -5%, recommend setting one immediately
- Reference actual numbers: entry price, mark price, unrealized PnL%, TP/SL prices
- If the position has a good TP/SL already set and reasonable PnL, "Hold" is valid
- Be direct and honest, not reassuring"""


def analyze_position(position: dict) -> dict:
    """
    Run AI analysis on a single open position. Delegates to agent_trade_monitor
    via agent_orchestrator.run_monitor(). External API unchanged.
    """
    import agent_orchestrator

    # Look up original TradePrepResult if an analyzed_call exists for this symbol
    original_prep = {}
    try:
        with db_conn() as conn:
            row = conn.execute(
                """SELECT analysis_json FROM analyzed_calls
                   WHERE symbol=? AND status IN ('matched','saved')
                   ORDER BY created_at DESC LIMIT 1""",
                (position["symbol"],),
            ).fetchone()
            if row and row["analysis_json"]:
                import json as _json
                analysis = _json.loads(row["analysis_json"])
                original_prep = {
                    "sl_price":  analysis.get("sl_price"),
                    "tp1_price": analysis.get("tp1_price") or analysis.get("tp1") or (analysis.get("risk_reward", {}) or {}).get("tp1"),
                }
    except Exception:
        pass

    result = agent_orchestrator.run_monitor(position, original_prep)

    # Reshape MonitorResult to existing return format that routes/live.py expects
    return {
        "risk_rating":        {"value": result["risk_rating"],
                               "label": result["alert_level"].title()},
        "action":             result["action"],
        "action_reason":      result["action_reason"],
        "tp_recommendation":  result["tp_recommendation"],
        "sl_recommendation":  result["sl_recommendation"],
        "key_risks":          result["key_risks"],
        "summary":            result["summary"],
        "historical_context": "",
        "time_urgency":       ("Immediate" if result["risk_rating"] >= 8 else
                               "Today" if result["risk_rating"] >= 6 else "No rush"),
        "_symbol":            result["_symbol"],
        "_model":             FAST_MODEL,
    }
