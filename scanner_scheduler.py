"""
scanner_scheduler.py — Runs the Setup Scanner every 30 minutes.

Starts automatically when the app starts (if Telegram is configured).
Sends a Telegram alert if any 6+/10 setups are found.

Timeline:
  App start → wait 5 min → first scan → wait 30 min → scan → repeat

Only starts if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env.
To disable without removing the env vars, set SCANNER_SCHEDULER=off in .env.
"""

import os
import threading
import time

import ai_scanner
import telegram_notify

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
