# Trading Journal

> **Disclaimer:** This project was entirely vibe-coded with [Claude Code](https://claude.ai/code) and has not been reviewed by professional developers or security experts. Use at your own risk. Contributions and code reviews are very welcome.

A self-hosted crypto futures trading journal with live Bitget API sync, AI-powered trade call analysis, and deep performance analytics. Runs on a Raspberry Pi (or any Linux box) and is accessible from any browser on your local network.

---

## Features

### Trade Journal
- Full trade history synced automatically from Bitget (USDT-M Futures)
- Import historical data via Bitget CSV export
- Filter by symbol, direction, date range, win/loss
- Edit analyst, notes, and tags on any trade — including old ones
- Manual trade entry for other exchanges or paper trades

### Dashboard
- KPIs: total P&L, win rate, profit factor, average trade, best/worst trade
- Cumulative P&L curve and wallet balance history
- Account equity and available balance (live from Bitget)

### Deep Dive Analytics
- P&L breakdown by symbol, month, day of week, and open hour
- Long vs Short comparison
- Trade duration breakdown
- Useful for spotting patterns in your trading behaviour

### AI Call Analyzer (Claude-powered)
- Paste an analyst's trade call → Claude extracts entry, SL, TP levels, scores the setup, and gives a full briefing
- Optionally attach a chart screenshot for vision analysis
- Saves calls with setup score, R:R, trade type, entry timing grade
- Records outcomes (TP1/TP2 hit, SL hit, manual close) to build a track record
- Per-analyst performance stats: win rate, avg PnL, TP hit rate, score accuracy

### Pending Limits / Shadow Trades
- Track limit orders placed on Bitget that aren't in the journal yet
- Live feed of open Bitget limit orders pulled from the API
- Link limits to analyst calls
- Bulk operations: set SL/TP, link to call, cancel all selected
- Auto-matches triggered limits to journal entries

### Live Positions
- Real-time open positions with unrealised P&L, duration, margin details
- Per-position AI analysis: trade quality, invalidation level, suggested actions
- Pending orders panel (entry limits and exit TP/SL orders)

### Auto-Sync
- Background sync every 5 minutes from Bitget API
- Cursor-based position pagination — catches trades regardless of how long they were held open
- Startup catch-up window to recover trades missed during downtime
- Idempotent: safe to sync repeatedly, no duplicates

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 / Flask 3.1 |
| Database | SQLite (via Python `sqlite3`) |
| Frontend | Vanilla JS SPA (single `index.html`, no build step) |
| Charts | Chart.js |
| AI | Anthropic Claude API (`claude-sonnet-4-6` / vision) |
| Exchange API | Bitget REST API v2 |
| Process manager | systemd |

---

## Requirements

- Python 3.10+
- A [Bitget](https://www.bitget.com) account with API access (read-only keys are sufficient)
- An [Anthropic API key](https://console.anthropic.com) for the AI features
- Linux host (tested on Raspberry Pi 5 with Raspberry Pi OS)

---

## Installation

```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
pip3 install -r requirements.txt
```

---

## Configuration

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
BITGET_API_KEY=your_api_key
BITGET_SECRET_KEY=your_secret_key
BITGET_PASSPHRASE=your_passphrase
PORT=8082
```

The Anthropic API key is set separately — the app reads `ANTHROPIC_API_KEY` from the environment or you can add it to `.env`.

### Bitget API key setup

1. Log in to Bitget → **Profile → API Management → Create API**
2. Permissions needed: **Read** only (the journal never places or cancels orders)
3. Copy the API Key, Secret Key, and Passphrase into `.env`

---

## Running

### Directly

```bash
python3 app.py
```

The app starts on `http://0.0.0.0:8082` (or the port set in `.env`).

### As a systemd service (recommended)

Copy the included service file and enable it:

```bash
sudo cp trading-journal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-journal
```

The service uses `EnvironmentFile=` to load `.env` and restarts automatically on failure.

---

## First run

1. Open `http://<your-host>:8082` in a browser
2. Go to **Import** and upload a Bitget CSV export to populate historical trades
3. The background sync will start automatically and keep the journal up to date from that point on
4. Use **Call Analyzer** to start logging analyst calls before you enter trades

---

## Project structure

```
app.py                  Flask routes and startup
database.py             Schema init and migrations
bitget_client.py        Bitget REST API v2 client (read-only)
bitget_sync.py          Background sync logic
importer.py             Bitget CSV import parser
analytics.py            Dashboard KPIs and deep dive stats
ai_advisor.py           AI analysis for open positions
ai_call_analyzer.py     AI trade call parser and scorer
ai_live_trade.py        Per-trade AI on the live positions view
templates/index.html    Single-page frontend (HTML + CSS)
static/app.js           All frontend JavaScript
docs/GUIDE.md           Developer reference (routes, schema, JS globals)
docs/USER_GUIDE.md      End-user feature guide
.env.example            Environment variable template
trading-journal.service systemd unit file
```

---

## Notes

- The journal is designed for personal use on a local network. There is no authentication layer — do not expose it to the public internet without adding one.
- All AI features require a valid `ANTHROPIC_API_KEY`. The journal works without it — the AI buttons will simply return an error.
- SQLite is sufficient for personal use (one user, <100k trades). No migration to Postgres is needed.

---

## Changelog

### v1.7 — Trading Tools & Heatmap
- **Position Sizing Calculator** — inline in Call Analyzer: enter Entry + SL, risk % auto-populates from account equity, shows Position Size / Leverage / Risk Amount / Risk Distance. Auto-fills entry and SL after every call analysis.
- **Economic Calendar** — fetches this week's high-impact USD events (ForexFactory, no API key). Yellow warning banner on Live Positions when events fall today or tomorrow. 
- **Trade Heatmap** — 7×24 grid in Deep Dive showing win rate by open hour (UTC) and close day. Color-coded: green ≥65% · blue 50–64% · yellow 40–49% · red <40%. Cells need ≥3 trades to activate.
- **BTC Dominance** — added to Dashboard Market Pulse strip via CoinGecko free API. Rising dominance shown in red (bad for alts), falling in green.

### v1.6 — Live Market Context
- **Fear & Greed Index** — live 0-100 sentiment score from alternative.me shown in a Market Pulse strip on the Dashboard
- **Bitget Funding Rate** — per-symbol, shown as chip on every Live Positions card; injected into per-position Claude analysis
- **Bitget Long/Short Ratio** — retail positioning per symbol on Live Positions cards; injected into analysis
- All three sources feed Claude's trade grading, per-position analysis, and full AI Advisor
- New module `market_context.py` with 5-minute in-memory cache; new `GET /api/market/context?symbols=` endpoint

### v1.5.5 — Edge Lab & UX Polish
- **Deep Dive split into two pages**: Deep Dive (charts + stats) and Edge Lab (setup analysis, grade breakdown, pattern detector, R:R tracking)
- **Analyst Leaderboard**: Edge Score (0-100) composite metric replaces raw table — ranks analysts by trade win rate, call outcome win rate, and TP1 hit rate. Medal rankings, color-coded rows, conversion rate column.
- **Correlation Detector enhanced**: sector-aware grouping (Bitcoin / ETH+L2 / SOL+L1 / Meme / DeFi / AI+Infra), two severity tiers (yellow = 2 positions, red = 3+)
- **AI Pattern Detector**: Claude analyses full trade history by setup, session, weekday, direction, duration, grade — returns warnings, insights, and strengths as cards
- **Setup Type filter in Journal**: filter by specific setup or ⚪ Untagged to quickly find and tag historical trades
- **Setup Type in Add Trade modal**: captured at creation time for manual trades

### v1.6 — Strategy & Pattern Intelligence
- **Analyst Leaderboard** — Edge Score (0-100) ranks analysts by composite metric: 50% trade win rate + 30% call outcome win rate + 20% TP1 hit rate. Color-coded rows, medal rankings, TP1 hit rate and call-to-trade conversion rate columns added.
- **Correlation Detector (enhanced)** — Now groups open positions by sector (Bitcoin / ETH+L2 / SOL+L1 / Meme / DeFi / AI+Infra). Two-tier severity: yellow (2 positions same sector/direction), red (3+). Triggered from Live Positions panel.
- **AI Pattern Detector** — New button in Deep Dive. Claude analyses full trade history by setup type, session, weekday, direction, duration, and grade. Returns up to 6 findings: warnings (edge leaks), insights (notable patterns), and strengths (what's working). Needs 20+ trades and 5+ per category.

### v1.5 — Trading Precision Features
- **AI Execution Grading** — click ⚡ Grade on any trade; Claude assigns A/B/C/D with a written explanation based on entry quality, exit discipline, and realized R:R. Richer analysis when trade is linked to an analyst call.
- **Setup Type Tagging** — label trades as Breakout, Pullback, Trend Continuation, Range Fade, Reversal, News/Event, or Other. Deep Dive shows P&L and win rate broken down by setup type.
- **Planned vs Realized R:R** — link a trade to an analyst call (via Call ID in the edit modal); Deep Dive computes and displays planned R:R vs what was actually achieved.
- Deep Dive gains three new sections: Execution Grade Analysis, P&L by Setup Type, Planned vs Realized R:R.
- New backend module `ai_trade_grader.py`; new API routes `POST /api/positions/<id>/grade` and `GET /api/analytics/rr`.

### v1.4.1 — Security Fix
- Incomplete string escaping fixed in `static/app.js` — backslashes now escaped before single quotes in analyst name interpolation (CWE-116)

### v1.4 — Code Quality & Security Completion
- `SECURITY.md` added — lightweight vulnerability reporting policy via GitHub private advisories
- CodeQL workflow added (`.github/workflows/codeql.yml`) — analysis now actually runs on push, PR, and weekly; branch protection is fully operational
- Frontend JS extracted from `templates/index.html` into `static/app.js` — `index.html` reduced from 3348 to 1078 lines

### v1.3 — Repository Hardening
- Branch protection on `main` — CodeQL must pass before merge
- Dependabot weekly pip dependency updates
- Squash merge only — cleaner commit history
- LICENSE fixed to standard GPL v3 (was showing as unrecognised)
- Secret scanning alert resolved — old revoked API key removed from history
- Dependencies bumped: Flask 3.1.3, Anthropic SDK 0.100.0

### v1.2 — Security Fixes
- Stack trace exposure fixed — exception details no longer returned to the client (CWE-209)
- Path traversal fixed — uploaded filenames sanitized with `secure_filename` before use (CWE-022)
- SQL filter inputs now validated with an allowlist before reaching the query layer (CWE-089)

### v1.1 — Privacy, Fixes & Polish
- **Open Position Risk** now shows true SL-based dollar risk instead of margin locked
- Recent trades table has a totals footer (sum of P&L and fees)
- Added 📈 favicon
- Dashboard monthly P&L was showing 0 — fixed
- Removed personal identifiers from all public files and docs
- systemd service template updated with `EnvironmentFile=` and generic placeholders

### v1.0 — Initial Release
- Full trade journal synced from Bitget USDT-M Futures
- Dashboard KPIs, P&L curve, equity curve, streak tracker, monthly target
- Deep Dive analytics by symbol, month, weekday, hour, direction, duration
- AI Call Analyzer: paste analyst call → Claude scores setup, tracks outcomes
- Pending Limits / Shadow Trades with bulk operations and AI analysis
- Live Positions with per-trade Claude analysis
- Background auto-sync every 5 minutes (cursor-based, no gaps)
- Analyst performance stats across journal, calls and pending limits

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
