import logging
logger = logging.getLogger(__name__)
"""
ai_scanner.py — Proactive setup scanner.

Scans a watchlist of USDT-M futures for trade setups scored 6-10/10.

Three-stage pipeline:
  Stage 1 — Confluence filter (parallel, no AI):
             Computes multi-TF RSI/MACD/EMA/ADX signals for all symbols.
             Passes symbols with ≥ 2 signals aligned in one direction.

  Stage 2 — Technical quality gate (no AI, instant):
             Rejects severely overextended RSI, absent S/R structure, flat ADX.

  Stage 3 — AI scoring (parallel Claude calls, finalists only):
             Claude evaluates each finalist and returns scored setups 6-10/10
             with specific entry zone, SL, TP1, TP2 and rationale for each level.
             Setups below 6 are discarded.

Results cached for 30 minutes. Scan runs in a background thread.
"""

import json
import os
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES
from constants import (MODEL, FAST_MODEL,
    SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N, SCANNER_CACHE_TTL,
    SCANNER_MAX_WORKERS, PROMPT_CACHE_MIN_CHARS)
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ai_client import send as ai_send
from database import db_conn
from helpers import strip_fence, build_cached_messages
import chart_context
import market_context
import ai_rulebook
import nansen_client


# ── Watchlist ──────────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST = [
    # BTC / ETH
    "BTCUSDT", "ETHUSDT",
    # Major L1s
    "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "ATOMUSDT", "NEARUSDT", "TRXUSDT", "XLMUSDT",
    "TONUSDT", "FTMUSDT", "ALGOUSDT", "EGLDUSDT",
    # Mid-cap L1s
    "SUIUSDT", "APTUSDT", "INJUSDT", "SEIUSDT", "ICPUSDT",
    "STXUSDT", "TIAUSDT", "HBARUSDT", "KASUSDT", "MINAUSDT",
    # L2 / ETH ecosystem
    "MATICUSDT", "ARBUSDT", "OPUSDT", "STRKUSDT", "LDOUSDT",
    "ZKUSDT", "METISUSDT", "ENSUSDT",
    # DeFi
    "UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT",
    "SNXUSDT", "COMPUSDT", "DYDXUSDT", "CAKEUSDT", "GMXUSDT",
    "PENDLEUSDT", "JUPUSDT", "SUSHIUSDT", "RUNEUSDT",
    # AI / Infra
    "FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "GRTUSDT",
    "AGIXUSDT", "OCEANUSDT", "ARKMUSDT", "ACTUSDT",
    # Meme
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
    "BOMEUSDT", "FLOKIUSDT", "MOGUSDT", "POPCATUSDT", "MEWUSDT",
    "TURBOUSDT",
    # BTC ecosystem
    "ORDIUSDT", "SATSUSDT",
    # Gaming / Metaverse
    "SANDUSDT", "AXSUSDT", "GALAUSDT", "IMXUSDT", "MANAUSDT",
    "APEUSDT", "YGGUSDT",
    # Solana ecosystem
    "JITOUSDT", "WUSDT", "PYTHUSDT", "RAYUSDT",
    # Other liquid
    "LTCUSDT", "BCHUSDT", "FILUSDT", "QNTUSDT", "VETUSDT",
    "OKBUSDT", "ONDOUSDT", "ZECUSDT", "ONEUSDT", "ROSAUSDT",
    "CELOUSDT", "THETAUSDT", "NEOUSDT", "ONTUSDT", "IOTAUSDT",
    "WOOUSDT", "KLAYUSDT", "GMTUSDT",
]

# ── Criteria defaults ──────────────────────────────────────────────────────────
# Each key maps to a scoring check. When False the stage-2 gate skips the hard
# filter AND the prompt tells Claude to ignore that criterion.

CRITERIA_DEFAULTS: dict = {
    "rsi":        True,   # Reject overextended RSI (>78 long / <22 short)
    "macd":       True,   # MACD alignment counts as a 4H signal
    "ema_stack":  True,   # EMA stack alignment counts as a 4H signal
    "adx":        True,   # Reject ADX < 15 (flat/choppy)
    "sr_anchor":  True,   # Require ≥2 S/R levels + entry within 4×ATR
    "wavetrend":  True,   # VMC Cipher / WaveTrend signal in scoring
    "volume":     True,   # Volume confirmation in scoring
    "funding":    True,   # Funding rate penalty (-1/-2 score points)
    "fear_greed": True,   # Fear & Greed ±0.5 adjustment
    "atr_sl":     True,   # Cap score ≤ 6 when SL < 1×ATR from entry
    "rr_minimum": True,   # Cap score ≤ 6 when R:R < 1.5:1
}

_CRITERIA_DISABLED_LABELS: dict = {
    "rsi":        "RSI overbought/oversold — do NOT penalise or filter on RSI extremes",
    "macd":       "MACD alignment — ignore MACD direction entirely",
    "ema_stack":  "EMA stack — ignore EMA alignment entirely",
    "adx":        "ADX trend strength — do NOT require or factor ADX",
    "sr_anchor":  "S/R anchor — entry does NOT need to be near a named level; score purely on momentum/pattern",
    "wavetrend":  "WaveTrend/VMC Cipher — ignore WT signal entirely",
    "volume":     "Volume confirmation — do NOT require or reward volume",
    "funding":    "Funding rate — do NOT apply any funding rate penalties",
    "fear_greed": "Fear & Greed — do NOT apply F&G score adjustments",
    "atr_sl":     "ATR SL floor — do NOT cap score if SL is tight (inside 1×ATR)",
    "rr_minimum": "R:R minimum — do NOT cap score for low R:R; score the setup quality regardless",
}


def _disabled_criteria_block(criteria: dict) -> str:
    """Return a prompt section listing which checks Claude must skip."""
    disabled = [
        f"  - {_CRITERIA_DISABLED_LABELS[k]}"
        for k in _CRITERIA_DISABLED_LABELS
        if not criteria.get(k, True)
    ]
    if not disabled:
        return ""
    return (
        "DISABLED SCORING CRITERIA (user has turned these OFF — do NOT apply them, "
        "do NOT mention them in your rationale):\n" + "\n".join(disabled)
    )


# ── Scan state ─────────────────────────────────────────────────────────────────

_state: dict = {
    "status":          "idle",   # idle | running | completed | error
    "stage":           0,        # 0=idle, 1=confluence, 2=quality gate, 3=AI scoring
    "stage_label":     "",       # e.g. "Stage 1 — Confluence filter"
    "stage_detail":    "",       # e.g. "42 / 100 symbols processed"
    "stage_progress":  0,        # 0–100 within current stage
    "started_at":      None,
    "completed_at":    None,
    "duration_sec":    None,
    "setups":          [],
    "scanned":         0,
    "after_filter":    0,
    "error":           None,
    "min_score":       SCANNER_MIN_SCORE,
}
_state_lock = threading.Lock()


def get_state() -> dict:
    with _state_lock:
        return dict(_state)


def _update(**kwargs):
    with _state_lock:
        _state.update(kwargs)


# ── Stage 1: confluence pre-filter ─────────────────────────────────────────────

def _fetch_one(symbol: str):
    try:
        tfs = ["4H", "1D"]
        ctx  = chart_context.get_chart_context(symbol, tfs)
        conf = chart_context.confluence_score(symbol, tfs, ctx=ctx)
        return symbol, ctx, conf
    except Exception as e:
        logger.warning("chart context fetch failed for %s: %s", symbol, e)
        return symbol, None, None


def _stage1(symbols: list, min_score: int = SCANNER_MIN_SCORE) -> list:
    """Return [(symbol, ctx, conf, direction)] with enough aligned signals.
    Emits live progress via _update() as futures complete."""
    threshold = 1 if min_score <= 3 else 2
    total = len(symbols)
    out   = []
    done  = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            symbol, ctx, conf = f.result()
            done += 1
            if done % 10 == 0 or done == total:
                _update(
                    stage_detail  = f"{done} / {total} symbols fetched",
                    stage_progress= int(done / total * 100),
                )
            if ctx is None or conf is None:
                continue
            if conf["bullish"] >= threshold:
                out.append((symbol, ctx, conf, "Long"))
            elif conf["bearish"] >= threshold:
                out.append((symbol, ctx, conf, "Short"))
    return out


# ── Stage 2: technical quality gate ────────────────────────────────────────────

def _stage2(candidates: list, min_score: int = SCANNER_MIN_SCORE,
             criteria: dict = None) -> list:
    """
    Technical quality gate — no AI, no network calls.
    Respects `criteria` dict: disabled criteria skip the corresponding hard filter.
    Skipped entirely when min_score ≤ 4 (user wants AI to be the sole judge).
    Caps output at 30 to control Claude API cost.
    """
    cr = criteria or CRITERIA_DEFAULTS

    if min_score <= 4:
        out = list(candidates)
        out.sort(key=lambda x: -x[2].get("score", 0))
        return out[:30]

    out = []
    for symbol, ctx, conf, direction in candidates:
        inds = ctx.get("4H", {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_val = inds.get("rsi", {}).get("value", 50)
        adx_val = (inds.get("adx", {}) or {}).get("value", 0)
        sr      = inds.get("support_resistance", [])
        ema     = inds.get("ema", {}) or {}
        macd    = inds.get("macd", {}) or {}
        adx_d   = inds.get("adx",  {}) or {}
        price   = ema.get("current_price")
        atr_val = (inds.get("atr", {}) or {}).get("value", 0)

        # Reject: RSI already deeply overextended in signal direction
        if cr.get("rsi", True):
            if direction == "Long"  and rsi_val > 78:
                continue
            if direction == "Short" and rsi_val < 22:
                continue

        # Reject: no trend structure (choppy / flat)
        if cr.get("adx", True) and adx_val < 15:
            continue

        # Reject: no S/R structure to define entry/SL/TP
        if cr.get("sr_anchor", True):
            if len(sr) < 2:
                continue
            if price and atr_val and sr:
                distances = [abs(l["price"] - price) for l in sr]
                if min(distances) > atr_val * 4:
                    continue

        # Require at least 2 aligned 4H signals (only from enabled indicators)
        bull_4h = bear_4h = 0
        if cr.get("rsi", True):
            if rsi_val > 55:   bull_4h += 1
            elif rsi_val < 45: bear_4h += 1
        if cr.get("macd", True):
            if macd.get("trend") == "bullish":   bull_4h += 1
            elif macd.get("trend") == "bearish": bear_4h += 1
        if cr.get("ema_stack", True):
            if "bullish" in ema.get("alignment", ""):   bull_4h += 1
            elif "bearish" in ema.get("alignment", ""): bear_4h += 1
        if cr.get("adx", True):
            if "bullish" in adx_d.get("direction", ""):   bull_4h += 1
            elif "bearish" in adx_d.get("direction", ""): bear_4h += 1

        # Minimum 2 aligned signals — but only if at least 2 criteria are enabled
        enabled_signal_criteria = sum(cr.get(k, True) for k in ("rsi", "macd", "ema_stack", "adx"))
        if enabled_signal_criteria >= 2:
            if direction == "Long"  and bull_4h < 2:
                continue
            if direction == "Short" and bear_4h < 2:
                continue

        out.append((symbol, ctx, conf, direction))

    out.sort(key=lambda x: -x[2].get("score", 0))
    return out[:30]


# ── Stage 3: AI scoring ─────────────────────────────────────────────────────────

def _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str, min_score=SCANNER_MIN_SCORE):
    inds_4h = ctx.get("4H", {}).get("indicators", {})
    ema     = inds_4h.get("ema", {}) or {}
    price   = ema.get("current_price", 0)
    atr_val = (inds_4h.get("atr", {}) or {}).get("value", 0)

    sr_4h = inds_4h.get("support_resistance", [])
    sr_text = "\n".join(
        f"  {l['type'].upper():10s} {l['price']:.6g}  "
        f"(strength {l.get('strength', 1)}, {l.get('touches', 1)} touches)"
        for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:6]
    ) or "  None detected"

    tl_4h = inds_4h.get("trendlines", [])
    tl_text = "\n".join(
        f"  {l.get('direction','?').upper()} trendline — {l.get('timeframe','4H')}, "
        f"{l.get('touches',1)} touches"
        for l in tl_4h[:3]
    ) or "  None"

    pt_4h = ctx.get("4H", {}).get("prompt_text", "No 4H data")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "No 1D data")
    conf_line = (
        f"{conf['label']} ({conf['score']:+.2f}/{conf['max']} — "
        f"{conf['bullish']} bullish / {conf['bearish']} bearish signals)"
    )

    price_note = f"Current price: {price:.6g}  |  1H-equivalent ATR: ~{atr_val:.4g}" if price else ""
    hist_text  = json.dumps(history) if history.get("trades") else "No closed trades on this symbol yet"
    mkt_block  = f"\nMARKET CONTEXT:\n{mkt_str}\n" if mkt_str else ""
    rb_block   = f"\nTRADER RULEBOOK (known patterns — respect these):\n{rulebook_str}\n" if rulebook_str else ""

    return f"""You are a professional crypto futures analyst. Score the current {direction.upper()} setup for {symbol} on a 1-10 scale and provide specific trade parameters.

TECHNICAL SUMMARY:
{pt_4h}
{pt_1d}

CONFLUENCE: {conf_line}
{price_note}

KEY S/R LEVELS (4H):
{sr_text}

ACTIVE TRENDLINES:
{tl_text}
{mkt_block}
TRADER HISTORY ON {symbol}:
{hist_text}
{rb_block}
─────────────────────────────────────────
{MARKET_CONTEXT_RULES}

{SCORING_SCALE}

{LEVEL_PROXIMITY_RULES}

REQUIREMENTS for any score ≥ {min_score}:
- A specific entry zone anchored to a named structural level (S/R, EMA, trendline) with exact prices
- A stop loss beyond the nearest structural level and ≥ 1× ATR from entry — state the level and ATR distance explicitly
- At least two take-profit levels at significant resistance/support — state exactly what each targets
- Detailed rationale for EVERY level: name the S/R zone, reference the indicator value, explain WHY

RATIONALE DEPTH REQUIRED:
  entry_rationale: "Price is pulling back to the 4H EMA50 ($X) which coincides with the 1D support zone at $Y (5 touches). RSI has cooled to 48 from 68 — reset without breaking structure."
  sl_rationale: "Below the 1D support at $Z and the swing low at $W. Distance of $D = 1.8× 4H ATR ($A), placing the stop clearly outside noise."
  tp1_rationale: "Previous 4H resistance at $R1, high-volume rejection on [date]. R:R 1:2.3 from midpoint entry."
  tp2_rationale: "Weekly resistance cluster and the 1.618 Fibonacci extension from last major swing. R:R 1:4.1."

If the setup scores below {min_score} (no valid entry level, SL inside ATR noise, or no logical TP):
{{"setup_score": 0, "reason": "one sentence why this doesn't qualify"}}

Otherwise respond with this exact structure:
{{
  "setup_score": 8, "setup_label": "Strong",
  "direction": "{direction}",
  "why_this_score": "2-3 sentences explaining specifically what earns this score and what would need to be different for a 9 or a 7",
  "entry_zone": {{"low": 0.0, "high": 0.0, "rationale": "Name the level, give the price, explain WHY this is the entry zone"}},
  "sl_price": 0.0,
  "sl_rationale": "Name the structural level, state ATR distance (e.g. 1.6× 4H ATR), explain the invalidation logic",
  "tp1_price": 0.0,
  "tp1_rationale": "Name the resistance/target, explain why price is likely to stall or reverse there",
  "tp2_price": 0.0,
  "tp2_rationale": "Name the higher target, explain the structural or Fibonacci significance",
  "rr_ratio": "1:X.X",
  "chart_pattern": "Specific pattern name — or null if none",
  "key_conditions": ["Specific signal with values, e.g. RSI 47 reset from 71", "MACD bull crossover on 4H", "EMA stack 20>50>200 bullish"],
  "risks": ["Specific risk with context", "Second risk"],
  "urgency": "Now|1-4h|Today|1-3 days",
  "timeframe": "4H",
  "confluence_summary": "One sentence: the 2-3 most important aligned signals that create conviction",
  "summary": "2-3 sentence overall assessment referencing actual price numbers from the technical picture"
}}

Respond with ONLY valid JSON — no markdown, no code fences."""


def _build_shared_prefix(mkt_str: str, rulebook_str: str,
                          min_score: int = SCANNER_MIN_SCORE, criteria: dict = None) -> str:
    """Shared context block — identical for all finalists in a scan, caches across calls."""
    cr        = criteria or CRITERIA_DEFAULTS
    mkt_block = f"MARKET CONTEXT:\n{mkt_str}\n\n" if mkt_str else ""
    rb_block  = f"TRADER RULEBOOK:\n{rulebook_str}\n\n" if rulebook_str else ""
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""

    # Build dynamic score cap line based on enabled criteria
    caps = []
    if cr.get("sr_anchor", True): caps.append("no structural entry")
    if cr.get("atr_sl",    True): caps.append("SL inside ATR noise")
    if cr.get("rr_minimum",True): caps.append("R:R below 1.5:1")
    cap_str = " or ".join(caps) if caps else "no valid setup"

    return (
        f"{mkt_block}{rb_block}"
        + SCORING_SCALE + "\n"
        + "5=Mod(borderline), 6=Accept(R:R≥1.5), 7=Good(R:R≥2:1), "
        + "8=Strong(R:R≥2.5:1), 9=Excellent(multi-TF,R:R≥3:1), 10=Perfect(R:R≥4:1)\n"
        + f"Score <{min_score} if: {cap_str}.{dis_part}"
    )


def _quick_score(symbol: str, ctx: dict, conf: dict, direction: str,
                 shared_prefix: str, min_score: int = SCANNER_MIN_SCORE) -> dict | None:
    """
    Pass 1 — cheap Haiku call returning only a score (0-10) and confirmed direction.
    Returns None if score < min_score or on any error.
    """
    pt_4h = ctx.get("4H", {}).get("prompt_text", "")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "")
    conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"

    inds_4h = ctx.get("4H", {}).get("indicators", {})
    sr = inds_4h.get("support_resistance", [])
    sr_compact = "  ".join(
        f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
        for l in sorted(sr, key=lambda x: -x.get("touches", 1))[:4]
    ) or "none"

    variable = (
        f"Score this {direction.upper()} setup for {symbol} — return score 0-10 "
        f"and one short sentence explaining the key factor behind the score.\n\n"
        f"{pt_4h}\n{pt_1d}\n"
        f"Confluence: {conf_line}\nS/R: {sr_compact}\n\n"
        f'If score < {min_score}: {{"score":0}}\n'
        f'If score >= {min_score}: {{"score":7,"direction":"{direction}",'
        f'"reason":"one sentence — main factor (e.g. \'4H EMA stack bullish, RSI reset to 52, clean S/R entry zone\')"}}\n'
        "Respond with ONLY valid JSON — no extras."
    )

    try:
        msg_text, _cached = ai_send(
            "scanner_quick", FAST_MODEL,
            [{"role": "user", "content": [
                {"type": "text", "text": shared_prefix},
                {"type": "text", "text": variable},
            ]}],
            max_tokens=120,
        )
        r = json.loads(strip_fence(msg_text.strip()))
        if r.get("score", 0) < min_score:
            return None
        return {
            "score":     r["score"],
            "direction": r.get("direction", direction),
            "reason":    r.get("reason", ""),
        }
    except Exception as e:
        logger.warning("quick-score failed for %s: %s", symbol, e)
        return None


def _ai_score(symbol, ctx, conf, direction, mkt_str, history, rulebook_str,
              min_score=SCANNER_MIN_SCORE, criteria=None):
    try:
        cr     = criteria or CRITERIA_DEFAULTS
        shared = _build_shared_prefix(mkt_str, rulebook_str, min_score, criteria=cr)
        prompt = _build_prompt(symbol, ctx, conf, direction, "", history, rulebook_str, min_score)
        raw_text, _cached = ai_send(
            "scanner_batch", MODEL,
            build_cached_messages(shared, prompt),
            max_tokens=1200,
        )
        result = json.loads(strip_fence(raw_text.strip()))
        if result.get("setup_score", 0) < min_score:
            return None
        result["_symbol"] = symbol
        return result
    except Exception as e:
        logger.warning("quick-score API call failed for %s: %s", symbol, e)
        return None


def _build_batch_prompt(finalists, histories, min_score=SCANNER_MIN_SCORE, criteria=None,
                        nansen_signals=None):
    """Build a single prompt for all top-N symbols. Returns (system_prefix, user_prompt)."""
    parts = []
    for i, (symbol, ctx, conf, direction, score, _reason) in enumerate(finalists, 1):
        inds_4h = ctx.get("4H", {}).get("indicators", {})
        ema     = inds_4h.get("ema", {}) or {}
        price   = ema.get("current_price", 0)
        atr_val = (inds_4h.get("atr", {}) or {}).get("value", 0)
        pt_4h   = ctx.get("4H", {}).get("prompt_text", "No 4H data")
        pt_1d   = ctx.get("1D", {}).get("prompt_text", "No 1D data")
        sr_4h   = inds_4h.get("support_resistance", [])
        sr_text = "  ".join(
            f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
            for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:4]
        ) or "none"
        hist = histories.get(symbol, {"trades": 0})
        conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"
        # Nansen smart money line (only when 5+ traders — already filtered in client)
        ns      = (nansen_signals or {}).get(symbol, {})
        ns_line = f"\n{ns['prompt_line']}" if ns.get("ok") else ""
        parts.append(
            f"--- SETUP {i}: {symbol} ({direction.upper()}) ---\n"
            f"{pt_4h}\n{pt_1d}\n"
            f"Confluence: {conf_line}  |  Price: {price:.6g}  |  ATR: {atr_val:.4g}\n"
            f"S/R: {sr_text}\n"
            f"History: {json.dumps(hist)}{ns_line}"
        )

    cr = criteria or CRITERIA_DEFAULTS
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""

    # Dynamic score cap rules based on enabled criteria
    level_rules = []
    if cr.get("sr_anchor", True): level_rules.append("Entry >1×ATR from level → max 6")
    if cr.get("atr_sl",    True): level_rules.append("SL <1×ATR from entry → max 6")
    if cr.get("rr_minimum",True): level_rules.append("R:R<1.5 → max 6")
    level_str = ". ".join(level_rules) + "." if level_rules else ""

    setups_text = "\n\n".join(parts)
    scoring_hint = SCORING_SCALE[:60] + " ... 9=Excellent(R:R≥3:1), 10=Perfect(R:R≥4:1)"
    user_prompt = (
        f"Analyze these {len(finalists)} crypto futures setups. "
        f"Return a JSON ARRAY of exactly {len(finalists)} objects — one per setup, in the same order.\n\n"
        f"{setups_text}\n\n"
        f"{scoring_hint}\n"
        f"{level_str}{dis_part}\n"
        f"For setups scoring >= {min_score}, use this structure:\n"
        '{"symbol":"X","direction":"Long","setup_score":7,"setup_label":"Good",'
        '"why_this_score":"2-3 sentences","entry_zone":{"low":0,"high":0,"rationale":"..."},'
        '"sl_price":0,"sl_rationale":"...","tp1_price":0,"tp1_rationale":"...",'
        '"tp2_price":0,"tp2_rationale":"...","rr_ratio":"1:X","chart_pattern":null,'
        '"key_conditions":["..."],"risks":["..."],"urgency":"Now|1-4h|Today|1-3 days",'
        '"timeframe":"4H","confluence_summary":"...","summary":"..."}\n'
        f'For setups scoring below {min_score}: {{"symbol":"X","setup_score":0,"reason":"why"}}\n\n'
        "Respond with ONLY a valid JSON array — no markdown, no code fences."
    )
    return user_prompt


def _batch_ai_score(finalists, mkt_str, histories, rulebook_str,
                    min_score=SCANNER_MIN_SCORE, criteria=None, nansen_signals=None):
    """
    Single Claude (Sonnet) call for all top-N finalists.
    Falls back to individual calls if the batch response is malformed or incomplete.
    """
    if not finalists:
        return []
    cr = criteria or CRITERIA_DEFAULTS
    shared = _build_shared_prefix(mkt_str, rulebook_str, min_score, criteria=cr)
    user_prompt = _build_batch_prompt(finalists, histories, min_score, criteria=cr,
                                      nansen_signals=nansen_signals)
    try:
        raw_text, _cached = ai_send(
            "scanner_batch", MODEL,
            build_cached_messages(shared, user_prompt),
            max_tokens=min(4096, 1200 * len(finalists)),
        )
        raw = strip_fence(raw_text.strip())
        results = json.loads(raw)
        if not isinstance(results, list) or len(results) < len(finalists):
            raise ValueError("incomplete batch response")

        out = []
        for i, (symbol, ctx, conf, direction, _score, _reason) in enumerate(finalists):
            r = results[i] if i < len(results) else {}
            if r.get("setup_score", 0) < min_score:
                continue
            r["_symbol"] = symbol
            out.append(r)
        print(f"[scanner] batch scored {len(finalists)} symbols → {len(out)} setups", flush=True)
        return out
    except Exception as e:
        print(f"[scanner] batch call failed ({e}), falling back to individual calls", flush=True)
        # Fallback: individual calls in parallel
        out = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs = {
                ex.submit(
                    _ai_score, sym, ctx, conf, direction,
                    mkt_str, histories.get(sym, {"trades": 0}), rulebook_str, min_score, cr
                ): sym
                for sym, ctx, conf, direction, _, _ in finalists
            }
            for f in as_completed(fs):
                result = f.result()
                if result is not None:
                    out.append(result)
        return out


# ── Symbol history helper ───────────────────────────────────────────────────────

def _symbol_history(symbol: str, conn) -> dict:
    rows = conn.execute("""
        SELECT realized_pnl FROM positions
        WHERE symbol = ? ORDER BY close_time DESC LIMIT 20
    """, (symbol,)).fetchall()
    if not rows:
        return {"trades": 0}
    pnls = [r[0] for r in rows if r[0] is not None]
    wins = [p for p in pnls if p > 0]
    return {
        "trades":       len(rows),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":    round(sum(pnls), 2),
    }


# ── Background scan thread ─────────────────────────────────────────────────────

def _scan_thread(symbols: list, min_score: int = SCANNER_MIN_SCORE, criteria: dict = None):
    cr = criteria or CRITERIA_DEFAULTS
    t0 = time.time()
    _update(
        status="running", started_at=t0, error=None, setups=[], scanned=0, after_filter=0,
        min_score=min_score,
        stage=1, stage_label="Stage 1 — Confluence filter",
        stage_detail=f"Fetching multi-TF data for {len(symbols)} symbols…",
        stage_progress=0,
    )

    try:
        # Stage 1 — confluence filter (emits per-symbol progress internally)
        candidates = _stage1(symbols, min_score)
        passed1 = len(candidates)

        # Stage 2 — technical quality gate
        _update(
            stage=2, stage_label="Stage 2 — Quality gate",
            stage_detail=f"{passed1} symbols passed confluence → applying technical filters…",
            stage_progress=0,
        )
        finalists = _stage2(candidates, min_score, criteria=cr)
        _update(scanned=len(symbols), after_filter=len(finalists),
                stage_detail=f"{passed1} passed confluence · {len(finalists)} passed quality gate",
                stage_progress=100)

        if not finalists:
            _update(status="completed", completed_at=time.time(),
                    duration_sec=round(time.time() - t0, 1),
                    stage=0, stage_label="", stage_detail="No candidates passed the quality gate")
            return

        # Shared context for all finalists
        try:
            mkt_ctx = market_context.get_market_context(
                [s for s, _, _, _ in finalists[:5]]
            )
            mkt_str = market_context.format_for_prompt(mkt_ctx)
        except Exception as e:
            logger.warning("market context fetch failed in scan: %s", e)
            mkt_str = ""

        # Append BTC market regime
        try:
            regime = market_context.get_btc_regime()
            regime_map = {"bull": "📈 BTC is in a BULL regime (EMA50 > EMA200) — favour long setups",
                          "bear": "📉 BTC is in a BEAR regime (EMA50 < EMA200) — favour short setups",
                          "range": "↔ BTC is in a RANGE/transition — both directions valid, be selective"}
            mkt_str = (mkt_str + "\n" if mkt_str else "") + f"BTC MARKET REGIME: {regime_map[regime]}"
        except Exception as e:
            logger.warning("scoring failed: %s", e)
        with db_conn() as conn:
            rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)
            histories = {s: _symbol_history(s, conn) for s, _, _, _ in finalists}

        # Nansen smart money signals — one API call for all finalists combined
        nansen_signals = {}
        if nansen_client.is_configured():
            _update(stage_detail="Fetching Nansen smart money signals…")
            try:
                finalist_syms  = [s for s, _, _, _ in finalists]
                nansen_signals = nansen_client.get_signals_for_symbols(finalist_syms)
                active = sum(1 for v in nansen_signals.values() if v.get("ok"))
                print(f"[Nansen] {active}/{len(finalist_syms)} finalists have smart money signal", flush=True)
            except Exception as e:
                print(f"[Nansen] Signal fetch failed: {e}", flush=True)

        # Stage 3a — Quick score all finalists with Haiku (cheap pre-filter pass)
        _update(
            stage=3, stage_label="Stage 3a — Haiku quick-score",
            stage_detail=f"Fast-scoring {len(finalists)} finalist{'s' if len(finalists)!=1 else ''} with Haiku…",
            stage_progress=0,
        )
        shared_prefix = _build_shared_prefix(mkt_str, rulebook_str, min_score, criteria=cr)
        quick_results = []
        qs_done = [0]
        qs_total = len(finalists)
        with ThreadPoolExecutor(max_workers=10) as ex:
            fq = {
                ex.submit(_quick_score, sym, ctx, conf, dir_, shared_prefix, min_score): (sym, ctx, conf, dir_)
                for sym, ctx, conf, dir_ in finalists
            }
            for f in as_completed(fq):
                sym, ctx, conf, dir_ = fq[f]
                qs_done[0] += 1
                _update(
                    stage_detail  = f"Haiku scoring: {qs_done[0]} / {qs_total} symbols",
                    stage_progress= int(qs_done[0] / qs_total * 100),
                )
                r = f.result()
                if r is not None:
                    quick_results.append((sym, ctx, conf, dir_, r["score"], r.get("reason", "")))

        # Sort by quick score, take top N for expensive full-detail pass
        quick_results.sort(key=lambda x: -x[4])
        top_finalists  = quick_results[:SCANNER_FULL_DETAIL_TOP_N]
        rest_finalists = quick_results[SCANNER_FULL_DETAIL_TOP_N:]

        # Stage 3b — Full detail with Sonnet: single batched call for all top-N
        _update(
            stage_label   = "Stage 3b — Sonnet full analysis",
            stage_detail  = f"Batch-scoring top {len(top_finalists)} setup{'s' if len(top_finalists)!=1 else ''} with Sonnet…",
            stage_progress= 0,
        )
        setups = _batch_ai_score(top_finalists, mkt_str, histories, rulebook_str,
                                  min_score, criteria=cr, nansen_signals=nansen_signals)
        _update(stage_progress=100)

        # Add non-top-N setups with Haiku score + one-sentence rationale
        for sym, ctx, conf, direction, score, reason in rest_finalists:
            inds = ctx.get("4H", {}).get("indicators", {})
            price = inds.get("ema", {}).get("current_price")
            setups.append({
                "symbol":            sym,
                "direction":         direction,
                "setup_score":       score,
                "setup_label":       "Quick score only",
                "why_this_score":    reason or "No rationale (Haiku quick-score pass)",
                "quick_score_only":  True,
                "confluence":        conf.get("label", ""),
                "current_price":     price,
            })

        # Attach Nansen smart money signal to each setup
        for setup in setups:
            sym = setup.get("_symbol") or setup.get("symbol", "")
            ns  = nansen_signals.get(sym, {})
            if ns.get("ok"):
                setup["nansen"] = {
                    "direction":   ns["direction"],
                    "strength":    ns["strength"],
                    "netflow_usd": ns["netflow_usd"],
                    "nof_traders": ns["nof_traders"],
                    "chain":       ns.get("chain", ""),
                }

        setups.sort(key=lambda x: -x.get("setup_score", 0))
        _update(
            status="completed", setups=setups,
            completed_at=time.time(), duration_sec=round(time.time() - t0, 1),
            stage=0, stage_label="",
            stage_detail=f"{len(setups)} setup{'s' if len(setups)!=1 else ''} found in {round(time.time()-t0,1)}s",
        )

    except Exception as e:
        _update(status="error", error=str(e),
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """
    Start a background scan. Returns False if already running or results are still
    fresh (< SCANNER_CACHE_TTL seconds old) AND the min_score hasn't changed.
    """
    with _state_lock:
        if _state["status"] == "running":
            return False
        completed_at   = _state.get("completed_at")
        score_unchanged = _state.get("min_score", SCANNER_MIN_SCORE) == min_score
        if completed_at and (time.time() - completed_at) < SCANNER_CACHE_TTL and score_unchanged:
            return False  # still fresh with same threshold

    syms = symbols or DEFAULT_WATCHLIST
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True


def force_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """Start a scan regardless of cache TTL. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    syms = symbols or DEFAULT_WATCHLIST
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True
