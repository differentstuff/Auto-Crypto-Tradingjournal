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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from database import db_conn
from helpers import strip_fence, build_cached_messages
import chart_context
import market_context
import ai_rulebook

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL      = "claude-sonnet-4-6"          # full detail pass
FAST_MODEL = "claude-haiku-4-5-20251001"  # quick score pass
MIN_SCORE  = 6
FULL_DETAIL_TOP_N = 12   # max symbols that get the expensive full-detail prompt
CACHE_TTL  = 1800  # 30 min between scans

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

# ── Scan state ─────────────────────────────────────────────────────────────────

_state: dict = {
    "status":        "idle",   # idle | running | completed | error
    "started_at":    None,
    "completed_at":  None,
    "duration_sec":  None,
    "setups":        [],
    "scanned":       0,
    "after_filter":  0,
    "error":         None,
    "min_score":     MIN_SCORE,
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
    except Exception:
        return symbol, None, None


def _stage1(symbols: list, min_score: int = MIN_SCORE) -> list:
    """Return [(symbol, ctx, conf, direction)] with enough aligned signals.
    For low min_score (≤ 3) only 1 aligned signal is required."""
    threshold = 1 if min_score <= 3 else 2
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            symbol, ctx, conf = f.result()
            if ctx is None or conf is None:
                continue
            if conf["bullish"] >= threshold:
                out.append((symbol, ctx, conf, "Long"))
            elif conf["bearish"] >= threshold:
                out.append((symbol, ctx, conf, "Short"))
    return out


# ── Stage 2: technical quality gate ────────────────────────────────────────────

def _stage2(candidates: list, min_score: int = MIN_SCORE) -> list:
    """
    Technical quality gate — no AI, no network calls.
    Filters to symbols with a genuine structural entry opportunity.
    Skipped entirely when min_score ≤ 4 (user wants AI to be the sole judge).
    Caps output at 30 to control Claude API cost.
    """
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
        if direction == "Long"  and rsi_val > 78:
            continue
        if direction == "Short" and rsi_val < 22:
            continue

        # Reject: no trend structure (choppy / flat)
        if adx_val < 15:
            continue

        # Reject: no S/R structure to define entry/SL/TP
        if len(sr) < 2:
            continue

        # Reject: price too far from any actionable S/R level
        if price and atr_val and sr:
            distances = [abs(l["price"] - price) for l in sr]
            if min(distances) > atr_val * 4:
                continue

        # Require at least 2 aligned signals specifically on 4H
        bull_4h = bear_4h = 0
        if rsi_val > 55:   bull_4h += 1
        elif rsi_val < 45: bear_4h += 1
        if macd.get("trend") == "bullish":   bull_4h += 1
        elif macd.get("trend") == "bearish": bear_4h += 1
        if "bullish" in ema.get("alignment", ""):   bull_4h += 1
        elif "bearish" in ema.get("alignment", ""): bear_4h += 1
        if "bullish" in adx_d.get("direction", ""):   bull_4h += 1
        elif "bearish" in adx_d.get("direction", ""): bear_4h += 1

        if direction == "Long"  and bull_4h < 2:
            continue
        if direction == "Short" and bear_4h < 2:
            continue

        out.append((symbol, ctx, conf, direction))

    # Cap to avoid excessive Claude API calls; sort by total confluence first
    out.sort(key=lambda x: -x[2].get("score", 0))
    return out[:30]


# ── Stage 3: AI scoring ─────────────────────────────────────────────────────────

def _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str, min_score=MIN_SCORE):
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
        f"{conf['label']} ({conf['score']:+d}/{conf['max']} — "
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
SCORING SCALE:
6 — Moderate: partial alignment, valid entry zone, but weak R:R or limited confluence
7 — Good: clear directional bias, structural entry, R:R ≥ 2:1, no major red flags
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 2.5:1
9 — Excellent: all 8-criteria + strong ADX, multi-TF alignment, no rulebook conflicts
10 — Perfect: textbook chart pattern, volume confirmation, ideal entry timing, R:R ≥ 4:1

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


def _build_shared_prefix(mkt_str: str, rulebook_str: str, min_score: int = MIN_SCORE) -> str:
    """Shared context block — identical for all finalists in a scan, so it caches across parallel calls."""
    mkt_block = f"MARKET CONTEXT:\n{mkt_str}\n\n" if mkt_str else ""
    rb_block  = f"TRADER RULEBOOK:\n{rulebook_str}\n\n" if rulebook_str else ""
    return (
        f"{mkt_block}{rb_block}"
        "SCORING SCALE:\n"
        "6=Moderate, 7=Good(R:R≥2:1), 8=Strong(≥3 signals,R:R≥2.5:1), "
        "9=Excellent(multi-TF+no conflicts), 10=Perfect(textbook+volume+R:R≥4:1)\n"
        f"Score <{min_score} if: no structural entry, SL inside ATR noise, or R:R below 1.5:1."
    )


def _quick_score(symbol: str, ctx: dict, conf: dict, direction: str,
                 shared_prefix: str, min_score: int = MIN_SCORE) -> dict | None:
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
        f"Score this {direction.upper()} setup for {symbol} — return score 0-10.\n\n"
        f"{pt_4h}\n{pt_1d}\n"
        f"Confluence: {conf_line}\nS/R: {sr_compact}\n\n"
        f'If score < {min_score}: {{"score":0}}\n'
        f'If score >= {min_score}: {{"score":7,"direction":"{direction}"}}\n'
        "Respond with ONLY valid JSON — no extras."
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=FAST_MODEL, max_tokens=50,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": shared_prefix},
                {"type": "text", "text": variable},
            ]}]
        )
        r = json.loads(strip_fence(msg.content[0].text.strip()))
        if r.get("score", 0) < min_score:
            return None
        return {"score": r["score"], "direction": r.get("direction", direction),
                "_input_tokens": msg.usage.input_tokens, "_output_tokens": msg.usage.output_tokens}
    except Exception:
        return None


def _ai_score(symbol, ctx, conf, direction, mkt_str, history, rulebook_str, min_score=MIN_SCORE):
    try:
        shared  = _build_shared_prefix(mkt_str, rulebook_str, min_score)
        prompt  = _build_prompt(symbol, ctx, conf, direction, "", history, rulebook_str, min_score)
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL, max_tokens=1200,
            messages=build_cached_messages(shared, prompt),
        )
        result = json.loads(strip_fence(message.content[0].text.strip()))
        if result.get("setup_score", 0) < min_score:
            return None
        result["_symbol"]        = symbol
        result["_input_tokens"]  = message.usage.input_tokens
        result["_output_tokens"] = message.usage.output_tokens
        return result
    except Exception:
        return None


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

def _scan_thread(symbols: list, min_score: int = MIN_SCORE):
    t0 = time.time()
    _update(status="running", started_at=t0, error=None, setups=[], scanned=0, after_filter=0,
            min_score=min_score)

    try:
        # Stage 1 — confluence filter
        candidates = _stage1(symbols, min_score)

        # Stage 2 — technical quality gate
        finalists = _stage2(candidates, min_score)
        _update(scanned=len(symbols), after_filter=len(finalists))

        if not finalists:
            _update(status="completed", completed_at=time.time(),
                    duration_sec=round(time.time() - t0, 1))
            return

        # Shared context for all finalists
        try:
            mkt_ctx = market_context.get_market_context(
                [s for s, _, _, _ in finalists[:5]]
            )
            mkt_str = market_context.format_for_prompt(mkt_ctx)
        except Exception:
            mkt_str = ""

        with db_conn() as conn:
            rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)
            histories = {s: _symbol_history(s, conn) for s, _, _, _ in finalists}

        # Stage 3a — Quick score all finalists with Haiku (cheap pass)
        # Shared prefix is identical for all → prompt cache hits after first call
        shared_prefix = _build_shared_prefix(mkt_str, rulebook_str, min_score)
        quick_results = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            fq = {
                ex.submit(_quick_score, sym, ctx, conf, dir_, shared_prefix, min_score): (sym, ctx, conf, dir_)
                for sym, ctx, conf, dir_ in finalists
            }
            for f in as_completed(fq):
                sym, ctx, conf, dir_ = fq[f]
                r = f.result()
                if r is not None:
                    quick_results.append((sym, ctx, conf, dir_, r["score"]))

        # Sort by quick score, take top N for expensive full-detail pass
        quick_results.sort(key=lambda x: -x[4])
        top_finalists = quick_results[:FULL_DETAIL_TOP_N]

        # Stage 3b — Full detail with Sonnet on top-N only
        setups = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs = {
                ex.submit(
                    _ai_score, sym, ctx, conf, direction,
                    mkt_str, histories.get(sym, {"trades": 0}), rulebook_str, min_score
                ): sym
                for sym, ctx, conf, direction, _ in top_finalists
            }
            for f in as_completed(fs):
                result = f.result()
                if result is not None:
                    setups.append(result)

        setups.sort(key=lambda x: -x.get("setup_score", 0))
        _update(status="completed", setups=setups,
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))

    except Exception as e:
        _update(status="error", error=str(e),
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scan(symbols: list = None, min_score: int = MIN_SCORE) -> bool:
    """
    Start a background scan. Returns False if already running or results are still
    fresh (< CACHE_TTL seconds old) AND the min_score hasn't changed.
    """
    with _state_lock:
        if _state["status"] == "running":
            return False
        completed_at = _state.get("completed_at")
        score_unchanged = _state.get("min_score", MIN_SCORE) == min_score
        if completed_at and (time.time() - completed_at) < CACHE_TTL and score_unchanged:
            return False  # still fresh with same threshold

    syms = symbols or DEFAULT_WATCHLIST
    t = threading.Thread(target=_scan_thread, args=(syms, min_score), daemon=True)
    t.start()
    return True


def force_scan(symbols: list = None, min_score: int = MIN_SCORE) -> bool:
    """Start a scan regardless of cache TTL. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    syms = symbols or DEFAULT_WATCHLIST
    t = threading.Thread(target=_scan_thread, args=(syms, min_score), daemon=True)
    t.start()
    return True
