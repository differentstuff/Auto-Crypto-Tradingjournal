# Trading Journal

> **Disclaimer:** Vibe-coded with [Claude Code](https://claude.ai/code). Not reviewed by professional security experts. Use at your own risk.

A self-hosted crypto futures trading journal with live Bitget/Blofin sync, AI-powered analysis, and deep performance analytics. Runs on a Raspberry Pi (or any Linux box), accessible from any local browser.

**Community:** [📢 t.me/autocryptotradingjournal](https://t.me/autocryptotradingjournal) · [💬 t.me/autotradingjournal](https://t.me/autotradingjournal)

<p align="center">
  <img src="docs/images/factsheet-preview.png" alt="Crypto Trading Journal fact sheet" width="720">
</p>

<p align="center">
  <strong>v1.0.1</strong>
  &nbsp;·&nbsp;
  <a href="https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal/releases/download/v2.5/trading-journal-factsheet.pdf">📄 Fact Sheet</a>
  &nbsp;·&nbsp;
  <a href="https://t.me/autocryptotradingjournal">📢 Telegram</a>
  &nbsp;·&nbsp;
  <a href="https://t.me/autotradingjournal">💬 Community</a>
</p>

---

## Features

### 📒 Trade Journal
- Full history synced from Bitget USDT-M Futures (5 min cadence, cursor-based, no gaps)
- Blofin integration with per-exchange filter across all analytics
- CSV import for historical data
- Edit analyst, notes, tags, setup type on any trade
- Manual trade entry

### 📊 Dashboard & Analytics
- KPIs: P&L, win rate, profit factor, Sharpe ratio, Calmar ratio
- Rolling 30-day metrics vs all-time comparison strip
- Cumulative P&L + drawdown overlay on wallet chart
- Deep Dive: breakdown by symbol, month, weekday, hour, direction, duration, setup type
- Expected Value per setup · MFE/MAE tracking · R:R planned vs realized

### 🤖 AI Call Analyzer
- Paste analyst call → Claude scores it 1–10 with step-by-step reasoning (stored as `cot_reasoning`)
- **CoT learning loop**: prior analysis of same symbol injected into next prompt
- **Grok social intelligence**: xAI Grok adds X/Twitter sentiment and news — weight scales with market cap (micro-cap 80%, small-cap 40%, large-cap skipped)
- Setup-type rubrics (Breakout / Reversal / Continuation / Range), ATR-aware SL check, portfolio correlation check
- Chart screenshot vision analysis, conditional chart context (skips fetch for non-technical calls)
- Saves entry/SL/TP levels, R:R, trade type, entry timing; tracks outcomes (TP1/TP2/SL/close)
- Per-analyst performance stats: win rate, avg P&L, TP hit rate, score accuracy

### 🔭 Setup Scanner
- Scans 100 USDT-M symbols every 30 min: confluence filter → technical quality gate → batched Sonnet scoring
- BTC market regime context (bull/bear/range) injected into scoring
- Telegram alerts with full entry/SL/TP details for setups ≥ 6/10
- **Auto-saves alerted setups to the journal** — positions auto-link to the scanner signal that triggered them
- Nansen smart money signals for scanner finalists
- Threshold self-calibration from 30-day TP/FP rates

### 📡 Live Positions
- Real-time open positions (Bitget + Blofin merged) with unrealised P&L, duration, liquidation distance
- **Call targets panel**: auto-links live position to its saved call — shows SL/TP distance from mark, TP1 hit alert with break-even prompt
- **Smart auto-linking**: scanner signals and closed→reopened positions link without user action
- Per-position AI analysis (Haiku): action recommendation, key risks, SL/TP suggestions
- Correlation warning for same-sector/same-direction concentration
- Economic calendar warning banner for high-impact USD events

### 📈 Chart Explorer
- Interactive candlestick charts (LightweightCharts) with S/R zones, trendlines, Fibonacci retracements
- VMC Cipher A/B (WaveTrend oscillator) in a synced lower pane
- Weekly S/R overlay on intraday charts, liquidation level lines from open positions
- Technical indicator cards: RSI, MACD, EMA stack, Bollinger Bands, ADX, Stoch RSI, ATR, Volume, CVD
- Pop-out detached chart window; 15m / 1H / 4H / 1D timeframe switcher

### 🧠 AI Learning System
- **Personalised Rulebook**: Claude synthesises 5–10 rules from your trade history; staleness decay; regen guard
- Anti-pattern injection: top positive patterns added to every call prompt
- Hindsight analysis: retroactive blind scoring with TP/FP/TN/FN verdicts stored in DB
- Token usage dashboard with per-call cost tracking

### ⚙️ Settings & Ops
- In-app credential management (no restart needed)
- `scripts/self_test.py`: 54-test smoke runner for all API endpoints
- systemd service with auto-restart

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 / Flask 3.1 |
| Database | SQLite 3 (WAL mode) |
| Frontend | Vanilla JS SPA (17 modules, no build step) |
| Charts | LightweightCharts v4.1.3 + Chart.js |
| Technical analysis | pandas-ta |
| AI — analysis | Claude Sonnet 4.6 (calls / scanner / advisor) |
| AI — fast scoring | Claude Haiku 4.5 (hindsight / quick-score / live check) |
| AI — social intel | xAI Grok (X/Twitter sentiment, weighted by market cap) |
| On-chain signals | Nansen.ai smart money screener |
| Market data | Bitget · Bybit · Binance · OKX funding · FRED macro |
| Prompt caching | Anthropic ephemeral `cache_control` |
| Exchange APIs | Bitget REST v2 · Blofin REST v1 (HMAC-SHA256) |
| Alerts | Telegram Bot API (stdlib only, no deps) |
| Process manager | systemd |

---

## Requirements

- Python 3.10+
- [Bitget](https://www.bitget.com) API key (read-only)
- [Anthropic API key](https://console.anthropic.com) for AI features
- Linux host (tested on Raspberry Pi 5)

---

## Installation

```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
pip3 install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

### Key env vars

```env
BITGET_API_KEY=        BITGET_SECRET_KEY=      BITGET_PASSPHRASE=
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=    TELEGRAM_CHAT_ID=
GROK_API_KEY=          # optional — xAI Grok social intelligence
NANSEN_API_KEY=        # optional — on-chain smart money signals
FRED_API_KEY=          # optional — macro context (free at fred.stlouisfed.org)
PORT=8082
```

### Bitget API setup
Log in → **Profile → API Management → Create API** → Read-only permissions only.

---

## Running

```bash
# Direct
python3 app.py
```

```bash
# As a systemd service (recommended for Pi)
sudo cp trading-journal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-journal
```

Access at `http://<host>:8082` from any browser on your network.

---

## First run

1. Open the journal in a browser
2. **Import** a Bitget CSV export to populate historical trades
3. Background sync starts automatically (every 5 min)
4. Use **Call Analyzer** to log analyst calls before entering trades
5. Run the scanner to surface setup opportunities

---

## Project structure

```
app.py                  Flask startup, 9 blueprints, 2 background threads
database.py             Schema, 26 migrations, db_conn() context manager
helpers.py              _ok, _err, log_token_usage, strip_fence, build_cached_messages
constants.py            All models, cache TTLs, thresholds (single source of truth)
prompt_builder.py       Context assembler: market + rulebook + chart + Nansen + Grok + similar trades
prompt_fragments.py     Shared scoring scale, level proximity rules, market context rules
trade_history.py        get_recent_trades / get_trade_stats / get_symbol_summary
trade_utils.py          normalize_symbol/direction, SECTORS, atr_sl_warning

routes/
  analytics.py          KPIs, deep dive, heatmap, R:R, MFE/MAE, EV, rolling, Sharpe/Calmar, charts
  calls.py              Call analyzer, saved calls, outcomes, auto-matching, analyst stats
  journal.py            Positions CRUD, import, symbols, wallet history
  limits.py             Pending limit orders
  live.py               Live positions (Bitget + Blofin), per-trade AI
  market.py             Market context, calendar, exchange symbols, prices
  scanner.py            Scanner run/status/watchlist/calibrate
  settings.py           Exchange credential management
  sync.py               Sync trigger, AI advisor, rulebook, Telegram
  hindsight.py          Retroactive analysis

ai_call.py              Call analysis: price extraction, sizing, CoT, Grok context
ai_scanner.py           3-stage pipeline: confluence → quality gate → batched Sonnet
ai_advisor.py           Full-portfolio coaching (Sonnet)
ai_rulebook.py          Personalised rulebook: guard, staleness decay, calibration
ai_hindsight.py         Retroactive blind scoring (Haiku)
ai_live_trade.py        Per-trade AI on live positions view
ai_limit.py             Pending limit analysis (Haiku)
ai_trade_grader.py      Execution grading A-D (Haiku)
ai_pattern_detector.py  Pattern detection + cross-pattern compounding
ai_client.py            Singleton Anthropic wrapper with auto token logging

chart_context.py        OHLCV fetch + caching (orchestrator); trendlines, Fibonacci
chart_indicators.py     Pure indicator suite: RSI/MACD/EMA/ADX/Bollinger/ATR/WaveTrend/CVD
chart_sr.py             Pure S/R detection with ATR-relative tolerance + recency weighting

grok_client.py          xAI Grok social intelligence; CoinGecko MC lookup; weight by cap tier
nansen_client.py        Nansen smart money screener (30-min cache)
market_context.py       Fear & Greed, funding rates, L/S ratio, BTC regime, FRED macro
bitget_client.py        Bitget REST v2 (HMAC-SHA256, read-only)
bitget_sync.py          Background sync: positions, regime tagging, call auto-close
blofin_client.py        Blofin REST v1 (5-header HMAC)
blofin_sync.py          Blofin position sync + regime tagging
importer.py             Bitget CSV import parser
analytics.py            All stat computations (KPIs, deep dive, MFE/MAE, EV, Sharpe/Calmar)
scanner_scheduler.py    Daemon: scan every 30 min, Telegram alert, persist setups to DB
telegram_notify.py      Telegram alerts (stdlib only)

scripts/self_test.py    54-test smoke runner for all 76 API endpoints
templates/index.html    Single-page app
templates/chart.html    Detached chart window
static/style.css        Dark theme
static/js/              17 modules (01-utils → 16-settings)

docs/GUIDE.md           Developer reference
docs/USER_GUIDE.md      End-user guide
docs/RATING_CRITERIA.md AI scoring criteria reference
docs/SCORING_GUIDE.md   Per-level scoring rubric (1–10)
.env.example            Environment variable template
trading-journal.service systemd unit
CLAUDE.md               AI subagent context file
```

---

## Notes

- Designed for personal use on a local network — no authentication layer. Do not expose to the public internet.
- All AI features require `ANTHROPIC_API_KEY`. The journal runs without it; AI buttons return an error.
- SQLite is sufficient for personal use (<100k trades, single user).

---

## Versioning

Versions increment only on significant feature milestones. Bug fixes and minor additions ship continuously without a version bump.

| Version | What it means |
|---------|--------------|
| v1.x | Feature additions to the core journal |
| v2.0 | Major new capability (e.g. exchange, auth layer, new data source tier) |

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
