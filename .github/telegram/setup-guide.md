# Telegram Community Setup Guide

## Recommended Structure

Telegram doesn't have nested channels, but the right setup is:

```
📢 Channel: @autocryptotradingjournal  (broadcast — only admins post)
    └── 💬 Linked Discussion Group (Forum mode — everyone can post + reply)
            ├── 📌 General           (default topic — introductions, questions)
            ├── 👋 Welcome           (pinned introduction, read-only for new members)
            ├── 🚀 Updates           (GitHub releases auto-posted here)
            ├── 💡 Feature Requests  (users open threads for ideas)
            └── 🐛 Bug Reports       (users open threads for issues)
```

---

## Step 1 — Create the linked Discussion Group

1. Open your channel `@autocryptotradingjournal` in the Telegram app
2. Tap the channel name at the top → **Edit** → **Discussion**
3. Tap **Create New Group** → name it e.g. `Auto Crypto Trading Journal — Community`
4. The group is now linked: when you post in the channel, a "Comments" button appears

---

## Step 2 — Enable Forum/Topics mode

1. Open the newly created group
2. Tap the group name → **Edit** → scroll to **Topics** → enable it
3. The group becomes a Forum — each topic is its own thread

---

## Step 3 — Create Topics

In the group, tap the pencil icon → **New Topic** and create:

| Topic name | Icon | Purpose |
|---|---|---|
| General | 💬 | Default — open discussion, questions |
| Welcome | 👋 | Pinned welcome message for new members |
| Updates | 🚀 | GitHub releases auto-posted here |
| Feature Requests | 💡 | Users open threads for ideas |
| Bug Reports | 🐛 | Users open threads for issues |

**Tip:** Pin the Welcome message at the top of the Welcome topic.

---

## Step 4 — Get the Group and Topic IDs

You need the numeric IDs for the GitHub Actions automation.

### Get Group ID
1. Add `@userinfobot` to the group, send any message → it replies with the group's numeric ID
2. Or: forward a message from the group to `@userinfobot`
3. The ID will look like `-1001234567890` (negative, starts with -100)

### Get Topic (Thread) IDs
1. In Telegram Desktop, right-click a message in the Updates topic → **Copy Message Link**
2. The link looks like `https://t.me/c/1234567890/2/15` — the second number (`2`) is the `message_thread_id`
3. Or: use the Bot API — send a test message to the topic and check the response for `message_thread_id`

---

## Step 5 — Add GitHub Secrets

Go to your GitHub repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather (same one used for scanner alerts) |
| `TELEGRAM_CHANNEL_ID` | `@autocryptotradingjournal` or the numeric channel ID |
| `TELEGRAM_GROUP_ID` | The numeric group ID from Step 4 (e.g. `-1001234567890`) |
| `TELEGRAM_UPDATES_TOPIC_ID` | The thread ID of the Updates topic (e.g. `2`) |

---

## Step 6 — Test

Publish a GitHub release (or a pre-release for testing). The workflow
`.github/workflows/telegram-release.yml` will fire automatically and post
to both the channel and the Updates topic.

You can also test locally:
```bash
TELEGRAM_BOT_TOKEN=xxx \
TELEGRAM_CHANNEL_ID=@autocryptotradingjournal \
RELEASE_TAG=v2.6.1 \
RELEASE_NAME="Test Release" \
RELEASE_BODY="- Fix: test bug" \
RELEASE_URL="https://github.com/test" \
python3 .github/scripts/telegram_release.py
```

---

## Pinned Messages

### Welcome topic pinned message
Use the content from `.github/telegram/welcome-message.txt`

### Channel description
```
Open-source self-hosted crypto futures trading journal.
AI-powered analytics, live Bitget + Blofin sync, setup scanner.
Runs on Raspberry Pi. 100% free.

GitHub: https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal
Community: [link to discussion group]
```

### Updates topic pinned message
```
🚀 This topic is automatically updated when a new version is released on GitHub.

Subscribe to this group to get notified about:
• New features
• Bug fixes
• Breaking changes

Latest release: https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal/releases
```

### Feedback & Feature Requests topic pinned message
```
💡 Share your ideas and suggestions here!

When opening a thread:
1. Search first — your idea might already exist
2. Give a clear title (e.g. "Add support for Bybit")
3. Describe what you'd like and why it would help
4. React with 👍 to upvote ideas you'd like to see

GitHub Issues: https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal/issues
```

### Bug Reports topic pinned message
```
🐛 Found a bug? Report it here or on GitHub Issues.

When reporting:
1. What you expected to happen
2. What actually happened
3. Steps to reproduce
4. Your version (visible in the journal's Settings page)

GitHub Issues: https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal/issues
```
