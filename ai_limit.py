"""
ai_limit.py — AI analysis for pending limit orders (split from ai_call_analyzer.py v2.1).

Evaluates a pending limit order: entry quality vs chart levels, SL sizing vs ATR,
portfolio correlation risk, and overall recommendation (Keep / Adjust / Cancel).
"""

import json
import os
from constants import ANTHROPIC_API_KEY, MODEL, FAST_MODEL
from concurrent.futures import ThreadPoolExecutor

import anthropic

from database import db_conn
from helpers import strip_fence, log_token_usage
import market_context
import prompt_builder
import trade_utils



def _correlation_warning(symbol: str, direction: str, open_positions: list,
                          other_limits: list) -> str:
    if not open_positions and not other_limits:
        return ""
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    all_pos = list(open_positions or []) + [
        {"symbol": l["symbol"], "direction": l["direction"]} for l in (other_limits or [])
    ]
    warnings = []
    for sector, symbols in trade_utils.SECTORS.items():
        if sym in symbols:
            same = [p for p in all_pos
                    if p.get("symbol") in symbols and p.get("direction") == direction
                    and p.get("symbol") != sym]
            if same:
                warnings.append(
                    f"Adds {direction} in {sector} alongside "
                    f"{', '.join(p['symbol'] for p in same[:3])}"
                )
            break
    same_dir = [p for p in all_pos if p.get("direction") == direction]
    if len(same_dir) >= 2:
        warnings.append(
            f"{len(same_dir)} other {direction} positions/limits — correlated risk"
        )
    return " | ".join(warnings)


def _build_prompt(lim: dict, equity: float, context_str: str = "",
                  atr_warning: str = "", corr_warning: str = "",
                  total_other_notional: float = 0) -> str:
    lim_info = {k: lim.get(k) for k in
                ["symbol", "direction", "limit_price", "size_usdt", "leverage",
                 "sl_price", "tp1_price", "tp2_price", "analyst", "notes"]}
    lim_info = {k: v for k, v in lim_info.items() if v is not None}

    risk_pct = None
    entry = float(lim.get("limit_price") or 0)
    sl    = float(lim.get("sl_price") or 0)
    size  = float(lim.get("size_usdt") or 0)
    if entry > 0 and sl > 0 and size > 0 and entry != sl:
        risk_amt = abs(entry - sl) / entry * size
        risk_pct = round(risk_amt / equity * 100, 2) if equity else None

    ctx_block  = f"\n{context_str}\n" if context_str else ""
    atr_block  = f"\n⚠ ATR RISK: {atr_warning}\n" if atr_warning else ""
    corr_block = f"\n⚠ PORTFOLIO CORRELATION: {corr_warning}\n" if corr_warning else ""
    other_note = (f"Other pending limits total: {total_other_notional:.0f} USDT notional\n"
                  if total_other_notional > 0 else "")

    return f"""You are a crypto futures trading advisor. Evaluate this PENDING LIMIT ORDER — it has not yet been triggered.

LIMIT ORDER DETAILS:
{json.dumps(lim_info)}

Account equity: {equity:.2f} USDT
{other_note}Estimated risk if triggered: {f'{risk_pct}% of equity' if risk_pct else 'unknown (no SL set)'}
{ctx_block}{atr_block}{corr_block}
Assess this pending limit and respond with ONLY valid JSON (no markdown, no code fences):

{{
  "entry_quality": "Good / Acceptable / Poor",
  "entry_reason": "1-2 sentences: is the limit price at a logical level (support/S&R/pullback)?",
  "setup_quality": {{"score": 1-10, "label": "Poor|Weak|Moderate|Good|Strong|Excellent"}},
  "sl_assessment": "Adequate / Tight / Too Wide / Missing",
  "tp_assessment": "Good levels / Acceptable / Needs adjustment / Missing",
  "risk_assessment": "Safe / Manageable / Elevated / Dangerous",
  "recommendation": "Keep / Adjust entry / Adjust SL / Adjust TP / Cancel",
  "adjustments": ["Specific adjustment 1", "Specific adjustment 2"],
  "key_risks": ["risk 1", "risk 2"],
  "summary": "2-3 sentence overall assessment of this pending limit"
}}

Rules:
- Entry quality: is the limit price at a support/resistance/pullback level that makes technical sense?
- If SL is missing, flag as dangerous and recommend Cancel or at minimum setting one
- If no TP is set, suggest a take-profit level based on chart resistance
- Be direct — if this looks like a poor setup, say so"""


def analyze_pending_limit(lim: dict, equity: float, open_positions: list,
                           other_limits: list) -> dict:
    """
    Analyze a pending limit order.

    lim:            pending_limits row as dict
    equity:         account equity in USDT
    open_positions: current open positions from Bitget API
    other_limits:   other waiting limit rows (for correlation + exposure calc)
    """
    symbol    = (lim.get("symbol") or "UNKNOWN").upper()
    direction = lim.get("direction", "Long")
    entry     = float(lim.get("limit_price") or 0)
    sl        = float(lim.get("sl_price") or 0)

    total_other = sum(float(l.get("size_usdt") or 0) for l in (other_limits or []))
    corr_warn   = _correlation_warning(symbol, direction, open_positions, other_limits)

    # ATR check and market context are independent — fetch in parallel
    if entry and sl:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_atr = ex.submit(trade_utils.atr_sl_warning, symbol, entry, sl)
            f_mkt = ex.submit(market_context.get_market_str, [symbol])
            atr_warn = f_atr.result()
            mkt_str  = f_mkt.result()
    else:
        atr_warn = ""
        mkt_str  = market_context.get_market_str([symbol])

    with db_conn() as conn:
        ctx_str = prompt_builder.build_context(
            conn            = conn,
            symbol          = symbol,
            direction       = direction,
            market_str      = mkt_str,
            timeframes      = ["4H", "1D"],
            include_similar = False,
        )

    prompt = _build_prompt(lim, equity, ctx_str, atr_warn, corr_warn, total_other)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=MODEL, max_tokens=768,
        messages=[{"role": "user", "content": prompt}]
    )
    cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0
    log_token_usage("limit_analyzer", MODEL,
                    message.usage.input_tokens, message.usage.output_tokens, cached)

    raw = strip_fence(message.content[0].text.strip())

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "entry_quality":  "Unknown",
            "setup_quality":  {"score": 0, "label": "Parse Error"},
            "recommendation": "Review manually",
            "summary":        raw[:300],
            "key_risks":      [],
            "adjustments":    [],
        }

    result["_atr_warning"]   = atr_warn
    result["_corr_warning"]  = corr_warn
    result["_input_tokens"]  = message.usage.input_tokens
    result["_output_tokens"] = message.usage.output_tokens
    return result
