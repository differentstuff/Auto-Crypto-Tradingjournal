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
        telegram_notify.send_setup_alert(setups)
        print(f"[Scanner Scheduler] Telegram alert sent ({len(setups)} setups)")
        _persist_setups(setups)


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
