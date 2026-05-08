#!/usr/bin/env python3
"""
telegram_release.py — Posts a formatted release announcement to Telegram.

Called by .github/workflows/telegram-release.yml.
All inputs come from environment variables (injected safely by the workflow).

Required env vars:
  TELEGRAM_BOT_TOKEN   — bot token from @BotFather
  TELEGRAM_CHANNEL_ID  — channel ID or @username

Optional env vars:
  TELEGRAM_GROUP_ID          — linked discussion group numeric ID
  TELEGRAM_UPDATES_TOPIC_ID  — message_thread_id of the Updates topic in the group
  RELEASE_TAG   — e.g. v2.6.1
  RELEASE_NAME  — release title
  RELEASE_BODY  — markdown release notes
  RELEASE_URL   — GitHub release URL
  PRERELEASE    — "true" | "false"
"""

import json
import os
import sys
import urllib.request


def send(token: str, chat_id: str, text: str, thread_id: str = "") -> bool:
    payload = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)

    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            if not resp.get("ok"):
                print(f"  Telegram error: {resp.get('description', resp)}", file=sys.stderr)
            return resp.get("ok", False)
    except Exception as e:
        print(f"  Request failed: {e}", file=sys.stderr)
        return False


def md_to_html(md: str) -> str:
    """Minimal GitHub Markdown → Telegram HTML conversion."""
    lines = []
    for line in md.strip().splitlines():
        s = line.strip()
        if s.startswith("### "):
            lines.append(f"\n<b>{s[4:]}</b>")
        elif s.startswith("## "):
            lines.append(f"\n<b>{s[3:]}</b>")
        elif s.startswith("- **") and ":**" in s:
            rest = s[2:].replace("**", "<b>", 1).replace("**", "</b>", 1)
            lines.append(f"  • {rest}")
        elif s.startswith("- "):
            lines.append(f"  • {s[2:]}")
        elif s.startswith("**") and s.endswith("**"):
            lines.append(f"<b>{s[2:-2]}</b>")
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def main():
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    group   = os.environ.get("TELEGRAM_GROUP_ID", "")
    topic   = os.environ.get("TELEGRAM_UPDATES_TOPIC_ID", "")
    tag     = os.environ.get("RELEASE_TAG",  "?")
    name    = os.environ.get("RELEASE_NAME", "")
    body    = os.environ.get("RELEASE_BODY", "")
    url     = os.environ.get("RELEASE_URL",  "")
    pre     = os.environ.get("PRERELEASE", "false").lower() == "true"

    if not token:
        print("TELEGRAM_BOT_TOKEN not set — skipping", file=sys.stderr)
        sys.exit(0)

    notes = md_to_html(body) if body.strip() else "<i>No release notes provided.</i>"
    if len(notes) > 2800:
        notes = notes[:2800] + "\n<i>…see full changelog on GitHub</i>"

    badge = "🧪 Pre-release" if pre else "🚀 New Release"
    sep   = "─" * 28

    channel_msg = (
        f"{badge} <b>Crypto Trading Journal {tag}</b>\n"
        f"<i>{name}</i>\n\n"
        f"{notes}\n\n"
        f"{sep}\n"
        f'🔗 <a href="{url}">Full release notes on GitHub</a>\n'
        f'⭐ <a href="https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal">Star the project</a>'
    )

    group_msg = (
        f"📦 <b>{tag}</b> — <i>{name}</i>\n\n"
        f"{notes}\n\n"
        f"{sep}\n"
        f'<a href="{url}">View on GitHub →</a>'
    )

    ok_any = False

    if channel:
        ok = send(token, channel, channel_msg)
        print(f"Channel {channel}: {'✓ sent' if ok else '✗ failed'}")
        ok_any = ok_any or ok
    else:
        print("TELEGRAM_CHANNEL_ID not set — skipping channel")

    if group:
        ok = send(token, group, group_msg, thread_id=topic)
        dest = f"group {group}" + (f" / topic {topic}" if topic else "")
        print(f"{dest}: {'✓ sent' if ok else '✗ failed'}")
        ok_any = ok_any or ok

    sys.exit(0 if ok_any or (not channel and not group) else 1)


if __name__ == "__main__":
    main()
