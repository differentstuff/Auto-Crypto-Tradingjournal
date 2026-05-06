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
import anthropic
from database import get_conn
import market_context
import chart_context

ANTHROPIC_API_KEY = os.environ.get(
    "ANTHROPIC_API_KEY",
    ""
)
MODEL = "claude-sonnet-4-6"


def _get_symbol_history(symbol: str, conn) -> dict:
    """Pull the trader's historical closed-trade stats for this symbol from the DB."""
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

    pnls     = [r[0] for r in rows if r[0] is not None]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p < 0]
    durations= [r[1] for r in rows if r[1] is not None]

    return {
        "trades":       len(rows),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":    round(sum(pnls), 2),
        "avg_win":      round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss":     round(sum(losses) / len(losses), 2) if losses else 0,
        "avg_duration_h": round(sum(durations) / len(durations) / 60, 1) if durations else 0,
        "recent_pnls":  [round(p, 2) for p in pnls[:10]],
    }


def _build_prompt(position: dict, history: dict, mkt_ctx: str = "", chart_ctx: str = "") -> str:
    pos_json  = json.dumps(position, indent=2)
    hist_json = json.dumps(history, indent=2)

    mkt_block   = f"\nCURRENT MARKET CONTEXT:\n{mkt_ctx}\n" if mkt_ctx else ""
    chart_block = f"\n{chart_ctx}\n" if chart_ctx else ""

    return f"""You are a professional crypto futures trading advisor. Analyze this OPEN position and give a specific, honest, actionable recommendation. The trader needs clear guidance — not generic advice.

OPEN POSITION:
{pos_json}

TRADER'S HISTORY ON {position['symbol']} (last {history.get('trades', 0)} closed trades):
{hist_json}
{mkt_block}{chart_block}
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
    conn    = get_conn()
    history = _get_symbol_history(position["symbol"], conn)
    conn.close()

    ctx       = market_context.get_market_context([position["symbol"]])
    mkt_str   = market_context.format_for_prompt(ctx)
    chart_str = chart_context.format_multi_tf_for_prompt(position["symbol"], ["4H", "1D"])
    prompt    = _build_prompt(position, history, mkt_str, chart_str)

    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

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
