"""
ai_call.py — Analyst trade call analysis (split from ai_call_analyzer.py v2.1).

New vs v2.2:
  - Uses prompt_builder for shared context (rulebook, calibration, chart, similar trades)
  - ATR-aware SL quality check: warns when stop is inside 1H noise range
  - Portfolio correlation check: warns when trade adds directional concentration
  - setup_type NameError bug fixed (was undefined in original)
  - open_positions parameter for correlation analysis
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic

from database import db_conn
from helpers import strip_fence, build_cached_messages
import market_context
import prompt_builder
import trade_utils

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL    = "claude-sonnet-4-6"
LEVERAGE = 10


# ── Sizing ─────────────────────────────────────────────────────────────────────

def _symbol_history(symbol: str, conn) -> dict:
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
    has_dca   = dca_price is not None
    risk_pct  = 2.0 if has_dca else 1.0
    risk_amt  = round(account_equity * risk_pct / 100, 2)

    if has_dca:
        e1_pct    = 100 - dca_pct
        avg_entry = (entry * e1_pct + dca_price * dca_pct) / 100
    else:
        avg_entry = entry
        e1_pct    = 100

    if avg_entry <= sl:
        return {"error": "Stop loss must be below entry price"}

    stop_dist = (avg_entry - sl) / avg_entry
    notional  = round(risk_amt / stop_dist, 0)
    margin    = round(notional / leverage, 2)

    result = {
        "account_equity":      round(account_equity, 2),
        "risk_pct":            risk_pct,
        "risk_note":           "2% total across both legs (1% per entry)" if has_dca else "1% of account equity",
        "risk_amount_usdt":    risk_amt,
        "entry_price":         entry,
        "sl_price":            sl,
        "avg_entry":           round(avg_entry, 6),
        "stop_dist_pct":       round(stop_dist * 100, 2),
        "total_notional_usdt": int(notional),
        "leverage":            leverage,
        "margin_needed_usdt":  margin,
        "entry_1_pct":         e1_pct,
        "entry_1_notional":    int(notional * e1_pct / 100),
    }
    if has_dca:
        result["dca_price"]        = dca_price
        result["entry_2_pct"]      = dca_pct
        result["entry_2_notional"] = int(notional * dca_pct / 100)
    return result


# ── Portfolio correlation check ────────────────────────────────────────────────

def _correlation_warning(symbol: str, direction: str, open_positions: list) -> str:
    """Returns a warning string if this trade adds directional concentration risk."""
    if not open_positions:
        return ""
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    warnings = []
    for sector, symbols in trade_utils.SECTORS.items():
        if sym in symbols:
            same = [p for p in open_positions
                    if p.get("symbol") in symbols and p.get("direction") == direction
                    and p.get("symbol") != sym]
            if same:
                warnings.append(
                    f"Adds {direction} in {sector} sector alongside "
                    f"{', '.join(p['symbol'] for p in same[:3])}"
                )
            break
    same_dir = [p for p in open_positions if p.get("direction") == direction]
    if len(same_dir) >= 2:
        warnings.append(
            f"Portfolio already has {len(same_dir)} {direction} positions — "
            "correlated risk if market reverses"
        )
    return " | ".join(warnings)


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(call_text: str, sizing: dict, history: dict,
                  has_image: bool, atr_warning: str = "", corr_warning: str = "") -> str:
    """Build the variable part of the call analysis prompt (context passed separately for caching)."""
    chart_note = (
        "A TradingView chart image is attached — analyse it carefully: "
        "identify the timeframe, key levels, chart pattern, support/resistance zones, "
        "the analyst's projected path, and any relevant price levels visible."
    ) if has_image else "No chart image was provided."

    atr_block  = f"\n⚠ ATR RISK: {atr_warning}\n" if atr_warning else ""
    corr_block = f"\n⚠ PORTFOLIO CORRELATION: {corr_warning}\n" if corr_warning else ""

    return f"""You are an expert crypto futures trading analyst. A trade call from a crypto analyst has been submitted for analysis.

{chart_note}
{atr_block}{corr_block}
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
  "bitget_settings": {{"symbol":"XYZUSDT","direction":"Long / Buy","margin_mode":"Cross","leverage":"10x","order_1":{{"type":"Market","notional_usdt":0,"note":""}},"order_2":{{"type":"Limit","price":"0.0","notional_usdt":0,"note":"DCA"}},"stop_loss":{{"price":"0.0","type":"Price SL or Candle Close SL (manual)","bitget_instruction":"exact Bitget instruction"}},"take_profit_1":{{"price":"0.0","note":""}},"take_profit_2":{{"price":"0.0","note":""}}}},
  "entry_timing": "Immediate / Wait for retest / Set limit order — with reasoning",
  "optimizations": ["Specific improvement 1", "Specific improvement 2", "Specific improvement 3"],
  "risks": ["Risk 1", "Risk 2", "Risk 3"],
  "historical_context": "One sentence about trader history on this symbol",
  "sl_warning": "If SL is a candle-close type, explain exactly how to manage it manually in Bitget",
  "summary": "2-3 sentence honest overall assessment of this call"
}}

Level proximity definitions (use when scoring setup quality):
- Entry ≤ 0.5× ATR from structural level → strong anchor, no penalty
- Entry 0.5–1.0× ATR from structural level → acceptable, note it
- Entry > 1.0× ATR from nearest level → structural anchor missing → score ≤ 6
- SL < 1.0× ATR from entry → inside noise → score ≤ 6
- R:R < 1.5:1 → score ≤ 6; R:R ≥ 2:1 for score 7+; R:R ≥ 3:1 for score 9+

Rules:
- Use the pre-calculated position sizing numbers EXACTLY — do not change them
- If stop loss is based on a candle close (not a price level), set has_candle_close_sl=true and explain how to handle it
- Take profit levels: derive from chart resistance zones, risk:reward, or analyst's targets
- Optimizations must be specific and actionable (not generic)
- Entry timing: consider whether market order is optimal or if a limit at a specific level is better
- omit order_2 block entirely if has_dca is false"""


# ── Main entry point ───────────────────────────────────────────────────────────

def analyze_call(call_text: str, account_equity: float,
                 image_b64: str = None, image_type: str = "image/png",
                 market_regime: str = None, open_positions: list = None) -> dict:
    """
    Analyze a trade call. Returns structured dict ready for JSON serialization.

    call_text:      Raw analyst call text
    account_equity: Current Bitget account equity in USDT
    image_b64:      Optional base64-encoded chart image
    image_type:     MIME type of the image
    market_regime:  Optional pre-fetched market context string
    open_positions: Open position list for correlation check
    """
    text_lower = call_text.lower()
    setup_type = None  # resolved by Claude; None avoids NameError on similar-trades lookup

    # Priority: $SYMBOL / #SYMBOL → explicit XXXUSDT → bare 3-6 uppercase ticker
    _NON_TICKERS = {"SL", "TP", "DCA", "USD", "ATR", "RSI", "ALL", "BUY", "ASK", "BID"}
    sym_match = (
        re.search(r'[\$#]([A-Z]{2,10})', call_text)
        or re.search(r'\b([A-Z]{2,10})USDT\b', call_text)
    )
    if not sym_match:
        for m in re.finditer(r'\b([A-Z]{3,6})\b', call_text):
            if m.group(1) not in _NON_TICKERS:
                sym_match = m
                break
    symbol = (sym_match.group(1) + "USDT") if sym_match else "UNKNOWN"
    if symbol.endswith("USDTUSDT"):
        symbol = symbol[:-4]

    direction = "Short" if any(w in text_lower for w in ("short", "sell", "bearish")) else "Long"
    has_dca   = "dca"   in text_lower

    def _extract_price(keywords, text):
        for kw in keywords:
            # [^$\d]{0,20} — up to 20 non-price chars before the $ sign
            m = re.search(rf'{kw}[^$\d]{{0,20}}\$(\d{{2,}}\.\d+)', text, re.IGNORECASE)
            if m: return float(m.group(1))
            m = re.search(rf'{kw}[^\d]{{0,20}}(\d{{2,}}\.\d+)', text, re.IGNORECASE)
            if m: return float(m.group(1))
        return None

    entry_price = _extract_price(
        ["entry at", "at \\$", "@ \\$", "price of \\$", "market \\$"], call_text)
    dca_price   = _extract_price(["dca at", "dca:", "dca \\$"], call_text)
    sl_price    = _extract_price(
        ["sl.*?under", "sl.*?below", "stop.*?under", "stop.*?below",
         "under \\$", "below \\$", "sl at", "sl:"], call_text)

    all_prices = [float(x) for x in re.findall(r'\$(\d{2,}\.\d+)', call_text)]
    if not entry_price and all_prices:
        entry_price = max(all_prices) if direction == "Long" else min(all_prices)
    if not sl_price and len(all_prices) >= 2:
        sl_price = min(all_prices) if direction == "Long" else max(all_prices)
    if not dca_price and has_dca and len(all_prices) >= 3:
        sorted_p  = sorted(all_prices)
        dca_price = sorted_p[1] if direction == "Long" else sorted_p[-2]

    sizing = {}
    if entry_price and sl_price and entry_price != sl_price:
        sizing = _calc_sizing(account_equity, entry_price, sl_price,
                              dca_price if has_dca else None)
    else:
        sizing = {
            "note":             "Could not auto-extract prices — check call text",
            "account_equity":   round(account_equity, 2),
            "risk_pct":         2.0 if has_dca else 1.0,
            "risk_amount_usdt": round(account_equity * (0.02 if has_dca else 0.01), 2),
        }

    corr_warn = _correlation_warning(symbol, direction, open_positions or [])

    # ATR check and market context are independent — fetch in parallel
    mkt_str  = market_regime or ""
    atr_warn = ""
    need_atr = bool(entry_price and sl_price)
    need_mkt = not mkt_str

    if need_atr and need_mkt:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_atr = ex.submit(trade_utils.atr_sl_warning, symbol, entry_price, sl_price)
            f_mkt = ex.submit(market_context.get_market_str, [symbol])
        atr_warn = f_atr.result()
        mkt_str  = f_mkt.result()
    elif need_atr:
        atr_warn = trade_utils.atr_sl_warning(symbol, entry_price, sl_price)
    elif need_mkt:
        mkt_str = market_context.get_market_str([symbol])

    with db_conn() as conn:
        history = _symbol_history(symbol, conn)
        ctx_str = prompt_builder.build_context(
            conn       = conn,
            symbol     = symbol,
            direction  = direction,
            setup_type = setup_type,
            market_str = mkt_str,
            timeframes = ["4H", "1D"],
        )

    prompt   = _build_prompt(call_text, sizing, history, has_image=bool(image_b64),
                              atr_warning=atr_warn, corr_warning=corr_warn)
    messages = build_cached_messages(ctx_str, prompt, image_b64, image_type)
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message  = client.messages.create(
        model=MODEL, max_tokens=2048,
        messages=messages,
    )

    raw = strip_fence(message.content[0].text.strip())

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "symbol":        symbol, "direction": direction,
            "setup_quality": {"score": 0, "label": "Parse Error"},
            "chart_analysis": raw[:500],
            "summary":        raw,
            "bitget_settings": {}, "risk_reward": {},
            "optimizations": [], "risks": [],
        }

    result["_call_text"]     = call_text
    result["_sizing"]        = sizing
    result["_history"]       = history
    result["_input_tokens"]  = message.usage.input_tokens
    result["_output_tokens"] = message.usage.output_tokens
    return result
