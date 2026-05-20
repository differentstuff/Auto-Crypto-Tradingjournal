"""
scanner_scheduler.py — Runs the Setup Scanner every 30 minutes.

Starts automatically when the app starts (if Telegram is configured).
Sends a Telegram alert if any 6+/10 setups are found.

Timeline:
  App start → wait 5 min → first scan → wait 30 min → scan → repeat

Only starts if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env.
To disable without removing the env vars, set SCANNER_SCHEDULER=off in .env.
"""

import json
import os
import threading
import time

import ai_scanner
import telegram_notify
from database import db_conn

FIRST_DELAY      = int(os.environ.get("SCANNER_FIRST_DELAY", 300))   # 5 min
INTERVAL         = int(os.environ.get("SCANNER_INTERVAL",    1800))  # 30 min
SCAN_TIMEOUT     = 900                                                 # 15 min max — extended for 500-coin list
WATCHER_INTERVAL = 2700                                                # 45 minutes

# Broad criteria: relaxes Stage-2 hard filters so all archetypes (continuation,
# reversal, breakout) pass through to Stage 3. Archetype is auto-detected per
# symbol in Stage 3 and scored with the appropriate rubric.
# Only risk-quality gates are kept (ATR SL floor + R:R minimum).
SCHEDULER_CRITERIA = {
    "rsi":        True,   # RSI extreme still relevant for reversals
    "macd":       True,   # MACD momentum signal
    "wavetrend":  True,   # WaveTrend — primary trigger for reversals
    "volume":     True,   # Volume confirmation
    "funding":    True,   # Funding rate penalty
    "fear_greed": True,   # F&G adjustment
    "atr_sl":     True,   # Hard gate: SL must be > 1×ATR from entry
    "rr_minimum": True,   # Hard gate: R:R must be >= 2:1
    # Disabled: these are strategy-specific and would reject valid setups
    "ema_stack":  False,  # Reversal setups can be countertrend — EMA not required
    "adx":        False,  # Reversal setups have LOW ADX — gate would reject them
    "sr_anchor":  False,  # Breakout/news setups may not anchor to named S/R
}
SCHEDULER_MIN_SCORE = 1   # Show everything — user decides whether to enter


def _wait_for_scan() -> dict:
    """Block until the running scan finishes or times out."""
    deadline = time.time() + SCAN_TIMEOUT
    while time.time() < deadline:
        state = ai_scanner.get_state()
        if state["status"] in ("completed", "error"):
            return state
        time.sleep(10)
    return ai_scanner.get_state()


def _enrich_and_filter_setups(setups: list) -> list:
    """
    Before sending Telegram alerts:
    1. Check live price vs entry zone — drop setups where price moved >5% past entry.
    2. Generate annotated 4H chart for each remaining setup.
    Returns filtered+enriched list (may be shorter than input).
    """
    import agent_chart_draw
    from ccxt_client import get_live_price
    from chart_context import get_candles

    enriched = []
    for s in setups:
        sym       = s.get("_symbol") or s.get("symbol", "")
        direction = (s.get("direction") or "Long").lower()
        ez        = s.get("entry_zone") or {}
        entry_ref = ez.get("high") or ez.get("low") or s.get("entry_price") or 0

        # ── Price freshness ────────────────────────────────────────────────
        # Guard 1: entry_ref=0 means no entry zone at all — drop, don't pass through
        if not entry_ref:
            print(f"[Scanner Scheduler] {sym} has no entry zone — skipping")
            continue

        try:
            live = get_live_price(sym)
            if live and entry_ref:
                # Guard 2: absolute distance from current price (catches historical
                # support levels far from current price — e.g. entry $0.146 when
                # current price is $0.24, which the directional drift alone may miss
                # if the exception path is taken).
                abs_pct_from_current = abs(live - entry_ref) / live * 100
                if abs_pct_from_current > 20.0:
                    print(f"[Scanner Scheduler] {sym} entry {entry_ref} is "
                          f"{abs_pct_from_current:.1f}% from current price {live:.6g} "
                          f"— skipping unreachable setup")
                    continue

                # Guard 3: directional drift — price moved past entry zone
                # For Long: positive drift = price moved above entry (missed move)
                # For Short: positive drift = price dropped below entry (missed move)
                if direction == "long":
                    drift = (live - entry_ref) / entry_ref * 100
                else:
                    drift = (entry_ref - live) / entry_ref * 100

                s["_live_price"]      = live
                s["_price_drift_pct"] = round(drift, 1)

                if drift > 5.0:
                    print(f"[Scanner Scheduler] {sym} price moved {drift:.1f}% from entry — skipping stale setup")
                    continue   # Drop from alert — entry is gone
                elif drift > 2.0:
                    s["_price_warning"] = f"Price moved {drift:.1f}% from entry zone — act fast or wait for pullback"
        except Exception as e:
            # If we can't verify price proximity, drop the setup — don't risk
            # sending a stale alert just because the price check failed.
            print(f"[Scanner Scheduler] Price check failed for {sym}: {e} — skipping")
            continue

        # ── Chart generation ──────────────────────────────────────────────
        try:
            from chart_sr import detect_support_resistance
            candles = get_candles(sym, "4H")
            if candles is not None and not candles.empty:
                ez = s.get("entry_zone") or {}
                sr_raw = detect_support_resistance(candles)
                chart_b64 = agent_chart_draw.draw(
                    candles     = candles,
                    symbol      = sym,
                    direction   = direction,
                    entry       = ez.get("low") or entry_ref,
                    entry_high  = ez.get("high") or None,
                    sl          = s.get("sl_price"),
                    tp1         = s.get("tp1_price"),
                    tp2         = s.get("tp2_price"),
                    criteria    = s.get("key_conditions") or [],
                    sr_levels   = sr_raw,
                )
                if chart_b64:
                    s["chart_png_b64"] = chart_b64
        except Exception as e:
            print(f"[Scanner Scheduler] Chart failed for {sym}: {e}")

        enriched.append(s)

    if len(enriched) < len(setups):
        print(f"[Scanner Scheduler] {len(setups) - len(enriched)} setup(s) dropped (price moved past entry)")
    return enriched


def _on_scan_complete(setups: list):
    """
    Completion hook registered with ai_scanner — fires for EVERY scan finish,
    whether triggered by the scheduler or manually via the API.
    """
    if not setups:
        return

    _persist_setups(setups)

    try:
        import entry_watcher
        entry_watcher.process_scan_results(setups)
    except Exception as ew_err:
        print(f"[Scanner Scheduler] Entry watcher error: {ew_err}")

    with db_conn() as conn:
        tg_enabled = conn.execute(
            "SELECT value FROM settings WHERE key='telegram_alerts_enabled'"
        ).fetchone()
    if tg_enabled is None or tg_enabled[0] == '1':
        alert_setups = _enrich_and_filter_setups(setups)
        if alert_setups:
            telegram_notify.send_setup_alert(alert_setups)
            print(f"[Scanner Scheduler] Telegram alert sent ({len(alert_setups)} setups)")
        else:
            print(f"[Scanner Scheduler] All {len(setups)} setup(s) stale (price moved) — no alert sent")
    else:
        print(f"[Scanner Scheduler] Telegram alerts disabled — skipped ({len(setups)} setups)")


def _run_once():
    started = ai_scanner.force_scan(min_score=SCHEDULER_MIN_SCORE, criteria=SCHEDULER_CRITERIA)
    if not started:
        print("[Scanner Scheduler] Scan already running — skipping cycle")
        return

    state   = _wait_for_scan()
    scanned = state.get("scanned", 0)
    filt    = state.get("after_filter", 0)
    dur     = state.get("duration_sec", "?")
    err     = state.get("error")

    if err:
        print(f"[Scanner Scheduler] Scan error: {err}")
        return

    print(f"[Scanner Scheduler] Done — {scanned} symbols, {filt} finalists ({dur}s)")


def _persist_setups(setups: list):
    """
    Save scanner-alerted setups to analyzed_calls (status='saved', analyst='scanner').

    This allows check-matches to auto-link open positions to the scanner signal
    that triggered them, without requiring manual call analysis.
    Only saves setups with a full AI analysis (setup_score > 0, has sl/tp prices).
    Skips symbols already saved within the last 4 hours to avoid duplicate entries.
    """
    if not setups:
        return
    saved = 0
    try:
        with db_conn() as conn:
            for s in setups:
                sym       = (s.get("symbol") or s.get("_symbol") or "").upper()
                direction = s.get("direction", "Long")
                score     = s.get("setup_score", 0)
                if not sym or not score:
                    continue  # quick-score-only setups have insufficient detail

                # Skip if a scanner call for this symbol was saved recently
                recent = conn.execute(
                    "SELECT id FROM analyzed_calls "
                    "WHERE symbol=? AND direction=? AND analyst='scanner' "
                    "AND created_at >= datetime('now', '-4 hours')",
                    (sym, direction)
                ).fetchone()
                if recent:
                    continue

                entry_zone = s.get("entry_zone") or {}
                entry_price = (
                    entry_zone.get("low") or
                    entry_zone.get("high") or
                    s.get("current_price") or None
                )
                sl_price  = s.get("sl_price")  or None
                tp1_price = s.get("tp1_price") or None
                tp2_price = s.get("tp2_price") or None
                rr_ratio  = s.get("rr_ratio")  or None
                trade_type = (
                    s.get("chart_pattern") or
                    s.get("trade_type") or
                    "Scanner Signal"
                )
                call_text = (
                    f"[Scanner] {sym} {direction} — {s.get('setup_label','?')} "
                    f"({score}/10). {s.get('summary','')}"
                )
                conn.execute("""
                    INSERT INTO analyzed_calls
                      (symbol, direction, call_text, entry_price,
                       sl_price, tp1_price, tp2_price,
                       setup_score, setup_label, rr_ratio, trade_type,
                       analysis_json, analyst, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'saved')
                """, (
                    sym, direction, call_text, entry_price,
                    sl_price, tp1_price, tp2_price,
                    score, s.get("setup_label"), rr_ratio, trade_type,
                    json.dumps(s), "scanner",
                ))
                saved += 1
            conn.commit()
        if saved:
            print(f"[Scanner Scheduler] Saved {saved} setup(s) to analyzed_calls", flush=True)
    except Exception as exc:
        print(f"[Scanner Scheduler] Failed to persist setups: {exc}", flush=True)


def _loop():
    print(f"[Scanner Scheduler] First scan in {FIRST_DELAY // 60} min, "
          f"then every {INTERVAL // 60} min")
    time.sleep(FIRST_DELAY)
    last_watcher_review = 0.0
    import journal_paused
    while True:
        try:
            if journal_paused.is_paused():
                print("[Scanner Scheduler] paused — skipping scan cycle")
            else:
                _run_once()
        except Exception as e:
            print(f"[Scanner Scheduler] Unhandled error: {e}")
        # Run watcher review every 45 min independently of scan cycle
        if time.time() - last_watcher_review >= WATCHER_INTERVAL:
            try:
                if not journal_paused.is_paused():
                    import entry_watcher
                    entry_watcher.run_review_cycle()
                last_watcher_review = time.time()
            except Exception as e:
                print(f"[Scanner Scheduler] Watcher review error: {e}")
        time.sleep(INTERVAL)


def start():
    """Start the scheduler if Telegram is configured and not explicitly disabled."""
    if os.environ.get("SCANNER_SCHEDULER", "").lower() == "off":
        print("[Scanner Scheduler] Disabled via SCANNER_SCHEDULER=off")
        return
    if not telegram_notify.is_configured():
        print("[Scanner Scheduler] Telegram not configured — "
              "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable alerts")
        return
    # Register completion hook so manual scans also trigger TG + entry_watcher
    ai_scanner.register_completion_hook(_on_scan_complete)
    t = threading.Thread(target=_loop, daemon=True, name="scanner-scheduler")
    t.start()
