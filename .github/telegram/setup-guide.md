# Telegram Community Setup — Live Configuration

## Current Structure (fully configured as of 2026-05-09)

```
📢 Channel: @autocryptotradingjournal  (id: -1003763955901)
   Bot @myopen99_bot — ADMIN ✅
   Welcome message pinned ✅
   GitHub Actions auto-posts releases ✅

💬 Forum Group: @autotradingjournal  (id: -1003889940179)
   Bot @myopen99_bot — ADMIN ✅
   Topics:
     👋 Welcome          (thread_id: 13) — pinned welcome message ✅
     🚀 Updates          (thread_id: 14) — GitHub releases auto-post here ✅
     💡 Feature Requests (thread_id: 15) — user idea threads ✅
     🐛 Bug Reports      (thread_id: 16) — user issue threads ✅
     💬 General          (thread_id: 17) — open discussion ✅
```

---

## GitHub Secrets (add in Settings → Secrets → Actions)

| Secret | Value |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | From `.env` — @myopen99_bot token |
| `TELEGRAM_CHANNEL_ID` | `-1003763955901` |
| `TELEGRAM_GROUP_ID` | `-1003889940179` |
| `TELEGRAM_UPDATES_TOPIC_ID` | `14` |

---

## How it works

- Every GitHub Release published → workflow fires → posts to channel + Updates topic
- Users join @autotradingjournal to discuss in topics
- Channel posts can be linked to the group for comments (via Channel Edit → Discussion)

---

## Links

- Channel: https://t.me/autocryptotradingjournal
- Forum group: https://t.me/autotradingjournal
- GitHub: https://github.com/anvilfilbert/Auto-Trader
