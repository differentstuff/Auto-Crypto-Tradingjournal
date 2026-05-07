"""
telegram_notify.py — Telegram bot notifications for the Setup Scanner.

No external dependencies — uses only urllib.request (stdlib).

Configuration (add to .env):
  TELEGRAM_BOT_TOKEN  — from @BotFather on Telegram
  TELEGRAM_CHAT_ID    — your personal chat or group ID
  APP_URL             — journal URL for the deep-link (default: http://192.168.1.21:8082)

Getting your Chat ID:
  1. Message @userinfobot on Telegram — it replies with your numeric ID.
  2. Or: message your bot once, then call
     https://api.telegram.org/bot{TOKEN}/getUpdates
     and find "message.chat.id" in the JSON.
"""

import json
import os
import urllib.request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
APP_URL        = os.environ.get("APP_URL", "http://192.168.1.21:8082")


def is_configured() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT)


def send_message(text: str) -> bool:
    """Send HTML-formatted message. Returns True on success."""
    if not is_configured():
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id":                  TELEGRAM_CHAT,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        print(f"[Telegram] Failed: {e}")
        return False


# ── Formatters ─────────────────────────────────────────────────────────────────

def _fp(v) -> str:
    """Format a price value."""
    if not v:
        return "—"
    n = float(v)
    if n >= 10000: return f"${n:,.0f}"
    if n >= 1000:  return f"${n:,.1f}"
    if n >= 1:     return f"${n:.4f}"
    return f"${n:.4g}"


def send_setup_alert(setups: list) -> bool:
    """Format scanner results and send as a Telegram alert."""
    n = len(setups)
    lines = [f"🔍 <b>Setup Scanner</b> — <b>{n} setup{'s' if n != 1 else ''} found</b>\n"]

    for s in setups[:6]:
        sym   = s.get("_symbol") or s.get("symbol", "?")
        base  = sym.replace("USDT", "")
        dir_  = (s.get("direction") or "").upper()
        score = s.get("setup_score", "?")
        label = s.get("setup_label", "")
        rr    = s.get("rr_ratio", "—")
        urg   = s.get("urgency", "")
        pat   = s.get("chart_pattern") or ""

        ent  = s.get("entry_zone") or {}
        el   = _fp(ent.get("low"))
        eh   = _fp(ent.get("high"))
        ent_str = f"{el}–{eh}" if el != "—" and eh != "—" and el != eh else el
        sl_str  = _fp(s.get("sl_price"))
        tp1_str = _fp(s.get("tp1_price"))

        dir_icon   = "📈" if dir_ == "LONG" else "📉"
        score_icon = "⭐⭐" if isinstance(score, int) and score >= 10 else "⭐"

        lines.append(
            f"{score_icon} <b>{base}USDT</b> {dir_icon} <b>{dir_}</b> — "
            f"<b>{score}/10</b> {label}"
        )
        lines.append(
            f"Entry {ent_str} · SL {sl_str} · TP1 {tp1_str} · R:R {rr}"
        )
        meta = " · ".join(filter(None, [urg, pat]))
        if meta:
            lines.append(f"<i>{meta}</i>")
        summary = (s.get("summary") or "").strip()
        if summary:
            lines.append(f"<i>{summary[:140]}{'…' if len(summary) > 140 else ''}</i>")
        lines.append("")

    if n > 6:
        lines.append(f"<i>…and {n - 6} more setup(s)</i>\n")

    lines.append(f'<a href="{APP_URL}">📊 Open Journal → Setup Scanner</a>')
    return send_message("\n".join(lines))


def send_test_message() -> bool:
    """Send a test message to verify configuration."""
    return send_message(
        "✅ <b>Trading Journal — Telegram connected</b>\n\n"
        "Setup Scanner alerts are active. You will be notified every 30 minutes "
        "when the scanner finds 6+/10 setups.\n\n"
        f'<a href="{APP_URL}">Open Journal</a>'
    )
