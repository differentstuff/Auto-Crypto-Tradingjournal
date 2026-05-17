#!/usr/bin/env python3
"""
hermes-telegram-bot.py — Custom Telegram bridge for the Trading Journal Hermes bot.

Replaces Hermes' native Telegram integration with proper prefix filtering:
  - Private chats : responds to everything (no prefix needed)
  - Group chats   : ONLY responds when message starts with "bot:" (case-insensitive)
                    Truly silent on all other group messages — never calls the LLM.

Language detection:
  - English   → respond in English
  - Indonesian → respond in Indonesian, then add "---" and English translation

Chart markers:
  When Hermes outputs [CHART:SYMBOL:direction:entry:sl:tp1:tp2:caption] in its reply,
  the proxy fetches the chart from the journal API and sends it as a Telegram photo.
  The marker is stripped from the text response.

Reads credentials from ~/.hermes-telegram.env automatically.
Calls Hermes API at localhost:8642 for LLM processing.
"""

import os
import re
import json
import time
import logging
import threading
import requests
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TG] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hermes-tg")

# ── Load credentials from ~/.hermes-telegram.env ─────────────────────────────
# Separate from ~/.hermes/.env so Hermes itself doesn't pick up the bot token.
for _ef in [Path.home() / ".hermes-telegram.env",
            Path.home() / ".hermes" / ".env"]:
    if _ef.exists():
        for line in _ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = set(
    int(x) for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",") if x.strip()
)
HERMES_URL    = "http://localhost:8642/v1/chat/completions"
HERMES_KEY    = os.environ.get("API_SERVER_KEY", "change-me-local-dev")
JOURNAL_URL   = "http://localhost:8082"
TG_BASE       = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Conversation history (per chat) ───────────────────────────────────────────
_histories: dict[int, list] = {}
_hist_lock = threading.Lock()
MAX_HISTORY = 20

# ── Indonesian word detection ─────────────────────────────────────────────────
_ID_WORDS = {
    "apa", "ini", "itu", "dan", "yang", "di", "ke", "dari", "untuk", "dengan",
    "adalah", "ada", "tidak", "bisa", "saya", "kamu", "kita", "kami", "mereka",
    "harga", "berapa", "bagaimana", "apakah", "tolong", "posisi", "scan", "pasar",
    "beli", "jual", "profit", "rugi", "lebih", "sudah", "belum", "akan", "baru",
    "atau", "juga", "karena", "jika", "kalau", "ketika", "sekarang", "tapi",
}

def _is_indonesian(text: str) -> bool:
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return len(words & _ID_WORDS) >= 2


def _build_user_message(text: str, indonesian: bool) -> str:
    if indonesian:
        return (
            f"{text}\n\n"
            "[Language instruction: respond in Bahasa Indonesia. "
            "After your response add a line with just '---' and then provide "
            "an English translation of your answer.]"
        )
    return text


# ── Telegram API ──────────────────────────────────────────────────────────────
def _tg(method: str, **kwargs) -> dict:
    try:
        r = requests.post(f"{TG_BASE}/{method}", json=kwargs, timeout=30)
        return r.json()
    except Exception as e:
        log.warning("TG API %s failed: %s", method, e)
        return {}


def _send(chat_id: int, text: str):
    if not text or not text.strip():
        return
    # Telegram message limit is 4096 chars
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        _tg("sendMessage", chat_id=chat_id, text=chunk,
            parse_mode="Markdown", disable_web_page_preview=True)


# ── Chart marker handling ─────────────────────────────────────────────────────
# Pattern: [CHART:SYMBOL:direction:entry:sl:tp1:tp2:caption]
# All fields after SYMBOL are optional — use _ or 0 to omit.
_CHART_RE = re.compile(r'\[CHART:([^\]]+)\]')

def _send_chart(chat_id: int, fields: str):
    """Parse a CHART marker and send the annotated chart as a Telegram photo."""
    parts = [p.strip() for p in fields.split(":")]
    symbol    = parts[0].upper() if len(parts) > 0 else ""
    direction = parts[1] if len(parts) > 1 and parts[1] not in ("", "_") else None
    entry     = parts[2] if len(parts) > 2 and parts[2] not in ("", "0", "_") else None
    sl        = parts[3] if len(parts) > 3 and parts[3] not in ("", "0", "_") else None
    tp1       = parts[4] if len(parts) > 4 and parts[4] not in ("", "0", "_") else None
    tp2       = parts[5] if len(parts) > 5 and parts[5] not in ("", "0", "_") else None
    caption   = parts[6] if len(parts) > 6 and parts[6] not in ("", "_") else symbol

    if not symbol:
        log.warning("CHART marker missing symbol: %r", fields)
        return

    params: dict = {}
    if direction: params["direction"] = direction
    if entry:     params["entry"]     = entry
    if sl:        params["sl"]        = sl
    if tp1:       params["tp1"]       = tp1
    if tp2:       params["tp2"]       = tp2

    try:
        r = requests.get(f"{JOURNAL_URL}/api/chart/annotated/{symbol}",
                         params=params, timeout=30)
        data = r.json().get("data", {})
        chart_b64 = data.get("chart_b64", "")
    except Exception as e:
        log.error("Chart API error for %s: %s", symbol, e)
        return

    if not chart_b64:
        log.warning("No chart_b64 returned for %s", symbol)
        return

    import base64
    img_bytes = base64.b64decode(chart_b64)
    try:
        resp = requests.post(
            f"{TG_BASE}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("chart.png", img_bytes, "image/png")},
            timeout=30,
        )
        if resp.ok:
            log.info("Chart sent for %s to chat %s", symbol, chat_id)
        else:
            log.error("Chart send failed for %s: %s", symbol, resp.text[:120])
    except Exception as e:
        log.error("Chart send exception for %s: %s", symbol, e)


def _process_reply(chat_id: int, reply: str):
    """Strip [CHART:...] markers from reply, send photos, then send the text."""
    markers = _CHART_RE.findall(reply)
    clean   = _CHART_RE.sub("", reply).strip()

    # Send text first (if any), then photos
    if clean:
        _send(chat_id, clean)
    for fields in markers:
        _send_chart(chat_id, fields)


# ── Hermes API ────────────────────────────────────────────────────────────────
def _ask_hermes(chat_id: int, user_text: str) -> str:
    with _hist_lock:
        hist = _histories.setdefault(chat_id, [])
        hist.append({"role": "user", "content": user_text})
        if len(hist) > MAX_HISTORY:
            hist[:] = hist[-MAX_HISTORY:]
        messages = list(hist)

    payload = {"model": "hermes", "messages": messages, "stream": False}
    headers = {"Authorization": f"Bearer {HERMES_KEY}"}
    try:
        r = requests.post(HERMES_URL, json=payload, headers=headers, timeout=120)
        data = r.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        log.error("Hermes API error: %s", e)
        return "⚠️ Could not reach the journal service."

    with _hist_lock:
        if reply:
            _histories[chat_id].append({"role": "assistant", "content": reply})

    return reply


# ── Message handler ───────────────────────────────────────────────────────────
def _handle(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id   = msg["chat"]["id"]
    chat_type = msg["chat"].get("type", "private")   # private | group | supergroup
    user_id   = (msg.get("from") or {}).get("id")
    text      = (msg.get("text") or "").strip()

    if not text or not user_id:
        return

    # Access control — ignore unknown users
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        log.debug("Ignored message from unauthorized user %s", user_id)
        return

    is_group = chat_type in ("group", "supergroup")

    if is_group:
        # ── Group: require "bot:" prefix ──────────────────────────────────────
        match = re.match(r"^bot\s*:\s*", text, re.IGNORECASE)
        if not match:
            # Truly silent — do not call Hermes at all
            return
        text = text[match.end():].strip()
        if not text:
            return
        log.info("Group activation [%s]: %r", chat_id, text[:60])
    else:
        log.info("DM [%s]: %r", chat_id, text[:60])

    indonesian = _is_indonesian(text)
    user_msg   = _build_user_message(text, indonesian)

    try:
        _tg("sendChatAction", chat_id=chat_id, action="typing")
        reply = _ask_hermes(chat_id, user_msg)
        _process_reply(chat_id, reply)
    except Exception as e:
        log.error("Handle error for chat %s: %s", chat_id, e)


# ── Polling loop ──────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in ~/.hermes-telegram.env")

    log.info("Starting Telegram bot (allowed users: %s)", ALLOWED_USERS or "ALL")
    log.info("Hermes API: %s", HERMES_URL)
    log.info("Journal API: %s", JOURNAL_URL)

    offset = 0
    while True:
        try:
            resp = _tg(
                "getUpdates",
                offset=offset,
                timeout=30,
                allowed_updates=["message", "edited_message"],
            )
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                threading.Thread(target=_handle, args=(update,), daemon=True).start()
        except Exception as e:
            log.warning("Poll error: %s — retrying in 5s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
