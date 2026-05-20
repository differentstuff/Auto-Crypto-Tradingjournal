"""
monitor_scheduler.py — Background thread that monitors open positions every 10 min.

For each open position that passes the filter (unrealized_pct < MONITOR_THRESHOLD_PCT
or duration_minutes > MONITOR_THRESHOLD_DURATION), runs the TradeMonitor agent chain:
  DataCollector → DataInterpreter → MarketSentiment → Haiku verdict

On risk_rating >= 7 or action != "Hold":
  - Sets monitor_alert=1 in analyzed_calls for UI badge
  - Sends Telegram alert
"""
import os
import threading
import time

import bitget_client
import telegram_notify
import agent_orchestrator
from constants import MONITOR_INTERVAL, MONITOR_THRESHOLD_PCT, MONITOR_THRESHOLD_DURATION
from database import db_conn

FIRST_DELAY = int(os.environ.get("MONITOR_FIRST_DELAY", "120"))   # 2 min


def _passes_filter(position: dict) -> bool:
    try:
        unrl = float(position.get("unrealized_pct", 0) or 0)
        dur  = float(position.get("duration_minutes", 0) or 0)
        return unrl < MONITOR_THRESHOLD_PCT or dur > MONITOR_THRESHOLD_DURATION
    except (TypeError, ValueError):
        return False


def _get_original_prep(conn, symbol: str) -> dict:
    try:
        row = conn.execute(
            """SELECT analysis_json FROM analyzed_calls
               WHERE symbol=? AND status IN ('matched','saved')
               ORDER BY created_at DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row and row["analysis_json"]:
            import json
            d = json.loads(row["analysis_json"])
            return {
                "sl_price":  d.get("sl_price"),
                "tp1_price": d.get("tp1_price") or d.get("tp1") or (d.get("risk_reward", {}) or {}).get("tp1"),
            }
    except Exception:
        pass
    return {}


def _run_once():
    try:
        positions = bitget_client.get_open_positions() or []
    except Exception as e:
        print(f"[Monitor] Failed to fetch positions: {e}", flush=True)
        return

    to_check = [p for p in positions if _passes_filter(p)]
    if not to_check:
        return

    print(f"[Monitor] Checking {len(to_check)}/{len(positions)} positions", flush=True)

    for pos in to_check:
        symbol = pos.get("symbol", "?")
        try:
            with db_conn() as conn:
                original_prep = _get_original_prep(conn, symbol)

            result = agent_orchestrator.run_monitor(pos, original_prep)

            should_alert = (result["risk_rating"] >= 7 or result["action"] != "Hold")

            if should_alert:
                with db_conn() as conn:
                    conn.execute(
                        """UPDATE analyzed_calls SET monitor_alert=1
                           WHERE symbol=? AND status IN ('matched','saved')""",
                        (symbol,),
                    )
                    conn.commit()
                _send_monitor_alert(pos, result)

            print(f"[Monitor] {symbol}: {result['action']} "
                  f"(risk {result['risk_rating']}/10)"
                  f"{' ⚠ ALERTED' if should_alert else ''}", flush=True)

        except Exception as e:
            print(f"[Monitor] Error for {symbol}: {e}", flush=True)


def _monitor_alerts_enabled() -> bool:
    """Check whether position monitor Telegram alerts are enabled (default on)."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='telegram_monitor_enabled'"
            ).fetchone()
        return (row is None) or (row[0] == '1')
    except Exception:
        return True


def _send_monitor_alert(position: dict, result: dict):
    if not _monitor_alerts_enabled():
        symbol = position.get("symbol", "?")
        print(f"[Monitor] Monitor alerts disabled — skipped alert for {symbol}", flush=True)
        return

    symbol  = position.get("symbol", "?")
    unrl    = float(position.get("unrealized_pct", 0) or 0)
    action  = result["action"]
    rating  = result["risk_rating"]
    reason  = result["action_reason"]
    summary = result["summary"]
    emoji   = "🔴" if rating >= 8 else "🟡" if rating >= 6 else "🟢"

    msg = (
        f"{emoji} *Monitor Alert — {symbol}*\n"
        f"Action: `{action}` (Risk {rating}/10)\n"
        f"Reason: {reason}\n\n"
        f"{summary}"
    )
    try:
        telegram_notify.send_message(msg)
    except Exception as e:
        print(f"[Monitor] Telegram alert failed: {e}", flush=True)


def start():
    def _loop():
        import journal_paused
        time.sleep(FIRST_DELAY)
        while True:
            try:
                if journal_paused.is_paused():
                    print("[Monitor] paused — skipping monitor cycle", flush=True)
                else:
                    _run_once()
            except Exception as e:
                print(f"[Monitor] Unexpected error in monitor loop: {e}", flush=True)
            time.sleep(MONITOR_INTERVAL)

    t = threading.Thread(target=_loop, name="monitor-scheduler", daemon=True)
    t.start()
    print(f"[Monitor] Background monitor started (every {MONITOR_INTERVAL}s, "
          f"first run in {FIRST_DELAY}s)", flush=True)
