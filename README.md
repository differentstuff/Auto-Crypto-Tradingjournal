# Trading Journal

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
| AI | Anthropic Claude API (`claude-opus-4-5` / vision) |
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
git clone https://github.com/anvilfilbert/trading-journal.git
cd trading-journal
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
templates/index.html    Single-page frontend (all UI)
static/                 CSS, icons
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

## License

MIT
