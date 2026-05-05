"""
ai_call_analyzer.py — Analyzes crypto analyst trade calls (text + optional chart image).

Given a trade call like:
  "$GIGGLE LONG — market entry, DCA at $37.47, SL 4H close under $34.68"
  + optional TradingView screenshot

Claude (with vision) reads the chart, parses the call, and returns:
  - Setup quality score
  - Exact Bitget entry settings
  - Position sizing (1% risk for no-DCA, 2% for DCA)
  - Optimizations on the analyst's original call
  - Risk/reward breakdown

Position sizing formula (futures):
  risk_amount   = account_equity × risk_pct / 100
  stop_dist_pct = (avg_entry − sl) / avg_entry
  notional      = risk_amount / stop_dist_pct
  margin_needed = notional / leverage
"""

import base64
import json
import os
import re
import anthropic
from database import get_conn

ANTHROPIC_API_KEY = os.environ.get(
    "ANTHROPIC_API_KEY",
    "REDACTED_ANTHROPIC_KEY"
)
MODEL      = "claude-sonnet-4-6"
LEVERAGE   = 10   # default for position sizing; user can override


# ── Helpers ────────────────────────────────────────────────────────────────────

def _symbol_history(symbol: str, conn) -> dict:
    """Last 20 closed trades on this symbol."""
    rows = conn.execute("""
        SELECT realized_pnl, duration_minutes, direction
        FROM positions WHERE symbol = ?
        ORDER BY close_time DESC LIMIT 20
    """, (symbol,)).fetchall()
    if not rows:
        return {"trades": 0}
    pnls = [r[0] for r in rows if r[0] is not None]
    wins = [p for p in pnls if p > 0]
    return {
        "trades":       len(rows),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":    round(sum(pnls), 2),
        "avg_pnl":      round(sum(pnls) / len(pnls), 2) if pnls else 0,
    }


def _calc_sizing(account_equity: float, entry: float, sl: float,
                 dca_price: float = None, dca_pct: int = 40,
                 leverage: int = LEVERAGE) -> dict:
    """
    Calculate position size so that a stop-out at sl costs exactly risk_amount.

    DCA split: if dca_price provided, entry_1 gets (100-dca_pct)%, entry_2 dca_pct%.
    Average entry is weighted; stop distance uses that average.
    """
    has_dca    = dca_price is not None
    risk_pct   = 2.0 if has_dca else 1.0
    risk_amt   = round(account_equity * risk_pct / 100, 2)

    if has_dca:
        e1_pct  = 100 - dca_pct
        e2_pct  = dca_pct
        avg_entry = (entry * e1_pct + dca_price * e2_pct) / 100
    else:
        avg_entry = entry
        e1_pct, e2_pct = 100, 0

    if avg_entry <= sl:
        return {"error": "Stop loss must be below entry price"}

    stop_dist  = (avg_entry - sl) / avg_entry          # e.g. 0.0855 = 8.55%
    notional   = round(risk_amt / stop_dist, 0)        # total USDT notional
    margin     = round(notional / leverage, 2)

    result = {
        "account_equity":    round(account_equity, 2),
        "risk_pct":          risk_pct,
        "risk_amount_usdt":  risk_amt,
        "entry_price":       entry,
        "sl_price":          sl,
        "avg_entry":         round(avg_entry, 6),
        "stop_dist_pct":     round(stop_dist * 100, 2),
        "total_notional_usdt": int(notional),
        "leverage":          leverage,
        "margin_needed_usdt": margin,
        "entry_1_pct":       e1_pct,
        "entry_1_notional":  int(notional * e1_pct / 100),
    }
    if has_dca:
        result["dca_price"]        = dca_price
        result["entry_2_pct"]      = e2_pct
        result["entry_2_notional"] = int(notional * e2_pct / 100)

    return result


def _build_prompt(call_text: str, sizing: dict, history: dict,
                  has_image: bool) -> str:
    chart_note = (
        "A TradingView chart image is attached — analyse it carefully: "
        "identify the timeframe, key levels, chart pattern, support/resistance zones, "
        "the analyst's projected path, and any relevant price levels visible."
    ) if has_image else "No chart image was provided."

    return f"""You are an expert crypto futures trading analyst. A trade call from a crypto analyst has been submitted for analysis.

{chart_note}

TRADE CALL TEXT:
{call_text}

PRE-CALCULATED POSITION SIZING (do NOT recalculate — embed these numbers directly in your response):
{json.dumps(sizing, indent=2)}

TRADER'S HISTORY ON THIS SYMBOL:
{json.dumps(history, indent=2)}

Analyze the call and respond with ONLY a valid JSON object (no markdown, no code fences):

{{
  "symbol": "XYZUSDT",
  "direction": "Long",
  "trade_type": "e.g. Breakout / Range / Trend Follow / Reversal",
  "has_dca": true,
  "has_candle_close_sl": true,
  "setup_quality": {{"score": 1-10, "label": "Poor|Weak|Moderate|Good|Strong|Excellent"}},
  "chart_analysis": "What you see in the chart: pattern, key levels, breakout zone, target zone, context. Be specific about price levels visible. 3-4 sentences.",
  "risk_reward": {{"ratio": "1:X.X", "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0}},
  "bitget_settings": {{
    "symbol": "XYZUSDT",
    "direction": "Long / Buy",
    "margin_mode": "Cross",
    "leverage": "10x",
    "order_1": {{"type": "Market", "notional_usdt": 0, "note": ""}},
    "order_2": {{"type": "Limit", "price": "0.0", "notional_usdt": 0, "note": "DCA"}},
    "stop_loss": {{"price": "0.0", "type": "Price SL or Candle Close SL (manual)", "bitget_instruction": "Exact instruction for setting this in Bitget"}},
    "take_profit_1": {{"price": "0.0", "note": ""}},
    "take_profit_2": {{"price": "0.0", "note": ""}}
  }},
  "entry_timing": "Immediate / Wait for retest / Set limit order — with reasoning",
  "optimizations": ["Specific improvement 1", "Specific improvement 2", "Specific improvement 3"],
  "risks": ["Risk 1", "Risk 2", "Risk 3"],
  "historical_context": "One sentence about trader history on this symbol",
  "sl_warning": "If SL is a candle-close type, explain exactly how to manage it manually in Bitget",
  "summary": "2-3 sentence honest overall assessment of this call"
}}

Rules:
- Use the pre-calculated position sizing numbers EXACTLY — do not change them
- If stop loss is based on a candle close (not a price level), set has_candle_close_sl=true and explain how to handle it
- Take profit levels: derive from chart resistance zones, risk:reward, or analyst's targets
- Optimizations must be specific and actionable (not generic)
- Entry timing: consider whether market order is optimal or if a limit at a specific level is better
- omit order_2 block entirely if has_dca is false"""


# ── Main entry point ───────────────────────────────────────────────────────────

def analyze_call(call_text: str, account_equity: float,
                 image_b64: str = None, image_type: str = "image/png") -> dict:
    """
    Analyze a trade call. Returns structured dict ready for JSON serialization.

    call_text:      Raw analyst call text
    account_equity: Current Bitget account equity in USDT
    image_b64:      Optional base64-encoded chart image
    image_type:     MIME type of the image (default image/png)
    """

    # ── Extract key prices from the call text ──────────────────────────────
    # Best-effort regex extraction; Claude will refine these
    text_lower = call_text.lower()

    # Symbol
    sym_match  = re.search(r'\$([A-Z]{2,10})', call_text)
    symbol     = (sym_match.group(1) + "USDT") if sym_match else "UNKNOWN"
    if symbol.endswith("USDTUSDT"):
        symbol = symbol[:-4]

    direction  = "Long"  if "long"  in text_lower else "Short"
    has_dca    = "dca"   in text_lower

    # Extract price numbers near keywords
    def _extract_price(keywords, text):
        """Extract a price (must be $XX.XX or a number ≥ 2 digits with decimal, or ≥ 4 digits whole)."""
        for kw in keywords:
            # Prefer $-prefixed price
            m = re.search(rf'{kw}[^$\d{{0,20}}]*\$(\d{{2,}}\.\d+)', text, re.IGNORECASE)
            if m: return float(m.group(1))
            # Fall back to bare decimal number (e.g. "under 34.68") — at least 2 digits before dot
            m = re.search(rf'{kw}[^\d{{0,20}}]*(\d{{2,}}\.\d+)', text, re.IGNORECASE)
            if m: return float(m.group(1))
        return None

    entry_price = _extract_price(["entry at", "at \\$", "@ \\$", "price of \\$", "market \\$"], call_text)
    dca_price   = _extract_price(["dca at", "dca:", "dca \\$"], call_text)
    sl_price    = _extract_price(["sl.*?under", "sl.*?below", "stop.*?under", "stop.*?below",
                                   "under \\$", "below \\$", "sl at", "sl:"], call_text)

    # Last-resort: all $XX.XX amounts in the text (requires $ prefix to avoid timeframe numbers like 4H)
    all_prices  = [float(x) for x in re.findall(r'\$(\d{2,}\.\d+)', call_text)]

    if not entry_price and all_prices:
        entry_price = max(all_prices) if direction == "Long" else min(all_prices)
    if not sl_price and len(all_prices) >= 2:
        sl_price = min(all_prices) if direction == "Long" else max(all_prices)
    if not dca_price and has_dca and len(all_prices) >= 3:
        sorted_p = sorted(all_prices)
        dca_price = sorted_p[1] if direction == "Long" else sorted_p[-2]

    # ── Position sizing ────────────────────────────────────────────────────
    sizing = {}
    if entry_price and sl_price and entry_price != sl_price:
        sizing = _calc_sizing(
            account_equity = account_equity,
            entry          = entry_price,
            sl             = sl_price,
            dca_price      = dca_price if has_dca else None,
        )
    else:
        sizing = {
            "note": "Could not auto-extract prices — check call text",
            "account_equity": round(account_equity, 2),
            "risk_pct": 2.0 if has_dca else 1.0,
            "risk_amount_usdt": round(account_equity * (0.02 if has_dca else 0.01), 2),
        }

    # ── Symbol history ─────────────────────────────────────────────────────
    conn    = get_conn()
    history = _symbol_history(symbol, conn)
    conn.close()

    # ── Build prompt + call Claude ─────────────────────────────────────────
    prompt  = _build_prompt(call_text, sizing, history, has_image=bool(image_b64))
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    content = []
    if image_b64:
        content.append({
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": image_type,
                "data":       image_b64,
            }
        })
    content.append({"type": "text", "text": prompt})

    message = client.messages.create(
        model     = MODEL,
        max_tokens= 2048,
        messages  = [{"role": "user", "content": content}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "symbol": symbol, "direction": direction,
            "setup_quality": {"score": 0, "label": "Parse Error"},
            "chart_analysis": raw[:500],
            "summary": raw,
            "bitget_settings": {}, "risk_reward": {},
            "optimizations": [], "risks": [],
        }

    # Attach computed data
    result["_sizing"]        = sizing
    result["_history"]       = history
    result["_input_tokens"]  = message.usage.input_tokens
    result["_output_tokens"] = message.usage.output_tokens
    return result
