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
from helpers import strip_fence
import chart_context
import market_context
import ai_rulebook

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL     = "claude-sonnet-4-6"
MIN_SCORE = 6
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
    """Return [(symbol, ctx, conf, direction)] with ≥ 2 signals aligned."""
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            symbol, ctx, conf = f.result()
            if ctx is None or conf is None:
                continue
            if conf["bullish"] >= 2:
                out.append((symbol, ctx, conf, "Long"))
            elif conf["bearish"] >= 2:
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

        # Reject: severely overextended (leave some room for momentum entries)
        if direction == "Long"  and rsi_val > 82:
            continue
        if direction == "Short" and rsi_val < 18:
            continue

        # Reject: completely flat / zero volatility
        if adx_val < 10:
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

REQUIREMENTS for any score ≥ 6:
- A specific entry zone (a structural level — S/R, EMA, trendline — not random)
- A stop loss placed beyond the nearest structural level and ≥ 1× ATR from entry
- At least one take-profit level at the next significant resistance/support
- A clear one-sentence rationale for EACH of the three levels above

If the setup scores below 6 (no valid entry, stop would be inside noise, no logical TP):
{{"setup_score": 0, "reason": "one sentence why this doesn't qualify"}}

Otherwise respond with this exact structure:
{{"setup_score": 8, "setup_label": "Strong",
  "direction": "{direction}",
  "entry_zone": {{"low": 0.0, "high": 0.0,
    "rationale": "Why exactly this zone — reference the structural level"}},
  "sl_price": 0.0,
  "sl_rationale": "What structural level this is beyond and ATR distance",
  "tp1_price": 0.0,
  "tp1_rationale": "What resistance/support this targets",
  "tp2_price": 0.0,
  "tp2_rationale": "What resistance/support this targets",
  "rr_ratio": "1:X.X",
  "chart_pattern": "Bull Flag / Break-and-Retest / W-bottom / etc. — or null",
  "key_conditions": ["most important signal 1", "signal 2", "signal 3"],
  "risks": ["main risk 1", "risk 2"],
  "urgency": "Now|1-4h|Today|1-3 days",
  "timeframe": "4H",
  "confluence_summary": "One sentence describing the overall technical picture",
  "summary": "2-3 sentence honest assessment referencing actual numbers"}}

Respond with ONLY valid JSON — no markdown, no code fences."""


def _ai_score(symbol, ctx, conf, direction, mkt_str, history, rulebook_str):
    try:
        prompt  = _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str)
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL, max_tokens=1024,
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
