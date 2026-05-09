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
from constants import ANTHROPIC_API_KEY, MODEL, FAST_MODEL
import anthropic
from database import db_conn
from helpers import strip_fence
import market_context
import prompt_builder



def _get_symbol_history(symbol: str, conn) -> dict:
    rows = conn.execute("""
        SELECT realized_pnl, duration_minutes, entry_price, close_price,
               direction, open_time, close_time
        FROM positions
        WHERE symbol = ?
        ORDER BY close_time DESC
        LIMIT 30
    """, (symbol,)).fetchall()

    if not rows:
        return {"trades": 0}

    pnls      = [r[0] for r in rows if r[0] is not None]
    wins      = [p for p in pnls if p > 0]
    losses    = [p for p in pnls if p < 0]
    durations = [r[1] for r in rows if r[1] is not None]

    return {
        "trades":         len(rows),
        "win_rate_pct":   round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":      round(sum(pnls), 2),
        "avg_win":        round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss":       round(sum(losses) / len(losses), 2) if losses else 0,
        "avg_duration_h": round(sum(durations) / len(durations) / 60, 1) if durations else 0,
        "recent_pnls":    [round(p, 2) for p in pnls[:10]],
    }


_POS_FIELDS = [
    "symbol", "direction", "leverage", "margin_mode", "size_usdt",
    "entry_price", "mark_price", "unrealized_pnl", "unrealized_pct",
    "take_profit", "stop_loss", "liquidation_price", "break_even_price",
    "duration_minutes", "achieved_profits", "total_fee",
]


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
    Run AI analysis on a single open position.
    position: dict from bitget_client.get_open_positions()
    Returns structured dict with recommendation.
    """
    mkt_str = market_context.get_market_str([position["symbol"]])

    with db_conn() as conn:
        history = _get_symbol_history(position["symbol"], conn)
        ctx_str = prompt_builder.build_context(
            conn            = conn,
            symbol          = position["symbol"],
            market_str      = mkt_str,
            timeframes      = ["4H", "1D"],
            include_similar = False,
        )

    prompt  = _build_prompt(position, history, ctx_str)
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=MODEL,
        max_tokens=768,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = strip_fence(message.content[0].text.strip())

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "risk_rating":        {"value": 0, "label": "Unknown"},
            "action":             "Review manually",
            "action_reason":      raw[:200],
            "tp_recommendation":  {"price": "", "rationale": ""},
            "sl_recommendation":  {"price": "", "rationale": ""},
            "key_risks":          [],
            "historical_context": "",
            "time_urgency":       "Unknown",
            "summary":            raw,
        }

    result["_symbol"]        = position["symbol"]
    result["_input_tokens"]  = message.usage.input_tokens
    result["_output_tokens"] = message.usage.output_tokens
    result["_history"]       = history
    return result
