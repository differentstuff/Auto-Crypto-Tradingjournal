"""
ai_scanner.py — Proactive setup scanner.

Scans a watchlist of USDT-M futures for high-conviction 9-10/10 trade setups.

Three-stage pipeline:
  Stage 1 — Confluence filter (parallel, no AI):
             Computes multi-TF RSI/MACD/EMA/ADX signals for all symbols.
             Passes only symbols with ≥ 3/4 signals aligned in one direction.

  Stage 2 — Technical quality gate (no AI, instant):
             Rejects overextended RSI, absent S/R structure, low ADX chop,
             price not near any actionable level.

  Stage 3 — AI scoring (parallel Claude calls, finalists only):
             Claude evaluates each finalist with full technical + market context
             and trader rulebook. Returns only 9-10/10 setups with specific
             entry/SL/TP price levels.

Results cached for 30 minutes. Scan runs in a background thread so the
API endpoint returns immediately.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from database import db_conn
from helpers import strip_fence
import chart_context
import market_context
import ai_rulebook

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL     = "claude-sonnet-4-6"
MIN_SCORE = 9
CACHE_TTL = 1800  # 30 min between scans

# ── Watchlist ──────────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST = [
    # Major
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    # L1 alts
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT",
    "SUIUSDT", "APTUSDT", "INJUSDT", "SEIUSDT", "ICPUSDT",
    # L2 / ETH ecosystem
    "MATICUSDT", "ARBUSDT", "OPUSDT", "STRKUSDT",
    # DeFi
    "UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT",
    # AI / Infra
    "FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "GRTUSDT",
    # Meme
    "DOGEUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "BOMEUSDT", "FLOKIUSDT",
    # Other liquid
    "LTCUSDT", "BCHUSDT", "FILUSDT", "SANDUSDT", "AXSUSDT",
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


def _stage1(symbols: list) -> list:
    """Return [(symbol, ctx, conf, direction)] with ≥ 3/4 aligned signals."""
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            symbol, ctx, conf = f.result()
            if ctx is None or conf is None:
                continue
            if conf["bullish"] >= 3:
                out.append((symbol, ctx, conf, "Long"))
            elif conf["bearish"] >= 3:
                out.append((symbol, ctx, conf, "Short"))
    return out


# ── Stage 2: technical quality gate ────────────────────────────────────────────

def _stage2(candidates: list) -> list:
    """Quick checks — no AI, no network calls. Returns filtered list."""
    out = []
    for symbol, ctx, conf, direction in candidates:
        inds = ctx.get("4H", {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_val = inds.get("rsi", {}).get("value", 50)
        adx_val = (inds.get("adx", {}) or {}).get("value", 0)
        sr      = inds.get("support_resistance", [])
        ema     = inds.get("ema", {}) or {}
        price   = ema.get("current_price")
        atr_val = (inds.get("atr", {}) or {}).get("value", 0)

        # Reject: already deeply overextended in signal direction
        if direction == "Long"  and rsi_val > 76:
            continue
        if direction == "Short" and rsi_val < 24:
            continue

        # Reject: completely directionless (ADX too low)
        if adx_val < 14:
            continue

        # Reject: no S/R structure to define entry/SL/TP
        if len(sr) < 2:
            continue

        # Reject: if we have price + ATR, skip if price is so far from all
        # S/R levels that a structural entry is implausible
        if price and atr_val and sr:
            distances = [abs(l["price"] - price) for l in sr]
            min_dist  = min(distances)
            if min_dist > atr_val * 6:   # more than 6 ATR from nearest level
                continue

        out.append((symbol, ctx, conf, direction))
    return out


# ── Stage 3: AI scoring ─────────────────────────────────────────────────────────

def _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str):
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

    return f"""You are a professional crypto futures analyst. Evaluate whether {symbol} qualifies as a 9-10/10 {direction.upper()} setup right now or within the next 24 hours.

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
SCORING CRITERIA — all must be satisfied for 9/10:
1. Confluence: ≥ 3 aligned signals for the {direction} direction
2. Defined entry: price AT or approaching a structural level (S/R / trendline / EMA)
3. Clean stop loss: placed beyond the next structural level, at least 1.2× ATR from entry
4. R:R ≥ 2.5:1 to TP1, ≥ 4:1 to TP2
5. RSI not overextended at entry (not above 73 for Long / below 27 for Short)
6. No rulebook violation

For 10/10 additionally: textbook pattern (breakout, flag, W-bottom…), multi-TF alignment,
ideal entry timing (price just touched support / broke resistance with volume).

If this is NOT a 9-10/10 setup, respond with:
{{"setup_score": 0, "reason": "one sentence"}}

If it IS a 9-10/10 setup, respond with specific price levels:
{{"setup_score": 9, "setup_label": "Excellent", "direction": "{direction}",
  "entry_zone": {{"low": 0.0, "high": 0.0, "rationale": "why this zone"}},
  "sl_price": 0.0, "sl_rationale": "structural reason for this SL",
  "tp1_price": 0.0, "tp2_price": 0.0, "rr_ratio": "1:X.X",
  "chart_pattern": "e.g. Bull Flag / Break-and-Retest / W-bottom (or null)",
  "key_conditions": ["signal 1", "signal 2", "signal 3"],
  "risks": ["risk 1", "risk 2"],
  "urgency": "Now|1-4h|Today|1-3 days",
  "timeframe": "4H",
  "summary": "2-3 sentence honest assessment"}}

Respond with ONLY valid JSON — no markdown, no code fences."""


def _ai_score(symbol, ctx, conf, direction, mkt_str, history, rulebook_str):
    try:
        prompt  = _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str)
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL, max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        result = json.loads(strip_fence(message.content[0].text.strip()))
        if result.get("setup_score", 0) < MIN_SCORE:
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

def _scan_thread(symbols: list):
    t0 = time.time()
    _update(status="running", started_at=t0, error=None, setups=[], scanned=0, after_filter=0)

    try:
        # Stage 1 — confluence filter
        candidates = _stage1(symbols)

        # Stage 2 — technical quality gate
        finalists = _stage2(candidates)
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

        # Stage 3 — AI scoring in parallel
        setups = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs = {
                ex.submit(
                    _ai_score, sym, ctx, conf, direction,
                    mkt_str, histories.get(sym, {"trades": 0}), rulebook_str
                ): sym
                for sym, ctx, conf, direction in finalists
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

def start_scan(symbols: list = None) -> bool:
    """
    Start a background scan. Returns False if one is already running
    or if results are still fresh (< CACHE_TTL seconds old).
    """
    with _state_lock:
        if _state["status"] == "running":
            return False
        completed_at = _state.get("completed_at")
        if completed_at and (time.time() - completed_at) < CACHE_TTL:
            return False  # still fresh

    syms = symbols or DEFAULT_WATCHLIST
    t = threading.Thread(target=_scan_thread, args=(syms,), daemon=True)
    t.start()
    return True


def force_scan(symbols: list = None) -> bool:
    """Start a scan regardless of cache TTL. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    syms = symbols or DEFAULT_WATCHLIST
    t = threading.Thread(target=_scan_thread, args=(syms,), daemon=True)
    t.start()
    return True
