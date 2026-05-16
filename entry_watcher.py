"""
entry_watcher.py — Active limit/market recommendation queue with invalidation monitoring.

Maintains up to 5 active trade recommendations (limit or market entry).
Called by scanner_scheduler after each scan and every 45 minutes for review.
"""
import json
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MAX_ACTIVE = 5
LIMIT_EXPIRY_HOURS = 24   # Limits expire if not hit after 24h
MARKET_ENTRY_THRESHOLD = 0.015   # ±1.5% = "price is at entry zone"
RETRACE_RSI_THRESHOLD  = 70      # RSI above this = overbought, retrace likely (for longs)
RETRACE_RSI_THRESHOLD_SHORT = 30 # RSI below this = oversold, retrace likely (for shorts)


# ── Database helpers ──────────────────────────────────────────────────────────

def _get_active(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM entry_watcher_recs WHERE status='active' ORDER BY score DESC"
    ).fetchall()]


def _invalidate(conn, rec_id: int, reason: str):
    conn.execute(
        "UPDATE entry_watcher_recs SET status='invalidated', invalidation_reason=?, "
        "invalidated_at=datetime('now') WHERE id=?",
        (reason, rec_id)
    )


def _expire(conn, rec_id: int):
    conn.execute(
        "UPDATE entry_watcher_recs SET status='expired' WHERE id=?",
        (rec_id,)
    )


def _replace(conn, old_id: int, new_symbol: str):
    conn.execute(
        "UPDATE entry_watcher_recs SET status='replaced', replaced_by=? WHERE id=?",
        (new_symbol, old_id)
    )


# ── Price and indicator helpers ───────────────────────────────────────────────

def _get_price(symbol: str) -> float | None:
    try:
        from ccxt_client import get_live_price
        return get_live_price(symbol)
    except Exception:
        return None


def _get_indicators_1h(symbol: str) -> dict:
    """Fetch 1H indicators for invalidation checks. Returns {} on failure."""
    try:
        from chart_context import get_candles
        from chart_indicators import compute_all_indicators
        candles = get_candles(symbol, "1H")
        if candles is None or candles.empty:
            return {}
        return compute_all_indicators(candles) or {}
    except Exception:
        return {}


def _price_in_zone(price: float, entry_low: float, entry_high: float,
                   direction: str) -> bool:
    """True when price is within MARKET_ENTRY_THRESHOLD of the entry zone."""
    mid = (entry_low + entry_high) / 2 if entry_low and entry_high else (entry_low or entry_high)
    if not mid:
        return False
    drift = (price - mid) / mid
    # For Long: we want price at or just below mid (approaching from above or at zone)
    # For Short: price at or just above mid
    return abs(drift) <= MARKET_ENTRY_THRESHOLD


def _retrace_likely(symbol: str, direction: str, price: float,
                    entry_ref: float) -> bool:
    """
    When price has moved past entry zone, check if a pullback back to entry
    is structurally likely.
    Long: price above entry → retrace likely if RSI overbought or WaveTrend sell
    Short: price below entry → retrace likely if RSI oversold or WaveTrend buy
    """
    try:
        inds = _get_indicators_1h(symbol)
        if not inds.get("ok"):
            return False
        rsi = inds.get("rsi", {}).get("value", 50)
        wt  = inds.get("wavetrend", {}) or {}
        wt_signal = wt.get("signal", "")
        if direction.lower() == "long":
            return rsi > RETRACE_RSI_THRESHOLD or wt_signal == "sell"
        else:
            return rsi < RETRACE_RSI_THRESHOLD_SHORT or wt_signal in ("buy", "gold_buy")
    except Exception:
        return False


def _check_invalidation(rec: dict) -> tuple[bool, str]:
    """
    Check if an active limit recommendation should be cancelled BEFORE SL hit.
    Returns (should_invalidate, reason_string).

    Invalidation signals (1H timeframe):
    1. EMA stack flips against trade direction
    2. Price breaks the S/R level that justified the entry (on 1H close)
    3. WaveTrend shows opposing crossover signal
    4. Price within 1.5× ATR of SL (getting dangerously close without entering)
    """
    symbol    = rec["symbol"]
    direction = rec.get("direction", "Long").lower()
    sl_price  = rec.get("sl_price") or 0
    entry_ref = rec.get("entry_low") or rec.get("entry_high") or 0

    try:
        price = _get_price(symbol)
        if price is None:
            return False, ""

        # Early invalidation: price within 1.5× ATR of SL without having hit entry
        if sl_price and entry_ref:
            sl_distance = abs(entry_ref - sl_price)
            price_to_sl = abs(price - sl_price)
            if price_to_sl < sl_distance * 0.5:
                return True, f"Price {price:.5g} is within 50% of SL distance — invalidated before breach"

        inds = _get_indicators_1h(symbol)
        if not inds.get("ok"):
            return False, ""

        ema  = inds.get("ema", {}) or {}
        wt   = inds.get("wavetrend", {}) or {}
        wt_signal = wt.get("signal", "")
        ema_align = ema.get("alignment", "")
        ema_stack = ema.get("stack", "")

        if direction == "long":
            # EMA stack turned bearish
            if "bearish" in ema_align and "bearish" in ema_stack:
                return True, "1H EMA stack turned fully bearish — bullish entry invalidated"
            # WaveTrend sell signal (momentum reversing against trade)
            if wt_signal == "sell":
                return True, "1H WaveTrend sell signal — opposing momentum, cancel limit"
        else:
            if "bullish" in ema_align and "bullish" in ema_stack:
                return True, "1H EMA stack turned fully bullish — bearish entry invalidated"
            if wt_signal in ("buy", "gold_buy"):
                return True, "1H WaveTrend buy signal — opposing momentum, cancel limit"

        return False, ""
    except Exception as e:
        logger.warning("Invalidation check failed for %s: %s", symbol, e)
        return False, ""


# ── Core watcher logic ────────────────────────────────────────────────────────

def classify_and_add(setups: list, conn):
    """
    Called after each scanner run. For each setup:
    1. Classify as 'market' (price at zone) or 'limit' (price needs to move).
    2. If 'limit': check retrace likelihood — skip if "gone is gone".
    3. Compare with active queue — replace lowest-scoring if new is better.
    Returns list of (action, rec) tuples for Telegram notification.
    """
    from ccxt_client import get_live_price
    notifications = []

    for s in setups:
        sym    = s.get("_symbol") or s.get("symbol", "")
        dir_   = s.get("direction", "Long")
        score  = float(s.get("setup_score") or s.get("_final_score") or s.get("_quick_score") or 0)
        ez     = s.get("entry_zone") or {}
        el     = ez.get("low") or 0
        eh     = ez.get("high") or el
        sl     = s.get("sl_price") or 0
        tp1    = s.get("tp1_price") or 0
        tp2    = s.get("tp2_price") or 0
        arch   = s.get("chart_pattern") or ""
        rat    = s.get("why_this_score") or s.get("summary") or ""
        conds  = json.dumps(s.get("key_conditions") or [])

        if not sym or not el or not sl:
            continue

        # Skip if already active for this symbol+direction
        existing_syms = {r["symbol"] + r["direction"] for r in _get_active(conn)}
        if sym + dir_ in existing_syms:
            continue

        # Get live price
        price = get_live_price(sym)
        if price is None:
            continue

        # Classify alert type
        if _price_in_zone(price, el, eh, dir_):
            alert_type = "market"
            expires_at = None  # market entries don't expire
        else:
            # Check if price has moved past entry zone
            mid = (el + eh) / 2
            drift = (price - mid) / mid if mid else 0

            moved_past = (dir_.lower() == "long" and drift > 0.015) or \
                         (dir_.lower() == "short" and drift < -0.015)

            if moved_past:
                if _retrace_likely(sym, dir_, price, mid):
                    alert_type = "limit"  # Wait for pullback
                else:
                    logger.info("[Watcher] %s %s — price gone, retrace unlikely, skipping", sym, dir_)
                    continue
            else:
                alert_type = "limit"  # Price hasn't reached zone yet

            expires_at = (datetime.utcnow() + timedelta(hours=LIMIT_EXPIRY_HOURS)).isoformat()

        # Compare with active queue
        active = _get_active(conn)
        if len(active) < MAX_ACTIVE:
            # Room available — just add
            rec_id = _insert_rec(conn, sym, dir_, alert_type, el, eh, sl, tp1, tp2,
                                  score, arch, rat, conds, expires_at, s)
            notifications.append(("new", _get_rec(conn, rec_id), None))
        else:
            # Queue full — replace worst if new is better
            worst = min(active, key=lambda r: r["score"])
            if score > worst["score"]:
                _replace(conn, worst["id"], sym)
                rec_id = _insert_rec(conn, sym, dir_, alert_type, el, eh, sl, tp1, tp2,
                                      score, arch, rat, conds, expires_at, s)
                notifications.append(("replace", _get_rec(conn, rec_id), worst))
            else:
                logger.info("[Watcher] %s score %.1f not better than worst active (%.1f) — skipping",
                             sym, score, worst["score"])

    conn.commit()
    return notifications


def _insert_rec(conn, sym, dir_, alert_type, el, eh, sl, tp1, tp2,
                score, arch, rat, conds, expires_at, raw_setup) -> int:
    cur = conn.execute("""
        INSERT INTO entry_watcher_recs
          (symbol, direction, alert_type, entry_low, entry_high,
           sl_price, tp1_price, tp2_price, score, archetype,
           rationale, key_conditions, expires_at, analysis_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (sym, dir_, alert_type, el, eh, sl, tp1, tp2,
          score, arch, rat, conds, expires_at, json.dumps(raw_setup)))
    return cur.lastrowid


def _get_rec(conn, rec_id: int) -> dict:
    row = conn.execute("SELECT * FROM entry_watcher_recs WHERE id=?", (rec_id,)).fetchone()
    return dict(row) if row else {}


def review_active(conn) -> list:
    """
    Called every 45 minutes. For each active recommendation:
    1. Check expiry
    2. Check if price is now in zone (market entry trigger)
    3. Check invalidation conditions
    Returns list of (action, rec, extra) tuples for Telegram.
    """
    notifications = []
    active = _get_active(conn)

    for rec in active:
        sym = rec["symbol"]
        dir_ = rec.get("direction", "Long")

        # 1. Check expiry
        if rec.get("expires_at"):
            try:
                exp = datetime.fromisoformat(rec["expires_at"])
                if datetime.utcnow() > exp:
                    _expire(conn, rec["id"])
                    notifications.append(("expired", rec, None))
                    logger.info("[Watcher] %s limit expired after %dh", sym, LIMIT_EXPIRY_HOURS)
                    continue
            except Exception:
                pass

        # 2. Check if limit has become a market entry
        if rec["alert_type"] == "limit":
            price = _get_price(sym)
            if price and _price_in_zone(price, rec["entry_low"] or 0,
                                         rec["entry_high"] or 0, dir_):
                # Upgrade to market entry
                conn.execute(
                    "UPDATE entry_watcher_recs SET alert_type='market' WHERE id=?",
                    (rec["id"],)
                )
                rec["alert_type"] = "market"
                rec["_live_price"] = price
                notifications.append(("enter_now", rec, None))
                logger.info("[Watcher] %s limit → ENTER NOW at %.5g", sym, price)
                continue

        # 3. Check invalidation
        should_invalidate, reason = _check_invalidation(rec)
        if should_invalidate:
            _invalidate(conn, rec["id"], reason)
            # Save learning data
            _save_invalidation_learning(conn, rec, reason)
            notifications.append(("invalidated", rec, reason))
            logger.info("[Watcher] %s invalidated: %s", sym, reason)

    conn.commit()
    return notifications


def _save_invalidation_learning(conn, rec: dict, reason: str):
    """Save invalidation event to analyzed_calls for learning."""
    try:
        price = _get_price(rec["symbol"])
        learning = {
            "invalidation_reason": reason,
            "price_at_invalidation": price,
            "entry_zone": {"low": rec.get("entry_low"), "high": rec.get("entry_high")},
            "sl_price": rec.get("sl_price"),
            "time_active_hours": round(
                (datetime.utcnow() -
                 datetime.fromisoformat(rec["created_at"])).total_seconds() / 3600, 1
            ) if rec.get("created_at") else None,
        }
        conn.execute(
            "UPDATE entry_watcher_recs SET analysis_json=json_patch(COALESCE(analysis_json,'{}'), ?) "
            "WHERE id=?",
            (json.dumps({"invalidation_learning": learning}), rec["id"])
        )
    except Exception as e:
        logger.warning("[Watcher] Learning save failed: %s", e)


# ── Telegram formatting ───────────────────────────────────────────────────────

def send_notifications(notifications: list):
    """Send Telegram messages for all watcher events."""
    if not notifications:
        return
    try:
        import telegram_notify
        from database import db_conn
        with db_conn() as conn:
            active = _get_active(conn)
        active_summary = _format_queue_summary(active)
    except Exception:
        active_summary = ""

    for action, rec, extra in notifications:
        try:
            import telegram_notify
            msg = _format_message(action, rec, extra, active_summary)
            chart = (rec or {}).get("chart_png_b64", "")
            if chart:
                telegram_notify.send_photo(msg, chart)
            else:
                telegram_notify.send_message(msg)
        except Exception as e:
            logger.error("[Watcher] TG send failed: %s", e)


def _fp(v) -> str:
    if not v: return "—"
    try:
        n = float(v)
        if n >= 1000: return f"${n:,.1f}"
        if n >= 1: return f"${n:.4f}"
        return f"${n:.4g}"
    except: return str(v)


def _format_queue_summary(active: list) -> str:
    if not active:
        return "\n📋 <b>Active queue: empty</b>"
    lines = ["\n📋 <b>Active limit queue:</b>"]
    for i, r in enumerate(active, 1):
        t = "🎯 NOW" if r["alert_type"] == "market" else "📋 Limit"
        lines.append(f"  {i}. {t} {r['symbol']} {r['direction']} — {_fp(r['entry_low'])} · score {r['score']:.0f}")
    return "\n".join(lines)


def _format_message(action: str, rec: dict, extra, queue_summary: str) -> str:
    sym   = rec.get("symbol", "?")
    dir_  = rec.get("direction", "?").upper()
    el    = _fp(rec.get("entry_low"))
    eh    = _fp(rec.get("entry_high"))
    sl    = _fp(rec.get("sl_price"))
    tp1   = _fp(rec.get("tp1_price"))
    tp2   = _fp(rec.get("tp2_price"))
    score = rec.get("score", 0)
    rat   = rec.get("rationale") or ""
    live  = rec.get("_live_price")
    entry_str = f"{el}" if el == eh else f"{el} – {eh}"
    base  = sym.replace("USDT", "")
    dir_icon = "📈" if dir_ == "LONG" else "📉"

    if action == "new":
        atype = rec.get("alert_type", "limit")
        if atype == "market":
            header = f"🎯 <b>ENTER NOW — {base}USDT {dir_icon} {dir_}</b>  ({score:.0f}/10)"
            action_line = f"➡️ <b>Action:</b> Open {dir_} at market ({_fp(live) or entry_str})"
        else:
            header = f"📋 <b>Set Limit — {base}USDT {dir_icon} {dir_}</b>  ({score:.0f}/10)"
            action_line = f"➡️ <b>Action:</b> Place limit order at {entry_str}"
        lines = [header, "",
                 f"📍 <b>Entry:</b>  {entry_str}",
                 f"🛑 <b>Stop Loss:</b>  {sl}",
                 f"🎯 <b>TP1:</b>  {tp1}",
                 f"🎯 <b>TP2:</b>  {tp2}",
                 "", action_line, ""]
        if rat:
            lines += [f"💡 <b>Why:</b> <i>{rat[:250]}</i>", ""]
        try:
            conds = json.loads(rec.get("key_conditions") or "[]")
            if conds:
                lines.append("📊 <b>Signals:</b>")
                for c in conds[:4]:
                    lines.append(f"  · {c}")
                lines.append("")
        except Exception:
            pass
        lines.append(queue_summary)

    elif action == "replace":
        old = extra or {}
        old_sym = old.get("symbol", "?").replace("USDT", "")
        lines = [
            f"🔄 <b>Queue updated — {base}USDT replaces {old_sym}USDT</b>",
            f"New score: <b>{score:.0f}/10</b>  Old score: {old.get('score', 0):.0f}/10",
            "",
            f"⚠️ <b>Cancel your {old_sym}USDT {old.get('direction','').upper()} limit on exchange</b>",
            f"📋 <b>Set new limit: {base}USDT {dir_icon} {dir_} at {entry_str}</b>",
            f"🛑 SL: {sl}  🎯 TP1: {tp1}  TP2: {tp2}",
            "",
            queue_summary,
        ]

    elif action == "enter_now":
        lines = [
            f"🚨 <b>ENTER NOW — {base}USDT {dir_icon} {dir_}</b>",
            f"<i>Your limit at {entry_str} has been reached!</i>",
            "",
            f"💹 <b>Current price:</b>  {_fp(live)}",
            f"🛑 <b>Stop Loss:</b>  {sl}",
            f"🎯 <b>TP1:</b>  {tp1}",
            f"🎯 <b>TP2:</b>  {tp2}",
            "",
            queue_summary,
        ]

    elif action == "invalidated":
        reason = extra or rec.get("invalidation_reason", "technical structure changed")
        lines = [
            f"⛔ <b>Cancel limit — {base}USDT {dir_icon} {dir_}</b>",
            f"<b>Reason:</b> <i>{reason}</i>",
            "",
            f"➡️ <b>Action: Cancel your {base}USDT {dir_} limit order on exchange</b>",
            "",
            queue_summary,
        ]

    elif action == "expired":
        lines = [
            f"⏰ <b>Limit expired — {base}USDT {dir_icon} {dir_}</b>",
            f"<i>Entry zone {entry_str} not reached in {LIMIT_EXPIRY_HOURS}h — cancelled</i>",
            "",
            f"➡️ <b>Cancel your {base}USDT {dir_} limit order if still set</b>",
            "",
            queue_summary,
        ]
    else:
        lines = [f"[Watcher] {action} — {sym}"]

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def run_review_cycle():
    """Called every 45 min from scheduler. Reviews active recs, sends notifications."""
    from database import db_conn
    try:
        with db_conn() as conn:
            notifications = review_active(conn)
        if notifications:
            send_notifications(notifications)
            logger.info("[Watcher] Review done — %d notification(s)", len(notifications))
        else:
            logger.info("[Watcher] Review done — all active recs valid")
    except Exception as e:
        logger.error("[Watcher] Review cycle failed: %s", e)


def process_scan_results(setups: list):
    """Called after each scanner run. Classifies setups and updates queue."""
    from database import db_conn
    if not setups:
        return
    try:
        with db_conn() as conn:
            notifications = classify_and_add(setups, conn)
        if notifications:
            send_notifications(notifications)
    except Exception as e:
        logger.error("[Watcher] process_scan_results failed: %s", e)
