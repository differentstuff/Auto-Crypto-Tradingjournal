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
from prompt_fragments import LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES
from constants import MODEL, FAST_MODEL
import re
from concurrent.futures import ThreadPoolExecutor

from ai_client import send as ai_send
from database import db_conn
from trade_history import get_symbol_summary
from helpers import strip_fence, build_cached_messages
import market_context
import prompt_builder
import trade_utils
import gemini_client
import agent_orchestrator

_TECH_TERMS = (
    "support", "resistance", "sr", "s/r", "trendline", "trend line",
    "ema", " sma", " ma ", "rsi", "macd", "level", "zone", "range",
    "breakout", "breakdown", "retest", "fib", "fibonacci", "atr",
    "pattern", "channel", "wedge", "triangle", "flag", "pennant",
    "consolidat", "accumul", "distribut", "volume", "liquidity",
    "sweep", "wick", "rejection", "confluence", "bullish", "bearish",
    "oversold", "overbought", "divergence", "crossover", "structure",
)


def _has_tech_levels(text: str) -> bool:
    """Return True if call text references technical analysis concepts worth charting."""
    lower = text.lower()
    return any(t in lower for t in _TECH_TERMS)

LEVERAGE = 10


# ── Sizing ─────────────────────────────────────────────────────────────────────


def _calc_sizing(account_equity: float, entry: float, sl: float,
                 dca_price: float = None, dca_pct: int = 40,
                 leverage: int = LEVERAGE, direction: str = "Long") -> dict:
    is_long   = direction.lower() == "long"
    has_dca   = dca_price is not None
    risk_pct  = 2.0 if has_dca else 1.0
    risk_amt  = round(account_equity * risk_pct / 100, 2)

    if has_dca:
        e1_pct    = 100 - dca_pct
        avg_entry = (entry * e1_pct + dca_price * dca_pct) / 100
    else:
        avg_entry = entry
        e1_pct    = 100

    # Validate SL placement: Long → SL below entry; Short → SL above entry
    if is_long and avg_entry <= sl:
        return {"error": "Long stop loss must be below entry price"}
    if not is_long and avg_entry >= sl:
        return {"error": "Short stop loss must be above entry price"}

    # stop_dist is always positive (distance as fraction of entry)
    stop_dist = abs(avg_entry - sl) / avg_entry
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
                  has_image: bool, atr_warning: str = "", corr_warning: str = "",
                  rubric: str = "", prior_cot: str = "") -> str:
    """Build the variable part of the call analysis prompt (context passed separately for caching)."""
    chart_note = (
        "A TradingView chart image is attached — analyse it carefully: "
        "identify the timeframe, key levels, chart pattern, support/resistance zones, "
        "the analyst's projected path, and any relevant price levels visible."
    ) if has_image else "No chart image was provided."

    atr_block    = f"\n⚠ ATR RISK: {atr_warning}\n" if atr_warning else ""
    corr_block   = f"\n⚠ PORTFOLIO CORRELATION: {corr_warning}\n" if corr_warning else ""
    rubric_block = f"\n{rubric}\n" if rubric else ""
    cot_block    = (
        f"\nPREVIOUS ANALYSIS OF THIS SYMBOL (your last call — check if setup conditions "
        f"have changed before repeating the same assessment):\n{prior_cot}\n"
    ) if prior_cot else ""

    return f"""You are an expert crypto futures trading analyst. A trade call from a crypto analyst has been submitted for analysis.

{chart_note}
{atr_block}{corr_block}{rubric_block}{cot_block}
TRADE CALL TEXT:
{call_text}

PRE-CALCULATED POSITION SIZING (do NOT recalculate — embed these numbers directly in your response):
{json.dumps(sizing, indent=2)}

TRADER'S HISTORY ON THIS SYMBOL:
{json.dumps(history, indent=2)}

INSTRUCTIONS: Before assigning any scores, reason step-by-step in the "thinking" field:
  1. Identify the trade direction and setup type
  2. Assess the structural anchor (is there a named S/R level, EMA, or trendline within 1× ATR?)
  3. Evaluate the SL placement (is it outside the ATR noise floor?)
  4. Calculate the R:R (is it ≥ 1.5:1? ≥ 2:1? ≥ 3:1?)
  5. Check market context signals (funding rate, Fear & Greed adjustments)
  6. Arrive at a score 1-10 with explicit reasoning
  THEN fill in all other fields.

Respond with ONLY a valid JSON object (no markdown, no code fences):

{{
  "thinking": "Step-by-step reasoning: 1) Direction = Long, setup = Breakout. 2) Entry $X is at... etc.",
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
  "pattern_flags": ["Rulebook warning that directly applies to this call, e.g. 'Friday breakout (your 3 losses avg -$166)'. Empty array if no warnings apply."],
  "optimizations": ["Specific improvement 1", "Specific improvement 2", "Specific improvement 3"],
  "risks": ["Risk 1", "Risk 2", "Risk 3"],
  "historical_context": "One sentence about trader history on this symbol",
  "sl_warning": "If SL is a candle-close type, explain exactly how to manage it manually in Bitget",
  "summary": "2-3 sentence honest overall assessment of this call"
}}

{LEVEL_PROXIMITY_RULES}

{MARKET_CONTEXT_RULES}

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

    # Symbol extraction (keep existing reliable logic)
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

    # Setup type detection
    detected_type = ""
    for kw, st in (("breakout","breakout"),("breakdown","breakout"),("reversal","reversal"),
                   ("trend follow","continuation"),("continuation","continuation"),
                   ("range","range"),("scalp","range")):
        if kw in text_lower:
            detected_type = st
            break

    with db_conn() as conn:
        analysis = agent_orchestrator.run_call_analysis(
            call_text      = call_text,
            symbol         = symbol,
            direction      = direction,
            account_equity = account_equity,
            setup_type     = detected_type,
            open_positions = open_positions or [],
            conn           = conn,
        )

    if analysis.get("degraded"):
        raise RuntimeError(analysis.get("error", "Agent pipeline failed"))

    # Reconstruct return format from AnalysisResult
    # raw_json is Claude's full response from TradePrep
    result = dict(analysis.get("raw_json") or {})

    # Ensure setup_quality exists with expected keys
    if "setup_quality" not in result:
        result["setup_quality"] = {}
    sq = result["setup_quality"]
    if not sq.get("score") and analysis.get("setup_score"):
        sq["score"] = analysis["setup_score"]
    if not sq.get("label") and sq.get("score"):
        score = int(sq.get("score", 0))
        sq["label"] = ("Strong" if score >= 9 else "Good" if score >= 7 else
                       "Monitor" if score >= 5 else "Avoid")

    # Consensus fields into setup_quality (existing routes read from here)
    if analysis.get("gemini_score"):
        sq["gemini_score"]         = analysis["gemini_score"]
        sq["consensus_score"]      = analysis.get("consensus", {}).get("consensus_score")
        sq["consensus_flag"]       = analysis.get("consensus", {}).get("flag")
        sq["consensus_confidence"] = analysis.get("consensus", {}).get("confidence")

    # Legacy compatibility keys
    result["_gemini"]    = {"score": analysis.get("gemini_score", 0)}
    result["_consensus"] = analysis.get("consensus", {})
    result["_sizing"]    = {
        "position_size_usdt":   analysis.get("position_size_usdt", 0.0),
        "total_notional_usdt":  analysis.get("position_size_usdt", 0.0),
        "margin_usdt":          analysis.get("margin_usdt", 0.0),
        "margin_needed_usdt":   analysis.get("margin_usdt", 0.0),
        "kelly_fraction":       analysis.get("kelly_fraction", 0.05),
        "risk_approved":        analysis.get("risk_approved", False),
        "account_equity":       round(account_equity, 2),
        "risk_pct":             1.0,
        "risk_amount_usdt":     round(account_equity * 0.01, 2),
        "leverage":             10,
        "entry_price":          analysis.get("entry_price"),
        "sl_price":             analysis.get("sl_price"),
        "avg_entry":            analysis.get("entry_price"),
    }

    # routes/calls.py reads rr.get("ratio") for rr_ratio column
    result.setdefault("risk_reward", {
        "ratio": analysis.get("rr_ratio"),
        "entry": analysis.get("entry_price"),
        "sl":    analysis.get("sl_price"),
        "tp1":   analysis.get("tp1_price"),
        "tp2":   analysis.get("tp2_price"),
    })

    # routes/calls.py reads tp1/tp2 prices from bitget_settings
    result.setdefault("bitget_settings", {
        "take_profit_1": {"price": analysis.get("tp1_price")},
        "take_profit_2": {"price": analysis.get("tp2_price")},
        "stop_loss":     {"price": analysis.get("sl_price")},
    })

    # Ensure NOT NULL DB columns are always populated
    result["symbol"]            = symbol
    result["direction"]         = result.get("direction") or direction

    result["_call_text"]        = call_text
    result["_history"]          = {}
    result["_signal_quality"]   = analysis.get("signal_quality", 0.0)
    result["_reviewer_warnings"] = analysis.get("reviewer_warnings", [])
    result["_contra_signal"]    = analysis.get("contra_signal", False)
    result["_model"]            = MODEL
    result["chart_png_b64"]     = analysis.get("chart_png_b64", "")

    return result
