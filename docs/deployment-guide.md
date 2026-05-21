# VPS Deployment Guide — Auto-Trader v2 (Reaction Network)

> **Target**: Debian 12 (Bookworm) VPS · Python 3.11+ · SQLite · systemd

## Quick Start

```bash
git clone https://github.com/differentstuff/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
bash setup.sh
```

Then edit config files (see below) and start the daemon.

---

## 1. Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Debian 11 (Bullseye) | Debian 12 (Bookworm) |
| Python | 3.11 | 3.13 |
| RAM | 512 MB | 1 GB |
| Disk | 1 GB | 5 GB (for logs + DB growth) |
| Network | Outbound HTTPS | Outbound HTTPS + WebSocket |

## 2. Configuration

After `setup.sh` runs, edit these files:

### `.env` — Environment Variables

```bash
# Required: At least one LLM provider
OPENROUTER_API_KEY=sk-or-v1-xxxxx
# Or: ANTHROPIC_API_KEY=xxxxx
# Or: GEMINI_API_KEY=xxxxx

# Telegram notifications (optional but recommended)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=123456789

# Logging
LOG_DIR=logs
LOG_FILE=logs/auto-trader.log
LOG_LEVEL=INFO
```

### `config/exchange.yaml` — Exchange & LLM Keys

```yaml
llm_keys:
  openrouter:
    - key: "sk-or-v1-YOUR-KEY"
      label: "openrouter-key-1"

exchange:
  bitget:
    api_key: "YOUR_BITGET_KEY"
    api_secret: "YOUR_BITGET_SECRET"
    passphrase: "YOUR_BITGET_PASSPHRASE"
    testnet: true          # Use testnet for paper trading
```

### `config/default.yaml` — Daemon Behavior

Key settings to review:

```yaml
daemon:
  cycle_interval_seconds: 300    # 5 minutes between cycles
  paper_mode: true               # MUST be true for testing

watchlist:
  symbols:
    - BTC/USDT
    - ETH/USDT
    # Add more as needed

learning:
  min_trades_for_analysis: 30    # Trades before learning kicks in
```

## 3. Systemd Service

### Install

```bash
sudo cp auto-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable auto-trader
sudo systemctl start auto-trader
```

### Service File (`auto-trader.service`)

```ini
[Unit]
Description=Auto Crypto Trading Journal v2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/opt/Auto-Crypto-Tradingjournal
ExecStart=/opt/Auto-Crypto-Tradingjournal/venv/bin/python main.py --paper
ExecStopPost=/opt/Auto-Crypto-Tradingjournal/venv/bin/python -c "import sqlite3; sqlite3.connect('trading_journal.db').backup('backups/stop_backup.db')"
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/Auto-Crypto-Tradingjournal/.env

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/Auto-Crypto-Tradingjournal /opt/Auto-Crypto-Tradingjournal/logs /opt/Auto-Crypto-Tradingjournal/data

[Install]
WantedBy=multi-user.target
```

### Useful Commands

```bash
# Status
sudo systemctl status auto-trader

# Logs (real-time)
sudo journalctl -u auto-trader -f

# Logs (last hour)
sudo journalctl -u auto-trader --since "1 hour ago"

# Logs (with file logs)
tail -f logs/auto-trader.log

# Restart after config change
sudo systemctl restart auto-trader

# Stop
sudo systemctl stop auto-trader

# Check if auto-starts on boot
sudo systemctl is-enabled auto-trader
```

## 4. 24h End-to-End Test

### Start the Test

```bash
# Make sure paper mode is on in config/default.yaml
grep "paper_mode: true" config/default.yaml

# Start the daemon
sudo systemctl start auto-trader

# Verify it's running
sudo systemctl status auto-trader
```

### Monitor Progress

Run the verification script periodically:

```bash
# Quick check (is it alive?)
bash scripts/verify_e2e.sh --quick

# Full check (after 30+ minutes)
bash scripts/verify_e2e.sh

# Detailed output
bash scripts/verify_e2e.sh --verbose
```

### What to Expect

| Timeframe | What Should Happen |
|---|---|
| 0–5 min | First cycle completes, DB created, tables initialized |
| 5–30 min | OHLCV data collected, substrate state saved |
| 30 min–2h | Enzymes firing (collect_ohlcv, detect_noise, score_confluence) |
| 2–6h | First paper trades may appear if signals are strong |
| 6–24h | Multiple cycles, learning data accumulating |
| 24h+ | Learning engine activates (after 30 trades), weight adjustments begin |

### Check Points

```bash
# How many cycles completed?
sqlite3 trading_journal.db "SELECT COUNT(*), MAX(created_at) FROM cycle_log"

# What enzymes fired?
sqlite3 trading_journal.db "SELECT DISTINCT enzymes_fired FROM cycle_log WHERE enzymes_fired != '[]' LIMIT 10"

# Any paper trades?
sqlite3 trading_journal.db "SELECT COUNT(*) FROM trade_learning"

# Learning data?
sqlite3 trading_journal.db "SELECT 'signal_accuracy:', COUNT(*) FROM signal_accuracy UNION ALL SELECT 'combination_accuracy:', COUNT(*) FROM combination_accuracy UNION ALL SELECT 'weight_history:', COUNT(*) FROM weight_history"

# Errors in log?
grep -ci "error\|exception\|traceback" logs/auto-trader.log
```

## 5. Daily Backup

Add a cron job for daily DB backups:

```bash
crontab -e
```

Add:

```cron
# Daily backup at 03:00
0 3 * * * /opt/Auto-Crypto-Tradingjournal/scripts/backup_db.sh >> /opt/Auto-Crypto-Tradingjournal/logs/backup.log 2>&1
```

The backup script keeps a 7-day rolling window automatically.

## 6. Updating

```bash
cd /opt/Auto-Crypto-Tradingjournal
git pull origin main
bash setup.sh --skip-apt          # Reinstall deps, skip system packages
sudo systemctl restart auto-trader
```

`setup.sh` never overwrites existing config files (`.env`, `config/exchange.yaml`).

## 7. Troubleshooting

### Daemon won't start

```bash
# Check the full error
sudo journalctl -u auto-trader -n 50 --no-pager

# Common causes:
# 1. Missing .env → run setup.sh or copy .env.example
# 2. Python version wrong → check venv/bin/python --version
# 3. Missing deps → source venv/bin/activate && pip install -r requirements.txt
```

### No data being collected

```bash
# Check if exchange keys are set
grep "api_key" config/exchange.yaml

# Check if LLM keys are set (needed for signal analysis)
grep "key:" config/exchange.yaml

# Run a single cycle manually with verbose output
source venv/bin/activate
python main.py --paper --cycle-once --log-level DEBUG
```

### High memory usage

```bash
# Check current usage
ps aux | grep main.py

# If > 500MB, restart and monitor
sudo systemctl restart auto-trader
watch -n 5 'ps aux | grep main.py'
```

### Database locked

SQLite handles concurrent reads well but only one writer. If you see `database is locked`:

```bash
# Check for multiple instances
pgrep -fa main.py

# Kill duplicates if any
sudo systemctl stop auto-trader
pkill -f main.py
sudo systemctl start auto-trader
```

## 8. Security Notes

- **Never commit `.env` or `config/exchange.yaml`** — they're in `.gitignore`
- Use **testnet** mode on exchanges during paper trading
- The systemd service uses `ProtectSystem=strict` to limit filesystem access
- LLM API keys are loaded from env vars, not hardcoded
- Consider a dedicated `trader` user (not root) for the service

## 9. File Structure (Production)

```
/opt/Auto-Crypto-Tradingjournal/
├── venv/                    # Python virtual environment
├── logs/
│   ├── auto-trader.log      # Main log file
│   └── backup.log           # Backup script log
├── data/
│   └── reports/             # Generated reports
├── backups/                 # 7-day rolling DB backups
├── trading_journal.db       # SQLite database (auto-created)
├── .env                     # API keys (NOT in git)
├── config/
│   ├── default.yaml         # Daemon config
│   ├── exchange.yaml        # Exchange + LLM keys (NOT in git)
│   └── strategies/          # Strategy definitions
├── core/                    # Daemon, substrate, enzyme framework
├── enzymes/                 # Reaction handlers
├── learning/                # Weight adjustment, rulebook
├── llm/                     # LLM client routing
├── indicators/              # Technical indicators
├── main.py                  # Entry point
├── setup.sh                 # Idempotent installer
└── scripts/
    ├── verify_e2e.sh        # E2E verification
    └── backup_db.sh         # DB backup with rotation