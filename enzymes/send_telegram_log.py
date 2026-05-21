"""
enzymes/send_telegram_log.py -- Transporter enzyme: Telegram notification.

Sends one-way log messages to a Telegram channel when:
  - A trade is opened or closed
  - A cycle completes with notable action
  - An idle streak reaches a threshold

Config-gated: only activates when modules.telegram_logs is True.
Graceful when token is missing: logs a warning and continues.

Writes: nothing to substrate (side-effect only)

Enzyme class: Transporter
Activates when: modules.telegram_logs is True AND cycle action is notable

Port of: telegram_notify.py (send_message, formatters)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _send_telegram_message(text: str, bot_token: str, chat_id: str) -> bool:
    """
    Send HTML-formatted message to Telegram.

    Returns True on success, False on any failure.
    No external dependencies — uses only urllib.request (stdlib).
    """
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        _log.warning("Telegram send failed: %s", e)
        return False


def _format_trade_message(action: str, trade: dict) -> str:
    """Format a trade notification message."""
    symbol = trade.get("symbol", "?")
    direction = trade.get("direction", "?").upper()
    entry = trade.get("entry_price", 0)
    sl = trade.get("sl_price", 0)
    tp1 = trade.get("tp1", 0)
    size = trade.get("size_usdt", 0)

    dir_icon = "📈" if direction == "LONG" else "📉"

    if action == "trade_open":
        return (
            f"{dir_icon} <b>{symbol} {direction}</b>\n"
            f"Entry: {entry:.2f} | SL: {sl:.2f} | TP: {tp1:.2f}\n"
            f"Size: {size:.2f} USDT"
        )
    elif action == "trade_closed":
        reason = trade.get("reason", "")
        return (
            f"🏁 <b>{symbol} CLOSED</b>\n"
            f"Reason: {reason}"
        )
    return ""


def _format_idle_message(idle_cycles: int, reasons: list) -> str:
    """Format an idle streak notification."""
    reasons_text = "; ".join(reasons[-3:]) if reasons else "no signal"
    return (
        f"⏳ <b>Idle streak: {idle_cycles} cycles</b>\n"
        f"Last reasons: {reasons_text}"
    )


@register_enzyme
class SendTelegramLog(Enzyme):
    """
    Transporter enzyme: send log notifications via Telegram.

    Config-gated: only activates when modules.telegram_logs is True
    in the strategy configuration. Graceful when bot_token or chat_id
    is missing — logs a warning and continues without error.

    Sends notifications for:
      - Trade opened (action = 'trade_open')
      - Trade closed (action = 'trade_closed')
      - Idle streak threshold (configurable)
    """

    name = "SendTelegramLog"
    enzyme_class = EnzymeClass.TRANSPORTER
    priority = 0

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Only activate when telegram_logs module is enabled AND action is notable."""
        if substrate.cfg("modules.telegram_logs", False) is not True:
            return False
        # Don't activate for idle/wait cycles (no news to report)
        action = substrate.decisions.get("action", "wait")
        if action == "wait":
            return False
        return True

    def transform(self, substrate: Substrate) -> Substrate:
        """Send Telegram notification for notable cycle events."""
        # Get credentials from config (not substrate — secrets are stripped)
        # The enzyme reads from its own config reference which came from
        # the daemon's strategy-safe config slice. For telegram, the token
        # is in the exchange.yaml under telegram.* which is in the
        # _SECRET_KEYS set. So we need to get it differently.
        # The daemon passes config to enzymes, but telegram credentials
        # are in the stripped section. We'll read from env vars as fallback.

        import os
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        # Also check substrate config (in case someone put it in strategy YAML)
        if not bot_token:
            bot_token = substrate.cfg("telegram.bot_token", "")
        if not chat_id:
            chat_id = substrate.cfg("telegram.chat_id", "")

        if not bot_token or not chat_id:
            self._log.debug("Telegram not configured — skipping notification")
            return substrate

        action = substrate.decisions.get("action", "wait")
        message = None

        # Trade opened
        if action == "trade_open":
            trade = substrate.decisions.get("trade_approved", {})
            if trade:
                message = _format_trade_message("trade_open", trade)

        # Trade closed
        elif action == "trade_closed":
            exit_approved = substrate.decisions.get("exit_approved", {})
            if exit_approved:
                message = _format_trade_message("trade_closed", exit_approved)

        # Idle streak notification (every 10 idle cycles)
        idle_cycles = substrate.learning.get("idle_cycles", 0)
        idle_reasons = substrate.learning.get("idle_reasons", [])
        idle_notify_every = substrate.cfg("telegram.idle_notify_every", 10)
        if idle_cycles > 0 and idle_cycles % idle_notify_every == 0:
            message = _format_idle_message(idle_cycles, idle_reasons)

        # Send if we have a message
        if message:
            success = _send_telegram_message(message, bot_token, chat_id)
            if success:
                self._log.info("Telegram notification sent")
            else:
                self._log.warning("Telegram notification failed")

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Low flux — notifications are not critical path."""
        if self.can_activate(substrate):
            return 0.1
        return 0.0