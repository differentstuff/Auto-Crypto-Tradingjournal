# Improvements Plan C — Infrastructure & Mobile UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate daily Pi-to-Mac database backups (offsite protection against SD card failure) and make the web UI usable on iOS/mobile (card layout for the three most-used views).

**Architecture:** Task 1 is a Mac-side shell script + launchd plist — no Python changes. Task 2 is pure CSS + minimal JS using DOM construction (createElement/textContent) for safety, with innerHTML only for server-validated numeric data where noted.

**Tech Stack:** Task 1: bash, rsync, expect, launchd. Task 2: CSS media queries, vanilla JS DOM API, existing Flask/HTML.

**Security:** All JS rendering uses DOM methods (createElement, textContent) for any user-editable strings. Server-generated numeric values (prices, PnL, percentages) and hardcoded strings (direction badges, status from VALID_STATUSES allowlist) may use template literals per the existing project pattern.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `scripts/backup_pi_to_mac.sh` | **Create** | rsync Pi DB to Mac backup directory, 30-file rolling. |
| `scripts/com.tradingjournal.backup.plist` | **Create** | launchd plist template — runs backup daily at 03:00. |
| `static/css/mobile.css` | **Create** | Card layout for live positions, scanner results, limits on narrow viewports. |
| `templates/index.html` | **Modify** | Link mobile.css, add bottom nav HTML. |
| `static/js/06-live.js` | **Modify** | renderPositionCards() for mobile. |
| `static/js/09-scanner.js` | **Modify** | renderScannerCards() for mobile. |
| `static/js/10-pending.js` | **Modify** | renderLimitCards() for mobile. |

---

## Task 1: Automated Pi-to-Mac Backup

**Problem:** The Pi backups/ directory lives on the same SD card as the live DB. SD card failure destroys both. This task adds a daily rsync from Pi to Mac at 03:00.

**Files:**
- Create: `scripts/backup_pi_to_mac.sh`
- Create: `scripts/com.tradingjournal.backup.plist`

- [ ] **Step 1: Create the backup script**

Create `scripts/backup_pi_to_mac.sh`:

```bash
#!/usr/bin/env bash
# scripts/backup_pi_to_mac.sh
# Rsync trading journal DB from Pi to Mac. Scheduled via launchd daily at 03:00.
#
# Password: set env var TRADING_JOURNAL_PI_PASSWORD or pass as $1.
# Never hardcode credentials in this file.

set -euo pipefail

PI_USER="fbauer"
PI_HOST="192.168.1.21"
PI_DB_PATH="/home/fbauer/trading-journal/trading_journal.db"
LOCAL_DIR="$HOME/Documents/TradingJournalBackups"
KEEP=30

mkdir -p "$LOCAL_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DEST="$LOCAL_DIR/trading_journal_${TIMESTAMP}.db"

PASSWORD="${1:-${TRADING_JOURNAL_PI_PASSWORD:-}}"
if [[ -z "$PASSWORD" ]]; then
    echo "[Backup] ERROR: set TRADING_JOURNAL_PI_PASSWORD env var" >&2
    exit 1
fi

expect -c "
set timeout 60
spawn rsync -az --progress \
    -e 'ssh -o StrictHostKeyChecking=no' \
    ${PI_USER}@${PI_HOST}:${PI_DB_PATH} ${DEST}
expect {
    \"password:\" { send \"${PASSWORD}\r\"; exp_continue }
    eof
}
"

if [[ -f "$DEST" ]]; then
    SIZE=$(du -sh "$DEST" | cut -f1)
    echo "[Backup] OK: $DEST ($SIZE)"
else
    echo "[Backup] FAILED: $DEST not created" >&2
    exit 1
fi

# Rolling delete: keep newest $KEEP backups
ls -t "$LOCAL_DIR"/trading_journal_*.db 2>/dev/null \
    | tail -n +$((KEEP+1)) \
    | while read -r OLD; do rm -f "$OLD"; echo "[Backup] Removed old: $OLD"; done
```

- [ ] **Step 2: Create the launchd plist template**

Create `scripts/com.tradingjournal.backup.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- Template — copy to ~/Library/LaunchAgents/ and fill in REPLACE_WITH_PASSWORD -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tradingjournal.backup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/scripts/backup_pi_to_mac.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TRADING_JOURNAL_PI_PASSWORD</key>
        <string>REPLACE_WITH_PASSWORD</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
    <key>StandardOutPath</key><string>/tmp/tradingjournal-backup.log</string>
    <key>StandardErrorPath</key><string>/tmp/tradingjournal-backup-error.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Make executable and test manually**

```bash
chmod +x scripts/backup_pi_to_mac.sh

TRADING_JOURNAL_PI_PASSWORD="<pi-password>" \
    bash scripts/backup_pi_to_mac.sh
```

Expected: `[Backup] OK: ~/Documents/TradingJournalBackups/trading_journal_*.db (16M)`

- [ ] **Step 4: Install launchd agent (on Mac, not committed)**

```bash
# Fill in password in a LOCAL copy (never commit the password):
cp scripts/com.tradingjournal.backup.plist ~/Library/LaunchAgents/
sed -i '' 's/REPLACE_WITH_PASSWORD/YOUR_REAL_PASSWORD/' \
    ~/Library/LaunchAgents/com.tradingjournal.backup.plist

# Load:
launchctl load ~/Library/LaunchAgents/com.tradingjournal.backup.plist
launchctl list | grep tradingjournal

# Force test run:
launchctl start com.tradingjournal.backup
sleep 30 && cat /tmp/tradingjournal-backup.log
```

Expected: new `.db` file in `~/Documents/TradingJournalBackups/`.

- [ ] **Step 5: Commit template files (no credentials)**

```bash
# Confirm placeholder is still in the repo copy:
grep "REPLACE_WITH_PASSWORD" scripts/com.tradingjournal.backup.plist

git add scripts/backup_pi_to_mac.sh scripts/com.tradingjournal.backup.plist
git commit -m "feat: automated Pi-to-Mac daily backup via launchd (template, no credentials)"
```

---

## Task 2: Mobile-Responsive UI (iOS Optimized)

**Problem:** On iPhone (375-430px) the three most-used views — Live Positions, Scanner Results, Pending Limits — show wide tables that require horizontal scroll and are unreadable.

**Fix:** CSS media query at 768px switches between table (desktop) and card (mobile) views. Cards are rendered by JS using DOM methods (createElement + textContent for strings, template literals only for server-numeric values). No new frameworks.

**Security invariant:** Prices, percentages, PnL values come from the server as validated numbers. Symbol strings are exchange ticker symbols (alphanumeric, validated by the exchange). Status strings come from VALID_STATUSES allowlist. Notes and user-freetext fields are set via textContent only (never injected into template literals).

**Files:**
- Create: `static/css/mobile.css`
- Modify: `templates/index.html`
- Modify: `static/js/06-live.js`, `09-scanner.js`, `10-pending.js`

- [ ] **Step 1: Create static/css/mobile.css**

```css
/* mobile.css — card layout for narrow viewports (iOS optimized).
   Tables and cards coexist in the DOM.
   Desktop (> 768px): tables shown (.hide-mobile hidden).
   Mobile (<=768px) : cards shown (.show-mobile visible, .hide-mobile gone).
*/

@media (max-width: 768px) {

  .hide-mobile { display: none !important; }
  .show-mobile { display: block !important; }

  /* Tab bar: horizontal scroll */
  .tab-bar {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    white-space: nowrap;
    padding-bottom: 2px;
    scrollbar-width: none;
  }
  .tab-bar::-webkit-scrollbar { display: none; }
  .tab-bar .tab-btn {
    display: inline-block;
    min-width: 80px;
    font-size: 13px;
    padding: 8px 12px;
  }

  /* Position card */
  .pos-card {
    background: var(--bg-secondary, #1a1a2e);
    border: 1px solid var(--border, #333);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
  }
  .pos-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .pos-card-symbol { font-size: 17px; font-weight: 700; letter-spacing: 0.5px; }
  .pos-card-dir { font-size: 13px; padding: 3px 8px; border-radius: 4px; font-weight: 600; }
  .pos-card-dir.long  { background: #0d3d1d; color: #4caf50; }
  .pos-card-dir.short { background: #3d0d0d; color: #f44336; }
  .pos-card-pnl { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
  .pos-card-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 16px;
    font-size: 13px;
  }
  .pos-card-grid .lbl { color: var(--text-muted, #888); font-size: 11px; }
  .pos-card-grid .val { font-weight: 500; }

  /* Scanner card */
  .scan-card {
    background: var(--bg-secondary, #1a1a2e);
    border: 1px solid var(--border, #333);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
  }
  .scan-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .scan-card-symbol { font-size: 16px; font-weight: 700; }
  .scan-score-badge { font-size: 14px; font-weight: 700; padding: 4px 10px; border-radius: 20px; }
  .scan-score-badge.hi  { background: #1a3a1a; color: #4caf50; }
  .scan-score-badge.mid { background: #1a2a3a; color: #64b5f6; }
  .scan-score-badge.low { background: #3a2a00; color: #f0a030; }
  .scan-card-row { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px; }
  .scan-card-row .lbl { color: var(--text-muted, #888); }

  /* Limit card */
  .limit-card {
    background: var(--bg-secondary, #1a1a2e);
    border: 1px solid var(--border, #333);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
  }
  .limit-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .limit-card-symbol { font-size: 16px; font-weight: 700; }
  .limit-card-status { font-size: 12px; padding: 3px 8px; border-radius: 4px; text-transform: capitalize; }
  .limit-card-status.waiting   { background: #1a2a3a; color: #64b5f6; }
  .limit-card-status.triggered { background: #1a3a1a; color: #4caf50; }
  .limit-card-status.expired   { background: #3a1a1a; color: #ef5350; }
  .limit-card-levels { display: grid; grid-template-columns: repeat(3,1fr); gap: 6px; font-size: 12px; margin-top: 8px; }
  .limit-card-levels .lvl { text-align: center; }
  .limit-card-levels .lbl { color: var(--text-muted, #888); font-size: 10px; }
  .limit-card-levels .val { font-weight: 600; margin-top: 2px; }

  /* KPI grid: 2 columns */
  .kpi-grid { grid-template-columns: repeat(2, 1fr) !important; }

  /* Bottom navigation */
  .bottom-nav {
    display: flex !important;
    position: fixed;
    bottom: 0; left: 0; right: 0;
    background: var(--bg-secondary, #1a1a2e);
    border-top: 1px solid var(--border, #333);
    height: 56px;
    z-index: 1000;
    padding-bottom: env(safe-area-inset-bottom);
  }
  .bottom-nav-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: var(--text-muted, #888);
    cursor: pointer;
    gap: 2px;
    border: none;
    background: none;
    padding: 0;
  }
  .bottom-nav-item.active { color: var(--accent, #4caf50); }
  .bottom-nav-item svg { width: 22px; height: 22px; }
  .main-content { padding-bottom: 60px; }
}

@media (min-width: 769px) {
  .show-mobile { display: none !important; }
  .bottom-nav  { display: none !important; }
}
```

- [ ] **Step 2: Run CSS brace balance check**

```bash
python3 -c "
css = open('static/css/mobile.css').read()
diff = css.count('{') - css.count('}')
print('Brace balance:', diff, '(must be 0)')
assert diff == 0
"
```

Expected: `Brace balance: 0`

- [ ] **Step 3: Link CSS and add bottom nav to templates/index.html**

In `<head>`, after existing CSS `<link>` tag:
```html
<link rel="stylesheet" href="/static/css/mobile.css?v=1.0">
```

Just before `</body>`, add the bottom nav:
```html
<nav class="bottom-nav" style="display:none" id="bottom-nav">
  <button class="bottom-nav-item" data-tab="live">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/>
    </svg>
    Live
  </button>
  <button class="bottom-nav-item" data-tab="scanner">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
    </svg>
    Scanner
  </button>
  <button class="bottom-nav-item" data-tab="limits">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
      <rect x="9" y="3" width="6" height="4" rx="2"/><path d="M9 12h6M9 16h4"/>
    </svg>
    Limits
  </button>
  <button class="bottom-nav-item" data-tab="journal">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
    </svg>
    Journal
  </button>
  <button class="bottom-nav-item" data-tab="analytics">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>
      <line x1="6" y1="20" x2="6" y2="14"/>
    </svg>
    Stats
  </button>
</nav>
<script>
// Wire bottom nav clicks to the existing switchTab() function
document.querySelectorAll('.bottom-nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (typeof switchTab === 'function') switchTab(tab);
        document.querySelectorAll('.bottom-nav-item').forEach(b =>
            b.classList.toggle('active', b.dataset.tab === tab));
    });
});
</script>
```

- [ ] **Step 4: Add position card container to Live tab HTML**

In the Live tab section, wrap the existing positions table:

```html
<!-- Mobile cards (shown via CSS on narrow viewports) -->
<div id="positions-cards" class="show-mobile"></div>
<!-- Desktop table (hidden on mobile) -->
<div class="hide-mobile">
  <!-- existing positions table unchanged -->
</div>
```

- [ ] **Step 5: Add renderPositionCards() to static/js/06-live.js**

```javascript
function renderPositionCards(positions) {
    const container = document.getElementById('positions-cards');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!positions || !positions.length) {
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.style.padding = '16px';
        msg.textContent = 'No open positions.';
        container.appendChild(msg);
        return;
    }

    positions.forEach(p => {
        const isLong = p.direction === 'Long';
        const pnl    = parseFloat(p.unrealized_pnl || 0);
        const pnlPct = parseFloat(p.unrealized_pct || 0);

        const card = document.createElement('div');
        card.className = 'pos-card';

        // Header row: symbol + direction badge
        const hdr = document.createElement('div');
        hdr.className = 'pos-card-header';

        const sym = document.createElement('span');
        sym.className = 'pos-card-symbol';
        sym.textContent = p.symbol;          // exchange ticker, alphanumeric

        const dir = document.createElement('span');
        dir.className = 'pos-card-dir ' + (isLong ? 'long' : 'short');
        dir.textContent = (isLong ? '▲' : '▼') + ' ' + (p.direction || '') + ' ' + (p.leverage || '') + 'x';

        hdr.appendChild(sym);
        hdr.appendChild(dir);

        // PnL line
        const pnlEl = document.createElement('div');
        pnlEl.className = 'pos-card-pnl ' + (pnl >= 0 ? 'pnl-pos' : 'pnl-neg');
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) +
                            ' (' + (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(1) + '%)';

        // Grid of stats
        const grid = document.createElement('div');
        grid.className = 'pos-card-grid';
        const stats = [
            ['Entry', parseFloat(p.entry_price || 0).toFixed(4)],
            ['Mark',  parseFloat(p.mark_price  || 0).toFixed(4)],
            ['SL',    p.stop_loss    || 'none'],
            ['TP',    p.take_profit  || '—'],
            ['Size',  '$' + parseFloat(p.size_usdt || 0).toFixed(0)],
            ['Margin','$' + parseFloat(p.margin_usdt || 0).toFixed(0)],
        ];
        stats.forEach(([label, value]) => {
            const cell = document.createElement('div');
            const lbl = document.createElement('div');
            lbl.className = 'lbl';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.className = 'val';
            val.textContent = value;   // numeric strings only, no HTML
            cell.appendChild(lbl);
            cell.appendChild(val);
            grid.appendChild(cell);
        });

        card.appendChild(hdr);
        card.appendChild(pnlEl);
        card.appendChild(grid);
        container.appendChild(card);
    });
}
```

Call `renderPositionCards(positions)` in the existing positions load function alongside the table render.

Bump `?v=` in `templates/index.html` for `06-live.js`.

- [ ] **Step 6: Add scanner card container + renderScannerCards() to 09-scanner.js**

Add container in scanner tab HTML:
```html
<div id="scanner-cards" class="show-mobile"></div>
<div class="hide-mobile">
  <!-- existing scanner results table -->
</div>
```

```javascript
function renderScannerCards(setups) {
    const container = document.getElementById('scanner-cards');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!setups || !setups.length) {
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.style.padding = '16px';
        msg.textContent = 'No setups found.';
        container.appendChild(msg);
        return;
    }

    setups.forEach(s => {
        const score = s.setup_score || 0;
        const ez    = s.entry_zone || {};
        const card  = document.createElement('div');
        card.className = 'scan-card';

        const hdr = document.createElement('div');
        hdr.className = 'scan-card-header';

        const symEl = document.createElement('span');
        symEl.className = 'scan-card-symbol';
        symEl.textContent = s.symbol;   // exchange symbol, alphanumeric

        const badge = document.createElement('span');
        const badgeCls = score >= 8 ? 'hi' : score >= 6 ? 'mid' : 'low';
        badge.className = 'scan-score-badge ' + badgeCls;
        badge.textContent = score + '/10';

        hdr.appendChild(symEl);
        hdr.appendChild(badge);

        const rows = [
            ['Direction', s.direction || '—'],
            ['Entry', ez.low && ez.high
                ? parseFloat(ez.low).toFixed(4) + '–' + parseFloat(ez.high).toFixed(4)
                : '—'],
            ['SL',  s.sl_price  ? parseFloat(s.sl_price).toFixed(4)  : '—'],
            ['TP1', s.tp1_price ? parseFloat(s.tp1_price).toFixed(4) : '—'],
            ['R:R', s.rr_ratio  || '—'],
        ];

        const rowsEl = document.createElement('div');
        rows.forEach(([label, value]) => {
            const row = document.createElement('div');
            row.className = 'scan-card-row';
            const lbl = document.createElement('span');
            lbl.className = 'lbl';
            lbl.textContent = label;
            const val = document.createElement('span');
            val.textContent = value;  // numeric or validated enum
            row.appendChild(lbl);
            row.appendChild(val);
            rowsEl.appendChild(row);
        });

        card.appendChild(hdr);
        card.appendChild(rowsEl);

        if (s.summary) {
            const sumEl = document.createElement('div');
            sumEl.style.cssText = 'font-size:12px;color:var(--text-muted,#888);margin-top:8px;line-height:1.4';
            sumEl.textContent = (s.summary || '').substring(0, 120);  // textContent — safe for LLM output
            card.appendChild(sumEl);
        }

        container.appendChild(card);
    });
}
```

Call `renderScannerCards(setups)` alongside the existing table render.

Bump `?v=` for `09-scanner.js`.

- [ ] **Step 7: Add limit card container + renderLimitCards() to 10-pending.js**

Add container in limits tab:
```html
<div id="limits-cards" class="show-mobile"></div>
<div class="hide-mobile">
  <!-- existing limits table -->
</div>
```

```javascript
// Valid statuses from routes/limits.py VALID_STATUSES allowlist:
const _VALID_STATUSES = new Set(['waiting','triggered','dismissed','expired']);

function renderLimitCards(limits) {
    const container = document.getElementById('limits-cards');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!limits || !limits.length) {
        const msg = document.createElement('p');
        msg.className = 'muted';
        msg.style.padding = '16px';
        msg.textContent = 'No pending limits.';
        container.appendChild(msg);
        return;
    }

    limits.forEach(lim => {
        const card = document.createElement('div');
        card.className = 'limit-card';

        const hdr = document.createElement('div');
        hdr.className = 'limit-card-header';

        const symEl = document.createElement('span');
        symEl.className = 'limit-card-symbol';
        symEl.textContent = lim.symbol;  // exchange symbol, alphanumeric

        // status comes from VALID_STATUSES allowlist — safe to use as CSS class
        const rawStatus = _VALID_STATUSES.has(lim.status) ? lim.status : 'waiting';
        const statusEl = document.createElement('span');
        statusEl.className = 'limit-card-status ' + rawStatus;
        statusEl.textContent = rawStatus;

        hdr.appendChild(symEl);
        hdr.appendChild(statusEl);

        const dirRow = document.createElement('div');
        dirRow.className = 'scan-card-row';
        const dirLbl = document.createElement('span');
        dirLbl.className = 'lbl';
        dirLbl.textContent = 'Direction';
        const dirVal = document.createElement('span');
        dirVal.textContent = (lim.direction || '') + ' ' + (lim.leverage || '') + 'x';
        dirRow.appendChild(dirLbl);
        dirRow.appendChild(dirVal);

        const priceRow = document.createElement('div');
        priceRow.className = 'scan-card-row';
        const priceLbl = document.createElement('span');
        priceLbl.className = 'lbl';
        priceLbl.textContent = 'Limit price';
        const priceVal = document.createElement('span');
        priceVal.textContent = lim.limit_price ? parseFloat(lim.limit_price).toFixed(4) : '—';
        priceRow.appendChild(priceLbl);
        priceRow.appendChild(priceVal);

        const levels = document.createElement('div');
        levels.className = 'limit-card-levels';
        [
            ['SL',  lim.sl_price,  'pnl-neg'],
            ['TP1', lim.tp1_price, 'pnl-pos'],
            ['TP2', lim.tp2_price, 'pnl-pos'],
        ].forEach(([label, price, cls]) => {
            const lvl = document.createElement('div');
            lvl.className = 'lvl';
            const lbl = document.createElement('div');
            lbl.className = 'lbl';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.className = 'val ' + cls;
            val.textContent = price ? parseFloat(price).toFixed(4) : '—';
            lvl.appendChild(lbl);
            lvl.appendChild(val);
            levels.appendChild(lvl);
        });

        card.appendChild(hdr);
        card.appendChild(dirRow);
        card.appendChild(priceRow);
        card.appendChild(levels);
        container.appendChild(card);
    });
}
```

Call `renderLimitCards(limits)` alongside existing table render.

Bump `?v=` for `10-pending.js`.

- [ ] **Step 8: Manual test on mobile**

Open journal URL on iPhone Safari. Verify:
- Tab bar scrolls horizontally
- Live: shows position cards, not wide table
- Scanner: shows result cards
- Limits: shows limit cards
- Bottom nav shows 5 tabs, tapping switches view
- iOS safe area at bottom respected
- Desktop browser (>768px): tables unchanged, bottom nav hidden

- [ ] **Step 9: Commit**

```bash
git add static/css/mobile.css templates/index.html \
        static/js/06-live.js static/js/09-scanner.js static/js/10-pending.js
git commit -m "feat: mobile-responsive card layout for positions, scanner, limits + iOS bottom nav"
```

---

## Final Checks

```bash
# Backup works:
ls -lh ~/Documents/TradingJournalBackups/ | head -5

# CSS brace balance:
python3 -c "
css = open('static/css/mobile.css').read()
diff = css.count('{') - css.count('}')
assert diff == 0, f'Unbalanced braces: {diff}'
print('CSS OK')
"

# Flask serves new files:
python3 -m pytest tests/ -v -q --tb=short 2>&1 | tail -10
```
