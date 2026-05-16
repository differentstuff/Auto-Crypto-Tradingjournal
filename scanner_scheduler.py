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

FIRST_DELAY   = int(os.environ.get("SCANNER_FIRST_DELAY", 300))   # 5 min
INTERVAL      = int(os.environ.get("SCANNER_INTERVAL",    1800))  # 30 min
SCAN_TIMEOUT  = 420                                                 # 7 min max per scan


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
    from ccxt_client import get_binance_price
    from chart_context import get_candles

    enriched = []
    for s in setups:
        sym       = s.get("_symbol") or s.get("symbol", "")
        direction = (s.get("direction") or "Long").lower()
        ez        = s.get("entry_zone") or {}
        entry_ref = ez.get("high") or ez.get("low") or s.get("entry_price") or 0

        # ── Price freshness ────────────────────────────────────────────────
        try:
            live = get_binance_price(sym)
            if live and entry_ref:
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
            print(f"[Scanner Scheduler] Price check failed for {sym}: {e}")

        # ── Chart generation ──────────────────────────────────────────────
        try:
            candles = get_candles(sym, "4H")
            if candles is not None and not candles.empty:
                chart_b64 = agent_chart_draw.draw(
                    candles   = candles,
                    symbol    = sym,
                    direction = direction,
                    entry     = entry_ref,
                    sl        = s.get("sl_price"),
                    tp1       = s.get("tp1_price"),
                    tp2       = s.get("tp2_price"),
                )
                if chart_b64:
                    s["chart_png_b64"] = chart_b64
        except Exception as e:
            print(f"[Scanner Scheduler] Chart failed for {sym}: {e}")

        enriched.append(s)

    if len(enriched) < len(setups):
        print(f"[Scanner Scheduler] {len(setups) - len(enriched)} setup(s) dropped (price moved past entry)")
    return enriched


def _run_once():
    started = ai_scanner.force_scan()
    if not started:
        print("[Scanner Scheduler] Scan already running — skipping cycle")
        return

    state   = _wait_for_scan()
    setups  = state.get("setups") or []
    scanned = state.get("scanned", 0)
    filt    = state.get("after_filter", 0)
    dur     = state.get("duration_sec", "?")
    err     = state.get("error")

    if err:
        print(f"[Scanner Scheduler] Scan error: {err}")
        return

    print(f"[Scanner Scheduler] Done — {scanned} symbols, "
          f"{filt} finalists, {len(setups)} setups ({dur}s)")

    if setups:
        _persist_setups(setups)
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
    while True:
        try:
            _run_once()
        except Exception as e:
            print(f"[Scanner Scheduler] Unhandled error: {e}")
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
    t = threading.Thread(target=_loop, daemon=True, name="scanner-scheduler")
    t.start()
