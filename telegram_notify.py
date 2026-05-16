"""
telegram_notify.py — Telegram bot notifications for the Setup Scanner.

No external dependencies — uses only urllib.request (stdlib).

Configuration (add to .env):
  TELEGRAM_BOT_TOKEN  — from @BotFather on Telegram
  TELEGRAM_CHAT_ID    — your personal chat or group ID
  APP_URL             — journal URL for the deep-link (default: http://localhost:8082)

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
APP_URL        = os.environ.get("APP_URL", "http://localhost:8082")


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


def send_photo(caption: str, png_b64: str) -> bool:
    """
    Send a PNG chart image with a caption to the Telegram channel.
    Falls back to send_message(caption) if the photo send fails.
    Returns True on success.
    """
    if not is_configured() or not png_b64:
        return send_message(caption)
    try:
        import base64
        img_bytes = base64.b64decode(png_b64)
        boundary  = "TJFormBoundary"
        def _field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()

        body = (
            _field("chat_id", TELEGRAM_CHAT)
            + _field("parse_mode", "HTML")
            + (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                f"{caption[:1024]}\r\n"
            ).encode()
            + (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode()
            + img_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        print(f"[Telegram] send_photo failed ({e}), falling back to text")
        return send_message(caption)


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
    """Format scanner results and send as Telegram alerts — one message per setup with chart."""
    if not setups:
        return False

    # Send individual alert per setup (with its own chart)
    success = False
    for s in setups[:5]:   # cap at 5
        sym   = s.get("_symbol") or s.get("symbol", "?")
        base  = sym.replace("USDT", "")
        dir_  = (s.get("direction") or "Long").upper()
        score = s.get("setup_score") or s.get("_final_score") or 0
        label = s.get("setup_label", "")
        rr    = s.get("rr_ratio", "—")
        urg   = s.get("urgency", "")
        arch  = s.get("chart_pattern") or ""

        ez    = s.get("entry_zone") or {}
        el    = _fp(ez.get("low"))
        eh    = _fp(ez.get("high"))
        entry_str = f"{el} – {eh}" if el != eh and el != "—" and eh != "—" else (el if el != "—" else eh)
        sl_str  = _fp(s.get("sl_price"))
        tp1_str = _fp(s.get("tp1_price"))
        tp2_str = _fp(s.get("tp2_price"))

        live_price   = s.get("_live_price")
        drift_pct    = s.get("_price_drift_pct")
        price_warn   = s.get("_price_warning", "")
        conditions   = s.get("key_conditions") or []
        why          = s.get("why_this_score") or s.get("summary") or ""
        confluence   = s.get("confluence_summary") or ""

        dir_icon  = "📈" if dir_ == "LONG" else "📉"
        score_bar = "🟢" if score >= 8 else "🟡" if score >= 6 else "🟠"

        lines = []
        lines.append(f"{score_bar} <b>{base}USDT {dir_icon} {dir_}</b>  —  <b>{score}/10</b> {label}")
        if arch:
            lines.append(f"<i>{arch}</i>")
        lines.append("")

        # Trade levels
        lines.append(f"📍 <b>Entry:</b>  {entry_str}")
        if live_price:
            lines.append(f"💹 <b>Live now:</b>  {_fp(live_price)}" + (f"  <i>(+{drift_pct:.1f}% from entry)</i>" if drift_pct and drift_pct > 0.5 else ""))
        lines.append(f"🛑 <b>Stop Loss:</b>  {sl_str}")
        lines.append(f"🎯 <b>TP1:</b>  {tp1_str}")
        lines.append(f"🎯 <b>TP2:</b>  {tp2_str}")
        lines.append(f"⚖️ <b>R:R:</b>  {rr}")
        if urg:
            lines.append(f"⏱ <b>Timing:</b>  {urg}")
        lines.append("")

        # Why enter
        if why:
            lines.append(f"💡 <b>Why enter:</b>")
            lines.append(f"<i>{why[:280]}{'…' if len(why) > 280 else ''}</i>")
            lines.append("")

        # Key signals
        if conditions:
            lines.append("📊 <b>Signals:</b>")
            for c in conditions[:5]:
                lines.append(f"  · {c}")
            lines.append("")

        if confluence:
            lines.append(f"🔗 <i>{confluence[:160]}</i>")

        # Price staleness warning
        if price_warn:
            lines.append(f"\n⚠️ <i>{price_warn}</i>")

        lines.append(f'\n<a href="{APP_URL}">📊 Open Scanner</a>')

        msg = "\n".join(lines)
        chart = s.get("chart_png_b64", "")
        if chart:
            ok = send_photo(msg, chart)
        else:
            ok = send_message(msg)
        if ok:
            success = True

    return success


def send_test_message() -> bool:
    """Send a test message to verify configuration."""
    return send_message(
        "✅ <b>Trading Journal — Telegram connected</b>\n\n"
        "Setup Scanner alerts are active. You will be notified every 30 minutes "
        "when the scanner finds 6+/10 setups.\n\n"
        f'<a href="{APP_URL}">Open Journal</a>'
    )
